"""The answer matrix: one row per past read, with every answer side by side and what price did next.

Built by the nightly job from the live store's Parquet tables (read through name_map) and saved with the day's fits, so
the live hook never scans the store; the hook only adds today's row to it:

    forecasts_at_read_time (calls)   one row per sum per read: the historical odds (clock_probs), JEV's own call (jev_probs),
                                     the call the phone showed (shown_probs), pool_v1's mix (pool_probs), learn_exclude
    graded_results (average_grades)  the result of that sum's window: up, flat or down, joined on (read_id, sum_id) - never on horizon
    jev_answers (answers)            layer 3: JEV's pick per question where status == "answered"; anything else is silent (None);
                                     the questions of one request group share one group, so a group casts one vote in the scorer
    code_facts (facts)               interim layers 1-2 until the code feature builder exists:
                                       layer 2 "fact:<name>"    a code label whose text takes few distinct values (a category, e.g. gap_open)
                                       layer 1 "level:<symbol>" a market value ranked against that symbol's own earlier values:
                                                                "low" (bottom 30%), "middle", "high" (top 30%); None with too little history

SHRINK FIRST, THEN JOIN: each long table is pivoted to one row per read before joining, so a read never multiplies
(250 facts x 25 answers x 2 sums would be thousands of rows). The base is forecasts_at_read_time JOIN graded_results
on (read_id, sum_id); the pivots are LEFT-joined onto it, so a read with no answers is kept with blanks, never dropped.

WALK-FORWARD: build_answer_matrix(..., before_day) holds only reads from days strictly before ``before_day``, and a
market value is ranked only against values from earlier DAYS (never the same day's earlier reads, so a training row is
labelled exactly as a live row is), so nothing a learner sees comes from its own day or later.
"""
from __future__ import annotations

import bisect
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ..scores import OUTCOMES
from .name_map import store_path

MAX_DISTINCT_LABEL_TEXTS = 8      # a code label with more distinct texts than this is free text, not a category
MIN_HISTORY_FOR_RANK = 20         # a market value needs this many earlier values before it is ranked low / middle / high
RANK_LOW_PERCENT = 30.0           # bottom 30% -> "low", top 30% -> "high", between -> "middle"
RANK_HIGH_PERCENT = 70.0
RANK_HISTORY_SESSIONS = 60        # rank against the trailing 60 sessions only
LAYER_OF_PREFIX = {"level": 1, "fact": 2, "jev": 3}


@dataclass
class Row:
    """One read's row: its answers across the layers, the forecasts made at read time, and the graded result."""
    read_id: str
    row_ts: str
    day: str
    sum_id: str
    historical_odds_probs: dict | None
    jev_own_probs: dict | None
    shown_probs: dict | None
    pool_v1_probs: dict | None
    outcome: str | None                      # up / flat / down, or None while the window is still open or ungraded
    learn_exclude: bool
    answers: dict[str, str | None] = field(default_factory=dict)   # column id -> label, None when silent
    shown_source: str | None = None          # what the shown call was: the blend, pool_v1 or (since 2026-10-06) pool_v2

    def is_trainable(self) -> bool:
        return self.outcome in OUTCOMES and not self.learn_exclude and self.historical_odds_probs is not None


@dataclass
class AnswerMatrix:
    lane: str
    sum_id: str
    built_for_day: str                        # every row's day is strictly before this
    rows: list[Row]
    columns: dict[str, dict]                  # column id -> {"layer": 1|2|3, "group": str, "family": str}
    level_history: dict[str, list[tuple[str, float]]]   # symbol -> [(row_ts, value)] sorted by row_ts, for ranking new values

    def trainable_rows(self) -> list[Row]:
        return [r for r in self.rows if r.is_trainable()]

    def max_day(self) -> str | None:
        return max((r.day for r in self.rows), default=None)

    def to_json(self) -> dict:
        return {"lane": self.lane, "sum_id": self.sum_id, "built_for_day": self.built_for_day, "rows": [asdict(r) for r in self.rows],
                "columns": self.columns, "level_history": self.level_history}

    @classmethod
    def from_json(cls, d: dict) -> "AnswerMatrix":
        return cls(lane=d["lane"], sum_id=d["sum_id"], built_for_day=d["built_for_day"], rows=[Row(**r) for r in d["rows"]],
                   columns=d["columns"], level_history={s: [(ts, float(v)) for ts, v in series] for s, series in d["level_history"].items()})


# ---------------------------------------------------------------- reading the store

