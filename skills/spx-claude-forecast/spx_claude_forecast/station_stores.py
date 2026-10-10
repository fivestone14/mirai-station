"""Where the station's own stores are, read-only, and how to reach the spx_jev code that reads them.

Every path here is READ. Nothing in this package writes under any of them; ``jsonl_store`` refuses to.

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

from .paths import STATION_ROOT

SPX_JEV_SKILL_DIR = STATION_ROOT / "skills" / "spx-jev"


def reversion_rows_file(state_dir: Path, day: str) -> Path:
    return state_dir / "reversion" / f"{day}.jsonl"


def reversion_bars_file(state_dir: Path, day: str) -> Path:
    return state_dir / "reversion" / "bars" / f"{day}-SPX.json"


def spx_jev_dir(state_dir: Path) -> Path:
    return state_dir / "spx_jev"


def live_bars_file(state_dir: Path, day: str) -> Path:
    return spx_jev_dir(state_dir) / "bars" / f"{day}.jsonl"


def context_file(state_dir: Path, day: str) -> Path:
    return spx_jev_dir(state_dir) / "context" / f"{day}.jsonl"


def context_days(state_dir: Path) -> list[str]:
    folder = spx_jev_dir(state_dir) / "context"
    return sorted(p.name[:10] for p in folder.glob("20??-??-??.jsonl")) if folder.exists() else []


def daily_closes_file(state_dir: Path, symbol: str) -> Path:
    return spx_jev_dir(state_dir) / "daily_closes" / f"{symbol}.jsonl"


def archive_file(state_dir: Path, day: str) -> Path:
    return spx_jev_dir(state_dir) / "archive" / f"{day}.jsonl"


def integral_grades_file(state_dir: Path) -> Path:
    return spx_jev_dir(state_dir) / "integral_grades.jsonl"


def code_features_file(state_dir: Path, day: str) -> Path:
    return spx_jev_dir(state_dir) / "mirai_prediction" / "raw" / "code_features" / f"{day}.jsonl"


def lob_flow_agg_file(state_dir: Path, day: str) -> Path:
    return state_dir / "lob_flow" / "agg" / f"{day}.jsonl"


def lob_flow_raw_dir(state_dir: Path) -> Path:
    return state_dir / "lob_flow" / "raw"


def siege_baseline_file(state_dir: Path) -> Path:
    return state_dir / "siege" / "baseline.json"


def dated_book_file(state_dir: Path) -> Path:
    return state_dir / "dated_gex" / "book.json"


def events_calendar_file() -> Path:
    return SPX_JEV_SKILL_DIR / "calendar" / "events.json"


def import_spx_jev(module: str) -> ModuleType:
    """Import ``spx_jev.<module>`` from the sibling skill, which is not installed anywhere: the house
    pattern is a lazy sys.path insert at the point of use (sessions.py does the same for runtime/)."""
    if str(SPX_JEV_SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SPX_JEV_SKILL_DIR))
    return importlib.import_module(f"spx_jev.{module}")
