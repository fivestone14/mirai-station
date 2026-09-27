"""A night's measure placed against the last 20 nights', in thirds: how the overnight labels say "how big".

A fixed line in sigma cannot say how big a night was (the old 0.40 sigma large-gap line), since a
normal night swings with the market. So a night's measure is ranked against the same measure on the
last NIGHT_RANK_COUNT prior nights, taken at the same clock minute, and said by its third: "larger than 15 of
the last 20 nights' moves, top third". A prior night sits out of the rank when it cannot stand for a
normal night:

- ``roll``: Schwab's stitched history moved to the next contract between its two prices (rolls.py), so
  its move is the spread between two contracts, not a market move;
- ``holiday``: it spans a weekday market holiday or follows a half day;
- ``short``: its bars cover under SHORT_NIGHT_SHARE of what the symbol's nights usually cover, so it
  is thin or broken;
- ``unmeasured``: a price the measure needs is missing, or its file is.

At least OVERNIGHT_RANK_MIN_NIGHTS usable nights are needed; with fewer the measure is omitted with the
reason, never guessed. ``night_move`` measures the move from the prior close to the read on one contract
and ``ranked_move`` (``es_move`` over the store) ranks it; ``window_move`` and ``prior_window_nights`` rank
any stretch of the night (story.py) against the same stretch on the last nights, and ``window_range`` and
``prior_window_ranges`` its high-low range, for the premarket lane's labels. A rank reads the last nights'
files through a small cache keyed on each file's size and time, so the many measures of one read load
each night once.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable

from . import overnight, rolls
from .cuts import NIGHT_RANK_COUNT, OVERNIGHT_RANK_MIN_NIGHTS
from .labels.words import third
from .sessions import is_trading_day, previous_trading_day
from .state_builder import load_jsonl

SHORT_NIGHT_SHARE = 0.9          # of the median coverage of the symbol's candidate nights
READ_STALE_MIN = 10              # the read price must come from a bar that finished at most this long before the read
CACHED_NIGHT_FILES = 2 * (NIGHT_RANK_COUNT + 1)   # two symbols' last nights and tonight: /ES and /ZN on one read


@dataclass(frozen=True)
class Night:
    """One prior night's measure, or why it sits out of the rank."""
    day: str
    value: float | None
    skip: str | None = None
    coverage: float | None = None


@dataclass(frozen=True)
class NightRank:
    """How many of ``of`` usable prior nights the value is larger than."""
    larger_than: int
    of: int

    @property
    def share(self) -> float:
        return self.larger_than / self.of

    @property
    def band(self) -> str:
        return f"{third(self.share)} third"

    def words(self, what: str) -> str:
        return f"larger than {self.larger_than} of the last {self.of} nights' {what}, {self.band}"


def rank_night(value: float, nights: list[Night]) -> tuple[NightRank | None, str | None]:
    """``value`` against the usable ones of the last NIGHT_RANK_COUNT ``nights`` (oldest first): ``(rank, None)``,
    or ``(None, reason)`` under OVERNIGHT_RANK_MIN_NIGHTS usable nights."""
    recent = nights[-NIGHT_RANK_COUNT:]
    usable = [n.value for n in recent if n.skip is None and n.value is not None]
    if len(usable) < OVERNIGHT_RANK_MIN_NIGHTS:
        skipped: dict[str, int] = {}
        for n in recent:
            if n.skip:
                skipped[n.skip] = skipped.get(n.skip, 0) + 1
        why = ", ".join(f"{k} {v}" for k, v in sorted(skipped.items()))
        return None, (f"only {len(usable)} usable of the last {len(recent)} nights{f' ({why} left out)' if why else ''}, "
                      f"fewer than {OVERNIGHT_RANK_MIN_NIGHTS}")
    return NightRank(sum(1 for v in usable if v < value), len(usable)), None


def mark_short(nights: list[Night]) -> list[Night]:
    """The nights whose coverage is under SHORT_NIGHT_SHARE of the median coverage, marked ``short``."""
    covered = [n.coverage for n in nights if n.coverage is not None]
    if not covered:
        return nights
    floor = SHORT_NIGHT_SHARE * statistics.median(covered)
    return [Night(n.day, n.value, "short", n.coverage) if n.skip is None and n.coverage is not None and n.coverage < floor
            else n for n in nights]