def _probs(v) -> dict | None:
    """A stored probability map as {up, flat, down} floats, or None when absent or malformed."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return None
    if not isinstance(v, dict):
        return None
    try:
        out = {k: float(v.get(k) or 0.0) for k in OUTCOMES}
    except (TypeError, ValueError):
        return None
    return out if sum(out.values()) > 0 else None


def _parquet_glob(folder: Path) -> str | None:
    """The glob over a hive-partitioned store folder, or None when the folder holds no files yet."""
    if not folder.exists() or not any(folder.glob("day=*/*.parquet")):
        return None
    return str(folder / "*" / "*.parquet")


def _query(sql: str) -> list[dict]:
    import duckdb
    rel = duckdb.sql(sql)
    cols = rel.columns
    return [dict(zip(cols, rec)) for rec in rel.fetchall()]


def load_base_rows(state_dir: Path | str, lane: str, sum_id: str, before_day: str) -> list[Row]:
    """forecasts_at_read_time JOIN graded_results on (read_id, sum_id): one Row per read of this sum, days before ``before_day``,
    without answers yet. A read whose window is not graded yet has outcome None."""
    calls = _parquet_glob(store_path(state_dir, "forecasts_at_read_time"))
    if calls is None:
        return []
    grades = _parquet_glob(store_path(state_dir, "graded_results"))
    grade_join = ""
    grade_cols = "NULL AS outcome"
    if grades is not None:
        grade_join = (f"LEFT JOIN (SELECT read_id, sum_id, outcome FROM read_parquet('{grades}', hive_partitioning=1) "
                      f"WHERE graded AND lane = '{lane}') g ON g.read_id = c.read_id AND g.sum_id = c.sum_id")
        grade_cols = "g.outcome AS outcome"
    sql = f"""
        SELECT c.read_id, CAST(c.row_ts AS VARCHAR) AS row_ts, CAST(c.day AS VARCHAR) AS day, c.sum_id,
               c.clock_probs, c.jev_probs, c.shown_probs, c.pool_probs, c.learn_exclude, c.shown_source, {grade_cols}
        FROM read_parquet('{calls}', hive_partitioning=1) c
        {grade_join}
        WHERE c.lane = '{lane}' AND c.sum_id = '{sum_id}' AND CAST(c.day AS VARCHAR) < '{before_day}'
        ORDER BY c.row_ts
    """
    rows = []
    for r in _query(sql):
        rows.append(Row(read_id=r["read_id"], row_ts=str(r["row_ts"]), day=str(r["day"])[:10], sum_id=sum_id,
                        historical_odds_probs=_probs(r["clock_probs"]), jev_own_probs=_probs(r["jev_probs"]),
                        shown_probs=_probs(r["shown_probs"]), pool_v1_probs=_probs(r["pool_probs"]),
                        outcome=r["outcome"] if r["outcome"] in OUTCOMES else None, learn_exclude=bool(r["learn_exclude"]),
                        shown_source=r["shown_source"]))
    return rows


def load_jev_answers(state_dir: Path | str, lane: str, before_day: str) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """(read_id -> {question_id: pick} for answered questions only, question_id -> its request group) for layer 3."""
    answers = _parquet_glob(store_path(state_dir, "jev_answers"))
    if answers is None:
        return {}, {}
    sql = f"""
        SELECT read_id, question_id, pick, group_id FROM read_parquet('{answers}', hive_partitioning=1)
        WHERE lane = '{lane}' AND status = 'answered' AND pick IS NOT NULL AND CAST(day AS VARCHAR) < '{before_day}'
    """
    picks: dict[str, dict[str, str]] = defaultdict(dict)
    group_of: dict[str, str] = {}
    for r in _query(sql):
        picks[r["read_id"]][r["question_id"]] = str(r["pick"])
        if r["group_id"]:
            group_of[r["question_id"]] = str(r["group_id"])
    return picks, group_of


def load_code_facts(state_dir: Path | str, lane: str, before_day: str) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, list[tuple[str, float]]]]:
    """The code's written labels and market values before ``before_day``:
    (read_id -> {name: text}, name -> family, symbol -> [(row_ts, value)] sorted by row_ts)."""
    facts = _parquet_glob(store_path(state_dir, "code_facts"))
    if facts is None:
        return {}, {}, {}
    sql = f"""
        SELECT read_id, CAST(row_ts AS VARCHAR) AS row_ts, source, path, family, name, text, value
        FROM read_parquet('{facts}', hive_partitioning=1)
        WHERE lane = '{lane}' AND status = 'written' AND CAST(day AS VARCHAR) < '{before_day}'
    """
    labels: dict[str, dict[str, str]] = defaultdict(dict)
    family_of: dict[str, str] = {}
    levels: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in _query(sql):
        if r["source"] == "label" and r["name"] and r["text"] is not None:
            labels[r["read_id"]][r["name"]] = str(r["text"])
            family_of[r["name"]] = r["family"] or "label"
        elif r["source"] == "market_context" and r["path"] and r["value"] is not None:
            try:
                levels[r["path"]].append((str(r["row_ts"]), float(r["value"])))
            except (TypeError, ValueError):
                continue
    for symbol in levels:
        levels[symbol].sort()
    return labels, family_of, levels


# ---------------------------------------------------------------- labels

def categorical_label_names(labels_by_read: dict[str, dict[str, str]]) -> set[str]:
    """The code labels whose text repeats (a category), not the ones that embed numbers (free text)."""
    texts: dict[str, set[str]] = defaultdict(set)
    for per_read in labels_by_read.values():
        for name, text in per_read.items():
            texts[name].add(text)
    return {name for name, seen in texts.items() if 1 <= len(seen) <= MAX_DISTINCT_LABEL_TEXTS}


def level_days(level_history: dict[str, list[tuple[str, float]]]) -> dict[str, list[str]]:
    """symbol -> its sorted distinct days, so rank_label can cut the trailing sessions with a bisect."""
    return {symbol: sorted({ts[:10] for ts, _ in series}) for symbol, series in level_history.items()}


def rank_label(history: list[tuple[str, float]], days: list[str], row_ts: str, value: float) -> str | None:
    """A value's place among the symbol's values from EARLIER DAYS (the trailing RANK_HISTORY_SESSIONS of them):
    "low", "middle", "high", or None with fewer than MIN_HISTORY_FOR_RANK such values. ``days`` are the history's sorted
    distinct days (level_days)."""
    row_day = row_ts[:10]
    cut = bisect.bisect_left(history, (row_day, float("-inf")))          # the first value on or after this read's day
    earlier_days = bisect.bisect_left(days, row_day)
    start = 0
    if earlier_days > RANK_HISTORY_SESSIONS:
        start = bisect.bisect_left(history, (days[earlier_days - RANK_HISTORY_SESSIONS], float("-inf")))
    earlier = history[start:cut]
    if len(earlier) < MIN_HISTORY_FOR_RANK:
        return None
    values = sorted(v for _, v in earlier)
    below = bisect.bisect_left(values, value)
    percent = 100.0 * below / len(values)
    if percent < RANK_LOW_PERCENT:
        return "low"
    if percent >= RANK_HIGH_PERCENT:
        return "high"
    return "middle"


def row_answers(row_ts: str, jev_answers: dict[str, str], labels: dict[str, str], label_names: set[str],
                level_values: dict[str, float], level_history: dict[str, list[tuple[str, float]]],
                days_by_symbol: dict[str, list[str]]) -> dict[str, str | None]:
    """One read's answers across the layers, keyed by column id."""
    out: dict[str, str | None] = {}
    for name in label_names:
        out[f"fact:{name}"] = labels.get(name)
    for symbol, value in level_values.items():
        out[f"level:{symbol}"] = rank_label(level_history.get(symbol, []), days_by_symbol.get(symbol, []), row_ts, value)
    for qid, pick in jev_answers.items():
        out[f"jev:{qid}"] = pick
    return out


