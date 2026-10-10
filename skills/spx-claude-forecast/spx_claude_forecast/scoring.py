"""Scores: every forecaster's chances for a read against what happened, one row per read x horizon x forecaster.

Forecasters scored on the same outcome line, named as spec/record_formats.md names them: ``claude_final``
(the graded forecast), ``claude_answer_1`` and ``claude_answer_2``, ``base_rate_shown_to_claude``,
``existing_system_time_of_day_odds_20_sessions``, ``existing_system_main_forecast`` and
``shown_precedents_outcomes``. The three-way scores come from the station's own scorer (``spx_jev.scores``:
each chance floored at 2 percent, log loss = -ln of the chance given to what happened, split into its
did-it-move and which-way parts) so Claude's number sits beside the station's on the same scale; the Brier
score and the ranked probability score over the seven size buckets are computed here. A Claude horizon
that was rejected, failed or never written scores AS the base rate (zero skill) and is flagged, so a hard
read skipped can never flatter the score; coverage is reported apart on the scorecard.
"""
from __future__ import annotations

from . import jsonl_store, library, station_stores
from .final_forecast import answer_number_of
from .grading import DIRECTIONS, SIZE_BUCKETS
from .paths import ForecastPaths
from .rulebook import TEST_VARIANT_NONE

SCORING_CODE_VERSION = "1"            # bump when a score's formula changes; every row carries it
CLAUDE_FINAL = "claude_final"
CLAUDE_ANSWERS = ("claude_answer_1", "claude_answer_2")
COMPARISON_FORECASTERS = ("base_rate_shown_to_claude", "existing_system_time_of_day_odds_20_sessions",
                          "existing_system_main_forecast", "shown_precedents_outcomes")
FORECASTERS = (CLAUDE_FINAL, *CLAUDE_ANSWERS, *COMPARISON_FORECASTERS)


def chances_of(block: dict | None) -> dict[str, float] | None:
    """up/flat/down as the station's floored chances (each at least 2 percent, summing to one) from a block's
    up_pct / flat_pct / down_pct; None when the block does not carry all three."""
    if not isinstance(block, dict):
        return None
    try:
        raw = {d: float(block[f"{d}_pct"]) / 100 for d in DIRECTIONS}
    except (KeyError, TypeError, ValueError):
        return None
    return station_stores.import_spx_jev("scores").floored(raw)


def brier_score(chances: dict[str, float], happened: str) -> float:
    return round(sum((chances[d] - (1.0 if d == happened else 0.0)) ** 2 for d in DIRECTIONS), 4)


def size_ranked_probability_score(size_buckets_pct: dict | None, happened_bucket: str | None) -> float | None:
    """The ranked probability score over the seven size buckets divided by six, so it runs from 0 (all the
    chance on the bucket that happened) to 1; None without a full split or a bucket."""
    if not isinstance(size_buckets_pct, dict) or happened_bucket not in SIZE_BUCKETS:
        return None
    try:
        shares = [float(size_buckets_pct[b]) for b in SIZE_BUCKETS]
    except (KeyError, TypeError, ValueError):
        return None
    total = sum(shares)
    if total <= 0:
        return None
    forecast_so_far = happened_so_far = score = 0.0
    for bucket, share in zip(SIZE_BUCKETS, shares):
        forecast_so_far += share / total
        happened_so_far += 1.0 if bucket == happened_bucket else 0.0
        score += (forecast_so_far - happened_so_far) ** 2
    return round(score / (len(SIZE_BUCKETS) - 1), 4)


def score_rows_for_read(outcome_lines: list[dict], final_line: dict | None, answer_lines: list[dict],
                        test_variant: str = TEST_VARIANT_NONE) -> list[dict]:
    """Score rows for one read from its sealed outcome lines, its final forecast line and its answer lines. With a
    ``test_variant`` the lines are that variant's (arms/), and only Claude's rows are scored, stamped with it: the
    comparison forecasters are production's rows already."""
    rows = []
    answers_by_number = {answer_number_of(a): a for a in answer_lines}
    production = test_variant == TEST_VARIANT_NONE
    for outcome in outcome_lines:
        result = outcome.get("result") or {}
        if result.get("status") != "final" or result.get("direction") not in DIRECTIONS:
            continue
        horizon, happened = outcome["horizon"], result["direction"]
        comparisons = outcome.get("comparison_forecasts") or {}
        base_block = comparisons.get("base_rate_shown_to_claude")
        base_chances = chances_of(base_block)
        claude_block = ((final_line or {}).get("forecast") or {}).get(horizon) or {}
        candidates = [(CLAUDE_FINAL, claude_block, claude_block.get("status") or "missing")]
        for name in (CLAUDE_ANSWERS if production else CLAUDE_ANSWERS[:1]):
            answer = answers_by_number.get(name.removeprefix("claude_")) or {}
            block = (answer.get("checked_forecast") or {}).get(horizon) or {}
            candidates.append((name, block, block.get("status") or "missing"))
        for name in (COMPARISON_FORECASTERS if production else ()):
            candidates.append((name, comparisons.get(name), "final" if comparisons.get(name) else "missing"))
        for name, block, status in candidates:
            chances = chances_of(block) if status in ("ok", "final") else None
            scored_as_base = False
            if chances is None and name.startswith("claude"):
                if base_chances is None:
                    continue
                chances, block, scored_as_base = base_chances, base_block, True
            if chances is None:
                continue
            rows.append(_score_row(outcome, name, status, scored_as_base, chances, block, happened, result.get("size_bucket"), test_variant))
    return rows


