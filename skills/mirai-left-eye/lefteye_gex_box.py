"""lefteye_gex_box.py — the GRAVITY ENGINE: the 6-slide GEX view set (BETA, shadow).

Vocabulary: GRAVITY = dealer positioning, where price gets PULLED (magnet/walls/
flip/regime — this module). FLOW = the live force, who is SHOVING price right now
(the aggressor-flow sensor in native_gex_feed; cross-checked in slide E). The
pin-vs-trend failure mode reads as: gravity gets overpowered by flow. Display
notation for labeled levels (docs/gw-vocab.md): walls render as GWc/GWp, the
magnet as MagP, pinning as Pin — storage keys here are never renamed.

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

import functools
import math
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
# ⚠ FLOW_CONFLICT (0.6) IS ITSELF DEAD AND IS KNOWINGLY LEFT ALONE. Measured over 534
# live samples the options aggressor tape spans [−0.104, +0.441] (median 0.018) — it has
# never had the RANGE to reach 0.6, and `sign_agrees` is False in 0 of 671 rows while every
# non-null read stamps agrees=True / confidence=1.0. Retuning it is a LIVE regime change,
# not a cleanup: reversion_lens.py:140 turns `sign_agrees is None` into a gamma-voter
# abstain, so an "honest" guard would silently delete the gamma vote on ~every pin day,
# un-A/B'd, from the first scan after deploy. So: we MEASURE the guard every scan
# (reconcile_sign's headroom/informative/trip keys + lefteye_guard_audit) and let the
# nightly audit declare it DEAD. Retune behind its own flag, with its own A/B.

# --- GAMMA-OWNERSHIP TAPE (2026-07-13) — SHADOW, RECORD-ONLY ------------------
# The axis `aggressor_flow` is structurally BLIND to. aggressor_flow is a DIFFERENCE —
# ((callBuy−callSell) + (putSell−putBuy)) / gross, native_gex_feed.py:249-266 — so it
# measures DIRECTION (bullish lean). The event that makes dealers SHORT gamma is customers
# NET BUYING options, which on 0DTE classically means buying BOTH wings: call-buying pushes
# term 1 up, put-buying pushes term 2 down, and they ANNIHILATE. A book where customers buy
# 1000 calls and 1000 puts — the dealer maximally short gamma — reads exactly 0.00, dead
# centre of "no signal". The alarm cannot fire because the signal subtracts itself out.
# gamma_tape is a SUM over signed customer CONTRACTS, so the both-wings case cannot cancel.
#
# NOT CALIBRATED. NOT CONSUMED. On the 3 measurable live days the sign was ANTI-correlated
# with realized trendiness (07-09, a trend day the engine called long_gamma on 57/57 scans:
# tape −0.0010, i.e. it would have CONFIRMED the wrong pin call; 07-08, the choppiest/most
# pin-like day: +0.0096, the "most accelerant" reading). The measured range never leaves
# ±0.01, because the NBBO quote rule tags the LIQUIDITY TAKER, not the counterparty, and the
# 0DTE taker population is two-sided (lottery buyers AND premium sellers both cross the
# spread). So this ships as a RECORDED axis with a pre-registered kill condition: if the
# backfill confirms the anti-correlation, gamma_tape is DELETED, not re-signed.
TAPE_BAND_SIGMA = 0.5              # gamma is LOCAL: the at-spot scalar sums ±0.5σ, not the
                                   # ±8% fetch window. Mirrors SHOVE_SIGMA.
TAPE_MIN_INTERVAL_CONTRACTS = 250.0  # marginal-window abstain floor: a thin bucket must
                                     # print None, not a ±1.0 built from four contracts.
TAPE_TRIP_HI = None                # UNCALIBRATED — set from the recorded q85 after backfill.
TAPE_TRIP_LO = None                # UNCALIBRATED — set from the recorded q15 after backfill.
TAPE_MIN_COVERAGE = None           # UNCALIBRATED — 0.689 coverage was a NORMAL day; no guess.
GAMMA_TAPE_CONSUME = False         # DEAD-MAN SWITCH. While False nothing may read the tape's
                                   # word or confidence. The trip levels being None means an
                                   # accidental consumer ABSTAINS rather than firing on a
                                   # guessed number.
GAMMA_TAPE_V = 1                   # weighting era: contracts × live γ (cf. pin_signed_v)
# --- N5 (2026-07-12): the DIRECTION marginal that rides the same snapshots ------------
# aggressor_flow (the tower's only tape-aggression number) is a WHOLE-DAY cumulative at ONE
# strike: it never decays and never turns — at 15:30 on a day whose last 45 minutes were pure
# selling it still reads "+0.03, buyers". flow_recent is the same bullish-lean question
# (buy calls / sell puts = +) asked over the 4-30 min marginal window, across ALL annotated
# 0DTE strikes, in DOLLARS (the unit aggressor_flow already speaks; premium decay is
# negligible inside a ≤30-min window). It shares gamma_tape's snapshot differencing and its
# clock; it is a RECORDED field — consumers switch to it deliberately, not silently.
FLOW_RECENT_MIN_GROSS = 250_000.0  # $ tagged in the window below which the marginal abstains
                                   # (a thin bucket must print None, not a ±1.0 of four ticks)
# NEVER inherit FLOW_CONFLICT (0.6) as this axis's trip level. It lives on the DIRECTION
# axis and is meaningless here (measured cumulative range ±0.01). FLOW_CONFLICT_LOB was cut
# to 0.25 for exactly this reason — a threshold borrowed across axes is how guards die.

# --- H7: THE REGIME IS TODAY'S BOOK (2026-07-12, LIVE) ------------------------
# The regime answers ONE question — "over the next hour, does dealer hedging DAMP price
# or AMPLIFY it?" — and the dealers who must re-hedge in the next hour are the ones short
# TODAY'S expiry. The engine was answering it off the blended 0-7DTE chain, where the
# MAJORITY of the weight is options that do not expire today. A fat 1-7DTE long-gamma mass
# can hold the blended sign POSITIVE while today's book at spot is short gamma and
# amplifying: the map says "dealers long gamma, moves get damped (pin-friendly)" on a day
# today's book is fuel. Worse, the Break Lens — the only offense head — can only COCK on a
# NEGATIVE sign (reversion_lens.py:668), so a stuck-positive blended sign means the breakout
# detector never wakes up. That is the root several other findings hang off.
#
# Precedent, already in this file: slide_flows reads CEX off the 0DTE book and VEX off the
# 1-7DTE book — "each read off the tenor that actually carries it, not the whole chain
# (which dilutes both)". H7 applies that same doctrine to the regime.
#
# The dated tenors are NOT discarded — they stay first-class, as TERRAIN rather than as
# votes: slide_tenor_walls still supplies the structural band walls, the dated sidecar still
# supplies the far shelves, and the blended read is recorded beside the 0DTE one every scan
# (regime_tenor / flip_tenor / net_gex_tenor) so the disagreement rate is MEASURED from day
# one instead of argued. Flip this to False to revert to the blended regime in one line.
REGIME_0DTE = True

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

# --- MAGNET v3 (N8 + N9 + H4, 2026-07-12) --------------------------------------------
# The 07-12 pressure test proved the live magnet was broken three ways at the source:
#   N8  PERMANENT BULL — BS gamma is right-symmetric, so the "signed pin field" collapses
#       to sign(callOI − putOI); the magnet was defined as the strongest POSITIVE strike,
#       putting it ABOVE spot on 512/571 live scans (89.7%) BY CONSTRUCTION. The UW
#       external per-strike sign was tested and REFUSED the same day (coin-flip, commit
#       9ec3a68) — there is no trustworthy per-strike dealer sign to buy. So v3 stops
#       pretending: the magnet field is SIGN-FREE structural mass. Calls AND puts both
#       count as attraction (a put-heavy shelf pins price exactly like a call-heavy one).
#   N9  YESTERDAY'S BOOK — the field was OI-weighted, i.e. prior-day positioning that
#       never updates intraday. v3 weights by OI + today's cumulative VOLUME: the standing
#       book still anchors, and the strikes actually being loaded TODAY pull the magnet
#       as the session builds (07-10 live: the OI magnet said 7625 all day while price
#       lived 7536-7576 around a 7550 volume wall).
#   ATM GUARD (why NO live-γ reprice): an unsigned live-γ field peaks AT the money by
#       construction — the exact spot-hugging artifact that got the γ-walls pulled off
#       the map on 07-11. Pure contract mass (OI+vol) carries the structure without it.
#   H4  46-PT TELEPORTS — measured live: pin_zones NEVER produced a second zone in 302
#       zoned scans, so the contested-blend commissioned to kill the teleport could not
#       run even once. The teleport killer is HYSTERESIS: the wheel only changes hands
#       when the challenger's local mass out-pulls the incumbent's by MAG_HYST; a small
#       glide (≤ MAG_GLIDE_PTS) always follows freely. Held scans are visible
#       (pin_held / pin_candidate) — stickiness is never silent.
# The old signed-OI magnet is RECORDED beside as pin_legacy (+ its A/B lineage fields);
# pin_field_v tags the era so no A/B ever mixes definitions. Kill switch: MAGNET_V3_DISABLE=1.
MAGNET_V3 = os.environ.get("MAGNET_V3_DISABLE") != "1"
MAG_HYST = 1.25          # challenger needs 25% more local mass to take the wheel
MAG_GLIDE_PTS = 10.0     # moves this small are a glide, never dampened
MAG_LOCAL_PTS = 10.0     # local-mass window (±pts) for the hysteresis comparison
# THE REACH WINDOW (v3 verify round, 2026-07-12): an UNWINDOWED mass argmax re-created
# the card's harm with the sign flipped — on the real 07-13 book the ±8% fetch window
# contains 7000P/7140P crash shelves (put/call OI 2.91), so at the open (volume≈0) the
# raw argmax sat at −10σ and the tower's prior read hard DOWN by construction; intraday
# a 20k-contract deep-OTM lotto strike (+2.3σ) could seize it. A magnet is a strike
# price can actually get PULLED to within the session: the field is windowed to
# ±MAG_WINDOW_SIGMA·σ of spot (fallback ±MAG_WINDOW_PCT when σ is missing). Crash
# hedges and lotto tails are TERRAIN (the walls/dated book carry them), not gravity.
MAG_WINDOW_SIGMA = 1.5
MAG_WINDOW_PCT = 0.015
PIN_MASS_FLOOR = 500.0   # contracts (OI+vol) in the window below which there is no magnet —
                         # a 65-contract dust field must not serve a live pin (verify round 2);
                         # pin honestly falls back to the flip upstream
PIN_FIELD_V = 3


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
_CAL_TO_SESSIONS = 252.0 / 365.0   # fallback ratio when the NYSE calendar is unreachable


def _sessions_ahead(dte: int, today: Optional[date] = None) -> float:
    """NYSE sessions remaining in (today, today+dte] — the TRADING-time numerator that
    pairs with the _TRADING_DAYS (252) denominator.

    τ is trading time everywhere in this engine (the 0DTE clock is minutes/(390·252)),
    so the dated numerator has to be sessions too. Counting CALENDAR days into a 252-day
    year — what this did before 2026-07-12 — inflates τ by up to 365/252 = 1.45x. Gamma
    scales as 1/√τ, so every dated gamma, wall and flip came out ~20% light, and the
    error was worst across weekends and holiday weeks (a Friday 3DTE was priced as three
    sessions when only one remained).

    Fail-open: if the holiday calendar can't be reached, fall back to the smooth
    calendar→session ratio (252/365) rather than the raw calendar count — still far
    closer than the bug, and never zero."""
    n = max(int(dte), 0)
    if n <= 0:
        return 0.0
    d0 = today or datetime.now(ET).date()
    try:
        hol = _nyse_holidays(d0.year) | _nyse_holidays(d0.year + 1)
    except Exception:
        return max(1.0, n * _CAL_TO_SESSIONS)
    sessions = 0
    for i in range(1, n + 1):
        d = d0 + timedelta(days=i)
        if d.weekday() < 5 and d not in hol:
            sessions += 1
    return float(max(sessions, 1))       # an expiry inside the window is >= 1 session


@functools.lru_cache(maxsize=8)
def _nyse_holidays(year: int) -> frozenset:
    """NYSE full-closure set, borrowed from the watch package's computed calendar (the
    same source dated_gex_feed._holidays uses). Raises on failure so _sessions_ahead
    can take its documented fallback rather than silently counting holidays as sessions.

    lru_cache is load-bearing, NOT an optimization nicety: this is a pure year→frozenset
    lookup but it sits at the leaf of the flip root-finder, which reprices every dated
    contract at every price-grid point — ~1.09M calls per build_views(). Uncached (each
    call did Path(__file__).resolve() + an import), it pushed build_views to 21-29s and
    blew native_gex_feed.read()'s 15s budget, silently dropping the ENTIRE flow block
    (aggressor_flow/gamma_tape/flow_recent/tape_prev) on every scan from 2026-07-13 on.
    Cached: build_views ~29s→~0.9s, identical output. A one-line calendar change per year
    is a cache miss (maxsize=8 covers this year + next + slack); everything else is a hit."""
    rt = str(Path(__file__).resolve().parent.parent.parent / "runtime")
    if rt not in sys.path:
        sys.path.insert(0, rt)
    from watch.intraday import market_status as _ms
    return _ms._market_holidays(year)
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
        tau = _tau_for(dte, minutes_to_close)          # ONE clock (sessions/252) for every tenor
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
        tau = _tau_for(dte, minutes_to_close)          # ONE clock (sessions/252) for every tenor
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
               minutes_to_close: Optional[float] = None,
               prefer: str = "open_interest") -> dict:
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
    basis = pick_basis(contracts, spot, prefer=prefer)
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


def _mass_by_strike(contracts: list[dict], weight_key: str) -> dict[float, float]:
    """MAGNET FIELD v3 (N8+N9) — per-strike structural mass: Σ weight across BOTH
    rights. NO call−put netting (the netted sign is the assumed-sign artifact that made
    the magnet a permanent bull) and NO live-γ reprice (an unsigned live-γ field peaks
    at the money by construction — the spot-hug artifact). Weight = oi_vol_w (standing
    book + today's tape) annotated by slide_0dte."""
    by_strike: dict[float, float] = {}
    for c in contracts:
        k, w = c.get("strike"), c.get(weight_key) or 0
        if k is None or not w:
            continue
        by_strike[float(k)] = by_strike.get(float(k), 0.0) + float(w)
    return by_strike


def _mass_near(field: dict[float, float], x: float, r: float = MAG_LOCAL_PTS) -> float:
    """Local pull around a level — the hysteresis comparison mass (H4)."""
    return sum(v for k, v in field.items() if abs(k - x) <= r)


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


def gamma_tape(zero_dte: list[dict], spot: float,
               minutes_to_close: Optional[float] = None,
               sigma: Optional[float] = None,
               anchor_spot: Optional[float] = None,
               prev: Optional[dict] = None,
               now: Optional[datetime] = None) -> Optional[dict]:
    """GAMMA-OWNERSHIP TAPE (SHADOW, record-only) — net gamma transferred OUT OF the
    liquidity-providing (hedged) book today, as a share of tagged gamma volume.

        > 0   the hedged book is SHEDDING gamma   (turning accelerant)
        < 0   the hedged book is ABSORBING gamma  (turning pin-ward)

    A FLOW, NOT A STOCK. It says which way the book is TURNING, never what dealers OWN,
    so it cannot answer `reconcile_sign`'s question and is never routed there.

    Weighted by CONTRACTS × `_live_gamma`, never dollars. BS gamma is right-independent
    (`_bs_gamma` takes no `right`), premium is not: a 600-pt-ITM 0DTE call carries maximum
    premium and ~zero gamma while its same-strike put carries ~zero premium and the IDENTICAL
    gamma — premium ranks them ~100,000:1 where hedging ranks them 1:1. Premium also decays
    through the session, drifting any dollar-netted numerator positive. A dollar-weighted
    "gamma" tape is not a gamma tape; `gamma_tape_dollar` is recorded ONLY as the A/B control
    that measures that error, and must never be read as the signal.

    NEVER computed from `signed_weight` — that is max(abs(net_flow), CHURN_FLOOR·gross), an
    ABSOLUTE value that destroys the very sign this axis carries. H10's churn floor is a PIN
    weight. A future refactor must not "unify" the two.

    CAVEATS, on the record: the NBBO quote rule tags the liquidity TAKER, not the customer
    (market makers take liquidity to hedge), and buy-to-OPEN and buy-to-CLOSE are
    indistinguishable intraday — 0DTE afternoons are closing-flow heavy, so a late rise may
    mean the OPPOSITE of a wind change until a next-morning ΔOI discriminator is fitted. Only
    the marketable-tagged ~70% is visible and the censoring is not random. `coverage` ships
    every scan. EVIDENCE, NEVER PROOF — this must never carry confidence 1.0.

    Fail-open: returns None when no 0DTE strike carries a contract-flow annotation (no ticks
    ⇒ unknown, never a balanced 0.0). `prev` is the previous scan's {"ts", "q"} cumulative
    snapshot; without it the marginal fields are None."""
    if not spot or not zero_dte:
        return None
    lo_b = hi_b = None
    if sigma:
        lo_b, hi_b = spot - TAPE_BAND_SIGMA * sigma, spot + TAPE_BAND_SIGMA * sigma
    nbs_lo, nbs_hi = spot * (1.0 - NBS_WINDOW), spot * (1.0 + NBS_WINDOW)

    num = den = 0.0                 # cumulative, γ·contracts
    num_s = den_s = 0.0             # at-spot band only (±TAPE_BAND_SIGMA·σ)
    num_f = den_f = 0.0             # γ frozen at anchor_spot — reprice-free control
    num_m = den_m = 0.0             # MARGINAL — the wind-change signal (γ-weighted ratio)
    num_d = den_d = 0.0             # dollar A/B control (the WRONG weighting)
    num_dir = den_dir = 0.0         # N5: DIRECTION marginal — bullish lean $ in the window
    # RAW contract counts, kept separately from the γ-weighted sums above. The thin-tape
    # floors are stated in CONTRACTS and must be compared against contracts: γ is ~0.01, so
    # a γ-weighted "count" of a real 1000-contract interval is ~10 and would fail any
    # contract-scaled floor. Weighting the RATIO and sizing the SAMPLE are different jobs.
    q_gross = q_interval = 0.0
    tagged = untagged = 0.0
    gross_oi = 0.0
    dgamma: dict[float, float] = {}
    q_now: dict = {}
    n = 0

    for c in zero_dte:
        k = c.get("strike")
        bq, sq = c.get("flow_buy_q"), c.get("flow_sell_q")
        if k is None or bq is None or sq is None:
            continue
        g = _live_gamma(c, spot, minutes_to_close)
        if g <= 0:
            continue                # γ ≥ 0 always ⇒ no abs() anywhere in the denominators
        k = float(k)
        bq, sq = float(bq), float(sq)
        net_q, gross_q = bq - sq, bq + sq
        n += 1
        b_dol, s_dol = float(c.get("flow_buy") or 0), float(c.get("flow_sell") or 0)
        # 4-tuple since N5: contracts feed the γ-ownership marginal, dollars feed the
        # DIRECTION marginal. Old 2-tuple snapshots stay readable (dollar leg skips).
        q_now[(round(k, 4), c.get("right"))] = (bq, sq, b_dol, s_dol)

        num += g * net_q
        den += g * gross_q
        q_gross += gross_q
        if lo_b is not None and lo_b <= k <= hi_b:
            num_s += g * net_q
            den_s += g * gross_q
        if anchor_spot:
            ga = _live_gamma(c, anchor_spot, minutes_to_close)
            if ga > 0:
                num_f += ga * net_q
                den_f += ga * gross_q

        # MARGINAL: the ratio of DELTAS, never the delta of the ratio. The cumulative
        # denominator grows all session, so by 13:00 the level is stiff and the very turn
        # we are hunting gets swamped (the deciding hour is ~8% of a 390-min cumulative).
        # Δ is clamped at 0 — a provider restatement must never manufacture negative volume.
        # A MISSING pq is skipped ON PURPOSE, not a bug. q_now snapshots EVERY in-window
        # strike (even zero-flow ones get a (0,0,0,0) entry, see :717), so a quiet strike
        # near spot already carries a zeroed baseline and its fresh flow IS captured. The
        # only strikes absent from prev are ones the ±8% window SHIFTED onto (far-edge
        # entrants) — deep-OTM, thin flow, and their b_dol is ALL-DAY cumulative, so
        # zero-baselining them would count stale earlier-day activity as window flow. The
        # miss (thin far-edge fresh flow) is smaller than the over-count it would trade for.
        if prev:
            pq = (prev.get("q") or {}).get((round(k, 4), c.get("right")))
            if pq:
                dbq, dsq = max(0.0, bq - pq[0]), max(0.0, sq - pq[1])
                num_m += g * (dbq - dsq)
                den_m += g * (dbq + dsq)
                q_interval += dbq + dsq
                # N5 direction marginal: bullish lean of the WINDOW's dollars — buy calls /
                # sell puts = +, the mirror = −. Same clamp-at-0 restatement guard.
                if len(pq) >= 4:
                    db_d, ds_d = max(0.0, b_dol - pq[2]), max(0.0, s_dol - pq[3])
                    sgn = 1.0 if c.get("right") == "call" else -1.0
                    num_dir += sgn * (db_d - ds_d)
                    den_dir += db_d + ds_d

        tagged += gross_q
        untagged += float(c.get("flow_mid_q") or 0) + float(c.get("flow_unk_q") or 0)
        gross_oi += g * float(c.get("open_interest") or 0)

        b_d, s_d = float(c.get("flow_buy") or 0), float(c.get("flow_sell") or 0)
        num_d += b_d - s_d
        den_d += b_d + s_d

        if nbs_lo <= k <= nbs_hi:
            # flow-side twin of net_by_strike, same units/axis/clip. MINUS: a customer BUY
            # means the hedged book LOSES that gamma.
            dgamma[k] = dgamma.get(k, 0.0) - net_q * g * 100.0 * spot * spot

    if n == 0 or den <= 0:
        return None                 # no tagged tape ⇒ unknown, not a balanced zero

    # The marginal needs a CLOCK. Without one (`now` absent) it abstains — a delta over an
    # unknown interval is not a rate. The 4-30 min window is enforced at BOTH ends: here
    # (consumption) and at the snapshot-advance rule in reversion_lens (the disk anchor only
    # advances once its window is ≥ the floor — production scans are one-shot processes, so
    # the anchor round-trips through state/tape_prev/ and a too-eager advance would keep the
    # window forever under the floor).
    interval_min = None
    if prev and prev.get("ts") and now:
        try:
            interval_min = round((now - prev["ts"]).total_seconds() / 60.0, 2)
        except Exception:
            interval_min = None
    marginal_ok = (q_interval >= TAPE_MIN_INTERVAL_CONTRACTS and den_m > 0
                   and interval_min is not None
                   and MOTION_MIN_LOOKBACK_MIN <= interval_min <= MOTION_MAX_LOOKBACK_MIN)
    # N5: the direction marginal has its own gross floor ($) but shares the clock window —
    # a valid window with thin tagged dollars abstains rather than printing noise.
    dir_ok = (den_dir >= FLOW_RECENT_MIN_GROSS and interval_min is not None
              and MOTION_MIN_LOOKBACK_MIN <= interval_min <= MOTION_MAX_LOOKBACK_MIN)

    def _r(x, d):
        return round(x / d, 5) if d > 0 else None

    return {
        # N5: WINDOWED bullish lean — what "the tape right now" actually is. Whole-day
        # aggressor_flow rides beside it for A/B continuity; it must not be worded as live.
        "flow_recent": _r(num_dir, den_dir) if dir_ok else None,
        "flow_recent_gross": round(den_dir, 0),
        "gamma_tape": _r(num, den),                  # cumulative level (day)
        "gamma_tape_spot": _r(num_s, den_s),         # ±0.5σ band — the question the regime asks
        "gamma_tape_fixed": _r(num_f, den_f),        # γ frozen at anchor_spot (reprice control)
        "gamma_tape_60m": _r(num_m, den_m) if marginal_ok else None,   # THE WIND-CHANGE SIGNAL
        "gamma_tape_dollar": _r(num_d, den_d),       # A/B CONTROL — the WRONG weighting
        "coverage": round(tagged / (tagged + untagged), 4) if (tagged + untagged) > 0 else None,
        "flow_share": round(den / gross_oi, 5) if gross_oi > 0 else None,
        "gross_contracts": round(q_gross, 1),        # RAW contracts (calibration input)
        "interval_contracts": round(q_interval, 1),  # RAW contracts in the marginal window
        "interval_min": interval_min,
        "n_strikes": n,
        "dgamma_by_strike": [[round(k, 2), round(v, 3)] for k, v in sorted(dgamma.items())] or None,
        "gamma_tape_v": GAMMA_TAPE_V,
        "q_now": q_now,                              # cumulative snapshot for the NEXT marginal
    }


def tape_word(t: Optional[dict]) -> Optional[str]:
    """The Watchtower's word for the ownership tape. DORMANT BY DESIGN — returns None while
    GAMMA_TAPE_CONSUME is False or the trip levels are uncalibrated. It exists now so that
    the vocabulary is reviewable today rather than invented under time pressure on the day
    the axis earns promotion, and so the flag is the ONLY thing standing between the two."""
    if not GAMMA_TAPE_CONSUME or not t or TAPE_TRIP_HI is None or TAPE_TRIP_LO is None:
        return None
    m, cov = t.get("gamma_tape_60m"), t.get("coverage")
    if m is None or cov is None or (TAPE_MIN_COVERAGE is not None and cov < TAPE_MIN_COVERAGE):
        return "tape: unreadable (thin or censored)"
    if m >= TAPE_TRIP_HI:
        return "tape: the hedged book is SHEDDING gamma — it is turning into an accelerant"
    if m <= TAPE_TRIP_LO:
        return "tape: the hedged book is ABSORBING gamma — it is settling toward the pin"
    return "tape: steady — no net gamma changing hands"


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
               minutes_to_close: Optional[float] = None,
               prev_pin: Optional[float] = None,
               sigma: Optional[float] = None,
               nbs_reach: Optional[float] = None) -> dict:
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
           "pin_blended": None, "zone1_share": None, "net_by_strike": None,
           # MAGNET v3 (N8+N9+H4): era tag, the legacy signed-OI magnet riding as the A/B
           # twin, hysteresis visibility, and the mass field the tablet can draw
           "pin_field_v": None, "pin_legacy": None, "pin_held": None,
           "pin_candidate": None, "mass_by_strike": None}
    if not zero_dte or not spot:
        return out
    basis = pick_basis(zero_dte, spot, prefer="volume")   # walls: 0DTE → volume first
    if not basis:
        return out
    # the magnet reads structural positioning: OI when it exists, else volume
    mag_basis = pick_basis(zero_dte, spot, prefer="open_interest")
    pin, pin_total, pin_share = _net_pin_profile(zero_dte, mag_basis, spot, minutes_to_close)
    # MAGNET v3 (N8+N9): the LIVE magnet field is sign-free structural mass, weighted
    # OI + today's volume. The signed-OI read above is demoted to pin_legacy (recorded
    # every scan — the A/B against the permanent-bull era must be measurable, not argued).
    mass: dict = {}
    if MAGNET_V3:
        out["pin_legacy"] = round(pin, 4) if pin is not None else None
        for c in zero_dte:
            c["oi_vol_w"] = float(c.get("open_interest") or 0) + float(c.get("volume") or 0)
        mass = _mass_by_strike(zero_dte, "oi_vol_w")
        # THE REACH WINDOW: only strikes price can plausibly be pulled to this session
        # may carry the magnet (±MAG_WINDOW_SIGMA·σ). Without it the argmax lands on
        # the deepest crash shelf at the open (put OI 3:1 on real books) or a deep-OTM
        # lotto pile intraday — the permanent-bull bug reborn with a new sign.
        reach = (MAG_WINDOW_SIGMA * sigma) if sigma else (MAG_WINDOW_PCT * spot)
        mass = {k: v for k, v in mass.items() if abs(k - spot) <= reach}
        if mass and sum(mass.values()) < PIN_MASS_FLOOR:
            mass = {}                # dust is not gravity: no magnet beats a fake one
        if mass:
            total = sum(mass.values())
            k_max = max(mass, key=lambda k: mass[k])
            pin, pin_total = k_max, round(total, 4)
            pin_share = (mass[k_max] / total) if total > 0 else None
        else:
            pin = pin_total = pin_share = None
        out["pin_field_v"] = PIN_FIELD_V
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
        if SIGNED_PIN and p_sig is not None and not MAGNET_V3:
            # legacy-era flag only: under v3 the live magnet is the mass field and the
            # signed-flow read stays a recorded shadow (H10's churn floor rides inside it)
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
        if MAGNET_V3:
            zmap = mass                             # v3: zones cluster the mass field —
            zones_read = bool(mass)                 # the SAME field that serves the live pin
        else:
            zone_key = "signed_weight" if (SIGNED_PIN and _sig_ok) else mag_basis
            zmap = _net_gex_by_strike(zero_dte, zone_key, spot, minutes_to_close) if zone_key else {}
            zones_read = bool(zone_key)             # compute genuinely ran
        zones, contested = pin_zones(zmap, spot)
    except Exception:
        zones, contested = [], False
        zones_read = False
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
    # HYSTERESIS (H4 v2, 2026-07-12): measured live, pin_zones NEVER produced a rival
    # zone in 302 zoned scans — the contested blend above cannot be the teleport killer
    # because the teleports happen inside (or without) zone-1. The wheel-change rule:
    # a JUMP (> MAG_GLIDE_PTS) only happens when the challenger's local mass out-pulls
    # the incumbent's by MAG_HYST; small moves glide freely; a dead incumbent (no local
    # mass anymore) surrenders the wheel. Held scans are visible, never silent.
    if MAGNET_V3 and prev_pin is not None and pin is not None and mass:
        if abs(pin - prev_pin) > MAG_GLIDE_PTS:
            inc = _mass_near(mass, prev_pin)
            new = _mass_near(mass, pin)
            out["pin_candidate"] = round(pin, 4)
            if inc > 0 and new < MAG_HYST * inc:
                pin = prev_pin                      # challenger not strong enough: hold
                out["pin_held"] = True
            else:
                out["pin_held"] = False             # wheel legitimately changed hands
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
    # v3: the motion pack's magnet-walk must track the LIVE magnet's own field. Verify
    # round measured the zone-1-if-tight rule DEAD (an all-positive field clusters into
    # one 300-600pt mega-zone on real books — 0/302 live scans tight, killing
    # magnet_walk_sigma that was live 248/302 under the old attract-centroid). The
    # centroid is now the mass-weighted mean strike of the REACH-WINDOWED field —
    # always computable when the field exists, smooth by construction, and it lives
    # where the magnet lives. Same-day diffs only (gex_motion's 4-30 min lookback).
    if MAGNET_V3:
        _mt = sum(mass.values())
        out["pin_centroid"] = (round(sum(k * v for k, v in mass.items()) / _mt, 4)
                               if mass and _mt > 0 else None)
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
            # m4 (2026-07-12, verify round v2): the persisted window must reach as wide
            # as the frame the tablet draws — BOTH the σ-headroom (≈±1.8σ) AND any framed
            # structural tenor wall (nbs_reach, ≤2.5σ, passed from build_views) — or the
            # outer lane renders permanent blank bars a caption still calls "measured".
            # ±3% floor keeps the historic width; cap raised to ±6% (crash regimes up to
            # σ/spot ≈ 3% stay covered; diary cost ≈ +1.2KB/row at the cap).
            _need = max((2.0 * sigma / spot) if sigma else 0.0,
                        (nbs_reach / spot + 0.002) if nbs_reach else 0.0)
            _w = max(NBS_WINDOW, min(0.06, _need))
            _lo, _hi = spot * (1.0 - _w), spot * (1.0 + _w)
            # round the STRIKE too: SPY-proxy strikes are rescaled floats (~10.4-pt
            # grid) that would otherwise serialize at full precision and bloat the
            # diary — round(2) keeps it compact and grid-alignable by the tablet.
            out["net_by_strike"] = [[round(k, 2), round(v, 3)]
                                    for k, v in sorted(_nbs.items())
                                    if _lo <= k <= _hi]
        # v3: persist the EXACT reach-windowed field the live magnet reads, so the tablet
        # can draw the magnet's own terrain beside the signed call−put tilt bars — the
        # drawn magnet can never float outside the field the screen shows (the window IS
        # the field now, so containment holds by construction).
        if MAGNET_V3 and mass:
            out["mass_by_strike"] = [[round(k, 2), round(v, 1)]
                                     for k, v in sorted(mass.items())]
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
           "call_wall_gamma": None, "put_wall_gamma": None, "flip": None, "basis": None,
           "net_by_strike": None}
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
    # PER-STRIKE TENOR FIELD (2026-07-13): signed net dealer GEX per strike for
    # the 1-7DTE book (calls +, puts −) on STORED gamma — stable intraday like
    # every other slide-C read, deliberately NOT the live clock (the terrain
    # must not breathe with the 0DTE singularity). Windowed to ±NBS_WINDOW of
    # spot and persisted compact so the tablet can draw the multi-day book as
    # hollow OUTLINE bars behind today's solid 0DTE bars — same axis, own
    # normalization, never blended into the pure 0DTE read. Fail-open: any
    # surprise leaves the key None.
    try:
        nbs: dict[float, float] = {}
        for c in near_term:
            k = c.get("strike"); w = c.get(basis) or 0
            if k is None or not w:
                continue
            g = _stored_gamma(c)
            if not g:
                continue
            nbs[k] = nbs.get(k, 0.0) + (g * w if c.get("right") == "call" else -g * w)
        if nbs:
            lo, hi = spot * (1.0 - NBS_WINDOW), spot * (1.0 + NBS_WINDOW)
            out["net_by_strike"] = [[round(k, 2), round(v, 3)]
                                    for k, v in sorted(nbs.items()) if lo <= k <= hi]
    except Exception:
        pass
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

    # ANTI-DEAD-GUARD TELEMETRY (2026-07-13) — VERDICT DELIBERATELY UNTOUCHED.
    # This guard has trip level 0.6 against a tape whose live range is [−0.104, +0.441]:
    # 0 trips in 671 rows, while every non-null read stamps agrees=True / confidence=1.0.
    # A guard that never had the RANGE to condemn cannot honestly bless — but we do NOT fix
    # that here, because `agrees` is load-bearing: reversion_lens.py:140 turns
    # `sign_agrees is None` into a gamma-voter abstain, so flipping True→None would delete
    # the gamma vote on ~every pin day, un-A/B'd, on the first scan after deploy. That is a
    # live regime change wearing an honesty patch. Instead we MEASURE the guard on every
    # scan so `lefteye_guard_audit` can declare it DEAD out loud, and the retune ships behind
    # its own flag with its own A/B. The root cause was never the threshold — it was that a
    # guard could sit at 0 trips for 671 rows and no number anywhere said so.
    out["headroom"] = (round(abs(aggressor_flow) / conflict, 3) if conflict else None)
    out["informative"] = bool(conflict) and abs(aggressor_flow) >= 0.5 * conflict
    out["trip"] = ("lob_freight_train" if (flow_kind == "lob" and out["agrees"] is False)
                   else "options_sign_inversion" if out["agrees"] is False else None)
    # N14 residual (2026-07-12): a guard that never had the RANGE to condemn cannot
    # honestly bless. `agrees` stays untouched (load-bearing — see above), but the
    # RECORD-ONLY confidence now tells the truth: 1.0 is reserved for a read the tape
    # actually tested (informative); an out-of-range "no objection" is None — unverified,
    # not verified. No live consumer reads this field (checked 07-12); the diary does.
    if out["agrees"] is True and not out["informative"]:
        out["confidence"] = None
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


