"""The recorder writes only what is missing and only finished sessions, and never touches a station file; the backup
mirrors the forecast folder, the recorder's own files and the market stores other jobs keep."""
from __future__ import annotations

import json
from datetime import datetime

from payload_fixtures import context_line, write_json, write_jsonl

from spx_claude_forecast import control, jsonl_store, recorder, station_stores, paths as forecast_paths
from spx_claude_forecast.control import ET
from spx_claude_forecast.paths import station_paths, station_store_files

MIDDAY = datetime(2026, 10, 9, 12, 0, tzinfo=ET)          # a Friday, mid-session
AFTER_CLOSE = datetime(2026, 10, 9, 16, 30, tzinfo=ET)
SATURDAY = datetime(2026, 10, 10, 5, 40, tzinfo=ET)


def test_book_stamp_is_file_safe_and_sorts_by_time():
    stamp = station_store_files.open_interest_book_stamp
    assert stamp("2026-10-09T17:10:04.746474-04:00") == "2026-10-09T171004-0400"
    assert stamp("2026-10-12T08:17:00-04:00") == "2026-10-12T081700-0400"
    assert stamp("2026-10-09T17:10:04-04:00") < stamp("2026-10-12T08:17:00-04:00")


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


def test_backup_mirrors_changed_files_and_skips_locks_and_run_logs(state_dir, tmp_path):
    paths = forecast_paths.ForecastPaths(state_dir)
    jsonl_store.append_json_line(paths.payloads_file("2026-10-09"), {"read_id": "live:a"})
    paths.lock_file("nightly").write_text("")
    backup_dir = tmp_path / "backup"
    mirror = forecast_paths.ForecastPaths(backup_dir)            # the backup keeps each file's path relative to the data root
    first = recorder.backup_forecast_folder(paths, backup_dir)
    assert first["copied"] >= 1 and mirror.payloads_file("2026-10-09").exists()
    assert not mirror.lock_file("nightly").parent.exists()
    assert recorder.backup_forecast_folder(paths, backup_dir)["copied"] == 0
    jsonl_store.append_json_line(paths.payloads_file("2026-10-09"), {"read_id": "live:b"})
    assert recorder.backup_forecast_folder(paths, backup_dir)["copied"] == 1
    assert not list(backup_dir.rglob("*.tmp"))


def test_backup_mirrors_the_market_stores_other_jobs_keep_and_leaves_todays_raw_tape(state_dir, tmp_path):
    """The dated-book job's history, the wall sensor's SPY volume files and the collector's finished raw days are
    mirrored under their own paths; today's raw day folder, still being written, is not."""
    paths = forecast_paths.ForecastPaths(state_dir)
    write_json(station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY.path(state_dir, stamp="2026-10-09T171004-0400"), {"ok": True})
    write_json(station_paths.SPY_VOLUME_BY_MINUTE.path(state_dir, day="2026-10-08"), {"day": "2026-10-08", "minute_volumes": {"570": 80}})
    raw = paths.raw_tape_folder
    (raw / "2026-10-08").mkdir(parents=True)
    (raw / "2026-10-08" / "tape.jsonl.gz").write_bytes(b"\x1f\x8b done")
    (raw / "2026-10-09").mkdir()
    (raw / "2026-10-09" / "tape.jsonl").write_text('{"c": 3}\n')
    backup_dir = tmp_path / "backup"
    assert recorder.backup_forecast_folder(paths, backup_dir, now=AFTER_CLOSE)["copied"] == 3
    assert station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY.path(backup_dir, stamp="2026-10-09T171004-0400").exists()
    assert station_paths.SPY_VOLUME_BY_MINUTE.path(backup_dir, day="2026-10-08").exists()
    assert (station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.path(backup_dir, day="2026-10-08") / "tape.jsonl.gz").exists()
    assert not station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.path(backup_dir, day="2026-10-09").exists()
    assert (raw / "2026-10-09" / "tape.jsonl").exists() and (raw / "2026-10-08" / "tape.jsonl.gz").exists()   # the station's files are untouched


def test_run_logs_each_task_and_a_failing_task_does_not_stop_the_rest(state_dir, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("schwab is down")

    monkeypatch.setattr(recorder, "fetch_spx_five_minute_bars", boom)
    monkeypatch.setattr(recorder, "is_market_hours", lambda now: False)
    monkeypatch.setattr(recorder, "now_et", lambda: AFTER_CLOSE)
    results = recorder.run(state_dir, ("spx_5m", "vix1d", "bogus", "backup"), backup_dir=tmp_path / "backup")
    assert "schwab is down" in results["spx_5m"]["error"] and results["vix1d"]["written"] == 0
    assert "unknown task" in results["bogus"]["error"]
    paths = forecast_paths.ForecastPaths(state_dir)
    jobs = [(l["job"], l["ok"]) for l in jsonl_store.read_json_lines(paths.run_log_file)]
    assert jobs == [("recorder:spx_5m", False), ("recorder:vix1d", True), ("recorder:bogus", False), ("recorder:backup", True)]


def test_run_over_every_task_offline_ends_with_the_files_in_the_backup(state_dir, tmp_path, monkeypatch):
    write_jsonl(station_stores.context_file(state_dir, "2026-10-08"),
                [context_line("2026-10-08T15:59:59-04:00", {"$VIX1D": {"last": 10.23, "close": 9.3}})])
    monkeypatch.setattr(recorder, "fetch_spx_five_minute_bars", lambda start, end: [{"ts": "2026-10-08T09:30:00-04:00", "close": 1.0}])
    monkeypatch.setattr(recorder, "fetch_leveraged_fund_sizes", lambda symbols: {"TQQQ": {"assets_usd": 38.7e9, "leverage": 3.0}})
    monkeypatch.setattr(recorder, "now_et", lambda: AFTER_CLOSE)
    results = recorder.run(state_dir, backup_dir=tmp_path / "backup")
    assert list(results) == list(recorder.TASKS) and not any("error" in r for r in results.values())
    assert results["spx_5m"]["days"] == ["2026-10-08"] and results["backup"]["copied"] >= 3
    mirror = forecast_paths.ForecastPaths(tmp_path / "backup")
    assert mirror.spx_5m_bars_file("2026-10-08").exists() and mirror.leveraged_fund_sizes_file.exists()
    assert mirror.vix1d_close_file.exists()
    assert len(jsonl_store.read_json_lines(forecast_paths.ForecastPaths(state_dir).run_log_file)) == len(recorder.TASKS)


def test_a_second_run_while_the_lock_is_held_does_nothing(state_dir, tmp_path):
    paths = forecast_paths.ForecastPaths(state_dir)
    with control.single_instance_lock(paths, recorder.LOCK_NAME):
        assert recorder.run(state_dir, ("vix1d",), backup_dir=tmp_path / "backup") == {}
    assert not jsonl_store.read_json_lines(paths.run_log_file)


def test_the_cli_runs_one_task_and_stands_down_on_the_kill_switch(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(control.DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.UPSTREAM_DISABLE_ENV, raising=False)
    state_dir = tmp_path / "state"
    assert recorder.main(["--state-dir", str(state_dir), "--task", "vix1d", "--backup-dir", str(tmp_path / "backup")]) == 0
    assert json.loads(capsys.readouterr().out)["vix1d"] == {"written": 0, "days": []}
    monkeypatch.setenv(control.DISABLE_ENV, "1")
    assert recorder.main(["--state-dir", str(tmp_path / "never"), "--task", "vix1d"]) == 0
    assert not (tmp_path / "never").exists()                                  # switched off: not even the folders
