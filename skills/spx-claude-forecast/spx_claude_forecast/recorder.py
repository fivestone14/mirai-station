"""The recorder: copies station data before it is overwritten or deleted, and backs this folder up off-disk.

Every task is idempotent (it copies only what is missing) and reads the station's stores read-only; the
copies land under ``state/spx_claude_forecast/recorder/``. Run by the ``spx-claude-forecast-recorder``
launchd job at 08:40 and 16:25 ET (after the 08:17 ET dated-book fetch and after the close), plus
Friday 17:30 ET after the Friday book refresh. The Schwab task runs only outside market hours.

    python -m spx_claude_forecast.recorder --state-dir <state> [--task all|dated_book|siege|vix1d|spx_5m|lob_raw|backup]

Tasks:
  dated_book   state/dated_gex/book.json is overwritten on every fetch (08:17 ET daily, 17:10 ET Fridays): keep
               every distinct book under recorder/dated_book/{as_of}.json, named by the book's own as_of
  siege        state/siege/baseline.json keeps 60 days of SPY per-minute volume and deletes the oldest day after
               that: keep one file per day under recorder/siege_spy_minutes/{day}.json
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
import os
import shutil
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from . import jsonl_store, station_stores
from .control import ET, is_switched_off, log, log_job_run, now_et, now_utc_iso, today_et
from .paths import ForecastPaths, default_state_dir, ensure_folders

SESSION_CLOSE_SETTLED = time(16, 5)      # a day's last $VIX1D quote is final once the close has printed
MARKET_HOURS = (time(9, 20), time(16, 10))   # the Schwab task stays out of this window on weekdays
SPX_5M_LOOKBACK_DAYS = 400               # ask for more than Schwab keeps; it answers with what it has
LOB_RAW_COMPLETE_MARK = "_complete.json"  # written last, so a copy that stopped halfway is redone
DEFAULT_BACKUP_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "mirai-station-backups" / "spx_claude_forecast"
BACKUP_SKIPPED_FOLDERS = ("locks", "run_logs")


# --- dated options book -------------------------------------------------------------------------------

def book_stamp(as_of: str) -> str:
    """A file-name-safe form of the book's ISO ``as_of`` that still sorts by time: ``2026-10-09T171004-0400``."""
    return datetime.fromisoformat(as_of).strftime("%Y-%m-%dT%H%M%S%z")


def copy_dated_book(paths: ForecastPaths, state_dir: Path) -> dict:
    source = station_stores.dated_book_file(state_dir)
    if not source.exists():
        return {"copied": False, "why": "no book on disk"}
    raw = source.read_bytes()
    try:
        as_of = json.loads(raw).get("as_of")
    except (json.JSONDecodeError, AttributeError):
        return {"copied": False, "why": "book is not valid JSON"}
    if not isinstance(as_of, str) or "T" not in as_of:
        return {"copied": False, "why": "book has no as_of"}
    target = paths.dated_book_copy(book_stamp(as_of))
    return {"copied": jsonl_store.write_bytes_once(target, raw), "as_of": as_of, "file": target.name}


# --- siege SPY minute volumes --------------------------------------------------------------------------

def copy_siege_spy_minutes(paths: ForecastPaths, state_dir: Path) -> dict:
    source = station_stores.siege_baseline_file(state_dir)
    if not source.exists():
        return {"copied": 0, "why": "no baseline on disk"}
    try:
        days = json.loads(source.read_text(encoding="utf-8")).get("days") or {}
    except (json.JSONDecodeError, AttributeError):
        return {"copied": 0, "why": "baseline is not valid JSON"}
    copied = []
    for day, minute_volumes in sorted(days.items()):
        target = paths.siege_spy_minutes_file(day)
        if target.exists():
            continue
        record = {"day": day, "minutes_present": len(minute_volumes), "minute_volumes": minute_volumes,
                  "source": "state/siege/baseline.json", "copied_at": now_utc_iso()}
        jsonl_store.write_json_atomically(target, record, indent=None)
        copied.append(day)
    return {"copied": len(copied), "days": copied, "days_on_disk": len(days)}


# --- $VIX1D close ----------------------------------------------------------------------------------------

def finished_session_days(days: list[str], now: datetime) -> list[str]:
    """The days whose session has ended: every day before today, and today once the close has printed."""
    today = now.date().isoformat()
    out = [d for d in days if d < today]
    if today in days and now.timetz().replace(tzinfo=None) >= SESSION_CLOSE_SETTLED:
        out.append(today)
    return out


def record_vix1d_close(paths: ForecastPaths, state_dir: Path, now: datetime | None = None) -> dict:
    now = now or now_et()
    already = {line.get("day") for line in jsonl_store.read_json_lines(paths.vix1d_close_file)}
    written = []
    for day in finished_session_days(station_stores.context_days(state_dir), now):
        if day in already:
            continue
        last_quote = None
        for line in jsonl_store.iter_json_lines(station_stores.context_file(state_dir, day)):
            quote = (line.get("quotes") or {}).get("$VIX1D")
            if isinstance(quote, dict) and isinstance(quote.get("last"), (int, float)):
                last_quote = {"day": day, "last": quote["last"], "session_open_value": quote.get("close"),
                              "quote_ts": line.get("ts"), "source": "state/spx_jev/context", "recorded_at": now_utc_iso()}
        if last_quote:
            jsonl_store.append_json_line(paths.vix1d_close_file, last_quote)
            written.append(day)
    return {"written": len(written), "days": written}


