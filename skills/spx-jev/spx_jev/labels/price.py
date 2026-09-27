"""The price family: where price is and how it has moved (price.*), and the momentum reads of the last
half hour (momentum.*).

The final question set's labels are measured on the morning anchor (rulers.sigma_anchor); the ones built
before it keep the row's sigma. Each set label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``."""
from __future__ import annotations

import statistics
from datetime import datetime, time, timedelta

from ..cuts import (AFTERNOON_LEG_SIGMA, BOTTOM_FIFTH, CLOSE_PUSH_SIGMA, DAY_SIDE_SIGMA, GAP_HALF_SHARE, MINUTE_WIDTH_CUT, MOVE_BURST_SHARE,
                    MOVE_RULE_SIGMA, MOVE_STRONG_SIGMA, ONE_WAY_CROSSES, ONE_WAY_HOUR_SIGMA, OPEN_DAY_MOVE_SIGMA, PACE_BIGGER,
                    PACE_SMALLER, PATH_CHOPPY, PATH_ORDERLY, PAUSE_BRIEF_MIN, PAUSE_LONG_MIN, PULLBACK_SHARE, RANGE_BOTTOM_SHARE,
                    RANGE_TOP_SHARE, RSI_OVERBOUGHT, RSI_OVERSOLD, TOP_FIFTH, VR_PIN, VR_TREND, WINDOW_30_MIN, WINDOW_60_MIN)
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import (ET, HOUR_MIN_BARS, MIN_RANGE_SESSIONS, ONE_MINUTE, RANGE_PRIOR_SESSIONS, RSI_PERIOD, bar_time, bars_finished_between, close_at,
                       day_high_low, high_low_close, is_num, session_extremes, settled_open, wilder_rsi)
from .ranks import rank_against, same_clock_values
from .rulers import NO_ANCHOR, SigmaRuler, ruled, sigma_anchor, typical_move
from .words import minutes_ago, pct, plural, sig, signed, third

LABELS = ("price.recent_move", "price.day_range_position", "price.vs_vwap", "price.vs_prior_close", "price.minute_width",
          "price.vs_yesterday", "price.vs_day_before", "price.multi_day_position",
          "momentum.rsi_1min", "momentum.rsi_5min", "momentum.pace", "momentum.closes", "momentum.pauses", "momentum.path_efficiency",
          "price.afternoon_leg", "price.day_character", "price.day_move", "price.day_move_split", "price.hour_one_way", "price.move_shape",
          "price.prior_close_push", "price.session_extreme_recent", "price.vwap_reach")
GATES = ("prior_close_push_fade", "average_reach_30")
DARK: dict[str, str] = {}

# The set's labels measured on the morning anchor, omitted together (their gates asleep) when the day has none.
ANCHORED = ("price.recent_move", "price.vs_vwap", "price.vs_prior_close", "price.afternoon_leg", "price.day_move", "price.day_move_split",
            "price.hour_one_way", "price.move_shape", "price.prior_close_push", "price.session_extreme_recent", "price.vwap_reach")
# Round one's stretch line for price against the day's average: a clause in the sentence, no question's threshold.
VWAP_STRETCH_SIGMA = 0.25
# A new session high or low this recent is news (round one's window for new_extreme_unconfirmed).
NEW_EXTREME_RECENT_MIN = 15
# The afternoon leg runs from the close of the 13:59 bar, the price at 14:00.
AFTERNOON_FROM_BAR = time(13, 59)
# The day's character: 30-minute returns against 5-minute returns since 10:00, once an hour of them exists.
CHARACTER_FROM = time(10, 0)
CHARACTER_STEP_MIN = 5
CHARACTER_MIN_STEPS = 12
# A 30-minute move is cut into six 5-minute chunks.
CHUNK_MIN = 5


def build_price_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _day_range_position(scene, ls)
    _day_character(scene, ls)
    _minute_width(scene, ls)
    _vs_prior_sessions(scene, ls)
    _multi_day_position(scene, ls)
    _momentum(scene, ls)
    anchor = sigma_anchor(scene)
    if anchor is None:
        for path in ANCHORED:
            ls.omit(path, NO_ANCHOR)
        for qid in GATES:
            ls.sleep(qid, NO_ANCHOR)
        return ls
    move30, no_move = _recent_move(scene, anchor, ls)
    _vs_vwap(scene, anchor, ls)
    _vs_prior_close(scene, anchor, ls)
    _afternoon_leg(scene, anchor, move30, ls)
    _day_move(scene, anchor, ls)
    _day_move_split(scene, anchor, ls)
    _hour_one_way(scene, anchor, ls)
    _move_shape(scene, anchor, move30, no_move, ls)
    _prior_close_push(scene, anchor, move30, no_move, ls)
    _session_extreme_recent(scene, anchor, ls)
    _vwap_reach(scene, anchor, ls)
    return ls


