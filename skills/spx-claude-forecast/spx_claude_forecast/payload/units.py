"""How numbers are written in the payload, in one place, so every block says the same thing the same way.

Every distance or move is given in ``sig`` (the read's expected daily move in index points) and never in
index points or as a price level, because Claude must not see a number it could recall. A value that is
ranked against the same minute on earlier sessions rides as ``{"v": value, "r": [higher_than, of]}``:
"bigger than ``higher_than`` of the last ``of`` sessions at this minute". The ranks use the station's own
rule (``spx_jev.labels.ranks.rank_sessions``: up to 20 sessions, at least 10) on the value as measured,
never on the rounded one that is shown. A value set against its usual for the minute (the median of the
prior sessions, ``against_usual``) needs the station's smallest count for a median (cuts.MIN_RANK_SESSIONS).

The words every block says the same way live here too: which options book a read stands on and why a book
field is left out, the settled open's clock, and the scanner's regime words. A raw diary row (the station's scene
carries the labeller's cut of a row, which drops the book's source, its net gamma and its net exposure) comes
from the inputs, loaded once per read (``inputs.raw_diary_row``, ``inputs.first_raw_diary_row``).
"""
from __future__ import annotations

import statistics
from datetime import date, datetime
from typing import Any, Callable, Sequence

from .. import station_stores
from ..control import ET

SIG_DECIMALS = 2
PCT_DECIMALS = 1
MULTIPLE_DECIMALS = 2             # a value as a multiple of its usual for this minute: 0.56x
NATIVE_BOOK = "native"            # inputs.options_book on SPX's own chain; "stand_in" is the scaled SPY proxy
STAND_IN_BOOK = "stand_in_book"   # why a field priced off the options book is left out on the SPY stand-in
NOT_UNTIL_SETTLED_OPEN = "not_until_09:35"   # the settled open is the 09:34 bar's close (measures.SETTLED_OPEN_BAR)
BILLION = 1e9                     # the scanner's net dealer gamma is dollars per 1% move; Claude reads $bn
REGIME_WORDS = {"long_gamma": "long", "short_gamma": "short", "uncertain": "uncertain"}   # the scanner's words, as the payload says them


def sig(points: float | None, sigma_points: float, decimals: int | None = SIG_DECIMALS) -> float | None:
    """A move or distance in index points as a share of the day's expected move. ``decimals=None`` leaves it
    unrounded, for a value that is ranked before it is shown (``ranked_sig``)."""
    if points is None or not sigma_points:
        return None
    ratio = float(points) / float(sigma_points)
    return ratio if decimals is None else round(ratio, decimals)


def pct(value: float | None, decimals: int = PCT_DECIMALS) -> float | None:
    return None if value is None else round(float(value), decimals)


def ranked(value: float | None, base: Sequence[float], what: str = "the same minute on earlier sessions",
           decimals: int | None = None) -> dict | None:
    """``{"v": value, "r": [higher_than, of]}``, or ``{"v": value}`` when there are too few sessions to rank, or None.
    The rank is of ``value`` as given, so it is passed unrounded; ``decimals`` rounds the shown value on the way out
    (0 shows a whole number)."""
    if value is None:
        return None
    rank, _ = _rank_sessions()(float(value), [float(b) for b in base if b is not None], what)
    out = {"v": _shown(value, decimals)}
    if rank is not None:
        out["r"] = [rank.higher_than, rank.of]
    return out


def ranked_sig(points: float | None, sigma_points: float, base: Sequence[float],
               what: str = "the same minute on earlier sessions") -> dict | None:
    """A signed move or distance in sig, ranked by its size against an unsigned base (the same measure on the prior
    sessions, each in its own ruler); the shown value keeps its sign."""
    value = sig(points, sigma_points, None)
    if value is None:
        return None
    out = ranked(abs(value), base, what)
    out["v"] = round(value, SIG_DECIMALS)
    return out


