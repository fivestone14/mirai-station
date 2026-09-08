"""
stream.py — the SCHWAB SIDE. One websocket, N tickers, book snapshots out.

Schwab allows exactly ONE streamer connection per login (Streamer Guide,
response code 12), which is why this is a per-ticker collector rather than one
process per ticker: the connection is the scarce resource, not the CPU.

Which service a symbol needs is decided by where it is LISTED, not by what you
want to learn from it — SNDK and QQQ are Nasdaq-listed (NASDAQ_BOOK), SPY is
NYSE Arca (NYSE_BOOK). An index has no book at all and must never be
subscribed here.

The payload is a WHOLE-BOOK snapshot roughly once a second, not a stream of
changes, so what lands on disk is a sequence of complete pictures. Two field
labels in schwab-py are wrong and are corrected on the way through: the
per-level `EXCHANGE` is a venue-or-MPID string, and `SEQUENCE` is a per-venue
QUOTE TIME in milliseconds since ET midnight, not a counter.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional, Sequence

# Level rows are stored flat and short-keyed: a session is ~700k rows per
# symbol and the key names would dominate the file.
#   t = snapshot ms   y = symbol   s = side (b|a)   p = price
#   z = size at that price   n = venues quoting it
ROW_KEYS = ("t", "y", "s", "p", "z", "n")

SERVICE_BY_LISTING = {"NASDAQ_BOOK": "nasdaq_book", "NYSE_BOOK": "nyse_book",
                      "OPTIONS_BOOK": "options_book"}


def flatten(msg: dict, service: str) -> list[dict]:
    """One labelled book frame -> flat level rows.

    Per-venue detail is deliberately dropped. It quadruples the file and the
    board only ever asks 'how much was resting at this price', while the venue
    breakdown of a single level answers nothing the level total does not. The
    venue COUNT is kept, because a level thinning from six quoters to one is a
    real event and costs one integer.
    """
    out: list[dict] = []
    ts = msg.get("timestamp")
    for item in msg.get("content", []) or []:
        sym = item.get("key")
        book_ms = item.get("BOOK_TIME") or ts
        if sym is None or book_ms is None:
            continue
        for side, levels_key, price_key, count_key in (
                ("b", "BIDS", "BID_PRICE", "NUM_BIDS"),
                ("a", "ASKS", "ASK_PRICE", "NUM_ASKS")):
            for lvl in (item.get(levels_key) or []):
                price = lvl.get(price_key)
                if price is None:
                    continue
                out.append({"t": int(book_ms), "y": sym, "s": side,
                            "p": float(price),
                            "z": float(lvl.get("TOTAL_VOLUME") or 0.0),
                            "n": int(lvl.get(count_key) or 0)})
    return out


class BookStream:
    """Owns the one connection. `client_factory` is injected so this package
    never learns where the host keeps its credentials, and schwab-py stays an
    optional dependency that only the live path imports."""

    def __init__(self, client_factory: Callable[[], object]):
        self._factory = client_factory

    async def run(self, specs: Sequence, on_rows: Callable[[list[dict]], None],
                  should_stop: Optional[Callable[[], bool]] = None) -> None:
        from schwab.streaming import StreamClient      # optional dep, lazy

        stream = StreamClient(self._factory(), account_id=None)
        await stream.login()

        by_service: dict[str, list[str]] = {}
        for sp in specs:
            by_service.setdefault(sp.book_service, []).append(sp.symbol)

        for service, symbols in by_service.items():
            meth = SERVICE_BY_LISTING.get(service)
            if meth is None:
                raise ValueError(f"book service not supported: {service}")
            getattr(stream, f"add_{meth}_handler")(
                lambda m, svc=service: on_rows(flatten(m, svc)))
            # SUBS is destructive — a second SUBS on the same service silently
            # drops everything the first one asked for. Every symbol for a
            # service therefore goes in ONE call.
            await getattr(stream, f"{meth}_subs")(symbols)

        while not (should_stop and should_stop()):
            await stream.handle_message()


def default_specs() -> list:
    """SNDK is the subject; SPY is the control, streamed only so an SNDK move
    can be told apart from a market-wide one. The control is recorded exactly
    like the subject and never blended into it."""
    from .contracts import BoardConfig, TickerSpec
    return [
        TickerSpec(symbol="SNDK", book_service="NASDAQ_BOOK",
                   board=BoardConfig(ticker="SNDK", kind="equity_book",
                                     slice_s=300, bin_w=0.05)),
        TickerSpec(symbol="SPY", book_service="NYSE_BOOK", control=True,
                   board=BoardConfig(ticker="SPY", kind="equity_book",
                                     slice_s=300, bin_w=0.01)),
    ]
