"""
sndk_feed.py — SNDK-PRO chain client: the native SNDK equity option book.

Fully isolated sibling of mirai-left-eye's native_gex_feed — it REUSES that
module's hardened MCP transport (`native_gex_feed._run`, the dated_gex_feed
precedent) but owns every SNDK-specific rule, so nothing here can change an
SPX read:

  * WEEKLY EXPIRIES, NOT DAILIES — SNDK lists weekly Fridays only (shifted to
    Thursday when the exchange is shut that Friday). "No expiry today" is the
    NORMAL state 4 of 5 days and must never trip an SPX-style zero_dte_dead
    rejection; the coverage teeth apply to the FRONT expiry instead.
  * PER-DAY PROBE DISCOVERY IS MANDATORY — bulk expiry discovery is broken for
    SNDK (returns only the monthly, live-verified 2026-07-27). Every business
    day of the next ~2 weeks is probed individually (the 2026-07-13 doctrine:
    discovery never trusts one response shape), and the day's verdict is CACHED
    so the probe cost is paid once per day, not per pull.
  * LIVE-QUOTE ANCHOR, FAIL-CLOSED — the chain's summary.underlying_price runs
    ~15% STALE for SNDK (1088.5 vs a real ~1246, live-verified). The strike
    window and all spot-relative math anchor on the Schwab live quote the
    caller passes in; with NO live quote there is no honest anchor, so the
    tick is skipped (no book, no row) rather than approximated off the stale
    spot. The stale chain spot rides only as a for-the-record field.
  * QUOTE-DERIVED IV — the provider computes SNDK IVs off its own stale spot,
    so near the REAL money the call side reads ~3.5 (350%!) and the put side
    0.0 (unsolvable). Neither side of a strike can rescue the other by parity
    alone; IV is REBUILT per strike from the live bid/ask mid of the OTM right
    (Black-Scholes bisection vs the live anchor spot) and shared with the twin
    (parity: same strike, same IV; BS gamma is right-independent). Provider IV
    survives only as a plausibility-banded, parity-clamped fallback.

Store: state/sndk_gex/ (discovery cache + raw-book cache + vol hint + fetch
log) — nothing here touches state/dated_gex, the native chain cache, or any
SPX store.

Kill switch: SNDK_PRO_DISABLE=1 → sndk_chain() returns None (checked in the
run wrapper too).

Public surface:
    weekly_expiries(now, n)      -> [date, ...]   calendar-computed weeklies
    discover_expiries(now)       -> [iso, ...]    probed + day-cached truth
    sndk_chain(now, live_spot)   -> {"spot", "anchor_spot", "contracts", "meta"} | None
    reset_chain_cache()
"""
from __future__ import annotations

import math
import os
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
# transport + engine helpers live in the sibling left-eye skill (same pattern
# native_gex_feed uses to borrow iv-viability's vault)
_LEFT_EYE = str(_SKILL_DIR.parent / "mirai-left-eye")
if _LEFT_EYE not in sys.path:
    sys.path.insert(0, _LEFT_EYE)

import atomic_io                                   # noqa: E402
import native_gex_feed as _nf                      # noqa: E402 — transport reuse ONLY
from lefteye_gex_box import (_atm_iv, _bs_gamma, _minutes_to_close,  # noqa: E402
                             _sessions_ahead, _tau_for,              # the engine's own
                             _RTH_MINUTES, _TRADING_DAYS)            # BS/clock/calendar

_ET = ZoneInfo("America/New_York")

TICKER = "SNDK"
_DISCOVERY_WINDOW_DAYS = 14   # probe ~2 weeks of business days so the nearest
                              # 1-2 weekly expiries are always captured
_WINDOW_LIVE = 0.08           # strike-window FLOOR ±8% of the LIVE anchor spot
_WINDOW_SIGMA_K = 2.0         # vol-adaptive width: K·σ_daily/spot — K ≥ the magnet's
                              # ±1.5σ reach window (MAG_WINDOW_SIGMA) + headroom, so a
                              # ~105%-IV SNDK day never truncates its own magnet field
_WINDOW_MAX = 0.25            # sanity clamp on the adaptive half-width
_IV_MIN, _IV_MAX = 0.03, 3.0  # plausibility band for any IV that may drive gamma
_PARITY_RATIO = 3.0           # a side > this × its parity twin is garbage → take the twin
_PARITY_SOFT = 1.5            # ITM side vs its OTM twin: the OTM right is extrinsic-only
                              # truth, so the ITM side clamps at this tighter fence (the
                              # live stale-spot pathology sits on the ITM side)
