"""Steps 3 and 4: answers become sentences, the weights decide who speaks, the sums ride on top."""
from __future__ import annotations

import re

from spx_jev.ask import PATH_RE, load_questions
from spx_jev.cuts import NEXT_30_FLAT_BAND_SIGMA, NEXT_30_FLAT_PCT, NEXT_60_FLAT_BAND_SIGMA, TAPE_BIG_UNITS, TAPE_FLAT_UNITS
from spx_jev.hour import (answer_sentences, band_of, hour_request, hour_summary, load_hour_doc, named_levels, one_sentence, unit_line,
                          views_of)
from spx_jev.lane import LIVE, TAPE
from spx_jev.weights import QuestionWeights

DOC = load_questions(LIVE.questions, LIVE.key)
BY_ID = {qid: q for g in DOC["groups"] for qid, q in g["questions"].items()}
UNIT = {"unit_points": 6.0, "unit_sigma": 0.08, "slices_used": 3, "source": "tape"}


def test_one_sentence_carries_the_ask_the_pick_and_how_sure():
    q = BY_ID["leg_vs_day_side"]
    s = one_sentence(q, {"pick": "leg_with_day", "probabilities": {"leg_with_day": 0.98, "quiet": 0.02}})
    assert s == "Did the latest leg run with or against the day's side of yesterday's close? leg with day, JEV was 98% sure"
    held = one_sentence(BY_ID["tick_lean_vs_usual"], {"pick": "buying", "probabilities": {"buying": 0.8}, "held_from": "2026-09-18T11:02:14-04:00"})
    assert held.endswith("(held since 11:02 ET, not re-asked)")


def test_every_question_asks_in_one_short_plain_sentence():
    """What the sums read an answer after: one question, no label path, no option name, its window filled."""
    label_path = re.compile(r"\b[a-z_]+\.[a-z_0-9]+\b")
    for lane in (LIVE, TAPE):
        for g in load_questions(lane.questions, lane.key)["groups"]:
            for qid, q in g["questions"].items():
                ask = q["ask"]
                assert ask.endswith("?") and ask.count("?") == 1 and len(ask.split()) <= 20, (qid, ask)
                assert not PATH_RE.search(ask) and not label_path.search(ask) and "{" not in ask, (qid, ask)
                assert not any("_" in o and o in ask for o in q["options"]), (qid, ask)


def test_a_score_answer_is_named_by_its_levels_and_read_like_a_choice():
    """JEV keys a score's probabilities by level number, with its criteria words as a legend; the level
    names come from the question's options, in order, and the card's entry then carries them."""
    from spx_jev.service import answer_entry
    q = BY_ID["price_move_5way"]
    raw = {"type": "score", "score": 2.9, "confidence": 0.4, "legend": {str(n): c for n, c in enumerate(q["criteria"])},
           "probabilities": {"0": 0.02, "1": 0.08, "2": 0.2, "3": 0.6, "4": 0.1}}
    named = named_levels(q, raw)
    assert named["probabilities"] == {"strong_down": 0.02, "down": 0.08, "nowhere": 0.2, "up": 0.6, "strong_up": 0.1}
    assert "legend" not in named and named["score"] == 2.9
    entry = answer_entry(named)
    assert entry["pick"] == "up" and entry["probabilities"] == named["probabilities"]
    assert one_sentence(q, entry).endswith("? up, JEV was 60% sure")
    assert named_levels(q, {**raw, "probabilities": [0.1, 0.2, 0.4, 0.2, 0.1]})["probabilities"]["nowhere"] == 0.4
    assert named_levels(q, {**raw, "probabilities": {"7": 1.0}}) == {**raw, "probabilities": {"7": 1.0}}   # no such level
    choice = {"type": "choice", "choice": "quiet", "probabilities": {"quiet": 1.0}}
    assert named_levels(BY_ID["leg_vs_day_side"], choice) is choice


