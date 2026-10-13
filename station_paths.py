"""Every data store of the station, defined once, under its final name, in its named folder.

Cleanup phase 1 (2026-10-11) named every store here; phase 7 (the same day) moved each store's VALUE to its folder in
the approved layout (``final_mapping_v4.md``); phase 8 gave the code one project home (``~/mirai_awakening``, every
folder named for what it holds) with the data root at ``data/``. Each store below is a ``Store`` whose NAME is the
store's final name from the map (``<subject>_<content>_by_<grain>``,
or a ``_latest`` / ``_card`` / ``_state`` / ``_log`` ending) and whose VALUE is where it now lives. The ``_TABLE`` ending
is this file's own rule, not the map's: a Parquet table and the raw folder it is built from share the map's name
(``tables/jev_answers_by_read_and_question/``), so the constant for the table takes ``_TABLE`` to tell the two apart.
When a folder moves again, only the templates in this file change; every reader and writer, and every page that asks
the viewstation for a store by name, follows.

The layout under the data root: ``market/`` (prices, quotes, depth, trades, open interest; its ``tables/``),
``spx/scanner/`` (the SPX options scanner, the order-flow collector's readings, the wall sensor), ``spx/jev/`` (the three
lanes, the shared archive, its ``tables/``), ``spx/prediction/`` (the base pool, the learner pool, the code questions'
answers, the forecasts and their ``tables/``), ``spx/claude_forecast/``, ``sndk/``, ``voice_desk/``, ``screen_files/``
(``phone/``, ``desktop/``: files written only for a screen), ``operations/`` (``logs/``, ``locks/``, ``health/``, the
alert states) and ``archive/`` (rollback copies and retired data).

The stores the map MERGED are one copy each since cleanup phase 5 (2026-10-12): the SPX minute bars are the bars
feed's day files alone (the grader's saved sessions folded in), the raw options tape is the collector's day folders
alone (compressed in place when a day is finished, never pruned; the recorder's gzip copies folded in), SPY's volume
per minute is one file a day under market/ (the wall sensor writes today's and reads its baseline back from them), the
open-interest book's history is written by the dated-book job itself, and the event calendar is one versioned file
under settings/. ``station_store_files.py`` reads and writes the shared ones one way for every pipeline.

How to import it
----------------
This file sits at the repo root, which is on nobody's ``sys.path`` by default: the jobs run as ``python -m watch...``
from ``alerts_and_phone_pushes/``, the pipelines from their own folders. Every consumer (the pipelines, the
collectors, the screens' server, the voice desk, the test suites) puts the repo root on ``sys.path`` first, by its
own position in the repo, then imports the module by name::

    import sys
    from pathlib import Path

    _REPO_ROOT = str(Path(__file__).resolve().parents[N])      # N: this file's depth below the repo root
    if _REPO_ROOT not in sys.path:
        sys.path.append(_REPO_ROOT)                                         # appended, so it shadows nothing
    import station_paths

A package does this once, in the module its other modules already import for paths (``spx_jev/__init__.py``,
``spx_claude_forecast/paths.py``, ``book_flow/cache.py``, ``desktop_and_phone_server/snapshot.py``,
``watch/paths.py``); a flat pipeline folder does it in each module that names a store. The module is pure standard
library and has no side effects when imported: it creates no folder and reads no file.

The data root
-------------
``state_root(explicit=None)`` is the one place the root is decided, in the order the station used before this file:
an explicit root (the ``--state-dir`` the launchd scripts pass to the spx-jev and Claude-forecast jobs), else
``MIRAI_STATE_DIR`` (the per-test override every suite sets, so it wins over the station-wide variable even in a
shell that sourced env.sh), else ``MIRAI_STATION_STATE`` (env.sh), else ``$MIRAI_STATION_ROOT/data``, else
``<repo>/data``. A consumer that resolved its root at import keeps doing so (``STATE_DIR = station_paths.state_root()``);
one that resolved per call keeps doing that too.

Keys
----
A template's ``{day}`` is ``YYYY-MM-DD``; ``{lane}`` is a JEV lane name (``live``, ``tape``, ``premarket``) and is
replaced by that lane's folder; ``{symbol}``, ``{ticker}``, ``{sha}``, ``{stamp}``, ``{sum_id}``, ``{horizon}``
and ``{stream}`` are the code's own keys. A store that is one of a keyed family (a pool horizon) names the allowed
values and what each becomes in the path; a family's members that the map names one by one are separate stores,
gathered in a ``_BY_...`` dict.

Three ways to a path. ``path(root, **keys)`` is the whole path from the data root. ``within(parent, folder, **keys)``
is the same file inside a folder the caller already holds, for the packages whose working root is a folder under the
data root (the prediction system's, the Claude forecast's, a JEV lane's, which a replay can point at a scratch
folder); ``in_lane_folder`` is its JEV spelling. ``file_name(**keys)`` is the last segment alone, for a reader that
holds the folder (the viewstation's tape folder, the fill ledger's folder). ``root_of(folder, **keys)`` goes the other
way: the data root a folder store's folder sits under, for code handed a lane's or a system's folder that must reach a
store outside it (the pools beside the lane, the cards under screen_files/).

Not defined here: rows the map marks DROP, and stores already retired (their writers are gone). A reader of a
DROP row keeps its own constant until phase 2 removes the writer.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent                # the project home, ~/mirai_awakening: this file sits at its root
STATE_FOLDER_NAME = "data"                                  # the data root's folder under the repo
DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def default_state_root() -> Path:
    """The root with no override: ``$MIRAI_STATION_ROOT/data`` when env.sh set the station root, else ``<repo>/data``."""
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


# The JEV lanes' folders under spx/jev/: each lane's own records, beside the shared archive and the tables.
JEV_LANE_FOLDERS = {"live": "spx/jev/live_lane", "tape": "spx/jev/opening_lane", "premarket": "spx/jev/premarket_lane"}


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
        """The store's folder relative to its root (``spx/scanner/spx_options_scanner_results_by_scan`` for
        ``spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl``), for the packages that keep a
        ``<root> / FOLDER / f"{day}.jsonl"`` habit over many call sites; the day-file shape is the map's own rule."""
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

    def root_of(self, folder: Path | str, **keys) -> Path | None:
        """The data root under which ``folder`` is this store's path (a folder store: a lane's folder, a system's
        folder), else None: the filled template stripped from the end of ``folder``, segment by segment, as given
        (no symlink is followed). Code handed such a folder uses it to reach a store that sits outside the folder."""
        wanted = Path(self._fill(self.relative, dict(keys))).parts
        parts = Path(folder).parts
        if len(parts) <= len(wanted) or parts[-len(wanted):] != wanted:
            return None
        return Path(*parts[:-len(wanted)])


