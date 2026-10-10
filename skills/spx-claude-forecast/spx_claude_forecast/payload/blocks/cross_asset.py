"""The cross-asset block: the VIX and where it sits against its own curve, and the ten-year yield since the
prior close.

Every quote is read from the market context at the cut (the newest snapshot at or before it) and must be no
older than QUOTE_MAX_AGE_MIN, else the feed had stopped. The ten-year yield is judged at min(cut, TREASURY_CASH_CLOSE):
Schwab's $TNX quote stops moving at 15:00 ET, so an afternoon read takes the 15:00 value and is not called stale for it.

Units pinned here:

* the context carries $TNX as the yield in percent (spx_jev.state_builder.context_value divides Schwab's
  ten-times quote, 52.50 -> 5.250), while ``daily_closes/$TNX.jsonl`` keeps Schwab's own ten-times index
  (52.31); ``ten_year_bp_since_close`` is (percent now - prior close / 10) x 100, so 5.250 against 52.31 is
  +1.9 bp;
* ``vix_vix3m`` is the diary row's ``vix_ts``, which is VIX over the three-month VIX (spx_jev.labels.vol
  ``iv.term_structure``), else the same ratio from the $VIX and $VIX3M quotes; ``backwardated`` is that
  ratio above BACKWARDATION_LINE;
* the prior VIX close is the daily close on file for the previous trading day; the prior $VIX3M close has no
  daily file, so it is Schwab's own ``close`` field as the newest context snapshot by the cut quoted it
  (``inputs.snapshot_prior_closes``), the same field the daily closes mirror for $VIX.

At the open the VIX family's quotes still read at the prior close until 09:31 or 09:32 (every open on file), so
a quote equal to its prior close before OPEN_QUOTES_SETTLE is not a value yet and the ratios built from it are
absent. Ranks are of the size of the move against the same minute on the prior sessions with a market context:
``r`` of a signed ``v`` says how many sessions moved less, either way. The VIX futures curve is always absent:
Schwab refuses the symbols (the design review's data-access check).
"""
from __future__ import annotations

import bisect
from datetime import datetime, time
from typing import Any

from ... import station_stores
from .. import units
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs, previous_session_day

BLOCK = "cross_asset"
VIX, VIX3M, VIX9D, TEN_YEAR = "$VIX", "$VIX3M", "$VIX9D", "$TNX"
# The context job snapshots about every 70 s; a quote older than this at the cut is a feed that stopped
# (spx_jev.labels.vol.QUOTE_MAX_AGE_MIN).
QUOTE_MAX_AGE_MIN = 5
# Schwab's $TNX quote stops moving at the Treasury cash close, so freshness is judged there in the afternoon.
TREASURY_CASH_CLOSE = time(15, 0)
# The VIX family's quotes read at the prior close until 09:31-09:32 on every open on file (build_plan, special reads);
# before this a quote equal to its prior close is still the prior close.
OPEN_QUOTES_SETTLE = time(9, 35)
# VIX at or above the three-month VIX: near-term fear priced above longer-term, the stressed shape
# (the same line as spx_jev.cuts.VIX_CURVE_FLAT).
BACKWARDATION_LINE = 1.0
# Schwab divides the yield index by ten for the context; the daily closes keep the index as served.
DAILY_CLOSE_TNX_PER_PERCENT = 10.0
BP_PER_PERCENT = 100.0
RATIO_DECIMALS = 3
LEVEL_DECIMALS = 2
BP_DECIMALS = 1


def build_cross_asset_block(inputs: FrozenInputs) -> BlockResult:
    """``{vix, vix_minus_prior_close, vix_vix3m, vix_vix3m_prior_close, backwardated, vix9d_vix,
    ten_year_bp_since_close}``, each left out with its reason when its quote or prior close is missing."""
    market = inputs.scene.market
    if market is None:
        result = whole_block_absent(BLOCK, "no_market_context")
        result.leave_out(f"{BLOCK}.vx_futures", "schwab_refuses_symbol")
        return result
    result = BlockResult()
    prior_vix = _prior_daily_close(inputs, VIX)
    vix, why = _fresh_quote(market, VIX, inputs.cut, prior_vix)
    if vix is None:
        for field in ("vix", "vix_minus_prior_close", "vix_vix3m", "backwardated", "vix9d_vix"):
            result.leave_out(f"{BLOCK}.{field}", why)
    else:
        result.data["vix"] = round(vix, LEVEL_DECIMALS)
        if prior_vix is None:
            result.leave_out(f"{BLOCK}.vix_minus_prior_close", "prior_close_missing")
        else:
            result.data["vix_minus_prior_close"] = round(vix - prior_vix, LEVEL_DECIMALS)
        _term_structure(inputs, market, vix, result)
        _front_ratio(inputs, market, vix, result)
    prior_vix3m = inputs.snapshot_prior_closes.get(VIX3M)
    if prior_vix is None or prior_vix3m is None:
        result.leave_out(f"{BLOCK}.vix_vix3m_prior_close", "prior_close_missing")
    else:
        result.data["vix_vix3m_prior_close"] = round(prior_vix / prior_vix3m, RATIO_DECIMALS)
    _ten_year(inputs, market, result)
    result.leave_out(f"{BLOCK}.vx_futures", "schwab_refuses_symbol")
    result.data = units.clean(result.data)
    return result


