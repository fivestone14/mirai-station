"""The clock, calendar and scale blocks: every value is computed from the frozen inputs, every missing fact is
declared with its reason, and no price level or date reaches the block."""
from __future__ import annotations

import json
import re

import pytest
from payload_fixtures import FakeMarket, at, fake_inputs, flat_bars, make_row, station_state_dir

from spx_claude_forecast.paths import ensure_folders
from spx_claude_forecast.payload.blocks.calendar import build_calendar_block
from spx_claude_forecast.payload.blocks.clock import build_clock_block
from spx_claude_forecast.payload.blocks.scale import build_scale_block
from spx_claude_forecast.payload.frozen_inputs import load_frozen_inputs

READ_ID = "live:2026-10-09T14:30:12.458122-04:00"
PRICE_LEVEL = re.compile(r"(?<![\d.])[1-9]\d{3}(?![\d.])")     # a whole number of 1000 or more, as a price or strike would read
DATE = re.compile(r"20\d\d-\d\d-\d\d")

EVENTS = {
    "covers_from": "2026-09-01", "covers_through": "2026-12-31",
    "events": [
        {"date": "2026-10-08", "time_et": "08:30", "kind": "JOBLESS_CLAIMS", "tier": "pre_open"},
        {"date": "2026-10-09", "time_et": "10:00", "kind": "UMICH_SENTIMENT", "tier": "data_10am"},
        {"date": "2026-10-14", "time_et": "08:30", "kind": "CPI", "tier": "pre_open"},
        {"date": "2026-10-28", "time_et": "14:00", "kind": "FOMC", "tier": 1},
        {"date": "2026-10-28", "time_et": "14:30", "end_et": "15:30", "kind": "FOMC_PRESSER", "tier": 1},
        {"date": "2026-11-05", "time_et": "12:00", "kind": "FED_GOVERNOR_SPEECH", "tier": "fed_speaker"},
    ],
}
NATIVE_RULER = {"em_open": 21.21, "em_points": 5.72, "em_consumed": 1.3187}


def absent_reasons(result) -> dict[str, str]:
    return {a["path"]: a["why"] for a in result.absent}


def native_row(cut, **over) -> dict:
    fields = {"gex_source": "native", "range_ruler": dict(NATIVE_RULER), "sigma_live": 41.5394, "atm_iv": 0.0844, **over}
    return make_row(cut, **fields)


def month_closes(months: list[tuple[str, float]], this_month: tuple[str, float]) -> dict[str, list[dict]]:
    """$SPX and TLT daily closes where each month's second close moves SPX by its percentage and TLT not at all,
    and every month's last close returns to 100 (SPX) and 50 (TLT), so each month's gap is its own number."""
    spx, tlt = [], []
    for month, second_pct in [*months, this_month]:
        spx += [{"day": f"{month}-01", "close": 100.0}, {"day": f"{month}-02", "close": 100.0 * (1 + second_pct / 100)},
                {"day": f"{month}-28", "close": 100.0}]
        tlt += [{"day": f"{month}-01", "close": 50.0}, {"day": f"{month}-02", "close": 50.0}, {"day": f"{month}-28", "close": 50.0}]
    this = this_month[0]
    return {"$SPX": [r for r in spx if r["day"] <= f"{this}-02"], "TLT": [r for r in tlt if r["day"] <= f"{this}-02"]}


# --- clock -----------------------------------------------------------------------------------------------------

def test_clock_counts_minutes_and_names_the_station_phase(tmp_path):
    result = build_clock_block(fake_inputs(tmp_path, cut=at(14, 30)))
    assert result.data == {"min_after_open": 300, "min_to_close": 90, "phase": "afternoon"} and not result.absent


@pytest.mark.parametrize("hh, mm, phase", [(9, 45, "opening"), (10, 0, "morning"), (11, 59, "late_morning"), (13, 0, "lunch")])
def test_clock_phase_follows_the_station_cuts(tmp_path, hh, mm, phase):
    assert build_clock_block(fake_inputs(tmp_path, cut=at(hh, mm))).data["phase"] == phase


# --- calendar --------------------------------------------------------------------------------------------------

def test_calendar_reads_the_day_and_the_cycles_without_a_date(tmp_path):
    result = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, 12), events=EVENTS))
    assert result.data["day_class"] == "medium-tier day"
    assert result.data["today"] == [{"kind": "UMICH_SENTIMENT", "at": "10:00", "tier": "medium", "min_ago": 270}]
    assert result.data["next_high_tier"] == {"kind": "CPI", "sessions_ahead": 3}
    assert result.data["fomc"] == {"phase": "none", "sessions_to_next": 13}
    assert result.data["month"] == {"trading_day": 7, "days_left": 15, "turn_window": "none"}
    assert result.data["expiry"] == {"today": "daily", "monthly_sessions_ahead": 5}
    assert absent_reasons(result) == {"calendar.month.pension_stock_minus_bond_mtd_pts": "no_daily_closes"}
    assert not DATE.search(json.dumps(result.data))