# =====================================================================================================================
# market/   prices, quotes, depth, trades, open interest, the calendar, and the tables built from them
# =====================================================================================================================
SPX_PRICE_BARS_BY_MINUTE = Store("market/spx_price_bars_by_minute/{day}.jsonl")   # the one copy: the bars feed's; read through station_store_files
WATCHLIST_QUOTES_BY_MINUTE = Store("market/watchlist_quotes_by_minute/{day}.jsonl")
WATCHLIST_PRICE_BARS_BY_MINUTE = Store("market/watchlist_price_bars_by_minute/{day}.jsonl")
FUTURES_PRICE_BARS_BY_MINUTE = Store("market/futures_price_bars_by_minute/{day}.jsonl")
FUTURES_PRICE_BAR_CHANGE_COUNTS_BY_SAVE = Store("market/futures_price_bar_change_counts_by_save.jsonl")
FUTURES_CONTRACTS_AND_PAST_ROLLS_LATEST = Store("market/futures_contracts_and_past_rolls_latest.json")
SPX_VIX_RATES_PRICE_BARS_BY_DAY = Store("market/spx_vix_rates_price_bars_by_day/{symbol}.jsonl")
NEWS_HEADLINES_BY_ARTICLE = Store("market/news_headlines_by_article/{day}.jsonl")
NEWS_HEADLINES_SEEN_STATE = Store("market/news_headlines_seen_state.json")
# The collector's raw day folder holds the SPX same-day options trades (tape), their depth (sweeps) and the SPY depth
# stream with its gap markers, kept forever: a finished day's files are gzipped in place (``{stream}.jsonl.gz``) by the
# collector's next start and nothing prunes them (a later retention phase may). Every reader takes the plain file or
# its .gz, whichever is there.
SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER = Store("market/spx_same_day_options_raw_day_folders/{day}")
SPX_SAME_DAY_OPTIONS_AND_SPY_DEPTH_RAW_STREAM = Store("market/spx_same_day_options_raw_day_folders/{day}/{stream}.jsonl")   # stream: tape, sweeps, spy-depth
SPX_SAME_DAY_OPTIONS_TRADES_BY_TRADE = Store("market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl")
SPX_SAME_DAY_OPTIONS_DEPTH_BY_MINUTE = Store("market/spx_same_day_options_raw_day_folders/{day}/sweeps.jsonl")
# SPY volume per minute, one file a day: the wall sensor writes today's on every healthy scan and reads the newest 60
# back as its baseline (station_store_files reads and writes them for every pipeline).
SPY_VOLUME_BY_MINUTE = Store("market/spy_volume_by_minute/{day}.json")
# the open-interest book: the latest pull, overwritten on each, and the dated-book job's own copy of every pull beside it
SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST = Store("market/spx_monthly_quarterly_open_interest_latest.json")
SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY = Store("market/spx_monthly_quarterly_open_interest_by_day/{stamp}.json")   # stamp: station_store_files.open_interest_book_stamp(as_of)
SPX_PRICE_BARS_BY_5_MINUTES = Store("market/spx_price_bars_by_5_minutes/{day}.jsonl")
VIX_ONE_DAY_CLOSES_BY_DAY = Store("market/vix_one_day_closes_by_day.jsonl")
LEVERAGED_FUND_ASSETS_BY_RUN = Store("market/leveraged_fund_assets_by_run.jsonl")
SPX_LARGEST_STOCK_WEIGHTS_BY_UPDATE = Store("market/spx_largest_stock_weights_by_update.json")
# The event calendar: one hand-kept, versioned file in the repo (under settings/, not under data/), read by the tape
# collector and the scanner's event lens (the CPI and FOMC rows, with the days they compute by rule), by the SPX JEV
# package (every tier) and by the Claude forecast.
MARKET_EVENT_CALENDAR_BY_EVENT = Store("settings/market_event_calendar_by_event.json", base="repo")
SPX_PRICE_BARS_BY_MINUTE_TABLE = Store("market/tables/spx_price_bars_by_minute")
FUTURES_PRICE_BARS_BY_MINUTE_TABLE = Store("market/tables/futures_price_bars_by_minute")

