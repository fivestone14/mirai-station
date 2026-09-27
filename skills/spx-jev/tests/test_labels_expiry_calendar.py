"""The expiry and calendar family's labels of the final set (calendar.*): each window and its edges, on the real
session calendar and the shipped event calendar, and the omissions."""
from __future__ import annotations

import json

from conftest import at, flat_bars
from spx_jev import events
from spx_jev.labels import expiry_calendar
from spx_jev.labels.expiry_calendar import build_expiry_calendar_labels

UNCOVERED = events.uncovered


def calendar_labels(scene_factory, day: str, hh: int = 12, mm: int = 32):
    now = at(hh, mm, day, ss=10)
    ls = build_expiry_calendar_labels(scene_factory(now, flat_bars(int((now - at(9, 30, day)).total_seconds() // 60), day=day)))
    return ls.state.get("calendar", {}), ls.omitted


# ---- calendar.expiry_phase (2026: the 18 September quarterly, the 30 September quarter end, the 16 October monthly)

def test_expiry_phase_on_the_quarter_end(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-09-30")
    assert state["expiry_phase"] == ("today, Wednesday 30 September, is the quarter's last trading day: the quarter-end SPXW options settle at "
                                     "today's 16:00 close; the 16 October monthly is 12 trading days away, outside the 4-day opex-week window; "
                                     "the 18 September monthly was 8 trading days ago, outside the 5-day after-opex window")


def test_expiry_phase_on_a_monthly_and_a_quarterly_expiry(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-16")
    assert state["expiry_phase"] == ("today, Friday 16 October, is the monthly expiry: the morning-settled SPX monthly options settled on this "
                                     "morning's opening prints and the daily SPXW options settle at today's 16:00 close")
    state, _ = calendar_labels(scene_factory, "2026-09-18")
    assert state["expiry_phase"].startswith("today, Friday 18 September, is the quarterly expiry: the morning-settled SPX monthly options")


def test_expiry_phase_at_the_edges_of_the_opex_week_window(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-12")
    assert state["expiry_phase"] == ("today, Monday 12 October, has only the daily SPXW expiry; the 16 October monthly is 4 trading days away, "
                                     "inside the 4-day opex-week window; the 18 September monthly was 16 trading days ago, outside the 5-day "
                                     "after-opex window")
    state, _ = calendar_labels(scene_factory, "2026-10-09")
    assert "the 16 October monthly is 5 trading days away, outside the 4-day opex-week window" in state["expiry_phase"]
    state, _ = calendar_labels(scene_factory, "2026-10-15")
    assert "the 16 October monthly is 1 trading day away, inside the 4-day opex-week window" in state["expiry_phase"]


def test_expiry_phase_at_the_edges_of_the_after_opex_window(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-19")
    assert state["expiry_phase"] == ("today, Monday 19 October, has only the daily SPXW expiry; the 20 November monthly is 24 trading days away, "
                                     "outside the 4-day opex-week window; the 16 October monthly was 1 trading day ago, inside the 5-day "
                                     "after-opex window")
    state, _ = calendar_labels(scene_factory, "2026-10-23")
    assert state["expiry_phase"].endswith("the 16 October monthly was 5 trading days ago, inside the 5-day after-opex window")
    state, _ = calendar_labels(scene_factory, "2026-10-26")
    assert state["expiry_phase"] == ("today, Monday 26 October, has only the daily SPXW expiry; the 20 November monthly is 19 trading days away, "
                                     "outside the 4-day opex-week window; the 16 October monthly was 6 trading days ago, outside the 5-day "
                                     "after-opex window; an ordinary day in the expiry cycle")


def test_expiry_phase_counts_trading_days_across_a_holiday(scene_factory):
    # Labor Day, Monday 7 September, is not a session
    state, _ = calendar_labels(scene_factory, "2026-09-11")
    assert "the 18 September monthly is 5 trading days away, outside the 4-day opex-week window" in state["expiry_phase"]
    assert "the 21 August monthly was 14 trading days ago" in state["expiry_phase"]


# ---- calendar.month_turn

def test_month_turn_on_the_months_last_two_trading_days(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-09-30")
    assert state["month_turn"] == ("today, Wednesday 30 September, is the last trading day of the month and of the quarter, inside the month's "
                                   "last 2 trading days; the next session is the first trading day of October")
    state, _ = calendar_labels(scene_factory, "2026-09-29")
    assert state["month_turn"] == "today, Tuesday 29 September, is the second-to-last trading day of the month, inside the month's last 2 trading days"
    state, _ = calendar_labels(scene_factory, "2026-10-30")
    assert state["month_turn"].startswith("today, Friday 30 October, is the last trading day of the month, inside")


def test_month_turn_on_the_months_first_trading_days(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-01")
    assert state["month_turn"] == "today, Thursday 1 October, is the 1st trading day of October, inside the month's first 3 trading days"
    state, _ = calendar_labels(scene_factory, "2026-10-05")
    assert state["month_turn"] == "today, Monday 5 October, is the 3rd trading day of October, inside the month's first 3 trading days"
    state, _ = calendar_labels(scene_factory, "2026-09-10")
    assert state["month_turn"].startswith("today, Thursday 10 September, is the 7th trading day of September")


def test_month_turn_outside_both_windows(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-06")
    assert state["month_turn"] == ("today, Tuesday 6 October, is the 4th trading day of October with 18 trading days left after it, outside the "
                                   "month's first 3 and last 2 trading days")
    state, _ = calendar_labels(scene_factory, "2026-09-28")
    assert state["month_turn"].endswith("with 2 trading days left after it, outside the month's first 3 and last 2 trading days")


# ---- calendar.event_cycle (the shipped calendar: jobs 2 October, CPI 14 October, the Fed 28 October, all 2026)

def test_event_cycle_on_the_eve_of_the_fed_and_of_a_report_before_the_open(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-27")
    assert state["event_cycle"] == "the next session, Wednesday 28 October, brings the Fed's rate decision: today is the eve of the Fed"
    state, _ = calendar_labels(scene_factory, "2026-10-01")
    assert state["event_cycle"] == ("the jobs report comes out at 08:30 before the next session's open, Friday 2 October: today is the eve of a "
                                    "major release")
    state, _ = calendar_labels(scene_factory, "2026-10-13")
    assert state["event_cycle"].startswith("the consumer price report comes out at 08:30 before the next session's open, Wednesday 14 October")


def test_event_cycle_on_the_day_after_and_on_an_ordinary_day(scene_factory):
    state, _ = calendar_labels(scene_factory, "2026-10-05")
    assert state["event_cycle"] == "the previous session, Friday 2 October, brought the jobs report: today is the day after a major release"
    state, _ = calendar_labels(scene_factory, "2026-10-29")
    assert state["event_cycle"] == "the previous session, Wednesday 28 October, brought the Fed's rate decision: today is the day after a major release"
    state, _ = calendar_labels(scene_factory, "2026-10-07")
    assert state["event_cycle"] == ("no jobs report, consumer price report or Fed decision fell on the previous session, Tuesday 6 October, or "
                                    "comes before or during the next, Thursday 8 October: an ordinary day")


def test_event_cycle_is_omitted_past_the_calendars_last_kept_day_and_before_its_first(scene_factory, monkeypatch, tmp_path):
    path = tmp_path / "events.json"
    path.write_text(json.dumps({"covers_from": "2026-09-01", "covers_through": "2026-10-01", "events": []}))
    events._load.cache_clear()
    monkeypatch.setattr(expiry_calendar, "uncovered", lambda d: UNCOVERED(d, path))
    state, omitted = calendar_labels(scene_factory, "2026-10-01")
    assert "event_cycle" not in state
    assert omitted["calendar.event_cycle"] == ("the event calendar (calendar/events.json) is kept only through 2026-10-01: extend it, "
                                               "so the releases either side of today are unknown")
    assert calendar_labels(scene_factory, "2026-09-01")[1]["calendar.event_cycle"] == \
        "the event calendar (calendar/events.json) is kept only from 2026-09-01, so the releases either side of today are unknown"


# ---- no session

def test_every_calendar_label_is_omitted_on_a_day_with_no_session(scene_factory):
    state, omitted = calendar_labels(scene_factory, "2026-09-07")
    assert state == {}
    assert {p: omitted[p] for p in ("calendar.event_cycle", "calendar.expiry_phase", "calendar.month_turn")} == \
        dict.fromkeys(("calendar.event_cycle", "calendar.expiry_phase", "calendar.month_turn"), "2026-09-07 is not a trading day")
