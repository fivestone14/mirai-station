"""
watchtower.py — HEAD B: the reasoning head (shadow-only).

WHAT THIS IS, in plain english
------------------------------
Head A (the Fade Lens in reversion_lens.py) decides with four hard yes/no
gates. The Watchtower is a SECOND opinion: once a scan looks interesting, the
whole scene — dealer map, tape, flow, day context — is written out as numbers
and handed to a Claude model, which returns a reasoned verdict: fire or stand
down, which direction, how far it should run (magnitude + range), and why.

It is a judge, not a trader:
  * SHADOW ONLY — its verdict is stored on the diary row and graded nightly;
    it has NO path to the phone and can never place or veto a real trade.
  * FAIL-OPEN — any error, timeout, or weird reply becomes a small "error"
    record and the scan simply carries on. A slow model can't break a tick.
  * BLIND-THEN-REVEAL — the model judges the raw scene first WITHOUT seeing
    what the gates decided (research: showing a prior drags answers toward
    agreement; hiding is the only fix that works). Only when the two heads
    disagree is the gate verdict revealed for one confirm-or-revise pass.
  * VOTES, NOT VIBES — a fire-worthy scene is judged 3 times and majority
    wins; how much the votes agree is the conviction number we trust (a
    model's own stated 0-1 confidence is known to be overconfident).
  * HYBRID WAKE — it judges any interesting scan (gates/stretch/wall), and on
    an otherwise quiet tape it still looks once an HOUR (heartbeat) or early on
    a big scene SHIFT (interrupt), throttled by a min-gap + the daily cap.

WHAT IT WRITES (one dict per scan, stored at telemetry["watchtower"]):
  quiet scan  → {"skipped": "quiet", ...}                      (tiny row)
  judged scan → {"fired", "direction", "magnitude_sigma", "range_sigma",
                 "conviction" (= vote agreement), "conviction_stated",
                 "scene", "would_change_mind", "overrides", "votes",
                 "blind", "revised", "agrees_with_gates", "model",
                 "prompt_version", "wall_s", "interest", "snippet"}
  The "snippet" is the human-facing read (See / Read / NEXT) and "interest"
  says why the tower woke (gates / stretch / hourly heartbeat / shift · …).
The nightly report card (gex_polarity_ab._grade_watchtower_sweep) grades
"fired" + "direction" by the SIGNED AREA between the 1-min price path and the
entry on the called side (the Sweep Ledger, 2026-07-17 — the single scoreboard
that replaced the trade + scene ledgers).

PAYLOAD HYGIENE (why the payload looks the way it does)
  * No dates and no absolute index levels — models have SPX history memorized;
    everything is expressed as σ-distances from spot and minutes on the clock.
  * Every comparison is precomputed and every sign is spelled out in words —
    LLMs misread raw numbers ("9.11 > 9.9") more often than words.
  * Field order is frozen; the few numbers that matter most sit at the top.

Manual replay (offline, safe — no model calls unless --live):
    python3 watchtower.py --replay 2026-07-02          # print payloads
    python3 watchtower.py --replay 2026-07-02 --live   # actually ask the model
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time as _clock
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent
STATE_DIR = SKILL_DIR.parent.parent / "state"
CONFIG = SKILL_DIR.parent.parent / "runtime" / "watch" / "config" / "limits-and-cooldowns.json"

# --- knobs -------------------------------------------------------------------
PROMPT_VERSION = "wt-8"     # bump on ANY prompt change — a bump resets the record
# wt-8 CORRECTIONS (2026-07-26, PRE-FIRST-LIVE): a guarded-generality sweep landed on the
# wt-8 payload BEFORE its first judged live scan (first live is Monday), so there is NO
# ledger to reset — the era tag is DELIBERATELY kept "wt-8" (a bump would reset a clock that
# has never ticked). Guiding principle: the payload must be THOROUGH but generally CORRECT
# and UNBIASED — the tower reasons over it. Eight changes, each the guarded form: (1) the
# HOLD-YOUR-THESIS flip triggers drop "magnet crossed spot" (the magnet is demoted terrain,
# not a structure flip) — gamma-sign and hard-flow flips stay. (2) the whole-day
# tape_day_average is REMOVED — it cancels toward zero and cannot turn; the tape rule points
# only at tape_recent_window + book_tilt (the windowed twin inherits the same cancels-to-zero,
# so a TRUE both-wings/accelerant fix still needs an unsigned gross-gamma detector — not in
# scope). (3) points_per_sigma is emitted so the tower can convert σ↔points (rest_of_day_range
# / tape_speed are in points); the pace/range read stays CONVICTION-LOWERING, not a hard
# stand-down, and is EXEMPT under confirmed-negative gamma with a move underway (an ignition
# bar must never be suppressed by a pace that has not shown up yet). (4) 0DTE siege verdicts
# may reach the payload but ONLY behind a ROBUST effort baseline (cold today → dormant). (5)
# net_gex_binding: a NORMALIZED net/gross-at-spot share (sign-explicit: + PIN, − amplification)
# in its OWN field, suppressed when the gamma sign is unknown (the tie band already caps
# conviction). (6) the DEX flow-signed path gains a thin-tape abstain floor (in lefteye_dex)
# and surfaces its magnitude on its OWN uncalibrated scale; the naive above/below split is
# DEMOTED to standing-inventory geography, not deleted. (7) the shove "confirmed-move"
# side-door now requires an OBJECTIVE payload confirmation (a FIRED break / wall breach), not
# an LLM-defined "confirmed", and the accelerant wording is non-directional. (8) macro_mood is
# named in one prompt-head line so the overnight read is actually consulted (subordinate to
# live structure). Record-only stance unchanged; no field gates a trade.
# wt-8 (2026-07-25): REGIME AS PERMISSION — the 07-22/23 forensic verdict. Every 07-22
# loss was the same potential-energy put-lean (shove asymmetry + soft_side + magnet,
# no live force) fired under LONG gamma; the identical play won big under SHORT gamma
# on 07-23. The fire gates never fired once in 4 days — override was the tower's only
# voice. Four changes: (1) the gamma sign is now the PERMISSION SLIP, not just the
# default: POSITIVE gamma FORBIDS drift/momentum/continuation bets (allowed plays are
# fade at a range edge/wall, pin/settle, or stand down); NEGATIVE (confirmed) allows
# continuation — amplification is real; UNKNOWN stands down. This is both the external
# evidence (long gamma structurally damps momentum) and the system's own ledger.
# (2) flow_asymmetry → terrain_asymmetry in the payload (storage keys untouched): the
# shove test measures which side is CHEAPER to move IF pushed — resistance, NOT live
# pushing — and confirms nothing alone. (3) soft_side gains a stuck-vane guard: when
# it has read the same value on >90% of the last ~50 diary rows it is worded as a
# NEAR-CONSTANT terrain fact, not today's opinion (it read "down" on ~97% of rows for
# 3 straight days, 07-21→23). (4) the magnet is demoted from directional default to
# LOCATION/late-day pin target (intraday pull hit ~33% in backtest) — never a drift
# base case at any hour under any regime. Record-only stance unchanged; the era bump
# resets the Wilson clock via the sweep card's prompt_version keying in gex_polarity_ab.
# wt-7 (2026-07-18): THE SIEGE ERA — the SIEGE stack is un-blinded into the payload.
# (1) siege_effort_at_walls: underlying (SPY) volume EFFORT at dealer-gamma wall
# touches, worded with the REVERSED SIGN spelled out — HEAVY effort (SIEGE) at a
# touched wall predicts the wall BREAKS (sweep n=168: sieged walls held 39% vs 54%
# quiet); QUIET predicts HOLD. The intuitive "absorption = strength" read is the
# wrong sign on this tape and the wording forbids it. (2) Payload hygiene holds:
# the block attaches ONLY on a clean feed (health OK), an unsaturated session, and
# towers carrying an actual SIEGE/QUIET verdict — NEUTRAL rows and verdictless
# watches stay off the payload (absence ≠ zero). Baseline maturity (cold/warming)
# rides along as a confidence caveat until 21 prior days accrue. (3) The prompt
# teaches it as containment evidence about THAT WALL — never a standalone
# direction. First underlying-tape word in the tower's room. Payload change =
# era bump: wt-6 rows judged scenes without the siege word — never mix ledgers.
# wt-5 (2026-07-12): THE TOWER GETS EYES era — the 07-12 pressure test's verdict was
# "it cannot call a fight", and this bump is the answer. (1) N2: scheduled_event — the
# event clock (FOMC/CPI/NFP kind + minutes-until/since + surprise) reaches the payload;
# an empty calendar horizon is itself surfaced (blindness must be visible). (2) N4: the
# verdict schema gains the STANCE axis — fight (range expands / breakout risk) vs
# settle (pin/drift) vs no_read — the prison-yard question, finally asked and gradable.
# (3) N15: the prompt's "gravity is the default" is now CONDITIONED ON THE GAMMA SIGN —
# under short gamma the default is amplification/continuation, the magnet is NOT a
# target. (4) N6: a flat tape reads FLAT and confirms nothing (both options tape and
# book tilt); N5: the windowed tape (flow_recent) leads the flow block and the whole-day
# average is explicitly labeled a day average. (5) N3: gravity_motion ships as worded
# facts (walls melting/building, magnet walking, grip) instead of a raw unlabeled dict.
# (6) N11: the charm drift word only speaks when the engine's charm_word_ok gate is
# open (it is False until charm earns a graded record — the old word was a constant:
# UP on 804/806 rows). (7) N1/N16: wall-breach events and an open pin→trend road reach
# the payload as containment facts. (8) N19: a single reveal sample can no longer
# delete a unanimous multi-vote blind majority (dissent is recorded instead), and when
# a revision IS accepted the shipped conviction re-folds over ALL samples.
# wt-4 (2026-07-12): CHARM + TERRAIN era (H6 + tenor doctrine). (1) The payload now
# spells out the into-the-close charm drift as a word (charm_drift_into_close) —
# net dealer charm NEGATIVE means hedge re-balancing BUYS into the bell (drift UP),
# POSITIVE means it SELLS (drift DOWN), and the pull strengthens as minutes_to_close
# falls (charm ∝ 1/time-left) — and the prompt teaches when to lean on it (the
# afternoon drift call was a coin-flip, likely read inverted). (2) The walls in
# dealer_map_gravity are now TODAY'S 0DTE gamma walls (H2 flipped the telemetry
# preference); the structural 1-7DTE band walls ride beside them, and a compact
# dated_structure block (far monthlies/quarter-end, σ-distances, unsigned;
# H8 v2 07-12: ALL walls ride ≤15σ, far ones tagged FAR — the ≤3σ filter had
# dropped every real dated wall every day) gives the tower the outer terrain. Payload change =
# era bump: wt-3 rows judged scenes without these facts — never mix ledgers.
# wt-3 (2026-07-10): payload-era bump for the magnet-source fixes — the gravity prior
# (magnet_sd/pull_word) now anchors on the TRUE always-on gex_views magnet instead of
# the armed-only wall-clamped reversion revert-target (which starved it to "UNKNOWN"
# on quiet scans and teleported up to ~1σ at arm/disarm seams); the clamped fade
# target rides beside it as a separate armed-only field. Pre-fix wt-2 rows reasoned
# blind on quiet tapes — mixing eras in one ledger would poison the Wilson gate.
# wt-2 (2026-07-09): reframed from fade-only judge → GRAVITY-ANCHORED directional
# forecaster (magnet-pull is the default direction; reversion is the exception), plus
# OUTCOME-AWARE thesis memory (recent calls carry how they're doing + the session trend)
# so it holds a directional read instead of re-fading each slice. Also un-blinded two lower
# shadow layers into the payload: live order-book flow (order_flow) + the sibling Break Lens
# escape read (break_lens_head_c), both raw-fact/blind, attached only when present, so it can
# confirm the pull against the tape and never fade a real break. See mirai-watchtower memory.
PINNED_MODEL = "claude-sonnet-5"   # exact id, never an alias (drift protection);
                                   # config models.watchtower overrides if present
CALL_TIMEOUT_S = 90.0       # hard per-call limit — one attempt, no retries
                            # (07-05 golden replay: median call 45s, and every
                            #  error in the set was a 60s timeout — 90s clears
                            #  the observed tail while the tick budget still
                            #  caps the total; votes degrade gracefully)
TICK_BUDGET_S = 240.0       # total wall-clock all calls may spend in one scan.
                            # (07-09 live: at 150s the 3-vote + reveal design
                            #  NEVER ran — max 2 votes, 0 reveals all day; 240s
                            #  fits votes at the observed 45-135s walls. MUST
                            #  stay well under the launchd StartInterval (300s)
                            #  so a slow judge can't pile ticks up.)
DAILY_CALL_CAP = 35         # judged scans per day (cost guard). 25 burned out
                            # by ~13:00 on 07-07 AND 07-09 (dark into the close,
                            # exactly when a second opinion matters most); 35 +
                            # the held-scene throttle (INTEREST_REJUDGE_MIN)
                            # covers a full session.
VOTES_ON_FIRE = 3           # samples when the blind verdict says "fire"
INTEREST_REJUDGE_MIN = 15   # an interesting but UNCHANGED scene re-judges at
                            # most this often. An armed scene stays "interesting"
                            # for hours and used to re-burn a call every 5-min
                            # scan on the SAME picture (the 07-09 budget burner);
                            # a big_change still interrupts immediately.
INTEREST_STRETCH = 0.7      # |stretch| σ that makes a quiet scan worth judging
INTEREST_WALL_SIGMA = 0.75  # wall distance σ that makes a scan worth judging

# --- hybrid wake (Watchtower trigger plan, 2026-07-07) -----------------------
HEARTBEAT_MIN = 60          # judge at least once an hour even on a dead tape
INTERRUPT_MIN_GAP = 10      # a scene-shift interrupt can't re-fire faster than
                            # this many minutes since the last judged scan (spam
                            # guard); the daily cap is still the hard ceiling
MAGNET_JUMP_SIGMA = 0.5     # magnet relocating this far (σ) = a different scene
FLOW_FLIP_MIN = 0.4         # aggressor flow crossing zero with at least this force
VIX_TS_JUMP = 0.10          # a lurch in the VIX term-structure ratio
CROSS_MIN_SIGMA = 0.05      # a flip/wall cross must clear this σ deadband on one
                            # side (2-dp rounding jitter at the level ≠ a cross)

# --- soft-side stuck-vane guard (wt-8) ----------------------------------------
SOFT_SIDE_LOOKBACK = 50     # diary rows sampled (today first, walking back days)
SOFT_SIDE_STUCK_SHARE = 0.90  # same value on more than this share = a stuck vane
SOFT_SIDE_MIN_READS = 30    # thinner history than this proves nothing (fail-open)

# --- flip transition band (wt-8 doctrine gap, 2026-07-25) ----------------------
FLIP_CHOP_BAND_SIGMA = 0.25  # |spot − gamma flip| ≤ this σ = the regime is IN
                             # TRANSITION — the chop band: fakes and whipsaws
                             # live at the flip's doorstep, the sign can flip
                             # scan to scan; no directional play from inside it


# ==============================================================================
# Pure functions (no I/O) — unit-testable
# ==============================================================================
def _sd(level, spot, sigma):
    """A price level as a signed σ-distance from spot (+ above / − below),
    or None when either side is missing. This is how EVERY level reaches the
    model — never as an absolute index number it may have memorized."""
    if level is None or not spot or not sigma:
        return None
    return round((level - spot) / sigma, 2)


def interesting(rev: dict) -> tuple[bool, str]:
    """PREFILTER — is this scan worth a model call at all? Quiet ticks are the
    vast majority; judging them would burn the daily budget on nothing. A scan
    is interesting when the gates armed/fired, or the tape is stretched, or
    price is pressing a wall."""
    rev = rev or {}
    if rev.get("fired") or rev.get("armed"):
        return True, "gates active"
    for k in ("gap_stretch", "vwap_stretch"):
        v = rev.get(k)
        if v is not None and abs(v) >= INTEREST_STRETCH:
            return True, f"{k} {v:+.2f}σ"
    for k in ("wall_dist_call", "wall_dist_put"):
        v = rev.get(k)
        if v is not None and abs(v) <= INTEREST_WALL_SIGMA:
            return True, f"{k} {v:+.2f}σ"
    return False, "quiet"


def _sign(x) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def scene_fingerprint(t: dict) -> dict:
    """A tiny, comparable snapshot of the dealer scene — the axes whose
    MOVEMENT means 'the picture changed'. Levels are σ-from-spot so they compare
    across ticks; every field is None-safe. Consumed by big_change()."""
    t = t or {}
    rev = t.get("reversion_extreme") or {}
    gx = t.get("gex_views") or {}
    spot, sigma = t.get("spot"), t.get("sigma")
    net_gex = gx.get("net_gex")
    return {
        # TRUE magnet (gex_views) — always-on and clamp-free, so magnet-jump diffs
        # compare like with like. The armed-only reversion magnet was None on ~99%
        # of scans (the jump interrupt had NEVER fired) and mixing sources across
        # arm/disarm seams manufactures ~1σ fake jumps.
        "magnet_sd": _sd(gx.get("magnet"), spot, sigma),
        "flip_sd": _sd(t.get("gamma_flip"), spot, sigma),
        "call_wall_sd": _sd(t.get("call_wall"), spot, sigma),
        "put_wall_sd": _sd(t.get("put_wall"), spot, sigma),
        "regime": t.get("regime"),
        "gamma_sign": t.get("gamma_sign"),
        # H3 tie: when the engine already called the sign a tie ("uncertain"),
        # the raw net's residual sign is noise — fingerprinting it fired a
        # 'net-GEX flipped sign' interrupt on every knife-edge scan pair,
        # burning judged-scan budget on exactly the days H3 declares unreadable
        "net_gex_sign": (0 if gx.get("regime") == "uncertain" else
                         None if net_gex is None else _sign(net_gex)),
        "flow": gx.get("aggressor_flow"),
        "vix_ts": t.get("vix_ts"),
    }


def _crossed(a, b) -> bool:
    """A signed σ-distance flipped sign between looks — price walked THROUGH the
    level (the gamma flip, or a wall). Ignores the exactly-at-level (==0) case and
    sub-deadband jitter right at the level (σ-distances are rounded to 2 dp)."""
    return (a is not None and b is not None
            and (a > 0) != (b > 0) and a != 0 and b != 0
            and (abs(a) >= CROSS_MIN_SIGMA or abs(b) >= CROSS_MIN_SIGMA))


def big_change(prev: dict | None, cur: dict) -> tuple[bool, str]:
    """THE INTERRUPT TEST — did the scene shift hard vs the previous scan?
    Returns (changed, plain-english reason). Any single trip fires; the spam
    guard (min-gap since last look) throttles re-fires. prev=None (first look of
    the day) is the heartbeat's job, not this one."""
    if not prev:
        return False, ""
    why: list[str] = []
    if prev.get("regime") and cur.get("regime") and prev["regime"] != cur["regime"]:
        why.append(f"regime {prev['regime']}→{cur['regime']}")
    if (prev.get("gamma_sign") and cur.get("gamma_sign")
            and prev["gamma_sign"] != cur["gamma_sign"]):
        why.append(f"gamma {prev['gamma_sign']}→{cur['gamma_sign']}")
    if _crossed(prev.get("flip_sd"), cur.get("flip_sd")):
        why.append("price crossed the gamma flip")
    ns, cs = prev.get("net_gex_sign"), cur.get("net_gex_sign")
    if ns is not None and cs is not None and ns != 0 and cs != 0 and ns != cs:
        why.append("net-GEX flipped sign")
    if _crossed(prev.get("call_wall_sd"), cur.get("call_wall_sd")):
        why.append("broke the call wall")
    if _crossed(prev.get("put_wall_sd"), cur.get("put_wall_sd")):
        why.append("broke the put wall")
    pm, cm = prev.get("magnet_sd"), cur.get("magnet_sd")
    if pm is not None and cm is not None and abs(cm - pm) >= MAGNET_JUMP_SIGMA:
        why.append("magnet jumped")
    pf, cf = prev.get("flow"), cur.get("flow")
    if pf is not None and cf is not None and pf * cf < 0 and abs(cf) >= FLOW_FLIP_MIN:
        why.append("flow flipped")
    pv, cv = prev.get("vix_ts"), cur.get("vix_ts")
    if pv is not None and cv is not None and abs(cv - pv) >= VIX_TS_JUMP:
        why.append("VIX term structure lurched")
    return bool(why), "; ".join(why)


