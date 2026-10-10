"""The raw archive: every read, grade and close-out, append only, typed, keyed by read id, no secrets."""
from __future__ import annotations

import json
from dataclasses import fields
from datetime import timedelta

from conftest import DAY, PRIOR_DAYS, at, flat_bars, make_row, write_prior_rows, write_state
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


def test_a_read_and_its_later_grade_land_in_the_archive_under_one_read_id(tmp_path, monkeypatch, fixed_zones):
    monkeypatch.setenv("TYPESAFE_API_KEY", CANARY)
    monkeypatch.setattr(service, "send_all", _send_all)
    monkeypatch.setattr(service, "send", _send)
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    write_prior_rows(tmp_path, {d: [make_row(at(9, 31, day=d), 7700.0)] for d in prior})
    state = write_state(tmp_path, DAY, [make_row(at(10, 35, ss=10), 7700.0)], flat_bars(70), prior)
    service.run_once(state, state / "spx_jev", DOC, True, DAY)
    read = _lines(state)[0]
    assert _typed(read, ReadRecord) and read["kind"] == "read" and read["schema_version"] == SCHEMA_VERSION
    assert read["read_id"] == f"live:{at(10, 35, ss=10).isoformat()}" and read["lane"] == "live" and read["sent"] is True
    assert read["labels"]["price"]["recent_move"] and read["omitted"]["breadth.tick_lean"] == "no market-context snapshot today"
    assert read["requests"][0]["questions"]["q_dir"]["criteria"]["rising"] == "r"
    assert read["responses"]["g1"]["answers"]["q_dir"]["choice"] == "rising"                      # JEV's reply, untouched
    assert read["hour_request"]["id"] == "hour" and read["hour_response"]["answers"]["next_30"]["choice"] == "flat"
    assert read["cadence"] == {"from": None, "held": {}, "not_due": {}, "asked": ["q_dir"], "reasked": {}} and read["market_context"] is None

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


def test_a_read_of_a_lane_without_a_checkpoint_writes_the_premarket_fields_as_null(tmp_path):
    rec = ReadRecord(read_id="live:t", lane="live", row_ts="t", sent=False, spot=7700.0, sigma=75.0, labels={}, omitted={}, requests=[],
                     skipped={}, responses=None, hour_request=None, hour_response=None, hour=None, cadence={}, market_context=None, event=None)
    line = json.loads(archive.append(tmp_path, DAY, rec).read_text())
    assert _typed(line, ReadRecord) and line["checkpoint"] is None and line["night"] is None and line["schema_version"] == 5
    assert line["average_request"] is None and line["average_response"] is None      # a read that asked no average-price sum


def test_a_close_out_record_is_typed(tmp_path):
    rec = CloseOutRecord(lane="tape", day=DAY, calls=[{"read": at(10, 30).isoformat()}], tally={"calls": 1, "graded": 1, "right": 0})
    path = archive.append(tmp_path, DAY, rec)
    line = json.loads(path.read_text())
    assert _typed(line, CloseOutRecord) and line["kind"] == "close_out" and line["tally"]["calls"] == 1
    assert archive.read_close_out(line) == line                              # a version 4 line reads as written


def test_a_version_3_close_out_reads_as_calls_that_stood_on_their_end_price():
    """Up to version 3 a close-out's calls carried only their end-price grade and its tally counted unsure picks
    under ``unsure``: read now, each graded call stands on its end price, so marked, and the unsure picks are passes."""
    v3 = {"lane": "tape", "day": DAY, "schema_version": 3, "kind": "close_out", "archived_at": "t",
          "calls": [{"read": "a", "pick": "up", "outcome": "up_big", "hit": False, "moved": {"realized_points": 9.0}},
                    {"read": "b", "pick": "unsure", "outcome": "flat", "hit": False, "moved": {}}, {"read": "c", "pick": "flat"}],
          "tally": {"calls": 3, "graded": 2, "right": 0, "unsure": 1}}
    got = archive.read_close_out(v3)
    assert [c.get("end_price") for c in got["calls"]] == [{"outcome": "up_big", "hit": False, "moved": {"realized_points": 9.0}},
                                                          {"outcome": "flat", "hit": False, "moved": {}}, None]
    assert [c.get("end_price_only") for c in got["calls"]] == [True, True, None] and not any("outcome" in c for c in got["calls"])
    assert got["tally"] == {"calls": 3, "graded": 2, "right": 0, "passed": 1, "end_price_only": 2}
    assert service.tally_words(got["tally"]) == "0 of 1 calls right · 1 passed · 2 on the end price only · 1 still to grade"
    assert v3["calls"][0]["outcome"] == "up_big" and "passed" not in v3["tally"]    # the line read is left as it was


def _written(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_a_replay_without_an_out_dir_writes_into_a_scratch_folder_never_the_station_records(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()
    state = write_state(tmp_path / "station", DAY, [make_row(at(10, 35, ss=10), 7700.0)], flat_bars(70))
    before = _written(state)
    assert service.main(["--state-dir", str(state), "--day", DAY]) == 0
    assert _written(state) == before                                     # the station's folders are as they were
    (scratch,) = (tmp_path / "scratch").iterdir()
    assert scratch.name.startswith(f"spx-jev-replay-{DAY}-live-")
    assert {"latest.json", f"{DAY}.jsonl", f"archive/{DAY}.jsonl"} <= set(_written(scratch))


def test_a_run_given_its_own_out_dir_keeps_its_archive_there(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    state = write_state(tmp_path / "station", DAY, [make_row(at(10, 35, ss=10), 7700.0)], flat_bars(70))
    out = tmp_path / "trial"
    assert service.main(["--state-dir", str(state), "--day", DAY, "--out-dir", str(out)]) == 0
    assert json.loads((out / "archive" / f"{DAY}.jsonl").read_text())["kind"] == "read"
    assert not (state / "spx_jev" / "archive").exists() and not (state / "spx_jev" / "latest.json").exists()
