"""Synthetic SNDK books for the sndk-pro suite: $5 grid on a ~$1250 stock,
weekly expiries, quotes priced from Black-Scholes at a known IV so the
quote-derived IV rebuild has a recoverable truth."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sndk_feed

SPOT = 1250.0
TRUE_IV = 0.5


def leg(strike: float, right: str, dte: int, expiry: str, *,
        iv=None, oi: int = 200, vol: int = 100, spot: float = SPOT,
        quote_iv: float = TRUE_IV, mtc=None) -> dict:
    """One contract with live-looking quotes (BS mid at quote_iv vs `spot`)
    and an optionally-garbage provider `iv` (None = provider sent null).
    `mtc` prices the quote on the intraday front-book clock (M1 tests);
    None = after the close, where the engine and front-book clocks agree."""
    tau = sndk_feed._tau_front(dte, mtc)
    px = sndk_feed._bs_price(spot, strike, quote_iv, tau, right)
    bid = round(px * 0.97, 2)
    ask = round(px * 1.03, 2)
    if bid <= 0:
        bid = ask = None            # untradeably far wing: no usable quote
    return {"right": right, "strike": float(strike), "dte": dte,
            "expiry": expiry, "root": "SNDK", "iv": iv,
            "open_interest": oi, "volume": vol, "gamma": None,
            "delta": None, "theta": None, "vega": None,
            "bid": bid, "ask": ask, "mark": None,
            "bid_size": 1, "ask_size": 1}


def book(front_dte: int = 4, front_expiry: str = "2026-07-31",
         second_dte: int = 11, second_expiry: str = "2026-08-07",
         spot: float = SPOT, lo: float = 1150.0, hi: float = 1350.0,
         mtc=None) -> list:
    """Two weekly expiries, both rights, $5 grid. Provider IVs mimic the live
    pathology: garbage on the ITM side, sane-ish on the OTM side."""
    out = []
    for exp, dte in ((front_expiry, front_dte), (second_expiry, second_dte)):
        k = lo
        while k <= hi:
            call_iv = 4.9 if k < spot else 0.52      # ITM calls: stale-spot garbage
            put_iv = 0.0 if k >= spot else 0.55      # ITM puts: unsolvable → 0.0
            out.append(leg(k, "call", dte, exp, iv=call_iv, spot=spot, mtc=mtc))
            out.append(leg(k, "put", dte, exp, iv=put_iv, spot=spot, mtc=mtc))
            k += 5.0
    return out


def prepared_book(**kw) -> list:
    """A book after the feed's post-processing (IV rebuilt + gamma filled) —
    what sndk_chain hands the views layer."""
    b = book(**kw)
    spot = kw.get("spot", SPOT)
    mtc = kw.get("mtc")
    sndk_feed.rebuild_iv(b, spot, mtc)
    sndk_feed._fill_gamma(b, spot, mtc)
    return b
