"""Every data store of the station, defined once, under its final name.

Cleanup phase 1 (2026-10-11). Each store below is a ``Store`` whose NAME is the store's final name from the
approved naming map (``final_mapping_v3.md``: ``<subject>_<content>_by_<grain>``, or a ``_latest`` / ``_card`` /
``_state`` / ``_log`` ending; a Parquet table takes ``_TABLE``) and whose VALUE is still TODAY'S location under the
data root, ``state/``. Nothing has moved yet. When the folders move, only the templates in this file change; every
reader and writer, and every page that asks the viewstation for a store by name, follows.

How to import it
----------------
This file lives in ``runtime/``, which is on ``sys.path`` only for the jobs that run as ``python -m watch...``
from the runtime folder. Everything else (the skills, the viewstation server, the voice desk, the test suites)
puts ``runtime/`` on ``sys.path`` first, by its own position in the repo, then imports the module by name::

    import sys
    from pathlib import Path

    _RUNTIME = str(Path(__file__).resolve().parents[N] / "runtime")      # N: this file's depth below the repo root
    if _RUNTIME not in sys.path:
        sys.path.append(_RUNTIME)                                         # appended, so it shadows nothing
    import station_paths

A package does this once, in the module its other modules already import for paths (``spx_jev/__init__.py``,
``spx_claude_forecast/paths.py``, ``book_flow/cache.py``, ``viewstation/snapshot.py``); a flat skill folder does it
in each module that names a store. The module is pure standard library and has no side effects when imported: it
creates no folder and reads no file.

The data root
-------------
``state_root(explicit=None)`` is the one place the root is decided, in the order the station used before this file:
an explicit root (the ``--state-dir`` the launchd scripts pass to the spx-jev and Claude-forecast jobs), else
``MIRAI_STATE_DIR`` (the per-test override every suite sets, so it wins over the station-wide variable even in a
shell that sourced env.sh), else ``MIRAI_STATION_STATE`` (env.sh), else ``$MIRAI_STATION_ROOT/state``, else
``<repo>/state``. A consumer that resolved its root at import keeps doing so (``STATE_DIR = station_paths.state_root()``);
one that resolved per call keeps doing that too.

Keys
----
A template's ``{day}`` is ``YYYY-MM-DD``; ``{lane}`` is a JEV lane name (``live``, ``tape``, ``premarket``) and is
replaced by that lane's folder; ``{symbol}``, ``{ticker}``, ``{sha}``, ``{stamp}``, ``{sum_id}``, ``{ledger_id}``
and ``{stream}`` are the code's own keys. A store that is one of a keyed family (a pool horizon) names the allowed
values; a family's members that the map names one by one are separate stores, gathered in a ``_BY_...`` dict.

Three ways to a path. ``path(root, **keys)`` is the whole path from the data root. ``within(parent, folder, **keys)``
is the same file inside a folder the caller already holds, for the packages whose working root is a folder under the
data root (the prediction system's, the Claude forecast's, a JEV lane's, which a replay can point at a scratch
folder); ``in_lane_folder`` is its JEV spelling. ``file_name(**keys)`` is the last segment alone, for a reader that
holds the folder (the viewstation's tape folder, the fill ledger's folder).

Not defined here: rows the map marks DROP, and stores already retired (their writers are gone). A reader of a
DROP row keeps its own constant until phase 2 removes the writer; the report of this phase lists them.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]            # ~/.claude/plugins/mirai-station
STATE_FOLDER_NAME = "state"                                 # the data root's folder under the repo, today
DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def default_state_root() -> Path:
    """The root with no override: ``$MIRAI_STATION_ROOT/state`` when env.sh set the station root, else ``<repo>/state``."""
    station_root = os.environ.get("MIRAI_STATION_ROOT")
    if station_root:
        return Path(station_root).expanduser().resolve() / STATE_FOLDER_NAME
    return REPO_ROOT / STATE_FOLDER_NAME


def state_root(explicit: Path | str | None = None) -> Path:
    """The data root: an explicit root, else ``MIRAI_STATE_DIR``, else ``MIRAI_STATION_STATE``, else the default."""
    if explicit:
        return Path(explicit).expanduser()
    for name in ("MIRAI_STATE_DIR", "MIRAI_STATION_STATE"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return default_state_root()


# The JEV lanes' folders, today. The live lane writes beside the shared archive; the other lanes have folders of their own.
JEV_LANE_FOLDERS = {"live": "spx_jev", "tape": "spx_jev/lanes/tape", "premarket": "spx_jev/lanes/premarket"}


class Store:
    """One store: a path template under the data root (or under the repo, for the files a skill folder still holds).

    ``keys`` fixes a key (``lane="live"``) or lists the allowed values of one and what each becomes in the path
    (``horizon={"next_30_minutes": "30", ...}``)."""

    __slots__ = ("relative", "base", "keys", "name")

    def __init__(self, relative: str, base: str = "state", **keys):
        self.relative = relative
        self.base = base                                       # "state": under the data root; "repo": under the repo
        self.keys = keys
        self.name = None                                       # set by _register, below

    def __repr__(self) -> str:
        return f"Store({self.name or self.relative!r})"

    def _fill(self, template: str, given: dict) -> str:
        values = {}
        wanted = set(re.findall(r"{(\w+)}", template))
        for key, rule in self.keys.items():
            if isinstance(rule, dict):
                if key not in wanted:
                    given.pop(key, None)
                    continue
                if key not in given:
                    raise KeyError(f"{self.name or self.relative}: {key} is needed, one of {sorted(rule)}")
                value = given.pop(key)
                if value not in rule:
                    raise KeyError(f"{self.name or self.relative}: {key} must be one of {sorted(rule)}, not {value!r}")
                values[key] = rule[value]
            else:
                given.pop(key, None)
                values[key] = rule
        if "lane" in given:
            lane = given.pop("lane")
            if lane not in JEV_LANE_FOLDERS:
                raise KeyError(f"{self.name or self.relative}: lane must be one of {sorted(JEV_LANE_FOLDERS)}, not {lane!r}")
            values["lane"] = JEV_LANE_FOLDERS[lane]
        elif "lane" in values:
            values["lane"] = JEV_LANE_FOLDERS[values["lane"]]
        values.update(given)
        try:
            return template.format(**values)
        except KeyError as exc:
            raise KeyError(f"{self.name or self.relative}: {exc.args[0]} is needed") from None

    def path(self, root: Path | str | None = None, **keys) -> Path:
        """The store's path under ``root`` (else the resolved data root); for a repo-held file, under the repo."""
        filled = self._fill(self.relative, dict(keys))
        if self.base == "repo":
            return (Path(root).expanduser() if root else REPO_ROOT) / filled
        return state_root(root) / filled

    def folder(self, root: Path | str | None = None, **keys) -> Path:
        """The folder the store's files sit in: the template with its last segment dropped (so a keyed file needs no key)."""
        head = self.relative.rpartition("/")[0]
        filled = self._fill(head, dict(keys)) if head else ""
        if self.base == "repo":
            return (Path(root).expanduser() if root else REPO_ROOT) / filled
        return state_root(root) / filled

    def file_name(self, **keys) -> str:
        """The store's last segment alone (``2026-10-10.jsonl``), for a reader that holds the folder."""
        return self._fill(self.relative.rpartition("/")[2], dict(keys))

    @property
    def relative_folder(self) -> Path:
        """The store's folder relative to its root (``reversion`` for ``reversion/{day}.jsonl``), for the packages that
        keep a ``<root> / FOLDER / f"{day}.jsonl"`` habit over many call sites; the day-file shape is the map's own rule."""
        return Path(self.relative).parent

    def relative_within(self, parent: "Store") -> Path:
        """The store's template relative to ``parent``, a folder store whose template this one extends
        (``hour/{day}.jsonl`` for a lane's forecasts under the lane folder)."""
        prefix = parent.relative + "/"
        if not self.relative.startswith(prefix):
            raise ValueError(f"{self.name or self.relative} is not under {parent.name or parent.relative}")
        return Path(self.relative[len(prefix):])

    def within(self, parent: "Store", folder: Path | str, **keys) -> Path:
        """The store's file inside ``folder``, which is where ``parent`` (a folder store whose template this one
        extends) is on this machine: the package's working root, or a replay's scratch folder."""
        return Path(folder) / self._fill(str(self.relative_within(parent)), dict(keys))

    def folder_within(self, parent: "Store", folder: Path | str, **keys) -> Path:
        """The store's folder inside ``folder`` (``within`` with the last segment dropped)."""
        head = str(self.relative_within(parent)).rpartition("/")[0]
        return Path(folder) / self._fill(head, dict(keys)) if head else Path(folder)

    def in_lane_folder(self, folder: Path | str, **keys) -> Path:
        """A lane-scoped JEV store's file inside a lane folder the caller holds."""
        return self.within(JEV_LANE_FOLDER, folder, **keys)

    def key_names(self) -> list[str]:
        """The keys a caller must give: the template's placeholders that are not fixed."""
        return [k for k in re.findall(r"{(\w+)}", self.relative) if not isinstance(self.keys.get(k), str)]