_MIN_SIDE_ROWS = 5            # coverage teeth (same values as native_gex_feed)
_THIN_TWIN_RATIO = 5

_CHAIN_TTL_S = 240.0          # persisted-book freshness (state/sndk_gex/chain_cache.json):
                              # launchd relaunches the process every 120s tick, so the
                              # cache lives on DISK — an in-process TTL could never hit
_CHAIN_LOCK = threading.Lock()

LAST_REJECT: Optional[dict] = None
LAST_DISCOVERY: str = "ok"    # "ok" | "degraded" — degraded = a candidate front-expiry
                              # probe ERRORED, so the front book may exist unseen


# --- calendar (pure) ---------------------------------------------------------

def _holidays(year: int) -> frozenset:
    """NYSE full-closure holidays via the watch package's computed calendar
    (dated_gex_feed._holidays verbatim). Fail-open to an empty set."""
    try:
        rt = str(_SKILL_DIR.parent.parent / "runtime")
        if rt not in sys.path:
            sys.path.insert(0, rt)
        from watch.intraday import market_status as _ms
        return _ms._market_holidays(year)
    except Exception:
        return frozenset()


def _week_expiry(friday: date) -> date:
    """One week's SNDK expiry: the Friday, stepped back to the prior business
    day when the exchange is shut that Friday (holiday Friday → Thursday)."""
    d = friday
    while d.weekday() >= 5 or d in _holidays(d.year):
        d -= timedelta(days=1)
    return d


def weekly_expiries(now: datetime, n: int = 3) -> list:
    """The next `n` calendar-computed weekly expiries with expiry >= today.
    CALENDAR HINT ONLY — discovery still probes the server per day (an exchange
    can skip or rebook a listing); this enumerates which days CAN be expiries."""
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    out = []
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    while len(out) < n:
        d = _week_expiry(friday)
        if d >= today:
            out.append(d)
        friday += timedelta(days=7)
    return out


def probe_days(today: date, window_days: int = _DISCOVERY_WINDOW_DAYS) -> list:
    """Business days in [today, today+window] — the per-day probe schedule.
    Weekends skipped (exchange fact); holidays are STILL probed cheaply via the
    server rather than trusted to the local calendar (a wrong holiday set must
    fail toward a probe, never toward blindness)."""
    return [today + timedelta(days=i) for i in range(window_days + 1)
            if (today + timedelta(days=i)).weekday() < 5]


# --- store -------------------------------------------------------------------

def _state_dir() -> Path:
    env = os.environ.get("MIRAI_STATE_DIR")
    base = Path(env) if env else (_SKILL_DIR.parent.parent / "state")
    return base / "sndk_gex"


def _log(row: dict) -> None:
    try:
        atomic_io.append_jsonl(_state_dir() / "fetch_log.jsonl", row)
    except Exception:
        pass


# --- server-side code (runs inside ONE cass_market_run per trip) -------------

_PROBE_CODE = """
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
spot = None
found = []
errors = []
misses = 0
days = %(days)r
for i, d_iso in enumerate(days):
    # Single-day existence ask (native_gex_feed._probe_day verbatim): the server
    # answers an UNMATCHED filter with the ENTIRE chain (no error), so rows only
    # count when their block really is the asked-for expiry. A probe ERROR is an
    # UNKNOWN, never an empty — it is reported apart so discovery can refuse to
    # day-cache a verdict that may be missing the front book (M2).
    try:
        r = _v(await call_tool('options_chain', {'symbol': %(sym)r,
            'expiration_date': d_iso, 'limit': 25}))
    except Exception:
        errors.append(d_iso)
        misses += 1
        if misses >= 3:
            errors.extend(days[i + 1:])   # unprobed days are unknowns too
            break     # provider is down — stop burning timeout on doomed probes
        continue
    misses = 0
    spot = spot or (r.get('summary') or {}).get('underlying_price')
    n = 0
    for e in (r.get('expirations') or []):
        if str(e.get('expiration') or '')[:10] == d_iso:
            n += len(e.get('contracts') or [])
    if n > 0:
        found.append(d_iso)
return {'spot': spot, 'found': found, 'errors': errors}
"""

