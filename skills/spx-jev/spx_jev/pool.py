"""The learning loop: how far to trust JEV's sum against a price-only reference, learned a day at a time.

The design is 06-learning-loop-design (revised 2026-09-26). At every live read a handful of forecasts
("experts") of where SPX ends each horizon are written down beside the sum (snapshot); once a session
is sealed, each expert's loss over the day is one piece of evidence and the experts' weights move by
it (update). A trading day, never a read, is the unit of evidence, because reads on one day rise and
fall together.

The reference R is the frozen time-of-day odds (baseline.py; the movement-so-far split lost the
validation and is dropped) calibrated by a three-number decaying table of what actually happened.
The experts, per horizon:

    jev_share_S   S * J + (1 - S) * R for S in JEV_SHARES, J being JEV's sum with its unsure mass spread
                  by R: the fixed JEV-trust mixes; jev_share_0.0 is R itself
    blend50       today's half-and-half blend with the live clock, JEV's unsure mass spread by the clock
    clock         the frozen time-of-day odds, uncalibrated
    clock_cal     the same, calibrated (the same forecast as R)
    questions     the question block: "no change" (R) against one expert per live question, each R tilted
                  by how often that question's answers came before each outcome, mixed by their own weights

The pool is a weighted average of the experts, kept apart for the two things a forecast says: will
price move (P(move), the move weights) and, given a move, which way (P(up | move), the direction
weights), each scored on its own part of the log loss (scores.py). A question with no answer yet
today is asleep and the block is mixed over the awake members only; a held answer is awake, since it
was given before the outcome.

The update, per horizon, on each sealed session after the last one applied: the reads' window
coverage, each expert's day-mean move and direction loss, a capped exponential-weights step against
R (in the block, against "no change"), Fixed-Share toward the prior, the block's per-question cap,
the decaying tables, and day-level betting e-processes, a question's scored only over the reads it was
awake on, against "no change" on those same reads (the weights step keeps its asleep reads, forecast by
the awake members' mix, as sleeping experts do). A question is labelled earning only by e-BH
over every question version ever tested; the pool becomes eligible for the phone only once its
e-process against the exact blend reaches PROMOTE_E after MIN_DAYS days, and steps back on the same
kind of test. It runs at the first grading run after a session ends (PoolWeights.learn).

Nothing here changes JEV's prompt: every live question keeps its sentence and weighs 1.0 in step 3.
The phone keeps today's exact 50/50 blend until the pool is promoted (at least MIN_DAYS days, its
e-process against the blend at PROMOTE_E, and SIM_GATES_PASSED, the simulation 06 requires first);
then it shows the pool, the blend kept beside it, and goes back to the blend by itself when the
demotion e-process reaches DEMOTE_E. POOL_ON_PHONE turns that off.

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

POOL_ON_PHONE = True            # show the pool on the phone while it is promoted: Will's decision, 2026-09-28
# 06 requires the spec-exact simulation's acceptance gates to pass before any promotion; they are not
# built, so the promotion evidence builds up but the pool is not promoted until this is set
SIM_GATES_PASSED = False

JEV_SHARES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
REFERENCE = "jev_share_0.0"
W0 = {**{f"jev_share_{s:.1f}": 0.05 for s in JEV_SHARES}, "blend50": 0.30, "clock": 0.05, "clock_cal": 0.05, "questions": 0.30}
NO_CHANGE = "no_change"         # the block's reference member: R, untilted
NO_CHANGE_PRIOR = 0.20          # its share of the block's prior; the questions share the rest
ETA = 1.0                       # one day is one observation, and log loss is 1-exp-concave
ALPHA = 0.02                    # Fixed-Share: every member keeps at least ALPHA of its prior
DAY_STEP_CAP = math.log(4)      # the most a member's log weight may move in one day
BLOCK_CAP = 0.25                # the most one question may hold of the block
TABLE_DECAY = 0.98              # the question tables, about a 34-session half life
CAL_DECAY = 0.95                # the calibration table, about 14 sessions
PRIOR_DAYS = 5.0                # the tables' and the calibration's prior, in days
DAY_SCORE_BOUND = 1.0           # a day's loss difference is clipped to this many nats before it is bet on
BET_CAP = 0.5
BET_PRIOR_DAYS, BET_PRIOR_VARIANCE = 5.0, 0.25
PROMOTE_E, DEMOTE_E, VETO_E = 20.0, 10.0, 10.0
MIN_DAYS = 20
EBH_LEVEL = 0.10
SUPPRESS_E, LIFT_E = 20.0, 5.0
CAP_BIND_WINDOW, CAP_BIND_LIMIT = 20, 0.05
ROUND = 12
LOG_NAME = "pool_log.jsonl"
SHOWN_BLEND, SHOWN_POOL = "blend50_exact", "pool_v1"
SIDES = ("M", "D")              # the move weights and the direction weights

CONSTANTS = {"eps": EPS, "eta": ETA, "alpha": ALPHA, "day_step_cap": DAY_STEP_CAP, "block_cap": BLOCK_CAP,
             "table_decay": TABLE_DECAY, "cal_decay": CAL_DECAY, "prior_days": PRIOR_DAYS, "day_score_bound": DAY_SCORE_BOUND,
             "bet_cap": BET_CAP, "bet_prior_days": BET_PRIOR_DAYS, "bet_prior_variance": BET_PRIOR_VARIANCE,
             "promote_e": PROMOTE_E, "demote_e": DEMOTE_E, "veto_e": VETO_E, "min_days": MIN_DAYS, "ebh_level": EBH_LEVEL,
             "suppress_e": SUPPRESS_E, "lift_e": LIFT_E, "w0": W0, "no_change_prior": NO_CHANGE_PRIOR, "jev_shares": JEV_SHARES,
             "cap_bind_window": CAP_BIND_WINDOW, "cap_bind_limit": CAP_BIND_LIMIT}
CONSTANTS_HASH = hashlib.sha256(json.dumps(CONSTANTS, sort_keys=True).encode()).hexdigest()[:16]
CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


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


def cold_state() -> dict:
    """Monday's state: every weight at its prior, empty tables, every e-value 1, the phone on the blend."""
    return {"constants_hash": CONSTANTS_HASH, "baseline": None,
            "top": {side: _logs(W0) for side in SIDES}, "block": {side: {NO_CHANGE: 0.0} for side in SIDES},
            "members": {}, "tables": {}, "cal": {"O": {k: 0.0 for k in OUTCOMES}, "E": {k: 0.0 for k in OUTCOMES}},
            "evidence": {}, "family": [], "archived": {},
            "phone": {"shows": "blend", "since": None, "promote": new_eprocess(), "demote": new_eprocess(), "harm_60": new_eprocess()},
            "cap_binds": [], "frozen": None, "last_session_applied": None}


