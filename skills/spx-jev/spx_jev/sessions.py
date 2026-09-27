"""The NYSE session calendar: the open, the close (13:00 on a half day), holidays and trading days.

Taken from the station's own market-hours gate (``runtime/watch/intraday/market_status``), the one
computed calendar every station job uses, so the labeller, the grader, the clock and the expiry
calendar can never disagree about when a session ends. If the gate cannot be imported the calendar
fails open to a plain Monday-to-Friday week with a 16:00 close, rather than stopping a read.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)       # NYSE half days (the day after Thanksgiving, some July 3rds and Christmas Eves)

_RUNTIME = Path(__file__).resolve().parents[3] / "runtime"


def _market_status():
    try:
        if str(_RUNTIME) not in sys.path:
            sys.path.insert(0, str(_RUNTIME))
        from watch.intraday import market_status
        return market_status
    except Exception:
        return None


def half_days(year: int) -> frozenset:
    ms = _market_status()
    return ms._half_days(year) if ms else frozenset()


def market_holidays(year: int) -> frozenset:
    ms = _market_status()
    return ms._market_holidays(year) if ms else frozenset()


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in market_holidays(d.year)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def session_open(now: datetime) -> datetime:
    return now.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)


def session_close(now: datetime) -> datetime:
    """16:00 ET, or 13:00 on a half day. The grader reads the same close, so a horizon past it is
    closed out instead of waiting forever for bars that will never come."""
    c = EARLY_CLOSE if now.date() in half_days(now.year) else SESSION_CLOSE
    return now.replace(hour=c.hour, minute=c.minute, second=0, microsecond=0)


def session_minutes(now: datetime) -> float:
    return (session_close(now) - session_open(now)).total_seconds() / 60.0
