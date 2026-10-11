"""gex_alerts.py — the ALERT BELL for the gex-only system.

Ported from the retired tick_graph alert_push node (2026-07-03 gex-module
restructure). Runs once per tick AFTER the left-eye scan
(run-watch-left-eye.sh → `python -m watch.cli gex-alerts`). Three jobs:

  1. FRESH PAPER FIRES → phone. Reads today's hunter heartbeats
     (skills/mirai-left-eye/logs/{date}.jsonl) and pushes any Fade-Lens fire
     not already pushed (sent-ledger dedup — one push per fire, ever).
  2. THE WALL-BREACH BELL. When spot pushes CONCRETELY beyond a dealer wall
     (the engine's recorded crossing, or the fallback check: buffer = frac ×
     the lens's σ yardstick), page the phone once per wall per cooldown.
     Walls, spot and σ come from the LENS DIARY (state/reversion/{date}.jsonl):
     the gravity engine's own numbers, no separate market feed.
  3. THE FEED SIRENS. A fresh diary row carrying a degraded-feed fact pages
     once per outage, with an all-clear when it heals; a live market with no
     row for too long pages the scanner silent.

Until 2026-10-11 a breach also re-dived the morning macro brief's analyst and
a pass after the close scored the brief's direction call; the brief retired
and both went with it. Nothing writes state/market_expectation/ now but the
alerts state below.

All best-effort: any failure costs a log line, never a tick. Idempotent
state: state/market_expectation/alerts_state.json (resets per day). The
feed-health pass also writes state/spx_jev/mirai_prediction/options_data.json
on every in-session tick, the SPX phone page's options-data banner.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .. import paths, push
from . import market_status, push_ntfy, settings

import station_paths                       # on sys.path through watch.paths

ET = ZoneInfo("America/New_York")
# SPX only, and deliberately so. This module's passes are index-shaped — the
# wall-breach bell reads the SPX diary's walls and the sirens its feed flags —
# so adding "SNDK" here would not give the stock a watchdog, it would give it
# a second bell nobody asked for.
#
# SNDK's scanner IS watched, by its own process: runtime/watch/intraday/
# sndk_deadman.py on com.mirai-station.sndk-deadman (2026-08-30). It lives
# outside this module on purpose — gex_alerts runs from the left-eye tick, so
# a siren that shares that process cannot report the tick's own death. Four
# SNDK sessions died unpaged in August (08-07, 08-14, 08-17, 08-24) while this
# tuple sat here looking like coverage.
LENS_TICKERS = ("SPX",)

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

# THE OTHER HALF OF EVERY SIREN (2026-09-09). Until today a degraded feed paged
# once and then nothing — the last thing the phone ever said about the SPX feed
# was that it was broken, however long ago it healed. On 2026-09-09 the native
# chain fell to the SPY proxy at 09:37 ET and came back at 15:51; the degrade
# page landed correctly and the recovery never came, so a phone glanced at after
# lunch reported a proxy day that had ended hours earlier. The SNDK dead-man's
# switch learned this in August and carries a recovery page for exactly this
# reason; the gravity feed's siren never got one.
_FEED_RECOVERY_TEXT = {
    "no_zero_dte": "today's 0DTE expiry is back in the chain",
    "zero_dte_dead": "today's 0DTE book is answering again",
    "flow_dead": "options flow back — aggressor ticks landing again",
    "scanner_silent": "scanner back — scan rows landing again",
}


def _recovery_text(flag: str) -> str:
    """What the all-clear says for every flag but the `source:` family, whose
    page is the options-data one below."""
    return _FEED_RECOVERY_TEXT.get(flag, f"back to normal: {flag}")


# THE OPTIONS PROVIDER'S OUTAGE (2026-10-08). On 10-06..08 the provider sent empty
# SPX books, the scanner ran on the SPY stand-in (`source:` flag), and the 14
# questions built from the provider's data went blank. The phone got two
# routine-looking pages (the stand-in book at once, flow_dead at 11:00) that said
# neither what was lost nor whose problem it was. Now the stand-in book pages
# once, after BOOK_OUT_WAIT_MIN straight, in those terms, and flow_dead keeps
# quiet while it stands (same provider, same outage).
#
# The provider's questions (skills/spx-jev/spx_jev/mirai_prediction/
# code_features.py PROVIDER_QUESTIONS) and the catalog's size
# (code_feature_catalog.json). This module imports nothing from spx_jev;
# runtime/watch/tests/test_gex_alerts.py holds these copies to it.
PROVIDER_QUESTIONS = ("OPTIONS-01", "OPTIONS-02", "OPTIONS-03", "OPTIONS-04",
                      "OPTIONS-05", "OPTIONS-06", "OPTIONS-07", "OPTIONS-08",
                      "OPTIONS-10", "OPTIONS-11", "VOLATILITY-01",
                      "VOLATILITY-09", "VOLATILITY-10", "VOLATILITY-16")
CODE_QUESTIONS_COUNT = 79
BOOK_OUT_WAIT_MIN = 15.0


def _duration_text(minutes: float) -> str:
    """'15 min', '1 h', '4 h 52 min'."""
    h, m = divmod(int(minutes), 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m or not h else "") if p)


def _book_down_text(minutes_out: float) -> str:
    n = len(PROVIDER_QUESTIONS)
    return (f"Options data down — {n} of {CODE_QUESTIONS_COUNT} SPX questions are blank. "
            f"The options provider has sent empty books for {_duration_text(minutes_out)}. "
            f"The call still runs on the other {CODE_QUESTIONS_COUNT - n}. Nothing to do on our side.")


def _book_back_text(minutes_out: float) -> str:
    return (f"Options data back — all {CODE_QUESTIONS_COUNT} questions answering again. "
            f"Down {_duration_text(minutes_out)}.")


def wall_breach(spot: Optional[float], call_wall: Optional[float],
                put_wall: Optional[float], implied_move: Optional[float],
                buffer_frac: float) -> Optional[Dict[str, Any]]:
    """Has price pushed CONCRETELY BEYOND a dealer gamma wall? (the bell's fallback check,
    for a row the engine recorded no crossing on).

    A breach fires only when spot is past the wall by a buffer — `buffer_frac` of the
    implied daily move (falling back to 0.05% of spot when implied_move is unknown).
    An ordinary pullback that merely *touches* or sits inside the walls does NOT fire;
    the point is a structural level giving way, not intraday noise.

    Returns {"wall": "call"|"put", "direction": +1|-1, "level": float,
             "beyond": float} or None. Direction is the breakout side: a call-wall
    breach is bullish (+1), a put-wall breach bearish (-1)."""
    if spot is None:
        return None
    buf = (abs(implied_move) * buffer_frac if implied_move is not None
           else abs(spot) * 0.0005)
    if call_wall is not None and spot > call_wall + buf:
        return {"wall": "call", "direction": 1, "level": float(call_wall),
                "beyond": round(spot - call_wall, 4)}
    if put_wall is not None and spot < put_wall - buf:
        return {"wall": "put", "direction": -1, "level": float(put_wall),
                "beyond": round(put_wall - spot, 4)}
    return None


def _options_data_path(state_dir: Path) -> Path:
    return station_paths.OPTIONS_FEED_STATUS_CARD.path(state_dir)


def _save_options_data(state_dir: Path, doc: Dict[str, Any]) -> None:
    """The phone's banner reads this; a reader must never see half a file."""
    p = _options_data_path(state_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(doc))
        os.replace(tmp, p)
    except OSError:
        pass


