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

ERA = "sr-3"                # bump on ANY change to the gates or the prompt.
                            # The store is era-stamped so a later read of the
                            # history can never blend two rule sets.
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
WAKE_PRICE_SIGMA = 0.15     # spot travelled this far since the last read
WAKE_MAGNET_SIGMA = 0.12    # magnet relocated this far = a different scene
HEARTBEAT_MIN = 45          # read at least this often even on a dead tape
MIN_GAP_MIN = 8             # spam guard: no two reads closer than this
DAILY_CALL_CAP = 40         # hard ceiling (busiest replayed day spent 32) — a
                            # runaway backstop, not a budget

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
MOMENTUM_ROWS = 5           # rolling window: last 5 scans (~10 min at 120s) —
                            # the blueprint's "3-5 snapshots"; 3 was twitch
                            # (gex_d p50 −0.04pp over 3 rows)
MOMENTUM_BUILD_PP = 0.5     # |Δ share| below this is "steady" (≈p75 of |gex_d|)
VOL_TREND_MIN = 28          # minutes of history before a 30-min IV read exists
VOL_TREND_MAX = 45          # a reference older than this is an outage artifact,
                            # not a 30-min window — the read is omitted instead
VOL_TREND_FLAT = 2.5        # |Δ IV| under this many vol pts reads "flat"
                            # (recorded Δ30m: p10 −9.0 / p50 +1.8 / p90 +12.6)
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
def should_wake(row: dict, prev_row: Optional[dict], prev: Optional[dict],
                now: datetime) -> Optional[str]:
    """Why this scan is worth a model READ, or None to stay asleep.

    `prev` must be the last row that actually spent a call, NOT simply the last
    row written. Those diverged the moment the arrow started being recomputed
    every scan: comparing against the last write made the min-gap test compare
    the book to itself two minutes ago, and the gate silently starved to ~1 call
    a day. Timing, drift and heartbeat are all measured from the last LOOK."""
    if prev is None:
        return "first read"
    last = _ts(prev)
    if last is None:
        return "first read"
    gap = (now - last).total_seconds() / 60.0
    if gap < MIN_GAP_MIN:
        return None
    sig = row.get("sigma") or 0
    p_spot = prev.get("spot")
    if sig and isinstance(p_spot, (int, float)) and row.get("spot") is not None:
        if abs(row["spot"] - p_spot) / sig >= WAKE_PRICE_SIGMA:
            return "price ran"
    pm = (prev.get("magnet_band") or {}).get("reported")
    cm = (row.get("gex_views") or {}).get("magnet")
    if sig and isinstance(pm, (int, float)) and isinstance(cm, (int, float)):
        if abs(cm - pm) / sig >= WAKE_MAGNET_SIGMA:
            return "magnet moved"
    if prev_row is not None:
        if row.get("regime") != prev_row.get("regime"):
            return "regime changed"
        if row.get("gamma_sign") != prev_row.get("gamma_sign"):
            return "gamma sign flipped"
        for k, word in (("call_wall", "call wall"), ("put_wall", "put wall")):
            a, b = row.get(k), prev_row.get(k)
            s = row.get("spot")
            if (isinstance(a, (int, float)) and isinstance(b, (int, float))
                    and isinstance(s, (int, float)) and a != b
                    and (s - a) * (s - b) < 0):
                return f"{word} crossed"
    if gap >= HEARTBEAT_MIN:
        return "heartbeat"
    return None


