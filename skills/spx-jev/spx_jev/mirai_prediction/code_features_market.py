"""The code feature builder's market answerers: the Phase 2 questions, answered from the market feed's symbols (context
bars), the daily closes, the index weights, the event calendar, the SPX diary and the lob-flow quote sweeps, beside the
SPX bars. One function per question, registered in MARKET_ANSWERERS with the signature of the bars answerers:

    answer(record, bars_up_to_read, history, day, hhmm) -> answer | None

Every answer comes from what was known at the read time: today's bars cut at the read (bars_up_to), prior sessions for a
rank (the same measure at the same minute, trailing HISTORY_SESSIONS sessions, at least MIN_HISTORY_SESSIONS), daily
closes before the day, index weights dated on or before the day. Every threshold that is not written in the question set
is a DRAFT and says so in the catalog's notes.

Shared machinery:
    Series          one symbol's bars of one session by minute of day: the close before a minute, a window, a move
    DayView         one session's series (SPX and the feed's symbols), prior sessions cached on the history
    rank_vs_prior   today's measure ranked against the same measure on the trailing sessions
    usual_link      a symbol's usual multiple of SPX's 30-minute return (the beta) and the spread of its residuals, fitted
                    on the prior sessions' half hours (labels/usual_link.py's rule, without the ruler)
"""
from __future__ import annotations

import bisect
import math
import statistics
from datetime import date, datetime, timedelta

from .. import events
from ..cuts import THIRD_HI
from ..index_weights import pick as pick_weights
from ..labels.vol import LOADING_EVENTS
from ..sessions import is_trading_day, next_trading_day, previous_trading_day
from .code_features import (MIN_HISTORY_SESSIONS, MarketHistory, _reference_levels, flat_labels, hhmm_of, night_bars_up_to,
                            rank_fraction, third_of, trailing_days)

SPX = "$SPX"                          # the index: its own minute bars, under this name in a DayView
SPY, QQQ, IWM, ES, ZN = "SPY", "QQQ", "IWM", "/ES", "/ZN"
VIX, VIX9D, TNX = "$VIX", "$VIX9D", "$TNX"
ADVANCING, DECLINING, ADD = "$ADVN", "$DECN", "$ADD"
MACRO_COMPLEX = (ZN, "HYG", "USO", "GLD")              # MACRO-01: bonds (the ten-year future), high-yield credit, oil, gold; /6E dropped
BULL_FUNDS, BEAR_FUNDS = ("SPXL", "TQQQ"), ("SPXS", "SQQQ")
SHARE_CLASSES = {"GOOG": "GOOGL"}                      # a share class the feed does not carry, summed into the one it does
MEGACAP_COUNT = 8
WINDOW_30 = 30
WINDOW_60 = 60
SETTLED_OPEN = "09:35"                                 # the settled open is the close of the 09:34 bar: bars before 09:35
LINK_WINDOW_ENDS = ("10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00")
MIN_LINK_PAIRS = 20                                    # fewest half hours to fit a usual multiple on
MAX_STALE_MIN = 5                                      # a symbol's last bar older than this at a window's edge: no price there
FIVE_MINUTE_SLOTS = [(f"{h:02d}:{m:02d}", f"{h:02d}:{m + 4:02d}") for h in range(9, 16) for m in range(0, 60, 5) if (h, m) >= (9, 30) and (h, m) < (16, 0)]
# DRAFT thresholds (none is written in the question set):
SPIKE_SHARE = 0.9                                      # TREND-11: a 5-minute volume at or above the 90th percentile of its slot
OPENING_READS_UNTIL = "10:35"                          # VOLATILITY-07 answers opening reads only: those by 10:35
VIX_PER_GAP_PCT = -1.7                                 # VOLATILITY-07: VIX points per 1% opening gap (labels/vol.py's per-sigma figure, a sigma near 1%)
ONE_DAY_MOVE_DAYS = 252.0                              # VOLATILITY-08: the one-day move VIX implies is VIX% / sqrt(252)
RANGE_REGIME_SESSIONS, RANGE_REGIME_AVERAGE = 60, 5    # VOLATILITY-17
CONTRACT_JUMP = 0.02                                   # LEVELS-07: /ES more than 2% from the night's last bar is another contract (roll guard)
ROUND_STEP = 25.0                                      # LEVELS-10: round numbers every 25 points
EDGE_TOUCH_SIGMA = 0.05                                # LEVELS-12: a bar within this share of sigma of an edge tests it
REJECTION_BARS = 3                                     # LEVELS-12: the bars after a test over which its rejection is measured
WALL_TOUCH_SIGMA = 0.10                                # OPTIONS-04: the label's touch rule (levels.wall_touch_effort)
PIN_CROSSINGS = 3                                      # OPTIONS-06
OPTION_WIDTH_MINUTES = 5                               # OPTIONS-08: the median spread of the last five sweeps
EXTREME_TOLERANCE = 0.0005                             # BREADTH-03: IWM's close within 0.05% of its own session extreme (closes)
FIVE_MINUTE_MARKS = [f"{h:02d}:{m:02d}" for h in range(9, 17) for m in range(0, 60, 5) if (h, m) > (9, 30) and (h, m) <= (16, 0)]
PROFILE_BIN = 5.0                                      # FLOW-06: SPX price bins of 5 points
PROFILE_SESSIONS = 5
NODE_HIGH_SHARE = 0.75                                 # FLOW-06: a bin at or above the 75th percentile of bin volume is a high-volume node
MIN_VOLUME_MINUTES = 24                                # FLOW-04: fewer of the 30 minutes with SPY volume than this is a feed hole
REACTION_MIN = 15                                      # EVENTS-03: a release's first reaction (labels/events_shocks.py)
DIGEST_MIN = 120                                       # EVENTS-03: a release older than this is digested
REACTION_EXTEND_SIGMA = 0.05                           # EVENTS-03: past the first reaction by this share of the normal-day sigma is extended (cuts.py)
LAST_HOUR_FROM = "15:00"                               # VOLATILITY-14: the last 0DTE hour is a mechanical cause
NEW_EXTREME_EARLIER = 30                               # VOLATILITY-14, BREADTH-03: a new extreme needs 30 earlier bars to beat
VIX_TIE = 0.05                                         # VOLATILITY-14: VIX within 0.05 points of its level at the earlier extreme confirms
BARRIER_SKIP_SIGMA = 0.02                              # LEVELS-10: a level this close to price is where price is, not ahead of it
STRETCH_SHARE = 0.25                                   # OPTIONS-06: stretching needs price this share of a 30-minute expected move off the pin
MIN_SWING_BARS = 2                                     # OPTIONS-06: a swing across the pin shorter than this is a wiggle
FAR_GAP_RANK = 0.8                                     # MACRO-02: the gap's top fifth, the rank the macro_gap judgment gate reads
LOADING_AHEAD_MIN = 60                                 # VOLATILITY-18: a ruler-loading event within the hour winds the quiet up
HIGH_TIER = frozenset({"FOMC", "FOMC_PRESSER", "CPI", "JOBS", "PCE"})                                                        # EVENTS-02
MEDIUM_TIER = frozenset({"PPI", "RETAIL_SALES", "GDP", "ISM_MANUFACTURING", "ISM_SERVICES", "JOLTS", "FOMC_MINUTES",
                         "FED_CHAIR_TESTIMONY", "FED_CHAIR_JACKSON_HOLE", "CONSUMER_CONFIDENCE", "UMICH_SENTIMENT"})
MONTH_FIRST_DAYS, MONTH_LAST_DAYS = 3, 2               # SESSION-02


# ---------------------------------------------------------------- minutes of day

def shift_minute(hhmm: str, minutes: int) -> str:
    t = datetime(2000, 1, 1, int(hhmm[:2]), int(hhmm[3:5])) + timedelta(minutes=minutes)
    return t.strftime("%H:%M")


def minutes_between(earlier: str, later: str) -> int:
    return (int(later[:2]) * 60 + int(later[3:5])) - (int(earlier[:2]) * 60 + int(earlier[3:5]))


# ---------------------------------------------------------------- one symbol's session

