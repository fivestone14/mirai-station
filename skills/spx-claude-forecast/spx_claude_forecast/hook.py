"""The one line the SPX read calls: start the forecast for a read in its own process, and never cost the read.

``spawn_claude_forecast(state_dir, read_id)`` is called from ``spx_jev.service.run_once`` right after the
read's record is archived. It checks the kill switches, starts ``python -m spx_claude_forecast.read_runner``
detached (its own session, output to ``run_logs/read_runner.out``), and returns at once. It never raises:
every failure is a False.

Under pytest it spawns nothing. pytest sets PYTEST_CURRENT_TEST for every test, and the SPX read's own
tests run ``run_once`` on synthetic states realistic enough that a spawned runner would build a payload and
call Claude for real (it happened on 2026-10-09); those tests must never reach a real call.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .control import PYTEST_ENV, switched_off_reason
from .paths import SKILL_DIR, ForecastPaths


def spawn_claude_forecast(state_dir: Path | str, read_id: str) -> bool:
    """Start the read runner for one read in the background; True when it was started."""
    try:
        if switched_off_reason() or os.environ.get(PYTEST_ENV):
            return False
        paths = ForecastPaths(Path(state_dir).expanduser())
        paths.run_log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(paths.run_log_file.parent / "read_runner.out", "ab") as out:
            cmd = [sys.executable, "-m", "spx_claude_forecast.read_runner", "--state-dir", str(state_dir), "--read-id", read_id]
            subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                             cwd=str(SKILL_DIR), close_fds=True)
        return True
    except Exception:
        return False
