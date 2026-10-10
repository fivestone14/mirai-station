"""Will's view of the track record: ``scorecard.json``, built from the score rows every night. Never sent to Claude.

Per horizon: how many live reads were graded, Claude's skill against the base rate it was shown (one minus
its mean log loss over the base rate's, on the same reads), the same against the station's time-of-day
odds and its main forecast, a 90 percent range from resampling whole days, how often Claude's top pick was
right, and the watchdogs: how often it sat on the base rate, leaned with the last thirty minutes, and how
far its two answers disagreed. Under SCORECARD_TOO_EARLY_READS reads the verdict is "Too early".
"""
from __future__ import annotations

import json
import random
from collections import defaultdict

from . import jsonl_store, scoring
from .control import now_utc_iso
from .library import HORIZONS
from .paths import ForecastPaths
from .scoring import CLAUDE_FINAL

SCORECARD_TOO_EARLY_READS = 100
RESAMPLE_ROUNDS = 400
REFERENCES = ("base_rate_shown_to_claude", "existing_system_time_of_day_odds_20_sessions", "existing_system_main_forecast")


def skill_pct(claude_losses: list[float], reference_losses: list[float]) -> float | None:
    """One minus the mean Claude log loss over the mean reference log loss, as a percent; paired lists."""
    if not claude_losses or len(claude_losses) != len(reference_losses):
        return None
    reference = sum(reference_losses) / len(reference_losses)
    if reference <= 0:
        return None
    return round(100 * (1 - (sum(claude_losses) / len(claude_losses)) / reference), 2)


def skill_range_by_day(pairs_by_day: dict[str, list[tuple[float, float]]], rounds: int = RESAMPLE_ROUNDS,
                       seed: int = 7) -> tuple[float, float] | None:
    """A 90 percent range for the skill from resampling whole days (reads within a day are near-copies)."""
    days = list(pairs_by_day)
    if len(days) < 2:
        return None
    rng = random.Random(seed)
    samples = []
    for _ in range(rounds):
        picked = [pairs_by_day[rng.choice(days)] for _ in days]
        c = [p[0] for day in picked for p in day]
        r = [p[1] for day in picked for p in day]
        s = skill_pct(c, r)
        if s is not None:
            samples.append(s)
    if not samples:
        return None
    samples.sort()
    return (samples[int(0.05 * (len(samples) - 1))], samples[int(0.95 * (len(samples) - 1))])


def scorecard_from_rows(rows: list[dict], reads_lines_by_day: dict[str, list[dict]] | None = None) -> dict:
    live = [r for r in rows if r.get("read_source") in (None, "live")]
    card: dict = {"built_at": now_utc_iso(), "horizons": {}, "verdict": "Too early"}
    graded_reads = {r["read_id"] for r in live if r["forecaster"] == CLAUDE_FINAL}
    card["graded_reads"] = len(graded_reads)
    if len(graded_reads) >= SCORECARD_TOO_EARLY_READS:
        card["verdict"] = "Scored"
    for horizon in HORIZONS:
        by_read: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in live:
            if r["horizon"] == horizon:
                by_read[r["read_id"]][r["forecaster"]] = r
        block: dict = {"reads": sum(1 for v in by_read.values() if CLAUDE_FINAL in v),
                       "reads_scored_as_base_rate": sum(1 for v in by_read.values() if v.get(CLAUDE_FINAL, {}).get("is_scored_as_base_rate")),
                       "claude_right_pct": None, "skill_vs": {}}
        claude_rows = [v[CLAUDE_FINAL] for v in by_read.values() if CLAUDE_FINAL in v]
        if claude_rows:
            block["claude_right_pct"] = round(100 * sum(1 for r in claude_rows if r["is_top_choice_correct"]) / len(claude_rows), 1)
            block["mean_log_loss"] = round(sum(r["log_loss"] for r in claude_rows) / len(claude_rows), 4)
        for reference in REFERENCES:
            paired = [(v[CLAUDE_FINAL], v[reference]) for v in by_read.values() if CLAUDE_FINAL in v and reference in v]
            if not paired:
                continue
            by_day: dict[str, list[tuple[float, float]]] = defaultdict(list)
            for claude_row, reference_row in paired:
                by_day[claude_row["trading_day"]].append((claude_row["log_loss"], reference_row["log_loss"]))
            block["skill_vs"][reference] = {
                "skill_pct": skill_pct([c["log_loss"] for c, _ in paired], [r["log_loss"] for _, r in paired]),
                "range_90_pct": skill_range_by_day(by_day), "reads": len(paired),
                "reference_right_pct": round(100 * sum(1 for _, r in paired if r["is_top_choice_correct"]) / len(paired), 1)}
        card["horizons"][horizon] = block
    card["watchdogs"] = watchdogs(reads_lines_by_day or {})
    card["daily_skill_vs_base_rate"] = daily_skill(live)
    return card


