"""The whole read, end to end with a fake JEV: the card, the held answers, the sums, the grades."""
from __future__ import annotations

import json

import pytest

from conftest import DAY, at, flat_bars, make_row
from sndk_jev import service
from sndk_jev.service import run_once

DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise, fall, or go nowhere?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}},
    {"id": "g2", "reads": ["context", "price.recent_move"],
     "questions": {"q_two": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Is price moving at all?",
                             "instructions": "Read `price.recent_move`.", "criteria": {"yes": "y", "no": "n", "unsure": "u"}}}},
    {"id": "news", "reads": ["context"],
     "questions": {"q_dark": {"status": "dark", "viewpoint": "news", "type": "choice", "ask": "Any news?",
                              "instructions": "Read `news.headline`.", "criteria": {"yes": "y", "unsure": "u"}}}},
]}
CANARY = "apikey_" + "c" * 24 + "_" + "d" * 24


def _state(tmp_path, rows, n_bars):
    (tmp_path / "sndk_reversion").mkdir(exist_ok=True)
    (tmp_path / "sndk_bars").mkdir(exist_ok=True)
    (tmp_path / "sndk_reversion" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "sndk_bars" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(b) for b in flat_bars(n_bars)) + "\n")
    for d in ("2026-09-16", "2026-09-17"):
        (tmp_path / "sndk_bars" / f"{d}.jsonl").write_text("\n".join(json.dumps(b) for b in flat_bars(390, day=d)) + "\n")
    return tmp_path


def _answers(failing=()):
    """The real send_all over a fake JEV: a failing group raises the way the network does, with the
    key in the message, so the scrub in send_all is what the test exercises."""
    from sndk_jev.ask import send_all as real_send_all

    def sender(r, api_key=None, timeout=10.0):
        if r["id"] in failing:
            raise TimeoutError(f"handshake timed out, key {CANARY} not at fault")
        return {"model": "fake-1", "answers": {
            qid: {"type": "choice", "choice": list(q["criteria"])[0], "confidence": 0.8,
                  "probabilities": {k: (0.85 if i == 0 else 0.05) for i, k in enumerate(q["criteria"])}}
            for qid, q in r["questions"].items()}}
    return lambda requests, **kw: real_send_all(requests, api_key=CANARY, sender=sender)


def _sums(req, **kw):
    return {"model": "fake-1", "answers": {
        "next_30": {"type": "choice", "choice": "flat", "confidence": 0.7, "probabilities": {"up": 0.1, "flat": 0.8, "down": 0.05, "unsure": 0.05}},
        "next_60": {"type": "choice", "choice": "flat", "confidence": 0.5, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.1, "unsure": 0.1}}}}


def test_a_read_answers_holds_sums_and_grades(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)     # bars 09:30..10:39; 65 finished by 10:35:10
    out = state / "jev"
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, DOC, True, DAY)
    assert c["sent"] and c["fresh"] == 2 and c["held"] == 0 and c["freshness"]["stale"] is True   # a 2026 row is old by now
    q = {e["id"]: e for e in c["questions"]}
    assert q["q_dir"]["answer"]["pick"] == "rising" and q["q_two"]["answer"]["pick"] == "yes" and "q_dark" not in q
    assert [d["id"] for d in c["dark"]] == ["q_dark"]
    assert c["hour"]["pick"] == "flat" and c["hour"]["used"] == 2 and c["hour"]["missing"] == 0
    last = json.loads((out / "last_asked.json").read_text())
    assert set(last) == {"q_dir", "q_two"} and last["q_dir"]["moved"] == 0.0
    assert len((out / f"{DAY}.jsonl").read_text().splitlines()) == 1
    hour_line = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert hour_line["fresh"] == {"q_dir": "rising", "q_two": "yes"} and hour_line["by"]["next_60"]["pick"] == "flat"
    assert not (out / "grades.jsonl").exists() or (out / "grades.jsonl").read_text() == ""   # the 30-minute mark is still ahead

    # the next read, 30 minutes on: group g2 fails, so q_two keeps its last answer, tagged with when it was given
    rows = [make_row(at(10, 35, ss=10), 1700.0), make_row(at(11, 5, ss=20), 1701.0)]
    _state(tmp_path, rows, 101)                                                  # bars 09:30..11:10
    monkeypatch.setattr(service, "send_all", _answers(failing={"g2"}))
    c2 = run_once(state, out, DOC, True, DAY)
    q2 = {e["id"]: e for e in c2["questions"]}
    assert q2["q_dir"]["answer"]["pick"] == "rising" and "held_from" not in q2["q_dir"]
    assert q2["q_two"]["held_from"] == "10:35" and q2["q_two"]["answer"]["pick"] == "yes"
    assert c2["fresh"] == 1 and c2["held"] == 1
    hour2 = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[1])
    assert hour2["fresh"] == {"q_dir": "rising"} and "(held since 10:35, not re-asked)" in hour2["sentences"]["q_two"]
    grades = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines() if l.strip()]
    assert len(grades) == 1 and grades[0]["horizons"] == ["next_30"] and grades[0]["band"] == "flat"   # the 10:35 read's 30-minute mark passed
    weights = json.loads((out / "weights.json").read_text())
    assert weights["graded_runs"] == 1 and weights["questions"]["q_dir"]["n"] == 1
    # the key in a group's error never reaches a file
    for p in out.rglob("*"):
        if p.is_file():
            assert CANARY not in p.read_text(), p


