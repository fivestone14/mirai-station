"""The breadth family: the NYSE and the sector funds around the index (breadth.*), from the market context
(market_context.py).

Schwab's breadth series as the context job saves them: $TICK and $TRIN are read a minute at a time; $UVOL and
$DVOL (the NYSE's up and down volume) and $VOLD and $VOLSPD (net volume, NYSE-wide and in S&P 500 members) are
running totals since 09:30, so a window's volume is the total at its end less the total at its start.

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Callable

from ..cuts import (MIN_RANK_SESSIONS, TICK_FAR_BAND, TICK_USUAL_BAND, TRIN_HIGH, TRIN_LOW, UPVOL_LEAN_HI, UPVOL_LEAN_LO,
                    WINDOW_30_MIN)
from ..sessions import session_open
from ..state_builder import MarketContext, Scene
from .label_set import LabelSet
from .measures import ET
from .words import pct, plural

LABELS = ("breadth.advance_decline", "breadth.tick_lean", "breadth.sectors_up",
          "breadth.tick_side_vs_usual", "breadth.upvol_share_30m", "breadth.volume_vs_count_30m",
          "breadth.at_extremes", "breadth.day_upvol_share", "breadth.flip_after_release", "breadth.members_net_day",
          "breadth.open_net_volume", "breadth.opening_tick", "breadth.tick_extreme_5m", "breadth.nasdaq_net_volume")
GATES = ("tick_extreme_follow", "breadth_flip_after_release")
DARK = {"breadth.nasdaq_net_volume": "no Nasdaq breadth series: Schwab serves no $TICKQ or $TRINQ history, $ADDQ is not a symbol, "
                                     "$VOLQ is stale, and $UVOLQ and $DVOLQ are not probed"}

# A majority of a 30-minute window's readings, and of the sectors.
MIN_TICK_MINUTES = 20
MIN_TRIN_MINUTES = 20
MIN_SECTORS = 8
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE")
# The context job saves a bar a minute; a newest value older than this means it stopped, not that it skipped a minute.
FRESH_MIN = 5


def build_breadth_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    if scene.market is None:
        for path in LABELS:
            if path not in DARK:
                ls.omit(path, "no market-context snapshot today")
        return ls
    _advance_decline(scene, ls)
    _tick_lean(scene, ls)
    _sectors_up(scene, ls)
    _tick_side_vs_usual(scene, ls)
    _upvol_share_30m(scene, ls)
    _volume_vs_count_30m(scene, ls)
    return ls


def _advance_decline(scene: Scene, ls: LabelSet) -> None:
    add = scene.market.last("$ADD", scene.now)
    if add is None:
        ls.omit("breadth.advance_decline", "no NYSE advance-decline value at or before now")
    elif add == 0:
        ls.put("breadth.advance_decline", "as many NYSE stocks are advancing as declining today")
    else:
        ls.put("breadth.advance_decline", f"on the NYSE {abs(round(add))} more stocks are {'advancing than declining' if add > 0 else 'declining than advancing'} today, more {'up' if add > 0 else 'down'} than {'down' if add > 0 else 'up'}")


def _tick_lean(scene: Scene, ls: LabelSet) -> None:
    ticks = scene.market.between("$TICK", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_lean", f"needs {MIN_TICK_MINUTES} NYSE tick readings in the last {WINDOW_30_MIN} minutes, have {len(ticks)}")
        return
    up, down = sum(1 for v in ticks if v > 0), sum(1 for v in ticks if v < 0)
    lean = "leaning to buying" if up > down else "leaning to selling" if down > up else "even"
    ls.put("breadth.tick_lean", f"over the last {WINDOW_30_MIN} minutes the NYSE tick read above zero {plural(up, 'time')} and below zero {plural(down, 'time')} of {len(ticks)}, {lean}")


def _sectors_up(scene: Scene, ls: LabelSet) -> None:
    mk = scene.market
    moved = [(s, mk.last(s, scene.now), mk.first(s)) for s in SECTORS]
    moved = [(s, now_v, open_v) for s, now_v, open_v in moved if now_v is not None and open_v]
    if len(moved) < MIN_SECTORS:
        ls.omit("breadth.sectors_up", f"needs {MIN_SECTORS} of the {len(SECTORS)} sector funds with a value today, have {len(moved)}")
        return
    up = sum(1 for _, now_v, open_v in moved if now_v > open_v)
    word = "most of them" if up * 2 > len(moved) else "fewer than half" if up * 2 < len(moved) else "exactly half"
    ls.put("breadth.sectors_up", f"{up} of {len(moved)} sector funds are above where they opened today, {word}")


# ----------------------------------------------------------------------------- shared reads

def _same_clock(scene: Scene, measure: Callable[[MarketContext, datetime], float | None]) -> list[float]:
    """``measure(market, then)`` on each prior session's market context at this read's clock minute."""
    clock = scene.now.astimezone(ET).time()
    out = []
    for day, mk in scene.prior_markets.items():
        v = measure(mk, datetime.combine(date.fromisoformat(day), clock, tzinfo=ET))
        if v is not None:
            out.append(v)
    return out


def _running_total(mk: MarketContext, symbol: str, t: datetime) -> float | None:
    """A running-total series ($UVOL, $DVOL, $VOLD, $VOLSPD) as it stood at ``t``: nothing yet at the open."""
    return 0.0 if t <= session_open(t) else mk.last(symbol, t, max_age_min=FRESH_MIN)


def _upvol_share(mk: MarketContext, start: datetime, end: datetime) -> float | None:
    """The share of NYSE volume that went into rising stocks from ``start`` to ``end``; None when either
    total is not known then or no volume traded."""
    totals = [_running_total(mk, s, t) for s in ("$UVOL", "$DVOL") for t in (start, end)]
    if any(v is None for v in totals):
        return None
    up, down = totals[1] - totals[0], totals[3] - totals[2]
    return up / (up + down) if up + down > 0 else None


def _upvol_lean(share: float) -> str:
    """The share against the lean lines, judged as the sentence prints it."""
    shown = round(share, 2)
    if shown > UPVOL_LEAN_HI:
        return f"past the {pct(UPVOL_LEAN_HI)} lean line on the buy side"
    if shown < UPVOL_LEAN_LO:
        return f"past the {pct(UPVOL_LEAN_LO)} lean line on the sell side"
    return f"inside the {pct(UPVOL_LEAN_LO)} to {pct(UPVOL_LEAN_HI)} even band"


def _tick_share_above_zero(mk: MarketContext, now: datetime) -> float | None:
    """The share of the last 30 minutes' $TICK closes above zero; None under MIN_TICK_MINUTES of them."""
    ticks = mk.between("$TICK", now - timedelta(minutes=WINDOW_30_MIN), now)
    return sum(1 for v in ticks if v > 0) / len(ticks) if len(ticks) >= MIN_TICK_MINUTES else None


