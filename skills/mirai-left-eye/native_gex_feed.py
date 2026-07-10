"""
native_gex_feed.py — native SPX GEX inputs from ThetaData, over a direct MCP-HTTP call.

The native GRAVITY feed + the FLOW SENSOR: gives GexBox (the gravity engine) a
*native* index option chain (retiring the SPY×10 proxy) and the signed "aggressor"
flow tilt — the live force that can overpower the gravity map's pull — without
spawning a claude subprocess. Read-only market data. Produces the `gex_theta`
(native gravity+flow) snapshot.

Security posture (audited):
  * the bearer lives ONLY in the Keychain (vault.get_cassandra_token) and only ever
    travels in the Authorization header — never in a URL, log, env var, or return value;
  * the httpx.Response never escapes this module — only normalized primitives do;
  * runtime hardening (log redaction + traceback scrub) is installed before the first call.

Public surface:
    native_chain(ticker)               -> {"spot": float, "contracts": [ {...} ]} | None
    aggressor_flow(ticker, spot)       -> float in [-1, 1] | None   (0DTE ATM, signed)
    read(ticker, spot, now, budget_s)  -> build_views dict (+ aggressor_flow) | None
                                          (time-boxed so a slow feed can't stall the caller)
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

# vault lives in the sibling iv-viability skill
_IV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "iv-viability")
if _IV not in sys.path:
    sys.path.insert(0, _IV)
import vault

ENDPOINT = "https://market-research.cassandrasedge.com/mcp"   # not secret; token is separate
ROOT = {"SPX": "SPXW"}                              # 0DTE dailies trade under the SPXW root
MONTHLY_ROOT = {"SPX": "SPX"}                       # AM-settled monthlies (OpEx-week OI lives here)
STRIKE_STEP = {"SPX": 5.0, "SPXW": 5.0}             # ATM strike grid per root
_PROTOCOL = "2025-06-18"
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_ET = ZoneInfo("America/New_York")

_CLIENT: Optional[httpx.Client] = None
_SESSION: Optional[str] = None
_HARDENED = False


def _today_iso() -> str:
    return datetime.now(_ET).date().isoformat()


def _client() -> httpx.Client:
    global _CLIENT, _HARDENED
    if not _HARDENED:
        vault.install_runtime_hardening()     # redact logs + scrub tracebacks first
        _HARDENED = True
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
    return _CLIENT


def _headers(with_session: bool) -> dict:
    h = {"Authorization": f"Bearer {vault.get_cassandra_token()}",   # value stays in-header only
         "Accept": "application/json, text/event-stream",
         "Content-Type": "application/json",
         "MCP-Protocol-Version": _PROTOCOL}
    if with_session and _SESSION:
        h["mcp-session-id"] = _SESSION
    return h


def _result(resp: httpx.Response) -> Optional[dict]:
    """The JSON-RPC reply from a plain-JSON or SSE response — the event carrying
    result/error, not a trailing keep-alive/notification."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        out = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except Exception:
                    continue
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    return obj
                out = obj
        return out
    try:
        return resp.json()
    except Exception:
        return None


def _initialize() -> None:
    global _SESSION
    if not ENDPOINT.startswith("https://"):        # never speak the token over cleartext
        raise RuntimeError("native_gex_feed: refusing non-https endpoint")
    c = _client()
    r = c.post(ENDPOINT, headers=_headers(with_session=False), json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL, "capabilities": {},
                   "clientInfo": {"name": "mirai-theta-gex", "version": "1.0"}}})
    if r.status_code in (401, 403):
        raise RuntimeError(f"native_gex_feed: auth rejected ({r.status_code})")   # do not retry
    r.raise_for_status()
    _SESSION = r.headers.get("mcp-session-id")
    c.post(ENDPOINT, headers=_headers(with_session=True),
           json={"jsonrpc": "2.0", "method": "notifications/initialized"})