# ---- the overnight move --------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    """The root's move in percent between two of its prices on one night, with when each price's bar finished."""
    pct: float
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class Range:
    """The root's high-low range in percent of its low over a stretch of one night, with when its first and
    last bars finished, so a roll between them can be refused as a Move's is."""
    pct: float
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class RankedMove:
    """A move from the prior close to the read, on one contract, and its size's rank against the last nights'."""
    move: Move
    rank: NightRank


def _price_at(rows: list[dict], symbol: str, until: datetime, exact: bool) -> tuple[float, datetime] | None:
    """The close of the newest bar finished by ``until`` (1-minute bars first, else 5-minute), with the
    moment it finished; ``exact`` asks for a bar finishing at ``until`` itself."""
    for minutes in overnight.BAR_MINUTES:
        done = [(datetime.fromisoformat(r["ts"]) + timedelta(minutes=minutes), r) for r in rows
                if r["symbol"] == symbol and r["bar_minutes"] == minutes]
        done = [(end, r) for end, r in done if end <= until]
        if not done:
            continue
        end, row = max(done, key=lambda p: p[0])
        if (end == until) if exact else (until - end <= timedelta(minutes=READ_STALE_MIN)):
            return row["close"], end
    return None


def overnight_move(rows: list[dict], symbol: str, day: date, read_clock: time) -> Move | None:
    """``symbol``'s move in percent from its price at the prior session's close to ``read_clock`` on ``day``."""
    prior_close = overnight.night_window(day)[0] + timedelta(minutes=overnight.NIGHT_LEAD_MIN)
    anchor = _price_at(rows, symbol, prior_close, exact=True)
    read = _price_at(rows, symbol, datetime.combine(day, read_clock, tzinfo=overnight.ET), exact=False)
    if anchor is None or read is None or anchor[0] <= 0:
        return None
    return Move(100.0 * (read[0] / anchor[0] - 1.0), anchor[1], read[1])


def price_by(rows: list[dict], symbol: str, until: datetime) -> tuple[float, datetime] | None:
    """The close of ``symbol``'s newest bar finished by ``until``, either resolution (the 1-minute bar when
    both finish together), with the moment it finished; how old that is, the caller judges."""
    done = [(datetime.fromisoformat(r["ts"]) + timedelta(minutes=r["bar_minutes"]), -r["bar_minutes"], r["close"]) for r in rows
            if r["symbol"] == symbol and r["bar_minutes"] in overnight.BAR_MINUTES]
    done = [d for d in done if d[0] <= until]
    if not done:
        return None
    end, _, close = max(done, key=lambda d: d[:2])
    return close, end


def window_move(rows: list[dict], symbol: str, start: datetime, end: datetime) -> Move | None:
    """``symbol``'s move in percent from its newest price by ``start`` to its newest price by ``end``
    (price_by). The move's ``start_at`` and ``end_at`` say when those bars finished, so a caller can see
    an edge that fell in a halt or a gap."""
    a, b = price_by(rows, symbol, start), price_by(rows, symbol, end)
    if a is None or b is None or a[0] <= 0:
        return None
    return Move(100.0 * (b[0] / a[0] - 1.0), a[1], b[1])


def window_range(rows: list[dict], symbol: str, start: datetime, end: datetime) -> Range | None:
    """``symbol``'s high-low range over the bars of either resolution that start at or after ``start`` and
    finish by ``end``, a bar flagged by the store's sanity check left out; None without a bar."""
    inside = []
    for r in rows:
        if r["symbol"] != symbol or r["bar_minutes"] not in overnight.BAR_MINUTES or r.get("flags"):
            continue
        begin = datetime.fromisoformat(r["ts"])
        if begin >= start and begin + timedelta(minutes=r["bar_minutes"]) <= end:
            inside.append((begin + timedelta(minutes=r["bar_minutes"]), r))
    if not inside:
        return None
    low, high = min(r["low"] for _, r in inside), max(r["high"] for _, r in inside)
    if low <= 0:
        return None
    return Range(100.0 * (high / low - 1.0), min(t for t, _ in inside), max(t for t, _ in inside))


