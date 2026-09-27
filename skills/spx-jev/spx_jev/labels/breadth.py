"""The breadth family: the NYSE and the sector funds around the index (breadth.*), from the market context
(market_context.py).

Schwab's breadth series as the context job saves them: $TICK and $TRIN are read a minute at a time; $UVOL and
$DVOL (the NYSE's up and down volume) and $VOLD and $VOLSPD (net volume, NYSE-wide and in S&P 500 members) are
running totals since 09:30, so a window's volume is the total at its end less the total at its start. A size is
judged against the same minute of the prior sessions' market context (``scene.prior_markets``).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``; the Nasdaq label
is DARK, since no Nasdaq breadth series is served."""
from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta
from typing import Callable

from .. import events
from ..cuts import (CHOP_CROSSES, DAY_ONE_SIDED, EVENT_DIGEST_MIN, MEMBER_SPLIT_Z, MIN_RANK_SESSIONS, OPEN_CLUSTER_MIN,
                    OPEN_DAY_MOVE_SIGMA, OPEN_TICK_LEAN, OPEN_VOL_Z, SMALLCAP_CONFIRM_SIGMA, TICK_BURST_PCT, TICK_FAR_BAND,
                    TICK_FOLLOW_SIGMA, TICK_USUAL_BAND, TRIN_HIGH, TRIN_LOW, UPVOL_LEAN_HI, UPVOL_LEAN_LO, WINDOW_10_MIN,
                    WINDOW_30_MIN, WINDOW_60_MIN)
from ..sessions import session_open
from ..state_builder import MarketContext, Scene
from .label_set import LabelSet
from .measures import ET, ONE_MINUTE, bar_time, close_at, session_extremes, settled_open
from .ranks import rank_against
from .rulers import sigma_anchor
from .words import pct, plural, sig, signed

LABELS = ("breadth.advance_decline", "breadth.tick_lean", "breadth.sectors_up",
          "breadth.tick_side_vs_usual", "breadth.upvol_share_30m", "breadth.volume_vs_count_30m",
          "breadth.at_extremes", "breadth.day_upvol_share", "breadth.flip_after_release", "breadth.members_net_day",
          "breadth.open_net_volume", "breadth.opening_tick", "breadth.tick_extreme_5m", "breadth.nasdaq_net_volume")
GATES = ("tick_extreme_follow", "breadth_flip_after_release")
DARK = {"breadth.nasdaq_net_volume": "no Nasdaq breadth series: Schwab serves no $TICKQ or $TRINQ history, $ADDQ is not a symbol, "
                                     "$VOLQ is stale, and $UVOLQ and $DVOLQ are not probed"}

# A majority of a 30-minute window's readings, and of the sectors.
MIN_TICK_MINUTES = 20
MIN_TRIN_MINUTES = 20
MIN_SECTORS = 8
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE")
# The context job saves a bar a minute; a newest value older than this means it stopped, not that it skipped a minute.
FRESH_MIN = 5
# A TICK burst is judged over the question's own five minutes.
TICK_BURST_WINDOW_MIN = 5
# A release is a decision, testimony or report at a moment in the session: the closes are not, nor a speech, nor
# the press conference that follows the decision it explains.
RELEASE_TIERS = (events.TIER, events.DATA_10AM, events.DATA_2PM)
NOT_RELEASES = events.AT_THE_CLOSE | {"FOMC_PRESSER"}
NET_VOLUME = {"$VOLD": "NYSE net volume", "$VOLSPD": "S&P 500 members' net volume"}


def build_breadth_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    release = _latest_release(scene)
    if release is None:
        ls.sleep("breadth_flip_after_release", f"no in-session release in the last {EVENT_DIGEST_MIN} minutes")
    else:
        ls.wake("breadth_flip_after_release")
    if scene.market is None:
        for path in LABELS:
            if path not in DARK:
                ls.omit(path, "no market-context snapshot today")
        ls.sleep("tick_extreme_follow", "no market-context snapshot today")
        return ls
    _advance_decline(scene, ls)
    _tick_lean(scene, ls)
    _sectors_up(scene, ls)
    _tick_side_vs_usual(scene, ls)
    _upvol_share_30m(scene, ls)
    _volume_vs_count_30m(scene, ls)
    _day_upvol_share(scene, ls)
    _members_net_day(scene, ls)
    _at_extremes(scene, ls)
    _flip_after_release(scene, release, ls)
    _open_net_volume(scene, ls)
    tick_bands = _tick_bands_by_minute(scene)
    _opening_tick(scene, tick_bands, ls)
    _tick_extreme_5m(scene, tick_bands, ls)
    return ls


