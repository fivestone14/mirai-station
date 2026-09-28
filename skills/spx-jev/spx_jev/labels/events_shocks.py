"""The events and shocks family: the scheduled event clock and what releases did (context.event_clock,
event.*), the morning brief and headlines (news.*), and unscheduled bursts seen through the tape (shock.*).

* The calendar labels read calendar/events.json (events.py) for today: its releases before the open,
  in the session and the Fed's speakers. Past the calendar's last kept day they are omitted, since an
  empty day there is unknown rather than quiet. A row whose date follows a rule is worded 'expected'.
* A burst is a 5-minute move from the settled open larger than the biggest 5-minute move of the hour to
  the same minute (SHOCK_LOOKBACK_MIN), or from the settled open when that is shorter, on every one of up to
  the last 20 sessions, each in its own morning ruler (ranks.rank_sessions, needing SAME_CLOCK_MIN_SESSIONS of them): a shock for its clock, never against
  a fixed cut, and the same rule whether 10 or 20 sessions are on file. Ranked against each session's biggest move of the hour rather than
  its same five minutes, since a read looks back over an hour of windows: one in twenty of those would
  top the same five minutes on every session by chance alone, and the shock would be awake on three
  reads in four.
* A reaction (event.reaction, event.statement_and_presser) is measured in the normal-day sigma, the
  median morning anchor of the prior sessions, because an event swells today's own anchor; its size is
  ranked against the same minutes on the prior sessions, event or not, each in its own ruler.
* What moved with a burst (shock.cross_asset) is ranked against the same five minutes on the prior
  sessions, each group's link to the index fitted over the hour before on each day as it is today.

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``; the
headline feed is DARK.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .. import events
from ..cuts import (BRIEF_CONF_MIN, BRIEF_DIR_MIN, EVENT_DIGEST_MIN, EVENT_DUE_MIN, FOLLOW_ON_MIN, GAP_HALF_SHARE, MIN_RANK_SESSIONS,
                    NIGHT_RANK_COUNT, REACTION_EXTEND_SIGMA, SAME_CLOCK_MIN_SESSIONS, SHOCK_FRESH_MIN,
                    SHOCK_LOOKBACK_MIN, SPEAKER_WINDOW_MIN, TICK_BURST_PCT, WINDOW_10_MIN)
from ..events import Event
from ..market_context import SYMBOLS
from ..state_builder import MarketContext, Scene, load_jsonl
from .gap_open import gap_rank
from .label_set import LabelSet
from .measures import (ET, ONE_MINUTE, bar_time, bars_finished_between, close_at, is_num, minute_of_day, move_size, session_extremes,
                       settled_open)
from .ranks import SameClockRank, fifth_side, rank_days, rank_sessions, same_clock_values, tick_bands_by_minute, tick_bursts
from .rulers import NO_ANCHOR, SigmaRuler, normal_day_sigma, ruled, sigma_anchor
from .words import pct, plural, sig

LABELS = ("context.event_clock", "event.reaction", "event.release_clock_10m", "event.statement_and_presser",
          "news.morning_brief", "news.intraday_headline", "shock.burst", "shock.cross_asset", "shock.vs_day_range")
GATES = ("event_clock", "release_in_lane", "shock_state", "move_reaction_path", "news_headline")
DARK = {"news.intraday_headline": "no headline feed writes into state (a right-eye or Nexus writer is not built)"}

CALENDAR_LABELS = ("context.event_clock", "event.release_clock_10m", "event.reaction", "event.statement_and_presser")
CALENDAR_GATES = ("event_clock", "release_in_lane", "move_reaction_path")
SHOCK_LABELS = ("shock.burst", "shock.vs_day_range", "shock.cross_asset")

REACTION_MIN = 15           # a first reaction is the 15 minutes after its start (the statement's, the release's, the burst's)
FOLLOW_ON = {"FOMC_PRESSER": "FOMC"}   # a follow-on and the release it follows, when due within FOLLOW_ON_MIN of it
SPEECH_MIN = 60             # a speaker row with no end time is taken to run an hour, remarks and questions
BURST_MIN = 5
BASELINE_MIN = 60           # the hour before a burst, over which a market's link to the index is fitted
FEED_MAX_AGE_MIN = 2        # a market value older than this at a burst's edge is a feed that had stopped
LINK_MIN_MINUTES = 30       # fewest minutes in the hour before a burst to fit a market's link to the index
DEFENSIVES = ("XLP", "XLU", "XLV")
BRIEF_SUBDIR = Path("market_expectation")
NOON = 12                   # the opening lane's speaker clause speaks of the morning


# ----------------------------------------------------------------------------- the whole family

def build_events_shocks_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    anchor = sigma_anchor(scene)
    windows = judged_windows(scene, anchor.points, EVENT_DIGEST_MIN) if anchor else []
    bursts = shock_bursts(windows)
    today = scene.now.astimezone(ET).date()
    why = events.uncovered(today)
    if why:
        for path in CALENDAR_LABELS:
            ls.omit(path, why)
        for qid in CALENDAR_GATES:
            ls.sleep(qid, why)
        day_events = None
    else:
        day_events = events.session_rows(today)
        _event_clock(scene, day_events, ls)
        _release_clock_10m(scene, day_events, ls)
        _reaction(scene, day_events, bursts, ls)
        _statement_and_presser(scene, day_events, ls)
    _morning_brief(scene, anchor, ls)
    _shocks(scene, anchor, windows, bursts, day_events, ls)
    ls.sleep("news_headline", DARK["news.intraday_headline"])
    return ls


# ----------------------------------------------------------------------------- the calendar

def _hm(t: datetime) -> str:
    return t.astimezone(ET).strftime("%H:%M")


def _minutes_between(a: datetime, b: datetime) -> int:
    """Whole minutes from ``a`` to ``b``, rounded up, so a shown 60 is never past a 60-minute window."""
    return math.ceil((b - a).total_seconds() / 60.0)


def _at_a_moment(e: Event) -> bool:
    """A row that happens at a moment of the session: not a release before the open, not a close."""
    return e.tier != events.PRE_OPEN and e.kind not in events.AT_THE_CLOSE


def _end(e: Event) -> datetime | None:
    return e.end or (e.start + timedelta(minutes=SPEECH_MIN) if e.tier == events.FED_SPEAKER else None)


def _what(e: Event) -> str:
    return f"{e.words}'s remarks" if e.tier == events.FED_SPEAKER else e.words


def _questions(e: Event) -> str:
    return " with audience questions" if e.q_and_a else ""


def _is_release(e: Event) -> bool:
    return e.tier in (events.PRE_OPEN, events.DATA_10AM, events.DATA_2PM) or e.kind == "FOMC"


def _ahead(e: Event) -> str:
    """How the row reads while it is still to come: '... comes out at 10:00', '... is expected at 10:00'."""
    if not e.verified:
        return f"{e.words} is expected at {_hm(e.start)}"
    if e.tier == events.FED_SPEAKER:
        return f"{e.words} speaks at {_hm(e.start)}{_questions(e)}"
    return f"{e.words} {'comes out' if _is_release(e) else 'is due'} at {_hm(e.start)}"


def _past(e: Event) -> str:
    if not e.verified:
        return f"{e.words} was expected at {_hm(e.start)}"
    if e.tier == events.FED_SPEAKER:
        return f"{e.words} spoke at {_hm(e.start)}"
    return f"{e.words} {'came out' if _is_release(e) else 'began'} at {_hm(e.start)}"


def _event_clock(scene: Scene, day_events: list[Event], ls: LabelSet) -> None:
    """Where this moment sits against every row of today's calendar, each with the window its question
    judges it by: the due window ahead, the digest window behind, the speaker window for the Fed's speakers."""
    now = scene.now
    clauses, live = [], False
    for e in day_events:
        end = _end(e)
        if e.tier == events.PRE_OPEN:
            clauses.append(f"{_past(e)}, before the open")
            live = live or _minutes_between(e.start, now) <= EVENT_DIGEST_MIN
        elif end is not None and e.start <= now < end and e.tier == events.FED_SPEAKER:
            clauses.append(f"{e.words} has been speaking since {_hm(e.start)}{_questions(e)}, inside the {SPEAKER_WINDOW_MIN}-minute "
                           f"speaker window")
            live = True
        elif end is not None and e.start <= now < end:
            clauses.append(f"{e.words} is under way until {_hm(end)}, inside the {EVENT_DUE_MIN}-minute due window")
            live = True
        elif e.start > now:
            n = _minutes_between(now, e.start)
            cut = SPEAKER_WINDOW_MIN if e.tier == events.FED_SPEAKER else EVENT_DUE_MIN
            name = "speaker" if e.tier == events.FED_SPEAKER else "due"
            clauses.append(f"{_ahead(e)}, {plural(n, 'minute')} from now, {'inside' if n <= cut else 'beyond'} the {cut}-minute {name} window")
            live = True
        else:
            n = _minutes_between(e.start, now)
            clauses.append(f"{_past(e)}, {plural(n, 'minute')} ago, {'inside' if n <= EVENT_DIGEST_MIN else 'beyond'} "
                           f"the {EVENT_DIGEST_MIN}-minute digest window")
            live = live or n <= EVENT_DIGEST_MIN
    if not day_events:
        clauses.append("nothing is scheduled today")
    elif all(e.tier == events.PRE_OPEN for e in day_events):
        clauses.append("nothing is scheduled in the session")
    quiet = events.in_quiet_period(now.astimezone(ET).date())
    clauses.append(f"the Fed {'is' if quiet else 'is not'} in its pre-meeting quiet period")
    ls.put("context.event_clock", "; ".join(clauses))
    if not day_events:
        ls.sleep("event_clock", "nothing is on the event calendar today")
    elif not live:
        ls.sleep("event_clock", f"every scheduled event today came out more than {EVENT_DIGEST_MIN} minutes ago")
    else:
        ls.wake("event_clock")


