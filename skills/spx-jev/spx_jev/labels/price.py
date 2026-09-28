"""The price family: where price is and how it has moved (price.*), and the momentum reads of the last
half hour (momentum.*).

The final question set's labels are measured on the morning anchor (rulers.sigma_anchor); the ones built
before it keep the row's sigma. Each set label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Callable

from ..cuts import (BOTTOM_FIFTH, GAP_HALF_SHARE, MINUTE_WIDTH_CUT, MOVE_BURST_SHARE, MOVE_RULE_SIGMA, NIGHT_RANK_COUNT, PACE_BIGGER,
                    PACE_SMALLER, PATH_CHOPPY, PATH_ORDERLY, PAUSE_BRIEF_MIN, PAUSE_LONG_MIN, PULLBACK_SHARE, RANGE_BOTTOM_SHARE,
                    RANGE_TOP_SHARE, RSI_OVERBOUGHT, RSI_OVERSOLD, TOP_FIFTH, WINDOW_30_MIN, WINDOW_60_MIN)
from ..sessions import previous_trading_day
from ..state_builder import Scene
from .gamma import prior_books
from .label_set import LabelSet
from .measures import (ET, HOUR_MIN_BARS, MIN_RANGE_SESSIONS, ONE_MINUTE, RANGE_PRIOR_SESSIONS, RSI_PERIOD, SETTLED_OPEN_BAR, bar_time,
                       bars_finished_between, close_at, day_high_low, high_low_close, is_num, move_size, path_efficiency, session_extremes,
                       settled_open, wilder_rsi, yesterdays_bars)
from .ranks import SameClockRank, move_rank, rank_sessions, same_clock_values
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
# A move and a distance are sized by their third against the same minute on the prior sessions (ranks.rank_sessions),
# and the sentence names the third in words: a move in the bottom third is no real move, one in the top third a strong
# one; a distance in the bottom third is near the level, one in the top third far from it.
MOVE_WORDS = {"bottom third": "no real move", "middle third": "a move", "top third": "a strong move"}
DISTANCE_WORDS = {"bottom third": "near it", "middle third": "away from it", "top third": "far from it"}
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
    rows = _same_clock_rows(scene)
    to_close = _level_distance(scene, anchor, rows, "prior_close", "yesterday's close")
    to_average = _level_distance(scene, anchor, rows, "vwap", "the day's average price")
    move30 = _recent_move(scene, anchor, ls)
    _vs_vwap(scene, anchor, to_average, ls)
    _vs_prior_close(scene, anchor, to_close, ls)
    _afternoon_leg(scene, anchor, move30.value, ls)
    _day_move(scene, anchor, ls)
    _day_move_split(scene, anchor, to_close, ls)
    _hour_one_way(scene, anchor, ls)
    _move_shape(scene, anchor, move30, ls)
    _prior_close_push(scene, anchor, move30, to_close, ls)
    _session_extreme_recent(scene, anchor, ls)
    _vwap_reach(scene, anchor, to_average, ls)
    return ls


@dataclass(frozen=True)
class Ranked:
    """A signed measure in sigma, its size's rank against the same minute on the prior sessions and the sizes it was
    ranked against (``base``, newest first); ``rank`` is None, with ``why``, when the measure or its rank could not be had."""
    value: float | None
    rank: SameClockRank | None
    why: str | None
    base: tuple[float, ...] = ()


def _against(rank: SameClockRank, word: str) -> str:
    """A rank in the owner's words: "larger than 17 of the last 20 sessions at this minute, top third"."""
    return f"{word} than {rank.higher_than} of the last {rank.of} sessions at this minute, {rank.band}"


