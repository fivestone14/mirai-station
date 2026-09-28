"""The overnight futures store: every /ES, /ZN and bitcoin futures bar from the prior close to the read,
one file per night, checked on every save and kept for machine learning.

    python3 -m spx_jev.overnight                        # the launchd job's run (09:26 and 16:20 ET, market days)
    python3 -m spx_jev.overnight --day 2026-09-25       # one night by hand
    python3 -m spx_jev.overnight --backfill             # every night Schwab still serves (Phase 0)

A night is named for the trading day it leads into. Its window runs from NIGHT_LEAD_MIN minutes before
the prior trading day's close (15:55 ET, 12:55 after a half day, so the prior close is in the file) to
that day's own close, and a save keeps only the bars finished by the read: Schwab answers to the end of
the day whatever end time is asked, so the answer is trimmed here. A weekend or a holiday falls inside
the night that follows it. Each save asks for both resolutions: 1-minute bars (Schwab keeps about
40,000, some six weeks) and 5-minute bars (back to March 2026), so the 5-minute series runs unbroken
from the backfill on.

``state/spx_jev/overnight/{day}.jsonl``, one bar per line, sorted by symbol, resolution and time:

    {"schema_version", "day", "ts" (the bar's start, ISO with the New York offset), "symbol" ("/ES"),
     "contract" ("/ESZ26"), "contract_from" ("roll_table" or "quote"), "bar_minutes" (1 or 5),
     "open", "high", "low", "close", "volume", "session" ("overnight", "premarket" from 04:00, or
     "regular": the prior session's last minutes and this day's session), "source", "saved_at", "flags"}

Every save merges into the file and rewrites it whole (a crash leaves the old file): a bar already
saved is kept as first saved, so a rerun adds nothing, and a re-fetch that disagrees with it is counted.
A bar that fails a sanity check (low above open or close, high below them, a price at or under zero, a
negative volume) is kept with its ``flags``, never dropped. The contract is the one the roll table
(rolls.py) says Schwab's stitched series was on at that bar, else the one Schwab quotes the root under.

``state/spx_jev/overnight/manifest.jsonl``, one line per save that changed a night: per symbol and
resolution the bar count against the bars expected in the window (CME Globex hours, Sunday 18:00 to
Friday 17:00 with the 17:00 halt; bitcoin round the clock since CRYPTO_ROUND_THE_CLOCK_FROM), the
gaps (each missing stretch's first bar and its minutes), the flagged bars, the duplicates in Schwab's
answer, the re-fetches that disagreed, the bars per session, the contracts in the rows and the one
quoted, whether the table knows of a roll Schwab's quote shows (``roll_pending``), and whether the
night spans a market holiday or follows a half day. A thin market (/BTC trades a few times a minute
at best) shows its quiet minutes as gaps: they are listed, not treated as lost data.

Bitcoin is saved twice, /BTC and /MBT (the micro contract): the same price, a median 0 bp apart, but
/MBT trades nearly every minute of a weeknight where /BTC trades a few times an hour, so /MBT
(BITCOIN) is the one the labels read and /BTC the cross-check. CME bitcoin trades round the clock
since 2026-05-29, so its weekend bars are kept, in the night of the Monday they lead into.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import json
import sys
import time as clock
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import rolls, schwab
from .sessions import (SESSION_OPEN, half_days, is_trading_day, market_holidays, next_trading_day, previous_trading_day,
                       session_close)
from .state_builder import DEFAULT_STATE_DIR, load_jsonl

ET = ZoneInfo("America/New_York")
OVERNIGHT_SUBDIR = Path("spx_jev") / "overnight"
SAVE_LOCK = ".save.lock"               # held while nights merge and write: the 09:26 job and the 09:28 read can save at once
SCHEMA_VERSION = 1
SOURCE = "schwab_price_history"
SYMBOLS = ("/ES", "/ZN", "/BTC", "/MBT")
BITCOIN = "/MBT"                       # the bitcoin future the labels read: the same price as /BTC, which trades 0 to 5 times an hour at weekends
BAR_MINUTES = (1, 5)
NIGHT_LEAD_MIN = 5                     # the prior session's last minutes, so its close is in the night's file
PREMARKET_FROM = time(4, 0)
GLOBEX_HALT = (time(17, 0), time(18, 0))
ROUND_THE_CLOCK = ("/BTC", "/MBT")
CRYPTO_ROUND_THE_CLOCK_FROM = datetime(2026, 5, 29, 17, 0, tzinfo=ET)   # CME bitcoin's first weekend bar in Schwab's history is 05-30
CRYPTO_SATURDAY_BREAK = (time(3, 0), time(5, 0))                        # CME's weekly crypto maintenance: no bars in Schwab's history
SAVE_TIMES = ("09:26", "16:20")         # the launchd job's fires, ET: before a 09:28 read, and after the close
CATCH_UP_DAYS = 7                      # the daily run re-saves every night this many calendar days back
ROLL_LOOKBACK_DAYS = 45                # the daily run re-detects rolls over this many calendar days of saved nights
BACKFILL_FROM = date(2026, 3, 1)       # asked of Schwab; it answers from as far back as it keeps
REFERENCE_SYMBOLS = tuple(sorted(set(rolls.REFERENCES.values())))


# ---- the night -----------------------------------------------------------------------------------

def _noon(day: date) -> datetime:
    return datetime.combine(day, time(12, 0), tzinfo=ET)


def night_window(day: date) -> tuple[datetime, datetime]:
    """``(start, end)`` of the night leading into ``day``: the prior trading day's close less NIGHT_LEAD_MIN,
    to ``day``'s close."""
    return (session_close(_noon(previous_trading_day(day))) - timedelta(minutes=NIGHT_LEAD_MIN),
            session_close(_noon(day)))


