"""Offline tests for the board builder. No network, no host state, no files."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from book_flow.board import BoardBuilder, to_wire, trim_bins   # noqa: E402
from book_flow.contracts import BoardConfig                      # noqa: E402

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 4, 9, 30, 0, tzinfo=ET)


def cfg(**kw):
    base = dict(ticker="T", kind="equity_book", slice_s=60, bin_w=None, max_bins=10)
    base.update(kw)
    return BoardConfig(**base)


def test_first_sight_is_not_an_add():
    """A price seen for the first time has nothing to difference against. If it
    counted as an add, every level would open the session with a phantom wall
    the size of its whole queue."""
    bb = BoardBuilder(cfg())
    bb.observe(T0, 100.0, 500, 400)
    (s,) = bb.finish()
    b = s.bins[0]
    assert (b.bid_add, b.bid_rem, b.ask_add, b.ask_rem) == (0, 0, 0, 0)
    assert (b.bid_rest, b.ask_rest) == (500, 400)


def test_growth_is_add_and_shrink_is_rem():
    bb = BoardBuilder(cfg())
    bb.observe(T0, 100.0, 500, 400)
    bb.observe(T0 + timedelta(seconds=5), 100.0, 700, 250)
    (s,) = bb.finish()
    b = s.bins[0]
    assert b.bid_add == 200 and b.bid_rem == 0
    assert b.ask_rem == 150 and b.ask_add == 0


def test_memory_survives_the_slice_boundary():
    """THE regression that matters. If `_last` reset per slice, the first
    observation of slice 2 would book its entire resting size as an add."""
    bb = BoardBuilder(cfg())
    bb.observe(T0, 100.0, 500, 500)
    bb.observe(T0 + timedelta(seconds=70), 100.0, 520, 500)   # next slice
    a, b = bb.finish()
    assert b.bins[0].bid_add == 20, "carried memory should see +20, not +520"
    assert b.bins[0].bid_rem == 0


def test_series_scoping_keeps_two_books_apart():
    """One strike carries a call book and a put book. Differenced together, an
    alternating feed invents a removal the size of the whole queue every row."""
    bb = BoardBuilder(cfg())
    bb.observe(T0, 100.0, 300, 300, series="100|call")
    bb.observe(T0, 100.0, 25, 25, series="100|put")
    bb.observe(T0, 100.0, 310, 300, series="100|call")
    (s,) = bb.finish()
    b = s.bins[0]
    assert b.bid_add == 10 and b.bid_rem == 0
    assert b.bid_rest == 335, "rest sums the books on the price, not last-wins"


def test_gaps_become_empty_slices():
    """A dead stretch must still occupy its steps, or the slider silently
    compresses time and the day looks faster than it was."""
    bb = BoardBuilder(cfg())
    bb.observe(T0, 100.0, 10, 10)
    bb.observe(T0 + timedelta(seconds=300), 100.0, 20, 10)
    out = bb.finish()
    assert len(out) == 6
    assert [s.obs for s in out] == [1, 0, 0, 0, 0, 1]


def test_binning_floors_to_the_lower_edge():
    bb = BoardBuilder(cfg(bin_w=0.05))
    bb.observe(T0, 100.02, 10, 10)
    bb.observe(T0, 100.04, 10, 10)
    bb.observe(T0, 100.07, 10, 10)
    (s,) = bb.finish()
    assert [b.price for b in s.bins] == [100.0, 100.05]


def test_wire_rows_align_to_the_price_axis():
    bb = BoardBuilder(cfg())
    bb.observe(T0, 100.0, 10, 10)
    bb.observe(T0, 102.0, 10, 10)
    bb.observe(T0 + timedelta(seconds=70), 102.0, 30, 10)
    doc = to_wire(bb.finish(), cfg())
    assert doc["prices"] == [100.0, 102.0]
    assert doc["slices"][1]["c"][0] is None          # 100.0 idle in slice 2
    assert doc["slices"][1]["c"][1][0] == 20         # 102.0 bid_add
    assert len(doc["fields"]) == len(doc["slices"][0]["c"][0])


def test_trim_keeps_the_busiest_prices_in_price_order():
    bb = BoardBuilder(cfg())
    for i, p in enumerate((10.0, 20.0, 30.0)):
        bb.observe(T0, p, 100, 100)
        bb.observe(T0 + timedelta(seconds=1), p, 100 + (i + 1) * 50, 100)
    keep = trim_bins(bb.finish(), 2)
    assert keep == [20.0, 30.0]
