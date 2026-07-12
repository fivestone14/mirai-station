"""lefteye_gex_box.py — the GRAVITY ENGINE: the 6-slide GEX view set (BETA, shadow).

Vocabulary: GRAVITY = dealer positioning, where price gets PULLED (magnet/walls/
flip/regime — this module). FLOW = the live force, who is SHOVING price right now
(the aggressor-flow sensor in native_gex_feed; cross-checked in slide E). The
pin-vs-trend failure mode reads as: gravity gets overpowered by flow.

An isolated rework of the dealer-gamma read (cf. lefteye_gamma). Instead of ONE
OI-weighted near-the-money slice re-pulled every scan, it pulls the FULL chain only
when it actually changed, caches it, and re-prices it cheaply into the six views a
0DTE mean-reversion fade actually needs (the "X-ray slides"):

  A · gamma flip      full-chain zero-gamma level as a BANDED zone → the regime border.
                      The REGIME itself is the sign of net gamma AT SPOT (robust to the
                      multi-crossing 0DTE curve), not a spot-vs-flip comparison.
  B · 0DTE pin+walls  same-day-only gamma → the magnet the fade reverts to + walls.
                      The magnet is the SIGNED net-GEX pin on the OI-preferred basis
                      (volume fallback early-day); the WALLS stay volume-weighted.
  C · tenor walls     stable 1-7DTE structural walls (OI-weighted) → the terrain map.
  D · volume overlay  the volume basis used for the empty-OI 0DTE strikes (in B).
  E · regime sign     long-gamma (pin) / short-gamma (trend) tag, CROSS-CHECKED
                      against aggressor flow — a pin read that fights heavy
                      one-directional buying is marked UNCERTAIN (the 0DTE
                      dealer-sign-inversion caveat).
  F · vanna/charm     net dealer VEX (vanna, dated book) + CEX (charm, 0DTE on the live
                      clock) → the vol- and time-driven drift the gamma map misses.

Motion pack (P4) + shove test (P5), 2026-07-05: the same map read as FILM, not
photo — `gex_motion` diffs the diary's gex_views rows into wall melt (share-based),
magnet walk (centroid, never the argmax), flip creep, grip slope and soft side;
`views["shove"]` re-prices net GEX at spot ± ½σ and records the SIGNED margins
(distance from flipping). Feeds the Watchtower payload and the Break-Lens gates.

Efficiency: open interest is daily-static, so the chain PULL is gated to triggers
(no cache | age | wall breach | >0.5σ spot move) while the slide re-price runs every
tick off the cache — never re-polling data that cannot have changed.

SHADOW: nothing here votes or trades. It is consumed by the (shadow) reversion
lens — now as the system's ONLY gravity read (the legacy lefteye_gamma engine is
retired; its wall/IV/spot helpers are absorbed below). The chain source is the
NATIVE ThetaData index book (primary, 2026-07-04); SPY's chain rescaled ×10 into
index strike space is the labeled degrade path when the native pull fails.
Every external lookup is injected, so the logic is pure + testable.
"""
from __future__ import annotations

import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

ET = ZoneInfo("America/New_York")

# Pull a WIDER window than the legacy 40-strike slice: the flip can sit 150-300pts
# below spot when big lower-strike puts dominate, and the slice would miss it.
SNAP_STRIKE_COUNT = 80
SNAP_DTE_MAX = 7

# Weighting floors (mirror lefteye_gamma's native thresholds).
MIN_OI_TOTAL = 500
MIN_VOL_TOTAL = 1000

# Refresh policy — OI is daily-static, so re-pull only when structure can have moved.
REFRESH_AGE_MIN = 25.0          # baseline staleness ceiling
REFRESH_MOVE_SIGMA = 0.5        # spot moved this far since the snapshot → re-pull

# Sign cross-check: aggressor flow at/above this inverts the assumed dealer sign —
# the options kind downgrades EITHER regime read (H5); the lob kind keeps the
# unsigned long-gamma-only freight-train test (see reconcile_sign).
FLOW_CONFLICT = 0.6          # options-aggressor scale: flow ≥ this ⇒ heavy one-way tape
FLOW_CONFLICT_LOB = 0.25     # order-book |tilt| scale (2 live days: tilt maxed ~0.26,
                             # break lens's own one-way bar ≈0.27) — the options 0.6
                             # is unreachable on this source, so the LOB veto needs
                             # its own threshold or it never trips (verified dead-as-0.6)

# Regime tie deadband (H3, 2026-07-12): |net GEX at spot| below this share of the
# GROSS |per-contract GEX| field is a BALANCED book, not a regime — without it a
# 0.001 imbalance flips long↔short on exactly the knife-edge days the label exists
# to name (strict >0 even stamped an exact 0.0 "short_gamma"). Ties read "uncertain",
# which every consumer already treats as an abstain (gamma vote, slide E, tablet).
REGIME_TIE_BAND = 0.02

# --- Motion pack (P4) + shove test (P5): GRAVITY read as film, not photo ------
# Shove: re-price net dealer GEX at spot ± this many σ — "if FLOW shoves price half
# a sigma, does the book flip from calming to amplifying?" Signed margins, not a bool.
SHOVE_SIGMA = 0.5
# Motion diffs compare this scan's gravity map to the latest prior diary row 4-30 min
# older (scan cadence ~5 min: <4 min re-reads the same chain pull and diffs noise —
# per-scan spot noise P50 is 0.039σ / P80 0.081σ on the 6-day re-baseline — while
# >30 min spans a regime change, not motion). Source-matched (native vs spy_proxy)
# so a degrade-path swap never masquerades as wall melt.
MOTION_MIN_LOOKBACK_MIN = 4.0
MOTION_MAX_LOOKBACK_MIN = 30.0
# Morning guard: thin 0DTE volume before ~10:30 ET makes every share/ratio read lie
# (the |γ·w| denominators are still noise), so no slope/ratio fields until 60 min
# after the open AND pin_total_gamma clears the floor. The floor ships 0.0 (time-only
# guard): no diary row carries pin_total_gamma before Mon 07-06's first live session,
# so there is nothing to calibrate against yet — tune after Monday's rows land.
MOTION_GUARD_MIN_ELAPSED_MIN = 60.0
MOTION_PIN_GAMMA_FLOOR = 0.0
# Wall relocation tolerance (pts). On the spy_proxy degrade path strikes are
# rescaled SPY strikes (× idx_spot/spy_spot, recomputed on every chain pull), so
# the SAME physical strike drifts <1.5 pts of float between rows — the 07-01
# diary shows 37/37 consecutive proxy wall reads differing in the float. Genuine
# SPX 0DTE relocation moves >= 5 pts (one strike), so anything under half a
# strike-spacing is scale drift, not a GRAVITY walk; exact float equality would
# read "relocated" every proxy scan and leave melt permanently unreadable.
WALL_RELOCATION_MIN_PTS = 2.5
# Flip-creep plausibility cap (σ). _flip_rootfind returns the zero-crossing
# NEAREST SPOT, and a multi-crossing 0DTE curve (positive-gamma islands — see
# slide_flip) swaps that crossing when spot drifts across an island midline: a
# 30-90 pt teleport with NO change in the book. A crossing swap is a REGIME-
# BORDER change, not creep, and must never be reported as velocity — over-cap
# diffs read as None (unknown), matching the relocation-is-walk-not-melt rule.
FLIP_CREEP_MAX_SIGMA = 0.5
# Centroid support floor: emit pin_centroid only when the ATTRACTIVE pull
# carries at least this share of the whole |net| field. On a storm-side
# (mostly repelling) book — the Break Lens's entire operating domain — the
# attract set is a handful of near-cancel strikes whose sign flips on tiny
# prints, reproducing the exact argmax teleport the centroid exists to fix.
# A magnet carried by ~0 mass is not a magnet (GRAVITY needs support).
PIN_CENTROID_MIN_SUPPORT = 0.05

SPY_PROXY = {"SPX": "SPY"}
STRUCTURAL_SCALE = {"SPX": 10.0}

# Phase 1 (open/close signed-weight fix, Option A): when ON, the 0DTE magnet weights the
# signed net-GEX field by NET aggressor flow |buy−sell| (native_gex_feed Phase-0 annotation)
# instead of gross volume, so a round-tripped strike (opened then closed) nets toward zero
# instead of inflating. OFF = today's behavior; `pin_signed` is recorded every scan
# regardless, so the shadow A/B can prove it beats today before this promotes.
SIGNED_PIN = False
# Churn floor (H10, 2026-07-12): a pure |net| weight makes a CHURNED strike — heavy
# two-way flow that round-trips to net ≈ 0 — vanish from the signed pin field
# entirely, and the churned strike is often exactly the one price gets nailed to
# (it is where the tape lives and where dealers re-hedge all day). Floor the weight
# at this share of the strike's GROSS two-way flow (buy + sell) so real hedge mass
# survives netting; the NET still leads whenever it is the bigger fact.
CHURN_FLOOR = 0.25


# ===========================================================================
# Pure logic (no I/O) — unit-testable
# ===========================================================================


def split_tenor(contracts: list[dict]) -> tuple[list[dict], list[dict]]:
    """(zero_dte, near_term) split by the per-contract dte: 0DTE vs 1-7DTE."""
    zero = [c for c in contracts if (c.get("dte") == 0)]
    near = [c for c in contracts if isinstance(c.get("dte"), int) and 1 <= c["dte"] <= 7]
    return zero, near


