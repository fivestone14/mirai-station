"""Scheduled events near a read: the rows of ``calendar/events.json``.

A read is tagged, never blocked, by the tier-1 rows: the tag reaches the record, the grade line and
the phone, JEV never sees it, the sums are not suppressed, and a read with an event due inside 30
minutes is graded but tallied apart and kept out of what the question weights learn from (grade.py),
so a Fed minute cannot teach a question what an ordinary half hour looks like.

The other tiers are for the event labels (labels/events_shocks.py): the releases before the open
(PRE_OPEN), the 10:00 and 14:00 releases (DATA_10AM, DATA_2PM) and the Fed officials' scheduled
remarks (FED_SPEAKER). AUCTION is the Treasury's note and bond auctions (results about 13:00 ET), a
tier the calendar has no rows of yet: a row is {"date", "time_et": "13:00", "kind": "AUCTION_10Y", "tier":
"auction", "scope": "rates", "source": the Treasury's tentative schedule}, added only from the published
schedule, and no label or learn_exclude reads the tier yet. The learning loop keeps out more than the weights do (learn_exclude, 06's
guardrails): the 10:00 and 14:00 releases and the Fed speakers too, and the close of a monthly
expiry and of the month's last session. The calendar is kept by hand from ``covers_from`` through ``covers_through``,
its releases before the open from ``pre_open_covers_from``; outside those days a label cannot tell a quiet
day from an unlisted one, so it says so instead of reading the calendar (uncovered).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from .expiry import expiry_kinds, quarter_end_expiry
from .sessions import session_close

CALENDAR = Path(__file__).resolve().parent.parent / "calendar" / "events.json"
ET = ZoneInfo("America/New_York")
TIER = "1"
PRE_OPEN, DATA_10AM, DATA_2PM, FED_SPEAKER, AUCTION = "pre_open", "data_10am", "data_2pm", "fed_speaker", "auction"
TIERS = frozenset({TIER, PRE_OPEN, DATA_10AM, DATA_2PM, FED_SPEAKER, AUCTION})   # every tier a calendar row may carry
LEARN_TIERS = frozenset({TIER, DATA_10AM, DATA_2PM, FED_SPEAKER})   # kept out of the learning loop (learn_exclude)
WINDOW_MIN = 60          # tagged when due within the longer graded horizon
SPEAKER_MIN = 60         # a Fed speaker row with no end_et runs this long, remarks and questions (the calendar's own note)
HOLD_OUT_MIN = 30        # kept out of the weights when due within the primary one

WORDS = {
    "FOMC": "the Fed's rate decision",
    "FOMC_PRESSER": "the Fed chair's press conference",
    "FED_CHAIR_TESTIMONY": "the Fed chair's testimony",
    "FED_CHAIR_JACKSON_HOLE": "the Fed chair's Jackson Hole speech",
    "MSCI_REBALANCE": "an MSCI index rebalance at the close",
    "RUSSELL_RECON": "the Russell index reconstitution at the close",
    "QUAD_WITCHING_SP_REBALANCE": "quarterly expiry and the S&P rebalance at the close",
    "QUAD_WITCHING_SP_NDX_REBALANCE": "quarterly expiry and the S&P and Nasdaq-100 rebalances at the close",
    "HALF_DAY_CLOSE": "the half-day close",
    "QUARTER_END": "the quarter-end close",
    "JOBS": "the jobs report",
    "CPI": "the consumer price report",
    "PPI": "the producer price report",
    "PCE": "the PCE inflation report",
    "RETAIL_SALES": "the retail sales report",
    "GDP": "the GDP report",
    "JOBLESS_CLAIMS": "the weekly jobless claims report",
    "ADP": "ADP's private payrolls report",
    "DURABLE_GOODS": "the durable goods orders report",
    "TRADE_BALANCE": "the trade balance report",
    "HOUSING_STARTS": "the housing starts report",
    "IMPORT_PRICES": "the import price report",
    "PRODUCTIVITY": "the productivity and labor costs report",
    "EMPLOYMENT_COST": "the employment cost index",
    "EMPIRE_STATE": "the New York Fed's manufacturing survey",
    "PHILLY_FED": "the Philadelphia Fed's manufacturing survey",
    "ISM_MANUFACTURING": "the ISM manufacturing report",
    "ISM_SERVICES": "the ISM services report",
    "JOLTS": "the job openings report",
    "CONSUMER_CONFIDENCE": "the Conference Board's consumer confidence report",
    "UMICH_SENTIMENT": "the University of Michigan's consumer sentiment report",
    "FOMC_MINUTES": "the minutes of the last Fed meeting",
    "FED_CHAIR_SPEECH": "the Fed chair",
    "FED_VICE_CHAIR_SPEECH": "the Fed vice chair",
    "FED_VICE_CHAIR_SUPERVISION_SPEECH": "the Fed's vice chair for supervision",
    "FED_GOVERNOR_SPEECH": "a Fed governor",
    "AUCTION_10Y": "the 10-year Treasury note auction",
    "AUCTION_30Y": "the 30-year Treasury bond auction",
}
# Releases before the open listed for the premarket lane's report window (story.releases) and left out of the
# session's event labels (session_rows), minor for the index: they would turn a quiet session into a report day.
MINOR_PRE_OPEN = frozenset({"ADP", "DURABLE_GOODS", "TRADE_BALANCE", "HOUSING_STARTS", "IMPORT_PRICES", "PRODUCTIVITY",
                            "EMPLOYMENT_COST", "EMPIRE_STATE", "PHILLY_FED"})
# Tier-1 rows that trade at the close rather than at a moment in the session.
AT_THE_CLOSE = frozenset({"MSCI_REBALANCE", "RUSSELL_RECON", "QUAD_WITCHING_SP_REBALANCE", "QUAD_WITCHING_SP_NDX_REBALANCE",
                          "HALF_DAY_CLOSE", "QUARTER_END"})
# The Fed's pre-meeting quiet period runs from the second Saturday before a meeting's first day
# through the day after its decision; every 2026 meeting starts the day before its decision.
QUIET_FROM_SATURDAYS_BEFORE = 2
SATURDAY = 5


@dataclass(frozen=True)
class Event:
    """One calendar row: when it starts (and ends, for a row that says), what it is, its tier, whether
    its date is published (``verified``) or follows a rule, and whether a speaker takes audience questions."""
    start: datetime
    end: datetime | None
    kind: str
    tier: str
    verified: bool = True
    q_and_a: bool = False

    @property
    def words(self) -> str:
        return words(self.kind)


@dataclass(frozen=True)
class Calendar:
    events: tuple[Event, ...]
    covers_through: date | None
    covers_from: date | None = None
    pre_open_covers_from: date | None = None


@lru_cache(maxsize=4)
def _load(path: str) -> Calendar:
    """Every readable row, by start. A row that is not a dict or has no readable date and time is left
    out; a broken file gives an empty calendar that covers nothing, never an error. A Fed speaker row with no
    end_et ends SPEAKER_MIN after it starts, so a read taken while one speaks is under way, not after it."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Calendar((), None)
    rows = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return Calendar((), None)
    out = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        try:
            start = datetime.fromisoformat(f"{e['date']}T{e['time_et']}").replace(tzinfo=ET)
            end = datetime.fromisoformat(f"{e['date']}T{e['end_et']}").replace(tzinfo=ET) if e.get("end_et") else None
        except (KeyError, TypeError, ValueError):
            continue
        if end is None and e.get("tier") == FED_SPEAKER:
            end = start + timedelta(minutes=SPEAKER_MIN)
        out.append(Event(start, end if end and end > start else None, str(e.get("kind", "event")), str(e.get("tier")),
                         e.get("verified") is not False, e.get("q_and_a") is True))
    through, since, pre_open_since = (_day(doc.get(k)) for k in ("covers_through", "covers_from", "pre_open_covers_from"))
    return Calendar(tuple(sorted(out, key=lambda r: r.start)), through, since, pre_open_since or since)


