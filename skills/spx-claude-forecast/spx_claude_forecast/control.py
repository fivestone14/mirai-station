"""The switches, the caps, the clock words and the run log every job in this package shares.

Kill switch: ``SPX_CLAUDE_FORECAST_DISABLE=1`` stops every job dead (no row, no call). The package also
stands down when ``SPX_JEV_DISABLE=1``, because without the SPX read there is nothing to forecast.
Pause switch: ``control.json`` with ``"paused": true`` silences only the Claude call; every record
still lands (a gap in the record costs more than a gap in the answers). The daily caps live there too.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonl_store
from .paths import RUN_LOG_KEPT_DAYS, ForecastPaths

DISABLE_ENV = "SPX_CLAUDE_FORECAST_DISABLE"
UPSTREAM_DISABLE_ENV = "SPX_JEV_DISABLE"          # the SPX read's own switch; off means no reads to forecast
ET = ZoneInfo("America/New_York")
JOB_NAME = "spx-claude-forecast"

DEFAULT_CONTROL = {
    "paused": False,                 # True silences the Claude call; records keep landing
    "max_calls_per_day": 45,         # 26 live calls plus the nightly test variants, with room; a backstop, not a budget
    "max_usd_per_day": 8.0,          # list-price equivalent of the calls' own bills; notional on the subscription
    "note": "Edit by hand or with: python -m spx_claude_forecast.control --pause / --resume",
}


def log(msg: str) -> None:
    """One line to stderr with the UTC clock, so the launchd log reads in order."""
    print(f"{now_utc_iso()} {JOB_NAME} :: {msg}", file=sys.stderr)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_et() -> datetime:
    return datetime.now(ET)


def today_et() -> str:
    return now_et().date().isoformat()


def is_switched_off() -> str | None:
    """The reason the package must not run, or None."""
    if os.environ.get(DISABLE_ENV) == "1":
        return f"{DISABLE_ENV}=1"
    if os.environ.get(UPSTREAM_DISABLE_ENV) == "1":
        return f"{UPSTREAM_DISABLE_ENV}=1 (no SPX reads to forecast)"
    return None


def load_control(paths: ForecastPaths) -> dict:
    """The control file over the defaults. Fails OPEN: a missing or unreadable file means nobody ever
    touched the switch, which is not the same as asking for silence."""
    try:
        data = json.loads(paths.control_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return dict(DEFAULT_CONTROL)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONTROL)
    return {**DEFAULT_CONTROL, **data}


def write_default_control(paths: ForecastPaths) -> bool:
    """Write the defaults once, so the file is there to edit; True when it was created."""
    if paths.control_file.exists():
        return False
    jsonl_store.write_json_atomically(paths.control_file, DEFAULT_CONTROL)
    return True


def set_paused(paths: ForecastPaths, paused: bool) -> dict:
    control = {**load_control(paths), "paused": bool(paused)}
    jsonl_store.write_json_atomically(paths.control_file, control)
    return control


def is_paused(paths: ForecastPaths) -> bool:
    return bool(load_control(paths).get("paused", False))


def log_job_run(paths: ForecastPaths, job: str, started_at: str, ok: bool, error: str | None = None, **counts) -> None:
    """One line per job run in run_logs/job_run_log.jsonl; never raises."""
    record = {"job": job, "started_at": started_at, "finished_at": now_utc_iso(), "ok": bool(ok), **counts}
    if error:
        record["error"] = error
        record["trace"] = traceback.format_exc()[-1500:]
    try:
        jsonl_store.append_json_line(paths.run_log_file, record, fsync=False)
    except Exception as e:                                               # a log line is never worth a crash
        log(f"run log skipped: {type(e).__name__}: {e}")


def prune_run_log(paths: ForecastPaths, now: datetime | None = None, keep_days: int = RUN_LOG_KEPT_DAYS) -> int:
    """Drop run-log lines older than ``keep_days``; returns how many were dropped."""
    if not paths.run_log_file.exists():
        return 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=keep_days)).isoformat(timespec="seconds")
    lines = jsonl_store.read_json_lines(paths.run_log_file)
    kept = [line for line in lines if str(line.get("started_at", "")) >= cutoff]
    dropped = len(lines) - len(kept)
    if dropped:
        body = "".join(json.dumps(line, ensure_ascii=False, default=str) + "\n" for line in kept)
        jsonl_store.write_bytes_atomically(paths.run_log_file, body.encode("utf-8"))
    return dropped


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .paths import default_state_dir, ensure_folders

    parser = argparse.ArgumentParser(description="Read or flip the spx-claude-forecast pause switch.")
    parser.add_argument("--state-dir", default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pause", action="store_true")
    group.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    paths = ensure_folders(Path(args.state_dir) if args.state_dir else default_state_dir())
    jsonl_store.allow_writes_under(paths.root)
    if args.pause or args.resume:
        control = set_paused(paths, args.pause)
    else:
        write_default_control(paths)
        control = load_control(paths)
    print(json.dumps({"switched_off": is_switched_off(), **control}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
