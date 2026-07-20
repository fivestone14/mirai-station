#!/usr/bin/env python3
# Pinned interpreter: the mirai-station venv (~/.local/share/mirai-station/venv) is the
# only one with schwab-py + scipy. System python3 lacks schwab → most signal modules
# silently fail → bogus quiet scans. Provision via runtime/scripts/venv-bootstrap.sh.
# (Absolute path on purpose: macOS env -S mangles quoted sh -c shebangs, and launchd
# also invokes this via run-watch-left-eye.sh with $MIRAI_STATION_VENV explicitly.)
"""
hunter.py — the SHIFT MANAGER: /mirai-left-eye scan orchestrator (gex-only).
(The every-5-minutes worker: wakes up, runs the Scan on each index, records the
diary row, refreshes the notes, rings the bell on fresh paper fires, sleeps.)

Pipeline per 5-min tick (post gex-module restructure, 2026-07-03):
    1. Universe = the Fade Lens tickers (SPX). No voters, no discovery.
       (QQQ was a voter-universe ticker and retired with the voters.)
    2. Per ticker: reversion.evaluate() — the SCAN: flow sensor → gravity
       engine → fade-lens gates — then record() the diary row.
    3. Refresh the human-facing notes (Today dashboard + ULT learning view).
    4. Flag a FRESH paper fire for the phone (armed-gate dedup — a still-lit
       trigger never re-alerts; it must reset or flip first).

HISTORY: the nine-voter/confluence/pick_builder/outcomes system was retired in
the 2026-07-03 gex-module restructure — the Fade Lens is the brain now, and it
stays paper/shadow until the nightly report cards prove it. Backup of the old
system: ~/.claude/plugins/mirai-station.backup-pre-gexmodule-20260703.

CLI:
    python3 hunter.py                       # one tick, compact output
    python3 hunter.py --ticker SPX          # single-ticker tick
    python3 hunter.py --dry-run             # skip log + state + note writes
    python3 hunter.py --verbose             # per-ticker diagnostic detail
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
SKILL_DIR = Path(__file__).resolve().parent
LOGS_DIR = SKILL_DIR / "logs"
STATE_PATH = SKILL_DIR / "state.json"
CONFIG_PATH = SKILL_DIR / "config.json"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import dashboard as dash  # noqa: E402
import reversion_lens as reversion  # noqa: E402
import gex_uw_bridge as uw_shadow  # noqa: E402  (SHADOW UW blend + learning)


def load_config() -> dict[str, Any]:
    """Read config.json; on any failure return safe defaults (push OFF)."""
    defaults = {"push_to_phone": False}
    if not CONFIG_PATH.exists():
        return defaults
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return defaults
    return {**defaults, **cfg}


# ===========================================================================
# Universe + gates
# ===========================================================================

# The scan universe IS the Fade Lens's tickers (SPX). Single source of
# truth: adding a ticker to the lens adds it to the scan.
INDEX_UNIVERSE = list(reversion.TICKERS)


def universe_for_today(now: datetime | None = None) -> list[str]:
    """The day's scan universe (empty on weekends → the tick is a no-op)."""
    now = now or datetime.now(tz=ET)
    if now.weekday() >= 5:
        return []
    return list(INDEX_UNIVERSE)


def time_gate(now: datetime | None = None) -> tuple[bool, str]:
    """Informational RTH gate for the heartbeat (the launchd wrapper already
    gates the scan on the authoritative market_status check)."""
    now = now or datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False, "weekend"
    t = now.astimezone(ET).time() if now.tzinfo else now.time()
    if t < dtime(9, 30):
        return False, "pre-open"
    if t >= dtime(16, 0):
        return False, "after-close"
    return True, "ok"


# ===========================================================================
# Dedup state (resets at session boundary) — kept for fire-alert dedup
# ===========================================================================


