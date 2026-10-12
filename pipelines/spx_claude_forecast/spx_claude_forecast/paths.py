"""Where everything this package writes lives, and nothing else.

Every file is named in runtime/station_paths.py; this module hands them out from one state root. The package
writes only under its own folder there, ``spx/claude_forecast/`` (``root``), the recorder's copies under
``market/``, its phone card under ``screen_files/phone/``, its logs and locks under ``operations/``, plus an
off-disk backup folder it is told about (``writable``). Every other station store it reads is listed in
``station_stores.py`` and is never opened for writing: a write anywhere else is a bug the writers in
``jsonl_store.py`` refuse (see ``assert_path_is_ours``).

    spx/claude_forecast/
      STORE_MAP.json                                  every path below, with its writer, reader and retention; written by ensure_folders (retired by the map; goes with its writer in phase 2)
      claude_forecast_pause_and_limits.json           the pause switch and the daily caps (control.py)
      claude_forecast_inputs_by_read/{day}.jsonl      one line per scheduled read slot: the frozen scene Claude saw (Phase 3)
      claude_forecast_answers_by_read/{day}.jsonl     Claude's answers and the final forecast per read (Phase 5)
      claude_forecast_outcomes_by_read/{day}.jsonl    what actually happened, sealed nightly, never edited (Phase 4/7)
      claude_forecast_test_answers_by_variant_and_read/{day}.jsonl   test-variant replies on frozen payloads, never mixed into the record (Phase 8)
      claude_forecast_rebuilt_inputs_by_read/, claude_forecast_rebuilt_outcomes_by_read/   past reads rebuilt from stored history (Phase 4)
      tables/claude_forecast_fingerprints_and_outcomes_by_read.parquet          one row per past read: market facts + outcome (rebuilt nightly)
      tables/claude_forecast_grades_by_forecaster_read_and_horizon.parquet      the track record: one row per read x horizon x forecaster
      tables/claude_forecast_fingerprints_and_outcomes_build_latest.json        what the library was built from
      claude_forecast_rulebooks_by_version/{sha}.txt  the rulebook text Claude was sent, once per distinct text
      claude_forecast_scoreboard_latest.json          Will's view (replaced atomically)
    market/                                           the recorder's copies of station data that would otherwise be overwritten or deleted (Phase 0)
      spx_monthly_quarterly_open_interest_by_day/{as_of}.json   every distinct dated options book
      spy_volume_by_minute/{day}.json                 one day of SPY per-minute volume from the wall sensor's baseline
      spx_price_bars_by_5_minutes/{day}.jsonl         SPX 5-minute bars from Schwab, one file per session
      vix_one_day_closes_by_day.jsonl                 one line per day: the last $VIX1D quote of the session
      leveraged_fund_assets_by_run.jsonl              one line per recorder run: the leveraged index funds' assets and leverage from Schwab
      spx_same_day_options_raw_day_folders_recorder_copy/{day}/   gzip copies of the collector's raw day folders before it deletes them
    screen_files/phone/claude_forecast_card.json      the phone tile (replaced atomically)
    operations/logs/claude_forecast_job_runs_log.jsonl   one line per job run (pruned after 30 days)
    operations/logs/claude_forecast_read_job_log.txt     the detached read runner's output, appended per read (never pruned)
    operations/locks/claude_forecast_*_job.lock       single-instance lock files
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from . import jsonl_store

PACKAGE_DIR = Path(__file__).resolve().parent
SKILL_DIR = PACKAGE_DIR.parent                       # skills/spx-claude-forecast
STATION_ROOT = SKILL_DIR.parent.parent               # ~/.claude/plugins/mirai-station

_RUNTIME = str(STATION_ROOT / "runtime")             # runtime/station_paths.py names every store, this package's included
if _RUNTIME not in sys.path:
    sys.path.append(_RUNTIME)
import station_paths  # noqa: E402

FORECAST_FOLDER_NAME = station_paths.CLAUDE_FORECAST_FOLDER.relative   # under the station's data root: spx/claude_forecast
RUN_LOG_KEPT_DAYS = 30                               # the job runs log is the only thing here that is ever pruned


def default_state_dir() -> Path:
    """The station's state root, as runtime/station_paths.py decides it: ``MIRAI_STATE_DIR`` (the per-test override),
    else ``MIRAI_STATION_STATE`` (set by runtime/scripts/env.sh), else ``<station>/state``."""
    return station_paths.state_root()


@dataclass(frozen=True)
class ForecastPaths:
    """Every path the package writes, each named in station_paths and derived from one state root so tests can point
    it at a temp folder."""

    state_dir: Path

    def _at(self, store, **keys) -> Path:
        return store.path(self.state_dir, **keys)

    @property
    def root(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_FOLDER)

    # --- the record: one file per market day, append-only -----------------------------------------
    def payloads_file(self, day: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_INPUTS_BY_READ, day=day)

    @property
    def payloads_dir(self) -> Path:
        return station_paths.CLAUDE_FORECAST_INPUTS_BY_READ.folder(self.state_dir)

    def reads_file(self, day: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_ANSWERS_BY_READ, day=day)

    @property
    def reads_dir(self) -> Path:
        return station_paths.CLAUDE_FORECAST_ANSWERS_BY_READ.folder(self.state_dir)

    def outcomes_file(self, day: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_OUTCOMES_BY_READ, day=day)

    @property
    def outcomes_dir(self) -> Path:
        return station_paths.CLAUDE_FORECAST_OUTCOMES_BY_READ.folder(self.state_dir)

    def arms_file(self, day: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_TEST_ANSWERS_BY_VARIANT_AND_READ, day=day)

    @property
    def arms_dir(self) -> Path:
        return station_paths.CLAUDE_FORECAST_TEST_ANSWERS_BY_VARIANT_AND_READ.folder(self.state_dir)

    def seed_payloads_file(self, day: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ, day=day)

    @property
    def seed_payloads_dir(self) -> Path:
        return station_paths.CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ.folder(self.state_dir)

    def seed_outcomes_file(self, day: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_REBUILT_OUTCOMES_BY_READ, day=day)

    @property
    def seed_outcomes_dir(self) -> Path:
        return station_paths.CLAUDE_FORECAST_REBUILT_OUTCOMES_BY_READ.folder(self.state_dir)

    # --- the nightly library ---------------------------------------------------------------------
    @property
    def library_rows_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BY_READ_TABLE)

    @property
    def library_scores_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE)

    @property
    def library_manifest_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BUILD_LATEST)

    # --- the recorder: copies of at-risk station data (under market/) ---------------------------
    def dated_book_copy_file(self, as_of_stamp: str) -> Path:
        return self._at(station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY, stamp=as_of_stamp)

    @property
    def dated_book_copies_dir(self) -> Path:
        return station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY.folder(self.state_dir)

    def siege_spy_minutes_file(self, day: str) -> Path:
        return self._at(station_paths.SPY_VOLUME_BY_MINUTE, day=day)

    def spx_5m_bars_file(self, day: str) -> Path:
        return self._at(station_paths.SPX_PRICE_BARS_BY_5_MINUTES, day=day)

    @property
    def vix1d_close_file(self) -> Path:
        return self._at(station_paths.VIX_ONE_DAY_CLOSES_BY_DAY)

    @property
    def leveraged_fund_sizes_file(self) -> Path:
        return self._at(station_paths.LEVERAGED_FUND_ASSETS_BY_RUN)

    def lob_raw_copy_dir(self, day: str) -> Path:
        return self._at(station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDERS_RECORDER_COPY, day=day)

    @property
    def recorder_copies(self) -> tuple[Path, ...]:
        """Every folder and file the recorder fills under market/, the backup's irreplaceable set beside the root."""
        return (self.dated_book_copies_dir, station_paths.SPY_VOLUME_BY_MINUTE.folder(self.state_dir),
                station_paths.SPX_PRICE_BARS_BY_5_MINUTES.folder(self.state_dir),
                station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDERS_RECORDER_COPY.folder(self.state_dir),
                self.vix1d_close_file, self.leveraged_fund_sizes_file)

    # --- everything else -------------------------------------------------------------------------
    @property
    def control_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_PAUSE_AND_LIMITS)

    @property
    def store_map_file(self) -> Path:
        return self.root / "STORE_MAP.json"            # retired by the map (nothing reads it); still written until its writer goes

    def rules_file(self, rules_sha: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_RULEBOOKS_BY_VERSION, sha=rules_sha)

    @property
    def run_log_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_JOB_RUNS_LOG)

    @property
    def read_runner_log_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_READ_JOB_LOG)

    def lock_file(self, name: str) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_JOB_LOCK_BY_JOB[name])

    @property
    def latest_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_CARD)

    @property
    def scorecard_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_SCOREBOARD_LATEST)

    @property
    def folders(self) -> tuple[Path, ...]:
        """Every folder ensure_folders creates; the list is also what STORE_MAP.json describes."""
        return (self.root, self.payloads_dir, self.reads_dir, self.outcomes_dir, self.arms_dir, self.seed_payloads_dir,
                self.seed_outcomes_dir, self.library_rows_file.parent, station_paths.CLAUDE_FORECAST_RULEBOOKS_BY_VERSION.folder(self.state_dir),
                self.run_log_file.parent, station_paths.CLAUDE_FORECAST_NIGHTLY_JOB_LOCK.folder(self.state_dir),
                self.latest_file.parent, self.dated_book_copies_dir, station_paths.SPY_VOLUME_BY_MINUTE.folder(self.state_dir),
                station_paths.SPX_PRICE_BARS_BY_5_MINUTES.folder(self.state_dir),
                station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDERS_RECORDER_COPY.folder(self.state_dir))

    @property
    def writable(self) -> tuple[Path, ...]:
        """The only places this package may write: its own folder, the recorder's copies (each folder or file on its
        own, since market/ holds other pipelines' stores too), its phone card, its logs and its locks. A write anywhere
        else is a bug jsonl_store refuses."""
        return (self.root, *self.recorder_copies, self.latest_file, self.run_log_file, self.read_runner_log_file,
                *(self.lock_file(job) for job in station_paths.CLAUDE_FORECAST_JOB_LOCK_BY_JOB))