# =====================================================================================================================
# spx/scanner/   the SPX options scanner (the left eye), the order-flow collector's readings and the wall sensor
# =====================================================================================================================
SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN = Store("spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl")
SPX_OPTIONS_SCANNER_GRADES_BY_DAY = Store("spx/scanner/spx_options_scanner_grades_by_day.jsonl")
SPX_ORDER_FLOW_READINGS_BY_MINUTE = Store("spx/scanner/spx_order_flow_readings_by_minute/{day}.jsonl")
SPX_ORDER_FLOW_GRADES_BY_DAY = Store("spx/scanner/spx_order_flow_grades_by_day/{day}.json")
SPX_OPTIONS_DEPTH_BASELINES_STATE = Store("spx/scanner/spx_options_depth_baselines_state.json")
SPY_DEPTH_BASELINES_STATE = Store("spx/scanner/spy_depth_baselines_state.json")
DEPTH_BASELINES_STATE_BY_ENGINE = {"lob_flow": SPX_OPTIONS_DEPTH_BASELINES_STATE, "spy_depth": SPY_DEPTH_BASELINES_STATE}
SPX_DEALER_WALL_TOUCHES_BY_EVENT = Store("spx/scanner/spx_dealer_wall_touches_by_event/{day}.jsonl")
SPX_DEALER_WALLS_LATEST = Store("spx/scanner/spx_dealer_walls_latest.json")
# The scanner's Claude second opinion keeps its memory beside its notes: a summary per session and the terrain it draws
# from them. The map retires the two files, but the reader's CLI still rebuilds them on demand, so they stay live stores.
SPX_OPTIONS_SCANNER_CLAUDE_NOTES_BY_READ = Store("spx/scanner/spx_options_scanner_claude_notes_by_read/{day}.jsonl")
SPX_OPTIONS_SCANNER_CLAUDE_SESSION_SUMMARIES_BY_DAY = Store("spx/scanner/spx_options_scanner_claude_session_summaries_by_day.jsonl")
SPX_OPTIONS_SCANNER_CLAUDE_TERRAIN_STATE = Store("spx/scanner/spx_options_scanner_claude_terrain_state.json")
SPX_OPTIONS_SCANNER_SIGNAL_CHECKS_BY_SCAN = Store("spx/scanner/spx_options_scanner_signal_checks_by_scan/{day}.jsonl")
SPX_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE = Store("spx/scanner/spx_options_strike_volume_weights_state.json")
SPY_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE = Store("spx/scanner/spy_options_strike_volume_weights_state.json")
# the fill ledger's two files by the ledger id the scanner stamps them under (native_<ticker>, proxy_<ticker>)
OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE_BY_LEDGER = {"native_SPX": SPX_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE,
                                                 "proxy_SPY": SPY_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE}
