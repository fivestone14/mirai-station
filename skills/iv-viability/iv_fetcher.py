#!/usr/bin/env python3
"""
iv-viability fetcher — Schwab options chain + price history, metrics, and verdict.

CLI:
    python3 iv_fetcher.py --ticker SPY
    python3 iv_fetcher.py --ticker GOOGL --strike 285 --expiry 2026-07-17 --type put
    python3 iv_fetcher.py --setup
    python3 iv_fetcher.py --rotate-key
    python3 iv_fetcher.py --wipe-credentials
    python3 iv_fetcher.py --reset-cache

Always prints `IV Analysis skill in use.....` as its first line.
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc

import vault  # noqa: E402
import iv_cache  # noqa: E402
import iv_metrics as M  # noqa: E402
import iv_synthesis as S  # noqa: E402

ANNOUNCEMENT = "IV Analysis skill in use....."

_QUIET = False
SKILL_DIR = Path(__file__).resolve().parent
RUNS_DIR = SKILL_DIR / "runs"
TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
DEFAULT_CALLBACK_URL = "https://127.0.0.1:8182"


def announce() -> None:
    if _QUIET:
        return
    print(ANNOUNCEMENT, flush=True)


# ---------------------------------------------------------------------------
# Setup / wipe / rotate flows
# ---------------------------------------------------------------------------


def cmd_setup() -> int:
    announce()
    print()
    print("Schwab API setup — one-time credential enrollment.")
    print("Credentials will be stored in the macOS Keychain (local, not iCloud).")
    print(f"Service: {vault.SERVICE}. NOT written to any config file.")
    print()
    print("Prerequisite: your Schwab app must be in 'Ready For Use' status.")
    print("If it says 'Approved - Pending', wait for Schwab approval first.")
    print()

    api_key = getpass.getpass("Schwab App Key (Client ID): ").strip()
    if not api_key:
        print("ERROR: App Key cannot be empty.", file=sys.stderr)
        return 2
    app_secret = getpass.getpass("Schwab App Secret (Client Secret): ").strip()
    if not app_secret:
        print("ERROR: App Secret cannot be empty.", file=sys.stderr)
        return 2
    callback_url = input(f"Callback URL [{DEFAULT_CALLBACK_URL}]: ").strip() or DEFAULT_CALLBACK_URL
    if not callback_url.lower().startswith("https://"):
        print("ERROR: Callback URL must be HTTPS.", file=sys.stderr)
        return 2

    print()
    print("[1/5] Storing app_key in Keychain (local)...", end=" ", flush=True)
    vault.store_credentials(api_key, app_secret, callback_url)
    print("ok")
    print("[2/5] Storing app_secret in Keychain (local)... ok")
    print("[3/5] Storing callback_url in Keychain (local)... ok")
    print("[4/5] Generating Fernet encryption key for token file... ok (in Keychain)")
    print("[5/5] Launching first OAuth handshake...")

    # Clear plaintext locals as soon as possible.
    del api_key, app_secret

    try:
        _oauth_handshake(callback_url)
    except Exception as e:
        print(f"\nERROR: OAuth handshake failed: {type(e).__name__}", file=sys.stderr)
        print(
            "Schwab likely still has your app in 'Approved - Pending' state, "
            "or the callback URL does not match exactly (check case + trailing slash).",
            file=sys.stderr,
        )
        return 3

    print()
    print("Setup complete. Run:")
    print("  python3 iv_fetcher.py --ticker SPY")
    print()
    print("Commands:")
    print("  --setup               re-run enrollment")
    print("  --rotate-key          re-encrypt token with a new Fernet key")
    print("  --wipe-credentials    delete all Keychain entries + encrypted token file")
    return 0


def _oauth_handshake(callback_url: str) -> None:
    """Run the interactive Schwab OAuth browser flow and save the token encrypted."""
    from schwab.auth import client_from_login_flow  # type: ignore

    api_key = vault.get_api_key()
    app_secret = vault.get_app_secret()
    tmp_path = SKILL_DIR / ".oauth_tmp.json"
    try:
        client_from_login_flow(
            api_key=api_key,
            app_secret=app_secret,
            callback_url=callback_url,
            token_path=str(tmp_path),
        )
        with tmp_path.open("r") as f:
            token = json.load(f)
        vault.save_token(token)
        print("      -> Token encrypted and stored. File perms: 600.")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
        del api_key, app_secret


def cmd_reauth() -> int:
    announce()
    if not vault.has_credentials():
        print("ERROR: credentials not enrolled. Run --setup first.", file=sys.stderr)
        return 2
    callback_url = vault.get_callback_url()
    print("Refreshing Schwab OAuth tokens using stored Keychain credentials...")
    try:
        _oauth_handshake(callback_url)
    except Exception as e:
        print(f"\nERROR: OAuth handshake failed: {type(e).__name__}", file=sys.stderr)
        return 3
    print("Re-auth complete. New 7-day refresh token stored.")
    return 0


def cmd_rotate_key() -> int:
    announce()
    if not vault.has_credentials():
        print("ERROR: credentials not enrolled. Run --setup first.", file=sys.stderr)
        return 2
    vault.rotate_key()
    print("Fernet key rotated. Token re-encrypted.")
    return 0


def cmd_wipe() -> int:
    announce()
    print("This will delete all Keychain entries and the encrypted token file.")
    confirm = input("Type YES to confirm: ").strip()
    if confirm != "YES":
        print("Cancelled.")
        return 1
    vault.wipe()
    print("All credentials wiped.")
    return 0


def cmd_reset_cache() -> int:
    announce()
    iv_cache.reset()
    print("IV history cache wiped.")
    return 0


# ---------------------------------------------------------------------------
# Runtime: fetch + metric + verdict
# ---------------------------------------------------------------------------


def _build_client():
    """Build a schwab-py client using token callbacks over decrypted in-memory state."""
    from schwab.auth import client_from_access_functions  # type: ignore

    api_key = vault.get_api_key()
    app_secret = vault.get_app_secret()
    token_state = {"token": vault.load_token()}

    def token_read(*args, **kwargs):
        return token_state["token"]

    def token_write(new_token, *args, **kwargs):
        token_state["token"] = new_token
        vault.save_token(new_token)

    return client_from_access_functions(
        api_key=api_key,
        app_secret=app_secret,
        token_read_func=token_read,
        token_write_func=token_write,
    )


def _fetch_chain(client, ticker: str, strike_count: int = 100,
                 from_offset: int = 1, to_offset: int = 120) -> dict:
    """
    Fetch an option chain window. Defaults: 100 strikes, next expiration out
    to 120 DTE (from_offset=1 skips same-day 0DTE).

    Falls back to 60 strikes on a 502 "Body buffer overflow" since Schwab's
    gateway caps response size for very liquid underlyings.
    """
    from schwab.client import Client  # type: ignore
    import httpx  # type: ignore

    today = date.today()
    params = dict(
        symbol=ticker,
        contract_type=Client.Options.ContractType.ALL,
        include_underlying_quote=True,
        strike_count=strike_count,
        from_date=today + timedelta(days=from_offset),
        to_date=today + timedelta(days=to_offset),
    )
    resp = client.get_option_chain(**params)
    if resp.status_code == 502 and strike_count > 60:
        # retry with a narrower strike window
        params["strike_count"] = 60
        resp = client.get_option_chain(**params)
    resp.raise_for_status()
    return resp.json()


def _fetch_leap_chain(client, ticker: str) -> dict:
    """Fetch the LEAP window: 300–420 DTE, 30 strikes around ATM."""
    return _fetch_chain(client, ticker, strike_count=30,
                        from_offset=300, to_offset=420)


def _fetch_history(client, ticker: str) -> dict:
    from schwab.client import Client  # type: ignore

    end = datetime.now(UTC)
    start = end - timedelta(days=400)
    resp = client.get_price_history_every_day(
        symbol=ticker,
        start_datetime=start,
        end_datetime=end,
    )
    resp.raise_for_status()
    return resp.json()


def _clean_greek(v, *, nonneg: bool = False):
    """Schwab returns -999 for volatility/greeks when its solver fails to converge
    (routine on deep-ITM strikes). Left raw, that sentinel masquerades as real data:
    volatility=-999 becomes iv=-9.99 (a negative σ that inverts every downstream
    stretch/shove read) and gamma=-999 becomes a giant fake magnet in the GEX
    profile. Drop the sentinel — and any impossible negative on an inherently
    non-negative field (iv/gamma/vega) — to None at the ingest boundary."""
    if v is None:
        return None
    if not math.isfinite(v):  # NaN / ±inf from a failed solve is not real data either
        return None
    if v <= -900:            # Schwab failed-solve sentinel (-999)
        return None
    if nonneg and v < 0:     # iv / gamma / vega are non-negative by definition
        return None
    return v


def _normalize_chain(chain: dict) -> dict:
    """Flatten Schwab's nested chain JSON into a simpler shape for metrics."""
    underlying = chain.get("underlying", {}) or {}
    spot = underlying.get("last") or underlying.get("mark") or underlying.get("close")
    quote_ts = underlying.get("quoteTime")
    contracts: list[dict] = []
    atm_iv_by_dte: dict[int, float] = {}

    for side_key, right in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
        exp_map = chain.get(side_key, {}) or {}
        for exp_key, strike_map in exp_map.items():
            # "2026-07-17:63" -> dte=63
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                dte = 0
            for strike_str, leg_list in strike_map.items():
                try:
                    strike = float(strike_str)
                except ValueError:
                    continue
                for leg in leg_list:
                    vol = _clean_greek(leg.get("volatility"), nonneg=True)
                    c = {
                        "right": right,
                        "strike": strike,
                        "dte": dte,
                        "bid": leg.get("bid"),
                        "ask": leg.get("ask"),
                        "bid_size": leg.get("bidSize"),
                        "ask_size": leg.get("askSize"),
                        "last": leg.get("last"),
                        "mark": leg.get("mark"),
                        "volume": leg.get("totalVolume"),
                        "open_interest": leg.get("openInterest"),
                        "iv": vol / 100.0 if vol else None,
                        "delta": _clean_greek(leg.get("delta")),
                        "gamma": _clean_greek(leg.get("gamma"), nonneg=True),
                        "theta": _clean_greek(leg.get("theta")),
                        "vega": _clean_greek(leg.get("vega"), nonneg=True),
                        "expiry": exp_key.split(":")[0],
                    }
                    # filter dead quotes
                    if (c["bid"] == 0 or c["bid"] is None) and (c["ask"] == 0 or c["ask"] is None):
                        continue
                    contracts.append(c)

    # Compute ATM IV per DTE bucket
    if spot:
        buckets: dict[int, list[tuple[float, float]]] = {}
        for c in contracts:
            if c["iv"] is None or c["iv"] <= 0:
                continue
            buckets.setdefault(c["dte"], []).append((abs(c["strike"] - spot), c["iv"]))
        for dte, pairs in buckets.items():
            pairs.sort(key=lambda p: p[0])
            if pairs:
                atm_iv_by_dte[dte] = pairs[0][1]

    return {
        "spot": spot,
        "quote_timestamp": quote_ts,
        "contracts": contracts,
        "atm_iv_by_dte": atm_iv_by_dte,
    }


