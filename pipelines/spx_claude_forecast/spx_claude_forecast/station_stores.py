"""Where the station's own stores are, read-only, and how to reach the spx_jev code that reads them.

Every path here is READ, and each is named in station_paths.py (the names below). Nothing in this package
writes under any of them; ``jsonl_store`` refuses to.

    SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN            the SPX options diary (the left-eye scanner's rows)
    SPX_PRICE_BARS_BY_MINUTE                       the SPX 1-minute bars, one file a day (the one store; station_store_files reads it)
    WATCHLIST_QUOTES_BY_MINUTE                     market quotes every ~60 s ($VIX, $VIX1D, /ES, breadth ...)
    SPX_VIX_RATES_PRICE_BARS_BY_DAY                daily OHLC per symbol, rewritten whole at 16:20 ET
    JEV_READS_AND_GRADES_BY_EVENT                  the SPX read records (kind "read") and their grades
    JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON   the station's average-price grades per read and horizon (the live lane's)
    CODE_QUESTION_ANSWERS_BY_READ                  code's 79 answers per read
    SPX_ORDER_FLOW_READINGS_BY_MINUTE              the 0DTE options tape, folded per minute (never deleted)
    SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER            the raw tape, one folder a day, kept forever (finished days gzipped in place)
    SPY_VOLUME_BY_MINUTE                           SPY's volume per minute, one file a day (the wall sensor's; station_store_files reads it)
    SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST     the dated options book, overwritten on every fetch
    SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY     every book pulled, kept by the dated-book job (paths.open_interest_history_folder)
    MARKET_EVENT_CALENDAR_BY_EVENT                 the station's one event calendar, versioned in the repo under settings/
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from .paths import STATION_ROOT, station_paths, station_store_files

SPX_JEV_SKILL_DIR = STATION_ROOT / "pipelines" / "spx_jev_questions_and_pools"


def reversion_rows_file(state_dir: Path, day: str) -> Path:
    return station_paths.SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN.path(state_dir, day=day)


def live_hour_file(state_dir: Path, day: str) -> Path:
    """The live lane's sums for the day (one record per read)."""
    return station_paths.JEV_AND_POOL_FORECASTS_BY_READ.path(state_dir, lane="live", day=day)


def context_file(state_dir: Path, day: str) -> Path:
    return station_paths.WATCHLIST_QUOTES_BY_MINUTE.path(state_dir, day=day)


def context_days(state_dir: Path) -> list[str]:
    return station_store_files.day_file_days(station_paths.WATCHLIST_QUOTES_BY_MINUTE.folder(state_dir))   # plain or gzipped


def daily_closes_file(state_dir: Path, symbol: str) -> Path:
    return station_paths.SPX_VIX_RATES_PRICE_BARS_BY_DAY.path(state_dir, symbol=symbol)


def integral_grades_file(state_dir: Path) -> Path:
    return station_paths.JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON.path(state_dir, lane="live")


def lob_flow_agg_file(state_dir: Path, day: str) -> Path:
    return station_paths.SPX_ORDER_FLOW_READINGS_BY_MINUTE.path(state_dir, day=day)


def dated_book_file(state_dir: Path) -> Path:
    return station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST.path(state_dir)


def events_calendar_file() -> Path:
    return station_paths.MARKET_EVENT_CALENDAR_BY_EVENT.path()


def import_spx_jev(module: str) -> ModuleType:
    """Import ``spx_jev.<module>`` from the sibling skill, which is not installed anywhere: the house
    pattern is a lazy sys.path insert at the point of use (sessions.py does the same for the watch package)."""
    if str(SPX_JEV_SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SPX_JEV_SKILL_DIR))
    return importlib.import_module(f"spx_jev.{module}")