SPX_OPTIONS_VOLUME_LAST_SCAN_STATE = Store("spx/scanner/spx_options_volume_last_scan_state.json")
OPTIONS_VOLUME_LAST_SCAN_STATE_BY_TICKER = {"SPX": SPX_OPTIONS_VOLUME_LAST_SCAN_STATE}   # the scanner scans SPX alone
SPX_DEALER_WALL_TOUCHES_TODAY_STATE = Store("spx/scanner/spx_dealer_wall_touches_today_state.json")
SPX_OPTIONS_TRADES_RESUME_POINT_STATE = Store("spx/scanner/spx_options_trades_resume_point_state.json")
SPX_ORDER_FLOW_READINGS_LATEST = Store("spx/scanner/spx_order_flow_readings_latest.json")
SPX_DEALER_GAMMA_LEVELS_LATEST = Store("spx/scanner/spx_dealer_gamma_levels_latest.json")
SPX_OPTIONS_SCANNER_SIGNALS_SENT_STATE = Store("spx/scanner/spx_options_scanner_signals_sent_state.json")

# =====================================================================================================================
# spx/jev/   the JEV question pipeline: its three lanes, the shared archive and its Parquet tables
# =====================================================================================================================
JEV_LANE_FOLDER = Store("{lane}")
JEV_READS_AND_GRADES_BY_EVENT = Store("spx/jev/jev_reads_and_grades_by_event/{day}.jsonl")
JEV_ANSWERS_BY_READ = Store("{lane}/jev_answers_by_read/{day}.jsonl")
JEV_AND_POOL_FORECASTS_BY_READ = Store("{lane}/jev_and_pool_forecasts_by_read/{day}.jsonl")
JEV_END_PRICE_GRADES_BY_READ = Store("{lane}/jev_end_price_grades_by_read.jsonl")
JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON = Store("{lane}/jev_average_price_grades_by_read_and_horizon.jsonl")
JEV_AVERAGE_PRICE_GRADES_LOCK = Store("{lane}/jev_average_price_grades_by_read_and_horizon.jsonl.lock")
JEV_QUESTION_WEIGHTS_STATE = Store("{lane}/jev_question_weights_state.json")
JEV_LAST_ANSWERS_STATE = Store("{lane}/jev_last_answers_state.json")
JEV_QUESTION_ASK_SCHEDULE_STATE = Store("{lane}/jev_question_ask_schedule_state.json", lane="live")
HISTORICAL_ODDS_END_PRICE_DAYS_STATE = Store("{lane}/historical_odds_end_price_days_state.json", lane="live")
HISTORICAL_ODDS_AVERAGE_PRICE_DAYS_STATE = Store("{lane}/historical_odds_average_price_days_state.json", lane="live")
JEV_TABLES_FOLDER = Store("spx/jev/tables")                                          # the four JEV tables below
JEV_READ_SUMMARIES_BY_READ_TABLE = Store("spx/jev/tables/jev_read_summaries_by_read")
JEV_ANSWERS_BY_READ_AND_QUESTION_TABLE = Store("spx/jev/tables/jev_answers_by_read_and_question")
JEV_AND_POOL_FORECASTS_BY_READ_AND_HORIZON_TABLE = Store("spx/jev/tables/jev_and_pool_forecasts_by_read_and_horizon")
JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON_TABLE = Store("spx/jev/tables/jev_average_price_grades_by_read_and_horizon")