def pick_basis(contracts: list[dict], spot: float, *, prefer: str = "open_interest") -> Optional[str]:
    """Choose OI if there's enough of it near the money, else volume, else None.
    0DTE strikes carry ~0 OI at the open, so they fall through to volume."""
    if not spot:
        return None
    near = [c for c in contracts if c.get("strike") is not None
            and abs(c["strike"] - spot) <= spot * 0.05]
    oi = sum((c.get("open_interest") or 0) for c in near)
    vol = sum((c.get("volume") or 0) for c in near)
    if prefer == "open_interest" and oi >= MIN_OI_TOTAL:
        return "open_interest"
    if vol >= MIN_VOL_TOTAL:
        return "volume"
    if oi > 0:
        return "open_interest"
    return None


# --- absorbed from the retired legacy engine (lefteye_gamma) ------------------
def _atm_iv(contracts: list[dict], spot: float) -> Optional[float]:
    """IV of the contract nearest the money — the σ yardstick's raw input.
    Call side preferred: snapshot ATM put IVs go stale-wrong (parity violations of
    ~2× observed in weekend chains, put side reading half the call side), while the
    two sides agree when quotes are healthy — so the call read is always the safe one.
    Requires iv > 0: a broker failed-solve sentinel (Schwab's -999 → iv −9.99) is
    truthy, and left in the pool would hand the σ yardstick a NEGATIVE vol."""
    pool = [c for c in contracts if (c.get("iv") or 0) > 0 and c.get("strike") is not None]
    if not pool:
        return None
    calls = [c for c in pool if c.get("right") == "call"]
    return min(calls or pool, key=lambda c: abs(c["strike"] - spot)).get("iv")


def _weight_wall(contracts: list[dict], spot: float, side: str,
                 weight_key: str) -> Optional[float]:
    """OI/volume wall: the strike with the largest single-side weight on the
    requested side of spot (calls ≥ spot, puts ≤ spot)."""
    agg: dict[float, float] = {}
    for c in contracts:
        if c.get("right") != side or c.get("strike") is None:
            continue
        w = c.get(weight_key) or 0
        if w > 0:
            agg[c["strike"]] = agg.get(c["strike"], 0.0) + w
    cand = [(k, v) for k, v in agg.items() if (k >= spot if side == "call" else k <= spot)]
    return max(cand, key=lambda kv: kv[1])[0] if cand else None




# --- Black-Scholes reprice + GEX(S) root-find (the robust flip) --------------
# The cumulative-sign-scan flip (lefteye_gamma's method) reads gamma only at the
# current spot and teleports hundreds of points on a sub-$1 move (FlashAlpha) — worst
# on 0DTE. The standard fix is to REPRICE every option's gamma via Black-Scholes across
# a spot grid and solve net GEX(S)=0 on that smooth curve (verified Q2, citations in
# the build notes). A uncertainty band cannot rescue the scan, so we don't reuse it.

_TRADING_DAYS = 252.0
_TAU_FLOOR_DAYS = 0.5          # treat 0DTE as ~half a session so BS gamma stays finite
_FLIP_SPAN = 0.20             # ±20% of spot candidate grid (coarse outer sweep)
_FLIP_GRID = 61               # coarse-grid resolution over the full span
_FLIP_INNER = 0.03            # dense inner window (±3% of spot) where late-day 0DTE
                              #   gamma islands live — sampled fine enough to catch a
                              #   short-gamma pocket whose two zero-crossings would fall
                              #   inside one coarse ~46-pt cell (the old blind spot)
_FLIP_INNER_STEP = 0.0005     # inner step ≈ 0.05% of spot (~3 pts at 6000): below the
                              #   5-pt strike grid, so no strike-scale island is missed


def _bs_gamma(S: float, K: float, sigma: float, tau: float) -> float:
    """Black-Scholes gamma Γ = φ(d1)/(S·σ·√τ), r=0. 0 on degenerate inputs."""
    if S <= 0 or K <= 0 or sigma <= 0 or tau <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * tau) / (sigma * math.sqrt(tau))
    return math.exp(-0.5 * d1 * d1) / (math.sqrt(2 * math.pi) * S * sigma * math.sqrt(tau))


def _reprices(c: dict, weight_key: str) -> bool:
    """Whether a contract contributes to the BS-repriced GEX sum: a strike, a positive
    weight on the chosen basis, a usable IV (> 0, so a broker −999 → iv −9.99 sentinel
    is excluded), and a dte. The SINGLE source of truth for `_net_gex_at`'s per-contract
    filter and `slide_flip`'s blackout guard, so the two can never drift apart."""
    return (bool(c.get("strike")) and bool(c.get(weight_key) or 0)
            and (c.get("iv") or 0) > 0 and c.get("dte") is not None)


def _stored_gamma(c: dict) -> float:
    """The stored broker/BS-fill gamma, clamped to ≥ 0. Gamma is physically non-negative,
    so a negative stored value is a broker −999 failed-solve sentinel → 0, never fake mass."""
    g = c.get("gamma") or 0.0
    return g if g > 0 else 0.0


def _net_gex_at(contracts: list[dict], S: float, weight_key: str,
                minutes_to_close: Optional[float] = None) -> float:
    """Net signed dealer GEX repriced at candidate spot S (calls +, puts −).
    0DTE gamma runs on the LIVE clock when supplied (one clock for the whole read —
    charm already did; pin/flip/regime now match), else the half-session floor."""
    tot = 0.0
    for c in contracts:
        if not _reprices(c, weight_key):
            continue
        dte = c["dte"]
        tau = _tau_for(dte, minutes_to_close) if dte == 0 else max(float(dte), _TAU_FLOOR_DAYS) / _TRADING_DAYS
        if not tau:
            continue
        gex = _bs_gamma(S, c["strike"], c["iv"], tau) * c[weight_key] * 100.0 * S * S * 0.01
        tot += (gex if c.get("right") == "call" else -gex)
    return tot


def _gross_gex_at(contracts: list[dict], S: float, weight_key: str,
                  minutes_to_close: Optional[float] = None) -> float:
    """GROSS |dealer GEX| at S — the same per-contract terms _net_gex_at signs and
    sums, summed UNSIGNED. The tie-band denominator: how much gamma mass exists at
    spot regardless of which side it lands on (net/gross ≈ 0 ⇒ a balanced book)."""
    tot = 0.0
    for c in contracts:
        if not _reprices(c, weight_key):
            continue
        dte = c["dte"]
        tau = _tau_for(dte, minutes_to_close) if dte == 0 else max(float(dte), _TAU_FLOOR_DAYS) / _TRADING_DAYS
        if not tau:
            continue
        tot += abs(_bs_gamma(S, c["strike"], c["iv"], tau) * c[weight_key] * 100.0 * S * S * 0.01)
    return tot


def _flip_grid(spot: float) -> list[float]:
    """Non-uniform GEX(S) sampling grid: a DENSE inner window near spot (±_FLIP_INNER
    at ~half a strike step) unioned with the COARSE wide span (±_FLIP_SPAN). Uniform
    46-pt cells cannot see a short-gamma pocket whose two zero-crossings both fall
    inside one cell; the dense inner grid resolves the late-day near-spot islands while
    the coarse outer grid still catches a far flip cheaply."""
    lo, hi = spot * (1 - _FLIP_SPAN), spot * (1 + _FLIP_SPAN)
    pts = {round(lo + (hi - lo) * i / (_FLIP_GRID - 1), 6) for i in range(_FLIP_GRID)}
    ilo, ihi = spot * (1 - _FLIP_INNER), spot * (1 + _FLIP_INNER)
    step = max(spot * _FLIP_INNER_STEP, 1e-6)
    n = int(round((ihi - ilo) / step))
    pts.update(round(ilo + j * step, 6) for j in range(n + 1))
    return sorted(pts)


def _bisect_gex(contracts: list[dict], lo: float, vlo: float, hi: float, vhi: float,
                weight_key: str, minutes_to_close: Optional[float],
                tol: float = 0.5, iters: int = 40) -> tuple[float, float, float]:
    """Refine a sign-changing bracket [lo, hi] of net GEX(S)=0 to ~`tol` points by
    bisection — kills the ~20-pt misplacement of interpolating across a wide cell.
    Returns (root, bracket_lo, bracket_hi) so the caller reports an honest tight band."""
    for _ in range(iters):
        if hi - lo <= tol:
            break
        mid = 0.5 * (lo + hi)
        vm = _net_gex_at(contracts, mid, weight_key, minutes_to_close)
        if vm == 0.0:
            return mid, mid, mid
        if (vm < 0) == (vlo < 0):
            lo, vlo = mid, vm
        else:
            hi, vhi = mid, vm
    root = lo - vlo * (hi - lo) / (vhi - vlo) if (vhi - vlo) != 0 else 0.5 * (lo + hi)
    return root, lo, hi


