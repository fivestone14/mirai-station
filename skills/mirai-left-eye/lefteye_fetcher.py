"""
lefteye_fetcher.py — the DATA RUNNER: Schwab daily-bar + quote fetcher for /mirai-left-eye.
(Fetches raw market data on request — price bars, quotes, option chains, VIX.)

Reuses iv-viability's authenticated Schwab client. Provides:
    - daily_bars(ticker, days)  — list of normalized daily bars
    - quote_open(ticker)        — today's open price (None pre-open)
    - intraday_bars / live_spot / chain_snapshot — the lens + gravity engine feeds
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Reuse iv-viability's authenticated client (sibling skill in this plugin)
IV_DIR = Path(__file__).resolve().parent.parent / "iv-viability"
if str(IV_DIR) not in sys.path:
    sys.path.insert(0, str(IV_DIR))

from iv_fetcher import _build_client, _fetch_history, _fetch_chain, _normalize_chain  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# Schwab rejects bare index symbols (SPX) on chain/history endpoints —
# they require the $-prefixed form. User-facing API stays clean; rewrite
# happens only at the Schwab boundary. Mirrors /bet's same fix.
_SCHWAB_SYMBOL_MAP = {
    "SPX": "$SPX",
}


def _schwab_symbol(ticker: str) -> str:
    return _SCHWAB_SYMBOL_MAP.get(ticker, ticker)


# Per-client singleton cache so multi-ticker scans don't re-auth on every call.
_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = _build_client()
    return _CLIENT


def _candle_to_bar(c: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Schwab candle into the daily-bar shape the modules expect."""
    o = c.get("open")
    h = c.get("high")
    l = c.get("low")
    cl = c.get("close")
    v = c.get("volume") or 0
    epoch_ms = c.get("datetime")
    if None in (o, h, l, cl) or epoch_ms is None:
        return None
    ts = datetime.fromtimestamp(int(epoch_ms) / 1000, tz=UTC).astimezone(ET)
    return {
        "ts": ts.isoformat(),
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(cl),
        "volume": float(v),
    }


def daily_bars(ticker: str, days: int = 10) -> list[dict[str, Any]]:
    """
    Returns the most recent `days` daily bars for `ticker`, ascending by ts.

    Excludes today if the session is in progress (i.e., the most-recent
    candle's date == today's ET date) — left-eye reasons on COMPLETED
    sessions for the prior_session signal. Today's intraday state comes
    from the quote, not the daily bar.
    """
    raw = _fetch_history(_client(), _schwab_symbol(ticker))
    candles = raw.get("candles", []) or []
    bars = [b for c in candles if (b := _candle_to_bar(c)) is not None]
    today_et = datetime.now(tz=ET).date()
    bars = [
        b for b in bars
        if datetime.fromisoformat(b["ts"]).date() != today_et
    ]
    return bars[-days:] if len(bars) > days else bars


def live_spot(ticker: str) -> float | None:
    """Live last (mark/close fallback) for `ticker`, $-mapping index symbols.

    Used by the gamma SPY-OI proxy to derive the index↔SPY price scale; cheap
    single-quote call, never the full chain."""
    try:
        sym = _schwab_symbol(ticker)
        r = _client().get_quote(sym)
        if r.status_code != 200:
            return None
        q = ((r.json() or {}).get(sym) or {}).get("quote") or {}
        px = q.get("lastPrice") or q.get("mark") or q.get("closePrice")
        return float(px) if px else None
    except Exception:
        return None


def intraday_bars(ticker: str) -> list[dict[str, Any]]:
    """
    Today's 1-min bars from market open through now (RTH only by default).
    Returns the same {ts, open, high, low, close, volume} shape as daily_bars().
    """
    from datetime import timedelta
    today_et = datetime.now(tz=ET).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today_et.replace(hour=9, minute=0)        # include early pre-open if returned
    end = today_et + timedelta(days=1)
    resp = _client().get_price_history_every_minute(
        symbol=_schwab_symbol(ticker),
        start_datetime=start.astimezone(UTC),
        end_datetime=end.astimezone(UTC),
        need_extended_hours_data=False,
    )
    resp.raise_for_status()
    raw = resp.json() or {}
    candles = raw.get("candles", []) or []
    return [b for c in candles if (b := _candle_to_bar(c)) is not None]


def chain_snapshot(ticker: str, strike_count: int = 30, dte_max: int = 7) -> dict[str, Any]:
    """
    Fetch + normalize a near-the-money chain window for 0DTE hunting.
    Defaults: 30 strikes, 0-7 DTE window.
    """
    raw = _fetch_chain(_client(), _schwab_symbol(ticker), strike_count=strike_count,
                       from_offset=0, to_offset=dte_max)
    return _normalize_chain(raw)


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="lefteye fetcher smoke test")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--days", type=int, default=6)
    args = ap.parse_args()

    print(json.dumps({"live_spot": live_spot(args.ticker),
                      "daily_bars": daily_bars(args.ticker, days=args.days)},
                     indent=2, default=str))