def _move_30(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
    """The size of the 30-minute move to ``then`` in sigma, for the same-clock rank."""
    ref, last = close_at(bars, then - timedelta(minutes=WINDOW_30_MIN)), close_at(bars, then)
    return abs(last - ref) / sigma if sigma and ref is not None and last is not None else None


def _recent_move(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> tuple[float | None, str | None]:
    """The 30-minute move in sigma, written as price.recent_move and returned for the reads that judge it; None
    with the reason it was omitted when it was. On the live lane spot is the row's while the bars can stop, so
    the move needs a bar finished in the minute before the window and 20 in it, or it would be measured from
    wherever the tape stopped."""
    back = scene.now - timedelta(minutes=WINDOW_30_MIN)
    ref, win = close_at(scene.bars, back), bars_finished_between(scene.bars, back, scene.now)
    if scene.minutes_since_open < WINDOW_30_MIN:
        why = "needs 30 minutes of session"
    elif ref is None:
        why = "no finished bar 30 minutes ago"
    elif not bars_finished_between(scene.bars, back - ONE_MINUTE, back):
        why = "the bars have stopped: no bar finished in the minute before the last 30 minutes"
    elif len(win) < 20:
        why = f"needs 20 finished bars in the last 30 minutes, have {len(win)}"
    else:
        why = None
    if why is not None:
        ls.omit("price.recent_move", why)
        return None, why
    d = (scene.spot - ref) / anchor.points
    verdict = "going_nowhere" if abs(d) <= MOVE_RULE_SIGMA else "rising" if d > 0 else "falling"
    fig = {"kind": "signed", "value": round(d, 3), "band": MOVE_RULE_SIGMA, "strong": MOVE_STRONG_SIGMA, "unit": "sigma", "verdict": verdict}
    verb = "rose" if d > 0 else "fell"
    if verdict == "going_nowhere":
        text = f"over the last 30 minutes price stayed within {MOVE_RULE_SIGMA} sigma of where it was, moving {signed(d)} sigma"
    elif abs(d) > MOVE_STRONG_SIGMA:
        text = f"over the last 30 minutes price {verb} {sig(abs(d))}, past the {MOVE_STRONG_SIGMA:.2f} sigma strong line"
    else:
        text = (f"over the last 30 minutes price {verb} {sig(abs(d))}, more than the {MOVE_RULE_SIGMA} sigma move rule "
                f"and short of the {MOVE_STRONG_SIGMA:.2f} sigma strong line")
    rank = rank_against(abs(d), same_clock_values(scene, _move_30))
    if rank is not None:
        text += f"; bigger than {rank.higher_than} of the last {rank.of} sessions in this half hour"
    hi, lo = day_high_low(win, scene.spot)
    if hi > lo:
        pos = (scene.spot - lo) / (hi - lo)
        where = "in the top fifth of" if pos >= TOP_FIFTH else "in the bottom fifth of" if pos <= BOTTOM_FIFTH else "between the top and bottom fifths of"
        text += f"; the half hour closed {where} its own range"
    ls.put("price.recent_move", ruled(anchor, text), figure=fig)
    return d, None


def _day_range_position(scene: Scene, ls: LabelSet) -> None:
    if not scene.bars:
        ls.omit("price.day_range_position", "no finished bars yet")
        return
    hi, lo = day_high_low(scene.bars, scene.spot)
    if hi - lo <= 0:
        ls.omit("price.day_range_position", "today's range is zero so far")
        return
    pos = (scene.spot - lo) / (hi - lo)
    if pos >= RANGE_TOP_SHARE:
        band = f"in the top band of today's range so far (past the {pct(RANGE_TOP_SHARE)} line)"
    elif pos <= RANGE_BOTTOM_SHARE:
        band = f"in the bottom band of today's range so far (below the {pct(RANGE_BOTTOM_SHARE)} line)"
    else:
        band = f"between the bands of today's range so far (between the {pct(RANGE_BOTTOM_SHARE)} and {pct(RANGE_TOP_SHARE)} lines)"
    ls.put("price.day_range_position", f"price is {band}, {pct(pos)} of the way up")


def _vs_vwap(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    vwap = scene.row.get("vwap")
    if not is_num(vwap) or vwap <= 0:
        ls.omit("price.vs_vwap", "row carries no vwap")
        return
    vwap = float(vwap)
    d = (scene.spot - vwap) / anchor.points
    verdict = "at_it" if abs(d) < MOVE_RULE_SIGMA else "above" if d > 0 else "below"
    fig = {"kind": "signed", "value": round(d, 3), "band": MOVE_RULE_SIGMA, "unit": "sigma", "verdict": verdict}
    if verdict == "at_it":
        text = f"price is within {MOVE_RULE_SIGMA} sigma of the day's volume-weighted average price, {signed(d)} sigma from it"
    else:
        line = "past" if abs(d) >= VWAP_STRETCH_SIGMA else "short of"
        text = (f"price is {sig(abs(d))} {verdict} the day's volume-weighted average price, more than the {MOVE_RULE_SIGMA} sigma move rule, "
                f"{line} the {VWAP_STRETCH_SIGMA} sigma stretch line")
    touched = _last_at_average(scene)
    if touched is not None:
        text += f"; it last traded at the average {minutes_ago(scene.now, touched)}"
    else:
        text += "; it has not traded at the average today"
    ls.put("price.vs_vwap", ruled(anchor, text), figure=fig)


def _last_at_average(scene: Scene) -> datetime | None:
    """When the newest finished bar that spanned the day's average finished. The average drifts through the
    day, so each bar is judged against the one the diary carried when it finished (the newest row stamped by
    then), never against this row's."""
    known = [(datetime.fromisoformat(r["ts"]), float(r["vwap"])) for r in scene.rows_today if is_num(r.get("vwap")) and r["vwap"] > 0]
    last, i, vwap = None, 0, None
    for b in scene.bars:
        done = bar_time(b) + ONE_MINUTE
        while i < len(known) and known[i][0] <= done:
            vwap = known[i][1]
            i += 1
        if vwap is not None and float(b["low"]) <= vwap <= float(b["high"]):
            last = done
    return last


def _vs_prior_close(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0:
        ls.omit("price.vs_prior_close", "row carries no prior close")
        return
    d = (scene.spot - float(pc)) / anchor.points
    rule = f"more than the {DAY_SIDE_SIGMA:.2f} sigma day-side rule" if abs(d) > DAY_SIDE_SIGMA else f"within the {DAY_SIDE_SIGMA:.2f} sigma day-side rule"
    ls.put("price.vs_prior_close", ruled(anchor, f"price is {sig(abs(d))} {'above' if d >= 0 else 'below'} yesterday's close, {rule}"))


def _minute_width(scene: Scene, ls: LabelSet) -> None:
    bars = scene.bars
    if len(bars) < 15:
        ls.omit("price.minute_width", "needs 15 finished bars")
        return
    last = float(bars[-1]["high"]) - float(bars[-1]["low"])
    med = statistics.median(float(x["high"]) - float(x["low"]) for x in bars)
    if med <= 0:
        ls.omit("price.minute_width", "today's typical minute has no range")
        return
    r = last / med
    ls.put("price.minute_width", f"this minute's range is {r:.1f} times a typical minute today, {'wider than' if r > MINUTE_WIDTH_CUT else 'within'} the {MINUTE_WIDTH_CUT:g}-times cut")


def _vs_prior_sessions(scene: Scene, ls: LabelSet) -> None:
    sessions = list(scene.prior_bars.values())
    spot, sigma = scene.spot, scene.sigma
    for back, name, path in ((1, "yesterday", "price.vs_yesterday"), (2, "the session before yesterday", "price.vs_day_before")):
        if len(sessions) < back:
            ls.omit(path, f"no stored session {back} back")
            continue
        hi, lo, cl = high_low_close(sessions[back - 1])
        dh, dl, dc = (spot - hi) / sigma, (spot - lo) / sigma, (spot - cl) / sigma
        if dh > MOVE_RULE_SIGMA:
            pos = f"price is {sig(dh)} above {name}'s high, more than the {MOVE_RULE_SIGMA} sigma move rule"
        elif abs(dh) <= MOVE_RULE_SIGMA:
            pos = f"price is within {MOVE_RULE_SIGMA} sigma of {name}'s high, at it"
        elif dl < -MOVE_RULE_SIGMA:
            pos = f"price is {sig(-dl)} below {name}'s low, more than the {MOVE_RULE_SIGMA} sigma move rule"
        elif abs(dl) <= MOVE_RULE_SIGMA:
            pos = f"price is within {MOVE_RULE_SIGMA} sigma of {name}'s low, at it"
        else:
            pos = f"price is inside {name}'s range, {sig(-dh)} below its high and {sig(dl)} above its low"
        # yesterday's close already has its own label, price.vs_prior_close; only the day before carries its close here
        close = f"; it is {sig(abs(dc))} {'above' if dc >= 0 else 'below'} {name}'s close" if back == 2 else ""
        ls.put(path, pos + close)


def _multi_day_position(scene: Scene, ls: LabelSet) -> None:
    week = list(scene.prior_bars.values())[:RANGE_PRIOR_SESSIONS]
    if len(week) < MIN_RANGE_SESSIONS:
        ls.omit("price.multi_day_position", f"needs {MIN_RANGE_SESSIONS} prior sessions, have {len(week)}")
        return
    whi = max(max(float(x["high"]) for x in b) for b in week)
    wlo = min(min(float(x["low"]) for x in b) for b in week)
    n = len(week)
    if whi - wlo <= 0:
        ls.omit("price.multi_day_position", "the prior sessions' range is zero")
        return
    spot = scene.spot
    dh, dl = (spot - whi) / scene.sigma, (spot - wlo) / scene.sigma
    edges = f"{sig(abs(dh))} {'above' if dh > 0 else 'below'} that high and {sig(abs(dl))} {'above' if dl > 0 else 'below'} their low"
    if spot > whi:
        ls.put("price.multi_day_position", f"price is above the last {n} sessions' high, in new ground, {edges}")
    elif spot < wlo:
        ls.put("price.multi_day_position", f"price is below the last {n} sessions' low, in new ground, {edges}")
    else:
        pos = (spot - wlo) / (whi - wlo)
        ls.put("price.multi_day_position", f"price is in the {third(pos)} third of the last {n} sessions' range, {pct(pos)} of the way up from its low to its high, {edges}")


def _momentum(scene: Scene, ls: LabelSet) -> None:
    bars, now, sigma = scene.bars, scene.now, scene.sigma
    closes = [float(x["close"]) for x in bars]
    for path, series, name, need in (("momentum.rsi_1min", closes, "1-minute", f"{RSI_PERIOD + 1} finished bars"),
                                     ("momentum.rsi_5min", closes[4::5], "5-minute", f"{(RSI_PERIOD + 1) * 5} minutes of finished bars")):
        rsi = wilder_rsi(series)
        if rsi is None:
            ls.omit(path, f"needs {need}")
        elif rsi > RSI_OVERBOUGHT:
            ls.put(path, f"the {name} RSI is {rsi:.0f}, above {RSI_OVERBOUGHT}")
        elif rsi < RSI_OVERSOLD:
            ls.put(path, f"the {name} RSI is {rsi:.0f}, below {RSI_OVERSOLD}")
        else:
            ls.put(path, f"the {name} RSI is {rsi:.0f}, between {RSI_OVERSOLD} and {RSI_OVERBOUGHT}")

    c0 = closes[-1] if closes else None
    c10 = close_at(bars, now - timedelta(minutes=10))
    c20 = close_at(bars, now - timedelta(minutes=20))
    if scene.minutes_since_open < 20 or c0 is None or c10 is None or c20 is None:
        ls.omit("momentum.pace", "needs 20 minutes of finished bars")
    else:
        last, prev = abs(c0 - c10) / sigma, abs(c10 - c20) / sigma
        if prev <= 0 and last <= 0:
            ls.put("momentum.pace", "the last 10 minutes and the 10 minutes before both moved about the same, close to nothing")
        elif prev <= 0:
            ls.put("momentum.pace", f"the last 10 minutes moved {sig(last)} after the 10 minutes before moved close to nothing, so more than {PACE_BIGGER:g} times as far")
        else:
            ratio = last / prev
            word = (f"more than {PACE_BIGGER:g} times as far as" if ratio > PACE_BIGGER else
                    "less than two thirds as far as" if ratio < PACE_SMALLER else "between two thirds and 1.5 times as far as")
            ls.put("momentum.pace", f"the last 10 minutes moved {sig(last)}, {word} the 10 minutes before, which moved {sig(prev)}")

    judged = ("momentum.closes", "momentum.pauses", "momentum.path_efficiency")
    win = bars_finished_between(bars, now - timedelta(minutes=30), now)
    start = close_at(bars, now - timedelta(minutes=30))
    if len(win) < 20 or start is None:
        for path in judged:
            ls.omit(path, "needs 30 minutes of finished bars")
        return
    # built before the set, so the half hour's move is judged on the row's sigma, not price.recent_move's anchor
    move30 = (scene.spot - start) / sigma
    if abs(move30) < MOVE_RULE_SIGMA:
        # a quiet read is a fact, not a gap: the questions that read these labels have a
        # "no move" answer, and leaving the labels out would skip those questions instead
        quiet = f"the last 30 minutes moved {sig(abs(move30))}, under the {MOVE_RULE_SIGMA} sigma move rule, so there is no move to judge"
        for path in judged:
            ls.put(path, quiet)
        return
    up = move30 > 0
    word = "up" if up else "down"
    agree = sum(1 for x in bars[-5:] if (float(x["close"]) > float(x["open"])) == up and float(x["close"]) != float(x["open"]))
    ls.put("momentum.closes", f"of the last five 1-minute bars, {agree} closed in the direction of the move, which is {word}")

    ext = start                 # the move's running extreme
    last_ext = -1               # index of the bar that last pushed it
    longest_stall = 0
    deepest = 0.0               # the deepest dip from the running extreme, in points
    for i, x in enumerate(win):
        hi, lo = float(x["high"]), float(x["low"])
        # a dip is measured from the extreme reached before this bar
        deepest = max(deepest, (ext - lo) if up else (hi - ext))
        if up and hi > ext:
            ext, last_ext = hi, i
        elif (not up) and lo < ext:
            ext, last_ext = lo, i
        longest_stall = max(longest_stall, i - last_ext if last_ext >= 0 else i + 1)
    # the pullback is that dip as a share of the move's full extent over the window,
    # never of the distance travelled so far, which is tiny early on and inflates a wobble
    extent = abs(ext - start)
    retrace = deepest / extent if extent > 0 else 0.0
    if retrace > 1.0:
        ls.put("momentum.pauses", f"during the last 30 minutes the move {word} gave back more than all of itself at its deepest pullback, far more than a quarter")
    elif retrace >= PULLBACK_SHARE:
        ls.put("momentum.pauses", f"during the last 30 minutes the move {word} gave back {pct(retrace)} of itself at its deepest pullback, more than a quarter")
    elif longest_stall > PAUSE_LONG_MIN:
        ls.put("momentum.pauses", f"during the last 30 minutes the move {word} paused for {plural(longest_stall, 'minute')} without a new extreme, longer than {PAUSE_LONG_MIN} minutes, and gave back {pct(retrace)} of itself, less than a quarter")
    elif longest_stall >= PAUSE_BRIEF_MIN:
        ls.put("momentum.pauses", f"during the last 30 minutes the move {word} paused for {plural(longest_stall, 'minute')}, between {PAUSE_BRIEF_MIN} and {PAUSE_LONG_MIN} minutes, and gave back {pct(retrace)} of itself, less than a quarter")
    else:
        ls.put("momentum.pauses", f"during the last 30 minutes the move {word} ran without a pause longer than {PAUSE_BRIEF_MIN} minutes and gave back {pct(retrace)} of itself, less than a quarter")

    wcloses = [float(x["close"]) for x in win]
    net = abs(wcloses[-1] - wcloses[0])
    travel = sum(abs(b - a) for a, b in zip(wcloses[:-1], wcloses[1:]))
    if net <= 0 or travel <= 0:
        ls.omit("momentum.path_efficiency", "the window's closes netted nothing, so there is no path to judge")
        return
    eff = net / travel
    lead = f"over the last 30 minutes price travelled {travel / net:.1f} times its net move, path efficiency {pct(eff)}"
    if eff >= PATH_ORDERLY:
        ls.put("momentum.path_efficiency", f"{lead}, orderly ({pct(PATH_ORDERLY)} or more)")
    elif eff >= PATH_CHOPPY:
        ls.put("momentum.path_efficiency", f"{lead}, mixed (between {pct(PATH_CHOPPY)} and {pct(PATH_ORDERLY)})")
    else:
        ls.put("momentum.path_efficiency", f"{lead}, choppy (under {pct(PATH_CHOPPY)})")


def _went(d: float) -> str:
    return f"rose {sig(d)}" if d > 0 else f"fell {sig(-d)}" if d < 0 else "did not move"


def _afternoon_leg(scene: Scene, anchor: SigmaRuler, move30: float | None, ls: LabelSet) -> None:
    start = next((float(b["close"]) for b in scene.bars if bar_time(b).astimezone(ET).time() == AFTERNOON_FROM_BAR), None)
    if start is None:
        ls.omit("price.afternoon_leg", f"no finished {AFTERNOON_FROM_BAR:%H:%M} bar: the afternoon leg starts at 14:00")
        return
    d = (scene.spot - start) / anchor.points
    if abs(d) > AFTERNOON_LEG_SIGMA:
        text = f"since 14:00 price has {'risen' if d > 0 else 'fallen'} {sig(abs(d))}, more than the {AFTERNOON_LEG_SIGMA} sigma afternoon-leg rule"
    else:
        text = f"since 14:00 price has stayed within the {AFTERNOON_LEG_SIGMA} sigma afternoon-leg rule, moving {signed(d)} sigma"
    context = []
    pc = scene.row.get("prior_close")
    if is_num(pc) and pc > 0:
        day = (scene.spot - float(pc)) / anchor.points
        context.append(f"the day is {sig(abs(day))} {'above' if day >= 0 else 'below'} yesterday's close")
    hi, lo = day_high_low(scene.bars, scene.spot)
    if hi > lo:
        context.append(f"price sits at {pct((scene.spot - lo) / (hi - lo))} of today's range")
    if context:
        text += "; " + " and ".join(context)
    if move30 is not None:
        text += f"; the last 30 minutes {_went(move30)}"
    ls.put("price.afternoon_leg", ruled(anchor, text))


def _day_character(scene: Scene, ls: LabelSet) -> None:
    """The variance ratio of the day's overlapping 30-minute returns against its 5-minute returns since 10:00:
    over one, moves have built on each other; under one, they have cancelled. Lo and MacKinlay's bias-corrected
    form, one drift for both and unbiased variances, so a random walk reads one at any hour rather than well
    under it on a morning's dozen returns."""
    t = datetime.combine(scene.now.astimezone(ET).date(), CHARACTER_FROM, tzinfo=ET)
    marks = []
    while t <= scene.now:
        marks.append(close_at(scene.bars, t))
        t += timedelta(minutes=CHARACTER_STEP_MIN)
    closes = [c for c in marks if c is not None]
    steps = WINDOW_30_MIN // CHARACTER_STEP_MIN
    if len(closes) - 1 < CHARACTER_MIN_STEPS:
        ls.omit("price.day_character", f"needs {CHARACTER_MIN_STEPS} 5-minute returns since 10:00, have {max(len(closes) - 1, 0)}")
        return
    returns = [b - a for a, b in zip(closes, closes[1:])]
    n, drift = len(returns), statistics.fmean(returns)
    short = statistics.variance(returns, drift)
    if short <= 0:
        ls.omit("price.day_character", "the 5-minute returns since 10:00 have not varied")
        return
    overlapping = sum((closes[i + steps] - closes[i] - steps * drift) ** 2 for i in range(n - steps + 1))
    ratio = overlapping / (steps * (n - steps + 1) * (1 - steps / n)) / short
    lead = f"so far today, 30-minute stretches have been {ratio:.2f} times"
    if ratio > VR_TREND:
        ls.put("price.day_character", f"{lead} larger than their 5-minute pieces would suggest, above the {VR_TREND:.2f} building line")
    elif ratio < VR_PIN:
        ls.put("price.day_character", f"{lead} as large as their 5-minute pieces would suggest, below the {VR_PIN:.2f} cancelling line")
    else:
        ls.put("price.day_character", f"{lead} what their 5-minute pieces would suggest, between the {VR_PIN:.2f} cancelling line "
                                      f"and the {VR_TREND:.2f} building line")


def _day_move(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    opened = settled_open(scene.bars)
    if opened is None:
        ls.omit("price.day_move", "no settled open yet: the 09:34 bar has not finished")
        return
    d = (scene.spot - opened) / anchor.points
    lead = f"since the settled 09:35 open SPX has {'risen' if d > 0 else 'fallen'} {sig(abs(d))}"
    if abs(d) > OPEN_DAY_MOVE_SIGMA:
        rules = f"more than the {MOVE_RULE_SIGMA} sigma move rule and past the {OPEN_DAY_MOVE_SIGMA:.2f} sigma open-day line"
    elif abs(d) > MOVE_RULE_SIGMA:
        rules = f"more than the {MOVE_RULE_SIGMA} sigma move rule and within the {OPEN_DAY_MOVE_SIGMA:.2f} sigma open-day line"
    else:
        lead = f"since the settled 09:35 open SPX has moved {signed(d)} sigma"
        rules = f"within the {MOVE_RULE_SIGMA} sigma move rule and the {OPEN_DAY_MOVE_SIGMA:.2f} sigma open-day line"
    ls.put("price.day_move", ruled(anchor, f"{lead}, {rules}"))


def _day_move_split(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    pc, opened = scene.row.get("prior_close"), settled_open(scene.bars)
    if not is_num(pc) or pc <= 0:
        ls.omit("price.day_move_split", "row carries no prior close")
        return
    if opened is None:
        ls.omit("price.day_move_split", "no settled open yet: the 09:34 bar has not finished")
        return
    a = anchor.points
    day, gap, since = (scene.spot - float(pc)) / a, (opened - float(pc)) / a, (scene.spot - opened) / a
    side = "above" if day >= 0 else "below"
    rule = "more than" if abs(day) > DAY_SIDE_SIGMA else "within"
    text = (f"SPX is {sig(abs(day))} {side} yesterday's close, {rule} the {DAY_SIDE_SIGMA:.2f} sigma day-side rule: "
            f"{sig(abs(gap))} {'up' if gap >= 0 else 'down'} came from the opening gap and "
            f"{sig(abs(since))} {'up' if since >= 0 else 'down'} from trading since the open")
    if abs(day) > DAY_SIDE_SIGMA:
        share = gap / day
        if share >= GAP_HALF_SHARE:
            text += f", so the gap is {pct(share)} of the day's move, at least half: most of it came from the gap"
        elif share > 0:
            text += f", so the gap is {pct(share)} of the day's move, under half: most of it was built since the open"
        else:
            text += ", so the gap ran against the day's move: all of it was built since the open"
    ls.put("price.day_move_split", ruled(anchor, text))


def _hour_one_way(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    back = scene.now - timedelta(minutes=WINDOW_60_MIN)
    ref = close_at(scene.bars, back)
    win = bars_finished_between(scene.bars, back, scene.now)
    if ref is None or len(win) < HOUR_MIN_BARS:
        ls.omit("price.hour_one_way", f"needs {HOUR_MIN_BARS} finished bars in the last {WINDOW_60_MIN} minutes and one before them")
        return
    d = (scene.spot - ref) / anchor.points
    closes = [float(b["close"]) for b in win]
    mean = statistics.fmean(closes)
    sides = [c > mean for c in closes if c != mean]
    crosses = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
    size = "past" if abs(d) >= ONE_WAY_HOUR_SIGMA else "short of"
    count = "fewer than" if crosses < ONE_WAY_CROSSES else "at or past"
    ls.put("price.hour_one_way", ruled(anchor, f"over the last {WINDOW_60_MIN} minutes price {_went(d)} and crossed its own hourly average "
                                               f"{plural(crosses, 'time')}, {count} the {ONE_WAY_CROSSES}-cross one-way rule, "
                                               f"{size} the {ONE_WAY_HOUR_SIGMA:.2f} sigma one-way size"))


def _move_shape(scene: Scene, anchor: SigmaRuler, move30: float | None, no_move: str | None, ls: LabelSet) -> None:
    """How the half hour's move was made: the largest of its six 5-minute chunks as a share of the net move,
    and its path efficiency, the net move over the distance the minute closes travelled."""
    if move30 is None:
        ls.omit("price.move_shape", f"no 30-minute move to shape: {no_move}")
        return
    if abs(move30) <= MOVE_RULE_SIGMA:
        ls.put("price.move_shape", ruled(anchor, f"the last half hour moved {signed(move30)} sigma, within the {MOVE_RULE_SIGMA} sigma move rule: "
                                                 f"no move to shape"))
        return
    back = scene.now - timedelta(minutes=WINDOW_30_MIN)
    marks = [close_at(scene.bars, back + timedelta(minutes=CHUNK_MIN * k)) for k in range(WINDOW_30_MIN // CHUNK_MIN)] + [scene.spot]
    win = bars_finished_between(scene.bars, back, scene.now)
    if any(m is None for m in marks) or len(win) < 20:
        ls.omit("price.move_shape", "needs 30 minutes of finished bars")
        return
    net = scene.spot - marks[0]
    burst = max((b - a) / net for a, b in zip(marks, marks[1:]))
    path = [marks[0]] + [float(b["close"]) for b in win] + [scene.spot]
    efficiency = abs(net) / sum(abs(b - a) for a, b in zip(path, path[1:]))
    burst_line = "past" if burst >= MOVE_BURST_SHARE else "short of"
    choppy_line = "under" if efficiency < PATH_CHOPPY else "above"
    ls.put("price.move_shape", ruled(anchor, f"the last half hour {_went(move30)}; one 5-minute stretch made {pct(burst)} of it, {burst_line} the "
                                             f"{pct(MOVE_BURST_SHARE)} burst line; its net move was {efficiency:.2f} of the distance travelled, "
                                             f"{choppy_line} the {PATH_CHOPPY:.2f} choppy line"))


def _prior_close_push(scene: Scene, anchor: SigmaRuler, move30: float | None, no_move: str | None, ls: LabelSet) -> None:
    """The half hour's move against yesterday's close, where price sits from it, and whether the closes
    crossed it in the half hour and today; prior_close_push_fade is awake only on a push toward it past the
    move rule that ends within the push distance or crossed it."""
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0 or move30 is None:
        why = "row carries no prior close" if not is_num(pc) or pc <= 0 else f"no 30-minute move to judge: {no_move}"
        ls.omit("price.prior_close_push", why)
        ls.sleep("prior_close_push_fade", why)
        return
    pc, a = float(pc), anchor.points
    back = scene.now - timedelta(minutes=WINDOW_30_MIN)
    ref = close_at(scene.bars, back)
    toward = (pc - ref) * move30 > 0
    dist = (scene.spot - pc) / a
    started_below = ref < pc
    half_hour = [ref] + [float(b["close"]) for b in bars_finished_between(scene.bars, back, scene.now)] + [scene.spot]
    today = [float(scene.bars[0]["open"])] + [float(b["close"]) for b in scene.bars] + [scene.spot]
    crossed_now = any((x < pc) != (y < pc) for x, y in zip(half_hour, half_hour[1:]))
    crossed_today = any((x < pc) != (y < pc) for x, y in zip(today, today[1:]))
    rule = "past" if abs(move30) > MOVE_RULE_SIGMA else "within"
    inside = "inside" if abs(dist) <= CLOSE_PUSH_SIGMA else "beyond"
    text = (f"over the last 30 minutes price {_went(move30)} {'toward' if toward else 'away from'} yesterday's close, {rule} the "
            f"{MOVE_RULE_SIGMA} sigma move rule; it now sits {sig(abs(dist))} {'above' if dist >= 0 else 'below'} it, {inside} the "
            f"{CLOSE_PUSH_SIGMA} sigma push distance")
    if crossed_now:
        through = (scene.spot < pc) != started_below
        text += f"; it crossed it in the last 30 minutes and is {'still through it' if through else 'back on its starting side'}"
    else:
        text += "; it has not crossed it in the last 30 minutes" + (", though it did earlier today" if crossed_today else " or today")
    ls.put("price.prior_close_push", ruled(anchor, text))
    if not (toward and abs(move30) > MOVE_RULE_SIGMA):
        ls.sleep("prior_close_push_fade", f"the last 30 minutes did not move toward yesterday's close past the {MOVE_RULE_SIGMA} sigma move rule")
    elif abs(dist) > CLOSE_PUSH_SIGMA and not crossed_now:
        ls.sleep("prior_close_push_fade", f"price is beyond {CLOSE_PUSH_SIGMA} sigma of yesterday's close and has not crossed it in the last 30 minutes")
    else:
        ls.wake("prior_close_push_fade")


def _session_extreme_recent(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    cut = scene.now - timedelta(minutes=NEW_EXTREME_RECENT_MIN)
    before = session_extremes([b for b in scene.bars if bar_time(b) + ONE_MINUTE <= cut])
    today = session_extremes(scene.bars)
    if before is None or today is None:
        ls.omit("price.session_extreme_recent", f"needs bars from before the last {NEW_EXTREME_RECENT_MIN} minutes")
        return

    def news(kind: str, made: datetime, level: float, earlier: float, earlier_at: datetime) -> str:
        return (f"SPX set a new session {kind} {minutes_ago(scene.now, made)}, {sig(abs(level - earlier) / anchor.points)} "
                f"{'above' if kind == 'high' else 'below'} the earlier {kind} from {(earlier_at - ONE_MINUTE).astimezone(ET):%H:%M}")

    found = []
    if today.high > before.high:
        found.append((today.high_at, news("high", today.high_at, today.high, before.high, before.high_at)))
    if today.low < before.low:
        found.append((today.low_at, news("low", today.low_at, today.low, before.low, before.low_at)))
    if not found:
        ls.omit("price.session_extreme_recent", f"no new session high or low in the last {NEW_EXTREME_RECENT_MIN} minutes")
        return
    ls.put("price.session_extreme_recent", ruled(anchor, "; ".join(words for _, words in sorted(found, reverse=True))))


def _vwap_reach(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    """How far the day's average is, in sigma and in typical 30-minute moves; average_reach_30 is awake
    only while price is more than the move rule from it."""
    vwap = scene.row.get("vwap")
    if not is_num(vwap) or vwap <= 0:
        ls.omit("price.vwap_reach", "row carries no vwap")
        ls.sleep("average_reach_30", "row carries no vwap")
        return
    d = (float(vwap) - scene.spot) / anchor.points
    if abs(d) <= MOVE_RULE_SIGMA:
        ls.sleep("average_reach_30", f"price is within {MOVE_RULE_SIGMA} sigma of the day's volume-weighted average price")
    else:
        ls.wake("average_reach_30")
    reach, how = typical_move(scene, anchor, WINDOW_30_MIN)
    if reach is None:
        ls.omit("price.vwap_reach", how)
        return
    rule = "past" if abs(d) > MOVE_RULE_SIGMA else "within"
    ls.put("price.vwap_reach", ruled(anchor, f"the day's volume-weighted average price is {sig(abs(d))} {'above' if d >= 0 else 'below'} price, "
                                             f"{rule} the {MOVE_RULE_SIGMA} sigma move rule, {abs(d) * anchor.points / reach:.1f} typical "
                                             f"30-minute moves away"))
