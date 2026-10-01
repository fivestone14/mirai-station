"""The learning loop: how far to trust JEV's sum against a price-only reference, learned a day at a time.

The design is 06-learning-loop-design (revised 2026-09-26). At every live read a handful of forecasts
("experts") of where SPX ends each horizon are written down beside the sum (snapshot); once a session
is sealed, the experts' weights move by each one's loss over the day (update). The e-processes bet once a
day, a day being one piece of evidence for a test; the weights and the tables learn from every read, since
at 30 minutes the reads of a day are close to independent (09-30 review).

The reference R is the frozen time-of-day odds (baseline.py; the movement-so-far split lost the
validation and is dropped) calibrated by a three-number decaying table of what actually happened.
The experts, per horizon:

    jev_share_S   S * J + (1 - S) * R for S in JEV_SHARES, J being JEV's sum with its unsure mass spread
                  by R: the fixed JEV-trust mixes; jev_share_0.0 is R itself
    blend50       today's half-and-half blend with the live clock, JEV's unsure mass spread by the clock
    baseline      the frozen time-of-day odds, uncalibrated
    baseline_cal  the same, calibrated (the same forecast as R)
    questions     the question block: "no change" (R) against one expert per live question, each R tilted
                  by how often that question's answers came before each outcome, mixed by their own weights

The pool is a weighted average of the experts, kept apart for the two things a forecast says: will
price move (P(move), the move weights) and, given a move, which way (P(up | move), the direction
weights), each scored on its own part of the log loss (scores.py). A question with no answer yet
today is asleep and the block is mixed over the awake members only; a held answer is awake, since it
was given before the outcome.

The update, per horizon, on each sealed session after the last one applied: the reads' window
coverage, each expert's day-mean move and direction loss, an exponential-weights step against R (in the
block, against "no change") of ETA per read times the day's coverage times the day-mean gap, the gap
clipped to GAP_CLIP, Fixed-Share toward the prior, the block's per-question cap, the decaying tables
(counting every read, a question's tilt centred on its own mean so a miss of R's every answer shares
cancels), and day-level betting e-processes, one bet a day, a question's scored only over the reads it was
awake on in its current version, against "no change" on those same reads (the weights step keeps its asleep
reads, forecast by the awake members' mix, as sleeping experts do). A state learned under other constants
is kept in archive/ and learned again from the records, the sessions it had applied replayed without bets
(update, reformed); nothing is ever added to it. A question is labelled earning only by e-BH
over every question version ever tested; the pool becomes eligible for the phone only once its
e-processes on PROMOTION_TESTS (against the exact blend, the blend with its unsure mass spread, and the pool at
its starting weights) all reach PROMOTE_E after MIN_DAYS days, so beating the exact blend on its unsure
accounting alone, or on the mix it began with, earns nothing; and it steps back on the same kind of test. It
runs at the first grading run after a session ends (PoolWeights.learn).

The reference's two experts and its raw odds on a snapshot (``raw_baseline``) are named by the
reference's source (w0): ``baseline`` here, ``clock`` in the average-price loop (integral_loop.py),
whose reference is the live time-of-day odds. The state and every snapshot keep the reference's
version as ``reference_version``. A state or a snapshot written before these names (``clock``,
``clock_cal``, ``raw_clock`` and ``baseline`` for the frozen odds and their version, until
2026-09-29) is read under them (renamed, legacy_snapshot).

Nothing here changes JEV's prompt: every live question keeps its sentence and weighs 1.0 in step 3.
The end-price sums kept beside the call keep today's exact 50/50 blend until this loop's pool is
promoted (at least MIN_DAYS days, its e-processes on PROMOTION_TESTS at PROMOTE_E, and SIM_GATES_PASSED,
the simulation 06 requires first); then they show the pool, the blend kept beside it, and go back to
the blend by itself when the demotion e-process reaches DEMOTE_E (shown). The call the phone shows,
the average-price sum, follows the average-price loop's own promotion by the same rules on the
average-price grade (integral_loop.shown), never this one's. POOL_ON_PHONE turns both off, and is off since
2026-09-30: JEV's own read leads the phone, and both loops' evidence is kept in the background.

A lane graded from the settled open keeps its own loop state in its own folder, on its sums from the
settled open. Every one of its reads forecasts the same window, the half hour after 09:34, so each
counts the same in its day rather than by the share of a window no earlier read covered. The premarket
lane does not run the loop (lane.PREMARKET.pool): its sums are not blended, so no read has the clock
blend50 needs, and no price-only reference for the window from the settled open has been validated
(clock.py).

Files, beside the lane's records:
    pool_30.json, pool_60.json   the state per horizon: weights, tables, e-processes, statuses, the watermark
                                 (pool_10.json and pool_30.json for sums of 10 and 30 minutes)
    pool_log.jsonl               one line per horizon per session applied or refused, with its manifest
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .lane import LIVE, Lane
from .scores import EPS, OUTCOMES, direction_of, floored, from_move_direction, log_loss, losses, move_of
from .sessions import SESSION_CLOSE, session_close
from .state_builder import ET, load_jsonl, parse_ts
from .weights import QuestionWeights

if TYPE_CHECKING:
    from .baseline import Baseline

# a promoted pool never takes over the phone: JEV's own read leads it and the loop's numbers stay in the background
# (Will's decision, 2026-09-30, replacing those of 2026-09-28 and 29)
POOL_ON_PHONE = False
# 06 requires the spec-exact simulation's acceptance gates to pass before any promotion; they are not
# built, so the promotion evidence builds up but the pool is not promoted until this is set
SIM_GATES_PASSED = False

JEV_SHARES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
REFERENCE = "jev_share_0.0"
SOURCE = "baseline"             # this loop's reference: the frozen time-of-day odds (baseline.py)


def w0(source: str) -> dict[str, float]:
    """The experts' prior weights, the reference's two named by its ``source`` (``baseline`` or ``clock``)."""
    return {**{f"jev_share_{s:.1f}": 0.05 for s in JEV_SHARES}, "blend50": 0.30, source: 0.05, f"{source}_cal": 0.05, "questions": 0.30}


W0 = w0(SOURCE)
NO_CHANGE = "no_change"         # the block's reference member: R, untilted
NO_CHANGE_PRIOR = 0.20          # its share of the block's prior; the questions share the rest
# reads a day are close to independent at 30 minutes (09-30 review: within-day correlation of the loss gaps about 0),
# so a weight learns from every read: a day's step is ETA per read times the day's read count (S_day, its window
# coverage) times the day-mean loss gap, the gap clipped first so one wild day cannot move a weight far
ETA = 0.25                      # per read; 1.0 per read over-reacts (09-30 review)
GAP_CLIP = 0.75                 # a day-mean loss gap is clipped to this many nats before it moves a weight
ALPHA = 0.02                    # Fixed-Share: every member keeps at least ALPHA of its prior
BLOCK_CAP = 0.25                # the most one question may hold of the block
TABLE_DECAY = 0.98              # the question tables, about a 34-session half life
CAL_DECAY = 0.95                # the calibration table, about 14 sessions
PRIOR_DAYS = 5.0                # the calibration's prior, in days
TABLE_PRIOR_READS = 40.0        # the question tables' prior, in reads: the tables count every read
DAY_SCORE_BOUND = 0.25          # a day's loss difference is clipped to this many nats before it is bet on; 0.25 is the
                                # floor: at 0.15 or under, thin days promote a worse pool (09-30 review)
BET_CAP = 0.5
BET_PRIOR_DAYS, BET_PRIOR_VARIANCE = 5.0, 0.25
PROMOTE_E, DEMOTE_E, VETO_E = 20.0, 10.0, 10.0
MIN_DAYS = 20
EBH_LEVEL = 0.10
SUPPRESS_E, LIFT_E = 20.0, 5.0
# the freeze: the weights stop for review when GAP_CLIP binds on more than CAP_BIND_LIMIT of the pool's expert-days over
# CAP_BIND_WINDOW sessions (named for the day-step cap the clip replaced: renaming them would change CONSTANTS and the state)
CAP_BIND_WINDOW, CAP_BIND_LIMIT = 20, 0.05
ROUND = 12
LOG_NAME = "pool_log.jsonl"
SHOWN_BLEND, SHOWN_POOL = "blend50_exact", "pool_v1"
# promotion needs the pool to beat, at PROMOTE_E each: the exact blend as the phone showed it, the blend with its unsure
# mass spread (so unsure accounting alone earns nothing), and itself at its starting weights (so the learning must count)
PROMOTION_TESTS = ("promote", "promote_spread", "promote_learned")
SIDES = ("M", "D")              # the move weights and the direction weights

CONSTANTS = {"eps": EPS, "eta": ETA, "alpha": ALPHA, "gap_clip": GAP_CLIP, "block_cap": BLOCK_CAP,
             "table_decay": TABLE_DECAY, "cal_decay": CAL_DECAY, "prior_days": PRIOR_DAYS, "table_prior_reads": TABLE_PRIOR_READS,
             "centred": True, "day_score_bound": DAY_SCORE_BOUND,
             "bet_cap": BET_CAP, "bet_prior_days": BET_PRIOR_DAYS, "bet_prior_variance": BET_PRIOR_VARIANCE,
             "promote_e": PROMOTE_E, "demote_e": DEMOTE_E, "veto_e": VETO_E, "min_days": MIN_DAYS, "ebh_level": EBH_LEVEL,
             "suppress_e": SUPPRESS_E, "lift_e": LIFT_E, "w0": W0, "no_change_prior": NO_CHANGE_PRIOR, "jev_shares": JEV_SHARES,
             "cap_bind_window": CAP_BIND_WINDOW, "cap_bind_limit": CAP_BIND_LIMIT}
CONSTANTS_HASH = hashlib.sha256(json.dumps(CONSTANTS, sort_keys=True).encode()).hexdigest()[:16]
CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
# the same constants under the names this loop's experts had before they were named by their source
LEGACY_NAMES = {"clock": "baseline", "clock_cal": "baseline_cal", "raw_clock": "raw_baseline"}
LEGACY_CONSTANTS_HASH = hashlib.sha256(json.dumps({**CONSTANTS, "w0": w0("clock")}, sort_keys=True).encode()).hexdigest()[:16]
# what forms the experts a rebuild keeps as a snapshot wrote them (reformed): the JEV mixes, the blend and the reference's
# calibration. Each snapshot keeps this fingerprint; one formed under other settings cannot be formed again faithfully,
# so a rebuild meeting it stops (fail closed) rather than reuse its experts
FORMING = {"eps": EPS, "jev_shares": JEV_SHARES, "blend_share": 0.5, "cal_decay": CAL_DECAY, "prior_days": PRIOR_DAYS}
FORMING_HASH = hashlib.sha256(json.dumps(FORMING, sort_keys=True).encode()).hexdigest()[:16]


def question_version(q: dict) -> str:
    """A question's version: its type, options and wording, the criteria JEV is sent in full (with the cuts
    filled into them) and the options in their order, which a score's list criteria are read by. Any change
    makes it a new question to the loop."""
    body = {"type": q.get("type"), "criteria": q.get("criteria"), "options": q.get("options"),
            "code_criteria": q.get("code_criteria"), "words": [q.get("ask"), q.get("instructions")]}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def soft_answer(entry: dict | None) -> dict[str, float] | None:
    """A question's answer as probabilities over its options, from what the card keeps of it (a choice's
    or a score's probabilities, or a yes/no's chance of yes), or None when it carries neither."""
    if not isinstance(entry, dict):
        return None
    p = entry.get("probabilities")
    if isinstance(p, dict):
        vals = {str(k): float(v) for k, v in p.items() if isinstance(v, (int, float)) and v >= 0}
        total = sum(vals.values())
        if total > 0:
            return {k: v / total for k, v in vals.items()}
    n = entry.get("noul")
    if isinstance(n, (int, float)) and 0 <= n <= 1:
        return {"true": float(n), "false": 1.0 - float(n)}
    return None


# ----------------------------------------------------------------------------- state

def _prob(logs: dict[str, float]) -> dict[str, float]:
    top = max(logs.values())
    ex = {k: math.exp(logs[k] - top) for k in sorted(logs)}
    s = math.fsum(ex.values())
    return {k: v / s for k, v in ex.items()}


def _logs(p: dict[str, float]) -> dict[str, float]:
    return {k: math.log(v) for k, v in p.items()}


def new_eprocess() -> dict:
    return {"e": 1.0, "n": 0, "sum": 0.0, "sum_sq": 0.0}


def _new_evidence() -> dict:
    return {"days": 0, "move": {"better": new_eprocess(), "worse": new_eprocess()},
            "direction": {"better": new_eprocess(), "worse": new_eprocess()},
            "status": {"move": None, "direction": None, "suppressed": False}}


def cold_state(source: str = SOURCE) -> dict:
    """Monday's state: every weight at its prior, empty tables, every e-value 1, the phone on the blend."""
    return {"constants_hash": CONSTANTS_HASH, "reference_version": None,
            "top": {side: _logs(w0(source)) for side in SIDES}, "block": {side: {NO_CHANGE: 0.0} for side in SIDES},
            "members": {}, "tables": {}, "cal": {"O": {k: 0.0 for k in OUTCOMES}, "E": {k: 0.0 for k in OUTCOMES}},
            "evidence": {}, "family": [], "archived": {},
            "phone": {"shows": "blend", "since": None, **{test: new_eprocess() for test in PROMOTION_TESTS},
                      "demote": new_eprocess(), "harm_60": new_eprocess()},
            "cap_binds": [], "frozen": None, "last_session_applied": None, "replay_until": None}


