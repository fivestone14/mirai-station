"""Daily auth-watcher runner — the piece launchd actually runs.

Plain English:
Once a day this reads the encrypted Schwab token, works out how old the login is,
and if it's day 6+ (or unreadable) it pushes a one-tap re-auth link to the phone.
It NEVER crashes the schedule: any problem becomes an "unknown" status that still
pings you, and the program always exits 0.

What it touches:
  - iv-viability's `vault` to decrypt the token + read the App Key / callback URL
    (these already live in the macOS Keychain; no password is read or stored).
  - `token_watcher` to decide ok / warn / expired / unknown.
  - `reauth` to build the Schwab login link + the ping text.
  - `push_ntfy` to send it.

Run by hand:   python -m watch.intraday.auth_check [--force]
Run by launchd: same, once per day (see runtime/launchd/com.mirai-station.auth-watch.plist)
"""
from __future__ import annotations

import argparse
import sys
from collections import namedtuple
from pathlib import Path

from . import push_ntfy, reauth, token_watcher


def _load_vault():
    """Import iv-viability's vault module (it lives in a sibling skill dir).

    Returns the module, or None if it can't be imported (e.g. deps/venv missing)
    — in which case we treat auth state as unknown and still ping.
    """
    # parents[3] = plugin root (this file is runtime/watch/intraday/auth_check.py)
    skill_dir = Path(__file__).resolve().parents[3] / "skills" / "iv-viability"
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))
    try:
        import vault  # type: ignore
        return vault
    except Exception:
        return None


def _read_status():
    """Decrypt the token and classify its age. Returns (status, vault_or_None)."""
    vault = _load_vault()
    if vault is None:
        return token_watcher.classify(None), None  # unknown -> ping
    try:
        token = vault.load_token()
    except Exception:
        return token_watcher.classify(None), vault  # no/own token -> unknown -> ping
    created = token_watcher.creation_timestamp_from_token(token)
    return token_watcher.classify(created), vault


def _authorize_url(vault) -> str | None:
    """Build the Schwab login link from the stored App Key + callback URL.
    Returns None if those aren't enrolled yet (we still ping, just without a link)."""
    if vault is None:
        return None
    try:
        return reauth.build_authorize_url(vault.get_api_key(), vault.get_callback_url())
    except Exception:
        return None


# --- Cassandra/ThetaData data-token health -------------------------------------------
# The native-GEX bearer is a SEPARATE token from the Schwab OAuth login above. It has
# no expiry clock, so the only way to know it is dead is to actually probe the endpoint.
# A dead bearer silently degrades the native SPX chain to the SPY×10 proxy — this makes
# that failure loud instead of silent.

CassandraPing = namedtuple("CassandraPing", "title message priority tags")


def _load_native():
    """Import the native GEX feed (sibling skill) for its `probe_token` health check.
    Returns the module, or None if it can't be imported (deps missing, etc.)."""
    # parents[3] = plugin root (this file is runtime/watch/intraday/auth_check.py)
    left_eye = Path(__file__).resolve().parents[3] / "skills" / "mirai-left-eye"
    if str(left_eye) not in sys.path:
        sys.path.insert(0, str(left_eye))
    try:
        import native_gex_feed  # type: ignore
        return native_gex_feed
    except Exception:
        return None


def cassandra_needs_ping(state: str) -> bool:
    """Page ONLY on a definitive dead token. Unlike the Schwab watcher we do NOT
    fail-safe to a ping on 'unknown' — a flaky network probe must not cry wolf about
    a bearer that may be perfectly fine (the trading loop's own degrade covers a
    transient outage; a truly revoked token returns a hard 401/403)."""
    return state == "auth_rejected"


def build_cassandra_ping(state: str, detail: str = "") -> CassandraPing:
    """Build the phone ping for a Cassandra/ThetaData token verdict."""
    if state == "auth_rejected":
        return CassandraPing(
            title="Data token rejected",
            message=("Cassandra/ThetaData bearer rejected (" + (detail or "401/403") + "). "
                     "Native SPX GEX has degraded to the SPY×10 proxy until it is re-minted "
                     "— run: native_gex_feed.py --setup (copies the fresh bearer from ~/.claude.json)."),
            priority="urgent", tags="key")
    return CassandraPing(   # ok / unknown — informational, only sent under --force
        title="Data token check",
        message=f"Cassandra/ThetaData token probe: {state} ({detail}).",
        priority="default", tags="key")


def _cassandra_status():
    """Probe the bearer, guarded. Returns (state, detail); import/other failure -> unknown."""
    native = _load_native()
    if native is None:
        return "unknown", "native_gex_feed import failed"
    try:
        return native.probe_token()
    except Exception as e:   # probe_token never raises, but stay fail-safe here too
        return "unknown", f"{type(e).__name__}: {e}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="auth-check")
    parser.add_argument("--force", action="store_true",
                        help="Send the ping regardless of age (for testing the phone path).")
    parser.add_argument("--quiet", action="store_true", help="Only print on action.")
    args = parser.parse_args(argv)

    status, vault = _read_status()

    if not args.quiet or token_watcher.needs_reauth(status):
        print(f"[auth-check] state={status.state} "
              f"age={'%.2f' % status.age_days if status.age_days is not None else '?'}d "
              f"left={'%.2f' % status.days_left if status.days_left is not None else '?'}d "
              f"— {status.reason}")

    if token_watcher.needs_reauth(status) or args.force:
        url = _authorize_url(vault)
        ping = reauth.build_reauth_ping(status, url)
        rec = push_ntfy.send(ping.message, title=ping.title, click_url=ping.click_url,
                             priority=ping.priority, tags="key")
        print(f"[auth-check] ping dispatched={rec.get('dispatched')} "
              f"{rec.get('reason') or rec.get('error') or ''}".rstrip())

    # Cassandra/ThetaData data token — probe the endpoint (no expiry clock to read).
    # A dead bearer would otherwise silently degrade native SPX GEX to the SPY×10 proxy.
    cstate, cdetail = _cassandra_status()
    if not args.quiet or cassandra_needs_ping(cstate):
        print(f"[auth-check] cassandra-token={cstate} — {cdetail}")
    if cassandra_needs_ping(cstate) or args.force:
        cp = build_cassandra_ping(cstate, cdetail)
        rec = push_ntfy.send(cp.message, title=cp.title, priority=cp.priority, tags=cp.tags)
        print(f"[auth-check] cassandra ping dispatched={rec.get('dispatched')} "
              f"{rec.get('reason') or rec.get('error') or ''}".rstrip())

    return 0  # never fail the schedule


if __name__ == "__main__":
    sys.exit(main())
