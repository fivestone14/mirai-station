"""The learning store: each market day's raw files as typed Parquet, every row checked, with DuckDB views over them.

    python3 -m spx_jev.store                        # the launchd job's run (16:40 ET): the last week's market days
    python3 -m spx_jev.store --day 2026-09-28       # rebuild one day
    python3 -m spx_jev.store --backfill             # every market day with a raw file on disk, naming other days left out

The raw files stay the record (the archive's own note: a cleaner store is a later transform of them). This job
only reads them, and writes only under ``state/spx_jev/store/``:

    <table>/day=YYYY-MM-DD/part-0.parquet   one file per table per built day, zero rows included, replaced whole;
                                             ``day`` is the folder's, not a column in the file (hive partitioning)
    spx_jev.duckdb                           one view per table over its files, ``day`` first, and ``meta``

The tables, each defined in TABLES with its columns, its key and its checks:

    reads           one row per read of any lane (archive ``read`` records): its lane and times, its ruler, how
                    many questions were asked, answered, lost, held, not due, asleep, missing and dark, and why
    facts           one row per label per read, written, omitted or asleep, with the reason for a missing one,
                    and one per market-context value the read could see, with when it became known
    answers         one row per question per read: its status, JEV's probability per option, the pick, where a
                    held answer came from (a lost ask the lane held its last answer for is held, its reason
                    saying so), whether it was asked again after a lost ask, and the hash of the question
                    exactly as JEV was sent it
    calls           one row per sum per read: JEV alone, the time-of-day odds, the blend, the learning loop's mix,
                    and which of them the card showed
    grades          one row per graded sum: the mark, the outcome, right, wrong or abstained, and the scores
    spx_bars        SPX's minute bars (the saved session file, else the bars feed's)
    context_bars    the market feed's minute bars, a derived $VOLD marked, a saved day's bar kept over a live one
    context_quotes  the market feed's quotes, each as of its snapshot
    overnight_bars  the overnight futures store's bars with their contract; a night starts in the prior session's
                    last minutes, which that session's own night also holds, so those are marked ``prior_session``
    rolls           the futures rolls found in the roll table, by the day they took effect
    events          the calendar's scheduled events on the day
    quarantine      every row a check refused, with the check's reason and the raw row
    validation      per table per day: rows read, kept, exact duplicates, rows a better source replaced, quarantined

A row is typed column by column first (a value that is not its column's type, or a required one that is
empty, sends it to quarantine), then held to its table's checks (ranges, a probability map that sums to one,
a bar whose high is under its low, and point in time: no fact known after its read, no grade written before
its mark, no bar saved or served before it finished), then to its read when it has one (a fact, answer, call
or grade whose read is not among the day's kept reads is quarantined). Rows sharing a key are one row: a
copy that agrees but for where it came from is counted as a duplicate; where a table ranks its sources (a saved
day's bar over a live snapshot's, Schwab's own $VOLD over a derived one) the better one is kept and a copy from
a worse source that disagrees is counted as superseded; any other disagreement, two equally good sources
included, is quarantined. Nothing is dropped without a count in ``validation`` or a row in ``quarantine``. A line of a raw
file that is not a JSON object, or whose parts are not the shapes its writer writes, is quarantined under
the table_name ``raw_line``, and the rest of the day is built. The archive's ``close_out``
records restate the calls and grades already stored, so they are read past.

Timestamps are stored as instants in New York time; the Treasury yields are in percent and a futures bar is
under its root (``/ES``), as the labels read them (state_builder.context_value, context_symbol).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import grade
from .archive import ARCHIVE_SUBDIR
from .ask import confidence as answer_confidence, pick as answer_pick
from .events import CALENDAR, ET
from .hour import FIVE
from .lane import LANES, RECORD
from .overnight import OVERNIGHT_SUBDIR
from .rolls import table_path as rolls_path
from .sessions import is_trading_day, previous_trading_day, session_close, session_open
from .state_builder import (CONTEXT_SUBDIR, DEFAULT_STATE_DIR, LIVE_BARS_SUBDIR, SESSION_BARS_SUBDIR, SYMBOL, context_symbol,
                            context_value)

STORE_SUBDIR = Path("spx_jev") / "store"
DB_NAME = "spx_jev.duckdb"
PART = "part-0.parquet"
SCHEMA_VERSION = 2
CATCH_UP_DAYS = 7              # a run rebuilds every market day this many calendar days back, so a late save is picked up
BUILD_AFTER_CLOSE_MIN = 30     # today counts from its close plus this (16:30, 13:30 on a half day); the 16:40 job runs after the 16:20 saves either way
PROB_SUM_TOLERANCE = 0.05      # JEV rounds each option to two places, so six options can sum to 0.97
TIME_TOLERANCE = timedelta(seconds=1)   # the archive stamps whole seconds, a read its microseconds

TS = pa.timestamp("us", tz="America/New_York")
PROBS = pa.map_(pa.string(), pa.float64())
TEXTS = pa.map_(pa.string(), pa.string())
COUNTS = pa.map_(pa.string(), pa.int64())
WORDS = pa.list_(pa.string())
STR, INT, NUM, BOOL, DAY = pa.string(), pa.int64(), pa.float64(), pa.bool_(), pa.date32()


# ----------------------------------------------------------------------------- typing

def _number(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{v!r} is not a number")
    if not math.isfinite(v):
        raise ValueError(f"{v!r} is not finite")
    return float(v)


def _instant(v: Any) -> datetime:
    t = datetime.fromisoformat(v) if isinstance(v, str) else v
    if not isinstance(t, datetime):
        raise ValueError(f"{v!r} is not a timestamp")
    if t.tzinfo is None:
        raise ValueError(f"{v!r} has no offset")
    return t.astimezone(ET)


def _coerce(v: Any, kind: pa.DataType) -> Any:
    """``v`` as a value of the column type ``kind``; ValueError, saying why, when it is not one."""
    if v is None:
        return None
    if kind == STR:
        if not isinstance(v, str):
            raise ValueError(f"{v!r} is not text")
        return v
    if kind == BOOL:
        if not isinstance(v, bool):
            raise ValueError(f"{v!r} is not true or false")
        return v
    if kind == INT:
        n = _number(v)
        if n != int(n):
            raise ValueError(f"{v!r} is not a whole number")
        return int(n)
    if kind == NUM:
        return _number(v)
    if kind == TS:
        return _instant(v)
    if kind == DAY:
        return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(v)
    if kind in (PROBS, COUNTS, TEXTS):
        if not isinstance(v, dict) or not all(isinstance(k, str) for k in v):
            raise ValueError(f"{v!r} is not a map by name")
        of = {PROBS: NUM, COUNTS: INT, TEXTS: STR}[kind]
        return {k: _coerce(x, of) for k, x in v.items()}
    if kind == WORDS:
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{v!r} is not a list of text")
        return list(v)
    raise ValueError(f"no reader for {kind}")


# ----------------------------------------------------------------------------- tables

Check = Callable[[dict], "str | None"]


@dataclass(frozen=True)
class Table:
    """A stored table: its columns ``(name, type, required)``, the columns that make a row one row, the checks a
    typed row must pass (each returns why it fails, or None), how to choose among rows sharing a key (lowest
    ``rank`` kept; a differing row of the same rank, or of a table with none, is quarantined), the columns that say where a row came from
    and so never make two rows differ, and whether each row belongs to a read of the day."""
    name: str
    columns: tuple[tuple[str, pa.DataType, bool], ...]
    key: tuple[str, ...]
    checks: tuple[Check, ...] = ()
    rank: Callable[[dict], tuple] | None = None
    provenance: tuple[str, ...] = ()
    of_read: bool = False

    @property
    def schema(self) -> pa.Schema:
        """The columns a day file holds: all but ``day``, which its folder names."""
        return pa.schema([pa.field(n, t, nullable=not req) for n, t, req in self.columns if n != "day"],
                         metadata={"spx_jev_store_schema": str(SCHEMA_VERSION), "table": self.name})


def _read_cols(*more: tuple[str, pa.DataType, bool]) -> tuple[tuple[str, pa.DataType, bool], ...]:
    return (("day", DAY, True), ("read_id", STR, True), ("lane", STR, True), ("row_ts", TS, True)) + more


def _is(col: str, allowed: tuple) -> Check:
    return lambda r: None if r[col] is None or r[col] in allowed else f"{col} {r[col]!r} is not one of {', '.join(map(str, allowed))}"


def _positive(*cols: str) -> Check:
    return lambda r: next((f"{c} {r[c]} is not above zero" for c in cols if r[c] is not None and r[c] <= 0), None)


def _not_negative(*cols: str) -> Check:
    return lambda r: next((f"{c} {r[c]} is below zero" for c in cols if r[c] is not None and r[c] < 0), None)


def _within(col: str, lo: float, hi: float) -> Check:
    return lambda r: None if r[col] is None or lo <= r[col] <= hi else f"{col} {r[col]} is outside {lo} to {hi}"


def _on_day(col: str) -> Check:
    return lambda r: None if r[col] is None or r[col].astimezone(ET).date() == r["day"] else f"{col} {r[col].isoformat()} is not on {r['day']}"


def _not_after(col: str, limit: str, what: str) -> Check:
    """Point in time: ``col`` is no later than ``limit`` (either missing passes)."""
    return lambda r: (None if r[col] is None or r[limit] is None or r[col] <= r[limit] + TIME_TOLERANCE
                      else f"{what}: {col} {r[col].isoformat()} is after {limit} {r[limit].isoformat()}")


def _probabilities(*cols: str) -> Check:
    def check(r: dict) -> str | None:
        for c in cols:
            p = r[c]
            if p is None:
                continue
            if not p:
                return f"{c} is empty"
            if any(not 0.0 <= x <= 1.0 for x in p.values()):
                return f"{c} has a probability outside 0 to 1: {p}"
            if abs(sum(p.values()) - 1.0) > PROB_SUM_TOLERANCE:
                return f"{c} sums to {sum(p.values()):.3f}, not 1"
        return None
    return check


def _bar_shape(r: dict) -> str | None:
    """A bar's high is its highest price and its low its lowest (a missing high or low passes)."""
    hi, lo = r["high"], r["low"]
    inside = [r[k] for k in ("open", "close") if r[k] is not None]
    if hi is not None and lo is not None and hi < lo:
        return f"high {hi} is under low {lo}"
    if hi is not None and any(x > hi for x in inside):
        return f"open or close above high {hi}"
    if lo is not None and any(x < lo for x in inside):
        return f"open or close below low {lo}"
    return None


