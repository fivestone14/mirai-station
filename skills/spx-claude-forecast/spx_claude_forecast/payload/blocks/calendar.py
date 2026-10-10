"""The calendar block: what the day holds on the event calendar and where it sits in the Fed, month and
expiry cycles, with no date or weekday in it.

The events come from the station's hand-kept calendar as the inputs carry it (``inputs.events``, the parsed
calendar/events.json): every row on the day with its clock time and how long ago it started (or how long
until it does), the day's class by the highest impact tier among them, the next high-impact row in sessions
ahead, and the Fed phase around the nearest rate decision. The calendar's own tiers (1, pre_open, data_10am,
data_2pm, fed_speaker, auction) fold into three impact tiers: high is a tier-1 row or a headline macro print
(jobs, CPI, PCE, GDP, PPI, retail sales), low is a minor release before the open or the weekly claims, and
medium is everything else. Outside the days the calendar is kept for an empty day cannot be told from an
unlisted one, so those fields are declared absent rather than read as quiet.

The month position counts trading days on the station's session calendar (``month_position``), the turn
window names the month-end liquidity window T-3 .. T+3 around the month's last session (T), and the pension
gap is the stock-minus-bond month-to-date move ($SPX minus TLT, in percentage points) from the daily closes
before the day, ranked against the same count of closes into earlier months. The expiry fields come from the
station's expiry calendar (``spx_jev.expiry``): SPX expires every session, so today's kind is daily unless
the day is a monthly, a quarterly (a quarter's monthly) or the quarter-end expiry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ... import station_stores
from ...control import ET
from .. import units
from ..block_result import BlockResult
from ..frozen_inputs import FrozenInputs

HIGH, MEDIUM, LOW = "high", "medium", "low"
# Releases before the open that set the day's tone the way a tier-1 row does; every other pre-open print is minor.
HEADLINE_RELEASES = frozenset({"JOBS", "CPI", "PCE", "GDP", "PPI", "RETAIL_SALES"})
WEEKLY_CLAIMS = "JOBLESS_CLAIMS"      # listed every Thursday, so it is a low tier on its own, not a headline
FOMC = "FOMC"                         # the rate decision's row kind; the presser is a row of its own
TURN_WINDOW_SESSIONS = 3              # the month-end liquidity window: T-3 .. T+3 around the month's last session
NOT_COVERED = "calendar_not_covered"
SPX, TLT = "$SPX", "TLT"              # the stock and bond legs of the pension gap, as the daily closes name them
GAP_DECIMALS = 2


@dataclass(frozen=True)
class _Event:
    """One calendar row as this block reads it: when it starts (and ends, when the row says), its kind and impact tier."""
    start: datetime
    end: datetime | None
    kind: str
    impact: str


@dataclass(frozen=True)
class _Calendar:
    events: tuple[_Event, ...]
    covers_from: date | None
    covers_through: date | None

    def covers(self, day: date) -> bool:
        """Every tier is listed for ``day``: a day with no rows is quiet, not unknown."""
        return (self.covers_through is not None and day <= self.covers_through
                and (self.covers_from is None or day >= self.covers_from))

    def covers_tier_one(self, day: date) -> bool:
        """The tier-1 rows run from the calendar's first row to its last kept day (the file's own note)."""
        return bool(self.events) and self.events[0].start.date() <= day <= (self.covers_through or date.min)


def build_calendar_block(inputs: FrozenInputs) -> BlockResult:
    """``{day_class, today, next_high_tier, fomc, month, expiry}``; each part declares its own absence."""
    result = BlockResult()
    day = date.fromisoformat(inputs.day)
    calendar = _parse_calendar(inputs.events)
    if calendar is None:
        for field in ("day_class", "today", "next_high_tier", "fomc"):
            result.leave_out(f"calendar.{field}", "no_event_calendar")
        today = next_high = fomc = day_class = None
    else:
        day_class, today = _today(calendar, day, inputs.cut, result)
        next_high = _next_high_tier(calendar, day, inputs.cut, result)
        fomc = _fomc(calendar, day, result)
    result.data = units.clean({
        "day_class": day_class,
        "today": today,
        "next_high_tier": next_high,
        "fomc": fomc,
        "month": _month(inputs, day, result),
        "expiry": _expiry(inputs.cut, day),
    })
    return result


# --- the event calendar ------------------------------------------------------------------------------------

def _parse_calendar(doc: dict | list | None) -> _Calendar | None:
    """The calendar as the inputs carry it; None when there is no readable ``events`` list. A row with no
    readable date and time is skipped, as the station skips it; a Fed speaker with no end runs SPEAKER_MIN."""
    if not isinstance(doc, dict) or not isinstance(doc.get("events"), list):
        return None
    events = station_stores.import_spx_jev("events")
    rows = []
    for row in doc["events"]:
        if not isinstance(row, dict):
            continue
        try:
            start = datetime.fromisoformat(f"{row['date']}T{row['time_et']}").replace(tzinfo=ET)
            end = datetime.fromisoformat(f"{row['date']}T{row['end_et']}").replace(tzinfo=ET) if row.get("end_et") else None
        except (KeyError, TypeError, ValueError):
            continue
        tier = str(row.get("tier"))
        if end is None and tier == events.FED_SPEAKER:
            end = start + timedelta(minutes=events.SPEAKER_MIN)
        kind = str(row.get("kind", "event"))
        rows.append(_Event(start, end if end and end > start else None, kind, _impact(kind, tier, events)))
    return _Calendar(tuple(sorted(rows, key=lambda e: e.start)), _day_or_none(doc.get("covers_from")),
                     _day_or_none(doc.get("covers_through")))


def _impact(kind: str, tier: str, events) -> str:
    """The calendar's tier folded into high, medium or low (the module note)."""
    if tier == events.TIER or kind in HEADLINE_RELEASES:
        return HIGH
    if tier == events.PRE_OPEN and (kind in events.MINOR_PRE_OPEN or kind == WEEKLY_CLAIMS):
        return LOW
    return MEDIUM


