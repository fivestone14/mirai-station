"""The recorder: copies station data before it is overwritten or deleted, and backs this folder up off-disk.

Every task is idempotent (it copies only what is missing, and only for sessions that have finished, so a
run by hand mid-session never freezes half a day) and reads the station's stores read-only; the copies
land under ``state/spx_claude_forecast/recorder/``. Run by the ``spx-claude-forecast-recorder`` launchd job
at 08:40 and 16:25 ET (after the 08:17 ET dated-book fetch and after the close), plus Friday 17:30 ET after
the Friday book refresh; it fires on weekends too, as a second chance at Friday's book before Monday's
fetch overwrites it. The Schwab task runs only outside market hours and never on a weekend.

    python -m spx_claude_forecast.recorder --state-dir <state> [--task all|dated_book|siege|vix1d|spx_5m|lob_raw|backup]

Tasks:
  dated_book   state/dated_gex/book.json is overwritten on every fetch (08:17 ET daily, 17:10 ET Fridays): keep
               every distinct book under recorder/dated_book/{as_of}.json, named by the book's own as_of
  siege        state/siege/baseline.json keeps 60 days of SPY per-minute volume, overwrites today's entry on every
               scan and deletes the oldest day after that: keep one file per finished day under
               recorder/siege_spy_minutes/{day}.json
  vix1d        $VIX1D is quoted every ~70 s in state/spx_jev/context/{day}.jsonl but never kept as a close:
               recorder/vix1d_close.jsonl gets one line per finished session, the day's last quote
  spx_5m       Schwab keeps SPX 5-minute bars far further back than 1-minute bars but loses a session a day:
               recorder/spx_5m/{day}.jsonl, one file per session, fetched outside market hours
  lob_raw      state/lob_flow/raw/{day}/ is deleted after 30 days: gzip copies under recorder/lob_raw/{day}/
  backup       mirror this whole folder (except locks and run logs) to the off-disk backup folder
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from . import jsonl_store, station_stores
from .control import ET, log, log_job_run, now_et, now_utc_iso, prune_run_log, single_instance_lock, switched_off_reason
from .paths import RUN_LOG_KEPT_DAYS, ForecastPaths, default_state_dir, ensure_folders

SESSION_CLOSE_SETTLED_AT = time(16, 5)    # a day's last quote and its stores are final once the close has printed
MARKET_HOURS = (time(9, 20), time(16, 10))   # the Schwab task stays out of this window on weekdays
SPX_5M_LOOKBACK_DAYS = 400               # ask for more than Schwab keeps; it answers with what it has
LOB_RAW_MANIFEST_NAME = "_complete.json"  # the day's manifest, written last, so a copy that stopped halfway is redone
DEFAULT_BACKUP_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "mirai-station-backups" / "spx_claude_forecast"
BACKUP_SKIPPED_FOLDERS = ("locks", "run_logs")
LOCK_NAME = "recorder"


# --- dated options book -------------------------------------------------------------------------------

def book_stamp(as_of: str) -> str:
    """A file-name-safe form of the book's ISO ``as_of`` that still sorts by time: ``2026-10-09T171004-0400``."""
    return datetime.fromisoformat(as_of).strftime("%Y-%m-%dT%H%M%S%z")


def copy_dated_book(paths: ForecastPaths, state_dir: Path) -> dict:
    source = station_stores.dated_book_file(state_dir)
    if not source.exists():
        return {"copied": 0, "why": "no book on disk"}
    raw = source.read_bytes()
    try:
        as_of = json.loads(raw).get("as_of")
    except (json.JSONDecodeError, AttributeError):
        return {"copied": 0, "why": "book is not valid JSON"}
    try:
        stamp = book_stamp(as_of)
    except (TypeError, ValueError):
        return {"copied": 0, "why": f"book has no usable as_of: {as_of!r}"}
    target = paths.dated_book_copy_file(stamp)
    return {"copied": int(jsonl_store.write_bytes_once(target, raw)), "as_of": as_of, "file": target.name}


# --- siege SPY minute volumes --------------------------------------------------------------------------

def copy_siege_spy_minutes(paths: ForecastPaths, state_dir: Path, now: datetime | None = None) -> dict:
    """One file per finished day; today's entry is overwritten by every siege scan, so it waits for the close."""
    source = station_stores.siege_baseline_file(state_dir)
    if not source.exists():
        return {"copied": 0, "why": "no baseline on disk"}
    try:
        days = json.loads(source.read_text(encoding="utf-8")).get("days") or {}
    except (json.JSONDecodeError, AttributeError):
        return {"copied": 0, "why": "baseline is not valid JSON"}
    copied = []
    for day in finished_session_days(sorted(days), now or now_et()):
        target = paths.siege_spy_minutes_file(day)
        if target.exists():
            continue
        minute_volumes = days[day]
        record = {"day": day, "minutes_present": len(minute_volumes), "minute_volumes": minute_volumes,
                  "source": "state/siege/baseline.json", "copied_at": now_utc_iso()}
        jsonl_store.write_json_atomically(target, record, indent=None)
        copied.append(day)
    return {"copied": len(copied), "days": copied, "days_on_disk": len(days)}


