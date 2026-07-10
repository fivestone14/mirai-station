"""
Pure metric math for iv-viability. No I/O.

Every public function returns a Metric namedtuple:
    Metric(value, confidence, reason, bias)

  value:      numeric or None
  confidence: 'high' | 'medium' | 'low' | 'none'
  reason:     short human-readable explanation
  bias:       'BUY' | 'SELL' | 'NEUTRAL' | None   (for synthesis tally)

Only 'high' and 'medium' confidence results are counted by synthesis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

Confidence = str  # 'high' | 'medium' | 'low' | 'none'
Bias = str  # 'BUY' | 'SELL' | 'NEUTRAL'


@dataclass
class Metric:
    value: float | None
    confidence: Confidence
    reason: str
    bias: Bias | None = None

    def counts(self) -> bool:
        return self.confidence in ("high", "medium") and self.bias in ("BUY", "SELL")


def _none(reason: str) -> Metric:
    return Metric(None, "none", reason, None)


def _safe_float(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# ---------------------------------------------------------------------------
# Absolute IV regime
# ---------------------------------------------------------------------------


def iv_rank(current_iv: float | None, iv_history: Sequence[float]) -> Metric:
    """IV Rank (1-yr): where today's ATM IV sits in its 252-day range."""
    cur = _safe_float(current_iv)
    if cur is None:
        return _none("current IV unavailable")
    clean = [v for v in (_safe_float(x) for x in iv_history) if v is not None]
    if len(clean) < 20:
        return Metric(None, "low", f"insufficient history (n={len(clean)})")
    lo, hi = min(clean), max(clean)
    if hi - lo < 1e-6:
        return _none("flat 1yr IV (max==min)")
    rank = (cur - lo) / (hi - lo) * 100.0
    conf: Confidence = "high" if len(clean) >= 200 else "medium"
    if rank >= 70:
        return Metric(rank, conf, f"IV rank {rank:.0f} — rich vs 1y range", "SELL")
    if rank <= 30:
        return Metric(rank, conf, f"IV rank {rank:.0f} — cheap vs 1y range", "BUY")
    return Metric(rank, conf, f"IV rank {rank:.0f} — mid range", "NEUTRAL")


def iv_percentile(current_iv: float | None, iv_history: Sequence[float]) -> Metric:
    """Share of the past year that closed below today's IV."""
    cur = _safe_float(current_iv)
    if cur is None:
        return _none("current IV unavailable")
    clean = [v for v in (_safe_float(x) for x in iv_history) if v is not None]
    if len(clean) < 20:
        return Metric(None, "low", f"insufficient history (n={len(clean)})")
    below = sum(1 for v in clean if v < cur)
    pct = below / len(clean) * 100.0
    conf: Confidence = "high" if len(clean) >= 200 else "medium"
    if pct >= 70:
        return Metric(pct, conf, f"IV percentile {pct:.0f} — richer than most days", "SELL")
    if pct <= 30:
        return Metric(pct, conf, f"IV percentile {pct:.0f} — cheaper than most days", "BUY")
    return Metric(pct, conf, f"IV percentile {pct:.0f} — mid", "NEUTRAL")


def realized_vol(closes: Sequence[float], window: int) -> float | None:
    """Annualized close-to-close realized vol over `window` bars."""
    xs = [v for v in (_safe_float(c) for c in closes) if v is not None and v > 0]
    if len(xs) < window + 1:
        return None
    arr = np.array(xs[-(window + 1):])
    rets = np.diff(np.log(arr))
    # drop split-like outliers (> 30% single-day move)
    rets = rets[np.abs(rets) < 0.30]
    if len(rets) < 2:
        return None
    return float(np.std(rets, ddof=1) * math.sqrt(252))


def vol_risk_premium(current_iv: float | None, closes: Sequence[float], window: int = 30) -> Metric:
    """IV minus realized vol — wide spread = vol overpriced."""
    cur = _safe_float(current_iv)
    if cur is None:
        return _none("current IV unavailable")
    hv = realized_vol(closes, window)
    if hv is None:
        return Metric(None, "low", f"insufficient price history for HV{window}")
    spread = cur - hv
    conf: Confidence = "high" if len(closes) >= 60 else "medium"
    reason = f"IV {cur:.2%} vs HV{window} {hv:.2%} (spread {spread:+.2%})"
    if spread >= 0.05:
        return Metric(spread, conf, reason + " — vol overpriced", "SELL")
    if spread <= -0.02:
        return Metric(spread, conf, reason + " — vol underpriced", "BUY")
    return Metric(spread, conf, reason + " — fair", "NEUTRAL")


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------


