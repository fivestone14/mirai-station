"""The open-signals block: what this morning's open said, kept for the whole day.

The gap is named two ways, as the station names it: the settled gap (the 09:34 close against yesterday's
close, the one the station ranks and judges) and the print gap (the 09:30 first print against yesterday's
close, the one the daily history knows). The fill base rate comes from the saved daily closes for the days
before this one: a day's print gap is its open against the prior close, and it filled when the day's range
reached back to that close; today's gap is read against the days whose print gap was the same size. How much
of the settled gap price has kept (``kept_x``: 1.0 at the settled open, 0 back at yesterday's close, more
than 1.0 extended) is given only for a gap past the station's touch tolerance of yesterday's close
(cuts.GAP_TOUCH_SIGMA): inside it there is no gap to keep, and a share of a hair's width would read as a
price level. The noise band is the average move from the 09:30 open to this minute over the prior
NOISE_PRIOR_SESSIONS sessions, as a share of the open, laid either side of today's open. The opening option
imbalance is the lob-flow collector's own fold of the 0DTE tape (state/lob_flow/agg, never deleted, read
through the flow block's reader) as written at 09:40, when its 15-minute window holds exactly the first
ten minutes of trading.
"""
from __future__ import annotations

import statistics
from datetime import time, timedelta
from typing import Any

from ... import station_stores
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs
from .flow import tape_readings

BLOCK = "open_signals"
# |print gap| in percent of the prior close: the buckets the fill base rate is read in, so a 0.1% gap is never
# judged by what 1% gaps did
GAP_BUCKETS_PCT = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.8), (0.8, float("inf")))
GAP_BASE_ON = "print_gap_size"
PCT_DECIMALS = 2                     # a gap in percent: 0.35, not 0.3
STRADDLE_DECIMALS = 1
KEPT_DECIMALS = 2
# The noise-area rule (Zarattini, Aziz and Barbon) averages the move from the open over the last 14 sessions.
NOISE_PRIOR_SESSIONS = 14
NOISE_DECIMALS = 3                   # the band is a few tenths of a percent
BP_DECIMALS = 1
OPEN_BASIS = "0930_bar"
OPENING_TAPE_MIN = 10                # the first ten minutes of the 0DTE tape: the opening imbalance
TILT_DECIMALS = 3


def build_open_signals_block(inputs: FrozenInputs) -> BlockResult:
    result = BlockResult()
    block: dict[str, Any] = {
        "gap": _gap(inputs, result),
        "noise_band": _noise_band(inputs, result),
        "opt_imbalance_10m": _opening_imbalance(inputs, result),
    }
    result.data = units.clean(block)
    return result


# --- the gap --------------------------------------------------------------------------------------------------

