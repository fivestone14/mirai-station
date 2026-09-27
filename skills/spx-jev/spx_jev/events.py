"""Scheduled events near a read: the tier-1 rows of ``calendar/events.json``.

A read is tagged, never blocked: the tag reaches the record, the grade line and the phone, JEV
never sees it, the sums are not suppressed, and a read with an event due inside 30 minutes is
graded but tallied apart and kept out of what the question weights learn from (grade.py), so a
Fed minute cannot teach a question what an ordinary half hour looks like.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

CALENDAR = Path(__file__).resolve().parent.parent / "calendar" / "events.json"
ET = ZoneInfo("America/New_York")
TIER = 1
WINDOW_MIN = 60          # tagged when due within the longer graded horizon
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
}


@lru_cache(maxsize=4)
def _load(path: str) -> tuple:
    """``((start, end or None, kind), ...)`` for the tier-1 rows. A row that is not a dict, has no
    readable date and time, or another tier is left out; a broken file gives nothing, never an error."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    rows = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return ()
    out = []
    for e in rows:
        if not isinstance(e, dict) or str(e.get("tier")) != str(TIER):
            continue
        try:
            start = datetime.fromisoformat(f"{e['date']}T{e['time_et']}").replace(tzinfo=ET)
            end = datetime.fromisoformat(f"{e['date']}T{e['end_et']}").replace(tzinfo=ET) if e.get("end_et") else None
        except (KeyError, TypeError, ValueError):
            continue
        out.append((start, end if end and end > start else None, str(e.get("kind", "event"))))
    return tuple(sorted(out, key=lambda r: r[0]))


def words(kind: str) -> str:
    return WORDS.get(kind, kind.replace("_", " ").lower())


def _minutes(n: int) -> str:
    return f"{n} minute" + ("" if n == 1 else "s")


def tag(now: datetime, path: Path | str = CALENDAR) -> dict | None:
    """``{"events": [{"kind", "words", "at", "minutes"}], "soonest_min", "within_30", "sentence"}`` for
    every tier-1 event due after ``now`` and within WINDOW_MIN minutes, and every one under way at
    ``now`` (a row with an end time), or None when there is none. ``at`` is the event's start as a full
    timestamp, so the phone can show it in the viewer's zone. Minutes are rounded up, so a shown 30
    always means inside the 30-minute hold-out."""
    horizon = now + timedelta(minutes=WINDOW_MIN)
    ev = []
    for start, end, kind in _load(str(path)):
        if now < start <= horizon:
            mins = math.ceil((start - now).total_seconds() / 60.0)
            ev.append({"kind": kind, "words": words(kind), "at": start.isoformat(), "minutes": mins,
                       "text": f"{words(kind)}, due in {_minutes(mins)}, at {start:%H:%M} ET"})
        elif end is not None and start <= now < end:
            ev.append({"kind": kind, "words": words(kind), "at": start.isoformat(), "minutes": 0,
                       "text": f"{words(kind)}, under way until {end:%H:%M} ET"})
    if not ev:
        return None
    ev.sort(key=lambda e: e["minutes"])
    soonest = ev[0]["minutes"]
    return {"events": [{k: v for k, v in e.items() if k != "text"} for e in ev], "soonest_min": soonest,
            "within_30": soonest <= HOLD_OUT_MIN, "sentence": "a scheduled event is ahead: " + "; ".join(e["text"] for e in ev)}
