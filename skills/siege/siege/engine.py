"""
engine.py — the SIEGE turn of the crank: one call per host scan folds the
tower watch, touch windows, effort verdicts, and outcome grades forward and
writes every store surface (latest / cards / health / baseline).

=========================== THE REVERSED SIGN =============================
Backtest sweep 2026-07 (n=168 touches, 11 of 13 days): HIGH volume effort
at a wall touch predicts the wall BREAKS — sieged walls held only 39% vs
54% for quiet touches. SIEGE = expect break. QUIET = expect hold. The
intuitive "absorption = strength" read is the WRONG sign on this tape;
never flip these labels back without a new sweep.
===========================================================================

Shadow with one reader: the only outputs are the siege store and the compact
dict the bridge writes into the diary row. Since the host's wt-7 era
(2026-07-18) the Watchtower reads that diary field into its payload
(siege_effort_at_walls) — the box itself still never imports host code.

FEED-LOST union guard: {no bars, last bar stale > 3 min in RTH, >= 3
consecutive zero-volume RTH bars, flat-OHLC-with-volume} — on any of them
the scan keeps last state (no touches, no verdicts, no grades, no baseline
fold), stamps health, and never fabricates.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from siege.basis import bars_by_minute, close_at, derive_ratio, minute_of_day
from siege.contracts import (BREAK, ENGAGED, FEED_DEGRADED, FEED_LOST, FEED_OK,
                             HOLD, NEUTRAL, QUIET, RESOLVED, SIEGE, TOWER_KINDS,
                             UNRESOLVED, WATCHING, SiegeConfig)
from siege.effort import BaselineStore, window_effort
from siege.store import SiegeStore
from siege.touch import (coverage_share, is_touch, open_episode, sigma_dist,
                         update_watch, window_range)


def feed_health(bars: list[dict], now: datetime,
                cfg: SiegeConfig) -> tuple[str, Optional[str]]:
    """The union guard — four dead-tape shapes, checked worst-first."""
    if not bars:
        return FEED_LOST, "no bars"
    cur = minute_of_day(now)
    in_rth = cfg.session_open_min <= cur < cfg.session_close_min
    try:
        age = (now - datetime.fromisoformat(str(bars[-1]["ts"]))).total_seconds()
    except (KeyError, ValueError, TypeError):
        return FEED_DEGRADED, "unreadable bar timestamp"
    if in_rth and age > cfg.stale_bar_s:
        return FEED_DEGRADED, f"last bar stale {int(age)}s"
    tail = bars[-cfg.zero_vol_run:]
    if in_rth and len(tail) >= cfg.zero_vol_run \
            and all((b.get("volume") or 0) == 0 for b in tail):
        return FEED_DEGRADED, f"{cfg.zero_vol_run} consecutive zero-volume bars"
    tail = bars[-cfg.flat_run:]
    if len(tail) >= cfg.flat_run \
            and all(b.get("open") == b.get("high") == b.get("low") == b.get("close")
                    for b in tail) \
            and all((b.get("volume") or 0) > 0 for b in tail):
        return FEED_DEGRADED, "flat-OHLC bars with volume"
    return FEED_OK, None


def _verdict(pct: float, cfg: SiegeConfig) -> str:
    """REVERSED SIGN: heavy effort = SIEGE = expect BREAK; quiet = expect HOLD."""
    if pct >= cfg.siege_pct:
        return SIEGE
    if pct <= cfg.quiet_pct:
        return QUIET
    return NEUTRAL


def _grade(ep: dict, spy_close: float, cfg: SiegeConfig) -> tuple[str, float]:
    """Outcome at +grade_delay after window close, on the FROZEN ratio (never
    re-derived): BREAK = beyond the frozen level in the breach direction by
    >= break_frac; HOLD = back on the original side; marooned inside the
    shoulder (or a magnet first seen at spot, no approach side) = UNRESOLVED."""
    px = spy_close * ep["ratio_used"]
    level, side = ep["level"], ep.get("side")
    move = round(px - level, 2)
    if side not in (-1, 1):
        return UNRESOLVED, move
    breach = -side
    if (px - level) * breach >= level * cfg.break_frac:
        return BREAK, move
    if (px - level) * side > 0:
        return HOLD, move
    return UNRESOLVED, move


def _resolve(store: SiegeStore, session: str, ep: dict, outcome: str,
             move_pts: Optional[float], now: datetime, coverage: float) -> None:
    """Stamp the episode + append its card row (the schema the viewstation
    reads — field names are FROZEN)."""
    ep["status"], ep["outcome"] = RESOLVED, outcome
    ep["ts_resolve"] = now.isoformat()
    store.append_card(session, {
        "ts_open": ep["ts_open"], "ts_resolve": ep["ts_resolve"],
        "tower_kind": ep["kind"], "level_frozen": ep["level"],
        "spy_level": ep["spy_level"], "ratio_used": ep["ratio_used"],
        "window_vol": ep["window_vol"], "baseline_med": ep["baseline_med"],
        "effort_basis": ep["effort_basis"], "effort_pct": ep["effort_pct"],
        "verdict": ep["verdict"], "outcome": outcome,
        "move_after_30m_pts": move_pts, "coverage_share": round(coverage, 3),
        "near_spot": ep["near_spot"], "degraded": ep["degraded"]})


def _flush_unresolved(store: SiegeStore, st: dict, now: datetime,
                      cfg: SiegeConfig) -> None:
    """Session rollover: episodes the clock stranded grade UNRESOLVED on
    their OWN day's card file — a new session starts with a clean slate."""
    old = st.get("session")
    if not old:
        return
    episodes = st.get("episodes") or []
    cov = coverage_share(episodes, cfg.session_close_min, cfg)
    for ep in episodes:
        if ep.get("status") == ENGAGED:
            ep["degraded"] = True
            _resolve(store, old, ep, UNRESOLVED, None, now, cov)