# =====================================================================================================================
# market/   prices, quotes, depth, trades, open interest, the calendar
# =====================================================================================================================
SPX_PRICE_BARS_BY_MINUTE = Store("spx_jev/bars/{day}.jsonl")
WATCHLIST_QUOTES_BY_MINUTE = Store("spx_jev/context/{day}.jsonl")
WATCHLIST_PRICE_BARS_BY_MINUTE = Store("spx_jev/context/bars/{day}.jsonl")
FUTURES_PRICE_BARS_BY_MINUTE = Store("spx_jev/overnight/{day}.jsonl")
FUTURES_PRICE_BAR_CHANGE_COUNTS_BY_SAVE = Store("spx_jev/overnight/manifest.jsonl")
FUTURES_CONTRACTS_AND_PAST_ROLLS_LATEST = Store("spx_jev/overnight/rolls.json")
FUTURES_PRICE_BARS_SAVE_LOCK = Store("spx_jev/overnight/.save.lock")                 # the overnight saver's single-copy lock
SPX_VIX_RATES_PRICE_BARS_BY_DAY = Store("spx_jev/daily_closes/{symbol}.jsonl")
NEWS_HEADLINES_BY_ARTICLE = Store("spx_jev/headlines/{day}.jsonl")
NEWS_HEADLINES_SEEN_STATE = Store("spx_jev/headlines/seen.json")
NEWS_HEADLINES_POLL_LOCK = Store("spx_jev/headlines/.lock")                          # the headline poller's single-copy lock
# The collector's raw day folder holds the trades and the depth (and the SPY depth's gap markers); the recorder keeps a
# gzip copy of each day before the collector deletes it. Both locations merge into one store at the move.
SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER = Store("lob_flow/raw/{day}")
SPX_SAME_DAY_OPTIONS_RAW_STREAM = Store("lob_flow/raw/{day}/{stream}.jsonl")         # stream: tape, sweeps, spy-depth
SPX_SAME_DAY_OPTIONS_TRADES_BY_TRADE = Store("lob_flow/raw/{day}/tape.jsonl")
SPX_SAME_DAY_OPTIONS_TRADES_BY_TRADE_RECORDER_COPY = Store("spx_claude_forecast/recorder/lob_raw/{day}")
SPX_SAME_DAY_OPTIONS_DEPTH_BY_MINUTE = Store("lob_flow/raw/{day}/sweeps.jsonl")
# SPY volume per minute: the wall sensor's rolling baseline (every day inside one file) and the recorder's one-day copies.
SPY_VOLUME_BY_MINUTE = Store("spx_claude_forecast/recorder/siege_spy_minutes/{day}.json")
SPY_VOLUME_BY_MINUTE_ROLLING_BASELINE_STATE = Store("siege/baseline.json")
SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST = Store("dated_gex/book.json")
SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY = Store("spx_claude_forecast/recorder/dated_book/{stamp}.json")
SPX_PRICE_BARS_BY_5_MINUTES = Store("spx_claude_forecast/recorder/spx_5m/{day}.jsonl")
VIX_ONE_DAY_CLOSES_BY_DAY = Store("spx_claude_forecast/recorder/vix1d_close.jsonl")
LEVERAGED_FUND_ASSETS_BY_RUN = Store("spx_claude_forecast/recorder/leveraged_fund_sizes.jsonl")
SPX_LARGEST_STOCK_WEIGHTS_BY_UPDATE = Store("spx_leaders/weights.json")
# The event calendar is kept twice today: the collector's seeded copy under state, and the spx-jev package's own file.
MARKET_EVENT_CALENDAR_BY_EVENT = Store("lob_flow/calendar.json")
MARKET_EVENT_CALENDAR_BY_EVENT_SPX_JEV_COPY = Store("skills/spx-jev/calendar/events.json", base="repo")
SPX_PRICE_BARS_BY_MINUTE_TABLE = Store("spx_jev/store/spx_bars")
FUTURES_PRICE_BARS_BY_MINUTE_TABLE = Store("spx_jev/store/overnight_bars")

