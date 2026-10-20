"""
stream_symbols.py — every symbol the station's one Schwab stream subscribes, in
one place: SPY's book (the order-flow sensor's control), the SPX index itself
(its live price, for Kaze) and the SPXW option symbols of the focus set.
"""
from __future__ import annotations

SPY_BOOK_SYMBOL = "SPY"          # LobConfig.control_symbol's default: the sensor's control book
SPX_INDEX_SYMBOL = "$SPX"        # the index: its last trade is the live SPX price


def spxw_symbol(expiry: str, right: str, strike: float) -> str:
    """Schwab option symbol: 6-char padded root + YYMMDD + C/P + strike*1000
    zero-padded to 8 digits, e.g. 'SPXW  260706C07475000'."""
    yy, mm, dd = expiry[2:4], expiry[5:7], expiry[8:10]
    cp = "C" if right == "call" else "P"
    return f"SPXW  {yy}{mm}{dd}{cp}{int(round(strike * 1000)):08d}"
