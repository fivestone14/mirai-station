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


def _sums(seen):
    def send(req, **kw):
        seen.append(req)
        return {"model": "fake-1", "answers": {"next_10": {"type": "choice", "choice": "flat", "confidence": 0.6, "probabilities": FIVE}}}
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
    assert c2["calls"][1]["outcome"] == "flat" and c2["calls"][1]["moved"] == {"realized_points": 0.0, "realized_units": 0.0}
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
    assert c["closed_out_at"] and c["tally"] == {"calls": 1, "graded": 1, "right": 1}
    assert (out / f"{DAY}.jsonl").read_text() == reads_before
    kinds = [json.loads(l)["kind"] for l in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]
    assert kinds == ["read", "grade", "close_out"]                          # the read, its grade at the close-out, the close-out


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
