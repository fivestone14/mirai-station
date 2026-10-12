"""The learning store: a day's raw files as typed Parquet, every refused row quarantined with its reason, the raw
files untouched, a rebuild the same. Offline; skipped where duckdb or pyarrow is absent."""
from __future__ import annotations

import json
import plistlib
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import duckdb  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from conftest import (DAY, archive_path, at, flat_bars, hour_path, integral_grades_path, lane_folder, night_path,  # noqa: E402
                      station_paths, write_json, write_jsonl)
from spx_jev import store  # noqa: E402

SKILL = Path(__file__).resolve().parents[1]
D = date.fromisoformat(DAY)                                   # Friday 2026-09-18
EARLY, READ = "2026-09-18T10:02:04.1-04:00", "2026-09-18T10:32:05.5-04:00"
TAPE_READ = "2026-09-18T09:40:00-04:00"
SHOCK_QUIET = "no shock in the last 60 minutes"
Q_SHAPE = {"type": "choice", "instructions": "Read `price.recent_move`.", "criteria": {"clean": "one way", "burst": "a jump"}}
Q_DAY = {"type": "choice", "instructions": "Read `price.day_character`.", "criteria": {"trend": "one way", "mixed": "both"}}
Q_HOUR = {"type": "noul", "instructions": "Read `price.hour_one_way`.", "criteria": {"true": "it did"}}


def _read(ts: str, lane: str = "live", **over) -> dict:
    rec = {"read_id": f"{lane}:{ts}", "lane": lane, "row_ts": ts, "sent": True, "spot": 6600.0, "sigma": 75.0,
           "labels": {"context": {"symbol": "SPX"}, "price": {"recent_move": "price rose 0.1 sigma"}},
           "omitted": {"shock.burst": SHOCK_QUIET, "breadth.advance_decline": "no NYSE advance-decline value at or before now"},
           "requests": [], "skipped": {}, "responses": {}, "hour_request": None, "hour_response": None, "hour": None,
           "cadence": {"from": None, "held": {}, "not_due": {}, "asked": []},
           "market_context": {"$VIX": {"value": 16.2, "known_at": "2026-09-18T10:01:00-04:00"}}, "event": None,
           "ruler": None, "band": None, "pool": None, "checkpoint": None, "night": None, "schema_version": 3, "kind": "read",
           "archived_at": "2026-09-18T14:40:00+00:00"}
    rec.update(over)
    return rec


def _hour(jev: dict, clock: dict, shown: dict) -> dict:
    by = {h: {"pick": max(shown, key=shown.get), "probabilities": shown, "confidence": None, "blended": True,
              "jev": {"pick": max(jev, key=jev.get), "probabilities": jev, "confidence": 0.4},
              "clock": {"pick": max(clock, key=clock.get), "probabilities": clock, "n": 180}} for h in ("next_30", "next_60")}
    return {**by["next_30"], "primary": "next_30", "by": by, "model": "jev-1.13.0", "shown_source": "blend50_exact",
            "blend": {"used": True, "jev_share": 0.5, "phase": "morning", "sessions": 19}, "used": 3, "left_out": 0, "missing": 1}