def _flip_rootfind(contracts: list[dict], spot: float, weight_key: str,
                   minutes_to_close: Optional[float] = None):
    """Solve net GEX(S)=0 on the BS-repriced curve over ±_FLIP_SPAN of spot, sampled
    dense near spot (`_flip_grid`) and refined by bisection. Returns (flip, band) where
    band = the refined bracket (the honest uncertainty), preferring the zero-crossing
    nearest spot. An identically-zero curve (no usable IV) returns no flip — never a
    root fabricated at spot."""
    if not spot:
        return None, None
    grid = _flip_grid(spot)
    vals = [(S, _net_gex_at(contracts, S, weight_key, minutes_to_close)) for S in grid]
    if all(v == 0.0 for _, v in vals):
        return None, None          # hollow book → no border, not a flip pinned to spot
    best = None
    for (s0, v0), (s1, v1) in zip(vals, vals[1:]):
        if v0 == 0.0:
            cand, band = s0, [round(s0, 4), round(s0, 4)]
        elif (v0 < 0) != (v1 < 0):
            cand, blo, bhi = _bisect_gex(contracts, s0, v0, s1, v1, weight_key, minutes_to_close)
            band = [round(blo, 4), round(bhi, 4)]
        else:
            continue
        if best is None or abs(cand - spot) < abs(best[0] - spot):
            best = (cand, band)
    if best is None:
        return None, None
    return round(best[0], 4), best[1]


def slide_flip(contracts: list[dict], spot: float,
               minutes_to_close: Optional[float] = None) -> dict:
    """BORDER FINDER (Slide A + E): finds the price where dealer behavior flips
    from calming to amplifying, with an honest uncertainty band.
    Full-chain gamma flip (BS-repriced root-find, banded) + regime.

    Regime is the SIGN OF NET GAMMA AT SPOT, never a spot-vs-flip comparison. A 0DTE
    chain's net-GEX(S) curve has SEVERAL zero-crossings, so price can sit inside a
    positive-gamma "island" bounded by a flip ABOVE and a flip BELOW it. Asking only
    "is spot above the nearest flip?" then mislabels that island as short-gamma whenever
    the nearest crossing happens to sit above spot — the bug that pinned every scan to
    short_gamma even while gamma at spot was plainly positive. Reading the sign at spot
    is robust to multi-crossing curves; the flip is retained purely as the zone BORDER.
    """
    out = {"flip": None, "flip_band": None, "net_gex": None,
           "regime": "unknown", "basis": None}
    basis = pick_basis(contracts, spot)
    if not basis or not spot:
        return out
    # DATA-BLACKOUT GUARD: a chain with strikes (OI/volume) but no usable IV sums to
    # net GEX 0.0 in _net_gex_at — every contract is skipped. That is a feed outage,
    # NOT a short-gamma read; without this guard the hollow all-zero curve would be
    # stamped "short_gamma" with full confidence and a fake flip pinned to spot. Require
    # at least one contract that actually reprices (the SAME predicate _net_gex_at sums).
    if not any(_reprices(c, basis) for c in contracts):
        return out
    flip, band = _flip_rootfind(contracts, spot, basis, minutes_to_close)  # border (+ band)
    net = _net_gex_at(contracts, spot, basis, minutes_to_close)  # signed dealer GEX at spot
    # TIE DEADBAND (H3, 2026-07-12): call a tie a tie. On the border the label exists
    # to name, |net| is a rounding-size residual of two huge cancelling sides — its
    # sign is noise, and strict >0 flipped the whole pin/push regime on it (an exact
    # 0.0 read even stamped "short_gamma"). Below the band the regime abstains as
    # "uncertain" — the same abstain value slide E already emits, so every consumer
    # (gamma vote, watchtower wording, UW buckets) handles it today.
    gross = _gross_gex_at(contracts, spot, basis, minutes_to_close)
    if gross > 0 and abs(net or 0.0) < REGIME_TIE_BAND * gross:
        regime = "uncertain"
    else:
        regime = "long_gamma" if (net or 0.0) > 0 else "short_gamma"
    out.update({"flip": flip, "flip_band": band, "net_gex": net,
                "regime": regime, "basis": basis})
    return out


def _pin_profile(contracts: list[dict], weight_key: str):
    """PIN STRENGTH + CONCENTRATION inputs from the same |gamma·weight| table the
    magnet uses: (pin_strike, total_weight, top_share).
      total_weight — how hard today's whole magnet field pulls (STRENGTH raw;
                     judged vs a trailing baseline in the nightly report card);
      top_share    — the magnet strike's share of that pull (CONCENTRATION:
                     ~0.6 = one towering strike, aim precisely; ~0.15 = a smear,
                     treat the magnet as a neighborhood, not a price)."""
    by_strike: dict[float, float] = {}
    for c in contracts:
        g = _stored_gamma(c); w = c.get(weight_key) or 0; k = c.get("strike")
        if not g or not w or k is None:
            continue
        by_strike[k] = by_strike.get(k, 0.0) + abs(g * w)
    if not by_strike:
        return None, None, None
    pin = max(by_strike, key=lambda k: by_strike[k])
    total = sum(by_strike.values())
    return pin, total, (by_strike[pin] / total if total > 0 else None)


def _live_gamma(c: dict, spot: float, minutes_to_close: Optional[float]) -> float:
    """Per-contract gamma repriced on the LIVE clock: Black-Scholes from IV when the
    strike carries usable IV, else the stored broker/BS-fill greek. This is the ONE
    gamma basis the signed pin field, the gamma walls and the wall-melt shares all
    share — so nothing reads a frozen fetch-time 0DTE gamma late in the session (the
    ~12:45-clock wall that sat 45-50 pts off into the close). Stored gamma is clamped
    to ≥ 0 so a broker −999 failed-solve sentinel can never survive as fake mass."""
    iv, k, dte = c.get("iv"), c.get("strike"), c.get("dte")
    if iv and iv > 0 and k is not None and dte is not None:
        tau = _tau_for(dte, minutes_to_close)
        if tau:
            return _bs_gamma(spot, float(k), float(iv), tau)
    return _stored_gamma(c)


def _net_gex_by_strike(contracts: list[dict], weight_key: Optional[str], spot: float,
                       minutes_to_close: Optional[float] = None) -> dict[float, float]:
    """The SIGNED PIN FIELD — per-strike NET dealer GEX (calls +, puts −), gamma
    repriced at the live spot on the live 0DTE clock (`_live_gamma`). One table,
    three readers: the magnet argmax (`_net_pin_profile`), the motion pack's centroid
    walk, and the soft-side mass split (`_pin_field_stats`) — one brain, one clock.
    Returns an empty dict on no spot / no weighted strikes (callers degrade to None)."""
    by_strike: dict[float, float] = {}
    if not spot:
        return by_strike
    for c in contracts:
        k = c.get("strike"); w = c.get(weight_key) or 0
        if k is None or not w:
            continue
        g = _live_gamma(c, spot, minutes_to_close)
        if not g:
            continue
        by_strike[k] = by_strike.get(k, 0.0) + (g * w if c.get("right") == "call" else -g * w)
    return by_strike


# --- magnet zones (2026-07-09, LIVE) ------------------------------------------
ZONE_FLOOR = 0.10    # min share of total attractive pull for RIVAL zones (ranks 2+);
                     # zone-1 is exempt so pin-existence stays in parity with argmax
ZONE_CAP = 4         # max zones recorded per scan (diary hygiene)
ZONE_RIVAL = 0.8     # rival zone >= this x leader => the field is CONTESTED
ZONE_MAX_WIDTH = 50.0  # pts — a zone spanning wider than this is a SMEAR, not a
                       # magnet: its weighted center is no-man's-land (review
                       # 07-09: real slices merged into one 225-pt "zone" whose
                       # center sat between the true 7500/7550 magnets). The
                       # LIVE pin only swaps to zone-1's center when zone-1 is
                       # tighter than this; zones are still recorded either way.
CONTESTED_BLEND_MAX_GAP = 100.0  # pts — rival zone centers farther apart than this
                                 # don't blend (H4): the middle of two distant magnets
                                 # is no-man's-land, same physics as the smear guard.

# PER-STRIKE FIELD persistence window (2026-07-10; widened 07-12). The signed
# net-GEX-by-strike array is saved COMPACT — only strikes within ±NBS_WINDOW of
# spot — so the tablet can draw MEASURED bars without bloating the diary. ±3% ≈
# ±227pts at SPX 7550 (~90 five-pt strikes): the coverage audit found ±2% is only
# ~1.5σ on a high-vol morning (live σ reached 1.30% of spot on 07-10), so a wall
# the engine sees at 1.6-2σ would never reach the drawn bars.
NBS_WINDOW = 0.03