def _seed_iv_cache(ticker: str, closes: list[float]) -> None:
    """
    Backfill the IV cache with rolling 20-day close-to-close HV as an IV proxy.
    This lets IV rank/percentile work on day one instead of after 20+ days.
    Only runs when the cache has < 20 entries for this ticker.
    """
    import math as _math
    import numpy as _np
    arr = _np.array([c for c in closes if c and c > 0])
    if len(arr) < 40:
        return
    log_rets = _np.diff(_np.log(arr))
    window = 20
    today = date.today()
    for i in range(window, len(log_rets)):
        chunk = log_rets[i - window:i]
        hv = float(_np.std(chunk, ddof=1) * _math.sqrt(252))
        if 0.01 <= hv <= 5.0:
            days_ago = len(log_rets) - i
            d = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            iv_cache.append(ticker, hv, today=d)


def _closes_from_history(history: dict) -> list[float]:
    candles = history.get("candles", []) or []
    return [c.get("close") for c in candles if c.get("close") is not None]


def _highest_oi_otm(contracts: list[dict], spot: float, right: str,
                     dte: int | None = None, min_oi: int = 50) -> dict | None:
    """
    Pick the OTM strike with the highest OI for a given right/dte.
    This is where MMs concentrate hedging — the real IV signal.
    Falls back to highest-volume if no strike meets min_oi.
    """
    candidates = [
        c for c in contracts
        if c["right"] == right
        and c.get("iv") is not None and c["iv"] > 0
        and (dte is None or c["dte"] == dte)
        and ((right == "call" and c["strike"] > spot * 1.01)
             or (right == "put" and c["strike"] < spot * 0.99))
    ]
    if not candidates:
        return None
    with_oi = [c for c in candidates if (c.get("open_interest") or 0) >= min_oi]
    if with_oi:
        return max(with_oi, key=lambda c: c.get("open_interest") or 0)
    # fallback: highest volume
    with_vol = [c for c in candidates if (c.get("volume") or 0) > 0]
    if with_vol:
        return max(with_vol, key=lambda c: c.get("volume") or 0)
    return None