def _in_session(r: dict) -> str | None:
    t = r["ts"]
    return None if session_open(t) <= t < session_close(t) else f"ts {t.isoformat()} is outside the session"


def _read_id_matches(r: dict) -> str | None:
    return None if r["read_id"].startswith(f"{r['lane']}:") else f"read_id {r['read_id']} is not of lane {r['lane']}"


def _answered(r: dict) -> str | None:
    if r["status"] != "answered":
        return None
    if not r["probabilities"]:
        return "answered with no probabilities"
    if r["pick"] is not None and r["pick"] not in r["probabilities"]:
        return f"pick {r['pick']!r} is not one of its options {sorted(r['probabilities'])}"
    return None


def _labelled(r: dict) -> str | None:
    if r["source"] == "label" and r["status"] == "written" and not r["text"]:
        return "a written label with no sentence"
    if r["status"] in ("omitted", "asleep") and not r["reason"]:
        return f"{r['status']} with no reason"
    return None


def _outcome(r: dict) -> str | None:
    bands = FIVE if LANES[r["lane"]].horizons.get(r["horizon"], (0, None))[1] == RECORD else grade.BANDS
    return None if r["outcome"] in bands else f"outcome {r['outcome']!r} is not one of {', '.join(bands)}"


def _roll_changes(r: dict) -> str | None:
    return None if r["from_contract"] != r["to_contract"] else f"a roll from {r['from_contract']} to itself"


def _ends_after_start(r: dict) -> str | None:
    return None if r["ends_at"] is None or r["ends_at"] > r["starts_at"] else "ends_at is not after starts_at"


def _saved_after_finish(r: dict) -> str | None:
    done = r["ts"] + timedelta(minutes=r["bar_minutes"])
    return None if r["saved_at"] + TIME_TOLERANCE >= done else f"saved at {r['saved_at'].isoformat()}, before the bar finished at {done.isoformat()}"


def _night_ends_by_close(r: dict) -> str | None:
    close = session_close(datetime.combine(r["day"], datetime.min.time(), tzinfo=ET))
    return None if r["ts"] < close else f"ts {r['ts'].isoformat()} is past the {r['day']} close the night leads into"


LANE_NAMES = tuple(LANES)
ANSWER_STATUSES = ("answered", "lost", "unsent", "held", "not_due", "asleep", "missing", "dark", "unread", "other")
FACT_STATUSES = ("written", "omitted", "asleep")

READS = Table("reads", _read_cols(
    ("archived_at", TS, True), ("minute_et", INT, False), ("minutes_from_open", INT, False), ("checkpoint", STR, False),
    ("sent", BOOL, True), ("model", STR, False), ("spot", NUM, False), ("sigma", NUM, False),
    ("ruler_source", STR, False), ("ruler_points", NUM, False), ("ruler_unit_sigma", NUM, False), ("ruler_sessions", INT, False),
    ("ruler_omitted", STR, False), ("band_flat_points", NUM, False), ("band_big_points", NUM, False),
    ("event_kinds", WORDS, False), ("event_soonest_min", INT, False), ("event_within_30", BOOL, False),
    ("questions", INT, True), *((s, INT, True) for s in ANSWER_STATUSES), ("reasked", INT, False),
    ("labels_written", INT, True), ("labels_omitted", INT, True), ("labels_asleep", INT, True), ("market_values", INT, True),
    ("sum_used", INT, False), ("sum_left_out", INT, False), ("sum_missing", INT, False), ("sum_error", STR, False),
    ("skip_reasons", TEXTS, False), ("ruler_json", STR, False), ("band_json", STR, False), ("event_json", STR, False),
    ("night_json", STR, False), ("archive_schema", INT, False)),
    key=("read_id",),
    checks=(_is("lane", LANE_NAMES), _read_id_matches, _on_day("row_ts"), _positive("spot", "sigma", "ruler_points"),
            _not_after("row_ts", "archived_at", "archived before its read")))