def night_for(now: datetime) -> date:
    """The trading day the night in progress leads into: today until its close, then the next trading day."""
    today = now.date()
    return today if is_trading_day(today) and now < session_close(now) else next_trading_day(today)


def session_of(ts: datetime, day: date) -> str:
    prior_close = session_close(_noon(previous_trading_day(day)))
    if ts < prior_close or ts >= datetime.combine(day, SESSION_OPEN, tzinfo=ET):
        return "regular"
    return "premarket" if ts >= datetime.combine(day, PREMARKET_FROM, tzinfo=ET) else "overnight"


def holiday_night(day: date) -> bool:
    """True when the night spans a weekday market holiday or follows a half day."""
    prev = previous_trading_day(day)
    between = (prev + timedelta(days=k) for k in range(1, (day - prev).days))
    return prev in half_days(prev.year) or any(d.weekday() < 5 and d in market_holidays(d.year) for d in between)


def expected_open(symbol: str, t: datetime) -> bool:
    """Whether ``symbol`` trades at ``t``: bitcoin round the clock since CRYPTO_ROUND_THE_CLOCK_FROM but for its
    Saturday maintenance, the rest (and bitcoin before) CME Globex hours. Exchange holidays are not modelled: they show as gaps, and
    the night is marked ``holiday_night``."""
    t = t.astimezone(ET)
    wd, c = t.weekday(), t.time()
    if symbol in ROUND_THE_CLOCK and t >= CRYPTO_ROUND_THE_CLOCK_FROM:
        return not (wd == 5 and CRYPTO_SATURDAY_BREAK[0] <= c < CRYPTO_SATURDAY_BREAK[1])
    if wd == 5 or (wd == 6 and c < GLOBEX_HALT[1]) or (wd == 4 and c >= GLOBEX_HALT[0]):
        return False
    return not GLOBEX_HALT[0] <= c < GLOBEX_HALT[1]


# ---- rows ----------------------------------------------------------------------------------------

def night_path(state_dir: Path, day: str) -> Path:
    return Path(state_dir) / OVERNIGHT_SUBDIR / f"{day}.jsonl"


def manifest_path(state_dir: Path) -> Path:
    return Path(state_dir) / OVERNIGHT_SUBDIR / "manifest.jsonl"


def _key(row: dict) -> tuple:
    return row["symbol"], datetime.fromisoformat(row["ts"]), row["bar_minutes"]


def _order(row: dict) -> tuple:
    symbol, ts, minutes = _key(row)
    return SYMBOLS.index(symbol) if symbol in SYMBOLS else len(SYMBOLS), symbol, minutes, ts


def bar_flags(bar: dict) -> list[str]:
    o, h, l, c = (bar[k] for k in ("open", "high", "low", "close"))
    flags = []
    if not l <= min(o, c) or not max(o, c) <= h or l > h:
        flags.append("ohlc_inconsistent")
    if min(o, h, l, c) <= 0:
        flags.append("nonpositive_price")
    if (bar.get("volume") or 0) < 0:
        flags.append("negative_volume")
    return flags