def pin_zones(by_strike: dict, spot: float,
              floor: float = ZONE_FLOOR, cap: int = ZONE_CAP):
    """MAGNET ZONES — the attractive field read as GROUPS, not a single winner.
    Real books are often multi-magnet (2026-07-09: 7550 ~51% vs 7500 ~42% of the
    pull, 50 pts apart — the argmax magnet 'teleported' between them all day).
    Gap-based clustering of the ATTRACTIVE strikes (net > 0): two attractive
    strikes join one zone when their gap <= 2x the MEDIAN neighbor step of the
    full grid — a dynamic rule because the grid is non-uniform (5-pt core,
    10-pt wings, and the SPY-proxy degrade path rescales to a ~10.4-pt grid
    that a fixed 5-pt hole rule would shatter into single-strike zones).
    Zone share = zone mass / TOTAL attractive mass (mostly-repelling books are
    the norm — an |all-mass| denominator would null ordinary days). Zone-1
    always survives; the floor prunes ranks 2+ only. Returns (zones, contested):
    zones ranked by share desc, each {center, share, side, lo, hi, n};
    contested = zone-2 pulling >= ZONE_RIVAL x zone-1."""
    if not by_strike or not spot:
        return [], False
    strikes = sorted(by_strike)
    steps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    join_gap = 2.0 * (statistics.median(steps) if steps else 5.0)
    attract = [(k, by_strike[k]) for k in strikes if by_strike[k] > 0]
    if not attract:
        return [], False
    total = sum(v for _, v in attract)
    clusters, cur = [], [attract[0]]
    for k, v in attract[1:]:
        if k - cur[-1][0] <= join_gap:
            cur.append((k, v))
        else:
            clusters.append(cur)
            cur = [(k, v)]
    clusters.append(cur)
    zones = []
    for cl in clusters:
        mass = sum(v for _, v in cl)
        center = sum(k * v for k, v in cl) / mass
        lo, hi = cl[0][0], cl[-1][0]
        side = "at" if lo <= spot <= hi else ("above" if center > spot else "below")
        zones.append({"center": round(center, 2), "share": round(mass / total, 4),
                      "side": side, "lo": round(lo, 2), "hi": round(hi, 2),
                      "n": len(cl)})
    zones.sort(key=lambda z: -z["share"])
    zones = [zones[0]] + [z for z in zones[1:] if z["share"] >= floor]
    zones = zones[:cap]
    contested = len(zones) >= 2 and zones[1]["share"] >= ZONE_RIVAL * zones[0]["share"]
    return zones, contested


def _net_pin_profile(contracts: list[dict], weight_key: str, spot: float,
                     minutes_to_close: Optional[float] = None):
    """SIGNED PIN — per-strike NET dealer GEX (calls +, puts −), gamma repriced at
    the live spot on the live 0DTE clock: the same gamma picture the regime reads
    (one brain, one clock — kills the cached-vs-live split and the frozen half-day
    pin late in the session). The magnet is the strongest ATTRACTIVE strike — the
    max POSITIVE net (dealers long gamma there hedge price toward it); a strike
    whose call and put forces cancel exerts no net pull and cannot be the magnet
    (the unsigned 'Absolute Gamma' read survives alongside as `pin_abs`). Returns
    (pin, total_abs_net, top_share); pin is None when NO strike attracts — an
    all-repelling book has no pin, and the magnet honestly falls back to the flip."""
    if not spot:
        return None, None, None
    by_strike = _net_gex_by_strike(contracts, weight_key, spot, minutes_to_close)
    if not by_strike:
        return None, None, None
    total = sum(abs(v) for v in by_strike.values())
    pin, net = max(by_strike.items(), key=lambda kv: kv[1])
    if net <= 0:
        return None, round(total, 4), None
    return pin, round(total, 4), (net / total if total > 0 else None)


def _pin_field_stats(zero_dte: list[dict], mag_basis: Optional[str], wall_basis: str,
                     spot: float, minutes_to_close: Optional[float],
                     call_wall_gamma: Optional[float],
                     put_wall_gamma: Optional[float]) -> dict:
    """MOTION-PACK RAW MATERIAL (P4) — the per-scan GRAVITY-shape fields the diary
    gets diffed into melt / walk / soft-side (`gex_motion`). Everything here is a
    SHARE or a signed sum, never raw mass: 0DTE gamma is volume-weighted and only
    GROWS with the session, so a raw diff can only ever read "growth" — the share
    is the honest melt denominator.

      {side}_wall_gamma_share  the wall strike's |γ·w| mass (same side-filtered
                               table `_gamma_wall` names the wall from, on the wall
                               basis) over total |γ·w| across ALL strikes, both sides;
      pin_centroid             gamma-weighted centroid Σ(K·net_K)/Σ(net_K) over the
                               ATTRACTIVE (net>0) strikes of the signed pin field —
                               the smooth magnet the walk diff is allowed to read
                               (the argmax pin teleports 30-65 pts between stillness);
                               None when no strike attracts;
      gamma_above/below_spot   Σ|net_K| each side of spot (K == spot excluded) —
                               the soft-side read: GRAVITY thins where the mass isn't."""
    out = {"call_wall_gamma_share": None, "put_wall_gamma_share": None,
           "pin_centroid": None, "gamma_above_spot": None, "gamma_below_spot": None}
    if not zero_dte or not spot:
        return out
    # wall shares — the wall's slice of the WHOLE |γ·w| field (both sides, wall basis)
    total = 0.0
    wall_mass = {"call": 0.0, "put": 0.0}
    wall_at = {"call": call_wall_gamma, "put": put_wall_gamma}
    for c in zero_dte:
        w = c.get(wall_basis) or 0; k = c.get("strike")
        if not w or k is None:
            continue
        g = _live_gamma(c, spot, minutes_to_close)   # live clock — matches _gamma_wall
        if not g:
            continue
        m = abs(g * w)
        total += m
        side = c.get("right")
        if side in wall_mass and wall_at.get(side) is not None and k == wall_at[side]:
            wall_mass[side] += m
    if total > 0:
        if call_wall_gamma is not None:
            out["call_wall_gamma_share"] = round(wall_mass["call"] / total, 4)
        if put_wall_gamma is not None:
            out["put_wall_gamma_share"] = round(wall_mass["put"] / total, 4)
    # signed pin field — the same map (and live clock) the magnet reads
    net_map = _net_gex_by_strike(zero_dte, mag_basis, spot, minutes_to_close)
    if net_map:
        attract = {k: v for k, v in net_map.items() if v > 0}
        field = sum(abs(v) for v in net_map.values())
        # support floor: a centroid carried by near-zero attractive mass flickers
        # on tiny prints (storm-side books) — thin support reads None, honestly
        if attract and field > 0:
            pull = sum(attract.values())
            if pull >= PIN_CENTROID_MIN_SUPPORT * field:
                out["pin_centroid"] = round(sum(k * v for k, v in attract.items()) / pull, 4)
        out["gamma_above_spot"] = round(sum(abs(v) for k, v in net_map.items() if k > spot), 4)
        out["gamma_below_spot"] = round(sum(abs(v) for k, v in net_map.items() if k < spot), 4)
    return out