def _alerts_state_path(state_dir: Path) -> Path:
    return station_paths.PHONE_PUSHES_WALL_TIMERS_FEED_OUTAGES_STATE.path(state_dir)


def _load_alerts_state(state_dir: Path, date_iso: str) -> Dict[str, Any]:
    fresh = {"date": date_iso, "pushed": [], "wallTimers": {}}
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
    path = station_paths.SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN.path(state_dir, day=date_iso)
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


def _on_stand_in(row: dict) -> bool:
    """The row's gravity map ran on the stand-in book, not SPX's own."""
    return any(f.startswith("source:") for f in _feed_health_flags(row))


def _stand_in_since(rows: List[dict]) -> Optional[str]:
    """The ts of the first row of the trailing run of stand-in-book rows: when
    this outage began. None when the newest lens row is on the native book."""
    since = None
    for r in reversed(rows):
        if r.get("ticker") not in LENS_TICKERS:
            continue
        if not _on_stand_in(r):
            break
        since = r.get("ts")
    return since


def _hunter_fires(date_iso: str) -> List[dict]:
    """Every Fade-Lens fire in today's hunter heartbeats."""
    path = station_paths.SPX_OPTIONS_SCANNER_SIGNAL_CHECKS_BY_SCAN.path(paths.PLUGIN_ROOT, day=date_iso)
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
        channel=None) -> Dict[str, Any]:
    """One alert pass. The push channel is injectable for tests; the default is live."""
    now = now or datetime.now(tz=ET)
    now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    date_iso = now_et.date().isoformat()
    minutes = now_et.hour * 60 + now_et.minute
    state_dir = Path(state_dir or paths.STATE_DIR)
    push.set_channel(channel or push_ntfy.make_channel())

    st = _load_alerts_state(state_dir, date_iso)
    out: Dict[str, Any] = {"pushed": 0, "breaches": 0, "feed_sirens": 0,
                           "feed_recoveries": 0}
    rows = _lens_rows(state_dir, date_iso)   # today's diary — passes 2 and 3 read it

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

    # --- 2) the wall-breach bell (one page per wall per cooldown) --------------
    try:
        latest_by_tk = {}
        for r in rows:
            if r.get("ticker") in LENS_TICKERS:
                latest_by_tk[r["ticker"]] = r

        cooldowns = dict(st.get("wallTimers") or {})
        buffer_frac = settings.wall_breach_buffer_frac()
        cd_min = settings.wall_breach_cooldown_min()

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
            br = wall_breach(r.get("spot"), r.get("call_wall"), r.get("put_wall"),
                             r.get("sigma"), buffer_frac)
            if br:
                breach_cands.append((tk, r, br))
        for tk, r, br in breach_cands:
            out["breaches"] += 1
            key = f"{tk}:{br['wall']}"
            last = cooldowns.get(key)
            if last is not None and (minutes - float(last)) < cd_min:
                continue
            # N1: the bell rings on every cooldown-fresh breach. The breach is the
            # fact; what was done about it (until 2026-10-11, a re-dive of the morning
            # macro brief's analyst) was never the bell's business.
            push.send(f"🧱 {tk} broke its {br['wall']} wall "
                      f"({br['beyond']:+.1f} beyond)",
                      tag="gex-wall")
            cooldowns[key] = minutes
        st["wallTimers"] = cooldowns
    except Exception as e:
        print(f"gex-alerts :: wall-breach pass degraded: {e}", flush=True)

    # --- 3) feed-health siren (2026-07-13) -------------------------------------
    # The 0DTE-discovery outage wrote `no_zero_dte` honestly on every live row and
    # paged no one — a diary flag is not an alarm. A fresh row means the scanner is
    # in-session right now; each degraded-feed fact on it pushes once per OUTAGE
    # (2026-09-09 — it was once per DAY, which made a heal-and-break-again session
    # mute after the first break) and every siren now has an all-clear to match.
    try:
        sirens = set(st.get("feedSirens") or [])
        down_since = dict(st.get("feedDownSince") or {})
        latest = next((r for r in reversed(rows)
                       if r.get("ticker") in LENS_TICKERS), None)
        age = _row_age_min(latest, now_et) if latest else None
        tk = (latest.get("ticker") if latest else None) or "SPX"

        def _siren(flag: str, text: str) -> None:
            """Page ONCE PER OUTAGE, and only remember a page that was delivered.

            Delivery, not intent: push.send swallows the channel's exception and
            reports dispatched: False (unreachable ntfy, revoked topic). Marking
            the flag anyway would retire the one notification the outage was ever
            going to get. An undelivered siren stays un-marked so the next tick
            tries again, and says so out loud."""
            if flag in sirens:
                return
            rec = push.send(f"🩺 {text}" if flag.startswith("source:")
                            else f"🩺 {tk} feed: {text}", tag="gex-feed")
            if not rec.get("dispatched"):
                out.setdefault("undelivered", []).append(
                    {"flag": flag, "why": rec.get("error") or "channel did not dispatch"})
                return
            sirens.add(flag)
            down_since[flag] = now_et.isoformat()
            out["feed_sirens"] += 1

        def _recover(flag: str) -> None:
            """The all-clear, which also RE-ARMS the siren for a second outage
            the same session. The old code never cleared a flag, so a feed that
            broke at 09:37, healed at 11:00 and broke again at 14:00 paged once
            for the morning and stayed mute through the afternoon."""
            if flag not in sirens:
                return
            if flag.startswith("source:"):
                text = _book_back_text(_row_age_min(
                    {"ts": st.get("bookOutSince") or down_since.get(flag)}, now_et) or 0.0)
            else:
                since = down_since.get(flag)
                when = f" (degraded since {str(since)[11:16]} ET)" if since else ""
                text = f"{tk} feed: {_recovery_text(flag)}{when}"
            rec = push.send(f"🩺 {text}", tag="gex-feed")
            if not rec.get("dispatched"):
                out.setdefault("undelivered", []).append(
                    {"flag": flag + ":recovery",
                     "why": rec.get("error") or "channel did not dispatch"})
                return
            sirens.discard(flag)
            down_since.pop(flag, None)
            out["feed_recoveries"] += 1

        if age is not None and age < _FEED_FRESH_MIN:
            flags = _feed_health_flags(latest)
            book_out = next((f for f in flags if f.startswith("source:")), None)
            out_min = None
            if book_out:
                # the outage starts at its first stand-in row, kept so a page that
                # waits (or retries) still measures from the real start
                st["bookOutSince"] = st.get("bookOutSince") or _stand_in_since(rows)
                out_min = _row_age_min({"ts": st["bookOutSince"]}, now_et)
            for flag in flags:
                if flag == book_out:
                    if out_min is not None and out_min >= BOOK_OUT_WAIT_MIN:
                        _siren(flag, _book_down_text(out_min))
                    continue
                _siren(flag, _FEED_SIREN_TEXT.get(
                    flag, f"gravity feed degraded: {flag}"))
            # A fresh row IS the scanner speaking — clear the silence siren first
            # so the sweep below never sees a flag this branch already answered.
            _recover("scanner_silent")
            # Anything still standing that this fresh row no longer reports has
            # healed. flow_dead is excluded: it is a whole-session claim, not a
            # per-row flag, so its own recovery test lives with its own siren.
            for flag in sorted(sirens - set(flags) - {"flow_dead"}):
                _recover(flag)
            # the outage is over once the book is native and its all-clear (if it
            # ever paged) is delivered; one shorter than the wait leaves no trace
            if (not book_out and st.get("bookOutSince")
                    and not any(f.startswith("source:") for f in sirens)):
                lasted = _row_age_min({"ts": st["bookOutSince"]}, now_et)
                if lasted is not None and lasted >= BOOK_OUT_WAIT_MIN:
                    st["bookLastSince"], st["bookBackAt"] = st["bookOutSince"], now_et.isoformat()
                st["bookOutSince"] = None
            down = bool(book_out and out_min is not None and out_min >= BOOK_OUT_WAIT_MIN)
            _save_options_data(state_dir, {
                "down": down,
                "since": st.get("bookOutSince") if down else st.get("bookLastSince"),
                "back_at": None if down else st.get("bookBackAt"),
                "blank_questions": list(PROVIDER_QUESTIONS) if down else [],
                "total_questions": CODE_QUESTIONS_COUNT,
                "updated": now_et.isoformat()})
            # dead-flow siren (2026-07-20): the option_trade_flow feed was silently
            # empty for FIVE consecutive sessions (the provider's right:'both' path
            # died in its 07-11/12 deploy) and nothing paged — aggressor_flow=None
            # fails open by design ("no ticks yet"), so per-row honesty never trips.
            # A whole morning of Nones is not "no ticks yet": if every lens row so
            # far is flow-blind past mid-morning, the FEED is dead — page once.
            # Not while the book is on the stand-in, and stand-in rows are no
            # evidence after it: the flow comes from the same provider, so the
            # options-data page already said it. On 10-08 the book came back at
            # 14:20 on a flow-blind row, and a morning of stand-in rows would
            # have paged "flow dead" right behind "options data back".
            lens_rows = [r for r in rows
                         if r.get("ticker") in LENS_TICKERS and not _on_stand_in(r)]
            if ("flow_dead" not in sirens and not st.get("bookOutSince")
                    and len(lens_rows) >= 20
                    and now_et.hour * 60 + now_et.minute >= 11 * 60
                    and all((r.get("gex_theta") or {}).get("aggressor_flow") is None
                            for r in lens_rows)):
                _siren("flow_dead",
                       "options flow dead — every scan today is flow-blind "
                       "(aggressor_flow None); check option_trade_flow upstream "
                       "(right:'both' regression family)")
            elif ("flow_dead" in sirens
                    and (latest.get("gex_theta") or {}).get("aggressor_flow") is not None):
                # one live tick is the whole claim refuted: "every scan today is
                # flow-blind" stops being true the moment a scan is not
                _recover("flow_dead")
        elif ((age is None or age >= _SCANNER_SILENT_MIN)
                and "scanner_silent" not in sirens
                and market_status.check(now_et).is_live):
            # the inverse siren: no fresh row while the market is live means the
            # scanner is down and writing NO honest flags — silence must also page
            _siren("scanner_silent",
                   "scanner silent — market is live but no scan row "
                   f"in {int(_SCANNER_SILENT_MIN)} min")
        st["feedSirens"] = sorted(sirens)
        st["feedDownSince"] = down_since
    except Exception as e:
        print(f"gex-alerts :: feed-health pass degraded: {e}", flush=True)

    _save_alerts_state(state_dir, st)
    return out