class Series:
    """One symbol's answer at one resolution, sorted by time, so each night is cut out of it by bisection."""

    def __init__(self, bars: list[dict]):
        timed = sorted(((datetime.fromisoformat(b["ts"]).astimezone(ET), b) for b in bars), key=lambda p: p[0])
        self.times = [t for t, _ in timed]
        self.bars = [b for _, b in timed]

    def between(self, start: datetime, end: datetime) -> list[tuple[datetime, dict]]:
        lo, hi = bisect.bisect_left(self.times, start), bisect.bisect_left(self.times, end)
        return list(zip(self.times[lo:hi], self.bars[lo:hi]))

    @property
    def first(self) -> datetime | None:
        return self.times[0] if self.times else None


def night_rows(day: date, symbol: str, minutes: int, series: Series, now: datetime, saved_at: str) -> list[dict]:
    """The bars of ``day``'s night that had finished by ``now``, as rows (contract not yet stamped)."""
    start, end = night_window(day)
    until = min(now, end)
    rows = []
    for ts, b in series.between(start, until):
        if ts + timedelta(minutes=minutes) <= until:
            rows.append({"schema_version": SCHEMA_VERSION, "day": day.isoformat(), "ts": ts.isoformat(), "symbol": symbol,
                         "contract": None, "contract_from": None, "bar_minutes": minutes,
                         **{k: float(b[k]) for k in ("open", "high", "low", "close")}, "volume": float(b.get("volume") or 0.0),
                         "session": session_of(ts, day), "source": SOURCE, "saved_at": saved_at, "flags": bar_flags(b)})
    return rows


def merge(existing: list[dict], fresh: list[dict]) -> tuple[list[dict], dict]:
    """``existing`` plus the fresh rows not on disk, each key once, first save kept. The counts:
    ``added``, ``duplicates`` (a key twice in the fresh answer), ``already_saved`` and ``disagreed``
    (already saved with other prices), per (symbol, bar_minutes)."""
    by_key = {_key(r): r for r in existing}
    stats: dict[tuple, dict] = {}
    seen = set()
    for r in fresh:
        k = _key(r)
        s = stats.setdefault((r["symbol"], r["bar_minutes"]), {"added": 0, "duplicates": 0, "already_saved": 0, "disagreed": 0})
        if k in seen:
            s["duplicates"] += 1
            continue
        seen.add(k)
        old = by_key.get(k)
        if old is None:
            by_key[k] = r
            s["added"] += 1
            continue
        s["already_saved"] += 1
        if any(old[f] != r[f] for f in ("open", "high", "low", "close", "volume")):
            s["disagreed"] += 1
    return sorted(by_key.values(), key=_order), stats


def stamp(rows: list[dict], table: dict, quoted: dict[str, str]) -> int:
    """Set each row's contract from the roll table (else the quote); returns how many changed."""
    changed = 0
    for r in rows:
        contract, source = rolls.contract_at(table, r["symbol"], datetime.fromisoformat(r["ts"]), quoted.get(r["symbol"]))
        if (r.get("contract"), r.get("contract_from")) != (contract, source):
            r["contract"], r["contract_from"] = contract, source
            changed += 1
    return changed


def write_night(state_dir: Path, day: str, rows: list[dict]) -> Path:
    path = night_path(state_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)
    return path


# ---- checks --------------------------------------------------------------------------------------

def expected_slots(symbol: str, minutes: int, start: datetime, until: datetime) -> list[datetime]:
    """Every ``minutes`` bar start from ``start`` whose bar has finished by ``until`` while the market trades."""
    step, out, t = timedelta(minutes=minutes), [], start
    while t + step <= until:
        if expected_open(symbol, t):
            out.append(t)
        t += step
    return out