def _run(code: str) -> Optional[dict]:
    """Call cass_market_run(code) and return the parsed tool result dict, or None. One
    bounded re-init retry on a dropped/expired session (transport error, 400/404, or a
    200 with no result)."""
    global _SESSION
    for attempt in range(2):
        try:
            if _SESSION is None:
                _initialize()
            r = _client().post(ENDPOINT, headers=_headers(with_session=True), json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "cass_market_run", "arguments": {"code": code}}})
        except httpx.TransportError:
            _SESSION = None
            continue                                  # bounded retry on connect/read error
        if r.status_code in (401, 403):
            raise RuntimeError(f"native_gex_feed: auth rejected ({r.status_code})")
        if r.status_code in (400, 404) and attempt == 0:
            _SESSION = None                           # stale session → re-init once
            continue
        r.raise_for_status()
        payload = _result(r)
        res = payload.get("result") if isinstance(payload, dict) else None
        if res is None:                               # error body / dead session
            if attempt == 0:
                _SESSION = None
                continue
            return None
        if not isinstance(res, dict):
            return None
        sc = res.get("structuredContent")
        if isinstance(sc, dict):
            return sc
        content = res.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            txt = content[0].get("text")
            if isinstance(txt, str):
                try:
                    return json.loads(txt)
                except Exception:
                    return None
        return None
    return None


# --- server-side normalizers (run inside cass_market_run; keep payloads small) -------------

_CHAIN_CODE = """
import datetime as _dt
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
def _side(t):
    t = str(t or '').lower()
    return 'call' if t.startswith('c') else 'put' if t.startswith('p') else None
today = _dt.date.fromisoformat(%(d0)r)

async def _discover(sym):
    # nearby probe: the live spot + which expiries actually exist in the window.
    # The server answers an UNMATCHED filter with the ENTIRE chain (no error), so
    # every expiry is re-checked against the 0-7d window instead of trusted.
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiry_from': %(d0)r,
        'expiry_to': %(d1)r, 'limit': 250}))
    spot = (r.get('summary') or {}).get('underlying_price')
    exps = []
    for e in (r.get('expirations') or []):
        ed = e.get('expiration')
        try:
            dte = (_dt.date.fromisoformat(ed) - today).days
        except Exception:
            continue
        if 0 <= dte <= 7:
            exps.append(ed)
    return spot, sorted(set(exps))

async def _chunk(sym, exp, side, lo, hi, depth=0):
    # one filtered pull per expiry x side. The server clips every response at 250
    # rows WITHOUT saying so — exactly-250 back means the window was amputated:
    # split it and refetch both halves.
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiration_date': exp,
        'contract_type': side, 'strike_gte': lo, 'strike_lte': hi, 'limit': 250}))
    rows = []
    for e in (r.get('expirations') or []):
        if (e.get('expiration') or '') != exp:
            continue                      # wrong-expiry rows = the silent full-chain fallback
        rows.extend(e.get('contracts') or [])
    if len(rows) >= 250 and depth < 3:
        # INCLUSIVE halves: [lo, mid] + [mid, hi]. A gap between the halves would
        # silently drop any strike that lands in it — exactly the crowded-wall
        # strike that forced the split. The mid-strike overlap is deduped at merge.
        mid = (lo + hi) / 2.0
        return (await _chunk(sym, exp, side, lo, mid, depth + 1) +
                await _chunk(sym, exp, side, mid, hi, depth + 1))
    return rows

spot, weekly_exps = await _discover(%(root)r)
roots = {%(root)r: weekly_exps}
monthly_root = %(monthly_root)r
if monthly_root and monthly_root != %(root)r:
    # AM-settled monthlies live under their own root and carry ~10x the weekly OI
    # on OpEx weeks; outside those weeks discovery returns no in-window expiries.
    m_spot, m_exps = await _discover(monthly_root)
    spot = spot or m_spot
    if m_exps:
        roots[monthly_root] = m_exps
if not spot:
    return {'spot': None, 'contracts': []}
lo, hi = spot * 0.92, spot * 1.08
out, chunks, seen = [], 0, set()
for sym, exps in roots.items():
    for exp in exps:
        dte = (_dt.date.fromisoformat(exp) - today).days
        for side in ('call', 'put'):
            rows = await _chunk(sym, exp, side, lo, hi)
            chunks += 1
            for c in rows:
                s = _side(c.get('type'))
                if s != side:
                    continue
                key = (sym, exp, s, c.get('strike'))
                if key in seen:
                    continue      # inclusive split halves / root overlap → dedup
                seen.add(key)
                g = c.get('greeks') or {}
                out.append({'right': s, 'strike': c.get('strike'), 'dte': dte,
                            'expiry': exp, 'root': sym, 'iv': c.get('iv'),
                            'open_interest': c.get('open_interest'),
                            'volume': c.get('volume'), 'gamma': g.get('gamma'),
                            'delta': g.get('delta'), 'theta': g.get('theta'),
                            'vega': g.get('vega'), 'bid': c.get('bid'),
                            'ask': c.get('ask'), 'mark': c.get('mark'),
                            'bid_size': c.get('bid_size'), 'ask_size': c.get('ask_size')})
return {'spot': spot, 'contracts': out, 'chunks': chunks,
        'expiries': sorted(set(e for es in roots.values() for e in es))}
"""

