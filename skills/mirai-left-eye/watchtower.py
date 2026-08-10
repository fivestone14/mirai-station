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
import sys
import time as _clock
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent
STATE_DIR = SKILL_DIR.parent.parent / "state"
CONFIG = SKILL_DIR.parent.parent / "runtime" / "watch" / "config" / "limits-and-cooldowns.json"

# --- knobs -------------------------------------------------------------------
PROMPT_VERSION = "wt-11"    # bump on ANY prompt change — a bump resets the record
# wt-11 (2026-08-10): THE SNDK sr-2→sr-5 PORT — the honesty upgrades the SNDK reader
# proved on its own tape, re-measured on THIS one before shipping (every threshold below
# is from SPX's own recorded rows, never copied):
# (1) dex tells no constant: naive_net_sign REMOVED — net_dex_total > 0 by construction,
#     measured positive on 2,627/2,627 recorded rows, and a field the doctrine tells you
#     to ignore is a field that must not ship (the SNDK dex_word lesson, third form). The
#     news is the CHANGE: net_change_30min (timestamp-true off today's rows) now rides.
# (2) charm/vanna stop wearing constant signs: vanna_sign/charm_sign REMOVED (vex_sign
#     96% positive, cex_sign 97% negative on 5,092 rows — near-constants that read like
#     signals) and charm_drift_into_close REMOVED (its charm_word_ok gate has never once
#     opened: False/None on 5,176/5,176 rows — a word that has never shipped is dead
#     weight in the room). Both flows now ship as NEUTRAL magnitude + level blocks
#     (charm: strength + the strike the clock funnels toward; vanna: strength, armed by
#     vol_trend) — N11's final form: no direction word off a structurally-locked sign.
# (3) the magnet band tells the tie: magnet_band {gap_pp, top_strikes (σ-from-spot +
#     share), sigma_from_spot} — the SPX magnet is a statistical TIE most of the day
#     (median top1−top2 gap 0.99pp of mass, n=4,258 scans); pin_top_share alone hid it.
# (4) vol_trend — 30-min ATM-IV change in vol pts (the switch that arms vanna/charm),
#     flat band 2.0 pts ≈ p72 of the recorded |Δ30m| (n=3,390 windows), omitted on a
#     late_day ruler (the τ→0 solve balloons on clock mechanics, not vol).
# (5) momentum — per-strike gamma-share deltas for the top magnet strikes over the last
#     5 scans, intersection-denominated (a strike window shift is the telescope, not
#     flow); build/fade bar 0.15pp ≈ p75 of the recorded |Δshare| (n=4,064).
# (6) the measured 30-min spread replaces silence on size: scale.move_30min_sigma
#     {half_under 0.08 / one_in_five_over 0.15 / one_in_twenty_over 0.27 / worst 0.46,
#     30 sessions, disjoint windows} + tape.moved_last_30min_sigma (timestamp-true) —
#     a spread teaches a distribution where a point teaches a ceiling (sr-5).
# (7) measured-empty stops impersonating unmeasured (sr-5): a cluster surface that was
#     READ and holds nothing on one side ships wall_cluster_above/below_clear — price
#     in open air is a real reading, often the loudest; absence stays "not measured".
# (8) THE MEMORY OPENS (sr-2): exactly two tools un-banned — the lefteye_rag history
#     CLI (one allow-listed Bash prefix; slices of the tower's own past reads, day
#     summaries, month terrain, numeric series) and WebSearch for the abnormal-tape
#     catalyst check. payload.history {level_unseen_today, abnormal_tape} is the
#     under-pull guard. Doctrine: history is context, never the trigger. The deny-list
#     stays for everything else, so the schema bill stays small (the 07-31 A/B's cost
#     finding holds; its fired-rate kill condition keeps running unchanged).
# (9) clock gains DATE (sr-4) — a doctrine AMENDMENT, documented: the no-dates rule was
#     written when golden replays fed in-training-window scenes to a model that has SPX
#     history memorized; live dates now sit past the pinned model's cutoff, and the
#     history CLI's --date/--from-date filters are unusable without today's date. The
#     no-ABSOLUTE-LEVELS rule stands untouched — the scene still speaks only σ; the
#     model meets levels only if it chooses to query history.
# (10) scale.aem — today's expected move split by the side the tape fears, from
#     adaptive_em, shipped ONLY when its asym_source is not the tower itself (a model
#     fed its own prior range as evidence is a feedback loop, not a measurement).
# wt-10 (2026-07-27): the three remaining chart-visible doctrine reads the tower could not
# see, all COLOR-tier advisory/record-only (no gate reads any of them), batched into one era
# bump (wt-9 never ran a live scan, so the clock resets at zero cost):
# (1) flow_confirmation — §7 standing OI vs live volume AT the operative walls: is today's
#     tape actually fighting at each wall (live battleground) or is the wall untested
#     standing inventory? Plus the volume king when today's heaviest strike is at neither
#     wall. Relative shares/ranks only (uniform-share baseline, no absolute contract counts
#     as signals), thin-tape abstain floor, σ-distances only.
# (2) transition_zone_measured — §4's cT/pT fences MEASURED from net_by_strike: the first
#     strike each side of the flip where one-signed control decisively resumes (θ = a share
#     of the surface's peak |net|). Three-state read (above cT / INSIDE / below pT), zone
#     width as the ambiguity-depth signal, missing fences named honestly (§4.6). The fixed
#     ±0.25σ chop band stays as the fallback heuristic; the measured zone refines it.
# (3) tape.expected_move_decay — §6.5: remaining reach ≈ √(time-left) share of the full-day
#     EM band, worded as a SIZE cap (shrink magnitude/range late day), never a direction.
#     Pure clock math (no store dependency); silent for the first hour.
# (4) strike_tags — the §3.4 doctrine tag ladder (same rank/argmax rules as the
#     viewstation TABLE tag column, GXTAG in index.html): ranked gamma walls P1-P6/C1-C3 +
#     Ab1-Ab3, OI peaks POI/COI/AbOI/nPOI, volume peaks CV/PV/nCV/nPV, delta-pressure
#     peaks D+/D−, all as σ-distances. Confluence doctrine (§3.3): several lenses on ONE
#     level = independent corroboration, outranks bar length. OI side-splits arrive with
#     the 07-27 gex_box build (oi_side_by_strike); volume side-splits also derive exactly
#     from net+gross (call=(g+n)/2) so CV/PV work on older rows; net tags fire only on a
#     strike that GENUINELY carries the sign (the viewstation's argmaxPos rule — a
#     one-sided book can never mislabel). Added pre-first-live, so wt-10 stays wt-10.
# wt-9 (2026-07-27): three COLOR-tier doctrine fold-ins added to the payload+prompt as
# advisory/record-only reads (no gate reads them): regime_intensity_1d (1-day net-γ trend),
# siege role (PAUSE vs TERMINUS target lean), and air_pocket_above/below (extend the
# objective across a thin gap). (A fourth, a DEX tape_vs_positioning_conflict word, was built
# then REMOVED — see the NOTE in the dex block.) Prompt changed → ledger resets from wt-8.
# wt-8 CORRECTIONS (2026-07-26, PRE-FIRST-LIVE): a guarded-generality sweep landed on the
# wt-8 payload BEFORE its first judged live scan (first live is Monday), so there was NO
# ledger to reset. (SUPERSEDED 2026-07-27: the wt-9 fold-ins changed the prompt, so the era
# WAS bumped to "wt-9" above — still at zero cost, the wt-8 clock never ticked.)
# Guiding principle: the payload must be THOROUGH but generally CORRECT
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
CALL_TIMEOUT_S = 100.0      # hard per-call limit — one attempt, no retries
                            # (07-05 golden replay: median call 45s, and every
                            #  error in the set was a 60s timeout — 90s cleared
                            #  the observed tail; wt-11 raises to 100s because
                            #  a call may now spend a history lookup or an
                            #  external search inside itself. The tick budget
                            #  still caps the total; votes degrade gracefully)
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
# wt-11: Bash and WebSearch left this list — they are the sr-2 on-demand paths,
# granted back below as ONE allow-listed CLI prefix + the catalyst search. A tool
# may never sit on both lists (the CLI errors on the contradiction).
_NO_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob",
             "Grep", "WebFetch", "Task", "Agent", "TodoWrite",
             "ExitPlanMode", "BashOutput", "KillShell", "SlashCommand", "Skill")
# sr-2 on-demand paths (wt-11): the ONE Bash command the tower may run (the
# lefteye_rag history CLI) and WebSearch for the abnormal-tape catalyst check.
# sys.executable pins the venv the tower itself runs in — the embedder lives there.
_RAG_CMD = f"{sys.executable} {SKILL_DIR / 'lefteye_rag.py'}"
_ALLOWED_TOOLS = (f"Bash({_RAG_CMD}:*)", "WebSearch")
DENY_TOOLS = True           # ON since 07-31. The tower never calls a tool, so it
                            # stops paying to ship their schemas: 50.3k -> 34.1k
                            # tokens a call (-32%), read block 43.0k -> 30.9k.
                            # The 07-29 test said no on the ground that tools-off
                            # buys a shallower think (wall 44.0s -> 15.7s, stated
                            # conviction 0.38 -> 0.31, n=6 on ONE marginal scene).
                            # That does not survive width. ab_deny_tools.py, 144
                            # paired samples, 24 scenes, 6 sessions, arms
                            # interleaved (run-2026-07-31T093110):
                            #   conviction   0.325 -> 0.312  (-4%, was -18%)
                            #   self-consist 0.958 -> 0.972  (no loss of stability)
                            #   stance hit   0.556 -> 0.563  (median-split bar)
                            #   modal verdict identical on 21 of 24 scenes
                            # Wall (26.4 -> 19.2s) and output (2046 -> 1422 tok) do
                            # fall, but the think being shorter did not make it worse
                            # on any axis the tape can score.
                            # THE ONE REAL DIFFERENCE — fire rate 12/72 -> 5/72. All
                            # three scenes that changed went the same way: tools-on
                            # fired a PUT, tools-off stood down. Tools-off is the more
                            # conservative tower. The sample cannot say whether that is
                            # a loss: directional hits ran 1/12 (on) vs 1/5 (off), too
                            # few and too wrong in BOTH arms to defend the extra fires.
                            # KILL CONDITION, pre-registered: if the fired-scan rate
                            # over the next 10 live sessions falls below half the
                            # trailing-10 rate under tools-on (diary `watchtower.fired`),
                            # flip this back to False and re-open the question. A cost
                            # cut that quietly mutes the tower is not a cost cut.
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