def _event_clock(now: datetime, t: dict) -> dict | None:
    """N2 — THE CLOCK THE TOWER NEVER HAD. The single biggest predictor of whether
    the yard erupts is a scheduled macro print minutes away, and the tower was blind
    to it. Reads the same shared calendar the event lens reads (FOMC/CPI seeded,
    NFP by rule), and speaks in minutes. When the calendar itself has nothing on the
    horizon, that BLINDNESS is surfaced instead of silently absent."""
    try:
        import lefteye_event_lens as _ev
        cal = _ev.load_calendar()
        day = now.astimezone(ET).date()
        todays = _ev.events_for(day, cal)
    except Exception:
        return None
    if todays:
        w = todays[0]
        try:
            hh, mm = int(w["time_et"][:2]), int(w["time_et"][3:5])
            t0 = now.astimezone(ET).replace(hour=hh, minute=mm, second=0, microsecond=0)
            mins = round((t0 - now.astimezone(ET)).total_seconds() / 60.0)
        except (KeyError, ValueError, TypeError):
            return None
        out: dict = {"kind": w.get("kind"), "time_et": w.get("time_et")}
        evl = t.get("event_lens") or {}
        if mins > 0:
            out["minutes_until"] = mins
            out["note"] = (f"{w.get('kind')} prints in {mins} minutes — the yard often erupts "
                           "around it; a pin read is NOT safe through the print, and positioning "
                           "ahead of it is a coin toss. Weight this above the gravity prior.")
        else:
            out["minutes_since"] = -mins
            if evl.get("surprise_ratio") is not None:
                out["surprise_ratio"] = evl.get("surprise_ratio")
                out["note"] = (f"{w.get('kind')} printed {-mins} min ago; surprise_ratio "
                               f"{evl['surprise_ratio']} (>1 = the print ate more than the whole "
                               "remaining expected-move budget — continuation risk).")
            else:
                out["note"] = f"{w.get('kind')} printed {-mins} min ago (aftermath session)."
        if len(todays) > 1:
            out["also_today"] = [e.get("kind") for e in todays[1:]]
        return out
    # no event today — is the calendar itself running dry? (hand-seeded file: going
    # blind silently is exactly the failure the card names)
    try:
        horizon = [e for e in cal if isinstance(e, dict) and e.get("date")
                   and e.get("date") > day.isoformat()]
        if not horizon:
            return {"kind": None,
                    "note": "event calendar has NO upcoming entries — the tower is blind to "
                            "scheduled prints until state/lob_flow/calendar.json is re-seeded."}
    except Exception:
        pass
    return None


def _motion_words(motion: dict, soft_side_stuck: bool = False) -> dict:
    """N3 — the wall-being-consumed signal, SPOKEN. gex_motion used to be dumped into
    the prompt as a raw unlabeled dict ('melt −0.041', no units, no sign convention)
    beside a note insisting every number is authoritative — the model was left to
    guess what the textbook pre-breakout picture looks like. Each field becomes a
    worded fact; None fields stay absent (a missing diff must not read as calm)."""
    out: dict = {}
    lb = motion.get("lookback_min")
    if lb is not None:
        out["window"] = f"changes measured over the last ~{lb:.0f} min"
    for side in ("call", "put"):
        m = motion.get(f"{side}_wall_melt_share")
        if m is None:
            continue
        if m <= -0.01:
            out[f"{side}_wall"] = (f"MELTING — the {side} wall lost {abs(m):.1%} of the field's "
                                   "mass at its strike (walls being consumed is the textbook "
                                   "pre-breakout picture on that side)")
        elif m >= 0.01:
            out[f"{side}_wall"] = f"BUILDING — the {side} wall gained {m:.1%} of the field's mass"
        else:
            out[f"{side}_wall"] = "holding (no meaningful mass change)"
    w = motion.get("magnet_walk_sigma")
    if w is not None:
        out["magnet"] = (f"WALKING {'UP' if w > 0 else 'DOWN'} {abs(w):.2f}σ over the window"
                         if abs(w) >= 0.02 else "holding its level")
    c = motion.get("flip_creep_sigma")
    if c is not None and abs(c) >= 0.02:
        out["gamma_flip"] = f"creeping {'UP' if c > 0 else 'DOWN'} {abs(c):.2f}σ"
    g = motion.get("grip_slope")
    if g is not None:
        out["grip"] = ("TIGHTENING — the magnet's share of the field is growing" if g >= 0.01
                       else "LOOSENING — the magnet's concentration is fading" if g <= -0.01
                       else "steady")
    s = motion.get("soft_side")
    if s in ("up", "down"):
        # wt-8 STUCK-VANE GUARD: a soft_side that has read the same value on >90% of
        # the last ~50 diary rows is terrain, not a fresh opinion — worded as such so
        # the tower stops re-counting a constant as today's evidence.
        if soft_side_stuck:
            out["soft_side"] = (f"{s} (NEAR-CONSTANT for days — treat as terrain "
                                "fact, not today's opinion)")
            return out
        # m8 (2026-07-13): the SHARE rides with the word. The tower was handed "soft
        # side UP" as a bare adjective and weighted it like a thesis; 0.494 vs 0.506 is
        # a coin flip wearing a direction's clothes. The number says how soft.
        gas = motion.get("gamma_above_share")
        lean = ""
        if gas is not None:
            edge = abs(gas - 0.5)
            lean = (f" — {gas:.1%} of the field's mass sits ABOVE spot"
                    + (" (BARELY soft: this is near a 50/50 split — treat it as no edge)"
                       if edge < 0.03 else
                       " (a decisive lean)" if edge >= 0.10 else ""))
        out["soft_side"] = (f"{s.upper()} — less gravity mass {'above' if s == 'up' else 'below'} "
                            f"spot; a flow shove meets the least resistance that way{lean}")
    return out


