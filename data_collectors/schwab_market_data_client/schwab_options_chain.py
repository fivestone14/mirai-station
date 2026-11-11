"""
schwab_options_chain.py — the real SPX options book, straight from Schwab's chain endpoint.

Added 2026-11-11: the hosted MCP (ThetaData behind cass_market_run) stopped offering
`options_chain` on 2026-11-09, which sent every scan to the SPY×10 proxy and emptied the
collector's quote sweep. Schwab's `get_option_chain("$SPX")` returns the same book in one
call (measured: 0-7 DTE, every strike, both sides, ~3,300 contracts, ~4 MB, ~2 s) with
open interest, volume, bid/ask and sizes, IV and greeks, so it is now the first source and
the MCP the second.

Two layers, so the shaping can be tested without a network:
    normalize(payload, today, ...)   pure: Schwab's JSON -> the station's contract rows
    fetch_book(client, symbol, ...)  one call for the window, per-day calls if Schwab's
                                     gateway refuses the big answer (502 body overflow)

A contract row is the shape native_gex_feed has always handed GexBox:
    {right, strike, dte, expiry, root, iv, open_interest, volume, gamma, delta, theta,
     vega, bid, ask, mark, bid_size, ask_size, symbol}

Differences from the old provider, all handled here:
  * Schwab quotes IV in percent (13.7); the station works in decimals (0.137).
  * Schwab marks "not available" with -999 (and sometimes "NaN"); those become None so the
    parity rescue and the gamma fill see a blank, never a huge negative number.
  * Schwab rounds gamma to 3 decimals (0.016), far too coarse away from the money, so gamma
    is always left None here and recomputed from IV by native_gex_feed._fill_gamma, the
    same Black-Scholes the engine reprices with.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

PROVIDER = "schwab"
INDEX_SYMBOL = {"SPX": "$SPX", "SPXW": "$SPX"}       # both roots list under the index symbol
_NA_FLOOR = -900.0                                   # Schwab's -999 "not available" marker


def _num(value) -> Optional[float]:
    """A finite number, or None for Schwab's blanks (-999, 'NaN', missing)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= _NA_FLOOR:       # NaN, or the -999 marker
        return None
    return number


def _whole(value) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None else None


def normalize(payload: dict, today: date, roots: Optional[Iterable[str]] = None,
              strike_lo: Optional[float] = None, strike_hi: Optional[float] = None,
              max_dte: Optional[int] = None) -> Optional[dict]:
    """Schwab's chain JSON -> {"spot", "contracts", "sides", "expiries", "delayed"}.

    `roots` keeps only those option roots (e.g. {"SPXW", "SPX"}); `strike_lo`/`strike_hi`
    and `max_dte` cut the window. `sides` is the per root × expiry × side row ledger the
    coverage guard reads. Returns None when the payload carries no usable spot."""
    if not isinstance(payload, dict) or payload.get("status") == "FAILED":
        return None
    spot = _num(payload.get("underlyingPrice"))
    if not spot:
        spot = _num((payload.get("underlying") or {}).get("last"))
    if not spot:
        return None
    keep_roots = set(roots) if roots else None
    contracts: list = []
    ledger: dict = {}
    for side, map_name in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
        for expiry_key, strikes in (payload.get(map_name) or {}).items():
            expiry = str(expiry_key)[:10]
            try:
                dte = (date.fromisoformat(expiry) - today).days
            except ValueError:
                continue
            if dte < 0 or (max_dte is not None and dte > max_dte):
                continue
            for rows in (strikes or {}).values():
                for row in rows or []:
                    root = str(row.get("optionRoot") or "").strip()
                    if not root:                       # older payloads: the symbol's first 6 chars
                        root = str(row.get("symbol") or "")[:6].strip()
                    if keep_roots is not None and root not in keep_roots:
                        continue
                    strike = _num(row.get("strikePrice"))
                    if strike is None:
                        continue
                    if strike_lo is not None and strike < strike_lo:
                        continue
                    if strike_hi is not None and strike > strike_hi:
                        continue
                    iv_percent = _num(row.get("volatility"))
                    contracts.append({
                        "right": side, "strike": strike, "dte": dte, "expiry": expiry,
                        "root": root,
                        "iv": (iv_percent / 100.0) if iv_percent and iv_percent > 0 else None,
                        "open_interest": _whole(row.get("openInterest")),
                        "volume": _whole(row.get("totalVolume")),
                        "gamma": None,                 # recomputed from IV downstream (see module note)
                        "delta": _num(row.get("delta")), "theta": _num(row.get("theta")),
                        "vega": _num(row.get("vega")),
                        "bid": _num(row.get("bid")), "ask": _num(row.get("ask")),
                        "mark": _num(row.get("mark")),
                        "bid_size": _whole(row.get("bidSize")),
                        "ask_size": _whole(row.get("askSize")),
                        "symbol": row.get("symbol")})
                    entry = ledger.setdefault((root, expiry, side), {"rows": 0, "dte": dte, "ks": []})
                    entry["rows"] += 1
                    entry["ks"].append(strike)
    sides = [{"root": root, "exp": expiry, "side": side, "rows": entry["rows"],
              "raw_rows": entry["rows"], "dte": entry["dte"],
              "k_lo": min(entry["ks"]), "k_hi": max(entry["ks"])}
             for (root, expiry, side), entry in sorted(ledger.items())]
    return {"spot": spot, "contracts": contracts, "sides": sides,
            "expiries": sorted({c["expiry"] for c in contracts}),
            "delayed": bool(payload.get("isDelayed"))}