# --- air pocket (wt-9 doctrine gap) ---------------------------------------------
AIR_POCKET_MIN_SIGMA = 0.5   # clear span behind the near wall (near.hi → next.lo, in
                             # σ) that counts as an AIR POCKET — thin space price
                             # crosses fast, so a break of the near wall extends the
                             # objective ACROSS the gap to the far wall, not to the near

# --- wt-10 doctrine gaps ---------------------------------------------------------
FENCE_CONTROL_SHARE = 0.15   # cT/pT fence threshold: a strike "decisively controls" its
                             # side when |net γ| ≥ this share of the surface's peak |net|
                             # — relative to the day's own book, never an absolute
FLOW_CONFIRM_MIN_CONTRACTS = 25_000  # thin-tape abstain floor for flow_confirmation: below
                             # this total gross volume the shares are noise (same abstain
                             # doctrine as the DEX flow-signed floor) — field absent

# --- wt-11 scene derivations (each measured-or-absent; every threshold from THIS
# --- tape's recorded distributions, pre-registered here — never copied from SNDK) -----
MOMENTUM_ROWS = 5            # rolling window: last 5 scans (~10 min at the ~2-min
                             # diary cadence — same wall-clock the SNDK window covers)
MOMENTUM_BUILD_PP = 0.15     # |Δ share| below this is "steady" (≈p75 of the recorded
                             # 5-scan |Δshare| for the top magnet strike, n=4,064)
VOL_TREND_MIN = 28           # minutes of history before a 30-min IV read exists
VOL_TREND_MAX = 45           # a reference older than this is an outage artifact,
                             # not a 30-min window — the read is omitted instead
VOL_TREND_FLAT = 2.0         # |Δ IV| under this many vol pts reads "flat"
                             # (≈p72 of the recorded |Δ30m|, n=3,390 windows)
# the measured 30-min move spread (sr-5's form): 30 sessions, 336 disjoint windows,
# 2026-06-25..08-10. A spread teaches a distribution; a point teaches a ceiling.
MOVE_30M_SPREAD = {"half_under": 0.08, "one_in_five_over": 0.15,
                   "one_in_twenty_over": 0.27, "worst_recorded": 0.46,
                   "sessions_measured": 30}
ABNORMAL_DAY_SIGMA = 1.5     # a day move beyond this many σ is abnormal for this
                             # tape — σ-relative on purpose (the SNDK fixed-% lesson)
ABNORMAL_30M_SIGMA = 0.5     # a 30-min sprint at ≈2× the recorded p95 — rare by
                             # construction, so the outside-world reach stays licensed
                             # for genuine exceptions only


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
        # per_side=2 (wt-9): the near wall AND the next one behind it, so the empty span
        # between them — the air pocket — is measurable each side.
        near = gw_vocab.nearest_walls(clusters, spot, per_side=2)

        def _row(c: dict) -> dict:
            return {"label": gw_vocab.gw_label(c["side"], c["tier"]),
                    "prime_tier": c["tier"],
                    "cluster_span_sigma": [_sd(c["lo"], spot, sigma),
                                           _sd(c["hi"], spot, sigma)],
                    "peak_sigma_from_spot": _sd(c["peak"], spot, sigma)}

        # AIR POCKET (wt-9): the clear span behind the near wall to the next structure. A
        # break of the near wall crosses that empty space fast, so the objective extends to
        # the FAR wall, not the near one — the inverse of the runway/reach read. Two guards
        # from the wt-9 pressure test: (a) the two clusters must be the SAME SIDE — a gap
        # measured between a call cluster and a put cluster is not a coherent runway; (b) the
        # whole pocket read is suppressed on a MESSY profile, so it can never hand out a
        # target-extension in the exact configuration profile_clean calls STAND DOWN. Emitted
        # only when the same-side gap ≥ AIR_POCKET_MIN_SIGMA; else absent.
        def _pocket(inner: dict, outer: dict) -> str | None:
            if inner["side"] != outer["side"] or not sigma:
                return None                       # opposite-sign neighbors ≠ a runway
            gap_pts = (outer["lo"] - inner["hi"] if outer["peak"] > inner["peak"]
                       else inner["lo"] - outer["hi"])   # empty points between the two spans
            if gap_pts <= 0:
                return None
            gap_sd = gap_pts / sigma
            if gap_sd < AIR_POCKET_MIN_SIGMA:
                return None
            return (f"AIR POCKET — {gap_sd:.2f}σ of clear space from the near wall "
                    f"({gw_vocab.gw_label(inner['side'], inner['tier'])}) to the next "
                    f"({gw_vocab.gw_label(outer['side'], outer['tier'])}); once the near wall "
                    "breaks, price crosses the empty span fast — extend the objective ACROSS "
                    "the gap to the far wall, not to the near one. Advisory (subordinate to the "
                    "gamma sign: under LONG gamma a break is faded, so the pocket is dormant).")

        _clean = bool(profile_word and profile_word.startswith("CLEAN"))
        walls: dict = {}
        # wt-11 (sr-5): measured-empty is a FINDING, not an absence. We are in
        # the successful branch — the surface WAS read and clusters exist — so
        # a side with nothing on it ships a clear-flag: price in open air that
        # way is a real reading, often the loudest one in the scene. A true
        # sensor failure still returns ({}, None) above, unchanged: absence
        # keeps meaning "not measured", a clear-flag means "measured empty".
        _CLEAR = ("MEASURED CLEAR — the surface was read and holds no wall "
                  "cluster on this side; price is in open air that way")
        if near["above"]:
            walls["wall_cluster_above"] = _row(near["above"][0])
            if _clean and len(near["above"]) > 1:   # no pocket while the profile reads MESSY
                _ap = _pocket(near["above"][0], near["above"][1])
                if _ap:
                    walls["air_pocket_above"] = _ap
        else:
            walls["wall_cluster_above_clear"] = _CLEAR
        if near["below"]:
            walls["wall_cluster_below"] = _row(near["below"][0])
            if _clean and len(near["below"]) > 1:
                _bp = _pocket(near["below"][0], near["below"][1])
                if _bp:
                    walls["air_pocket_below"] = _bp
        else:
            walls["wall_cluster_below_clear"] = _CLEAR
        return walls, profile_word
    except Exception:
        return {}, None      # fail-open: a broken read stays out of the room


def _transition_fences(net_by_strike, flip, spot, sigma) -> dict:
    """wt-10 doctrine gap (§4 cT/pT) — the MEASURED transition fences. The doctrine's
    three-state regime read (above cT = confirmed calm / inside = NO regime / below
    pT = confirmed fast) has only existed here as the fixed ±FLIP_CHOP_BAND_SIGMA
    heuristic; this measures the actual fences off the same net_by_strike surface the
    cluster read uses: walking outward from the flip, the first strike each side where
    one-signed control decisively resumes (|net| ≥ FENCE_CONTROL_SHARE × peak |net|,
    correct sign). Zone WIDTH is the §4.4 ambiguity-depth signal; a side where control
    never resumes is a MISSING FENCE named honestly (§4.6), never silently defaulted.
    Fences are boundaries of behaviour — no tiering, no targets, no order flow of
    their own. Fail-open: no surface / no flip / no decisive strike either side → {}."""
    try:
        if not net_by_strike or flip is None or not spot or not sigma:
            return {}
        pts = sorted((float(k), float(v)) for k, v in net_by_strike)
        peak = max(abs(v) for _, v in pts)
        if peak <= 0:
            return {}
        theta = FENCE_CONTROL_SHARE * peak
        ct = next((k for k, v in pts if k > flip and v >= theta), None)          # upper fence
        pt = next((k for k, v in reversed(pts) if k < flip and v <= -theta), None)  # lower fence
        if ct is None and pt is None:
            return {}
        out: dict = {}
        if ct is not None:
            out["ct_upper_fence_sigma_from_spot"] = _sd(ct, spot, sigma)
        else:
            out["ct_upper_fence"] = ("MISSING — no strike above the flip decisively nets "
                                     "long: the calm side has no measured floor of control")
        if pt is not None:
            out["pt_lower_fence_sigma_from_spot"] = _sd(pt, spot, sigma)
        else:
            out["pt_lower_fence"] = ("MISSING — no strike below the flip decisively nets "
                                     "short: the fast side has no measured ceiling of control")
        if ct is not None and pt is not None:
            width = round((ct - pt) / sigma, 2)
            out["zone_width_sigma"] = width
            out["width_note"] = (f"the fences bracket {width}σ of no-man's-land "
                                 "(WIDE = deep ambiguity across the whole neighborhood; "
                                 "NARROW = the regime switches cleanly)")
        if ct is not None and spot > ct:
            pos = ("above cT — the CALM side is measured-confirmed here (three-state read); "
                   "still confirm against gamma_sign_at_spot")
        elif pt is not None and spot < pt:
            pos = ("below pT — the FAST side is measured-confirmed here (three-state read); "
                   "still confirm against gamma_sign_at_spot")
        else:
            pos = ("INSIDE the transition zone — between the fences NEITHER side controls: "
                   "the measured answer is NO REGIME (same stand-down as the chop band, "
                   "measured rather than assumed); no directional play from inside")
        out["spot_position"] = pos
        out["note"] = ("MEASURED cT/pT fences (the chop band's measured refinement). Fences "
                       "are boundaries of behaviour, not walls: they carry no order flow, "
                       "take no tier, and are never targets.")
        return out
    except Exception:
        return {}      # fail-open


