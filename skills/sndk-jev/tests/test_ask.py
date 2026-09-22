from __future__ import annotations

import json

import pytest

from conftest import at, bars_from_closes, flat_bars
from sndk_jev.ask import DEFAULT_QUESTIONS, build_requests, get_path, load_questions, paths_in, summarize
from sndk_jev.state_builder import build_state


def test_paths_in_reads_backticks():
    q = {"instructions": "Read `price.recent_move` and `gex`. Now?", "criteria": {"a": "see `iv.skew`"}}
    assert paths_in(q) == ["price.recent_move", "gex", "iv.skew"]


def test_get_path_treats_empty_group_as_missing():
    assert get_path({"price": {}}, "price") is None
    assert get_path({"price": {"x": "y"}}, "price.x") == "y"
    assert get_path({"price": {"x": "y"}}, "price.z") is None


def test_requests_carry_only_the_slice_and_skip_questions_with_missing_labels():
    doc = {"groups": [
        {"id": "a", "reads": ["context", "price.recent_move", "volume.now"],
         "questions": {"q1": {"type": "noul", "instructions": "Read `price.recent_move`.", "criteria": {"true": "t", "false": "f"}},
                       "q2": {"type": "noul", "instructions": "Read `volume.now`.", "criteria": {"true": "t", "false": "f"}}}},
        {"id": "b", "reads": ["volume"],
         "questions": {"q3": {"type": "noul", "instructions": "Read `volume`.", "criteria": {"true": "t", "false": "f"}}}},
    ]}
    state = {"context": {"symbol": "SNDK"}, "price": {"recent_move": "rose", "vs_vwap": "above"}, "gex": {"x": "y"}}
    reqs, skipped = build_requests(state, doc)
    assert [r["id"] for r in reqs] == ["a"]
    assert reqs[0]["state"] == {"context": {"symbol": "SNDK"}, "price": {"recent_move": "rose"}}
    assert list(reqs[0]["questions"]) == ["q1"]
    assert skipped == {"a": {"q2": "missing volume.now"}, "b": {"q3": "missing volume", "*": "no question in this group has all the labels it needs"}}


def _spec():
    from pathlib import Path
    with open(Path(__file__).resolve().parent.parent / "spec" / "labels.json", encoding="utf-8") as f:
        return json.load(f)


def test_every_question_reads_only_labels_the_spec_knows():
    doc = load_questions(DEFAULT_QUESTIONS)
    known = {l["path"] for l in _spec()["labels"]}
    groups = {p.split(".")[0] for p in known}
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            for p in paths_in(q):
                assert p in known or p in groups, f"{qid} reads {p}, which no label in spec/labels.json provides"


def test_live_questions_are_answerable_and_waiting_ones_wait_only_on_unbuilt_labels(full_scene):
    """A question whose labels are all built must reach a request. One that reads a free or new
    label may be skipped, but only because of that label, never because of a built one."""
    s = full_scene
    state, omitted = build_state(s)
    assert omitted == {}, omitted
    built = {l["path"] for l in _spec()["labels"] if l["status"] == "built"}
    built_groups = {p.split(".")[0] for p in built}
    doc = load_questions(DEFAULT_QUESTIONS)
    reqs, skipped = build_requests(state, doc)
    asked = {qid for r in reqs for qid in r["questions"]}
    n_live = 0
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            paths = paths_in(q)
            if q.get("status") == "dark":
                # dark: never asked, whatever the state holds, and the reason says why
                assert qid not in asked, f"{qid} is dark but was asked"
                assert skipped[g["id"]][qid].startswith("dark:"), skipped[g["id"]].get(qid)
                continue
            live = all(p in built or (p in built_groups and "." not in p) for p in paths)
            if live:
                n_live += 1
                assert qid in asked, f"{qid} reads only built labels but was skipped: {skipped.get(g['id'], {}).get(qid)}"
                assert q.get("status", "live") in ("live", "shadow"), f"{qid} reads only built labels but is marked {q.get('status')}"
            else:
                assert qid not in asked, f"{qid} reads an unbuilt label but was asked"
                assert q.get("status") == "waiting", f"{qid} reads an unbuilt label and must be marked waiting"
    assert n_live >= 8
    for r in reqs:
        assert r["state"]["context"]["symbol"] == "SNDK"
        for q in r["questions"].values():
            assert set(q) <= {"type", "instructions", "criteria"}, "only JEV's own fields may be sent"
        json.dumps(r)


def test_send_all_sends_every_request_together_and_keeps_errors():
    from sndk_jev.ask import send_all
    import threading
    seen, lock = [], threading.Lock()

    def fake(req, api_key=None, timeout=10.0):
        with lock:
            seen.append(req["id"])
        if req["id"] == "bad":
            raise RuntimeError("boom")
        return {"model": "jev-1.13.0", "answers": {q: {"type": "noul", "noul": 0.5} for q in req["questions"]}}

    reqs = [{"id": rid, "state": {}, "questions": {"q": {}}} for rid in ("a", "bad", "c")]
    out = send_all(reqs, sender=fake)
    assert sorted(seen) == ["a", "bad", "c"]
    assert out["bad"] == {"error": "boom"}
    assert out["a"]["model"] == "jev-1.13.0" and out["c"]["answers"]["q"]["noul"] == 0.5
    assert send_all([], sender=fake) == {}


def test_summarize_reads_every_answer_type():
    ans = {"answers": {
        "a": {"type": "noul", "noul": 0.81},
        "b": {"type": "choice", "choice": "up", "probabilities": {"up": 0.7, "down": 0.3}, "confidence": 0.55},
        "c": {"type": "score", "score": 1.4, "confidence": 0.6, "probabilities": [0.1, 0.4, 0.5]},
    }}
    lines = summarize(ans)
    assert lines[0] == "a: yes 0.81"
    assert lines[1].startswith("b: up 0.70  confidence 0.55")
    assert lines[2].startswith("c: score 1.40  confidence 0.60")
