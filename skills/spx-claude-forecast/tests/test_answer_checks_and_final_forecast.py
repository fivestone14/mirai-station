"""Code's checks on Claude's answer, and the final forecast averaged from two answers."""
from __future__ import annotations

from spx_claude_forecast import answer_checks, final_forecast, rulebook

SCENE = {"tape": {"m30_sig": {"v": 0.03}, "range_pos": 0.95}, "flow": {"tilt_15m": {"v": 0.05}},
         "absent": [{"path": "flow.ofi_1m", "why": "not_recorded"}]}
CARDS = {"pd8d7b": "seed:2026-10-05T14:30", "p0ee3a": "seed:2026-09-25T14:00"}
HORIZONS = ["next_30_minutes", "to_close"]


def good_answer() -> dict:
    return {"similar_precedents": [{"precedent_id": "pd8d7b", "weight_pct_of_picked_precedents": 60, "similar_on": ["range_pos"],
                                    "differs_most_on": "gap_sig"},
                                   {"precedent_id": "p0ee3a", "weight_pct_of_picked_precedents": 40, "similar_on": [], "differs_most_on": "m30"}],
            "reasons": [{"input_field_path": "tape.m30_sig.v", "pushes_toward": "smaller_move", "horizon": "next_30_minutes"},
                        {"input_field_path": "tape.range_pos", "pushes_toward": "up", "horizon": "all"}],
            "strongest_reason_against_lean": {"input_field_path": "flow.tilt_15m.v", "pushes_toward": "down", "horizon": "all"},
            "forecast": {"next_30_minutes": {"up_pct": 10, "flat_pct": 70, "down_pct": 20,
                                             "up_by_size_pct": {"up_1_to_2_flat_edges": 7, "up_2_to_3_flat_edges": 2, "up_over_3_flat_edges": 1},
                                             "down_by_size_pct": {"down_1_to_2_flat_edges": 14, "down_2_to_3_flat_edges": 4, "down_over_3_flat_edges": 2}},
                         "to_close": {"up_pct": 8, "flat_pct": 72, "down_pct": 20,
                                      "up_by_size_pct": {"up_1_to_2_flat_edges": 7, "up_2_to_3_flat_edges": 1, "up_over_3_flat_edges": 0},
                                      "down_by_size_pct": {"down_1_to_2_flat_edges": 14, "down_2_to_3_flat_edges": 4, "down_over_3_flat_edges": 2}}}}


def test_a_clean_answer_passes_whole_and_builds_the_seven_buckets():
    checked = answer_checks.check_answer(good_answer(), SCENE, HORIZONS, CARDS)
    h30 = checked.checked_forecast["next_30_minutes"]
    assert h30["status"] == "ok" and (h30["up_pct"], h30["flat_pct"], h30["down_pct"]) == (10, 70, 20)
    assert h30["size_buckets_pct"]["flat_within_1_flat_edge"] == 70 and sum(h30["size_buckets_pct"].values()) == 100
    assert checked.answer_checks["removed_invalid_items"] == [] and checked.answer_checks["valid_reason_count"] == 2
    assert checked.checked_similar_precedents[0]["read_id"] == "seed:2026-10-05T14:30"
    assert checked.checked_strongest_reason_against_lean["input_field_path"] == "flow.tilt_15m.v"
    assert checked.is_grounded


def test_shares_off_by_two_are_rescaled_and_off_by_more_rejected():
    answer = good_answer()
    answer["forecast"]["next_30_minutes"].update({"up_pct": 11, "flat_pct": 70, "down_pct": 21})   # 102
    answer["forecast"]["to_close"].update({"up_pct": 30, "flat_pct": 30, "down_pct": 30})           # 90
    checked = answer_checks.check_answer(answer, SCENE, HORIZONS, CARDS)
    h30 = checked.checked_forecast["next_30_minutes"]
    assert h30["status"] == "ok" and h30["up_pct"] + h30["flat_pct"] + h30["down_pct"] == 100
    assert checked.answer_checks["horizons_rescaled_to_sum_100"] == ["next_30_minutes"]
    assert checked.checked_forecast["to_close"]["status"] == "rejected"
    assert checked.answer_checks["horizons_rejected_sum_far_from_100"] == ["to_close"]