@lru_cache(maxsize=CACHED_NIGHT_FILES)
def _cached_rows(path: str, mtime_ns: int, size: int, symbol: str) -> tuple[dict, ...]:
    return tuple(r for r in load_jsonl(Path(path)) if r["symbol"] == symbol)


def _night_rows(state_dir: Path, day: date, symbol: str) -> tuple[dict, ...]:
    """``symbol``'s rows of the night into ``day``, loaded once while its file is unchanged; empty without a file."""
    path = overnight.night_path(state_dir, day.isoformat())
    try:
        st = path.stat()
    except FileNotFoundError:
        return ()
    return _cached_rows(str(path), st.st_mtime_ns, st.st_size, symbol)


def _prior(state_dir: Path, day: date, symbol: str, table: dict, move_of: Callable[[list[dict], date], Move | Range | None],
           until: Callable[[date], datetime], measure: Callable[[Move | Range], float]) -> list[Night]:
    """``measure`` of ``move_of`` on each of the NIGHT_RANK_COUNT trading days before ``day``, oldest first, each
    skipped for a roll, a holiday, a short night (coverage up to ``until``) or a missing price."""
    days, d = [], day
    while len(days) < NIGHT_RANK_COUNT:
        d = previous_trading_day(d)
        days.append(d)
    out = []
    for d in reversed(days):
        mine = list(_night_rows(state_dir, d, symbol))
        move = move_of(mine, d) if mine else None
        coverage = _coverage(mine, symbol, d, until(d)) if mine else None
        if overnight.holiday_night(d):
            out.append(Night(d.isoformat(), None, "holiday", coverage))
        elif move is None:
            out.append(Night(d.isoformat(), None, "unmeasured", coverage))
        elif not rolls.same_contract(table, symbol, move.start_at, move.end_at):
            out.append(Night(d.isoformat(), None, "roll", coverage))
        else:
            out.append(Night(d.isoformat(), measure(move), None, coverage))
    return mark_short(out)


def prior_nights(state_dir: Path, day: date, symbol: str, read_clock: time, table: dict,
                 measure: Callable[[Move], float]) -> list[Night]:
    """``measure`` of the move from the prior close to ``read_clock`` on each of the last NIGHT_RANK_COUNT nights before ``day``."""
    return _prior(state_dir, day, symbol, table, lambda rows, d: overnight_move(rows, symbol, d, read_clock),
                  lambda d: datetime.combine(d, read_clock, tzinfo=overnight.ET), measure)


def prior_window_nights(state_dir: Path, day: date, symbol: str, window: Callable[[date], tuple[datetime, datetime]], table: dict,
                        measure: Callable[[Move], float]) -> list[Night]:
    """``measure`` of ``window_move`` over ``window(d)``, the same stretch of each of the last NIGHT_RANK_COUNT nights
    before ``day`` (story.stretch_window), each skipped like prior_nights'."""
    def move_of(rows: list[dict], d: date) -> Move | None:
        start, end = window(d)
        return window_move(rows, symbol, start, end)
    return _prior(state_dir, day, symbol, table, move_of, lambda d: window(d)[1], measure)


def prior_window_ranges(state_dir: Path, day: date, symbol: str, window: Callable[[date], tuple[datetime, datetime]],
                        table: dict) -> list[Night]:
    """``window_range`` over ``window(d)`` in percent, the same stretch of each of the last NIGHT_RANK_COUNT nights
    before ``day``, each skipped like prior_nights'."""
    def range_of(rows: list[dict], d: date) -> Range | None:
        start, end = window(d)
        return window_range(rows, symbol, start, end)
    return _prior(state_dir, day, symbol, table, range_of, lambda d: window(d)[1], lambda r: r.pct)


def _coverage(rows: list[dict], symbol: str, day: date, until: datetime) -> float | None:
    """The share of the night's expected bars on file up to ``until``, at the finest resolution saved."""
    start = overnight.night_window(day)[0]
    for minutes in overnight.BAR_MINUTES:
        if any(r["bar_minutes"] == minutes for r in rows):
            return overnight.check(rows, symbol, minutes, start, until)["coverage"]
    return None