def _day_archive() -> list[dict]:
    """Two live reads and a tape read. The first live read answers the day question; the second asks two
    questions (one answered, one lost), holds the day question from the first, sleeps the shock question on
    its label's reason, and is missing and dark on two more; the tape read and one live horizon are graded."""
    early = _read(EARLY, requests=[{"id": "price_move", "state": {}, "questions": {"day_character": Q_DAY}}],
                  responses={"price_move": {"model": "jev-1.13.0", "answers": {"day_character": {
                      "type": "choice", "choice": "mixed", "confidence": 0.7, "probabilities": {"trend": 0.2, "mixed": 0.8}}}}},
                  archived_at="2026-09-18T14:05:00+00:00")
    shown = {"up": 0.3, "down": 0.2, "flat": 0.45, "unsure": 0.05}
    read = _read(READ,
                 requests=[{"id": "price_move", "state": {}, "questions": {"move_shape": Q_SHAPE}},
                           {"id": "hour_shape", "state": {}, "questions": {"one_way_hour": Q_HOUR}}],
                 responses={"price_move": {"model": "jev-1.13.0", "answers": {"move_shape": {
                     "type": "choice", "choice": "clean", "confidence": 0.9, "probabilities": {"clean": 0.9, "burst": 0.1}}}},
                            "hour_shape": {"error": "JEV unreachable for group hour_shape: TimeoutError"}},
                 skipped={"price_move": {"day_character": "not on its schedule at the 10:32 ET read", "*": "nothing more"},
                          "shocks": {"shock_state": f"asleep: {SHOCK_QUIET}", "breadth_lean": "missing breadth.advance_decline",
                                     "moc_side": "dark: the source it needs is not plugged in"}},
                 cadence={"from": None, "held": {"day_character": EARLY},
                          "not_due": {"day_character": "not on its schedule at the 10:32 ET read"},
                          "asked": ["move_shape", "one_way_hour"],
                          "reasked": {"move_shape": {"row_ts": EARLY, "why": "JEV unreachable"}}},
                 hour=_hour({"up": 0.4, "down": 0.1, "flat": 0.4, "unsure": 0.1}, {"up": 0.2, "down": 0.2, "flat": 0.6}, shown),
                 pool={"next_30": {"pool": {"up": 0.25, "flat": 0.5, "down": 0.25}, "p_move": 0.5, "p_up_given_move": 0.5,
                                   "state_hash": "abc", "baseline": "v1:x", "members": {"move_shape": "0123456789ab"}},
                       "next_60": {"left_out": "JEV gave no probabilities for next_60"}},
                 event={"events": [{"kind": "FOMC", "at": "2026-09-18T11:00:00-04:00", "minutes": 28}], "soonest_min": 28,
                        "within_30": True, "sentence": "a scheduled event is ahead"})
    tape = _read(TAPE_READ, lane="tape", market_context={"$VIX": {"value": 16.0, "known_at": "2026-09-18T09:39:00-04:00"}}, ruler={"unit_points": 6.1, "unit_sigma": 0.08, "source": "tape"},
                 band={"flat_points": 2.5, "big_points": 5.4}, archived_at="2026-09-18T13:41:00+00:00",
                 hour={"pick": "down_small", "probabilities": {"down_small": 0.5, "flat": 0.3, "up_small": 0.2}, "primary": "next_10",
                       "by": {"next_10": {"pick": "down_small", "probabilities": {"down_small": 0.5, "flat": 0.3, "up_small": 0.2},
                                          "views": {"direction": {"pick": "down", "probabilities": {"down": 0.5, "flat": 0.3, "up": 0.2}}}}},
                       "model": "jev-1.13.0"})
    grade_live = {"read_id": f"live:{READ}", "lane": "live", "row_ts": READ, "kind": "grade", "schema_version": 3,
                  "archived_at": "2026-09-18T15:03:00+00:00",
                  "grade": {"row_ts": READ, "horizons": ["next_30"], "anchor": {"points": 75.0, "source": "anchor"},
                            "next_30": {"realized_sigma": 0.2, "band": "up", "pick": "flat", "hit": False, "brier": 0.8,
                                        "p_band": 0.3, "jev_pick": "up", "jev_hit": True, "jev_brier": 0.5, "clock_brier": 0.9}}}
    grade_tape = {"read_id": f"tape:{TAPE_READ}", "lane": "tape", "row_ts": TAPE_READ, "kind": "grade", "schema_version": 3,
                  "archived_at": "2026-09-18T13:51:00+00:00",
                  "grade": {"row_ts": TAPE_READ, "horizons": ["next_10"],
                            "next_10": {"realized_points": -3.0, "band": "down_small", "pick": "unsure", "hit": False, "brier": 0.4}}}
    close_out = {"lane": "tape", "day": DAY, "calls": [], "tally": {}, "kind": "close_out", "schema_version": 3}
    return [early, read, tape, grade_live, grade_tape, close_out]


