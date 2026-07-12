"""
reversion_lens.py — BETA mean-reversion / exhaustion lens for SPX (shadow mode).

The "box" from the 2026-06-23 session plan (see docs/reversion-lens-spec.md): a
self-contained module that reads MARKET REGIME from a stack of corroborating reads
(gamma sign/flip + walls, realized-range vs expected-move, intraday variance-ratio,
VIX term structure), decides pinning vs trending (>=2 reads must agree), and on that
basis emits two lens signals (the Fade Lens IS the brain — the voter/confluence
system was deleted 2026-07-03):

    reversion_extreme  — fade a sigma-stretch / wall tag, but only in a pinning
                         regime; arms on proximity, scores on the reaction.
    level_reclaim      — go WITH a decisive reclaim/break of a key level (prior
                         close/high/low, round number, or a broken gamma wall);
                         a wall break on volume flips the regime to trending.

BETA / SHADOW posture: REVERSION_LIVE=False by default → these COMPUTE and LOG every
tick (regime, stretch, wall proximity, would-fire) to the telemetry stream + the
heartbeat, but nothing trades. Going live requires BOTH flipping REVERSION_LIVE AND
clearing the Wilson promotion gate (see live_allowed) — the switch alone arms nothing.
SPX only. All external lookups are wrapped so a failure degrades to a quiet
null record and never breaks a scan.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent

# --- BETA gate -------------------------------------------------------------
REVERSION_LIVE = False           # False = shadow (record only, no confluence vote)
THETA_SHADOW = True              # theta native-GEX A/B recorder (record only, never fires)

# GAMMA-TAPE inter-tick memory (2026-07-13): {ticker: {"ts": datetime, "q": {(K, right):
# (buy_q, sell_q)}}}. The provider's per-strike flow is a running WHOLE-DAY cumulative, so
# differencing consecutive snapshots is what turns it into a windowed tape — a cumulative
# can never show a turn (by 13:00 the deciding hour is ~8% of the denominator), and the turn
# is the entire point. In-process only, like the rest of the scan's memory: a restart simply
# yields no marginal until the next scan, which is the honest answer.
_TAPE_PREV: dict[str, dict] = {}


def live_allowed() -> tuple[bool, str]:
    """THE EARN-TRUST GATE, enforced. Live requires BOTH the hand switch
    (REVERSION_LIVE) AND a graded paper record that clears the Wilson promotion
    gate — flipping the constant alone can no longer arm anything. Fail-closed:
    if the gate can't be evaluated, the answer is paper."""
    if not REVERSION_LIVE:
        return False, "REVERSION_LIVE is off (shadow mode)"
    try:
        import gex_polarity_ab as _ab
        return _ab.promotion_gate()
    except Exception as e:  # noqa: BLE001 — a broken gate must read as "not earned"
        return False, f"promotion gate unavailable ({type(e).__name__}) — staying paper"
TICKERS = ("SPX",)               # scope: SPX only (mini-index twin retired 2026-07-04 — same underlying, <1% of the gamma)

# --- tunables (will become self-tuned via the posterior loop) --------------
STRETCH_FIRE = 1.0               # |gap-from-prior-close σ| at/above this = extreme
                                 # (beta-loose to capture candidates; self-tuned later —
                                 #  today's -1.32σ capitulation arms at 1.0, not 1.5)
WALL_PROX_SIGMA = 0.50           # within this many σ of a wall = armed (incl. overshoot)
RANGE_EM_PIN = 0.70              # realized range <= this × expected move = compressed/pin
RANGE_EM_TREND = 1.00            # realized range >= this × EM = expanded/trend
VR_PIN = 0.90                    # variance-ratio below = reverting (pin)
VR_TREND = 1.10                  # variance-ratio above = trending
VIXTS_CONTANGO = 0.95            # VIX/VIX3M below = calm/contango (pin)
VIXTS_BACKWARD = 1.00            # VIX/VIX3M at/above = stress/backwardation (trend)
ROUND_STEP = {"SPX": 50.0}               # round-number magnet spacing

# --- beta paper-resolution (target-before-stop over the day's window) -------
# A would-fire "wins" if the index reaches a favorable target BEFORE an adverse
# stop, AT ANY TIME within the rest of the session — 5 min, 25 min, or 2 hours,
# it doesn't matter (matches how a real position is exited: take profit whenever
# it shows up, but not if it stops out first). Target < stop because options are
# convex — a small favorable underlying move already puts the option in profit.
PAPER_TARGET_SIGMA = 0.30   # favorable index move (σ) ≈ option in profit
PAPER_STOP_SIGMA = 0.30     # adverse index move (σ) ≈ a stop-out
# SYMMETRIC by design: with unequal lines a skill-free coin-flip grades at
# stop/(target+stop) — the old 0.25/0.35 handed random guessing a 58.3% "win
# rate" while every edge check compared against 50%. Equal lines make 50% mean
# no-skill again, so the Wilson gate below measures real edge.
PAPER_MAX_HOLD_MIN = 120    # watch up to ~2h within the session for it to work

# --- runway gate (NEW, shadow) ---------------------------------------------
# A fade only pays if price has enough ROOM to reach the +PAPER_TARGET_SIGMA target
# before it stalls at the magnet it's reverting toward. The magnet is the gamma pin
# (gamma_flip) when fresh GEX gives us one, else the opposing wall as a structural
# fallback. Candidates with less than RUNWAY_MIN_SIGMA of room are still recorded
# (telemetry) but are NOT counted as would-fires — this filters the "armed on a tiny
# stretch with no room" candidates that dominate the scratch column. Static for now;
# the posterior loop self-tunes it later. Accuracy depends on a fresh GEX magnet.
RUNWAY_MIN_SIGMA = PAPER_TARGET_SIGMA + 0.05   # 0.35σ of room required to count a fire

# --- BREAK LENS (offense, shadow) — the two-stage cock→fire machine ---------
# Rides the level_reclaim storage key (rebuilt IN PLACE 2026-07-05; the old
# 1.3× volume-pop trigger's verdict survives as `vol_pop_fired`). GRAVITY arms
# it — a storm-side (negative net-GEX-at-spot) fade rejection is dealers
# AMPLIFYING, not damping — and FLOW fires it. Dials pinned to the 2026-07-05
# SPX-only re-baseline (319 scans over 6 days, 2026-06-25→07-02).
BREAK_COCK_EXPIRE_MIN = 45.0     # cock TTL: median rejection→first +0.15σ break-direction
                                 # move is 34 min and 8 of 11 eventual continuations begin
                                 # within 45 (n=11, too thin to move off the default)
BREAK_MAX_COCKS_PER_LEVEL = 2    # cocks per 5-pt level bucket per day: one re-arm, then done
BREAK_RUNWAY_MIN_SIGMA = 0.35    # smallest runway that fits the symmetric 0.30σ profit leg
                                 # plus one median scan of noise (P50 0.039σ); continued
                                 # episodes ran ~0.25σ at 60m, so it does not over-filter
BREAK_TAPE_MIN = 0.15            # |tilt × determinate_share| floor for a ONE-WAY tape (FLOW);
                                 # NO EVIDENCE BASE until lob_flow's first live day — ships
                                 # beta-loose, self-tuned like the other dials
BREAK_LEVEL_STEP = 5.0           # level bucket for the per-level cock cap (round to nearest 5)
BREAK_LATE_DAY_ET_MIN = 15 * 60 + 15   # ~15:15 ET — by here charm has drained the pin's
                                 # grip (GRAVITY fades into the close), a break TAILWIND;
                                 # recorded in the fire_gates snapshot (late_day_tailwind),
                                 # NOT yet an active gate relaxation (needs evidence first)
# INACTIVE HOOK — VEX signs uncalibrated; must stay 1.0 and no gate may read
# vex/cex until the polarity cards settle the sign; down-breaks ("put" cocks)
# earn extra conviction then. Multiplier only — it can never touch direction.
VANNA_AFTERBURNER_DOWN = 1.0


# ===========================================================================
# Pure logic (no I/O) — unit-testable
# ===========================================================================
def gamma_vote_sign(gxv: Optional[dict]) -> str:
    """The gamma voter's input for the regime stack: the gravity engine's
    sign-at-spot — EXCEPT a flow-downgraded read (slide E marked it 🟡
    "uncertain": a pin fighting heavy one-way tape) ABSTAINS instead of voting
    'pin' at full strength. The other three regime reads must then carry a pin
    verdict alone. This is the fix for the 2026-07-02 losing cluster, where
    fades kept arming into a freight train the flow sensor had already flagged.

    FAIL-SAFE (L1, 2026-07-07): a long-gamma pin whose tape was never SEEN
    (sign_agrees is None → flow unknown) also abstains — the veto used to pass
    such a read at full confidence, trusting the pin most when it was least
    corroborated. sign_agrees is True only when a tape read confirmed the pin
    and False (already routed to 'uncertain' above) when it conflicted; None
    means blind, so the gamma vote sits out until a real tape read arrives."""
    g = gxv or {}
    if g.get("regime") == "uncertain":
        return "unknown"
    raw = g.get("regime_raw")
    if raw == "long_gamma" and g.get("sign_agrees") is None:
        return "unknown"                         # blind on the tape → don't vote pin
    return {"long_gamma": "positive",
            "short_gamma": "negative"}.get(raw, "unknown")


# Order-book coverage below this fraction = too little of the tape was classifiable
# to trust its direction → treat as blind (→ fail-safe abstain), NOT a small number
# that would falsely read as "tape confirms the pin". Two live days: determinate_share
# p50 ~0.55, min ~0.34, so only the very thinnest tapes fail the gate.
DET_FLOOR = 0.40

# H2 size guard: a 0DTE gamma wall KNOWN to carry less than this share of the
# whole |γ·w| field is noise, not a wall — fall through to the OI/tenor walls.
# Unknown share (the morning guard) trusts the wall. 0.02, not 0.03: the share
# denominator is the WHOLE field both sides, and the live put side ran
# 0.031-0.054 on 07-10 (mornings 0.031-0.033) — a 0.03 floor sat exactly on
# its operating point and would have flickered the put wall off at the open.
WALL_MIN_SHARE = 0.02

# H2 v2 placement guards (2026-07-12 verification): a wall is OPERATIVE only
# when it stands where a wall stands. The 0DTE gamma wall HUGS spot (07-10
# replay: 0.01-0.62σ away, mostly ≤0.3σ — the ATM artifact re-forming at
# price, not a barrier); fed raw to the fade it armed the wall-proximity test
# on essentially every scan and the F3 clamp mechanically zeroed every fire
# (runway could never reach RUNWAY_MIN_SIGMA). Floor = the fire floor: a wall
# inside the minimum runway is part of the neighborhood price is exploring —
# the operative containment is the FIRST wall beyond it ("to get to the far
# wall you must break the near one"). Ceiling = reach: the 8000/7000 crash
# strata σ-multiples out were the ORIGINAL H2 bug and must never return via
# the tenor fallback.
WALL_MIN_DIST_SIGMA = RUNWAY_MIN_SIGMA
WALL_MAX_DIST_SIGMA = 3.0


def operative_wall(candidates, spot, sigma, side: str):
    """The wall the fade actually fights (H2 v2): the FIRST candidate — tenor
    order, nearest book first — placed like a wall: WALL_MIN_DIST_SIGMA to
    WALL_MAX_DIST_SIGMA from spot on its own side (calls above, puts below).
    None when nothing qualifies — an honest absence arms on stretch only and
    leaves the runway unclamped, never a fiction bound. A broken (overshot)
    wall stops qualifying, so the NEXT wall out becomes operative: break-to-
    extend falls out of the geometry."""
    if not spot or not sigma or sigma <= 0:
        return None
    for w in candidates:
        if w is None:
            continue
        d = (w - spot) / sigma if side == "call" else (spot - w) / sigma
        if WALL_MIN_DIST_SIGMA <= d <= WALL_MAX_DIST_SIGMA:
            return w
    return None


def _veto_flow(options_flow: Optional[float],
               lob_fold: Optional[dict]) -> tuple[Optional[float], str]:
    """The tape read the pin-veto consumes (M5/M9 + L1, 2026-07-07). Prefers the
    FRESH ~30s order-book tape over the stale whole-day options aggressor read.
    `determinate_share` is a RELIABILITY GATE, not a multiplier: below DET_FLOOR the
    tape is too thin to read → None (→ the blind fail-safe), above it we judge on the
    pure signed `tilt` (the true analog of the options tilt; the LOB caller pairs it
    with FLOW_CONFLICT_LOB, since tilt×det against the options-scaled 0.6 never trips).
    Falls back to options, then None. Both sources signed [-1,1], buy-positive.
    Returns (flow | None, source ∈ {'lob','lob_thin','options','none'})."""
    lf = (lob_fold or {}).get("lob_flow") or {}
    tilt, det = lf.get("tilt"), lf.get("determinate_share")
    if tilt is not None and det is not None:
        try:
            t, d = float(tilt), float(det)
        except (TypeError, ValueError):
            t = d = float("nan")
        if math.isfinite(t) and math.isfinite(d):
            if d < DET_FLOOR:
                return None, "lob_thin"        # too little coverage → blind → fail-safe
            return max(-1.0, min(1.0, t)), "lob"
    if options_flow is not None:
        return options_flow, "options"
    return None, "none"


