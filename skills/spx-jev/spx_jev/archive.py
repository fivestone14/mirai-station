"""The raw archive: everything a read saw, asked, was told and later scored, for machine learning.

    state/spx_jev/archive/{day}.jsonl    append only, never rewritten, one JSON object per line
                                         (<out-dir>/archive/ for a run pointed at a folder of its own,
                                         lane.Lane.archive_folder)

Three record kinds, each a dataclass below, each line carrying ``schema_version`` and ``kind``:

    read       every run of either lane (sent or not): the complete labels and the omitted ones with
               their reasons, the exact requests sent to JEV and its exact replies, the sums request and
               reply, the sum as shown (with the blend), the cadence state (held, not due, asked), the
               market-context values the read could see, the tier-1 event tag, the learning loop's
               forecasts on the live lane and, on the tape lane, the unit and the bands
    grade      one per graded horizon line the grader writes, keyed to its read by ``read_id``
    close_out  the opening lane's grade-only run after its last read: the day's calls and tally

A read's ``read_id`` is its lane and its row's timestamp, the same key the grader and the card use,
so a grade finds its read without a lookup table. Nothing secret is written: the key never reaches a
request or a reply, and a failed request's error is scrubbed before it is kept (ask.send_all). A
cleaner store is a later transform of these files; nothing here is ever edited in place.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2          # 2: a read carries the learning loop's forecasts (pool)
ARCHIVE_SUBDIR = Path("spx_jev") / "archive"


def read_id(lane: str, row_ts: str) -> str:
    return f"{lane}:{row_ts}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ReadRecord:
    read_id: str
    lane: str
    row_ts: str
    sent: bool
    spot: float
    sigma: float
    labels: dict[str, dict[str, Any]]            # the state: {group: {key: sentence}}
    omitted: dict[str, str]                      # {group.key: reason}
    requests: list[dict]                         # exactly what was sent to JEV, one per group
    skipped: dict[str, dict[str, str]]           # questions left out of the requests, and why
    responses: dict[str, dict] | None            # JEV's replies by group, untouched; None when not sent
    hour_request: dict | None                    # the sums request, None when nothing was summed
    hour_response: dict | None                   # JEV's reply to it, untouched
    hour: dict | None                            # the sum as the card shows it (blended on the live lane)
    cadence: dict[str, Any]                      # {"from", "held": {qid: iso}, "not_due": {qid: reason}, "asked": [qid]}
    market_context: dict[str, dict] | None       # {symbol: {"value", "known_at"}} the read could see
    event: dict | None                           # the tier-1 event tag (events.py), never sent to JEV
    ruler: dict | None = None                    # the tape lane's unit
    band: dict | None = None                     # the tape lane's bands in points
    pool: dict | None = None                     # the learning loop's forecasts per horizon (pool.snapshot), live lane
    schema_version: int = SCHEMA_VERSION
    kind: str = "read"
    archived_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class GradeRecord:
    read_id: str
    lane: str
    row_ts: str
    grade: dict                                  # the grade line exactly as grades.jsonl holds it
    schema_version: int = SCHEMA_VERSION
    kind: str = "grade"
    archived_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class CloseOutRecord:
    lane: str
    day: str
    calls: list[dict]                            # the day's calls with their grades (service.day_calls)
    tally: dict[str, int]
    schema_version: int = SCHEMA_VERSION
    kind: str = "close_out"
    archived_at: str = field(default_factory=_now)


def append(folder: Path | str, day: str, record: ReadRecord | GradeRecord | CloseOutRecord) -> Path:
    """Append one record to the day's file in the archive ``folder``; the only write this module makes."""
    path = Path(folder) / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path
