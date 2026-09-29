"""When a question is asked on a lane: the machine schedule every question carries, read by code.

A question's ``schedule`` holds one entry per lane it serves (the question doc keeps only the loading
lane's). An entry is one of:

    {"every_min": 30, "from": "10:02", "to": "15:32"}     asked on the lane's reads in the window, every
                                                        every_min minutes counted from the window's first read
    {"at": ["09:35"], "hold": true}                     asked at those reads only; after the last, its answer
                                                        is held for the rest of the day (a day constant), or,
                                                        with none from today, asked at the next read (cadence.plan)
    {"at": ["10:02"], "then": "..."}                    the same: asked, then held (re-asking when the code's
                                                        answer changes is not built)
    {"at": ["15:02", "15:32"]}                          asked at those reads only
    {"hold_until": "11:32"}                             never asked on this lane; the answer given on the
                                                        question's other lane is held here until then

``from`` may name an event instead of a clock ("after the press conference starts": from the Fed chair's
press conference that day), and ``days`` may restrict the entry to event days ("FOMC only"); both are
read from the event calendar (events.py). ``note`` is for people. Anything else stops the load, so a
schedule the code cannot read never goes out as a guess. The free-text ``cadence`` beside it is for
people and is never parsed.

A read stands for the latest of its lane's read times at or before it (lane.read_times), allowing the
lane's grace for a row or bar stamped a little before the job fired: the live lane's 10:01:40 row is its
10:02 read, and a row at 12:45 still belongs to the 12:32 read. A live send passes the moment its job
fired, which caps the grace: a read cannot stand for a read time its job has not reached, so a 10:26:30
row sent at 10:27 is the 10:02 read, not the 10:32. A fire up to EARLY_FIRE before a read time is that
read (launchd may fire a job a few seconds early). A moment a full spacing past the lane's last read is
no read of that lane (the opening lane at noon).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from .events import ET, starts_on

if TYPE_CHECKING:
    from .lane import Lane

KEYS = {"every_min", "from", "to", "days", "at", "hold", "then", "note", "hold_until"}
EARLY_FIRE = timedelta(seconds=30)      # a job fired this long before a read time is that read
FROM_EVENTS = {"after the press conference starts": "FOMC_PRESSER"}
DAY_EVENTS = {"FOMC only": "FOMC"}


def _clock(text: str) -> time:
    return time.fromisoformat(text)


def check(entry: dict, where: str) -> None:
    """Refuse an entry the code cannot read, naming the question."""
    if not isinstance(entry, dict) or set(entry) - KEYS:
        raise ValueError(f"{where}: schedule entry {entry!r} has keys the code does not read")
    kinds = [k for k in ("every_min", "at", "hold_until") if k in entry]
    if len(kinds) != 1:
        raise ValueError(f"{where}: a schedule entry needs exactly one of every_min, at or hold_until, has {kinds}")
    try:
        if "every_min" in entry:
            if not isinstance(entry["every_min"], int) or entry["every_min"] <= 0 or "to" not in entry or "from" not in entry:
                raise ValueError("every_min needs a positive whole number of minutes, a from and a to")
            _clock(entry["to"])
            if entry["from"] not in FROM_EVENTS:
                _clock(entry["from"])
            if "days" in entry and entry["days"] not in DAY_EVENTS:
                raise ValueError(f"days {entry['days']!r} is not one of {sorted(DAY_EVENTS)}")
        elif "at" in entry:
            if not entry["at"]:
                raise ValueError("at names no read")
            for t in entry["at"]:
                _clock(t)
        else:
            _clock(entry["hold_until"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"{where}: schedule entry {entry!r} does not read: {e}") from None


def holds_for_the_day(entry: dict) -> bool:
    """Asked at its reads, then held for the rest of the day."""
    return "at" in entry and (bool(entry.get("hold")) or "then" in entry)


def every_min(entry: dict | None) -> int | None:
    return entry.get("every_min") if entry else None


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def read_slot(lane: Lane, now: datetime, fired: datetime | None = None) -> str | None:
    """The lane read ``now`` stands for, as "HH:MM" market time; None before the lane's first read or a
    full spacing after its last. ``fired``, the moment a live send's job fired, caps the grace."""
    local = now.astimezone(ET)
    reads = lane.read_times()
    limit = local + timedelta(minutes=lane.read_grace_min)
    if fired is not None:
        limit = min(limit, fired.astimezone(ET) + EARLY_FIRE)
    past = [r for r in reads if r <= limit.strftime("%H:%M")]
    if not past:
        return None
    spacing = _minutes(reads[-1]) - _minutes(reads[-2])
    return past[-1] if past[-1] != reads[-1] or local.hour * 60 + local.minute - _minutes(reads[-1]) < spacing else None


def asks_at(entry: dict, slot: str, day: date, reads: tuple[str, ...]) -> bool:
    """Whether the entry asks its question at the read ``slot`` of ``day`` on a lane reading at ``reads``."""
    if "at" in entry:
        return slot in entry["at"]
    if "hold_until" in entry:
        return False
    if "days" in entry and starts_on(day, DAY_EVENTS[entry["days"]]) is None:
        return False
    start = entry["from"]
    if start in FROM_EVENTS:
        event = starts_on(day, FROM_EVENTS[start])
        if event is None:
            return False
        start = event.strftime("%H:%M")
    window = [r for r in reads if start <= r <= entry["to"]]
    return slot in window and (_minutes(slot) - _minutes(window[0])) % entry["every_min"] == 0


def not_due(doc: dict, lane: Lane, now: datetime, fired: datetime | None = None) -> dict[str, str]:
    """``{question id: why}`` for every live and shadow question of the lane's doc that its schedule does not
    ask at this read (``fired`` as read_slot's). A question with no schedule is asked on every read; dark
    questions are the packer's."""
    slot = read_slot(lane, now, fired)
    day = now.astimezone(ET).date()
    out = {}
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            entry = q.get("schedule")
            if q.get("status") == "dark" or entry is None:
                continue
            if slot is None:
                out[qid] = f"no {lane.name} lane read at {now.astimezone(ET):%H:%M} ET"
            elif asks_at(entry, slot, day, lane.read_times()):
                continue
            elif holds_for_the_day(entry) and slot > entry["at"][-1]:
                out[qid] = f"a day constant: asked at {entry['at'][-1]} ET and held"
            elif "hold_until" in entry:
                out[qid] = (f"asked on its other lane and held here until {entry['hold_until']} ET" if slot <= entry["hold_until"]
                            else f"held from its other lane only until {entry['hold_until']} ET")
            else:
                out[qid] = f"not on its schedule at the {slot} ET read"
    return out