def _gap(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    measures = station_stores.import_spx_jev("labels.measures")
    gap_open = station_stores.import_spx_jev("labels.gap_open")
    scene, spot = inputs.scene, inputs.spot
    prior_close = units.positive(scene.row.get("prior_close"))
    if prior_close is None:
        result.leave_out(f"{BLOCK}.gap", "no_prior_close")
        return None
    settled_open = measures.settled_open(scene.bars)
    if settled_open is None:
        result.leave_out(f"{BLOCK}.gap", units.NOT_UNTIL_SETTLED_OPEN)
        return None
    settled_gap = settled_open - prior_close
    high, low = measures.day_high_low(scene.bars, spot)
    gap: dict[str, Any] = {
        "settled_sig": units.ranked_sig(settled_gap, inputs.sigma_points, gap_open.prior_gap_sizes(scene)),
        "settled_pct": round((settled_open / prior_close - 1.0) * 100.0, PCT_DECIMALS),
        "print_pct": None,
        "x_straddle": _times_straddle(inputs, abs(settled_gap), result),
        "filled": low <= prior_close <= high,
        "kept_x": _kept(inputs, settled_gap, prior_close, result),
    }
    print_open = _opening_print(inputs)
    if print_open is None:
        for field in ("print_pct", "fill_by_close_base_pct", "fill_base_n"):
            result.leave_out(f"{BLOCK}.gap.{field}", "no_0930_bar")
    else:
        gap["print_pct"] = round((print_open / prior_close - 1.0) * 100.0, PCT_DECIMALS)
        gap.update(_fill_base_rate(inputs, abs(print_open / prior_close - 1.0) * 100.0, result))
    gap["base_on"] = GAP_BASE_ON
    return units.clean(gap)


def _kept(inputs: FrozenInputs, settled_gap: float, prior_close: float, result: BlockResult) -> float | None:
    """Spot's distance from yesterday's close as a multiple of the settled gap (the module note); none for a gap
    within the station's touch tolerance of that close."""
    cuts = station_stores.import_spx_jev("cuts")
    if abs(settled_gap) < cuts.GAP_TOUCH_SIGMA * inputs.sigma_points:
        result.leave_out(f"{BLOCK}.gap.kept_x", "gap_within_touch_tolerance")
        return None
    return round((inputs.spot - prior_close) / settled_gap, KEPT_DECIMALS)


def _times_straddle(inputs: FrozenInputs, gap_points: float, result: BlockResult) -> float | None:
    """The gap over the morning's same-day straddle: the first row's ``em_open`` (what the 0DTE book priced before
    the gap was known), else the first row's ``em_points``."""
    rows = inputs.scene.rows_today
    straddle = next((em for r in rows if (em := units.positive((r.get("range_ruler") or {}).get("em_open"))) is not None), None)
    if straddle is None and rows:
        straddle = units.positive((rows[0].get("range_ruler") or {}).get("em_points"))
    if straddle is None:
        result.leave_out(f"{BLOCK}.gap.x_straddle", "no_straddle")
        return None
    return round(gap_points / straddle, STRADDLE_DECIMALS)


def _opening_print(inputs: FrozenInputs) -> float | None:
    """The 09:30 bar's open, the day's first print; None until that bar has finished."""
    measures = station_stores.import_spx_jev("labels.measures")
    bars = inputs.scene.bars
    if not bars or measures.bar_time(bars[0]) != inputs.scene.session_open:
        return None
    return units.positive(bars[0].get("open"))


def _fill_base_rate(inputs: FrozenInputs, print_gap_pct: float, result: BlockResult) -> dict[str, int]:
    """How often, on the saved days before this one whose print gap was the size of today's, the day's range
    reached back to the prior close: the rate in percent and the count of days behind it."""
    days = sorted((d for d in inputs.daily_closes.get("$SPX", []) if isinstance(d.get("day"), str)), key=lambda d: d["day"])
    if not days:
        for field in ("fill_by_close_base_pct", "fill_base_n"):
            result.leave_out(f"{BLOCK}.gap.{field}", "no_daily_closes")
        return {}
    low, high = _gap_bucket(print_gap_pct)
    filled = n = 0
    for before, day in zip(days, days[1:]):
        prior_close = units.positive(before.get("close"))
        opened, day_high, day_low = (units.positive(day.get(k)) for k in ("open", "high", "low"))
        if None in (prior_close, opened, day_high, day_low):
            continue
        if not low <= abs(opened / prior_close - 1.0) * 100.0 < high:
            continue
        n += 1
        filled += day_low <= prior_close <= day_high
    if not n:
        for field in ("fill_by_close_base_pct", "fill_base_n"):
            result.leave_out(f"{BLOCK}.gap.{field}", "no_days_in_bucket")
        return {}
    return {"fill_by_close_base_pct": round(filled / n * 100.0), "fill_base_n": n}


def _gap_bucket(gap_pct: float) -> tuple[float, float]:
    return next(bucket for bucket in GAP_BUCKETS_PCT if bucket[0] <= gap_pct < bucket[1])


# --- the noise band -------------------------------------------------------------------------------------------

def _noise_band(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The average unsigned move from the 09:30 open to this minute over the prior NOISE_PRIOR_SESSIONS sessions
    (percent of the open), which side of today's open +- that band spot sits on, and how far past the band in
    basis points: positive beyond it, negative inside it (the distance to the nearer edge)."""
    cuts = station_stores.import_spx_jev("cuts")
    opened = _opening_print(inputs)
    if opened is None:
        result.leave_out(f"{BLOCK}.noise_band", "no_0930_bar")
        return None
    sessions = list(inputs.scene.prior_bars.values())[:NOISE_PRIOR_SESSIONS]
    moves = [m for bars in sessions if (m := _move_from_open_pct(bars, inputs.cut.timetz())) is not None]
    if len(moves) < cuts.MIN_RANK_SESSIONS:
        result.leave_out(f"{BLOCK}.noise_band", f"needs_{cuts.MIN_RANK_SESSIONS}_prior_sessions")
        return None
    band_pct = statistics.fmean(moves)
    move_pct = (inputs.spot / opened - 1.0) * 100.0
    if move_pct > band_pct:
        side, past_pct = "above", move_pct - band_pct
    elif move_pct < -band_pct:
        side, past_pct = "below", -band_pct - move_pct
    else:
        side, past_pct = "inside", -min(band_pct - move_pct, move_pct + band_pct)
    return {"band_pct": round(band_pct, NOISE_DECIMALS), "side": side, "dist_bp": round(past_pct * 100.0, BP_DECIMALS),
            "open_basis": OPEN_BASIS}


def _move_from_open_pct(bars: list[dict], clock: time) -> float | None:
    """A prior session's unsigned move from its 09:30 bar's open to ``clock`` on that day, in percent of the open;
    None when its first bar is not the 09:30 bar."""
    measures = station_stores.import_spx_jev("labels.measures")
    sessions = station_stores.import_spx_jev("sessions")
    if not bars:
        return None
    opened_at = measures.bar_time(bars[0])
    if opened_at.time() != sessions.SESSION_OPEN:
        return None
    then = opened_at.replace(hour=clock.hour, minute=clock.minute, second=clock.second, microsecond=clock.microsecond)
    opened, close = units.positive(bars[0].get("open")), measures.close_at(bars, then)
    return abs(close / opened - 1.0) * 100.0 if opened is not None and close is not None else None


# --- the opening option imbalance -----------------------------------------------------------------------------

def _opening_imbalance(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The collector's signed tilt of the 0DTE tape as folded at 09:40 (the newest reading written by then), the
    trades it counted, and whether that fold was fresh (within the station's tape age limit, with a side told for
    some of the flow)."""
    state_builder = station_stores.import_spx_jev("state_builder")
    at = inputs.scene.session_open + timedelta(minutes=OPENING_TAPE_MIN)
    if inputs.cut < at:
        result.leave_out(f"{BLOCK}.opt_imbalance_10m", f"not_until_{at:%H:%M}")
        return None
    readings = [r for r in tape_readings(inputs, inputs.day) if r.at <= at and r.tilt is not None]
    if not readings:
        result.leave_out(f"{BLOCK}.opt_imbalance_10m", "tape_missing")
        return None
    newest = readings[-1]
    fresh = at - newest.at <= timedelta(minutes=state_builder.OPTIONS_TAPE_MAX_AGE_MIN)
    told = newest.told_share is not None and newest.told_share > 0
    return {"tilt": round(newest.tilt, TILT_DECIMALS), "trades": newest.trades, "feed_ok": bool(fresh and told)}


__all__ = ["build_open_signals_block", "BLOCK"]
