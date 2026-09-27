"""The events and shocks family: the scheduled event clock and what releases did (context.event_clock,
event.*), the morning brief and headlines (news.*), and unscheduled bursts seen through the tape (shock.*).

* The calendar labels read calendar/events.json (events.py) for today: its releases before the open,
  in the session and the Fed's speakers. Past the calendar's last kept day they are omitted, since an
  empty day there is unknown rather than quiet. A row whose date follows a rule is worded 'expected'.

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. A
label listed here and not written is omitted by the registry as not built; the headline feed is DARK.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

from .. import events
from ..cuts import BRIEF_CONF_MIN, BRIEF_DIR_MIN, EVENT_DIGEST_MIN, EVENT_DUE_MIN, GAP_RULE_SIGMA, SPEAKER_WINDOW_MIN, WINDOW_10_MIN
from ..events import Event
from ..state_builder import Scene, load_jsonl
from .label_set import LabelSet
from .measures import ET, is_num, settled_open
from .rulers import SigmaRuler, sigma_anchor
from .words import plural, sig

LABELS = ("context.event_clock", "event.reaction", "event.release_clock_10m", "event.statement_and_presser",
          "news.morning_brief", "news.intraday_headline", "shock.burst", "shock.cross_asset", "shock.vs_day_range")
GATES = ("event_clock", "release_in_lane", "shock_state", "move_reaction_path", "news_headline")
DARK = {"news.intraday_headline": "no headline feed writes into state (a right-eye or Nexus writer is not built)"}

CALENDAR_LABELS = ("context.event_clock", "event.release_clock_10m")
CALENDAR_GATES = ("event_clock", "release_in_lane")
NO_RULER = "no morning sigma ruler: no diary row by 09:40, no live sigma and no VIX at the settled open"

SPEECH_MIN = 60             # a speaker row with no end time is taken to run an hour, remarks and questions
BRIEF_SUBDIR = Path("market_expectation")
NOON = 12                   # the opening lane's speaker clause speaks of the morning


# ----------------------------------------------------------------------------- the whole family

def build_events_shocks_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    anchor = sigma_anchor(scene)
    today = scene.now.astimezone(ET).date()
    through = events.covered_through()
    if through is None or today > through:
        why = (f"the event calendar (calendar/events.json) is kept only through {through}: extend it" if through else
               "the event calendar (calendar/events.json) names no last kept day (covers_through)")
        for path in CALENDAR_LABELS:
            ls.omit(path, why)
        for qid in CALENDAR_GATES:
            ls.sleep(qid, why)
    else:
        day_events = events.on_day(today)
        _event_clock(scene, day_events, ls)
        _release_clock_10m(scene, day_events, ls)
    _morning_brief(scene, anchor, ls)
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
    is due this morning."""
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
    speakers = [e for e in moments if e.tier == events.FED_SPEAKER and e.start > now - timedelta(minutes=w)
                and (e.start.astimezone(ET).hour < NOON or not morning)]
    if not speakers:
        clauses.append("no Fed speaker is scheduled this morning" if morning else "no more Fed speakers are scheduled today")
    ls.put("event.release_clock_10m", "; ".join(clauses))
    if inside:
        ls.wake("release_in_lane")
    else:
        ls.sleep("release_in_lane", f"nothing scheduled is due within {w} minutes or started within the last {w}")


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
        ls.omit(path, NO_RULER)
        return
    if opened is None:
        ls.omit(path, "the settled open (the close of the 09:34 bar) has not finished yet")
        return
    if not is_num(prior):
        ls.omit(path, "row carries no prior close")
        return
    gap = (opened - float(prior)) / anchor.points
    leans = f"leans {'up' if lean > 0 else 'down'} {abs(lean):g}" if lean else "takes no side, 0"
    no_view = abs(lean) < BRIEF_DIR_MIN or confidence < BRIEF_CONF_MIN
    gap_past = abs(gap) >= GAP_RULE_SIGMA
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
                 f"{'past' if gap_past else 'inside'} the {GAP_RULE_SIGMA:.2f} sigma gap rule, {verdict}")
