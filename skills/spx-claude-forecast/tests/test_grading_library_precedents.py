"""Outcomes, the library rows built from them, the base rate and the precedent picker, all offline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spx_claude_forecast import grading, jsonl_store, library, precedents, paths as forecast_paths


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
    manifest = library.rebuild_library(paths)
    rows = library.read_library(paths)
    assert manifest["rows"] == len(rows) == 20 * 3
    live_rows = [r for r in rows if r["origin"] == "live"]
    assert len(live_rows) == 1 and live_rows[0]["gap_sig"] == 9.9 and live_rows[0]["next_30_minutes_direction"] == "up"
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
