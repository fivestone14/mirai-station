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
The nightly report card (gex_polarity_ab._grade_watchtower) grades "fired" +
"direction" with the exact same target-before-stop rules as Head A, and the
magnitude prediction against the recorded move_60m / mfe_sigma vector.

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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent
STATE_DIR = SKILL_DIR.parent.parent / "state"
CONFIG = SKILL_DIR.parent.parent / "runtime" / "watch" / "config" / "limits-and-cooldowns.json"

# --- knobs -------------------------------------------------------------------
PROMPT_VERSION = "wt-5"     # bump on ANY prompt change — a bump resets the record
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
# dated_structure block (far monthlies/quarter-end, σ-distances, unsigned, ≤3σ
# only) gives the tower the outer terrain for the first time. Payload change =
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


def _motion_words(motion: dict) -> dict:
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
        out["soft_side"] = (f"{s.upper()} — less gravity mass {'above' if s == 'up' else 'below'} "
                            "spot; a flow shove meets the least resistance that way")
    return out


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
    event_clock = None
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
    # N5 + N6 (wt-5): the WINDOWED tape leads; the whole-day cumulative is labeled a
    # day average (it never decays and never turns — at 15:30 on a pure-selling
    # afternoon it still read "+0.03, buyers"); and a FLAT read of either confirms
    # NOTHING (free confidence on noise was the old failure).
    flow = gx.get("aggressor_flow")
    fr = (t.get("gex_theta") or {}).get("flow_recent")
    def _tape_word(x, hard_at):
        if x is None:
            return None
        if abs(x) < 0.05:
            return f"{x:+.2f} — FLAT (a flat tape confirms NOTHING either way)"
        return (f"{x:+.2f} ({'buyers' if x > 0 else 'sellers'} hitting the tape"
                f"{' HARD' if abs(x) >= hard_at else ''})")
    flow_recent_word = _tape_word(fr, 0.5)
    flow_word = _tape_word(flow, 0.6) or "no live flow read"
    # GRAVITY MOTION (the map as film, not photo) — the diary's gex_motion diff
    # (wall melt / magnet walk / flip creep / grip slope / soft side), attached
    # when this scan produced one. Defensive value first: "the fade leans on a
    # melting wall — stand down" is exactly the scene read the tower is for.
    motion = t.get("gex_motion") if isinstance(t.get("gex_motion"), dict) else None
    # explicit gravity anchor: which way the map pulls (magnet vs spot) is the DEFAULT
    # directional prior — spelled out so the read starts from the pull, not the stretch.
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
    elif magnet_sd > 0.05:
        pull_word = f"UP — magnet is {magnet_sd:+.2f}σ ABOVE spot; base case is a drift UP (call)"
    elif magnet_sd < -0.05:
        pull_word = f"DOWN — magnet is {magnet_sd:+.2f}σ BELOW spot; base case is a drift DOWN (put)"
    else:
        pull_word = "NEUTRAL — spot sits on the magnet; two-sided, no gravity edge"
    # N15 (wt-5): the magnet-pull prior only RULES under long gamma. Under short gamma
    # dealer hedging amplifies every move away from balance — the magnet is a LOCATION,
    # not a target, and drift-to-the-magnet is exactly the read the tower must not default to.
    if g == "negative" and magnet_sd is not None:
        pull_word += (" — CAUTION: dealers are SHORT gamma today, so this pull is NOT the "
                      "default; amplification rules (see gamma_sign_at_spot)")
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
    # (≤3σ) — a 9σ crash strike in the payload would re-teach the exact
    # far-wall anchoring H2 just removed. age_days rides along so a stale wall
    # is never mistaken for a fresh fact.
    dated_bands = []
    for b in ((t.get("dated_gex") or {}).get("bands") or []):
        _cw, _pw = _sd(b.get("call_wall"), spot, sigma), _sd(b.get("put_wall"), spot, sigma)
        row = {"band": b.get("band"), "days_to_expiry": b.get("dte"),
               **({"age_days": b.get("age_days")} if b.get("age_days") is not None else {}),
               **({"call_wall_sigma_above": _cw} if _cw is not None and abs(_cw) <= 3 else {}),
               **({"put_wall_sigma_below": _pw} if _pw is not None and abs(_pw) <= 3 else {})}
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
    lr = t.get("level_reclaim") or {}
    _bstate = lr.get("break_state")
    _bdir = lr.get("direction") if lr.get("direction") not in (None, "none") else lr.get("cock_direction")
    break_lens: dict = {}
    if lr.get("fired") or (_bstate not in (None, "idle")) or lr.get("cock_direction"):
        break_lens = {k: v for k, v in {
            "break_state": _bstate or ("fired" if lr.get("fired") else None),
            "escape_direction": _bdir,
            "reclaim_level_sigma_from_spot": _sd(lr.get("level") or lr.get("cock_level"), spot, sigma),
            "level_kind": lr.get("level_kind") or lr.get("cock_level_kind"),
            "flips_regime": lr.get("flips_regime") or None,
            "note": "an active break = the map may be failing to contain price (trend/escape "
                    "risk); do NOT fade into a confirmed break.",
        }.items() if v is not None}
    payload = {
        "task": "You are the Watchtower: forecast where 0DTE SPX goes over the next hour — gravity first.",
        "clock": {"minutes_since_open": mins_open, "minutes_to_close": mins_close},
        "dealer_map_gravity": {
            "pull_toward_magnet": pull_word,
            "gamma_sign_at_spot": gamma_word,
            "magnet_sigma_from_spot": magnet_sd,
            # the Fade lens's wall-clamped revert-target — present only when the tape
            # is armed; a fade context read, NOT the gravity pull (absent, never null)
            **({"fade_revert_target_sigma": fade_target_sd,
                **({"fade_target_wall_clamped": True}
                   if rev.get("magnet_out_of_band") else {})}
               if fade_target_sd is not None else {}),
            "gamma_flip_sigma_from_spot": _sd(t.get("gamma_flip"), spot, sigma),
            # NEAR walls: today's 0DTE gamma walls (H2) — the bounds price actually
            # fights this session; the structural band walls follow as outer terrain
            "call_wall_sigma_above": _sd(t.get("call_wall"), spot, sigma),
            "put_wall_sigma_below": _sd(t.get("put_wall"), spot, sigma),
            **({"structural_call_wall_sigma_above": tenor_cw_sd}
               if tenor_cw_sd is not None else {}),
            **({"structural_put_wall_sigma_below": tenor_pw_sd}
               if tenor_pw_sd is not None else {}),
            "pin_top_share": gx.get("pin_top_share"),
            "vanna_sign": gx.get("vex_sign"), "charm_sign": gx.get("cex_sign"),
            **({"charm_drift_into_close": charm_drift} if charm_drift else {}),
        },
        # N5 (wt-5): the windowed tape leads; the day average is explicitly labeled one
        "live_flow": {
            **({"tape_recent_window": flow_recent_word} if flow_recent_word else {}),
            "tape_day_average": flow_word + " — WHOLE-DAY average: it never decays and "
                                            "cannot show a turn; prefer tape_recent_window",
        },
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
        **({"gravity_motion": _motion_words(motion) or motion} if motion else {}),
        **({"break_lens_head_c": break_lens} if break_lens else {}),
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
        "- THE DEFAULT DEPENDS ON THE GAMMA SIGN — check gamma_sign_at_spot FIRST.\n"
        "  * Dealers LONG gamma (positive): GRAVITY RULES. Price drifts toward the MAGNET and "
        "is bounded by the walls. Start from pull_toward_magnet: magnet above spot → base case "
        "UP (call); below → DOWN (put). Base stance: settle.\n"
        "  * Dealers SHORT gamma (negative): AMPLIFICATION RULES. Hedging pushes price AWAY "
        "from balance — the magnet is a LOCATION, not a target, and drift-to-the-magnet is the "
        "one read you must NOT default to. Follow the tape/break direction; expect range "
        "expansion. Base stance: fight.\n"
        "  * Sign UNKNOWN (tie/blackout): a balanced book is a HAIR-TRIGGER, not a calm one — "
        "no directional default, stance leans fight-risk, conviction low.\n"
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
        "- MEAN-REVERSION IS THE EXCEPTION, not the default. Only call AGAINST the magnet-pull "
        "when the tape is extremely stretched into a NEAR wall with a turn bar and flow rolling "
        "over — otherwise go WITH the pull. Do NOT fade a trend that is heading toward the magnet.\n"
        "- CONFIRM WITH THE TAPE — but a FLAT tape confirms NOTHING. live_flow.tape_recent_window "
        "(the last minutes) outranks tape_day_average (a whole-day cumulative that cannot show a "
        "turn). order_flow.book_tilt and an active break_lens_head_c either confirm or contradict "
        "the pull. Any read marked FLAT adds zero confidence to any thesis. NEVER fade into a "
        "confirmed break; be wary of calling against strong book flow. All attached only when "
        "present; absence ≠ zero.\n"
        "- CHARM IS THE CLOCK'S TUG — and it grips hardest near the bell. "
        "dealer_map_gravity.charm_drift_into_close is precomputed (direction included — do not "
        "re-derive it from charm_sign): it names which way dealer hedge re-balancing pressures "
        "price as time decays. Before ~13:00 ET treat it as background. In the afternoon weight "
        "it as a real vote on the drift call — it usually SHARPENS the magnet-pull and can break "
        "a NEUTRAL tie; it does not override a hard break or heavy one-way flow.\n"
        "- READ THE MAP IN TENOR ORDER: today's 0DTE book first (the walls and magnet — they are "
        "what dealers re-balance TODAY), the structural_*_wall band walls second (multi-day "
        "terrain), dated_structure last (far monthly/quarter-end OI shelves — react to them only "
        "when spot is near one; they are not targets and their sign is unknown).\n"
        "- direction: \"call\" = you expect price UP over the horizon, \"put\" = DOWN. Set "
        "fired=false (direction null) when you have no directional edge — but ALWAYS still "
        "answer stance (a two-sided scene that will stay pinned is settle, not no_read).\n"
        "- HOLD YOUR THESIS. day_context.my_recent_verdicts lists your recent calls, each with "
        "working_sigma_since = how far price has since moved in that call's favor (negative = it "
        "is failing). day_context.session shows the trend since the open. Keep your DIRECTION "
        "across scans and adjust magnitude/conviction on small moves. FLIP direction only if the "
        "STRUCTURE changed (magnet crossed spot, gamma sign flipped, flow flipped hard) OR your "
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
        d = max(set(dirs), key=dirs.count)
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
            for label, key in (("gates_head_a", "lens"), ("watchtower_head_b", "watchtower")):
                c = rec.get(key) or {}
                # PROMPT-ERA SEGMENTATION (07-09): the tower's own record only
                # counts days graded under the CURRENT prompt — a bump really
                # does reset what it is shown about itself. Unstamped legacy
                # days (wt-1 era) are excluded from head B. Head A is unversioned.
                if key == "watchtower" and c.get("prompt_version") != PROMPT_VERSION:
                    continue
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