def state_path(out_dir: Path, minutes: int) -> Path:
    return Path(out_dir) / f"pool_{minutes}.json"


def load_state(out_dir: Path, minutes: int) -> dict:
    try:
        state = json.loads(state_path(out_dir, minutes).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cold_state()
    return renamed(state, LEGACY_NAMES, (LEGACY_CONSTANTS_HASH, CONSTANTS_HASH))


def renamed(state: dict, experts: dict[str, str] | None = None, hashes: tuple[str, str] | None = None) -> dict:
    """A saved state under the names it is read by now: its reference's version as ``reference_version`` (``baseline``
    before), and, learned under ``hashes[0]``, the same constants under the old names, its experts renamed by
    ``experts`` and its constants_hash ``hashes[1]``, so the loop carries on where it stood."""
    if "baseline" in state and "reference_version" not in state:
        state["reference_version"] = state.pop("baseline")
    if hashes and state.get("constants_hash") == hashes[0]:
        for side in SIDES:
            state["top"][side] = {(experts or {}).get(n, n): v for n, v in state["top"][side].items()}
        state["constants_hash"] = hashes[1]
    return state


def legacy_snapshot(snap: dict) -> dict:
    """A snapshot this loop wrote on a read before its experts were named by their source, under today's names."""
    if "raw_clock" not in snap:
        return snap
    out = {LEGACY_NAMES.get(k, k): v for k, v in snap.items() if k != "baseline"}
    out["experts"] = {LEGACY_NAMES.get(n, n): f for n, f in snap["experts"].items()}
    out["reference_version"] = snap.get("baseline")
    return out


def _rounded(x):
    if isinstance(x, float):
        return round(x, ROUND)
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rounded(v) for v in x]
    return x


