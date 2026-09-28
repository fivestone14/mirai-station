"""The gap and open family: the gap from yesterday's close to the settled open and its fate (gap.*), the
first minutes against the settled open (open.*), and the overnight range against the session (overnight.range,
overnight.price_vs_range); the premarket lane's overnight labels are labels/premarket.py's.

Every distance is in the morning anchor (rulers.sigma_anchor), and a sentence measured on an estimated
anchor says so. The gap is the settled open (the 09:34 close) against the row's ``prior_close``, and its
size is ranked in thirds against the same gap on the prior sessions, never against a fixed cut; whether
price has moved away from the settled open is judged in today's tape unit (rulers.ruler), not a fixed cut
either. Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``;
the overnight range labels wait for a session read of the overnight store (DARK).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ..cuts import (GAP_HALF_SHARE, GAP_RULE_SIGMA, GAP_TOUCH_SIGMA, GIVEBACK_THIRD, NOISE_EDGE_SIGMA, NOISE_LOOKBACK, ONE_CROSS,
                    OPEN_CONTESTED_CROSSES, RANGE_BOTTOM_SHARE, RANGE_TOP_SHARE, WINDOW_30_MIN, WINDOW_60_MIN)
from ..state_builder import Scene, first_row
from .label_set import LabelSet
from .measures import (ET, ONE_MINUTE, SETTLED_OPEN_BAR, bar_time, bars_between, bars_finished_between, close_at, day_high_low,
                       is_num, session_extremes, settled_open)
from .ranks import SameClockRank, rank_days, rank_sessions, same_clock_values
from .rulers import NO_ANCHOR, RULER_HOLD_UNTIL, SigmaRuler, ruled, ruler, sigma_anchor, typical_move
from .words import above_or_below, minutes_ago, pct, plural, sig

LABELS = ("gap.size", "gap.fill_progress", "gap.morning_vs_gap", "gap.reach_distance",
          "open.fresh_extreme", "open.noise_band", "open.path", "open.settled_open_crosses",
          "overnight.price_vs_range", "overnight.range")
GATES = ("gap_fill_next_hour",)
NO_OVERNIGHT = ("the overnight store (state/spx_jev/overnight/, saved at 09:26 ET) holds /ES's bars, "
                "but no session read measures the night's range from them yet")
DARK = {
    "overnight.price_vs_range": NO_OVERNIGHT,
    "overnight.range": NO_OVERNIGHT,
}

# The morning a gap is judged over (gap.morning_vs_gap) ends here.
LATE_MORNING = time(11, 30)
# The opening lane reads every 5 minutes, so there a fresh extreme is one made since its last read.
OPENING_LANE_WINDOW_MIN = 5
# The opening range the fresh-extreme label places price against: the first 15 minutes.
OPENING_RANGE_MIN = 15


def build_gap_open_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    anchor = sigma_anchor(scene)
    gap, why = _measure_gap(scene, anchor)
    _size(scene, gap, why, ls)
    _fill_progress(scene, gap, why, ls)
    _morning_vs_gap(scene, gap, why, ls)
    _reach_distance(scene, anchor, ls)
    _fresh_extreme(scene, anchor, ls)
    _noise_band(scene, anchor, ls)
    _path(scene, anchor, gap, ls)
    _settled_open_crosses(scene, anchor, ls)
    return ls


@dataclass(frozen=True)
class Gap:
    """This morning's gap: yesterday's close, the settled open and the ruler it is measured in."""
    prior_close: float
    settled_open: float
    anchor: SigmaRuler

    @property
    def size(self) -> float:
        """The signed gap in sigma."""
        return (self.settled_open - self.prior_close) / self.anchor.points

    @property
    def real(self) -> bool:
        return abs(self.size) >= GAP_RULE_SIGMA

    @property
    def side(self) -> str:
        return "up" if self.size > 0 else "down"


def _measure_gap(scene: Scene, anchor: SigmaRuler | None) -> tuple[Gap | None, str]:
    """The gap, or None and why it cannot be measured."""
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0:
        return None, "row carries no prior close"
    so = settled_open(scene.bars)
    if so is None:
        return None, "no settled open yet: the 09:34 bar has not finished"
    if anchor is None:
        return None, NO_ANCHOR
    return Gap(float(pc), so, anchor), ""


def _minutes_since_settled(scene: Scene) -> str:
    return plural(int((scene.now - _settled_at(scene)).total_seconds() // 60), "minute")


def _settled_at(scene: Scene) -> datetime:
    """When the settled open's bar finished: 09:35."""
    return scene.session_open.replace(hour=SETTLED_OPEN_BAR.hour, minute=SETTLED_OPEN_BAR.minute) + ONE_MINUTE


