#!/usr/bin/env python3
# Interpreter: same venv as sndk_hunter.py, and the shebang cannot pin it — name
# the venv python explicitly or use runtime/scripts/run-sndk-read.sh.
"""
sndk_read.py — the SNDK chart's live reading. One wake = one read row.

WHAT THIS IS NOT: a ported Watchtower. The SPX tower is a forward forecaster
whose call IS the model's opinion. Here the two paths are deliberately
DECOUPLED (sr-2, 2026-08-02 blueprint):

    the ARROW is decided by a deterministic aggregator (this module, no model)
    and still draws on the chart, exactly as before;
    the READING is the model's OWN inference — vector + magnitude — from an
    UNBIASED scene that carries evidence only, never a verdict. The old
    arrow_already_decided block anchored the model and is gone from the scene.

That split is not stylistic — it is what four sessions of recorded SNDK rows
measured (2026-07-28..31, 756 rows, verified against state/sndk_reversion/):

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

So: silence is the default and must be OVERCOME. Both layers must clear their
OWN separation gate before an arrow exists — but only the magnet POINTS. Shove
measures the same gamma mass the magnet does, so it can veto and can never vote
(see _layers); there is no "the layers agreed" step and there must never be one.
Measured on the four recorded sessions both gates clear on 69 of 756 scans
(9.1%, ~17/day); dwell then holds the arrow alive on 103 of 756 (13.6%).

Isolation (README doctrine): writes ONLY state/sndk_reads. Never imports
watchtower, never touches the SPX diary, never blocks sndk_hunter's tick —
this is its own launchd job so a slow model call cannot drop a scan.

Two switches, and they are NOT the same thing:
    SNDK_READ_DISABLE=1   KILL — no arrow, no row, no record at all (checked
                          here AND in run-sndk-read.sh).
    sndk_reads/control.json  PAUSE — silences only the model's sentence; every
                          deterministic layer keeps running and every row keeps
                          landing on disk (see reasoning_on).

CLI:
    python3 sndk_read.py              # one wake check (RTH-gated)
    python3 sndk_read.py --force      # bypass the RTH gate
    python3 sndk_read.py --dry        # decide + aggregate, print, NO model call
    python3 sndk_read.py --replay DAY # re-run the gate over a recorded day,
                                      # print the wake/arrow timeline, write nothing
"""
from __future__ import annotations

import json
import math
import os
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
                                       # (walls ladder = the ladder's own walls)

_ET = ZoneInfo("America/New_York")
_SQRT_TDAYS = math.sqrt(252.0)         # engine trading-days constant (√252)

ERA = "wk-1"                # bump on ANY change to the gates or the prompt.
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

# --- admissibility gates (the arrow) -----------------------------------------
# A layer may only COUNT once it has separated from its own noise — and only the
# magnet ever points (see _layers). Both numbers are pre-registered here so a
# later reader can see they were chosen before the arrow shipped, not fitted to it.
MAGNET_SEP_PP = 5.0         # top1 - top2 share of gamma mass, percentage points.
                            # Median gap is 3.87pp, so this is silent by default
                            # and speaks on ~38% of scans.
SHOVE_SEP_REL = 0.30        # |up - down| / max(|up|,|down|). 53.5% of scans sit
                            # inside a 15% near-tie; this clears ~40%.
PATH_STRETCH_SIGMA = 0.30   # price already travelled this far in 30 min -> the
                            # arrow wears a "already ran" caution. Median 30-min
                            # move is 0.077σ, so this marks a genuine outlier.
MIN_DWELL_MIN = 15          # no direction reversal within this many minutes of
                            # the last one, ever (anti-flicker, S9)

# --- what the model may NOT cite ---------------------------------------------
# Neither of these gates the arrow; both gate the SENTENCE. A reading is only
# honest if the number behind it could still have changed today.
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
PCTL_CACHE_V = 2            # bump to invalidate pctl_prior.json — v2 counts
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
                   "breadth", "momentum", "dealer_positioning", "walls"],
    OI_SNAPSHOT: ["regime", "magnet", "breadth", "dealer_positioning", "walls"],
}

PRESENT_TENSE_FORBIDDEN_FOR = ("magnet", "walls", "breadth",
                               "dealer_positioning", "regime.charm", "regime.flip")
LEXICON_ENFORCE = False
_STALE_NOUNS = ("magnet", "wall", "walls", "dealer", "gamma", "charm",
                "breadth", "vanna", "open interest", "oi")
_LIVE_VERBS = ("is building", "are building", "is fading", "are fading",
               "is buying", "are buying", "is selling", "are selling",
               "right now", "currently", "just now", "is stacking",
               "are stacking", "is piling", "are piling")

WALLS_PER_SIDE = 2          # the ladder stops at 2 — deeper strikes are noise

# --- the model call ----------------------------------------------------------
CALL_TIMEOUT_S = 100.0      # one attempt, no retries — a slow read is skipped.
                            # Raised from 60s in sr-2: the read may now spend a
                            # history lookup or an external search inside the
                            # call. Still under the 120s tick, and this is its
                            # own launchd job so a slow read never costs a scan.
_NO_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob",
             "Grep", "WebFetch", "Task", "Agent", "TodoWrite",
             "ExitPlanMode", "BashOutput", "KillShell", "SlashCommand", "Skill")