FACTS = Table("facts", _read_cols(
    ("source", STR, True), ("path", STR, True), ("family", STR, False), ("name", STR, False), ("status", STR, True),
    ("text", STR, False), ("reason", STR, False), ("value", NUM, False), ("known_at", TS, True)),
    key=("read_id", "source", "path"),
    checks=(_is("source", ("label", "market_context")), _is("status", FACT_STATUSES), _labelled,
            _not_after("known_at", "row_ts", "known after its read")),
    of_read=True)

ANSWERS = Table("answers", _read_cols(
    ("group_id", STR, False), ("question_id", STR, True), ("status", STR, True), ("reason", STR, False),
    ("type", STR, False), ("pick", STR, False), ("confidence", NUM, False), ("probabilities", PROBS, False),
    ("score", NUM, False), ("noul", NUM, False), ("model", STR, False), ("question_hash", STR, False),
    ("pool_version", STR, False), ("held_from", TS, False), ("held_found", BOOL, False), ("reasked", BOOL, False),
    ("reasked_from", TS, False), ("reask_why", STR, False)),
    key=("read_id", "question_id"),
    checks=(_is("status", ANSWER_STATUSES), _probabilities("probabilities"), _answered, _within("confidence", 0.0, 1.0),
            _not_after("held_from", "row_ts", "held from after its read"),
            _not_after("reasked_from", "row_ts", "asked again for a later read")),
    of_read=True)

CALLS = Table("calls", _read_cols(
    ("horizon", STR, True), ("minutes", INT, True), ("mark", TS, False), ("minute_et", INT, False),
    ("minutes_from_open", INT, False), ("is_primary", BOOL, True), ("shown_source", STR, True), ("shown_pick", STR, False),
    ("shown_probs", PROBS, True), ("shown_confidence", NUM, False), ("jev_pick", STR, False), ("jev_probs", PROBS, False),
    ("jev_confidence", NUM, False), ("clock_pick", STR, False), ("clock_probs", PROBS, False), ("clock_n", INT, False),
    ("blended", BOOL, False), ("blend_jev_share", NUM, False), ("blend_phase", STR, False), ("blend_sessions", INT, False),
    ("pool_probs", PROBS, False), ("pool_p_move", NUM, False), ("pool_p_up_given_move", NUM, False),
    ("pool_state_hash", STR, False), ("pool_baseline", STR, False), ("pool_left_out", STR, False),
    ("direction_pick", STR, False), ("direction_probs", PROBS, False), ("size_pick", STR, False), ("size_probs", PROBS, False),
    ("learn_exclude", BOOL, False), ("model", STR, False)),
    key=("read_id", "horizon"),
    checks=(_probabilities("shown_probs", "jev_probs", "clock_probs", "pool_probs", "direction_probs", "size_probs"),
            _within("pool_p_move", 0.0, 1.0), _within("pool_p_up_given_move", 0.0, 1.0),
            _not_after("row_ts", "mark", "marked before its read")),
    of_read=True)

GRADES = Table("grades", _read_cols(
    ("horizon", STR, True), ("minutes", INT, True), ("mark", TS, True), ("archived_at", TS, True), ("outcome", STR, True),
    ("direction", STR, False), ("size", STR, False), ("realized_sigma", NUM, False), ("realized_points", NUM, False),
    ("realized_units", NUM, False), ("pick", STR, False), ("abstained", BOOL, True), ("correct", BOOL, False),
    ("hit", BOOL, False), ("brier", NUM, False), ("p_band", NUM, False), ("jev_pick", STR, False), ("jev_hit", BOOL, False),
    ("jev_brier", NUM, False), ("clock_brier", NUM, False), ("direction_pick", STR, False), ("direction_hit", BOOL, False),
    ("direction_brier", NUM, False), ("size_pick", STR, False), ("size_hit", BOOL, False), ("size_brier", NUM, False),
    ("anchor_points", NUM, False), ("anchor_source", STR, False), ("from_settled_open", NUM, False), ("from_at", TS, False)),
    key=("read_id", "horizon"),
    checks=(_is("lane", LANE_NAMES), _outcome, _within("p_band", 0.0, 1.0), _within("brier", 0.0, 2.0), _within("jev_brier", 0.0, 2.0),
            _within("clock_brier", 0.0, 2.0), _within("direction_brier", 0.0, 2.0), _within("size_brier", 0.0, 2.0),
            _not_after("row_ts", "mark", "marked before its read"), _not_after("mark", "archived_at", "graded before its mark")),
    of_read=True)

_BAR = (("open", NUM, True), ("high", NUM, False), ("low", NUM, False), ("close", NUM, True), ("volume", NUM, False))

SPX_BARS = Table("spx_bars", (("day", DAY, True), ("ts", TS, True)) + _BAR + (("source", STR, True),),
                 key=("ts",),
                 checks=(_on_day("ts"), _in_session, _positive("open", "high", "low", "close"), _not_negative("volume"), _bar_shape),
                 rank=lambda r: (r["source"] != "session_file",), provenance=("source",))

CONTEXT_BARS = Table("context_bars", (("day", DAY, True), ("symbol", STR, True), ("served_as", STR, True), ("ts", TS, True))
                     + _BAR + (("derived", BOOL, True), ("derived_from", STR, False), ("source", STR, True),
                               ("known_at", TS, True), ("written_at", TS, True)),
                     key=("symbol", "ts"),
                     checks=(_on_day("ts"), _not_negative("volume"), _bar_shape,
                             _not_after("known_at", "written_at", "written before the bar finished")),
                     rank=lambda r: (r["derived"], r["source"] != "saved_day"), provenance=("source", "written_at"))

CONTEXT_QUOTES = Table("context_quotes", (("day", DAY, True), ("symbol", STR, True), ("served_as", STR, True),
                                          ("taken_at", TS, True), ("last", NUM, True), ("prior_close", NUM, False),
                                          ("volume", NUM, False), ("quote_time", INT, False)),
                       key=("symbol", "taken_at"),
                       checks=(_on_day("taken_at"), _not_negative("volume"),
                               lambda r: "a quote of zero is an empty shell, not a price" if r["last"] == 0 else None))

OVERNIGHT_BARS = Table("overnight_bars", (("day", DAY, True), ("ts", TS, True), ("symbol", STR, True), ("contract", STR, False),
                                          ("contract_from", STR, False), ("bar_minutes", INT, True), ("prior_session", BOOL, True)) + _BAR
                       + (("session", STR, False), ("source", STR, False), ("saved_at", TS, True), ("flags", WORDS, False),
                          ("store_schema", INT, False)),
                       key=("symbol", "bar_minutes", "ts"),
                       checks=(_is("bar_minutes", (1, 5)), _positive("open", "high", "low", "close"), _not_negative("volume"),
                               _bar_shape, _night_ends_by_close, _saved_after_finish))

ROLLS = Table("rolls", (("day", DAY, True), ("symbol", STR, True), ("rolled_at", TS, False), ("from_contract", STR, True),
                        ("to_contract", STR, True), ("basis_step", NUM, False), ("basis_unit", STR, False),
                        ("step_vs_typical", NUM, False), ("reference", STR, False), ("jump", NUM, False)),
              key=("symbol", "day"),
              checks=(_roll_changes, lambda r: None if r["rolled_at"] is None or r["rolled_at"].astimezone(ET).date() <= r["day"]
                      else f"rolled_at {r['rolled_at'].isoformat()} is after its day {r['day']}"))

