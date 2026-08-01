#!/usr/bin/env python3
# Pinned interpreter: same venv as sndk_hunter.py.
"""
sndk_read.py — the SNDK chart's live reading. One wake = one read row.

WHAT THIS IS NOT: a ported Watchtower. The SPX tower is a forward forecaster
whose call IS the model's opinion. Here the split is deliberate and inverted:

    the ARROW is decided by a deterministic aggregator (this module, no model)
    the READING is written by the model (one terse call, no direction)

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

_ET = ZoneInfo("America/New_York")

ERA = "sr-1"                # bump on ANY change to the gates or the prompt.
                            # The store is era-stamped so a later read of the
                            # history can never blend two rule sets.
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

# --- the model call ----------------------------------------------------------
CALL_TIMEOUT_S = 60.0       # one attempt, no retries — a slow read is skipped
_NO_TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob",
             "Grep", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite",
             "ExitPlanMode", "BashOutput", "KillShell", "SlashCommand", "Skill")


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
    gv = row.get("gex_views") or {}
    mb = gv.get("mass_by_strike") or []
    spot = row.get("spot")
    tot = sum(v for _, v in mb if isinstance(v, (int, float)))
    if not mb or tot <= 0 or spot is None:
        return {"top": [], "gap_pp": None, "tie": True, "lo": None, "hi": None,
                "reported": gv.get("magnet"), "in_window": None}
    ranked = sorted(((float(k), float(v) / tot * 100.0) for k, v in mb),
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
# the model call — the READING only, never the direction
# ---------------------------------------------------------------------------
_DOCTRINE = """You read one stock's dealer-gamma map and say what you see, in plain English, for a smart reader who does not know options jargon.

WHAT YOU DO NOT DO: you never pick a direction. The arrow on this chart is decided by a deterministic aggregator before you are called, and it is handed to you already made. Do not argue with it, do not restate it as a prediction, do not add one of your own.

WHAT YOU DO: describe the terrain the reader is looking at, and name the one thing that would change the picture.

