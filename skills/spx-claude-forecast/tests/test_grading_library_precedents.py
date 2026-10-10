"""Outcomes, the library rows built from them, the base rate and the precedent picker, all offline."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from payload_fixtures import at, flat_bars

from spx_claude_forecast import grading, jsonl_store, library, precedents, paths as forecast_paths

OUTCOME_LINE_FIELDS_IN_ORDER = ("line_type", "read_id", "claude_input_sha256", "horizon", "grading_rule_version",
                                "finalize_attempt_number", "finalized_at", "trading_day", "half_hour_slot_et",
                                "prompt_and_model_version", "read_source", "result")   # spec/record_formats.md's outcome line


def test_direction_and_size_buckets_treat_exactly_one_edge_as_flat():
    assert grading.direction_of(1.0) == "flat" and grading.direction_of(1.01) == "up" and grading.direction_of(-1.01) == "down"
    assert grading.size_bucket_of(0.0) == "flat_within_1_flat_edge"
    assert grading.size_bucket_of(1.5) == "up_1_to_2_flat_edges" and grading.size_bucket_of(-2.5) == "down_2_to_3_flat_edges"
    assert grading.size_bucket_of(3.0) == "up_2_to_3_flat_edges" and grading.size_bucket_of(-3.01) == "down_over_3_flat_edges"


def test_flat_edges_follow_the_station_zones_and_scale_to_close_by_minutes_left():
    edges = grading.flat_edges_for({"next_30": 3.41, "next_60": 4.89, "average_30": 2.0}, minutes_left=90)
    assert edges["next_30_minutes"] == 2.0
    assert edges["next_60_minutes"] == pytest.approx((61 * 121 / (6 * 3600)) ** 0.5 * 4.89, abs=0.01)
    assert edges["to_close"] == pytest.approx(4.89 * (90 / 60) ** 0.5, abs=0.01)
    assert grading.flat_edges_for({"next_60": 0.5}, minutes_left=10)["to_close"] == grading.TO_CLOSE_FLOOR_POINTS


STEP_POINTS = 0.2          # the synthetic session rises this much a minute from 09:30
ZONES = {"next_30": 3.41, "next_60": 4.89, "average_30": 2.0}


def _session(official_close: float | None = 7850.0) -> grading.SessionPrices:
    """A whole synthetic session of rising bars (09:30 to 15:59) with no prior sessions on file."""
    return grading.SessionPrices(day="2026-10-09", bars=flat_bars(until_hh=15, until_mm=59, price=7813.01, step=STEP_POINTS),
                                 official_close=official_close, prior_bars={})


def _price_at(hh: int, mm: int) -> float:
    return 7813.01 + ((hh - 9) * 60 + mm - 30) * STEP_POINTS


def test_grade_read_lays_the_outcome_line_out_as_the_spec_does_and_grades_each_horizon():
    spot = _price_at(14, 30)
    lines = grading.grade_read(_session(), "live:2026-10-09T14:30:12-04:00", "2026-10-09T14:30:12-04:00", spot, ZONES,
                               slot="14:30", claude_input_sha256="sha256:abc", prompt_and_model_version="cr-1", now=at(17, 30))
    by_h = {l["horizon"]: l for l in lines}
    assert tuple(lines[0])[:len(OUTCOME_LINE_FIELDS_IN_ORDER)] == OUTCOME_LINE_FIELDS_IN_ORDER
    assert lines[0]["claude_input_sha256"] == "sha256:abc" and lines[0]["prompt_and_model_version"] == "cr-1"
    assert lines[0]["finalized_at"] == "2026-10-09T17:30:00-04:00"
    h30 = by_h["next_30_minutes"]["result"]
    assert h30["status"] == "final" and h30["graded_move_measured_to"] == "window_average_price" and h30["window_minutes"] == 30
    assert h30["graded_move_points"] == pytest.approx(STEP_POINTS * 14.5, abs=0.01)     # the window's 30 bars start at the read's minute
    assert h30["flat_edge_points"] == 2.0 and h30["direction"] == "up" and h30["size_bucket"] == "up_1_to_2_flat_edges"
    assert h30["graded_move_flat_edges"] == pytest.approx(h30["graded_move_points"] / 2.0, abs=0.001)
    tc = by_h["to_close"]["result"]
    assert tc["status"] == "final" and tc["graded_move_measured_to"] == "official_close_price" and tc["window_minutes"] == 90
    assert tc["graded_move_points"] == pytest.approx(7850.0 - spot, abs=0.01) and tc["direction"] == "down"


def test_grade_read_at_the_close_marks_windows_past_it_not_asked_and_measures_to_the_last_bar_without_a_close():
    lines = grading.grade_read(_session(official_close=None), "live:x", "2026-10-09T15:31:40-04:00", _price_at(15, 31), ZONES, slot="15:30")
    by_h = {l["horizon"]: l["result"] for l in lines}
    assert by_h["next_30_minutes"]["status"] == "final" and by_h["next_30_minutes"]["window_minutes"] == 29   # graded to the close
    assert by_h["next_60_minutes"]["status"] == "not_asked"
    assert by_h["to_close"]["status"] == "final" and by_h["to_close"]["graded_move_measured_to"] == "last_bar_close_price"
    assert by_h["to_close"]["graded_move_points"] == pytest.approx(_price_at(15, 59) - _price_at(15, 31), abs=0.01)
    after_close = grading.grade_read(_session(), "live:x", "2026-10-09T16:00:10-04:00", _price_at(16, 0), ZONES, slot="16:00")
    assert {l["horizon"]: l["result"]["status"] for l in after_close} == {h: "not_asked" for h in (*grading.HORIZON_WINDOWS, "to_close")}
    no_zones = grading.grade_read(_session(), "live:x", "2026-10-09T14:30:12-04:00", _price_at(14, 30), "no zones", slot="14:30")
    assert all(l["result"]["status"] == "no_data" for l in no_zones)


def _read_record(shown_source: str = "pool_v2", archived_seconds_after: int = 28) -> dict:
    clock = {"probabilities": {"up": 0.1834, "down": 0.1627, "flat": 0.654}}
    main = {"up": 0.2361, "flat": 0.616, "down": 0.1479}
    return {"row_ts": "2026-10-09T14:30:12-04:00",
            "archived_at": (at(14, 30, 12) + timedelta(seconds=archived_seconds_after)).isoformat(),
            "hour": {"average": {"probabilities": main, "clock": clock, "shown_source": shown_source},
                     "by": {"next_60": {"probabilities": main, "clock": clock, "shown_source": shown_source}}}}


def test_comparison_forecasts_copy_the_station_odds_and_only_a_live_main_forecast():
    found, missing = grading.comparison_forecasts_from_read_record(_read_record(), "next_30_minutes")
    assert found["existing_system_time_of_day_odds_20_sessions"] == {"up_pct": 18.3, "flat_pct": 65.4, "down_pct": 16.3,
                                                                     "forecasts_move_measured_to": "window_average_price"}
    main = found["existing_system_main_forecast"]
    assert main["is_written_live"] is True and main["written_after_read_seconds"] == 28 and main["written_at"].endswith("-04:00")
    assert (main["up_pct"], main["flat_pct"], main["down_pct"]) == (23.6, 61.6, 14.8) and missing == {}
    h60, _ = grading.comparison_forecasts_from_read_record(_read_record(), "next_60_minutes")
    assert h60["existing_system_main_forecast"]["forecasts_move_measured_to"] == "window_end_price"
    _, replay = grading.comparison_forecasts_from_read_record(_read_record(archived_seconds_after=3 * 86400), "next_30_minutes")
    assert replay["existing_system_main_forecast"].startswith("a replay")
    _, other = grading.comparison_forecasts_from_read_record(_read_record(shown_source="blend50"), "next_30_minutes")
    assert "not pool_v2" in other["existing_system_main_forecast"]
    _, none = grading.comparison_forecasts_from_read_record(None, "to_close")
    assert set(none) == {"existing_system_time_of_day_odds_20_sessions", "existing_system_main_forecast"}


def test_the_existing_system_result_check_takes_the_newest_rule_and_compares_directions():
    grades = {("2026-10-09T14:30:12-04:00", "next_30"): {"label": "flat", "rule_version": 4, "edge": 2.06, "g": 0.82}}
    check = grading.existing_system_result_check(grades, "2026-10-09T14:30:12-04:00", "next_30_minutes", "flat")
    assert check == {"direction": "flat", "grading_rule_version": 4, "flat_edge_points": 2.06, "graded_move_points": 0.82,
                     "is_same_direction": True}
    assert grading.existing_system_result_check(grades, "2026-10-09T14:30:12-04:00", "next_30_minutes", "up")["is_same_direction"] is False
    assert grading.existing_system_result_check(grades, "2026-10-09T14:30:12-04:00", "to_close", "flat") is None


def _payload_line(day: str, slot: str, read_id: str, fingerprint: dict, status: str = "built", flags: dict | None = None) -> dict:
    scene = {"tape": {"price_minus_close_sig": {"v": fingerprint["vs_close"]}, "m30_sig": {"v": fingerprint["m30"]},
                      "range_sig": {"v": fingerprint["range"]}, "range_pos": fingerprint["range_pos"],
                      "price_minus_vwap_sig": {"v": fingerprint["vs_vwap"]}},
             "open_signals": {"gap": {"settled_sig": {"v": fingerprint["gap"]}}},
             "cross_asset": {"vix_vix3m": fingerprint["vix_term"]}}
    return {"line_type": "payload", "read_id": read_id, "trading_day": day, "half_hour_slot_et": slot, "status": status,
            "claude_input_sha256": "sha256:" + read_id[-16:].replace(":", "0"), "quality_flags": flags or {"anchor_vs_vix_x": 1.0},
            "scene": scene}


def _outcome(read_id: str, horizon: str, edges: float) -> dict:
    return {"line_type": "outcome", "read_id": read_id, "horizon": horizon, "finalize_attempt_number": 1,
            "result": {"status": "final", "graded_move_flat_edges": edges, "direction": grading.direction_of(edges),
                       "size_bucket": grading.size_bucket_of(edges)}}


def _fp(gap=0.3, vs_close=0.6, m30=0.03, range_=0.46, range_pos=0.9, vs_vwap=0.2, vix_term=0.83) -> dict:
    return {"gap": gap, "vs_close": vs_close, "m30": m30, "range": range_, "range_pos": range_pos, "vs_vwap": vs_vwap, "vix_term": vix_term}


def test_library_row_carries_the_fingerprint_outcomes_and_gates():
    line = _payload_line("2026-10-01", "14:30", "seed:2026-10-01T14:30", _fp())
    outcomes = [_outcome("seed:2026-10-01T14:30", "next_30_minutes", 0.4), _outcome("seed:2026-10-01T14:30", "to_close", -1.6)]
    row = library.library_row({**line, "origin": "seed"}, outcomes)
    assert row["gap_sig"] == 0.3 and row["vix_term_ratio"] == 0.83 and row["slot_minute_of_day"] == 14 * 60 + 30
    assert row["next_30_minutes_direction"] == "flat" and row["to_close_size_bucket"] == "down_1_to_2_flat_edges"
    assert row["next_60_minutes_status"] == "missing"
    assert row["is_eligible_precedent"] is True and row["is_eligible_base_rate"] is True


def test_a_manual_exclusion_day_or_a_wrong_sized_anchor_makes_a_row_ineligible():
    bad_day = _payload_line("2026-10-07", "14:30", "seed:2026-10-07T14:30", _fp())
    assert library.unit_suspect_reason(bad_day).startswith("options outage")
    bad_anchor = _payload_line("2026-09-01", "14:30", "seed:2026-09-01T14:30", _fp(), flags={"anchor_vs_vix_x": 0.5})
    row = library.library_row({**bad_anchor, "origin": "seed"}, [_outcome("seed:2026-09-01T14:30", "next_30_minutes", 0.1)])
    assert row["is_unit_suspect"] is True and row["is_eligible_precedent"] is False and "0.50" in row["exclude_reason"]


def _write_history(paths: forecast_paths.ForecastPaths, days: list[str], slots=("14:00", "14:30", "15:00")) -> None:
    for i, day in enumerate(days):
        payloads, outcomes = [], []
        for j, slot in enumerate(slots):
            read_id = f"seed:{day}T{slot}"
            fp = _fp(gap=0.1 * (i % 5), m30=0.01 * j, range_=0.3 + 0.05 * (i % 4), vs_close=0.2 + 0.1 * (i % 3),
                     range_pos=0.2 + 0.1 * (i % 7), vs_vwap=0.05 * (j - 1), vix_term=0.8 + 0.01 * (i % 6))
            payloads.append(_payload_line(day, slot, read_id, fp))
            edges = ((i + j) % 5) - 2          # -2..2: a spread of outcomes
            for horizon in library.HORIZONS:
                outcomes.append(_outcome(read_id, horizon, float(edges)))
        for line in payloads:
            jsonl_store.append_json_line(paths.seed_payloads_file(day), line, fsync=False)
        for line in outcomes:
            jsonl_store.append_json_line(paths.seed_outcomes_file(day), line, fsync=False)


def _days(n: int) -> list[str]:
    from datetime import date, timedelta
    start = date(2026, 7, 20)
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_the_library_rebuilds_from_the_record_and_a_live_row_beats_a_seed_row(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    days = _days(20)
    _write_history(paths, days)
    live_id = f"live:{days[-1]}T14:30:12-04:00"
    jsonl_store.append_json_line(paths.payloads_file(days[-1]), _payload_line(days[-1], "14:30", live_id, _fp(gap=9.9)), fsync=False)
    jsonl_store.append_json_line(paths.outcomes_file(days[-1]), _outcome(live_id, "next_30_minutes", 1.2), fsync=False)
    extra_id = f"live:{days[-1]}T14:44:02-04:00"                                      # a second live read in the same slot is kept
    jsonl_store.append_json_line(paths.payloads_file(days[-1]), _payload_line(days[-1], "14:30", extra_id, _fp(gap=8.8)), fsync=False)
    manifest = library.rebuild_library(paths)
    rows = library.read_library(paths)
    assert manifest["rows"] == len(rows) == 20 * 3 + 1
    live_rows = {r["read_id"]: r for r in rows if r["origin"] == "live"}
    assert set(live_rows) == {live_id, extra_id} and live_rows[live_id]["gap_sig"] == 9.9
    assert live_rows[live_id]["next_30_minutes_direction"] == "up" and live_rows[extra_id]["next_30_minutes_status"] == "missing"
    assert not any(r["read_id"] == f"seed:{days[-1]}T14:30" for r in rows)            # the seed row on that slot gave way
    assert paths.library_manifest_file.exists() and manifest["sessions"] == 20


def test_base_rate_pools_neighbouring_half_hours_one_vote_per_session_and_only_earlier_days(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    days = _days(20)
    _write_history(paths, days)
    rows = library.collect_rows(paths)
    today = days[-1]
    base = precedents.base_rate_for(rows, 14 * 60 + 30, before_day=today)
    assert base["sessions"] == 19 and base["pooled_minutes"] == 30
    counts = base["next_30_minutes"]["counts"]
    assert len(counts) == 7 and sum(counts) == 19            # one vote per session, 19 earlier sessions
    assert set(base["next_30_minutes"]) >= {"up_pct", "flat_pct", "down_pct", "sessions"}
    thin = precedents.base_rate_for(rows, 14 * 60 + 30, before_day=days[5])
    assert thin["pooled_minutes"] == 0 and thin["sessions"] == 5


def test_precedents_are_gated_by_time_of_day_scored_on_the_seven_items_and_one_per_session(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    days = _days(20)
    _write_history(paths, days)
    rows = library.collect_rows(paths)
    today = days[-1]
    now = _fp(gap=0.1, m30=0.0, range_=0.3, vs_close=0.2)
    now_fp = {"gap_sig": now["gap"], "price_minus_close_sig": now["vs_close"], "last_30_minutes_sig": now["m30"],
              "range_sig": now["range"], "range_position": now["range_pos"], "price_minus_vwap_sig": now["vs_vwap"],
              "vix_term_ratio": now["vix_term"]}
    picked, record = precedents.pick_precedents(rows, now_fp, 14 * 60 + 30, before_day=today)
    assert len(picked) == 8 and len({p["trading_day"] for p in picked}) == 8
    assert all(p["trading_day"] < today for p in picked)
    assert all(abs(p["row"]["slot_minute_of_day"] - (14 * 60 + 30)) <= 45 for p in picked)
    assert picked[0]["distance"] <= picked[-1]["distance"] and record["candidates"] > 0
    history = precedents.history_for_read(rows, now_fp, 14 * 60 + 30, before_day=today)
    card = history.cards[0]
    assert card["id"].startswith("p") and "ago_sessions" in card and "outcome_flat_edges" in card
    text = json.dumps(history.cards)
    assert "2026-" not in text and "read_id" not in text          # never a date, never a join key
    assert len(history.precedent_set) == 8 and history.precedent_set[0]["read_id"].startswith("seed:")


def test_too_few_qualifying_past_reads_leaves_the_cards_out(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    days = _days(3)
    _write_history(paths, days)
    rows = library.collect_rows(paths)
    history = precedents.history_for_read(rows, {"gap_sig": 0.1}, 14 * 60 + 30, before_day=days[-1])
    assert history.cards == [] and history.absent and history.absent[-1]["path"] == "precedents"


def test_shown_precedents_outcomes_blends_the_cards_with_the_base_rate():
    cards = [{"outcome_flat_edges": {"next_30_minutes": 1.5}}, {"outcome_flat_edges": {"next_30_minutes": -0.2}},
             {"outcome_flat_edges": {"next_30_minutes": 0.1}}]
    out = precedents.shown_precedents_outcomes(cards, [1, 4, 4, 33, 5, 2, 1], "next_30_minutes")
    assert out["precedent_count"] == 3 and out["base_rate_blended_in_as_precedent_count"] == 20
    assert abs(out["up_pct"] + out["flat_pct"] + out["down_pct"] - 100) < 0.3
    assert precedents.shown_precedents_outcomes([], None, "next_30_minutes") is None