EVENTS = Table("events", (("day", DAY, True), ("starts_at", TS, True), ("ends_at", TS, False), ("kind", STR, True),
                          ("tier", STR, True), ("scope", STR, False), ("in_session", BOOL, False), ("verified", BOOL, False),
                          ("q_and_a", BOOL, False), ("note", STR, False), ("source", STR, False), ("calendar_built", STR, False)),
               key=("kind", "starts_at"),
               checks=(_on_day("starts_at"), _ends_after_start))

QUARANTINE = Table("quarantine", (("day", DAY, True), ("table_name", STR, True), ("key", STR, False), ("reason", STR, True),
                                  ("source", STR, True), ("row_json", STR, True)), key=())

VALIDATION = Table("validation", (("day", DAY, True), ("table_name", STR, True), ("rows_in", INT, True), ("kept", INT, True),
                                  ("duplicates", INT, True), ("superseded", INT, True), ("quarantined", INT, True),
                                  ("reasons", COUNTS, True), ("sources", WORDS, True), ("store_schema", INT, True),
                                  ("built_at", TS, True)), key=())

TABLES = (READS, FACTS, ANSWERS, CALLS, GRADES, SPX_BARS, CONTEXT_BARS, CONTEXT_QUOTES, OVERNIGHT_BARS, ROLLS, EVENTS)
BY_NAME = {t.name: t for t in TABLES + (QUARANTINE, VALIDATION)}


# ----------------------------------------------------------------------------- raw files

def _lines(path: Path) -> Iterator[tuple[int, Any]]:
    """Each non-blank line of a JSONL file with its number, parsed, or None for a line that is not UTF-8 JSON."""
    if not path.exists():
        return
    with open(path, "rb") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield n, json.loads(line.decode("utf-8"))
                except ValueError:                     # not JSON, or not UTF-8
                    yield n, None


def _json_file(path: Path) -> tuple[Any, bool]:
    """``(document, readable)``: None and True for a file that is not there."""
    try:
        return (json.loads(path.read_text(encoding="utf-8")) if path.exists() else None), True
    except (OSError, ValueError):
        return None, False


def _parse(v: Any) -> datetime | None:
    try:
        return _instant(v)
    except (TypeError, ValueError):
        return None


def _clock(ts: Any) -> tuple[int | None, int | None]:
    """The read's minute of the New York day and its minutes from the open (negative before it)."""
    t = _parse(ts)
    if t is None:
        return None, None
    return t.hour * 60 + t.minute, math.floor((t - session_open(t)).total_seconds() / 60)