def _shove_words(shove: dict, net_gex: float | None) -> dict | None:
    """m8 (2026-07-13) — THE BOOK'S SLOPE, spoken. The shove test re-prices net dealer
    gamma at spot ± a flow shove and asks the only question that matters on a short-
    gamma day: if price gets pushed, does dealer hedging FEED the move or ABSORB it?

    It was computed every scan since the Break Lens build, written to the diary, drawn
    on the tablet — and never once shown to the Watchtower. On 2026-07-13 that blindness
    cost the tower four call verdicts: at 12:43 net GEX at spot was −3.0B (short), a
    shove UP re-priced to +3.5B (sign FLIPS — a rally walks back into LONG gamma where
    dealers damp it: rallies get ABSORBED) while a shove DOWN re-priced to −8.6B (short
    gamma DEEPENS 2.85× — hedging sells into weakness: the slide ACCELERATES). The book
    was structurally rigged for down, and the tower called up. Four times.

    Sign convention (from lefteye_gex_box.SHOVE TEST): margin = shoved net GEX / |net
    GEX at spot|. NEGATIVE margin = more short gamma that way = the AMPLIFYING side.
    POSITIVE = long gamma that way = the DAMPING side. So the accelerant side is simply
    the side with the lower (more negative) margin — that one comparison is the read."""
    if not isinstance(shove, dict):
        return None
    up_m, dn_m = shove.get("shove_up_margin"), shove.get("shove_down_margin")
    if up_m is None or dn_m is None:
        return None                     # absent, never a fabricated zero
    probe = shove.get("shove_sigma")
    net0 = shove.get("net_gex_spot") if shove.get("net_gex_spot") is not None else net_gex

    def _side(margin: float, flips: bool) -> str:
        short_at_spot = bool(net0 is not None and net0 < 0)
        if margin < 0:      # short gamma that way — hedging feeds the move
            deep = (f", DEEPENING to {abs(margin):.1f}× the gamma at spot"
                    if short_at_spot and abs(margin) > 1.15 else "")
            return (f"margin {margin:+.2f} — dealers go/stay SHORT gamma that way{deep}: "
                    "hedging pushes WITH the move. This is an ACCELERANT side — a shove "
                    "here feeds itself." + (" (the calm BREAKS on this side)" if flips else ""))
        return (f"margin {margin:+.2f} — dealers go/stay LONG gamma that way: hedging pushes "
                "AGAINST the move. This is an ABSORBING side — a shove here gets damped and "
                "faded." + (" (a move this way walks BACK INTO the calm zone)" if flips else ""))

    accel = "DOWN" if dn_m < up_m else "UP" if up_m < dn_m else None
    out = {
        "what_this_is": (f"TERRAIN: if flow shoved price ±{probe:.2f}σ from here, this is what "
                         "dealer gamma would BECOME — which side is CHEAPER to move IF pushed. "
                         "This measures RESISTANCE, not live pushing; it confirms nothing alone."),
        "if_price_shoves_UP": _side(up_m, bool(shove.get("up_flips"))),
        "if_price_shoves_DOWN": _side(dn_m, bool(shove.get("down_flips"))),
    }
    if net0 is not None:
        out["net_gex_at_spot"] = (f"{net0 / 1e9:+.2f}B — the MAGNITUDE behind the gamma sign "
                                  "(a shallow book flips easily; a deep one does not)")
    if accel and abs(up_m - dn_m) >= 0.15:
        absorb = "UP" if accel == "DOWN" else "DOWN"
        out["asymmetry"] = (
            f"THE BOOK IS ASYMMETRIC: {accel} is the ACCELERANT side, {absorb} is the ABSORBING "
            f"side (margins {dn_m:+.2f} down vs {up_m:+.2f} up). {accel} is the LOWER-RESISTANCE "
            "side IF something pushes — not a push itself: it is a slope, NO reason on its own to "
            f"expect a move that way. Calling {absorb} against confirmed live flow is calling "
            "into the side of the book built to swallow the move; calling the accelerant side "
            "with NO live force behind it is the potential-energy lean the 07-22 ledger paid "
            "for. Under SHORT gamma, and ONLY when an OBJECTIVE confirmed move is already in the "
            "payload (a FIRED break or a wall breach — never your own read of 'confirmed'), this "
            "outranks soft_side.")
    else:
        out["asymmetry"] = ("SYMMETRIC — the book leans neither way under a shove; no "
                            "asymmetry edge (do not manufacture one).")
    return out


def _speed_words(speed: dict) -> dict | None:
    """m8 (2026-07-13) — THE SPEEDOMETER, spoken. How violent is the tape underneath
    gravity and flow, and which side is the violence coming from? Also never shown to
    the tower before today. `quality` guards it: a mangled feed must stay silent rather
    than whisper a number the model will treat as authoritative."""
    if not isinstance(speed, dict) or speed.get("quality") != "ok":
        return None
    out: dict = {}
    pace = speed.get("rv_pace")
    if pace is not None:
        word = ("HOT — the tape is moving much faster than normal for this time of day"
                if pace >= 1.5 else
                "SLOW — a quieter tape than normal for this time of day (pins hold better)"
                if pace <= 0.6 else "normal pace for this time of day")
        out["pace"] = f"{pace:.2f}× the same-time-of-day norm — {word}"
    share = speed.get("semivar_down_share")
    if share is not None:
        if share >= 0.60:
            word = (f"{share:.0%} of today's variance came from DOWN moves — this is a "
                    "SELLING-DOMINATED tape; the motion is one-sided and it is not up")
        elif share <= 0.40:
            word = (f"{1 - share:.0%} of today's variance came from UP moves — this is a "
                    "BUYING-DOMINATED tape")
        else:
            word = f"{share:.0%} down / {1 - share:.0%} up — a TWO-SIDED tape (no side owns it)"
        out["who_owns_the_motion"] = word
    jz, jsign = speed.get("jump_z"), speed.get("jump_sign")
    if jz is not None and jz >= 2.0:
        out["jumps"] = (f"jump_z {jz:.1f} — the tape is moving in discrete JUMPS, not a smooth "
                        "drift (gaps through levels are live; stops and pins are unsafe)"
                        + (f"; the biggest 1-min move was {jsign.upper()}" if jsign else ""))
    rod = speed.get("rod_range_pts")
    if rod is not None:
        # wt-8 (Fix 3): a REST-OF-DAY budget, not a decision-horizon one — it caps the
        # whole session ahead, so it LOWERS conviction on an oversized call, it does not
        # veto one (an ignition bar expands the day's own range). Horizon-matching this to
        # the next-N-min via a share_cum read is a documented FOLLOW-UP, not built here.
        out["rest_of_day_range_pts"] = (f"{rod:.0f} pts of high-low range still expected before "
                                        "the bell — a magnitude beyond this is a LOW-CONVICTION "
                                        "call, not an impossible one")
    return out or None


def _gw_cluster_read(net_by_strike, spot, sigma) -> tuple[dict, str | None]:
    """wt-8 doctrine gaps (2026-07-25) — the GW cluster system, finally in the
    tower's room. Dominance tiers and the doctrine's CLEAN? gate lived in
    gw_vocab (docs/gw-vocab.md) and the viewstation, but the payload's walls
    arrived as bare σ-levels: the tower could not tell a ''' fortress from a '
    picket, and it had no way to see a contradictory hierarchy at all.

    Returns (walls, profile_word):
      walls — wall_cluster_above / wall_cluster_below for the nearest cluster
        each side of spot, each carrying its GW label, PRIME TIER and cluster
        span as σ-distances (dominance decides which walls matter);
      profile_word — the hierarchy half of the doctrine's cleanliness gate:
        "CLEAN — …", or "MESSY PROFILE — … STAND DOWN" when opposite-sign
        clusters interleave (a put-dominant cluster peak ABOVE a call-dominant
        one) or no dominant structure survives on a present surface.
    Fail-open + honest: no surface (or any error) → ({}, None) — fields ABSENT,
    never a fabricated CLEAN and never a false MESSY."""
    try:
        import gw_vocab
        if not net_by_strike or not spot or not sigma:
            return {}, None
        clusters = gw_vocab.cluster_walls(net_by_strike, spot)
        if not clusters:
            return {}, ("MESSY PROFILE — no dominant structure at all on the measured "
                        "surface; the most advanced read is recognizing there is no "
                        "clean read — STAND DOWN")
        call_peaks = [c["peak"] for c in clusters if c["side"] == "call"]
        put_peaks = [c["peak"] for c in clusters if c["side"] == "put"]
        if call_peaks and put_peaks and max(put_peaks) > min(call_peaks):
            profile_word = ("MESSY PROFILE — contradictory wall hierarchy (a put-dominant "
                            "cluster peak sits ABOVE a call-dominant cluster peak); the "
                            "most advanced read is recognizing there is no clean read — "
                            "STAND DOWN")
        else:
            profile_word = ("CLEAN — coherent wall hierarchy (no put-dominant cluster "
                            "stranded above a call-dominant one)")
        near = gw_vocab.nearest_walls(clusters, spot, per_side=1)

        def _row(c: dict) -> dict:
            return {"label": gw_vocab.gw_label(c["side"], c["tier"]),
                    "prime_tier": c["tier"],
                    "cluster_span_sigma": [_sd(c["lo"], spot, sigma),
                                           _sd(c["hi"], spot, sigma)],
                    "peak_sigma_from_spot": _sd(c["peak"], spot, sigma)}

        walls: dict = {}
        if near["above"]:
            walls["wall_cluster_above"] = _row(near["above"][0])
        if near["below"]:
            walls["wall_cluster_below"] = _row(near["below"][0])
        return walls, profile_word
    except Exception:
        return {}, None      # fail-open: a broken read stays out of the room


