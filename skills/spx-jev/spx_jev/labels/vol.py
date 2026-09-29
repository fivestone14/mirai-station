"""The volatility family: implied volatility, realized against priced movement, the expected move and the
VIX curve (iv.*), and the VIX family, the straddle and the skew (vol.*, skew.*).

The final question set's labels are measured on the morning anchor (rulers.sigma_anchor); the ones built
before it keep the row's sigma. They read the diary rows (the VIX, at-the-money vol, the straddle left and
the VIX curve), the prior sessions' diaries and the lob-flow tape (vol_sources), the minute bars, and the
context job's VIX family and NYSE TICK; a shock is the events and shocks family's own burst. Each set
label's sentence, how it is computed and its source are in spec/question_set.json ``labels``.

Every set label judges its measure by its rank against the same measure at the same minute on up to the
last 20 sessions (ranks.rank_sessions, needing SAME_CLOCK_MIN_SESSIONS of them), never by a fixed line: a
diary measure against the prior sessions' diaries as their reads at that minute had them, a price measure
in each session's own morning ruler. A signed measure is ranked with its named side highest (a VIX rise, a
richer straddle), so its top third is that side and its bottom third the other."""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .. import events
from ..cuts import (EVENT_DIGEST_MIN, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, HALF_RANK, IV_FLAT_BAND_PTS, MIN_RANK_SESSIONS,
                    NIGHT_RANK_COUNT, REALIZED_QUIET_RATIO, REALIZED_WILD_RATIO, SAME_CLOCK_MIN_SESSIONS, SHOCK_LOOKBACK_MIN,
                    SKEW_FLAT_RANK, SKEW_STEEP_RANK, STRESS_HOLD_SHARE, STRESS_RETREAT_SHARE, TICK_BURST_PCT, TICK_CLUSTER,
                    TOP_FIFTH, VIX_CURVE_FLAT, WINDOW_10_MIN, WINDOW_30_MIN, ZERO_DTE_LAST_HOUR_MIN)
from ..sessions import next_trading_day, session_close, session_minutes
from ..state_builder import BAR_CLOCK_OWN_KEYS, MarketContext, Scene, first_row, row_days
from .events_shocks import judged_windows, shock_bursts
from .label_set import LabelSet
from .measures import (ET, ONE_MINUTE, bar_time, bars_finished_between, close_at, day_high_low, is_num, move_size,
                       session_extremes, settled_open)
from .plausible import left_out
from .ranks import (SameClockRank, fifth, move_rank, rank_days, rank_sessions, same_clock_market, same_clock_values,
                    tick_bands_by_minute, tick_bursts)
from .rulers import SigmaRuler, sigma_anchor
from .vol_sources import (ROW_MAX_GAP, DiaryPoint, Skew, diary_point, minute_floor, point_at, prior_diary, prior_diary_by,
                          skew_at)
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
# A real move, for whether the half hour was still: its largest move over this many minutes above the bottom
# third of the same half hour's largest on the prior sessions.
REAL_MOVE_MIN = 10
# The context job snapshots the VIX family every minute: a value older than this means it stopped.
QUOTE_MAX_AGE_MIN = 5
# VVIX's leftover move is fitted on the prior sessions' half hours ending 10:30 to 15:30 (11 a session).
VVIX_FIT_FIRST, VVIX_FIT_READS = time(10, 30), 11
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
    if _restamped(scene):
        today[-1] = today[-2]
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
    _vvix_vs_vix(scene, today, ls)
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


def _price_move(bars: list[dict], then: datetime, window: int, points: float | None) -> float | None:
    """A prior session's _spx_move: its close at ``then`` against the close of the last bar finished ``window``
    minutes before, in sigma of its own ruler ``points``."""
    ref, last = close_at(bars, then - timedelta(minutes=window)), close_at(bars, then)
    return (last - ref) / points if points and ref is not None and last is not None else None


def _restamped(scene: Scene) -> bool:
    """Whether the read's row is the newest diary row restamped at a bar's close (state_builder.bar_clock_row): its
    diary values are that row's, read when that row was written, not at the bar."""
    rows = scene.rows_today
    return scene.bar_clock and len(rows) > 1 and all(rows[-1].get(k) == rows[-2].get(k) for k in {*rows[-1], *rows[-2]}
                                                      if k not in BAR_CLOCK_OWN_KEYS)


def _window_start(points: Sequence[DiaryPoint], now: datetime, window: int) -> DiaryPoint | None:
    """The point a diary window of ``window`` minutes to ``now`` starts at, or None. A window that starts before the
    day's first row, by no more than a row's gap, starts at it: the 09:40 read's ten minutes run from the open's first
    print."""
    start = now - timedelta(minutes=window)
    return point_at(points, start) or (points[0] if start < points[0].ts <= start + ROW_MAX_GAP else None)


def _change(points: Sequence[DiaryPoint], now: datetime, window: int,
            read: Callable[[DiaryPoint], float | None]) -> tuple[float, float] | None:
    """``(then, now)`` of a diary value across the last ``window`` minutes, from the day's points up to ``now``;
    None when either end is missing, or when the window starts at the newest point, which would read one row twice
    and call it unchanged. On a restamped read (_restamped) the newest point is its diary row's own."""
    then = _window_start(points, now, window)
    a, b = (read(then) if then and then is not points[-1] else None), read(points[-1])
    return None if a is None or b is None else (a, b)


def _no_window(points: Sequence[DiaryPoint], now: datetime, window: int, what: str) -> str:
    """Why a diary window has no change: its start is the newest row itself, or ``what``."""
    if _window_start(points, now, window) is points[-1]:
        return (f"the diary's newest row, {points[-1].ts.astimezone(ET):%H:%M}, is also its row {window} minutes ago: "
                f"no second reading to measure a change across")
    return what


