"""The nightly job, 17:15 ET: seal what happened, rebuild the library, score every forecaster, write the scorecard.

    python -m spx_claude_forecast.nightly --state-dir <state> [--day <day>]

Steps, each logged and each failing softly so the rest still run:
  seal       one outcome line per horizon for every built payload of the day that has none settled yet,
             graded with the station's own grader and the flat zones the read was built with, with the
             station's own forecasts for the read copied beside it; refused before the close plus
             SEAL_AFTER_CLOSE_MINUTES. A horizon already final or not_asked is never written again; one that
             was no_data is tried again and, if it now grades, gets the next finalize_attempt_number.
  catch_up   the same for the last CATCH_UP_DAYS days, so a missed night is never a hole
  library    library/rows.parquet rebuilt whole from the record
  scores     library/scores.parquet rebuilt whole
  scorecard  scorecard.json for the phone and for Will
  backup     the folder mirrored off-disk; the run log pruned
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import grading, jsonl_store, library, precedents, recorder, scorecard, scoring, station_stores
from .control import ET, log, log_job_run, now_et, now_utc_iso, prune_run_log, single_instance_lock, switched_off_reason
from .paths import ForecastPaths, default_state_dir, ensure_folders

SEAL_AFTER_CLOSE_MINUTES = 75       # the official close and the 16:20 ET daily-close save must both be on disk
CATCH_UP_DAYS = 10
SETTLED_STATUSES = ("final", "not_asked")   # an outcome line with one of these is the horizon's last word


def seal_day(state_dir: Path, paths: ForecastPaths, day: str, now: datetime | None = None) -> dict:
    """Write the outcome lines for every built payload of ``day`` whose horizons are not all settled; returns counts
    (``sealed`` reads touched, ``outcome_lines`` written, ``skipped`` payloads)."""
    now = now or now_et()
    sessions = station_stores.import_spx_jev("sessions")
    close = sessions.session_close(datetime(*map(int, day.split("-")), 12, 0, tzinfo=ET))
    if now < close + timedelta(minutes=SEAL_AFTER_CLOSE_MINUTES):
        return {"day": day, "sealed": 0, "why": f"refused before the close plus {SEAL_AFTER_CLOSE_MINUTES} min"}
    earlier_lines: dict[tuple[str, str], list[dict]] = {}
    for line in jsonl_store.iter_json_lines(paths.outcomes_file(day)):
        earlier_lines.setdefault((str(line.get("read_id")), str(line.get("horizon"))), []).append(line)
    prices = station_grades = None
    sealed = written = skipped = 0
    for payload in jsonl_store.iter_json_lines(paths.payloads_file(day)):
        read_id = str(payload.get("read_id"))
        logged = payload.get("logged_never_sent") or {}
        spot = logged.get("spot")
        open_horizons = [h for h in library.HORIZONS if not _is_settled(earlier_lines.get((read_id, h), []))]
        if payload.get("status") not in ("built", "refused") or not isinstance(spot, (int, float)) or not open_horizons:
            skipped += 1
            continue
        prices = prices or grading.load_session_prices(state_dir, day)
        if station_grades is None:
            station_grades = grading.load_existing_system_grades(state_dir, day)
        zones = logged.get("flat_zones_points") or "no flat zones logged on the payload"
        row_ts = logged.get("row_ts") or read_id.split(":", 1)[1]
        read_record = _archive_read(state_dir, day, read_id)
        touched = False
        for line in grading.grade_read(prices, read_id, row_ts, float(spot), zones, slot=payload.get("half_hour_slot_et"),
                                       claude_input_sha256=payload.get("claude_input_sha256"),
                                       prompt_and_model_version=payload.get("prompt_and_model_version"), now=now):
            horizon, result = line["horizon"], line["result"]
            earlier = earlier_lines.get((read_id, horizon), [])
            if horizon not in open_horizons or (earlier and result.get("status") not in SETTLED_STATUSES):
                continue                                              # settled already, or still no_data: nothing new to say
            line["finalize_attempt_number"] = len(earlier) + 1
            line["comparison_forecasts"], line["missing_comparison_forecasts"] = comparison_forecasts_for(payload, read_record, horizon)
            if result.get("direction"):
                check = grading.existing_system_result_check(station_grades, row_ts, horizon, result["direction"])
                if check:
                    line["existing_system_result_check"] = check
            jsonl_store.append_json_line(paths.outcomes_file(day), line)
            written += 1
            touched = True
        sealed += touched
    return {"day": day, "sealed": sealed, "outcome_lines": written, "skipped": skipped}


def _is_settled(outcome_lines: list[dict]) -> bool:
    return any((line.get("result") or {}).get("status") in SETTLED_STATUSES for line in outcome_lines)


def comparison_forecasts_for(payload: dict, read_record: dict | None, horizon: str) -> tuple[dict, dict]:
    """``(comparison_forecasts, missing_comparison_forecasts)`` for one horizon, in the spec's order: the base rate
    Claude was shown (from the payload), the station's time-of-day odds and main forecast (from its archived read
    record), and the shown cards' own outcomes blended with the base rate (from the frozen scene, never the library)."""
    found: dict = {}
    missing: dict = {}
    base = (payload.get("base_rate_shown_to_claude") or {}).get(horizon)
    if base:
        found["base_rate_shown_to_claude"] = base
    else:
        missing["base_rate_shown_to_claude"] = "none shown on the payload"
    station_found, station_missing = grading.comparison_forecasts_from_read_record(read_record, horizon)
    found.update(station_found)
    missing.update(station_missing)
    scene = payload.get("scene") or {}
    shown = precedents.shown_precedents_outcomes((scene.get("precedents") or {}).get("cards") or [],
                                                 (scene.get("base_rate") or {}).get(horizon), horizon)
    if shown:
        found["shown_precedents_outcomes"] = shown
    else:
        missing["shown_precedents_outcomes"] = "no cards with an outcome for this horizon were shown"
    return found, missing


