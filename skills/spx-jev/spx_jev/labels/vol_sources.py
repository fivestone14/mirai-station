"""The volatility family's own sources, read point in time from the station's state: the prior sessions'
diary series (the VIX, the straddle and the VIX curve through each day).

* Prior diaries. A same-clock rank of the straddle or the VIX curve needs each prior session's rows, and
  the Scene keeps only each day's first. They are read here, only for days before the one being built,
  and kept for the process: a past day's file never changes.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from ..state_builder import load_rows
from .measures import is_num

# The scanner writes a row every one to two minutes; a row further than this from a moment does not stand for it.
ROW_MAX_GAP = timedelta(minutes=10)


# ----------------------------------------------------------------------------- prior diaries

@dataclass(frozen=True)
class DiaryPoint:
    """What one diary row says about volatility at its time; a field the row lacks is None."""
    ts: datetime
    vix: float | None
    vix_ts: float | None
    atm_iv: float | None
    em_points: float | None
    em_open: float | None


def diary_point(row: dict) -> DiaryPoint:
    ruler = row.get("range_ruler") or {}
    vix = (ruler.get("vol_carry") or {}).get("vix")

    def num(v):
        return float(v) if is_num(v) and v > 0 else None

    return DiaryPoint(datetime.fromisoformat(row["ts"]), num(vix), num(row.get("vix_ts")), num(row.get("atm_iv")),
                      num(ruler.get("em_points")), num(ruler.get("em_open")))


@lru_cache(maxsize=32)
def prior_diary(state_dir: Path, day: str) -> tuple[DiaryPoint, ...]:
    """A finished session's diary as DiaryPoints, oldest first; empty when its file is missing."""
    return tuple(diary_point(r) for r in load_rows(state_dir, day))


def point_at(points: Sequence[DiaryPoint], t: datetime) -> DiaryPoint | None:
    """The newest point stamped at or before ``t``, when it is within ROW_MAX_GAP of it: an older one
    says where things stood before a gap in the diary, not at ``t``."""
    done = [p for p in points if p.ts <= t]
    return done[-1] if done and done[-1].ts >= t - ROW_MAX_GAP else None