def _top_oi_strikes(contracts: list[dict], dte: int | None, n: int = 5) -> list[dict]:
    """
    Return the top N strikes by OI for a given DTE — these are the
    attraction levels where pinning pressure and MM hedging concentrate.
    """
    pool = [
        c for c in contracts
        if (dte is None or c["dte"] == dte)
        and (c.get("open_interest") or 0) > 0
    ]
    # aggregate OI per strike across calls + puts
    by_strike: dict[float, int] = {}
    for c in pool:
        by_strike[c["strike"]] = by_strike.get(c["strike"], 0) + (c.get("open_interest") or 0)
    ranked = sorted(by_strike.items(), key=lambda x: x[1], reverse=True)[:n]
    return [{"strike": k, "total_oi": v} for k, v in ranked]


def _nearest_by_delta(contracts: list[dict], target_delta: float, right: str) -> dict | None:
    best = None
    best_dist = 999
    for c in contracts:
        if c["right"] != right:
            continue
        d = c.get("delta")
        if d is None:
            continue
        dist = abs(abs(d) - target_delta)
        if dist < best_dist:
            best_dist = dist
            best = c
    if best_dist > 0.08:
        return None
    return best


def _strikes_oi_for_max_pain(contracts: list[dict], dte: int) -> dict[float, dict[str, int]]:
    out: dict[float, dict[str, int]] = {}
    for c in contracts:
        if c["dte"] != dte:
            continue
        k = c["strike"]
        out.setdefault(k, {"call_oi": 0, "put_oi": 0})
        oi = c.get("open_interest") or 0
        if c["right"] == "call":
            out[k]["call_oi"] += oi
        else:
            out[k]["put_oi"] += oi
    return out


def _gex_profile(contracts: list[dict], spot: float) -> dict[float, float]:
    profile: dict[float, float] = {}
    for c in contracts:
        gamma = c.get("gamma")
        oi = c.get("open_interest")
        if gamma is None or oi is None or oi <= 0:
            continue
        sign = 1 if c["right"] == "call" else -1
        gex = sign * gamma * oi * 100 * spot * spot
        profile[c["strike"]] = profile.get(c["strike"], 0) + gex
    return profile