def column_catalog(label_names: set[str], family_of: dict[str, str], symbols: set[str], question_ids: set[str],
                   group_of_question: dict[str, str]) -> dict[str, dict]:
    """Every column with its layer, group and family. A layer-3 question's group is its request group (the questions JEV
    was asked together), so the scorer takes one vote per group; a layer 1-2 column is its own group until the code feature
    builder names its groups."""
    cols: dict[str, dict] = {}
    for symbol in sorted(symbols):
        cols[f"level:{symbol}"] = {"layer": 1, "group": f"level:{symbol}", "family": "market_level"}
    for name in sorted(label_names):
        cols[f"fact:{name}"] = {"layer": 2, "group": f"fact:{name}", "family": family_of.get(name, "label")}
    for qid in sorted(question_ids):
        cols[f"jev:{qid}"] = {"layer": 3, "group": f"jev:{group_of_question.get(qid, qid)}", "family": "jev"}
    return cols


# ---------------------------------------------------------------- building

def build_answer_matrix(state_dir: Path | str, lane: str, sum_id: str, before_day: str) -> AnswerMatrix:
    """The matrix for one lane and sum, holding reads from days strictly before ``before_day`` (YYYY-MM-DD)."""
    base = load_base_rows(state_dir, lane, sum_id, before_day)
    jev_answers, group_of_question = load_jev_answers(state_dir, lane, before_day)
    labels, family_of, level_history = load_code_facts(state_dir, lane, before_day)
    label_names = categorical_label_names(labels)
    days_by_symbol = level_days(level_history)
    value_at: dict[str, dict[str, float]] = defaultdict(dict)       # read_id -> symbol -> value (this read's own value)
    ts_of = {r.read_id: r.row_ts for r in base}
    for symbol, series in level_history.items():
        by_ts = dict(series)
        for read_id, ts in ts_of.items():
            if ts in by_ts:
                value_at[read_id][symbol] = by_ts[ts]
    question_ids = {q for per_read in jev_answers.values() for q in per_read}
    for row in base:
        row.answers = row_answers(row.row_ts, jev_answers.get(row.read_id, {}), labels.get(row.read_id, {}),
                                  label_names, value_at.get(row.read_id, {}), level_history, days_by_symbol)
    columns = column_catalog(label_names, family_of, set(level_history), question_ids, group_of_question)
    for row in base:
        for col in columns:
            row.answers.setdefault(col, None)
    return AnswerMatrix(lane=lane, sum_id=sum_id, built_for_day=before_day, rows=base, columns=columns,
                        level_history=dict(level_history))


