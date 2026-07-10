"""
spy_stream.py — the CONTROL'S FEED + the FOCUS-SET STREAM, both over the
host's existing Schwab auth (a client_factory is injected; schwab-py is an
OPTIONAL dependency — nothing else in the package needs it installed).

  * SchwabStream.run() — ONE connection carrying both subscriptions:
      - SPY top-of-book (LEVELONE_EQUITIES, bid/ask + sizes) -> DepthSamples
      - the ~30-strike SPXW focus set (LEVELONE_OPTIONS; note the option
        field enums BID_SIZE=16 / ASK_SIZE=17 differ from equities) for TRUE
        quote-revision counts on the strikes that matter.
    SPXW symbol acceptance confirmed live 2026-07-04.

One streaming connection per Schwab account — the daemon owns it; both
subscriptions ride the same StreamClient.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from .contracts import DepthSample

ET = ZoneInfo("America/New_York")


def spxw_symbol(expiry: str, right: str, strike: float) -> str:
    """Schwab option symbol: 6-char padded root + YYMMDD + C/P + strike*1000
    zero-padded to 8 digits, e.g. 'SPXW  260706C07475000'."""
    yy, mm, dd = expiry[2:4], expiry[5:7], expiry[8:10]
    cp = "C" if right == "call" else "P"
    return f"SPXW  {yy}{mm}{dd}{cp}{int(round(strike * 1000)):08d}"


class SchwabStream:
    """One connection, two subscriptions. on_depth receives DepthSamples for
    the equity symbol; on_option_quote receives raw dicts
    {symbol, bid, ask, bid_size, ask_size, ts} for the focus set."""

    def __init__(self, client_factory: Callable[[], object],
                 account_index: int = 0):
        self._factory = client_factory
        self._account_index = account_index

    async def run(self, equity_symbol: str,
                  option_symbols: Sequence[str],
                  on_depth: Callable[[DepthSample], None],
                  on_option_quote: Optional[Callable[[dict], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None) -> None:
        """Login, subscribe, pump messages until should_stop(). Raises on
        login failure (the daemon's supervisor handles backoff/restart)."""
        from schwab.streaming import StreamClient  # optional dep, lazy

        client = self._factory()
        stream = StreamClient(client, account_id=None)
        await stream.login()

        eq_fields = StreamClient.LevelOneEquityFields
        stream.add_level_one_equity_handler(
            lambda msg: _handle_equity(msg, equity_symbol, on_depth))
        await stream.level_one_equity_subs(
            [equity_symbol],
            fields=[eq_fields.SYMBOL, eq_fields.BID_PRICE, eq_fields.ASK_PRICE,
                    eq_fields.BID_SIZE, eq_fields.ASK_SIZE])

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


def _handle_equity(msg: dict, symbol: str,
                   on_depth: Callable[[DepthSample], None]) -> None:
    for item in msg.get("content", []) or []:
        if item.get("key") != symbol:
            continue
        on_depth(DepthSample(
            ts=datetime.now(tz=ET).isoformat(),
            bid=item.get("BID_PRICE"), ask=item.get("ASK_PRICE"),
            bid_size=item.get("BID_SIZE"), ask_size=item.get("ASK_SIZE")))


def _handle_option(msg: dict, on_quote: Callable[[dict], None]) -> None:
    for item in msg.get("content", []) or []:
        on_quote({"symbol": item.get("key"),
                  "bid": item.get("BID_PRICE"), "ask": item.get("ASK_PRICE"),
                  "bid_size": item.get("BID_SIZE"),
                  "ask_size": item.get("ASK_SIZE"),
                  "ts": datetime.now(tz=ET).isoformat()})


# ---------------------------------------------------------------------------
# --probe CLI (live smoke test; NOT used by the test suite)
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

    got = {"depth": 0, "opt": 0}

    async def _main():
        feed = SchwabStream(lambda: eval(a.client_py))  # probe-only convenience
        stop_at = asyncio.get_event_loop().time() + a.seconds
        try:
            await asyncio.wait_for(
                feed.run("SPY", [], lambda d: got.__setitem__("depth", got["depth"] + 1),
                         should_stop=lambda: asyncio.get_event_loop().time() > stop_at),
                timeout=a.seconds + 10)
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 — probe prints all
            print(f"probe ended: {type(e).__name__}: {e}")
        print(f"depth samples: {got['depth']}")

    asyncio.run(_main())