# sr-2 on-demand paths: the ONE Bash command the read may run (the history
# tool) and WebSearch for the abnormal-tape catalyst check. Everything else
# stays banned — the scene is still the primary evidence.
_RAG_CMD = f"{sys.executable} {_SKILL_DIR / 'sndk_rag.py'}"
_ALLOWED_TOOLS = (f"Bash({_RAG_CMD}:*)", "WebSearch")


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

    SNDK_READ_DISABLE=1 stops this module dead — no arrow, no row, no record.
    This is the other thing: it silences only the MODEL CALL while every
    deterministic layer keeps running and every row keeps landing on disk. The
    arrow, the gates, the magnet ranking, the frozen list and the wake reason
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


# ---------------------------------------------------------------------------
# the aggregator — this, not the model, decides the arrow
# ---------------------------------------------------------------------------
def _shove(row: dict) -> dict:
    gv = row.get("gex_views") or {}
    return gv.get("shove") or {}


def _layers(row: dict) -> list[dict]:
    """The reads behind the arrow, each with its separation test and the number
    that drove it.

    THESE ARE NOT INDEPENDENT AND THE CODE MUST NOT PRETEND THEY ARE. The first
    draft of this module required "two layers to agree" before drawing an arrow.
    Replayed over the four recorded sessions both gates cleared on 69 of 756
    scans and agreed on 69 of 69 — 100%. The reason is structural, not lucky:
    the magnet IS the largest pile of gamma, and shove measures the asymmetry of
    that same gamma mass, so agreement is guaranteed by construction. Two
    correlated views of one measurement dressed as corroboration is exactly the
    false-confidence pattern this module exists to remove.

    So the roles are named honestly instead:
        magnet  -> SOURCE. The direction comes from here and nowhere else.
        shove   -> BREADTH. Not a second opinion; a check that the asymmetry is
                   broad rather than one lucky strike. It can veto, never vote.
        path    -> CAUTION. Genuinely independent of the book (it is price, not
                   positioning) and used only to flag, never to point.

    Every entry carries `cite` — the raw field and value a reader can check
    against the chart with their own eyes. Constants (INADMISSIBLE) never appear
    here at all."""
    spot = row.get("spot")
    out: list[dict] = []

    # --- magnet: only once the top strike has actually beaten the runner-up ---
    mb = magnet_band(row)
    if spot is not None and mb["top"]:
        top_k = mb["top"][0][0]
        adm = (not mb["tie"]) and abs(top_k - spot) > 0
        out.append({
            "name": "magnet", "role": "source",
            "dir": ("up" if top_k > spot else "down") if adm else None,
            "admissible": adm,
            "cite": f"top strike {top_k:g} holds {mb['top'][0][1]:.1f}% vs "
                    f"{mb['top'][1][0]:g} at {mb['top'][1][1]:.1f}%"
                    if len(mb["top"]) >= 2 else f"top strike {top_k:g}",
            "sep": mb["gap_pp"], "sep_needs": MAGNET_SEP_PP, "sep_units": "pp",
        })

    # --- shove: BREADTH gate, not a vote ------------------------------------
    # Records which side is heavier for the record, but deliberately emits no
    # direction: on four sessions the terrain convention (heavier side resists)
    # and the raw convention (heavier side is the way it goes) swing ~30 points
    # day to day across only 86 sign runs, so neither is established and this
    # module will not assert one. What it CAN say without picking a sign is
    # whether the mass is lopsided enough for the top strike to mean anything.
    sh = _shove(row)
    up, dn = sh.get("shove_up_margin"), sh.get("shove_down_margin")
    if isinstance(up, (int, float)) and isinstance(dn, (int, float)):
        scale = max(abs(up), abs(dn), 1e-9)
        rel = abs(up - dn) / scale
        adm = rel >= SHOVE_SEP_REL
        out.append({
            "name": "shove", "role": "breadth",
            "dir": None,                      # never votes — see the docstring
            "heavier": "up" if up > dn else "down",
            "admissible": adm,
            "cite": f"resistance up {up:.2f} vs down {dn:.2f}",
            "sep": round(rel, 3), "sep_needs": SHOVE_SEP_REL, "sep_units": "rel",
        })

    # --- path: CAUTION flag, genuinely independent of the book --------------
    # The one pattern with any day-to-day stability in the recorded data is
    # short-horizon mean reversion, so an arrow pointing the way price has just
    # travelled is the least trustworthy kind. This does not vote and does not
    # veto; it raises a flag the chart renders and the reading must acknowledge.
    sig = row.get("sigma") or 0
    ran = row.get("_ran_30m_sigma")
    if sig and isinstance(ran, (int, float)):
        out.append({
            "name": "path", "role": "caution",
            "dir": None,
            "ran": round(ran, 2),
            "stretched": abs(ran) >= PATH_STRETCH_SIGMA,
            "admissible": True,
            "cite": f"price ran {ran:+.2f}σ in the last 30 min",
            "sep": abs(round(ran, 2)), "sep_needs": PATH_STRETCH_SIGMA,
            "sep_units": "σ",
        })
    return out