def _diary_base(scene: Scene, measure: Callable[[Sequence[DiaryPoint], list[dict], datetime, float | None], float | None]) -> list[float]:
    """``measure(points, bars, then, sigma)`` on each prior session at this read's clock minute, newest first: its
    bars and its own ruler as ranks.same_clock_values gives them, ``points`` its diary as its read at that minute
    had it (prior_diary_by). A session without a diary then is skipped."""
    def at_then(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
        points = prior_diary_by(scene.state_dir, then)
        return measure(points, bars, then, sigma) if points else None

    return same_clock_values(scene, at_then)


def _diary_rank(scene: Scene, value: float, measure: Callable[[Sequence[DiaryPoint], list[dict], datetime, float | None], float | None],
                what: str) -> tuple[SameClockRank | None, str | None]:
    """``value`` against the same diary measure at this minute on the prior sessions (_diary_base), under the
    owner's rank rule (ranks.rank_sessions): the rank, or the reason there is none."""
    if scene.state_dir is None:
        return None, "no state folder to read the prior sessions' diaries from"
    return rank_sessions(value, _diary_base(scene, measure), what)


def _signed_standing(rank: SameClockRank, highest: str, span: str = "at this minute") -> str:
    """A signed measure's rank in words, naming the side ranked highest: "ranked with a rise highest, higher than
    15 of the last 20 sessions at this minute, top third"."""
    return f"ranked with {highest} highest, higher than {rank.higher_than} of the last {rank.of} sessions {span}, {rank.band}"


def _vix_share(points: Sequence[DiaryPoint], now: datetime, window: int) -> float | None:
    """VIX's move over the ``window`` minutes to ``now`` as a share of its level, either way (_change)."""
    got = _change(points, now, window, lambda p: p.vix)
    return None if got is None else abs(got[1] - got[0]) / got[1]


def _vix_share_rank(scene: Scene, share: float, window: int) -> tuple[SameClockRank | None, str | None]:
    """VIX's ``window``-minute move as a share of its level against the same window to this minute on the prior
    sessions: the rank vix_stir reads and vvix_hidden_stir's 'VIX barely moved'."""
    return _diary_rank(scene, share, lambda points, bars, then, sigma: _vix_share(points, then, window),
                       f"a diary VIX at this minute and {window} minutes before")


def _stir(rank: SameClockRank) -> str:
    """vix_stir's four levels in words: the thirds, with the top fifth apart from the rest of the top third."""
    if rank.share >= TOP_FIFTH:
        return "top fifth"
    return "top third, short of the top fifth" if rank.band == "top third" else rank.band


def _vix_change(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX's move over the window as a share of its level, ranked against the same window to this minute on the
    prior sessions: 30 minutes on the live lane, 10 on the opening lane."""
    window = WINDOW_10_MIN if scene.bar_clock else WINDOW_30_MIN
    got = _change(today, scene.now, window, lambda p: p.vix)
    if got is None:
        ls.omit("vol.vix_change_30", _no_window(today, scene.now, window,
                                                 f"no diary VIX now and about {window} minutes ago (range_ruler.vol_carry.vix)"))
        return
    then, now = got
    d = now - then
    rank, why = _vix_share_rank(scene, abs(d) / now, window)
    if rank is None:
        ls.omit("vol.vix_change_30", why)
        return
    how = "was unchanged" if d == 0 else f"{'rose' if d > 0 else 'fell'} {abs(d):.2f} points"
    ls.put("vol.vix_change_30", f"over the last {window} minutes VIX {how}, {_share(abs(d) / now)} of its {now:.1f} level, "
                                f"{rank.words()}, {_stir(rank)}")


def _since_1400(points: Sequence[DiaryPoint], now: datetime) -> tuple[float, float] | None:
    """``(then, now)`` of the diary VIX from the newest point by 14:00 on ``now``'s day to the newest by ``now``."""
    anchor = point_at(points, datetime.combine(now.astimezone(ET).date(), AFTERNOON_ANCHOR, tzinfo=ET))
    a, b = (anchor.vix if anchor else None), points[-1].vix
    return None if a is None or b is None else (a, b)


def _share_since_1400(points: Sequence[DiaryPoint], now: datetime) -> float | None:
    got = _since_1400(points, now)
    return None if got is None else (got[1] - got[0]) / got[1]


def _vix_since_1400(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX's change since 14:00 as a share of its level, signed, ranked against the same change to this minute on
    the prior sessions."""
    anchor_t = datetime.combine(scene.now.astimezone(ET).date(), AFTERNOON_ANCHOR, tzinfo=ET)
    if scene.now < anchor_t:
        ls.omit("vol.vix_since_1400", "before 14:00")
        return
    got = _since_1400(today, scene.now)
    if got is None:
        ls.omit("vol.vix_since_1400", "no diary VIX now and at 14:00 (range_ruler.vol_carry.vix)")
        return
    then, now = got
    d = now - then
    rank, why = _diary_rank(scene, d / now, lambda points, bars, t, sigma: _share_since_1400(points, t),
                            "a diary VIX at 14:00 and at this minute")
    if rank is None:
        ls.omit("vol.vix_since_1400", why)
        return
    how = "was unchanged" if d == 0 else f"{'rose' if d > 0 else 'fell'} {abs(d):.2f} points"
    ls.put("vol.vix_since_1400", f"since 14:00 VIX {how}, {_share(abs(d) / now)} of its {now:.1f} level; "
                                 f"{_signed_standing(rank, 'a rise', 'from 14:00 to this minute')}")


def _vix_left(points: Sequence[DiaryPoint], bars: list[dict], then: datetime, sigma: float | None) -> float | None:
    """A prior session's vol.vix_vs_price at ``then``: VIX's 30-minute change less what SPX's move alone explains
    (VIX_PER_SIGMA), as a share of VIX."""
    got, move = _change(points, then, WINDOW_30_MIN, lambda p: p.vix), _price_move(bars, then, WINDOW_30_MIN, sigma)
    return None if got is None or move is None else (got[1] - got[0] - VIX_PER_SIGMA * move) / got[1]


def _vix_vs_price(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX's 30-minute change less what price's move alone would do to it (VIX_PER_SIGMA), as a share of VIX,
    ranked against the same leftover at this minute on the prior sessions."""
    ruler = sigma_anchor(scene)
    got = _change(today, scene.now, WINDOW_30_MIN, lambda p: p.vix)
    move = _spx_move(scene, WINDOW_30_MIN, ruler) if ruler else None
    if ruler is None or got is None or move is None:
        ls.omit("vol.vix_vs_price", _no_window(today, scene.now, WINDOW_30_MIN, "needs the morning sigma ruler, a finished bar 30 minutes "
                                                                                 "ago and the diary VIX now and then"))
        return
    then, now = got
    d = now - then
    explained = VIX_PER_SIGMA * move
    resid = d - explained
    rank, why = _diary_rank(scene, resid / now, _vix_left, "a diary VIX, a ruler and bars at this minute and 30 minutes before")
    if rank is None:
        ls.omit("vol.vix_vs_price", why)
        return
    ls.put("vol.vix_vs_price",
           f"over the last 30 minutes VIX ended {abs(resid):.2f} points ({_share(abs(resid) / now)} of VIX) {'above' if resid >= 0 else 'below'} "
           f"what price explains: it {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points, and the {abs(move):.2f} sigma SPX "
           f"{'drop' if move < 0 else 'rise'} alone would {'lift' if explained >= 0 else 'lower'} it about {abs(explained):.2f}; "
           f"{_signed_standing(rank, 'VIX furthest above what price explains')}{_ruled(ruler)}")


def _iv_left(points: Sequence[DiaryPoint], bars: list[dict], then: datetime, sigma: float | None) -> float | None:
    """A prior session's vol.atm_iv_residual at ``then``: same-day at-the-money vol's 30-minute change in vol points
    less what SPX's move alone explains (ATM_IV_PER_SIGMA)."""
    got, move = _change(points, then, WINDOW_30_MIN, lambda p: p.atm_iv), _price_move(bars, then, WINDOW_30_MIN, sigma)
    return None if got is None or move is None else (got[1] - got[0]) * 100.0 - ATM_IV_PER_SIGMA * move


def _atm_iv_residual(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """Same-day at-the-money vol's 30-minute change less what price's move alone would do to it (ATM_IV_PER_SIGMA),
    ranked against the same leftover at this minute on the prior sessions."""
    if scene.minutes_to_close < ZERO_DTE_LAST_HOUR_MIN:
        ls.omit("vol.atm_iv_residual", "the last hour of the 0DTE book: its implied volatility follows the expiry clock, not the market",
                ended=True)
        return
    ruler = sigma_anchor(scene)
    got = _change(today, scene.now, WINDOW_30_MIN, lambda p: p.atm_iv)
    move = _spx_move(scene, WINDOW_30_MIN, ruler) if ruler else None
    if ruler is None or got is None or move is None:
        ls.omit("vol.atm_iv_residual", _no_window(today, scene.now, WINDOW_30_MIN, "needs the morning sigma ruler, a finished bar 30 "
                                                                                    "minutes ago and at-the-money vol on the rows now and then"))
        return
    then, now = got
    d = (now - then) * 100.0
    explained = ATM_IV_PER_SIGMA * move
    resid = d - explained
    rank, why = _diary_rank(scene, resid, _iv_left, "at-the-money vol, a ruler and bars at this minute and 30 minutes before")
    if rank is None:
        ls.omit("vol.atm_iv_residual", why)
        return
    ls.put("vol.atm_iv_residual",
           f"over the last 30 minutes same-day at-the-money vol {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points while price "
           f"{'fell' if move < 0 else 'rose'} {sig(abs(move))}; the {'fall' if move < 0 else 'rise'} alone explains a "
           f"{'rise' if explained >= 0 else 'fall'} of {abs(explained):.2f}, so vol ended {abs(resid):.2f} points "
           f"{'above' if resid >= 0 else 'below'} what price explains; {_signed_standing(rank, 'vol furthest above what price explains')}"
           f"{_ruled(ruler)}")


def _opening_surprise(first: float, last: float, opened: float, close: float, points: float) -> tuple[float, float, float]:
    """``(gap, implied, surprise)``: the gap from yesterday's close to the settled open in sigma of ``points``, where
    that gap alone leaves VIX from yesterday's last (VIX_PER_GAP_SIGMA), and VIX's first print less that."""
    gap = (opened - close) / points
    implied = last + VIX_PER_GAP_SIGMA * gap
    return gap, implied, first - implied


def _last_vix(state_dir: Path, day: str) -> float | None:
    return next((p.vix for p in reversed(prior_diary(state_dir, day)) if p.vix), None)


def _prior_surprises(scene: Scene) -> list[float]:
    """vol.vix_overnight_surprise's surprise on each prior session, newest first: its first diary VIX against the
    session before's last, after its own gap (its settled open against the prior close on its first row, in its
    own morning anchor). A session whose session before has no diary on file is left out."""
    days = row_days(scene.state_dir)
    out = []
    for day in rank_days(scene):
        before = max((d for d in days if d < day), default=None)
        points, ruler, opened = prior_diary(scene.state_dir, day), scene.prior_rulers.get(day), settled_open(scene.prior_bars[day])
        if before is None or next_trading_day(date.fromisoformat(before)).isoformat() != day or not points or points[0].vix is None:
            continue
        row = first_row(scene.state_dir, day)
        close, last = (row[0].get("prior_close") if row else None), _last_vix(scene.state_dir, before)
        if ruler is not None and opened is not None and is_num(close) and last is not None:
            out.append(_opening_surprise(points[0].vix, last, opened, float(close), ruler.points)[2])
    return out


def _vix_overnight_surprise(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """Today's first diary VIX against the prior session's last, less what the gap alone would do to it
    (VIX_PER_GAP_SIGMA times the gap from yesterday's close to the settled open, in the morning anchor), ranked
    against the same surprise on the prior sessions."""
    first = today[0].vix
    prior = [d for d in row_days(scene.state_dir) if d < scene.day] if scene.state_dir else []
    if prior and next_trading_day(date.fromisoformat(prior[-1])).isoformat() != scene.day:
        ls.omit("vol.vix_overnight_surprise", f"the previous session's diary is not on file: its newest earlier day is {prior[-1]}")
        return
    last = _last_vix(scene.state_dir, prior[-1]) if prior else None
    opened, close, ruler = settled_open(scene.bars), scene.row.get("prior_close"), sigma_anchor(scene)
    if first is None or last is None:
        ls.omit("vol.vix_overnight_surprise", "needs the diary VIX on today's first row and the prior session's last")
        return
    if opened is None or not is_num(close) or ruler is None:
        ls.omit("vol.vix_overnight_surprise", "needs the settled open (the 09:34 bar's close), yesterday's close and the morning sigma ruler")
        return
    gap, implied, resid = _opening_surprise(first, last, opened, float(close), ruler.points)
    rank, why = rank_sessions(resid, _prior_surprises(scene), "a first and a last diary VIX, a settled open and yesterday's close")
    if rank is None:
        ls.omit("vol.vix_overnight_surprise", why)
        return
    ls.put("vol.vix_overnight_surprise",
           f"VIX's first print today was {first:.2f}, {abs(first - last):.2f} {'above' if first >= last else 'below'} yesterday's last "
           f"{last:.2f}; after this morning's {sig(abs(gap))} gap {'up' if gap >= 0 else 'down'} it would normally sit near {implied:.2f}, "
           f"so it is {abs(resid):.2f} points {'richer' if resid >= 0 else 'cheaper'} than the gap implies; ranked with richer highest, "
           f"higher than {rank.higher_than} of the last {rank.of} sessions' openings, {rank.band}{_ruled(ruler)}")


def _vix_left_since(points: Sequence[DiaryPoint], bars: list[dict], then: datetime, sigma: float | None, start: time) -> float | None:
    """A prior session's vol.vix_on_shock at ``then``: VIX's change from the diary point by ``start`` (a clock time)
    less what SPX's move over the same minutes explains (VIX_PER_SIGMA), in points."""
    lead = datetime.combine(then.date(), start, tzinfo=ET)
    before, ref, last = point_at(points, lead), close_at(bars, lead), close_at(bars, then)
    if not sigma or before is None or before.vix is None or points[-1].vix is None or ref is None or last is None:
        return None
    return points[-1].vix - before.vix - VIX_PER_SIGMA * (last - ref) / sigma


def _vix_on_shock(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX since just before the last hour's largest burst, less what price's move over the same span explains
    (VIX_PER_SIGMA), ranked against the same minutes on the prior sessions, shock or not. The burst is the shock
    family's own (shock.burst), found the same way, so both read one shock."""
    path = "vol.vix_on_shock"
    ruler = sigma_anchor(scene)
    if ruler is None:
        ls.omit(path, "no morning sigma ruler to find a shock with")
        return
    since = scene.now - timedelta(minutes=SHOCK_LOOKBACK_MIN)
    shocks = [b for b in shock_bursts(judged_windows(scene, ruler.points, EVENT_DIGEST_MIN)) if b.end > since]
    if not shocks:
        ls.omit(path, f"no shock in the last {SHOCK_LOOKBACK_MIN} minutes (shock.burst)", ended=True)
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
    start = lead.astimezone(ET).time()
    rank, why = _diary_rank(scene, resid, lambda points, bars, then, sigma: _vix_left_since(points, bars, then, sigma, start),
                            f"a diary VIX, a ruler and bars at {_clock(lead)} and at this minute")
    if rank is None:
        ls.omit(path, why)
        return
    ls.put(path,
           f"since just before the shock, at {_clock(lead)}, VIX ended {abs(resid):.2f} points {'above' if resid >= 0 else 'below'} what "
           f"price explains: it {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points, and the {sig(abs(move))} "
           f"{'drop' if move < 0 else 'rise'} alone would {'lift' if explained >= 0 else 'lower'} it {abs(explained):.2f}; "
           f"{_signed_standing(rank, 'VIX furthest above what price explains', f'from {_clock(lead)} to this minute')}{_ruled(ruler)}")


# ----------------------------------------------------------------------------- the straddle and the ruler

def _clock(t: datetime) -> str:
    return f"{t.astimezone(ET):%H:%M}"


def _same_clock(t: datetime, day: str) -> datetime:
    """``t``'s clock minute on a prior session, market time."""
    return datetime.combine(date.fromisoformat(day), t.astimezone(ET).time(), tzinfo=ET)


def _minutes_left(t: datetime) -> float:
    return (session_close(t) - t).total_seconds() / 60.0


def _straddle_share(point: DiaryPoint | None) -> float | None:
    """The straddle left at the point against the opening straddle scaled by the clock alone (the square
    root of the session's minutes left): 1.0 is a straddle that has only decayed with time."""
    if point is None or point.em_points is None or point.em_open is None or _minutes_left(point.ts) <= 0:
        return None
    return point.em_points / (point.em_open * math.sqrt(_minutes_left(point.ts) / session_minutes(point.ts)))


def _reprice(points: Sequence[DiaryPoint], now: datetime) -> float | None:
    """The straddle left at the day's newest point against the one 30 minutes before ``now``, less what the clock
    alone takes off it: 0.05 is 5% richer than the clock leaves it."""
    last, then = points[-1], point_at(points, now - timedelta(minutes=WINDOW_30_MIN))
    if then is None or last.em_points is None or then.em_points is None or _minutes_left(last.ts) <= 0:
        return None
    return last.em_points / then.em_points / math.sqrt(_minutes_left(last.ts) / _minutes_left(then.ts)) - 1.0


def _straddle_reprice(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """The straddle left now against the one 30 minutes ago, less what the clock alone would have taken off it,
    ranked against the same repricing at this minute on the prior sessions."""
    reprice = _reprice(today, scene.now)
    if reprice is None:
        ls.omit("vol.straddle_reprice_30", "no straddle left on the rows now and about 30 minutes ago (range_ruler.em_points)")
        return
    rank, why = _diary_rank(scene, reprice, lambda points, bars, then, sigma: _reprice(points, then),
                            "a straddle left at this minute and 30 minutes before")
    if rank is None:
        ls.omit("vol.straddle_reprice_30", why)
        return
    ls.put("vol.straddle_reprice_30",
           f"over the last 30 minutes the same-day straddle for the rest of today ended {abs(reprice) * 100:.1f}% "
           f"{'richer' if reprice >= 0 else 'cheaper'} than the clock alone would have left it; {_signed_standing(rank, 'richer')}")


def _straddle_vs_clock(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """The straddle's share of the opening straddle (_straddle_share) ranked against the same share at this minute
    on the prior sessions, with how many times their median it is."""
    share = _straddle_share(today[-1])
    if share is None:
        ls.omit("vol.straddle_vs_clock", "row carries no straddle left and opening straddle (range_ruler.em_points, em_open)")
        return
    if scene.state_dir is None:
        ls.omit("vol.straddle_vs_clock", "no state folder to read the prior sessions' diaries from")
        return
    base = _diary_base(scene, lambda points, bars, then, sigma: _straddle_share(points[-1]))
    rank, why = rank_sessions(share, base, "a straddle at this minute")
    if rank is None:
        ls.omit("vol.straddle_vs_clock", why)
        return
    ls.put("vol.straddle_vs_clock",
           f"the same-day straddle for the rest of today is {share / statistics.median(base[:rank.of]):.2f} times its usual share of the "
           f"opening straddle at {_clock(scene.now)} (its median on the last {rank.of} sessions), {rank.words()}, {rank.band}")


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


def _release_pace(scene: Scene, today: list[DiaryPoint], start: datetime, delivered: float) -> tuple[float, SameClockRank] | str:
    """What the tape delivers now over what the straddle priced for 30 minutes just before ``start``, ranked
    against the same ratio on the prior sessions (their tape at this minute over their straddle at ``start``'s):
    ``(how many times their median it is, its rank)``, or the reason it cannot be judged. A tape's high-to-low
    ranges run about twice a straddle's expected move, so only the prior sessions say what a usual tape is."""
    before = _straddle_30(point_at(today, start))
    if before is None:
        return f"no straddle on the rows just before {_clock(start)} to judge the tape against"
    usual = [t / p for d in rank_days(scene)
             if (p := _straddle_30(point_at(prior_diary(scene.state_dir, d), _same_clock(start, d))))
             and (t := _tape_30(scene.prior_bars[d], _same_clock(scene.now, d)))]
    rank, why = rank_sessions(delivered / before, usual, f"a straddle at {_clock(start)} and a tape at this minute")
    if rank is None:
        return f"to judge the tape against, {why}"
    return delivered / before / statistics.median(usual[:rank.of]), rank


def _ruler_event_load(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """Whether the day's priced movement is swollen by an event ahead, released by one just past, swollen or
    compressed with none, or normal: the straddle's 30-minute move against what the tape delivers, the tape
    since an event against the straddle before it, and the morning anchor, each ranked against the prior
    sessions', beside the calendar."""
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
    anchor = sigma_anchor(scene)
    if anchor is None:
        ls.omit(path, "needs the morning sigma ruler")
        return
    if scene.state_dir is None:
        ls.omit(path, "no state folder to read the prior sessions' diaries from")
        return
    base = []
    for d in rank_days(scene):
        then = _same_clock(scene.now, d)
        p, t = _straddle_30(point_at(prior_diary(scene.state_dir, d), then)), _tape_30(scene.prior_bars[d], then)
        if p is not None and t:
            base.append(p / t)
    load = priced / delivered
    rank, why = rank_sessions(load, base, "a straddle and a tape at this minute")
    anchors = [r.points for d in rank_days(scene) if (r := scene.prior_rulers.get(d)) is not None]
    swell_rank, swell_why = rank_sessions(anchor.points, anchors, "a trusted morning ruler")
    if rank is None or swell_rank is None:
        ls.omit(path, why or swell_why)
        return
    swell = anchor.points / statistics.median(anchors[:swell_rank.of])
    day_events = events.session_rows(day)
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
                times, pace_rank = pace
                released = pace_rank.band == "top third"
                event_words += (f"; the tape now moves {times:.2f} times its usual against the straddle priced just before it, "
                                f"{pace_rank.words()}, {pace_rank.band}")
    if released:
        verdict = "released after an event"
    elif ahead and rank.share >= TOP_FIFTH:
        verdict = "loaded before an event"
    elif not day_events and swell_rank.band == "top third":
        verdict = "swollen with no event"
    elif swell_rank.band == "bottom third":
        verdict = "compressed"
    else:
        verdict = "normal"
    ls.put(path,
           f"{verdict}: the same-day straddle prices a 30-minute move {load:.2f} times what the tape's recent 5-minute ranges scale to, "
           f"higher than {rank.higher_than} of the last {rank.of} sessions at {_clock(scene.now)} ({fifth(rank)}); {event_words}; "
           f"this morning's sigma ruler is {swell:.2f} times its {swell_rank.of}-session median, larger than {swell_rank.higher_than} "
           f"of the last {swell_rank.of} sessions' morning rulers, {swell_rank.band}{_ruled(anchor)}")


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


def _largest_move_30(bars: list[dict], then: datetime, points: float | None) -> float | None:
    """The largest REAL_MOVE_MIN-minute move inside the 30 minutes to ``then``, in sigma of ``points``: the
    measure a real move is ranked on (measures.move_size, each end inside the window)."""
    moves = [m for k in range(REALIZED_WINDOW_MIN - REAL_MOVE_MIN + 1)
             if (m := move_size(bars, then - timedelta(minutes=k), points, REAL_MOVE_MIN)) is not None]
    return max(moves) if moves else None


def _realized_vs_clock(scene: Scene, ls: LabelSet) -> None:
    """The last 30 minutes' realized swing in the morning anchor, ranked against the same half hour on the
    prior sessions, with whether a real move came inside it (vol.realized_vs_clock), and the swing as a multiple
    of their median (vol.realized_vs_clock_rank)."""
    ruler = sigma_anchor(scene)
    value = _realized_30(scene.bars, scene.now, ruler.points if ruler else None)
    if value is None:
        for path in ("vol.realized_vs_clock", "vol.realized_vs_clock_rank"):
            ls.omit(path, f"needs the morning sigma ruler and {REALIZED_MIN_BARS} finished bars in the last {REALIZED_WINDOW_MIN} minutes")
        return
    base = same_clock_values(scene, _realized_30)
    rank, why = rank_sessions(value, base, "bars at this minute")
    if rank is None:
        for path in ("vol.realized_vs_clock", "vol.realized_vs_clock_rank"):
            ls.omit(path, why)
        return
    if rank.higher_than == rank.of:
        standing = f"more than every one of the last {rank.of} sessions at this time of day"
    else:
        standing = f"more than {rank.higher_than} of the last {rank.of} sessions at this time of day, {fifth(rank)}"
    largest = _largest_move_30(scene.bars, scene.now, ruler.points)
    move, why = (rank_sessions(largest, same_clock_values(scene, _largest_move_30), "bars at this minute") if largest is not None
                 else (None, f"needs a finished bar {REAL_MOVE_MIN} minutes before one in the last {REALIZED_WINDOW_MIN} minutes"))
    if move is None:
        ls.omit("vol.realized_vs_clock", why)
    else:
        ls.put("vol.realized_vs_clock",
               f"over the last {REALIZED_WINDOW_MIN} minutes SPX's realized swing was {sig(value)}, {standing}; its largest "
               f"{REAL_MOVE_MIN}-minute move inside them was {sig(largest)}, larger than {move.higher_than} of the last {move.of} "
               f"sessions' largest in the same half hour, {move.band}: {'no real move' if move.band == 'bottom third' else 'a real move'}"
               f"{_ruled(ruler)}")
    usual = statistics.median(base[:rank.of])
    if usual <= 0:
        ls.omit("vol.realized_vs_clock_rank", "the prior sessions' usual swing at this minute is zero")
        return
    ls.put("vol.realized_vs_clock_rank",
           f"the last {REALIZED_WINDOW_MIN} minutes moved {value / usual:.2f} times the usual pace for this half hour (its median on the "
           f"last {rank.of} sessions at this time of day), {rank.words()}, {rank.band}{_ruled(ruler)}")


# ----------------------------------------------------------------------------- the VIX family around SPX (the context job)

def _quote(market: MarketContext | None, symbol: str, t: datetime) -> float | None:
    return market.last(symbol, t, max_age_min=QUOTE_MAX_AGE_MIN) if market else None


def _front_gap(market: MarketContext | None, t: datetime) -> float | None:
    """Nine-day VIX less VIX at ``t``, in points."""
    front, vix = _quote(market, "$VIX9D", t), _quote(market, "$VIX", t)
    return None if front is None or vix is None else front - vix


def _front_shift(market: MarketContext | None, t: datetime) -> float | None:
    """The 30-minute change to ``t`` in nine-day VIX less VIX, in points."""
    now, then = _front_gap(market, t), _front_gap(market, t - timedelta(minutes=WINDOW_30_MIN))
    return None if now is None or then is None else now - then


def _front_fear_shift(scene: Scene, ls: LabelSet) -> None:
    """The 30-minute change in nine-day VIX less VIX, ranked against the same change at this minute on the prior
    sessions' market context."""
    now, then = _front_gap(scene.market, scene.now), _front_gap(scene.market, scene.now - timedelta(minutes=WINDOW_30_MIN))
    if now is None or then is None:
        ls.omit("vol.front_fear_shift", "no $VIX9D and $VIX in the market context now and 30 minutes ago (the context job)")
        return
    d = now - then
    rank, why = rank_sessions(d, same_clock_market(scene, _front_shift), "$VIX9D and $VIX at this minute and 30 minutes before")
    if rank is None:
        ls.omit("vol.front_fear_shift", why)
        return

    def side(gap: float) -> str:
        return f"{abs(gap):.2f} {'under' if gap < 0 else 'over'} it"

    ls.put("vol.front_fear_shift",
           f"over the last 30 minutes nine-day VIX {'rose' if d >= 0 else 'fell'} {abs(d):.2f} points against the 30-day VIX, "
           f"from {side(then)} to {side(now)}; {_signed_standing(rank, 'nine-day VIX gaining')}")


def _move_standing(rank: SameClockRank, minutes: int) -> str:
    """An SPX move's rank in words (ranks.move_rank), the size it is judged by."""
    return f"larger than {rank.higher_than} of the last {rank.of} sessions' {minutes}-minute moves at this minute, {rank.band}"


def _vvix_with_move(scene: Scene, ls: LabelSet) -> None:
    """The 30-minute SPX move ranked against the same half hour on the prior sessions (ranks.move_rank), and which
    way VVIX went meanwhile."""
    ruler = sigma_anchor(scene)
    move = _spx_move(scene, WINDOW_30_MIN, ruler) if ruler else None
    a, b = _quote(scene.market, "$VVIX", scene.now - timedelta(minutes=WINDOW_30_MIN)), _quote(scene.market, "$VVIX", scene.now)
    if move is None or a is None or b is None:
        ls.omit("vol.vvix_with_move", "needs the morning sigma ruler, a finished bar 30 minutes ago and $VVIX in the market context now "
                                      "and then (the context job)")
        return
    rank, why = move_rank(scene, move, WINDOW_30_MIN)
    if rank is None:
        ls.omit("vol.vvix_with_move", why)
        return
    vvix = f"held at {b:.2f}" if b == a else f"{'rose' if b > a else 'fell'} {abs(b - a):.2f} points, from {a:.2f} to {b:.2f}"
    ls.put("vol.vvix_with_move", f"over the last 30 minutes SPX {'fell' if move < 0 else 'rose'} {sig(abs(move))}, "
                                 f"{_move_standing(rank, WINDOW_30_MIN)}, and VVIX {vvix}{_ruled(ruler)}")


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


def _vvix_vs_vix(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VVIX's 30-minute change less what SPX's move and VIX's change explain, fitted on the prior sessions' half
    hours, its size ranked against the fit's leftover over the same half hour on the prior sessions; beside it
    whether SPX and VIX barely moved: each one's 30-minute move in the bottom third of the same half hour on the
    prior sessions (ranks.move_rank, and vix_stir's rank of the diary VIX)."""
    path = "vol.vvix_vs_vix"
    ruler = sigma_anchor(scene)
    now = _half_hour(scene.market, scene.bars, scene.now, ruler.points) if ruler else None
    if now is None:
        ls.omit(path, "needs the morning sigma ruler, bars 30 minutes apart and $VIX and $VVIX in the market context now and then "
                      "(the context job)")
        return
    xs, ys, sessions, same_clock = [], [], 0, []
    for d in rank_days(scene):
        prior, anchor = scene.prior_markets.get(d), scene.prior_rulers.get(d)
        if prior is None or anchor is None:
            continue
        first = datetime.combine(date.fromisoformat(d), VVIX_FIT_FIRST, tzinfo=ET)
        reads = [r for k in range(VVIX_FIT_READS)
                 if (r := _half_hour(prior, scene.prior_bars[d], first + timedelta(minutes=WINDOW_30_MIN * k), anchor.points))]
        sessions += bool(reads)
        xs += [(r[0], r[1]) for r in reads]
        ys += [r[2] for r in reads]
        if here := _half_hour(prior, scene.prior_bars[d], _same_clock(scene.now, d), anchor.points):
            same_clock.append(here)
    fit = _fit_two(xs, ys) if sessions >= MIN_RANK_SESSIONS else None
    if fit is None:
        ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions of $VIX and $VVIX to fit against, have {sessions}")
        return
    vix_share = _vix_share(today, scene.now, WINDOW_30_MIN)
    if vix_share is None:
        ls.omit(path, "no diary VIX now and about 30 minutes ago (range_ruler.vol_carry.vix)")
        return
    (spx_rank, spx_why), (vix_rank, vix_why) = move_rank(scene, now[0], WINDOW_30_MIN), _vix_share_rank(scene, vix_share, WINDOW_30_MIN)
    if spx_rank is None or vix_rank is None:
        ls.omit(path, spx_why or vix_why)
        return
    a, b, c = fit
    move, dvix, dvvix, vix = now
    explained = a + b * move + c * dvix
    left = dvvix - explained
    rank, why = rank_sessions(abs(left), [abs(dv - (a + b * x1 + c * x2)) for x1, x2, dv, _ in same_clock],
                              "$VIX and $VVIX at this minute and 30 minutes before")
    if rank is None:
        ls.omit(path, why)
        return
    band = ("top fifth" if rank.share >= TOP_FIFTH else "at or above the median, short of the top fifth" if rank.share >= HALF_RANK
            else "under the median")
    if spx_rank.band == vix_rank.band == "bottom third":
        others = "while SPX and VIX barely moved (both 30-minute moves in the bottom third of the last sessions' at this minute)"
    else:
        others = "while SPX or VIX also moved (one or both 30-minute moves above the bottom third of the last sessions' at this minute)"
    ls.put(path,
           f"over the last 30 minutes VVIX {'rose' if dvvix >= 0 else 'fell'} {abs(dvvix):.2f} points; VIX's and SPX's moves explain "
           f"a {'rise' if explained >= 0 else 'fall'} of about {abs(explained):.2f}; the {abs(left):.2f} left over is bigger than "
           f"on {rank.higher_than} of the last {rank.of} sessions at this minute, {band}, {others}{_ruled(ruler)}")


def _vix_curve(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """VIX against three-month VIX (the diary's vix_ts) against the inversion line, ranked against the prior
    sessions at this minute, and nine-day VIX against VIX."""
    path = "vol.term_structure"
    ratio, front, vix = today[-1].vix_ts, _quote(scene.market, "$VIX9D", scene.now), _quote(scene.market, "$VIX", scene.now)
    if ratio is None:
        ls.omit(path, "row carries no VIX against three-month VIX (vix_ts)")
        return
    if front is None or vix is None:
        ls.omit(path, "no $VIX9D and $VIX in the market context (the context job)")
        return
    rank, why = _diary_rank(scene, ratio, lambda points, bars, then, sigma: points[-1].vix_ts, "the VIX curve at this minute")
    if rank is None:
        ls.omit(path, why)
        return
    if ratio >= VIX_CURVE_FLAT:
        level = f"{ratio:.2f} times three-month VIX, at or over the {VIX_CURVE_FLAT:.2f} inversion line"
    else:
        level = f"{min(ratio, VIX_CURVE_FLAT - 0.01):.2f} times three-month VIX, under the {VIX_CURVE_FLAT:.2f} inversion line"
    nine = front / vix
    nine_words = (f"{nine:.2f} of VIX, at or over the inversion line" if nine >= VIX_CURVE_FLAT
                  else f"{min(nine, VIX_CURVE_FLAT - 0.01):.2f} of VIX, under the inversion line")
    diaries = [prior_diary(scene.state_dir, d) for d in scene.prior_bars]
    inverted = sum(1 for points in diaries if any(p.vix_ts and p.vix_ts >= VIX_CURVE_FLAT for p in points))
    record = (f"the curve has not inverted on any of the last {len(diaries)} sessions" if not inverted
              else f"the curve inverted on {inverted} of the last {len(diaries)} sessions")
    ls.put(path, f"VIX is {level}; flatter than {rank.higher_than} of the last {rank.of} sessions at this time, {fifth(rank)}; "
                 f"nine-day VIX is {nine_words}; {record}")


# ----------------------------------------------------------------------------- a stress day's path

def _off_low(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
    """A prior session's distance at ``then`` above its session low so far, in sigma of its own ruler."""
    lows = session_extremes(bars)
    return (float(bars[-1]["close"]) - lows.low) / sigma if sigma and lows is not None else None


def _record_rise(change: float, rank: SameClockRank) -> bool:
    """A rise from the open higher than on every one of the prior sessions at this minute; a fall never is one."""
    return change > 0 and rank.higher_than == rank.of


def _rise_words(change: float, rank: SameClockRank, unit: str = "") -> str:
    """Why a change from the open is not a record rise, for the not-a-stress-day reason."""
    if change <= 0:
        return f"{change:+.2f}{unit} from its open, not above it"
    return (f"{change:+.2f}{unit} from its open, higher than {rank.higher_than} of the last {rank.of} sessions at this minute, "
            f"not every one")


def _stress_path(scene: Scene, today: list[DiaryPoint], ls: LabelSet) -> None:
    """On a stress day only, how much of its rise VIX has given back from its session high, how far SPX sits off its
    session low against the same distance at this minute on the prior sessions, and how often NYSE TICK's lows
    reached their minute's bottom burst band in the last 30 minutes. A stress day's VIX, or its VIX curve, has risen
    from the open by more than on every one of the prior sessions at this minute, or VIX is over their highs, or the
    curve is at or over the inversion line; any other day the label is omitted, so its question sleeps. The curve's
    level alone is no gauge: it drifts for weeks and tops its recent sessions on calm days."""
    path = "vol.stress_path"
    now, opened, curve, curve_open = today[-1].vix, today[0].vix, today[-1].vix_ts, today[0].vix_ts
    if now is None or opened is None:
        ls.omit(path, "no diary VIX now and at the open (range_ruler.vol_carry.vix)")
        return
    rise = now - opened
    rise_rank, why = _diary_rank(scene, rise, lambda points, bars, then, sigma: points[-1].vix - points[0].vix
                                 if points[-1].vix and points[0].vix else None, "a diary VIX at the open and at this minute")
    shift = curve - curve_open if curve is not None and curve_open is not None else None
    shift_rank = None
    if rise_rank is not None and shift is not None:
        shift_rank, why = _diary_rank(scene, shift, lambda points, bars, then, sigma: points[-1].vix_ts - points[0].vix_ts
                                      if points[-1].vix_ts and points[0].vix_ts else None,
                                      "the VIX curve at the open and at this minute")
    if rise_rank is None or (shift is not None and shift_rank is None):
        ls.omit(path, why)
        return
    prior = [p.vix for d in scene.prior_bars for p in prior_diary(scene.state_dir, d) if p.vix]
    prior_high = max(prior) if prior else None
    inverted = curve is not None and curve >= VIX_CURVE_FLAT
    if not (_record_rise(rise, rise_rank) or (prior_high is not None and now > prior_high) or inverted
            or (shift_rank and _record_rise(shift, shift_rank))):
        high_words = (f"at or under its {prior_high:.2f} high of the prior sessions" if prior_high is not None
                      else "with no prior diaries to compare it with")
        curve_words = (f", and {curve:.2f} times three-month VIX, under the {VIX_CURVE_FLAT:.2f} inversion line"
                       if curve is not None else "")
        if shift_rank:
            curve_words += f", {_rise_words(shift, shift_rank)}"
        ls.omit(path, f"not a stress day: VIX is {_rise_words(rise, rise_rank, ' points')}, {high_words}{curve_words}", ended=True)
        return
    lows, ruler = session_extremes(scene.bars), sigma_anchor(scene)
    if lows is None or ruler is None:
        ls.omit(path, "needs today's bars and the morning sigma ruler")
        return
    off_low = max((scene.spot - lows.low) / ruler.points, 0.0)
    low_rank, why = rank_sessions(off_low, same_clock_values(scene, _off_low), "bars at this minute")
    if low_rank is None:
        ls.omit(path, why)
        return
    start = scene.now - timedelta(minutes=WINDOW_30_MIN)
    tick_bars = scene.market.bars_between("$TICK", start, scene.now) if scene.market else []
    if not tick_bars:
        ls.omit(path, left_out(scene.market, "$TICK") or "no NYSE TICK bars in the market context over the last 30 minutes (the context job)")
        return
    bursts = tick_bursts(tick_bands_by_minute(scene), tick_bars)
    if bursts is None:
        ls.omit(path, f"needs {SAME_CLOCK_MIN_SESSIONS} prior sessions of NYSE TICK bars at each of the last {WINDOW_30_MIN} minutes")
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
    where = "at its session low now" if off_low == 0 else f"{sig(off_low)} above its session low from {_clock(lows.low_at)}"
    hits = bursts[1]
    cluster = f"at least the {TICK_CLUSTER}-reading cluster" if hits >= TICK_CLUSTER else f"short of the {TICK_CLUSTER}-reading cluster"
    ls.put(path, f"VIX {now:.1f}, {'up' if rise >= 0 else 'down'} {abs(rise):.1f} points since the open; {vix_words}; SPX sits {where}, "
                 f"further off its low than {low_rank.higher_than} of the last {low_rank.of} sessions at this minute, {low_rank.band}; "
                 f"NYSE TICK's 1-minute lows reached the bottom {pct(1 - TICK_BURST_PCT)} band for their minute {plural(hits, 'time')} "
                 f"in 30 minutes, {cluster}{_ruled(ruler)}")


# ----------------------------------------------------------------------------- the same-day skew (the lob-flow tape)

def _skew(scene: Scene, ls: LabelSet) -> None:
    """The skew labels from the tape's quotes in the minute to this read's last whole minute and 30 minutes
    before it, each ranked against the same minutes on the prior sessions' tapes."""
    paths = ("skew.put_tilt_vs_usual", "skew.shift_vs_price")
    at_min = minute_floor(scene.now)
    then = at_min - timedelta(minutes=WINDOW_30_MIN)
    smiles = skew_at(scene.state_dir, scene.day, (at_min, then), scene.now) if scene.state_dir else None
    if smiles is None:
        why = f"no lob-flow tape for {scene.day} (state/lob_flow/raw)"
        for path in paths:
            ls.omit(path, why)
        ls.sleep("put_tilt_vs_clock", why)
        return
    prior = _prior_smiles(scene)
    _put_tilt(scene, smiles[at_min], at_min, prior, ls)
    _skew_shift(scene, smiles[then], smiles[at_min], then, at_min, prior, ls)


def _prior_smiles(scene: Scene) -> dict[str, tuple[Skew | None, Skew | None]]:
    """``(30 minutes before, now)`` smiles at this read's minute on each prior session with a tape, newest first,
    each tape read once as it was on file at the read's clock that day."""
    out = {}
    for d in rank_days(scene)[:NIGHT_RANK_COUNT]:
        read_at = _same_clock(scene.now, d)
        t = minute_floor(read_at)
        then = t - timedelta(minutes=WINDOW_30_MIN)
        smiles = skew_at(scene.state_dir, d, (then, t), read_at)
        if smiles is not None:
            out[d] = (smiles[then], smiles[t])
    return out


def _tilt(smile: Skew | None) -> float | None:
    """25-delta put vol less 25-delta call vol, as a share of at-the-money vol."""
    return None if smile is None or smile.put_25 is None or smile.call_25 is None else (smile.put_25 - smile.call_25) / smile.atm


def _wing(smile: Skew | None) -> float | None:
    """10-delta put vol less 25-delta put vol, as a share of at-the-money vol."""
    return None if smile is None or smile.put_10 is None or smile.put_25 is None else (smile.put_10 - smile.put_25) / smile.atm


def _steepness(rank: SameClockRank) -> str:
    return ("past the steep rank" if rank.share >= SKEW_STEEP_RANK else "past the flat rank" if rank.share <= SKEW_FLAT_RANK
            else "within the usual range")


def _put_tilt(scene: Scene, smile: Skew | None, at_min: datetime, prior: dict[str, tuple[Skew | None, Skew | None]],
              ls: LabelSet) -> None:
    """The 25-delta put-call tilt ranked against the prior sessions' at the same minute, with the far-put wing."""
    path, clock = "skew.put_tilt_vs_usual", _clock(at_min)
    tilt = _tilt(smile)
    if tilt is None:
        why = f"no fresh 25-delta put and call quotes on the lob-flow tape in the minute to {clock}"
        ls.omit(path, why)
        ls.sleep("put_tilt_vs_clock", why)
        return
    tilts = [t for _, now in prior.values() if (t := _tilt(now)) is not None]
    wings = [w for _, now in prior.values() if (w := _wing(now)) is not None]
    rank, why = rank_sessions(tilt, tilts, f"25-delta quotes on the tape at {clock}")
    if rank is None:
        ls.omit(path, why)
        ls.sleep("put_tilt_vs_clock", why)
        return
    gap = (smile.put_25 - smile.call_25) * 100.0
    if gap < 0:
        lead = f"same-day 25-delta calls are priced {abs(gap):.1f} vol points above 25-delta puts, a call tilt"
    else:
        lead = f"same-day 25-delta puts are priced {gap:.1f} vol points above 25-delta calls, {tilt:.2f} of the at-the-money level"
    wing = _wing(smile)
    wing_rank = rank_sessions(wing, wings, "")[0] if wing is not None else None
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


def _one_sd_tilt(smile: Skew | None) -> float | None:
    """The put-call tilt one remaining standard deviation either side of the forward, as a share of at-the-money vol."""
    return None if smile is None or smile.put_1sd is None or smile.call_1sd is None else (smile.put_1sd - smile.call_1sd) / smile.atm


def _tilt_left(before: Skew | None, now: Skew | None, move: float | None) -> float | None:
    """The change in the one-sd tilt from ``before`` to ``now`` less what price's ``move`` in sigma explains (SKEW_PER_SIGMA)."""
    a, b = _one_sd_tilt(before), _one_sd_tilt(now)
    return None if a is None or b is None or move is None else b - a - SKEW_PER_SIGMA * move


def _skew_shift(scene: Scene, before: Skew | None, now: Skew | None, then: datetime, at_min: datetime,
                prior: dict[str, tuple[Skew | None, Skew | None]], ls: LabelSet) -> None:
    """The 30-minute change in the put-call tilt one remaining standard deviation either side of the forward,
    less what price's move explains (SKEW_PER_SIGMA), ranked against the same leftover at this minute on the
    prior sessions' tapes, each move in its own ruler."""
    path = "skew.shift_vs_price"
    tilts = [_one_sd_tilt(before), _one_sd_tilt(now)]
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
    base = []
    for day, (a, b) in prior.items():
        t = minute_floor(_same_clock(scene.now, day))
        ruler_d = scene.prior_rulers.get(day)
        left = _tilt_left(a, b, _price_move(scene.prior_bars[day], t, WINDOW_30_MIN, ruler_d.points if ruler_d else None))
        if left is not None:
            base.append(left)
    rank, why = rank_sessions(resid, base, f"quotes one remaining standard deviation either side on the tape at {_clock(then)} "
                                           f"and {_clock(at_min)}")
    if rank is None:
        ls.omit(path, why)
        return
    ls.put(path,
           f"over the last 30 minutes the gap between same-day put and call prices {'widened' if d >= 0 else 'narrowed'} {abs(d):.3f} of "
           f"at-the-money vol while price {'fell' if move < 0 else 'rose'} {sig(abs(move))}; price explains a "
           f"{'widening' if explained >= 0 else 'narrowing'} of {abs(explained):.3f}, so puts got {'dearer' if resid >= 0 else 'cheaper'} "
           f"by {abs(resid):.3f} beyond the move; {_signed_standing(rank, 'puts getting dearest')}{_ruled(ruler)}")