def load_state() -> dict[str, Any]:
    """Read state.json; reset if it's from a prior session."""
    today = datetime.now(tz=ET).date().isoformat()
    if not STATE_PATH.exists():
        return {"date": today}
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"date": today}
    if state.get("date") != today:
        return {"date": today}
    return state


def save_state(state: dict[str, Any]) -> None:
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except OSError as e:
        print(f"[hunter] state save failed: {e}", file=sys.stderr)


# Fresh-trigger arming gate — a lens fire alerts ONCE; the same still-lit
# trigger cannot re-alert until it resets (a quiet/flipped tick re-arms it).
def _arm_key(ticker: str, direction: str) -> str:
    return f"{ticker}:{direction}"


def rearm_inactive_directions(
    state: dict[str, Any], ticker: str, firing_direction: str | None
) -> None:
    """Re-arm every direction this ticker is NOT firing this tick."""
    armed = state.setdefault("armed", {})
    for direction in ("call", "put"):
        if direction == firing_direction:
            continue
        rec = armed.get(_arm_key(ticker, direction))
        if rec is not None:
            rec["disarmed"] = False


def fresh_trigger_gate(
    state: dict[str, Any], ticker: str, direction: str, fired_signals: list[str]
) -> tuple[bool, str]:
    """Allowed when armed (never fired, or reset since); blocked on a stale
    repeat of the same still-lit trigger. Pure read — no state mutation."""
    rec = state.get("armed", {}).get(_arm_key(ticker, direction))
    if rec is None or not rec.get("disarmed"):
        return True, "fresh trigger (armed)"
    new_signals = set(fired_signals) - set(rec.get("fired_signals") or [])
    if new_signals:
        return True, f"fresh signal joined: {', '.join(sorted(new_signals))}"
    return False, "stale repeat — same trigger still lit"


def mark_fired(
    state: dict[str, Any], ticker: str, direction: str,
    fired_signals: list[str], now: datetime,
) -> None:
    """Disarm a (ticker, direction) after it alerts."""
    armed = state.setdefault("armed", {})
    armed[_arm_key(ticker, direction)] = {
        "disarmed": True,
        "fired_signals": list(fired_signals),
        "ts": now.isoformat(),
    }


# Cross-lens referee — INERT conflict marker (Break Lens P7, 2026-07-05).
# The Fade Lens (reversion_extreme) trades the pull back toward GRAVITY; the
# Break Lens (level_reclaim) rides the FLOW away from it. When both would fire
# in OPPOSITE directions on the same scan, the book would lean both ways at
# once — that scan is marked stood_down on the diary row so the report cards
# can grade the conflict. Marker ONLY: nothing is suppressed, and the alert
# path below (keyed on reversion_extreme alone) is byte-for-byte unaffected.
def referee_guard(telemetry: dict[str, Any]) -> dict[str, Any] | None:
    """Return the stood_down marker when the fade and the break would ride
    OPPOSITE directions on the same scan; else None. Two conflict kinds, kept
    distinguishable by `reason` (the gated conflict wins when both hold):

      opposing_break_and_fade            — fade fire vs GATED break fire. A
        structural near-impossibility on legitimate paths (a fade fire needs
        'pinning', a gated break fire needs 'trending' — the same per-scan
        regime), kept for the fail-open/legacy edge cases.
      opposing_break_pre_gates_and_fade  — fade fire vs the UNGATED break
        trigger (fired_pre_gates carries no regime gate, so this CAN co-occur
        in pinning regime) — the collision that matters once shadow-alert
        privileges ride the ungated record.

    Pure read — never mutates telemetry."""
    rev = telemetry.get("reversion_extreme") or {}
    lvl = telemetry.get("level_reclaim") or {}
    if not rev.get("fired"):
        return None
    rev_dir = rev.get("direction")
    lvl_dir = lvl.get("direction")
    if rev_dir not in ("call", "put") or lvl_dir not in ("call", "put"):
        return None
    if rev_dir == lvl_dir:
        return None
    for fire_key, reason in (("fired", "opposing_break_and_fade"),
                             ("fired_pre_gates", "opposing_break_pre_gates_and_fade")):
        if lvl.get(fire_key):
            return {
                "stood_down": True,
                "reason": reason,
                "directions": {"reversion_extreme": rev_dir, "level_reclaim": lvl_dir},
                "ts": telemetry.get("ts"),
            }
    return None