def _canonical(state: dict) -> str:
    return json.dumps(_rounded(state), sort_keys=True, separators=(",", ":"))


def state_hash(state: dict) -> str:
    return hashlib.sha256(_canonical(state).encode()).hexdigest()[:16]


def save_state(out_dir: Path, minutes: int, state: dict) -> None:
    """Rounded to ROUND places and written with sorted keys and fixed separators, so a rebuild from the
    same records hashes the same; in one step, so a reader never sees half a file."""
    path = state_path(out_dir, minutes)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(_canonical(state), encoding="utf-8")
    os.replace(tmp, path)


def block_prior(members: list[str]) -> dict[str, float]:
    """The block's prior: NO_CHANGE_PRIOR for "no change", the rest shared by the questions."""
    if not members:
        return {NO_CHANGE: 1.0}
    return {NO_CHANGE: NO_CHANGE_PRIOR, **{q: (1.0 - NO_CHANGE_PRIOR) / len(members) for q in members}}


# ----------------------------------------------------------------------------- the forecasts at a read

def calibrated(base: dict[str, float], cal: dict, long_run: dict[str, float]) -> dict[str, float]:
    """base(k) * (O_k + m pi_k) / (E_k + m pi_k), renormalised: the base nudged by what happened against
    what it expected (day-normalised, decaying), with PRIOR_DAYS days of prior at its long-run shares."""
    raw = {k: base[k] * (cal["O"][k] + PRIOR_DAYS * long_run[k]) / (cal["E"][k] + PRIOR_DAYS * long_run[k]) for k in OUTCOMES}
    s = sum(raw.values())
    return {k: v / s for k, v in raw.items()}


def spread_unsure(p: dict, by: dict[str, float]) -> dict[str, float]:
    """A sum's probabilities over up, flat and down, its unsure mass spread in proportion to ``by``."""
    vals = {k: max(float(v), 0.0) for k, v in p.items() if isinstance(v, (int, float))}
    total = sum(vals.values())
    if total <= 0:
        return dict(by)
    u = vals.get("unsure", 0.0) / total
    return {k: vals.get(k, 0.0) / total + u * by[k] for k in OUTCOMES}


def tilted(r: dict[str, float], answer: dict[str, float], table: dict | None, long_run: dict[str, float]) -> dict[str, float]:
    """A question's expert, f(k) proportional to R(k) * T_a(k) / T_mean(k): R tilted by how often each answer came
    before each outcome against how often R expected it, t_o(k) = (C[o][k] + m r_k) / (X[o][k] + m r_k) with m =
    TABLE_PRIOR_READS, T_a(k) = sum over options o of a(o) t_o(k), centred on the question's own answer-weighted mean
    tilt T_mean(k) = sum over o of n_o t_o(k) / sum of n_o (n_o the reads behind option o). Centring keeps only what
    tells the question's answers apart: a miss of R's that every answer shares (R calling flat too rarely, say)
    cancels, so a question that always gives one answer is R. With empty tables it is R."""
    table = table or {}
    c_rows, x_rows = table.get("C", {}), table.get("X", {})

    def t(o: str, k: str) -> float:
        m = TABLE_PRIOR_READS * long_run[k]
        return (c_rows.get(o, {}).get(k, 0.0) + m) / (x_rows.get(o, {}).get(k, 0.0) + m)

    seen = {o: math.fsum(row.values()) for o, row in c_rows.items()}
    total = math.fsum(seen.values())
    raw = {}
    for k in OUTCOMES:
        mean = math.fsum(n * t(o, k) for o, n in seen.items()) / total if total > 0 else 1.0
        raw[k] = r[k] * math.fsum(a * t(o, k) for o, a in answer.items()) / mean
    s = sum(raw.values())
    return {k: v / s for k, v in raw.items()}


def mixed(forecasts: dict[str, dict[str, float]], logs: dict[str, dict[str, float]]) -> dict[str, float]:
    """The linear pool of floored ``forecasts``: P(move) with the move weights and P(up | move) with the
    direction weights, each renormalised over the members present."""
    names = sorted(forecasts)
    wm, wd = (_prob({n: logs[side][n] for n in names}) for side in SIDES)
    p_move = math.fsum(wm[n] * move_of(forecasts[n]) for n in names)
    p_up = math.fsum(wd[n] * direction_of(forecasts[n]) for n in names)
    return from_move_direction(p_move, p_up)


def _r4(p: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 4) for k, v in p.items()}


def answered(answers: dict[str, dict[str, float]], members: dict[str, str], fresh: set[str]) -> dict:
    """The questions' part of a snapshot: each awake live question's soft answer (``q_probs``), which were awake and
    which fresh, and each live question's version (``members``). A snapshot left out because JEV gave the end-price
    sum no answer still carries it, so the average-price loop can learn a call standing on its average-price sum
    alone (integral_loop)."""
    awake = sorted(q for q in answers if q in members)
    return {"q_probs": {q: _r4(answers[q]) for q in awake}, "awake": awake, "fresh": sorted(q for q in fresh if q in awake),
            "members": dict(sorted(members.items()))}


