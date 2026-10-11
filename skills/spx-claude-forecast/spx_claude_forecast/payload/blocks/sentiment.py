"""The sentiment block: mood checks from the markets around the S&P, each a move since the prior close with its
size ranked against the same minute on the prior sessions.

Read from the market context at the cut, the symbols the context job has quoted since 2026-10-12
(spx_jev.market_context.SYMBOLS["sentiment"]): the Nikkei futures (/NKD, which trade through this session while the
Tokyo index stands still), the Cboe SKEW index ($SKEW, what crash protection costs), the dollar fund (UUP), the
two-year note future (/ZT), copper (/HG), the Nasdaq volatility index ($VXN) and ARKK (speculative appetite, also
set against SPY so the S&P's own move is taken out). A quote must be fresh (units.QUOTE_MAX_AGE_MIN) or the field
is ``stale``; a symbol never quoted by the cut is ``not_quoted``; the whole block is absent as ``not_recorded``
when none of them is quoted (every session before 2026-10-12).

The base of a move is Schwab's own prior close for the symbol as the day's snapshots state it
(``inputs.snapshot_prior_closes``), else the prior session's last quote on file; on the prior sessions, whose
snapshots are not kept as such, it is the session before's last quote (units.prior_session_last_quote, as the
foreign block reads one). Moves are percent of the base, or basis points of it for the note, whose PRICE moves in
hundredths: the field says price, since a note price down is its yield up. Ranks are units.size_ranked on the
move as measured: ``r`` of a signed ``v`` says how many sessions moved less, either way. The two levels (the SKEW
index and VXN) ride as they are, ranked against their same-clock values, the SKEW index with its change since the
prior close and VXN less the VIX (tech fear over broad fear).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ... import station_stores
from .. import units
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs, previous_session_day

BLOCK = "sentiment"
NIKKEI_FUTURES, SKEW, DOLLAR_FUND, TWO_YEAR_NOTE, COPPER, NASDAQ_VOL, ARKK, SPY, VIX = (
    "/NKD", "$SKEW", "UUP", "/ZT", "/HG", "$VXN", "ARKK", "SPY", "$VIX")
# Each move field, its symbol and the unit the move is written in
MOVES = (("nikkei_futures_pct_since_close", NIKKEI_FUTURES, "pct"), ("dollar_pct_since_close", DOLLAR_FUND, "pct"),
         ("two_year_note_price_bp_since_close", TWO_YEAR_NOTE, "bp"), ("copper_pct_since_close", COPPER, "pct"),
         ("arkk_pct_since_close", ARKK, "pct"))
ARKK_MINUS_SPY = "arkk_minus_spy_pct_since_close"
LEVELS = (("skew_index", SKEW), ("vxn", NASDAQ_VOL))
SKEW_MINUS_PRIOR_CLOSE, VXN_MINUS_VIX = "skew_index_minus_prior_close", "vxn_minus_vix"
UNIT_SCALE = {"pct": 100.0, "bp": 10_000.0}
UNIT_DECIMALS = {"pct": 2, "bp": 1}
LEVEL_DECIMALS = 2


def build_sentiment_block(inputs: FrozenInputs) -> BlockResult:
    market = inputs.scene.market
    symbols = [symbol for _, symbol, _ in MOVES] + [symbol for _, symbol in LEVELS]
    if market is None or not any(market.known.get(symbol) for symbol in symbols):
        return whole_block_absent(BLOCK, "not_recorded")
    result = BlockResult()
    ranks = station_stores.import_spx_jev("labels.ranks")
    for field, symbol, unit in MOVES:
        move = _move_since_close(market, symbol, inputs.cut, _todays_base(inputs, symbol))
        if isinstance(move, str):
            result.leave_out(f"{BLOCK}.{field}", move)
            continue
        base = ranks.same_clock_market(inputs.scene, lambda prior, then, symbol=symbol: _prior_move(inputs, prior, then, symbol))
        result.data[field] = _size_ranked_in(unit, move, base)
    _arkk_against_spy(inputs, market, ranks, result)
    levels: dict[str, float] = {}
    for field, symbol in LEVELS:
        level = _fresh_quote(market, symbol, inputs.cut)
        if isinstance(level, str):
            result.leave_out(f"{BLOCK}.{field}", level)
            continue
        base = ranks.same_clock_market(inputs.scene, lambda prior, then, symbol=symbol: prior.last(symbol, then, max_age_min=units.QUOTE_MAX_AGE_MIN))
        result.data[field] = units.ranked(level, base, decimals=LEVEL_DECIMALS)
        levels[symbol] = level
    _skew_against_prior_close(inputs, levels.get(SKEW), result)
    _vxn_against_vix(inputs, market, levels.get(NASDAQ_VOL), result)
    result.data = units.clean(result.data)
    return result


def _arkk_against_spy(inputs: FrozenInputs, market: Any, ranks: Any, result: BlockResult) -> None:
    """ARKK's move less SPY's, in percent: the appetite for the speculative end with the S&P's own move taken out."""
    arkk = _move_since_close(market, ARKK, inputs.cut, _todays_base(inputs, ARKK))
    spy = _move_since_close(market, SPY, inputs.cut, _todays_base(inputs, SPY))
    if isinstance(arkk, str) or isinstance(spy, str):
        result.leave_out(f"{BLOCK}.{ARKK_MINUS_SPY}", arkk if isinstance(arkk, str) else spy)
        return

    def prior_spread(prior: Any, then: datetime) -> float | None:
        a, s = _prior_move(inputs, prior, then, ARKK), _prior_move(inputs, prior, then, SPY)
        return None if a is None or s is None else a - s

    result.data[ARKK_MINUS_SPY] = _size_ranked_in("pct", arkk - spy, ranks.same_clock_market(inputs.scene, prior_spread))