def _state(tmp_path, archive: list | None = None, extra_lines: list[str] = ()) -> None:
    """A station state dir holding one day of every raw file the store reads."""
    lines = [json.dumps(r) for r in (_day_archive() if archive is None else archive)] + list(extra_lines)
    archive_path(tmp_path, DAY).parent.mkdir(parents=True)
    archive_path(tmp_path, DAY).write_text("\n".join(lines) + "\n")
    write_jsonl(hour_path(lane_folder(tmp_path), DAY), [{"row_ts": READ, "learn_exclude": {"30": True, "60": False}}])
    write_jsonl(station_paths.SPX_PRICE_BARS_BY_MINUTE.path(tmp_path, day=DAY), flat_bars(390))
    night = [{"schema_version": 1, "day": DAY, "ts": "2026-09-18T08:00:00-04:00", "symbol": "/ES", "contract": "/ESZ26",
              "contract_from": "roll_table", "bar_minutes": 1, "open": 6600.0, "high": 6601.0, "low": 6599.0, "close": 6600.5,
              "volume": 100.0, "session": "premarket", "source": "schwab_price_history", "saved_at": "2026-09-18T09:26:10-04:00",
              "flags": []}]
    night.insert(0, {**night[0], "ts": "2026-09-17T15:55:00-04:00", "session": "regular"})     # Thursday's last minutes
    write_jsonl(night_path(tmp_path, DAY), night)


@pytest.fixture
def built(tmp_path):
    _state(tmp_path)
    log = store.build_day(tmp_path, D, at(16, 45))
    return tmp_path, log


def _table(root, name: str) -> list[dict]:
    return pq.ParquetFile(store.partition(root, store.BY_NAME[name], D)).read().to_pylist()


def test_every_table_is_written_for_the_day_with_its_types_and_schema_version(built):
    root, log = built
    for table in store.TABLES + (store.VALIDATION,):
        schema = pq.ParquetFile(store.partition(root, table, D)).schema_arrow
        assert schema.metadata[b"spx_jev_store_schema"] == str(store.SCHEMA_VERSION).encode()
        assert schema.equals(table.schema, check_metadata=False) and "day" not in schema.names
    reads = pq.ParquetFile(store.partition(root, store.BY_NAME["reads"], D)).schema_arrow
    assert str(reads.field("row_ts").type) == "timestamp[us, tz=America/New_York]"
    assert set(log) == {t.name for t in store.TABLES}
    assert not store.partition(root, store.BY_NAME["quarantine"], D).exists()      # nothing refused: no file of refusals


def test_a_read_is_one_row_with_its_times_ruler_counts_and_reasons(built):
    root, _ = built
    reads = {r["read_id"]: r for r in _table(root, "reads")}
    r = reads[f"live:{READ}"]
    assert (r["minute_et"], r["minutes_from_open"]) == (10 * 60 + 32, 62)
    assert (r["questions"], r["answered"], r["lost"], r["held"], r["asleep"], r["missing"], r["dark"], r["reasked"]) == (6, 1, 1, 1, 1, 1, 1, 1)
    assert dict(r["skip_reasons"])["shock_state"] == f"asleep: {SHOCK_QUIET}" and "*" not in dict(r["skip_reasons"])
    assert (r["labels_written"], r["labels_omitted"], r["labels_asleep"], r["market_values"]) == (2, 1, 1, 1)
    assert r["event_kinds"] == ["FOMC"] and r["event_within_30"] is True and r["sum_missing"] == 1
    tape = reads[f"tape:{TAPE_READ}"]
    assert (tape["ruler_source"], tape["ruler_points"], tape["ruler_unit_sigma"], tape["band_flat_points"]) == ("tape", 6.1, 0.08, 2.5)


def test_each_question_of_a_read_has_its_status_its_options_and_the_question_it_was_sent(built):
    root, _ = built
    ans = {(a["read_id"], a["question_id"]): a for a in _table(root, "answers")}
    shape = ans[(f"live:{READ}", "move_shape")]
    assert (shape["status"], shape["pick"], dict(shape["probabilities"])) == ("answered", "clean", {"clean": 0.9, "burst": 0.1})
    assert shape["question_hash"] == store.question_hash(Q_SHAPE) and shape["base_pool_question_version"] == "0123456789ab"
    assert shape["reasked"] is True and shape["reasked_from"] == datetime.fromisoformat(EARLY)
    lost = ans[(f"live:{READ}", "one_way_hour")]
    assert lost["status"] == "lost" and "TimeoutError" in lost["reason"] and lost["probabilities"] is None
    held = ans[(f"live:{READ}", "day_character")]
    assert (held["status"], held["held_found"], held["pick"]) == ("held", True, "mixed")
    assert held["question_hash"] == ans[(f"live:{EARLY}", "day_character")]["question_hash"]
    assert {q: ans[(f"live:{READ}", q)]["status"] for q in ("shock_state", "breadth_lean", "moc_side")} == \
        {"shock_state": "asleep", "breadth_lean": "missing", "moc_side": "dark"}


