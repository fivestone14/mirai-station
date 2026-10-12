"""The question weights: one interface, neutral for now, loaded by step 3 and learned by the grader."""
from __future__ import annotations

import json

from spx_jev.weights import MIN_WEIGHT, WEIGHTS_NAME, QuestionWeights

ALLOWED = {"q_a": {"up", "down"}, "q_b": {"true", "false"}}


def test_neutral_weights_are_one_for_every_live_question_and_count_the_fresh_picks():
    graded = [{"band": "flat", "fresh": {"q_a": "up", "q_b": "true"}},
              {"band": "up", "fresh": {"q_a": "down", "q_retired": "x"}},
              {"band": "up", "fresh": {"q_a": "sideways"}}]                   # an option q_a no longer has
    w = QuestionWeights.learn(graded, ALLOWED)
    assert w.method == "neutral" and set(w.questions) == set(ALLOWED)
    assert w.questions["q_a"]["n"] == 2 and w.questions["q_b"]["n"] == 1
    assert all(v["weight"] == 1.0 and v["in_step_3"] for v in w.questions.values())
    assert w.as_json()["method"] == "neutral" and w.as_json()["min_weight"] == MIN_WEIGHT


def test_loading_reads_the_graders_file_and_a_missing_one_weighs_everything_one(tmp_path):
    assert QuestionWeights.load(tmp_path).weight("anything") == 1.0
    (tmp_path / WEIGHTS_NAME).write_text(json.dumps({"questions": {"q_a": {"weight": 0.2}}}))
    w = QuestionWeights.load(tmp_path)
    assert w.weight("q_a") == 0.2 and not w.in_step_3("q_a") and w.in_step_3("q_b")
    (tmp_path / WEIGHTS_NAME).write_text("not json")
    assert QuestionWeights.load(tmp_path).weight("q_a") == 1.0


def test_a_learning_method_drops_in_by_overriding_learn():
    class Halves(QuestionWeights):
        method = "halves"

        @classmethod
        def learn(cls, graded, allowed):
            return cls({qid: {"weight": 0.5, "n": 0, "in_step_3": True, "why": "test"} for qid in allowed})
    w = Halves.learn([], ALLOWED)
    assert w.weight("q_a") == 0.5 and w.in_step_3("q_a") and w.as_json()["method"] == "halves"
