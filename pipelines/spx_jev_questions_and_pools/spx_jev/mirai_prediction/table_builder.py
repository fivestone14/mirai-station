"""Builds this system's tables from its raw record files, with checks: raw/ is the record, tables/ can always be rebuilt.

    new_voice_forecasts   from raw/new_voice_forecasts/{day}.jsonl: one row per (read_id, sum_id, voice_name), the last line wins;
                          a row is refused (to tables/_checks/refused_rows/{day}.jsonl) when its probabilities do not sum to one,
                          its read is not among the day's reads in the live store, or it was created before its read;
                          learn_exclude (the live loop's own flag for the read) travels with every row
    voice_scores          every forecast of a graded read scored: penalty (spx_jev.scores log_loss with the 0.02 floor), whether the
                          called band was the outcome, and the reference's penalty on the same read; excluded reads are
                          scored too (they were shown on the phone) and flagged, so the scoreboard counts them and pool_v2 skips them
Each day's partition is written whole and atomically (day=YYYY-MM-DD/part-0.parquet), so a rebuild is safe to repeat.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..scores import OUTCOMES, floored, log_loss
from .name_map import REFERENCE_VOICE, store_path
from .paths import append_json_line, forecasts_table_dir, raw_forecasts_file, read_json_lines, refused_rows_file, voice_scores_table_dir

PROBS_TOLERANCE = 1e-3


def _parse_time(v) -> datetime | None:
    """An ISO time as an aware datetime (a naive one is taken as UTC), or None."""
    try:
        t = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _day_read_ids(state_dir: Path | str, lane: str, day: str) -> set[str] | None:
    """The live store's read ids for the day, or None when the store has not built that day yet."""
    import duckdb
    folder = store_path(state_dir, "jev_reads") / f"day={day}"
    if not folder.exists() or not any(folder.glob("*.parquet")):
        return None
    rel = duckdb.sql(f"SELECT read_id FROM read_parquet('{folder}/*.parquet') WHERE lane = '{lane}'")
    return {r[0] for r in rel.fetchall()}


def check_forecast_row(row: dict, known_reads: set[str] | None) -> str | None:
    """None when the row passes, else why it is refused."""
    probs = row.get("voice_probs")
    if not isinstance(probs, dict) or any(k not in probs for k in OUTCOMES):
        return "voice_probs is not an up/flat/down map"
    try:
        values = [float(probs[k]) for k in OUTCOMES]
    except (TypeError, ValueError):
        return "voice_probs holds a non-number"
    if any(v < 0 or v > 1 for v in values) or abs(sum(values) - 1.0) > PROBS_TOLERANCE:
        return f"voice_probs sum to {sum(values):.4f}, not 1"
    if not row.get("read_id") or not row.get("sum_id") or not row.get("voice_name"):
        return "missing read_id, sum_id or voice_name"
    if known_reads is not None and row["read_id"] not in known_reads:
        return "read_id is not among the day's reads in the store"
    created, row_ts = _parse_time(row.get("created_at")), _parse_time(row.get("row_ts"))
    if created and row_ts and row.get("source") == "live" and created < row_ts:
        return "created before its read"
    return None


def _write_partition(folder: Path, day: str, rows: list[dict], schema: pa.Schema) -> int:
    part = folder / f"day={day}"
    part.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema) if rows else schema.empty_table()
    tmp = part / f"part-0.{os.getpid()}.tmp.parquet"
    pq.write_table(table, tmp)
    os.replace(tmp, part / "part-0.parquet")                    # whole or not at all: a crash never leaves a half file
    return len(rows)


FORECAST_SCHEMA = pa.schema([("read_id", pa.string()), ("lane", pa.string()), ("row_ts", pa.string()), ("sum_id", pa.string()),
                             ("voice_name", pa.string()), ("up", pa.float64()), ("flat", pa.float64()), ("down", pa.float64()),
                             ("fit_day", pa.string()), ("created_at", pa.string()), ("source", pa.string()), ("learn_exclude", pa.bool_())])

SCORE_SCHEMA = pa.schema([("read_id", pa.string()), ("lane", pa.string()), ("row_ts", pa.string()), ("sum_id", pa.string()),
                          ("voice_name", pa.string()), ("outcome", pa.string()), ("called", pa.string()), ("right", pa.bool_()),
                          ("penalty", pa.float64()), ("reference_penalty", pa.float64()), ("source", pa.string()), ("learn_exclude", pa.bool_())])


