"""The recorder copies only what is missing and only finished sessions, names copies by their own stamps, and
never touches a station file."""
from __future__ import annotations

import gzip
import json
from datetime import datetime

from payload_fixtures import context_line, write_json, write_jsonl

from spx_claude_forecast import control, jsonl_store, recorder, station_stores, paths as forecast_paths
from spx_claude_forecast.control import ET

MIDDAY = datetime(2026, 10, 9, 12, 0, tzinfo=ET)          # a Friday, mid-session
AFTER_CLOSE = datetime(2026, 10, 9, 16, 30, tzinfo=ET)
SATURDAY = datetime(2026, 10, 10, 5, 40, tzinfo=ET)


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
    assert first["copied"] == 1 and again["copied"] == 0
    assert paths.dated_book_copy_file("2026-10-09T171004-0400").exists()
    write_json(book, {"ok": True, "as_of": "2026-10-12T08:17:00-04:00", "bands": []})     # Monday overwrites it
    assert recorder.copy_dated_book(paths, state_dir)["copied"] == 1
    assert len(list((paths.recorder_dir / "dated_book").glob("*.json"))) == 2


def test_a_book_without_a_usable_as_of_is_reported_not_raised(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    book = station_stores.dated_book_file(state_dir)
    write_json(book, {"ok": False, "as_of": None})
    assert recorder.copy_dated_book(paths, state_dir)["copied"] == 0
    write_json(book, {"ok": True, "as_of": "yesterday-ish"})
    assert "as_of" in recorder.copy_dated_book(paths, state_dir)["why"]
    assert not list((paths.recorder_dir / "dated_book").glob("*.json"))


def test_siege_days_are_copied_one_file_per_finished_day_and_only_when_missing(state_dir):
    """Siege overwrites today's entry on every scan, so today is copied only once the close has printed."""
    paths = forecast_paths.ForecastPaths(state_dir)
    write_json(station_stores.siege_baseline_file(state_dir),
               {"days": {"2026-10-07": {"570": 100, "571": 90}, "2026-10-08": {"570": 80}, "2026-10-09": {"570": 5}}})
    assert recorder.copy_siege_spy_minutes(paths, state_dir, now=MIDDAY)["days"] == ["2026-10-07", "2026-10-08"]
    copy = json.loads(paths.siege_spy_minutes_file("2026-10-07").read_text())
    assert copy["minute_volumes"] == {"570": 100, "571": 90} and copy["minutes_present"] == 2
    assert not paths.siege_spy_minutes_file("2026-10-09").exists()
    assert recorder.copy_siege_spy_minutes(paths, state_dir, now=AFTER_CLOSE)["days"] == ["2026-10-09"]
    assert recorder.copy_siege_spy_minutes(paths, state_dir, now=AFTER_CLOSE)["copied"] == 0


def test_vix1d_close_is_the_last_quote_of_a_finished_session_only(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    write_jsonl(station_stores.context_file(state_dir, "2026-10-08"), [
        context_line("2026-10-08T15:58:00-04:00", {"$VIX1D": {"last": 10.1, "close": 9.3}}),
        context_line("2026-10-08T15:59:59-04:00", {"$VIX1D": {"last": 10.23, "close": 9.3}}),
        context_line("2026-10-08T16:00:30-04:00", bars={}),
    ])
    write_jsonl(station_stores.context_file(state_dir, "2026-10-09"), [
        context_line("2026-10-09T10:00:00-04:00", {"$VIX1D": {"last": 9.5, "close": 10.24}}),
    ])
    assert recorder.record_vix1d_close(paths, state_dir, now=MIDDAY)["days"] == ["2026-10-08"]
    assert recorder.record_vix1d_close(paths, state_dir, now=AFTER_CLOSE)["days"] == ["2026-10-09"]
    lines = jsonl_store.read_json_lines(paths.vix1d_close_file)
    assert [(l["day"], l["last"], l["previous_close"]) for l in lines] == [("2026-10-08", 10.23, 9.3), ("2026-10-09", 9.5, 10.24)]
    assert recorder.record_vix1d_close(paths, state_dir, now=AFTER_CLOSE)["written"] == 0


def test_finished_session_days_reads_the_clock_in_new_york_whatever_zone_it_is_given():
    from datetime import timezone
    days = ["2026-10-08", "2026-10-09"]
    at_16_30_et_as_utc = datetime(2026, 10, 9, 20, 30, tzinfo=timezone.utc)
    assert recorder.finished_session_days(days, at_16_30_et_as_utc) == days
    at_12_00_et_as_utc = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)
    assert recorder.finished_session_days(days, at_12_00_et_as_utc) == ["2026-10-08"]


def test_spx_5m_bars_are_split_by_session_and_skipped_in_market_hours_and_on_weekends(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    bars = [{"ts": "2026-10-08T09:30:00-04:00", "close": 1.0}, {"ts": "2026-10-08T09:35:00-04:00", "close": 2.0},
            {"ts": "2026-10-09T09:30:00-04:00", "close": 3.0}]
    calls = []

    def fake_fetch(start, end):
        calls.append((start, end))
        return bars

    during = datetime(2026, 10, 9, 11, 0, tzinfo=ET)
    assert recorder.save_spx_5m_bars(paths, now=during, fetch=fake_fetch)["written"] == 0 and not calls
    assert recorder.save_spx_5m_bars(paths, now=SATURDAY, fetch=fake_fetch)["why"].startswith("weekend") and not calls
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
    result = recorder.copy_lob_raw(paths, state_dir, now=AFTER_CLOSE)
    assert result["days"] == ["2026-09-09", "2026-10-08"]
    with gzip.open(paths.lob_raw_copy_dir("2026-10-08") / "tape.jsonl.gz", "rb") as f:
        assert f.read() == b'{"b": 2}\n' * 3
    manifest = json.loads((paths.lob_raw_copy_dir("2026-10-08") / recorder.LOB_RAW_MANIFEST_NAME).read_text())
    assert manifest["files"]["tape.jsonl.gz"]["source_bytes"] == len('{"b": 2}\n' * 3)
    assert not paths.lob_raw_copy_dir("2026-10-09").exists()
    assert recorder.copy_lob_raw(paths, state_dir, now=AFTER_CLOSE)["copied"] == 0
    assert (raw / "2026-10-08" / "tape.jsonl").exists()                   # the station's file is untouched


def test_a_raw_day_the_collector_is_still_compressing_waits_for_the_next_run(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    raw = station_stores.lob_flow_raw_dir(state_dir)
    (raw / "2026-10-08").mkdir(parents=True)
    (raw / "2026-10-08" / "tape.jsonl").write_text('{"b": 2}\n')
    (raw / "2026-10-08" / "tape.jsonl.gz").write_bytes(b"\x1f\x8b half")      # the collector's gzip, mid-write
    result = recorder.copy_lob_raw(paths, state_dir, now=AFTER_CLOSE)
    assert result["copied"] == 0 and result["left_mid_compress"] == ["2026-10-08"]
    assert not paths.lob_raw_copy_dir("2026-10-08").exists()
    (raw / "2026-10-08" / "tape.jsonl").unlink()                              # the collector finished
    assert recorder.copy_lob_raw(paths, state_dir, now=AFTER_CLOSE)["days"] == ["2026-10-08"]


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
    assert not list(backup_dir.rglob("*.tmp"))


def test_run_logs_each_task_and_a_failing_task_does_not_stop_the_rest(state_dir, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("schwab is down")

    monkeypatch.setattr(recorder, "fetch_spx_five_minute_bars", boom)
    monkeypatch.setattr(recorder, "is_market_hours", lambda now: False)
    monkeypatch.setattr(recorder, "now_et", lambda: AFTER_CLOSE)
    results = recorder.run(state_dir, ("spx_5m", "siege", "bogus", "backup"), backup_dir=tmp_path / "backup")
    assert "schwab is down" in results["spx_5m"]["error"] and "copied" in results["siege"]
    assert "unknown task" in results["bogus"]["error"]
    paths = forecast_paths.ForecastPaths(state_dir)
    jobs = [(l["job"], l["ok"]) for l in jsonl_store.read_json_lines(paths.run_log_file)]
    assert jobs == [("recorder:spx_5m", False), ("recorder:siege", True), ("recorder:bogus", False), ("recorder:backup", True)]


def test_run_over_every_task_offline_ends_with_the_copies_in_the_backup(state_dir, tmp_path, monkeypatch):
    write_json(station_stores.dated_book_file(state_dir), {"ok": True, "as_of": "2026-10-09T17:10:04-04:00", "bands": []})
    write_json(station_stores.siege_baseline_file(state_dir), {"days": {"2026-10-08": {"570": 80}}})
    write_jsonl(station_stores.context_file(state_dir, "2026-10-08"),
                [context_line("2026-10-08T15:59:59-04:00", {"$VIX1D": {"last": 10.23, "close": 9.3}})])
    monkeypatch.setattr(recorder, "fetch_spx_five_minute_bars", lambda start, end: [{"ts": "2026-10-08T09:30:00-04:00", "close": 1.0}])
    monkeypatch.setattr(recorder, "now_et", lambda: AFTER_CLOSE)
    results = recorder.run(state_dir, backup_dir=tmp_path / "backup")
    assert list(results) == list(recorder.TASKS) and not any("error" in r for r in results.values())
    assert results["spx_5m"]["days"] == ["2026-10-08"] and results["backup"]["copied"] >= 4
    assert (tmp_path / "backup" / "recorder" / "dated_book" / "2026-10-09T171004-0400.json").exists()
    assert (tmp_path / "backup" / "recorder" / "vix1d_close.jsonl").exists()
    assert len(jsonl_store.read_json_lines(forecast_paths.ForecastPaths(state_dir).run_log_file)) == len(recorder.TASKS)


def test_a_second_run_while_the_lock_is_held_does_nothing(state_dir, tmp_path):
    paths = forecast_paths.ForecastPaths(state_dir)
    with control.single_instance_lock(paths, recorder.LOCK_NAME):
        assert recorder.run(state_dir, ("siege",), backup_dir=tmp_path / "backup") == {}
    assert not jsonl_store.read_json_lines(paths.run_log_file)


def test_the_cli_runs_one_task_and_stands_down_on_the_kill_switch(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(control.DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.UPSTREAM_DISABLE_ENV, raising=False)
    state_dir = tmp_path / "state"
    assert recorder.main(["--state-dir", str(state_dir), "--task", "siege", "--backup-dir", str(tmp_path / "backup")]) == 0
    assert json.loads(capsys.readouterr().out)["siege"]["why"] == "no baseline on disk"
    monkeypatch.setenv(control.DISABLE_ENV, "1")
    assert recorder.main(["--state-dir", str(tmp_path / "never"), "--task", "siege"]) == 0
    assert not (tmp_path / "never").exists()                                  # switched off: not even the folders