class Series:
    """One symbol's minute bars of one session, by minute of day (bars already cut at the read for the read's day)."""

    def __init__(self, bars: list[dict]):
        self.bars = bars
        self.minutes = [hhmm_of(b["ts"]) for b in bars]

    def count_before(self, hhmm: str) -> int:
        return bisect.bisect_left(self.minutes, hhmm)

    def close_before(self, hhmm: str, max_stale: int = MAX_STALE_MIN) -> float | None:
        """The last close of a bar starting before ``hhmm``, within ``max_stale`` minutes of it."""
        k = self.count_before(hhmm)
        if not k or minutes_between(self.minutes[k - 1], hhmm) > max_stale:
            return None
        return float(self.bars[k - 1]["close"])

    def window(self, hhmm: str, minutes: int) -> list[dict]:
        """The bars starting in the ``minutes`` before ``hhmm``."""
        return self.bars[bisect.bisect_left(self.minutes, shift_minute(hhmm, -minutes)):self.count_before(hhmm)]

    def between(self, lo: str, hi: str) -> list[dict]:
        """The bars starting at or after ``lo`` and before ``hi``."""
        return self.bars[bisect.bisect_left(self.minutes, lo):self.count_before(hi)]

    def move_pct(self, hhmm: str, minutes: int) -> float | None:
        a, b = self.close_before(shift_minute(hhmm, -minutes)), self.close_before(hhmm)
        return b / a - 1.0 if a and b is not None else None

    def volume(self, hhmm: str, minutes: int) -> float:
        return sum(float(b.get("volume") or 0.0) for b in self.window(hhmm, minutes))


class DayView:
    """One session's series: SPX's own bars and the feed's symbols, by name."""

    def __init__(self, day: str, spx_bars: list[dict], context_bars: dict[str, list[dict]], cache: dict | None = None):
        self.day = day
        self._spx = spx_bars
        self._context = context_bars
        self._cache = cache if cache is not None else {}

    def series(self, symbol: str) -> Series:
        key = (self.day, symbol)
        s = self._cache.get(key)
        if s is None:
            s = Series(self._spx if symbol == SPX else self._context.get(symbol, []))
            self._cache[key] = s
        return s


def prior_views(history: MarketHistory, day: str) -> list[DayView]:
    """The trailing sessions before ``day`` that hold the feed's symbols, as views (their series cached on the history)."""
    return [DayView(d, history.spx_bars_by_day.get(d, []), history.context_bars_by_day[d], history.cache)
            for d in trailing_days(history.context_bars_by_day, day)]


def today_view(history: MarketHistory, day: str, bars: list[dict], row_ts: str) -> DayView:
    cut = {s: cut_at(b, row_ts) for s, b in history.context_bars_by_day.get(day, {}).items()}
    return DayView(day, bars, cut)


def rank_vs_prior(history: MarketHistory, day: str, hhmm: str, value: float, measure) -> float | None:
    """``value`` ranked against ``measure(view, hhmm)`` on each trailing session where it is defined."""
    prior = [m for v in prior_views(history, day) if (m := measure(v, hhmm)) is not None]
    return rank_fraction(value, prior)


def unsigned(value: float | None) -> float | None:
    return abs(value) if value is not None else None


def part_of(measure: tuple | None, k: int):
    return measure[k] if measure is not None else None


def cut_at(bars: list[dict], row_ts: str) -> list[dict]:
    """bars_up_to for bars whose ts are ET ISO strings of one day: the bars finished by ``row_ts``, by string order."""
    cutoff = (datetime.fromisoformat(row_ts).astimezone(events.ET) - timedelta(minutes=1)).isoformat()
    return bars[:bisect.bisect_right([b["ts"] for b in bars], cutoff)]


def prior_day_of(history: MarketHistory, day: str) -> str | None:
    days = trailing_days(history.context_bars_by_day, day, 1)
    return days[0] if days else None


def prior_close_of(history: MarketHistory, day: str, symbol: str) -> float | None:
    """The symbol's last close of the session before ``day`` (SPX from its own bars)."""
    if symbol == SPX:
        days = trailing_days(history.spx_bars_by_day, day, 1)
        return float(history.spx_bars_by_day[days[0]][-1]["close"]) if days else None
    prior = prior_day_of(history, day)
    bars = history.context_bars_by_day.get(prior, {}).get(symbol) if prior else None
    return float(bars[-1]["close"]) if bars else None


# ---------------------------------------------------------------- the usual link (beta) of a symbol to SPX

def usual_link(history: MarketHistory, day: str, symbol: str, minutes: int = WINDOW_30) -> tuple[float, float] | None:
    """(beta, residual spread) of the symbol's ``minutes``-minute returns on SPX's over the prior sessions' half hours;
    None with fewer than MIN_LINK_PAIRS pairs or a flat index. Cached on the history for the day."""
    key = ("link", day, symbol, minutes)
    if key in history.cache:
        return history.cache[key]
    pairs = []
    for view in prior_views(history, day):
        spx, own = view.series(SPX), view.series(symbol)
        for end in LINK_WINDOW_ENDS:
            x, y = spx.move_pct(end, minutes), own.move_pct(end, minutes)
            if x is not None and y is not None:
                pairs.append((x, y))
    link = None
    if len(pairs) >= MIN_LINK_PAIRS:
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        var = sum((x - mx) ** 2 for x in xs)
        if var > 0:
            beta = sum((x - mx) * (y - my) for x, y in pairs) / var
            spread = statistics.pstdev([y - beta * x for x, y in pairs])
            link = (beta, spread)
    history.cache[key] = link
    return link


def beyond_link(view: DayView, symbol: str, hhmm: str, minutes: int, link: tuple[float, float]) -> float | None:
    """The symbol's return over the window less its usual multiple of SPX's: the move SPX does not explain."""
    x, y = view.series(SPX).move_pct(hhmm, minutes), view.series(symbol).move_pct(hhmm, minutes)
    return y - link[0] * x if x is not None and y is not None else None


def links_for(history: MarketHistory, day: str, symbols) -> dict[str, tuple[float, float]] | None:
    """Today's usual link per symbol (the same fit is applied to every prior session in a rank); None when one is missing."""
    links = {s: usual_link(history, day, s) for s in symbols}
    return None if any(l is None for l in links.values()) else links


def since_close_beyond(history: MarketHistory, view: DayView, symbol: str, hhmm: str, link: tuple[float, float]) -> float | None:
    """The symbol's return since the prior session's close less its usual multiple of SPX's since the same close."""
    spx_prior, own_prior = prior_close_of(history, view.day, SPX), prior_close_of(history, view.day, symbol)
    spx_now, own_now = view.series(SPX).close_before(hhmm), view.series(symbol).close_before(hhmm)
    if not spx_prior or not own_prior or spx_now is None or own_now is None:
        return None
    return (own_now / own_prior - 1.0) - link[0] * (spx_now / spx_prior - 1.0)


def megacaps(history: MarketHistory, day: str) -> list[tuple[str, float]] | None:
    """The MEGACAP_COUNT largest index stocks and their shares from the weights dated on or before ``day``, largest first."""
    entry = pick_weights(history.index_weights, day) if history.index_weights else None
    if entry is None:
        return None
    weights: dict[str, float] = {}
    for symbol, w in (entry.get("weights") or {}).items():
        if isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w < 1:
            name = SHARE_CLASSES.get(symbol, symbol)
            weights[name] = weights.get(name, 0.0) + float(w)
    if len(weights) < MEGACAP_COUNT:
        return None
    return sorted(weights.items(), key=lambda kv: -kv[1])[:MEGACAP_COUNT]


# ---------------------------------------------------------------- SPX path measures

def closes_of(bars: list[dict]) -> list[float]:
    return [float(b["close"]) for b in bars]


def path_share(view: DayView, hhmm: str) -> float | None:
    """TREND-04's measure: net move over the summed 1-minute moves, over the last 30 minutes, or since 09:35 before 10:05."""
    spx = view.series(SPX)
    bars = spx.between(SETTLED_OPEN, hhmm) if hhmm < "10:05" else spx.window(hhmm, WINDOW_30)
    if len(bars) < (5 if hhmm < "10:05" else 25):
        return None
    c = closes_of(bars)
    travel = sum(abs(b - a) for a, b in zip(c, c[1:]))
    return abs(c[-1] - c[0]) / travel if travel else None


def running_vwap(bars: list[dict]) -> list[float | None]:
    """The session's volume-weighted average price after each bar: closes weighted by volume (closes only, so the live
    quotes and the saved minute bars measure the same thing)."""
    out, value, volume = [], 0.0, 0.0
    for b in bars:
        v = float(b.get("volume") or 0.0)
        value += float(b["close"]) * v
        volume += v
        out.append(value / volume if volume else None)
    return out


def vwap_balance(view: DayView, hhmm: str) -> tuple[int, int, int] | None:
    """TREND-07's measure on SPY closes at the 5-minute marks: (minutes since its last mark on the other side of its VWAP,
    side flips between the marks of the last 30 minutes, the latest mark's side: +1 above, -1 below). Marks, not every
    close, so live quotes and saved bars count the same flips."""
    spy = view.series(SPY)
    bars = spy.bars[:spy.count_before(hhmm)]
    vwap = running_vwap(bars)
    if len(bars) < 10 or vwap[-1] is None:
        return None
    at_mark = {}
    for mark in FIVE_MINUTE_MARKS:
        if mark > hhmm:
            break
        k = spy.count_before(mark)
        if k and vwap[k - 1] is not None and minutes_between(spy.minutes[k - 1], mark) <= MAX_STALE_MIN:
            at_mark[mark] = 1 if float(bars[k - 1]["close"]) >= vwap[k - 1] else -1
    if not at_mark:
        return None
    marks = list(at_mark)
    latest = at_mark[marks[-1]]
    crossed = next((m for m in reversed(marks) if at_mark[m] != latest), None)
    since = minutes_between(crossed or marks[0], hhmm)
    recent = [at_mark[m] for m in marks if minutes_between(m, hhmm) <= WINDOW_30]
    flips = sum(1 for a, b in zip(recent, recent[1:]) if a != b)
    return since, flips, latest


