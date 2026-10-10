"""The options block: today's dealer book as the diary row carries it, every level a signed distance from
price in sig, every share ranked against the same minute on the prior sessions.

The whole block is absent on a stand-in book, so the SPY proxy never reaches Claude: the row's ``gex_source``
must say "native". The Scene's row is the labeller's reduced copy (spx_jev.row_adapter), which drops the
book's source, its net exposure and the net gamma figures, so the row is read whole from the diary file when
the file is on disk, and the Scene's copy stands in otherwise.

Net gamma is the scanner's signed dealer gamma at spot in dollars per 1% move (lefteye_gex_box._net_gex_at:
gamma x contracts x 100 x S^2 x 0.01), given here in billions: ``gex_views.net_gex`` is today's same-day
book, ``net_gex_tenor`` the blended 0-to-7-day book, and ``net_exposure.prev_close_gamma`` that blended book
as the previous session's last row had it. The regimes are the scanner's words for the same three books: by
open interest and by today's volume for the same-day book, by open interest for the 0-to-7-day book. The
heavy strikes are the same-day book's heaviest call-side and put-side gamma strikes; the 1-to-7-day walls are
the scanner's structural band walls (``call_wall_tenor``, ``put_wall_tenor``). The skew is the same-day smile
rebuilt from the collector's raw tape (spx_jev.labels.vol_sources.skew_at), 25-delta put vol less 25-delta
call vol in vol points, so a positive number is a put skew.

Ranks follow the station's gamma family: each prior session's diary row at this minute (gamma.prior_books),
a distance in that session's own ruler, a share as its row had it.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .. import units
from ... import station_stores
from ...control import ET
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs

BLOCK = "options"
NATIVE_BOOK = "native"            # gex_source on a row whose book is the SPX chain itself, not the SPY stand-in
SAME_DAY_BOOK = "0dte"            # regime_source when the scanner read today's same-day book rather than the blended one
LOOKBACK_MIN = 30                 # the half hour the heavy strikes and the ATM vol are compared over
BILLION = 1e9
NOT_IN_DIARY = "not_in_diary"
DIARY_LINE_START = '{"ts": "'    # every diary line opens with its own timestamp, so a row is found without parsing the day
REGIME_WORDS = {"long_gamma": "long", "short_gamma": "short", "uncertain": "uncertain"}
# Each level the block gives as a distance: its name, where the row keeps it, and whether its distance is ranked.
LEVELS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("gamma_flip", ("gex_views", "flip"), True),
    ("call_side_heavy", ("gex_views", "call_wall_gamma"), False),
    ("charm_wall", ("gex_views", "charm_wall"), False),
    ("magnet", ("gex_views", "magnet"), False),
    ("put_side_heavy", ("gex_views", "put_wall_gamma"), False),
    ("call_wall_1_7dte", ("call_wall_tenor",), False),
    ("put_wall_1_7dte", ("put_wall_tenor",), False),
)


def build_options_block(inputs: FrozenInputs) -> BlockResult:
    row = diary_row(inputs)
    if row.get("gex_source") != NATIVE_BOOK:
        return whole_block_absent(BLOCK, "stand_in_book")
    result = BlockResult()
    gv = row.get("gex_views") or {}
    earlier = row_minutes_ago(inputs, LOOKBACK_MIN)
    prior = _prior_books(inputs)
    block = {
        "book": NATIVE_BOOK,
        "regime": _regime(gv, result),
        "net_gex_usd_bn_per_1pct": _net_gex(row, gv, result),
        "gamma_above_pct": _gamma_above(gv, inputs.spot, prior, result),
        "top_strike_share_pct": _top_strike_share(gv, prior, result),
        "levels_minus_price_sig": _levels(row, inputs, prior, result),
        "heavy_strikes_moved_30m": _heavy_strikes_moved(gv, earlier, result),
        "turnover_x_oi": _turnover(gv, result),
        "skew_put25_minus_call25_volpts": _skew(inputs, result),
        "atm_iv_30m_volpts": _atm_iv_change(row, earlier, result),
    }
    result.data = units.clean(block)
    return result


# --- the row, whole, and the rows it is set against ------------------------------------------------------

def diary_row(inputs: FrozenInputs) -> dict:
    """The read's diary row with every field: the diary file's line stamped ``row_ts``, else the Scene's copy."""
    state_dir = inputs.scene.state_dir
    path = station_stores.reversion_rows_file(state_dir, inputs.day) if state_dir is not None else None
    if path is None or not path.exists():
        return inputs.scene.row
    prefix = f'{DIARY_LINE_START}{inputs.row_ts}"'
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(prefix):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                break
            return raw if isinstance(raw, dict) else inputs.scene.row
    return inputs.scene.row


