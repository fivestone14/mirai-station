"""One read end to end with a stand-in Claude, then the night: sealing, the library, the scores, the scorecard."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from payload_fixtures import at, fake_inputs, station_state_dir

from spx_claude_forecast import claude_call, grading, jsonl_store, nightly, read_runner, scorecard, scoring, paths as forecast_paths
from spx_claude_forecast.control import ET
from spx_claude_forecast.payload import build


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


def test_build_payload_on_fake_inputs_gives_a_hashed_scene_an_ask_and_two_prompts(tmp_path):
    inputs = fake_inputs(tmp_path)
    built = build.build_payload(inputs, [], origin="live")
    assert built.claude_input_sha256.startswith("sha256:") and built.line["status"] in ("built", "refused")
    assert set(built.prompts) == {"answer_1", "answer_2"} and built.direction_order["answer_2"] == ["down_pct", "flat_pct", "up_pct"]
    assert "next_30_minutes" in built.asked_horizons and "to_close" in built.asked_horizons
    assert built.ask["horizons"]["next_30_minutes"]["flat_edge_sig"] == pytest.approx(2.0 / 75.31, abs=0.001)
    assert built.line["builder_sha"] and built.line["rules_sha"] and built.line["prompt_sha256"]["answer_1"] != built.line["prompt_sha256"]["answer_2"]
    assert "absent" in built.scene and {"path": "precedents", "why": "fewer_than_5_qualifying_past_reads"} in built.scene["absent"]


def test_leak_checks_catch_a_date_a_price_level_and_another_voice():
    assert build.leak_checks({"a": "2026-10-09"}) and build.leak_checks({"spot": 7813.01}) and build.leak_checks({"x": "JEV said up"})
    assert build.leak_checks({"tape": {"m30_sig": {"v": 0.03}}, "sessions": 50, "pct": 99.9, "trades": 16641, "add_live_approx": 1203}) == []
    assert build.leak_checks({"levels": [{"what": "flip", "sig": -0.5}], "spot_level": 7813}) and build.leak_checks({"x": [7813.0]})


@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_a_real_read_runs_end_to_end_with_a_stand_in_claude(tmp_path, monkeypatch):
    """The real 10-09 14:30 read: payload line, two answer lines, a final line and latest.json, no leaks."""
    real = station_state_dir()
    state = tmp_path / "state"
    state.mkdir()
    for name in ("reversion", "spx_jev", "lob_flow", "siege", "dated_gex"):
        (state / name).symlink_to(real / name)
    read_id = "live:2026-10-09T14:30:12.458122-04:00"
    summary = read_runner.run_read(state, read_id, call=fake_call)
    assert summary["status"] == "built", summary
    paths = forecast_paths.ForecastPaths(state)
    payloads = jsonl_store.read_json_lines(paths.payloads_file("2026-10-09"))
    reads = jsonl_store.read_json_lines(paths.reads_file("2026-10-09"))
    assert len(payloads) == 1 and payloads[0]["status"] == "built"
    assert [l["line_type"] for l in reads] == ["claude_answer", "claude_answer", "final_forecast"]
    assert reads[-1]["usable_answer_count"] == 2 and reads[-1]["forecast"]["next_30_minutes"]["up_pct"] == 20
    assert json.loads(paths.latest_file.read_text())["read_id"] == read_id
    assert read_runner.run_read(state, read_id, call=fake_call)["skipped"] == "already written"
    blocks = set(payloads[0]["scene"]) - {"absent", "precedents"}
    assert {"clock", "tape", "scale", "data_sources"} <= blocks, blocks


def _write_a_sealed_day(paths: forecast_paths.ForecastPaths, day: str, slots: tuple[str, ...]) -> None:
    for slot in slots:
        read_id = f"live:{day}T{slot}:12-04:00"
        base = {"next_30_minutes": {"up_pct": 19.3, "flat_pct": 59.6, "down_pct": 21.1}}
        jsonl_store.append_json_line(paths.payloads_file(day), {"line_type": "payload", "read_id": read_id, "trading_day": day,
                                                                "half_hour_slot_et": slot, "status": "built", "origin": "live",
                                                                "base_rate_shown_to_claude": base, "scene": {}}, fsync=False)
        final = {"line_type": "final_forecast", "read_id": read_id, "test_variant": "none", "status": "ok", "trading_day": day,
                 "any_answer_has_valid_reason": True,
                 "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 10, "flat_pct": 75, "down_pct": 15,
                                                  "is_within_3_pct_points_of_base_rate": False,
                                                  "is_leaning_same_way_as_last_30_minutes_move_vs_base_rate": True,
                                                  "largest_gap_between_answers_pct_points": 4}}}
        jsonl_store.append_json_line(paths.reads_file(day), {"line_type": "claude_answer", "read_id": read_id, "test_variant": "none",
                                                             "call_stats": {"cost_usd": 0.1}}, fsync=False)
        jsonl_store.append_json_line(paths.reads_file(day), final, fsync=False)
        jsonl_store.append_json_line(paths.outcomes_file(day), {
            "line_type": "outcome", "read_id": read_id, "horizon": "next_30_minutes", "trading_day": day, "half_hour_slot_et": slot,
            "read_source": "live", "finalize_attempt_number": 1,
            "result": {"status": "final", "direction": "flat", "size_bucket": "flat_within_1_flat_edge", "graded_move_flat_edges": 0.2},
            "comparison_forecasts": {"base_rate_shown_to_claude": base["next_30_minutes"],
                                     "existing_system_main_forecast": {"up_pct": 23.6, "flat_pct": 61.6, "down_pct": 14.8}}}, fsync=False)


def test_scores_and_the_scorecard_pair_claude_with_each_reference_on_the_same_reads(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    for day in ("2026-10-06", "2026-10-07", "2026-10-08"):
        _write_a_sealed_day(paths, day, ("10:00", "14:30"))
    rows = scoring.collect_score_rows(paths)
    claude = [r for r in rows if r["forecaster"] == "claude"]
    assert len(claude) == 6 and all(r["happened"] == "flat" and r["is_right"] for r in claude)
    assert {r["forecaster"] for r in rows} == {"claude", "claude_answer_1", "base_rate_shown_to_claude", "existing_system_main_forecast"}
    assert all(r["scored_as_base_rate"] for r in rows if r["forecaster"] == "claude_answer_1")   # no checked answer: scored as the base rate
    assert scoring.write_scores(paths, rows) == len(rows)
    card = scorecard.write_scorecard(paths, scoring.read_scores(paths))
    h30 = card["horizons"]["next_30_minutes"]
    assert card["verdict"] == "Too early" and h30["reads"] == 6 and h30["claude_right_pct"] == 100.0
    assert h30["skill_vs"]["base_rate_shown_to_claude"]["skill_pct"] > 0           # 75% flat beats 59.6% flat when flat happens
    assert h30["skill_vs"]["existing_system_main_forecast"]["reads"] == 6
    assert card["watchdogs"]["leaned_with_last_30_minutes_pct"] == 100.0 and card["watchdogs"]["cost_usd_total"] == pytest.approx(0.6)
    assert len(card["daily_skill_vs_base_rate"]) == 3


def test_sealing_is_refused_before_the_close_plus_75_minutes(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    early = datetime(2026, 10, 9, 16, 30, tzinfo=ET)
    assert nightly.seal_day(state_dir, paths, "2026-10-09", now=early)["sealed"] == 0
    assert "refused" in nightly.seal_day(state_dir, paths, "2026-10-09", now=early)["why"]


@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_the_night_seals_a_real_read_and_rebuilds_the_library(tmp_path):
    real = station_state_dir()
    state = tmp_path / "state"
    state.mkdir()
    for name in ("reversion", "spx_jev", "lob_flow", "siege", "dated_gex"):
        (state / name).symlink_to(real / name)
    read_id = "live:2026-10-09T14:30:12.458122-04:00"
    read_runner.run_read(state, read_id, call=fake_call)
    paths = forecast_paths.ForecastPaths(state)
    late = datetime(2026, 10, 9, 17, 30, tzinfo=ET)
    sealed = nightly.seal_day(state, paths, "2026-10-09", now=late)
    assert sealed["sealed"] == 1
    outcomes = jsonl_store.read_json_lines(paths.outcomes_file("2026-10-09"))
    by_h = {o["horizon"]: o for o in outcomes}
    assert by_h["next_30_minutes"]["result"]["direction"] == "flat" and by_h["next_30_minutes"]["result"]["flat_edge_points"] == 2.0
    assert by_h["next_30_minutes"]["existing_system_result_check"]["is_same_direction"] is True
    assert "existing_system_main_forecast" in by_h["next_30_minutes"]["comparison_forecasts"]
    results = nightly.run_nightly(state, "2026-10-09", now=late)
    assert results["library"]["rows"] >= 1 and results["scores"]["rows"] >= 3 and "error" not in results["scorecard"]