def test_a_sent_run_refuses_a_row_from_another_day(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    with pytest.raises(RuntimeError, match="no diary row for today yet"):
        run_once(state, state / "jev", DOC, True, None)
    assert not (state / "jev" / "latest.json").exists()


def test_an_unsent_run_says_why_and_holds_nothing(tmp_path):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 1700.0)], 70)
    c = run_once(state, state / "jev", DOC, False, DAY, unsent_reason="not sent: no key on this machine")
    assert not c["sent"] and c["fresh"] == 0 and c["held"] == 0 and c["hour"] is None
    assert all(e["skipped"] == "not sent: no key on this machine" for e in c["questions"])
    assert not (state / "jev" / "last_asked.json").exists()


def test_the_sum_is_blended_with_the_time_of_day_once_there_are_enough_sessions(tmp_path, monkeypatch):
    """With ten prior sessions the card's sum is JEV's sum blended with the clock's odds, JEV's own
    sum rides beside it, and the hour record the grader reads carries both parts."""
    from sndk_jev.clock import JEV_SHARE, MIN_SESSIONS
    state = _state(tmp_path, [make_row(at(12, 2, ss=10), 1700.0)], 160)
    days = [f"2026-09-{d:02d}" for d in range(1, MIN_SESSIONS + 1)]
    for d in days:
        (state / "sndk_bars" / f"{d}.jsonl").write_text("\n".join(json.dumps(b) for b in flat_bars(390, day=d)) + "\n")
        rows = [make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 1700.0) for m in range(0, 390, 5)]
        (state / "sndk_reversion" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = state / "jev"
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, DOC, True, DAY)
    h = c["hour"]
    assert h["blend"]["used"] is True and h["blend"]["phase"] == "lunch" and h["blend"]["sessions"] >= MIN_SESSIONS
    assert c["session"] == {"close": "16:00", "last_read": "15:32"}           # the phone's clock words follow the real close
    assert h["jev"]["probabilities"]["flat"] == 0.8                     # the fake JEV's own sum, untouched
    assert h["clock"]["probabilities"]["flat"] == 1.0                    # every prior session was flat
    assert h["probabilities"]["flat"] == JEV_SHARE * 0.8 + (1 - JEV_SHARE) * 1.0
    line = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[-1])
    assert line["by"]["next_30"]["jev"]["pick"] == "flat" and "clock" in line["by"]["next_60"]
    assert (out / "clock_days.json").is_file()


