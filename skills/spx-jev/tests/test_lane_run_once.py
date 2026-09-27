"""The opening lane end to end with a fake JEV: its own folder, every read fresh, the unit and the band on
every record, the exact-bar grade ten minutes on, the close-out, and the live folder left as it was."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import DAY, at, bars_from_closes, make_row, write_state
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
    """Bars with a 3-point wick, so the tape unit is measured (6 points a slice) rather than floored."""
    prior = {d: bars_from_closes([7700.0] * 390, day=d) for d in ("2026-09-16", "2026-09-17")}
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
    assert c["ruler"] == {"unit_points": 6.0, "unit_sigma": 0.08, "slices_used": 3, "source": "tape"}
    assert c["band"] == {"flat_points": round(TAPE_FLAT_UNITS * 6, 2), "big_points": round(TAPE_BIG_UNITS * 6, 2),
                         "flat_units": TAPE_FLAT_UNITS, "big_units": TAPE_BIG_UNITS}
    assert c["hour"]["primary"] == "next_10" and "blend" not in c["hour"] and c["hour"]["views"]["size"]["pick"] == "small"
    assert seen[0]["state"]["context"]["unit"].startswith("one tape unit is 6.0 points;")
    assert c["schedule"]["reads"][0] == at(9, 35).isoformat() and c["schedule"]["close_out"] == at(10, 42).isoformat()
    assert not (out / "last_asked.json").exists()

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
