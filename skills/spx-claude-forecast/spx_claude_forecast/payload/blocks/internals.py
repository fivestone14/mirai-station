"""The NYSE internals block: advancers minus decliners as the session shows them live, how that stood half an
hour earlier, and the advancing share.

Schwab serves no $ADD during its own session, but it does serve the advancing and declining issue counts
($ADVN and $DECN), and their difference matched the $ADD it served for the same day afterwards
(spx_jev.market_context.DERIVED_ADD), so ``add_live_approx`` is $ADVN less $DECN at the cut: "approx" because
it is the live difference, not the series Schwab puts right after the close. $TICK, $TRIN, $UVOL, $DVOL,
$VOLD and $VOLSPD are served wrong during their own session and put right only after it
(spx_jev.labels.plausible.SAME_DAY_WRONG), so ``tick_vold`` is always declared absent and nothing here reads
them. Each count is read from the newest finished minute bar at the cut and must be fresh: a count older than
BREADTH_MAX_AGE_MIN is a feed that stopped, not a reading. Counts of issues carry no price and are not ranked.
"""
from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from typing import Any

from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs

BLOCK = "internals"
ADVANCING, DECLINING = "$ADVN", "$DECN"
# The context job saves a breadth bar a minute; a newest bar older than this is a feed that stopped, not a skipped
# minute (spx_jev.labels.breadth.FRESH_MIN).
BREADTH_MAX_AGE_MIN = 5
# The change is read over the same half hour every tape and flow field uses.
CHANGE_WINDOW_MIN = 30
SAME_DAY_WRONG_FIELD = "tick_vold"


def build_internals_block(inputs: FrozenInputs) -> BlockResult:
    """``{add_live_approx, add_30m_change, adv_share_pct}`` from $ADVN and $DECN at the cut, or the block absent."""
    market = inputs.scene.market
    counts = _counts_at(market, inputs.cut)
    if counts is None:
        result = whole_block_absent(BLOCK, "not_quoted" if _ever_quoted(market, inputs.cut) is False else "stale")
        result.leave_out(f"{BLOCK}.{SAME_DAY_WRONG_FIELD}", "same_day_wrong")
        return result
    advancing, declining = counts
    result = BlockResult(data={"add_live_approx": int(round(advancing - declining)),
                               "adv_share_pct": int(round(100.0 * advancing / (advancing + declining)))})
    earlier = _counts_at(market, inputs.cut - timedelta(minutes=CHANGE_WINDOW_MIN))
    if earlier is None:
        result.leave_out(f"{BLOCK}.add_30m_change", "no_value_30m_ago")
    else:
        result.data["add_30m_change"] = int(round((advancing - declining) - (earlier[0] - earlier[1])))
    result.leave_out(f"{BLOCK}.{SAME_DAY_WRONG_FIELD}", "same_day_wrong")
    return result


def _counts_at(market: Any, at: datetime) -> tuple[float, float] | None:
    """``(advancing, declining)`` as known at ``at`` and no older than BREADTH_MAX_AGE_MIN; None otherwise."""
    if market is None:
        return None
    advancing = market.last(ADVANCING, at, max_age_min=BREADTH_MAX_AGE_MIN)
    declining = market.last(DECLINING, at, max_age_min=BREADTH_MAX_AGE_MIN)
    if advancing is None or declining is None or advancing + declining <= 0:
        return None
    return float(advancing), float(declining)


def _ever_quoted(market: Any, cut: datetime) -> bool:
    """Whether both counts had any value by ``cut``: tells a feed that never ran from one that stopped."""
    if market is None:
        return False
    for symbol in (ADVANCING, DECLINING):
        points = market.known.get(symbol) or []
        if not bisect.bisect_right([t for t, _ in points], cut):
            return False
    return True


__all__ = ["build_internals_block"]
