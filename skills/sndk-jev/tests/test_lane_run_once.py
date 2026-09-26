"""The tape lane end to end with a fake JEV: its own folder, every read fresh, the unit and the band on
every record, the exact-bar grade ten minutes on, and the live folder left byte for byte as it was."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import DAY, at, bars_from_closes, make_row
from sndk_jev import grade, service
from sndk_jev.lane import TAPE
from sndk_jev.service import run_once

DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise, fall, or go nowhere?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}},
    {"id": "g2", "reads": ["context", "price.recent_move"],
     "questions": {"q_two": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Is price moving at all?",
                             "instructions": "Read `price.recent_move`.", "criteria": {"yes": "y", "no": "n", "unsure": "u"}}}},
]}
FIVE = {"down_big": 0.05, "down_small": 0.1, "flat": 0.6, "up_small": 0.15, "up_big": 0.05, "unsure": 0.05}


def _state(tmp_path, rows, n_bars):
    """Bars with a 3-point wick, so the tape unit is measured (6 points a slice) rather than floored."""
    (tmp_path / "sndk_reversion").mkdir(exist_ok=True)
    (tmp_path / "sndk_bars").mkdir(exist_ok=True)
    (tmp_path / "sndk_reversion" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    bars = bars_from_closes([1700.0] * n_bars, wick=3.0)
    (tmp_path / "sndk_bars" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")
    for d in ("2026-09-16", "2026-09-17"):
        (tmp_path / "sndk_bars" / f"{d}.jsonl").write_text("\n".join(json.dumps(b) for b in bars_from_closes([1700.0] * 390, day=d)) + "\n")
    return tmp_path


def _live_folder(state):
    """A live folder with files in it, as on the station, so a lane run has something to leave alone."""
    live = state / "jev"
    (live / "hour").mkdir(parents=True)
    (live / "latest.json").write_text('{"row_ts": "the live card"}')
    (live / "last_asked.json").write_text('{"q_dir": {"row_ts": "x"}}')
    (live / "hour" / f"{DAY}.jsonl").write_text('{"row_ts": "a live sum"}\n')
    (live / "weights.json").write_text('{"questions": {}}')
    return live


def _snapshot(live):
    return {p.relative_to(live): p.read_bytes() for p in live.rglob("*") if p.is_file() and "lanes" not in p.parts}


def _today(monkeypatch, day=DAY):
    """The grader's calendar pinned to noon on ``day``, so a replayed day is graded as today: on a
    finished day the lane closes out every mark past the last bar, which is right on the station
    and wrong for a test that adds bars between two reads."""
    fixed = datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=ZoneInfo("America/New_York"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(grade, "datetime", Clock)


def _answers():
    def send_all(requests, **kw):
        return {r["id"]: {"model": "fake-1", "answers": {
            qid: {"type": "choice", "choice": list(q["criteria"])[0], "confidence": 0.8,
                  "probabilities": {k: (0.85 if i == 0 else 0.05) for i, k in enumerate(q["criteria"])}}
            for qid, q in r["questions"].items()}} for r in requests}
    return send_all


def _sums(seen):
    def send(req, **kw):
        seen.append(req)
        return {"model": "fake-1", "answers": {"next_10": {"type": "choice", "choice": "flat", "confidence": 0.6, "probabilities": FIVE}}}
    return send


def test_a_tape_lane_read_writes_its_own_folder_fresh_every_time_and_leaves_the_live_folder_alone(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)    # bars 09:30 .. 10:39: the read is stamped 10:40
    live = _live_folder(state)
    before = _snapshot(live)
    seen = []
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums(seen))
    out = state / "jev" / "lanes" / "tape"
    c = run_once(state, out, DOC, True, DAY, lane=TAPE)
    assert _snapshot(live) == before                                       # the live folder, byte for byte
    assert c["lane"] == "tape" and c["row_ts"] == at(10, 40).isoformat()   # the bar clock, not the 10:35:10 row
    assert c["ruler"] == {"unit_dollars": 6.0, "unit_sigma": round(6 / 45, 3), "slices_used": 3, "source": "tape"}
    assert c["sent"] and c["fresh"] == 2 and c["held"] == 0 and c["cadence_from"] is None
    assert c["hour"]["pick"] == "flat" and c["hour"]["primary"] == "next_10" and "blend" not in c["hour"]
    assert c["hour"]["views"]["size"]["pick"] == "small" and c["hour"]["used"] == 2
    assert seen[0]["state"]["context"]["unit"] == "one tape unit is $6; flat is within $2 either way (0.35 of a unit); small is $2 to $4; big is more than $4 (0.7 of a unit)"
    assert not (out / "last_asked.json").exists() and not (out / "cadence.json").exists()
    rec = json.loads((out / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert rec["lane"] == "tape" and rec["ruler"] == c["ruler"] and rec["held"] == {} and rec["row_ts"] == c["row_ts"]
    hour = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert hour["lane"] == "tape" and hour["ruler"] == c["ruler"] and hour["spot"] == 1700.0
    assert hour["band"] == {"flat_dollars": 2.1, "big_dollars": 4.2, "flat_units": 0.35, "big_units": 0.7}
    assert hour["by"]["next_10"]["views"]["direction"]["probabilities"]["unsure"] == 0.05
    assert json.loads((out / "latest.json").read_text())["lane"] == "tape"
    weights = json.loads((out / "weights.json").read_text())
    assert weights["lane"] == "tape" and weights["graded_runs"] == 0 and list(weights["sums"]) == ["next_10"]

    # ten minutes on: the 10:40 read's mark has its exact bar, and this read is as fresh as the first
    _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0), make_row(at(10, 45, ss=20), 1701.0)], 80)   # bars to 10:49
    c2 = run_once(state, out, DOC, True, DAY, lane=TAPE)
    assert c2["row_ts"] == at(10, 50).isoformat() and c2["fresh"] == 2 and c2["held"] == 0
    assert _snapshot(live) == before
    grades = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines() if l.strip()]
    assert len(grades) == 1 and grades[0]["horizons"] == ["next_10"] and grades[0]["band"] == "flat" and grades[0]["size"] == "small"
    assert grades[0]["realized_dollars"] == 0.0 and grades[0]["fresh"] == {"q_dir": "rising", "q_two": "yes"}
    weights = json.loads((out / "weights.json").read_text())
    assert weights["graded_runs"] == 1 and weights["questions"]["q_dir"]["n"] == 1 and weights["passed"] == 0


def test_a_tape_lane_run_without_a_folder_of_its_own_refuses(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums([]))
    with pytest.raises(ValueError, match="no folder of its own"):
        run_once(state, None, DOC, True, DAY, lane=replace(TAPE, out_dir=None))
    with pytest.raises(ValueError, match="must not write into"):
        run_once(state, state / "jev", DOC, True, DAY, lane=TAPE)
    assert not (state / "jev").exists()


def test_the_cli_picks_the_lane_and_its_folder(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert service.main(["--state-dir", str(state), "--lane", "tape", "--day", DAY]) == 0
    c = json.loads((state / "jev" / "lanes" / "tape" / "latest.json").read_text())
    assert c["lane"] == "tape" and c["ruler"]["source"] == "tape" and not c["sent"]
    assert not (state / "jev" / "latest.json").exists()
    with pytest.raises(ValueError, match="must not write into"):
        service.main(["--state-dir", str(state), "--lane", "tape", "--out-dir", str(state / "jev")])


def test_the_card_carries_the_calls_in_play_with_their_grades_and_the_days_tally(tmp_path, monkeypatch):
    """Two reads ten minutes apart: the second card lists both calls newest first, the first graded at its
    mark (the read's minute plus 10) and the second still open, and the tally counts them; the lane's card
    also carries its schedule, so the phone knows when it hands back to the live reads."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "jev" / "lanes" / "tape"
    c1 = run_once(state, out, DOC, True, DAY, lane=TAPE)
    assert c1["calls"] == [{"read": at(10, 40).isoformat(), "mark": at(10, 50).isoformat(), "minutes": 10, "pick": "flat", "p": 0.6, "odds": FIVE}]
    assert c1["tally"] == {"calls": 1, "graded": 0, "right": 0}
    assert c1["schedule"] == {"reads": list(TAPE.schedule), "looks_ahead_min": 10, "close_out": "10:42"}
    assert c1["marks"] == {"next_10": at(10, 50).isoformat()}
    _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0), make_row(at(10, 45, ss=20), 1701.0)], 80)
    c2 = run_once(state, out, DOC, True, DAY, lane=TAPE)
    assert [c["read"] for c in c2["calls"]] == [at(10, 50).isoformat(), at(10, 40).isoformat()]
    assert c2["calls"][1] == {"read": at(10, 40).isoformat(), "mark": at(10, 50).isoformat(), "minutes": 10, "pick": "flat",
                              "p": 0.6, "odds": FIVE, "outcome": "flat", "hit": True,
                              "moved": {"realized_dollars": 0.0, "realized_units": 0.0}}
    assert "outcome" not in c2["calls"][0] and c2["tally"] == {"calls": 2, "graded": 1, "right": 1}


