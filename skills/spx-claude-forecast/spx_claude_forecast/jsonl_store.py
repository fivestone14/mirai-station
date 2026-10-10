"""The only way this package writes a file: append-only JSON lines, atomic replacement, write-once blobs.

Why its own module instead of the helpers in spx_jev or mirai-left-eye: two processes (Claude's two
answers) append to the same ``reads/{day}.jsonl`` at once, so a line is written with ``O_APPEND``
under an exclusive ``flock`` in a single ``os.write``, and a file that does not end in a newline gets
one first so a torn line from a crash is fenced off rather than glued to the next record. Replaced
files are fsynced and so is their folder. Every writer refuses a path outside the forecast root or
the backup root, which is how "never writes into Pool 2's files" is enforced in code.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

_WRITABLE_ROOTS: list[Path] = []     # set by allow_writes_under(); empty means "nothing is writable yet"


class WriteOutsideOwnFolder(RuntimeError):
    """Raised when a writer is pointed anywhere but the forecast folder or the backup folder."""


def allow_writes_under(*roots: Path | str) -> None:
    """Declare the folders this process may write to. Called once by whoever owns the state root."""
    _WRITABLE_ROOTS[:] = [Path(r).expanduser().resolve() for r in roots]


def assert_path_is_ours(path: Path | str) -> Path:
    """The resolved path, or WriteOutsideOwnFolder if no declared root contains it."""
    resolved = Path(path).expanduser()
    resolved = resolved.resolve() if resolved.exists() else (resolved.parent.resolve() / resolved.name)
    for root in _WRITABLE_ROOTS:
        if root == resolved or root in resolved.parents:
            return resolved
    raise WriteOutsideOwnFolder(f"refusing to write outside the forecast folder: {resolved}")


def canonical_json(data: Any) -> str:
    """One byte-stable rendering: sorted keys, no spaces, UTF-8 kept as is."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_json_sha256(data: Any, chars: int = 16) -> str:
    """``sha256:`` plus the first ``chars`` hex digits of the canonical rendering."""
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()[:chars]


def text_sha256(text: str, chars: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:chars]


def file_sha256(path: Path | str, chars: int = 16) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:chars]


def append_json_line(path: Path | str, record: dict, *, fsync: bool = True) -> None:
    """Append one record as one line, atomically with respect to other appenders of the same file."""
    target = assert_path_is_ours(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        if size and _last_byte(target, size) != b"\n":
            os.write(fd, b"\n")                                  # fence off a torn line from a crash
        os.write(fd, line.encode("utf-8"))
        if fsync:
            os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _last_byte(path: Path, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(size - 1)
        return f.read(1)


def read_json_lines(path: Path | str) -> list[dict]:
    """Every well-formed record in the file, oldest first; a torn or malformed line is skipped."""
    return list(iter_json_lines(path))


def iter_json_lines(path: Path | str) -> Iterator[dict]:
    p = Path(path)
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def write_json_atomically(path: Path | str, data: Any, *, indent: int | None = 1) -> None:
    """Replace a whole JSON file in one step: temp file, fsync, rename, fsync the folder."""
    write_bytes_atomically(path, json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True, default=str).encode("utf-8"))


def write_bytes_atomically(path: Path | str, data: bytes) -> None:
    target = assert_path_is_ours(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")       # a name of this process's own
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        _fsync_folder(target.parent)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_bytes_once(path: Path | str, data: bytes) -> bool:
    """Write a content-addressed file if it is missing or short; True when something was written."""
    target = assert_path_is_ours(path)
    if target.exists() and target.stat().st_size == len(data):
        return False
    write_bytes_atomically(target, data)
    return True


def _fsync_folder(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
