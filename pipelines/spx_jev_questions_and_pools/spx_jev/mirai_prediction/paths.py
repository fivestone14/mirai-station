"""Where the Mirai Prediction System keeps its data, every file named in station_paths.py.

This system's folder is ``<state_dir>/spx/prediction/`` (``data_root``); its stores sit in it, and the few files it
writes for other parts of the station sit where those parts look (``state_dir_of`` finds the data root again from the
folder a job holds):

    spx/prediction/
        forecasts_by_forecaster_read_and_horizon/{day}.jsonl    written live by the read hook; append-only; the record
        code_question_answers_by_read/{day}.jsonl               the code feature builder's answers per read (layers 1-2), written live
                                                                 before the read is forecast, or by the backfill; append-only; the record
        tables/forecasts_by_forecaster_read_and_horizon/day=.../ built nightly from the raw file with checks; safe to delete and rebuild
        tables/grades_by_forecaster_read_and_horizon/day=.../    each voice's penalty per graded read
        learner_pool/learner_pool_state/{horizon}.json          the learner pool's state: voice weights, which voices are asleep, days learned
        learner_pool/learner_pool_trained_forecasters_by_day/{day}_{horizon}.json
                                                                 the fitted learners for that day (fit on days before it) with the answer
                                                                 matrix they were fitted on; only the newest FIT_FILES_KEPT_DAYS days are kept
    screen_files/phone/forecaster_scoreboard_card.json          the phone card's file
    operations/logs/prediction_job_runs_log.jsonl               every job and hook run
    operations/logs/forecaster_forecasts_job_log.txt            the after-read hook's output
    operations/health/forecaster_table_refused_rows_by_event/{day}.jsonl   rows a check refused, with the reason
    archive/learner_pool_rollback_copies/                       backups taken before any state file changes (the newest
                                                                 ARCHIVE_BACKUPS_KEPT per file name) and what a forced replay
                                                                 cleared (replay_force_*, REPLAY_BACKUPS_KEPT_DAYS days)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[4])             # station_paths.py names every store
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402

FOLDER = station_paths.PREDICTION_FOLDER     # this system's folder under the data root: spx/prediction
FIT_FILES_KEPT_DAYS = 10         # older fit files are deleted by the nightly job (every one can be rebuilt from the store)
ARCHIVE_BACKUPS_KEPT = 10        # archive/ keeps the newest backups per file name; the nightly job deletes the rest
REPLAY_BACKUPS_KEPT_DAYS = 7     # and a forced replay's backup folder (replay_force_<stamp>) once it is this old
STAMP = "%Y%m%dT%H%M%SZ"         # the UTC stamp every backup is named by


def data_root(state_dir: Path | str) -> Path:
    """This system's folder: <state_dir>/spx/prediction."""
    return FOLDER.path(state_dir)


def state_dir_of(root: Path | str) -> Path:
    """The data root this system's folder ``root`` sits under (every job hands data_root(state_dir) back), for the
    files it writes outside its folder: the phone card, the logs, the refused rows, the rollback copies."""
    state_dir = FOLDER.root_of(root)
    if state_dir is None:
        raise ValueError(f"{root} is not the prediction system's folder ({FOLDER.relative}) under a data root")
    return state_dir


def ensure_folders(root: Path) -> Path:
    """Create every folder of the layout; returns the root."""
    root = Path(root)
    for folder in (raw_forecasts_dir(root), raw_code_features_dir(root), forecasts_table_dir(root), voice_scores_table_dir(root),
                   refused_rows_file(root, "x").parent, learner_pool_dir(root), voice_fits_dir(root), job_run_log_file(root).parent,
                   archive_dir(root)):
        folder.mkdir(parents=True, exist_ok=True)
    return root


def raw_forecasts_file(root: Path, day: str) -> Path:
    return station_paths.FORECASTS_BY_FORECASTER_READ_AND_HORIZON.within(FOLDER, root, day=day)


def raw_forecasts_dir(root: Path) -> Path:
    return station_paths.FORECASTS_BY_FORECASTER_READ_AND_HORIZON.folder_within(FOLDER, root)


def raw_code_features_file(root: Path, day: str) -> Path:
    return station_paths.CODE_QUESTION_ANSWERS_BY_READ.within(FOLDER, root, day=day)


def raw_code_features_dir(root: Path) -> Path:
    return station_paths.CODE_QUESTION_ANSWERS_BY_READ.folder_within(FOLDER, root)


def forecasts_table_dir(root: Path) -> Path:
    return station_paths.FORECASTS_BY_FORECASTER_READ_AND_HORIZON_TABLE.within(FOLDER, root)


def voice_scores_table_dir(root: Path) -> Path:
    return station_paths.GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE.within(FOLDER, root)


