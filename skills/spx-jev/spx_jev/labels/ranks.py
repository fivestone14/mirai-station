"""A value placed against the same minute of the prior sessions.

SPX's size swings with the clock (the 09:30-10:30 range runs about twice the afternoon's), so a size is
judged against what the same minute looked like on up to the last 20 sessions, never against a fixed
cut: "higher than 17 of the last 20 sessions at this minute, top third". A rank needs at least
MIN_RANK_SESSIONS sessions; the owner's rule for every threshold that sizes or judges a market measure
(rank_sessions) takes up to the last NIGHT_RANK_COUNT sessions and needs SAME_CLOCK_MIN_SESSIONS of them,
else the label is omitted with the reason. A session whose morning ruler was estimated
(rulers.morning_ruler) is left out of every rank whose base is built on rank_days or same_clock_values:
the sigma-scaled measures and the diary's own. The tape, breadth and SPY ranks, whose measures the ruler
never touches, keep every session (same_clock_market).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Callable

from ..cuts import BOTTOM_FIFTH, MIN_RANK_SESSIONS, NIGHT_RANK_COUNT, SAME_CLOCK_MIN_SESSIONS, TICK_BURST_PCT, TOP_FIFTH
from .measures import ET, ONE_MINUTE, bar_time, move_size
from .words import third

if TYPE_CHECKING:
    from ..state_builder import MarketContext, Scene


@dataclass(frozen=True)
class SameClockRank:
    """How many of ``of`` prior sessions the value is higher than, at this minute."""
    higher_than: int
    of: int

    @property
    def share(self) -> float:
        """The share of the prior sessions the value beats, 0 to 1: compare it with top_fifth, third_hi and the like."""
        return self.higher_than / self.of

    @property
    def band(self) -> str:
        """The third the value sits in, as a verdict under the owner's rule words it: "top third"."""
        return f"{third(self.share)} third"

    def words(self) -> str:
        return f"higher than {self.higher_than} of the last {self.of} sessions at this minute"


def rank_against(value: float, base: list[float]) -> SameClockRank | None:
    """``value`` against one number per prior session; None under MIN_RANK_SESSIONS sessions, too thin to
    rank, so the label is omitted and its question skipped rather than guessed."""
    if len(base) < MIN_RANK_SESSIONS:
        return None
    return SameClockRank(sum(1 for b in base if b < value), len(base))


def rank_sessions(value: float, base: list[float], what: str) -> tuple[SameClockRank | None, str | None]:
    """``value`` against the same measure on up to the last NIGHT_RANK_COUNT prior sessions (``base``, newest
    first, as same_clock_values and same_clock_market give it): ``(rank, None)``, or ``(None, reason)`` under
    SAME_CLOCK_MIN_SESSIONS of them, the reason naming ``what`` the sessions lacked ("a 30-minute move at this minute"). The rank every
    threshold that sizes or judges a market measure is replaced by, as night_ranks.rank_night is overnight."""
    recent = base[:NIGHT_RANK_COUNT]
    if len(recent) < SAME_CLOCK_MIN_SESSIONS:
        return None, f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with {what}, have {len(recent)}"
    return SameClockRank(sum(1 for b in recent if b < value), len(recent)), None


def move_rank(scene: Scene, move: float, minutes: int) -> tuple[SameClockRank | None, str | None]:
    """The size of SPX's ``minutes`` move to now, ``move`` in today's morning ruler, against the same minutes to
    this clock on the prior sessions, each in its own ruler (rank_sessions): the one rank every family judges
    whether SPX moved by."""
    base = same_clock_values(scene, lambda bars, then, sigma: move_size(bars, then, sigma, minutes))
    return rank_sessions(abs(move), base, f"a {minutes}-minute move at this minute")


def rank_at_slot(value: float, base: list[float]) -> dict | None:
    """rank_against in thirds, as the tape lane's labels and the tape unit word it ("in the top third for
    this minute, higher than 15 of 20 prior sessions"); the dict rides on the records as it is."""
    rank = rank_against(value, base)
    if rank is None:
        return None
    return {"band": f"{third(rank.share)} third", "higher_than": rank.higher_than, "of": rank.of}