# ===========================================================================
# The tick
# ===========================================================================


def scan(
    tickers: list[str] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one tick: SCAN each ticker, record the diary, refresh the notes,
    flag fresh paper fires. Returns the heartbeat dict (also logged)."""
    now = now or datetime.now(tz=ET)
    tickers = tickers or universe_for_today(now)
    state = load_state()
    gate_ok, gate_reason = time_gate(now)

    heartbeat: dict[str, Any] = {
        "ts": now.isoformat(),
        "universe": tickers,
        "time_gate": {"ok": gate_ok, "reason": gate_reason},
        "evaluated": [],
        "fires": [],
    }
    if verbose:
        print(f"=== mirai-left-eye tick {now.isoformat()} ===")
        print(f"universe: {tickers}  |  time_gate={gate_ok} ({gate_reason})")

    magp_lbl = {}   # ticker → "MagP''"-style label for the fire lines (display only)
    for ticker in tickers:
        rev_record = reversion.evaluate(ticker, now)
        if not rev_record:
            heartbeat["evaluated"].append({"ticker": ticker, "error": "scan returned nothing"})
            if verbose:
                print(f"[{ticker}] no scan (data unavailable)")
            continue
        t = rev_record["telemetry"]
        rev = t.get("reversion_extreme") or {}
        gv = t.get("gex_views") or {}
        # CROSS-LENS REFEREE (inert): fade fighting the FLOW vs break riding it
        # in opposite directions on one scan → mark both stood_down on the row
        # BEFORE it hits the diary. Fail-open: any error leaves the tick as-is.
        try:
            referee = referee_guard(t)
            if referee:
                t["referee"] = referee
                heartbeat.setdefault("stood_down", []).append(
                    {"ticker": ticker, **referee})
        except Exception:
            pass
        if not dry_run:
            reversion.record(t, now)
            # SHADOW: UW Periscope blend + learning. Records/compares alongside the
            # native map; never mutates `t` or any decision. Fail-open by contract,
            # but the exception is recorded in the heartbeat so a silent stall is visible.
            try:
                uw_shadow.run(t, ticker, now)
            except Exception as _uw_err:
                heartbeat.setdefault("shadow_errors", []).append(
                    {"ticker": ticker, "error": repr(_uw_err)})

        heartbeat["evaluated"].append({
            "ticker": ticker,
            "spot": t.get("spot"),
            "regime": t.get("regime"),
            "gex_source": t.get("gex_source"),
            "dealer_regime": gv.get("regime"),
            "magnet": gv.get("magnet"),
            "would_fire": rev.get("fired"),
            "direction": rev.get("direction"),
            "runway_sigma": rev.get("runway_sigma"),
            "gap_stretch": rev.get("gap_stretch"),
        })
        if verbose:
            # GW standard (docs/gw-vocab.md): the magnet level wears MagP + tier
            print(f"[{ticker}] spot {t.get('spot')}  regime {t.get('regime')}"
                  f"  dealers {gv.get('regime')}"
                  f"  {reversion._magp_label(t, gv.get('magnet'))} {gv.get('magnet')}"
                  f"  {'FIRE ' + str(rev.get('direction')) if rev.get('fired') else 'quiet'}")

        # WATCHTOWER (Head B, shadow): echo its human-facing snippet when it
        # judged this scan. Print-only, best-effort — never affects the tick.
        wt_snippet = (t.get("watchtower") or {}).get("snippet")
        if wt_snippet:
            try:
                print(wt_snippet)
            except Exception:
                pass

        direction = rev.get("direction")
        fired = bool(rev.get("fired"))
        rearm_inactive_directions(state, ticker, direction if fired else None)
        if not fired or direction not in ("call", "put"):
            continue
        fresh, reason = fresh_trigger_gate(state, ticker, direction, ["reversion_extreme"])
        if not fresh:
            heartbeat.setdefault("deduped", []).append(
                {"ticker": ticker, "direction": direction, "reason": reason})
            continue
        # paper unless the earn-trust gate says the record is proven (H4): the
        # hand switch alone can never mark a fire live.
        allowed, gate_reason = reversion.live_allowed()
        fire = {
            "ts": now.isoformat(), "ticker": ticker, "direction": direction,
            "side": "LONG" if direction == "call" else "SHORT",
            "magnet": rev.get("magnet"), "gap_stretch": rev.get("gap_stretch"),
            "runway_sigma": rev.get("runway_sigma"),
            "paper": not allowed, "gate": gate_reason,
        }
        # display-only (GW standard): the fire line's magnet label, tiered from
        # this scan's surface while it is still in hand — never persisted
        magp_lbl[ticker] = reversion._magp_label(t, rev.get("magnet"))
        heartbeat["fires"].append(fire)
        mark_fired(state, ticker, direction, ["reversion_extreme"], now)

    if not dry_run:
        _persist_scan(heartbeat, state, now)

    # Compact human line + optional phone flag (labels paper vs live honestly).
    fires = heartbeat["fires"]
    if fires:
        for f in fires:
            mode = "PAPER" if f.get("paper", True) else "LIVE"
            lbl = magp_lbl.get(f["ticker"], "MagP''")
            print(f"mirai-left-eye :: 🎯 {mode} fade {f['side']} {f['ticker']}"
                  f" → {lbl} {f['magnet']}  (stretch {f['gap_stretch']}σ,"
                  f" runway {f['runway_sigma']}σ)")
        if load_config().get("push_to_phone") and not dry_run:
            tag = "paper" if all(f.get("paper", True) for f in fires) else "LIVE"
            line = " · ".join(f"{f['ticker']} fade {f['side']} → {f['magnet']}" for f in fires)
            print(f"[PUSH] 🌑 {tag}: {line}")
    else:
        parts = []
        for e in heartbeat["evaluated"]:
            if e.get("error"):
                parts.append(f"{e['ticker']} n/a")
            else:
                parts.append(f"{e['ticker']} {e.get('regime', '?')}")
        print(f"mirai-left-eye :: quiet tick  ({' · '.join(parts) or 'no tickers'})")

    return heartbeat


def _persist_scan(heartbeat: dict[str, Any], state: dict[str, Any], now: datetime) -> None:
    """Append the heartbeat to today's log, save dedup state, refresh notes."""
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        with open(LOGS_DIR / f"{now.date().isoformat()}.jsonl", "a") as f:
            f.write(json.dumps(heartbeat, default=str) + "\n")
    except OSError as e:
        print(f"[hunter] log write failed: {e}", file=sys.stderr)
    save_state(state)
    # Best-effort note refresh: never let rendering break a tick.
    try:
        dash.write(now)
    except Exception:
        pass
    try:
        reversion.write_learning_view(now)
    except Exception:
        pass


# ===========================================================================
# CLI
# ===========================================================================


def main() -> None:
    ap = argparse.ArgumentParser(description="mirai-left-eye tick (gex-only)")
    ap.add_argument("--ticker", help="Single-ticker tick, bypass universe filter")
    ap.add_argument("--dry-run", action="store_true", help="Skip log + state + note writes")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    tickers = [args.ticker.upper()] if args.ticker else None
    scan(tickers=tickers, dry_run=args.dry_run, verbose=args.verbose)


if __name__ == "__main__":
    main()
