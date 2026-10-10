"""The seed: past half-hours rebuilt from stored files, so the first live read already has history to match.

    python -m spx_claude_forecast.seed --state-dir <state> [--from 2026-07-20] [--to <day>] [--force]

For every session from ``--from`` on and every half-hour slot a live read would have, the cut is the diary
row a live read would have taken: the newest row at or before the slot plus SEED_ROW_WINDOW_MINUTES (a live
read fires just after the half hour and takes the newest row then), provided it is no further than that
window before the slot; otherwise the slot is ``blind``. The payload is built at that row's own timestamp
with the same builder the live read uses (without the station's labeller, which the blocks do not need),
and the outcome is graded with the same grader. Rows land under ``seed/payloads`` and ``seed/outcomes``
with ``origin: seed`` and ``read_id`` ``seed:<day>T<HH:MM>``; they are precedents and base-rate counts
only, never scored as Claude. A slot already seeded is skipped unless ``--force``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import grading, jsonl_store, station_stores
from .control import ET, log, log_job_run, now_utc_iso, single_instance_lock
from .paths import ForecastPaths, default_state_dir, ensure_folders
from .payload.build import build_payload
from .payload.frozen_inputs import load_frozen_inputs_at
from .read_runner import blind_payload_line

SEED_FROM_DEFAULT = "2026-07-20"       # siege's SPY minutes and the diary's full fields start here
SEED_SLOTS = tuple(f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 30) if (h, m) >= (9, 30) and (h, m) <= (15, 30))
SEED_ROW_WINDOW_MINUTES = 2            # the diary row must sit this close to the slot, as a live read's does
SEED_MISSING_COMPARISONS = {           # a seed read has no Claude call and no station record to compare against
    "base_rate_shown_to_claude": "a seed read is built without a library",
    "existing_system_time_of_day_odds_20_sessions": "a seed read has no station read record",
    "existing_system_main_forecast": "a seed read has no station read record",
    "shown_precedents_outcomes": "a seed read shows no cards",
}


def session_days(state_dir: Path, start: str, end: str) -> list[str]:
    """Trading days with a saved full session of bars between ``start`` and ``end`` inclusive."""
    folder = state_dir / "reversion" / "bars"
    return sorted(p.name[:10] for p in folder.glob("20??-??-??-SPX.json") if start <= p.name[:10] <= end)


def seeded_slots(paths: ForecastPaths, day: str) -> set[str]:
    return {line.get("half_hour_slot_et") for line in jsonl_store.iter_json_lines(paths.seed_payloads_file(day))}


def diary_row_times(state_dir: Path, day: str) -> list[datetime]:
    """Every diary row's timestamp for the day, oldest first, in Eastern time."""
    state_builder = station_stores.import_spx_jev("state_builder")
    times = []
    for line in jsonl_store.iter_json_lines(station_stores.reversion_rows_file(state_dir, day)):
        if isinstance(line.get("ts"), str):
            try:
                times.append(state_builder.parse_ts(line["ts"]).astimezone(ET))
            except ValueError:
                continue
    return sorted(times)


def row_time_for_slot(row_times: list[datetime], slot_at: datetime) -> datetime | None:
    """The diary row a live read at this slot would have taken: the newest row at or before the slot plus
    SEED_ROW_WINDOW_MINUTES, as long as it is no further than that window before the slot; else None."""
    window = timedelta(minutes=SEED_ROW_WINDOW_MINUTES)
    candidates = [t for t in row_times if t <= slot_at + window]
    if not candidates or candidates[-1] < slot_at - window:
        return None
    return candidates[-1]


def seed_day(state_dir: Path, paths: ForecastPaths, day: str, *, force: bool = False) -> dict:
    """Seed every slot of one day; returns counts."""
    done = set() if force else seeded_slots(paths, day)
    counts = {"day": day, "built": 0, "blind": 0, "skipped": len(done), "errors": 0}
    row_times = diary_row_times(state_dir, day)
    prices = None
    for slot in SEED_SLOTS:
        if slot in done:
            continue
        read_id = f"seed:{day}T{slot}"
        y, m, d = (int(x) for x in day.split("-"))
        hh, mm = (int(x) for x in slot.split(":"))
        slot_at = datetime(y, m, d, hh, mm, tzinfo=ET)
        try:
            cut = row_time_for_slot(row_times, slot_at)
            if cut is None:
                raise LookupError(f"no diary row within {SEED_ROW_WINDOW_MINUTES} min of {slot}")
            inputs = load_frozen_inputs_at(state_dir, paths, cut, read_id=read_id, with_labels=False)
            if inputs.slot != slot:
                raise LookupError(f"the row at {cut.strftime('%H:%M:%S')} rounds to slot {inputs.slot}, not {slot}")
        except Exception as e:
            jsonl_store.append_json_line(paths.seed_payloads_file(day),
                                         blind_payload_line(read_id, day, slot, "seed", slot_at, f"{type(e).__name__}: {e}"), fsync=False)
            counts["blind"] += 1
            continue
        try:
            built = build_payload(inputs, [], origin="seed")
            jsonl_store.append_json_line(paths.seed_payloads_file(day), built.line, fsync=False)
            prices = prices or grading.load_session_prices(state_dir, day)
            for line in grading.grade_read(prices, read_id, inputs.row_ts, inputs.spot, inputs.flat_zones, read_source="seed",
                                           slot=slot, claude_input_sha256=built.claude_input_sha256,
                                           prompt_and_model_version=built.line.get("prompt_and_model_version")):
                line["comparison_forecasts"] = {}
                line["missing_comparison_forecasts"] = dict(SEED_MISSING_COMPARISONS)
                jsonl_store.append_json_line(paths.seed_outcomes_file(day), line, fsync=False)
            counts["built"] += 1
        except Exception as e:
            counts["errors"] += 1
            log(f"seed {read_id}: {type(e).__name__}: {e}")
    return counts


def run_seed(state_dir: Path, start: str, end: str, *, force: bool = False) -> dict:
    paths = ensure_folders(state_dir)
    with single_instance_lock(paths, "seed") as held:
        if not held:
            return {"skipped": "another seed run holds the lock"}
        started = now_utc_iso()
        totals = {"days": 0, "built": 0, "blind": 0, "skipped": 0, "errors": 0}
        for day in session_days(state_dir, start, end):
            counts = seed_day(state_dir, paths, day, force=force)
            log(json.dumps(counts))
            totals["days"] += 1
            for k in ("built", "blind", "skipped", "errors"):
                totals[k] += counts[k]
        log_job_run(paths, "seed", started, totals["errors"] == 0, **totals)
        return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild past half-hours as seed history for the forecast.")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--from", dest="start", default=SEED_FROM_DEFAULT)
    parser.add_argument("--to", dest="end", default=(datetime.now(ET).date() - timedelta(days=1)).isoformat())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else default_state_dir()
    print(json.dumps(run_seed(state_dir, args.start, args.end, force=args.force), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
