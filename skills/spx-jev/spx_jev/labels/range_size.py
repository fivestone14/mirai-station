"""The range family: how wide the day has been and where price sits in it (range.*), and on the opening
lane the stretch since the lane's last read in tape units (tape.*).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

from datetime import timedelta

from ..cuts import MIN_RANK_SESSIONS, MOVE_RULE_SIGMA, SHAPE_CUT_SIGMA, TAPE_BIG_UNITS, TAPE_FLAT_UNITS, WALL_NEAR_SIGMA
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import (MIN_RANGE_SESSIONS, RANGE_PRIOR_SESSIONS, bar_time, bars_between, bars_finished_between, close_at, day_high_low, high_low_close, is_num, minute_of_day,
                       move_bar, stretch, stretch_range, walls)
from .ranks import rank_at_slot
from .words import plural, sig, third, units_of

LABELS = ("range.box_status", "range.today_vs_normal", "range.prior_level_touches", "range.session_shape", "range.nearest_level",
          "tape.move_since_read", "tape.range_since_read",
          "range.first_hour", "range.hour_vs_clock", "range.pace_vs_priced", "ruler.flat_band_reach", "tape.unit_vs_normal")
GATES: tuple[str, ...] = ()
DARK: dict[str, str] = {}

# The opening box is the first 30 minutes; a break is past the move bar (measures.move_bar).
OPENING_BOX_MIN = 30


def build_range_size_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _box_status(scene, ls)
    _today_vs_normal(scene, ls)
    _prior_level_touches(scene, ls)
    _session_shape(scene, ls)
    _nearest_level(scene, ls)
    _since_last_read(scene, ls)
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


def _yesterday(scene: Scene) -> list[dict] | None:
    return next(iter(scene.prior_bars.values()), None)


def _prior_level_touches(scene: Scene, ls: LabelSet) -> None:
    y = _yesterday(scene)
    bar = move_bar(scene.bars)
    if not y or bar is None:
        ls.omit("range.prior_level_touches", "needs yesterday's bars and 10 finished bars today")
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
    y = _yesterday(scene)
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