def row_minutes_ago(inputs: FrozenInputs, minutes: int) -> dict | None:
    """Today's newest diary row written by ``minutes`` before the cut, and not more than the station's row slack
    before that (the scanner writes a row about every 90 seconds, so a wider gap means it was not running)."""
    gamma = station_stores.import_spx_jev("labels.gamma")
    then = inputs.cut - timedelta(minutes=minutes)
    slack = timedelta(minutes=gamma.ROW_SLACK_MIN)
    rows = [r for r in inputs.scene.rows_today if then - slack <= datetime.fromisoformat(r["ts"]) <= then]
    return rows[-1] if rows else None


def _prior_books(inputs: FrozenInputs) -> list[Any]:
    """Each prior session's diary row at this minute with its ruler (gamma.PriorBook), newest first."""
    gamma = station_stores.import_spx_jev("labels.gamma")
    return gamma.prior_books(inputs.scene) or []


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _share_base(prior: list[Any], share: Callable[[dict, float], float | None]) -> list[float]:
    """``share(gex_views, spot)`` on each prior session's row at this minute, where it can be measured."""
    out = []
    for book in prior:
        spot = book.row.get("spot")
        v = share(book.row.get("gex_views") or {}, float(spot)) if _is_num(spot) else None
        if v is not None:
            out.append(v)
    return out


# --- the book's regimes and net gamma --------------------------------------------------------------------

def _regime(gv: dict, result: BlockResult) -> dict | None:
    """Long or short gamma by book: the same-day book by open interest and by today's volume, the 0-to-7-day
    book by open interest. The same-day open-interest word counts only when the scanner read the same-day book."""
    out = {}
    if gv.get("regime_source") != SAME_DAY_BOOK:
        result.leave_out(f"{BLOCK}.regime.0dte_oi", "blended_book_fallback")
    else:
        out["0dte_oi"] = _regime_word("0dte_oi", gv.get("regime"), result)
    out["0dte_volume"] = _regime_word("0dte_volume", gv.get("regime_0dte_vol"), result)
    out["0_7dte_oi"] = _regime_word("0_7dte_oi", gv.get("regime_tenor"), result)
    return units.clean(out) or None


def _regime_word(field: str, word: Any, result: BlockResult) -> str | None:
    if word in REGIME_WORDS:
        return REGIME_WORDS[word]
    result.leave_out(f"{BLOCK}.regime.{field}", NOT_IN_DIARY)
    return None


def _net_gex(row: dict, gv: dict, result: BlockResult) -> dict | None:
    """Signed dealer gamma at spot in $bn per 1% move, per book, and the 0-to-7-day book's at the prior close."""
    figures = {"0dte": gv.get("net_gex"), "0_7dte": gv.get("net_gex_tenor"),
               "at_prior_close_0_7dte": (row.get("net_exposure") or {}).get("prev_close_gamma")}
    out = {}
    for field, dollars in figures.items():
        if _is_num(dollars):
            out[field] = round(float(dollars) / BILLION, 1)
        else:
            result.leave_out(f"{BLOCK}.net_gex_usd_bn_per_1pct.{field}", NOT_IN_DIARY)
    return out or None


# --- where the gamma sits ---------------------------------------------------------------------------------