def variance_ratio(closes: list[float], q: int = 4) -> Optional[float]:
    """Lo-MacKinlay VR(q) = Var(q-period return) / (q × Var(1-period)). Scale-
    invariant, so index price bars are fine. >1 trending, <1 mean-reverting."""
    if not closes or len(closes) < max(q * 4, 12):
        return None
    rets = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    if len(rets) < q + 1:
        return None
    var1 = statistics.pvariance(rets)
    qret = [closes[i] - closes[i - q] for i in range(q, len(closes))]
    if var1 <= 0 or len(qret) < 2:
        return None
    varq = statistics.pvariance(qret)
    return round(varq / (q * var1), 4)


def classify_regime(g_sign: str, range_em: Optional[float],
                    vr: Optional[float], vix_ts: Optional[float]) -> dict:
    """Stack the reads → 'pinning' / 'trending' / 'neutral'. Each read votes
    'pin', 'trend', or abstains; a confident regime needs >=2 votes one way and
    more than the other (the '>=2 reads must agree, no single dial' rule)."""
    votes: dict[str, Optional[str]] = {}
    votes["gamma"] = ("pin" if g_sign == "positive"
                      else "trend" if g_sign == "negative" else None)
    votes["range_em"] = (None if range_em is None else
                         "pin" if range_em <= RANGE_EM_PIN else
                         "trend" if range_em >= RANGE_EM_TREND else None)
    votes["variance_ratio"] = (None if vr is None else
                               "pin" if vr < VR_PIN else
                               "trend" if vr > VR_TREND else None)
    votes["vix_ts"] = (None if vix_ts is None else
                       "pin" if vix_ts < VIXTS_CONTANGO else
                       "trend" if vix_ts >= VIXTS_BACKWARD else None)
    pin = sum(1 for v in votes.values() if v == "pin")
    trend = sum(1 for v in votes.values() if v == "trend")
    if pin >= 2 and pin > trend:
        regime = "pinning"
    elif trend >= 2 and trend > pin:
        regime = "trending"
    else:
        regime = "neutral"
    # confidence = agreement strength among the reads that voted
    voted = pin + trend
    conf = round((max(pin, trend) / voted), 3) if voted else 0.0
    return {"regime": regime, "pin_votes": pin, "trend_votes": trend,
            "confidence": conf, "reads": votes}


def reversion_extreme(spot: float, prior_close: float, sigma: float,
                      call_wall: Optional[float], put_wall: Optional[float],
                      regime: str, last_bars: list[dict],
                      vwap_stretch: Optional[float] = None,
                      gamma_flip: Optional[float] = None,
                      pin_magnet: Optional[float] = None) -> dict:
    """Fade signal. Score in [-1,1]: + = fade LONG (oversold), − = fade SHORT.
    Arms on a >=STRETCH_FIRE σ stretch (gap from prior close OR distance from the
    session VWAP) OR proximity to a wall (incl. overshoot); 'reaction' = the latest
    bar turning back against the stretch. `arm_reason` records WHY it armed.

    Runway gate (shadow): a fire also requires RUNWAY_MIN_SIGMA of room from spot to
    the magnet it reverts toward. COUPLING FIX (2026-07-03): the preferred magnet is
    `pin_magnet` — the FILL-CONFIRMED pin from the gravity engine (a different
    witness than the flip, which Gate 2's gamma vote already leans on) — falling
    back to gamma_flip, then the opposing wall. One fact can no longer cast two
    votes (or veto its own setup on fade-longs). Armed candidates that lack room
    are recorded but not counted as would-fires."""
    out = {"name": "reversion_extreme", "score": 0.0, "confidence": 0.0,
           "fired": False, "armed": False, "reaction": False, "direction": "none",
           "gap_stretch": 0.0, "vwap_stretch": None, "arm_reason": None,
           "wall_dist_call": None, "wall_dist_put": None,
           "runway_sigma": None, "runway_ok": False, "magnet": None,
           "magnet_raw": None, "magnet_out_of_band": False,
           "fired_pre_runway": False, "notes": ""}
    if not spot or not sigma or sigma <= 0 or not prior_close:
        out["notes"] = "missing spot/sigma/prior_close"
        return out

    gap = (spot - prior_close) / sigma
    out["gap_stretch"] = round(gap, 3)
    vws = vwap_stretch if vwap_stretch is not None else 0.0
    out["vwap_stretch"] = round(vwap_stretch, 3) if vwap_stretch is not None else None
    dist_call = (call_wall - spot) / sigma if call_wall else None
    dist_put = (spot - put_wall) / sigma if put_wall else None
    out["wall_dist_call"] = round(dist_call, 3) if dist_call is not None else None
    out["wall_dist_put"] = round(dist_put, 3) if dist_put is not None else None

    # direction: oversold (deep negative gap/VWAP stretch OR at/below put wall) →
    # fade LONG; overbought (deep positive stretch OR at/above call wall) → SHORT.
    near_put = dist_put is not None and dist_put <= WALL_PROX_SIGMA   # at/below put wall (overshoot ok)
    near_call = dist_call is not None and dist_call <= WALL_PROX_SIGMA
    down_stretch = gap <= -STRETCH_FIRE or vws <= -STRETCH_FIRE
    up_stretch = gap >= STRETCH_FIRE or vws >= STRETCH_FIRE
    if down_stretch or near_put:
        direction, sign = "call", +1.0
        out["arm_reason"] = "put_wall" if (near_put and not down_stretch) else "stretch"
    elif up_stretch or near_call:
        direction, sign = "put", -1.0
        out["arm_reason"] = "call_wall" if (near_call and not up_stretch) else "stretch"
    else:
        out["notes"] = f"not stretched (gap {gap:+.2f}σ vwap {vws:+.2f}σ, no wall tag)"
        return out
    out["direction"] = direction
    out["armed"] = True

    # runway gate: room from spot to the magnet it reverts toward. Witness order
    # (coupling fix): fill-confirmed pin → flip → opposing wall. Fade LONG bounces
    # UP toward the magnet above; fade SHORT drops DOWN toward it. Recorded always;
    # gates `fired` so a no-room candidate is a non-fire (but stays visible as armed).
    level = pin_magnet if pin_magnet is not None else gamma_flip
    if direction == "call":
        magnet = level if level is not None else call_wall
    else:
        magnet = level if level is not None else put_wall
    # F3 clamp (2026-07-06): the pin can print OUTSIDE the wall band -- thin/early OI
    # can put the net-GEX magnet above the call wall or below the put wall. Runway
    # measured to an out-of-band pin is fiction: price would have to blow through the
    # wall to reach it (07-06 AM: magnet 7630 vs a 7550 call wall implied a phantom
    # ~1.5-sigma runway while only ~0.45-sigma of real room to the ceiling existed).
    # Clamp the revert-target to the wall it reverts TOWARD: a fade LONG (call) can't
    # be pulled past the call wall above it, a fade SHORT (put) can't be pulled past
    # the put wall below it. Direction-aware so the clamp only ever moves the magnet
    # TOWARD spot -- runway can shrink but never grow, so an out-of-band pin can never
    # manufacture a fire (a wrong-side pin just yields negative runway and no fire).
    # Keep the raw pin + a flag so the clamp is visible and nothing is silently lost.
    out["magnet_raw"] = round(magnet, 2) if magnet is not None else None
    if magnet is not None:
        if direction == "call" and call_wall is not None and magnet > call_wall:
            magnet, out["magnet_out_of_band"] = call_wall, True
        elif direction == "put" and put_wall is not None and magnet < put_wall:
            magnet, out["magnet_out_of_band"] = put_wall, True
    if direction == "call":
        runway = (magnet - spot) / sigma if magnet is not None else None
    else:
        runway = (spot - magnet) / sigma if magnet is not None else None
    out["magnet"] = round(magnet, 2) if magnet is not None else None
    out["runway_sigma"] = round(runway, 3) if runway is not None else None
    out["runway_ok"] = bool(runway is not None and runway >= RUNWAY_MIN_SIGMA)

    # reaction: latest bar turns back against the stretch (a fade-long wants the
    # last bar green / closing up; a fade-short wants it red / closing down).
    reaction = False
    if last_bars:
        b = last_bars[-1]
        if direction == "call":
            reaction = (b.get("close", 0) >= b.get("open", 0))
        else:
            reaction = (b.get("close", 0) <= b.get("open", 0))
    out["reaction"] = bool(reaction)

    # magnitude: how extreme — the larger of the gap / VWAP stretch (σ, capped)
    extremity = min(1.0, max(abs(gap), abs(vws)) / (STRETCH_FIRE * 2))
    score = sign * max(0.3, extremity)
    out["score"] = round(score, 3)
    pinning = regime == "pinning"
    out["confidence"] = round((0.7 if pinning else 0.35) * (1.0 if reaction else 0.6), 3)
    # `fired_pre_runway` = the old gate (for A/B telemetry); `fired` adds the runway gate.
    out["fired_pre_runway"] = bool(out["armed"] and reaction and pinning)
    out["fired"] = bool(out["fired_pre_runway"] and out["runway_ok"])
    out["notes"] = (f"{direction} fade ({out['arm_reason']}) | gap {gap:+.2f}σ "
                    f"vwap {vws:+.2f}σ call/put dist "
                    f"{out['wall_dist_call']}/{out['wall_dist_put']}σ "
                    f"| runway {out['runway_sigma']}σ ok={out['runway_ok']} "
                    f"| regime {regime} react={reaction}")
    return out


def level_reclaim(spot: float, sigma: float, prior_close: float,
                  prior_high: Optional[float], prior_low: Optional[float],
                  call_wall: Optional[float], put_wall: Optional[float],
                  round_step: float, last_bars: list[dict]) -> dict:
    """Momentum-at-levels signal: fires WITH a decisive break/reclaim of a key
    level confirmed by the latest bar's direction + a relative volume pop. A wall
    break flips the regime (signalled via 'flips_regime')."""
    out = {"name": "level_reclaim", "score": 0.0, "confidence": 0.0,
           "fired": False, "direction": "none", "level": None, "level_kind": None,
           "flips_regime": False, "notes": ""}
    if not spot or not sigma or sigma <= 0:
        out["notes"] = "missing spot/sigma"
        return out

    levels = []
    for kind, lvl in (("prior_close", prior_close), ("prior_high", prior_high),
                      ("prior_low", prior_low), ("call_wall", call_wall),
                      ("put_wall", put_wall)):
        if lvl:
            levels.append((kind, float(lvl)))
    if round_step:
        rnd = round(spot / round_step) * round_step
        levels.append(("round", rnd))
    if not levels:
        out["notes"] = "no levels"
        return out

    kind, lvl = min(levels, key=lambda kl: abs(kl[1] - spot))
    dist = (spot - lvl) / sigma
    if abs(dist) > WALL_PROX_SIGMA:
        out["notes"] = f"nearest level {kind} {lvl:.1f} too far ({dist:+.2f}σ)"
        return out

    # break/reclaim direction + volume confirmation from the last bar
    vol_pop = False
    direction = "none"
    if len(last_bars) >= 6:
        b = last_bars[-1]
        recent = [x.get("volume", 0) or 0 for x in last_bars[-6:-1]]
        avg = (sum(recent) / len(recent)) if recent else 0
        vol_pop = avg > 0 and (b.get("volume", 0) or 0) >= 1.3 * avg
        if b.get("close", 0) > lvl and b.get("close", 0) >= b.get("open", 0):
            direction = "call"
        elif b.get("close", 0) < lvl and b.get("close", 0) <= b.get("open", 0):
            direction = "put"
    if direction == "none":
        out["notes"] = f"at {kind} {lvl:.1f} ({dist:+.2f}σ) no decisive break"
        return out

    out["direction"] = direction
    out["level"] = round(lvl, 2)
    out["level_kind"] = kind
    out["flips_regime"] = kind in ("call_wall", "put_wall") and vol_pop
    out["score"] = round((+0.6 if direction == "call" else -0.6) * (1.0 if vol_pop else 0.5), 3)
    out["confidence"] = round(0.7 if vol_pop else 0.4, 3)
    out["fired"] = bool(vol_pop)
    out["notes"] = (f"{direction} break of {kind} {lvl:.1f} "
                    f"vol_pop={vol_pop} flips_regime={out['flips_regime']}")
    return out