def _tower_rows(st: dict, towers: dict, spot: float, sigma,
                cfg: SiegeConfig) -> list[dict]:
    """One latest.json entry per tower kind: the open episode wins, else the
    last resolved episode while its level still stands, else plain watching."""
    rows = []
    eps = st["episodes"]

    def _sd(level):
        d = sigma_dist(spot, level, sigma)
        return round(d, 3) if d is not None else None

    for kind in TOWER_KINDS:
        open_ep = next((e for e in eps if e["kind"] == kind
                        and e["status"] == ENGAGED), None)
        level_now = towers.get(kind)
        if open_ep:
            rows.append({"kind": kind, "level": open_ep["level"],
                         "sigma_dist": _sd(open_ep["level"]), "status": ENGAGED,
                         "frozen_at": open_ep["ts_open"],
                         "effort_pct": open_ep["effort_pct"],
                         "verdict": open_ep["verdict"], "outcome": None,
                         "near_spot": open_ep["near_spot"]})
            continue
        done = [e for e in eps if e["kind"] == kind and e["status"] == RESOLVED]
        last = done[-1] if done else None
        if last and (level_now is None
                     or abs(level_now - last["level"]) <= cfg.level_repick_tol):
            rows.append({"kind": kind, "level": last["level"],
                         "sigma_dist": _sd(last["level"]), "status": RESOLVED,
                         "frozen_at": last["ts_open"],
                         "effort_pct": last["effort_pct"],
                         "verdict": last["verdict"], "outcome": last["outcome"],
                         "near_spot": last["near_spot"]})
            continue
        if level_now is not None:
            rows.append({"kind": kind, "level": level_now,
                         "sigma_dist": _sd(level_now), "status": WATCHING,
                         "frozen_at": None, "effort_pct": None, "verdict": None,
                         "outcome": None, "near_spot": False})
    return rows