def _score_row(outcome: dict, forecaster: str, status: str, scored_as_base: bool, chances: dict[str, float], block: dict,
               happened: str, happened_bucket: str | None, test_variant: str) -> dict:
    scores = station_stores.import_spx_jev("scores")
    move_part, direction_part = scores.losses(chances, happened)
    top = max(chances, key=chances.get)
    return {"read_id": outcome["read_id"], "trading_day": outcome.get("trading_day"), "half_hour_slot_et": outcome.get("half_hour_slot_et"),
            "prompt_and_model_version": outcome.get("prompt_and_model_version"), "read_source": outcome.get("read_source"),
            "grading_rule_version": outcome.get("grading_rule_version"), "claude_input_sha256": outcome.get("claude_input_sha256"),
            "test_variant": test_variant, "horizon": outcome["horizon"], "forecaster": forecaster, "forecast_status": status,
            "is_scored_as_base_rate": scored_as_base, "result_direction": happened, "result_size_bucket": happened_bucket,
            "up_pct": block.get("up_pct"), "flat_pct": block.get("flat_pct"), "down_pct": block.get("down_pct"),
            "top_choice_pct": round(chances[top] * 100, 1), "log_loss": round(scores.log_loss(chances, happened), 4),
            "log_loss_move_part": round(move_part, 4), "log_loss_direction_part": round(direction_part, 4),
            "brier_score": brier_score(chances, happened),
            "size_ranked_probability_score": None if scored_as_base else size_ranked_probability_score(block.get("size_buckets_pct"), happened_bucket),
            "is_top_choice_correct": top == happened, "scoring_code_version": SCORING_CODE_VERSION}


def load_reads_by_day(paths: ForecastPaths) -> dict[str, list[dict]]:
    """Every reads line, by day: read once a night and shared by the scores and the scorecard."""
    return _lines_by_day(paths.root / "reads")


def load_arms_by_day(paths: ForecastPaths) -> dict[str, list[dict]]:
    """Every arms line (the nightly test variants' answers and forecasts), by day."""
    return _lines_by_day(paths.root / "arms")


def _lines_by_day(folder) -> dict[str, list[dict]]:
    if not folder.exists():
        return {}
    return {file.name[:10]: jsonl_store.read_json_lines(file) for file in sorted(folder.glob("20??-??-??.jsonl"))}


def test_variant_lines(arms_lines: list[dict]) -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str], list[dict]]]:
    """``(forecast line by (read_id, variant), answer lines by (read_id, variant))`` from one day's arms file."""
    forecasts: dict[tuple[str, str], dict] = {}
    answers: dict[tuple[str, str], list[dict]] = {}
    for line in arms_lines:
        key = (str(line.get("read_id")), str(line.get("test_variant")))
        if line.get("line_type") == "test_variant_forecast":
            forecasts[key] = line
        elif line.get("line_type") == "claude_answer":
            answers.setdefault(key, []).append(line)
    return forecasts, answers


def production_lines(reads_lines: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """``(final line by read_id, answer lines by read_id)`` for the production lines of one day's reads file."""
    finals: dict[str, dict] = {}
    answers: dict[str, list[dict]] = {}
    for line in reads_lines:
        if line.get("test_variant") not in (None, TEST_VARIANT_NONE):
            continue
        if line.get("line_type") == "final_forecast":
            finals[str(line.get("read_id"))] = line
        elif line.get("line_type") == "claude_answer":
            answers.setdefault(str(line.get("read_id")), []).append(line)
    return finals, answers


def collect_score_rows(paths: ForecastPaths, reads_by_day: dict[str, list[dict]] | None = None) -> list[dict]:
    """Every score row for every read with sealed outcomes, the newest finalize attempt per horizon."""
    rows = []
    outcomes_dir = paths.root / "outcomes"
    if not outcomes_dir.exists():
        return rows
    reads_by_day = reads_by_day if reads_by_day is not None else load_reads_by_day(paths)
    arms_by_day = load_arms_by_day(paths)
    for outcome_file in sorted(outcomes_dir.glob("20??-??-??.jsonl")):
        newest_by_read: dict[str, dict[str, dict]] = {}
        for line in jsonl_store.iter_json_lines(outcome_file):
            by_horizon = newest_by_read.setdefault(str(line.get("read_id")), {})
            horizon = str(line.get("horizon"))
            if horizon not in by_horizon or int(line.get("finalize_attempt_number", 1)) >= int(by_horizon[horizon].get("finalize_attempt_number", 1)):
                by_horizon[horizon] = line
        day = outcome_file.name[:10]
        finals, answers = production_lines(reads_by_day.get(day, []))
        for read_id, by_horizon in newest_by_read.items():
            rows.extend(score_rows_for_read(list(by_horizon.values()), finals.get(read_id), answers.get(read_id, [])))
        variant_forecasts, variant_answers = test_variant_lines(arms_by_day.get(day, []))
        for (read_id, variant), forecast in variant_forecasts.items():
            if read_id in newest_by_read:
                rows.extend(score_rows_for_read(list(newest_by_read[read_id].values()), forecast,
                                                variant_answers.get((read_id, variant), []), test_variant=variant))
    return rows


def write_scores(paths: ForecastPaths, rows: list[dict]) -> int:
    library.write_parquet_rows(paths.library_scores_file, rows)
    return len(rows)


def read_scores(paths: ForecastPaths) -> list[dict]:
    return library.read_parquet_rows(paths.library_scores_file)


__all__ = ["SCORING_CODE_VERSION", "FORECASTERS", "CLAUDE_FINAL", "CLAUDE_ANSWERS", "COMPARISON_FORECASTERS", "chances_of",
           "brier_score", "size_ranked_probability_score", "score_rows_for_read", "load_reads_by_day", "load_arms_by_day",
           "production_lines", "test_variant_lines", "collect_score_rows", "write_scores", "read_scores"]
