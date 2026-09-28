"""How a market beside the index usually moves with it, and how far it moved beyond that: what the
leadership and macro families measure every fund, stock and outside market by.

* The usual multiple (the beta): the slope of a symbol's 30-minute returns on the index's over the prior
  sessions on file (up to 20), on the clock's half hours from 10:00 to 16:00.
* A move beyond the usual multiple (the residual) is the symbol's return less the multiple times the
  index's, written in SPX sigma: a share of the day's morning anchor.
* Its own usual move (OwnMoves): the symbol's move over the same minutes to this clock on the prior
  sessions, so "past its usual move" is a rank against its own, never a fixed line.

Every size is ranked against the same measure at this minute on up to the last 20 sessions, needing 10
(ranks.rank_sessions); the multiple is a fit and needs MIN_RANK_SESSIONS sessions.

Point in time: SPX comes from the bars that finished by the moment, every other symbol from the market
context as it was known then; a value older than VALUE_MAX_AGE_MIN is a stopped feed and counts as none.
"""
from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Callable

from ..cuts import MIN_RANK_SESSIONS, WINDOW_30_MIN
from ..sessions import session_open
from .measures import ET, ONE_MINUTE, bar_time
from .ranks import SameClockRank, rank_sessions, same_clock_market, same_clock_values
from .rulers import SigmaRuler

if TYPE_CHECKING:
    from ..state_builder import MarketContext, Scene

SPX = "$SPX"                    # the index itself, read from the SPX minute bars
VALUE_MAX_AGE_MIN = 5           # a price older than this at a window's edge is a feed that had stopped
LINK_FIRST_END = time(10, 30)   # the first half hour the usual multiple is measured on (09:30 has no finished bar)
LINK_LAST_END = time(16, 0)


class Session:
    """One session's prices as they were known: SPX from its minute bars, the rest from its market context."""

    def __init__(self, bars: list[dict], market: MarketContext | None):
        self.bars, self.market = bars, market
        self._finished = [bar_time(b) + ONE_MINUTE for b in bars]

    def price(self, symbol: str, t: datetime) -> float | None:
        if symbol != SPX:
            return self.market.last(symbol, t, max_age_min=VALUE_MAX_AGE_MIN) if self.market else None
        k = bisect.bisect_right(self._finished, t)
        if not k or t - self._finished[k - 1] > timedelta(minutes=VALUE_MAX_AGE_MIN):
            return None
        return float(self.bars[k - 1]["close"])

    def move(self, symbol: str, start: datetime, end: datetime) -> float | None:
        """The symbol's return from ``start`` to ``end``; None without a price at either edge."""
        a, b = self.price(symbol, start), self.price(symbol, end)
        return b / a - 1.0 if a and b is not None else None


@dataclass(frozen=True)
class UsualLink:
    multiple: float     # the symbol's usual 30-minute return per unit of the index's (the beta)
    spread: float       # the standard deviation of its 30-minute return beyond the multiple
    sessions: int


def link_windows(day: str) -> list[tuple[datetime, datetime]]:
    """The day's half hours the usual multiple is measured on."""
    d = date.fromisoformat(day)
    end, last = datetime.combine(d, LINK_FIRST_END, tzinfo=ET), datetime.combine(d, LINK_LAST_END, tzinfo=ET)
    out = []
    while end <= last:
        out.append((end - timedelta(minutes=WINDOW_30_MIN), end))
        end += timedelta(minutes=WINDOW_30_MIN)
    return out


def usual_link(scene: Scene, symbol: str, index: str = SPX) -> UsualLink | None:
    """How ``symbol`` usually moves with ``index`` over the prior sessions; None under MIN_RANK_SESSIONS
    sessions that carry both (the floor a rank has), or when the index never moved."""
    xs, ys, days = [], [], 0
    for day, bars in scene.prior_bars.items():
        s = Session(bars, scene.prior_markets.get(day))
        pairs = [(x, y) for a, b in link_windows(day) if (x := s.move(index, a, b)) is not None and (y := s.move(symbol, a, b)) is not None]
        if pairs:
            days += 1
            xs.extend(x for x, _ in pairs)
            ys.extend(y for _, y in pairs)
    if days < MIN_RANK_SESSIONS:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    return UsualLink(beta, statistics.pstdev(y - beta * x for x, y in zip(xs, ys)), days)


