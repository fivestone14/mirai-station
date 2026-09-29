"""The opening lane end to end with a fake JEV: its own folder, every read fresh, the unit and the band on
every record, the exact-bar grade ten minutes on, the close-out, and the live folder left as it was."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import DAY, PRIOR_DAYS, at, bars_from_closes, make_row, write_prior_rows, write_state
from spx_jev import grade, service
from spx_jev.cuts import TAPE_BIG_UNITS, TAPE_FLAT_UNITS
from spx_jev.lane import TAPE
from spx_jev.service import run_once

DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise, fall, or go nowhere?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}}]}
FIVE = {"down_big": 0.05, "down_small": 0.1, "flat": 0.6, "up_small": 0.15, "up_big": 0.05, "unsure": 0.05}
ET = ZoneInfo("America/New_York")


def _state(tmp_path, rows, n_bars):
    """Bars with a 3-point wick, so the tape unit is measured (6 points a slice) rather than floored, over ten flat prior
    sessions with trusted morning rulers."""
    prior = {d: bars_from_closes([7700.0] * 390, day=d) for d in PRIOR_DAYS}
    write_prior_rows(tmp_path, {d: [make_row(at(9, 31, day=d), 7700.0)] for d in prior})
    return write_state(tmp_path, DAY, rows, bars_from_closes([7700.0] * n_bars, wick=3.0), prior)


def _today(monkeypatch):
    """The grader's calendar pinned to noon on DAY, so the replayed day is graded as today."""
    fixed = datetime.fromisoformat(f"{DAY}T12:00:00").replace(tzinfo=ET)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(grade, "datetime", Clock)


def _answers(requests, **kw):
    return {r["id"]: {"model": "fake-1", "answers": {
        qid: {"type": "choice", "choice": list(q["criteria"])[0], "confidence": 0.8,
              "probabilities": {k: (0.85 if i == 0 else 0.05) for i, k in enumerate(q["criteria"])}}
        for qid, q in r["questions"].items()}} for r in requests}


AVERAGE = {"type": "choice", "choice": "flat", "confidence": 0.6, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.2}}


def _sums(seen):
    """The end-price sum's request, kept in ``seen``, and the average-price sum's, each answered flat."""
    def send(req, **kw):
        if req["id"] == "hour":
            seen.append(req)
            return {"model": "fake-1", "answers": {"next_10": {"type": "choice", "choice": "flat", "confidence": 0.6, "probabilities": FIVE}}}
        return {"model": "fake-1", "answers": {"average_10": AVERAGE}}
    return send


def test_a_tape_read_writes_its_own_folder_and_is_graded_on_the_exact_bar_ten_minutes_on(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)     # bars 09:30 .. 10:39: the read is stamped 10:40
    (state / "spx_jev" / "latest.json").write_text('{"row_ts": "the live card"}')
    seen = []
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums(seen))
    out = state / "spx_jev" / "lanes" / "tape"
    c = run_once(state, out, DOC, True, DAY, lane=TAPE)
    assert json.loads((state / "spx_jev" / "latest.json").read_text()) == {"row_ts": "the live card"}
    assert c["lane"] == "tape" and c["row_ts"] == at(10, 40).isoformat()
    assert c["ruler"] == {"unit_points": 6.0, "unit_sigma": 0.08, "slices_used": 3, "source": "tape",
                          "rank": {"band": "top third", "higher_than": 10, "of": 10}}      # the prior sessions' minutes span a point
    assert c["band"] == {"flat_points": round(TAPE_FLAT_UNITS * 6, 2), "big_points": round(TAPE_BIG_UNITS * 6, 2),
                         "flat_units": TAPE_FLAT_UNITS, "big_units": TAPE_BIG_UNITS}
    assert c["hour"]["primary"] == "next_10" and "blend" not in c["hour"] and c["hour"]["views"]["size"]["pick"] == "small"
    assert seen[0]["state"]["context"]["unit"].startswith("one tape unit is 6.0 points, in the top third for this minute")
    assert c["schedule"]["reads"][0] == at(9, 35).isoformat() and c["schedule"]["close_out"] == at(10, 42).isoformat()
    # the lane keeps its last answers for its day constants (asked at 09:35 and held), in its own folder
    assert json.loads((out / "last_asked.json").read_text())["q_dir"]["row_ts"] == at(10, 40).isoformat()
    assert not (state / "spx_jev" / "last_asked.json").exists()

    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(10, 45, ss=20), 7701.0)], 80)
    c2 = run_once(state, out, DOC, True, DAY, lane=TAPE)
    assert c2["row_ts"] == at(10, 50).isoformat() and "move_since_read" in c2["stretch"]
    grades = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines() if l.strip()]
    assert len(grades) == 1 and grades[0]["band"] == "flat" and grades[0]["realized_points"] == 0.0
    assert c2["calls"][1]["end_price"] == {"outcome": "flat", "hit": True, "moved": {"realized_points": 0.0, "realized_units": 0.0},
                                           "pick": "flat", "p": 0.6}
    graded = c2["calls"][1]["integral"]                                     # and on the average price over the same ten bars
    assert (graded["label"], graded["g"], graded["edge"], graded["verdict"]) == ("flat", 0.0, 1.56, "right")
    assert not any("checks" in x for x in c2["calls"])                    # each call has its own mark: no shared checks
    assert json.loads((out / "weights.json").read_text())["lane"] == "tape"


