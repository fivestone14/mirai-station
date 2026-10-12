"""The one line the live service calls after it has saved a read: start the read hook in a separate process and return.

spawn_after_read never raises and never waits: it launches ``python -m spx_jev.mirai_prediction.read_hook`` detached
(its own session, output appended to operations/logs/forecaster_forecasts_job_log.txt), so the live read, its grading and the phone's card are
never delayed or broken by anything this system does. Set SPX_JEV_PREDICTION_DISABLE=1 to turn the hook off.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .paths import data_root, read_hook_log_file

DISABLE_ENV = "SPX_JEV_PREDICTION_DISABLE"


def spawn_after_read(state_dir: Path | str, lane: str, read_id: str) -> bool:
    """Start the read hook for one read in the background; True when it was started."""
    try:
        if os.environ.get(DISABLE_ENV) == "1":
            return False
        log_file = read_hook_log_file(data_root(state_dir))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        out = open(log_file, "ab")
        cmd = [sys.executable, "-m", "spx_jev.mirai_prediction.read_hook", "--state-dir", str(state_dir), "--lane", lane, "--read-id", read_id]
        subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                         cwd=str(Path(__file__).resolve().parents[2]), close_fds=True)
        out.close()
        return True
    except Exception:
        return False
