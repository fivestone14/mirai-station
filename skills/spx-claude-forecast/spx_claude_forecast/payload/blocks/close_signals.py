"""The close-signals block: the returns the close is known to lean on, and the dealer gamma the day opened under.

r1 is the first half hour's return, from the settled open (the 09:34 close) to the close known at 10:00, in
basis points; r12 (15:00 to 15:30) and rROD (yesterday's close to 15:30) exist only once the cut has reached
15:30, so a 15:29 read carries neither. Each runs to the newest bar finished at or before its mark and says
which minute that was, so a feed gap never passes an older close off as the mark's. The opening gamma is the
day's FIRST diary row's book, as the loader read it (inputs.first_raw_diary_row: the labeller's
cut-down row drops the net gamma and the book's source): the regime, the net dealer gamma in $bn per 1% move
for the 0DTE and the 0-to-7-day books, and the flip as its distance from that row's spot. On a stand-in book
(gex_source other than native) the opening gamma and the rROD gate are both left out, since the gate is the
regime.

The leveraged-fund rebalance (since 2026-10-11): the funds that promise two or three times their index's daily move
(and the inverse ones) must trade into the close to keep that promise, and the need is known from the day's move
and their size: for each fund, assets x L x (L - 1) x its index's move since the prior close, the standard
end-of-day rebalance formula, which has the same sign as the move for long and inverse funds alike (an up day
makes every fund a buyer into the close). Sizes are the recorder's newest line at or before the cut
(inputs.leveraged_fund_sizes); each fund's index move is the S&P's own spot against its prior daily close, or the
fund's index symbol as the market context quotes it at the cut against Schwab's prior close for it. The estimate is
summed by index family in $bn, with the dollars a further 1% move would add and how many funds were counted; it is
left out when no sizes are on file, when the newest are older than SIZES_MAX_AGE_H (a recorder that stopped), or
when no index has a fresh move. Most of it is Nasdaq and chip funds, which reach the S&P through its biggest names.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from ... import station_stores
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs

BLOCK = "close_signals"
R1_MARK = time(10, 0)                # the first half hour from the settled open runs to the close known at 10:00
R12_START = time(15, 0)              # r12 is the 15:00 -> 15:30 return, the half hour before the last
LATE_MARK = time(15, 30)             # r12 and rROD end here, so the 15:30 read is the first to carry them
BP_PER_RETURN = 10_000.0             # a return of 1.0 is 10,000 basis points
BP_DECIMALS = 1
RROD_GATES = {"short": "open_short_gamma", "long": "closed_long_gamma"}   # the rROD effect holds only under short gamma
REBALANCE_FIELD = "leveraged_fund_rebalance"
# Each fund's index symbol (recorder.LEVERAGED_FUNDS) as the family it is summed under
INDEX_FAMILIES = {"$SPX": "spx", "QQQ": "nasdaq_100", "SMH": "semiconductors", "IWM": "small_caps", "NVDA": "single_stocks", "TSLA": "single_stocks"}
SPX = "$SPX"
SIZES_MAX_AGE_H = 14 * 24            # fund sizes drift slowly, but past this the recorder has stopped and the age itself would read as a level
USD_PER_BN = 1e9
BN_DECIMALS = 2
HOURS_DECIMALS = 1


def build_close_signals_block(inputs: FrozenInputs) -> BlockResult:
    result = BlockResult()
    block: dict[str, Any] = {"r1": _first_half_hour(inputs, result)}
    block.update(_late_returns(inputs, result))
    block.update(_opening_gamma(inputs, result))
    block[REBALANCE_FIELD] = _leveraged_fund_rebalance(inputs, result)
    result.data = units.clean(block)
    return result


# --- the leveraged funds' closing rebalance ----------------------------------------------------------------------

def _leveraged_fund_rebalance(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """``{into_close_usd_bn, more_usd_bn_per_1pct, by_index_usd_bn, funds_counted, sizes_age_h, funds_left_out}``:
    the dollars the funds must trade into the close (plus buying, minus selling), from each fund's size and its
    index's move since the prior close, and the dollars a further 1% move would add; see the module docstring for
    the formula."""
    field = f"{BLOCK}.{REBALANCE_FIELD}"
    sizes = inputs.leveraged_fund_sizes
    if not sizes or not isinstance(sizes.get("funds"), dict):
        result.leave_out(field, "no_fund_sizes_recorded")
        return None
    sizes_age_h = (inputs.cut - datetime.fromisoformat(str(sizes.get("recorded_at")))).total_seconds() / 3600
    if sizes_age_h > SIZES_MAX_AGE_H:
        result.leave_out(field, "fund_sizes_stale")
        return None
    moves = _index_moves(inputs, {fund.get("index") for fund in sizes["funds"].values()})
    by_family: dict[str, float] = {}
    per_1pct = 0.0
    counted, left_out = 0, []
    for symbol, fund in sizes["funds"].items():
        index, leverage, assets = fund.get("index"), fund.get("leverage"), fund.get("assets_usd")
        if index not in moves or not units.is_num(leverage) or units.positive(assets) is None:
            left_out.append(symbol)
            continue
        need_per_unit_move = assets * leverage * (leverage - 1)
        family = INDEX_FAMILIES.get(index, index)
        by_family[family] = by_family.get(family, 0.0) + need_per_unit_move * moves[index]
        per_1pct += need_per_unit_move / 100.0
        counted += 1
    if not counted:
        result.leave_out(field, "no_index_move_at_cut")
        return None
    return units.clean({
        "into_close_usd_bn": round(sum(by_family.values()) / USD_PER_BN, BN_DECIMALS),
        "more_usd_bn_per_1pct": round(per_1pct / USD_PER_BN, BN_DECIMALS),
        "by_index_usd_bn": {family: round(dollars / USD_PER_BN, BN_DECIMALS) for family, dollars in by_family.items()},
        "funds_counted": counted,
        "sizes_age_h": round(sizes_age_h, HOURS_DECIMALS),
        "funds_left_out": left_out or None,
    })


def _index_moves(inputs: FrozenInputs, indexes: set) -> dict[str, float]:
    """Each index's move since its prior close as a fraction: the S&P from the read's spot against its prior daily
    close, every other symbol from its fresh quote at the cut against Schwab's prior close for it."""
    moves: dict[str, float] = {}
    if SPX in indexes:
        prior = units.prior_daily_close(inputs, SPX)
        if prior:
            moves[SPX] = inputs.spot / prior - 1.0
    market = inputs.scene.market
    for symbol in indexes:
        if symbol == SPX or not isinstance(symbol, str) or market is None:
            continue
        prior = inputs.snapshot_prior_closes.get(symbol)
        quote = market.last(symbol, inputs.cut, max_age_min=units.QUOTE_MAX_AGE_MIN)
        if prior and quote is not None and quote > 0:
            moves[symbol] = float(quote) / prior - 1.0
    return moves