def range_ratio(view: DayView, hhmm: str) -> float | None:
    """VOLATILITY-04's measure: the last 30 minutes' high-low range over the 30 minutes' before it."""
    spx = view.series(SPX)
    recent, earlier = spx.window(hhmm, WINDOW_30), spx.window(shift_minute(hhmm, -WINDOW_30), WINDOW_30)
    if len(recent) < 25 or len(earlier) < 25:
        return None
    before = max(float(b["high"]) for b in earlier) - min(float(b["low"]) for b in earlier)
    now = max(float(b["high"]) for b in recent) - min(float(b["low"]) for b in recent)
    return now / before if before > 0 else None


def new_extreme_vs_prior_swing(values: list[float], window_from: int) -> tuple[int, int] | None:
    """(i, j): the highest of ``values`` from ``window_from`` on (i) beats the highest before it (j), the extreme of the
    earlier swing, not the runner-up bar a minute before; None with fewer than NEW_EXTREME_EARLIER earlier values or no
    new extreme. Lows are passed negated."""
    if window_from < NEW_EXTREME_EARLIER or window_from >= len(values):
        return None
    earlier = values[:window_from]
    j = earlier.index(max(earlier))
    recent = values[window_from:]
    i = recent.index(max(recent)) + window_from
    return (i, j) if values[i] > values[j] else None


def session_new_extreme(bars: list[dict], minutes: int = WINDOW_30) -> tuple[str, int, int] | None:
    """A session high or low set in the last ``minutes`` of ``bars`` (finished by the read) beyond the extreme before
    them: ("high" | "low", the new extreme's bar index, the earlier extreme's bar index); the later of the two wins."""
    if not bars:
        return None
    window_from = len(bars) - len([b for b in bars if hhmm_of(b["ts"]) >= shift_minute(hhmm_of(bars[-1]["ts"]), -minutes + 1)])
    found = []
    for kind, sign in (("high", 1.0), ("low", -1.0)):
        got = new_extreme_vs_prior_swing([sign * float(b[kind]) for b in bars], window_from)
        if got is not None:
            found.append((got[0], kind, got[1]))
    if not found:
        return None
    i, kind, j = max(found)
    return kind, i, j


def mechanical_cause(history: MarketHistory, day: str, hhmm: str) -> bool:
    """VOLATILITY-14's mechanical causes: a release in its first reaction, the last 0DTE hour, the monthly expiry (the
    third Friday) or a pre-holiday session."""
    if hhmm >= LAST_HOUR_FROM:
        return True
    d = date.fromisoformat(day)
    next_weekday = d + timedelta(days=3 if d.weekday() == 4 else 1)          # a weekend is no holiday: every Friday was mechanical
    if (d.weekday() == 4 and 15 <= d.day <= 21) or next_trading_day(d) > next_weekday:
        return True
    now = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=events.ET)
    return any(e.start <= now < e.start + timedelta(minutes=REACTION_MIN) for e in calendar_rows(history, day))


# ---------------------------------------------------------------- the calendar

def calendar_rows(history: MarketHistory, day: str) -> list[events.Event]:
    path = history.calendar_path or events.CALENDAR
    return events.session_rows(date.fromisoformat(day), path)


def calendar_covers(history: MarketHistory, day: str) -> bool:
    return events.uncovered(date.fromisoformat(day), history.calendar_path or events.CALENDAR) is None


def read_moment(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=events.ET)


def latest_release(history: MarketHistory, day: str, hhmm: str) -> events.Event | None:
    """The latest release of the day whose first REACTION_MIN minutes are over by the read, within DIGEST_MIN of it; Fed
    speakers' remarks are not releases."""
    now = read_moment(day, hhmm)
    done = [e for e in calendar_rows(history, day) if e.tier != events.FED_SPEAKER
            and e.start + timedelta(minutes=REACTION_MIN) <= now <= e.start + timedelta(minutes=DIGEST_MIN)]
    return max(done, key=lambda e: e.start) if done else None


def reaction_prices(history: MarketHistory, record: dict, view: DayView, e: events.Event, hhmm: str, symbol: str = SPX,
                    night_bars: list[dict] | None = None) -> tuple[float, float, float] | None:
    """(price at the release, price 15 minutes after it, price now) for a release in the session from the symbol's
    bars, or for one before the open from the night's bars with the read's quote as now."""
    start, end = e.start.strftime("%H:%M"), (e.start + timedelta(minutes=REACTION_MIN)).strftime("%H:%M")
    if e.start.strftime("%H:%M") >= "09:30":
        s = view.series(symbol)
        p0, p1, now = s.close_before(start), s.close_before(end), s.close_before(hhmm)
        if p0 is None and start == "09:30" and s.bars and hhmm_of(s.bars[0]["ts"]) == "09:30":
            p0 = float(s.bars[0]["open"])                         # a release at the open: the first session bar's open
    else:
        night = Series(night_bars_up_to(night_bars or [], view.day, "09:30"))
        p0, p1 = night.close_before(start), night.close_before(end)
        now = view.series(symbol).close_before(hhmm) if symbol != SPX else None
        if now is None:
            quote = ((record.get("market_context") or {}).get(ES if symbol == SPX else symbol) or {}).get("value")
            now = float(quote) if isinstance(quote, (int, float)) else None
        if now is None and symbol == SPX:                         # no /ES quote on the read: the night's last /ES bar by it
            last = night_bars_up_to(night_bars or [], view.day, hhmm)
            now = float(last[-1]["close"]) if last else None
    return (p0, p1, now) if p0 is not None and p1 is not None and now is not None else None


# ---------------------------------------------------------------- the answerers

def answer_trend_04(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    share = path_share(view, hhmm)
    if share is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, share, path_share)
    return {"top": "clean", "middle": "mixed", "bottom": "choppy"}[third_of(rank)] if rank is not None else None


