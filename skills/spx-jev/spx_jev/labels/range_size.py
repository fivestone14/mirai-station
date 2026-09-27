"""The range family: how wide the day has been and where price sits in it (range.*), how far a typical
half hour reaches now (ruler.*), the tape unit against the same minute of the prior sessions and, on the
opening lane, the stretch since the lane's last read in tape units (tape.*).

The final question set's labels are measured on the morning anchor (rulers.sigma_anchor); the ones built
before it keep the row's sigma. Each set label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..cuts import (FLAT_REACH_NARROW_30, FLAT_REACH_NARROW_60, FLAT_REACH_WIDE_30, FLAT_REACH_WIDE_60, IB_BREAK_SIGMA, IB_EXTEND_SIGMA,
                    MIN_RANK_SESSIONS, MOVE_RULE_SIGMA, NEXT_30_FLAT_BAND_SIGMA, NEXT_60_FLAT_BAND_SIGMA, ONE_RATIO, RANGE_PACE_SLEEPY,
                    RANGE_PACE_WILD, RULER_FLOOR_SIGMA, SHAPE_CUT_SIGMA, TAPE_BIG_UNITS, TAPE_FLAT_UNITS, THIRD_HI, WALL_NEAR_SIGMA,
                    WINDOW_30_MIN, WINDOW_60_MIN)
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import (ET, HOUR_MIN_BARS, MIN_RANGE_SESSIONS, ONE_MINUTE, RANGE_PRIOR_SESSIONS, bar_time, bars_between, bars_finished_between,
                       close_at, day_high_low, high_low_close, is_num, minute_of_day, move_bar, stretch, stretch_range, walls, yesterdays_bars)
from .ranks import rank_against, rank_at_slot, same_clock_values
from .rulers import NO_ANCHOR, RULER_HOLD_UNTIL, SigmaRuler, ruled, ruler, sigma_anchor, typical_move, unit_rank, unit_sigma
from .words import minutes_ago, pct, plural, sig, third, units_of

LABELS = ("range.box_status", "range.today_vs_normal", "range.prior_level_touches", "range.session_shape", "range.nearest_level",
          "tape.move_since_read", "tape.range_since_read",
          "range.first_hour", "range.hour_vs_clock", "range.pace_vs_priced", "ruler.flat_band_reach", "tape.unit_vs_normal")
GATES: tuple[str, ...] = ()
DARK: dict[str, str] = {}
# Measured only on the bar clock (the opening lane): a live read neither writes nor omits them.
BAR_CLOCK_ONLY = ("tape.move_since_read", "tape.range_since_read", "tape.unit_vs_normal")

# The opening box is the first 30 minutes; a break is past the move bar (measures.move_bar).
OPENING_BOX_MIN = 30
# The set's labels measured on the morning anchor, omitted together when the day has none.
ANCHORED = ("range.first_hour", "range.hour_vs_clock", "range.pace_vs_priced", "ruler.flat_band_reach")
FULL_SESSION_MIN = 390          # sigma is a full session's expected move
FIRST_HOUR_MIN = 60


def build_range_size_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _box_status(scene, ls)
    _today_vs_normal(scene, ls)
    _prior_level_touches(scene, ls)
    _session_shape(scene, ls)
    _nearest_level(scene, ls)
    _since_last_read(scene, ls)
    anchor = sigma_anchor(scene)
    _unit_vs_normal(scene, anchor, ls)
    if anchor is None:
        for path in ANCHORED:
            ls.omit(path, NO_ANCHOR)
        return ls
    _first_hour(scene, anchor, ls)
    _hour_vs_clock(scene, anchor, ls)
    _pace_vs_priced(scene, anchor, ls)
    _flat_band_reach(scene, anchor, ls)
    return ls


def _box_status(scene: Scene, ls: LabelSet) -> None:
    bars, spot, sigma, open_t = scene.bars, scene.spot, scene.sigma, scene.session_open
    opening = bars_between(bars, open_t, open_t + timedelta(minutes=OPENING_BOX_MIN))
    if len(opening) < OPENING_BOX_MIN:
        ls.put("range.box_status", f"the opening box is still forming, {plural(len(opening), 'minute')} of its {OPENING_BOX_MIN} are in")
        return
    box_hi = max(float(x["high"]) for x in opening)
    box_lo = min(float(x["low"]) for x in opening)
    bar = move_bar(bars)
    width = (box_hi - box_lo) / sigma
    if spot > box_hi + bar:
        status = f"price has broken above the opening box and sits {sig((spot - box_hi) / sigma)} beyond its top"
    elif spot < box_lo - bar:
        status = f"price has broken below the opening box and sits {sig((box_lo - spot) / sigma)} beyond its bottom"
    else:
        status = "price is inside the opening box"
    later = [x for x in bars if bar_time(x) >= open_t + timedelta(minutes=OPENING_BOX_MIN)]
    broke_up = any(float(x["close"]) > box_hi + bar for x in later)
    broke_dn = any(float(x["close"]) < box_lo - bar for x in later)
    history = ""
    if status.startswith("price is inside"):
        if broke_up and broke_dn:
            history = "; earlier today it broke out both ways and came back"
        elif broke_up:
            history = "; earlier today it broke above the box and came back inside"
        elif broke_dn:
            history = "; earlier today it broke below the box and came back inside"
        else:
            history = " and has stayed inside it all day"
    ls.put("range.box_status", f"{status}{history}; the box is {sig(width)} wide")


def _today_vs_normal(scene: Scene, ls: LabelSet) -> None:
    bars = scene.bars
    if not bars:
        ls.omit("range.today_vs_normal", "no finished bars yet")
        return
    hi, lo = day_high_low(bars, scene.spot)
    open0 = float(bars[0]["open"])
    if open0 <= 0:
        ls.omit("range.today_vs_normal", "no opening price")
        return
    today = (hi - lo) / open0
    cutoff = minute_of_day(scene.now)
    prior: list[float] = []
    for pbars in list(scene.prior_bars.values())[:RANGE_PRIOR_SESSIONS]:
        same = [x for x in pbars if minute_of_day(bar_time(x)) + 1 <= cutoff]
        if len(same) < 5 or float(same[0]["open"]) <= 0:
            continue
        prior.append((max(float(x["high"]) for x in same) - min(float(x["low"]) for x in same)) / float(same[0]["open"]))
    if len(prior) < MIN_RANGE_SESSIONS:
        ls.omit("range.today_vs_normal", f"needs {MIN_RANGE_SESSIONS} prior sessions of bars at this time of day, have {len(prior)}")
        return
    below = sum(1 for p in prior if p < today)
    ls.put("range.today_vs_normal",
           f"today's range so far is in the {third(below / len(prior))} third of the last {len(prior)} sessions at this time of day, larger than {below} of them")


def _prior_level_touches(scene: Scene, ls: LabelSet) -> None:
    _, y, why = yesterdays_bars(scene)
    bar = move_bar(scene.bars)
    if y is None or bar is None:
        ls.omit("range.prior_level_touches", why or "needs 10 finished bars today")
        return
    hi, lo, cl = high_low_close(y)
    counts = {name: sum(1 for x in scene.bars if float(x["low"]) <= lvl + bar and float(x["high"]) >= lvl - bar)
              for name, lvl in (("high", hi), ("low", lo), ("close", cl))}
    # a count of minutes, not of visits: a bar sitting on the level all afternoon counts every minute
    ls.put("range.prior_level_touches",
           f"price has spent {plural(counts['high'], 'minute')} within reach of yesterday's high, {plural(counts['low'], 'minute')} within reach of yesterday's low and {plural(counts['close'], 'minute')} within reach of yesterday's close today, counting reach as two typical minutes of range")


def _session_shape(scene: Scene, ls: LabelSet) -> None:
    bars, spot, sigma = scene.bars, scene.spot, scene.sigma
    if scene.minutes_since_open < 30 or not bars:
        ls.omit("range.session_shape", "needs 30 minutes of session")
        return
    open0 = float(bars[0]["open"])
    hi, lo = day_high_low(bars, spot)
    net = (spot - open0) / sigma
    up_ext, dn_ext = (hi - open0) / sigma, (open0 - lo) / sigma
    from_high, from_low = (hi - spot) / sigma, (spot - lo) / sigma
    cut = SHAPE_CUT_SIGMA
    if up_ext >= cut and from_high >= cut and from_high >= up_ext / 2:
        ls.put("range.session_shape", f"so far today price rose {sig(up_ext)} above the open then gave back {sig(from_high)} of it, so the session has reversed down, past the {cut} sigma cut")
    elif dn_ext >= cut and from_low >= cut and from_low >= dn_ext / 2:
        ls.put("range.session_shape", f"so far today price fell {sig(dn_ext)} below the open then recovered {sig(from_low)} of it, so the session has reversed up, past the {cut} sigma cut")
    elif net >= cut:
        ls.put("range.session_shape", f"so far today price is {sig(net)} above the open, more than the {cut} sigma cut, so the session is rising")
    elif net <= -cut:
        ls.put("range.session_shape", f"so far today price is {sig(-net)} below the open, more than the {cut} sigma cut, so the session is falling")
    else:
        ls.put("range.session_shape", f"so far today price is {sig(abs(net))} from the open, within the {cut} sigma cut, so the session is flat")


def _nearest_level(scene: Scene, ls: LabelSet) -> None:
    bars, spot, sigma, now = scene.bars, scene.spot, scene.sigma, scene.now
    back30 = now - timedelta(minutes=30)
    win = bars_finished_between(bars, back30, now)
    ref = close_at(bars, back30)
    if scene.minutes_since_open < 30 or len(win) < 20 or ref is None:
        ls.omit("range.nearest_level", "needs 30 minutes of finished bars")
        return
    levels: list[tuple[str, float]] = []
    vwap = scene.row.get("vwap")
    if is_num(vwap) and vwap > 0:
        levels.append(("the day's average price", float(vwap)))
    # the day's high and low come from before the window, so a level is never the window's own extreme
    early = bars_finished_between(bars, scene.session_open, back30)
    if early:
        levels.append(("the day's high", max(float(x["high"]) for x in early)))
        levels.append(("the day's low", min(float(x["low"]) for x in early)))
    _, y, _ = yesterdays_bars(scene)
    if y:
        hi, lo, cl = high_low_close(y)
        levels += [("yesterday's high", hi), ("yesterday's low", lo), ("yesterday's close", cl)]
    levels += [(f"the {name}", spot + d * sigma) for name, d, _ in walls(scene.row, spot, sigma)]
    if not levels:
        ls.omit("range.nearest_level", "no level to measure against")
        return
    name, lvl = min(levels, key=lambda lv: abs(lv[1] - spot))
    d = (spot - lvl) / sigma
    was_below, is_below = ref < lvl, spot < lvl
    side = "above" if is_below else "below"
    if was_below and not is_below:
        ls.put("range.nearest_level", f"price crossed {name} from below in the last 30 minutes and is {sig(d)} above it")
    elif is_below and not was_below:
        ls.put("range.nearest_level", f"price crossed {name} from above in the last 30 minutes and is {sig(-d)} below it")
    elif is_below and any(float(x["high"]) > lvl for x in win):
        ls.put("range.nearest_level", f"in the last 30 minutes price pushed above {name} and is back below it, {sig(-d)} under")
    elif not is_below and any(float(x["low"]) < lvl for x in win):
        ls.put("range.nearest_level", f"in the last 30 minutes price pushed below {name} and is back above it, {sig(d)} over")
    elif abs(d) <= MOVE_RULE_SIGMA:
        ls.put("range.nearest_level", f"the nearest level is {name}, {sig(abs(d))} {side} price, within the {MOVE_RULE_SIGMA} sigma reach, untouched in the last 30 minutes")
    elif abs(d) <= WALL_NEAR_SIGMA:
        ls.put("range.nearest_level", f"the nearest level is {name}, {sig(abs(d))} {side} price, beyond the {MOVE_RULE_SIGMA} sigma reach but within {WALL_NEAR_SIGMA} sigma, untouched in the last 30 minutes")
    else:
        ls.put("range.nearest_level", f"no level is within {WALL_NEAR_SIGMA} sigma of price; the nearest is {name}, {sig(abs(d))} {side}")


def _since_last_read(scene: Scene, ls: LabelSet) -> None:
    """Written only on the bar clock (the tape lane); the live lane writes nothing and omits nothing.
    The stretch runs from the lane's last read today, or from the open on the day's first read, to
    this read. The move keeps the sum's bands in tape units; the range is placed against the same
    minutes on the prior sessions, in thirds, with the count in the sentence."""
    if not scene.bar_clock:
        return
    since = scene.last_read if scene.last_read is not None else scene.session_open
    what = "the last read" if scene.last_read is not None else "the open"
    gap = (scene.now - since).total_seconds() / 60.0
    lead = f"since {what}, {gap:g} minutes ago"
    start_min, end_min = minute_of_day(since), minute_of_day(scene.now)
    anchor, win = stretch(scene.bars, start_min, end_min)
    if gap <= 0 or not win:
        for path in ("tape.move_since_read", "tape.range_since_read"):
            ls.omit(path, f"no finished bars {lead}")
        return
    if scene.unit is None:
        for path in ("tape.move_since_read", "tape.range_since_read"):
            ls.omit(path, "no tape unit this read: the bars have stopped")
        return
    u = float(scene.unit["unit_points"])
    if scene.last_read is None:
        ls.omit("tape.move_since_read", "the day's first read: no earlier read today to measure from")
    elif anchor is None:
        ls.omit("tape.move_since_read", f"no finished bar at {what}")
    else:
        net = scene.spot - anchor
        x = net / u
        where = "level with it" if net == 0 else f"{abs(net):.1f} points {'above' if net > 0 else 'below'} it"
        verb = "rose" if net > 0 else "fell"
        if abs(x) <= TAPE_FLAT_UNITS:
            cut, verdict = f"within the {TAPE_FLAT_UNITS} cut", "held"
        elif abs(x) <= TAPE_BIG_UNITS:
            cut, verdict = f"more than the {TAPE_FLAT_UNITS} cut and no more than {TAPE_BIG_UNITS}", f"{verb} small"
        else:
            cut, verdict = f"more than the {TAPE_BIG_UNITS} cut", f"{verb} big"
        ls.put("tape.move_since_read", f"{lead}, price ended {where}, {units_of(abs(x), u)}, {cut}, so it {verdict}")
    # the range needs every minute of the window on today's tape and on each prior session it is ranked against
    minutes = end_min - start_min
    if len(win) < minutes:
        ls.omit("tape.range_since_read", f"bars missing {lead}: {len(win)} of {minutes} minutes")
        return
    value = stretch_range(win)
    base = [stretch_range(pwin) for pbars in scene.prior_bars.values() if len(pwin := stretch(pbars, start_min, end_min)[1]) >= minutes]
    rank = rank_at_slot(value, base)
    if rank is None:
        ls.omit("tape.range_since_read", f"needs {MIN_RANK_SESSIONS} prior sessions of bars at these minutes, have {len(base)}")
        return
    ls.put("tape.range_since_read", f"the range {lead}, is {value:.1f} points, {units_of(value / u, u)}, "
                                    f"in the {rank['band']} for this minute, higher than {rank['higher_than']} of {rank['of']} prior sessions")


def _first_hour(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    """Where price is against the first hour's range, then what the bars since did with its edges: a break is
    a bar more than the break buffer past an edge, its reach the furthest any bar went past it."""
    open_t, a = scene.session_open, anchor.points
    end = open_t + timedelta(minutes=FIRST_HOUR_MIN)
    first = bars_between(scene.bars, open_t, end)
    if len(first) < FIRST_HOUR_MIN:
        ls.omit("range.first_hour", f"needs the first hour's {FIRST_HOUR_MIN} finished bars, have {len(first)}")
        return
    hi, lo = max(float(b["high"]) for b in first), min(float(b["low"]) for b in first)
    later = [b for b in scene.bars if bar_time(b) >= end]
    buffer = IB_BREAK_SIGMA * a
    above = [b for b in later if float(b["high"]) > hi + buffer]
    below = [b for b in later if float(b["low"]) < lo - buffer]
    spot = scene.spot
    if spot > hi:
        where = f"price is above the first hour's high, {sig((spot - hi) / a)} beyond it"
    elif spot < lo:
        where = f"price is below the first hour's low, {sig((lo - spot) / a)} beyond it"
    else:
        where = f"price is {'back ' if above or below else ''}inside the first hour's range"
    since = f"since {end.astimezone(ET):%H:%M}"

    def broke(breaks: list[dict], edge: str, reach: float) -> str:
        line = "at or past" if reach >= IB_EXTEND_SIGMA else "short of"
        return (f"{edge} {minutes_ago(scene.now, bar_time(breaks[0]) + ONE_MINUTE)}, reached {sig(reach)} beyond it, "
                f"{line} the {IB_EXTEND_SIGMA} sigma extension line")

    reach_up = (max(float(b["high"]) for b in above) - hi) / a if above else 0.0
    reach_dn = (lo - min(float(b["low"]) for b in below)) / a if below else 0.0
    if above and below:
        history = f"{since} price broke both edges: {broke(above, 'above its high', reach_up)}; and {broke(below, 'below its low', reach_dn)}"
    elif above:
        history = f"{since} price broke {broke(above, 'above its high', reach_up)}, and never broke its low"
    elif below:
        history = f"{since} price broke {broke(below, 'below its low', reach_dn)}, and never broke its high"
    else:
        history = f"{since} no bar has gone more than the {IB_BREAK_SIGMA} sigma break buffer past either edge"
    ls.put("range.first_hour", ruled(anchor, f"{where}; the first hour spanned {sig((hi - lo) / a)}; {history}"))


def _hour_range(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
    """The high-low range of the hour to ``then``, in sigma; None short of HOUR_MIN_BARS bars or without a ruler."""
    win = bars_finished_between(bars, then - timedelta(minutes=WINDOW_60_MIN), then)
    return stretch_range(win) / sigma if sigma and len(win) >= HOUR_MIN_BARS else None


def _hour_vs_clock(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    if scene.minutes_since_open < WINDOW_60_MIN:
        ls.omit("range.hour_vs_clock", f"needs {WINDOW_60_MIN} minutes of session")
        return
    value = _hour_range(scene.bars, scene.now, anchor.points)
    if value is None:
        ls.omit("range.hour_vs_clock", f"needs {HOUR_MIN_BARS} finished bars in the last {WINDOW_60_MIN} minutes")
        return
    base = same_clock_values(scene, _hour_range)
    rank = rank_against(value, base)
    if rank is None:
        ls.omit("range.hour_vs_clock", f"needs {MIN_RANK_SESSIONS} prior sessions with a morning ruler at this minute, have {len(base)}")
        return
    line = "past" if rank.share >= THIRD_HI else "short of"
    ls.put("range.hour_vs_clock", ruled(anchor, f"the last hour's high-low range is {sig(value)}, wider than {rank.higher_than} of the last "
                                                f"{rank.of} sessions at this time of day ({pct(rank.share)}), {line} the {pct(THIRD_HI)} wide line"))


def _pace_vs_priced(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    """Today's range against a one-sigma move scaled to the minutes since the open by the square root of time."""
    if not scene.bars:
        ls.omit("range.pace_vs_priced", "no finished bars yet")
        return
    minutes = min(scene.minutes_since_open, FULL_SESSION_MIN)
    hi, lo = day_high_low(scene.bars, scene.spot)
    ratio = (hi - lo) / (anchor.points * math.sqrt(minutes / FULL_SESSION_MIN))
    if ratio < RANGE_PACE_SLEEPY:
        band = f"under the line, below the {RANGE_PACE_SLEEPY:g} under line"
    elif ratio < ONE_RATIO:
        band = f"near the line, between the {RANGE_PACE_SLEEPY:g} under line and {ONE_RATIO:g}"
    elif ratio < RANGE_PACE_WILD:
        band = f"over the line, between {ONE_RATIO:g} and the {RANGE_PACE_WILD:g} far-over line"
    else:
        band = f"far over the line, past the {RANGE_PACE_WILD:g} far-over line"
    ls.put("range.pace_vs_priced", ruled(anchor, f"today's range so far is {ratio:.2f} of a one-sigma move for the {int(minutes)} minutes "
                                                 f"since the open: {band}"))


