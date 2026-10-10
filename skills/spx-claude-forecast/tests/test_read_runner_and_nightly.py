"""The payload line's checks and stamps, one read end to end with a stand-in Claude, then the night: sealing, the
library, the scores, the scorecard."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pytest

from payload_fixtures import REAL_READ_ID, fake_inputs

from spx_claude_forecast import claude_call, jsonl_store, nightly, read_runner, scorecard, scoring, paths as forecast_paths
from spx_claude_forecast.control import ET
from spx_claude_forecast.payload import build
from spx_claude_forecast.payload.blocks.data_sources import build_data_sources_block


# --- the payload line ------------------------------------------------------------------------------------------

def test_build_payload_on_fake_inputs_gives_a_hashed_scene_an_ask_and_two_prompts(tmp_path):
    inputs = fake_inputs(tmp_path)
    built = build.build_payload(inputs, [], origin="live")
    assert built.claude_input_sha256.startswith("sha256:") and built.line["status"] in ("built", "refused")
    assert set(built.prompts) == {"answer_1", "answer_2"} and built.direction_order["answer_2"] == ["down_pct", "flat_pct", "up_pct"]
    assert "next_30_minutes" in built.asked_horizons and "to_close" in built.asked_horizons
    assert built.ask["horizons"]["next_30_minutes"]["flat_edge_sig"] == pytest.approx(2.0 / 75.31, abs=0.001)
    assert built.line["builder_sha"] and built.line["rules_sha"] and built.line["prompt_sha256"]["answer_1"] != built.line["prompt_sha256"]["answer_2"]
    assert "absent" in built.scene and {"path": "precedents", "why": "fewer_than_5_qualifying_past_reads"} in built.scene["absent"]


def test_the_leak_check_refuses_a_date_a_price_level_or_another_voice_and_lets_counts_and_ages_through():
    assert build.leak_checks({"a": "2026-10-09"}) and build.leak_checks({"spot": 7813.01}) and build.leak_checks({"x": "JEV said up"})
    assert build.leak_checks({"levels": [{"what": "flip", "sig": -0.5}], "spot_level": 7813}) and build.leak_checks({"x": [7813.0]})
    assert build.leak_checks({"tape": {"high": {"minus_price_sig": 1260}}}) == ["a number that reads as a price level is in the scene: ['minus_price_sig=1260']"]
    assert build.leak_checks({"tape": {"m30_sig": {"v": 0.03}}, "sessions": 50, "pct": 99.9, "trades": 16641, "add_live_approx": 1203,
                              "data_sources": {"headlines": {"age_s": 1260}}, "base_rate": {"next_30_minutes": {"counts": {"up": 1200}}},
                              "open_signals": {"opt_imbalance_10m": {"trades": 16641}}}) == []


def test_load_notes_stay_on_the_payload_line_and_out_of_the_scene(tmp_path):
    inputs = fake_inputs(tmp_path)
    inputs.load_notes.append("archive read: no line for this read_id")
    assert "load_notes" not in build_data_sources_block(inputs).data
    assert build.quality_flags(inputs, [])["load_notes"] == ["archive read: no line for this read_id"]
    assert build.leak_checks({"data_sources": build_data_sources_block(inputs).data}) == []


def test_built_at_and_the_lag_are_one_instant(tmp_path):
    inputs = fake_inputs(tmp_path)
    line = build.build_payload(inputs, [], origin="live").line
    assert abs((datetime.fromisoformat(line["built_at"]) - inputs.cut).total_seconds() - line["built_lag_s"]) < 1.0


# --- the read runner -------------------------------------------------------------------------------------------

def _answer_for(prompt: str) -> dict:
    """A plausible reply that follows the rulebook, built from the ask inside the prompt."""
    ask = json.loads(prompt.split("\n\nASK:\n", 1)[1])
    forecast = {}
    for horizon in ask["horizons"]:
        forecast[horizon] = {"up_pct": 20, "flat_pct": 60, "down_pct": 20,
                             "up_by_size_pct": {"up_1_to_2_flat_edges": 14, "up_2_to_3_flat_edges": 4, "up_over_3_flat_edges": 2},
                             "down_by_size_pct": {"down_1_to_2_flat_edges": 14, "down_2_to_3_flat_edges": 4, "down_over_3_flat_edges": 2}}
    return {"similar_precedents": [], "reasons": [{"input_field_path": "clock.min_to_close", "pushes_toward": "smaller_move", "horizon": "all"},
                                                  {"input_field_path": "clock.min_after_open", "pushes_toward": "up", "horizon": "all"}],
            "strongest_reason_against_lean": {"input_field_path": "clock.phase", "pushes_toward": "down", "horizon": "all"},
            "forecast": forecast}


def fake_call(prompt: str, rulebook: str, **_) -> claude_call.CallResult:
    answer = _answer_for(prompt)
    return claude_call.CallResult(json.dumps(answer), answer, None, 1.5, {"cost_usd": 0.1, "input_tokens": 5000, "output_tokens": 300},
                                  cli_version="test", model_served="claude-opus-5-5")


def test_the_real_caller_is_refused_under_pytest_and_a_stand_in_is_not(state_dir):
    """pytest sets PYTEST_CURRENT_TEST; a runner started under a test run must never reach Claude."""
    paths = forecast_paths.ForecastPaths(state_dir)
    assert read_runner._why_not_to_call(paths, "2026-10-09", claude_call.call_claude) == "refused_under_pytest"
    assert read_runner._why_not_to_call(paths, "2026-10-09", fake_call) is None


def test_the_daily_caps_count_every_answer_line_of_the_day(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    jsonl_store.write_json_atomically(paths.control_file, {"max_calls_per_day": 2, "max_usd_per_day": 15.0})
    for n in (1, 2):
        jsonl_store.append_json_line(paths.reads_file("2026-10-09"), {"line_type": "claude_answer", "answer_id": f"r#answer_{n}",
                                                                       "call_stats": {}, "error": "timeout after 120 s"}, fsync=False)
    assert read_runner._why_not_to_call(paths, "2026-10-09", fake_call) == "capped"
    assert read_runner._why_not_to_call(paths, "2026-10-08", fake_call) is None


@pytest.fixture(scope="module")
def real_run(station_state: Path) -> dict:
    """The real 10-09 14:30 read, run once end to end with the stand-in Claude into the session's temp forecast folder;
    the read test reads its lines and the nightly test seals them."""
    return read_runner.run_read(station_state, REAL_READ_ID, call=fake_call)


def test_a_real_read_runs_end_to_end_with_a_stand_in_claude(station_state, real_run):
    """The real 10-09 14:30 read: payload line, two answer lines, a final line and latest.json, no leaks."""
    assert real_run["status"] == "built", real_run
    paths = forecast_paths.ForecastPaths(station_state)
    payloads = [l for l in jsonl_store.read_json_lines(paths.payloads_file("2026-10-09")) if l["read_id"] == REAL_READ_ID]
    reads = jsonl_store.read_json_lines(paths.reads_file("2026-10-09"))
    assert len(payloads) == 1 and payloads[0]["status"] == "built"
    assert [l["line_type"] for l in reads] == ["claude_answer", "claude_answer", "final_forecast"]
    assert [l["answer_id"] for l in reads[:2]] == [f"{REAL_READ_ID}#answer_1", f"{REAL_READ_ID}#answer_2"]
    final = reads[-1]
    assert final["usable_answer_count"] == 2 and final["forecast"]["next_30_minutes"]["up_pct"] == 20
    assert final["forecast"]["next_30_minutes"]["answer_2"]["up_pct"] == 20 and final["written_at"].endswith("-04:00")
    assert "shown_precedents_outcomes" not in final and final["picked_precedents_among_code_nearest_3_count"] is None
    assert json.loads(paths.latest_file.read_text())["read_id"] == REAL_READ_ID
    assert read_runner.run_read(station_state, REAL_READ_ID, call=fake_call)["skipped"] == "already written"
    blocks = set(payloads[0]["scene"]) - {"absent", "precedents"}
    assert {"clock", "tape", "scale", "data_sources"} <= blocks, blocks


# --- the night -------------------------------------------------------------------------------------------------

def _write_a_sealed_day(paths: forecast_paths.ForecastPaths, day: str, slots: tuple[str, ...]) -> None:
    for slot in slots:
        read_id = f"live:{day}T{slot}:12-04:00"
        base = {"next_30_minutes": {"up_pct": 19.3, "flat_pct": 59.6, "down_pct": 21.1}}
        jsonl_store.append_json_line(paths.payloads_file(day), {"line_type": "payload", "read_id": read_id, "trading_day": day,
                                                                "half_hour_slot_et": slot, "status": "built", "origin": "live",
                                                                "base_rate_shown_to_claude": base, "scene": {}}, fsync=False)
        buckets = {b: (75 if b == "flat_within_1_flat_edge" else 5 if b.endswith("1_to_2_flat_edges") else 2.5) for b in scoring.SIZE_BUCKETS}
        final = {"line_type": "final_forecast", "read_id": read_id, "test_variant": "none", "status": "ok", "trading_day": day,
                 "any_answer_has_valid_reason": True,
                 "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 10, "flat_pct": 75, "down_pct": 15, "size_buckets_pct": buckets,
                                                  "is_within_3_pct_points_of_base_rate": False,
                                                  "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": True,
                                                  "largest_gap_between_answers_pct_points": 4}}}
        jsonl_store.append_json_line(paths.reads_file(day), {"line_type": "claude_answer", "answer_id": f"{read_id}#answer_1", "read_id": read_id,
                                                             "test_variant": "none", "call_stats": {"cost_usd": 0.1}}, fsync=False)
        jsonl_store.append_json_line(paths.reads_file(day), final, fsync=False)
        jsonl_store.append_json_line(paths.outcomes_file(day), {
            "line_type": "outcome", "read_id": read_id, "claude_input_sha256": "sha256:0000000000000000", "horizon": "next_30_minutes",
            "grading_rule_version": 1, "finalize_attempt_number": 1, "trading_day": day, "half_hour_slot_et": slot,
            "prompt_and_model_version": "cr-1", "read_source": "live",
            "result": {"status": "final", "direction": "flat", "size_bucket": "flat_within_1_flat_edge", "graded_move_flat_edges": 0.2},
            "comparison_forecasts": {"base_rate_shown_to_claude": base["next_30_minutes"],
                                     "existing_system_main_forecast": {"up_pct": 23.6, "flat_pct": 61.6, "down_pct": 14.8}}}, fsync=False)


def test_scores_carry_the_spec_columns_and_a_missing_claude_answer_scores_as_the_base_rate(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    _write_a_sealed_day(paths, "2026-10-08", ("14:30",))
    rows = {r["forecaster"]: r for r in scoring.collect_score_rows(paths)}
    assert set(rows) == {"claude_final", "claude_answer_1", "claude_answer_2", "base_rate_shown_to_claude", "existing_system_main_forecast"}
    final = rows["claude_final"]
    for column in ("trading_day", "half_hour_slot_et", "prompt_and_model_version", "read_source", "grading_rule_version",
                   "claude_input_sha256", "test_variant", "horizon", "forecast_status", "result_direction", "result_size_bucket",
                   "up_pct", "flat_pct", "down_pct", "top_choice_pct", "log_loss", "log_loss_move_part", "log_loss_direction_part",
                   "brier_score", "size_ranked_probability_score", "is_top_choice_correct", "scoring_code_version"):
        assert column in final, column
    assert final["result_direction"] == "flat" and final["is_top_choice_correct"] and final["top_choice_pct"] == 75.0
    assert final["log_loss"] == pytest.approx(-math.log(0.75), abs=0.001)
    assert final["log_loss_move_part"] + final["log_loss_direction_part"] == pytest.approx(final["log_loss"], abs=0.001)
    assert 0 < final["size_ranked_probability_score"] < 0.1 and final["is_scored_as_base_rate"] is False
    for name in ("claude_answer_1", "claude_answer_2"):              # answer 1 has no checked block, answer 2 no line at all
        assert rows[name]["is_scored_as_base_rate"] and rows[name]["forecast_status"] == "missing"
        assert rows[name]["flat_pct"] == 59.6 and rows[name]["size_ranked_probability_score"] is None
    assert rows["existing_system_main_forecast"]["forecast_status"] == "final" and rows["base_rate_shown_to_claude"]["flat_pct"] == 59.6


def test_scores_and_the_scorecard_pair_claude_with_each_reference_on_the_same_reads(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    for day in ("2026-10-06", "2026-10-07", "2026-10-08"):
        _write_a_sealed_day(paths, day, ("10:00", "14:30"))
    rows = scoring.collect_score_rows(paths)
    claude = [r for r in rows if r["forecaster"] == scoring.CLAUDE_FINAL]
    assert len(claude) == 6 and all(r["result_direction"] == "flat" and r["is_top_choice_correct"] for r in claude)
    assert scoring.write_scores(paths, rows) == len(rows)
    card = scorecard.write_scorecard(paths, scoring.read_scores(paths))
    h30 = card["horizons"]["next_30_minutes"]
    assert card["verdict"] == "Too early" and h30["reads"] == 6 and h30["claude_right_pct"] == 100.0 and h30["reads_scored_as_base_rate"] == 0
    assert h30["skill_vs"]["base_rate_shown_to_claude"]["skill_pct"] > 0           # 75% flat beats 59.6% flat when flat happens
    assert h30["skill_vs"]["existing_system_main_forecast"]["reads"] == 6
    assert h30["skill_vs"]["existing_system_main_forecast"]["reference_right_pct"] == 100.0
    assert card["watchdogs"]["leaned_with_last_30_minutes_pct"] == 100.0 and card["watchdogs"]["cost_usd_total"] == pytest.approx(0.6)
    assert len(card["daily_skill_vs_base_rate"]) == 3


def test_sealing_is_refused_before_the_close_plus_75_minutes(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    early = datetime(2026, 10, 9, 16, 30, tzinfo=ET)
    assert nightly.seal_day(state_dir, paths, "2026-10-09", now=early)["sealed"] == 0
    assert "refused" in nightly.seal_day(state_dir, paths, "2026-10-09", now=early)["why"]


def test_the_night_seals_a_real_read_once_per_horizon_and_rebuilds_the_library(station_state, real_run, tmp_path):
    assert real_run["status"] == "built", real_run
    paths = forecast_paths.ensure_folders(station_state)        # arms the write guard for this root, as run_nightly does before sealing
    late = datetime(2026, 10, 9, 17, 30, tzinfo=ET)
    assert nightly.seal_day(station_state, paths, "2026-10-09", now=late) == {"day": "2026-10-09", "sealed": 1, "outcome_lines": 3, "skipped": 0}
    outcomes = jsonl_store.read_json_lines(paths.outcomes_file("2026-10-09"))
    by_h = {o["horizon"]: o for o in outcomes}
    h30 = by_h["next_30_minutes"]
    assert h30["result"]["direction"] == "flat" and h30["result"]["flat_edge_points"] == 2.0 and h30["finalized_at"] == "2026-10-09T17:30:00-04:00"
    assert h30["claude_input_sha256"].startswith("sha256:") and h30["prompt_and_model_version"] == "cr-1"
    assert h30["existing_system_result_check"]["is_same_direction"] is True
    main = h30["comparison_forecasts"]["existing_system_main_forecast"]
    assert main["is_written_live"] is True and main["written_after_read_seconds"] <= 600
    assert set(h30["comparison_forecasts"]) == {"existing_system_time_of_day_odds_20_sessions", "existing_system_main_forecast"}
    assert set(h30["missing_comparison_forecasts"]) == {"base_rate_shown_to_claude", "shown_precedents_outcomes"}   # no library yet
    assert by_h["next_60_minutes"]["comparison_forecasts"]["existing_system_main_forecast"]["forecasts_move_measured_to"] == "window_end_price"
    # sealing again writes nothing: every horizon is settled
    assert nightly.seal_day(station_state, paths, "2026-10-09", now=late)["outcome_lines"] == 0
    # a horizon left no_data earlier is tried again and, graded now, gets the next finalize_attempt_number
    payload = next(l for l in jsonl_store.read_json_lines(paths.payloads_file("2026-10-09")) if l["read_id"] == REAL_READ_ID)
    second = {**payload, "read_id": "live:2026-10-09T14:00:51.311512-04:00",
              "logged_never_sent": {**payload["logged_never_sent"], "row_ts": "2026-10-09T14:00:51.311512-04:00"}}
    jsonl_store.append_json_line(paths.payloads_file("2026-10-09"), second, fsync=False)
    for horizon, status in (("next_30_minutes", "no_data"), ("next_60_minutes", "final"), ("to_close", "final")):
        jsonl_store.append_json_line(paths.outcomes_file("2026-10-09"), {"line_type": "outcome", "read_id": second["read_id"],
                                                                         "horizon": horizon, "finalize_attempt_number": 1,
                                                                         "result": {"status": status}}, fsync=False)
    assert nightly.seal_day(station_state, paths, "2026-10-09", now=late)["outcome_lines"] == 1
    retried = [o for o in jsonl_store.read_json_lines(paths.outcomes_file("2026-10-09")) if o["read_id"] == second["read_id"]]
    assert len(retried) == 4 and retried[-1]["horizon"] == "next_30_minutes" and retried[-1]["finalize_attempt_number"] == 2
    assert retried[-1]["result"]["status"] == "final"
    results = nightly.run_nightly(station_state, "2026-10-09", now=late, backup_dir=tmp_path / "backup")
    assert results["library"]["rows"] >= 2 and results["scores"]["rows"] >= 3 and "error" not in results["scorecard"]
    assert (tmp_path / "backup" / "outcomes" / "2026-10-09.jsonl").exists()
