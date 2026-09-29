"""The whole read, end to end with a fake JEV: the card, the held answers, the sums, the grades, the times."""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from conftest import DAY, PRIOR_DAYS, at, flat_bars, make_row, write_prior_rows, write_state
from spx_jev import archive, service
from spx_jev.service import run_once

DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise, fall, or go nowhere?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}},
    {"id": "g2", "reads": ["context", "price.recent_move"],
     "questions": {"q_two": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Is price moving at all?",
                             "instructions": "Read `price.recent_move`.", "criteria": {"yes": "y", "no": "n", "unsure": "u"}}}},
]}
CANARY = "apikey_" + "c" * 24 + "_" + "d" * 24
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?[+-]\d\d:\d\d$")
BARE_CLOCK = re.compile(r"^\d\d:\d\d$")


def _state(tmp_path, rows, n_bars):
    """The day's rows and bars over ten flat prior sessions, each with a trusted morning ruler, so the moves the
    questions read are ranked against the same minute."""
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    write_prior_rows(tmp_path, {d: [make_row(at(9, 31, day=d), 7700.0)] for d in prior})
    return write_state(tmp_path, DAY, rows, flat_bars(n_bars), prior)


def _answers(failing=()):
    """The real send_all over a fake JEV: a failing group raises the way the network does, with the
    key in the message, so the scrub in send_all is what the test exercises."""
    from spx_jev.ask import send_all as real_send_all

    def sender(r, api_key=None, timeout=10.0, deadline=None):
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


def _times(card: dict):
    """Every string on the card that looks like a clock, with where it sits."""
    def walk(v, where):
        if isinstance(v, dict):
            for k, x in v.items():
                yield from walk(x, f"{where}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                yield from walk(x, f"{where}[{i}]")
        elif isinstance(v, str):
            yield where, v
    return list(walk(card, "card"))


def test_a_read_answers_holds_sums_and_grades(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, DOC, True, DAY)
    assert c["symbol"] == "SPX" and c["sent"] and c["fresh"] == 2 and c["held"] == 0 and "book_asof" not in c
    q = {e["id"]: e for e in c["questions"]}
    assert q["q_dir"]["answer"]["pick"] == "rising" and q["q_two"]["answer"]["pick"] == "yes"
    assert c["hour"]["pick"] == "flat" and c["hour"]["used"] == 2 and c["hour"]["missing"] == 0
    assert json.loads((out / "last_asked.json").read_text())["q_dir"]["moved"] == 0.0

    # thirty minutes on: group g2 fails, so q_two keeps its last answer, stamped with the read it was given on
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(11, 5, ss=20), 7701.0)], 101)
    monkeypatch.setattr(service, "send_all", _answers(failing={"g2"}))
    c2 = run_once(state, out, DOC, True, DAY)
    q2 = {e["id"]: e for e in c2["questions"]}
    assert q2["q_two"]["held_from"] == at(10, 35, ss=10).isoformat() and q2["q_two"]["answer"]["pick"] == "yes"
    hour2 = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[1])
    assert hour2["fresh"] == {"q_dir": "rising"} and "(held since 10:35 ET, not re-asked)" in hour2["sentences"]["q_two"]
    rec2 = json.loads((out / f"{DAY}.jsonl").read_text().splitlines()[1])
    assert rec2["held"] == {"q_two": at(10, 35, ss=10).isoformat()}
    grades = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines() if l.strip()]
    assert len(grades) == 1 and grades[0]["horizons"] == ["next_30"] and grades[0]["band"] == "flat"
    weights = json.loads((out / "weights.json").read_text())
    assert weights["method"] == "pool_v1" and weights["questions"]["q_dir"] == {**weights["questions"]["q_dir"], "weight": 1.0, "n": 1}
    for p in out.rglob("*"):
        if p.is_file():
            assert CANARY not in p.read_text(), p


