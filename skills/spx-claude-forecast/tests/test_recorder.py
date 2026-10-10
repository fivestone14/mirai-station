"""The recorder copies only what is missing, names copies by their own stamps, and never touches a station file."""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

from conftest import write_json, write_jsonl

from spx_claude_forecast import jsonl_store, recorder, station_stores, paths as forecast_paths
from spx_claude_forecast.control import ET


def test_book_stamp_is_file_safe_and_sorts_by_time():
    assert recorder.book_stamp("2026-10-09T17:10:04.746474-04:00") == "2026-10-09T171004-0400"
    assert recorder.book_stamp("2026-10-12T08:17:00-04:00") == "2026-10-12T081700-0400"
    assert recorder.book_stamp("2026-10-09T17:10:04-04:00") < recorder.book_stamp("2026-10-12T08:17:00-04:00")


def test_the_dated_book_is_copied_once_per_as_of(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    book = station_stores.dated_book_file(state_dir)
    write_json(book, {"ok": True, "as_of": "2026-10-09T17:10:04.746474-04:00", "bands": []})
    first = recorder.copy_dated_book(paths, state_dir)
    again = recorder.copy_dated_book(paths, state_dir)
    assert first["copied"] is True and again["copied"] is False
    assert paths.dated_book_copy("2026-10-09T171004-0400").exists()
    write_json(book, {"ok": True, "as_of": "2026-10-12T08:17:00-04:00", "bands": []})     # Monday overwrites it
    assert recorder.copy_dated_book(paths, state_dir)["copied"] is True
    assert len(list((paths.recorder_dir / "dated_book").glob("*.json"))) == 2


def test_siege_days_are_copied_one_file_per_day_and_only_when_missing(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    write_json(station_stores.siege_baseline_file(state_dir),
               {"days": {"2026-07-20": {"570": 100, "571": 90}, "2026-07-21": {"570": 80}}})
    assert recorder.copy_siege_spy_minutes(paths, state_dir)["copied"] == 2
    copy = json.loads(paths.siege_spy_minutes_file("2026-07-20").read_text())
    assert copy["minute_volumes"] == {"570": 100, "571": 90} and copy["minutes_present"] == 2
    assert recorder.copy_siege_spy_minutes(paths, state_dir)["copied"] == 0


def test_vix1d_close_is_the_last_quote_of_a_finished_session_only(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    write_jsonl(station_stores.context_file(state_dir, "2026-10-08"), [
        {"ts": "2026-10-08T15:58:00-04:00", "quotes": {"$VIX1D": {"last": 10.1, "close": 10.24}}},
        {"ts": "2026-10-08T15:59:59-04:00", "quotes": {"$VIX1D": {"last": 10.23, "close": 10.24}}},
        {"ts": "2026-10-08T16:00:30-04:00", "bars": {}},
    ])
    write_jsonl(station_stores.context_file(state_dir, "2026-10-09"), [
        {"ts": "2026-10-09T10:00:00-04:00", "quotes": {"$VIX1D": {"last": 9.5, "close": 10.24}}},
    ])
    midday = datetime(2026, 10, 9, 12, 0, tzinfo=ET)
    assert recorder.record_vix1d_close(paths, state_dir, now=midday)["days"] == ["2026-10-08"]
    after_close = datetime(2026, 10, 9, 16, 30, tzinfo=ET)
    assert recorder.record_vix1d_close(paths, state_dir, now=after_close)["days"] == ["2026-10-09"]
    lines = jsonl_store.read_json_lines(paths.vix1d_close_file)
    assert [(l["day"], l["last"]) for l in lines] == [("2026-10-08", 10.23), ("2026-10-09", 9.5)]
    assert recorder.record_vix1d_close(paths, state_dir, now=after_close)["written"] == 0


def test_spx_5m_bars_are_split_by_session_and_skipped_in_market_hours(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    bars = [{"ts": "2026-10-08T09:30:00-04:00", "close": 1.0}, {"ts": "2026-10-08T09:35:00-04:00", "close": 2.0},
            {"ts": "2026-10-09T09:30:00-04:00", "close": 3.0}]
    calls = []

    def fake_fetch(start, end):
        calls.append((start, end))
        return bars

    during = datetime(2026, 10, 9, 11, 0, tzinfo=ET)
    assert recorder.save_spx_5m_bars(paths, now=during, fetch=fake_fetch)["written"] == 0 and not calls
    evening = datetime(2026, 10, 9, 19, 0, tzinfo=ET)
    result = recorder.save_spx_5m_bars(paths, now=evening, fetch=fake_fetch)
    assert result["days"] == ["2026-10-08", "2026-10-09"] and result["earliest_served"] == "2026-10-08"
    assert len(jsonl_store.read_json_lines(paths.spx_5m_bars_file("2026-10-08"))) == 2
    assert recorder.save_spx_5m_bars(paths, now=evening, fetch=fake_fetch)["written"] == 0


def test_raw_tape_days_are_gzipped_marked_complete_and_todays_left_alone(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    raw = station_stores.lob_flow_raw_dir(state_dir)
    (raw / "2026-09-09").mkdir(parents=True)
    with gzip.open(raw / "2026-09-09" / "tape.jsonl.gz", "wb") as f:
        f.write(b'{"a": 1}\n')
    (raw / "2026-10-08").mkdir()
    (raw / "2026-10-08" / "tape.jsonl").write_text('{"b": 2}\n' * 3)
    (raw / "2026-10-09").mkdir()
    (raw / "2026-10-09" / "tape.jsonl").write_text('{"c": 3}\n')
    now = datetime(2026, 10, 9, 16, 25, tzinfo=ET)
    result = recorder.copy_lob_raw(paths, state_dir, now=now)
    assert result["days"] == ["2026-09-09", "2026-10-08"]
    with gzip.open(paths.lob_raw_copy_dir("2026-10-08") / "tape.jsonl.gz", "rb") as f:
        assert f.read() == b'{"b": 2}\n' * 3
    assert (paths.lob_raw_copy_dir("2026-10-08") / recorder.LOB_RAW_COMPLETE_MARK).exists()
    assert not paths.lob_raw_copy_dir("2026-10-09").exists()
    assert recorder.copy_lob_raw(paths, state_dir, now=now)["copied"] == 0
    assert (raw / "2026-10-08" / "tape.jsonl").exists()                   # the station's file is untouched


def test_backup_mirrors_changed_files_and_skips_locks_and_run_logs(state_dir, tmp_path):
    paths = forecast_paths.ForecastPaths(state_dir)
    jsonl_store.append_json_line(paths.payloads_file("2026-10-09"), {"read_id": "live:a"})
    paths.lock_file("nightly").write_text("")
    backup_dir = tmp_path / "backup"
    first = recorder.backup_forecast_folder(paths, backup_dir)
    assert first["copied"] >= 1 and (backup_dir / "payloads" / "2026-10-09.jsonl").exists()
    assert not (backup_dir / "locks").exists()
    assert recorder.backup_forecast_folder(paths, backup_dir)["copied"] == 0
    jsonl_store.append_json_line(paths.payloads_file("2026-10-09"), {"read_id": "live:b"})
    assert recorder.backup_forecast_folder(paths, backup_dir)["copied"] == 1


def test_run_logs_each_task_and_a_failing_task_does_not_stop_the_rest(state_dir, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("schwab is down")

    monkeypatch.setattr(recorder, "save_spx_5m_bars", boom)
    results = recorder.run(state_dir, ("spx_5m", "siege", "backup"), backup_dir=tmp_path / "backup")
    assert "schwab is down" in results["spx_5m"]["error"] and "copied" in results["siege"]
    paths = forecast_paths.ForecastPaths(state_dir)
    jobs = [(l["job"], l["ok"]) for l in jsonl_store.read_json_lines(paths.run_log_file)]
    assert ("recorder:spx_5m", False) in jobs and ("recorder:siege", True) in jobs