_PULL_CODE = """
import datetime as _dt
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
def _side(t):
    t = str(t or '').lower()
    return 'call' if t.startswith('c') else 'put' if t.startswith('p') else None
today = _dt.date.fromisoformat(%(d0)r)
cell = {'spot': None}     # chain summary spot, captured off the chunk responses

async def _chunk(sym, exp, side, lo, hi, depth=0):
    # native_gex_feed._chunk verbatim: one filtered pull per expiry x side with
    # BEHAVIORAL clip detection (>=250 rows, or strikes stopping short of the
    # asked window mid-grid) and inclusive-half splits deduped at merge.
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiration_date': exp,
        'contract_type': side, 'strike_gte': lo, 'strike_lte': hi, 'limit': 250}))
    cell['spot'] = cell['spot'] or (r.get('summary') or {}).get('underlying_price')
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
    short_hi = (g is not None and ks[-1] < hi - 2 * g
                and (ks[-1] - ks[-2]) <= 2 * g)
    short_lo = (g is not None and ks[0] > lo + 2 * g
                and (ks[1] - ks[0]) <= 2 * g)
    clipped = total >= 250 or short_lo or short_hi
    if clipped and depth < 3:
        mid = (lo + hi) / 2.0
        return (await _chunk(sym, exp, side, lo, mid, depth + 1) +
                await _chunk(sym, exp, side, mid, hi, depth + 1))
    return rows

out, chunks, seen, sides = [], 0, set(), []
for exp in %(exps)r:
    dte = (_dt.date.fromisoformat(exp) - today).days
    for side in ('call', 'put'):
        try:
            rows = await _chunk(%(sym)r, exp, side, %(lo)r, %(hi)r)
        except Exception:
            rows = []
        chunks += 1
        n_side = sum(1 for c in rows if _side(c.get('type')) == side)
        kk = [c.get('strike') for c in rows if _side(c.get('type')) == side
              and isinstance(c.get('strike'), (int, float))]
        sides.append({'exp': exp, 'side': side, 'rows': n_side,
                      'raw_rows': len(rows), 'dte': dte,
                      'k_lo': min(kk) if kk else None,
                      'k_hi': max(kk) if kk else None})
        for c in rows:
            s = _side(c.get('type'))
            if s != side:
                continue
            key = (exp, s, c.get('strike'))
            if key in seen:
                continue          # inclusive split halves → dedup
            seen.add(key)
            g = c.get('greeks') or {}
            out.append({'right': s, 'strike': c.get('strike'), 'dte': dte,
                        'expiry': exp, 'root': %(sym)r, 'iv': c.get('iv'),
                        'open_interest': c.get('open_interest'),
                        'volume': c.get('volume'), 'gamma': g.get('gamma'),
                        'delta': g.get('delta'), 'theta': g.get('theta'),
                        'vega': g.get('vega'), 'bid': c.get('bid'),
                        'ask': c.get('ask'), 'mark': c.get('mark'),
                        'bid_size': c.get('bid_size'), 'ask_size': c.get('ask_size')})
return {'spot': cell['spot'], 'contracts': out, 'chunks': chunks, 'sides': sides}
"""


# --- discovery (probed once per day, cached) ---------------------------------

def discover_expiries(now: Optional[datetime] = None) -> list:
    """Today's probed expiry list (ISO strings, ascending), day-cached in
    state/sndk_gex/discovery.json so the ~10 probe calls are paid once per day.
    Empty list = discovery failed (caller degrades); never raises.

    M2 — a probe ERROR is an unknown, not an empty: if any errored day is a
    candidate expiry day (a Friday or its holiday shift) at-or-before the
    earliest FOUND expiry, the front book may exist unseen. That verdict is
    INCOMPLETE — it serves this tick (marked LAST_DISCOVERY="degraded" →
    meta.discovery on the row) but is never day-cached, so the next tick
    re-probes instead of running all day on next week's book as "front"."""
    global LAST_DISCOVERY
    now = now or datetime.now(_ET)
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    path = _state_dir() / "discovery.json"
    try:
        cached = atomic_io.read_json_or(path, None)
        if (isinstance(cached, dict) and cached.get("date") == today.isoformat()
                and cached.get("expiries")):
            LAST_DISCOVERY = "ok"          # only complete verdicts are cached
            return list(cached["expiries"])
    except Exception:
        pass
    days = [d.isoformat() for d in probe_days(today)]
    try:
        res = _nf._run(_PROBE_CODE % {"sym": TICKER, "days": days})
    except Exception as e:
        LAST_DISCOVERY = "degraded"
        _log({"ts": now.isoformat(), "probe_error": f"{type(e).__name__}: {e}"})
        return []
    found = sorted((res or {}).get("found") or [])
    errors = sorted(set((res or {}).get("errors") or []))
    candidates = {d.isoformat() for d in weekly_expiries(now, n=3)}
    degraded = any(e in candidates and (not found or e <= found[0])
                   for e in errors)
    LAST_DISCOVERY = "degraded" if degraded else "ok"
    if found and not degraded:
        try:
            atomic_io.write_json_atomic(path, {
                "date": today.isoformat(), "expiries": found,
                "spot_hint": (res or {}).get("spot"),
                "probed_days": days, "ts": now.isoformat()})
        except Exception:
            pass
    _log({"ts": now.isoformat(), "probed": len(days), "found": found,
          "errors": errors or None, "discovery": LAST_DISCOVERY})
    return found