def break_lens_state(prior_rows: list[dict], *, now: datetime,
                     spot: Optional[float], sigma: Optional[float],
                     gamma_sign: str, regime: str, rev: dict, lvl: dict,
                     lob_flow: Optional[dict],
                     shelf_up: Optional[tuple], shelf_dn: Optional[tuple],
                     last_bars: list[dict],
                     lob_fold_ts: Optional[str] = None,
                     lob_fold_age_s: Optional[float] = None) -> dict:
    """BREAK LENS (offense, shadow) — the two-stage cock→fire machine that rides
    the level_reclaim storage key. GRAVITY arms it (a storm-side rejection means
    dealers amplify, not damp); FLOW fires it (one-way tape into open runway).

    Pure function, derived FRESH each scan from today's diary rows — the diary
    is the system's ONLY inter-tick memory (the process exits between ticks),
    so there is NO new state file; carry-forward = the latest prior SPX row
    that CARRIES break fields (fail-open/legacy rows are transparent), and the
    per-level cap = distinct `cocked_at` today whose 5-pt bucket matches.

        idle ──COCK──▶ cocked ──FIRE──▶ fired          (one shot per cock)
                          │────DECOCK──▶ decocked      (calm side reclaimed)
                          │────EXPIRE──▶ expired       (> BREAK_COCK_EXPIRE_MIN)

    COCK   — gamma_sign 'negative' (storm side, named field: the flat telemetry
             gamma_sign) AND the fade lens armed (ANY rejection scene — reaction
             NOT required, per the locked 2026-07-05 decision) AND an anchor
             level resolvable AND the level's cock cap not spent. Continuation
             direction = the OPPOSITE of the fade. No fire on the cocking scan.
    HOLD   — neutral regime, failed gates, or gamma_sign 'unknown': never fire,
             never decock — the cock just waits out its clock.
    DECOCK — gamma_sign flips 'positive': price reclaimed the calm side of the
             flip and GRAVITY damps again.
    EXPIRE — cock older than BREAK_COCK_EXPIRE_MIN.
    FIRE   — base trigger (last vol_bar decisively closes beyond cock_level in
             cock_direction — same bar rules as level_reclaim's break read) AND
             all four gates: trending regime + still storm-side + one-way tape
             + runway to the next shelf. The tape gate is the ONLY fail-closed
             rule anywhere: missing/stale/vetoed lob_flow → tape_ok False → NO
             gated fire.

    `fired_pre_gates` is the UNGATED twin (base trigger alone) — the faster A/B
    Wilson record. Both twins are ONE-SHOT (True only on the transition scan;
    break_state carries the memory) so _fresh_would_fires' dedup stays exact.
    On any trigger scan the returned dict also carries `direction` =
    cock_direction (the grader reads it).

    `shelf_up`/`shelf_dn` are (level, kind) pairs — the best-known wall on each
    side (0DTE gamma wall preferred, OI band wall fallback). shelf_up bounds
    up-breaks ('call'), shelf_dn down-breaks ('put'); the same pair supplies
    the arming-wall anchor when level_reclaim resolved no level."""
    out = {"break_state": "idle", "cocked_at": None, "cock_level": None,
           "cock_level_kind": None, "cock_direction": None,
           "cock_count_today": 0, "cock_age_min": None,
           "fired": False, "fired_pre_gates": False,
           # the legacy 1.3× volume-pop detector's verdict, kept for continuity
           "vol_pop_fired": bool(lvl.get("fired")),
           "fire_gates": None}

    # --- today's break history from the diary (SPX rows, time-ordered) ------
    # Sorted by the raw ISO ts string, the _fresh_would_fires convention: every
    # row is written with the same ET offset, so string order IS time order —
    # and one stray naive-ts row (fixture leakage has precedent) can only
    # mis-sort itself, never raise the naive-vs-aware compare a datetime sort
    # would (which would fail-open the whole break machine for the day).
    hist: list[tuple[str, dict]] = []
    for r in prior_rows or []:
        if r.get("ticker") not in TICKERS or not isinstance(r.get("ts"), str):
            continue                       # corrupt row: carries no state
        hist.append((r["ts"], r.get("level_reclaim") or {}))
    hist.sort(key=lambda x: x[0])

    def _bucket(level) -> Optional[float]:
        # diary-carried levels are coerced defensively: one junk cock_level row
        # would otherwise raise here on EVERY later scan — a dead break machine
        # for the rest of the day (the bad row never leaves the diary)
        try:
            return round(float(level) / BREAK_LEVEL_STEP) * BREAK_LEVEL_STEP
        except (TypeError, ValueError):
            return None

    # distinct cocks today: the first sighting of each cocked_at carries its level
    cocks: dict[str, Optional[float]] = {}
    for _, lr in hist:
        ca = lr.get("cocked_at")
        if ca and ca not in cocks:
            cocks[ca] = lr.get("cock_level")

    def _count_at(level) -> int:
        b = _bucket(level)
        return (sum(1 for lv in cocks.values() if _bucket(lv) == b)
                if b is not None else 0)

    def _base_trigger(direction, level) -> bool:
        # last vol_bar decisively closes beyond the level in the break direction
        # (the same bar rules level_reclaim uses; NO volume requirement — the
        # old volume-pop lives on separately as vol_pop_fired)
        if not last_bars or level is None or direction not in ("call", "put"):
            return False
        b = last_bars[-1]
        if direction == "call":
            return (b.get("close", 0) > level and b.get("close", 0) >= b.get("open", 0))
        return (b.get("close", 0) < level and b.get("close", 0) <= b.get("open", 0))

    def _fire_gates(direction: str, base_trigger: bool) -> dict:
        """The four-gate snapshot (recorded while cocked/fired so the diary
        shows exactly what each cock is waiting on)."""
        # GRAVITY gates: the regime stack's verdict + still on the storm side
        trending = regime == "trending"
        storm_side = gamma_sign == "negative"
        # ONE-WAY TAPE (FLOW) — THE ONLY FAIL-CLOSED GATE anywhere: key absent
        # (stale/missing fold), tilt or determinate_share None/MALFORMED, or a
        # purge/calendar veto → tape_ok False → no gated fire. +tilt = bullish.
        # Values are coerced defensively: a malformed fold (the sensor's first
        # live day is 07-06) must fail CLOSED right here, not raise into the
        # outer fail-open and take the whole break block down with it.
        lf = lob_flow or {}
        try:
            tilt = float(lf["tilt"]) if lf.get("tilt") is not None else None
            det = (float(lf["determinate_share"])
                   if lf.get("determinate_share") is not None else None)
        except (TypeError, ValueError, KeyError):
            tilt = det = None
        tape_one_way = (tilt * det) if (tilt is not None and det is not None) else None
        tape_ok = bool(
            tape_one_way is not None
            and not lf.get("purge_flag") and not lf.get("calendar_blocked")
            and (tape_one_way > 0 if direction == "call" else tape_one_way < 0)
            and abs(tape_one_way) >= BREAK_TAPE_MIN)
        # RUNWAY — room to the next shelf in the break direction (GRAVITY again)
        shelf_pair = shelf_up if direction == "call" else shelf_dn
        shelf_v, shelf_kind = shelf_pair if shelf_pair else (None, None)
        runway_sigma = (round(abs(spot - shelf_v) / sigma, 3)
                        if (shelf_v is not None and spot and sigma) else None)
        # SIGNED twin (audit, record-only): + = the shelf still sits AHEAD of
        # spot in the break direction, − = spot has already blown past it. A
        # wall-anchored cock's shelf IS the broken wall, so the |·| gate above
        # can read overshoot-behind as room-ahead — the sign keeps the true
        # geometry visible in the diary; runway_ok itself is unchanged
        # (re-anchoring to the NEXT structural level needs a sanctioned change).
        runway_signed_sigma = None
        if shelf_v is not None and spot and sigma:
            runway_signed_sigma = round(((shelf_v - spot) if direction == "call"
                                         else (spot - shelf_v)) / sigma, 3)
        runway_ok = bool(runway_sigma is not None
                         and runway_sigma >= BREAK_RUNWAY_MIN_SIGMA)
        # LATE-DAY TAILWIND (record-only): after ~15:15 ET the pin's grip is
        # drained — context for the nightly cards, not yet a gate relaxation.
        now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        late_day = (now_et.hour * 60 + now_et.minute) >= BREAK_LATE_DAY_ET_MIN
        # VANNA AFTERBURNER (INACTIVE HOOK): once VEX signs calibrate, down-
        # breaks ('put' cocks) earn fuller conviction via VANNA_AFTERBURNER_DOWN.
        # It must stay 1.0 and no gate may read vex/cex until then.
        return {"trending": trending, "storm_side": storm_side,
                "tape_one_way": round(tape_one_way, 4) if tape_one_way is not None else None,
                "tape_ok": tape_ok,
                # tape freshness audit: the fold wrapper's write clock (the fold
                # block itself carries no timestamp; folds may legally be up to
                # 600 s old) — so a gated fire's tape read can be aged post-hoc
                "tape_fold_ts": lob_fold_ts,
                "tape_fold_age_s": lob_fold_age_s,
                "runway_sigma": runway_sigma,
                "runway_signed_sigma": runway_signed_sigma,
                "runway_ok": runway_ok,
                "shelf": round(shelf_v, 2) if shelf_v is not None else None,
                "shelf_kind": shelf_kind,
                "base_trigger": bool(base_trigger),
                "late_day_tailwind": late_day}

    # carry-forward reads the latest row that actually CARRIES break state —
    # rows without a break_state key (a scan whose break block fail-opened, or
    # an old-schema row) are transparent, so one bad scan can never amnesia an
    # active cock into a fresh re-cock with a reset TTL and a duplicate
    # one-shot. The TTL check below still bounds any staleness this spans.
    prior = next((lr for _, lr in reversed(hist) if "break_state" in lr), {})

    if prior.get("break_state") == "cocked":
        # ---- ACTIVE COCK: carry the identity forward, then decock/expire/fire
        cocked_at = prior.get("cocked_at")
        direction = prior.get("cock_direction")
        cock_level = prior.get("cock_level")
        out.update({"cocked_at": cocked_at, "cock_level": cock_level,
                    "cock_level_kind": prior.get("cock_level_kind"),
                    "cock_direction": direction,
                    "cock_count_today": _count_at(cock_level)})
        age = None
        try:
            t0 = datetime.fromisoformat(cocked_at)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=ET)
            age = (now - t0).total_seconds() / 60.0
        except (ValueError, TypeError):
            pass
        out["cock_age_min"] = round(age, 2) if age is not None else None

        if gamma_sign == "positive":
            # DECOCK — the calm side of the flip is reclaimed; GRAVITY damps again
            out["break_state"] = "decocked"
        elif age is None or age > BREAK_COCK_EXPIRE_MIN:
            # EXPIRE — TTL spent (an unreadable cock clock also reads as spent)
            out["break_state"] = "expired"
        else:
            # HOLD by default — neutral regime and gamma 'unknown' both wait here
            out["break_state"] = "cocked"
            trig = _base_trigger(direction, cock_level)
            gates = _fire_gates(direction, trig)
            out["fire_gates"] = gates
            # ungated twin: base trigger alone, ONE SHOT per cock (the diary
            # remembers whether this cock already spent it)
            pre_spent = any(lr.get("cocked_at") == cocked_at and lr.get("fired_pre_gates")
                            for _, lr in hist)
            out["fired_pre_gates"] = bool(trig and not pre_spent)
            # gated fire: base trigger AND all four gates, once per cock —
            # firing consumes the cock, so the one-shot is structural
            if (trig and gates["trending"] and gates["storm_side"]
                    and gates["tape_ok"] and gates["runway_ok"]):
                out["break_state"] = "fired"
                out["fired"] = True
            if out["fired"] or out["fired_pre_gates"]:
                out["direction"] = direction   # trigger scans: the grader reads it
    else:
        # ---- NO ACTIVE COCK (idle / a terminal state last scan) -------------
        out["cock_count_today"] = len(cocks)   # no level anchor → total today
        if gamma_sign == "negative" and (rev or {}).get("armed"):
            # COCK — the storm-side rejection scene. Continuation = the opposite
            # of the fade the rejection armed (a rejected fade-long breaks DOWN).
            direction = "put" if rev.get("direction") == "call" else "call"
            # anchor: level_reclaim's resolved level when it found one, else the
            # ARMING WALL (put-side wall for a fade-long arm, call-side for a
            # fade-short) — the best-known wall pair supplied by the caller
            anchor, anchor_kind = lvl.get("level"), lvl.get("level_kind")
            if anchor is None:
                wall_pair = shelf_dn if rev.get("direction") == "call" else shelf_up
                if wall_pair:
                    anchor, anchor_kind = wall_pair
            if anchor is not None and _count_at(anchor) < BREAK_MAX_COCKS_PER_LEVEL:
                out.update({"break_state": "cocked",
                            "cocked_at": now.isoformat(),
                            "cock_level": round(float(anchor), 2),
                            "cock_level_kind": anchor_kind,
                            "cock_direction": direction,
                            "cock_count_today": _count_at(anchor) + 1,
                            "cock_age_min": 0.0})
                # NO fire on the cocking scan (either twin) — but the gates
                # snapshot still records what the fresh cock is waiting on
                out["fire_gates"] = _fire_gates(direction,
                                                _base_trigger(direction, out["cock_level"]))
    return out


# ===========================================================================
# I/O orchestration (best-effort; never raises)
# ===========================================================================
def _index_bar_proxy(ticker: str) -> str:
    return "SPY" if ticker in TICKERS else ticker


