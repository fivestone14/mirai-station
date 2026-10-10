"""Where everything this package writes lives, and nothing else.

The package writes only under one folder, ``state/spx_claude_forecast/`` (``forecast_root``), plus an
off-disk backup folder it is told about. Every other station store it reads is listed in
``station_stores.py`` and is never opened for writing: a write anywhere else is a bug the writers in
``jsonl_store.py`` refuse (see ``assert_path_is_ours``).

    state/spx_claude_forecast/
      STORE_MAP.json                 every path below, with its writer, reader and retention; written by ensure_folders
      control.json                   the pause switch and the daily caps (control.py)
      payloads/{day}.jsonl           one line per scheduled read slot: the frozen scene Claude saw (Phase 3)
      reads/{day}.jsonl              Claude's answers and the final forecast per read (Phase 5)
      outcomes/{day}.jsonl           what actually happened, sealed nightly, never edited (Phase 4/7)
      arms/{day}.jsonl               test-variant replies on frozen payloads, never mixed into the record (Phase 8)
      seed/payloads/, seed/outcomes/ past reads rebuilt from stored history (Phase 4)
      library/rows.parquet           one row per past read: market facts + outcome (rebuilt nightly)
      library/scores.parquet         the track record: one row per read x horizon x forecaster
      library/manifest.json          what the library was built from
      recorder/                      copies of station data that would otherwise be overwritten or deleted (Phase 0)
        dated_book/{as_of}.json      every distinct state/dated_gex/book.json
        siege_spy_minutes/{day}.json one day of SPY per-minute volume from state/siege/baseline.json
        spx_5m/{day}.jsonl           SPX 5-minute bars from Schwab, one file per session
        vix1d_close.jsonl            one line per day: the last $VIX1D quote of the session
        lob_raw/{day}/               gzip copies of state/lob_flow/raw/{day}/ before the collector deletes them
      rules/{sha}.txt                the rulebook text Claude was sent, once per distinct text
      run_logs/job_run_log.jsonl     one line per job run (pruned after 30 days)
      locks/                         single-instance lock files
      latest.json, scorecard.json    the phone tile and Will's view (replaced atomically)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import jsonl_store

PACKAGE_DIR = Path(__file__).resolve().parent
SKILL_DIR = PACKAGE_DIR.parent                       # skills/spx-claude-forecast
STATION_ROOT = SKILL_DIR.parent.parent               # ~/.claude/plugins/mirai-station
FORECAST_FOLDER_NAME = "spx_claude_forecast"         # under the station's state/ folder, beside state/spx_jev
RUN_LOG_KEPT_DAYS = 30                               # run_logs/ is the only thing here that is ever pruned


def default_state_dir() -> Path:
    """The station's state root: ``MIRAI_STATION_STATE`` (set by runtime/scripts/env.sh), else
    ``MIRAI_STATE_DIR`` (the older skills' override), else ``<station>/state``."""
    for name in ("MIRAI_STATION_STATE", "MIRAI_STATE_DIR"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return STATION_ROOT / "state"


@dataclass(frozen=True)
class ForecastPaths:
    """Every path the package writes, derived from one state root so tests can point it at a temp folder."""

    state_dir: Path

    @property
    def root(self) -> Path:
        return self.state_dir / FORECAST_FOLDER_NAME

    # --- the record: one file per market day, append-only -----------------------------------------
    def payloads_file(self, day: str) -> Path:
        return self.root / "payloads" / f"{day}.jsonl"

    def reads_file(self, day: str) -> Path:
        return self.root / "reads" / f"{day}.jsonl"

    def outcomes_file(self, day: str) -> Path:
        return self.root / "outcomes" / f"{day}.jsonl"

    def arms_file(self, day: str) -> Path:
        return self.root / "arms" / f"{day}.jsonl"

    def seed_payloads_file(self, day: str) -> Path:
        return self.root / "seed" / "payloads" / f"{day}.jsonl"

    def seed_outcomes_file(self, day: str) -> Path:
        return self.root / "seed" / "outcomes" / f"{day}.jsonl"

    # --- the nightly library ---------------------------------------------------------------------
    @property
    def library_rows_file(self) -> Path:
        return self.root / "library" / "rows.parquet"

    @property
    def library_scores_file(self) -> Path:
        return self.root / "library" / "scores.parquet"

    @property
    def library_manifest_file(self) -> Path:
        return self.root / "library" / "manifest.json"

    # --- the recorder: copies of at-risk station data -------------------------------------------
    @property
    def recorder_dir(self) -> Path:
        return self.root / "recorder"

    def dated_book_copy_file(self, as_of_stamp: str) -> Path:
        return self.recorder_dir / "dated_book" / f"{as_of_stamp}.json"

    def siege_spy_minutes_file(self, day: str) -> Path:
        return self.recorder_dir / "siege_spy_minutes" / f"{day}.json"

    def spx_5m_bars_file(self, day: str) -> Path:
        return self.recorder_dir / "spx_5m" / f"{day}.jsonl"

    @property
    def vix1d_close_file(self) -> Path:
        return self.recorder_dir / "vix1d_close.jsonl"

    def lob_raw_copy_dir(self, day: str) -> Path:
        return self.recorder_dir / "lob_raw" / day

    # --- everything else -------------------------------------------------------------------------
    @property
    def control_file(self) -> Path:
        return self.root / "control.json"

    @property
    def store_map_file(self) -> Path:
        return self.root / "STORE_MAP.json"

    def rules_file(self, rules_sha: str) -> Path:
        return self.root / "rules" / f"{rules_sha}.txt"

    @property
    def run_log_file(self) -> Path:
        return self.root / "run_logs" / "job_run_log.jsonl"

    def lock_file(self, name: str) -> Path:
        return self.root / "locks" / f"{name}.lock"

    @property
    def latest_file(self) -> Path:
        return self.root / "latest.json"

    @property
    def scorecard_file(self) -> Path:
        return self.root / "scorecard.json"

    @property
    def folders(self) -> tuple[Path, ...]:
        """Every folder ensure_folders creates; the list is also what STORE_MAP.json describes."""
        r = self.root
        return (r, r / "payloads", r / "reads", r / "outcomes", r / "arms", r / "seed" / "payloads",
                r / "seed" / "outcomes", r / "library", r / "rules", r / "run_logs", r / "locks",
                self.recorder_dir / "dated_book", self.recorder_dir / "siege_spy_minutes",
                self.recorder_dir / "spx_5m", self.recorder_dir / "lob_raw")


STORE_MAP = {
    "payloads/{day}.jsonl": {"writer": "read hook (Phase 3)", "reader": "nightly job, Claude call", "retention": "forever", "edited": "never"},
    "reads/{day}.jsonl": {"writer": "Claude call (Phase 5)", "reader": "nightly job, phone tile", "retention": "forever", "edited": "never"},
    "outcomes/{day}.jsonl": {"writer": "nightly seal (Phase 7)", "reader": "library, scorecard", "retention": "forever", "edited": "never; a regrade appends a new rule version"},
    "arms/{day}.jsonl": {"writer": "nightly test variants (Phase 8)", "reader": "scorecard", "retention": "forever", "edited": "never"},
    "seed/": {"writer": "seed rebuild (Phase 4)", "reader": "library", "retention": "forever, one set per builder version", "edited": "never"},
    "library/": {"writer": "nightly rebuild", "reader": "read hook (base rate, precedents), scorecard", "retention": "rebuildable from the files above", "edited": "replaced whole"},
    "recorder/dated_book/{as_of}.json": {"writer": "recorder", "reader": "payload builder", "retention": "forever", "edited": "never"},
    "recorder/siege_spy_minutes/{day}.json": {"writer": "recorder", "reader": "payload builder (SPY volume vs usual)", "retention": "forever", "edited": "never"},
    "recorder/spx_5m/{day}.jsonl": {"writer": "recorder (Schwab, outside market hours)", "reader": "base rate (later)", "retention": "forever", "edited": "never"},
    "recorder/vix1d_close.jsonl": {"writer": "recorder", "reader": "payload builder", "retention": "forever", "edited": "never"},
    "recorder/lob_raw/{day}/": {"writer": "recorder", "reader": "later flow research", "retention": "forever (insurance only; the flow block reads lob_flow/agg)", "edited": "never"},
    "rules/{sha}.txt": {"writer": "Claude call", "reader": "anyone rebuilding a prompt", "retention": "forever", "edited": "never"},
    "control.json": {"writer": "Will or the CLI", "reader": "every job", "retention": "current", "edited": "replaced atomically"},
    "run_logs/job_run_log.jsonl": {"writer": "every job", "reader": "health checks", "retention": f"{RUN_LOG_KEPT_DAYS} days", "edited": "never"},
    "latest.json, scorecard.json": {"writer": "read hook, nightly job", "reader": "the phone", "retention": "current", "edited": "replaced atomically"},
}


def ensure_folders(state_dir: Path | str, backup_dir: Path | str | None = None) -> ForecastPaths:
    """Set this process up to write: create every folder the package writes to, declare the forecast root
    (and the backup folder, when given) as the only writable places, and refresh STORE_MAP.json when its
    text changed (an unchanged file keeps its mtime, so the backup does not recopy it); returns the path set."""
    paths = ForecastPaths(Path(state_dir).expanduser())
    for folder in paths.folders:
        folder.mkdir(parents=True, exist_ok=True)
    jsonl_store.allow_writes_under(paths.root, *([backup_dir] if backup_dir else []))
    text = json.dumps({"root": str(paths.root), "stores": STORE_MAP}, ensure_ascii=False, indent=1, sort_keys=True)
    if not paths.store_map_file.exists() or paths.store_map_file.read_text(encoding="utf-8") != text:
        jsonl_store.write_bytes_atomically(paths.store_map_file, text.encode("utf-8"))
    return paths