def slide_0dte(zero_dte: list[dict], spot: float,
               minutes_to_close: Optional[float] = None) -> dict:
    """MAGNET FINDER (Slide B + D): names today's pull-strike and today's
    ceiling/floor walls. `pin` = the strongest ATTRACTIVE strike by SIGNED net
    dealer GEX (calls +, puts −, repriced on the live clock) — GRAVITY is
    positioning pull, so the pin prefers the structural OI basis and falls back
    to volume early in the day. The legacy unsigned max-|gamma×weight| read is
    kept as `pin_abs` (fill-ledger recency weighted when available, per the
    stale-755-pin failure of 2026-07-02) so the two magnet definitions stay
    comparable in the diary. Walls stay on the cumulative volume basis (they
    are structural bounds, not a chase target)."""
    out = {"pin": None, "call_wall": None, "put_wall": None,
           "call_wall_gamma": None, "put_wall_gamma": None,
           "basis": None, "pin_basis": None, "n": len(zero_dte),
           "pin_total_gamma": None, "pin_top_share": None,
           "pin_abs": None, "pin_abs_basis": None,
           "pin_signed": None, "pin_signed_total": None, "pin_signed_share": None,
           "pin_signed_v": None,
           # motion-pack raw material (P4): wall SHARES + signed-field shape,
           # recorded every scan so the diary can be diffed into melt/walk/soft-side
           "call_wall_gamma_share": None, "put_wall_gamma_share": None,
           "pin_centroid": None, "gamma_above_spot": None, "gamma_below_spot": None,
           # magnet zones (2026-07-09, LIVE): the field as groups + the legacy
           # argmax kept beside the live zone-center pin for the nightly A/B
           "pin_zones": None, "pin_contested": None, "pin_argmax": None,
           "pin_blended": None, "zone1_share": None, "net_by_strike": None}
    if not zero_dte or not spot:
        return out
    basis = pick_basis(zero_dte, spot, prefer="volume")   # walls: 0DTE → volume first
    if not basis:
        return out
    # the magnet reads structural positioning: OI when it exists, else volume
    mag_basis = pick_basis(zero_dte, spot, prefer="open_interest")
    pin, pin_total, pin_share = _net_pin_profile(zero_dte, mag_basis, spot, minutes_to_close)
    # legacy unsigned pin (recency fill-weighted when the ledger annotated) — recorded only
    abs_key = basis
    if any((c.get("fill_weight") or 0) > 0 for c in zero_dte):
        abs_key = "fill_weight"
    pin_abs, _, _ = _pin_profile(zero_dte, abs_key)
    # Phase 1 (Option A, SHADOW): the SIGNED pin — weight the net-GEX field by NET aggressor
    # flow |buy − sell| (Phase-0 annotation) instead of gross volume, so a round-tripped strike
    # (opened then closed) nets toward zero instead of inflating. Recorded as pin_signed every
    # scan for the A/B; only drives the live magnet when SIGNED_PIN is on. Fail-open: no ticks
    # → pin_signed None and the live pin is untouched.
    _sig_ok = False
    for c in zero_dte:
        nf = c.get("net_flow")
        if nf is not None:
            # H10: keep a churned pin's gamma weight — floor |net| at CHURN_FLOOR
            # of the gross two-way flow so a round-tripped strike keeps its real
            # hedge mass instead of netting itself off the map. Fail-open: no
            # buy/sell annotation → the plain |net| weight, exactly as before.
            gross_f = (c.get("flow_buy") or 0) + (c.get("flow_sell") or 0)
            c["signed_weight"] = max(abs(float(nf)), CHURN_FLOOR * float(gross_f))
            _sig_ok = True
    if _sig_ok:
        p_sig, pt_sig, ps_sig = _net_pin_profile(zero_dte, "signed_weight", spot, minutes_to_close)
        out["pin_signed"] = round(p_sig, 4) if p_sig is not None else None
        out["pin_signed_total"] = round(pt_sig, 4) if pt_sig is not None else None
        out["pin_signed_share"] = round(ps_sig, 4) if ps_sig is not None else None
        # weighting-era tag: v2 = churn-floored (H10, 2026-07-12); pre-07-12
        # rows carried plain |net| with no tag — the A/B must never mix eras.
        out["pin_signed_v"] = 2
        if SIGNED_PIN and p_sig is not None:
            pin, pin_total, pin_share = p_sig, pt_sig, ps_sig   # flag on → live magnet
    # MAGNET ZONES (2026-07-09, LIVE swap — user-sanctioned): the attractive
    # field clustered into groups; the live magnet becomes zone-1's weighted
    # CENTER (the strongest GROUP, not the strongest single strike), and the
    # legacy argmax rides beside it as pin_argmax for the nightly tape-measure
    # A/B. Zones compute on the SAME weight field that serves the live pin
    # (including a future SIGNED_PIN flip) — one live magnet definition, ever.
    # Fail-open: any surprise leaves the argmax pin exactly as it always was.
    zones_read = False
    try:
        zone_key = "signed_weight" if (SIGNED_PIN and _sig_ok) else mag_basis
        zmap = _net_gex_by_strike(zero_dte, zone_key, spot, minutes_to_close) if zone_key else {}
        zones, contested = pin_zones(zmap, spot)
        zones_read = bool(zone_key)                 # compute genuinely ran
    except Exception:
        zones, contested = [], False
    out["pin_argmax"] = round(pin, 4) if pin is not None else None
    if zones_read:
        # clean-empty records [] (all-repelling READ) — None stays "no read"
        out["pin_zones"] = zones or []
        out["pin_contested"] = contested
    if zones:
        out["zone1_share"] = zones[0]["share"]
        # MEGA-ZONE GUARD: only a TIGHT zone-1 may steer the live magnet — a
        # smear's centroid is no-man's-land between the true magnets; the
        # argmax keeps the wheel, the zones still ride for the A/B + tablet.
        if (zones[0]["hi"] - zones[0]["lo"]) <= ZONE_MAX_WIDTH:
            pin = zones[0]["center"]                # ← the LIVE magnet
            # CONTESTED-MAGNET BLEND (H4, 2026-07-12): when two magnets fight,
            # stand in the middle. With a rival pulling ≥ ZONE_RIVAL× the leader,
            # zone-1 flips ownership on tiny prints and the live magnet teleports
            # the full inter-zone gap (~50 pts on 2026-07-09) — a fake direction
            # signal on exactly the choppy days that lose money. Blend toward the
            # rival with a weight that is 0 at the contested threshold and ½ at a
            # perfect tie — CONTINUOUS through both the threshold and the flip of
            # zone order, so the magnet glides instead of jumping. Rivals must
            # both be tight and near each other (a far/smeared rival keeps
            # today's behavior; the contested flag rides either way).
            if (contested
                    and (zones[1]["hi"] - zones[1]["lo"]) <= ZONE_MAX_WIDTH
                    and abs(zones[1]["center"] - zones[0]["center"]) <= CONTESTED_BLEND_MAX_GAP):
                _r = zones[1]["share"] / zones[0]["share"]      # ∈ [ZONE_RIVAL, 1]
                _t = (_r - ZONE_RIVAL) / (1.0 - ZONE_RIVAL)     # 0 at threshold → 1 at tie
                pin = zones[0]["center"] + 0.5 * _t * (zones[1]["center"] - zones[0]["center"])
                out["pin_blended"] = round(pin, 4)              # visible: blend is never silent
    call_wall_gamma = _gamma_wall(zero_dte, spot, "call", basis, minutes_to_close)
    put_wall_gamma = _gamma_wall(zero_dte, spot, "put", basis, minutes_to_close)
    out["pin_total_gamma"] = round(pin_total, 4) if pin_total is not None else None
    out["pin_top_share"] = round(pin_share, 4) if pin_share is not None else None
    # motion-pack fields — fail-open: a stats failure leaves the new fields None
    # and the legacy slide exactly as it always was
    try:
        out.update(_pin_field_stats(zero_dte, mag_basis, basis, spot, minutes_to_close,
                                    call_wall_gamma, put_wall_gamma))
    except Exception:
        pass
    # PER-STRIKE SIGNED FIELD (2026-07-10): the real net dealer GEX per strike
    # (calls +, puts −, repriced on the live clock) that the magnet/zones are
    # derived from — the SAME field, not a model. Persisted COMPACT (windowed to
    # ±NBS_WINDOW of spot, rounded) so the tablet draws MEASURED per-strike bars
    # instead of a Gaussian. Fail-open: any error leaves the key None.
    try:
        # SAME weight field the live magnet/zones use — follows the SIGNED_PIN flip so
        # the drawn bars can never diverge from the pin they are meant to explain.
        _nbs_basis = "signed_weight" if (SIGNED_PIN and _sig_ok) else mag_basis
        _nbs = _net_gex_by_strike(zero_dte, _nbs_basis, spot, minutes_to_close)
        if _nbs:
            _lo, _hi = spot * (1.0 - NBS_WINDOW), spot * (1.0 + NBS_WINDOW)
            # round the STRIKE too: SPY-proxy strikes are rescaled floats (~10.4-pt
            # grid) that would otherwise serialize at full precision and bloat the
            # diary — round(2) keeps it compact and grid-alignable by the tablet.
            out["net_by_strike"] = [[round(k, 2), round(v, 3)]
                                    for k, v in sorted(_nbs.items())
                                    if _lo <= k <= _hi]
    except Exception:
        pass
    out.update({"pin": round(pin, 4) if pin is not None else None,
                "call_wall": _weight_wall(zero_dte, spot, "call", basis),
                "put_wall": _weight_wall(zero_dte, spot, "put", basis),
                "call_wall_gamma": call_wall_gamma,
                "put_wall_gamma": put_wall_gamma,
                "basis": basis, "pin_basis": mag_basis,
                "pin_abs": round(pin_abs, 4) if pin_abs is not None else None,
                "pin_abs_basis": abs_key})
    return out


def slide_tenor_walls(near_term: list[dict], spot: float) -> dict:
    """TERRAIN MAP (Slide C): the stable multi-day walls that don't drift
    intraday — 1-7DTE structural walls + flip (OI-weighted)."""
    out = {"call_wall": None, "put_wall": None,
           "call_wall_gamma": None, "put_wall_gamma": None, "flip": None, "basis": None}
    if not near_term or not spot:
        return out
    basis = pick_basis(near_term, spot)
    if not basis:
        return out
    flip, _band = _flip_rootfind(near_term, spot, basis)   # same robust method as slide A
    out.update({"call_wall": _weight_wall(near_term, spot, "call", basis),
                "put_wall": _weight_wall(near_term, spot, "put", basis),
                # dated terrain walls stay on stored gamma → stable intraday (reprice=False)
                "call_wall_gamma": _gamma_wall(near_term, spot, "call", basis, reprice=False),
                "put_wall_gamma": _gamma_wall(near_term, spot, "put", basis, reprice=False),
                "flip": flip, "basis": basis})
    return out


def reconcile_sign(regime: str, aggressor_flow: Optional[float],
                   conflict: float = FLOW_CONFLICT, flow_kind: str = "options") -> dict:
    """GRAVITY-vs-FLOW CHECK (Slide E): downgrades the gamma-sign read when the tape
    says the assumed dealer sign is wrong. Returns the (possibly downgraded) sign + a
    confidence multiplier the consumer applies. `conflict` is the source-scaled trip
    level (FLOW_CONFLICT vs FLOW_CONFLICT_LOB); `flow_kind` names the tape's semantics:

    "options" — the signed 0DTE aggressor tilt (+ = customers BUYING calls / SELLING
    puts). DIRECTION-AWARE (H5, 2026-07-12): the whole GEX map assumes dealers are
    long calls / short puts, and it is exactly the POSITIVE tape (customers taking
    the other side of both legs) that inverts that assumption. Negative flow —
    customers selling calls / buying puts — CONFIRMS it. The old abs() test was deaf
    to this: it downgraded confirming tape as often as inverting tape and could never
    tell the sign-flip it was built to catch from its harmless mirror. When the tape
    inverts the assumption, EITHER regime read (long or short) is suspect — the sign
    convention under both is the same one the tape just contradicted.

    "lob" — the ~30s order-book tilt (+ = buyers pressing the book). The equity tape
    carries no dealer-positioning information, so its sign can't confirm or deny the
    convention; heavy one-way pressure in EITHER direction is a freight train a pin
    read should not stand in front of (the 2026-07-02 losing cluster). Unsigned test,
    long-gamma only — exactly the old behavior, now scoped to the source it fits."""
    out = {"sign": regime, "flow": aggressor_flow, "agrees": True, "confidence": 1.0}
    if aggressor_flow is None or regime not in ("long_gamma", "short_gamma"):
        # unknown flow can't confirm; and a tie/unknown regime (H3) has no sign
        # for the tape to confirm OR deny — either way, abstain, don't bless
        out["agrees"] = None
        return out
    if flow_kind == "lob":
        if regime == "long_gamma" and abs(aggressor_flow) >= conflict:
            out.update({"sign": "uncertain", "agrees": False, "confidence": 0.5})
    elif aggressor_flow >= conflict:
        # customers buying calls / selling puts — the assumed dealer sign is inverted
        out.update({"sign": "uncertain", "agrees": False, "confidence": 0.5})
    return out