def test_shadow_and_low_weight_answers_are_left_out_with_a_reason():
    answered = {"leg_vs_day_side": {"pick": "leg_with_day", "probabilities": {"leg_with_day": 1.0}},
                "flow_vs_price": {"pick": "below", "probabilities": {"below": 0.4}},
                "tick_lean_vs_usual": {"pick": "buying", "probabilities": {"buying": 0.9}},
                "nobody": {"pick": "x"}}
    sentences, left_out = answer_sentences(DOC, answered, QuestionWeights({"tick_lean_vs_usual": {"weight": 0.2}}))
    assert list(sentences) == ["leg_vs_day_side"]
    assert left_out["flow_vs_price"].startswith("a shadow forecast") and "0.20" in left_out["tick_lean_vs_usual"]
    assert left_out["nobody"] == "not a question in the doc"
    assert list(answer_sentences(DOC, answered)[0]) == ["leg_vs_day_side", "tick_lean_vs_usual"]   # neutral: nobody is weighed out


def test_the_sums_carry_the_spx_bands_and_base_rates():
    req = hour_request({"leg_vs_day_side": "leg with day, JEV was 98% sure"})
    assert req["id"] == "hour" and req["state"]["context"]["symbol"] == "SPX"
    assert req["state"]["context"]["horizon"] == "the next 30 minutes, and the next 60 minutes"
    assert list(req["questions"]) == ["next_30", "next_60"]
    assert f"within {NEXT_30_FLAT_BAND_SIGMA} sigma" in req["questions"]["next_30"]["criteria"]["flat"]
    assert f"about {NEXT_30_FLAT_PCT}% of the time" in req["questions"]["next_30"]["criteria"]["flat"]
    assert f"within {NEXT_60_FLAT_BAND_SIGMA} sigma" in req["questions"]["next_60"]["criteria"]["flat"]
    assert all(set(q) == {"type", "instructions", "criteria"} for q in req["questions"].values())


def test_the_lanes_sum_is_priced_in_points_from_the_unit():
    band = band_of(UNIT)
    assert band == {"flat_points": round(TAPE_FLAT_UNITS * 6, 2), "big_points": round(TAPE_BIG_UNITS * 6, 2),
                    "flat_units": TAPE_FLAT_UNITS, "big_units": TAPE_BIG_UNITS}
    line = unit_line(UNIT, band)
    assert line.startswith(f"one tape unit is 6.0 points; flat is within {TAPE_FLAT_UNITS * 6:.1f} points either way ({TAPE_FLAT_UNITS:g} of a unit)")
    ranked = unit_line({**UNIT, "rank": {"band": "top third", "higher_than": 15, "of": 20}}, band)
    assert ", in the top third for this minute, wider than 15 of 20 prior sessions;" in ranked
    req = hour_request({"q": "a sentence"}, lane=TAPE, ruler=UNIT)
    assert req["state"]["context"]["unit"] == line and list(req["questions"]) == ["next_10"]
    assert list(load_hour_doc(lane=TAPE)["questions"]["next_10"]["criteria"]) == ["down_big", "down_small", "flat", "up_small", "up_big", "unsure"]


def test_the_summary_keeps_the_primary_on_top_and_reads_a_five_way_sum_three_ways():
    reply = {"model": "jev-1", "answers": {
        "next_30": {"type": "choice", "choice": "flat", "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.15, "unsure": 0.05}},
        "next_60": {"type": "choice", "choice": "up", "probabilities": {"up": 0.4, "flat": 0.3, "down": 0.2, "unsure": 0.1}}}}
    s = hour_summary(reply)
    assert s["primary"] == "next_30" and s["pick"] == "flat" and s["by"]["next_60"]["pick"] == "up"
    assert hour_summary({"error": "HTTP 500"}) == {"error": "HTTP 500"} and hour_summary(None) is None
    p = {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.2, "up_big": 0.15, "unsure": 0.2}
    v = views_of(p)
    assert v["direction"] == {"pick": "up", "probabilities": {"up": 0.35, "flat": 0.3, "down": 0.15, "unsure": 0.2}}
    assert v["size"] == {"pick": "small", "probabilities": {"big": 0.2, "small": 0.6, "unsure": 0.2}}
