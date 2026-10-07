"""The answer matrix: one row per past read, with every answer side by side and what price did next.

Built by the nightly job from the live store's Parquet tables (read through name_map) and saved with the day's fits, so
the live hook never scans the store; the hook only adds today's row to it:

    forecasts_at_read_time (calls)   one row per sum per read: the historical odds (clock_probs), JEV's own call (jev_probs),
                                     the call the phone showed (shown_probs), pool_v1's mix (pool_probs), learn_exclude
    graded_results (average_grades)  the result of that sum's window: up, flat or down, joined on (read_id, sum_id) - never on horizon
    jev_answers (answers)            layer 3: JEV's pick per question where status == "answered"; anything else is silent (None);
                                     the questions of one request group share one group, so a group casts one vote in the scorer
    raw/code_features/{day}.jsonl    layers 1-2: the code feature builder's answer per catalog question ("code:<question_id>"),
                                     this system's own raw record (code_features.py), joined on read_id; a read with no line
                                     has None in every code column. Each column's layer and group come from the catalog.

SHRINK FIRST, THEN JOIN: each long table is pivoted to one row per read before joining, so a read never multiplies.
The base is forecasts_at_read_time JOIN graded_results on (read_id, sum_id); the pivots are LEFT-joined onto it, so a
read with no answers is kept with blanks, never dropped.

WALK-FORWARD: build_answer_matrix(..., before_day) holds only reads from days strictly before ``before_day``, and the
code features of a read were computed from data known at its read time (prior sessions' history only), so nothing a
learner sees comes from its own day or later.

THE CUT-OVER (name_map.CUT_OVER_DAY, Phase 4): from that day the live lane asks only the judgment questions, so a ``jev:``
column of a pre-merge question stops being answered. A matrix built for a day after the cut-over (before_day > CUT_OVER_DAY)
drops every ``jev:`` column whose last answered day is before the cut-over (column_catalog), and its rows' answers with it:
the matcher reads a column neither read answered as half a mismatch, so dead columns would dilute every match against
the old reads. Every ``code:`` column is kept whatever its answers: the catalog is the same before and after. A matrix built
for the cut-over day itself, or before it, keeps every column, so the fits before the cut-over are what they were.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ..scores import OUTCOMES
from .code_features import column_name, load_catalog
from .name_map import CUT_OVER_DAY, store_path
from .paths import CODE_FEATURES, RAW, data_root, read_json_lines


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

    def trainable_rows(self) -> list[Row]:
        return [r for r in self.rows if r.is_trainable()]

    def max_day(self) -> str | None:
        return max((r.day for r in self.rows), default=None)

    def to_json(self) -> dict:
        return {"lane": self.lane, "sum_id": self.sum_id, "built_for_day": self.built_for_day, "rows": [asdict(r) for r in self.rows],
                "columns": self.columns}

    @classmethod
    def from_json(cls, d: dict) -> "AnswerMatrix":
        return cls(lane=d["lane"], sum_id=d["sum_id"], built_for_day=d["built_for_day"], rows=[Row(**r) for r in d["rows"]],
                   columns=d["columns"])


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


def load_jev_answers(state_dir: Path | str, lane: str, before_day: str) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, str]]:
    """(read_id -> {question_id: pick} for answered questions only, question_id -> its request group, question_id -> the last
    day it was answered) for layer 3."""
    answers = _parquet_glob(store_path(state_dir, "jev_answers"))
    if answers is None:
        return {}, {}, {}
    sql = f"""
        SELECT read_id, question_id, pick, group_id, CAST(day AS VARCHAR) AS day FROM read_parquet('{answers}', hive_partitioning=1)
        WHERE lane = '{lane}' AND status = 'answered' AND pick IS NOT NULL AND CAST(day AS VARCHAR) < '{before_day}'
    """
    picks: dict[str, dict[str, str]] = defaultdict(dict)
    group_of: dict[str, str] = {}
    last_day: dict[str, str] = {}
    for r in _query(sql):
        picks[r["read_id"]][r["question_id"]] = str(r["pick"])
        if r["group_id"]:
            group_of[r["question_id"]] = str(r["group_id"])
        day = str(r["day"])[:10]
        if day > last_day.get(r["question_id"], ""):
            last_day[r["question_id"]] = day
    return picks, group_of, last_day


def load_code_features(state_dir: Path | str, lane: str, before_day: str) -> dict[str, dict[str, str | None]]:
    """read_id -> {question_id: answer} from this system's raw/code_features files for days before ``before_day``
    (the last line for a read wins), for layers 1-2."""
    folder = data_root(state_dir) / RAW / CODE_FEATURES
    out: dict[str, dict[str, str | None]] = {}
    for path in sorted(folder.glob("????-??-??.jsonl")) if folder.exists() else []:
        if path.stem >= before_day:
            continue
        for line in read_json_lines(path):
            if line.get("read_id") and line.get("lane", lane) == lane and isinstance(line.get("answers"), dict):
                out[line["read_id"]] = line["answers"]
    return out


# ---------------------------------------------------------------- columns

def row_answers(code_answers: dict[str, str | None], jev_answers: dict[str, str]) -> dict[str, str | None]:
    """One read's answers across the layers, keyed by column id: every catalog question (None when unanswered), then JEV's picks."""
    out: dict[str, str | None] = {column_name(q["id"]): code_answers.get(q["id"]) for q in load_catalog()}
    for qid, pick in jev_answers.items():
        out[f"jev:{qid}"] = pick
    return out


def retired_before_cut_over(question_ids: set[str], last_day_of_question: dict[str, str], before_day: str) -> set[str]:
    """The questions whose ``jev:`` column a matrix built for ``before_day`` leaves out: once the matrix is for a day after the
    cut-over, every question last answered before the cut-over day (one never answered counts as never: kept)."""
    if before_day <= CUT_OVER_DAY:
        return set()
    return {qid for qid in question_ids if (last := last_day_of_question.get(qid)) is not None and last < CUT_OVER_DAY}


def column_catalog(question_ids: set[str], group_of_question: dict[str, str], last_day_of_question: dict[str, str] | None = None,
                   before_day: str | None = None) -> dict[str, dict]:
    """Every column with its layer, group and family: the code features' layer and group are the catalog's (layers 1-2), every
    one of them whatever the reads answered; a layer-3 question's group is its request group (the questions JEV was asked
    together), so the scorer takes one vote per group. With ``before_day`` and each question's last answered day, a matrix for a
    day after the cut-over leaves out the questions retired before it (retired_before_cut_over)."""
    cols: dict[str, dict] = {}
    for q in load_catalog():
        cols[column_name(q["id"])] = {"layer": int(q["layer"]), "group": q["group"], "family": q["method"]}
    dropped = retired_before_cut_over(question_ids, last_day_of_question or {}, before_day) if before_day else set()
    for qid in sorted(question_ids - dropped):
        cols[f"jev:{qid}"] = {"layer": 3, "group": f"jev:{group_of_question.get(qid, qid)}", "family": "jev"}
    return cols


# ---------------------------------------------------------------- building

def build_answer_matrix(state_dir: Path | str, lane: str, sum_id: str, before_day: str) -> AnswerMatrix:
    """The matrix for one lane and sum, holding reads from days strictly before ``before_day`` (YYYY-MM-DD)."""
    base = load_base_rows(state_dir, lane, sum_id, before_day)
    jev_answers, group_of_question, last_day_of_question = load_jev_answers(state_dir, lane, before_day)
    code_answers = load_code_features(state_dir, lane, before_day)
    question_ids = {q for per_read in jev_answers.values() for q in per_read}
    columns = column_catalog(question_ids, group_of_question, last_day_of_question, before_day)
    for row in base:
        answers = row_answers(code_answers.get(row.read_id, {}), jev_answers.get(row.read_id, {}))
        row.answers = {col: answers.get(col) for col in columns}      # a dropped column leaves the row too
    return AnswerMatrix(lane=lane, sum_id=sum_id, built_for_day=before_day, rows=base, columns=columns)


# ---------------------------------------------------------------- today's read (for the live hook)

def todays_rows(state_dir: Path | str, lane: str, day: str, read_record: dict, matrix_by_sum: dict[str, AnswerMatrix],
                code_answers: dict[str, str | None], hour_record: dict | None = None) -> dict[str, Row]:
    """One ungraded Row per sum for a read made today, built from the raw archive record exactly as the store would
    (spx_jev.store._read_rows), with the code feature builder's answers for the read and JEV's picks. ``matrix_by_sum`` is
    {sum_id: matrix} built for today. The hour record is read from the lane's file unless the service passes it before
    it is written. Returns {sum_id: Row}; empty when the record has no sums."""
    from .. import store
    from .live_records import load_hour_records
    row_ts = read_record.get("row_ts")
    hour_rec = hour_record if hour_record is not None else load_hour_records(state_dir, lane, day).get(row_ts)
    hours = {(lane, row_ts): hour_rec} if hour_rec else {}
    parts = store._read_rows(date.fromisoformat(day), "live", read_record, {}, hours)       # "live" is the store's label for a read record
    jev = {a["question_id"]: str(a["pick"]) for a in parts["answers"] if a.get("status") == "answered" and a.get("pick") is not None}
    out: dict[str, Row] = {}
    for call in parts["calls"]:
        sum_id = call["sum_id"]
        matrix = matrix_by_sum.get(sum_id)
        if matrix is None:
            continue
        answers = row_answers(code_answers, jev)
        for col in matrix.columns:
            answers.setdefault(col, None)
        out[sum_id] = Row(read_id=read_record["read_id"], row_ts=row_ts, day=day, sum_id=sum_id,
                          historical_odds_probs=_probs(call.get("clock_probs")), jev_own_probs=_probs(call.get("jev_probs")),
                          shown_probs=_probs(call.get("shown_probs")), pool_v1_probs=_probs(call.get("pool_probs")),
                          outcome=None, learn_exclude=bool(call.get("learn_exclude")), answers=answers,
                          shown_source=call.get("shown_source"))
    return out