def evaluate(ticker: str, now: datetime | None = None) -> Optional[dict]:
    """SCAN — one full look at one ticker: gathers all inputs, runs both gravity
    engines, packs one diary row. Returns a record with the two signal dicts,
    the regime verdict, and a flat telemetry row — or None on any failure (the
    caller treats None as 'nothing to record this tick')."""
    now = now or datetime.now(tz=ET)
    try:
        import lefteye_fetcher as fetcher
        import lefteye_vwap as vwap
        import lefteye_gex_box as _gxb
    except Exception:
        return None
    try:
        spot = fetcher.live_spot(ticker)
        if not spot:
            return None

        # FLOW SENSOR + native gravity FIRST (one budgeted read): supplies the
        # gex_theta record below AND the live flow for the gravity engine's
        # slide-E cross-check. Best-effort: failure leaves flow=None.
        tv = None
        flow = None
        if THETA_SHADOW:
            try:
                import native_gex_feed as _tf
                # 15s budget: the chunked native fetch is ~15 API round-trips on a
                # cold pull (TTL-cached after — GexBox below reuses it for free).
                tv = _tf.read(ticker, spot, now, budget_s=15.0,
                              tape_prev=_TAPE_PREV.get(ticker))
                if tv:
                    flow = tv.get("aggressor_flow")
                    # Carry this scan's cumulative contract-flow snapshot forward so the NEXT
                    # scan can difference it. Only advance when the snapshot actually moved —
                    # the native chain is TTL-cached, and re-diffing an identical snapshot
                    # would manufacture a zero-delta "the wind is calm" reading out of nothing.
                    _q = (tv.get("gamma_tape") or {}).get("q_now")
                    _prev = _TAPE_PREV.get(ticker) or {}
                    if _q and _q != _prev.get("q"):
                        _TAPE_PREV[ticker] = {"ts": now, "q": _q}
            except Exception:
                tv = None

        # PIN-VETO TAPE (M5/M9 + L1): the flow the slide-E cross-check consumes.
        # Prefer the FRESH ~30s order-book tape over the whole-day options read —
        # read-only (no strike write) and self-gated on staleness/ticker inside
        # read_latest, so it can run BEFORE the gravity read without the write
        # handshake. `flow` (raw options aggressor) stays untouched for the
        # gex_theta A/B record below.
        lob_fold = None
        try:
            import lob_bridge as _lobb
            lob_fold = _lobb.read_latest(ticker, now)
        except Exception:
            lob_fold = None
        veto_flow, veto_src = _veto_flow(flow, lob_fold)
        # the tape conflict trips on different scales AND semantics per source — the
        # LOB tilt tops out ~0.26 and its sign carries no dealer-positioning info
        # (freight-train test, unsigned), while the options aggressor reaches 0.6+
        # and its + side is exactly the dealer-sign inversion (H5) — slide E must
        # be told which tape it is reading.
        flow_conflict = _gxb.FLOW_CONFLICT_LOB if veto_src == "lob" else _gxb.FLOW_CONFLICT
        flow_kind = "lob" if veto_src == "lob" else "options"

        # GRAVITY ENGINE — the ONLY gravity read (legacy oracle retired):
        # supplies the σ yardstick (ATM-IV), walls, flip, regime and the magnet.
        gxv = _gxb.GexBox(ticker, now=now, live_spot=spot, aggressor_flow=veto_flow,
                          flow_conflict=flow_conflict, flow_kind=flow_kind)
        if not gxv:
            return None
        _meta = gxv.get("meta") or {}
        _slides = gxv.get("slides") or {}
        _C = _slides.get("C_tenor") or {}
        _B = _slides.get("B_0dte") or {}
        sigma = _meta.get("sigma")
        # band bounds (H2 v2, 2026-07-12 — doctrine: 0DTE first, PLACED like a
        # wall): candidates in tenor order — today's 0DTE gamma wall (share-
        # guarded), the 0DTE OI/volume wall, then the 1-7DTE structural wall —
        # and operative_wall picks the FIRST one standing at a real wall's
        # distance (≥ the fire floor, ≤ 3σ reach). The old tenor-first read
        # aimed fades at 8000/7000-class crash strata σ-multiples out (fake
        # runway, dead clamp — the original H2 bug); a raw 0DTE-first flip
        # verified WORSE (the spot-hugging ATM artifact armed every scan and
        # zeroed every fire). The structural walls always ride beside in the
        # diary + watchtower payload as outer terrain.
        _cw_share, _pw_share = _B.get("call_wall_gamma_share"), _B.get("put_wall_gamma_share")
        def _sized(w, share):
            return w if (w is not None and (share is None or share >= WALL_MIN_SHARE)) else None
        call_wall_tenor, put_wall_tenor = _C.get("call_wall"), _C.get("put_wall")
        call_wall = operative_wall((_sized(_B.get("call_wall_gamma"), _cw_share),
                                    _B.get("call_wall"), call_wall_tenor),
                                   spot, sigma, "call")
        put_wall = operative_wall((_sized(_B.get("put_wall_gamma"), _pw_share),
                                   _B.get("put_wall"), put_wall_tenor),
                                  spot, sigma, "put")
        gflip = gxv.get("flip")
        gex_source = _meta.get("gex_source")
        # gamma read = sign of net gamma AT SPOT — but a flow-downgraded
        # (🟡 uncertain) read abstains from the regime vote (see gamma_vote_sign).
        g_sign = gamma_vote_sign(gxv)

        # prior session close/high/low from completed daily bars
        daily = fetcher.daily_bars(ticker, days=3) or []
        prior = daily[-1] if daily else {}
        prior_close = prior.get("close")
        prior_high, prior_low = prior.get("high"), prior.get("low")

        # index PRICE bars (own scale) for range + variance-ratio + reaction.
        # DEAD-TAPE GUARD: no RTH bars = the market is not actually trading
        # (full holiday the status gate doesn't know, or a dead feed) — recording
        # would fill the diary with frozen-quote junk rows (2026-07-03 incident:
        # 90 junk rows on the July-4-observed holiday; only Gate 4 stopped fires).
        idx_bars = fetcher.intraday_bars(ticker) or []
        if not idx_bars:
            return None
        closes = [b["close"] for b in idx_bars if b.get("close")]
        sess_hi = max((b["high"] for b in idx_bars), default=None)
        sess_lo = min((b["low"] for b in idx_bars), default=None)
        realized_range = (sess_hi - sess_lo) if (sess_hi and sess_lo) else None
        # Compare the session-so-far range to the expected move PRORATED to elapsed
        # session time, not to a full-day σ — otherwise the morning range always
        # reads "compressed" (a time-of-day bias that mislabels a volatile gap-day
        # morning as pinning, exactly when a fade gets run over). Abstain in the
        # first ~20 min when the sample is too thin to judge.
        now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        mins_elapsed = min(390.0, max(0.0, (now_et.hour * 60 + now_et.minute) - (9 * 60 + 30)))
        if realized_range and sigma and mins_elapsed >= 20:
            em_so_far = sigma * math.sqrt(mins_elapsed / 390.0)
            range_em = realized_range / em_so_far if em_so_far else None
        else:
            range_em = None
        vr = variance_ratio(closes)

        # Volume-bearing bars (SPY proxy) for the level-break volume + the
        # reversion reaction. SPY trades at ~1/10 the index, so its bar PRICES
        # must be rescaled into the index's own units before they can be compared
        # against index-scale levels/walls — otherwise a 736 SPY close would be
        # "below" a 7400 SPX level on every tick (a units bug). Volume is kept.
        proxy_bars = fetcher.intraday_bars(_index_bar_proxy(ticker)) or idx_bars
        spy_last = proxy_bars[-1].get("close") if proxy_bars else None
        scale = (spot / spy_last) if (spot and spy_last) else 1.0
        vol_bars = [{**b,
                     "open": (b.get("open") or 0) * scale,
                     "high": (b.get("high") or 0) * scale,
                     "low": (b.get("low") or 0) * scale,
                     "close": (b.get("close") or 0) * scale}
                    for b in proxy_bars]

        # VIX term structure
        vix = fetcher.live_spot("$VIX")
        vix3m = fetcher.live_spot("$VIX3M")
        vix_ts = (vix / vix3m) if (vix and vix3m) else None

        # Session VWAP from the index-scaled volume bars → distance from VWAP in σ
        # (the second reversion stretch leg, alongside the gap from prior close).
        vwap_series = vwap.compute_vwap_series(vol_bars) if vol_bars else None
        idx_vwap = vwap_series[-1] if vwap_series else None
        vwap_stretch = ((spot - idx_vwap) / sigma) if (idx_vwap and sigma) else None

        regime_v = classify_regime(g_sign, range_em, vr, vix_ts)
        regime = regime_v["regime"]

        rev = reversion_extreme(spot, prior_close, sigma, call_wall, put_wall,
                                regime, vol_bars, vwap_stretch=vwap_stretch,
                                gamma_flip=gflip,
                                pin_magnet=gxv.get("magnet"))   # coupling fix: the
                                # runway's witness is the fill-confirmed pin, not
                                # the flip Gate 2's gamma vote already leans on
        lvl = level_reclaim(spot, sigma, prior_close, prior_high, prior_low,
                            call_wall, put_wall, ROUND_STEP.get(ticker, 0.0), vol_bars)

        telemetry = {
            "ts": now.isoformat(), "ticker": ticker, "spot": spot,
            "gex_source": gex_source, "gamma_sign": g_sign,
            "gamma_flip": gflip,
            "call_wall": call_wall, "put_wall": put_wall, "sigma": sigma,
            # outer terrain (H2): the structural 1-7DTE band walls the near walls
            # replaced as the operative bound — recorded so nothing is silently
            # lost and the A/B can compare the two doctrines row by row
            "call_wall_tenor": call_wall_tenor, "put_wall_tenor": put_wall_tenor,
            "prior_close": prior_close,
            "range_em": round(range_em, 3) if range_em is not None else None,
            "variance_ratio": vr, "vix_ts": round(vix_ts, 3) if vix_ts else None,
            "regime": regime, "regime_conf": regime_v["confidence"],
            "regime_reads": regime_v["reads"],
            "reversion_extreme": {k: rev[k] for k in
                ("score", "confidence", "fired", "armed", "reaction", "direction",
                 "arm_reason", "gap_stretch", "vwap_stretch",
                 "wall_dist_call", "wall_dist_put",
                 # runway gate fields — without these the recorded telemetry can't show
                 # the before/after-runway split and the ULT runway columns stay empty.
                 "runway_sigma", "runway_ok", "magnet", "magnet_raw",
                 "magnet_out_of_band", "fired_pre_runway")},
            "level_reclaim": {k: lvl[k] for k in
                ("score", "confidence", "fired", "direction", "level",
                 "level_kind", "flips_regime")},
            "live": REVERSION_LIVE,
        }

        # Today's diary rows, read ONCE — the only inter-tick memory both the
        # motion diff and the Break Lens state machine derive from. Fail-open:
        # an unreadable diary is an empty history, never a dead scan.
        try:
            rows_today = _read_today_telemetry(now)
        except Exception:
            rows_today = []

        # Record the gravity engine's read (computed once above) — the proxy
        # gravity snapshot the report cards grade. Best-effort, isolated.
        try:
            if gxv:
                rec = {"magnet": gxv.get("magnet"), "flip": gxv.get("flip"),
                       "flip_band": gxv.get("flip_band"), "regime": gxv.get("regime"),
                       "regime_raw": gxv.get("regime_raw"), "sign_agrees": gxv.get("sign_agrees"),
                       "aggressor_flow": flow,           # raw whole-day options read (A/B continuity)
                       "veto_flow": veto_flow,           # what slide-E actually consumed
                       "veto_flow_source": veto_src,     # "lob" (fresh) | "options" (fallback) | "none"
                       "source": (gxv.get("meta") or {}).get("gex_source")}
                # DEAD-GUARD TELEMETRY (2026-07-13): slide E's own headroom, every scan.
                # `sign_agrees` alone cannot distinguish "the tape confirms the map" from
                # "the tape can never reach the trip level" — and for 671 rows it has been
                # the latter while reading like the former. lefteye_guard_audit turns these
                # three fields into a nightly DEAD verdict. A guard that cannot fire must be
                # loud, not silent.
                _E = (gxv.get("slides") or {}).get("E_sign") or {}
                rec["sign_headroom"] = _E.get("headroom")        # |stat| / trip level
                rec["sign_informative"] = _E.get("informative")  # got within 2× of tripping
                rec["sign_trip"] = _E.get("trip")                # which guard fired, if any
                rec["sign_flow_kind"] = flow_kind
                # H7 A/B: the regime is now TODAY'S book; the blended 0-7DTE read it replaced
                # rides beside it every scan. The disagreement rate between these two IS the
                # measurement of how much next week's book was distorting today's regime —
                # unmeasurable before, because only the blended read was ever computed.
                rec["regime_tenor"] = gxv.get("regime_tenor")    # the old blended read
                rec["regime_source"] = gxv.get("regime_source")  # "0dte" | "blended_fallback"
                rec["regime_basis"] = gxv.get("regime_basis")    # open_interest | volume
                rec["flip_tenor"] = gxv.get("flip_tenor")
                # shadow twin: today's book on the VOLUME basis. 0DTE OI is yesterday's, so
                # whether the regime should weight by OI or by live volume is still open (N9).
                # Recorded so it gets settled with data, not by argument.
                rec["regime_0dte_vol"] = gxv.get("regime_0dte_vol")
                rec["net_gex_0dte_vol"] = gxv.get("net_gex_0dte_vol")
                rec["net_gex"] = gxv.get("net_gex")              # 0DTE net GEX at spot
                rec["net_gex_tenor"] = gxv.get("net_gex_tenor")  # blended net GEX at spot
                rec["regime_disagrees"] = (
                    gxv.get("regime_raw") != gxv.get("regime_tenor")
                    if gxv.get("regime_raw") and gxv.get("regime_tenor") else None)
                # record-only: vanna/charm dealer-flow exposures for the paper A/B
                # (CEX = 0DTE same-day charm drift; VEX = dated vanna). Logged, not acted on.
                rec["vex"] = gxv.get("vex"); rec["cex"] = gxv.get("cex")
                rec["vex_sign"] = gxv.get("vex_sign"); rec["cex_sign"] = gxv.get("cex_sign")
                _ff = (gxv.get("slides") or {}).get("F_flows") or {}
                rec["charm_wall"] = _ff.get("charm_wall")
                rec["vanna_wall"] = _ff.get("vanna_wall")
                # UW-style gamma walls (0DTE) recorded next to the legacy OI walls for the A/B
                _b0 = (gxv.get("slides") or {}).get("B_0dte") or {}
                rec["call_wall_gamma"] = _b0.get("call_wall_gamma")
                rec["put_wall_gamma"] = _b0.get("put_wall_gamma")
                # PIN STRENGTH + CONCENTRATION (shadow): raw magnet-field weight +
                # the magnet strike's share of it — judged in the nightly report card
                rec["pin_total_gamma"] = _b0.get("pin_total_gamma")
                rec["pin_top_share"] = _b0.get("pin_top_share")
                # MAGNET PROVENANCE (2026-07-06): which weighting the signed pin
                # used + the legacy unsigned pin beside it, so the report cards
                # can compare the two magnet definitions from the diary alone
                rec["pin_basis"] = _b0.get("pin_basis")
                rec["pin_abs"] = _b0.get("pin_abs")
                rec["pin_abs_basis"] = _b0.get("pin_abs_basis")
                # SIGNED PIN (Phase 1, shadow): the magnet when strikes are weighted by NET
                # aggressor flow |buy−sell| instead of gross volume — round-tripped strikes
                # net toward zero. Recorded beside the live pin so the A/B can score it.
                rec["pin_signed"] = _b0.get("pin_signed")
                rec["pin_signed_total"] = _b0.get("pin_signed_total")
                rec["pin_signed_share"] = _b0.get("pin_signed_share")
                rec["pin_signed_v"] = _b0.get("pin_signed_v")   # weighting era (2=churn-floored)
                # MAGNET ZONES (2026-07-09, LIVE): the attractive field read as
                # GROUPS — the live magnet is zone-1's weighted center; the
                # legacy argmax rides beside it so the nightly tape-measure can
                # A/B the two magnet definitions from the diary alone.
                rec["pin_zones"] = _b0.get("pin_zones")
                rec["pin_contested"] = _b0.get("pin_contested")
                rec["pin_argmax"] = _b0.get("pin_argmax")
                rec["pin_blended"] = _b0.get("pin_blended")   # H4: contested blend, when it drove the pin
                rec["zone1_share"] = _b0.get("zone1_share")
                # PIN FIELD (Break Lens motion inputs): wall SHARES (raw 0DTE
                # gamma is volume-weighted and only grows — never diff raw
                # mass), the gamma-weighted pin CENTROID (the argmax magnet
                # teleports 30-65 pts), and the soft-side mass split. None
                # until the gravity engine ships them — the keys still ride.
                rec["call_wall_gamma_share"] = _b0.get("call_wall_gamma_share")
                rec["put_wall_gamma_share"] = _b0.get("put_wall_gamma_share")
                rec["pin_centroid"] = _b0.get("pin_centroid")
                rec["gamma_above_spot"] = _b0.get("gamma_above_spot")
                rec["gamma_below_spot"] = _b0.get("gamma_below_spot")
                # PER-STRIKE SIGNED FIELD (2026-07-10): compact [[strike, net], ...]
                # (calls +, puts −) so the tablet draws MEASURED gamma bars, not a
                # modeled Gaussian. None on pre-field rows → tablet falls back.
                rec["net_by_strike"] = _b0.get("net_by_strike")
                # PER-STRIKE TENOR FIELD (2026-07-13): the 1-7DTE terrain, same
                # compact shape — the tablet's hollow outline bars behind the
                # solid 0DTE bars (never blended into the pure 0DTE read)
                rec["net_by_strike_tenor"] = ((gxv.get("slides") or {}).get("C_tenor") or {}).get("net_by_strike")
                # SHOVE TEST: signed net-GEX margins at spot ± 0.5σ (distance
                # from flipping, not a binary) — computed by the gravity engine
                rec["shove"] = gxv.get("shove")
                new_pin = gxv.get("magnet")
                if new_pin is not None and sigma:
                    d = rev.get("direction")
                    if d == "call":
                        rec["runway_new"] = round((new_pin - spot) / sigma, 3)
                    elif d == "put":
                        rec["runway_new"] = round((spot - new_pin) / sigma, 3)
                telemetry["gex_views"] = rec
        except Exception:
            pass

        # SHADOW (GEX MOTION): the map read as FILM, not photo — a pure diff of
        # this scan's gravity read against a 4-30 min older diary row (wall
        # melt, magnet walk, flip creep, grip slope, soft side). Fail-open: any
        # error (including the engine not shipping gex_motion yet) leaves the
        # key absent and the scan untouched.
        try:
            telemetry["gex_motion"] = _gxb.gex_motion(rows_today, telemetry["gex_views"],
                                                      sigma=sigma, now=now)
        except Exception:
            pass

        # SHADOW (theta A/B): record the native gravity+flow read (fetched above)
        # beside the Schwab-proxy read for a paper hit/miss compare. Record-only and
        # time-boxed, so it can neither change what the lens fires on nor stall the
        # scan. aggressor_flow may be None until same-day ticks publish — that gap
        # is itself the latency signal.
        if tv:
            try:
                b0 = (tv.get("slides") or {}).get("B_0dte") or {}
                trec = {"magnet": tv.get("magnet"), "flip": tv.get("flip"),
                        "regime": tv.get("regime"), "regime_raw": tv.get("regime_raw"),
                        "sign_agrees": tv.get("sign_agrees"),
                        "sign_confidence": tv.get("sign_confidence"),
                        "vex": tv.get("vex"), "cex": tv.get("cex"),
                        "vex_sign": tv.get("vex_sign"), "cex_sign": tv.get("cex_sign"),
                        "call_wall_gamma": b0.get("call_wall_gamma"),
                        "put_wall_gamma": b0.get("put_wall_gamma"),
                        "pin_total_gamma": b0.get("pin_total_gamma"),
                        "pin_top_share": b0.get("pin_top_share"),
                        "pin_basis": b0.get("pin_basis"),
                        "pin_abs": b0.get("pin_abs"),
                        "pin_abs_basis": b0.get("pin_abs_basis"),
                        # signed pin (Phase 1, shadow) — native mirror of the |buy−sell| magnet
                        "pin_signed": b0.get("pin_signed"),
                        "pin_signed_total": b0.get("pin_signed_total"),
                        "pin_signed_share": b0.get("pin_signed_share"),
                        "pin_signed_v": b0.get("pin_signed_v"),
                        # magnet zones (2026-07-09, LIVE) — native mirror
                        "pin_zones": b0.get("pin_zones"),
                        "pin_contested": b0.get("pin_contested"),
                        "pin_argmax": b0.get("pin_argmax"),
                        "pin_blended": b0.get("pin_blended"),   # H4 blend — native mirror
                        "zone1_share": b0.get("zone1_share"),
                        # pin-field mirror (native gets the same B_0dte fields
                        # free via build_views; shove/motion stay gex_views-only)
                        "call_wall_gamma_share": b0.get("call_wall_gamma_share"),
                        "put_wall_gamma_share": b0.get("put_wall_gamma_share"),
                        "pin_centroid": b0.get("pin_centroid"),
                        "gamma_above_spot": b0.get("gamma_above_spot"),
                        "gamma_below_spot": b0.get("gamma_below_spot"),
                        "net_by_strike": b0.get("net_by_strike"),   # measured per-strike field (native mirror)
                        "net_by_strike_tenor": ((tv.get("slides") or {}).get("C_tenor") or {}).get("net_by_strike"),
                        "aggressor_flow": flow, "flow_available": flow is not None,
                        "source": "theta_native"}
                # GAMMA-OWNERSHIP TAPE (2026-07-13) — RECORDED, CONSUMED BY NOTHING.
                # Recorded on the NATIVE leg only: the native chain (and so the per-strike
                # contract-flow annotation) refreshes on a 240s TTL, while GexBox may hold a
                # cached chain for up to 25 min — a 25-minute-old tape is not a tape. The
                # gex_views copy exists for shape parity and is explicitly not the record.
                _t = tv.get("gamma_tape") or {}
                trec.update({
                    "gamma_tape": _t.get("gamma_tape"),                # cumulative level
                    "gamma_tape_spot": _t.get("gamma_tape_spot"),      # ±0.5σ band
                    "gamma_tape_fixed": _t.get("gamma_tape_fixed"),    # reprice-free control
                    "gamma_tape_60m": _t.get("gamma_tape_60m"),        # THE WIND-CHANGE SIGNAL
                    "gamma_tape_dollar": _t.get("gamma_tape_dollar"),  # A/B control (WRONG weighting)
                    "tape_coverage": _t.get("coverage"),
                    "tape_flow_share": _t.get("flow_share"),
                    "tape_gross_contracts": _t.get("gross_contracts"),
                    "tape_interval_contracts": _t.get("interval_contracts"),
                    "tape_interval_min": _t.get("interval_min"),
                    "tape_n_strikes": _t.get("n_strikes"),
                    "dgamma_by_strike": _t.get("dgamma_by_strike"),    # flow-side twin of net_by_strike
                    "gamma_tape_v": _t.get("gamma_tape_v"),
                })
                pin = tv.get("magnet")
                if pin is not None and sigma:
                    d = rev.get("direction")
                    if d == "call":
                        trec["runway_new"] = round((pin - spot) / sigma, 3)
                    elif d == "put":
                        trec["runway_new"] = round((spot - pin) / sigma, 3)
                leg = telemetry.get("gex_views") or {}       # A/B vs the Schwab-proxy read
                if leg.get("magnet") is not None and pin is not None:
                    trec["magnet_vs_proxy"] = round(pin - leg["magnet"], 3)
                # engine-sign agreement compares the RAW gamma signs — a flow
                # downgrade to "uncertain" is not a sign dispute, and neither is
                # an H3 TIE: a knife-edge book reading 1.9% net/gross on one
                # engine and 2.1% on the other is two engines agreeing the book
                # is balanced, not a dispute — a tie on either leg abstains.
                leg_raw = leg.get("regime_raw") or leg.get("regime")
                t_raw = trec.get("regime_raw") or trec.get("regime")
                if "uncertain" in (leg_raw, t_raw):
                    trec["regime_agree"] = None
                else:
                    trec["regime_agree"] = (leg_raw == t_raw) if leg_raw else None
                telemetry["gex_theta"] = trec
            except Exception:
                pass

        # SHADOW (SPEEDOMETER): RV nowcast from today's 1-min index bars vs the
        # trailing same-time-of-day norm (the saved EOD bars files) — how much
        # of a normal day's variance the tape has actually spent, jump + sign,
        # and downside-variance dominance. Pure math, read-only. Fail-open:
        # any error leaves the key absent and the scan untouched.
        try:
            import lefteye_speedometer as _spd
            _spd_rec = _spd.read(idx_bars, now=now, ticker=ticker)
            if _spd_rec is not None:
                telemetry["speedometer"] = _spd_rec
        except Exception:
            pass

        # SHADOW (GAMMA ROLL): cross-expiry wall attribution from the UW mark's
        # v2 buckets (today / this week / next six weeks) — speaks only when
        # the 0DTE wall and the 8-45DTE wall point at different levels (the pin
        # may jump once today's options bleed off). Read-only peek at the
        # existing mark; one feed one vote (source tag "uw_periscope").
        # Fail-open.
        try:
            import lefteye_gamma_roll as _groll
            _gr_rec = _groll.read(_groll.load_mark(), spot, now)
            if _gr_rec is not None:
                telemetry["gamma_roll"] = _gr_rec
        except Exception:
            pass

        # SHADOW (DATED BOOK): far-dated structural OI walls — the next two SPX
        # monthlies + the quarter-end — from the nightly sidecar's stored book
        # (dated_gex_feed, own launchd job). Cache-ONLY file read, never a fetch
        # in the tick; unsigned location + magnitude only; key absent when the
        # store is missing, disabled, or staler than the floor. Fail-open.
        try:
            import dated_gex_feed as _dgx
            _dg_rec = _dgx.diary_summary(now)
            if _dg_rec is not None:
                telemetry["dated_gex"] = _dg_rec
        except Exception:
            pass

        # SHADOW (RANGE RULER): the day's expected-move budget from a live,
        # de-biased 0DTE ATM straddle re-quote (cache-ONLY chain peek — never a
        # fetch in the tick) + how much of the open anchor is already spent +
        # the VIX carry frame. Sole owner of expected-move units in the stack.
        # Fail-open.
        try:
            import lefteye_range_ruler as _rr
            import native_gex_feed as _nf2
            _ch = _nf2.cached_chain(ticker)
            _day_open = idx_bars[0].get("open") if idx_bars else None
            # Event kinds via the Event Lens's cached READ-ONLY calendar loader —
            # never FileCalendar here (its __init__ can WRITE a missing calendar
            # file, and a write has no place inside the tick). OPEX / NFP are not
            # in the JSON (rule-computed elsewhere), so the two 1-line rules ride
            # along, commented.
            try:
                import lefteye_event_lens as _evl0
                _today = now.astimezone(ET).date()
                _kinds = [e.get("kind") for e in _evl0.load_calendar()
                          if e.get("date") == _today.isoformat() and e.get("kind")]
                if _today.weekday() == 4 and 15 <= _today.day <= 21:
                    _kinds.append("OPEX")     # monthly OpEx = third Friday
                if _today.weekday() == 4 and _today.day <= 7:
                    _kinds.append("NFP")      # jobs Friday = first Friday
                _kinds = _kinds or None
            except Exception:
                _kinds = None
            _rr_rec = _rr.read(
                (_ch or {}).get("contracts") or [], spot, now,
                em_open=_rr.em_open_from_rows(rows_today),
                minutes_to_close=_gxb._minutes_to_close(now),
                day_open=_day_open, day_high=sess_hi, day_low=sess_lo,
                vix=vix, vix3m=vix3m, event_kinds=_kinds)
            if _rr_rec is not None:      # same absent-key convention as siblings
                telemetry["range_ruler"] = _rr_rec
        except Exception:
            pass

        # SHADOW (EVENT LENS): scheduled-event day read (CPI/FOMC/NFP) — the
        # budget priced just before the print vs the realized move just after
        # (surprise ratio >1 = continuation risk, <1 = crush-and-fade). Rides
        # the lob-flow calendar file read-only and consumes the Range Ruler's
        # same-scan read. Non-event days leave the key absent by design.
        # Fail-open.
        try:
            import lefteye_event_lens as _evl
            _ev_rec = _evl.read(now, spot, prior_close, idx_bars,
                                range_ruler_now=telemetry.get("range_ruler"),
                                todays_rows=rows_today)
            if _ev_rec is not None:
                telemetry["event_lens"] = _ev_rec
        except Exception:
            pass

        # SHADOW (Layer 2 — LOB FLOW): hand the gravity strikes down to the
        # collector and attach its latest fold ("lob_flow" + "spy_depth" keys).
        # File-only (never a network call in the tick) and best-effort — any
        # failure simply leaves the keys absent for this scan.
        try:
            import lob_bridge as _lob
            # walls for the collector's defense test: 0DTE GAMMA walls first (the
            # strikes MMs actually defend intraday), tenor OI band walls only as
            # fallback — the 8000/7000 band walls carry no tape and burned ~60% of
            # defense slots on permanently-thin strikes (2026-07-10 audit).
            # DELIBERATELY the RAW gamma wall, not H2's operative_wall: the
            # collector watches the order book AT the defended strike, and the
            # spot-hugging ATM strike is exactly where that defense lives — the
            # placement floor that protects the fade would starve the sensor.
            _lob_cw = _B.get("call_wall_gamma") if _B.get("call_wall_gamma") is not None else call_wall
            _lob_pw = _B.get("put_wall_gamma") if _B.get("put_wall_gamma") is not None else put_wall
            _lob.attach_telemetry(telemetry, ticker, now,
                                  magnet=gxv.get("magnet"), call_wall=_lob_cw,
                                  put_wall=_lob_pw, gamma_flip=gflip, sigma=sigma)
        except Exception:
            pass

        # SHADOW (BREAK LENS — offense): the two-stage cock→fire machine rides
        # the level_reclaim key. Placed AFTER the lob attach so the tape gate
        # sees THIS scan's flow fold, BEFORE the Watchtower so Head B sees the
        # verdict. Fail-open: on any error the block stays exactly as
        # level_reclaim() returned it (the Fade Lens is never contaminated).
        try:
            _call_gamma_wall = _B.get("call_wall_gamma")
            _put_gamma_wall = _B.get("put_wall_gamma")
            # best-known wall each side: 0DTE gamma wall (GRAVITY, live clock)
            # preferred, OI band wall fallback — (level, kind) pairs
            shelf_up = ((_call_gamma_wall, "call_wall_gamma") if _call_gamma_wall is not None else
                        (call_wall, "call_wall") if call_wall is not None else None)
            shelf_dn = ((_put_gamma_wall, "put_wall_gamma") if _put_gamma_wall is not None else
                        (put_wall, "put_wall") if put_wall is not None else None)
            # tape freshness audit: the fold's write clock, read through the
            # bridge's own staleness gate (the attached lob_flow block carries
            # no timestamp of its own). Only read when a fold actually attached
            # THIS scan — recording a clock for a fold the tape gate never saw
            # (e.g. a ticker-mismatched wrapper) would mislabel the audit.
            # Best-effort — None when unreadable.
            _fold_ts = _fold_age = None
            if telemetry.get("lob_flow"):
                try:
                    import lob_bridge as _lob_bridge
                    _fold_wrap = _lob_bridge._state().read_latest(
                        now, _lob_bridge._lob().LobConfig().latest_max_age_s)
                    if _fold_wrap:
                        _fold_ts = _fold_wrap.get("written_at")
                        _fold_age = _fold_wrap.get("age_s")
                except Exception:
                    _fold_ts = _fold_age = None
            bl = break_lens_state(rows_today, now=now, spot=spot, sigma=sigma,
                                  gamma_sign=g_sign, regime=regime, rev=rev, lvl=lvl,
                                  lob_flow=telemetry.get("lob_flow"),
                                  shelf_up=shelf_up, shelf_dn=shelf_dn,
                                  last_bars=vol_bars,
                                  lob_fold_ts=_fold_ts, lob_fold_age_s=_fold_age)
            lvl.update(bl)
            telemetry["level_reclaim"].update(bl)
        except Exception:
            # FAIL-OPEN, with ONE normalized key: the legacy dict left behind
            # carries `fired` = the OLD 1.3× volume-pop verdict, which would
            # grade as a gated Break fire (the ledger live privileges ride).
            # Keep the legacy verdict under vol_pop_fired and hold `fired` to
            # its locked meaning (gated fire only) even on failure scans; the
            # Fade Lens (reversion_extreme) is untouched either way.
            _lr = telemetry.get("level_reclaim")
            if isinstance(_lr, dict):
                _lr.setdefault("vol_pop_fired", bool(_lr.get("fired")))
                _lr["fired"] = False

        # SHADOW (HEAD B — THE WATCHTOWER): the reasoning head judges the same
        # scene the gates just judged and its verdict rides the diary row.
        # Runs LAST so it can see everything above (gravity, flow, LOB). It has
        # no path to the phone and fail-opens on any error — see watchtower.py.
        try:
            import watchtower as _wt
            wt_rec = _wt.observe(telemetry, now=now)
            if wt_rec:
                telemetry["watchtower"] = wt_rec
        except Exception:
            pass

        signals = [{"name": rev["name"], "ticker": ticker, "score": rev["score"],
                    "confidence": rev["confidence"], "fired": rev["fired"],
                    "notes": rev["notes"], "ts": now.isoformat()},
                   {"name": lvl["name"], "ticker": ticker, "score": lvl["score"],
                    "confidence": lvl["confidence"], "fired": lvl["fired"],
                    "notes": lvl["notes"], "ts": now.isoformat()}]
        return {"signals": signals, "regime": regime_v, "telemetry": telemetry}
    except Exception:
        return None


