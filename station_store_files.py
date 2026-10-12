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

Cleanup phase 9 (2026-10-12) keeps every day file forever and compresses the old ones: a finished day of a big daily
store (``COMPRESSED_DAY_FILE_STORES``) older than ``KEEP_PLAIN_DAYS`` days is gzipped in place,
``{day}.jsonl`` -> ``{day}.jsonl.gz``, losslessly, by ``compress_finished_days`` once a night (the spx-jev-store job,
16:40 ET). Every reader of those stores reads a day file through the helpers below (``read_day_file_text``,
``read_day_file_lines``, ``open_day_file``, ``day_file_exists``, ``day_files``, ``day_file_days``), which take the
plain path the store names and read whichever of the plain file and its ``.gz`` is there; a line appended to an
already-compressed day (a by-hand backfill) lands in a new plain file, is read after the .gz's lines, and is folded
into the .gz the next night.

Pure standard library, no side effects on import; imported the way ``station_paths`` is (the repo root on sys.path).
``python station_store_files.py --compress-finished-days [--state-dir DIR]`` runs the compression by hand.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

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


# --- day files, plain or gzipped -------------------------------------------------------------------------------------
# A finished day of these stores older than KEEP_PLAIN_DAYS days is gzipped in place by compress_finished_days, once a
# night: ``{day}.jsonl`` -> ``{day}.jsonl.gz``, the same bytes, the plain file's mtime kept. Each is a store whose live
# writers only append to today's file (or, by hand, add to a past day), read by the jobs and screens over many days.
# Not here: the futures bars (spx-jev-overnight restamps every saved night whole when a roll changes), the small
# stores (under ~0.1 MB a day), the Parquet tables, the raw tape (the collector gzips it itself), the state, latest
# and card files.
KEEP_PLAIN_DAYS = 14
COMPRESSED_DAY_FILE_SUFFIX = ".gz"
EASTERN_TIME = ZoneInfo("America/New_York")
COMPRESSED_DAY_FILE_STORES = (
    (station_paths.SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN, {}),
    (station_paths.SPX_ORDER_FLOW_READINGS_BY_MINUTE, {}),
    (station_paths.WATCHLIST_QUOTES_BY_MINUTE, {}),
    (station_paths.WATCHLIST_PRICE_BARS_BY_MINUTE, {}),
    (station_paths.JEV_READS_AND_GRADES_BY_EVENT, {}),
    *((store, {"lane": lane}) for store in (station_paths.JEV_ANSWERS_BY_READ, station_paths.JEV_AND_POOL_FORECASTS_BY_READ)
      for lane in station_paths.JEV_LANE_FOLDERS),
    (station_paths.CLAUDE_FORECAST_INPUTS_BY_READ, {}),
    (station_paths.CLAUDE_FORECAST_ANSWERS_BY_READ, {}),
    (station_paths.CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ, {}),
    (station_paths.SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN, {}),
    (station_paths.SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN, {}),
)


def compressed_day_file_path(path: Path | str) -> Path:
    """The ``.gz`` beside a day file: ``2026-09-01.jsonl`` -> ``2026-09-01.jsonl.gz``."""
    path = Path(path)
    return path.with_name(path.name + COMPRESSED_DAY_FILE_SUFFIX)


def day_file_exists(path: Path | str) -> bool:
    """Whether the day file (the plain path the store names) is on disk, plain or gzipped."""
    return Path(path).is_file() or compressed_day_file_path(path).is_file()


def read_day_file_bytes(path: Path | str) -> bytes:
    """The day file's bytes, plain or gzipped: the .gz's decompressed bytes, then any plain file's (a line added to a
    compressed day by hand, until the next night folds it in). FileNotFoundError when neither is there."""
    path = Path(path)
    parts = []
    try:
        parts.append(gzip.decompress(compressed_day_file_path(path).read_bytes()))
    except FileNotFoundError:
        pass
    try:
        parts.append(path.read_bytes())
    except FileNotFoundError:
        if not parts:
            raise
    if len(parts) == 2 and parts[0] and not parts[0].endswith(b"\n"):
        parts.insert(1, b"\n")
    return b"".join(parts)


def read_day_file_text(path: Path | str, encoding: str = "utf-8", errors: str = "strict") -> str:
    """``Path.read_text`` for a day file, plain or gzipped (``read_day_file_bytes``)."""
    return read_day_file_bytes(path).decode(encoding, errors)


def read_day_file_lines(path: Path | str, encoding: str = "utf-8", errors: str = "strict") -> list[str]:
    """The day file's lines without their endings, plain or gzipped; ``[]`` when neither is there."""
    try:
        return read_day_file_text(path, encoding, errors).splitlines()
    except FileNotFoundError:
        return []