def _day_or_none(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _today(calendar: _Calendar, day: date, cut: datetime, result: BlockResult) -> tuple[str | None, list[dict] | None]:
    """The day's class and every row on the day, each with its clock time and its distance from the cut."""
    if not calendar.covers(day):
        result.leave_out("calendar.day_class", NOT_COVERED)
        result.leave_out("calendar.today", NOT_COVERED)
        return None, None
    rows = [e for e in calendar.events if e.start.date() == day]
    today = [units.clean({
        "kind": e.kind, "at": units.hhmm(e.start), "tier": e.impact,
        "min_ago": units.minutes_ago(e.start, cut) if e.start <= cut else None,
        "min_ahead": _minutes_ahead(cut, e.start) if e.start > cut else None,
        "under_way": True if e.end is not None and e.start <= cut < e.end else None,
    }) for e in rows]
    return _day_class(rows), today


def _day_class(rows: list[_Event]) -> str:
    """The day by its highest impact tier: "high-tier day" .. "low-tier day", or "quiet day" with no rows."""
    for tier in (HIGH, MEDIUM, LOW):
        if any(e.impact == tier for e in rows):
            return f"{tier}-tier day"
    return "quiet day"


def _minutes_ahead(cut: datetime, start: datetime) -> int:
    """Whole minutes until ``start``, rounded up as the station's event tag does, so a shown 30 is never past 30."""
    return math.ceil((start - cut).total_seconds() / 60.0)


def _next_high_tier(calendar: _Calendar, day: date, cut: datetime, result: BlockResult) -> dict | None:
    """The first high-impact row after the cut, in sessions ahead (0 means later today)."""
    if not calendar.covers(day):
        result.leave_out("calendar.next_high_tier", NOT_COVERED)
        return None
    expiry = station_stores.import_spx_jev("expiry")
    ahead = next((e for e in calendar.events if e.impact == HIGH and e.start > cut), None)
    if ahead is None:
        result.leave_out("calendar.next_high_tier", "none_on_calendar_ahead")
        return None
    return {"kind": ahead.kind, "sessions_ahead": expiry.trading_days_between(day, ahead.start.date())}


def _fomc(calendar: _Calendar, day: date, result: BlockResult) -> dict | None:
    """The day's place against the rate decisions: decision_day, day_before, day_after or none, and the sessions
    to the next decision (0 on the decision day)."""
    if not calendar.covers_tier_one(day):
        result.leave_out("calendar.fomc", NOT_COVERED)
        return None
    sessions = station_stores.import_spx_jev("sessions")
    expiry = station_stores.import_spx_jev("expiry")
    decisions = sorted({e.start.date() for e in calendar.events if e.kind == FOMC})
    if day in decisions:
        phase = "decision_day"
    elif sessions.next_trading_day(day) in decisions:
        phase = "day_before"
    elif sessions.previous_trading_day(day) in decisions:
        phase = "day_after"
    else:
        phase = "none"
    upcoming = next((d for d in decisions if d >= day), None)
    if upcoming is None:
        result.leave_out("calendar.fomc.sessions_to_next", "none_on_calendar_ahead")
    return units.clean({"phase": phase,
                        "sessions_to_next": None if upcoming is None else expiry.trading_days_between(day, upcoming)})


# --- the month -----------------------------------------------------------------------------------------------

def _month(inputs: FrozenInputs, day: date, result: BlockResult) -> dict:
    market_features = station_stores.import_spx_jev("mirai_prediction.code_features_market")
    trading_day, days_left = market_features.month_position(day)
    return units.clean({
        "trading_day": trading_day,
        "days_left": days_left,
        "turn_window": _turn_window(trading_day, days_left),
        "pension_stock_minus_bond_mtd_pts": _pension_gap(inputs.daily_closes, inputs.day, market_features, result),
    })


def _turn_window(trading_day: int, days_left: int) -> str:
    """T on the month's last session, T-1 .. T-3 before it, T+1 .. T+3 on the new month's first sessions, else none."""
    if days_left == 0:
        return "T"
    if days_left <= TURN_WINDOW_SESSIONS:
        return f"T-{days_left}"
    if trading_day <= TURN_WINDOW_SESSIONS:
        return f"T+{trading_day}"
    return "none"


def _pension_gap(daily_closes: dict[str, list[dict]], day: str, market_features, result: BlockResult) -> dict | None:
    """$SPX's month-to-date move minus TLT's, in percentage points, through the last close before the day
    (``month_gap``, the station's own measure), ranked against the gap at the same count of closes into each
    earlier month with that many, newest month first."""
    spx, tlt = daily_closes.get(SPX) or [], daily_closes.get(TLT) or []
    if not spx or not tlt:
        result.leave_out("calendar.month.pension_stock_minus_bond_mtd_pts", "no_daily_closes")
        return None
    month = day[:7]
    this_month = [r["day"] for r in spx if str(r.get("day", ""))[:7] == month]
    if not this_month:
        result.leave_out("calendar.month.pension_stock_minus_bond_mtd_pts", "no_close_yet_this_month")
        return None
    gap = market_features.month_gap(spx, tlt, this_month[-1])
    if gap is None:
        result.leave_out("calendar.month.pension_stock_minus_bond_mtd_pts", "no_prior_month_close")
        return None
    closes_in = len(this_month)
    base = []
    for earlier in sorted({str(r.get("day", ""))[:7] for r in spx if str(r.get("day", ""))[:7] < month}, reverse=True):
        days = [r["day"] for r in spx if r["day"][:7] == earlier]
        if len(days) < closes_in:
            continue
        earlier_gap = market_features.month_gap(spx, tlt, days[closes_in - 1])
        if earlier_gap is not None:
            base.append(earlier_gap * 100.0)
    return units.ranked(round(gap * 100.0, GAP_DECIMALS), base, "the same count of closes into earlier months")


# --- the expiries --------------------------------------------------------------------------------------------

def _expiry(cut: datetime, day: date) -> dict:
    """Today's expiry kind and the sessions to the next monthly (0 when it is today's)."""
    expiry = station_stores.import_spx_jev("expiry")
    kinds = expiry.expiry_kinds(day)
    today = next((k for k in ("quarter_end", "quarterly", "monthly") if k in kinds), "daily")
    return {"today": today, "monthly_sessions_ahead": expiry.trading_days_between(day, expiry.next_monthly(cut))}