def record(telemetry: dict, now: datetime | None = None) -> None:
    """DIARY WRITER — appends the scan's row to today's diary file, the permanent
    evidence stream (state/reversion/{date}.jsonl, best-effort). This is the
    learning-capture stream the self-tuner will reconcile."""
    if not telemetry:
        return
    now = now or datetime.now(tz=ET)
    try:
        import json
        d = SKILL_DIR.parent.parent / "state" / "reversion"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{now.date().isoformat()}.jsonl", "a") as f:
            f.write(json.dumps(telemetry, default=str) + "\n")
    except Exception:
        pass


# ===========================================================================
# Canonical DEALER MAP — ONE resolved gravity read that every surface renders
# ===========================================================================
# Vocabulary: GRAVITY = dealer positioning (where price gets pulled); FLOW =
# the live force (who is shoving right now, the native aggressor sensor).
# The same gravity fact (regime / flip / magnet / walls) is computed twice per
# row: gex_views (proxy gravity) and gex_theta (native gravity+flow). The
# "legacy"-labelled fallbacks below read the FLAT telemetry keys (gamma_sign /
# gamma_flip / walls) — since the 2026-07-03 restructure those are themselves
# engine-derived, so the fallback is a same-engine redundancy, not a third
# engine. Resolving the winner HERE — at the staging layer — keeps the tablet
# and the markdown from narrating two different dealer stories off one row.
#
# Policy (locked until the nightly polarity/magnet A/B says otherwise):
# gex_views LEADS · gex_theta is the challenger surfaced as agreement badges
# (it has days of history, not weeks) · legacy fields only fill gaps.

