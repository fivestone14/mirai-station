"""The overnight block: what the futures did between the prior close and the open, from the station's overnight
store (``state/spx_jev/overnight/{day}.jsonl``, spx_jev.overnight), the night named for the session it leads into.

The night is read as the store saved it and cut at the read: only bars finished by the cut count, and every
price is taken at the last pre-open point, the open (09:30 ET) or the cut itself on a read before it. The move
from the prior close is /ES at that point against /ES at the prior session's close (its bar finishing exactly
there, 16:00 ET or 13:00 after a half day), in sig; the range is the night's /ES high less its low, flagged bars
left out, in sig; ``zn_pct`` is the ten-year note future over the same two points, in percent.

The legs are the station's own stretches of the night (spx_jev.story.STRETCHES), each exchange's clock setting
its edge, so the New York times move on the clock-change weeks:

    after_close      the prior close to 18:00 ET, when Globex reopens (the after-hours earnings move)
    asia             18:00 ET to Tokyo's 15:30 close (02:30 ET in New York's summer time, 01:30 in winter)
    europe_open      Tokyo's close to 30 minutes after Frankfurt's 09:00 open (03:30 ET in summer, 04:00 in the mismatch week)
    europe_morning   to 08:00 ET
    pre_report       08:00 ET to the first pre-open release the calendar lists between 08:00 and 08:45 (08:30 when none)
    report_window    the release minute to 08:45 ET, measured every day, report or not
    last_stretch     08:45 ET to the open

Each leg is /ES at its end less /ES at its start (the newest bar finished by each edge), in sig; the leg the read
falls inside ends at the read, and a leg that has not started is not in the story yet. ``gap_origin`` names the
leg that carried most of the night: the largest leg in the direction of the legs' sum, or the largest by size
when they sum to nothing.

Ranks follow the station's own night rule (spx_jev.night_ranks): the size of the move in percent against the
same stretch on the last 20 nights, a night that spans a roll, a holiday, is thin or lacks a price sitting out;
``v`` is today's value in sig and ``r`` says how many of those nights moved less. A move across a contract roll
(spx_jev.rolls) is the spread between two contracts and is left out, as is one while Schwab quotes a contract
the roll table has not located yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable

from ... import jsonl_store, station_stores
from ...control import ET
from .. import units
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs

BLOCK = "overnight"
INDEX_FUTURE = "/ES"
TEN_YEAR_FUTURE = "/ZN"
# The pre-open price must come from a bar that finished within this of the point it is read at, else the store has
# a hole there (spx_jev.night_ranks.READ_STALE_MIN).
PRE_OPEN_STALE_MIN = 10
PCT_DECIMALS = 2


@dataclass(frozen=True)
class Stretch:
    """A move of one symbol between two of the night's prices, with when each price's bar finished."""
    points: float
    pct: float
    start_at: datetime
    end_at: datetime


def build_overnight_block(inputs: FrozenInputs) -> BlockResult:
    """``{es_vs_close_sig, range_sig, gap_origin, zn_pct, legs_sig}`` for the night into the read's day."""
    overnight = station_stores.import_spx_jev("overnight")
    path = overnight.night_path(inputs.state_dir, inputs.day)
    if not path.exists():
        return whole_block_absent(BLOCK, "no_overnight_file")
    rows = [r for r in jsonl_store.iter_json_lines(path) if _finished_by(r, inputs.cut)]
    if not any(r.get("symbol") == INDEX_FUTURE for r in rows):
        return whole_block_absent(BLOCK, "no_es_bars")
    day = date.fromisoformat(inputs.day)
    until = min(inputs.cut, _session_open(day))
    table = station_stores.import_spx_jev("rolls").load(path.parent)
    result = BlockResult()
    _index_future(inputs, rows, day, until, table, result)
    _ten_year_future(inputs, rows, day, until, table, result)
    result.data = units.clean(result.data)
    return result


# --- /ES ---------------------------------------------------------------------------------------------------

def _index_future(inputs: FrozenInputs, rows: list[dict], day: date, until: datetime, table: dict, result: BlockResult) -> None:
    sigma = inputs.sigma_points
    pending = _roll_pending(inputs, day, INDEX_FUTURE, table)
    if pending:
        for field in ("es_vs_close_sig", "range_sig", "gap_origin", "legs_sig"):
            result.leave_out(f"{BLOCK}.{field}", pending)
        return
    _since_close(inputs, rows, day, until, table, result)
    _range(inputs, rows, day, until, table, result)
    legs = _legs(rows, day, until, table, sigma, result)
    _gap_origin(legs, result)


