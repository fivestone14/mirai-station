"""The price family: where price is and how it has moved (price.*), and the momentum reads of the last
half hour (momentum.*).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

import statistics
from datetime import timedelta

from ..cuts import (MINUTE_WIDTH_CUT, MOVE_RULE_SIGMA, OUTLIER_DAY_SIGMA, PACE_BIGGER, PACE_SMALLER, PATH_CHOPPY, PATH_ORDERLY,
                    PAUSE_BRIEF_MIN, PAUSE_LONG_MIN, PULLBACK_SHARE, RSI_OVERBOUGHT, RSI_OVERSOLD)
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import MIN_RANGE_SESSIONS, RANGE_PRIOR_SESSIONS, RSI_PERIOD, bars_finished_between, close_at, day_high_low, high_low_close, is_num, wilder_rsi
from .words import pct, plural, sig, signed, third

LABELS = ("price.recent_move", "price.day_range_position", "price.vs_vwap", "price.vs_prior_close", "price.minute_width",
          "price.vs_yesterday", "price.vs_day_before", "price.multi_day_position",
          "momentum.rsi_1min", "momentum.rsi_5min", "momentum.pace", "momentum.closes", "momentum.pauses", "momentum.path_efficiency",
          "price.afternoon_leg", "price.day_character", "price.day_move", "price.day_move_split", "price.hour_one_way", "price.move_shape",
          "price.prior_close_push", "price.session_extreme_recent", "price.vwap_reach")
GATES = ("prior_close_push_fade", "average_reach_30")
DARK: dict[str, str] = {}



def build_price_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    move30 = _recent_move(scene, ls)
    _day_range_position(scene, ls)
    _vs_vwap(scene, ls)
    _vs_prior_close(scene, ls)
    _minute_width(scene, ls)
    _vs_prior_sessions(scene, ls)
    _multi_day_position(scene, ls)
    _momentum(scene, ls, move30)
    return ls


def _recent_move(scene: Scene, ls: LabelSet) -> float | None:
    """The 30-minute move in sigma, written as price.recent_move and returned for the momentum reads."""
    if scene.minutes_since_open < 30:
        ls.omit("price.recent_move", "needs 30 minutes of session")
        return None
    ref = close_at(scene.bars, scene.now - timedelta(minutes=30))
    if ref is None:
        ls.omit("price.recent_move", "no finished bar 30 minutes ago")
        return None
    d = (scene.spot - ref) / scene.sigma
    verdict = "going_nowhere" if abs(d) < MOVE_RULE_SIGMA else "rising" if d > 0 else "falling"
    fig = {"kind": "signed", "value": round(d, 3), "band": MOVE_RULE_SIGMA, "unit": "sigma", "verdict": verdict}
    if verdict == "going_nowhere":
        text = f"over the last 30 minutes price stayed within {MOVE_RULE_SIGMA} sigma of where it was, moving {signed(d)} sigma"
    else:
        text = f"over the last 30 minutes price {'rose' if d > 0 else 'fell'} {sig(abs(d))}, more than the {MOVE_RULE_SIGMA} sigma move rule"
    ls.put("price.recent_move", text, figure=fig)
    return d


def _day_range_position(scene: Scene, ls: LabelSet) -> None:
    if not scene.bars:
        ls.omit("price.day_range_position", "no finished bars yet")
        return
    hi, lo = day_high_low(scene.bars, scene.spot)
    if hi - lo <= 0:
        ls.omit("price.day_range_position", "today's range is zero so far")
        return
    pos = (scene.spot - lo) / (hi - lo)
    ls.put("price.day_range_position", f"price is in the {third(pos)} third of today's range so far, {pct(pos)} of the way up from the low to the high")


def _vs_vwap(scene: Scene, ls: LabelSet) -> None:
    vwap = scene.row.get("vwap")
    if not is_num(vwap) or vwap <= 0:
        ls.omit("price.vs_vwap", "row carries no vwap")
        return
    d = (scene.spot - float(vwap)) / scene.sigma
    verdict = "at_it" if abs(d) < MOVE_RULE_SIGMA else "above" if d > 0 else "below"
    fig = {"kind": "signed", "value": round(d, 3), "band": MOVE_RULE_SIGMA, "unit": "sigma", "verdict": verdict}
    if verdict == "at_it":
        text = f"price is within {MOVE_RULE_SIGMA} sigma of the day's volume-weighted average price, {signed(d)} sigma from it"
    else:
        text = f"price is {sig(abs(d))} {verdict} the day's volume-weighted average price, more than the {MOVE_RULE_SIGMA} sigma move rule"
    ls.put("price.vs_vwap", text, figure=fig)


def _vs_prior_close(scene: Scene, ls: LabelSet) -> None:
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0:
        ls.omit("price.vs_prior_close", "row carries no prior close")
        return
    d = (scene.spot - float(pc)) / scene.sigma
    side = "above" if d > 0 else "below"
    if abs(d) < MOVE_RULE_SIGMA:
        ls.put("price.vs_prior_close", f"price is within {MOVE_RULE_SIGMA} sigma of last night's close, {signed(d)} sigma from it, an ordinary day so far")
    elif abs(d) >= OUTLIER_DAY_SIGMA:
        ls.put("price.vs_prior_close", f"price is {sig(abs(d))} {side} last night's close, more than {OUTLIER_DAY_SIGMA} sigma, an outlier day")
    else:
        ls.put("price.vs_prior_close", f"price is {sig(abs(d))} {side} last night's close, within {OUTLIER_DAY_SIGMA} sigma, an ordinary day so far")


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


def _momentum(scene: Scene, ls: LabelSet, move30: float | None) -> None:
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
    if move30 is None:
        for path in judged:
            ls.omit(path, "no 30-minute move to judge")
        return
    if abs(move30) < MOVE_RULE_SIGMA:
        # a quiet read is a fact, not a gap: the questions that read these labels have a
        # "no move" answer, and leaving the labels out would skip those questions instead
        quiet = f"the last 30 minutes moved {sig(abs(move30))}, under the {MOVE_RULE_SIGMA} sigma move rule, so there is no move to judge"
        for path in judged:
            ls.put(path, quiet)
        return
    up = move30 > 0
    word = "up" if up else "down"
    last5 = bars[-5:]
    if len(last5) < 5:
        ls.omit("momentum.closes", "needs five finished bars")
    else:
        agree = sum(1 for x in last5 if (float(x["close"]) > float(x["open"])) == up and float(x["close"]) != float(x["open"]))
        ls.put("momentum.closes", f"of the last five 1-minute bars, {agree} closed in the direction of the move, which is {word}")

    win = bars_finished_between(bars, now - timedelta(minutes=30), now)
    start = close_at(bars, now - timedelta(minutes=30))
    if len(win) < 20 or start is None:
        ls.omit("momentum.pauses", "needs 30 minutes of finished bars")
        ls.omit("momentum.path_efficiency", "needs 30 minutes of finished bars")
        return
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