def _recommend_leap(leap_chain: dict, direction: str, spot: float) -> dict | None:
    """
    Pick a ~0.75-delta LEAP call (bullish) or put (bearish) from the LEAP
    chain. Returns None for neutral direction or empty chain.
    """
    if direction not in ("bullish", "bearish") or not leap_chain:
        return None
    norm = _normalize_chain(leap_chain)
    right = "call" if direction == "bullish" else "put"
    candidates = [
        c for c in norm["contracts"]
        if c["right"] == right
        and c.get("delta") is not None
        and c.get("open_interest") is not None
        and c["dte"] >= 300
    ]
    if not candidates:
        return None
    target_delta = 0.75
    candidates.sort(key=lambda c: abs(abs(c["delta"]) - target_delta))
    best = candidates[0]
    mid = ((best.get("bid") or 0) + (best.get("ask") or 0)) / 2 or best.get("mark")
    intrinsic = (max(spot - best["strike"], 0.0) if right == "call"
                 else max(best["strike"] - spot, 0.0))
    extrinsic_pct = ((mid - intrinsic) / mid * 100) if mid and mid > 0 else None
    return {
        "direction": direction,
        "right": right,
        "strike": best["strike"],
        "expiry": best["expiry"],
        "dte": best["dte"],
        "delta": best["delta"],
        "mid": mid,
        "bid": best.get("bid"),
        "ask": best.get("ask"),
        "iv": best.get("iv"),
        "vega": best.get("vega"),
        "open_interest": best.get("open_interest"),
        "volume": best.get("volume"),
        "extrinsic_pct": extrinsic_pct,
        "tradable": (best.get("open_interest") or 0) >= 50,
    }


def _check_earnings_proximity(raw_chain: dict) -> bool:
    """
    Check if earnings are within 48 hours by parsing the underlying quote
    from the Schwab chain response. Returns True if earnings are imminent.
    """
    underlying = raw_chain.get("underlying", {}) or {}
    # Schwab returns earningsDate as epoch ms or ISO string depending on version
    ed = underlying.get("earningsDate")
    if not ed:
        return False
    try:
        if isinstance(ed, (int, float)) and ed > 1e9:
            earnings_dt = datetime.fromtimestamp(ed / 1000, tz=ZoneInfo("America/New_York"))
        elif isinstance(ed, str):
            earnings_dt = datetime.fromisoformat(ed.replace("Z", "+00:00"))
        else:
            return False
        now = datetime.now(tz=ZoneInfo("America/New_York"))
        hours_until = (earnings_dt - now).total_seconds() / 3600
        return 0 <= hours_until <= 48
    except (ValueError, TypeError, OSError):
        return False


def _format_quote_age(ts_ms: int | None) -> tuple[str, bool]:
    if not ts_ms:
        return "(unknown)", True
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("America/New_York"))
    except (OSError, ValueError):
        return "(unparseable)", True
    age = datetime.now(tz=ZoneInfo("America/New_York")) - dt
    stale = age > timedelta(hours=24)
    label = f"{dt.strftime('%Y-%m-%d %H:%M:%S ET')}"
    label += " (stale)" if stale else " (live)"
    return label, stale


