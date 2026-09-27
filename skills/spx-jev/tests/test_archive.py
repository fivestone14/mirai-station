"""The raw archive: every read, grade and close-out, append only, typed, keyed by read id, no secrets."""
from __future__ import annotations

import json
from dataclasses import fields
from datetime import timedelta

from conftest import DAY, at, flat_bars, make_row, write_state
from spx_jev import archive, service
from spx_jev.archive import SCHEMA_VERSION, CloseOutRecord, GradeRecord, ReadRecord

DOC = {"version": "test", "groups": [
    {"id": "g1", "reads": ["context", "price.recent_move"],
     "questions": {"q_dir": {"status": "live", "viewpoint": "volume_price", "type": "choice", "ask": "Did price rise?",
                             "instructions": "Read `price.recent_move`.",
                             "criteria": {"rising": "r", "falling": "f", "going_nowhere": "n", "unsure": "u"}}}}]}
CANARY = "apikey_" + "e" * 24 + "_" + "f" * 24


def _send_all(requests, **kw):
    return {r["id"]: {"model": "fake-1", "answers": {qid: {"type": "choice", "choice": "rising", "confidence": 0.8,
                                                            "probabilities": {"rising": 0.85, "falling": 0.05, "going_nowhere": 0.05, "unsure": 0.05}}
                                                      for qid in r["questions"]}} for r in requests}


def _send(req, **kw):
    return {"model": "fake-1", "answers": {
        "next_30": {"type": "choice", "choice": "flat", "confidence": 0.7, "probabilities": {"up": 0.1, "flat": 0.8, "down": 0.05, "unsure": 0.05}},
        "next_60": {"type": "choice", "choice": "flat", "confidence": 0.5, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.1, "unsure": 0.1}}}}


def _lines(state):
    return [json.loads(l) for l in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]


def _typed(line: dict, record_type) -> bool:
    """The line carries exactly the record's fields."""
    return set(line) == {f.name for f in fields(record_type)}


def test_a_read_and_its_later_grade_land_in_the_archive_under_one_read_id(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", CANARY)
    monkeypatch.setattr(service, "send_all", _send_all)
    monkeypatch.setattr(service, "send", _send)
    state = write_state(tmp_path, DAY, [make_row(at(10, 35, ss=10), 7700.0)], flat_bars(70))
    service.run_once(state, state / "spx_jev", DOC, True, DAY)
    read = _lines(state)[0]
    assert _typed(read, ReadRecord) and read["kind"] == "read" and read["schema_version"] == SCHEMA_VERSION
    assert read["read_id"] == f"live:{at(10, 35, ss=10).isoformat()}" and read["lane"] == "live" and read["sent"] is True
    assert read["labels"]["price"]["recent_move"] and read["omitted"]["breadth.tick_lean"] == "no market-context snapshot today"
    assert read["requests"][0]["questions"]["q_dir"]["criteria"]["rising"] == "r"
    assert read["responses"]["g1"]["answers"]["q_dir"]["choice"] == "rising"                      # JEV's reply, untouched
    assert read["hour_request"]["id"] == "hour" and read["hour_response"]["answers"]["next_30"]["choice"] == "flat"
    assert read["cadence"] == {"from": None, "held": {}, "not_due": {}, "asked": ["q_dir"]} and read["market_context"] is None

    write_state(tmp_path, DAY, [make_row(at(10, 35, ss=10), 7700.0), make_row(at(11, 5, ss=20), 7701.0)], flat_bars(101))
    service.run_once(state, state / "spx_jev", DOC, True, DAY)
    lines = _lines(state)
    assert [l["kind"] for l in lines] == ["read", "read", "grade"]
    grade = lines[2]
    assert _typed(grade, GradeRecord) and grade["read_id"] == read["read_id"] and grade["grade"]["band"] == "flat"
    assert lines[:1] == [read]                                                                      # append only: the first line is untouched
    assert CANARY not in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text()


def test_the_market_context_a_read_could_see_is_archived_with_when_each_value_was_known(full_scene):
    seen = full_scene.market.at(full_scene.now)
    assert seen["$ADD"] == {"value": 326.0, "known_at": (full_scene.now - timedelta(minutes=5)).isoformat()}
    assert all(set(v) == {"value", "known_at"} for v in seen.values())


def test_a_close_out_record_is_typed(tmp_path):
    rec = CloseOutRecord(lane="tape", day=DAY, calls=[{"read": at(10, 30).isoformat()}], tally={"calls": 1, "graded": 1, "right": 0})
    path = archive.append(tmp_path, DAY, rec)
    line = json.loads(path.read_text())
    assert _typed(line, CloseOutRecord) and line["kind"] == "close_out" and line["tally"]["calls"] == 1