def _release_clock_10m(scene: Scene, day_events: list[Event], ls: LabelSet) -> None:
    """The releases and speakers starting within WINDOW_10_MIN either side of now, and whether a Fed speaker
    is speaking or due this morning."""
    now, w = scene.now, WINDOW_10_MIN
    moments = [e for e in day_events if _at_a_moment(e)]
    clauses = []
    for e in moments:
        if now < e.start <= now + timedelta(minutes=w):
            clauses.append(f"{_ahead(e)}, {plural(_minutes_between(now, e.start), 'minute')} from now, inside this {w}-minute window")
        elif now - timedelta(minutes=w) <= e.start <= now:
            clauses.append(f"{_past(e)}, {plural(_minutes_between(e.start, now), 'minute')} ago, inside the last {w} minutes")
    inside = bool(clauses)
    if not inside:
        later = [e for e in moments if e.start > now]
        clauses.append(f"nothing scheduled starts within {w} minutes of now" +
                       (f"; next, {_ahead(later[0])}, {plural(_minutes_between(now, later[0].start), 'minute')} from now"
                        if later else "; nothing more is scheduled in the session"))
    morning = now.astimezone(ET).hour < NOON
    speakers = [e for e in moments if e.tier == events.FED_SPEAKER and now < _end(e)
                and (e.start.astimezone(ET).hour < NOON or not morning)]
    clauses += [f"{e.words} has been speaking since {_hm(e.start)}{_questions(e)}" for e in speakers
                if e.start < now - timedelta(minutes=w)]
    if not speakers:
        clauses.append("no Fed speaker is scheduled this morning" if morning else "no more Fed speakers are scheduled today")
    ls.put("event.release_clock_10m", "; ".join(clauses))
    if inside:
        ls.wake("release_in_lane")
    else:
        ls.sleep("release_in_lane", f"nothing scheduled is due within {w} minutes or started within the last {w}")