def snapshot(state: dict, reference: "Baseline", h: str, now: datetime, jev: dict, live_clock: dict | None, shown: dict,
             answers: dict[str, dict[str, float]], members: dict[str, str], fresh: set[str], source: str = SOURCE) -> dict:
    """Every forecast of one horizon at one read, as the update will score it: the experts, the block's
    members and mixture, the pool with its P(move) and P(up | move), the exact blend as the card kept
    it, the reference's raw odds the calibration learns from (``raw_<source>``), and the questions'
    part (answered). Weights and tables are the state's as of the last session applied; the
    calibration and the tables are too, unless they were learned against another reference version, which the
    update resets at the next session applied: a read under a refitted baseline is calibrated and tilted from
    nothing, as that update will learn it. The constants it was formed under and the reference's long-run shares
    are kept, so a rebuild under other constants can form it again (reformed). ``{"left_out": why}`` when an expert
    cannot be formed."""
    if not live_clock:
        return {"left_out": "no time-of-day odds this read, so today's blend is JEV alone and blend50 cannot be formed"}
    raw, long_run = reference.clock(h, now), reference.whole_day(h)
    same_reference = state["reference_version"] in (None, reference.version)
    cal = state["cal"] if same_reference else cold_state()["cal"]
    tables = state["tables"] if same_reference else {}
    r = calibrated(raw, cal, long_run)
    j = spread_unsure(jev, r)
    experts = {f"jev_share_{s:.1f}": {k: s * j[k] + (1 - s) * r[k] for k in OUTCOMES} for s in JEV_SHARES}
    c = {k: float(live_clock.get(k, 0.0)) for k in OUTCOMES}
    jc = spread_unsure(jev, c)
    experts.update({"blend50": {k: 0.5 * jc[k] + 0.5 * c[k] for k in OUTCOMES}, source: raw, f"{source}_cal": r})
    part = answered(answers, members, fresh)
    experts = {n: floored(f) for n, f in experts.items()}
    mixes = _mixes(state, tables, experts, r, {q: answers[q] for q in part["awake"]}, long_run)
    return {**mixes, "blend50_exact": {k: round(float(v), 4) for k, v in shown.items() if isinstance(v, (int, float))},
            f"raw_{source}": _r4(raw), **part, "state_hash": state_hash(state), "reference_version": reference.version,
            "last_session_applied": state["last_session_applied"], "constants_hash": CONSTANTS_HASH, "long_run": _r4(long_run),
            "forming_hash": FORMING_HASH}


def _mixes(state: dict, tables: dict, experts: dict[str, dict], r: dict[str, float], answers: dict[str, dict],
           long_run: dict[str, float]) -> dict:
    """The parts of a snapshot the state's weights and tables make: each awake question's tilt of ``r``, the
    block's mixture of them (``questions``), the pool over every expert, and its P(move) and P(up | move)."""
    member_f = {NO_CHANGE: floored(r), **{q: floored(tilted(r, a, tables.get(q), long_run)) for q, a in answers.items()}}
    # a question that is not yet a member (it joins at the next update) is written down but not mixed
    mixable = {n: f for n, f in member_f.items() if n == NO_CHANGE or n in state["members"]}
    experts = {**experts, "questions": floored(mixed(mixable, state["block"]))}
    pool = mixed(experts, state["top"])
    return {"experts": {n: _r4(f) for n, f in experts.items()}, "block_members": {n: _r4(f) for n, f in member_f.items()},
            "pool": _r4(floored(pool)), "p_move": round(move_of(pool), 4), "p_up_given_move": round(direction_of(pool), 4)}


def reformed(snap: dict, state: dict, long_run: dict[str, float]) -> dict:
    """A snapshot written under other constants, formed again from ``state`` as a read under these would have formed
    it: the experts the state does not shape (the JEV mixes, the blend, the reference and its calibration, which
    every constants change so far has left alone) are kept as written, and the question tilts, the block and the
    pool are made again from its answers. ``long_run`` stands in for the reference's long-run shares on a snapshot
    from before they were kept."""
    same_reference = state["reference_version"] in (None, snap.get("reference_version"))
    experts = {n: floored(f) for n, f in snap["experts"].items() if n != "questions"}
    mixes = _mixes(state, state["tables"] if same_reference else {}, experts, experts[REFERENCE],
                   {q: snap["q_probs"][q] for q in snap["awake"]}, snap.get("long_run") or long_run)
    return {**snap, **mixes, "state_hash": state_hash(state), "constants_hash": CONSTANTS_HASH, "reformed": True}


def shown(hour: dict, snaps: dict[str, dict], state_primary: dict) -> dict:
    """The end-price sums as the card keeps them beside the call and the grader scores them: today's
    exact blend, unless POOL_ON_PHONE is set and the pool was promoted on the primary horizon's
    evidence; then the pool at every horizon, the exact blend kept beside it. The sum says which under
    ``shown_source``."""
    use_pool = POOL_ON_PHONE and state_primary["phone"]["shows"] == "pool" and all("pool" in s for s in snaps.values())
    if not use_pool:
        return {**hour, "shown_source": SHOWN_BLEND}
    by = dict(hour.get("by") or {})
    for h, s in snaps.items():
        p = s["pool"]
        by[h] = {**by.get(h, {}), "pick": max(p, key=p.get), "probabilities": p, "blend50_exact": s["blend50_exact"]}
    out = {**hour, "by": by, "shown_source": SHOWN_POOL}
    prim = by.get(hour.get("primary") or LIVE.primary)
    if prim:
        out.update({"pick": prim["pick"], "probabilities": prim["probabilities"], "blend50_exact": prim["blend50_exact"]})
    return out


# ----------------------------------------------------------------------------- the update

def coverage(stamps: list[datetime], minutes: int) -> list[float]:
    """Each read's share of its window [t, t + h] not already covered by an earlier read's window, in
    time order: overlapping windows are one stretch of market, counted once."""
    out, covered = [], []
    for start in stamps:
        end = start + timedelta(minutes=minutes)
        free = [(start, end)]
        for a, b in covered:
            free = [piece for s, e in free for piece in ((s, min(e, a)), (max(s, b), e)) if piece[0] < piece[1]]
        out.append(sum((e - s).total_seconds() for s, e in free) / (minutes * 60.0))
        covered.append((start, end))
    return out


def bet(ep: dict, x: float) -> None:
    """One day of a betting e-process on a score x in [-1, 1] (aGRAPA): the bet is sized from the days
    before only, their mean and variance shrunk toward 0 and BET_PRIOR_VARIANCE by BET_PRIOR_DAYS days."""
    n = ep["n"]
    mean = ep["sum"] / (n + BET_PRIOR_DAYS)
    var = (ep["sum_sq"] - 2 * mean * ep["sum"] + n * mean * mean + BET_PRIOR_DAYS * BET_PRIOR_VARIANCE) / (n + BET_PRIOR_DAYS)
    lam = min(max(mean / (var + mean * mean), 0.0), BET_CAP)
    ep["e"] *= 1.0 + lam * x
    ep["n"], ep["sum"], ep["sum_sq"] = n + 1, ep["sum"] + x, ep["sum_sq"] + x * x


