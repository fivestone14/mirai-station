"""The leadership family: who carries the index, from the market context (market_context.py): equal
weight against cap weight, semis, the megacaps, sector agreement and rotation, size (leaders.*,
sectors.*, tells.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. A
label listed here and not written is omitted by the registry as not built.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("leaders.equal_weight_vs_cap_30m", "leaders.heavyweight_gap", "leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m",
          "leaders.rotation_30m", "leaders.semis_vs_index_30m", "leaders.single_name_10m", "leaders.size_spread_day",
          "sectors.agreement_30m", "tells.sector_lead_10m")
GATES = ("heavyweight_gap_split",)
DARK: dict[str, str] = {}


def build_leadership_labels(scene: Scene) -> LabelSet:
    return LabelSet()
