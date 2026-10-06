"""Where the Mirai Prediction System keeps its data: one folder, sorted by what kind of data it is.

    <state_dir>/spx_jev/mirai_prediction/
        raw/new_voice_forecasts/{day}.jsonl        written live by the read hook; append-only; the record
        tables/new_voice_forecasts/day=.../         built nightly from raw/ with checks; safe to delete and rebuild
        tables/voice_scores/day=.../                each voice's penalty per graded read
        tables/_checks/refused_rows/{day}.jsonl     rows a check refused, with the reason
        pools/pool_v2/weights_{sum_id}.json         pool_v2's state: voice weights, which voices are asleep, days learned
        pools/pool_v2/weight_updates_{sum_id}.jsonl one line per day learned
        catalogs/voice_fits/{day}_{sum_id}.json     the fitted learners for that day (fit on days before it) with the answer
                                                    matrix they were fitted on; only the newest FIT_FILES_KEPT_DAYS days are kept
        scoreboard.json                             the phone card's file
        run_logs/job_run_log.jsonl                  every job and hook run
        archive/                                    backups taken before any state file changes
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_FOLDER = "mirai_prediction"
RAW = "raw"
TABLES = "tables"
POOLS = "pools"
CATALOGS = "catalogs"
RUN_LOGS = "run_logs"
ARCHIVE = "archive"
NEW_VOICE_FORECASTS = "new_voice_forecasts"
VOICE_SCORES = "voice_scores"
REFUSED_ROWS = "_checks/refused_rows"
POOL_V2 = "pool_v2"
VOICE_FITS = "voice_fits"
SCOREBOARD_FILE = "scoreboard.json"
JOB_RUN_LOG = "job_run_log.jsonl"
FIT_FILES_KEPT_DAYS = 10         # older fit files are deleted by the nightly job (every one can be rebuilt from the store)


def spx_jev_dir(state_dir: Path | str) -> Path:
    """The live service's own folder: <state_dir>/spx_jev (read-only for this system)."""
    return Path(state_dir) / "spx_jev"


def data_root(state_dir: Path | str) -> Path:
    """This system's folder: <state_dir>/spx_jev/mirai_prediction."""
    return spx_jev_dir(state_dir) / SYSTEM_FOLDER


def ensure_folders(root: Path) -> Path:
    """Create every folder of the layout; returns the root."""
    for sub in (f"{RAW}/{NEW_VOICE_FORECASTS}", f"{TABLES}/{NEW_VOICE_FORECASTS}", f"{TABLES}/{VOICE_SCORES}",
                f"{TABLES}/{REFUSED_ROWS}", f"{POOLS}/{POOL_V2}", f"{CATALOGS}/{VOICE_FITS}", RUN_LOGS, ARCHIVE):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def raw_forecasts_file(root: Path, day: str) -> Path:
    return root / RAW / NEW_VOICE_FORECASTS / f"{day}.jsonl"


def forecasts_table_dir(root: Path) -> Path:
    return root / TABLES / NEW_VOICE_FORECASTS


def voice_scores_table_dir(root: Path) -> Path:
    return root / TABLES / VOICE_SCORES


def refused_rows_file(root: Path, day: str) -> Path:
    return root / TABLES / REFUSED_ROWS / f"{day}.jsonl"


def pool_v2_weights_file(root: Path, sum_id: str) -> Path:
    return root / POOLS / POOL_V2 / f"weights_{sum_id}.json"


def pool_v2_updates_file(root: Path, sum_id: str) -> Path:
    return root / POOLS / POOL_V2 / f"weight_updates_{sum_id}.jsonl"


def voice_fits_file(root: Path, day: str, sum_id: str) -> Path:
    return root / CATALOGS / VOICE_FITS / f"{day}_{sum_id}.json"


def prune_voice_fits(root: Path, keep_days: int = FIT_FILES_KEPT_DAYS) -> list[str]:
    """Delete fit files older than the newest ``keep_days`` fit days; returns the names deleted."""
    folder = root / CATALOGS / VOICE_FITS
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
    return root / SCOREBOARD_FILE


def job_run_log_file(root: Path) -> Path:
    return root / RUN_LOGS / JOB_RUN_LOG


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")


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
    """One line per job or hook run in run_logs/job_run_log.jsonl."""
    append_json_line(job_run_log_file(root), {"job_name": job_name, "started_at": started_at, "finished_at": now_utc_iso(),
                                              "ok": ok, "error": error, **counts})


def backup_before_change(root: Path, path: Path) -> Path | None:
    """Copy a state file into archive/ before it changes; returns the copy's path, or None when there was no file."""
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copy = root / ARCHIVE / f"{path.name}.{stamp}"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(path.read_bytes())
    return copy
