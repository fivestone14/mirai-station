"""The hook the SPX read calls (detached, never raising) and the seed that rebuilds past half-hours."""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from payload_fixtures import station_state_dir

from spx_claude_forecast import hook, jsonl_store, seed, paths as forecast_paths
from spx_claude_forecast.control import ET


def test_the_hook_starts_the_read_runner_detached_and_obeys_the_kill_switch(tmp_path, monkeypatch):
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    monkeypatch.delenv(hook.UPSTREAM_DISABLE_ENV, raising=False)
    started = []

    def fake_popen(cmd, **kwargs):
        started.append((cmd, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert hook.spawn_claude_forecast(tmp_path / "state", "live:2026-10-09T14:30:12-04:00") is True
    cmd, kwargs = started[0]
    assert cmd[1:4] == ["-m", "spx_claude_forecast.read_runner", "--state-dir"] and cmd[-1] == "live:2026-10-09T14:30:12-04:00"
    assert kwargs["start_new_session"] is True and kwargs["cwd"].endswith("spx-claude-forecast")
    assert (tmp_path / "state" / "spx_claude_forecast" / "run_logs" / "read_runner.out").exists()
    monkeypatch.setenv(hook.DISABLE_ENV, "1")
    assert hook.spawn_claude_forecast(tmp_path / "state", "live:x") is False and len(started) == 1


def test_the_hook_never_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    monkeypatch.delenv(hook.UPSTREAM_DISABLE_ENV, raising=False)

    def broken(*a, **k):
        raise OSError("no python")

    monkeypatch.setattr(subprocess, "Popen", broken)
    assert hook.spawn_claude_forecast(tmp_path / "state", "live:x") is False


def test_seed_slots_cover_every_half_hour_of_the_session():
    assert seed.SEED_SLOTS[0] == "09:30" and seed.SEED_SLOTS[-1] == "15:30" and len(seed.SEED_SLOTS) == 13


@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_seeding_one_real_day_writes_a_payload_and_an_outcome_per_slot(tmp_path):
    real = station_state_dir()
    state = tmp_path / "state"
    state.mkdir()
    for name in ("reversion", "spx_jev", "lob_flow", "siege", "dated_gex"):
        (state / name).symlink_to(real / name)
    paths = forecast_paths.ensure_folders(state)
    counts = seed.seed_day(state, paths, "2026-10-01")
    assert counts["built"] + counts["blind"] == 13 and counts["errors"] == 0, counts
    payloads = jsonl_store.read_json_lines(paths.seed_payloads_file("2026-10-01"))
    outcomes = jsonl_store.read_json_lines(paths.seed_outcomes_file("2026-10-01"))
    built = [p for p in payloads if p["status"] == "built"]
    assert len(built) == counts["built"] and all(p["origin"] == "seed" and p["read_id"].startswith("seed:") for p in built)
    assert len(outcomes) == 3 * len(built)
    final = [o for o in outcomes if o["result"]["status"] == "final"]
    assert final and all(o["read_source"] == "seed" for o in final)
    assert seed.seed_day(state, paths, "2026-10-01")["skipped"] == 13          # idempotent