def daily_skill(rows: list[dict]) -> list[dict]:
    by_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["horizon"] == "next_30_minutes" and r["forecaster"] in (CLAUDE_FINAL, "base_rate_shown_to_claude"):
            by_day[r["trading_day"]][r["forecaster"]].append(r["log_loss"])
    out = []
    for day in sorted(by_day):
        c, b = by_day[day][CLAUDE_FINAL], by_day[day]["base_rate_shown_to_claude"]
        if c and b and len(c) == len(b):
            out.append({"day": day, "reads": len(c), "skill_pct": skill_pct(c, b)})
    return out


def watchdogs(reads_lines_by_day: dict[str, list[dict]]) -> dict:
    finals = [l for lines in reads_lines_by_day.values() for l in lines
              if l.get("line_type") == "final_forecast" and l.get("status") == "ok"]
    answers = [l for lines in reads_lines_by_day.values() for l in lines if l.get("line_type") == "claude_answer"]
    sat = leans = gaps = counted = 0
    gap_total = 0.0
    for final in finals:
        block = (final.get("forecast") or {}).get("next_30_minutes") or {}
        if block.get("status") != "ok":
            continue
        counted += 1
        sat += bool(block.get("is_within_3_pct_points_of_base_rate"))
        leans += bool(block.get("is_leaning_same_way_as_last_30_minutes_move_vs_base_rate"))
        if isinstance(block.get("largest_gap_between_answers_pct_points"), (int, float)):
            gaps += 1
            gap_total += block["largest_gap_between_answers_pct_points"]
    cost = sum((a.get("call_stats") or {}).get("cost_usd", 0) for a in answers)
    errors = sum(1 for a in answers if a.get("error"))
    return {"final_forecasts": counted,
            "sat_on_base_rate_pct": round(100 * sat / counted, 1) if counted else None,
            "leaned_with_last_30_minutes_pct": round(100 * leans / counted, 1) if counted else None,
            "mean_gap_between_answers_pct_points": round(gap_total / gaps, 1) if gaps else None,
            "answers": len(answers), "answer_errors": errors,
            "cost_usd_total": round(cost, 2), "ungrounded_pct": round(100 * sum(1 for f in finals if not f.get("any_answer_has_valid_reason")) / counted, 1) if counted else None}


def write_scorecard(paths: ForecastPaths, rows: list[dict], reads_by_day: dict[str, list[dict]] | None = None) -> dict:
    card = scorecard_from_rows(rows, reads_by_day if reads_by_day is not None else scoring.load_reads_by_day(paths))
    jsonl_store.write_json_atomically(paths.scorecard_file, card)
    return card


def read_scorecard(paths: ForecastPaths) -> dict | None:
    """The scorecard the nightly last wrote, or None before the first nightly or on an unreadable file."""
    try:
        card = json.loads(paths.scorecard_file.read_text())
    except (OSError, ValueError):
        return None
    return card if isinstance(card, dict) else None