def term_structure_slope(atm_iv_by_dte: dict[int, float]) -> Metric:
    """Slope between front-month and ~90-day ATM IV."""
    clean = {d: v for d, v in atm_iv_by_dte.items()
             if _safe_float(v) is not None and d > 0}
    if len(clean) < 2:
        return _none("need >= 2 expiries for term structure")
    front_dte = min(clean.keys())
    back_dte = max(d for d in clean.keys() if d >= 60) if any(d >= 60 for d in clean) else max(clean.keys())
    if front_dte == back_dte:
        return _none("only one expiry bucket")
    front_iv = clean[front_dte]
    back_iv = clean[back_dte]
    slope = front_iv - back_iv
    reason = f"front {front_dte}d {front_iv:.2%} vs back {back_dte}d {back_iv:.2%}"
    if slope > 0.02:
        return Metric(slope, "high", reason + " — backwardation, event/stress priced in", "SELL")
    if slope < -0.01:
        return Metric(slope, "high", reason + " — contango, calm regime", "BUY")
    return Metric(slope, "high", reason + " — flat", "NEUTRAL")


def front_month_premium(atm_iv_by_dte: dict[int, float]) -> Metric:
    """Front-month IV premium over 90-day baseline — event crush risk."""
    clean = {d: v for d, v in atm_iv_by_dte.items()
             if _safe_float(v) is not None and d > 0}
    if len(clean) < 2:
        return _none("need >= 2 expiries")
    front_dte = min(clean.keys())
    far = [d for d in clean.keys() if d >= 60]
    if not far:
        return Metric(None, "low", "no expiries >= 60d for baseline")
    baseline_dte = min(far)
    premium = clean[front_dte] - clean[baseline_dte]
    reason = f"front {front_dte}d premium {premium:+.2%} over {baseline_dte}d baseline"
    if premium >= 0.05:
        return Metric(premium, "high", reason + " — crush risk for longs", "SELL")
    if premium <= 0.0:
        return Metric(premium, "high", reason + " — no event premium", "NEUTRAL")
    return Metric(premium, "medium", reason, "NEUTRAL")


# ---------------------------------------------------------------------------
# Skew
# ---------------------------------------------------------------------------


def risk_reversal_25d(
    put_iv_25d: float | None,
    call_iv_25d: float | None,
    put_oi: int = 0,
    call_oi: int = 0,
) -> Metric:
    """25-delta risk reversal = OTM put IV - OTM call IV."""
    p = _safe_float(put_iv_25d)
    c = _safe_float(call_iv_25d)
    if p is None or c is None:
        return _none("no valid 25d strikes")
    if put_oi < 10 or call_oi < 10:
        return Metric(None, "low", f"25d OI too thin (put={put_oi}, call={call_oi})")
    rr = p - c
    reason = f"25d RR {rr:+.2%} (put {p:.2%} - call {c:.2%})"
    if rr >= 0.04:
        return Metric(rr, "high", reason + " — downside fear priced rich", "BUY")
    if rr <= -0.02:
        return Metric(rr, "high", reason + " — upside IV bid, squeeze setup", "BUY")
    return Metric(rr, "high", reason + " — neutral skew", "NEUTRAL")


def put_skew_steepness(
    strikes: Sequence[float],
    put_ivs: Sequence[float],
    atm_strike: float,
) -> Metric:
    """Slope of put IVs from ATM into OTM wings."""
    pairs = [
        (s, iv) for s, iv in zip(strikes, put_ivs)
        if _safe_float(s) is not None and _safe_float(iv) is not None and s <= atm_strike
    ]
    if len(pairs) < 3:
        return Metric(None, "low", "insufficient put strikes for skew fit")
    xs = np.array([p[0] for p in pairs])
    ys = np.array([p[1] for p in pairs])
    # moneyness x-axis so slope is scale-free
    if atm_strike <= 0:
        return _none("bad ATM strike")
    moneyness = xs / atm_strike - 1.0
    try:
        slope, _intercept = np.polyfit(moneyness, ys, 1)
    except (np.linalg.LinAlgError, ValueError):
        return Metric(None, "low", "polyfit failed")
    if not math.isfinite(slope):
        return Metric(None, "low", "non-finite skew slope")
    reason = f"put skew slope {slope:+.2f}"
    if slope <= -0.8:
        return Metric(slope, "high", reason + " — steep crash hedges bid", "SELL")
    if slope >= -0.3:
        return Metric(slope, "high", reason + " — flat skew, tails cheap", "BUY")
    return Metric(slope, "medium", reason + " — normal", "NEUTRAL")


