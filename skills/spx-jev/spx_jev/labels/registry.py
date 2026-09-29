"""Every label family in one place, and the one call that builds a read's labels from them all."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..state_builder import Scene
from . import (bitcoin, breadth, context, events_shocks, expiry_calendar, gamma, gap_open, leadership, levels, macro, plausible, premarket,
               price, range_size, read_sequence, story, tape_flow, vol)
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
    bar_clock_only: tuple[str, ...] = ()             # labels only a read on the bar clock measures; off it they are not accounted
    premarket: tuple[str, ...] = ()                  # labels a premarket read accounts for; a family with none does not run before the open
    premarket_only: tuple[str, ...] = ()             # labels only a premarket read measures; a session read does not account for them


FAMILIES = (
    Family("context", context.build_context_labels, context.LABELS, context.GATES, context.DARK, premarket=context.LABELS),
    Family("price", price.build_price_labels, price.LABELS, price.GATES, price.DARK),
    Family("gap_open", gap_open.build_gap_open_labels, gap_open.LABELS, gap_open.GATES, gap_open.DARK),
    Family("range_size", range_size.build_range_size_labels, range_size.LABELS, range_size.GATES, range_size.DARK,
           range_size.BAR_CLOCK_ONLY),
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
    Family("premarket", premarket.build_premarket_labels, premarket.LABELS, premarket.GATES, premarket.DARK,
           premarket=premarket.PREMARKET, premarket_only=premarket.PREMARKET),
    Family("story", story.build_story_labels, story.LABELS, story.GATES, story.DARK, premarket=story.PREMARKET, premarket_only=story.PREMARKET),
    Family("bitcoin", bitcoin.build_bitcoin_labels, bitcoin.LABELS, bitcoin.GATES, bitcoin.DARK,
           premarket=bitcoin.PREMARKET, premarket_only=bitcoin.PREMARKET),
    Family("read_sequence", read_sequence.build_read_sequence_labels, read_sequence.LABELS, read_sequence.GATES, read_sequence.DARK),
)

# Labels renamed once reads were archived under the old path, old path to new: the fact measured is the same, so
# a reader of the archive (the learning store) files both paths under the new one.
RENAMED = {"breadth.upvol_share_30m": "breadth.net_volume_change_30m", "vol.vix_change_30": "vol.vix_change"}


def build_labels(scene: Scene) -> LabelSet:
    """Every family's labels and sleep gates for one moment.

    A label a family owns but neither wrote nor omitted is omitted here: with its dark reason when no feed
    carries its data yet, else as not built; a bar-clock-only label off the bar clock is left alone (the
    live lane neither writes nor omits the opening lane's stretch labels). A premarket read (the scene
    before the open) runs only the families that serve it and accounts only for their premarket labels;
    a session read leaves the premarket-only ones alone. A gate a family owns but did not decide sleeps, so a gated
    question is never asked before its gate exists. A family that fails costs only its own labels, each
    omitted with the failure as its reason, and its gates; a family that writes a label or decides a gate
    it does not own is a bug in that family and stops the read. Every family reads the market context after
    plausible.checked has taken out the breadth series that do not read like their own history."""
    scene = plausible.checked(scene)
    out = LabelSet()
    for family in FAMILIES:
        if scene.premarket and not family.premarket:
            continue
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
        for path in family.premarket if scene.premarket else family.labels:
            if path not in got.paths() and not (path in family.bar_clock_only and not scene.bar_clock) \
                    and not (path in family.premarket_only and not scene.premarket):
                got.omit(path, f"dark: {family.dark[path]}" if path in family.dark else NOT_BUILT)
        for qid in family.gates:
            if qid not in got.gates:
                got.sleep(qid, GATE_NOT_BUILT)
        out.update(got)
    return out