# ----------------------------------------------------------------------------- reactions, in the normal-day sigma

def _nds_words(x: float) -> str:
    return f"{abs(x):.2f} normal-day sigma"


def _window_rank(scene: Scene, size: float, start: datetime, end: datetime) -> tuple[SameClockRank | None, str | None]:
    """A move's ``size`` from ``start`` to ``end`` against the same minutes on the prior sessions, event or not, each
    in its own ruler (ranks.rank_sessions): how the reactions and the press conference are judged."""
    minutes = (end - start).total_seconds() / 60.0
    base = same_clock_values(replace(scene, now=end), lambda bars, then, sigma: move_size(bars, then, sigma, minutes))
    return rank_sessions(abs(size), base, f"a move from {_hm(start)} to {_hm(end)}")


def _larger(rank: SameClockRank, minutes: str = "the same minutes") -> str:
    """A reaction's rank in words, saying the prior sessions were each sized in their own sigma, not the normal-day sigma
    today's move is given in."""
    return f"larger than {rank.higher_than} of the last {rank.of} sessions over {minutes}, each in its own sigma, {rank.band}"


def _reaction_kind(rows: list[Event]) -> str:
    """What started a reaction: the rows due at its start, or none for a burst at no scheduled time."""
    if not rows:
        return "this was an unscheduled burst"
    kinds = dict.fromkeys("scheduled Fed remarks" if e.tier == events.FED_SPEAKER else
                          "a scheduled Fed event" if e.kind.startswith("FOMC") or e.kind.startswith("FED_") else
                          "scheduled economic data" for e in rows)
    return "this was " + " and ".join(kinds)


def _reaction(scene: Scene, day_events: list[Event], bursts: list[Burst], ls: LabelSet) -> None:
    """What price did since the first reaction to the latest release, Fed remarks or burst whose first
    REACTION_MIN minutes are over, within the digest window: a burst at a scheduled time is that event's."""
    path, gate = "event.reaction", "move_reaction_path"
    nds = normal_day_sigma(scene)
    if nds is None:
        why = f"needs {MIN_RANK_SESSIONS} prior sessions with a morning anchor for the normal-day sigma"
        ls.omit(path, why)
        ls.sleep(gate, why)
        return
    now, first_min = scene.now, timedelta(minutes=REACTION_MIN)
    earliest, latest = now - timedelta(minutes=EVENT_DIGEST_MIN), now - first_min
    scheduled = [e for e in day_events if _at_a_moment(e)]
    starts: list[tuple[datetime, Event | None]] = [(e.start, e) for e in scheduled if earliest <= e.start <= latest]
    starts += [(b.start, None) for b in bursts if earliest <= b.start <= latest
               and not any(_during_first_reaction(b, e) for e in scheduled)]
    if not starts:
        why = (f"no release, Fed remarks or burst in the last {EVENT_DIGEST_MIN} minutes whose first "
               f"{REACTION_MIN} minutes are over")
        ls.omit(path, why)
        ls.sleep(gate, why)
        return
    start, e = max(starts, key=lambda s: s[0])
    due = [x for x in scheduled if x.start == start] if e is not None else []
    p0, p1 = close_at(scene.bars, start), close_at(scene.bars, start + first_min)
    if p0 is None or p1 is None:
        why = f"no finished bars around the reaction's start at {_hm(start)}"
        ls.omit(path, why)
        ls.sleep(gate, why)
        return
    first = (p1 - p0) / nds
    rank, no_rank = _window_rank(scene, first, start, start + first_min)
    if rank is None:
        ls.omit(path, no_rank)
        ls.sleep(gate, no_rank)
        return
    named = " and ".join(_what(x) + ("" if x.verified else " (expected at that time)") for x in due)
    head = f"the reaction starts at {_hm(start)} with {named or 'a sudden burst'}"
    lead = next((r for r in scheduled if r.kind == FOLLOW_ON.get(e.kind)
                 and timedelta(0) < e.start - r.start <= timedelta(minutes=FOLLOW_ON_MIN)), None) if e else None
    if lead is not None:
        r0, r1 = close_at(scene.bars, lead.start), close_at(scene.bars, lead.start + first_min)
        if r0 is not None and r1 is not None:
            head += f" ({lead.words} at {_hm(lead.start)} moved price {_nds_words((r1 - r0) / nds)} in its first {REACTION_MIN} minutes)"
    size = _larger(rank, f"the same {REACTION_MIN} minutes")
    if first == 0:
        text = f"{head}; in its first {REACTION_MIN} minutes price did not move, {size}"
    else:
        side = 1 if first > 0 else -1
        text = (f"{head}; in its first {REACTION_MIN} minutes price {'rose' if side > 0 else 'fell'} {_nds_words(first)}, "
                f"{size}; since then {_path_after(scene.spot, p0, p1, side, nds)}")
    ls.put(path, f"{text}; {_reaction_kind(due)}")
    if rank.band != "bottom third":
        ls.wake(gate)
    else:
        ls.sleep(gate, f"the first reaction moved {_nds_words(first)}, {size}")


def _during_first_reaction(burst: Burst, e: Event) -> bool:
    """Whether a burst overlaps the first REACTION_MIN minutes after a scheduled row: then it is that row's reaction."""
    return burst.start < e.start + timedelta(minutes=REACTION_MIN) and burst.end > e.start