def _since_settled(scene: Scene) -> list[dict]:
    """Today's finished bars after the settled open's bar."""
    return [b for b in scene.bars if bar_time(b) >= _settled_at(scene)]


def _first_touch(gap: Gap, bars: list[dict]) -> datetime | None:
    """When a bar first came within the touch distance of yesterday's close from the gap's side."""
    tol = GAP_TOUCH_SIGMA * gap.anchor.points
    for b in bars:
        if (float(b["low"]) <= gap.prior_close + tol) if gap.size > 0 else (float(b["high"]) >= gap.prior_close - tol):
            return bar_time(b) + ONE_MINUTE
    return None


def _gap_side(gap: Gap, kept: float, touched: bool) -> str:
    """The code's answer key for gap_side_now."""
    if not gap.real:
        return "no_gap"
    return f"gap_{gap.side}_{'losing' if kept < GAP_HALF_SHARE or touched else 'intact'}"


def prior_gap_sizes(scene: Scene) -> list[float]:
    """Each prior session's gap measured as today's is, unsigned, newest first: its settled open against the
    prior close on its first diary row, in its own morning anchor. rank_days leaves out a session whose anchor
    was estimated; one without an anchor, a settled open or a prior close on file is left out too."""
    out = []
    for day in rank_days(scene):
        ruler, so, rows = scene.prior_rulers.get(day), settled_open(scene.prior_bars[day]), first_row(scene.state_dir, day)
        pc = rows[0].get("prior_close") if rows else None
        if ruler is not None and so is not None and is_num(pc) and pc > 0:
            out.append(abs(so - float(pc)) / ruler.points)
    return out


def gap_rank(scene: Scene, size: float) -> tuple[SameClockRank | None, str | None]:
    """A gap of ``size`` sigma against the prior sessions' gaps (prior_gap_sizes) under the owner's rank rule
    (ranks.rank_sessions): the one rank of the gap's size, for gap.size and every label that asks whether
    there was a real gap."""
    if scene.state_dir is None:
        return None, "no state folder to read the prior sessions' closes from"
    return rank_sessions(abs(size), prior_gap_sizes(scene), "a trusted morning ruler, a settled open and yesterday's close")


def _size(scene: Scene, gap: Gap | None, why: str, ls: LabelSet) -> None:
    """How large this morning's gap was against the prior sessions' gaps, in thirds, then which way it went."""
    if gap is None:
        ls.omit("gap.size", why)
        return
    g = gap.size
    rank, no_rank = gap_rank(scene, g)
    if rank is None:
        ls.omit("gap.size", no_rank)
        return
    # the morning's straddle is the first row's em_open: what today's 0DTE priced before the gap was known
    em_open = next((float(em) for r in scene.rows_today if is_num(em := (r.get("range_ruler") or {}).get("em_open")) and em > 0), None)
    straddle = f"; {abs(gap.settled_open - gap.prior_close) / em_open:.1f} times this morning's same-day straddle" if em_open else ""
    ls.put("gap.size", ruled(gap.anchor, f"this morning's gap of {sig(abs(g))} is larger than {rank.higher_than} of the last {rank.of} "
                                         f"days' gaps, {rank.band}: price opened {above_or_below(g)} yesterday's close, "
                                         f"measured at 09:35 because the 09:30 print uses stale prices{straddle}"))


