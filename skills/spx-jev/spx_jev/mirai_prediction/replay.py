"""Replay the stored days in order, exactly as the live loop would have run them: proof the chain works end to end, and
a weight history for pool_v2 before go-live.

    python -m spx_jev.mirai_prediction.replay --state-dir <state> --lane live --from 2026-09-29 --to 2026-10-05 [--force]

For each day d: fit the learners for d (reads from days before d only), forecast every stored read of d with every voice
(lines marked source="replay"), then run the night's steps for d (table, scores, pool_v2). A day already present in raw/
is skipped unless --force, which first backs up and clears this system's raw files, tables and pool_v2 state for the range.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .live_records import load_pool_v1_snapshots
from .name_map import SUMS_BY_LANE, store_path
from .nightly_job import run_nightly
from .paths import ARCHIVE, POOLS, POOL_V2, data_root, ensure_folders, forecasts_table_dir, log_job_run, now_utc_iso, raw_forecasts_file, voice_scores_table_dir
from .read_hook import forecast_read
from .scoreboard import write_scoreboard
from .voice_fits import fit_voices_for_day


def stored_days(state_dir: Path | str, lane: str, from_day: str, to_day: str) -> list[str]:
    """The days in the range the live store has reads for, in order."""
    import duckdb
    folder = store_path(state_dir, "forecasts_at_read_time")
    if not folder.exists():
        return []
    rel = duckdb.sql(f"SELECT DISTINCT CAST(day AS VARCHAR) FROM read_parquet('{folder}/*/*.parquet', hive_partitioning=1) "
                     f"WHERE lane = '{lane}' AND CAST(day AS VARCHAR) BETWEEN '{from_day}' AND '{to_day}' ORDER BY 1")
    return [r[0] for r in rel.fetchall()]


def read_ids_of_day(state_dir: Path | str, lane: str, day: str) -> list[str]:
    import duckdb
    folder = store_path(state_dir, "forecasts_at_read_time") / f"day={day}"
    if not folder.exists():
        return []
    rel = duckdb.sql(f"SELECT DISTINCT read_id, CAST(row_ts AS VARCHAR) AS t FROM read_parquet('{folder}/*.parquet') WHERE lane = '{lane}' ORDER BY t")
    return [r[0] for r in rel.fetchall()]


def clear_range(root: Path, days: list[str]) -> None:
    """Back up then remove this system's raw files, table partitions and pool_v2 state for the days (--force)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / ARCHIVE / f"replay_force_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for day in days:
        for path in (raw_forecasts_file(root, day), forecasts_table_dir(root) / f"day={day}", voice_scores_table_dir(root) / f"day={day}"):
            if path.exists():
                shutil.move(str(path), str(backup / f"{path.parent.name}__{path.name}"))
    pool_dir = root / POOLS / POOL_V2
    if pool_dir.exists():
        shutil.move(str(pool_dir), str(backup / "pool_v2"))
        pool_dir.mkdir(parents=True, exist_ok=True)


def run_replay(state_dir: Path | str, lane: str, from_day: str, to_day: str, force: bool = False) -> dict:
    root = ensure_folders(data_root(state_dir))
    days = stored_days(state_dir, lane, from_day, to_day)
    if force:
        clear_range(root, days)
    started_at = now_utc_iso()
    report: dict[str, object] = {"lane": lane, "days": {}}
    sums = SUMS_BY_LANE.get(lane, ())
    for day in days:
        if raw_forecasts_file(root, day).exists() and not force:
            report["days"][day] = {"skipped": "already replayed; use --force to redo"}
            continue
        day_fits_by_sum = {sum_id: fit_voices_for_day(state_dir, root, lane, sum_id, day)[0] for sum_id in sums}
        snapshots = load_pool_v1_snapshots(state_dir, lane, day)
        written = 0
        for read_id in read_ids_of_day(state_dir, lane, day):
            result = forecast_read(state_dir, lane, day, read_id, source="replay", day_fits_by_sum=day_fits_by_sum,
                                   pool_v1_snapshots=snapshots, budget_seconds=None)
            written += result.get("written", 0)
        night = run_nightly(state_dir, lane, day, fit_next=False, scoreboard=False)
        report["days"][day] = {"forecast_lines": written, "night": {k: v for k, v in night.items() if k not in ("day", "lane")}}
    write_scoreboard(root, lane, sums)
    log_job_run(root, "replay", started_at, True, lane=lane, from_day=from_day, to_day=to_day, days=len(days), force=force)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="replay stored days through the Mirai Prediction System")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--lane", default="live")
    ap.add_argument("--from", dest="from_day", required=True)
    ap.add_argument("--to", dest="to_day", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    report = run_replay(args.state_dir, args.lane, args.from_day, args.to_day, force=args.force)
    for day, r in report["days"].items():
        print(day, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
