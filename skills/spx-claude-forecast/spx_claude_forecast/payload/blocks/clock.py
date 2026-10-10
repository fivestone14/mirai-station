"""The clock block: where the read sits in the session, in minutes and by the station's phase of the day.

Minutes after the open and to the close are counted from the read's own cut against the station's session
calendar (the scene's open and close, so a half day closes at 13:00), each rounded to the nearest minute so
the two add up to the session's length. The phase is the station's own (``spx_jev.clock.phase_of``:
opening, morning, late_morning, lunch, afternoon), the same cut the clock odds are counted by, so Claude
and the clock odds never name the same minute differently.
"""
from __future__ import annotations

from ... import station_stores
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs


def build_clock_block(inputs: FrozenInputs) -> BlockResult:
    """``{min_after_open, min_to_close, phase}``; nothing here can be missing once the read exists."""
    clock = station_stores.import_spx_jev("clock")
    result = BlockResult()
    result.data = units.clean({
        "min_after_open": round(inputs.scene.minutes_since_open),
        "min_to_close": round(inputs.scene.minutes_to_close),
        "phase": clock.phase_of(inputs.cut),
    })
    return result