# =====================================================================================================================
# spx/scanner/   the SPX options scanner (the left eye), the order-flow collector's readings and the wall sensor
# =====================================================================================================================
SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN = Store("reversion/{day}.jsonl")
SPX_OPTIONS_SCANNER_GRADES_BY_DAY = Store("reversion/polarity_history.jsonl")
SPX_ORDER_FLOW_READINGS_BY_MINUTE = Store("lob_flow/agg/{day}.jsonl")
SPX_ORDER_FLOW_GRADES_BY_DAY = Store("lob_flow/cards/{day}.json")
SPX_OPTIONS_DEPTH_BASELINES_STATE = Store("lob_flow/baselines/lob_flow.json")
SPY_DEPTH_BASELINES_STATE = Store("lob_flow/baselines/spy_depth.json")
DEPTH_BASELINES_STATE_BY_ENGINE = {"lob_flow": SPX_OPTIONS_DEPTH_BASELINES_STATE, "spy_depth": SPY_DEPTH_BASELINES_STATE}
SPX_DEALER_WALL_TOUCHES_BY_EVENT = Store("siege/cards/{day}.jsonl")
SPX_DEALER_WALLS_LATEST = Store("siege/latest.json")
SPX_OPTIONS_SCANNER_CLAUDE_NOTES_BY_READ = Store("spx_rag/slices/{day}.jsonl")
SPX_OPTIONS_SCANNER_SIGNAL_CHECKS_BY_SCAN = Store("skills/mirai-left-eye/logs/{day}.jsonl", base="repo")
SPX_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE = Store("gex_fills/native_SPX.json")
SPY_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE = Store("gex_fills/proxy_SPY.json")
OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE_BY_LEDGER = Store("gex_fills/{ledger_id}.json")  # the two above, by the ledger's own id
SPX_OPTIONS_VOLUME_LAST_SCAN_STATE = Store("tape_prev/SPX.json")
OPTIONS_VOLUME_LAST_SCAN_STATE_BY_TICKER = Store("tape_prev/{ticker}.json")         # the scanner keeps one per ticker it scans
SPX_DEALER_WALL_TOUCHES_TODAY_STATE = Store("siege/session.json")
SPX_OPTIONS_TRADES_RESUME_POINT_STATE = Store("lob_flow/cursor.json")
SPX_ORDER_FLOW_READINGS_LATEST = Store("lob_flow/latest.json")
SPX_DEALER_GAMMA_LEVELS_LATEST = Store("lob_flow/gex_context.json")
SPX_OPTIONS_SCANNER_SIGNALS_SENT_STATE = Store("skills/mirai-left-eye/state.json", base="repo")

