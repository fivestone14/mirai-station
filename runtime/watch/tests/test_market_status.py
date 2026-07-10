"""Unit tests for the holiday-aware market-hours gate.

Pure-logic, no network:

    cd runtime && python3 -m unittest watch.tests.test_market_status -v

Pins the 2026 NYSE calendar (floating holidays, Good Friday, weekend
observance, half-day early closes) and the RTH window edges.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, time

from watch.intraday import market_status as ms

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None


def _at(y, mo, d, hh, mm):
    return datetime(y, mo, d, hh, mm, tzinfo=ET)


class TestHolidayCalendar2026(unittest.TestCase):
    def test_full_closures(self):
        h = ms._market_holidays(2026)
        expected = {
            date(2026, 1, 1),    # New Year's (Thu)
            date(2026, 1, 19),   # MLK (3rd Mon Jan)
            date(2026, 2, 16),   # Washington's Birthday (3rd Mon Feb)
            date(2026, 4, 3),    # Good Friday (Easter Apr 5)
            date(2026, 5, 25),   # Memorial (last Mon May)
            date(2026, 6, 19),   # Juneteenth (Fri)
            date(2026, 7, 3),    # Independence observed (Jul 4 is Sat -> Fri)
            date(2026, 9, 7),    # Labor (1st Mon Sep)
            date(2026, 11, 26),  # Thanksgiving (4th Thu Nov)
            date(2026, 12, 25),  # Christmas (Fri)
        }
        self.assertEqual(h, expected)

    def test_half_days(self):
        hd = ms._half_days(2026)
        self.assertIn(date(2026, 11, 27), hd)   # day after Thanksgiving
        self.assertIn(date(2026, 12, 24), hd)   # Christmas Eve (25th is Fri)
        self.assertNotIn(date(2026, 7, 3), hd)  # Jul 3 is a FULL holiday this year, not half


class TestObservanceRules(unittest.TestCase):
    def test_july4_saturday_observed_friday(self):
        # 2026: Jul 4 is Saturday -> full closure moves to Fri Jul 3.
        self.assertIn(date(2026, 7, 3), ms._market_holidays(2026))

    def test_newyear_saturday_no_friday_closure(self):
        # 2022: Jan 1 is Saturday -> NYSE stays open, no Dec-31/Jan-1 closure.
        self.assertNotIn(date(2022, 1, 1), ms._market_holidays(2022))
        self.assertNotIn(date(2021, 12, 31), ms._market_holidays(2022))

    def test_newyear_sunday_observed_monday(self):
        # 2023: Jan 1 is Sunday -> observed Mon Jan 2.
        self.assertIn(date(2023, 1, 2), ms._market_holidays(2023))

    def test_christmas_sunday_observed_monday(self):
        # 2022: Dec 25 is Sunday -> observed Mon Dec 26.
        self.assertIn(date(2022, 12, 26), ms._market_holidays(2022))


class TestGate(unittest.TestCase):
    def test_thanksgiving_is_closed(self):
        self.assertFalse(ms.check(_at(2026, 11, 26, 11, 0)).is_live)

    def test_good_friday_is_closed(self):
        self.assertFalse(ms.check(_at(2026, 4, 3, 11, 0)).is_live)

    def test_regular_weekday_open(self):
        st = ms.check(_at(2026, 7, 10, 11, 0))  # a normal Friday
        self.assertTrue(st.is_live)
        self.assertEqual(st.reason, "open")

    def test_weekend_closed(self):
        self.assertFalse(ms.check(_at(2026, 7, 11, 11, 0)).is_live)  # Saturday

    def test_before_open_and_after_close(self):
        self.assertFalse(ms.check(_at(2026, 7, 10, 9, 0)).is_live)
        self.assertFalse(ms.check(_at(2026, 7, 10, 16, 0)).is_live)
        self.assertTrue(ms.check(_at(2026, 7, 10, 9, 30)).is_live)

    def test_half_day_early_close(self):
        # Christmas Eve 2026 (half day): open at 11:00, shut by 13:30.
        self.assertTrue(ms.check(_at(2026, 12, 24, 11, 0)).is_live)
        self.assertEqual(ms.check(_at(2026, 12, 24, 11, 0)).reason, "open (half-day session)")
        self.assertFalse(ms.check(_at(2026, 12, 24, 13, 30)).is_live)


if __name__ == "__main__":
    unittest.main()
