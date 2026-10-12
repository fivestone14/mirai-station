"""The recorder: copies station data before it is overwritten or deleted, and backs the irreplaceable data up off-disk.

Every task is idempotent (it copies only what is missing, and only for sessions that have finished, so a
run by hand mid-session never freezes half a day) and reads the station's stores read-only; the copies
land under ``market/`` (station_paths.py names each). Run by the ``spx-claude-forecast-recorder`` launchd job
at 08:40 and 16:25 ET (after the 08:17 ET dated-book fetch and after the close), plus Friday 17:30 ET after
the Friday book refresh; it fires on weekends too, as a second chance at Friday's book before Monday's
fetch overwrites it. The two Schwab tasks (spx_5m, leveraged_funds) stay out of market hours, where the login
is left to the live feeds; spx_5m also skips weekends, which bring no new session.

    python -m spx_claude_forecast.recorder --state-dir <state> [--task all|dated_book|siege|vix1d|spx_5m|lob_raw|leveraged_funds|backup]

Tasks:
  dated_book   the dated options book (market/spx_monthly_quarterly_open_interest_latest.json) is overwritten on every
               fetch (08:17 ET daily, 17:10 ET Fridays): keep every distinct book under
               market/spx_monthly_quarterly_open_interest_by_day/{as_of}.json, named by the book's own as_of
  siege        the wall sensor's baseline (spx/scanner/spy_volume_baselines_state.json) keeps 60 days of SPY per-minute
               volume, overwrites today's entry on every scan and deletes the oldest day after that: keep one file per
               finished day under market/spy_volume_by_minute/{day}.json
  vix1d        $VIX1D is quoted every ~70 s in market/watchlist_quotes_by_minute/{day}.jsonl but never kept as a close:
               market/vix_one_day_closes_by_day.jsonl gets one line per finished session, the day's last quote
  spx_5m       Schwab keeps SPX 5-minute bars far further back than 1-minute bars but loses a session a day:
               market/spx_price_bars_by_5_minutes/{day}.jsonl, one file per session, fetched outside market hours
  lob_raw      the collector's raw day folders (market/spx_same_day_options_raw_day_folders/{day}/) are deleted after
               30 days: gzip copies under market/spx_same_day_options_raw_day_folders_recorder_copy/{day}/
  leveraged_funds  the leveraged index funds' assets and leverage as Schwab's instrument data states them now, one line
               per run in market/leveraged_fund_assets_by_run.jsonl, so a read can estimate the day's closing rebalance
               from sizes it could have known (the close-signals block)
  backup       mirror the forecast folder and the recorder's copies, under their paths relative to the data root, to the
               off-disk backup folder (the logs and locks under operations/ are not backed up)
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
from .paths import station_paths
from .control import ET, PYTEST_ENV, log, log_job_run, now_et, now_utc_iso, prune_run_log, single_instance_lock, switched_off_reason
from .paths import RUN_LOG_KEPT_DAYS, ForecastPaths, default_state_dir, ensure_folders

SESSION_CLOSE_SETTLED_AT = time(16, 5)    # a day's last quote and its stores are final once the close has printed
MARKET_HOURS = (time(9, 20), time(16, 10))   # the Schwab task stays out of this window on weekdays
SPX_5M_LOOKBACK_DAYS = 400               # ask for more than Schwab keeps; it answers with what it has
LOB_RAW_MANIFEST_NAME = "_complete.json"  # the day's manifest, written last, so a copy that stopped halfway is redone
# The iCloud root the station backs up into; the backup mirrors the data root's layout under it (spx/claude_forecast/,
# market/). The files it put there before phase 7, under spx_claude_forecast/, stay as they are until the owner decides.
DEFAULT_BACKUP_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "mirai-station-backups"
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
                  "source": station_paths.SPY_VOLUME_BASELINES_STATE.relative, "copied_at": now_utc_iso()}
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
                              "quote_ts": line.get("ts"), "source": str(station_paths.WATCHLIST_QUOTES_BY_MINUTE.relative_folder), "recorded_at": now_utc_iso()}
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
    _refuse_schwab_under_pytest()
    schwab = station_stores.import_spx_jev("schwab")
    return schwab.five_minute_bars("$SPX", start, end)


def _refuse_schwab_under_pytest() -> None:
    """A test run never reaches Schwab: every task that fetches takes a stand-in, and this is the backstop."""
    if os.environ.get(PYTEST_ENV):
        raise RuntimeError("refused: a Schwab fetch under pytest; pass a stand-in fetch")


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

def backup_sources(paths: ForecastPaths) -> list[Path]:
    """Every file the backup mirrors: the whole forecast folder and the recorder's copies under market/ (the
    irreplaceable data), each under its path relative to the data root."""
    files: list[Path] = []
    for place in (paths.root, *paths.recorder_copies):
        if place.is_file():
            files.append(place)
        elif place.is_dir():
            files.extend(p for p in sorted(place.rglob("*")) if p.is_file())
    return [p for p in files if not p.name.endswith(".tmp")]


def backup_forecast_folder(paths: ForecastPaths, backup_dir: Path) -> dict:
    """Mirror the forecast folder and the recorder's copies into ``backup_dir`` under their paths relative to the
    data root: a file is copied when it is missing there or its size or mtime differs (the copy keeps the source's
    mtime). Nothing is ever deleted from the backup."""
    copied = checked = 0
    for source in backup_sources(paths):
        relative = source.relative_to(paths.state_dir)
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

# The leveraged index funds whose closing rebalance the close-signals block estimates, each with the symbol whose move
# drives it (the fund's own index, as the context job quotes it). Chosen 2026-10-10 by size: these carry nearly all
# the dollars (TQQQ alone over a third); the VIX products and the small inverse funds are left out as too small.
LEVERAGED_FUNDS = {
    "UPRO": "$SPX", "SPXL": "$SPX", "SSO": "$SPX", "SPXU": "$SPX", "SPXS": "$SPX", "SDS": "$SPX", "SH": "$SPX",
    "TQQQ": "QQQ", "QLD": "QQQ", "SQQQ": "QQQ", "SOXL": "SMH", "SOXS": "SMH", "TNA": "IWM", "NVDL": "NVDA", "TSLL": "TSLA",
}
LEVERAGE_FACTOR_PER_1X = 100.0    # Schwab states 3x as 300 and -3x as -300
LEVERAGE_RANGE = (1.0, 5.0)       # a stated leverage outside this, as 1x, is not one: the scale or the field has changed


def fetch_leveraged_fund_sizes(symbols: tuple[str, ...]) -> dict[str, dict]:
    """``{symbol: {"assets_usd", "leverage"}}`` from Schwab's instrument data (its fundamental projection: market cap
    is the fund's assets, fundLeverageFactor its leverage x100) through the station's own client. A fund Schwab
    does not answer for, answers without both numbers, or states a leverage outside LEVERAGE_RANGE is left out."""
    _refuse_schwab_under_pytest()
    from schwab.client import Client as SchwabClient        # the client library, only here
    client = station_stores.import_spx_jev("schwab")._fetcher()._client()
    response = client.get_instruments(list(symbols), SchwabClient.Instrument.Projection.FUNDAMENTAL)
    response.raise_for_status()
    data = response.json()
    items = data.get("instruments", []) if isinstance(data, dict) else data
    sizes: dict[str, dict] = {}
    for item in items or []:
        fundamental = item.get("fundamental") or {}
        symbol, cap, factor = item.get("symbol"), fundamental.get("marketCap"), fundamental.get("fundLeverageFactor")
        if symbol not in symbols or not isinstance(cap, (int, float)) or not isinstance(factor, (int, float)) or cap <= 0:
            continue
        leverage = factor / LEVERAGE_FACTOR_PER_1X
        if LEVERAGE_RANGE[0] <= abs(leverage) <= LEVERAGE_RANGE[1]:
            sizes[symbol] = {"assets_usd": float(cap), "leverage": leverage}
    return sizes


def record_leveraged_fund_sizes(paths: ForecastPaths, now: datetime | None = None, fetch=None) -> dict:
    """One line per run outside market hours: every fund's assets and leverage as Schwab states them now, with the
    index each follows, so a read estimates the day's closing rebalance from sizes it could have known
    (frozen_inputs takes the newest line at or before its cut). Funds Schwab did not answer for are named on the
    line, never guessed."""
    now = now or now_et()
    if is_market_hours(now):
        return {"written": 0, "why": "market hours: the Schwab login is left to the live feeds"}
    sizes = (fetch or fetch_leveraged_fund_sizes)(tuple(LEVERAGED_FUNDS))
    funds = {symbol: {**sizes[symbol], "index": index} for symbol, index in LEVERAGED_FUNDS.items() if symbol in sizes}
    if not funds:
        return {"written": 0, "why": "Schwab answered for no fund"}
    line = {"recorded_at": now.isoformat(timespec="seconds"), "funds": funds,
            "missing": [symbol for symbol in LEVERAGED_FUNDS if symbol not in sizes]}
    jsonl_store.append_json_line(paths.leveraged_fund_sizes_file, line)
    return {"written": 1, "funds": len(funds), "missing": len(line["missing"])}


TASKS = ("dated_book", "siege", "vix1d", "spx_5m", "lob_raw", "leveraged_funds", "backup")


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
        "leveraged_funds": lambda: record_leveraged_fund_sizes(paths),
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