def usual(base: Sequence[float]) -> float | None:
    """The median of the prior sessions' values at this minute (newest first) over the station's rank window; None
    under its smallest count for a same-clock median."""
    cuts = station_stores.import_spx_jev("cuts")
    recent = [float(b) for b in base[:cuts.NIGHT_RANK_COUNT] if b is not None]
    return statistics.median(recent) if len(recent) >= cuts.MIN_RANK_SESSIONS else None


def against_usual(value: float, base: Sequence[float], shown: Callable[[float], float], decimals: int = MULTIPLE_DECIMALS,
                  what: str = "the same minute on earlier sessions") -> dict | None:
    """``{"v": shown(usual), "r": ...}``: the value set against the prior sessions' median (``shown`` says how: a
    multiple of it, the difference from it), with the rank of the value itself among them; None when there are too
    few sessions for a median."""
    median = usual(base)
    if median is None:
        return None
    out = {"v": round(shown(median), decimals)}
    rank = ranked(value, base, what)
    if "r" in rank:
        out["r"] = rank["r"]
    return out


def _shown(value: float, decimals: int | None) -> float:
    if decimals is None:
        return value
    return round(value) if decimals == 0 else round(value, decimals)


def _rank_sessions() -> Callable:
    return station_stores.import_spx_jev("labels.ranks").rank_sessions


def hhmm(when: datetime | None) -> str | None:
    return None if when is None else when.strftime("%H:%M")


def minutes_ago(when: datetime | None, cut: datetime) -> int | None:
    return None if when is None else int((cut - when).total_seconds() // 60)


def same_clock_values(scene: Any, measure: Callable[[list[dict], datetime, float], float | None]) -> list[float]:
    """``measure(bars, then, sigma)`` on each prior session at the same clock as now, newest first, through the
    station's own helper so the rank base is the one its labels use."""
    ranks = station_stores.import_spx_jev("labels.ranks")
    values = ranks.same_clock_values(scene, measure)
    return [v for v in values if v is not None]


def same_clock(cut: datetime, day: str) -> datetime:
    """The cut's clock minute on a prior session, market time."""
    return datetime.combine(date.fromisoformat(day), cut.astimezone(ET).time(), tzinfo=ET)


def clean(block: dict) -> dict:
    """Drop None values so an absent fact is declared in ``absent`` rather than written as null."""
    return {k: v for k, v in block.items() if v is not None}


# --- the numbers a row carries ---------------------------------------------------------------------------

def is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def positive(value: Any) -> float | None:
    """A positive number as a float, else None: a price or a level a row or a bar may lack or carry as zero."""
    return float(value) if is_num(value) and value > 0 else None


def billions(dollars: Any, decimals: int = 1) -> float | None:
    """The scanner's dollars per 1% move as the $bn Claude reads."""
    return round(float(dollars) / BILLION, decimals) if is_num(dollars) else None


def regime_word(word: Any) -> str | None:
    """The scanner's regime as the payload says it (long, short, uncertain); None for anything else."""
    return REGIME_WORDS.get(word) if isinstance(word, str) else None


def book_absence(inputs: Any) -> str | None:
    """Why a field priced off the read's options book is left out, or None on SPX's own chain: the scaled SPY
    stand-in prices a different thing, and a book that cannot be told is treated as a stand-in."""
    if inputs.options_book == NATIVE_BOOK:
        return None
    return STAND_IN_BOOK if inputs.options_book else "options_book_unknown"


# --- the diary, raw and on the prior sessions --------------------------------------------------------------

def prior_books(scene: Any) -> list:
    """Each prior session's diary row at this minute with its ruler (gamma.PriorBook), newest first: the base for a
    distance or a share the diary carries, each measured from that row's own spot."""
    return station_stores.import_spx_jev("labels.gamma").prior_books(scene) or []


def prior_row_distances(prior: list, level_of: Callable[[dict], float | None]) -> list[float]:
    """``|spot - level_of(row)|`` in each prior row's own ruler, newest first; a row without the level, a spot or a
    ruler is skipped."""
    out = []
    for book in prior:
        level, spot = level_of(book.row), positive(book.row.get("spot"))
        if level is not None and spot is not None and book.ruler is not None and book.ruler.points:
            out.append(abs(spot - level) / float(book.ruler.points))
    return out
