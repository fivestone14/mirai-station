"""The story family: the night so far, told from the stretches of the overnight futures session (story.py)
as each read rebuilds them from bars (premarket.*): where futures stand against their 16:00 price, each
stretch in time order, the arc from the first stretch that moved, the leg since the previous checkpoint,
the report window against the night, and the night against yesterday's last cash hour.

JEV's earlier answers never reach these sentences: a read measures every stretch afresh from the bars
finished by ``now``. Sizes are in the pre-open ruler (rulers.normal_day_sigma), each stretch ranked in thirds
against the same stretch on the last nights (night_ranks.prior_window_nights). Written only on a
premarket read (PREMARKET). Each label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("premarket.where_now", "premarket.legs", "premarket.arc", "premarket.since_checkpoint", "premarket.release_vs_night",
          "premarket.vs_last_hour")
GATES = ("overnight_arc", "latest_leg_vs_night", "night_legs_agree", "release_vs_night", "night_vs_last_hour")
DARK: dict[str, str] = {}
PREMARKET = LABELS


def build_story_labels(scene: Scene) -> LabelSet:
    return LabelSet()