def check(rows: list[dict], symbol: str, minutes: int, start: datetime, until: datetime) -> dict:
    """One symbol and resolution in one night: counts against expected, gaps, flags, sessions, contracts."""
    mine = [r for r in rows if r["symbol"] == symbol and r["bar_minutes"] == minutes]
    have = {datetime.fromisoformat(r["ts"]) for r in mine}
    slots = expected_slots(symbol, minutes, start, until)
    gaps, run = [], None
    for t in slots:
        if t in have:
            run = None
        elif run is not None and t - run[1] == timedelta(minutes=minutes):
            run[1], run[2] = t, run[2] + minutes
        else:
            run = [t, t, minutes]
            gaps.append(run)
    sessions: dict[str, int] = {}
    for r in mine:
        sessions[r["session"]] = sessions.get(r["session"], 0) + 1
    on_slot = len(have & set(slots))
    return {
        "bars": len(mine), "expected": len(slots), "coverage": round(on_slot / len(slots), 4) if slots else None,
        "outside_hours": len(mine) - on_slot, "missing_minutes": sum(g[2] for g in gaps),
        "gaps": [[g[0].isoformat(), g[2]] for g in gaps],
        "flagged": {f: n for f in ("ohlc_inconsistent", "nonpositive_price", "negative_volume")
                    if (n := sum(1 for r in mine if f in r["flags"]))},
        "sessions": sessions, "contracts": sorted({r["contract"] for r in mine if r["contract"]}),
        "first": min(have).isoformat() if have else None, "last": max(have).isoformat() if have else None,
    }


# ---- saving --------------------------------------------------------------------------------------

def fetch(symbols: tuple[str, ...], start: datetime, now: datetime) -> tuple[dict, list[str]]:
    """``({(symbol, minutes): Series}, failed)``: both resolutions of every symbol from ``start``, spaced."""
    got, failed = {}, []
    for symbol in symbols:
        for minutes, call in ((1, schwab.minute_bars), (5, schwab.five_minute_bars)):
            clock.sleep(schwab.CALL_SPACING_S)
            try:
                got[(symbol, minutes)] = Series(call(symbol, start, now, extended_hours=True))
            except Exception as e:  # one refused call costs that symbol's resolution, never the rest
                failed.append(f"{symbol} {minutes}-min: {type(e).__name__}: {e}")
    return got, failed