def test_calendar_shows_events_ahead_and_under_way_on_a_decision_day(tmp_path):
    before = build_calendar_block(fake_inputs(tmp_path, cut=at(13, 0, day="2026-10-28"), events=EVENTS)).data
    assert before["day_class"] == "high-tier day"
    assert before["today"][0] == {"kind": "FOMC", "at": "14:00", "tier": "high", "min_ahead": 60}
    assert before["next_high_tier"] == {"kind": "FOMC", "sessions_ahead": 0}
    assert before["fomc"] == {"phase": "decision_day", "sessions_to_next": 0}
    during = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 45, day="2026-10-28"), events=EVENTS)).data
    assert during["today"][1] == {"kind": "FOMC_PRESSER", "at": "14:30", "tier": "high", "min_ago": 15, "under_way": True}


def test_fomc_phase_around_the_decision(tmp_path):
    day_before = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-10-27"), events=EVENTS))
    assert day_before.data["fomc"] == {"phase": "day_before", "sessions_to_next": 1}
    day_after = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-10-29"), events=EVENTS))
    assert day_after.data["fomc"] == {"phase": "day_after"}
    assert absent_reasons(day_after)["calendar.fomc.sessions_to_next"] == "none_on_calendar_ahead"


def test_a_day_with_no_rows_is_quiet_only_when_the_calendar_covers_it(tmp_path):
    quiet = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-10-13"), events=EVENTS))
    assert quiet.data["day_class"] == "quiet day" and quiet.data["today"] == []
    uncovered = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-08-20"), events=EVENTS))
    reasons = absent_reasons(uncovered)
    assert {reasons[f"calendar.{f}"] for f in ("day_class", "today", "next_high_tier", "fomc")} == {"calendar_not_covered"}
    assert set(uncovered.data) == {"month", "expiry"}
    missing = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30), events=None))
    assert {reasons for reasons in absent_reasons(missing).values()} >= {"no_event_calendar"}


@pytest.mark.parametrize("day, window", [("2026-10-26", "none"), ("2026-10-27", "T-3"), ("2026-10-29", "T-1"), ("2026-10-30", "T"),
                                         ("2026-11-02", "T+1"), ("2026-11-04", "T+3"), ("2026-11-05", "none")])
def test_turn_window_brackets_the_months_last_session(tmp_path, day, window):
    result = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day=day), events=EVENTS))
    assert result.data["month"]["turn_window"] == window


@pytest.mark.parametrize("day, kind, ahead", [("2026-10-16", "monthly", 0), ("2026-12-18", "quarterly", 0), ("2026-12-31", "quarter_end", 10)])
def test_expiry_names_todays_kind_and_sessions_to_the_next_monthly(tmp_path, day, kind, ahead):
    result = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day=day), events=EVENTS))
    assert result.data["expiry"] == {"today": kind, "monthly_sessions_ahead": ahead}


def test_pension_gap_is_spx_minus_tlt_month_to_date_ranked_against_the_same_count_of_closes(tmp_path):
    prior = [(f"2025-{m:02d}", float(g)) for m, g in zip(range(9, 13), range(-5, -1))]
    prior += [(f"2026-{m:02d}", float(g)) for m, g in zip(range(1, 10), range(-1, 8))]          # 13 months, gaps -5 .. 7
    closes = month_closes(prior, ("2026-10", 2.0))
    result = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-10-05"), events=EVENTS, daily_closes=closes))
    assert result.data["month"]["pension_stock_minus_bond_mtd_pts"] == {"v": 2.0, "r": [6, 12]}   # 2025-09 has no month before it
    thin = month_closes([("2026-09", -1.0)], ("2026-10", 2.0))
    result = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-10-05"), events=EVENTS, daily_closes=thin))
    assert result.data["month"]["pension_stock_minus_bond_mtd_pts"] == {"v": 2.0}


def test_pension_gap_is_absent_on_the_months_first_session(tmp_path):
    closes = month_closes([("2026-09", -1.0)], ("2026-10", 2.0))
    closes = {s: [r for r in rows if r["day"] < "2026-10"] for s, rows in closes.items()}
    result = build_calendar_block(fake_inputs(tmp_path, cut=at(14, 30, day="2026-10-01"), events=EVENTS, daily_closes=closes))
    assert absent_reasons(result)["calendar.month.pension_stock_minus_bond_mtd_pts"] == "no_close_yet_this_month"


# --- scale -----------------------------------------------------------------------------------------------------