def _fill_progress(scene: Scene, gap: Gap | None, why: str, ls: LabelSet) -> None:
    """Where price sits against yesterday's close now, then how much of the gap it keeps and whether it has
    touched the close since the settled open; decides gap_fill_next_hour's gate from the same numbers."""
    if gap is None:
        ls.omit("gap.fill_progress", why)
        ls.sleep("gap_fill_next_hour", f"no gap to fill: {why}")
        return
    now_d = (scene.spot - gap.prior_close) / gap.anchor.points
    where = f"{sig(abs(now_d))} {above_or_below(now_d)} yesterday's close"
    if not gap.real:
        ls.put("gap.fill_progress", ruled(gap.anchor, f"there was no real gap: price opened {sig(abs(gap.size))} {above_or_below(gap.size)} "
                                                      f"yesterday's close, within the {sig(GAP_RULE_SIGMA)} gap rule; it now sits {where}"))
        ls.sleep("gap_fill_next_hour", f"no real gap: the settled open was within the {sig(GAP_RULE_SIGMA)} gap rule")
        return
    kept = (scene.spot - gap.prior_close) / (gap.settled_open - gap.prior_close)
    touched = _first_touch(gap, _since_settled(scene))
    side = _gap_side(gap, kept, touched is not None)
    minutes = _minutes_since_settled(scene)
    head = f"the gap {gap.side} is {'still open' if side.endswith('intact') else 'losing ground'} after {minutes}"
    line = "at or above the half line" if kept >= GAP_HALF_SHARE else "below the half line"
    if kept >= 0:
        location = f"price sits {where} and keeps {pct(kept)} of the {sig(abs(gap.size))} gap, {line}"
    else:
        location = f"price sits {where}, through it, and keeps none of the {sig(abs(gap.size))} gap, {line}"
    touch = (f"it first touched yesterday's close (within {sig(GAP_TOUCH_SIGMA)}) {minutes_ago(scene.now, touched)}" if touched
             else f"it has not touched yesterday's close (within {sig(GAP_TOUCH_SIGMA)})")
    ls.put("gap.fill_progress", ruled(gap.anchor, f"{head}: {location}; {touch}; the gap was past the {sig(GAP_RULE_SIGMA)} gap rule"))
    if side.endswith("losing"):
        ls.wake("gap_fill_next_hour")
    else:
        ls.sleep("gap_fill_next_hour", f"the gap {gap.side} is still open, not losing ground")


def _morning_vs_gap(scene: Scene, gap: Gap | None, why: str, ls: LabelSet) -> None:
    if gap is None:
        ls.omit("gap.morning_vs_gap", why)
        return
    if not gap.real:
        ls.omit("gap.morning_vs_gap", f"no real gap this morning: the settled open was within the {sig(GAP_RULE_SIGMA)} gap rule")
        return
    late = scene.session_open.replace(hour=LATE_MORNING.hour, minute=LATE_MORNING.minute)
    late_close = next((float(b["close"]) for b in scene.bars if bar_time(b) == late - ONE_MINUTE), None)
    if late_close is None:
        ls.omit("gap.morning_vs_gap", f"the morning runs to {LATE_MORNING:%H:%M} and its last bar has not finished")
        return
    m = (late_close - gap.settled_open) / gap.anchor.points
    touched = _first_touch(gap, bars_finished_between(scene.bars, _settled_at(scene), late)) is not None
    now_d = (scene.spot - gap.prior_close) / gap.anchor.points
    ls.put("gap.morning_vs_gap", ruled(
           gap.anchor,
           f"from the settled open to {LATE_MORNING:%H:%M} price {'rose' if m >= 0 else 'fell'} {sig(abs(m))}, "
           f"{'with' if m * gap.size > 0 else 'against' if m else 'neither with nor against'} this morning's {sig(abs(gap.size))} gap {gap.side} "
           f"(past the {sig(GAP_RULE_SIGMA)} gap rule), and {'touched' if touched else 'did not touch'} yesterday's close; "
           f"it now sits {sig(abs(now_d))} {above_or_below(now_d)} it"))