def _archive_read(state_dir: Path, day: str, read_id: str) -> dict | None:
    try:
        live_records = station_stores.import_spx_jev("mirai_prediction.live_records")
        return live_records.load_archive_read(state_dir, "live", day, read_id)
    except Exception:
        return None


def run_nightly(state_dir: Path, day: str | None = None, now: datetime | None = None, backup_dir: Path | None = None) -> dict:
    now = now or now_et()
    day = day or now.date().isoformat()
    backup_dir = backup_dir or recorder.DEFAULT_BACKUP_DIR
    paths = ensure_folders(state_dir, backup_dir)
    results: dict = {}
    shared: dict = {}

    def score_step() -> dict:
        shared["reads_by_day"] = scoring.load_reads_by_day(paths)
        return {"rows": scoring.write_scores(paths, scoring.collect_score_rows(paths, shared["reads_by_day"]))}

    def scorecard_step() -> dict:
        card = scorecard.write_scorecard(paths, scoring.read_scores(paths), shared.get("reads_by_day"))
        return {k: card.get(k) for k in ("graded_reads", "verdict")}

    with single_instance_lock(paths, "nightly") as held:
        if not held:
            return {"skipped": "another nightly run holds the lock"}
        steps = [
            ("seal", lambda: seal_day(state_dir, paths, day, now)),
            ("catch_up", lambda: [seal_day(state_dir, paths, d, now) for d in _recent_days_with_payloads(paths, day)]),
            ("library", lambda: library.rebuild_library(paths)),
            ("scores", score_step),
            ("scorecard", scorecard_step),
            ("backup", lambda: {**recorder.backup_forecast_folder(paths, backup_dir), "run_log_pruned": prune_run_log(paths)}),
        ]
        for name, step in steps:
            started = now_utc_iso()
            try:
                results[name] = step()
                log_job_run(paths, f"nightly:{name}", started, True, day=day)
            except Exception as e:
                results[name] = {"error": f"{type(e).__name__}: {e}"}
                log_job_run(paths, f"nightly:{name}", started, False, error=results[name]["error"], day=day)
            log(f"{name}: {json.dumps(results[name], default=str)[:300]}")
    return results


def _recent_days_with_payloads(paths: ForecastPaths, day: str) -> list[str]:
    folder = paths.root / "payloads"
    if not folder.exists():
        return []
    days = sorted(p.name[:10] for p in folder.glob("20??-??-??.jsonl") if p.name[:10] < day)
    return days[-CATCH_UP_DAYS:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seal outcomes, rebuild the library, score, write the scorecard.")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--day", default=None)
    args = parser.parse_args(argv)
    off = switched_off_reason()
    if off:
        log(f"switched off ({off}); nothing run")
        return 0
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else default_state_dir()
    results = run_nightly(state_dir, args.day)
    print(json.dumps(results, indent=1, default=str))
    return 1 if any(isinstance(r, dict) and "error" in r for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
