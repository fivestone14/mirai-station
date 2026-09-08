"""
contracts.py — the SHARED LANGUAGE of the board. Data shapes and knobs only:
no logic, no I/O, nothing importable that can fail.

The board is one idea reused by two very different feeds:

    price  x  time  ->  how much size was ADDED and how much was REMOVED

For the SNDK equity book that is literal — consecutive NASDAQ_BOOK snapshots
differ, and the difference at each price level IS the add/remove. For the SPX
options tape it is inferred: every trade carries the bid/ask size prevailing at
its own strike, so consecutive prints at one strike sample the resting size and
their difference is the same quantity, measured less often.

Both produce the SAME Slice, so the renderer never learns which fed it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

ET = "America/New_York"

# The wire form of one bin is a fixed-order list, not an object: a day is
# ~30 bins x 390 slices and the key names would be ~70% of the bytes.
BIN_FIELDS = ("price", "bid_add", "bid_rem", "ask_add", "ask_rem",
              "bid_rest", "ask_rest", "vol")


@dataclass(frozen=True)
class Bin:
    """One price level inside one slice.

    add/rem are FLOWS over the slice (how much appeared, how much left);
    rest is a LEVEL (what was sitting there when the slice closed). `rem` is
    stored positive — the sign lives in the field name, so a consumer that
    forgets to negate cannot silently draw a removal as an addition.

    A removal is 'cancelled OR filled' and the two cannot be separated from
    snapshots alone (see the report: a size decrease is either, and the
    identification fails). `vol` is what is known to have traded, so
    `rem - vol` is the closest honest cancel proxy — computed by the consumer,
    never stored as if it were measured.
    """
    price: float
    bid_add: float = 0.0
    bid_rem: float = 0.0
    ask_add: float = 0.0
    ask_rem: float = 0.0
    bid_rest: float = 0.0
    ask_rest: float = 0.0
    vol: float = 0.0

    def as_row(self) -> list:
        return [round(getattr(self, f), 4) for f in BIN_FIELDS]

    @staticmethod
    def from_row(row) -> "Bin":
        return Bin(**dict(zip(BIN_FIELDS, row)))


@dataclass(frozen=True)
class Slice:
    """One time step of the board. `start` is the slice's own opening clock in
    ET, so the slider reads wall time and never an index."""
    start: str
    bins: tuple[Bin, ...] = ()
    obs: int = 0                       # observations folded in; 0 = a dead slice

    def as_wire(self) -> dict:
        return {"t": self.start, "n": self.obs,
                "b": [b.as_row() for b in self.bins]}


@dataclass
class BoardConfig:
    """Per-ticker knobs. One config = one board.

    bin_w=None means the price is ALREADY discrete and is used as its own bin —
    true for option strikes ($5 on SPX), false for an equity book that ticks in
    cents. Leaving it None on an equity feed would make one bin per penny and
    a board nobody can read, so the collector must set it.
    """
    ticker: str
    kind: str                          # "options_tape" | "equity_book"
    slice_s: int = 60
    bin_w: Optional[float] = None
    max_bins: int = 60                 # widest board the renderer draws well
    tz: str = ET
    lanes: tuple[str, str] = ("bid", "ask")


@dataclass
class TickerSpec:
    """What the collector subscribes to for one ticker. `book_service` is the
    Schwab streamer service, chosen by where the symbol is LISTED (SNDK and
    QQQ are Nasdaq-listed -> NASDAQ_BOOK; SPY is NYSE Arca -> NYSE_BOOK).
    `control` marks a symbol streamed only so the primary can be compared
    against it — it is recorded exactly the same way and never blended in."""
    symbol: str
    book_service: str = "NASDAQ_BOOK"
    control: bool = False
    board: Optional[BoardConfig] = None
    extra: dict = field(default_factory=dict)
