"""The rulers a distance or a size is measured in (the question set's conventions, "rulers").

* The morning anchor (sigma_anchor, morning_ruler): distances and moves are in the day's sigma as the
  scanner first set it, never the row's ``sigma``, which ratchets up with the live one. The anchor
  counts only from a row stamped by ANCHOR_GUARD; without one the day's earliest ``sigma_live`` stands
  in, else the settled open times the VIX over the square root of 252, and either is flagged
  estimated ("ruler estimated"): ranks leave such a day out.
* The live sigma (sigma_live): spot times at-the-money implied volatility over the square root of 252.
* The straddle left (straddle_left, remaining_straddles): what today's 0DTE straddle still prices for the
  rest of the day, in points, so the same distance reads as out of reach at 13:30 and inside the priced
  move at 15:30. Only straddle questions use it.
* The normal-day sigma (normal_day_sigma): the median morning anchor of up to the last 20 sessions, for
  events, where today's own anchor is swollen by the event it prices.
* The tape unit (ruler, tape_unit), for the opening lane: the median high-to-low range of the last three
  finished 5-minute slices. Held at RULER_HOLD_SIGMA until 09:45, when three slices first exist;
  floored at RULER_FLOOR_SIGMA so a dead tape still has a unit; never capped, so a wild open is
  measured as wild.
* The typical move (typical_move): how far SPX usually moves over the next 30 or 60 minutes now, the tape
  unit and the straddle left combined.

A sentence measured on an estimated morning ruler ends "(ruler estimated)" (ruled).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from ..cuts import MIN_RANK_SESSIONS, RULER_FLOOR_SIGMA, RULER_HOLD_SIGMA
from ..events import WORDS as EVENT_KINDS, starts_on
from .measures import ET, SETTLED_OPEN_BAR, bars_finished_between, is_num, minute_of_day, settled_open, slot
from .ranks import rank_at_slot, same_clock_values

if TYPE_CHECKING:
    from ..state_builder import MarketContext, Scene

TRADING_DAYS = 252
ANCHOR_GUARD = time(9, 40)       # the anchor counts only from a row stamped by then
RULER_SLICE_MIN = 5
RULER_SLICES = 3
RULER_HOLD_UNTIL = time(9, 45)
NO_ANCHOR = "no morning sigma ruler: no row by 09:40, no live sigma and no VIX at the settled open"
# Today's straddle left prices this share of a one-sigma move for the rest of the day (the set's reach formula).
STRADDLE_PER_SIGMA = 0.68


def ruler(bars: list[dict], sigma: float, now: datetime) -> dict | None:
    """The tape unit for a read at ``now``: ``unit_points``, ``unit_sigma``, the ``slices_used`` and
    its ``source`` (held, tape or floor). None when the newest slice has no bars: the tape has
    stopped, and a unit from an older stretch would pass off the last move as the current one."""
    if now.time() < RULER_HOLD_UNTIL:
        return {"unit_points": round(RULER_HOLD_SIGMA * sigma, 2), "unit_sigma": RULER_HOLD_SIGMA, "slices_used": 0, "source": "held"}
    ranges = []
    for k in range(RULER_SLICES):
        end = now - timedelta(minutes=RULER_SLICE_MIN * k)
        sl = bars_finished_between(bars, end - timedelta(minutes=RULER_SLICE_MIN), end)
        if sl:
            ranges.append(max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl))
        elif k == 0:
            return None
    unit = statistics.median(ranges)
    if unit < RULER_FLOOR_SIGMA * sigma:
        return {"unit_points": round(RULER_FLOOR_SIGMA * sigma, 2), "unit_sigma": RULER_FLOOR_SIGMA, "slices_used": len(ranges), "source": "floor"}
    return {"unit_points": round(unit, 2), "unit_sigma": round(unit / sigma, 3), "slices_used": len(ranges), "source": "tape"}


def slice_range(bars: list[dict], end_min: int) -> float | None:
    """High-to-low range of the 5-minute slice ending at ``end_min`` (a minute of day); None with no bars."""
    sl = slot(bars, end_min - RULER_SLICE_MIN, end_min)
    return max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl) if sl else None


def unit_sigma(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
    """The tape unit a session had at ``then``, in its own sigma: the middle of the three 5-minute ranges
    ending then. None without a ruler or with a slice that has no bars."""
    ranges = [slice_range(bars, minute_of_day(then) - RULER_SLICE_MIN * k) for k in range(RULER_SLICES)]
    return statistics.median(ranges) / sigma if sigma and all(r is not None for r in ranges) else None


def unit_rank(scene: Scene, unit: dict, anchor: SigmaRuler) -> dict | None:
    """The tape unit in today's morning ruler against the same minute on the prior sessions, each in its
    own ruler (unit_sigma, same_clock_values: a session with an estimated ruler is left out), placed in
    thirds (rank_at_slot). None while the unit is held (before 09:45 every day's unit is the same number)
    and under MIN_RANK_SESSIONS sessions. The one rank of the unit: the context line and
    tape.unit_vs_normal both read it."""
    if unit.get("source") == "held":
        return None
    return rank_at_slot(float(unit["unit_points"]) / anchor.points, same_clock_values(scene, unit_sigma))


def tape_unit(scene: Scene) -> dict | None:
    """The unit for a read on the tape lane: ruler() on the read's sigma, with its rank against the prior
    sessions at this minute under ``rank`` when there is one. What the record, the card and the sum's
    context line carry."""
    unit = ruler(scene.bars, scene.sigma, scene.now)
    anchor = sigma_anchor(scene)
    if unit and anchor:
        rank = unit_rank(scene, unit, anchor)
        if rank:
            unit["rank"] = rank
    return unit


@dataclass(frozen=True)
class SigmaRuler:
    """A day's sigma in index points and where it came from: the scanner's ``anchor``, the earliest
    ``live`` sigma, or the ``vix`` at the settled open. Anything but the anchor is an estimate."""
    points: float
    source: str

    @property
    def estimated(self) -> bool:
        return self.source != "anchor"


def morning_ruler(rows: list[dict], vix_at_open: float | None, open_price: float | None) -> SigmaRuler | None:
    """The day's morning anchor from its diary rows (oldest first) with the guard and its fallbacks
    (see the module note); None when not even the VIX and the settled open are known."""
    first = rows[0] if rows else None
    if first is not None and datetime.fromisoformat(first["ts"]).astimezone(ET).time() <= ANCHOR_GUARD:
        anchor = first.get("sigma_anchor", first.get("sigma"))
        if is_num(anchor) and anchor > 0:
            return SigmaRuler(float(anchor), "anchor")
    live = next((float(r["sigma_live"]) for r in rows if is_num(r.get("sigma_live")) and r["sigma_live"] > 0), None)
    if live is not None:
        return SigmaRuler(live, "live")
    if vix_at_open and open_price:
        return SigmaRuler(open_price * vix_at_open / 100.0 / math.sqrt(TRADING_DAYS), "vix")
    return None


def vix_at_settled_open(market: MarketContext | None, day: str) -> float | None:
    """The VIX known when the settled open's bar finished."""
    if market is None:
        return None
    done = datetime.combine(datetime.fromisoformat(day).date(), SETTLED_OPEN_BAR, tzinfo=ET) + timedelta(minutes=1)
    return market.last("$VIX", done)


def sigma_anchor(scene: Scene) -> SigmaRuler | None:
    """Today's morning anchor, as far as this read can know it."""
    return morning_ruler(scene.rows_today, vix_at_settled_open(scene.market, scene.day), settled_open(scene.bars))


def sigma_live(scene: Scene) -> float | None:
    """One trading day's expected move from the live implied volatility, in points: the row's
    ``sigma_live``, else spot times its at-the-money IV over the square root of 252."""
    live = scene.row.get("sigma_live")
    if is_num(live) and live > 0:
        return float(live)
    iv = scene.row.get("atm_iv")
    return scene.spot * float(iv) / math.sqrt(TRADING_DAYS) if is_num(iv) and iv > 0 else None


def straddle_left(scene: Scene) -> float | None:
    """What today's 0DTE straddle still prices for the rest of the day, in points (``range_ruler.em_points``)."""
    em = (scene.row.get("range_ruler") or {}).get("em_points")
    return float(em) if is_num(em) and em > 0 else None


def remaining_straddles(scene: Scene, points: float) -> float | None:
    """A distance in points as a count of what today's straddle still prices."""
    em = straddle_left(scene)
    return abs(points) / em if em else None


def normal_day_sigma(scene: Scene) -> float | None:
    """The median morning anchor of the prior sessions whose anchor was not estimated, in points; None
    under MIN_RANK_SESSIONS of them."""
    anchors = [r.points for r in scene.prior_rulers.values() if r is not None and not r.estimated]
    return statistics.median(anchors) if len(anchors) >= MIN_RANK_SESSIONS else None


def ruled(anchor: SigmaRuler, sentence: str) -> str:
    """A sentence measured on an estimated morning ruler says so."""
    return f"{sentence} (ruler estimated)" if anchor.estimated else sentence


def typical_move(scene: Scene, anchor: SigmaRuler, minutes: int) -> tuple[float | None, str]:
    """How far SPX typically moves over the next ``minutes`` now, in points, and what the figure combines;
    None with the reason when it cannot be measured. The tape's reach is the tape unit grown by the square
    root of time; the straddle's is what today's straddle still prices, as a one-sigma move, spread over the
    minutes left. The two combine as their geometric mean, and the tape stands alone while a scheduled
    event is still ahead today, since the straddle prices the event rather than an ordinary half hour."""
    unit = ruler(scene.bars, anchor.points, scene.now)
    if unit is None:
        return None, "no tape unit this read: the bars have stopped"
    tape = float(unit["unit_points"]) * math.sqrt(minutes / RULER_SLICE_MIN)
    if _event_ahead(scene):
        return tape, "the tape alone, with a scheduled event still ahead today"
    em = straddle_left(scene)
    if em is None:
        return None, "row carries no straddle left (range_ruler.em_points)"
    left = scene.minutes_to_close
    if left <= 0:
        return None, "the session has closed"
    straddle = em / STRADDLE_PER_SIGMA * math.sqrt(min(minutes, left) / left)
    return math.sqrt(tape * straddle), "tape and straddle combined"


def _event_ahead(scene: Scene) -> bool:
    """A tier-1 event starting after now and before today's close; one at the close itself is not ahead of the straddle."""
    day = scene.now.astimezone(ET).date()
    return any(scene.now < start < scene.session_close for kind in EVENT_KINDS if (start := starts_on(day, kind)) is not None)