# --- IV rebuild (pure) -------------------------------------------------------

def _tau_front(dte: Optional[int], minutes_to_close: Optional[float]) -> Optional[float]:
    """SNDK front-book clock (M1). The engine's `_tau_for` counts sessions in
    (today, expiry] for dte>0 — it EXCLUDES today's remaining session, which is
    exact at each close but prices a Thursday-morning weekly as 1 session when
    ~2 remain (IV/EM inflated ×√2 at that open, ×1.22 Wed, ×1.15 Tue, ×1.12
    Mon). Here dte>0 adds TODAY'S remaining session fraction (live clock) to
    the engine's sessions-ahead count; dte==0 IS the engine's read, verbatim.
    Outside RTH the remainder is 0 — identical to the engine, which is exactly
    right at/after the close. Same trading-time unit throughout: sessions/252."""
    if dte is None:
        return None
    if dte <= 0:
        return _tau_for(dte, minutes_to_close)
    rem = 0.0
    if minutes_to_close is not None:
        rem = min(max(float(minutes_to_close), 0.0), _RTH_MINUTES) / _RTH_MINUTES
    return (rem + _sessions_ahead(int(dte))) / _TRADING_DAYS


def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _mid(c: dict) -> Optional[float]:
    """bid/ask mid (range_ruler's quote rule: bid > 0, ask >= bid), else mark."""
    bid, ask = _num(c.get("bid")), _num(c.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2.0
    mark = _num(c.get("mark"))
    return mark if (mark is not None and mark > 0) else None


def _bs_price(S: float, K: float, sigma: float, tau: float, right: str) -> float:
    """Black-Scholes price, r=0 (same conventions as lefteye_gex_box._bs_gamma)."""
    if S <= 0 or K <= 0 or sigma <= 0 or tau <= 0:
        return max(S - K, 0.0) if right == "call" else max(K - S, 0.0)
    st = sigma * math.sqrt(tau)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * tau) / st
    d2 = d1 - st
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
    call = S * nd1 - K * nd2
    return call if right == "call" else call - S + K       # put via parity, r=0


def _iv_from_price(price: float, S: float, K: float, tau: float, right: str) -> Optional[float]:
    """Bisection IV solve. None when the price sits outside the no-arbitrage
    bracket or the vol lands outside the solve range."""
    if not (price and price > 0 and S > 0 and K > 0 and tau and tau > 0):
        return None
    intrinsic = max(S - K, 0.0) if right == "call" else max(K - S, 0.0)
    upper = S if right == "call" else K
    if not (intrinsic < price < upper):
        return None
    lo, hi = 1e-3, 4.0
    if _bs_price(S, K, hi, tau, right) < price:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _bs_price(S, K, mid, tau, right) < price:
            lo = mid
        else:
            hi = mid
    iv = 0.5 * (lo + hi)
    return iv if _IV_MIN <= iv <= _IV_MAX else None