def test_a_tape_run_pointed_at_the_live_folder_refuses(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    with pytest.raises(ValueError, match="must not write into"):
        run_once(state, state / "spx_jev", DOC, True, DAY, lane=TAPE)


def test_the_close_out_grades_the_last_calls_and_asks_jev_nothing(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "spx_jev" / "lanes" / "tape"
    run_once(state, out, DOC, True, DAY, lane=TAPE)
    reads_before = (out / f"{DAY}.jsonl").read_text()

    def refuse(*a, **k):
        raise AssertionError("the close-out asked JEV")
    monkeypatch.setattr(service, "send_all", refuse)
    monkeypatch.setattr(service, "send", refuse)
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.setattr(service, "now_et", lambda: datetime.fromisoformat(f"{DAY}T10:52:00").replace(tzinfo=ET))
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 82)
    assert service.main(["--state-dir", str(state), "--lane", "tape"]) == 0
    c = json.loads((out / "latest.json").read_text())
    assert c["closed_out_at"] and c["tally"] == {"calls": 1, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 0}
    assert (out / f"{DAY}.jsonl").read_text() == reads_before
    kinds = [json.loads(l)["kind"] for l in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]
    assert kinds == ["read", "grade", "close_out"]                          # the read, its grade at the close-out, the close-out


def _graded(pick, label, g=None, edge=1.59, end=None):
    """A call as day_calls gives it once both grades are in: the average price's ``label`` (its direction deciding)
    and, by default, an end price that disagrees with it."""
    verdict = "passed" if pick == "unsure" else "right" if pick.split("_")[0] == label else "wrong"
    return {"pick": pick, "end_price": end or {"outcome": "up_big", "hit": False},
            "integral": {"graded": True, "label": label, "g": g if g is not None else {"up": 3.0, "down": -3.0}.get(label, 0.0),
                         "edge": edge, "verdict": verdict}}


def test_the_tally_counts_the_average_price_grade_its_direction_deciding_and_passes_apart():
    """The S9 call (down_small, the average -4.52 against 1.59, the end price down big) is right: its direction
    decides, the size never does. An unsure call is passed, never a miss, and counts in neither the right nor the
    graded directional calls; a call whose average is not graded yet is still to grade whatever its end price."""
    s9 = _graded("down_small", "down", -4.52, end={"outcome": "down_big", "hit": False})
    calls = [s9, _graded("flat", "up"), _graded("up", "up"), _graded("unsure", "flat"),
             {"pick": "flat", "end_price": {"outcome": "flat", "hit": True}},            # the average waits on a bar
             {"pick": "up", "integral": {"graded": False, "reason": "not graded: bars missing"}}]
    tally = service.calls_block(calls)["tally"]
    assert tally == {"calls": 6, "graded": 4, "right": 2, "passed": 1, "end_price_only": 0, "closed": 0}
    assert service.tally_words(tally) == "2 of 3 calls right · 1 passed · 2 still to grade"
    assert service.tally_words({**tally, "calls": 4}) == "2 of 3 calls right · 1 passed"
    assert service.tally_words({"calls": 3, "graded": 3, "right": 2, "passed": 0}) == "2 of 3 calls right"
    assert service.tally_words({"calls": 8, "graded": 7, "right": 0, "passed": 7}) == "7 passed · 1 still to grade"
    assert service.tally_words({"calls": 2, "graded": 0, "right": 0, "passed": 0}) == "no calls graded yet"
    # the end price never moves the tally of a call graded on the average: right at its mark and wrong on the average is wrong
    wrong = [_graded("up", "down", end={"outcome": "up", "hit": True})] * 3
    assert service.calls_block(wrong)["tally"] == {"calls": 3, "graded": 3, "right": 0, "passed": 0, "end_price_only": 0, "closed": 0}


def test_a_close_out_whose_bar_has_not_come_leaves_the_call_open_and_a_later_live_run_grades_it(tmp_path, monkeypatch):
    """09-28: the bars stopped from 09:56 to 10:11, the close-outs got one try, and the morning's last calls stayed
    ungraded on the card and in the archive all day. The close-out waits for the bar its lane's last call is graded
    on; when it still has not come, the card is not closed out, and each live run after the lane's close-out grades
    the lane again and refreshes its card until nothing is left to grade."""
    state = _state(tmp_path, [make_row(at(10, 25, ss=10), 7700.0)], 60)     # bars 09:30 .. 10:29: the 10:30 read, marked at 10:40
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "spx_jev" / "lanes" / "tape"
    run_once(state, out, DOC, True, DAY, lane=TAPE)
    waited = []
    monkeypatch.setattr(service, "wait_for_bar", lambda state_dir, fire: waited.append(fire) or False)
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(service, "now_et", lambda: at(10, 42))
    assert service.main(["--state-dir", str(state), "--lane", "tape"]) == 0
    c = json.loads((out / "latest.json").read_text())
    assert waited == [at(10, 40)] and "closed_out_at" not in c and c["tally"]["graded"] == 0
    assert c["graded_at"]                                                         # the phone redraws on it all the same
    archive = state / "spx_jev" / "archive" / f"{DAY}.jsonl"

    def tape_kinds():
        return [r["kind"] for r in map(json.loads, archive.read_text().splitlines()) if r["lane"] == "tape"]
    assert tape_kinds() == ["read", "close_out"]

    _state(tmp_path, [make_row(at(10, 25, ss=10), 7700.0), make_row(at(11, 1, ss=5), 7700.0)], 91)
    monkeypatch.setattr(service, "now_et", lambda: at(11, 2))
    assert service.main(["--state-dir", str(state)]) == 0                         # the live lane's 11:02 run
    c = json.loads((out / "latest.json").read_text())
    assert c["closed_out_at"] == c["graded_at"] and c["tally"] == {"calls": 1, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0,
                                                                      "closed": 0}
    assert tape_kinds() == ["read", "close_out", "grade", "close_out"]
    assert service.main(["--state-dir", str(state)]) == 0                         # closed out: a later live run leaves it be
    assert json.loads((out / "latest.json").read_text()) == c


def test_a_call_that_can_never_be_graded_is_counted_apart_never_as_still_to_grade():
    """A call closed for good (a halted window, no band on record) will never get a grade: counting it as still
    to grade would promise one, beside its own row saying it was not graded."""
    calls = [_graded("up", "up"), {"pick": "down", "closed": "halted window: no bar at the mark on a finished day"}]
    tally = service.calls_block(calls)["tally"]
    assert tally == {"calls": 2, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 1}
    assert service.still_to_grade(tally) == 0
    assert service.tally_words(tally) == "1 of 1 calls right · 1 never graded"
    assert service.tally_words({**tally, "calls": 3}) == "1 of 1 calls right · 1 still to grade · 1 never graded"
    unsure = service.calls_block([_graded("unsure", "flat"), *calls])["tally"]
    assert service.tally_words(unsure) == "1 of 1 calls right · 1 passed · 1 never graded"
    # a morning whose only call was closed for good has nothing left to grade, and says so rather than "no calls graded yet"
    assert service.tally_words(service.calls_block(calls[1:])["tally"]) == "1 never graded"


def test_a_call_closed_at_its_end_price_but_graded_on_the_average_is_graded_never_also_never_graded():
    """On the opening lane the end price needs the mark's own minute and the average price tolerates one missing bar
    before it, so a call can be closed at its end price and still graded on the average. Counted as closed too, it left
    an open call beside it with nothing still to grade: the close-out called the day finished and stopped retrying, and
    the words said "1 of 1 calls right · 1 never graded" about the one call."""
    both = {**_graded("up", "up"), "closed": "halted window: no bar at the mark on a finished day"}
    del both["end_price"]
    tally = service.calls_block([both, {"pick": "down"}])["tally"]
    assert tally == {"calls": 2, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 0}
    assert service.still_to_grade(tally) == 1
    assert service.tally_words(tally) == "1 of 1 calls right · 1 still to grade"


SCHEDULED = {"groups": [{"id": "g1", "reads": ["context"], "questions": {
    "q_const": {"status": "live", "type": "choice", "lanes": ["opening_five_minute", "thirty_minute"],
                "schedule": {"opening_five_minute": {"at": ["09:35"], "hold": True}, "thirty_minute": {"hold_until": "11:32"}},
                "instructions": "Read `context.symbol`.", "criteria": {"a": "a", "b": "b"}},
    "q_every": {"status": "live", "type": "choice", "lanes": ["opening_five_minute", "thirty_minute"],
                "schedule": {"opening_five_minute": {"every_min": 5, "from": "09:40", "to": "10:30"},
                             "thirty_minute": {"every_min": 30, "from": "10:02", "to": "15:32"}},
                "instructions": "Read `context.symbol`.", "criteria": {"a": "a", "b": "b"}},
    "q_gated": {"status": "live", "type": "noul", "lanes": ["thirty_minute"], "sleep_when": "nothing happened",
                "schedule": {"thirty_minute": {"every_min": 30, "from": "10:02", "to": "15:32"}},
                "instructions": "Read `context.symbol`.", "criteria": {"true": "t", "false": "f"}}}}]}


def test_a_day_constant_is_asked_at_0935_held_on_the_lane_and_borrowed_by_the_live_reads_until_1132(tmp_path, monkeypatch):
    from spx_jev.ask import load_questions
    from spx_jev.lane import LIVE
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(SCHEDULED))
    tape_doc, live_doc = load_questions(doc_path, TAPE.key), load_questions(doc_path, LIVE.key)
    root = tmp_path / "state"
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    tape_out = root / "spx_jev" / "lanes" / "tape"

    def tape_read(n_bars):
        _state(root, [make_row(at(9, 31), 7700.0)], n_bars)
        return {q["id"]: q for q in run_once(root, tape_out, tape_doc, True, DAY, lane=TAPE)["questions"]}

    first = tape_read(5)                                                  # bars 09:30..09:34: the 09:35 read
    assert first["q_const"]["answer"]["pick"] == "a" and first["q_every"]["skipped"] == "not on its schedule at the 09:35 ET read"
    second = tape_read(10)                                                # the 09:40 read
    assert second["q_const"]["held_from"] == at(9, 35).isoformat() and second["q_every"]["answer"]["pick"] == "a"

    def live_read(hh, mm):
        rows = [make_row(at(9, 31), 7700.0), make_row(at(hh, mm, ss=10), 7700.0)]
        write_state(root, DAY, rows, bars_from_closes([7700.0] * 390, wick=3.0))
        return {q["id"]: q for q in run_once(root, root / "spx_jev", live_doc, True, DAY)["questions"]}

    at_1002 = live_read(10, 2)
    assert at_1002["q_const"]["held_from"] == at(9, 35).isoformat() and at_1002["q_every"]["answer"]["pick"] == "a"
    assert at_1002["q_gated"]["skipped"] == "asleep: no label family decides its gate"
    assert "held_from" in live_read(11, 32)["q_const"]
    at_1202 = live_read(12, 2)
    assert at_1202["q_const"]["answer"] is None and at_1202["q_const"]["skipped"] == "held from its other lane only until 11:32 ET"


