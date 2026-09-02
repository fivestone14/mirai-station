#!/usr/bin/env python3
# Interpreter: same venv as sndk_hunter.py, and the shebang cannot pin it — name
# the venv python explicitly or use runtime/scripts/run-sndk-read.sh.
"""
sndk_read.py — the SNDK chart's live reading. One wake = one read row.

WHAT THIS IS NOT: a ported Watchtower. The SPX tower is a forward forecaster
whose call IS the model's opinion. Here the model is asked what it NOTICES —
observations checked number-by-number against the scene, never a direction:
the direction call was asked for a year, measured at zero forward value, and
removed (obs-1).

ONE LANE (obs-3, 2026-09-01). For two months a second, deterministic lane —
the ARROW, a magnet voter with a breadth veto, a dwell clock and a ghost —
drew on the chart beside the reading. It is deleted; the tombstone where
aggregate() stood records the measured reasons. Old read rows keep their
`arrow` field as history; nothing writes a new one.

The design rests on what four sessions of recorded SNDK rows measured
(2026-07-28..31, 756 rows, verified against state/sndk_reversion/):

  * 12 of the ~20 fields a "hand it everything" payload would carry are
    CONSTANTS. dex_word is the identical string on 756/756 rows and renders as
    "Dealers sell into strength" — it read that way through all 189 scans of a
    +12% day. pin_contested/zone1_share/pin_basis/regime_basis: 100% constant.
    A constant that READS like a signal manufactures conviction for free, in
    one direction, forever. They are named in INADMISSIBLE and omitted (never
    nulled) by build_scene, so they can neither vote nor be cited.
  * The magnet is a statistical tie. Median gap between the #1 and #2 strike is
    3.87pp of mass; under 5pp on 62% of scans. A scalar magnet hides that.
  * Median 30-min move is 0.077σ; median |magnet - spot| is 0.612σ. The old
    arrow pointed 8x further than price travels in the window it implied.
  * Counting independent sign RUNS rather than rows, four days hold 42 magnet
    runs and 86 shove runs. Every accuracy claim on this surface is inside the
    noise band. Nothing here is presented as an edge.

Isolation (README doctrine): writes ONLY state/sndk_reads. Never imports
watchtower, never touches the SPX diary, never blocks sndk_hunter's tick —
this is its own launchd job so a slow model call cannot drop a scan.

Two switches, and they are NOT the same thing:
    SNDK_READ_DISABLE=1   KILL — no row, no record at all (checked
                          here AND in run-sndk-read.sh).
    sndk_reads/control.json  PAUSE — silences only the model's sentence; every
                          deterministic layer keeps running and every row keeps
                          landing on disk (see reasoning_on).

CLI:
    python3 sndk_read.py              # one wake check (RTH-gated)
    python3 sndk_read.py --force      # bypass the RTH gate
    python3 sndk_read.py --dry        # decide + build the scene, print, NO model call
    python3 sndk_read.py --replay DAY # re-run the gate over a recorded day,
                                      # print the wake timeline, write nothing
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import sys
import time as _clock
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_LEFT_EYE = str(_SKILL_DIR.parent / "mirai-left-eye")
if _LEFT_EYE not in sys.path:
    sys.path.insert(0, _LEFT_EYE)

import atomic_io                       # noqa: E402
import gw_vocab as _gw                 # noqa: E402 — the ONE clustering rule
try:
    import sndk_bars                   # noqa: E402 — the minute-bar sidecar's reader half
except Exception:                      # the reader must keep running without it
    sndk_bars = None
PRIOR_BARS_MIN = getattr(sndk_bars, "COMPLETE_SESSION_MIN_BARS", 300)
                                       # (walls ladder = the ladder's own walls)

_ET = ZoneInfo("America/New_York")
_SQRT_TDAYS = math.sqrt(252.0)         # engine trading-days constant (√252)

ERA = "obs-5"               # bump on ANY change to the gates or the prompt.
                            # obs-5 (2026-09-02): THE MINUTE-BAR SIDECAR. The
                            # session's highs and lows, the opening box, every
                            # box break and the prior sessions' range now read
                            # state/sndk_bars/<day>.jsonl — one completed
                            # minute per line, written by its own launchd job
                            # (sndk_bars.py) — and fall back to the 2-minute
                            # spots when the file is absent or thin, saying
                            # which in price.extremes_from and
                            # context.ranges.measured_from. Measured before:
                            # scan-sampled extremes sat 0.10σ (~$7) short, the
                            # opening box ~$8 narrow, one box break in three
                            # missed or invented.
                            # obs-4 (2026-09-02): four repairs to the frame and
                            # one new block — all payload, none of it a forecast.
                            # (1) held_between_since_last_read is SEEDED with
                            # spot_then, so the range runs from where price was
                            # at the last reading: a $34 drop shipped as "held
                            # between 1518 and 1546" on 09-02 10:01 because the
                            # start sat outside its own range. (2) the frame's
                            # "unchanged" walls compare ladder to ladder; the
                            # old "now" was the diary scalar and the two
                            # disagreed inside one block (ladder 1550, diary
                            # 1600, "unchanged 1600"). (3) no diary wall is ever
                            # frozen in the gate when a per-strike surface
                            # exists: a side the ladder found empty is empty,
                            # never "1500, unchanged" beside "no put wall"
                            # (09-01 12:33). (4) the guard matches word STEMS,
                            # checks above/under against the live spot, and a
                            # second small model reviews the surviving sentence
                            # for forecast or judgement. NEW: context.ranges —
                            # the opening box, the box in force, today's breaks
                            # and the prior sessions' range: measured, no verdict.
                            # The store is era-stamped so a later read of the
                            # history can never blend two rule sets.
                            # sr-8 (2026-08-30): the payload pays only for what
                            # VARIES. 30 leaves left the scene — 18 that held a
                            # single value across all 4,149 recorded scans, and
                            # 12 exactly reconstructable from leaves that stay
                            # — and their explanations moved into _DOCTRINE,
                            # which is prompt-cached and so costs about a tenth
                            # per repeat. Measured 3,868 -> 2,645 bytes, 144 ->
                            # 115 leaf names. Nothing measured was lost: what
                            # went was second copies, standing facts about the
                            # instrument and the feed, and one-step arithmetic.
                            # `sigma_measured_from` is INVERTED — it named the
                            # rule on every scan and now names only the
                            # exception. Fields deliberately KEPT though
                            # derivable: top_strike_lead_pp and walls[].sigma
                            # (the read leans on them, and reasoning tokens are
                            # paid for too) and front_expiry.expiry_date
                            # (business-day arithmetic). One rename, no bytes:
                            # vwap_dist_sigma_from_live_spot ->
                            # vwap_minus_live_spot_sigma, because 13 of 15
                            # reviewers read the old name's sign backwards.
                            # sr-7 (2026-08-30): PROVENANCE, and leaf names that
                            # survive being read alone. The scene was honest
                            # about arithmetic and silent about time — 0 of 53
                            # leaves carried a source or a timestamp, and one
                            # overnight OI snapshot was re-priced on every one
                            # of ~190 daily scans
                            # against a live quote and told in the present
                            # tense. Then: every block declared built_from
                            # (sr-8 moved that map out of the payload);
                            # data_sources carries the scan / quote / BOOK
                            # clocks separately (the feed serves a cached book
                            # on half the scans, so book age was understated by
                            # 2-4 min on every one of them); book-derived sigma
                            # distances measure from meta.chain_spot, which the
                            # live spot misses by a median $2.13 and by $41.99
                            # at worst; percentiles count DISTINCT BOOKS, not
                            # scans; momentum's window is told on the book
                            # clock and its "building/fading/steady" verdict is
                            # gone (a fitted 0.5pp constant, the is_a_tie
                            # pattern again); and freshness_rules DELETES a
                            # block whose source aged out rather than labelling
                            # it, because labelling was already tried. Renames:
                            # dealer_flow -> dealer_positioning, gex ->
                            # share_of_book_gamma_pp, price.now -> live_spot,
                            # clock.book_age_min -> data_sources.options_book.
                            # age_min (and finally measured off the book).
                            # sr-2 (2026-08-02, blueprint v3): the verdict left
                            # the payload — build_scene no longer carries
                            # arrow_already_decided; the model now infers its
                            # OWN vector + magnitude from an unbiased scene
                            # (aem / vol_trend / flip / charm / momentum /
                            # dealer_flow / walls ladder added), with the
                            # on-demand history tool + external fetch. The
                            # deterministic arrow is unchanged and still draws.
                            # sr-3 (2026-08-08): the flip band stops speaking
                            # four times. named_levels_sigma_from_spot and
                            # lowest_named_level left the scene — measured on
                            # 923 recorded rows, hvl IS gamma_flip and the edges
                            # are that centre ±0.25σ exactly, so those keys
                            # restated flip_block's own ct/pt/center numbers and
                            # gave one measurement the appearance of three
                            # agreeing levels. The doctrine now says the edges
                            # are arithmetic. Gates and arrow unchanged.
                            # sr-4 (2026-08-10): the scene learns what day it
                            # is — clock grows date, minutes_to_close and
                            # front_expiry {dte, date}, all from values already
                            # on the row. Weekday deliberately absent (== dte
                            # on every recorded session). Gates, arrow and the
                            # output schema unchanged.
                            # sr-5 (2026-08-10): two honesty fixes, one bump.
                            # (a) Measured-empty stops impersonating unmeasured
                            # — walls ship *_side_clear and the flip ships
                            # none_on_board when the board was read and holds
                            # nothing, so "price in open air" no longer looks
                            # like a dead sensor. (b) The 0.08 point becomes
                            # the measured SPREAD (scale.move_30min_sigma:
                            # half_under/one_in_five_over/one_in_twenty_over
                            # + sessions_measured) in scene, doctrine AND the
                            # voice doctrine — the model's magnitudes copied
                            # the single number as a ceiling.
                            # sr-6 (2026-08-11): the pulse check. A book whose
                            # newest row is older than STALE_BOOK_MIN never
                            # wakes the model (the heartbeat would otherwise
                            # narrate a frozen board every 45m); rows land
                            # stamped wake="stale_book" + book_age_min, and
                            # the scene ships clock.book_age_min so marginal
                            # staleness is visible instead of laundered.
PINNED_MODEL = "claude-sonnet-5"       # exact id, never an alias (drift protection)

# --- wake gate ---------------------------------------------------------------
# The SPX tower's own fingerprint thresholds STARVE here and it is measurable,
# not a matter of taste: SNDK's sigma runs 8-10% of spot (~$100 on a $1,200
# stock) against SPX's 0.5-0.8%, so MAGNET_JUMP_SIGMA=0.5 sits ~4x above the p90
# of SNDK's real scan-to-scan magnet movement (0.12-0.13σ). Ported unchanged the
# gate fires 8-11x/day of which most are heartbeat filler — a clock wearing an
# event gate's clothes. Retuned to SNDK's own scale it fires 20-32x/day
# (23/22/20/32 replayed over 07-28..31) with the heartbeat filling only 1-4 of
# those slots, i.e. genuinely event-driven.
# wk-1 (2026-08-31). The gate now measures on the BOOK clock, against the state
# at the last time it SPOKE, and every discrete flip needs two consecutive books
# before it counts. Replayed over 23 stored sessions the old gate fired 21.7
# times a day, 26.0% of them with no material change since the previous fire,
# with a worst half-hour of 6 calls; these constants give 18.3 / 18.4% / 4 with
# the miss rate unchanged inside noise (26.7% against 25.3%).
#
# THE FRONTIER IS REAL AND THE PLAN'S TABLE WAS WRONG ABOUT IT. The Phase 3
# proposal claimed a variant cutting false alarms fivefold WHILE ALSO reducing
# misses; measured, that variant (min_gap 10, spot 0.30sigma, iv 3.0pp, flip
# 0.55sigma, heartbeat 90) fires 12.6 times a day at 9.7% false alarms but
# misses 36.9% of material changes against today's 25.3%. Quiet is bought with
# misses at every point on the curve. These values are the knee: most of the
# noise reduction, none of the deafness. Move them together, and re-measure —
# do not tune one in isolation.
WAKE_SPOT_SIGMA = 0.20      # spot travelled this far since it last SPOKE
WAKE_IV_PP = 3.0            # implied vol moved this far on the SMOOTHED series.
                            # 3.0 and not 2.0: the p95 of a one-step change on
                            # the SMOOTHED series is 2.08pp, so a 2.0 threshold
                            # sits underneath the noise floor and fires on the
                            # sensor. Measured, it was 36% of all wakes.
WAKE_FLIP_CROSS_SIGMA = 0.02  # a flip CROSSING must clear this much on the far side —
                              # a hair more than the 2-minute wobble — or it is noise
WAKE_FLIP_SIGMA = 0.50      # the flip level itself relocated this far. The p95
                            # of a one-step flip move is 0.56 sigma, so anything
                            # under ~0.5 is inside the ordinary wander.
WAKE_IV_MEDIAN_BOOKS = 5    # atm_iv is mostly measurement noise: median |change|
                            # is 1.07pp one book apart but only 1.71pp eight
                            # books apart, where a random walk would give 3.03.
                            # A 5-book rolling median cuts the one-book figure
                            # 83% to 0.18pp and barely touches the eight-book
                            # one, so an IV trigger reads the median or it is
                            # triggering on the sensor rather than on vol.
WAKE_CONFIRM_BOOKS = 2      # a discrete flip must hold for this many CONSECUTIVE
                            # distinct books. Measured on the tape: the gamma
                            # sign reverts one book later 37% of the time, the
                            # call wall 31%, the put wall 24%, the top strike
                            # 18%. Without confirmation a third of every
                            # structural wake is flicker.
STALE_BOOK_MIN = 6          # newest diary row older than this → the tape is
                            # not breathing (scanner down, halt, feed refusing
                            # ticks) and the model is NEVER woken: a sentence
                            # about a frozen board is worse than silence, and
                            # the heartbeat wake would otherwise fire on
                            # exactly this case every 45 minutes. Three missed
                            # 120s ticks; the worst recorded live hole (277s,
                            # 08-05 fetch refusal) stays under it.
HEARTBEAT_MIN = 60          # read at least this often even on a dead tape
MIN_GAP_MIN = 10            # spam guard: no two reads closer than this. Raised
                            # from 8 in wk-1: the median gap under the old gate
                            # was 12.3 minutes with a p10 of 8.2, so 8 was
                            # binding often enough to be the real cadence.
DAILY_CALL_CAP = 30         # hard ceiling, and it must stay a BACKSTOP rather
                            # than a budget. Measured uncapped over 23 replayed
                            # sessions the wk-1 gate wants a mean of 18.7 a day,
                            # median 18, p90 26, busiest 27 — so 24 bound on 7
                            # of 23 days and was quietly doing the limiting that
                            # the logic is supposed to do. A day that reaches 30
                            # is a day to go and look at, not a day the budget
                            # worked.

# --- the magnet's tie rule ---------------------------------------------------
# The top strike only counts as separated once it has beaten the runner-up by
# this much of the day's gamma mass; magnet_band flags `tie` until it clears.
# Pre-registered before the surface shipped, not fitted to it.
MAGNET_SEP_PP = 5.0         # top1 - top2 share of gamma mass, percentage points.
                            # Median gap is 3.87pp, so this is silent by default
                            # and speaks on ~38% of scans.
# --- what the model may NOT cite ---------------------------------------------
# Both of these gate the SENTENCE. A reading is only honest if the number
# behind it could still have changed today.
FROZEN_MIN = 30             # a field unchanged this long renders as frozen and
                            # may not be cited as a reason for a NEW call
# Fields measured constant across all 756 recorded rows. Named here rather than
# detected at runtime so the ban is auditable: these may never vote and may never
# be cited. dex_word is the dangerous one — a permanently bearish-READING string.
INADMISSIBLE = ("dex_word", "pin_contested", "zone1_share", "pin_basis",
                "regime_basis", "dex_flow_word", "dex_flow_signed",
                "variance_ratio", "vix_ts", "event_flag")

# --- scene v2 derivations (each measured-or-absent; thresholds from the
# --- recorded 07-28..31 distributions, pre-registered here) ------------------
MOMENTUM_ROWS = 5           # rolling window: the last 5 SCANS — the
                            # blueprint's "3-5 snapshots"; 3 was twitch
                            # (share-change p50 −0.04pp over 3 rows). Note it
                            # is 5 scans, not 5 books: with the feed's ~50%
                            # cache rate those 5 scans carry 3 distinct books
                            # and span 8-12 minutes, which is why the window
                            # is REPORTED off the book clock (sr-7) instead of
                            # being labelled "10m" off a 120s scan cadence.
VOL_TREND_MIN = 28          # minutes of history before a 30-min IV read exists
VOL_TREND_MAX = 45          # a reference older than this is an outage artifact,
                            # not a 30-min window — the read is omitted instead
VOL_TREND_FLAT = 2.5        # |Δ IV| under this many vol pts reads "flat"
                            # (recorded Δ30m: p10 −9.0 / p50 +1.8 / p90 +12.6)
_WALL_AGE_LOOKBACK = 120    # rows walked back when ageing a wall (~4h at 120s).
                            # A bound, not a judgement: past it the age reports
                            # absent rather than making the read quadratic.
# obs-4: RANGES, told as boxes. The first OPENING_RANGE_MIN minutes of the tape
# form the opening box; a frozen box is BROKEN when a scan sits beyond it by
# more than RANGE_BREAK_SIGMA (about $3 on this name — the median 2-minute
# wobble is $2.12, so a one-dollar poke is not a break); a break starts a new
# box, which forms over BOX_FORM_MIN minutes and then freezes in its turn. The
# broken box is history and the new one is the box in force — "range bound"
# inside a box that was breached is not a claim anyone should be handed. The
# prior sessions' range is a separate fact on a separate clock: it can hold
# while today's box breaks, and the block says both.
OPENING_RANGE_MIN = 30
BOX_FORM_MIN = 30
RANGE_BREAK_SIGMA = 0.05
PRIOR_RANGE_SESSIONS = 5
RANGE_BREAKS_MAX = 8
# obs-4: the frame is A MOVE when spot travelled this far since the last
# reading, and the doctrine tells the model to open with then->now rather than
# with the "held between" sentence — the template the 09-02 10:01 read reached
# for across a 0.6-sigma drop.
FRAME_MOVE_SIGMA = 0.15
# obs-4: THE SEMANTIC GUARD. The word list catches the words it knows; a second
# small model reads the surviving sentence for what a list cannot see — a
# forecast, an expectation, a judgement about where price goes — and a hit
# deletes the text the way a banned word does. Fails OPEN: a judge that errors
# or times out is recorded as skipped, never treated as a verdict.
SEMANTIC_GUARD = True
SEMANTIC_GUARD_MODEL = "claude-haiku-4-5-20251001"
SEMANTIC_GUARD_TIMEOUT_S = 40.0
SEMANTIC_GUARD_EFFORT = "low"
PCTL_CACHE_V = 3            # bump to invalidate pctl_prior.json — v3 adds the
                            # per-DAY medians novelty is ranked against; v2 counts
                            # one observation per BOOK, not per scan (sr-7)
PCTL_MIN_SESSIONS = 5       # a number's percentile against its own history is
                            # OMITTED under this many prior sessions. Round-2
                            # measured that per-day medians on this tape swing
                            # 3x, so a percentile drawn from three days is a
                            # sentence about three days wearing the authority
                            # of a distribution. n always ships with the word.
PCTL_MAX_SESSIONS = 22      # and it forgets past this, like terrain's month
# sr-7 — the three things a scene number can be measured FROM, named once so a
# block can declare its own provenance and a reader never has to infer it. These
# strings are the scene's public vocabulary: `built_from` on a block, and
# sr-8: these names are no longer echoed back inside the payload — `built_from`
# left the scene and `present_tense_forbidden_for` moved into the doctrine, which
# interpolates PRESENT_TENSE_FORBIDDEN_FOR directly. They remain the vocabulary
# BUILT_FROM and the freshness gate are written in, and the doctrine speaks them
# aloud, so the names still have to mean the same thing in both places.
WALL_CLOCK = "wall_clock"               # the session calendar — never ages
LIVE_TAPE = "live_tape"                 # this scan's quote/path — seconds old
OPTIONS_BOOK = "options_book"           # the chain snapshot: minutes old, cached
OI_SNAPSHOT = "open_interest_snapshot"  # last night's close — NOT intraday

# sr-7 freshness gate. A leaf whose source is older than its ceiling is DROPPED,
# not annotated: the scene's own doctrine is "absence = not measured", and an
# observation nobody can date is exactly that. The book ceiling matches
# STALE_BOOK_MIN so the gate the wake uses and the gate the payload uses can
# never disagree; the quote ceiling is deliberately tighter — a quote is the one
# thing here that is supposed to be seconds old.
MAX_BOOK_AGE_MIN = float(STALE_BOOK_MIN)
RULER_MAX_DRIFT = 0.25      # a chain_spot further than this from the live spot
                            # is a broken field, not a stale one: the worst
                            # recorded gap over 3,392 August rows is $43.27 on a
                            # ~$1,480 name (2.9%), so 25% cannot reject real data

# ONE ceiling, and it is the book's. The first draft of sr-7 also carried
# MAX_QUOTE_AGE_MIN = 2.0 against `now - row.ts`, on the reasoning that a quote
# is supposed to be seconds old. That was a ceiling on the SCAN CADENCE wearing
# a quote's name: the diary carries no quote timestamp at all (meta has
# spot_source and no clock), the scanner fires every 120s, and the reader fires
# on its own 120s timer — so the ceiling sat exactly on the cadence and deleted
# `price` on 42 of 3,409 recorded August scans, on healthy tape, taking
# live_spot out of a scene whose every other block was still telling the model
# to convert distances using price.live_minus_book_spot_sigma.
#
# Nothing is lost by removing it. Book age is never smaller than scan age, so
# the book ceiling already covers every case a real quote ceiling would, and
# data_sources.scan_taken_at states the scan's own age for anyone who wants it.
#
# open_interest and wall_clock are absent for the opposite reason: OI is ~18
# hours old BY DESIGN (saying so is the fix, dropping it is not) and the
# calendar cannot go stale. A source missing from this table is never dropped.
_MAX_AGE_MIN = {OPTIONS_BOOK: MAX_BOOK_AGE_MIN}

# sr-7: which blocks may never be narrated in the present tense. Everything here
# derives from open interest, which updates once overnight — "dealers are
# buying" about a field measured at yesterday's close is a false statement about
# time, not a debatable read. LEXICON_ENFORCE runs the check in the shadow
# first (the standing house rule for any new guard): the flag is recorded on the
# read row so the rate can be measured before it is allowed to reject anything.
# Which blocks rest on which source, declared once for the whole payload rather
# than repeated inside all nine blocks.
#
# SOURCE -> BLOCKS, not block -> sources, and the direction is the whole saving.
# Written the obvious way round it came out LONGER than the inline lists it
# replaced (+25 chars): a forward map has to spell out all eleven block names as
# keys AND still repeat the long source strings beside each one. Inverted, the
# four source names appear once each instead of eighteen times, and the block
# names — short words like "magnet" — carry the repetition instead.
#
# `clock.front_expiry` is listed apart from `clock` because it is the one thing
# in the session calendar that is NOT the wall clock (dte comes off the book),
# and filing the whole calendar under the book would take minutes_to_close down
# with a dead feed.
BUILT_FROM = {
    WALL_CLOCK: ["clock"],
    LIVE_TAPE: ["price", "history"],
    OPTIONS_BOOK: ["clock.front_expiry", "scale", "price", "regime", "magnet",
                   "breadth", "momentum", "dealer_positioning", "walls",
                   "structure"],
    OI_SNAPSHOT: ["regime", "magnet", "breadth", "dealer_positioning", "walls",
                  "structure"],
}

PRESENT_TENSE_FORBIDDEN_FOR = ("magnet", "walls", "breadth",
                               "dealer_positioning", "regime.charm", "regime.flip",
                               "structure")
LEXICON_ENFORCE = False
_STALE_NOUNS = ("magnet", "wall", "walls", "dealer", "gamma", "charm",
                "breadth", "vanna", "open interest", "oi")
_LIVE_VERBS = ("is building", "are building", "is fading", "are fading",
               "is buying", "are buying", "is selling", "are selling",
               "right now", "currently", "just now", "is stacking",
               "are stacking", "is piling", "are piling")

WALLS_PER_SIDE = 2          # the ladder stops at 2 — deeper strikes are noise

# --- the model call ----------------------------------------------------------
CALL_EFFORT = "medium"      # PIN THE REASONING BUDGET, for the same reason
                            # PINNED_MODEL exists two lines up: an unpinned
                            # setting is production configured from outside the
                            # repo. On 2026-08-31 `"effortLevel": "xhigh"` was
                            # written into ~/.claude/settings.json at 07:11 and
                            # every `claude -p` on the machine started reasoning
                            # ~10x longer. Measured on one live scene: xhigh
                            # 72-121s and 7,100-11,300 output tokens, high 50s,
                            # medium 15-28s, low 9-11s — while the ANSWER stayed
                            # the same ~460 tokens at every level. At xhigh ~95%
                            # of what the model generated was private thinking.
                            #
                            # 12 of 14 obs-1 calls hit the 100s cap that day.
                            # The floor proves the setting moved rather than the
                            # prompt: 08-26/27/28 logged minimum call times of
                            # 4.6, 4.8 and 4.9 seconds across ~95 calls, and
                            # xhigh cannot produce a 5-second call.
                            #
                            # medium reproduces the historical operating point
                            # (live median 17.2s yesterday). No quality cost is
                            # measurable — scored through
                            # check_reading_against_scene, high kept FEWER
                            # observations than low — but that is one sample
                            # per level, so read it as "no measured benefit"
                            # rather than "proven equivalent".
CALL_TIMEOUT_S = 100.0      # one attempt, no retries — a slow read is skipped.
                            # Raised from 60s in sr-2, when the read could still
                            # spend a history lookup or an external search
                            # inside the call; that grant is gone (obs-1) and
                            # the headroom is kept. Still under the 120s tick,
                            # and this is its own launchd job so a slow read
                            # never costs a scan.
# Bash and WebSearch are ON THIS LIST NOW, and the omission was a live hole.
# `--allowedTools` was assumed to be the gate; it is not. Probed directly under
# both the old and the new argv, `claude -p` executed a Bash command and
# returned its output (num_turns 2) — so removing `--allowedTools` in obs-1 was
# a no-op, and the doctrine's claim "YOU HAVE NO TOOLS" was simply false.
# `--disallowedTools` is the only thing that actually gates, and a single
# WebSearch inside a read would eat the entire latency budget on its own.
#
# WHY THERE IS NO GRANT AT ALL. obs-1 revoked the sr-2 tool grant (the history
# CLI plus WebSearch). It was used 0 times across all 391 production calls.
# That alone would not settle it — the doctrine's own named triggers fired 94
# times and the model never reached, which is the PROMPT failing, not the tool
# — but the observation contract removes the reason to reach at all: an
# observation is a statement about the snapshot in hand, and every claim has
# to resolve to a pointer INTO that snapshot. History cannot be cited under
# this contract even when consulted, so granting a tool buys a latency risk
# and a licence to wander with no reachable upside. `sndk_rag` keeps running
# on the write side, filing a slice per read; it is the read side that is
# closed.
_NO_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob",
             "Grep", "WebFetch", "Task", "Agent", "TodoWrite",
             "ExitPlanMode", "BashOutput", "KillShell", "SlashCommand", "Skill",
             "Bash", "WebSearch")


def _state_dir() -> Path:
    env = os.environ.get("MIRAI_STATE_DIR")
    return Path(env) if env else (_SKILL_DIR.parent.parent / "state")


def _diary_dir() -> Path:
    return _state_dir() / "sndk_reversion"


def _reads_dir() -> Path:
    return _state_dir() / "sndk_reads"


def _control_path() -> Path:
    return _reads_dir() / "control.json"


def reasoning_on() -> bool:
    """The live PAUSE switch, distinct from the kill switch.

    SNDK_READ_DISABLE=1 stops this module dead — no row, no record.
    This is the other thing: it silences only the MODEL CALL while every
    deterministic layer keeps running and every row keeps landing on disk. The
    gates, the magnet ranking, the frozen list and the wake reason
    are all pure functions of a row already written, so pausing costs nothing
    but the sentence — and the store stays continuous, which is the whole point
    if these rows are ever going to train anything. A gap in a training set is
    far more expensive than a gap in the prose.

    Fails OPEN (reasoning on): a missing or unreadable control file means
    nobody has ever touched the switch, which is not the same as asking for
    silence."""
    try:
        d = json.loads(_control_path().read_text())
        return bool(d.get("reasoning", True)) if isinstance(d, dict) else True
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return True


def _market_live() -> bool:
    """Authoritative RTH gate, fail-CLOSED (mirrors sndk_hunter._market_live)."""
    try:
        rt = str(_SKILL_DIR.parent.parent / "runtime")
        if rt not in sys.path:
            sys.path.insert(0, rt)
        from watch.intraday import market_status as _ms
        return bool(_ms.check().is_live)
    except Exception:
        return False


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for ln in path.read_text().splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue               # a torn tail line must not hide earlier rows
            if isinstance(r, dict):
                out.append(r)
    except OSError:
        return []
    return out


def _parse_ts(s) -> Optional[datetime]:
    """An ISO stamp -> datetime, or None. Every clock in this module reads through
    here, so a malformed or absent stamp degrades to "unknown" and never to a
    date — an age computed off epoch zero would read as a decades-old field and
    freeze the whole board."""
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _ts(r: dict) -> Optional[datetime]:
    try:
        return _parse_ts(r["ts"])
    except (KeyError, TypeError):
        return None


def _book_asof(row: dict) -> Optional[datetime]:
    """When the OPTIONS BOOK in this row was actually measured.

    sr-7. Every structural number in the scene — magnet, walls, breadth, dealer
    positioning, charm, flip — is re-priced from this snapshot, and the snapshot
    is NOT the scan: the feed serves a disk cache on half the scans, so a row
    written at 11:04 routinely carries a book measured at 11:02 (measured over
    2026-08-28: 188 scans, 94 distinct books, every second row a repeat). Age
    measured off the scan clock therefore reads 0-1 minute on a book that is
    2-4 minutes old, which is the error this function exists to end.

    Falls back to the row's own timestamp when meta.book_asof is absent (early
    fixtures, pre-row_v4 rows) — a fallback that can only UNDERSTATE age, so it
    is stamped as such by `_book_clock` rather than passed off as measured."""
    t = _parse_ts((row.get("meta") or {}).get("book_asof"))
    return t if t is not None else _ts(row)


def _age_min(t: Optional[datetime], now: datetime) -> Optional[float]:
    """Minutes between `t` and `now`, to one decimal. None when undated.

    Module-level and singular ON PURPOSE. sr-7 shipped two of these for an hour:
    read_once floored (`int(secs // 60)`) while build_scene rounded, so a book
    aged in [6.05, 7.00) minutes passed the wake gate and failed the payload
    gate — the model would have been asked for a direction and a magnitude in
    sigma with no sigma ruler, no price and no book in the scene. It happened
    once in 3,409 recorded August scans (2026-08-24 12:18:15, 365.2s), on a
    quiet scan that spent no call. A comment two lines from the constant claimed
    the two gates "can never disagree"; the only way to make that true is for
    there to be one function."""
    if t is None:
        return None
    return round(max(0.0, (now - t).total_seconds() / 60.0), 1)


def _book_is_repeat(row: dict, prev_row: Optional[dict]) -> Optional[bool]:
    """True when this scan re-serves the previous scan's book unchanged."""
    a = (row.get("meta") or {}).get("book_asof")
    # `.get("meta", {})` returns a STORED None rather than the default, so a row
    # written with an explicit `"meta": null` crashed build_scene outright and
    # took the read row and the arrow down with it — the one thing this module's
    # torn-row rule says must never happen. Every other meta read here uses this
    # idiom; this line was the exception.
    b = ((prev_row or {}).get("meta") or {}).get("book_asof")
    if a is None or b is None:
        return None
    return a == b


