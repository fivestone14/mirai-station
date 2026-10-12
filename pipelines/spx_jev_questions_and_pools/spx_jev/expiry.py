"""SPX's expiries, point in time: today's 0DTE, the next one, and the monthly and quarterly expiries.

SPX lists an expiry on every trading day, not once a week, so nothing here is SNDK's weekly logic:

    today's        every trading day the PM-settled SPXW options expire, settling at the close
                   (16:00 ET, 13:00 on a half day); after the settle there is no expiry left today
    the next       the next trading day's SPXW (1DTE)
    the monthly    the third Friday, or the Thursday before when that Friday is a market holiday. Two
                   listings expire that day: the AM-settled SPX monthly, settled on the opening prints,
                   and the PM-settled SPXW, settled at the close
    the quarterly  the March, June, September and December monthly (quarterly expiry, the day the index
                   rebalances trade), and apart from it the quarter-end SPXW, PM-settled on the
                   quarter's last trading day

Everything is computed from the date and the station's session calendar (sessions.py); the options
book on the diary row says which of these books a label describes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .sessions import is_trading_day, next_trading_day, session_close

ET = ZoneInfo("America/New_York")
QUARTER_MONTHS = (3, 6, 9, 12)


def settle_at(d: date) -> datetime:
    """When a PM-settled SPXW expiry on ``d`` settles: that session's close."""
    return session_close(datetime(d.year, d.month, d.day, 12, 0, tzinfo=ET))


def monthly_expiry(year: int, month: int) -> date:
    """The month's standard expiry: the third Friday, or the trading day before it when it is a holiday."""
    first = date(year, month, 1)
    third_friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    d = third_friday
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def quarter_end_expiry(year: int, month: int) -> date:
    """The quarter-end SPXW expiry for the quarter ending in ``month``: its last trading day."""
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def expiry_kinds(d: date) -> list[str]:
    """What expires on ``d`` beyond the daily SPXW: "monthly", "quarterly" (a quarter's monthly) and
    "quarter_end". Empty on an ordinary daily expiry or a day with no session."""
    if not is_trading_day(d):
        return []
    kinds = []
    if d == monthly_expiry(d.year, d.month):
        kinds += ["monthly", "quarterly"] if d.month in QUARTER_MONTHS else ["monthly"]
    if d.month in QUARTER_MONTHS and d == quarter_end_expiry(d.year, d.month):
        kinds.append("quarter_end")
    return kinds


def todays_settle(now: datetime) -> datetime | None:
    """Today's 0DTE settle, or None once it has passed or when there is no session today."""
    now_et = now.astimezone(ET)
    if not is_trading_day(now_et.date()):
        return None
    settle = settle_at(now_et.date())
    return settle if now_et < settle else None


def next_expiry(now: datetime) -> date:
    """The expiry after today's: the next trading day (1DTE)."""
    return next_trading_day(now.astimezone(ET).date())


def next_monthly(now: datetime) -> date:
    """The nearest monthly expiry still ahead: this month's while it has not settled, else next month's."""
    d = now.astimezone(ET).date()
    m = monthly_expiry(d.year, d.month)
    if m > d or (m == d and todays_settle(now) is not None):
        return m
    return monthly_expiry(d.year + (d.month == 12), d.month % 12 + 1)


def trading_days_between(start: date, end: date) -> int:
    """Trading days after ``start`` up to and including ``end``."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += is_trading_day(d)
    return n


def calendar_of(now: datetime) -> dict:
    """The expiries as the card carries them, every time a full timestamp: today's settle (None once
    passed), the next expiry's settle, the next monthly's settle and what expires today."""
    today = todays_settle(now)
    return {"today_settles_at": today.isoformat() if today else None,
            "next_settles_at": settle_at(next_expiry(now)).isoformat(),
            "next_monthly_settles_at": settle_at(next_monthly(now)).isoformat(),
            "expiring_today": expiry_kinds(now.astimezone(ET).date())}