class AgainstIndex:
    """The reads of one moment against the index: today's session, the day's sigma as a share of price,
    and each symbol's usual link, measured once per read."""

    def __init__(self, scene: Scene, ruler: SigmaRuler):
        self.scene, self.ruler = scene, ruler
        self.today = Session(scene.bars, scene.market)
        self.sigma_share = ruler.points / scene.spot
        self._links: dict[tuple[str, str], UsualLink | None] = {}

    @property
    def ruler_note(self) -> str:
        """What a sentence adds when today's sigma is a stand-in for the morning anchor."""
        return "; ruler estimated" if self.ruler.estimated else ""

    def link(self, symbol: str, index: str = SPX) -> UsualLink | None:
        if (symbol, index) not in self._links:
            self._links[symbol, index] = usual_link(self.scene, symbol, index)
        return self._links[symbol, index]

    def move(self, symbol: str, minutes: int) -> float | None:
        """The symbol's return over the last ``minutes`` minutes."""
        now = self.scene.now
        return self.today.move(symbol, now - timedelta(minutes=minutes), now)

    def sigma(self, ret: float) -> float:
        """A return in SPX sigma."""
        return ret / self.sigma_share

    def same_clock(self, measure: Callable[[Session, datetime, float], float | None]) -> list[float]:
        """``measure(session, then, sigma_share)`` on each prior session at this minute (ranks.same_clock_values),
        its session holding the bars and the market context known by then."""
        def on_day(bars: list[dict], then: datetime, sigma_points: float | None) -> float | None:
            if sigma_points is None:
                return None
            s = Session(bars, self.scene.prior_markets.get(then.date().isoformat()))
            spot = s.price(SPX, then)
            return measure(s, then, sigma_points / spot) if spot else None
        return same_clock_values(self.scene, on_day)

    def rank(self, value: float, measure: Callable[[Session, datetime, float], float | None],
             what: str) -> tuple[SameClockRank | None, str | None]:
        """``value`` against the same measure at this minute of the prior sessions (ranks.rank_sessions), or why it
        has no rank, naming ``what`` the sessions lacked."""
        return rank_sessions(value, self.same_clock(measure), what)


class OwnMoves:
    """Each symbol's own ``minutes`` move to this clock on the prior sessions, what "past its usual move" is
    judged by: the size of a move ranked against the symbol's own at this minute. A return is in the symbol's
    own units, which the index's ruler never touches, so every session with the market context counts
    (ranks.same_clock_market)."""

    def __init__(self, scene: Scene, symbols: list[str], minutes: int):
        self.minutes = minutes
        span = timedelta(minutes=minutes)
        self.sizes = {s: [abs(m) for m in same_clock_market(scene, lambda mk, then, s=s: Session([], mk).move(s, then - span, then))]
                      for s in symbols}

    def rank(self, symbol: str, move: float) -> tuple[SameClockRank | None, str | None]:
        return rank_sessions(abs(move), self.sizes[symbol], f"a {self.minutes}-minute move of {symbol} at this minute")

    def past_usual(self, symbol: str, move: float) -> bool | None:
        """Whether the size of ``move`` is above the bottom third of the symbol's own at this minute: the symbol
        moved; None without the sessions to say."""
        rank, _ = self.rank(symbol, move)
        return None if rank is None else rank.band != "bottom third"


def beyond(link: UsualLink, move: float, index_move: float) -> float:
    """The part of a return its usual multiple of the index's return does not explain."""
    return move - link.multiple * index_move


def beyond_rank(against: AgainstIndex, symbol: str, minutes: int) -> tuple[float, UsualLink, SameClockRank] | str:
    """``symbol``'s move beyond its usual multiple of SPX over the last ``minutes``, in sigma, with its link and
    its rank against the same minutes of the prior sessions; or the reason it cannot be measured."""
    move, index_move = against.move(symbol, minutes), against.move(SPX, minutes)
    if move is None or index_move is None:
        return needs_move([s for s, m in ((symbol, move), (SPX, index_move)) if m is None], minutes)
    link = against.link(symbol)
    if link is None:
        return needs_link([symbol])
    value = against.sigma(beyond(link, move, index_move))

    def then_beyond(s: Session, then: datetime, sigma_share: float) -> float | None:
        start = then - timedelta(minutes=minutes)
        m, i = s.move(symbol, start, then), s.move(SPX, start, then)
        return None if m is None or i is None else beyond(link, m, i) / sigma_share
    rank, why = against.rank(value, then_beyond, f"{symbol} and {SPX} at this minute")
    return (value, link, rank) if rank is not None else why


def needs_move(symbols: list[str], minutes: int) -> str:
    return f"needs a price for {', '.join(symbols)} now and {minutes} minutes ago"


def needs_link(symbols: list[str], index: str = SPX) -> str:
    return f"needs {MIN_RANK_SESSIONS} prior sessions of half hours with {', '.join(symbols)} and {index} to know the usual multiple"


def minutes_back(end: datetime, minutes: int) -> int:
    """``minutes``, or the minutes since the session's first finished minute (09:31) when a window of them to ``end``
    would start before it: at 09:40 the 10-minute window is the 9 since 09:31, on today and each prior day alike."""
    return min(minutes, int((end - session_open(end) - ONE_MINUTE) / ONE_MINUTE))


def against_usual(value: float, side: int) -> str:
    """What a sentence adds when a signed value and the side of its same-clock rank (its fifth or its third) point
    opposite ways (the prior sessions at this minute sat mostly on one side of zero), so the verdict reads true."""
    if not side or (value >= 0) == (side > 0):
        return ""
    return f", {'above' if side > 0 else 'below'} the usual for this minute"