def answer_trend_07(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    m = vwap_balance(view, hhmm)
    if m is None:
        return None
    since, flips, latest = m
    flip_rank = rank_vs_prior(history, day, hhmm, flips, lambda v, h: part_of(vwap_balance(v, h), 1))
    since_rank = rank_vs_prior(history, day, hhmm, since, lambda v, h: part_of(vwap_balance(v, h), 0))
    if flip_rank is None or since_rank is None:
        return None
    if third_of(flip_rank) == "top":
        return "orbiting"
    if third_of(since_rank) == "top":
        return f"long {'above' if latest > 0 else 'below'} VWAP"
    return "recently crossed"


def slot_volumes(view: DayView, hhmm: str) -> dict[tuple[str, str], float]:
    """SPY's volume per finished 5-minute slot of the last 30 minutes before ``hhmm``."""
    spy = view.series(SPY)
    out = {}
    for lo, hi in FIVE_MINUTE_SLOTS:
        if shift_minute(hi, 1) <= hhmm and minutes_between(lo, hhmm) <= WINDOW_30:
            inside = spy.between(lo, shift_minute(hi, 1))
            if inside:
                out[(lo, hi)] = sum(float(b.get("volume") or 0.0) for b in inside)
    return out


def answer_trend_11(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    slots = slot_volumes(view, hhmm)
    if not slots or not bars:
        return None
    prior = prior_views(history, day)
    spike = None
    for slot in sorted(slots):
        base = [v for p in prior if (v := slot_volumes(p, shift_minute(slot[1], 1)).get(slot)) is not None]
        if len(base) < MIN_HISTORY_SESSIONS:
            return None
        if slots[slot] >= sorted(base)[max(int(math.ceil(SPIKE_SHARE * len(base))) - 1, 0)]:
            spike = slot
    if spike is None:
        return "none"
    inside = view.series(SPX).between(spike[0], shift_minute(spike[1], 1))
    if not inside:
        return None
    net = float(inside[-1]["close"]) - float(inside[0]["open"])
    if net == 0:
        return "none"
    midpoint = (max(float(b["high"]) for b in inside) + min(float(b["low"]) for b in inside)) / 2.0
    now = float(bars[-1]["close"])
    held = now > midpoint if net > 0 else now < midpoint
    return f"{'up' if net > 0 else 'down'} spike {'held' if held else 'faded'}"


def answer_volatility_04(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    ratio = range_ratio(view, hhmm)
    if ratio is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, ratio, range_ratio)
    return {"bottom": "compressed", "middle": "normal", "top": "expanded"}[third_of(rank)] if rank is not None else None


def opening_vix_surprise(history: MarketHistory, view: DayView) -> float | None:
    """VOLATILITY-07's measure: the 09:35 VIX less what the prior session's last VIX and the gap alone would leave it."""
    vix = view.series(VIX).close_before(SETTLED_OPEN)
    opened = view.series(SPX).close_before(SETTLED_OPEN)
    last_vix, prior_close = prior_close_of(history, view.day, VIX), prior_close_of(history, view.day, SPX)
    if vix is None or opened is None or last_vix is None or not prior_close:
        return None
    gap_pct = (opened / prior_close - 1.0) * 100.0
    return vix - (last_vix + VIX_PER_GAP_PCT * gap_pct)


def answer_volatility_07(record, bars, history, day, hhmm):
    if hhmm < SETTLED_OPEN or hhmm > OPENING_READS_UNTIL:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    surprise = opening_vix_surprise(history, view)
    if surprise is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, surprise, lambda v, h: opening_vix_surprise(history, v))
    return {"top": "richer", "middle": "in line", "bottom": "cheaper"}[third_of(rank)] if rank is not None else None


def implied_gap_ratio(history: MarketHistory, day: str, prior_day: str, settled_open: float) -> float | None:
    """VOLATILITY-08's measure: the gap from the prior close to the settled open over the one-day move the prior
    close's VIX implied (prior close x VIX / 100 / sqrt(252)), signed."""
    prior_bars = history.spx_bars_by_day.get(prior_day)
    vix_rows = [r for r in history.daily_closes.get(VIX, []) if r.get("day", "") < day and isinstance(r.get("close"), (int, float))]
    if not prior_bars or not vix_rows or vix_rows[-1]["day"] != prior_day:
        return None
    prior_close, vix = float(prior_bars[-1]["close"]), float(vix_rows[-1]["close"])
    implied = prior_close * vix / 100.0 / math.sqrt(ONE_DAY_MOVE_DAYS)
    return (settled_open - prior_close) / implied if implied > 0 else None


def answer_volatility_08(record, bars, history, day, hhmm):
    if hhmm < SETTLED_OPEN or hhmm > OPENING_READS_UNTIL:       # one answer a day: stale after the opening reads
        return None
    opened = Series(bars).close_before(SETTLED_OPEN)
    days = sorted(history.spx_bars_by_day)
    if opened is None or not days or days[-1] >= day:
        return None
    ratio = implied_gap_ratio(history, day, days[-1], opened)
    if ratio is None:
        return None
    prior = []
    for before, d in zip(days, days[1:]):
        settled = Series(history.spx_bars_by_day[d]).close_before(SETTLED_OPEN)
        r = implied_gap_ratio(history, d, before, settled) if settled is not None else None
        if r is not None:
            prior.append(abs(r))
    rank = rank_fraction(abs(ratio), prior)
    if rank is None:
        return None
    size = third_of(rank)
    if size == "bottom":
        return "small"
    return f"{'large' if size == 'top' else 'normal'} {'up' if ratio > 0 else 'down'}"


def front_curve(view: DayView, hhmm: str) -> float | None:
    short, spot = view.series(VIX9D).close_before(hhmm), view.series(VIX).close_before(hhmm)
    return short / spot if short is not None and spot else None


def answer_volatility_12(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    ratio = front_curve(view, hhmm)
    if ratio is None:
        return None
    if ratio >= 1.0:
        return "inverted"
    rank = rank_vs_prior(history, day, hhmm, ratio, front_curve)
    if rank is None:
        return None
    return "calm" if third_of(rank) == "bottom" else "normal"


def answer_volatility_14(record, bars, history, day, hhmm):
    found = session_new_extreme(bars)
    if found is None:                      # no new extreme: LEVELS-05 says so
        return None
    kind, i, j = found
    if mechanical_cause(history, day, hhmm):
        return "mechanical"
    vix = today_view(history, day, bars, record["row_ts"]).series(VIX)
    at_new, at_old = vix.close_before(shift_minute(hhmm_of(bars[i]["ts"]), 1)), vix.close_before(shift_minute(hhmm_of(bars[j]["ts"]), 1))
    if at_new is None or at_old is None:
        return None
    confirms = abs(at_new - at_old) < VIX_TIE or (at_new < at_old if kind == "high" else at_new > at_old)
    return f"new {kind}, VIX {'confirms' if confirms else 'diverges'}"


def answer_volatility_17(record, bars, history, day, hhmm):
    rows = [r for r in history.daily_closes.get(SPX, []) if r.get("day", "") < day
            and all(isinstance(r.get(k), (int, float)) and r.get(k) for k in ("high", "low", "close"))]
    ranges = [(float(r["high"]) - float(r["low"])) / float(r["close"]) for r in rows]
    n, k = RANGE_REGIME_AVERAGE, RANGE_REGIME_SESSIONS
    if len(ranges) < n + k:
        return None
    averages = [statistics.fmean(ranges[i - n:i]) for i in range(n, len(ranges) + 1)]
    rank = rank_fraction(averages[-1], averages[-1 - k:-1])
    return {"bottom": "quiet", "middle": "normal", "top": "wide"}[third_of(rank)] if rank is not None else None


def answer_levels_07(record, bars, history, day, hhmm):
    night = night_bars_up_to(history.es_night_bars_by_day.get(day, []), day, "09:30")
    quote = ((record.get("market_context") or {}).get(ES) or {}).get("value")
    es_now = float(quote) if isinstance(quote, (int, float)) else today_view(history, day, bars, record["row_ts"]).series(ES).close_before(hhmm)
    if not night or es_now is None:
        return None
    last = float(night[-1]["close"])
    if not last or abs(es_now / last - 1.0) > CONTRACT_JUMP:
        return None
    high, low = max(float(b["high"]) for b in night), min(float(b["low"]) for b in night)
    return "above overnight high" if es_now > high else "below overnight low" if es_now < low else "inside overnight range"


def barrier_room(history: MarketHistory, view: DayView, hhmm: str, sigma: float) -> float | None:
    """LEVELS-10's measure: the distance, as a share of price, to the nearest level beyond price in the direction of the
    last 30-minute move: the prior session's high, low and close, the night's /ES edges moved onto SPX, the session's
    high and low before the last 30 minutes (the half hour's own new extreme is LEVELS-05's), and the next round number;
    a level within BARRIER_SKIP_SIGMA of price is passed over."""
    spx = view.series(SPX)
    bars = spx.bars[:spx.count_before(hhmm)]
    before = spx.bars[:spx.count_before(shift_minute(hhmm, -WINDOW_30))]
    move = spx.move_pct(hhmm, WINDOW_30)
    if not bars or not move:
        return None
    price, side = float(bars[-1]["close"]), 1 if move > 0 else -1
    levels = _reference_levels(history, view.day, bars[0])
    prior_days = trailing_days(history.spx_bars_by_day, view.day, 1)
    if prior_days:
        levels.append(float(history.spx_bars_by_day[prior_days[0]][-1]["close"]))
    if before:
        levels += [max(float(b["high"]) for b in before), min(float(b["low"]) for b in before)]
    levels.append((math.floor(price / ROUND_STEP) + (1 if side > 0 else 0)) * ROUND_STEP)
    beyond = [(l - price) * side for l in levels if (l - price) * side > BARRIER_SKIP_SIGMA * sigma]
    return min(beyond) / price if beyond else None


def answer_levels_10(record, bars, history, day, hhmm):
    sigma = record.get("sigma")
    if not isinstance(sigma, (int, float)) or sigma <= 0:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    room = barrier_room(history, view, hhmm, sigma)
    if room is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, room, lambda v, h: barrier_room(history, v, h, sigma))
    return {"bottom": "close", "middle": "normal", "top": "far"}[third_of(rank)] if rank is not None else None


def edge_tests(bars: list[dict], edge: float, touch: float, high_edge: bool) -> list[tuple[int, int]]:
    """Runs of consecutive bars that reach within ``touch`` of ``edge`` (from below for a high edge, above for a low), as (first, last) indexes."""
    hit = [(float(b["high"]) >= edge - touch) if high_edge else (float(b["low"]) <= edge + touch) for b in bars]
    runs, start = [], None
    for i, h in enumerate(hit + [False]):
        if h and start is None:
            start = i
        elif not h and start is not None:
            runs.append((start, i - 1))
            start = None
    return runs


def answer_levels_12(record, bars, history, day, hhmm):
    sigma = record.get("sigma")
    if not isinstance(sigma, (int, float)) or sigma <= 0 or len(bars) < 2:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    spy = view.series(SPY)
    window = view.series(SPX).window(hhmm, WINDOW_30)
    before = bars[:len(bars) - len(window)]
    if not window:
        return None
    edges = []
    if before:
        edges += [(max(float(b["high"]) for b in before), True), (min(float(b["low"]) for b in before), False)]
    prior_days = trailing_days(history.spx_bars_by_day, day, 1)
    if prior_days:
        prior = history.spx_bars_by_day[prior_days[0]]
        edges += [(max(float(b["high"]) for b in prior), True), (min(float(b["low"]) for b in prior), False)]
    touch = EDGE_TOUCH_SIGMA * sigma
    tested = [(runs, edge, high_edge) for edge, high_edge in edges if len(runs := edge_tests(window, edge, touch, high_edge)) >= 2]
    if not tested:
        return "no tested edge"
    runs, edge, high_edge = max(tested, key=lambda t: (t[0][-1][1], len(t[0])))

    def measure(run):
        """(depth past the edge, mean SPY volume per bar of the test, the rejection over the bars after it, None with none
        after it yet)."""
        first, last = run
        inside, after = window[first:last + 1], window[last + 1:last + 1 + REJECTION_BARS]
        extreme = max(float(b["high"]) for b in inside) if high_edge else min(float(b["low"]) for b in inside)
        minutes = {hhmm_of(b["ts"]) for b in inside}
        volume = sum(float(b.get("volume") or 0.0) for b in spy.bars if hhmm_of(b["ts"]) in minutes) / len(inside)
        if not after:
            rejection = None
        elif high_edge:
            rejection = extreme - min(float(b["close"]) for b in after)
        else:
            rejection = max(float(b["close"]) for b in after) - extreme
        return (extreme - edge) * (1 if high_edge else -1), volume, rejection
    first, latest = measure(runs[0]), measure(runs[-1])
    if first[2] is None or latest[2] is None:       # a test still running has no rejection yet: depth and volume both must tire
        tiring = 2 if latest[0] < first[0] and latest[1] < first[1] else 0
    else:
        tiring = sum((latest[0] < first[0], latest[1] < first[1], latest[2] > first[2]))
    if tiring < 2:
        return "neither"
    return "buyers tiring" if high_edge else "sellers tiring"


def diary_at(history: MarketHistory, ts: str) -> dict | None:
    """The newest diary row at or before ``ts``."""
    rows = [r for r in history.diary_rows if r["ts"] <= ts]
    return rows[-1] if rows else None


def wall_levels(row: dict | None) -> dict[str, list[float]]:
    """{"call": [...], "put": [...]}: the 0DTE and 1-to-7-day walls a diary row names."""
    out = {"call": [], "put": []}
    for side in out:
        for key in (f"{side}_wall", f"{side}_wall_tenor"):
            level = (row or {}).get(key)
            if isinstance(level, (int, float)) and level not in out[side]:
                out[side].append(float(level))
    return out


def gamma_wall_levels(row: dict | None) -> dict[str, list[float]]:
    """{"call": [...], "put": [...]}: the near gamma walls a diary row names (gex_views.call_wall_gamma / put_wall_gamma)."""
    out = {"call": [], "put": []}
    for side in out:
        level = (row or {}).get(f"{side}_wall_gamma")
        if isinstance(level, (int, float)):
            out[side].append(float(level))
    return out


def answer_options_04(record, bars, history, day, hhmm):
    sigma = record.get("sigma")
    if not isinstance(sigma, (int, float)) or sigma <= 0 or not bars or not history.diary_rows:
        return None
    touch = WALL_TOUCH_SIGMA * sigma
    hour = Series(bars).window(hhmm, WINDOW_60)
    touched = None
    for b in hour:
        walls = gamma_wall_levels(diary_at(history, b["ts"]))
        for side in ("call", "put"):
            for level in walls[side]:
                if float(b["low"]) - touch <= level <= float(b["high"]) + touch:
                    touched = (side, level)
    if touched is None:
        return "no touch"
    side, level = touched
    now = float(bars[-1]["close"])
    held = now <= level if side == "call" else now >= level
    return f"{'held at' if held else 'broke'} {side} wall"


def answer_options_06(record, bars, history, day, hhmm):
    sigma = record.get("sigma")
    row = diary_at(history, record["row_ts"])
    if not isinstance(sigma, (int, float)) or sigma <= 0 or row is None or len(bars) < 10:
        return None
    hour = closes_of(Series(bars).window(hhmm, WINDOW_60))
    expected = sigma * math.sqrt(WINDOW_30 / 390.0)
    walls, gamma = wall_levels(row), gamma_wall_levels(row)
    levels = [l for l in [row.get("magnet")] + walls["call"] + walls["put"] + gamma["call"] + gamma["put"] if isinstance(l, (int, float))]
    def crossings(level: float) -> int:
        sides = [1 if c >= level else -1 for c in hour]
        return sum(1 for a, b in zip(sides, sides[1:]) if a != b)
    crossed = [(crossings(l), l) for l in levels if crossings(l) >= PIN_CROSSINGS]
    if not crossed:
        return "not pinned"
    level = max(crossed)[1]
    distance = abs(hour[-1] - level)
    if distance > expected:
        return "broke loose"
    swings, start = [], 0
    sides = [1 if c >= level else -1 for c in hour]
    for i in range(1, len(hour)):
        if sides[i] != sides[i - 1]:
            swings.append((max(abs(c - level) for c in hour[start:i]), i - start))
            start = i
    # the hour's first swing is cut by the window, a one-bar swing is a wiggle across the pin: neither sets the usual
    swings = [s for s in swings[1:] if s[1] >= MIN_SWING_BARS]
    if not swings:
        return "holding"
    wider = distance > statistics.median(s[0] for s in swings)
    slower = len(hour) - start > statistics.median(s[1] for s in swings)
    return "stretching" if wider and slower and distance > STRETCH_SHARE * expected else "holding"


def quote_width(sweeps: list[tuple[str, float, str]], hhmm: str) -> tuple[float, str] | None:
    """OPTIONS-08's measure: (the median quoted spread of the sweeps in the OPTION_WIDTH_MINUTES before ``hhmm`` in the
    newest sweep's delta bucket, that bucket)."""
    lo = shift_minute(hhmm, -OPTION_WIDTH_MINUTES)
    recent = [(spread, bucket) for ts, spread, bucket in sweeps if lo <= hhmm_of(ts) < hhmm]
    if not recent:
        return None
    bucket = recent[-1][1]
    return statistics.median(spread for spread, b in recent if b == bucket), bucket


def answer_options_08(record, bars, history, day, hhmm):
    """The width ranked only against the prior sessions quoting the same delta bucket at the minute (after ~13:10 the
    25-40 bucket empties and the nearer one, about half as wide, stands in), ties counted half: the spreads are whole
    ticks, so a strict rank would read the usual tick as tight."""
    today = [sweep for sweep in history.quote_sweeps_by_day.get(day, []) if sweep[0] <= record["row_ts"]]
    width = quote_width(today, hhmm)
    if width is None:
        return None
    prior = [w[0] for d in trailing_days(history.quote_sweeps_by_day, day)
             if (w := quote_width(history.quote_sweeps_by_day[d], hhmm)) is not None and w[1] == width[1]]
    if len(prior) < MIN_HISTORY_SESSIONS:
        return None
    rank = (sum(1 for w in prior if w < width[0]) + 0.5 * sum(1 for w in prior if w == width[0])) / len(prior)
    return "tight" if rank < 1 / 3 else "normal" if rank < 2 / 3 else "wide" if rank < 0.9 else "very wide"


def advance_decline(view: DayView, hhmm: str) -> float | None:
    """A prior session's advance-decline line at the minute: the derived $ADVN - $DECN, else the saved $ADD (clean once
    the session is saved the next day); nothing from labels/plausible.py's SAME_DAY_WRONG list.
    The read's own day is never read here (derived_add takes the record's derived value)."""
    up, down = view.series(ADVANCING).close_before(hhmm), view.series(DECLINING).close_before(hhmm)
    if up is not None and down is not None:
        return up - down
    return view.series(ADD).close_before(hhmm)


def derived_add(record: dict) -> float | None:
    """The read's $ADVN - $DECN from its market context; nothing else (not $ADD, not the same-day-wrong figures)."""
    context = record.get("market_context") or {}
    values = [(context.get(s) or {}).get("value") if isinstance(context.get(s), dict) else None for s in (ADVANCING, DECLINING)]
    return float(values[0]) - float(values[1]) if all(isinstance(v, (int, float)) for v in values) else None


def answer_breadth_02(record, bars, history, day, hhmm):
    add = derived_add(record)
    spx_prior, now = prior_close_of(history, day, SPX), Series(bars).close_before(hhmm)
    if add is None or not spx_prior or now is None:
        return None
    pairs = []
    for view in prior_views(history, day):
        a, prior, c = advance_decline(view, hhmm), prior_close_of(history, view.day, SPX), view.series(SPX).close_before(hhmm)
        if a is not None and prior and c is not None:
            pairs.append((c / prior - 1.0, a))
    if len(pairs) < MIN_HISTORY_SESSIONS:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    var = sum((x - mx) ** 2 for x in xs)
    beta = sum((x - mx) * (y - my) for x, y in pairs) / var if var > 0 else 0.0
    residuals = [y - my - beta * (x - mx) for x, y in pairs]
    rank = rank_fraction(add - my - beta * (now / spx_prior - 1.0 - mx), residuals)
    return {"top": "stronger", "middle": "in line", "bottom": "weaker"}[third_of(rank)] if rank is not None else None


def answer_breadth_03(record, bars, history, day, hhmm):
    found = session_new_extreme(bars)
    if found is None:                      # no new extreme: LEVELS-05 says so
        return None
    kind, i, j = found
    view = today_view(history, day, bars, record["row_ts"])
    then, now = shift_minute(hhmm_of(bars[j]["ts"]), 1), hhmm
    up = kind == "high"
    up_then, down_then = view.series(ADVANCING).close_before(then), view.series(DECLINING).close_before(then)
    up_now, down_now = view.series(ADVANCING).close_before(now), view.series(DECLINING).close_before(now)
    add_then = up_then - down_then if up_then is not None and down_then is not None else None      # the day's own line: derived only
    add_now = up_now - down_now if up_now is not None and down_now is not None else None
    iwm = view.series(IWM)
    iwm_bars, iwm_now = iwm.bars[:iwm.count_before(now)], iwm.close_before(now)
    names = megacaps(history, day)
    if add_then is None or add_now is None or not iwm_bars or iwm_now is None or names is None:
        return None
    iwm_extreme = max(float(b["close"]) for b in iwm_bars) if up else min(float(b["close"]) for b in iwm_bars)   # closes: live == saved
    basket = 0.0
    for symbol, w in names:
        a, b = view.series(symbol).close_before(then), view.series(symbol).close_before(now)
        if not a or b is None:
            return None
        basket += w * (b / a - 1.0)
    confirmed = sum((add_now > add_then if up else add_now < add_then,
                     abs(iwm_now - iwm_extreme) / iwm_extreme <= EXTREME_TOLERANCE,
                     basket > 0 if up else basket < 0))
    return f"new {kind}, {'confirmed' if confirmed >= 2 else 'not confirmed'}"


def largest_pull(view: DayView, hhmm: str, names: list[tuple[str, float]], links: dict[str, tuple[float, float]]) -> tuple[str, float, dict[str, float]] | None:
    """(the name, its weight-scaled 30-minute move beyond SPX, every name's residual)."""
    residuals = {}
    for symbol, _ in names:
        r = beyond_link(view, symbol, hhmm, WINDOW_30, links[symbol])
        if r is None:
            return None
        residuals[symbol] = r
    weights = dict(names)
    name = max(residuals, key=lambda s: abs(weights[s] * residuals[s]))
    return name, weights[name] * residuals[name], residuals


def answer_breadth_07(record, bars, history, day, hhmm):
    names = megacaps(history, day)
    links = links_for(history, day, [s for s, _ in names]) if names else None
    if links is None:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    got = largest_pull(view, hhmm, names, links)
    if got is None:
        return None
    name, pull, residuals = got
    rank = rank_vs_prior(history, day, hhmm, abs(pull), lambda v, h: unsigned(part_of(largest_pull(v, h, names, links), 1)))
    if rank is None:
        return None
    if third_of(rank) != "top":
        return "small"
    return f"large {'up' if pull > 0 else 'down'}"


def gap_pulls(history: MarketHistory, view: DayView, hhmm: str, names: list[tuple[str, float]], links: dict[str, tuple[float, float]]) -> tuple[str, float, float] | None:
    """(the name whose move since the prior close is largest beyond SPX by weight, that pull, the rest of the index's move)."""
    spx_prior, spx_now = prior_close_of(history, view.day, SPX), view.series(SPX).close_before(hhmm)
    if not spx_prior or spx_now is None:
        return None
    pulls, lead = {}, 0.0
    for symbol, w in names:
        r = since_close_beyond(history, view, symbol, hhmm, links[symbol])
        own_prior, own_now = prior_close_of(history, view.day, symbol), view.series(symbol).close_before(hhmm)
        if r is None or not own_prior or own_now is None:
            return None
        pulls[symbol] = w * r
        lead += w * (own_now / own_prior - 1.0)
    name = max(pulls, key=lambda s: abs(pulls[s]))
    return name, pulls[name], (spx_now / spx_prior - 1.0) - lead


def answer_breadth_09(record, bars, history, day, hhmm):
    names = megacaps(history, day)
    links = links_for(history, day, [s for s, _ in names]) if names else None
    if links is None:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    got = gap_pulls(history, view, hhmm, names, links)
    if got is None:
        return None
    name, pull, rest = got
    pull_rank = rank_vs_prior(history, day, hhmm, abs(pull), lambda v, h: unsigned(part_of(gap_pulls(history, v, h, names, links), 1)))
    rest_rank = rank_vs_prior(history, day, hhmm, abs(rest), lambda v, h: unsigned(part_of(gap_pulls(history, v, h, names, links), 2)))
    if pull_rank is None or rest_rank is None:
        return None
    if third_of(pull_rank) != "top":
        return "none in play"
    followed = (rest > 0) == (pull > 0) and third_of(rest_rank) != "bottom"
    return "in play, rest followed" if followed else "in play, rest did not"


def directional_volume_share(view: DayView, hhmm: str) -> tuple[float, int] | None:
    """FLOW-02's measure: (the share of SPY's volume over the last 30 minutes traded in the minutes SPX moved the way the
    half hour did, that way: +1 up, -1 down)."""
    spx, spy = view.series(SPX), view.series(SPY)
    bars = spx.window(hhmm, WINDOW_30)
    if len(bars) < 25:
        return None
    net = float(bars[-1]["close"]) - float(bars[0]["open"])
    if net == 0:
        return None
    volume = {hhmm_of(b["ts"]): float(b.get("volume") or 0.0) for b in spy.window(hhmm, WINDOW_30)}
    total = sum(volume.values())
    if total <= 0:
        return None
    with_it = sum(volume.get(hhmm_of(b["ts"]), 0.0) for b in bars if (float(b["close"]) - float(b["open"])) * net > 0)
    return with_it / total, 1 if net > 0 else -1


def answer_flow_02(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    m = directional_volume_share(view, hhmm)
    if m is None:
        return None
    share, way = m
    rank = rank_vs_prior(history, day, hhmm, share, lambda v, h: part_of(directional_volume_share(v, h), 0))
    if rank is None:
        return None
    side = "up" if way > 0 else "down"
    return {"top": f"confirmed {side}", "middle": "balanced", "bottom": f"diverging {side}"}[third_of(rank)]


def travel_per_volume(view: DayView, hhmm: str) -> float | None:
    """FLOW-04's measure: SPX points travelled over the last 30 minutes per million SPY shares; None when fewer than
    MIN_VOLUME_MINUTES of the 30 minutes carry SPY volume (a feed hole, not a quiet tape)."""
    c = closes_of(view.series(SPX).window(hhmm, WINDOW_30))
    spy = view.series(SPY).window(hhmm, WINDOW_30)
    volume = sum(float(b.get("volume") or 0.0) for b in spy)
    if len(c) < 25 or volume <= 0 or sum(1 for b in spy if float(b.get("volume") or 0.0) > 0) < MIN_VOLUME_MINUTES:
        return None
    return sum(abs(b - a) for a, b in zip(c, c[1:])) / (volume / 1e6)


def answer_flow_04(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    ratio = travel_per_volume(view, hhmm)
    if ratio is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, ratio, travel_per_volume)
    return {"bottom": "heavy with little travel", "middle": "normal", "top": "easy travel on light volume"}[third_of(rank)] if rank is not None else None


def volume_profile(history: MarketHistory, day: str) -> dict[float, float]:
    """FLOW-06's profile: SPY's volume per minute binned by SPX's close over the last PROFILE_SESSIONS sessions."""
    key = ("profile", day)
    if key in history.cache:
        return history.cache[key]
    bins: dict[float, float] = {}
    for view in prior_views(history, day)[-PROFILE_SESSIONS:]:
        volume = {hhmm_of(b["ts"]): float(b.get("volume") or 0.0) for b in view.series(SPY).bars}
        for b in view.series(SPX).bars:
            price_bin = math.floor(float(b["close"]) / PROFILE_BIN) * PROFILE_BIN
            bins[price_bin] = bins.get(price_bin, 0.0) + volume.get(hhmm_of(b["ts"]), 0.0)
    history.cache[key] = bins
    return bins


def answer_flow_06(record, bars, history, day, hhmm):
    bins = volume_profile(history, day)
    if not bars or len(bins) < 4:
        return None
    price = float(bars[-1]["close"])
    here = math.floor(price / PROFILE_BIN) * PROFILE_BIN
    volumes = sorted(v for v in bins.values() if v > 0)
    if not volumes:
        return None
    if here > max(bins):
        return "outside the profile, above"
    if here < min(bins):
        return "outside the profile, below"
    high_cut = volumes[min(int(NODE_HIGH_SHARE * len(volumes)), len(volumes) - 1)]
    nodes = [p for p, v in bins.items() if v >= high_cut]
    if here in nodes:
        return "at a node"
    above = min((p - here for p in nodes if p > here), default=math.inf)
    below = min((here - p for p in nodes if p < here), default=math.inf)
    if above == below:
        return "at a node"
    return "node above" if above < below else "node below"


def moved_sign(history: MarketHistory, day: str, view: DayView, symbol: str, hhmm: str, minutes: int) -> int | None:
    """+1 / -1 for a move above the bottom third of the symbol's own moves at this minute, 0 for a smaller one, None unknown."""
    move = view.series(symbol).move_pct(hhmm, minutes)
    if move is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, abs(move), lambda v, h: unsigned(v.series(symbol).move_pct(h, minutes)))
    if rank is None:
        return None
    return 0 if third_of(rank) == "bottom" else (1 if move > 0 else -1)


def answer_flow_07(record, bars, history, day, hhmm):
    """The day's SPY/QQQ/IWM from its quote snapshots (history.today_quote_bars), whether or not its bars are saved, so a
    backfilled read measures what the live read did."""
    view = DayView(day, bars, {s: cut_at(history.today_quote_bars.get(s, []), record["row_ts"]) for s in (SPY, QQQ, IWM)})
    signs = [moved_sign(history, day, view, s, hhmm, WINDOW_30) for s in (SPY, QQQ, IWM)]
    if None in signs:
        return None
    movers = [s for s in signs if s]
    if not movers:
        return "none moved"
    if len(movers) == 3 and len(set(movers)) == 1:
        return f"same way {'up' if movers[0] > 0 else 'down'}"
    return "in conflict" if len(set(movers)) == 2 else "mixed"


def macro_lean(view: DayView, hhmm: str, links: dict[str, tuple[float, float]]) -> float | None:
    """MACRO-01's measure: each outside market's 30-minute move beyond its usual multiple of SPX, in its own residual
    spread, signed by its usual link's direction, averaged."""
    parts = []
    for symbol in MACRO_COMPLEX:
        link = links[symbol]
        if link[1] == 0 or link[0] == 0:
            return None
        r = beyond_link(view, symbol, hhmm, WINDOW_30, link)
        if r is None:
            return None
        parts.append(r / link[1] * math.copysign(1.0, link[0]))
    return statistics.fmean(parts)


def answer_macro_01(record, bars, history, day, hhmm):
    links = links_for(history, day, MACRO_COMPLEX)
    if links is None:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    lean = macro_lean(view, hhmm, links)
    if lean is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, lean, lambda v, h: macro_lean(v, h, links))
    if rank is None:
        return None
    return "up" if rank >= 0.8 else "down" if rank < 0.2 else "no clear lean"