def aggregate(row: dict, prev: Optional[dict], now: datetime) -> dict:
    """Silence unless the magnet has genuinely separated AND the mass behind it
    is broad. Direction comes from the magnet alone; shove can only veto.

    This is the whole anti-narrative mechanism. An LLM handed this surface will
    always find a coherent story; a gate handed a 3.9pp tie returns nothing, and
    nothing is the honest answer on ~91% of scans."""
    layers = _layers(row)
    by = {l["name"]: l for l in layers}
    src, breadth = by.get("magnet"), by.get("shove")

    why: Optional[str] = None
    direction: Optional[str] = None
    if src is None:
        why = "no strike mass reported"
    elif not src["admissible"]:
        why = (f"no strike is in charge — top two are "
               f"{src['sep']}pp apart, needs {src['sep_needs']}pp")
    elif breadth is None:
        why = "terrain unreported"
    elif not breadth["admissible"]:
        why = (f"the book is too flat to trust one strike "
               f"({breadth['sep']} lopsidedness, needs {breadth['sep_needs']})")
    else:
        direction = src["dir"]

    # --- minimum dwell: a reversal must wait out MIN_DWELL_MIN ---------------
    # Re-reasoning a barely-changed payload flips arrows for reasons that do not
    # exist in the market. The payload is identical scan-to-scan 98.3% of the
    # time for the magnet, so without this the arrow's motion IS the noise.
    prev_arrow = (prev or {}).get("arrow") or {}
    prev_dir = prev_arrow.get("dir")
    since = prev_arrow.get("since")
    run = int(prev_arrow.get("run") or 0)
    issued_spot = prev_arrow.get("issued_spot")
    held = False
    if prev_dir and direction != prev_dir and since:
        # Dwell covers EVERY state change, not just up<->down. Replayed on
        # 07-31 a reversal-only rule still let the arrow appear and stand down
        # 20 times in a session — the same flicker wearing a different shape,
        # and each flap spent a call. A live arrow holds its ground for
        # MIN_DWELL_MIN and says it is fading rather than blinking out.
        st = _parse_ts(since)
        if st and (now - st) < timedelta(minutes=MIN_DWELL_MIN):
            age = int((now - st).total_seconds() // 60)
            held_why = why
            # NAME THE EVENT BEFORE REASSIGNING. `direction` is about to become
            # prev_dir (always truthy here), so testing it after the assignment
            # made the 'stand-down' branch unreachable and every held stand-down
            # printed "reversal held" — seen live at 10:32 on 07-31.
            kind = "reversal" if direction else "stand-down"
            direction, held = prev_dir, True
            why = (f"{kind} held — {prev_dir} is only {age}m old "
                   f"(needs {MIN_DWELL_MIN}m)"
                   + (f"; {held_why}" if held_why else ""))

    if direction and direction != prev_dir:
        since = now.isoformat()
        run += 1
        issued_spot = row.get("spot")
    elif not direction:
        since, issued_spot = None, None

    # --- the ghost: the last COMPLETED call, and it persists ---------------
    # The first cut read `prev_dir` straight off the previous row, which meant
    # that while a call was live the "ghost" was that same call's own origin —
    # replayed over 07-30 it duplicated the live arrow on 25 of the 26 rows it
    # appeared — and one scan after a call died it vanished entirely, because
    # the row after that had no prev_dir left to read. That is the opposite of
    # its stated job: a bad call is supposed to STAY visible.
    #
    # So the ghost is now written only when a call ENDS (reverses or stands
    # down), and it is carried forward untouched until another call ends. It can
    # never be the live arrow, and it does not evaporate the moment it becomes
    # inconvenient. A held reversal is not an ending — dwell restored the
    # direction, so nothing completed.
    ghost = prev_arrow.get("ghost")
    if (prev_dir and not held and direction != prev_dir
            and prev_arrow.get("issued_spot") is not None):
        ghost = {"dir": prev_dir, "spot": prev_arrow.get("issued_spot"),
                 "since": prev_arrow.get("since"), "ended": now.isoformat()}

    # the caution flag — independent of the book, so it is the one cross-check
    # here that is not the magnet wearing a different hat
    path = by.get("path")
    caution = None
    if direction and path and path.get("stretched"):
        ran_up = (path.get("ran") or 0) > 0
        if (ran_up and direction == "up") or ((not ran_up) and direction == "down"):
            caution = (f"price already ran {path['ran']:+.2f}σ this way in 30 min — "
                       f"the move may be behind it")

    return {
        "dir": direction,
        "state": "call" if direction else "silent",
        "since": since,
        # the run counter is the day's Nth call and it never rewinds — a silent
        # scan keeps the count so the NEXT call is numbered against the session,
        # not against the current streak
        "run": run,
        "issued_spot": issued_spot,
        "held_reversal": held,
        # the arrow is standing its ground on dwell while its own gate has
        # already failed — the chart draws it dimmed, not solid
        "fading": why if held else None,
        "caution": caution,
        "layers": layers,
        "silent_because": None if direction else why,
        "ghost": ghost,
    }


# ---------------------------------------------------------------------------
# frozen-field detection — what the model may NOT cite
# ---------------------------------------------------------------------------
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
def gate_state(row: dict) -> dict:
    """The structural state the wake gate compares against, snapshotted.

    THIS EXISTS BECAUSE `prev` IS NOT A DIARY ROW. `read_once` passes the last
    row that SPENT A CALL, and those live in state/sndk_reads/, whose schema is
    ts / wake / spot / sigma / arrow / magnet_band / reading / ... and carries
    none of `call_wall`, `put_wall`, `gamma_flip`, `atm_iv`, `gamma_sign` or
    `gex_views`. A gate reading those off `prev` gets None every time and
    silently degenerates to price-and-heartbeat — the exact starvation
    should_wake's own docstring records happening once before.

    So the read row now carries this snapshot, and the gate reads it through
    `_spoke_field`, which falls back to the row itself so a diary row (tests,
    replay) still works unchanged."""
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    out = {"spot": _fin(row.get("spot")), "sigma": _fin(row.get("sigma")),
           "gamma_sign": row.get("gamma_sign"), "magnet": _fin(gv.get("magnet")),
           "gamma_flip": _fin(row.get("gamma_flip")),
           "call_wall": _fin(row.get("call_wall")),
           "put_wall": _fin(row.get("put_wall")),
           "atm_iv": _fin(row.get("atm_iv"))}
    return {k: v for k, v in out.items() if v is not None}


def _spoke_field(prev: dict, key: str):
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
    spoke_spot = _spoke_field(prev, "spot")
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
        if _spoke_field(prev, "gamma_sign") is not None and _held_for(
                books, lambda b: b.get("gamma_sign"),
                _spoke_field(prev, "gamma_sign"), WAKE_CONFIRM_BOOKS):
            return "gamma sign flipped"
        if _spoke_field(prev, "magnet") is not None and _held_for(
                books, lambda b: _fin((b.get("gex_views") or {}).get("magnet")),
                _spoke_field(prev, "magnet"), WAKE_CONFIRM_BOOKS):
            return "pin moved"

    # 3-4. price CROSSING a level the last reading actually spoke about. The
    # level is the one that was true when it spoke, never the relabelled one.
    if spot is not None and spoke_spot is not None:
        f = _spoke_field(prev, "gamma_flip")
        if (f is not None and sig and (spot - f) * (spoke_spot - f) < 0
                and abs(spot - f) / sig > 0.02):
            return "crossed flip"
        for k, word in (("call_wall", "call wall"), ("put_wall", "put wall")):
            w = _spoke_field(prev, k)
            if w is not None and (spot - w) * (spoke_spot - w) < 0:
                return f"{word} crossed"
        # 5. plain travel
        if sig and abs(spot - spoke_spot) / sig >= WAKE_SPOT_SIGMA:
            return "price ran"

    # 6. implied vol, on the SMOOTHED series — the raw one is mostly sensor
    if books and new_book:
        ivs = [v for v in (_fin(b.get("atm_iv")) for b in books) if v is not None]
        spoke_iv = _spoke_field(prev, "atm_iv")
        if ivs and spoke_iv is not None:
            med = statistics.median(ivs[-WAKE_IV_MEDIAN_BOOKS:])
            if abs(med - spoke_iv) * 100.0 >= WAKE_IV_PP:
                return "iv moved"

    # 7. the flip LEVEL relocating, as opposed to price crossing it
    fl, spoke_fl = _fin(row.get("gamma_flip")), _spoke_field(prev, "gamma_flip")
    if new_book and sig and fl is not None and spoke_fl is not None:
        if abs(fl - spoke_fl) / sig >= WAKE_FLIP_SIGMA:
            return "flip moved"

    if gap >= HEARTBEAT_MIN:
        return "heartbeat"
    return None


# ---------------------------------------------------------------------------
# the model call — sr-2: the model infers its OWN vector from an unbiased scene
# ---------------------------------------------------------------------------
_DOCTRINE = f"""You read one stock's dealer-positioning snapshot COLD and infer a direction vector and a magnitude from the evidence — nothing in the scene is a conclusion, and nobody has decided anything for you. Write for a smart reader who does not know options jargon.

THE INSTRUMENT. This is SNDK, a single stock, not an index. Its sigma (a typical day's move) runs 8-10% of the share price — enormous. It has weekly expiries, not daily, so most days have no expiry at all. Standing levels come from open interest, which is yesterday's positioning about a stock that can move 12% in a session.

FIELD NAMES SAY WHAT THEY ARE. Every leaf in this scene is named so it can be read alone: a `_pp` suffix is percentage points, `_bn` is billions of dollars, `_musd` is millions, `_min` is minutes, `_sigma` is a distance in sigma. Read the name before you read the number.

WHERE EVERY NUMBER CAME FROM (read this block first — it decides how much any other block is worth):
- WHICH BLOCK RESTS ON WHAT. `price` and `history` are the LIVE TAPE, good to the second. `clock` is the wall clock. `scale`, `regime`, `magnet`, `breadth`, `momentum`, `dealer_positioning`, `walls` and `clock.front_expiry` come out of the OPTIONS BOOK, which is minutes old and often a cached repeat. Of those, `regime`, `magnet`, `breadth`, `dealer_positioning` and `walls` rest further on the OPEN INTEREST SNAPSHOT struck at last night's close. A block you cannot find was either not measured or deleted for age; see freshness_rules.
- data_sources holds TWO clocks and they disagree on purpose. `scan_taken_at` is when this row was written, which is also when the spot was quoted — there is no separate quote clock, and `spot_feed` names where that price came from. `options_book.measured_at` is when the CHAIN was pulled, and `options_book.age_min` is its real age: the feed re-serves a cached book on about half of all scans (`is_repeat_of_previous_scan` says whether this one is a repeat), so the book is routinely 2-4 minutes old on a scan that is seconds old. Never quote the scan's freshness for a structural number.
- `open_interest` is the one that matters most and the one most easily misread. Every standing structure in this scene — magnet, walls, breadth, dealer_positioning, charm, flip — is computed from open interest struck at the PRIOR SESSION'S CLOSE (`prior_session_date` names the day) and it does not change during the session: `measured_unchanged_so_far_today` re-proves it against `strikes_compared_today` strikes whenever today's scans give it enough overlapping strikes to answer — and says nothing at all rather than guessing when they do not. What moves intraday is price moving under a fixed map, not the map moving.
- Because of that, THESE BLOCKS MAY NEVER BE NARRATED IN THE PRESENT TENSE: {", ".join(PRESENT_TENSE_FORBIDDEN_FOR)}. "Dealers are buying", "the wall is building", "gamma is piling up" are false statements about time, not debatable reads. Say "as of last night's close" or say nothing. Only `momentum` and `*_change_30min` fields describe something that moved today.
- freshness_rules is enforced, not advisory: a block whose source aged past its ceiling is DELETED before you see it, and `blocks_dropped_this_scan` names what went. So an absent block may mean "measured but too old" — check there before calling anything unknown.

THE TWO RULERS, AND WHICH ONE A DISTANCE USES:
- `price.live_spot` is where the stock is NOW. `price.spot_when_book_was_measured` is where it was when the chain was pulled. On a cached row these differ by a median of about $2 and by as much as $42.
- EVERY book-derived distance in this scene — `regime.flip`, `magnet`, `walls` — is measured from the BOOK's spot, not the live one, because that is the only frame in which the book's own numbers are consistent. That is the standing rule and blocks do not restate it. The EXCEPTION announces itself: a block carrying `sigma_measured_from: live_spot_because_chain_spot_was_absent` had no book spot to divide, so its distances are already in the live frame and `price.live_minus_book_spot_sigma` will be missing from the scene as well. To convert a book-measured distance to a distance from where price actually is, SUBTRACT `price.live_minus_book_spot_sigma` — it is how far the live spot sits above the book's spot, so taking it off a book-measured distance is what moves that distance into the live frame. The one distance NOT in the book's frame is `price.vwap_minus_live_spot_sigma`, which is measured off the live quote because vwap is a live-tape level; read its name as the subtraction it is, so POSITIVE means vwap is ABOVE price — price trading BELOW its average for the day. That is the opposite sense to `price.moved_last_30min_sigma`, which is positive when price ROSE.

HOW TO READ THE SCENE (grouped by force, not by metric):
- clock is the SESSION calendar and nothing else. `front_expiry.days_to_expiry` is where you are in the weekly cycle — a 4-dte Monday book holds standing positioning with all week to migrate, while 0 collapses to expiry-day mechanics where pinning and charm run at full strength. `minutes_to_close` says how much session is left for any read to resolve in — a 30-minute call needs 30 minutes of tape.
- scale: the sigma ruler, plus `expected_move_today_asym` — today's likely range split unevenly toward the side the options market fears (from put/call IV skew). It breathes with vol.
- regime: the day's character. `vol_trend` says whether implied vol is rising or falling NOW — it is the switch that arms vanna and charm. `flip` is the chop band, and it ships as ONE measured level plus a width: `band_center_is_gamma_flip_sigma` IS the gamma flip, the band runs that centre plus and minus `edges_are_center_plus_minus_sigma`, and `live_price_vs_band` says where price sits in it. Compute an edge if you need one — it is arithmetic on two numbers in front of you, not a third independent level, so an edge "agreeing" with the centre is one witness, not two. Words like "clear negative" describe WHERE PRICE SITS relative to the dealer-hedging flip, a location on the map, not a bearish or bullish stamp. `charm` is time-decay hedging: `magnitude_musd_per_day_uncalibrated` (compare day to day, never to another book) and `drifts_toward_strike`, the level it funnels toward late in the session — a level, never a promised direction.
- magnet: strikes pulling price, each with its `share_of_book_gamma_pp`. `top_strike_lead_pp` is the top strike's lead over the runner-up in points of gamma mass — SMALL means no strike is really in charge, and there is no threshold where it flips: read the number, and read `top_strike_lead_vs_own_history` for whether today's lead is unusual for this tape. That comparison counts DISTINCT BOOKS, not scans, so a cached book never votes twice.
- breadth: `lopsidedness_0_is_even` is how one-sided the book's resistance is. It NEVER points a direction — both readings of which side a heavy book favours are unestablished here. `heavier_side` names a side as a fact, not a lean. It reads THE SAME GAMMA PILE as the magnet — always, by construction — so breadth agreeing with the magnet is one witness twice, not two witnesses.
- momentum: the ONLY block here that describes change during today's session. `window_between_books` tells you exactly what was compared — `books_compared` distinct books spanning `span_min` minutes, measured on the book clock, not the scan clock. `share_of_book_gamma_change_pp` and `gross_volume_change_contracts` are the change itself; there is no verdict word, because a fixed threshold cannot tell building from noise. (OI deltas and true order-flow CVD are not measured here; absent means unmeasured, never zero.)
- dealer_positioning: standing dealer positioning as of last night's close. `net_delta_bn` is $-delta in billions from an assumed-sign book, and ITS SIGN IS AN ARTIFACT OF THE FORMULA, which cannot produce another one — it has been positive on every one of 4,149 recorded scans, ranging 1.15 to 8.24 and never once crossing zero. Its sign is a fact about the formula, NOT a fact about dealers, so it is never evidence for anything and never means "dealers are long" or "dealers are bullish"; only `net_delta_change_30min_bn` can be news. `net_vanna_musd_per_vol_point` only matters when vol_trend is moving.
- walls: standing structure, up to two per side, ALWAYS ordered by DISTANCE, nearest first — walls.call[0]/put[0] are simply the first thing price would meet going that way, NOT the strongest; the second says what is behind the first (right there, or open air). `share_of_book_gamma_pp` is the wall's share of total book gamma — that number, not the position, says how heavy a wall is. When the heaviest cluster on a side is further out than both, it ships separately as call_heaviest_wall_behind_the_ladder / put_heaviest_wall_behind_the_ladder. `unchanged_for_min` is how long that wall has held (`unchanged_for_at_least_min` when the lookback ran out first); a wall that has not moved in hours is standing structure, not news.
- history flags when to reach outside: `price_at_level_unseen_earlier_today`, `tape_abnormal_vs_own_history`.
- ANY missing field was not cleanly measured this scan. Treat absence as "no data", never as neutral, never as zero. The OPPOSITE case is stated outright: a `*_side_has_no_wall` flag or `flip.no_flip_anywhere_on_board` means the board WAS measured and genuinely holds nothing there — price in open air with no ceiling, or a book with no flip, is a real reading and often the loudest one in the scene. Absence = unknown; a no-wall flag = known empty. Never confuse the two.

WHEN TO REACH OUTSIDE THE SCENE (both optional, most reads need neither):
- History: run `{_RAG_CMD} query --help` for narrative recall, and `{_RAG_CMD} series --help` for the numeric spot/magnet/walls series by time (and per-strike with --strike). Reach for them when something does not add up from the live snapshot alone — price testing a level unseen today (history.price_at_level_unseen_earlier_today flags it), a level acting out of character, or a cross-day question ("has 1300 held before?"). Day tier = today's slices + day summaries; month tier = standing terrain. Exact asks go through the numbers, not the text: past sessions answer to --date/--from-date/--to-date, --min-move (signed %: -5 = fell 5%+), and --near-strike (matches any day that traded through the level) — use --text only for meaning ("rejected the wall"), never for dates or biggest/worst. History is context, never the trigger — the live scene stays primary.
- Outside world: when the tape is genuinely abnormal (history.tape_abnormal_vs_own_history, an outsized gap or move), a human would ask WHY — use WebSearch for the catalyst (earnings, macro, news) or sector context. Extreme cases only, not every scan.

HONESTY RULES, all of them load-bearing:
- Never cite a field listed in frozen_do_not_cite as the reason for anything new. If the magnet has not moved in two hours, it is not news.
- Never narrate an open-interest block in the present tense. See the provenance rules above — this is the one that will be checked.
- The magnet is usually a TIE. When top_strike_lead_pp is small, no strike is really in charge — say so rather than naming one.
- Never invent a level. If price is past the last named level on the board, say exactly that — on this name it is often true.
- Thirty-minute moves here have a SPREAD, not a typical size: half run under 0.09 sigma, one in five exceeds 0.20, one in twenty exceeds 0.46, and the worst recorded is 1.71 — measured over 8 sessions of an extreme post-earnings stretch, so treat it as the shape of the tail, not a calibrated table. Size your magnitude against the whole spread — a big call needs a named force behind it, but the tail is real, so never let the median become your ceiling. Pointing at a level 0.6 sigma away is still a landmark, not a 30-minute target.
- The one pattern this tape has shown any short-horizon stability in is MEAN REVERSION: a vector pointing the same way price just travelled (price.moved_last_30min_sigma) is the least trustworthy kind and needs extra evidence beyond the move itself.
- "none" is an honest vector. When the evidence genuinely balances, say what is balanced instead of forcing a lean.

OUTPUT. Reply with ONLY a JSON object, no prose around it, no code fence:
{{"vector": "up" | "down" | "none",
 "magnitude_sigma": <expected travel over the next ~30 minutes, in sigma — omit when vector is "none">,
 "line": "<one sentence, max 24 words: the read and the force behind it>",
 "breaks_if": "<one short clause naming the specific level or condition that would flip this read>",
 "cited": "<the one or two fields and numbers you leaned on>"}}"""


def _stale_language_flags(reading: dict) -> list[str]:
    """Phrases that narrate an open-interest field as if it were happening now.

    sr-7. The scene labels every OI-derived block "as of last night's close",
    and a label is not a control: the same doctrine already told the model the
    book was N minutes stale and no reading ever discounted anything for it.
    This is the measurement half of the control — every hit is recorded on the
    read row so the rate is known before LEXICON_ENFORCE is allowed to reject
    anything. A guard that has never fired is not evidence that nothing is
    wrong; a guard that fires on 40% of readings is not a guard either, and
    only the tape can say which one this is."""
    text = " ".join(str(reading.get(k) or "")
                    for k in ("line", "breaks_if", "cited")).lower()
    if not any(w in text for w in _STALE_NOUNS):
        return []
    return sorted({v for v in _LIVE_VERBS if v in text})


def _validate_reading(obj: dict) -> Optional[dict]:
    """Keep ONLY the schema's fields, validated — a malformed vector is
    dropped (never guessed at), a magnitude is clamped sane and only rides
    with a directional vector, and extra keys do not ride along."""
    reading = {k: str(obj.get(k, ""))[:400]
               for k in ("line", "breaks_if", "cited") if obj.get(k)}
    vec = obj.get("vector")
    if vec in ("up", "down", "none"):
        reading["vector"] = vec
        if vec != "none":
            try:
                mag = float(obj.get("magnitude_sigma"))
                if math.isfinite(mag):
                    reading["magnitude_sigma"] = round(min(max(mag, 0.0), 3.0), 2)
            except (TypeError, ValueError):
                pass               # a vector without a magnitude is honest
    if reading:
        flags = _stale_language_flags(reading)
        if flags:
            reading["stale_language_flags"] = flags
            if LEXICON_ENFORCE:
                # the enforcing branch does NOT silently rewrite the sentence:
                # a laundered read is worse than a refused one. The row keeps
                # the flags and loses the prose, which is a visible failure.
                return {k: v for k, v in reading.items()
                        if k in ("vector", "magnitude_sigma",
                                 "stale_language_flags")}
    return reading or None


def _extract_json(text: str):
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


def _ask(prompt: str, model: str, timeout: float = CALL_TIMEOUT_S):
    """ONE `claude -p` call. sr-2 grants exactly TWO tools — the history CLI
    (a single allow-listed Bash prefix) and WebSearch — because the on-demand
    reach is part of the design; everything else stays banned, so the scene
    remains the primary evidence and the schema bill stays small. Doctrine
    rides --append-system-prompt so it stays byte-identical all day and is
    served from cache; the varying scene rides the body.

    Returns (obj | None, error | None, wall_seconds)."""
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
           "--append-system-prompt", _DOCTRINE,
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--allowedTools", ",".join(_ALLOWED_TOOLS),
           "--disallowedTools", *_NO_TOOLS]   # variadic — must stay LAST
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
    for p in days:
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        seen_book = None
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
            lp = _lopsided(r)
            if lp is not None:
                lops.append(lp)
    blob = {"sessions": key, "pctl_v": PCTL_CACHE_V,
            "magnet_gap_pp": sorted(gaps), "lopsidedness": sorted(lops)}
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
        e = {"strike": c["peak"], "sigma": sd(c["peak"]),
             "share_of_book_gamma_pp": round(c["strength"] / tot * 100.0, 1)}
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


