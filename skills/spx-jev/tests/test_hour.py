"""Steps 3 and 4: answers become sentences, the weights decide who speaks, the sums ride on top."""
from __future__ import annotations

import json
import re

from spx_jev.ask import PATH_RE, load_questions
from spx_jev.cuts import NEXT_30_FLAT_PCT
from spx_jev.flat_zone import band
from spx_jev.hour import (answer_sentences, hour_request, hour_summary, load_hour_doc, named_levels, one_sentence, unit_line, views_of,
                          zone_line)
from spx_jev.lane import LIVE, PREMARKET, TAPE
from spx_jev.weights import QuestionWeights

DOC = load_questions(LIVE.questions, LIVE.key)
BY_ID = {qid: q for g in DOC["groups"] for qid, q in g["questions"].items()}
UNIT = {"unit_points": 6.0, "unit_sigma": 0.08, "slices_used": 3, "source": "tape"}
ZONES = {"next_30": 5.25, "next_60": 8.25, "average_30": 3.11}
TAPE_ZONES = {"next_10": 2.52, "average_10": 1.56, "big": 5.34}


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


def test_the_sums_carry_their_flat_zones_in_points_and_the_base_rates():
    """Each end-price sum's text names its zone for this read in points, the context names every box's, and the base
    rates ride beside them; nothing names a sigma band."""
    req = hour_request({"leg_vs_day_side": "leg with day, JEV was 98% sure"}, ZONES)
    assert req["id"] == "hour" and req["state"]["context"]["symbol"] == "SPX"
    assert req["state"]["context"]["horizon"] == "the next 30 minutes, and the next 60 minutes"
    assert list(req["questions"]) == ["next_30", "next_60"]
    assert "within 5.25 points of where it is now" in req["questions"]["next_30"]["criteria"]["flat"]
    assert f"about {NEXT_30_FLAT_PCT}% of the time" in req["questions"]["next_30"]["criteria"]["flat"]
    assert "more than 8.25 points above" in req["questions"]["next_60"]["criteria"]["up"]
    assert req["state"]["context"]["flat_zone"] == zone_line(ZONES) == (
        "over the next 30 minutes flat is within 5.25 points of the price now either way; over the next 60 minutes flat is within "
        "8.25 points of the price now either way")
    assert "sigma" not in json.dumps(req["questions"]) and "{zone_points}" not in json.dumps(req["questions"])
    assert all(set(q) == {"type", "instructions", "criteria"} for q in req["questions"].values())
    pre = zone_line({"open_10": 4.0, "open_30": 7.0}, PREMARKET)
    assert pre == "flat is within 4.00 points of the settled open either way 10 minutes after it, and within 7.00 points of the settled open either way 30 minutes after it"


def test_the_lanes_sum_names_its_three_bands_in_points_and_the_unit_beside_them():
    assert band(TAPE_ZONES, TAPE) == {"flat_points": 2.52, "big_points": 5.34}
    line = unit_line(UNIT)
    assert line == "one tape unit is 6.0 points"
    ranked = unit_line({**UNIT, "rank": {"band": "top third", "higher_than": 15, "of": 20}})
    assert ranked == "one tape unit is 6.0 points, in the top third for this minute, wider than 15 of 20 prior sessions"
    req = hour_request({"q": "a sentence"}, TAPE_ZONES, lane=TAPE, ruler=UNIT)
    assert req["state"]["context"]["unit"] == line and list(req["questions"]) == ["next_10"]
    assert req["state"]["context"]["flat_zone"] == ("over the next 10 minutes flat is within 2.52 points of the price now either way; "
                                                    "small is 2.52 to 5.34 points; big is more than 5.34 points")
    c = req["questions"]["next_10"]["criteria"]
    assert list(c) == ["down_big", "down_small", "flat", "up_small", "up_big", "unsure"]
    assert c["flat"].startswith("price ends within 2.52 points of where it is now, either way")
    assert c["up_small"].startswith("price ends above where it is now by more than 2.52 points and no more than 5.34 points")
    assert c["down_big"].startswith("price ends more than 5.34 points below where it is now")


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


def test_a_view_whose_likeliest_mass_ties_with_unsure_picks_unsure():
    """The 09:35 tape read of 2026-09-28: flat 0.45 and unsure 0.45. JEV picked unsure, so the direction view is no
    committed flat call and can never count as a hit."""
    v = views_of({"down_big": 0.0, "down_small": 0.05, "flat": 0.45, "up_small": 0.05, "up_big": 0.0, "unsure": 0.45})
    assert v["direction"]["pick"] == "unsure"
    assert v["size"]["pick"] == "small"                             # 0.55 against 0.45: no tie, a committed size
    tied = views_of({"down_big": 0.1, "down_small": 0.2, "flat": 0.1, "up_small": 0.2, "up_big": 0.1, "unsure": 0.3})
    assert tied["direction"]["pick"] == "unsure"                    # up 0.3, down 0.3 and unsure 0.3, summed in floats


# ---- the cut-over (Phase 4): the sums ride on the judgment answers and the code's measurements

def test_the_cut_over_sentences_are_the_judgment_answers_then_the_code_answers_in_the_catalogs_order():
    from spx_jev.hour import code_sentence, cut_over_sentences
    from spx_jev.mirai_prediction.code_features import catalog_by_id
    fresh = {"news_reaction": {"pick": "shrugging_off", "probabilities": {"shrugging_off": 0.7}},
             "price_move_5way": {"pick": "small_up", "probabilities": {"small_up": 0.6}},
             "push_blowoff_or_fresh": {"pick": None}}
    code = {"TREND-10": "up held", "TREND-01": "big up", "LEVELS-01": None}
    sentences, left_out = cut_over_sentences(DOC, fresh, code)
    assert list(sentences) == ["news_reaction", "code:TREND-01", "code:TREND-10"]
    assert sentences["news_reaction"] == f"{BY_ID['news_reaction']['ask']} shrugging off, JEV was 70% sure"
    assert sentences["code:TREND-01"] == code_sentence(catalog_by_id()["TREND-01"], "big up") == "Last 30-minute move, signed: big up"
    assert left_out == {"price_move_5way": "not a judgment question: not an input from the cut-over", "push_blowoff_or_fresh": "no pick in the answer"}
    assert cut_over_sentences(DOC, {}, None) == ({}, {})
