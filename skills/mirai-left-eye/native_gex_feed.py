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
# read=90: a cold option_trade_flow materialization runs ~45s server-side; the old
# read=20 was one missed SSE keep-alive away from a spurious timeout whose retry
# DOUBLED the server's load at the worst moment (07-20 latency forensics). A timeout
# only bounds the wait — it adds no latency to fast calls; read()'s 15s budget is
# what protects the scan, and it abandons the thread regardless.
_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)
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
window_days = (_dt.date.fromisoformat(%(d1)r) - today).days

async def _probe_day(sym, d_iso):
    # Single-day existence ask. The server answers an UNMATCHED filter with the
    # ENTIRE chain (no error), so rows only count when their block really is the
    # asked-for expiry — a holiday probe filters to zero, never to noise.
    r = _v(await call_tool('options_chain', {'symbol': sym,
        'expiration_date': d_iso, 'limit': 25}))
    spot = (r.get('summary') or {}).get('underlying_price')
    n = 0
    for e in (r.get('expirations') or []):
        if str(e.get('expiration') or '')[:10] == d_iso:   # tolerate datetime-shaped drift
            n += len(e.get('contracts') or [])
    return spot, n > 0

async def _discover(sym):
    # DISCOVERY NEVER TRUSTS ONE RESPONSE SHAPE (2026-07-13): the bulk window ask
    # silently collapsed to a single expiry per query when the server started
    # spending its whole 250-row clip on one expiration — an entire live session
    # ran 0DTE-blind on its say-so. The bulk ask survives only as a cheap hint;
    # every day of the window the hint did not confirm is probed individually,
    # so existence is decided per-day and discovery keeps working if the bulk
    # endpoint changes shape again or disappears outright. A hint-claimed expiry
    # that turns out empty is still adjudicated downstream by the coverage guard.
    spot = None
    found = set()
    try:
        r = _v(await call_tool('options_chain', {'symbol': sym, 'expiry_from': %(d0)r,
            'expiry_to': %(d1)r, 'limit': 250}))
        spot = (r.get('summary') or {}).get('underlying_price')
        for e in (r.get('expirations') or []):
            ed = str(e.get('expiration') or '')[:10]   # tolerate datetime-shaped drift
            try:
                dte = (_dt.date.fromisoformat(ed) - today).days
            except Exception:
                continue
            # a hint block only confirms a day when it carries CONTRACTS — a drift
            # to listing-only blocks must fall through to the probe, not skip it
            if 0 <= dte <= window_days and (e.get('contracts') or []):
                found.add(ed)
    except Exception:
        pass
    misses = 0
    for i in range(window_days + 1):
        d = today + _dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue      # exchange fact, not provider shape: no weekend expiries
        d_iso = d.isoformat()
        if d_iso in found:
            continue
        try:
            s, ok = await _probe_day(sym, d_iso)
        except Exception:
            misses += 1
            if misses >= 3:
                break     # provider is down — stop burning timeout on doomed probes
            continue
        misses = 0
        spot = spot or s
        if ok:
            found.add(d_iso)
    return spot, sorted(found)