THE INSTRUMENT. This is SNDK, a single stock, not an index. Its sigma (a typical day's move) runs 8-10% of the share price — enormous. It has weekly expiries, not daily, so most days have no expiry at all. Levels here come from open interest, which is yesterday's positioning about a stock that can move 12% in a session.

HONESTY RULES, all of them load-bearing:
- Never cite a field listed as frozen as the reason for anything new. If the magnet has not moved in two hours, it is not news.
- The magnet is usually a TIE. When the gap between the top two strikes is small, say so plainly ("no strike is really in charge") rather than naming one.
- If the aggregator is silent, the honest reading explains what is missing, and does NOT supply a direction to fill the gap.
- Never invent a level. If price is past the last named level on the board, say exactly that — on this name it is often true.
- Distances are what matter, and a typical 30-minute move here is about 0.08 sigma while the named levels sit 0.6 sigma away. Do not imply price is about to reach something eight times further than it usually travels.

OUTPUT. Reply with ONLY a JSON object, no prose around it, no code fence:
{"line": "<one sentence, max 22 words, what the terrain looks like right now>",
 "breaks_if": "<one short clause naming the specific level or condition that would change this read>",
 "cited": "<the one field and number you leaned on, e.g. 'resistance up 1.47 vs down 0.48'>"}
Keep it under 80 words total. Plain words. No jargon, no hedging stacks, no restating the numbers you were given."""


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
    """ONE `claude -p` call. Tool-less by construction (the read never needs a
    tool, so it stops paying to ship their schemas — measured -32% tokens on the
    SPX path). Doctrine rides --append-system-prompt so it stays byte-identical
    all day and is served from cache; the varying scene rides the body.

    Returns (obj | None, error | None, wall_seconds)."""
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
           "--append-system-prompt", _DOCTRINE,
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
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


def build_scene(row: dict, arrow: dict, band: dict, frozen: list,
                rows: list[dict], now: datetime) -> dict:
    """The model's view of the world. Deliberately SMALL and free of constants.

    Everything the constancy audit found frozen across 756 rows is omitted
    rather than nulled — 'absent' and 'zero' must never look alike, and a field
    that cannot vary cannot inform."""
    spot = row.get("spot")
    sig = row.get("sigma") or 0

    def sd(v):
        if not isinstance(v, (int, float)) or not sig or spot is None:
            return None
        return round((v - spot) / sig, 2)

    # the day's own path, which the old design never handed over at all
    path = [r.get("spot") for r in rows if isinstance(r.get("spot"), (int, float))]
    ladder = row.get("profile_ladder") or {}
    named = {k: sd(ladder.get(k)) for k in ("gwc", "ct", "hvl", "pt", "gwp")
             if isinstance(ladder.get(k), (int, float))}
    named = {k: v for k, v in named.items() if v is not None}

    scene = {
        "instrument": "SNDK",
        "clock": {"minutes_since_open": int((now - now.replace(
            hour=9, minute=30, second=0, microsecond=0)).total_seconds() // 60)},
        "scale": {"one_sigma_dollars": round(sig, 2) if sig else None,
                  "sigma_pct_of_price": round(sig / spot * 100, 1)
                  if sig and spot else None,
                  "typical_30min_move_sigma": 0.08},
        "price": {"now": spot,
                  "vs_prior_close_pct": round(
                      (spot / row["prior_close"] - 1) * 100, 2)
                  if row.get("prior_close") and spot else None,
                  "session_low": min(path) if path else None,
                  "session_high": max(path) if path else None,
                  "moved_last_30min_sigma": (
                      round((spot - path[-15]) / sig, 2)
                      if sig and len(path) >= 15 else None)},
        "regime": {"gamma_sign": row.get("gamma_sign"),
                   "word": row.get("regime")},
        "magnet": {"is_a_tie": band["tie"], "gap_pp": band["gap_pp"],
                   "top_strikes": band["top"],
                   "sigma_from_spot": sd(band["top"][0][0]) if band["top"] else None},
        "named_levels_sigma_from_spot": named,
        "lowest_named_level": (min(named.values()) if named else None),
        "arrow_already_decided": {"direction": arrow["dir"],
                                  "silent_because": arrow["silent_because"],
                                  "layers": [{"name": l["name"], "dir": l["dir"],
                                              "cite": l["cite"],
                                              "separated": l["admissible"]}
                                             for l in arrow["layers"]]},
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
    rows = [r for r in _read_jsonl(_diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK"]
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

    scene = build_scene(row, arrow, band, frozen, rows, now)
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

    prompt = ("Read this dealer-gamma scene and reply with the JSON object only.\n\n"
              "SCENE:\n" + json.dumps(scene, default=str))
    obj, err, wall = _ask(prompt, PINNED_MODEL)
    out["wall_s"], out["model"] = wall, PINNED_MODEL
    if err or not isinstance(obj, dict):
        out["error"] = err or "unparseable reply"
    else:
        # keep only the three fields; a model that writes extra keys does not get
        # to smuggle a direction in through one of them
        out["reading"] = {k: str(obj.get(k, ""))[:400]
                          for k in ("line", "breaks_if", "cited")
                          if obj.get(k)}
        out["reading_ts"] = now.isoformat()   # stamped once, carried forward after
        out["reading_age_min"] = 0
    atomic_io.append_jsonl(_reads_dir() / f"{day}.jsonl", out)
    r = out["reading"] or {}
    print(f"sndk-read :: {wake} | arrow={arrow['dir'] or 'silent'} "
          # not "both layers agree" — there is no agreement step, and saying so
          # in the log is the same false-confidence the design removed
          f"({arrow['silent_because'] or 'magnet separated, breadth clear'}) | "
          f"{wall}s | {r.get('line', out['error'] or '')}")
    return 0


def replay(day: str) -> int:
    """Re-run the gate over a recorded day. Writes nothing — this is how the
    wake rate and the arrow count were measured before shipping."""
    rows = [r for r in _read_jsonl(_diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK"]
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
