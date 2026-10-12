"""
touch.py — tower watching + the touch window geometry.

A TOUCH is spot arriving at a tower: |spot − level| <= 0.001·spot. At first
touch the level is FROZEN into an episode — a re-picked wall NEVER re-anchors
an open window (the host's gravity engine re-picks walls freely; the question
"did THIS level hold" only means something if the level stops moving the
moment the fight starts). One episode per (tower, frozen level) per session.

NEAR-SPOT guard: a tower that was never observed >= 0.25σ away from spot
before its touch is the spot-hugging ATM artifact (the raw 0DTE wall rides
spot — the same lesson as operative_wall's placement floor), not a level spot
travelled to. Its episode is still recorded, but the verdict is suppressed.
A level re-pick resets the watch history: the old level's standing distance
must not launder a fresh spot-hugging pick.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from siege.basis import minute_of_day
from siege.contracts import CALL_WALL, ENGAGED, PUT_WALL, SiegeConfig


def sigma_dist(spot, level, sigma) -> Optional[float]:
    if not spot or level is None or not sigma:
        return None
    return abs(spot - level) / sigma


def is_touch(spot: float, level: float, cfg: SiegeConfig) -> bool:
    return abs(spot - level) <= cfg.touch_tol_frac * spot


def update_watch(watch: dict, kind: str, level: float, spot: float,
                 sigma, cfg: SiegeConfig) -> dict:
    """Fold one scan's tower observation into the per-kind watch entry:
    the max σ-distance seen (the near-spot evidence) and the approach side
    (the last side spot stood on while NOT touching)."""
    w = watch.get(kind)
    if w is None or abs(level - w["level"]) > cfg.level_repick_tol:
        w = {"level": level, "max_sigma_dist": None, "side": None}
        watch[kind] = w
    w["level"] = level
    d = sigma_dist(spot, level, sigma)
    if d is not None:
        w["max_sigma_dist"] = d if w["max_sigma_dist"] is None else max(w["max_sigma_dist"], d)
    if not is_touch(spot, level, cfg) and spot != level:
        w["side"] = 1 if spot > level else -1
    return w


def open_episode(kind: str, w: dict, ratio: float, now: datetime,
                 saturated: bool, cfg: SiegeConfig) -> dict:
    """FREEZE the level at first touch. The window is first-touch ±window_min;
    the SPY-side level and the basis ratio are frozen here too (never
    re-derived — see basis.py)."""
    level = w["level"]
    side = w.get("side")
    if side is None:
        # never seen off the level: walls still have a geometric side; a
        # magnet does not, and grades UNRESOLVED without one
        side = {CALL_WALL: -1, PUT_WALL: 1}.get(kind)
    return {"kind": kind, "level": level,
            "spy_level": round(level / ratio, 4), "ratio_used": ratio,
            "ts_open": now.isoformat(), "minute_open": minute_of_day(now),
            "side": side,
            "near_spot": (w.get("max_sigma_dist") or 0.0) < cfg.near_spot_sigma,
            "suppressed": saturated, "status": ENGAGED, "degraded": False,
            "effort_pct": None, "window_vol": None, "baseline_med": None,
            "effort_basis": None, "verdict": None, "outcome": None}


def window_range(minute_open: int, cfg: SiegeConfig) -> list[int]:
    """The touch window's RTH minutes: first-touch ±window_min, clipped."""
    lo = max(cfg.session_open_min, minute_open - cfg.window_min)
    hi = min(cfg.session_close_min - 1, minute_open + cfg.window_min)
    return list(range(lo, hi + 1))


def coverage_share(episodes: list[dict], cur_min: int, cfg: SiegeConfig) -> float:
    """Share of the elapsed session covered by touch windows — the coverage
    self-invalidation input: a day that is one long touch can't tell a touch
    from the day."""
    covered: set[int] = set()
    for ep in episodes:
        covered.update(m for m in window_range(ep["minute_open"], cfg)
                       if m <= cur_min)
    elapsed = max(1, cur_min - cfg.session_open_min + 1)
    return len(covered) / elapsed
