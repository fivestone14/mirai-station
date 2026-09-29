"""The breadth family: the NYSE and the sector funds around the index (breadth.*), from the market context
(market_context.py).

Schwab's breadth series as the context job saves them: $TICK and $TRIN are read a minute at a time. $UVOL and
$DVOL (thousands of shares) are the day's volume so far in the NYSE stocks up, and down, on the day at that
minute: a stock that turns takes its whole day's volume from one to the other, so neither only grows and their
change over a window is not that window's volume; only from the open, where both start at nothing, do they give
a share. $VOLD (their difference, in shares) and $VOLSPD (the same in S&P 500 members) are net volume, and a
window's lean is the change in net volume from its start to its end; a minute Schwab served no $VOLD for carries
it derived from $UVOL and $DVOL, marked so (market_context.derived_vold). Every
measure is ranked against the same measure at the same minute on up to the last 20 prior sessions' market context
(``scene.prior_markets``; ranks.rank_sessions, needing SAME_CLOCK_MIN_SESSIONS of them, else the label is omitted
with the reason) and said by its third, never against a fixed line: a measure with a side leans to buying in its
top third and to selling in its bottom third. SPX's own move beside a breadth read is ranked by ranks.move_rank.

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``; the Nasdaq label
is DARK, since no Nasdaq breadth series is served."""
from __future__ import annotations

import statistics
from datetime import datetime, time, timedelta

from .. import events
from ..cuts import EVENT_DIGEST_MIN, OPEN_CLUSTER_MIN, SAME_CLOCK_MIN_SESSIONS, TICK_BURST_PCT, WINDOW_10_MIN, WINDOW_30_MIN, WINDOW_60_MIN
from ..sessions import session_open
from ..state_builder import MarketContext, Scene
from .label_set import LabelSet
from .measures import ET, ONE_MINUTE, SETTLED_OPEN_BAR, bar_time, close_at, session_extremes, settled_open
from .plausible import left_out
from .price import NEW_EXTREME_RECENT_MIN, _minutes_since_bar
from .ranks import (SameClockRank, fifth_side, move_rank, rank_sessions, same_clock_market, same_clock_values, tick_bands_by_minute,
                    tick_bursts)
from .rulers import NO_ANCHOR, sigma_anchor
from .words import pct, plural, sig, signed