def _band_edges(base: tuple[float, ...] | list[float]) -> tuple[float, float]:
    """The largest value still in the bottom third of the recent sessions in ``base`` and the largest still short of
    the top third, as rank_sessions and SameClockRank.band place a value: the edges the phone's gauge shades."""
    s = sorted(base[:NIGHT_RANK_COUNT])
    return s[math.ceil(len(s) / 3) - 1], s[2 * len(s) // 3]


def _minutes_since_bar(scene: Scene, bar: time) -> int:
    """Whole minutes from the finish of today's ``bar`` to now: over them move_rank measures each session's move from
    the close of its own ``bar``."""
    done = datetime.combine(scene.now.astimezone(ET).date(), bar, tzinfo=ET) + ONE_MINUTE
    return int((scene.now - done).total_seconds() // 60)


def _same_clock_unscaled(scene: Scene, measure: Callable[[list[dict], datetime], float | None]) -> list[float]:
    """same_clock_values for a measure no ruler scales (a count of crossings, a path's own efficiency, a ratio of the
    day's own returns): every prior session with bars counts, its ruler estimated or not, as gap_open's crossings do,
    since with no rulers none sits out."""
    return same_clock_values(replace(scene, prior_rulers={}), lambda bars, then, sigma: measure(bars, then))


def _same_clock_rows(scene: Scene) -> list[tuple[dict, float]]:
    """Each prior session's diary row at this read's clock minute (gamma.prior_books, estimated rulers left out) with
    its morning ruler in points, newest first: the rows the distances to yesterday's close and to the day's average
    are ranked on, each from the row's own spot. A session with no ruler on file, or no row near the minute, is out."""
    return [(b.row, b.ruler.points) for b in prior_books(scene) or () if b.ruler is not None and is_num(b.row.get("spot"))]


def _level_distance(scene: Scene, anchor: SigmaRuler, rows: list[tuple[dict, float]], key: str, name: str) -> Ranked:
    """Price's signed distance from the level the row carries under ``key``, and the size of that distance against the
    same distance at this minute on the prior sessions' rows (_same_clock_rows)."""
    level = scene.row.get(key)
    if not is_num(level) or level <= 0:
        return Ranked(None, None, f"row carries no {key.replace('_', ' ')}")
    d = (scene.spot - float(level)) / anchor.points
    if scene.state_dir is None:
        return Ranked(d, None, "no state folder to read the prior sessions' diaries from")
    base = [abs(float(r["spot"]) - float(r[key])) / points for r, points in rows if is_num(r.get(key)) and r[key] > 0]
    rank, why = rank_sessions(abs(d), base, f"{name} on a diary row at this minute")
    return Ranked(d, rank, why, tuple(base))


def _recent_move(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> Ranked:
    """The 30-minute move in sigma and its rank (ranks.move_rank), written as price.recent_move and returned for the
    reads that judge it; its rank is None with the reason it was omitted when it was. On the live lane spot is the
    row's while the bars can stop, so the move needs a bar finished in the minute before the window and 20 in it, or
    it would be measured from wherever the tape stopped."""
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
        return Ranked(None, None, why)
    d = (scene.spot - ref) / anchor.points
    rank, why = move_rank(scene, d, WINDOW_30_MIN)
    if rank is None:
        ls.omit("price.recent_move", why)
        return Ranked(d, None, why)
    verdict = "going_nowhere" if rank.band == "bottom third" else "rising" if d > 0 else "falling"
    flat, strong = _band_edges(same_clock_values(scene, lambda bars, then, sigma: move_size(bars, then, sigma, WINDOW_30_MIN)))
    fig = {"kind": "signed", "value": round(d, 3), "band": round(flat, 3), "strong": round(strong, 3), "unit": "sigma", "verdict": verdict}
    went = f"moved {signed(d)} sigma" if verdict == "going_nowhere" else f"{'rose' if d > 0 else 'fell'} {sig(abs(d))}"
    text = f"over the last 30 minutes price {went}, {_against(rank, 'larger')}: {MOVE_WORDS[rank.band]}"
    hi, lo = day_high_low(win, scene.spot)
    if hi > lo:
        pos = (scene.spot - lo) / (hi - lo)
        where = "in the top fifth of" if pos >= TOP_FIFTH else "in the bottom fifth of" if pos <= BOTTOM_FIFTH else "between the top and bottom fifths of"
        text += f"; the half hour closed {where} its own range"
    ls.put("price.recent_move", ruled(anchor, text), figure=fig)
    return Ranked(d, rank, None)


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


def _vs_vwap(scene: Scene, anchor: SigmaRuler, to_average: Ranked, ls: LabelSet) -> None:
    """Price against the day's average, its distance sized against the same minute on the prior sessions: in the
    bottom third it is at the average."""
    d, rank = to_average.value, to_average.rank
    if rank is None:
        ls.omit("price.vs_vwap", to_average.why)
        return
    verdict = "at_it" if rank.band == "bottom third" else "above" if d > 0 else "below"
    fig = {"kind": "signed", "value": round(d, 3), "band": round(_band_edges(to_average.base)[0], 3), "unit": "sigma", "verdict": verdict}
    text = (f"price is {sig(abs(d))} {'above' if d >= 0 else 'below'} the day's volume-weighted average price, "
            f"{_against(rank, 'further from it')}: {'at the average' if verdict == 'at_it' else DISTANCE_WORDS[rank.band]}")
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


def _vs_prior_close(scene: Scene, anchor: SigmaRuler, to_close: Ranked, ls: LabelSet) -> None:
    """Price against yesterday's close, the distance sized against the same minute on the prior sessions: in the
    bottom third it is near the close."""
    d, rank = to_close.value, to_close.rank
    if rank is None:
        ls.omit("price.vs_prior_close", to_close.why)
        return
    ls.put("price.vs_prior_close", ruled(anchor, f"price is {sig(abs(d))} {'above' if d >= 0 else 'below'} yesterday's close, "
                                                 f"{_against(rank, 'further from it')}: {DISTANCE_WORDS[rank.band]}"))


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
    """Price against yesterday's and the session before's range, each only when that trading day's own
    bars are on file (measures.yesterdays_bars), never an older session in its place."""
    spot, sigma = scene.spot, scene.sigma
    y_day, y_bars, why = yesterdays_bars(scene)
    before, before_why = None, why
    if y_day is not None:
        d = previous_trading_day(date.fromisoformat(y_day)).isoformat()
        before = scene.prior_bars.get(d)
        before_why = f"the session before yesterday's bars are not on file: no stored session {d}"
    for back, name, path, bars, missing in ((1, "yesterday", "price.vs_yesterday", y_bars, why),
                                            (2, "the session before yesterday", "price.vs_day_before", before, before_why)):
        if bars is None:
            ls.omit(path, missing)
            continue
        hi, lo, cl = high_low_close(bars)
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

    eff = path_efficiency([float(x["close"]) for x in win])
    if not eff:
        ls.omit("momentum.path_efficiency", "the window's closes netted nothing, so there is no path to judge")
        return
    lead = f"over the last 30 minutes price travelled {1 / eff:.1f} times its net move, path efficiency {pct(eff)}"
    if eff >= PATH_ORDERLY:
        ls.put("momentum.path_efficiency", f"{lead}, orderly ({pct(PATH_ORDERLY)} or more)")
    elif eff >= PATH_CHOPPY:
        ls.put("momentum.path_efficiency", f"{lead}, mixed (between {pct(PATH_CHOPPY)} and {pct(PATH_ORDERLY)})")
    else:
        ls.put("momentum.path_efficiency", f"{lead}, choppy (under {pct(PATH_CHOPPY)})")


def _went(d: float) -> str:
    return f"rose {sig(d)}" if d > 0 else f"fell {sig(-d)}" if d < 0 else "did not move"


def _since(d: float, rank: SameClockRank) -> str:
    """A move since a moment and its rank in the owner's words: "risen 0.21 sigma, larger than 17 of the last 20
    sessions at this minute, top third: a strong move"; a move in the bottom third keeps its sign in the figure."""
    went = f"moved {signed(d)} sigma" if rank.band == "bottom third" else f"{'risen' if d > 0 else 'fallen'} {sig(abs(d))}"
    return f"{went}, {_against(rank, 'larger')}: {MOVE_WORDS[rank.band]}"


def _afternoon_leg(scene: Scene, anchor: SigmaRuler, move30: float | None, ls: LabelSet) -> None:
    start = next((float(b["close"]) for b in scene.bars if bar_time(b).astimezone(ET).time() == AFTERNOON_FROM_BAR), None)
    if start is None:
        ls.omit("price.afternoon_leg", f"no finished {AFTERNOON_FROM_BAR:%H:%M} bar: the afternoon leg starts at 14:00")
        return
    d = (scene.spot - start) / anchor.points
    rank, why = move_rank(scene, d, _minutes_since_bar(scene, AFTERNOON_FROM_BAR))
    if rank is None:
        ls.omit("price.afternoon_leg", why)
        return
    text = f"since 14:00 price has {_since(d, rank)}"
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


def _variance_ratio(bars: list[dict], then: datetime) -> tuple[float | None, str]:
    """The variance ratio of the day's overlapping 30-minute returns against its 5-minute returns from 10:00 to ``then``:
    over one, moves have built on each other; under one, they have cancelled. Lo and MacKinlay's bias-corrected form,
    one drift for both and unbiased variances, so a random walk reads one at any hour rather than well under it on a
    morning's dozen returns. None, with the reason, before an hour of varied returns."""
    t = datetime.combine(then.astimezone(ET).date(), CHARACTER_FROM, tzinfo=ET)
    marks = []
    while t <= then:
        marks.append(close_at(bars, t))
        t += timedelta(minutes=CHARACTER_STEP_MIN)
    closes = [c for c in marks if c is not None]
    steps = WINDOW_30_MIN // CHARACTER_STEP_MIN
    if len(closes) - 1 < CHARACTER_MIN_STEPS:
        return None, f"needs {CHARACTER_MIN_STEPS} 5-minute returns since 10:00, have {max(len(closes) - 1, 0)}"
    returns = [b - a for a, b in zip(closes, closes[1:])]
    n, drift = len(returns), statistics.fmean(returns)
    short = statistics.variance(returns, drift)
    if short <= 0:
        return None, "the 5-minute returns since 10:00 have not varied"
    overlapping = sum((closes[i + steps] - closes[i] - steps * drift) ** 2 for i in range(n - steps + 1))
    return overlapping / (steps * (n - steps + 1) * (1 - steps / n)) / short, ""


def _day_character(scene: Scene, ls: LabelSet) -> None:
    """The day's variance ratio (_variance_ratio) against the same ratio at this minute on the prior sessions: in the
    top third moves have built on each other more than usual for this time of day, in the bottom third they have
    cancelled more."""
    ratio, why = _variance_ratio(scene.bars, scene.now)
    if ratio is None:
        ls.omit("price.day_character", why)
        return
    rank, why = rank_sessions(ratio, _same_clock_unscaled(scene, lambda bars, then: _variance_ratio(bars, then)[0]),
                              "an hour of 5-minute returns since 10:00 at this minute")
    if rank is None:
        ls.omit("price.day_character", why)
        return
    word = {"bottom third": "cancelling", "middle third": "mixed", "top third": "building"}[rank.band]
    ls.put("price.day_character", f"so far today, 30-minute stretches have been {ratio:.2f} times what their 5-minute pieces would suggest, "
                                  f"{_against(rank, 'higher')}: {word}")


def _day_move(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    opened = settled_open(scene.bars)
    if opened is None:
        ls.omit("price.day_move", "no settled open yet: the 09:34 bar has not finished")
        return
    d = (scene.spot - opened) / anchor.points
    rank, why = move_rank(scene, d, _minutes_since_bar(scene, SETTLED_OPEN_BAR))
    if rank is None:
        ls.omit("price.day_move", why)
        return
    ls.put("price.day_move", ruled(anchor, f"since the settled 09:35 open SPX has {_since(d, rank)}"))


def _day_move_split(scene: Scene, anchor: SigmaRuler, to_close: Ranked, ls: LabelSet) -> None:
    """The day's distance from yesterday's close, sized against the same minute on the prior sessions, split into the
    opening gap and the trading since the open; once it is away from the close, which of the two made most of it."""
    opened = settled_open(scene.bars)
    if to_close.value is not None and opened is None:
        ls.omit("price.day_move_split", "no settled open yet: the 09:34 bar has not finished")
        return
    if to_close.rank is None:
        ls.omit("price.day_move_split", to_close.why)
        return
    a, pc, day, rank = anchor.points, float(scene.row["prior_close"]), to_close.value, to_close.rank
    gap, since = (opened - pc) / a, (scene.spot - opened) / a
    text = (f"SPX is {sig(abs(day))} {'above' if day >= 0 else 'below'} yesterday's close, {_against(rank, 'further from it')}: "
            f"{DISTANCE_WORDS[rank.band]}; {sig(abs(gap))} {'up' if gap >= 0 else 'down'} came from the opening gap and "
            f"{sig(abs(since))} {'up' if since >= 0 else 'down'} from trading since the open")
    if rank.band != "bottom third":
        share = gap / day
        if share >= GAP_HALF_SHARE:
            text += f", so the gap is {pct(share)} of the day's move, at least half: most of it came from the gap"
        elif share > 0:
            text += f", so the gap is {pct(share)} of the day's move, under half: most of it was built since the open"
        else:
            text += ", so the gap ran against the day's move: all of it was built since the open"
    ls.put("price.day_move_split", ruled(anchor, text))


def _hour_crosses(bars: list[dict], then: datetime) -> int | None:
    """How often the 1-minute closes of the hour to ``then`` crossed their own mean; None without HOUR_MIN_BARS bars in
    the hour and one finished before it."""
    back = then - timedelta(minutes=WINDOW_60_MIN)
    win = bars_finished_between(bars, back, then)
    if close_at(bars, back) is None or len(win) < HOUR_MIN_BARS:
        return None
    closes = [float(b["close"]) for b in win]
    mean = statistics.fmean(closes)
    sides = [c > mean for c in closes if c != mean]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


def _hour_one_way(scene: Scene, anchor: SigmaRuler, ls: LabelSet) -> None:
    """The hour's move and how often it crossed its own average, each against the same hour on the prior sessions:
    a move out of the bottom third is a move, and crosses in the bottom third are one-way."""
    crosses = _hour_crosses(scene.bars, scene.now)
    if crosses is None:
        ls.omit("price.hour_one_way", f"needs {HOUR_MIN_BARS} finished bars in the last {WINDOW_60_MIN} minutes and one before them")
        return
    d = (scene.spot - close_at(scene.bars, scene.now - timedelta(minutes=WINDOW_60_MIN))) / anchor.points
    size, why = move_rank(scene, d, WINDOW_60_MIN)
    if size is None:
        ls.omit("price.hour_one_way", why)
        return
    count, why = rank_sessions(crosses, _same_clock_unscaled(scene, _hour_crosses),
                               "an hour of bars at this minute")
    if count is None:
        ls.omit("price.hour_one_way", why)
        return
    way = "one-way" if count.band == "bottom third" else "two-way"
    ls.put("price.hour_one_way", ruled(anchor, f"over the last {WINDOW_60_MIN} minutes price {_went(d)}, {_against(size, 'larger')}: "
                                               f"{MOVE_WORDS[size.band]}; it crossed its own hourly average {plural(crosses, 'time')}, "
                                               f"{_against(count, 'more often')}: {way}"))


def _half_hour_efficiency(bars: list[dict], then: datetime) -> float | None:
    """The path efficiency (measures.path_efficiency) of the half hour to ``then``, from the close before it through
    each minute's close; None without 20 bars in it."""
    back = then - timedelta(minutes=WINDOW_30_MIN)
    ref, win = close_at(bars, back), bars_finished_between(bars, back, then)
    return path_efficiency([ref] + [float(b["close"]) for b in win]) if ref is not None and len(win) >= 20 else None


def _move_shape(scene: Scene, anchor: SigmaRuler, move30: Ranked, ls: LabelSet) -> None:
    """How the half hour's move was made: the largest of its six 5-minute chunks as a share of the net move,
    and its path efficiency, the net move over the distance the minute closes travelled, against the same half
    hour on the prior sessions: in the bottom third it was choppy."""
    if move30.rank is None:
        ls.omit("price.move_shape", f"no 30-minute move to shape: {move30.why}")
        return
    d, size = move30.value, move30.rank
    if size.band == "bottom third":
        ls.put("price.move_shape", ruled(anchor, f"the last half hour moved {signed(d)} sigma, {_against(size, 'larger')}: "
                                                 f"no real move to shape"))
        return
    back = scene.now - timedelta(minutes=WINDOW_30_MIN)
    marks = [close_at(scene.bars, back + timedelta(minutes=CHUNK_MIN * k)) for k in range(WINDOW_30_MIN // CHUNK_MIN)] + [scene.spot]
    win = bars_finished_between(scene.bars, back, scene.now)
    if any(m is None for m in marks) or len(win) < 20:
        ls.omit("price.move_shape", "needs 30 minutes of finished bars")
        return
    net = scene.spot - marks[0]
    burst = max((b - a) / net for a, b in zip(marks, marks[1:]))
    # measured on the finished bars alone, as each prior session's is, never to the row's spot
    efficiency = _half_hour_efficiency(scene.bars, scene.now)
    if efficiency is None:
        ls.omit("price.move_shape", "the half hour's minute closes travelled nothing, so there is no path to judge")
        return
    path, why = rank_sessions(efficiency, _same_clock_unscaled(scene, _half_hour_efficiency),
                              "a half hour of bars at this minute")
    if path is None:
        ls.omit("price.move_shape", why)
        return
    burst_line = "past" if burst >= MOVE_BURST_SHARE else "short of"
    ls.put("price.move_shape", ruled(anchor, f"the last half hour {_went(d)}, {_against(size, 'larger')}: {MOVE_WORDS[size.band]}; one "
                                             f"5-minute stretch made {pct(burst)} of it, {burst_line} the {pct(MOVE_BURST_SHARE)} burst line; "
                                             f"its net move was {efficiency:.2f} of the distance travelled, {_against(path, 'more one-way')}"
                                             + (": choppy" if path.band == "bottom third" else "")))


def _prior_close_push(scene: Scene, anchor: SigmaRuler, move30: Ranked, to_close: Ranked, ls: LabelSet) -> None:
    """The half hour's move against yesterday's close, where price sits from it, and whether the closes crossed it in
    the half hour and today; prior_close_push_fade is awake only on a push toward it that is a move (out of the bottom
    third of the same half hour on the prior sessions) and ends near the close (the bottom third of the same distance)
    or crossed it."""
    if to_close.value is None or move30.rank is None or to_close.rank is None:
        why = to_close.why if to_close.value is None or move30.rank is not None else f"no 30-minute move to judge: {move30.why}"
        ls.omit("price.prior_close_push", why)
        ls.sleep("prior_close_push_fade", why)
        return
    d, size, dist, near = move30.value, move30.rank, to_close.value, to_close.rank
    pc = float(scene.row["prior_close"])
    back = scene.now - timedelta(minutes=WINDOW_30_MIN)
    ref = close_at(scene.bars, back)
    toward = (pc - ref) * d > 0
    started_below = ref < pc
    half_hour = [ref] + [float(b["close"]) for b in bars_finished_between(scene.bars, back, scene.now)] + [scene.spot]
    today = [float(scene.bars[0]["open"])] + [float(b["close"]) for b in scene.bars] + [scene.spot]
    crossed_now = any((x < pc) != (y < pc) for x, y in zip(half_hour, half_hour[1:]))
    crossed_today = any((x < pc) != (y < pc) for x, y in zip(today, today[1:]))
    text = (f"over the last 30 minutes price {_went(d)} {'toward' if toward else 'away from'} yesterday's close, "
            f"{_against(size, 'larger')}: {MOVE_WORDS[size.band]}; it now sits {sig(abs(dist))} {'above' if dist >= 0 else 'below'} it, "
            f"{_against(near, 'further from it')}: {DISTANCE_WORDS[near.band]}")
    if crossed_now:
        through = (scene.spot < pc) != started_below
        text += f"; it crossed it in the last 30 minutes and is {'still through it' if through else 'back on its starting side'}"
    else:
        text += "; it has not crossed it in the last 30 minutes" + (", though it did earlier today" if crossed_today else " or today")
    ls.put("price.prior_close_push", ruled(anchor, text))
    if not toward or size.band == "bottom third":
        ls.sleep("prior_close_push_fade", "the last 30 minutes made no real move toward yesterday's close: none toward it, or one in the "
                                          "bottom third of the same half hour on the prior sessions")
    elif near.band != "bottom third" and not crossed_now:
        ls.sleep("prior_close_push_fade", "price is not near yesterday's close (out of the bottom third of the same distance on the prior "
                                          "sessions) and has not crossed it in the last 30 minutes")
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
        ls.omit("price.session_extreme_recent", f"no new session high or low in the last {NEW_EXTREME_RECENT_MIN} minutes", ended=True)
        return
    ls.put("price.session_extreme_recent", ruled(anchor, "; ".join(words for _, words in sorted(found, reverse=True))))


def _vwap_reach(scene: Scene, anchor: SigmaRuler, to_average: Ranked, ls: LabelSet) -> None:
    """How far the day's average is, in sigma and in typical 30-minute moves; average_reach_30 is awake only while
    price is away from it: out of the bottom third of the same distance on the prior sessions."""
    if to_average.rank is None:
        ls.omit("price.vwap_reach", to_average.why)
        ls.sleep("average_reach_30", to_average.why)
        return
    d, rank = -to_average.value, to_average.rank
    if rank.band == "bottom third":
        ls.sleep("average_reach_30", "price is near the day's volume-weighted average price: in the bottom third of the same distance "
                                     "on the prior sessions at this minute")
    else:
        ls.wake("average_reach_30")
    reach, how = typical_move(scene, anchor, WINDOW_30_MIN)
    if reach is None:
        ls.omit("price.vwap_reach", how)
        return
    ls.put("price.vwap_reach", ruled(anchor, f"the day's volume-weighted average price is {sig(abs(d))} {'above' if d >= 0 else 'below'} price, "
                                             f"{_against(rank, 'further')}: {DISTANCE_WORDS[rank.band]}, {abs(d) * anchor.points / reach:.1f} "
                                             f"typical 30-minute moves away"))