_FLOW_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
r = await call_tool('option_trade_flow', {'symbol': %(root)r, 'expiration': %(exp)r,
    'date': %(exp)r, 'strike': %(strike)r, 'right': 'both', 'bucket_minutes': 390})
v = _v(r)
if not isinstance(v, dict) or 'by_strike' not in v:
    return {'available': False}
by = v.get('by_strike') or []
def _num(s, k): return s.get(k) or 0
cb = sum(_num(s, 'buy_dollars') for s in by if s.get('right') == 'call')
cs = sum(_num(s, 'sell_dollars') for s in by if s.get('right') == 'call')
pb = sum(_num(s, 'buy_dollars') for s in by if s.get('right') == 'put')
ps = sum(_num(s, 'sell_dollars') for s in by if s.get('right') == 'put')
gross = cb + cs + pb + ps
if gross <= 0:
    return {'available': False}          # no same-day ticks → unknown, not a balanced 0.0
# bullish aggression = buying calls + selling puts; bearish = the mirror
return {'available': True, 'flow': (((cb - cs) + (ps - pb)) / gross), 'gross': gross}
"""

# Phase 0 (open/close signed-weight fix): per-strike aggressor buys & sells across the
# whole 0DTE window (not just the ATM strike aggressor_flow reads), so the signed pin can
# weight by NET (buy − sell) instead of gross volume — a round-tripped strike (opened then
# closed) nets toward zero instead of inflating. Same NBBO quote-rule classifier as
# aggressor_flow (buyer-initiated ≥ ask, seller-initiated ≤ bid), not BVC.
_FLOWSTRIKE_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
r = await call_tool('option_trade_flow', {'symbol': %(root)r, 'expiration': %(exp)r,
    'date': %(exp)r, 'strike_from': %(lo)r, 'strike_to': %(hi)r, 'right': 'both',
    'bucket_minutes': 390})
v = _v(r)
if not isinstance(v, dict) or 'by_strike' not in v:
    return {'available': False}
out = []
for s in (v.get('by_strike') or []):
    out.append({'strike': s.get('strike'), 'right': s.get('right'),
                'buy_dollars': s.get('buy_dollars') or 0,
                'sell_dollars': s.get('sell_dollars') or 0})
return {'available': True, 'by_strike': out}
"""

_BARS_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
root = %(root)r; day = %(day)r
c0 = _v(await call_tool('options_chain', {'symbol': root, 'expiry_from': day, 'expiry_to': day, 'limit': 1}))
spot = (c0.get('summary') or {}).get('underlying_price')
if not spot:
    return {'points': []}
step = 5 if root in ('SPX', 'SPXW') else 1
strike = round(spot / step) * step
# the underlying index price rides on any ATM option's greek series (Schwab-independent)
g = _v(await call_tool('historical_option_greeks', {'symbol': root, 'expiration': day, 'strike': strike,
    'right': 'call', 'date_from': day, 'date_to': day, 'interval': '1m'}))
pts = []
for p in (g.get('points') or []):
    up = p.get('underlying_price'); ts = p.get('timestamp')
    if up and up > 0 and ts:
        pts.append({'timestamp': ts, 'underlying_price': up})