def test_a_day_constant_whose_0935_ask_got_no_answer_is_asked_at_0940_once_and_the_archive_says_why(tmp_path, monkeypatch):
    from spx_jev.ask import load_questions
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(SCHEDULED))
    doc = load_questions(doc_path, TAPE.key)
    root = tmp_path / "state"
    tape_out = root / "spx_jev" / "lanes" / "tape"
    monkeypatch.setattr(service, "send", _sums([]))
    no_answer = "JEV returned HTTP 503 for group g1: upstream connect error"
    sent = []

    def jev(fail):
        def send_all(requests, **kw):
            sent.append(sorted(q for r in requests for q in r["questions"]))
            return {r["id"]: {"error": no_answer} for r in requests} if fail else _answers(requests)
        return send_all

    def tape_read(n_bars, fail=False):
        monkeypatch.setattr(service, "send_all", jev(fail))
        _state(root, [make_row(at(9, 31), 7700.0)], n_bars)
        return {q["id"]: q for q in run_once(root, tape_out, doc, True, DAY, lane=TAPE)["questions"]}

    first = tape_read(5, fail=True)                                        # the 09:35 read: JEV answers nothing
    assert sent == [["q_const"]] and first["q_const"]["answer"] is None
    lost = json.loads((tape_out / "last_asked.json").read_text())["q_const"]
    assert lost == {"lost": {"row_ts": at(9, 35).isoformat(), "why": no_answer}}
    second = tape_read(10)                                                 # 09:40: asked again, beside the question due now
    assert sent[1] == ["q_const", "q_every"] and second["q_const"]["answer"]["pick"] == "a"
    assert "lost" not in json.loads((tape_out / "last_asked.json").read_text())["q_const"]
    third = tape_read(15)                                                  # 09:45: answered at 09:40, so held, never asked again
    assert sent[2] == ["q_every"] and third["q_const"]["held_from"] == at(9, 40).isoformat()
    reads = [json.loads(l) for l in (root / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]
    assert [r["cadence"]["reasked"] for r in reads if r["kind"] == "read"] == [
        {}, {"q_const": {"row_ts": at(9, 35).isoformat(), "why": no_answer}}, {}]


def test_a_live_tape_send_stands_for_the_read_its_job_fired_at_and_a_replay_for_its_bar(tmp_path, monkeypatch):
    """The 09:35 job reading at 09:38 (a bar that came late) is the 09:35 read, so its day constant is asked rather
    than held from a 09:40 read that has not happened; a replay of that bar has only the bar's clock."""
    from spx_jev.ask import load_questions
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(SCHEDULED))
    doc = load_questions(doc_path, TAPE.key)
    root = tmp_path / "state"
    _state(root, [make_row(at(9, 31), 7700.0)], 8)                        # bars 09:30..09:37: the read is stamped 09:38
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    monkeypatch.setattr(service, "today_et", lambda: DAY)
    monkeypatch.setattr(service, "STALE_ROW_SKIP_MIN", 1e9)
    monkeypatch.setattr(service, "now_et", lambda: at(9, 38, ss=5))
    sent = {q["id"]: q for q in run_once(root, tmp_path / "sent", doc, True, None, lane=TAPE)["questions"]}
    assert sent["q_const"]["answer"]["pick"] == "a" and sent["q_every"]["skipped"] == "not on its schedule at the 09:35 ET read"
    replay = {q["id"]: q for q in run_once(root, tmp_path / "replay", doc, True, DAY, lane=TAPE)["questions"]}
    assert replay["q_const"]["skipped"] == "a day constant: asked at 09:35 ET and held" and replay["q_every"]["answer"]["pick"] == "a"