def _path_after(spot: float, p0: float, p1: float, side: int, nds: float) -> str:
    """Where price went after a first reaction from ``p0`` to ``p1``: further, some of it given back, or back through its start."""
    further = (spot - p1) * side / nds
    if further > 0:
        verdict = "past" if further > REACTION_EXTEND_SIGMA else "short of"
        return (f"it pushed {_nds_words(further)} further the same way, {verdict} the {REACTION_EXTEND_SIGMA:.2f} normal-day sigma "
                f"extension rule, and has given back none of it")
    if (spot - p0) * side < 0:
        return (f"it has given back all of it and crossed back through where the reaction started, now "
                f"{_nds_words((spot - p0) / nds)} the other side of it")
    return f"it has given back {_share_given_back((p1 - spot) / (p1 - p0))}, and has not crossed back through where the reaction started"


def _share_given_back(share: float) -> str:
    """A share given back against the half line; the shown percent never crosses the line its verdict names."""
    if share <= 0:
        return "none of it, within the half line"
    if share > GAP_HALF_SHARE:
        return f"{pct(max(share, GAP_HALF_SHARE + 0.01))} of it, past the half line"
    return f"{pct(share)} of it, within the half line"


def _statement_and_presser(scene: Scene, day_events: list[Event], ls: LabelSet) -> None:
    """On a Fed decision day: the statement's first REACTION_MIN minutes, then the move since the press conference began."""
    path = "event.statement_and_presser"
    statement = next((e for e in day_events if e.kind == "FOMC"), None)
    presser = next((e for e in day_events if e.kind == "FOMC_PRESSER"), None)
    now, first_min = scene.now, timedelta(minutes=REACTION_MIN)
    if statement is None:
        ls.omit(path, "no Fed rate decision today")
        return
    if presser is None:
        ls.omit(path, "no press conference follows today's Fed decision")
        return
    if now <= presser.start:
        ls.omit(path, f"the Fed chair's press conference starts at {_hm(presser.start)}, after this read")
        return
    nds = normal_day_sigma(scene)
    if nds is None:
        ls.omit(path, f"needs {MIN_RANK_SESSIONS} prior sessions with a morning anchor for the normal-day sigma")
        return
    s0, s1, p0 = (close_at(scene.bars, t) for t in (statement.start, statement.start + first_min, presser.start))
    if s0 is None or s1 is None or p0 is None:
        ls.omit(path, "no finished bars at the statement and the press conference")
        return
    moved, since = (s1 - s0) / nds, (scene.spot - p0) / nds
    said_rank, no_rank = _window_rank(scene, moved, statement.start, statement.start + first_min)
    went_rank, no_went = _window_rank(scene, since, presser.start, now)
    if said_rank is None or went_rank is None:
        ls.omit(path, no_rank or no_went)
        return
    said = ("did not move" if moved == 0 else f"{'rose' if moved > 0 else 'fell'} {_nds_words(moved)}")
    quiet = said_rank.band == "bottom third"
    way = "" if quiet or since == 0 else ", the same way" if (since > 0) == (moved > 0) else ", the other way"
    went = "has not moved" if since == 0 else f"has {'risen' if since > 0 else 'fallen'} {_nds_words(since)}{way}"
    ls.put(path, f"in the {REACTION_MIN} minutes after the Fed's {_hm(statement.start)} statement price {said}, {_larger(said_rank)}; "
                 f"since the chair's press conference began at {_hm(presser.start)} price {went}, {_larger(went_rank)}")


# ----------------------------------------------------------------------------- the morning brief

def _morning_brief(scene: Scene, anchor: SigmaRuler | None, ls: LabelSet) -> None:
    """The morning news brief's lean against this morning's gap: the first brief of the day written for the
    morning, from the learning log, which a midday redive never overwrites."""
    path = "news.morning_brief"
    if scene.state_dir is None:
        ls.omit(path, "no state folder to read the morning brief from")
        return
    name = BRIEF_SUBDIR / f"learning-{scene.day}.jsonl"
    briefs = sorted((b for b in load_jsonl(Path(scene.state_dir) / name)
                     if b.get("kind") == "brief" and b.get("reason") == "morning" and isinstance(b.get("ts"), str)),
                    key=lambda b: b["ts"])
    if not briefs:
        ls.omit(path, f"no morning brief in {name}")
        return
    brief, written = briefs[0], datetime.fromisoformat(briefs[0]["ts"])
    if written > scene.now:
        ls.omit(path, f"the morning brief was written at {_hm(written)}, after this read")
        return
    overall = brief.get("overall") if isinstance(brief.get("overall"), dict) else {}
    lean, confidence = overall.get("direction"), overall.get("confidence")
    if not is_num(lean) or not is_num(confidence):
        ls.omit(path, "the morning brief carries no direction and confidence")
        return
    opened, prior = settled_open(scene.bars), scene.row.get("prior_close")
    if anchor is None:
        ls.omit(path, NO_ANCHOR)
        return
    if opened is None:
        ls.omit(path, "the settled open (the close of the 09:34 bar) has not finished yet")
        return
    if not is_num(prior):
        ls.omit(path, "row carries no prior close")
        return
    gap = (opened - float(prior)) / anchor.points
    rank, no_rank = gap_rank(scene, gap)
    if rank is None:
        ls.omit(path, no_rank)
        return
    leans = f"leans {'up' if lean > 0 else 'down'} {abs(lean):g}" if lean else "takes no side, 0"
    no_view = abs(lean) < BRIEF_DIR_MIN or confidence < BRIEF_CONF_MIN
    gap_past = rank.band != "bottom third"
    if no_view:
        verdict = "so the brief counts as no view"
    elif not gap_past:
        verdict = "so the brief takes a side the gap did not"
    elif (lean > 0) == (gap > 0):
        verdict = "so the brief leans the way the gap went"
    else:
        verdict = "so the brief leans against the way the gap went"
    ls.put(path, f"the {_hm(written)} morning brief {leans} on a -1 to +1 scale, "
                 f"{'under' if abs(lean) < BRIEF_DIR_MIN else 'past'} the {BRIEF_DIR_MIN:g} direction floor, with confidence "
                 f"{confidence:g}, {'under' if confidence < BRIEF_CONF_MIN else 'past'} the {BRIEF_CONF_MIN:g} confidence floor; "
                 f"this morning's gap was {sig(abs(gap))} {'up' if gap >= 0 else 'down'}{' (ruler estimated)' if anchor.estimated else ''}, "
                 f"larger than {rank.higher_than} of the last {rank.of} days' gaps, {rank.band}, "
                 f"{'a real gap' if gap_past else 'no real gap'}, {verdict}")


