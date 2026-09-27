"""The volatility family: implied volatility, realized against priced movement, the expected move and the
VIX curve (iv.*), and the VIX family, the straddle and the skew (vol.*, skew.*).

The final question set's labels are measured on the morning anchor (rulers.sigma_anchor); the ones built
before it keep the row's sigma. They read the diary rows (the VIX, at-the-money vol, the straddle left and
the VIX curve), the prior sessions' diaries and the lob-flow tape (vol_sources), the minute bars, and the
context job's VIX family and NYSE TICK; a shock is the events and shocks family's own burst. Each set
label's sentence, how it is computed and its source are in spec/question_set.json ``labels``."""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

from .. import events
from ..cuts import (ATM_RESID_VOLPTS, BOTTOM_FIFTH, BOUNCE_SIGMA, EVENT_DIGEST_MIN, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW,
                    FRONT_SHIFT_PTS, HALF_RANK, IV_FLAT_BAND_PTS, LOADED_RATIO, MIN_RANK_SESSIONS, MOVE_RULE_SIGMA,
                    NEAR_LOW_SIGMA, ONE_RATIO, PAIR_MOVE_SIGMA, REALIZED_QUIET_RATIO, REALIZED_WILD_RATIO, RULER_HIGH, RULER_LOW,
                    RV_HOT, SHOCK_LOOKBACK_MIN, SKEW_FLAT_RANK, SKEW_RESID_CUT, SKEW_STEEP_RANK, STRADDLE_CHEAP,
                    STRADDLE_REPRICE_SHARE, STRADDLE_RICH, STRESS_CURVE, STRESS_HOLD_SHARE, STRESS_RETREAT_SHARE, STRESS_VIX_RISE_PTS,
                    TICK_CLUSTER, TICK_EXTREME, TOP_FIFTH, VIX_CURVE_FLAT, VIX_CURVE_NEAR_FLAT, VIX_GAP_RESID_PTS, VIX_JUMP_PCT, VIX_JUMP_PCT_10,
                    VIX_MOVE_PCT, VIX_MOVE_PCT_10, VIX_RESID_PCT, VIX_SHOCK_RESID, VIX_STILL_PCT, VIX_STILL_PCT_10, WINDOW_10_MIN,
                    WINDOW_30_MIN, ZERO_DTE_LAST_HOUR_MIN)
from ..sessions import next_trading_day, session_close, session_minutes
from ..state_builder import MarketContext, Scene, row_days
from .events_shocks import judged_windows, shock_bursts
from .label_set import LabelSet
from .measures import (ET, ONE_MINUTE, bar_time, bars_finished_between, close_at, day_high_low, is_num, session_extremes,
                       settled_open)
from .ranks import SameClockRank, rank_against, same_clock_values
from .rulers import SigmaRuler, normal_day_sigma, sigma_anchor
from .vol_sources import ROW_MAX_GAP, DiaryPoint, Skew, diary_point, minute_floor, point_at, prior_diary, skew_at
from .words import pct, plural, sig, signed

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
# What the tape delivers over 30 minutes: the median high-to-low range of the last six finished 5-minute
# slices, scaled from 5 minutes to 30 by the square root of time.
TAPE_SLICE_MIN, TAPE_SLICES = 5, 6
# The calendar's events that load the day's straddle before them: the Fed's decision and the chair's set pieces.
# Any row of the calendar, of any tier, makes the day one with a scheduled event.
LOADING_EVENTS = ("FOMC", "FED_CHAIR_TESTIMONY", "FED_CHAIR_JACKSON_HOLE")
# VIX's reaction to a shock is measured from this many minutes before the burst began (the set's words).
SHOCK_LEAD_MIN = 6
# A real move, for how long the tape has been still: MOVE_RULE_SIGMA within this many minutes (context.time_since_last_move's).
REAL_MOVE_MIN = 10
# The context job snapshots the VIX family every minute: a value older than this means it stopped.
QUOTE_MAX_AGE_MIN = 5
# VVIX's leftover move is fitted on the prior sessions' half hours ending 10:30 to 15:30 (11 a session).
VVIX_FIT_FIRST, VVIX_FIT_READS = time(10, 30), 11
# The put tilt is ranked against this many prior sessions at the same minute (the set's "prior 10 sessions").
SKEW_RANK_SESSIONS = 10
# How far the one-remaining-sd put-call tilt (a share of at-the-money vol) moves with price, fitted once on
# the lob-flow tape of the 21 sessions to 2026-09-25 (230 half hours, morning anchor; not refitted yet).
SKEW_PER_SIGMA = -0.19

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
    _vix_on_shock(scene, today, ls)
    _straddle_reprice(scene, today, ls)
    _straddle_vs_clock(scene, today, ls)
    _ruler_event_load(scene, today, ls)
    _realized_vs_clock(scene, ls)
    _front_fear_shift(scene, ls)
    _vvix_with_move(scene, ls)
    _vvix_vs_vix(scene, ls)
    _vix_curve(scene, today, ls)
    _stress_path(scene, today, ls)
    _skew(scene, ls)
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
    """``(then, now)`` of a diary value across the last ``window`` minutes; None when either end is missing. A
    window that starts before the day's first row, by no more than a row's gap, starts at it: the 09:40 read's
    ten minutes run from the open's first print."""
    start = now - timedelta(minutes=window)
    then = point_at(today, start) or (today[0] if start < today[0].ts <= start + ROW_MAX_GAP else None)
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
           f"over the last 30 minutes VIX ended {abs(resid):.2f} points ({_share(shown)} of VIX) {'above' if resid >= 0 else 'below'} what "
           f"price explains, {words}: it {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points, and the {abs(move):.2f} sigma SPX "
           f"{'drop' if move < 0 else 'rise'} alone would {'lift' if explained >= 0 else 'lower'} it about {abs(explained):.2f}{_ruled(ruler)}")


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
    if prior and next_trading_day(date.fromisoformat(prior[-1])).isoformat() != scene.day:
        ls.omit("vol.vix_overnight_surprise", f"the previous session's diary is not on file: its newest earlier day is {prior[-1]}")
        return
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