def _since_close(inputs: FrozenInputs, rows: list[dict], day: date, until: datetime, table: dict, result: BlockResult) -> None:
    """/ES at the last pre-open point against the prior close, in sig, its size ranked against the last nights."""
    field = f"{BLOCK}.es_vs_close_sig"
    move, why = _night_move(rows, INDEX_FUTURE, day, until, table)
    if move is None:
        result.leave_out(field, why)
        return
    night_ranks = station_stores.import_spx_jev("night_ranks")
    nights = night_ranks.prior_window_nights(inputs.state_dir, day, INDEX_FUTURE, _same_window(until), table, lambda m: abs(m.pct))
    result.data["es_vs_close_sig"] = _size_ranked(units.sig(move.points, inputs.sigma_points), abs(move.pct), _usable(nights))


def _range(inputs: FrozenInputs, rows: list[dict], day: date, until: datetime, table: dict, result: BlockResult) -> None:
    """The night's /ES high less its low, in sig, its size in percent ranked against the last nights' ranges."""
    field = f"{BLOCK}.range_sig"
    bars = [r for r in rows if r.get("symbol") == INDEX_FUTURE and not r.get("flags")
            and _starts(r) >= _night_start(day) and _ends(r) <= until]
    if not bars:
        result.leave_out(field, "no_bars")
        return
    if not _same_contract(table, INDEX_FUTURE, min(_ends(b) for b in bars), max(_ends(b) for b in bars)):
        result.leave_out(field, "contract_roll")
        return
    low, high = min(float(b["low"]) for b in bars), max(float(b["high"]) for b in bars)
    if low <= 0:
        result.leave_out(field, "no_bars")
        return
    night_ranks = station_stores.import_spx_jev("night_ranks")
    nights = night_ranks.prior_window_ranges(inputs.state_dir, day, INDEX_FUTURE, _same_window(until), table)
    result.data["range_sig"] = _size_ranked(units.sig(high - low, inputs.sigma_points), 100.0 * (high / low - 1.0), _usable(nights))


def _legs(rows: list[dict], day: date, until: datetime, table: dict, sigma: float, result: BlockResult) -> dict[str, float | None]:
    """Each stretch of the night the read has seen (spx_jev.story.stretches), as /ES points moved over it in sig;
    a leg with no bar at an edge, or a roll inside it, is left out and carried as None."""
    story = station_stores.import_spx_jev("story")
    legs: dict[str, float | None] = {}
    for stretch in story.stretches(day, until):
        move = _window_move(rows, INDEX_FUTURE, stretch.start, stretch.end)
        if move is None:
            result.leave_out(f"{BLOCK}.legs_sig.{stretch.name}", "no_bar_at_edge")
            legs[stretch.name] = None
        elif not _same_contract(table, INDEX_FUTURE, move.start_at, move.end_at):
            result.leave_out(f"{BLOCK}.legs_sig.{stretch.name}", "contract_roll")
            legs[stretch.name] = None
        else:
            legs[stretch.name] = units.sig(move.points, sigma)
    if not legs:
        result.leave_out(f"{BLOCK}.legs_sig", "no_legs")
        return legs
    result.data["legs_sig"] = units.clean(legs)
    return legs


def _gap_origin(legs: dict[str, float | None], result: BlockResult) -> None:
    """The leg that carried most of the night: the largest in the direction of the legs' sum, or the largest by
    size when they sum to nothing; absent when a leg is missing, since the comparison would be between unequals."""
    field = f"{BLOCK}.gap_origin"
    if not legs:
        result.leave_out(field, "no_legs")
        return
    if any(v is None for v in legs.values()):
        result.leave_out(field, "leg_missing")
        return
    net = sum(legs.values())
    if net == 0:
        result.data["gap_origin"] = max(legs, key=lambda name: abs(legs[name]))
    else:
        result.data["gap_origin"] = max(legs, key=lambda name: legs[name] * (1 if net > 0 else -1))


# --- /ZN ---------------------------------------------------------------------------------------------------

def _ten_year_future(inputs: FrozenInputs, rows: list[dict], day: date, until: datetime, table: dict, result: BlockResult) -> None:
    field = f"{BLOCK}.zn_pct"
    if not any(r.get("symbol") == TEN_YEAR_FUTURE for r in rows):
        result.leave_out(field, "no_zn_bars")
        return
    pending = _roll_pending(inputs, day, TEN_YEAR_FUTURE, table)
    if pending:
        result.leave_out(field, pending)
        return
    move, why = _night_move(rows, TEN_YEAR_FUTURE, day, until, table)
    if move is None:
        result.leave_out(field, why)
        return
    result.data["zn_pct"] = round(move.pct, PCT_DECIMALS)


