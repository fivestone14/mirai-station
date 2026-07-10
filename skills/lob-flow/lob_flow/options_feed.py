"""
options_feed.py — the CHAIN TAP: both options feeds over the hosted MCP
(ThetaData behind cass_market_run), plus the JSON-RPC client itself.

  * McpClient          — minimal MCP JSON-RPC client (own session, single-
                         flight lock, https-only, token never logged). A
                         deliberate standalone port of the host's client so
                         this box imports nothing from the host.
  * OptionsQuoteFeedMcp.sweep()  — one server-side pass over the 0DTE chain,
                         both sides, WITH bid/ask sizes and fresh deltas.
  * OptionsTapeFeedMcp.trades_since() — batched incremental tape pull: every
                         trade arrives with the quote that prevailed when it
                         printed (event-time truth; poll delay only affects
                         recency, never resolution).

`python -m lob_flow.options_feed --probe --token-cmd '...'` smoke-tests both
against the live endpoint (network; never used by the test suite).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

import httpx

from .contracts import QuoteRow, TradeEvent

PROTOCOL = "2025-06-18"
TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)
ET = ZoneInfo("America/New_York")   # never inherit the process TZ


class McpClient:
    """JSON-RPC client for one MCP endpoint. token_provider is injected (the
    host decides where secrets live); an optional harden() runs once before
    the first network call (log redaction etc.)."""

    def __init__(self, endpoint: str, token_provider: Callable[[], str],
                 harden: Optional[Callable[[], None]] = None):
        if not endpoint.startswith("https://"):
            raise RuntimeError("lob_flow McpClient: refusing non-https endpoint")
        self.endpoint = endpoint
        self._token = token_provider
        self._harden = harden
        self._hardened = False
        self._client: Optional[httpx.Client] = None
        self._session: Optional[str] = None
        self._lock = threading.Lock()          # single-flight: one call at a time

    def _http(self) -> httpx.Client:
        if not self._hardened and self._harden:
            self._harden()
            self._hardened = True
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        return self._client

    def _headers(self, with_session: bool) -> dict:
        h = {"Authorization": f"Bearer {self._token()}",   # header-only, never logged
             "Accept": "application/json, text/event-stream",
             "Content-Type": "application/json",
             "MCP-Protocol-Version": PROTOCOL}
        if with_session and self._session:
            h["mcp-session-id"] = self._session
        return h

    @staticmethod
    def _result(resp: httpx.Response) -> Optional[dict]:
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

    def _initialize(self) -> None:
        c = self._http()
        r = c.post(self.endpoint, headers=self._headers(False), json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "lob-flow", "version": "1.0"}}})
        if r.status_code in (401, 403):
            raise RuntimeError(f"lob_flow McpClient: auth rejected ({r.status_code})")
        r.raise_for_status()
        self._session = r.headers.get("mcp-session-id")
        c.post(self.endpoint, headers=self._headers(True),
               json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def run(self, code: str) -> Optional[dict]:
        """cass_market_run(code) -> parsed result dict | None. One bounded
        re-init retry on a dropped session; 429 backs off once."""
        with self._lock:
            for attempt in range(2):
                try:
                    if self._session is None:
                        self._initialize()
                    r = self._http().post(
                        self.endpoint, headers=self._headers(True), json={
                            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "cass_market_run",
                                       "arguments": {"code": code}}})
                except httpx.TransportError:
                    self._session = None
                    continue
                if r.status_code in (401, 403):
                    raise RuntimeError(
                        f"lob_flow McpClient: auth rejected ({r.status_code})")
                if r.status_code == 429:
                    import time
                    time.sleep(2.0)
                    continue
                if r.status_code in (400, 404) and attempt == 0:
                    self._session = None
                    continue
                r.raise_for_status()
                payload = self._result(r)
                res = payload.get("result") if isinstance(payload, dict) else None
                if res is None:
                    if attempt == 0:
                        self._session = None
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


# ---------------------------------------------------------------------------
# Server-side code blocks (run inside cass_market_run; keep payloads small)
# ---------------------------------------------------------------------------

_SWEEP_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
def _side(t):
    t = str(t or '').lower()
    return 'call' if t.startswith('c') else 'put' if t.startswith('p') else None

async def _chunk(sym, exp, side, lo, hi, depth=0):
    # the server clips at 250 rows silently — exactly-250 back means split
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiration_date': exp,
        'contract_type': side, 'strike_gte': lo, 'strike_lte': hi, 'limit': 250}))
    rows = []
    for e in (r.get('expirations') or []):
        if (e.get('expiration') or '') != exp:
            continue
        rows.extend(e.get('contracts') or [])
    if len(rows) >= 250 and depth < 3:
        mid = (lo + hi) / 2.0
        return (await _chunk(sym, exp, side, lo, mid, depth + 1) +
                await _chunk(sym, exp, side, mid, hi, depth + 1))
    return rows

r0 = _v(await call_tool('options_chain', {'symbol': %(root)r,
    'expiration_date': %(exp)r, 'limit': 1}))
spot = (r0.get('summary') or {}).get('underlying_price')
if not spot:
    return {'spot': None, 'rows': []}
lo, hi = spot * %(lo_frac)r, spot * %(hi_frac)r
out, seen = [], set()
for side in ('call', 'put'):
    for c in await _chunk(%(root)r, %(exp)r, side, lo, hi):
        s = _side(c.get('type'))
        if s != side:
            continue
        key = (s, c.get('strike'))
        if key in seen:
            continue
        seen.add(key)
        g = c.get('greeks') or {}
        out.append({'right': s, 'strike': c.get('strike'),
                    'bid': c.get('bid'), 'ask': c.get('ask'),
                    'bid_size': c.get('bid_size'), 'ask_size': c.get('ask_size'),
                    'delta': g.get('delta')})
return {'spot': spot, 'rows': out}
"""

