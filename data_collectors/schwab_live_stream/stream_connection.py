"""
stream_connection.py — the station's ONE Schwab streaming connection (Schwab
allows one per account), over the host's existing Schwab auth: a
client_factory is injected; schwab-py is imported lazily, so nothing else
needs it installed. Moved here from the tape collector's ``lob_flow/spy_stream.py``
on 2026-10-20 so more than one listener can ride the same connection.

  * SchwabLiveStream.run() — one connection carrying every subscription:
      - SPY top-of-book (LEVELONE_EQUITIES, bid/ask + sizes) -> on_depth
      - $SPX's last trade (the same LEVELONE_EQUITIES call, plus LAST_PRICE and
        TRADE_TIME_MILLIS) -> on_index_tick, only when that listener is given
      - the ~30-strike SPXW focus set (LEVELONE_OPTIONS; note the option
        field enums BID_SIZE=16 / ASK_SIZE=17 differ from equities) for TRUE
        quote-revision counts on the strikes that matter.
    SPXW symbol acceptance confirmed live 2026-07-04.

The lob-collector daemon owns the connection (see README.md); every listener
is a plain callback, so a listener's trouble is its own and never the stream's.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from .stream_symbols import SPX_INDEX_SYMBOL, SPY_BOOK_SYMBOL

ET = ZoneInfo("America/New_York")
BOOK_KEYS = ("BID_PRICE", "ASK_PRICE", "BID_SIZE", "ASK_SIZE")   # a book sample carries at least one


class SchwabLiveStream:
    """One connection, every subscription. on_depth receives dicts
    {ts, bid, ask, bid_size, ask_size} (a DepthSample's fields) for the equity
    symbol; on_option_quote receives raw dicts {symbol, bid, ask, bid_size,
    ask_size, ts} for the focus set; on_index_tick receives (price, trade time
    ET ISO) for every $SPX update."""

    def __init__(self, client_factory: Callable[[], object],
                 account_index: int = 0):
        self._factory = client_factory
        self._account_index = account_index

    async def run(self, equity_symbol: str,
                  option_symbols: Sequence[str],
                  on_depth: Callable[[dict], None],
                  on_option_quote: Optional[Callable[[dict], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None,
                  on_index_tick: Optional[Callable[[float, str], None]] = None) -> None:
        """Login, subscribe, pump messages until should_stop(). Raises on
        login failure (the daemon's supervisor handles backoff/restart)."""
        from schwab.streaming import StreamClient  # optional dep, lazy

        client = self._factory()
        stream = StreamClient(client, account_id=None)
        await stream.login()

        eq_fields = StreamClient.LevelOneEquityFields
        fields = [eq_fields.SYMBOL, eq_fields.BID_PRICE, eq_fields.ASK_PRICE,
                  eq_fields.BID_SIZE, eq_fields.ASK_SIZE]
        symbols = [equity_symbol]
        index = None
        if on_index_tick is not None:
            # Schwab's field list is one per service, so $SPX's two fields reach SPY's
            # rows too; _handle_equity drops a SPY row that carries no book field.
            fields += [eq_fields.LAST_PRICE, eq_fields.TRADE_TIME_MILLIS]
            symbols.append(SPX_INDEX_SYMBOL)
            index = {"price": None, "ms": None, "on_tick": on_index_tick}
        stream.add_level_one_equity_handler(
            lambda msg: _handle_equity(msg, equity_symbol, on_depth, index))
        await stream.level_one_equity_subs(symbols, fields=fields)

        if option_symbols and on_option_quote is not None:
            op_fields = StreamClient.LevelOneOptionFields
            stream.add_level_one_option_handler(
                lambda msg: _handle_option(msg, on_option_quote))
            await stream.level_one_option_subs(
                list(option_symbols),
                fields=[op_fields.SYMBOL, op_fields.BID_PRICE,
                        op_fields.ASK_PRICE,
                        op_fields.BID_SIZE, op_fields.ASK_SIZE])

        while not (should_stop and should_stop()):
            await stream.handle_message()


def _handle_equity(msg: dict, symbol: str, on_depth: Callable[[dict], None],
                   index: Optional[dict] = None) -> None:
    for item in msg.get("content", []) or []:
        key = item.get("key")
        if index is not None and key == SPX_INDEX_SYMBOL:
            _handle_index(item, index)
            continue
        if key != symbol:
            continue
        if index is not None and not any(k in item for k in BOOK_KEYS):
            continue                             # a last-trade-only row: no book sample, as before $SPX joined
        on_depth({"ts": datetime.now(tz=ET).isoformat(),
                  "bid": item.get("BID_PRICE"), "ask": item.get("ASK_PRICE"),
                  "bid_size": item.get("BID_SIZE"), "ask_size": item.get("ASK_SIZE")})


def _handle_index(item: dict, index: dict) -> None:
    """Fold one $SPX row (Schwab sends only the fields that changed) into the
    last known trade and hand it on; nothing here may raise into the stream."""
    try:
        if item.get("LAST_PRICE") is not None:
            index["price"] = float(item["LAST_PRICE"])
        if item.get("TRADE_TIME_MILLIS") is not None:
            index["ms"] = int(item["TRADE_TIME_MILLIS"])
        if index["price"] is None:
            return
        at = (datetime.fromtimestamp(index["ms"] / 1000, tz=ET) if index["ms"]
              else datetime.now(tz=ET))
        index["on_tick"](index["price"], at.isoformat())
    except Exception:  # noqa: BLE001 — the listener reports its own trouble; the stream goes on
        pass


def _handle_option(msg: dict, on_quote: Callable[[dict], None]) -> None:
    for item in msg.get("content", []) or []:
        on_quote({"symbol": item.get("key"),
                  "bid": item.get("BID_PRICE"), "ask": item.get("ASK_PRICE"),
                  "bid_size": item.get("BID_SIZE"),
                  "ask_size": item.get("ASK_SIZE"),
                  "ts": datetime.now(tz=ET).isoformat()})


# ---------------------------------------------------------------------------
# --probe CLI (live smoke test; NOT used by the test suite)
#   run from data_collectors/: python -m schwab_live_stream.stream_connection --probe --client-py '...'
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    ap = argparse.ArgumentParser(
        description="live Schwab stream probe (needs host auth via --client-py)")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--client-py", required=True,
                    help="python expression evaluating to a schwab client, e.g. "
                         "'__import__(\"iv_fetcher\")._build_client()'")
    ap.add_argument("--seconds", type=int, default=15)
    a = ap.parse_args()
    if not a.probe:
        ap.print_help()
        sys.exit(2)

    got = {"depth": 0, "index": 0, "last": None}

    def _tick(price, at):
        got["index"] += 1
        got["last"] = (price, at)

    async def _main():
        feed = SchwabLiveStream(lambda: eval(a.client_py))  # probe-only convenience
        stop_at = asyncio.get_event_loop().time() + a.seconds
        try:
            await asyncio.wait_for(
                feed.run(SPY_BOOK_SYMBOL, [], lambda d: got.__setitem__("depth", got["depth"] + 1),
                         should_stop=lambda: asyncio.get_event_loop().time() > stop_at,
                         on_index_tick=_tick),
                timeout=a.seconds + 10)
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 — probe prints all
            print(f"probe ended: {type(e).__name__}: {e}")
        print(f"depth samples: {got['depth']}  $SPX ticks: {got['index']}  last: {got['last']}")

    asyncio.run(_main())