def daily_link(history: MarketHistory, day: str, symbol: str) -> float | None:
    """MACRO-02's multiple: the slope of SPX's close-to-close returns on the symbol's over the prior sessions."""
    key = ("daily_link", day, symbol)
    if key in history.cache:
        return history.cache[key]
    days = trailing_days(history.context_bars_by_day, day)
    pairs = []
    for before, d in zip(days, days[1:]):
        a, b = history.context_bars_by_day[before].get(symbol), history.context_bars_by_day[d].get(symbol)
        sa, sb = history.spx_bars_by_day.get(before), history.spx_bars_by_day.get(d)
        if a and b and sa and sb:
            pairs.append((float(b[-1]["close"]) / float(a[-1]["close"]) - 1.0, float(sb[-1]["close"]) / float(sa[-1]["close"]) - 1.0))
    beta = None
    if len(pairs) >= MIN_HISTORY_SESSIONS:
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        var = sum((x - mx) ** 2 for x in xs)
        beta = sum((x - mx) * (y - my) for x, y in pairs) / var if var > 0 else None
    history.cache[key] = beta
    return beta


def bond_gap(history: MarketHistory, view: DayView, hhmm: str, beta: float) -> float | None:
    """SPX's move since the prior close less what /ZN's move since its implies (beta times it)."""
    spx_prior, zn_prior = prior_close_of(history, view.day, SPX), prior_close_of(history, view.day, ZN)
    spx_now, zn_now = view.series(SPX).close_before(hhmm), view.series(ZN).close_before(hhmm)
    if not spx_prior or not zn_prior or spx_now is None or zn_now is None:
        return None
    return (spx_now / spx_prior - 1.0) - beta * (zn_now / zn_prior - 1.0)