def rebuild_iv(contracts: list, spot: float,
               minutes_to_close: Optional[float] = None) -> dict:
    """REBUILD every strike's IV from its live quotes vs the LIVE anchor spot.

    The provider's SNDK IVs are computed off its own stale chain spot (live-
    verified 2026-07-27: ATM call 3.5 / ATM put 0.0 vs a sane ~0.5), so unlike
    the SPX book they cannot be trusted on EITHER side. Per (expiry, strike):
    solve the OTM right's mid (clean extrinsic) and share the result with the
    ITM twin (put-call parity: identical IV; BS gamma is right-independent).
    When the OTM quote is unusable, the ITM mid's extrinsic (mid − intrinsic)
    is the OTM twin's synthetic price (parity, r=0). Contracts that still have
    no solve keep the provider IV only inside the plausibility band, with the
    deep-ITM parity clamp (a side > _PARITY_RATIO × its twin takes the twin).
    Mutates in place; returns counters for meta."""
    by_ks: dict[tuple, dict] = {}
    for c in contracts:
        k, r = c.get("strike"), c.get("right")
        if k is None or r not in ("call", "put"):
            continue
        by_ks.setdefault((c.get("expiry"), float(k)), {})[r] = c
    rebuilt = kept = clamped = dropped = 0
    for (_exp, k), pair in by_ks.items():
        legs = list(pair.values())
        dte = legs[0].get("dte")
        tau = _tau_front(dte, minutes_to_close) if dte is not None else None
        otm_right = "call" if k >= spot else "put"
        itm_right = "put" if otm_right == "call" else "call"
        price = _mid(pair[otm_right]) if otm_right in pair else None
        if price is None and itm_right in pair:
            im = _mid(pair[itm_right])
            if im is not None:
                extrinsic = im - abs(spot - k)     # parity, r=0: ITM extrinsic = OTM price
                price = extrinsic if extrinsic > 0 else None
        iv = _iv_from_price(price, spot, k, tau, otm_right) if (price and tau) else None
        if iv is not None:
            for c in legs:
                c["iv"] = round(iv, 4)
                c["iv_src"] = "bs_mid"
                rebuilt += 1
            continue
        # fallback: provider IV, plausibility-banded + parity-clamped. The ITM
        # side clamps at the tighter _PARITY_SOFT fence (m7): its OTM twin is
        # extrinsic-only truth, and the stale-spot pathology lives ITM.
        for c in legs:
            own = _num(c.get("iv"))
            other = "call" if c.get("right") == "put" else "put"
            twin = _num(pair[other].get("iv")) if other in pair else None
            twin_ok = twin is not None and _IV_MIN <= twin <= _IV_MAX
            ratio = _PARITY_RATIO if c.get("right") == otm_right else _PARITY_SOFT
            if own is not None and _IV_MIN <= own <= _IV_MAX:
                if twin_ok and own > ratio * twin:
                    c["iv"] = twin                 # ITM garbage → prefer the twin
                    c["iv_src"] = "parity_clamp"
                    clamped += 1
                else:
                    kept += 1                      # provider IV plausible: keep as-is
            elif twin_ok:
                c["iv"] = twin
                c["iv_src"] = "parity_twin"
                clamped += 1
            else:
                c["iv"] = None                     # no honest IV → out of the reprice
                c["gamma"] = None                  # m6: the provider greeks are the
                c["delta"] = None                  # same stale-spot artifact — never
                dropped += 1                       # let them ride past a nulled IV
    return {"iv_rebuilt": rebuilt, "iv_kept_provider": kept,
            "iv_clamped": clamped, "iv_dropped": dropped}


def _fill_gamma(contracts: list, spot: float,
                minutes_to_close: Optional[float] = None) -> None:
    """Recompute per-strike gamma from the (rebuilt) IV where the provider sent
    null — native_gex_feed._fill_gamma against the LIVE anchor spot, on the SAME
    front-book clock the IV was solved with (M1): BS gamma depends only on σ√τ,
    so a solve/fill τ mismatch would break that invariance."""
    for c in contracts:
        if c.get("gamma"):
            continue
        iv, k, dte = c.get("iv"), c.get("strike"), c.get("dte")
        if iv and iv > 0 and k and dte is not None:
            try:
                tau = _tau_front(int(dte), minutes_to_close)
                c["gamma"] = _bs_gamma(spot, float(k), float(iv), tau)
                c["gamma_src"] = "bs_iv"
            except Exception:
                pass


# --- coverage (SNDK teeth: the FRONT expiry, never "today") ------------------