def _call(client, symbol: str, d0: date, d1: date) -> Optional[dict]:
    """One chain request; the parsed JSON, or None on any non-200 answer."""
    from schwab.client import Client  # type: ignore  (lazy: the tests never need it)
    response = client.get_option_chain(
        symbol, contract_type=Client.Options.ContractType.ALL,
        strike_range=Client.Options.StrikeRange.ALL,
        include_underlying_quote=True, from_date=d0, to_date=d1)
    if response.status_code != 200:
        return None
    return response.json()


def fetch_book(client, symbol: str, today: date, days: int = 7,
               roots: Optional[Iterable[str]] = None,
               lo_frac: Optional[float] = None, hi_frac: Optional[float] = None) -> Optional[dict]:
    """The book from `today` through `today + days`, strikes within lo_frac..hi_frac of spot.

    One request for the whole window; if Schwab's gateway refuses it (a 502 on a very large
    answer) the window is asked for one weekday at a time and merged, so a wide day costs
    more calls instead of the whole book. Returns normalize()'s dict plus "chunks" (requests
    that answered) and "provider", or None when nothing usable came back."""
    def shape(payload):
        if payload is None:
            return None
        spot = _num(payload.get("underlyingPrice")) or _num((payload.get("underlying") or {}).get("last"))
        lo = spot * lo_frac if spot and lo_frac is not None else None
        hi = spot * hi_frac if spot and hi_frac is not None else None
        return normalize(payload, today, roots=roots, strike_lo=lo, strike_hi=hi, max_dte=days)

    book = shape(_call(client, symbol, today, today + timedelta(days=days)))
    chunks = 1
    if book is None and days > 0:
        merged = None
        chunks = 0
        for offset in range(days + 1):
            day = today + timedelta(days=offset)
            if day.weekday() >= 5:
                continue                               # no weekend expiries
            part = shape(_call(client, symbol, day, day))
            if part is None:
                continue
            chunks += 1
            if merged is None:
                merged = part
            else:
                merged["contracts"].extend(part["contracts"])
                merged["sides"].extend(part["sides"])
                merged["expiries"] = sorted(set(merged["expiries"]) | set(part["expiries"]))
                merged["delayed"] = merged["delayed"] or part["delayed"]
        book = merged
    if book is None:
        return None
    book["chunks"] = chunks
    book["provider"] = PROVIDER
    return book
