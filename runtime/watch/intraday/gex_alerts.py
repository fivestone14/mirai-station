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
from . import macro_mood, push_ntfy, settings

ET = ZoneInfo("America/New_York")
LENS_TICKERS = ("SPX",)
_EOD_MINUTES = 16 * 60


def _alerts_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "market_expectation" / "alerts_state.json"


def _load_alerts_state(state_dir: Path, date_iso: str) -> Dict[str, Any]:
    fresh = {"date": date_iso, "pushed": [], "redives": 0, "wallTimers": {},
             "refOpen": None, "refLast": None}
    try:
        st = json.loads(_alerts_state_path(state_dir).read_text())
        return st if st.get("date") == date_iso else fresh
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
        for ln in path.read_text().splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


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
    out: Dict[str, Any] = {"pushed": 0, "breaches": 0, "redives": 0, "eod_scored": False}

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
        rows = _lens_rows(state_dir, date_iso)
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

    _save_alerts_state(state_dir, st)
    return out
