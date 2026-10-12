"""
sources.py — the one FEED that fills a board, and nothing else.

  spx_tape_board()   reads the recorded SPX options tape (on disk since
                     2026-08-05) and infers resting-size changes from the
                     bid/ask sizes each print carries.

The tape board samples the book only when a trade prints, so a quiet strike
goes unobserved and its changes are folded into the next print rather than
lost. The wire document's `kind` field says so ("options_tape"), purely so a
reader knows how much to trust the numbers. A second feed, the equity-book
collector's whole-snapshot differences, was built beside this one and never
switched on; it went on 2026-10-11.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from .board import BoardBuilder, to_wire, trim_bins
from .contracts import BoardConfig

ET = ZoneInfo("America/New_York")


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Transparently reads the rotated form. Sessions older than the current
    one are gzipped in place by the recorder's disk rotation, so a reader that
    only knows `.jsonl` can see exactly one day of history and silently reports
    every older session as missing."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue                      # a torn final line on a crash


# ---------------------------------------------------------------------------
# SPX options tape  (market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl)
# ---------------------------------------------------------------------------

def spx_tape_board(path: Path, *, slice_s: int = 60, max_bins: int = 60,
                   ticker: str = "SPX") -> dict:
    """Rows carry ts_ms, strike, right, size, bid_size, ask_size.

    Strikes are already discrete ($5 on SPX) so they ARE the bins — bin_w stays
    None. Every row is scoped by `strike|right` so the call book and the put
    book are differenced separately, then summed back into the strike.
    """
    cfg = BoardConfig(ticker=ticker, kind="options_tape",
                      slice_s=slice_s, bin_w=None, max_bins=max_bins)
    bb = BoardBuilder(cfg)
    rows = sorted(_iter_jsonl(path), key=lambda r: r.get("ts_ms") or 0)
    for r in rows:
        ts_ms, strike = r.get("ts_ms"), r.get("strike")
        if ts_ms is None or strike is None:
            continue
        bb.observe(datetime.fromtimestamp(ts_ms / 1000.0, tz=ET),
                   float(strike), r.get("bid_size"), r.get("ask_size"),
                   traded=r.get("size") or 0.0,
                   series=f"{strike}|{r.get('right')}")
    slices = bb.finish()
    return to_wire(slices, cfg, trim_bins(slices, max_bins))