# --- the night's prices -----------------------------------------------------------------------------------

def _night_move(rows: list[dict], symbol: str, day: date, until: datetime, table: dict) -> tuple[Stretch | None, str]:
    """``symbol`` from the bar finishing exactly at the prior close to the newest bar by ``until`` (within
    PRE_OPEN_STALE_MIN of it), on one contract; else ``(None, why)``."""
    night_ranks = station_stores.import_spx_jev("night_ranks")
    start = _night_start(day)
    anchor = night_ranks.price_by(rows, symbol, start)
    if anchor is None or anchor[1] != start or anchor[0] <= 0:
        return None, "no_prior_close_bar"
    read = night_ranks.price_by(rows, symbol, until)
    if read is None or until - read[1] > timedelta(minutes=PRE_OPEN_STALE_MIN):
        return None, "no_bar_near_open"
    if not _same_contract(table, symbol, anchor[1], read[1]):
        return None, "contract_roll"
    return Stretch(read[0] - anchor[0], 100.0 * (read[0] / anchor[0] - 1.0), anchor[1], read[1]), ""


def _window_move(rows: list[dict], symbol: str, start: datetime, end: datetime) -> Stretch | None:
    """``symbol`` from its newest price by ``start`` to its newest price by ``end`` (spx_jev.night_ranks.price_by)."""
    night_ranks = station_stores.import_spx_jev("night_ranks")
    a, b = night_ranks.price_by(rows, symbol, start), night_ranks.price_by(rows, symbol, end)
    if a is None or b is None or a[0] <= 0:
        return None
    return Stretch(b[0] - a[0], 100.0 * (b[0] / a[0] - 1.0), a[1], b[1])


def _same_window(until: datetime) -> Callable[[date], tuple[datetime, datetime]]:
    """``day -> (prior close, the same clock as ``until`` or that day's open)``: the night's stretch on a prior night,
    for spx_jev.night_ranks.prior_window_nights."""
    clock = until.timetz()

    def window(day: date) -> tuple[datetime, datetime]:
        return _night_start(day), min(_session_open(day), datetime.combine(day, clock))
    return window


def _roll_pending(inputs: FrozenInputs, day: date, symbol: str, table: dict) -> str:
    """``"roll_pending"`` while Schwab quotes ``symbol`` under a contract the roll table has not rolled to (the newest
    save's quote by the cut, spx_jev.night_ranks.quoted_contract); empty otherwise."""
    night_ranks = station_stores.import_spx_jev("night_ranks")
    quoted = night_ranks.quoted_contract(inputs.state_dir, day, symbol, inputs.cut)
    return "roll_pending" if night_ranks.roll_pending(table, symbol, quoted) else ""


def _same_contract(table: dict, symbol: str, t1: datetime, t2: datetime) -> bool:
    return station_stores.import_spx_jev("rolls").same_contract(table, symbol, t1, t2)


def _night_start(day: date) -> datetime:
    """The prior session's close, where the night begins (spx_jev.story.night_start: 13:00 after a half day)."""
    return station_stores.import_spx_jev("story").night_start(day)


def _session_open(day: date) -> datetime:
    sessions = station_stores.import_spx_jev("sessions")
    return sessions.session_open(datetime.combine(day, time(12, 0), tzinfo=ET))


def _starts(row: dict) -> datetime:
    return datetime.fromisoformat(row["ts"])


def _ends(row: dict) -> datetime:
    return _starts(row) + timedelta(minutes=int(row["bar_minutes"]))


def _finished_by(row: dict, cut: datetime) -> bool:
    return isinstance(row.get("ts"), str) and isinstance(row.get("bar_minutes"), int) and _ends(row) <= cut


def _usable(nights: list) -> list[float]:
    """The prior nights' values that can stand for a normal night (no roll, holiday, thin night or missing price)."""
    return [n.value for n in nights if n.skip is None and n.value is not None]


def _size_ranked(value_sig: float | None, size_pct: float, base_pct: list[float]) -> dict | None:
    """``{"v": value in sig, "r": [...]}``: the rank of the move's size in percent against the prior nights'."""
    if value_sig is None:
        return None
    ranked = units.ranked(size_pct, base_pct)
    ranked["v"] = value_sig
    return ranked


__all__ = ["build_overnight_block"]