def build_payload(t: dict, reveal_gates: bool = False) -> dict:
    """THE SCENE — everything the model may see, as one dict of precomputed,
    σ-normalized numbers with the signs spelled out. `reveal_gates=False` is
    the BLIND pass: the four gates' continuous inputs are present (they are
    raw facts) but their pass/fail verdicts and the fired decision are NOT.
    Live order-book flow (`order_flow`) and the sibling Break Lens escape read
    (`break_lens_head_c`) ride along as raw facts too — attached only when they
    carry a genuine read, so the tower can confirm the pull against the tape and
    avoid fading a real break. Field order is stable on purpose (payload hygiene)."""
    rev = t.get("reversion_extreme") or {}
    gx = t.get("gex_views") or {}
    spot, sigma = t.get("spot"), t.get("sigma")
    # clock: minutes only, never a date (charm/pin decay reasoning needs time-of-day)
    mins_open = mins_close = None
    event_clock = ts = None
    try:
        ts = datetime.fromisoformat(t["ts"]).astimezone(ET)
        mins_open = max(0, (ts.hour * 60 + ts.minute) - (9 * 60 + 30))
        mins_close = max(0, (16 * 60) - (ts.hour * 60 + ts.minute))
        event_clock = _event_clock(ts, t)          # N2: FOMC/CPI/NFP countdown
    except (KeyError, ValueError, TypeError):
        pass
    g = t.get("gamma_sign")
    gamma_word = {"positive": "POSITIVE — dealers long gamma, moves get damped (pin-friendly)",
                  "negative": "NEGATIVE — dealers short gamma, moves get amplified (breakout fuel)",
                  }.get(g, "UNKNOWN — the gamma read abstained")
    # wt-8 CORRECTION (Fix 2): the whole-day aggressor_flow cumulative (tape_day_average)
    # is REMOVED. It never decays and never turns — at 15:30 on a pure-selling afternoon it
    # still read "+0.03, buyers" — and worse it is a single-strike DIFFERENCE that cancels
    # toward zero on the both-wings/accelerant days it most needs to speak. Only the
    # windowed twin (flow_recent) rides now, and a FLAT read of it confirms NOTHING.
    # NOTE: the windowed read inherits the SAME cancels-to-zero on both-wings days — a true
    # accelerant-day fix needs an UNSIGNED gross-gamma detector, which is NOT in this scope.
    fr = (t.get("gex_theta") or {}).get("flow_recent")
    def _tape_word(x, hard_at):
        if x is None:
            return None
        if abs(x) < 0.05:
            return f"{x:+.2f} — FLAT (a flat tape confirms NOTHING either way)"
        return (f"{x:+.2f} ({'buyers' if x > 0 else 'sellers'} hitting the tape"
                f"{' HARD' if abs(x) >= hard_at else ''})")
    flow_recent_word = _tape_word(fr, 0.5)
    # GRAVITY MOTION (the map as film, not photo) — the diary's gex_motion diff
    # (wall melt / magnet walk / flip creep / grip slope / soft side), attached
    # when this scan produced one. Defensive value first: "the fade leans on a
    # melting wall — stand down" is exactly the scene read the tower is for.
    motion = t.get("gex_motion") if isinstance(t.get("gex_motion"), dict) else None
    # wt-8 stuck-vane guard: the one diary read in this builder (fail-open, cheap) —
    # a soft_side that hasn't turned in ~50 rows must be worded as terrain, not opinion
    soft_stuck = _soft_side_stuck(ts, motion.get("soft_side")) if motion else False
    # wt-8 MAGNET DEMOTION: the magnet is a LOCATION/late-day pin target, never a
    # directional prior. The backtest sweep put the intraday pull-toward-magnet hit
    # rate at ~33% (a drift artifact, worse than a coin flip) — the word keeps the
    # fact (where the pin sits) and strips the "base case is a drift" framing that
    # armed the 07-22 put-lean.
    # ONE CONTINUOUS SOURCE (wt-3): the TRUE gravity pin (gex_views.magnet — always on,
    # never wall-clamped). The armed-only reversion revert-target used to lead here, but
    # it starved the prior to "UNKNOWN" on quiet scans, teleported up to ~1σ at arm/
    # disarm seams, and its no-pin fallback is the fade-side WALL — which made the
    # "pull" tautologically agree with the fade (the wt-1 bias sneaking back in). The
    # clamped fade target now rides beside it as its own field below.
    magnet_sd = _sd(gx.get("magnet") if gx.get("magnet") is not None else rev.get("magnet"),
                    spot, sigma)
    fade_target_sd = _sd(rev.get("magnet"), spot, sigma)   # armed-only revert target
    if magnet_sd is None:
        pull_word = "UNKNOWN — no magnet read; lean on flip/gamma/flow"
    elif abs(magnet_sd) <= 0.05:
        pull_word = ("spot sits ON the magnet — the pin target is here; two-sided, "
                     "no location edge")
    else:
        pull_word = (f"magnet sits {magnet_sd:+.2f}σ {'ABOVE' if magnet_sd > 0 else 'BELOW'} "
                     "spot — the magnet is a LOCATION/late-day pin target (intraday pull hit "
                     "~33% in backtest); it is NOT a directional default at any hour under "
                     "any regime")
    # CONTESTED MAGNET (wt-8 doctrine gap): the rival-pin warning lived only in the
    # human snippet (render_snippet) — the model was told "the pin sits here" while
    # the humans were told the field is contested. Two magnets under one table
    # usually means chop between them, not a clean run to either. Fail-open: an
    # uncontested or unreadable pin-zone record leaves the field absent.
    contested_word = None
    try:
        zs = gx.get("pin_zones") or []
        if gx.get("pin_contested") and len(zs) >= 2 and zs[0].get("share"):
            _rival_sd = _sd(zs[1].get("center"), spot, sigma)
            contested_word = (
                f"CONTESTED — a rival pin zone"
                + (f" {_rival_sd:+.2f}σ from spot" if _rival_sd is not None else "")
                + f" pulls {zs[1]['share'] / zs[0]['share']:.2f}× the primary's share: "
                  "two magnets under one table usually means CHOP between them, not a "
                  "clean run to either")
    except Exception:
        contested_word = None
    # LIVE ORDER-BOOK FLOW (the tape beneath the options map) — raw sensor, blind. Shadow
    # layer still warming its 21-day baseline, so attach ONLY when it carries a genuine read
    # (payload hygiene: a cold/empty book must not read as a zero-flow book).
    lob = t.get("lob_flow") or {}
    spy = t.get("spy_depth") or {}
    order_flow: dict = {}
    if lob.get("regime") is not None or lob.get("magnet") is not None or (lob.get("tape_trades") or 0) > 0:
        tilt = lob.get("tilt")
        if tilt is not None:
            # N6: a dead-flat book must never render as "buyers pressing" — |tilt| under
            # the noise floor is FLAT and confirms nothing (free confidence on noise)
            if abs(tilt) < 0.02:
                order_flow["book_tilt"] = (f"{tilt:+.2f} — FLAT order book "
                                           "(confirms NOTHING either way)")
            else:
                order_flow["book_tilt"] = (f"{tilt:+.2f} — "
                    f"{'buyers' if tilt > 0 else 'sellers'} pressing the order book")
        if lob.get("regime") is not None:
            order_flow["lob_regime"] = lob.get("regime")
        # only a book-DERIVED magnet belongs under an order-flow label — on turbulence
        # the engine borrows the options gravity magnet (magnet_source="gravity"), and
        # re-labeling that here would show the model the SAME pin twice as if the
        # order book independently confirmed it.
        if lob.get("magnet_source") == "defense":
            _lm = _sd(lob.get("magnet"), spot, sigma)
            if _lm is not None:
                order_flow["lob_magnet_sigma_from_spot"] = _lm
        for _flag in ("scatter", "spread_alert", "turbulence"):
            if lob.get(_flag):
                order_flow[_flag] = True
    if spy.get("regime") is not None or spy.get("size_z") is not None:
        order_flow["spy_depth_regime"] = spy.get("regime")
        if spy.get("size_z") is not None:
            order_flow["spy_depth_size_z"] = spy.get("size_z")
    # SIBLING HEAD C — the Break Lens (level-reclaim escape detector). Blind REGIME fact:
    # is the map's containment breaking, and which way? A cocked/fired break warns the scene
    # may TREND (escape) rather than revert — never fade INTO a confirmed break. Evidence,
    # not a verdict to defer to. Attached only when the lens is actually active.
    # CHARM DRIFT (H6, wt-4): the clock's tug, spelled out as a word (payload
    # hygiene — signs in words, precomputed). Net dealer charm NEGATIVE ⇒ dealer
    # deltas bleed LOWER as time passes ⇒ dealers BUY to re-hedge ⇒ drift pressure
    # UP into the close (the classic put-heavy charm grind-up); POSITIVE ⇒ the
    # mirror, hedge SELLING, drift DOWN. Charm ∝ 1/time-left, so the pull is weak
    # in the morning and grips hardest in the last ~2 hours — the wording carries
    # that so the model never reads a 10am charm sign as a 3pm force.
    # N11 (wt-5): the WORD only speaks when the engine's gate is open. Net dealer charm
    # is structurally locked negative by the assumed-sign artifact — it said "UP into
    # the close" on 804/806 live rows, and H6/wt-4 told the model to vote on it every
    # afternoon. charm_word_ok stays False until charm has a graded record; the raw
    # cex/cex_sign numbers still ride the payload as (unworded) facts.
    charm_drift = None
    if gx.get("charm_word_ok"):
        charm_drift = {
            "negative": "UP into the close — net dealer charm is negative: hedge re-balancing "
                        "BUYS as time decays; weak before ~13:00 ET, grips hardest in the last ~2h",
            "positive": "DOWN into the close — net dealer charm is positive: hedge re-balancing "
                        "SELLS as time decays; weak before ~13:00 ET, grips hardest in the last ~2h",
        }.get(gx.get("cex_sign"))
    # DATED STRUCTURE (wt-4, tenor doctrine level 3): the far-dated OI walls from
    # the nightly sidecar — outer terrain the tower was previously blind to.
    # Unsigned LOCATIONS only (assumed-sign is wrong ~44% out there; magnitude
    # deliberately not carried), σ-distances only, and only walls within reach
    # (far ones TAGGED, ≤15σ cap — H8 v2) so a crash strike can't silently
    # re-teach the far-wall anchoring H2 removed. age_days rides along so a stale wall
    # is never mistaken for a fresh fact.
    # H8 v2 (2026-07-12): the ≤3σ reach filter dropped EVERY real dated wall EVERY day
    # (live walls sit 4.7-13.8σ out) — the map got the quarterly but the Watchtower
    # never saw any of it. Walls now always reach the payload: in-reach ones plainly,
    # far ones explicitly TAGGED far (σ-distance stated, ≤15σ sanity cap) with wording
    # that keeps H2's anchor discipline — outer terrain, never a target.
    dated_bands = []
    for b in ((t.get("dated_gex") or {}).get("bands") or []):
        # m7 (2026-07-12): the wall the tower is told about is the NEAREST STRONG one
        # each side — the level that actually bounds today's tape. The absolute-max
        # king (often a 7000-class crash bet ~8% below spot) only serves when no
        # strong wall stands nearer; pre-m7 rows carry no near_* keys and fall back.
        # side guard (verify round): near_* was computed at PULL-time spot (05:17 PT;
        # a carried quarterly can be days old) — price may have walked through it since.
        # A drifted near wall on the wrong side of the LIVE spot falls back to the king.
        _ncw, _npw = b.get("near_call_wall"), b.get("near_put_wall")
        _cw_lvl = _ncw if (_ncw is not None and spot and _ncw > spot) else b.get("call_wall")
        _pw_lvl = _npw if (_npw is not None and spot and _npw < spot) else b.get("put_wall")
        _cw, _pw = _sd(_cw_lvl, spot, sigma), _sd(_pw_lvl, spot, sigma)
        row = {"band": b.get("band"), "days_to_expiry": b.get("dte"),
               **({"age_days": b.get("age_days")} if b.get("age_days") is not None else {})}
        if _cw is not None and abs(_cw) <= 15:
            row["call_wall_sigma_above"] = _cw
            if abs(_cw) > 3:
                row["call_wall_reach"] = "FAR — not reachable today; outer terrain only"
        if _pw is not None and abs(_pw) <= 15:
            row["put_wall_sigma_below"] = _pw
            if abs(_pw) > 3:
                row["put_wall_reach"] = "FAR — not reachable today; outer terrain only"
        if "call_wall_sigma_above" in row or "put_wall_sigma_below" in row:
            dated_bands.append(row)
    # structural 1-7DTE band walls (outer terrain beside H2's operative walls).
    # Same ≤3σ reach filter as dated_structure: an 8-10σ crash stratum in the
    # payload would re-teach the exact far-wall anchoring H2 just removed. And
    # suppressed when the operative wall IS the tenor wall (fallback landed
    # there) — the same level twice reads as independent confirmation.
    def _tenor_sd(tenor_w, near_w):
        if tenor_w is None or tenor_w == near_w:
            return None
        sd_v = _sd(tenor_w, spot, sigma)
        return sd_v if sd_v is not None and abs(sd_v) <= 3 else None
    tenor_cw_sd = _tenor_sd(t.get("call_wall_tenor"), t.get("call_wall"))
    tenor_pw_sd = _tenor_sd(t.get("put_wall_tenor"), t.get("put_wall"))
    # m8 (2026-07-13): the two layers the tower has never seen. Both fail-open — a
    # missing/mangled read is ABSENT from the payload, never a zero (payload hygiene).
    shove_words = _shove_words(gx.get("shove") or {}, gx.get("net_gex"))
    speed_words = _speed_words(t.get("speedometer") or {})
    # NET-GEX BINDING STRENGTH (wt-8, Fix 5): the raw net $B is NON-generalizing — a "big"
    # morning value is "thin" by 15:50. net/gross at spot is a RELATIVE, sign-explicit share:
    # how one-signed the gamma mass at spot is (+ → strong PIN, trust the pin; − → strong
    # AMPLIFICATION, trust continuation — never a bare "trust the regime"). Its OWN field so it
    # survives when shove margins are None. SUPPRESSED when the gamma sign is unknown: in the
    # tie band the regime already reads "uncertain" (g == "unknown") and the prompt already
    # caps conviction there — don't re-cap it. Fail-open on a missing/zero gross.
    binding_word = None
    _net0, _gross0 = gx.get("net_gex"), gx.get("gross_gex")
    if g in ("positive", "negative") and _net0 is not None and _gross0 and _gross0 > 0:
        _share = _net0 / _gross0
        if _share >= 0:
            binding_word = (f"{_share:+.0%} of the gamma mass at spot nets LONG — a "
                            "PIN this strong BINDS price here: trust the pin/fade, scale "
                            "conviction UP toward the pin, DOWN as this share nears 0")
        else:
            binding_word = (f"{_share:+.0%} of the gamma mass at spot nets SHORT — "
                            "AMPLIFICATION this strong FEEDS the move: trust continuation, "
                            "scale conviction UP with the tape, DOWN as this share nears 0")
    # FLIP TRANSITION BAND (wt-8 doctrine gap): the doctrine marks the flip's
    # doorstep as the chop zone — the regime is IN TRANSITION there, and the
    # biggest whipsaw scans are exactly the ones where spot straddles the flip.
    # The word only appears INSIDE the band (absent ≠ "far from the flip").
    flip_sd = _sd(t.get("gamma_flip"), spot, sigma)
    transition_word = None
    if flip_sd is not None and abs(flip_sd) <= FLIP_CHOP_BAND_SIGMA:
        transition_word = (
            f"IN THE CHOP BAND ({abs(flip_sd):.2f}σ from the flip) — the regime is IN "
            "TRANSITION: fakes and whipsaws live here and the gamma sign can flip scan "
            "to scan; NO directional play from inside the band")
    # GW CLUSTER DOMINANCE + PROFILE CLEANLINESS (wt-8 doctrine gaps): the nearest
    # wall each side rides with its cluster span + prime tier, and the same
    # clustering pass answers the doctrine's CLEAN? gate. Fail-open in the helper:
    # no measured surface → both absent.
    gw_walls, profile_word = _gw_cluster_read(gx.get("net_by_strike"), spot, sigma)
    lr = t.get("level_reclaim") or {}
    _bstate = lr.get("break_state")
    _bdir = lr.get("direction") if lr.get("direction") not in (None, "none") else lr.get("cock_direction")
    break_lens: dict = {}
    if lr.get("fired") or (_bstate not in (None, "idle")) or lr.get("cock_direction"):
        # m8 (2026-07-13): COCKED IS NOT FIRED. The note used to say the same thing for
        # both states, so the tower read a cocked-but-ungated break as permission to call
        # that direction — on 07-13 it fired FOUR calls (12:43, 14:35, 14:57, 15:19) on a
        # break that never fired, into an accelerant-down book. A cocked break is a place
        # to WATCH, not a direction to take; only a FIRED break is a directional fact.
        _fired_break = bool(lr.get("fired")) or _bstate == "fired"
        _note = ("BREAK FIRED — the map is failing to contain price on that side: this IS a "
                 "directional fact. Follow it or stand down; never fade INTO it."
                 if _fired_break else
                 "COCKED, NOT FIRED — the trigger is ARMED and has NOT TRIGGERED. This is a "
                 "WATCH level, not a direction. It says where price MIGHT escape IF it escapes; "
                 "it is NOT evidence that it will. Do NOT take escape_direction as a directional "
                 "call on its own — least of all against terrain_asymmetry, tape_speed or the tape. "
                 "Either wait for it to FIRE, or stand down (fired=false).")
        break_lens = {k: v for k, v in {
            "break_state": _bstate or ("fired" if lr.get("fired") else None),
            "has_it_actually_fired": _fired_break,
            "escape_direction": _bdir,
            "reclaim_level_sigma_from_spot": _sd(lr.get("level") or lr.get("cock_level"), spot, sigma),
            "level_kind": lr.get("level_kind") or lr.get("cock_level_kind"),
            "flips_regime": lr.get("flips_regime") or None,
            "note": _note,
        }.items() if v is not None}
    # SIEGE (wt-7): underlying (SPY) volume EFFORT at wall touches — the REVERSED
    # SIGN finding, finally in the room. Fail-open on every guard the box itself
    # respects: a degraded feed, a saturated session, or a verdictless watch is
    # ABSENT from the payload, never a zero (payload hygiene).
    sg = t.get("siege") or {}
    siege_rows = []
    # wt-8 (Fix 4): 0DTE walls HUG spot, so the siege box tags such a touch near_spot and
    # (today) suppresses its verdict as an ATM artifact. Admit a near-spot verdict ONLY once
    # the effort baseline is ROBUST (21 accrued days) — an immature percentile on a
    # spot-hugging wall is noise wearing a verdict. Non-near-spot verdicts ride as before.
    # DORMANT: the baseline is cold today AND the siege engine only emits near-spot verdicts
    # under a robust baseline (a deferred change in that package), so this is no live change.
    siege_baseline_robust = sg.get("baseline") == "robust"
    if sg.get("health") == "OK" and not sg.get("saturated"):
        for tw in (sg.get("towers") or []):
            v = tw.get("verdict")
            if v not in ("SIEGE", "QUIET"):
                continue
            if tw.get("near_spot") and not siege_baseline_robust:
                continue                # never forward a spot-hugging verdict pre-robust
            pct = tw.get("effort_pct")
            word = (f"SIEGE — HEAVY underlying volume at this wall touch"
                    f" (effort {pct:.0f}th pct): REVERSED SIGN — heavy volume at a "
                    "wall is attackers pressing, NOT defenders absorbing; expect this "
                    "wall to BREAK (sieged walls held only 39% vs 54% quiet in the sweep)"
                    if v == "SIEGE" else
                    f"QUIET — light underlying volume at this wall touch"
                    f" (effort {pct:.0f}th pct): expect this wall to HOLD")
            siege_rows.append({k: v2 for k, v2 in {
                "wall": tw.get("kind"),
                "level_sigma_from_spot": _sd(tw.get("level"), spot, sigma),
                "read": word,
                # a resolved episode whose level still stands carries its outcome —
                # "this wall was sieged and BROKE" is a containment fact for the scene
                **({"already_resolved": tw.get("outcome")} if tw.get("outcome") else {}),
            }.items() if v2 is not None})
    siege_block: dict = {}
    if siege_rows:
        siege_block = {"towers": siege_rows}
        if sg.get("baseline") in ("cold", "warming"):
            siege_block["baseline_maturity"] = (
                f"{sg['baseline']} — the effort baseline has not accrued its full "
                "21 prior days; treat percentiles as lower-confidence")
    # DEX (SHADOW, advisory) — the DIRECTION side of the dealer map: net dealer
    # delta still to re-hedge (lefteye_dex). Record-only this era: the tower may
    # read it as context, no gate does, and the wording says so. Payload hygiene:
    # shares + a $bn magnitude word, never absolute strike levels; absent (never
    # null) when the scan carried no dex read.
    dx = t.get("dex_views") or {}
    dex_block: dict = {}
    if dx.get("net_dex_total") is not None:
        _dx_fs = dx.get("dex_flow_signed")
        # wt-8 (Fix 6c): the naive above/below split is DEMOTED, not deleted — kept as
        # standing-inventory GEOGRAPHY (where the delta mass sits), and the naive SIGN rides
        # LAST, de-emphasized, since it is structurally LONG by construction (a constant, not
        # a live read). The flow-signed twin — whose sign is actually alive — leads the sign
        # reads now, magnitude on its OWN uncalibrated scale (Fix 6b: NOT the naive $bn
        # formatter — a different, ~100×-swingier scale, so a distinct unit and caveat).
        _geo = {k: v for k, v in {
            "delta_mass_above_spot_share": dx.get("dex_above_spot"),
            "delta_mass_below_spot_share": dx.get("dex_below_spot"),
        }.items() if v is not None}
        dex_block = {k: v for k, v in {
            "magnitude": (f"≈ ${dx['net_dex_total'] / 1e9:.1f}bn underlying-equivalent "
                          "— UNCALIBRATED: no graded baseline yet, 'large' is not "
                          "defined this era"),
            "standing_inventory_geography": ({**_geo,
                "note": "WHERE standing dealer-delta inventory sits vs spot — geography, "
                        "not a live directional call"} if _geo else None),
            **({"flow_signed_read": dx.get("dex_flow_word"),
                "flow_signed_magnitude": (
                    f"≈ {dx['dex_flow_signed'] / 1e6:+.0f}M underlying-δ on TODAY'S tape "
                    "only — UNCALIBRATED and on a DIFFERENT scale than the naive net above "
                    "(it swings ~100× intraday); read the SIGN and its move, not the level")}
               if _dx_fs is not None else {}),
            # DEMOTED: the naive sign is the weakest part of this read, so it rides last
            "naive_net_sign": dx.get("dex_word"),
            "note": "DEX = the direction side: net dealer delta still to re-hedge — "
                    "mechanical pressure, the strongest DIRECTIONAL read in the stack "
                    "when large. ADVISORY THIS ERA: record-only and ungraded — context "
                    "beside the gamma map, never a gate. The naive_net_sign shares the "
                    "gamma map's assumed-sign caveat (flow-inferred sign disagrees on "
                    "~44% of strikes) and is structurally LONG by construction — weigh the "
                    "flow_signed_read and the geography, not that sign.",
        }.items() if v is not None}
    payload = {
        "task": "You are the Watchtower: forecast where 0DTE SPX goes over the next hour — "
                "the gamma sign is the PERMISSION SLIP: fade/pin plays under LONG gamma, "
                "continuation allowed under SHORT gamma, stand down when it is unknown.",
        "clock": {"minutes_since_open": mins_open, "minutes_to_close": mins_close},
        # wt-8 (Fix 3a): the σ↔points ruler. Read at the top of this builder and never
        # emitted before — the tower needs it to reconcile σ-targets with the point-range
        # facts (tape.rest_of_day_range_pts, tape_speed) it is already handed. This is a
        # SCALE, not an absolute index level (the no-absolute-levels hygiene rule holds:
        # 1σ ≈ points_per_sigma index points; reason in σ, never anchor to the level).
        "scale": {"points_per_sigma": round(sigma, 2) if sigma else None},
        "dealer_map_gravity": {
            # wt-8: the PERMISSION SLIP leads the block — the sign decides which
            # plays exist at all before the magnet says where the pin sits
            "gamma_sign_at_spot": gamma_word,
            # wt-8 (Fix 5): how HARD that sign binds — the normalized net/gross share at
            # spot, its OWN field (survives when shove is None), absent in the tie band
            **({"net_gex_binding": binding_word} if binding_word else {}),
            "pull_toward_magnet": pull_word,
            "magnet_sigma_from_spot": magnet_sd,
            **({"magnet_contested": contested_word} if contested_word else {}),
            # the Fade lens's wall-clamped revert-target — present only when the tape
            # is armed; a fade context read, NOT the gravity pull (absent, never null)
            **({"fade_revert_target_sigma": fade_target_sd,
                **({"fade_target_wall_clamped": True}
                   if rev.get("magnet_out_of_band") else {})}
               if fade_target_sd is not None else {}),
            "gamma_flip_sigma_from_spot": flip_sd,
            **({"regime_transition_band": transition_word} if transition_word else {}),
            # NEAR walls: today's 0DTE gamma walls (H2) — the bounds price actually
            # fights this session; the structural band walls follow as outer terrain
            "call_wall_sigma_above": _sd(t.get("call_wall"), spot, sigma),
            "put_wall_sigma_below": _sd(t.get("put_wall"), spot, sigma),
            # GW dominance (wt-8): the nearest measured CLUSTER each side — span +
            # prime tier; dominance decides which walls matter, labels don't
            **gw_walls,
            **({"structural_call_wall_sigma_above": tenor_cw_sd}
               if tenor_cw_sd is not None else {}),
            **({"structural_put_wall_sigma_below": tenor_pw_sd}
               if tenor_pw_sd is not None else {}),
            **({"profile_clean": profile_word} if profile_word else {}),
            "pin_top_share": gx.get("pin_top_share"),
            "vanna_sign": gx.get("vex_sign"), "charm_sign": gx.get("cex_sign"),
            **({"charm_drift_into_close": charm_drift} if charm_drift else {}),
        },
        # wt-8 (Fix 2): only the WINDOWED tape rides — the whole-day cumulative is gone.
        # Attached only when it carries a read (payload hygiene: a cold window ≠ zero flow).
        **({"live_flow": {"tape_recent_window": flow_recent_word}} if flow_recent_word else {}),
        # N2 (wt-5): the event clock — the biggest eruption predictor, finally visible
        **({"scheduled_event": event_clock} if event_clock else {}),
        # N1/N16 (wt-5): containment facts — a broken fence and an open road are the
        # loudest structural facts a forecaster can be handed
        **({"wall_breach_event": {
                "side": (t.get("wall_breach") or {}).get("side"),
                "overshoot_sigma": (t.get("wall_breach") or {}).get("overshoot_sigma"),
                "note": "price BROKE this scan through the operative wall — containment "
                        "failed on that side; do not fade the break",
            }} if t.get("wall_breach") else {}),
        **({"containment_road": {
                "state": f"OPEN ({(t.get('regime_road') or {}).get('kind')}) — the "
                         f"{(t.get('regime_road') or {}).get('side')} wall is broken and price "
                         "holds beyond it; the regime is forced TRENDING (follow, don't fade)",
            }} if t.get("regime_road") else {}),
        # order-book flow, gravity-motion, and the sibling Break Lens are attached only
        # when they carry a genuine read — payload hygiene: a missing read must not read
        # as a zero read (absent, never null).
        **({"order_flow": order_flow} if order_flow else {}),
        **({"gravity_motion": _motion_words(motion, soft_stuck) or motion} if motion else {}),
        # m8 (2026-07-13) / wt-8 rename: the book's SLOPE under a shove — which side
        # dealer hedging would feed and which it would swallow IF pushed. Renamed
        # flow_asymmetry → terrain_asymmetry (payload word only; shove storage keys
        # untouched): it measures resistance, not live pushing, and the old name read
        # as a flow fact — half of the 07-22 potential-energy put-lean.
        **({"terrain_asymmetry": shove_words} if shove_words else {}),
        **({"break_lens_head_c": break_lens} if break_lens else {}),
        # SIEGE (wt-7): effort-at-wall verdicts — containment evidence about the
        # touched wall itself, never a standalone direction
        **({"siege_effort_at_walls": siege_block} if siege_block else {}),
        # DEX (SHADOW): the direction-side dealer-delta read — advisory context
        # this era, record-only, no gate reads it
        **({"dealer_delta_dex": dex_block} if dex_block else {}),
        **({"dated_structure": {
                "bands": dated_bands,
                "note": "far-dated OI wall LOCATIONS (unsigned, no magnitude carried). "
                        "Outer terrain: relevant only when spot is actually near one "
                        "(they are hard shelves on approach), NEVER a drift target.",
            }} if dated_bands else {}),
        "tape": {
            "gap_from_prior_close_sigma": rev.get("gap_stretch"),
            "vwap_stretch_sigma": rev.get("vwap_stretch"),
            "last_bar_turned_back": rev.get("reaction"),
            "range_vs_expected_move": t.get("range_em"),
            "variance_ratio": t.get("variance_ratio"),
            "vix_term_structure": t.get("vix_ts"),
            # m8: HOW VIOLENT, and WHO OWNS THE MOTION — the speedometer, finally in
            # the room. Absent (never null) when the feed is not clean.
            **({"tape_speed": speed_words} if speed_words else {}),
        },
        "note": ("All levels are signed σ-distances from spot (+ above, − below). "
                 "Numbers above are precomputed and authoritative — do not re-derive."),
    }
    if reveal_gates:
        payload["gates_verdict_head_a"] = {
            "fired": rev.get("fired"), "direction": rev.get("direction"),
            "armed": rev.get("armed"), "regime_is_pinning": t.get("regime") == "pinning",
            "runway_ok": rev.get("runway_ok"),
            "note": "This is the rule-checklist's decision. It is EVIDENCE, not the answer.",
        }
    return payload


