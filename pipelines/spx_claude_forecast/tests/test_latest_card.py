"""latest.json, the phone's card: the final forecast with the reasons in plain words and the scorecard's verdict."""
from __future__ import annotations

from types import SimpleNamespace

from spx_claude_forecast import latest_card, reason_words

FINAL = {"line_type": "final_forecast", "read_id": "live:2026-10-12T14:30:12-04:00", "status": "ok", "trading_day": "2026-10-12",
         "half_hour_slot_et": "14:30", "usable_answer_count": 2, "prompt_and_model_version": "cr-1",
         "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 30.0, "flat_pct": 37.0, "down_pct": 33.0,
                                          "base_rate_shown_to_claude": {"up_pct": 20.0, "flat_pct": 60.0, "down_pct": 20.0}}},
         "written_at": "2026-10-12T14:31:40-04:00"}
BUILT = SimpleNamespace(line={"cut_at": "2026-10-12T14:30:12-04:00"}, claude_input_sha256="sha256:0123456789abcdef")


def _answer(reasons, against=None):
    return {"line_type": "claude_answer", "checked_reasons": reasons, "checked_strongest_reason_against_lean": against}


def test_a_reason_gets_its_words_beside_its_codes():
    reason = {"input_field_path": "tape.rv30_vs_clock_x", "pushes_toward": "smaller_move", "horizon": "next_30_minutes"}
    assert reason_words.reason_in_words(reason) == {
        **reason, "input_field_path_in_words": "the last 30 minutes' realized move against the usual for this time of day",
        "pushes_toward_in_words": "points to a smaller move", "horizon_in_words": "next 30 min"}
    assert reason_words.reason_in_words(None) is None


def test_a_path_the_words_do_not_list_is_still_readable():
    assert reason_words.field_words("code.TREND-05") == "the station's question TREND-05"
    assert reason_words.field_words("tape.some_new_measure_sig") == "the tape: some new measure sig"
    assert reason_words.field_words("overnight") == "overnight"


def test_the_card_carries_the_forecast_the_distinct_reasons_in_words_the_reason_against_and_the_record():
    reason_a = {"input_field_path": "clock.min_to_close", "pushes_toward": "smaller_move", "horizon": "all"}
    reason_b = {"input_field_path": "flow.lean_30m_vs_usual", "pushes_toward": "up", "horizon": "next_30_minutes"}
    against = {"input_field_path": "clock.phase", "pushes_toward": "down", "horizon": "all"}
    answers = [_answer([reason_a, reason_b], None), _answer([reason_a, against], against)]     # the repeat is kept once
    scorecard = {"verdict": "Scored", "graded_reads": 120, "built_at": "2026-12-01T22:15:00+00:00",
                 "horizons": {"next_30_minutes": {"claude_right_pct": 54.2, "reads": 120,
                                                  "skill_vs": {"base_rate_shown_to_claude": {"skill_pct": 4.1, "reads": 120}}}}}
    card = latest_card.latest_card_for(BUILT, FINAL, answers, scorecard)
    assert card["read_id"] == FINAL["read_id"] and card["read_at"] == "2026-10-12T14:30:12-04:00"
    assert card["forecast"] is FINAL["forecast"] and card["usable_answer_count"] == 2 and card["status"] == "ok"
    assert [r["input_field_path"] for r in card["reasons"]] == ["clock.min_to_close", "flow.lean_30m_vs_usual", "clock.phase"]
    assert card["reasons"][0]["input_field_path_in_words"] == "minutes to the close"
    assert card["reasons"][0]["pushes_toward_in_words"] == "points to a smaller move" and card["reasons"][0]["horizon_in_words"] == "every horizon"
    assert card["strongest_reason_against_lean"]["input_field_path_in_words"] == "the part of the session"
    assert card["record"] == {"verdict": "Scored", "graded_reads": 120, "scorecard_built_at": "2026-12-01T22:15:00+00:00",
                              "horizons": {"next_30_minutes": {"claude_right_pct": 54.2, "skill_vs_base_rate_pct": 4.1, "reads": 120}}}


def test_a_read_with_no_call_and_no_scorecard_still_writes_a_whole_card():
    card = latest_card.latest_card_for(BUILT, {**FINAL, "status": "paused", "usable_answer_count": 0}, [], None)
    assert card["status"] == "paused" and card["reasons"] == [] and card["strongest_reason_against_lean"] is None
    assert card["record"] == {"verdict": "Too early", "graded_reads": 0, "scorecard_built_at": None, "horizons": {}}


def test_at_most_four_reasons_are_shown():
    reasons = [{"input_field_path": f"tape.m{i}_sig", "pushes_toward": "up", "horizon": "all"} for i in range(6)]
    assert len(latest_card.unique_reasons_in_words([_answer(reasons)])) == latest_card.REASONS_SHOWN_MAX