# =====================================================================================================================
# spx/jev/   the JEV question pipeline: its three lanes, the shared archive and its Parquet tables
# =====================================================================================================================
JEV_LANE_FOLDER = Store("{lane}")
JEV_READS_AND_GRADES_BY_EVENT = Store("spx_jev/archive/{day}.jsonl")
JEV_ANSWERS_BY_READ = Store("{lane}/{day}.jsonl")
JEV_AND_POOL_FORECASTS_BY_READ = Store("{lane}/hour/{day}.jsonl")
JEV_END_PRICE_GRADES_BY_READ = Store("{lane}/grades.jsonl")
JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON = Store("{lane}/integral_grades.jsonl")
JEV_AVERAGE_PRICE_GRADES_LOCK = Store("{lane}/integral_grades.jsonl.lock")
JEV_QUESTION_WEIGHTS_STATE = Store("{lane}/weights.json")
JEV_LAST_ANSWERS_STATE = Store("{lane}/last_asked.json")
JEV_QUESTION_ASK_SCHEDULE_STATE = Store("{lane}/cadence.json", lane="live")
HISTORICAL_ODDS_END_PRICE_DAYS_STATE = Store("{lane}/clock_days.json", lane="live")
HISTORICAL_ODDS_AVERAGE_PRICE_DAYS_STATE = Store("{lane}/clock_integral_days.json", lane="live")
JEV_READ_SUMMARIES_BY_READ_TABLE = Store("spx_jev/store/reads")
JEV_ANSWERS_BY_READ_AND_QUESTION_TABLE = Store("spx_jev/store/answers")
JEV_AND_POOL_FORECASTS_BY_READ_AND_HORIZON_TABLE = Store("spx_jev/store/calls")
JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON_TABLE = Store("spx_jev/store/average_grades")
JEV_TABLES_FOLDER = Store("spx_jev/store")                                           # every table above, plus the build's counts and refused rows

