"""The raw archive: everything a read saw, asked, was told and later scored, for machine learning.

    state/spx_jev/archive/{day}.jsonl    append only, never rewritten, one JSON object per line
                                         (<out-dir>/archive/ for a run pointed at a folder of its own,
                                         lane.Lane.archive_folder)

Three record kinds, each a dataclass below, each line carrying ``schema_version`` and ``kind``:

    read       every run of every lane (sent or not): the complete labels and the omitted ones with
               their reasons, the exact requests sent to JEV and its exact replies, the sums request and
               reply, the sum as shown (with the blend), from version 5 the average-price sum's request and
               reply (``average_request``, ``average_response``), the cadence state (held, not due, asked, and
               asked again because an earlier ask got no answer), the market-context values the read could see,
               the tier-1 event tag, the learning loop's forecasts, on the tape lane the unit and the bands, and
               on the premarket lane its checkpoint, its pre-open ruler and the overnight bars it saw
    grade      one per graded horizon line the grader writes, keyed to its read by ``read_id``
    close_out  a lane's grade-only run after its last read (the live lane's after the close): the day's calls and
               tally. A lane and day can have several, one from the close-out and one from each later retry whose tally
               moved (service.retry_close_outs); the newest per lane and day stands. From version 4
               each call is graded on the average price over its window (``integral``), its end-price grade kept
               beside it under ``end_price`` (``end_price_only`` when it stands on that alone), and the tally counts
               the average price: ``right`` is right on the average, ``passed`` the unsure picks (``unsure`` up to
               version 3), ``end_price_only`` the calls counted on the end price. Up to version 3 a call carried its
               end-price outcome, hit and move on itself and the tally counted those; read_close_out gives an older
               line in the version 4 shape. From version 5 a call's pick and odds are the average-price sum's when
               it answered (``sum`` names the sum), and ``end_price`` carries the end-price sum's own pick and its
               probability; a version 4 call's pick is the end-price sum's

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

SCHEMA_VERSION = 5          # 2: a read carries the learning loop's forecasts (pool); 3: a premarket read carries its checkpoint and the night it saw;
                            # 4: a close_out's calls and tally are graded on the average price (see close_out above);
                            # 5: a read carries the average-price sum's request and reply, and a close_out's calls are its calls (see close_out);
                            #    every version 5 read's cadence carries ``reasked`` and every close_out's tally ``closed`` (calls closed for good),
                            #    which late version 3 lines may carry too, added without a bump
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
    hour: dict | None                            # the sums as the card carries them (blended on the live lane), the call under ``average``
    cadence: dict[str, Any]                      # {"from", "held": {qid: iso}, "not_due": {qid: reason}, "asked": [qid],
                                                 #  "reasked": {qid: {"row_ts", "why"}}: asked again, the earlier ask today got no answer}
    market_context: dict[str, dict] | None       # {symbol: {"value", "known_at"}} the read could see
    event: dict | None                           # the tier-1 event tag (events.py), never sent to JEV
    ruler: dict | None = None                    # the tape lane's unit; the premarket lane's pre-open ruler, or why it has none
    band: dict | None = None                     # the tape lane's bands in points
    pool: dict | None = None                     # the learning loop's forecasts per horizon (pool.snapshot)
    checkpoint: str | None = None                # the premarket lane's checkpoint, "HH:MM" market time
    night: dict | None = None                    # the premarket lane's overnight store as the read saw it (premarket.night_seen)
    average_request: dict | None = None          # the average-price sum's request, None when it was not asked
    average_response: dict | None = None         # JEV's reply to it, untouched
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


def read_close_out(line: dict) -> dict:
    """A close_out line in the version 4 shape, whatever version wrote it. A call from version 3 or before had only its
    end-price grade: it comes back with that under ``end_price`` and marked ``end_price_only``, and its tally's
    ``unsure`` as ``passed``, every call it graded counted under ``end_price_only``."""
    if line.get("schema_version", 0) >= 4:
        return line
    calls = []
    for c in line.get("calls") or []:
        c = dict(c)
        if "outcome" in c:
            c["end_price"] = {k: c.pop(k) for k in ("outcome", "hit", "moved") if k in c}
            c["end_price_only"] = True
        calls.append(c)
    tally = dict(line.get("tally") or {})
    tally["passed"], tally["end_price_only"] = tally.pop("unsure", 0), tally.get("graded", 0)
    return {**line, "calls": calls, "tally": tally}


def append(folder: Path | str, day: str, record: ReadRecord | GradeRecord | CloseOutRecord) -> Path:
    """Append one record to the day's file in the archive ``folder``; the only write this module makes."""
    path = Path(folder) / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path