def _advance_decline(scene: Scene, ls: LabelSet) -> None:
    add = scene.market.last("$ADD", scene.now)
    if add is None:
        ls.omit("breadth.advance_decline", "no NYSE advance-decline value at or before now")
    elif add == 0:
        ls.put("breadth.advance_decline", "as many NYSE stocks are advancing as declining today")
    else:
        ls.put("breadth.advance_decline", f"on the NYSE {abs(round(add))} more stocks are {'advancing than declining' if add > 0 else 'declining than advancing'} today, more {'up' if add > 0 else 'down'} than {'down' if add > 0 else 'up'}")


def _tick_lean(scene: Scene, ls: LabelSet) -> None:
    ticks = scene.market.between("$TICK", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_lean", f"needs {MIN_TICK_MINUTES} NYSE tick readings in the last {WINDOW_30_MIN} minutes, have {len(ticks)}")
        return
    up, down = sum(1 for v in ticks if v > 0), sum(1 for v in ticks if v < 0)
    lean = "leaning to buying" if up > down else "leaning to selling" if down > up else "even"
    ls.put("breadth.tick_lean", f"over the last {WINDOW_30_MIN} minutes the NYSE tick read above zero {plural(up, 'time')} and below zero {plural(down, 'time')} of {len(ticks)}, {lean}")


def _sectors_up(scene: Scene, ls: LabelSet) -> None:
    mk = scene.market
    moved = [(s, mk.last(s, scene.now), mk.first(s)) for s in SECTORS]
    moved = [(s, now_v, open_v) for s, now_v, open_v in moved if now_v is not None and open_v]
    if len(moved) < MIN_SECTORS:
        ls.omit("breadth.sectors_up", f"needs {MIN_SECTORS} of the {len(SECTORS)} sector funds with a value today, have {len(moved)}")
        return
    up = sum(1 for _, now_v, open_v in moved if now_v > open_v)
    word = "most of them" if up * 2 > len(moved) else "fewer than half" if up * 2 < len(moved) else "exactly half"
    ls.put("breadth.sectors_up", f"{up} of {len(moved)} sector funds are above where they opened today, {word}")


# ----------------------------------------------------------------------------- shared reads

def _same_clock(scene: Scene, measure: Callable[[MarketContext, datetime], float | None]) -> list[float]:
    """``measure(market, then)`` on each prior session's market context at this read's clock minute."""
    clock = scene.now.astimezone(ET).time()
    out = []
    for day, mk in scene.prior_markets.items():
        v = measure(mk, datetime.combine(date.fromisoformat(day), clock, tzinfo=ET))
        if v is not None:
            out.append(v)
    return out


def _running_total(mk: MarketContext, symbol: str, t: datetime) -> float | None:
    """A running-total series ($UVOL, $DVOL, $VOLD, $VOLSPD) as it stood at ``t``: nothing yet at the open."""
    return 0.0 if t <= session_open(t) else mk.last(symbol, t, max_age_min=FRESH_MIN)


def _upvol_share(mk: MarketContext, start: datetime, end: datetime) -> float | None:
    """The share of NYSE volume that went into rising stocks from ``start`` to ``end``; None when either
    total is not known then or no volume traded."""
    totals = [_running_total(mk, s, t) for s in ("$UVOL", "$DVOL") for t in (start, end)]
    if any(v is None for v in totals):
        return None
    up, down = totals[1] - totals[0], totals[3] - totals[2]
    return up / (up + down) if up + down > 0 else None


def _upvol_lean(share: float) -> str:
    """The share against the lean lines, judged as the sentence prints it."""
    shown = round(share, 2)
    if shown > UPVOL_LEAN_HI:
        return f"past the {pct(UPVOL_LEAN_HI)} lean line on the buy side"
    if shown < UPVOL_LEAN_LO:
        return f"past the {pct(UPVOL_LEAN_LO)} lean line on the sell side"
    return f"inside the {pct(UPVOL_LEAN_LO)} to {pct(UPVOL_LEAN_HI)} even band"


def _millions(shares: float) -> str:
    return f"{signed(shares / 1e6, 0)}M"


def _net_volume_z(scene: Scene, symbol: str, ls: LabelSet, path: str) -> tuple[float, float] | None:
    """A net-volume running total now and how many of its usual swings that is: the total over the spread
    of the same total at this clock on the prior sessions (0.00 at the open on every day, so the spread is
    the whole of a usual day's lean). Omits ``path`` with the reason and returns None when either is
    missing."""
    value = _running_total(scene.market, symbol, scene.now)
    if value is None:
        ls.omit(path, f"no {NET_VOLUME[symbol]} ({symbol}) known within {FRESH_MIN} minutes of now: the market-context job "
                      f"stopped or has not saved it")
        return None
    base = _same_clock(scene, lambda mk, then: _running_total(mk, symbol, then))
    if len(base) < MIN_RANK_SESSIONS:
        ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions with {NET_VOLUME[symbol]} ({symbol}) at this minute, have {len(base)}")
        return None
    swing = statistics.stdev(base)
    if swing <= 0:
        ls.omit(path, f"{NET_VOLUME[symbol]} ({symbol}) stood the same at this minute on every prior session: no usual swing to size it by")
        return None
    return value, round(value / swing, 2)


def _swings(value: float, z: float, clock: str) -> str:
    """A net-volume total and its size against the usual swing at this clock, with its side."""
    side = " on the buy side" if z > 0 else " on the sell side" if z < 0 else ""
    return f"{_millions(value)}, {abs(z):.2f} times the usual swing for {clock}{side}"


def _tick_share_above_zero(mk: MarketContext, now: datetime) -> float | None:
    """The share of the last 30 minutes' $TICK closes above zero; None under MIN_TICK_MINUTES of them."""
    ticks = mk.between("$TICK", now - timedelta(minutes=WINDOW_30_MIN), now)
    return sum(1 for v in ticks if v > 0) / len(ticks) if len(ticks) >= MIN_TICK_MINUTES else None


# ----------------------------------------------------------------------------- the last 30 minutes

def _tick_side_vs_usual(scene: Scene, ls: LabelSet) -> None:
    """The share of the last 30 minutes' TICK closes above zero against the median share at this clock on
    the prior sessions: raw TICK sits below zero most of the time, so only the usual for the half hour says
    which side it leaned."""
    ticks = scene.market.between("$TICK", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_side_vs_usual", f"needs {MIN_TICK_MINUTES} NYSE TICK readings in the last {WINDOW_30_MIN} minutes, have {len(ticks)}")
        return
    base = _same_clock(scene, _tick_share_above_zero)
    if len(base) < MIN_RANK_SESSIONS:
        ls.omit("breadth.tick_side_vs_usual", f"needs {MIN_RANK_SESSIONS} prior sessions with NYSE TICK readings in this half hour, have {len(base)}")
        return
    above = sum(1 for v in ticks if v > 0)
    gap = round(above / len(ticks) - statistics.median(base), 2) or 0.0
    against = "the same as usual" if gap == 0 else f"{abs(gap):.2f} {'more' if gap > 0 else 'less'} than usual"
    if abs(gap) <= TICK_USUAL_BAND:
        band = f"within the usual band ({TICK_USUAL_BAND:.2f})"
    elif abs(gap) <= TICK_FAR_BAND:
        band = f"past the usual band ({TICK_USUAL_BAND:.2f}), inside the far band ({TICK_FAR_BAND:.2f})"
    else:
        band = f"past the far band ({TICK_FAR_BAND:.2f})"
    ls.put("breadth.tick_side_vs_usual",
           f"over the last {WINDOW_30_MIN} minutes NYSE TICK sat above zero {above} of {len(ticks)} minutes, {against} for this half hour: {band}")


def _upvol_share_30m(scene: Scene, ls: LabelSet) -> None:
    share = _upvol_share(scene.market, scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if share is None:
        ls.omit("breadth.upvol_share_30m", f"no NYSE up and down volume known both {WINDOW_30_MIN} minutes ago and now, within "
                                           f"{FRESH_MIN} minutes of each: the market-context job stopped or has not saved them")
        return
    ls.put("breadth.upvol_share_30m",
           f"over the last {WINDOW_30_MIN} minutes {pct(share)} of NYSE volume traded in rising stocks, {_upvol_lean(share)}")


def _volume_vs_count_30m(scene: Scene, ls: LabelSet) -> None:
    trin = scene.market.between("$TRIN", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(trin) < MIN_TRIN_MINUTES:
        ls.omit("breadth.volume_vs_count_30m", f"needs {MIN_TRIN_MINUTES} NYSE TRIN readings in the last {WINDOW_30_MIN} minutes, have {len(trin)}")
        return
    mean = round(statistics.fmean(trin), 2)
    if mean < TRIN_LOW:
        verdict = f"under the {TRIN_LOW} line: volume per rising stock ran heavier than volume per falling stock"
    elif mean > TRIN_HIGH:
        verdict = f"over the {TRIN_HIGH} line: volume per falling stock ran heavier than volume per rising stock"
    else:
        verdict = f"between the {TRIN_LOW} and {TRIN_HIGH} lines: volume per rising stock and per falling stock matched"
    ls.put("breadth.volume_vs_count_30m", f"over the last {WINDOW_30_MIN} minutes NYSE TRIN averaged {mean:.2f}, {verdict}")


# ----------------------------------------------------------------------------- the day so far

def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _crossings(n: int) -> str:
    return f"the 30-minute share crossed 50% {_times(n)} today" if n else "the 30-minute share has not crossed 50% today"


def _day_upvol_share(scene: Scene, ls: LabelSet) -> None:
    """The day's share of NYSE volume in rising stocks against the one-sided line, where it stood earlier when
    it is not one-sided now, and how often the share of one half hour since the open landed on the other side
    of even from the half hour before it. The half hours are taken whole, as the reads take them, so a share
    hovering at 50% is not counted as rotation minute by minute; the day's share is looked back on from half
    an hour in, before which it swings on the first minutes' thin volume."""
    mk, now, opened = scene.market, scene.now, scene.session_open
    settled = opened + timedelta(minutes=WINDOW_30_MIN)
    if now < settled:
        ls.omit("breadth.day_upvol_share", f"needs {WINDOW_30_MIN} minutes of session")
        return
    share = _upvol_share(mk, opened, now)
    if share is None:
        ls.omit("breadth.day_upvol_share", f"no NYSE up and down volume known within {FRESH_MIN} minutes of now: the market-context "
                                           f"job stopped or has not saved them")
        return
    minutes = [t for t, _ in mk.known.get("$UVOL") or [] if settled <= t <= now]
    day_shares = [(t, v) for t in minutes if (v := _upvol_share(mk, opened, t)) is not None]
    half_hours = [opened + timedelta(minutes=WINDOW_30_MIN * k) for k in range(1, int((now - opened) / timedelta(minutes=WINDOW_30_MIN)) + 1)]
    sides = [v > 0.5 for end in half_hours if (v := _upvol_share(mk, end - timedelta(minutes=WINDOW_30_MIN), end)) is not None and v != 0.5]
    crossed = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
    one_sided_low = round(1 - DAY_ONE_SIDED, 2)

    def one_sided(v: float) -> bool:
        return round(v, 2) >= DAY_ONE_SIDED or round(v, 2) <= one_sided_low

    shown = round(share, 2)
    if shown >= DAY_ONE_SIDED:
        where = f"past the {pct(DAY_ONE_SIDED)} one-sided share on the buy side"
    elif shown <= one_sided_low:
        where = f"past the {pct(one_sided_low)} one-sided share on the sell side"
    else:
        where = f"short of the {pct(one_sided_low)} and {pct(DAY_ONE_SIDED)} one-sided shares, {_upvol_lean(share)}"
        earlier = [(t, v) for t, v in day_shares if one_sided(v)]
        if earlier:
            t, v = max(earlier, key=lambda tv: abs(tv[1] - 0.5))
            where += f"; earlier today it stood past a one-sided share, at {pct(v)} at {t.astimezone(ET):%H:%M}"
        else:
            where += f"; it has not stood past a one-sided share since {settled.astimezone(ET):%H:%M}"
    rotation = f"{'at or past' if crossed >= CHOP_CROSSES else 'under'} the {CHOP_CROSSES}-crossing rotation rule"
    ls.put("breadth.day_upvol_share",
           f"since the open {pct(share)} of NYSE volume went into rising stocks, {where}; {_crossings(crossed)}, {rotation}")


def _spx_from_settled_open(scene: Scene) -> str:
    """Where SPX stands against its settled open, for the members' net volume to be read beside; empty
    before the 09:34 bar has finished or without a ruler."""
    opened, ruler = settled_open(scene.bars), sigma_anchor(scene)
    if opened is None or ruler is None:
        return ""
    move = round((scene.spot - opened) / ruler.points, 2)
    estimated = " (ruler estimated)" if ruler.estimated else ""
    if abs(move) > OPEN_DAY_MOVE_SIGMA:
        return (f" while SPX is {sig(abs(move))} {'above' if move > 0 else 'below'} its settled open, past the "
                f"{OPEN_DAY_MOVE_SIGMA} sigma open-day line{estimated}")
    return f" while SPX is {signed(move)} sigma from its settled open, within the {OPEN_DAY_MOVE_SIGMA} sigma open-day line{estimated}"


def _members_net_day(scene: Scene, ls: LabelSet) -> None:
    members = _net_volume_z(scene, "$VOLSPD", ls, "breadth.members_net_day")
    nyse = _net_volume_z(scene, "$VOLD", ls, "breadth.members_net_day") if members else None
    if members is None or nyse is None:
        return
    clock = f"{scene.now.astimezone(ET):%H:%M}"
    shown = round(members[0] / 1e6)
    sign = "above zero" if shown > 0 else "below zero" if shown < 0 else "at zero"
    gap = round(members[1] - nyse[1], 2) or 0.0
    if abs(gap) > MEMBER_SPLIT_Z:
        split = f"S&P 500 net volume runs {abs(gap):.2f} swings {'stronger' if gap > 0 else 'weaker'} than NYSE's, past the {MEMBER_SPLIT_Z} split line"
    else:
        split = f"S&P 500 net volume is matched with NYSE's, {abs(gap):.2f} swings apart, within the {MEMBER_SPLIT_Z} split line"
    ls.put("breadth.members_net_day",
           f"since the open S&P 500 members' net volume is {_swings(*members, clock)}, {sign}{_spx_from_settled_open(scene)}; "
           f"NYSE net volume is {_swings(*nyse, clock)}; {split}")


def _at_extremes(scene: Scene, ls: LabelSet) -> None:
    """At SPX's newest session extreme: NYSE net volume against where it stood at the previous one, and small
    caps (IWM) against their own extreme so far, in SPX sigma. The previous extreme is the session's as it
    stood half an hour before the new one was made (one 30-minute read earlier), so a grind of one-minute
    highs is held against where the last read saw the extreme, not against the minute before."""
    mk = scene.market
    ext = session_extremes(scene.bars)
    if ext is None:
        ls.omit("breadth.at_extremes", "no finished SPX bars yet")
        return
    is_high = ext.high_at >= ext.low_at
    word, made_at, spx_extreme = ("high", ext.high_at, ext.high) if is_high else ("low", ext.low_at, ext.low)
    before = session_extremes([b for b in scene.bars if bar_time(b) + ONE_MINUTE <= made_at - timedelta(minutes=WINDOW_30_MIN)])
    if before is None:
        ls.omit("breadth.at_extremes", f"the session {word} was made within {WINDOW_30_MIN} minutes of the open: no earlier {word} to hold it against")
        return
    before_at = before.high_at if is_high else before.low_at
    ruler = sigma_anchor(scene)
    if ruler is None:
        ls.omit("breadth.at_extremes", "no sigma ruler for today: no morning anchor, live sigma or VIX at the settled open")
        return
    net_now, net_before = _running_total(mk, "$VOLD", made_at), _running_total(mk, "$VOLD", before_at)
    small_caps, small_caps_then = mk.between("IWM", scene.session_open, made_at), mk.last("IWM", made_at, max_age_min=FRESH_MIN)
    at_clock, before_clock = f"{made_at.astimezone(ET):%H:%M}", f"{before_at.astimezone(ET):%H:%M}"
    if net_now is None or net_before is None:
        ls.omit("breadth.at_extremes", f"no NYSE net volume ($VOLD) known within {FRESH_MIN} minutes of both SPX {word}s, "
                                       f"{before_clock} and {at_clock}")
        return
    if not small_caps or small_caps_then is None:
        ls.omit("breadth.at_extremes", f"no small-cap (IWM) price known within {FRESH_MIN} minutes of the SPX {word} at {at_clock}")
        return
    now_m, before_m = round(net_now / 1e6), round(net_before / 1e6)
    reached = now_m >= before_m if is_high else now_m <= before_m
    level = "level with" if now_m == before_m else "above" if now_m > before_m else "below"
    own = max(small_caps) if is_high else min(small_caps)
    apart = round(abs(own - small_caps_then) / own * spx_extreme / ruler.points, 2)
    confirm = f"{'within' if apart <= SMALLCAP_CONFIRM_SIGMA else 'past'} the {SMALLCAP_CONFIRM_SIGMA} sigma confirm rule"
    iwm = (f"at their own session {word}" if apart == 0 else
           f"{sig(apart)} {'below' if is_high else 'above'} their own session {word}")
    ls.put("breadth.at_extremes",
           f"when SPX made its new session {word} at {at_clock}, NYSE net volume was {_millions(net_now)}, {level} the "
           f"{_millions(net_before)} where it stood at the previous {word} at {before_clock}, {'reaching' if reached else 'short of'} "
           f"that level, and small caps (IWM) were {iwm}, {confirm}{'; ruler estimated' if ruler.estimated else ''}")


# ----------------------------------------------------------------------------- around a release

def _latest_release(scene: Scene) -> events.Event | None:
    """The newest release of today's session made by now and within EVENT_DIGEST_MIN minutes of it."""
    return next((e for e in reversed(events.on_day(scene.now.astimezone(ET).date()))
                 if e.tier in RELEASE_TIERS and e.kind not in NOT_RELEASES and scene.session_open < e.start <= scene.now
                 and scene.now - e.start <= timedelta(minutes=EVENT_DIGEST_MIN)), None)


def _flip_after_release(scene: Scene, release: events.Event | None, ls: LabelSet) -> None:
    """The share of NYSE volume in rising stocks since the release against the hour before it (from the open
    when the release came in the first hour), each against the lean lines."""
    if release is None:
        ls.omit("breadth.flip_after_release", f"no in-session release in the last {EVENT_DIGEST_MIN} minutes")
        return
    before_from = max(release.start - timedelta(minutes=WINDOW_60_MIN), scene.session_open)
    since, before = _upvol_share(scene.market, release.start, scene.now), _upvol_share(scene.market, before_from, release.start)
    released = f"{release.start.astimezone(ET):%H:%M}"
    if since is None or before is None:
        ls.omit("breadth.flip_after_release", f"no NYSE up and down volume known within {FRESH_MIN} minutes of {released} and of now: "
                                              f"the market-context job stopped or has not saved them")
        return
    ago = round((scene.now - release.start).total_seconds() / 60)
    lead = round((release.start - before_from).total_seconds() / 60)
    before_words = "in the hour before it" if lead == WINDOW_60_MIN else f"in the {plural(lead, 'minute')} before it, from the open,"
    ls.put("breadth.flip_after_release",
           f"since {release.words} at {released}, {plural(ago, 'minute')} ago, {pct(since)} of NYSE volume went into rising stocks, "
           f"{_upvol_lean(since)}; {before_words} the share was {pct(before)}, {_upvol_lean(before)}")


# ----------------------------------------------------------------------------- the opening lane

def _open_net_volume(scene: Scene, ls: LabelSet) -> None:
    """NYSE and S&P 500 members' net volume since the open, each in usual swings for this minute against the
    lean line, and advancers minus decliners over the last 10 minutes ranked against the same minutes."""
    nyse = _net_volume_z(scene, "$VOLD", ls, "breadth.open_net_volume")
    members = _net_volume_z(scene, "$VOLSPD", ls, "breadth.open_net_volume") if nyse else None
    if nyse is None or members is None:
        return
    clock = f"{scene.now.astimezone(ET):%H:%M}"
    leans = [f"{NET_VOLUME[symbol]} is {_swings(value, z, clock)}, {'past' if abs(z) > OPEN_VOL_Z else 'within'} the {OPEN_VOL_Z} lean line"
             for symbol, (value, z) in (("$VOLD", nyse), ("$VOLSPD", members))]
    thrust = _advancers_10m(scene.market, scene.now)
    if thrust is not None:
        rank = rank_against(thrust, _same_clock(scene, _advancers_10m))
        leans.append(f"advancers minus decliners {'rose' if thrust > 0 else 'fell' if thrust < 0 else 'held'} {abs(thrust):.0f} in the "
                     f"last {WINDOW_10_MIN} minutes{', ' + rank.words() if rank else ''}")
    ls.put("breadth.open_net_volume", f"since {scene.session_open.astimezone(ET):%H:%M} " + "; ".join(leans))


def _advancers_10m(mk: MarketContext, now: datetime) -> float | None:
    """How far NYSE advancers minus decliners ($ADD) moved over the last 10 minutes."""
    then, latest = mk.last("$ADD", now - timedelta(minutes=WINDOW_10_MIN), max_age_min=FRESH_MIN), mk.last("$ADD", now, max_age_min=FRESH_MIN)
    return None if then is None or latest is None else latest - then


def _percentile(values: list[float], share: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[round(share * 100) - 1]


def _tick_bands_by_minute(scene: Scene) -> dict[time, tuple[float, float]]:
    """The top and bottom burst bands of $TICK for each minute of the day: the TICK_BURST_PCT percentile of
    the prior sessions' bar highs at that minute and the mirror percentile of their lows, where at least
    MIN_RANK_SESSIONS sessions have the bar."""
    by_minute: dict[time, list[dict]] = {}
    for mk in scene.prior_markets.values():
        for _, bar in mk.bars.get("$TICK") or []:
            by_minute.setdefault(bar_time(bar).astimezone(ET).time(), []).append(bar)
    return {minute: (_percentile([float(b["high"]) for b in bars], TICK_BURST_PCT),
                     _percentile([float(b["low"]) for b in bars], 1 - TICK_BURST_PCT))
            for minute, bars in by_minute.items() if len(bars) >= MIN_RANK_SESSIONS}


def _tick_bursts(bands: dict[time, tuple[float, float]], bars: list[dict]) -> tuple[int, int] | None:
    """How many of today's $TICK ``bars`` reached their minute's top band with their high, and the bottom band
    with their low; None when a minute has no bands."""
    up = down = 0
    for bar in bars:
        band = bands.get(bar_time(bar).astimezone(ET).time())
        if band is None:
            return None
        up += float(bar["high"]) >= band[0]
        down += float(bar["low"]) <= band[1]
    return up, down


def _fresh_tick_bars(scene: Scene, since: datetime) -> list[dict]:
    """Today's $TICK bars finished after ``since``; none when the newest is older than FRESH_MIN."""
    bars = scene.market.bars_between("$TICK", since, scene.now)
    return bars if bars and scene.now - (bar_time(bars[-1]) + ONE_MINUTE) <= timedelta(minutes=FRESH_MIN) else []


def _burst_count(n: int, part: str, band: str) -> str:
    reached = f"its 1-minute {part} {'reached' if n else 'never reached'} the {band} {pct(1 - TICK_BURST_PCT)} band"
    if n == 0:
        return reached
    return (f"{reached} for these minutes {_times(n)}, {'at or past' if n >= OPEN_CLUSTER_MIN else 'short of'} the "
            f"{OPEN_CLUSTER_MIN}-burst cluster count")


def _opening_tick(scene: Scene, bands: dict[time, tuple[float, float]], ls: LabelSet) -> None:
    bars = _fresh_tick_bars(scene, scene.session_open)
    if not bars:
        ls.omit("breadth.opening_tick", f"no NYSE TICK bar in the last {FRESH_MIN} minutes: the market-context job stopped or has not saved it")
        return
    bursts = _tick_bursts(bands, bars)
    if bursts is None:
        ls.omit("breadth.opening_tick", f"needs {MIN_RANK_SESSIONS} prior sessions of NYSE TICK bars at each minute since the open")
        return
    mean = round(statistics.fmean(float(b["close"]) for b in bars))
    if abs(mean) > OPEN_TICK_LEAN:
        lean = f"past the {OPEN_TICK_LEAN} lean line on the {'buy' if mean > 0 else 'sell'} side"
    else:
        lean = f"within the {OPEN_TICK_LEAN} lean line"
    ls.put("breadth.opening_tick", f"since the open NYSE TICK averaged {mean:+d}, {lean}; {_burst_count(bursts[0], 'highs', 'top')}; "
                                   f"{_burst_count(bursts[1], 'lows', 'bottom')}")


def _tick_extreme_5m(scene: Scene, bands: dict[time, tuple[float, float]], ls: LabelSet) -> None:
    """A TICK burst in the last five minutes and whether SPX followed it over the same minutes; the gate of
    tick_extreme_follow, which sleeps without a burst."""
    bars = _fresh_tick_bars(scene, scene.now - timedelta(minutes=TICK_BURST_WINDOW_MIN))
    bursts = _tick_bursts(bands, bars) if bars else None
    if bursts is None:
        why = (f"no NYSE TICK bar in the last {FRESH_MIN} minutes: the market-context job stopped or has not saved it" if not bars else
               f"needs {MIN_RANK_SESSIONS} prior sessions of NYSE TICK bars at each of the last {TICK_BURST_WINDOW_MIN} minutes")
        ls.omit("breadth.tick_extreme_5m", why)
        ls.sleep("tick_extreme_follow", why)
        return
    buying, selling = bursts[0] > 0, bursts[1] > 0
    if buying or selling:
        ls.wake("tick_extreme_follow")
    else:
        ls.sleep("tick_extreme_follow", f"no NYSE TICK burst in the last {TICK_BURST_WINDOW_MIN} minutes")
    ruler = sigma_anchor(scene)
    then, latest = close_at(scene.bars, scene.now - timedelta(minutes=TICK_BURST_WINDOW_MIN)), close_at(scene.bars, scene.now)
    if ruler is None or then is None or latest is None:
        ls.omit("breadth.tick_extreme_5m", f"needs a finished SPX bar {TICK_BURST_WINDOW_MIN} minutes ago and a sigma ruler for today")
        return
    band = pct(1 - TICK_BURST_PCT)
    if buying and selling:
        burst = f"high reached the top {band} band for its minute and its low reached the bottom {band} band (bursts both ways)"
    elif buying:
        burst = f"high reached the top {band} band for its minute (a buying burst) and its low did not reach the bottom band"
    elif selling:
        burst = f"low reached the bottom {band} band for its minute (a selling burst) and its high did not reach the top band"
    else:
        burst = f"highs and lows stayed inside the top and bottom {band} bands for their minutes (no burst)"
    move = round((latest - then) / ruler.points, 2)
    follow = f"{'past' if abs(move) > TICK_FOLLOW_SIGMA else 'short of'} the {TICK_FOLLOW_SIGMA} sigma follow line"
    spx = (f"SPX {'rose' if move > 0 else 'fell'} {sig(abs(move))} over the same {TICK_BURST_WINDOW_MIN} minutes" if move else
           f"SPX did not move over the same {TICK_BURST_WINDOW_MIN} minutes")
    ls.put("breadth.tick_extreme_5m", f"in the last {TICK_BURST_WINDOW_MIN} minutes NYSE TICK's 1-minute {burst}; {spx}, {follow}"
                                      f"{'; ruler estimated' if ruler.estimated else ''}")
