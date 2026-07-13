"""
dated_gex_feed.py — the DATED BOOK SIDECAR: far-dated structural OI walls, nightly.

WHY (2026-07-10 tenor-expansion decision): the live fetch is 0-7 DTE only, so the
multi-month structural walls (7000/8000-class round-number OI on EVERY monthly)
are visible only during OpEx week and vanish from the map the rest of the month —
while persisting in the market. This sidecar pulls exactly two extra bands:

  * the next TWO SPX AM 3rd-Friday monthlies    (band "monthly")
  * the next TWO SPXW quarter-end EOM expiries  (band "quarter_end", collar strata;
    two since 2026-07-12/H8 — one quarterly left the forward collar invisible for
    ~2 months every time a quarter turned)

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
    third_fridays(now, n=2)        -> [date, ...]   next n AM-monthly expiries
    quarter_end_expiry(now)        -> date          front quarter's EOM expiry
    quarter_end_expiries(now, n=2) -> [date, ...]   next n quarter-end expiries
    pull(now=None)                 -> dict          nightly job entry (network)
    diary_summary(now=None)        -> dict | None   scan-side cache-only reader
"""
from __future__ import annotations

import json
import math
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
# Per-BAND staleness floors (H9, 2026-07-12): the book-level as_of is the NEWEST
# pull, so a carried band (quarterly on its weekly cadence, or a band whose job
# failed) could wear the fresh book's sticker while being a week old itself —
# and a stale wall read as fresh is exactly the misread that produces bad trades.
# Each band is gated on ITS OWN age: monthlies re-pull nightly (5d = dead job),
# quarterlies refresh weekly by design (10d = genuinely stale, not cadence).
_BAND_STALE_FLOOR_DAYS = {"monthly": 5, "quarter_end": 10}
_TOP_WALLS = 5            # per-side wall slice persisted to the diary
_NEAR_WALL_BAND = 0.035      # m7: "near" = within ±3.5% of spot — the range a dated wall
                             # can actually bound today's tape from
_NEAR_WALL_MIN_GLOBAL = 0.15 # ...and the local king must still carry ≥15% of the side's
                             # global max OI, so dust can never be crowned support.
                             # (v1 used ≥50%-of-global-max, which DEGENERATED whenever the
                             # crash bet IS the max: on both live monthlies 7000P = 100%
                             # and the real support carried 13-29% — the rule handed the
                             # card's exact harm straight back. Verify round, 2026-07-12.)
_ROUND_STEP = 500.0       # round-number strikes verified present after each pull


# --- calendar (pure date math; holiday set borrowed from market_status) ----------

def _holidays(year: int) -> frozenset:
    """NYSE full-closure holidays via the watch package's computed calendar.
    Fail-open: an import problem returns an empty set — worst case the nightly
    job fires on a holiday; an empty result keeps the previous book untouched."""
    try:
        rt = str(_SKILL_DIR.parent.parent / "runtime")
        if rt not in sys.path:
            sys.path.insert(0, rt)
        from watch.intraday import market_status as _ms
        return _ms._market_holidays(year)
    except Exception:
        return frozenset()


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
    return quarter_end_expiries(now, n=1)[0]