def test_a_grading_error_in_the_close_out_is_logged_and_the_card_and_record_are_still_written(tmp_path, monkeypatch, capsys):
    """A grader that raises in the close-out once escaped with a traceback and no timestamp, and left the card
    unrefreshed and no close-out record: it is logged like run_once's, and the close-out still does both."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "spx_jev" / "lanes" / "tape"
    run_once(state, out, DOC, True, DAY, lane=TAPE)

    def broken(*a, **k):
        raise KeyError("next_10")
    monkeypatch.setattr(service, "grade_run", broken)
    c = service.close_out(state, out, DOC, TAPE, DAY)
    assert c["graded_at"] and c["tally"]["calls"] == 1
    assert json.loads((out / "latest.json").read_text())["graded_at"] == c["graded_at"]
    archived = [json.loads(l) for l in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]
    assert archived[-1]["kind"] == "close_out"
    assert "spx-jev :: grading skipped this close-out: KeyError: 'next_10'" in capsys.readouterr().err


def test_a_tape_read_waits_for_the_bar_that_finishes_at_its_minute(tmp_path):
    """The bars job runs at no fixed second: at the 09:40 fire the file may end at 09:39, and a read on it
    would be stamped 09:39, a minute short of the 10-minute big-print window. The read waits for the bar."""
    from spx_jev.state_builder import make_scene
    state = _state(tmp_path, [make_row(at(9, 31), 7700.0)], 9)           # bars 09:30 .. 09:38: the newest ends 09:39
    all_bars = bars_from_closes([7700.0] * 10, wick=3.0)
    naps = []

    def bars_job_runs(_s):
        naps.append(_s)
        write_state(tmp_path, DAY, [make_row(at(9, 31), 7700.0)], all_bars)
    assert service.wait_for_bar(state, at(9, 40), sleep=bars_job_runs) is True and len(naps) == 1
    assert make_scene(state, DAY, bar_clock=True).now == at(9, 40)
    assert service.wait_for_bar(state, at(9, 41), timeout_s=0, sleep=bars_job_runs) is False   # no bar: the read goes on


def test_the_first_read_after_1001_waits_for_a_row_its_30_minute_window_can_start_from(tmp_path):
    """A row stamped 10:00:51 looks back to 09:30:51, before the first bar finished at 09:31: the 10:02 read
    waits for the scanner's next row rather than lose its 30-minute labels."""
    from spx_jev.state_builder import make_scene
    rows = [make_row(at(9, 31), 7700.0), make_row(at(10, 0, ss=51), 7700.0)]
    state = write_state(tmp_path, DAY, rows, bars_from_closes([7700.0] * 32))
    first = service.first_window_row(at(10, 2))
    assert first == at(10, 1) and service.first_window_row(at(9, 32)) is None and service.first_window_row(at(10, 32)) is None

    def scanner_writes(_s):
        write_state(tmp_path, DAY, [*rows, make_row(at(10, 2, ss=6), 7700.0)], bars_from_closes([7700.0] * 32))
    assert service.wait_for_row(state, first, sleep=scanner_writes) is True
    assert make_scene(state, DAY).now == at(10, 2, ss=6)


