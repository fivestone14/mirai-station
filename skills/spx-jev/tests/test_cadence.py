"""Cadence: a question is asked afresh when its interval has elapsed, held in between, recounted daily."""
from __future__ import annotations

import json
from datetime import timedelta

from conftest import at
from spx_jev.ask import build_requests, load_questions
from spx_jev.cadence import (CHANGE_CUT, cadence_of, distance, ensure_cadence, fill_missing, held_answer, is_due, plan, recount,
                             snap, vector)
from spx_jev.hour import one_sentence
from spx_jev.lane import LANES

DOC = load_questions(LANES["live"].questions, LANES["live"].key)
BY_ID = {qid: q for g in DOC["groups"] for qid, q in g["questions"].items()}
# the live questions asked every 30 minutes from 10:02, the plain case the cadence works on
LIVE = [qid for qid, q in BY_ID.items() if q.get("status") == "live" and q["schedule"] == {"every_min": 30, "from": "10:02", "to": "15:32"}]


def test_the_schedules_every_min_is_the_starting_cadence_and_snaps_to_the_steps():
    assert cadence_of({}, BY_ID["price_move_5way"], "price_move_5way") == 30
    assert cadence_of({}, BY_ID["ruler_loaded"], "ruler_loaded") == 60                      # every_min 60
    assert cadence_of({}, BY_ID["expiry_calendar"], "expiry_calendar") == 30                # asked at named reads
    assert {cadence_of({}, q, qid) for qid, q in BY_ID.items()} <= {30, 60, 120}
    assert snap(10) == 30 and snap(45) == 30 and snap(60) == 60 and snap(119) == 60 and snap(500) == 120


def test_due_and_held_follow_the_clock_and_a_held_answer_keeps_its_full_timestamp():
    now = at(11, 32)
    entry = {"row_ts": at(11, 2).isoformat(), "answer": {"pick": "above", "probabilities": {"above": 0.9}}}
    assert is_due(None, now, 30)
    assert is_due(entry, now, 30) and not is_due(entry, now, 60)
    h = held_answer(entry, now, 60)
    assert h["pick"] == "above" and h["held_from"] == at(11, 2).isoformat()
    assert held_answer(entry, at(13, 30), 60) is None            # older than twice the cadence: never held
    assert held_answer(entry, at(11, 50), 30) is not None         # under an hour is always young enough


def test_distance_is_the_whole_answer_moving_not_the_pick_flipping():
    a90 = {"probabilities": {"above": 0.90, "even": 0.08, "below": 0.02}}
    a55 = {"probabilities": {"above": 0.55, "even": 0.40, "below": 0.05}}
    a88 = {"probabilities": {"above": 0.88, "even": 0.10, "below": 0.02}}
    assert round(distance(a90, a55), 2) == 0.70 and distance(a90, a55) >= CHANGE_CUT
    assert round(distance(a90, a88), 2) == 0.04 and distance(a90, a88) < CHANGE_CUT
    assert vector({"noul": 0.8}) == {"yes": 0.8, "no": 0.2} and distance(None, a90) == 0.0


def test_plan_holds_only_live_questions_that_are_not_due_and_says_when_in_market_time():
    now = at(11, 32)
    last = {qid: {"row_ts": at(11, 2).isoformat(), "answer": {"pick": "x", "probabilities": {"x": 1.0}}} for qid in LIVE}
    cad = {"questions": {LIVE[0]: {"minutes": 60}, LIVE[1]: {"minutes": 30}}}
    skip, held = plan(DOC, last, cad, now)
    assert skip[LIVE[0]] == "not due: cadence 60 min, last asked 11:02 ET" and held[LIVE[0]]["held_from"] == at(11, 2).isoformat()
    assert LIVE[1] not in skip and "flow_vs_price" not in skip        # due every read; shadow: asked when due, never held
    moving = {LIVE[0]: {**last[LIVE[0]], "moved": 0.7}}
    assert LIVE[0] not in plan(DOC, moving, cad, now)[0]              # in motion: asked now
    reqs, skipped = build_requests({"context": {"symbol": "SPX"}}, DOC, skip=skip)
    assert all(LIVE[0] not in r["questions"] for r in reqs)


def test_a_missing_label_keeps_the_held_answer():
    now = at(11, 32)
    q = "tick_lean_vs_usual"
    last = {q: {"row_ts": at(11, 2).isoformat(), "answer": {"pick": "buying", "probabilities": {"buying": 0.8}}}}
    held = fill_missing(DOC, {"breadth": {q: "missing breadth.tick_side_vs_usual"}}, last, {"questions": {}}, now, {})
    assert one_sentence(BY_ID[q], held[q]).endswith("buying, JEV was 80% sure (held since 11:02 ET, not re-asked)")