def resolve_dealer_map(latest: Optional[dict]) -> Optional[dict]:
    """DEALER MAP (the ONE STORY) — collapses one telemetry row's competing GEX
    reads into a single source-labelled truth every surface renders identically.
    Pure + read-only; shadow like its inputs."""
    if not latest:
        return None
    gv = latest.get("gex_views") or {}
    th = latest.get("gex_theta") or {}

    regime = gv.get("regime")
    regime_source = "gex_views" if regime else None
    if regime is None:                       # engine had no basis → legacy sign
        regime = {"positive": "long_gamma",
                  "negative": "short_gamma"}.get(latest.get("gamma_sign"))
        regime_source = "legacy" if regime else None

    flip = gv.get("flip")
    flip_source = "gex_views" if flip is not None else None
    if flip is None:
        flip = latest.get("gamma_flip")
        flip_source = "legacy" if flip is not None else None

    # magnet fallback order keeps the TRUE-pull concept: gex_views (leader) →
    # gex_theta (challenger, same raw-pin concept) → the reversion lens's
    # PRE-CLAMP pin (magnet_raw — the same gv pin it was derived from). Never the
    # clamped revert-target: that is a fade context read, and rendering it here
    # would draw a wall as dealer gravity on the tablet/markdown.
    magnet = gv.get("magnet")
    magnet_source = "gex_views" if magnet is not None else None
    if magnet is None:
        magnet = th.get("magnet")
        magnet_source = "gex_theta" if magnet is not None else None
    if magnet is None:
        _rev = latest.get("reversion_extreme") or {}
        magnet = _rev.get("magnet_raw")
        magnet_source = "reversion_raw" if magnet is not None else None

    native = None
    if th:
        native = {"regime": th.get("regime"),
                  "regime_agree": th.get("regime_agree"),
                  "magnet": th.get("magnet"),
                  "magnet_delta": th.get("magnet_vs_proxy"),
                  "flow": th.get("aggressor_flow"),
                  "sign_agrees": th.get("sign_agrees"),
                  "sign_confidence": th.get("sign_confidence")}

    return {
        "regime": regime, "regime_source": regime_source,
        "flip": flip, "flip_source": flip_source,
        "flip_band": gv.get("flip_band"),
        "magnet": magnet, "magnet_source": magnet_source,
        # band bounds are the H2 operative walls (0DTE-first, placement-guarded)
        # since 2026-07-12; the structural tenor pair + the raw 0DTE gamma walls
        # ride alongside, labelled as such.
        "call_wall": latest.get("call_wall"), "put_wall": latest.get("put_wall"),
        "call_wall_tenor": latest.get("call_wall_tenor"),
        "put_wall_tenor": latest.get("put_wall_tenor"),
        "call_wall_gamma": gv.get("call_wall_gamma"),
        "put_wall_gamma": gv.get("put_wall_gamma"),
        "vex_sign": gv.get("vex_sign"), "cex_sign": gv.get("cex_sign"),
        "charm_wall": gv.get("charm_wall"), "vanna_wall": gv.get("vanna_wall"),
        # CONCENTRATION: the magnet strike's share of the pull field —
        # intraday-valid (STRENGTH needs the trailing baseline → nightly card)
        "pin_top_share": gv.get("pin_top_share"),
        "source": gv.get("source") or latest.get("gex_source"),
        "native": native,
    }


# ===========================================================================
# Learning dashboard — the human-facing "ULT SPX Learning" note
# ===========================================================================
# Renders today's telemetry into a clean, scannable Obsidian note so the regime
# reads + would-fire candidates are easy to eyeball and learn from. Purely
# informational (the lens is in shadow/beta — it is not trading yet).

_LEARNING_FILE = "Mirai Awakening — ULT SPX Learning.md"
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _read_today_telemetry(now: datetime) -> list[dict]:
    import json
    path = SKILL_DIR.parent.parent / "state" / "reversion" / f"{now.date().isoformat()}.jsonl"
    rows: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # one torn line (crash/disk-full mid-append) must not
                           # hide every row after it — the break machine derives
                           # its memory from these rows and would replay all day
    except OSError:
        pass
    return rows


def _regime_badge(regime: str) -> str:
    return {"pinning": "🟢 PINNING", "trending": "🔴 TRENDING"}.get(regime, "⚪ NEUTRAL")


def _wall_bar(spot, put_wall, call_wall, width: int = 25) -> str:
    """ASCII position bar: ● marks where spot sits between the put and call walls."""
    if not (spot and put_wall and call_wall and call_wall > put_wall):
        return ""
    frac = max(0.0, min(1.0, (spot - put_wall) / (call_wall - put_wall)))
    pos = int(round(frac * (width - 1)))
    bar = "".join("●" if i == pos else "─" for i in range(width))
    return f"`{put_wall:.0f} ┤{bar}├ {call_wall:.0f}`  ·  spot {spot:.0f}"


def _aggregate(rows: list[dict], ticker: str) -> dict:
    recs = [r for r in rows if r.get("ticker") == ticker]
    reg = [r.get("regime") for r in recs]
    rev = [r.get("reversion_extreme", {}) for r in recs]
    lvl = [r.get("level_reclaim", {}) for r in recs]
    return {
        "scans": len(recs),
        "pin": reg.count("pinning"), "trend": reg.count("trending"),
        "neutral": reg.count("neutral"),
        "armed": sum(1 for x in rev if x.get("armed")),
        "would_fire_old": sum(1 for x in rev if x.get("fired_pre_runway")),
        "would_fire": sum(1 for x in rev if x.get("fired")),          # runway-gated
        "no_runway": sum(1 for x in rev if x.get("fired_pre_runway") and not x.get("fired")),
        "level_breaks": sum(1 for x in lvl if x.get("fired")),
        "latest": recs[-1] if recs else None,
    }