def test_a_failing_shadow_grade_costs_neither_the_read_nor_the_close_out(tmp_path, monkeypatch, capsys):
    """The integral grade raising inside the grader: the read still grades and writes its card, and the close-out
    still grades, refreshes the card and archives its record, the job exiting cleanly."""
    def broken(*a, **k):
        raise RuntimeError("no bars")
    working = grade.integral_run
    monkeypatch.setattr(grade, "integral_run", broken)
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "spx_jev" / "lanes" / "tape"
    run_once(state, out, DOC, True, DAY, lane=TAPE)                          # the 10:40 read
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(10, 45, ss=20), 7701.0)], 80)
    c = run_once(state, out, DOC, True, DAY, lane=TAPE)                      # the 10:50 read grades the 10:40 one
    assert c["row_ts"] == at(10, 50).isoformat() and len((out / "grades.jsonl").read_text().splitlines()) == 1
    said = capsys.readouterr().err
    assert "integral shadow grade failed: RuntimeError: no bars" in said and "grading skipped" not in said

    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.setattr(service, "now_et", lambda: datetime.fromisoformat(f"{DAY}T10:52:00").replace(tzinfo=ET))
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(10, 45, ss=20), 7701.0)], 82)
    assert service.main(["--state-dir", str(state), "--lane", "tape"]) == 0
    card = json.loads((out / "latest.json").read_text())
    # the end price graded the first call; with the average price not written, it stands on its end price, so marked,
    # in the card's tally and in the archived close-out's. The 10:50 call's bar has not come, so the card is graded but
    # not closed out
    assert card["graded_at"] and "closed_out_at" not in card
    assert card["tally"] == {"calls": 2, "graded": 1, "right": 1, "passed": 0, "end_price_only": 1, "closed": 0}
    assert card["calls"][1]["end_price"]["outcome"] == "flat" and card["calls"][1]["end_price_only"] and "integral" not in card["calls"][1]
    archived = [json.loads(l) for l in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]
    assert archived[-1]["kind"] == "close_out" and archived[-1]["tally"]["end_price_only"] == 1
    assert not (out / "integral_grades.jsonl").exists() and "integral shadow grade failed" in capsys.readouterr().err
    # the shadow grade back: the next card write finds the line, and the fallback goes
    monkeypatch.setattr(grade, "integral_run", working)
    again = service.close_out(state, out, DOC, TAPE)
    assert again["calls"][1]["integral"]["verdict"] == "right" and "end_price_only" not in again["calls"][1]
    assert again["tally"] == {"calls": 2, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 0}


def test_a_tape_read_mid_window_gives_the_open_call_its_average_so_far(tmp_path, monkeypatch):
    """The 10:45 read lands five minutes into the 10:40 call's ten: the card gives that call its average over the
    five minutes finished, on the flat bars flat, as of 10:45; the call just made has none yet."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "spx_jev" / "lanes" / "tape"
    run_once(state, out, DOC, True, DAY, lane=TAPE)                          # the 10:40 read
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(10, 44, ss=20), 7700.0)], 75)
    c = run_once(state, out, DOC, True, DAY, lane=TAPE)                      # the 10:45 read
    newest, open_one = c["calls"]
    assert c["row_ts"] == at(10, 45).isoformat() and "so_far" not in newest
    assert open_one["so_far"] == {"g": 0.0, "edge": 1.56, "label": "flat", "minutes": 5, "of": 10, "as_of": at(10, 45).isoformat()}
