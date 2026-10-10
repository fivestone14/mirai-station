"""The close-signals block: the returns the close is known to lean on, and the dealer gamma the day opened under.

r1 is the first half hour's return, from the settled open (the 09:34 close) to the close known at 10:00, in
basis points; r12 (15:00 to 15:30) and rROD (yesterday's close to 15:30) exist only once the cut has reached
15:30, so a 15:29 read carries neither. Each runs to the newest bar finished at or before its mark and says
which minute that was, so a feed gap never passes an older close off as the mark's. The opening gamma is the
day's FIRST diary row's book, read whole from the diary file (the labeller's cut-down row drops the net
gamma and the book's source): the regime, the net dealer gamma in $bn per 1% move for the 0DTE and the
0-to-7-day books, and the flip as its distance from that row's spot. On a stand-in book (gex_source other
than native) the opening gamma and the rROD gate are both left out, since the gate is the regime. The
leveraged-ETF rebalance estimate waits for its recorder.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from ... import jsonl_store, station_stores
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs

BLOCK = "close_signals"
R1_MARK = time(10, 0)                # the first half hour from the settled open runs to the close known at 10:00
R12_START = time(15, 0)              # r12 is the 15:00 -> 15:30 return, the half hour before the last
LATE_MARK = time(15, 30)             # r12 and rROD end here, so the 15:30 read is the first to carry them
BP = 10_000.0
BP_DECIMALS = 1
USD_BN = 1e9                         # the scanner's net_gex is dollars per 1% move; Claude reads $bn
GEX_DECIMALS = 1
NATIVE_BOOK = "native"
SHORT_GAMMA = "short_gamma"
LONG_GAMMA = "long_gamma"
REGIME_SUFFIX = "_gamma"             # the scanner's "long_gamma" rides as "long"
RROD_GATES = {SHORT_GAMMA: "open_short_gamma", LONG_GAMMA: "closed_long_gamma"}   # the rROD effect holds only under short gamma
LETF_REASON = "include_later"        # no leveraged-ETF recorder yet


def build_close_signals_block(inputs: FrozenInputs) -> BlockResult:
    result = BlockResult()
    block: dict[str, Any] = {"r1": _first_half_hour(inputs, result)}
    block.update(_late_returns(inputs, result))
    block.update(_opening_gamma(inputs, result))
    result.leave_out(f"{BLOCK}.letf", LETF_REASON)
    result.data = units.clean(block)
    return result


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
    prior_close = _positive(inputs.scene.row.get("prior_close"))
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
    return round((end / start - 1.0) * BP, BP_DECIMALS)


# --- the opening gamma ----------------------------------------------------------------------------------------

def _opening_gamma(inputs: FrozenInputs, result: BlockResult) -> dict[str, Any]:
    """The day's first diary row's book: regime, net dealer gamma per book in $bn per 1%, the flip's distance from
    that row's spot, the book's source and the row's clock; with the rROD gate the regime sets."""
    first = inputs.scene.rows_today[0]
    raw = _raw_diary_row(inputs.state_dir, inputs.day, first["ts"]) or first
    source, views = raw.get("gex_source"), raw.get("gex_views") or {}
    regime = views.get("regime")
    why = ("no_gex_source" if not isinstance(source, str) else "stand_in_book" if source != NATIVE_BOOK
           else "no_opening_regime" if not isinstance(regime, str) else None)
    if why is not None:
        result.leave_out(f"{BLOCK}.gamma_open", why)
        result.leave_out(f"{BLOCK}.rrod_gate", why)
        return {}
    gamma: dict[str, Any] = {"regime": regime.removesuffix(REGIME_SUFFIX), "book": source, "at": units.hhmm(datetime.fromisoformat(first["ts"]))}
    net = units.clean({"0dte": _billions(views.get("net_gex")), "0_7dte": _billions(views.get("net_gex_tenor"))})
    if net:
        gamma["net_gex_usd_bn_per_1pct"] = net
    else:
        result.leave_out(f"{BLOCK}.gamma_open.net_gex_usd_bn_per_1pct", "no_net_gex")
    flip, spot = _positive(views.get("flip")), _positive(first.get("spot"))
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


def _raw_diary_row(state_dir: Any, day: str, ts: str) -> dict | None:
    """The diary row stamped ``ts`` as the scanner wrote it, read from the day's file a line at a time and stopped
    at the first row past it (the scanner writes in time order); None when the file or the row is not there."""
    for line in jsonl_store.iter_json_lines(station_stores.reversion_rows_file(state_dir, day)):
        stamped = line.get("ts")
        if stamped == ts:
            return line
        if isinstance(stamped, str) and stamped > ts:
            return None
    return None


def _billions(dollars: Any) -> float | None:
    if not isinstance(dollars, (int, float)) or isinstance(dollars, bool):
        return None
    return round(float(dollars) / USD_BN, GEX_DECIMALS)


def _positive(value: Any) -> float | None:
    """A positive number as a float, else None: a level the row may lack or carry as zero."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


__all__ = ["build_close_signals_block", "BLOCK"]
