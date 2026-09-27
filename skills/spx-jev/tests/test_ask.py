"""The packer, the question templates and the sender."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import measured
from spx_jev.ask import (DEFAULT_QUESTIONS, build_requests, confidence, constants_named, get_path, load_questions, paths_in, pick,
                         summarize)
from spx_jev.cuts import QUESTION_CONSTANTS
from spx_jev.hour import load_hour_doc
from spx_jev.lane import LANES
from spx_jev.labels.registry import build_labels

QUESTIONS = Path(__file__).resolve().parent.parent / "questions"
SHIPPED = sorted(QUESTIONS.glob("*.json"))
# a number followed by a unit a threshold is written in: none may be typed into a doc by hand
TYPED_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|sigma|vol points?|times|of a (?:tape )?unit|tape units?)")
NAMED = re.compile(r"\{[^{}]*\}")


def _texts(value):
    """Every string inside a question field."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)


def _shipped_questions():
    for path in SHIPPED:
        raw = json.loads(path.read_text())
        for g in raw.get("groups") or [{"questions": raw["questions"]}]:
            for qid, q in g["questions"].items():
                yield path, qid, q


def test_paths_in_reads_backticks():
    q = {"instructions": "Read `price.recent_move` and `gex`. Now?", "criteria": {"a": "see `iv.term_structure`"}}
    assert paths_in(q) == ["price.recent_move", "gex", "iv.term_structure"]


def test_get_path_treats_empty_group_as_missing():
    assert get_path({"price": {}}, "price") is None
    assert get_path({"price": {"x": "y"}}, "price.x") == "y"
    assert get_path({"price": {"x": "y"}}, "price.z") is None


def test_requests_carry_only_the_slice_and_skip_questions_with_missing_labels():
    doc = {"groups": [
        {"id": "a", "reads": ["context", "price.recent_move", "breadth.tick_lean"],
         "questions": {"q1": {"type": "noul", "instructions": "Read `price.recent_move`.", "criteria": {"true": "t", "false": "f"}},
                       "q2": {"type": "noul", "instructions": "Read `breadth.tick_lean`.", "criteria": {"true": "t", "false": "f"}}}},
        {"id": "b", "reads": ["breadth"],
         "questions": {"q3": {"type": "noul", "instructions": "Read `breadth`.", "criteria": {"true": "t", "false": "f"}}}},
    ]}
    state = {"context": {"symbol": "SPX"}, "price": {"recent_move": "rose", "vs_vwap": "above"}, "gex": {"x": "y"}}
    reqs, skipped = build_requests(state, doc)
    assert [r["id"] for r in reqs] == ["a"]
    assert reqs[0]["state"] == {"context": {"symbol": "SPX"}, "price": {"recent_move": "rose"}}
    assert list(reqs[0]["questions"]) == ["q1"]
    assert skipped == {"a": {"q2": "missing breadth.tick_lean"}, "b": {"q3": "missing breadth", "*": "nothing to ask in this group this read"}}


def test_a_label_the_group_does_not_read_is_named_not_called_missing():
    doc = {"groups": [{"id": "a", "reads": ["context", "price.recent_move"],
                       "questions": {"q1": {"type": "noul", "instructions": "Read `price.vs_vwap`.", "criteria": {"true": "t", "false": "f"}}}}]}
    state = {"context": {"symbol": "SPX"}, "price": {"recent_move": "rose", "vs_vwap": "above"}}
    reqs, skipped = build_requests(state, doc)
    assert reqs == [] and skipped["a"]["q1"] == "the group does not read price.vs_vwap"


def test_a_dark_question_is_never_asked():
    doc = {"groups": [{"id": "a", "reads": ["context"], "questions": {
        "q": {"status": "dark", "type": "noul", "instructions": "Read `context.symbol`.", "criteria": {"true": "t", "false": "f"}}}}]}
    reqs, skipped = build_requests({"context": {"symbol": "SPX"}}, doc)
    assert reqs == [] and skipped["a"]["q"].startswith("dark:")


# ---- the templates

def test_every_shipped_doc_loads_with_its_constants_filled():
    for lane in LANES.values():
        questions = [q for g in load_questions(lane.questions)["groups"] for q in g["questions"].values()]
        questions += list(load_hour_doc(lane=lane)["questions"].values())
        left = [t for q in questions for t in _texts(q) if NAMED.search(t)]
        assert not left, f"lane {lane.name} kept an unfilled name: {left[:2]}"
    assert {p.name for p in SHIPPED} == {lane.questions.name for lane in LANES.values()} | {lane.hour_doc.name for lane in LANES.values()}


def test_no_threshold_is_typed_into_a_doc_by_hand():
    for path, qid, q in _shipped_questions():
        for key in ("ask", "instructions", "criteria"):
            for text in _texts(q.get(key)):
                hits = TYPED_NUMBER.findall(NAMED.sub("", text))
                assert not hits, f"{path.name} {qid}.{key} types {hits} by hand; name the constant in braces"


def test_every_name_a_doc_asks_for_is_a_constant_the_code_defines():
    for path, qid, q in _shipped_questions():
        for key in ("ask", "instructions", "criteria"):
            for text in _texts(q.get(key)):
                for name in constants_named(text):
                    assert name in QUESTION_CONSTANTS, f"{path.name} {qid} names {name}, which cuts.py does not define"


