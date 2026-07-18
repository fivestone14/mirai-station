"""
siege — underlying (SPY) volume EFFORT at dealer-gamma walls. Shadow-only,
self-contained (imports nothing from the host).

THE REVERSED SIGN (backtest 2026-07, n=168 touches, 11/13 days): heavy
volume at a wall touch predicts the wall BREAKS (held 39% vs 54% for quiet
touches). SIEGE = expect break; QUIET = expect hold. See contracts.py.
"""
from siege.contracts import (BREAK, CALL_WALL, HOLD, MAGNET, NEUTRAL, PUT_WALL,
                             QUIET, SIEGE, TOWER_KINDS, UNRESOLVED, SiegeConfig)
from siege.engine import scan
from siege.store import SiegeStore

__all__ = ["SiegeConfig", "SiegeStore", "scan", "TOWER_KINDS", "CALL_WALL",
           "PUT_WALL", "MAGNET", "SIEGE", "QUIET", "NEUTRAL", "BREAK", "HOLD",
           "UNRESOLVED"]