def test_a_score_answer_reaches_the_card_the_store_and_the_sums_by_its_option_names(tmp_path, monkeypatch):
    """JEV keys a score by level number with its criteria words as a legend; the read names the levels as the
    question's options, so the pick the weights and the grader meet is one the question has."""
    doc = {"version": "test", "groups": [
        {"id": "g1", "reads": ["context", "price.recent_move"],
         "questions": {"q_size": {"status": "live", "viewpoint": "volume_price", "type": "score", "ask": "How far has price moved?",
                                  "instructions": "Read `price.recent_move`.", "options": ["none", "small", "large"],
                                  "criteria": ["no move", "a small move", "a large move"]}}}]}

    def sender(r, api_key=None, timeout=10.0, deadline=None):
        return {"model": "fake-1", "answers": {"q_size": {
            "type": "score", "score": 1.1, "confidence": 0.6, "legend": {"0": "no move", "1": "a small move", "2": "a large move"},
            "probabilities": {"0": 0.2, "1": 0.7, "2": 0.1}}}}
    from spx_jev.ask import send_all as real_send_all
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    monkeypatch.setattr(service, "send_all", lambda requests, **kw: real_send_all(requests, api_key=CANARY, sender=sender))
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, doc, True, DAY)
    entry = c["questions"][0]
    assert entry["options"] == ["none", "small", "large"]
    assert entry["answer"]["pick"] == "small" and entry["answer"]["probabilities"] == {"none": 0.2, "small": 0.7, "large": 0.1}
    assert json.loads((out / "last_asked.json").read_text())["q_size"]["answer"]["pick"] == "small"
    assert json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])["fresh"] == {"q_size": "small"}


def test_every_time_on_the_card_is_a_full_timestamp_with_its_offset(tmp_path, monkeypatch):
    """The phone shows times in the viewer's own zone, so no field may carry a bare market clock."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    run_once(state, out, DOC, True, DAY)
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(11, 5, ss=20), 7701.0)], 101)
    monkeypatch.setattr(service, "send_all", _answers(failing={"g2"}))
    c = run_once(state, out, DOC, True, DAY)
    fields = _times(c)
    assert not [(w, v) for w, v in fields if BARE_CLOCK.match(v)]
    for key in ("generated_at", "row_ts"):
        assert ISO.match(c[key]), key
    assert c["session"] == {"close": at(16, 0).isoformat(), "last_read": at(15, 32).isoformat()}
    assert all(ISO.match(v) for v in c["marks"].values() if v) and all(ISO.match(x["read"]) and ISO.match(x["mark"]) for x in c["calls"])
    held = [e for e in c["questions"] if e.get("held_from")]
    assert held and all(ISO.match(e["held_from"]) for e in held)
    assert datetime.fromisoformat(c["session"]["close"]).utcoffset() is not None


def test_a_sent_run_refuses_a_row_from_another_day_and_a_row_already_read(tmp_path, monkeypatch):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    with pytest.raises(service.NoRowYet, match="no diary row for today yet"):
        run_once(state, state / "spx_jev", DOC, True, None)
    monkeypatch.setattr(service, "today_et", lambda: DAY)
    monkeypatch.setattr(service, "STALE_ROW_SKIP_MIN", 1e9)
    run_once(state, state / "spx_jev", DOC, True, None)
    before = (state / "spx_jev" / "latest.json").read_bytes()
    with pytest.raises(service.NoRowYet, match="already read"):
        run_once(state, state / "spx_jev", DOC, True, None)
    assert (state / "spx_jev" / "latest.json").read_bytes() == before


def test_a_live_read_on_a_stalled_scanner_is_skipped_and_the_card_stays(tmp_path, monkeypatch):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    fixed = datetime(2026, 9, 25, 12, 0, tzinfo=ZoneInfo("America/New_York"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(service, "datetime", Clock)
    day = fixed.date().isoformat()
    state = write_state(tmp_path, day, [make_row(fixed - timedelta(minutes=7), 7700.0)], flat_bars(150, day=day))
    (state / "spx_jev" / "latest.json").write_text('{"row_ts": "the last card"}')
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    with pytest.raises(service.NoRowYet, match="7.0 minutes old") as skipped:
        run_once(state, state / "spx_jev", DOC, True, None)
    # the scanner can be running with no SPX quote to write (09-28's Schwab outage): the words name the diary
    assert "the SPX diary has not been written since 11:53 ET" in str(skipped.value) and "scanner" not in str(skipped.value)
    assert json.loads((state / "spx_jev" / "latest.json").read_text())["row_ts"] == "the last card"


def test_a_sent_read_with_nothing_due_says_nothing_was_sent(tmp_path, monkeypatch, capsys):
    """The 09:32 read asks nothing by schedule: the card still says the run was allowed to send, so the phone
    does not call it unkeyed, but it asked JEV nothing, and its log line says so rather than "sent"."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    monkeypatch.setattr(service, "not_due", lambda doc, lane, now, fired=None: {"q_dir": "not due", "q_two": "not due"})
    quiet = run_once(state, state / "spx_jev", DOC, True, DAY)
    assert quiet["sent"] and quiet["asked"] == 0 and quiet["hour"] is None
    monkeypatch.setattr(service, "not_due", lambda doc, lane, now, fired=None: {})
    asked = run_once(state, state / "spx_jev", DOC, True, DAY)
    assert asked["asked"] == 2 and run_once(state, state / "spx_jev", DOC, False, DAY)["asked"] == 0

    monkeypatch.setenv("TYPESAFE_API_KEY", CANARY)
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.setattr(service, "now_et", lambda: at(9, 32, ss=5))
    for card, said in ((quiet, "answered 0/2 nothing due, nothing sent"), (asked, "answered 2/2 sent")):
        monkeypatch.setattr(service, "run_once", lambda *a, card=card, **k: card)
        assert service.main(["--state-dir", str(state)]) == 0
        assert said in capsys.readouterr().err