def test_a_lost_ask_the_lane_held_its_last_answer_for_is_stored_as_held_with_that_answer(tmp_path):
    rows = _day_archive()
    early, read = rows[0], rows[1]
    early["requests"].append({"id": "hour_shape", "state": {}, "questions": {"one_way_hour": Q_HOUR}})
    early["responses"]["hour_shape"] = {"model": "jev-1.13.0", "answers": {"one_way_hour": {"type": "noul", "noul": 0.7}}}
    read["cadence"]["held"]["one_way_hour"] = EARLY                  # the group timed out; the sum read the 10:02 answer
    _state(tmp_path, rows)
    store.build_day(tmp_path, D, at(16, 45))
    a = {(r["read_id"], r["question_id"]): r for r in _table(tmp_path, "answers")}[(f"live:{READ}", "one_way_hour")]
    assert (a["status"], a["held_from"], a["held_found"], a["pick"]) == ("held", datetime.fromisoformat(EARLY), True, "true")
    assert a["reason"].startswith("lost, its last answer held: JEV unreachable") and a["group_id"] == "hour_shape"
    r = {r["read_id"]: r for r in _table(tmp_path, "reads")}[f"live:{READ}"]
    assert (r["lost"], r["held"]) == (0, 2)


def test_a_read_archived_before_the_lane_recorded_its_re_asks_stores_them_as_unknown_not_as_none(built):
    root, _ = built
    reads = {r["read_id"]: r for r in _table(root, "reads")}
    assert (reads[f"live:{EARLY}"]["reasked"], reads[f"live:{READ}"]["reasked"]) == (None, 1)   # only READ's cadence has reasked
    ans = {(a["read_id"], a["question_id"]): a["reasked"] for a in _table(root, "answers")}
    assert (ans[(f"live:{EARLY}", "day_character")], ans[(f"live:{READ}", "shock_state")]) == (None, False)


def test_a_call_keeps_jev_alone_the_clock_the_blend_the_learned_mix_and_what_was_shown(built):
    root, _ = built
    calls = {(c["read_id"], c["sum_id"]): c for c in _table(root, "calls")}
    c = calls[(f"live:{READ}", "next_30")]
    assert c["shown_source"] == "blend50_exact" and dict(c["jev_probs"])["up"] == 0.4 and c["clock_n"] == 180
    assert (c["blended"], c["blend_jev_share"], c["base_pool_p_move"], c["learn_exclude"]) == (True, 0.5, 0.5, True)
    assert c["mark"] == at(11, 2) and c["is_primary"] is True and c["is_call"] is True and c["base_pool_reference_version"] == "v1:x"
    assert calls[(f"live:{READ}", "next_60")]["base_pool_left_out"] == "JEV gave no probabilities for next_60"
    tape = calls[(f"tape:{TAPE_READ}", "next_10")]
    assert tape["shown_source"] == "jev" and tape["direction_pick"] == "down" and tape["mark"] == at(9, 50)


AVG = {"pick": "down", "probabilities": {"up": 0.2, "flat": 0.3, "down": 0.5}, "confidence": None, "primary": "average_30",
       "box": "next_30", "minutes": 28, "flat_points": 5.9, "edge_points": 3.5, "price": 6600.0, "model": "jev-1.13.0",
       "by": {"average_30": {"pick": "down", "probabilities": {"up": 0.2, "flat": 0.3, "down": 0.5}, "confidence": None,
                             "blended": True, "jev": {"pick": "down", "probabilities": {"up": 0.1, "flat": 0.2, "down": 0.7},
                                                      "confidence": 0.6},
                             "clock": {"pick": "flat", "probabilities": {"up": 0.3, "flat": 0.4, "down": 0.3}, "n": 110}}},
       "blend": {"used": True, "jev_share": 0.5, "phase": "morning", "sessions": 19}}
