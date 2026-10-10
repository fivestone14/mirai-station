"""The seed: past half-hours rebuilt from stored files, so the first live read already has history to match.

    python -m spx_claude_forecast.seed --state-dir <state> [--from 2026-07-20] [--to <day>] [--force]

For every session from ``--from`` on and every half-hour slot a live read would have, the diary row nearest
the slot is the cut (within SEED_ROW_WINDOW_MINUTES, else the slot is ``blind``), the payload is built with
the same builder the live read uses (without the station's labeller, which the blocks do not need), and
the outcome is graded with the same grader. Rows land under ``seed/payloads`` and ``seed/outcomes`` with
``origin: seed`` and ``read_id`` ``seed:<day>T<HH:MM>``; they are precedents and base-rate counts only,
never scored as Claude. A slot already seeded is skipped unless ``--force``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import grading, jsonl_store, station_stores
from .control import ET, log, log_job_run, now_utc_iso, single_instance_lock
from .paths import ForecastPaths, default_state_dir, ensure_folders
from .payload.build import build_payload
from .payload.frozen_inputs import load_frozen_inputs_at

SEED_FROM_DEFAULT = "2026-07-20"       # siege's SPY minutes and the diary's full fields start here
SEED_SLOTS = tuple(f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 30) if (h, m) >= (9, 30) and (h, m) <= (15, 30))
SEED_ROW_WINDOW_MINUTES = 2            # the diary row must sit this close to the slot, as a live read's does


def session_days(state_dir: Path, start: str, end: str) -> list[str]:
    """Trading days with a saved full session of bars between ``start`` and ``end`` inclusive."""
    folder = state_dir / "reversion" / "bars"
    return sorted(p.name[:10] for p in folder.glob("20??-??-??-SPX.json") if start <= p.name[:10] <= end)


def seeded_slots(paths: ForecastPaths, day: str) -> set[str]:
    return {line.get("half_hour_slot_et") for line in jsonl_store.iter_json_lines(paths.seed_payloads_file(day))}


def seed_day(state_dir: Path, paths: ForecastPaths, day: str, *, force: bool = False) -> dict:
    """Seed every slot of one day; returns counts."""
    done = set() if force else seeded_slots(paths, day)
    counts = {"day": day, "built": 0, "blind": 0, "skipped": len(done), "errors": 0}
    prices = None
    for slot in SEED_SLOTS:
        if slot in done:
            continue
        read_id = f"seed:{day}T{slot}"
        y, m, d = (int(x) for x in day.split("-"))
        hh, mm = (int(x) for x in slot.split(":"))
        cut = datetime(y, m, d, hh, mm, tzinfo=ET) + timedelta(minutes=SEED_ROW_WINDOW_MINUTES)
        try:
            inputs = load_frozen_inputs_at(state_dir, paths, cut, read_id=read_id, with_labels=False)
            row_cut = inputs.cut
            slot_at = datetime(y, m, d, hh, mm, tzinfo=ET)
            if abs((row_cut - slot_at).total_seconds()) > SEED_ROW_WINDOW_MINUTES * 60:
                raise LookupError(f"nearest diary row is at {row_cut.strftime('%H:%M:%S')}, not within "
                                  f"{SEED_ROW_WINDOW_MINUTES} min of {slot}")
        except Exception as e:
            jsonl_store.append_json_line(paths.seed_payloads_file(day), {
                "line_type": "payload", "read_id": read_id, "trading_day": day, "half_hour_slot_et": slot, "origin": "seed",
                "status": "blind", "why": f"{type(e).__name__}: {e}", "cut_at": cut.isoformat(), "built_at": now_utc_iso()},
                fsync=False)
            counts["blind"] += 1
            continue
        try:
            built = build_payload(inputs, [], origin="seed")
            built.line["half_hour_slot_et"] = slot                      # the slot it stands for, not the row's rounding
            jsonl_store.append_json_line(paths.seed_payloads_file(day), built.line, fsync=False)
            prices = prices or grading.load_session_prices(state_dir, day)
            for line in grading.grade_read(prices, read_id, inputs.row_ts, inputs.spot, inputs.flat_zones,
                                           read_source="seed", slot=slot):
                line["claude_input_sha256"] = built.claude_input_sha256
                line["missing_comparison_forecasts"] = {"existing_system": "no station forecast before 09-28 for seed reads"}
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