def _same_day_share(gv: dict, _spot: float) -> float | None:
    """The share of the same-day book's gamma above price."""
    above, below = gv.get("gamma_above_spot"), gv.get("gamma_below_spot")
    if _is_num(above) and _is_num(below) and above + below > 0:
        return float(above) / float(above + below)
    return None


def _week_share(gv: dict, spot: float) -> float | None:
    """The share of the 1-to-7-day book's gamma above price (``net_by_strike_tenor``, today's expiry excluded,
    each strike by its size), a strike at price on neither side."""
    tenor = [(float(x[0]), abs(float(x[1]))) for x in gv.get("net_by_strike_tenor") or []
             if isinstance(x, (list, tuple)) and len(x) >= 2 and _is_num(x[0]) and _is_num(x[1])]
    above = sum(v for k, v in tenor if k > spot)
    total = above + sum(v for k, v in tenor if k < spot)
    return above / total if total > 0 else None


def _gamma_above(gv: dict, spot: float, prior: list[Any], result: BlockResult) -> dict | None:
    out = {}
    for field, share in (("0dte", _same_day_share), ("1_7dte", _week_share)):
        v = share(gv, spot)
        if v is None:
            result.leave_out(f"{BLOCK}.gamma_above_pct.{field}", NOT_IN_DIARY)
            continue
        out[field] = units.ranked(round(v * 100), [round(b * 100) for b in _share_base(prior, share)])
    return out or None


def _top_strike_share(gv: dict, prior: list[Any], result: BlockResult) -> dict | None:
    """The single heaviest strike's share of the same-day book's gamma, in percent."""
    top = gv.get("pin_top_share")
    if not _is_num(top):
        result.leave_out(f"{BLOCK}.top_strike_share_pct", NOT_IN_DIARY)
        return None
    base = [units.pct(b * 100) for b in _share_base(prior, _top_share)]
    return units.ranked(units.pct(float(top) * 100), base)


def _top_share(gv: dict, _spot: float) -> float | None:
    top = gv.get("pin_top_share")
    return float(top) if _is_num(top) else None


# --- the levels, as distances -----------------------------------------------------------------------------

def _level_at(row: dict, keys: tuple[str, ...]) -> float | None:
    node: Any = row
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return float(node) if _is_num(node) else None


def _levels(row: dict, inputs: FrozenInputs, prior: list[Any], result: BlockResult) -> list[dict] | None:
    """Each level as ``{what, sig, r?}``: its signed distance from price in sig, and for the flip how far it sits
    against the prior sessions' flips at this minute (the rank of the unsigned distance, each in its own ruler)."""
    out = []
    for what, keys, ranked in LEVELS:
        level = _level_at(row, keys)
        if level is None:
            result.leave_out(f"{BLOCK}.levels_minus_price_sig.{what}", NOT_IN_DIARY)
            continue
        entry = {"what": what, "sig": units.sig(level - inputs.spot, inputs.sigma_points)}
        if ranked:
            distance = abs(level - inputs.spot) / inputs.sigma_points
            rank = units.ranked(distance, _distance_base(prior, keys))
            if rank and "r" in rank:
                entry["r"] = rank["r"]
        out.append(entry)
    return out or None


def _distance_base(prior: list[Any], keys: tuple[str, ...]) -> list[float]:
    """How far the same level sat from price on each prior session's row at this minute, in its own ruler."""
    out = []
    for book in prior:
        level, spot = _level_at(book.row, keys), book.row.get("spot")
        if level is not None and _is_num(spot) and book.ruler is not None and book.ruler.points:
            out.append(abs(level - float(spot)) / float(book.ruler.points))
    return out


def _heavy_strikes(gv: dict) -> tuple[float, float] | None:
    call, put = gv.get("call_wall_gamma"), gv.get("put_wall_gamma")
    return (float(call), float(put)) if _is_num(call) and _is_num(put) else None


