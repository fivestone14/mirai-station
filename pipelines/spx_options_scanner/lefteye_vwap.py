"""
lefteye_vwap.py — computes the day's cumulative volume-weighted average price
series from 1-min bars; the fair-price line that intraday stretch is measured
against.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def compute_vwap_series(bars_1m: list[dict[str, Any]]) -> list[float]:
    """FAIR-PRICE LINE — the day's volume-weighted "fair value" that stretch is
    measured from: cumulative (typical_price * volume) / cumulative volume,
    parallel to bars."""
    out: list[float] = []
    cum_pv = 0.0
    cum_v = 0.0
    for b in bars_1m:
        tp = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        v = float(b.get("volume", 0))
        cum_pv += tp * v
        cum_v += v
        out.append(cum_pv / cum_v if cum_v > 0 else float(b["close"]))
    return out