def _flow_confirmation(gx: dict, call_wall, put_wall, spot, sigma) -> dict:
    """wt-10 doctrine gap (§7 standing OI vs live volume) — is today's tape actually
    FIGHTING at the operative walls? The per-strike gross-volume and standing-OI
    ladders have been drawn on the viewstation since wt-8 but never summarized into
    the room. For each 0DTE wall: today's gross-volume share and standing-|OI| share
    at the wall's strike, judged against the UNIFORM share (1/n strikes — relative to
    the day's own book, generalizing across days), composed into one word:
      live volume + standing OI  → CONTESTED — the wall's verdict is being decided today
      volume, thin OI            → a volume-built level (0DTE flow) — can melt by close
      OI, no volume              → UNTESTED standing shelf — strength unproven today
    Plus the VOLUME KING when today's heaviest strike is at neither wall — the fight
    is somewhere else. Shares/ranks only (no absolute contract counts as signals),
    σ-distances only, thin-tape abstain below FLOW_CONFIRM_MIN_CONTRACTS total.
    Fail-open: no surface / thin tape / no walls → {}."""
    try:
        vol = gx.get("vol_gross_by_strike")
        if not vol or not spot or not sigma:
            return {}
        vv = sorted(((float(k), abs(float(v))) for k, v in vol), key=lambda p: -p[1])
        total = sum(v for _, v in vv)
        n = len(vv)
        if total < FLOW_CONFIRM_MIN_CONTRACTS or n < 5:
            return {}                              # thin-tape abstain (shares are noise)
        uniform = 1.0 / n
        vol_share = {k: v / total for k, v in vv}
        vol_rank = {k: i + 1 for i, (k, _) in enumerate(vv)}
        oi_abs = {float(k): abs(float(v)) for k, v in (gx.get("oi_by_strike") or [])}
        oi_total = sum(oi_abs.values())
        # walls come FROM this strike grid, so nearest-strike matching with a one-step
        # tolerance is exact in practice and safe when grids drift
        strikes = sorted(vol_share)
        step = min((b - a for a, b in zip(strikes, strikes[1:])), default=None)

        def _at(level):
            if level is None or step is None:
                return None
            k = min(strikes, key=lambda s: abs(s - level))
            return k if abs(k - level) <= 1.5 * step else None

        def _wall_row(level):
            k = _at(level)
            if k is None:
                return None
            vs, vr = vol_share[k], vol_rank[k]
            os_ = (oi_abs.get(k, 0.0) / oi_total) if oi_total else None
            v_hi, v_lo = vs >= 3 * uniform, vs <= 0.5 * uniform
            o_hi = os_ is not None and os_ >= 3 * uniform
            if v_hi and o_hi:
                word = ("CONTESTED — heavy standing OI AND heavy volume today: the wall's "
                        "verdict is being decided live (a fought wall is a live battleground, "
                        "not a proven shelf — weigh siege/effort, not the bar length)")
            elif v_hi:
                word = ("a VOLUME-BUILT level — today's 0DTE flow made this wall, thin "
                        "standing OI behind it: less durable than an OI wall, can melt "
                        "into the close")
            elif v_lo and o_hi:
                word = ("UNTESTED standing-OI shelf — nobody has fought here today: its "
                        "strength is UNPROVEN either way (standing inventory, not a "
                        "demonstrated hold)")
            elif v_lo:
                word = "minor traffic — neither today's volume nor standing OI concentrates here"
            else:
                word = ("moderately traded — some of today's tape has visited this wall; "
                        "neither clearly contested nor clearly untested")
            return {"level_sigma_from_spot": _sd(k, spot, sigma),
                    "todays_volume_share": round(vs, 3),
                    "volume_rank": f"{vr} of {n}",
                    **({"standing_oi_share": round(os_, 3)} if os_ is not None else {}),
                    "read": word}

        out: dict = {}
        cw, pw = _wall_row(call_wall), _wall_row(put_wall)
        if cw:
            out["call_wall"] = cw
        if pw:
            out["put_wall"] = pw
        # the VOLUME KING — where today's fight actually is, when it is at neither wall
        king, king_v = vv[0]
        _kc, _kp = _at(call_wall), _at(put_wall)
        if king not in (_kc, _kp) and king_v / total >= 3 * uniform:
            out["volume_king"] = {
                "level_sigma_from_spot": _sd(king, spot, sigma),
                "todays_volume_share": round(king_v / total, 3),
                "read": ("today's HEAVIEST options volume sits HERE — at neither operative "
                         "wall: the live fight/defense is at this level, so expect price "
                         "reactions here even though the wall map does not mark it"),
            }
        if not out:
            return {}
        out["note"] = ("§7 flow confirmation: TODAY'S volume vs STANDING OI at the "
                       "operative walls — separates live battlegrounds from untested "
                       "paper shelves. Advisory context on wall RELIABILITY only; it "
                       "never picks a direction. Shares are of today's whole 0DTE book "
                       f"(uniform share ≈ {uniform:.1%}).")
        return out
    except Exception:
        return {}      # fail-open


def _strike_tags(gx: dict, dx: dict, spot, sigma) -> dict:
    """wt-10 (§3.4) — the doctrine tag ladder, summarized into the room. Same
    rank/argmax rules as the viewstation TABLE's tag column (GXTAG): ranked gamma
    walls (P1-P6 / C1-C3, largest |net γ| first) + side-agnostic Ab1-Ab3; the OI
    peaks POI/COI/AbOI/nPOI (standing structure — these BUILD the map); the volume
    peaks CV/PV/nCV/nPV (today's flow — which parts of the map are LIVE); and the
    flow-signed delta-pressure peaks D+/D−. All σ-distances, locations only.

    Accuracy guards, each verified against the table's logic:
      * net tags (nPOI/nCV/nPV/D±) fire ONLY when the winning strike genuinely
        carries that sign (argmaxPos) — a one-sided book can never mislabel;
      * volume side-splits prefer the engine's vol_side_by_strike and otherwise
        derive EXACTLY from the stored net+gross panes (call=(g+n)/2, put=(g−n)/2
        — an identity, both built in the same loop);
      * per-side OI has no derivable fallback (only net OI is stored pre-07-27),
        so POI/COI/AbOI attach only when oi_side_by_strike is present; nPOI falls
        back to the net pane (put-dominant = net < 0, strictly);
      * the volume family shares flow_confirmation's thin-tape abstain floor.
    Fail-open: any missing surface drops its family; nothing left → {}."""
    try:
        if not spot or not sigma:
            return {}
        out: dict = {}

        def _argmax(pts, score, pos_only=False):
            bk, bs = None, (0 if pos_only else float("-inf"))
            for p in pts or []:
                try:
                    s = score(p)
                except (TypeError, ValueError, IndexError):
                    continue
                if s is not None and s > bs:
                    bs, bk = s, float(p[0])
            return _sd(bk, spot, sigma)

        # gamma ranks — net_by_strike (calls +, puts −), largest |net| first
        nbs = [(float(k), float(v)) for k, v in (gx.get("net_by_strike") or [])]
        if nbs:
            def _ranked(keep, n):
                won = sorted((p for p in nbs if keep(p[1])),
                             key=lambda p: abs(p[1]), reverse=True)[:n]
                return [_sd(k, spot, sigma) for k, _ in won]
            p_ranks = _ranked(lambda v: v < 0, 6)
            c_ranks = _ranked(lambda v: v > 0, 3)
            ab_ranks = _ranked(lambda v: True, 3)
            if p_ranks:
                out["ranked_put_walls_sigma_P1_first"] = p_ranks
            if c_ranks:
                out["ranked_call_walls_sigma_C1_first"] = c_ranks
            if ab_ranks:
                out["abs_gamma_peaks_sigma_Ab1_first"] = ab_ranks
        # standing OI peaks — per-side [k, callOI, putOI] when the engine emits it
        oi_side = gx.get("oi_side_by_strike")
        if oi_side:
            out.update({k: v for k, v in {
                "put_oi_peak_sigma_POI": _argmax(oi_side, lambda p: float(p[2])),
                "call_oi_peak_sigma_COI": _argmax(oi_side, lambda p: float(p[1])),
                "total_oi_peak_sigma_AbOI": _argmax(oi_side, lambda p: float(p[1]) + float(p[2])),
                "net_put_oi_peak_sigma_nPOI": _argmax(
                    oi_side, lambda p: float(p[2]) - float(p[1]), pos_only=True),
            }.items() if v is not None})
        elif gx.get("oi_by_strike"):     # net-pane fallback: put-dominant = net < 0
            npoi = _argmax(gx["oi_by_strike"], lambda p: -float(p[1]), pos_only=True)
            if npoi is not None:
                out["net_put_oi_peak_sigma_nPOI"] = npoi
        # today's volume peaks — behind the same thin-tape floor as flow_confirmation
        vol_side = gx.get("vol_side_by_strike")
        if not vol_side:
            vnet = dict((float(k), float(v)) for k, v in (gx.get("vol_by_strike") or []))
            vgross = [(float(k), float(v)) for k, v in (gx.get("vol_gross_by_strike") or [])]
            vol_side = [[k, (g + vnet.get(k, 0.0)) / 2, (g - vnet.get(k, 0.0)) / 2]
                        for k, g in vgross]
        if vol_side and sum(float(p[1]) + float(p[2]) for p in vol_side) >= FLOW_CONFIRM_MIN_CONTRACTS:
            out.update({k: v for k, v in {
                "call_volume_peak_sigma_CV": _argmax(vol_side, lambda p: float(p[1])),
                "put_volume_peak_sigma_PV": _argmax(vol_side, lambda p: float(p[2])),
                "net_call_volume_peak_sigma_nCV": _argmax(
                    vol_side, lambda p: float(p[1]) - float(p[2]), pos_only=True),
                "net_put_volume_peak_sigma_nPV": _argmax(
                    vol_side, lambda p: float(p[2]) - float(p[1]), pos_only=True),
            }.items() if v is not None})
        # delta-pressure peaks — the flow-SIGNED per-strike delta (the only live sign)
        fbs = (dx or {}).get("dex_flow_by_strike")
        if fbs:
            out.update({k: v for k, v in {
                "delta_buy_pressure_peak_sigma_Dplus": _argmax(
                    fbs, lambda p: float(p[1]), pos_only=True),
                "delta_sell_pressure_peak_sigma_Dminus": _argmax(
                    fbs, lambda p: -float(p[1]), pos_only=True),
            }.items() if v is not None})
        if not out:
            return {}
        out["note"] = ("§3.4 tag ladder (locations only, NEVER a direction). OI tags "
                       "(POI/COI/AbOI/nPOI) are STANDING structure — they build the map; "
                       "volume tags (CV/PV/nCV/nPV) are TODAY'S flow — they say which "
                       "parts are live right now; D± are where today's flow-signed hedge "
                       "pressure concentrates. CONFLUENCE OUTRANKS BAR LENGTH (§3.3): "
                       "several independent lenses landing on ONE level (within ~0.05σ) "
                       "is independent corroboration — weight that level above a longer "
                       "bare bar; a stacked level that ALSO carries a volume tag is the "
                       "strongest configuration on the board.")
        return out
    except Exception:
        return {}      # fail-open


