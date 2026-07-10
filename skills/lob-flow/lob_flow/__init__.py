"""
lob-flow — Layer 2 (FLOW) of the 0DTE reasoning stack: a self-contained,
shadow-only sensor box over the SPX 0DTE options chain.

What it watches (plain English):
  * the TAPE — who is actually buying vs selling options right now
  * the LIGHTS — resting quote sizes per strike: monkeys fleeing (scatter)
    vs huddling back around a level the gravity map already marked (defense)

What it may NEVER do: pick a direction, move a strike, or place anything.
Its only possible influence, after it earns promotion, is a clamped nudge to
the host's confidence. See README.md and docs in the plan artifact.

Public surface — everything a host needs:
    from lob_flow import LobConfig, LayerView, read_latest
"""
from .baselines import BaselineStore, FileCalendar, fold_day_into_baselines
from .contracts import (BlockInfo, DepthSample, LayerView, LobConfig,
                        QuoteRow, TradeEvent)
from .engine import (confidence_modifier, evaluate_once, grade_day,
                     promotion_check, wilson_bounds)
from .io_state import StateDir


__all__ = ["LobConfig", "LayerView", "QuoteRow", "TradeEvent", "DepthSample",
           "BlockInfo", "StateDir", "BaselineStore", "FileCalendar",
           "fold_day_into_baselines", "evaluate_once", "grade_day",
           "promotion_check", "confidence_modifier", "wilson_bounds"]
