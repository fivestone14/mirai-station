"""The learning loop on the average-price grade, switched on for the live lane (lane.Lane.integral_loop).

Switched on, the grader runs this loop beside pool.PoolWeights and weights.json reports it under ``pool_integral``, beside
the end-price loop's report: the same experts, day-level update, e-processes and statuses (pool.py, read as a
library, never changed here), learned on the
lane's primary box from the average-price sum's graded reads alone, each read's outcome the label integral_grades.jsonl
gave it under this integral.RULE_VERSION, never the end price's band. The end-price loop keeps learning beside it.
Neither loop's pool reaches the phone: the call keeps its exact blend here (shown), and since 2026-10-06 Pool 2's
combined forecast replaces it after the read (mirai_prediction.live_call); the promotion that once decided the call
was retired on 2026-10-07. Switched off, nothing here is imported, read or written. The gate for trusting what it
learns is GATE_SESSIONS SPX sessions of average-price grades;
the dry run shows what the loop has learnt from the graded history, and how far the gate is:

    python3 -m spx_jev.grade --integral-loop-dry-run    # built from nothing in a scratch folder; no live file is written

The reference R is the time-of-day odds counted on the average price (clock.integral_odds), as a read on the session
could know them: the counts the clock stored in clock_integral_days.json for the counted sessions before it, read and
never counted here, so no end-price count can reach it. Its two experts are named by that source, clock and
clock_cal, with the read's raw odds as raw_clock (pool.w0), where the end-price loop's frozen reference names them
baseline and baseline_cal; the state keeps its version as ``reference_version``. Its version moves only with the
counting rule and the odds' own constants (REFERENCE_CONSTANTS), so the calibration carries from one session to the
next as the counted sessions roll and starts again when either changes. A session whose blended reads have no reference on file (the counts were
recounted under a new rule and no read has counted the days before it yet) stops the update, as an unsealed one does,
rather than be passed over for good. JEV's sum is the average-price sum's own
odds, blend50 its blend with those odds as the phone showed it, and the question block's answers are the ones the
read's end-price snapshot wrote down (pool.snapshot's ``q_probs``, ``members``, ``fresh``): the same answers, given
before either outcome.

The forecasts of a read are formed when its session is applied, from the state as of the last session applied, which
is the state a read that day would have seen: nothing is written at the read. A session is sealed once every read's
primary is terminal in grades.jsonl and each one graded there has its line in integral_grades.jsonl, and every call
standing on its average-price sum alone (grade.average_alone: its end-price sums got no answer) has its own; an unsealed one
stops the update (fail closed), and one none of whose reads carries an average-price call is from before the question
and is passed over. Left out, as the end-price loop leaves them out (pool._session_reads): a half day, a scheduled event inside the window
(pool.event_inside), a read with no outcome; and here also a stale read (integral.stale_read), a read with no
average-price call, and one whose answers or reference are not on file, each with its reason. A call standing alone is
learned like any other once its read's end-price snapshot carries the questions' answers (pool.answered), which
the snapshot keeps even when JEV's end-price sum is missing.

Its constants are pool's with this loop's outcome, the grade's rule, the reference's rule and LOOP_VERSION: a state
learned under any others, an end-price state among them, is never added to; it is kept in archive/ and the loop learns
again from the records, as pool.update does, so the two histories can never be mixed, whichever way the switch is
flipped.

Files, beside the lane's records, apart from the end-price loop's:
    pool_30_integral.json        the state of the primary box (pool_{minutes}_integral.json)
    pool_integral_log.jsonl      one line per session applied or refused, with its manifest
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import clock, integral, pool
from .grade import INTEGRAL_NAME, average_alone, average_call
from .lane import LIVE, Lane
from .sessions import SESSION_CLOSE, session_close
from .state_builder import ET, load_jsonl, parse_ts

LOOP_VERSION = 1                # bump when this loop's learning changes: a state from before is learned again (update)
GATE_SESSIONS = 10              # SPX sessions of average-price grades before Will decides whether to trust what it learns
LOG_NAME = "pool_integral_log.jsonl"
SOURCE = "clock"                # this loop's reference is the live time-of-day odds, so its experts are clock and clock_cal
CONSTANTS = {**pool.CONSTANTS, "w0": pool.w0(SOURCE), "outcome": "integral.label", "integral_rule": integral.RULE_VERSION,
             "reference_rule": clock.INTEGRAL_RULE_VERSION, "loop_version": LOOP_VERSION}
CONSTANTS_HASH = hashlib.sha256(json.dumps(CONSTANTS, sort_keys=True).encode()).hexdigest()[:16]
CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def reference_constants() -> dict:
    """The numbers the reference's odds are formed with (clock.py): a change to one is a new reference."""
    return {"shrink": clock.SHRINK, "min_sessions": clock.MIN_SESSIONS, "max_sessions": clock.MAX_SESSIONS,
            "min_scored_reads": clock.MIN_SCORED_READS}