_VERDICT_SHAPE = {          # the exact JSON the model must return (schema-by-example)
    "fired": True, "direction": "call", "magnitude_sigma": 0.5,
    "range_sigma": [-0.2, 0.8], "horizon_min": 60, "conviction": 0.65,
    # N4 (wt-5): THE PRISON-YARD QUESTION, as its own axis. fired=false used to blur
    # "no directional edge" together with "it will pin" AND "it will erupt" (66% of
    # judged scans) — the tower was never asked the one question it exists for.
    "stance": "settle",     # "fight" = range expands / breakout risk over the horizon;
                            # "settle" = pin/drift inside the walls; "no_read" = honest blind
    "scene": "one short plain-english read of where the map pulls and why",
    "would_change_mind": "what structural change would flip this direction",
}


def _prompt(payload: dict, blind_verdict: dict | None = None) -> str:
    """Assemble the full prompt. With `blind_verdict` set this is the REVEAL
    pass: the model sees its own blind answer + the gates' verdict and must
    confirm or revise, explaining any override of the gates."""
    head = (
        "You forecast the next ~hour of 0DTE SPX from the dealer-positioning map. "
        "Answer THREE things: STANCE (does the range erupt or settle?), DIRECTION, and SIZE.\n"
        "How to read the map:\n"
        "- THE GAMMA SIGN IS THE PERMISSION SLIP (wt-8) — check gamma_sign_at_spot FIRST. It "
        "does not just set a default; it decides WHICH PLAYS ARE ALLOWED AT ALL. This is both "
        "the external evidence (a long-gamma tape structurally damps momentum; a short-gamma "
        "tape amplifies it) and this tower's own ledger: every 07-22 loss was the same "
        "potential-energy put-lean (terrain_asymmetry + soft_side + magnet, no live force) "
        "fired under LONG gamma; the same play won under SHORT gamma on 07-23.\n"
        "  * Dealers LONG gamma (positive): drift/momentum/continuation bets are FORBIDDEN — "
        "including that potential-energy lean (terrain slope + soft side + magnet with no live "
        "tape behind it). Hedging damps and fades moves. The ONLY allowed plays: FADE at a "
        "range edge/wall, PIN/settle, or STAND DOWN. Base stance: settle.\n"
        "  * Dealers SHORT gamma (negative — CONFIRMED, not unknown): continuation/drift bets "
        "are allowed; amplification is real — hedging pushes price AWAY from balance. Follow "
        "the tape/break direction; expect range expansion. Base stance: fight.\n"
        "  * Sign UNKNOWN (tie/blackout): STAND DOWN — no directional bet on an unknown "
        "regime. A balanced book is a HAIR-TRIGGER, not a calm one: stance leans fight-risk, "
        "conviction low.\n"
        "  * IN THE CHOP BAND: if regime_transition_band is PRESENT, spot sits within "
        f"{FLIP_CHOP_BAND_SIGMA}σ of the gamma flip — the regime is IN TRANSITION, and the "
        "flip's doorstep is the CHOP ZONE: fakes and whipsaws live here, and the sign can "
        "flip scan to scan. NO directional play from inside the band; conviction is CAPPED "
        "low; stance leans settle or fight on the rest of the evidence — NEVER a drift call "
        "from the band itself.\n"
        "- THE MAGNET IS A LOCATION, NOT A DIRECTION. pull_toward_magnet names where the "
        "map's pin sits — a LOCATION/late-day pin target (intraday pull toward it hit ~33% in "
        "backtest); it is NOT a directional default at any hour under any regime. Use it to "
        "place pins and fades, never to pick a drift direction. If magnet_contested is "
        "present, the field has TWO pins — expect chop between them, not a clean run.\n"
        "- BINDING STRENGTH IS A CONVICTION SCALER (wt-8). net_gex_binding is the NORMALIZED "
        "net/gross gamma share at spot — how one-signed the book is, comparable across days and "
        "hours where the raw $ is not. It does NOT pick a direction; it scales conviction on the "
        "direction the gamma sign already permits: a strongly POSITIVE share is a hard PIN (size "
        "the fade/pin up), a strongly NEGATIVE share is real AMPLIFICATION (size the continuation "
        "up), and a share near 0 is a thin book — low conviction either way. Absent in the tie "
        "band (the sign is already unknown there).\n"
        "- WALL DOMINANCE DECIDES WHICH WALLS MATTER — LABELS DON'T (GW vocab). "
        "wall_cluster_above / wall_cluster_below carry the nearest measured wall cluster "
        "each side: its span in σ and its PRIME TIER — ' minor, '' significant, ''' "
        "dominant (share of the strongest structure on the surface). A ''' wall outranks "
        "a ' wall: a pin or fade at a ''' wall is backed by real mass; a ' wall is thin "
        "and breaks cheap. Weigh the tier, never the name.\n"
        "- A MESSY PROFILE IS A STAND-DOWN GATE (wt-8). If profile_clean reads MESSY "
        "PROFILE (opposite-sign clusters interleaved, or no dominant structure at all), "
        "the wall hierarchy is contradictory and there is NO clean read — the most "
        "advanced read is recognizing that: STAND DOWN (fired=false), never manufacture "
        "a direction from a contradictory map. Absent = no measured surface this scan, "
        "NOT a clean bill; CLEAN is a precondition, not a signal.\n"
        "- STANCE is its own answer, independent of direction: \"fight\" = the range expands / "
        "containment breaks over the horizon; \"settle\" = price pins/drifts inside the walls; "
        "\"no_read\" = genuinely blind. You are graded on stance separately — fired=false with "
        "stance=settle (confident pin) and fired=false with stance=no_read (no idea) are "
        "DIFFERENT answers; never blur them.\n"
        "- SCHEDULED EVENTS OUTRANK GRAVITY. If scheduled_event shows a print minutes away, "
        "the yard usually erupts around it: stance leans fight, pins are unsafe through the "
        "print, and a pre-print directional call deserves low conviction.\n"
        "- A BROKEN FENCE IS A FACT. wall_breach_event / containment_road mean containment "
        "FAILED: never fade a break; follow it or stand down.\n"
        "- COCKED IS NOT FIRED (m8). A break_lens_head_c with break_state=\"cocked\" "
        "(has_it_actually_fired=false), or a fade setup that is merely armed with "
        "runway_ok=false, is A TRIGGER THAT HAS NOT TRIGGERED. It marks where price MIGHT "
        "escape — it is NOT evidence that it will, and its escape_direction is NOT a "
        "directional call. NEVER fire a direction on an un-triggered trigger while "
        "terrain_asymmetry, tape_speed, live_flow or the tape point the other way: wait for it "
        "to FIRE, or stand down (fired=false, stance=fight — a coiled break IS an eruption "
        "read, so say fight; just don't guess the SIDE).\n"
        "- TERRAIN ASYMMETRY IS THE BOOK'S SLOPE, NOT A PUSH (wt-8). terrain_asymmetry tells "
        "you which side is CHEAPER to move IF pushed — what dealer gamma would BECOME under a "
        "shove each way. It measures RESISTANCE, NOT live pushing, and it confirms NOTHING "
        "alone: slope is potential energy, and potential energy without a live force (tape, "
        "flow, a FIRED break) is not a trade. Under SHORT gamma AND an OBJECTIVE confirmed move "
        "ALREADY IN THE PAYLOAD (a FIRED break_lens_head_c, a wall_breach_event, or an OPEN "
        "containment_road — never your own read of 'confirmed'), the ACCELERANT side sharpens "
        "the continuation read and outranks soft_side and the magnet; under LONG gamma, or with "
        "no such objective confirmation, the slope is NOT permission to bet. Never "
        "fire a direction on terrain_asymmetry + soft_side + magnet alone — that is the exact "
        "lean the 07-22 ledger paid for.\n"
        "- SPEED AND OWNERSHIP (m8). tape.tape_speed says how violent the tape is and WHO "
        "OWNS THE MOTION (semivariance). A selling-dominated tape on a HOT pace is a tape "
        "with a side — do not call against it on structure alone. pace and rest_of_day_range_pts "
        "are CONVICTION-LOWERING guidance, NOT a hard stand-down: a SLOW pace or a small "
        "rest-of-day budget shrinks your magnitude and conviction, it does not veto the call. "
        "EXEMPTION (wt-8): under CONFIRMED-NEGATIVE gamma with a move already underway "
        "(a FIRED break, a wall breach, a HOT one-sided tape), do NOT let a not-yet-elevated "
        "pace suppress the continuation — the pace often shows up AFTER the ignition bar, and "
        "waiting for it forfeits the move. Convert with scale.points_per_sigma when comparing a "
        "σ-magnitude to a point-range.\n"
        "- UNDER LONG GAMMA the only directional play is the FADE AT AN EDGE: the tape "
        "stretched into a NEAR wall or range edge with a turn bar and flow rolling over. No "
        "edge, no fade — then the honest answer is pin/settle (fired=false, stance=settle) or "
        "stand down. Never manufacture a drift call from structure alone. (Under short gamma "
        "this rule is void — continuation is allowed there.)\n"
        "- CONFIRM WITH THE TAPE — but a FLAT tape confirms NOTHING. live_flow.tape_recent_window "
        "(the last minutes of options aggression) is the live tape read; order_flow.book_tilt "
        "(the equity order book) and an active break_lens_head_c either confirm or contradict "
        "the pull. Any read marked FLAT adds zero confidence to any thesis. NEVER fade into a "
        "confirmed break; be wary of calling against strong book flow. All attached only when "
        "present; absence ≠ zero.\n"
        "- CHARM IS THE CLOCK'S TUG — and it grips hardest near the bell. IF "
        "dealer_map_gravity.charm_drift_into_close is PRESENT it is precomputed (direction "
        "included — never re-derive a drift direction from charm_sign yourself): before ~13:00 ET "
        "background, in the afternoon a real vote on the drift call. IF IT IS ABSENT, charm has "
        "no graded record yet — give charm/charm_sign NO directional weight at all.\n"
        "- SIEGE IS THE WALL'S STRESS TEST — WITH A REVERSED SIGN (wt-7). "
        "siege_effort_at_walls reads the UNDERLYING (SPY) volume spent while price touched a "
        "wall. HEAVY effort (SIEGE) means expect that wall to BREAK — heavy volume at a wall "
        "is attackers pressing, not defenders absorbing; the intuitive \"absorption = "
        "strength\" read is the WRONG sign on this tape. QUIET means expect the wall to "
        "HOLD. This is containment evidence about THAT WALL only — it sharpens or weakens a "
        "pin/break read at that level; it is NEVER a standalone BREAK direction. It is "
        "SUBORDINATE to the gamma regime: under LONG gamma a pin can HOLD despite heavy "
        "effort (dealer hedging damps the attack), so a SIEGE verdict there lowers the pin's "
        "safety but does not license a breakout call. A SIEGE verdict agrees with a "
        "cocked/fired break on the same side; a QUIET wall makes a pin at it safer. Absent "
        "when the feed is degraded or no touch carried a verdict.\n"
        "- DEX IS THE DIRECTION SIDE — ADVISORY ONLY THIS ERA. dealer_delta_dex is the net "
        "dealer DELTA still to re-hedge: mechanical pressure, the strongest DIRECTIONAL read "
        "in the stack when large — but here it is UNGRADED and record-only. Treat it as "
        "context that sharpens a read the gamma sign and the tape already support; NEVER a "
        "standalone direction, never a reason to override the gamma-sign defaults. Its "
        "naive_net_sign is structurally LONG (the same assumed-sign convention as the gamma "
        "map, wrong on ~44% of strikes per the flow-inferred cross-check) — weigh the "
        "standing_inventory_geography split and the flow_signed_read (whose sign is actually "
        "alive), not the naive sign.\n"
        "- READ THE MAP IN TENOR ORDER: today's 0DTE book first (the walls and magnet — they are "
        "what dealers re-balance TODAY), the structural_*_wall band walls second (multi-day "
        "terrain), dated_structure last (far monthly/quarter-end OI shelves — react to them only "
        "when spot is near one; they are not targets and their sign is unknown).\n"
        "- MACRO MOOD IS OVERNIGHT CONTEXT, NOT A TRIGGER (wt-8). day_context.macro_mood is the "
        "morning brief's overnight sentiment read (direction + confidence). Let it break a tie "
        "and set a mild prior; it is SUBORDINATE to live structure and the gamma sign, a "
        "scheduled_event outranks it, and it is never a reason to fire on its own.\n"
        "- direction: \"call\" = you expect price UP over the horizon, \"put\" = DOWN. Set "
        "fired=false (direction null) when you have no directional edge — but ALWAYS still "
        "answer stance (a two-sided scene that will stay pinned is settle, not no_read).\n"
        "- HOLD YOUR THESIS. day_context.my_recent_verdicts lists your recent calls, each with "
        "working_sigma_since = how far price has since moved in that call's favor (negative = it "
        "is failing). day_context.session shows the trend since the open. Keep your DIRECTION "
        "across scans and adjust magnitude/conviction on small moves. FLIP direction only if the "
        "STRUCTURE changed (gamma sign flipped, flow flipped hard) OR your "
        "recent same-way calls are persistently failing (working_sigma_since staying negative) — "
        "then the map is telling you your read was wrong, so switch to the side that is working.\n"
        "- magnitude_sigma: expected move in your direction over horizon_min. range_sigma: "
        "[worst, best] in σ. conviction: 0-1, honest probability price reaches +0.30σ in your "
        "direction before −0.30σ within 120 min.\n"
        "- The checklist numbers are evidence, not the answer — judge the whole scene.\n"
    )
    scene = f"SCENE:\n{json.dumps(payload, indent=1)}\n"
    if blind_verdict is not None:
        head += (
            "\nYou already judged this scene blind; now the rule-checklist's verdict "
            "is revealed inside the scene (gates_verdict_head_a). CONFIRM or REVISE "
            "your verdict. If you stand against the checklist, say exactly why in an "
            "\"overrides\" list (short strings). Changing your answer just to match "
            "the checklist is a failure mode — only revise if the reveal genuinely "
            "changes the picture.\n"
            f"YOUR BLIND VERDICT: {json.dumps(blind_verdict)}\n"
        )
    tail = ("\nOutput ONLY this JSON object (no fences, no commentary):\n"
            + json.dumps({**_VERDICT_SHAPE, **({"overrides": ["why I stand against the gates"]}
                                               if blind_verdict is not None else {})}))
    return head + scene + tail