LABELS = ("breadth.advance_decline", "breadth.tick_lean", "breadth.sectors_up",
          "breadth.tick_side_vs_usual", "breadth.net_volume_change_30m", "breadth.volume_vs_count_30m",
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
# A release is a tier-1 row or a report at a moment in the session: a decision, testimony or the Fed chair's
# Jackson Hole speech. The closes are not, nor the press conference that follows the decision it explains, nor
# the Fed officials' scheduled remarks (the fed_speaker tier).
RELEASE_TIERS = (events.TIER, events.DATA_10AM, events.DATA_2PM)
NOT_RELEASES = events.AT_THE_CLOSE | {"FOMC_PRESSER"}
NET_VOLUME = {"$VOLD": "NYSE net volume", "$VOLSPD": "S&P 500 members' net volume"}
SERIES = {**NET_VOLUME, "$ADD": "NYSE advancers minus decliners"}
THIRDS = ("bottom third", "middle third", "top third")


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
    _net_volume_change_30m(scene, ls)
    _volume_vs_count_30m(scene, ls)
    _day_upvol_share(scene, ls)
    _members_net_day(scene, ls)
    _at_extremes(scene, ls)
    _flip_after_release(scene, release, ls)
    _open_net_volume(scene, ls)
    tick_bands = tick_bands_by_minute(scene)
    _opening_tick(scene, tick_bands, ls)
    _tick_extreme_5m(scene, tick_bands, ls)
    return ls


def _advance_decline(scene: Scene, ls: LabelSet) -> None:
    add = scene.market.last("$ADD", scene.now)
    if add is None:
        ls.omit("breadth.advance_decline", _unsaved(scene.market, "$ADD", scene.now))
    elif add == 0:
        ls.put("breadth.advance_decline", "as many NYSE stocks are advancing as declining today")
    else:
        ls.put("breadth.advance_decline", f"on the NYSE {abs(round(add))} more stocks are {'advancing than declining' if add > 0 else 'declining than advancing'} today, more {'up' if add > 0 else 'down'} than {'down' if add > 0 else 'up'}")


def _tick_lean(scene: Scene, ls: LabelSet) -> None:
    ticks = scene.market.between("$TICK", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_lean", left_out(scene.market, "$TICK") or
                f"needs {MIN_TICK_MINUTES} NYSE tick readings in the last {WINDOW_30_MIN} minutes, have {len(ticks)}")
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

def _unsaved(mk: MarketContext, symbol: str, t: datetime) -> str | None:
    """The reason when ``symbol`` has no value at all today by ``t``, naming it: a series Schwab served empty
    (on 2026-09-28 $ADD, $VOLD and $VOLSPD all day) is not one that stopped, and one that does not read like its
    own history was taken out (plausible.gap). None when it has a value."""
    if why := left_out(mk, symbol):
        return why
    return None if mk.last(symbol, t) is not None else f"the market-context job has saved no {SERIES[symbol]} ({symbol}) today"


def _running_total(mk: MarketContext, symbol: str, t: datetime) -> float | None:
    """A running-total series ($UVOL, $DVOL, $VOLD, $VOLSPD) as it stood at ``t``: nothing yet at the open."""
    return 0.0 if t <= session_open(t) else mk.last(symbol, t, max_age_min=FRESH_MIN)


def _day_upvol_share_at(mk: MarketContext, t: datetime) -> float | None:
    """The share of the day's NYSE volume to ``t`` in stocks up on the day then; None when either total is not
    known then or no volume has traded."""
    up, down = _running_total(mk, "$UVOL", t), _running_total(mk, "$DVOL", t)
    return up / (up + down) if up is not None and down is not None and up + down > 0 else None


def _net_volume_change(mk: MarketContext, start: datetime, end: datetime) -> float | None:
    """How far NYSE net volume ($VOLD) moved from ``start`` to ``end``, in shares: above zero leans to buying;
    None when either end is not known."""
    then, latest = _running_total(mk, "$VOLD", start), _running_total(mk, "$VOLD", end)
    return None if then is None or latest is None else latest - then


def _ranked(rank: SameClockRank, where: str = "at this minute") -> str:
    """A rank in the owner's words: "higher than 17 of the last 20 sessions at this minute, top third"."""
    return f"higher than {rank.higher_than} of the last {rank.of} sessions {where}, {rank.band}"


def _lean(rank: SameClockRank, where: str = "at this minute") -> str:
    """A measure with a side by its third: the top third leans to buying, the bottom third to selling."""
    side = {"top third": "leaning to buying", "bottom third": "leaning to selling"}.get(rank.band, "no lean")
    return f"{_ranked(rank, where)}: {side}"


def _millions(shares: float) -> str:
    return f"{signed(shares / 1e6, 0)}M"


def _net_volume_rank(scene: Scene, symbol: str, ls: LabelSet, path: str) -> tuple[float, SameClockRank] | None:
    """A net-volume running total now, with its side, and its rank against the same total at this minute on
    the prior sessions (0 at the open on every day, so the rank is of the whole day's lean so far). Omits
    ``path`` with the reason and returns None when either is missing."""
    value = _running_total(scene.market, symbol, scene.now)
    if value is None:
        ls.omit(path, _unsaved(scene.market, symbol, scene.now) or f"no {NET_VOLUME[symbol]} ({symbol}) known within {FRESH_MIN} "
                                                                   f"minutes of now: the market-context job stopped or has not saved it")
        return None
    rank, why = rank_sessions(value, same_clock_market(scene, lambda mk, then: _running_total(mk, symbol, then)),
                              f"{NET_VOLUME[symbol]} ({symbol}) at this minute")
    if rank is None:
        ls.omit(path, why)
        return None
    return value, rank


def _net_volume(symbol: str, value: float, rank: SameClockRank) -> str:
    return f"{NET_VOLUME[symbol]} is {_millions(value)}, {_lean(rank)}"


def _tick_share_above_zero(mk: MarketContext, now: datetime) -> float | None:
    """The share of the last 30 minutes' $TICK closes above zero; None under MIN_TICK_MINUTES of them."""
    ticks = mk.between("$TICK", now - timedelta(minutes=WINDOW_30_MIN), now)
    return sum(1 for v in ticks if v > 0) / len(ticks) if len(ticks) >= MIN_TICK_MINUTES else None


def _trin_mean(mk: MarketContext, now: datetime) -> float | None:
    """The mean of the last 30 minutes' $TRIN closes; None under MIN_TRIN_MINUTES of them."""
    trin = mk.between("$TRIN", now - timedelta(minutes=WINDOW_30_MIN), now)
    return statistics.fmean(trin) if len(trin) >= MIN_TRIN_MINUTES else None


def _at_clock(scene: Scene, t: datetime, then: datetime) -> datetime:
    """``t`` today moved to the same clock on the prior session whose read-clock moment is ``then``: how a
    window that does not end at the read (a release's, an extreme's) is found on the prior sessions."""
    return then - (scene.now - t)


# ----------------------------------------------------------------------------- the last 30 minutes

def _five_levels(rank: SameClockRank) -> str:
    """A rank on five levels, as a question with an outer tail on each side needs it: the top and bottom fifths,
    the rest of the top and bottom thirds, and the middle third."""
    fifth = fifth_side(rank)
    if fifth:
        return f"in the {'top' if fifth > 0 else 'bottom'} fifth"
    if rank.band == "middle third":
        return "in the middle third"
    return f"in the {rank.band}, short of the {rank.band.split()[0]} fifth"


def _tick_side_vs_usual(scene: Scene, ls: LabelSet) -> None:
    """The share of the last 30 minutes' TICK closes above zero, ranked against the same share at this minute on
    the prior sessions on five levels: raw TICK sits below zero most of the time, so only the usual for the half
    hour says which side it leaned. The median share is written beside it as the usual."""
    ticks = scene.market.between("$TICK", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if len(ticks) < MIN_TICK_MINUTES:
        ls.omit("breadth.tick_side_vs_usual", left_out(scene.market, "$TICK") or
                f"needs {MIN_TICK_MINUTES} NYSE TICK readings in the last {WINDOW_30_MIN} minutes, have {len(ticks)}")
        return
    base = same_clock_market(scene, _tick_share_above_zero)
    above = sum(1 for v in ticks if v > 0)
    rank, why = rank_sessions(above / len(ticks), base, "NYSE TICK readings in this half hour")
    if rank is None:
        ls.omit("breadth.tick_side_vs_usual", why)
        return
    gap = round(above / len(ticks) - statistics.median(base[:rank.of]), 2) or 0.0
    against = "the same as the usual share" if gap == 0 else f"{abs(gap):.2f} {'more' if gap > 0 else 'less'} than the usual share"
    ls.put("breadth.tick_side_vs_usual",
           f"over the last {WINDOW_30_MIN} minutes NYSE TICK sat above zero {above} of {len(ticks)} minutes, {against} for this half "
           f"hour; {rank.words()}, {_five_levels(rank)}")


def _net_volume_change_30m(scene: Scene, ls: LabelSet) -> None:
    """NYSE net volume's change over the last 30 minutes, ranked against the same change at this minute."""
    change = _net_volume_change(scene.market, scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if change is None:
        ls.omit("breadth.net_volume_change_30m", _unsaved(scene.market, "$VOLD", scene.now) or
                f"no NYSE net volume ($VOLD) known both {WINDOW_30_MIN} minutes ago and now, within {FRESH_MIN} minutes of each: "
                f"the market-context job stopped or has not saved it")
        return
    rank, why = rank_sessions(change, same_clock_market(scene, lambda mk, then: _net_volume_change(mk, then - timedelta(minutes=WINDOW_30_MIN), then)),
                              f"NYSE net volume ($VOLD) over the {WINDOW_30_MIN} minutes to this minute")
    if rank is None:
        ls.omit("breadth.net_volume_change_30m", why)
        return
    ls.put("breadth.net_volume_change_30m",
           f"over the last {WINDOW_30_MIN} minutes NYSE net volume changed by {_millions(change)}, {_lean(rank)}")


def _volume_vs_count_30m(scene: Scene, ls: LabelSet) -> None:
    """Mean $TRIN over the half hour ranked at this minute: TRIN is the advancers' share of the count over their
    share of the volume, so its bottom third is volume leaning to rising stocks beyond their count, for this
    time of day, and its top third to falling stocks."""
    mean = _trin_mean(scene.market, scene.now)
    if mean is None:
        have = len(scene.market.between("$TRIN", scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now))
        ls.omit("breadth.volume_vs_count_30m", left_out(scene.market, "$TRIN") or
                f"needs {MIN_TRIN_MINUTES} NYSE TRIN readings in the last {WINDOW_30_MIN} minutes, have {have}")
        return
    rank, why = rank_sessions(mean, same_clock_market(scene, _trin_mean), f"{MIN_TRIN_MINUTES} NYSE TRIN readings in this half hour")
    if rank is None:
        ls.omit("breadth.volume_vs_count_30m", why)
        return
    verdict = {"bottom third": "volume ran heavier in rising stocks, against their count, than usual for this half hour",
               "top third": "volume ran heavier in falling stocks, against their count, than usual for this half hour"}.get(
        rank.band, "volume in rising and falling stocks matched their counts as usual for this half hour")
    ls.put("breadth.volume_vs_count_30m", f"over the last {WINDOW_30_MIN} minutes NYSE TRIN averaged {mean:.2f}, {_ranked(rank)}: {verdict}")


# ----------------------------------------------------------------------------- the day so far

def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _half_hours(now: datetime) -> list[datetime]:
    """The ends of the whole half hours since the open, up to ``now``: the marks the reads take them at."""
    opened = session_open(now)
    return [opened + timedelta(minutes=WINDOW_30_MIN * k) for k in range(1, int((now - opened) / timedelta(minutes=WINDOW_30_MIN)) + 1)]


def _crossings(mk: MarketContext, now: datetime) -> int | None:
    """How often one whole half hour since the open changed NYSE net volume the other way from the half hour
    before it, so net volume wobbling minute by minute is not counted as rotation; None when no half hour's
    change is known."""
    changes = [v for end in _half_hours(now) if (v := _net_volume_change(mk, end - timedelta(minutes=WINDOW_30_MIN), end)) is not None]
    if not changes:
        return None
    sides = [v > 0 for v in changes if v != 0]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


def _day_share_rank(scene: Scene, t: datetime) -> tuple[float | None, SameClockRank | None, str | None]:
    """The day's share of NYSE volume in rising stocks from the open to ``t``, and its rank against the same share
    at ``t``'s clock on the prior sessions or the reason it has none."""
    share = _day_upvol_share_at(scene.market, t)
    if share is None:
        return None, None, f"no NYSE up and down volume known within {FRESH_MIN} minutes of {t.astimezone(ET):%H:%M}"
    base = same_clock_market(scene, lambda mk, then: _day_upvol_share_at(mk, _at_clock(scene, t, then)))
    return share, *rank_sessions(share, base, "NYSE up and down volume since the open at this minute")


def _one_sided(rank: SameClockRank) -> str:
    """The day's share by its fifth: one-sided in the top or bottom fifth, else its third."""
    fifth = fifth_side(rank)
    return f"{_five_levels(rank)}: one-sided on the {'buy' if fifth > 0 else 'sell'} side" if fifth else rank.band


def _day_upvol_share(scene: Scene, ls: LabelSet) -> None:
    """The day's share of NYSE volume in rising stocks ranked at this minute, one-sided in the top or bottom
    fifth; where it stood earlier when it is not one-sided now, at each whole half hour's mark ranked at that
    mark; and how often the half hours' net volume changes switched sign, ranked against the same count at this
    minute. The
    day's share is looked back on from half an hour in, before which it swings on the first minutes' thin volume."""
    now = scene.now
    settled = scene.session_open + timedelta(minutes=WINDOW_30_MIN)
    if now < settled:
        ls.omit("breadth.day_upvol_share", f"needs {WINDOW_30_MIN} minutes of session")
        return
    share, rank, why = _day_share_rank(scene, now)
    if share is None:
        ls.omit("breadth.day_upvol_share", left_out(scene.market, "$UVOL") or left_out(scene.market, "$DVOL") or
                f"no NYSE up and down volume known within {FRESH_MIN} minutes of now: the market-context job stopped or has not saved them")
        return
    crossed = _crossings(scene.market, now)
    if crossed is None:
        ls.omit("breadth.day_upvol_share", _unsaved(scene.market, "$VOLD", now) or
                f"no NYSE net volume ($VOLD) known at both ends of a whole half hour since the open, within {FRESH_MIN} minutes of each")
        return
    crossed_rank, no_crossed_rank = rank_sessions(crossed, same_clock_market(scene, _crossings), "NYSE net volume ($VOLD) since the open")
    if rank is None or crossed_rank is None:
        ls.omit("breadth.day_upvol_share", why or no_crossed_rank)
        return
    where = f"{rank.words()}, {_one_sided(rank)}"
    if not fifth_side(rank):
        marks = [(t, *_day_share_rank(scene, t)[:2]) for t in _half_hours(now) if t < now]
        earlier = [(t, v, r) for t, v, r in marks if r is not None and fifth_side(r)]
        if earlier:
            t, v, r = max(earlier, key=lambda e: abs(e[1] - 0.5))
            where += (f"; earlier today it stood one-sided, at {pct(v)} at {t.astimezone(ET):%H:%M}, in the "
                      f"{'top' if fifth_side(r) > 0 else 'bottom'} fifth for that minute")
        else:
            where += f"; it has not stood one-sided at a half hour's mark since {settled.astimezone(ET):%H:%M}"
    rotation = (f"more often than {crossed_rank.higher_than} of the last {crossed_rank.of} sessions by this minute, {crossed_rank.band}"
                f"{': rotating' if crossed_rank.band == 'top third' else ''}")
    crossings = (f"the half hours' NYSE net volume changes switched sign {_times(crossed)} today" if crossed else
                 "the half hours' NYSE net volume changes have not switched sign today")
    ls.put("breadth.day_upvol_share", f"since the open {pct(share)} of NYSE volume went into rising stocks, {where}; {crossings}, {rotation}")


def _spx_from_settled_open(scene: Scene) -> str:
    """Where SPX stands against its settled open and that move's rank at this minute (ranks.move_rank over the
    minutes since the settled open, as price.day_move judges it: its bottom third is near the open), for the
    members' net volume to be read beside; empty before the 09:34 bar has finished or without a ruler."""
    opened, ruler = settled_open(scene.bars), sigma_anchor(scene)
    if opened is None or ruler is None:
        return ""
    move = (scene.spot - opened) / ruler.points
    estimated = " (ruler estimated)" if ruler.estimated else ""
    where = f"{sig(abs(move))} {'above' if move > 0 else 'below'} its settled open" if round(move, 2) else "at its settled open"
    rank, _ = move_rank(scene, move, _minutes_since_bar(scene, SETTLED_OPEN_BAR))
    if rank is None:
        return f" while SPX is {where}{estimated}"
    return (f" while SPX is {where}, a move larger than {rank.higher_than} of the last {rank.of} sessions' moves from their "
            f"settled opens at this minute, {rank.band}{': near the open' if rank.band == 'bottom third' else ''}{estimated}")


def _members_net_day(scene: Scene, ls: LabelSet) -> None:
    """S&P 500 members' net volume since the open against SPX's move from its settled open, and against NYSE's
    net volume by their thirds at this minute: a higher third is stronger, a lower one weaker."""
    members = _net_volume_rank(scene, "$VOLSPD", ls, "breadth.members_net_day")
    nyse = _net_volume_rank(scene, "$VOLD", ls, "breadth.members_net_day") if members else None
    if members is None or nyse is None:
        return
    shown = round(members[0] / 1e6)
    sign = "above zero" if shown > 0 else "below zero" if shown < 0 else "at zero"
    apart = THIRDS.index(members[1].band) - THIRDS.index(nyse[1].band)
    if apart:
        split = (f"S&P 500 net volume runs {'stronger' if apart > 0 else 'weaker'} than NYSE's, in a "
                 f"{'higher' if apart > 0 else 'lower'} third ({members[1].band} against {nyse[1].band})")
    else:
        split = f"S&P 500 net volume is matched with NYSE's, both in the {nyse[1].band}"
    ls.put("breadth.members_net_day",
           f"since the open {_net_volume('$VOLSPD', *members)}, {sign}{_spx_from_settled_open(scene)}; "
           f"{_net_volume('$VOLD', *nyse)}; {split}")


def _small_caps_apart(mk: MarketContext | None, bars: list[dict], t: datetime, points: float | None, high: bool) -> float | None:
    """How far small caps (IWM) stood at ``t`` from their own session high (``high``) or low so far, in SPX sigma:
    IWM's share off its extreme times SPX's level at ``t`` over the day's ruler in points. None without IWM near
    ``t``, an SPX close or a ruler."""
    small_caps = mk.between("IWM", session_open(t), t) if mk else []
    then, spx = mk.last("IWM", t, max_age_min=FRESH_MIN) if mk else None, close_at(bars, t)
    if not small_caps or then is None or spx is None or not points:
        return None
    own = max(small_caps) if high else min(small_caps)
    return abs(own - then) / own * spx / points


def _at_extremes(scene: Scene, ls: LabelSet) -> None:
    """At the new session extreme price.session_extreme_recent names: NYSE net volume against where it stood
    at the earlier extreme it beat, and small caps (IWM) against their own extreme so far, in SPX sigma, ranked
    against the same distance at that minute on the prior sessions, each in its own ruler (the bottom third
    confirms). The earlier extreme is the session's as it stood NEW_EXTREME_RECENT_MIN minutes before the read,
    and each is stamped with its bar's start minute, as that label takes and stamps them, so the two name one
    earlier extreme."""
    mk = scene.market
    cut = scene.now - timedelta(minutes=NEW_EXTREME_RECENT_MIN)
    before, today = session_extremes([b for b in scene.bars if bar_time(b) + ONE_MINUTE <= cut]), session_extremes(scene.bars)
    if before is None or today is None:
        ls.omit("breadth.at_extremes", f"needs SPX bars from before the last {NEW_EXTREME_RECENT_MIN} minutes")
        return
    new_high, new_low = today.high > before.high, today.low < before.low
    if not new_high and not new_low:
        ls.omit("breadth.at_extremes", f"no new session high or low in the last {NEW_EXTREME_RECENT_MIN} minutes", ended=True)
        return
    is_high = new_high and (not new_low or today.high_at >= today.low_at)
    word, made_at, before_at = ("high", today.high_at, before.high_at) if is_high else ("low", today.low_at, before.low_at)
    ruler = sigma_anchor(scene)
    if ruler is None:
        ls.omit("breadth.at_extremes", NO_ANCHOR)
        return
    net_now, net_before = _running_total(mk, "$VOLD", made_at), _running_total(mk, "$VOLD", before_at)
    apart = _small_caps_apart(mk, scene.bars, made_at, ruler.points, is_high)
    at_clock, before_clock = (f"{(t - ONE_MINUTE).astimezone(ET):%H:%M}" for t in (made_at, before_at))
    if net_now is None or net_before is None:
        ls.omit("breadth.at_extremes", _unsaved(mk, "$VOLD", scene.now) or
                f"no NYSE net volume ($VOLD) known within {FRESH_MIN} minutes of both SPX {word}s, {before_clock} and {at_clock}")
        return
    if apart is None:
        ls.omit("breadth.at_extremes", f"no small-cap (IWM) price known within {FRESH_MIN} minutes of the SPX {word} at {at_clock}")
        return
    base = same_clock_values(scene, lambda bars, then, points: _small_caps_apart(scene.prior_markets.get(then.date().isoformat()), bars,
                                                                                 _at_clock(scene, made_at, then), points, is_high))
    rank, why = rank_sessions(apart, base, f"small caps (IWM), SPX bars and a trusted morning ruler at {at_clock}")
    if rank is None:
        ls.omit("breadth.at_extremes", why)
        return
    now_m, before_m = round(net_now / 1e6), round(net_before / 1e6)
    reached = now_m >= before_m if is_high else now_m <= before_m
    level = "level with" if now_m == before_m else "above" if now_m > before_m else "below"
    shown = round(apart, 2)
    iwm = (f"at their own session {word}" if shown == 0 else
           f"{sig(shown)} {'below' if is_high else 'above'} their own session {word}")
    confirm = "confirming" if rank.band == "bottom third" else "not confirming"
    ls.put("breadth.at_extremes",
           f"when SPX made its new session {word} at {at_clock}, NYSE net volume was {_millions(net_now)}, {level} the "
           f"{_millions(net_before)} where it stood at the previous {word} at {before_clock}, {'reaching' if reached else 'short of'} "
           f"that level, and small caps (IWM) were {iwm}, further from it than {rank.higher_than} of the last {rank.of} sessions at "
           f"{at_clock}, {rank.band}: {confirm}{'; ruler estimated' if ruler.estimated else ''}")


# ----------------------------------------------------------------------------- around a release

def _latest_release(scene: Scene) -> events.Event | None:
    """The newest release of today's session made by now and within EVENT_DIGEST_MIN minutes of it."""
    return next((e for e in reversed(events.on_day(scene.now.astimezone(ET).date()))
                 if e.tier in RELEASE_TIERS and e.kind not in NOT_RELEASES and scene.session_open < e.start <= scene.now
                 and scene.now - e.start <= timedelta(minutes=EVENT_DIGEST_MIN)), None)


def _window_change_rank(scene: Scene, start: datetime, end: datetime) -> tuple[float | None, SameClockRank | None, str | None]:
    """NYSE net volume's change from ``start`` to ``end`` today, and its rank against the same window, same clock
    and length, on the prior sessions, release or not, or the reason it has none."""
    change = _net_volume_change(scene.market, start, end)
    if change is None:
        return None, None, None
    base = same_clock_market(scene, lambda mk, then: _net_volume_change(mk, _at_clock(scene, start, then), _at_clock(scene, end, then)))
    return change, *rank_sessions(change, base, f"NYSE net volume ($VOLD) from {start.astimezone(ET):%H:%M} to {end.astimezone(ET):%H:%M}")


def _flip_after_release(scene: Scene, release: events.Event | None, ls: LabelSet) -> None:
    """NYSE net volume's change since the release and over the hour before it (from the open when the release
    came in the first hour), each ranked against the same minutes on the prior sessions."""
    if release is None:
        ls.omit("breadth.flip_after_release", f"no in-session release in the last {EVENT_DIGEST_MIN} minutes")
        return
    before_from = max(release.start - timedelta(minutes=WINDOW_60_MIN), scene.session_open)
    since, since_rank, no_since = _window_change_rank(scene, release.start, scene.now)
    before, before_rank, no_before = _window_change_rank(scene, before_from, release.start)
    released = f"{release.start.astimezone(ET):%H:%M}"
    if since is None or before is None:
        ls.omit("breadth.flip_after_release", _unsaved(scene.market, "$VOLD", scene.now) or
                f"no NYSE net volume ($VOLD) known within {FRESH_MIN} minutes of {released} and of now: the market-context job "
                f"stopped or has not saved it")
        return
    if since_rank is None or before_rank is None:
        ls.omit("breadth.flip_after_release", no_since or no_before)
        return
    ago = round((scene.now - release.start).total_seconds() / 60)
    lead = round((release.start - before_from).total_seconds() / 60)
    before_words = "in the hour before it" if lead == WINDOW_60_MIN else f"in the {plural(lead, 'minute')} before it, from the open,"
    ls.put("breadth.flip_after_release",
           f"since {release.words} at {released}, {plural(ago, 'minute')} ago, NYSE net volume changed by {_millions(since)}, "
           f"{_lean(since_rank, 'over the same minutes')}; {before_words} net volume changed by {_millions(before)}, "
           f"{_lean(before_rank, 'over the same minutes')}")


# ----------------------------------------------------------------------------- the opening lane

def _open_net_volume(scene: Scene, ls: LabelSet) -> None:
    """NYSE and S&P 500 members' net volume since the open, each ranked against the same total at this minute on
    the prior sessions (the top third buying, the bottom third selling), and advancers minus decliners over the
    last 10 minutes ranked against the same minutes."""
    nyse = _net_volume_rank(scene, "$VOLD", ls, "breadth.open_net_volume")
    members = _net_volume_rank(scene, "$VOLSPD", ls, "breadth.open_net_volume") if nyse else None
    if nyse is None or members is None:
        return
    leans = [_net_volume("$VOLD", *nyse), _net_volume("$VOLSPD", *members)]
    thrust = _advancers_10m(scene.market, scene.now)
    if thrust is not None:
        leans.append(f"advancers minus decliners {'rose' if thrust > 0 else 'fell' if thrust < 0 else 'held'} {abs(thrust):.0f} in the "
                     f"last {WINDOW_10_MIN} minutes{_thrust_rank(thrust, same_clock_market(scene, _advancers_10m))}")
    ls.put("breadth.open_net_volume", f"since {scene.session_open.astimezone(ET):%H:%M} " + "; ".join(leans))


def _advancers_10m(mk: MarketContext, now: datetime) -> float | None:
    """How far NYSE advancers minus decliners ($ADD) moved over the last 10 minutes."""
    then, latest = mk.last("$ADD", now - timedelta(minutes=WINDOW_10_MIN), max_age_min=FRESH_MIN), mk.last("$ADD", now, max_age_min=FRESH_MIN)
    return None if then is None or latest is None else latest - then


def _thrust_rank(thrust: float, base: list[float]) -> str:
    """The $ADD change against the same minutes of the prior sessions; a fall is ranked by how far it fell,
    so the steepest fall of them reads as the biggest, not as higher than none. Empty under
    SAME_CLOCK_MIN_SESSIONS sessions."""
    if thrust >= 0:
        rank, _ = rank_sessions(thrust, base, "")
        return f", {rank.words()}" if rank else ""
    rank, _ = rank_sessions(-thrust, [-b for b in base], "")
    return f", a bigger fall than {rank.higher_than} of the last {rank.of} sessions at this minute" if rank else ""


def _fresh_tick_bars(scene: Scene, since: datetime) -> list[dict]:
    """Today's $TICK bars finished after ``since``; none when the newest is older than FRESH_MIN."""
    bars = scene.market.bars_between("$TICK", since, scene.now)
    return bars if bars and scene.now - (bar_time(bars[-1]) + ONE_MINUTE) <= timedelta(minutes=FRESH_MIN) else []


def _tick_mean_since_open(mk: MarketContext, now: datetime) -> float | None:
    """The mean of the $TICK bars' closes from the open to ``now``; None without a bar."""
    bars = mk.bars_between("$TICK", session_open(now), now)
    return statistics.fmean(float(b["close"]) for b in bars) if bars else None


def _burst_count(n: int, part: str, band: str) -> str:
    reached = f"its 1-minute {part} {'reached' if n else 'never reached'} the {band} {pct(1 - TICK_BURST_PCT)} band"
    if n == 0:
        return reached
    return (f"{reached} for these minutes {_times(n)}, {'at or past' if n >= OPEN_CLUSTER_MIN else 'short of'} the "
            f"{OPEN_CLUSTER_MIN}-burst cluster count")


def _no_tick_bands(minutes: str) -> str:
    return f"needs {SAME_CLOCK_MIN_SESSIONS} prior sessions of NYSE TICK bars at each {minutes}"


def _opening_tick(scene: Scene, bands: dict[time, tuple[float, float]], ls: LabelSet) -> None:
    """The mean $TICK since the open, ranked against the same mean at this minute on the prior sessions (the top
    third leans to buying, the bottom third to selling), and the bursts past each minute's burst bands."""
    bars = _fresh_tick_bars(scene, scene.session_open)
    if not bars:
        ls.omit("breadth.opening_tick", left_out(scene.market, "$TICK") or
                f"no NYSE TICK bar in the last {FRESH_MIN} minutes: the market-context job stopped or has not saved it")
        return
    bursts = tick_bursts(bands, bars)
    if bursts is None:
        ls.omit("breadth.opening_tick", _no_tick_bands("minute since the open"))
        return
    mean = _tick_mean_since_open(scene.market, scene.now)
    rank, why = rank_sessions(mean, same_clock_market(scene, _tick_mean_since_open), "NYSE TICK bars since the open")
    if rank is None:
        ls.omit("breadth.opening_tick", why)
        return
    ls.put("breadth.opening_tick", f"since the open NYSE TICK averaged {round(mean):+d}, {_lean(rank)}; "
                                   f"{_burst_count(bursts[0], 'highs', 'top')}; {_burst_count(bursts[1], 'lows', 'bottom')}")


def _tick_extreme_5m(scene: Scene, bands: dict[time, tuple[float, float]], ls: LabelSet) -> None:
    """A TICK burst in the last five minutes and whether SPX followed it over the same minutes: a real move is one
    outside the bottom third of the same five minutes on the prior sessions (ranks.move_rank), and a one-sided
    burst is followed only by a real move on its own side; the gate of tick_extreme_follow, which sleeps without
    a burst."""
    bars = _fresh_tick_bars(scene, scene.now - timedelta(minutes=TICK_BURST_WINDOW_MIN))
    bursts = tick_bursts(bands, bars) if bars else None
    if bursts is None:
        why = ((left_out(scene.market, "$TICK") or f"no NYSE TICK bar in the last {FRESH_MIN} minutes: the market-context job stopped or has "
                                               f"not saved it") if not bars else
               _no_tick_bands(f"of the last {TICK_BURST_WINDOW_MIN} minutes"))
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
    rank, why = move_rank(scene, (latest - then) / ruler.points, TICK_BURST_WINDOW_MIN)
    if rank is None:
        ls.omit("breadth.tick_extreme_5m", why)
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
    spx = (f"SPX {'rose' if move > 0 else 'fell'} {sig(abs(move))} over the same {TICK_BURST_WINDOW_MIN} minutes" if move else
           f"SPX did not move over the same {TICK_BURST_WINDOW_MIN} minutes")
    size = (f"in the {rank.band} of the last {rank.of} sessions' {TICK_BURST_WINDOW_MIN}-minute moves at this minute (larger than "
            f"{rank.higher_than} of them): {'no real move' if rank.band == 'bottom third' else 'a real move'}")
    burst_side = 0 if buying == selling else 1 if buying else -1
    against = ", against the burst" if burst_side and move * burst_side < 0 else ""
    ls.put("breadth.tick_extreme_5m", f"in the last {TICK_BURST_WINDOW_MIN} minutes NYSE TICK's 1-minute {burst}; {spx}, {size}{against}"
                                      f"{'; ruler estimated' if ruler.estimated else ''}")