# --- the VIX curve ----------------------------------------------------------------------------------------

def _term_structure(inputs: FrozenInputs, market: Any, vix: float, result: BlockResult) -> None:
    """VIX over the three-month VIX: the diary row's own ratio once the $VIX3M quote has settled, else the quotes'."""
    vix3m, why = _fresh_quote(market, VIX3M, inputs.cut, inputs.snapshot_prior_closes.get(VIX3M))
    if vix3m is None:
        result.leave_out(f"{BLOCK}.vix_vix3m", why)
        result.leave_out(f"{BLOCK}.backwardated", why)
        return
    row_ratio = inputs.scene.row.get("vix_ts")
    ratio = float(row_ratio) if isinstance(row_ratio, (int, float)) and row_ratio > 0 else vix / vix3m
    result.data["vix_vix3m"] = round(ratio, RATIO_DECIMALS)
    result.data["backwardated"] = ratio > BACKWARDATION_LINE


def _front_ratio(inputs: FrozenInputs, market: Any, vix: float, result: BlockResult) -> None:
    vix9d, why = _fresh_quote(market, VIX9D, inputs.cut, _prior_daily_close(inputs, VIX9D))
    if vix9d is None:
        result.leave_out(f"{BLOCK}.vix9d_vix", why)
    else:
        result.data["vix9d_vix"] = round(vix9d / vix, RATIO_DECIMALS)


# --- the ten-year yield -----------------------------------------------------------------------------------

def _ten_year(inputs: FrozenInputs, market: Any, result: BlockResult) -> None:
    """The yield's move since the prior close in basis points, its size ranked against the same move at this clock
    on the prior sessions."""
    field = f"{BLOCK}.ten_year_bp_since_close"
    prior_close = _prior_daily_close(inputs, TEN_YEAR)
    if prior_close is None:
        result.leave_out(field, "prior_close_missing")
        return
    change = _ten_year_change(market, inputs.cut, prior_close)
    if change is None:
        result.leave_out(field, "not_quoted" if _newest(market, TEN_YEAR, inputs.cut) is None else "stale")
        return
    closes = _daily_closes_by_day(inputs, TEN_YEAR)

    def same_clock(prior_market: Any, then: datetime) -> float | None:
        before = closes.get(previous_session_day(then.date().isoformat()))
        return None if before is None else _ten_year_change(prior_market, then, before)

    ranks = station_stores.import_spx_jev("labels.ranks")
    result.data["ten_year_bp_since_close"] = _size_ranked(round(change, BP_DECIMALS),
                                                          ranks.same_clock_market(inputs.scene, same_clock))


def _ten_year_change(market: Any, at: datetime, prior_close_index: float) -> float | None:
    """Basis points from the prior close (Schwab's ten-times index) to the fresh yield in percent at ``at``, judged
    at the Treasury cash close once ``at`` is past it."""
    judged_at = min(at, at.replace(hour=TREASURY_CASH_CLOSE.hour, minute=TREASURY_CASH_CLOSE.minute, second=0, microsecond=0))
    yield_pct = market.last(TEN_YEAR, judged_at, max_age_min=QUOTE_MAX_AGE_MIN)
    if yield_pct is None:
        return None
    return (float(yield_pct) - prior_close_index / DAILY_CLOSE_TNX_PER_PERCENT) * BP_PER_PERCENT


# --- shared reads -----------------------------------------------------------------------------------------

def _newest(market: Any, symbol: str, at: datetime) -> tuple[datetime, float] | None:
    """The newest ``(known at, value)`` of ``symbol`` at or before ``at``."""
    points = market.known.get(symbol) or []
    k = bisect.bisect_right([t for t, _ in points], at)
    return points[k - 1] if k else None


def _fresh_quote(market: Any, symbol: str, cut: datetime, prior_close: float | None) -> tuple[float | None, str]:
    """``(value, "")`` for a quote at the cut that is fresh and has moved off its prior close since the open; else
    ``(None, why)``: not_quoted, stale, or stale_at_open."""
    newest = _newest(market, symbol, cut)
    if newest is None:
        return None, "not_quoted"
    known_at, value = newest
    if (cut - known_at).total_seconds() > QUOTE_MAX_AGE_MIN * 60:
        return None, "stale"
    if prior_close is not None and value == prior_close and known_at.time() < OPEN_QUOTES_SETTLE:
        return None, "stale_at_open"
    return float(value), ""


def _daily_closes_by_day(inputs: FrozenInputs, symbol: str) -> dict[str, float]:
    return {str(r["day"]): float(r["close"]) for r in inputs.daily_closes.get(symbol) or []
            if isinstance(r.get("close"), (int, float)) and r.get("day")}


def _prior_daily_close(inputs: FrozenInputs, symbol: str) -> float | None:
    """The daily close on file for the previous trading day, as served; None when the file lags."""
    return _daily_closes_by_day(inputs, symbol).get(previous_session_day(inputs.day))


def _size_ranked(value: float, base: list[float]) -> dict:
    """``{"v": value, "r": [...]}`` with the rank of the move's size against the sizes in ``base``."""
    ranked = units.ranked(abs(value), [abs(b) for b in base])
    ranked["v"] = value
    return ranked


__all__ = ["build_cross_asset_block"]
