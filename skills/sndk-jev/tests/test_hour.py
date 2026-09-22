"""Steps 3 and 4: answers become sentences, weights decide who speaks, two sums ride on top."""
from __future__ import annotations

from sndk_jev.ask import DEFAULT_QUESTIONS, load_questions
from sndk_jev.hour import HOUR_QIDS, PRIMARY, answer_sentences, hour_request, hour_summary, load_hour_doc, one_sentence

DOC = load_questions(DEFAULT_QUESTIONS)
BY_ID = {qid: q for g in DOC["groups"] for qid, q in g["questions"].items()}


def test_one_sentence_carries_the_ask_the_pick_and_how_sure():
    q = BY_ID["price_recent_direction"]
    s = one_sentence(q, {"pick": "rising", "probabilities": {"rising": 0.98, "falling": 0.01, "going_nowhere": 0.01}})
    assert s == "Over the last 30 minutes, did price rise, fall, or go nowhere? rising, JEV was 98% sure"
    q = BY_ID["prior_level_tested"]
    assert one_sentence(q, {"pick": "true", "noul": 0.98, "probabilities": None}).endswith("yes, JEV was 98% sure")
    assert one_sentence(q, {"pick": "false", "noul": 0.2, "probabilities": None}).endswith("no, JEV was 80% sure")


def test_shadow_and_low_weight_answers_are_left_out_with_a_reason():
    answered = {
        "price_recent_direction": {"pick": "rising", "probabilities": {"rising": 1.0}},
        "direction_lean": {"pick": "below", "probabilities": {"below": 0.4}},          # shadow
        "volume_now": {"pick": "normal", "probabilities": {"normal": 0.9}},
        "nobody": {"pick": "x"},
    }
    weights = {"volume_now": {"weight": 0.2}}
    sentences, left_out = answer_sentences(DOC, answered, weights)
    assert list(sentences) == ["price_recent_direction"]
    assert left_out["direction_lean"].startswith("a shadow forecast")
    assert "0.20" in left_out["volume_now"] and "0.5" in left_out["volume_now"]
    assert left_out["nobody"] == "not a question in the doc"


def test_hour_request_sends_only_jev_fields_and_both_sums():
    req = hour_request({"price_recent_direction": "Over the last 30 minutes... rising, JEV was 98% sure"})
    assert req["id"] == "hour"
    assert set(req["state"]) == {"context", "answers"}
    assert req["state"]["context"]["symbol"] == "SNDK"
    assert list(req["questions"]) == list(HOUR_QIDS) == ["next_30", "next_60"]
    for qid, band in (("next_30", "0.12 sigma"), ("next_60", "0.17 sigma")):
        q = req["questions"][qid]
        assert set(q) == {"type", "instructions", "criteria"}
        assert list(q["criteria"]) == ["up", "down", "flat", "unsure"]
        assert band in q["criteria"]["flat"] and "three times in five" in q["criteria"]["flat"]


def test_hour_summary_keeps_the_primary_on_top_and_every_sum_under_by():
    reply = {"model": "jev-1.13.0", "answers": {
        "next_30": {"type": "choice", "choice": "flat", "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.15, "unsure": 0.05}, "confidence": 0.5},
        "next_60": {"type": "choice", "choice": "up", "probabilities": {"up": 0.4, "flat": 0.3, "down": 0.2, "unsure": 0.1}, "confidence": 0.2}}}
    s = hour_summary(reply)
    assert s["primary"] == PRIMARY == "next_30"
    assert s["pick"] == "flat" and s["probabilities"]["flat"] == 0.6 and s["model"] == "jev-1.13.0"
    assert s["by"]["next_60"]["pick"] == "up" and s["by"]["next_60"]["confidence"] == 0.2
    assert hour_summary({"error": "HTTP 500"}) == {"error": "HTTP 500"}
    assert hour_summary(None) is None


def test_hour_doc_is_well_formed():
    d = load_hour_doc()
    assert list(d["questions"]) == ["next_30", "next_60"]
    assert d["primary"] == "next_30"
    assert d["horizons"]["next_30"]["minutes"] == 30 and d["horizons"]["next_60"]["minutes"] == 60
