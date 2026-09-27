"""SPX's daily expiries and the session calendar, point in time; and the labels that name them."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from conftest import at, flat_bars
from spx_jev.cuts import ZERO_DTE_LAST_HOUR_MIN
from spx_jev.expiry import (calendar_of, expiry_kinds, monthly_expiry, next_expiry, next_monthly, quarter_end_expiry, settle_at,
                            todays_settle, trading_days_between)
from spx_jev.sessions import is_trading_day, next_trading_day, session_close
from spx_jev.labels.registry import build_labels


def test_the_session_closes_at_16_or_13_on_a_half_day_and_skips_holidays():
    assert session_close(at(12, 0)).hour == 16 and session_close(at(12, 0, day="2026-11-27")).hour == 13
    assert not is_trading_day(date(2026, 11, 26)) and not is_trading_day(date(2026, 9, 19))     # Thanksgiving; a Saturday
    assert next_trading_day(date(2026, 9, 18)) == date(2026, 9, 21)


def test_todays_expiry_lasts_until_the_settle_and_the_next_is_the_next_trading_day():
    assert todays_settle(at(15, 59)) == at(16, 0) and todays_settle(at(16, 0)) is None
    assert todays_settle(at(12, 0, day="2026-11-27")).hour == 13                                  # a half day settles at 13:00
    assert next_expiry(at(12, 0)) == date(2026, 9, 21)                                           # Friday's next is Monday
    assert settle_at(date(2026, 9, 21)).isoformat() == "2026-09-21T16:00:00-04:00"


def test_the_monthly_is_the_third_friday_and_the_quarterly_is_a_quarters_monthly():
    assert monthly_expiry(2026, 9) == date(2026, 9, 18) and monthly_expiry(2026, 10) == date(2026, 10, 16)
    assert monthly_expiry(2027, 3) == date(2027, 3, 19)
    assert quarter_end_expiry(2026, 9) == date(2026, 9, 30) and quarter_end_expiry(2026, 12) == date(2026, 12, 31)
    assert expiry_kinds(date(2026, 9, 18)) == ["monthly", "quarterly"] and expiry_kinds(date(2026, 10, 16)) == ["monthly"]
    assert expiry_kinds(date(2026, 9, 30)) == ["quarter_end"] and expiry_kinds(date(2026, 9, 22)) == []
    assert expiry_kinds(date(2026, 9, 19)) == []                                                  # no session, no expiry


def test_the_next_monthly_rolls_after_its_settle():
    assert next_monthly(at(15, 0)) == date(2026, 9, 18) and next_monthly(at(16, 5)) == date(2026, 10, 16)
    assert trading_days_between(date(2026, 9, 25), date(2026, 10, 16)) == 15


def test_the_card_calendar_is_all_full_timestamps():
    c = calendar_of(at(12, 0))
    assert c == {"today_settles_at": "2026-09-18T16:00:00-04:00", "next_settles_at": "2026-09-21T16:00:00-04:00",
                 "next_monthly_settles_at": "2026-09-18T16:00:00-04:00", "expiring_today": ["monthly", "quarterly"]}


def test_the_expiry_labels_name_the_book_and_the_clock(full_scene, scene_factory):
    state = build_labels(full_scene).state
    e = state["expiry"]
    assert e["settle_clock"] == f"today's 0DTE SPXW options settle at the 16:00 ET close, 210 minutes from now, before their last {ZERO_DTE_LAST_HOUR_MIN} minutes, when their gamma decays fastest"
    assert e["opex_today"].startswith("today is the quarterly expiry: the AM-settled SPX monthly options settled")
    assert e["dated_weight"] == "of the dated books pulled this morning (1 monthly, 1 quarter-end), the monthly expiry 28 days out holds the most options weight, 75% of their gamma"
    assert e["today_vs_week"].endswith("both read long gamma, where dealers' hedging damps moves, so they agree")
    late = build_labels(scene_factory(at(15, 20, day="2026-09-22"), flat_bars(350, day="2026-09-22"))).state
    assert f"40 minutes from now, inside their last {ZERO_DTE_LAST_HOUR_MIN} minutes" in late["expiry"]["settle_clock"]
    assert late["expiry"]["opex_today"] == "today is an ordinary daily expiry; the next monthly expiry is 18 trading days away"
    assert "gex" in state and all("0DTE" in state["gex"][k] for k in ("weight_side", "wall_thickness", "heaviest_strike_grip", "ladder_state"))


def test_a_stale_dated_pull_or_an_unread_0dte_book_is_omitted(full_scene):
    row = {**full_scene.row, "dated_gex": {**full_scene.row["dated_gex"], "staleness": "stale"},
           "gex_views": {**full_scene.row["gex_views"], "regime_source": "blended_fallback"}}
    omitted = build_labels(replace(full_scene, row=row)).omitted
    assert omitted["expiry.dated_weight"] == "the dated books are stale, not this morning's"
    assert omitted["expiry.today_vs_week"].startswith("today's 0DTE book could not be read")