# =====================================================================================================================
# spx/prediction/   the base pool (pool 1), the learner pool (pool 2), the code questions and the forecaster tables
# =====================================================================================================================
# The base pool's state, one file per horizon: the end-price files are keyed by the horizon's minutes (30, 60; a lane's own
# when the loop is run on it), the average-price file by the window it averages. At the move both become one
# horizon-keyed folder (base_pool_state/next_30_minutes.json ...).
BASE_POOL_STATE = Store("{lane}/pool_{minutes}.json", lane="live")
BASE_POOL_AVERAGE_PRICE_STATE = Store("{lane}/pool_{minutes}_integral.json", lane="live")
BASE_POOL_UPDATES_BY_DAY_AND_HORIZON = Store("{lane}/pool_{horizon}_log.jsonl", lane="live",
                                             horizon={"average_of_next_30_minutes": "integral"})
BASE_POOL_STATE_ROLLBACK_COPIES = Store("{lane}/archive", lane="live")              # <state file>.pre-<constants hash>-<day> copies
LEARNER_POOL_FOLDER = Store("spx_jev/mirai_prediction")                              # the prediction system's own folder, today
LEARNER_POOL_STATE = Store("spx_jev/mirai_prediction/pools/pool_v2/weights_{sum_id}.json")
LEARNER_POOL_TRAINED_FORECASTERS_BY_DAY = Store("spx_jev/mirai_prediction/catalogs/voice_fits/{day}_{sum_id}.json")
LEARNER_POOL_ROLLBACK_COPIES = Store("spx_jev/mirai_prediction/archive")             # <file>.<UTC stamp> copies and replay_force_<stamp>/
CODE_QUESTION_ANSWERS_BY_READ = Store("spx_jev/mirai_prediction/raw/code_features/{day}.jsonl")
FORECASTS_BY_FORECASTER_READ_AND_HORIZON = Store("spx_jev/mirai_prediction/raw/new_voice_forecasts/{day}.jsonl")
FORECASTS_BY_FORECASTER_READ_AND_HORIZON_TABLE = Store("spx_jev/mirai_prediction/tables/new_voice_forecasts")
GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE = Store("spx_jev/mirai_prediction/tables/voice_scores")

# =====================================================================================================================
# spx/claude_forecast/   Claude's own SPX forecast
# =====================================================================================================================
CLAUDE_FORECAST_FOLDER = Store("spx_claude_forecast")                                # the package's one writable folder, today
CLAUDE_FORECAST_INPUTS_BY_READ = Store("spx_claude_forecast/payloads/{day}.jsonl")
CLAUDE_FORECAST_ANSWERS_BY_READ = Store("spx_claude_forecast/reads/{day}.jsonl")
CLAUDE_FORECAST_OUTCOMES_BY_READ = Store("spx_claude_forecast/outcomes/{day}.jsonl")
CLAUDE_FORECAST_TEST_ANSWERS_BY_VARIANT_AND_READ = Store("spx_claude_forecast/arms/{day}.jsonl")
CLAUDE_FORECAST_RULEBOOKS_BY_VERSION = Store("spx_claude_forecast/rules/{sha}.txt")
CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ = Store("spx_claude_forecast/seed/payloads/{day}.jsonl")
CLAUDE_FORECAST_REBUILT_OUTCOMES_BY_READ = Store("spx_claude_forecast/seed/outcomes/{day}.jsonl")
CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BY_READ_TABLE = Store("spx_claude_forecast/library/rows.parquet")
CLAUDE_FORECAST_GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE = Store("spx_claude_forecast/library/scores.parquet")
CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BUILD_LATEST = Store("spx_claude_forecast/library/manifest.json")
CLAUDE_FORECAST_SCOREBOARD_LATEST = Store("spx_claude_forecast/scorecard.json")
CLAUDE_FORECAST_PAUSE_AND_LIMITS = Store("spx_claude_forecast/control.json")
CLAUDE_FORECAST_RECORDER_FOLDER = Store("spx_claude_forecast/recorder")              # the recorder's copies (the market/ rows above)