# =====================================================================================================================
# spx/prediction/   the base pool (pool 1), the learner pool (pool 2), the code questions and the forecaster tables
# =====================================================================================================================
PREDICTION_FOLDER = Store("spx/prediction")                                          # the prediction system's folder: every store below but its rollback copies
# The base pool's state, one file per horizon it learns: the live lane's two end-price horizons and its average-price
# one (the station's pools), plus the 10-minute horizons of the opening and pre-market lanes, which the loop learns only
# when it is run on one of those lanes by hand, into a base_pool_state folder inside that lane's own folder (base_pool.py).
BASE_POOL_STATE = Store("spx/prediction/base_pool/base_pool_state/{horizon}.json",
                        horizon={h: h for h in ("next_10_minutes", "next_30_minutes", "next_60_minutes",
                                                "average_of_next_10_minutes", "average_of_next_30_minutes")})
BASE_POOL_UPDATES_BY_DAY_AND_HORIZON = Store("spx/prediction/base_pool/base_pool_updates_by_day_and_horizon/{horizon}.jsonl",
                                             horizon={"average_of_next_30_minutes": "average_of_next_30_minutes"})
BASE_POOL_STATE_ROLLBACK_COPIES = Store("archive/base_pool_state_rollback_copies")   # <state file>.pre-<constants hash>-<day> copies
LEARNER_POOL_SUM_IDS = {"next_60": "next_60_minutes", "average_30": "average_of_next_30_minutes"}   # the code's sum ids -> the horizon words
LEARNER_POOL_STATE = Store("spx/prediction/learner_pool/learner_pool_state/{sum_id}.json", sum_id=LEARNER_POOL_SUM_IDS)
LEARNER_POOL_TRAINED_FORECASTERS_BY_DAY = Store("spx/prediction/learner_pool/learner_pool_trained_forecasters_by_day/{day}_{sum_id}.json",
                                                sum_id=LEARNER_POOL_SUM_IDS)
LEARNER_POOL_ROLLBACK_COPIES = Store("archive/learner_pool_rollback_copies")         # <file>.<UTC stamp> copies and replay_force_<stamp>/
CODE_QUESTION_ANSWERS_BY_READ = Store("spx/prediction/code_question_answers_by_read/{day}.jsonl")
FORECASTS_BY_FORECASTER_READ_AND_HORIZON = Store("spx/prediction/forecasts_by_forecaster_read_and_horizon/{day}.jsonl")
FORECASTS_BY_FORECASTER_READ_AND_HORIZON_TABLE = Store("spx/prediction/tables/forecasts_by_forecaster_read_and_horizon")
GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE = Store("spx/prediction/tables/grades_by_forecaster_read_and_horizon")
# The direction line (mirai_prediction/direction_line.py, 2026-10-13): the separate SPX line that answers only "if it
# moves, which way?" for the 30-minute average-price box, its forecasters weighted by points. Its calls and grades are
# day files; its weights are one state file per sum it runs on (the average-price box alone).
DIRECTION_LINE_SUM_IDS = {"average_30": "average_of_next_30_minutes"}
DIRECTION_LINE_CALLS_BY_READ = Store("spx/prediction/direction_line/direction_line_calls_by_read/{day}.jsonl")
DIRECTION_LINE_GRADES_BY_READ = Store("spx/prediction/direction_line/direction_line_grades_by_read/{day}.jsonl")
DIRECTION_LINE_WEIGHTS_STATE = Store("spx/prediction/direction_line/direction_line_weights_state/{sum_id}.json", sum_id=DIRECTION_LINE_SUM_IDS)

# =====================================================================================================================
# spx/claude_forecast/   Claude's own SPX forecast
# =====================================================================================================================
CLAUDE_FORECAST_FOLDER = Store("spx/claude_forecast")                                # the package's own folder: the record, the tables, the rulebooks
CLAUDE_FORECAST_INPUTS_BY_READ = Store("spx/claude_forecast/claude_forecast_inputs_by_read/{day}.jsonl")
CLAUDE_FORECAST_ANSWERS_BY_READ = Store("spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl")
CLAUDE_FORECAST_OUTCOMES_BY_READ = Store("spx/claude_forecast/claude_forecast_outcomes_by_read/{day}.jsonl")
CLAUDE_FORECAST_TEST_ANSWERS_BY_VARIANT_AND_READ = Store("spx/claude_forecast/claude_forecast_test_answers_by_variant_and_read/{day}.jsonl")
CLAUDE_FORECAST_RULEBOOKS_BY_VERSION = Store("spx/claude_forecast/claude_forecast_rulebooks_by_version/{sha}.txt")
CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ = Store("spx/claude_forecast/claude_forecast_rebuilt_inputs_by_read/{day}.jsonl")
CLAUDE_FORECAST_REBUILT_OUTCOMES_BY_READ = Store("spx/claude_forecast/claude_forecast_rebuilt_outcomes_by_read/{day}.jsonl")
CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BY_READ_TABLE = Store("spx/claude_forecast/tables/claude_forecast_fingerprints_and_outcomes_by_read.parquet")
CLAUDE_FORECAST_GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE = Store("spx/claude_forecast/tables/claude_forecast_grades_by_forecaster_read_and_horizon.parquet")
CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BUILD_LATEST = Store("spx/claude_forecast/tables/claude_forecast_fingerprints_and_outcomes_build_latest.json")
CLAUDE_FORECAST_SCOREBOARD_LATEST = Store("spx/claude_forecast/claude_forecast_scoreboard_latest.json")
CLAUDE_FORECAST_PAUSE_AND_LIMITS = Store("spx/claude_forecast/claude_forecast_pause_and_limits.json")

