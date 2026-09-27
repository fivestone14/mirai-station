"""Reads over minute bars that more than one family needs: windows, the close at a moment, a day's range,
the walls on the row, RSI. Bars are dicts with an ISO ``ts`` (the minute's start) and open, high, low
and close; a bar counts once its minute has finished (``ts`` plus one minute).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ONE_MINUTE = timedelta(minutes=1)
# The settled open is the close of the 09:34 bar: the 09:30 print is made of stale opening quotes (a
# median 0.115 sigma off), and 28% of a real gap is made after it.
SETTLED_OPEN_BAR = time(9, 34)
RSI_PERIOD = 14                 # Wilder's RSI over 14 closes
# A move past two typical minutes (twice the median 1-minute range of the last 30 bars) is a real step.
MOVE_BAR_MINUTES = 2
# Today against the most recent prior sessions (their range, or theirs at the same time of day).
RANGE_PRIOR_SESSIONS = 5
MIN_RANGE_SESSIONS = 3


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def bar_time(b: dict) -> datetime:
    """When the bar's minute started."""
    return datetime.fromisoformat(b["ts"])


def minute_of_day(t: datetime) -> int:
    return t.hour * 60 + t.minute


def bars_between(bars: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Bars that started in [start, end)."""
    return [b for b in bars if start <= bar_time(b) < end]


def bars_finished_between(bars: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Bars that finished in (start, end], so a window never leans on an unfinished minute."""
    return [b for b in bars if start < bar_time(b) + ONE_MINUTE <= end]


def close_at(bars: list[dict], t: datetime) -> float | None:
    """Close of the last bar that had finished by ``t``: where price stood at that moment."""
    cands = [b for b in bars if bar_time(b) + ONE_MINUTE <= t]
    return float(cands[-1]["close"]) if cands else None


def slot(bars: list[dict], start_min: int, end_min: int) -> list[dict]:
    """Bars whose minute of day falls in [start_min, end_min)."""
    return [b for b in bars if start_min <= minute_of_day(bar_time(b)) < end_min]


def stretch(bars: list[dict], start_min: int, end_min: int) -> tuple[float | None, list[dict]]:
    """The bars of a day in the clock window [start_min, end_min) and the close just before it: the
    anchor a move is measured from."""
    before = [b for b in bars if minute_of_day(bar_time(b)) < start_min]
    return (float(before[-1]["close"]) if before else None), slot(bars, start_min, end_min)


def stretch_range(win: list[dict]) -> float:
    return max(float(b["high"]) for b in win) - min(float(b["low"]) for b in win)


def high_low_close(bars: list[dict]) -> tuple[float, float, float]:
    return (max(float(x["high"]) for x in bars), min(float(x["low"]) for x in bars), float(bars[-1]["close"]))


def day_high_low(bars: list[dict], spot: float) -> tuple[float, float]:
    """Today's high and low so far, counting spot, which can sit past the newest finished bar."""
    return (max(max(float(x["high"]) for x in bars), spot), min(min(float(x["low"]) for x in bars), spot))


def move_bar(bars: list[dict]) -> float | None:
    """Two typical minutes: twice the median 1-minute range of the last 30 bars."""
    recent = bars[-30:]
    if len(recent) < 10:
        return None
    return MOVE_BAR_MINUTES * statistics.median(float(x["high"]) - float(x["low"]) for x in recent)


def walls(row: dict, spot: float, sigma: float) -> list[tuple[str, float, str]]:
    """``(name, signed distance in sigma, row key)`` of each wall the row names."""
    return [(name, (float(row[key]) - spot) / sigma, key)
            for name, key in (("call wall", "call_wall"), ("put wall", "put_wall")) if is_num(row.get(key))]


def wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def settled_open(bars: list[dict]) -> float | None:
    """The day's settled open: the close of its 09:34 bar. None until that bar has finished (``bars``
    holds finished bars only) or when the day has no 09:34 bar."""
    return next((float(b["close"]) for b in bars if bar_time(b).time() == SETTLED_OPEN_BAR), None)


@dataclass(frozen=True)
class SessionExtremes:
    """The session's high and low so far and when each was first made (the finish of the bar that made it)."""
    high: float
    high_at: datetime
    low: float
    low_at: datetime


def session_extremes(bars: list[dict], since: datetime | None = None) -> SessionExtremes | None:
    """The high and low of the bars that started at or after ``since`` (default all of them), with when
    each was first made; None with no bars."""
    win = [b for b in bars if since is None or bar_time(b) >= since]
    if not win:
        return None
    hi = max(win, key=lambda b: float(b["high"]))      # max and min keep the first of equal extremes
    lo = min(win, key=lambda b: float(b["low"]))
    return SessionExtremes(float(hi["high"]), bar_time(hi) + ONE_MINUTE, float(lo["low"]), bar_time(lo) + ONE_MINUTE)
