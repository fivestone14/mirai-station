"""
Synthesis layer: combine metric outputs into a direction tag, a
position-type recommendation, and an aggregate verdict. Applies the
CLAUDE.md Pre-Trade IV Check gate as a BUY-only override.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Sequence

from iv_metrics import Metric


@dataclass
class Verdict:
    headline: str  # BUY VOL | SELL VOL | MIXED | INSUFFICIENT DATA
    direction: str  # bullish | bearish | neutral
    position_type: str
    buy_count: int
    sell_count: int
    counted: int
    gate_fired: list[str] = field(default_factory=list)
    dimensions_disagree: list[str] = field(default_factory=list)


def tally(metrics: Sequence[Metric]) -> tuple[int, int, int]:
    """Return (buy, sell, counted)."""
    buy = sell = counted = 0
    for m in metrics:
        if not m.counts():
            continue
        counted += 1
        if m.bias == "BUY":
            buy += 1
        elif m.bias == "SELL":
            sell += 1
    return buy, sell, counted


def _has_value(m: Metric) -> bool:
    """Directional signals can contribute regardless of BUY/SELL bias tag,
    as long as the metric has a non-None value and at least medium confidence."""
    return m.value is not None and m.confidence in ("high", "medium")


def direction_tag(
    rr: Metric,
    skew_slope: Metric,
    term: Metric,
    flow: Metric,
    pc_vol: Metric | None = None,
) -> str:
    """Combine directional signals into bullish/bearish/neutral."""
    scores = {"bullish": 0, "bearish": 0}

    # Front-expiry signals (7-30 DTE) get 1.5x weight — gamma-driven MM
    # hedging makes these the strongest real-time directional reads.
    # Back-month signals stay at 1.0x — they're structural, not tactical.
    FRONT = 1.5   # front-expiry weight
    FRONT_H = 0.75  # half-weight front signal
    BACK = 1.0    # back-month weight
    BACK_H = 0.5  # half-weight back signal

    # risk reversal (front expiry): positive = downside fear = contrarian bullish
    if _has_value(rr):
        if rr.value >= 0.03:
            scores["bullish"] += FRONT
        elif rr.value >= 0.015:
            scores["bullish"] += FRONT_H
        elif rr.value <= -0.015:
            scores["bullish"] += FRONT  # upside IV bid = squeeze

    # put skew (front expiry): flattening = bullish, steepening = bearish
    if _has_value(skew_slope):
        if skew_slope.value >= -0.3:
            scores["bullish"] += FRONT
        elif skew_slope.value <= -0.8:
            scores["bearish"] += FRONT

    # term structure (cross-expiry — back-month weight)
    if _has_value(term):
        if term.value > 0.02:
            scores["bullish"] += BACK
        elif term.value < -0.01:
            scores["bullish"] += BACK_H  # contango/calm trend

    # delta-weighted volume flow (front-expiry dominated)
    if _has_value(flow):
        if flow.value > 0:
            scores["bullish"] += FRONT
        elif flow.value < 0:
            scores["bearish"] += FRONT

    # put/call volume ratio (front-expiry dominated)
    if pc_vol is not None and _has_value(pc_vol):
        if pc_vol.value <= 0.7:
            scores["bullish"] += FRONT  # call-heavy
        elif pc_vol.value >= 1.3:
            scores["bearish"] += FRONT  # put-heavy

    if scores["bullish"] > scores["bearish"] + 0.5:
        return "bullish"
    if scores["bearish"] > scores["bullish"] + 0.5:
        return "bearish"
    return "neutral"


def position_type(
    iv_regime: str,  # 'cheap' | 'rich' | 'neutral'
    direction: str,
    gex_regime: str,  # 'vol-suppressing' | 'vol-amplifying' | 'unknown'
    dte: int | None,
    event_in_life: bool,
) -> str:
    """Map the (IV × direction × GEX) triple to a recommended structure."""
    if iv_regime == "cheap":
        if direction in ("bullish", "bearish"):
            if gex_regime == "vol-amplifying":
                return f"long {'call' if direction == 'bullish' else 'put'} (cheap vol, amplifying regime)"
            return f"debit vertical or calendar ({direction})"
        return "long straddle or strangle (cheap vol, undirected)"
    if iv_regime == "rich":
        if event_in_life:
            if direction == "neutral":
                return "jade lizard or iron condor (rich vol into event, neutral)"
            return f"credit vertical ({'put' if direction == 'bullish' else 'call'} side) into event"
        if direction in ("bullish", "bearish"):
            return f"credit vertical ({'put' if direction == 'bullish' else 'call'} side)"
        return "iron condor / short strangle (rich vol, range-bound)"
    # neutral IV
    if direction == "bullish":
        return "debit call spread or stock replacement"
    if direction == "bearish":
        return "debit put spread or protective put"
    return "no edge — wait for IV or direction signal"


def classify_iv_regime(iv_rank: Metric, vrp: Metric) -> str:
    """Collapse IV rank + VRP into cheap/rich/neutral."""
    sells = 0
    buys = 0
    for m in (iv_rank, vrp):
        if m.counts():
            if m.bias == "SELL":
                sells += 1
            elif m.bias == "BUY":
                buys += 1
    if sells >= 1 and buys == 0:
        return "rich"
    if buys >= 1 and sells == 0:
        return "cheap"
    return "neutral"


def classify_gex_regime(gex: Metric) -> str:
    if not gex.counts():
        return "unknown"
    if gex.bias == "SELL":
        return "vol-suppressing"
    if gex.bias == "BUY":
        return "vol-amplifying"
    return "unknown"


# ---------------------------------------------------------------------------
# CLAUDE.md Pre-Trade IV Check — BUY-only override
# ---------------------------------------------------------------------------


def pre_trade_iv_check(
    now_et: datetime,
    iv_rank: Metric,
    front_month_premium: Metric,
    event_within_48h: bool,
    increases_vega: bool,
) -> list[str]:
    """
    Return the list of Pre-Trade IV flags that fire for a BUY-vol trade.
    Empty list means green light.
    """
    flags: list[str] = []

    # 1. Time of day — first 30-60 min of session
    if now_et.weekday() < 5:
        open_time = time(9, 30)
        cutoff = time(10, 0)
        nt = now_et.time()
        if open_time <= nt <= cutoff:
            flags.append(
                "Morning IV premium — consider waiting until 10:00 AM ET"
            )

    # 2. IV level elevated
    if iv_rank.counts() and iv_rank.bias == "SELL":
        flags.append(
            f"IV rank elevated ({iv_rank.value:.0f}) — paying a vega tax on this entry"
        )

    # 3. Catalyst proximity
    if event_within_48h:
        flags.append(
            "Event within 48h — pre-catalyst IV bid, expect crush after"
        )

    # 4. Vega direction
    if increases_vega and (
        (front_month_premium.counts() and front_month_premium.bias == "SELL")
        or iv_rank.bias == "SELL"
    ):
        flags.append(
            "Position increases vega into elevated front-month IV"
        )

    return flags


def synthesize(
    metrics: dict[str, Metric],
    now_et: datetime,
    event_within_48h: bool,
    increases_vega: bool,
    dte: int | None = None,
) -> Verdict:
    """Combine everything into a final verdict."""
    values = list(metrics.values())
    buy, sell, counted = tally(values)

    # Direction
    direction = direction_tag(
        metrics.get("risk_reversal", Metric(None, "none", "")),
        metrics.get("put_skew", Metric(None, "none", "")),
        metrics.get("term_structure", Metric(None, "none", "")),
        metrics.get("delta_flow", Metric(None, "none", "")),
        metrics.get("pc_vol", Metric(None, "none", "")),
    )

    # Regimes
    iv_regime = classify_iv_regime(
        metrics.get("iv_rank", Metric(None, "none", "")),
        metrics.get("vrp", Metric(None, "none", "")),
    )
    gex_regime = classify_gex_regime(metrics.get("gex", Metric(None, "none", "")))

    # Position type
    pt = position_type(iv_regime, direction, gex_regime, dte, event_within_48h)

    # Aggregate headline
    if counted == 0:
        headline = "INSUFFICIENT DATA"
    elif buy > sell + 1:
        headline = "BUY VOL"
    elif sell > buy + 1:
        headline = "SELL VOL"
    else:
        headline = "MIXED"

    # Pre-Trade IV gate — BUY-only
    gate_fired = pre_trade_iv_check(
        now_et,
        metrics.get("iv_rank", Metric(None, "none", "")),
        metrics.get("front_month_premium", Metric(None, "none", "")),
        event_within_48h,
        increases_vega,
    )
    if gate_fired and headline == "BUY VOL":
        headline = "BUY VOL (GATED — see flags)"

    # Disagreement surfacing for MIXED
    disagree: list[str] = []
    if headline == "MIXED":
        if iv_regime == "cheap" and gex_regime == "vol-suppressing":
            disagree.append("IV cheap but GEX vol-suppressing")
        if iv_regime == "rich" and gex_regime == "vol-amplifying":
            disagree.append("IV rich but GEX vol-amplifying")
        if direction != "neutral" and buy == sell:
            disagree.append(f"direction={direction} but BUY/SELL tally split")

    return Verdict(
        headline=headline,
        direction=direction,
        position_type=pt,
        buy_count=buy,
        sell_count=sell,
        counted=counted,
        gate_fired=gate_fired,
        dimensions_disagree=disagree,
    )