def _skew_against_prior_close(inputs: FrozenInputs, level: float | None, result: BlockResult) -> None:
    """The SKEW index less its prior close: whether crash protection got dearer today."""
    if level is None:
        return
    base = _todays_base(inputs, SKEW)
    if base is None:
        result.leave_out(f"{BLOCK}.{SKEW_MINUS_PRIOR_CLOSE}", "no_prior_close")
        return
    result.data[SKEW_MINUS_PRIOR_CLOSE] = round(level - base, LEVEL_DECIMALS)


def _vxn_against_vix(inputs: FrozenInputs, market: Any, vxn: float | None, result: BlockResult) -> None:
    """VXN less the VIX: tech fear over broad fear."""
    if vxn is None:
        return
    vix = _fresh_quote(market, VIX, inputs.cut)
    if isinstance(vix, str):
        result.leave_out(f"{BLOCK}.{VXN_MINUS_VIX}", vix)
        return
    result.data[VXN_MINUS_VIX] = round(vxn - vix, LEVEL_DECIMALS)


# --- moves and their bases --------------------------------------------------------------------------------------

def _fresh_quote(market: Any, symbol: str, at: datetime) -> float | str:
    """The quote at ``at`` no older than units.QUOTE_MAX_AGE_MIN; else why not: not_quoted or stale."""
    quote = market.last(symbol, at, max_age_min=units.QUOTE_MAX_AGE_MIN)
    if quote is not None:
        return float(quote)
    return "not_quoted" if market.last(symbol, at) is None else "stale"


def _move_since_close(market: Any, symbol: str, at: datetime, base: float | None) -> float | str:
    """The fresh quote at ``at`` over ``base`` less one, as a fraction; else why not: not_quoted, stale, no_prior_close."""
    quote = _fresh_quote(market, symbol, at)
    if isinstance(quote, str):
        return quote
    if base is None or base <= 0:
        return "no_prior_close"
    return quote / base - 1.0


def _todays_base(inputs: FrozenInputs, symbol: str) -> float | None:
    """Schwab's prior close for the symbol from the day's snapshots, else the prior session's last quote on file."""
    prior_close = inputs.snapshot_prior_closes.get(symbol)
    if prior_close:
        return float(prior_close)
    return units.prior_session_last_quote(inputs.scene.prior_markets.get(previous_session_day(inputs.day)), symbol)


def _prior_move(inputs: FrozenInputs, prior_market: Any, then: datetime, symbol: str) -> float | None:
    """The same move at ``then`` on a prior session, based on the session before's last quote; None when either side
    is missing."""
    before = inputs.scene.prior_markets.get(previous_session_day(then.date().isoformat()))
    move = _move_since_close(prior_market, symbol, then, units.prior_session_last_quote(before, symbol))
    return None if isinstance(move, str) else move


def _size_ranked_in(unit: str, move: float, base: list[float]) -> dict:
    """The move and its base in the field's unit, ranked by size as measured and shown to the unit's decimals."""
    scale = UNIT_SCALE[unit]
    return units.size_ranked(move * scale, [b * scale for b in base], decimals=UNIT_DECIMALS[unit])


__all__ = ["build_sentiment_block"]
