"""The volatility family: implied volatility, realized against priced movement, the expected move and the
VIX curve (iv.*), and the VIX family, the straddle and the skew (vol.*, skew.*).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, time, timedelta

from ..cuts import (ATM_RESID_VOLPTS, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, IV_FLAT_BAND_PTS, REALIZED_QUIET_RATIO, REALIZED_WILD_RATIO,
                    VIX_CURVE_FLAT, VIX_GAP_RESID_PTS, VIX_JUMP_PCT, VIX_JUMP_PCT_10, VIX_MOVE_PCT, VIX_MOVE_PCT_10, VIX_RESID_PCT,
                    VIX_STILL_PCT, VIX_STILL_PCT_10, WINDOW_10_MIN, WINDOW_30_MIN, ZERO_DTE_LAST_HOUR_MIN)
from ..state_builder import Scene, row_days
from .label_set import LabelSet
from .measures import ET, ONE_MINUTE, bar_time, bars_finished_between, close_at, day_high_low, is_num, settled_open
from .rulers import SigmaRuler, sigma_anchor
from .vol_sources import DiaryPoint, diary_point, point_at, prior_diary
from .words import pct, sig, signed

LABELS = ("iv.trend_30min", "iv.vs_realized_30", "iv.expected_move_used", "iv.move_sides", "iv.term_structure",
          "vol.atm_iv_residual", "vol.front_fear_shift", "vol.realized_vs_clock", "vol.realized_vs_clock_rank", "vol.ruler_event_load",
          "vol.straddle_reprice_30", "vol.straddle_vs_clock", "vol.stress_path", "vol.term_structure", "vol.vix_change_30",
          "vol.vix_on_shock", "vol.vix_overnight_surprise", "vol.vix_since_1400", "vol.vix_vs_price", "vol.vvix_vs_vix",
          "vol.vvix_with_move", "skew.put_tilt_vs_usual", "skew.shift_vs_price")
GATES = ("put_tilt_vs_clock",)
DARK: dict[str, str] = {}

FULL_SESSION_MIN = 390          # a full session's minutes: sigma is a full day's expected move
# Realized against priced movement over the last 30 minutes: the realized path from the 1-minute
# closes, against sigma scaled to 30 minutes by the square root of time.
REALIZED_WINDOW_MIN = 30
REALIZED_MIN_BARS = 25
# How far VIX and same-day at-the-money vol move with price, fitted once on the diary and bars of the 65 sessions
# to 2026-09-25 against the morning anchor (not refitted yet): VIX points per sigma of a 30-minute SPX move
# (758 reads), 0DTE at-the-money vol points per sigma outside the last hour (400 reads; the set guessed about
# -3.1), and VIX points per sigma of the overnight gap, today's first row against the prior session's last (54 days).
VIX_PER_SIGMA = -1.08
ATM_IV_PER_SIGMA = -2.2
VIX_PER_GAP_SIGMA = -1.7
# The VIX that closes the session is read against the early afternoon: the newest row by 14:00.
AFTERNOON_ANCHOR = time(14, 0)


def build_vol_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _trend_30min(scene, ls)
    _realized_vs_priced(scene, ls)
    _expected_move_used(scene, ls)
    _move_sides(scene, ls)
    _term_structure(scene, ls)
    today = [diary_point(r) for r in scene.rows_today]
    _vix_change(scene, today, ls)
    _vix_since_1400(scene, today, ls)
    _vix_vs_price(scene, today, ls)
    _atm_iv_residual(scene, today, ls)
    _vix_overnight_surprise(scene, today, ls)
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


# ----------------------------------------------------------------------------- the VIX and same-day vol against price

def _share(x: float) -> str:
    """A share of a level in percent, to two decimals: 0.0126 is '1.26%'."""
    return f"{x * 100:.2f}%"


def _line(cut: float) -> str:
    """A percent line as the set writes it: 0.013 is '1.3%', 0.0035 is '0.35%'."""
    return f"{round(cut * 100, 4):g}%"


def _in_band(x: float, lo: float, hi: float, step: float) -> float:
    """``x`` kept inside [lo, hi - step] so that, shown to ``step``, it never crosses the line its verdict names."""
    return min(max(x, lo), hi - step)


def _ruled(ruler: SigmaRuler) -> str:
    return "; ruler estimated" if ruler.estimated else ""


def _spx_move(scene: Scene, window: int, ruler: SigmaRuler) -> float | None:
    """Price's move over the last ``window`` minutes in the morning anchor: spot against the close of the last
    bar finished ``window`` minutes ago."""
    ref = close_at(scene.bars, scene.now - timedelta(minutes=window))
    return None if ref is None else (scene.spot - ref) / ruler.points


def _change(today: list[DiaryPoint], now: datetime, window: int, read: Callable[[DiaryPoint], float | None]) -> tuple[float, float] | None:
    """``(then, now)`` of a diary value across the last ``window`` minutes; None when either end is missing."""
    then = point_at(today, now - timedelta(minutes=window))
    a, b = (read(then) if then else None), read(today[-1])
    return None if a is None or b is None else (a, b)


def _vix_change(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX's move over the window as a share of its level: 30 minutes on the live lane, 10 on the opening lane,
    each against its own lines."""
    if scene.bar_clock:
        window, (still, move, jump) = WINDOW_10_MIN, (VIX_STILL_PCT_10, VIX_MOVE_PCT_10, VIX_JUMP_PCT_10)
    else:
        window, (still, move, jump) = WINDOW_30_MIN, (VIX_STILL_PCT, VIX_MOVE_PCT, VIX_JUMP_PCT)
    got = _change(today, scene.now, window, lambda p: p.vix)
    if got is None:
        ls.omit("vol.vix_change_30", f"no diary VIX now and about {window} minutes ago (range_ruler.vol_carry.vix)")
        return
    then, now = got
    d, step = now - then, 0.0001
    share = abs(d) / now
    if share < still:
        shown, words = min(share, still - step), f"under the {_line(still)} still line"
    elif share < move:
        shown, words = _in_band(share, still, move, step), f"at or past the {_line(still)} still line and short of the {_line(move)} moving line"
    elif share < jump:
        shown, words = _in_band(share, move, jump, step), f"at or past the {_line(move)} moving line and short of the {_line(jump)} jump line"
    else:
        shown, words = share, f"at or past the {_line(jump)} jump line"
    how = "was unchanged" if d == 0 else f"{'rose' if d > 0 else 'fell'} {abs(d):.2f} points"
    ls.put("vol.vix_change_30", f"over the last {window} minutes VIX {how}, {_share(shown)} of its {now:.1f} level, {words}")