def answer_macro_02(record, bars, history, day, hhmm):
    beta = daily_link(history, day, ZN)
    if beta is None:
        return None
    view = today_view(history, day, bars, record["row_ts"])
    gap = bond_gap(history, view, hhmm, beta)
    if gap is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, abs(gap), lambda v, h: unsigned(bond_gap(history, v, h, beta)))
    if rank is None:
        return None
    if third_of(rank) == "bottom":
        return "nowhere"
    # the top fifth is said apart: the macro_gap judgment gate reads it (FAR_GAP_RANK)
    return f"{'far ' if rank >= FAR_GAP_RANK else ''}{'up' if gap < 0 else 'down'}"


def answer_macro_07(record, bars, history, day, hhmm):
    view = today_view(history, day, bars, record["row_ts"])
    volume = view.series(ZN).volume(hhmm, WINDOW_30)
    if volume <= 0:
        return None
    rank = rank_vs_prior(history, day, hhmm, volume, lambda v, h: v.series(ZN).volume(h, WINDOW_30) or None)
    return {"bottom": "quiet", "middle": "normal", "top": "busy"}[third_of(rank)] if rank is not None else None


def day_tier(history: MarketHistory, d: date) -> str | None:
    """high / medium / ordinary for a day, None when the calendar does not cover it."""
    if events.uncovered(d, history.calendar_path or events.CALENDAR) is not None:
        return None
    kinds = {e.kind for e in events.on_day(d, history.calendar_path or events.CALENDAR)}
    return "high" if kinds & HIGH_TIER else "medium" if kinds & MEDIUM_TIER else "ordinary"


