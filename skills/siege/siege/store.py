"""
store.py — crash-safe persistence for the SIEGE store. Same atomic_io
pattern as the host's left-eye state files: every writer swaps a fully-formed
temp file into place with a single os.replace, every reader tolerates a
missing/short/torn file. One writer per file by convention (the engine, one
scan at a time).

Layout under one injected root (state/siege in the mirai wiring):
    latest.json         # the viewstation contract — field names are FROZEN
    health.json         # feed verdict per scan
    baseline.json       # minute-volume normalcy memory, folded day by day
    session.json        # the intraday episode state machine
    cards/{date}.jsonl  # one row per resolved episode
"""
from __future__ import annotations

import json
import os
from pathlib import Path


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
    """One object, all paths — nothing else in the package knows the layout."""

    def __init__(self, root):
        self.root = Path(root)

    @property
    def latest(self) -> Path: return self.root / "latest.json"
    @property
    def health(self) -> Path: return self.root / "health.json"
    @property
    def baseline(self) -> Path: return self.root / "baseline.json"
    @property
    def session(self) -> Path: return self.root / "session.json"

    def cards(self, day: str) -> Path:
        return self.root / "cards" / f"{day}.jsonl"

    # -- typed accessors (readers tolerate absence, writers are atomic) ------
    def read_session(self) -> dict:
        return read_json_or(self.session, {})

    def write_session(self, obj: dict) -> None:
        write_json_atomic(self.session, obj)

    def read_baseline(self) -> dict:
        return read_json_or(self.baseline, {})

    def write_baseline(self, obj: dict) -> None:
        write_json_atomic(self.baseline, obj)

    def write_latest(self, obj: dict) -> None:
        write_json_atomic(self.latest, obj)

    def write_health(self, obj: dict) -> None:
        write_json_atomic(self.health, obj)

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
