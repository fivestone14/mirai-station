"""The tape block: where SPX's price sits and how it has moved today, read off the finished 1-minute bars
and the diary rows known at the cut.

Every distance is in sig (the read's expected daily move) and every size that has a same-minute history
is ranked against the prior sessions at this clock, through the station's own measures: the settled open
is the 09:34 close (measures.settled_open), a move is close to close (measures.close_at), the realized
swing is the station's 30-minute one (vol._realized_30), the open's crossings and path are the gap family's
(gap_open), the VWAP touch is the price family's (price._last_at_average). The day's VWAP is the median of
the last three diary rows' VWAP, so one bad scan cannot move it. Nothing here is a price level: a high or
a low rides as its distance from price and the clock it was made at.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any

from ... import station_stores
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs

BLOCK = "tape"
HALF_HOUR_MIN = 30
HOUR_MIN = 60
FIVE_MIN = 5
FIVE_MINUTE_MOVES = 6                # the last half hour step by step: six 5-minute close-to-close moves
FIVE_MINUTE_DECIMALS = 3             # a 5-minute move is a few hundredths of a sig; two decimals would flatten it
VWAP_ROWS = 3                        # the day's VWAP is the median of the last three rows', so one bad scan cannot move it
SETTLED_OPEN_REASON = "not_until_09:35"
NO_BARS_REASON = "no_finished_bars"


def build_tape_block(inputs: FrozenInputs) -> BlockResult:
    result = BlockResult()
    measures = station_stores.import_spx_jev("labels.measures")
    scene = inputs.scene
    bars = scene.bars
    prior_close = _positive(scene.row.get("prior_close"))
    settled_open = measures.settled_open(bars)
    prior_rows = _prior_rows_at_clock(scene)
    block: dict[str, Any] = {
        "price_minus_close_sig": _price_minus_close(inputs, prior_close, prior_rows, result),
        "price_minus_open_sig": _price_minus_open(inputs, settled_open, result),
        "m30_sig": _move(inputs, HALF_HOUR_MIN, result),
        "m60_sig": _move(inputs, HOUR_MIN, result),
    }
    block.update(_range_fields(inputs, result))
    block["five_session_pos"] = _five_session_pos(inputs, result)
    block["price_minus_vwap_sig"] = _price_minus_vwap(inputs, prior_rows, result)
    block["vwap_last_touch_min_ago"] = _vwap_last_touch(inputs, result)
    block.update(_high_low(inputs, result))
    block["prior_high"] = _prior_high(inputs, result)
    block["path_eff_since_open"] = _path_efficiency_since_open(inputs, settled_open, result)
    block["open_crosses"] = _open_crosses(inputs, settled_open, result)
    block["rv30_vs_clock_x"] = _realized_vs_clock(inputs, result)
    block["last_6x5m_sig"] = _last_five_minute_moves(inputs, result)
    block["half_hours_vs_close_sig"] = _half_hours_vs_close(inputs, prior_close, result)
    result.data = units.clean(block)
    return result


# --- price against its references ---------------------------------------------------------------------------

def _price_minus_close(inputs: FrozenInputs, prior_close: float | None, prior_rows: list, result: BlockResult) -> dict | None:
    """Spot against yesterday's close, its size ranked against the same distance on the prior sessions' rows at this minute."""
    if prior_close is None:
        result.leave_out(f"{BLOCK}.price_minus_close_sig", "no_prior_close")
        return None
    value = units.sig(inputs.spot - prior_close, inputs.sigma_points)
    return _ranked_by_size(value, _row_distances(prior_rows, "prior_close"))


def _price_minus_open(inputs: FrozenInputs, settled_open: float | None, result: BlockResult) -> dict | None:
    """Spot against the settled open, ranked against each prior session's distance from its own settled open at this minute."""
    if settled_open is None:
        result.leave_out(f"{BLOCK}.price_minus_open_sig", SETTLED_OPEN_REASON)
        return None
    measures = station_stores.import_spx_jev("labels.measures")

    def distance_from_open(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
        opened, close = measures.settled_open(bars), measures.close_at(bars, then)
        return abs(close - opened) / sigma if sigma and opened is not None and close is not None else None

    value = units.sig(inputs.spot - settled_open, inputs.sigma_points)
    return _ranked_by_size(value, units.same_clock_values(inputs.scene, distance_from_open))


def _move(inputs: FrozenInputs, minutes: int, result: BlockResult) -> dict | None:
    """The close-to-close move over the last ``minutes`` finished minutes, ranked by its size (measures.move_size)."""
    measures = station_stores.import_spx_jev("labels.measures")
    bars, cut = inputs.scene.bars, inputs.cut
    ref, last = measures.close_at(bars, cut - timedelta(minutes=minutes)), measures.close_at(bars, cut)
    if ref is None or last is None:
        result.leave_out(f"{BLOCK}.m{minutes}_sig", f"needs_{minutes}_min_of_bars")
        return None
    value = units.sig(last - ref, inputs.sigma_points)
    base = units.same_clock_values(inputs.scene, lambda b, then, sigma: measures.move_size(b, then, sigma, minutes))
    return _ranked_by_size(value, base)


# --- the day's range ----------------------------------------------------------------------------------------

def _range_fields(inputs: FrozenInputs, result: BlockResult) -> dict[str, Any]:
    """Today's high-low range so far (counting spot), ranked against the prior sessions' range by this minute,
    and where spot sits in it, 0 at the low and 1 at the high."""
    measures = station_stores.import_spx_jev("labels.measures")
    bars = inputs.scene.bars
    if not bars:
        result.leave_out(f"{BLOCK}.range_sig", NO_BARS_REASON)
        result.leave_out(f"{BLOCK}.range_pos", NO_BARS_REASON)
        return {}
    high, low = measures.day_high_low(bars, inputs.spot)
    base = units.same_clock_values(inputs.scene, lambda b, then, sigma: measures.stretch_range(b) / sigma if b and sigma else None)
    fields: dict[str, Any] = {"range_sig": units.ranked(units.sig(high - low, inputs.sigma_points), base)}
    if high > low:
        fields["range_pos"] = round((inputs.spot - low) / (high - low), 2)
    else:
        result.leave_out(f"{BLOCK}.range_pos", "zero_range")
    return fields


def _five_session_pos(inputs: FrozenInputs, result: BlockResult) -> float | None:
    """Where spot sits in the last five sessions' high-low range (the station's price.multi_day_position): 0 at
    their low, 1 at their high, past either in new ground."""
    measures = station_stores.import_spx_jev("labels.measures")
    week = list(inputs.scene.prior_bars.values())[:measures.RANGE_PRIOR_SESSIONS]
    if len(week) < measures.MIN_RANGE_SESSIONS:
        result.leave_out(f"{BLOCK}.five_session_pos", f"needs_{measures.MIN_RANGE_SESSIONS}_prior_sessions")
        return None
    high = max(max(float(b["high"]) for b in bars) for bars in week)
    low = min(min(float(b["low"]) for b in bars) for bars in week)
    if high <= low:
        result.leave_out(f"{BLOCK}.five_session_pos", "zero_range")
        return None
    return round((inputs.spot - low) / (high - low), 2)


# --- VWAP -----------------------------------------------------------------------------------------------------

def _price_minus_vwap(inputs: FrozenInputs, prior_rows: list, result: BlockResult) -> dict | None:
    """Spot against the day's VWAP (the median of the last VWAP_ROWS rows'), its size ranked against the same
    distance on the prior sessions' rows at this minute."""
    vwaps = [v for r in inputs.scene.rows_today if (v := _positive(r.get("vwap"))) is not None][-VWAP_ROWS:]
    if len(vwaps) < VWAP_ROWS:
        result.leave_out(f"{BLOCK}.price_minus_vwap_sig", "no_vwap")
        return None
    value = units.sig(inputs.spot - statistics.median(vwaps), inputs.sigma_points)
    return _ranked_by_size(value, _row_distances(prior_rows, "vwap"))


def _vwap_last_touch(inputs: FrozenInputs, result: BlockResult) -> int | None:
    """Minutes since the newest finished bar that spanned the VWAP the diary carried when it finished."""
    price = station_stores.import_spx_jev("labels.price")
    touched = price._last_at_average(inputs.scene)
    if touched is None:
        result.leave_out(f"{BLOCK}.vwap_last_touch_min_ago", "no_touch_today")
        return None
    return units.minutes_ago(touched, inputs.cut)


# --- the session's extremes and yesterday's high -------------------------------------------------------------

def _high_low(inputs: FrozenInputs, result: BlockResult) -> dict[str, Any]:
    """The session's high and low from the finished bars: each as its distance from price, the clock it was
    first made at (the finish of its bar) and how many minutes ago that was."""
    measures = station_stores.import_spx_jev("labels.measures")
    extremes = measures.session_extremes(inputs.scene.bars)
    if extremes is None:
        result.leave_out(f"{BLOCK}.high", NO_BARS_REASON)
        result.leave_out(f"{BLOCK}.low", NO_BARS_REASON)
        return {}
    return {"high": _extreme(inputs, extremes.high, extremes.high_at), "low": _extreme(inputs, extremes.low, extremes.low_at)}


def _extreme(inputs: FrozenInputs, level: float, made_at: datetime) -> dict:
    return {"minus_price_sig": units.sig(level - inputs.spot, inputs.sigma_points), "at": units.hhmm(made_at),
            "min_ago": units.minutes_ago(made_at, inputs.cut)}


def _prior_high(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """Yesterday's high (measures.yesterdays_bars, so an older session never stands in) as its distance from price,
    with how many finished minutes price has closed above it since it last closed at or below; 0 when spot is
    not above it."""
    measures = station_stores.import_spx_jev("labels.measures")
    _, yesterday, _ = measures.yesterdays_bars(inputs.scene)
    if yesterday is None:
        result.leave_out(f"{BLOCK}.prior_high", "no_prior_session_bars")
        return None
    high = max(float(b["high"]) for b in yesterday)
    above = 0
    if inputs.spot > high:
        for bar in reversed(inputs.scene.bars):
            if float(bar["close"]) <= high:
                break
            above += 1
    return {"minus_price_sig": units.sig(high - inputs.spot, inputs.sigma_points), "price_above_for_min": above}


# --- the path since the open ----------------------------------------------------------------------------------

def _path_efficiency_since_open(inputs: FrozenInputs, settled_open: float | None, result: BlockResult) -> float | None:
    """The net move from the settled open over the distance travelled close to close (measures.path_efficiency)."""
    if settled_open is None:
        result.leave_out(f"{BLOCK}.path_eff_since_open", SETTLED_OPEN_REASON)
        return None
    measures = station_stores.import_spx_jev("labels.measures")
    gap_open = station_stores.import_spx_jev("labels.gap_open")
    closes = gap_open.OpenSoFar(settled_open, gap_open._since_settled(inputs.scene)).closes
    efficiency = measures.path_efficiency(closes)
    if efficiency is None:
        result.leave_out(f"{BLOCK}.path_eff_since_open", "no_travel")
        return None
    return round(efficiency, 2)


def _open_crosses(inputs: FrozenInputs, settled_open: float | None, result: BlockResult) -> dict | None:
    """How many times the finished closes crossed the settled open (gap_open._crosses), ranked against the
    crossings to this minute on every prior session (a count no ruler scales, so none sits out)."""
    if settled_open is None:
        result.leave_out(f"{BLOCK}.open_crosses", SETTLED_OPEN_REASON)
        return None
    gap_open = station_stores.import_spx_jev("labels.gap_open")
    crosses = gap_open._crosses(gap_open._since_settled(inputs.scene), settled_open)
    base = [gap_open._crosses(opened.since, opened.settled_open) for opened in gap_open._prior_opens(inputs.scene)]
    return units.ranked(crosses, base)


def _realized_vs_clock(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The last 30 minutes' realized swing (vol._realized_30) as a multiple of the median of the same half hour on
    the prior sessions, with its rank among them."""
    vol = station_stores.import_spx_jev("labels.vol")
    cuts = station_stores.import_spx_jev("cuts")
    realized = vol._realized_30(inputs.scene.bars, inputs.cut, inputs.sigma_points)
    if realized is None:
        result.leave_out(f"{BLOCK}.rv30_vs_clock_x", f"needs_{vol.REALIZED_MIN_BARS}_bars_in_{vol.REALIZED_WINDOW_MIN}m")
        return None
    base = units.same_clock_values(inputs.scene, vol._realized_30)
    usual = statistics.median(base[:cuts.NIGHT_RANK_COUNT]) if base else 0.0
    if usual <= 0:
        result.leave_out(f"{BLOCK}.rv30_vs_clock_x", "no_prior_sessions_at_clock")
        return None
    return {**units.ranked(realized, base), "v": round(realized / usual, 2)}


def _last_five_minute_moves(inputs: FrozenInputs, result: BlockResult) -> list[float] | None:
    """The last FIVE_MINUTE_MOVES close-to-close moves of 5 minutes each, oldest first, ending at the cut."""
    measures = station_stores.import_spx_jev("labels.measures")
    marks = [inputs.cut - timedelta(minutes=FIVE_MIN * k) for k in range(FIVE_MINUTE_MOVES, -1, -1)]
    closes = [measures.close_at(inputs.scene.bars, mark) for mark in marks]
    if any(c is None for c in closes):
        result.leave_out(f"{BLOCK}.last_6x5m_sig", f"needs_{FIVE_MIN * FIVE_MINUTE_MOVES}_min_of_bars")
        return None
    return [units.sig(b - a, inputs.sigma_points, FIVE_MINUTE_DECIMALS) for a, b in zip(closes, closes[1:])]


def _half_hours_vs_close(inputs: FrozenInputs, prior_close: float | None, result: BlockResult) -> list[float] | None:
    """Where price stood against yesterday's close at each half-hour mark from 10:00 to the cut, in order."""
    if prior_close is None:
        result.leave_out(f"{BLOCK}.half_hours_vs_close_sig", "no_prior_close")
        return None
    measures = station_stores.import_spx_jev("labels.measures")
    marks, mark = [], inputs.scene.session_open + timedelta(minutes=HALF_HOUR_MIN)
    while mark <= inputs.cut:
        marks.append(mark)
        mark += timedelta(minutes=HALF_HOUR_MIN)
    closes = [c for mark in marks if (c := measures.close_at(inputs.scene.bars, mark)) is not None]
    if not closes:
        result.leave_out(f"{BLOCK}.half_hours_vs_close_sig", NO_BARS_REASON if marks else "not_until_10:00")
        return None
    return [units.sig(c - prior_close, inputs.sigma_points) for c in closes]


# --- shared ---------------------------------------------------------------------------------------------------

def _ranked_by_size(value: float | None, base: list[float]) -> dict | None:
    """units.ranked on the size of a signed move: the base is unsigned, the value keeps its sign."""
    if value is None:
        return None
    return {**units.ranked(abs(value), base), "v": value}


def _prior_rows_at_clock(scene: Any) -> list:
    """Each prior session's diary row at this minute with its ruler (gamma.prior_books): the base for the distances
    the diary carries (yesterday's close, VWAP), each measured from that row's own spot."""
    gamma = station_stores.import_spx_jev("labels.gamma")
    return gamma.prior_books(scene) or []


def _row_distances(prior_rows: list, key: str) -> list[float]:
    """``|spot - row[key]|`` in each prior row's own ruler, newest first; a row without the level or a ruler is skipped."""
    out = []
    for book in prior_rows:
        level, spot = _positive(book.row.get(key)), _positive(book.row.get("spot"))
        if book.ruler is not None and level is not None and spot is not None:
            out.append(abs(spot - level) / book.ruler.points)
    return out


def _positive(value: Any) -> float | None:
    """A positive number as a float, else None: a price the row may lack or carry as zero."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


__all__ = ["build_tape_block", "BLOCK"]