Q_AVG = {"type": "choice", "instructions": "Where does the average price sit?", "criteria": {"up": "above", "flat": "near", "down": "below"}}
AVG_REPLY = {"model": "jev-1.13.0", "answers": {"average_30": {"type": "choice", "choice": "down", "confidence": 0.6,
                                                               "probabilities": {"up": 0.1, "flat": 0.2, "down": 0.7}}}}


def _with_average(rows: list[dict], hour: dict | None = None) -> list[dict]:
    """The day's archive with the 10:32 read asking the average-price sum (archive version 5), its call ``hour``."""
    read = rows[1]
    read.update(schema_version=5, average_request={"id": "average", "state": {}, "questions": {"average_30": Q_AVG}},
                average_response=AVG_REPLY, hour=hour if hour is not None else {**read["hour"], "average": AVG})
    return rows


def _integral(row_ts: str, rule: int = 3, **over) -> dict:
    line = {"row_ts": row_ts, "horizon": "next_30", "rule_version": rule, "sum": "average_30", "graded": True, "minutes": 30,
            "from": 6600.0, "f": 5.9, "factor": 0.5918, "edge": 3.5, "g": -4.2, "label": "down", "pick": "down",
            "direction": "down", "verdict": "right", "margin": 1.2, "running": ["flat", "down"],
            "best": {"points": 1.0, "minute": 2}, "worst": {"points": -6.0, "minute": 29},
            "sharp_move": {"points": -2.0, "minute": 12, "higher_than": 15, "of": 20, "sharp": False, "note": "no sharp move"},
            "filled": [7], "bad_ticks": [], "stale_read": False, "end_label": "down",
            "scores": {"brier": 0.38, "log_loss": 0.69, "jev_brier": 0.14, "jev_log_loss": 0.36, "clock_brier": 0.74, "clock_log_loss": 1.2},
            "edge_told": 3.5}
    line.update(over)
    return line


def test_the_call_the_phone_showed_is_the_average_price_sums_with_its_odds_edge_and_window(tmp_path):
    _state(tmp_path, _with_average(_day_archive()))
    store.build_day(tmp_path, D, at(16, 45))
    calls = {(c["read_id"], c["sum_id"]): c for c in _table(tmp_path, "calls")}
    c = calls[(f"live:{READ}", "average_30")]
    assert (c["is_call"], c["horizon"], c["is_primary"], c["shown_pick"], dict(c["shown_probs"])["down"]) == (True, "next_30", False, "down", 0.5)
    assert (c["minutes"], c["mark"], c["edge_points"], c["flat_points"], c["price"]) == (28, at(11, 2), 3.5, 5.9, 6600.0)
    assert (c["shown_source"], dict(c["jev_probs"])["down"], c["clock_n"], c["blend_jev_share"], c["learn_exclude"]) == \
        ("blend50_exact", 0.7, 110, 0.5, True)
    assert calls[(f"live:{READ}", "next_30")]["is_call"] is False and calls[(f"live:{READ}", "next_60")]["is_call"] is False
    assert calls[(f"tape:{TAPE_READ}", "next_10")]["is_call"] is True           # a read that never asked it calls its end price
    ans = {(a["read_id"], a["question_id"]): a for a in _table(tmp_path, "answers")}[(f"live:{READ}", "average_30")]
    assert (ans["group_id"], ans["status"], ans["pick"], ans["question_hash"]) == ("average", "answered", "down", store.question_hash(Q_AVG))
    r = {r["read_id"]: r for r in _table(tmp_path, "reads")}[f"live:{READ}"]
    assert (r["questions"], r["answered"], r["average_error"]) == (6, 1, None)     # the call's own question is not the doc's


def test_a_read_whose_end_price_sums_failed_still_has_its_average_price_call(tmp_path):
    rows = _with_average(_day_archive(), hour={"error": "JEV unreachable for group hour: TimeoutError", "average": AVG})
    rows[1]["hour_response"] = {"error": "JEV unreachable for group hour: TimeoutError"}
    _state(tmp_path, rows)
    store.build_day(tmp_path, D, at(16, 45))
    calls = [c for c in _table(tmp_path, "calls") if c["read_id"] == f"live:{READ}"]
    assert [(c["sum_id"], c["is_call"], c["shown_pick"]) for c in calls] == [("average_30", True, "down")]
    r = {r["read_id"]: r for r in _table(tmp_path, "reads")}[f"live:{READ}"]
    assert r["sum_error"].startswith("JEV unreachable") and r["average_error"] is None


