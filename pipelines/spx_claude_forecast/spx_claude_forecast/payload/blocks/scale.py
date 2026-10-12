"""The scale block: the rulers this read's distances are measured in, and how much the day has moved
against what the options market priced.

Every distance in the payload is in sig, the day's expected move in points as the morning anchor set it
(``inputs.sigma_points``); ``sigma_src`` says where that anchor came from (the station's ``SigmaRuler``:
anchor, live or vix) and ``sigma_live_x`` how the live implied move compares with it. The at-the-money
implied volatility and the 0DTE straddle (what it priced at the open, what it still prices, how much of it
the day's range has used) come from the diary row and are the station's own numbers (``labels.rulers``,
``range_ruler``); on a stand-in options book (``inputs.options_book``, from the raw diary row) they are left
out, since a proxy chain prices a different thing, and a book that cannot be told counts as a stand-in.
$VIX1D is the newest quote known at the cut and its prior close the recorder's; neither exists before the
station began quoting it.

``realized_30m_vs_priced_x`` is the last 30 minutes' realized swing against the move the day's sigma
prices for 30 minutes:

    realized_points = sqrt( sum over the bars finished in (cut - 30 min, cut] of (close - previous close)^2 ),
                      the first difference taken from the close before the window (the open at the bell),
                      needing at least 25 finished bars (the station's ``labels.vol._realized_30``)
    priced_points   = sigma_points * sqrt(30 / 390)      (a full session is 390 minutes; no other factor)
    ratio           = realized_points / priced_points

so 1.0 means the tape delivered exactly the half hour the options priced, below it quieter, above it wilder.
"""
from __future__ import annotations

import math
from datetime import time

from ... import station_stores
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs

BOOK_FIELDS = ("atm_iv_pct", "straddle_open_sig", "straddle_left_sig", "straddle_used_x")   # priced off the options book
FIRST_QUOTE_AT = time(9, 31)      # the 09:30 read's quotes are still the prior close (the build plan's first-read rule)
STRADDLE_LEFT_DECIMALS = 3        # late in the day the straddle left is a few hundredths of a sig: two decimals lose it
IV_DECIMALS = 2                   # the row's at-the-money vol is a fraction (0.0844); shown as 8.44 percent


def build_scale_block(inputs: FrozenInputs) -> BlockResult:
    """``{sigma_src, sigma_live_x, atm_iv_pct, straddle_open_sig, straddle_left_sig, straddle_used_x, vix1d,
    vix1d_prior_close, realized_30m_vs_priced_x}``; each part declares its own absence."""
    result = BlockResult()
    data = {"sigma_src": _sigma_source(inputs, result), "sigma_live_x": _sigma_live_x(inputs, result)}
    book_absence = units.book_absence(inputs)
    if book_absence is None:
        data["atm_iv_pct"] = _atm_iv_pct(inputs, result)
        data.update(_straddle(inputs, result))
    else:
        for field in BOOK_FIELDS:
            result.leave_out(f"scale.{field}", book_absence)
    data["vix1d"] = _vix1d(inputs, result)
    data["vix1d_prior_close"] = _vix1d_prior_close(inputs, result)
    data["realized_30m_vs_priced_x"] = _realized_vs_priced(inputs, result)
    result.data = units.clean(data)
    return result


def _sigma_source(inputs: FrozenInputs, result: BlockResult) -> str | None:
    if inputs.anchor is None:
        result.leave_out("scale.sigma_src", "no_morning_ruler")
        return None
    return str(inputs.anchor.source)


def _sigma_live_x(inputs: FrozenInputs, result: BlockResult) -> float | None:
    """The live implied day move (the row's sigma_live, else spot times its ATM vol) over the morning anchor."""
    rulers = station_stores.import_spx_jev("labels.rulers")
    live = rulers.sigma_live(inputs.scene)
    if live is None:
        result.leave_out("scale.sigma_live_x", "not_recorded")
        return None
    return units.sig(live, inputs.sigma_points)


def _atm_iv_pct(inputs: FrozenInputs, result: BlockResult) -> float | None:
    iv = units.positive(inputs.scene.row.get("atm_iv"))
    if iv is None:
        result.leave_out("scale.atm_iv_pct", "not_recorded")
        return None
    return units.pct(iv * 100.0, IV_DECIMALS)


def _straddle(inputs: FrozenInputs, result: BlockResult) -> dict:
    """The 0DTE straddle at the open and what is left of it, in sig, and how much of it the day's range has
    used: the row's own ratio (``em_consumed``), else today's high-to-low range over the open straddle."""
    rulers = station_stores.import_spx_jev("labels.rulers")
    measures = station_stores.import_spx_jev("labels.measures")
    ruler = inputs.scene.row.get("range_ruler") or {}
    em_open = units.positive(ruler.get("em_open"))
    if em_open is None:
        for field in ("straddle_open_sig", "straddle_left_sig", "straddle_used_x"):
            result.leave_out(f"scale.{field}", "not_recorded")
        return {}
    left = rulers.straddle_left(inputs.scene)
    if left is None:
        result.leave_out("scale.straddle_left_sig", "not_recorded")
    used = ruler.get("em_consumed")
    if not units.is_num(used) and inputs.scene.bars:
        high, low = measures.day_high_low(inputs.scene.bars, inputs.spot)
        used = (high - low) / em_open
    if not units.is_num(used):
        result.leave_out("scale.straddle_used_x", "not_recorded")
        used = None
    return {"straddle_open_sig": units.sig(em_open, inputs.sigma_points),
            "straddle_left_sig": units.sig(left, inputs.sigma_points, STRADDLE_LEFT_DECIMALS),
            "straddle_used_x": None if used is None else round(float(used), units.MULTIPLE_DECIMALS)}


def _vix1d(inputs: FrozenInputs, result: BlockResult) -> float | None:
    """$VIX1D's newest quote known at the cut; none at the 09:30 read, where every quote is still the prior close."""
    if inputs.cut.time() < FIRST_QUOTE_AT:
        result.leave_out("scale.vix1d", f"not_until_{FIRST_QUOTE_AT:%H:%M}")
        return None
    quote = inputs.scene.market.last("$VIX1D", inputs.cut) if inputs.scene.market is not None else None
    if quote is None:
        result.leave_out("scale.vix1d", "no_vix1d_quote")
        return None
    return float(quote)


def _vix1d_prior_close(inputs: FrozenInputs, result: BlockResult) -> float | None:
    if inputs.vix1d_prior_close is None:
        result.leave_out("scale.vix1d_prior_close", "not_recorded")
        return None
    return float(inputs.vix1d_prior_close)


def _realized_vs_priced(inputs: FrozenInputs, result: BlockResult) -> float | None:
    """The module note's ratio: the station's own 30-minute realized swing (in sig of the anchor) over the
    square root of 30/390, so both sides are in the same unit."""
    vol = station_stores.import_spx_jev("labels.vol")
    realized_sig = vol._realized_30(inputs.scene.bars, inputs.cut, inputs.sigma_points)
    if realized_sig is None:
        result.leave_out("scale.realized_30m_vs_priced_x",
                         f"fewer_than_{vol.REALIZED_MIN_BARS}_finished_bars_in_{vol.REALIZED_WINDOW_MIN}m")
        return None
    priced_sig = math.sqrt(vol.REALIZED_WINDOW_MIN / vol.FULL_SESSION_MIN)
    return round(realized_sig / priced_sig, units.MULTIPLE_DECIMALS)
