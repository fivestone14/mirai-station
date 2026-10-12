"""lefteye_adaptive_em.py — ADAPTIVE (ASYMMETRIC) EXPECTED MOVE (SHADOW, record-only, em_v:1).

The Range Ruler owns expected-move units in this stack, but its EM is a
straddle price — SYMMETRIC by construction (± the same points). The tape's
expected excursion rarely is. This module re-splits the ruler's budget into an
asymmetric down/up pair using ONLY asymmetry the system has already measured —
no invented numbers: when a component is missing, the whole record is absent.

THE FORMULA (em_v:1, documented here because the diary rows outlive the code):

    em          = range_ruler.em_points            (the de-biased straddle EM)
    d           = the DOWN share ∈ (0,1), from the first available source:

      1. "watchtower" — a LIVE tower call (fired this scan, or a prior fired
         call whose horizon_min has not yet expired) whose range_sigma band
         [lo, hi] BRACKETS spot (lo ≤ 0 ≤ hi). The band is already a pair of
         one-sided excursion bounds, so the split is direct:
             d = |lo| / (|lo| + |hi|)
         A non-bracketing band (both bounds one side of spot) is a drift
         forecast, not a range split — it falls through, never clamped.

      2. "speedometer" — today's realized signed-semivariance split
         (semivar_down_share s = down-move share of VARIANCE, quality "ok" or
         "cold" — "thin"/"degraded" tapes prove nothing). Variance → excursion
         via the square root (one-sided range scales with one-sided vol):
             d = √s / (√s + √(1−s))

    em_down_pts = 2·em·d          em_up_pts = 2·em·(1−d)

    The TOTAL width (down + up = 2·em) is exactly the ruler's symmetric band —
    asymmetry REALLOCATES the priced budget, it never adds to it. d = 0.5
    reproduces the symmetric ruler bit-for-bit.

    Terrain shares (gamma_above/below_spot) were considered and REJECTED as
    the fallback: positioning mass is where price gets PULLED, not how far it
    ranges — the 07-17 backtest graded magnet pull a drift artifact (33% hit),
    while the semivariance split is a measurement of today's own tape.

SHADOW: recorded to telemetry as `adaptive_em`, consumed by nothing. Pure,
stdlib-only, no I/O — the caller hands in records already on the row.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

# Era tag: no A/B may ever pool rows across a formula change without checking it.
EM_V = 1


def _num(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _down_share_watchtower(wt: Optional[dict]) -> Optional[float]:
    """The down share from one tower verdict's range_sigma band — None unless
    the call FIRED and the band brackets spot (lo ≤ 0 ≤ hi, nonzero width)."""
    if not isinstance(wt, dict) or wt.get("fired") is not True:
        return None
    rng = wt.get("range_sigma")
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return None
    lo, hi = _num(rng[0]), _num(rng[1])
    if lo is None or hi is None or lo > 0 or hi < 0 or (abs(lo) + abs(hi)) <= 0:
        return None
    return abs(lo) / (abs(lo) + abs(hi))


def _live_call(rows_today, ticker: str, now: datetime) -> Optional[dict]:
    """The most recent PRIOR tower call for `ticker` still inside its own
    horizon_min — newest first; the first fired row wins or the search ends
    (an expired or stood-down newest call means no call is live NOW; reaching
    past it would resurrect a dead thesis). Fail-open: torn rows are skipped."""
    for r in reversed(rows_today or []):
        if not isinstance(r, dict) or r.get("ticker") != ticker:
            continue
        wt = r.get("watchtower")
        if not isinstance(wt, dict) or "fired" not in wt:
            continue                       # unjudged scan — not a verdict either way
        if wt.get("fired") is not True:
            return None                    # a judged stand-down releases the call
        try:
            ts = datetime.fromisoformat(r["ts"])
            horizon = float(wt.get("horizon_min") or 0)
        except (KeyError, TypeError, ValueError):
            return None
        return wt if now <= ts + timedelta(minutes=horizon) else None
    return None


def _down_share_speedometer(speed: Optional[dict]) -> Optional[float]:
    """√-converted semivariance split — None on a missing share or a tape too
    thin/mangled to measure ("thin"/"degraded"; "cold" only lacks the baseline
    table, which the semivariance never touches)."""
    if not isinstance(speed, dict) or speed.get("quality") not in ("ok", "cold"):
        return None
    s = _num(speed.get("semivar_down_share"))
    if s is None or not 0.0 <= s <= 1.0:
        return None
    rd, ru = math.sqrt(s), math.sqrt(1.0 - s)
    return rd / (rd + ru) if (rd + ru) > 0 else None


def read(range_ruler: Optional[dict], spot: Optional[float], *, ticker: str,
         now: datetime, watchtower: Optional[dict] = None,
         rows_today: Optional[list] = None,
         speedometer: Optional[dict] = None) -> Optional[dict]:
    """One adaptive-EM read (formula in the module docstring). Returns the
    record, or None when the EM or every asymmetry source is missing — the
    absent key IS the honest answer, never a symmetric stand-in."""
    em = _num((range_ruler or {}).get("em_points"))
    if em is None or em <= 0 or not spot:
        return None

    d = _down_share_watchtower(watchtower)
    if d is None:
        d = _down_share_watchtower(_live_call(rows_today, ticker, now))
    src = "watchtower" if d is not None else None
    if d is None:
        d = _down_share_speedometer(speedometer)
        src = "speedometer" if d is not None else None
    if d is None:
        return None

    down, up = 2.0 * em * d, 2.0 * em * (1.0 - d)
    return {
        "em_down_pts": round(down, 2), "em_up_pts": round(up, 2),
        # % of spot, range_ruler.em_pct precision — the UI's unit twin
        "em_down_pct": round(down / spot, 5), "em_up_pct": round(up / spot, 5),
        "down_share": round(d, 4),
        "asym_source": src,
        "em_points": em,                              # the symmetric base, for the A/B
        "em_quality": (range_ruler or {}).get("quality"),
        "em_v": EM_V,
    }
