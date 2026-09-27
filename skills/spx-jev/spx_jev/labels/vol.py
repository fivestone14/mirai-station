"""The volatility family: implied volatility, realized against priced movement, the expected move and the
VIX curve (iv.*)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..cuts import (EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, IV_FLAT_BAND_PTS, REALIZED_QUIET_RATIO, REALIZED_WILD_RATIO, VIX_CURVE_FLAT,
                    ZERO_DTE_LAST_HOUR_MIN)
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ONE_MINUTE, bar_time, bars_finished_between, day_high_low, is_num
from .words import pct, sig, signed

LABELS = ("iv.trend_30min", "iv.vs_realized_30", "iv.expected_move_used", "iv.move_sides", "iv.term_structure")

FULL_SESSION_MIN = 390          # a full session's minutes: sigma is a full day's expected move
# Realized against priced movement over the last 30 minutes: the realized path from the 1-minute
# closes, against sigma scaled to 30 minutes by the square root of time.
REALIZED_WINDOW_MIN = 30
REALIZED_MIN_BARS = 25


def build_vol_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _trend_30min(scene, ls)
    _realized_vs_priced(scene, ls)
    _expected_move_used(scene, ls)
    _move_sides(scene, ls)
    _term_structure(scene, ls)
    return ls


def _trend_30min(scene: Scene, ls: LabelSet) -> None:
    iv_now = scene.row.get("atm_iv")
    if not is_num(iv_now):
        ls.omit("iv.trend_30min", "row carries no at-the-money implied volatility")
        return
    if scene.minutes_to_close < ZERO_DTE_LAST_HOUR_MIN:
        ls.omit("iv.trend_30min", "the last hour of the 0DTE book: its implied volatility follows the expiry clock, not the market")
        return
    target = scene.now - timedelta(minutes=30)
    earlier = [r for r in scene.rows_today if datetime.fromisoformat(r["ts"]) <= target and is_num(r.get("atm_iv"))]
    if not earlier or datetime.fromisoformat(earlier[-1]["ts"]) < target - timedelta(minutes=10):
        ls.omit("iv.trend_30min", "no row with implied volatility about 30 minutes ago")
        return
    d = (float(iv_now) - float(earlier[-1]["atm_iv"])) * 100.0
    verdict = "flat" if abs(d) <= IV_FLAT_BAND_PTS else "rising" if d > 0 else "falling"
    fig = {"kind": "signed", "value": round(d, 2), "band": IV_FLAT_BAND_PTS, "unit": "vol points", "verdict": verdict}
    if verdict == "flat":
        text = f"over the last 30 minutes at-the-money implied volatility stayed within the {IV_FLAT_BAND_PTS:g} vol point flat band, changing {signed(d)} vol points"
    else:
        text = f"over the last 30 minutes at-the-money implied volatility {'rose' if d > 0 else 'fell'} {abs(d):.2f} vol points, more than the {IV_FLAT_BAND_PTS:g} point flat band"
    ls.put("iv.trend_30min", text, figure=fig)


def _realized_vs_priced(scene: Scene, ls: LabelSet) -> None:
    """How much price actually moved over the last 30 minutes against the move the options
    market prices for 30 minutes. Realized is the square root of the summed squared 1-minute
    close changes, scaled to 30 minutes by the minutes the closes actually span (a change across
    a missing minute already carries that minute's movement, so a gap is neither quiet nor
    counted twice); priced is sigma times the square root of 30 over a full session's minutes."""
    bars, now, w = scene.bars, scene.now, REALIZED_WINDOW_MIN
    win = bars_finished_between(bars, now - timedelta(minutes=w), now)
    before = [x for x in bars if bar_time(x) + ONE_MINUTE <= now - timedelta(minutes=w)]
    if before:
        start, start_t = float(before[-1]["close"]), bar_time(before[-1]) + ONE_MINUTE
    elif win and bars and win[0] is bars[0]:
        start, start_t = float(bars[0]["open"]), bar_time(bars[0])   # the window reaches back to the bell
    else:
        start, start_t = None, None
    if len(win) < REALIZED_MIN_BARS or start is None:
        ls.omit("iv.vs_realized_30", f"needs {REALIZED_MIN_BARS} finished bars in the last {w} minutes")
        return
    span = (bar_time(win[-1]) + ONE_MINUTE - start_t).total_seconds() / 60.0
    closes = [start] + [float(x["close"]) for x in win]
    ss = sum((b - a) ** 2 for a, b in zip(closes[:-1], closes[1:])) * (w / span) if span > 0 else float("nan")
    realized = math.sqrt(ss) / scene.sigma if ss >= 0 else float("nan")
    priced = math.sqrt(w / FULL_SESSION_MIN)
    r = realized / priced
    if not math.isfinite(r):
        ls.omit("iv.vs_realized_30", "the last 30 minutes' closes do not give a finite movement")
        return
    if r < REALIZED_QUIET_RATIO:
        shown = min(r, REALIZED_QUIET_RATIO - 0.01)
        cut = f"under the {REALIZED_QUIET_RATIO:g}-times cut, so quieter than priced"
    elif r > REALIZED_WILD_RATIO:
        shown = max(r, REALIZED_WILD_RATIO + 0.01)
        cut = f"over the {REALIZED_WILD_RATIO:g}-times cut, so wilder than priced"
    else:
        shown = min(max(r, REALIZED_QUIET_RATIO), REALIZED_WILD_RATIO)
        cut = f"between the {REALIZED_QUIET_RATIO:g}-times and {REALIZED_WILD_RATIO:g}-times cuts, so about as priced"
    # the shown ratio never crosses the cut its verdict names, whatever the rounding does
    ls.put("iv.vs_realized_30",
           f"over the last {w} minutes price's realized movement, from its 1-minute closes, was {sig(realized)}, "
           f"{shown:.2f} times the {sig(priced)} move the options market prices for {w} minutes, {cut}")


