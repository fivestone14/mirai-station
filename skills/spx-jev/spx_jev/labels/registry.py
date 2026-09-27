"""Every label family in one place, and the one call that builds a read's labels from them all."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..state_builder import Scene
from . import breadth, context, expiry_calendar, gamma, price, range_size, tape_flow, vol
from .label_set import LabelSet


@dataclass(frozen=True)
class Family:
    name: str
    build: Callable[[Scene], LabelSet]
    labels: tuple[str, ...]          # every label path the family writes or omits, and no other


FAMILIES = (
    Family("context", context.build_context_labels, context.LABELS),
    Family("price", price.build_price_labels, price.LABELS),
    Family("range_size", range_size.build_range_size_labels, range_size.LABELS),
    Family("vol", vol.build_vol_labels, vol.LABELS),
    Family("gamma", gamma.build_gamma_labels, gamma.LABELS),
    Family("tape_flow", tape_flow.build_tape_flow_labels, tape_flow.LABELS),
    Family("expiry_calendar", expiry_calendar.build_expiry_calendar_labels, expiry_calendar.LABELS),
    Family("breadth", breadth.build_breadth_labels, breadth.LABELS),
)


def build_labels(scene: Scene) -> LabelSet:
    """Every family's labels for one moment. A family that fails costs only its own labels, each
    omitted with the failure as its reason; a family that writes a label it does not own is a bug in
    that family and stops the read."""
    out = LabelSet()
    for family in FAMILIES:
        try:
            got = family.build(scene)
        except Exception as e:  # one family's fault must not cost the read every other family's labels
            got = LabelSet()
            for path in family.labels:
                got.omit(path, f"the {family.name} labels failed this read: {type(e).__name__}: {e}")
        stray = got.paths() - set(family.labels)
        if stray:
            raise ValueError(f"the {family.name} family wrote labels it does not own: {sorted(stray)}")
        out.update(got)
    return out