# --- $VIX1D close ----------------------------------------------------------------------------------------

def finished_session_days(days: list[str], now: datetime) -> list[str]:
    """The days whose session has ended: every day before today, and today once the close has printed."""
    now = now.astimezone(ET)
    today = now.date().isoformat()
    out = [d for d in days if d < today]
    if today in days and now.time() >= SESSION_CLOSE_SETTLED_AT:
        out.append(today)
    return out


def record_vix1d_close(paths: ForecastPaths, state_dir: Path, now: datetime | None = None) -> dict:
    """One line per finished session: the day's last $VIX1D quote. ``previous_close`` is Schwab's closePrice on
    that quote, the prior session's close, kept as a cross-check against the line before it."""
    already = {line.get("day") for line in jsonl_store.read_json_lines(paths.vix1d_close_file)}
    written = []
    for day in finished_session_days(station_stores.context_days(state_dir), now or now_et()):
        if day in already:
            continue
        last_quote = None
        for line in jsonl_store.iter_json_lines(station_stores.context_file(state_dir, day)):
            quote = (line.get("quotes") or {}).get("$VIX1D")
            if isinstance(quote, dict) and isinstance(quote.get("last"), (int, float)):
                last_quote = {"day": day, "last": quote["last"], "previous_close": quote.get("close"),
                              "quote_ts": line.get("ts"), "source": "state/spx_jev/context", "recorded_at": now_utc_iso()}
        if last_quote:
            jsonl_store.append_json_line(paths.vix1d_close_file, last_quote)
            written.append(day)
    return {"written": len(written), "days": written}


# --- SPX 5-minute bars from Schwab ---------------------------------------------------------------------

def is_market_hours(now: datetime) -> bool:
    now = now.astimezone(ET)
    return now.weekday() < 5 and MARKET_HOURS[0] <= now.time() < MARKET_HOURS[1]


def fetch_spx_five_minute_bars(start: datetime, end: datetime) -> list[dict]:
    """The station's Schwab wrapper, imported only here so nothing else needs the login."""
    schwab = station_stores.import_spx_jev("schwab")
    return schwab.five_minute_bars("$SPX", start, end)


def save_spx_5m_bars(paths: ForecastPaths, now: datetime | None = None, fetch=None) -> dict:
    """One file per finished session, from one Schwab call for everything it still serves. ``fetch`` defaults to
    the live wrapper at call time, so a test or a monkeypatch can stand in for Schwab."""
    now = (now or now_et()).astimezone(ET)
    if now.weekday() >= 5:
        return {"written": 0, "why": "weekend: no new session to fetch"}
    if is_market_hours(now):
        return {"written": 0, "why": "market hours: the Schwab login is left to the live feeds"}
    bars = (fetch or fetch_spx_five_minute_bars)(now - timedelta(days=SPX_5M_LOOKBACK_DAYS), now)
    by_day: dict[str, list[dict]] = {}
    for bar in bars:
        by_day.setdefault(str(bar.get("ts", ""))[:10], []).append(bar)
    finished = set(finished_session_days(sorted(by_day), now))
    written = []
    for day, day_bars in sorted(by_day.items()):
        if day not in finished or paths.spx_5m_bars_file(day).exists():
            continue
        body = "".join(json.dumps(bar, ensure_ascii=False) + "\n" for bar in day_bars)
        jsonl_store.write_bytes_atomically(paths.spx_5m_bars_file(day), body.encode("utf-8"))
        written.append(day)
    return {"written": len(written), "days": written, "earliest_served": min(by_day) if by_day else None,
            "bars_served": len(bars)}


# --- raw options tape --------------------------------------------------------------------------------------

def copy_lob_raw(paths: ForecastPaths, state_dir: Path, now: datetime | None = None) -> dict:
    """Gzip copies of every finished day's raw tape, with a manifest written last. A day the collector is still
    compressing (a plain file beside its own .gz) is left for the next run, so a half-written .gz is never kept."""
    source_root = station_stores.lob_flow_raw_dir(state_dir)
    if not source_root.exists():
        return {"copied": 0, "why": "no raw tape on disk"}
    today = (now or now_et()).astimezone(ET).date().isoformat()
    copied, mid_compress = [], []
    for day_dir in sorted(p for p in source_root.iterdir() if p.is_dir() and p.name < today):
        target_dir = paths.lob_raw_copy_dir(day_dir.name)
        if (target_dir / LOB_RAW_MANIFEST_NAME).exists():
            continue
        sources = sorted(p for p in day_dir.iterdir() if p.is_file())
        if any(p.suffix != ".gz" and p.with_name(p.name + ".gz").exists() for p in sources):
            mid_compress.append(day_dir.name)
            continue
        manifest = {"day": day_dir.name, "files": {}, "copied_at": now_utc_iso()}
        for source in sources:
            name = source.name if source.suffix == ".gz" else source.name + ".gz"
            target = jsonl_store.replace_file_atomically(target_dir / name, lambda tmp, source=source: _gzip_copy(source, tmp))
            manifest["files"][name] = {"bytes": target.stat().st_size, "source_bytes": source.stat().st_size}
        jsonl_store.write_json_atomically(target_dir / LOB_RAW_MANIFEST_NAME, manifest)   # written last, on purpose
        copied.append(day_dir.name)
    return {"copied": len(copied), "days": copied, "left_mid_compress": mid_compress}


