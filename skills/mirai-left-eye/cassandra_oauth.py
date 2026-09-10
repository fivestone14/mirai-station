"""
cassandra_oauth.py — OAuth enrolment + refresh-token auth for the
Cassandra's Edge / ThetaData MCP endpoint.

WHY THIS EXISTS (2026-09-09). Until this morning the endpoint honoured a
long-lived bearer copied out of ~/.claude.json, and `native_gex_feed` read that
one string from the Keychain forever. At 09:37 ET it began answering every
request with 401 `invalid_token` and a WWW-Authenticate header pointing at an
AuthKit authorization server. The tokens that server issues **expire in 300
seconds**. That is the whole story of the outage: not a wrong token, a token
shape that cannot be stored. A static bearer is dead five minutes after it is
minted, so re-keying can never fix it and the daily auth-watch could only ever
report the corpse.

What survives a five-minute clock is the REFRESH token, so that is what is
enrolled once (interactively, by a human at a browser) and what every later scan
spends. The access token becomes a cache with an expiry beside it.

THE SHAPE
  enrolment (once, human present)   `native_gex_feed.py --login`
      discover → register (RFC 7591) → authorization code + PKCE → refresh token
  every scan (headless, ~60s apart)
      cached access token if it has >SKEW left, else one refresh round-trip

WHY NOT THE DEVICE-CODE GRANT, which is the obvious fit for a headless station:
the authorization server advertises it in `grant_types_supported`, but its
REGISTRATION endpoint refuses to mint a client that asks for it — tried
2026-09-09, HTTP 422, "each value in grant_types must be one of the following
values: authorization_code, refresh_token". So enrolment is the native-app
loopback flow (RFC 8252): a listener on 127.0.0.1 catches the redirect. For a
station being enrolled over SSH, where the browser is on another machine and
nothing can reach that listener, `--login --manual` prints the URL and takes the
redirected address back by paste.

DISCOVERY is done properly rather than hardcoded, because the authorization
server this endpoint points at today has *staging* in its hostname and is very
likely to move: an unauthenticated request draws a 401 whose WWW-Authenticate
header names the resource-metadata document (RFC 9728), which names the
authorization server, whose well-known document names every endpoint used here.
Nothing about the provider is baked into this file.

CONCURRENCY. The scanner is a one-shot process — the left eye every 60s, SNDK
every 120s, the LOB collector every 60s — so "cache in memory" caches nothing
and all three can want a new access token in the same second. AuthKit rotates
the refresh token on every use, so two simultaneous refreshes would leave one
process holding a refresh token the server has already retired. Every refresh
therefore happens under a file lock, and re-reads the cache after acquiring it:
the loser of the race finds the winner's fresh token and never spends its own.

KNOWN, ACCEPTED RISK — the rotation crash window. The server retires the old
refresh token the instant it issues a new one, so between that HTTP response
landing and `set_cassandra_refresh_token` completing there is a window of a few
milliseconds in which a killed process leaves the Keychain holding a token the
server has already forgotten. The station is then locked out until a human runs
`--login` again. Not coded around: the window is tiny, the failure is loud
(`needs_login`, which the daily auth-watch pages on), and the recovery is one
command. Worth knowing about rather than rediscovering at an open.

SECURITY POSTURE (matches native_gex_feed's audit):
  * refresh + access tokens live ONLY in the Keychain, via vault;
  * neither ever travels in a log line, an env var or a return value that is
    printed — the enrolment prints an authorization URL, never a token;
  * the authorization code is single-use and PKCE-bound, so the URL a `--manual`
    enrolment pastes back is worthless to anyone who sees it afterwards;
  * runtime hardening (log redaction + traceback scrub) is installed before the
    first network call. Verified 2026-09-09 rather than assumed: the redactor
    used to mask the LABEL and leave the value (`refresh_token=eyJ...` became
    `[REDACTED]=eyJ...`), in log lines and tracebacks alike. vault._scrub now
    takes the value too;
  * the transport does NOT follow redirects, because the refresh token travels in
    a POST body and httpx strips only the Authorization header across hosts;
  * every URL a credential is spoken to is checked for https at the point of use,
    including endpoints that arrive from discovery and ones read back from a
    stored registration.

WHAT THIS DOES NOT PROTECT AGAINST: any process running as this user can read the
refresh token out of the Keychain without a prompt — verified. That is inherent to
a credential a headless launchd job must use every 60 seconds, and it was equally
true of the static bearer this replaced. What the move to OAuth does buy on that
axis: a stolen ACCESS token is worthless in five minutes, and a stolen refresh
token can be revoked server-side, which a permanent bearer could not be.

Public surface:
    bearer(force_refresh=False) -> str      the access token for Authorization
    login(timeout_s=300, manual=False) -> None   one-time interactive enrolment
    logout()                    -> None     forget the enrolment
    status()                    -> dict     {"state": ok|not_enrolled|..., ...}
    is_enrolled()               -> bool
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import httpx

_IV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "iv-viability")
if _IV not in sys.path:
    sys.path.insert(0, _IV)
import vault

RESOURCE = "https://market-research.cassandrasedge.com/mcp"

# Refresh this many seconds BEFORE the access token actually dies. A scan's
# whole native read is budgeted at 15s and one refresh round-trip is ~300ms, so
# 60s of headroom means a token handed to a scan is still alive when that scan
# finishes even if the network is having a bad minute. With a 300s token this
# spends one refresh per ~4 minutes of scanning, not one per scan.
SKEW_S = 60.0

# The enrolment asks for these. offline_access is the one that matters — without
# it the server returns an access token and no refresh token, which is exactly
# the situation this module exists to escape.
SCOPES = "openid profile email offline_access"

# The loopback port the redirect URI is registered against. Fixed by preference
# so a re-enrolment reuses one registration and the manual flow has a
# predictable URL to paste back; an ephemeral port is the fallback when
# something else already holds it.
REDIRECT_PORT = 8765
REDIRECT_PATH = "/callback"

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_LOCK_PATH = Path(__file__).resolve().parent.parent.parent / "state" / ".cassandra_oauth.lock"
_HARDENED = False


class OAuthError(RuntimeError):
    """Transient or configuration failure — worth retrying or reporting."""


class NeedsLogin(OAuthError):
    """The refresh token is gone, expired or rejected. Only a human at a browser
    can fix this; callers should say so rather than retry."""


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def _harden() -> None:
    global _HARDENED
    if not _HARDENED:
        vault.install_runtime_hardening()
        _HARDENED = True


def _client() -> httpx.Client:
    """follow_redirects=False, matching native_gex_feed's audited transport.

    NOT a style choice. The refresh token travels in a POST *body*, and httpx
    strips only the Authorization HEADER across hosts — a 307/308 from the token
    endpoint carries method and body intact to wherever Location points, http://
    included. Measured 2026-09-09 against a local pair of servers: with redirects
    followed, `refresh_token=...` arrived verbatim at the redirect target; with
    them off, the 307 is simply returned and nothing leaves. An OAuth endpoint has
    no legitimate reason to redirect a token request, so a redirect here is either
    a misconfiguration or an attack, and both deserve a failure rather than a
    forwarded credential."""
    _harden()
    return httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False)


def _https(url: str, what: str) -> str:
    """Every URL this module speaks a credential to must be https, INCLUDING the
    ones discovery hands us. native_gex_feed guards its one hardcoded endpoint
    this way; these are worse, because they arrive in a document from the network
    and are exactly what an attacker would rewrite to harvest a refresh token."""
    if not str(url).startswith("https://"):
        raise OAuthError(f"refusing non-https {what}: {url!r}")
    return url


def _resource_metadata_url(c: httpx.Client) -> str:
    """Ask the resource server itself where its metadata lives (RFC 9728). An
    unauthenticated POST draws the 401 whose WWW-Authenticate header carries
    `resource_metadata=`; the well-known path is the fallback for a server that
    answers 401 without the hint."""
    try:
        r = c.post(RESOURCE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {}})
        header = r.headers.get("www-authenticate", "")
    except httpx.HTTPError as e:
        raise OAuthError(f"cannot reach {RESOURCE}: {type(e).__name__}") from e
    for part in header.split(","):
        part = part.strip()
        if part.startswith("resource_metadata="):
            return _https(part.split("=", 1)[1].strip().strip('"'), "resource metadata URL")
    base = RESOURCE.split("://", 1)[1]
    origin, _, path = base.partition("/")
    return f"https://{origin}/.well-known/oauth-protected-resource/{path}"


def _discover(c: httpx.Client) -> dict:
    """resource metadata → authorization server → its endpoints. Returns the AS
    metadata document with the issuer folded in."""
    meta_url = _resource_metadata_url(c)
    try:
        rm = c.get(meta_url).json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        raise OAuthError(f"resource metadata unreadable at {meta_url}: {type(e).__name__}") from e
    servers = rm.get("authorization_servers") or []
    if not servers:
        raise OAuthError(f"resource metadata names no authorization server ({meta_url})")
    issuer = _https(str(servers[0]).rstrip("/"), "authorization server")
    for well_known in (f"{issuer}/.well-known/oauth-authorization-server",
                       f"{issuer}/.well-known/openid-configuration"):
        try:
            doc = c.get(well_known).json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            continue
        if doc.get("token_endpoint"):
            _https(doc["token_endpoint"], "token endpoint")
            if doc.get("registration_endpoint"):
                _https(doc["registration_endpoint"], "registration endpoint")
            if doc.get("authorization_endpoint"):
                _https(doc["authorization_endpoint"], "authorization endpoint")
            doc["issuer"] = doc.get("issuer") or issuer
            return doc
    raise OAuthError(f"no usable authorization-server metadata under {issuer}")


def _register(c: httpx.Client, meta: dict, redirect_uri: str) -> dict:
    """Dynamic client registration (RFC 7591) as a PUBLIC native client: no
    secret to store on the mini, PKCE instead.

    The registration is refused (422) unless `redirect_uris` is present and
    `grant_types` is a subset of {authorization_code, refresh_token} — which is
    why the enrolment is a loopback redirect and not the device code the server
    otherwise advertises. The server's own validation messages are folded into
    the error: a bare 'HTTPStatusError' costs an afternoon, and this endpoint
    says exactly which field it disliked."""
    endpoint = meta.get("registration_endpoint")
    if not endpoint:
        raise OAuthError("authorization server does not support dynamic registration; "
                         "a client_id must be configured by hand")
    body = {
        "client_name": "mirai-station",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "native",
        "redirect_uris": [redirect_uri],
        "scope": SCOPES,
    }
    try:
        r = c.post(endpoint, json=body)
    except httpx.HTTPError as e:
        raise OAuthError(f"client registration unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise OAuthError(f"client registration refused ({r.status_code}): {_why(r)}")
    try:
        reg = r.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise OAuthError("client registration returned a body that is not JSON") from e
    if not reg.get("client_id"):
        raise OAuthError("client registration returned no client_id")
    return {"issuer": meta.get("issuer"),
            "client_id": reg["client_id"],
            "client_secret": reg.get("client_secret"),
            "authorization_endpoint": meta.get("authorization_endpoint"),
            "token_endpoint": meta["token_endpoint"],
            "redirect_uri": redirect_uri,
            "registered_at": time.time()}


def _why(r: httpx.Response) -> str:
    """The server's own words about a rejection, flattened to one line. Errors
    only — this never touches a 200 body, so it cannot print a token."""
    try:
        doc = r.json()
    except (json.JSONDecodeError, ValueError):
        return (r.text or "")[:200].replace("\n", " ").strip() or "no detail"
    if isinstance(doc, dict):
        errs = doc.get("errors")
        if isinstance(errs, list) and errs:
            return "; ".join(
                f"{e.get('field', '?')}: {e.get('code') or e.get('message')}"
                for e in errs if isinstance(e, dict))
        for key in ("error_description", "message", "error"):
            if doc.get(key):
                return str(doc[key])[:200]
    if not doc:
        return "no detail"
    return json.dumps(doc)[:200]


def _client_blob(c: httpx.Client, redirect_uri: Optional[str] = None) -> dict:
    """The stored registration, discovering and registering on first use. Kept
    in the Keychain beside the tokens so the refresh path never has to rediscover
    anything: a scan reads one Keychain item and posts to a known URL.

    `redirect_uri` is passed only by `login`, and only then does it matter: the
    refresh grant sends no redirect URI at all, so a scan is happy with whatever
    is stored. A login reuses the stored client when the URI still matches (the
    usual case — the fixed port was free again) and registers a new one only
    when the port moved, which keeps enrolments from leaving a trail of orphaned
    client registrations behind at the provider."""
    blob = vault.get_cassandra_client()
    if blob and blob.get("token_endpoint"):
        if redirect_uri is None:
            return blob
        if blob.get("redirect_uri") == redirect_uri and blob.get("authorization_endpoint"):
            return blob
    blob = _register(c, _discover(c), redirect_uri or _redirect_uri(REDIRECT_PORT))
    vault.set_cassandra_client(blob)
    return blob


def _redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}{REDIRECT_PATH}"


# ---------------------------------------------------------------------------
# enrolment (interactive, once)
# ---------------------------------------------------------------------------
class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the one redirect the browser makes after the human approves."""

    def do_GET(self):                                    # noqa: N802 (stdlib contract)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return
        self.server.query = dict(urllib.parse.parse_qsl(parsed.query))
        ok = "code" in self.server.query
        body = (b"<html><body style='font:16px system-ui;padding:3rem'>"
                b"<h2>mirai-station is enrolled.</h2>"
                b"<p>You can close this tab and go back to the terminal.</p></body></html>"
                if ok else
                b"<html><body style='font:16px system-ui;padding:3rem'>"
                b"<h2>Enrolment failed.</h2>"
                b"<p>The terminal has the detail.</p></body></html>")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                           # silence the stdlib access log
        pass


