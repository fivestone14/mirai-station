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
    assert c["symbol"] == "SPX" and c["sent"] and c["fresh_count"] == 2 and c["held_count"] == 0 and "book_asof" not in c
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
    assert weights["pool_integral"]["method"] == "pool_v1_integral"
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
    assert not c["sent"] and c["fresh_count"] == 0 and c["hour"] is None
    assert all(e["skipped"] == "not sent: no key on this machine" for e in c["questions"])
    assert not (state / "spx_jev" / "last_asked.json").exists()


def test_a_failed_request_is_a_plain_reason_on_the_card_and_jevs_own_text_stays_in_the_archive(tmp_path, monkeypatch):
    """The phone once showed "JEV unreachable for group hour: TimeoutError: The read operation timed out" and an Envoy
    503 page: the card says what happened in plain words, the question and the sums alike, and the archive keeps the
    network's text."""
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)

    def sums_time_out(req, **kw):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(service, "send_all", _answers(failing=("g2",)))
    monkeypatch.setattr(service, "send", sums_time_out)
    c = run_once(state, state / "spx_jev", DOC, True, DAY)
    assert next(q for q in c["questions"] if q["id"] == "q_two")["skipped"] == "asked, no answer received: JEV did not answer in time"
    assert c["hour"]["error"] == c["hour"]["average"]["error"] == "JEV did not answer in time"
    read = json.loads((state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert read["responses"]["g2"]["error"].startswith("TimeoutError: handshake timed out")
    assert read["hour_response"]["error"] == "TimeoutError: The read operation timed out"


def test_the_card_calls_the_call_a_forecast_not_a_trade_signal_and_keeps_shadow_for_shadow_questions(tmp_path):
    """The card's note said the sums were "never a call" beside the call it carries: the call and the end-price
    questions are graded forecasts, never trade signals, and shadow questions are asked and logged, never graded."""
    state = _state(tmp_path, [make_row(at(11, 32), 7700.0)], 120)
    note = run_once(state, tmp_path / "out", DOC, False, DAY)["shadow_note"]
    assert note.endswith("each is a forecast, not a trade signal") and "never a call" not in note
    assert "shadow questions are asked and logged, never graded and never weighted" in note


def test_a_dark_question_carries_its_own_reason_onto_the_card_and_into_its_skip_line(tmp_path):
    """The phone names what each dark question waits for, but the card carried only its id, viewpoint and ask, and
    every skip line said the same "not plugged in": both now carry the questions doc's own dark_reason."""
    reason = "minute bars for euro futures (/6E), which the market-context job does not fetch"
    doc = {"version": "test", "groups": [*DOC["groups"], {"id": "g3", "reads": ["context"], "questions": {
        "q_dark": {"status": "dark", "viewpoint": "volume_price", "type": "choice", "ask": "Is the euro leading?",
                   "instructions": "Read `context.symbol`.", "criteria": {"yes": "y", "no": "n"}, "dark_reason": reason}}}]}
    state = _state(tmp_path, [make_row(at(11, 32), 7700.0)], 120)
    c = run_once(state, tmp_path / "out", doc, False, DAY)
    assert c["dark"] == [{"id": "q_dark", "viewpoint": "volume_price", "ask": "Is the euro leading?", "dark_reason": reason}]
    read = json.loads((tmp_path / "out" / "archive" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert read["skipped"]["g3"]["q_dark"] == f"dark: {reason}"


def test_a_row_is_stale_past_the_skip_line_and_a_replay_is_never_called_stale(tmp_path):
    """The card's own 15-minute stale line could never fire on a sent read, which the 6-minute skip line stops first,
    and called every replay stale with "nothing newer has been scanned". An unsent run on the newest row is stale past
    the skip line; a replay of a past day says its age is not measured."""
    state = _state(tmp_path, [make_row(at(11, 32), 7700.0)], 120)
    live = run_once(state, tmp_path / "now", DOC, False)["freshness"]
    assert live["stale"] and live["note"].endswith("nothing newer has been scanned")
    replay = run_once(state, tmp_path / "replay", DOC, False, DAY)["freshness"]
    assert not replay["stale"] and replay["note"] == "a replay: its row's age is not measured against the clock"


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
    # the day's volume-weighted average is VWAP, never "the day's average" beside a call graded on the average price
    assert by["price.vs_vwap"]["title"] == "Price against VWAP"
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
    # the day is over and both marks were graded in the same run: the end-price loop learned it at once
    learned = json.loads((out / "pool_30.json").read_text())
    assert learned["last_session_applied"] == DAY and learned["evidence"]["q_dir"]["days"] == 1
    assert json.loads((out / "pool_60.json").read_text())["last_session_applied"] == DAY
    # the weights report it as they did, and the average-price loop beside it passes the day over: no read had an
    # average-price call
    weights = json.loads((out / "weights.json").read_text())
    assert weights["method"] == "pool_v1" and weights["pool"]["last_session_applied"] == DAY and weights["questions"]["q_dir"]["days"] == 1
    beside = weights["pool_integral"]
    assert beside["method"] == "pool_v1_integral" and beside["pool"]["last_session_applied"] == DAY
    assert beside["questions"]["q_dir"]["weight"] == 1.0 and "days" not in beside["questions"]["q_dir"]
    said = [json.loads(line) for line in (out / "pool_integral_log.jsonl").read_text().splitlines()]
    assert [(x["session"], x["applied"], x["why"]) for x in said] == [(DAY, False, "no read carries an average-price call: before the question")]


def test_a_read_whose_end_price_sums_got_no_answer_keeps_the_answers_the_average_price_loop_learns_from(tmp_path, monkeypatch):
    """Only the end-price request failed: the read writes no end-price snapshot, but keeps the questions' answers
    beside the reason (pool.answered), so the average-price loop can learn the call standing on its average alone."""
    state = _state(tmp_path, [make_row(at(12, 2, ss=10), 7700.0)], 150)

    def sums(req, **kw):
        if req["id"] == "hour":
            raise RuntimeError("JEV unreachable for group hour: TimeoutError: The read operation timed out")
        return {"model": "fake-1", "answers": {"average_30": {"type": "choice", "choice": "up", "confidence": 0.6,
                                                             "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}}}}
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", sums)
    c = run_once(state, state / "spx_jev", DOC, True, DAY)
    rec = json.loads((state / "spx_jev" / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    snap = rec["pool"]["next_30"]
    assert snap["left_out"] == "JEV gave no probabilities for next_30" and set(snap["members"]) == {"q_dir", "q_two"}
    assert snap["awake"] == ["q_dir", "q_two"] and "shown_source" not in rec
    assert c["hour"]["average"]["pick"] == "up" and [call["sum"] for call in c["calls"]] == ["average_30"]


def _average_sums(req, **kw):
    if req["id"] == "hour":
        return _sums(req)
    return {"model": "fake-1", "answers": {"average_30": {"type": "choice", "choice": "up", "confidence": 0.6,
                                                         "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}}}}


def test_the_call_shows_its_exact_blend_said_under_shown_source(tmp_path, monkeypatch):
    """The call keeps its exact blend, said under shown_source (integral_loop.shown), on the card and in the sum record;
    neither loop's pool replaces it (the promotion was retired on 2026-10-07)."""
    state = _state(tmp_path, [make_row(at(12, 2, ss=10), 7700.0)], 150)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _average_sums)
    c = run_once(state, state / "spx_jev", DOC, True, DAY)
    rec = json.loads((state / "spx_jev" / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert c["hour"]["average"]["shown_source"] == rec["average"]["shown_source"] == "blend50_exact"
    assert c["hour"]["average"]["pick"] == "up" and c["hour"]["shown_source"] == "blend50_exact"


def test_a_failing_average_price_loop_costs_neither_the_read_nor_the_close_out(tmp_path, monkeypatch, capsys):
    """The average-price loop's state unreadable as a state (a list): the read still grades and writes its card, the
    average-price grade still appends, the weights come from the end-price loop, and the close-out still refreshes the card."""
    from spx_jev.clock import MIN_SESSIONS
    prior = {f"2026-09-{d:02d}": flat_bars(390, day=f"2026-09-{d:02d}") for d in range(1, MIN_SESSIONS + 1)}
    state = write_state(tmp_path, DAY, [make_row(at(12, 2, ss=10), 7700.0)], flat_bars(390), prior)
    for d in prior:
        (state / "reversion" / f"{d}.jsonl").write_text("".join(json.dumps(make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 7700.0)) + "\n"
                                                                for m in range(0, 390, 5)))
    out = state / "spx_jev"
    out.mkdir(parents=True, exist_ok=True)
    (out / "pool_30_integral.json").write_text("[]")
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, DOC, True, DAY)
    assert c["row_ts"] == at(12, 2, ss=10).isoformat() and (out / "latest.json").exists()
    assert "average-price loop failed: AttributeError" in capsys.readouterr().err
    weights = json.loads((out / "weights.json").read_text())
    assert weights["method"] == "pool_v1" and weights["pool"]["last_session_applied"] == DAY and weights["graded_runs"] == 1
    assert weights["pool_integral"] == {"failed": "AttributeError: 'list' object has no attribute 'get'"}
    lines = (out / "integral_grades.jsonl").read_text().splitlines()
    assert {json.loads(line)["horizon"] for line in lines} == {"next_30", "next_60"}
    card = service.close_out(state, out, DOC, service.LIVE, DAY)
    assert card and card["graded_at"] and card["tally"]["graded"] == 1 and "average-price loop failed" in capsys.readouterr().err
    assert (out / "pool_30_integral.json").read_text() == "[]"


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


# ---- the judgment group (Phase 3): the code's answers before the requests, the gate, never a raise

JUDGMENT_DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise, fall, or go nowhere?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}},
    {"id": "judgment", "reads": ["context", "judgment"],
     "questions": {"news_reaction": {"status": "shadow", "viewpoint": "judgment", "type": "choice", "sleep_when": "no headline captured",
                                     "ask": "Is SPX shrugging off, overreacting or in proportion?", "instructions": "Read `judgment.headlines`.",
                                     "criteria": {"shrugging_off": "s", "overreacting": "o", "in_proportion": "p", "unclear": "u"}},
                   "push_blowoff_or_fresh": {"status": "shadow", "viewpoint": "judgment", "type": "choice", "sleep_when": "no push",
                                             "ask": "Blow-off or fresh push?", "instructions": "Read `judgment.push_exhaustion`.",
                                             "criteria": {"blow_off": "b", "unclear": "u", "fresh_push": "f"}}}},
]}


def _headlines(state, minutes_before: int, title: str):
    from spx_jev.headlines import folder
    folder(state).mkdir(parents=True, exist_ok=True)
    line = {"captured_at": (at(10, 35, ss=10) - __import__("datetime").timedelta(minutes=minutes_before)).isoformat(timespec="seconds"),
            "pub_claimed": None, "feed": "cnbc_top", "source": "CNBC", "title": title, "url": None, "guid": None}
    with open(folder(state) / f"{DAY}.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def test_the_code_answers_the_gates_before_the_requests_and_a_fired_judgment_question_is_asked_unsummed(tmp_path, monkeypatch):
    from spx_jev import judgment
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    _headlines(state, 10, "Nvidia slips as export curbs widen")
    order = []

    def code_answers(state_dir, lane_name, day, rec, record):
        order.append(("code", record, rec["read_id"], sorted(rec["labels"])))
        return {"TREND-10": "up held", "TREND-11": "none"}
    real = _answers()

    def send_all(requests, **kw):
        order.append(("send", sorted(r["id"] for r in requests)))
        return real(requests, **kw)
    monkeypatch.setattr(judgment, "code_answers_for", code_answers)
    monkeypatch.setattr(service, "send_all", send_all)
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, JUDGMENT_DOC, True, DAY)
    assert [o[0] for o in order] == ["code", "send"] and order[1][1] == ["g1", "judgment"]
    assert order[0][1] is False and order[0][2] == f"live:{at(10, 35, ss=10).isoformat()}" and "price" in order[0][3] and "judgment" not in order[0][3]
    q = {e["id"]: e for e in c["questions"]}
    assert q["news_reaction"]["answer"]["pick"] == "shrugging_off" and q["push_blowoff_or_fresh"]["answer"]["pick"] == "blow_off"
    rec = json.loads((out / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert rec["state"]["judgment"]["headlines"].startswith("1 headlines captured from 10:25 to 10:25 ET") and "Nvidia slips" in rec["state"]["judgment"]["headlines"]
    assert rec["state"]["judgment"]["push_exhaustion"].startswith("the code's 60-minute range sweep reads 'up held' (TREND-10)")
    assert [r["id"] for r in rec["requests"]] == ["g1", "judgment"] and rec["skipped"].get("judgment") is None
    hour_rec = json.loads((out / "hour" / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert hour_rec["used"] == {"q_dir": "rising"} and hour_rec["left_out"]["news_reaction"] == "a shadow forecast, never an input"
    assert c["held_count"] == 0
    assert not list((state / "spx_jev" / "mirai_prediction").rglob("*.jsonl")) if (state / "spx_jev" / "mirai_prediction").exists() else True


def test_a_judgment_question_whose_gate_is_off_costs_the_read_nothing(tmp_path, monkeypatch):
    from spx_jev import judgment
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    monkeypatch.setattr(judgment, "code_answers_for", lambda *a, **k: {"TREND-10": "none", "TREND-11": None})
    sent = []
    real = _answers()
    monkeypatch.setattr(service, "send_all", lambda requests, **kw: sent.extend(r["id"] for r in requests) or real(requests, **kw))
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, JUDGMENT_DOC, True, DAY)
    assert sent == ["g1"]
    q = {e["id"]: e for e in c["questions"]}
    assert q["news_reaction"]["answer"] is None and q["news_reaction"]["skipped"] == "gate: headlines not fired: no headlines captured in the 60 minutes before the read"
    assert q["push_blowoff_or_fresh"]["skipped"] == "gate: TREND-10 or TREND-11 not fired: TREND-10 'none', TREND-11 not measured this read"
    rec = json.loads((out / f"{DAY}.jsonl").read_text().splitlines()[0])
    assert set(rec["skipped"]["judgment"]) == {"news_reaction", "push_blowoff_or_fresh", "*"} and "judgment" in rec["state"]


def test_the_judgment_group_failing_never_costs_the_read(tmp_path, monkeypatch):
    from spx_jev import judgment
    state = _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0)], 70)
    out = state / "spx_jev"
    _headlines(state, 10, "a headline")
    monkeypatch.setattr(judgment, "code_answers_for", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(service, "send_all", _answers())
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, JUDGMENT_DOC, True, DAY)        # the code part fails: the headline gate still fires, the push gate reads not measured
    q = {e["id"]: e for e in c["questions"]}
    assert q["news_reaction"]["answer"]["pick"] == "shrugging_off" and q["push_blowoff_or_fresh"]["skipped"].startswith("gate: TREND-10 or TREND-11 not fired")
    monkeypatch.setattr(judgment, "facts_for", lambda *a, **k: 1 / 0)
    _state(tmp_path, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(11, 5, ss=20), 7701.0)], 101)
    c = run_once(state, out, JUDGMENT_DOC, True, DAY)        # the whole group fails: every judgment question is not fired, the read goes on
    q = {e["id"]: e for e in c["questions"]}
    assert q["q_dir"]["answer"]["pick"] == "rising" and c["hour"]["pick"] == "flat"
    assert q["news_reaction"]["skipped"] == "gate: headlines not fired: the judgment group failed (ZeroDivisionError)"


# ---- the cut-over (Phase 4): date-gated, nothing moves before CUT_OVER_DAY

CUT_DOC = {"version": "test", "groups": [
    {**JUDGMENT_DOC["groups"][0], "questions": {"q_dir": {**JUDGMENT_DOC["groups"][0]["questions"]["q_dir"], "retired_from": "2026-10-08"}}},
    JUDGMENT_DOC["groups"][1]]}
EVE, CUT = "2026-10-07", "2026-10-08"


def _state_on(tmp_path, day: str):
    """A 10:35 row and 70 bars on ``day``, over enough flat prior sessions, read every five minutes, for the time-of-day
    blend and so the learning loop's snapshot."""
    from spx_jev.clock import MIN_SESSIONS
    prior = {f"2026-09-{d:02d}": flat_bars(390, day=f"2026-09-{d:02d}") for d in range(1, MIN_SESSIONS + 1)}
    write_prior_rows(tmp_path, {d: [make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=d), 7700.0) for m in range(0, 390, 5)] for d in prior})
    return write_state(tmp_path, day, [make_row(at(10, 35, day=day, ss=10), 7700.0)], flat_bars(70, day=day), prior)


def _headline_on(state, day: str, title: str):
    from spx_jev.headlines import folder
    folder(state).mkdir(parents=True, exist_ok=True)
    line = {"captured_at": at(10, 25, day=day).isoformat(timespec="seconds"), "pub_claimed": None, "feed": "cnbc_top", "source": "CNBC",
            "title": title, "url": None, "guid": None}
    with open(folder(state) / f"{day}.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def _cut_over_read(tmp_path, monkeypatch, day: str, doc: dict, code: dict | None):
    from spx_jev import judgment
    state = _state_on(tmp_path, day)
    out = state / "spx_jev"
    _headline_on(state, day, "Nvidia slips as export curbs widen")
    monkeypatch.setattr(judgment, "code_answers_for", lambda *a, **k: dict(code or {}))
    sent = []
    real = _answers()
    monkeypatch.setattr(service, "send_all", lambda requests, **kw: sent.extend(r["id"] for r in requests) or real(requests, **kw))
    monkeypatch.setattr(service, "send", _sums)
    c = run_once(state, out, doc, True, day)
    hour_rec = json.loads((out / "hour" / f"{day}.jsonl").read_text().splitlines()[0])
    return c, hour_rec, sent, out


def test_the_eve_of_the_cut_over_reads_exactly_as_a_doc_that_never_retires(tmp_path, monkeypatch):
    """On 2026-10-07 a retiring question is asked, summed and held as it was, and the sums' request is byte for byte the
    one the same read makes from a doc without retired_from: the code's sentences are not in it."""
    from spx_jev.hour import UNITS
    code = {"TREND-01": "big up", "TREND-10": "up held", "TREND-11": "none"}
    c, rec, sent, _ = _cut_over_read(tmp_path / "retiring", monkeypatch, EVE, CUT_DOC, code)
    c0, rec0, sent0, _ = _cut_over_read(tmp_path / "plain", monkeypatch, EVE, JUDGMENT_DOC, code)
    assert sent == sent0 == ["g1", "judgment"]
    assert json.dumps(rec["request"], sort_keys=True) == json.dumps(rec0["request"], sort_keys=True)
    assert json.dumps(rec["average_request"], sort_keys=True) == json.dumps(rec0["average_request"], sort_keys=True)
    assert list(rec["request"]["state"]["answers"]) == ["q_dir"] and rec["request"]["state"]["context"]["units"] == UNITS
    assert rec["used"] == {"q_dir": "rising"} and rec["left_out"]["news_reaction"] == "a shadow forecast, never an input"
    assert "code_sentences" not in rec and "code_sentences" not in c["hour"]
    assert {e["id"] for e in c["questions"]} == {"q_dir", "news_reaction", "push_blowoff_or_fresh"} and c["dark"] == []
    assert rec["pool"]["next_30"]["members"].keys() == {"q_dir"} and rec["pool"]["next_30"]["awake"] == ["q_dir"]


def test_from_the_cut_over_the_retired_question_is_dark_and_the_sums_ride_on_the_judgment_and_code_answers(tmp_path, monkeypatch):
    from spx_jev.ask import RETIRED
    from spx_jev.hour import CUT_OVER_UNITS
    code = {"TREND-01": "big up", "TREND-10": "up held", "TREND-11": "none", "LEVELS-01": None}
    c, rec, sent, out = _cut_over_read(tmp_path, monkeypatch, CUT, CUT_DOC, code)
    assert sent == ["judgment"]
    req = rec["request"]
    assert req["id"] == "hour" and set(req) == {"id", "state", "questions"} and set(req["state"]) == {"context", "answers"}
    assert list(req["questions"]) == ["next_30", "next_60"] and req["state"]["context"]["units"] == CUT_OVER_UNITS
    assert list(req["state"]["answers"]) == ["news_reaction", "push_blowoff_or_fresh", "code:TREND-01", "code:TREND-10", "code:TREND-11"]
    assert req["state"]["answers"]["news_reaction"] == "Is SPX shrugging off, overreacting or in proportion? shrugging off, JEV was 85% sure"
    assert req["state"]["answers"]["code:TREND-01"] == "Last 30-minute move, signed: big up"
    assert "q_dir" not in json.dumps(req) and rec["average_request"]["state"]["answers"] == req["state"]["answers"]
    assert rec["used"] == {"news_reaction": "shrugging_off", "push_blowoff_or_fresh": "blow_off"} and rec["fresh"] == {} and rec["left_out"] == {}
    assert rec["code_sentences"] == 3 and c["hour"]["pick"] == "flat" and c["hour"]["used"] == 2 and c["hour"]["code_sentences"] == 3
    # the card: the retired question is listed apart with its reason and never counted; the pool still forms its snapshot
    assert [e["id"] for e in c["questions"]] == ["news_reaction", "push_blowoff_or_fresh"] and c["fresh_count"] == 2
    assert c["dark"] == [{"id": "q_dir", "viewpoint": "volume_price", "ask": "Did price rise, fall, or go nowhere?", "dark_reason": RETIRED}]
    snap = rec["pool"]["next_30"]
    assert set(snap["experts"]) == set(service.pool.W0) and snap["members"] == {} and snap["awake"] == []
    record = json.loads((out / f"{CUT}.jsonl").read_text().splitlines()[0])
    assert record["skipped"]["g1"] == {"q_dir": f"dark: {RETIRED}", "*": "nothing to ask in this group this read"}
    assert "q_dir" not in json.loads((out / "last_asked.json").read_text())


def test_from_the_cut_over_a_read_with_no_judgment_and_no_code_answer_still_asks_the_sums_over_nothing(tmp_path, monkeypatch):
    doc = {"version": "test", "groups": [CUT_DOC["groups"][0]]}
    c, rec, sent, _ = _cut_over_read(tmp_path, monkeypatch, CUT, doc, {})
    assert sent == [] and c["asked"] == 0
    assert rec["request"] is not None and rec["request"]["state"]["answers"] == {} and rec["average_request"]["state"]["answers"] == {}
    assert c["hour"]["pick"] == "flat" and c["hour"]["used"] == 0 and rec["sentences"] == {} and "code_sentences" not in rec