def test_a_read_whose_average_price_sum_failed_calls_its_end_price_and_says_why(tmp_path):
    rows = _with_average(_day_archive(), hour={**_day_archive()[1]["hour"], "average": {"error": "no average_30 answer",
                                                                                          "primary": "average_30", "box": "next_30"}})
    rows[1]["average_response"] = {"error": "JEV unreachable for group average: TimeoutError"}
    _state(tmp_path, rows)
    store.build_day(tmp_path, D, at(16, 45))
    calls = {c["sum_id"]: c for c in _table(tmp_path, "calls") if c["read_id"] == f"live:{READ}"}
    assert set(calls) == {"next_30", "next_60"} and calls["next_30"]["is_call"] is True
    assert {r["read_id"]: r for r in _table(tmp_path, "reads")}[f"live:{READ}"]["average_error"] == "no average_30 answer"
    ans = {(a["read_id"], a["question_id"]): a for a in _table(tmp_path, "answers")}[(f"live:{READ}", "average_30")]
    assert (ans["status"], ans["reason"]) == ("lost", "JEV unreachable for group average: TimeoutError")


def test_the_average_price_grade_is_stored_from_each_lanes_file_its_newest_rule_standing(tmp_path):
    _state(tmp_path, _with_average(_day_archive()))
    integral_grades_path(lane_folder(tmp_path)).write_text("".join(json.dumps(g) + "\n" for g in (
        _integral(READ, rule=2, label="flat", verdict="wrong"), _integral(READ),
        {"row_ts": EARLY, "horizon": "next_30", "rule_version": 1, "graded": False, "reason": "not graded: bars missing",
         "missing": [29, 30], "bad_ticks": [], "stale_read": False},
        _integral("2026-09-17T10:32:00-04:00"))))
    integral_grades_path(lane_folder(tmp_path, "tape")).parent.mkdir(parents=True)
    integral_grades_path(lane_folder(tmp_path, "tape")).write_text(json.dumps(_integral(
        TAPE_READ, horizon="next_10", sum="average_10", minutes=10, label="flat", verdict="passed", lean={"direction": "down", "p": 0.4},
        size={"band": "flat", "size": "small", "call": None, "right": None})) + "\n")
    log = store.build_day(tmp_path, D, at(16, 45))
    grades = {g["read_id"]: g for g in _table(tmp_path, "average_grades")}
    live = grades[f"live:{READ}"]
    assert (live["rule_version"], live["sum_id"], live["outcome"], live["verdict"], live["correct"], live["abstained"]) == \
        (3, "average_30", "down", "right", True, False)
    assert (live["mark"], live["average_move"], live["edge_told"], live["minutes_filled"], live["brier"], live["running"]) == \
        (at(11, 2), -4.2, 3.5, 1, 0.38, ["flat", "down"])
    assert log["average_grades"]["superseded"] == 1                               # the rule-2 line, under the newer rule
    early = grades[f"live:{EARLY}"]
    assert (early["graded"], early["sum_id"], early["minutes_missing"], early["verdict"]) == (False, "next_30", 2, None)
    tape = grades[f"tape:{TAPE_READ}"]
    assert (tape["verdict"], tape["abstained"], tape["correct"], tape["lean"], tape["size_outcome"]) == ("passed", True, None, "down", "small")
    # the size the end-price five-way pick named is a pick, never "the call", which is the average-price sum's alone
    assert tape["size_pick"] is None and "size_call" not in tape
    assert len(grades) == 3                                                        # the other day's line is that day's


def test_a_calls_frozen_reference_is_read_under_its_new_name_and_its_old_one(tmp_path):
    rows = _day_archive()
    rows[1]["pool"]["next_30"]["reference_version"] = rows[1]["pool"]["next_30"].pop("baseline")
    _state(tmp_path, rows)
    store.build_day(tmp_path, D, at(16, 45))
    assert {c["sum_id"]: c["base_pool_reference_version"] for c in _table(tmp_path, "calls") if c["lane"] == "live" and c["read_id"] == f"live:{READ}"}["next_30"] == "v1:x"


