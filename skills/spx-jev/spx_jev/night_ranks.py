"""A night's measure placed against the last 20 nights', in thirds: how the overnight labels say "how big".

A fixed line in sigma cannot say how big a night was (the old 0.40 sigma large-gap line), since a
normal night swings with the market. So a night's measure is ranked against the same measure on the
last NIGHTS prior nights, taken at the same clock minute, and said by its third: "larger than 15 of
the last 20 nights' moves, top third". A prior night sits out of the rank when it cannot stand for a
normal night:

- ``roll``: Schwab's stitched history moved to the next contract between its two prices (rolls.py), so
  its move is the spread between two contracts, not a market move;
- ``holiday``: it spans a weekday market holiday or follows a half day;
- ``short``: its bars cover under SHORT_NIGHT_SHARE of what the symbol's nights usually cover, so it
  is thin or broken;
- ``unmeasured``: a price the measure needs is missing, or its file is.

At least OVERNIGHT_RANK_MIN_NIGHTS usable nights are needed; with fewer the measure is omitted with the
reason, never guessed. The overnight labels wait for the premarket lane; ``es_move`` is the first
measure, ready for it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from . import overnight, rolls
from .cuts import OVERNIGHT_RANK_MIN_NIGHTS
from .labels.words import third
from .sessions import is_trading_day, previous_trading_day
from .state_builder import load_jsonl

NIGHTS = 20
SHORT_NIGHT_SHARE = 0.9          # of the median coverage of the symbol's candidate nights
READ_STALE_MIN = 10              # the read price must come from a bar that finished at most this long before the read


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
    """``value`` against the usable ones of the last NIGHTS ``nights`` (oldest first): ``(rank, None)``,
    or ``(None, reason)`` under OVERNIGHT_RANK_MIN_NIGHTS usable nights."""
    recent = nights[-NIGHTS:]
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
    """The root's move from its own prior close to the read, on one night."""
    pct: float
    close_at: datetime
    read_at: datetime


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


def prior_nights(state_dir: Path, day: date, symbol: str, read_clock: time, table: dict,
                 measure: Callable[[Move], float]) -> list[Night]:
    """``measure`` of the move on each of the NIGHTS trading days before ``day``, oldest first, each
    skipped for a roll, a holiday, a short night or a missing price."""
    days, d = [], day
    while len(days) < NIGHTS:
        d = previous_trading_day(d)
        days.append(d)
    out = []
    for d in reversed(days):
        rows = load_jsonl(overnight.night_path(state_dir, d.isoformat()))
        mine = [r for r in rows if r["symbol"] == symbol]
        move = overnight_move(mine, symbol, d, read_clock) if mine else None
        coverage = _coverage(mine, symbol, d, read_clock) if mine else None
        if overnight.holiday_night(d):
            out.append(Night(d.isoformat(), None, "holiday", coverage))
        elif move is None:
            out.append(Night(d.isoformat(), None, "unmeasured", coverage))
        elif not rolls.same_contract(table, symbol, move.close_at, move.read_at):
            out.append(Night(d.isoformat(), None, "roll", coverage))
        else:
            out.append(Night(d.isoformat(), measure(move), None, coverage))
    return mark_short(out)


def _coverage(rows: list[dict], symbol: str, day: date, read_clock: time) -> float | None:
    """The share of the night's expected bars on file up to the read, at the finest resolution saved."""
    start = overnight.night_window(day)[0]
    until = datetime.combine(day, read_clock, tzinfo=overnight.ET)
    for minutes in overnight.BAR_MINUTES:
        if any(r["bar_minutes"] == minutes for r in rows):
            return overnight.check(rows, symbol, minutes, start, until)["coverage"]
    return None


def es_move(state_dir: Path, now: datetime, quoted: str | None = None) -> dict:
    """overnight.es_move's measure at ``now``: /ES from its prior close to the read on the same contract,
    its size ranked against the last NIGHTS nights' moves to the same minute. ``{"move_pct", "side",
    "rank": {"band", "larger_than", "of"}, "words"}``, or ``{"omitted": reason}``."""
    day, read_clock = now.date(), now.astimezone(overnight.ET).time().replace(second=0, microsecond=0)
    if not is_trading_day(day):
        return {"omitted": f"{day} is not a market day"}
    folder = Path(state_dir) / overnight.OVERNIGHT_SUBDIR
    table = rolls.load(folder)
    if rolls.pending(table, "/ES", quoted):
        return {"omitted": f"Schwab quotes {quoted} but the roll table is still on {table['current'].get('/ES')}: "
                           "the switch is not located yet, so the prior close may be on the old contract"}
    rows = [r for r in load_jsonl(overnight.night_path(state_dir, day.isoformat())) if r["symbol"] == "/ES"]
    move = overnight_move(rows, "/ES", day, read_clock)
    if move is None:
        return {"omitted": "no /ES price at yesterday's close or within the last "
                           f"{READ_STALE_MIN} minutes in the overnight store"}
    if not rolls.same_contract(table, "/ES", move.close_at, move.read_at):
        return {"omitted": "the futures rolled to the next contract overnight, so the move from yesterday's close "
                           "is the spread between two contracts"}
    rank, why = rank_night(abs(move.pct), prior_nights(state_dir, day, "/ES", read_clock, table, lambda m: abs(m.pct)))
    if rank is None:
        return {"omitted": f"overnight move not ranked: {why}"}
    side = "up" if move.pct > 0 else "down" if move.pct < 0 else "unchanged"
    close = move.close_at.strftime("%H:%M")
    return {"move_pct": round(move.pct, 4), "side": side,
            "rank": {"band": rank.band, "larger_than": rank.larger_than, "of": rank.of},
            "words": f"since {close} yesterday S&P futures are {side}, a move {rank.words('moves to this time')}"}