def _vix_on_shock(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX since just before the last hour's largest burst, less what price's move over the same span explains
    (VIX_PER_SIGMA). The burst is the shock family's own (shock.burst), found the same way, so both read one shock."""
    path = "vol.vix_on_shock"
    ruler = sigma_anchor(scene)
    if ruler is None:
        ls.omit(path, "no morning sigma ruler to find a shock with")
        return
    since = scene.now - timedelta(minutes=SHOCK_LOOKBACK_MIN)
    shocks = [b for b in shock_bursts(judged_windows(scene, ruler.points, EVENT_DIGEST_MIN)) if b.end > since]
    if not shocks:
        ls.omit(path, f"no shock in the last {SHOCK_LOOKBACK_MIN} minutes (shock.burst)")
        return
    shock = max(shocks, key=lambda b: abs(b.move))
    lead = shock.start - timedelta(minutes=SHOCK_LEAD_MIN)
    before, ref = point_at(today, lead), close_at(scene.bars, lead)
    if before is None or before.vix is None or today[-1].vix is None or ref is None:
        ls.omit(path, f"no diary VIX and finished bar {SHOCK_LEAD_MIN} minutes before the shock, or no diary VIX now")
        return
    d = today[-1].vix - before.vix
    move = (scene.spot - ref) / ruler.points
    explained = VIX_PER_SIGMA * move
    resid = d - explained
    if abs(resid) >= VIX_SHOCK_RESID:
        shown, words = abs(resid), f"past the {VIX_SHOCK_RESID:g}-point rule"
    else:
        shown, words = min(abs(resid), VIX_SHOCK_RESID - 0.01), f"within the {VIX_SHOCK_RESID:g}-point rule"
    ls.put(path,
           f"since just before the shock, at {_clock(lead)}, VIX ended {shown:.2f} points {'above' if resid >= 0 else 'below'} what price "
           f"explains, {words}: it {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points, and the {sig(abs(move))} "
           f"{'drop' if move < 0 else 'rise'} alone would {'lift' if explained >= 0 else 'lower'} it {abs(explained):.2f}{_ruled(ruler)}")


# ----------------------------------------------------------------------------- the straddle and the ruler

def _clock(t: datetime) -> str:
    return f"{t.astimezone(ET):%H:%M}"


def _same_clock(t: datetime, day: str) -> datetime:
    """``t``'s clock minute on a prior session, market time."""
    return datetime.combine(date.fromisoformat(day), t.astimezone(ET).time(), tzinfo=ET)


def _ranked_prior_days(scene: Scene) -> list[str]:
    """The prior sessions a rank may use, newest first: a day whose morning ruler was estimated sits out
    (as in ranks.same_clock_values)."""
    return [d for d in scene.prior_bars if not ((r := scene.prior_rulers.get(d)) is not None and r.estimated)]


def _fifth(rank: SameClockRank) -> str:
    return "top fifth" if rank.share >= TOP_FIFTH else "bottom fifth" if rank.share <= BOTTOM_FIFTH else "between the fifths"


def _minutes_left(t: datetime) -> float:
    return (session_close(t) - t).total_seconds() / 60.0


def _straddle_share(point: DiaryPoint | None) -> float | None:
    """The straddle left at the point against the opening straddle scaled by the clock alone (the square
    root of the session's minutes left): 1.0 is a straddle that has only decayed with time."""
    if point is None or point.em_points is None or point.em_open is None or _minutes_left(point.ts) <= 0:
        return None
    return point.em_points / (point.em_open * math.sqrt(_minutes_left(point.ts) / session_minutes(point.ts)))


def _straddle_reprice(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """The straddle left now against the one 30 minutes ago, less what the clock alone would have taken off it."""
    now, then = today[-1], point_at(today, scene.now - timedelta(minutes=WINDOW_30_MIN))
    if then is None or now.em_points is None or then.em_points is None or _minutes_left(now.ts) <= 0:
        ls.omit("vol.straddle_reprice_30", "no straddle left on the rows now and about 30 minutes ago (range_ruler.em_points)")
        return
    reprice = now.em_points / then.em_points / math.sqrt(_minutes_left(now.ts) / _minutes_left(then.ts)) - 1.0
    if abs(reprice) > STRADDLE_REPRICE_SHARE:
        shown, words = max(abs(reprice), STRADDLE_REPRICE_SHARE + 0.001), f"past the {pct(STRADDLE_REPRICE_SHARE)} repricing line"
    else:
        shown, words = abs(reprice), f"within the {pct(STRADDLE_REPRICE_SHARE)} repricing line"
    ls.put("vol.straddle_reprice_30",
           f"over the last 30 minutes the same-day straddle for the rest of today ended {shown * 100:.1f}% "
           f"{'richer' if reprice >= 0 else 'cheaper'} than the clock alone would have left it, {words}")


def _straddle_vs_clock(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """The straddle's share of the opening straddle (_straddle_share) against its median on the prior sessions at this minute."""
    share = _straddle_share(today[-1])
    if share is None:
        ls.omit("vol.straddle_vs_clock", "row carries no straddle left and opening straddle (range_ruler.em_points, em_open)")
        return
    if scene.state_dir is None:
        ls.omit("vol.straddle_vs_clock", "no state folder to read the prior sessions' diaries from")
        return
    base = [s for d in _ranked_prior_days(scene)
            if (s := _straddle_share(point_at(prior_diary(scene.state_dir, d), _same_clock(scene.now, d)))) is not None]
    if len(base) < MIN_RANK_SESSIONS:
        ls.omit("vol.straddle_vs_clock", f"needs {MIN_RANK_SESSIONS} prior sessions with a straddle at this minute, have {len(base)}")
        return
    ratio = share / statistics.median(base)
    if ratio < STRADDLE_CHEAP:
        shown, words = min(ratio, STRADDLE_CHEAP - 0.01), f"under the {STRADDLE_CHEAP:g} cheap line"
    elif ratio > STRADDLE_RICH:
        shown, words = max(ratio, STRADDLE_RICH + 0.01), f"over the {STRADDLE_RICH:g} rich line"
    else:
        shown, words = ratio, f"between the {STRADDLE_CHEAP:g} cheap and {STRADDLE_RICH:g} rich lines"
    ls.put("vol.straddle_vs_clock",
           f"the same-day straddle for the rest of today is {shown:.2f} times its usual share of the opening straddle at "
           f"{_clock(scene.now)} (its median on the last {len(base)} sessions), {words}")


def _straddle_30(point: DiaryPoint | None) -> float | None:
    """The move the straddle left prices for the next 30 minutes, in points: the square root of time
    scales the rest of the day down to 30 minutes."""
    if point is None or point.em_points is None or _minutes_left(point.ts) < WINDOW_30_MIN:
        return None
    return point.em_points * math.sqrt(WINDOW_30_MIN / _minutes_left(point.ts))


def _tape_30(bars: list[dict], t: datetime) -> float | None:
    """What the tape delivers over 30 minutes at ``t``, in points (TAPE_SLICES); None when a slice has no bars."""
    ranges = []
    for k in range(TAPE_SLICES):
        end = t - timedelta(minutes=TAPE_SLICE_MIN * k)
        sl = bars_finished_between(bars, end - timedelta(minutes=TAPE_SLICE_MIN), end)
        if not sl:
            return None
        ranges.append(max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl))
    return statistics.median(ranges) * math.sqrt(WINDOW_30_MIN / TAPE_SLICE_MIN)


def _release_pace(scene: Scene, today: list[DiaryPoint], start: datetime, delivered: float) -> float | str:
    """What the tape delivers now over what the straddle priced for 30 minutes just before ``start``, against
    the same ratio on the prior sessions (their tape at this minute over their straddle at ``start``'s): a tape's
    high-to-low ranges run about twice a straddle's expected move, so 1.0 is a usual tape, not the straddle's.
    The reason it cannot be judged, when it cannot."""
    before = _straddle_30(point_at(today, start))
    if before is None:
        return f"no straddle on the rows just before {_clock(start)} to judge the tape against"
    usual = [t / p for d in _ranked_prior_days(scene)
             if (p := _straddle_30(point_at(prior_diary(scene.state_dir, d), _same_clock(start, d))))
             and (t := _tape_30(scene.prior_bars[d], _same_clock(scene.now, d)))]
    if len(usual) < MIN_RANK_SESSIONS:
        return (f"needs {MIN_RANK_SESSIONS} prior sessions with a straddle at {_clock(start)} and a tape at this minute to judge "
                f"the tape against, have {len(usual)}")
    return delivered / before / statistics.median(usual)


def _ruler_event_load(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """Whether the day's priced movement is swollen by an event ahead, released by one just past, swollen or
    compressed with none, or normal: the straddle's 30-minute move against what the tape delivers (ranked at
    this minute), the calendar, and the morning anchor against its normal-day median."""
    path = "vol.ruler_event_load"
    day = scene.now.astimezone(ET).date()
    why = events.uncovered(day)
    if why:
        ls.omit(path, why)
        return
    priced, delivered = _straddle_30(today[-1]), _tape_30(scene.bars, scene.now)
    if priced is None or not delivered:
        ls.omit(path, "needs the straddle left on the row, 30 minutes of session left and six finished 5-minute slices")
        return
    anchor, normal = sigma_anchor(scene), normal_day_sigma(scene)
    if anchor is None or normal is None:
        ls.omit(path, f"needs the morning sigma ruler and {MIN_RANK_SESSIONS} prior sessions' trusted anchors for its normal")
        return
    if scene.state_dir is None:
        ls.omit(path, "no state folder to read the prior sessions' diaries from")
        return
    base = []
    for d in _ranked_prior_days(scene):
        then = _same_clock(scene.now, d)
        p, t = _straddle_30(point_at(prior_diary(scene.state_dir, d), then)), _tape_30(scene.prior_bars[d], then)
        if p is not None and t:
            base.append(p / t)
    load = priced / delivered
    rank = rank_against(load, base)
    if rank is None:
        ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions with a straddle and a tape at this minute, have {len(base)}")
        return
    swell = anchor.points / normal
    sessions = sum(1 for r in scene.prior_rulers.values() if r is not None and not r.estimated)
    day_events = events.on_day(day)
    event = next((e for e in day_events if e.kind in LOADING_EVENTS), None)
    released = ahead = False
    if not day_events:
        event_words = "nothing is on the event calendar today"
    else:
        event_words = "on the event calendar today: " + ", ".join(f"{e.words} at {_clock(e.start)}" for e in day_events)
    if event is not None:
        start = event.start
        ago = round((scene.now - start).total_seconds() / 60.0)
        if start > scene.now:
            ahead = True
            event_words += f"; {event.words} is still ahead"
        elif ago > EVENT_DIGEST_MIN:
            event_words += f"; {event.words} came out {ago} minutes ago, past the {EVENT_DIGEST_MIN}-minute digest window"
        else:
            event_words += f"; {event.words} came out {ago} minutes ago, inside the {EVENT_DIGEST_MIN}-minute digest window"
            pace = _release_pace(scene, today, start, delivered)
            if isinstance(pace, str):
                event_words += f"; {pace}"
            else:
                released = pace > ONE_RATIO
                shown = max(pace, ONE_RATIO + 0.01) if released else pace
                event_words += (f"; the tape now moves {shown:.2f} times its usual against the straddle priced just before it, "
                                f"{'over' if released else 'at or under'} the one-to-one line")
    if released:
        verdict = "released after an event"
    elif ahead and load >= LOADED_RATIO and rank.share >= TOP_FIFTH:
        verdict = "loaded before an event"
    elif not day_events and swell >= RULER_HIGH:
        verdict = "swollen with no event"
    elif swell <= RULER_LOW:
        verdict = "compressed"
    else:
        verdict = "normal"
    if load >= LOADED_RATIO:
        load_words = f"{load:.2f} times what the tape's recent 5-minute ranges scale to, past the {LOADED_RATIO:g} loaded line"
    else:
        load_words = f"{min(load, LOADED_RATIO - 0.01):.2f} times what the tape's recent 5-minute ranges scale to, under the {LOADED_RATIO:g} loaded line"
    if swell >= RULER_HIGH:
        swell_words = f"{swell:.2f} times its {sessions}-session median, past the {RULER_HIGH:g} swollen line"
    elif swell <= RULER_LOW:
        swell_words = f"{swell:.2f} times its {sessions}-session median, at or under the {RULER_LOW:g} compressed line"
    else:
        swell_words = (f"{_in_band(swell, RULER_LOW + 0.01, RULER_HIGH, 0.01):.2f} times its {sessions}-session median, "
                       f"between the {RULER_LOW:g} compressed and {RULER_HIGH:g} swollen lines")
    ls.put(path,
           f"{verdict}: the same-day straddle prices a 30-minute move {load_words}, higher than {rank.higher_than} of the last "
           f"{rank.of} sessions at {_clock(scene.now)} ({_fifth(rank)}); {event_words}; this morning's sigma ruler is {swell_words}"
           f"{_ruled(anchor)}")


# ----------------------------------------------------------------------------- realized movement against the clock

def _realized_30(bars: list[dict], then: datetime, points: float | None) -> float | None:
    """The last 30 minutes' realized swing at ``then``, in sigma of ``points``: the square root of the summed
    squared 1-minute close changes of the bars finished in the window (at least REALIZED_MIN_BARS), from the
    close before it, or from the open when the window reaches back to the bell."""
    start = then - timedelta(minutes=REALIZED_WINDOW_MIN)
    win = bars_finished_between(bars, start, then)
    if not points or len(win) < REALIZED_MIN_BARS:
        return None
    before = [b for b in bars if bar_time(b) + ONE_MINUTE <= start]
    closes = [float(before[-1]["close"]) if before else float(win[0]["open"])] + [float(b["close"]) for b in win]
    return math.sqrt(sum((b - a) ** 2 for a, b in zip(closes, closes[1:]))) / points


def _stillness(scene: Scene, points: float) -> str:
    """When the last real move (MOVE_RULE_SIGMA within REAL_MOVE_MIN minutes) ended and how long the tape was
    still before it began, with whether the last one is inside the last 30 minutes."""
    bars = scene.bars
    closes = [float(b["close"]) for b in bars]
    moved = [i for i in range(REAL_MOVE_MIN, len(closes)) if abs(closes[i] - closes[i - REAL_MOVE_MIN]) / points >= MOVE_RULE_SIGMA]
    rule = f"{MOVE_RULE_SIGMA:g} sigma within {REAL_MOVE_MIN} minutes"
    if not moved:
        return f"price has made no real move ({rule}) today, none for at least the last {WINDOW_30_MIN} minutes"
    last = moved[-1]
    ago = (scene.now - (bar_time(bars[last]) + ONE_MINUTE)).total_seconds() / 60.0
    ended = "under a minute ago" if ago < 1 else f"{plural(round(ago), 'minute')} ago"
    when = f"inside the last {WINDOW_30_MIN} minutes" if ago < WINDOW_30_MIN else f"at least {WINDOW_30_MIN} minutes ago"
    first = last
    while first - 1 in moved:
        first -= 1
    earlier = [i for i in moved if i < first]
    if earlier:
        still = round((bar_time(bars[first]) - bar_time(bars[earlier[-1]])).total_seconds() / 60.0)
        before = f"before that there was none for {plural(still, 'minute')}"
    else:
        before = "before that there was none since the open"
    return f"the last real move ({rule}) ended {ended}, {when}; {before}"


def _realized_vs_clock(scene: Scene, ls: LabelSet) -> None:
    """The last 30 minutes' realized swing in the morning anchor, ranked against the same half hour on the
    prior sessions (vol.realized_vs_clock) and as a multiple of their median (vol.realized_vs_clock_rank)."""
    ruler = sigma_anchor(scene)
    value = _realized_30(scene.bars, scene.now, ruler.points if ruler else None)
    if value is None:
        for path in ("vol.realized_vs_clock", "vol.realized_vs_clock_rank"):
            ls.omit(path, f"needs the morning sigma ruler and {REALIZED_MIN_BARS} finished bars in the last {REALIZED_WINDOW_MIN} minutes")
        return
    base = same_clock_values(scene, _realized_30)
    rank = rank_against(value, base)
    if rank is None:
        for path in ("vol.realized_vs_clock", "vol.realized_vs_clock_rank"):
            ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions with bars at this minute, have {len(base)}")
        return
    if rank.higher_than == rank.of:
        standing = f"more than every one of the last {rank.of} sessions at this time of day"
    elif rank.share >= TOP_FIFTH or rank.share <= BOTTOM_FIFTH:
        standing = f"more than {rank.higher_than} of the last {rank.of} sessions at this time of day, {_fifth(rank)}"
    else:
        standing = f"more than {rank.higher_than} of the last {rank.of} sessions at this time of day, between the bottom and top fifths"
    ls.put("vol.realized_vs_clock",
           f"over the last {REALIZED_WINDOW_MIN} minutes SPX's realized swing was {sig(value)}, {standing}; "
           f"{_stillness(scene, ruler.points)}{_ruled(ruler)}")
    usual = statistics.median(base)
    if usual <= 0:
        ls.omit("vol.realized_vs_clock_rank", "the prior sessions' usual swing at this minute is zero")
        return
    pace = value / usual
    words = (f"{max(pace, RV_HOT + 0.01):.2f} times the usual pace for this half hour (its median on the last {len(base)} sessions "
             f"at this time of day), past the {RV_HOT:g} hot line" if pace > RV_HOT else
             f"{pace:.2f} times the usual pace for this half hour (its median on the last {len(base)} sessions at this time of day), "
             f"at or under the {RV_HOT:g} hot line")
    ls.put("vol.realized_vs_clock_rank", f"the last {REALIZED_WINDOW_MIN} minutes moved {words}{_ruled(ruler)}")


# ----------------------------------------------------------------------------- the VIX family around SPX (the context job)

def _quote(market: MarketContext | None, symbol: str, t: datetime) -> float | None:
    return market.last(symbol, t, max_age_min=QUOTE_MAX_AGE_MIN) if market else None


def _front_gap(market: MarketContext | None, t: datetime) -> float | None:
    """Nine-day VIX less VIX at ``t``, in points."""
    front, vix = _quote(market, "$VIX9D", t), _quote(market, "$VIX", t)
    return None if front is None or vix is None else front - vix


def _front_fear_shift(scene: Scene, ls: LabelSet) -> None:
    """The 30-minute change in nine-day VIX less VIX, against the shift rule."""
    now, then = _front_gap(scene.market, scene.now), _front_gap(scene.market, scene.now - timedelta(minutes=WINDOW_30_MIN))
    if now is None or then is None:
        ls.omit("vol.front_fear_shift", "no $VIX9D and $VIX in the market context now and 30 minutes ago (the context job)")
        return
    d = now - then
    if abs(d) > FRONT_SHIFT_PTS:
        shown, words = max(abs(d), FRONT_SHIFT_PTS + 0.01), f"beyond the {FRONT_SHIFT_PTS:g}-point rule"
    else:
        shown, words = abs(d), f"within the {FRONT_SHIFT_PTS:g}-point rule"

    def side(gap: float) -> str:
        return f"{abs(gap):.2f} {'under' if gap < 0 else 'over'} it"

    ls.put("vol.front_fear_shift",
           f"over the last 30 minutes nine-day VIX {'rose' if d >= 0 else 'fell'} {shown:.2f} points against the 30-day VIX, "
           f"from {side(then)} to {side(now)}, {words}")


def _vvix_with_move(scene: Scene, ls: LabelSet) -> None:
    """The 30-minute SPX move against the pairing rule, and which way VVIX went meanwhile."""
    ruler = sigma_anchor(scene)
    move = _spx_move(scene, WINDOW_30_MIN, ruler) if ruler else None
    a, b = _quote(scene.market, "$VVIX", scene.now - timedelta(minutes=WINDOW_30_MIN)), _quote(scene.market, "$VVIX", scene.now)
    if move is None or a is None or b is None:
        ls.omit("vol.vvix_with_move", "needs the morning sigma ruler, a finished bar 30 minutes ago and $VVIX in the market context now "
                                      "and then (the context job)")
        return
    if abs(move) > PAIR_MOVE_SIGMA:
        size = f"{sig(max(abs(move), PAIR_MOVE_SIGMA + 0.01))}, past the {PAIR_MOVE_SIGMA:g} sigma pairing rule"
    else:
        size = f"{sig(abs(move))}, within the {PAIR_MOVE_SIGMA:g} sigma pairing rule"
    vvix = f"held at {b:.2f}" if b == a else f"{'rose' if b > a else 'fell'} {abs(b - a):.2f} points, from {a:.2f} to {b:.2f}"
    ls.put("vol.vvix_with_move", f"over the last 30 minutes SPX {'fell' if move < 0 else 'rose'} {size}, and VVIX {vvix}{_ruled(ruler)}")


def _half_hour(market: MarketContext | None, bars: list[dict], t: datetime, points: float) -> tuple[float, float, float, float] | None:
    """``(SPX move in sigma, VIX change, VVIX change, VIX)`` over the 30 minutes to ``t``; None when a piece is missing."""
    start = t - timedelta(minutes=WINDOW_30_MIN)
    p0, p1 = close_at(bars, start), close_at(bars, t)
    vix0, vix1 = _quote(market, "$VIX", start), _quote(market, "$VIX", t)
    vv0, vv1 = _quote(market, "$VVIX", start), _quote(market, "$VVIX", t)
    if None in (p0, p1, vix0, vix1, vv0, vv1):
        return None
    return (p1 - p0) / points, vix1 - vix0, vv1 - vv0, vix1


def _fit_two(xs: list[tuple[float, float]], ys: list[float]) -> tuple[float, float, float] | None:
    """Least squares ``y = a + b x1 + c x2``: ``(a, b, c)``, or None when the two inputs do not vary apart."""
    n = len(ys)
    m1, m2, my = sum(x[0] for x in xs) / n, sum(x[1] for x in xs) / n, sum(ys) / n
    s11 = sum((x[0] - m1) ** 2 for x in xs)
    s22 = sum((x[1] - m2) ** 2 for x in xs)
    s12 = sum((x[0] - m1) * (x[1] - m2) for x in xs)
    s1y = sum((x[0] - m1) * (y - my) for x, y in zip(xs, ys))
    s2y = sum((x[1] - m2) * (y - my) for x, y in zip(xs, ys))
    det = s11 * s22 - s12 * s12
    if det <= 1e-12:
        return None
    b, c = (s1y * s22 - s2y * s12) / det, (s2y * s11 - s1y * s12) / det
    return my - b * m1 - c * m2, b, c


def _vvix_vs_vix(scene: Scene, ls: LabelSet) -> None:
    """VVIX's 30-minute change less what SPX's move and VIX's change explain, fitted on the prior sessions' half
    hours, its size ranked against the fit's own leftovers."""
    path = "vol.vvix_vs_vix"
    ruler = sigma_anchor(scene)
    now = _half_hour(scene.market, scene.bars, scene.now, ruler.points) if ruler else None
    if now is None:
        ls.omit(path, "needs the morning sigma ruler, bars 30 minutes apart and $VIX and $VVIX in the market context now and then "
                      "(the context job)")
        return
    xs, ys, sessions = [], [], 0
    for d in _ranked_prior_days(scene):
        prior, anchor = scene.prior_markets.get(d), scene.prior_rulers.get(d)
        if prior is None or anchor is None:
            continue
        first = datetime.combine(date.fromisoformat(d), VVIX_FIT_FIRST, tzinfo=ET)
        reads = [r for k in range(VVIX_FIT_READS)
                 if (r := _half_hour(prior, scene.prior_bars[d], first + timedelta(minutes=WINDOW_30_MIN * k), anchor.points))]
        sessions += bool(reads)
        xs += [(r[0], r[1]) for r in reads]
        ys += [r[2] for r in reads]
    fit = _fit_two(xs, ys) if sessions >= MIN_RANK_SESSIONS else None
    if fit is None:
        ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions of $VIX and $VVIX to fit against, have {sessions}")
        return
    a, b, c = fit
    move, dvix, dvvix, vix = now
    explained = a + b * move + c * dvix
    left = dvvix - explained
    rank = rank_against(abs(left), [abs(y - (a + b * x1 + c * x2)) for (x1, x2), y in zip(xs, ys)])
    band = ("top fifth" if rank.share >= TOP_FIFTH else "at or above the median, short of the top fifth" if rank.share >= HALF_RANK
            else "under the median")
    if abs(move) < MOVE_RULE_SIGMA and abs(dvix) / vix < VIX_STILL_PCT:
        others = f"while SPX and VIX barely moved (SPX under the {MOVE_RULE_SIGMA:g} sigma move rule, VIX under the {_line(VIX_STILL_PCT)} still line)"
    else:
        others = f"while SPX or VIX also moved (SPX at or past the {MOVE_RULE_SIGMA:g} sigma move rule, or VIX at or past the {_line(VIX_STILL_PCT)} still line)"
    ls.put(path,
           f"over the last 30 minutes VVIX {'rose' if dvvix >= 0 else 'fell'} {abs(dvvix):.2f} points; VIX's and SPX's moves explain "
           f"a {'rise' if explained >= 0 else 'fall'} of about {abs(explained):.2f}; the {abs(left):.2f} left over is bigger than "
           f"{rank.higher_than} of {rank.of} half hours on the last {sessions} sessions, {band}, {others}{_ruled(ruler)}")


def _vix_curve(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX against three-month VIX (the diary's vix_ts) against the near-flat and inversion lines, ranked
    against the prior sessions at this minute, and nine-day VIX against VIX."""
    path = "vol.term_structure"
    ratio, front, vix = today[-1].vix_ts, _quote(scene.market, "$VIX9D", scene.now), _quote(scene.market, "$VIX", scene.now)
    if ratio is None:
        ls.omit(path, "row carries no VIX against three-month VIX (vix_ts)")
        return
    if front is None or vix is None:
        ls.omit(path, "no $VIX9D and $VIX in the market context (the context job)")
        return
    if scene.state_dir is None:
        ls.omit(path, "no state folder to read the prior sessions' diaries from")
        return
    diaries = {d: prior_diary(scene.state_dir, d) for d in scene.prior_bars}
    base = [p.vix_ts for d in _ranked_prior_days(scene) if (p := point_at(diaries[d], _same_clock(scene.now, d))) and p.vix_ts]
    rank = rank_against(ratio, base)
    if rank is None:
        ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions with the VIX curve at this minute, have {len(base)}")
        return
    if ratio >= VIX_CURVE_FLAT:
        level = f"{ratio:.2f} times three-month VIX, at or over the {VIX_CURVE_FLAT:.2f} inversion line"
    elif ratio >= VIX_CURVE_NEAR_FLAT:
        level = (f"{min(ratio, VIX_CURVE_FLAT - 0.01):.2f} times three-month VIX, at or over the {VIX_CURVE_NEAR_FLAT:.2f} near-flat line "
                 f"and under the {VIX_CURVE_FLAT:.2f} inversion line")
    else:
        level = f"{min(ratio, VIX_CURVE_NEAR_FLAT - 0.01):.2f} times three-month VIX, under the {VIX_CURVE_NEAR_FLAT:.2f} near-flat line"
    nine = front / vix
    nine_words = (f"{nine:.2f} of VIX, at or over the inversion line" if nine >= VIX_CURVE_FLAT
                  else f"{min(nine, VIX_CURVE_FLAT - 0.01):.2f} of VIX, under the inversion line")
    inverted = sum(1 for points in diaries.values() if any(p.vix_ts and p.vix_ts >= VIX_CURVE_FLAT for p in points))
    record = (f"the curve has not inverted on any of the last {len(diaries)} sessions" if not inverted
              else f"the curve inverted on {inverted} of the last {len(diaries)} sessions")
    ls.put(path, f"VIX is {level}; flatter than {rank.higher_than} of the last {rank.of} sessions at this time, {_fifth(rank)}; "
                 f"nine-day VIX is {nine_words}; {record}")


# ----------------------------------------------------------------------------- a stress day's path

def _stress_path(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """On a stress day only (STRESS_VIX_RISE_PTS, the prior sessions' VIX high, STRESS_CURVE), how much of its
    rise VIX has given back from its session high, how far SPX sits off its session low in normal-day sigma,
    and how often NYSE TICK hit its selling extreme in the last 30 minutes. Any other day it is omitted, so
    its question sleeps."""
    path = "vol.stress_path"
    now, opened, curve = today[-1].vix, today[0].vix, today[-1].vix_ts
    if now is None or opened is None:
        ls.omit(path, "no diary VIX now and at the open (range_ruler.vol_carry.vix)")
        return
    prior = [p.vix for d in scene.prior_bars for p in prior_diary(scene.state_dir, d) if p.vix] if scene.state_dir else []
    prior_high = max(prior) if prior else None
    rise = now - opened
    if not (rise >= STRESS_VIX_RISE_PTS or (prior_high is not None and now > prior_high) or (curve is not None and curve >= STRESS_CURVE)):
        high_words = (f"at or under its {prior_high:.2f} high of the prior sessions" if prior_high is not None
                      else "with no prior diaries to compare it with")
        curve_words = f", and {curve:.2f} times three-month VIX, under {STRESS_CURVE:g}" if curve is not None else ""
        ls.omit(path, f"not a stress day: VIX is {rise:+.2f} points from its open, under {STRESS_VIX_RISE_PTS:g}, {high_words}{curve_words}")
        return
    lows, normal = session_extremes(scene.bars), normal_day_sigma(scene)
    if lows is None or normal is None:
        ls.omit(path, f"needs today's bars and {MIN_RANK_SESSIONS} prior sessions' trusted anchors for the normal-day sigma")
        return
    start = scene.now - timedelta(minutes=WINDOW_30_MIN)
    tick_bars = scene.market.bars_between("$TICK", start, scene.now) if scene.market else []
    ticks = [float(b["low"]) for b in tick_bars] or (scene.market.between("$TICK", start, scene.now) if scene.market else [])
    if not ticks:
        ls.omit(path, "no NYSE TICK in the market context over the last 30 minutes (the context job)")
        return
    peak = max(today, key=lambda p: p.vix or 0.0)            # max keeps the first of equal highs
    if peak.vix <= opened:
        vix_words = "VIX has not been above its open today"
    else:
        back = (peak.vix - now) / (peak.vix - opened)
        if back >= STRESS_RETREAT_SHARE:
            back_words = f"{pct(back)} of its rise from there, past the {pct(STRESS_RETREAT_SHARE)} retreat share"
        elif back >= STRESS_HOLD_SHARE:
            back_words = (f"{pct(_in_band(back, STRESS_HOLD_SHARE, STRESS_RETREAT_SHARE, 0.01))} of its rise from there, short of the "
                          f"{pct(STRESS_RETREAT_SHARE)} retreat share and at or past the {pct(STRESS_HOLD_SHARE)} hold share")
        else:
            back_words = f"{pct(min(back, STRESS_HOLD_SHARE - 0.01))} of its rise from there, short of the {pct(STRESS_HOLD_SHARE)} hold share"
        at_high = ", and is at its session high now" if back <= 0 else ""
        vix_words = f"session high {peak.vix:.1f} at {_clock(peak.ts)}, it has given back {back_words}{at_high}"
    off_low = (scene.spot - lows.low) / normal
    if off_low <= 0:                                          # spot sits at or under the newest finished bar's low
        low_words = f"at its session low now, within the {NEAR_LOW_SIGMA:g} near-low line"
    elif off_low >= BOUNCE_SIGMA:
        low_words = f"{off_low:.2f} normal-day sigma above its session low from {_clock(lows.low_at)}, past the {BOUNCE_SIGMA:g} bounce line"
    elif off_low > NEAR_LOW_SIGMA:
        low_words = (f"{_in_band(off_low, NEAR_LOW_SIGMA + 0.01, BOUNCE_SIGMA, 0.01):.2f} normal-day sigma above its session low from "
                     f"{_clock(lows.low_at)}, beyond the {NEAR_LOW_SIGMA:g} near-low line and short of the {BOUNCE_SIGMA:g} bounce line")
    else:
        low_words = f"{off_low:.2f} normal-day sigma above its session low from {_clock(lows.low_at)}, within the {NEAR_LOW_SIGMA:g} near-low line"
    hits = sum(1 for t in ticks if t <= -TICK_EXTREME)
    cluster = f"at least the {TICK_CLUSTER}-reading cluster" if hits >= TICK_CLUSTER else f"short of the {TICK_CLUSTER}-reading cluster"
    ls.put(path, f"VIX {now:.1f}, {'up' if rise >= 0 else 'down'} {abs(rise):.1f} points since the open; {vix_words}; SPX sits {low_words}; "
                 f"NYSE TICK printed at or below -{TICK_EXTREME} {plural(hits, 'time')} in 30 minutes, {cluster}")


# ----------------------------------------------------------------------------- the same-day skew (the lob-flow tape)

def _skew(scene: Scene, ls: LabelSet) -> None:
    """The skew labels from the tape's quotes in the minute to this read's last whole minute."""
    paths = ("skew.put_tilt_vs_usual", "skew.shift_vs_price")
    at_min = minute_floor(scene.now)
    then = at_min - timedelta(minutes=WINDOW_30_MIN)
    smiles = skew_at(scene.state_dir, scene.day, (at_min, then)) if scene.state_dir else None
    if smiles is None:
        why = f"no lob-flow tape for {scene.day} (state/lob_flow/raw)"
        for path in paths:
            ls.omit(path, why)
        ls.sleep("put_tilt_vs_clock", why)
        return
    _put_tilt(scene, smiles[at_min], at_min, ls)
    _skew_shift(scene, smiles[then], smiles[at_min], then, at_min, ls)


def _tilt(smile: Skew | None) -> float | None:
    """25-delta put vol less 25-delta call vol, as a share of at-the-money vol."""
    return None if smile is None or smile.put_25 is None or smile.call_25 is None else (smile.put_25 - smile.call_25) / smile.atm


def _wing(smile: Skew | None) -> float | None:
    """10-delta put vol less 25-delta put vol, as a share of at-the-money vol."""
    return None if smile is None or smile.put_10 is None or smile.put_25 is None else (smile.put_10 - smile.put_25) / smile.atm


def _steepness(rank: SameClockRank) -> str:
    return ("past the steep rank" if rank.share >= SKEW_STEEP_RANK else "past the flat rank" if rank.share <= SKEW_FLAT_RANK
            else "within the usual range")


def _put_tilt(scene: Scene, smile: Skew | None, at_min: datetime, ls: LabelSet) -> None:
    """The 25-delta put-call tilt ranked against the prior sessions' at the same minute, with the far-put wing."""
    path, clock = "skew.put_tilt_vs_usual", _clock(at_min)
    tilt = _tilt(smile)
    if tilt is None:
        why = f"no fresh 25-delta put and call quotes on the lob-flow tape in the minute to {clock}"
        ls.omit(path, why)
        ls.sleep("put_tilt_vs_clock", why)
        return
    tilts, wings = [], []
    for d in _ranked_prior_days(scene):
        if len(tilts) == SKEW_RANK_SESSIONS:
            break
        t = minute_floor(_same_clock(scene.now, d))
        prior = (skew_at(scene.state_dir, d, (t,)) or {}).get(t)
        if (pt := _tilt(prior)) is not None:
            tilts.append(pt)
            if (pw := _wing(prior)) is not None:
                wings.append(pw)
    rank = rank_against(tilt, tilts)
    if rank is None:
        why = f"needs {MIN_RANK_SESSIONS} prior sessions with 25-delta quotes on the tape at {clock}, have {len(tilts)}"
        ls.omit(path, why)
        ls.sleep("put_tilt_vs_clock", why)
        return
    gap = (smile.put_25 - smile.call_25) * 100.0
    if gap < 0:
        lead = f"same-day 25-delta calls are priced {abs(gap):.1f} vol points above 25-delta puts, a call tilt"
    else:
        lead = f"same-day 25-delta puts are priced {gap:.1f} vol points above 25-delta calls, {tilt:.2f} of the at-the-money level"
    wing = _wing(smile)
    wing_rank = rank_against(wing, wings) if wing is not None else None
    if wing_rank is None:
        far = "far puts (10-delta) have no fresh quote or no history at this minute to compare"
    elif wing_rank.share >= SKEW_STEEP_RANK:
        far = "far puts (10-delta) are at a steeper premium than usual to 25-delta puts, past the steep rank"
    elif wing_rank.share <= SKEW_FLAT_RANK:
        far = "far puts (10-delta) are at a flatter premium than usual to 25-delta puts, past the flat rank"
    else:
        far = "far puts (10-delta) are at a usual premium to 25-delta puts"
    ls.put(path, f"{lead}; steeper than {rank.higher_than} of the last {rank.of} sessions at {clock}, {_steepness(rank)}; {far}")
    ls.wake("put_tilt_vs_clock")


def _skew_shift(scene: Scene, before: Skew | None, now: Skew | None, then: datetime, at_min: datetime, ls: LabelSet) -> None:
    """The 30-minute change in the put-call tilt one remaining standard deviation either side of the forward,
    less what price's move explains (SKEW_PER_SIGMA)."""
    path = "skew.shift_vs_price"
    tilts = [None if s is None or s.put_1sd is None or s.call_1sd is None else (s.put_1sd - s.call_1sd) / s.atm for s in (before, now)]
    ruler = sigma_anchor(scene)
    p0, p1 = close_at(scene.bars, then), close_at(scene.bars, at_min)
    if None in tilts or ruler is None or p0 is None or p1 is None:
        ls.omit(path, f"needs quotes one remaining standard deviation either side on the lob-flow tape at {_clock(then)} and "
                      f"{_clock(at_min)}, the bars then and the morning sigma ruler")
        return
    move = (p1 - p0) / ruler.points
    d = tilts[1] - tilts[0]
    explained = SKEW_PER_SIGMA * move
    resid = d - explained
    if abs(resid) > SKEW_RESID_CUT:
        shown, words = max(abs(resid), SKEW_RESID_CUT + 0.001), f"past the {SKEW_RESID_CUT:g} line"
    else:
        shown, words = abs(resid), f"inside the {SKEW_RESID_CUT:g} line"
    ls.put(path,
           f"over the last 30 minutes the gap between same-day put and call prices {'widened' if d >= 0 else 'narrowed'} {abs(d):.3f} of "
           f"at-the-money vol while price {'fell' if move < 0 else 'rose'} {sig(abs(move))}; price explains a "
           f"{'widening' if explained >= 0 else 'narrowing'} of {abs(explained):.3f}, so puts got {'dearer' if resid >= 0 else 'cheaper'} "
           f"by {shown:.3f} beyond the move, {words}{_ruled(ruler)}")
