"""Whether each breadth series Schwab served reads like its own history, point in time, before any label reads it.

On 2026-09-28 Schwab served $UVOL and $DVOL at about 30,000 times their size on every saved session (1.5 billion
up at the first minute, where the sessions before started near 15 thousand), $TRIN built from them, and a $TICK
that never went below zero and sat at exactly zero on two minutes in three. The live snapshots and the evening's
saved day agree minute for minute, so the feed served these numbers; nothing here misread them. A rank against
the prior sessions would have called any of them a record.

So every series in CHECKED is judged at each read on its readings from the open to the read's minute, against
the same minutes on each prior session with the series:

* its size, the median of its readings' sizes, more than SIZE_OFF times the prior sessions' median size, or (for
  a series never at or below zero on them) less than one SIZE_OFF-th of it;
* stuck on one reading: the share of its readings at its most repeated value above STUCK_SHARE and above
  STUCK_OVER_USUAL times the prior sessions' median share, once STUCK_MIN_READINGS of them are at it;
* a series Schwab computes from others (DERIVED) goes with them: $TRIN is advancers over decliners divided by
  $UVOL over $DVOL, and on every saved day $TRIN times $UVOL over $DVOL gives one steady advancers-to-decliners
  ratio.

Needs SAME_CLOCK_MIN_SESSIONS prior sessions with the series, else it is not judged. Each prior session is judged
the same way against the others and sits out of every rank for a series it fails, so one bad day never becomes
part of what the next day is measured against; today is judged against the prior sessions that passed. A series
that fails today is taken out of the read's market context, and every label that reads it is omitted with
``left_out(market, symbol)``, the reason. Once Schwab has served a new series for most of the last sessions, it is
the usual, and the old one is what fails.

Today's own NYSE breadth (SAME_DAY_WRONG) is never read at all. Asked again on the night of 2026-09-29, Schwab
served 2026-09-28 whole ($TICK from -1,142 to +953, $UVOL back in thousands, $ADD, $VOLD and $VOLSPD all there)
while 2026-09-29 was still wrong: Schwab puts a session's breadth right only after it, and the checks above can
only catch the damage once enough of the morning has gone (on 2026-09-29 the 10:02 and 10:30 reads still carried
the floored $TICK). The session is saved again once it is right (market_context.refresh_breadth), and $ADD, whose
same-day history comes back empty, is taken live from $ADVN and $DECN (market_context.derived_add).
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from ..cuts import SAME_CLOCK_MIN_SESSIONS
from ..sessions import session_open
from ..state_builder import MarketContext, Scene
from .measures import ET

NAMES = {"$TICK": "NYSE TICK", "$ADD": "NYSE advancers minus decliners", "$TRIN": "NYSE TRIN", "$VOLD": "NYSE net volume",
         "$UVOL": "NYSE up volume", "$DVOL": "NYSE down volume", "$VOLSPD": "S&P 500 members' net volume"}
CHECKED = tuple(NAMES)
DERIVED = {"$TRIN": ("$UVOL", "$DVOL")}
# What Schwab serves of these during their own session: a $TICK floored at zero, $UVOL and $DVOL in shares where
# their history is in thousands, a $TRIN built from those, and no $VOLD or $VOLSPD at all.
SAME_DAY_WRONG = ("$TICK", "$TRIN", "$UVOL", "$DVOL", "$VOLD", "$VOLSPD")
# The saved sessions from 2026-08-10 to 2026-09-25 each stay within 17 times the others' size at every minute
# (net volume on 2026-09-18, a quarterly expiry); 2026-09-28's up and down volume were 25,000 times it and more.
SIZE_OFF = 100.0
# Those sessions' $TICK repeated one reading on at most a fifth of its minutes, and $TRIN, served to two
# decimals, on at most half of them once it had ten and never on five of its first minutes; 2026-09-28's $TICK
# read zero on each of its first five minutes and on two minutes in three all day.
STUCK_SHARE = 0.5
STUCK_OVER_USUAL = 3.0
STUCK_MIN_READINGS = 5


@dataclass
class CheckedMarket(MarketContext):
    """A market context with the series that failed the check taken out, and why each did."""
    implausible: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Readings:
    size: float                 # the median of the readings' sizes
    stuck: float                # the share of the readings at the most repeated value
    most: float                 # that value
    repeats: int                # how many readings were at it
    n: int
    positive: bool              # every reading above zero


def _readings(mk: MarketContext | None, symbol: str, then: datetime) -> _Readings | None:
    values = mk.between(symbol, session_open(then), then) if mk is not None else []
    if not values:
        return None
    most, count = Counter(values).most_common(1)[0]
    return _Readings(statistics.median(abs(v) for v in values), count / len(values), most, count, len(values), min(values) > 0)


def _times(ratio: float) -> str:
    return f"{ratio:,.0f} times" if ratio >= 10 else f"{ratio:.1f} times"


def _judge(symbol: str, own: _Readings | None, others: list[_Readings]) -> str | None:
    """Why ``own`` does not read like ``others``, the same series over the same minutes; None when it does, or
    when there are too few others to say."""
    if own is None or len(others) < SAME_CLOCK_MIN_SESSIONS:
        return None
    name, of = f"{NAMES[symbol]} ({symbol})", f"the last {len(others)} sessions"
    usual = statistics.median(o.size for o in others)
    if usual > 0 and own.size > SIZE_OFF * usual:
        return f"{name} reads {_times(own.size / usual)} its usual size at these minutes on {of}: not the series they had"
    if all(o.positive for o in others) and own.size * SIZE_OFF < usual:
        size = f"{_times(usual / own.size)} smaller than" if own.size else "zero against"
        return f"{name} reads {size} its usual size at these minutes on {of}: not the series they had"
    usual_stuck = statistics.median(o.stuck for o in others)
    if own.repeats >= STUCK_MIN_READINGS and own.stuck > STUCK_SHARE and own.stuck > STUCK_OVER_USUAL * usual_stuck:
        return (f"{name} read {own.most:g} on {own.repeats} of its {own.n} minutes, where {of} repeated one reading "
                f"on a median {usual_stuck:.0%} of theirs: a stuck series")
    return None


def _drop(mk: MarketContext | None, why: dict[str, str]) -> MarketContext | None:
    if mk is None or not why:
        return mk
    return CheckedMarket({s: v for s, v in mk.known.items() if s not in why}, {s: b for s, b in mk.bars.items() if s not in why},
                         implausible=why)


def _with_derived(why: dict[str, str]) -> dict[str, str]:
    for symbol, sources in DERIVED.items():
        failed = [s for s in sources if s in why]
        if failed and symbol not in why:
            why[symbol] = (f"{NAMES[symbol]} ({symbol}) is Schwab's advancers over decliners divided by {' over '.join(sources)}, "
                           f"and {why[failed[0]]}")
    return why


def same_day_wrong(symbol: str) -> str:
    return (f"{NAMES[symbol]} ({symbol}) is served wrong during its own session and put right only after it, "
            f"so today's is never read")


def checked(scene: Scene) -> Scene:
    """``scene`` with today's SAME_DAY_WRONG series and each breadth series that fails the check taken out of
    today's market context, and each prior session's taken out where it fails; today's reasons ride on
    ``scene.market.implausible`` (left_out)."""
    today_why = {symbol: same_day_wrong(symbol) for symbol in SAME_DAY_WRONG}
    if not scene.prior_markets:
        return replace(scene, market=_drop(scene.market, today_why))
    clock = scene.now.astimezone(ET).time()
    thens = {day: datetime.combine(date.fromisoformat(day), clock, tzinfo=ET) for day in scene.prior_markets}
    prior_why: dict[str, dict[str, str]] = {day: {} for day in scene.prior_markets}
    for symbol in CHECKED:
        got = {day: r for day, mk in scene.prior_markets.items() if (r := _readings(mk, symbol, thens[day])) is not None}
        for day, r in got.items():
            if why := _judge(symbol, r, [o for d, o in got.items() if d != day]):
                prior_why[day][symbol] = why
        passed = [r for day, r in got.items() if symbol not in prior_why[day]]
        if symbol not in today_why and (why := _judge(symbol, _readings(scene.market, symbol, scene.now), passed)):
            today_why[symbol] = why
    return replace(scene, market=_drop(scene.market, _with_derived(today_why)),
                   prior_markets={day: _drop(mk, _with_derived(prior_why[day])) for day, mk in scene.prior_markets.items()})


def left_out(mk: MarketContext | None, symbol: str) -> str | None:
    """Why ``symbol`` is not in today's market context when the check took it out; None otherwise."""
    return mk.implausible.get(symbol) if isinstance(mk, CheckedMarket) else None