# =====================================================================================================================
# sndk/   the SNDK options scanner and its Claude reader
# =====================================================================================================================
SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN = Store("sndk_reversion/{day}.jsonl")
SNDK_PRICE_BARS_BY_MINUTE = Store("sndk_bars/{day}.jsonl")
SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN = Store("sndk_reads/{day}.jsonl")
SNDK_PRICE_BAR_FACTS_BY_READ = Store("sndk_side/{day}.jsonl")
SNDK_LAST_PRICES_BY_QUOTE = Store("sndk_tape/{day}.jsonl")
SNDK_OPTIONS_READER_NOTES_BY_READ = Store("sndk_rag/slices/{day}.jsonl")
SNDK_OPTIONS_CHAIN_STATE = Store("sndk_gex/chain_cache.json")
SNDK_OPTIONS_EXPIRIES_STATE = Store("sndk_gex/discovery.json")
SNDK_IMPLIED_VOLATILITY_STATE = Store("sndk_gex/vol_hint.json")
SNDK_OPTIONS_READER_REASONING_SWITCH = Store("sndk_reads/control.json")
SNDK_OPTIONS_READER_PERCENTILE_HISTORY_STATE = Store("sndk_reads/pctl_prior.json")
SNDK_PAST_VOLUME_CURVES_STATE = Store("sndk_reads/prior_volume.json")

# =====================================================================================================================
# screen_files/   files written only for a screen: the phone's cards and the desktop's 3D view
# =====================================================================================================================
JEV_LIVE_CARD = Store("{lane}/latest.json", lane="live")
JEV_OPENING_CARD = Store("{lane}/latest.json", lane="tape")
JEV_PREMARKET_CARD = Store("{lane}/latest.json", lane="premarket")
JEV_CARD_BY_LANE = {"live": JEV_LIVE_CARD, "tape": JEV_OPENING_CARD, "premarket": JEV_PREMARKET_CARD}
CLAUDE_FORECAST_CARD = Store("spx_claude_forecast/latest.json")
FORECASTER_SCOREBOARD_CARD = Store("spx_jev/mirai_prediction/scoreboard.json")
OPTIONS_FEED_STATUS_CARD = Store("spx_jev/mirai_prediction/options_data.json")
SPX_OPTIONS_TRADES_3D_VIEW_CARD = Store("flow/boards/{ticker}-{day}.json")          # the view bins other tickers the same way

# =====================================================================================================================
# operations/   logs, locks, health files and alert state
# =====================================================================================================================
STATION_RUNTIME_LOG = Store("logs/runtime-{day}.log")                                # env.sh's log(); runtime/watch names the folder
CLAUDE_FORECAST_JOB_RUNS_LOG = Store("spx_claude_forecast/run_logs/job_run_log.jsonl")
CLAUDE_FORECAST_READ_JOB_LOG = Store("spx_claude_forecast/run_logs/read_runner.out")
PREDICTION_JOB_RUNS_LOG = Store("spx_jev/mirai_prediction/run_logs/job_run_log.jsonl")
FORECASTER_FORECASTS_JOB_LOG = Store("spx_jev/mirai_prediction/run_logs/read_hook.out")
SPX_OPTIONS_TAPE_COLLECTOR_LOCK = Store("lob_flow/collector.lock")
SPX_OPTIONS_TAPE_COLLECTOR_FOLDER = Store("lob_flow")                                # the collector's folder: its disk use is measured here
CLAUDE_FORECAST_NIGHTLY_JOB_LOCK = Store("spx_claude_forecast/locks/nightly.lock")
CLAUDE_FORECAST_RECORDER_JOB_LOCK = Store("spx_claude_forecast/locks/recorder.lock")
CLAUDE_FORECAST_REBUILD_PAST_READS_JOB_LOCK = Store("spx_claude_forecast/locks/seed.lock")
CLAUDE_FORECAST_JOB_LOCK_BY_JOB = {"nightly": CLAUDE_FORECAST_NIGHTLY_JOB_LOCK, "recorder": CLAUDE_FORECAST_RECORDER_JOB_LOCK,
                                   "seed": CLAUDE_FORECAST_REBUILD_PAST_READS_JOB_LOCK}
