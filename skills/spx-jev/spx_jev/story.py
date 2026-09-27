"""The night's story so far: the stretches of the overnight futures session from their 16:00 price to the
read, fixed in advance, so a replay cannot pick its windows after seeing the results.

    after_close     the prior session's close to 18:00 that evening (the after-hours earnings move)
    asia            18:00 to Tokyo's close (15:30 in Tokyo); a weekend or a holiday falls in it
    europe_open     Tokyo's close to 30 minutes after Frankfurt's open (09:00 in Frankfurt)
    europe_morning  to 08:00
    pre_report      08:00 to the release minute
    report_window   the release minute to 08:45, measured every day, report or not
    last_stretch    08:45 to the read

Each exchange's own clock sets its edge, so the clock changes move them on their own days: Frankfurt
opens at 04:00 New York time in the week of 10-26, and Tokyo closes at 01:30 from 11-01. The release
minute is the first report the calendar lists before the open, between 08:00 and 08:45 (08:30 when
there is none). A stretch that starts after the read is not in the story yet, and the one the read
falls in ends at the read.

Nothing here reads JEV's earlier answers or an earlier read's record: every read rebuilds every stretch
from the overnight store's bars (night_ranks.window_move) and ranks it against the same stretch on the
last nights (night_ranks.prior_window_nights), so a missed read changes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import events
from .sessions import previous_trading_day, session_close, session_open

ET = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
FRANKFURT = ZoneInfo("Europe/Berlin")

STRETCHES = ("after_close", "asia", "europe_open", "europe_morning", "pre_report", "report_window", "last_stretch")
GLOBEX_REOPEN = time(18, 0)
TOKYO_CLOSE = time(15, 30)
FRANKFURT_OPEN_PLUS_30 = time(9, 30)
EUROPE_MORNING_END = time(8, 0)
REPORT_WINDOW_END = time(8, 45)
NO_REPORT_MINUTE = time(8, 30)


@dataclass(frozen=True)
class Stretch:
    """One stretch of the night: its name, its edges, and whether the read has passed its end."""
    name: str
    start: datetime
    end: datetime
    finished: bool


def release_minute(day: date, path: Path | str = events.CALENDAR) -> time:
    """The minute the report window starts on ``day``: the first release the calendar lists before the
    open, after 08:00 and before 08:45; NO_REPORT_MINUTE when there is none."""
    due = [e.start.time() for e in events.on_day(day, path)
           if e.tier == events.PRE_OPEN and EUROPE_MORNING_END < e.start.time() < REPORT_WINDOW_END]
    return min(due, default=NO_REPORT_MINUTE)


def edges(day: date, release: time) -> list[tuple[str, datetime, datetime]]:
    """Every stretch of the night into ``day`` as ``(name, start, end)``, oldest first, contiguous from the
    prior session's close to the report window's end; the last stretch runs from there to the open."""
    prior, noon = previous_trading_day(day), datetime.combine(day, time(12), tzinfo=ET)
    at = [session_close(datetime.combine(prior, time(12), tzinfo=ET)),
          datetime.combine(prior, GLOBEX_REOPEN, tzinfo=ET),
          datetime.combine(day, TOKYO_CLOSE, tzinfo=TOKYO).astimezone(ET),
          datetime.combine(day, FRANKFURT_OPEN_PLUS_30, tzinfo=FRANKFURT).astimezone(ET),
          datetime.combine(day, EUROPE_MORNING_END, tzinfo=ET),
          datetime.combine(day, release, tzinfo=ET),
          datetime.combine(day, REPORT_WINDOW_END, tzinfo=ET),
          session_open(noon)]
    return [(name, at[k], at[k + 1]) for k, name in enumerate(STRETCHES)]


def stretches(day: date, now: datetime, release: time | None = None) -> list[Stretch]:
    """The stretches the read at ``now`` has seen, oldest first: each started one, the one in progress
    ending at ``now``. ``release`` defaults to the calendar's (release_minute)."""
    out = []
    for name, start, end in edges(day, release or release_minute(day)):
        if start >= now:
            break
        out.append(Stretch(name, start, min(end, now), end <= now))
    return out


def stretch_window(name: str, until: time | None = None, release: time | None = None):
    """``day -> (start, end)`` of the named stretch on any night, for night_ranks.prior_window_nights: the
    same stretch on the prior nights, cut at the clock ``until`` when the read is inside it, each night with
    its own release minute unless ``release`` pins one."""
    def window(day: date) -> tuple[datetime, datetime]:
        start, end = next((start, end) for n, start, end in edges(day, release or release_minute(day)) if n == name)
        return start, min(end, datetime.combine(day, until, tzinfo=ET)) if until else end
    return window