# ---------------------------------------------------------------- today's read (for the live hook)

def todays_rows(state_dir: Path | str, lane: str, day: str, read_id: str | None, matrix_by_sum: dict[str, AnswerMatrix],
                read_record: dict | None = None, hour_record: dict | None = None) -> dict[str, Row]:
    """One ungraded Row per sum for a read made today, built from the raw archive record exactly as the store would
    (spx_jev.store._read_rows), with labels ranked against the matrices' history. ``matrix_by_sum`` is {sum_id: matrix}
    built for today. The records are read from the lane's files, or taken from ``read_record`` / ``hour_record`` when the
    service passes them before they are written. Returns {sum_id: Row}; empty when the record cannot be found or has no sums."""
    from .. import store
    from .live_records import load_archive_read, load_hour_records
    rec = read_record if read_record is not None else load_archive_read(state_dir, lane, day, read_id)
    if rec is None:
        return {}
    row_ts = rec.get("row_ts")
    hour_rec = hour_record if hour_record is not None else load_hour_records(state_dir, lane, day).get(row_ts)
    hours = {(lane, row_ts): hour_rec} if hour_rec else {}
    parts = store._read_rows(date.fromisoformat(day), "live", rec, {}, hours)       # "live" is the store's label for a read record
    jev = {a["question_id"]: str(a["pick"]) for a in parts["answers"] if a.get("status") == "answered" and a.get("pick") is not None}
    labels = {f["name"]: str(f["text"]) for f in parts["facts"] if f.get("source") == "label" and f.get("status") == "written" and f.get("text") is not None}
    levels = {}
    for f in parts["facts"]:
        if f.get("source") == "market_context" and f.get("value") is not None:
            try:
                levels[f["path"]] = float(f["value"])
            except (TypeError, ValueError):
                pass
    out: dict[str, Row] = {}
    days_by_symbol_of: dict[str, dict[str, list[str]]] = {}
    for call in parts["calls"]:
        sum_id = call["sum_id"]
        matrix = matrix_by_sum.get(sum_id)
        if matrix is None:
            continue
        days_by_symbol = days_by_symbol_of.setdefault(sum_id, level_days(matrix.level_history))
        label_names = {c[len("fact:"):] for c in matrix.columns if c.startswith("fact:")}
        answers = row_answers(row_ts, jev, labels, label_names, levels, matrix.level_history, days_by_symbol)
        for col in matrix.columns:
            answers.setdefault(col, None)
        out[sum_id] = Row(read_id=rec["read_id"], row_ts=row_ts, day=day, sum_id=sum_id,
                          historical_odds_probs=_probs(call.get("clock_probs")), jev_own_probs=_probs(call.get("jev_probs")),
                          shown_probs=_probs(call.get("shown_probs")), pool_v1_probs=_probs(call.get("pool_probs")),
                          outcome=None, learn_exclude=bool(call.get("learn_exclude")), answers=answers,
                          shown_source=call.get("shown_source"))
    return out
