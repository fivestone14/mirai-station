"""The end-price sums stay in shadow exactly as they were: asking JEV the average-price question beside them must not
move a byte of what the old sums send, grade or teach. One fixed read on each lane that sums on a diary row, run end to
end with a fake JEV, against the files the code wrote for the same fixture before the average-price question existed
(golden/old_sums.json): the sums request, grades.jsonl, weights.json and the learning loop's pool state."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from conftest import DAY, PRIOR_DAYS, at, bars_from_closes, flat_bars, make_row, write_prior_rows, write_state
from spx_jev import grade, service
from spx_jev.ask import send_all as real_send_all
from spx_jev.lane import TAPE
from spx_jev.service import run_once

GOLDEN = Path(__file__).resolve().parent / "golden" / "old_sums.json"
DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise, fall, or go nowhere?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}}]}
# a day that climbs for its first hour and a half, then gives some of it back, so the sums' windows land in different bands
CLOSES = [round(7700.0 + 30.0 * math.sin(i / 40.0) + 0.05 * i, 2) for i in range(390)]
OLD_SUMS = {
    "next_30": {"type": "choice", "choice": "up", "confidence": 0.6, "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.1, "unsure": 0.1}},
    "next_60": {"type": "choice", "choice": "flat", "confidence": 0.5, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.1, "unsure": 0.1}},
    "next_10": {"type": "choice", "choice": "up_small", "confidence": 0.5,
                "probabilities": {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.4, "up_big": 0.1, "unsure": 0.05}}}
AVERAGE = {"type": "choice", "choice": "down", "confidence": 0.7, "probabilities": {"up": 0.2, "flat": 0.2, "down": 0.6}}


def _answers(requests, **kw):
    def sender(r, api_key=None, timeout=10.0, deadline=None):
        return {"model": "fake-1", "answers": {
            qid: {"type": "choice", "choice": list(q["criteria"])[0], "confidence": 0.8,
                  "probabilities": {k: (0.85 if i == 0 else 0.05) for i, k in enumerate(q["criteria"])}}
            for qid, q in r["questions"].items()}}
    return real_send_all(requests, api_key="fake", sender=sender)


def _sums(req, **kw):
    """The old sums' request is answered as before; any other request (the average-price sum's) gets its own answer."""
    return {"model": "fake-1", "answers": {qid: (OLD_SUMS[qid] if qid in OLD_SUMS else AVERAGE) for qid in req["questions"]}}


def _files(out: Path) -> dict[str, str]:
    rec = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    files = {"request": json.dumps(rec["request"], ensure_ascii=False, sort_keys=True)}
    for name in ("grades.jsonl", "weights.json", "pool_30.json", "pool_60.json"):
        if (out / name).exists():
            files[name] = (out / name).read_text()
    return files


def _today(monkeypatch):
    """The grader's calendar pinned to noon on DAY, so a mark past the bars on file waits for them."""
    fixed = datetime.fromisoformat(f"{DAY}T12:00:00").replace(tzinfo=ZoneInfo("America/New_York"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(grade, "datetime", Clock)


def run_fixture(tmp_path, monkeypatch) -> dict[str, dict[str, str]]:
    """The live lane's 10:02 read and the opening lane's 10:40 read of DAY, each graded once the day's bars are in."""
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums)
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    write_prior_rows(tmp_path, {d: [make_row(at(9, 31, day=d), 7700.0)] for d in prior})
    state = write_state(tmp_path, DAY, [make_row(at(10, 2, ss=10), CLOSES[31])], bars_from_closes(CLOSES, wick=3.0), prior)
    live = state / "spx_jev"
    run_once(state, live, DOC, True, DAY)
    # the opening lane reads on the newest finished bar: its 10:40 read sees the bars to 10:39, and is graded once the rest are in
    (state / "spx_jev" / "bars" / f"{DAY}.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bars_from_closes(CLOSES[:70], wick=3.0)))
    tape = state / "spx_jev" / "lanes" / "tape"
    run_once(state, tape, DOC, True, DAY, lane=TAPE)
    (state / "spx_jev" / "bars" / f"{DAY}.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bars_from_closes(CLOSES, wick=3.0)))
    grade.run(state, tape, grade.live_options(DOC), lane=TAPE)
    return {"live": _files(live), "tape": _files(tape)}


def test_the_old_sums_request_grades_weights_and_pool_are_byte_identical_to_before(tmp_path, monkeypatch):
    got = run_fixture(tmp_path, monkeypatch)
    want = json.loads(GOLDEN.read_text())
    for lane in ("live", "tape"):
        assert set(got[lane]) == set(want[lane]), lane
        for name, text in want[lane].items():
            assert got[lane][name] == text, f"{lane} {name} moved"
    # the fixture is a real one: both sums were graded, on each lane
    assert {"next_30", "next_60"} <= {h for line in got["live"]["grades.jsonl"].splitlines() for h in json.loads(line).get("horizons", [])}
    assert "next_10" in got["tape"]["grades.jsonl"] and "pool_30.json" in got["live"]