def open_day_file(path: Path | str, mode: str = "r", encoding: str | None = "utf-8", errors: str | None = None) -> IO:
    """``open(path, mode)`` for reading a day file, plain or gzipped ("r" or "rb"): the plain file or the .gz is
    streamed; a day that has both is read whole. FileNotFoundError when neither is there."""
    if mode not in ("r", "rt", "rb"):
        raise ValueError(f"open_day_file reads only, not {mode!r}")
    path, binary = Path(path), "b" in mode
    gz = compressed_day_file_path(path)
    if not gz.is_file():
        return open(path, "rb") if binary else open(path, "r", encoding=encoding, errors=errors)
    if not path.is_file():
        return gzip.open(gz, "rb") if binary else gzip.open(gz, "rt", encoding=encoding, errors=errors)
    data = read_day_file_bytes(path)
    return io.BytesIO(data) if binary else io.StringIO(data.decode(encoding or "utf-8", errors or "strict"))


def day_file_signature(path: Path | str) -> tuple[int, int] | None:
    """``(size, mtime_ns)`` of the day's content as written, for a cache keyed on the file; None when neither the
    plain file nor the .gz is there. A gzipped day gives its uncompressed size (the gzip trailer's) and the mtime that
    compression kept, so the signature is the same before and after compression; a day with both gives the sizes
    summed and the newer mtime."""
    size, mtime_ns, found = 0, 0, False
    try:
        stat = Path(path).stat()
        size, mtime_ns, found = stat.st_size, stat.st_mtime_ns, True
    except OSError:
        pass
    gz = compressed_day_file_path(path)
    try:
        with open(gz, "rb") as f:
            stat = os.fstat(f.fileno())
            f.seek(-4, os.SEEK_END)
            size += int.from_bytes(f.read(4), "little")       # ISIZE: the uncompressed length (mod 2**32)
            mtime_ns, found = max(mtime_ns, stat.st_mtime_ns), True
    except OSError:
        pass
    return (size, mtime_ns) if found else None


def day_file_mtime(path: Path | str) -> float | None:
    """The day file's last-modified time in seconds (``day_file_signature``'s), None when it is not there."""
    signature = day_file_signature(path)
    return None if signature is None else signature[1] / 1e9