# --- Vanna / Charm exposure (VEX/CEX) + gamma-weighted walls -----------------
# Additive, SHADOW. Two reads the naive gamma-only map misses:
#   • VEX (vanna exposure) — net dealer ∂delta/∂vol. Which way vol-driven hedging
#     pushes: positive net dealer vanna ⇒ falling IV (rallies) forces dealer BUYING
#     → the reflexive "vanna grind-up"; negative ⇒ the opposite drift.
#   • CEX (charm exposure) — net dealer ∂delta/∂time. The delta the book bleeds per
#     day → the predictable into-the-close / into-OpEx drift direction.
# Sign convention mirrors GEX: dealers long calls (+), short puts (−). In this
# engine's r=q=0 world per-contract vanna & charm are identical for a call and a put,
# so the call/put sign IS the dealer-position sign (not an option-type sign).
# Magnitudes are uncalibrated (shadow) — the paper A/B validates sign & relative size.


def _bs_d1d2(S: float, K: float, sigma: float, tau: float):
    """(d1, d2, σ√τ) for r=q=0, or None on degenerate inputs."""
    if S <= 0 or K <= 0 or sigma <= 0 or tau <= 0:
        return None
    srt = sigma * math.sqrt(tau)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * tau) / srt
    return d1, d1 - srt, srt


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _bs_vanna(S: float, K: float, sigma: float, tau: float) -> float:
    """∂delta/∂σ = −φ(d1)·d2/σ  (r=q=0). >0 for OTM calls, <0 for ITM; call==put."""
    dd = _bs_d1d2(S, K, sigma, tau)
    if dd is None:
        return 0.0
    d1, d2, _ = dd
    return -_norm_pdf(d1) * d2 / sigma


def _bs_charm(S: float, K: float, sigma: float, tau: float) -> float:
    """Charm = ∂delta/∂t = φ(d1)·d2/(2τ) = −∂delta/∂τ  (r=q=0, calendar-time delta DECAY
    per YEAR forward; sign tracks moneyness — ITM call delta rises, OTM decays toward 0).
    Identical for a call and a put here, so the call/put ± is the dealer-position sign."""
    dd = _bs_d1d2(S, K, sigma, tau)
    if dd is None:
        return 0.0
    d1, d2, _ = dd
    return _norm_pdf(d1) * d2 / (2.0 * tau)


_RTH_MINUTES = 390.0          # minutes in a regular-hours session (6.5h)
_CEX_FLOOR_MIN = 5.0          # floor 0DTE time-to-close so charm stays finite at the bell


def _tau_for(dte: Optional[int], minutes_to_close: Optional[float]) -> Optional[float]:
    """Time-to-expiry in YEARS. A 0DTE contract is priced at the LIVE minutes-to-close
    (charm ∝ 1/τ, so the into-the-bell acceleration is the whole 0DTE signal); if the
    clock isn't supplied it falls back to the half-session floor. Dated contracts use
    whole calendar days. Returns None on a missing dte."""
    if dte is None:
        return None
    if dte > 0:
        return float(dte) / _TRADING_DAYS
    if minutes_to_close is not None:                       # 0DTE, live clock
        return max(float(minutes_to_close), _CEX_FLOOR_MIN) / (_RTH_MINUTES * _TRADING_DAYS)
    return _TAU_FLOOR_DAYS / _TRADING_DAYS                  # 0DTE, no clock → floor


def _net_greek_exposure_at(contracts: list[dict], S: float, weight_key: str,
                           unit_fn: Callable[[float, float, float, float], float],
                           scale: float, minutes_to_close: Optional[float] = None) -> Optional[float]:
    """Σ dealer-signed (calls +, puts −) unit_greek·weight·100·S·scale at spot S."""
    if not S:
        return None
    tot = 0.0
    for c in contracts:
        K = c.get("strike"); w = c.get(weight_key) or 0; iv = c.get("iv"); dte = c.get("dte")
        if not K or not w or not iv or dte is None:
            continue
        tau = _tau_for(dte, minutes_to_close)
        if not tau:
            continue
        ex = unit_fn(S, K, iv, tau) * w * 100.0 * S * scale
        tot += (ex if c.get("right") == "call" else -ex)
    return tot


def _greek_peak_strike(contracts: list[dict], S: float, weight_key: str,
                       unit_fn: Callable[[float, float, float, float], float],
                       scale: float, minutes_to_close: Optional[float] = None) -> Optional[float]:
    """The strike carrying the largest |dealer-signed greek exposure| (a 'wall')."""
    if not S:
        return None
    by_strike: dict[float, float] = {}
    for c in contracts:
        K = c.get("strike"); w = c.get(weight_key) or 0; iv = c.get("iv"); dte = c.get("dte")
        if not K or not w or not iv or dte is None:
            continue
        tau = _tau_for(dte, minutes_to_close)
        if not tau:
            continue
        ex = unit_fn(S, K, iv, tau) * w * 100.0 * S * scale
        by_strike[K] = by_strike.get(K, 0.0) + (ex if c.get("right") == "call" else -ex)
    return max(by_strike, key=lambda k: abs(by_strike[k])) if by_strike else None


def _gamma_wall(contracts: list[dict], spot: float, side: str, weight_key: str,
                minutes_to_close: Optional[float] = None, *, reprice: bool = True) -> Optional[float]:
    """UW-style wall: the strike with the largest call/put GAMMA exposure on the
    correct side of spot (calls ≥ spot, puts ≤ spot). Distinct from the OI/volume
    wall (`_weight_wall`).

    reprice=True (0DTE, slide B): gamma is repriced on the LIVE clock (`_live_gamma`)
    — the same basis the signed pin field uses — so the wall sharpens into the close
    instead of freezing at the fetch-time clock.
    reprice=False (dated, slide C): keeps the STORED gamma so the multi-day terrain
    walls stay stable intraday (their tau is clock-independent anyway); the ≥0 clamp
    still rejects a broker −999 sentinel."""
    by_strike: dict[float, float] = {}
    for c in contracts:
        if c.get("right") != side:
            continue
        w = c.get(weight_key) or 0; k = c.get("strike")
        if not w or k is None:
            continue
        if side == "call" and k < spot:
            continue
        if side == "put" and k > spot:
            continue
        g = _live_gamma(c, spot, minutes_to_close) if reprice else _stored_gamma(c)
        if not g:
            continue
        by_strike[k] = by_strike.get(k, 0.0) + abs(g * w)
    return max(by_strike, key=lambda k: by_strike[k]) if by_strike else None


def _exposure_sign(x: Optional[float]) -> str:
    if x is None:
        return "unknown"
    if x > 0:
        return "positive"
    if x < 0:
        return "negative"
    return "neutral"


_VEX_SCALE = 0.01                 # per 1 vol-point (1% IV) move
_CEX_SCALE = 1.0 / _TRADING_DAYS  # per trading day of decay


def slide_flows(contracts: list[dict], spot: float, *,
                minutes_to_close: Optional[float] = None) -> dict:
    """DRIFT GAUGES (Slide F): the slow pressure from volatility moves and time
    decay into the close. Net vanna/charm dealer exposure (VEX/CEX), each read
    off the tenor that actually carries it — not the whole chain (which dilutes both):

      • CEX (charm) ← the 0DTE day-chain, VOLUME-weighted (same-day OI ≈ 0 at the open),
        priced at the LIVE minutes-to-close. Charm ∝ 1/τ, so it explodes into the bell —
        this is the contract set that drives the afternoon pin/drift, exactly what a 0DTE
        fade cares about.
      • VEX (vanna) ← the 1-7DTE book, OI-weighted. A 0DTE option has ~zero vega, hence
        ~zero vanna, so the vol-driven hedging lives entirely in the dated strikes; pulling
        VEX from 0DTE would read ~0 and be meaningless.

    Reads the same per-contract IV the flip uses; magnitudes are uncalibrated (shadow)."""
    out = {"vex": None, "cex": None, "vex_sign": "unknown", "cex_sign": "unknown",
           "vanna_wall": None, "charm_wall": None, "vex_basis": None, "cex_basis": None,
           "vex_tenor": "1-7DTE", "cex_tenor": "0DTE", "minutes_to_close": minutes_to_close}
    if not spot:
        return out
    zero, near = split_tenor(contracts)

    # CEX — same-day decay, volume-weighted, on the live clock
    cb = pick_basis(zero, spot, prefer="volume")
    if cb:
        cex = _net_greek_exposure_at(zero, spot, cb, _bs_charm, _CEX_SCALE, minutes_to_close)
        out.update({"cex": round(cex, 2) if cex is not None else None,
                    "cex_sign": _exposure_sign(cex), "cex_basis": cb,
                    "charm_wall": _greek_peak_strike(zero, spot, cb, _bs_charm,
                                                     _CEX_SCALE, minutes_to_close)})

    # VEX — vol-driven, on the vega-bearing dated book
    vb = pick_basis(near, spot)
    if vb:
        vex = _net_greek_exposure_at(near, spot, vb, _bs_vanna, _VEX_SCALE)
        out.update({"vex": round(vex, 2) if vex is not None else None,
                    "vex_sign": _exposure_sign(vex), "vex_basis": vb,
                    "vanna_wall": _greek_peak_strike(near, spot, vb, _bs_vanna, _VEX_SCALE)})
    return out