def build_new_voice_forecasts_table(root: Path, state_dir: Path | str, lane: str, day: str) -> dict:
    """raw -> tables/new_voice_forecasts/day=DAY; returns counts."""
    raw = read_json_lines(raw_forecasts_file(root, day))
    known = _day_read_ids(state_dir, lane, day)
    kept: dict[tuple[str, str, str], dict] = {}
    refused = 0
    refused_file = refused_rows_file(root, day)
    if refused_file.exists():
        refused_file.unlink()
    for row in raw:
        if row.get("lane") not in (None, lane):
            continue
        why = check_forecast_row(row, known)
        if why:
            refused += 1
            append_json_line(refused_file, {"table": "new_voice_forecasts", "reason": why, "row": row})
            continue
        kept[(row["read_id"], row["sum_id"], row["voice_name"])] = row       # the last line for a key wins
    out = [{"read_id": r["read_id"], "lane": lane, "row_ts": str(r.get("row_ts")), "sum_id": r["sum_id"], "voice_name": r["voice_name"],
            "up": float(r["voice_probs"]["up"]), "flat": float(r["voice_probs"]["flat"]), "down": float(r["voice_probs"]["down"]),
            "fit_day": r.get("fit_day"), "created_at": str(r.get("created_at")), "source": r.get("source") or "live",
            "learn_exclude": bool(r.get("learn_exclude", False))}
           for r in kept.values()]
    n = _write_partition(forecasts_table_dir(root), day, out, FORECAST_SCHEMA)
    return {"raw_lines": len(raw), "rows": n, "refused": refused, "duplicates": len(raw) - n - refused,
            "store_day_known": known is not None}


def load_day_forecasts(root: Path, day: str) -> list[dict]:
    """The built forecasts of one day as dicts with a voice_probs map."""
    part = forecasts_table_dir(root) / f"day={day}" / "part-0.parquet"
    if not part.exists():
        return []
    rows = pq.read_table(part).to_pylist()
    for r in rows:
        r["voice_probs"] = {"up": r.pop("up"), "flat": r.pop("flat"), "down": r.pop("down")}
    return rows


def load_day_outcomes(state_dir: Path | str, lane: str, day: str) -> dict[tuple[str, str], str]:
    """{(read_id, sum_id): outcome} for the day's graded windows (graded_results, joined on read_id AND sum_id)."""
    import duckdb
    folder = store_path(state_dir, "graded_results") / f"day={day}"
    if not folder.exists() or not any(folder.glob("*.parquet")):
        return {}
    rel = duckdb.sql(f"SELECT read_id, sum_id, outcome FROM read_parquet('{folder}/*.parquet') WHERE lane = '{lane}' AND graded")
    return {(r[0], r[1]): r[2] for r in rel.fetchall() if r[2] in OUTCOMES}


def build_voice_scores_table(root: Path, state_dir: Path | str, lane: str, day: str) -> dict:
    """Score every forecast of a graded read for the day -> tables/voice_scores/day=DAY."""
    forecasts = load_day_forecasts(root, day)
    outcomes = load_day_outcomes(state_dir, lane, day)
    reference: dict[tuple[str, str], float] = {}
    for f in forecasts:
        if f["voice_name"] == REFERENCE_VOICE and (f["read_id"], f["sum_id"]) in outcomes:
            reference[(f["read_id"], f["sum_id"])] = log_loss(floored(f["voice_probs"]), outcomes[(f["read_id"], f["sum_id"])])
    out = []
    for f in forecasts:
        key = (f["read_id"], f["sum_id"])
        y = outcomes.get(key)
        if y is None:
            continue
        probs = floored(f["voice_probs"])
        called = max(OUTCOMES, key=lambda k: probs[k])
        out.append({"read_id": f["read_id"], "lane": lane, "row_ts": f["row_ts"], "sum_id": f["sum_id"], "voice_name": f["voice_name"],
                    "outcome": y, "called": called, "right": called == y, "penalty": log_loss(probs, y),
                    "reference_penalty": reference.get(key), "source": f.get("source"), "learn_exclude": bool(f.get("learn_exclude", False))})
    n = _write_partition(voice_scores_table_dir(root), day, out, SCORE_SCHEMA)
    return {"forecasts": len(forecasts), "graded_windows": len(outcomes), "scored": n}


def day_forecasts_by_read(root: Path, day: str, sum_id: str, skip_excluded: bool = True) -> dict[str, dict[str, dict[str, float]]]:
    """{read_id: {voice: probs}} for one sum from the day's built table; by default without the reads the live loop excluded."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for f in load_day_forecasts(root, day):
        if f["sum_id"] == sum_id and not (skip_excluded and f.get("learn_exclude")):
            out.setdefault(f["read_id"], {})[f["voice_name"]] = f["voice_probs"]
    return out