def validate_verdict(v) -> dict | None:
    """Schema guard — clamp the model's reply to the shape the diary and the
    grader expect. Returns None when the reply is unusable (wrong types /
    missing the decision itself), which the caller records as an error."""
    if not isinstance(v, dict) or not isinstance(v.get("fired"), bool):
        return None
    d = v.get("direction")
    if v["fired"] and d not in ("call", "put"):
        return None
    def _num(x, lo, hi):
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        return max(lo, min(hi, f)) if math.isfinite(f) else None   # reject NaN/Inf
    rng = v.get("range_sigma")
    rng = ([_num(rng[0], -5, 5), _num(rng[1], -5, 5)]
           if isinstance(rng, (list, tuple)) and len(rng) == 2 else None)
    return {"fired": v["fired"], "direction": d if v["fired"] else None,
            "magnitude_sigma": _num(v.get("magnitude_sigma"), 0, 5),
            "range_sigma": rng,
            "horizon_min": int(_num(v.get("horizon_min"), 5, 240) or 60),
            "conviction_stated": _num(v.get("conviction"), 0, 1),
            # N4: the fight/settle axis — clamped to the three honest answers; a missing
            # or junk stance records as no_read (never silently invented)
            "stance": (v.get("stance") if v.get("stance") in ("fight", "settle", "no_read")
                       else "no_read"),
            "scene": str(v.get("scene") or "")[:500],
            "would_change_mind": str(v.get("would_change_mind") or "")[:300],
            "overrides": [str(o)[:200] for o in
                          (v.get("overrides") if isinstance(v.get("overrides"), list) else [])][:5]}