def rank_days(scene: Scene) -> list[str]:
    """The prior sessions a rank may use, newest first: a day whose morning ruler was estimated sits out."""
    return [d for d in scene.prior_bars if not ((r := scene.prior_rulers.get(d)) is not None and r.estimated)]


def same_clock_values(scene: Scene, measure: Callable[[list[dict], datetime, float | None], float | None]) -> list[float]:
    """``measure(bars, then, sigma)`` on each prior session at this read's clock minute, newest first:
    ``bars`` are the session's bars that had finished by ``then`` (the same minute on that day, market
    time) and ``sigma`` its morning ruler in points, None when its diary is not on file. A session whose
    ruler was estimated is skipped, and so is a measure that returns None."""
    clock = scene.now.astimezone(ET).time()
    out = []
    for day in rank_days(scene):
        bars, ruler = scene.prior_bars[day], scene.prior_rulers.get(day)
        then = datetime.combine(date.fromisoformat(day), clock, tzinfo=ET)
        v = measure([b for b in bars if bar_time(b) + ONE_MINUTE <= then], then, ruler.points if ruler else None)
        if v is not None:
            out.append(v)
    return out


def same_clock_market(scene: Scene, measure: Callable[[MarketContext, datetime], float | None]) -> list[float]:
    """``measure(market, then)`` on each prior session's market context at this read's clock minute, newest
    first; a session without the context, or whose measure returns None, is skipped."""
    clock = scene.now.astimezone(ET).time()
    out = []
    for day, mk in scene.prior_markets.items():
        v = measure(mk, datetime.combine(date.fromisoformat(day), clock, tzinfo=ET))
        if v is not None:
            out.append(v)
    return out


# A rank in words by its fifth, as the question criteria name it; one wording for every label.
FIFTH_WORDS = {1: "in the top fifth", -1: "in the bottom fifth", 0: "between the top and bottom fifths"}


def fifth_side(rank: SameClockRank) -> int:
    """+1 in the top fifth of the same-clock sessions, -1 in the bottom fifth, 0 in neither."""
    return 1 if rank.share >= TOP_FIFTH else -1 if rank.share <= BOTTOM_FIFTH else 0


def fifth(rank: SameClockRank) -> str:
    return FIFTH_WORDS[fifth_side(rank)]


def percentile(values: list[float], share: float) -> float:
    """The ``share`` quantile of ``values``, interpolated between the two nearest."""
    s = sorted(values)
    k = share * (len(s) - 1)
    lo = int(k)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (k - lo)


def tick_bands_by_minute(scene: Scene) -> dict[time, tuple[float, float]]:
    """The top and bottom burst bands of $TICK for each minute of the day: the TICK_BURST_PCT percentile of
    the last NIGHT_RANK_COUNT prior sessions' bar highs at that minute and the mirror percentile of their
    lows, where at least SAME_CLOCK_MIN_SESSIONS sessions have the bar. A $TICK reading judged at its minute,
    never against a fixed +-1000."""
    by_minute: dict[time, list[dict]] = {}
    for mk in list(scene.prior_markets.values())[:NIGHT_RANK_COUNT]:
        for _, bar in mk.bars.get("$TICK") or []:
            by_minute.setdefault(bar_time(bar).astimezone(ET).time(), []).append(bar)
    return {minute: (percentile([float(b["high"]) for b in bars], TICK_BURST_PCT),
                     percentile([float(b["low"]) for b in bars], 1 - TICK_BURST_PCT))
            for minute, bars in by_minute.items() if len(bars) >= SAME_CLOCK_MIN_SESSIONS}


def tick_bursts(bands: dict[time, tuple[float, float]], bars: list[dict]) -> tuple[int, int] | None:
    """How many of today's $TICK ``bars`` reached their minute's top band with their high, and the bottom band
    with their low; None when a minute has no bands."""
    up = down = 0
    for bar in bars:
        band = bands.get(bar_time(bar).astimezone(ET).time())
        if band is None:
            return None
        up += float(bar["high"]) >= band[0]
        down += float(bar["low"]) <= band[1]
    return up, down