def _listener() -> tuple[Optional[HTTPServer], int]:
    """Bind before registering, because the redirect URI must name the port we
    actually got. The fixed port is tried first so a re-enrolment reuses one
    registration; an ephemeral port is the fallback when it is taken."""
    for port in (REDIRECT_PORT, 0):
        try:
            srv = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        except OSError:
            continue
        srv.query = None
        return srv, srv.server_address[1]
    raise OAuthError("could not bind a loopback port for the OAuth redirect")


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def login(timeout_s: float = 300.0, *, manual: bool = False, printer=print) -> None:
    """Enrol this machine. Authorization code + PKCE (RFC 8252's native-app
    shape) because the provider's registration endpoint refuses a device-code
    client — see the module docstring.

    Two ways to get the code back:
      default   a listener on 127.0.0.1 catches the redirect; the browser opens
                by itself. Right when you are sitting at the mini.
      manual    nothing listens; the URL is printed, and you paste back the
                address the browser ends up at. Right over SSH, where the
                browser is on a different machine entirely.
    """
    srv, port = (None, REDIRECT_PORT) if manual else _listener()
    try:
        with _client() as c:
            blob = _client_blob(c, redirect_uri=_redirect_uri(port))
            auth_endpoint = blob.get("authorization_endpoint")
            if not auth_endpoint:
                raise OAuthError("authorization server advertises no authorization endpoint")

            verifier, challenge = _pkce()
            state = secrets.token_urlsafe(32)
            url = auth_endpoint + "?" + urllib.parse.urlencode({
                "response_type": "code",
                "client_id": blob["client_id"],
                "redirect_uri": blob["redirect_uri"],
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": RESOURCE,
            })

            if manual:
                printer(f"\n  Open this in any browser:\n\n    {url}\n")
                printer("  Approve it, then paste the address the browser lands on\n"
                        "  (it will look like a connection error — the address bar is\n"
                        "  what matters, and it carries the code):\n")
                returned = input("  URL: ").strip()
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(returned).query))
            else:
                printer(f"\n  Opening your browser to approve this station.\n"
                        f"  If nothing opens, paste this in yourself:\n\n    {url}\n")
                webbrowser.open(url)
                # Serve until the callback lands or the deadline passes. A browser
                # asks for more than the one redirect — favicon, devtools probes,
                # a preconnect — so this cannot be a single handle_request(): the
                # first stray GET would eat the wait and the enrolment would time
                # out with the human staring at a success page.
                deadline = time.time() + timeout_s
                while srv.query is None and time.time() < deadline:
                    srv.timeout = min(5.0, max(0.5, deadline - time.time()))
                    srv.handle_request()
                query = srv.query or {}

            if not query:
                raise OAuthError("no authorization code came back before the timeout")
            if query.get("error"):
                raise OAuthError(f"authorization refused: {query.get('error')} "
                                 f"{query.get('error_description', '')}".strip())
            if query.get("state") != state:
                # Checked in BOTH modes. It was briefly skipped for --manual on the
                # reasoning that a pasted URL is self-evidently the user's own —
                # which is backwards: pasting is precisely where someone can be
                # handed a URL from elsewhere, and the state is what proves the
                # code came back from the request WE started.
                raise OAuthError("state mismatch on the callback — enrolment abandoned")
            code = query.get("code")
            if not code:
                raise OAuthError("the callback carried no authorization code")

            tok, err = _token_request(c, blob, {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": blob["redirect_uri"],
                "code_verifier": verifier,
            })
            if tok is None:
                raise OAuthError(f"code exchange failed: {err}")
            refresh = tok.get("refresh_token")
            if not refresh:
                vault.wipe_cassandra_oauth()
                raise OAuthError(
                    "the server issued no refresh token — the enrolment would die in "
                    f"{tok.get('expires_in', '?')}s. Check that offline_access is permitted.")
            vault.set_cassandra_refresh_token(refresh)
            _cache_access(tok)
            vault.clear_cassandra_token()      # the dead static bearer, gone for good
            printer("\n  Enrolled. The refresh token is in the Keychain; the station "
                    "mints its own\n  access tokens from now on. Confirm with --status.\n")
    except BaseException:
        # A half-finished enrolment is worse than none: it would let is_enrolled()
        # answer yes and send every scan down a path that cannot work.
        if not vault.has_cassandra_refresh_token():
            vault.wipe_cassandra_oauth()
        raise
    finally:
        if srv is not None:
            srv.server_close()