# ----------------------------------------------------------------------------- the last 30 minutes

def _tick_side_vs_usual(scene: Scene, ls: LabelSet) -> None:
    """The share of the last 30 minutes' TICK closes above zero against the median share at this clock on
    the prior sessions: raw TICK sits below zero most of the time, so only the usual for the half hour says
    which side it leaned."""
    ticks = scene.market.between("$TICK", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_side_vs_usual", f"needs {MIN_TICK_MINUTES} NYSE TICK readings in the last {WINDOW_30_MIN} minutes, have {len(ticks)}")
        return
    base = _same_clock(scene, _tick_share_above_zero)
    if len(base) < MIN_RANK_SESSIONS:
        ls.omit("breadth.tick_side_vs_usual", f"needs {MIN_RANK_SESSIONS} prior sessions with NYSE TICK readings in this half hour, have {len(base)}")
        return
    above = sum(1 for v in ticks if v > 0)
    gap = round(above / len(ticks) - statistics.median(base), 2) or 0.0
    against = "the same as usual" if gap == 0 else f"{abs(gap):.2f} {'more' if gap > 0 else 'less'} than usual"
    if abs(gap) <= TICK_USUAL_BAND:
        band = f"within the usual band ({TICK_USUAL_BAND:.2f})"
    elif abs(gap) <= TICK_FAR_BAND:
        band = f"past the usual band ({TICK_USUAL_BAND:.2f}), inside the far band ({TICK_FAR_BAND:.2f})"
    else:
        band = f"past the far band ({TICK_FAR_BAND:.2f})"
    ls.put("breadth.tick_side_vs_usual",
           f"over the last {WINDOW_30_MIN} minutes NYSE TICK sat above zero {above} of {len(ticks)} minutes, {against} for this half hour: {band}")


def _upvol_share_30m(scene: Scene, ls: LabelSet) -> None:
    share = _upvol_share(scene.market, scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if share is None:
        ls.omit("breadth.upvol_share_30m", f"no NYSE up and down volume known both {WINDOW_30_MIN} minutes ago and now, within "
                                           f"{FRESH_MIN} minutes of each: the market-context job stopped or has not saved them")
        return
    ls.put("breadth.upvol_share_30m",
           f"over the last {WINDOW_30_MIN} minutes {pct(share)} of NYSE volume traded in rising stocks, {_upvol_lean(share)}")


def _volume_vs_count_30m(scene: Scene, ls: LabelSet) -> None:
    trin = scene.market.between("$TRIN", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(trin) < MIN_TRIN_MINUTES:
        ls.omit("breadth.volume_vs_count_30m", f"needs {MIN_TRIN_MINUTES} NYSE TRIN readings in the last {WINDOW_30_MIN} minutes, have {len(trin)}")
        return
    mean = round(statistics.fmean(trin), 2)
    if mean < TRIN_LOW:
        verdict = f"under the {TRIN_LOW} line: volume per rising stock ran heavier than volume per falling stock"
    elif mean > TRIN_HIGH:
        verdict = f"over the {TRIN_HIGH} line: volume per falling stock ran heavier than volume per rising stock"
    else:
        verdict = f"between the {TRIN_LOW} and {TRIN_HIGH} lines: volume per rising stock and per falling stock matched"
    ls.put("breadth.volume_vs_count_30m", f"over the last {WINDOW_30_MIN} minutes NYSE TRIN averaged {mean:.2f}, {verdict}")
