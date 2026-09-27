"""The gap and open family: the gap from yesterday's close to the settled open and its fate (gap.*), the
first minutes against the settled open (open.*), and the overnight futures session (overnight.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. A
label listed here and not written is omitted by the registry as not built; the overnight labels wait
for a feed (DARK).
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("gap.size", "gap.fill_progress", "gap.morning_vs_gap", "gap.reach_distance",
          "open.fresh_extreme", "open.noise_band", "open.path", "open.settled_open_crosses",
          "overnight.bond_gap", "overnight.es_move", "overnight.gap_origin", "overnight.price_vs_range", "overnight.range",
          "overnight.range_vs_normal", "overnight.release_reaction")
GATES = ("gap_fill_next_hour", "overnight_bonds_vs_gap")
NO_OVERNIGHT = ("no overnight futures feed: nothing saves /ESZ26's extended-hours bars (no premarket lane or 09:26 job, "
                "and schwab.minute_bars asks for regular hours only)")
DARK = {
    "overnight.bond_gap": "no overnight feed for /ZNZ26 or /BTCV26: neither is probed or in market_context.SYMBOLS, and nothing saves extended hours",
    "overnight.es_move": NO_OVERNIGHT,
    "overnight.gap_origin": NO_OVERNIGHT,
    "overnight.price_vs_range": NO_OVERNIGHT,
    "overnight.range": NO_OVERNIGHT,
    "overnight.range_vs_normal": NO_OVERNIGHT,
    "overnight.release_reaction": NO_OVERNIGHT,
}


def build_gap_open_labels(scene: Scene) -> LabelSet:
    return LabelSet()
