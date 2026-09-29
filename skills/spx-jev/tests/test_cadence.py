"""Cadence: a question is asked afresh when its interval has elapsed, held in between, recounted daily."""
from __future__ import annotations

import json
from datetime import timedelta

from conftest import at
from spx_jev.ask import build_requests, load_questions
from spx_jev.cadence import (CHANGE_CUT, cadence_of, distance, ensure_cadence, fill_missing, held_answer, is_due, mark_asleep, plan,
                             recount, snap, vector)
from spx_jev.hour import one_sentence
from spx_jev.lane import LANES
from spx_jev.schedule import not_due, read_slot

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


def test_a_label_left_out_because_its_condition_is_over_holds_nothing():
    """At 14:00 the 13:30 shock has left the lookback: the shock questions are not held at their 13:30 answers."""
    now = at(14, 0)
    last = {q: {"row_ts": at(13, 30).isoformat(), "answer": {"pick": "inside_range", "probabilities": {"inside_range": 0.8}}}
            for q in ("shock_at_extreme", "tick_lean_vs_usual")}
    state = {"context": {"symbol": "SPX"}}
    ended = {p: "no five-minute move in the last 60 minutes passed the shock rule" for p in ("shock.burst", "shock.vs_day_range")}
    _, skipped = build_requests(state, DOC, skip={q: "not due" for q in BY_ID if q not in last}, ended=ended)
    assert skipped["shocks_and_news"]["shock_at_extreme"].startswith("asleep: no five-minute move")
    assert skipped["breadth"]["tick_lean_vs_usual"].startswith("missing breadth.")
    held = fill_missing(DOC, skipped, last, {"questions": {}}, now, {})
    assert set(held) == {"tick_lean_vs_usual"}


def test_an_answer_from_before_the_question_slept_never_comes_back_on_a_later_read():
    """13:30 answers the shock question; at 14:00 the shock has left the lookback and it sleeps; at 14:30 its label
    is missing, and its cadence would have held it: neither brings the 13:30 answer back. A fresh answer after the
    sleep holds again."""
    q = "shock_at_extreme"
    last = {q: {"row_ts": at(13, 30).isoformat(), "answer": {"pick": "inside_range", "probabilities": {"inside_range": 0.8}}}}
    ended = {p: "no five-minute move in the last 60 minutes passed the shock rule" for p in ("shock.burst", "shock.vs_day_range")}
    _, skipped = build_requests({"context": {"symbol": "SPX"}}, DOC, skip={x: "not due" for x in BY_ID if x != q}, ended=ended)
    mark_asleep(last, skipped, {q}, at(14, 0).isoformat())
    assert last[q]["asleep"] == at(14, 0).isoformat()
    missing = {"shocks_and_news": {q: "missing shock.burst"}}
    assert fill_missing(DOC, missing, last, {"questions": {}}, at(14, 30), {}) == {}
    assert q not in plan(DOC, last, {"questions": {q: {"minutes": 120}}}, at(14, 30))[1]
    assert held_answer(last[q], at(14, 30), 120) is None
    fresh = {q: {"row_ts": at(14, 30).isoformat(), "answer": last[q]["answer"]}}          # a fresh answer replaces the entry
    assert fill_missing(DOC, missing, fresh, {"questions": {}}, at(15, 0), {})[q]["held_from"] == at(14, 30).isoformat()


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
    no_code = {q: {k: v for k, v in last[q].items() if k != "code_answer"}}   # an answer given beside no code answer holds
    assert q in plan(DOC, no_code, {}, now, not_due, {}, "11:32", code_answers={q: "flatter_than_usual"})[0]
    one_way = "one_way_hour"                                            # asked on its schedule, never on a code change
    assert one_way in plan(DOC, {one_way: {**last[q], "code_answer": "x"}}, {}, now, {one_way: "not on its schedule"}, {}, "11:32",
                           code_answers={one_way: "y"})[0]


TAPE, LIVE_LANE = LANES["tape"], LANES["live"]
TAPE_DOC = load_questions(TAPE.questions, TAPE.key)
ANSWER = {"pick": "x", "probabilities": {"x": 1.0}}
NO_ANSWER_503 = "JEV returned HTTP 503 for group opening_context: upstream connect error or disconnect/reset before headers"


def test_a_once_only_question_whose_ask_got_no_answer_at_0935_is_asked_at_0940():
    """A failed ask is not an ask: the day constant is asked again at the next read, then held like any other.
    A lost ask from another day, or a moment that is no read of the lane, asks nothing."""
    q = "open_vs_prior_range"
    lost = {q: {"lost": {"row_ts": at(9, 35).isoformat(), "why": NO_ANSWER_503}}}
    due = not_due(TAPE_DOC, TAPE, at(9, 40))
    assert due[q] == "a day constant: asked at 09:35 ET and held"
    skip, held = plan(TAPE_DOC, lost, {}, at(9, 40), due, {}, "09:40", learned=False)
    assert q not in skip and q not in held
    answered = {q: {"row_ts": at(9, 40).isoformat(), "answer": ANSWER}}          # the re-ask was answered: never asked again
    skip, held = plan(TAPE_DOC, answered, {}, at(9, 45), not_due(TAPE_DOC, TAPE, at(9, 45)), {}, "09:45", learned=False)
    assert q in skip and held[q]["held_from"] == at(9, 40).isoformat()
    yesterday = {q: {"row_ts": at(9, 35).isoformat(), "answer": ANSWER, "lost": {"row_ts": at(9, 35, day=DAY_BEFORE).isoformat(), "why": NO_ANSWER_503}}}
    assert q in plan(TAPE_DOC, yesterday, {}, at(9, 40), due, {}, "09:40", learned=False)[0]
    assert q in plan(TAPE_DOC, lost, {}, at(12, 0), {q: "no tape lane read at 12:00 ET"}, {}, None, learned=False)[0]