def day_files(folder: Path | str, suffix: str = ".jsonl") -> list[Path]:
    """Every day file in ``folder``, oldest day first, as the plain path the store names (``{day}{suffix}``) whether
    the day is on disk plain, gzipped or both; read each with the helpers above."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    days = set()
    for name in os.listdir(folder):
        if name.endswith(COMPRESSED_DAY_FILE_SUFFIX):
            name = name[: -len(COMPRESSED_DAY_FILE_SUFFIX)]
        if name.endswith(suffix) and station_paths.DAY_PATTERN.match(name[: -len(suffix)]):
            days.add(name[: -len(suffix)])
    return [folder / f"{day}{suffix}" for day in sorted(days)]


def day_file_days(folder: Path | str, suffix: str = ".jsonl") -> list[str]:
    """Every day with a day file in ``folder``, plain or gzipped, oldest first."""
    return [p.name[: -len(suffix)] for p in day_files(folder, suffix)]


def replace_day_file_text(path: Path | str, text: str, encoding: str = "utf-8") -> Path:
    """Rewrite a day file whole and atomically (a temp file, then one rename), in the form the day is kept in: a
    compressed day is written as its .gz (and a plain file beside it, already read into ``text`` by the caller,
    goes), any other day as the plain file. Returns the file written."""
    path = Path(path)
    gz = compressed_day_file_path(path)
    data = text.encode(encoding)
    target = gz if gz.is_file() else path
    if target is gz:
        data = gzip.compress(data, mtime=0)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomically(target, data)
    if target is gz and path.exists():
        path.unlink()
    return target


def _write_bytes_atomically(target: Path, data: bytes, mtime_ns: int | None = None) -> None:
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mtime_ns is not None:
            os.utime(tmp, ns=(mtime_ns, mtime_ns))
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def compress_finished_days(data_root: Path | str | None = None, keep_plain_days: int = KEEP_PLAIN_DAYS,
                           today: date | None = None) -> dict:
    """Gzip in place every plain day file of ``COMPRESSED_DAY_FILE_STORES`` whose day is more than ``keep_plain_days``
    days before ``today`` (Eastern time's date by default); today's and the last ``keep_plain_days`` days' files stay
    plain. Idempotent: a day already gzipped is not touched again, so a second run does nothing.

    Each file: its bytes are read and compressed (folded after the existing .gz's when a line was added to a
    compressed day), the result is checked to decompress to exactly those bytes, written beside it as a temp file,
    checked again from disk, given the plain file's mtime and renamed onto ``{day}.jsonl.gz``; only then is the plain
    file removed, and only when it did not change meanwhile (else the .gz is put back as it was and the day waits for
    the next night). Nothing is deleted that is not in the .gz. Returns the night's counts."""
    root = station_paths.state_root(data_root)
    today = today or datetime.now(EASTERN_TIME).date()
    cutoff = (today - timedelta(days=keep_plain_days)).isoformat()
    summary = {"compressed": 0, "folded_into_existing": 0, "left_plain_changed": 0, "plain_bytes": 0, "compressed_bytes": 0,
               "stores": 0, "failed": [], "kept_plain_from": cutoff}
    for store, keys in COMPRESSED_DAY_FILE_STORES:
        folder = store.folder(root, **keys)
        if not folder.is_dir():
            continue
        summary["stores"] += 1
        for path in sorted(folder.glob("????-??-??.jsonl")):
            day = path.name[:10]
            if not station_paths.DAY_PATTERN.match(day) or day >= cutoff or not path.is_file():
                continue
            try:
                outcome = _compress_day_file(path)
            except (OSError, ValueError, EOFError) as exc:
                summary["failed"].append(f"{path.relative_to(root)}: {type(exc).__name__}: {exc}")
                continue
            if outcome is None:
                summary["left_plain_changed"] += 1
                continue
            plain_bytes, compressed_bytes, folded = outcome
            summary["compressed"] += 1
            summary["folded_into_existing"] += int(folded)
            summary["plain_bytes"] += plain_bytes
            summary["compressed_bytes"] += compressed_bytes
    return summary


def _compress_day_file(path: Path) -> tuple[int, int, bool] | None:
    """One plain day file into its .gz (compress_finished_days); None when the plain file changed while it was being
    compressed, with the .gz as it was before. ``(plain bytes, .gz bytes, whether an existing .gz was folded into)``."""
    gz = compressed_day_file_path(path)
    before = path.stat()
    plain = path.read_bytes()
    if len(plain) != before.st_size:
        return None
    previous = gz.read_bytes() if gz.is_file() else None
    content = plain
    if previous is not None:
        earlier = gzip.decompress(previous)
        content = earlier + (b"\n" if earlier and not earlier.endswith(b"\n") else b"") + plain
    compressed = gzip.compress(content, compresslevel=9, mtime=0)
    if gzip.decompress(compressed) != content:
        raise ValueError("the compressed bytes do not decompress to the day file")
    _write_bytes_atomically(gz, compressed, mtime_ns=before.st_mtime_ns)
    if gzip.decompress(gz.read_bytes()) != content:
        raise ValueError("the .gz on disk does not decompress to the day file")
    after = path.stat()
    if (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino):
        if previous is None:
            gz.unlink()
        else:
            _write_bytes_atomically(gz, previous)
        return None
    path.unlink()
    return len(plain), len(compressed), previous is not None


def compress_finished_days_line(summary: dict) -> str:
    """The night's one summary line."""
    line = (f"compress-finished-days :: {summary['compressed']} day files gzipped in {summary['stores']} stores, "
            f"{summary['plain_bytes'] / 1e6:.1f} MB -> {summary['compressed_bytes'] / 1e6:.1f} MB; "
            f"kept plain from {summary['kept_plain_from']}")
    if summary["folded_into_existing"]:
        line += f"; {summary['folded_into_existing']} folded into an existing .gz"
    if summary["left_plain_changed"]:
        line += f"; {summary['left_plain_changed']} left plain (written to while compressing)"
    if summary["failed"]:
        line += f"; {len(summary['failed'])} failed: " + " | ".join(summary["failed"][:5])
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The shared store files; --compress-finished-days gzips old day files.")
    parser.add_argument("--compress-finished-days", action="store_true", help="gzip every finished day file older than "
                        f"{KEEP_PLAIN_DAYS} days in place (compress_finished_days)")
    parser.add_argument("--state-dir", help="the data root (default: station_paths.state_root())")
    parser.add_argument("--keep-plain-days", type=int, default=KEEP_PLAIN_DAYS)
    args = parser.parse_args(argv)
    if not args.compress_finished_days:
        parser.error("nothing asked: pass --compress-finished-days")
    summary = compress_finished_days(args.state_dir, args.keep_plain_days)
    print(compress_finished_days_line(summary))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