# --- SPX 5-minute bars from Schwab ---------------------------------------------------------------------

def is_market_hours(now: datetime) -> bool:
    return now.weekday() < 5 and MARKET_HOURS[0] <= now.timetz().replace(tzinfo=None) < MARKET_HOURS[1]


def fetch_spx_five_minute_bars(start: datetime, end: datetime) -> list[dict]:
    """The station's Schwab wrapper, imported only here so nothing else needs the login."""
    schwab = station_stores.import_spx_jev("schwab")
    return schwab.five_minute_bars("$SPX", start, end)


def save_spx_5m_bars(paths: ForecastPaths, now: datetime | None = None, fetch=fetch_spx_five_minute_bars) -> dict:
    now = now or now_et()
    if is_market_hours(now):
        return {"written": 0, "why": "market hours: the Schwab login is left to the live feeds"}
    bars = fetch(now - timedelta(days=SPX_5M_LOOKBACK_DAYS), now)
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
    now = now or now_et()
    source_root = station_stores.lob_flow_raw_dir(state_dir)
    if not source_root.exists():
        return {"copied": 0, "why": "no raw tape on disk"}
    today = now.date().isoformat()
    copied = []
    for day_dir in sorted(p for p in source_root.iterdir() if p.is_dir() and p.name < today):
        target_dir = paths.lob_raw_copy_dir(day_dir.name)
        if (target_dir / LOB_RAW_COMPLETE_MARK).exists():
            continue
        manifest = {"day": day_dir.name, "files": {}, "copied_at": now_utc_iso()}
        for source in sorted(p for p in day_dir.iterdir() if p.is_file()):
            name = source.name if source.suffix == ".gz" else source.name + ".gz"
            target = target_dir / name
            jsonl_store.assert_path_is_ours(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            try:
                if source.suffix == ".gz":
                    shutil.copyfile(source, tmp)
                else:
                    with open(source, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    tmp.unlink()
            manifest["files"][name] = {"bytes": target.stat().st_size, "source_bytes": source.stat().st_size}
        jsonl_store.write_json_atomically(target_dir / LOB_RAW_COMPLETE_MARK, manifest)   # written last, on purpose
        copied.append(day_dir.name)
    return {"copied": len(copied), "days": copied}


# --- off-disk backup --------------------------------------------------------------------------------------

def backup_forecast_folder(paths: ForecastPaths, backup_dir: Path) -> dict:
    """Mirror the forecast folder into ``backup_dir``: a file is copied when it is missing there or its size
    or mtime differs. Nothing is ever deleted from the backup."""
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
        jsonl_store.assert_path_is_ours(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
        copied += 1
    return {"copied": copied, "checked": checked, "backup_dir": str(backup_dir)}


# --- the job --------------------------------------------------------------------------------------------------

TASKS = ("dated_book", "siege", "vix1d", "spx_5m", "lob_raw", "backup")


def run(state_dir: Path, tasks: tuple[str, ...] = TASKS, backup_dir: Path | None = None) -> dict:
    """Run the named tasks in order; a task that fails is logged and the rest still run."""
    paths = ensure_folders(state_dir)
    backup_dir = backup_dir or DEFAULT_BACKUP_DIR
    jsonl_store.allow_writes_under(paths.root, backup_dir)
    results: dict[str, dict] = {}
    for task in tasks:
        started = now_utc_iso()
        try:
            if task == "dated_book":
                results[task] = copy_dated_book(paths, state_dir)
            elif task == "siege":
                results[task] = copy_siege_spy_minutes(paths, state_dir)
            elif task == "vix1d":
                results[task] = record_vix1d_close(paths, state_dir)
            elif task == "spx_5m":
                results[task] = save_spx_5m_bars(paths)
            elif task == "lob_raw":
                results[task] = copy_lob_raw(paths, state_dir)
            elif task == "backup":
                results[task] = backup_forecast_folder(paths, backup_dir)
            else:
                results[task] = {"error": "unknown task"}
            log_job_run(paths, f"recorder:{task}", started, True, **_counts(results[task]))
        except Exception as e:
            results[task] = {"error": f"{type(e).__name__}: {e}"}
            log_job_run(paths, f"recorder:{task}", started, False, error=results[task]["error"])
        log(f"{task}: {json.dumps(results[task], default=str)[:300]}")
    return results


def _counts(result: dict) -> dict:
    return {k: v for k, v in result.items() if isinstance(v, (int, float, str)) and k != "error"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy at-risk station data and back up the forecast folder.")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--task", default="all", choices=("all",) + TASKS)
    parser.add_argument("--backup-dir", default=None)
    args = parser.parse_args(argv)
    off = is_switched_off()
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
