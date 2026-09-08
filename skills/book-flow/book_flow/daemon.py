"""
daemon.py — the COLLECTOR: the only long-lived process, and the only place in
this package that touches a network.

SHIPPED DISABLED. `ENABLED` is False and no launchd job is loaded, because
starting this takes the single Schwab streamer connection away from the
lob-flow SPX collector, which would drop that box from `stream+mcp` to
mcp-only and silence its SPY control. Turning this on is a decision with a
consequence elsewhere, so it is a deliberate act, never a side effect of
installing a file.

Posture, inherited from the lob-flow box because it has been right in
production for a month:
  * one instance, guaranteed by a flock — two collectors on one login fight
    over the connection and each kills the other;
  * the supervisor tick re-runs every minute, so a crash self-heals;
  * rows hit the journal before anything derived is computed, so a crash can
    re-read but never lose;
  * a gap is MARKED, never interpolated.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STATE = PLUGIN_ROOT / "state" / "flow"

ENABLED = False                 # see the module docstring before flipping this
FLUSH_ROWS = 2000               # rows buffered before a write; ~30s at 1 Hz
HEALTH_S = 30


def rth_is_live(now: Optional[datetime] = None) -> bool:
    """Deliberately simple and local: this package must not import the host's
    market calendar. The launchd runner already gates on the host's own
    authoritative check, so this is only a second belt."""
    now = now or datetime.now(tz=ET)
    if now.weekday() > 4:
        return False
    return dtime(9, 25) <= now.time() <= dtime(16, 5)


class _Journal:
    """One append-only file per ticker per day. Buffered, flushed on size and
    on close, fsync left to the OS — a lost tail on a hard crash costs seconds
    of a session that is being recorded precisely because it is replayable."""

    def __init__(self, root: Path):
        self.root = root
        self._fh = None
        self._day = None
        self._sym = None
        self._buf: list[str] = []
        self.written = 0

    def _open(self, sym: str, day: str):
        if self._fh is not None:
            self.flush()
            self._fh.close()
        path = self.root / sym / day
        path.mkdir(parents=True, exist_ok=True)
        self._fh = (path / "book.jsonl").open("a")
        self._sym, self._day = sym, day

    def add(self, row: dict, now: datetime) -> None:
        day = now.date().isoformat()
        sym = row["y"]
        if sym != self._sym or day != self._day:
            self._open(sym, day)
        self._buf.append(json.dumps(row, separators=(",", ":")))
        if len(self._buf) >= FLUSH_ROWS:
            self.flush()

    def flush(self) -> None:
        if self._fh and self._buf:
            self._fh.write("\n".join(self._buf) + "\n")
            self._fh.flush()
            self.written += len(self._buf)
            self._buf.clear()

    def close(self) -> None:
        self.flush()
        if self._fh:
            self._fh.close()
            self._fh = None


def mark_gap(root: Path, sym: str, now: datetime, why: str) -> None:
    """A gap is recorded, never smoothed over. The board differences
    consecutive observations, so a silent hole would surface later as one
    enormous fake add at whatever price reappeared first."""
    try:
        p = root / sym / now.date().isoformat()
        p.mkdir(parents=True, exist_ok=True)
        with (p / "gaps.jsonl").open("a") as fh:
            fh.write(json.dumps({"ts": now.isoformat(), "why": why}) + "\n")
    except Exception:
        pass


async def run(client_factory: Callable[[], object],
              specs: Optional[Sequence] = None,
              root: Path = STATE,
              is_live: Callable[[], bool] = rth_is_live,
              clock: Callable[[], datetime] = lambda: datetime.now(tz=ET)) -> None:
    from .stream import BookStream, default_specs

    specs = list(specs or default_specs())
    journal = _Journal(root)
    stop = asyncio.Event()
    state = {"rows": 0, "last_health": 0.0, "connected_at": clock()}

    def on_rows(rows: list[dict]) -> None:
        now = clock()
        for r in rows:
            journal.add(r, now)
        state["rows"] += len(rows)

    def should_stop() -> bool:
        if stop.is_set() or not is_live():
            return True
        t = asyncio.get_event_loop().time()
        if t - state["last_health"] >= HEALTH_S:
            state["last_health"] = t
            _write_health(root, clock(), state["rows"], journal.written, specs)
        return False

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    backoff = 5
    stream = BookStream(client_factory)
    try:
        while not stop.is_set() and is_live():
            started = clock()
            try:
                await stream.run(specs, on_rows, should_stop)
                backoff = 5
            except Exception as exc:                  # noqa: BLE001 — reconnect
                for sp in specs:
                    mark_gap(root, sp.symbol, clock(), f"{type(exc).__name__}: {exc}")
                # a connection that held for a while was not a credentials
                # problem; only a fast repeated failure should back off hard
                if (clock() - started).total_seconds() >= 60:
                    backoff = 5
                await asyncio.sleep(min(backoff, 120))
                backoff *= 2
            finally:
                journal.flush()
    finally:
        journal.close()
        _write_health(root, clock(), state["rows"], journal.written, specs, done=True)


def _write_health(root: Path, now: datetime, seen: int, written: int,
                  specs, done: bool = False) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "health.json").write_text(json.dumps({
            "ts": now.isoformat(), "rows_seen": seen, "rows_written": written,
            "symbols": [s.symbol for s in specs], "running": not done}))
    except Exception:
        pass


def main(client_factory: Callable[[], object], root: Path = STATE) -> int:
    if not ENABLED:
        print("book-flow: DISABLED (daemon.ENABLED is False) — refusing to take "
              "the Schwab connection from the lob-flow collector", file=sys.stderr)
        return 0
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / "collector.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("book-flow: another collector holds the lock — exiting")
        return 0
    lock.write(str(os.getpid()))
    lock.flush()
    if not rth_is_live():
        print("book-flow: outside market hours — nothing to do")
        return 0
    asyncio.run(run(client_factory, root=root))
    return 0