def call_skew(
    atm_iv: float | None,
    otm_call_iv: float | None,
) -> Metric:
    """Upside IV vs ATM — bid on upside = squeeze signal."""
    a = _safe_float(atm_iv)
    c = _safe_float(otm_call_iv)
    if a is None or c is None:
        return _none("missing ATM or OTM call IV")
    diff = c - a
    reason = f"upside IV {c:.2%} vs ATM {a:.2%} (diff {diff:+.2%})"
    if diff >= 0.02:
        return Metric(diff, "high", reason + " — upside IV bid", "BUY")
    if diff <= -0.01:
        return Metric(diff, "high", reason + " — upside cheap", "NEUTRAL")
    return Metric(diff, "medium", reason, "NEUTRAL")


def wing_richness(
    atm_iv: float | None,
    otm_put_iv: float | None,
    otm_call_iv: float | None,
) -> Metric:
    """Average of OTM wings vs ATM — flat smile = tails cheap."""
    a = _safe_float(atm_iv)
    p = _safe_float(otm_put_iv)
    c = _safe_float(otm_call_iv)
    if None in (a, p, c):
        return _none("missing ATM or wing IV")
    wing_avg = (p + c) / 2
    diff = wing_avg - a
    reason = f"wings {wing_avg:.2%} vs ATM {a:.2%} (diff {diff:+.2%})"
    if diff <= 0.005:
        return Metric(diff, "high", reason + " — flat smile, buy wings", "BUY")
    if diff >= 0.03:
        return Metric(diff, "high", reason + " — rich tails, sell wings", "SELL")
    return Metric(diff, "medium", reason, "NEUTRAL")


# ---------------------------------------------------------------------------
# Contract pricing
# ---------------------------------------------------------------------------


def bid_ask_spread_pct(bid: float | None, ask: float | None) -> Metric:
    b = _safe_float(bid)
    a = _safe_float(ask)
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return _none("invalid bid/ask")
    mid = (b + a) / 2
    pct = (a - b) / mid * 100
    reason = f"spread {pct:.1f}% of mid"
    if pct > 10:
        return Metric(pct, "high", reason + " — wide, execution tax", "NEUTRAL")
    if pct < 3:
        return Metric(pct, "high", reason + " — tight", "NEUTRAL")
    return Metric(pct, "high", reason, "NEUTRAL")


def vega_theta_ratio(vega: float | None, theta: float | None) -> Metric:
    v = _safe_float(vega)
    t = _safe_float(theta)
    if v is None or t is None:
        return _none("missing vega/theta")
    if abs(t) < 1e-4:
        return Metric(None, "low", "negligible theta")
    ratio = v / abs(t)
    reason = f"vega/|theta| {ratio:.2f}"
    if ratio >= 3:
        return Metric(ratio, "high", reason + " — vol play dominant", "NEUTRAL")
    if ratio <= 1:
        return Metric(ratio, "high", reason + " — decay play dominant", "NEUTRAL")
    return Metric(ratio, "medium", reason + " — balanced", "NEUTRAL")


def extrinsic_pct(
    mid_price: float | None,
    spot: float | None,
    strike: float | None,
    right: str,
) -> Metric:
    m = _safe_float(mid_price)
    s = _safe_float(spot)
    k = _safe_float(strike)
    if None in (m, s, k) or m <= 0:
        return _none("missing pricing inputs")
    if right.lower() == "call":
        intrinsic = max(s - k, 0.0)
    else:
        intrinsic = max(k - s, 0.0)
    extrinsic = m - intrinsic
    if extrinsic < 0:
        return Metric(extrinsic, "low", "negative extrinsic (stale quote)")
    pct = extrinsic / m * 100
    reason = f"extrinsic {pct:.0f}% of premium"
    return Metric(pct, "high", reason, "NEUTRAL")


def break_even_vs_expected(
    break_even: float | None,
    spot: float | None,
    atm_iv: float | None,
    dte: int | None,
) -> Metric:
    be = _safe_float(break_even)
    s = _safe_float(spot)
    iv = _safe_float(atm_iv)
    if None in (be, s, iv) or dte is None or dte <= 0 or s <= 0:
        return _none("missing expected-move inputs")
    one_sd = s * iv * math.sqrt(dte / 252.0)
    distance = abs(be - s)
    ratio = distance / one_sd if one_sd > 0 else math.inf
    reason = f"break-even {distance:.2f} vs 1SD {one_sd:.2f} (ratio {ratio:.2f})"
    if ratio > 1.0:
        return Metric(ratio, "high", reason + " — outside 1SD, low probability", "SELL")
    if ratio < 0.5:
        return Metric(ratio, "high", reason + " — inside half-SD", "BUY")
    return Metric(ratio, "high", reason, "NEUTRAL")


# ---------------------------------------------------------------------------
# OI & volume flow
# ---------------------------------------------------------------------------


