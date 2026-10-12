"""
basis.py — the SPY↔SPX RULER. The host measures walls in SPX points but the
volume evidence lives on SPY, so every window carries one multiplicative
ratio (SPX = SPY × ratio) derived from TIME-MATCHED closes.

Frozen-per-observation rule: history is NEVER re-derived. Each touch window
freezes `ratio_used` at open, and every later read of that window (grading,
cards) uses the frozen number — a drifting basis must not regrade yesterday's
touch. When the current scan has no time-matched SPY bar, the engine carries
the last frozen ratio forward instead of inventing one (and the scan is
already DEGRADED by the feed guard in that case).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def minute_of_day(ts) -> Optional[int]:
    """ET minutes-from-midnight for a datetime or ISO string (None if unreadable)."""
    try:
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is not None:
            ts = ts.astimezone(ET)
        return ts.hour * 60 + ts.minute
    except (ValueError, TypeError, AttributeError):
        return None


def bars_by_minute(bars: list[dict]) -> dict[int, dict]:
    """{ET minute: bar} — bars with unreadable timestamps are dropped, not guessed."""
    out = {}
    for b in bars or []:
        m = minute_of_day(b.get("ts"))
        if m is not None:
            out[m] = b
    return out


def derive_ratio(spot: Optional[float], bars: list[dict], now: datetime,
                 tol_min: int) -> Optional[float]:
    """SPX/SPY ratio from the newest SPY close time-matched to the scan clock
    (within tol_min). None when there is no matched bar — the caller carries
    the last frozen ratio instead."""
    if not spot or not bars:
        return None
    b = bars[-1]
    try:
        age = (now - datetime.fromisoformat(str(b["ts"]))).total_seconds()
    except (KeyError, ValueError, TypeError):
        return None
    if age < 0 or age > tol_min * 60:
        return None
    close = b.get("close")
    if not close:
        return None
    return spot / close


def close_at(by_min: dict[int, dict], minute: int,
             tol_min: int) -> tuple[Optional[float], Optional[int]]:
    """The close at `minute`, or the nearest earlier close within tol_min —
    (close, minute_found) or (None, None). Never reaches FORWARD in time."""
    for m in range(minute, minute - tol_min - 1, -1):
        b = by_min.get(m)
        if b and b.get("close"):
            return b["close"], m
    return None, None