def day_score(loss_a: float, loss_b: float) -> float:
    """A day's evidence that A beats B, from their day-mean losses, bounded to [-1, 1]."""
    return max(-DAY_SCORE_BOUND, min(DAY_SCORE_BOUND, loss_b - loss_a)) / DAY_SCORE_BOUND


def weights_step(logs: dict[str, float], day_loss: dict[str, float], ref: str, prior: dict[str, float],
                 s_day: float) -> tuple[dict[str, float], dict]:
    """One day of ``s_day`` reads: each member's log weight moves by -ETA * s_day times its day-mean loss less the
    reference's, that gap clipped to GAP_CLIP first, then Fixed-Share toward the prior. The new probabilities, and
    what the log keeps (``bound`` when the clip bit)."""
    record, moved = {}, {}
    for n in sorted(logs):
        gap = day_loss[n] - day_loss[ref]
        clipped = max(-GAP_CLIP, min(GAP_CLIP, gap))
        raw, capped = -ETA * s_day * gap, -ETA * s_day * clipped
        moved[n] = logs[n] + capped
        record[n] = {"before": logs[n], "raw": raw, "capped": capped, "bound": gap != clipped}
    p = _prob(moved)
    return {n: (1 - ALPHA) * p[n] + ALPHA * prior[n] for n in p}, record


