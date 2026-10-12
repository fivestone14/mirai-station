"""The store files more than one pipeline reads or writes, read and written one way.

``station_paths.py`` names every store and opens no file; this module opens the few that two or more pipelines
share, so each is read the same way everywhere and written by one function. Cleanup phase 5 (2026-10-12) made
each of them the ONE copy of its data:

* The SPX minute bars, ``market/spx_price_bars_by_minute/{day}.jsonl`` (``SPX_PRICE_BARS_BY_MINUTE``): one bar a
  line, ``{"ts": "<ISO, America/New_York, the bar's start>", "open", "high", "low", "close", "volume"}``, written by
  the bars feed a minute at a time (``spx_jev.bars.append_day``) and topped up after the close (``spx_jev.save_day``).
  Read here by the scanner's grader and speedometer, the JEV scene (``state_builder``), the Claude forecast's seed
  and the spec scripts; the desktop's replay reads the same file through the viewstation. The grader's own saved
  sessions (``spx_price_bars_by_minute_scanner_copy/{day}-SPX.json``, a JSON list of the same bars) were folded
  into these files by ``scheduled_jobs/maintenance/merge_duplicate_stores_2026_10.py``.
* SPY's volume per minute, ``market/spy_volume_by_minute/{day}.json`` (``SPY_VOLUME_BY_MINUTE``): one file a day,
  ``{"day", "minutes_present", "minute_volumes": {"<minute of day>": shares}, "written_at"}``, a minute keyed by
  when its bar started in minutes after midnight ET (570 = 09:30). The wall sensor writes today's file on every
  healthy scan (replaced whole) and reads the newest 60 files back as its baseline; the JEV volume labels, the code
  questions and the Claude forecast read the days they need. The sensor's one-file memory of every day
  (``spx/scanner/spy_volume_baselines_state.json``) was folded into these files by the same command; the files the
  Claude forecast's recorder copied out of it before that carry ``copied_at`` and ``source`` instead of ``written_at``.
* The open-interest book's history stamp. The dated-book job keeps every book it pulls under
  ``market/spx_monthly_quarterly_open_interest_by_day/{stamp}.json`` beside the latest file it overwrites; the stamp
  is a file-name-safe form of the book's own ``as_of`` that still sorts by time, and the Claude forecast takes the
  newest stamp at or before a read's cut.

Pure standard library, no side effects on import; imported the way ``station_paths`` is (the repo root on sys.path).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import station_paths

BAR_FIELDS = ("open", "high", "low", "close")


# --- the SPX minute bars ---------------------------------------------------------------------------------------------

def read_spx_price_bars(root: Path | str | None, day: str) -> list[dict]:
    """The day's bars, oldest first: every line that is a bar (a ``ts`` string and numeric open, high, low and
    close); a torn or foreign line is skipped, and a day with no file is ``[]``."""
    return read_spx_price_bars_file(station_paths.SPX_PRICE_BARS_BY_MINUTE.path(root, day=day))


def read_spx_price_bars_file(path: Path | str) -> list[dict]:
    """``read_spx_price_bars`` for a day file the caller already holds; an unreadable file is ``[]``."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    bars = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            bar = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_bar(bar):
            bars.append(bar)
    bars.sort(key=lambda b: b["ts"])
    return bars


def is_bar(bar: object) -> bool:
    """Whether ``bar`` is one of the store's bars: a dict with a ``ts`` string and a number for each of open, high,
    low and close (volume may be missing or null, the index has none)."""
    return (isinstance(bar, dict) and isinstance(bar.get("ts"), str)
            and all(isinstance(bar.get(k), (int, float)) and not isinstance(bar.get(k), bool) for k in BAR_FIELDS))


def spx_price_bar_days(root: Path | str | None) -> list[str]:
    """Every day with a bars file, oldest first."""
    folder = station_paths.SPX_PRICE_BARS_BY_MINUTE.folder(root)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("????-??-??.jsonl") if station_paths.DAY_PATTERN.match(p.stem))


# --- SPY's volume per minute -----------------------------------------------------------------------------------------

def read_spy_volume_by_minute(root: Path | str | None, day: str) -> dict[int, float]:
    """``{minute of day: shares}`` for ``day``, ``{}`` without a file or minutes. Cached by the file's mtime, since
    today's file is replaced on every scan and a past day's never changes."""
    path = station_paths.SPY_VOLUME_BY_MINUTE.path(root, day=day)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return {}
    return dict(_read_spy_volume_file(str(path), mtime_ns))


@lru_cache(maxsize=128)
def _read_spy_volume_file(path: str, mtime_ns: int) -> dict[int, float]:
    try:
        raw = (json.loads(Path(path).read_text(encoding="utf-8")) or {}).get("minute_volumes") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {int(m): float(v) for m, v in raw.items() if str(m).isdigit() and isinstance(v, (int, float)) and not isinstance(v, bool)}


def spy_volume_days(root: Path | str | None) -> list[str]:
    """Every day with a SPY volume file, oldest first."""
    folder = station_paths.SPY_VOLUME_BY_MINUTE.folder(root)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("????-??-??.json") if station_paths.DAY_PATTERN.match(p.stem))


def read_spy_volume_days(root: Path | str | None, newest: int | None = None, through: str | None = None) -> dict[str, dict[int, float]]:
    """``{day: {minute of day: shares}}`` for the newest ``newest`` days on file (every day when None), on or before
    ``through`` when given: the wall sensor's baseline is the newest 60, today's included once it has written one."""
    days = [d for d in spy_volume_days(root) if through is None or d <= through]
    if newest is not None:
        days = days[-newest:] if newest > 0 else []
    return {d: read_spy_volume_by_minute(root, d) for d in days}


def write_spy_volume_by_minute(root: Path | str | None, day: str, minute_volumes: dict, written_at: datetime | str | None = None) -> Path:
    """Replace ``day``'s file whole and atomically (a temp file beside it, then one rename, so a reader never sees a
    half-written file): minutes as strings, shares as whole numbers, as the sensor's memory kept them."""
    path = station_paths.SPY_VOLUME_BY_MINUTE.path(root, day=day)
    minutes = {str(int(m)): int(v) for m, v in minute_volumes.items()}
    if written_at is None:
        written_at = datetime.now().astimezone()
    stamp = written_at.isoformat(timespec="seconds") if isinstance(written_at, datetime) else written_at
    record = {"day": day, "minutes_present": len(minutes), "minute_volumes": minutes, "written_at": stamp}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


# --- the open-interest book's history -------------------------------------------------------------------------------

def open_interest_book_stamp(as_of: str) -> str:
    """A file-name-safe form of the book's ISO ``as_of`` that still sorts by time: ``2026-10-09T171004-0400``."""
    return datetime.fromisoformat(as_of).strftime("%Y-%m-%dT%H%M%S%z")
