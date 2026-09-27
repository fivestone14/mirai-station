"""Every label family in one place, and the one call that builds a read's labels from them all."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..state_builder import Scene
from . import (breadth, context, events_shocks, expiry_calendar, gamma, gap_open, leadership, levels, macro, price, range_size,
               tape_flow, vol)
from .label_set import LabelSet

NOT_BUILT = "not built yet: no code writes this label"
GATE_NOT_BUILT = "its sleep gate is not built yet"


@dataclass(frozen=True)
class Family:
    name: str
    build: Callable[[Scene], LabelSet]
    labels: tuple[str, ...]                          # every label path the family writes or omits, and no other
    gates: tuple[str, ...] = ()                      # the gated questions whose sleep_when it judges
    dark: dict[str, str] = field(default_factory=dict)   # labels whose data no feed carries yet, with why


FAMILIES = (
    Family("context", context.build_context_labels, context.LABELS, context.GATES, context.DARK),
    Family("price", price.build_price_labels, price.LABELS, price.GATES, price.DARK),
    Family("gap_open", gap_open.build_gap_open_labels, gap_open.LABELS, gap_open.GATES, gap_open.DARK),
    Family("range_size", range_size.build_range_size_labels, range_size.LABELS, range_size.GATES, range_size.DARK),
    Family("levels", levels.build_levels_labels, levels.LABELS, levels.GATES, levels.DARK),
    Family("vol", vol.build_vol_labels, vol.LABELS, vol.GATES, vol.DARK),
    Family("gamma", gamma.build_gamma_labels, gamma.LABELS, gamma.GATES, gamma.DARK),
    Family("tape_flow", tape_flow.build_tape_flow_labels, tape_flow.LABELS, tape_flow.GATES, tape_flow.DARK),
    Family("expiry_calendar", expiry_calendar.build_expiry_calendar_labels, expiry_calendar.LABELS, expiry_calendar.GATES,
           expiry_calendar.DARK),
    Family("breadth", breadth.build_breadth_labels, breadth.LABELS, breadth.GATES, breadth.DARK),
    Family("leadership", leadership.build_leadership_labels, leadership.LABELS, leadership.GATES, leadership.DARK),
    Family("macro", macro.build_macro_labels, macro.LABELS, macro.GATES, macro.DARK),
    Family("events_shocks", events_shocks.build_events_shocks_labels, events_shocks.LABELS, events_shocks.GATES, events_shocks.DARK),
)


def build_labels(scene: Scene) -> LabelSet:
    """Every family's labels and sleep gates for one moment.

    A label a family owns but neither wrote nor omitted is omitted here: with its dark reason when no feed
    carries its data yet, else as not built. A gate a family owns but did not decide sleeps, so a gated
    question is never asked before its gate exists. A family that fails costs only its own labels, each
    omitted with the failure as its reason, and its gates; a family that writes a label or decides a gate
    it does not own is a bug in that family and stops the read."""
    out = LabelSet()
    for family in FAMILIES:
        try:
            got = family.build(scene)
        except Exception as e:  # one family's fault must not cost the read every other family's labels
            got = LabelSet()
            for path in family.labels:
                got.omit(path, f"the {family.name} labels failed this read: {type(e).__name__}: {e}")
            for qid in family.gates:
                got.sleep(qid, f"the {family.name} labels failed this read")
        stray = (got.paths() - set(family.labels)) | (set(got.gates) - set(family.gates))
        if stray:
            raise ValueError(f"the {family.name} family wrote labels or gates it does not own: {sorted(stray)}")
        for path in family.labels:
            if path not in got.paths():
                got.omit(path, f"dark: {family.dark[path]}" if path in family.dark else NOT_BUILT)
        for qid in family.gates:
            if qid not in got.gates:
                got.sleep(qid, GATE_NOT_BUILT)
        out.update(got)
    return out