# ----------------------------------------------------------------------------- bursts

@dataclass(frozen=True)
class Burst:
    """A judged 5-minute window: from the close at ``start`` to the close of the bar finishing at ``end``,
    its move in sigma, and its ``rank`` against the prior sessions' biggest 5-minute move in the hour to ``end``."""
    start: datetime
    end: datetime
    from_close: float
    to_close: float
    high: float
    low: float
    move: float
    rank: SameClockRank
    passed: bool

    @property
    def side(self) -> int:
        return 1 if self.move > 0 else -1


@dataclass(frozen=True)
class _Closes:
    """A session's closes by when each bar finished, so the close at a moment (what measures.close_at finds) is
    a lookup: every window of a read asks each prior session for its closes, and a scan of its bars each
    time would cost a second a read."""
    ends: list[datetime]
    closes: list[float]

    @classmethod
    def of(cls, bars: list[dict]) -> _Closes:
        return cls([bar_time(b) + ONE_MINUTE for b in bars], [float(b["close"]) for b in bars])

    def at(self, t: datetime) -> float | None:
        k = bisect.bisect_right(self.ends, t)
        return self.closes[k - 1] if k else None

    def between(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """The bars that finished in (start, end], as (when, close)."""
        lo, hi = bisect.bisect_right(self.ends, start), bisect.bisect_right(self.ends, end)
        return list(zip(self.ends[lo:hi], self.closes[lo:hi]))


@dataclass(frozen=True)
class _PriorSession:
    """A prior session a rank in sigma may use (ranks.rank_days): its day, closes and own morning ruler in points."""
    day: str
    closes: _Closes
    sigma: float

    def at(self, t: datetime) -> datetime:
        """``t``'s clock minute on this session, market time."""
        return datetime.combine(date.fromisoformat(self.day), t.astimezone(ET).time(), tzinfo=ET)


def _prior_sessions(scene: Scene) -> list[_PriorSession]:
    """The prior sessions a same-clock rank reads, newest first; one without a ruler is left out, as
    ranks.same_clock_values leaves it out."""
    return [_PriorSession(day, _Closes.of(scene.prior_bars[day]), r.points) for day in rank_days(scene)
            if (r := scene.prior_rulers.get(day)) is not None]


def _five_minute_moves(p: _PriorSession, settled: time) -> dict[int, float]:
    """|the 5-minute move| to the finish of each of a prior session's bars, from the settled open on, in its own
    sigma, by the minute of day it finished."""
    out = {}
    for end, close in zip(p.closes.ends, p.closes.closes):
        start = end - timedelta(minutes=BURST_MIN)
        if start.astimezone(ET).time() >= settled and (a := p.closes.at(start)) is not None:
            out[minute_of_day(end.astimezone(ET))] = abs(close - a) / p.sigma
    return out


def _hour_biggest(moves: list[dict[int, float]], end: datetime) -> list[float]:
    """Each prior session's biggest 5-minute move that finished in the SHOCK_LOOKBACK_MIN minutes to ``end``'s clock."""
    last = minute_of_day(end.astimezone(ET))
    out = []
    for m in moves:
        hour = [v for k in range(last - SHOCK_LOOKBACK_MIN + 1, last + 1) if (v := m.get(k)) is not None]
        if hour:
            out.append(max(hour))
    return out


def judged_windows(scene: Scene, anchor: float, lookback_min: int) -> list[Burst]:
    """Every 5-minute window from the settled open that ended in the last ``lookback_min`` minutes, its move
    ranked against the prior sessions' biggest 5-minute move in the hour to the same minute and passed when it
    beats every one; a window short of SAME_CLOCK_MIN_SESSIONS of them is not judged."""
    bars, now = scene.bars, scene.now
    settled = scene.session_open + timedelta(minutes=BURST_MIN)
    moves = [_five_minute_moves(p, settled.astimezone(ET).time()) for p in _prior_sessions(scene)]
    out = []
    for b in bars:
        end = bar_time(b) + ONE_MINUTE
        start = end - timedelta(minutes=BURST_MIN)
        if end <= now - timedelta(minutes=lookback_min) or start < settled:
            continue
        win, from_close = bars_finished_between(bars, start, end), close_at(bars, start)
        if len(win) < BURST_MIN or from_close is None:
            continue
        to_close = float(win[-1]["close"])
        move = (to_close - from_close) / anchor
        rank, _ = rank_sessions(abs(move), _hour_biggest(moves, end), "")
        if rank is None:
            continue
        high, low = max(float(x["high"]) for x in win), min(float(x["low"]) for x in win)
        out.append(Burst(start, end, from_close, to_close, high, low, move, rank, rank.higher_than == rank.of))
    return out


def shock_bursts(windows: list[Burst]) -> list[Burst]:
    """The windows that passed the shock rule, overlapping ones taken as one burst, each by its largest move."""
    groups: list[list[Burst]] = []
    for w in sorted((w for w in windows if w.passed), key=lambda w: w.end):
        if groups and w.start < groups[-1][-1].end:
            groups[-1].append(w)
        else:
            groups.append([w])
    return [max(g, key=lambda w: abs(w.move)) for g in groups]


def _hour_words(scene: Scene, w: Burst) -> str:
    """A window's rank in words, naming the span its base was taken over: the hour to its minute, or from the settled
    open to it while that is shorter (_hour_biggest's hour holds no window from before the settled open)."""
    first_end = scene.session_open + timedelta(minutes=2 * BURST_MIN)
    span = ("from the settled open to that minute" if w.end - timedelta(minutes=SHOCK_LOOKBACK_MIN - 1) < first_end
            else "of the hour to that minute")
    return f"larger than the biggest five-minute move {span} on {w.rank.higher_than} of the last {w.rank.of} sessions"


def _shocks(scene: Scene, anchor: SigmaRuler | None, windows: list[Burst], bursts: list[Burst],
            day_events: list[Event] | None, ls: LabelSet) -> None:
    if anchor is None:
        for path in SHOCK_LABELS:
            ls.omit(path, NO_ANCHOR)
        ls.sleep("shock_state", NO_ANCHOR)
        return
    since = scene.now - timedelta(minutes=SHOCK_LOOKBACK_MIN)
    recent = [w for w in windows if w.end > since]
    shocks = [b for b in bursts if b.end > since]
    first_end = scene.session_open + timedelta(minutes=2 * BURST_MIN)
    if not recent and scene.now < first_end:
        why = f"the first five minutes after the settled open end at {_hm(first_end)}"
    elif not recent:
        why = (f"no five-minute window in the last {SHOCK_LOOKBACK_MIN} minutes could be judged: each is ranked against the biggest "
               f"five-minute move of the hour to its minute (from the settled open when that is shorter) on up to the last "
               f"{NIGHT_RANK_COUNT} sessions and needs "
               f"{SAME_CLOCK_MIN_SESSIONS} of them with a trusted morning ruler")
    elif not shocks:
        top = max(recent, key=lambda w: abs(w.move))
        why = (f"no five-minute move in the last {SHOCK_LOOKBACK_MIN} minutes passed the shock rule (larger than the biggest "
               f"five-minute move of the hour to that minute, or from the settled open when that is shorter, on every one of up to "
               f"the last {NIGHT_RANK_COUNT} sessions); the largest was {sig(abs(top.move))}, {_hour_words(scene, top)}")
    else:
        burst = max(shocks, key=lambda b: abs(b.move))
        ls.wake("shock_state")
        _burst_label(scene, anchor, burst, day_events, ls)
        _vs_day_range(scene, anchor, burst, ls)
        _cross_asset(scene, anchor, burst, ls)
        return
    # no shock in the lookback is the shock over, not unmeasured: its questions hold no earlier answer
    for path in SHOCK_LABELS:
        ls.omit(path, why, ended=bool(recent))
    ls.sleep("shock_state", why)


def _burst_label(scene: Scene, anchor: SigmaRuler, burst: Burst, day_events: list[Event] | None, ls: LabelSet) -> None:
    now = scene.now
    est = " (ruler estimated)" if anchor.estimated else ""
    head = (f"{plural(_minutes_between(burst.start, now), 'minute')} ago price {'rose' if burst.side > 0 else 'fell'} "
            f"{sig(abs(burst.move))}{est} in {BURST_MIN} minutes, {_hour_words(scene, burst)}, past the shock rule")
    if day_events is None:
        when = "whether it came at a scheduled time is unknown: the event calendar has run out"
    else:
        at = next((e for e in day_events if _at_a_moment(e) and _during_first_reaction(burst, e)), None)
        when = (f"inside the first {REACTION_MIN} minutes after {_what(at)} at {_hm(at.start)}" if at
                else "not at a scheduled release time")
    ended = _minutes_between(burst.end, now)
    fresh = (f"inside the {SHOCK_FRESH_MIN}-minute fresh window" if ended <= SHOCK_FRESH_MIN
             else f"older than the {SHOCK_FRESH_MIN}-minute fresh window")
    given = _share_given_back((burst.to_close - scene.spot) * burst.side / abs(burst.to_close - burst.from_close))
    ended_words = f"{plural(ended, 'minute')} ago" if ended else "just now"
    ls.put("shock.burst", f"{head}; {when}; the burst ended {ended_words}, {fresh}; since then price has given back {given}")


def _from_session_extreme(up: bool):
    """How far a session's last close sits back from its session high (``up``) or low, in its own sigma: the
    distance shock.vs_day_range ranks price's distance from the shock's new extreme against."""
    def measure(bars: list[dict], _then: datetime, sigma: float | None) -> float | None:
        ext = session_extremes(bars)
        if ext is None or not sigma:
            return None
        last = float(bars[-1]["close"])
        return ((ext.high - last) if up else (last - ext.low)) / sigma
    return measure


def _vs_day_range(scene: Scene, anchor: SigmaRuler, burst: Burst, ls: LabelSet) -> None:
    """The burst's extreme against the session's high or low before it, and how far price sits back from the
    new extreme now, the burst's own or one price has pushed on to since, against how far the prior sessions
    sat from their own session extreme at this minute."""
    before = [b for b in scene.bars if bar_time(b) + ONE_MINUTE <= burst.start]
    earlier = session_extremes(before)
    if earlier is None:
        ls.omit("shock.vs_day_range", "no bars before the burst to set the day's earlier range")
        return
    up = burst.side > 0
    extreme, old = (burst.high, earlier.high) if up else (burst.low, earlier.low)
    word = "high" if up else "low"
    past = (extreme - old) * burst.side / anchor.points
    if past <= 0:
        ls.put("shock.vs_day_range", ruled(anchor, f"the shock stayed inside the day's earlier range: its {word} stopped "
                                                   f"{sig(-past)} short of the earlier session {word}"))
        return
    since = session_extremes(bars_finished_between(scene.bars, burst.start, scene.now))
    newest = since.high if up else since.low
    further = (newest - extreme) * burst.side / anchor.points
    back = (newest - scene.spot) * burst.side / anchor.points
    rank, no_rank = rank_sessions(max(back, 0.0), same_clock_values(scene, _from_session_extreme(up)),
                                  f"a distance from its session {word} at this minute")
    if rank is None:
        ls.omit("shock.vs_day_range", no_rank)
        return
    pushed = f", which price has since pushed {sig(further)} further" if round(further, 2) else ""
    where = (f"{sig(back)} {'below' if up else 'above'} that new {word}" if round(back, 2) > 0 else
             f"at or {'above' if up else 'below'} that new {word}")
    verdict = ("so it is still at the new extreme" if rank.band == "bottom third" else "so it has given the new extreme up")
    ls.put("shock.vs_day_range", ruled(anchor, f"the shock took price to a new session {word}, {sig(past)} {'over' if up else 'under'} "
                                               f"the earlier {word}{pushed}, and price is {where}, farther from it than "
                                               f"{rank.higher_than} of the last {rank.of} sessions sat from their own session {word} "
                                               f"at this minute, {rank.band}, {verdict}"))


# ----------------------------------------------------------------------------- what moved with a burst

def _change(symbol: str, a: float, b: float, spot: float, anchor: float) -> float:
    """A move from ``a`` to ``b`` in the unit its rank reads: a yield in basis points, anything else as the
    same return on the index, in its sigma."""
    return (b - a) * 100.0 if symbol == "$TNX" else (b / a - 1.0) * spot / anchor


def _link(index: _Closes, mk: MarketContext, symbol: str, before: datetime, spot: float, anchor: float) -> tuple[float | None, int]:
    """How far ``symbol`` moved per sigma of the index, minute by minute over the hour before ``before``
    (the slope through zero), and how many minutes it rests on; None under LINK_MIN_MINUTES of them."""
    pairs = []
    for t, i1 in index.between(before - timedelta(minutes=BASELINE_MIN), before):
        i0 = index.at(t - ONE_MINUTE)
        s0, s1 = mk.last(symbol, t - ONE_MINUTE, FEED_MAX_AGE_MIN), mk.last(symbol, t, FEED_MAX_AGE_MIN)
        if i0 is not None and s0 and s1 is not None:
            pairs.append(((i1 - i0) / anchor, _change(symbol, s0, s1, spot, anchor)))
    across = sum(x * x for x, _ in pairs)
    if len(pairs) < LINK_MIN_MINUTES or across == 0:
        return None, len(pairs)
    return sum(x * y for x, y in pairs) / across, len(pairs)


@dataclass(frozen=True)
class _GroupMoves:
    """One session's five minutes, each group's move beyond its link to the index: the index's ``side``, the
    ten-year yield in basis points, semiconductors and the defensive funds' mean in index sigma, and how many
    sector funds moved the index's way. A group left unmeasured is None, with why under its name in ``gaps``."""
    side: int
    rates: float | None
    semis: float | None
    shelter: float | None
    with_it: int | None
    gaps: dict[str, str]


def _group_moves(index: _Closes, mk: MarketContext, start: datetime, end: datetime, anchor: float) -> _GroupMoves | None:
    """What moved with the index from ``start`` to ``end``; None without the index's closes at both."""
    i0, i1 = index.at(start), index.at(end)
    if i0 is None or i1 is None:
        return None
    move = (i1 - i0) / anchor
    gaps: dict[str, str] = {}

    def beyond(symbol: str) -> float | None:
        a, b = mk.last(symbol, start, FEED_MAX_AGE_MIN), mk.last(symbol, end, FEED_MAX_AGE_MIN)
        if not a or b is None:
            gaps[symbol] = f"{symbol} has no value within {FEED_MAX_AGE_MIN} minutes of the burst's start and end"
            return None
        beta, n = _link(index, mk, symbol, start, i0, anchor)
        if beta is None:
            gaps[symbol] = f"needs {LINK_MIN_MINUTES} minutes of {symbol} in the hour before the burst to fit its link to the index, has {n}"
            return None
        return _change(symbol, a, b, i0, anchor) - beta * move

    side = 1 if move > 0 else -1
    rates, semis, defensive = beyond("$TNX"), beyond("SMH"), [beyond(x) for x in DEFENSIVES]
    sectors = [(a, b) for x in SYMBOLS["sectors"] if (a := mk.last(x, start, FEED_MAX_AGE_MIN)) and (b := mk.last(x, end, FEED_MAX_AGE_MIN)) is not None]
    if len(sectors) < len(SYMBOLS["sectors"]):
        gaps["sectors"] = (f"needs all {len(SYMBOLS['sectors'])} sector funds with a value at the burst's start and end, "
                           f"have {len(sectors)}")
    return _GroupMoves(side, rates, semis, None if None in defensive else statistics.fmean(defensive),
                       sum(1 for a, b in sectors if (b - a) * side > 0) if "sectors" not in gaps else None, gaps)


def _prior_group_moves(scene: Scene, burst: Burst) -> list[_GroupMoves]:
    """The same five minutes as ``burst`` on each prior session with a market context, in its own ruler, newest first."""
    out = []
    for p in _prior_sessions(scene):
        mk = scene.prior_markets.get(p.day)
        if mk is not None and (m := _group_moves(p.closes, mk, p.at(burst.start), p.at(burst.end), p.sigma)) is not None:
            out.append(m)
    return out


def _more_than(rank: SameClockRank) -> str:
    return f"more than on {rank.higher_than} of the last {rank.of} sessions over the same five minutes"


def _top_fifth(rank: SameClockRank) -> str:
    return f"{'in' if fifth_side(rank) > 0 else 'short of'} the top fifth"


def _cross_asset(scene: Scene, anchor: SigmaRuler, burst: Burst, ls: LabelSet) -> None:
    """Each group's move during the burst beyond its usual link to the index, ranked against the same five minutes on
    the prior sessions, event or not: the ten-year yield, semiconductors, the sector funds with the NYSE TICK against
    its burst bands for those minutes, the defensive funds. A group passes its rank in the top fifth. The megacaps'
    share needs their index weights, which no file carries yet. The yield's bars stop at 15:00, so without it the
    label says so and still reads the index-side groups."""
    path, mk = "shock.cross_asset", scene.market
    if mk is None:
        ls.omit(path, "no market-context snapshot today")
        return
    now = _group_moves(_Closes.of(scene.bars), mk, burst.start, burst.end, anchor.points)
    gaps = {k: v for k, v in now.gaps.items() if k != "$TNX"}
    if gaps:
        ls.omit(path, next(iter(gaps.values())))
        return
    tick_bars = mk.bars_between("$TICK", burst.start, burst.end)
    if not tick_bars:
        ls.omit(path, "no NYSE TICK bar during the burst")
        return
    reached = tick_bursts(tick_bands_by_minute(scene), tick_bars)
    if reached is None:
        ls.omit(path, f"NYSE TICK's burst bands need {SAME_CLOCK_MIN_SESSIONS} prior sessions with a TICK bar at the burst's minutes")
        return
    prior = _prior_group_moves(scene, burst)
    side = now.side
    what = "{} over the same five minutes"
    semis, no_semis = rank_sessions(now.semis * side, [m.semis * m.side for m in prior if m.semis is not None], what.format("SMH"))
    shelter, no_shelter = rank_sessions(-now.shelter * side, [-m.shelter * m.side for m in prior if m.shelter is not None],
                                        what.format("the defensive funds"))
    sectors, no_sectors = rank_sessions(now.with_it, [m.with_it for m in prior if m.with_it is not None], what.format("every sector fund"))
    if semis is None or shelter is None or sectors is None:
        ls.omit(path, no_semis or no_shelter or no_sectors)
        return
    if now.rates is None:
        rates_words = f"the ten-year yield is not measured ({now.gaps['$TNX']})"
    else:
        rates, no_rates = rank_sessions(abs(now.rates), [abs(m.rates) for m in prior if m.rates is not None], what.format("$TNX"))
        moved = (f"{'rose' if now.rates > 0 else 'fell'} {abs(now.rates):.1f} basis points beyond its usual link to the index"
                 if round(now.rates, 1) else "moved with its usual link to the index")
        rates_words = (f"the ten-year yield {moved}, not ranked ({no_rates})" if rates is None else
                       f"the ten-year yield {moved}, {_more_than(rates)}, {_top_fifth(rates)}")
    semis_moved = (f"{'rose' if now.semis > 0 else 'fell'} {sig(abs(now.semis))} beyond theirs" if round(now.semis, 2) else
                   "moved with their usual link")
    semis_way = {1: ", the shock's way", -1: ", against the shock"}.get(fifth_side(semis), "")
    semis_verdict = (f"in the top fifth{semis_way}" if fifth_side(semis) > 0 else f"in the bottom fifth{semis_way}"
                     if fifth_side(semis) < 0 else "between the top and bottom fifths")
    up, down = reached
    tick_hit = up if side > 0 else down
    tick_against = down if side > 0 else up
    tick = max(float(b["high"]) for b in tick_bars) if side > 0 else min(float(b["low"]) for b in tick_bars)
    band = f"{pct(1 - TICK_BURST_PCT)} band for those minutes"
    tick_verdict = (f"at or past its {'top' if side > 0 else 'bottom'} {band}, the shock's way" if tick_hit else
                    f"inside its {band} the shock's way, but past the other one, against the shock" if tick_against else
                    f"inside its {band} the shock's way")
    shelter_moved = (f"{'rose' if now.shelter > 0 else 'fell'} {sig(abs(now.shelter))} beyond their usual link, "
                     f"{'with' if now.shelter * side > 0 else 'against'} the shock" if round(now.shelter, 2) else
                     "moved with their usual link to the index")
    bid = fifth_side(shelter) > 0
    shelter_verdict = ("a defensive bid" if bid and side < 0 else
                       "not a defensive bid, which needs them rising against a falling index" if bid else
                       "no defensive bid")
    moved = "rose" if side > 0 else "fell"
    ls.put(path, ruled(anchor, f"during the shock {rates_words}; "
                 f"semiconductors {semis_moved}, their move the shock's way {_more_than(semis)}, {semis_verdict}; "
                 f"no megacap's share of it is measured, since their index weights are not on file; "
                 f"{now.with_it} of {len(SYMBOLS['sectors'])} sector funds {moved} with it, {_more_than(sectors)}, {_top_fifth(sectors)}, "
                 f"and NYSE TICK reached {round(tick)}, {tick_verdict}; "
                 f"the defensive funds (staples, utilities, health care) {shelter_moved}, their move against the index "
                 f"{_more_than(shelter)}, {_top_fifth(shelter)}, {shelter_verdict}"))
