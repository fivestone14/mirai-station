"""lefteye_dex.py — DEALER DELTA EXPOSURE (DEX): the DIRECTION side of the map (SHADOW).

The gamma engine (lefteye_gex_box) answers a MAGNITUDE question — does dealer
hedging damp or amplify the next move? DEX asks the DIRECTION question the stack
has never recorded: how much net dealer DELTA is standing on the book, i.e. the
mechanical re-hedge pressure on the underlying itself (TanukiTrade doctrine: net
dealer delta still to re-hedge — the strongest DIRECTIONAL read in the stack when
large). Same chain, same clock, same τ — a second surface off the book GexBox
already priced, never a second fetch.

Doctrine (signs, on the record):

  • DEALER SIGN = THE SAME NAIVE ASSUMPTION AS GEX. Dealers are long the calls
    (sold to them) and short the puts (bought from them): call terms +, put
    terms −, applied to each contract's own signed BS delta — mirror-exact with
    `_net_gex_at`'s convention so the two surfaces can never disagree about who
    owns what. Delta is the one greek in this stack that is NOT right-symmetric
    (call N(d1), put N(d1)−1), so the right is PRICED here, not just signed on.

  • THE NAIVE NET IS STRUCTURALLY LONG. A long call carries +Δ and a short put
    carries −Δput > 0, so EVERY repriceable contract contributes positive dealer
    delta and net_dex_total > 0 BY CONSTRUCTION — the delta twin of the N8
    permanent-bull magnet and the N11 locked-negative charm. The information
    lives in the MAGNITUDE, the per-strike distribution and the above/below
    split, never in the naive total's sign; `dex_word` carries that caveat every
    time it speaks (a constant sign must never read as a live signal).

  • UW CAVEAT: the flow-inferred dealer sign (UW periscope) disagrees with this
    assumed sign on ~44% of strikes (~55% on 0DTE) — exactly the slice a naive
    read gets wrong. UW's own per-strike sign measured as a coin flip and was
    REFUSED as a replacement (uw_coherence, 2026-07-12), so the refinement hook
    here is OUR tape: `dex_flow_signed` re-signs today's traded 0DTE contracts
    from the per-strike aggressor annotation (flow_buy_q/sell_q — customer net
    buy ⇒ dealer short) and rides beside the naive read every scan. It sees only
    today's tape, never the standing OI book. RECORD BOTH, PROMOTE NEITHER — the
    A/B decides, not this module.

SHADOW: consumed by nothing. Recorded to telemetry as `dex_views`; spoken to the
Watchtower as ADVISORY context only (no gate reads it). Pure module — the caller
hands in the SAME cached chain GexBox priced (read budget intact, no I/O here).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

import lefteye_gex_box as _gxb  # noqa: E402 — τ/pricing/basis helpers (twin surface)

# Surface era tag (cf. pin_field_v / gamma_tape_v): no A/B may ever pool rows
# across a definition change without checking this.
DEX_V = 1


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_delta(S: float, K: float, sigma: float, tau: float, right: str) -> float:
    """Black-Scholes delta, r=q=0: call N(d1) ∈ (0,1), put N(d1)−1 ∈ (−1,0).
    0.0 on degenerate inputs (mirrors _bs_gamma). Reuses `_bs_d1d2` so the d1
    under this surface is bit-identical to the one under vanna/charm."""
    dd = _gxb._bs_d1d2(S, K, sigma, tau)
    if dd is None:
        return 0.0
    nd1 = _norm_cdf(dd[0])
    return nd1 if right == "call" else nd1 - 1.0


def _dealer_dex(c: dict, S: float, weight_key: str,
                minutes_to_close: Optional[float]) -> Optional[float]:
    """Per-contract DEALER-signed dollar-delta: Δ·w·100·S (underlying-equivalent
    $, the delta analogue of `_net_greek_exposure_at`'s unit·w·100·S), then the
    SAME dealer-position sign the gamma engine applies — calls +, puts −. The
    put leg's −Δput is positive: both legs are LONG dealer delta (the
    structural-long doctrine in the header). None = does not reprice — the
    shared `_reprices` predicate (strike, weight, usable IV, dte), so a broker
    −999 IV sentinel or an empty-OI strike can never fabricate delta mass."""
    if not _gxb._reprices(c, weight_key):
        return None
    tau = _gxb._tau_for(c["dte"], minutes_to_close)   # ONE clock (sessions/252)
    if not tau:
        return None
    d = _bs_delta(S, c["strike"], c["iv"], tau, c.get("right"))
    ex = d * c[weight_key] * 100.0 * S
    return ex if c.get("right") == "call" else -ex


def dex_word(net_total: Optional[float]) -> Optional[str]:
    """The plain hedging-pressure word. Net dealer delta LONG ⇒ the standing
    re-hedge SELLS the underlying (mechanical supply — dealers shed length into
    strength); SHORT ⇒ the mirror, mechanical demand under weakness. The naive
    surface is structurally LONG (header doctrine), so the long branch carries
    its own caveat every time it speaks — N11's lesson, applied at birth instead
    of after 804 constant rows."""
    if net_total is None:
        return None
    if net_total > 0:
        return ("dealers_need_to_SELL_on_rallies — naive net dealer delta is LONG: "
                "the standing hedge sheds underlying into strength (mechanical "
                "supply). CAVEAT: the naive net is long BY CONSTRUCTION — read the "
                "magnitude, the above/below split and the flow-signed twin, never "
                "this sign alone")
    if net_total < 0:
        return ("dealers_need_to_BUY_on_dips — naive net dealer delta is SHORT: the "
                "standing hedge buys underlying into weakness (mechanical demand)")
    return "dex: balanced — no net dealer delta to re-hedge"


def dex_flow_word(x: Optional[float]) -> Optional[str]:
    """The flow-signed twin's word — the axis whose sign is actually alive.
    Positive = today's tape left dealers net LONG delta (they sold what customers
    net bought... in delta terms), so the hedge pressure is SELL; negative = the
    mirror. Today's-tape-only: the standing OI book is invisible to it."""
    if x is None:
        return None
    if x > 0:
        return ("flow-signed: today's tape left dealers net LONG delta — "
                "hedge pressure SELLS the underlying")
    if x < 0:
        return ("flow-signed: today's tape left dealers net SHORT delta — "
                "hedge pressure BUYS the underlying")
    return "flow-signed: today's tape is delta-flat"