def _flat_band_reach(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    """The flat band against a typical move now, for each horizon flat_band_reach is asked at: the next 30 and 60 minutes."""
    clauses = []
    for minutes, band, narrow, wide in ((WINDOW_30_MIN, NEXT_30_FLAT_BAND_SIGMA, FLAT_REACH_NARROW_30, FLAT_REACH_WIDE_30),
                                        (WINDOW_60_MIN, NEXT_60_FLAT_BAND_SIGMA, FLAT_REACH_NARROW_60, FLAT_REACH_WIDE_60)):
        reach, how = typical_move(scene, anchor, minutes)
        if reach is None:
            ls.omit("ruler.flat_band_reach", how)
            return
        typical = reach / anchor.points
        cover = band / typical
        if cover < narrow:
            line = f"under the {narrow:.2f} narrow line"
        elif cover > wide:
            line = f"over the {wide:.2f} wide line"
        else:
            line = f"between the {narrow:.2f} narrow line and the {wide:.2f} wide line"
        # both horizons combine the same sources, so only the first says which
        now = f"now ({how}) " if not clauses else ""
        clauses.append(f"the next-{minutes}-minute flat band is {sig(band)}; a typical {minutes}-minute move {now}is {sig(typical)}, "
                       f"so the band covers {cover:.2f} of it, {line}")
    ls.put("ruler.flat_band_reach", ruled(anchor, "; ".join(clauses)))


def _unit_vs_normal(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """The tape unit in sigma against the same three slices on each prior session, in that session's sigma
    (rulers.unit_rank, the rank the sum's context line carries). Written only on the bar clock, as the other tape labels are: the opening lane is the one that reads it."""
    if not scene.bar_clock:
        return
    if anchor is None:
        ls.omit("tape.unit_vs_normal", NO_ANCHOR)
        return
    unit = ruler(scene.bars, anchor.points, scene.now)
    if unit is None:
        ls.omit("tape.unit_vs_normal", "no tape unit this read: the bars have stopped")
        return
    if unit["source"] == "held":
        ls.omit("tape.unit_vs_normal", f"the tape unit is held until {RULER_HOLD_UNTIL:%H:%M}, when three 5-minute slices first exist")
        return

    rank = unit_rank(scene, unit, anchor)
    if rank is None:
        have = len(same_clock_values(scene, unit_sigma))
        ls.omit("tape.unit_vs_normal", f"needs {MIN_RANK_SESSIONS} prior sessions with a morning ruler at this minute, have {have}")
        return
    value = float(unit["unit_points"]) / anchor.points
    floor = f", at its {RULER_FLOOR_SIGMA} sigma floor" if unit["source"] == "floor" else ""
    wider = f"all {rank['of']}" if rank["higher_than"] == rank["of"] else str(rank["higher_than"])
    ls.put("tape.unit_vs_normal", ruled(anchor, f"the tape unit (the middle of the last three 5-minute ranges) is {sig(value)}{floor}, wider than "
                                                f"{wider} of the last {rank['of']} sessions at {scene.now.astimezone(ET):%H:%M}, in the {rank['band']}"))
