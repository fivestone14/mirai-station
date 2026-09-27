"""The station's Schwab login, borrowed: minute bars and quotes for the SPX feeds (bars.py, market_context.py,
overnight.py).

The client is the one every station job uses, ``lefteye_fetcher._client()`` over iv-viability's
vault (it saves a refreshed token the way the station always does, and nothing else). The lob-flow
daemon's streamer is never touched: these are REST calls. Imported only when a feed runs, so the
labeller, the service and the tests never need schwab-py or the vault.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LEFT_EYE = Path(__file__).resolve().parents[2] / "mirai-left-eye"
QUOTE_BATCH = 25            # symbols per quote call
CALL_SPACING_S = 0.5        # pause between calls, so a minute's snapshot never bursts the rate limit


def _fetcher():
    if str(LEFT_EYE) not in sys.path:
        sys.path.insert(0, str(LEFT_EYE))
    import lefteye_fetcher
    return lefteye_fetcher


def minute_bars(symbol: str, start: datetime, end: datetime, extended_hours: bool = False) -> list[dict]:
    """Schwab 1-minute candles for ``symbol`` between two aware datetimes, in the station's bar shape
    ``{ts (ET, bar start), open, high, low, close, volume}``. Regular hours only unless ``extended_hours``;
    a future's history comes back whole either way. An empty answer is an empty list, not an error."""
    return _bars("get_price_history_every_minute", symbol, start, end, extended_hours)


def five_minute_bars(symbol: str, start: datetime, end: datetime, extended_hours: bool = False) -> list[dict]:
    """minute_bars in 5-minute candles, which Schwab keeps about five times further back."""
    return _bars("get_price_history_every_five_minutes", symbol, start, end, extended_hours)


def _bars(method: str, symbol: str, start: datetime, end: datetime, extended_hours: bool) -> list[dict]:
    fetcher = _fetcher()
    resp = getattr(fetcher._client(), method)(
        symbol=fetcher._schwab_symbol(symbol), start_datetime=start.astimezone(timezone.utc),
        end_datetime=end.astimezone(timezone.utc), need_extended_hours_data=extended_hours)
    resp.raise_for_status()
    candles = (resp.json() or {}).get("candles") or []
    return [b for c in candles if (b := fetcher._candle_to_bar(c)) is not None]


def quotes(symbols: list[str]) -> dict[str, dict]:
    """``{symbol: Schwab's quote block}`` in batches of QUOTE_BATCH. A future answers under its front
    contract's name ("/ES" comes back as "/ESZ26"), so an answer is filed under the symbol asked for."""
    client = _fetcher()._client()
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), QUOTE_BATCH):
        if i:
            time.sleep(CALL_SPACING_S)
        batch = symbols[i:i + QUOTE_BATCH]
        resp = client.get_quotes(batch)
        resp.raise_for_status()
        answer = resp.json() or {}
        for sym in batch:
            key = _answer_key(sym, answer)
            block = (answer.get(key) or {}).get("quote") if key else None
            if isinstance(block, dict):
                out[sym] = block
    return out


def front_contracts(roots: list[str]) -> dict[str, str]:
    """``{"/ES": "/ESZ26"}``: the contract Schwab answers each futures root with, from one quote call."""
    answer = _fetcher()._client().get_quotes(list(roots))
    answer.raise_for_status()
    keys = answer.json() or {}
    return {root: key for root in roots if (key := _answer_key(root, keys)) is not None}


def _answer_key(sym: str, answer: dict) -> str | None:
    """The key Schwab filed ``sym``'s answer under: itself, or for a futures root its front contract."""
    return sym if sym in answer else next((k for k in answer if sym.startswith("/") and k.startswith(sym)), None)
