"""
dated_gex_feed.py — the DATED BOOK SIDECAR: far-dated structural OI walls, nightly.

WHY (2026-07-10 tenor-expansion decision): the live fetch is 0-7 DTE only, so the
multi-month structural walls (7000/8000-class round-number OI on EVERY monthly)
are visible only during OpEx week and vanish from the map the rest of the month —
while persisting in the market. This sidecar pulls exactly two extra bands:

  * the next TWO SPX AM 3rd-Friday monthlies  (band "monthly")
  * the current quarter-end SPXW EOM expiry   (band "quarter_end", collar strata)

BLAST-RADIUS CONTRACT (the whole point of the design):
  * separate PROCESS  — runs from its own launchd job (05:17 PT pre-open), never
    inside a scan tick; the scan side is a cache-ONLY file read (`diary_summary`).
  * separate STORE    — state/dated_gex/book.json; nothing here ever touches
    native_gex_feed's chain cache or the contracts list build_views consumes, so
    gamma_flip / regime / shove / magnet keep their exact input distribution.
  * MAGNITUDE ONLY    — unsigned OI / gamma-mass / vanna-mass; the schema carries
    no signed field to misuse (assumed-sign disagrees with flow-inferred ~44%).
  * fail-open         — any error leaves the previous good book untouched
    (uw_periscope semantics: never overwrite good with bad, never regress) and
    the diary key simply absent for the scan.

Kill switch: DATED_BOOK_DISABLE=1 no-ops BOTH sides (runner exits 0, reader
returns None) — one env var silences the sidecar even if launchd keeps firing.

Public surface:
    third_fridays(now, n=2)   -> [date, ...]     next n AM-monthly expiries
    quarter_end_expiry(now)   -> date            current quarter's EOM expiry
    pull(now=None)            -> dict            nightly job entry (network)
    diary_summary(now=None)   -> dict | None     scan-side cache-only reader
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import atomic_io

_ET = ZoneInfo("America/New_York")
_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# Strike windows per band (fractions of spot). Monthlies: ±12% core with the put
# side reaching −20% (6000P-class crash strata sit ~−20% and are top-5 OI on every
# monthly). Quarter-end: collar complex — long-put leg ~−20%, call cap ladder to
# ~+15%; the call side below −5% carries nothing structural.
_BANDS = {
    "monthly":     {"call": (-0.12, 0.12), "put": (-0.20, 0.12)},
    "quarter_end": {"call": (-0.05, 0.15), "put": (-0.20, 0.05)},
}
_ROOT_FOR = {"monthly": "SPX", "quarter_end": "SPXW"}   # AM monthlies / PM EOM

_STALE_FLOOR_DAYS = 5     # beyond this the reader returns None — a week-old wall
                          # map is worse than no map (spec staleness floor)
_TOP_WALLS = 5            # per-side wall slice persisted to the diary
_ROUND_STEP = 500.0       # round-number strikes verified present after each pull


# --- calendar (pure date math; holiday set borrowed from market_status) ----------

def _holidays(year: int) -> frozenset:
    """NYSE full-closure holidays via the watch package's computed calendar.
    Fail-open: an import problem returns an empty set — worst case the nightly
    job fires on a holiday and stores an empty chain no-op."""
    try:
        rt = str(_SKILL_DIR.parent.parent / "runtime")
        if rt not in sys.path:
            sys.path.insert(0, rt)
        from watch.intraday import market_status as _ms
        return _ms._market_holidays(year)
    except Exception:
        return frozenset()


def _prev_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5 or d in _holidays(d.year):
        d -= timedelta(days=1)
    return d


def _third_friday(year: int, month: int) -> date:
    """The month's SPX AM-monthly expiration: 3rd Friday, stepped back to the
    prior business day when the exchange is shut that Friday (NYSE convention)."""
    first = date(year, month, 1)
    d = first + timedelta(days=(4 - first.weekday()) % 7 + 14)   # 3rd Friday
    while d.weekday() >= 5 or d in _holidays(d.year):
        d -= timedelta(days=1)
    return d


def third_fridays(now: datetime, n: int = 2) -> list:
    """The next `n` AM-monthly expiries with expiry >= today. On the OpEx date
    itself the monthly is included only PRE-OPEN (it settles at the 9:30 ET
    print; the Friday-evening roll re-pull must fetch the NEW front monthlies)."""
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    settled_today = (now.tzinfo is not None
                     and now.astimezone(_ET).time() >= time(9, 30))
    out, y, m = [], today.year, today.month
    while len(out) < n:
        d = _third_friday(y, m)
        if d > today or (d == today and not settled_today):
            out.append(d)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def quarter_end_expiry(now: datetime) -> date:
    """The current quarter's EOM (SPXW quarter-end) expiry: last business day of
    the quarter month, rolling to the next quarter once it has passed."""
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    y, qm = today.year, ((today.month - 1) // 3 + 1) * 3
    while True:
        nxt = date(y + 1, 1, 1) if qm == 12 else date(y, qm + 1, 1)
        d = nxt - timedelta(days=1)
        while d.weekday() >= 5 or d in _holidays(d.year):
            d -= timedelta(days=1)
        if d >= today:
            return d
        qm += 3
        if qm > 12:
            qm, y = 3, y + 1


# --- store --------------------------------------------------------------------

def _state_dir() -> Path:
    env = os.environ.get("MIRAI_STATE_DIR")
    base = Path(env) if env else (_SKILL_DIR.parent.parent / "state")
    return base / "dated_gex"


def _book_path() -> Path:
    return _state_dir() / "book.json"


def _log(row: dict) -> None:
    try:
        atomic_io.append_jsonl(_state_dir() / "fetch_log.jsonl", row)
    except Exception:
        pass


# --- server-side fetch (runs inside ONE cass_market_run; native _chunk clone) ---

_DATED_CHAIN_CODE = """
import datetime as _dt
def _v(x): return x.get('value', x) if isinstance(x, dict) else x
def _side(t):
    t = str(t or '').lower()
    return 'call' if t.startswith('c') else 'put' if t.startswith('p') else None
