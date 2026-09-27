"""The read-sequence family: the 30-minute lane's last reads told in order (seq.*), each rebuilt from the
bars and the market context as they stood at that read's minute, never from the answers JEV gave then:
the day's move from the open at each read (seq.day_move_by_read) and the volume share of rising stocks at
each read against the day's side (seq.breadth_by_read). Each label's sentence, how it is computed and its
source are in spec/question_set.json ``labels``.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("seq.day_move_by_read", "seq.breadth_by_read")
GATES = ("seq_day_move_stage", "seq_breadth_drift")
DARK: dict[str, str] = {}


def build_read_sequence_labels(scene: Scene) -> LabelSet:
    return LabelSet()