def _day(v: object) -> date | None:
    try:
        return date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def words(kind: str) -> str:
    return WORDS.get(kind, kind.replace("_", " ").lower())


def _minutes(n: int) -> str:
    return f"{n} minute" + ("" if n == 1 else "s")


def uncovered(day: date, path: Path | str = CALENDAR, tier: str | None = None) -> str | None:
    """Why the calendar cannot say what ``day`` holds, or None when it can: before its first kept day
    (covers_from, none meaning every earlier day is kept) or past its last (covers_through) an empty day
    is unknown, not quiet. Asked for the ``tier`` PRE_OPEN only, the first kept day is pre_open_covers_from."""
    cal = _load(str(path))
    if cal.covers_through is None:
        return "the event calendar (calendar/events.json) names no last kept day (covers_through)"
    if day > cal.covers_through:
        return f"the event calendar (calendar/events.json) is kept only through {cal.covers_through}: extend it"
    since = cal.pre_open_covers_from if tier == PRE_OPEN else cal.covers_from
    if since is not None and day < since:
        return f"the event calendar (calendar/events.json) is kept only from {since}"
    return None


def on_day(day: date, path: Path | str = CALENDAR) -> list[Event]:
    """Every row of every tier on ``day``, by start."""
    return [e for e in _load(str(path)).events if e.start.date() == day]