calls = [0]

async def _near(sym, lo_d, hi_d, target):
    # server-validated expiry: the listed expiration nearest the computed date
    # (calendar math can miss an exchange rebook; the server wins, logged).
    calls[0] += 1
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiry_from': lo_d,
        'expiry_to': hi_d, 'limit': 250}))
    spot = (r.get('summary') or {}).get('underlying_price')
    best, bgap = None, 99
    for e in (r.get('expirations') or []):
        ed = e.get('expiration')
        try:
            gap = abs((_dt.date.fromisoformat(ed) - _dt.date.fromisoformat(target)).days)
        except Exception:
            continue
        if gap < bgap:
            best, bgap = ed, gap
    return spot, best

async def _chunk(sym, exp, side, lo, hi, depth=0):
    # one filtered pull per expiry x side; the server clips at 250 rows WITHOUT
    # saying so — exactly-250 back means the window was amputated: split it.
    calls[0] += 1
    r = _v(await call_tool('options_chain', {'symbol': sym, 'expiration_date': exp,
        'contract_type': side, 'strike_gte': lo, 'strike_lte': hi, 'limit': 250}))
    rows = []
    for e in (r.get('expirations') or []):
        if (e.get('expiration') or '') != exp:
            continue
        rows.extend(e.get('contracts') or [])
    if len(rows) >= 250 and depth < 4:
        mid = (lo + hi) / 2.0     # INCLUSIVE halves; overlap deduped at merge
        return (await _chunk(sym, exp, side, lo, mid, depth + 1) +
                await _chunk(sym, exp, side, mid, hi, depth + 1))
    return rows

