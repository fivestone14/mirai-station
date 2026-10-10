"""How numbers are written in the payload, in one place, so every block says the same thing the same way.

Every distance or move is given in ``sig`` (the read's expected daily move in index points) and never in
index points or as a price level, because Claude must not see a number it could recall. A value that is
ranked against the same minute on earlier sessions rides as ``{"v": value, "r": [higher_than, of]}``:
"bigger than ``higher_than`` of the last ``of`` sessions at this minute". The ranks use the station's own
rule (``spx_jev.labels.ranks.rank_sessions``: up to 20 sessions, at least 10).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Sequence

from .. import station_stores

SIG_DECIMALS = 2
PCT_DECIMALS = 1


def sig(points: float | None, sigma_points: float, decimals: int = SIG_DECIMALS) -> float | None:
    """A move or distance in index points as a share of the day's expected move."""
    if points is None or not sigma_points:
        return None
    return round(float(points) / float(sigma_points), decimals)


def pct(value: float | None, decimals: int = PCT_DECIMALS) -> float | None:
    return None if value is None else round(float(value), decimals)


def ranked(value: float | None, base: Sequence[float], what: str = "the same minute on earlier sessions") -> dict | None:
    """``{"v": value, "r": [higher_than, of]}``, or ``{"v": value}`` when there are too few sessions to rank, or None."""
    if value is None:
        return None
    rank, _ = _rank_sessions()(float(value), [float(b) for b in base if b is not None], what)
    if rank is None:
        return {"v": value}
    return {"v": value, "r": [rank.higher_than, rank.of]}


def _rank_sessions() -> Callable:
    return station_stores.import_spx_jev("labels.ranks").rank_sessions


def hhmm(when: datetime | None) -> str | None:
    return None if when is None else when.strftime("%H:%M")


def minutes_ago(when: datetime | None, cut: datetime) -> int | None:
    return None if when is None else int((cut - when).total_seconds() // 60)


def same_clock_values(scene: Any, measure: Callable[[list[dict], datetime, float], float | None]) -> list[float]:
    """``measure(bars, then, sigma)`` on each prior session at the same clock as now, newest first, through the
    station's own helper so the rank base is the one its labels use."""
    ranks = station_stores.import_spx_jev("labels.ranks")
    values = ranks.same_clock_values(scene, measure)
    return [v for v in values if v is not None]


def clean(block: dict) -> dict:
    """Drop None values so an absent fact is declared in ``absent`` rather than written as null."""
    return {k: v for k, v in block.items() if v is not None}