# =====================================================================================================================
# sndk/   the SNDK options scanner and its Claude reader
# =====================================================================================================================
SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN = Store("sndk/sndk_options_scanner_results_by_scan/{day}.jsonl")
SNDK_PRICE_BARS_BY_MINUTE = Store("sndk/sndk_price_bars_by_minute/{day}.jsonl")
SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN = Store("sndk/sndk_options_reader_gates_and_answers_by_run/{day}.jsonl")
SNDK_PRICE_BAR_FACTS_BY_READ = Store("sndk/sndk_price_bar_facts_by_read/{day}.jsonl")
SNDK_LAST_PRICES_BY_QUOTE = Store("sndk/sndk_last_prices_by_quote/{day}.jsonl")
# The reader's memory beside its notes: a summary per session and the terrain drawn from them, rebuilt on demand by its
# CLI (the map retires the two files; they stay live stores while that code runs).
SNDK_OPTIONS_READER_NOTES_BY_READ = Store("sndk/sndk_options_reader_notes_by_read/{day}.jsonl")
SNDK_OPTIONS_READER_SESSION_SUMMARIES_BY_DAY = Store("sndk/sndk_options_reader_session_summaries_by_day.jsonl")
SNDK_OPTIONS_READER_TERRAIN_STATE = Store("sndk/sndk_options_reader_terrain_state.json")
SNDK_OPTIONS_CHAIN_STATE = Store("sndk/sndk_options_chain_state.json")
SNDK_OPTIONS_EXPIRIES_STATE = Store("sndk/sndk_options_expiries_state.json")
SNDK_IMPLIED_VOLATILITY_STATE = Store("sndk/sndk_implied_volatility_state.json")
SNDK_OPTIONS_READER_REASONING_SWITCH = Store("sndk/sndk_options_reader_reasoning_switch.json")
SNDK_OPTIONS_READER_PERCENTILE_HISTORY_STATE = Store("sndk/sndk_options_reader_percentile_history_state.json")
SNDK_PAST_VOLUME_CURVES_STATE = Store("sndk/sndk_past_volume_curves_state.json")

# =====================================================================================================================
# voice_desk/   the spoken SNDK desk's own files (the owner's default: the desk still runs; keep or retire is open)
# =====================================================================================================================
VOICE_DESK_SESSION_STATE = Store("voice_desk/voice_desk_session_state.json")        # the open agent session's id and day
VOICE_DESK_TRANSCRIPTS_BY_EXCHANGE = Store("voice_desk/voice_desk_transcripts_by_exchange/{day}.jsonl")   # one row per spoken exchange; the raw explorer never serves them

# =====================================================================================================================
# screen_files/   files written only for a screen: the phone's cards and the desktop's 3D view
# =====================================================================================================================
JEV_LIVE_CARD = Store("screen_files/phone/jev_live_card.json")
JEV_OPENING_CARD = Store("screen_files/phone/jev_opening_card.json")
JEV_PREMARKET_CARD = Store("screen_files/phone/jev_premarket_card.json")
JEV_CARD_BY_LANE = {"live": JEV_LIVE_CARD, "tape": JEV_OPENING_CARD, "premarket": JEV_PREMARKET_CARD}
CLAUDE_FORECAST_CARD = Store("screen_files/phone/claude_forecast_card.json")
FORECASTER_SCOREBOARD_CARD = Store("screen_files/phone/forecaster_scoreboard_card.json")
DIRECTION_LINE_CARD = Store("screen_files/phone/direction_line_card.json")
OPTIONS_FEED_STATUS_CARD = Store("screen_files/phone/options_feed_status_card.json")
SPX_OPTIONS_TRADES_3D_VIEW_CARD = Store("screen_files/desktop/spx_options_trades_3d_view_card/{day}.json")   # a rebuildable cache of one day's board