def test_the_bars_keep_one_row_a_minute_from_the_one_store(built):
    root, log = built
    spx = _table(root, "spx_bars")
    assert len(spx) == 390 and {b["source"] for b in spx} == {"live_file"} and log["spx_bars"]["duplicates"] == 0
    night = _table(root, "overnight_bars")
    assert [(b["ts"], b["prior_session"]) for b in night] == [(at(15, 55, day="2026-09-17"), True), (at(8, 0), False)]
    assert night[1]["contract"] == "/ESZ26" and night[1]["overnight_schema"] == 1      # the overnight file's version


def _bad_archive() -> list[dict]:
    rows = _day_archive()
    early, read = rows[0], rows[1]
    read["responses"]["price_move"]["answers"]["move_shape"]["probabilities"] = {"clean": 1.4, "burst": 0.1}
    early["spot"] = -1.0                                                                          # its children follow it out
    rows.append({"kind": "mystery"})
    return rows


def test_every_refused_row_is_quarantined_with_its_reason_and_none_is_dropped(tmp_path):
    _state(tmp_path, _bad_archive(), extra_lines=['{"kind": "read", "torn'])
    log = store.build_day(tmp_path, D, at(16, 45))
    bad = _table(tmp_path, "quarantine")
    why = {(q["table_name"], q["key"]): q["reason"] for q in bad}
    assert "outside 0 to 1" in why[("answers", f"live:{READ}|move_shape")]
    assert why[("reads", f"live:{EARLY}")] == "spot -1.0 is not above zero"
    assert why[("answers", f"live:{EARLY}|day_character")] == f"its read live:{EARLY} is not among the day's kept reads"
    assert sum(q["table_name"] == "raw_line" for q in bad) == 2                                        # the torn line and the unknown kind
    held = next(a for a in _table(tmp_path, "answers") if a["question_id"] == "day_character")
    assert held["status"] == "held"                                   # a held answer stays, though its source read was refused
    rows_in = {t: c["rows_in"] for t, c in log.items()}
    assert all(c["rows_in"] == c["kept"] + c["duplicates"] + c["superseded"] + c["quarantined"] for c in log.values()), log
    assert sum(c["quarantined"] for c in log.values()) == len(bad) and rows_in["raw_line"] == 2
    validation = {v["table_name"]: v for v in _table(tmp_path, "validation")}
    assert validation["answers"]["quarantined"] == 2 and validation["answers"]["store_schema"] == store.SCHEMA_VERSION


def test_two_equally_good_sources_that_disagree_are_quarantined_not_one_kept_by_order():
    bar = {"day": D, "ts": "2026-09-18T10:00:00-04:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
           "source": "live_file"}
    later = {**bar, "close": 1.9, "_source": "spx_jev/bars/2026-09-18.jsonl:2"}
    kept, bad, counts = store.validate(store.SPX_BARS, [bar, later])
    assert [k["close"] for k in kept] == [1.5] and (counts["superseded"], len(bad)) == (0, 1)
    assert bad[0]["reason"].startswith("differs from the row kept for the same ts")
    kept, bad, counts = store.validate(store.SPX_BARS, [later, {**bar, "source": "session_file"}])
    assert [k["close"] for k in kept] == [1.5] and (counts["superseded"], bad) == (1, [])     # the saved session file wins


def test_every_column_selects_in_plain_sql_none_a_duckdb_reserved_word():
    keywords = dict(duckdb.sql("SELECT keyword_name, keyword_category FROM duckdb_keywords()").fetchall())
    assert [(t.name, n) for t in store.BY_NAME.values() for n, _, _ in t.columns if keywords.get(n, "unreserved") != "unreserved"] == []


def test_a_rebuild_leaves_the_raw_files_untouched_and_writes_the_same_rows(built):
    root, _ = built
    tables = [t.store.path(root) for t in store.TABLES + (store.QUARANTINE, store.VALIDATION)]
    raw = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*")
           if p.is_file() and not any(p.is_relative_to(t) for t in tables)}
    first = {t.name: _table(root, t.name) for t in store.TABLES}
    store.build_day(root, D, at(17, 0))
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in raw} == raw
    assert {name: _table(root, name) for name in first} == first
    assert [p.name for p in store.partition(root, store.BY_NAME["reads"], D).parent.iterdir()] == [store.PART]