def capped_block(v: dict[str, float], suppressed: set[str], prior: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """No question above BLOCK_CAP of the block, the excess to "no change"; a suppressed question pinned
    at the Fixed-Share floor; renormalised. Returns the block and the questions the cap bound."""
    v = dict(v)
    bound = sorted(q for q in v if q != NO_CHANGE and v[q] > BLOCK_CAP)
    for q in bound:
        v[NO_CHANGE] += v[q] - BLOCK_CAP
        v[q] = BLOCK_CAP
    for q in sorted(suppressed & set(v)):
        v[q] = ALPHA * prior[q]
    s = math.fsum(v.values())
    return {q: x / s for q, x in v.items()}, bound


def graded_outcomes(grades: list[dict], horizons) -> dict[str, dict]:
    """``{row_ts: {"done": horizons graded or closed for good, "bands": {h: outcome}}}`` from the grades file."""
    out: dict[str, dict] = {}
    for g in grades:
        e = out.setdefault(g.get("row_ts"), {"done": set(), "bands": {}})
        if g.get("graded") is False:
            e["done"] |= set(horizons)
            continue
        e["done"] |= set(g.get("horizons") or []) | set(g.get("skipped") or {})
        for h in g.get("horizons") or []:
            if isinstance(g.get(h), dict) and g[h].get("band") in OUTCOMES:
                e["bands"][h] = g[h]["band"]
    return out


def event_inside(rec: dict, minutes: int) -> bool:
    """A scheduled event inside the read's window or under way, as the read's record says (events.learn_exclude,
    written when the read was made); a record from before it carries only the tier-1 tag (events.tag covers
    the next 60 minutes)."""
    ex = rec.get("learn_exclude")
    if isinstance(ex, dict) and str(minutes) in ex:
        return bool(ex[str(minutes)])
    ev = rec.get("event") if isinstance(rec.get("event"), dict) else None
    return ev is not None and (minutes > 30 or bool(ev.get("within_30")))


def membership(state: dict, members: dict[str, str], day: str) -> dict:
    """The block brought to the session's live questions: a new one joins at the prior share with the
    others' ratios kept; a retired one leaves, its evidence archived and still counted in the family;
    one whose options or words changed starts again as a new version."""
    changes: dict[str, list[str]] = {"joined": [], "retired": [], "new_version": []}
    current = state["members"]
    for q in sorted(current):
        if members.get(q) == current[q]["version"]:
            continue
        state["archived"][f"{q}@{current[q]['version']}"] = state["evidence"].pop(q, None)
        state["tables"].pop(q, None)
        for side in SIDES:
            state["block"][side].pop(q, None)
        changes["new_version" if q in members else "retired"].append(q)
        del current[q]
    joining = sorted(q for q in members if q not in current)
    if joining:
        prior = block_prior(sorted(set(current) | set(joining)))
        for side in SIDES:
            old = _prob(state["block"][side])
            scale = (1.0 - math.fsum(prior[q] for q in joining)) / math.fsum(old.values())
            state["block"][side] = _logs({**{n: w * scale for n, w in old.items()}, **{q: prior[q] for q in joining}})
        for q in joining:
            current[q] = {"version": members[q], "since": day}
            state["evidence"][q] = _new_evidence()
            if f"{q}@{members[q]}" not in state["family"]:
                state["family"].append(f"{q}@{members[q]}")
        changes["joined"] = joining
    return changes


def statuses(state: dict) -> list[dict]:
    """Earning, per part, by e-BH at EBH_LEVEL over every question version ever tested (retired ones
    included), after MIN_DAYS days; suppressed when the harm e-value reaches SUPPRESS_E after MIN_DAYS
    days, lifted when it falls under LIFT_E. Labels for a person; they never touch the prompt."""
    changes = []
    family = max(len(state["family"]), 1)
    for part in ("move", "direction"):
        ranked = sorted([(ev[part]["better"]["e"], q) for q, ev in state["evidence"].items()]
                        + [(ev[part]["better"]["e"], key) for key, ev in state["archived"].items() if ev], reverse=True)
        k_hat = max((k for k in range(1, len(ranked) + 1) if ranked[k - 1][0] >= family / (EBH_LEVEL * k)), default=0)
        selected = {name for _, name in ranked[:k_hat]}
        for q, ev in sorted(state["evidence"].items()):
            new = "earning" if q in selected and ev["days"] >= MIN_DAYS else None
            if ev["status"][part] != new:
                changes.append({"question": q, "part": part, "from": ev["status"][part], "to": new})
                ev["status"][part] = new
    for q, ev in sorted(state["evidence"].items()):
        harm = max(ev["move"]["worse"]["e"], ev["direction"]["worse"]["e"])
        was = ev["status"]["suppressed"]
        now = (harm >= LIFT_E) if was else (harm >= SUPPRESS_E and ev["days"] >= MIN_DAYS)
        if now != was:
            changes.append({"question": q, "part": "suppressed", "from": was, "to": now})
            ev["status"]["suppressed"] = now
    return changes


def on_version(snap: dict, q: str, version: str) -> bool:
    """Whether ``q`` was awake on the read and asked in ``version``: a read of an earlier version of it the same day
    (reworded mid-session) is no evidence about this one."""
    return q in snap["q_probs"] and (snap.get("members") or {}).get(q) == version


def apply_session(state: dict, day: str, reads: list[dict], minutes: int, primary: bool, harm_60: dict | None,
                  same_window: bool = False, source: str = SOURCE, replay: bool = False) -> dict:
    """One sealed session for one horizon, ``reads`` being its included ``{"row_ts", "snapshot",
    "outcome"}``: the day-mean losses, the weights steps, the tables, the e-processes, and on the
    primary horizon the statuses and the phone's promotion (vetoed while ``harm_60`` is at VETO_E).
    ``same_window``: every read forecasts one window (a lane graded from the settled open), so each
    counts the same in the day. ``source`` names the reference's experts (w0). ``replay``: a session learned
    again in a rebuild, which moves the weights, tables and calibration but bets nothing, so every e-process
    starts at 1 on the first session after the rebuild (a bound chosen after seeing the replayed days would
    otherwise lower the bar). Returns what the log keeps."""
    reads = sorted(reads, key=lambda r: r["row_ts"])
    # reads forecasting one window are one observation between them, however many there were
    c = [1.0 / len(reads)] * len(reads) if same_window else coverage([parse_ts(r["row_ts"]) for r in reads], minutes)
    s_day = math.fsum(c)
    snaps, ys = [r["snapshot"] for r in reads], [r["outcome"] for r in reads]
    experts = [{n: floored(f) for n, f in s["experts"].items()} for s in snaps]
    version = {q: m["version"] for q, m in state["members"].items()}

    def day_mean(values) -> float:
        return math.fsum(ci * v for ci, v in zip(c, values)) / s_day

    def member(s: dict, f: dict, q: str) -> dict:
        # an asleep question's forecast, or one asked in an earlier version, is the block mixed over the awake members
        return floored(s["block_members"][q]) if q == NO_CHANGE or on_version(s, q, version[q]) else f["questions"]

    names = [NO_CHANGE] + sorted(state["members"])
    prior = w0(source)
    top_loss, block_loss = {}, {}
    for i, side in enumerate(SIDES):
        top_loss[side] = {n: day_mean(losses(f[n], y)[i] for f, y in zip(experts, ys)) for n in prior}
        block_loss[side] = {q: day_mean(losses(member(s, f, q), y)[i] for s, f, y in zip(snaps, experts, ys)) for q in names}
    log: dict = {"reads": len(reads), "coverage": c, "top": {}, "block": {}, "block_cap_bound": {}}
    if replay:
        log["replay"] = True
    total = bound = 0
    if state["frozen"] is None:
        suppressed = {q for q, ev in state["evidence"].items() if ev["status"]["suppressed"]}
        v0 = block_prior(sorted(state["members"]))
        for side in SIDES:
            block = {q: state["block"][side][q] for q in names}
            v, log["block"][side] = weights_step(block, block_loss[side], NO_CHANGE, v0, s_day)
            v, log["block_cap_bound"][side] = capped_block(v, suppressed, v0)
            state["block"][side] = _logs(v)
            w, log["top"][side] = weights_step(state["top"][side], top_loss[side], REFERENCE, prior, s_day)
            state["top"][side] = _logs(w)
            # the freeze watches the pool's own experts: the block's ~90 questions would drown out the ten that matter
            total += len(log["top"][side])
            bound += sum(1 for x in log["top"][side].values() if x["bound"])
    for q in state["members"]:
        # the tables forget every sealed session (06 step 7), a session the question slept through too
        t = state["tables"].get(q)
        if t is not None:
            for key in ("C", "X"):
                t[key] = {o: {k: TABLE_DECAY * x for k, x in row.items()} for o, row in t[key].items()}
        awake = [(ci, s, y) for ci, s, y in zip(c, snaps, ys) if on_version(s, q, version[q])]
        if not awake:
            continue
        # every read counts, at its window coverage: a day with few answers adds little
        t = state["tables"].setdefault(q, {"C": {}, "X": {}})
        for ci, s, y in awake:
            ref = floored(s["experts"][REFERENCE])
            for o, a in s["q_probs"][q].items():
                crow = t["C"].setdefault(o, {k: 0.0 for k in OUTCOMES})
                xrow = t["X"].setdefault(o, {k: 0.0 for k in OUTCOMES})
                crow[y] += ci * a
                for k in OUTCOMES:
                    xrow[k] += ci * a * ref[k]
    for k in OUTCOMES:
        state["cal"]["O"][k] = CAL_DECAY * state["cal"]["O"][k] + day_mean(1.0 if y == k else 0.0 for y in ys)
        state["cal"]["E"][k] = CAL_DECAY * state["cal"]["E"][k] + day_mean(floored(s[f"raw_{source}"])[k] for s in snaps)
    pool_loss = day_mean(log_loss(floored(s["pool"]), y) for s, y in zip(snaps, ys))
    # the exact blend is scored as the phone showed it: unsure mass counts against it, no renormalising
    blend_loss = day_mean(-math.log(max(float(s["blend50_exact"].get(y, 0.0)), EPS)) for s, y in zip(snaps, ys))
    # and with its unsure mass spread, so the pool must beat the blend on more than that accounting
    spread_loss = day_mean(log_loss(f["blend50"], y) for f, y in zip(experts, ys))
    # and the pool at its starting weights, so what it learned must count, not the mix it began with (09-30 review)
    start = {side: _logs(w0(source)) for side in SIDES}
    start_loss = day_mean(log_loss(floored(mixed(f, start)), y) for f, y in zip(experts, ys))
    log["pool_vs_blend"] = {"pool": pool_loss, "blend50_exact": blend_loss, "blend50": spread_loss, "pool_at_start": start_loss}
    # a replayed day still fills the freeze's window, but never trips it
    state["cap_binds"] = (state["cap_binds"] + [[day, total, bound]])[-CAP_BIND_WINDOW:]
    if replay:
        return log
    log["evidence"] = {}
    for q, ev in sorted(state["evidence"].items()):
        # scored only over the reads it was awake on in this version, against "no change" on those same reads: an
        # asleep read's forecast is the other members' mix, which would credit or blame it for them
        awake = [(ci, s["block_members"], y) for ci, s, y in zip(c, snaps, ys) if on_version(s, q, version[q])]
        s_q = math.fsum(ci for ci, _, _ in awake)
        if s_q <= 0:
            continue
        ev["days"] += 1
        for i, part in enumerate(("move", "direction")):
            loss_q, loss_ref = (math.fsum(ci * losses(floored(m[n]), y)[i] for ci, m, y in awake) / s_q for n in (q, NO_CHANGE))
            x = day_score(loss_q, loss_ref)
            bet(ev[part]["better"], x)
            bet(ev[part]["worse"], -x)
        log["evidence"][q] = {"days": ev["days"], **{part: {k: ev[part][k]["e"] for k in ("better", "worse")} for part in ("move", "direction")}}
    phone = state["phone"]
    for test in PROMOTION_TESTS[1:]:
        phone.setdefault(test, new_eprocess())             # a state from before the test
    if primary:
        log["status_changes"] = statuses(state)
        if phone["shows"] == "blend":
            for test, against in zip(PROMOTION_TESTS, (blend_loss, spread_loss, start_loss)):
                bet(phone[test], day_score(pool_loss, against))
            vetoed = harm_60 is not None and harm_60["e"] >= VETO_E
            earned = min(phone[test]["e"] for test in PROMOTION_TESTS) >= PROMOTE_E
            if earned and phone["promote"]["n"] >= MIN_DAYS and not vetoed:
                if SIM_GATES_PASSED:
                    phone.update({"shows": "pool", "since": day, "demote": new_eprocess()})
                    log["phone"] = "promoted: the pool is eligible for the phone"
                else:
                    log["phone"] = "held on the blend: the evidence is there, but the simulation gates (06) have not passed"
        else:
            bet(phone["demote"], day_score(blend_loss, pool_loss))
            if phone["demote"]["e"] >= DEMOTE_E:
                phone.update({"shows": "blend", "since": day, **{test: new_eprocess() for test in PROMOTION_TESTS}})
                log["phone"] = "demoted: back to the exact blend; the promotion evidence starts again"
    else:
        bet(phone["harm_60"], day_score(blend_loss, pool_loss))
    seen, hit = sum(t for _, t, _ in state["cap_binds"]), sum(b for _, _, b in state["cap_binds"])
    if state["frozen"] is None and len(state["cap_binds"]) >= CAP_BIND_WINDOW and seen and hit / seen > CAP_BIND_LIMIT:
        state["frozen"] = (f"the gap clip bound on more than {CAP_BIND_LIMIT:.0%} of the pool's expert-days over "
                           f"{CAP_BIND_WINDOW} sessions: the weights are frozen for review")
        log["frozen"] = state["frozen"]
    return log


def _session_reads(recs: list[dict], outcomes: dict[str, dict], h: str, minutes: int) -> tuple[list[dict], dict[str, str]]:
    """The session's included reads for one horizon, and every excluded one with its reason."""
    half_day = session_close(parse_ts(recs[0]["row_ts"])).time() != SESSION_CLOSE
    reads, excluded = [], {}
    for r in recs:
        snap = legacy_snapshot((r["pool"] if isinstance(r["pool"], dict) else {}).get(h) or {"left_out": "no snapshot for this horizon"})
        band = outcomes.get(r["row_ts"], {}).get("bands", {}).get(h)
        if half_day:
            excluded[r["row_ts"]] = "a half day"
        elif "left_out" in snap:
            excluded[r["row_ts"]] = f"no snapshot: {snap['left_out']}"
        elif event_inside(r, minutes):
            excluded[r["row_ts"]] = f"a scheduled event inside the {minutes}-minute window"
        elif band is None:
            excluded[r["row_ts"]] = "no outcome at this horizon"
        else:
            reads.append({"row_ts": r["row_ts"], "snapshot": snap, "outcome": band})
    return reads, excluded


def learn_session(state: dict, day: str, reads: list[dict], excluded: dict[str, str], minutes: int, primary: bool,
                  harm_60: dict | None, same_window: bool, source: str = SOURCE) -> dict:
    """One sealed session's included ``reads`` learned into ``state``, as both loops' updates learn it: the watermark
    moved to ``day``, the reads' reference taken up, the block brought to their live questions (membership), and
    apply_session, a day up to the state's ``replay_until`` replayed without bets. Returns what the log keeps."""
    state["last_session_applied"] = day
    body: dict = {"manifest": {"included": [r["row_ts"] for r in reads], "excluded": excluded}}
    if not reads:
        return body
    version = reads[-1]["snapshot"]["reference_version"]
    if state["reference_version"] not in (None, version):
        # a new reference (a refitted baseline, a new counting rule): its calibration and the question tables, which
        # counted outcomes against the old one's expectations, start again
        state["cal"] = cold_state()["cal"]
        state["tables"] = {}
        body["reference_changed"] = {"from": state["reference_version"], "to": version}
    state["reference_version"] = version
    body["membership"] = membership(state, reads[-1]["snapshot"]["members"], day)
    body.update(apply_session(state, day, reads, minutes, primary, harm_60, same_window, source,
                              replay=day <= (state.get("replay_until") or "")))
    return body


def archive_state(path: Path, state: dict) -> Path:
    """Keep a state learned under other constants in the folder's archive/, named by the constants it was learned
    under and today's date, before a rebuild replaces it. Returns where it went."""
    folder = path.parent / "archive"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(ET).date().isoformat()
    base = f"{path.name}.pre-{state.get('constants_hash') or 'unknown'}-{stamp}"
    dest, n = folder / base, 1
    while dest.exists():                                   # never over a state kept earlier the same day
        n += 1
        dest = folder / f"{base}.{n}"
    dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def carried_evidence(old: dict, new: dict) -> dict:
    """A rebuilt state ``new`` with each promotion e-process starting where ``old``'s stood, but never above 1: the
    evidence for the pool is not earned again by days the rebuild replays without bets, and the evidence against it
    is not forgiven by the rebuild (09-30 review). The bets are sized afresh."""
    for test in PROMOTION_TESTS:
        e = (old.get("phone", {}).get(test) or {}).get("e", 1.0)
        new["phone"][test] = {**new_eprocess(), "e": min(float(e), 1.0)}
    return new


def update(out_dir: Path, today: str | None = None, lane: Lane = LIVE, baseline: "Baseline | None" = None) -> dict[str, str]:
    """Apply every sealed session after the watermark, oldest first, both horizons (the longer first,
    so the primary's promotion sees the same day's veto). A session is sealed once it is over and every
    read has a terminal grade at every horizon. An unsealed session, or one some of whose reads carry no
    snapshot, stops the run and is logged (fail closed); one none of whose reads carries a snapshot is
    from before the loop and is passed over. A session already applied is never applied again.
    A state made under other constants (its constants_hash) is kept in archive/ and learned again from the
    records: from cold, every session it had applied replayed under these constants (snapshots formed again,
    reformed; no bets, so every e-process starts at 1), then on as usual. The replay's last day is kept in the state
    (``replay_until``), so a rebuild stopped part way (an unsealed session, say) still bets nothing on those days
    when it goes on. ``baseline`` gives the long-run shares a snapshot from before they were kept is formed again
    with (the current Baseline when not given); a snapshot whose tables were learned under another reference than
    that Baseline's cannot be formed again faithfully, so it stops the run (fail closed).
    Returns ``{h: what happened}``."""
    out_dir = Path(out_dir)
    today = today or datetime.now(ET).date().isoformat()
    outcomes = graded_outcomes(load_jsonl(out_dir / "grades.jsonl"), lane.horizons)
    order = sorted(lane.horizons, key=lambda h: -lane.horizons[h][0])
    states = {h: load_state(out_dir, lane.horizons[h][0]) for h in order}
    said = {h: "nothing new to apply" for h in order}
    changed = [h for h in order if states[h].get("constants_hash") != CONSTANTS_HASH]
    if changed:
        replay_until = max((s["last_session_applied"] or "") for s in states.values())
        kept = {h: str(archive_state(state_path(out_dir, lane.horizons[h][0]), states[h]))
                for h in order if state_path(out_dir, lane.horizons[h][0]).exists()}
        _log(out_dir, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": CODE_HASH, "applied": False,
                       "rebuild": {"from": {h: states[h].get("constants_hash") for h in order}, "to": CONSTANTS_HASH,
                                   "kept": kept, "replay_until": replay_until or None},
                       "why": f"constants changed since {', '.join(changed)} began: learned again from the records"})
        states = {h: carried_evidence(states[h], {**cold_state(), "replay_until": replay_until or None}) for h in order}
    watermark = min((s["last_session_applied"] or "") for s in states.values())
    stopped = False
    hour_dir = out_dir / "hour"
    for day in sorted(p.stem for p in hour_dir.glob("*.jsonl")) if hour_dir.exists() else []:
        if day <= watermark or day >= today:
            continue
        recs, seen = [], set()
        for r in load_jsonl(hour_dir / f"{day}.jsonl"):
            if isinstance(r.get("by"), dict) and r.get("row_ts") not in seen:
                seen.add(r["row_ts"])
                recs.append(r)
        head = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": day, "code": CODE_HASH}
        if recs and not any("pool" in r for r in recs):
            why = "no read carries a snapshot: before the loop"
        elif not all("pool" in r for r in recs):
            _log(out_dir, {**head, "applied": False, "why": "some reads carry no snapshot: stopped, fail closed"})
            said = {h: f"{day}: some reads carry no snapshot; stopped" for h in order}
            break
        elif any(outcomes.get(r["row_ts"], {}).get("done", set()) != set(lane.horizons) for r in recs):
            _log(out_dir, {**head, "applied": False, "why": "unsealed: a read still has a horizon to grade"})
            said = {h: f"{day}: unsealed; stopped" for h in order}
            break
        else:
            why = None
        for h in order:
            state, minutes = states[h], lane.horizons[h][0]
            if (state["last_session_applied"] or "") >= day:
                continue
            if why or not recs:
                state["last_session_applied"] = day
                _log(out_dir, {**head, "horizon": h, "applied": False, "why": why or "no reads"})
                continue
            reads, excluded = _session_reads(recs, outcomes, h, minutes)
            # a snapshot written under other constants is formed again from the state a read would have seen today
            stale = [r for r in reads if r["snapshot"].get("constants_hash") != CONSTANTS_HASH]
            if stale and baseline is None:
                from .baseline import Baseline
                baseline = Baseline.load()
            # a snapshot from before its fingerprint was kept was formed under today's FORMING
            unfaithful = [r["row_ts"] for r in stale if r["snapshot"].get("forming_hash") not in (None, FORMING_HASH)
                          or (not r["snapshot"].get("long_run") and state["tables"]
                              and state["reference_version"] in (None, r["snapshot"].get("reference_version"))
                              and r["snapshot"].get("reference_version") != baseline.version)]
            if unfaithful:
                _log(out_dir, {**head, "horizon": h, "applied": False,
                               "why": "a snapshot to form again was made under another reference or other forming settings "
                                      "than today's: stopped, fail closed"})
                said[h] = f"{day}: a snapshot cannot be formed again; stopped"
                stopped = True
                break
            reads = [r if r["snapshot"].get("constants_hash") == CONSTANTS_HASH
                     else {**r, "snapshot": reformed(r["snapshot"], state, baseline.whole_day(h))} for r in reads]
            primary = h == lane.primary
            harm_60 = states[order[0]]["phone"]["harm_60"] if primary and h != order[0] else None
            body = learn_session(state, day, reads, excluded, minutes, primary, harm_60, lane.graded_from_settled_open)
            # rounded after every session, as saved, so one night at a time and a rebuild agree to the bit
            states[h] = json.loads(_canonical(state))
            _log(out_dir, {**head, "horizon": h, "applied": bool(reads), **_rounded(body)})
            said[h] = f"{day}: applied, {len(reads)} reads"
        if stopped:
            break
    for h in order:
        save_state(out_dir, lane.horizons[h][0], states[h])
    return said


