"""
sources.py — the two FEEDS that can fill a board, and nothing else.

  spx_tape_board()   reads the recorded SPX options tape (already on disk since
                     2026-08-05) and infers resting-size changes from the
                     bid/ask sizes each print carries. Real data, available
                     today, and the reason the renderer can be built before the
                     equity collector exists.

  book_snapshot_board()  reads recorded NASDAQ_BOOK/NYSE_BOOK snapshots, where
                     the add/remove is not inferred at all — it is literally the
                     difference between two consecutive snapshots.

Both hand back the SAME wire document, so the tab cannot tell them apart. The
`kind` field is the only thing that says which, and it is carried purely so a
reader knows how much to trust the numbers: the tape board samples the book
only when a trade prints, so a quiet strike goes unobserved and its changes are
folded into the next print rather than lost. The snapshot board has no such
hole.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
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
# SPX options tape  (state/lob_flow/raw/{day}/tape.jsonl)
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


# ---------------------------------------------------------------------------
# Equity depth snapshots  (state/flow/{ticker}/{day}/book.jsonl)
# ---------------------------------------------------------------------------

def book_snapshot_board(path: Path, *, ticker: str, slice_s: int = 60,
                        bin_w: Optional[float] = 0.05,
                        max_bins: int = 60) -> dict:
    """Rows are one flattened NASDAQ_BOOK/NYSE_BOOK snapshot level:
    {ts_ms, symbol, side, price, size}.

    One price carries ONE book here, so no series scoping is needed — but the
    two sides arrive as separate rows, and a level that empties disappears from
    the snapshot rather than arriving as a zero. `_zero_missing` puts the zero
    back, because a level dropping out IS the removal and skipping it would
    make the board's biggest events its blindest spot.
    """
    cfg = BoardConfig(ticker=ticker, kind="equity_book", slice_s=slice_s,
                      bin_w=bin_w, max_bins=max_bins)
    bb = BoardBuilder(cfg)
    for ts_ms, levels in _group_snapshots(path):
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=ET)
        for price, (bid, ask) in levels.items():
            # No `traded=` here, and it is not an omission. A NASDAQ_BOOK frame
            # is a picture of RESTING orders and carries no trade information at
            # all — no last price, no last size, no cumulative volume. So `vol`
            # is legitimately zero on every bin of an equity board, and the
            # renderer must say the traded view is unavailable for this feed
            # rather than drawing an empty board that looks like a quiet day.
            bb.observe(ts, price, bid, ask)
    slices = bb.finish()
    return to_wire(slices, cfg, trim_bins(slices, max_bins))


def _group_snapshots(path: Path) -> Iterator[tuple[int, dict]]:
    """Regroup flat level rows back into whole snapshots, and emit an explicit
    0 for every price that was present in the previous snapshot and is absent
    from this one."""
    cur_ts: Optional[int] = None
    cur: dict[float, list] = {}
    seen: set[float] = set()
    for r in _iter_jsonl(path):
        ts_ms = r.get("ts_ms")
        if ts_ms is None:
            continue
        if cur_ts is not None and ts_ms != cur_ts:
            yield cur_ts, _with_zeros(cur, seen)
            seen = set(cur)
            cur = {}
        cur_ts = ts_ms
        price = r.get("price")
        if price is None:
            continue
        slot = cur.setdefault(float(price), [0.0, 0.0])
        slot[0 if r.get("side") == "bid" else 1] += float(r.get("size") or 0.0)
    if cur_ts is not None:
        yield cur_ts, _with_zeros(cur, seen)


def _with_zeros(cur: dict, seen: set) -> dict:
    out = {p: (v[0], v[1]) for p, v in cur.items()}
    for p in seen - set(out):
        out[p] = (0.0, 0.0)
    return out
