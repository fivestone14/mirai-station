"""The levels family: where price sits against yesterday's range and value area, round numbers, the
opening range, armed breaks and wall touches (levels.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. A
label listed here and not written is omitted by the registry as not built.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("levels.break_armed", "levels.open_vs_prior_range", "levels.prior_day", "levels.prior_value", "levels.round_number",
          "levels.wall_touch_effort")
GATES = ("break_armed",)
DARK: dict[str, str] = {}


def build_levels_labels(scene: Scene) -> LabelSet:
    return LabelSet()