def state_path(out_dir: Path, minutes: int) -> Path:
    return Path(out_dir) / f"pool_{minutes}.json"


def load_state(out_dir: Path, minutes: int) -> dict:
    try:
        return json.loads(state_path(out_dir, minutes).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cold_state()


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
    """A question's expert, f(k) proportional to R(k) * sum over options o of a(o) * (C[o][k] + m r_k) /
    (X[o][k] + m r_k): R tilted by how often each answer came before each outcome against how often R
    expected it. With empty tables it is R."""
    table = table or {}
    raw = {}
    for k in OUTCOMES:
        tilt = 0.0
        for o, a in answer.items():
            c = table.get("C", {}).get(o, {}).get(k, 0.0)
            x = table.get("X", {}).get(o, {}).get(k, 0.0)
            tilt += a * (c + PRIOR_DAYS * long_run[k]) / (x + PRIOR_DAYS * long_run[k])
        raw[k] = r[k] * tilt
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


def snapshot(state: dict, baseline: "Baseline", h: str, now: datetime, jev: dict, live_clock: dict | None, shown: dict,
             answers: dict[str, dict[str, float]], members: dict[str, str], fresh: set[str]) -> dict:
    """Every forecast of one horizon at one read, as the update will score it: the experts, the block's
    members and mixture, the pool with its P(move) and P(up | move), the exact blend as the phone showed
    it, the raw clock the calibration learns from, the questions' soft answers and which were awake
    (answered, fresh or held) and fresh, and each live question's version. Weights and tables are the
    state's as of the last session applied; the calibration is too, unless it was learned against another
    baseline version, which the update resets at the next session applied: a read under a refitted
    baseline is calibrated from nothing, as that update will learn it. ``{"left_out": why}`` when an
    expert cannot be formed."""
    if not live_clock:
        return {"left_out": "no time-of-day odds this read, so today's blend is JEV alone and blend50 cannot be formed"}
    raw_clock, long_run = baseline.clock(h, now), baseline.whole_day(h)
    cal = state["cal"] if state["baseline"] in (None, baseline.version) else cold_state()["cal"]
    r = calibrated(raw_clock, cal, long_run)
    j = spread_unsure(jev, r)
    experts = {f"jev_share_{s:.1f}": {k: s * j[k] + (1 - s) * r[k] for k in OUTCOMES} for s in JEV_SHARES}
    c = {k: float(live_clock.get(k, 0.0)) for k in OUTCOMES}
    jc = spread_unsure(jev, c)
    experts.update({"blend50": {k: 0.5 * jc[k] + 0.5 * c[k] for k in OUTCOMES}, "clock": raw_clock, "clock_cal": r})
    awake = sorted(q for q in answers if q in members)
    member_f = {NO_CHANGE: floored(r), **{q: floored(tilted(r, answers[q], state["tables"].get(q), long_run)) for q in awake}}
    # a question that is not yet a member (it joins at the next update) is written down but not mixed
    experts["questions"] = mixed({n: f for n, f in member_f.items() if n == NO_CHANGE or n in state["members"]}, state["block"])
    experts = {n: floored(f) for n, f in experts.items()}
    pool = mixed(experts, state["top"])
    return {"experts": {n: _r4(f) for n, f in experts.items()}, "block_members": {n: _r4(f) for n, f in member_f.items()},
            "pool": _r4(floored(pool)), "p_move": round(move_of(pool), 4), "p_up_given_move": round(direction_of(pool), 4),
            "blend50_exact": {k: round(float(v), 4) for k, v in shown.items() if isinstance(v, (int, float))},
            "raw_clock": _r4(raw_clock), "q_probs": {q: _r4(answers[q]) for q in awake}, "awake": awake,
            "fresh": sorted(q for q in fresh if q in awake), "members": dict(sorted(members.items())),
            "state_hash": state_hash(state), "baseline": baseline.version, "last_session_applied": state["last_session_applied"]}


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


def weights_step(logs: dict[str, float], day_loss: dict[str, float], ref: str, prior: dict[str, float]) -> tuple[dict[str, float], dict]:
    """One day: each member's log weight moves by -ETA times its day loss less the reference's, capped
    at DAY_STEP_CAP, then Fixed-Share toward the prior. The new probabilities, and what the log keeps."""
    record, moved = {}, {}
    for n in sorted(logs):
        raw = -ETA * (day_loss[n] - day_loss[ref])
        capped = max(-DAY_STEP_CAP, min(DAY_STEP_CAP, raw))
        moved[n] = logs[n] + capped
        record[n] = {"before": logs[n], "raw": raw, "capped": capped, "bound": raw != capped}
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


def apply_session(state: dict, day: str, reads: list[dict], minutes: int, primary: bool, harm_60: dict | None,
                  same_window: bool = False) -> dict:
    """One sealed session for one horizon, ``reads`` being its included ``{"row_ts", "snapshot",
    "outcome"}``: the day-mean losses, the weights steps, the tables, the e-processes, and on the
    primary horizon the statuses and the phone's promotion (vetoed while ``harm_60`` is at VETO_E).
    ``same_window``: every read forecasts one window (a lane graded from the settled open), so each
    counts the same in the day. Returns what the log keeps."""
    reads = sorted(reads, key=lambda r: r["row_ts"])
    c = [1.0] * len(reads) if same_window else coverage([parse_ts(r["row_ts"]) for r in reads], minutes)
    s_day = math.fsum(c)
    snaps, ys = [r["snapshot"] for r in reads], [r["outcome"] for r in reads]
    experts = [{n: floored(f) for n, f in s["experts"].items()} for s in snaps]

    def day_mean(values) -> float:
        return math.fsum(ci * v for ci, v in zip(c, values)) / s_day

    def member(s: dict, f: dict, q: str) -> dict:
        # an asleep question's forecast is the block mixed over the awake members
        return floored(s["block_members"][q]) if q in s["block_members"] else f["questions"]

    names = [NO_CHANGE] + sorted(state["members"])
    top_loss, block_loss = {}, {}
    for i, side in enumerate(SIDES):
        top_loss[side] = {n: day_mean(losses(f[n], y)[i] for f, y in zip(experts, ys)) for n in W0}
        block_loss[side] = {q: day_mean(losses(member(s, f, q), y)[i] for s, f, y in zip(snaps, experts, ys)) for q in names}
    log: dict = {"reads": len(reads), "coverage": c, "top": {}, "block": {}, "block_cap_bound": {}}
    total = bound = 0
    if state["frozen"] is None:
        suppressed = {q for q, ev in state["evidence"].items() if ev["status"]["suppressed"]}
        v0 = block_prior(sorted(state["members"]))
        for side in SIDES:
            v, log["block"][side] = weights_step({q: state["block"][side][q] for q in names}, block_loss[side], NO_CHANGE, v0)
            v, log["block_cap_bound"][side] = capped_block(v, suppressed, v0)
            state["block"][side] = _logs(v)
            w, log["top"][side] = weights_step(state["top"][side], top_loss[side], REFERENCE, W0)
            state["top"][side] = _logs(w)
            for rec in (log["block"][side], log["top"][side]):
                total += len(rec)
                bound += sum(1 for x in rec.values() if x["bound"])
    for q in state["members"]:
        # the tables forget every sealed session (06 step 7), a session the question slept through too
        t = state["tables"].get(q)
        if t is not None:
            for key in ("C", "X"):
                t[key] = {o: {k: TABLE_DECAY * x for k, x in row.items()} for o, row in t[key].items()}
        awake = [(ci, s, y) for ci, s, y in zip(c, snaps, ys) if q in s["q_probs"]]
        s_q = math.fsum(ci for ci, _, _ in awake)
        if s_q <= 0:
            continue
        t = state["tables"].setdefault(q, {"C": {}, "X": {}})
        for ci, s, y in awake:
            ref = floored(s["experts"][REFERENCE])
            for o, a in s["q_probs"][q].items():
                crow = t["C"].setdefault(o, {k: 0.0 for k in OUTCOMES})
                xrow = t["X"].setdefault(o, {k: 0.0 for k in OUTCOMES})
                crow[y] += ci * a / s_q
                for k in OUTCOMES:
                    xrow[k] += ci * a * ref[k] / s_q
    for k in OUTCOMES:
        state["cal"]["O"][k] = CAL_DECAY * state["cal"]["O"][k] + day_mean(1.0 if y == k else 0.0 for y in ys)
        state["cal"]["E"][k] = CAL_DECAY * state["cal"]["E"][k] + day_mean(floored(s["raw_clock"])[k] for s in snaps)
    log["evidence"] = {}
    for q, ev in sorted(state["evidence"].items()):
        # scored only over the reads it was awake on, against "no change" on those same reads: an asleep
        # read's forecast is the other members' mix, which would credit or blame it for them
        awake = [(ci, s["block_members"], y) for ci, s, y in zip(c, snaps, ys) if q in s["q_probs"]]
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
    pool_loss = day_mean(log_loss(floored(s["pool"]), y) for s, y in zip(snaps, ys))
    # the exact blend is scored as the phone showed it: unsure mass counts against it, no renormalising
    blend_loss = day_mean(-math.log(max(float(s["blend50_exact"].get(y, 0.0)), EPS)) for s, y in zip(snaps, ys))
    log["pool_vs_blend"] = {"pool": pool_loss, "blend50_exact": blend_loss}
    phone = state["phone"]
    if primary:
        log["status_changes"] = statuses(state)
        if phone["shows"] == "blend":
            bet(phone["promote"], day_score(pool_loss, blend_loss))
            vetoed = harm_60 is not None and harm_60["e"] >= VETO_E
            if phone["promote"]["e"] >= PROMOTE_E and phone["promote"]["n"] >= MIN_DAYS and not vetoed:
                if SIM_GATES_PASSED:
                    phone.update({"shows": "pool", "since": day, "demote": new_eprocess()})
                    log["phone"] = "promoted: the pool is eligible for the phone"
                else:
                    log["phone"] = "held on the blend: the evidence is there, but the simulation gates (06) have not passed"
        else:
            bet(phone["demote"], day_score(blend_loss, pool_loss))
            if phone["demote"]["e"] >= DEMOTE_E:
                phone.update({"shows": "blend", "since": day, "promote": new_eprocess()})
                log["phone"] = "demoted: back to the exact blend; the promotion evidence starts again"
    else:
        bet(phone["harm_60"], day_score(blend_loss, pool_loss))
    state["cap_binds"] = (state["cap_binds"] + [[day, total, bound]])[-CAP_BIND_WINDOW:]
    seen, hit = sum(t for _, t, _ in state["cap_binds"]), sum(b for _, _, b in state["cap_binds"])
    if state["frozen"] is None and len(state["cap_binds"]) >= CAP_BIND_WINDOW and seen and hit / seen > CAP_BIND_LIMIT:
        state["frozen"] = (f"the day-step cap bound on more than {CAP_BIND_LIMIT:.0%} of expert-days over {CAP_BIND_WINDOW} "
                           "sessions: the weights are frozen for review")
        log["frozen"] = state["frozen"]
    return log


def _session_reads(recs: list[dict], outcomes: dict[str, dict], h: str, minutes: int) -> tuple[list[dict], dict[str, str]]:
    """The session's included reads for one horizon, and every excluded one with its reason."""
    half_day = session_close(parse_ts(recs[0]["row_ts"])).time() != SESSION_CLOSE
    reads, excluded = [], {}
    for r in recs:
        snap = (r["pool"] if isinstance(r["pool"], dict) else {}).get(h) or {"left_out": "no snapshot for this horizon"}
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


def update(out_dir: Path, today: str | None = None, lane: Lane = LIVE) -> dict[str, str]:
    """Apply every sealed session after the watermark, oldest first, both horizons (the longer first,
    so the primary's promotion sees the same day's veto). A session is sealed once it is over and every
    read has a terminal grade at every horizon. An unsealed session, or one some of whose reads carry no
    snapshot, stops the run and is logged (fail closed); one none of whose reads carries a snapshot is
    from before the loop and is passed over. A session already applied is never applied again. A state
    made under other constants (its constants_hash) stops the run before anything is applied.
    Returns ``{h: what happened}``."""
    out_dir = Path(out_dir)
    today = today or datetime.now(ET).date().isoformat()
    outcomes = graded_outcomes(load_jsonl(out_dir / "grades.jsonl"), lane.horizons)
    order = sorted(lane.horizons, key=lambda h: -lane.horizons[h][0])
    states = {h: load_state(out_dir, lane.horizons[h][0]) for h in order}
    said = {h: "nothing new to apply" for h in order}
    changed = [h for h in order if states[h].get("constants_hash") != CONSTANTS_HASH]
    if changed:
        # the constants are fixed for the trial (06): a state learned under others is not added to
        _log(out_dir, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": CODE_HASH, "applied": False,
                       "why": f"constants changed since {', '.join(changed)} began: stopped, fail closed"})
        return {h: "constants changed; stopped" for h in order}
    watermark = min((s["last_session_applied"] or "") for s in states.values())
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
            state["last_session_applied"] = day
            if why or not recs:
                _log(out_dir, {**head, "horizon": h, "applied": False, "why": why or "no reads"})
                continue
            reads, excluded = _session_reads(recs, outcomes, h, minutes)
            body: dict = {"manifest": {"included": [r["row_ts"] for r in reads], "excluded": excluded}}
            if reads:
                version = reads[-1]["snapshot"]["baseline"]
                if state["baseline"] not in (None, version):
                    # a refitted baseline is a new reference: its calibration starts again
                    state["cal"] = cold_state()["cal"]
                    body["baseline_changed"] = {"from": state["baseline"], "to": version}
                state["baseline"] = version
                body["membership"] = membership(state, reads[-1]["snapshot"]["members"], day)
                primary = h == lane.primary
                body.update(apply_session(state, day, reads, minutes, primary,
                                          states[order[0]]["phone"]["harm_60"] if primary and h != order[0] else None,
                                          lane.graded_from_settled_open))
            # rounded after every session, as saved, so one night at a time and a rebuild agree to the bit
            states[h] = json.loads(_canonical(state))
            _log(out_dir, {**head, "horizon": h, "applied": bool(reads), **_rounded(body)})
            said[h] = f"{day}: applied, {len(reads)} reads"
    for h in order:
        save_state(out_dir, lane.horizons[h][0], states[h])
    return said


def _log(out_dir: Path, line: dict) -> None:
    with open(Path(out_dir) / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")


# ----------------------------------------------------------------------------- the seam

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
                               "phone": {"shows": state["phone"]["shows"], "on_phone": POOL_ON_PHONE and state["phone"]["shows"] == "pool",
                                         "promote_e": state["phone"]["promote"]["e"], "days": state["phone"]["promote"]["n"]},
                               "top": top, "frozen": state["frozen"]})

    def as_json(self) -> dict:
        return {**super().as_json(), "pool": self.pool}