def cmd_run(args) -> int:
    announce()
    vault.install_runtime_hardening()

    ticker = args.ticker.upper()
    if not TICKER_RE.match(ticker):
        print(f"ERROR: invalid ticker '{ticker}' (must match {TICKER_RE.pattern})", file=sys.stderr)
        return 2

    if not vault.has_credentials():
        print("ERROR: credentials not enrolled. Run: python3 iv_fetcher.py --setup", file=sys.stderr)
        return 2
    if not vault.token_file_exists():
        print("ERROR: encrypted token file missing. Run: python3 iv_fetcher.py --setup", file=sys.stderr)
        return 2

    try:
        client = _build_client()
    except vault.VaultError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        raw_chain = _fetch_chain(client, ticker)
        raw_history = _fetch_history(client, ticker)
    except Exception as e:
        print(f"ERROR: Schwab fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 4

    chain = _normalize_chain(raw_chain)
    spot = chain["spot"]
    contracts = chain["contracts"]
    if not contracts or not spot:
        print(f"UNKNOWN TICKER OR NO LISTED OPTIONS: {ticker}", file=sys.stderr)
        return 5

    quote_label, stale = _format_quote_age(chain.get("quote_timestamp"))

    # Realized vol + IV rank history
    closes = _closes_from_history(raw_history)
    # Pick the shortest expiry >= 7 days out for ATM IV. 0-1 DTE options
    # carry very noisy IV (near-zero premium on deep OTM, pin risk on ATM)
    # so they are a bad reference for regime metrics.
    all_dtes = sorted(d for d in chain["atm_iv_by_dte"].keys() if d >= 7)
    if all_dtes:
        atm_front_dte = all_dtes[0]
    elif chain["atm_iv_by_dte"]:
        atm_front_dte = min(chain["atm_iv_by_dte"].keys())
    else:
        atm_front_dte = None
    atm_iv = chain["atm_iv_by_dte"].get(atm_front_dte) if atm_front_dte is not None else None

    # Persist today's ATM IV to the cache.
    # On first run (cache < 20 entries), seed with rolling 20d HV from price
    # history as an IV proxy so rank/percentile work immediately.
    if atm_iv is not None:
        iv_cache.append(ticker, atm_iv)
    iv_history = iv_cache.read_all(ticker)
    if len(iv_history) < 20 and len(closes) >= 40:
        _seed_iv_cache(ticker, closes)
        iv_history = iv_cache.read_all(ticker)

    # Compute metrics
    metrics: dict[str, M.Metric] = {}
    metrics["iv_rank"] = M.iv_rank(atm_iv, iv_history)
    metrics["iv_percentile"] = M.iv_percentile(atm_iv, iv_history)
    metrics["vrp"] = M.vol_risk_premium(atm_iv, closes, 30)
    metrics["vrp10"] = M.vol_risk_premium(atm_iv, closes, 10)
    metrics["term_structure"] = M.term_structure_slope(chain["atm_iv_by_dte"])
    metrics["front_month_premium"] = M.front_month_premium(chain["atm_iv_by_dte"])

    put_25 = _nearest_by_delta(contracts, 0.25, "put")
    call_25 = _nearest_by_delta(contracts, 0.25, "call")
    metrics["risk_reversal"] = M.risk_reversal_25d(
        put_25["iv"] if put_25 else None,
        call_25["iv"] if call_25 else None,
        int(put_25["open_interest"]) if put_25 else 0,
        int(call_25["open_interest"]) if call_25 else 0,
    )

    # Put skew fit on front expiry
    front_puts = [c for c in contracts if c["right"] == "put" and c["dte"] == atm_front_dte and c["iv"]]
    metrics["put_skew"] = M.put_skew_steepness(
        [c["strike"] for c in front_puts],
        [c["iv"] for c in front_puts],
        spot,
    )

    # Wing strikes: pick by highest OI (where MMs hedge), not by distance.
    # These are the strikes the market actually prices — the real skew signal.
    otm_call = _highest_oi_otm(contracts, spot, "call", dte=atm_front_dte)
    otm_put = _highest_oi_otm(contracts, spot, "put", dte=atm_front_dte)
    metrics["call_skew"] = M.call_skew(atm_iv, otm_call["iv"] if otm_call else None)
    metrics["wing_richness"] = M.wing_richness(
        atm_iv,
        otm_put["iv"] if otm_put else None,
        otm_call["iv"] if otm_call else None,
    )

    # Attraction levels: top 5 strikes by OI on the front expiry — where
    # pinning, gamma hedging, and MM positioning concentrate.
    attraction = _top_oi_strikes(contracts, atm_front_dte, n=5)

    # Flow
    total_put_vol = sum(c.get("volume") or 0 for c in contracts if c["right"] == "put")
    total_call_vol = sum(c.get("volume") or 0 for c in contracts if c["right"] == "call")
    metrics["pc_vol"] = M.put_call_volume_ratio(total_put_vol, total_call_vol)
    metrics["delta_flow"] = M.delta_weighted_volume(contracts)
    metrics["max_pain"] = M.max_pain(_strikes_oi_for_max_pain(contracts, atm_front_dte or 0), spot)
    metrics["gex"] = M.net_dealer_gex(contracts, spot)
    metrics["gamma_flip"] = M.gamma_flip_level(_gex_profile(contracts, spot))

    # Contract-specific (optional)
    contract_spec = None
    if args.strike and args.expiry and args.type:
        right = args.type.lower()
        target = next(
            (c for c in contracts
             if c["right"] == right
             and abs(c["strike"] - args.strike) < 0.01
             and c["expiry"] == args.expiry),
            None,
        )
        if target is None:
            avail_expiries = sorted(set(c["expiry"] for c in contracts))
            avail_strikes = sorted(set(c["strike"] for c in contracts if c["expiry"] == args.expiry))
            print(f"ERROR: contract not found: {ticker} {args.strike} {args.expiry} {right}",
                  file=sys.stderr)
            if avail_strikes:
                nearest = sorted(avail_strikes, key=lambda s: abs(s - args.strike))[:5]
                print(f"  nearest strikes for {args.expiry}: {nearest}", file=sys.stderr)
            else:
                print(f"  available expiries: {avail_expiries[:10]}", file=sys.stderr)
            return 6
        contract_spec = target
        mid = ((target["bid"] or 0) + (target["ask"] or 0)) / 2 if target["bid"] else target.get("mark")
        metrics["spread"] = M.bid_ask_spread_pct(target["bid"], target["ask"])
        metrics["vega_theta"] = M.vega_theta_ratio(target["vega"], target["theta"])
        metrics["extrinsic"] = M.extrinsic_pct(mid, spot, target["strike"], right)
        if mid and target["strike"]:
            be = target["strike"] + mid if right == "call" else target["strike"] - mid
            metrics["breakeven"] = M.break_even_vs_expected(be, spot, atm_iv, target["dte"])

    metrics["iv_crush"] = M.iv_crush_pnl_estimate(
        atm_iv,
        chain["atm_iv_by_dte"].get(max(chain["atm_iv_by_dte"].keys())) if chain["atm_iv_by_dte"] else None,
        contract_spec["vega"] if contract_spec else None,
    )

    # Synthesis — check if earnings are within 48h from the underlying quote
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    event_within_48h = _check_earnings_proximity(raw_chain)
    increases_vega = contract_spec is not None and (contract_spec.get("vega") or 0) > 0
    verdict = S.synthesize(metrics, now_et, event_within_48h, increases_vega,
                            dte=contract_spec["dte"] if contract_spec else None)

    # LEAP recommendation — fetch a separate 300-420 DTE chain and pick a ~0.75d leg
    leap_pick = None
    if args.leap:
        try:
            leap_chain = _fetch_leap_chain(client, ticker)
            leap_pick = _recommend_leap(leap_chain, verdict.direction, spot)
        except Exception as e:
            print(f"WARNING: LEAP chain fetch failed: {type(e).__name__}: {e}", file=sys.stderr)

    # Print output
    _print_output(ticker, spot, quote_label, stale, metrics, verdict, args.verbose, leap_pick, attraction)

    # Persist artifact
    _persist_artifact(ticker, raw_chain, raw_history, metrics, verdict, leap_pick)
    return 0


def _metric_line(name: str, m: M.Metric) -> str:
    bias_tag = f"[{m.bias}]" if m.bias else "[--]"
    return f"- {name}: {m.reason} {bias_tag}"


# Human-friendly labels for the slim signal table.
# Kept to ~20 chars so they fit the two-column layout.
_SLIM_LABEL = {
    "iv_rank":             "IV rank (1yr)",
    "iv_percentile":       "IV percentile (1yr)",
    "vrp":                 "IV vs 30d realized",
    "vrp10":               "IV vs 10d realized",
    "term_structure":      "term structure",
    "front_month_premium": "front-month premium",
    "risk_reversal":       "25Δ risk reversal",
    "put_skew":            "put skew slope",
    "call_skew":           "call skew",
    "wing_richness":       "wing IV vs ATM",
    "spread":              "bid/ask spread",
    "vega_theta":          "vega/theta ratio",
    "extrinsic":           "extrinsic %",
    "breakeven":           "break-even vs 1SD",
    "pc_vol":              "put/call volume",
    "delta_flow":          "delta-weighted vol",
    "max_pain":            "max pain",
    "gex":                 "dealer gamma",
    "gamma_flip":          "gamma flip level",
    "iv_crush":            "IV crush estimate",
}
_LABEL_WIDTH = 21


def _punchline(m: M.Metric) -> str:
    """
    Reformat a metric's reason for the slim signals table:
    takes 'numeric details — punchline' and returns 'punchline · details'
    with the punchline first so the trader scans left-to-right to see WHY.
    Falls back to the raw reason if there's no em-dash separator.
    """
    reason = m.reason or ""
    if " — " in reason:
        details, _, phrase = reason.partition(" — ")
        return f"{phrase} ({details})"
    return reason


def _print_slim(
    ticker: str,
    spot: float,
    quote_label: str,
    stale: bool,
    metrics: dict[str, M.Metric],
    verdict: S.Verdict,
    leap_pick: dict | None,
    attraction: list[dict] | None = None,
) -> None:
    print()
    print(f"{ticker} @ {spot:.2f} · {quote_label}")
    if stale:
        print("  WARNING: quote is stale (>24h old or market closed)")

    print()
    print("─" * 60)
    print(f"VERDICT: {verdict.headline} · {verdict.direction} → {verdict.position_type}")
    print(f"  tally: {verdict.buy_count} BUY / {verdict.sell_count} SELL "
          f"({verdict.counted} counted)")
    if verdict.dimensions_disagree:
        for d in verdict.dimensions_disagree:
            print(f"  conflict: {d}")
    print("─" * 60)

    # Driving signals — grouped by bias with punchline-first reasons
    buy_drivers = [(k, m) for k, m in metrics.items() if m.counts() and m.bias == "BUY"]
    sell_drivers = [(k, m) for k, m in metrics.items() if m.counts() and m.bias == "SELL"]
    if buy_drivers or sell_drivers:
        print()
        print("signals driving verdict:")
        if buy_drivers:
            print()
            print(f"  BUY ({len(buy_drivers)})")
            for k, m in buy_drivers:
                label = _SLIM_LABEL.get(k, k)
                print(f"    {label:<{_LABEL_WIDTH}} {_punchline(m)}")
        if sell_drivers:
            print()
            print(f"  SELL ({len(sell_drivers)})")
            for k, m in sell_drivers:
                label = _SLIM_LABEL.get(k, k)
                print(f"    {label:<{_LABEL_WIDTH}} {_punchline(m)}")

    # Regime block — key context reads that aren't tagged buy/sell
    regime_lines = _regime_lines(metrics, spot)
    if regime_lines:
        print()
        print("regime:")
        for line in regime_lines:
            print(f"  {line}")

    # Attraction levels — where MMs and volume concentrate
    if attraction:
        print()
        print("attraction levels (top OI strikes, front expiry):")
        for a in attraction:
            delta_spot = a["strike"] - spot
            marker = " ◄ nearest" if abs(delta_spot) == min(abs(x["strike"] - spot) for x in attraction) else ""
            print(f"  ${a['strike']:<10.2f} OI {a['total_oi']:>8,}{marker}")

    # Skipped metrics footer
    skipped = [_SLIM_LABEL.get(k, k) for k, m in metrics.items() if m.confidence == "none"]
    low_conf = [_SLIM_LABEL.get(k, k) for k, m in metrics.items() if m.confidence == "low"]
    footer = []
    if skipped:
        footer.append(f"skipped: {', '.join(skipped)}")
    if low_conf:
        footer.append(f"low-confidence: {', '.join(low_conf)}")
    if footer:
        print()
        print("  " + " · ".join(footer))

    # Gate flags
    if verdict.gate_fired:
        print()
        print("  ! Pre-Trade IV Check (BUY-only override):")
        for f in verdict.gate_fired:
            print(f"    - {f}")
        print("    origin: GOOGL Apr 9 put roll")

    # LEAP recommendation — compact form
    if leap_pick is not None:
        print()
        print("LEAP RECOMMENDATION")
        print(f"  {leap_pick['direction']} · long {leap_pick['delta']:+.2f}Δ {leap_pick['right']}")
        mid = leap_pick.get('mid') or 0
        b = leap_pick.get('bid')
        a = leap_pick.get('ask')
        print(f"  {ticker} {leap_pick['expiry']} ${leap_pick['strike']:.2f}"
              f"{leap_pick['right'][0].upper()} · ${mid:.2f} mid"
              f" (bid {b} / ask {a})")
        iv = leap_pick.get('iv')
        vega = leap_pick.get('vega')
        ex = leap_pick.get('extrinsic_pct')
        parts = []
        if iv is not None:
            parts.append(f"IV {iv:.1%}")
        if vega is not None:
            parts.append(f"vega {vega:+.2f}")
        if ex is not None:
            parts.append(f"{ex:.0f}% extrinsic")
        if parts:
            print("  " + " · ".join(parts))
        oi = leap_pick.get('open_interest')
        vol = leap_pick.get('volume')
        liq = "tradable" if leap_pick['tradable'] else "thin — check book"
        suffix = ""
        if vol is not None and vol < 10:
            suffix = " (low volume, use limit)"
        print(f"  OI {oi} · vol {vol} · {liq}{suffix}")


def _regime_lines(metrics: dict[str, M.Metric], spot: float) -> list[str]:
    """Build the context/regime block — no buy/sell, just the reads."""
    lines = []

    # IV + term + RR
    iv_parts = []
    vrp = metrics.get("vrp")
    if vrp and vrp.value is not None:
        # vrp.reason already has the IV and HV numbers; strip the tail
        iv_parts.append(vrp.reason.split(" — ")[0])
    term = metrics.get("term_structure")
    if term and term.value is not None:
        label = "flat"
        if term.value > 0.02:
            label = "backwardation"
        elif term.value < -0.01:
            label = "contango"
        iv_parts.append(f"term {label} ({term.value*100:+.1f}pts)")
    rr = metrics.get("risk_reversal")
    if rr and rr.value is not None:
        iv_parts.append(f"RR {rr.value*100:+.1f}%")
    if iv_parts:
        lines.append(" · ".join(iv_parts))

    # Flow line
    flow_parts = []
    pc = metrics.get("pc_vol")
    if pc and pc.value is not None:
        tilt = "balanced"
        if pc.value <= 0.7:
            tilt = "call-heavy"
        elif pc.value >= 1.3:
            tilt = "put-heavy"
        flow_parts.append(f"P/C {pc.value:.2f} {tilt}")
    df = metrics.get("delta_flow")
    if df and df.value is not None:
        direction = "bullish" if df.value > 0 else ("bearish" if df.value < 0 else "flat")
        flow_parts.append(f"δ-vol {df.value:+,.0f} {direction}")
    if flow_parts:
        lines.append("flow: " + " · ".join(flow_parts))

    # Gamma line
    gamma_parts = []
    gex = metrics.get("gex")
    if gex and gex.value is not None:
        gamma_parts.append(f"GEX ${gex.value/1e9:+.1f}B")
    gflip = metrics.get("gamma_flip")
    if gflip and gflip.value is not None:
        delta_spot = spot - gflip.value
        above_below = "above" if delta_spot > 0 else "below"
        gamma_parts.append(f"flip ${gflip.value:.2f} (spot {above_below} by ${abs(delta_spot):.2f})")
    if gamma_parts:
        lines.append("gamma: " + " · ".join(gamma_parts))

    # Max pain
    mp = metrics.get("max_pain")
    if mp and mp.value is not None:
        diff = mp.value - spot
        lines.append(f"max pain: ${mp.value:.2f} ({diff:+.2f} vs spot)")

    return lines


def _print_full(
    ticker: str,
    spot: float,
    quote_label: str,
    stale: bool,
    metrics: dict[str, M.Metric],
    verdict: S.Verdict,
    leap_pick: dict | None,
) -> None:
    """Original verbose layout — every section, every metric."""
    print()
    print(f"{ticker} @ {spot:.2f}")
    print(f"Quote timestamp: {quote_label}")
    if stale:
        print("  WARNING: quote is stale (>24h old or market closed)")
    print()

    def section(label: str, keys: list[str]):
        print(f"**{label}**")
        for k in keys:
            if k in metrics:
                print(_metric_line(k, metrics[k]))
        print()

    section("Absolute IV", ["iv_rank", "iv_percentile", "vrp", "vrp10"])
    section("Term structure", ["term_structure", "front_month_premium"])
    section("Skew", ["risk_reversal", "put_skew", "call_skew", "wing_richness"])
    if any(k in metrics for k in ("spread", "vega_theta", "extrinsic", "breakeven")):
        section("Contract pricing", ["spread", "vega_theta", "extrinsic", "breakeven"])
    section("OI & volume flow", ["pc_vol", "delta_flow", "max_pain"])
    section("Gamma exposure", ["gex", "gamma_flip"])
    section("Timing", ["iv_crush"])

    print("=" * 60)
    print(f"VERDICT: {verdict.headline}")
    print(f"  direction:     {verdict.direction}")
    print(f"  position type: {verdict.position_type}")
    print(f"  tally:         {verdict.buy_count} BUY / {verdict.sell_count} SELL "
          f"({verdict.counted} counted)")
    if verdict.dimensions_disagree:
        print("  disagreement:")
        for d in verdict.dimensions_disagree:
            print(f"    - {d}")
    if verdict.gate_fired:
        print()
        print("  >> Pre-Trade IV Check FLAGS (BUY-only override):")
        for f in verdict.gate_fired:
            print(f"    ! {f}")
        print("  Origin: GOOGL Apr 9 put roll — correct structure, wrong timing.")
    print("=" * 60)


def _print_output(
    ticker: str,
    spot: float,
    quote_label: str,
    stale: bool,
    metrics: dict[str, M.Metric],
    verdict: S.Verdict,
    verbose: bool,
    leap_pick: dict | None = None,
    attraction: list[dict] | None = None,
) -> None:
    if verbose:
        _print_full(ticker, spot, quote_label, stale, metrics, verdict, leap_pick)
    else:
        _print_slim(ticker, spot, quote_label, stale, metrics, verdict, leap_pick, attraction)
        return  # slim path prints its own LEAP block
    # Verbose path: print LEAP block in the old format below the separator

    if leap_pick is not None:
        print()
        print("LEAP RECOMMENDATION")
        print(f"  direction:  {leap_pick['direction']} (from 0-120 DTE sentiment)")
        print(f"  structure:  long {leap_pick['right']} (~0.75Δ stock replacement)")
        print(f"  strike:     ${leap_pick['strike']:.2f}")
        print(f"  expiry:     {leap_pick['expiry']} ({leap_pick['dte']} DTE)")
        print(f"  delta:      {leap_pick['delta']:+.2f}")
        if leap_pick.get('mid'):
            print(f"  mid:        ${leap_pick['mid']:.2f}"
                  f"  (bid {leap_pick.get('bid')}, ask {leap_pick.get('ask')})")
        if leap_pick.get('iv') is not None:
            print(f"  IV:         {leap_pick['iv']:.2%}")
        if leap_pick.get('vega') is not None:
            print(f"  vega:       {leap_pick['vega']:+.2f}")
        if leap_pick.get('extrinsic_pct') is not None:
            print(f"  extrinsic:  {leap_pick['extrinsic_pct']:.0f}% of premium")
        print(f"  OI / vol:   {leap_pick.get('open_interest')} / {leap_pick.get('volume')}"
              f"  {'tradable' if leap_pick['tradable'] else 'thin liquidity — check book'}")


def _persist_artifact(
    ticker: str,
    raw_chain: dict,
    raw_history: dict,
    metrics: dict[str, M.Metric],
    verdict: S.Verdict,
    leap_pick: dict | None = None,
) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS_DIR / f"{ticker}_{ts}.json"
    payload = {
        "ticker": ticker,
        "timestamp": ts,
        "metrics": {
            k: {"value": m.value, "confidence": m.confidence, "reason": m.reason, "bias": m.bias}
            for k, m in metrics.items()
        },
        "verdict": {
            "headline": verdict.headline,
            "direction": verdict.direction,
            "position_type": verdict.position_type,
            "buy_count": verdict.buy_count,
            "sell_count": verdict.sell_count,
            "counted": verdict.counted,
            "gate_fired": verdict.gate_fired,
            "dimensions_disagree": verdict.dimensions_disagree,
        },
        "leap_pick": leap_pick,
    }
    path.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IV viability fetcher")
    p.add_argument("--ticker", help="ticker symbol, e.g. SPY")
    p.add_argument("--strike", type=float, help="contract strike (optional)")
    p.add_argument("--expiry", help="contract expiry YYYY-MM-DD (optional)")
    p.add_argument("--type", choices=["call", "put"], help="call or put (optional)")
    p.add_argument("--verbose", action="store_true", help="print raw metric values")
    p.add_argument("--leap", action="store_true",
                   help="also recommend a LEAP (~365 DTE) structure based on the 0-100 DTE sentiment")
    p.add_argument("--setup", action="store_true", help="run one-time credential enrollment")
    p.add_argument("--reauth", action="store_true", help="refresh OAuth tokens using existing Keychain credentials (no re-prompt)")
    p.add_argument("--rotate-key", action="store_true", help="rotate the Fernet encryption key")
    p.add_argument("--wipe-credentials", action="store_true", help="delete all credentials")
    p.add_argument("--reset-cache", action="store_true", help="wipe IV history cache")
    p.add_argument("--quiet", action="store_true", help="suppress the leading 'IV Analysis skill in use.....' banner")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    global _QUIET
    _QUIET = bool(args.quiet)
    try:
        vault.install_runtime_hardening()
    except vault.VaultError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.setup:
        return cmd_setup()
    if args.reauth:
        return cmd_reauth()
    if args.rotate_key:
        return cmd_rotate_key()
    if args.wipe_credentials:
        return cmd_wipe()
    if args.reset_cache:
        return cmd_reset_cache()
    if not args.ticker:
        print("ERROR: --ticker required (or use --setup / --wipe-credentials / --rotate-key / --reset-cache)",
              file=sys.stderr)
        return 2
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
