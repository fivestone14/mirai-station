"""The nightly test variants: the cards removed, their outcomes shuffled, random cards; asked once per read, scored beside production."""
from __future__ import annotations

import json

from test_read_runner_and_nightly import fake_call

from spx_claude_forecast import jsonl_store, precedents, scorecard, scoring, test_variants, paths as forecast_paths
from spx_claude_forecast.rulebook import TEST_VARIANT_NONE

DAY = "2026-10-12"
NOW = {"gap_sig": 0.2, "price_minus_close_sig": 0.3, "last_30_minutes_sig": 0.1, "range_sig": 0.5, "range_position": 0.6,
       "price_minus_vwap_sig": 0.0, "vix_term_ratio": 0.85}
HORIZON = {"window_minutes": 30, "graded_move_measured_to": "window_average_price", "flat_edge_sig": 0.03}


def _slot_minute(slot: str) -> int:
    return int(slot[:2]) * 60 + int(slot[3:])


def _library_rows(slot: str = "14:30", sessions: int = 12) -> list[dict]:
    """Twelve eligible past reads at one slot, every everyday item and every outcome different."""
    rows = []
    for i in range(sessions):
        day = f"2026-09-{10 + i:02d}"
        rows.append({"read_id": f"seed:{day}T{slot}:00-04:00", "trading_day": day, "half_hour_slot_et": slot, "origin": "seed",
                     "slot_minute_of_day": _slot_minute(slot), "is_eligible_precedent": True,
                     "gap_sig": 0.1 * i - 0.5, "price_minus_close_sig": 0.2 * i - 1, "last_30_minutes_sig": 0.05 * i,
                     "range_sig": 0.3 + 0.05 * i, "range_position": (i % 10) / 10, "price_minus_vwap_sig": 0.1 * i - 0.6,
                     "vix_term_ratio": 0.8 + 0.01 * i,
                     "next_30_minutes_status": "final", "next_30_minutes_move_flat_edges": 0.5 * i - 2,
                     "next_60_minutes_status": "final", "next_60_minutes_move_flat_edges": 0.4 * i - 2,
                     "to_close_status": "final", "to_close_move_flat_edges": 0.3 * i - 1})
    return rows


def _payload_line(rows: list[dict], slot: str = "14:30") -> dict:
    """A built payload line whose cards the picker chose from ``rows``, as the live runner writes one."""
    read_id = f"live:{DAY}T{slot}:10-04:00"
    history = precedents.history_for_read(rows, NOW, _slot_minute(slot), DAY)
    scene = {"clock": {"min_after_open": 300, "min_to_close": 90, "phase": "afternoon"},
             "tape": {"m30_sig": 0.1, "range_sig": 0.5},
             "precedents": {"rule": history.picker.get("rule"), "candidate_sessions": history.picker.get("candidate_sessions"),
                            "shown": len(history.cards), "order": "by_id", "now": NOW, "cards": sorted(history.cards, key=lambda c: c["id"])},
             "absent": []}
    ask = {"direction_order_asked": ["up_pct", "flat_pct", "down_pct"],
           "horizons": {"next_30_minutes": HORIZON, "next_60_minutes": {**HORIZON, "window_minutes": 60}}}
    return {"line_type": "payload", "read_id": read_id, "claude_input_sha256": jsonl_store.canonical_json_sha256(scene),
            "trading_day": DAY, "half_hour_slot_et": slot, "status": "built", "scene": scene, "ask": ask,
            "asked_horizons": list(ask["horizons"]), "precedent_set": history.precedent_set,
            "base_rate_shown_to_claude": {h: {"up_pct": 20.0, "flat_pct": 60.0, "down_pct": 20.0} for h in ask["horizons"]}}


def _final_line(payload: dict) -> dict:
    return {"line_type": "final_forecast", "read_id": payload["read_id"], "test_variant": TEST_VARIANT_NONE, "status": "ok",
            "usable_answer_count": 2, "trading_day": DAY, "half_hour_slot_et": payload["half_hour_slot_et"]}