def test_a_size_split_off_by_one_is_fixed_on_the_largest_bucket_and_off_by_more_is_dropped():
    answer = good_answer()
    answer["forecast"]["next_30_minutes"]["up_by_size_pct"]["up_1_to_2_flat_edges"] = 6         # sums to 9, up is 10
    answer["forecast"]["to_close"]["down_by_size_pct"]["down_1_to_2_flat_edges"] = 5            # sums to 11, down is 20
    checked = answer_checks.check_answer(answer, SCENE, HORIZONS, CARDS)
    assert checked.checked_forecast["next_30_minutes"]["size_buckets_pct"]["up_1_to_2_flat_edges"] == 7
    assert checked.answer_checks["horizons_size_split_fixed_off_by_1"] == ["next_30_minutes"]
    assert "size_buckets_pct" not in checked.checked_forecast["to_close"]
    assert checked.answer_checks["horizons_size_split_discarded"] == ["to_close"]
    assert checked.checked_forecast["to_close"]["status"] == "ok"


def test_bad_precedents_and_ungrounded_reasons_are_removed_never_rewritten():
    answer = good_answer()
    answer["similar_precedents"].append({"precedent_id": "now", "weight_pct_of_picked_precedents": 10})
    answer["reasons"].append({"input_field_path": "flow.ofi_1m", "pushes_toward": "up", "horizon": "all"})          # absent
    answer["reasons"].append({"input_field_path": "tape.nothing", "pushes_toward": "up", "horizon": "all"})         # not in scene
    answer["reasons"].append({"input_field_path": "tape.range_pos", "pushes_toward": "sideways", "horizon": "all"})  # bad value
    checked = answer_checks.check_answer(answer, SCENE, HORIZONS, CARDS)
    removed = {r["answer_path"]: r["removed_because"] for r in checked.answer_checks["removed_invalid_items"]}
    assert removed["similar_precedents.2"].startswith("not a shown card")
    assert removed["reasons.2"] == "path is listed in absent" and removed["reasons.3"] == "path is not in the scene"
    assert "allowed value" in removed["reasons.4"]
    assert checked.answer_checks["valid_reason_count"] == 2 and len(checked.checked_similar_precedents) == 2


def test_no_answer_rejects_every_horizon_and_is_not_grounded():
    checked = answer_checks.check_answer(None, SCENE, HORIZONS, CARDS)
    assert all(h["status"] == "rejected" for h in checked.checked_forecast.values()) and not checked.is_grounded


def _checked_line(answer_id: str, up: int, flat: int, down: int, reasons: int = 2) -> dict:
    return {"answer_id": answer_id, "answer_checks": {"valid_reason_count": reasons},
            "checked_forecast": {"next_30_minutes": {"status": "ok", "up_pct": up, "flat_pct": flat, "down_pct": down,
                                                     "size_buckets_pct": {b: (flat if b == "flat_within_1_flat_edge" else 0) for b in
                                                                          final_forecast.SIZE_BUCKETS}},
                                 "to_close": {"status": "rejected", "why": "shares sum to 90"}}}


def test_the_final_forecast_averages_the_answers_and_measures_the_gaps():
    base = {"next_30_minutes": {"up_pct": 19.3, "flat_pct": 59.6, "down_pct": 21.1}}
    final = final_forecast.final_forecast_for([_checked_line("a#answer_1", 10, 70, 20), _checked_line("a#answer_2", 12, 66, 22)],
                                              HORIZONS, base, last_30_minutes_sig=0.03)
    h30 = final["forecast"]["next_30_minutes"]
    assert (h30["up_pct"], h30["flat_pct"], h30["down_pct"]) == (11, 68, 21)
    assert h30["largest_gap_between_answers_pct_points"] == 4 and h30["answer_2"]["flat_pct"] == 66
    assert h30["largest_gap_from_base_rate_pct_points"] == 8.4 and h30["is_within_3_pct_points_of_base_rate"] is False
    assert h30["is_leaning_same_way_as_last_30_minutes_move_vs_base_rate"] is False       # tilt: (11-19.3)-(21-21.1) < 0, m30 > 0
    assert final["forecast"]["to_close"] == {"status": "no_usable_answer"}
    assert final["usable_answer_count"] == 2 and final["any_answer_has_valid_reason"] is True


def test_the_rulebook_carries_no_digits_and_every_contract_name():
    text = rulebook.rulebook_text()
    import re
    assert re.findall(r"(?<![A-Za-z_0-9-])\d+(?![A-Za-z_0-9])", text.replace("S&P 500", "S&P")) == []   # digits only inside field names
    for name in ("similar_precedents", "weight_pct_of_picked_precedents", "strongest_reason_against_lean", "up_by_size_pct",
                 "down_by_size_pct", "flat_edge_sig", "direction_order_asked", "outcome_flat_edges", "ago_sessions"):
        assert name in text, name
    assert len(rulebook.rulebook_sha()) == 16
