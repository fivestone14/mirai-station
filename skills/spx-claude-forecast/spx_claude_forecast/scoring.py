"""Scores: every forecaster's chances for a read against what happened, one row per read x horizon x forecaster.

Forecasters scored on the same outcome line: ``claude`` (the final forecast), ``claude_answer_1`` and
``claude_answer_2``, ``base_rate_shown_to_claude``, ``existing_system_time_of_day_odds_20_sessions``,
``existing_system_main_forecast`` and ``shown_precedents_outcomes``. Scores use the station's own rule
(``spx_jev.scores``: each chance floored at 2 percent, log loss = -ln of the chance given to what happened)
so Claude's number sits beside the station's on the same scale. A Claude horizon that was rejected, paused
or missing scores AS the base rate (zero skill), and coverage is reported apart, so a hard read skipped
can never flatter the score.
"""
from __future__ import annotations

import io
import math

from . import jsonl_store, station_stores
from .grading import DIRECTIONS
from .library import HORIZONS
from .paths import ForecastPaths

CLAUDE = "claude"
FORECASTERS = (CLAUDE, "claude_answer_1", "claude_answer_2", "base_rate_shown_to_claude",
               "existing_system_time_of_day_odds_20_sessions", "existing_system_main_forecast", "shown_precedents_outcomes")
CHANCE_FLOOR = 0.02


def floored(chances_pct: dict) -> dict[str, float] | None:
    """up/flat/down as chances summing to 1, each at least CHANCE_FLOOR, from percentages; None if incomplete."""
    try:
        raw = {d: float(chances_pct[f"{d}_pct"]) / 100 for d in DIRECTIONS}
    except (KeyError, TypeError, ValueError):
        return None
    lifted = {d: max(v, CHANCE_FLOOR) for d, v in raw.items()}
    total = sum(lifted.values())
    return {d: v / total for d, v in lifted.items()}


def log_loss(chances: dict[str, float], happened: str) -> float:
    return round(-math.log(chances[happened]), 4)


def brier(chances: dict[str, float], happened: str) -> float:
    return round(sum((chances[d] - (1.0 if d == happened else 0.0)) ** 2 for d in DIRECTIONS), 4)


def score_rows_for_read(outcome_lines: list[dict], final_line: dict | None, answer_lines: list[dict]) -> list[dict]:
    """Score rows for one read from its sealed outcome lines, its final forecast line and its answer lines."""
    rows = []
    for outcome in outcome_lines:
        result = outcome.get("result") or {}
        if result.get("status") != "final" or result.get("direction") not in DIRECTIONS:
            continue
        horizon, happened = outcome["horizon"], result["direction"]
        comparisons = outcome.get("comparison_forecasts") or {}
        base_pct = comparisons.get("base_rate_shown_to_claude")
        candidates: dict[str, tuple[dict | None, str]] = {}
        for name in FORECASTERS[3:]:
            candidates[name] = (comparisons.get(name), "final" if comparisons.get(name) else "missing")
        claude_block = ((final_line or {}).get("forecast") or {}).get(horizon) or {}
        candidates[CLAUDE] = (claude_block if claude_block.get("status") == "ok" else None, claude_block.get("status") or "missing")
        for i, answer in enumerate(answer_lines[:2], start=1):
            block = ((answer.get("checked_forecast") or {}).get(horizon)) or {}
            candidates[f"claude_answer_{i}"] = (block if block.get("status") == "ok" else None, block.get("status") or "missing")
        for name, (chances_pct, status) in candidates.items():
            chances = floored(chances_pct) if chances_pct else None
            scored_as_base = False
            if chances is None and name.startswith("claude") and base_pct:
                chances, scored_as_base = floored(base_pct), True       # a missing Claude horizon scores as the base rate
            if chances is None:
                continue
            rows.append({"read_id": outcome["read_id"], "trading_day": outcome.get("trading_day"),
                         "half_hour_slot_et": outcome.get("half_hour_slot_et"), "read_source": outcome.get("read_source"),
                         "horizon": horizon, "forecaster": name, "forecast_status": status, "scored_as_base_rate": scored_as_base,
                         "happened": happened, "size_bucket": result.get("size_bucket"),
                         "up_pct": round(chances["up"] * 100, 1), "flat_pct": round(chances["flat"] * 100, 1),
                         "down_pct": round(chances["down"] * 100, 1), "log_loss": log_loss(chances, happened),
                         "brier_score": brier(chances, happened), "is_right": max(chances, key=chances.get) == happened,
                         "grading_rule_version": outcome.get("grading_rule_version")})
    return rows


def collect_score_rows(paths: ForecastPaths) -> list[dict]:
    """Every score row for every live read with sealed outcomes."""
    rows = []
    outcomes_dir = paths.root / "outcomes"
    if not outcomes_dir.exists():
        return rows
    for outcome_file in sorted(outcomes_dir.glob("20??-??-??.jsonl")):
        day = outcome_file.name[:10]
        by_read: dict[str, list[dict]] = {}
        for line in jsonl_store.iter_json_lines(outcome_file):
            by_read.setdefault(str(line.get("read_id")), []).append(line)
        finals: dict[str, dict] = {}
        answers: dict[str, list[dict]] = {}
        for line in jsonl_store.iter_json_lines(paths.reads_file(day)):
            if line.get("test_variant") not in (None, "none"):
                continue
            if line.get("line_type") == "final_forecast":
                finals[str(line.get("read_id"))] = line
            elif line.get("line_type") == "claude_answer":
                answers.setdefault(str(line.get("read_id")), []).append(line)
        for read_id, outcome_lines in by_read.items():
            newest = {}
            for line in outcome_lines:
                h = line.get("horizon")
                if h not in newest or int(line.get("finalize_attempt_number", 1)) >= int(newest[h].get("finalize_attempt_number", 1)):
                    newest[h] = line
            rows.extend(score_rows_for_read(list(newest.values()), finals.get(read_id), answers.get(read_id, [])))
    return rows


def write_scores(paths: ForecastPaths, rows: list[dict]) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows) if rows else pa.table({"read_id": pa.array([], pa.string())})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    jsonl_store.write_bytes_atomically(paths.library_scores_file, buffer.getvalue())
    return len(rows)


def read_scores(paths: ForecastPaths) -> list[dict]:
    if not paths.library_scores_file.exists():
        return []
    import pyarrow.parquet as pq

    return pq.read_table(paths.library_scores_file).to_pylist()


__all__ = ["FORECASTERS", "CLAUDE", "HORIZONS", "floored", "log_loss", "brier", "score_rows_for_read", "collect_score_rows",
           "write_scores", "read_scores", "station_stores"]