SNDK_PRICE_BARS_HEALTH_LATEST = Store("sndk_bars/health.json")
TABLE_BUILD_COUNTS_BY_TABLE_AND_DAY = Store("spx_jev/store/validation")
TABLE_BUILD_REFUSED_ROWS_BY_EVENT = Store("spx_jev/store/quarantine")
FORECASTER_TABLE_REFUSED_ROWS_BY_EVENT = Store("spx_jev/mirai_prediction/tables/_checks/refused_rows/{day}.jsonl")
JEV_PREMARKET_OUTAGE_ALERT_STATE = Store("{lane}/deadman_state.json", lane="premarket")
SNDK_OUTAGE_ALERT_STATE = Store("sndk_reads/deadman_state.json")
PHONE_PUSHES_WALL_TIMERS_FEED_OUTAGES_STATE = Store("market_expectation/alerts_state.json")

# =====================================================================================================================
# Not in the approved map: paths live code still writes or reads, named here so no path is defined anywhere else.
# The report of this phase lists each one for the owner's decision.
# =====================================================================================================================
# The two Claude readers' memory beside their notes: a summary per session and the terrain they draw from the summaries.
# The map retires the files; the readers' CLIs still rebuild them on demand, so they are named until that code goes.
SPX_OPTIONS_SCANNER_CLAUDE_SESSION_SUMMARIES_BY_DAY = Store("spx_rag/summaries.jsonl")
SPX_OPTIONS_SCANNER_CLAUDE_TERRAIN_STATE = Store("spx_rag/terrain.json")
SNDK_OPTIONS_READER_SESSION_SUMMARIES_BY_DAY = Store("sndk_rag/summaries.jsonl")
SNDK_OPTIONS_READER_TERRAIN_STATE = Store("sndk_rag/terrain.json")
# The voice desk's own files: the open session and the day's spoken exchanges (the raw explorer never serves them).
VOICE_DESK_SESSION_STATE = Store("voice/session.json")
VOICE_DESK_TRANSCRIPTS_BY_DAY = Store("voice/convo-{day}.jsonl")
# book_flow's equity-book collector (shipped disabled, no launchd job): its depth snapshots, gap markers, health and lock.
EQUITY_BOOK_COLLECTOR_FOLDER = Store("flow")
EQUITY_BOOK_TICKER_FOLDER = Store("flow/{ticker}")
EQUITY_BOOK_DEPTH_SNAPSHOTS_BY_SLICE = Store("flow/{ticker}/{day}/book.jsonl")
EQUITY_BOOK_GAPS_BY_EVENT = Store("flow/{ticker}/{day}/gaps.jsonl")
EQUITY_BOOK_COLLECTOR_HEALTH_LATEST = Store("flow/health.json")
EQUITY_BOOK_COLLECTOR_LOCK = Store("flow/collector.lock")
# The Schwab sign-in's single-copy lock (cassandra_oauth.py), kept at the data root.
SCHWAB_SIGN_IN_LOCK = Store(".cassandra_oauth.lock")


def _register() -> dict[str, Store]:
    """Every Store above by its constant name, in definition order; names the stores and powers the by-name lookups."""
    stores: dict[str, Store] = {}
    for name, value in list(globals().items()):
        if isinstance(value, Store) and name.isupper():
            value.name = name
            stores[name] = value
    return stores


STORES = _register()


def store(name: str) -> Store:
    """The store called ``name`` (a constant above), or a KeyError naming the problem."""
    try:
        return STORES[name]
    except KeyError:
        raise KeyError(f"no store is called {name!r}") from None