spot = None
out, seen, expiry_map = [], set(), {}
for job in %(jobs)r:
    s, actual = await _near(job['root'], job['probe_lo'], job['probe_hi'], job['exp'])
    spot = spot or s
    if not actual:
        expiry_map[job['exp']] = None
        continue
    expiry_map[job['exp']] = actual
    if not spot:
        continue
    for side in ('call', 'put'):
        lo = spot * (1.0 + job[side + '_lo'])
        hi = spot * (1.0 + job[side + '_hi'])
        rows = await _chunk(job['root'], actual, side, lo, hi)
        for c in rows:
            sd = _side(c.get('type'))
            if sd != side:
                continue
            oi = c.get('open_interest') or 0
            if oi <= 0:
                continue          # dated book = resting OI structure only
            key = (job['root'], actual, sd, c.get('strike'))
            if key in seen:
                continue
            seen.add(key)
            g = c.get('greeks') or {}
            out.append({'right': sd, 'strike': c.get('strike'), 'expiry': actual,
                        'root': job['root'], 'band': job['band'],
                        'iv': c.get('iv'), 'open_interest': oi,
                        'gamma': g.get('gamma'), 'vega': g.get('vega')})
return {'spot': spot, 'contracts': out, 'calls': calls[0], 'expiry_map': expiry_map}
"""


def _run_code(code: str) -> Optional[dict]:
    """One cass_market_run round-trip via native_gex_feed's hardened client.
    Imported lazily so the scan-side reader never loads network machinery."""
    import native_gex_feed as _nf
    return _nf._run(code)


# --- pull (nightly job; the ONLY network path) ----------------------------------

def _mass(contracts: list, spot: float, expiry: date, today: date) -> tuple:
    """Unsigned gamma/vanna mass magnitudes for one expiry — display context only.
    Fail-open to (None, None) if the BS helpers are unavailable."""
    try:
        from lefteye_gex_box import _bs_gamma, _bs_vanna, _TRADING_DAYS
        tau = max((expiry - today).days, 1) / _TRADING_DAYS
        gm = vm = 0.0
        for c in contracts:
            iv, k, oi = c.get("iv"), c.get("strike"), c.get("open_interest") or 0
            if not (iv and iv > 0 and k and oi):
                continue
            gm += abs(_bs_gamma(spot, float(k), float(iv), tau)) * oi * 100.0 * spot * spot * 0.01
            vm += abs(_bs_vanna(spot, float(k), float(iv), tau)) * oi * 100.0
        return round(gm, 2), round(vm, 2)
    except Exception:
        return None, None


def _round_strike_check(contracts: list, spot: float) -> dict:
    """Verify the big round-number strikes survived the 250-row clip's halving —
    a missing 7000/7500/8000-class strike is exactly the wall we came for. The
    expected set is bounded by the UNION of the fetch windows actually pulled
    (a round strike outside every window can't be 'missing')."""
    have = {float(c["strike"]) for c in contracts if c.get("strike")}
    bands = {c.get("band") for c in contracts if c.get("band") in _BANDS}
    if not bands:
        return {"checked": 0, "missing": []}
    lo_f = min(_BANDS[b]["put"][0] for b in bands)
    hi_f = max(_BANDS[b]["call"][1] for b in bands)
    lo, hi = spot * (1.0 + lo_f), spot * (1.0 + hi_f)
    expected = []
    k = (int(lo // _ROUND_STEP) + 1) * _ROUND_STEP
    while k <= hi:
        expected.append(k)
        k += _ROUND_STEP
    missing = [k for k in expected if k not in have]
    return {"checked": len(expected), "missing": missing}


def _quarterly_due(now: datetime, prev: dict) -> bool:
    """Quarter-end cadence: Mondays, daily inside the final 2 weeks, whenever the
    stored book has no quarterly band yet, or spot sits within 1% of a stored
    quarter-end wall (all decided at JOB time — never on a scan)."""
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    qe = quarter_end_expiry(now)
    if (qe - today).days <= 14 or today.weekday() == 0:
        return True
    bands = (prev or {}).get("bands") or []
    qb = [b for b in bands if b.get("band") == "quarter_end"
          and (b.get("expiry") or "") >= today.isoformat()]
    if not qb:
        return True
    spot = (prev or {}).get("spot")
    if spot:
        for b in qb:
            for k, _oi in (b.get("top_calls") or []) + (b.get("top_puts") or []):
                if abs(k - spot) / spot <= 0.01:
                    return True
    return False


def pull(now: Optional[datetime] = None) -> dict:
    """Nightly job entry: ONE batched cass_market_run for both monthlies (+ the
    quarter-end when due), post-processed into state/dated_gex/book.json with
    uw_periscope store semantics. Returns a status dict; NEVER raises."""
    now = now or datetime.now(_ET)
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    if os.environ.get("DATED_BOOK_DISABLE") == "1":
        return {"ok": False, "skipped": "disabled"}
    if today.weekday() >= 5 or today in _holidays(today.year):
        _log({"ts": now.isoformat(), "skipped": "market_closed"})
        return {"ok": False, "skipped": "market_closed"}

    prev = atomic_io.read_json_or(_book_path(), {})
    jobs = []
    for exp in third_fridays(now, n=2):
        w = _BANDS["monthly"]
        jobs.append({"exp": exp.isoformat(), "root": _ROOT_FOR["monthly"], "band": "monthly",
                     "probe_lo": (exp - timedelta(days=5)).isoformat(),
                     "probe_hi": (exp + timedelta(days=5)).isoformat(),
                     "call_lo": w["call"][0], "call_hi": w["call"][1],
                     "put_lo": w["put"][0], "put_hi": w["put"][1]})
    if _quarterly_due(now, prev):
        qe, w = quarter_end_expiry(now), _BANDS["quarter_end"]
        jobs.append({"exp": qe.isoformat(), "root": _ROOT_FOR["quarter_end"], "band": "quarter_end",
                     "probe_lo": (qe - timedelta(days=5)).isoformat(),
                     "probe_hi": (qe + timedelta(days=5)).isoformat(),
                     "call_lo": w["call"][0], "call_hi": w["call"][1],
                     "put_lo": w["put"][0], "put_hi": w["put"][1]})

    try:
        res = _run_code(_DATED_CHAIN_CODE % {"jobs": jobs})
    except Exception as e:
        res = None
        _log({"ts": now.isoformat(), "ok": False, "error": f"{type(e).__name__}: {e}"})
    if not res or not res.get("spot") or not res.get("contracts"):
        # never overwrite good with bad: previous book stays untouched
        _log({"ts": now.isoformat(), "ok": False, "kept_previous": bool(prev)})
        return {"ok": False, "kept_previous": bool(prev)}

    spot, contracts = float(res["spot"]), res["contracts"]
    by_exp: dict = {}
    for c in contracts:
        by_exp.setdefault((c["expiry"], c["band"]), []).append(c)
    bands = []
    for (exp_iso, band), cs in sorted(by_exp.items()):
        exp_d = date.fromisoformat(exp_iso)
        oi_k: dict = {}
        for c in cs:
            k = float(c["strike"])
            slot = oi_k.setdefault(k, {"call": 0, "put": 0})
            slot[c["right"]] += int(c.get("open_interest") or 0)
        strikes = [[k, v["call"], v["put"]] for k, v in sorted(oi_k.items())]
        top_c = sorted(([k, v["call"]] for k, v in oi_k.items() if v["call"] > 0),
                       key=lambda kv: -kv[1])[:_TOP_WALLS]
        top_p = sorted(([k, v["put"]] for k, v in oi_k.items() if v["put"] > 0),
                       key=lambda kv: -kv[1])[:_TOP_WALLS]
        gm, vm = _mass(cs, spot, exp_d, today)
        bands.append({"expiry": exp_iso, "band": band, "root": cs[0]["root"],
                      "dte_at_pull": (exp_d - today).days,
                      "strikes": strikes,                      # [[k, call_oi, put_oi]]
                      "top_calls": top_c, "top_puts": top_p,   # unsigned OI walls
                      "call_wall": top_c[0][0] if top_c else None,
                      "put_wall": top_p[0][0] if top_p else None,
                      "call_oi_total": sum(v["call"] for v in oi_k.values()),
                      "put_oi_total": sum(v["put"] for v in oi_k.values()),
                      "gamma_mass": gm, "vanna_mass": vm})
    book = {"ok": True, "as_of": now.isoformat(), "oi_date": today.isoformat(),
            "spot": spot, "bands": bands,
            "landed_date": (prev.get("landed_date") or today.isoformat()),
            "meta": {"calls": res.get("calls"),
                     "expiry_map": res.get("expiry_map"),
                     "round_strike_check": _round_strike_check(contracts, spot)}}
    # never regress to an older book (paranoia: clock skew / replayed job)
    if prev.get("ok") and str(prev.get("as_of", "")) > book["as_of"]:
        _log({"ts": now.isoformat(), "ok": False, "regress_blocked": True})
        return {"ok": False, "regress_blocked": True}
    atomic_io.write_json_atomic(_book_path(), book)
    _log({"ts": now.isoformat(), "ok": True, "calls": res.get("calls"),
          "bands": [b["band"] + ":" + b["expiry"] for b in bands],
          "missing_rounds": book["meta"]["round_strike_check"]["missing"]})
    return {"ok": True, "bands": len(bands), "calls": res.get("calls")}


# --- diary_summary (scan side; cache-ONLY, zero network) -------------------------

def diary_summary(now: Optional[datetime] = None) -> Optional[dict]:
    """Compact fail-open read of the stored dated book for the diary/tablet.
    None when disabled, absent, unreadable, or staler than the floor — the diary
    key simply doesn't appear and every consumer degrades to today's behavior."""
    if os.environ.get("DATED_BOOK_DISABLE") == "1":
        return None
    try:
        book = atomic_io.read_json_or(_book_path(), None)
        if not book or not book.get("ok"):
            return None
        now = now or datetime.now(_ET)
        today = now.astimezone(_ET).date() if now.tzinfo else now.date()
        as_of = datetime.fromisoformat(book["as_of"])
        stale_days = (today - as_of.date()).days
        if stale_days > _STALE_FLOOR_DAYS:
            return None
        bands = []
        for b in book.get("bands") or []:
            exp_d = date.fromisoformat(b["expiry"])
            if exp_d < today:
                continue                       # expired band: drop, never display
            bands.append({"expiry": b["expiry"], "band": b["band"],
                          "dte": (exp_d - today).days,
                          "call_wall": b.get("call_wall"), "put_wall": b.get("put_wall"),
                          "top_calls": (b.get("top_calls") or [])[:_TOP_WALLS],
                          "top_puts": (b.get("top_puts") or [])[:_TOP_WALLS],
                          "call_oi_total": b.get("call_oi_total"),
                          "put_oi_total": b.get("put_oi_total"),
                          "gamma_mass": b.get("gamma_mass"),
                          "vanna_mass": b.get("vanna_mass")})
        if not bands:
            return None
        return {"as_of": book["as_of"], "oi_date": book.get("oi_date"),
                "stale_days": stale_days,
                "staleness": ("fresh" if stale_days == 0 else
                              "stale_1d" if stale_days == 1 else
                              f"stale_{stale_days}d"),
                "landed_date": book.get("landed_date"),
                "spot_at_pull": book.get("spot"), "bands": bands}
    except Exception:
        return None


if __name__ == "__main__":
    if "--pull" in sys.argv:
        st = pull()
        print(json.dumps(st))
        sys.exit(0)
    elif "--selftest" in sys.argv:
        st = pull()
        print("pull:", json.dumps(st))
        print("summary:", json.dumps(diary_summary(), indent=2))
        sys.exit(0)
    elif "--summary" in sys.argv:
        print(json.dumps(diary_summary(), indent=2))
        sys.exit(0)
    else:
        print("usage: dated_gex_feed.py [--pull | --selftest | --summary]")