def _distinct_books(rows: list[dict]) -> list[datetime]:
    """Today's book timestamps with consecutive repeats collapsed, oldest first.

    This is the denominator every "unusual for this tape" claim must use. A
    percentile over SCANS counts each cached book twice and quietly halves the
    effective sample — the tag then describes the scan cadence, not the book."""
    out: list[datetime] = []
    seen = None
    for r in rows:
        raw = (r.get("meta") or {}).get("book_asof")
        key = raw if raw is not None else r.get("ts")
        if key is None or key == seen:
            continue
        seen = key
        t = _book_asof(r)
        if t is not None:
            out.append(t)
    return out


def _median_gap_min(stamps: list[datetime]) -> Optional[float]:
    """Median minutes between consecutive stamps — the measured cadence, never
    an assumed one. None under two stamps."""
    if len(stamps) < 2:
        return None
    gaps = sorted((b - a).total_seconds() / 60.0
                  for a, b in zip(stamps, stamps[1:]))
    m = len(gaps) // 2
    val = gaps[m] if len(gaps) % 2 else (gaps[m - 1] + gaps[m]) / 2.0
    return round(val, 1)


def with_path(row: dict, rows: list[dict]) -> dict:
    """Stamp the row with how far price has travelled in the last ~30 minutes,
    in sigma. The diary posts every ~120s, so 15 rows back is ~30 min; the walk
    is by timestamp rather than by count so a gap in the scan (a dropped tick,
    a late open) cannot silently shorten the window."""
    out = dict(row)
    sig, spot, t = row.get("sigma") or 0, row.get("spot"), _ts(row)
    if not sig or spot is None or t is None:
        return out
    cutoff = t - timedelta(minutes=30)
    ref = None
    for r in reversed(rows):
        rt = _ts(r)
        if rt is None or rt > t:
            continue
        ref = r
        if rt <= cutoff:
            break
    if ref is not None and isinstance(ref.get("spot"), (int, float)):
        rt = _ts(ref)
        # only claim a 30-min read when we actually have ~30 min of history
        if rt is not None and (t - rt) >= timedelta(minutes=20):
            out["_ran_30m_sigma"] = (spot - ref["spot"]) / sig
    return out


# ---------------------------------------------------------------------------
# the magnet, told honestly
# ---------------------------------------------------------------------------
def magnet_band(row: dict) -> dict:
    """The magnet as a BAND over the tied top strikes, never a scalar line.

    pin_top_share ~0.13 does not mean "the strongest strike is weak" — it means
    there is no strongest strike. On 07-31 09:30 the magnet (1500) held 12.8% of
    mass and the runner-up (1390) held 11.4%: a 1.4pp margin deciding the whole
    arrow. Rendering that as one line is the single largest source of false
    confidence on the chart, so this returns the top three, their shares, and
    the gap, and flags `tie` when the gap cannot clear MAGNET_SEP_PP.

    Also reports `in_window`: the strike window is SPOT-RELATIVE
    (max(+-8%, 2sigma/spot), clamped +-25%) and moved from +-8.0% to +-23.4%
    WITHIN 07-31 — so a magnet that "relocated" may only be the telescope moving.
    A caller must not narrate a gravity story about a geometry artifact."""
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    spot = row.get("spot")
    # tolerate a torn surface: non-numeric strikes/masses and NaN are skipped,
    # never allowed to crash the wake (this module tolerates torn JSONL lines
    # by design — a poisoned pair must not silence the reader AND the arrow)
    mb = []
    for pair in (gv.get("mass_by_strike") or []):
        try:
            k, v = _fin(pair[0]), _fin(pair[1])
        except (TypeError, IndexError, KeyError):
            continue
        if k is not None and v is not None:
            mb.append((k, v))
    tot = sum(v for _, v in mb)
    if not mb or tot <= 0 or spot is None:
        return {"top": [], "gap_pp": None, "tie": True, "lo": None, "hi": None,
                "reported": gv.get("magnet"), "in_window": None}
    ranked = sorted(((k, v / tot * 100.0) for k, v in mb),
                    key=lambda x: -x[1])[:3]
    gap = round(ranked[0][1] - ranked[1][1], 2) if len(ranked) >= 2 else None
    band = [k for k, _ in ranked[:2]] or [ranked[0][0]]
    strikes = [float(k) for k, _ in mb]
    return {
        "top": [[round(k, 2), round(s, 2)] for k, s in ranked],
        "gap_pp": gap,
        "tie": (gap is None or gap < MAGNET_SEP_PP),
        "lo": min(band), "hi": max(band),
        "reported": gv.get("magnet"),
        # the observation window, so a jump can be attributed to the telescope
        "in_window": [min(strikes), max(strikes)] if strikes else None,
    }


# the raw shove block off a diary row — breadth_block and _lopsided read it
def _shove(row: dict) -> dict:
    gv = row.get("gex_views") or {}
    return gv.get("shove") or {}