def _heavy_strikes_moved(gv: dict, earlier: dict | None, result: BlockResult) -> bool | None:
    """Whether the same-day book's heaviest call-side or put-side strike differs from the row half an hour ago."""
    field = f"{BLOCK}.heavy_strikes_moved_30m"
    now = _heavy_strikes(gv)
    if now is None:
        result.leave_out(field, NOT_IN_DIARY)
        return None
    if earlier is None:
        result.leave_out(field, "no_row_30m_ago")
        return None
    then = _heavy_strikes(earlier.get("gex_views") or {})
    if then is None:
        result.leave_out(field, NOT_IN_DIARY)
        return None
    return now != then


# --- what trades against what stands ----------------------------------------------------------------------

def _turnover(gv: dict, result: BlockResult) -> float | None:
    """Today's same-day contracts traded near price as a multiple of the standing open interest on those strikes."""
    traded = sum(float(v[1]) + float(v[2]) for v in gv.get("vol_side_by_strike") or []
                 if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2]))
    standing = sum(abs(float(v[1])) + abs(float(v[2])) for v in gv.get("oi_side_by_strike") or []
                   if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2]))
    if traded <= 0 or standing <= 0:
        result.leave_out(f"{BLOCK}.turnover_x_oi", NOT_IN_DIARY)
        return None
    return round(traded / standing, 1)


# --- the vol surface --------------------------------------------------------------------------------------

def _put_minus_call(smile: Any) -> float | None:
    """25-delta put vol less 25-delta call vol, in vol points."""
    if smile is None or smile.put_25 is None or smile.call_25 is None:
        return None
    return round((smile.put_25 - smile.call_25) * 100, 2)


def _skew(inputs: FrozenInputs, result: BlockResult) -> dict | None:
    """The same-day smile's put-call tilt at the cut's last whole minute, from the collector's raw tape as it was on
    file at the cut, ranked against the same minute on the prior sessions' tapes (read as the station's vol family
    reads them, so a tape read once serves both)."""
    field = f"{BLOCK}.skew_put25_minus_call25_volpts"
    state_dir = inputs.scene.state_dir
    if state_dir is None:
        result.leave_out(field, "no_state_dir")
        return None
    vol_sources = station_stores.import_spx_jev("labels.vol_sources")
    ranks = station_stores.import_spx_jev("labels.ranks")
    cuts = station_stores.import_spx_jev("cuts")
    at_min = vol_sources.minute_floor(inputs.cut)
    before = at_min - timedelta(minutes=LOOKBACK_MIN)
    smiles = vol_sources.skew_at(state_dir, inputs.day, (at_min, before), inputs.cut)
    if smiles is None:
        result.leave_out(field, "raw_tape_missing")
        return None
    tilt = _put_minus_call(smiles[at_min])
    if tilt is None:
        result.leave_out(field, "no_smile_at_minute")
        return None
    base = []
    for day in ranks.rank_days(inputs.scene)[:cuts.NIGHT_RANK_COUNT]:
        read_at = datetime.combine(date.fromisoformat(day), inputs.cut.astimezone(ET).time(), tzinfo=ET)
        then = vol_sources.minute_floor(read_at)
        prior = vol_sources.skew_at(state_dir, day, (then - timedelta(minutes=LOOKBACK_MIN), then), read_at)
        v = _put_minus_call(prior[then]) if prior is not None else None
        if v is not None:
            base.append(v)
    return units.ranked(tilt, base)


def _atm_iv_change(row: dict, earlier: dict | None, result: BlockResult) -> float | None:
    """How far the scanner's at-the-money implied volatility moved over the half hour, in vol points."""
    field = f"{BLOCK}.atm_iv_30m_volpts"
    now = row.get("atm_iv")
    if not _is_num(now):
        result.leave_out(field, NOT_IN_DIARY)
        return None
    if earlier is None:
        result.leave_out(field, "no_row_30m_ago")
        return None
    then = earlier.get("atm_iv")
    if not _is_num(then):
        result.leave_out(field, NOT_IN_DIARY)
        return None
    return round((float(now) - float(then)) * 100, 2)