def session_rows(day: date, path: Path | str = CALENDAR) -> list[Event]:
    """The rows on ``day`` the session's event labels read: every tier's but the minor releases before the open."""
    return [e for e in on_day(day, path) if e.kind not in MINOR_PRE_OPEN]


def in_quiet_period(day: date, path: Path | str = CALENDAR) -> bool:
    """Whether ``day`` falls in the Fed's pre-meeting quiet period of a meeting on the calendar."""
    for e in _load(str(path)).events:
        if e.kind != "FOMC":
            continue
        first_day = e.start.date() - timedelta(days=1)
        saturday = first_day - timedelta(days=(first_day.weekday() - SATURDAY) % 7 or 7)
        if saturday - timedelta(weeks=QUIET_FROM_SATURDAYS_BEFORE - 1) <= day <= e.start.date() + timedelta(days=1):
            return True
    return False


def starts_on(day: date, kind: str, path: Path | str = CALENDAR) -> datetime | None:
    """When the first tier-1 event of ``kind`` starts on ``day``, or None when there is none that day."""
    return next((e.start for e in _load(str(path)).events if e.tier == TIER and e.kind == kind and e.start.date() == day), None)


def tag(now: datetime, path: Path | str = CALENDAR) -> dict | None:
    """``{"events": [{"kind", "words", "at", "minutes"}], "soonest_min", "within_30", "sentence"}`` for
    every tier-1 event due after ``now`` and within WINDOW_MIN minutes, and every one under way at
    ``now`` (a row with an end time), or None when there is none. ``at`` is the event's start as a full
    timestamp, so the phone can show it in the viewer's zone. Minutes are rounded up, so a shown 30
    always means inside the 30-minute hold-out."""
    horizon = now + timedelta(minutes=WINDOW_MIN)
    ev = []
    for e in _load(str(path)).events:
        if e.tier != TIER:
            continue
        if now < e.start <= horizon:
            mins = math.ceil((e.start - now).total_seconds() / 60.0)
            ev.append({"kind": e.kind, "words": e.words, "at": e.start.isoformat(), "minutes": mins,
                       "text": f"{e.words}, due in {_minutes(mins)}, at {e.start:%H:%M} ET"})
        elif e.end is not None and e.start <= now < e.end:
            ev.append({"kind": e.kind, "words": e.words, "at": e.start.isoformat(), "minutes": 0,
                       "text": f"{e.words}, under way until {e.end:%H:%M} ET"})
    if not ev:
        return None
    ev.sort(key=lambda e: e["minutes"])
    soonest = ev[0]["minutes"]
    return {"events": [{k: v for k, v in e.items() if k != "text"} for e in ev], "soonest_min": soonest,
            "within_30": soonest <= HOLD_OUT_MIN, "sentence": "a scheduled event is ahead: " + "; ".join(e["text"] for e in ev)}


def learn_exclude(now: datetime, horizons: tuple[int, ...], path: Path | str = CALENDAR) -> dict[str, bool]:
    """``{str(minutes): bool}`` per horizon: whether the learning loop leaves the read out of what it learns
    (06's guardrails), written on the read's record when it is made. Out when a tier-1, 10:00, 14:00 or Fed
    speaker row starts inside the window or is under way, or when the window reaches the close of a monthly
    expiry or of the month's last session (quarter_end_expiry is the last trading day of any month)."""
    d = now.astimezone(ET).date()
    close = session_close(now.astimezone(ET))
    at_close = bool(expiry_kinds(d)) or d == quarter_end_expiry(d.year, d.month)
    rows = [e for e in _load(str(path)).events if e.tier in LEARN_TIERS]
    out = {}
    for minutes in horizons:
        end = now + timedelta(minutes=minutes)
        out[str(minutes)] = (any(now < e.start <= end or (e.end is not None and e.start <= now < e.end) for e in rows)
                             or (at_close and now < close <= end))
    return out