# LANE A REMOVED (obs-3, 2026-09-01). `aggregate()` and its `_layers()`
# evidence table — the deterministic arrow with its magnet voter, breadth
# veto, 15-minute dwell, path caution and ghost — lived here for two months.
# Deleted deliberately, not lost:
#   it was a DIRECTION surface on a system whose redesign says the direction
#     call measured worthless (391 readings, zero forward value);
#   its own content was audited at pointing to the nearest round hundred on
#     94% of 51 episodes — a round-number detector wearing a signal's clothes;
#   its state changes woke the model (11% of calls) while the scene deliberately
#     hid the arrow from the model, so those calls could never be narrated;
#   and two philosophies on one screen teach a reader to trust neither.
# The scene never carried it (sr-2), so the model's contract is untouched. Old
# read rows keep their `arrow` field as history; nothing writes a new one.
def frozen_fields(rows: list[dict], now: datetime) -> list[dict]:
    """Fields that have not moved for FROZEN_MIN minutes.

    Rendered greyed on the chart with "unchanged 2h", and barred from being
    cited as the reason for a NEW call. Applied to the recorded data most of the
    payload greys out, which is the honest picture."""
    probes = {
        "magnet": lambda r: (r.get("gex_views") or {}).get("magnet"),
        "call wall": lambda r: r.get("call_wall"),
        "put wall": lambda r: r.get("put_wall"),
        "regime": lambda r: r.get("regime"),
        "gamma sign": lambda r: r.get("gamma_sign"),
    }
    out = []
    for name, fn in probes.items():
        cur = fn(rows[-1]) if rows else None
        held_since = None
        for r in reversed(rows):
            if fn(r) != cur:
                break
            held_since = _ts(r) or held_since
        if held_since is None:
            continue
        mins = int((now - held_since).total_seconds() // 60)
        if mins >= FROZEN_MIN:
            out.append({"field": name, "value": cur, "for_min": mins})
    return out


# ---------------------------------------------------------------------------
# wake gate
# ---------------------------------------------------------------------------
def ladder_nearest(row: dict) -> dict:
    """The nearest wall on each side under the SAME rule the scene ships —
    gw_vocab.cluster_walls on the per-strike surface, sided and measured from
    the book's own spot exactly as walls_ladder does — so a wall frozen in the
    gate is always a wall the model was shown.

    obs-4. Returns {"surface": bool, "call_wall": K|None, "put_wall": K|None}.
    `surface` says whether the row carried a per-strike surface at all. When it
    did, a side with no rung is None and STAYS None — the diary's pre-cluster
    scalar is never substituted, because that substitution is how the frame
    once said "put wall 1500, unchanged" beside a walls block saying the put
    side was empty (09-01 12:33). When the row carries no surface (a thin row,
    the tests' minimal rows), the scanner's own scalars are the only
    measurement there is and they are used as such — consistently, on both
    sides of every comparison, never mixed with the ladder."""
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    nbs = gv.get("net_by_strike")
    spot = _fin(row.get("spot"))
    if not nbs:
        return {"surface": False, "call_wall": _fin(row.get("call_wall")),
                "put_wall": _fin(row.get("put_wall"))}
    out = {"surface": True, "call_wall": None, "put_wall": None}
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    bs = _fin(meta.get("chain_spot"))
    if bs is not None and spot is not None and (
            bs <= 0 or abs(bs - spot) / spot > RULER_MAX_DRIFT):
        bs = None
    ruler = bs if bs is not None else spot
    if ruler is None:
        return out
    try:
        clusters = _gw.cluster_walls(nbs, ruler)
    except (TypeError, ValueError):
        return out
    calls = [c["peak"] for c in clusters if c["side"] == "call" and c["peak"] > ruler]
    puts = [c["peak"] for c in clusters if c["side"] == "put" and c["peak"] < ruler]
    if calls:
        out["call_wall"] = float(min(calls))
    if puts:
        out["put_wall"] = float(max(puts))
    return out


def state_for_next_wake(row: dict, scene: Optional[dict] = None) -> dict:
    """The structural state the wake gate compares against, snapshotted.

    THIS EXISTS BECAUSE `prev` IS NOT A DIARY ROW. `read_once` passes the last
    row that SPENT A CALL, and those live in state/sndk_reads/, whose schema is
    ts / wake / spot / sigma / magnet_band / reading / ... and carries
    none of `call_wall`, `put_wall`, `gamma_flip`, `atm_iv`, `gamma_sign` or
    `gex_views`. A gate reading those off `prev` gets None every time and
    silently degenerates to price-and-heartbeat — the exact starvation
    should_wake's own docstring records happening once before.

    So the read row now carries this snapshot, and the gate reads it through
    `value_at_last_read`, which falls back to the row itself so a diary row
    (tests, replay) still works unchanged."""
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    # "unknown" is a sensor state, not a market state. Stored as a real value it
    # made the wake gate fire "gamma sign flipped" on unknown<->known flaps —
    # measured 142 such transitions across 8 sessions against 2 genuine sign
    # flips (~18 fake wakes a day waiting to happen). session_context already
    # guards this with _known(); the snapshot now applies the same rule.
    lw = ladder_nearest(row)
    out = {"spot": _fin(row.get("spot")), "sigma": _fin(row.get("sigma")),
           "gamma_sign": _known_sign(row.get("gamma_sign")),
           "magnet": _fin(gv.get("magnet")),
           "gamma_flip": _fin(row.get("gamma_flip")),
           "call_wall": lw["call_wall"],
           "put_wall": lw["put_wall"],
           "atm_iv": _fin(row.get("atm_iv"))}
    if scene is not None and lw["surface"]:
        # obs-4: and ONLY the ladder's. The old branch kept the diary scalar
        # whenever the ladder had no rung on a side, which is how a read was
        # handed "put wall 1500, unchanged" beside a walls block saying the put
        # side was empty (09-01 12:33). A side the ladder found empty is empty,
        # and a walls block the freshness gate deleted freezes nothing at all —
        # the frame may only name what the model was shown.
        # THE FRAME'S WALLS ARE THE LADDER'S, when a scene exists to read them
        # from. The diary scalars are the pre-cluster walls sr-3 evicted from
        # the scene because they disagreed with the ladder (08-06: diary said
        # put wall 1200 while walls.put[0] was 1250) — so a frame built on them
        # could tell the model price crossed a wall it was never shown. The
        # ladder's nearest rung is what "nearest_call_wall" actually means.
        w = scene.get("walls") or {}
        for side, key in (("call", "call_wall"), ("put", "put_wall")):
            rungs = w.get(side) or []
            out[key] = _fin((rungs[0] or {}).get("strike")) if rungs else None
    return {k: v for k, v in out.items() if v is not None}


def _known_sign(v):
    """A gamma sign that was actually measured, or None."""
    return v if v not in (None, "", "unknown", "unmeasured") else None


def value_at_last_read(prev: dict, key: str):
    """Read a structural field off the last-spoken row, snapshot first.

    `magnet` is nested under gex_views on a diary row and flat in the snapshot,
    which is the one asymmetry worth spelling out rather than hiding."""
    g = prev.get("gate")
    if isinstance(g, dict) and key in g:
        return g[key]
    if key == "magnet":
        gv = prev.get("gex_views")
        return _fin(gv.get("magnet")) if isinstance(gv, dict) else None
    return _fin(prev.get(key)) if key != "gamma_sign" else prev.get(key)



# obs-3: what each wake reason means, said in words the lexicon allows. The RAW
# reason is never handed to the model — "pin moved" baits it into echoing "pin",
# which is a banned word, and in testing that deleted the entire read. Every
# value here is asserted lexicon-clean by a test.
_WAKE_WORDS = {
    "first read": "the session's first look",
    "price ran": "price travelled since the last read",
    "pin moved": "the heaviest strike is a different strike than at the last read",
    "gamma sign flipped": "the book's computed gamma sign changed and held for two fresh books",
    "crossed flip": "price moved to the other side of the hedging flip as it stood at the last read",
    "call wall crossed": "price crossed a level from the last read",
    "put wall crossed": "price crossed a level from the last read",
    "iv moved": "implied vol moved since the last read",
    "flip moved": "the hedging-flip level relocated",
    "heartbeat": "timed check-in; nothing crossed a wake threshold",
}


def frame_since_last_read(row: dict, rows: list[dict],
                          last_call: Optional[dict], wake: Optional[str],
                          force: bool, now: datetime,
                          prior_rows_today: bool = False) -> dict:
    """The bridge from the model's last reading to this one — obs-3's one new
    block, and the reason the honest nothing-changed sentence is possible at
    all. Proven the hard way: "price has held between 1538.8 and 1546.1 since
    the 13:05 read" was DELETED by the number gate without this block (the
    since-window range is not a number on today's board) and the row was then
    falsely logged as a forced abstain.

    Anchored on the last LOOK (the gate's own frame), not the last surviving
    sentence — the wake reason and this block must agree about what "since"
    means. Every number a model might quote ships as a value, pre-computed,
    because the doctrine forbids the model doing arithmetic and the gate
    deletes what is not on the board: the delta in dollars, the clock as an
    HH:MM string, the since-window range.

    Crossings are tested against the FROZEN levels in the last call's `gate`
    snapshot — the same straddle arithmetic the wake gate uses — because a
    crossing is only observable against a frozen frame; the live label steps
    aside as price arrives. The gamma flip is deliberately excluded from the
    crossed list: it is barred as a nameable level for measured reasons
    (its uncertainty exceeds its distance from spot), and freezing it does not
    cure that. A flip crossing is narrated in words via the wake reason."""
    t_last = _ts(last_call) if last_call else None
    if t_last is None:
        # an era change mid-session empties the era-scoped store, and calling
        # 13:40 "the first read of the session" is false — the honest label is
        # that nothing has been read UNDER THE CURRENT RULES.
        return ({"first_read_under_current_rules": True} if prior_rows_today
                else {"first_read_of_session": True})
    out: dict = {"last_read_at": t_last.astimezone(_ET).strftime("%H:%M"),
                 "minutes_since": max(0, int((now - t_last).total_seconds() // 60))}
    spot_then = value_at_last_read(last_call, "spot")
    spot_now = _fin(row.get("spot"))
    if spot_then is not None and spot_now is not None:
        out["spot_then"] = round(spot_then, 2)
        out["spot_now"] = round(spot_now, 2)
        out["spot_change_dollars"] = round(spot_now - spot_then, 2)
    if wake:
        out["why_this_read"] = _WAKE_WORDS.get(wake, "routine check")
    elif force:
        out["why_this_read"] = "manual check"

    crossed = []
    seen = set()
    tested = 0
    if spot_then is not None and spot_now is not None:
        # MAGNET FIRST: when one price wore two labels, the surviving label is
        # the one the doctrine itself says outlives a crossing 97% of the time —
        # the old order suppressed heaviest_strike in favour of the wall name.
        for key, label in (("magnet", "heaviest_strike"),
                           ("call_wall", "nearest_call_wall"),
                           ("put_wall", "nearest_put_wall")):
            lvl = value_at_last_read(last_call, key)
            if lvl is None or round(lvl, 2) in seen:
                continue
            tested += 1
            # strict straddle, PLUS the parked-exactly-on case: a last read
            # sitting on the level to the cent, then travelling away through
            # it, is a departure worth naming — the strict product read 0 and
            # called it nothing.
            if ((spot_now - lvl) * (spot_then - lvl) < 0
                    or (spot_then == lvl and spot_now != lvl)):
                crossed.append({"level": round(lvl, 2),
                                "was_labelled_then": label,
                                "price_went": "up" if spot_now > lvl else "down"})
                seen.add(round(lvl, 2))
    if crossed:
        out["crossed_since_then"] = crossed
    elif tested and wake != "crossed flip":
        # measured-empty, said outright — but ONLY when levels were actually
        # tested (an empty gate snapshot tested nothing and may claim nothing),
        # and never beside a flip-crossing wake, where "nothing crossed" and
        # "price crossed the flip" in one block was a self-contradiction the
        # model had to guess its way around. The flip stays out of the crossed
        # LIST for the measured reasons; the wake words carry that crossing.
        out["nothing_crossed_since_then"] = True

    # obs-4: LIKE WITH LIKE. The "then" walls are the gate's, which are the
    # ladder's; the "now" walls used to be the diary's pre-cluster scalars, and
    # on 09-02 10:01 the two disagreed inside one block — ladder 1550, diary
    # 1600, "call_wall unchanged 1600" beside a walls block showing 1550. Both
    # sides now come from ladder_nearest, the same rule the gate freezes.
    lw_now = ladder_nearest(row)
    unchanged = {}
    absent = []
    for key, cur in (("gamma_sign", _known_sign(row.get("gamma_sign"))),
                     ("heaviest_strike", _fin((row.get("gex_views") or {}).get("magnet"))),
                     ("call_wall", lw_now["call_wall"]),
                     ("put_wall", lw_now["put_wall"])):
        then = value_at_last_read(
            last_call, "magnet" if key == "heaviest_strike" else key)
        if then is not None and cur is not None and then == cur:
            unchanged[key] = then if not isinstance(then, float) else round(then, 2)
        elif (key in ("call_wall", "put_wall") and then is None and cur is None
                and lw_now["surface"]):
            # measured empty at both readings is a fact worth one word — it is
            # not a hole, and it is never a number
            absent.append(key.split("_")[0])
    if unchanged:
        out["unchanged_since_then"] = unchanged
    if absent:
        out["walls_absent_then_and_now"] = absent

    # ONE range, and its anchor is IN ITS NAME. The first night of live-fire QA
    # shipped `range_since` beside `session_range_unbroken_since` — two
    # different ranges, two different clocks — and the model fused them into
    # "held between X and Y since HH:MM" with the wrong clock: confidently
    # false on 8 of 11 quiet reads, breaches up to $28, and every number
    # individually on the board so no gate could see the lie. The session-
    # extremes anchor is gone; the only range here is the one since the last
    # read, and the only clock it pairs with is `last_read_at`, three lines up.
    # obs-4: the range is SEEDED WITH spot_then. Rows strictly after the last
    # read gave a window that began one scan late, so the price the model last
    # spoke at sat outside its own "held between" — on 09-02 10:01 a fall from
    # 1557 to 1518 shipped as held_between 1518-1546, and the model said
    # "held". The window now runs from where price WAS to wherever it went.
    lo = hi = spot_then
    for r in rows:
        sp = _fin(r.get("spot"))
        t = _ts(r)
        if sp is None or t is None or t <= t_last:
            continue
        lo = sp if lo is None else min(lo, sp)
        hi = sp if hi is None else max(hi, sp)
    if lo is not None and hi is not None:
        out["held_between_since_last_read"] = {"low": round(lo, 2),
                                               "high": round(hi, 2)}
    # obs-4: is this frame A MOVE or A HOLD, said outright with its rule, so
    # the model does not reach for the "held between" template across a drop
    # the block itself measured. The threshold is FRAME_MOVE_SIGMA.
    sig = _fin(row.get("sigma"))
    if spot_then is not None and spot_now is not None and sig:
        chg = round((spot_now - spot_then) / sig, 2)
        out["spot_change_sigma"] = chg
        out["frame_is"] = "a move" if abs(chg) >= FRAME_MOVE_SIGMA else "a hold"
    return out


def _books_since(rows: list[dict], since: Optional[datetime]) -> list[dict]:
    """One row per DISTINCT book, oldest first, for books measured after `since`.

    The scanner fires every 120s and the feed re-serves a disk cache on half of
    those, so a row is not an observation — 179 rows a day carry 90 distinct
    books, median 4.1 minutes apart. Every wk-1 test counts books, because
    counting rows counts the cache twice and calls it confirmation."""
    out, seen = [], None
    for r in rows:
        b = _book_asof(r)
        if b is None or (since is not None and b <= since):
            continue
        if seen is None or b != seen:
            out.append(r)
            seen = b
    return out


def _held_for(books: list[dict], get, was, n: int) -> bool:
    """True when the last `n` distinct books ALL read differently from `was`.

    The anti-flicker rule. On the recorded tape a changed gamma sign reverts one
    book later 37% of the time, a call wall 31%, a put wall 24%, the top strike
    18% — so a single differing book is not evidence that anything moved."""
    if len(books) < n:
        return False
    tail = books[-n:]
    return all(get(b) is not None and get(b) != was for b in tail)


def should_wake(row: dict, prev_row: Optional[dict], prev: Optional[dict],
                now: datetime, rows: Optional[list[dict]] = None) -> Optional[str]:
    """Why this scan is worth a model READ, or None to stay asleep.

    `prev` must be the last row that actually spent a call, NOT simply the last
    row written. Those diverged the moment the arrow started being recomputed
    every scan: comparing against the last write made the min-gap test compare
    the book to itself two minutes ago, and the gate silently starved to ~1 call
    a day. Timing, drift and heartbeat are all measured from the last LOOK.

    wk-1 (2026-08-31) rewrote the body. Three things changed and each was
    measured rather than argued:

      THE BOOK CLOCK, NOT THE SCAN CLOCK. Half of all rows are the same book
      served twice. Every comparison here now runs over `_books_since`.

      CONFIRMATION ON DISCRETE FLIPS. A sign or pin that differs for one book is
      flicker a third of the time; it must hold for WAKE_CONFIRM_BOOKS.

      A WALL MOVING IS NOT AN EVENT; PRICE CROSSING ONE IS. 45% of wall
      relocations happen in a step where spot moved under 0.05 sigma, and ~30%
      undo themselves one book later — the wall is chasing price, so waking on
      the relabel is waking on our own arithmetic. The old gate woke on any wall
      change; this one wakes only on a crossing of the wall it last SPOKE about.

    `rows` is optional so existing callers and tests keep working; without it
    the confirmation tests are skipped rather than guessed at."""
    if prev is None:
        return "first read"
    last = _ts(prev)
    if last is None:
        return "first read"
    gap = (now - last).total_seconds() / 60.0
    if gap < MIN_GAP_MIN:
        return None

    sig = _fin(row.get("sigma")) or 0
    spot = _fin(row.get("spot"))
    spoke_spot = value_at_last_read(prev, "spot")
    books = _books_since(rows or [], _book_asof(prev))

    # STRUCTURE IS EVALUATED ON THE BOOK CLOCK, PRICE ON THE LIVE ONE, and the
    # split is the scene's own provenance model applied to the gate. The
    # scanner fires every 120s and the feed re-serves a cached book on half of
    # those, so a structural test run per SCAN asks the same book the same
    # question two or three times and fires again each time MIN_GAP allows.
    # Measured: without this guard the gate spent 20.3 calls a day and hit the
    # daily cap on 11 of 23 replayed sessions, with a median gap of 8.3 minutes
    # — pinned to the spam floor, which is a clock wearing event clothing, the
    # exact thing wk-1 set out to remove. With it: 12.5 a day, cap never
    # reached. Price crossings and travel are LIVE TAPE and stay on every scan,
    # because a level being crossed is news the moment it happens.
    new_book = _book_asof(row) != _book_asof(prev)

    # 1-2. discrete structure, and only when it HOLDS
    if books and new_book:
        spoke_sign = _known_sign(value_at_last_read(prev, "gamma_sign"))
        if spoke_sign is not None and _held_for(
                books, lambda b: _known_sign(b.get("gamma_sign")),
                spoke_sign, WAKE_CONFIRM_BOOKS):
            return "gamma sign flipped"
        if value_at_last_read(prev, "magnet") is not None and _held_for(
                books, lambda b: _fin((b.get("gex_views") or {}).get("magnet")),
                value_at_last_read(prev, "magnet"), WAKE_CONFIRM_BOOKS):
            return "pin moved"

    # 3-4. price CROSSING a level the last reading actually spoke about. The
    # level is the one that was true when it spoke, never the relabelled one.
    if spot is not None and spoke_spot is not None:
        f = value_at_last_read(prev, "gamma_flip")
        if (f is not None and sig and (spot - f) * (spoke_spot - f) < 0
                and abs(spot - f) / sig > WAKE_FLIP_CROSS_SIGMA):
            return "crossed flip"
        for k, word in (("call_wall", "call wall"), ("put_wall", "put wall")):
            w = value_at_last_read(prev, k)
            if w is not None and (spot - w) * (spoke_spot - w) < 0:
                return f"{word} crossed"
        # 5. plain travel
        if sig and abs(spot - spoke_spot) / sig >= WAKE_SPOT_SIGMA:
            return "price ran"

    # 6. implied vol, on the SMOOTHED series — the raw one is mostly sensor
    if books and new_book:
        ivs = [v for v in (_fin(b.get("atm_iv")) for b in books) if v is not None]
        spoke_iv = value_at_last_read(prev, "atm_iv")
        if ivs and spoke_iv is not None:
            med = statistics.median(ivs[-WAKE_IV_MEDIAN_BOOKS:])
            if abs(med - spoke_iv) * 100.0 >= WAKE_IV_PP:
                return "iv moved"

    # 7. the flip LEVEL relocating, as opposed to price crossing it
    fl = _fin(row.get("gamma_flip"))
    spoke_fl = value_at_last_read(prev, "gamma_flip")
    if new_book and sig and fl is not None and spoke_fl is not None:
        if abs(fl - spoke_fl) / sig >= WAKE_FLIP_SIGMA:
            return "flip moved"

    if gap >= HEARTBEAT_MIN:
        return "heartbeat"
    return None


# --- obs-1: the observation contract ----------------------------------------
#
# WHAT THE MODEL IS ASKED FOR CHANGED, and the gates below are the reason it can
# be trusted. The old contract asked for a direction and a magnitude; the 08-27b
# audit measured that forecast at zero forward value, and the model declined to
# make it at all on 154 of 390 calls. The new contract asks what is UNUSUAL in
# the snapshot, in a human voice, with every number traceable to the field it
# came from.
#
# THE LEVEL RULE, and it is the one that came out of measurement rather than
# taste. A statement may name the MAGNET / heaviest-cluster family and no other
# level. Measured over 1,395 arrivals on the recorded tape: "X is the magnet"
# is still true when price actually reaches X on 95.3% of occasions, and the
# magnet relabels on a crossing with probability 0.000. "X is the call wall"
# survives 73.1%, and on a crossing it relabels with probability 1.000 — median
# 0.0 minutes — because `_weight_wall` is DEFINED as the heaviest strike on the
# requested side of spot (lefteye_gex_box.py:318). call_wall >= spot on
# 4,149 of 4,149 recorded rows. "Price broke the call wall" is not a hard claim
# to check, it is an unobservable one: the wall steps aside as price arrives.
# So the walls may be described as structure and may never be named as a level
# something happens AT.
_OBS_MAX = 3

# Forecast vocabulary. Nothing about where price goes, how far, or how likely.
_BANNED_FORECAST = (
    "rally", "sell off", "selloff", "bounce", "breakout", "break out",
    "break down", "breakdown", "target", "support", "resistance", "squeeze",
    "bullish", "bearish", "expect", "likely", "unlikely", "should", "will",
    "suggests", "implies", "favours", "favors", "sets up", "poised", "primed",
    "bias", "lean", "risk of", "pull", "pulls", "pulling", "attract",
    "attracts", "pin", "pins", "pinning", "push", "pushes",
    "rebound", "reversal",
    # inflections measured escaping on live replies (obs-3 QA): the stem was
    # banned and its past tense or -s form walked straight through the gate.
    "pinned", "pushed", "pulled", "rallied", "rallies", "rallying",
    "leans", "leaned", "leaning",
)
# "higher", "lower", "upside" and "downside" were on the list and came off it.
# On a board they are POSITIONAL — "no call wall to the upside" means nothing
# above price, and "the heavier wall lower down" names a strike. Measured on a
# live read, "read_banned:resistance,upside" deleted an otherwise good sentence
# over the second word. "support" and "resistance" stay banned because they
# assert that price will react at a level, which is the claim itself; direction
# words that merely say WHERE do not.
# NOUNS THE SCENE ITSELF USES ARE NOT BANNED, and the reason is a measured one.
# The list above once held "magnet", "call wall" and "put wall". Every one of
# them is the name of a block the model is handed and asked to read, so banning
# the noun deleted the human sentence whenever the model referred to the thing
# by the only name it has: 2 of 2 completed obs-1 reads lost `say` to "call
# wall", and the first obs-1b read lost `quiet_because` to "magnet" — while
# saying something entirely reasonable about it being ordinary.
#
# The danger was always the VERB. A wall relabels to follow price with
# probability 1.000 on a crossing and the magnet's pull is unestablished, so
# "price will reach the magnet" is the false claim — and it dies on "will",
# "reach" being harmless on its own. Naming a level is description; asserting
# what price does at it is forecasting, and the verb lists above catch that.
# Causal connectives. Banned for CORRECTNESS, not caution: the walls relabel to
# follow price with probability 1.000 on a crossing, so a causal verb about them
# is false by measurement, and Zhu et al. (14,922 explanations) found evidence-
# bounded prompting cuts unsupported causal attribution by only ~40% and never
# eliminates it — the risk peaking exactly where evidence is rich but
# insufficient, which describes an option book.
_BANNED_CAUSAL = (
    "because", "due to", "driving", "drove", "as a result", "in response to",
    "which is why", "causing", "caused", "defending", "thanks to", "owing to",
)
# obs-1b REMOVES the wall-name ban. It was right about the danger and wrong
# about the mechanism: the scene ships a `walls` block the model is meant to
# read, so banning the NOUN wiped the human sentence on 2 of the 2 completed
# obs-1 reads — a 100% deletion rate on the only part a person reads.
#
# The danger is real (a wall relabels on a crossing with probability 1.000, so
# it cannot be named as a place price reaches), but what carries it is the VERB,
# and the forecast and causal lists above already catch every verb that could.
# Describing a wall is fine. "Price will reach the call wall" dies on "will".
_BANNED_LEVELS = ()
# obs-4: JUDGEMENT. Words that grade the board or price rather than describe
# it — a wall is heavy or light (a share), never strong or weak (a promise);
# a level is a strike, never a ceiling, a floor or a cap; and nothing on a
# board is due, at risk, or setting up. Same fate as a forecast word.
_BANNED_JUDGEMENT = (
    "anticipate", "forecast", "projected", "projection", "odds", "probable",
    "probably", "ceiling", "floor", "cap", "lid", "strong", "strongly",
    "weak", "weakly", "overbought", "oversold", "overextended", "stretched",
    "exhausted", "exhaustion", "fragile", "vulnerable", "at risk", "danger",
    "dangerous", "threat", "threatens", "healthy", "unhealthy", "overdue",
    "due for", "look for", "watch for", "call for", "calls for", "setting up",
    "set up for", "favour", "favor", "favouring", "favoring",
)


def _stem_pattern(w: str) -> str:
    """obs-4: a banned word bans its INFLECTIONS too. The list caught "favors"
    and let "favoring" walk through (09-02 09:30); every earlier escape on the
    live tape was the same shape — a stem on the list, its -ing or -ed form
    not. Phrases stay exact; a word ending in y takes ies/ied/ying; a word
    ending in e drops it before ing; every word takes s/es/ed/ing and the
    doubled-consonant forms (pinned, pinning)."""
    if " " in w:
        return re.escape(w)
    if w.endswith("y") and len(w) > 3:
        return re.escape(w[:-1]) + r"(?:y|ies|ied|ying)"
    if w.endswith("e"):
        return re.escape(w[:-1]) + r"e?(?:s|d|ing)?"
    last = re.escape(w[-1])
    return re.escape(w) + rf"(?:s|es|ed|ing|{last}ed|{last}ing)?"


_BANNED_RE = re.compile(
    r"\b(?:" + "|".join(_stem_pattern(w) for w in
                        _BANNED_FORECAST + _BANNED_CAUSAL + _BANNED_LEVELS
                        + _BANNED_JUDGEMENT)
    + r")\b", re.I)

# obs-4: A SENTENCE THAT PLACES PRICE ABOVE OR UNDER A NUMBER IS CHECKED
# AGAINST THE LIVE SPOT. "still under the 1500 strike" shipped at 1528.70 on
# 09-02 10:11 with every number on the board and no banned word in it. The
# subject list is deliberately narrow — price, spot, the verbs that describe
# where price sits — and a match whose preceding words name a wall, a strike,
# a level or an absence is skipped, because "no wall sits above 1600" is about
# walls. False negatives are accepted; a deleted true sentence is not.
_POS_RE = re.compile(
    r"\b(?:price|spot|now|still|sits|sitting|trading|trades|holding|holds|"
    r"hovering|hovers|stays|staying|remains|remaining|parked)\b"
    r"(?:[^.;,]{0,24}?)\b(above|over|under|below|beneath)\b\s+"
    r"(?:the\s+|that\s+|its\s+)?\$?(\d{3,5}(?:,\d{3})?(?:\.\d+)?)", re.I)


def position_slips(text: str, spot) -> list[str]:
    """Every above/under claim about price that the live spot contradicts."""
    spot = _fin(spot)
    if spot is None or not text:
        return []
    out = []
    for m in _POS_RE.finditer(text):
        pre = text[max(0, m.start() - 40):m.start()].lower()
        if any(w in pre for w in ("wall", "strike", "level", "no ", "nothing",
                                  "none", "cluster")):
            continue
        n = float(m.group(2).replace(",", ""))
        if n < 100:
            continue
        rel = m.group(1).lower()
        ok = (spot > n + 0.5) if rel in ("above", "over") else (spot < n - 0.5)
        if not ok:
            out.append(f"{rel} {m.group(2)}")
    return out


# obs-4: THE SEMANTIC REVIEWER. A second, small model reads what survived the
# word list and the number gate and answers one question per text: is this a
# neutral observation, or does it predict, expect, or judge? The list can only
# ban the words it knows; this catches the sentence that forecasts in
# ordinary words. It is a reviewer, not an author — it never rewrites, and a
# hit deletes the text the way a banned word does.
_SEMANTIC_SYSTEM = """You are a strict reviewer of one-line market observations about a stock's option board. Each numbered text must be a NEUTRAL OBSERVATION: where price is or was, what changed and when, which strike holds how much of the board, what sits above or below price, whether a box or range held or broke in the past. It must NOT predict, expect, or judge: nothing about where price is going, what it will or should or could do next, how likely anything is, whether a level will hold or break or attract or push, and no words of approval, alarm, or advice. Describing position, size, and a past change is not a forecast; saying or implying what happens next is.

Reply with ONLY a JSON object, no prose: {"verdicts": [{"i": <index>, "forecast": true|false, "phrase": "<the offending words, or an empty string>"}]} — exactly one entry per numbered text, in order."""


def semantic_review(texts: list[str], model: str = None,
                    timeout: float = None) -> Optional[list]:
    """One small-model call over every surviving text. Returns a list aligned
    with `texts` — None where the text is clean, the offending phrase where it
    is not — or None as a whole when the judge could not answer (timeout,
    spawn failure, unparseable reply), which the caller records as skipped."""
    if not texts:
        return []
    model = model or SEMANTIC_GUARD_MODEL
    timeout = timeout or SEMANTIC_GUARD_TIMEOUT_S
    prompt = ("TEXTS:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
              + "\n\nReply with the JSON object only.")
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
           "--append-system-prompt", _SEMANTIC_SYSTEM,
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--effort", SEMANTIC_GUARD_EFFORT,
           "--disallowedTools", *_NO_TOOLS]   # variadic — must stay LAST
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    env = first_json_object(r.stdout)
    text = env.get("result") if isinstance(env, dict) and "result" in env else r.stdout
    obj = first_json_object(text if isinstance(text, str) else r.stdout)
    if not isinstance(obj, dict) or not isinstance(obj.get("verdicts"), list):
        return None
    out: list = [None] * len(texts)
    for v in obj["verdicts"]:
        if not isinstance(v, dict):
            continue
        i = v.get("i")
        if isinstance(i, int) and 0 <= i < len(texts) and v.get("forecast") is True:
            out[i] = (str(v.get("phrase") or "")[:80] or "flagged")
    return out

# ---------------------------------------------------------------------------
# the model call — sr-2: the model infers its OWN vector from an unbiased scene
# ---------------------------------------------------------------------------
_DOCTRINE = f"""You are watching one stock's option book and saying what you NOTICE. You are not forecasting. Nobody wants to know where you think price is going — that question was asked of you for a year, measured, and found to carry no information at all.

TALK LIKE A PERSON. Imagine someone sitting next to you who knows markets but not options jargon, and you are pointing at the screen. "The heaviest strike on the board is 1500, and it has been all session" is what a human says. "top_strike_lead_pp = 1.81" is not. Full sentences, plain words, no shorthand, no bullet fragments, no field names in the prose — the numbers are checked against the scene before anyone reads your answer.

YOU READ THE WHOLE BOARD AND YOU DECIDE WHAT MATTERS. Nothing has been pre-selected for you. Go through the scene, weigh the parts against each other, and point at the few things a person watching this stock would actually want to know about right now — usually one or two, often none at all.

ONE BLOCK IS NOT A MEASUREMENT OF TODAY: `context`. You are handed a single snapshot, so you cannot know how today compares with other days, or what moved since the last option book. `context` supplies exactly that and nothing else — where today's numbers rank against this stock's own closed sessions, what changed since the last book, what changed since your OWN last reading (`since_last_read`), and the day's price boxes (`ranges`). It states facts, never conclusions. Whether any of it is worth saying is your call.

OPEN WITH THE FRAME. `context.since_last_read` bridges your last reading to this one: when you last spoke, price then and now, any level crossed since — stated as the label it wore then — and what held still. Your first sentence answers what changed since you last said something. `frame_is` says which kind of frame this is, by rule: "a move" when `spot_change_sigma` is 0.15 or more in either direction, "a hold" otherwise. On a move, OPEN WITH PRICE THEN AND PRICE NOW — both are in the block — and only then say what is on the board; the "held between" sentence belongs to a hold and never to a move, because a range whose two ends are a move apart is not a range anyone held. `walls_absent_then_and_now` names a side the ladder found empty at both readings: say "no put wall then, none now", never a number. Crossings are usually SHALLOW at the moment you speak (median about two dollars on this name), so say "just through 1500", never "decisively through". After a crossing, name the next structure on the board in the direction price moved — and PREFER THE HEAVIEST-STRIKE FAMILY for it: measured reading-to-reading, the heaviest strike is still itself 97% of the time while a ladder wall relabels one time in five. Never name an exact flip level in a frame-to-frame claim, and never name a rung within one grid step of the strike just crossed — that is usually the crossed level wearing a new label. When the board holds NO wall on that side, say so: "open air above" is often the loudest fact available. When the block shows nothing crossed and nothing relabelled, say it affirmatively with the numbers it hands you — "price has held between X and Y since your last read" is a complete, correct read when `frame_is` says a hold — X and Y come from `held_between_since_last_read`, and the ONLY clock that range pairs with is `last_read_at`. Never anchor a range on any other time. ALWAYS WRITE `read`, even then: the quiet line is what makes one uneventful stretch distinguishable from another later on. On the session's first read (or the first under the current rules) there is no frame yet; describe the standing board instead.

THE DAY'S BOXES. `context.ranges` tells the price-range story as boxes, every number measured from today's scans and the prior sessions' scans, with no verdict in any of it. `opening` is the first half hour's box — low, high, and whether it has held or broke, with the clock and the direction when it did. `in_force` is the box that stands NOW: the opening box while it holds; after a break, the box that formed since the break (a box forms over half an hour and then freezes; a scan beyond a frozen box by more than a twentieth of a sigma breaks it). A broken box is over — never describe price as inside a box that `breaks_today` says it left; the new box replaced it, and the opening box stays in the block only so the day's start can be named. `prior_sessions` is the range of the last few closed sessions with where the live price sits in it and whether today has traded beyond it. Every box and extreme names its witness: `measured_from` and `price.extremes_from` say "1_minute_bars" when the minute-bar record was used — its wicks saw what a 2-minute scan steps over — and "scans_every_2_min" when it was not, in which case the true extremes may sit a few dollars beyond what is stated. That range can hold while today's box breaks, and the two facts are stated separately for exactly that reason. Say what a box DID — "broke above the opening box at 10:07", "still inside the prior sessions' range" — and nothing about what follows.

KEEP IT SHORT AND PLAIN. TWO SENTENCES, forty words at the outside — the way you would say it to someone sitting beside you, not the way you would write it down. No jargon, no field names, no padding, no listing everything you looked at. Say the one or two things that matter and stop. The levels go in `points` with a few words each; do not repeat them in the prose. Every number you say has to be one that APPEARS IN THE SCENE, exactly as it appears. That includes numbers you work out yourself: do not convert a distance into sigma, do not turn a share into a percentage of something else, do not average two figures. A number you computed is not on the board, and the sentence carrying it is deleted rather than corrected. If you want to say a level is far away, say which level and let the reader see the two prices.

THE INSTRUMENT. This is SNDK, a single stock, not an index. Its sigma (a typical day's move) runs 8-10% of the share price — enormous. It has weekly expiries, not daily, so most days have no expiry at all. Standing levels come from open interest, which is yesterday's positioning about a stock that can move 12% in a session.

FIELD NAMES SAY WHAT THEY ARE. Every leaf in this scene is named so it can be read alone: a `_pp` suffix is percentage points, `_bn` is billions of dollars, `_musd` is millions, `_min` is minutes, `_sigma` is a distance in sigma. Read the name before you read the number.

WHERE EVERY NUMBER CAME FROM (read this block first — it decides how much any other block is worth):
- WHICH BLOCK RESTS ON WHAT. `price` and `history` are the LIVE TAPE, good to the second. `clock` is the wall clock. `scale`, `regime`, `magnet`, `breadth`, `momentum`, `dealer_positioning`, `walls` and `clock.front_expiry` come out of the OPTIONS BOOK, which is minutes old and often a cached repeat. Of those, `regime`, `magnet`, `breadth`, `dealer_positioning` and `walls` rest further on the OPEN INTEREST SNAPSHOT struck at last night's close. A block you cannot find was either not measured or deleted for age; see freshness_rules.
- data_sources may also carry `minute_bars` — the minute-bar record's own clock: how many completed minutes it holds today and how old the newest is. It is the witness behind the session extremes and the boxes when present.
- data_sources holds TWO clocks and they disagree on purpose. `scan_taken_at` is when this row was written, which is also when the spot was quoted — there is no separate quote clock, and `spot_feed` names where that price came from. `options_book.measured_at` is when the CHAIN was pulled, and `options_book.age_min` is its real age: the feed re-serves a cached book on about half of all scans (`is_repeat_of_previous_scan` says whether this one is a repeat), so the book is routinely 2-4 minutes old on a scan that is seconds old. Never quote the scan's freshness for a structural number.
- `open_interest` is the one that matters most and the one most easily misread. Every standing structure in this scene — magnet, walls, breadth, dealer_positioning, charm, flip — is computed from open interest struck at the PRIOR SESSION'S CLOSE (`prior_session_date` names the day) and it does not change during the session: `measured_unchanged_so_far_today` re-proves it against `strikes_compared_today` strikes whenever today's scans give it enough overlapping strikes to answer — and says nothing at all rather than guessing when they do not. What moves intraday is price moving under a fixed map, not the map moving.
- Because of that, THESE BLOCKS MAY NEVER BE NARRATED IN THE PRESENT TENSE: {", ".join(PRESENT_TENSE_FORBIDDEN_FOR)}. "Dealers are buying", "the wall is building", "gamma is piling up" are false statements about time, not debatable reads. Say "as of last night's close" or say nothing. Only `momentum` and `*_change_30min` fields describe something that moved today.
- freshness_rules is enforced, not advisory: a block whose source aged past its ceiling is DELETED before you see it, and `blocks_dropped_this_scan` names what went. So an absent block may mean "measured but too old" — check there before calling anything unknown.

THE TWO RULERS, AND WHICH ONE A DISTANCE USES:
- `price.live_spot` is where the stock is NOW. `price.spot_when_book_was_measured` is where it was when the chain was pulled. On a cached row these differ by a median of about $2 and by as much as $42.
- EVERY book-derived distance in this scene — `regime.flip`, `magnet`, `walls` — is measured from the BOOK's spot, not the live one, because that is the only frame in which the book's own numbers are consistent. That is the standing rule and blocks do not restate it. The EXCEPTION announces itself: a block carrying `sigma_measured_from: live_spot_because_chain_spot_was_absent` had no book spot to divide, so its distances are already in the live frame and `price.live_minus_book_spot_sigma` will be missing from the scene as well. To convert a book-measured distance to a distance from where price actually is, SUBTRACT `price.live_minus_book_spot_sigma` — it is how far the live spot sits above the book's spot, so taking it off a book-measured distance is what moves that distance into the live frame. The one distance NOT in the book's frame is `price.vwap_minus_live_spot_sigma`, which is measured off the live quote because vwap is a live-tape level; read its name as the subtraction it is, so POSITIVE means vwap is ABOVE price — price trading BELOW its average for the day. That is the opposite sense to `price.moved_last_30min_sigma`, which is positive when price ROSE.

HOW TO READ THE SCENE (grouped by force, not by metric):
- clock is the SESSION calendar and nothing else. `front_expiry.days_to_expiry` is where you are in the weekly cycle — a 4-dte Monday book holds standing positioning with all week to migrate, while 0 collapses to expiry-day mechanics, which run at full strength on the day itself. `minutes_to_close` says how much session is left for any read to resolve in — a 30-minute call needs 30 minutes of tape.
- scale: the sigma ruler, plus `expected_move_today_asym` — today's likely range split unevenly toward the side the options market fears (from put/call IV skew). It breathes with vol.
- regime: the day's character. `vol_trend` says whether implied vol is rising or falling NOW — it is the switch that arms vanna and charm. `flip` is the chop band, and it ships as ONE measured level plus a width: `band_center_is_gamma_flip_sigma` IS the gamma flip, the band runs that centre plus and minus `edges_are_center_plus_minus_sigma`, and `live_price_vs_band` says where price sits in it. Compute an edge if you need one — it is arithmetic on two numbers in front of you, not a third independent level, so an edge "agreeing" with the centre is one witness, not two. Words like "clear negative" describe WHERE PRICE SITS relative to the dealer-hedging flip, a location on the map, not a bearish or bullish stamp. `charm` is time-decay hedging: `magnitude_musd_per_day_uncalibrated` (compare day to day, never to another book) and `drifts_toward_strike`, the level it funnels toward late in the session — a level, never a promised direction.
- magnet: the strikes carrying the most gamma, each with its `share_of_book_gamma_pp`. `top_strike_lead_pp` is the top strike's lead over the runner-up in points of gamma mass — SMALL means no strike is really in charge, and there is no threshold where it flips: read the number, and read `top_strike_lead_vs_own_history` for whether today's lead is unusual for this tape. That comparison counts DISTINCT BOOKS, not scans, so a cached book never votes twice.
- breadth: `lopsidedness_0_is_even` is how one-sided the book is. It NEVER points a direction — both readings of which side a heavy book favours are unestablished here. `heavier_side` names a side as a fact, not a lean. It reads THE SAME GAMMA PILE as the magnet — always, by construction — so breadth agreeing with the magnet is one witness twice, not two witnesses.
- momentum: the ONLY block here that describes change during today's session. `window_between_books` tells you exactly what was compared — `books_compared` distinct books spanning `span_min` minutes, measured on the book clock, not the scan clock. `share_of_book_gamma_change_pp` and `gross_volume_change_contracts` are the change itself; there is no verdict word, because a fixed threshold cannot tell building from noise. (OI deltas and true order-flow CVD are not measured here; absent means unmeasured, never zero.)
- dealer_positioning: standing dealer positioning as of last night's close. `net_delta_bn` is $-delta in billions from an assumed-sign book, and ITS SIGN IS AN ARTIFACT OF THE FORMULA, which cannot produce another one — it has been positive on every one of 4,149 recorded scans, ranging 1.15 to 8.24 and never once crossing zero. Its sign is a fact about the formula, NOT a fact about dealers, so it is never evidence for anything and never means "dealers are long" or "dealers are bullish"; only `net_delta_change_30min_bn` can be news. `net_vanna_musd_per_vol_point` only matters when vol_trend is moving.
- walls: standing structure, up to two per side, ALWAYS ordered by DISTANCE, nearest first — walls.call[0]/put[0] are simply the first thing price would meet going that way, NOT the strongest; the second says what is behind the first (right there, or open air). `cluster_share_of_book_gamma_pp` is the CLUSTER's share of the whole signed surface — that number, not the position, says how heavy a wall is. It is deliberately NOT called `share_of_book_gamma_pp`: the magnet's field of that name is ONE STRIKE's share of a different surface, and the two disagree by a median 38% on strikes that appear in both. Never compare one to the other or add them together. When the heaviest cluster on a side is further out than both, it ships separately as call_heaviest_wall_behind_the_ladder / put_heaviest_wall_behind_the_ladder. `unchanged_for_min` is how long that wall has held (`unchanged_for_at_least_min` when the lookback ran out first); a wall that has not moved in hours is standing structure, not news.
- structure: where the board's gamma SITS, in dollars and shares, on the same book as `walls`. `bands` are runs of adjacent heavy strikes — low, high, the band's share of the whole surface, and which side of price it sits on — nearest first. `air` is a stretch between price and the nearest wall with almost no gamma in it. `weight_above_spot_pp` is the share of the whole surface sitting above price. Measured on this tape over 23 sessions: price spends no more time inside a band than inside a random band of the same width, moves toward one no more often than toward a random one, and the heaviest strike holds no better than an imaginary line at the same distance. So a band is a place the weight is, never a place price is drawn to, and a heavy wall is heavy, never overpowering. Name where the weight is; never what price will do about it.
- WHICH LEVELS YOU MAY NAME. You may name the magnet's top strike, and the strikes in the magnet list. You may NOT name a call wall or a put wall as a level where anything happens. This is arithmetic, not politeness: those two are DEFINED as the heaviest strike on their side of spot, so the call wall is above price and the put wall below it, always, on every row ever recorded. The instant price rises through a call wall, that strike stops qualifying and a different one takes the name. Measured: a wall relabels on a crossing 100% of the time, within a median of zero minutes, while the magnet relabels 0% of the time and is still the same strike 40 minutes later on 99% of occasions. A wall is real structure and you may describe how heavy it is; it is not a place price can be said to reach or break, because the label moves out of the way. THE ONE EXCEPTION is a level in `context.since_last_read.crossed_since_then`: that price is the wall AS IT STOOD AT YOUR LAST READING, frozen in the block, so it cannot relabel out from under the sentence — you may name it and say price moved through it, as the label it wore then.
- history flags when to reach outside: `price_at_level_unseen_earlier_today`, `tape_abnormal_vs_own_history`.
- WHO HOLDS WHAT IS ASSUMED, NOT MEASURED, AND THE ASSUMPTION IS PROBABLY WRONG FOR A SINGLE STOCK. Every signed number here — the gamma sign, the regime word, the flip, dealer positioning — rests on a convention that dealers are long the calls and short the puts. That convention was written for the S&P index. The studies that MEASURED single-stock positioning instead of assuming it found the opposite: end users are net sellers of both legs, so dealers are net LONG single-stock options, and average measured market-maker gamma is positive. On this book the convention implies dealers hold about a fifth of the company in stock, which is not credible. So: never say what dealers are doing, never say hedging will amplify or damp anything, and never treat the flip as a level price respects. Describe where the number is; do not describe who is on the other side of it.
- ANY missing field was not cleanly measured this scan. Treat absence as "no data", never as neutral, never as zero. The OPPOSITE case is stated outright: a `*_side_has_no_wall` flag or `flip.no_flip_anywhere_on_board` means the board WAS measured and genuinely holds nothing there — price in open air with no ceiling, or a book with no flip, is a real reading and often the loudest one in the scene. Absence = unknown; a no-wall flag = known empty. Never confuse the two.

YOU HAVE NO TOOLS, AND THAT IS THE POINT. Earlier versions granted a history
lookup and a web search. They were used zero times in 391 production calls, and
the contract now removes the reason to want them: an observation is a statement
about the snapshot in front of you, and every claim has to resolve to a pointer
INTO that snapshot. Anything you learned elsewhere could not be pointed at, so
it could not survive the gate. Work from what you were given, and say plainly
in `read` when what you were given is not enough to say anything at all.

HONESTY RULES, all of them load-bearing:
- Never cite a field listed in frozen_do_not_cite as the reason for anything new. If the magnet has not moved in two hours, it is not news.
- Never narrate an open-interest block in the present tense. See the provenance rules above — this is the one that will be checked.
- The magnet is usually a TIE. When top_strike_lead_pp is small, no strike is really in charge — say so rather than naming one.
- Never invent a level. If price is past the last named level on the board, say exactly that — on this name it is often true.
- NOTHING STANDING OUT IS THE COMMON ANSWER AND IT IS COMPLETE WORK. Most scans of most sessions hold nothing a reader needs. Returning quiet is a correct, finished answer and the one expected most often. Inventing something to fill the space is the worst failure available to you: a screen that cries wolf gets ignored, and then the real thing goes unread too.
- A FACT IN `context` IS NOT AN INSTRUCTION. A number ranking high against prior sessions may still be nothing worth mentioning. That judgement is the job.

WORDS THAT DELETE YOUR SENTENCE, listed in full so there is no guessing. Any of these appearing in `read` or in a note throws that text away — it is not softened, it is dropped, and the reader sees nothing:

  {", ".join(sorted(_BANNED_FORECAST))}
  {", ".join(sorted(_BANNED_CAUSAL))}

  {", ".join(sorted(_BANNED_JUDGEMENT))}

The first group is anything about where price goes or how likely something is. The third grades the board — strong, weak, a ceiling, a floor, due, at risk — which is a forecast wearing an adjective. Every inflection counts: favouring, targeted, pinning die with their stems. Two more checks run on what survives the lists: a sentence that places price above or under a number is compared with the live price and deleted when it is backwards, and a second reviewer reads the sentence for a forecast, an expectation, or a judgement hiding in ordinary words and deletes it on a hit. Describe; do not judge. The second is cause and effect: a board does not make things happen, and on this tape the walls relabel to follow price rather than the other way round, so a causal verb about one is false by measurement rather than merely unwise.

WHAT TO SAY INSTEAD. Describe position, not consequence: not "1500 is resistance" but "1500 is the heaviest strike, and price is just under it". Not "this should hold" but "this is where the largest share of the board's gamma sits". Not "the magnet is pulling price toward 1500" but "1500 holds the biggest share of the board's gamma, and price is drifting toward it" — drift describes, pull promises. The numbers inside these instructions are teaching aids, not board values: never quote a number you did not find in the scene. A measured past change with its window named is always welcome — "implied vol came down about two points over the last half hour" says only what was measured. Naming a level is description; saying what price will do there is not.

OUTPUT. Reply with ONLY a JSON object, no prose around it, no code fence:
{{"quiet": true | false,
 "read": "<TWO short sentences, forty words at the outside: what changed since your last reading — or that nothing did — and what the board shows now. Never omitted.>",
 "points": [{{"level": <a price that appears in the scene>, "note": "<a few words on why this one is worth watching>"}}]}}

POINTS ARE FOR LEVELS A READER SHOULD LOOK AT NOW. On an ordinary unchanged board an EMPTY list beside your quiet sentence is usually the better answer — repeating the same standing strike reading after reading teaches the reader to stop looking at the list."""


def present_tense_slips(reading: dict) -> list[str]:
    """Phrases that narrate an open-interest field as if it were happening now.

    sr-7. The scene labels every OI-derived block "as of last night's close",
    and a label is not a control: the same doctrine already told the model the
    book was N minutes stale and no reading ever discounted anything for it.
    This is the measurement half of the control — every hit is recorded on the
    read row so the rate is known before LEXICON_ENFORCE is allowed to reject
    anything. A guard that has never fired is not evidence that nothing is
    wrong; a guard that fires on 40% of readings is not a guard either, and
    only the tape can say which one this is."""
    # obs-1 retargets this at the fields that exist now. Left pointing at
    # `line`/`breaks_if`/`cited` it read the empty string on every call and
    # reported a clean sheet forever — a guard that cannot see its subject is
    # worse than no guard, because the zero looks like evidence.
    text = " ".join([str(reading.get("say") or ""),
                     str(reading.get("quiet_because") or "")]
                    + [str(o.get("what") or "")
                       for o in (reading.get("notable") or [])]).lower()
    if not any(w in text for w in _STALE_NOUNS):
        return []
    return sorted({v for v in _LIVE_VERBS if v in text})



# THOUSANDS SEPARATORS. Without the comma group, "about $1,225" tokenised as
# `1` (skipped as small) and `225`, and 225 matches nothing on a $1,500 stock —
# so the most natural sentence the doctrine asks for deleted itself.
_NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _clip(text: str, n: int) -> str:
    """Trim to a WORD boundary. The first draft cut at a fixed character count
    and the very first live quiet read came back ending "...the two active st",
    which reads as a truncated feed rather than a finished sentence."""
    if len(text) <= n:
        return text
    cut = text[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,;:") + "…"


def banned_words(text: str) -> list[str]:
    """Every banned term in a piece of prose, lowercased and deduplicated."""
    return sorted({m.group(0).lower() for m in _BANNED_RE.finditer(text or "")})


def prices_on_the_board(scene) -> set:
    """Prices the board actually names — strikes, walls, the flip, spot.

    A `level` used to be checked against every number anywhere in the scene,
    which let `4.1` (the book refresh interval), `13.1` (a gamma percentage),
    `246` (minutes a wall had held) and `120` (the scan count) all pass as
    prices on a $1,500 stock. A level is the one field a reader acts on, so it
    is checked against the things that ARE prices."""
    out = set()
    mag = scene.get("magnet") or {}
    for t in (mag.get("top_strikes") or []):
        v = _fin(t.get("strike"))
        if v is not None:
            out.add(round(v, 2))
    w = scene.get("walls") or {}
    for side in ("call", "put"):
        for e in (w.get(side) or []):
            v = _fin(e.get("strike"))
            if v is not None:
                out.add(round(v, 2))
        hv = w.get(f"{side}_heaviest_wall_behind_the_ladder") or {}
        v = _fin(hv.get("strike"))
        if v is not None:
            out.add(round(v, 2))
    pr = scene.get("price") or {}
    for k in ("live_spot", "spot_when_book_was_measured",
              "session_high", "session_low"):
        v = _fin(pr.get(k))
        if v is not None:
            out.add(round(v, 2))
    ch = ((scene.get("regime") or {}).get("charm") or {})
    v = _fin(ch.get("drifts_toward_strike"))
    if v is not None:
        out.add(round(v, 2))
    # THE CONTEXT BLOCK'S OWN PRICES. `changed_since_last_book` names a was/now
    # pair for the heaviest strike and each wall, read off the diary row rather
    # than the rebuilt scene — so those prices were absent here and the gate
    # deleted any point built on the very block the doctrine tells the model to
    # trust. Measured: 14 of the 32 scans in the newest session that carried a
    # change block named at least one price the gate would have rejected.
    for mv in ((scene.get("context") or {}).get("changed_since_last_book") or {}).values():
        if isinstance(mv, dict):
            for side in ("was", "now"):
                v = _fin(mv.get(side))
                if v is not None:
                    out.add(round(v, 2))
    # obs-3: the since-last-read frame's own prices. A crossed level is FROZEN
    # in the block precisely so it can be named — without this admission the
    # model narrates a crossing in prose and its point is deleted as
    # level_not_on_the_board, which then logs a FALSE forced abstain. Only the
    # price-shaped fields are admitted; minutes and deltas stay out, which is
    # this function's whole charter.
    slr = ((scene.get("context") or {}).get("since_last_read") or {})
    for c in (slr.get("crossed_since_then") or []):
        v = _fin(c.get("level") if isinstance(c, dict) else None)
        if v is not None:
            out.add(round(v, 2))
    for k in ("spot_then", "spot_now"):
        v = _fin(slr.get(k))
        if v is not None:
            out.add(round(v, 2))
    for k in ("low", "high"):
        v = _fin((slr.get("held_between_since_last_read") or {}).get(k))
        if v is not None:
            out.add(round(v, 2))
    # obs-4: a box edge is a level a reader looks at — the opening box, the
    # box in force and the prior sessions' range may all be pointed at
    rg = ((scene.get("context") or {}).get("ranges") or {})
    for blk in ("opening", "in_force", "prior_sessions"):
        for k in ("low", "high"):
            v = _fin((rg.get(blk) or {}).get(k))
            if v is not None:
                out.add(round(v, 2))
    # THE GAMMA FLIP IS DELIBERATELY NOT A NAMEABLE LEVEL, and this is the one
    # exclusion worth spelling out because the flip looks more like a level than
    # anything else on the board.
    #
    # It exists ONLY because the sign convention puts dealers on opposite sides
    # of calls and puts. Per-option gamma is strictly positive, so under either
    # same-sign convention the net is one-signed at every price and there is no
    # flip anywhere. Computed on a live SNDK chain: a root at 1514 under
    # long-calls/short-puts, and NO root in [100, 40000] under either same-sign
    # convention. It is a property of an assumption, not of the market.
    #
    # And its uncertainty swamps its distance. Across defensible inputs — ±10
    # vol points, rates 0-6%, expiry and OI filters — the level ranges 1472 to
    # 1532, which is 0.46 of one day's sigma on this name, while sitting 0.27 of
    # a sigma from spot. You cannot say which side of it price is on.
    #
    # The flip stays in the scene as a location and keeps its own block; it is
    # simply not something the model may point a reader AT.
    return out


def numbers_on_the_board(scene) -> set:
    """Every number anywhere in the scene, rounded to 2dp.

    obs-2 checks prose against THIS rather than against a shortlist of offered
    pointers. The model now reads the whole board and reasons over all of it, so
    the honest question is no longer "did you cite what we gave you" but "is the
    number you said actually on the board" — which catches invention without
    constraining thought."""
    out = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            out.add(round(float(x), 2))
        elif isinstance(x, str):
            # commas stripped, matching _numbers_check: the scene's OWN prose
            # ships "1,893 distinct books", and float("1,893") raised and was
            # silently skipped — so a verbatim quote of the board's own string
            # was deleted as invented and logged as a FALSE forced abstain.
            # NUMBERS INSIDE STRINGS COUNT. `context.vs_prior_sessions` states
            # its ranks as prose — "higher than 16 of the 22 prior sessions" —
            # and walking numeric leaves alone made 16 and 22 invisible to the
            # gate. The model would quote the rank it was just handed and lose
            # the whole sentence for inventing it. Measured live: the read was
            # deleted on every call, a 0% prose-survival rate, which is a broken
            # contract however good the rest of it is.
            for m in _NUM_RE.finditer(x):
                try:
                    out.add(round(float(m.group(0).replace(",", "")), 2))
                except ValueError:
                    pass
    walk(scene)
    return out


def check_reading_against_scene(obj: dict, scene: dict, judge=None) -> dict:
    """Check the model's reading against the scene it was handed.

    obs-2 GAVE THE REASONING BACK. obs-1b had Python nominate what was unusual
    and left the model to narrate the shortlist; that was the wrong division of
    labour, and the latency that seemed to justify it turned out to be a global
    `effortLevel` setting rather than the contract. Judging what matters on a
    board IS the reasoning, and it is the half the model is for.

    What is still checked is what can be checked without constraining thought:
    a number spoken must be a number on the board, a level named must be a level
    that exists, and the vocabulary stays descriptive. Nothing here tells the
    model WHAT to find."""
    nums = numbers_on_the_board(scene)
    levels = prices_on_the_board(scene)
    dropped: list[str] = []

    def _numbers_check(text, tag):
        """Every number in the prose must exist in the scene, allowing the
        rounding a person would actually use out loud."""
        for m in _NUM_RE.finditer(text or ""):
            n = float(m.group(0).replace(",", ""))
            # 1.0 was the floor because counts and clock figures cluster there.
            # It also made every SIGMA unreachable — wall sigmas, the flip band,
            # top_strike_sigma, moved_last_30min_sigma are all under 1.0 — which
            # is exactly the class of number the doctrine forbids the model from
            # computing. 0.05 keeps small counts out and brings sigmas back in.
            if abs(n) <= 0.05:
                continue
            dp = len(m.group(0).split(".")[1]) if "." in m.group(0) else 0
            # a whole number is rounded speech: "about 64" against a measured
            # 63.52 was deleting true sentences. A dollar of slack for
            # integers; decimals keep the precision they claim.
            tol = 1.0 if dp == 0 else max(0.5 * 10 ** -dp, 0.011)
            # SIGN-BLIND ON PURPOSE. "down about 1.7% on the day" against a
            # scene carrying vs_prior_close_pct: -1.72 was deleting the whole
            # sentence, because the gate looked for +1.7. "Down about X" is how
            # people say a negative number out loud, and the direction is
            # already carried by the word next to it.
            if not any(abs(abs(n) - abs(v)) <= tol for v in nums):
                dropped.append(f"{tag}_number_not_on_the_board:{m.group(0)}")
                return False
        return True

    spot_now = _fin((scene.get("price") or {}).get("live_spot"))
    read = _clip(str(obj.get("read") or "").strip(), 700)
    hits = banned_words(read)
    if hits:
        dropped.append("read_banned:" + ",".join(hits))
        read = ""
    elif read and not _numbers_check(read, "read"):
        read = ""
    elif read and (ps := position_slips(read, spot_now)):
        # obs-4: "still under the 1500 strike" at 1528.70 — every number on the
        # board, no banned word, and backwards
        dropped.append("read_position_contradicts_spot:" + ",".join(ps))
        read = ""

    points = []
    for pt in (obj.get("points") or [])[:_OBS_MAX * 2]:
        if not isinstance(pt, dict):
            dropped.append("point_malformed")
            continue
        raw_note = str(pt.get("note") or "").strip()
        note = _clip(raw_note, 160)
        if note != raw_note:
            # nothing is shortened in silence — same rule as the points cap
            dropped.append("point_note_clipped")
        lvl = _fin(pt.get("level"))
        if not note or lvl is None:
            dropped.append("point_malformed")
            continue
        # half a dollar of tolerance, matching the prose gate. The grid carries
        # halves (1487.5, 1465), and a reader saying "1488" is speaking.
        if not any(abs(lvl - x) <= 0.5 for x in levels):
            # a level the board does not contain is the one invention that
            # matters most: it sends a reader to a price nothing measured.
            dropped.append(f"level_not_on_the_board:{lvl:g}")
            continue
        h = banned_words(note)
        if h:
            dropped.append("point_banned:" + ",".join(h))
            continue
        if not _numbers_check(note, "point"):
            continue
        ps = position_slips(note, spot_now)
        if ps:
            dropped.append("point_position_contradicts_spot:" + ",".join(ps))
            continue
        points.append({"level": lvl, "note": note})
    # obs-4: THE SEMANTIC REVIEW runs over what survived, before the cap, so
    # the cap counts survivors. Injectable for tests; the module switch turns
    # the live reviewer off entirely. A reviewer that could not answer is
    # recorded as skipped and nothing is deleted on its account.
    if judge is None and SEMANTIC_GUARD:
        judge = semantic_review
    if judge is not None:
        texts = ([read] if read else []) + [p["note"] for p in points]
        if texts:
            verdicts = judge(texts)
            if verdicts is None:
                dropped.append("semantic_guard_skipped")
            else:
                k = 0
                if read:
                    if verdicts[0]:
                        dropped.append("read_semantic_forecast:" + verdicts[0])
                        read = ""
                    k = 1
                kept = []
                for j, p in enumerate(points):
                    v = verdicts[k + j] if k + j < len(verdicts) else None
                    if v:
                        dropped.append("point_semantic_forecast:" + v)
                    else:
                        kept.append(p)
                points = kept
    if len(points) > _OBS_MAX:
        # never drop without saying so. Truncating in silence recorded the model
        # as having CHOSEN to say less than it did, which corrupts the one
        # distinction the nightly audit is built on.
        dropped.append(f"points_truncated_to_{_OBS_MAX}")
        points = points[:_OBS_MAX]

    # QUIET MEANS NO LEVELS TO WATCH, not silence. The prose stays either way:
    # on a quiet read it says in one line why the board is ordinary, and that
    # line is the only thing distinguishing one quiet slice from another in the
    # memory store. Without it every uneventful session records the same
    # sentence, and a history of identical entries cannot support the pattern
    # search it exists for.
    quiet = not points
    reading = {"quiet": quiet}
    if read:
        reading["read"] = read
    if points:
        reading["points"] = points
    if quiet:
        reading["abstain"] = "forced" if dropped else "chosen"
    tense = present_tense_slips({"say": read,
                                 "notable": [{"what": p["note"]} for p in points]})
    if tense:
        reading["stale_language_flags"] = tense
        if LEXICON_ENFORCE:
            # sr-7 shipped this switch wired to nothing — flipping it on would
            # have changed no behaviour at all. Enforcement drops the read the
            # way a banned verb does: recorded in dropped, never silently.
            dropped.append("read_stale_tense:" + ",".join(tense))
            reading.pop("read", None)
            if not reading.get("points"):
                reading["quiet"] = True
                reading["abstain"] = "forced"
    if dropped:
        reading["dropped_observations"] = dropped[:8]
    return reading


def first_json_object(text: str):
    """First balanced JSON object in a blob (the CLI envelope wraps the reply)."""
    if not isinstance(text, str):
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


def call_the_model(prompt: str, model: str, timeout: float = CALL_TIMEOUT_S):
    """ONE `claude -p` call, every tool disallowed (_NO_TOOLS — obs-1 revoked
    the sr-2 grant). Doctrine rides --append-system-prompt so it stays
    byte-identical all day and is served from cache; the varying scene rides
    the body.

    Returns (obj | None, error | None, wall_seconds, raw | None) — `raw` is a
    truncated copy of what actually came back whenever parsing failed, because
    an undiagnosable drop is the blindness raw_reply exists to end, and the
    largest drop there is (an unparseable reply) was the one case that kept
    none."""
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
           "--append-system-prompt", _DOCTRINE,
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--effort", CALL_EFFORT,
           "--disallowedTools", *_NO_TOOLS]   # variadic — must stay LAST
    t0 = _clock.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout", round(_clock.time() - t0, 1), None
    except OSError as e:
        return None, f"spawn: {e}", round(_clock.time() - t0, 1), None
    wall = round(_clock.time() - t0, 1)
    if r.returncode != 0:
        return (None, f"rc={r.returncode}: {(r.stderr or '')[-200:]}", wall,
                (r.stdout or "")[:1200] or None)
    env = first_json_object(r.stdout)
    text = env.get("result") if isinstance(env, dict) and "result" in env else r.stdout
    obj = first_json_object(text if isinstance(text, str) else r.stdout)
    return obj, None, wall, (None if obj is not None
                             else str(text or r.stdout)[:1200] or None)


def _fin(v) -> Optional[float]:
    """A finite, non-bool number or None. NaN is the dangerous one: it rides
    isinstance checks, renders as a token in the prompt, and NaN < threshold
    comparisons silently pick the CONFIDENT branch (adversarial audit 08-02:
    NaN masses read as 'a strike IS in charge')."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _row_atm_iv(r: dict) -> Optional[float]:
    """A row's front-book ATM IV. ROW_V 3 records it; older rows derive it
    exactly (sigma_live = spot·iv/√252, so iv = sigma_live·√252/spot)."""
    iv = r.get("atm_iv")
    if isinstance(iv, (int, float)) and iv > 0:
        return float(iv)
    sl, sp = r.get("sigma_live"), r.get("spot")
    if isinstance(sl, (int, float)) and isinstance(sp, (int, float)) and sl > 0 and sp > 0:
        return sl * _SQRT_TDAYS / sp
    return None


def vol_trend(rows: list[dict], now: datetime) -> Optional[dict]:
    """Is implied vol rising or falling NOW — the switch that arms vanna/charm.
    30-min ATM-IV change in vol points, by timestamp (a scan gap must not
    shorten the window); flat band VOL_TREND_FLAT pre-registered off the
    recorded Δ30m distribution. None without ~30 min of history.

    EXPIRY-AFTERNOON GUARD: on the weekly's own expiry day the front-book ATM
    IV reprices on minutes-to-close, so into the bell the solve balloons on
    pure clock mechanics (recorded 07-31: +59 vol pts in 30 min at the close
    vs a ±12 p90 on normal days). A τ→0 artifact is not a vol signal — the
    read is omitted once the ruler itself says late_day on a 0-DTE book."""
    if rows:
        last = rows[-1]
        rr = last.get("range_ruler") or {}
        if (last.get("gex_views") or {}).get("front_dte") == 0 \
                and rr.get("quality") == "late_day":
            return None
    ser = []
    for r in rows:
        t, iv = _ts(r), _row_atm_iv(r)
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


def flip_block(row: dict, sd, ran_30m: Optional[float]) -> Optional[dict]:
    """The chop band told as one object: edges, center, and where price sits.
    ct/pt are the ladder's transition edges; the center IS the gamma flip
    (live-schema fact: profile_ladder.hvl = the flip — there is no volume-shelf
    hvl on this book). live_price_vs_band is position from the ladder's own zone
    word, plus a drift clause only when the last 30 min actually moved."""
    ladder = row.get("profile_ladder")
    ladder = ladder if isinstance(ladder, dict) else {}
    ct_s, pt_s = sd(ladder.get("ct")), sd(ladder.get("pt"))
    center_s = sd(row.get("gamma_flip"))
    if ct_s is None and pt_s is None and center_s is None:
        return None
    words = {"positive": "above ct (clear positive)",
             "positive transition": "inside band, gamma-positive side",
             "negative transition": "inside band, gamma-negative side",
             "negative": "below pt (clear negative)"}
    pos = words.get(ladder.get("state"))
    if pos and "inside band" in pos and isinstance(ran_30m, (int, float)) and abs(ran_30m) >= 0.05:
        pos += ", drifting toward " + ("ct (pos edge)" if ran_30m > 0 else "pt (neg edge)")
    half = (round((ct_s - pt_s) / 2.0, 2)
            if ct_s is not None and pt_s is not None else None)
    out = {"band_upper_edge_ct_sigma": ct_s,
           "band_lower_edge_pt_sigma": pt_s,
           "band_center_is_gamma_flip_sigma": center_s,
           "edges_are_center_plus_minus_sigma": half,
           "live_price_vs_band": pos}
    # sr-8: when the half-width is known the two edges are the CENTRE, restated.
    # Measured over 4,149 recorded scans: ct == centre + 0.25 and pt ==
    # centre - 0.25 on 2,814 of 2,814 scans that carried a band, one distinct
    # half-width. sr-7's own doctrine already called them "arithmetic, not three
    # independent levels" — this stops paying for the arithmetic twice. The
    # width itself STAYS and is the reason this is safe: it is the measured
    # number, so if the ladder ever produces a different band the scene says so
    # instead of a doctrine constant quietly lying.
    #
    # Dropped only when `half` exists (which by construction means both edges
    # did) AND the CENTRE survived. Both conditions are load-bearing. A partial
    # band — one edge, no width — is unreached on the recorded tape but reachable
    # in the code, and there the raw edge is the only reading there is. So is a
    # band with both edges and no `gamma_flip`: dropping the edges there would
    # ship a half-width measured from a centre the model cannot see, which is
    # the old comment's own warning ("it cannot survive alone") pointed at the
    # field that inherited the risk.
    if half is not None and center_s is not None:
        out.pop("band_upper_edge_ct_sigma")
        out.pop("band_lower_edge_pt_sigma")
    return {k: v for k, v in out.items() if v is not None} or None


def charm_block(row: dict) -> Optional[dict]:
    """Front-book charm: magnitude (uncalibrated $M of delta bleed per day —
    compare day to day, not to other books) and the strike the clock funnels
    toward. N11 stands: no direction word, ever — net charm's sign is
    structurally locked by the assumed-sign book, and a constant that reads
    like a signal manufactures conviction. Omitted when not cleanly computed."""
    fl = row.get("flows_front") or {}
    cex = fl.get("cex")
    if not isinstance(cex, (int, float)):
        return None
    out = {"magnitude_musd_per_day_uncalibrated": round(abs(cex) / 1e6, 2)}
    if isinstance(fl.get("charm_wall"), (int, float)):
        out["drifts_toward_strike"] = fl["charm_wall"]
    return out


def momentum_block(rows: list[dict], band: dict) -> Optional[dict]:
    """Book-over-book deltas for the top magnet strikes over the last
    MOMENTUM_ROWS scans. share_of_book_gamma_change_pp = the strike's share of
    front-book gamma mass, in pp (scale-free — raw gamma is unit-opaque);
    gross_volume_change_contracts = contracts traded there since the reference
    book, only when the strike sat inside both windows (a window shift is the
    telescope, not flow).

    sr-7 removed the verdict word (`read`: building / fading / steady). It was
    MOMENTUM_BUILD_PP, a 0.5pp constant fitted in July, shipped to the model as
    a finished judgement — the same pattern already deleted from `is_a_tie` and
    `dex_word`. The change itself is the evidence and it is right here; a
    threshold that cannot track a distribution must not speak for it.

    DELIBERATELY ABSENT (omit-never-null, flagged in the inventory doc):
    oi_d — upstream OI updates once daily, an intraday delta is a frozen 0;
    cvd — no bid/ask aggressor tape exists for the stock leg, and a
    bar-direction proxy measures drift, not aggression (the 07-17 BVC lesson)."""
    if len(rows) <= MOMENTUM_ROWS or not band.get("top"):
        return None
    cur, ref = rows[-1], rows[-1 - MOMENTUM_ROWS]
    t_c, t_r = _ts(cur), _ts(ref)
    if t_c is None or t_r is None:
        return None
    # sr-7: comparing a book against itself yields 0.00 on every strike and
    # reads as "steady" — a manufactured non-event. The feed serves a cached
    # book on half the scans, so this is reachable whenever the cadence slips.
    b_c, b_r = _book_asof(cur), _book_asof(ref)
    if b_c is None or b_r is None or b_c <= b_r:
        return None
    def _pairs(r, key, absolute=False):
        # same torn-surface tolerance magnet_band has: a NaN or non-numeric
        # pair is skipped, never crashed on and never allowed to poison the
        # denominator into confident NaN reads (final verification pass 08-02)
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
    # shares are computed over the INTERSECTION of the two windows: the strike
    # window is spot-relative and moves with price, so a strike entering or
    # leaving the frame shifts a full-window denominator — the same telescope
    # artifact magnet_band documents. Comparing like against like keeps a
    # "building" read about the book, never about the frame (SE review 08-02).
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
        entry = {"share_of_book_gamma_change_pp": round(gex_d, 2)}
        if k in vg_c and k in vg_r:            # _pairs already guarantees finite
            entry["gross_volume_change_contracts"] = int(vg_c[k] - vg_r[k])
        by[f"{k:g}"] = entry
    if not by:
        return None
    # sr-7: the window is told on the BOOK clock, because the numbers above are
    # differences between two books, not between two scans. The old label said
    # "last 5 scans (10m)" off the scan clock; measured book-to-book the same
    # five scans span 8 or 12 minutes (median 12.3 over August), so the label
    # was wrong by up to a quarter of its own window and wrong in a direction
    # the model could not check. books_compared says how many distinct books
    # the span actually contains — 5 cached scans can be as few as 3.
    books = [t for t in (_book_asof(r) for r in rows[-1 - MOMENTUM_ROWS:])
             if t is not None]
    # sr-8 drops `last_book_measured_at`: it is the CURRENT book, so it equalled
    # data_sources.options_book.measured_at on 4,026 of 4,026 recorded scans
    # that carried momentum. The window's FAR end is the one a reader cannot get
    # anywhere else, so that is the one that ships.
    window = {"books_compared": len({t.isoformat() for t in books}),
              "span_min": round((b_c - b_r).total_seconds() / 60.0, 1),
              "first_book_measured_at": b_r.isoformat()}
    return {"window_between_books": window, "by_strike": by}


def dealer_positioning_block(rows: list[dict]) -> Optional[dict]:
    """Second-order dealer lean, one place. dex = standing $-delta (billions)
    plus its timestamp-true 30-min change. vanna = front-book net ($M per vol
    point), armed by vol_trend. Each field measured-or-absent; an empty block
    is dropped, never shipped hollow.

    NO LEAN WORD (adversarial audit 08-02): a sign-derived word read
    "long delta" on 756/756 simulated scenes — the exact banned pattern the
    module header documents (a constant that reads like a signal manufactures
    conviction, in one direction, forever). The blueprint's own frame is that
    the CHANGE is the signal, so the change is what ships.

    sr-3 (2026-08-08) finishes that job. Deleting the word was not enough: the
    doctrine still glossed the sign as "positive = dealers estimated net LONG
    delta", which is the same constant wearing a definition instead of an
    adjective. `net_dex_total` is > 0 BY CONSTRUCTION (lefteye_dex.py:22, its
    own words) and measured positive on 1,675 of 1,675 recorded rows — so that
    gloss was a permanently-bullish reading handed over as a fact about the
    market. The doctrine now names the constancy, and the field carries its own
    note so the model meets the caveat where it meets the number. The LEVEL
    stays because net_change_30min is measured off it."""
    if not rows:
        return None
    row = rows[-1]
    out = {}
    net = _fin((row.get("dex_views") or {}).get("net_dex_total"))
    if net is not None:
        # sr-3 put the sign caveat on the FIELD as well as in the doctrine, on
        # the reasoning that the model meets the number, not the paragraph.
        # sr-8 returns it to the doctrine alone: `True` on 4,149 of 4,149 scans
        # is a constant, the doctrine states the same caveat at more length
        # (naming the 1,675/1,675 measurement the field could only assert), and
        # the field's own name had become the argument for keeping it. The
        # caveat is not weakened — it is stated once, where repetition is free.
        out["net_delta_bn"] = round(net / 1e9, 2)
        t_now = _ts(row)
        if t_now is not None:
            for r in reversed(rows[:-1]):
                t = _ts(r)
                if t is None or (t_now - t) < timedelta(minutes=VOL_TREND_MIN):
                    continue
                if (t_now - t) > timedelta(minutes=VOL_TREND_MAX):
                    break            # outage-shaped window — no honest 30m read
                ref = _fin((r.get("dex_views") or {}).get("net_dex_total"))
                if ref is not None:
                    out["net_delta_change_30min_bn"] = round((net - ref) / 1e9, 2)
                break
    vex = _fin((row.get("flows_front") or {}).get("vex"))
    if vex is not None:
        out["net_vanna_musd_per_vol_point"] = round(vex / 1e6, 2)
    return out or None


def _prior_sessions(today: str) -> dict:
    """Every measured value of the graded quantities, from CLOSED sessions only.

    Causal by construction — today's own file is never opened, so a number can
    never be graded against a distribution it is itself inside, and the answer
    a scan gets at 09:31 is the answer it gets at 15:59. Cached to disk against
    the exact session list it was built from, so a fresh process re-reads ~8
    small files once a day rather than on every tick.

    Forced/off-hours rows are excluded on the same rule the reader uses
    everywhere else: a midnight row is not a scan of the tape."""
    days = sorted(p for p in _diary_dir().glob("2026-*.jsonl")
                  if p.stem < today)[-PCTL_MAX_SESSIONS:]
    key = [p.stem for p in days]
    cache = _reads_dir() / "pctl_prior.json"
    try:
        blob = json.loads(cache.read_text())
        if blob.get("sessions") == key and blob.get("pctl_v") == PCTL_CACHE_V:
            return blob
    except (OSError, ValueError):
        pass
    gaps: list[float] = []
    lops: list[float] = []
    # obs-1: ONE VOTE PER DAY, alongside the per-book lists. A scan-level
    # percentile is the wrong denominator for "is today unusual" — measured
    # over 26 recorded sessions the per-session medians of the magnet lead span
    # 0.87 to 12.59, so a day sitting in a tail sits there ALL DAY and casts
    # ~90 identical votes. Ranking today's value against 90x22 book-values told
    # us something was extreme on 53% of scans, which is not what extreme means.
    # The day medians below are the honest comparison: 22 sessions, 22 votes.
    day_gaps: list[float] = []
    day_lops: list[float] = []
    for p in days:
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        seen_book = None
        day_gap_vals: list[float] = []
        day_lop_vals: list[float] = []
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue                      # a torn line is skipped, never fatal
            if (r.get("meta") or {}).get("forced"):
                continue
            # sr-7: ONE VOTE PER BOOK. Both graded quantities are pure functions
            # of the option book, and the feed re-serves a cached book on half
            # the scans — so a per-scan distribution counts every second
            # observation twice and calls the duplicate independent evidence.
            # Measured on 2026-08-28: 188 scans, 94 books. The correction is not
            # cosmetic — today's lopsidedness graded "bigger than 78% of scans"
            # and "bigger than 58% of distinct books" off the same tape.
            bk = (r.get("meta") or {}).get("book_asof") or r.get("ts")
            if bk is not None and bk == seen_book:
                continue
            seen_book = bk
            b = magnet_band(r)
            if b and len(b["top"]) >= 2 and b["gap_pp"] is not None:
                gaps.append(b["gap_pp"])
                day_gap_vals.append(b["gap_pp"])
            lp = _lopsided(r)
            if lp is not None:
                lops.append(lp)
                day_lop_vals.append(lp)
        if day_gap_vals:
            day_gaps.append(statistics.median(day_gap_vals))
        if day_lop_vals:
            day_lops.append(statistics.median(day_lop_vals))
    blob = {"sessions": key, "pctl_v": PCTL_CACHE_V,
            "magnet_gap_pp": sorted(gaps), "lopsidedness": sorted(lops),
            "magnet_gap_pp_days": sorted(day_gaps),
            "lopsidedness_days": sorted(day_lops)}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(blob))
    except OSError:
        pass                                   # a cache that cannot write still answers
    return blob


def _pctl_word(name: str, value, now: Optional[datetime]) -> Optional[str]:
    """"bigger than 12% of 1,001 distinct books in the 8 prior sessions" — a
    number's standing in its own recorded history, with n on its face.

    sr-7: n is DISTINCT BOOKS, not scans. The denominator is on the face of the
    string because the two differ by 2x and the difference moves the answer —
    the same lopsidedness reads p78 against scans and p58 against books.

    This is the granular replacement for a pass/fail threshold, and it is
    granular in the one way that survives: a fixed 5.0pp cut fitted in July
    still says 5.0pp in August, while a percentile re-reads the tape it is
    describing. OMITTED — never guessed — under PCTL_MIN_SESSIONS, because a
    percentile is a claim about a distribution and three days is not one."""
    if value is None or now is None:
        return None
    try:
        blob = _prior_sessions(now.strftime("%Y-%m-%d"))
    except Exception:                          # history is a nicety, never a blocker
        return None
    xs, n = blob.get(name) or [], len(blob.get("sessions") or [])
    if n < PCTL_MIN_SESSIONS or not xs:
        return None
    below = sum(1 for x in xs if x < value)
    return (f"bigger than {round(below / len(xs) * 100)}% of {len(xs):,} "
            f"distinct books in the {n} prior sessions")


def _lopsided(row: dict) -> Optional[float]:
    """How one-sided the book's resistance is, 0 = even. The aggregator's own
    `shove` quantity, unchanged — `|up - dn| / max(|up|, |dn|)`."""
    sh = _shove(row)
    up, dn = sh.get("shove_up_margin"), sh.get("shove_down_margin")
    if not isinstance(up, (int, float)) or not isinstance(dn, (int, float)):
        return None
    return round(abs(up - dn) / max(abs(up), abs(dn), 1e-9), 3)


def breadth_block(row: dict, now: Optional[datetime]) -> Optional[dict]:
    """How lopsided the book is — a NUMBER, first time it has ever reached the
    model.

    Until sr-3 this quantity existed only inside the aggregator, where it was
    spent on one comparison (`rel >= 0.30`) and thrown away: 0.299 and 0.001
    both came out "not admissible", and the model was told neither. That
    threshold was fitted in July and the tape moved under it — which is the
    general case, since a constant cannot track a distribution.

    NO DIRECTION, and that is load-bearing: on four sessions the two possible
    conventions (heavier side resists / heavier side wins) swing ~30 points day
    to day across 86 sign runs, so neither is established and this module will
    not assert one. `heavier` names a side as a fact; it is not a lean.

    Also NOT independent of the magnet — both read the same gamma pile, and
    replay had them agreeing on 69 of 69 scans where both cleared. The note
    says so, because two witnesses who are one witness is exactly the failure
    the flip band was carrying."""
    rel = _lopsided(row)
    if rel is None:
        return None
    sh = _shove(row)
    up, dn = sh.get("shove_up_margin"), sh.get("shove_down_margin")
    # sr-8: the "one witness, not two" warning was a frozen string here and a
    # sentence in the doctrine. The warning stands; only the duplicate goes.
    out = {"lopsidedness_0_is_even": rel,
           "heavier_side": "up" if up > dn else "down"}
    w = _pctl_word("lopsidedness", rel, now)
    if w:
        out["lopsidedness_vs_own_history"] = w
    return out


def _rank_among_days(name: str, value, now) -> Optional[tuple]:
    """(how many prior SESSIONS this value beats, how many there were).

    A rank among days, never a percentile of scans. The distinction is the
    single most important thing about this function: 94-98.5% of the variance
    in these quantities is BETWEEN days, so one unusual session casts ~90
    identical votes into a scan-level distribution and every scan of it reads
    as extreme. Ranked against 22 session medians, a tail is a tail once."""
    if value is None or now is None:
        return None
    try:
        blob = _prior_sessions(now.strftime("%Y-%m-%d"))
    except Exception:
        return None
    xs = blob.get(name + "_days") or []
    if len(xs) < PCTL_MIN_SESSIONS:
        return None
    return sum(1 for x in xs if x < value), len(xs)


def session_context(scene: dict, rows: list[dict], now) -> Optional[dict]:
    """WHAT THE MODEL CANNOT SEE FROM ONE SNAPSHOT, and nothing else.

    obs-2. The previous version of this function decided what was UNUSUAL and
    handed the model a shortlist to narrate. That was the wrong division of
    labour: judging what matters on a board is reasoning, and reasoning is the
    half the model is for. It is also unnecessary — the latency that appeared to
    justify moving the work into Python turned out to be a global `effortLevel`
    setting, and simplifying the schema was measured at no effect (112-118s
    either way). With effort pinned there is room for the model to think.

    What survives is the half Python must do because the model is structurally
    unable to: it is handed ONE snapshot, so it cannot know that today's magnet
    lead is the widest of 22 sessions, or that the gamma sign flipped since the
    last book. Those are facts about time, and the payload has no time in it.

    So this block carries FACTS AND NO VERDICTS. A rank, not "extreme". A
    before-and-after, not "changed_this_scan". Nothing here says what is
    interesting; that is the model's call, on the whole scene, and it may
    perfectly well decide none of it matters."""
    out: dict = {}

    # what today's numbers look like against this stock's own CLOSED sessions.
    # One value per session — 94-98.5% of the variance in these is BETWEEN days,
    # so a per-scan comparison lets one unusual session vote ~90 times.
    ranks = {}
    mag = scene.get("magnet") or {}
    for label, key, val in (
            ("top_strike_lead_pp", "magnet_gap_pp", mag.get("top_strike_lead_pp")),
            ("lopsidedness", "lopsidedness",
             (scene.get("breadth") or {}).get("lopsidedness_0_is_even"))):
        r = _rank_among_days(key, val, now)
        if r:
            beats, n = r
            ranks[label] = (f"higher than {beats} of the {n} prior sessions"
                            if beats else f"lower than all {n} prior sessions")
    if ranks:
        out["vs_prior_sessions"] = ranks

    # what actually moved since the last OPTION BOOK — not the last scan, since
    # half of all scans carry the same book served twice.
    books = _distinct_books_rows(rows)
    if len(books) >= 2:
        prev, cur = books[-2], books[-1]
        moved = {}
        # BOTH sides have to be real. The first live scene came back with
        # `gamma_sign: {was: "unknown", now: "positive"}` — a book that had not
        # been read yet, reported as a flip. A transition out of not-measured is
        # not a change in the market, and telling the model it is invites a
        # sentence about something that never happened.
        _known = lambda v: v not in (None, "", "unknown", "unmeasured")
        if (_known(cur.get("gamma_sign")) and _known(prev.get("gamma_sign"))
                and cur["gamma_sign"] != prev["gamma_sign"]):
            moved["gamma_sign"] = {"was": prev["gamma_sign"],
                                   "now": cur["gamma_sign"]}
        pm = _fin((prev.get("gex_views") or {}).get("magnet"))
        cm = _fin((cur.get("gex_views") or {}).get("magnet"))
        if cm is not None and pm is not None and cm != pm:
            moved["heaviest_strike"] = {"was": pm, "now": cm}
        for k, name in (("call_wall", "nearest_call_wall"),
                        ("put_wall", "nearest_put_wall")):
            a, b = _fin(prev.get(k)), _fin(cur.get(k))
            if a is not None and b is not None and a != b:
                moved[name] = {"was": a, "now": b}
        if moved:
            out["changed_since_last_book"] = moved

    return out or None


def _distinct_books_rows(rows: list[dict]) -> list[dict]:
    """One row per distinct book, oldest first — the book clock, not the scan."""
    out, seen = [], None
    for r in rows or []:
        b = (r.get("meta") or {}).get("book_asof")
        if seen is None or b != seen:
            out.append(r)
            seen = b
    return out


def walls_ladder(row: dict, sd, rows: Optional[list[dict]] = None,
                 now: Optional[datetime] = None, ruler_spot=None,
                 ruler_name: Optional[str] = None) -> Optional[dict]:
    """Laddered standing walls, up to WALLS_PER_SIDE a side, NEAREST first —
    walls.call[0]/put[0] are the old gwc/gwp (same clustering rule the ladder
    used: gw_vocab.cluster_walls on the measured net_by_strike surface).
    NEAREST is the whole ordering: [0] is what price meets first, never
    "the strongest" — see `_wall_ages` and the *_heaviest_wall_behind_the_ladder
    key below.
    `gex` = the cluster's share of total book |γ| in percent — scale-free, so
    the model can weigh wall against wall without unit-opaque raw gamma. The
    denominator is the FULL net_by_strike surface, not the surviving clusters:
    a cluster-only total shifts scan-to-scan as strikes cross the 25%
    concentration floor, so "the wall's share rose" could be pure denominator
    churn (SE review 08-02).

    sr-7 ONE-FRAME RULE. `ruler_spot` decides BOTH which side a cluster is filed
    under and how far away it is reported to be. The first draft of sr-7 moved
    only the distance to the book's spot and left clustering and the side filter
    on the live spot, which put 52 of 10,101 recorded wall entries under a side
    their own sigma contradicted — 2026-08-05 11:58 shipped
    walls.call[0] = {strike: 1400.0, sigma: -0.04} while the doctrine told the
    model call[0] is the first thing price meets going UP. Whichever frame is
    the right one to argue about, a single wall entry must not be measured in
    two of them."""
    spot = ruler_spot if ruler_spot is not None else row.get("spot")
    nbs = (row.get("gex_views") or {}).get("net_by_strike")
    if not nbs or not isinstance(spot, (int, float)):
        return None
    try:
        clusters = _gw.cluster_walls(nbs, spot)
    except (TypeError, ValueError):
        return None
    if not clusters:
        return None
    tot = 0.0
    for pair in nbs:
        try:
            g = _fin(pair[1])
        except (TypeError, IndexError, KeyError):
            continue
        if g is not None:
            tot += abs(g)
    if not math.isfinite(tot) or tot <= 0:
        return None

    def entry(c, age=None):
        # `cluster_share_of_book_gamma_pp`, NOT `share_of_book_gamma_pp`. The
        # two were the same name carrying different quantities: the magnet's is
        # ONE STRIKE's share of the window's mass surface, this one is a
        # CLUSTER's share of the whole signed surface. Different numerator,
        # different denominator, one name.
        #
        # Measured over 167 replayed scenes, 136 strikes appeared in both blocks
        # and disagreed by a median 3.45pp — 38% relative — with 69% off by more
        # than 2pp and a worst case of 23.14 against 82.1 for strike 1600 in the
        # same scene. Handed both, a model has no way to reconcile them, and in
        # trial it did what anyone would: invented a "live scan versus last
        # night's close" distinction that does not exist, to explain a
        # contradiction that is ours rather than the market's.
        #
        # sr-7's rule was that a leaf must survive being read alone. Two leaves
        # sharing a name is the sharpest possible violation of it.
        e = {"strike": c["peak"], "sigma": sd(c["peak"]),
             "cluster_share_of_book_gamma_pp": round(c["strength"] / tot * 100.0, 1)}
        if age is not None:
            mins, exact = age
            e["unchanged_for_min" if exact
              else "unchanged_for_at_least_min"] = mins
        return {k: v for k, v in e.items() if v is not None}

    # selection is UNCHANGED from sr-2 on purpose: a cluster must match the
    # side by gamma sign AND sit on that side of spot. Only the ageing and the
    # heaviest-behind key are new — widening the filter would quietly redefine
    # what a wall is, which is not a documentation fix.
    # sr-8: `ordered_by` was one frozen string on every scan. The ordering is
    # load-bearing — walls[0] is what price MEETS FIRST, never "the strongest",
    # and the 08-06 incident below is what happens when that is misread — so
    # the rule stays stated, in the doctrine, where a standing rule costs a
    # tenth as much and has room to say why it matters.
    out = {} if ruler_name is None else {"sigma_measured_from": ruler_name}
    for key, pool, near in (
            ("call", [c for c in clusters
                      if c["side"] == "call" and c["peak"] > spot],
             lambda c: c["peak"]),
            ("put", [c for c in clusters
                     if c["side"] == "put" and c["peak"] < spot],
             lambda c: -c["peak"])):
        if not pool:
            # sr-5: measured-empty is a FINDING, not an absence. The board was
            # measured (clusters exist — we are in the successful branch) and
            # this side holds nothing between price and open air; deleting the
            # key made that indistinguishable from a torn feed, and on this
            # name price in the clear is often the loudest fact in the scene.
            # A true sensor failure still returns None above — unchanged.
            out[key + "_side_has_no_wall"] = True
            continue
        ladder = sorted(pool, key=near)[:WALLS_PER_SIDE]
        ages = _wall_ages(rows, key, [c["peak"] for c in ladder], now)  # noqa: E501
        out[key] = [entry(c, ages.get(c["peak"])) for c in ladder]
        # sr-3: the ladder is ordered by DISTANCE, so the heaviest cluster on a
        # side can sit third and never ship at all. Measured 2026-08-06 15:59:
        # walls.put shipped 1250 (gex 6.8) and 1225 while 1150 — the heaviest
        # put cluster on the board — was cut for being further away, under a
        # doctrine that called walls.put[0] "the strongest". The ladder keeps
        # its job (what price meets FIRST); the heaviest is named separately
        # when it is not already in it, so neither fact can hide the other.
        top = max(pool, key=lambda c: c["strength"])
        if top["peak"] not in {c["peak"] for c in ladder}:
            out[key + "_heaviest_wall_behind_the_ladder"] = entry(top)
    return out


def structure_block(row: dict, ruler_spot) -> Optional[dict]:
    """obs-4: WHERE THE BOARD'S GAMMA SITS — bands, air, and the split above
    and below price. Same surface and same cluster rule as walls_ladder
    (gw_vocab.cluster_walls on net_by_strike, measured from the book's spot);
    what is new is the SPAN of a band and the emptiness between price and the
    nearest wall, neither of which the two-rung ladder carries.

    Measured before it was built (2026-09-02, 23 sessions, 1,946 distinct
    books): price spends no more time inside a band than inside a random band
    of the same width (34.7% vs 28.7% expected, t=+1.6, 8 of 17 days above),
    moves toward the nearest band no more often than toward a random one
    (61.1% vs 59.7%, z=+0.46), and a dominant top strike holds no better than
    an imaginary line at the same distance (hold 89% vs 92%, n=9, shuffle
    p=1.0). So this block says where the weight is and nothing about what
    price does near it — a band is not a magnet, and a heavy wall is heavy,
    not overpowering. `times_next` was deliberately NOT added: it duplicates
    magnet.top_strike_lead_pp on a different surface, and one fact must not
    wear two names."""
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    nbs = gv.get("net_by_strike")
    ruler = _fin(ruler_spot)
    if not nbs or ruler is None:
        return None
    pts = []
    for pair in nbs:
        try:
            k, g = _fin(pair[0]), _fin(pair[1])
        except (TypeError, IndexError, KeyError):
            continue
        if k is not None and g is not None:
            pts.append((k, g))
    pts.sort()
    tot = sum(abs(g) for _, g in pts)
    if not pts or not math.isfinite(tot) or tot <= 0:
        return None
    try:
        clusters = _gw.cluster_walls(nbs, ruler)
        step = _gw.infer_step([k for k, _ in pts])
    except (TypeError, ValueError):
        return None
    out: dict = {}
    bands = []
    for c in clusters:
        if c["hi"] <= c["lo"]:
            continue                       # one strike is a wall, not a band
        side = ("above" if c["lo"] > ruler else
                "below" if c["hi"] < ruler else "straddling price")
        near = (0.0 if side == "straddling price"
                else min(abs(c["lo"] - ruler), abs(c["hi"] - ruler)))
        bands.append((near, {"low": c["lo"], "high": c["hi"],
                             "share_of_book_gamma_pp": round(c["strength"] / tot * 100.0, 1),
                             "side": side}))
    bands.sort(key=lambda x: x[0])
    if bands:
        out["bands"] = [b for _, b in bands[:3]]
    strongest = max(abs(g) for _, g in pts)
    air = []
    for side, pool in (("above", [c for c in clusters if c["lo"] > ruler]),
                       ("below", [c for c in clusters if c["hi"] < ruler])):
        if not pool:
            continue
        c = min(pool, key=lambda c: abs(c["peak"] - ruler))
        lo_e, hi_e = (ruler, c["lo"]) if side == "above" else (c["hi"], ruler)
        if not step or (hi_e - lo_e) <= 1.5 * step:
            continue
        between = sum(abs(g) for k, g in pts if lo_e < k < hi_e)
        if between < 0.15 * strongest:
            air.append({"side": side, "from": round(lo_e, 2), "to": round(hi_e, 2)})
    if air:
        out["air"] = air
    above = sum(abs(g) for k, g in pts if k > ruler)
    out["weight_above_spot_pp"] = round(above / tot * 100.0, 1)
    return out or None


def _wall_ages(rows: Optional[list[dict]], side: str, strikes: list[float],
               now: Optional[datetime]) -> dict:
    """How long each laddered wall has held, in minutes — measured on the SAME
    cluster rule build_scene ships, walking today's rows back until that side's
    ladder no longer contains the strike.

    This exists because `frozen_fields` probes the DIARY's scalar call_wall /
    put_wall while the scene ships walls re-derived by cluster_walls. On
    2026-08-06 15:59 that mismatch had the payload warning "put wall unchanged
    371m" about 1200 — a strike that is not a cluster at all and appears
    NOWHERE in the scene — while walls.put[0]=1250, sitting 0.15σ under price,
    carried no staleness word of any kind. A guard pointed at a number the
    model cannot see is worse than no guard: it spends the model's trust.

    Bounded at _WALL_AGE_LOOKBACK rows so a long session cannot make the read
    quadratic. Returns {} on any missing input — absent, never a guessed 0."""
    if not rows or now is None or not strikes:
        return {}
    want = set(strikes)
    seen: dict = {}
    oldest = None
    for r in reversed(rows[:-1][-_WALL_AGE_LOOKBACK:]):
        # each historical row is clustered in ITS OWN book frame, matching what
        # walls_ladder did for the newest row. Walking back in a different frame
        # would make a wall look like it moved when only the ruler did.
        sp = _fin((r.get("meta") or {}).get("chain_spot"))
        if sp is None:
            sp = r.get("spot")
        nbs = (r.get("gex_views") or {}).get("net_by_strike")
        t = _ts(r)
        if not nbs or not isinstance(sp, (int, float)) or t is None:
            break                      # a hole is not evidence of holding
        try:
            cl = _gw.cluster_walls(nbs, sp)
        except (TypeError, ValueError):
            break
        oldest = t
        pool = [c for c in cl if c["side"] == side
                and (c["peak"] > sp if side == "call" else c["peak"] < sp)]
        held = {c["peak"] for c in sorted(
            pool, key=(lambda c: c["peak"]) if side == "call" else (lambda c: -c["peak"])
        )[:WALLS_PER_SIDE]}
        gone = want - held
        for k in gone:
            seen.setdefault(k, (max(0, round((now - t).total_seconds() / 60.0)), True))
        want -= gone
        if not want:
            break
    # A wall still standing at the far end of the window is CENSORED, not
    # unmeasured: we know it held at least that long and no more than that is
    # knowable from here. Reporting it as a plain age would understate the
    # staleness of the very field the guard exists to flag, and dropping it
    # would tell the model "not measured" about something we did measure.
    if want and oldest is not None:
        floor = max(0, round((now - oldest).total_seconds() / 60.0))
        for k in want:
            seen[k] = (floor, False)
    return seen


ABNORMAL_DAY_SIGMA = 1.5    # a day move beyond this many σ is abnormal FOR
                            # THIS NAME — a fixed 8% is ~1σ on a 10%-σ stock
                            # and fired on 56% of recorded scans (adversarial
                            # audit 08-02): "extreme cases only" must be
                            # measured on the stock's own ruler
ABNORMAL_PCT_FLOOR = 12.0   # σ unavailable → absolute fallback, still rare


def history_flags(row: dict, rows: list[dict], vs_prior_pct: Optional[float],
                  ran_30m: Optional[float]) -> Optional[dict]:
    """The under-pull guard: cheap, unmissable payload flags that tap the
    model on the shoulder to reach for the history tool / outside world.
    Omitted entirely when there is nothing to say — and calibrated so
    "abnormal" stays a genuine exception, never a majority state (a flag
    that is usually on trains the model to ignore it, and it licenses the
    outside-world reach far too often)."""
    out = {}
    spot = _fin(row.get("spot"))
    prior = [s for r in rows[:-1] if (s := _fin(r.get("spot"))) is not None]
    if spot is not None and len(prior) >= 30 and \
            (spot > max(prior) or spot < min(prior)):
        out["price_at_level_unseen_earlier_today"] = True
    sig, vs = _fin(row.get("sigma")), _fin(vs_prior_pct)
    sigma_pct = (sig / spot * 100.0) if (sig and spot) else None
    day_bar = (ABNORMAL_DAY_SIGMA * sigma_pct if sigma_pct
               else ABNORMAL_PCT_FLOOR)
    if (vs is not None and abs(vs) >= day_bar) or \
            ((r30 := _fin(ran_30m)) is not None and abs(r30) >= 0.5):
        out["tape_abnormal_vs_own_history"] = True
    return out or None


OI_MIN_STRIKES_COMPARED = 5   # below this the "unchanged" claim is not worth
                              # making — say nothing rather than say it thinly


def _oi_unchanged_today(rows: list[dict]) -> tuple:
    """Did open interest actually hold still across today's scans?

    sr-7. The scene now states outright that every structural number rests on
    last night's OI, so that claim gets re-measured on every scan instead of
    resting on a one-off audit. Compares today's EARLIEST recorded oi_by_strike
    surface against the newest one.

    Two traps, both hit on live data while writing this:
      * the first row of a session often carries no surface at all (warm-up),
        so the comparison starts at the first row that HAS one, not at rows[0];
      * the strike window is spot-relative and walks with price, so the two
        surfaces list different strikes. Comparing the raw lists answers a
        question about the telescope. Only the INTERSECTION is compared, which
        is the question actually being asked: did the OI on a strike change?

    Returns (unchanged, strikes_compared); (None, None) when unanswerable —
    absence is never a "yes"."""
    def surf(r):
        out = {}
        for pair in ((r.get("gex_views") or {}).get("oi_by_strike") or []):
            try:
                k, v = _fin(pair[0]), _fin(pair[1])
            except (TypeError, IndexError, KeyError):
                continue
            if k is not None and v is not None:
                out[k] = v
        return out

    last = surf(rows[-1]) if rows else {}
    first = next((f for f in (surf(r) for r in rows[:-1]) if f), {})
    common = set(first) & set(last)
    if len(common) < OI_MIN_STRIKES_COMPARED:
        return None, None
    return all(first[k] == last[k] for k in common), len(common)


def _prior_session_date(today: str) -> Optional[str]:
    """The most recent session BEFORE today that this tape actually recorded —
    the session whose close the standing open interest was struck at. Read off
    the diary rather than off a weekday calendar, so a holiday or a dark day
    can never be handed over as a date that was never traded."""
    days = sorted(pp.stem for pp in _diary_dir().glob("2026-*.jsonl")
                  if pp.stem < today)
    return days[-1] if days else None


def minute_bars(day: str) -> list[dict]:
    """The sidecar's completed minute bars for `day`, or [] — never zeros."""
    if sndk_bars is None:
        return []
    try:
        return sndk_bars.read_bars(day)
    except Exception:
        return []


def _file_key(p: Optional[Path]):
    try:
        st = p.stat()
        return (str(p), st.st_mtime_ns, st.st_size)
    except (OSError, AttributeError):
        return (str(p), None)


_EXTREMES_CACHE: dict = {}


def _day_extremes(day: str, diary_path: Path) -> Optional[tuple]:
    """(low, high, witness) for one CLOSED session. The minute-bar file wins
    when it holds most of the session (PRIOR_BARS_MIN of 390 — a half-filled
    day would understate the range); otherwise the 2-minute scans, forced rows
    skipped. Cached on both files' (path, mtime, size): a closed day's answer
    never changes, so five days cost five stats per scan, not five parses."""
    bars_path = sndk_bars.bars_path(day) if sndk_bars else None
    key = (_file_key(diary_path), _file_key(bars_path))
    hit = _EXTREMES_CACHE.get(day)
    if hit and hit[0] == key:
        return hit[1]
    val = None
    bars = minute_bars(day)
    if len(bars) >= PRIOR_BARS_MIN:
        val = (min(b["low"] for b in bars), max(b["high"] for b in bars),
               "1_minute_bars")
    else:
        spots = []
        for r in _read_jsonl(diary_path):
            if (r.get("meta") or {}).get("forced"):
                continue
            if r.get("ticker") not in (None, "SNDK"):
                continue
            sp = _fin(r.get("spot"))
            if sp is not None:
                spots.append(sp)
        if spots:
            val = (min(spots), max(spots), "scans_every_2_min")
    _EXTREMES_CACHE[day] = (key, val)
    return val


def _prior_sessions_range(today: str) -> Optional[dict]:
    """The last PRIOR_RANGE_SESSIONS closed sessions' low and high, each day
    from its minute bars when the file is full, else from its scans, with
    `measured_from` saying which ("mixed" when the days differ). Today's own
    file is never opened."""
    days = sorted(p for p in _diary_dir().glob(
        "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")
        if p.stem < today)[-PRIOR_RANGE_SESSIONS:]
    found = [(p.stem, ex) for p in days if (ex := _day_extremes(p.stem, p))]
    if not found:
        return None
    srcs = {ex[2] for _, ex in found}
    return {"sessions": len(found), "from": found[0][0], "to": found[-1][0],
            "low": round(min(ex[0] for _, ex in found), 2),
            "high": round(max(ex[1] for _, ex in found), 2),
            "measured_from": "mixed" if len(srcs) > 1 else srcs.pop()}


def _range_points(rows: list[dict], bars: Optional[list[dict]]) -> tuple:
    """(time, low, high) triples in time order, and which witness made them.
    From the sidecar a point is a minute's wick; from the diary it is one spot
    twice. The diary spots newer than the last completed bar ride along, so the
    live price is never older than the bars."""
    pts = []
    for b in bars or []:
        t = _parse_ts(b.get("ts"))
        lo, hi = _fin(b.get("low")), _fin(b.get("high"))
        if t is not None and lo is not None and hi is not None:
            pts.append((t, lo, hi))
    witness = "1_minute_bars" if pts else "scans_every_2_min"
    last_bar = max((p[0] for p in pts), default=None)
    for r in rows:
        sp, t = _fin(r.get("spot")), _ts(r)
        if sp is not None and t is not None and (last_bar is None or t > last_bar):
            pts.append((t, sp, sp))
    pts.sort(key=lambda p: p[0])
    return pts, witness


def ranges_block(rows: list[dict], now: datetime, sig: Optional[float],
                 today: Optional[str] = None,
                 bars: Optional[list[dict]] = None) -> Optional[dict]:
    """obs-4: THE DAY'S BOXES — every number measured, no verdict anywhere.

    The opening box is the first OPENING_RANGE_MIN minutes of the tape, frozen
    once formed. A frozen box BREAKS when a minute's wick — or a scan, when
    there are no bars — sits beyond it by more than RANGE_BREAK_SIGMA (about
    $3 here; the 2-minute wobble is $2), and the break is recorded with its
    clock and direction. A break starts a new box, which
    forms over BOX_FORM_MIN minutes and freezes in its turn: that is the box in
    force, and the broken one is history. The opening box stays in the block
    only so the day's start can be named. The prior sessions' range rides on
    its own clock beside all of this — it can hold while today's box breaks,
    and the block states both without joining them.

    Windows are anchored on the day's FIRST ROW, not on 09:30, so a late open
    or a holiday session measures the same way and the fixtures (which sit
    wherever their clock sits) reach every leaf."""
    pts, measured_from = _range_points(rows, bars)
    if not pts:
        return None

    def hhmm(t):
        return t.astimezone(_ET).strftime("%H:%M")

    depth = (RANGE_BREAK_SIGMA * sig) if sig else None
    t_first = pts[0][0]
    form_start = t_first
    lo, hi = pts[0][1], pts[0][2]
    frozen: Optional[dict] = None
    opening: Optional[dict] = None
    breaks: list[dict] = []
    for t, p_lo, p_hi in pts:
        if frozen is None:
            lo, hi = min(lo, p_lo), max(hi, p_hi)
            span = OPENING_RANGE_MIN if opening is None else BOX_FORM_MIN
            if (t - form_start) >= timedelta(minutes=span):
                frozen = {"low": lo, "high": hi,
                          "formed_over": f"{hhmm(form_start)}-{hhmm(t)}"}
                if opening is None:
                    opening = dict(frozen)
            continue
        if depth is not None and (p_hi > frozen["high"] + depth
                                  or p_lo < frozen["low"] - depth):
            up = (p_hi - frozen["high"]) >= (frozen["low"] - p_lo)
            breaks.append({"at": hhmm(t), "went": "up" if up else "down",
                           "box_low": round(frozen["low"], 2),
                           "box_high": round(frozen["high"], 2)})
            frozen = None
            form_start = t
            lo, hi = p_lo, p_hi

    # the live price is the newest diary spot — never a wick pretending to be
    # where price sits
    live = [(t, sp) for r in rows
            if (t := _ts(r)) is not None and (sp := _fin(r.get("spot"))) is not None]
    spot_now = max(live)[1] if live else pts[-1][2]
    out: dict = {"measured_from": measured_from}
    if opening is not None:
        op = {"low": round(opening["low"], 2), "high": round(opening["high"], 2),
              "formed_over": opening["formed_over"]}
        if breaks:
            op["status"] = f"broke {breaks[0]['went']} at {breaks[0]['at']}"
        else:
            op["status"] = "held so far"
        out["opening"] = op
    else:
        out["opening"] = {"still_forming": True, "since": hhmm(t_first),
                          "low": round(lo, 2), "high": round(hi, 2)}
    if frozen is not None:
        inf = {"low": round(frozen["low"], 2), "high": round(frozen["high"], 2),
               "formed_over": frozen["formed_over"],
               "is_the_opening_box": not breaks}
        inf["live_spot_is"] = ("inside" if frozen["low"] <= spot_now <= frozen["high"]
                               else "just above" if spot_now > frozen["high"]
                               else "just below")
        out["in_force"] = inf
    else:
        inf = {"still_forming": True, "since": hhmm(form_start),
               "low": round(lo, 2), "high": round(hi, 2)}
        if breaks:
            inf["replaced_a_box_broken_at"] = breaks[-1]["at"]
        out["in_force"] = inf
    if breaks:
        out["breaks_today"] = {"count": len(breaks),
                               "breaks": breaks[-RANGE_BREAKS_MAX:]}
    ps = _prior_sessions_range(today or now.strftime("%Y-%m-%d"))
    if ps:
        ps["live_spot_is"] = ("inside" if ps["low"] <= spot_now <= ps["high"]
                              else "above" if spot_now > ps["high"] else "below")
        d_lo, d_hi = min(p[1] for p in pts), max(p[2] for p in pts)
        beyond = ("both" if d_hi > ps["high"] and d_lo < ps["low"]
                  else "above" if d_hi > ps["high"]
                  else "below" if d_lo < ps["low"] else None)
        if beyond:
            ps["today_traded_beyond_it"] = beyond
        if opening is not None:
            o_lo, o_hi = opening["low"], opening["high"]
            ps["todays_opening_box_is"] = (
                "inside" if o_lo >= ps["low"] and o_hi <= ps["high"]
                else "above" if o_lo > ps["high"]
                else "below" if o_hi < ps["low"] else "straddling an edge")
        out["prior_sessions"] = ps
    return out or None


def build_scene(row: dict, band: dict, frozen: list,
                rows: list[dict], now: datetime,
                since_last_read: Optional[dict] = None) -> dict:
    """The model's view of the world — sr-2: an UNBIASED snapshot, no verdict.

    The old arrow_already_decided block is gone (a pre-baked conclusion
    anchors the read), and with obs-3 the deterministic arrow behind it was
    deleted outright — the exclusion is structural now. Fields are grouped by force (scale /
    regime / magnet / momentum / dealer_positioning / walls), and everything the
    constancy audit found frozen across 756 rows stays omitted rather than
    nulled — 'absent' and 'zero' must never look alike, and a field that
    cannot vary cannot inform. Same rule for every new block: not cleanly
    computed -> absent.

    sr-7 (2026-08-30) is a NAMING and PROVENANCE pass, not a new measurement.
    Two findings drove it:

    (1) Nothing in the scene said WHEN any number was measured or WHAT it was
        measured from. Zero of 53 leaves carried a source tag or a timestamp,
        and the scene re-priced one overnight OI snapshot on every one of ~190
        daily scans against a live quote while presenting the result in the
        present tense.
        `data_sources` carries the three clocks (scan / quote / book) and
        `freshness_rules` DROPS a block whose source has aged out rather than
        trusting the reader to notice a label. The block-to-source map itself
        is BUILT_FROM, a module constant the gate reads and sr-8 stopped
        shipping — 269 bytes of static map on every scan, which the doctrine
        now states once in prose.

    (2) Distances lied by a spot. Every sigma distance divided a LIVE spot into
        a book measured at a different spot — median $2.13 apart on cached
        rows, $41.99 at worst. Book-derived distances are now measured from
        `spot_when_book_was_measured`, the blocks that use that ruler say so in
        `sigma_measured_from`, and `price.live_minus_book_spot_sigma` is the
        single number that converts one frame to the other.

    Leaf names are the deliverable of this pass: a name must survive being read
    alone, out of context, by someone who has never seen the rest of the file.
    `gex` became `share_of_book_gamma_pp`; `net` became `net_delta_bn`; `now`
    became `live_spot`; `window` became `window_between_books`. Nothing here is
    a new number — it is the same book, finally saying what it is."""
    spot = _fin(row.get("spot"))
    sig = _fin(row.get("sigma")) or 0
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    # THE RULER. Every structural number below was computed against the spot the
    # CHAIN saw, not the spot the quote feed is showing now. Dividing a live
    # spot into that book is a category error that was silently costing up to
    # 0.9 sigma of distance on a cached row, so the book's own spot is the
    # denominator wherever the numerator came out of the book.
    #
    # Two guards, both learned the hard way from the book-age fallback next door.
    # (1) SANITY. chain_spot is a new external dependency and _fin happily
    #     returns 0.0 or a negative; a zero here would have shipped
    #     top_strike_sigma 33.29 and walls at +32.54 with nothing complaining.
    #     Recorded range over 3,392 August rows is 1170-1520, so a value that is
    #     not positive and within RULER_MAX_DRIFT of the live spot is refused.
    # (2) HONESTY. The fallback is announced. sigma_measured_from used to say
    #     "spot_when_book_was_measured" even when there was no chain_spot to
    #     measure from — the one place an sr-7 provenance tag could lie, and the
    #     doctrine would then tell the model to convert with a field the scene
    #     does not contain.
    book_spot = _fin(meta.get("chain_spot"))
    if book_spot is not None and spot is not None and (
            book_spot <= 0 or abs(book_spot - spot) / spot > RULER_MAX_DRIFT):
        book_spot = None
    ruler_spot = book_spot if book_spot is not None else spot
    # sr-8 INVERTS the provenance tag. sr-7 stamped `sigma_measured_from` on
    # three blocks on every scan, and it read "spot_when_book_was_measured" on
    # 4,149 of 4,149 — a tag that says the same word every time is not telling
    # the reader anything, it is being read past. The RULE now lives in the
    # doctrine ("book-derived distances divide the book's spot") and the tag
    # ships only when that rule does NOT hold, which is the case it was written
    # for: the fallback, where there is no chain_spot and the doctrine's
    # conversion field does not exist either. A guard that only speaks when
    # something is wrong is a guard that gets read.
    ruler_name = (None if book_spot is not None
                  else "live_spot_because_chain_spot_was_absent")

    def sd(v):
        """Sigma distance from the spot the BOOK was measured at."""
        v = _fin(v)
        if v is None or not sig or ruler_spot is None:
            return None
        return round((v - ruler_spot) / sig, 2)

    def sd_live(v):
        """Sigma distance from the LIVE spot — for live-tape levels only."""
        v = _fin(v)
        if v is None or not sig or spot is None:
            return None
        return round((v - spot) / sig, 2)

    def prune(d):
        # omit-never-null INSIDE the blocks too: an early-session
        # moved_last_30min or a missing prior_close must vanish, not read as
        # null — the doctrine promises "missing = not measured", and a null
        # keep-field would break that promise (fidelity audit #5, 08-02)
        return {k: v for k, v in d.items() if v is not None}

    # the day's own path, which the old design never handed over at all
    path = [s for r in rows if (s := _fin(r.get("spot"))) is not None]
    # obs-5: the minute-bar sidecar, read once per scene. Its wicks are the
    # session's true extremes; the 2-minute spots are the fallback and the
    # block says which it used.
    bars = minute_bars(now.strftime("%Y-%m-%d"))
    bars_now = [b for b in bars if (bt := _parse_ts(b.get("ts"))) is not None and bt <= now]
    # gwc/gwp migrated to walls.call[0]/put[0] — same clustering rule, told
    # as a ladder with the second wall behind the first. named_levels went the
    # same way in sr-3: measured over all 923 recorded rows carrying the family,
    # profile_ladder.hvl == gamma_flip on 923/923 and (ct-flip)/sigma,
    # (pt-flip)/sigma take exactly ONE value, (+0.25, -0.25) — so ct/hvl/pt were
    # flip_block's own three numbers a second time, and lowest_named_level was
    # min() of that duplicate. Four scene keys, one measurement; the model cited
    # them 0 times in 61 sr-2 readings. flip_block is now the sole owner.
    pc = _fin(row.get("prior_close"))
    vs_prior = (round((spot / pc - 1) * 100, 2)
                if pc and spot else None)
    # ONE ruler for every 30-min claim: the timestamp-true window with_path
    # measured. The old count-based path[-15] silently shortened across scan
    # gaps — the exact lie with_path's own docstring pre-registers, and it
    # disagreed with the honest window on 20/700 recorded scans, once by
    # 0.81 sigma (adversarial audit 08-02).
    ran_30m = _fin(row.get("_ran_30m_sigma"))
    moved_30m = round(ran_30m, 2) if ran_30m is not None else None

    # ------------------------------------------------------------ the clocks
    # sr-7, corrected by sr-8: TWO of them, not three. `scan_taken_at` is when
    # this row was written; `options_book.measured_at` is when the CHAIN was
    # pulled, which on half of all scans is a disk cache from the previous
    # scan. (sr-7's third clock, `price_quote`, was `scan_taken_at` wearing
    # another name — see the deletion below.) Measured
    # 2026-08-28: 188 scans, 94 distinct books — so the book's real cadence is
    # ~4 minutes while the scan cadence is ~2, and every age taken off the scan
    # clock understated the book by the difference.
    t_row = _ts(row)
    b_asof = _book_asof(row)
    prev_row = rows[-2] if len(rows) >= 2 else None
    scan_stamps = [t for r in rows if (t := _ts(r)) is not None]
    books = _distinct_books(rows)

    # the LIVE_TAPE age is the SCAN's age, and calling it anything else is how
    # sr-7 ended up shipping a quote clock that was this number twice.
    scan_age = _age_min(t_row, now)
    book_age = _age_min(b_asof, now)

    # sr-8: THE THIRD CLOCK WAS THE FIRST ONE WEARING A HAT. sr-7 shipped
    # `price_quote {quoted_at, age_min, feed}` as one of "three separate clocks
    # that disagree on purpose". Measured over 4,149 recorded scans they did not
    # disagree at all: `quoted_at` equalled `scan_taken_at` on 4,149/4,149 and
    # `age_min` was 0.0 on 4,149/4,149 — necessarily, since both came off the
    # row's own `ts`.
    #
    # sr-7 had already diagnosed this and shipped the block anyway. The comment
    # above MAX_BOOK_AGE_MIN rejects a quote ceiling in these words: "the diary
    # carries no quote timestamp at all (meta has spot_source and no clock)".
    # There are two clocks on this row, the scan and the book. Telling the model
    # there are three invites it to read a 0.0-minute quote age as evidence the
    # price is fresh, when it is only evidence that the row timestamped itself.
    #
    # `spot_source` survives as a single leaf because it is the one part of that
    # block which was never the scan clock: it names WHERE the price came from,
    # and a fallback source would change it. Constant on the recorded tape,
    # which by sr-8's own rule argues for the doctrine — but the desktop payload
    # tab reads it out of the scene, and a provenance tag with a real second
    # value is not the same kind of thing as a frozen literal like `instrument`.
    book = {"measured_at": b_asof.isoformat() if b_asof else None,
            "age_min": book_age,
            "served_from": meta.get("book_source"),
            "is_repeat_of_previous_scan": _book_is_repeat(row, prev_row),
            "cache_age_s": _fin(meta.get("cache_age_s")),
            "distinct_books_so_far_today": len(books) or None,
            "refresh_interval_min": _median_gap_min(books)}
    if meta.get("book_asof") is None:
        # honesty about the fallback: this age is the SCAN's, and a scan is
        # never older than the book it carries, so the number can only be too
        # small. Say which clock produced it rather than passing it off.
        book["measured_at_is_fallback_scan_clock"] = True
    oi_same, oi_n = _oi_unchanged_today(rows)
    # sr-8: `snapshot_is` and `updates_intraday` were a fixed string and a
    # fixed False describing how the FEED works, not what it measured today.
    # The doctrine makes that point at length and in the place it matters (the
    # present-tense ban). What stays here is what varies and can be checked:
    # which session the snapshot was struck at, and whether it has in fact held
    # still across today's scans.
    oi = {"prior_session_date": _prior_session_date(now.strftime("%Y-%m-%d")),
          "measured_unchanged_so_far_today": oi_same,
          "strikes_compared_today": oi_n}

    # pruned like every other block: on the first scan of a session there is no
    # gap to measure yet, and a null `scan_interval_min` would be this block
    # breaking the omit-never-null rule in the middle of explaining it.
    data_sources = prune({
        "scan_taken_at": t_row.isoformat() if t_row else None,
        "scans_so_far_today": len(rows),
        "scan_interval_min": _median_gap_min(scan_stamps),
        "spot_feed": meta.get("spot_source"),
        "options_book": prune(book) or None,
        "open_interest": prune(oi) or None,
        # obs-5: the sidecar's own clock — how many completed minutes it holds
        # and how old the newest is. Absent means no bars file for the day.
        "minute_bars": (prune({
            "bars_so_far_today": len(bars_now),
            "last_bar_at": bars_now[-1]["ts"],
            "age_min": _age_min(_parse_ts(bars_now[-1]["ts"]), now)})
            if bars_now else None),
    })

    # sr-4: the day-scoped calendar. Until now the model read a Monday 4-dte
    # book with the same eyes as expiry Friday — no date, no dte, no idea how
    # much session remained for a 30-minute call to resolve in — on a name
    # whose whole dealer story cycles weekly (charm magnitude swells ~5x
    # across one cycle for pure calendar reasons). Every value is already on
    # the row or the wall clock; weekday is deliberately NOT shipped — on the
    # recorded tape weekday and dte are the same number (Mon=4..Fri=0), and
    # one fact must not wear two names (the flip-band lesson). The 16:00
    # close is the standing RTH assumption — a half-day session will overstate
    # minutes_to_close (the no-pulse blind spot, still open, owns that).
    #
    # sr-7 moved book_age_min OUT of here. This block is the SESSION clock and
    # nothing else; how old a measurement is belongs with the measurement, in
    # data_sources, where all three clocks can be read against each other.
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    # sr-8 drops `minutes_since_open` and keeps `minutes_to_close`. The two sum
    # to 389 on 4,148 of 4,149 recorded scans, so one is the other — and the
    # question the reader is actually asked is whether a 30-minute call has 30
    # minutes of tape left to resolve in, which is the one that was kept. (The
    # sum is not quite a constant, so the kept field is the measured one rather
    # than a subtraction: both floor, and minutes_to_close clamps at zero, which
    # is the honest answer after the close and not a negative.)
    clock = {"minutes_to_close": max(0, int((close_t - now).total_seconds() // 60)),
             "session_date": now.strftime("%Y-%m-%d")}
    fd = _fin((row.get("gex_views") or {}).get("front_dte"))
    if fd is not None:
        # front_expiry is the one thing in `clock` that is NOT the wall clock —
        # dte comes off the book and the date off meta.expiries. It carries its
        # own declaration rather than dragging the whole session calendar under
        # the book's ceiling: minutes_to_close stays true when the book is dead,
        # and an expiry date does not go stale in six minutes.
        fe = {"days_to_expiry": int(fd)}
        # meta.expiries entries are {"date","dte"} dicts on live rows and were
        # bare strings in early fixtures — accept both, coerce neither.
        exps = meta.get("expiries")
        if isinstance(exps, list):
            ds = [x for x in exps if isinstance(x, str)]
            ds += [x["date"] for x in exps
                   if isinstance(x, dict) and isinstance(x.get("date"), str)]
            nxt = next((e for e in sorted(ds) if e >= clock["session_date"]), None)
            if nxt:
                fe["expiry_date"] = nxt
        clock["front_expiry"] = fe

    # scale — the rulers: sigma + the asymmetric expected move (rebuilt IV smile)
    #
    # sr-8 drops `move_30min_sigma_distribution`. The SPREAD itself stands —
    # sr-5 measured it because a single "typical move" number taught a ceiling
    # (all 33 magnitudes ever emitted sat inside 0.06-0.20 while a fifth of real
    # windows exceed 0.20) and that lesson is not in question. What changed is
    # WHERE it rides: five frozen literals from one 8-session post-earnings
    # study, identical on every scan, were being paid for at full payload price
    # on every model call while the doctrine already restated all five in prose.
    # (~190 SCANS a day, but the wake gate and DAILY_CALL_CAP mean the MODEL
    # speaks about 19 times — measured: 391 calls over 21 sessions. The scan
    # rate is not the billing rate, and sr-7's comments conflated the two.)
    # The doctrine is byte-identical all day and prompt-cached, so the same
    # sentence costs about a tenth there. A frozen number belongs in the cached
    # half; only what varies earns a place in the scene.
    # sr-8 drops `sigma_pct_of_price`: it is one_sigma_dollars over
    # price.live_spot, both of which ship, and the doctrine states the 8-10%
    # band this name actually exists to convey.
    scale = {"one_sigma_dollars": round(sig, 2) if sig else None}
    skew = row.get("iv_skew") if isinstance(row.get("iv_skew"), dict) else {}
    em = _fin((row.get("range_ruler") or {}).get("em_points"))
    d = _fin(skew.get("down_share"))
    if em is not None and em > 0 and d is not None:
        scale["expected_move_today_asym"] = {
            "up_dollars": round(2 * em * (1 - d), 2),
            "down_dollars": round(2 * em * d, 2),
            "skewed_toward": ("downside" if d >= 0.52 else
                              "upside" if d <= 0.48 else "balanced")}

    regime = prune({"sigma_measured_from": ruler_name,
                    "gamma_sign": row.get("gamma_sign"),
                    "regime_label": row.get("regime")})
    vt = vol_trend(rows, now)
    if vt:
        regime["vol_trend"] = vt
    fb = flip_block(row, sd, ran_30m)
    if fb:
        regime["flip"] = fb
    elif (row.get("gex_views") or {}).get("net_by_strike"):
        # sr-5: the book WAS measured and holds no flip — gamma one-signed at
        # every strike (the recorded case: a net-negative board). That is a
        # regime fact, not a hole; omitting it read as "flip unknown" on 49%
        # of scenes and the model was told absence means "not measured".
        regime["flip"] = {"no_flip_anywhere_on_board": True}
    cb = charm_block(row)
    if cb:
        regime["charm"] = cb

    # price is the ONE block that reads from two clocks at once, so it is the
    # one block whose leaf names carry their own basis.
    # live_minus_book_spot_sigma is the conversion between the two frames and
    # the reason every other distance can stay short: SUBTRACT it from any
    # book-measured sigma to get the distance from where price actually is.
    # (live sigma = (K - live)/sig = book sigma - (live - book)/sig, and this
    # field is that second term. The doctrine said "add" for one afternoon,
    # which walks the wrong way by twice the gap; the name now carries the
    # direction so the verb is not something anyone has to hold in their head.)
    # sr-8 drops `live_minus_book_spot_dollars`. It was the same quantity as
    # the sigma twin below in the other unit, and both spots it is the
    # difference of are right here. Sigma is the frame every distance in this
    # scene is already in, so sigma is the copy that survives.
    price = {"live_spot": spot,
             "spot_when_book_was_measured": book_spot,
             "live_minus_book_spot_sigma": (round((spot - book_spot) / sig, 2)
                                            if spot is not None and book_spot is not None and sig
                                            else None),
             "vs_prior_close_pct": vs_prior,
             # obs-5: the wicks win when the sidecar has them; the live spot is
             # folded in so a print newer than the last completed bar counts
             "session_low": (min([b["low"] for b in bars_now] + path) if bars_now
                             else (min(path) if path else None)),
             "session_high": (max([b["high"] for b in bars_now] + path) if bars_now
                              else (max(path) if path else None)),
             "extremes_from": ("1_minute_bars" if bars_now else
                               ("scans_every_2_min" if path else None)),
             "moved_last_30min_sigma": moved_30m}
    vw = sd_live(row.get("vwap"))
    if vw is not None:
        # sr-8 RENAME, and the reason is measured. As
        # `vwap_dist_sigma_from_live_spot` this field said which SPOT it was
        # measured from and never which way it pointed: 13 of 15 reviewers read
        # its sign backwards (recorded in the phone glance, which sidestepped
        # the trap by rendering the vwap price instead of the ratio). It is
        # (vwap - live spot) / sigma, so POSITIVE means vwap sits ABOVE price,
        # i.e. price is BELOW its average — the opposite feel to
        # moved_last_30min_sigma, which is positive when price ROSE.
        #
        # The fix is the name, because `price.live_minus_book_spot_sigma` sits
        # four lines up doing the same thing — a difference of two prices over
        # sigma — and names its subtraction. One block should not hold two
        # differences, one of which spells out its direction and one of which
        # does not. sr-7's own rule: a leaf must survive being read alone.
        price["vwap_minus_live_spot_sigma"] = vw

    scene = {
        # sr-8: `instrument` was the string "SNDK" on every scan of a reader
        # that has never watched anything else. The doctrine opens on it —
        # "THE INSTRUMENT. This is SNDK, a single stock, not an index" — with
        # the sigma scale and the weekly-expiry cadence that actually matter.
        "data_sources": data_sources,
        "clock": clock,
        "scale": prune(scale),
        "price": prune(price),
        "regime": prune(regime),
        # magnet: evidence only when a book was measured. An empty/absent book
        # must not ship a bare gap claim, and a SINGLE-strike book — maximal
        # dominance — has no runner-up, so the lead is unanswerable and omitted
        # (adversarial audit 08-02).
        #
        # sr-3 drops `is_a_tie`. It was `gap_pp < MAGNET_SEP_PP`, i.e. a
        # hardcoded July constant (5.0pp) shipped to the model as a finished
        # verdict — and it read True on ~95% of August scans, which is the
        # dex_word pattern a third time: a near-constant that sounds like a
        # discovery. The lead itself is the evidence and it is already here;
        # top_strike_lead_vs_own_history says how unusual today's lead is
        # against the tape's own record, which a fixed threshold could not track.
        "magnet": (prune({
            "sigma_measured_from": ruler_name,
            "top_strikes": [{"strike": k, "share_of_book_gamma_pp": v}
                            for k, v in band["top"]],
            "top_strike_lead_pp": band["gap_pp"] if len(band["top"]) >= 2 else None,
            "top_strike_lead_vs_own_history": (
                _pctl_word("magnet_gap_pp", band["gap_pp"], now)
                if len(band["top"]) >= 2 else None),
            "top_strike_sigma": sd(band["top"][0][0])})
            if band["top"] else None),
        "breadth": breadth_block(row, now),
        "momentum": momentum_block(rows, band),
        "dealer_positioning": dealer_positioning_block(rows),
        "walls": walls_ladder(row, sd, rows, now,
                              ruler_spot=ruler_spot, ruler_name=ruler_name),
        # obs-4: where the weight sits — bands, air, the split above/below
        "structure": structure_block(row, ruler_spot),
        "history": history_flags(row, rows, vs_prior, ran_30m),
        # sr-3: wall entries are dropped here. frozen_fields probes the DIARY's
        # scalar call_wall/put_wall; the scene ships walls re-derived by
        # cluster_walls, and on 2026-08-06 those disagreed — the list warned
        # about put wall 1200, a strike absent from the whole scene, while
        # walls.put[0]=1250 went unlabelled. The walls now carry their own
        # `unchanged_for_min`, measured on the numbers the model actually reads.
        # The row's frozen list is untouched: the chart greys diary fields and
        # is right to keep describing them.
        "frozen_do_not_cite": [f"{f['field']} unchanged {f['for_min']}m"
                               for f in frozen
                               if f["field"] not in ("call wall", "put wall")],
    }
    scene = {k: v for k, v in scene.items() if v not in (None, {}, [])}
    scene["freshness_rules"] = _drop_stale_blocks(
        scene, {LIVE_TAPE: scan_age, OPTIONS_BOOK: book_age})
    # WHAT IS UNUSUAL, computed here rather than asked of the model — see
    # session_context. Built AFTER the freshness gate on purpose: a fact
    # pointing into a block that was just deleted for age would be an offer the
    # reader cannot take, so the gate runs first and every surviving pointer
    # resolves by construction. Re-checked anyway, because "by construction" is
    # what the last three bugs all said about themselves.
    ctx = session_context(scene, rows, now) or {}
    # obs-4: the day's boxes — a fact about time, so it rides in `context`
    rb = ranges_block(rows, now, sig, now.strftime("%Y-%m-%d"), bars=bars_now)
    if rb:
        ctx["ranges"] = rb
    if since_last_read:
        # obs-3: the bridge from the model's own last reading — built by the
        # caller, because only read_once knows which row last spent a call.
        ctx["since_last_read"] = since_last_read
    if ctx:
        scene["context"] = ctx
    return scene


def _drop_stale_blocks(scene: dict, ages: dict) -> dict:
    """Enforce the freshness ceilings by DELETING what has aged out, and report
    exactly what was deleted.

    sr-7, and the whole point of the sr-7 provenance work. Shipping an age
    beside a number and hoping the reader discounts it is the design that
    already failed: the doctrine told the model every number was N minutes
    stale, the N was measured off the wrong clock, and no reading ever
    discounted anything. A ceiling that deletes cannot be ignored, and the
    deletion lands in a vocabulary the scene already has — absence means "not
    measured", which is precisely true of an observation too old to stand.

    open_interest carries no ceiling on purpose: it is ~18 hours old BY DESIGN
    and saying so is the fix, not dropping it. WALL_CLOCK never ages."""
    dropped = []
    # sr-8: read the static map, do not ship it. sr-7 sent `built_from` in the
    # payload and therefore had to reconcile it against what the scene actually
    # carried, so the map could never name a block the reader could not see.
    # Nothing reads it now but this loop, and popping a block the scene does not
    # have is already a no-op below — so the reconcile pass went with the field
    # it existed to keep honest.
    built = BUILT_FROM
    for src, names in built.items():
        age, cap = ages.get(src), _MAX_AGE_MIN.get(src)
        if age is None or cap is None or age <= cap:
            continue
        for name in names:
            if "." in name or scene.pop(name, None) is None:
                continue
            dropped.append({"block": name, "source": src,
                            "age_min": age, "max_age_min": cap})
    # A guardrail may only name fields the scene actually ships — the same rule
    # that moved wall staleness onto the walls in sr-3. Without this the model
    # is handed "magnet unchanged 387m, do not cite" about a magnet block that
    # was deleted three lines ago, which is a warning about a fact it cannot see.
    gone = {d["block"] for d in dropped}
    if gone and scene.get("frozen_do_not_cite"):
        kept = [f for f in scene["frozen_do_not_cite"]
                if f.split(" ")[0] not in gone]
        if kept:
            scene["frozen_do_not_cite"] = kept
        else:
            scene.pop("frozen_do_not_cite")
    # sr-8: only `blocks_dropped_this_scan` varies. The rule itself, the
    # ceiling and the present-tense ban were three constants and a fixed
    # six-name list on every scan, all of them already spelled out in the
    # doctrine — which is where a standing rule belongs. What is left here is
    # the one thing that is news: what actually went missing on THIS scan.
    return {"blocks_dropped_this_scan": dropped}


# ---------------------------------------------------------------------------
# one wake
# ---------------------------------------------------------------------------
def read_once(now: Optional[datetime] = None, force: bool = False,
              dry: bool = False) -> int:
    if os.environ.get("SNDK_READ_DISABLE") == "1":
        print("sndk-read :: disabled (SNDK_READ_DISABLE=1)")
        return 0
    now = now or datetime.now(_ET)
    if not force and not _market_live():
        print("sndk-read :: market closed — no read")
        return 0

    day = now.date().isoformat()
    # forced (off-hours --force) rows are excluded from the READER's view of
    # the day: they are not live tape, and on 07-28 a single 00:05 warmup row
    # overstated session_high by ~0.7σ all day and inflated every frozen age
    # (adversarial audit 08-02). The diary keeps them; the reader ignores them.
    rows = [r for r in _read_jsonl(_diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK"
            and not (r.get("meta") or {}).get("forced")]
    if not rows:
        print("sndk-read :: no diary rows yet")
        return 0
    row = rows[-1]
    prev_row = rows[-2] if len(rows) >= 2 else None

    # era-scoped: a row written under an older rule set may never seed today's
    # dwell clock or its run counter (the store is stamped for exactly this)
    reads_all = _read_jsonl(_reads_dir() / f"{day}.jsonl")
    reads = [r for r in reads_all if r.get("era") == ERA]
    prev = reads[-1] if reads else None
    # THE LAST ROW THAT ACTUALLY SAID SOMETHING, which is not the same row.
    # Carry-forward used `prev`, and `prev.reading` is None whenever that call
    # errored — so ONE timeout poisoned every row after it, each quiet scan
    # dutifully copying the None forward. Measured on 2026-08-31: 183 of the
    # session's 258 minutes carried no reading at all, not because nothing was
    # said but because the first timeout at 10:15 erased the 10:04 sentence and
    # every carry after it propagated the hole.
    #
    # A hidden surface must never read as a missing one. That was the rule the
    # carry-forward was written for, and it was defeated by reading the wrong
    # row.
    # the last row whose reading still HAS PROSE — a fully-deleted reading
    # ({quiet, abstain: forced} with every word gone) is truthy, and carrying
    # it forward blanked the screen exactly the way the timeout bug did,
    # through the other door.
    said = next((r for r in reversed(reads)
                 if (r.get("reading") or {}).get("read")), None)
    # the last row that actually spent a call — the wake gate measures drift
    # from the last LOOK, never from the last write (see should_wake)
    calls = [r for r in reads if r.get("wall_s") is not None]
    last_call = calls[-1] if calls else None

    capped = len(calls) >= DAILY_CALL_CAP
    if capped:
        # the cap blocks the CALL, never the record. Returning here left the
        # store silent for the rest of a 30-call day — and a day that busy is
        # precisely the day the record matters. The row still lands below,
        # stamped, with the sentence carried forward.
        print(f"sndk-read :: daily cap {DAILY_CALL_CAP} reached — "
              f"rows continue, calls do not")

    row = with_path(row, rows)
    band = magnet_band(row)
    frozen = frozen_fields(rows, now)
    wake = should_wake(row, prev_row, last_call, now, rows)
    if capped:
        wake = None

    # sr-6: the pulse check. Nothing here ever asked how old the newest row
    # was — a dead scanner, a halt, or a feed refusing ticks all read as an
    # ordinarily quiet tape, and the HEARTBEAT wake would then spend a model
    # call narrating the same frozen board every 45 minutes. A stale book
    # never wakes the model (an unparseable timestamp counts as stale — fail
    # closed, like the feed's own no-quote rule). The row still lands, stamped
    # stale_book so an outage can never pool with genuine quiet. Manual
    # --force remains a human override.
    # sr-7: measured off the BOOK, not off the scan. The scan clock said 0-1
    # minute while the book was up to 3.4 minutes old, so a gate written to
    # refuse a 6-minute-old book was really refusing a ~9-minute-old one, and
    # every row it let through was stamped with an age that was not the age of
    # anything in it.
    t_row, t_book = _ts(row), _book_asof(row)
    book_age = _age_min(t_book, now)
    scan_age = _age_min(t_row, now)
    # same function, same rounding, same constant as the payload's own gate
    stale = book_age is None or book_age > MAX_BOOK_AGE_MIN
    if stale and wake and not force:
        print(f"sndk-read :: STALE BOOK ({book_age}m) — '{wake}' suppressed")
        wake = None

    # sr-7: a forced read on a stale book builds AS OF THE ROW, not as of the
    # wall clock. Otherwise `--force` at 18:30 hands the freshness gate a
    # 150-minute-old book, every evidence block is deleted, and the run spends a
    # real model call asking for a direction from a scene containing a calendar
    # and nothing else. The row is old either way; the honest thing is to show
    # the last scene the reader COULD have built and let `data_sources` say when
    # that was — which is exactly the rule the payload tab already follows.
    scene_now = (t_row or now) if (stale and force and t_row is not None) else now
    scene = build_scene(row, band, frozen_fields(rows, scene_now)
                        if scene_now is not now else frozen, rows, scene_now,
                        since_last_read=frame_since_last_read(
                            row, rows, last_call, wake, force, scene_now,
                            prior_rows_today=bool(reads_all)))
    out = {
        "ts": now.isoformat(), "era": ERA,
        "wake": ("capped" if capped else
                 "stale_book" if stale and not force else (wake or "quiet")),
        "book_age_min": book_age,
        "book_asof": (row.get("meta") or {}).get("book_asof"),
        "scan_age_min": scan_age,
        "spot": row.get("spot"), "sigma": row.get("sigma"),
        "magnet_band": band, "frozen": frozen, "scans": len(rows),
        **({"forced": True} if force else {}),
        # wk-1: the state the NEXT gate compares against. Written on every read
        # row, not only on the ones that spend a call, so a restart mid-session
        # cannot leave the gate with nothing to measure from.
        "gate": state_for_next_wake(row, scene),
        "reading": None, "model": None, "wall_s": None, "error": None,
    }

    if not wake and not force:
        # still write the row: the reading simply carries forward. A hidden
        # surface must never read as a missing one.
        # Age is measured from when the SENTENCE was written, not from the last
        # row. Every quiet scan copies the reading forward, so measuring off the
        # previous row reset the age to the ~2-minute scan gap on every carry —
        # a sentence written at 10:00 still read "2m old" at 15:00, and the
        # card's own >10m dim could never fire. reading_ts rides with the text.
        out["reading"] = (said or {}).get("reading")
        out["reading_ts"] = (said or {}).get("reading_ts")
        rts = _parse_ts(out["reading_ts"])
        out["reading_age_min"] = (int((now - rts).total_seconds() // 60)
                                  if rts else None)
        atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
        print("sndk-read :: quiet (reading carried forward)")
        return 0

    # PAUSED: the wake gate fired and we record that it fired — the row keeps
    # its magnet ranking and the reason it woke, so the record has no hole where
    # the reasoning would have been. Only the sentence is withheld, and the row
    # says so rather than looking like a scan that had nothing to say.
    if not reasoning_on():
        out["paused"] = True
        out["reading"] = None
        atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
        print(f"sndk-read :: PAUSED | {wake} | row recorded, no model call")
        return 0

    if dry:
        print(json.dumps({k: out[k] for k in
                          ("ts", "wake", "spot", "magnet_band", "frozen")},
                         indent=1, default=str))
        print("--- scene handed to the model ---")
        print(json.dumps(scene, indent=1, default=str))
        return 0

    prompt = ("Read this scene cold and reply with the JSON object only.\n\n"
              "SCENE:\n" + json.dumps(scene, default=str))
    obj, err, wall, raw = call_the_model(prompt, PINNED_MODEL)
    out["wall_s"], out["model"] = wall, PINNED_MODEL
    if err or not isinstance(obj, dict):
        out["error"] = err or "unparseable reply"
        if raw:
            out["raw_reply"] = raw
        # ...and the standing sentence survives the failure. A call that timed
        # out is a call that said nothing, which is exactly the case
        # carry-forward exists for — leaving `reading` None here is what turned
        # one 100-second timeout into a blank screen for the rest of the day.
        out["reading"] = (said or {}).get("reading")
        out["reading_ts"] = (said or {}).get("reading_ts")
        rts = _parse_ts(out["reading_ts"])
        out["reading_age_min"] = (int((now - rts).total_seconds() // 60)
                                  if rts else None)
    else:
        reading = check_reading_against_scene(obj, scene)
        out["reading"] = reading
        # WHAT WAS DROPPED, IN THE MODEL'S OWN WORDS. `dropped_observations`
        # records the REASON a gate fired and never the text it fired on, so a
        # drop left no evidence of what was actually said — which made the two
        # live obs-1 mutilations diagnosable only by reproducing the symptom.
        # Kept only when something was dropped, and truncated: this is an audit
        # trail, not a second copy of the reply.
        if reading.get("dropped_observations"):
            out["raw_reply"] = json.dumps(obj, default=str)[:1200]
        # obs-1: the two numbers the nightly audit lives on. `quiet` says the
        # model found nothing; `abstain` says whether it CHOSE that or whether
        # the gates forced it by deleting everything. Counting them together
        # would hide exactly the failure the gates exist to catch.
        out["quiet"] = reading.get("quiet")
        out["abstain"] = reading.get("abstain")
        out["dropped_observations"] = reading.get("dropped_observations")
        out["reading_ts"] = now.isoformat()   # stamped once, carried forward after
        out["reading_age_min"] = 0
    atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
    if out["reading"] and not out.get("error"):
        # one RAG slice per COMPLETED read — an errored call carries the last
        # sentence forward for the screen, and filing that carry as a new
        # memory stamped at the new time would duplicate narratives and let
        # the day-story picker choose the copy. The screen forgives; the
        # memory must not.
        try:
            import sndk_rag
            sndk_rag.record_slice(row, out, scene, now)
        except Exception:
            pass
    r = out["reading"] or {}
    n = len(r.get("points") or [])
    print(f"sndk-read :: {wake} | {n} to watch"
          + (f" ({r['abstain']})" if r.get("abstain") else "")
          + f" | {wall}s | " + (r.get("read") or out["error"] or ""))
    return 0


def replay(day: str) -> int:
    """Re-run the gate over a recorded day. Writes nothing — this is how the
    wake rate was measured before shipping."""
    rows = [r for r in _read_jsonl(_diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK"
            and not (r.get("meta") or {}).get("forced")]   # same rule as live
    if not rows:
        print(f"no rows for {day}")
        return 1
    last_call = None
    wakes = 0
    from collections import Counter
    why = Counter()
    for i, row in enumerate(rows):
        now = _ts(row)
        if now is None:
            continue
        row = with_path(row, rows[:i + 1])
        w = should_wake(row, rows[i - 1] if i else None, last_call, now,
                        rows[:i + 1])
        if not w:
            continue
        # the gate snapshot, exactly as a live row would carry it — without it
        # every structural wake (walls, flip, IV, sign, pin) was unreachable
        # and this tool measured a crippled gate while claiming to measure the
        # shipped one.
        last_call = {"ts": row["ts"], "spot": row.get("spot"),
                     "magnet_band": magnet_band(row),
                     "gate": state_for_next_wake(row)}
        wakes += 1
        why[w] += 1
        print(f"  {row['ts'][11:16]}  {w:18s} spot={row.get('spot'):>9}")
    print(f"\n{day}: {wakes} model calls / {len(rows)} scans "
          f"({wakes/len(rows)*100:.0f}%) · {dict(why)}")
    return 0


if __name__ == "__main__":
    if "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        sys.exit(replay(sys.argv[i + 1]))
    sys.exit(read_once(force="--force" in sys.argv, dry="--dry" in sys.argv))