# =====================================================================================================================
# operations/   logs, locks, health files and alert state
# =====================================================================================================================
STATION_RUNTIME_LOG = Store("operations/logs/station_runtime_log/{day}.log")        # env.sh's log(); alerts_and_phone_pushes/watch names the folder
CLAUDE_FORECAST_JOB_RUNS_LOG = Store("operations/logs/claude_forecast_job_runs_log.jsonl")
CLAUDE_FORECAST_READ_JOB_LOG = Store("operations/logs/claude_forecast_read_job_log.txt")
PREDICTION_JOB_RUNS_LOG = Store("operations/logs/prediction_job_runs_log.jsonl")
FORECASTER_FORECASTS_JOB_LOG = Store("operations/logs/forecaster_forecasts_job_log.txt")
# What each launchd job prints, captured by scheduled_jobs/job_start_scripts/start_job_with_logs.sh (launchd itself
# expands nothing in a plist path): {job} is the launchd Label without its com.mirai-station. prefix, hyphens written
# as underscores (spx-jev-tape -> spx_jev_tape). Appended to on every run; nothing rotates them.
LAUNCHD_JOB_OUTPUT_LOG = Store("operations/logs/{job}_output_log.txt")
LAUNCHD_JOB_ERRORS_LOG = Store("operations/logs/{job}_errors_log.txt")
SPX_OPTIONS_TAPE_COLLECTOR_LOCK = Store("operations/locks/spx_options_tape_collector.lock")
CLAUDE_FORECAST_NIGHTLY_JOB_LOCK = Store("operations/locks/claude_forecast_nightly_job.lock")
CLAUDE_FORECAST_RECORDER_JOB_LOCK = Store("operations/locks/claude_forecast_recorder_job.lock")
CLAUDE_FORECAST_REBUILD_PAST_READS_JOB_LOCK = Store("operations/locks/claude_forecast_rebuild_past_reads_job.lock")
CLAUDE_FORECAST_JOB_LOCK_BY_JOB = {"nightly": CLAUDE_FORECAST_NIGHTLY_JOB_LOCK, "recorder": CLAUDE_FORECAST_RECORDER_JOB_LOCK,
                                   "seed": CLAUDE_FORECAST_REBUILD_PAST_READS_JOB_LOCK}
SCHWAB_SIGN_IN_LOCK = Store("operations/locks/schwab_sign_in.lock")                  # the Schwab sign-in's single-copy lock (cassandra_oauth.py)
FUTURES_PRICE_BARS_SAVE_LOCK = Store("operations/locks/futures_price_bars_save.lock")   # the overnight saver's single-copy lock
NEWS_HEADLINES_POLL_LOCK = Store("operations/locks/news_headlines_poll.lock")       # the headline poller's single-copy lock
DIRECTION_LINE_JOB_LOCK = Store("operations/locks/direction_line_job.lock")         # held while the direction line grades, learns and calls
SNDK_PRICE_BARS_HEALTH_LATEST = Store("operations/health/sndk_price_bars_health_latest.json")
TABLE_BUILD_COUNTS_BY_TABLE_AND_DAY = Store("operations/health/table_build_counts_by_table_and_day")
TABLE_BUILD_REFUSED_ROWS_BY_EVENT = Store("operations/health/table_build_refused_rows_by_event")
FORECASTER_TABLE_REFUSED_ROWS_BY_EVENT = Store("operations/health/forecaster_table_refused_rows_by_event/{day}.jsonl")
JEV_PREMARKET_OUTAGE_ALERT_STATE = Store("operations/jev_premarket_outage_alert_state.json")
SNDK_OUTAGE_ALERT_STATE = Store("operations/sndk_outage_alert_state.json")
PHONE_PUSHES_WALL_TIMERS_FEED_OUTAGES_STATE = Store("operations/phone_pushes_wall_timers_feed_outages_state.json")


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
