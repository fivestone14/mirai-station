"""After the close, once a day: build the tables from raw/, score every voice, update pool_v2, refit for tomorrow, scoreboard,
prune the archive.

    python -m spx_jev.mirai_prediction.nightly_job --state-dir <state> --lane live [--day YYYY-MM-DD]

Runs after the live store's own rebuild (16:40 ET), so the day's grades are in graded_results. Each step is logged on its
own; a step that fails is recorded and the next one still runs, except that scoring and pool_v2 need the built table.
Safe to repeat: tables are rewritten whole, pool_v2 learns a day once, fits are rewritten.

Without --day the job catches up: every day with raw lines that pool_v2 has not learned yet (the last CATCH_UP_DAYS days),
then today, so a night the box slept through, or a store build that failed, is made up the next time it runs.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

from .name_map import SUM_WINDOW_MINUTES, SUMS_BY_LANE
from .paths import RAW, NEW_VOICE_FORECASTS, data_root, ensure_folders, log_job_run, now_utc_iso, prune_archive, prune_voice_fits, today_et
from .pool_v2 import add_missing_voices, load_pool_v2, log_update, new_pool_v2, save_pool_v2, update_pool_v2_after_day
from .scoreboard import write_scoreboard
from .table_builder import build_new_voice_forecasts_table, build_voice_scores_table, day_forecasts_by_read, load_day_outcomes
from .voice_fits import fit_voices_for_day, next_market_day

CATCH_UP_DAYS = 10


def update_pool_v2_for_day(root: Path, state_dir: Path | str, lane: str, day: str) -> dict:
    """Pool_v2 learns the day's graded reads, per sum (the reads the live loop excluded are left out)."""
    outcomes = load_day_outcomes(state_dir, lane, day)
    result = {}
    for sum_id in SUMS_BY_LANE.get(lane, ()):
        reads, read_times = [], []
        for read_id, forecasts in day_forecasts_by_read(root, day, sum_id).items():
            outcome = outcomes.get((read_id, sum_id))
            if outcome is not None:
                reads.append(({v: p for v, p in forecasts.items() if v not in ("pool_v2", "pool_v1")}, outcome))
                read_times.append(read_id.split(":", 1)[1] if ":" in read_id else None)      # read_id = "<lane>:<row_ts>"
        voice_names = sorted({v for forecasts, _ in reads for v in forecasts})
        state = load_pool_v2(root, sum_id) or (new_pool_v2(sum_id, voice_names) if voice_names else None)
        if state is None:
            result[sum_id] = {"applied": False, "why": "no forecasts for the day"}
            continue
        state = add_missing_voices(state, voice_names)
        state, line = update_pool_v2_after_day(state, day, reads, read_times, SUM_WINDOW_MINUTES.get(sum_id))
        save_pool_v2(root, state)
        log_update(root, state, line)
        result[sum_id] = {"applied": line.get("applied"), "reads": line.get("reads"), "counted_reads": line.get("counted_reads"),
                          "why": line.get("why"), **({"frozen": line["frozen"]} if line.get("frozen") else {})}
    return result


def refit_for_next_day(root: Path, state_dir: Path | str, lane: str, day: str) -> dict:
    """Fit the learners for the next market day on everything up to and including ``day``; old fit files are pruned."""
    fit_day = next_market_day(day)
    result = {"fit_day": fit_day}
    for sum_id in SUMS_BY_LANE.get(lane, ()):
        day_fits, path = fit_voices_for_day(state_dir, root, lane, sum_id, fit_day)
        result[sum_id] = {"path": str(path), "scorer_rows": day_fits.fits["additive_scorer"].rows_used,
                          "jev_corrected_asleep": day_fits.fits["jev_corrected"].asleep}
    result["pruned"] = prune_voice_fits(root)
    return result


def run_nightly(state_dir: Path | str, lane: str, day: str, fit_next: bool = True, scoreboard: bool = True) -> dict:
    """The whole night's work for one lane and day; returns each step's result."""
    root = ensure_folders(data_root(state_dir))
    results: dict[str, object] = {"day": day, "lane": lane}
    steps = [("build_new_voice_forecasts_table", lambda: build_new_voice_forecasts_table(root, state_dir, lane, day)),
             ("build_voice_scores_table", lambda: build_voice_scores_table(root, state_dir, lane, day)),
             ("update_pool_v2", lambda: update_pool_v2_for_day(root, state_dir, lane, day))]
    if fit_next:
        steps.append(("refit_for_next_day", lambda: refit_for_next_day(root, state_dir, lane, day)))
    if scoreboard:
        steps.append(("write_scoreboard", lambda: str(write_scoreboard(root, lane, SUMS_BY_LANE.get(lane, ())))))
        steps.append(("prune_archive", lambda: {"deleted": prune_archive(root)}))
    ok = True
    for name, step in steps:
        started = now_utc_iso()
        try:
            results[name] = step()
            log_job_run(root, f"nightly:{name}", started, True, day=day, lane=lane)
        except Exception as e:
            ok = False
            results[name] = {"error": f"{type(e).__name__}: {e}"}
            log_job_run(root, f"nightly:{name}", started, False, error=f"{type(e).__name__}: {e}", day=day, lane=lane,
                        trace=traceback.format_exc()[-1500:])
            if name == "build_new_voice_forecasts_table":
                break
    results["ok"] = ok
    return results


def days_to_catch_up(root: Path, lane: str, today: str) -> list[str]:
    """Days before today, within CATCH_UP_DAYS, that have raw lines but that some sum's pool_v2 has not learned."""
    earliest = (date.fromisoformat(today) - timedelta(days=CATCH_UP_DAYS)).isoformat()
    learned = None
    for sum_id in SUMS_BY_LANE.get(lane, ()):
        state = load_pool_v2(root, sum_id)
        days = set(state["days_learned"]) if state else set()
        learned = days if learned is None else learned & days
    raw_days = sorted(p.name[:10] for p in (root / RAW / NEW_VOICE_FORECASTS).glob("*.jsonl"))
    return [d for d in raw_days if earliest <= d < today and d not in (learned or set())]


def run_nightly_with_catch_up(state_dir: Path | str, lane: str, today: str) -> list[dict]:
    """The missed days first (without refitting), then today with the refit and the scoreboard."""
    root = ensure_folders(data_root(state_dir))
    results = [run_nightly(state_dir, lane, day, fit_next=False, scoreboard=False) for day in days_to_catch_up(root, lane, today)]
    results.append(run_nightly(state_dir, lane, today))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the Mirai Prediction System's nightly job")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--lane", default="live")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (ET); default: catch up the missed days, then today")
    ap.add_argument("--no-fit", action="store_true", help="skip refitting the learners for the next day")
    args = ap.parse_args(argv)
    if args.day:
        results = [run_nightly(args.state_dir, args.lane, args.day, fit_next=not args.no_fit)]
    else:
        results = run_nightly_with_catch_up(args.state_dir, args.lane, today_et())
    for result in results:
        print(result)
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