def _vix_since_1400(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    anchor_t = datetime.combine(scene.now.astimezone(ET).date(), AFTERNOON_ANCHOR, tzinfo=ET)
    if scene.now < anchor_t:
        ls.omit("vol.vix_since_1400", "before 14:00")
        return
    then, now = point_at(today, anchor_t), today[-1].vix
    if then is None or then.vix is None or now is None:
        ls.omit("vol.vix_since_1400", "no diary VIX now and at 14:00 (range_ruler.vol_carry.vix)")
        return
    d = now - then.vix
    share, step = abs(d) / now, 0.0001
    if share > VIX_MOVE_PCT:
        shown, words = max(share, VIX_MOVE_PCT + step), f"past the {_line(VIX_MOVE_PCT)} moving line"
    else:
        shown, words = share, f"inside the {_line(VIX_MOVE_PCT)} moving line"
    how = "was unchanged" if d == 0 else f"{'rose' if d > 0 else 'fell'} {abs(d):.2f} points"
    ls.put("vol.vix_since_1400", f"since 14:00 VIX {how}, {_share(shown)} of its {now:.1f} level, {words}")


def _vix_vs_price(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX's 30-minute change less what price's move alone would do to it (VIX_PER_SIGMA), as a share of VIX."""
    ruler = sigma_anchor(scene)
    got = _change(today, scene.now, WINDOW_30_MIN, lambda p: p.vix)
    move = _spx_move(scene, WINDOW_30_MIN, ruler) if ruler else None
    if ruler is None or got is None or move is None:
        ls.omit("vol.vix_vs_price", "needs the morning sigma ruler, a finished bar 30 minutes ago and the diary VIX now and then")
        return
    then, now = got
    d = now - then
    explained = VIX_PER_SIGMA * move
    resid = d - explained
    share, step = abs(resid) / now, 0.0001
    if share > VIX_RESID_PCT:
        shown, words = max(share, VIX_RESID_PCT + step), f"past the {_line(VIX_RESID_PCT)} line"
    else:
        shown, words = share, f"inside the {_line(VIX_RESID_PCT)} line"
    ls.put("vol.vix_vs_price",
           f"over the last 30 minutes VIX {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points; the {abs(move):.2f} sigma SPX "
           f"{'drop' if move < 0 else 'rise'} alone would {'lift' if explained >= 0 else 'lower'} it about {abs(explained):.2f}, so fear rose "
           f"{abs(resid):.2f} points ({_share(shown)} of VIX) {'more' if resid >= 0 else 'less'} than price explains, {words}{_ruled(ruler)}")


def _atm_iv_residual(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """Same-day at-the-money vol's 30-minute change less what price's move alone would do to it (ATM_IV_PER_SIGMA)."""
    if scene.minutes_to_close < ZERO_DTE_LAST_HOUR_MIN:
        ls.omit("vol.atm_iv_residual", "the last hour of the 0DTE book: its implied volatility follows the expiry clock, not the market")
        return
    ruler = sigma_anchor(scene)
    got = _change(today, scene.now, WINDOW_30_MIN, lambda p: p.atm_iv)
    move = _spx_move(scene, WINDOW_30_MIN, ruler) if ruler else None
    if ruler is None or got is None or move is None:
        ls.omit("vol.atm_iv_residual", "needs the morning sigma ruler, a finished bar 30 minutes ago and at-the-money vol on the rows now and then")
        return
    then, now = got
    d = (now - then) * 100.0
    explained = ATM_IV_PER_SIGMA * move
    resid = d - explained
    if abs(resid) > ATM_RESID_VOLPTS:
        shown, words = max(abs(resid), ATM_RESID_VOLPTS + 0.01), f"past the {ATM_RESID_VOLPTS:g} line"
    else:
        shown, words = abs(resid), f"inside the {ATM_RESID_VOLPTS:g} line"
    ls.put("vol.atm_iv_residual",
           f"over the last 30 minutes same-day at-the-money vol {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points while price "
           f"{'fell' if move < 0 else 'rose'} {sig(abs(move))}; the {'fall' if move < 0 else 'rise'} alone explains a "
           f"{'rise' if explained >= 0 else 'fall'} of {abs(explained):.2f}, so vol ended {shown:.2f} points "
           f"{'above' if resid >= 0 else 'below'} what price explains, {words}{_ruled(ruler)}")


def _vix_overnight_surprise(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """Today's first diary VIX against the prior session's last, less what the gap alone would do to it
    (VIX_PER_GAP_SIGMA times the gap from yesterday's close to the settled open, in the morning anchor)."""
    first = today[0].vix
    prior = [d for d in row_days(scene.state_dir) if d < scene.day] if scene.state_dir else []
    last = next((p.vix for p in reversed(prior_diary(scene.state_dir, prior[-1])) if p.vix), None) if prior else None
    opened, close, ruler = settled_open(scene.bars), scene.row.get("prior_close"), sigma_anchor(scene)
    if first is None or last is None:
        ls.omit("vol.vix_overnight_surprise", "needs the diary VIX on today's first row and the prior session's last")
        return
    if opened is None or not is_num(close) or ruler is None:
        ls.omit("vol.vix_overnight_surprise", "needs the settled open (the 09:34 bar's close), yesterday's close and the morning sigma ruler")
        return
    gap = (opened - float(close)) / ruler.points
    implied = last + VIX_PER_GAP_SIGMA * gap
    resid = first - implied
    if abs(resid) > VIX_GAP_RESID_PTS:
        shown, words = max(abs(resid), VIX_GAP_RESID_PTS + 0.01), f"past the {VIX_GAP_RESID_PTS:g} line"
    else:
        shown, words = abs(resid), f"within the {VIX_GAP_RESID_PTS:g} line"
    ls.put("vol.vix_overnight_surprise",
           f"VIX's first print today was {first:.2f}, {abs(first - last):.2f} {'above' if first >= last else 'below'} yesterday's last "
           f"{last:.2f}; after this morning's {sig(abs(gap))} gap {'up' if gap >= 0 else 'down'} it would normally sit near {implied:.2f}, "
           f"so it is {shown:.2f} points {'richer' if resid >= 0 else 'cheaper'} than the gap implies, {words}{_ruled(ruler)}")