# ---------------------------------------------------------------------------
# the model call — sr-2: the model infers its OWN vector from an unbiased scene
# ---------------------------------------------------------------------------
_DOCTRINE = f"""You read one stock's dealer-positioning snapshot COLD and infer a direction vector and a magnitude from the evidence — nothing in the scene is a conclusion, and nobody has decided anything for you. Write for a smart reader who does not know options jargon.

THE INSTRUMENT. This is SNDK, a single stock, not an index. Its sigma (a typical day's move) runs 8-10% of the share price — enormous. It has weekly expiries, not daily, so most days have no expiry at all. Standing levels come from open interest, which is yesterday's positioning about a stock that can move 12% in a session.

HOW TO READ THE SCENE (grouped by force, not by metric):
- scale: the sigma ruler, plus aem — today's likely range split unevenly toward the side the options market fears (from put/call IV skew). It breathes with vol.
- regime: the day's character. vol_trend says whether implied vol is rising or falling NOW — it is the switch that arms vanna and charm. flip shows the chop band (ct=upper edge, pt=lower edge, center) and where price sits in it — price_in_band words like "clear negative" describe WHERE PRICE SITS relative to the dealer-hedging flip, a location on the map, not a bearish or bullish stamp. ONE measured number lives here: the center IS the gamma flip, and ct/pt are that center plus and minus a fixed 0.25 sigma — the edges are arithmetic, not three independent levels, so three of them agreeing is one witness, not three. charm is time-decay hedging: magnitude (uncalibrated, compare day to day) and the strike it funnels toward late in the session — a level, never a promised direction.
- magnet: strikes pulling price, with the tie told honestly. gap_pp small = no strike in charge.
- momentum: change over the stated window, top magnet strikes only — building vs fading beats any static level. (OI deltas and true order-flow CVD are not measured here; absent means unmeasured, never zero.)
- dealer_flow: standing dealer positioning. dex net is signed $-delta in billions from an assumed-sign book (positive = dealers estimated net LONG delta); the LEVEL is structural and slow, so net_change_30min is the part that can actually be news. vanna (in $M per vol point) only matters when vol_trend is moving.
- walls: standing structure, up to two per side, nearest first — walls.call[0]/put[0] are the strongest near ceilings/floors; the second wall says what is behind the first (right there, or open air). gex is the wall's share of total book gamma in percent. Walls are NOT the magnet strikes.
- price.vwap_dist_sigma is the volume-anchored level, and the only level on this board that is not derived from the flip.
- ANY missing field was not cleanly measured this scan. Treat absence as "no data", never as neutral, never as zero.

WHEN TO REACH OUTSIDE THE SCENE (both optional, most reads need neither):
- History: run `{_RAG_CMD} query --help` for narrative recall, and `{_RAG_CMD} series --help` for the numeric spot/magnet/walls series by time (and per-strike with --strike). Reach for them when something does not add up from the live snapshot alone — price testing a level unseen today (history.level_unseen_today flags it), a level acting out of character, or a cross-day question ("has 1300 held before?"). Day tier = today's slices + day summaries; month tier = standing terrain. Exact asks go through the numbers, not the text: past sessions answer to --date/--from-date/--to-date, --min-move (signed %: -5 = fell 5%+), and --near-strike (matches any day that traded through the level) — use --text only for meaning ("rejected the wall"), never for dates or biggest/worst. History is context, never the trigger — the live scene stays primary.
- Outside world: when the tape is genuinely abnormal (history.abnormal_tape, an outsized gap or move), a human would ask WHY — use WebSearch for the catalyst (earnings, macro, news) or sector context. Extreme cases only, not every scan.

HONESTY RULES, all of them load-bearing:
- Never cite a field listed in frozen_do_not_cite as the reason for anything new. If the magnet has not moved in two hours, it is not news.
- The magnet is usually a TIE. When gap_pp is small, no strike is really in charge — say so rather than naming one.
- Never invent a level. If price is past the last named level on the board, say exactly that — on this name it is often true.
- A typical 30-minute move here is about 0.08 sigma. Your magnitude must respect that: several times 0.08 needs a named force behind it, and pointing at a level 0.6 sigma away is a landmark, not a 30-minute target.
- The one pattern this tape has shown any short-horizon stability in is MEAN REVERSION: a vector pointing the same way price just travelled (moved_last_30min_sigma) is the least trustworthy kind and needs extra evidence beyond the move itself.
- "none" is an honest vector. When the evidence genuinely balances, say what is balanced instead of forcing a lean.

OUTPUT. Reply with ONLY a JSON object, no prose around it, no code fence:
{{"vector": "up" | "down" | "none",
 "magnitude_sigma": <expected travel over the next ~30 minutes, in sigma — omit when vector is "none">,
 "line": "<one sentence, max 24 words: the read and the force behind it>",
 "breaks_if": "<one short clause naming the specific level or condition that would flip this read>",
 "cited": "<the one or two fields and numbers you leaned on>"}}
Keep it under 100 words total. Plain words. No jargon, no hedging stacks, no restating numbers you were given."""


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
    hvl on this book). price_in_band is position from the ladder's own zone
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
    out = {"ct_sigma": ct_s, "pt_sigma": pt_s, "center_sigma": center_s,
           "price_in_band": pos}
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
    out = {"magnitude": round(abs(cex) / 1e6, 2)}
    if isinstance(fl.get("charm_wall"), (int, float)):
        out["drift_toward"] = fl["charm_wall"]
    return out