# --- wt-11 scene helpers (pure, fail-open — an unreadable input is ABSENT) ----
def _fin(v):
    """A finite, non-bool number or None. NaN is the dangerous one: it rides
    isinstance checks, renders as a token in the prompt, and NaN < threshold
    comparisons silently pick the CONFIDENT branch (the SNDK adversarial
    audit's finding, enforced here from day one)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _parse_row_ts(r: dict):
    """A diary row's ts → aware datetime, or None — a malformed stamp must
    degrade to 'unknown', never to a date (an age computed off epoch zero
    reads as decades-stale and freezes the whole board)."""
    try:
        return datetime.fromisoformat(r["ts"])
    except (KeyError, TypeError, ValueError):
        return None


def _row_atm_iv(r: dict):
    """A row's front-book ATM IV. wt-11 rows record it (atm_iv, None when the
    1%-of-spot fallback priced σ); older rows derive it exactly (sigma_live =
    spot·iv/√252, so iv = sigma_live·√252/spot)."""
    iv = _fin(r.get("atm_iv"))
    if iv is not None and iv > 0:
        return iv
    sl, sp = _fin(r.get("sigma_live")), _fin(r.get("spot"))
    if sl and sp and sl > 0 and sp > 0:
        return sl * math.sqrt(252.0) / sp
    return None


def _vol_trend(rows: list[dict]) -> dict | None:
    """Is implied vol rising or falling NOW — the switch that arms vanna and
    charm. 30-min ATM-IV change in vol points, by timestamp (a scan gap must
    not shorten the window); flat band VOL_TREND_FLAT pre-registered off the
    recorded Δ30m distribution. None without ~30 min of history.

    LATE-DAY GUARD: the 0DTE front-book ATM IV reprices on minutes-to-close,
    so into the bell the solve balloons on pure clock mechanics — a τ→0
    artifact is not a vol signal. The read is omitted once the range ruler
    itself stamps the day late_day (the same authority the EM read trusts)."""
    if not rows:
        return None
    rr = rows[-1].get("range_ruler") or {}
    if rr.get("quality") == "late_day":
        return None
    ser = []
    for r in rows:
        t, iv = _parse_row_ts(r), _row_atm_iv(r)
        if t is not None and iv is not None:
            ser.append((t, iv))
    if not ser:
        return None
    t_now, iv_now = ser[-1]
    ref = next(((t, iv) for t, iv in reversed(ser[:-1])
                if (t_now - t) >= timedelta(minutes=VOL_TREND_MIN)), None)
    # the reference must actually BE ~30 min back: after a scan outage the
    # nearest old-enough row can be hours old, and shipping that Δ under a
    # 30-min name would lie about its window — no read beats a mislabeled one
    if ref is None or (t_now - ref[0]) > timedelta(minutes=VOL_TREND_MAX):
        return None
    d = (iv_now - ref[1]) * 100.0
    word = ("flat" if abs(d) < VOL_TREND_FLAT
            else "rising" if d > 0 else "falling")
    return {"direction": word, "iv_change_last_30min": round(d, 1)}


def _magnet_band(gx: dict) -> dict | None:
    """The magnet's tie, told in numbers — top strikes by share of gamma mass
    and the top1−top2 gap in pp. The SPX magnet is a statistical tie most of
    the day (median gap 0.99pp over 4,258 recorded scans); pin_top_share alone
    cannot say so. Returns strikes in RAW form — the caller converts to
    σ-distances before anything ships (no absolute levels in the scene)."""
    mb = []
    for pair in (gx.get("mass_by_strike") or []):
        try:
            k, v = _fin(pair[0]), _fin(pair[1])
        except (TypeError, IndexError, KeyError):
            continue
        if k is not None and v is not None:
            mb.append((k, abs(v)))
    tot = sum(v for _, v in mb)
    if not mb or tot <= 0:
        return None
    ranked = sorted(mb, key=lambda x: -x[1])[:3]
    gap = (round((ranked[0][1] - ranked[1][1]) / tot * 100.0, 2)
           if len(ranked) >= 2 else None)
    return {"top": [(k, round(v / tot * 100.0, 2)) for k, v in ranked],
            "gap_pp": gap}


def _momentum_block(rows: list[dict], band: dict | None, sd) -> dict | None:
    """Snapshot-over-snapshot deltas for the top magnet strikes over the last
    MOMENTUM_ROWS scans. gex_share_d_pp = the strike's share of gamma mass, in
    pp (scale-free — raw gamma is unit-opaque); vol_d = gross contracts traded
    there since the reference scan. Shares are computed over the INTERSECTION
    of the two strike windows: the window is spot-relative and moves with
    price, so a full-window denominator turns a frame shift into fake flow
    (the SNDK telescope lesson). Keys are σ-distances — no absolute levels."""
    if not band or not band.get("top") or len(rows) <= MOMENTUM_ROWS:
        return None
    cur, ref = rows[-1], rows[-1 - MOMENTUM_ROWS]
    t_c, t_r = _parse_row_ts(cur), _parse_row_ts(ref)
    if t_c is None or t_r is None:
        return None

    def _pairs(r, key, absolute=False):
        out = {}
        for pair in ((r.get("gex_views") or {}).get(key) or []):
            try:
                k, v = _fin(pair[0]), _fin(pair[1])
            except (TypeError, IndexError, KeyError):
                continue
            if k is not None and v is not None:
                out[k] = abs(v) if absolute else v
        return out

    mb_c = _pairs(cur, "mass_by_strike", absolute=True)
    mb_r = _pairs(ref, "mass_by_strike", absolute=True)
    vg_c = _pairs(cur, "vol_gross_by_strike")
    vg_r = _pairs(ref, "vol_gross_by_strike")
    common = set(mb_c) & set(mb_r)
    tot_c = sum(mb_c[k] for k in common)
    tot_r = sum(mb_r[k] for k in common)
    if tot_c <= 0 or tot_r <= 0:
        return None
    by = {}
    for k, _share in band["top"][:2]:
        k = float(k)
        if k not in mb_c or k not in mb_r:
            continue
        gex_d = mb_c[k] / tot_c * 100.0 - mb_r[k] / tot_r * 100.0
        entry = {"gex_share_d_pp": round(gex_d, 2)}
        if k in vg_c and k in vg_r:
            entry["vol_d"] = int(vg_c[k] - vg_r[k])
        vol_d = entry.get("vol_d")
        # symmetric evidentiary standard: fading earns its hedge exactly the
        # way building does (an unhedged "fading" is a one-way tilt)
        if gex_d >= MOMENTUM_BUILD_PP:
            entry["read"] = ("building" if (vol_d or 0) > 0
                             else "building (vol unconfirmed)")
        elif gex_d <= -MOMENTUM_BUILD_PP:
            entry["read"] = ("fading" if (vol_d or 0) > 0
                             else "fading (vol unconfirmed)")
        else:
            entry["read"] = "steady"
        k_sd = sd(k)
        if k_sd is None:
            continue
        if k_sd == 0:
            k_sd = 0.0                   # never a "-0.00" key (negative zero)
        by[f"strike_at_{k_sd:+.2f}sigma"] = entry
    if not by:
        return None
    span = int((t_c - t_r).total_seconds() // 60)
    return {"window": f"last {MOMENTUM_ROWS} scans ({span}m)", "by_strike": by}


def _dex_change_30m(rows: list[dict], cur_net: float) -> float | None:
    """The timestamp-true 30-min change in net dealer delta, $bn — the one
    part of the dex level that can be news (the level's sign is fixed by the
    formula). Same window discipline as _vol_trend: a reference that is not
    genuinely ~30 min old is no reference at all."""
    if not rows:
        return None
    t_now = _parse_row_ts(rows[-1])
    if t_now is None:
        return None
    for r in reversed(rows[:-1]):
        t = _parse_row_ts(r)
        if t is None or (t_now - t) < timedelta(minutes=VOL_TREND_MIN):
            continue
        if (t_now - t) > timedelta(minutes=VOL_TREND_MAX):
            break                        # outage-shaped window — no honest read
        ref = _fin((r.get("dex_views") or {}).get("net_dex_total"))
        if ref is not None:
            return round((cur_net - ref) / 1e9, 2)
        break
    return None


def _moved_30m(rows: list[dict], sigma) -> float | None:
    """How far price travelled in the last ~30 minutes, in σ — timestamp-true
    (a scan gap must not silently shorten the window; the count-based twin
    lied on 20/700 SNDK scans, once by 0.81σ)."""
    if not rows or not sigma:
        return None
    cur = rows[-1]
    spot, t = _fin(cur.get("spot")), _parse_row_ts(cur)
    if spot is None or t is None:
        return None
    cutoff = t - timedelta(minutes=30)
    ref = None
    for r in reversed(rows):
        rt = _parse_row_ts(r)
        if rt is None or rt > t:
            continue
        ref = r
        if rt <= cutoff:
            break
    if ref is None:
        return None
    rs, rt = _fin(ref.get("spot")), _parse_row_ts(ref)
    # only claim a 30-min read when we actually have ~30 min of history
    if rs is None or rt is None or (t - rt) < timedelta(minutes=20):
        return None
    return round((spot - rs) / sigma, 2)


def _history_flags(t: dict, rows: list[dict], moved_30m) -> dict | None:
    """The under-pull guard: cheap, unmissable flags that tap the model on the
    shoulder to reach for the history tool / outside world. Omitted entirely
    when there is nothing to say — calibrated so "abnormal" stays a genuine
    exception (a flag that is usually on trains the model to ignore it, and it
    licenses the outside-world reach far too often)."""
    out = {}
    spot, sigma = _fin(t.get("spot")), _fin(t.get("sigma"))
    prior = [s for r in rows[:-1] if (s := _fin(r.get("spot"))) is not None]
    if spot is not None and len(prior) >= 30 and \
            (spot > max(prior) or spot < min(prior)):
        out["level_unseen_today"] = True
    pc = _fin(t.get("prior_close"))
    day_sd = ((spot - pc) / sigma) if (spot is not None and pc and sigma) else None
    if (day_sd is not None and abs(day_sd) >= ABNORMAL_DAY_SIGMA) or \
            ((m := _fin(moved_30m)) is not None and abs(m) >= ABNORMAL_30M_SIGMA):
        out["abnormal_tape"] = True
    return out or None


def build_payload(t: dict, reveal_gates: bool = False,
                  rows: list[dict] | None = None) -> dict:
    """THE SCENE — everything the model may see, as one dict of precomputed,
    σ-normalized numbers with the signs spelled out. `reveal_gates=False` is
    the BLIND pass: the four gates' continuous inputs are present (they are
    raw facts) but their pass/fail verdicts and the fired decision are NOT.
    Live order-book flow (`order_flow`) and the sibling Break Lens escape read
    (`break_lens_head_c`) ride along as raw facts too — attached only when they
    carry a genuine read, so the tower can confirm the pull against the tape and
    avoid fading a real break. Field order is stable on purpose (payload hygiene).

    `rows` (wt-11) = today's PRIOR diary rows — the current scan is not on disk
    yet (the _session_path convention), so it is folded in here as the newest
    row. Every rows-derived block (vol_trend / momentum / dex change / the
    30-min path / history flags) is measured-or-absent: no rows, no claim."""
    rev = t.get("reversion_extreme") or {}
    gx = t.get("gex_views") or {}
    spot, sigma = t.get("spot"), t.get("sigma")
    allrows = [r for r in (rows or []) if isinstance(r, dict)] + [t]
    # clock: minutes + (wt-11) the DATE. The no-dates rule was written when golden
    # replays fed in-training-window scenes to a model with SPX history memorized;
    # live dates sit past the pinned model's cutoff, and the history CLI's date
    # filters are unusable without today's date. Absolute LEVELS stay banned.
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
    # CHARM + VANNA, N11's FINAL FORM (wt-11): neutral magnitude + level, no sign
    # word, no raw sign field. The history of this block is the whole argument —
    # wt-4 spelled the drift direction out, N11/wt-5 gated the word on
    # charm_word_ok (the sign is structurally locked by the assumed-sign book:
    # cex_sign read "negative" on 97% of 5,092 recorded rows, vex_sign "positive"
    # on 96%), and that gate then never once opened (False/None on 5,176/5,176
    # rows) — so the word was dead weight and the raw sign fields were
    # near-constants wearing signal clothes. What survives is what varies: the
    # STRENGTH (uncalibrated — compare day to day, never across books) and the
    # LEVEL the clock funnels toward (charm_wall — a location, never a promised
    # direction). vanna's strength rides beside it, armed by tape.vol_trend
    # (vanna only matters when implied vol is actually moving).
    charm_block = None
    _cex = _fin(gx.get("cex"))
    if _cex is not None:
        charm_block = {"strength": round(abs(_cex) / 1e6, 1),
                       "note": "uncalibrated $M/day of hedge re-balancing as time "
                               "decays — compare day to day; grips hardest in the "
                               "last ~2h; NO direction (the sign is fixed by the "
                               "assumed-sign book, not by dealers)"}
        _cw_sd = _sd(gx.get("charm_wall"), spot, sigma)
        if _cw_sd is not None:
            charm_block["funnels_toward_sigma"] = _cw_sd
    vanna_block = None
    _vex = _fin(gx.get("vex"))
    if _vex is not None:
        vanna_block = {"strength": round(abs(_vex) / 1e6, 1),
                       "note": "uncalibrated $M per vol point — only alive when "
                               "tape.vol_trend is actually moving; NO direction "
                               "(same assumed-sign caveat as charm)"}
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
    # REGIME INTENSITY, 1-DAY (wt-9): the doctrine reads the 1-day CHANGE in net gamma as
    # "is the regime intensifying or fading?" — an entrenchment scaler, not a direction. The
    # yesterday-close pair already rides the row (net_exposure, built in reversion_lens) but
    # the tower has never read it. It is the WHOLE-BOOK (blended 0-7DTE) net-Γ, repriced at
    # spot: a 0DTE 1-day diff is meaningless (yesterday's 0DTE expired), so the blended book is
    # the only surface with day-over-day continuity. Two corrections from the wt-9 pressure
    # test: (a) the WORD must branch on the sign of TODAY'S level, not just the sign of the
    # change — "+5 → +3" is still a long-gamma pin weakening, NOT a fast regime intensifying;
    # (b) because both values are repriced AT their own day's spot, an overnight gap moves this
    # number without any repositioning, so it is worded as context, never "the book tilted".
    # Guard on prev_close_date (a fabricated zero can't masquerade as a prior read); a flat day
    # emits nothing; the % is shown only when the prior book was meaningfully off the flip
    # (near-balanced prior → the ratio is noise). COLOR/advisory.
    ne = t.get("net_exposure") or {}
    regime_1d_word = None
    _cur, _prev = ne.get("net_gamma_total"), ne.get("prev_close_gamma")
    if _cur is not None and _prev is not None and ne.get("prev_close_date"):
        _d1 = _cur - _prev
        # only show a % when the prior book was clearly one-signed (≥10% of today's |net|):
        # dividing by a near-zero prior detonates the ratio on exactly the flip-day scans.
        _pct1 = (_d1 / abs(_prev)) if (_prev and abs(_prev) >= 0.10 * abs(_cur)) else None
        _pct_txt = f", {_pct1:+.0%}" if _pct1 is not None else ""
        _tail = (f" vs yesterday's close ({ne['prev_close_date']}{_pct_txt}). Advisory — a "
                 "whole-book (0-7DTE) entrenchment read repriced at spot (partly reflects the "
                 "overnight spot move, not only repositioning); scales conviction on how "
                 "durable the regime is, never a direction.")
        if _d1 != 0:
            if _cur > 0 and _prev > 0:      # stayed long-gamma — a strengthening/weakening pin
                _state = ("a long-gamma PIN is ENTRENCHING (more durable)" if _d1 > 0
                          else "a long-gamma PIN is WEAKENING (drifting toward the flip)")
            elif _cur < 0 and _prev < 0:    # stayed short-gamma — deepening/easing amplification
                _state = ("short-gamma AMPLIFICATION is EASING (drifting toward the flip)" if _d1 > 0
                          else "short-gamma AMPLIFICATION is DEEPENING (a fast regime intensifying)")
            elif (_cur > 0) != (_prev > 0):  # crossed zero — the real regime-change event
                _state = (f"the whole-book gamma FLIPPED "
                          f"{'SHORT→LONG' if _cur > 0 else 'LONG→SHORT'} over the day — a "
                          "regime change (confirm against today's 0DTE gamma sign)")
            else:                            # one side exactly zero — name the move, claim no state
                _state = ("net gamma moved " + ("UP toward long" if _d1 > 0 else "DOWN toward short"))
            regime_1d_word = f"whole-book net gamma: {_state}{_tail}"
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
    # wt-10: the measured cT/pT fences (§4) + flow confirmation at the walls (§7) —
    # both fail-open helpers ({} stays out of the room), both advisory this era
    transition_zone = _transition_fences(gx.get("net_by_strike"), t.get("gamma_flip"),
                                         spot, sigma)
    flow_confirm = _flow_confirmation(gx, t.get("call_wall"), t.get("put_wall"), spot, sigma)
    # wt-10 (§3.4): the doctrine tag ladder — ranked walls, OI/volume/delta peaks
    strike_tags = _strike_tags(gx, t.get("dex_views") or {}, spot, sigma)
    # wt-10 (§6.5): the day's reach DECAYS — remaining expected-move ≈ √(time-left) share
    # of the full-day band. Pure clock math, worded as a SIZE cap only; silent the first
    # hour (the band is still ~full) and outside market hours (no clock).
    em_decay = None
    if mins_open is not None and mins_close is not None and mins_open >= 60 and mins_close > 0:
        _rem = (min(mins_close, 390) / 390) ** 0.5
        em_decay = (f"~{_rem:.0%} of the full-day expected-move band remains "
                    f"({mins_close}m to the close; reach shrinks as √time-left) — a target "
                    "beyond that share of a full-day σ move is no longer realistic: cap "
                    "magnitude_sigma and range_sigma accordingly. SIZE guidance only, "
                    "never a direction.")
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
            # PAUSE vs TERMINUS (wt-9): the doctrine's target-selection LEAN, read off the
            # reversed-sign verdict — but subordinate to the gamma regime (the pressure test
            # caught the earlier wording asserting an unconditional "trade through", which
            # contradicts the siege rule: under LONG gamma a SIEGE verdict does NOT license a
            # breakout). So this is a LEAN with the regime caveat inline, never an imperative.
            role = ("leans BREAK (a PAUSE-type wall): heavy effort → this wall is more likely to "
                    "GIVE. IF the gamma regime permits a break (short / confirmed-negative), "
                    "treat it as a speed bump and the objective lies PAST it; under LONG gamma "
                    "this only lowers the pin's safety, it does NOT license trading through"
                    if v == "SIEGE" else
                    "leans HOLD (a TERMINUS-type level): light effort → this wall is more likely "
                    "to HOLD, a candidate pin/turn level IF the regime supports it (safer under "
                    "LONG gamma) — not an assertion that the move ends here")
            siege_rows.append({k: v2 for k, v2 in {
                "wall": tw.get("kind"),
                "level_sigma_from_spot": _sd(tw.get("level"), spot, sigma),
                "role": role,
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
        # NOTE (wt-9 pressure test): a `tape_vs_positioning_conflict` word was tried here and
        # REMOVED. It compared sign(net_dex_total) vs sign(dex_flow_signed), but net_dex_total
        # is STRICTLY POSITIVE by construction (the structural-long naive net — see lefteye_dex
        # header), so the guard collapsed to "dex_flow_signed < 0": a one-signed relabel of the
        # flow sign that CONTRADICTED the flow_signed_read in this very block and was not the
        # doctrine's §2.6 lean-vs-price-tape conflict at all. A faithful §2.6 read (flow hedge
        # direction vs the live PRICE tape) is a future item, not this.
        # wt-11: naive_net_sign REMOVED — dex_word is a constant string riding a
        # net that is > 0 BY CONSTRUCTION (measured positive on 2,627/2,627
        # recorded rows). wt-8 demoted it to last with a caveat; the SNDK sr-3
        # lesson is that a demoted constant is still a constant in the room — a
        # field the doctrine tells you to ignore must not ship at all. The one
        # thing the naive level can be is a BASELINE for its own change, so the
        # timestamp-true 30-min Δ now rides instead (measured off today's rows).
        _dx_d30 = _dex_change_30m(allrows, _fin(dx.get("net_dex_total")) or 0.0)
        dex_block = {k: v for k, v in {
            "magnitude": (f"≈ ${dx['net_dex_total'] / 1e9:.1f}bn underlying-equivalent "
                          "— UNCALIBRATED: no graded baseline yet, 'large' is not "
                          "defined this era. The SIGN of this number is fixed by the "
                          "formula (positive on every recorded row) — it is never "
                          "evidence; only the change below can be news"),
            **({"net_change_30min_bn": _dx_d30} if _dx_d30 is not None else {}),
            "standing_inventory_geography": ({**_geo,
                "note": "WHERE standing dealer-delta inventory sits vs spot — geography, "
                        "not a live directional call"} if _geo else None),
            **({"flow_signed_read": dx.get("dex_flow_word"),
                "flow_signed_magnitude": (
                    f"≈ {dx['dex_flow_signed'] / 1e6:+.0f}M underlying-δ on TODAY'S tape "
                    "only — UNCALIBRATED and on a DIFFERENT scale than the naive net above "
                    "(it swings ~100× intraday); read the SIGN and its move, not the level")}
               if _dx_fs is not None else {}),
            "note": "DEX = the direction side: net dealer delta still to re-hedge — "
                    "mechanical pressure, the strongest DIRECTIONAL read in the stack "
                    "when large. ADVISORY THIS ERA: record-only and ungraded — context "
                    "beside the gamma map, never a gate. Weigh the flow_signed_read "
                    "(whose sign is actually alive) and the geography; the naive "
                    "magnitude's sign is a fact about the formula, not the market.",
        }.items() if v is not None}
    # --- wt-11 rows-derived blocks (each measured-or-absent) -------------------
    _sdf = lambda k: _sd(k, spot, sigma)               # noqa: E731 — the σ ruler
    vol_tr = _vol_trend(allrows)
    band = _magnet_band(gx)
    moved30 = _moved_30m(allrows, _fin(sigma))
    momentum = _momentum_block(allrows, band, _sdf)
    history = _history_flags(t, allrows, moved30)
    # aem — today's likely range split toward the side the tape fears. Shipped ONLY
    # when the split's source is not the tower itself: adaptive_em prefers the
    # tower's own called range when one is live (asym_source="watchtower"), and a
    # model fed its own prior as evidence is a feedback loop, not a measurement.
    aem = None
    _ae = t.get("adaptive_em") if isinstance(t.get("adaptive_em"), dict) else None
    if _ae and _ae.get("asym_source") and _ae["asym_source"] != "watchtower":
        _up, _dn = _fin(_ae.get("em_up_pts")), _fin(_ae.get("em_down_pts"))
        if _up is not None and _dn is not None and sigma:
            aem = {"up_sigma": round(_up / sigma, 2),
                   "down_sigma": round(_dn / sigma, 2),
                   "source": f"{_ae['asym_source']} (realized-tape split)",
                   "note": "today's likely range, split toward the side the tape "
                           "fears — it breathes with vol, never a target"}
    _mb_payload = None
    if band and band.get("top"):
        _tops = []
        for k, sh in band["top"]:
            k_sd = _sdf(k)
            if k_sd is not None:
                _tops.append([k_sd, sh])
        _mb_payload = {k: v for k, v in {
            # the tie is the headline: gap_pp small = no strike is in charge
            "gap_pp": band["gap_pp"],
            "top_strikes": _tops or None,
            "note": "top strikes by share of gamma mass, as [σ-from-spot, share%] — "
                    "the median top1−top2 gap on this tape is ~1pp of mass, so the "
                    "magnet is usually a statistical TIE: when gap_pp is small, no "
                    "strike is really in charge — say so rather than naming one",
        }.items() if v is not None}
    payload = {
        "task": "You are the Watchtower: forecast where 0DTE SPX goes over the next hour — "
                "the gamma sign is the PERMISSION SLIP: fade/pin plays under LONG gamma, "
                "continuation allowed under SHORT gamma, stand down when it is unknown.",
        "clock": {"minutes_since_open": mins_open, "minutes_to_close": mins_close,
                  # wt-11 (sr-4): the date — the history CLI's day filters are
                  # unusable without it; absolute levels stay banned above
                  **({"date": ts.date().isoformat()} if ts else {})},
        # wt-8 (Fix 3a): the σ↔points ruler. Read at the top of this builder and never
        # emitted before — the tower needs it to reconcile σ-targets with the point-range
        # facts (tape.rest_of_day_range_pts, tape_speed) it is already handed. This is a
        # SCALE, not an absolute index level (the no-absolute-levels hygiene rule holds:
        # 1σ ≈ points_per_sigma index points; reason in σ, never anchor to the level).
        "scale": {"points_per_sigma": round(sigma, 2) if sigma else None,
                  # wt-11 (sr-5): the measured 30-min spread — a distribution to size
                  # against, not a single number to copy as a ceiling
                  "move_30min_sigma": dict(MOVE_30M_SPREAD),
                  **({"aem": aem} if aem else {})},
        "dealer_map_gravity": {
            # wt-8: the PERMISSION SLIP leads the block — the sign decides which
            # plays exist at all before the magnet says where the pin sits
            "gamma_sign_at_spot": gamma_word,
            # wt-8 (Fix 5): how HARD that sign binds — the normalized net/gross share at
            # spot, its OWN field (survives when shove is None), absent in the tie band
            **({"net_gex_binding": binding_word} if binding_word else {}),
            # wt-9: the 1-day change in whole-book net gamma — is the regime intensifying
            # or fading? An entrenchment scaler, not a direction (absent on a flat day).
            **({"regime_intensity_1d": regime_1d_word} if regime_1d_word else {}),
            "pull_toward_magnet": pull_word,
            "magnet_sigma_from_spot": magnet_sd,
            # wt-11: the magnet's TIE, in numbers — median top1−top2 gap is
            # 0.99pp of mass on this tape, and pin_top_share alone hid it
            **({"magnet_band": _mb_payload} if _mb_payload else {}),
            **({"magnet_contested": contested_word} if contested_word else {}),
            # the Fade lens's wall-clamped revert-target — present only when the tape
            # is armed; a fade context read, NOT the gravity pull (absent, never null)
            **({"fade_revert_target_sigma": fade_target_sd,
                **({"fade_target_wall_clamped": True}
                   if rev.get("magnet_out_of_band") else {})}
               if fade_target_sd is not None else {}),
            "gamma_flip_sigma_from_spot": flip_sd,
            **({"regime_transition_band": transition_word} if transition_word else {}),
            # wt-10: the §4 fences, measured — the chop band's three-state refinement
            **({"transition_zone_measured": transition_zone} if transition_zone else {}),
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
            # wt-11: charm/vanna as NEUTRAL strength+level blocks — the raw sign
            # fields were near-constants (97%/96% one-way) and the drift word's
            # gate never once opened; what varies is what ships
            **({"charm": charm_block} if charm_block else {}),
            **({"vanna": vanna_block} if vanna_block else {}),
        },
        # wt-8 (Fix 2): only the WINDOWED tape rides — the whole-day cumulative is gone.
        # Attached only when it carries a read (payload hygiene: a cold window ≠ zero flow).
        **({"live_flow": {"tape_recent_window": flow_recent_word}} if flow_recent_word else {}),
        # wt-10 (§7): today's volume vs standing OI at the operative walls — live
        # battleground or untested shelf; advisory on wall RELIABILITY, never direction
        **({"flow_confirmation": flow_confirm} if flow_confirm else {}),
        # wt-10 (§3.4): the tag ladder — ranked gamma walls + OI/volume/delta peaks,
        # locations only; confluence (several lenses on one level) outranks bar length
        **({"strike_tags": strike_tags} if strike_tags else {}),
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
        # wt-11: change over the stated window for the top magnet strikes —
        # building vs fading beats any static level; intersection-denominated
        # so a strike-window shift can never read as flow
        **({"momentum": momentum} if momentum else {}),
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
            # wt-11: the timestamp-true 30-min path — the live number the
            # scale.move_30min_sigma spread exists to size against
            **({"moved_last_30min_sigma": moved30} if moved30 is not None else {}),
            "last_bar_turned_back": rev.get("reaction"),
            "range_vs_expected_move": t.get("range_em"),
            # wt-10 (§6.5): the remaining reach, √time-decayed — a SIZE cap, no direction
            **({"expected_move_decay": em_decay} if em_decay else {}),
            "variance_ratio": t.get("variance_ratio"),
            "vix_term_structure": t.get("vix_ts"),
            # wt-11: is implied vol rising or falling NOW — the switch that arms
            # vanna/charm; omitted on a late_day ruler (the τ→0 solve balloons
            # on clock mechanics, not vol)
            **({"vol_trend": vol_tr} if vol_tr else {}),
            # m8: HOW VIOLENT, and WHO OWNS THE MOTION — the speedometer, finally in
            # the room. Absent (never null) when the feed is not clean.
            **({"tape_speed": speed_words} if speed_words else {}),
        },
        # wt-11: the under-pull guard — flags that license the history tool /
        # outside-world reach; omitted entirely when there is nothing to say
        **({"history": history} if history else {}),
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


def _prompt_parts(payload: dict, blind_verdict: dict | None = None) -> tuple[str, str]:
    """Assemble the prompt SPLIT at the cache seam: (doctrine, body).

    `doctrine` is byte-identical on every call, every scan, all day — it rides in
    the system block (`--append-system-prompt`) where the server caches it once and
    reads it back at a tenth the price. `body` is everything that changes scan to
    scan: the reveal preamble, the SCENE, and the output shape. Splitting here is
    purely a transport decision — the model receives the same text in the same
    order as `_prompt` returns.

    With `blind_verdict` set this is the REVEAL pass: the model sees its own blind
    answer + the gates' verdict and must confirm or revise, explaining any override
    of the gates. That preamble lives in the BODY, not the doctrine, so the blind
    and reveal passes share one cached prefix instead of forking it.
    """
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
        "  * THE FENCES ARE THE MEASURED VERSION (wt-10): transition_zone_measured carries the "
        "cT/pT fences read off today's actual gamma surface — a THREE-STATE regime read: above "
        "cT the calm side is measured-confirmed, below pT the fast side is, and INSIDE the "
        "zone the measured answer is NO REGIME (same stand-down as the chop band, but measured "
        "rather than assumed — trust it over the fixed band when both are present). "
        "zone_width_sigma is the ambiguity DEPTH: a wide zone means the whole neighborhood is "
        "regime-less, so regime-dependent plays deserve less conviction even outside it. A "
        "MISSING fence means that side never decisively resumes control — the zone has no "
        "measured edge there. Fences are boundaries of behaviour, NEVER walls or targets.\n"
        "- THE MAGNET IS A LOCATION, NOT A DIRECTION — AND USUALLY A TIE. pull_toward_magnet "
        "names where the map's pin sits — a LOCATION/late-day pin target (intraday pull toward "
        "it hit ~33% in backtest); it is NOT a directional default at any hour under any "
        "regime. Use it to place pins and fades, never to pick a drift direction. "
        "magnet_band carries the top strikes' shares and the top1−top2 gap in pp of mass: "
        "the median gap on this tape is ~1pp, so when gap_pp is small NO strike is really in "
        "charge — say so rather than naming one; there is no threshold where it flips, read "
        "the number. If magnet_contested is present, the field has TWO pins — expect chop "
        "between them, not a clean run.\n"
        "- MOMENTUM BEATS ANY STATIC LEVEL (wt-11). momentum reports how the top magnet "
        "strikes' share of gamma mass moved over the stated window (building / fading / "
        "steady, with today's traded volume as the hedge on the word). A building pin is "
        "worth more than a big one; a fading wall is worth less than its size. Keys are "
        "σ-distances; absent = not cleanly measured this scan, never zero.\n"
        "- BINDING STRENGTH IS A CONVICTION SCALER (wt-8). net_gex_binding is the NORMALIZED "
        "net/gross gamma share at spot — how one-signed the book is, comparable across days and "
        "hours where the raw $ is not. It does NOT pick a direction; it scales conviction on the "
        "direction the gamma sign already permits: a strongly POSITIVE share is a hard PIN (size "
        "the fade/pin up), a strongly NEGATIVE share is real AMPLIFICATION (size the continuation "
        "up), and a share near 0 is a thin book — low conviction either way. Absent in the tie "
        "band (the sign is already unknown there).\n"
        "- REGIME INTENSITY IS THE 1-DAY TREND (wt-9). regime_intensity_1d reports how the "
        "WHOLE-BOOK (0-7DTE) net gamma moved vs yesterday's close — a long-gamma pin entrenching "
        "or weakening, short-gamma amplification deepening or easing, or a flip across zero (the "
        "field names the state; read it, don't re-derive it from the sign of the change). It is "
        "an ENTRENCHMENT scaler, NOT a direction: it says how DURABLE today's regime is (a "
        "deepening short-gamma book is less likely to flip calm intraday). It is repriced at "
        "spot, so part of the move is just the overnight gap — treat it as SOFT context that "
        "scales conviction, never a drift call, and confirm against today's 0DTE gamma sign. "
        "Absent on a flat day or a missing prior close.\n"
        "- WALL DOMINANCE DECIDES WHICH WALLS MATTER — LABELS DON'T (GW vocab). "
        "wall_cluster_above / wall_cluster_below carry the nearest measured wall cluster "
        "each side: its span in σ and its PRIME TIER — ' minor, '' significant, ''' "
        "dominant (share of the strongest structure on the surface). A ''' wall outranks "
        "a ' wall: a pin or fade at a ''' wall is backed by real mass; a ' wall is thin "
        "and breaks cheap. Weigh the tier, never the name.\n"
        "- FLOW CONFIRMATION SEPARATES LIVE WALLS FROM PAPER SHELVES (wt-10). "
        "flow_confirmation reads TODAY'S options volume against STANDING OI at each operative "
        "wall: a CONTESTED wall is a live battleground whose verdict is being decided right "
        "now (weigh siege/effort there, not the bar length); a VOLUME-BUILT level is today's "
        "0DTE flow and can melt into the close; an UNTESTED standing-OI shelf has not been "
        "fought today — its strength is UNPROVEN, so a pin/fade leaning on it deserves less "
        "conviction than one at a contested-and-holding wall. volume_king marks where today's "
        "heaviest volume actually sits when it is at NEITHER wall — expect live reactions "
        "there even though the wall map does not mark it. Advisory on wall RELIABILITY only — "
        "it never picks a direction. Absent = thin tape or no surface; absence ≠ untested.\n"
        "- TAG CONFLUENCE OUTRANKS BAR LENGTH (wt-10). strike_tags is the doctrine tag "
        "ladder: ranked gamma walls (P1/C1 = largest each side, Ab = largest regardless of "
        "side), the standing-OI peaks (POI/COI/AbOI/nPOI — structure, they BUILD the map), "
        "today's volume peaks (CV/PV/nCV/nPV — which parts of the map are LIVE now), and the "
        "flow-signed delta-pressure peaks (D+/D−). These are LOCATIONS, never a direction. "
        "The one rule: when several INDEPENDENT lenses land on the same level (within "
        "~0.05σ), that level is independently corroborated — weight it above a longer bare "
        "bar; a stacked level that also carries a volume tag is the strongest level on the "
        "board. A missing family (e.g. no OI side-split, thin tape) is absent, not zero.\n"
        "- AN AIR POCKET EXTENDS THE OBJECTIVE (wt-9). air_pocket_above / air_pocket_below name "
        "a stretch of CLEAR space (in σ) behind the nearest wall, before the next same-side "
        "structure. Thin space has no hedging to slow price, so once the near wall breaks the "
        "move runs to the FAR side of the pocket — set the objective at the far wall, not the "
        "near one. It is a MAGNITUDE read, not a direction or a trigger, and it is SUBORDINATE "
        "to the gamma sign: it applies only where a break is permitted (short / confirmed-"
        "negative gamma, where accelerant + empty space is the runaway combination) — under "
        "LONG gamma a break is faded, so the pocket is dormant and never a reason to project a "
        "breakout target. Absent = no measured same-side gap this scan (and suppressed entirely "
        "on a MESSY profile).\n"
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
        "- THE DAY'S REACH DECAYS (wt-10). tape.expected_move_decay reports how much of the "
        "full-day expected-move band remains — reach shrinks as √(time-left), so a full-day-"
        "sized target called mid-afternoon is not realistic even when the thesis is right. Cap "
        "magnitude_sigma and range_sigma to roughly the remaining share. It is a SIZE cap "
        "layered on top of the pace guidance, never a direction and never a stand-down; the "
        "confirmed-negative-gamma ignition exemption above still applies to DIRECTION, but "
        "even an ignition ride sizes within the remaining reach.\n"
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
        "- CHARM AND VANNA HAVE STRENGTH AND A LEVEL, NEVER A DIRECTION (wt-11). Their raw "
        "signs are locked by the assumed-sign book (near-constant on every recorded row), so "
        "a sign-derived drift word would be a permanent lean wearing signal clothes — none "
        "ships, and you may NEVER re-derive one. What the scene carries is honest: "
        "charm.strength (uncalibrated hedge re-balancing $/day — compare day to day, grips "
        "hardest in the last ~2h) and charm.funnels_toward_sigma, the LEVEL the clock pulls "
        "settlement toward — a place pinning gets easier, not a promised move. vanna.strength "
        "matters only while tape.vol_trend is actually rising or falling — flat vol, dormant "
        "vanna. vol_trend is the switch; read it first.\n"
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
        "cocked/fired break on the same side; a QUIET wall makes a pin at it safer. Each row's "
        "role field is the target-selection LEAN (wt-9), and it is SUBORDINATE to the gamma "
        "regime just like the verdict: a QUIET wall leans HOLD (a terminus-type pin/turn level "
        "IF the regime supports it), a SIEGE wall leans BREAK (a pause-type speed bump whose "
        "objective lies past it) — but ONLY where the regime permits a break; under LONG gamma "
        "even a SIEGE wall is not a license to trade through, it only lowers the pin's safety. "
        "Absent when the feed is degraded or no touch carried a verdict.\n"
        "- DEX IS THE DIRECTION SIDE — ADVISORY ONLY THIS ERA. dealer_delta_dex is the net "
        "dealer DELTA still to re-hedge: mechanical pressure, the strongest DIRECTIONAL read "
        "in the stack when large — but here it is UNGRADED and record-only. Treat it as "
        "context that sharpens a read the gamma sign and the tape already support; NEVER a "
        "standalone direction, never a reason to override the gamma-sign defaults. The naive "
        "magnitude's sign is fixed BY THE FORMULA (positive on every recorded row) — it is "
        "never evidence and never means dealers are long or bullish; only "
        "net_change_30min_bn can be news there. Weigh the flow_signed_read (whose sign is "
        "actually alive) and the standing_inventory_geography split.\n"
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
        "direction before −0.30σ within 120 min. SIZE AGAINST THE MEASURED SPREAD (wt-11): "
        "thirty-minute moves on this tape have a SPREAD, not a typical size — "
        "scale.move_30min_sigma gives it (half under 0.08σ, one in five over 0.15σ, one in "
        "twenty over 0.27σ, worst recorded 0.46σ, with how many sessions it rests on) and "
        "tape.moved_last_30min_sigma is the live number beside it. Size your magnitude "
        "against the whole spread — a big call needs a named force behind it, but the tail "
        "is real, so never let the median become your ceiling. The √time decay cap and the "
        "pace guidance still layer on top.\n"
        "- ABSENT MEANS UNMEASURED; A CLEAR-FLAG MEANS MEASURED-EMPTY (wt-11). Any missing "
        "field was not cleanly measured this scan — treat absence as no data, never as "
        "neutral, never as zero. The OPPOSITE case is stated outright: "
        "wall_cluster_above_clear / wall_cluster_below_clear mean the surface WAS read and "
        "holds nothing on that side — price in open air is a real reading, often the loudest "
        "one in the scene. Never confuse the two.\n"
        "- WHEN TO REACH OUTSIDE THE SCENE (wt-11 — both optional, most scans need "
        "neither). History: run `" + _RAG_CMD + " query --help` for narrative recall of "
        "your own past reads (today's slices, day summaries, standing terrain), and `"
        + _RAG_CMD + " series --help` for the numeric spot/magnet/walls series by time "
        "(and per-strike with --strike). Reach for them when something does not add up "
        "from the live snapshot alone — price testing a level unseen today "
        "(history.level_unseen_today flags it), a level acting out of character, or a "
        "cross-day question. Exact asks go through the numbers (--date/--from-date/"
        "--to-date, --min-move signed %, --near-strike) — use --text only for meaning, "
        "never for dates or biggest/worst. Outside world: when the tape is genuinely "
        "abnormal (history.abnormal_tape), a human would ask WHY — use WebSearch for the "
        "catalyst (a print, macro news) or context. Extreme cases only, not every scan. "
        "History is context, NEVER the trigger — the live scene stays primary, and a "
        "recalled read is your own past opinion, not a fact about today.\n"
        "- The checklist numbers are evidence, not the answer — judge the whole scene.\n"
    )
    scene = f"SCENE:\n{json.dumps(payload, indent=1)}\n"
    reveal = ""
    if blind_verdict is not None:
        reveal = (
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
    return head, reveal + scene + tail


def _prompt(payload: dict, blind_verdict: dict | None = None) -> str:
    """The whole prompt as ONE string — the shape the replay path and the tests
    read. The live path uses `_prompt_parts` instead so the doctrine can ride in
    the cached system block; the text and its order are identical either way."""
    head, body = _prompt_parts(payload, blind_verdict)
    return head + body


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


def _ask_claude(prompt: str, model: str, timeout: float = CALL_TIMEOUT_S,
                system: str | None = None):
    """ONE `claude -p` subprocess call — deliberately NOT the watch package's
    claude_cli wrapper: that one retries with backoff (3 attempts), which is
    right for a nightly brief and wrong inside a 5-minute scan tick. Here a
    slow call fails ONCE and the tick moves on (fail-open).

    EXACTLY TWO TOOLS (wt-11, the sr-2 grant): the lefteye_rag history CLI as
    one allow-listed Bash prefix, and WebSearch for the abnormal-tape catalyst
    check. Everything else stays stripped (DENY_TOOLS) so the schema bill stays
    small — the 07-31 A/B's cost finding holds, and its pre-registered
    fired-rate kill condition keeps running unchanged under this era.

    `system` rides in --append-system-prompt: an APPEND, never --system-prompt.
    Replacing the CLI's own system block measured 8x cheaper but collapsed the
    model's reasoning (output 3.3k -> 0.4k tokens on the same scene) — the saving
    was the thinking, which is the one thing this call is for. Same lesson, twice:
    on this call path, cheap and quiet is how a regression gets in.

    Returns (verdict_dict | None, error | None, wall_seconds)."""
    cmd = ["claude", "-p", prompt, "--model", model,
           "--output-format", "json"]     # envelope carries the served model id
    if system:
        cmd.extend(["--append-system-prompt", system])
    cmd.extend(["--allowedTools", ",".join(_ALLOWED_TOOLS)])
    if DENY_TOOLS:
        # --disallowedTools is variadic — it must stay LAST or it swallows what follows.
        # A tool may never sit on both lists (Bash/WebSearch left _NO_TOOLS in wt-11).
        cmd.extend(["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--disallowedTools", *_NO_TOOLS])
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

        # wt-11: today's prior rows feed the rows-derived blocks (vol_trend /
        # momentum / dex change / 30-min path / history flags) — read once,
        # reused by the reveal pass so both passes describe the same tape
        rows_today = _today_rows(now)
        payload = build_payload(telemetry, reveal_gates=False, rows=rows_today)
        payload["day_context"] = {"macro_mood": _macro_mood(),
                                  "session": _session_path(now, telemetry),
                                  "my_recent_verdicts": recent,
                                  "lifetime_scorecards": _scorecards()}

        # --- pass 1: BLIND vote(s) — majority decides ------------------------
        # One doctrine string serves the blind votes AND the reveal below: built
        # once, byte-identical, so the first call of the day warms the server-side
        # cache and every later call reads it back instead of re-sending it.
        votes, walls = [], 0.0
        doctrine, body = _prompt_parts(payload)
        raw, err, wall = _ask_claude(body, model, system=doctrine)
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
            raw, err, wall = _ask_claude(body, model, system=doctrine)
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
            reveal_payload = build_payload(telemetry, reveal_gates=True,
                                           rows=rows_today)
            reveal_payload["day_context"] = payload["day_context"]
            _, reveal_body = _prompt_parts(reveal_payload, blind_verdict=blind)
            raw, err, wall = _ask_claude(reveal_body, model, system=doctrine)
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
        # wt-11: one RAG slice per judged verdict — the tower's own sentence
        # becomes tomorrow's recallable memory. Fail-open: memory must never
        # cost a scan row (the sndk_read rule, kept verbatim).
        try:
            import lefteye_rag
            lefteye_rag.record_slice(telemetry, result, now)
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
    for i, r in enumerate(rows):
        ok, why = interesting(r.get("reversion_extreme") or {})
        if not ok:
            continue
        ts = (r.get("ts") or "")[11:16]
        print(f"--- {ts}  interest: {why}")
        # rows[:i] = the tape as it stood BEFORE this scan — the same causal
        # window the live path hands build_payload (the current row folds in)
        if live:
            doctrine, body = _prompt_parts(build_payload(r, rows=rows[:i]))
            v, err, wall = _ask_claude(body, _model(), system=doctrine)
            print(json.dumps(validate_verdict(v) or {"error": err}, indent=1), f"({wall}s)")
        else:
            print(json.dumps(build_payload(r, rows=rows[:i]), indent=1)[:800], "…\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--replay" in args:
        day = next((a for a in args if a[:2] != "--"), None)
        _replay(day or datetime.now(ET).date().isoformat(), live="--live" in args)
    else:
        print(__doc__)
