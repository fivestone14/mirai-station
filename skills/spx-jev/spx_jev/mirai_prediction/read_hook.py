"""After every live read: every voice forecasts the read, pool_v2 mixes them, and the lines are written to raw/.

Runs in its own process (service_hook.spawn_after_read), never on the read's path. For each sum the lane learns on
(SUMS_BY_LANE) it loads the day's fits with the answer matrix they were fitted on (or the newest earlier day's), builds
today's row from the raw records, asks every voice, mixes pool_v2 over the voices present, and appends one line per voice
plus one for "pool_v2" to raw/new_voice_forecasts/{day}.jsonl. The first sum always runs; a later sum is skipped once the
time budget is spent. Everything is inside a try/except: a failure is logged, never raised.

    python -m spx_jev.mirai_prediction.read_hook --state-dir <state> --lane live [--read-id <id>] [--day YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from .answer_matrix import build_answer_matrix, todays_rows
from .live_records import load_pool_v1_snapshots
from .name_map import SUMS_BY_LANE
from .paths import (append_json_line, data_root, ensure_folders, log_job_run, now_utc_iso, raw_forecasts_file, read_json_lines, today_et)
from .pool_v2 import add_missing_voices, load_pool_v2, mix_pool_v2, new_pool_v2, save_pool_v2
from .voice_fits import DayFits, load_voice_fits
from .voices import all_voice_forecasts

TIME_BUDGET_SECONDS = 5.0


def already_written(root: Path, day: str, read_id: str, sum_id: str) -> bool:
    return any(r.get("read_id") == read_id and r.get("sum_id") == sum_id for r in read_json_lines(raw_forecasts_file(root, day)))


def day_fits_for(state_dir: Path | str, root: Path, lane: str, sum_id: str, day: str) -> DayFits:
    """The fits and matrix to forecast ``day`` with: the saved ones, else an unfitted set over a matrix built now
    (the very first day, before any nightly job has run)."""
    saved = load_voice_fits(root, sum_id, day)
    if saved is not None:
        return saved
    return DayFits(fits={}, matrix=build_answer_matrix(state_dir, lane, sum_id, before_day=day), fit_day=None)


def forecast_read(state_dir: Path | str, lane: str, day: str, read_id: str | None, source: str = "live",
                  day_fits_by_sum: dict[str, DayFits] | None = None, pool_v1_snapshots: dict | None = None,
                  budget_seconds: float | None = TIME_BUDGET_SECONDS) -> dict:
    """Forecast one read with every voice and write the lines. Returns counts. The optional caches let the replay reuse
    the day's fits and pool_v1 snapshots across a day's reads."""
    started = time.monotonic()
    root = ensure_folders(data_root(state_dir))
    sums = SUMS_BY_LANE.get(lane, ())
    day_fits_by_sum = dict(day_fits_by_sum or {})
    for sum_id in sums:
        day_fits_by_sum.setdefault(sum_id, day_fits_for(state_dir, root, lane, sum_id, day))
    rows = todays_rows(state_dir, lane, day, read_id, {s: f.matrix for s, f in day_fits_by_sum.items()})
    if not rows:
        return {"read_id": read_id, "written": 0, "why": "no read record with sums found"}
    snapshots = pool_v1_snapshots if pool_v1_snapshots is not None else load_pool_v1_snapshots(state_dir, lane, day)
    written, skipped = 0, []
    for sum_id, row in rows.items():
        if written and budget_seconds is not None and time.monotonic() - started > budget_seconds:
            skipped.append(f"{sum_id}: out of time")
            break
        if already_written(root, day, row.read_id, sum_id):
            skipped.append(f"{sum_id}: already written")
            continue
        day_fits = day_fits_by_sum[sum_id]
        forecasts, notes = all_voice_forecasts(day_fits.fits, row, snapshots.get(row.row_ts), day_fits.matrix.trainable_rows())
        state = load_pool_v2(root, sum_id)
        if state is None:
            state = new_pool_v2(sum_id, list(forecasts))
            save_pool_v2(root, state)                              # the pool is born on the first read; the nightly job updates it
        state = add_missing_voices(state, list(forecasts))         # a voice new today joins in memory; the nightly job saves it
        mixed = mix_pool_v2(state, forecasts)
        line = {"read_id": row.read_id, "lane": lane, "day": day, "row_ts": row.row_ts, "sum_id": sum_id, "fit_day": day_fits.fit_day,
                "created_at": now_utc_iso(), "source": source, "learn_exclude": row.learn_exclude}
        for voice, probs in forecasts.items():
            append_json_line(raw_forecasts_file(root, day), {**line, "voice_name": voice, "voice_probs": probs,
                                                           **({"notes": notes[voice]} if voice in notes else {})})
            written += 1
        if mixed is not None:
            append_json_line(raw_forecasts_file(root, day), {**line, "voice_name": "pool_v2", "voice_probs": mixed,
                                                           "voices_mixed": sorted(forecasts)})
            written += 1
    return {"read_id": next(iter(rows.values())).read_id, "written": written, "skipped": skipped,
            "seconds": round(time.monotonic() - started, 3)}


def after_read(state_dir: Path | str, lane: str, read_id: str | None = None, day: str | None = None) -> dict:
    """The hook's whole job, never raising: forecast the read, write the lines, log the run."""
    started_at = now_utc_iso()
    root = ensure_folders(data_root(state_dir))
    day = day or (read_id.split(":", 1)[1][:10] if read_id and ":" in read_id else today_et())
    try:
        result = forecast_read(state_dir, lane, day, read_id)
        log_job_run(root, "read_hook", started_at, True, lane=lane, **{k: v for k, v in result.items() if k != "skipped"},
                    skipped=len(result.get("skipped", [])))
        return result
    except Exception as e:
        log_job_run(root, "read_hook", started_at, False, error=f"{type(e).__name__}: {e}", lane=lane, read_id=read_id,
                    trace=traceback.format_exc()[-1500:])
        return {"read_id": read_id, "written": 0, "error": f"{type(e).__name__}: {e}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="forecast one live read with every voice and write the lines")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--lane", default="live")
    ap.add_argument("--read-id", default=None, help="default: the newest read of the day")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (ET); default: the read id's day, else today")
    args = ap.parse_args(argv)
    result = after_read(args.state_dir, args.lane, args.read_id, args.day)
    print(result)
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