def dex_views(contracts: list[dict], spot: float,
              minutes_to_close: Optional[float] = None) -> Optional[dict]:
    """THE DEX SURFACE — per-strike net dealer delta off the whole 0-7DTE book,
    with the 0DTE slice recorded beside it. The PRIMARY read is the blended book
    on purpose (the reverse of the regime's H7 emphasis, for the reverse reason):
    a 3DTE call's delta is hedged TODAY even though its gamma barely matters
    today — standing delta inventory lives on the whole chain, while re-hedge
    INTENSITY (gamma) lives on today's expiry. Basis is OI-preferred
    (`pick_basis`): DEX is standing inventory, with the same volume fallback the
    magnet uses early-day.

    Pure + fail-open: returns None when the book cannot be read AT ALL (no
    chain / no spot / no basis / IV blackout) — an outage is never a zero-delta
    book (slide_flip's blackout-guard doctrine, verbatim). Per-strike output is
    windowed to ±NBS_WINDOW of spot (diary hygiene, same as net_by_strike)."""
    if not contracts or not spot:
        return None
    basis = _gxb.pick_basis(contracts, spot)
    if not basis:
        return None
    if not any(_gxb._reprices(c, basis) for c in contracts):
        return None          # hollow book (no usable IV) → no read, never a zero

    by_strike: dict[float, float] = {}
    tot = 0.0
    above = below = 0.0
    n = 0
    for c in contracts:
        ex = _dealer_dex(c, spot, basis, minutes_to_close)
        if ex is None:
            continue
        k = float(c["strike"])
        n += 1
        tot += ex
        by_strike[k] = by_strike.get(k, 0.0) + ex
        if k > spot:
            above += abs(ex)
        elif k < spot:
            below += abs(ex)

    # 0DTE twin — read off its own basis, volume-preferred (slide_flows' CEX
    # doctrine verbatim: same-day OI ≈ 0 at the open, so the OI-preferred
    # headline basis would leave today's book invisible exactly when it is
    # busiest). Recorded beside the blended total, consumed by nothing.
    tot0: Optional[float] = None
    zero, _near = _gxb.split_tenor(contracts)
    basis0 = _gxb.pick_basis(zero, spot, prefer="volume") if zero else None
    if basis0:
        for c in zero:
            ex = _dealer_dex(c, spot, basis0, minutes_to_close)
            if ex is not None:
                tot0 = (tot0 or 0.0) + ex

    # FLOW-SIGNED refinement hook — own loop, own filter: its weight is the TAPE
    # (flow_buy_q/sell_q contracts, 0DTE-annotated by native_gex_feed), so a
    # heavily-traded strike with zero OI on the naive basis still counts here.
    # Customer net buy (bq − sq) ⇒ dealer net SHORT that many contracts, so the
    # dealer delta is (sq − bq)·Δ — the sign comes from the tape, never the right.
    fs_tot: Optional[float] = None
    fs_by_strike: dict[float, float] = {}
    fs_n = 0
    for c in contracts:
        bq, sq = c.get("flow_buy_q"), c.get("flow_sell_q")
        k = c.get("strike")
        if bq is None or sq is None or k is None or (c.get("iv") or 0) <= 0 \
                or c.get("dte") is None:
            continue
        tau = _gxb._tau_for(c["dte"], minutes_to_close)
        if not tau:
            continue
        d = _bs_delta(spot, float(k), c["iv"], tau, c.get("right"))
        fs = (float(sq) - float(bq)) * d * 100.0 * spot
        fs_tot = (fs_tot or 0.0) + fs
        fs_by_strike[float(k)] = fs_by_strike.get(float(k), 0.0) + fs
        fs_n += 1

    lo, hi = spot * (1.0 - _gxb.NBS_WINDOW), spot * (1.0 + _gxb.NBS_WINDOW)
    denom = above + below
    return {
        "net_dex_total": round(tot, 2),                     # $-delta, whole 0-7DTE book
        "net_dex_0dte": round(tot0, 2) if tot0 is not None else None,
        "net_dex_by_strike": [[round(k, 2), round(v, 2)]
                              for k, v in sorted(by_strike.items())
                              if lo <= k <= hi] or None,
        "dex_above_spot": round(above / denom, 4) if denom > 0 else None,
        "dex_below_spot": round(below / denom, 4) if denom > 0 else None,
        "dex_word": dex_word(tot),
        # flow-signed twin — None when no strike carries a tape annotation
        # (no ticks ⇒ unknown, never a balanced zero)
        "dex_flow_signed": round(fs_tot, 2) if fs_tot is not None else None,
        "dex_flow_by_strike": [[round(k, 2), round(v, 2)]
                               for k, v in sorted(fs_by_strike.items())
                               if lo <= k <= hi] or None,
        "dex_flow_word": dex_flow_word(fs_tot),
        # DISTINCT strikes (a call+put pair at one strike is ONE row here, matching
        # dex_flow_by_strike) — fs_n counts contract rows and would over-count 2x.
        "dex_flow_n_strikes": (len(fs_by_strike) or None) if fs_n else None,
        "basis": basis,
        "basis_0dte": basis0,
        "n": n,
        "dex_v": DEX_V,
    }
