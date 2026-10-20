"""
spx_price_writer.py — saves the stream's latest $SPX trade to
``market/spx_live_price_latest.json`` (``station_paths.SPX_LIVE_PRICE_LATEST``)
so a reader outside the stream's process (Kaze's phone page) sees the price
within a second or two of the trade.

    {"price": 6712.43, "at": "<trade time, ET ISO>", "written_at": "<ET ISO>"}

At most one write a second (the tick that arrives a second after the last write
carries the newest price), each one atomic (a tmp file, then ``os.replace``), so a
reader never sees half a file. The writer is only ever called from inside the
stream: it starts no process and no job of its own. A failed write never reaches
the stream — it is caught here and printed at most once a minute.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

_REPO_ROOT = str(Path(__file__).resolve().parents[2])             # station_paths.py names every store
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402

ET = ZoneInfo("America/New_York")
MIN_WRITE_GAP_S = 1.0            # at most one write a second
ERROR_PRINT_GAP_S = 60.0         # a failing disk prints once a minute, not once a tick


class SpxPriceWriter:
    """The stream's on_index_tick listener: save(price, trade_time_iso) keeps
    the newest trade and writes it to the live-price file at most once a second."""

    def __init__(self, state_root: Path | str | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.path = station_paths.SPX_LIVE_PRICE_LATEST.path(station_paths.state_root(state_root))
        self._clock = clock
        self._last_write: Optional[float] = None
        self._last_error_print: Optional[float] = None

    def save(self, price: float, trade_time_iso: str) -> None:
        """Write the trade if a second has passed since the last write; never raises."""
        try:
            now = self._clock()
            if self._last_write is not None and now - self._last_write < MIN_WRITE_GAP_S:
                return
            self._write({"price": float(price), "at": trade_time_iso,
                         "written_at": datetime.now(tz=ET).isoformat()})
            self._last_write = now
        except Exception as e:  # noqa: BLE001 — the stream must outlive any disk trouble
            self._print_error(e)

    def _write(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(row))
        os.replace(tmp, self.path)

    def _print_error(self, e: Exception) -> None:
        try:
            now = self._clock()
            if self._last_error_print is None or now - self._last_error_print >= ERROR_PRINT_GAP_S:
                self._last_error_print = now
                print(f"spx_price_writer: write failed: {type(e).__name__}: {e}", file=sys.stderr)
        except Exception:  # noqa: BLE001 — not even the log line may break the stream
            pass
