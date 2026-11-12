"""
schwab_options_feed.py — the CHAIN TAP over Schwab: the same two feeds
options_feed.py takes from the hosted MCP, taken from Schwab instead.

Added 2026-11-11, after the MCP stopped offering `options_chain` and
`historical_option_trades` (2026-11-09) and both journals went empty.

  * OptionsQuoteFeedSchwab.sweep()      — today's 0DTE chain, both sides, with
                         bid/ask sizes and deltas, from one Schwab chain call.
                         The fetch is injected (the host owns the Schwab login
                         and the JSON shaping), so this box still imports
                         nothing from the host.
  * OptionsTapeFeedStream.trades_since() — the trade tape, REBUILT from the
                         station's one Schwab stream. Schwab has no trade-by-
                         trade history endpoint for options; what it does push,
                         for every subscribed contract, is the running total
                         volume, the last price and its trade time. A rise in
                         total volume is therefore a print: size = the rise,
                         price = the last price, time = the trade time, and the
                         quote attached is the one that stood BEFORE the row
                         that reported it (the quote the print met).

What the rebuilt tape is, and is not (a reader must be able to tell, so every
row it writes carries `src = "schwab_stream"`):
  * It covers the contracts the stream is subscribed to (the focus set: the
    ATM window plus the gravity strikes), the same contracts the MCP tape was
    pulled for.
  * Prints that land between two stream rows arrive as ONE row with their
    summed size at the last of their prices. Volume is exact; the count of
    prints, and the price of all but the last print in a burst, are not.
  * Volume traded while the stream was down is not on the tape: after a
    reconnect a contract's first row only re-bases its total (a short gap is
    bridged as one row, a long one is counted in `missed_volume` and skipped).
  * `condition` is always None (Schwab does not send the exchange condition).
  * `last_size` (added 2026-11-12) is the size of the newest single print in the
    row, from the stream's LAST_SIZE, capped at the row's size. A row whose
    last_size equals its size holds exactly one print, so a reader can apply
    single-trade rules (a "big print") to the print rather than to the lump.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from .contracts import QuoteRow, TradeEvent

_DATA_COLLECTORS = str(Path(__file__).resolve().parents[2])   # schwab_live_stream sits beside this box
if _DATA_COLLECTORS not in sys.path:
    sys.path.append(_DATA_COLLECTORS)
from schwab_live_stream.stream_symbols import parse_option_symbol  # noqa: E402

TAPE_SOURCE = "schwab_stream"
BRIDGE_GAP_S = 90.0        # a reconnect shorter than this is bridged as one catch-up row
MAX_PENDING = 200_000      # hard bound on undrained trades (a stalled tape loop)


class OptionsQuoteFeedSchwab:
    """Chain sweeper over Schwab. `fetch_day_book(expiry_iso)` returns
    {"spot": float, "contracts": [{root, expiry, right, strike, bid, ask,
    bid_size, ask_size, delta}, ...]} or None. Returns (spot, [QuoteRow]),
    the same answer OptionsQuoteFeedMcp.sweep gives."""

    def __init__(self, fetch_day_book: Callable[[str], Optional[dict]],
                 lo_frac: float = 0.97, hi_frac: float = 1.03):
        self._fetch = fetch_day_book
        self.lo_frac, self.hi_frac = lo_frac, hi_frac

    def sweep(self, root: str, expiry: str, now: datetime) -> tuple[Optional[float], list[QuoteRow]]:
        book = self._fetch(expiry)
        spot = (book or {}).get("spot")
        if not book or not spot:
            return spot, []
        lo, hi = spot * self.lo_frac, spot * self.hi_frac
        ts = now.isoformat()
        rows = [QuoteRow(root=root, expiry=expiry, right=c.get("right"),
                         strike=c.get("strike"), bid=c.get("bid"), ask=c.get("ask"),
                         bid_size=c.get("bid_size"), ask_size=c.get("ask_size"),
                         delta=c.get("delta"), ts=ts)
                for c in book.get("contracts") or []
                if c.get("root") == root and c.get("expiry") == expiry
                and c.get("strike") is not None and lo <= c["strike"] <= hi
                and c.get("right") in ("call", "put")]
        return spot, rows


class _Contract:
    """What the stream last said about one contract."""
    __slots__ = ("bid", "ask", "bid_size", "ask_size", "last_price", "last_size",
                 "trade_ms", "volume", "rebase")

    def __init__(self):
        self.bid = self.ask = self.bid_size = self.ask_size = None
        self.last_price = self.last_size = self.trade_ms = self.volume = None
        self.rebase = False


class OptionsTapeFeedStream:
    """The tape, rebuilt from the Schwab stream's level-one option rows.

    The stream's option listener calls on_stream_quote(row) for every row; the
    collector's tape loop calls trades_since(), which hands over everything
    gathered since the last call. on_stream_reset() is called before each
    (re)connect so the first total a contract reports afterwards is treated as
    a re-base, not as one giant print."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._contracts: dict[str, _Contract] = {}
        self._pending: list[TradeEvent] = []
        self._last_row_s: Optional[float] = None     # when any row last arrived
        self._down_since_s: Optional[float] = None   # last row before the current reset
        self.missed_volume = 0                       # contracts traded while blind
        self.dropped = 0                             # trades shed at MAX_PENDING

    # -- stream side ---------------------------------------------------------

    def on_stream_reset(self) -> None:
        with self._lock:
            self._down_since_s = self._last_row_s
            for c in self._contracts.values():
                c.rebase = True

    def on_stream_quote(self, row: dict) -> None:
        parsed = parse_option_symbol(row.get("symbol"))
        if parsed is None:
            return
        _root, _expiry, right, strike = parsed
        now_s = self._clock()
        with self._lock:
            c = self._contracts.get(row["symbol"])
            if c is None:
                c = self._contracts[row["symbol"]] = _Contract()
            price = _positive(row.get("last_price"))
            last_size = _whole(row.get("last_size"))
            if last_size is not None and last_size > 0:
                c.last_size = last_size                   # Schwab sends a field only when it changes
            trade_ms = _whole(row.get("trade_ms"))
            volume = _whole(row.get("total_volume"))
            if volume is not None:
                if c.volume is None:
                    pass                                  # first sight: the day's total so far
                elif c.rebase:
                    blind = (now_s - self._down_since_s) if self._down_since_s else None
                    if volume > c.volume:
                        if blind is not None and blind <= BRIDGE_GAP_S:
                            self._emit(c, strike, right, volume - c.volume, price, trade_ms, row, now_s)
                        else:
                            self.missed_volume += volume - c.volume
                elif volume > c.volume:
                    self._emit(c, strike, right, volume - c.volume, price, trade_ms, row, now_s)
                c.volume = volume
                c.rebase = False
            if price is not None:
                c.last_price = price
            if trade_ms is not None:
                c.trade_ms = trade_ms
            for name in ("bid", "ask", "bid_size", "ask_size"):
                if row.get(name) is not None:
                    setattr(c, name, row[name])
            self._last_row_s = now_s

    def _emit(self, c: _Contract, strike: float, right: str, size: int,
              price: Optional[float], trade_ms: Optional[int], row: dict, now_s: float) -> None:
        price = price if price is not None else c.last_price
        if price is None or size <= 0:
            return                                        # a volume rise with no price yet
        # the row's own trade time; a row that reports the volume without one (Schwab sends
        # only changed fields) is stamped on arrival rather than with an earlier print's time
        now_ms = int(now_s * 1000)
        ms = trade_ms or now_ms
        if ms > now_ms + 5000 or ms < now_ms - 600_000:
            ms = now_ms                                   # a clock that cannot be this print's
        # the quote the print met: what stood before this row, else what this row brings
        quote = {name: (getattr(c, name) if getattr(c, name) is not None else row.get(name))
                 for name in ("bid", "ask", "bid_size", "ask_size")}
        last_size = min(c.last_size, size) if c.last_size else None
        if len(self._pending) >= MAX_PENDING:
            self.dropped += 1
            return
        self._pending.append(TradeEvent(
            ts_ms=ms, strike=strike, right=right, price=float(price), size=int(size),
            bid=quote["bid"], ask=quote["ask"],
            bid_size=_whole(quote["bid_size"]), ask_size=_whole(quote["ask_size"]),
            condition=None, src=TAPE_SOURCE, last_size=last_size))

    # -- tape-loop side ------------------------------------------------------

    def trades_since(self, root: str, expiry: str,
                     contracts: Sequence[tuple[float, str]],
                     cursor: dict) -> tuple[list[TradeEvent], dict]:
        """Everything the stream reported since the last call, oldest first.
        The cursor is carried for the journal's sake (newest trade time per
        contract); nothing is re-pulled, so it never filters."""
        with self._lock:
            events, self._pending = self._pending, []
        events.sort(key=lambda e: e.ts_ms)
        new_cursor = {"day": expiry, "contracts": dict(cursor.get("contracts") or {})}
        for e in events:
            cid = f"{e.strike}|{e.right}"
            prev = new_cursor["contracts"].get(cid)
            prev_ms = prev.get("ms", 0) if isinstance(prev, dict) else int(prev or 0)
            if e.ts_ms >= prev_ms:
                new_cursor["contracts"][cid] = {"ms": e.ts_ms, "n": 1}
        return events, new_cursor


def _positive(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _whole(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
