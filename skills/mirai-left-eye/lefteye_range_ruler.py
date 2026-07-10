"""lefteye_range_ruler.py — THE RANGE RULER: the day's expected-move yardstick,
read live off the ATM 0DTE straddle, plus a "how much of the ruler is already
spent" gauge.

Plain English: the market prices a straddle at the money every day — that price
IS the crowd's bet on how far the index travels by the close. This module turns
that bet into points (em_points), pins the 9:30 reading as the day's ruler
(em_open), and reports what fraction of that ruler the tape has already used
(em_consumed). A day that has spent 0.3 of its ruler still has room to stretch;
a day past 1.0 is already outside the crowd's priced band — that overshoot is
the signal, so em_consumed is deliberately UNCLAMPED.

Units vocabulary (who owns what):
  * telemetry `sigma`  = GexBox's model-IV full-day 1σ (lefteye_gex_box) — a
    MODEL number, rebuilt from the chain's IV surface.
  * `em_points` (here) = the LIVE de-biased residual straddle — what the market
    is charging RIGHT NOW for the rest of the day.
  THIS module owns expected-move units. It never writes `sigma` or `range_em`;
  consumers that want the model 1σ keep reading GexBox.

The 0DTE book: contracts with dte == 0. The AM-settled monthly (SPX root) that
shares an OpEx date is already dropped UPSTREAM in native_chain (native_gex_feed
strips it before anything downstream sees dte==0), so the dte==0 rows here are
the PM-settled SPXW book — the straddle that actually decays to the 16:00 print.

Per-leg price ladder (best available truth first):
  1. mid = (bid+ask)/2, taken only on a sane quote (bid>0, ask>=bid)  → "straddle_mid"
  2. mark, if positive                                                → "straddle_mark"
     (anchor reports the WORST leg used: one mark leg taints the read)
  3. whole-straddle Brenner–Subrahmanyam fallback from ATM IV:
     EM_raw = BS_FACTOR × spot × iv_atm × √T_yr                       → "iv_sqrt_t"
  4. nothing prices → anchor None, quality "missing".

De-bias: straddles systematically overprice the realized move (variance risk
premium, ~15% in the literature), so EM_raw is scaled by DEBIAS=0.85 before it
becomes em_points. Constant in v1 — calibratable later once the diary carries
enough (em_open vs realized range) pairs to fit it per-regime.

Quality ladder (priority: missing > late_day > reanchored > fallback > ok):
  * "late_day"   — minutes_to_close < LATE_DAY_MIN: the residual straddle is
    mostly noise-priced pin risk, not range information. NOTE: this inherits the
    system-wide hardcoded 16:00 close (no early-close model exists anywhere in
    the repo), so on half days the flag fires ~3 hours late. Documented, not
    fixed here.
  * "reanchored" — em_open had to be set from a scan taken more than
    REANCHOR_GRACE_MIN past the 09:30 open (restart / missed open): the ruler is
    a mid-day residual, so em_consumed against it is suspect.
  * "fallback"   — the anchor degraded to straddle_mark or iv_sqrt_t.

em_consumed is PROBATIONARY: consumers must not gate on it yet — it records to
the diary so its distribution can be studied first.

Event flag and vol carry are pure passthroughs (the calendar and the VIX pulls
are NOT owned here): event_flag picks the most severe of the injected
event_kinds; vol_carry packages vix/vix3m with their ratio.

SHADOW + pure: every input is injected (chain rows, spot, clock, OHLC, vol),
there are no writes, no network, no state stores — read() never raises, it
degrades to quality "missing". Wired into the (shadow) reversion lens by its
caller; em_open_from_rows() rebuilds the day's opening ruler from the diary.
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

DEBIAS = 0.85            # straddle-overpricing de-bias (~15% variance risk premium
                         # in the literature); constant in v1, calibratable later
BS_FACTOR = 0.7979       # Brenner–Subrahmanyam √(2/π): straddle ≈ this × S·σ·√T
LATE_DAY_MIN = 60.0      # minutes-to-close under this → residual read is noise
REANCHOR_GRACE_MIN = 45.0  # em_open set later than 09:30+this → "reanchored"

# Most-severe-first: the flag names the day's dominant scheduled risk.
_EVENT_SEVERITY = ("FOMC", "CPI", "NFP", "VIXEXP", "OPEX", "QTR_ROLL")


def _num(x) -> float | None:
    """A finite float or None — bools and garbage never sneak through."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _leg_price(c: dict) -> tuple[float | None, str | None]:
    """Ladder rungs 1-2 for one leg: (price, "mid"|"mark") or (None, None)."""
    bid, ask = _num(c.get("bid")), _num(c.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2.0, "mid"
    mark = _num(c.get("mark"))
    if mark is not None and mark > 0:
        return mark, "mark"
    return None, None


def _atm_strike(zero: list[dict], spot: float) -> tuple[float | None, dict | None, dict | None]:
    """Strike nearest spot that carries BOTH rights (a straddle needs two legs).
    SPX sits on a 5.0 grid, so an exact-midpoint tie is real — break it to the
    LOWER strike, deterministically."""
    by_strike: dict[float, dict[str, dict]] = {}
    for c in zero:
        k = _num(c.get("strike"))
        r = str(c.get("right") or "").lower()[:1]
        if k is None or r not in ("c", "p"):
            continue
        by_strike.setdefault(k, {}).setdefault(r, c)
    both = [k for k, legs in by_strike.items() if "c" in legs and "p" in legs]
    if not both:
        return None, None, None
    k_atm = min(both, key=lambda k: (abs(k - spot), k))
    return k_atm, by_strike[k_atm]["c"], by_strike[k_atm]["p"]


def read(contracts, spot, now, em_open=None, *, minutes_to_close=None,
         day_open=None, day_high=None, day_low=None, vix=None, vix3m=None,
         event_kinds=None) -> dict:
    """One RANGE RULER read: price the ATM 0DTE straddle, de-bias it into
    em_points, anchor/keep em_open, and gauge em_consumed. Pure; never raises."""
    try:
        was_unanchored = em_open is None

        # --- ATM straddle off the 0DTE (PM-settled SPXW) book -----------------
        zero = [c for c in contracts if isinstance(c, dict) and c.get("dte") == 0]
        k_atm, call, put = _atm_strike(zero, float(spot))

        em_raw, anchor = None, None
        if k_atm is not None:
            pc, tc = _leg_price(call)
            pp, tp = _leg_price(put)
            if pc is not None and pp is not None:
                em_raw = pc + pp
                # anchor = WORST leg used: one mark leg taints the whole read
                anchor = "straddle_mid" if (tc == "mid" and tp == "mid") else "straddle_mark"
            else:
                # rung 3: whole-straddle IV fallback (Brenner–Subrahmanyam)
                ivs = [v for v in (_num(call.get("iv")), _num(put.get("iv")))
                       if v is not None and v > 0]
                if ivs:
                    iv_atm = sum(ivs) / len(ivs)
                    mtc = 390.0 if minutes_to_close is None else max(float(minutes_to_close), 0.0)
                    t_yr = (mtc / 390.0) / 252.0
                    em_raw = BS_FACTOR * float(spot) * iv_atm * math.sqrt(t_yr)
                    anchor = "iv_sqrt_t"

        # DEBIAS applies to ALL anchors uniformly: all three rungs estimate the
        # SAME quantity (the market-priced expected move), and the ~15% straddle
        # overpricing lives in the priced vol level itself, not in which field we
        # read it from — a per-anchor de-bias would make em_points jump whenever
        # the ladder degrades mid-day.
        em_points = round(DEBIAS * em_raw, 2) if em_raw is not None else None
        em_pct = round(em_points / float(spot), 5) if (em_points is not None and spot) else None

        # --- open anchoring ----------------------------------------------------
        if was_unanchored:
            em_open = em_points
        now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        reanchored = (was_unanchored and em_points is not None
                      and (now_et - open_et).total_seconds() / 60.0 > REANCHOR_GRACE_MIN)

        # --- spent / consumed ----------------------------------------------------
        # spent_pts is the max ONE-SIDED excursion from the open: the straddle
        # prices a ± band-RADIUS around the open, so the matching consumption
        # metric is how far the tape got on its FURTHEST side. range/2 would
        # undercount trend days (all range on one side).
        spent_pts = None
        do, dh, dl = _num(day_open), _num(day_high), _num(day_low)
        if do is not None and dh is not None and dl is not None:
            # rounded like em_points (2 dp) — raw float subtraction otherwise
            # writes 55.289999999999964-style noise into the diary
            spent_pts = round(max(dh - do, do - dl), 2)
        em_consumed = None
        eo = _num(em_open)
        if spent_pts is not None and eo is not None and eo > 0:
            # UNCLAMPED (>1 IS the signal) and PROBATIONARY (record, don't gate).
            em_consumed = round(spent_pts / eo, 4)

        # --- quality (priority: missing > late_day > reanchored > fallback > ok)
        if em_points is None:
            quality = "missing"
        elif minutes_to_close is not None and float(minutes_to_close) < LATE_DAY_MIN:
            quality = "late_day"
        elif reanchored:
            quality = "reanchored"
        elif anchor in ("straddle_mark", "iv_sqrt_t"):
            quality = "fallback"
        else:
            quality = "ok"

        # --- passthroughs (calendar / vol NOT owned here) ------------------------
        event_flag = None
        if event_kinds:
            kinds = {str(k).upper() for k in event_kinds}
            event_flag = next((s for s in _EVENT_SEVERITY if s in kinds), None)
        vol_carry = None
        vx, v3 = _num(vix), _num(vix3m)
        if vx is not None and v3 is not None and v3 > 0:
            vol_carry = {"vix": vx, "vix3m": v3, "ratio": round(vx / v3, 3)}

        return {"em_points": em_points, "em_pct": em_pct, "em_open": em_open,
                "em_consumed": em_consumed, "spent_pts": spent_pts,
                "anchor": anchor, "quality": quality, "event_flag": event_flag,
                "vol_carry": vol_carry, "source": "native_chain"}
    except Exception:
        # Fail-open: a torn input must never take the scan down.
        return {"em_points": None, "em_pct": None, "em_open": em_open,
                "em_consumed": None, "spent_pts": None, "anchor": None,
                "quality": "missing", "event_flag": None, "vol_carry": None,
                "source": "native_chain"}


def em_open_from_rows(rows: list[dict]) -> float | None:
    """Rebuild the day's opening ruler from diary rows: chronological scan,
    SPX rows only, first POSITIVE range_ruler.em_open wins. Torn/foreign rows
    are skipped; anything surprising → None (never raises)."""
    try:
        for r in rows or []:
            try:
                if not isinstance(r, dict) or r.get("ticker") != "SPX":
                    continue
                rr = r.get("range_ruler")
                if not isinstance(rr, dict):
                    continue
                v = _num(rr.get("em_open"))
                if v is not None and v > 0:
                    return v
            except Exception:
                continue
        return None
    except Exception:
        return None