def refused_rows_file(root: Path, day: str) -> Path:
    return station_paths.FORECASTER_TABLE_REFUSED_ROWS_BY_EVENT.path(state_dir_of(root), day=day)


def learner_pool_weights_file(root: Path, sum_id: str) -> Path:
    return station_paths.LEARNER_POOL_STATE.within(FOLDER, root, sum_id=sum_id)


def learner_pool_dir(root: Path) -> Path:
    return station_paths.LEARNER_POOL_STATE.folder_within(FOLDER, root)


def voice_fits_file(root: Path, day: str, sum_id: str) -> Path:
    return station_paths.LEARNER_POOL_TRAINED_FORECASTERS_BY_DAY.within(FOLDER, root, day=day, sum_id=sum_id)


def voice_fits_dir(root: Path) -> Path:
    return station_paths.LEARNER_POOL_TRAINED_FORECASTERS_BY_DAY.folder_within(FOLDER, root)


def archive_dir(root: Path) -> Path:
    return station_paths.LEARNER_POOL_ROLLBACK_COPIES.path(state_dir_of(root))


def read_hook_log_file(root: Path) -> Path:
    return station_paths.FORECASTER_FORECASTS_JOB_LOG.path(state_dir_of(root))


def prune_voice_fits(root: Path, keep_days: int = FIT_FILES_KEPT_DAYS) -> list[str]:
    """Delete fit files older than the newest ``keep_days`` fit days; returns the names deleted."""
    folder = voice_fits_dir(root)
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    days = sorted({f.name[:10] for f in files})
    keep_from = days[-keep_days] if len(days) > keep_days else None
    deleted = []
    for f in files:
        if keep_from is not None and f.name[:10] < keep_from:
            f.unlink()
            deleted.append(f.name)
    return deleted


def scoreboard_file(root: Path) -> Path:
    return station_paths.FORECASTER_SCOREBOARD_CARD.path(state_dir_of(root))


def job_run_log_file(root: Path) -> Path:
    return station_paths.PREDICTION_JOB_RUNS_LOG.path(state_dir_of(root))


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_et() -> str:
    """Today's market date (New York), the day every file of this system is keyed by."""
    from ..events import ET
    return datetime.now(ET).date().isoformat()


def write_json_atomically(path: Path, data: object) -> None:
    """Write a JSON file in one step, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")      # a name of this process's own, so two writers never share one
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def append_json_line(path: Path, record: dict) -> None:
    """Append one JSON line (the raw record files and the logs are append-only)."""
    append_json_lines(path, [record])


def append_json_lines(path: Path, records: list[dict]) -> None:
    """Append several JSON lines in one write, so a set that belongs together (one sum's voices) lands whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n" for record in records)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def read_json_lines(path: Path) -> list[dict]:
    """Every well-formed JSON object line of a file; a bad line is skipped, never fatal."""
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def log_job_run(root: Path, job_name: str, started_at: str, ok: bool, error: str | None = None, **counts) -> None:
    """One line per job or hook run in operations/logs/prediction_job_runs_log.jsonl."""
    append_json_line(job_run_log_file(root), {"job_name": job_name, "started_at": started_at, "finished_at": now_utc_iso(),
                                              "ok": ok, "error": error, **counts})


def backup_before_change(root: Path, path: Path) -> Path | None:
    """Copy a state file into the rollback-copies folder before it changes; returns the copy's path, or None when there was no file."""
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime(STAMP)
    copy = archive_dir(root) / f"{path.name}.{stamp}"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(path.read_bytes())
    return copy


def prune_archive(root: Path, now: datetime | None = None, keep: int = ARCHIVE_BACKUPS_KEPT,
                  replay_days: int = REPLAY_BACKUPS_KEPT_DAYS) -> list[str]:
    """Delete all but the newest ``keep`` backups of each file name in the rollback-copies folder, and a forced replay's backup folder
    older than ``replay_days`` days; returns the names deleted. A name without the backup stamp is left alone."""
    folder = archive_dir(root)
    if not folder.exists():
        return []
    now = now or datetime.now(timezone.utc)
    deleted: list[str] = []
    by_name: dict[str, list[Path]] = {}
    for path in sorted(folder.iterdir()):                  # by name, so a file's backups come oldest first
        if path.is_dir() and path.name.startswith("replay_force_"):
            stamped = _stamped(path.name[len("replay_force_"):]) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if now - stamped > timedelta(days=replay_days):                   # by its name's stamp, else by its age on disk
                shutil.rmtree(path)
                deleted.append(path.name)
        elif path.is_file():
            name, _, stamp = path.name.rpartition(".")
            if _stamped(stamp) is not None:
                by_name.setdefault(name, []).append(path)
    for backups in by_name.values():
        for path in backups[:len(backups) - keep]:
            path.unlink()
            deleted.append(path.name)
    return deleted


def _stamped(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