def build_views(contracts: list[dict], spot: float,
                aggressor_flow: Optional[float] = None, *,
                minutes_to_close: Optional[float] = None,
                sigma: Optional[float] = None,
                flow_conflict: float = FLOW_CONFLICT,
                flow_kind: str = "options") -> dict:
    """Pure assembler: the six slides + the consumer-facing magnet/regime. `magnet`
    is the 0DTE pin (B) when present, else the full-chain flip (A) — that is what the
    reversion lens measures runway to. `minutes_to_close` sharpens the 0DTE charm
    clock; `sigma` (the day's σ yardstick) arms the ±½σ shove test; `flow_conflict`
    + `flow_kind` are the source-scaled trip level and tape semantics for slide E."""
    zero, near = split_tenor(contracts)
    A = slide_flip(contracts, spot, minutes_to_close)
    B = slide_0dte(zero, spot, minutes_to_close)
    C = slide_tenor_walls(near, spot)
    E = reconcile_sign(A["regime"], aggressor_flow, flow_conflict, flow_kind)
    F = slide_flows(contracts, spot, minutes_to_close=minutes_to_close)   # vanna/charm flows
    magnet = B["pin"] if B["pin"] is not None else A["flip"]
    # SHOVE TEST (P5): re-price net dealer GEX at spot ± ½σ and record the SIGNED
    # margins — how much calming GRAVITY survives a half-sigma FLOW shove each way
    # (distance from zero = distance from flipping; a bool would throw that away).
    # Same basis + clock as the regime read. Fail-open: any surprise → None.
    shove = None
    try:
        if sigma and spot and A["basis"]:
            net0 = A["net_gex"]                 # signed dealer GEX at spot (slide A)
            up = _net_gex_at(contracts, spot + SHOVE_SIGMA * sigma, A["basis"], minutes_to_close)
            dn = _net_gex_at(contracts, spot - SHOVE_SIGMA * sigma, A["basis"], minutes_to_close)
            denom = abs(net0) if net0 else None
            shove = {"shove_sigma": SHOVE_SIGMA,
                     "net_gex_spot": round(net0, 2) if net0 is not None else None,
                     "shove_up_net_gex": round(up, 2),
                     "shove_down_net_gex": round(dn, 2),
                     # dimensionless: shoved GEX / |GEX at spot| — comparable across days
                     "shove_up_margin": round(up / denom, 4) if denom else None,
                     "shove_down_margin": round(dn / denom, 4) if denom else None,
                     # convenience bools (extras, not the record): sign differs from spot's
                     "up_flips": bool(net0) and (up < 0) != (net0 < 0),
                     "down_flips": bool(net0) and (dn < 0) != (net0 < 0)}
    except Exception:
        shove = None
    return {
        "spot": round(spot, 4) if spot else None,
        "magnet": magnet,                       # the pin the fade reverts to (for runway)
        "regime": E["sign"],                    # sign-checked regime
        "regime_raw": A["regime"],
        "flip": A["flip"], "flip_band": A["flip_band"],
        "sign_agrees": E["agrees"], "sign_confidence": E["confidence"],
        "vex": F["vex"], "cex": F["cex"],        # net vanna / charm exposure (shadow)
        "vex_sign": F["vex_sign"], "cex_sign": F["cex_sign"],
        "shove": shove,                          # ±½σ GRAVITY stress-test (P5); None w/o σ
        "slides": {"A_flip": A, "B_0dte": B, "C_tenor": C,
                   "D_volume": {"0dte_basis": B["basis"]}, "E_sign": E, "F_flows": F},
    }


def gex_motion(prior_rows: list[dict], current: dict, *,
               sigma: Optional[float], now: datetime) -> Optional[dict]:
    """MOTION PACK (P4) — the GRAVITY map read as FILM, not photo. Pure diff of this
    scan's gex_views record against the latest prior SPX diary row 4-30 min older
    whose chain source matches (native vs spy_proxy — a degrade-path swap must never
    masquerade as motion). No I/O: the diary rows the caller hands in are the
    system's only inter-tick memory.

      wall melt    Δ wall-strike SHARE of total |γ·w| (raw 0DTE gamma is volume-
                   weighted and only grows — never diff raw mass), and ONLY while
                   the wall STRIKE is unchanged: relocation = walk, not melt;
      magnet walk  Δ of the gamma-weighted CENTROID of the signed pin field / σ
                   (NEVER the argmax pin — it teleports 30-65 pts between stillness);
      flip creep   Δ of the root-found flip / σ — full-chain and OI-backed, the one
                   motion read trusted through the morning guard;
      grip slope   Δ pin_top_share — is the magnet's CONCENTRATION building or fading;
      soft side    which side of spot carries LESS |net| mass — where a FLOW shove
                   meets the least GRAVITY (Break-Lens runway / fade stand-down).

    Serves BOTH downstream consumers (Watchtower payload + Break-Lens fire gates)
    from the same diary fields. Returns None when no comparable prior row exists
    (first tick of the day) or on any surprise — the key rides the row as None,
    never crashes a scan (fail-open)."""
    try:
        cur_src = ((current or {}).get("source") or "").split("×")[0]
        if not cur_src:
            return None
        best = None                    # (ts, gex_views, age_min) of the LATEST qualifying row
        for r in prior_rows or []:
            try:
                if r.get("ticker") != "SPX":
                    continue           # never diff across price spaces (XSP/QQQ rows)
                gvr = r.get("gex_views") or {}
                src = (gvr.get("source") or "").split("×")[0]
                if not src or src != cur_src:
                    continue
                ts = datetime.fromisoformat(r["ts"])
                age = (now - ts).total_seconds() / 60.0
                if not (MOTION_MIN_LOOKBACK_MIN <= age <= MOTION_MAX_LOOKBACK_MIN):
                    continue
                if best is None or ts > best[0]:
                    best = (ts, gvr, age)
            except Exception:
                continue               # one malformed row never kills the diff
        if best is None:
            return None
        prior_ts, prior, age = best

        # MORNING GUARD — thin pre-10:30 volume makes every share/ratio read lie;
        # only the OI-backed full-chain flip is trusted early (see the constants).
        # BOTH ends of the diff are guarded: a 10:31 scan compared against a 10:05
        # thin-book row would otherwise read pure volume fill-in as melt/walk for
        # the first ~30 min after the guard lifts.
        pin_total = (current or {}).get("pin_total_gamma")
        try:
            open_et = now.astimezone(ET).replace(hour=9, minute=30, second=0, microsecond=0)
            elapsed_min = (now.astimezone(ET) - open_et).total_seconds() / 60.0
            prior_elapsed_min = (prior_ts.astimezone(ET) - open_et).total_seconds() / 60.0
        except Exception:
            elapsed_min = None         # unknowable clock → guard stays up (conservative)
            prior_elapsed_min = None
        morning_guard = (elapsed_min is None
                         or elapsed_min < MOTION_GUARD_MIN_ELAPSED_MIN
                         or prior_elapsed_min is None
                         or prior_elapsed_min < MOTION_GUARD_MIN_ELAPSED_MIN
                         or pin_total is None
                         or pin_total < MOTION_PIN_GAMMA_FLOOR)

        out = {"lookback_min": round(age, 2), "morning_guard": morning_guard,
               "call_wall_melt_share": None, "put_wall_melt_share": None,
               "call_wall_relocated": None, "put_wall_relocated": None,
               "magnet_walk_sigma": None, "flip_creep_sigma": None,
               "grip_slope": None, "soft_side": None, "gamma_above_share": None}

        def _delta(key: str) -> Optional[float]:
            a, b = prior.get(key), current.get(key)
            return (b - a) if (a is not None and b is not None) else None

        # flip creep — computed BEFORE the guard gate: the root-found flip is the
        # one full-chain, OI-backed read the thin morning book cannot fake.
        # Plausibility-capped: the nearest-spot crossing SWAPS between island
        # borders on multi-crossing 0DTE curves (a 30-90 pt teleport with no
        # book change) — an over-cap jump is a border swap, not creep → None.
        d_flip = _delta("flip")
        if sigma and d_flip is not None:
            creep = d_flip / sigma
            if abs(creep) <= FLIP_CREEP_MAX_SIGMA:
                out["flip_creep_sigma"] = round(creep, 4)
        if morning_guard:
            return out

        # wall melt vs walk — a melt is only a melt while the strike holds still.
        # "Unchanged" is tolerance-tested, not float-equal: proxy rescale drifts
        # the SAME physical strike <1.5 pts every pull (see the constant).
        for side in ("call", "put"):
            w0, w1 = prior.get(f"{side}_wall_gamma"), current.get(f"{side}_wall_gamma")
            if w0 is None or w1 is None:
                continue               # relocated stays None: unknown, so no melt read
            relocated = abs(w0 - w1) >= WALL_RELOCATION_MIN_PTS
            out[f"{side}_wall_relocated"] = relocated
            if not relocated:
                d_share = _delta(f"{side}_wall_gamma_share")
                if d_share is not None:
                    out[f"{side}_wall_melt_share"] = round(d_share, 4)  # negative = melting

        d_cent = _delta("pin_centroid")
        if sigma and d_cent is not None:
            out["magnet_walk_sigma"] = round(d_cent / sigma, 4)
        d_grip = _delta("pin_top_share")
        if d_grip is not None:
            out["grip_slope"] = round(d_grip, 4)

        # soft side — current-row shape (not a diff): the side with LESS |net| mass
        above = (current or {}).get("gamma_above_spot")
        below = (current or {}).get("gamma_below_spot")
        if above is not None and below is not None and (above + below) > 0:
            out["gamma_above_share"] = round(above / (above + below), 4)
            if above < below:
                out["soft_side"] = "up"
            elif below < above:
                out["soft_side"] = "down"
        return out
    except Exception:
        return None                    # fail-open: motion is a shadow read, never a crash


