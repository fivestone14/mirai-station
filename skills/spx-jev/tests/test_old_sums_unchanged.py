"""The end-price sums, kept beside the call, stay exactly as they were: asking JEV the average-price question beside them must not
move a byte of what the old sums send, grade or teach, whatever the new question's reply holds. One fixed read on each
lane, run end to end with a fake JEV, against the files the code wrote for the same fixture before the average-price
question existed (golden/old_sums.json): the sums request, grades.jsonl, weights.json, the learning loop's pool state
and the time-of-day counts (clock_days.json), the live read's sums blended with them. With the live lane's average-price
loop switched on, weights.json carries that loop's report beside the rest under ``pool_integral`` (grade.weights_from):
without it, the file is the old path's to the byte."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conftest import DAY, PRIOR_DAYS, at, bars_from_closes, flat_bars, make_row, write_prior_rows, write_state
from spx_jev import grade, premarket, service, story
from spx_jev.ask import send_all as real_send_all
from spx_jev.lane import LIVE, PREMARKET, TAPE
from spx_jev.service import run_once
from test_premarket import DOC as PREMARKET_DOC, _station

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
                "probabilities": {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.4, "up_big": 0.1, "unsure": 0.05}},
    "open_10": {"type": "choice", "choice": "flat", "confidence": 0.6, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.15, "unsure": 0.05}},
    "open_30": {"type": "choice", "choice": "up", "confidence": 0.5, "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.15, "unsure": 0.05}}}
AVERAGE = {"type": "choice", "choice": "down", "confidence": 0.7, "probabilities": {"up": 0.2, "flat": 0.2, "down": 0.6}}
# what the average-price request may get back instead of an answer: every one must leave the old sums as they were
BAD_REPLIES = {
    "error": {"error": "JEV returned HTTP 529 for group average after 3 tries: overloaded"},
    "answers is text": {"model": "fake-1", "answers": "garbled"},
    "null odds": {"model": "fake-1", "answers": {q: {"type": "choice", "choice": None, "probabilities": {"up": None, "flat": "0.5"}}
                                                 for q in ("average_30", "average_10", "open_average_30")}},
}
FIXED_MTIME = 1_789_000_000         # every input file's clock, so the time-of-day counts' file prints are the same on every run


def _answers(requests, **kw):
    def sender(r, api_key=None, timeout=10.0, deadline=None):
        return {"model": "fake-1", "answers": {
            qid: {"type": "choice", "choice": list(q["criteria"])[0], "confidence": 0.8,
                  "probabilities": {k: (0.85 if i == 0 else 0.05) for i, k in enumerate(q["criteria"])}}
            for qid, q in r["questions"].items()}}
    return real_send_all(requests, api_key="fake", sender=sender)


def _sums(average_reply=None):
    """The old sums' request is answered as before; any other request (the average-price sum's) gets ``average_reply``,
    or its own answer."""
    def send(req, **kw):
        if req["id"] != "hour" and average_reply is not None:
            return average_reply
        return {"model": "fake-1", "answers": {qid: (OLD_SUMS[qid] if qid in OLD_SUMS else AVERAGE) for qid in req["questions"]}}
    return send


def _files(out: Path, clock: bool = False) -> dict[str, str]:
    rec = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    files = {"request": json.dumps(rec["request"], ensure_ascii=False, sort_keys=True)}
    for name in ("grades.jsonl", "weights.json", "pool_30.json", "pool_60.json") + (("clock_days.json",) if clock else ()):
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


def _pin_clocks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def _bars_file(state: Path, closes: list[float]) -> None:
    path = state / "spx_jev" / "bars" / f"{DAY}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(b) + "\n" for b in bars_from_closes(closes, wick=3.0)))


def run_fixture(tmp_path, monkeypatch, average_reply=None) -> dict[str, dict[str, str]]:
    """The live lane's 10:02 read, blended with the time-of-day odds of ten prior sessions read every five minutes; the
    opening lane's 10:40 read; and the premarket lane's 09:28 read in a station of its own. Each is graded once the
    day's bars are in. ``average_reply`` is what the average-price request gets back instead of an answer."""
    _today(monkeypatch)
    monkeypatch.setattr(service, "send_all", _answers)
    monkeypatch.setattr(service, "send", _sums(average_reply))
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    write_prior_rows(tmp_path, {d: [make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 7700.0) for m in range(1, 390, 5)] for d in prior})
    state = write_state(tmp_path, DAY, [make_row(at(10, 2, ss=10), CLOSES[31])], bars_from_closes(CLOSES, wick=3.0), prior)
    _pin_clocks(tmp_path)
    live = state / "spx_jev"
    run_once(state, live, DOC, True, DAY)
    # the opening lane reads on the newest finished bar: its 10:40 read sees the bars to 10:39, and is graded once the rest are in
    _bars_file(state, CLOSES[:70])
    tape = state / "spx_jev" / "lanes" / "tape"
    run_once(state, tape, DOC, True, DAY, lane=TAPE)
    _bars_file(state, CLOSES)
    grade.run(state, tape, grade.live_options(DOC), lane=TAPE)
    # the premarket lane, before the open: its sums are graded from the settled open once the day's bars are in
    monkeypatch.setattr(premarket, "send_all", _answers)
    monkeypatch.setattr(premarket, "send", _sums(average_reply))
    monkeypatch.setattr(story, "releases", lambda day: [])
    station = _station(tmp_path / "premarket")
    pre = PREMARKET.folder(station)
    premarket.run_checkpoint(station, pre, PREMARKET_DOC, True, at(9, 28), "09:28", save=False)
    _bars_file(station, CLOSES)
    grade.run(station, pre, grade.live_options(PREMARKET_DOC), lane=PREMARKET)
    return {"live": _files(live, clock=True), "tape": _files(tape), "premarket": _files(pre)}