def _expected_move_used(scene: Scene, ls: LabelSet) -> None:
    ruler_ = scene.row.get("range_ruler") or {}
    used = ruler_.get("em_consumed")
    if not is_num(used) and is_num(ruler_.get("em_open")) and ruler_["em_open"] > 0 and scene.bars:
        hi, lo = day_high_low(scene.bars, scene.spot)
        used = (hi - lo) / float(ruler_["em_open"])
    if not is_num(used):
        ls.omit("iv.expected_move_used", "row carries no expected-move ruler")
    elif used < 0.5:
        ls.put("iv.expected_move_used", f"today's range so far has used {used:.1f} of today's expected move, under half of it")
    elif used <= 1.0:
        ls.put("iv.expected_move_used", f"today's range so far has used {used:.1f} of today's expected move, between half and all of it")
    else:
        ls.put("iv.expected_move_used", f"today's range so far has used {used:.1f} times today's expected move, more than all of it")


def _move_sides(scene: Scene, ls: LabelSet) -> None:
    ds = (scene.row.get("adaptive_em") or {}).get("down_share")
    lo_cut, hi_cut = EVEN_SPLIT_LOW, EVEN_SPLIT_HIGH
    if not is_num(ds):
        ls.omit("iv.move_sides", "row carries no expected-move split")
    elif ds > hi_cut:
        ls.put("iv.move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, skewed to the downside beyond the {pct(hi_cut)} cut")
    elif ds < lo_cut:
        ls.put("iv.move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, skewed to the upside beyond the {pct(hi_cut)} cut")
    else:
        ls.put("iv.move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, even within the {pct(lo_cut)} to {pct(hi_cut)} band")


def _term_structure(scene: Scene, ls: LabelSet) -> None:
    ratio = scene.row.get("vix_ts")
    if not is_num(ratio) or ratio <= 0:
        ls.omit("iv.term_structure", "row carries no VIX against three-month VIX")
    elif ratio >= VIX_CURVE_FLAT:
        ls.put("iv.term_structure", f"the VIX is {ratio:.2f} times the three-month VIX, at or above {VIX_CURVE_FLAT:g}: near-term fear is priced at or above three-month fear, the stressed shape")
    else:
        ls.put("iv.term_structure", f"the VIX is {ratio:.2f} times the three-month VIX, below {VIX_CURVE_FLAT:g}: near-term fear is priced under three-month fear, the calm shape")