def test_an_unknown_constant_stops_the_load(tmp_path):
    doc = {"groups": [{"id": "g", "reads": [], "questions": {"q": {"type": "noul", "instructions": "Read `price.recent_move`.",
                                                                   "criteria": {"true": "moved more than {no_such_cut} sigma", "false": "f"}}}}]}
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="no_such_cut"):
        load_questions(p)


def test_a_filled_constant_is_the_code_number_and_a_format_spec_is_honoured(tmp_path):
    doc = {"groups": [{"id": "g", "reads": [], "questions": {"q": {"type": "noul", "instructions": "Read `gex.weight_side`.",
                                                                   "criteria": {"true": "more than {even_split_high:.0%} above, {move_rule_sigma} sigma", "false": "f"}}}}]}
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(doc))
    q = load_questions(p)["groups"][0]["questions"]["q"]
    assert q["criteria"]["true"] == f"more than 60% above, {QUESTION_CONSTANTS['move_rule_sigma']} sigma"


def _spec_paths():
    spec = json.loads((Path(__file__).resolve().parent.parent / "spec" / "labels.json").read_text())
    return {lab["path"] for lab in spec["labels"]}


def test_every_question_reads_only_labels_the_spec_knows():
    known = _spec_paths()
    groups = {p.split(".")[0] for p in known}
    for lane in LANES.values():
        for g in load_questions(lane.questions)["groups"]:
            for qid, q in g["questions"].items():
                for p in paths_in(q):
                    assert p in known or p in groups, f"{qid} reads {p}, which no label in spec/labels.json provides"


def test_every_live_question_is_asked_on_a_full_read(full_scene):
    labels = build_labels(full_scene)
    state, omitted = labels.state, measured(labels.omitted)
    assert omitted == {}, omitted
    doc = load_questions(DEFAULT_QUESTIONS)
    reqs, skipped = build_requests(state, doc)
    asked = {qid for r in reqs for qid in r["questions"]}
    every = {qid for g in doc["groups"] for qid, q in g["questions"].items() if q.get("status") in ("live", "shadow")}
    assert asked == every, skipped
    for r in reqs:
        assert r["state"]["context"]["symbol"] == "SPX"
        for q in r["questions"].values():
            assert set(q) <= {"type", "instructions", "criteria"}, "only JEV's own fields may be sent"


# ---- the sender

def test_send_turns_every_network_failure_into_a_scrubbed_runtime_error(monkeypatch):
    import urllib.error
    from spx_jev import ask
    key = "apikey_" + "a" * 24 + "_" + "b" * 24
    calls = []

    def urlopen_timeout(req, timeout=0):
        calls.append("t")
        raise TimeoutError("timed out")
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen_timeout)
    with pytest.raises(RuntimeError) as e:
        ask.send({"id": "g", "state": {}, "questions": {}}, api_key=key)
    assert "unreachable" in str(e.value) and "TimeoutError" in str(e.value) and key not in str(e.value)
    assert calls == ["t", "t"], "one retry on a timeout"

    class Body:
        def read(self):
            return f"bad request, your key {key} is wrong".encode()

        def close(self):
            pass

    def urlopen_http(req, timeout=0):
        raise urllib.error.HTTPError("u", 400, "bad", {}, Body())
    monkeypatch.setattr(ask.urllib.request, "urlopen", urlopen_http)
    with pytest.raises(RuntimeError) as e:
        ask.send({"id": "g", "state": {}, "questions": {}}, api_key=key)
    assert "HTTP 400" in str(e.value) and "[key redacted]" in str(e.value) and key not in str(e.value)


def test_send_all_sends_every_request_together_and_keeps_errors():
    from spx_jev.ask import send_all

    def fake(req, api_key=None, timeout=10.0):
        if req["id"] == "slow":
            raise TimeoutError("handshake operation timed out")
        return {"model": "jev-1", "answers": {q: {"type": "noul", "noul": 0.9} for q in req["questions"]}}
    out = send_all([{"id": "slow", "questions": {}}, {"id": "ok", "questions": {"q": {}}}], sender=fake)
    assert out["ok"]["answers"]["q"]["noul"] == 0.9
    assert out["slow"] == {"error": "TimeoutError: handshake operation timed out"}
    assert send_all([], sender=fake) == {}


def test_the_answer_readers_and_the_summary():
    ans = {"answers": {
        "a": {"type": "noul", "noul": 0.81},
        "b": {"type": "choice", "choice": "up", "probabilities": {"up": 0.7, "down": 0.3}, "confidence": 0.55},
        "c": {"type": "score", "score": 1.4, "confidence": 0.6, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}},
    }}
    assert pick(ans["answers"]["a"]) == "true" and round(confidence(ans["answers"]["a"]), 2) == 0.62
    assert pick(ans["answers"]["b"]) == "up" and confidence(ans["answers"]["b"]) == 0.55
    assert pick(ans["answers"]["c"]) == "2" and confidence(ans["answers"]["c"]) is None
    lines = summarize(ans)
    assert lines[0] == "a: yes 0.81" and lines[1].startswith("b: up 0.70  confidence 0.55") and lines[2].startswith("c: score 1.40")


def test_the_tape_lanes_copied_question_is_the_live_one_word_for_word():
    live = {qid: q for g in load_questions(LANES["live"].questions)["groups"] for qid, q in g["questions"].items()}
    tape = {qid: q for g in load_questions(LANES["tape"].questions)["groups"] if g["id"] != "tape" for qid, q in g["questions"].items()}
    assert tape and all(q == live[qid] for qid, q in tape.items())