def test_each_variant_changes_only_the_precedents_block_and_is_seeded_by_the_production_scene():
    rows = _library_rows()
    payload = _payload_line(rows)
    production_cards = payload["scene"]["precedents"]["cards"]
    assert len(production_cards) == precedents.PRECEDENT_COUNT
    none, _ = test_variants.variant_payload(payload, test_variants.NO_PRECEDENTS_SHOWN, rows)
    assert none.scene["precedents"]["cards"] == [] and none.scene["precedents"]["shown"] == 0 and none.card_ids == {}
    assert {"path": "precedents.cards", "why": "none_shown"} in none.scene["absent"]
    shuffled, _ = test_variants.variant_payload(payload, test_variants.PRECEDENT_OUTCOMES_SHUFFLED, rows)
    by_id = {c["id"]: c for c in production_cards}
    dealt = shuffled.scene["precedents"]["cards"]
    assert [c["id"] for c in dealt] == [c["id"] for c in production_cards]                       # the cards and their items stay
    assert all(c["outcome_flat_edges"] != by_id[c["id"]]["outcome_flat_edges"] for c in dealt)   # no card keeps its own outcome
    assert sorted(json.dumps(c["outcome_flat_edges"], sort_keys=True) for c in dealt) == \
        sorted(json.dumps(c["outcome_flat_edges"], sort_keys=True) for c in production_cards)      # the same outcomes, dealt around
    assert shuffled.card_ids == {p["card_id"]: p["read_id"] for p in payload["precedent_set"]}
    drawn, _ = test_variants.variant_payload(payload, test_variants.RANDOM_PRECEDENTS_SHOWN, rows)
    assert len(drawn.scene["precedents"]["cards"]) == precedents.PRECEDENT_COUNT and len(drawn.card_ids) == precedents.PRECEDENT_COUNT
    assert len({c["id"] for c in drawn.scene["precedents"]["cards"]} & {c["id"] for c in production_cards}) < precedents.PRECEDENT_COUNT
    assert {k: v for k, v in drawn.scene["precedents"].items() if k not in ("cards", "shown")} == \
        {k: v for k, v in payload["scene"]["precedents"].items() if k not in ("cards", "shown")}   # the draw is not announced to Claude
    for variant in (none, shuffled, drawn):
        untouched = {k: v for k, v in payload["scene"].items() if k not in ("precedents", "absent")}
        assert {k: v for k, v in variant.scene.items() if k not in ("precedents", "absent")} == untouched
        assert variant.claude_input_sha256 == payload["claude_input_sha256"]                      # the join key stays production's
        assert variant.test_variant_claude_input_sha256 != payload["claude_input_sha256"]
        assert list(variant.prompts) == ["answer_1"] and variant.stamp()["test_variant_version"] == test_variants.TEST_VARIANT_VERSION
        assert variant.asked_horizons == ["next_30_minutes", "next_60_minutes"]
    again, _ = test_variants.variant_payload(payload, test_variants.RANDOM_PRECEDENTS_SHOWN, rows)
    assert again.test_variant_claude_input_sha256 == drawn.test_variant_claude_input_sha256       # a night can be replayed


def test_a_variant_that_would_send_the_production_scene_unchanged_is_skipped():
    rows = _library_rows()
    payload = _payload_line(rows)
    payload["scene"]["precedents"]["cards"] = []
    payload["precedent_set"] = []
    assert test_variants.variant_payload(payload, test_variants.NO_PRECEDENTS_SHOWN, rows) == (None, "the production read showed no cards")
    assert test_variants.variant_precedents(payload, test_variants.PRECEDENT_OUTCOMES_SHUFFLED, rows)[2] == "fewer than two cards with an outcome to shuffle"
    assert test_variants.variant_precedents(payload, test_variants.RANDOM_PRECEDENTS_SHOWN, rows[:3])[2].startswith("fewer than 5 sessions")


