"""The levels family: where price sits against yesterday's range and value area, round numbers, armed
breaks and wall touches (levels.*).

Every distance is in the morning anchor (rulers.sigma_anchor), and a sentence measured on an estimated
anchor says so. Yesterday is the session before today on the trading calendar, never an older one
standing in. Each label's sentence, how it is computed and its source are in spec/question_set.json
``labels``.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..cuts import (ACCEPT_MINUTES, CROSS_LOOKBACK_MIN, LEVEL_REACH_SIGMA, OUTSIDE_BUFFER_SIGMA, ROUND_NEAR_SIGMA, VALUE_EDGE_SIGMA,
                    WALL_TOUCH_QUIET_PERCENTILE, WALL_TOUCH_SIEGE_PERCENTILE, WALL_TOUCH_SIGMA, WINDOW_30_MIN)
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ONE_MINUTE, bar_time, bars_finished_between, close_at, high_low_close, is_num, settled_open, yesterdays_bars
from .rulers import NO_ANCHOR, SigmaRuler, ruled, sigma_anchor
from .words import above_or_below, minutes_ago, ordinal, pct, plural, sig

LABELS = ("levels.break_armed", "levels.open_vs_prior_range", "levels.prior_day", "levels.prior_value", "levels.round_number",
          "levels.wall_touch_effort")
GATES = ("break_armed",)
DARK: dict[str, str] = {}

# The value area is the band holding this share of yesterday's minutes, the market-profile convention.
VALUE_AREA_SHARE = 0.70
# Yesterday's time-at-price profile is counted in one-point bins, finer than a typical SPX minute's range.
PROFILE_BIN_POINTS = 1.0
# The round levels SPX traders mark: multiples of 50 points, a 100-point level where one falls on a hundred.
ROUND_STEP_POINTS = 50
# An armed break expires this long after it was armed (reversion_lens.BREAK_COCK_EXPIRE_MIN).
BREAK_EXPIRE_MIN = 45
BREAK_WAYS = {"call": "upward", "put": "downward"}
LEVEL_NAMES = {"prior_close": "yesterday's close", "prior_high": "yesterday's high", "prior_low": "yesterday's low",
               "call_wall": "the call-side heavy strike", "put_wall": "the put-side heavy strike",
               "call_wall_tenor": "the 1-to-7-day book's call-side heavy strike",
               "put_wall_tenor": "the 1-to-7-day book's put-side heavy strike"}
# The heavy strikes a touch is described at; the siege box's third tower, the magnet, has no side.
WALL_SIDES = {"call_wall": "call-side", "put_wall": "put-side"}


def build_levels_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    anchor = sigma_anchor(scene)
    _break_armed(scene, anchor, ls)
    _wall_touch_effort(scene, anchor, ls)
    _round_number(scene, anchor, ls)
    _, yesterday_bars, why = yesterdays_bars(scene)
    if anchor is None or yesterday_bars is None:
        for path in ("levels.open_vs_prior_range", "levels.prior_day", "levels.prior_value"):
            ls.omit(path, why or NO_ANCHOR)
        return ls
    _open_vs_prior_range(scene, anchor, yesterday_bars, ls)
    _prior_day(scene, anchor, yesterday_bars, ls)
    _prior_value(scene, anchor, yesterday_bars, ls)
    return ls


def _round_kind(level: float) -> int:
    return 100 if level % 100 == 0 else ROUND_STEP_POINTS


def _level_name(lr: dict) -> str:
    kind, level = lr.get("cock_level_kind"), lr.get("cock_level")
    if kind == "round" and is_num(level):
        return f"a round {_round_kind(float(level))}-point level"
    return LEVEL_NAMES.get(kind, "the level it was turned back at")


def _break_armed(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """The scanner's break setup (level_reclaim's break lens): armed now, else expired or called off
    within the last half hour, else nothing; the break_armed question is awake for the first two."""
    lr = scene.row.get("level_reclaim")
    if not lr or not lr.get("break_state"):
        ls.omit("levels.break_armed", "row carries no break read (level_reclaim.break_state)")
        ls.sleep("break_armed", "row carries no break read")
        return
    if lr["break_state"] == "cocked":
        ls.wake("break_armed")
        if lr.get("cock_direction") not in BREAK_WAYS or not is_num(lr.get("cock_level")) or not isinstance(lr.get("cocked_at"), str):
            ls.omit("levels.break_armed", "the armed break carries no direction, level or arming time")
            return
        if anchor is None:
            ls.omit("levels.break_armed", NO_ANCHOR)
            return
        armed = datetime.fromisoformat(lr["cocked_at"])
        left = max(round(BREAK_EXPIRE_MIN - (scene.now - armed).total_seconds() / 60.0), 0)
        d = (scene.spot - float(lr["cock_level"])) / anchor.points
        ls.put("levels.break_armed", ruled(
               anchor,
               f"a break {BREAK_WAYS[lr['cock_direction']]} is armed: {minutes_ago(scene.now, armed)} price was turned "
               f"back at {_level_name(lr)} while the book leaned to puts, and it now sits {sig(abs(d))} {above_or_below(d)} that level; "
               f"it expires in {plural(left, 'minute')} if price does not close through that level"))
        return
    since = scene.now - timedelta(minutes=WINDOW_30_MIN)
    ended = next(((datetime.fromisoformat(r["ts"]), r["level_reclaim"]) for r in reversed(scene.rows_today)
                  if (r.get("level_reclaim") or {}).get("break_state") in ("expired", "decocked")
                  and r["level_reclaim"].get("cock_direction") in BREAK_WAYS and datetime.fromisoformat(r["ts"]) >= since), None)
    if ended is None:
        ls.put("levels.break_armed", f"no break is armed, and none expired or was called off in the last {WINDOW_30_MIN} minutes")
        ls.sleep("break_armed", f"no break armed, and none expired or was called off in the last {WINDOW_30_MIN} minutes")
        return
    when, old = ended
    how = "expired without firing" if old["break_state"] == "expired" else "was called off"
    ls.put("levels.break_armed", f"no break is armed now: a break {BREAK_WAYS[old['cock_direction']]} armed at "
                                 f"{_level_name(old)} {how} {minutes_ago(scene.now, when)}, within the last {WINDOW_30_MIN} minutes")
    ls.wake("break_armed")


def _wall_touch_effort(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """The newest touch of a heavy strike in the last half hour (the siege box's towers: a touch began when
    its tower was first seen engaged and lasts while the tower shows engaged; a tower shows resolved for
    hours after, so a touch seen only resolved ended when first seen so), where price sits against that
    strike now, and the SPY volume the box judged during the touch. A touch the box has not judged
    describes no volume, and one still touching needs it."""
    sg = scene.row.get("siege")
    if not sg:
        ls.omit("levels.wall_touch_effort", "row carries no siege read")
        return
    if sg.get("health") != "OK":
        ls.omit("levels.wall_touch_effort", f"the siege box's SPY feed is {sg.get('health') or 'unreported'}, not OK")
        return
    if sg.get("baseline") != "robust":
        ls.omit("levels.wall_touch_effort", f"the siege box's volume baseline is {sg.get('baseline') or 'unreported'}, not yet robust")
        return
    if anchor is None:
        ls.omit("levels.wall_touch_effort", NO_ANCHOR)
        return
    began: dict[tuple, datetime] = {}
    last_touched: dict[tuple, datetime] = {}
    judged: dict[tuple, dict] = {}
    for r in scene.rows_today:
        seen = datetime.fromisoformat(r["ts"])
        for t in (r.get("siege") or {}).get("towers") or []:
            key = (t.get("kind"), t.get("level"))
            if key[0] in WALL_SIDES and is_num(key[1]) and t.get("status") in ("engaged", "resolved"):
                began.setdefault(key, seen)
                last_touched[key] = seen if t["status"] == "engaged" else last_touched.get(key, seen)
                if t.get("verdict") and is_num(t.get("effort_pct")):
                    judged[key] = t
    recent = [(touched, began[key], key) for key, touched in last_touched.items()
              if scene.now - touched <= timedelta(minutes=WINDOW_30_MIN)]
    if not recent:
        ls.omit("levels.wall_touch_effort", f"no heavy strike was touched in the last {WINDOW_30_MIN} minutes", ended=True)
        return
    _, when, (kind, level) = max(recent)
    side = WALL_SIDES[kind]
    # positive: past the strike, the way a break goes (up through the call side, down through the put side)
    past = (scene.spot - float(level)) / anchor.points * (1 if kind == "call_wall" else -1)
    touch_rule = sig(WALL_TOUCH_SIGMA)
    effort = _effort_words(judged.get((kind, level)))
    if past > WALL_TOUCH_SIGMA:
        text = (f"price went through the {side} heavy strike, first touched {minutes_ago(scene.now, when)}, and sits {sig(past)} past it, "
                f"more than the {touch_rule} touch rule, so it broke through")
    elif past < -WALL_TOUCH_SIGMA:
        text = (f"price tested the {side} heavy strike {minutes_ago(scene.now, when)} and is now {sig(-past)} back from it, more than the "
                f"{touch_rule} touch rule, so the strike held")
    elif not effort:
        ls.omit("levels.wall_touch_effort", f"price is still touching the {side} heavy strike and the siege box has not judged the "
                                            "touch's SPY volume: its window is still open, or the strike hugged price all along")
        return
    else:
        text = (f"price tested the {side} heavy strike {minutes_ago(scene.now, when)} and is now {sig(abs(past))} "
                f"{'past' if past > 0 else 'back from'} it, not {'past' if past > 0 else 'back'} by more than the {touch_rule} touch "
                f"rule, so it is still touching")
    ls.put("levels.wall_touch_effort", ruled(anchor, f"{text}; {effort}" if effort else text))


def _effort_words(tower: dict | None) -> str:
    if tower is None:
        return ""
    effort = float(tower["effort_pct"])
    siege, quiet = WALL_TOUCH_SIEGE_PERCENTILE, WALL_TOUCH_QUIET_PERCENTILE
    band = (f"the top band for those minutes (at or above the {siege}th percentile)" if effort >= siege else
            f"the bottom band for those minutes (at or below the {quiet}th percentile)" if effort <= quiet else
            f"the middle band for those minutes (between the {quiet}th and {siege}th percentiles)")
    return f"SPY volume during the touch was in the {ordinal(round(effort))} percentile of normal, {band}"


def _round_number(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """A round level price pushed through in the last CROSS_LOOKBACK_MIN minutes from at least the near
    distance away and is still through, else one it is pressing within the near distance. A push through
    that price has fallen back from is neither, so the label is omitted as ended, as it is with no level in play."""
    if not scene.bars:
        ls.omit("levels.round_number", "no finished bars yet")
        return
    if anchor is None:
        ls.omit("levels.round_number", NO_ANCHOR)
        return
    points, spot = anchor.points, scene.spot
    near = ROUND_NEAR_SIGMA * points
    since = max(scene.now - timedelta(minutes=CROSS_LOOKBACK_MIN), scene.session_open)
    ref = close_at(scene.bars, since) if since > scene.session_open else float(scene.bars[0]["open"])
    back = f"{CROSS_LOOKBACK_MIN} minutes ago" if since > scene.session_open else "at the open"
    window = bars_finished_between(scene.bars, since, scene.now)
    step = ROUND_STEP_POINTS
    below_spot = math.ceil(spot / step) * step - step          # the round level just under price
    above_spot = math.floor(spot / step) * step + step         # the round level just over price
    for level, up in ((below_spot, True), (above_spot, False)):
        crossed = _pushed_through(window, ref, level, near, up)
        if crossed is not None:
            r, d = (ref - level) / points, (spot - level) / points
            ls.put("levels.round_number", ruled(
                   anchor,
                   f"{minutes_ago(scene.now, bar_time(crossed) + ONE_MINUTE)} price {'rose' if up else 'fell'} through a round "
                   f"{_round_kind(level)}-point level and now sits {sig(abs(d))} {above_or_below(d)} it; {back} it was {sig(abs(r))} "
                   f"{above_or_below(r)}, past the {sig(ROUND_NEAR_SIGMA)} near distance"))
            return
    for level, up in ((above_spot, True), (below_spot, False)):
        crossed = _pushed_through(window, ref, level, near, up)
        if crossed is not None:
            d = (spot - level) / points
            ls.omit("levels.round_number", f"{minutes_ago(scene.now, bar_time(crossed) + ONE_MINUTE)} price {'rose' if up else 'fell'} "
                                           f"through a round {_round_kind(level)}-point level and is back {above_or_below(d)} it by {sig(abs(d))}: "
                                           f"neither a push through that held nor a level pressed without one", ended=True)
            return
    level = min((below_spot, above_spot, below_spot + step), key=lambda lv: abs(spot - lv))
    d = (spot - level) / points
    if abs(d) > ROUND_NEAR_SIGMA:
        ls.omit("levels.round_number", f"no round level in play: the nearest is {sig(abs(d))} {'below' if d >= 0 else 'above'} price, "
                                       f"beyond the {sig(ROUND_NEAR_SIGMA)} near distance, and none was pushed through in the last "
                                       f"{CROSS_LOOKBACK_MIN} minutes", ended=True)
        return
    r = (ref - level) / points
    side = above_or_below(d)
    ls.put("levels.round_number", ruled(
           anchor,
           f"price sits {sig(abs(d))} {side} a round {_round_kind(level)}-point level, within the {sig(ROUND_NEAR_SIGMA)} near distance, "
           f"pressing it from {side} without a push through in the last {CROSS_LOOKBACK_MIN} minutes; {back} it was "
           f"{sig(abs(r))} {above_or_below(r)} it"))


def _pushed_through(window: list[dict], ref: float, level: float, near: float, up: bool) -> dict | None:
    """The window's first bar that closed through ``level`` (upward when ``up``), when price started the
    window at least ``near`` points short of it; None otherwise."""
    if (ref > level - near) if up else (ref < level + near):
        return None
    return next((b for b in window if (float(b["close"]) > level if up else float(b["close"]) < level)), None)


def _open_vs_prior_range(scene: Scene, anchor: SigmaRuler, yesterday: list[dict], ls: LabelSet) -> None:
    so = settled_open(scene.bars)
    if so is None:
        ls.omit("levels.open_vs_prior_range", "no settled open yet: the 09:34 bar has not finished")
        return
    hi, lo, _ = high_low_close(yesterday)
    points, buffer = anchor.points, sig(OUTSIDE_BUFFER_SIGMA)
    width = f"yesterday's range was {sig((hi - lo) / points)}"
    up, down = (so - hi) / points, (lo - so) / points
    if up > OUTSIDE_BUFFER_SIGMA:
        text = f"the settled open is {sig(up)} above yesterday's high of the day, past the {buffer} buffer; {width}"
    elif down > OUTSIDE_BUFFER_SIGMA:
        text = f"the settled open is {sig(down)} below yesterday's low of the day, past the {buffer} buffer; {width}"
    elif up > 0:
        text = (f"the settled open is {sig(up)} above yesterday's high of the day, within the {buffer} buffer, so it counts as the "
                f"upper half of yesterday's range; {width}")
    elif down > 0:
        text = (f"the settled open is {sig(down)} below yesterday's low of the day, within the {buffer} buffer, so it counts as the "
                f"lower half of yesterday's range; {width}")
    elif so >= (hi + lo) / 2:
        text = f"the settled open is inside yesterday's range, in its upper half, {sig(-up)} below yesterday's high of the day; {width}"
    else:
        text = f"the settled open is inside yesterday's range, in its lower half, {sig(-down)} above yesterday's low of the day; {width}"
    ls.put("levels.open_vs_prior_range", ruled(anchor, text))


def _prior_day(scene: Scene, anchor: SigmaRuler, yesterday: list[dict], ls: LabelSet) -> None:
    """Price against yesterday's high and low: beyond one now (for how many minutes it has stayed beyond
    since it last closed back inside), back inside after trading beyond one, or inside near or away from
    both."""
    if not scene.bars:
        ls.omit("levels.prior_day", "no finished bars yet")
        return
    hi, lo, _ = high_low_close(yesterday)
    points, spot, reach = anchor.points, scene.spot, sig(LEVEL_REACH_SIGMA)
    if spot > hi or spot < lo:
        up = spot > hi
        edge, name, side = (hi, "yesterday's high", "above") if up else (lo, "yesterday's low", "below")
        inside = [i for i, b in enumerate(scene.bars) if not (float(b["close"]) > edge if up else float(b["close"]) < edge)]
        run = scene.bars[inside[-1] + 1:] if inside else scene.bars
        d = abs(spot - edge) / points
        where = f"it now sits {sig(d)} {side} it, {'beyond' if d > LEVEL_REACH_SIGMA else 'within'} the {reach} reach"
        if not run:
            since = "since price was last back inside" if len(inside) < len(scene.bars) else "yet"
            text = f"price is {side} {name} now and no finished minute has closed {side} it {since}; {where}"
        else:
            accepted = "at or past" if len(run) >= ACCEPT_MINUTES else "short of"
            text = (f"price crossed {side} {name} {minutes_ago(scene.now, bar_time(run[0]))} and has stayed {side} it for "
                    f"{plural(len(run), 'finished minute')}, {accepted} the {ACCEPT_MINUTES}-minute acceptance time; {where}")
        ls.put("levels.prior_day", ruled(anchor, text))
        return
    pokes = [(b, float(b["high"]) > hi) for b in scene.bars if float(b["high"]) > hi or float(b["low"]) < lo]
    to_hi, to_lo = (hi - spot) / points, (spot - lo) / points
    if pokes:
        bar, high_side = pokes[-1]
        name, d, side = ("yesterday's high", to_hi, "below") if high_side else ("yesterday's low", to_lo, "above")
        text = (f"price is back inside yesterday's range, {sig(d)} {side} {name}, after trading "
                f"{'above' if high_side else 'below'} it {minutes_ago(scene.now, bar_time(bar) + ONE_MINUTE)}")
    elif min(to_hi, to_lo) <= LEVEL_REACH_SIGMA:
        name, d, side = ("yesterday's high", to_hi, "below") if to_hi <= to_lo else ("yesterday's low", to_lo, "above")
        text = (f"price is inside yesterday's range, {sig(d)} {side} {name}, within the {reach} reach, and has not traded "
                f"{'above' if side == 'below' else 'below'} it today")
    else:
        text = (f"price is inside yesterday's range, {sig(to_hi)} below its high and {sig(to_lo)} above its low, beyond the {reach} "
                f"reach of both, and has not traded outside it today")
    ls.put("levels.prior_day", ruled(anchor, text))


def value_area(bars: list[dict]) -> tuple[float, float, float] | None:
    """The low and high of the band holding VALUE_AREA_SHARE of a session's minutes and its busiest level
    (the point of control), from each bar's minute spread evenly over its high-low range; None with no
    range. The band grows from the busiest bin toward the busier neighbour, the market-profile rule."""
    time_at: dict[int, float] = {}
    for b in bars:
        low, high = float(b["low"]), float(b["high"])
        first, last = math.floor(low / PROFILE_BIN_POINTS), math.floor(high / PROFILE_BIN_POINTS)
        for k in range(first, last + 1):
            if high > low:
                overlap = min(high, (k + 1) * PROFILE_BIN_POINTS) - max(low, k * PROFILE_BIN_POINTS)
                share = overlap / (high - low)
            else:
                share = 1.0
            if share > 0:
                time_at[k] = time_at.get(k, 0.0) + share
    if len(time_at) < 2:
        return None
    total, bottom, top = sum(time_at.values()), min(time_at), max(time_at)
    busiest = max(sorted(time_at), key=lambda k: time_at[k])
    lo_k = hi_k = busiest
    held = time_at[busiest]
    while held < VALUE_AREA_SHARE * total:
        below = time_at.get(lo_k - 1, 0.0) if lo_k > bottom else -1.0     # -1: no minute lies further that way
        above = time_at.get(hi_k + 1, 0.0) if hi_k < top else -1.0
        if above >= below:
            hi_k += 1
            held += above
        else:
            lo_k -= 1
            held += below
    return lo_k * PROFILE_BIN_POINTS, (hi_k + 1) * PROFILE_BIN_POINTS, (busiest + 0.5) * PROFILE_BIN_POINTS


def _prior_value(scene: Scene, anchor: SigmaRuler, yesterday: list[dict], ls: LabelSet) -> None:
    area = value_area(yesterday)
    if area is None:
        ls.omit("levels.prior_value", "yesterday's bars have no range to profile")
        return
    low, high, busiest = area
    points, spot, edge = anchor.points, scene.spot, sig(VALUE_EDGE_SIGMA)
    band = f"yesterday's value area (the band where SPX spent {pct(VALUE_AREA_SHARE)} of yesterday's session, {sig((high - low) / points)} wide)"
    over, under = (spot - high) / points, (low - spot) / points
    if over > 0:
        where = f"price is {sig(over)} above the high of {band}, {'beyond' if over > VALUE_EDGE_SIGMA else 'within'} the {edge} edge distance"
    elif under > 0:
        where = f"price is {sig(under)} below the low of {band}, {'beyond' if under > VALUE_EDGE_SIGMA else 'within'} the {edge} edge distance"
    else:
        where = f"price is inside {band}, {sig(-under)} above its low and {sig(-over)} below its high"
    d = (busiest - spot) / points
    ls.put("levels.prior_value", ruled(anchor, f"{where}; yesterday's busiest level is {sig(abs(d))} {above_or_below(d)} price"))
