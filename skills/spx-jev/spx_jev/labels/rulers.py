"""The rulers a distance or a size is measured in.

The tape unit, for the opening lane: the median high-to-low range of the last three finished 5-minute
slices. Held at RULER_HOLD_SIGMA until 09:45, when three slices first exist; floored at
RULER_FLOOR_SIGMA so a dead tape still has a unit; never capped, so a wild open is measured as wild.
"""
from __future__ import annotations

import statistics
from datetime import datetime, time, timedelta

from ..cuts import RULER_FLOOR_SIGMA, RULER_HOLD_SIGMA
from .measures import bars_finished_between, minute_of_day, slot
from .ranks import rank_at_slot

RULER_SLICE_MIN = 5
RULER_SLICES = 3
RULER_HOLD_UNTIL = time(9, 45)


def ruler(bars: list[dict], sigma: float, now: datetime) -> dict | None:
    """The tape unit for a read at ``now``: ``unit_points``, ``unit_sigma``, the ``slices_used`` and
    its ``source`` (held, tape or floor). None when the newest slice has no bars: the tape has
    stopped, and a unit from an older stretch would pass off the last move as the current one."""
    if now.time() < RULER_HOLD_UNTIL:
        return {"unit_points": round(RULER_HOLD_SIGMA * sigma, 2), "unit_sigma": RULER_HOLD_SIGMA, "slices_used": 0, "source": "held"}
    ranges = []
    for k in range(RULER_SLICES):
        end = now - timedelta(minutes=RULER_SLICE_MIN * k)
        sl = bars_finished_between(bars, end - timedelta(minutes=RULER_SLICE_MIN), end)
        if sl:
            ranges.append(max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl))
        elif k == 0:
            return None
    unit = statistics.median(ranges)
    if unit < RULER_FLOOR_SIGMA * sigma:
        return {"unit_points": round(RULER_FLOOR_SIGMA * sigma, 2), "unit_sigma": RULER_FLOOR_SIGMA, "slices_used": len(ranges), "source": "floor"}
    return {"unit_points": round(unit, 2), "unit_sigma": round(unit / sigma, 3), "slices_used": len(ranges), "source": "tape"}


def slice_range(bars: list[dict], end_min: int) -> float | None:
    """High-to-low range of the 5-minute slice ending at ``end_min`` (a minute of day); None with no bars."""
    sl = slot(bars, end_min - RULER_SLICE_MIN, end_min)
    return max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl) if sl else None


def unit_rank(unit: dict, prior_bars: dict[str, list[dict]], now: datetime) -> dict | None:
    """The tape unit against the same minute on the prior sessions: the same three slices measured on
    each prior day's bars, placed in thirds (rank_at_slot). None while the unit is held (before 09:45
    every day's unit is the same number) and when fewer than MIN_RANK_SESSIONS prior sessions carry
    all three slices."""
    if unit.get("source") == "held":
        return None
    end_min = minute_of_day(now)
    base = []
    for pbars in prior_bars.values():
        ranges = [slice_range(pbars, end_min - RULER_SLICE_MIN * k) for k in range(RULER_SLICES)]
        if all(r is not None for r in ranges):
            base.append(statistics.median(ranges))
    return rank_at_slot(float(unit["unit_points"]), base)


def tape_unit(bars: list[dict], sigma: float, now: datetime, prior_bars: dict[str, list[dict]]) -> dict | None:
    """The unit for a read on the tape lane: ruler() with its rank against the prior sessions at this
    minute under ``rank`` when there is one. What the record, the card and the sum's context line carry."""
    unit = ruler(bars, sigma, now)
    if unit:
        rank = unit_rank(unit, prior_bars, now)
        if rank:
            unit["rank"] = rank
    return unit