def momentum_block(rows: list[dict], band: dict) -> Optional[dict]:
    """Snapshot-over-snapshot deltas for the top magnet strikes over the last
    MOMENTUM_ROWS scans. gex_d = the strike's share of front-book gamma mass,
    in pp (scale-free — raw gamma is unit-opaque); vol_d = gross contracts
    traded there since the reference scan, only when the strike sat inside
    both windows (a window shift is the telescope, not flow).

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
        entry = {"gex_share_d_pp": round(gex_d, 2)}
        if k in vg_c and k in vg_r:            # _pairs already guarantees finite
            entry["vol_d"] = int(vg_c[k] - vg_r[k])
        vol_d = entry.get("vol_d")
        # symmetric evidentiary standard: fading earns its hedge exactly the
        # way building does (an unhedged "fading" was a one-way tilt)
        if gex_d >= MOMENTUM_BUILD_PP:
            entry["read"] = ("building" if (vol_d or 0) > 0
                             else "building (vol unconfirmed)")
        elif gex_d <= -MOMENTUM_BUILD_PP:
            entry["read"] = ("fading" if (vol_d or 0) > 0
                             else "fading (vol unconfirmed)")
        else:
            entry["read"] = "steady"
        by[f"{k:g}"] = entry
    if not by:
        return None
    span = int((t_c - t_r).total_seconds() // 60)
    return {"window": f"last {MOMENTUM_ROWS} scans ({span}m)", "by_strike": by}


def dealer_flow_block(rows: list[dict]) -> Optional[dict]:
    """Second-order dealer lean, one place. dex = standing signed $-delta
    (billions; + = dealers net long delta, sign convention glossed once in
    the doctrine) plus its timestamp-true 30-min change. vanna = front-book
    net ($M per vol point), armed by vol_trend. Each field measured-or-absent;
    an empty block is dropped, never shipped hollow.

    NO LEAN WORD (adversarial audit 08-02): a sign-derived word read
    "long delta" on 756/756 simulated scenes — the exact banned pattern the
    module header documents (a constant that reads like a signal manufactures
    conviction, in one direction, forever). The blueprint's own frame is that
    the CHANGE is the signal, so the change is what ships; the model can read
    the sign off the signed number without being handed a conclusion."""
    if not rows:
        return None
    row = rows[-1]
    out = {}
    net = _fin((row.get("dex_views") or {}).get("net_dex_total"))
    if net is not None:
        dex = {"net": round(net / 1e9, 2)}
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
                    dex["net_change_30min"] = round((net - ref) / 1e9, 2)
                break
        out["dex"] = dex
    vex = _fin((row.get("flows_front") or {}).get("vex"))
    if vex is not None:
        out["vanna"] = {"net": round(vex / 1e6, 2),
                        "note": "most alive on IV moves"}
    return out or None


def walls_ladder(row: dict, sd) -> Optional[dict]:
    """Laddered standing walls, up to WALLS_PER_SIDE a side, NEAREST first —
    walls.call[0]/put[0] are the old gwc/gwp (same clustering rule the ladder
    used: gw_vocab.cluster_walls on the measured net_by_strike surface).
    `gex` = the cluster's share of total book |γ| in percent — scale-free, so
    the model can weigh wall against wall without unit-opaque raw gamma. The
    denominator is the FULL net_by_strike surface, not the surviving clusters:
    a cluster-only total shifts scan-to-scan as strikes cross the 25%
    concentration floor, so "the wall's share rose" could be pure denominator
    churn (SE review 08-02)."""
    spot = row.get("spot")
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

    def entry(c):
        e = {"strike": c["peak"], "sigma": sd(c["peak"]),
             "gex": round(c["strength"] / tot * 100.0, 1)}
        return {k: v for k, v in e.items() if v is not None}
    calls = sorted((c for c in clusters if c["side"] == "call" and c["peak"] > spot),
                   key=lambda c: c["peak"])[:WALLS_PER_SIDE]
    puts = sorted((c for c in clusters if c["side"] == "put" and c["peak"] < spot),
                  key=lambda c: -c["peak"])[:WALLS_PER_SIDE]
    out = {}
    if calls:
        out["call"] = [entry(c) for c in calls]
    if puts:
        out["put"] = [entry(c) for c in puts]
    return out or None


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
        out["level_unseen_today"] = True
    sig, vs = _fin(row.get("sigma")), _fin(vs_prior_pct)
    sigma_pct = (sig / spot * 100.0) if (sig and spot) else None
    day_bar = (ABNORMAL_DAY_SIGMA * sigma_pct if sigma_pct
               else ABNORMAL_PCT_FLOOR)
    if (vs is not None and abs(vs) >= day_bar) or \
            ((r30 := _fin(ran_30m)) is not None and abs(r30) >= 0.5):
        out["abnormal_tape"] = True
    return out or None


def build_scene(row: dict, band: dict, frozen: list,
                rows: list[dict], now: datetime) -> dict:
    """The model's view of the world — sr-2: an UNBIASED snapshot, no verdict.

    The old arrow_already_decided block is gone (a pre-baked conclusion anchors
    the read); the deterministic arrow still computes and still draws on the
    chart, it just never enters this JSON. Fields are grouped by force (scale /
    regime / magnet / momentum / dealer_flow / walls), and everything the
    constancy audit found frozen across 756 rows stays omitted rather than
    nulled — 'absent' and 'zero' must never look alike, and a field that
    cannot vary cannot inform. Same rule for every new block: not cleanly
    computed → absent."""
    spot = _fin(row.get("spot"))
    sig = _fin(row.get("sigma")) or 0

    def sd(v):
        v = _fin(v)
        if v is None or not sig or spot is None:
            return None
        return round((v - spot) / sig, 2)

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
    # 0.81σ (adversarial audit 08-02).
    ran_30m = _fin(row.get("_ran_30m_sigma"))
    moved_30m = round(ran_30m, 2) if ran_30m is not None else None

    # scale — the rulers: sigma + aem (asymmetric, from the rebuilt IV smile)
    scale = {"one_sigma_dollars": round(sig, 2) if sig else None,
             "sigma_pct_of_price": round(sig / spot * 100, 1)
             if sig and spot else None,
             "typical_30min_move_sigma": 0.08}
    skew = row.get("iv_skew") if isinstance(row.get("iv_skew"), dict) else {}
    em = _fin((row.get("range_ruler") or {}).get("em_points"))
    d = _fin(skew.get("down_share"))
    if em is not None and em > 0 and d is not None:
        scale["aem"] = {
            "up_dollars": round(2 * em * (1 - d), 2),
            "down_dollars": round(2 * em * d, 2),
            "skew": ("downside" if d >= 0.52 else
                     "upside" if d <= 0.48 else "balanced"),
            "source": "put/call IV skew"}

    regime = {"gamma_sign": row.get("gamma_sign"), "word": row.get("regime")}
    vt = vol_trend(rows, now)
    if vt:
        regime["vol_trend"] = vt
    fb = flip_block(row, sd, ran_30m)
    if fb:
        regime["flip"] = fb
    cb = charm_block(row)
    if cb:
        regime["charm"] = cb

    price = {"now": spot,
             "vs_prior_close_pct": vs_prior,
             "session_low": min(path) if path else None,
             "session_high": max(path) if path else None,
             "moved_last_30min_sigma": moved_30m}
    vw = sd(row.get("vwap"))
    if vw is not None:
        price["vwap_dist_sigma"] = vw

    def prune(d):
        # omit-never-null INSIDE the blocks too: an early-session
        # moved_last_30min or a missing prior_close must vanish, not read as
        # null — the doctrine promises "missing = not measured", and a null
        # keep-field would break that promise (fidelity audit #5, 08-02)
        return {k: v for k, v in d.items() if v is not None}

    scene = {
        "instrument": "SNDK",
        "clock": {"minutes_since_open": int((now - now.replace(
            hour=9, minute=30, second=0, microsecond=0)).total_seconds() // 60)},
        "scale": prune(scale),
        "price": prune(price),
        "regime": prune(regime),
        # magnet: evidence only when a book was measured. An empty/absent book
        # must not ship a bare {"is_a_tie": true} (a manufactured "no strike
        # in charge" claim), and a SINGLE-strike book — maximal dominance —
        # must not read as a tie either: with no runner-up the tie question is
        # unanswerable, so is_a_tie/gap_pp are omitted (adversarial audit 08-02).
        "magnet": (prune({
            "is_a_tie": band["tie"] if len(band["top"]) >= 2 else None,
            "gap_pp": band["gap_pp"] if len(band["top"]) >= 2 else None,
            "top_strikes": band["top"],
            "sigma_from_spot": sd(band["top"][0][0])})
            if band["top"] else None),
        "momentum": momentum_block(rows, band),
        "dealer_flow": dealer_flow_block(rows),
        "walls": walls_ladder(row, sd),
        "history": history_flags(row, rows, vs_prior, ran_30m),
        "frozen_do_not_cite": [f"{f['field']} unchanged {f['for_min']}m"
                               for f in frozen],
    }
    return {k: v for k, v in scene.items() if v not in (None, {}, [])}


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
    wake = should_wake(row, prev_row, last_call, now)

    # a direction that just appeared, vanished, or reversed is itself news and
    # must never wait for the clock — this is the one wake reason the book's own
    # fingerprint cannot express
    prev_dir = ((prev or {}).get("arrow") or {}).get("dir")
    if arrow["dir"] != prev_dir and not arrow.get("held_reversal"):
        wake = wake or ("arrow appeared" if arrow["dir"] else "arrow stood down")

    scene = build_scene(row, band, frozen, rows, now)
    out = {
        "ts": now.isoformat(), "era": ERA, "wake": wake or "quiet",
        "spot": row.get("spot"), "sigma": row.get("sigma"),
        "arrow": arrow, "magnet_band": band, "frozen": frozen,
        "spoke": spoken + (1 if arrow["dir"] else 0), "scans": len(rows),
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