# ===========================================================================
# Snapshot + cache (I/O) — pull rarely, re-price every tick
# ===========================================================================


@dataclass
class _Cache:
    contracts: list[dict]
    spot: float                 # index-space spot at snapshot time
    sigma: float
    anchor_spot: float          # spot the snapshot was pulled at (for the move trigger)
    ts: datetime
    source: str


_CACHE: dict[str, _Cache] = {}


def _default_chain_lookup(ticker: str) -> Optional[dict]:
    """Pull the wide chain for `ticker` via the left-eye fetcher (best-effort)."""
    try:
        import lefteye_fetcher
        return lefteye_fetcher.chain_snapshot(ticker, strike_count=SNAP_STRIKE_COUNT,
                                              dte_max=SNAP_DTE_MAX)
    except Exception:
        return None


def _default_native_lookup(ticker: str) -> Optional[dict]:
    """Native index chain via ThetaData (chunked, full-resolution, parity-rescued IV).
    Best-effort: None on any failure hands the engine to the SPY-proxy degrade path."""
    try:
        import native_gex_feed
        return native_gex_feed.native_chain(ticker)
    except Exception:
        return None


# GexBox sentinel: "caller didn't say" — prod wiring gets the real native lookup,
# tests that inject a chain_lookup keep the proxy-only behavior they were written for.
_NATIVE_UNSET = object()


def _live_index_spot(ticker: str) -> Optional[float]:
    """Live index spot via the Data Runner — absorbed from the retired legacy
    engine; used for the SPY→index rescale on every fresh chain pull."""
    try:
        import lefteye_fetcher
        return lefteye_fetcher.live_spot(ticker)
    except Exception:
        return None


def _rescaled_chain(ticker: str, chain_lookup: Callable[[str], Optional[dict]],
                    spot_lookup: Callable[[str], Optional[float]],
                    now: datetime,
                    native_lookup: Optional[Callable[[str], Optional[dict]]] = None) -> Optional[_Cache]:
    """CHAIN SNAPSHOT — native index book first, SPY stand-in second.
    For an index ticker the native ThetaData chain (already in index price space)
    is PRIMARY; when that pull fails, borrow SPY's option book and stretch it ×10
    into the index's price space (scale = index_spot / spy_spot; GEX weights are
    kept, only LEVELS are scaled — mirrors lefteye_gamma). `meta.gex_source` labels
    which path served the read, so a degrade is never silent."""
    proxy = SPY_PROXY.get(ticker)
    chain = native_lookup(ticker) if (native_lookup is not None and proxy is not None) else None
    native_served = chain is not None
    if native_served:
        proxy = None                       # native chain is already index-space
        src_ticker = ticker
    else:
        src_ticker = proxy or ticker
        chain = chain_lookup(src_ticker)
    if not chain:
        return None
    # copy so we never mutate the fetcher's dicts (annotation + rescale below)
    contracts = [dict(c) for c in (chain.get("contracts") or [])]
    src_spot = chain.get("spot")
    if not src_spot or not contracts:
        return None
    # FILL LEDGER — annotate recency fill weights on SOURCE strikes (stable keys;
    # the proxy rescale shifts strike levels every scan and would break the diffs).
    # Native chains arrive PRE-annotated by native_chain() — annotating again here
    # would stamp the same book twice per scan under a second ledger id.
    # Best-effort: on any failure the pin falls back to cumulative volume.
    if not native_served:
        try:
            import lefteye_fill_ledger as _fl
            _fl.annotate(f"{'proxy' if proxy else 'box'}_{src_ticker}", contracts, now)
        except Exception:
            pass
    if proxy is None:
        idx_spot, scale = src_spot, 1.0
    else:
        idx_spot = spot_lookup(ticker)
        scale = (idx_spot / src_spot) if (idx_spot and src_spot) else STRUCTURAL_SCALE.get(ticker, 1.0)
        idx_spot = idx_spot or (src_spot * scale)
        for c in contracts:                    # rescale strikes into index space
            c["strike"] = c["strike"] * scale if c.get("strike") is not None else None
    atm_iv = _atm_iv(contracts, idx_spot)
    # σ must stay positive: should_refresh's move trigger and the shove test both gate on
    # sigma > 0, so a bad (negative/zero) atm_iv falls back to the 1%-of-spot default.
    sigma = (idx_spot * atm_iv / (252.0 ** 0.5)) if (atm_iv and atm_iv > 0) else idx_spot * 0.01
    return _Cache(contracts=contracts, spot=round(idx_spot, 4), sigma=round(sigma, 4),
                  anchor_spot=round(idx_spot, 4), ts=now,
                  source=("spy_proxy×%.4f" % scale) if proxy else "native")


def should_refresh(cache: Optional[_Cache], now: datetime,
                   live_spot: Optional[float]) -> bool:
    """Re-pull only when the structure can have changed: no cache, stale, or a
    >REFRESH_MOVE_SIGMA spot move since the snapshot (the strike window re-centers)."""
    if cache is None:
        return True
    if (now - cache.ts).total_seconds() / 60.0 >= REFRESH_AGE_MIN:
        return True
    if live_spot is not None and cache.sigma > 0:
        if abs(live_spot - cache.anchor_spot) >= REFRESH_MOVE_SIGMA * cache.sigma:
            return True
    return False


def GexBox(ticker: str, *, now: Optional[datetime] = None,
              live_spot: Optional[float] = None,
              aggressor_flow: Optional[float] = None,
              flow_conflict: float = FLOW_CONFLICT,
              flow_kind: str = "options",
              chain_lookup: Optional[Callable[[str], Optional[dict]]] = None,
              spot_lookup: Optional[Callable[[str], Optional[float]]] = None,
              native_lookup=_NATIVE_UNSET,
              cache: Optional[dict] = None) -> Optional[dict]:
    """GRAVITY ENGINE orchestrator: fetch rarely, re-price every scan, produce
    the six slides. Refreshes the cached chain only on a trigger, then re-prices the
    six slides at `live_spot`. Returns the views dict (+ meta) or None if no chain.
    All lookups are injectable for tests; `cache` lets a test pass an isolated store.
    Chain source: native index chain primary, SPY proxy fallback — but ONLY on the
    default wiring; a test that injects `chain_lookup` without `native_lookup` gets
    the proxy path it was written against (no surprise network calls)."""
    now = now or datetime.now(tz=ET)
    injected = chain_lookup is not None
    chain_lookup = chain_lookup or _default_chain_lookup
    spot_lookup = spot_lookup or _live_index_spot
    if native_lookup is _NATIVE_UNSET:
        native_lookup = None if injected else _default_native_lookup
    store = _CACHE if cache is None else cache

    entry = store.get(ticker)
    if should_refresh(entry, now, live_spot):
        fresh = _rescaled_chain(ticker, chain_lookup, spot_lookup, now,
                                native_lookup=native_lookup)
        if fresh is not None:
            store[ticker] = entry = fresh
        # if the pull failed, fall through and re-price the stale cache (degrade)
    if entry is None:
        return None

    spot = live_spot if live_spot is not None else entry.spot
    mtc = _minutes_to_close(now)                  # 0DTE charm clock (None outside RTH)
    views = build_views(entry.contracts, spot, aggressor_flow=aggressor_flow,
                        minutes_to_close=mtc, sigma=entry.sigma,   # σ arms the shove test
                        flow_conflict=flow_conflict,               # source-scaled tape veto
                        flow_kind=flow_kind)                       # + its sign semantics
    age = round((now - entry.ts).total_seconds() / 60.0, 2)
    views["meta"] = {"gex_source": entry.source, "snapshot_spot": entry.anchor_spot,
                     "age_min": age, "repriced": live_spot is not None and live_spot != entry.spot,
                     "sigma": entry.sigma, "minutes_to_close": mtc}
    return views


def _minutes_to_close(now: datetime) -> Optional[float]:
    """Minutes from `now` (ET) to the 16:00 ET cash close; None outside a sane RTH
    window so 0DTE charm falls back to the half-session floor pre-open / after-hours."""
    try:
        close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        m = (close - now).total_seconds() / 60.0
    except Exception:
        return None
    return m if 0.0 < m <= _RTH_MINUTES else None