@functools.lru_cache(maxsize=256)
def _tau_for(dte: Optional[int], minutes_to_close: Optional[float]) -> Optional[float]:
    """Time-to-expiry in YEARS, measured in TRADING time. A 0DTE contract is priced at
    the LIVE minutes-to-close (charm ∝ 1/τ, so the into-the-bell acceleration is the
    whole 0DTE signal); if the clock isn't supplied it falls back to the half-session
    floor. Dated contracts are priced at the SESSIONS remaining, not the calendar days.
    Returns None on a missing dte.

    The whole clock is one unit — sessions / 252. A calendar-day numerator over a
    trading-day denominator (the pre-2026-07-12 bug) inflated τ by up to 365/252 =
    1.45x, worst over weekends and holiday weeks, which under-stated every gamma.

    lru_cache: the twin of the _nyse_holidays cache — this sits at the same flip
    root-finder leaf (~625K calls per build_views via _net_gex_at) and its dte>0
    branch re-ran _sessions_ahead's set-union + day loop every call (~60-70% of the
    engine's post-holiday-fix CPU; flip stack 0.78s→0.20s cached, output verified
    bit-identical). Inputs per scan collapse to ~8 (dte, clock) pairs. Same one-shot
    -scanner caveat as _nyse_holidays: a long-lived process crossing a session date
    boundary would serve yesterday's session count for dated tenors — the scanner
    is relaunched per tick, so the cache dies with the process."""
    if dte is None:
        return None
    if dte > 0:
        return _sessions_ahead(dte) / _TRADING_DAYS
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
           "vex_tenor": "1-7DTE", "cex_tenor": "0DTE", "minutes_to_close": minutes_to_close,
           # N11 (2026-07-12): net dealer charm is STRUCTURALLY locked negative by the
           # assumed-sign symmetry artifact — the drift word read "UP into the close" on
           # 804 of 806 live rows. A constant is not a signal: the WORD is suspended until
           # charm has a graded record (the NUMBER keeps recording for exactly that grading).
           # Nothing downstream may speak a charm direction while this is False.
           "charm_word_ok": False}
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
                flow_kind: str = "options",
                tape_prev: Optional[dict] = None,
                anchor_spot: Optional[float] = None,
                now: Optional[datetime] = None,
                prev_pin: Optional[float] = None,
                sigma_floor: Optional[float] = None) -> dict:
    """Pure assembler: the six slides + the consumer-facing magnet/regime. `magnet`
    is the 0DTE pin (B) when present, else the regime read's flip — that is what the
    reversion lens measures runway to. `minutes_to_close` sharpens the 0DTE charm
    clock; `sigma` (the day's σ yardstick) arms the ±½σ shove test; `flow_conflict`
    + `flow_kind` are the source-scaled trip level and tape semantics for slide E.

    H7 (2026-07-12): the REGIME, the FLIP and the SHOVE are read off TODAY'S book (A0),
    not the blended 0-7DTE chain (A). The blended read is computed anyway and recorded
    beside it (regime_tenor / flip_tenor / net_gex_tenor) so the disagreement rate is
    measured, and it remains the FALLBACK when today's book is unreadable."""
    zero, near = split_tenor(contracts)
    A = slide_flip(contracts, spot, minutes_to_close)     # blended 0-7DTE — A/B twin + fallback
    A0 = slide_flip(zero, spot, minutes_to_close)         # H7: TODAY'S book — the regime authority
    # H7 records the TENOR fix. It deliberately does NOT settle the BASIS question, which is a
    # separate open finding: pick_basis prefers open_interest, and a 0DTE strike's OI is
    # YESTERDAY'S — so A0 is "today's expiry, weighted by yesterday's positions". Volume is the
    # live read (slide_flows already forces volume for the 0DTE charm: "same-day OI ≈ 0 at the
    # open"). Which one the REGIME should use is genuinely unsettled — dealer gamma comes from
    # inventory (OI), but 0DTE inventory is stale and today's gamma at spot is dominated by what
    # traded today. So: A0 keeps the SAME basis rule the 0DTE pin already uses (one book, one
    # basis), and the volume-basis twin is RECORDED beside it so the question gets decided by
    # data rather than by whoever edits this line next. Shadow. Consumed by nothing.
    A0v = slide_flip(zero, spot, minutes_to_close, prefer="volume")
    # Fall back to the blended read only when today's book cannot be read at all (no basis,
    # or the IV blackout guard tripped — slide_flip returns regime "unknown"). A thin-but-real
    # 0DTE book still rules; going blind is the only thing that hands the vote back to the
    # dated chain, and the fallback is NAMED in the output so it can never pass as a 0DTE read.
    use_0dte = REGIME_0DTE and A0["regime"] != "unknown"
    R = A0 if use_0dte else A
    R_set = zero if use_0dte else contracts             # the contract set R was computed on
    # N8 reach on the RULER: the live σ decays all day, which would shrink the reach
    # window to ~±45pts by the close and silently evict a standing incumbent. The
    # day-open anchor (sigma_floor, from the diary) floors it; a real vol spike still
    # widens it (max) — same physics as reversion_lens.sigma_ruler.
    _reach_sigma = max(x for x in (sigma, sigma_floor) if x) \
        if (sigma or sigma_floor) else None
    # m4 (verify round): C is computed BEFORE B so the persisted bar window can be
    # told how far the frame will reach — the tablet frames the structural tenor
    # walls (within 2.5σ) and a framed wall with no measured bar under it is the
    # card's harm verbatim. The hint is the farthest such wall, in price points.
    C = slide_tenor_walls(near, spot)
    _reach_hint = None
    try:
        if spot and _reach_sigma:
            _ds = [abs(w - spot) for w in (C.get("call_wall"), C.get("put_wall"))
                   if w is not None and abs(w - spot) <= 2.5 * _reach_sigma]
            _reach_hint = max(_ds) if _ds else None
    except (TypeError, ValueError):
        _reach_hint = None
    B = slide_0dte(zero, spot, minutes_to_close, prev_pin=prev_pin,   # H4: hysteresis witness
                   sigma=_reach_sigma,                                # N8: the reach window
                   nbs_reach=_reach_hint)                             # m4: cover the framed walls
    E = reconcile_sign(R["regime"], aggressor_flow, flow_conflict, flow_kind)
    F = slide_flows(contracts, spot, minutes_to_close=minutes_to_close)   # vanna/charm flows
    magnet = B["pin"] if B["pin"] is not None else (R["flip"] if R["flip"] is not None
                                                    else A["flip"])
    # SHOVE TEST (P5): re-price net dealer GEX at spot ± ½σ and record the SIGNED
    # margins — how much calming GRAVITY survives a half-sigma FLOW shove each way
    # (distance from zero = distance from flipping; a bool would throw that away).
    # Same basis, clock AND TENOR as the regime read (H7) — shoving a blended book while
    # calling the regime off today's would be asking two different questions.
    shove = None
    try:
        if sigma and spot and R["basis"]:
            net0 = R["net_gex"]                 # signed dealer GEX at spot (the regime read)
            # m2 (2026-07-12): the shove asks "can FLOW move price there before the
            # bell?" — so the probe distance must live on the time LEFT, not the whole
            # day. A ±0.5σ_day probe at 15:30 tests a move that cannot happen, and the
            # test cried "fragile" about it. √(minutes-left/390) is the same Brownian
            # clock the grade barriers use (N18). Outside RTH (mtc None) the full-day
            # probe stands — the whole session is then ahead. shove_v=2 tags
            # the era: v1 margins probed a fixed ±0.5σ_day and must never pool with these.
            probe = SHOVE_SIGMA
            if minutes_to_close:
                probe = SHOVE_SIGMA * math.sqrt(max(minutes_to_close, 5.0) / 390.0)
            up = _net_gex_at(R_set, spot + probe * sigma, R["basis"], minutes_to_close)
            dn = _net_gex_at(R_set, spot - probe * sigma, R["basis"], minutes_to_close)
            denom = abs(net0) if net0 else None
            shove = {"shove_sigma": round(probe, 4), "shove_v": 2,
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
    # GAMMA-OWNERSHIP TAPE (shadow) — the axis aggressor_flow cancels itself out on. Fail-open
    # (same discipline as `shove`): any surprise → None, never a fabricated zero. RECORDED AND
    # READ BY NOTHING — GAMMA_TAPE_CONSUME is False, the trip levels are None, and `tape_word`
    # returns None. On deploy this changes no live output of the fade lens, the Watchtower or
    # the tablet; anything that moves is a bug.
    tape = None
    try:
        tape = gamma_tape(zero, spot, minutes_to_close, sigma, anchor_spot, tape_prev, now)
    except Exception:
        tape = None
    return {
        "spot": round(spot, 4) if spot else None,
        "magnet": magnet,                       # the pin the fade reverts to (for runway)
        # H7: regime / flip / net_gex are TODAY'S BOOK. The blended 0-7DTE twin rides beside
        # them (regime_tenor / flip_tenor / net_gex_tenor) as terrain and as the A/B, so the
        # disagreement rate is measured from the first live scan instead of argued about.
        "regime": E["sign"],                    # sign-checked regime (0DTE)
        "regime_raw": R["regime"],              # pre-slide-E regime (0DTE)
        "regime_tenor": A["regime"],            # blended 0-7DTE — the old read, now the A/B twin
        "regime_source": "0dte" if use_0dte else "blended_fallback",
        "regime_basis": R["basis"],             # which weight the regime was read on
        # SHADOW twin: today's book on the VOLUME basis. Records the open OI-vs-volume question
        # (0DTE OI is yesterday's) so it can be settled with data. Consumed by nothing.
        "regime_0dte_vol": A0v["regime"], "net_gex_0dte_vol": A0v["net_gex"],
        "gamma_tape": tape,                     # SHADOW, record-only. No consumer, by design.
        "flip": R["flip"], "flip_band": R["flip_band"],
        "flip_tenor": A["flip"],                # blended border — outer terrain, not the regime
        "net_gex": R["net_gex"], "net_gex_tenor": A["net_gex"],
        "sign_agrees": E["agrees"], "sign_confidence": E["confidence"],
        "vex": F["vex"], "cex": F["cex"],        # net vanna / charm exposure (shadow)
        "vex_sign": F["vex_sign"], "cex_sign": F["cex_sign"],
        "shove": shove,                          # ±½σ GRAVITY stress-test (P5); None w/o σ
        "slides": {"A_flip": A, "A0_0dte_flip": A0, "B_0dte": B, "C_tenor": C,
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
    coverage: Optional[dict] = None   # N7: the native fetch's own coverage verdict —
                                      # rides the LIVE engine leg, not just the shadow read


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
                  source=("spy_proxy×%.4f" % scale) if proxy else "native",
                  coverage=(chain.get("meta") or {}).get("coverage") if native_served else None)


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
              cache: Optional[dict] = None,
              prev_pin: Optional[float] = None,
              sigma_floor: Optional[float] = None) -> Optional[dict]:
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
                        flow_kind=flow_kind,                       # + its sign semantics
                        prev_pin=prev_pin,                         # H4: last diary pin (hysteresis)
                        sigma_floor=sigma_floor)                   # N8: reach rides the ruler
    age = round((now - entry.ts).total_seconds() / 60.0, 2)
    views["meta"] = {"gex_source": entry.source, "snapshot_spot": entry.anchor_spot,
                     "age_min": age, "repriced": live_spot is not None and live_spot != entry.spot,
                     "sigma": entry.sigma, "minutes_to_close": mtc,
                     "native_coverage": entry.coverage}   # N7: live-leg coverage verdict
    # N7: when the read is NOT native and the native feed rejected its last fetch on
    # coverage, say so — "proxy because the native book came back half-fetched" is a
    # different fact from "proxy because the token died", and the diary must carry it.
    if entry.source != "native":
        try:
            import native_gex_feed as _ngf
            rej = getattr(_ngf, "LAST_REJECT", None)
            if rej and rej.get("ts"):
                age_s = (now - datetime.fromisoformat(rej["ts"])).total_seconds()
                if 0 <= age_s <= 900:
                    views["meta"]["native_reject"] = rej.get("reason")
        except Exception:
            pass
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