def scan(store: SiegeStore, *, now: datetime, spot: Optional[float], sigma,
         towers: dict, spy_bars: list[dict],
         cfg: Optional[SiegeConfig] = None) -> Optional[dict]:
    """One SIEGE scan. `towers` = {kind: SPX level}, `spy_bars` = today's raw
    SPY minute bars ({ts, open, high, low, close, volume}). Returns the
    compact record the bridge writes into the diary row (or None when there
    is no spot to reason about)."""
    cfg = cfg or SiegeConfig()
    if not spot:
        return None
    session = _session_of(now)
    st = store.read_session()
    if st.get("session") != session:
        _flush_unresolved(store, st, now, cfg)
        st = {"session": session, "episodes": [], "watch": {}, "ratio": None}
    st.setdefault("watch", {})
    episodes = st.setdefault("episodes", [])

    baseline = BaselineStore(store.read_baseline(), cfg)
    feed, reason = feed_health(spy_bars, now, cfg)
    healthy = feed == FEED_OK
    by_min = bars_by_minute(spy_bars)
    minute_vols = {m: (b.get("volume") or 0) for m, b in by_min.items()
                   if cfg.session_open_min <= m < cfg.session_close_min}
    cur_min = minute_of_day(now)

    if healthy:
        baseline.fold_today(session, minute_vols)
        store.write_baseline(baseline.data())
        r = derive_ratio(spot, spy_bars, now, cfg.bar_match_tol_min)
        if r:
            st["ratio"] = r            # else: carry the last frozen ratio
    ratio = st.get("ratio")

    if not healthy:
        for ep in episodes:            # this episode lived through a feed hole
            if ep["status"] == ENGAGED:
                ep["degraded"] = True

    # 1) tower watch + touch detection. DEGRADED/LOST scans change nothing:
    #    keep last state, never open a window off bars we don't trust.
    engaged_kinds = {ep["kind"] for ep in episodes if ep["status"] == ENGAGED}
    done_levels = {(ep["kind"], round(ep["level"], 2)) for ep in episodes}
    for kind, level in towers.items():
        if level is None:
            continue
        w = update_watch(st["watch"], kind, level, spot, sigma, cfg)
        if (healthy and ratio and kind not in engaged_kinds
                and cur_min <= cfg.last_open_min
                and (kind, round(w["level"], 2)) not in done_levels
                and is_touch(spot, w["level"], cfg)):
            # coverage BEFORE this window opens — saturation suppresses the
            # NEW verdict, it doesn't erase the record
            saturated_now = coverage_share(episodes, cur_min, cfg) > cfg.saturation_share
            episodes.append(open_episode(kind, w, ratio, now, saturated_now, cfg))
            engaged_kinds.add(kind)

    if healthy:
        # 2) verdicts at window close (bars through minute_open+window exist)
        for ep in episodes:
            if ep["status"] != ENGAGED or ep["effort_pct"] is not None \
                    or cur_min <= ep["minute_open"] + cfg.window_min:
                continue
            eff = window_effort(baseline, session,
                                window_range(ep["minute_open"], cfg),
                                minute_vols, cfg)
            if not eff:
                continue
            ep.update(eff)
            # suppressed verdicts (near-spot artifact / saturated session)
            # stay None — the effort number is still recorded for the sweep
            if not ep["near_spot"] and not ep["suppressed"]:
                ep["verdict"] = _verdict(ep["effort_pct"], cfg)

        # 3) outcome grading at window close + grade_delay
        for ep in episodes:
            if ep["status"] != ENGAGED:
                continue
            grade_min = ep["minute_open"] + cfg.window_min + cfg.grade_delay_min
            if cur_min < grade_min:
                continue
            cov = coverage_share(episodes, cur_min, cfg)
            close, _m = close_at(by_min, grade_min, cfg.bar_match_tol_min)
            if close is None:
                if cur_min >= cfg.session_close_min:   # the day ran out first
                    ep["degraded"] = True
                    _resolve(store, session, ep, UNRESOLVED, None, now, cov)
                continue                # defer: bars may retro-fill next scan
            outcome, move = _grade(ep, close, cfg)
            _resolve(store, session, ep, outcome, move, now, cov)

    coverage = coverage_share(episodes, cur_min, cfg)
    saturated = coverage > cfg.saturation_share
    tower_rows = _tower_rows(st, towers, spot, sigma, cfg)

    store.write_session(st)
    store.write_health({"ts": now.isoformat(), "session": session,
                        "feed": feed, "reason": reason})
    store.write_latest({
        "as_of": now.isoformat(), "session": session, "spot": spot,
        "spx_spy_ratio": round(ratio, 6) if ratio else None,
        "baseline": {"days_accrued": baseline.days_accrued(session),
                     "label": baseline.label(session)},
        "health": {"feed": feed, "reason": reason},
        "saturated": saturated, "towers": tower_rows})

    return {"health": feed, "saturated": saturated,
            "baseline": baseline.label(session),
            "ratio": round(ratio, 4) if ratio else None,
            "towers": [{k: t[k] for k in ("kind", "level", "status", "effort_pct",
                                          "verdict", "outcome", "near_spot")}
                       for t in tower_rows]}


def _session_of(now: datetime) -> str:
    from siege.basis import ET
    return (now.astimezone(ET) if now.tzinfo else now).date().isoformat()