def save_night(state_dir: Path, day: date, got: dict, quoted: dict[str, str], table: dict, now: datetime,
               failed: list[str]) -> dict | None:
    """Merge one night's fresh bars into its file and append its manifest line; None when nothing changed."""
    saved_at = now.isoformat(timespec="seconds")
    start, end = night_window(day)
    until = min(now, end)
    fresh = [r for (symbol, minutes), series in got.items() for r in night_rows(day, symbol, minutes, series, now, saved_at)]
    path = night_path(state_dir, day.isoformat())
    existing = load_jsonl(path)
    rows, stats = merge(existing, fresh)
    restamped = stamp(rows, table, quoted)
    added = sum(s["added"] for s in stats.values())
    if not rows or (not added and not restamped and path.exists()):
        return None
    write_night(state_dir, day.isoformat(), rows)
    symbols = {}
    for symbol in SYMBOLS:
        per = {}
        for minutes in BAR_MINUTES:
            c = check(rows, symbol, minutes, start, until)
            answered = got.get((symbol, minutes))
            if answered is not None and answered.first and answered.first > start:
                c["history_from"] = answered.first.isoformat()
            per[str(minutes)] = {**c, **stats.get((symbol, minutes), {"added": 0, "duplicates": 0, "already_saved": 0, "disagreed": 0})}
        symbols[symbol] = {"contract_quoted": quoted.get(symbol), "roll_pending": rolls.pending(table, symbol, quoted.get(symbol)),
                           "same_contract_through_night": rolls.same_contract(table, symbol, start, until), "bars": per}
    line = {"schema_version": SCHEMA_VERSION, "day": day.isoformat(), "saved_at": saved_at,
            "window": [start.isoformat(), until.isoformat(timespec="seconds")], "holiday_night": holiday_night(day),
            "rows": len(rows), "added": added, "restamped": restamped if existing else 0, "failed": failed, "symbols": symbols}
    with open(manifest_path(state_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    return line


def save_nights(state_dir: Path, days: list[date], now: datetime, fetch_from: datetime | None = None) -> list[dict]:
    """One fetch per symbol and resolution covering every night in ``days``, then each night merged and
    checked; the manifest lines of the nights that changed."""
    if not days:
        return []
    failed = []
    try:
        quoted = schwab.front_contracts(list(SYMBOLS))
    except Exception as e:
        quoted = {}
        failed.append(f"quotes: {type(e).__name__}: {e}")
    got, refused = fetch(SYMBOLS, fetch_from or night_window(min(days))[0], now)
    failed += refused
    lines = []
    with saving(state_dir):
        table = rolls.load(Path(state_dir) / OVERNIGHT_SUBDIR)
        for day in sorted(days):
            line = save_night(state_dir, day, got, quoted, table, now, failed)
            if line:
                lines.append(line)
    return lines


@contextmanager
def saving(state_dir: Path):
    """The store's save lock, waited for: a Mac that slept through the 09:26 job and the 09:28 read fires both
    on waking, and each merges into the same night's file and its one temporary file."""
    folder = Path(state_dir) / OVERNIGHT_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / SAVE_LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def saved_days(state_dir: Path) -> list[str]:
    return sorted(p.stem for p in (Path(state_dir) / OVERNIGHT_SUBDIR).glob("????-??-??.jsonl"))


def refresh_rolls(state_dir: Path, now: datetime, since: str | None = None) -> dict:
    """Re-detect every symbol's rolls over the nights saved from ``since`` (all, when None), fetching the
    reference markets' 5-minute bars, write the table, and restamp every saved night whose contracts
    changes (all of them, only when the rolls or the current contracts changed). Returns the table."""
    folder = Path(state_dir) / OVERNIGHT_SUBDIR
    days = [d for d in saved_days(state_dir) if since is None or d >= since]
    if not days:
        return rolls.load(folder)
    start = night_window(date.fromisoformat(days[0]))[0]
    references = {}
    for ref in REFERENCE_SYMBOLS:
        clock.sleep(schwab.CALL_SPACING_S)
        references[ref] = schwab.five_minute_bars(ref, start, now)
    five, nights = {s: {} for s in SYMBOLS}, {s: {} for s in SYMBOLS}
    for day in days:
        rows = load_jsonl(night_path(state_dir, day))
        for symbol in SYMBOLS:
            mine = [r for r in rows if r["symbol"] == symbol]
            for r in mine:
                if r["bar_minutes"] == 5:
                    five[symbol][r["ts"]] = r
            finest = min((r["bar_minutes"] for r in mine), default=None)
            nights[symbol][day] = [r for r in mine if r["bar_minutes"] == finest and r["session"] != "regular"]
    table = before = rolls.load(folder)
    quoted = {}
    try:
        quoted = schwab.front_contracts(list(SYMBOLS))
    except Exception as e:  # the table then keeps its own names; a first detection waits for a quote
        print(f"spx-jev-overnight :: quotes failed, contract names left as they were: {type(e).__name__}: {e}", file=sys.stderr)
    for symbol in SYMBOLS:
        found = rolls.detect(symbol, list(five[symbol].values()), references[rolls.REFERENCES[symbol]], nights[symbol])
        table = rolls.merge(table, symbol, found, days[0], quoted.get(symbol))
    changed = (table["current"], table["rolls"]) != (before["current"], before["rolls"])
    table["detected_at"] = now.isoformat(timespec="seconds")
    rolls.save(folder, table)
    for day in saved_days(state_dir) if changed else []:
        rows = load_jsonl(night_path(state_dir, day))
        if stamp(rows, table, quoted):
            write_night(state_dir, day, rows)
    return table


# ---- the runs ------------------------------------------------------------------------------------

def days_to_save(now: datetime, catch_up: int = CATCH_UP_DAYS) -> list[date]:
    """The night in progress and every trading day's night of the last ``catch_up`` calendar days."""
    tonight = night_for(now)
    days = [d for k in range(catch_up, -1, -1) if is_trading_day(d := now.date() - timedelta(days=k)) and d < tonight]
    return days + [tonight]


def backfill_days(got: dict, now: datetime) -> list[date]:
    """Every trading day from the one whose night holds the oldest bar Schwab served to the night in progress."""
    oldest = min((s.first for s in got.values() if s.first), default=None)
    if oldest is None:
        return []
    d, last, out = oldest.date(), night_for(now), []
    while d <= last:
        if is_trading_day(d) and night_window(d)[1] > oldest:
            out.append(d)
        d += timedelta(days=1)
    return out


def summary(lines: list[dict]) -> dict:
    """Per symbol and resolution: nights with bars, bars, nights with gaps, missing minutes, earliest bar."""
    out: dict = {}
    latest = {}
    for line in lines:
        latest[line["day"]] = line
    for line in latest.values():
        for symbol, s in line["symbols"].items():
            for minutes, c in s["bars"].items():
                o = out.setdefault(f"{symbol} {minutes}-min", {"nights": 0, "bars": 0, "nights_with_gaps": 0, "missing_minutes": 0,
                                                              "flagged": 0, "earliest": None})
                if not c["bars"]:
                    continue
                o["nights"] += 1
                o["bars"] += c["bars"]
                o["nights_with_gaps"] += bool(c["gaps"])
                o["missing_minutes"] += c["missing_minutes"]
                o["flagged"] += sum(c["flagged"].values())
                o["earliest"] = min(filter(None, (o["earliest"], c["first"])))
    return out


def backfill(state_dir: Path, now: datetime, since: date = BACKFILL_FROM) -> tuple[list[dict], dict]:
    """Phase 0: every night Schwab still serves, both resolutions, then the roll table over all of it.
    Rerunning adds only what is not on disk."""
    start = datetime.combine(since, time(0, 0), tzinfo=ET)
    failed = []
    try:
        quoted = schwab.front_contracts(list(SYMBOLS))
    except Exception as e:
        quoted = {}
        failed.append(f"quotes: {type(e).__name__}: {e}")
    got, refused = fetch(SYMBOLS, start, now)
    failed += refused
    table = rolls.load(Path(state_dir) / OVERNIGHT_SUBDIR)
    lines = [line for day in backfill_days(got, now) if (line := save_night(state_dir, day, got, quoted, table, now, failed))]
    table = refresh_rolls(state_dir, now)
    return lines, table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Save the overnight futures bars under state/spx_jev/overnight/.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--day", help="YYYY-MM-DD, the trading day one night leads into")
    ap.add_argument("--backfill", action="store_true", help="every night Schwab still serves, then the roll table")
    args = ap.parse_args(argv)
    state_dir, now = Path(args.state_dir), datetime.now(ET)
    if args.backfill:
        lines, table = backfill(state_dir, now)
        for name, s in summary(lines).items():
            print(f"spx-jev-overnight :: {name}: {s['nights']} nights, {s['bars']} bars, {s['nights_with_gaps']} nights with gaps "
                  f"({s['missing_minutes']} minutes), {s['flagged']} flagged, from {s['earliest']}")
        for r in table["rolls"]:
            print(f"spx-jev-overnight :: roll {r['symbol']} {r.get('from')} -> {r.get('to')} on {r['day']} at {r.get('at')} "
                  f"(basis step {r['basis_step']} {r['basis_unit']}, {r['step_vs_typical']}x typical)")
        for symbol, windows in table.get("windows_without_roll", {}).items():
            for w in windows:
                print(f"spx-jev-overnight :: {symbol}: no roll found in its window {w[0]} to {w[1]}")
        failed = sorted({f for line in lines for f in line["failed"]})
        print(f"spx-jev-overnight :: backfilled {len(lines)} nights under {state_dir / OVERNIGHT_SUBDIR}"
              f"{'; failed ' + '; '.join(failed) if failed else ''}")
        return 1 if failed else 0
    if args.day:
        days = [date.fromisoformat(args.day)]
    elif not is_trading_day(now.date()):
        print(f"spx-jev-overnight :: {now.date()} is not a market day")
        return 0
    else:
        days = days_to_save(now)
    lines = save_nights(state_dir, days, now)
    try:
        refresh_rolls(state_dir, now, since=(now.date() - timedelta(days=ROLL_LOOKBACK_DAYS)).isoformat())
    except Exception as e:  # the bars are saved; the table waits for the next run
        print(f"spx-jev-overnight :: roll detection failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    failed = sorted({f for line in lines for f in line["failed"]})
    for line in lines:
        es = line["symbols"]["/ES"]["bars"]["1"]
        print(f"spx-jev-overnight :: {line['day']} +{line['added']} bars, {line['rows']} on file; /ES 1-min "
              f"{es['bars']}/{es['expected']} expected, {len(es['gaps'])} gaps")
    print(f"spx-jev-overnight :: {len(lines)} of {len(days)} nights changed under {state_dir / OVERNIGHT_SUBDIR}"
          f"{'; failed ' + '; '.join(failed) if failed else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