def _live(tmp_path, monkeypatch, row_age_min, book_age_min, book_time=True):
    """Today's rows and bars under a fixed noon-ET wall clock (so the test never straddles midnight):
    the newest row ``row_age_min`` old by that clock and its options book ``book_age_min`` old."""
    from datetime import datetime as real_dt, timedelta
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    fixed = real_dt(2026, 9, 25, 12, 0, tzinfo=et)

    class Clock(real_dt):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(service, "datetime", Clock)
    day = fixed.date().isoformat()
    row = make_row(fixed - timedelta(minutes=row_age_min), 1700.0)
    if book_time:
        row["meta"]["book_asof"] = (fixed - timedelta(minutes=book_age_min)).isoformat()
    else:
        row["meta"].pop("book_asof")
    (tmp_path / "sndk_reversion").mkdir(exist_ok=True)
    (tmp_path / "sndk_bars").mkdir(exist_ok=True)
    (tmp_path / "sndk_reversion" / f"{day}.jsonl").write_text(json.dumps(row) + "\n")
    (tmp_path / "sndk_bars" / f"{day}.jsonl").write_text("\n".join(json.dumps(b) for b in flat_bars(390, day=day)) + "\n")
    return tmp_path


def test_the_documented_lines_are_six_minutes():
    from sndk_jev.state_builder import STALE_BOOK_MIN
    assert service.STALE_ROW_SKIP_MIN == 6 and STALE_BOOK_MIN == 6


def test_a_live_read_on_a_stalled_scanner_is_skipped_and_the_card_stays(tmp_path, monkeypatch):
    """Past the stale-row line nothing is sent, summed or graded, and the last card is left as it was."""
    state = _live(tmp_path, monkeypatch, row_age_min=7, book_age_min=10)
    (state / "jev").mkdir()
    (state / "jev" / "latest.json").write_text('{"questions": [], "row_ts": "the last card"}')
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    with pytest.raises(service.NoRowYet, match="7.0 minutes old"):
        run_once(state, state / "jev", DOC, True, None)
    assert json.loads((state / "jev" / "latest.json").read_text())["row_ts"] == "the last card"
    assert not (state / "jev" / "hour").exists()


def test_a_live_read_leaves_out_the_labels_of_a_stale_book(tmp_path, monkeypatch):
    """A fresh row on a book past the stale-book line: the book's labels are omitted with the reason."""
    state = _live(tmp_path, monkeypatch, row_age_min=1, book_age_min=10)
    c = run_once(state, state / "jev", DOC, False, None)
    assert "gex.weight_side" in c["omitted"] and "past the 6-minute line" in c["omitted"]["gex.weight_side"]
    fresh = _live(tmp_path / "b", monkeypatch, row_age_min=1, book_age_min=2) if (tmp_path / "b").mkdir() is None else None
    assert "gex.weight_side" not in run_once(fresh, fresh / "jev", DOC, False, None)["omitted"]


def test_a_live_row_with_no_book_time_still_writes_its_card(tmp_path, monkeypatch):
    state = _live(tmp_path, monkeypatch, row_age_min=1, book_age_min=0, book_time=False)
    c = run_once(state, state / "jev", DOC, False, None)
    assert "of unknown age" in c["omitted"]["gex.weight_side"] and (state / "jev" / "latest.json").exists()


def test_a_replay_is_never_gated_by_the_wall_clock(tmp_path, monkeypatch):
    """--day replays a past row as it was: its book is hours old by today's clock and is still described."""
    state = _state(tmp_path, [make_row(at(11, 0, ss=10), 1700.0)], 100)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, state / "jev", DOC, True, DAY)
    assert not any("past the 6-minute line" in str(v) for v in c["omitted"].values())


def test_a_read_before_a_scheduled_close_event_carries_the_tag(tmp_path, monkeypatch):
    """2026-09-18 closed on quarterly expiry and the S&P rebalance: the 15:31 read is tagged, inside 30
    minutes, in the per-run record, the sum record and the card, and no request to JEV carries it."""
    state = _state(tmp_path, [make_row(at(15, 31, ss=10), 1700.0)], 390)
    seen = []
    real = _answers()
    monkeypatch.setattr(service, "send_all", lambda reqs, **kw: seen.extend(reqs) or real(reqs, **kw))
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, state / "jev", DOC, True, DAY)
    assert c["event"]["within_30"] is True and "at 16:00" in c["event"]["sentence"]
    line = json.loads((state / "jev" / "hour" / f"{DAY}.jsonl").read_text().splitlines()[-1])
    rec = json.loads((state / "jev" / f"{DAY}.jsonl").read_text().splitlines()[-1])
    assert line["event"]["within_30"] is True and rec["event"]["within_30"] is True
    assert seen and not any("event" in json.dumps(r["state"]) or "scheduled" in json.dumps(r["state"]) for r in seen)
