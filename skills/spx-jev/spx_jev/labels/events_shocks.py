"""The events and shocks family: the scheduled event clock and what releases did (context.event_clock,
event.*), the morning brief and headlines (news.*), and unscheduled bursts seen through the tape (shock.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. A
label listed here and not written is omitted by the registry as not built; the headline feed is DARK.
"""
from __future__ import annotations

from ..state_builder import Scene
from .label_set import LabelSet

LABELS = ("context.event_clock", "event.reaction", "event.release_clock_10m", "event.statement_and_presser",
          "news.morning_brief", "news.intraday_headline", "shock.burst", "shock.cross_asset", "shock.vs_day_range")
GATES = ("event_clock", "release_in_lane", "shock_state", "move_reaction_path", "news_headline")
DARK = {"news.intraday_headline": "no headline feed writes into state (a right-eye or Nexus writer is not built)"}


def build_events_shocks_labels(scene: Scene) -> LabelSet:
    return LabelSet()
