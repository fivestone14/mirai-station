"""The learning store: a day's raw files as typed Parquet, every refused row quarantined with its reason, the raw
files untouched, a rebuild the same, and DuckDB views over it all. Offline; skipped where duckdb or pyarrow is absent."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import duckdb  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from conftest import DAY, at, flat_bars  # noqa: E402
from spx_jev import store  # noqa: E402

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


def _state(tmp_path, archive: list | None = None, extra_lines: list[str] = ()) -> object:
    """A station state dir holding one day of every raw file the store reads, and its own calendar."""
    lines = [json.dumps(r) for r in (_day_archive() if archive is None else archive)] + list(extra_lines)
    folder = tmp_path / "spx_jev"
    (folder / "archive").mkdir(parents=True)
    (folder / "archive" / f"{DAY}.jsonl").write_text("\n".join(lines) + "\n")
    (folder / "hour").mkdir()
    (folder / "hour" / f"{DAY}.jsonl").write_text(json.dumps({"row_ts": READ, "learn_exclude": {"30": True, "60": False}}) + "\n")
    (folder / "bars").mkdir()
    (folder / "bars" / f"{DAY}.jsonl").write_text("".join(json.dumps(b) + "\n" for b in flat_bars(390)))
    (tmp_path / "reversion" / "bars").mkdir(parents=True)
    (tmp_path / "reversion" / "bars" / f"{DAY}-SPX.json").write_text(json.dumps(flat_bars(390)))
    bar = {"ts": "2026-09-18T10:00:00-04:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
    vold = {"ts": "2026-09-18T10:00:00-04:00", "open": 5000.0, "close": 6000.0, "volume": 0.0, "derived": "($UVOL - $DVOL) * 1000"}
    (folder / "context" / "bars").mkdir(parents=True)
    (folder / "context" / "bars" / f"{DAY}.jsonl").write_text(json.dumps(
        {"ts": "2026-09-18T10:01:00-04:00", "bars": {"XLK": bar, "$VOLD": {**bar, "close": 1.2}, "/ESZ26": bar, "$TNX": bar}}) + "\n")
    (folder / "context" / f"{DAY}.jsonl").write_text(json.dumps(
        {"ts": "2026-09-18T10:01:30-04:00", "bars": {"XLK": bar, "$VOLD": vold},
         "quotes": {"$VIX": {"last": 16.2, "close": 15.9, "volume": 0, "quote_time": None}}, "failed": []}) + "\n")
    (folder / "overnight").mkdir()
    night = [{"schema_version": 1, "day": DAY, "ts": "2026-09-18T08:00:00-04:00", "symbol": "/ES", "contract": "/ESZ26",
              "contract_from": "roll_table", "bar_minutes": 1, "open": 6600.0, "high": 6601.0, "low": 6599.0, "close": 6600.5,
              "volume": 100.0, "session": "premarket", "source": "schwab_price_history", "saved_at": "2026-09-18T09:26:10-04:00",
              "flags": []}]
    (folder / "overnight" / f"{DAY}.jsonl").write_text("".join(json.dumps(b) + "\n" for b in night))
    (folder / "overnight" / "rolls.json").write_text(json.dumps({"schema_version": 1, "current": {"/ES": "/ESZ26"}, "rolls": [
        {"symbol": "/ES", "day": DAY, "at": "2026-09-17T18:00:00-04:00", "from": "/ESU26", "to": "/ESZ26", "basis_step": 0.7,
         "basis_unit": "percent", "step_vs_typical": 20.0, "reference": "$SPX", "jump": 60.0}]}))
    calendar = tmp_path / "events.json"
    calendar.write_text(json.dumps({"built": "2026-09-01", "events": [
        {"date": DAY, "time_et": "10:00", "end_et": "10:30", "kind": "FED_SPEAKER", "tier": 2, "scope": "market",
         "in_session": True, "verified": True, "source": "x"},
        {"date": "2026-09-21", "time_et": "10:00", "kind": "OTHER_DAY", "tier": 2}]}))
    return calendar


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CALENDAR", _state(tmp_path))
    log = store.build_day(tmp_path, D, at(16, 45))
    return tmp_path, log


def _table(root, name: str) -> list[dict]:
    return pq.ParquetFile(store.partition(store.store_dir(root), name, D)).read().to_pylist()


def test_every_table_is_written_for_the_day_with_its_types_and_schema_version(built):
    root, log = built
    for name in store.BY_NAME:
        schema = pq.ParquetFile(store.partition(store.store_dir(root), name, D)).schema_arrow
        assert schema.metadata[b"spx_jev_store_schema"] == str(store.SCHEMA_VERSION).encode()
        assert schema.equals(store.BY_NAME[name].schema, check_metadata=False) and "day" not in schema.names
    reads = pq.ParquetFile(store.partition(store.store_dir(root), "reads", D)).schema_arrow
    assert str(reads.field("row_ts").type) == "timestamp[us, tz=America/New_York]"
    assert set(log) == {t.name for t in store.TABLES}


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


def test_a_fact_is_written_omitted_or_asleep_on_the_reason_its_question_sleeps_on(built):
    root, _ = built
    facts = {(f["read_id"], f["path"]): f for f in _table(root, "facts")}
    assert facts[(f"live:{READ}", "shock.burst")]["status"] == "asleep"
    assert facts[(f"live:{EARLY}", "shock.burst")]["status"] == "omitted"        # nothing slept on it at that read
    assert facts[(f"live:{READ}", "breadth.advance_decline")]["status"] == "omitted"
    vix = facts[(f"live:{READ}", "$VIX")]
    assert vix["source"] == "market_context" and vix["value"] == 16.2 and vix["known_at"] < datetime.fromisoformat(READ)


def test_each_question_of_a_read_has_its_status_its_options_and_the_question_it_was_sent(built):
    root, _ = built
    ans = {(a["read_id"], a["question_id"]): a for a in _table(root, "answers")}
    shape = ans[(f"live:{READ}", "move_shape")]
    assert (shape["status"], shape["pick"], dict(shape["probabilities"])) == ("answered", "clean", {"clean": 0.9, "burst": 0.1})
    assert shape["question_hash"] == store.question_hash(Q_SHAPE) and shape["pool_version"] == "0123456789ab"
    assert shape["reasked"] is True and shape["reasked_from"] == datetime.fromisoformat(EARLY)
    lost = ans[(f"live:{READ}", "one_way_hour")]
    assert lost["status"] == "lost" and "TimeoutError" in lost["reason"] and lost["probabilities"] is None
    held = ans[(f"live:{READ}", "day_character")]
    assert (held["status"], held["held_found"], held["pick"]) == ("held", True, "mixed")
    assert held["question_hash"] == ans[(f"live:{EARLY}", "day_character")]["question_hash"]
    assert {q: ans[(f"live:{READ}", q)]["status"] for q in ("shock_state", "breadth_lean", "moc_side")} == \
        {"shock_state": "asleep", "breadth_lean": "missing", "moc_side": "dark"}


def test_a_call_keeps_jev_alone_the_clock_the_blend_the_learned_mix_and_what_was_shown(built):
    root, _ = built
    calls = {(c["read_id"], c["horizon"]): c for c in _table(root, "calls")}
    c = calls[(f"live:{READ}", "next_30")]
    assert c["shown_source"] == "blend50_exact" and dict(c["jev_probs"])["up"] == 0.4 and c["clock_n"] == 180
    assert (c["blended"], c["blend_jev_share"], c["pool_p_move"], c["learn_exclude"]) == (True, 0.5, 0.5, True)
    assert c["mark"] == at(11, 2) and c["primary"] is True
    assert calls[(f"live:{READ}", "next_60")]["pool_left_out"] == "JEV gave no probabilities for next_60"
    tape = calls[(f"tape:{TAPE_READ}", "next_10")]
    assert tape["shown_source"] == "jev" and tape["direction_pick"] == "down" and tape["mark"] == at(9, 50)


def test_a_grade_has_its_mark_outcome_and_an_unsure_pick_abstains_rather_than_counting_wrong(built):
    root, _ = built
    grades = {g["read_id"]: g for g in _table(root, "grades")}
    live = grades[f"live:{READ}"]
    assert (live["mark"], live["outcome"], live["correct"], live["abstained"], live["jev_hit"]) == (at(11, 2), "up", False, False, True)
    tape = grades[f"tape:{TAPE_READ}"]
    assert (tape["outcome"], tape["abstained"], tape["correct"], tape["hit"]) == ("down_small", True, None, False)


def test_the_bars_keep_one_row_a_minute_the_better_source_winning_and_every_copy_counted(built):
    root, log = built
    spx = _table(root, "spx_bars")
    assert len(spx) == 390 and {b["source"] for b in spx} == {"session_file"} and log["spx_bars"]["duplicates"] == 390
    ctx = {(b["symbol"], b["source"]): b for b in _table(root, "context_bars")}
    assert ("XLK", "saved_day") in ctx and ("XLK", "live") not in ctx          # the same minute from the live snapshot
    vold = ctx[("$VOLD", "saved_day")]
    assert vold["derived"] is False and vold["close"] == 1.2                   # Schwab's own over the derived one
    assert ("/ES", "saved_day") in ctx and ctx[("/ES", "saved_day")]["served_as"] == "/ESZ26"
    assert ctx[("$TNX", "saved_day")]["close"] == 0.15                         # the yield in percent, as the labels read it
    assert (log["context_bars"]["duplicates"], log["context_bars"]["superseded"]) == (1, 1)
    assert _table(root, "context_quotes")[0]["prior_close"] == 15.9
    assert _table(root, "overnight_bars")[0]["contract"] == "/ESZ26"
    assert [r["to_contract"] for r in _table(root, "rolls")] == ["/ESZ26"]
    assert [(e["kind"], e["ends_at"]) for e in _table(root, "events")] == [("FED_SPEAKER", at(10, 30))]


def _bad_archive() -> list[dict]:
    rows = _day_archive()
    early, read = rows[0], rows[1]
    read["market_context"]["$VVIX"] = {"value": 90.0, "known_at": "2026-09-18T10:40:00-04:00"}     # seen after the read
    read["responses"]["price_move"]["answers"]["move_shape"]["probabilities"] = {"clean": 1.4, "burst": 0.1}
    early["spot"] = -1.0                                                                          # its children follow it out
    rows[3]["archived_at"] = "2026-09-18T14:50:00+00:00"                                          # graded before its 11:02 mark
    rows.append({**rows[3], "archived_at": "2026-09-18T15:04:00+00:00",                           # a regrade after it
                 "grade": {**rows[3]["grade"], "next_30": {**rows[3]["grade"]["next_30"], "band": "down"}}})
    rows.append({"read_id": "live:2026-09-18T11:02:00-04:00", "lane": "live", "row_ts": "2026-09-18T11:02:00-04:00",
                 "kind": "grade", "archived_at": "2026-09-18T16:00:00+00:00",
                 "grade": {"horizons": ["next_30"], "next_30": {"band": "flat", "pick": "flat", "hit": True}}})
    rows.append({"kind": "mystery"})
    return rows


def test_every_refused_row_is_quarantined_with_its_reason_and_none_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CALENDAR", _state(tmp_path, _bad_archive(), extra_lines=['{"kind": "read", "torn']))
    log = store.build_day(tmp_path, D, at(16, 45))
    bad = _table(tmp_path, "quarantine")
    why = {(q["table"], q["key"]): q["reason"] for q in bad}
    assert why[("facts", f"live:{READ}|market_context|$VVIX")].startswith("known after its read")
    assert "outside 0 to 1" in why[("answers", f"live:{READ}|move_shape")]
    assert why[("reads", f"live:{EARLY}")] == "spot -1.0 is not above zero"
    assert why[("answers", f"live:{EARLY}|day_character")] == f"its read live:{EARLY} is not among the day's kept reads"
    assert why[("grades", f"live:{READ}|next_30")].startswith("graded before its mark")
    assert "is not among the day's kept reads" in why[("grades", "live:2026-09-18T11:02:00-04:00|next_30")]
    assert sum(q["table"] == "raw_line" for q in bad) == 2                                        # the torn line and the unknown kind
    assert [g["outcome"] for g in _table(tmp_path, "grades") if g["lane"] == "live"] == ["down"]   # the regrade stands
    held = next(a for a in _table(tmp_path, "answers") if a["question_id"] == "day_character")
    assert held["status"] == "held"                                   # a held answer stays, though its source read was refused
    rows_in = {t: c["rows_in"] for t, c in log.items()}
    assert all(c["rows_in"] == c["kept"] + c["duplicates"] + c["superseded"] + c["quarantined"] for c in log.values()), log
    assert sum(c["quarantined"] for c in log.values()) == len(bad) and rows_in["raw_line"] == 2
    validation = {v["table"]: v for v in _table(tmp_path, "validation")}
    assert validation["grades"]["quarantined"] == 2 and validation["grades"]["store_schema"] == store.SCHEMA_VERSION


def test_a_second_grade_of_the_same_sum_that_disagrees_is_quarantined_not_kept_over_the_first(tmp_path, monkeypatch):
    rows = _day_archive()
    rows.append({**rows[3], "archived_at": "2026-09-18T15:30:00+00:00",
                 "grade": {**rows[3]["grade"], "next_30": {**rows[3]["grade"]["next_30"], "band": "down"}}})
    rows.append(rows[4])                                                                          # an exact copy
    monkeypatch.setattr(store, "CALENDAR", _state(tmp_path, rows))
    log = store.build_day(tmp_path, D, at(16, 45))
    assert {g["read_id"]: g["outcome"] for g in _table(tmp_path, "grades")}[f"live:{READ}"] == "up"
    assert (log["grades"]["duplicates"], log["grades"]["quarantined"]) == (1, 1)
    assert _table(tmp_path, "quarantine")[0]["reason"].startswith("differs from the row kept for the same read_id, horizon")


def test_an_outcome_is_held_to_its_lanes_bands():
    row = {"lane": "live", "horizon": "next_30", "outcome": "down_small"}
    assert "is not one of up, flat, down" in store._outcome(row)
    assert store._outcome({**row, "lane": "tape", "horizon": "next_10"}) is None


def test_a_rebuild_leaves_the_raw_files_untouched_and_writes_the_same_rows(built):
    root, _ = built
    raw = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and "store" not in p.parts}
    first = {name: _table(root, name) for name in store.BY_NAME if name != "validation"}
    store.build_day(root, D, at(17, 0))
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in raw} == raw
    assert {name: _table(root, name) for name in first} == first
    assert [p.name for p in store.partition(store.store_dir(root), "reads", D).parent.iterdir()] == [store.PART]


def test_the_duckdb_file_has_a_view_per_table_and_is_left_alone_once_current(built):
    root, _ = built
    s = store.store_dir(root)
    assert store.write_views(s) is True
    with duckdb.connect(str(s / store.DB_NAME), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM answers WHERE status = 'held'").fetchone() == (1,)
        assert con.execute("SELECT probabilities['mixed'] FROM answers WHERE status = 'held'").fetchone() == (0.8,)
        assert con.execute("SELECT count(*) FROM grades g JOIN reads r USING (read_id)").fetchone() == (2,)
        assert store.write_views(s) is False                    # a reader holding the file open does not stop a run


def test_today_is_built_once_its_saves_have_run_and_only_market_days(tmp_path):
    assert D not in store.days_to_build(at(16, 25)) and store.days_to_build(at(16, 40))[-1] == D
    assert store.days_to_build(at(16, 40), catch_up=7)[0] == date(2026, 9, 11)
    assert store.last_finished(at(12, 0, day="2026-09-20")) == D                      # Sunday: Friday is the last
    for d in ("2026-09-17", "2026-09-19", "2026-09-21"):                               # Thursday, Saturday, a future Monday
        (tmp_path / "spx_jev" / "overnight").mkdir(parents=True, exist_ok=True)
        (tmp_path / "spx_jev" / "overnight" / f"{d}.jsonl").write_text("")
    (tmp_path / "reversion" / "bars").mkdir(parents=True)
    (tmp_path / "reversion" / "bars" / "2026-09-16-SPX.json").write_text("[]")
    (tmp_path / "reversion" / "bars" / "2026-09-15-SNDK.json").write_text("[]")
    assert store.days_on_disk(tmp_path, D) == [date(2026, 9, 16), date(2026, 9, 17)]


def test_the_command_builds_the_day_names_its_counts_and_writes_the_views(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(store, "CALENDAR", _state(tmp_path))
    assert store.main(["--state-dir", str(tmp_path), "--day", DAY]) == 0
    out = capsys.readouterr().out
    assert f"{DAY} reads 3, facts " in out and "grades 2 rows (0 quarantined)" in out and "views written" in out
    assert (store.store_dir(tmp_path) / store.DB_NAME).exists()


def test_a_record_whose_parts_are_malformed_is_quarantined_and_the_rest_of_the_day_is_built(tmp_path, monkeypatch):
    rows = _day_archive()
    rows[2]["requests"] = ["not a request"]
    monkeypatch.setattr(store, "CALENDAR", _state(tmp_path, rows))
    log = store.build_day(tmp_path, D, at(16, 45))
    assert [r["lane"] for r in _table(tmp_path, "reads")] == ["live", "live"]
    reasons = [q["reason"] for q in _table(tmp_path, "quarantine")]
    assert any(r.startswith("a malformed read record: AttributeError") for r in reasons), reasons
    assert log["grades"]["kept"] == 1                                    # the tape read's grade follows it out