async def _chunk(sym, exp, side, lo, hi, depth=0):
    # one filtered pull per expiry x side. The server clips every response WITHOUT
    # saying so. Clip detection is BEHAVIORAL, not a magic row count (2026-07-13
    # pressure test: a server that quietly lowered its clip to 100 shipped a book
    # whose top strike sat BELOW SPOT with coverage 'complete'): a response is
    # treated as amputated when it is suspiciously large (>=250, the clip we have
    # observed) OR when the returned strikes stop short of the asked window by
    # more than 2x the strike gap inferred from the data itself.
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiration_date': exp,
        'contract_type': side, 'strike_gte': lo, 'strike_lte': hi, 'limit': 250}))
    rows = []
    total = 0
    for e in (r.get('expirations') or []):
        total += len(e.get('contracts') or [])
        if str(e.get('expiration') or '')[:10] != exp:
            continue                      # wrong-expiry rows = the silent full-chain fallback
        rows.extend(e.get('contracts') or [])
    ks = sorted(set(c.get('strike') for c in rows
                    if isinstance(c.get('strike'), (int, float))))
    gaps = sorted(b - a for a, b in zip(ks, ks[1:]) if b > a)
    g = gaps[len(gaps) // 2] if gaps else None
    # A CLIP cuts MID-GRID: strikes stop short of the asked window while the local
    # gap is still the dense body gap. A natural listing boundary thins FIRST —
    # live-measured 2026-07-13: every real SPXW wing steps 5 → 25-wide before
    # ending at 8100 vs the asked 8154, so span-shortfall alone must not trigger
    # (it would recurse every healthy fetch to the depth cap and blow the timeout).
    short_hi = (g is not None and ks[-1] < hi - 2 * g
                and (ks[-1] - ks[-2]) <= 2 * g)
    short_lo = (g is not None and ks[0] > lo + 2 * g
                and (ks[1] - ks[0]) <= 2 * g)
    clipped = total >= 250 or short_lo or short_hi
    if clipped and depth < 3:
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
out, chunks, seen, sides = [], 0, set(), []
for sym, exps in roots.items():
    for exp in exps:
        dte = (_dt.date.fromisoformat(exp) - today).days
        for side in ('call', 'put'):
            rows = await _chunk(sym, exp, side, lo, hi)
            chunks += 1
            # N7: per-expiry x side row count — the client-side coverage guard's
            # raw material. An empty side here is a DOCUMENTED server behavior,
            # and without this ledger it is indistinguishable from an empty book.
            # Rows are counted SIDE-FILTERED (2026-07-13 pressure test: a server
            # ignoring the contract_type filter delivered 241 calls + 9 puts to
            # the PUT ask and the raw count vouched for the amputated side).
            n_side = sum(1 for c in rows if _side(c.get('type')) == side)
            kk = [c.get('strike') for c in rows if _side(c.get('type')) == side
                  and isinstance(c.get('strike'), (int, float))]
            sides.append({'root': sym, 'exp': exp, 'side': side,
                          'rows': n_side, 'raw_rows': len(rows), 'dte': dte,
                          'k_lo': min(kk) if kk else None,
                          'k_hi': max(kk) if kk else None})
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
return {'spot': spot, 'contracts': out, 'chunks': chunks, 'sides': sides,
        'expiries': sorted(set(e for es in roots.values() for e in es))}
"""

_FLOW_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
# right:'both' — the server (SDK 2f3e07d, deployed 07-20) fans this out into two
# single-right pulls IN PARALLEL and concatenates byte-compatible per-strike rows.
# Do NOT re-split client-side: two sequential calls cost ~2x (per-right cache keys,
# back-to-back materializations) and stitch call/put from two different instants.
r = await call_tool('option_trade_flow', {'symbol': %(root)r, 'expiration': %(exp)r,
    'date': %(exp)r, 'strike': %(strike)r, 'right': 'both', 'bucket_minutes': 390})
v = _v(r)
by = (v.get('by_strike') or []) if isinstance(v, dict) else []
if not by:
    return {'available': False}
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
# right:'both' — server-side parallel fan-out (SDK 2f3e07d); see _FLOW_CODE note.
r = await call_tool('option_trade_flow', {'symbol': %(root)r, 'expiration': %(exp)r,
    'date': %(exp)r, 'strike_from': %(lo)r, 'strike_to': %(hi)r, 'right': 'both',
    'bucket_minutes': 390})
v = _v(r)
by = (v.get('by_strike') or []) if isinstance(v, dict) else []
if not by:
    return {'available': False}
out = []
for s in by:
    out.append({'strike': s.get('strike'), 'right': s.get('right'),
                'buy_dollars': s.get('buy_dollars') or 0,
                'sell_dollars': s.get('sell_dollars') or 0,
                # CONTRACTS (2026-07-13) — the provider already returns these and we
                # were throwing them away. Premium is NOT monotone in gamma: a deep-ITM
                # 0DTE call carries max premium and ~zero gamma while its same-strike put
                # carries ~zero premium and the IDENTICAL gamma (BS Γ is right-independent),
                # and premium decays through the session, drifting any dollar-netted sum
                # positive. Any gamma-weighted read must net CONTRACTS, never dollars.
                'buy_vol': s.get('buy_vol') or 0,
                'sell_vol': s.get('sell_vol') or 0,
                # the untagged remainder: mid-print / complex / COB volume the NBBO quote
                # rule cannot classify. Carried so `coverage` is a published number rather
                # than an assumption — the censoring is NOT random (the complex book skews
                # long-gamma-creating), so a read must be able to say "I am half blind".
                'mid_vol': s.get('mid_vol') or 0,
                'unknown_vol': s.get('unknown_vol') or 0})
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


# --- N7 (2026-07-12): MINIMUM-COVERAGE GUARD --------------------------------------------
# A single expiry×side chunk coming back empty is a DOCUMENTED server behavior — and before
# this guard the book still had a spot, still had thousands of contracts, still passed every
# check and still said "native" while the regime flipped from short- to long-gamma on the
# missing half. The guard has two teeth:
#   * a dead/thin side of TODAY'S (weekly-root) 0DTE book REJECTS the fetch — the tradeable
#     book must never drive the map half-blind; the caller degrades to the labeled proxy
#     path (or its stale cache) and LAST_REJECT says why;
#   * a dead/thin side elsewhere (1-7DTE) DEGRADES: the book ships, but meta.coverage says
#     exactly which sides are missing so no consumer can claim it was whole.
MIN_SIDE_ROWS = 5          # a side below this while its twin is fat is amputated, not thin
THIN_TWIN_RATIO = 5        # "fat twin" = twin has ≥ THIN_TWIN_RATIO × MIN_SIDE_ROWS rows
LAST_REJECT: Optional[dict] = None   # {"ts", "reason", ...} — read by GexBox for the diary


def _coverage(sides: Optional[list], weekly_root: str, today_iso: str) -> dict:
    """Pure coverage verdict over the per-expiry×side row ledger. Backward-compatible:
    a response without `sides` (older server code) reads as complete."""
    per: dict = {}
    for s in sides or []:
        try:
            key = (s.get("root"), s.get("exp"))
            per.setdefault(key, {})[s.get("side")] = int(s.get("rows") or 0)
        except Exception:
            continue
    missing = []
    for (root, exp), d in sorted(per.items(), key=lambda kv: (kv[0][1] or "", kv[0][0] or "")):
        for side in ("call", "put"):
            n = d.get(side, 0)
            twin = d.get("put" if side == "call" else "call", 0)
            dead = (n == 0)
            # RELATIVE thin (2026-07-13 pressure test): a side dwarfed RATIO x by
            # its twin is amputated at ANY scale — the old '< MIN_SIDE_ROWS' cliff
            # let a 9-put vs 241-call book (side filter ignored upstream) read as
            # complete. The MIN_SIDE_ROWS floor keeps tiny-but-balanced books legal.
            thin = (0 < n and twin >= THIN_TWIN_RATIO * n
                    and twin >= THIN_TWIN_RATIO * MIN_SIDE_ROWS)
            if dead or thin:
                missing.append({"root": root, "exp": exp, "side": side,
                                "rows": n, "twin_rows": twin})
    zero_dead = any(m["root"] == weekly_root and m["exp"] == today_iso for m in missing)
    has_zero = any(root == weekly_root and exp == today_iso for (root, exp) in per)
    return {"complete": not missing, "missing": missing or None,
            "zero_dte_dead": zero_dead,
            # discovery (bulk hint UNION a direct per-day probe of today) found no
            # weekly expiry for today — legitimate on holidays/weekends, a paged
            # alarm inside a live session (gex_alerts feed-health siren)
            "no_zero_dte": (not has_zero) if per else None}


def _fill_gamma(contracts: list, spot: float) -> None:
    """Theta returns iv/delta/theta/vega but frequently null gamma; recompute per-strike
    gamma from IV with GexBox's own Black-Scholes so the gamma-weighted walls (slides B/C)
    stay populated and consistent with the flip reprice. Fills only where gamma is missing."""
    try:
        from lefteye_gex_box import _bs_gamma, _tau_for
    except Exception:
        return
    for c in contracts:
        if c.get("gamma"):
            continue
        iv, k, dte = c.get("iv"), c.get("strike"), c.get("dte")
        if iv and iv > 0 and k and dte is not None:
            try:
                tau = _tau_for(int(dte), None)     # sessions/252 — same clock as the engine
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
        # N7: coverage verdict BEFORE anything downstream touches the book. A dead/thin
        # side of today's weekly 0DTE book means the ONE book the regime reads is half
        # missing — reject the fetch outright; the caller's degrade path is labeled.
        global LAST_REJECT
        cov = _coverage(res.get("sides"), root, today.isoformat())
        if cov.get("zero_dte_dead"):
            LAST_REJECT = {"ts": now.isoformat(), "reason": "coverage",
                           "missing": cov.get("missing")}
            return None
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
                            # CONTRACTS (2026-07-13) — the gamma-ownership tape's basis.
                            # Separate fields, not a replacement: |net_flow| ($) stays the
                            # PIN weight (H10 churn floor), contracts feed gamma_tape.
                            c["flow_buy_q"] = rec["buy_vol"]
                            c["flow_sell_q"] = rec["sell_vol"]
                            c["flow_mid_q"] = rec["mid_vol"]      # untagged → coverage
                            c["flow_unk_q"] = rec["unknown_vol"]
        except Exception:
            pass                                   # feed hiccup → contracts simply un-annotated
        try:
            import lefteye_fill_ledger as _fl
            _fl.annotate(f"native_{ticker}", contracts, now)
        except Exception:
            pass                                   # best-effort: pin falls back to volume
        out = {"spot": spot, "contracts": contracts,
               "meta": {"chunks": res.get("chunks"), "expiries": res.get("expiries"),
                        "am_settled_dropped": am_dropped, "coverage": cov, **iv_meta}}
        _CHAIN_CACHE[ticker] = (now, out)
        LAST_REJECT = None       # a served book clears the reject — a LATER proxy fallback
        return out               # (token death, timeout) must not inherit 'coverage' as its why


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


def flow_by_strike(ticker: str, spot: float, day: str | None = None) -> Optional[dict]:
    """FLOW SENSOR, per strike — {(strike, right): {'buy': $, 'sell': $, 'net': buy−sell,
    'buy_vol': n, 'sell_vol': n, 'net_vol': n, 'mid_vol': n, 'unknown_vol': n}} across the
    0DTE window, or None. Same NBBO quote-rule aggressor tags as aggressor_flow.

    DOLLARS drive the pin weight (H10 `signed_weight`); CONTRACTS drive the gamma-ownership
    tape (`lefteye_gex_box.gamma_tape`). They are different questions and must not be merged.

    `day` (ISO) is for BACKFILL only — the live call passes None and hits the identical
    path, so a backfilled row is computed by the same code that computes a live one."""
    if not spot:
        return None
    root = ROOT.get(ticker, ticker)
    lo, hi = round(spot * 0.92), round(spot * 1.08)
    res = _run(_FLOWSTRIKE_CODE % {"root": root, "exp": day or _today_iso(),
                                   "lo": lo, "hi": hi})
    if not res or not res.get("available"):
        return None
    out: dict = {}
    for s in res.get("by_strike") or []:
        k, r = s.get("strike"), s.get("right")
        if k is None or r not in ("call", "put"):
            continue
        b = float(s.get("buy_dollars") or 0)
        sl = float(s.get("sell_dollars") or 0)
        bv = float(s.get("buy_vol") or 0)
        sv = float(s.get("sell_vol") or 0)
        out[(round(float(k), 4), r)] = {
            "buy": b, "sell": sl, "net": b - sl,
            "buy_vol": bv, "sell_vol": sv, "net_vol": bv - sv,
            "mid_vol": float(s.get("mid_vol") or 0),
            "unknown_vol": float(s.get("unknown_vol") or 0)}
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
         flow: Optional[float] = None, tape_prev: Optional[dict] = None) -> Optional[dict]:
    """REAL-DATA READ — one time-boxed trip to ThetaData: real chain + live flow,
    never allowed to stall the scan. Native GEX views for the shadow A/B, computed within a hard wall-clock budget so a
    slow feed can never stall the caller. The fetch runs in a daemon thread joined with a
    deadline (sequential inside — the shared MCP session must not be raced). Returns the
    build_views dict (with an added 'aggressor_flow' key), or None on timeout/failure.
    Pass `flow` to reuse an already-fetched FLOW SENSOR reading (skips the re-fetch —
    the caller shares one flow read between the proxy and native engines).
    `tape_prev` is the previous scan's {"ts", "q"} cumulative contract-flow snapshot — the
    gamma tape's only inter-tick memory, and what makes its MARGINAL (wind-change) reading
    possible without a second API call: the provider's by_strike is a running whole-day
    cumulative, so differencing two snapshots IS a windowed tape, for free."""
    from lefteye_gex_box import build_views, _minutes_to_close
    box: dict = {}

    def work():
        nat = native_chain(ticker)   # fill-ledger annotation happens inside the fetch
        if not nat:
            return
        f = flow if flow is not None else aggressor_flow(ticker, spot)
        v = build_views(nat["contracts"], spot, aggressor_flow=f,
                        minutes_to_close=_minutes_to_close(now),
                        tape_prev=tape_prev, anchor_spot=nat.get("spot"), now=now)
        v["aggressor_flow"] = f
        v["native_meta"] = nat.get("meta")   # chunk count + IV rescue/drop counters
        box["v"] = v

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(budget_s)
    if "v" not in box:
        # OBSERVABLE FAILURE, never a silent None: this exact silent-timeout channel hid
        # the 07-13→07-18 flow-block outage (a 29s build_views vs the 15s budget read as
        # "quiet tape" for a week). Stamp LAST_REJECT so GexBox's diary/feed-health path
        # can see it, and say so on stderr — but never clobber a more specific reject
        # (e.g. "coverage") that work() already recorded before returning empty.
        global LAST_REJECT
        reason = "budget_timeout" if t.is_alive() else "native_read_failed"
        if t.is_alive() or LAST_REJECT is None:
            LAST_REJECT = {"ts": now.isoformat(), "reason": reason,
                           "budget_s": budget_s}
        print(f"[native_gex_feed] read({ticker!r}) returned no views: {reason} "
              f"(budget {budget_s:.0f}s)", file=sys.stderr)
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
