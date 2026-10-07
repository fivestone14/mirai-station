"""Daily closes: each session's open, high, low, close and volume for a few symbols, years back, from Schwab's daily
history, one call a symbol.

    python3 -m spx_jev.daily_closes                    # every symbol's history to the last closed session (save_day's 16:20 step)
    python3 -m spx_jev.daily_closes --through 2026-10-02

Schwab keeps daily candles for years, so one call serves a symbol's whole history and the first run is the
backfill. One file per symbol under ``state/spx_jev/daily_closes/``:

    {symbol}.jsonl     one line per session, oldest first, as Schwab served it (the Treasury yield $TNX at ten
                       times the yield, as the market feed keeps it; state_builder.context_value puts it in percent):
                       {"day", "ts": Schwab's own stamp on the candle, "open", "high", "low", "close", "volume", "fetched_at"}

Each run rewrites a symbol's file whole from the history fetched, atomically, so a rerun gives the same file and a
run that fails mid-way leaves the old one; a symbol Schwab answers with no bars, or whose call fails, is logged and
keeps its file. Point in time: ``load`` returns the sessions before a day only, since a day's close is not known
during it. Only a session that has closed is kept (``through``): a daily candle of a session still running is partial.
Market days only, as the minute backfill (market_context.backfill): on 2026-10-06 Schwab served a $VIX candle on ten
market holidays of the last two years (2025-07-04, 2026-09-07), days with no session to read it on; they are
left out and counted in the log.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import schwab
from .feed_log import failure, log
from .sessions import is_trading_day
from .state_builder import DEFAULT_STATE_DIR, load_jsonl

ET = ZoneInfo("America/New_York")
JOB = "spx-jev-daily-closes"
DAILY_CLOSES_SUBDIR = Path("spx_jev") / "daily_closes"
SYMBOLS = ("$SPX", "TLT", "$TNX", "$VIX", "$VIX9D")
HISTORY_YEARS = 10             # how far back each call asks; Schwab serves what it keeps
FIELDS = ("open", "high", "low", "close", "volume")


def closes_path(state_dir: Path, symbol: str) -> Path:
    return Path(state_dir) / DAILY_CLOSES_SUBDIR / f"{symbol}.jsonl"


def session_rows(bars: list[dict], through: date, fetched_at: datetime) -> tuple[list[dict], list[str]]:
    """``(rows, days left out)``: one row per market day from Schwab's daily bars, oldest first, each day read off its
    bar's UTC date, days after ``through`` and days that are not market days left out (the latter named); two
    bars of one day keep the later served. UTC, not New York: Schwab stamps a daily candle in the small hours UTC
    (today's at 04:00, the history's at 05:00 or 06:00), which in New York is the same day in summer but can be
    23:00 the day before once the clocks go back, so the New York date would file a winter day under its eve."""
    by_day: dict[str, dict] = {}
    off_days = []
    for b in bars:
        day = datetime.fromisoformat(b["ts"]).astimezone(timezone.utc).date()
        if day > through:
            continue
        if not is_trading_day(day):
            off_days.append(day.isoformat())
            continue
        by_day[day.isoformat()] = {"day": day.isoformat(), "ts": b["ts"], **{k: b.get(k) for k in FIELDS},
                                   "fetched_at": fetched_at.isoformat(timespec="seconds")}
    return [by_day[d] for d in sorted(by_day)], sorted(set(off_days))


def write_symbol(state_dir: Path, symbol: str, rows: list[dict]) -> Path:
    """The symbol's file, whole, in one atomic replace: on disk old or new, never half."""
    path = closes_path(state_dir, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)
    return path


def load(state_dir: Path, symbol: str, before: str) -> list[dict]:
    """The symbol's sessions before ``before`` (YYYY-MM-DD), oldest first: what a read on that day could know."""
    return sorted((r for r in load_jsonl(closes_path(state_dir, symbol)) if isinstance(r.get("day"), str) and r["day"] < before),
                  key=lambda r: r["day"])


def save(state_dir: Path, through: date, now: datetime | None = None, symbols: tuple[str, ...] = SYMBOLS) -> dict:
    """Every symbol's history to ``through`` fetched and written whole. A symbol whose call fails or comes back
    empty is named under ``failed`` with the reason, its file left as it was, and costs no other symbol.
    ``{"saved": {symbol: sessions on file}, "failed": {symbol: why}}``."""
    now = now or datetime.now(ET)
    start = now - timedelta(days=365 * HISTORY_YEARS)
    saved: dict[str, int] = {}
    failed: dict[str, str] = {}
    for i, symbol in enumerate(symbols):
        if i:
            time.sleep(schwab.CALL_SPACING_S)
        try:
            rows, off_days = session_rows(schwab.daily_bars(symbol, start, now), through, now)
            if off_days:
                log(JOB, f"{symbol}: {len(off_days)} candles on days that are not market days left out ({off_days[0]} to {off_days[-1]})")
            if not rows:
                failed[symbol] = "no daily bars"
                log(JOB, f"{symbol} returned no daily bars through {through.isoformat()}; its file is kept as it was", err=True)
                continue
            write_symbol(state_dir, symbol, rows)
        except Exception as e:  # a failed call, a bar not in the shape served, or a write: this symbol's alone
            failed[symbol] = f"{type(e).__name__}"
            log(JOB, f"{symbol} daily closes failed: {failure(e)}", err=True)
            continue
        saved[symbol] = len(rows)
    return {"saved": saved, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    from .save_day import last_closed       # save_day runs this step, so its rule for the last closed session is read late

    ap = argparse.ArgumentParser(description="Save each symbol's daily closes, years back, under state/spx_jev/daily_closes/.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--through", help="YYYY-MM-DD, the last session to keep; default the last one that has closed")
    args = ap.parse_args(argv)
    now = datetime.now(ET)
    through = date.fromisoformat(args.through) if args.through else last_closed(now)
    r = save(Path(args.state_dir), through, now)
    for symbol, n in r["saved"].items():
        log(JOB, f"{symbol}: {n} sessions through {through.isoformat()} -> {closes_path(Path(args.state_dir), symbol)}")
    return 1 if r["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