def test_scale_on_a_native_book(tmp_path):
    cut = at(14, 30, 12)
    inputs = fake_inputs(tmp_path, cut=cut, row=native_row(cut), bars=flat_bars(step=1.0),
                         market=FakeMarket({"$VIX1D": [(at(14, 30, 5), 8.72)]}))
    result = build_scale_block(inputs)
    assert result.data == {"sigma_src": "anchor", "sigma_live_x": 0.55, "atm_iv_pct": 8.44, "straddle_open_sig": 0.28,
                           "straddle_left_sig": 0.076, "straddle_used_x": 1.32, "vix1d": 8.72, "vix1d_prior_close": 10.23,
                           "realized_30m_vs_priced_x": 0.26}      # sqrt(30) points / 75.31 over sqrt(30/390)
    assert not result.absent


def test_scale_drops_the_book_fields_on_a_stand_in_book(tmp_path):
    cut = at(14, 30, 12)
    result = build_scale_block(fake_inputs(tmp_path, cut=cut, row=native_row(cut, gex_source="spy_proxy×10.0346")))
    reasons = absent_reasons(result)
    assert {reasons[f"scale.{f}"] for f in ("atm_iv_pct", "straddle_open_sig", "straddle_left_sig", "straddle_used_x")} == {"stand_in_book"}
    assert result.data["sigma_live_x"] == 0.55 and "atm_iv_pct" not in result.data


def test_scale_reads_the_book_off_the_raw_diary_line_when_the_row_has_none(tmp_path):
    cut = at(14, 30, 12)
    row = native_row(cut)
    del row["gex_source"]
    assert "scale.atm_iv_pct" in absent_reasons(build_scale_block(fake_inputs(tmp_path, cut=cut, row=dict(row))))
    diary = tmp_path / "state" / "reversion" / "2026-10-09.jsonl"
    diary.parent.mkdir(parents=True)
    diary.write_text(json.dumps({"ts": row["ts"], "gex_source": "native"}) + "\n", encoding="utf-8")
    assert build_scale_block(fake_inputs(tmp_path, cut=cut, row=dict(row))).data["atm_iv_pct"] == 8.44


def test_scale_at_the_first_read_has_no_quote_and_too_few_bars(tmp_path):
    cut = at(9, 30, 40)
    ruler = {"em_open": 21.21, "em_points": 21.21}
    result = build_scale_block(fake_inputs(tmp_path, cut=cut, row=native_row(cut, range_ruler=ruler), bars=[],
                                           market=FakeMarket({"$VIX1D": [(at(9, 30, 5), 10.23)]})))
    reasons = absent_reasons(result)
    assert reasons["scale.vix1d"] == "not_until_09:31"
    assert reasons["scale.realized_30m_vs_priced_x"] == "fewer_than_25_finished_bars_in_30m"
    assert reasons["scale.straddle_used_x"] == "not_recorded" and result.data["straddle_open_sig"] == 0.28


def test_straddle_used_falls_back_to_the_days_range_over_the_open_straddle(tmp_path):
    cut = at(14, 30, 12)
    result = build_scale_block(fake_inputs(tmp_path, cut=cut, row=native_row(cut, range_ruler={"em_open": 20.0, "em_points": 5.0})))
    assert result.data["straddle_used_x"] == 0.05      # flat bars span one point, over a 20-point open straddle


def test_scale_declares_a_missing_quote_and_prior_close(tmp_path):
    cut = at(14, 30, 12)
    result = build_scale_block(fake_inputs(tmp_path, cut=cut, row=native_row(cut), market=None, vix1d_prior_close=None))
    reasons = absent_reasons(result)
    assert reasons["scale.vix1d"] == "no_vix1d_quote" and reasons["scale.vix1d_prior_close"] == "not_recorded"


# --- the real store --------------------------------------------------------------------------------------------

@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_the_three_blocks_build_for_the_1430_read_with_no_level_or_date():
    state_dir = station_state_dir()
    inputs = load_frozen_inputs(state_dir, ensure_folders(state_dir), READ_ID)
    clock, calendar, scale = (build(inputs) for build in (build_clock_block, build_calendar_block, build_scale_block))
    assert clock.data == {"min_after_open": 300, "min_to_close": 90, "phase": "afternoon"}
    assert set(calendar.data) == {"day_class", "today", "next_high_tier", "fomc", "month", "expiry"}
    assert calendar.data["month"]["trading_day"] == 7 and "r" in calendar.data["month"]["pension_stock_minus_bond_mtd_pts"]
    assert calendar.data["expiry"] == {"today": "daily", "monthly_sessions_ahead": 5}
    assert set(scale.data) == {"sigma_src", "sigma_live_x", "atm_iv_pct", "straddle_open_sig", "straddle_left_sig",
                               "straddle_used_x", "vix1d", "vix1d_prior_close", "realized_30m_vs_priced_x"}
    assert scale.data["sigma_src"] == "anchor" and 0 < scale.data["sigma_live_x"] < 1
    text = json.dumps([r.data for r in (clock, calendar, scale)] + [r.absent for r in (clock, calendar, scale)])
    assert not PRICE_LEVEL.search(text) and not DATE.search(text)
