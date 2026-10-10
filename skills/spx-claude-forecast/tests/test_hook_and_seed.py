"""The hook the SPX read calls (detached, never raising, never under pytest) and the seed that rebuilds past half-hours."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta

import pytest

from payload_fixtures import station_state_dir

from spx_claude_forecast import control, hook, jsonl_store, seed, paths as forecast_paths
from spx_claude_forecast.control import ET


def _outside_pytest(monkeypatch) -> None:
    """The hook stands down under pytest; these tests pretend to be the live station."""
    monkeypatch.delenv(control.DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.UPSTREAM_DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.PYTEST_ENV, raising=False)


def test_the_hook_starts_the_read_runner_detached_and_obeys_the_kill_switch(tmp_path, monkeypatch):
    _outside_pytest(monkeypatch)
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
    monkeypatch.setenv(control.DISABLE_ENV, "1")
    assert hook.spawn_claude_forecast(tmp_path / "state", "live:x") is False and len(started) == 1


def test_the_hook_spawns_nothing_under_pytest(tmp_path, monkeypatch):
    """pytest sets PYTEST_CURRENT_TEST for every test; the SPX read's own tests must never reach a real Claude call."""
    monkeypatch.delenv(control.DISABLE_ENV, raising=False)
    monkeypatch.delenv(control.UPSTREAM_DISABLE_ENV, raising=False)
    assert control.PYTEST_ENV in os.environ
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: started.append(cmd))
    assert hook.spawn_claude_forecast(tmp_path / "state", "live:2026-10-09T14:30:12-04:00") is False
    assert started == []


def test_the_hook_never_raises(tmp_path, monkeypatch):
    _outside_pytest(monkeypatch)

    def broken(*a, **k):
        raise OSError("no python")

    monkeypatch.setattr(subprocess, "Popen", broken)
    assert hook.spawn_claude_forecast(tmp_path / "state", "live:x") is False


def test_seed_slots_cover_every_half_hour_of_the_session():
    assert seed.SEED_SLOTS[0] == "09:30" and seed.SEED_SLOTS[-1] == "15:30" and len(seed.SEED_SLOTS) == 13


def test_the_seed_takes_the_row_a_live_read_would_have_taken():
    slot_at = datetime(2026, 10, 1, 14, 30, tzinfo=ET)
    rows = [slot_at + timedelta(seconds=s) for s in (-205, -86, 4, 141, 224)]       # 14:26:35 ... 14:33:44
    assert seed.row_time_for_slot(rows, slot_at) == slot_at + timedelta(seconds=4)   # the newest row up to slot + 2 min
    assert seed.row_time_for_slot(rows[:2], slot_at) == slot_at - timedelta(seconds=86)   # none after the slot: the last before
    assert seed.row_time_for_slot(rows[:1], slot_at) is None                        # the only row is too far before the slot
    assert seed.row_time_for_slot(rows[4:], slot_at) is None                        # the only row is past the window
    assert seed.row_time_for_slot([], slot_at) is None


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
    for p in built:                                                   # cut at the diary row itself, within 2 min of the slot
        cut, row = datetime.fromisoformat(p["cut_at"]), datetime.fromisoformat(p["logged_never_sent"]["row_ts"])
        slot_at = datetime.fromisoformat(f"2026-10-01T{p['half_hour_slot_et']}:00").replace(tzinfo=ET)
        assert cut == row and abs((cut - slot_at).total_seconds()) <= 120, p["read_id"]
    assert len(outcomes) == 3 * len(built)
    final = [o for o in outcomes if o["result"]["status"] == "final"]
    assert final and all(o["read_source"] == "seed" and o["claude_input_sha256"].startswith("sha256:") for o in final)
    assert set(final[0]["missing_comparison_forecasts"]) == set(seed.SEED_MISSING_COMPARISONS)
    assert seed.seed_day(state, paths, "2026-10-01")["skipped"] == 13          # idempotent
