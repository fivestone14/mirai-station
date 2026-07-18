"""
siege_bridge.py — the ONLY coupling point between Mirai and the SIEGE box at
skills/siege (underlying SPY volume EFFORT at dealer-gamma wall touches).
Everything mirai-specific lives here: the sys.path shim, the state dir, the
SPX scope gate, and the one telemetry attach the 5-min tick makes. The
package itself never imports mirai code.

=========================== THE REVERSED SIGN =============================
Backtest sweep 2026-07 (n=168 touches, 11 of 13 days): HIGH volume at a
wall touch predicts the wall BREAKS (held 39% vs 54% for quiet touches).
SIEGE = expect break; QUIET = expect hold. See skills/siege/README.md.
===========================================================================

Shadow with one reader: the box writes to its own store (state/siege) and
this bridge attaches one "siege" diary field. Since wt-7 (2026-07-18) the
Watchtower's build_payload reads that field into siege_effort_at_walls —
the wording and the era bump live in watchtower.py, never here.

Kill switch: SIEGE_DISABLE=1 makes attach_telemetry a no-op.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parent
_SIEGE = str(SKILL_DIR.parent / "siege")
if _SIEGE not in sys.path:
    sys.path.insert(0, _SIEGE)

STATE_DIR = SKILL_DIR.parent.parent / "state" / "siege"


def _siege():
    """Import the box lazily so a missing package degrades to 'sensor absent'
    instead of breaking the scan import chain."""
    import siege
    return siege


def attach_telemetry(telemetry: dict, ticker: str, now: datetime, *,
                     spot: Optional[float], sigma, call_wall=None,
                     put_wall=None, magnet=None,
                     spy_bars: Optional[list] = None) -> None:
    """The ONE call the scan makes: run a siege scan on inputs the tick
    already has in hand (RAW SPY proxy bars + the gravity levels) and attach
    the compact record. Any failure leaves telemetry untouched — the sensor
    simply reads as absent for that scan. SPX-scoped: a manual scan of any
    other ticker must not turn the SPY tape into that ticker's evidence."""
    try:
        if os.environ.get("SIEGE_DISABLE") == "1":
            return
        box = _siege()
        if ticker != box.SiegeConfig().ticker:
            return
        towers = {k: v for k, v in ((box.CALL_WALL, call_wall),
                                    (box.PUT_WALL, put_wall),
                                    (box.MAGNET, magnet)) if v is not None}
        rec = box.scan(box.SiegeStore(STATE_DIR), now=now, spot=spot,
                       sigma=sigma, towers=towers, spy_bars=spy_bars or [])
        if rec:
            telemetry["siege"] = rec
    except Exception:
        pass


if __name__ == "__main__":
    import json
    box = _siege()
    latest = box.SiegeStore(STATE_DIR).root / "latest.json"
    try:
        print(json.dumps(json.loads(latest.read_text()), indent=2))
    except OSError:
        print("no siege latest.json yet")