def _ticker_panel(agg: dict, ticker: str) -> str:
    """The under-the-hood decision for one ticker RIGHT NOW, as a child-simple
    numbered trace: regime → stretch → runway → turn → decision."""
    latest = agg["latest"]
    if not latest:
        return f"### {ticker}\n\n_No readings recorded yet today._\n"
    rev = latest.get("reversion_extreme", {})
    badge = _regime_badge(latest.get("regime"))
    voted = latest.get("regime_reads", {})
    n_agree = max(sum(1 for v in voted.values() if v == "pin"),
                  sum(1 for v in voted.values() if v == "trend"))
    n_voted = sum(1 for v in voted.values() if v)
    bar = _wall_bar(latest.get("spot"), latest.get("put_wall"), latest.get("call_wall"))
    magnet = rev.get("magnet")

    armed = rev.get("armed")
    side = "LONG" if rev.get("direction") == "call" else "SHORT"
    reason = {"put_wall": "at the put wall", "call_wall": "at the call wall"}.get(
        rev.get("arm_reason"), "stretched far from fair value")

    # numbered decision trace (the "show your work")
    t = ["**Decision (step by step):**"]
    t.append(f"1. **Regime** → {badge.lower()}  _(gamma/VIX reads: {n_agree} of {n_voted} agree)_")
    if armed:
        t.append(f"2. **Stretch** → {side} setup, {reason}  _(gap {rev.get('gap_stretch')}σ)_")
        rw, ok = rev.get("runway_sigma"), rev.get("runway_ok")
        # magnet is a zone CENTER now (float, e.g. 7548.62) — round for humans
        _mtxt = f"{magnet:.0f}" if isinstance(magnet, (int, float)) else magnet
        room = (f"3. **Runway** → {rw}σ of room to the magnet ({_mtxt}) → "
                + ("🟢 enough room" if ok else f"🔴 NOT enough (need ≥{RUNWAY_MIN_SIGMA}σ)"))
        t.append(room)
        t.append(f"4. **Turn** → {'✅ last candle turned back' if rev.get('reaction') else '⏳ waiting for a turn'}")
        if rev.get("fired"):
            verdict = f"🎯 **FIRE — fade {side}** (a paper guess)"
        elif rev.get("fired_pre_runway"):
            verdict = "⏭️ **SKIP — no runway** (it *would* have fired before the runway gate)"
        else:
            verdict = "⏳ **WAIT — needs the turn to confirm**"
        t.append(f"→ {verdict}")
    else:
        t.append("2. **Stretch** → not far enough from fair value")
        t.append("3. **Runway** → —   ·   4. **Turn** → —")
        t.append("→ 💤 **STAND BY** (nothing stretched)")

    lines = [f"### {ticker}  ·  {badge}", ""]
    if bar:
        mline = f"  ·  magnet (pin) {magnet}" if magnet is not None else ""
        lines += [bar + mline, ""]
    lines += ["\n".join(t), ""]
    return "\n".join(lines)


def _prospect_status(latest: Optional[dict]) -> tuple[str, str, str]:
    """Compact (status, setup-side, stretch) for the at-a-glance picklist table.
    Mirrors the verdict logic in _ticker_panel exactly, so the glance table and
    the show-your-work trace below can never disagree."""
    if not latest:
        return "⚪ no data yet", "—", "—"
    rev = latest.get("reversion_extreme", {})
    if not rev.get("armed"):
        return "💤 stand by", "—", "—"
    side = "LONG" if rev.get("direction") == "call" else "SHORT"
    gap = rev.get("gap_stretch")
    stretch = f"{gap}σ" if gap is not None else "—"
    if rev.get("fired"):
        status = "🎯 FIRE (paper)"
    elif rev.get("fired_pre_runway"):
        status = "⏭️ skip — no runway"
    else:
        status = "⏳ wait — needs turn"
    return status, side, stretch


def _prospects_table(rows: list[dict]) -> str:
    """At-a-glance picklist at the TOP of the note: every prospect the lens is
    watching and exactly where each stands this moment — so you can eyeball
    'what's live' in one look without reading each trace below."""
    lines = [
        "## 📋 Picklist — prospects & status",
        "",
        "_The names on the watch and where each stands right now "
        "(🎯 fire · ⏳ wait · ⏭️ skip · 💤 stand by). Full “show your work” for each is below._",
        "",
        "| Prospect | Regime | Setup | Stretch | Status |",
        "|:--|:--|:-:|--:|:--|",
    ]
    for tk in TICKERS:
        latest = _aggregate(rows, tk)["latest"]
        badge = _regime_badge(latest.get("regime")) if latest else "—"
        status, side, stretch = _prospect_status(latest)
        lines.append(f"| **{tk}** | {badge} | {side} | {stretch} | {status} |")
    return "\n".join(lines)


_GEX_STATUS = {
    "positive": "🟢 LONG-gamma · pinning",
    "negative": "🔴 SHORT-gamma · trending",
    "long_gamma": "🟢 LONG-gamma · pinning",
    "short_gamma": "🔴 SHORT-gamma · trending",
    # two roads lead to "uncertain" since H3 — a flow downgrade of a real sign,
    # or a TIE (balanced book, no regime). The regime string alone can't tell
    # them apart, so the label stays neutral; _gex_status() disambiguates on
    # regime_raw when the row carries it.
    "uncertain": "🟡 UNCERTAIN · no trusted sign",
}
_GEX_MEANING = {
    "positive": "dealers **dampen** moves → fades favored",
    "negative": "dealers **amplify** moves → fades risky (go with the trend)",
    "long_gamma": "dealers **dampen** moves → fades favored",
    "short_gamma": "dealers **amplify** moves → fades risky (go with the trend)",
    "uncertain": "no trusted gamma sign (flow conflict, or a balanced book) → stand cautious",
}


def _gex_src_label(src) -> str:
    """Friendly provenance for the GEX read — so the heat map's trust is visible."""
    s = str(src or "")
    if s == "theta_native" or s.startswith("native"):
        return "live GEX"
    if s.startswith("spy_proxy"):
        return "SPY-OI proxy"
    return "model" if s == "oracle" else (s or "—")


def _native_badge(dm: Optional[dict]) -> str:
    """One-glance challenger check: does the native ThetaData read agree with
    the displayed (proxy-led) regime? Empty when no native record this tick."""
    nat = (dm or {}).get("native") or {}
    agree = nat.get("regime_agree")
    if agree is True:
        return " · native ✅ agrees"
    if agree is False:
        other = {"long_gamma": "long-γ", "short_gamma": "short-γ"}.get(
            nat.get("regime"), "?")
        return f" · native ⚠️ says {other}"
    return ""


def _gex_heatmap(latest: Optional[dict], width: int = 16) -> str:
    """A left→right dealer-gamma heat strip across the put-wall…call-wall band:
    🟩 long-gamma (above the flip, pinning) · 🟥 short-gamma (below the flip,
    trending) · 🟨 the gamma-flip cell · 🔵 = where spot sits now. Flip/regime
    come from the resolved dealer map (same read the tablet renders). Degrades
    to a flat sign-coloured band when no flip level is available this tick."""
    dm = resolve_dealer_map(latest) or {}
    spot, cw, pw = latest.get("spot"), dm.get("call_wall"), dm.get("put_wall")
    flip, regime = dm.get("flip"), dm.get("regime")
    if not (spot and cw and pw and cw > pw):
        return "`—  no wall structure this tick`"
    cells = []
    for i in range(width):
        p = pw + (cw - pw) * (i + 0.5) / width
        if flip is not None:
            cells.append("🟩" if p >= flip else "🟥")
        else:                                            # no flip → colour by regime
            cells.append("🟩" if regime == "long_gamma"
                         else "🟥" if regime == "short_gamma" else "🟨")
    if flip is not None and pw <= flip <= cw:            # mark the flip transition cell
        fi = min(width - 1, max(0, int((flip - pw) / (cw - pw) * width)))
        cells[fi] = "🟨"
    si = min(width - 1, max(0, int((spot - pw) / (cw - pw) * width)))
    cells[si] = "🔵"                                     # you-are-here
    return f"**{pw:.0f}** {''.join(cells)} **{cw:.0f}**"


def _gex_section(rows: list[dict]) -> str:
    """STORYTELLER (note) — renders the dealer map into the Obsidian note: one
    easy-to-read metric per index plus a dealer-gamma heat strip. Rebuilt from
    the latest telemetry every render, so it refreshes with every GEX pull."""
    lines = [
        "## 🔥 GEX read — dealer-gamma heat map",
        "",
        "_🔵 spot · 🟩 long-gamma (pinning) · 🟥 short-gamma (trending) · 🟨 gamma flip. "
        "Gravity = where dealers pull price; the native badge checks both engines agree._",
        "",
    ]
    for tk in TICKERS:
        latest = _aggregate(rows, tk)["latest"]
        if not latest:
            lines += [f"**{tk}** · ⚪ no GEX yet today", ""]
            continue
        dm = resolve_dealer_map(latest) or {}
        regime = dm.get("regime")
        status = _GEX_STATUS.get(regime, "⚪ neutral / unknown")
        flip, band = dm.get("flip"), dm.get("flip_band")
        flip_s = f" · flip {flip:.0f}" if flip is not None else ""
        if band and band[0] is not None and band[1] is not None:
            flip_s += f" (band {band[0]:.0f}–{band[1]:.0f})"
        magnet = dm.get("magnet")
        magnet_s = f" · magnet {magnet:.0f}" if magnet is not None else ""
        meaning = _GEX_MEANING.get(regime, "no clean dealer-gamma read")
        lines += [
            f"**{tk}** · {status} · _{_gex_src_label(dm.get('source'))}_"
            f"{flip_s}{magnet_s} · spot {latest.get('spot'):.0f}{_native_badge(dm)}",
            "",
            _gex_heatmap(latest),
            "",
            f"→ {meaning}",
            "",
        ]
    return "\n".join(lines).rstrip()


def _capture_table(rows: list[dict]) -> str:
    a = _aggregate(rows, "SPX")
    def row(name, k):
        return f"| {name} | {a[k]} |"
    return "\n".join([
        "| | SPX |", "|:--|:-:|",
        row("scans recorded", "scans"),
        f"| regime pin / trend / neutral | {a['pin']} / {a['trend']} / {a['neutral']} |",
        row("setups armed", "armed"),
        row("would-fire (before runway)", "would_fire_old"),
        row("🛣️ filtered — no runway", "no_runway"),
        row("✅ would-fire (after runway)", "would_fire"),
        row("level breaks flagged", "level_breaks"),
    ])


def _pipeline_diagram() -> str:
    return (
        "```\n"
        "  👀 LOOK     dealer walls + how jumpy the tape is   →  PINNING or TRENDING?\n"
        "     │\n"
        "  📏 STRETCH  how far is price from fair value?       →  too far = ARMED\n"
        "     │\n"
        "  🛣️ RUNWAY   is there ROOM to reach the target       →  yes = keep · no = SKIP   ⟵ NEW\n"
        "     │        before price hits the magnet (pin)?\n"
        "  ↩️ TURN     did the last candle start turning back?  →  yes = FIRE a paper guess\n"
        "     │\n"
        "  🎯 SCORE    hit +0.30σ target before −0.30σ stop,    →  🟢 win · 🔴 loss · ⚪ scratch\n"
        "     │        anytime today?\n"
        "  🎓 LEARN    tally by condition → nudge the dials → better guesses tomorrow\n"
        "```")


def _learning_loop_diagram() -> str:
    return (
        "```\n"
        "   paper guesses ──▶ scored win/loss/scratch ──▶ tally by regime + runway\n"
        "        ▲                                                    │\n"
        "        └──────  nudge the dials (runway, target)  ◀─────────┘\n"
        "   (today: dials are fixed; the auto-tuner is the next step)\n"
        "```")


def _learned_sentence(rows: list[dict], gated, pre_runway) -> str:
    """One plain-English line you can hand to Claude to review/adjust the dials."""
    a = _aggregate(rows, "SPX")
    armed = a["armed"]
    filtered = a["no_runway"]
    def wr(res):
        w = sum(1 for r in res if r["outcome"] == "win")
        d = w + sum(1 for r in res if r["outcome"] == "loss")
        return (w, d, (w / d * 100) if d else 0.0)
    gw, gd, gpct = wr(gated)
    pw, pd, ppct = wr(pre_runway)
    mfes = [r["mfe_sigma"] for r in (gated or pre_runway)]
    avg_mfe = (sum(mfes) / len(mfes)) if mfes else 0.0
    return (
        f"📝 **Learned today:** {armed} setups armed; the runway gate filtered **{filtered}** "
        f"with no room; scored guesses won **{gpct:.0f}%** ({gw}/{gd}) WITH the runway gate vs "
        f"**{ppct:.0f}%** ({pw}/{pd}) without it, and bounces averaged **{avg_mfe:+.2f}σ** "
        f"against the +{PAPER_TARGET_SIGMA}σ target — "
        f"{'the runway gate is helping; ' if gpct >= ppct else 'the runway gate did not help yet; '}"
        f"if the average bounce stays below the target, lower PAPER_TARGET_SIGMA or raise "
        f"RUNWAY_MIN_SIGMA. _(feed this line to Claude to adjust the dials.)_")


def _default_bars_lookup(ticker: str) -> list[dict]:
    """Today's 1-min index bars (own price; vol=0 but high/low/close valid).
    Best-effort — returns [] on any failure so resolution just degrades to pending."""
    try:
        import lefteye_fetcher
        return lefteye_fetcher.intraday_bars(ticker) or []
    except Exception:
        return []