def majority(votes: list[dict]) -> tuple[dict, float]:
    """VOTE FOLD — majority on fired, then majority direction among the firing
    votes; the winning vote's own record is kept for scene/magnitude. Returns
    (final_verdict, agreement) where agreement = share of votes that fully
    match the final fired+direction — this is the conviction number we trust."""
    fires = [v for v in votes if v["fired"]]
    fired = len(fires) > len(votes) / 2
    if fired:
        dirs = [v["direction"] for v in fires]
        counts = sorted(((dirs.count(x), x) for x in set(dirs)), reverse=True)
        # DIRECTION TIE (2026-07-12): a 1-1 call/put split used to be broken by SET
        # ITERATION ORDER — hash-randomized per process, and the scanner is one-shot,
        # so the shipped direction was a literal coin flip. A tied direction is no
        # majority at all: the honest verdict is stand down, tie visible in the votes.
        if len(counts) > 1 and counts[0][0] == counts[1][0]:
            fired, d = False, None
            winner = {**fires[0], "fired": False, "direction": None}
        else:
            d = counts[0][1]
            winner = next(v for v in fires if v["direction"] == d)
    else:
        d = None
        winner = next((v for v in votes if not v["fired"]), votes[0])
    agree = sum(1 for v in votes
                if v["fired"] == fired and (not fired or v["direction"] == d))
    # HONEST CONVICTION (07-09): one vote is trivially "unanimous" — a single
    # answer must not wear a 1.0 badge the graders and the tablet read as full
    # agreement. n==1 → None (unknown), and consumers already null-guard conv.
    if len(votes) == 1:
        return dict(winner), None
    return dict(winner), round(agree / len(votes), 2)


def render_snippet(rec: dict, t: dict, now: datetime, why: str) -> str:
    """THE OUTPUT — a fast, human-readable read of the scene that ends in the one
    thing that matters: what SPX does NEXT. Human-facing, so absolute levels are
    fine here (unlike the σ-only model payload). Four short lines; NEXT stands
    out on purpose."""
    spot = t.get("spot")
    # display magnet: the TRUE gravity pin, same source (and same order) as the
    # payload's magnet_sd — the snippet and the prior must never name different magnets.
    magnet = (t.get("gex_views") or {}).get("magnet")
    if magnet is None:
        magnet = (t.get("reversion_extreme") or {}).get("magnet")
    mood = {"positive": "sticky (pin-friendly)",
            "negative": "runny (breakout fuel)"}.get(t.get("gamma_sign"), "mixed read")
    see = f"SPX {spot:.0f}" if isinstance(spot, (int, float)) else "SPX —"
    see += f", {mood}"
    if isinstance(magnet, (int, float)):
        see += f"; magnet {magnet:.0f}"
    if rec.get("fired"):
        dword = "UP" if rec.get("direction") == "call" else "DOWN"
        mag = rec.get("magnitude_sigma")
        # wt-2 wording: gravity-following is the DEFAULT, so name the call by
        # its relation to the magnet — "ride" with the pull, "fade" against it.
        # (wt-1 rendered every fire as "fade … toward {magnet}", which read
        #  self-contradictory the moment a call pointed AWAY from the magnet.)
        verb, tgt = "move", ""
        if isinstance(magnet, (int, float)) and isinstance(spot, (int, float)):
            toward = (magnet >= spot) == (rec.get("direction") == "call")
            verb = "ride" if toward else "fade"
            tgt = (f" toward {magnet:.0f}" if toward
                   else f" against the {magnet:.0f} pull")
        nxt = f"{verb} {dword}{tgt}" + (f"  (~{mag}σ)" if mag else "")
    else:
        nxt = "no call — stand down / likely drift"
    lines = [f"🗼 {now.strftime('%H:%M')} · {why}",
             f"See:   {see}",
             f"Read:  {(rec.get('scene') or '—').strip()}",
             f"▶▶ NEXT:  {nxt}"]
    alt = (rec.get("would_change_mind") or "").strip()
    if alt:
        lines.append(f"      ↳ or:  {alt} → flips it")
    # MAGNET ZONES (2026-07-09): on a contested field, name the rival — two
    # magnets under one table usually means chop between them, not a clean run.
    try:
        zs = (t.get("gex_views") or {}).get("pin_zones") or []
        if (t.get("gex_views") or {}).get("pin_contested") and len(zs) >= 2:
            z1, z2 = zs[0], zs[1]
            if z1.get("share"):
                lines.append(f"      ⚠ contested — rival zone {z2['center']:.0f} "
                             f"pulling {z2['share'] / z1['share']:.2f}× of {z1['center']:.0f}")
    except Exception:
        pass
    return "\n".join(lines)


# ==============================================================================
# I/O — model call, config, day context
# ==============================================================================
def _model() -> str:
    """The pinned model id: config models.watchtower wins, else the constant.
    Kept as an exact id so a silent alias re-point can't change the judge."""
    try:
        m = (json.loads(CONFIG.read_text()).get("models") or {}).get("watchtower")
        return m or PINNED_MODEL
    except (OSError, json.JSONDecodeError, AttributeError):
        return PINNED_MODEL


def _extract_json(text: str):
    """First complete JSON object in `text` (models sometimes add prose/fences)."""
    if not text:
        return None
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text, i)
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _ask_claude(prompt: str, model: str, timeout: float = CALL_TIMEOUT_S):
    """ONE `claude -p` subprocess call — deliberately NOT the watch package's
    claude_cli wrapper: that one retries with backoff (3 attempts), which is
    right for a nightly brief and wrong inside a 5-minute scan tick. Here a
    slow call fails ONCE and the tick moves on (fail-open). Tool-less by
    construction: no --allowedTools, so the model can only read the prompt.
    Returns (verdict_dict | None, error | None, wall_seconds)."""
    cmd = ["claude", "-p", prompt, "--model", model,
           "--output-format", "json"]     # envelope carries the served model id
    t0 = _clock.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout", round(_clock.time() - t0, 1)
    except OSError as e:
        return None, f"spawn: {e}", round(_clock.time() - t0, 1)
    wall = round(_clock.time() - t0, 1)
    if r.returncode != 0:
        return None, f"rc={r.returncode}: {(r.stderr or '')[-200:]}", wall
    env = _extract_json(r.stdout)
    text = env.get("result") if isinstance(env, dict) and "result" in env else r.stdout
    return _extract_json(text if isinstance(text, str) else r.stdout), None, wall


def _today_rows(now: datetime) -> list[dict]:
    """Today's diary parsed once into row dicts (unparseable lines skipped) —
    the single reader shared by the payload context and the trigger last-look."""
    f = STATE_DIR / "reversion" / f"{now.date().isoformat()}.jsonl"
    rows: list[dict] = []
    try:
        for ln in f.read_text().splitlines():
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows


def _soft_side_stuck(now: datetime | None, current) -> bool:
    """wt-8 STUCK-VANE GUARD — gex_motion.soft_side read "down" on ~97% of rows for
    three straight days (07-21→23) and the tower weighted every row as today's
    opinion. A vane that never turns is describing the terrain, not the wind.
    Collect the last SOFT_SIDE_LOOKBACK non-null soft_side readings from the diary
    (today's rows newest-first, walking back prior calendar days), and call the vane
    STUCK when the current value matches more than SOFT_SIDE_STUCK_SHARE of them.
    Fail-open: no clock, thin history, or unreadable files → not stuck."""
    if now is None or current not in ("up", "down"):
        return False
    reads: list[str] = []
    for back in range(6):                   # today + up to 5 prior days (spans a weekend)
        f = STATE_DIR / "reversion" / f"{(now - timedelta(days=back)).date().isoformat()}.jsonl"
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        day_reads = []
        for ln in lines:
            try:
                s = (json.loads(ln).get("gex_motion") or {}).get("soft_side")
            except (json.JSONDecodeError, AttributeError):
                continue
            if s in ("up", "down"):
                day_reads.append(s)
        reads.extend(reversed(day_reads))   # newest reading first
        if len(reads) >= SOFT_SIDE_LOOKBACK:
            break
    reads = reads[:SOFT_SIDE_LOOKBACK]
    if len(reads) < SOFT_SIDE_MIN_READS:
        return False
    return reads.count(current) / len(reads) > SOFT_SIDE_STUCK_SHARE


def _was_call(wt: dict) -> bool:
    """ONE source of truth for 'this row spent a model call' — votes recorded,
    or an errored call that still logged wall_s (errored calls burn budget too:
    a stuck model must not get 78 shots at 60s each). Drives BOTH the daily-cap
    counter and the heartbeat clock, so the two can never disagree."""
    return bool(wt.get("votes")) or wt.get("wall_s") is not None


def _today_context(now: datetime) -> tuple[list[dict], int]:
    """(my last ≤6 verdicts — the tower's short memory, each stamped with how it is
    DOING so far: working_sigma_since = the move price has made in that call's OWN
    direction since it was made, in σ (+ = the call is working, − = it is failing) —
    so the tower can notice a persistently-failing thesis instead of repeating it) and
    how many scans already spent model calls (the daily-cap counter, derived)."""
    rows = _today_rows(now)
    cur_spot = next((r.get("spot") for r in reversed(rows)
                     if isinstance(r.get("spot"), (int, float))), None)
    verdicts, calls, judged = [], 0, []
    for row in rows:
        wt = row.get("watchtower") or {}
        if _was_call(wt):
            calls += 1
        # same-era memory only (wt-4): a prompt/payload era judged scenes with
        # different facts — feeding its verdicts into day_context would poison
        # the new era's thesis memory on the bump day. calls still count ALL
        # eras (the daily cap is a spend cap, not a ledger).
        if wt.get("votes") and wt.get("prompt_version") == PROMPT_VERSION:
            judged.append((row, wt))
    for row, wt in judged[-6:]:
        d = wt.get("direction")
        v = {"fired": wt.get("fired"), "direction": d, "conviction": wt.get("conviction")}
        try:
            v["at"] = row["ts"][11:16]
        except (KeyError, TypeError):
            pass
        entry, sig = row.get("spot"), row.get("sigma")
        if (isinstance(entry, (int, float)) and isinstance(sig, (int, float)) and sig
                and cur_spot is not None and d in ("call", "put")):
            sign = 1 if d == "call" else -1
            v["working_sigma_since"] = round(sign * (cur_spot - entry) / sig, 2)
        verdicts.append(v)
    return verdicts, calls


