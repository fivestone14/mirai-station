"""Write the code feature builder's answers for every archived read of a lane over a range of days (source "backfill").

    python -m spx_jev.mirai_prediction.backfill_code_features --state-dir <state> --from 2026-09-28 --to 2026-10-06 [--lane live] [--force]

Idempotent: a read with a line in raw/code_features/{day}.jsonl already is skipped. --force backs the day's file up into
archive/ and rewrites it whole (after the builder changes). Prints, per day, the reads seen, written and skipped, and
over the range, per question, how many reads it answered and how many it left None.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .code_feature_inputs import load_day_bars, load_market_history, record_code_features, stored_code_features
from .code_features import load_catalog
from .live_records import _json_lines
from .paths import backup_before_change, data_root, ensure_folders, log_job_run, now_utc_iso, raw_code_features_file


def archived_reads(state_dir: Path | str, lane: str, day: str) -> list[dict]:
    """The lane's read records of the day from the shared archive, in time order, each with its read_id."""
    from ..archive import read_id as make_read_id
    from ..lane import LANES
    path = Path(LANES[lane].archive_folder(state_dir)) / f"{day}.jsonl"
    reads = []
    for rec in _json_lines(path):
        if rec.get("kind") not in (None, "read") or rec.get("lane") != lane or not rec.get("row_ts"):
            continue
        reads.append({**rec, "read_id": rec.get("read_id") or make_read_id(lane, rec["row_ts"])})
    return sorted(reads, key=lambda r: r["row_ts"])


def archive_days(state_dir: Path | str, lane: str, from_day: str, to_day: str) -> list[str]:
    from ..lane import LANES
    folder = Path(LANES[lane].archive_folder(state_dir))
    return sorted(p.stem for p in folder.glob("????-??-??.jsonl") if from_day <= p.stem <= to_day) if folder.exists() else []


def run_backfill(state_dir: Path | str, lane: str, from_day: str, to_day: str, force: bool = False) -> dict:
    root = ensure_folders(data_root(state_dir))
    started_at = now_utc_iso()
    per_day: dict[str, dict] = {}
    answered: Counter = Counter()
    silent: Counter = Counter()
    for day in archive_days(state_dir, lane, from_day, to_day):
        reads = archived_reads(state_dir, lane, day)
        if not reads:
            continue
        if force:
            path = raw_code_features_file(root, day)
            backup_before_change(root, path)
            path.unlink(missing_ok=True)
        stored = stored_code_features(root, day)
        history = load_market_history(state_dir, lane, day)
        bars = load_day_bars(state_dir, day)
        written = skipped = 0
        for rec in reads:
            if rec["read_id"] in stored:
                skipped += 1
                answers = stored[rec["read_id"]].get("answers") or {}
            else:
                answers = record_code_features(root, state_dir, lane, day, rec, "backfill", history, bars, stored)
                written += 1
            for qid, answer in answers.items():
                (answered if answer is not None else silent)[qid] += 1
        per_day[day] = {"reads": len(reads), "written": written, "skipped": skipped}
    per_question = {q["id"]: {"answered": answered[q["id"]], "none": silent[q["id"]]} for q in load_catalog()}
    log_job_run(root, "backfill_code_features", started_at, True, lane=lane, from_day=from_day, to_day=to_day,
                days=len(per_day), written=sum(d["written"] for d in per_day.values()), force=force)
    return {"lane": lane, "days": per_day, "questions": per_question}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="backfill the code feature builder's answers for archived reads")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--lane", default="live")
    ap.add_argument("--from", dest="from_day", required=True)
    ap.add_argument("--to", dest="to_day", required=True)
    ap.add_argument("--force", action="store_true", help="rewrite the days' files (backed up first)")
    args = ap.parse_args(argv)
    report = run_backfill(args.state_dir, args.lane, args.from_day, args.to_day, force=args.force)
    for day, counts in report["days"].items():
        print(day, counts)
    for qid, counts in report["questions"].items():
        print(f"{qid:14s} answered {counts['answered']:4d}  none {counts['none']:4d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