def test_a_held_question_whose_ask_got_no_answer_is_asked_at_the_next_read_not_held():
    """ruler_loaded is asked every hour; its 11:02 ask timed out, so at 11:32 it is asked rather than holding 10:02's
    answer, whether the schedule or the cadence would have held it."""
    q = "ruler_loaded"
    assert BY_ID[q]["schedule"]["every_min"] == 60
    last = {q: {"row_ts": at(10, 2).isoformat(), "answer": ANSWER,
                "lost": {"row_ts": at(11, 2).isoformat(), "why": "JEV unreachable for group size: TimeoutError: The read operation timed out"}}}
    off_schedule = {q: "not on its schedule at the 11:32 ET read"}
    skip, held = plan(DOC, last, {}, at(11, 32), off_schedule, {}, "11:32")
    assert q not in skip and q not in held
    assert q not in plan(DOC, last, {"questions": {q: {"minutes": 120}}}, at(11, 32), {}, {}, "11:32")[0]
    answered = {q: {k: v for k, v in last[q].items() if k != "lost"}}
    skip, held = plan(DOC, answered, {}, at(11, 32), off_schedule, {}, "11:32")
    assert q in skip and held[q]["held_from"] == at(10, 2).isoformat()


def test_mondays_lost_opening_answers_are_asked_again_at_0940_and_held_by_the_live_lane():
    """2026-09-28: JEV answered the tape lane's 09:35 opening_context and events_regime requests with HTTP 503. Their
    four questions are asked only at 09:35, and the live lane, which holds the three opening_context answers from
    the tape lane until 11:32, had nothing to hold: those three went unanswered all day (event_clock was answered
    by the live lane from 10:30). Replayed through plan: the four are asked at 09:40, and the live lane's 10:30 read
    holds the three from 09:40. The day's other four unanswered questions were the premarket lane's, lost at its
    last checkpoint, 09:28, which this rule does not reach."""
    monday = "2026-09-28"
    lost_ids = ("open_vs_prior_range", "vix_overnight_surprise", "brief_vs_tape", "event_clock")
    last = {q: {"lost": {"row_ts": at(9, 35, day=monday).isoformat(), "why": NO_ANSWER_503}} for q in lost_ids}
    now = at(9, 40, day=monday)
    skip, _ = plan(TAPE_DOC, last, {}, now, not_due(TAPE_DOC, TAPE, now), {}, read_slot(TAPE, now), learned=False)
    assert not set(lost_ids) & set(skip)
    answered = {q: {"row_ts": now.isoformat(), "answer": ANSWER} for q in lost_ids}
    live_doc, later = load_questions(LIVE_LANE.questions, LIVE_LANE.key), at(10, 30, day=monday, ss=46)
    skip, held = plan(live_doc, {}, {}, later, not_due(live_doc, LIVE_LANE, later), answered, read_slot(LIVE_LANE, later))
    assert {q: held[q]["held_from"] for q in lost_ids[:3]} == dict.fromkeys(lost_ids[:3], now.isoformat())


def test_a_day_constant_whose_one_read_held_nothing_is_asked_at_the_next_read_then_held():
    """2026-09-28: btc_five_day_lead slept at the tape lane's 09:35 read and was never asked, yet every later read
    said "asked at 09:35 ET and held" with nothing held. A day constant with no answer from today is asked at the
    next read, and a held one's reason names the read its answer came from."""
    q = "btc_five_day_lead"
    assert TAPE_DOC and any(q in g["questions"] for g in TAPE_DOC["groups"])
    due = not_due(TAPE_DOC, TAPE, at(9, 40))
    assert due[q] == "a day constant: asked at 09:35 ET and held"
    for nothing_today in ({}, {q: {"row_ts": at(9, 35, day=DAY_BEFORE).isoformat(), "answer": ANSWER}}):
        skip, held = plan(TAPE_DOC, nothing_today, {}, at(9, 40), due, {}, "09:40", learned=False)
        assert q not in skip and q not in held
    answered = {q: {"row_ts": at(9, 40).isoformat(), "answer": ANSWER}}
    skip, held = plan(TAPE_DOC, answered, {}, at(9, 45), not_due(TAPE_DOC, TAPE, at(9, 45)), {}, "09:45", learned=False)
    assert skip[q] == "a day constant: asked at 09:40 ET and held" and held[q]["held_from"] == at(9, 40).isoformat()
    assert q in plan(TAPE_DOC, {}, {}, at(12, 0), {q: "no tape lane read at 12:00 ET"}, {}, None, learned=False)[0]


def test_a_question_held_from_its_other_lane_says_whether_that_lane_had_an_answer():
    """The live lane holds open_vs_prior_range from the tape lane until 11:32: its reason names the tape lane's read,
    or says there was nothing to hold, rather than claiming a held answer the read does not have."""
    q = "open_vs_prior_range"
    now = at(10, 32)
    due = {q: "asked on its other lane and held here until 11:32 ET"}
    skip, held = plan(DOC, {}, {}, now, due, {q: {"row_ts": at(9, 40).isoformat(), "answer": ANSWER}}, "10:32")
    assert skip[q] == "asked on its other lane at 09:40 ET and held here until 11:32 ET" and q in held
    skip, held = plan(DOC, {}, {}, now, due, {}, "10:32")
    assert skip[q] == "asked on its other lane only, which has no answer from today to hold here" and q not in held
    late = {q: "held from its other lane only until 11:32 ET"}
    assert plan(DOC, {}, {}, at(12, 2), late, {}, "12:02")[0][q] == late[q]


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