def _gzip_copy(source: Path, target: Path) -> None:
    """``source`` as gzip at ``target``; a source that is already gzip is copied byte for byte."""
    if source.suffix == ".gz":
        shutil.copyfile(source, target)
        return
    with open(source, "rb") as src, gzip.open(target, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


# --- off-disk backup --------------------------------------------------------------------------------------

def backup_forecast_folder(paths: ForecastPaths, backup_dir: Path) -> dict:
    """Mirror the forecast folder into ``backup_dir``: a file is copied when it is missing there or its size
    or mtime differs (the copy keeps the source's mtime). Nothing is ever deleted from the backup."""
    copied = checked = 0
    for source in sorted(paths.root.rglob("*")):
        if not source.is_file() or source.name.endswith(".tmp"):
            continue
        relative = source.relative_to(paths.root)
        if relative.parts and relative.parts[0] in BACKUP_SKIPPED_FOLDERS:
            continue
        checked += 1
        target = backup_dir / relative
        stat = source.stat()
        if target.exists():
            tstat = target.stat()
            if tstat.st_size == stat.st_size and int(tstat.st_mtime) == int(stat.st_mtime):
                continue
        jsonl_store.replace_file_atomically(target, lambda tmp, source=source: shutil.copy2(source, tmp))
        copied += 1
    return {"copied": copied, "checked": checked, "backup_dir": str(backup_dir)}


# --- the job --------------------------------------------------------------------------------------------------

TASKS = ("dated_book", "siege", "vix1d", "spx_5m", "lob_raw", "backup")


def run(state_dir: Path, tasks: tuple[str, ...] = TASKS, backup_dir: Path | None = None) -> dict:
    """Run the named tasks in order under the single-instance lock; a task that fails is logged and the rest
    still run. Returns ``{task: result}``, empty when another recorder run held the lock."""
    backup_dir = backup_dir or DEFAULT_BACKUP_DIR
    paths = ensure_folders(state_dir, backup_dir)
    runners = {
        "dated_book": lambda: copy_dated_book(paths, state_dir),
        "siege": lambda: copy_siege_spy_minutes(paths, state_dir),
        "vix1d": lambda: record_vix1d_close(paths, state_dir),
        "spx_5m": lambda: save_spx_5m_bars(paths),
        "lob_raw": lambda: copy_lob_raw(paths, state_dir),
        "backup": lambda: backup_forecast_folder(paths, backup_dir),
    }
    results: dict[str, dict] = {}
    with single_instance_lock(paths, LOCK_NAME) as held:
        if not held:
            log("another recorder run holds the lock; nothing done")
            return results
        for task in tasks:
            started = now_utc_iso()
            try:
                if task not in runners:
                    raise ValueError(f"unknown task {task!r}; one of {TASKS}")
                results[task] = runners[task]()
                log_job_run(paths, f"recorder:{task}", started, True, **_run_log_fields(results[task]))
            except Exception as e:
                results[task] = {"error": f"{type(e).__name__}: {e}"}
                log_job_run(paths, f"recorder:{task}", started, False, error=results[task]["error"])
            log(f"{task}: {json.dumps(results[task], default=str)[:300]}")
        dropped = prune_run_log(paths)
        if dropped:
            log(f"run log: dropped {dropped} lines older than {RUN_LOG_KEPT_DAYS} days")
    return results


def _run_log_fields(result: dict) -> dict:
    """The scalar fields of a task's result (counts, names, reasons), which is what its run-log line carries."""
    return {k: v for k, v in result.items() if isinstance(v, (int, float, str)) and k != "error"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy at-risk station data and back up the forecast folder.")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--task", default="all", choices=("all",) + TASKS)
    parser.add_argument("--backup-dir", default=None)
    args = parser.parse_args(argv)
    off = switched_off_reason()
    if off:
        log(f"switched off ({off}); nothing recorded")
        return 0
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else default_state_dir()
    tasks = TASKS if args.task == "all" else (args.task,)
    results = run(state_dir, tasks, Path(args.backup_dir).expanduser() if args.backup_dir else None)
    print(json.dumps(results, indent=1, default=str))
    return 1 if any("error" in r for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