def build_scene(row: dict, band: dict, frozen: list,
                rows: list[dict], now: datetime) -> dict:
    """The model's view of the world — sr-2: an UNBIASED snapshot, no verdict.

    The old arrow_already_decided block is gone (a pre-baked conclusion anchors
    the read); the deterministic arrow still computes and still draws on the
    chart, it just never enters this JSON. Fields are grouped by force (scale /
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
             "session_low": min(path) if path else None,
             "session_high": max(path) if path else None,
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
    reads = [r for r in _read_jsonl(_reads_dir() / f"{day}.jsonl")
             if r.get("era") == ERA]
    prev = reads[-1] if reads else None
    # the last row that actually spent a call — the wake gate measures drift
    # from the last LOOK, never from the last write (see should_wake)
    calls = [r for r in reads if r.get("wall_s") is not None]
    last_call = calls[-1] if calls else None

    spoken = sum(1 for r in reads if (r.get("arrow") or {}).get("dir"))
    if len(calls) >= DAILY_CALL_CAP:
        print(f"sndk-read :: daily cap {DAILY_CALL_CAP} reached")
        return 0

    # The arrow is pure Python over a row already on disk, so it costs nothing
    # and is recomputed EVERY run — the chart is never showing a stale direction
    # just because the model was asleep. Only the READING is wake-gated, because
    # only the reading costs a call.
    row = with_path(row, rows)
    band = magnet_band(row)
    arrow = aggregate(row, prev, now)
    frozen = frozen_fields(rows, now)
    wake = should_wake(row, prev_row, last_call, now, rows)

    # a direction that just appeared, vanished, or reversed is itself news and
    # must never wait for the clock — this is the one wake reason the book's own
    # fingerprint cannot express
    prev_dir = ((prev or {}).get("arrow") or {}).get("dir")
    if arrow["dir"] != prev_dir and not arrow.get("held_reversal"):
        wake = wake or ("arrow appeared" if arrow["dir"] else "arrow stood down")

    # sr-6: the pulse check. Nothing here ever asked how old the newest row
    # was — a dead scanner, a halt, or a feed refusing ticks all read as an
    # ordinarily quiet tape, and the HEARTBEAT wake would then spend a model
    # call narrating the same frozen board every 45 minutes. A stale book
    # never wakes the model (an unparseable timestamp counts as stale — fail
    # closed, like the feed's own no-quote rule). The row still lands, stamped
    # stale_book so an outage can never pool with genuine quiet; the arrow is
    # pure recomputation and stays. Manual --force remains a human override.
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
                        if scene_now is not now else frozen, rows, scene_now)
    out = {
        "ts": now.isoformat(), "era": ERA,
        "wake": ("stale_book" if stale and not force else (wake or "quiet")),
        "book_age_min": book_age,
        "book_asof": (row.get("meta") or {}).get("book_asof"),
        "scan_age_min": scan_age,
        "spot": row.get("spot"), "sigma": row.get("sigma"),
        "arrow": arrow, "magnet_band": band, "frozen": frozen,
        "spoke": spoken + (1 if arrow["dir"] else 0), "scans": len(rows),
        # wk-1: the state the NEXT gate compares against. Written on every read
        # row, not only on the ones that spend a call, so a restart mid-session
        # cannot leave the gate with nothing to measure from.
        "gate": gate_state(row),
        "reading": None, "model": None, "wall_s": None, "error": None,
    }

    if not wake and not force:
        # still write the row: the arrow is current, the reading simply carries
        # forward. A hidden surface must never read as a missing one.
        # Age is measured from when the SENTENCE was written, not from the last
        # row. Every quiet scan copies the reading forward, so measuring off the
        # previous row reset the age to the ~2-minute scan gap on every carry —
        # a sentence written at 10:00 still read "2m old" at 15:00, and the
        # card's own >10m dim could never fire. reading_ts rides with the text.
        out["reading"] = (prev or {}).get("reading")
        out["reading_ts"] = (prev or {}).get("reading_ts")
        rts = _parse_ts(out["reading_ts"])
        out["reading_age_min"] = (int((now - rts).total_seconds() // 60)
                                  if rts else None)
        atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
        print(f"sndk-read :: quiet | arrow={arrow['dir'] or 'silent'} "
              f"(reading carried forward)")
        return 0

    # PAUSED: the wake gate fired and we record that it fired — the row keeps its
    # arrow, its gate separations, its magnet ranking and the reason it woke, so
    # the training set has no hole where the reasoning would have been. Only the
    # sentence is withheld, and the row says so rather than looking like a scan
    # that had nothing to say.
    if not reasoning_on():
        out["paused"] = True
        out["reading"] = None
        atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
        print(f"sndk-read :: PAUSED | {wake} | arrow={arrow['dir'] or 'silent'} "
              f"| row recorded, no model call")
        return 0

    if dry:
        print(json.dumps({k: out[k] for k in
                          ("ts", "wake", "spot", "arrow", "magnet_band", "frozen")},
                         indent=1, default=str))
        print("--- scene handed to the model ---")
        print(json.dumps(scene, indent=1, default=str))
        return 0

    prompt = ("Read this scene cold and reply with the JSON object only.\n\n"
              "SCENE:\n" + json.dumps(scene, default=str))
    obj, err, wall = _ask(prompt, PINNED_MODEL)
    out["wall_s"], out["model"] = wall, PINNED_MODEL
    if err or not isinstance(obj, dict):
        out["error"] = err or "unparseable reply"
    else:
        out["reading"] = _validate_reading(obj)
        out["reading_ts"] = now.isoformat()   # stamped once, carried forward after
        out["reading_age_min"] = 0
    atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
    if out["reading"]:
        # one RAG slice per completed read (payload + what the model concluded).
        # Fail-open: memory must never cost a read row.
        try:
            import sndk_rag
            sndk_rag.record_slice(row, out, scene, now)
        except Exception:
            pass
    r = out["reading"] or {}
    print(f"sndk-read :: {wake} | arrow={arrow['dir'] or 'silent'} | "
          f"vector={r.get('vector', '—')}"
          f"{' ' + format(r.get('magnitude_sigma'), '.2f') + 'σ' if r.get('magnitude_sigma') is not None else ''} | "
          f"{wall}s | {r.get('line', out['error'] or '')}")
    return 0


def replay(day: str) -> int:
    """Re-run the gate over a recorded day. Writes nothing — this is how the
    wake rate and the arrow count were measured before shipping."""
    rows = [r for r in _read_jsonl(_diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK"
            and not (r.get("meta") or {}).get("forced")]   # same rule as live
    if not rows:
        print(f"no rows for {day}")
        return 1
    prev = last_call = None
    wakes = arrow_scans = flips = 0
    from collections import Counter
    why = Counter()
    for i, row in enumerate(rows):
        now = _ts(row)
        if now is None:
            continue
        row = with_path(row, rows[:i + 1])
        a = aggregate(row, prev, now)
        if a["dir"]:
            arrow_scans += 1
        pd = ((prev or {}).get("arrow") or {}).get("dir")
        w = should_wake(row, rows[i - 1] if i else None, last_call, now)
        if a["dir"] != pd and not a.get("held_reversal"):
            w = w or ("arrow appeared" if a["dir"] else "arrow stood down")
            flips += 1
        prev = {"ts": row["ts"], "spot": row.get("spot"), "arrow": a,
                "magnet_band": magnet_band(row)}
        if not w:
            continue
        last_call = prev
        wakes += 1
        why[w] += 1
        print(f"  {row['ts'][11:16]}  {w:18s} spot={row.get('spot'):>9}  "
              f"arrow={(a['dir'] or '—'):6s} {a['silent_because'] or ''}"
              f"{'  ⚠ ' + a['caution'] if a.get('caution') else ''}")
    print(f"\n{day}: {wakes} model calls / {len(rows)} scans "
          f"({wakes/len(rows)*100:.0f}%) · arrow live on {arrow_scans} scans "
          f"({arrow_scans/len(rows)*100:.0f}%) · {flips} changes · {dict(why)}")
    return 0


if __name__ == "__main__":
    if "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        sys.exit(replay(sys.argv[i + 1]))
    sys.exit(read_once(force="--force" in sys.argv, dry="--dry" in sys.argv))