STORE_MAP = {
    "payloads/{day}.jsonl": {"writer": "read hook (Phase 3)", "reader": "nightly job (seal, test variants, library); the read runner's already-built check", "retention": "forever", "edited": "never"},
    "reads/{day}.jsonl": {"writer": "Claude call (Phase 5)", "reader": "nightly job (test variants, scores, scorecard); the daily call cap", "retention": "forever", "edited": "never"},
    "outcomes/{day}.jsonl": {"writer": "nightly seal (Phase 7)", "reader": "nightly job (seal, library, scores)", "retention": "forever", "edited": "never; a regrade appends a new rule version"},
    "arms/{day}.jsonl": {"writer": "nightly test variants (test_variants.py)", "reader": "nightly job (test variants, scores); the daily call cap", "retention": "forever", "edited": "never"},
    "seed/": {"writer": "seed rebuild (Phase 4)", "reader": "library", "retention": "forever, one set per builder version", "edited": "never"},
    "library/": {"writer": "nightly rebuild", "reader": "read runner (base rate, precedents); nightly job (test variants, scorecard)", "retention": "rebuildable from the files above", "edited": "replaced whole"},
    "recorder/dated_book/{as_of}.json": {"writer": "recorder", "reader": "payload builder", "retention": "forever", "edited": "never"},
    "recorder/siege_spy_minutes/{day}.json": {"writer": "recorder", "reader": "payload builder (SPY volume vs usual)", "retention": "forever", "edited": "never"},
    "recorder/spx_5m/{day}.jsonl": {"writer": "recorder (Schwab, outside market hours)", "reader": "none yet (kept as insurance)", "retention": "forever", "edited": "never"},
    "recorder/vix1d_close.jsonl": {"writer": "recorder", "reader": "payload builder", "retention": "forever", "edited": "never"},
    "recorder/leveraged_fund_sizes.jsonl": {"writer": "recorder (Schwab instrument data, every run)", "reader": "payload builder (the close-signals rebalance estimate)", "retention": "forever", "edited": "never"},
    "recorder/lob_raw/{day}/": {"writer": "recorder", "reader": "none yet (the flow block reads the station's own lob_flow/agg and lob_flow/raw)", "retention": "forever (insurance only)", "edited": "never"},
    "rules/{sha}.txt": {"writer": "Claude call", "reader": "none in code; kept so a sent prompt can be rebuilt by hand", "retention": "forever", "edited": "never"},
    "control.json": {"writer": "Will or the CLI", "reader": "the call gate (read runner, nightly test variants)", "retention": "current", "edited": "replaced atomically"},
    "run_logs/job_run_log.jsonl": {"writer": "every job", "reader": "none in code; the nightly and the recorder prune it", "retention": f"{RUN_LOG_KEPT_DAYS} days", "edited": "never"},
    "run_logs/read_runner.out": {"writer": "read hook (the detached read runner's stdout and stderr)", "reader": "none in code; read by hand when a read goes wrong", "retention": "forever", "edited": "appended"},
    "latest.json, scorecard.json": {"writer": "read hook, nightly job", "reader": "the phone's Claude card (latest.json); the read runner's card verdict and Will (scorecard.json)", "retention": "current", "edited": "replaced atomically"},
}


def ensure_folders(state_dir: Path | str, backup_dir: Path | str | None = None) -> ForecastPaths:
    """Set this process up to write: create every folder the package writes to, declare its writable places
    (``ForecastPaths.writable``, and the backup folder when given) as the only ones, and refresh STORE_MAP.json when its
    text changed (an unchanged file keeps its mtime, so the backup does not recopy it); returns the path set."""
    paths = ForecastPaths(Path(state_dir).expanduser())
    for folder in paths.folders:
        folder.mkdir(parents=True, exist_ok=True)
    jsonl_store.allow_writes_under(*paths.writable, *([backup_dir] if backup_dir else []))
    text = json.dumps({"root": str(paths.root), "stores": STORE_MAP}, ensure_ascii=False, indent=1, sort_keys=True)
    if not paths.store_map_file.exists() or paths.store_map_file.read_text(encoding="utf-8") != text:
        jsonl_store.write_bytes_atomically(paths.store_map_file, text.encode("utf-8"))
    return paths