def test_a_question_whose_label_says_nothing_happened_sleeps_on_the_card_and_in_the_archive_and_is_not_missing(tmp_path, monkeypatch):
    """No new session high or low is the extreme question's quiet state: the card, the record and the archive say it
    is asleep and why, and the sum counts only the question whose label has no data (no market context) as missing."""
    doc = {"version": "test", "groups": [
        {"id": "g1", "reads": ["context", "price"], "questions": {
            "q_dir": DOC["groups"][0]["questions"]["q_dir"],
            "q_extreme": {"status": "live", "type": "noul", "ask": "Did the new extreme hold?",
                          "instructions": "Read `price.session_extreme_recent`.", "criteria": {"true": "t", "false": "f"}}}},
        {"id": "g2", "reads": ["context", "breadth"], "questions": {
            "q_breadth": {"status": "live", "type": "noul", "ask": "Is breadth leaning?",
                          "instructions": "Read `breadth.tick_lean`.", "criteria": {"true": "t", "false": "f"}}}}]}
    state = _state(tmp_path, [make_row(at(9, 31), 7700.0), make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, doc, True, DAY)
    q = {e["id"]: e for e in c["questions"]}
    assert q["q_extreme"]["skipped"] == "asleep: no new session high or low in the last 15 minutes"
    assert q["q_breadth"]["skipped"] == "missing breadth.tick_lean" and c["omitted"]["breadth.tick_lean"] == "no market-context snapshot today"
    assert c["hour"]["missing"] == 1
    rec = json.loads((out / f"{DAY}.jsonl").read_text())
    [kept] = [json.loads(l) for l in (out / "archive" / f"{DAY}.jsonl").read_text().splitlines() if json.loads(l).get("kind") == "read"]
    assert rec["skipped"]["g1"]["q_extreme"] == kept["skipped"]["g1"]["q_extreme"] == q["q_extreme"]["skipped"]


def test_an_unsent_run_says_why_and_holds_nothing(tmp_path):
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    c = run_once(state, state / "spx_jev", DOC, False, DAY, unsent_reason="not sent: no key on this machine")
    assert not c["sent"] and c["fresh"] == 0 and c["hour"] is None
    assert all(e["skipped"] == "not sent: no key on this machine" for e in c["questions"])
    assert not (state / "spx_jev" / "last_asked.json").exists()


def test_the_sum_is_blended_with_the_time_of_day_once_there_are_enough_sessions(tmp_path, monkeypatch):
    from spx_jev.clock import JEV_SHARE, MIN_SESSIONS
    prior = {f"2026-09-{d:02d}": flat_bars(390, day=f"2026-09-{d:02d}") for d in range(1, MIN_SESSIONS + 1)}
    state = write_state(tmp_path, DAY, [make_row(at(12, 2, ss=10), 7700.0)], flat_bars(160), prior)
    for d in prior:
        (state / "reversion" / f"{d}.jsonl").write_text("".join(json.dumps(make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 7700.0)) + "\n"
                                                                for m in range(0, 390, 5)))
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    h = run_once(state, state / "spx_jev", DOC, True, DAY)["hour"]
    assert h["blend"]["used"] is True and h["blend"]["phase"] == "lunch"
    assert h["probabilities"]["flat"] == JEV_SHARE * 0.8 + (1 - JEV_SHARE) * 1.0


def test_the_situation_rows_carry_a_word_and_a_figure(full_scene):
    from spx_jev.service import SITUATION, situation_rows
    from spx_jev.labels.registry import build_labels
    labels = build_labels(full_scene)
    rows = situation_rows(labels.state, labels.figures)
    assert [r["path"] for r in rows] == [p for p, _ in SITUATION]
    by = {r["path"]: r for r in rows}
    assert by["price.recent_move"]["verdict"] == "Rising" and by["gex.air_to_wall"]["verdict"] == "Close"
    assert all("sigma" not in r["sentence"] and "verdict" not in r.get("figure", {}) for r in rows)


