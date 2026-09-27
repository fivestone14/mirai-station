"""The premarket family: the overnight futures before the open (overnight.*), from the night's bars in the
overnight store (scene.night), ranked against the same measure on the last nights (night_ranks), never
against a fixed line. Its sizes are in the pre-open ruler, the median morning anchor of the last sessions
(rulers.normal_day_sigma), the one stamped on the premarket lane's records.

Written only on a premarket read (PREMARKET): a session read neither writes nor omits them, and leaves
its gates to the registry. Each label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("overnight.bond_gap", "overnight.es_move", "overnight.gap_origin", "overnight.range_vs_normal", "overnight.release_reaction")
GATES = ("gap_origin", "overnight_bonds_vs_gap", "release_reaction_path")
DARK: dict[str, str] = {}
PREMARKET = LABELS


def build_premarket_labels(scene: Scene) -> LabelSet:
    return LabelSet()
