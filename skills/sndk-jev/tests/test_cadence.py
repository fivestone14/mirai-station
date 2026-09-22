"""Cadence: a question is asked afresh when its interval has elapsed, held in between, recounted daily."""
from __future__ import annotations

import json
from datetime import timedelta

from conftest import at
from sndk_jev.ask import DEFAULT_QUESTIONS, build_requests, load_questions
from sndk_jev.cadence import (MIN_READS, cadence_of, ensure_cadence, fill_missing, held_answer, is_due, parse_cadence,
                              plan, recount, snap)
from sndk_jev.hour import one_sentence

DOC = load_questions(DEFAULT_QUESTIONS)
BY_ID = {qid: q for g in DOC["groups"] for qid, q in g["questions"].items()}
LIVE = [qid for qid, q in BY_ID.items() if q.get("status") == "live"]


def test_doc_wording_becomes_minutes_and_snaps_to_the_steps():
    assert parse_cadence("every scan") == 30 and parse_cadence("hourly") == 60 and parse_cadence("nonsense") == 30
    assert snap(10) == 30 and snap(45) == 30 and snap(60) == 60 and snap(119) == 60 and snap(500) == 120


def test_due_and_held_follow_the_clock():
    now = at(11, 32)
    entry = {"row_ts": at(11, 2).isoformat(), "answer": {"pick": "above", "probabilities": {"above": 0.9}}}
    assert is_due(None, now, 30)
    assert is_due(entry, now, 30) and not is_due(entry, now, 60)
    h = held_answer(entry, now, 60)
    assert h["pick"] == "above" and h["held_from"] == "11:02"
    assert held_answer(entry, at(13, 30), 60) is None            # older than twice the cadence: never held
    assert held_answer(entry, at(11, 50), 30) is not None         # under an hour is always young enough


def test_plan_skips_only_live_questions_that_are_not_due():
    now = at(11, 32)
    last = {qid: {"row_ts": at(11, 2).isoformat(), "answer": {"pick": "x", "probabilities": {"x": 1.0}}} for qid in LIVE}
    cad = {"questions": {LIVE[0]: {"minutes": 60}, LIVE[1]: {"minutes": 30}}}
    skip, held = plan(DOC, last, cad, now)
    assert LIVE[0] in skip and LIVE[0] in held and held[LIVE[0]]["held_from"] == "11:02"
    assert LIVE[1] not in skip                                      # due again on every read
    assert "direction_lean" not in skip                             # shadow: always asked
    reqs, skipped = build_requests({"context": {"symbol": "SNDK"}}, DOC, skip=skip)
    assert all(LIVE[0] not in r["questions"] for r in reqs)
    assert any(skipped[g].get(LIVE[0], "").startswith("not due") for g in skipped)


def test_a_missing_label_keeps_the_held_answer():
    now = at(11, 32)
    last = {"volume_now": {"row_ts": at(11, 2).isoformat(), "answer": {"pick": "heavy", "probabilities": {"heavy": 0.8}}}}
    skipped = {"volume": {"volume_now": "missing volume.now"}}
    held = fill_missing(DOC, skipped, last, {"questions": {}}, now, {})
    assert held["volume_now"]["held_from"] == "11:02"
    s = one_sentence(BY_ID["volume_now"], held["volume_now"])
    assert s.endswith("heavy, JEV was 80% sure, held from 11:02")


def _records(picks_by_time: dict[str, list[tuple[int, int]]], qid: str) -> list[dict]:
    """picks_by_time: pick -> list of (hh, mm) reads; builds records with one answer each."""
    recs = []
    for pick, times in picks_by_time.items():
        for hh, mm in times:
            recs.append({"row_ts": at(hh, mm).isoformat(),
                         "answers": {"g": {"answers": {qid: {"type": "choice", "probabilities": {pick: 0.9, "other": 0.1}}}}}})
    return recs


def test_recount_sets_every_read_for_a_flipper_and_slower_for_a_holder():
    q_flip, q_hold = LIVE[0], LIVE[1]
    times = [(9, 32), (10, 2), (10, 32), (11, 2), (11, 32), (12, 2), (12, 32), (13, 2), (13, 32), (14, 2), (14, 32)]
    recs = []
    for i, (hh, mm) in enumerate(times):
        recs.append({"row_ts": at(hh, mm).isoformat(), "answers": {"g": {"answers": {
            q_flip: {"type": "choice", "probabilities": {"a" if i % 2 else "b": 0.9}},
            q_hold: {"type": "choice", "probabilities": {"same": 0.9}}}}}})
    cad = recount(recs, DOC, None, "2026-09-18")
    assert cad["questions"][q_flip]["minutes"] == 30 and cad["questions"][q_flip]["changes"] == 10
    assert cad["questions"][q_hold]["minutes"] == 120 and cad["questions"][q_hold]["changes"] == 0
    # a question with too few reads keeps what it had
    few = [q for q in LIVE if q not in (q_flip, q_hold)][0]
    assert cad["questions"][few]["minutes"] == parse_cadence(BY_ID[few].get("cadence")) and "under" in cad["questions"][few]["why"]


def test_ensure_cadence_recounts_once_from_the_previous_day(tmp_path):
    out = tmp_path / "jev"; out.mkdir()
    qid = LIVE[0]
    recs = [{"row_ts": at(9 + i // 2, 32 if i % 2 else 2).isoformat(),
             "answers": {"g": {"answers": {qid: {"type": "choice", "probabilities": {"same": 0.9}}}}}} for i in range(1, 13)]
    (out / "2026-09-21.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    cad = ensure_cadence(out, DOC, "2026-09-22")
    assert cad["recounted_from"] == "2026-09-21" and cad["questions"][qid]["minutes"] == 120
    assert json.loads((out / "cadence.json").read_text())["recounted_from"] == "2026-09-21"
    again = ensure_cadence(out, DOC, "2026-09-22")                 # same source day: no second recount
    assert again["recounted_at"] == cad["recounted_at"]
    assert cadence_of(cad, BY_ID[qid], qid) == 120 and cadence_of({}, BY_ID[qid], qid) == parse_cadence(BY_ID[qid].get("cadence"))