def test_a_read_before_a_scheduled_close_event_carries_the_tag_and_jev_never_sees_it(tmp_path, monkeypatch):
    """2026-09-18 closed on quarterly expiry and the S&P rebalance."""
    state = _state(tmp_path, [make_row(at(15, 31, ss=10), 7700.0)], 390)
    seen = []
    real = _answers()
    monkeypatch.setattr(service, "send_all", lambda reqs, **kw: seen.extend(reqs) or real(reqs, **kw))
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, state / "spx_jev", DOC, True, DAY)
    assert c["event"]["within_30"] is True and c["event"]["events"][0]["at"] == at(16, 0).isoformat()
    assert seen and not any("scheduled" in json.dumps(r["state"]) for r in seen)


def test_a_live_read_writes_the_loops_forecasts_keeps_the_blend_on_the_phone_and_a_sealed_day_is_learned(tmp_path, monkeypatch):
    from spx_jev.clock import MIN_SESSIONS
    prior = {f"2026-09-{d:02d}": flat_bars(390, day=f"2026-09-{d:02d}") for d in range(1, MIN_SESSIONS + 1)}
    state = write_state(tmp_path, DAY, [make_row(at(12, 2, ss=10), 7700.0)], flat_bars(390), prior)
    for d in prior:
        (state / "reversion" / f"{d}.jsonl").write_text("".join(json.dumps(make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 7700.0)) + "\n"
                                                                for m in range(0, 390, 5)))
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, state / "spx_jev", DOC, True, DAY)
    out = state / "spx_jev"
    rec = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    snap = rec["pool"]["next_30"]
    assert set(snap["experts"]) == set(service.pool.W0) and snap["awake"] == ["q_dir", "q_two"] and snap["members"].keys() == {"q_dir", "q_two"}
    assert snap["blend50_exact"] == {k: round(v, 4) for k, v in c["hour"]["probabilities"].items()}
    assert c["hour"]["shown_source"] == "blend50_exact" and rec["shown_source"] == "blend50_exact"
    read = json.loads((out / "archive" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert read["schema_version"] == archive.SCHEMA_VERSION and read["pool"]["next_60"]["experts"]
    # the day is over and both marks were graded in the same run: the loop learned it at once
    weights = json.loads((out / "weights.json").read_text())
    assert weights["pool"]["last_session_applied"] == DAY and weights["pool"]["phone"] == {**weights["pool"]["phone"], "shows": "blend", "on_phone": False}
    assert weights["questions"]["q_dir"]["days"] == 1 and weights["questions"]["q_dir"]["weight"] == 1.0
    assert json.loads((out / "pool_60.json").read_text())["last_session_applied"] == DAY


def test_the_live_lanes_fire_after_the_close_grades_the_last_calls_and_asks_jev_nothing(tmp_path, monkeypatch):
    """The 15:32 read's 30-minute mark is the closing bar: the 16:02 fire, past the close, grades it that evening."""
    from zoneinfo import ZoneInfo
    from spx_jev import grade
    ny = ZoneInfo("America/New_York")
    at_1602 = datetime.fromisoformat(f"{DAY}T16:02:00").replace(tzinfo=ny)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at_1602.astimezone(tz) if tz else at_1602.replace(tzinfo=None)
    monkeypatch.setattr(grade, "datetime", Clock)
    state = _state(tmp_path, [make_row(at(15, 31, ss=40), 7700.0)], 361)
    out = state / "spx_jev"
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    run_once(state, out, DOC, True, DAY)
    reads_before = (out / f"{DAY}.jsonl").read_text()

    def refuse(*a, **k):
        raise AssertionError("the close-out asked JEV")
    monkeypatch.setattr(service, "send_all", refuse)
    monkeypatch.setattr(service, "send", refuse)
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.setattr(service, "now_et", lambda: at_1602)
    _state(tmp_path, [make_row(at(15, 31, ss=40), 7700.0)], 390)
    assert service.main(["--state-dir", str(state)]) == 0
    c = json.loads((out / "latest.json").read_text())
    assert c["closed_out_at"] and c["tally"]["graded"] == 1
    assert (out / f"{DAY}.jsonl").read_text() == reads_before