def _session_path(now: datetime, telemetry: dict) -> dict | None:
    """The day's price trend so far, so the tower forecasts WITH the day, not blind to
    it: open / now / high / low and the net move since the open in σ. The current scan
    is not on disk yet, so its spot is folded in from the live telemetry."""
    spots = [r.get("spot") for r in _today_rows(now)
             if isinstance(r.get("spot"), (int, float))]
    cur, sig = telemetry.get("spot"), telemetry.get("sigma")
    if isinstance(cur, (int, float)):
        spots.append(cur)
    if not spots:
        return None
    net = (round((spots[-1] - spots[0]) / sig, 2)
           if isinstance(sig, (int, float)) and sig else None)
    trend = ("up" if net is not None and net > 0.15 else
             "down" if net is not None and net < -0.15 else "sideways")
    return {"open": round(spots[0], 2), "now": round(spots[-1], 2),
            "high": round(max(spots), 2), "low": round(min(spots), 2),
            "net_move_since_open_sigma": net, "trend": trend}


def _last_look(now: datetime) -> tuple[float | None, dict | None]:
    """From today's diary: (minutes since the last JUDGED scan | None if none
    yet, fingerprint of the PREVIOUS scan's scene | None). Drives the heartbeat
    (time since last call) and the interrupt (change vs the last scene). One
    read; the current scan is not on disk yet, so the last row IS the prior."""
    last_call_dt, prev_fp = None, None
    for row in _today_rows(now):
        prev_fp = scene_fingerprint(row)                   # last row wins
        if _was_call(row.get("watchtower") or {}):
            try:                                           # keep the last PARSEABLE
                last_call_dt = datetime.fromisoformat(row.get("ts"))
            except (ValueError, TypeError):
                pass                                       # malformed ts ≠ wipe clock
    mins = (now - last_call_dt).total_seconds() / 60.0 if last_call_dt else None
    return mins, prev_fp


def _scorecards() -> dict:
    """Tiny lifetime summary of both heads from the history ledger — the tower
    knows its own and the checklist's track record (empty early on; fine)."""
    out = {}
    hist = STATE_DIR / "reversion" / "polarity_history.jsonl"
    try:
        tallies = {"gates_head_a": [0, 0], "watchtower_head_b": [0, 0]}
        for ln in hist.read_text().splitlines():
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            for label, key in (("gates_head_a", "lens"),
                               ("watchtower_head_b", "watchtower_sweep")):
                c = rec.get(key) or {}
                # PROMPT-ERA SEGMENTATION (07-09): the tower's own record only
                # counts days graded under the CURRENT prompt — a bump really
                # does reset what it is shown about itself. Head B reads the
                # SWEEP card (2026-07-17, wins/losses = area+duration verdicts;
                # mixed days excluded); legacy trade cards are a retired exam
                # and never pool in. Head A is unversioned.
                if key == "watchtower_sweep":
                    if c.get("prompt_version") != PROMPT_VERSION:
                        continue
                    tallies[label][0] += c.get("wins") or 0
                    tallies[label][1] += (c.get("wins") or 0) + (c.get("losses") or 0)
                else:
                    tallies[label][0] += c.get("wins") or 0
                    tallies[label][1] += c.get("n") or 0
        for label, (w, n) in tallies.items():
            if n:
                out[label] = {"wins": w, "n": n, "win_rate": round(w / n, 2)}
    except OSError:
        pass
    return out


def _macro_mood() -> dict | None:
    """The morning brief's one-line mood, if present (shadow tilt context)."""
    try:
        m = json.loads((STATE_DIR / "market_expectation" / "latest.json").read_text())
        ov = m.get("overall") or {}   # writer nests the read under "overall" (macro_mood.build_expectation)
        return {"direction": ov.get("direction"), "confidence": ov.get("confidence")}
    except (OSError, json.JSONDecodeError):
        return None


# ==============================================================================
# The one entry point the scan calls
# ==============================================================================
def _market_is_live(now: datetime) -> bool:
    """Intrinsic market-hours guard — the tower never judges or spends a model
    call outside a weekday 09:30-16:00 ET, HOWEVER the scan is invoked (a manual
    hunter.py run, a future scheduler, …). The launchd path is already gated on
    the authoritative holiday-aware market_status in run-watch-left-eye.sh; this
    is belt-and-suspenders on top of it, from the scan's own clock."""
    t = now.astimezone(ET)
    minutes = t.hour * 60 + t.minute
    return t.weekday() < 5 and (9 * 60 + 30) <= minutes < (16 * 60)


def observe(telemetry: dict, now: datetime | None = None) -> dict | None:
    """JUDGE ONE SCAN. Called from reversion_lens.evaluate() after the row is
    built; the return value is stored at telemetry["watchtower"]. NEVER raises
    and never blocks past its budget — any failure is a small record, and the
    scan continues exactly as if the tower didn't exist (fail-open, shadow)."""
    try:
        if os.environ.get("WATCHTOWER_DISABLE"):
            return None                                   # tests / kill-switch
        now = now or datetime.now(tz=ET)
        if not _market_is_live(now):
            return None                                   # RTH-only, however invoked
        rev = telemetry.get("reversion_extreme") or {}
        base = {"prompt_version": PROMPT_VERSION}
        ok, why = interesting(rev)
        if ok:
            # HELD-SCENE THROTTLE (07-09): interesting alone no longer burns a
            # call every scan — the same armed picture, re-judged every 5 min,
            # exhausted the whole daily cap by 13:03 two sessions out of three.
            # Judge each NEW slice of time, hold the thesis across an unchanged
            # one: re-judge only after INTEREST_REJUDGE_MIN, or immediately on
            # a genuine scene change.
            mins_since, prev_fp = _last_look(now)
            if mins_since is not None and mins_since < INTEREST_REJUDGE_MIN:
                changed, creason = big_change(prev_fp, scene_fingerprint(telemetry))
                if not changed:
                    return {**base, "skipped": "held"}    # thesis stands, no call
                why = f"{why} · {creason}"
        if not ok:
            # HYBRID WAKE: an interesting scan judges as before; an otherwise
            # quiet scan still judges on the hourly HEARTBEAT, or early on a big
            # scene SHIFT (interrupt), throttled by the min-gap spam guard.
            mins_since, prev_fp = _last_look(now)
            if mins_since is None or mins_since >= HEARTBEAT_MIN:
                ok, why = True, "hourly heartbeat"
            else:
                changed, creason = big_change(prev_fp, scene_fingerprint(telemetry))
                if changed and mins_since >= INTERRUPT_MIN_GAP:
                    ok, why = True, f"shift · {creason}"
        if not ok:
            return {**base, "skipped": "quiet"}           # tiny row, no calls
        recent, calls_today = _today_context(now)
        if calls_today >= DAILY_CALL_CAP:
            return {**base, "skipped": "cap"}
        model = _model()
        t_start = _clock.time()

        payload = build_payload(telemetry, reveal_gates=False)
        payload["day_context"] = {"macro_mood": _macro_mood(),
                                  "session": _session_path(now, telemetry),
                                  "my_recent_verdicts": recent,
                                  "lifetime_scorecards": _scorecards()}

        # --- pass 1: BLIND vote(s) — majority decides ------------------------
        votes, walls = [], 0.0
        raw, err, wall = _ask_claude(_prompt(payload), model)
        walls += wall
        v = validate_verdict(raw)
        if v is None:
            return {**base, "model": model, "error": err or "bad verdict",
                    "wall_s": round(walls, 1)}
        votes.append(v)
        gates_fired = bool(rev.get("fired"))
        # extra votes when the answer MATTERS: the tower wants to fire, or it
        # is standing against gates that fired (a veto needs the same rigor)
        while (len(votes) < VOTES_ON_FIRE
               and (votes[0]["fired"] or gates_fired)
               and _clock.time() - t_start < TICK_BUDGET_S - CALL_TIMEOUT_S):
            raw, err, wall = _ask_claude(_prompt(payload), model)
            walls += wall
            v = validate_verdict(raw)
            if v is not None:
                votes.append(v)
        final, agreement = majority(votes)
        blind = {"fired": final["fired"], "direction": final["direction"],
                 "stance": final.get("stance"),
                 "conviction_stated": final["conviction_stated"]}

        # --- pass 2: REVEAL, only on disagreement ----------------------------
        # (agreement with the gates needs no second look; a disagreement gets
        #  one confirm-or-revise pass with the gates now visible)
        disagree = (final["fired"] != gates_fired or
                    (final["fired"] and gates_fired
                     and final["direction"] != rev.get("direction")))
        revised = False
        reveal_dissent = None
        if disagree and _clock.time() - t_start < TICK_BUDGET_S - CALL_TIMEOUT_S:
            reveal_payload = build_payload(telemetry, reveal_gates=True)
            reveal_payload["day_context"] = payload["day_context"]
            raw, err, wall = _ask_claude(_prompt(reveal_payload, blind_verdict=blind), model)
            walls += wall
            v = validate_verdict(raw)
            if v is not None:
                flips = (v["fired"] != final["fired"]
                         or v["direction"] != final["direction"])
                # N19 (wt-5): ONE reveal sample may not delete a UNANIMOUS multi-vote
                # blind majority — on 07-10 every one of the 5 revisions was the tower
                # abandoning its own fire to agree with the gates (the independent
                # second opinion, deleted by a single capitulating sample). The dissent
                # is recorded, visibly, and the majority stands. A non-unanimous blind
                # verdict may still be revised — and then the shipped conviction is
                # re-folded over ALL samples so it describes the call it ships beside.
                unanimous = (len(votes) >= 2 and agreement is not None
                             and agreement >= 0.99)
                if flips and unanimous:
                    reveal_dissent = {"fired": v["fired"], "direction": v["direction"],
                                      "overrides": v["overrides"]}
                elif flips:
                    final = v
                    revised = True
                    allv = votes + [v]
                    agreement = round(sum(
                        1 for x in allv
                        if x["fired"] == v["fired"]
                        and (not v["fired"] or x["direction"] == v["direction"])
                    ) / len(allv), 2)
                else:
                    final = v          # same call, refreshed scene/magnitude

        agrees = (final["fired"] == gates_fired and
                  (not final["fired"] or final["direction"] == rev.get("direction")))
        result = {**base, "model": model,
                  "fired": final["fired"], "direction": final["direction"],
                  "magnitude_sigma": final["magnitude_sigma"],
                  "range_sigma": final["range_sigma"],
                  "horizon_min": final["horizon_min"],
                  "stance": final.get("stance"),             # N4: fight / settle / no_read
                  "conviction": agreement,                   # the number we trust — and it
                  "conviction_stated": final["conviction_stated"],  # describes the SHIPPED call (N19)
                  "scene": final["scene"],
                  "would_change_mind": final["would_change_mind"],
                  "overrides": final["overrides"],
                  "votes": {"n": len(votes), "agreement": agreement},
                  "blind": blind, "revised": revised,
                  **({"reveal_dissent": reveal_dissent} if reveal_dissent else {}),
                  "agrees_with_gates": agrees,
                  "interest": why, "wall_s": round(walls, 1)}
        try:                                     # snippet must never break a row
            result["snippet"] = render_snippet(result, telemetry, now, why)
        except Exception:                        # noqa: BLE001
            pass
        return result
    except Exception as e:  # noqa: BLE001 — shadow head must NEVER break a scan
        return {"prompt_version": PROMPT_VERSION, "error": f"{type(e).__name__}: {e}"}


# ==============================================================================
# Replay — the offline golden-set runner (reads a past diary day, no state writes)
# ==============================================================================
def _replay(day: str, live: bool) -> None:
    f = STATE_DIR / "reversion" / f"{day}.jsonl"
    rows = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
    print(f"replay {day}: {len(rows)} rows — payloads for the interesting scans\n")
    for r in rows:
        ok, why = interesting(r.get("reversion_extreme") or {})
        if not ok:
            continue
        ts = (r.get("ts") or "")[11:16]
        print(f"--- {ts}  interest: {why}")
        if live:
            v, err, wall = _ask_claude(_prompt(build_payload(r)), _model())
            print(json.dumps(validate_verdict(v) or {"error": err}, indent=1), f"({wall}s)")
        else:
            print(json.dumps(build_payload(r), indent=1)[:800], "…\n")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--replay" in args:
        day = next((a for a in args if a[:2] != "--"), None)
        _replay(day or datetime.now(ET).date().isoformat(), live="--live" in args)
    else:
        print(__doc__)
