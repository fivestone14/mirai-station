"""A value placed against the same minute of the prior sessions.

SPX's size swings with the clock (the 09:30-10:30 range runs about twice the afternoon's), so a size is
judged against what the same minute looked like on up to the last 20 sessions, never against a fixed
cut: "higher than 17 of the last 20 sessions at this minute". A rank needs at least MIN_RANK_SESSIONS
sessions; a session whose morning ruler was estimated (rulers.morning_ruler) is left out of every rank.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Callable

from ..cuts import BOTTOM_FIFTH, MIN_RANK_SESSIONS, TOP_FIFTH
from .measures import ET, ONE_MINUTE, bar_time
from .words import third

if TYPE_CHECKING:
    from ..state_builder import Scene


@dataclass(frozen=True)
class SameClockRank:
    """How many of ``of`` prior sessions the value is higher than, at this minute."""
    higher_than: int
    of: int

    @property
    def share(self) -> float:
        """The share of the prior sessions the value beats, 0 to 1: compare it with top_fifth, third_hi and the like."""
        return self.higher_than / self.of

    def words(self) -> str:
        return f"higher than {self.higher_than} of the last {self.of} sessions at this minute"


def rank_against(value: float, base: list[float]) -> SameClockRank | None:
    """``value`` against one number per prior session; None under MIN_RANK_SESSIONS sessions, too thin to
    rank, so the label is omitted and its question skipped rather than guessed."""
    if len(base) < MIN_RANK_SESSIONS:
        return None
    return SameClockRank(sum(1 for b in base if b < value), len(base))


def rank_at_slot(value: float, base: list[float]) -> dict | None:
    """rank_against in thirds, as the tape lane's labels and the tape unit word it ("in the top third for
    this minute, higher than 15 of 20 prior sessions"); the dict rides on the records as it is."""
    rank = rank_against(value, base)
    if rank is None:
        return None
    return {"band": f"{third(rank.share)} third", "higher_than": rank.higher_than, "of": rank.of}


def same_clock_values(scene: Scene, measure: Callable[[list[dict], datetime, float | None], float | None]) -> list[float]:
    """``measure(bars, then, sigma)`` on each prior session at this read's clock minute, newest first:
    ``bars`` are the session's bars that had finished by ``then`` (the same minute on that day, market
    time) and ``sigma`` its morning ruler in points, None when its diary is not on file. A session whose
    ruler was estimated is skipped, and so is a measure that returns None."""
    clock = scene.now.astimezone(ET).time()
    out = []
    for day, bars in scene.prior_bars.items():
        ruler = scene.prior_rulers.get(day)
        if ruler is not None and ruler.estimated:
            continue
        then = datetime.combine(date.fromisoformat(day), clock, tzinfo=ET)
        v = measure([b for b in bars if bar_time(b) + ONE_MINUTE <= then], then, ruler.points if ruler else None)
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
