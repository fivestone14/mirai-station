"""Weekly-expiry enumerator + probe schedule (pure calendar math)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import sndk_feed

ET = ZoneInfo("America/New_York")


def _dt(y, m, d):
    return datetime(y, m, d, 10, 0, tzinfo=ET)


def test_plain_fridays():
    # Mon 2026-07-27 → the next three ordinary weekly Fridays
    assert sndk_feed.weekly_expiries(_dt(2026, 7, 27), n=3) == [
        date(2026, 7, 31), date(2026, 8, 7), date(2026, 8, 14)]


def test_expiry_day_still_included():
    # ON the Friday itself the week's expiry is today (dte 0), not skipped
    assert sndk_feed.weekly_expiries(_dt(2026, 7, 31), n=2) == [
        date(2026, 7, 31), date(2026, 8, 7)]


def test_holiday_friday_shifts_to_thursday():
    # Christmas 2026-12-25 is a Friday NYSE closure → the weekly shifts back
    # to Thursday 12-24 (the holiday-Friday → Thursday rule)
    hol = sndk_feed._holidays(2026)
    assert date(2026, 12, 25) in hol, "calendar sanity: Christmas must be a closure"
    exps = sndk_feed.weekly_expiries(_dt(2026, 12, 21), n=2)
    assert exps[0] == date(2026, 12, 24)
    assert exps[0].weekday() == 3          # Thursday
    assert exps[1] == date(2027, 1, 1) or exps[1].weekday() < 5


def test_july_4_observed_friday_shifts():
    # 2026-07-04 falls on a Saturday → observed Friday 2026-07-03 closure
    hol = sndk_feed._holidays(2026)
    assert date(2026, 7, 3) in hol
    exps = sndk_feed.weekly_expiries(_dt(2026, 6, 29), n=1)
    assert exps[0] == date(2026, 7, 2)     # Thursday of the holiday week


def test_probe_days_skip_weekends_and_cover_two_weeks():
    days = sndk_feed.probe_days(date(2026, 7, 27))
    assert all(d.weekday() < 5 for d in days)
    assert days[0] == date(2026, 7, 27)
    assert days[-1] >= date(2026, 8, 7)    # the 2nd weekly is always in reach
    # every calendar-computable weekly in the window is a probed day
    for e in sndk_feed.weekly_expiries(_dt(2026, 7, 27), n=2):
        assert e in days
