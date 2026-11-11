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


def parse_option_symbol(symbol: str):
    """The reverse of spxw_symbol, for any Schwab option symbol: 'SPXW  260706C07475000' ->
    ('SPXW', '2026-07-06', 'call', 7475.0), or None when the text is not an option symbol."""
    text = str(symbol or "")
    if len(text) != 21 or text[12] not in "CP":
        return None
    digits = text[6:12] + text[13:]
    if not digits.isdigit():
        return None
    root = text[:6].strip()
    if not root:
        return None
    expiry = f"20{text[6:8]}-{text[8:10]}-{text[10:12]}"
    return root, expiry, ("call" if text[12] == "C" else "put"), int(text[13:]) / 1000.0
