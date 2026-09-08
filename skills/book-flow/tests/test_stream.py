"""Offline tests for the collector's read path: a labelled Schwab book frame
all the way to the wire document the tab draws. No network, no schwab-py."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book_flow.sources import book_snapshot_board          # noqa: E402
from book_flow.stream import flatten                        # noqa: E402

ET = ZoneInfo("America/New_York")
BASE = int(datetime(2026, 9, 8, 9, 30, tzinfo=ET).timestamp() * 1000)


def frame(ms, bids, asks):
    """A NASDAQ_BOOK frame in schwab-py's labelled shape."""
    def side(levels, pk, ck):
        return [{pk: p, "TOTAL_VOLUME": z, ck: n,
                 "BIDS" if pk == "BID_PRICE" else "ASKS":
                     [{"EXCHANGE": "NSDQ", "BID_VOLUME": z, "SEQUENCE": 1}]}
                for p, z, n in levels]
    return {"service": "NASDAQ_BOOK", "timestamp": ms,
            "content": [{"key": "SNDK", "BOOK_TIME": ms,
                         "BIDS": side(bids, "BID_PRICE", "NUM_BIDS"),
                         "ASKS": side(asks, "ASK_PRICE", "NUM_ASKS")}]}


def test_flatten_produces_one_row_per_level_per_side():
    rows = flatten(frame(BASE, [(1739.50, 400, 3), (1739.45, 900, 5)],
                                [(1740.00, 300, 2)]), "NASDAQ_BOOK")
    assert len(rows) == 3
    assert {r["s"] for r in rows} == {"b", "a"}
    bid = next(r for r in rows if r["p"] == 1739.50)
    assert (bid["y"], bid["z"], bid["n"], bid["t"]) == ("SNDK", 400.0, 3, BASE)


def test_frame_without_book_time_falls_back_to_the_message_clock():
    f = frame(BASE, [(10.0, 5, 1)], [])
    del f["content"][0]["BOOK_TIME"]
    assert flatten(f, "NASDAQ_BOOK")[0]["t"] == BASE


def test_end_to_end_snapshots_to_board(tmp_path):
    """Three snapshots: a level grows, then empties out entirely. The board
    must show the growth as an add and the DISAPPEARANCE as a removal — a
    level that drops out of the snapshot is the removal, and skipping it would
    make the board blindest at its biggest events."""
    path = tmp_path / "book.jsonl"
    frames = [
        frame(BASE,          [(1739.50, 400, 3)], [(1740.00, 300, 2)]),
        frame(BASE + 1000,   [(1739.50, 650, 4)], [(1740.00, 300, 2)]),
        frame(BASE + 2000,   [(1739.50, 650, 4)], []),          # ask gone
    ]
    with path.open("w") as fh:
        for f in frames:
            for r in flatten(f, "NASDAQ_BOOK"):
                fh.write(json.dumps({"ts_ms": r["t"], "side":
                                     "bid" if r["s"] == "b" else "ask",
                                     "price": r["p"], "size": r["z"]}) + "\n")

    doc = book_snapshot_board(path, ticker="SNDK", slice_s=60, bin_w=0.05)
    i = doc["prices"].index(1739.50)
    j = doc["prices"].index(1740.0)
    F = doc["fields"]
    cell_b = doc["slices"][0]["c"][i]
    cell_a = doc["slices"][0]["c"][j]
    assert cell_b[F.index("bid_add")] == 250, "400 -> 650 is a 250 add"
    assert cell_b[F.index("bid_rem")] == 0
    assert cell_a[F.index("ask_rem")] == 300, "a level that vanishes is a removal"
    assert cell_a[F.index("ask_rest")] == 0