# --- the returns ----------------------------------------------------------------------------------------------

def _first_half_hour(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The return from the settled open to the close known at R1_MARK, in basis points, with the minute it ran to."""
    measures = station_stores.import_spx_jev("labels.measures")
    mark = _mark(inputs.cut, R1_MARK)
    if inputs.cut < mark:
        result.leave_out(f"{BLOCK}.r1", f"not_until_{mark:%H:%M}")
        return None
    settled_open = measures.settled_open(inputs.scene.bars)
    if settled_open is None:
        result.leave_out(f"{BLOCK}.r1", "no_settled_open")
        return None
    last = _bar_finished_by(inputs.scene.bars, mark, measures)
    if last is None:
        result.leave_out(f"{BLOCK}.r1", f"no_bar_by_{mark:%H:%M}")
        return None
    close, finished = last
    return {"bp": _return_bp(settled_open, close), "to": units.hhmm(finished)}


def _late_returns(inputs: FrozenInputs, result: BlockResult) -> dict[str, Any]:
    """r12 (the close at R12_START to the close at LATE_MARK) and rROD (yesterday's close to the same), in basis
    points, with the minute both ran to; neither until the cut has reached LATE_MARK."""
    measures = station_stores.import_spx_jev("labels.measures")
    mark = _mark(inputs.cut, LATE_MARK)
    if inputs.cut < mark:
        for field in ("r12_bp", "rrod_bp"):
            result.leave_out(f"{BLOCK}.{field}", f"not_until_{mark:%H:%M}")
        return {}
    last = _bar_finished_by(inputs.scene.bars, mark, measures)
    if last is None:
        for field in ("r12_bp", "rrod_bp"):
            result.leave_out(f"{BLOCK}.{field}", f"no_bar_by_{mark:%H:%M}")
        return {}
    close, finished = last
    fields: dict[str, Any] = {"late_to": units.hhmm(finished)}
    start = measures.close_at(inputs.scene.bars, _mark(inputs.cut, R12_START))
    if start is None:
        result.leave_out(f"{BLOCK}.r12_bp", f"no_close_at_{R12_START:%H:%M}")
    else:
        fields["r12_bp"] = _return_bp(start, close)
    prior_close = units.positive(inputs.scene.row.get("prior_close"))
    if prior_close is None:
        result.leave_out(f"{BLOCK}.rrod_bp", "no_prior_close")
    else:
        fields["rrod_bp"] = _return_bp(prior_close, close)
    return fields


def _mark(cut: datetime, clock: time) -> datetime:
    return cut.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)


def _bar_finished_by(bars: list[dict], mark: datetime, measures: Any) -> tuple[float, datetime] | None:
    """The newest bar finished at or before ``mark``: its close and when it finished."""
    done = [b for b in bars if measures.bar_time(b) + measures.ONE_MINUTE <= mark]
    if not done:
        return None
    return float(done[-1]["close"]), measures.bar_time(done[-1]) + measures.ONE_MINUTE


def _return_bp(start: float, end: float) -> float:
    return round((end / start - 1.0) * BP_PER_RETURN, BP_DECIMALS) or 0.0      # never -0.0


# --- the opening gamma ----------------------------------------------------------------------------------------

def _opening_gamma(inputs: FrozenInputs, result: BlockResult) -> dict[str, Any]:
    """The day's first diary row's book: regime, net dealer gamma per book in $bn per 1%, the flip's distance from
    that row's spot, the book's source and the row's clock; with the rROD gate the regime sets."""
    first = inputs.first_raw_diary_row or inputs.scene.rows_today[0]
    source, views = first.get("gex_source"), first.get("gex_views") or {}
    regime = units.regime_word(views.get("regime"))
    why = ("no_gex_source" if not isinstance(source, str) else units.STAND_IN_BOOK if source != units.NATIVE_BOOK
           else "no_opening_regime" if regime is None else None)
    if why is not None:
        result.leave_out(f"{BLOCK}.gamma_open", why)
        result.leave_out(f"{BLOCK}.rrod_gate", why)
        return {}
    gamma: dict[str, Any] = {"regime": regime, "book": source, "at": units.hhmm(datetime.fromisoformat(first["ts"]))}
    net = units.clean({"0dte": units.billions(views.get("net_gex")), "0_7dte": units.billions(views.get("net_gex_tenor"))})
    if net:
        gamma["net_gex_usd_bn_per_1pct"] = net
    else:
        result.leave_out(f"{BLOCK}.gamma_open.net_gex_usd_bn_per_1pct", "no_net_gex")
    flip, spot = units.positive(views.get("flip")), units.positive(first.get("spot"))
    if flip is not None and spot is not None:
        gamma["price_minus_flip_sig"] = units.sig(spot - flip, inputs.sigma_points)
    else:
        result.leave_out(f"{BLOCK}.gamma_open.price_minus_flip_sig", "no_flip")
    fields: dict[str, Any] = {"gamma_open": gamma}
    if regime in RROD_GATES:
        fields["rrod_gate"] = RROD_GATES[regime]
    else:
        result.leave_out(f"{BLOCK}.rrod_gate", f"opening_regime_{regime}")
    return fields


__all__ = ["build_close_signals_block", "BLOCK"]