def test_what_the_schedule_does_not_ask_holds_by_its_kind():
    """A day constant holds all day, a question held from the other lane holds that lane's answer, and an
    hourly one off its hour holds while young; shadow questions are never held."""
    now = at(11, 32)
    answer = {"pick": "x", "probabilities": {"x": 1.0}}
    last = {"vol_curve_vs_usual": {"row_ts": at(10, 2).isoformat(), "answer": answer},
            "ruler_loaded": {"row_ts": at(11, 2).isoformat(), "answer": answer},
            "flow_vs_price": {"row_ts": at(11, 2).isoformat(), "answer": answer}}
    borrowed = {"open_vs_prior_range": {"row_ts": at(9, 35).isoformat(), "answer": answer}}
    not_due = {q: "not on its schedule" for q in ("vol_curve_vs_usual", "ruler_loaded", "flow_vs_price", "open_vs_prior_range")}
    skip, held = plan(DOC, last, {}, now, not_due, borrowed, "11:32")
    assert set(not_due) <= set(skip) and set(held) == {"vol_curve_vs_usual", "ruler_loaded", "open_vs_prior_range"}
    assert held["open_vs_prior_range"]["held_from"] == at(9, 35).isoformat()
    later, stale = at(15, 2), {k: {**v, "row_ts": at(10, 2, day="2026-09-17").isoformat()} for k, v in last.items()}
    _, held_late = plan(DOC, last, {}, later, not_due, borrowed, "15:02")
    assert "vol_curve_vs_usual" in held_late and "ruler_loaded" not in held_late          # a day constant has no age limit
    assert "open_vs_prior_range" not in held_late                                           # held from the other lane only until 11:32
    assert plan(DOC, stale, {}, now, not_due, {}, "11:32")[1] == {}                         # nothing from another day


def test_a_question_held_until_its_code_answer_changes_is_asked_again_when_it_does():
    """vol_curve_vs_usual is asked at 10:02, then held until the code's own reading of the curve changes."""
    q = "vol_curve_vs_usual"
    now, not_due = at(11, 32), {q: "a day constant: asked at 10:02 ET and held"}
    last = {q: {"row_ts": at(10, 2).isoformat(), "answer": {"pick": "usual", "probabilities": {"usual": 0.8}}, "code_answer": "usual"}}
    skip, held = plan(DOC, last, {}, now, not_due, {}, "11:32", code_answers={q: "flatter_than_usual"})
    assert q not in skip and q not in held
    for same in ({q: "usual"}, {}, None):
        skip, held = plan(DOC, last, {}, now, not_due, {}, "11:32", code_answers=same)
        assert q in skip and held[q]["held_from"] == at(10, 2).isoformat()
    yesterday = {q: {**last[q], "row_ts": at(10, 2, day=DAY_BEFORE).isoformat()}}
    assert q in plan(DOC, yesterday, {}, now, not_due, {}, "11:32", code_answers={q: "flatter_than_usual"})[0]
    one_way = "one_way_hour"                                            # asked on its schedule, never on a code change
    assert one_way in plan(DOC, {one_way: {**last[q], "code_answer": "x"}}, {}, now, {one_way: "not on its schedule"}, {}, "11:32",
                           code_answers={one_way: "y"})[0]


def test_recount_sets_every_read_for_a_flipper_and_slower_for_a_holder():
    q_flip, q_hold = LIVE[0], LIVE[1]
    times = [(9, 32), (10, 2), (10, 32), (11, 2), (11, 32), (12, 2), (12, 32), (13, 2), (13, 32), (14, 2), (14, 32)]
    recs = [{"row_ts": at(hh, mm).isoformat(), "answers": {"g": {"answers": {
        q_flip: {"type": "choice", "probabilities": {"a" if i % 2 else "b": 0.9}},
        q_hold: {"type": "choice", "probabilities": {"same": 0.9}}}}}} for i, (hh, mm) in enumerate(times)]
    cad = recount(recs, DOC, None, DAY_BEFORE)
    assert cad["questions"][q_flip]["minutes"] == 30 and cad["questions"][q_flip]["changes"] == 10
    assert cad["questions"][q_hold]["minutes"] == 120 and cad["questions"][q_hold]["changes"] == 0
    few = LIVE[2]
    assert cad["questions"][few]["minutes"] == cadence_of({}, BY_ID[few], few) and "under" in cad["questions"][few]["why"]


def test_recount_counts_held_reads_as_the_answer_standing():
    q = LIVE[0]
    recs = [{"row_ts": at(10, 2).isoformat(), "answers": {"g": {"answers": {q: {"type": "choice", "probabilities": {"a": 0.9}}}}}}]
    for k in range(1, 11):
        t = at(10, 2) + timedelta(minutes=30 * k)
        recs.append({"row_ts": t.isoformat(), "answers": {}, "held": {q: at(10, 2).isoformat()}})
    e = recount(recs, DOC, None, DAY_BEFORE)["questions"][q]
    assert e["reads"] == 11 and e["changes"] == 0 and e["minutes"] == 120, e


def test_ensure_cadence_recounts_once_from_the_previous_day(tmp_path):
    out = tmp_path / "spx_jev"
    out.mkdir()
    qid = LIVE[0]
    recs = [{"row_ts": at(9 + i // 2, 32 if i % 2 else 2, day=DAY_BEFORE).isoformat(),
             "answers": {"g": {"answers": {qid: {"type": "choice", "probabilities": {"same": 0.9}}}}}} for i in range(1, 13)]
    (out / f"{DAY_BEFORE}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    cad = ensure_cadence(out, DOC, "2026-09-18")
    assert cad["recounted_from"] == DAY_BEFORE and cad["questions"][qid]["minutes"] == 120
    again = ensure_cadence(out, DOC, "2026-09-18")                   # same source day: no second recount
    assert again["recounted_at"] == cad["recounted_at"]
    assert cadence_of(cad, BY_ID[qid], qid) == 120 and cadence_of({}, BY_ID[qid], qid) == 30


DAY_BEFORE = "2026-09-17"