def cold_state() -> dict:
    """pool.cold_state under this loop's constants and its reference's names."""
    return {**pool.cold_state(SOURCE), "constants_hash": CONSTANTS_HASH}


def state_path(out_dir: Path, lane: Lane = LIVE) -> Path:
    return Path(out_dir) / f"pool_{lane.horizons[lane.primary][0]}_integral.json"


def load_state(out_dir: Path, lane: Lane = LIVE) -> dict:
    try:
        return pool.renamed(json.loads(state_path(out_dir, lane).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return cold_state()


def save_state(out_dir: Path, lane: Lane, state: dict) -> None:
    """As pool.save_state writes it: canonical, in one step."""
    path = state_path(out_dir, lane)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(pool._canonical(state), encoding="utf-8")
    os.replace(tmp, path)


class Reference:
    """The average-price time-of-day odds over ``days`` (each counted session's ``{phase: {outcome: n}}``), in the shape
    pool.snapshot reads a Baseline in: the phase's odds shrunk toward the whole day's (clock._phase_odds, as
    clock.integral_odds gives them), the whole day's shares, and the counting rule's version."""

    def __init__(self, days: list[dict], version: str) -> None:
        self.days, self.version = days, version

    def clock(self, h: str, now: datetime) -> dict[str, float]:
        return clock._phase_odds(self.days, clock.phase_of(now))["probabilities"]

    def whole_day(self, h: str) -> dict[str, float]:
        whole = Counter()
        for counts in self.days:
            for c in counts.values():
                whole.update(c)
        n = sum(whole.values())
        return {o: whole[o] / n for o in clock.OUTCOMES}


def reference(cache: dict, day: str, version: str) -> Reference | None:
    """The reference a read on ``day`` could know, from the clock's stored days (``cache``): the counted sessions among
    the clock.MAX_SESSIONS before it with clock.MIN_SCORED_READS reads, as clock.integral_odds counts them; None with
    fewer than clock.MIN_SESSIONS."""
    days = sorted((d for d in cache if d < day), reverse=True)[:clock.MAX_SESSIONS]
    counted = [cache[d]["counts"] for d in days if sum(sum(c.values()) for c in cache[d]["counts"].values()) >= clock.MIN_SCORED_READS]
    return Reference(counted, version) if len(counted) >= clock.MIN_SESSIONS else None


def references(out_dir: Path, lane: Lane = LIVE) -> tuple[dict, str]:
    """The clock's stored counts on the average price (clock_integral_days.json, under its rule) and the version of
    the reference they form, its counting rule and the odds' own constants: what reference() reads."""
    key = clock._integral_rule_key(lane)
    version = f"integral_clock:{hashlib.sha256(json.dumps([key, reference_constants()], sort_keys=True).encode()).hexdigest()[:16]}"
    return clock._load_cache(Path(out_dir) / clock.INTEGRAL_CACHE_NAME, key), version


def average_lines(out_dir: Path, lane: Lane = LIVE) -> dict[str, dict]:
    """``{row_ts: its line}`` of the primary box in integral_grades.jsonl under this rule_version, the first of each."""
    out: dict[str, dict] = {}
    for g in load_jsonl(Path(out_dir) / INTEGRAL_NAME):
        if g.get("horizon") == lane.primary and g.get("rule_version") == integral.RULE_VERSION:
            out.setdefault(g.get("row_ts"), g)
    return out


def _session_reads(recs: list[dict], lines: dict[str, dict], state: dict, ref: Reference | None,
                   lane: Lane) -> tuple[list[dict], dict[str, str]]:
    """pool._session_reads on the average price: the session's included reads, each with its snapshot formed from ``state`` and its average-price label, and every
    excluded one with its reason."""
    h, minutes = lane.primary, lane.horizons[lane.primary][0]
    half_day = session_close(parse_ts(recs[0]["row_ts"])).time() != SESSION_CLOSE
    reads, excluded = [], {}
    for r in recs:
        avg, line = average_call(r, h, lane), lines.get(r["row_ts"])
        own = (r.get("pool") if isinstance(r.get("pool"), dict) else {}).get(h) or {}
        if half_day:
            excluded[r["row_ts"]] = "a half day"
        elif pool.event_inside(r, minutes):
            excluded[r["row_ts"]] = f"a scheduled event inside the {minutes}-minute window"
        elif avg is None:
            excluded[r["row_ts"]] = "no average-price call on this read"
        elif line is None or not line.get("graded"):
            excluded[r["row_ts"]] = "no outcome on the average price" + (f": {line.get('reason')}" if line else "")
        elif line.get("stale_read"):
            excluded[r["row_ts"]] = "a stale read: no bar traded its spot in the minutes up to it"
        elif "members" not in own:
            excluded[r["row_ts"]] = "no answers on file: the read's end-price snapshot was left out"
        elif ref is None:
            # an unblended read had no odds to form a reference from; a blended one stops the update first (update)
            excluded[r["row_ts"]] = (f"no snapshot: fewer than {clock.MIN_SESSIONS} sessions of time-of-day odds on the average "
                                     "price before this one")
        else:
            blended = (avg.get("blend") or {}).get("used")
            # a call that showed this loop's pool keeps the exact blend beside it (shown), which is what blend50 was
            exact = avg.get("blend50_exact") or avg["probabilities"]
            snap = pool.snapshot(state, ref, h, parse_ts(r["row_ts"]), avg["jev"]["probabilities"] if blended else exact,
                                 avg["clock"]["probabilities"] if blended else None, exact,
                                 own["q_probs"], own["members"], set(own["fresh"]), SOURCE)
            if "left_out" in snap:
                excluded[r["row_ts"]] = f"no snapshot: {snap['left_out']}"
            else:
                reads.append({"row_ts": r["row_ts"], "snapshot": snap, "outcome": line["label"]})
    return reads, excluded


def update(out_dir: Path, today: str | None = None, lane: Lane = LIVE, into: Path | None = None) -> dict[str, str]:
    """Apply every sealed session after the watermark, oldest first, to the primary box, reading the lane's records in
    ``out_dir`` and keeping the state and the log in ``into`` (``out_dir`` itself unless the dry run names another). A
    session already applied is never applied again. A state made under other constants, the end price's among them,
    is never added to: it is kept in archive/ and the loop learns again from cold, every session it had applied
    replayed under these constants with no bets (pool.apply_session's ``replay``), then on as usual; the forecasts are
    formed at the update, so the replay forms them under these constants. Returns ``{h: what happened}``. Mirrors
    pool.update on the one box and the average-price label."""
    out_dir = Path(out_dir)
    into = Path(into) if into is not None else out_dir
    today = today or datetime.now(ET).date().isoformat()
    h = lane.primary
    state = load_state(into, lane)
    if state.get("constants_hash") != CONSTANTS_HASH:
        replay_until = state.get("last_session_applied") or ""
        path = state_path(into, lane)
        kept = str(pool.archive_state(path, state)) if path.exists() else None
        _log(into, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": CODE_HASH, "applied": False,
                    "rebuild": {"from": state.get("constants_hash"), "to": CONSTANTS_HASH, "kept": kept,
                                "replay_until": replay_until or None},
                    "why": f"constants changed since {h} began on the average price: learned again from the records"})
        # the replay's last day is kept in the state, so a rebuild stopped part way still bets nothing on those days
        state = {**cold_state(), "replay_until": replay_until or None}
    said = {h: "nothing new to apply"}
    outcomes = pool.graded_outcomes(load_jsonl(out_dir / "grades.jsonl"), lane.horizons)
    lines = average_lines(out_dir, lane)
    cache, version = references(out_dir, lane)
    hour_dir = out_dir / "hour"
    for day in sorted(p.stem for p in hour_dir.glob("*.jsonl")) if hour_dir.exists() else []:
        if day <= (state["last_session_applied"] or "") or day >= today:
            continue
        recs, seen = [], set()
        for r in load_jsonl(hour_dir / f"{day}.jsonl"):
            if (isinstance(r.get("by"), dict) or average_alone(r, lane)) and r.get("row_ts") not in seen:
                seen.add(r["row_ts"])
                recs.append(r)
        head = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": day, "code": CODE_HASH, "horizon": h}
        # a read graded at the box in grades.jsonl waits for its average-price line; one closed out there needs none; a
        # call standing on its average-price sum alone, which grades.jsonl never grades, waits for its own line
        unsealed = [r for r in recs if (r["row_ts"] not in lines if average_alone(r, lane) else
                                        h not in outcomes.get(r["row_ts"], {}).get("done", set())
                                        or (h in outcomes[r["row_ts"]]["bands"] and r["row_ts"] not in lines))]
        if recs and not any(average_call(r, h, lane) for r in recs):
            why = "no read carries an average-price call: before the question"
        elif unsealed:
            _log(into, {**head, "applied": False, "why": "unsealed: a read still has its average-price grade to come"})
            said = {h: f"{day}: unsealed; stopped"}
            break
        else:
            why = None
        ref = reference(cache, day, version)
        if not why and ref is None and any(((average_call(r, h, lane) or {}).get("blend") or {}).get("used") for r in recs):
            # the reads were blended with odds the clock counted, so the counts exist to be recounted: wait for them
            _log(into, {**head, "applied": False, "why": "no reference on file for reads blended with one: stopped, fail closed"})
            said = {h: f"{day}: no reference on file; stopped"}
            break
        if why or not recs:
            state["last_session_applied"] = day
            _log(into, {**head, "applied": False, "why": why or "no reads"})
            continue
        reads, excluded = _session_reads(recs, lines, state, ref, lane)
        body = pool.learn_session(state, day, reads, excluded, lane.horizons[h][0], True, lane.graded_from_settled_open, SOURCE)
        state = json.loads(pool._canonical(state))      # rounded after every session, as pool.update does
        _log(into, {**head, "applied": bool(reads), **pool._rounded(body)})
        said[h] = f"{day}: applied, {len(reads)} reads"
    save_state(into, lane, state)
    return said


def shown(average: dict | None) -> dict | None:
    """The call as the phone shows it and the grader grades it: the average-price sum as it came, its exact blend, said
    under ``shown_source`` (pool.SHOWN_BLEND). This loop's pool never replaces it (the promotion was retired on
    2026-10-07; Pool 2 takes the call over after the read, mirai_prediction.live_call). An average with no odds (its
    error) is returned as it came."""
    if not isinstance(average, dict) or not isinstance(average.get("probabilities"), dict):
        return average
    return {**average, "shown_source": pool.SHOWN_BLEND}


def _log(out_dir: Path, line: dict) -> None:
    with open(Path(out_dir) / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")


def learned_fresh(out_dir: Path) -> list[dict]:
    """The fresh picks (the sum record's ``fresh``) of every read this loop has learned from: those its log lists as
    included in a session it applied, each once."""
    by_day: dict[str, set[str]] = {}
    for x in load_jsonl(Path(out_dir) / LOG_NAME):
        if x.get("applied"):
            for ts in (x.get("manifest") or {}).get("included", []):
                by_day.setdefault(ts[:10], set()).add(ts)
    out, seen = [], set()
    for day, stamps in sorted(by_day.items()):
        for r in load_jsonl(Path(out_dir) / "hour" / f"{day}.jsonl"):
            if r.get("row_ts") in stamps and r["row_ts"] not in seen:
                seen.add(r["row_ts"])
                out.append(r.get("fresh") or {})
    return out


class IntegralPoolWeights(pool.PoolWeights):
    """pool.PoolWeights on the average-price grade (its learn, mirrored): learn() applies every newly sealed session to
    this loop's state and reports each live question's standing from it, every weight still 1.0, and its ``n`` over the
    reads this loop learned from (learned_fresh), not the end-price loop's."""

    method = "pool_v1_integral"

    @classmethod
    def learn(cls, graded: list[dict], allowed: dict[str, set[str]], out_dir: Path | None = None,
              lane: Lane = LIVE) -> "IntegralPoolWeights":
        applied = update(out_dir, lane=lane) if out_dir is not None else {}
        state = load_state(out_dir, lane) if out_dir is not None else cold_state()
        fresh = learned_fresh(out_dir) if out_dir is not None else []
        n = Counter(qid for f in fresh for qid, p in f.items() if qid in allowed and str(p) in allowed[qid])
        block = {side: pool._prob(state["block"][side]) for side in pool.SIDES}
        questions = {}
        for qid in sorted(allowed):
            ev = state["evidence"].get(qid)
            entry = {"weight": 1.0, "n": n[qid], "in_step_3": True,
                     "why": "every live question keeps its sentence; the loop's standing on the average price is a label for a person"}
            if ev is not None:
                entry.update({"days": ev["days"], "status": ev["status"],
                              "e": {part: {k: ev[part][k]["e"] for k in ("better", "worse")} for part in ("move", "direction")},
                              "against_no_change": {side: block[side].get(qid, 0.0) / block[side][pool.NO_CHANGE] for side in pool.SIDES}})
            questions[qid] = entry
        top = {side: pool._prob(state["top"][side]) for side in pool.SIDES}
        return cls(questions, {"applied": applied, "last_session_applied": state["last_session_applied"],
                               "top": top, "frozen": state["frozen"], "outcome": "average price"})


def dry_run(out_dir: Path, lane: Lane = LIVE, today: str | None = None) -> dict:
    """The loop rebuilt from nothing on the lane's graded history in ``out_dir``, its state and log kept in a scratch
    folder that is removed after, whatever the switch says: nothing under ``out_dir`` is written. Returns what update
    said, the state it reached and its log lines."""
    work = Path(tempfile.mkdtemp(prefix="integral_loop."))
    try:
        said = update(out_dir, today, lane, into=work)
        return {"said": said, "state": load_state(work, lane), "log": load_jsonl(work / LOG_NAME)}
    finally:
        shutil.rmtree(work)


def describe(run: dict, lane: Lane = LIVE) -> list[str]:
    """dry_run's result in plain lines: the sessions and reads it learned from, the sessions passed over (before the
    question, or no reads) apart from those every read of which was left out, what it left out and why, the pool's
    weights, whether they are frozen, and each question's standing against "no change"."""
    state, log = run["state"], run["log"]
    applied = [x for x in log if x.get("applied")]
    left_out = Counter(why for x in log for why in (x.get("manifest") or {}).get("excluded", {}).values())
    refused = [x for x in log if x.get("applied") is False and x.get("session")]
    all_out = sum(1 for x in refused if "manifest" in x)
    passed = sum(1 for x in refused if "manifest" not in x and not x["why"].startswith(("unsealed", "no reference")))
    gate = "met" if len(applied) >= GATE_SESSIONS else f"{GATE_SESSIONS - len(applied)} to go"
    out = [f"the {lane.name} lane's loop on the average-price grade, rebuilt in a scratch folder (nothing live was written): "
           f"{run['said'][lane.primary]}",
           f"sessions learned from: {len(applied)}, the gate is {GATE_SESSIONS} ({gate}); passed over: {passed}; "
           f"every read left out: {all_out}; last applied {state['last_session_applied'] or 'none'}",
           f"reads learned from: {sum(len(x['manifest']['included']) for x in applied)}; left out: {sum(left_out.values())}"]
    out += [f"  {n} {why}" for why, n in left_out.most_common()]
    top = {side: pool._prob(state["top"][side]) for side in pool.SIDES}
    out.append("the pool's weights, move / direction: " + ", ".join(f"{n} {top['M'][n]:.3f} / {top['D'][n]:.3f}" for n in sorted(top["M"])))
    out.append(f"frozen: {state['frozen'] or 'no'}")
    block = {side: pool._prob(state["block"][side]) for side in pool.SIDES}
    for q, ev in sorted(state["evidence"].items()):
        vs = {side: block[side].get(q, 0.0) / block[side][pool.NO_CHANGE] for side in pool.SIDES}
        status = ", ".join(f"{part} {ev['status'][part]}" for part in ("move", "direction") if ev["status"][part]) or "not earning"
        out.append(f"  {q}: {vs['M']:.2f} / {vs['D']:.2f} of \"no change\" over {ev['days']} days; e better {ev['move']['better']['e']:.2f} / "
                   f"{ev['direction']['better']['e']:.2f}; {status}{'; suppressed' if ev['status']['suppressed'] else ''}")
    return out