def answer_events_02(record, bars, history, day, hhmm):
    d = date.fromisoformat(day)
    today, before, after = day_tier(history, d), day_tier(history, previous_trading_day(d)), day_tier(history, next_trading_day(d))
    if today is None:
        return None
    if today == "high":
        return "high-tier day"
    if after is None:                      # the next session is past the calendar: it could be the eve of one
        return None
    if after == "high":
        return "eve of a high-tier day"
    if before is None:
        return None
    if before == "high":
        return "day after a high-tier day"
    return "medium-tier day" if today == "medium" else "ordinary"


def normal_day_sigma(history: MarketHistory, row_ts: str) -> float | None:
    """The normal-day sigma (labels/rulers.normal_day_sigma: the median morning ruler of the prior sessions), as the
    premarket lane stamps it on the day's reads before ``row_ts``."""
    return next((float(r["sigma"]) for r in reversed(history.premarket_reads)
                 if isinstance(r.get("row_ts"), str) and r["row_ts"] <= row_ts and isinstance(r.get("sigma"), (int, float)) and r["sigma"] > 0), None)


def answer_events_03(record, bars, history, day, hhmm):
    sigma = normal_day_sigma(history, record["row_ts"])        # the label rule's ruler, not the read's own morning one
    if not calendar_covers(history, day) or sigma is None:
        return None
    e = latest_release(history, day, hhmm)
    if e is None:
        return "no release"
    view = today_view(history, day, bars, record["row_ts"])
    prices = reaction_prices(history, record, view, e, hhmm, SPX, history.es_night_bars_by_day.get(day))
    if prices is None:
        return None
    p0, p1, now = prices
    first = p1 - p0
    if first == 0:
        return None
    side = 1 if first > 0 else -1
    if (now - p0) * side < 0:
        return f"reversed {'down' if side > 0 else 'up'}"
    way = "up" if side > 0 else "down"
    return f"{way} then {'extended' if (now - p1) * side > REACTION_EXTEND_SIGMA * sigma else 'held/faded'}"


def month_trading_days(d: date) -> list[date]:
    days, x = [], d.replace(day=1)
    while x.month == d.month:
        if is_trading_day(x):
            days.append(x)
        x += timedelta(days=1)
    return days


def month_position(d: date) -> tuple[int, int]:
    """(the day's number among the month's trading days, the trading days left after it)."""
    days = month_trading_days(d)
    before = sum(1 for x in days if x <= d)
    return before, len(days) - before