def _coverage(sides: Optional[list], expiries: list, today_iso: str) -> dict:
    """Coverage verdict over the per-expiry×side ledger. SNDK ADAPTATION of
    native_gex_feed._coverage: there is no daily 0DTE, so `no expiry today` is
    NORMAL (recorded, never alarmed) and the reject-grade teeth bite on the
    FRONT (nearest) expiry — the one tradeable book — instead of today's."""
    per: dict = {}
    for s in sides or []:
        try:
            per.setdefault(s.get("exp"), {})[s.get("side")] = int(s.get("rows") or 0)
        except Exception:
            continue
    missing = []
    for exp, d in sorted(per.items(), key=lambda kv: kv[0] or ""):
        for side in ("call", "put"):
            n = d.get(side, 0)
            twin = d.get("put" if side == "call" else "call", 0)
            dead = (n == 0)
            thin = (0 < n and twin >= _THIN_TWIN_RATIO * n
                    and twin >= _THIN_TWIN_RATIO * _MIN_SIDE_ROWS)
            if dead or thin:
                missing.append({"exp": exp, "side": side,
                                "rows": n, "twin_rows": twin})
    front = min(expiries) if expiries else None
    front_dead = any(m["exp"] == front for m in missing) if front else bool(per)
    return {"complete": not missing, "missing": missing or None,
            "front_expiry": front,
            "front_dead": front_dead,
            "expiry_today": today_iso in (expiries or [])}


# --- chain (disk-cached raw book, single-flight) -----------------------------

def reset_chain_cache() -> None:
    """Drop the persisted raw book so the next call re-pulls (tests / manual)."""
    try:
        (_state_dir() / "chain_cache.json").unlink(missing_ok=True)
    except Exception:
        pass


def _window_frac() -> float:
    """Half-width of the strike fetch window as a fraction of the anchor (M4).
    SNDK runs ~105% IV — a fixed ±8% is ≈±1.1σ there and truncates the magnet's
    own ±1.5σ reach window — so the width adapts to the last known daily vol:
    max(floor, K·σ_daily/spot), clamped sane. The σ hint is what the previous
    tick persisted off its rebuilt front-book ATM IV; the first-ever tick has
    no hint and takes the 8% floor (self-corrects on tick two)."""
    try:
        hint = atomic_io.read_json_or(_state_dir() / "vol_hint.json", None)
        sdf = _num((hint or {}).get("sigma_daily_frac"))
    except Exception:
        sdf = None
    if sdf and sdf > 0:
        return min(max(_WINDOW_LIVE, _WINDOW_SIGMA_K * sdf), _WINDOW_MAX)
    return _WINDOW_LIVE


def _save_vol_hint(contracts: list, anchor: float, now: datetime) -> None:
    """Persist σ_daily/spot from the freshly REBUILT front-book ATM IV — the
    hint the next tick's _window_frac reads. Fail-open."""
    try:
        dtes = sorted({c.get("dte") for c in contracts
                       if isinstance(c.get("dte"), int) and c.get("dte") >= 0})
        front = [c for c in contracts if c.get("dte") == dtes[0]] if dtes else contracts
        iv = _atm_iv(front or contracts, anchor)
        if iv and iv > 0:
            atomic_io.write_json_atomic(_state_dir() / "vol_hint.json", {
                "sigma_daily_frac": round(float(iv) / math.sqrt(_TRADING_DAYS), 5),
                "atm_iv": round(float(iv), 4), "ts": now.isoformat()})
    except Exception:
        pass


def _read_book_cache(now: datetime) -> Optional[dict]:
    """The persisted RAW book when fresh (< _CHAIN_TTL_S), else None. launchd
    relaunches the process every 120s tick, so this disk cache — not an
    in-process TTL — is what halves the server budget (M5): the off tick
    re-prices the cached quotes at the fresh live anchor instead of re-pulling.
    Corrupt or stale → None (caller pulls)."""
    try:
        c = atomic_io.read_json_or(_state_dir() / "chain_cache.json", None)
        ts = datetime.fromisoformat(c["ts"])
        age = (now - ts).total_seconds()
        if 0 <= age < _CHAIN_TTL_S and c.get("contracts") and c.get("expiries"):
            c["age_s"] = age
            return c
    except Exception:
        pass
    return None


