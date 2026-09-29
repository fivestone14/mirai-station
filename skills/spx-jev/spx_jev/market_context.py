"""The market around SPX, a snapshot a minute: breadth, volatility, futures, rates, sectors, the big names and
the funds for bonds, credit, oil and gold.

    python3 -m spx_jev.market_context                        # one snapshot now (the launchd job's run)
    python3 -m spx_jev.market_context --backfill 2026-08-10  # every past session's minute bars since then

Schwab keeps about 34 sessions of 1-minute history, so anything wanted for longer has to be saved as
it happens. Two files per day under ``state/spx_jev/context/``:

    {day}.jsonl        one line per live snapshot, written by the job each minute in market hours:
                       {"ts": when it was taken, "quotes": {symbol: {"last", "close", "volume", "quote_time"}},
                        "bars": {symbol: the newest finished 1-minute bar}}
                       Quotes for every symbol Schwab quotes (batched, QUOTE_BATCH a call); a finished bar
                       for the breadth symbols, whose quotes are empty shells, and for $VIX1D nothing but
                       its quote, since it has no history.
    bars/{day}.jsonl   one line per minute of a past session, written by --backfill:
                       {"ts": the minute's end, "bars": {symbol: bar}}; a day already on disk is skipped.

The labeller reads both through ``state_builder.load_market_context``, point in time: a bar counts once
its minute has finished, a quote once its snapshot was taken. A symbol with nothing on disk simply
has no values, and every label that needs it is omitted with the reason.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import schwab
from .feed_log import failure, log
from .sessions import SESSION_CLOSE, SESSION_OPEN
from .state_builder import CONTEXT_SUBDIR, DEFAULT_STATE_DIR

ET = ZoneInfo("America/New_York")
JOB = "spx-jev-context"
SYMBOLS = {
    "breadth": ("$TICK", "$ADD", "$TRIN", "$VOLD", "$UVOL", "$DVOL", "$VOLSPD"),
    "volatility": ("$VIX", "$VIX9D", "$VVIX", "$VIX3M", "$VIX1D"),
    "futures": ("/ES", "/MBT"),                    # /MBT, micro bitcoin, for the session bitcoin labels (labels/bitcoin.py)
    "rates": ("$TNX", "$IRX"),
    "sectors": ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE"),
    "index_funds": ("SMH", "RSP", "QQQ", "IWM", "SPY"),
    "macro_funds": ("TLT", "HYG", "USO", "GLD"),   # the bond, oil and pooled-markets labels (labels/macro.py)
    "megacaps": ("NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO"),
}
ALL_SYMBOLS = tuple(s for group in SYMBOLS.values() for s in group)
BAR_SYMBOLS = SYMBOLS["breadth"]          # quotes come back as empty shells: read the newest finished bar instead
NO_HISTORY = ("$VIX1D",)                  # quoted, never kept by Schwab's history: only a live snapshot saves it
FIRST_BACKFILL_DAY = "2026-08-10"         # the oldest minute Schwab still served on 2026-09-26

def _session(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, SESSION_OPEN, tzinfo=ET), datetime.combine(day, SESSION_CLOSE, tzinfo=ET)


def _quote_entry(q: dict) -> dict:
    return {"last": q.get("lastPrice"), "close": q.get("closePrice"), "volume": q.get("totalVolume"),
            "quote_time": q.get("quoteTime")}


def _no_bars(symbol: str, when: str) -> None:
    """Schwab answered a symbol's history without an error and with no bars: said on a line of its own, since
    nothing else would show it (on 2026-09-28 $ADD, $VOLD and $VOLSPD came back empty all day, unremarked)."""
    log(JOB, f"{symbol} returned no minute bars {when}", err=True)


def snapshot(now: datetime) -> dict:
    """One minute's line: batched quotes for every quoted symbol and the newest finished bar for each
    breadth symbol. A call that fails is named under ``failed`` and costs only what it would have
    fetched; so is a breadth symbol Schwab answers with no bars once a minute of the session has
    finished, as ``"<symbol>: empty"``."""
    line: dict = {"ts": now.isoformat(timespec="seconds"), "quotes": {}, "bars": {}, "failed": []}
    quoted = [s for s in ALL_SYMBOLS if s not in BAR_SYMBOLS]
    try:
        line["quotes"] = {s: _quote_entry(q) for s, q in schwab.quotes(quoted).items()}
    except Exception as e:  # one failed call costs this minute's quotes, never the bars below
        line["failed"].append(f"quotes: {type(e).__name__}")
        log(JOB, f"quotes failed: {failure(e)}", err=True)
    start, _ = _session(now.date())
    for symbol in BAR_SYMBOLS:
        time.sleep(schwab.CALL_SPACING_S)
        try:
            got = schwab.minute_bars(symbol, start, now)
        except Exception as e:
            line["failed"].append(f"{symbol}: {type(e).__name__}")
            log(JOB, f"{symbol} bars failed: {failure(e)}", err=True)
            continue
        if not got and now >= start + timedelta(minutes=1):
            line["failed"].append(f"{symbol}: empty")
            _no_bars(symbol, f"since {start:%H:%M} ET")
        done = [b for b in got if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= now]
        if done:
            line["bars"][symbol] = done[-1]
    return line


def append_snapshot(state_dir: Path, line: dict) -> Path:
    path = Path(state_dir) / CONTEXT_SUBDIR / f"{line['ts'][:10]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    return path


def backfill_day(state_dir: Path, day: date) -> Path | None:
    """Every history symbol's minute bars for one past session, one line per minute. A day already on
    disk is left alone, and a day with no bars at all (a holiday, a weekend) writes nothing; on any other
    day each symbol Schwab served no bars for is logged as an error."""
    path = Path(state_dir) / CONTEXT_SUBDIR / "bars" / f"{day.isoformat()}.jsonl"
    if path.exists():
        return None
    start, end = _session(day)
    by_minute: dict[str, dict] = {}
    empty = []
    for symbol in ALL_SYMBOLS:
        if symbol in NO_HISTORY:
            continue
        time.sleep(schwab.CALL_SPACING_S)
        n = 0
        for b in schwab.minute_bars(symbol, start, end + timedelta(minutes=1)):
            t = datetime.fromisoformat(b["ts"])
            if start <= t < end:
                by_minute.setdefault((t + timedelta(minutes=1)).isoformat(), {})[symbol] = b
                n += 1
        if not n:
            empty.append(symbol)
    if not by_minute:
        return None
    for symbol in empty:
        _no_bars(symbol, f"for the {day.isoformat()} session")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps({"ts": ts, "bars": by_minute[ts]}) + "\n" for ts in sorted(by_minute)), encoding="utf-8")
    tmp.replace(path)                    # a day is on disk whole or not at all, so a rerun never skips half a day
    return path


def backfill(state_dir: Path, first: date, last: date) -> list[Path]:
    """backfill_day for every weekday from ``first`` to ``last``; returns the files written."""
    written, d = [], first
    while d <= last:
        if d.weekday() < 5:
            p = backfill_day(state_dir, d)
            if p:
                written.append(p)
        d += timedelta(days=1)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Save the market around SPX under state/spx_jev/context/.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--backfill", metavar="FROM", nargs="?", const=FIRST_BACKFILL_DAY,
                    help=f"save every past session's minute bars from this day (default {FIRST_BACKFILL_DAY}) to yesterday")
    args = ap.parse_args(argv)
    state_dir, now = Path(args.state_dir), datetime.now(ET)
    if args.backfill:
        written = backfill(state_dir, date.fromisoformat(args.backfill), now.date() - timedelta(days=1))
        log(JOB, f"backfilled {len(written)} sessions under {state_dir / CONTEXT_SUBDIR / 'bars'}")
        return 0
    line = snapshot(now)
    path = append_snapshot(state_dir, line)
    log(JOB, f"{len(line['quotes'])} quotes, {len(line['bars'])} bars"
             f"{', failed ' + ', '.join(line['failed']) if line['failed'] else ''} -> {path}")
    return 0 if line["quotes"] or line["bars"] else 1


if __name__ == "__main__":
    sys.exit(main())
