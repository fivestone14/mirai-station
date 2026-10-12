"""Where everything this package writes lives, and nothing else.

Every file is named in station_paths.py; this module hands them out from one state root. The package
writes only under its own folder there, ``spx/claude_forecast/`` (``root``), the recorder's own files under
``market/``, its phone card under ``screen_files/phone/``, its logs and locks under ``operations/``, plus an
off-disk backup folder it is told about (``writable``). Every other station store it reads is listed in
``station_stores.py`` and is never opened for writing: a write anywhere else is a bug the writers in
``jsonl_store.py`` refuse (see ``assert_path_is_ours``).

    spx/claude_forecast/
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
    market/                                           the recorder's own files (Phase 0): what no other job keeps
      spx_price_bars_by_5_minutes/{day}.jsonl         SPX 5-minute bars from Schwab, one file per session
      vix_one_day_closes_by_day.jsonl                 one line per day: the last $VIX1D quote of the session
      leveraged_fund_assets_by_run.jsonl              one line per recorder run: the leveraged index funds' assets and leverage from Schwab
    The recorder's copy tasks went with cleanup phase 5 (2026-10-12): the dated-book job keeps its own history
    (spx_monthly_quarterly_open_interest_by_day/, read here through open_interest_history_folder), the wall sensor
    writes spy_volume_by_minute/ itself and the collector keeps its raw day folders forever; the backup still mirrors
    those three stores (backed_up_market_stores) beside the recorder's own files.
    screen_files/phone/claude_forecast_card.json      the phone tile (replaced atomically)
    operations/logs/claude_forecast_job_runs_log.jsonl   one line per job run (pruned after 30 days)
    operations/logs/claude_forecast_read_job_log.txt     the detached read runner's output, appended per read (never pruned)
    operations/locks/claude_forecast_*_job.lock       single-instance lock files

Who writes and reads each store, and what one row holds, is in docs/data_map.md at the repo root.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import jsonl_store

PACKAGE_DIR = Path(__file__).resolve().parent
SKILL_DIR = PACKAGE_DIR.parent                       # pipelines/spx_claude_forecast
STATION_ROOT = SKILL_DIR.parent.parent               # ~/mirai_awakening

_REPO_ROOT = str(STATION_ROOT)             # station_paths.py names every store, this package's included
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402
import station_store_files  # noqa: E402

FORECAST_FOLDER_NAME = station_paths.CLAUDE_FORECAST_FOLDER.relative   # under the station's data root: spx/claude_forecast
RUN_LOG_KEPT_DAYS = 30                               # the job runs log is the only thing here that is ever pruned


def default_state_dir() -> Path:
    """The station's state root, as station_paths.py decides it: ``MIRAI_STATE_DIR`` (the per-test override),
    else ``MIRAI_STATION_STATE`` (set by scheduled_jobs/job_start_scripts/env.sh), else ``<station>/state``."""
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

    # --- the recorder's own files (under market/), and the stores the backup mirrors beside them ----
    @property
    def open_interest_history_folder(self) -> Path:
        """The dated-book job's copy of every book it pulled (read here, never written)."""
        return station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY.folder(self.state_dir)

    def spx_5m_bars_file(self, day: str) -> Path:
        return self._at(station_paths.SPX_PRICE_BARS_BY_5_MINUTES, day=day)

    @property
    def vix1d_close_file(self) -> Path:
        return self._at(station_paths.VIX_ONE_DAY_CLOSES_BY_DAY)

    @property
    def leveraged_fund_sizes_file(self) -> Path:
        return self._at(station_paths.LEVERAGED_FUND_ASSETS_BY_RUN)

    @property
    def recorder_files(self) -> tuple[Path, ...]:
        """Every folder and file the recorder itself fills under market/."""
        return (station_paths.SPX_PRICE_BARS_BY_5_MINUTES.folder(self.state_dir), self.vix1d_close_file, self.leveraged_fund_sizes_file)

    @property
    def raw_tape_folder(self) -> Path:
        """The collector's raw day folders (one a day, kept forever); the backup mirrors the finished days."""
        return station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.folder(self.state_dir)

    @property
    def backed_up_market_stores(self) -> tuple[Path, ...]:
        """The irreplaceable set under market/ the backup mirrors beside the forecast folder: the recorder's own files
        and the three stores other jobs keep (the open-interest history, SPY's volume per minute, the raw tape)."""
        return (*self.recorder_files, self.open_interest_history_folder, station_paths.SPY_VOLUME_BY_MINUTE.folder(self.state_dir),
                self.raw_tape_folder)

    # --- everything else -------------------------------------------------------------------------
    @property
    def control_file(self) -> Path:
        return self._at(station_paths.CLAUDE_FORECAST_PAUSE_AND_LIMITS)

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
        """Every folder ensure_folders creates."""
        return (self.root, self.payloads_dir, self.reads_dir, self.outcomes_dir, self.arms_dir, self.seed_payloads_dir,
                self.seed_outcomes_dir, self.library_rows_file.parent, station_paths.CLAUDE_FORECAST_RULEBOOKS_BY_VERSION.folder(self.state_dir),
                self.run_log_file.parent, station_paths.CLAUDE_FORECAST_NIGHTLY_JOB_LOCK.folder(self.state_dir),
                self.latest_file.parent, station_paths.SPX_PRICE_BARS_BY_5_MINUTES.folder(self.state_dir))

    @property
    def writable(self) -> tuple[Path, ...]:
        """The only places this package may write: its own folder, the recorder's own files (each folder or file on its
        own, since market/ holds other pipelines' stores too), its phone card, its logs and its locks. A write anywhere
        else is a bug jsonl_store refuses."""
        return (self.root, *self.recorder_files, self.latest_file, self.run_log_file, self.read_runner_log_file,
                *(self.lock_file(job) for job in station_paths.CLAUDE_FORECAST_JOB_LOCK_BY_JOB))


def ensure_folders(state_dir: Path | str, backup_dir: Path | str | None = None) -> ForecastPaths:
    """Set this process up to write: create every folder the package writes to and declare its writable places
    (``ForecastPaths.writable``, and the backup folder when given) as the only ones; returns the path set."""
    paths = ForecastPaths(Path(state_dir).expanduser())
    for folder in paths.folders:
        folder.mkdir(parents=True, exist_ok=True)
    jsonl_store.allow_writes_under(*paths.writable, *([backup_dir] if backup_dir else []))
    return paths