def test_today_is_built_once_its_saves_have_run_and_only_market_days(tmp_path):
    assert D not in store.days_to_build(at(16, 25)) and store.days_to_build(at(16, 40))[-1] == D
    assert store.days_to_build(at(16, 40), catch_up=7)[0] == date(2026, 9, 11)
    assert store.last_finished(at(12, 0, day="2026-09-20")) == D                      # Sunday: Friday is the last
    for d in ("2026-09-17", "2026-09-19", "2026-09-21"):                               # Thursday, Saturday, a future Monday
        write_jsonl(night_path(tmp_path, d), [])
    write_jsonl(station_paths.SPX_PRICE_BARS_BY_MINUTE.path(tmp_path, day="2026-09-16"), [])
    assert store.days_on_disk(tmp_path, D) == [date(2026, 9, 16), date(2026, 9, 17)]
    assert store.off_days_on_disk(tmp_path, D + timedelta(days=1)) == [date(2026, 9, 19)]


def test_the_nightly_job_fires_once_the_command_counts_today_as_finished():
    job = plistlib.loads((SKILL / "launchd" / "com.mirai-station.spx-jev-store.plist.template").read_bytes())
    fire = job["StartCalendarInterval"]
    ny = at(fire["Hour"] + 3, fire["Minute"])                                  # the box's Pacific clock, three hours behind
    assert store.last_finished(ny) == D and store.last_finished(ny - timedelta(minutes=15)) < D


def test_the_command_builds_the_day_and_names_its_counts(tmp_path, capsys):
    _state(tmp_path)
    assert store.main(["--state-dir", str(tmp_path), "--day", DAY]) == 0
    out = capsys.readouterr().out
    assert f"{DAY} reads 3, answers " in out and "calls 3 rows (0 quarantined)" in out and "views" not in out


def test_a_backfill_names_the_raw_files_it_leaves_out_for_not_being_market_days(tmp_path, capsys):
    _state(tmp_path)
    write_jsonl(night_path(tmp_path, "2026-09-07"), [])                                           # Labor Day's futures
    assert store.main(["--state-dir", str(tmp_path), "--backfill"]) == 0
    out = capsys.readouterr().out
    assert "not market days, so not stored: the raw files of 2026-09-07" in out and f"{DAY} reads 3" in out


def test_a_record_whose_parts_are_malformed_is_quarantined_and_the_rest_of_the_day_is_built(tmp_path):
    rows = _day_archive()
    rows[2]["requests"] = ["not a request"]
    _state(tmp_path, rows)
    log = store.build_day(tmp_path, D, at(16, 45))
    assert [r["lane"] for r in _table(tmp_path, "reads")] == ["live", "live"]
    reasons = [q["reason"] for q in _table(tmp_path, "quarantine")]
    assert any(r.startswith("a malformed read record: AttributeError") for r in reasons), reasons
    assert log["calls"]["kept"] == 2                                     # the tape read's call follows it out


def test_a_raw_line_in_the_wrong_shape_or_not_utf8_is_quarantined_and_the_rest_of_the_day_is_built(tmp_path):
    _state(tmp_path)
    with open(station_paths.SPX_PRICE_BARS_BY_MINUTE.path(tmp_path, day=DAY), "ab") as f:
        f.write(b'{"ts": "\xff"}\n')
    with open(hour_path(lane_folder(tmp_path), DAY), "a") as f:
        f.write(json.dumps({"row_ts": {"not": "text"}}) + "\n")
    integral_grades_path(lane_folder(tmp_path)).write_text(json.dumps({"row_ts": READ, "horizon": ["next_30"]}) + "\n")
    log = store.build_day(tmp_path, D, at(16, 45))
    reasons = sorted(q["reason"] for q in _table(tmp_path, "quarantine"))
    assert reasons[0].startswith("a malformed average-price grade: TypeError") and reasons[1:] == [
        "an hour record whose row_ts is not text", "not a JSON object"], reasons
    assert log["raw_line"]["quarantined"] == 3 and log["spx_bars"]["kept"] == 390 and log["calls"]["kept"] == 3
