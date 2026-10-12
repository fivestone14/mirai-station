"""
store.py — crash-safe persistence for the SIEGE store. Same atomic_io
pattern as the host's left-eye state files: every writer swaps a fully-formed
temp file into place with a single os.replace, every reader tolerates a
missing/short/torn file. One writer per file by convention (the engine, one
scan at a time).

Layout under one injected root (the station's data root, data/, in the mirai wiring; each file is named in
station_paths.py; the sensor's own sit under spx/scanner/, SPY's volume under market/):
    spx/scanner/spx_dealer_walls_latest.json                   # "latest": the viewstation contract — field names are FROZEN; carries the feed verdict per scan
    market/spy_volume_by_minute/{date}.json                    # "baseline": SPY's volume per minute, one file a day; today's rewritten on every healthy
                                                               #   scan, the newest 60 read back as the normalcy memory (station_store_files reads and writes them)
    spx/scanner/spx_dealer_wall_touches_today_state.json       # "session": the intraday episode state machine
    spx/scanner/spx_dealer_wall_touches_by_event/{date}.jsonl  # "cards": one row per resolved episode
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])             # station_paths.py names every store
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402
import station_store_files  # noqa: E402


def write_json_atomic(path, obj):
    """Write obj as JSON by temp-file + atomic rename. Reader never sees a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)          # single atomic syscall: name -> new inode
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def read_json_or(path, default):
    """Return parsed JSON, or `default` on any missing/short/torn/invalid file."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def append_jsonl(path, obj):
    """Append one JSON object as a line. A single O_APPEND write of one line is
    atomic enough on a local filesystem under a single appender."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, default=str)
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


class SiegeStore:
    """One object, all paths — each named in station_paths.py; ``root`` is the station's data root."""

    def __init__(self, root):
        self.root = Path(root)

    @property
    def latest(self) -> Path: return station_paths.SPX_DEALER_WALLS_LATEST.path(self.root)
    @property
    def session(self) -> Path: return station_paths.SPX_DEALER_WALL_TOUCHES_TODAY_STATE.path(self.root)

    def spy_volume_file(self, day: str) -> Path:
        return station_paths.SPY_VOLUME_BY_MINUTE.path(self.root, day=day)

    def cards(self, day: str) -> Path:
        return station_paths.SPX_DEALER_WALL_TOUCHES_BY_EVENT.path(self.root, day=day)

    # -- typed accessors (readers tolerate absence, writers are atomic) ------
    def read_session(self) -> dict:
        return read_json_or(self.session, {})

    def write_session(self, obj: dict) -> None:
        write_json_atomic(self.session, obj)

    def read_spy_volume_days(self, newest: int) -> dict[str, dict[str, int]]:
        """The newest ``newest`` days' SPY volume files as the baseline keeps them, {day: {"<minute>": shares}}."""
        days = station_store_files.read_spy_volume_days(self.root, newest=newest)
        return {d: {str(m): int(v) for m, v in mins.items()} for d, mins in days.items()}

    def write_spy_volume_day(self, day: str, minute_volumes: dict) -> None:
        """Replace ``day``'s SPY volume file whole (atomic: temp file, then one rename)."""
        station_store_files.write_spy_volume_by_minute(self.root, day, minute_volumes)

    def write_latest(self, obj: dict) -> None:
        write_json_atomic(self.latest, obj)

    def append_card(self, day: str, row: dict) -> None:
        append_jsonl(self.cards(day), row)

    def read_cards(self, day: str) -> list[dict]:
        rows = []
        try:
            for ln in self.cards(day).read_text().splitlines():
                if not ln.strip():
                    continue
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue           # torn line, not a dead day
        except OSError:
            pass
        return rows