_TAPE_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
out = {}
for spec in %(contracts)r:
    strike, right, start = spec['strike'], spec['right'], spec.get('start')
    args = {'symbol': %(root)r, 'expiration': %(exp)r,
            'date_from': %(exp)r, 'date_to': %(exp)r,
            'strike': strike, 'right': right}
    if start:
        args['start_time'] = start
    try:
        r = _v(await call_tool('historical_option_trades', args))
    except Exception:
        continue
    trades = r.get('trades') if isinstance(r, dict) else None
    if trades is None and isinstance(r, dict):
        trades = r.get('data') or r.get('rows') or []
    rows = []
    for t in (trades or []):
        rows.append({'ts': t.get('timestamp'), 'price': t.get('price'),
                     'size': t.get('size'), 'bid': t.get('bid'),
                     'ask': t.get('ask'), 'bid_size': t.get('bid_size'),
                     'ask_size': t.get('ask_size'),
                     'condition': t.get('condition')})
    out[str(strike) + '|' + right] = rows
return {'contracts': out}
"""
# ^ concatenation, not %-formatting, inside the server block: the sandbox
#   rejects str % tuple ("unsupported operand"), and that one line silently
#   emptied every tape batch on first contact (2026-07-06)


class OptionsQuoteFeedMcp:
    """Chain sweeper over the MCP. One server round-trip per sweep (chunked
    inside on the server). Returns (spot, [QuoteRow])."""

    def __init__(self, client: McpClient, lo_frac: float = 0.97,
                 hi_frac: float = 1.03):
        self.client = client
        self.lo_frac, self.hi_frac = lo_frac, hi_frac

    def sweep(self, root: str, expiry: str, now: datetime) -> tuple[Optional[float], list[QuoteRow]]:
        res = self.client.run(_SWEEP_CODE % {"root": root, "exp": expiry,
                                             "lo_frac": self.lo_frac,
                                             "hi_frac": self.hi_frac})
        if not res or not res.get("rows"):
            return (res or {}).get("spot"), []
        ts = now.isoformat()
        rows = [QuoteRow(root=root, expiry=expiry, right=r.get("right"),
                         strike=r.get("strike"), bid=r.get("bid"), ask=r.get("ask"),
                         bid_size=r.get("bid_size"), ask_size=r.get("ask_size"),
                         delta=r.get("delta"), ts=ts)
                for r in res["rows"] if r.get("strike") is not None]
        return res.get("spot"), rows


TAPE_BATCH = 6      # contracts per server call — a 0DTE ATM strike can print
                    # thousands of trades in minutes; one giant all-focus call
                    # blows the read timeout and dies SILENTLY (run() -> None)


class OptionsTapeFeedMcp:
    """Incremental tape puller in small batches; per-contract cursors (last
    seen trade ts-ms + same-ms count) resume without gaps or double counting.
    A failed batch simply contributes nothing this poll — its cursors don't
    advance, so the next poll re-covers it."""

    def __init__(self, client: McpClient):
        self.client = client

    def trades_since(self, root: str, expiry: str,
                     contracts: Sequence[tuple[float, str]],
                     cursor: dict) -> tuple[list[TradeEvent], dict]:
        events: list[TradeEvent] = []
        new_cursor = {"day": expiry,
                      "contracts": dict((cursor.get("contracts") or {}))}
        for i in range(0, len(contracts), TAPE_BATCH):
            batch = contracts[i:i + TAPE_BATCH]
            evs = self._pull_batch(root, expiry, batch, cursor, new_cursor)
            events.extend(evs)
        events.sort(key=lambda e: e.ts_ms)
        return events, new_cursor

    def _pull_batch(self, root: str, expiry: str,
                    contracts: Sequence[tuple[float, str]],
                    cursor: dict, new_cursor: dict) -> list[TradeEvent]:
        specs = []
        for strike, right in contracts:
            cid = f"{strike}|{right}"
            last_ms, _ = _cursor_entry(cursor, cid)
            spec = {"strike": strike, "right": right}
            if last_ms:
                # HH:MM:SS start (ET, explicitly — never the process TZ):
                # re-pull the cursor's second and dedupe below, never a gap
                t = datetime.fromtimestamp(last_ms / 1000.0, tz=ET)
                spec["start"] = t.strftime("%H:%M:%S")
            specs.append(spec)
        res = self.client.run(_TAPE_CODE % {"root": root, "exp": expiry,
                                            "contracts": specs})
        events: list[TradeEvent] = []
        if not res:
            return events
        for cid, rows in (res.get("contracts") or {}).items():
            try:
                strike_s, right = cid.split("|")
                strike = float(strike_s)
            except ValueError:
                continue
            last_ms, last_n = _cursor_entry(cursor, cid)
            seen_at_last = 0          # same-ms prints across a poll boundary are
            accepted = []             # distinguished by COUNT, not just timestamp
            for t in rows or []:
                ms = _to_ms(t.get("ts"))
                if ms is None or ms < last_ms:
                    continue
                # validity BEFORE the same-ms count: both polls must count the
                # same population or the skip lands on the wrong prints
                if not (t.get("price") and t.get("size")):
                    continue
                if ms == last_ms:
                    seen_at_last += 1
                    if seen_at_last <= last_n:
                        continue                      # already had this print
                accepted.append(TradeEvent(
                    ts_ms=ms, strike=strike, right=right,
                    price=float(t["price"]), size=int(t["size"]),
                    bid=t.get("bid"), ask=t.get("ask"),
                    bid_size=t.get("bid_size"), ask_size=t.get("ask_size"),
                    condition=t.get("condition")))
            if accepted:
                events.extend(accepted)
                max_ms = max(e.ts_ms for e in accepted)
                n_at_max = sum(1 for e in accepted if e.ts_ms == max_ms)
                if max_ms == last_ms:
                    n_at_max += last_n
                new_cursor["contracts"][cid] = {"ms": max_ms, "n": n_at_max}
        return events


def _cursor_entry(cursor: dict, cid: str) -> tuple[int, int]:
    """(last_ms, count_seen_at_that_ms). Accepts the legacy plain-int form
    (treated as 'everything at that ms already seen')."""
    prev = (cursor.get("contracts") or {}).get(cid)
    if isinstance(prev, dict):
        return int(prev.get("ms") or 0), int(prev.get("n") or 0)
    if prev:
        return int(prev), 1 << 30
    return 0, 0


def _to_ms(ts) -> Optional[int]:
    """Trade timestamps arrive as epoch-ms ints or ISO strings — accept both.
    Naive ISO strings are ThetaData's ET, made explicit here."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts if ts > 1e12 else ts * 1000)
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# --probe CLI (live smoke test; NOT used by the test suite)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import subprocess
    import sys
    from datetime import date

    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--endpoint", default="https://market-research.cassandrasedge.com/mcp")
    ap.add_argument("--token-cmd", required=True,
                    help="shell command that prints the bearer token")
    ap.add_argument("--root", default="SPXW")
    a = ap.parse_args()
    if not a.probe:
        ap.print_help()
        sys.exit(2)
    tok = subprocess.check_output(a.token_cmd, shell=True, text=True).strip()
    client = McpClient(a.endpoint, lambda: tok)
    exp = date.today().isoformat()
    spot, rows = OptionsQuoteFeedMcp(client).sweep(a.root, exp, datetime.now())
    with_sizes = sum(1 for r in rows if r.bid_size is not None)
    print(f"sweep: spot={spot} rows={len(rows)} with_sizes={with_sizes}")
    if spot and rows:
        atm = min(rows, key=lambda r: abs(r.strike - spot))
        evs, cur = OptionsTapeFeedMcp(client).trades_since(
            a.root, exp, [(atm.strike, "call")], {"day": exp, "contracts": {}})
        print(f"tape: {len(evs)} trades at {atm.strike}C; cursor={cur['contracts']}")
