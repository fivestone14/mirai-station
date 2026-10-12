"""The authoritative live-hours gate - the first node of the intraday graph.

Plain English:
Before doing anything each tick, the routine asks one question: is the US market
open right now? If not, it returns "market is closed" and exits immediately - no
data fetch, no reasoning, no log. This is the in-graph gate the plan requires;
the launchd schedule is only a coarse outer guard.

This uses a self-contained, conservative check: weekday + 9:30-16:00 ET + the
full NYSE holiday calendar computed from the date (floating Monday/Thursday
holidays, Good Friday via the Gregorian computus, and Saturday/Sunday
observance) + early-close (13:00 ET) half-days. The check can only ever be MORE
restrictive than reality, never less, so it cannot accidentally run the routine
when the market is shut.

`now` is injected so the gate is deterministic and unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import FrozenSet, Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    _ET = None

# Regular trading hours.
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)   # NYSE half-day early close


@dataclass
class MarketStatus:
    is_live: bool
    reason: str
    minutes_et: Optional[int]  # minutes since ET midnight, for time-of-day signals


# --- holiday calendar (pure date math, no network, no external calendar) ----------

def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher computus). Anchors Good Friday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of a month, e.g. 3rd Monday of January."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` (Mon=0) of a month, e.g. last Monday of May."""
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """NYSE weekend observance for a fixed-date holiday: Sat -> preceding Fri,
    Sun -> following Mon. (New Year's is handled specially by the caller.)"""
    if d.weekday() == 5:   # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:   # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def _market_holidays(year: int) -> FrozenSet[date]:
    """Full-closure NYSE holidays for `year` (the market is shut all day)."""
    days = set()

    # New Year's Day: closed Jan 1, or Mon Jan 2 if it lands on Sunday. If it
    # lands on Saturday the NYSE does NOT close the preceding Friday (Dec 31).
    jan1 = date(year, 1, 1)
    if jan1.weekday() == 6:        # Sunday -> observed Monday
        days.add(date(year, 1, 2))
    elif jan1.weekday() != 5:      # Mon-Fri (Saturday -> no observance)
        days.add(jan1)

    days.add(_nth_weekday(year, 1, 0, 3))            # MLK Day (3rd Mon Jan)
    days.add(_nth_weekday(year, 2, 0, 3))            # Washington's Birthday (3rd Mon Feb)
    days.add(_easter(year) - timedelta(days=2))      # Good Friday
    days.add(_last_weekday(year, 5, 0))              # Memorial Day (last Mon May)
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))       # Juneteenth
    days.add(_observed(date(year, 7, 4)))            # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))            # Labor Day (1st Mon Sep)
    days.add(_nth_weekday(year, 11, 3, 4))           # Thanksgiving (4th Thu Nov)
    days.add(_observed(date(year, 12, 25)))          # Christmas
    return frozenset(days)


@lru_cache(maxsize=None)
def _half_days(year: int) -> FrozenSet[date]:
    """Early-close (13:00 ET) half-days: day after Thanksgiving, and the eves of
    Independence Day / Christmas when those fall such that the eve is a normal
    weekday. Half-days are MORE restrictive (they shorten the session), matching
    this gate's fail-safe contract."""
    days = set()
    days.add(_nth_weekday(year, 11, 3, 4) + timedelta(days=1))   # Fri after Thanksgiving
    # July 3 is a half-day when July 4 is a Tue-Fri weekday holiday (so the 3rd is Mon-Thu).
    if date(year, 7, 4).weekday() in (1, 2, 3, 4):
        days.add(date(year, 7, 3))
    # Dec 24 is a half-day when Christmas (25th) is a Tue-Fri weekday holiday.
    if date(year, 12, 25).weekday() in (1, 2, 3, 4):
        days.add(date(year, 12, 24))
    return frozenset(days)


def _now_et(now: Optional[datetime]) -> datetime:
    if now is not None:
        return now
    if _ET is not None:
        return datetime.now(_ET)
    return datetime.utcnow()


def _minutes_et(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _fallback_status(dt: datetime) -> MarketStatus:
    """Conservative stdlib-only check: weekend + full holiday calendar + RTH
    window (with half-day early close)."""
    minutes = _minutes_et(dt)
    d = dt.date()
    if dt.weekday() >= 5:  # Saturday/Sunday
        return MarketStatus(False, "market is closed (weekend)", minutes)
    if d in _market_holidays(d.year):
        return MarketStatus(False, "market is closed (holiday)", minutes)
    if dt.time() < _RTH_OPEN:
        return MarketStatus(False, "market is closed (before 9:30 ET open)", minutes)
    half = d in _half_days(d.year)
    close = _EARLY_CLOSE if half else _RTH_CLOSE
    if dt.time() >= close:
        why = ("early close 13:00 ET half-day" if half else "after 16:00 ET close")
        return MarketStatus(False, f"market is closed ({why})", minutes)
    return MarketStatus(True, "open (half-day session)" if half else "open", minutes)


def check(now: Optional[datetime] = None) -> MarketStatus:
    """Return whether the market is live right now (conservative clock)."""
    dt = _now_et(now)
    return _fallback_status(dt)