def logout() -> None:
    vault.wipe_cassandra_oauth()


# ---------------------------------------------------------------------------
# the runtime path
# ---------------------------------------------------------------------------
def _token_request(c: httpx.Client, blob: dict, form: dict) -> tuple[Optional[dict], str]:
    """One POST to the token endpoint. Returns (token_document, "") on success or
    (None, error_code) on a well-formed OAuth error. Never raises for an error
    the caller is expected to branch on — a device poll gets `authorization_pending`
    dozens of times and that is not an exception."""
    _https(blob["token_endpoint"], "token endpoint")   # re-check a STORED blob too
    data = dict(form)
    data["client_id"] = blob["client_id"]
    data["resource"] = RESOURCE          # RFC 8707: audience the MCP will accept
    if blob.get("client_secret"):
        data["client_secret"] = blob["client_secret"]
    try:
        r = c.post(blob["token_endpoint"], data=data)
    except httpx.HTTPError as e:
        raise OAuthError(f"token endpoint unreachable: {type(e).__name__}") from e
    try:
        doc = r.json()
    except (json.JSONDecodeError, ValueError):
        doc = {}
    if r.status_code == 200 and doc.get("access_token"):
        return doc, ""
    return None, str(doc.get("error") or f"http_{r.status_code}")


def _cache_access(tok: dict) -> None:
    """Store the access token with the moment it dies. `expires_in` is seconds
    from now and is trusted only as far as SKEW_S allows; a server that omits it
    is treated as the shortest thing we have seen (300s) rather than as forever."""
    ttl = float(tok.get("expires_in") or 300)
    vault.set_cassandra_access(tok["access_token"], time.time() + ttl)