def put_call_volume_ratio(total_put_vol: int, total_call_vol: int) -> Metric:
    if total_call_vol <= 0:
        return _none("no call volume")
    ratio = total_put_vol / total_call_vol
    reason = f"P/C vol {ratio:.2f}"
    if ratio >= 1.3:
        return Metric(ratio, "high", reason + " — put-heavy flow", "NEUTRAL")
    if ratio <= 0.7:
        return Metric(ratio, "high", reason + " — call-heavy flow", "NEUTRAL")
    return Metric(ratio, "high", reason, "NEUTRAL")


def delta_weighted_volume(
    contracts: Iterable[dict],
) -> Metric:
    """Sum of delta * volume — positive = net long delta flow."""
    total = 0.0
    count = 0
    for c in contracts:
        delta = _safe_float(c.get("delta"))
        vol = _safe_float(c.get("volume"))
        if delta is None or vol is None:
            continue
        total += delta * vol
        count += 1
    if count == 0:
        return _none("no contracts with delta+volume")
    reason = f"net delta-volume {total:+.0f}"
    if total > 0:
        return Metric(total, "high", reason + " — bullish flow", "NEUTRAL")
    if total < 0:
        return Metric(total, "high", reason + " — bearish flow", "NEUTRAL")
    return Metric(total, "high", reason + " — flat", "NEUTRAL")


def max_pain(strikes_oi: dict[float, dict[str, int]], spot: float) -> Metric:
    """OI-weighted strike where total option value at expiry is minimized."""
    if not strikes_oi:
        return _none("no OI data")
    strikes = sorted(strikes_oi.keys())
    totals: dict[float, float] = {}
    for k in strikes:
        loss = 0.0
        for s in strikes:
            call_oi = strikes_oi[s].get("call_oi", 0)
            put_oi = strikes_oi[s].get("put_oi", 0)
            loss += max(k - s, 0) * call_oi + max(s - k, 0) * put_oi
        totals[k] = loss
    min_loss = min(totals.values())
    candidates = [k for k, v in totals.items() if v == min_loss]
    pain = min(candidates)  # tie -> lower strike
    conf: Confidence = "medium" if len(candidates) > 1 else "high"
    reason = f"max pain {pain:.2f} vs spot {spot:.2f}"
    return Metric(pain, conf, reason, "NEUTRAL")


# ---------------------------------------------------------------------------
# Gamma exposure
# ---------------------------------------------------------------------------


def net_dealer_gex(contracts: Iterable[dict], spot: float) -> Metric:
    """Sum of gamma * OI * 100 * spot^2 * sign; calls +, puts -."""
    total = 0.0
    seen = 0
    for c in contracts:
        gamma = _safe_float(c.get("gamma"))
        oi = _safe_float(c.get("open_interest"))
        right = (c.get("right") or "").lower()
        if gamma is None or oi is None or oi <= 0:
            continue
        sign = 1 if right == "call" else -1
        total += sign * gamma * oi * 100 * spot * spot
        seen += 1
    if seen == 0:
        return _none("no contracts with gamma+OI")
    reason = f"net GEX ${total/1e9:+.2f}B"
    if total > 0:
        return Metric(total, "high", reason + " — vol-suppressing regime", "SELL")
    return Metric(total, "high", reason + " — vol-amplifying regime", "BUY")


def gamma_flip_level(gex_by_strike: dict[float, float]) -> Metric:
    """Strike where cumulative GEX crosses zero."""
    if not gex_by_strike:
        return _none("no gamma profile")
    strikes = sorted(gex_by_strike.keys())
    cum = 0.0
    prev_strike = None
    prev_cum = 0.0
    for k in strikes:
        cum += gex_by_strike[k]
        if prev_strike is not None and (prev_cum * cum) < 0:
            # linear interpolate between prev_strike and k
            frac = prev_cum / (prev_cum - cum) if (prev_cum - cum) != 0 else 0.5
            flip = prev_strike + frac * (k - prev_strike)
            return Metric(flip, "high", f"gamma flip at {flip:.2f}", "NEUTRAL")
        prev_strike = k
        prev_cum = cum
    return Metric(None, "low", "no gamma sign change in profile")


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def iv_crush_pnl_estimate(
    front_iv: float | None,
    back_iv: float | None,
    vega: float | None,
    contracts_count: int = 1,
) -> Metric:
    """Expected $ P/L impact of IV collapse from front to back month level."""
    f = _safe_float(front_iv)
    b = _safe_float(back_iv)
    v = _safe_float(vega)
    if None in (f, b, v):
        return _none("missing IV or vega")
    if f <= b:
        return Metric(0.0, "high", "no crush expected", "NEUTRAL")
    drop_pts = (f - b) * 100  # IV in decimal -> vol points
    pnl = -drop_pts * v * 100 * contracts_count  # long vega loses on crush
    reason = f"est crush P/L ${pnl:+.0f} on {drop_pts:.1f} vol-pt drop"
    return Metric(pnl, "medium", reason, "SELL" if pnl < -50 else "NEUTRAL")
