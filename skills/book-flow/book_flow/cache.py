"""
cache.py — the only thing the viewstation is allowed to call.

Building a day's board is a 2-3 second pass over ~900k tape rows. That is fine
once and unacceptable on every tab open, so the wire document is written to
disk the first time it is asked for and served from there afterwards. The cache
key includes the SOURCE FILE'S mtime and size: a day that is still being
recorded keeps changing, and a board cached at noon must not be served as if it
were the finished session.

Nothing here reaches the network, and every failure returns a document with an
`error` field rather than raising — the tab must always render something true,
even if that something is "no data for this day".
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from .sources import book_snapshot_board, spx_tape_board
from .track import spot_track

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STATE = PLUGIN_ROOT / "state"
CACHE_DIR = STATE / "flow" / "boards"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Where each ticker's raw session lives, and how to read it. SPX rides the
# lob-flow recorder that has been running since 2026-08-05; every other ticker
# comes from the book collector's own flattened snapshots.
SPX_TAPE = STATE / "lob_flow" / "raw" / "{day}" / "tape.jsonl"
BOOK_SNAP = STATE / "flow" / "{ticker}" / "{day}" / "book.jsonl"

# Bumped whenever the WIRE SHAPE changes. It rides the cache key, so an old
# board rebuilds instead of being served without a field the tab expects.
DOC_V = 2


def _resolve(base: Path) -> Optional[Path]:
    """The live session writes `x.jsonl`; rotation gzips it to `x.jsonl.gz`.
    Prefer the plain file — while a day is still recording, only it is current."""
    gz = base.with_suffix(base.suffix + ".gz")
    if base.is_file():
        return base
    return gz if gz.is_file() else None


def _src(day: str, ticker: str) -> tuple[Optional[Path], str]:
    if ticker == "SPX":
        return _resolve(Path(str(SPX_TAPE).format(day=day))), "options_tape"
    return _resolve(Path(str(BOOK_SNAP).format(ticker=ticker, day=day))), "equity_book"


def available_days(ticker: str = "SPX") -> list[str]:
    """Days with a recorded session, newest first. Drives the tab's day picker,
    so it must never invent a day the renderer would then fail to load."""
    root = (STATE / "lob_flow" / "raw") if ticker == "SPX" else (STATE / "flow" / ticker)
    name = "tape.jsonl" if ticker == "SPX" else "book.jsonl"
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.glob("[0-9]" * 4 + "-*-*")
                   if _resolve(p / name)), reverse=True)


def board_for_day(day: str, ticker: str = "SPX", *, slice_s: int = 60,
                  max_bins: int = 60, bin_w: Optional[float] = 0.05) -> dict:
    if not DAY_RE.match(day or ""):
        return {"error": "bad day"}
    if not re.fullmatch(r"[A-Z.$^]{1,8}", ticker or ""):
        return {"error": "bad ticker"}
    src, kind = _src(day, ticker)
    if src is None:
        return {"error": f"no recorded session for {ticker} on {day}",
                "ticker": ticker, "day": day, "prices": [], "slices": []}

    st = src.stat()
    # bin_w belongs in the key for the same reason slice_s does: it changes the
    # ARITHMETIC, not just the framing. Leaving it out meant a board rebuilt at a
    # new bin width was served the old one from disk — silently, and looking
    # entirely plausible, because a board at the wrong bin width is still a board.
    stamp = (f"v{DOC_V}-{int(st.st_mtime)}-{st.st_size}"
             f"-{slice_s}-{max_bins}-{bin_w}")
    cache = CACHE_DIR / f"{ticker}-{day}.json"
    if cache.is_file():
        try:
            doc = json.loads(cache.read_text())
            if doc.get("_stamp") == stamp:
                return doc
        except Exception:
            pass                                  # a torn cache rebuilds

    if kind == "options_tape":
        doc = spx_tape_board(src, slice_s=slice_s, max_bins=max_bins,
                             ticker=ticker)
    else:
        doc = book_snapshot_board(src, ticker=ticker, slice_s=slice_s,
                                  bin_w=bin_w, max_bins=max_bins)
    doc["day"] = day
    # Where the underlying actually was while all this was being posted. Never
    # fatal: a board with no price path is still a board, and a day recorded
    # before the diary existed legitimately has none.
    try:
        doc["track"] = spot_track(day, ticker, [s["t"] for s in doc.get("slices") or []],
                                  slice_s)
    except Exception:
        doc["track"] = None
    doc["_stamp"] = stamp
    _write(cache, doc)
    return doc


def _write(path: Path, doc: dict) -> None:
    """Atomic, and never fatal — a board that cannot be cached is still a board
    that can be served."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser(description="prebuild board caches")
    ap.add_argument("--ticker", default="SPX")
    ap.add_argument("--day", help="one day; omit to build every recorded day")
    ap.add_argument("--slice-s", type=int, default=60)
    a = ap.parse_args()
    days = [a.day] if a.day else available_days(a.ticker)
    for d in days:
        t0 = time.time()
        doc = board_for_day(d, a.ticker, slice_s=a.slice_s)
        n = len(doc.get("slices") or [])
        print(f"{a.ticker} {d}: {n} slices, {len(doc.get('prices') or [])} prices "
              f"({time.time() - t0:.1f}s)" + (f"  ERROR {doc['error']}" if doc.get("error") else ""))
