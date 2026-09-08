"""
board.py — the BOARD BUILDER. One pure accumulator, no I/O, no clock of its
own: feed it observations in time order, it hands back slices.

The whole thing turns on one rule. Resting size at a price is a LEVEL, and the
board wants FLOWS, so every observation is differenced against the last one
seen AT THAT PRICE:

    grew  -> size was added
    shrank-> size was removed (cancelled or filled; snapshots cannot say which)

That per-price memory deliberately SURVIVES the slice boundary. A book does not
reset at 09:31:00, so neither does `_last` — resetting it would book the first
observation of every slice as a giant phantom add, which is the single easiest
way to get this wrong.

A price seen for the FIRST time all day is not an add either: there is nothing
to difference against, so it seeds `_last` and contributes only its resting
level. Otherwise every strike would open the session with a fake wall.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from .contracts import Bin, BoardConfig, Slice


class _Acc:
    __slots__ = ("bid_add", "bid_rem", "ask_add", "ask_rem",
                 "bid_rest", "ask_rest", "vol")

    def __init__(self):
        self.bid_add = self.bid_rem = self.ask_add = self.ask_rem = 0.0
        self.bid_rest = self.ask_rest = 0.0
        self.vol = 0.0


class BoardBuilder:
    """Streaming accumulator. `observe()` must be called in non-decreasing ts
    order; `finish()` closes the open slice and returns everything."""

    def __init__(self, cfg: BoardConfig):
        self.cfg = cfg
        self._last: dict = {}                       # bin | (bin, series) -> (bid, ask)
        self._cur: dict[float, _Acc] = {}
        self._rest: dict[float, dict] = {}          # bin -> {series: (bid, ask)}
        self._start: Optional[datetime] = None
        self._obs = 0
        self._out: list[Slice] = []

    # -- binning ----------------------------------------------------------
    def _bin(self, price: float) -> float:
        w = self.cfg.bin_w
        if not w:
            return float(price)
        # INTEGER arithmetic, not `price // w`. In binary floating point
        # 1739.50 / 0.05 lands a hair under 34790, so the float floor drops the
        # price a whole bin to 1739.45 — every round price would be filed one
        # tick low and the busiest levels on the board would be wrong. Micro
        # units are exact for any price and tick size this will ever see.
        micro = int(round(price * 1e6))
        step = int(round(w * 1e6))
        return (micro // step) * step / 1e6

    def _slice_start(self, ts: datetime) -> datetime:
        s = self.cfg.slice_s
        epoch = int(ts.timestamp())
        return ts.fromtimestamp(epoch - (epoch % s), tz=ts.tzinfo)

    # -- ingest -----------------------------------------------------------
    def observe(self, ts: datetime, price: float,
                bid_size: Optional[float], ask_size: Optional[float],
                traded: float = 0.0, series: Optional[str] = None) -> None:
        """`series` scopes the per-price MEMORY without splitting the output
        bin. It exists because one price can carry several independent books:
        an option strike has a call book and a put book, and differencing one
        against the other invents a removal the size of the whole queue every
        time the feed alternates. Pass the contract id there; the add/remove it
        produces still lands in the strike's own bin. Equity depth has one book
        per price and leaves it None."""
        if price is None:
            return
        start = self._slice_start(ts)
        if self._start is None:
            self._start = start
        elif start > self._start:
            self._roll_to(start)

        b = self._bin(price)
        acc = self._cur.get(b)
        if acc is None:
            acc = self._cur[b] = _Acc()

        bs = 0.0 if bid_size is None else float(bid_size)
        asz = 0.0 if ask_size is None else float(ask_size)
        mem = b if series is None else (b, series)
        prev = self._last.get(mem)
        if prev is not None:
            d_bid, d_ask = bs - prev[0], asz - prev[1]
            if d_bid > 0:
                acc.bid_add += d_bid
            elif d_bid < 0:
                acc.bid_rem += -d_bid
            if d_ask > 0:
                acc.ask_add += d_ask
            elif d_ask < 0:
                acc.ask_rem += -d_ask
        self._last[mem] = (bs, asz)

        # rest is the LAST seen level in the slice, not a mean: the board asks
        # "what was sitting there when this minute closed", and a mean of a
        # quantity that jumps would draw a wall that never existed
        # with several series on one price the level is the SUM of what each
        # book is showing, so track them and total — assigning would leave the
        # bin reading whichever contract happened to print last
        rests = self._rest.setdefault(b, {})
        rests[mem] = (bs, asz)
        acc.bid_rest = sum(v[0] for v in rests.values())
        acc.ask_rest = sum(v[1] for v in rests.values())
        if traded:
            acc.vol += float(traded)
        self._obs += 1

    # -- slice boundaries -------------------------------------------------
    def _emit(self) -> None:
        if self._start is None:
            return
        bins = tuple(
            Bin(price=p, bid_add=a.bid_add, bid_rem=a.bid_rem,
                ask_add=a.ask_add, ask_rem=a.ask_rem,
                bid_rest=a.bid_rest, ask_rest=a.ask_rest, vol=a.vol)
            for p, a in sorted(self._cur.items()))
        self._out.append(Slice(start=self._start.isoformat(timespec="seconds"),
                               bins=bins, obs=self._obs))

    def _roll_to(self, start: datetime) -> None:
        """Close the open slice and emit EMPTY slices for any gap, so the
        slider's steps are wall-clock even across a dead stretch — a board that
        silently skips a quiet minute lies about how fast the day moved."""
        self._emit()
        step = timedelta(seconds=self.cfg.slice_s)
        cur = self._start + step
        while cur < start:
            self._out.append(Slice(start=cur.isoformat(timespec="seconds"),
                                   bins=(), obs=0))
            cur += step
        self._start = start
        self._cur = {}
        self._obs = 0

    def finish(self) -> list[Slice]:
        self._emit()
        self._start = None
        self._cur = {}
        self._obs = 0
        return self._out


def trim_bins(slices: list[Slice], max_bins: int) -> list[float]:
    """Pick the prices worth drawing: the busiest `max_bins` by total activity,
    returned in price order. A board wider than the screen is unreadable, and
    the tails are usually strikes that printed twice all day."""
    weight: dict[float, float] = {}
    for s in slices:
        for b in s.bins:
            weight[b.price] = weight.get(b.price, 0.0) + (
                b.bid_add + b.bid_rem + b.ask_add + b.ask_rem)
    keep = sorted(weight, key=lambda p: weight[p], reverse=True)[:max_bins]
    return sorted(keep)


def to_wire(slices: Iterable[Slice], cfg: BoardConfig,
            prices: Optional[list[float]] = None) -> dict:
    """The document the browser gets: one price axis, one row per slice, and
    every bin row positionally aligned to that axis so the renderer indexes
    instead of searching."""
    slices = list(slices)
    prices = prices if prices is not None else trim_bins(slices, cfg.max_bins)
    idx = {p: i for i, p in enumerate(prices)}
    rows = []
    for s in slices:
        cells = [None] * len(prices)
        for b in s.bins:
            i = idx.get(b.price)
            if i is not None:
                cells[i] = b.as_row()[1:]        # price is the axis, not a field
        rows.append({"t": s.start, "n": s.obs, "c": cells})
    return {"ticker": cfg.ticker, "kind": cfg.kind, "slice_s": cfg.slice_s,
            "bin_w": cfg.bin_w, "prices": prices, "slices": rows,
            "fields": ["bid_add", "bid_rem", "ask_add", "ask_rem",
                       "bid_rest", "ask_rest", "vol"]}
