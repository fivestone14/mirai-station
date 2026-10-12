"""
io_state.py — the FILING SYSTEM of the box: every byte that touches disk goes
through here. Atomic writes (tmp+rename, never a torn read), the latest.json
handshake the host tick reads, append-only raw journals with cursors for
crash-replay, and the rotation that enforces the disk budget.

Every file is named in station_paths.py; StateDir is handed the station's data root and asks it:
    spx/scanner/spx_order_flow_readings_latest.json      # the ONLY file the host's tick reads
    spx/scanner/spx_dealer_gamma_levels_latest.json      # written by the host, read by the daemon
    market/market_event_calendar_by_event.json           # scheduled-event dates (seeded if missing)
    spx/scanner/spx_options_trades_resume_point_state.json, operations/locks/spx_options_tape_collector.lock
    spx/scanner/{spx_options,spy}_depth_baselines_state.json
    market/spx_same_day_options_raw_day_folders/{date}/{stream}.jsonl   # per-day append-only journals (the disk budget's folder)
    spx/scanner/spx_order_flow_readings_by_minute/{date}.jsonl          # per-fold aggregate rows (kept forever)
    spx/scanner/spx_order_flow_grades_by_day/{date}.json                # nightly head-to-head grades
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

_REPO_ROOT = str(Path(__file__).resolve().parents[3])             # station_paths.py names every store
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402

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
        """The raw day folders' parent: what the rotation prunes and the disk budget measures."""
        return station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.folder(self.state_root)

    # -- named files ---------------------------------------------------------
    @property
    def latest(self) -> Path: return station_paths.SPX_ORDER_FLOW_READINGS_LATEST.path(self.state_root)
    @property
    def gex_context(self) -> Path: return station_paths.SPX_DEALER_GAMMA_LEVELS_LATEST.path(self.state_root)
    @property
    def calendar(self) -> Path: return station_paths.MARKET_EVENT_CALENDAR_BY_EVENT.path(self.state_root)
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
    def disk_usage_mb(self) -> float:
        """The raw day folders' size: the one thing the cap can shrink (the aggregates, baselines and cards, which it
        never deletes, no longer count toward it since they left the collector's folder)."""
        total = 0
        if not self.raw_root.exists():
            return 0.0
        for p in self.raw_root.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
        return total / 1e6

    def rotate(self, now: datetime, raw_retention_days: int, disk_cap_mb: int) -> dict:
        """Daemon-start housekeeping: gzip yesterday's raw journals, drop raw
        days past retention, and enforce the hard disk cap oldest-first.
        Aggregates, baselines, and cards are never deleted by the cap."""
        report = {"gzipped": 0, "dropped_days": 0, "cap_dropped": 0}
        raw_root = self.raw_root
        if not raw_root.exists():
            return report
        today = now.date().isoformat()
        cutoff = (now.date() - timedelta(days=raw_retention_days)).isoformat()
        for day_dir in sorted(raw_root.iterdir()):
            if not day_dir.is_dir():
                continue
            if day_dir.name < cutoff:
                shutil.rmtree(day_dir, ignore_errors=True)
                report["dropped_days"] += 1
                continue
            if day_dir.name < today:                     # compress finished days
                import gzip
                for f in day_dir.glob("*.jsonl"):
                    try:
                        with open(f, "rb") as src, gzip.open(f"{f}.gz", "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        f.unlink()
                        report["gzipped"] += 1
                    except OSError:
                        continue
        # hard cap: drop oldest raw days until under budget — but NEVER today's
        # (it is the live crash-replay source)
        while self.disk_usage_mb() > disk_cap_mb:
            days = sorted(d for d in raw_root.iterdir()
                          if d.is_dir() and d.name != today)
            if not days:
                break
            shutil.rmtree(days[0], ignore_errors=True)
            report["cap_dropped"] += 1
        return report


# ---------------------------------------------------------------------------
# Cursor persistence (tape resume without gaps or double counting)
# ---------------------------------------------------------------------------


def load_cursor(sd: StateDir, day: str) -> dict:
    cur = read_json(sd.cursor) or {}
    return cur if cur.get("day") == day else {"day": day, "contracts": {}}


def save_cursor(sd: StateDir, cursor: dict) -> None:
    atomic_write_json(sd.cursor, cursor)