return {'points': pts}
"""


def _fill_gamma(contracts: list, spot: float) -> None:
    """Theta returns iv/delta/theta/vega but frequently null gamma; recompute per-strike
    gamma from IV with GexBox's own Black-Scholes so the gamma-weighted walls (slides B/C)
    stay populated and consistent with the flip reprice. Fills only where gamma is missing."""
    try:
        from lefteye_gex_box import _bs_gamma, _TRADING_DAYS, _TAU_FLOOR_DAYS
    except Exception:
        return
    for c in contracts:
        if c.get("gamma"):
            continue
        iv, k, dte = c.get("iv"), c.get("strike"), c.get("dte")
        if iv and iv > 0 and k and dte is not None:
            try:
                tau = max(float(dte), _TAU_FLOOR_DAYS) / _TRADING_DAYS
                c["gamma"] = _bs_gamma(spot, float(k), float(iv), tau)
                c["gamma_src"] = "bs_iv"
            except Exception:
                pass


def _parity_iv_fallback(contracts: list) -> dict:
    """NO SILENT STRIKE DROPS — deep-ITM quotes (canonically puts above spot) often
    arrive with null/zero IV because the quote is all intrinsic value. Put-call parity
    says the same-strike opposite right carries the identical implied vol, and BS gamma
    is right-symmetric, so borrowing the twin's IV is exact — the strike stays in the
    map instead of quietly tilting it call-side. Returns rescue/drop counters so the
    drop is never invisible again."""
    twin_iv = {}
    for c in contracts:
        iv = c.get("iv")
        if iv and iv > 0:
            twin_iv[(c.get("expiry"), c.get("strike"), c.get("right"))] = iv
    rescued = dropped = 0
    for c in contracts:
        iv = c.get("iv")
        if iv and iv > 0:
            continue
        other = "call" if c.get("right") == "put" else "put"
        borrowed = twin_iv.get((c.get("expiry"), c.get("strike"), other))
        if borrowed:
            c["iv"] = borrowed
            c["iv_src"] = "parity_twin"
            rescued += 1
        else:
            dropped += 1
    return {"iv_rescued": rescued, "iv_dropped": dropped}


# One chain pull can now be ~15 API round-trips; within a scan the flow-sensor read
# and the gravity engine both want the same book, so a short TTL cache serves the
# second caller for free (GexBox's own 25-min snapshot cache sits above this).
# The lock makes the fetch SINGLE-FLIGHT: a timed-out read()'s orphan thread and
# GexBox's follow-up lookup must never race two fetches over the shared MCP session.
_CHAIN_TTL_S = 240.0
_CHAIN_CACHE: dict[str, tuple] = {}
_CHAIN_LOCK = threading.Lock()


def reset_chain_cache() -> None:
    """Drop the module-level chain cache (tests / a fresh session)."""
    _CHAIN_CACHE.clear()


def cached_chain(ticker: str) -> Optional[dict]:
    """Cache-ONLY peek: return the fresh cached chain if present, else None.

    NEVER fetches — for shadow consumers (e.g. gex_uw_bridge) that must reuse the
    book the live map already pulled this tick and must not add a second fetch to
    the 60s tick budget when native is degraded/uncached.
    """
    hit = _CHAIN_CACHE.get(ticker)
    if hit and (datetime.now(_ET) - hit[0]).total_seconds() < _CHAIN_TTL_S:
        return hit[1]
    return None


def native_chain(ticker: str) -> Optional[dict]:
    """REAL CHAIN — pulls the actual SPX option book, strike by strike.
    Native 0-7 DTE index chain in GexBox's contract shape (no SPY proxy): chunked
    per-expiry×per-side fetch (beats the server's silent 250-row clip), weekly +
    monthly roots, parity-rescued IV, gamma recomputed from IV. Fill-ledger
    recency weights are annotated HERE, once per fetch — every consumer of this
    cache (flow-sensor read(), GexBox) sees the same annotated book."""
    now = datetime.now(_ET)
    hit = _CHAIN_CACHE.get(ticker)
    if hit and (now - hit[0]).total_seconds() < _CHAIN_TTL_S:
        return hit[1]
    with _CHAIN_LOCK:
        now = datetime.now(_ET)                      # re-check under the lock: the
        hit = _CHAIN_CACHE.get(ticker)               # winner of the race filled it
        if hit and (now - hit[0]).total_seconds() < _CHAIN_TTL_S:
            return hit[1]
        root = ROOT.get(ticker, ticker)
        today = now.date()
        res = _run(_CHAIN_CODE % {"root": root, "monthly_root": MONTHLY_ROOT.get(ticker),
                                  "d0": today.isoformat(),
                                  "d1": (today + timedelta(days=7)).isoformat()})
        if not res or res.get("spot") is None or not res.get("contracts"):
            return None
        spot, contracts = res["spot"], res["contracts"]
        # OpEx-Friday guard: on a monthly/quarterly expiration the AM-settled index
        # monthly (SPX root) shares TODAY's date but SETTLES AT THE 9:30 OPEN — it
        # exerts no dealer-hedging gamma intraday, yet carries ~10-50x the SPXW weekly
        # OI, so leaving its dte==0 leg in the 0DTE bucket lets a DEAD book dominate the
        # magnet/flip/walls all day (each dead strike repriced on the live 4pm clock).
        # The tradeable 0DTE is the PM-settled SPXW daily; drop only the AM-settled
        # monthly root at dte==0 (its 1-7DTE legs, if any, are still live and kept).
        am_root = MONTHLY_ROOT.get(ticker)
        am_dropped = 0
        if am_root and am_root != ROOT.get(ticker, ticker):
            kept = [c for c in contracts
                    if not (c.get("root") == am_root and c.get("dte") == 0)]
            am_dropped = len(contracts) - len(kept)
            contracts = kept
            if not contracts:
                return None
        iv_meta = _parity_iv_fallback(contracts)   # rescue ITM strikes BEFORE gamma fill
        _fill_gamma(contracts, spot)               # provider omits gamma → recompute from IV
        # Phase 0: annotate 0DTE contracts with per-strike aggressor buys/sells (net = buy−sell).
        # Best-effort + SHADOW: adds fields only; no consumer until the box signed-weight flag
        # is on, and strikes with no ticks stay unannotated (pin falls back to volume).
        try:
            if SIGNED_FLOW:
                fbs = flow_by_strike(ticker, spot)
                if fbs:
                    for c in contracts:
                        if c.get("dte") != 0 or c.get("strike") is None:
                            continue
                        rec = fbs.get((round(float(c["strike"]), 4), c.get("right")))
                        if rec:
                            c["flow_buy"] = rec["buy"]
                            c["flow_sell"] = rec["sell"]
                            c["net_flow"] = rec["net"]        # signed $; |net| used as weight
        except Exception:
            pass                                   # feed hiccup → contracts simply un-annotated
        try:
            import lefteye_fill_ledger as _fl
            _fl.annotate(f"native_{ticker}", contracts, now)
        except Exception:
            pass                                   # best-effort: pin falls back to volume
        out = {"spot": spot, "contracts": contracts,
               "meta": {"chunks": res.get("chunks"), "expiries": res.get("expiries"),
                        "am_settled_dropped": am_dropped, **iv_meta}}
        _CHAIN_CACHE[ticker] = (now, out)
        return out


def aggressor_flow(ticker: str, spot: float) -> Optional[float]:
    """FLOW SENSOR — who's hitting the buy vs sell button on today's ATM options.
    Signed 0DTE ATM flow tilt in [-1, 1] (buy calls/sell puts = +). None if no ticks yet."""
    if not spot:
        return None
    root = ROOT.get(ticker, ticker)
    step = STRIKE_STEP.get(root, 5.0)
    strike = round(spot / step) * step
    res = _run(_FLOW_CODE % {"root": root, "exp": _today_iso(), "strike": strike})
    if not res or not res.get("available") or res.get("flow") is None:
        return None
    return max(-1.0, min(1.0, float(res["flow"])))


# Phase 0 gate: fetch the per-strike signed flow. The WEIGHTING that consumes it is gated
# separately in lefteye_gex_box (shadow-first); this flag only controls the extra fetch.
SIGNED_FLOW = True


def flow_by_strike(ticker: str, spot: float) -> Optional[dict]:
    """FLOW SENSOR, per strike — {(strike, right): {'buy': $, 'sell': $, 'net': buy−sell}}
    across the 0DTE window, or None. Same NBBO quote-rule aggressor tags as aggressor_flow."""
    if not spot:
        return None
    root = ROOT.get(ticker, ticker)
    lo, hi = round(spot * 0.92), round(spot * 1.08)
    res = _run(_FLOWSTRIKE_CODE % {"root": root, "exp": _today_iso(), "lo": lo, "hi": hi})
    if not res or not res.get("available"):
        return None
    out: dict = {}
    for s in res.get("by_strike") or []:
        k, r = s.get("strike"), s.get("right")
        if k is None or r not in ("call", "put"):
            continue
        b = float(s.get("buy_dollars") or 0)
        sl = float(s.get("sell_dollars") or 0)
        out[(round(float(k), 4), r)] = {"buy": b, "sell": sl, "net": b - sl}
    return out or None


def native_bars(ticker: str, day: str | None = None) -> list:
    """Fallback 1-min index price path from ThetaData (the underlying_price carried on an
    ATM option's greek series), for when Schwab bars are unavailable. Returns
    [{ts, high, low, close}] with ET-aware ts; high==low==close since only the underlying
    price is available (no intra-minute extremes)."""
    root = ROOT.get(ticker, ticker)
    day = day or _today_iso()
    res = _run(_BARS_CODE % {"root": root, "day": day})
    if not res or not res.get("points"):
        return []
    bars = []
    for p in res["points"]:
        px, ts = p.get("underlying_price"), p.get("timestamp")
        if not (px and px > 0 and ts):
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_ET)         # ThetaData timestamps are naive ET
        except (ValueError, TypeError):
            continue
        bars.append({"ts": dt.isoformat(), "high": px, "low": px, "close": px})
    return bars


def read(ticker: str, spot: float, now: datetime, budget_s: float = 15.0,
         flow: Optional[float] = None) -> Optional[dict]:
    """REAL-DATA READ — one time-boxed trip to ThetaData: real chain + live flow,
    never allowed to stall the scan. Native GEX views for the shadow A/B, computed within a hard wall-clock budget so a
    slow feed can never stall the caller. The fetch runs in a daemon thread joined with a
    deadline (sequential inside — the shared MCP session must not be raced). Returns the
    build_views dict (with an added 'aggressor_flow' key), or None on timeout/failure.
    Pass `flow` to reuse an already-fetched FLOW SENSOR reading (skips the re-fetch —
    the caller shares one flow read between the proxy and native engines)."""
    from lefteye_gex_box import build_views, _minutes_to_close
    box: dict = {}

    def work():
        nat = native_chain(ticker)   # fill-ledger annotation happens inside the fetch
        if not nat:
            return
        f = flow if flow is not None else aggressor_flow(ticker, spot)
        v = build_views(nat["contracts"], spot, aggressor_flow=f,
                        minutes_to_close=_minutes_to_close(now))
        v["aggressor_flow"] = f
        v["native_meta"] = nat.get("meta")   # chunk count + IV rescue/drop counters
        box["v"] = v

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(budget_s)
    return box.get("v")


# --- ops helpers ---------------------------------------------------------------------------

def _setup_from_claude_json() -> None:
    """One-time: copy the market-research bearer from ~/.claude.json into the Keychain."""
    cfg = json.load(open(os.path.expanduser("~/.claude.json")))
    auth = cfg["mcpServers"]["market-research"]["headers"]["Authorization"]
    tok = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else auth
    vault.set_cassandra_token(tok)
    print("cassandra token stored:", vault.has_cassandra_token())


def probe_token() -> tuple[str, str]:
    """Lightweight authenticated health check of the Cassandra/ThetaData bearer.

    Does one `initialize` round-trip and classifies the outcome:
      ('ok', ...)            the token authenticated cleanly.
      ('auth_rejected', ...) the server returned 401/403 — the bearer is dead or
                             revoked. `native_chain` will then silently fall back
                             to the SPY×10 proxy until the token is re-minted.
      ('unknown', ...)       transient/network/other error — NOT a token verdict,
                             so callers must not page on it.

    Never raises. Resets the cached session so the probe can't be fooled by a
    stale one. Used by the daily auth-watch job to turn the previously-silent
    native degrade into a phone ping.
    """
    global _SESSION
    try:
        _SESSION = None
        _initialize()
        return ("ok", "authenticated")
    except RuntimeError as e:                       # our own explicit auth/https guards
        msg = str(e)
        return ("auth_rejected", msg) if "auth rejected" in msg else ("unknown", msg)
    except Exception as e:                          # httpx status/transport, anything else
        return ("unknown", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        _setup_from_claude_json()
    elif "--selftest" in sys.argv:
        ch = native_chain("SPX")
        n = len(ch["contracts"]) if ch else 0
        z = sum(1 for c in (ch or {}).get("contracts", []) if c.get("dte") == 0)
        print("native_chain SPX: spot", (ch or {}).get("spot"), "contracts", n, "0DTE", z)
        if ch:
            print("aggressor_flow SPX:", aggressor_flow("SPX", ch["spot"]))
    else:
        print("usage: native_gex_feed.py [--setup | --selftest]")
