"""book_flow — the price x time board: what was added and removed at each price
through a session, and the collector that records it.

Public surface is deliberately tiny; everything else is an implementation
detail the viewstation must not reach into.
"""
from .contracts import Bin, BoardConfig, Slice, TickerSpec
from .board import BoardBuilder, to_wire, trim_bins
from .sources import book_snapshot_board, spx_tape_board

__all__ = ["Bin", "BoardConfig", "Slice", "TickerSpec", "BoardBuilder",
           "to_wire", "trim_bins", "spx_tape_board", "book_snapshot_board"]