def _label_unit(symbol: str, v: Any) -> Any:
    """A market-feed number in the unit the labels read (state_builder.context_value); anything else as it is."""
    return context_value(symbol, v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _json(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)


def question_hash(question: dict) -> str:
    """The question exactly as JEV was sent it (ask.jev_only): its type, instructions and criteria."""
    return hashlib.sha256(json.dumps(question, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def _skip_status(qid: str, reason: str, not_due: dict) -> str:
    if qid in not_due:
        return "not_due"
    for prefix, status in (("asleep:", "asleep"), ("missing ", "missing"), ("dark:", "dark"), ("the group does not read", "unread")):
        if reason.startswith(prefix):
            return status
    return "other"


@dataclass
class Raw:
    """What one day's raw files hold, before any check: rows per table, each with its ``_source`` (file and
    line), and the lines no table could read."""
    rows: dict[str, list[dict]]
    unreadable: list[dict]
    sources: dict[str, list[str]]


def _unreadable(day: date, source: str, value: Any, why: str) -> dict:
    return {"day": day, "table_name": "raw_line", "key": None, "reason": why, "source": source, "row_json": _json(value) or "null"}


def _asked_index(reads: list[tuple[str, dict]]) -> dict[tuple[str, str], dict]:
    """Every answer the day's reads got from JEV, by question and read time: where a held answer came from."""
    out = {}
    for _, rec in reads:
        responses = rec.get("responses") if isinstance(rec.get("responses"), dict) else {}
        for req in rec.get("requests") if isinstance(rec.get("requests"), list) else []:
            if not isinstance(req, dict) or not isinstance(req.get("questions"), dict):
                continue
            reply = responses.get(req.get("id"))
            answers = reply.get("answers") if isinstance(reply, dict) and isinstance(reply.get("answers"), dict) else {}
            for qid, q in req["questions"].items():
                if isinstance(answers.get(qid), dict):
                    out[(qid, rec.get("row_ts"))] = {"answer": answers[qid], "hash": question_hash(q), "model": reply.get("model")}
    return out


def _answer_fields(a: dict | None) -> dict:
    if not isinstance(a, dict):
        return {"type": None, "pick": None, "confidence": None, "probabilities": None, "score": None, "noul": None}
    p = a.get("probabilities")
    n = a.get("noul")
    if not isinstance(p, dict) and isinstance(n, (int, float)) and not isinstance(n, bool):
        p = {"true": n, "false": 1 - n}
    return {"type": a.get("type"), "pick": answer_pick(a), "confidence": answer_confidence(a), "probabilities": p,
            "score": a.get("score"), "noul": n}


def _read_rows(day: date, src: str, rec: dict, asked_by: dict, hours: dict) -> dict[str, list[dict]]:
    """One archive read as its reads, facts, answers and calls rows."""
    lane, row_ts = rec.get("lane"), rec.get("row_ts")
    rid = rec.get("read_id")
    base = {"day": day, "read_id": rid, "lane": lane, "row_ts": row_ts, "_source": src}
    cadence = rec.get("cadence") if isinstance(rec.get("cadence"), dict) else {}
    held, not_due = cadence.get("held") or {}, cadence.get("not_due") or {}
    reasked = cadence.get("reasked") or {}
    # an archive from before the lane recorded its re-asks (cadence.reasked) cannot say whether one was: None
    reask_known = "reasked" in cadence
    responses = rec.get("responses") if isinstance(rec.get("responses"), dict) else None
    pool_snap = rec.get("pool") if isinstance(rec.get("pool"), dict) else {}
    versions = next((s.get("members") for s in pool_snap.values() if isinstance(s, dict) and s.get("members")), {}) or {}

    answers: dict[str, dict] = {}
    for req in rec.get("requests") or []:
        gid = req.get("id")
        reply = responses.get(gid) if responses is not None else None
        for qid, q in (req.get("questions") or {}).items():
            a = ((reply or {}).get("answers") or {}).get(qid)
            if responses is None or not rec.get("sent"):
                status, reason = "unsent", "the read was not sent to JEV"
            elif not isinstance(reply, dict) or reply.get("error"):
                status, reason = "lost", (reply or {}).get("error") or "no reply for its group"
            elif not isinstance(a, dict):
                status, reason = "lost", "JEV's reply carries no answer for it"
            else:
                status, reason = "answered", None
            again = reasked.get(qid) if isinstance(reasked.get(qid), dict) else None
            answers[qid] = {**base, "group_id": gid, "question_id": qid, "status": status, "reason": reason,
                            **_answer_fields(a if status == "answered" else None),
                            "model": (reply or {}).get("model") if status == "answered" else None,
                            "question_hash": question_hash(q), "pool_version": versions.get(qid),
                            "held_from": None, "held_found": None, "reasked": (again is not None) if reask_known else None,
                            "reasked_from": (again or {}).get("row_ts"), "reask_why": (again or {}).get("why")}
    skip_reasons: dict[str, str] = {}
    for gid, qs in (rec.get("skipped") or {}).items():
        for qid, why in (qs or {}).items():
            if qid == "*" or qid in answers:
                continue
            skip_reasons[qid] = why
            answers[qid] = {**base, "group_id": gid, "question_id": qid, "status": _skip_status(qid, str(why), not_due),
                            "reason": why, **_answer_fields(None), "model": None, "question_hash": None,
                            "pool_version": versions.get(qid), "held_from": None, "held_found": None, "reasked": False if reask_known else None,
                            "reasked_from": None, "reask_why": None}
    for qid, since in held.items():
        if qid in answers and answers[qid]["status"] == "answered":
            continue
        source = asked_by.get((qid, since))
        row = answers.get(qid) or {**base, "group_id": None, "question_id": qid, "reason": None, "pool_version": versions.get(qid),
                                   "reasked": False if reask_known else None, "reasked_from": None, "reask_why": None}
        # a group that got no answer holds its questions' last answers, and the sum reads those (service.run_once)
        reason = f"lost, its last answer held: {row['reason']}" if row.get("status") == "lost" else row["reason"]
        answers[qid] = {**row, "status": "held", "reason": reason, **_answer_fields((source or {}).get("answer")),
                        "model": (source or {}).get("model"), "question_hash": (source or {}).get("hash"),
                        "held_from": since, "held_found": source is not None}

    asleep_reasons = {str(why)[len("asleep:"):].strip() for why in skip_reasons.values() if str(why).startswith("asleep:")}
    facts = []
    for family, labels in (rec.get("labels") or {}).items():
        for name, text in (labels or {}).items():
            facts.append({**base, "source": "label", "path": f"{family}.{name}", "family": family, "name": name,
                          "status": "written", "text": text, "reason": None, "value": None, "known_at": row_ts})
    for path, why in (rec.get("omitted") or {}).items():
        family, _, name = str(path).partition(".")
        facts.append({**base, "source": "label", "path": path, "family": family, "name": name or None,
                      "status": "asleep" if why in asleep_reasons else "omitted", "text": None, "reason": why,
                      "value": None, "known_at": row_ts})
    for symbol, seen in (rec.get("market_context") or {}).items():
        seen = seen if isinstance(seen, dict) else {}
        facts.append({**base, "source": "market_context", "path": symbol, "family": None, "name": None, "status": "written",
                      "text": None, "reason": None, "value": seen.get("value"), "known_at": seen.get("known_at")})

    hour = rec.get("hour") if isinstance(rec.get("hour"), dict) else {}
    ruler = rec.get("ruler") if isinstance(rec.get("ruler"), dict) else {}
    band = rec.get("band") if isinstance(rec.get("band"), dict) else {}
    event = rec.get("event") if isinstance(rec.get("event"), dict) else {}
    counts = Counter(a["status"] for a in answers.values())
    minute_et, from_open = _clock(row_ts)
    hour_error = (rec.get("hour_response") or {}).get("error") if isinstance(rec.get("hour_response"), dict) else None
    model = hour.get("model") or next((r.get("model") for r in (responses or {}).values() if isinstance(r, dict) and r.get("model")), None)
    read = {**base, "archived_at": rec.get("archived_at"), "minute_et": minute_et, "minutes_from_open": from_open,
            "checkpoint": rec.get("checkpoint"), "sent": rec.get("sent"), "model": model, "spot": rec.get("spot"),
            "sigma": rec.get("sigma"), "ruler_source": ruler.get("source") or ruler.get("kind"),
            "ruler_points": ruler.get("unit_points", ruler.get("points")), "ruler_unit_sigma": ruler.get("unit_sigma"),
            "ruler_sessions": ruler.get("sessions"), "ruler_omitted": ruler.get("omitted"),
            "band_flat_points": band.get("flat_points"), "band_big_points": band.get("big_points"),
            "event_kinds": [e.get("kind") for e in event.get("events") or [] if isinstance(e, dict)] if event else None,
            "event_soonest_min": event.get("soonest_min"), "event_within_30": event.get("within_30"),
            "questions": len(answers), **{s: counts.get(s, 0) for s in ANSWER_STATUSES},
            "reasked": sum(1 for a in answers.values() if a["reasked"]) if reask_known else None,
            "labels_written": sum(1 for f in facts if f["source"] == "label" and f["status"] == "written"),
            "labels_omitted": sum(1 for f in facts if f["source"] == "label" and f["status"] == "omitted"),
            "labels_asleep": sum(1 for f in facts if f["status"] == "asleep"),
            "market_values": sum(1 for f in facts if f["source"] == "market_context"),
            "sum_used": hour.get("used") if not isinstance(hour.get("used"), dict) else len(hour["used"]),
            "sum_left_out": hour.get("left_out"), "sum_missing": hour.get("missing"), "sum_error": hour_error,
            "skip_reasons": skip_reasons, "ruler_json": _json(rec.get("ruler")), "band_json": _json(rec.get("band")),
            "event_json": _json(rec.get("event")), "night_json": _json(rec.get("night")), "archive_schema": rec.get("schema_version")}
    return {"reads": [read], "facts": facts, "answers": list(answers.values()),
            "calls": _call_rows(base, rec, hour, pool_snap, hours.get((lane, row_ts)), minute_et, from_open)}


def _call_rows(base: dict, rec: dict, hour: dict, pool_snap: dict, hour_rec: dict | None, minute_et, from_open) -> list[dict]:
    lane = LANES.get(rec.get("lane"))
    if lane is None or not hour:
        return []
    exclude = (hour_rec or {}).get("learn_exclude") or {}
    blend = hour.get("blend") if isinstance(hour.get("blend"), dict) else {}
    out = []
    for h, (minutes, _) in lane.horizons.items():
        b = (hour.get("by") or {}).get(h)
        if not isinstance(b, dict) and h == hour.get("primary") and isinstance(hour.get("probabilities"), dict):
            b = hour
        if not isinstance(b, dict) or not isinstance(b.get("probabilities"), dict):
            continue
        jev = b.get("jev") if b.get("blended") and isinstance(b.get("jev"), dict) else b
        clock = b.get("clock") if isinstance(b.get("clock"), dict) else {}
        snap = pool_snap.get(h) if isinstance(pool_snap.get(h), dict) else {}
        views = b.get("views") if isinstance(b.get("views"), dict) else {}
        try:
            mark = grade.mark_at(base["row_ts"], minutes, lane)
        except (TypeError, ValueError):
            mark = None
        out.append({**base, "horizon": h, "minutes": minutes, "mark": mark, "minute_et": minute_et, "minutes_from_open": from_open,
                    "is_primary": h == lane.primary, "shown_source": hour.get("shown_source") or "jev", "shown_pick": b.get("pick"),
                    "shown_probs": b.get("probabilities"), "shown_confidence": b.get("confidence"),
                    "jev_pick": jev.get("pick"), "jev_probs": jev.get("probabilities"), "jev_confidence": jev.get("confidence"),
                    "clock_pick": clock.get("pick"), "clock_probs": clock.get("probabilities"), "clock_n": clock.get("n"),
                    "blended": b.get("blended"), "blend_jev_share": blend.get("jev_share"), "blend_phase": blend.get("phase"),
                    "blend_sessions": blend.get("sessions"), "pool_probs": snap.get("pool"), "pool_p_move": snap.get("p_move"),
                    "pool_p_up_given_move": snap.get("p_up_given_move"), "pool_state_hash": snap.get("state_hash"),
                    "pool_baseline": snap.get("baseline"), "pool_left_out": snap.get("left_out"),
                    "direction_pick": (views.get("direction") or {}).get("pick"),
                    "direction_probs": (views.get("direction") or {}).get("probabilities"),
                    "size_pick": (views.get("size") or {}).get("pick"), "size_probs": (views.get("size") or {}).get("probabilities"),
                    "learn_exclude": exclude.get(str(minutes)), "model": hour.get("model")})
    return out


def _grade_rows(day: date, src: str, rec: dict) -> list[dict]:
    g = rec.get("grade") if isinstance(rec.get("grade"), dict) else {}
    lane = LANES.get(rec.get("lane"))
    anchor = g.get("anchor") if isinstance(g.get("anchor"), dict) else {}
    start = g.get("from") if isinstance(g.get("from"), dict) else {}
    out = []
    for h in g.get("horizons") or []:
        d = g.get(h) if isinstance(g.get(h), dict) else {}
        minutes = lane.horizons[h][0] if lane and h in lane.horizons else None
        try:
            mark = grade.mark_at(rec.get("row_ts"), minutes, lane) if minutes is not None else None
        except (TypeError, ValueError):
            mark = None
        abstained = d.get("pick") == "unsure"
        out.append({"day": day, "read_id": rec.get("read_id"), "lane": rec.get("lane"), "row_ts": rec.get("row_ts"), "_source": src,
                    "horizon": h, "minutes": minutes, "mark": mark, "archived_at": rec.get("archived_at"), "outcome": d.get("band"),
                    **{k: d.get(k) for k in ("direction", "size", "realized_sigma", "realized_points", "realized_units", "pick",
                                             "hit", "brier", "p_band", "jev_pick", "jev_hit", "jev_brier", "clock_brier",
                                             "direction_pick", "direction_hit", "direction_brier", "size_pick", "size_hit",
                                             "size_brier")},
                    "abstained": abstained, "correct": None if abstained else d.get("hit"),
                    "anchor_points": anchor.get("points"), "anchor_source": anchor.get("source"),
                    "from_settled_open": start.get("settled_open"), "from_at": start.get("at")})
    return out


def _bar_row(day: date, bar: dict, **more: Any) -> dict:
    return {"day": day, "ts": bar.get("ts"), **{k: bar.get(k) for k in ("open", "high", "low", "close", "volume")}, **more}


def _context_rows(day: date, where: str, line: dict, source: str) -> dict[str, list[dict]]:
    """One market-feed line as its context_bars and context_quotes rows."""
    out: dict[str, list[dict]] = {"context_bars": [], "context_quotes": []}
    for key, bar in (line.get("bars") or {}).items():
        bar = bar if isinstance(bar, dict) else {}
        symbol = context_symbol(key)
        prices = {k: _label_unit(symbol, bar.get(k)) for k in ("open", "high", "low", "close")}
        done = _parse(bar.get("ts"))
        out["context_bars"].append({**_bar_row(day, {**bar, **prices}), "symbol": symbol, "served_as": key,
                                    "derived": bool(bar.get("derived")), "derived_from": bar.get("derived") or None,
                                    "source": source, "known_at": done + timedelta(minutes=1) if done else None,
                                    "written_at": line.get("ts"), "_source": where})
    for key, q in (line.get("quotes") or {}).items():
        q = q if isinstance(q, dict) else {}
        symbol = context_symbol(key)
        out["context_quotes"].append({"day": day, "symbol": symbol, "served_as": key, "taken_at": line.get("ts"),
                                      "last": _label_unit(symbol, q.get("last")),
                                      "prior_close": _label_unit(symbol, q.get("close")), "volume": q.get("volume"),
                                      "quote_time": q.get("quote_time"), "_source": where})
    return out


def read_raw(state_dir: Path, day: date) -> Raw:
    """Every row one day's raw files hold for each table, oldest source first within a table."""
    state_dir, iso = Path(state_dir), day.isoformat()
    rows: dict[str, list[dict]] = {t.name: [] for t in TABLES}
    unreadable: list[dict] = []
    sources: dict[str, list[str]] = {t.name: [] for t in TABLES}

    def rel(path: Path) -> str:
        return path.relative_to(state_dir).as_posix() if path.is_relative_to(state_dir) else str(path)

    def lines(path: Path, *tables: str) -> Iterator[tuple[str, dict]]:
        if path.exists():
            for t in tables:
                sources[t].append(rel(path))
        for n, obj in _lines(path):
            where = f"{rel(path)}:{n}"
            if isinstance(obj, dict):
                yield where, obj
            else:
                unreadable.append(_unreadable(day, where, obj, "not a JSON object"))

    def take(where: str, obj: dict, what: str, parse: Callable[[], dict[str, list[dict]]]) -> None:
        """``parse``'s rows for their tables, or the whole line quarantined when its parts are not the shapes
        its writer writes."""
        try:
            got = parse()
        except (AttributeError, TypeError, ValueError) as e:
            unreadable.append(_unreadable(day, where, obj, f"a malformed {what}: {type(e).__name__}: {e}"))
            return
        for table, more in got.items():
            rows[table].extend(more)

    archive = list(lines(state_dir / ARCHIVE_SUBDIR / f"{iso}.jsonl", "reads", "facts", "answers", "calls", "grades"))
    reads = [(w, r) for w, r in archive if r.get("kind") == "read"]
    for w, r in archive:
        if r.get("kind") not in ("read", "grade", "close_out"):
            unreadable.append(_unreadable(day, w, r, f"an archive record of unknown kind {r.get('kind')!r}"))
    hours = {}
    for name, lane in LANES.items():
        for w, h in lines(lane.folder(state_dir) / "hour" / f"{iso}.jsonl", "calls"):
            if isinstance(h.get("row_ts"), str):
                hours[(name, h["row_ts"])] = h
            else:
                unreadable.append(_unreadable(day, w, h, "an hour record whose row_ts is not text"))
    asked_by = _asked_index(reads)
    for w, r in archive:
        take(w, r, f"{r.get('kind')} record", lambda: _read_rows(day, w, r, asked_by, hours) if r.get("kind") == "read" else
             {"grades": _grade_rows(day, w, r)} if r.get("kind") == "grade" else {})

    session = state_dir / SESSION_BARS_SUBDIR / f"{iso}-{SYMBOL}.json"
    saved, readable = _json_file(session)
    if session.exists():
        sources["spx_bars"].append(rel(session))
    if not readable or (saved is not None and not isinstance(saved, list)):
        unreadable.append(_unreadable(day, rel(session), None, "not a JSON list of bars"))
    elif saved:
        for i, bar in enumerate(saved):
            where = f"{rel(session)}[{i}]"
            if isinstance(bar, dict):
                rows["spx_bars"].append({**_bar_row(day, bar, source="session_file"), "_source": where})
            else:
                unreadable.append(_unreadable(day, where, bar, "not a JSON object"))
    for w, bar in lines(state_dir / LIVE_BARS_SUBDIR / f"{iso}.jsonl", "spx_bars"):
        rows["spx_bars"].append({**_bar_row(day, bar, source="live_file"), "_source": w})

    folder = state_dir / CONTEXT_SUBDIR
    for path, source in ((folder / "bars" / f"{iso}.jsonl", "saved_day"), (folder / f"{iso}.jsonl", "live")):
        for w, line in lines(path, "context_bars", *(("context_quotes",) if source == "live" else ())):
            take(w, line, "market-feed line", lambda: _context_rows(day, w, line, source))

    prior_close = session_close(datetime.combine(previous_trading_day(day), datetime.min.time(), tzinfo=ET))
    for w, bar in lines(state_dir / OVERNIGHT_SUBDIR / f"{iso}.jsonl", "overnight_bars"):
        ts = _parse(bar.get("ts"))
        rows["overnight_bars"].append({**_bar_row(day, bar), "prior_session": ts < prior_close if ts else None,
                                       **{k: bar.get(k) for k in ("symbol", "contract", "contract_from", "bar_minutes",
                                                                  "session", "source", "saved_at", "flags")},
                                       "store_schema": bar.get("schema_version"), "_source": w})

    table = rolls_path(state_dir / OVERNIGHT_SUBDIR)
    doc, readable = _json_file(table)
    if table.exists():
        sources["rolls"].append(rel(table))
    if not readable or (doc is not None and not isinstance(doc, dict)):
        unreadable.append(_unreadable(day, rel(table), None, "not a JSON object"))
    elif doc:
        for i, r in enumerate(doc.get("rolls") or []):
            if isinstance(r, dict) and r.get("day") == iso:
                rows["rolls"].append({"day": day, "symbol": r.get("symbol"), "rolled_at": r.get("at"), "from_contract": r.get("from"),
                                      "to_contract": r.get("to"), **{k: r.get(k) for k in ("basis_step", "basis_unit",
                                                                                          "step_vs_typical", "reference", "jump")},
                                      "_source": f"{rel(table)}#rolls[{i}]"})

    calendar, _ = _json_file(CALENDAR)
    sources["events"].append(CALENDAR.name)
    if not isinstance(calendar, dict):
        unreadable.append(_unreadable(day, str(CALENDAR), None, "the calendar is not a JSON object"))
    else:
        for i, e in enumerate(calendar.get("events") or []):
            if not isinstance(e, dict) or e.get("date") != iso:
                continue
            start = f"{iso}T{e.get('time_et')}" if isinstance(e.get("time_et"), str) else None
            end = f"{iso}T{e.get('end_et')}" if isinstance(e.get("end_et"), str) else None
            rows["events"].append({"day": day, "starts_at": _local(start), "ends_at": _local(end),
                                   **{k: e.get(k) for k in ("kind", "scope", "in_session", "verified", "note", "source")},
                                   "tier": None if e.get("tier") is None else str(e.get("tier")), "q_and_a": e.get("q_and_a") is True,
                                   "calendar_built": calendar.get("built"), "_source": f"{CALENDAR.name}#events[{i}]"})
    return Raw(rows, unreadable, sources)


def _local(s: str | None) -> Any:
    """A calendar's ``date``T``time_et`` in New York time, or the text as written when it is not a time."""
    if s is None:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=ET)
    except ValueError:
        return s


# ----------------------------------------------------------------------------- the checks

def _key_text(table: Table, raw: dict) -> str | None:
    if not table.key:
        return None
    return "|".join(str(raw.get(c)) for c in table.key)


def typed(table: Table, raw: dict) -> tuple[dict | None, str | None]:
    """The row with every column read as its type, or None and why the first column that is not failed."""
    row = {}
    for name, kind, required in table.columns:
        try:
            row[name] = _coerce(raw.get(name), kind)
        except (TypeError, ValueError) as e:
            return None, f"{name}: {e}"
        if required and row[name] is None:
            return None, f"{name} is required and empty"
    return row, None


def validate(table: Table, rows: list[dict], reads: set[str] | None = None) -> tuple[list[dict], list[dict], dict]:
    """``(kept, quarantined, counts)`` for one table's rows of one day. ``reads`` is the read ids kept for the
    day, which a row of a read's table must name."""
    kept: dict[tuple, dict] = {}
    first_seen: dict[tuple, str] = {}
    out: list[dict] = []
    counts = {"rows_in": len(rows), "duplicates": 0, "superseded": 0}

    def values(row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in table.provenance}

    def refuse(raw: dict, why: str) -> None:
        out.append({"day": raw.get("day"), "table_name": table.name, "key": _key_text(table, raw), "reason": why,
                    "source": raw.get("_source") or "", "row_json": json.dumps({k: v for k, v in raw.items() if k != "_source"},
                                                                               default=str, ensure_ascii=False, sort_keys=True)})

    for raw in rows:
        row, why = typed(table, raw)
        if why is None:
            why = next((w for check in table.checks if (w := check(row))), None)
        if why is None and table.of_read and row["read_id"] not in (reads or set()):
            why = f"its read {row['read_id']} is not among the day's kept reads"
        if why is not None:
            refuse(raw, why)
            continue
        key = tuple(row[c] for c in table.key)
        if key not in kept:
            kept[key], first_seen[key] = row, raw.get("_source") or ""
            continue
        same = values(kept[key]) == values(row)
        ranks = (table.rank(row), table.rank(kept[key])) if table.rank is not None else None
        if same or (ranks and ranks[0] != ranks[1]):
            counts["duplicates" if same else "superseded"] += 1
            if ranks and ranks[0] < ranks[1]:
                kept[key], first_seen[key] = row, raw.get("_source") or ""
        else:
            refuse(raw, f"differs from the row kept for the same {', '.join(table.key)} ({first_seen[key]})")
    counts["kept"], counts["quarantined"] = len(kept), len(out)
    return list(kept.values()), out, counts


# ----------------------------------------------------------------------------- writing

def store_dir(state_dir: Path | str) -> Path:
    return Path(state_dir) / STORE_SUBDIR


def partition(store: Path, table: str, day: date) -> Path:
    return store / table / f"day={day.isoformat()}" / PART


def _write(store: Path, table: Table, day: date, rows: list[dict]) -> Path:
    """The day's partition of ``table``, replaced whole: written beside it and renamed over it, so a reader
    sees the old file or the new one, never half of one."""
    path = partition(store, table.name, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{PART}.{os.getpid()}.tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), tmp)
    os.replace(tmp, path)
    return path


def build_day(state_dir: Path | str, day: date, now: datetime | None = None) -> dict[str, dict]:
    """Rebuild one day of every table from its raw files: read, check, write. Returns the validation counts by table."""
    now = now or datetime.now(ET)
    raw = read_raw(Path(state_dir), day)
    store = store_dir(state_dir)
    kept: dict[str, list[dict]] = {}
    quarantined = list(raw.unreadable)
    log: dict[str, dict] = {}
    read_ids: set[str] = set()
    for table in TABLES:
        rows, bad, counts = validate(table, raw.rows[table.name], read_ids)
        if table is READS:
            read_ids = {r["read_id"] for r in rows}
        kept[table.name], log[table.name] = rows, {**counts, "reasons": dict(Counter(b["reason"] for b in bad))}
        quarantined.extend(bad)
    if raw.unreadable:
        log["raw_line"] = {"rows_in": len(raw.unreadable), "kept": 0, "duplicates": 0, "superseded": 0,
                           "quarantined": len(raw.unreadable), "reasons": dict(Counter(b["reason"] for b in raw.unreadable))}
    for table in TABLES:
        _write(store, table, day, kept[table.name])
    _write(store, QUARANTINE, day, quarantined)
    _write(store, VALIDATION, day, [{"day": day, "table_name": name, **counts, "sources": raw.sources.get(name, []),
                                     "store_schema": SCHEMA_VERSION, "built_at": now} for name, counts in log.items()])
    return log


def _views_current(db: Path, store: Path) -> bool:
    """The DuckDB file already has a view per table over this store at this schema. Opened read only, so a
    notebook holding the file open for reading never blocks the nightly run."""
    if not db.exists():
        return False
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            views = {r[0] for r in con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()}
            meta = con.execute("SELECT schema_version, store FROM meta").fetchone()
    except duckdb.Error:
        return False
    return views >= set(BY_NAME) and meta == (SCHEMA_VERSION, str(store))


def _held_open(e: duckdb.Error) -> bool:
    """Another process has the DuckDB file open for writing (DuckDB has no exception of its own for this)."""
    return isinstance(e, duckdb.IOException) and "Could not set lock" in str(e)


def write_views(store: Path) -> bool:
    """A view per table over its day files, and ``meta`` (the schema version and the store it reads), in
    ``spx_jev.duckdb``; nothing is written when they are already there. True when the file was written."""
    db = store / DB_NAME
    if _views_current(db, store):
        return False
    with duckdb.connect(str(db)) as con:
        for name in BY_NAME:
            files = (store / name).as_posix().replace("'", "''") + "/day=*/*.parquet"
            con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT day, * EXCLUDE (day) FROM read_parquet('{files}', "
                        f"hive_partitioning = true, hive_types = {{'day': DATE}}, union_by_name = true)")
        con.execute("CREATE OR REPLACE TABLE meta AS SELECT ? AS schema_version, ? AS store, ? AS written_at",
                    [SCHEMA_VERSION, str(store), datetime.now(timezone.utc)])
    return True


# ----------------------------------------------------------------------------- which days

def last_finished(now: datetime) -> date:
    """The newest market day whose after-close saves have run: today BUILD_AFTER_CLOSE_MIN after its close."""
    now = now.astimezone(ET)
    today = now.date()
    if is_trading_day(today) and now >= session_close(now) + timedelta(minutes=BUILD_AFTER_CLOSE_MIN):
        return today
    return previous_trading_day(today)


def days_to_build(now: datetime, catch_up: int = CATCH_UP_DAYS) -> list[date]:
    """The market days from ``catch_up`` calendar days back to the last finished one."""
    last = last_finished(now)
    first = now.astimezone(ET).date() - timedelta(days=catch_up)
    return [d for k in range((last - first).days + 1) if is_trading_day(d := first + timedelta(days=k))]


def _days_named(state_dir: Path | str, until: date) -> set[date]:
    """Every day up to ``until`` that any raw file the store reads is named for."""
    state_dir = Path(state_dir)
    names = [p.stem for folder in (ARCHIVE_SUBDIR, LIVE_BARS_SUBDIR, CONTEXT_SUBDIR, CONTEXT_SUBDIR / "bars", OVERNIGHT_SUBDIR)
             for p in (state_dir / folder).glob("????-??-??.jsonl")]
    names += [p.name[:10] for p in (state_dir / SESSION_BARS_SUBDIR).glob(f"????-??-??-{SYMBOL}.json")]
    out = set()
    for n in names:
        try:
            d = date.fromisoformat(n)
        except ValueError:
            continue
        if d <= until:
            out.add(d)
    return out


def days_on_disk(state_dir: Path | str, until: date) -> list[date]:
    """Every market day up to ``until`` that any raw file the store reads is named for."""
    return sorted(d for d in _days_named(state_dir, until) if is_trading_day(d))


def off_days_on_disk(state_dir: Path | str, until: date) -> list[date]:
    """The days up to ``until`` a raw file is named for that are not market days (a holiday's futures, say),
    which the store, a table of market days, leaves out."""
    return sorted(d for d in _days_named(state_dir, until) if not is_trading_day(d))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the learning store under state/spx_jev/store/: each market day's raw "
                                             "files as checked Parquet, with DuckDB views over them.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--day", help="YYYY-MM-DD, rebuild one day")
    which.add_argument("--backfill", action="store_true", help="rebuild every market day with a raw file on disk")
    args = ap.parse_args(argv)
    state_dir, now = Path(args.state_dir).resolve(), datetime.now(ET)     # the views name their files by this path
    if args.day:
        days = [date.fromisoformat(args.day)]
    elif args.backfill:
        days = days_on_disk(state_dir, last_finished(now))
        if off := off_days_on_disk(state_dir, last_finished(now)):
            print(f"spx-jev-store :: not market days, so not stored: the raw files of {', '.join(map(str, off))}")
    else:
        days = days_to_build(now)
    if not days:
        print("spx-jev-store :: no finished market day to build")
        return 0
    kept, quarantined, failed = Counter(), Counter(), 0
    for d in days:
        try:
            log = build_day(state_dir, d, now)
        except Exception as e:  # one day's bad file must not cost the other days; the next run rebuilds it
            failed += 1
            print(f"spx-jev-store :: {d} failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        kept.update({t: c["kept"] for t, c in log.items()})
        quarantined.update({t: c["quarantined"] for t, c in log.items()})
        bad = {t: c["quarantined"] for t, c in log.items() if c["quarantined"]}
        print(f"spx-jev-store :: {d} " + ", ".join(f"{t} {c['kept']}" for t, c in log.items() if t in BY_NAME)
              + (f"; quarantined {', '.join(f'{t} {n}' for t, n in bad.items())}" if bad else ""))
    store = store_dir(state_dir)
    try:
        wrote = write_views(store)
    except duckdb.Error as e:
        if not _held_open(e):
            print(f"spx-jev-store :: views not written to {store / DB_NAME}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        # the Parquet is built; the views are the only thing waiting, and the next run writes them if they need it
        print(f"spx-jev-store :: views left as they were: another process has {store / DB_NAME} open for writing",
              file=sys.stderr)
        wrote = False
    print(f"spx-jev-store :: {len(days) - failed} of {len(days)} days built under {store}; "
          + ", ".join(f"{t.name} {kept[t.name]} rows ({quarantined[t.name]} quarantined)" for t in TABLES)
          + (f"; raw_line {quarantined['raw_line']} quarantined" if quarantined["raw_line"] else "")
          + ("; views written" if wrote else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