def month_gap(spx: list[dict], tlt: list[dict], through: str) -> float | None:
    """The stock-minus-bond return from the prior month's last close to the last close on or before ``through``."""
    def ret(rows):
        upto = [r for r in rows if r.get("day", "") <= through and isinstance(r.get("close"), (int, float))]
        if not upto:
            return None
        month = upto[-1]["day"][:7]
        prior = [r for r in upto if r["day"][:7] < month]
        return float(upto[-1]["close"]) / float(prior[-1]["close"]) - 1.0 if prior and prior[-1]["close"] else None
    a, b = ret(spx), ret(tlt)
    return a - b if a is not None and b is not None else None


def answer_session_02(record, bars, history, day, hhmm):
    d = date.fromisoformat(day)
    nth, left = month_position(d)
    if nth <= MONTH_FIRST_DAYS:
        return "new-month inflow"
    if left >= MONTH_LAST_DAYS:
        return None
    # the month-end window's gap is measured once, through the last close before its first day, so its days agree
    window_from = month_trading_days(d)[-MONTH_LAST_DAYS].isoformat()
    spx, tlt = history.daily_closes.get(SPX, []), history.daily_closes.get("TLT", [])
    spx, tlt = [r for r in spx if r.get("day", "") < day], [r for r in tlt if r.get("day", "") < day]
    gap = month_gap([r for r in spx if r["day"] < window_from], [r for r in tlt if r["day"] < window_from], window_from)
    if gap is None:
        return None
    month_ends = sorted({r["day"][:7] for r in spx if r["day"][:7] < day[:7]})
    prior = [g for m in month_ends if (g := month_gap(spx, tlt, f"{m}-31")) is not None]
    rank = rank_fraction(gap, prior)
    if rank is None:
        return None
    return "sell big" if rank >= 0.8 else "sell" if rank >= 0.6 else "small" if rank >= 0.4 else "buy" if rank >= 0.2 else "buy big"


def usual_fund_volumes(history: MarketHistory, day: str, hhmm: str) -> dict[str, float] | None:
    """Each 3x fund's median 30-minute share volume at this minute over the prior sessions; None when one has none.
    Cached on the history for the day and minute."""
    key = ("fund_volume", day, hhmm)
    if key not in history.cache:
        usual = {}
        for symbol in BULL_FUNDS + BEAR_FUNDS:
            seen = [v for view in prior_views(history, day) if (v := view.series(symbol).volume(hhmm, WINDOW_30)) > 0]
            usual[symbol] = statistics.median(seen) if len(seen) >= MIN_HISTORY_SESSIONS else None
        history.cache[key] = None if None in usual.values() else usual
    return history.cache[key]


def leveraged_tilt(history: MarketHistory, day: str, view: DayView, hhmm: str) -> tuple[float, float] | None:
    """SENTIMENT-02's measure: (bull-minus-bear over both, where bull and bear are the equal-weight means of each fund's
    30-minute share volume over its own usual at this minute, SPX's 30-minute move). Each fund against its own usual, so
    the bear funds' decay and TQQQ/SQQQ's size do not tilt it."""
    usual = usual_fund_volumes(history, day, hhmm)
    spx = view.series(SPX).move_pct(hhmm, WINDOW_30)
    if usual is None or spx is None:
        return None
    bull = statistics.fmean(view.series(s).volume(hhmm, WINDOW_30) / usual[s] for s in BULL_FUNDS)
    bear = statistics.fmean(view.series(s).volume(hhmm, WINDOW_30) / usual[s] for s in BEAR_FUNDS)
    if bull + bear <= 0:
        return None
    return (bull - bear) / (bull + bear), spx


def answer_sentiment_02(record, bars, history, day, hhmm):
    if history.today_context_from_quotes:       # the funds' live volume is quote snapshots, not the minute bars it was fitted on
        return None
    view = today_view(history, day, bars, record["row_ts"])
    m = leveraged_tilt(history, day, view, hhmm)
    if m is None:
        return None
    key = ("tilt_fit", day)
    if key not in history.cache:
        pairs = [t for v in prior_views(history, day) for end in LINK_WINDOW_ENDS if (t := leveraged_tilt(history, day, v, end)) is not None]
        fit = None
        if len(pairs) >= MIN_LINK_PAIRS:
            xs, ys = [p[1] for p in pairs], [p[0] for p in pairs]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            var = sum((x - mx) ** 2 for x in xs)
            fit = (my, mx, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var > 0 else 0.0)
        history.cache[key] = fit
    fit = history.cache[key]
    if fit is None:
        return None

    def residual(t):
        return t[0] - fit[0] - fit[2] * (t[1] - fit[1])
    rank = rank_vs_prior(history, day, hhmm, residual(m), lambda v, h: residual(t) if (t := leveraged_tilt(history, day, v, h)) is not None else None)
    return {"top": "high", "middle": "normal", "bottom": "low"}[third_of(rank)] if rank is not None else None


def loading_event_ahead(history: MarketHistory, day: str, hhmm: str) -> bool | None:
    """A ruler-loading event (labels/vol.py LOADING_EVENTS: the Fed's decision, the chair's testimony or Jackson Hole) due
    within LOADING_AHEAD_MIN minutes; None when the calendar does not cover the day."""
    if not calendar_covers(history, day):
        return None
    now = read_moment(day, hhmm)
    return any(e.kind in LOADING_EVENTS and now < e.start <= now + timedelta(minutes=LOADING_AHEAD_MIN) for e in calendar_rows(history, day))


def implied_vol_trending(record: dict) -> bool | None:
    """iv.trend_30min on the read: at-the-money implied volatility rose or fell past its flat band; None when omitted."""
    s = flat_labels(record).get("iv.trend_30min")
    if not s:
        return None
    if "stayed within" in s:
        return False
    return True if "implied volatility rose" in s or "implied volatility fell" in s else None


def yield_move_fired(history: MarketHistory, day: str, view: DayView, hhmm: str) -> bool | None:
    """The ten-year yield's move since the prior close in the top third at this minute (judgment.YieldMove.fired's rule:
    $TNX against its prior daily close, ranked against the prior sessions at the same minute)."""
    def move(v: DayView, h: str) -> float | None:
        rows = [r for r in history.daily_closes.get(TNX, []) if r.get("day", "") < v.day and isinstance(r.get("close"), (int, float))]
        now = v.series(TNX).close_before(h)
        return abs(now - float(rows[-1]["close"])) if rows and now is not None else None
    m = move(view, hhmm)
    if m is None:
        return None
    rank = rank_vs_prior(history, day, hhmm, m, move)
    return rank >= THIRD_HI if rank is not None else None


def answer_volatility_18(record, bars, history, day, hhmm):
    """Coiled or resting, asked only of a compressed range (VOLATILITY-04): coiled when a ruler-loading event is due within
    the hour, implied volatility is trending, the ten-year yield moves more than usual, or a heavyweight is in play
    (BREADTH-09); resting when none is. The cheap checks go first: BREADTH-09 is measured only when they are all quiet."""
    if answer_volatility_04(record, bars, history, day, hhmm) != "compressed":
        return None
    view = today_view(history, day, bars, record["row_ts"])
    checks = (lambda: loading_event_ahead(history, day, hhmm), lambda: implied_vol_trending(record),
              lambda: yield_move_fired(history, day, view, hhmm),
              lambda: (lambda a: a.startswith("in play") if a else None)(answer_breadth_09(record, bars, history, day, hhmm)))
    measured = False
    for check in checks:
        fired = check()
        if fired:
            return "coiled"
        measured = measured or fired is not None
    return "resting" if measured else None


MARKET_ANSWERERS = {
    "TREND-04": answer_trend_04, "TREND-07": answer_trend_07, "TREND-11": answer_trend_11,
    "VOLATILITY-04": answer_volatility_04, "VOLATILITY-07": answer_volatility_07, "VOLATILITY-08": answer_volatility_08,
    "VOLATILITY-12": answer_volatility_12, "VOLATILITY-14": answer_volatility_14, "VOLATILITY-17": answer_volatility_17,
    "VOLATILITY-18": answer_volatility_18,
    "LEVELS-07": answer_levels_07, "LEVELS-10": answer_levels_10, "LEVELS-12": answer_levels_12,
    "OPTIONS-04": answer_options_04, "OPTIONS-06": answer_options_06, "OPTIONS-08": answer_options_08,
    "BREADTH-02": answer_breadth_02, "BREADTH-03": answer_breadth_03, "BREADTH-07": answer_breadth_07, "BREADTH-09": answer_breadth_09,
    "FLOW-02": answer_flow_02, "FLOW-04": answer_flow_04, "FLOW-06": answer_flow_06, "FLOW-07": answer_flow_07,
    "MACRO-01": answer_macro_01, "MACRO-02": answer_macro_02, "MACRO-07": answer_macro_07,
    "EVENTS-02": answer_events_02, "EVENTS-03": answer_events_03, "SESSION-02": answer_session_02, "SENTIMENT-02": answer_sentiment_02,
}