def sndk_chain(now: Optional[datetime] = None,
               live_spot: Optional[float] = None) -> Optional[dict]:
    """ONE SNDK book: discovered weeklies, chunk-fetched around the live-quote
    anchor, IV rebuilt from quotes, gamma filled. The raw book persists to
    state/sndk_gex/chain_cache.json for 240s; a fresh cache is RE-PRICED at the
    live quote instead of re-pulled (meta.book_source / cache_age_s say which).
    None on kill switch, missing live quote (M3: the chain's own spot is ~15%
    stale — no quote, no book), discovery failure, or a dead front book
    (LAST_REJECT says why)."""
    global LAST_REJECT
    if os.environ.get("SNDK_PRO_DISABLE") == "1":
        return None
    now = now or datetime.now(_ET)
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    anchor = _num(live_spot)
    if not anchor or anchor <= 0:
        LAST_REJECT = {"ts": now.isoformat(), "reason": "no_live_quote"}
        _log({"ts": now.isoformat(), "skip": "no_live_quote"})
        return None
    with _CHAIN_LOCK:
        cached = _read_book_cache(now)
        if cached:
            contracts = cached["contracts"]
            expiries = list(cached["expiries"])
            sides, chain_spot = cached.get("sides"), cached.get("spot")
            chunks, window = cached.get("chunks"), cached.get("window")
            discovery = cached.get("discovery")
            book_asof, cache_age = cached["ts"], round(cached["age_s"], 1)
            book_source = "disk_cache"
        else:
            expiries = discover_expiries(now)
            if not expiries:
                LAST_REJECT = {"ts": now.isoformat(), "reason": "discovery_empty"}
                return None
            discovery = LAST_DISCOVERY
            window = _window_frac()
            lo, hi = anchor * (1.0 - window), anchor * (1.0 + window)
            try:
                res = _nf._run(_PULL_CODE % {"sym": TICKER, "exps": list(expiries),
                                             "d0": today.isoformat(),
                                             "lo": round(lo, 2), "hi": round(hi, 2)})
            except Exception as e:
                LAST_REJECT = {"ts": now.isoformat(),
                               "reason": f"pull_error:{type(e).__name__}"}
                return None
            if not res or not res.get("contracts"):
                LAST_REJECT = {"ts": now.isoformat(), "reason": "empty_pull"}
                return None
            contracts = res["contracts"]
            sides, chain_spot = res.get("sides"), res.get("spot")
            chunks = res.get("chunks")
            book_asof, cache_age = now.isoformat(), 0.0
            book_source = "pull"
        cov = _coverage(sides, list(expiries), today.isoformat())
        if cov.get("front_dead"):
            LAST_REJECT = {"ts": now.isoformat(), "reason": "coverage",
                           "missing": cov.get("missing")}
            return None
        if book_source == "pull":
            # persist the RAW book (pre-rebuild, post-coverage — a dead book
            # must not be served for the next 240s) for the off-tick reprice
            try:
                atomic_io.write_json_atomic(_state_dir() / "chain_cache.json", {
                    "ts": now.isoformat(), "date": today.isoformat(),
                    "expiries": list(expiries), "spot": chain_spot,
                    "sides": sides, "chunks": chunks, "window": window,
                    "discovery": discovery, "contracts": contracts})
            except Exception:
                pass
        # rebuild + fill on the front-book clock (M1) vs the live anchor —
        # cached books re-solve here too, so spot is as-of NOW either way
        mtc = _minutes_to_close(now)
        iv_meta = rebuild_iv(contracts, anchor, mtc)
        _fill_gamma(contracts, anchor, mtc)
        _save_vol_hint(contracts, anchor, now)
        out = {"spot": chain_spot,                  # chain (stale) spot, for the record
               "anchor_spot": round(float(anchor), 4),
               "contracts": contracts,
               "meta": {"chunks": chunks,
                        "expiries": [{"date": e,
                                      "dte": (date.fromisoformat(e) - today).days}
                                     for e in expiries],
                        "coverage": cov,
                        "window": round(window, 4) if window else window,
                        "discovery": discovery,
                        "book_source": book_source, "book_asof": book_asof,
                        "cache_age_s": cache_age, **iv_meta}}
        LAST_REJECT = None
        return out


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import json
        import lefteye_fetcher
        sp = lefteye_fetcher.live_spot(TICKER)
        ch = sndk_chain(live_spot=sp)
        n = len(ch["contracts"]) if ch else 0
        print("sndk_chain: live_spot", sp, "chain_spot", (ch or {}).get("spot"),
              "contracts", n)
        if ch:
            print(json.dumps(ch["meta"], indent=2))
        else:
            print("reject:", LAST_REJECT)
    else:
        print("usage: sndk_feed.py [--selftest]")
