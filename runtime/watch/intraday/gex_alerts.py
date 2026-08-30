"""gex_alerts.py — the ALERT BELL for the gex-only system.

Ported from the retired tick_graph macro_mood_node + alert_push nodes
(2026-07-03 gex-module restructure). Runs once per tick AFTER the left-eye
scan (run-watch-left-eye.sh → `python -m watch.cli gex-alerts`). Three jobs:

  1. FRESH PAPER FIRES → phone. Reads today's hunter heartbeats
     (skills/mirai-left-eye/logs/{date}.jsonl) and pushes any Fade-Lens fire
     not already pushed (sent-ledger dedup — one push per fire, ever).
  2. WALL-BREACH RE-DIVE. When spot pushes CONCRETELY beyond a dealer wall
     (buffer = frac × the lens's σ yardstick), re-fire the Morning-Mood
     analyst — daily budget + per-level cooldown capped, one S&P slot —
     and log the breach to the mood learning ledger. Walls, spot
     and σ come from the LENS DIARY (state/reversion/{date}.jsonl): the
     gravity engine's own numbers, no separate market feed.
  3. EOD. After 16:00 ET, score the mood's direction call once (score_eod),
     using the day's first/last diary spots as the realized move.

All best-effort: any failure costs a log line, never a tick. Idempotent
state: state/market_expectation/alerts_state.json (resets per day).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .. import paths, push
from . import macro_mood, market_status, push_ntfy, settings

ET = ZoneInfo("America/New_York")
# SPX only, and deliberately so. This module's passes are index-shaped — the
# wall-breach re-dive spends a Morning-Mood analyst call, and the EOD pass
# scores the mood's direction — so adding "SNDK" here would not give the stock
# a watchdog, it would give it a second opinion machine nobody asked for.
#
# SNDK's scanner IS watched, by its own process: runtime/watch/intraday/
# sndk_deadman.py on com.mirai-station.sndk-deadman (2026-08-30). It lives
# outside this module on purpose — gex_alerts runs from the left-eye tick, so
# a siren that shares that process cannot report the tick's own death. Four
# SNDK sessions died unpaged in August (08-07, 08-14, 08-17, 08-24) while this
# tuple sat here looking like coverage.
LENS_TICKERS = ("SPX",)
_EOD_MINUTES = 16 * 60

# Feed-health siren (2026-07-13): a diary row younger than this means the scanner
# is in-session RIGHT NOW — rows are only written when the market-status gate said
# live, so row freshness IS the session test (no calendar logic duplicated here).
_FEED_FRESH_MIN = 20.0

# The INVERSE siren: rows land ~every 4 min in-session, so a live market with no
# row for this long means the scanner itself is down — the 07-13 failure mode one
# layer deeper (a dead scanner writes no honest flags, and silence reads as health).
_SCANNER_SILENT_MIN = 45.0

# flag → phone text; an unlisted flag still pages, with its raw name
_FEED_SIREN_TEXT = {
    "no_zero_dte": "today's 0DTE expiry missing from the chain — pin/flow/live walls blind",
    "zero_dte_dead": "today's 0DTE book half-dead — native fetch rejected",
}


def _alerts_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "market_expectation" / "alerts_state.json"


def _load_alerts_state(state_dir: Path, date_iso: str) -> Dict[str, Any]:
    fresh = {"date": date_iso, "pushed": [], "redives": 0, "wallTimers": {},
             "refOpen": None, "refLast": None}
    try:
        st = json.loads(_alerts_state_path(state_dir).read_text())
        # isinstance: valid-JSON-non-dict (null, list) must reset, not crash the run
        return st if isinstance(st, dict) and st.get("date") == date_iso else fresh
    except (OSError, json.JSONDecodeError, ValueError):
        return fresh


def _save_alerts_state(state_dir: Path, st: Dict[str, Any]) -> None:
    try:
        p = _alerts_state_path(state_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, default=str))
    except OSError:
        pass


def _lens_rows(state_dir: Path, date_iso: str) -> List[dict]:
    """All of today's diary rows (the gravity engine's own numbers)."""
    path = Path(state_dir) / "reversion" / f"{date_iso}.jsonl"
    rows: List[dict] = []
    try:
        # errors="replace": one mangled byte must cost one row, never the whole run
        for ln in path.read_text(errors="replace").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def _row_age_min(row: dict, now_et: datetime) -> Optional[float]:
    """Minutes since the diary row was written; None when its ts won't parse."""
    try:
        ts = datetime.fromisoformat(str(row.get("ts")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        return (now_et - ts).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def _feed_health_flags(row: dict) -> List[str]:
    """Degraded-feed facts the diary row already states honestly. Each one means
    this scan's gravity map ran without a sense it normally has."""
    flags: List[str] = []
    cov = row.get("native_coverage") or {}
    if cov.get("no_zero_dte"):
        flags.append("no_zero_dte")
    if cov.get("zero_dte_dead"):
        flags.append("zero_dte_dead")
    src = row.get("gex_source")
    if src and src != "native":
        # family only — the live proxy string embeds the per-tick SPX/SPY rescale
        # ("spy_proxy×10.0348", 11 distinct values on 07-10 alone); the raw value
        # would defeat the once-per-day dedup and turn a proxy day into a pager storm
        flags.append("source:" + str(src).split("×", 1)[0])
    return flags


def _hunter_fires(date_iso: str) -> List[dict]:
    """Every Fade-Lens fire in today's hunter heartbeats."""
    path = paths.LEFT_EYE_SKILL / "logs" / f"{date_iso}.jsonl"
    fires: List[dict] = []
    try:
        for ln in path.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                hb = json.loads(ln)
            except json.JSONDecodeError:
                continue
            fires.extend(hb.get("fires") or [])
    except OSError:
        pass
    return fires


def run(now: Optional[datetime] = None, *, state_dir: Optional[Path] = None,
        redive_provider=None, channel=None) -> Dict[str, Any]:
    """One alert pass. Providers injectable for tests; defaults are live."""
    now = now or datetime.now(tz=ET)
    now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    date_iso = now_et.date().isoformat()
    minutes = now_et.hour * 60 + now_et.minute
    state_dir = Path(state_dir or paths.STATE_DIR)
    push.set_channel(channel or push_ntfy.make_channel())

    st = _load_alerts_state(state_dir, date_iso)
    out: Dict[str, Any] = {"pushed": 0, "breaches": 0, "redives": 0,
                           "eod_scored": False, "feed_sirens": 0}
    rows = _lens_rows(state_dir, date_iso)   # today's diary — passes 2 and 4 read it

    # --- 1) fresh paper fires → phone (one push per fire, ever) --------------
    try:
        pushed = set(st.get("pushed") or [])
        for f in _hunter_fires(date_iso):
            fid = f"{f.get('ts')}:{f.get('ticker')}:{f.get('direction')}"
            if fid in pushed:
                continue
            side = f.get("side") or ("LONG" if f.get("direction") == "call" else "SHORT")
            # magnet is a zone CENTER now (float, e.g. 7548.62) — round for the phone
            _m = f.get("magnet")
            _m = f"{_m:.0f}" if isinstance(_m, (int, float)) else _m
            push.send(f"🌑 paper: {f.get('ticker')} fade {side} → magnet "
                      f"{_m} (stretch {f.get('gap_stretch')}σ)",
                      tag="gex-fire")
            pushed.add(fid)
            out["pushed"] += 1
        st["pushed"] = sorted(pushed)
    except Exception as e:
        print(f"gex-alerts :: fire push degraded: {e}", flush=True)

    # --- 2) wall-breach re-dive (ported budget/cooldown semantics) ------------
    try:
        exp = macro_mood.read_expectation(state_dir, date_iso)
        if exp is None:
            latest = macro_mood.read_latest(state_dir)
            if latest and str(latest.get("date") or "")[:10] >= date_iso:
                exp = latest
        latest_by_tk = {}
        for r in rows:
            if r.get("ticker") in LENS_TICKERS:
                latest_by_tk[r["ticker"]] = r

        # reference-index anchors for EOD scoring (SPX preferred)
        spx_rows = [r for r in rows if r.get("ticker") == "SPX" and r.get("spot")]
        if spx_rows:
            if st.get("refOpen") is None:
                st["refOpen"] = float(spx_rows[0]["spot"])
            st["refLast"] = float(spx_rows[-1]["spot"])

        used = int(st.get("redives") or 0)
        cooldowns = dict(st.get("wallTimers") or {})
        redive = redive_provider or macro_mood.make_redive_provider(state_dir)
        buffer_frac = settings.macro_wall_buffer_frac()
        daily_cap = settings.macro_redive_daily_cap()
        cd_min = settings.macro_redive_cooldown_min()
        exp_dir = float((exp.get("overall") or {}).get("direction") or 0.0) if exp else 0.0
        redived_roots: set = set()

        # N1 (2026-07-12): the engine now records the breach EVENT on the row
        # (reversion_lens.detect_breach — this scan's spot vs the PREVIOUS row's
        # operative wall). The old proximity check compared spot to the SAME row's
        # wall, which break-to-extend had already re-placed OUTWARD — arithmetically
        # the bell could never ring. Recorded events from recent rows ring it now;
        # the legacy check stays as a fallback for pre-event rows (it is inert on
        # them for exactly the reason above, and harmless).
        breach_cands = []
        for r in rows[-12:]:
            tk = r.get("ticker")
            wb = r.get("wall_breach")
            if tk in LENS_TICKERS and isinstance(wb, dict) and wb.get("wall") is not None:
                breach_cands.append((tk, r, {
                    "wall": round(float(wb["wall"])),
                    "direction": 1 if wb.get("side") == "call" else -1,
                    "beyond": round(float(wb.get("spot") or 0) - float(wb["wall"]), 1)}))
        for tk, r in latest_by_tk.items():
            br = macro_mood.wall_breach(r.get("spot"), r.get("call_wall"),
                                        r.get("put_wall"), r.get("sigma"),
                                        buffer_frac)
            if br:
                breach_cands.append((tk, r, br))
        for tk, r, br in breach_cands:
            out["breaches"] += 1
            key = f"{tk}:{br['wall']}"
            last = cooldowns.get(key)
            if last is not None and (minutes - float(last)) < cd_min:
                continue
            against = exp_dir != 0.0 and (br["direction"] > 0) != (exp_dir > 0)
            fired = False
            root = "SPY" if tk == "SPX" else tk            # S&P shares one slot
            if redive is not None and used < daily_cap and root not in redived_roots:
                try:
                    redive(exp, {"ticker": tk, "wall": br["wall"],
                                 "direction": br["direction"], "now": now_et})
                    used += 1
                    fired = True
                    redived_roots.add(root)
                except Exception:
                    fired = False
            # N1: the BELL is not the re-dive — it rings on every (cooldown-fresh)
            # breach even when the re-dive budget is spent. The breach is the fact;
            # the re-dive is a reaction to it.
            push.send(f"🧱 {tk} broke its {br['wall']} wall "
                      f"({br['beyond']:+.1f} beyond)"
                      + (" — mood re-dive fired" if fired else ""),
                      tag="gex-wall")
            cooldowns[key] = minutes
            macro_mood.log_learning(state_dir, {
                "kind": "wall_breach", "ticker": tk, "wall": br["wall"],
                "direction": br["direction"], "beyond": br["beyond"],
                "minutes_et": minutes, "against_expectation": against,
                "redived": fired}, date_iso)
        st["redives"] = used
        st["wallTimers"] = cooldowns
        out["redives"] = used
    except Exception as e:
        print(f"gex-alerts :: wall-breach pass degraded: {e}", flush=True)

    # --- 3) EOD mood scoring (idempotent inside score_eod) ---------------------
    try:
        if minutes >= _EOD_MINUTES and st.get("refOpen") is not None \
                and st.get("refLast") is not None:
            exp_for_score = macro_mood.read_expectation(state_dir, date_iso)
            realized = float(st["refLast"]) - float(st["refOpen"])
            won = macro_mood.score_eod(state_dir, exp_for_score, realized, date_iso)
            out["eod_scored"] = won is not None
    except Exception as e:
        print(f"gex-alerts :: EOD scoring degraded: {e}", flush=True)

    # --- 4) feed-health siren (2026-07-13) -------------------------------------
    # The 0DTE-discovery outage wrote `no_zero_dte` honestly on every live row and
    # paged no one — a diary flag is not an alarm. A fresh row means the scanner is
    # in-session right now; each degraded-feed fact on it pushes once per day.
    try:
        sirens = set(st.get("feedSirens") or [])
        latest = next((r for r in reversed(rows)
                       if r.get("ticker") in LENS_TICKERS), None)
        age = _row_age_min(latest, now_et) if latest else None
        if age is not None and age < _FEED_FRESH_MIN:
            for flag in _feed_health_flags(latest):
                if flag in sirens:
                    continue
                text = _FEED_SIREN_TEXT.get(flag, f"gravity feed degraded: {flag}")
                push.send(f"🩺 {latest.get('ticker') or 'SPX'} feed: {text}",
                          tag="gex-feed")
                sirens.add(flag)
                out["feed_sirens"] += 1
            # dead-flow siren (2026-07-20): the option_trade_flow feed was silently
            # empty for FIVE consecutive sessions (the provider's right:'both' path
            # died in its 07-11/12 deploy) and nothing paged — aggressor_flow=None
            # fails open by design ("no ticks yet"), so per-row honesty never trips.
            # A whole morning of Nones is not "no ticks yet": if every lens row so
            # far is flow-blind past mid-morning, the FEED is dead — page once.
            lens_rows = [r for r in rows if r.get("ticker") in LENS_TICKERS]
            if ("flow_dead" not in sirens and len(lens_rows) >= 20
                    and now_et.hour * 60 + now_et.minute >= 11 * 60
                    and all((r.get("gex_theta") or {}).get("aggressor_flow") is None
                            for r in lens_rows)):
                push.send("🩺 SPX feed: options flow dead — every scan today is "
                          "flow-blind (aggressor_flow None); check option_trade_flow "
                          "upstream (right:'both' regression family)", tag="gex-feed")
                sirens.add("flow_dead")
                out["feed_sirens"] += 1
        elif ((age is None or age >= _SCANNER_SILENT_MIN)
                and "scanner_silent" not in sirens
                and market_status.check(now_et).is_live):
            # the inverse siren: no fresh row while the market is live means the
            # scanner is down and writing NO honest flags — silence must also page
            push.send("🩺 SPX feed: scanner silent — market is live but no scan row "
                      f"in {int(_SCANNER_SILENT_MIN)} min", tag="gex-feed")
            sirens.add("scanner_silent")
            out["feed_sirens"] += 1
        st["feedSirens"] = sorted(sirens)
    except Exception as e:
        print(f"gex-alerts :: feed-health pass degraded: {e}", flush=True)

    _save_alerts_state(state_dir, st)
    return out
