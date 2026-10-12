"""
io_state.py — the FILING SYSTEM of the box: every byte that touches disk goes
through here. Atomic writes (tmp+rename, never a torn read), the latest.json
handshake the host tick reads, append-only raw journals with cursors for
crash-replay, and the gzip of finished raw days (kept forever).

Every file is named in station_paths.py; StateDir is handed the station's data root and asks it:
    spx/scanner/spx_order_flow_readings_latest.json      # the ONLY file the host's tick reads
    spx/scanner/spx_dealer_gamma_levels_latest.json      # written by the host, read by the daemon
    settings/market_event_calendar_by_event.json         # the station's one event calendar, versioned in the repo (read-only)
    spx/scanner/spx_options_trades_resume_point_state.json, operations/locks/spx_options_tape_collector.lock
    spx/scanner/{spx_options,spy}_depth_baselines_state.json
    market/spx_same_day_options_raw_day_folders/{date}/{stream}.jsonl   # per-day append-only journals, gzipped once the day is over, never pruned
    spx/scanner/spx_order_flow_readings_by_minute/{date}.jsonl          # per-fold aggregate rows (kept forever)
    spx/scanner/spx_order_flow_grades_by_day/{date}.json                # nightly head-to-head grades
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

_REPO_ROOT = str(Path(__file__).resolve().parents[3])             # station_paths.py names every store
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402
import station_store_files  # noqa: E402

# ---------------------------------------------------------------------------
# Atomic primitives
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, obj: dict) -> None:
    """tmp+rename so a reader can never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, default=str))
    os.replace(tmp, path)


def read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# StateDir — one object, all paths
# ---------------------------------------------------------------------------


class StateDir:
    def __init__(self, root: Path):
        self.state_root = Path(root)                              # the station's data root (data/)

    @property
    def raw_root(self) -> Path:
        """The raw day folders' parent: one folder a day, kept forever (finished days gzipped in place)."""
        return station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.folder(self.state_root)

    # -- named files ---------------------------------------------------------
    @property
    def latest(self) -> Path: return station_paths.SPX_ORDER_FLOW_READINGS_LATEST.path(self.state_root)
    @property
    def gex_context(self) -> Path: return station_paths.SPX_DEALER_GAMMA_LEVELS_LATEST.path(self.state_root)
    @property
    def calendar(self) -> Path: return station_paths.MARKET_EVENT_CALENDAR_BY_EVENT.path()   # in the repo, not under the data root
    @property
    def cursor(self) -> Path: return station_paths.SPX_OPTIONS_TRADES_RESUME_POINT_STATE.path(self.state_root)
    @property
    def lock(self) -> Path: return station_paths.SPX_OPTIONS_TAPE_COLLECTOR_LOCK.path(self.state_root)

    def baseline(self, name: str) -> Path:
        return station_paths.DEPTH_BASELINES_STATE_BY_ENGINE[name].path(self.state_root)

    def raw(self, day: str, stream: str) -> Path:
        return station_paths.SPX_SAME_DAY_OPTIONS_AND_SPY_DEPTH_RAW_STREAM.path(self.state_root, day=day, stream=stream)

    def agg(self, day: str) -> Path:
        return station_paths.SPX_ORDER_FLOW_READINGS_BY_MINUTE.path(self.state_root, day=day)

    def card(self, day: str) -> Path:
        return station_paths.SPX_ORDER_FLOW_GRADES_BY_DAY.path(self.state_root, day=day)

    # -- latest.json handshake -----------------------------------------------
    def write_latest(self, obj: dict, now: datetime) -> None:
        atomic_write_json(self.latest, {**obj, "written_at": now.isoformat()})

    def read_latest(self, now: datetime, max_age_s: int) -> Optional[dict]:
        """The host-tick read: None unless fresh — a stale fold must read as
        'sensor absent', never as a live opinion."""
        obj = read_json(self.latest)
        if not obj:
            return None
        try:
            age = (now - datetime.fromisoformat(obj["written_at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return None
        if age < 0 or age > max_age_s:
            return None
        obj["age_s"] = round(age, 1)
        return obj

    # -- append-only journals + replay ----------------------------------------
    def append(self, path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def rows(self, path: Path) -> Iterator[dict]:
        """Tolerant reader: skips torn/corrupt lines (a crash mid-append must
        not poison the replay)."""
        gz = path.with_suffix(path.suffix + ".gz")
        if not path.exists() and gz.exists():
            import gzip
            try:
                for ln in gzip.open(gz, "rt"):
                    if ln.strip():
                        try:
                            yield json.loads(ln)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                return
            return
        if not path.exists():
            return
        for ln in open(path):
            if ln.strip():
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    continue

    def agg_rows(self, day: str) -> Iterator[dict]:
        """One day's order-flow readings (the agg journal), a torn line skipped: plain, or gzipped once the day is
        older than station_store_files.KEEP_PLAIN_DAYS (the nightly compression), read through the station's one
        day-file reader."""
        for ln in station_store_files.read_day_file_lines(self.agg(day)):
            if ln.strip():
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    continue

    def gap_marker(self, day: str, stream: str, now: datetime, reason: str) -> None:
        """NEVER interpolate across a disconnect — mark it instead, so the
        baseline fold can see (and skip) the hole."""
        self.append(self.raw(day, stream), {"gap": True, "ts": now.isoformat(),
                                            "reason": reason})

    # -- single instance -------------------------------------------------------
    def acquire_lock(self):
        """flock handle (caller keeps it alive) or None if another collector
        already owns the day. Opened without O_TRUNC so a refused attempt
        never wipes the owner's recorded PID."""
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock, os.O_CREAT | os.O_WRONLY)
        fh = os.fdopen(fd, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return None
        fh.truncate(0)
        fh.write(str(os.getpid()))
        fh.flush()
        return fh

    # -- rotation ---------------------------------------------------------------
    def rotate(self, now: datetime) -> dict:
        """Daemon-start housekeeping: gzip every finished day's raw journals in
        place. Nothing is deleted: the raw day folders are the station's one
        copy of the same-day options tape (cleanup phase 5, 2026-10-12; the
        recorder's gzip copies were folded in), kept until a retention phase
        decides otherwise. Today's folder is left plain: it is the live
        crash-replay source."""
        report = {"gzipped": 0}
        raw_root = self.raw_root
        if not raw_root.exists():
            return report
        today = now.date().isoformat()
        for day_dir in sorted(raw_root.iterdir()):
            if not day_dir.is_dir() or day_dir.name >= today:
                continue
            import gzip                                  # compress finished days
            for f in day_dir.glob("*.jsonl"):
                try:
                    with open(f, "rb") as src, gzip.open(f"{f}.gz", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    f.unlink()
                    report["gzipped"] += 1
                except OSError:
                    continue
        return report


# ---------------------------------------------------------------------------
# Cursor persistence (tape resume without gaps or double counting)
# ---------------------------------------------------------------------------


def load_cursor(sd: StateDir, day: str) -> dict:
    cur = read_json(sd.cursor) or {}
    return cur if cur.get("day") == day else {"day": day, "contracts": {}}


def save_cursor(sd: StateDir, cursor: dict) -> None:
    atomic_write_json(sd.cursor, cursor)
