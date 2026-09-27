"""The macro family: markets outside the index running ahead of it (bonds, oil, the pooled macro gap),
and the flows around it (xasset.*, flows.*, close.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. A
label listed here and not written is omitted by the registry as not built; the paid feeds are DARK.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("xasset.bond_gap_30min", "xasset.macro_gap_30min", "xasset.oil_gap_30min", "flows.rebalance_side", "flows.etf_creations",
          "close.moc_imbalance")
GATES: tuple[str, ...] = ()
DARK = {
    "flows.etf_creations": "no intraday ETF creation feed",
    "close.moc_imbalance": "no closing-auction imbalance feed (a paid feed), and no read after 15:32 to use it",
}


def build_macro_labels(scene: Scene) -> LabelSet:
    return LabelSet()
