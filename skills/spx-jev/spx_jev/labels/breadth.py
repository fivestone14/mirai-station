"""The breadth family: the NYSE and the sector funds around the index (breadth.*), from the market context
(market_context.py).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

from datetime import timedelta

from ..state_builder import Scene
from .label_set import LabelSet
from .words import plural

LABELS = ("breadth.advance_decline", "breadth.tick_lean", "breadth.sectors_up",
          "breadth.at_extremes", "breadth.day_upvol_share", "breadth.flip_after_release", "breadth.members_net_day",
          "breadth.open_net_volume", "breadth.opening_tick", "breadth.tick_extreme_5m", "breadth.tick_side_vs_usual",
          "breadth.upvol_share_30m", "breadth.volume_vs_count_30m", "breadth.nasdaq_net_volume")
GATES = ("tick_extreme_follow", "breadth_flip_after_release")
DARK = {"breadth.nasdaq_net_volume": "no Nasdaq breadth series: Schwab serves no $TICKQ or $TRINQ history, $ADDQ is not a symbol, "
                                     "$VOLQ is stale, and $UVOLQ and $DVOLQ are not probed"}

# A majority of the last 30 minutes' tick closes, and of the sectors.
TICK_WINDOW_MIN = 30
MIN_TICK_MINUTES = 20
MIN_SECTORS = 8
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE")


def build_breadth_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    mk, now = scene.market, scene.now
    if mk is None:
        for path in ("breadth.advance_decline", "breadth.tick_lean", "breadth.sectors_up"):
            ls.omit(path, "no market-context snapshot today")
        return ls
    add = mk.last("$ADD", now)
    if add is None:
        ls.omit("breadth.advance_decline", "no NYSE advance-decline value at or before now")
    elif add == 0:
        ls.put("breadth.advance_decline", "as many NYSE stocks are advancing as declining today")
    else:
        ls.put("breadth.advance_decline", f"on the NYSE {abs(round(add))} more stocks are {'advancing than declining' if add > 0 else 'declining than advancing'} today, more {'up' if add > 0 else 'down'} than {'down' if add > 0 else 'up'}")

    ticks = mk.between("$TICK", now - timedelta(minutes=TICK_WINDOW_MIN), now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_lean", f"needs {MIN_TICK_MINUTES} NYSE tick readings in the last {TICK_WINDOW_MIN} minutes, have {len(ticks)}")
    else:
        up, down = sum(1 for v in ticks if v > 0), sum(1 for v in ticks if v < 0)
        lean = "leaning to buying" if up > down else "leaning to selling" if down > up else "even"
        ls.put("breadth.tick_lean", f"over the last {TICK_WINDOW_MIN} minutes the NYSE tick read above zero {plural(up, 'time')} and below zero {plural(down, 'time')} of {len(ticks)}, {lean}")

    moved = [(s, mk.last(s, now), mk.first(s)) for s in SECTORS]
    moved = [(s, now_v, open_v) for s, now_v, open_v in moved if now_v is not None and open_v]
    if len(moved) < MIN_SECTORS:
        ls.omit("breadth.sectors_up", f"needs {MIN_SECTORS} of the {len(SECTORS)} sector funds with a value today, have {len(moved)}")
    else:
        up = sum(1 for _, now_v, open_v in moved if now_v > open_v)
        word = "most of them" if up * 2 > len(moved) else "fewer than half" if up * 2 < len(moved) else "exactly half"
        ls.put("breadth.sectors_up", f"{up} of {len(moved)} sector funds are above where they opened today, {word}")
    return ls