def test_the_close_out_grades_the_last_calls_and_asks_jev_nothing(tmp_path, monkeypatch):
    """The job's fire after the last read: every passed mark is graded and the card's calls and tally are
    refreshed; no read is written and nothing is sent."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums([]))
    out = state / "jev" / "lanes" / "tape"
    run_once(state, out, DOC, True, DAY, lane=TAPE)                           # the 10:40 read; its mark is 10:50
    reads_before = (out / f"{DAY}.jsonl").read_text()

    def refuse(*a, **k):
        raise AssertionError("the close-out asked JEV")
    monkeypatch.setattr(service, "send_all", refuse)
    monkeypatch.setattr(service, "send", refuse)
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    closing = datetime.fromisoformat(f"{DAY}T10:52:00").replace(tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(service, "now_et", lambda: closing)
    _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 82)              # bars to 10:51: the 10:50 mark has its bar
    assert service.main(["--state-dir", str(state), "--lane", "tape"]) == 0
    c = json.loads((out / "latest.json").read_text())
    assert c["closed_out_at"] and c["row_ts"] == at(10, 40).isoformat()
    assert c["calls"][0]["outcome"] == "flat" and c["tally"] == {"calls": 1, "graded": 1, "right": 1}
    assert (out / f"{DAY}.jsonl").read_text() == reads_before                # no read was recorded
    # before the close-out time the same command reads as usual, and a lane that did not read today closes nothing
    monkeypatch.setattr(service, "now_et", lambda: closing.replace(day=closing.day + 1))
    assert service.close_out(state, out, DOC, TAPE) is None