def quoted_contract(state_dir: Path, day: date, symbol: str, now: datetime) -> str | None:
    """The contract Schwab quoted ``symbol`` under at the newest save of the night into ``day`` made by ``now``
    that quoted it (the manifest's ``contract_quoted``), for rolls.pending; a save whose quote call failed
    is passed over, so it cannot hide a roll an earlier save saw. None when no save by then quoted it."""
    quotes = [(datetime.fromisoformat(line["saved_at"]), quoted) for line in load_jsonl(overnight.manifest_path(state_dir))
              if line.get("day") == day.isoformat() and datetime.fromisoformat(line["saved_at"]) <= now
              and (quoted := ((line.get("symbols") or {}).get(symbol) or {}).get("contract_quoted"))]
    return max(quotes)[1] if quotes else None


def roll_pending(table: dict, symbol: str, quoted: str | None) -> str:
    """Why ``symbol``'s moves cannot be measured yet when Schwab quotes a contract the roll table has not rolled
    to (rolls.pending); empty when it has."""
    if not rolls.pending(table, symbol, quoted):
        return ""
    return (f"Schwab quotes {quoted} but the roll table is still on {table['current'].get(symbol)}: "
            "the switch is not located yet, so the prior close may be on the old contract")


def night_move(rows: list[dict], symbol: str, day: date, read_clock: time, table: dict,
               quoted: str | None = None) -> tuple[Move | None, str]:
    """``symbol``'s move in ``rows`` (the night's store rows) from its prior close to ``read_clock`` on ``day``, on
    one contract; ``(None, why)`` when the quote shows a roll the table has not located, a price is missing
    or the night spans a roll."""
    pending = roll_pending(table, symbol, quoted)
    if pending:
        return None, pending
    move = overnight_move(rows, symbol, day, read_clock)
    if move is None:
        return None, f"no {symbol} price at its prior close or within the last {READ_STALE_MIN} minutes in the overnight store"
    if not rolls.same_contract(table, symbol, move.start_at, move.end_at):
        return None, (f"{symbol} rolled to the next contract overnight, so the move from the prior close "
                      "is the spread between two contracts")
    return move, ""


def ranked_move(rows: list[dict], state_dir: Path, day: date, symbol: str, read_clock: time, table: dict,
                quoted: str | None = None) -> tuple[RankedMove | None, str]:
    """night_move with its size ranked against the last NIGHT_RANK_COUNT nights' moves to the same minute;
    ``(None, why)`` as night_move's, or when too few nights rank."""
    move, why = night_move(rows, symbol, day, read_clock, table, quoted)
    if move is None:
        return None, why
    rank, why = rank_night(abs(move.pct), prior_nights(state_dir, day, symbol, read_clock, table, lambda m: abs(m.pct)))
    if rank is None:
        return None, f"overnight move not ranked: {why}"
    return RankedMove(move, rank), ""


def es_move(state_dir: Path, now: datetime, quoted: str | None = None) -> dict:
    """overnight.es_move's measure at ``now`` over the store (ranked_move on /ES): ``{"move_pct", "side",
    "rank": {"band", "larger_than", "of"}, "words"}``, or ``{"omitted": reason}``."""
    day, read_clock = now.date(), now.astimezone(overnight.ET).time().replace(second=0, microsecond=0)
    if not is_trading_day(day):
        return {"omitted": f"{day} is not a market day"}
    table = rolls.load(Path(state_dir) / overnight.OVERNIGHT_SUBDIR)
    ranked, why = ranked_move(list(_night_rows(state_dir, day, "/ES")), state_dir, day, "/ES", read_clock, table, quoted)
    if ranked is None:
        return {"omitted": why}
    move, rank = ranked.move, ranked.rank
    side = "up" if move.pct > 0 else "down" if move.pct < 0 else "unchanged"
    close = move.start_at.strftime("%H:%M")
    return {"move_pct": round(move.pct, 4), "side": side,
            "rank": {"band": rank.band, "larger_than": rank.larger_than, "of": rank.of},
            "words": f"since {close} yesterday S&P futures are {side}, a move {rank.words('moves to this time')}"}
