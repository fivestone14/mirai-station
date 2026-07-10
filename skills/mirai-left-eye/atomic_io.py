"""atomic_io.py — crash-safe JSON persistence for the left-eye state files.

The left-eye tick is a fresh launchd process every scan, so shared state must
survive a mid-write crash/kill. Every writer here swaps a fully-formed temp file
into place with a single os.replace (atomic on POSIX), and every reader tolerates
a missing/short/torn file by returning a caller-supplied default instead of
raising. Rule enforced by convention (not code): exactly ONE writer per file.
"""
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