def _log(out_dir: Path, line: dict) -> None:
    with open(Path(out_dir) / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")


# ----------------------------------------------------------------------------- the seam

def phone_report(state: dict) -> dict:
    """The promotion evidence as weights.json reports it: what the state would show, whether the phone shows it
    (never while POOL_ON_PHONE is off), and the pool's e-value on each of PROMOTION_TESTS."""
    phone = state["phone"]
    return {"shows": phone["shows"], "on_phone": POOL_ON_PHONE and phone["shows"] == "pool",
            **{f"{test}_e": (phone.get(test) or new_eprocess())["e"] for test in PROMOTION_TESTS}, "days": phone["promote"]["n"]}


class PoolWeights(QuestionWeights):
    """The loop behind the question-weights seam. learn() applies every newly sealed session and reports
    each live question's standing against "no change"; every live question still weighs 1.0 in step 3,
    since trimming the prompt stays a person's decision."""

    method = "pool_v1"

    def __init__(self, questions: dict[str, dict] | None = None, pool: dict | None = None) -> None:
        super().__init__(questions)
        self.pool = pool or {}

    @classmethod
    def learn(cls, graded: list[dict], allowed: dict[str, set[str]], out_dir: Path | None = None, lane: Lane = LIVE) -> "PoolWeights":
        applied = update(out_dir, lane=lane) if out_dir is not None else {}
        state = load_state(out_dir, lane.horizons[lane.primary][0]) if out_dir is not None else cold_state()
        n = Counter(qid for g in graded for qid, p in (g.get("fresh") or {}).items() if qid in allowed and str(p) in allowed[qid])
        block = {side: _prob(state["block"][side]) for side in SIDES}
        questions = {}
        for qid in sorted(allowed):
            ev = state["evidence"].get(qid)
            entry = {"weight": 1.0, "n": n[qid], "in_step_3": True,
                     "why": "every live question keeps its sentence; the loop's standing is a label for a person"}
            if ev is not None:
                entry.update({"days": ev["days"], "status": ev["status"],
                              "e": {part: {k: ev[part][k]["e"] for k in ("better", "worse")} for part in ("move", "direction")},
                              # a question's weight shown against "no change", never as a raw share of the block
                              "against_no_change": {side: block[side].get(qid, 0.0) / block[side][NO_CHANGE] for side in SIDES}})
            questions[qid] = entry
        top = {side: _prob(state["top"][side]) for side in SIDES}
        return cls(questions, {"applied": applied, "last_session_applied": state["last_session_applied"],
                               "phone": phone_report(state), "top": top, "frozen": state["frozen"]})

    def as_json(self) -> dict:
        return {**super().as_json(), "pool": self.pool}