def _rotate_refresh(tok: dict) -> None:
    """AuthKit hands back a NEW refresh token on every refresh and retires the
    old one. Persisting it is not optional: skip it once and the next scan
    presents a token the server has already forgotten."""
    new_refresh = tok.get("refresh_token")
    if new_refresh:
        vault.set_cassandra_refresh_token(new_refresh)


def _cached_bearer() -> Optional[str]:
    blob = vault.get_cassandra_access()
    if not blob:
        return None
    try:
        expires_at = float(blob.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at - time.time() <= SKEW_S:
        return None
    return blob["token"]


def bearer(force_refresh: bool = False) -> str:
    """The access token to put in the Authorization header.

    Cheap and cached in the common case: a Keychain read, a clock comparison,
    done. Only when the cached token is inside SKEW_S of death (or the caller
    just ate a 401 and passed force_refresh) does this spend a round-trip."""
    if not force_refresh:
        cached = _cached_bearer()
        if cached:
            return cached

    refresh = vault.get_cassandra_refresh_token()     # raises CredentialsNotEnrolled
    _harden()

    from filelock import FileLock, Timeout as LockTimeout
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(_LOCK_PATH), timeout=20):
            # Re-read INSIDE the lock: while we waited, another scan may have
            # refreshed and rotated. Spending our now-retired refresh token here
            # is exactly the race the lock exists to prevent.
            if not force_refresh:
                cached = _cached_bearer()
                if cached:
                    return cached
            refresh = vault.get_cassandra_refresh_token()
            with _client() as c:
                blob = _client_blob(c)
                tok, err = _token_request(c, blob, {"grant_type": "refresh_token",
                                                    "refresh_token": refresh})
            if tok is None:
                if err in ("invalid_grant", "invalid_request", "unauthorized_client"):
                    raise NeedsLogin(
                        f"the refresh token was rejected ({err}) — re-enrol with: "
                        "python3 native_gex_feed.py --login")
                raise OAuthError(f"token refresh failed: {err}")
            _rotate_refresh(tok)
            _cache_access(tok)
            return tok["access_token"]
    except LockTimeout as e:
        # Someone is mid-refresh and slow. A stale-but-live cached token beats
        # failing the scan outright; only refuse when there is nothing at all.
        blob = vault.get_cassandra_access()
        if blob and blob.get("token"):
            return blob["token"]
        raise OAuthError("timed out waiting for another process to refresh the token") from e


def is_enrolled() -> bool:
    try:
        return vault.has_cassandra_refresh_token()
    except Exception:
        return False


def status() -> dict:
    """A small verdict for the daily auth-watch. Never raises."""
    try:
        if not is_enrolled():
            return {"state": "not_enrolled",
                    "detail": "no refresh token — run: native_gex_feed.py --login"}
        blob = vault.get_cassandra_access() or {}
        left = None
        try:
            left = round(float(blob.get("expires_at") or 0) - time.time())
        except (TypeError, ValueError):
            pass
        bearer()                                   # proves the refresh path works
        return {"state": "ok", "detail": f"refresh token healthy (cached access {left}s left)"}
    except NeedsLogin as e:
        return {"state": "needs_login", "detail": str(e)}
    except Exception as e:
        return {"state": "unknown", "detail": f"{type(e).__name__}: {e}"}