def _old_path(weights: str, lane: str) -> str:
    """weights.json less what has been added beside the old path since, written as the grader writes it: the live
    lane's average-price loop report, and each sum's hit rate on its committed calls."""
    w = json.loads(weights)
    if lane == "live" and LIVE.integral_loop:
        assert w.pop("pool_integral")["method"] == "pool_v1_integral"
    for s in w["sums"].values():
        del s["committed_calls"], s["committed_hit_rate"]
    return json.dumps(w, ensure_ascii=False, indent=1)


def _same_as_before(got: dict) -> None:
    want = json.loads(GOLDEN.read_text())
    for lane in ("live", "tape", "premarket"):
        assert set(got[lane]) == set(want[lane]), lane
        for name, text in want[lane].items():
            if name == "weights.json":
                assert _old_path(got[lane][name], lane) == text, f"{lane} {name} moved"
                continue
            assert got[lane][name] == text, f"{lane} {name} moved"


def test_the_old_sums_request_grades_weights_pool_and_clock_are_byte_identical_to_before(tmp_path, monkeypatch):
    got = run_fixture(tmp_path, monkeypatch)
    _same_as_before(got)
    # the fixture is a real one: every sum was graded on each lane, and the live read's sums were blended
    assert {"next_30", "next_60"} <= {h for line in got["live"]["grades.jsonl"].splitlines() for h in json.loads(line).get("horizons", [])}
    assert "next_10" in got["tape"]["grades.jsonl"] and "open_30" in got["premarket"]["grades.jsonl"] and "pool_30.json" in got["live"]
    assert "clock_brier" in got["live"]["grades.jsonl"] and json.loads(got["live"]["clock_days.json"])["days"]


@pytest.mark.parametrize("reply", BAD_REPLIES.values(), ids=list(BAD_REPLIES))
def test_a_failed_or_garbled_average_reply_leaves_the_old_sums_and_the_read_whole(tmp_path, monkeypatch, reply):
    """The read goes on: the old sums' files are the same bytes, the hour record keeps the end-price sum's pick and
    ``by``, the card says the average-price sum got no readable answer, and the call is the end-price sum's."""
    got = run_fixture(tmp_path, monkeypatch, reply)
    _same_as_before(got)
    for folder, lane in ((tmp_path / "spx_jev", service.LIVE), (tmp_path / "spx_jev" / "lanes" / "tape", TAPE),
                         (PREMARKET.folder(tmp_path / "premarket"), PREMARKET)):
        rec = json.loads((folder / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
        # the end-price sum as it would have been: JEV's own pick, blended on the live lane
        own = rec["by"][lane.primary].get("jev", rec["by"][lane.primary])
        assert own["pick"] == OLD_SUMS[lane.primary]["choice"] and set(rec["by"]) == set(lane.horizons) and rec["pick"], lane.name
        assert rec["average"]["error"] and "pick" not in rec["average"], lane.name
        card = json.loads((folder / "latest.json").read_text())
        assert card["hour"]["average"]["error"] == rec["average"]["error"], lane.name
        [call] = service.day_calls(folder, DAY, lane)
        assert call["sum"] == lane.primary and call["pick"] == rec["pick"] and call["average_missing"], lane.name