def _fresh_would_fires(rows: list[dict], ticker: str, fire_key: str = "fired",
                       signal_key: str = "reversion_extreme"):
    """The fresh would-fire ENTRIES for one ticker from telemetry (RTH only): a new
    trigger = a fired tick whose direction differs from the prior tick (so a signal
    that stays armed for many ticks counts once). `fire_key` selects the gate —
    "fired" (runway-gated) or "fired_pre_runway" (old gate, for A/B comparison).
    `signal_key` selects WHOSE fires to read: "reversion_extreme" (Head A, the
    gates) or "watchtower" (Head B) — both store fired/direction the same way,
    so one resolver grades both brains on identical rules.
    Returns [(ts, t0, P, sigma, dir)]."""
    from datetime import time as _time
    fires = []
    prev_dir = None
    for r in sorted([x for x in rows if x.get("ticker") == ticker and x.get("ts")],
                    key=lambda x: x["ts"]):
        rev = r.get(signal_key) or {}
        d = rev.get("direction")
        if not rev.get(fire_key):
            # A row that says NOTHING about the thesis must not split it into
            # re-entries (07-09: two 90s timeouts + cap-skips turned ONE morning
            # put thesis into FOUR graded trades, -2.53r of correlated entries).
            # For Head B only a JUDGED stand-down (votes recorded, fired False)
            # releases the direction; skipped/held/cap/timeout rows leave the
            # held thesis intact. Head A rows are always full verdicts, so its
            # behavior is unchanged.
            if signal_key != "watchtower" or rev.get("votes"):
                prev_dir = None
            continue
        try:
            t0 = datetime.fromisoformat(r["ts"]).astimezone(ET)
        except (ValueError, TypeError):
            continue            # corrupt row: neither an entry nor a consumption
        if d == prev_dir:
            continue
        if not (_time(9, 45) <= t0.time() < _time(16, 0)):
            # Outside the entry window: do NOT consume the direction — a trigger
            # that lit up pre-window and stays lit becomes a valid FIRST entry at
            # its first in-window scan (fixes the early-fire under-count).
            continue
        P, sigma = r.get("spot"), r.get("sigma")
        if P and sigma:
            prev_dir = d        # consume only when an entry is actually taken
            fires.append((r["ts"], t0, P, sigma, d))
    return fires


def _resolve_beta_trades(rows: list[dict], bars_lookup=None,
                         target: float = PAPER_TARGET_SIGMA,
                         stop: float = PAPER_STOP_SIGMA,
                         max_hold: float = PAPER_MAX_HOLD_MIN,
                         fire_key: str = "fired",
                         signal_key: str = "reversion_extreme"):
    """Resolve each FRESH would-fire (SPX, RTH) by TARGET-BEFORE-STOP, checked
    against the index's 1-MINUTE BAR HIGHS/LOWS (not the 5-min telemetry snapshots),
    so an intra-interval spike through a line is caught. From the entry, did price
    TOUCH the favorable target (+target σ) before the adverse stop (−stop σ), at ANY
    time in the window — 5 min, 25 min or 2 h, it doesn't matter. Outcomes: 'win',
    'loss', 'scratch' (neither by the window's close), 'pending' (window still open /
    incomplete data). A single bar that touches BOTH lines is scored pessimistically
    as a loss. Returns (resolved, pending).

    THE OUTCOME VECTOR (2026-07-05): win/lose alone can't tell a $50 move from a
    $150 move — the exit rule clips every winner at the same +target line. So each
    trade also records, in σ units, over the FULL window (the walk keeps going
    even after the exit line is hit):
        r          — cookies won per cookie risked: +1 target, −1 stop,
                     fractional for a scratch (exit ÷ the stop distance)
        exit_sigma — where the trade actually got out, signed (+ favorable)
        mfe_sigma  — best run in the trade's favor (the clipped runner shows here)
        mae_sigma  — worst dip against it, signed ≤ 0 (how scary the ride was)
        move_30m/60m/120m — signed move in the predicted direction at fixed
                     minute marks (None if the session ended before the mark);
                     this is what grades a "direction + magnitude" prediction
    Shadow/paper only — reads telemetry + read-only bars, touches nothing live."""
    from datetime import time as _time
    bars_lookup = bars_lookup or _default_bars_lookup
    resolved: list[dict] = []
    pending = 0
    for tk in TICKERS:
        fires = _fresh_would_fires(rows, tk, fire_key=fire_key, signal_key=signal_key)
        if not fires:
            continue                                 # nothing to resolve → skip the bar fetch
        # build the 1-min (time, high, low, close) path for the day
        # (close may be None on synthetic bars — the walk falls back to hi/lo midpoint)
        path = []
        for b in bars_lookup(tk) or []:
            try:
                bt = datetime.fromisoformat(b["ts"]).astimezone(ET)
            except (ValueError, TypeError, KeyError):
                continue
            if b.get("high") is not None and b.get("low") is not None:
                path.append((bt, b["high"], b["low"], b.get("close")))
        path.sort(key=lambda x: x[0])
        for (ts, t0, P, sigma, d) in fires:
            is_long = d == "call"                    # call = fade long (want up), put = fade short
            tgt = P + target * sigma if is_long else P - target * sigma
            stp = P - stop * sigma if is_long else P + stop * sigma
            outcome, ttr, last_in_win = None, None, None
            mfe, mae = 0.0, 0.0                      # full-window excursions (σ, favorable axis)
            last_px = None                           # latest in-window close (scratch exit price)
            moves: dict[int, float] = {}             # signed move at the 30/60/120-min marks
            for (bt, hi, lo, cl) in path:
                if bt <= t0:
                    continue
                elapsed = (bt - t0).total_seconds() / 60
                if elapsed > max_hold or bt.time() >= _time(16, 0):
                    break                            # past the window — stop watching
                last_in_win = elapsed
                px = cl if cl is not None else (hi + lo) / 2.0
                last_px = px
                # excursions on the favorable axis: + = with the trade, − = against
                best = ((hi - P) if is_long else (P - lo)) / sigma
                worst = ((lo - P) if is_long else (P - hi)) / sigma
                if best > mfe:
                    mfe = best
                if worst < mae:
                    mae = worst
                for mark in (30, 60, 120):           # keep overwriting until the mark passes
                    if elapsed <= mark:
                        moves[mark] = ((px - P) if is_long else (P - px)) / sigma
                if outcome is not None:
                    continue                         # exit already known — walking for the vector only
                tgt_hit = (hi >= tgt) if is_long else (lo <= tgt)
                stp_hit = (lo <= stp) if is_long else (hi >= stp)
                if tgt_hit and stp_hit:              # ambiguous single bar → pessimistic
                    outcome, ttr = "loss", elapsed
                elif tgt_hit:
                    outcome, ttr = "win", elapsed
                elif stp_hit:
                    outcome, ttr = "loss", elapsed
            close = datetime.combine(t0.date(), _time(16, 0), tzinfo=ET)
            window_end = min(max_hold, (close - t0).total_seconds() / 60)
            if outcome is None:
                # Only call a SCRATCH if the 1-min bars actually reach near the window
                # close; otherwise the data is incomplete (day still running) → PENDING.
                if last_in_win is not None and last_in_win >= window_end - 15:
                    outcome = "scratch"
                else:
                    pending += 1
                    continue
            # r + exit: a win got out at +target, a loss at −stop, a scratch at the
            # last in-window close. r divides by the stop distance (the cookie risked).
            if outcome == "win":
                exit_sigma, hold = target, ttr
            elif outcome == "loss":
                exit_sigma, hold = -stop, ttr
            else:
                exit_sigma = (((last_px - P) if is_long else (P - last_px)) / sigma
                              if last_px is not None else 0.0)
                hold = last_in_win
            # a fixed-minute mark only counts if the walk actually reached it
            # (~6 min of tolerance for sparse test bars / feed gaps)
            for mark in (30, 60, 120):
                if last_in_win is None or last_in_win < mark - 6:
                    moves[mark] = None
            resolved.append({"ticker": tk, "ts": ts, "dir": d, "outcome": outcome,
                             "r": round(exit_sigma / stop, 2) if stop else None,
                             "exit_sigma": round(exit_sigma, 3),
                             "mfe_sigma": round(mfe, 3),
                             "mae_sigma": round(mae, 3),
                             "move_30m": round(moves[30], 3) if moves.get(30) is not None else None,
                             "move_60m": round(moves[60], 3) if moves.get(60) is not None else None,
                             "move_120m": round(moves[120], 3) if moves.get(120) is not None else None,
                             "ttr_min": round(ttr) if ttr is not None else None,
                             "hold_min": round(hold) if hold is not None else None})
    return resolved, pending


def _beta_performance_section(resolved: list[dict], pre_runway: list[dict],
                              pending: int) -> str:
    """Compact paper SUMMARY of the BETA LENS — the runway-gated would-fires resolved
    by target-before-stop, with the pre-runway set shown alongside for A/B. Shadow/
    paper: the lens places no real trades."""
    head = "## 🧪 Did the guesses work?  ·  _paper only — shadow mode_"
    if not resolved and not pre_runway:
        return (f"{head}\n\n_None resolved yet — {pending} still developing "
                f"(each watched until it hits a target/stop or the day ends)._")
    def wr(res):
        w = sum(1 for r in res if r["outcome"] == "win")
        d = w + sum(1 for r in res if r["outcome"] == "loss")
        return w, d, (w / d * 100) if d else 0.0
    _, _, pre_pct = wr(pre_runway)
    wins = [r for r in resolved if r["outcome"] == "win"]
    losses = [r for r in resolved if r["outcome"] == "loss"]
    scratch = [r for r in resolved if r["outcome"] == "scratch"]
    decided = len(wins) + len(losses)
    gated_pct = (len(wins) / decided * 100) if decided else 0.0
    ttrs = [r["ttr_min"] for r in wins if r["ttr_min"] is not None]
    avg_ttr = (sum(ttrs) / len(ttrs)) if ttrs else 0.0
    avg_mfe = (sum(r["mfe_sigma"] for r in resolved) / len(resolved)) if resolved else 0.0
    lines = [
        head, "",
        f"> **Win rate {gated_pct:.0f}%** (with runway)  ·  was **{pre_pct:.0f}%** before the "
        f"runway gate  ·  🟢 {len(wins)} win · 🔴 {len(losses)} loss · ⚪ {len(scratch)} scratch "
        f"· ⏳ {pending} pending",
        f"> _avg win in **{avg_ttr:.0f} min** · avg best move **{avg_mfe:+.2f}σ** vs the "
        f"+{PAPER_TARGET_SIGMA}σ target (stop −{PAPER_STOP_SIGMA}σ), anytime in the day_",
        "",
        "| time | ticker | fade | best | result | when |",
        "|:--|:--|:-:|--:|:-:|--:|",
    ]
    icon = {"win": "🟢 win", "loss": "🔴 loss", "scratch": "⚪ scratch"}
    for r in (resolved or pre_runway)[-6:][::-1]:
        side = "LONG" if r["dir"] == "call" else "SHORT"
        when = f"{r['ttr_min']}m" if r["ttr_min"] is not None else "—"
        lines.append(f"| {r['ts'][11:16]} | {r['ticker']} | {side} "
                     f"| {r['mfe_sigma']:+.2f}σ | {icon[r['outcome']]} | {when} |")
    return "\n".join(lines)


def render_learning_view(rows: list[dict], now: datetime) -> str:
    ctx = (f"_{_WEEKDAYS[now.weekday()]} {now.date().isoformat()} · "
           f"{now.strftime('%H:%M')} ET · 👀 watching &amp; learning — NOT trading yet_")
    gated, pending = _resolve_beta_trades(rows, fire_key="fired")
    pre_runway, _ = _resolve_beta_trades(rows, fire_key="fired_pre_runway")
    return "\n".join([
        "---\ncssclasses:\n  - mirai\n---", "",
        "# 🧠 Mirai Awakening — SPX \"Fade-the-Bounce\" Learner",
        "", ctx, "",
        "> [!info] In one line (for a 10-year-old)",
        "> When SPX gets yanked too far from the price dealers keep \"pinning\" it to, "
        "this lens bets it'll **snap back** — but only if there's **room** to snap back to. "
        "It isn't trading; it just makes guesses on paper and grades them to learn.",
        "", "---", "",
        _prospects_table(rows),
        "", "---", "",
        _gex_section(rows),
        "", "---", "",
        "## 🔧 How it decides — the pipeline", "",
        _pipeline_diagram(),
        "", "---", "",
        "## 🧭 Right now — show your work", "",
        _ticker_panel(_aggregate(rows, "SPX"), "SPX"),
        "---", "",
        "## 📊 Today by the numbers", "",
        _capture_table(rows),
        "", "---", "",
        _beta_performance_section(gated, pre_runway, pending),
        "", "---", "",
        "## 🎓 What we learned today", "",
        _learned_sentence(rows, gated, pre_runway),
        "", "---", "",
        "## 🔁 How it learns", "",
        _learning_loop_diagram(),
        "", "---",
        "_Beta · shadow mode · SPX only · resets at premarket · not financial advice._",
    ])


def write_learning_view(now: datetime | None = None) -> None:
    """Render today's learning note next to the main dashboard (best-effort)."""
    now = now or datetime.now(tz=ET)
    try:
        import dashboard
        target = dashboard._vault_file().parent / _LEARNING_FILE
        rows = _read_today_telemetry(now)
        target.write_text(render_learning_view(rows, now), encoding="utf-8")
    except Exception:
        pass