def _reach_distance(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0:
        ls.omit("gap.reach_distance", "row carries no prior close")
        return
    if anchor is None:
        ls.omit("gap.reach_distance", NO_ANCHOR)
        return
    reach, why = typical_move(scene, anchor, WINDOW_60_MIN)
    if reach is None:
        ls.omit("gap.reach_distance", why)
        return
    d = (float(pc) - scene.spot) / anchor.points
    ls.put("gap.reach_distance", ruled(anchor, f"yesterday's close is {sig(abs(d))} {above_or_below(d)} price, "
                                               f"{abs(float(pc) - scene.spot) / reach:.1f} typical {WINDOW_60_MIN}-minute moves away"))


def _fresh_extreme(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """Whether the window's bars made a new session high or low against the bars before them, then where
    price sits in today's range, then where it sits against the first 15 minutes' range. The window
    is the lane's: 5 minutes on the opening lane, 30 on the live lane."""
    if anchor is None:
        ls.omit("open.fresh_extreme", NO_ANCHOR)
        return
    window = OPENING_LANE_WINDOW_MIN if scene.bar_clock else WINDOW_30_MIN
    start = scene.now - timedelta(minutes=window)
    before = session_extremes(bars_finished_between(scene.bars, scene.session_open, start))
    recent = session_extremes(bars_finished_between(scene.bars, start, scene.now))
    if before is None or recent is None:
        ls.omit("open.fresh_extreme", f"needs finished bars both before and inside the last {window} minutes")
        return
    points = anchor.points
    new_high, new_low = recent.high > before.high, recent.low < before.low
    hi, lo = day_high_low(scene.bars, scene.spot)
    if hi <= lo:
        ls.omit("open.fresh_extreme", "today's range is zero so far")
        return
    lead = f"in the last {window} minutes price set"
    if new_high and new_low:
        head = (f"{lead} both a new session high, {sig((recent.high - before.high) / points)} above the earlier high from "
                f"{before.high_at.astimezone(ET):%H:%M}, and a new session low, {sig((before.low - recent.low) / points)} below the "
                f"earlier low from {before.low_at.astimezone(ET):%H:%M}")
    elif new_high:
        head = (f"{lead} a new session high, {sig((recent.high - before.high) / points)} above the earlier high from "
                f"{before.high_at.astimezone(ET):%H:%M}; it now sits {sig((hi - scene.spot) / points)} below that high")
    elif new_low:
        head = (f"{lead} a new session low, {sig((before.low - recent.low) / points)} below the earlier low from "
                f"{before.low_at.astimezone(ET):%H:%M}; it now sits {sig((scene.spot - lo) / points)} above that low")
    else:
        head = (f"in the last {window} minutes price set no new session high or low; the high from {before.high_at.astimezone(ET):%H:%M} "
                f"is {sig((hi - scene.spot) / points)} above price and the low from {before.low_at.astimezone(ET):%H:%M} "
                f"is {sig((scene.spot - lo) / points)} below it")
    width = f"today's {sig((hi - lo) / points)} range"
    pos = (scene.spot - lo) / (hi - lo)
    if pos >= RANGE_TOP_SHARE:
        band = f"in the top band of {width} ({pct(pos)} of the way up, at or past the {pct(RANGE_TOP_SHARE)} line)"
    elif pos <= RANGE_BOTTOM_SHARE:
        band = f"in the bottom band of {width} ({pct(pos)} of the way up, at or under the {pct(RANGE_BOTTOM_SHARE)} line)"
    else:
        band = f"between the bands of {width} ({pct(pos)} of the way up, between the {pct(RANGE_BOTTOM_SHARE)} and {pct(RANGE_TOP_SHARE)} lines)"
    ls.put("open.fresh_extreme", ruled(anchor, f"{head}; price is {band}{_opening_range(scene)}"))


def _opening_range(scene: Scene) -> str:
    """Where price sits against the first 15 minutes' range, with when it first closed outside it; empty
    until that range is complete."""
    end = scene.session_open + timedelta(minutes=OPENING_RANGE_MIN)
    first = bars_between(scene.bars, scene.session_open, end)
    if scene.now < end or len(first) < OPENING_RANGE_MIN:
        return ""
    top, bottom = max(float(b["high"]) for b in first), min(float(b["low"]) for b in first)
    after = [b for b in scene.bars if bar_time(b) >= end]
    if scene.spot > top:
        broke = next((b for b in after if float(b["close"]) > top), None)
        side = "above"
    elif scene.spot < bottom:
        broke = next((b for b in after if float(b["close"]) < bottom), None)
        side = "below"
    else:
        return f"; it is inside the first-{OPENING_RANGE_MIN}-minute range"
    when = f", which it first broke at {(bar_time(broke) + ONE_MINUTE).astimezone(ET):%H:%M}" if broke else ""
    return f"; it is {side} the first-{OPENING_RANGE_MIN}-minute range{when}"


def _move_from_open(bars: list[dict], then: datetime, _sigma: float | None) -> float | None:
    """How far a day's price had moved from its settled open by ``then``, as a share of the open."""
    so, c = settled_open(bars), close_at(bars, then)
    return abs(c / so - 1.0) if so and c is not None else None


def _noise_band(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """The usual move-from-the-open band for this minute (Zarattini, Aziz and Barbon): the average move
    from the settled open at this minute over the last NOISE_LOOKBACK sessions, laid above the higher
    and below the lower of today's settled open and yesterday's close."""
    so, pc = settled_open(scene.bars), scene.row.get("prior_close")
    if so is None or not _since_settled(scene):
        ls.omit("open.noise_band", "no finished bar after the settled open yet")
        return
    if not is_num(pc) or pc <= 0:
        ls.omit("open.noise_band", "row carries no prior close")
        return
    if anchor is None:
        ls.omit("open.noise_band", NO_ANCHOR)
        return
    moves = same_clock_values(scene, _move_from_open)[:NOISE_LOOKBACK]
    if len(moves) < NOISE_LOOKBACK:
        ls.omit("open.noise_band", f"needs {NOISE_LOOKBACK} prior sessions with bars at this minute, have {len(moves)}")
        return
    usual = sum(moves) / len(moves)
    upper, lower = max(so, float(pc)) * (1 + usual), min(so, float(pc)) * (1 - usual)
    up, down = (scene.spot - upper) / anchor.points, (scene.spot - lower) / anchor.points
    band = f"the usual move-from-the-open band for {scene.now.astimezone(ET):%H:%M}"
    how = f"(edges anchored at the higher and lower of the open and yesterday's close, {NOISE_LOOKBACK}-session average move)"
    edge = NOISE_EDGE_SIGMA
    if abs(up) <= edge or abs(down) <= edge:
        d, name = (up, "upper") if abs(up) <= abs(down) else (down, "lower")
        text = f"price is at the {name} edge of {band}, {sig(abs(d))} {above_or_below(d)} it, within the {sig(edge)} edge distance {how}"
    elif up > edge:
        text = f"price is {sig(up)} above the upper edge of {band}, beyond the {sig(edge)} edge distance {how}"
    elif down < -edge:
        text = f"price is {sig(-down)} below the lower edge of {band}, beyond the {sig(edge)} edge distance {how}"
    else:
        text = (f"price is inside {band}, {sig(-up)} below its upper edge and {sig(down)} above its lower edge, "
                f"more than the {sig(edge)} edge distance from both {how}")
    ls.put("open.noise_band", ruled(anchor, text))


def _crosses(bars: list[dict], level: float) -> int:
    """How many times consecutive closes switched side of ``level``; a close on it keeps the side before."""
    count, side = 0, 0
    for b in bars:
        now_side = (float(b["close"]) > level) - (float(b["close"]) < level)
        if now_side and side and now_side != side:
            count += 1
        side = now_side or side
    return count


def _path(scene: Scene, anchor: SigmaRuler | None, gap: Gap | None, ls: LabelSet) -> None:
    """The opening's path from the settled open: how far each way it reached, how often it crossed, how
    much of its furthest reach on price's side it gave back, and where price sits now in today's tape
    unit: more than one unit from the open is away from it, within one is rotating. Omitted while the
    unit is held (before 09:45 it is the same fixed number every day) and when the bars have stopped."""
    so, since = settled_open(scene.bars), _since_settled(scene)
    if so is None or not since:
        ls.omit("open.path", "no finished bar after the settled open yet")
        return
    if anchor is None:
        ls.omit("open.path", NO_ANCHOR)
        return
    unit = ruler(scene.bars, anchor.points, scene.now)
    if unit is None:
        ls.omit("open.path", "no tape unit this read: the bars have stopped")
        return
    if unit["source"] == "held":
        ls.omit("open.path", f"the tape unit is held until {RULER_HOLD_UNTIL:%H:%M}, when three 5-minute slices first exist")
        return
    points, unit_points = anchor.points, float(unit["unit_points"])
    hi, lo = day_high_low(since, scene.spot)
    hi, lo = max(hi, so), min(lo, so)           # bars that never traded back to the open reached nothing on that side
    now_d = (scene.spot - so) / points
    minutes = _minutes_since_settled(scene)
    crosses = _crosses(since, so)
    crossed = "never crossed it" if crosses == 0 else "crossed it once" if crosses == 1 else f"crossed it {crosses} times"
    reached = f"{minutes} after the settled open, price reached {sig((hi - so) / points)} above it and {sig((so - lo) / points)} below it"
    floor = ", at its floor" if unit["source"] == "floor" else ""
    where = (f"price is now {abs(scene.spot - so) / unit_points:.1f} tape units {above_or_below(now_d)} the settled open "
             f"(one tape unit = the typical 5-minute swing right now, {sig(unit_points / points)}{floor})")
    if abs(scene.spot - so) <= unit_points:
        history = f"{reached} and {crossed}"
        where += ", within one tape unit"
    else:
        up = now_d > 0
        reach = (hi - so) if up else (so - lo)
        giveback = ((hi - scene.spot) if up else (scene.spot - lo)) / reach
        line = "under the stall line" if giveback < GIVEBACK_THIRD else "at or past the stall line"
        history = f"{reached}, {crossed}, and gave back {pct(giveback)} of its {'high' if up else 'low'}, {line}"
        where += f", more than one tape unit away on the {'up' if up else 'down'} side"
        if gap is not None:
            where += (", with no real gap this morning" if not gap.real else
                      ", on the gap's side" if up == (gap.size > 0) else ", against the gap's side")
    ls.put("open.path", ruled(anchor, f"{history}; {where}"))


def _settled_open_crosses(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    so, since = settled_open(scene.bars), _since_settled(scene)
    if so is None or not since:
        ls.omit("open.settled_open_crosses", "no finished bar after the settled open yet")
        return
    if anchor is None:
        ls.omit("open.settled_open_crosses", NO_ANCHOR)
        return
    n = _crosses(since, so)
    if n <= ONE_CROSS:
        verdict = f"no more than {ONE_CROSS} cross, one-sided"
    elif n < OPEN_CONTESTED_CROSSES:
        verdict = f"more than {ONE_CROSS} but under the {OPEN_CONTESTED_CROSSES}-cross contested rule"
    else:
        verdict = f"at or past the {OPEN_CONTESTED_CROSSES}-cross contested rule"
    d = (scene.spot - so) / anchor.points
    minutes = _minutes_since_settled(scene)
    ls.put("open.settled_open_crosses", ruled(anchor, f"since the settled open {minutes} ago price has crossed it {plural(n, 'time')}, "
                                                      f"{verdict}; it now sits {sig(abs(d))} {above_or_below(d)} it"))