def test_a_night_asks_every_variant_on_the_first_middle_and_last_read_once_and_keeps_to_its_cap(tmp_path):
    paths = forecast_paths.ensure_folders(tmp_path / "state")
    slots = ("09:30", "11:00", "13:00", "14:30", "15:30")
    rows = [row for slot in slots for row in _library_rows(slot)]
    for slot in slots:
        payload = _payload_line(rows, slot)
        jsonl_store.append_json_line(paths.payloads_file(DAY), payload, fsync=False)
        jsonl_store.append_json_line(paths.reads_file(DAY), _final_line(payload), fsync=False)
    counts = test_variants.run_test_variants(paths, DAY, rows, call=fake_call)
    assert counts["reads"] == 3 and counts["asked"] == 9 and counts["errors"] == [] and counts["skipped"] == {}
    arms = jsonl_store.read_json_lines(paths.arms_file(DAY))
    answers = [line for line in arms if line["line_type"] == "claude_answer"]
    forecasts = [line for line in arms if line["line_type"] == "test_variant_forecast"]
    assert len(answers) == 9 and len(forecasts) == 9
    assert {f["half_hour_slot_et"] for f in forecasts} == {"09:30", "13:00", "15:30"}
    assert {f["test_variant"] for f in forecasts} == set(test_variants.VARIANTS)
    for answer in answers:
        assert answer["answer_id"] == f"{answer['read_id']}#test:{answer['test_variant']}:answer_1"
        assert answer["test_variant_version"] == 1 and answer["test_variant_claude_input_sha256"] != answer["claude_input_sha256"]
        assert isinstance(answer["test_variant_precedents"]["cards"], list) and answer["checked_forecast"]["next_30_minutes"]["status"] == "ok"
    for forecast in forecasts:
        assert forecast["usable_answer_count"] == 1 and forecast["forecast"]["next_30_minutes"]["status"] == "ok"
        assert forecast["forecast"]["next_30_minutes"]["base_rate_shown_to_claude"]["flat_pct"] == 60.0
        assert forecast["answer_ids"] == [f"{forecast['read_id']}#test:{forecast['test_variant']}:answer_1"]
    assert test_variants.run_test_variants(paths, DAY, rows, call=fake_call)["asked"] == 0     # once per read and variant
    capped = forecast_paths.ensure_folders(tmp_path / "capped")
    jsonl_store.write_json_atomically(capped.control_file, {"max_test_variant_calls_per_night": 4})
    for slot in slots:
        payload = _payload_line(rows, slot)
        jsonl_store.append_json_line(capped.payloads_file(DAY), payload, fsync=False)
        jsonl_store.append_json_line(capped.reads_file(DAY), _final_line(payload), fsync=False)
    counts = test_variants.run_test_variants(capped, DAY, rows, call=fake_call)
    assert counts["asked"] == 4 and set(counts["skipped"].values()) == {"capped"}


def test_a_variants_forecast_is_scored_beside_production_and_never_counted_as_a_graded_read():
    read_id = f"live:{DAY}T14:30:10-04:00"
    outcome = {"read_id": read_id, "trading_day": DAY, "half_hour_slot_et": "14:30", "read_source": "live", "horizon": "next_30_minutes",
               "result": {"status": "final", "direction": "flat", "size_bucket": "flat_within_1_flat_edge"},
               "comparison_forecasts": {"base_rate_shown_to_claude": {"up_pct": 20, "flat_pct": 60, "down_pct": 20}}}
    production = {"line_type": "final_forecast", "read_id": read_id,
                  "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 25, "flat_pct": 50, "down_pct": 25}}}
    variant = {"line_type": "test_variant_forecast", "read_id": read_id, "test_variant": test_variants.NO_PRECEDENTS_SHOWN,
               "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 30, "flat_pct": 40, "down_pct": 30}}}
    rows = (scoring.score_rows_for_read([outcome], production, [])
            + scoring.score_rows_for_read([outcome], variant, [], test_variant=test_variants.NO_PRECEDENTS_SHOWN))
    variant_rows = [r for r in rows if r["test_variant"] == test_variants.NO_PRECEDENTS_SHOWN]
    assert {r["forecaster"] for r in variant_rows} == {"claude_final", "claude_answer_1"}      # the comparisons are scored once, on production
    assert any(r["forecaster"] == "base_rate_shown_to_claude" and r["test_variant"] == TEST_VARIANT_NONE for r in rows)
    card = scorecard.scorecard_from_rows(rows)
    assert card["graded_reads"] == 1 and card["horizons"]["next_30_minutes"]["reads"] == 1
    block = card["test_variants"][test_variants.NO_PRECEDENTS_SHOWN]["next_30_minutes"]
    assert block["reads"] == 1 and block["production_right_pct"] == 100.0 and block["variant_right_pct"] == 100.0
    assert block["production_skill_vs_variant_pct"] > 0                                       # 50% on flat beats 40% on flat
    assert card["checkpoints"] == {"at_graded_reads": [150, 300, 600], "reached": [], "next": 150}
    assert scorecard.checkpoints_for(310) == {"at_graded_reads": [150, 300, 600], "reached": [150, 300], "next": 600}