def quarter_end_expiries(now: datetime, n: int = 2) -> list:
    """The next `n` quarter-end EOM expiries (H8, 2026-07-12). One quarterly was
    a hole: the moment a quarter turned, its successor's collar walls — the
    biggest OI strata on the board — vanished from the map for ~2 months until
    they became "current" again. Two quarterlies keep the forward collar visible
    the whole way in."""
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    out = []
    y, qm = today.year, ((today.month - 1) // 3 + 1) * 3
    while len(out) < n:
        nxt = date(y + 1, 1, 1) if qm == 12 else date(y, qm + 1, 1)
        d = nxt - timedelta(days=1)
        while d.weekday() >= 5 or d in _holidays(d.year):
            d -= timedelta(days=1)
        if d >= today:
            out.append(d)
        qm += 3
        if qm > 12:
            qm, y = 3, y + 1
    return out


# --- store --------------------------------------------------------------------

def near_wall(pairs, spot, side: str):
    """m7 — the LOCAL KING: the biggest-OI strike on the correct side of spot within
    ±_NEAR_WALL_BAND, provided it carries ≥ _NEAR_WALL_MIN_GLOBAL of the side's global
    max (dust is never crowned). This is the wall that bounds TODAY's tape; the global
    max (often a 7000-class crash bet ~8% out) stays recorded as terrain. None = no
    real support/resistance stands within reach — an honest absence, never the far max."""
    try:
        pairs = [(float(k), float(v)) for k, v in (pairs or []) if v and v > 0]
    except (TypeError, ValueError):
        return None
    if not pairs or not spot:
        return None
    mx = max(v for _, v in pairs)
    band = [(k, v) for k, v in pairs
            if abs(k - spot) <= _NEAR_WALL_BAND * spot
            and ((k > spot) if side == "call" else (k < spot))]
    if not band:
        return None
    k_best, v_best = max(band, key=lambda kv: (kv[1], -abs(kv[0] - spot)))
    return k_best if v_best >= _NEAR_WALL_MIN_GLOBAL * mx else None


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
    # Tolerance ±3 days: a holiday/rebook shifts an expiry by 1-2 business days;
    # anything farther is a WRONG expiry (e.g. an SPXW daily surfacing when the
    # probe's 250-row clip amputated the list) — skip the band, never mis-bind.
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
    if bgap > 3:
        best = None
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
            out.append({'right': sd, 'strike': c.get('strike'), 'expiry': actual,
                        'root': job['root'], 'band': job['band'],
                        'iv': c.get('iv'), 'open_interest': oi})
return {'spot': spot, 'contracts': out, 'calls': calls[0], 'expiry_map': expiry_map}
"""


def _run_code(code: str) -> Optional[dict]:
    """One cass_market_run round-trip via native_gex_feed's hardened client.
    Imported lazily so the scan-side reader never loads network machinery."""
    import native_gex_feed as _nf
    return _nf._run(code)


# --- pull (nightly job; the ONLY network path) ----------------------------------

_PROF_SPAN = 0.06          # ±6% of spot — the price range a dated book actually acts over
_PROF_STEPS = 48           # 49 grid points ⇒ ~0.25% of spot per step (~19 SPX pts)
_BETA_SWEEP = (0.0, 0.4, 0.7, 1.0)   # plausible spot-vol beta: dσ per 1% spot move


def _profile(legs: list, spot: float, tau: float) -> Optional[dict]:
    """The HEDGE-RAIL profile: net dealer GEX ($B per 1% move) repriced across a ±6%
    price grid, plus the zero-crossing (the flip) and a sign-robustness verdict.

    This is the ONLY honest place to compute it. The store is magnitude-only, so a
    client would have to invert σ out of the gex — and gamma is non-monotone in σ, so
    that inversion has two roots and no way to choose. Here the true per-strike IV is
    still in hand.

    `contested`: net gamma at spot is a small difference of two large numbers, and the
    vanna channel can out-vote it. Sweep the spot-vol beta over its plausible range and
    ask whether the dealer's +1% hedge changes SIGN. If it does, the tenor has no
    direction we can honestly claim — the rail draws it with no fill and no number.

    Sign convention: dealers long calls / short puts — the SAME assumption the live 0DTE
    map uses. It is an assumption, not a measurement; the rail says so on its face."""
    if not legs or spot <= 0 or tau <= 0:
        return None
    try:
        from lefteye_gex_box import _bs_gamma as _g, _bs_vanna as _v
    except Exception:
        return None

    def _net_gex(S: float) -> float:
        tot = 0.0
        for k, right, oi, iv in legs:
            gx = _g(S, k, iv, tau) * oi * 100.0 * S * S * 0.01
            tot += gx if right == "call" else -gx
        return tot

    grid = [spot * (1.0 + _PROF_SPAN * (2.0 * i / _PROF_STEPS - 1.0))
            for i in range(_PROF_STEPS + 1)]
    gex = [_net_gex(S) for S in grid]

    flip = None                                  # first zero-crossing, linear-interpolated
    for i in range(1, len(grid)):
        if (gex[i - 1] < 0) != (gex[i] < 0) and gex[i] != gex[i - 1]:
            flip = grid[i - 1] + (0.0 - gex[i - 1]) * (grid[i] - grid[i - 1]) / (gex[i] - gex[i - 1])
            break

    # contested: does the +1% hedge flip sign anywhere in the beta sweep?
    g_at = v_at = 0.0
    for k, right, oi, iv in legs:
        sgn = 1.0 if right == "call" else -1.0
        g_at += sgn * oi * 100.0 * _g(spot, k, iv, tau)
        v_at += sgn * oi * 100.0 * _v(spot, k, iv, tau)
    signs, hedges = set(), []
    for beta in _BETA_SWEEP:
        # +1% spot ⇒ dS = 0.01·S and dσ = −beta·0.01 (spot up, vol down).
        # The dealer TRADES the opposite of the delta they acquire: + = must BUY.
        d_delta = g_at * 0.01 * spot + v_at * (-beta * 0.01)
        hedges.append(-d_delta * spot / 1e9)             # $B of delta to re-hedge
        if abs(d_delta) > 1e-6:
            signs.add(d_delta > 0)
    contested = len(signs) > 1

    return {"grid": [round(S, 1) for S in grid],
            "gex": [round(x / 1e9, 4) for x in gex],     # $B per 1% move
            "flip": round(flip, 1) if flip is not None else None,
            "contested": contested,
            # what a +1% move forces dealers to trade, $B (+ = BUY). h1 is the
            # gamma-only answer (beta=0); h1_range is the span once the vanna channel
            # is swept. A CONTESTED lane still knows BOTH bounds — it just can't pick
            # one, and saying "gamma says sell, vanna says buy" beats saying nothing.
            "h1": round(hedges[0], 3),
            "h1_range": [round(min(hedges), 3), round(max(hedges), 3)]}


def _mass(contracts: list, spot: float, expiry: date, today: date) -> tuple:
    """Unsigned gamma/vanna mass magnitudes for one expiry — display context only.
    Fail-open to (None, None) if the BS helpers are unavailable."""
    try:
        from lefteye_gex_box import _bs_gamma, _bs_vanna, _TRADING_DAYS, _sessions_ahead
        # TRADING time, not calendar: sessions/252. A calendar numerator over the 252-day
        # year inflated tau by up to 1.45x (see _sessions_ahead) and under-stated gamma.
        tau = _sessions_ahead((expiry - today).days, today) / _TRADING_DAYS
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
    k = math.ceil(lo / _ROUND_STEP) * _ROUND_STEP   # edge-inclusive: a round strike
    while k <= hi:                                  # exactly ON the window boundary counts
        expected.append(k)
        k += _ROUND_STEP
    missing = [k for k in expected if k not in have]
    return {"checked": len(expected), "missing": missing}


def _parity_iv_rescue(contracts: list) -> int:
    """NO SILENT GEX HOLES — deep-ITM quotes (canonically puts above spot) arrive
    with null/zero IV because the quote is all intrinsic; put-call parity says the
    same-strike same-expiry twin carries the identical implied vol, and BS gamma
    is right-symmetric, so borrowing the twin's IV is EXACT for the gex components.
    Ported from native_gex_feed._parity_iv_fallback (the live chain's rescue) after
    the 07-10 accuracy audit found the July 8000 put wall shipping iv=0.0 on 168k
    OI — a 48% understatement and a wrong net sign at the biggest overhead wall.
    Returns the rescue count (recorded in meta so the drop is never invisible)."""
    twin = {}
    for c in contracts:
        iv = c.get("iv")
        if iv and iv > 0:
            twin[(c.get("expiry"), c.get("strike"), c.get("right"))] = iv
    rescued = 0
    for c in contracts:
        iv = c.get("iv")
        if iv and iv > 0:
            continue
        other = "call" if c.get("right") == "put" else "put"
        b = twin.get((c.get("expiry"), c.get("strike"), other))
        if b:
            c["iv"] = b
            rescued += 1
    return rescued


def _quarterly_due(now: datetime, prev: dict) -> bool:
    """Quarter-end cadence: daily inside the final 2 weeks (of the FRONT quarterly);
    otherwise refresh once ANY expected quarterly band's own pull is ≥7 days old
    (age-based weekly — survives holiday Mondays with no special case), when an
    expected band is missing/expired/unstamped, or when spot sits within 1% of a
    stored quarter-end wall. BOTH quarterlies are judged (H8) — a fresh front
    quarter must not mask a missing/stale forward one. Unknown spot fails TOWARD
    a refresh. All decided at JOB time, never on a scan."""
    today = now.astimezone(_ET).date() if now.tzinfo else now.date()
    expected = quarter_end_expiries(now, n=2)
    if (expected[0] - today).days <= 14:
        return True
    bands = (prev or {}).get("bands") or []
    live_q = [b for b in bands if b.get("band") == "quarter_end"
              and (b.get("expiry") or "") >= today.isoformat()]
    spot = (prev or {}).get("spot")
    for qe in expected:
        # match by NEAREST stored quarterly within the same ±3-day tolerance the
        # fetch probe uses (_near) — the server-validated expiry can shift a day
        # or two off the computed one on exchange rebooks, and an exact-key miss
        # would re-pull a band we already hold every single night
        qb = None
        for b in live_q:
            try:
                if abs((date.fromisoformat(str(b["expiry"])) - qe).days) <= 3:
                    qb = b
                    break
            except (KeyError, ValueError, TypeError):
                continue
        if qb is None:
            return True                          # expected quarterly absent → refresh
        try:
            age = (today - date.fromisoformat(str(qb.get("as_of", ""))[:10])).days
        except Exception:
            return True                          # unstamped band → refresh
        if age >= 7:
            return True
        if not spot:
            return True                          # unknown spot → fail toward refresh
        for k, _oi in (qb.get("top_calls") or []) + (qb.get("top_puts") or []):
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
        w = _BANDS["quarter_end"]
        for qe in quarter_end_expiries(now, n=2):   # front + forward quarter (H8)
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

    # Post-fetch processing + store, inside the same never-raises contract as the
    # fetch: one malformed row must cost that ROW, and any surprise must cost only
    # THIS pull (previous good book untouched, failure logged) — never the runner.
    try:
        spot, contracts = float(res["spot"]), res["contracts"]
        iv_rescued = _parity_iv_rescue(contracts)   # BEFORE the gex components
        by_exp: dict = {}
        for c in contracts:
            if c.get("strike") is None or c.get("right") not in ("call", "put"):
                continue                       # malformed row: skip, never crash
            by_exp.setdefault((c["expiry"], c["band"]), []).append(c)
        bands = []
        # per-strike GEX components need the same BS gamma the band totals use;
        # fail-open to OI-only rows if the engine helpers are unavailable
        try:
            from lefteye_gex_box import (_bs_gamma as _bsg, _TRADING_DAYS as _TD,
                                          _sessions_ahead as _sess)
        except Exception:
            _bsg = None
        for (exp_iso, band), cs in sorted(by_exp.items()):
            exp_d = date.fromisoformat(exp_iso)
            # sessions/252 — the engine's trading-time clock (never calendar/252)
            tau = (_sess((exp_d - today).days, today) / _TD) if _bsg else \
                  (max((exp_d - today).days, 1) * (252.0 / 365.0) / 252.0)
            oi_k: dict = {}
            legs: list = []          # (K, right, oi, iv) — the repricing chain for `profile`
            for c in cs:
                k = float(c["strike"])
                slot = oi_k.setdefault(k, {"call": 0, "put": 0, "cg": 0.0, "pg": 0.0,
                                           "civ": 0.0, "piv": 0.0})
                oi = int(c.get("open_interest") or 0)
                slot[c["right"]] += oi
                # per-strike dated GEX (2026-07-10): UNSIGNED call/put dollar-gamma
                # components — Γ(S,K,iv,τ)·OI·100·S²·1% — the same formula as the
                # band gamma_mass total, split by side and strike. The store stays
                # magnitude-only (two non-negative components, no signed field);
                # a display may derive the conventional net (calls − puts) — the
                # SAME assumed-dealer-sign convention the live 0DTE map uses.
                iv = c.get("iv")
                if _bsg and iv and iv > 0 and oi > 0:
                    gx = abs(_bsg(spot, k, float(iv), tau)) * oi * 100.0 * spot * spot * 0.01
                    slot["cg" if c["right"] == "call" else "pg"] += gx
                    # PERSIST THE IV (2026-07-12). Gamma is NON-MONOTONE in σ — every
                    # gamma value has two roots — so a reader who only gets the gex
                    # magnitude cannot recover σ without guessing a branch. Storing the
                    # one float that produced the number deletes that whole error class.
                    slot["civ" if c["right"] == "call" else "piv"] = round(float(iv), 4)
                    legs.append((k, c["right"], oi, float(iv)))
            strikes = [[k, v["call"], v["put"], round(v["cg"], 2), round(v["pg"], 2),
                        v["civ"] or None, v["piv"] or None]
                       for k, v in sorted(oi_k.items())]
            top_c = sorted(([k, v["call"]] for k, v in oi_k.items() if v["call"] > 0),
                           key=lambda kv: -kv[1])[:_TOP_WALLS]
            top_p = sorted(([k, v["put"]] for k, v in oi_k.items() if v["put"] > 0),
                           key=lambda kv: -kv[1])[:_TOP_WALLS]
            gm, vm = _mass(cs, spot, exp_d, today)
            bands.append({"expiry": exp_iso, "band": band, "root": cs[0]["root"],
                          "as_of": now.isoformat(),           # per-band pull stamp
                          "dte_at_pull": (exp_d - today).days,
                          # [[k, call_oi, put_oi, call_gex, put_gex, call_iv, put_iv]]
                          # (rows 5→7 on 2026-07-12; indices 0-4 unchanged, so every
                          #  existing reader keeps working against the same offsets)
                          "strikes": strikes,
                          "top_calls": top_c, "top_puts": top_p,   # unsigned OI walls
                          "call_wall": top_c[0][0] if top_c else None,
                          "put_wall": top_p[0][0] if top_p else None,
                          # m7: the wall that bounds TODAY's tape — nearest strong strike
                          # each side of spot (the magnitude kings above stay recorded;
                          # they are terrain, not the answer to "where is support?")
                          "near_call_wall": near_wall(
                              [[k, v["call"]] for k, v in oi_k.items()], spot, "call"),
                          "near_put_wall": near_wall(
                              [[k, v["put"]] for k, v in oi_k.items()], spot, "put"),
                          "call_oi_total": sum(v["call"] for v in oi_k.values()),
                          "put_oi_total": sum(v["put"] for v in oi_k.values()),
                          "gamma_mass": gm, "vanna_mass": vm,
                          "profile": _profile(legs, spot, tau)})
        # CARRY FORWARD: a live prev band not re-fetched today (quarterly on its
        # weekly cadence, or a band whose job failed) rides into the new book
        # with its own as_of intact — a not-due day must never DELETE structure.
        fetched = {(b["band"], b["expiry"]) for b in bands}
        for pb in (prev.get("bands") or []):
            try:
                if ((pb.get("band"), pb.get("expiry")) in fetched
                        or str(pb.get("expiry", "")) < today.isoformat()):
                    continue                   # re-fetched, or expired → drop
                bands.append(pb)
            except Exception:
                continue
        bands.sort(key=lambda b: str(b.get("expiry", "")))
        book = {"ok": True, "as_of": now.isoformat(), "oi_date": today.isoformat(),
                "spot": spot, "bands": bands,
                "landed_date": (prev.get("landed_date") or today.isoformat()),
                "meta": {"calls": res.get("calls"),
                         "expiry_map": res.get("expiry_map"),
                         "iv_rescued": iv_rescued,
                         "round_strike_check": _round_strike_check(contracts, spot)}}
        # never regress to an older book — re-read at the last moment so a slow
        # pull that raced a newer one can't clobber it (TOCTOU window → ms)
        prev2 = atomic_io.read_json_or(_book_path(), {})
        if prev2.get("ok") and str(prev2.get("as_of", "")) > book["as_of"]:
            _log({"ts": now.isoformat(), "ok": False, "regress_blocked": True})
            return {"ok": False, "regress_blocked": True}
        atomic_io.write_json_atomic(_book_path(), book)
        _log({"ts": now.isoformat(), "ok": True, "calls": res.get("calls"),
              "bands": [b["band"] + ":" + b["expiry"] for b in bands],
              "missing_rounds": book["meta"]["round_strike_check"]["missing"]})
        return {"ok": True, "bands": len(bands), "calls": res.get("calls")}
    except Exception as e:
        _log({"ts": now.isoformat(), "ok": False, "kept_previous": bool(prev),
              "error": f"{type(e).__name__}: {e}"})
        return {"ok": False, "kept_previous": bool(prev),
                "error": f"{type(e).__name__}: {e}"}


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
        now_et = now.astimezone(_ET) if now.tzinfo else now
        bands = []
        for b in book.get("bands") or []:
            exp_d = date.fromisoformat(b["expiry"])
            if exp_d < today:
                continue                       # expired band: drop, never display
            if exp_d == today:
                # settled-today drop (OpEx Friday until the 14:10 PT roll re-pull
                # lands): the AM monthly is DEAD after the 9:30 settlement print,
                # the PM quarter-end after the 16:00 close — never display a dead
                # band as live structure.
                if b["band"] == "monthly" and now_et.time() >= time(9, 30):
                    continue
                if b["band"] == "quarter_end" and now_et.time() >= time(16, 0):
                    continue
            # PER-BAND AGE GATE (H9): every band is judged on its OWN pull stamp,
            # never the book's — a carried week-old wall must not wear the fresh
            # book's sticker. An UNSTAMPED band is unverifiable and by
            # construction at least as old as any stamped one (every pull stamps
            # what it fetches) — drop it; inheriting the book's age would hand
            # it the NEWEST stamp in the store. Over-floor bands are DROPPED,
            # and age_days rides every surviving band so consumers (watchtower
            # payload, tablet dimming) can show exactly how fresh each wall is.
            try:
                band_age = (today - datetime.fromisoformat(str(b.get("as_of"))).date()).days
            except (TypeError, ValueError):
                continue                         # unstamped → unverifiable → drop
            if band_age > _BAND_STALE_FLOOR_DAYS.get(b["band"], _STALE_FLOOR_DAYS):
                continue
            bands.append({"expiry": b["expiry"], "band": b["band"],
                          "as_of": b.get("as_of"),   # per-band pull stamp (carried
                                                     # bands are older than the book)
                          "age_days": band_age,      # H9: the band's OWN freshness
                          "dte": (exp_d - today).days,
                          "call_wall": b.get("call_wall"), "put_wall": b.get("put_wall"),
                          # m7: nearest strong walls ride the diary beside the max-OI kings.
                          # KEY PRESENCE preserved: an explicit None (book seen, nothing
                          # near) stays distinguishable from a pre-m7 band (no key at all).
                          **({"near_call_wall": b["near_call_wall"]} if "near_call_wall" in b else {}),
                          **({"near_put_wall": b["near_put_wall"]} if "near_put_wall" in b else {}),
                          "top_calls": (b.get("top_calls") or [])[:_TOP_WALLS],
                          "top_puts": (b.get("top_puts") or [])[:_TOP_WALLS],
                          "call_oi_total": b.get("call_oi_total"),
                          "put_oi_total": b.get("put_oi_total"),
                          "gamma_mass": b.get("gamma_mass"),
                          "vanna_mass": b.get("vanna_mass")})
        if not bands:
            return None
        oldest = max(b["age_days"] for b in bands)
        return {"as_of": book["as_of"], "oi_date": book.get("oi_date"),
                "stale_days": stale_days,
                # the label reads the OLDEST surviving band (H9), not the book —
                # the book's as_of is the newest pull and would launder a carried
                # week-old quarterly into "fresh"
                "oldest_band_days": oldest,
                "staleness": ("fresh" if oldest == 0 else
                              "stale_1d" if oldest == 1 else
                              f"stale_{oldest}d"),
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
