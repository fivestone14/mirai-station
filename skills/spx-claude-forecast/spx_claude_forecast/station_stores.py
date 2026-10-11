"""Where the station's own stores are, read-only, and how to reach the spx_jev code that reads them.

Every path here is READ, and each is named in runtime/station_paths.py. Nothing in this package writes under any of
them; ``jsonl_store`` refuses to.

    state/reversion/{day}.jsonl              the SPX options diary (the left-eye scanner's rows)
    state/reversion/bars/{day}-SPX.json      the saved full session of SPX 1-minute bars
    state/spx_jev/bars/{day}.jsonl           today's live SPX 1-minute bars
    state/spx_jev/context/{day}.jsonl        market quotes every ~60 s ($VIX, $VIX1D, /ES, breadth ...)
    state/spx_jev/daily_closes/{sym}.jsonl   daily OHLC per symbol, rewritten whole at 16:20 ET
    state/spx_jev/archive/{day}.jsonl        the SPX read records (kind "read") and their grades
    state/spx_jev/integral_grades.jsonl      the station's average-price grades per read and horizon
    state/spx_jev/mirai_prediction/raw/code_features/{day}.jsonl   code's 79 answers per read
    state/lob_flow/agg/{day}.jsonl           the 0DTE options tape, folded per minute (never deleted)
    state/lob_flow/raw/{day}/                the raw tape (deleted after 30 days; the recorder copies it)
    state/siege/baseline.json                SPY per-minute volume per day (keeps 60 days; the recorder copies it)
    state/dated_gex/book.json                the dated options book (overwritten on every fetch; the recorder copies it)
    skills/spx-jev/calendar/events.json      the event calendar
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from .paths import STATION_ROOT, station_paths

SPX_JEV_SKILL_DIR = STATION_ROOT / "skills" / "spx-jev"


def reversion_rows_file(state_dir: Path, day: str) -> Path:
    return station_paths.SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN.path(state_dir, day=day)


def session_bars_dir(state_dir: Path) -> Path:
    """The saved full sessions of SPX bars, beside the diary (a duplicate of the bars feed; the map drops it)."""
    return station_paths.SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN.folder(state_dir) / "bars"


def spx_jev_dir(state_dir: Path) -> Path:
    return station_paths.JEV_LANE_FOLDER.path(state_dir, lane="live")


def live_hour_file(state_dir: Path, day: str) -> Path:
    """The live lane's sums for the day (one record per read)."""
    return station_paths.JEV_AND_POOL_FORECASTS_BY_READ.path(state_dir, lane="live", day=day)


def context_file(state_dir: Path, day: str) -> Path:
    return station_paths.WATCHLIST_QUOTES_BY_MINUTE.path(state_dir, day=day)


def context_days(state_dir: Path) -> list[str]:
    folder = station_paths.WATCHLIST_QUOTES_BY_MINUTE.folder(state_dir)
    return sorted(p.name[:10] for p in folder.glob("20??-??-??.jsonl")) if folder.exists() else []


def daily_closes_file(state_dir: Path, symbol: str) -> Path:
    return station_paths.SPX_VIX_RATES_PRICE_BARS_BY_DAY.path(state_dir, symbol=symbol)


def integral_grades_file(state_dir: Path) -> Path:
    return station_paths.JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON.path(state_dir, lane="live")


def lob_flow_agg_file(state_dir: Path, day: str) -> Path:
    return station_paths.SPX_ORDER_FLOW_READINGS_BY_MINUTE.path(state_dir, day=day)


def lob_flow_raw_dir(state_dir: Path) -> Path:
    return station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.folder(state_dir)


def siege_baseline_file(state_dir: Path) -> Path:
    return station_paths.SPY_VOLUME_BY_MINUTE_ROLLING_BASELINE_STATE.path(state_dir)


def dated_book_file(state_dir: Path) -> Path:
    return station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST.path(state_dir)


def events_calendar_file() -> Path:
    return station_paths.MARKET_EVENT_CALENDAR_BY_EVENT_SPX_JEV_COPY.path(STATION_ROOT)


def import_spx_jev(module: str) -> ModuleType:
    """Import ``spx_jev.<module>`` from the sibling skill, which is not installed anywhere: the house
    pattern is a lazy sys.path insert at the point of use (sessions.py does the same for runtime/)."""
    if str(SPX_JEV_SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SPX_JEV_SKILL_DIR))
    return importlib.import_module(f"spx_jev.{module}")
