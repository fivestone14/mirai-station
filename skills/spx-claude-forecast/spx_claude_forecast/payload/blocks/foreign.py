"""The foreign block, background only: Tokyo, Paris and Frankfurt against their prior closes, and the yen.

Built only from symbols the station's market context actually carries at the cut: the Nikkei ($N225), the
CAC 40 ($FCHI), the DAX ($DAX) and the yen future (/6J), which the context job does not quote yet (the plan's
recorder for them comes later), so on day one every field is absent as ``not_recorded`` and the block with it.
Once a symbol is quoted, its one-day return is its newest quote at the cut against its last quote on the prior
session's market context (the prior close as that session saw it: Tokyo and Europe have closed before New York
does, so the prior session's last quote is their prior close). ``europe_at`` is the clock the CAC 40's quote
last changed, Europe's close once it has closed. The yen future is dollars per yen, so USD/JPY is its inverse:
``usdjpy_1d_pct`` is the prior /6J over the current one less one, and ``usdjpy_shock`` flags a move past
USDJPY_SHOCK_PCT either way. Korea and Taiwan are declared for later (build_plan section 5).
"""
from __future__ import annotations

import bisect
from datetime import datetime, time
from typing import Any

from .. import units
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs, previous_session_day

BLOCK = "foreign"
TOKYO, PARIS, FRANKFURT, YEN_FUTURE = "$N225", "$FCHI", "$DAX", "/6J"
# Each return field and the symbol it is built from; the yen is inverted into USD/JPY (see the module docstring).
RETURNS = (("tokyo_ret_pct", TOKYO), ("europe_ret_pct", PARIS), ("dax_ret_pct", FRANKFURT), ("usdjpy_1d_pct", YEN_FUTURE))
INVERTED = (YEN_FUTURE,)
# A one-day USD/JPY move past this, either way, is a currency shock (build_plan section 5, "USD/JPY shock").
USDJPY_SHOCK_PCT = 1.5
# The prior session's last quote is read at its day's end, so a quote after the cash close still counts.
END_OF_DAY = time(23, 59, 59)
PCT_DECIMALS = 2
LATER_FIELD = "korea_taiwan"


def build_foreign_block(inputs: FrozenInputs) -> BlockResult:
    """``{tokyo_ret_pct, europe_ret_pct, dax_ret_pct, europe_at, usdjpy_1d_pct, usdjpy_shock}`` from the symbols
    quoted at the cut, each missing one absent as not_recorded; the whole block when none is."""
    market = inputs.scene.market
    quoted = {symbol: _newest(market, symbol, inputs.cut) for _, symbol in RETURNS} if market is not None else {}
    if not any(quoted.values()):
        return whole_block_absent(BLOCK, "not_recorded")
    prior_market = inputs.scene.prior_markets.get(previous_session_day(inputs.day))
    result = BlockResult()
    for field, symbol in RETURNS:
        newest = quoted[symbol]
        if newest is None:
            result.leave_out(f"{BLOCK}.{field}", "not_recorded")
            continue
        prior = _prior_session_last(prior_market, symbol)
        if prior is None or prior <= 0 or newest[1] <= 0:
            result.leave_out(f"{BLOCK}.{field}", "no_prior_session_quote")
            continue
        change = prior / newest[1] - 1.0 if symbol in INVERTED else newest[1] / prior - 1.0
        result.data[field] = round(100.0 * change, PCT_DECIMALS)
    if quoted[PARIS] is not None:
        result.data["europe_at"] = units.hhmm(_last_changed(market, PARIS, inputs.cut))
    if "usdjpy_1d_pct" in result.data:
        result.data["usdjpy_shock"] = abs(result.data["usdjpy_1d_pct"]) > USDJPY_SHOCK_PCT
    result.leave_out(f"{BLOCK}.{LATER_FIELD}", "include_later")
    result.data = units.clean(result.data)
    return result


def _newest(market: Any, symbol: str, at: datetime) -> tuple[datetime, float] | None:
    points = market.known.get(symbol) or []
    k = bisect.bisect_right([t for t, _ in points], at)
    return (points[k - 1][0], float(points[k - 1][1])) if k else None


def _prior_session_last(prior_market: Any, symbol: str) -> float | None:
    """The symbol's last quote on the prior session's market context, read at that day's end."""
    if prior_market is None:
        return None
    points = prior_market.known.get(symbol) or []
    if not points:
        return None
    day_end = datetime.combine(points[-1][0].date(), END_OF_DAY, tzinfo=points[-1][0].tzinfo)
    value = prior_market.last(symbol, day_end)
    return None if value is None else float(value)


def _last_changed(market: Any, symbol: str, at: datetime) -> datetime:
    """When the newest value at ``at`` first appeared: a closed market's quote repeats, so this is its close."""
    points = market.known.get(symbol) or []
    k = bisect.bisect_right([t for t, _ in points], at) - 1
    while k > 0 and points[k - 1][1] == points[k][1]:
        k -= 1
    return points[k][0]


__all__ = ["build_foreign_block"]
