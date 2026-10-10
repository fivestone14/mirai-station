"""The kill switch, the pause switch, the single-instance lock, and the run log."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from spx_claude_forecast import control, jsonl_store, paths as forecast_paths


def test_the_kill_switch_names_its_reason(monkeypatch):
    monkeypatch.delenv(control.DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.UPSTREAM_DISABLE_ENV, raising=False)
    assert control.switched_off_reason() is None
    monkeypatch.setenv(control.DISABLE_ENV, "1")
    assert control.DISABLE_ENV in control.switched_off_reason()
    monkeypatch.delenv(control.DISABLE_ENV)
    monkeypatch.setenv(control.UPSTREAM_DISABLE_ENV, "1")
    assert "no SPX reads" in control.switched_off_reason()


def test_a_missing_control_file_fails_open_and_pause_round_trips(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    assert control.is_paused(paths) is False
    assert control.write_default_control(paths) is True and control.write_default_control(paths) is False
    control.set_paused(paths, True)
    assert control.is_paused(paths) is True and control.load_control(paths)["max_calls_per_day"] == 45
    control.set_paused(paths, False)
    assert control.is_paused(paths) is False


def test_a_corrupt_control_file_fails_open(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    paths.control_file.write_text("{not json", encoding="utf-8")
    assert control.is_paused(paths) is False


def test_the_cli_writes_the_defaults_once_then_flips_the_pause_switch(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(control.DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.UPSTREAM_DISABLE_ENV, raising=False)
    state_dir = tmp_path / "state"
    paths = forecast_paths.ForecastPaths(state_dir)
    assert control.main(["--state-dir", str(state_dir)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert paths.control_file.exists() and shown["paused"] is False and shown["switched_off"] is None
    assert control.main(["--state-dir", str(state_dir), "--pause"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is True and control.is_paused(paths) is True
    assert control.main(["--state-dir", str(state_dir), "--resume"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is False and control.is_paused(paths) is False


def test_the_single_instance_lock_turns_a_second_holder_away_without_waiting(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    with control.single_instance_lock(paths, "recorder") as held:
        assert held is True and paths.lock_file("recorder").exists()
        with control.single_instance_lock(paths, "recorder") as held_again:
            assert held_again is False
        with control.single_instance_lock(paths, "nightly") as other_job:
            assert other_job is True
    with control.single_instance_lock(paths, "recorder") as held_later:
        assert held_later is True


def test_the_run_log_keeps_recent_lines_and_prunes_old_ones(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    control.log_job_run(paths, "recorder:siege", old, True, copied=3)
    control.log_job_run(paths, "recorder:siege", control.now_utc_iso(), False, error="boom")
    assert control.prune_run_log(paths) == 1
    lines = jsonl_store.read_json_lines(paths.run_log_file)
    assert len(lines) == 1 and lines[0]["ok"] is False and lines[0]["error"] == "boom"
