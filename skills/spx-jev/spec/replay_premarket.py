"""Replay the premarket lane over the saved nights and judge each premarket question against the open (read only).

    python3 spec/replay_premarket.py --out DIR                        # every saved night of the station's state
    python3 spec/replay_premarket.py --out DIR --workers 8            # the nights spread over 8 processes
    python3 spec/replay_premarket.py --out DIR --report FILE --from 2026-07-06 --to 2026-09-25 --state-dir DIR

Every night in the overnight store (state/spx_jev/overnight/, overnight.py) is read again at each of its
day's checkpoints (premarket.checkpoints) the way the lane reads it: the scene before the open
(premarket.make_premarket_scene), every label and gate (labels.registry.build_labels), and the requests
the lane would send (ask.build_requests) with the questions not due at the read left out (schedule.not_due);
a read the lane could not make (premarket.NoPreOpenRead) is kept with its reason. Every premarket question is
replayed as live whatever its status, so a dark question is judged before it goes live. A question's
answer here is the code's: the verdict on its own label's figure (the label whose verdict is one of its
options). JEV is never asked.

What happened next comes from the SPX session bars (state/reversion/bars/{day}-SPX.json): the gap from
the prior close to the settled open (the 09:34 close), the move from the settled open to 09:44, 10:04,
10:34 and the close, and the range of the first 30 and 60 minutes after it, each in the pre-open ruler
the day's reads stamped. A question is judged on the outcome its answers speak to:

    size         (it serves "size")                  the range of the first 30 minutes
    signed       (its answers are ordered down to up)  the move to 10:04
    sign_free    (the rest)                          the move to 10:04 counted along the question's reference
                                                     (its ref_side, REFERENCES), so above zero went its way

A sign-free question's reference is read from the figures its read wrote, each measured on one contract
(a roll refuses it), so a night whose reference was not measured is counted and left ungraded.

For each question at each read that asks it: the nights asked, asleep and missing a label, the answer
counts, per answer the share that rose (or went the reference's way) and the mean move at each mark, the
rank correlation where the answers are ordered (else the share of the outcome's variance the answers
explain), both halves of the nights, and a verdict:

    too few   under MIN_NIGHTS graded nights, or under two answers given on MIN_PER_ANSWER nights each
    holds     a permutation p, adjusted by Holm's method for every question and read tested beside it,
              under P_HOLDS, and the same sign (the same best answer) in both halves
    noise     otherwise

Read only: nothing under the state directory is written, and --out may not point inside it. Writes
{out}/reads.jsonl (one line per night and checkpoint), {out}/outcomes.jsonl (one line per day),
{out}/judged.json (one entry per question and read) and the report (--report, default {out}/report.md).
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from itertools import repeat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spx_jev import events, overnight, premarket  # noqa: E402
from spx_jev.ask import build_requests, get_path, load_questions  # noqa: E402
from spx_jev.labels import bitcoin  # noqa: E402
from spx_jev.labels.measures import ET, bar_time, is_num, settled_open  # noqa: E402
from spx_jev.labels.registry import build_labels  # noqa: E402
from spx_jev.lane import PREMARKET  # noqa: E402
from spx_jev.schedule import asks_at, not_due  # noqa: E402
from spx_jev.state_builder import DEFAULT_STATE_DIR, load_bars, previous_session  # noqa: E402

MIN_NIGHTS = 20
MIN_PER_ANSWER = 5
P_HOLDS = 0.05
SHUFFLES = 5_000
SEED = 20260927

# Answers ordered from the index's down side to its up side (or from small to large): the rank correlation reads them in this order.
ORDERED = {
    "overnight_move_vs_expected": ("big_down", "down", "flat", "up", "big_up"),
    "pm_overnight_session": ("quiet_night", "normal_night", "wide_night"),
    "overnight_bonds_vs_gap": ("overnight_ahead_down", "in_line", "overnight_ahead_up"),
    "btc_overnight_vs_futures": ("btc_ahead_down", "btc_ahead_up"),
}
# The outcomes, and the marks after the settled open they are measured at (None: the day's close).
MOVES = {"open_10": 10, "open_30": 30, "open_60": 60, "to_close": None}
RANGES = {"range_30": 30, "range_60": 60}
JUDGED_ON = {"size": "range_30", "signed": "open_30", "sign_free": "open_30"}


# ---- outcomes ------------------------------------------------------------------------------------

def close_by_mark(bars: list[dict], mark: datetime) -> float | None:
    """The close of the bar standing for ``mark``: the newest one that started at the mark or up to the
    lane's bar gap before it (lane.bar_gap_min), as the grader takes it."""
    near = [b for b in bars if mark - timedelta(minutes=PREMARKET.bar_gap_min) <= bar_time(b) <= mark]
    return float(near[-1]["close"]) if near else None


def outcome(state_dir: Path, day: date, ruler: float | None) -> dict:
    """What SPX did from the settled open, in the pre-open ruler ``ruler`` (points): ``{"settled_open",
    "prior_close", "gap", "open_10", "open_30", "open_60", "to_close", "range_30", "range_60"}``, a mark
    whose bar is missing left None; or ``{"omitted": why}``."""
    if not is_num(ruler) or not ruler > 0:
        return {"omitted": "no pre-open ruler"}
    bars = load_bars(state_dir, day.isoformat())
    start = settled_open(bars)
    if start is None:
        return {"omitted": "no SPX 09:34 bar on file"}
    prior = previous_session(state_dir, day.isoformat(), {})
    prior_close = float(prior[1][-1]["close"]) if prior else None
    opened = datetime.combine(day, time(9, 34), tzinfo=ET)
    out = {"settled_open": start, "prior_close": prior_close,
           "gap": None if prior_close is None else round((start - prior_close) / ruler, 4)}
    for name, minutes in MOVES.items():
        price = float(bars[-1]["close"]) if minutes is None else close_by_mark(bars, opened + timedelta(minutes=minutes))
        out[name] = None if price is None else round((price - start) / ruler, 4)
    for name, minutes in RANGES.items():
        win = [b for b in bars if opened < bar_time(b) <= opened + timedelta(minutes=minutes)]
        full = len(win) >= minutes - PREMARKET.bar_gap_min
        out[name] = round((max(float(b["high"]) for b in win) - min(float(b["low"]) for b in win)) / ruler, 4) if full else None
    return out


# ---- the reads -----------------------------------------------------------------------------------

def premarket_doc() -> dict:
    """The premarket lane's questions, every one of them live for the replay."""
    doc = copy.deepcopy(load_questions(PREMARKET.questions, lane_key=PREMARKET.key))
    for group in doc["groups"]:
        for q in group["questions"].values():
            q["replayed_from"], q["status"] = q.get("status"), "live"
    return doc


def own_label(q: dict, figures: dict) -> str | None:
    """The question's own label: the one of its labels whose figure's verdict is one of its options."""
    return next((p for p in q.get("labels") or [] if (figures.get(p) or {}).get("verdict") in q.get("options", [])), None)


def _sign(x) -> int | None:
    return None if not is_num(x) or x == 0 else (1 if x > 0 else -1)


def _value(figures: dict, path: str) -> float | None:
    return (figures.get(path) or {}).get("value")


def first_leg_side(scene, figures: dict) -> int | None:
    """The side of the night's first leg that moved: premarket.arc's value is the share of it the net move
    keeps, so the leg points the net's way when that share is positive and against it when negative."""
    net, kept = _sign(_value(figures, "premarket.where_now")), _sign(_value(figures, "premarket.arc"))
    return None if net is None or kept is None else net * kept


def before_leg_side(scene, figures: dict) -> int | None:
    """The side of the night before the leg since the previous checkpoint: the leg's own side when it added,
    the other side when it gave back or crossed (both are against the night before it)."""
    f = figures.get("premarket.since_checkpoint") or {}
    leg = _sign(f.get("value"))
    return None if leg is None else leg if f.get("verdict") == "added" else -leg


def before_report_side(scene, figures: dict) -> int | None:
    """The side of the night before the report: the report window's own side when it extended the night, the
    other side when it unwound it or carried futures across their 16:00 price."""
    f = figures.get("premarket.release_vs_night") or {}
    window = _sign(f.get("value"))
    return None if window is None else window if f.get("verdict") == "extended_night" else -window


def weekend_leg_side(scene, figures: dict) -> int | None:
    """The side of bitcoin's weekend leg, the one weekend.btc_path takes its reopen leg's way (bitcoin.way_of),
    measured on the read's night the same way; None when the label was not written."""
    if "weekend.btc_path" not in figures:
        return None
    legs = bitcoin.weekend_legs(scene.night, *bitcoin.weekend_edges(scene.now.astimezone(ET).date()), scene.now)
    return None if legs is None else bitcoin.way_of(legs[0].pct)


# Each sign-free question's reference (its ref_side where the set names one): its words, and its side at a read
# from the scene and the figures the read wrote, None when the read did not measure it.
REFERENCES = {
    "gap_origin": ("the night's net move", lambda scene, f: _sign(_value(f, "overnight.es_move"))),
    "night_legs_agree": ("the night's net move", lambda scene, f: _sign(_value(f, "premarket.legs"))),
    "overnight_arc": ("the night's first leg that moved", first_leg_side),
    "latest_leg_vs_night": ("the night's net move before the leg", before_leg_side),
    "release_vs_night": ("the night's net move before the report", before_report_side),
    "night_vs_last_hour": ("yesterday's last hour", lambda scene, f: _sign(_value(f, "premarket.vs_last_hour"))),
    "release_reaction_path": ("the report's reaction to 08:45", lambda scene, f: _sign(_value(f, "overnight.release_reaction"))),
    "btc_weekend_path": ("bitcoin's weekend leg", weekend_leg_side),
}


def read(state_dir: Path, doc: dict, day: date, checkpoint: str) -> dict:
    """One replayed read: its ruler, the night's side, and every premarket question's fate and code answer."""
    now = datetime.combine(day, time.fromisoformat(checkpoint), tzinfo=ET)
    out = {"day": day.isoformat(), "checkpoint": checkpoint}
    try:
        scene = premarket.make_premarket_scene(state_dir, now)
    except premarket.NoPreOpenRead as e:
        return {**out, "omitted": f"no read: {e}"}
    labels = build_labels(scene)
    questions = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    skip = not_due(doc, PREMARKET, now)
    requests, skipped = build_requests(labels.state, doc, skip=skip, gates=labels.gates)
    asked = {qid for r in requests for qid in r["questions"]}
    why = {qid: reason for g in skipped.values() for qid, reason in g.items()}
    fates = {}
    for qid, q in questions.items():
        label = own_label(q, labels.figures)
        fate = {"answer": labels.figures[label]["verdict"] if label else None}
        if kind_of(q, qid) == "sign_free":
            fate["side"] = REFERENCES[qid][1](scene, labels.figures)
        if qid in asked:
            fate["fate"] = "asked"
        elif qid in skip:
            fate["fate"] = "not_due"
        elif why.get(qid, "").startswith("asleep"):
            fate.update(fate="asleep", why=why[qid][len("asleep: "):])
        else:
            missing = [p for p in q.get("labels") or [] if get_path(labels.state, p) is None]
            fate.update(fate="missing", why="; ".join(f"{p}: {labels.omitted.get(p, 'not written')}" for p in missing) or why.get(qid))
        fates[qid] = fate
    return {**out, "ruler": round(scene.sigma, 4), "questions": fates,
            "figures": {p: f for p, f in labels.figures.items() if p.split(".")[0] in ("overnight", "premarket", "weekend")}}


def replay_day(state_dir: Path, doc: dict, day: date) -> tuple[list[dict], dict]:
    """Every checkpoint's read of one night, and what SPX did after the open in the ruler its reads stamped."""
    reads = [read(state_dir, doc, day, c) for c in premarket.checkpoints(day)]
    ruler = next((r["ruler"] for r in reversed(reads) if "ruler" in r), None)
    return reads, {"day": day.isoformat(), "calendar": events.uncovered(day, tier=events.PRE_OPEN), **outcome(state_dir, day, ruler)}


def replayable_days(state_dir: Path, first: date | None, last: date | None, now: datetime) -> list[date]:
    """The saved nights whose day is a finished market day, within ``first`` and ``last``."""
    days = [date.fromisoformat(d) for d in overnight.saved_days(state_dir)]
    return [d for d in days if overnight.night_window(d)[1] <= now and (first is None or d >= first) and (last is None or d <= last)]


# ---- judging -------------------------------------------------------------------------------------

def ranks(values: list[float]) -> list[float]:
    """Each value's rank, 1 upward, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=lambda k: values[k])
    out = [0.0] * len(values)
    k = 0
    while k < len(order):
        j = k
        while j + 1 < len(order) and values[order[j + 1]] == values[order[k]]:
            j += 1
        for m in range(k, j + 1):
            out[order[m]] = (k + j) / 2 + 1
        k = j + 1
    return out


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
    return None if sxx == 0 or syy == 0 else sxy / (sxx * syy) ** 0.5


def spearman(answers: list[int], values: list[float]) -> float | None:
    """The rank correlation between the answers' places in their order and the outcome."""
    return pearson(ranks([float(a) for a in answers]), ranks(values))


def explained(answers: list[str], values: list[float]) -> float | None:
    """The share of the outcome's variance between the answers' means (eta squared)."""
    mean = statistics.fmean(values) if values else 0.0
    total = sum((v - mean) ** 2 for v in values)
    if len(values) < 3 or total == 0:
        return None
    by: dict[str, list[float]] = {}
    for a, v in zip(answers, values):
        by.setdefault(a, []).append(v)
    return sum(len(vs) * (statistics.fmean(vs) - mean) ** 2 for vs in by.values()) / total


def best_answer(answers: list[str], values: list[float]) -> str | None:
    """The answer with the highest mean outcome among those given on MIN_PER_ANSWER nights or more."""
    by: dict[str, list[float]] = {}
    for a, v in zip(answers, values):
        by.setdefault(a, []).append(v)
    means = {a: statistics.fmean(vs) for a, vs in by.items() if len(vs) >= MIN_PER_ANSWER}
    return max(means, key=means.get) if means else None


def statistic(qid: str, answers: list[str], values: list[float]) -> float | None:
    """Spearman's rho for ordered answers, else eta squared."""
    if qid in ORDERED:
        return spearman([ORDERED[qid].index(a) for a in answers], values)
    return explained(answers, values)


def permutation_p(qid: str, answers: list[str], values: list[float], observed: float) -> float:
    """How often shuffled outcomes reach the observed statistic (its size, for rho)."""
    rng, shuffled, hits = random.Random(SEED), list(values), 0
    for _ in range(SHUFFLES):
        rng.shuffle(shuffled)
        s = statistic(qid, answers, shuffled)
        hits += s is not None and abs(s) >= abs(observed)
    return (hits + 1) / (SHUFFLES + 1)


def kind_of(q: dict, qid: str) -> str:
    if q.get("serves") == "size":
        return "size"
    return "signed" if qid in ORDERED else "sign_free"


def judged_value(kind: str, name: str, oc: dict, side: int | None) -> float | None:
    """The outcome ``name`` as the question's kind reads it: along the reference's side for sign-free answers."""
    v = oc.get(name)
    if v is None or kind != "sign_free" or name in RANGES:
        return v
    return None if side is None else v * side


def pattern(reason: str) -> str:
    """A reason with its counts and sizes blanked, so the same reason on different nights counts once; its dates
    and clock times stay."""
    return re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|(?<!\d)-?\d+(\.\d+)?",
                  lambda m: m.group(0) if re.search(r"\d[-:]", m.group(0)) else "#", reason)


def judge(qid: str, q: dict, checkpoint: str, reads: list[dict], outcomes: dict[str, dict]) -> dict:
    """One question at one read over every replayed night: its fates, answers, outcomes by answer and verdict."""
    kind = kind_of(q, qid)
    mine = [r for r in reads if r["checkpoint"] == checkpoint]
    fates = Counter(r["questions"][qid]["fate"] if "questions" in r else "no_read" for r in mine)
    reasons = {f: Counter(pattern(r["questions"][qid].get("why") or "") for r in mine
                          if "questions" in r and r["questions"][qid]["fate"] == f).most_common(3) for f in ("asleep", "missing")}
    graded = []
    for r in sorted(mine, key=lambda r: r["day"]):
        if "questions" not in r or r["questions"][qid]["fate"] != "asked" or r["questions"][qid]["answer"] is None:
            continue
        oc = outcomes.get(r["day"]) or {}
        row = {name: judged_value(kind, name, oc, r["questions"][qid].get("side")) for name in ["gap", *MOVES, *RANGES]}
        graded.append((r["day"], r["questions"][qid]["answer"], row))
    target = JUDGED_ON[kind]
    pairs = [(a, row[target]) for _, a, row in graded if row[target] is not None]
    by_answer = {}
    for a in (ORDERED.get(qid) or q.get("options", [])):
        rows = [row for _, x, row in graded if x == a and row[target] is not None]
        by_answer[a] = {"n": len(rows), **{name: _mean_share([row[name] for row in rows]) for name in ["gap", *MOVES, *RANGES]}}
    out = {"question": qid, "checkpoint": checkpoint, "kind": kind, "judged_on": target, "status": q.get("replayed_from"),
           "reference": REFERENCES[qid][0] if kind == "sign_free" else None,
           "fates": dict(fates), "reasons": reasons, "answers": dict(Counter(a for _, a, _ in graded)),
           "unanswered": sum(1 for r in mine if "questions" in r and r["questions"][qid]["fate"] == "asked"
                             and r["questions"][qid]["answer"] is None),
           "unfolded": sum(1 for r in mine if "questions" in r and r["questions"][qid]["fate"] == "asked"
                           and r["questions"][qid]["answer"] is not None and kind == "sign_free"
                           and r["questions"][qid].get("side") is None),
           "graded": len(pairs), "by_answer": by_answer}
    answers, values = [a for a, _ in pairs], [v for _, v in pairs]
    given = sum(1 for n in Counter(answers).values() if n >= MIN_PER_ANSWER)
    if len(pairs) < MIN_NIGHTS or given < 2:
        return {**out, "verdict": "too few", "basis": f"{len(pairs)} graded nights, {given} answers given on {MIN_PER_ANSWER}+ nights"}
    stat = statistic(qid, answers, values)
    half = len(pairs) // 2
    halves = [pairs[:half], pairs[half:]]
    name = "rho" if qid in ORDERED else "eta2"
    if stat is None:
        return {**out, "verdict": "noise", "basis": f"the outcome did not vary ({len(pairs)} nights)"}
    p = permutation_p(qid, answers, values, stat)
    if qid in ORDERED:
        split = [statistic(qid, [a for a, _ in h], [v for _, v in h]) for h in halves]
        agree = all(s is not None and s * stat > 0 for s in split)
    else:
        split = [best_answer([a for a, _ in h], [v for _, v in h]) for h in halves]
        agree = split[0] is not None and split[0] == split[1]
    return settled({**out, "statistic": name, name: round(stat, 3), "p": round(p, 4), "agree": agree,
                    "halves": [round(s, 3) if isinstance(s, float) else s for s in split]}, p)


def settled(j: dict, p_holm: float) -> dict:
    """A tested question's verdict and basis on ``p_holm``, its p adjusted for the questions tested beside it
    (its own p when tested alone)."""
    name = j["statistic"]
    return {**j, "p_holm": round(p_holm, 4), "verdict": "holds" if p_holm < P_HOLDS and j["agree"] else "noise",
            "basis": f"{name} {j[name]:+.3f}, p {j['p']:.3f} (Holm {p_holm:.3f}), halves {_halves_words(j['halves'])}"}


def holm(judged: list[dict]) -> list[dict]:
    """Every verdict with its p adjusted by Holm's step-down over the questions and reads tested together, so a
    batch of questions with no link to the open names none as holding in more than P_HOLDS of replays."""
    tested = sorted((k for k, j in enumerate(judged) if "p" in j), key=lambda k: judged[k]["p"])
    adjusted, running = {}, 0.0
    for rank, k in enumerate(tested):
        running = max(running, min(1.0, (len(tested) - rank) * judged[k]["p"]))
        adjusted[k] = running
    return [settled(j, adjusted[k]) if k in adjusted else j for k, j in enumerate(judged)]


def _mean_share(values: list[float | None]) -> dict:
    vs = [v for v in values if v is not None]
    return {"mean": round(statistics.fmean(vs), 3), "above_zero": round(sum(1 for v in vs if v > 0) / len(vs), 3)} if vs else {}


def _halves_words(split: list) -> str:
    return " / ".join(f"{s:+.3f}" if isinstance(s, float) else str(s) for s in split)


# ---- the report ----------------------------------------------------------------------------------

KIND_WORDS = {"size": "the range of the first 30 minutes from the settled open",
              "signed": "the move from the settled open to 10:04",
              "sign_free": "the move from the settled open to 10:04, counted along {reference}"}


def report(judged: list[dict], reads: list[dict], outcomes: dict[str, dict], days: list[date], bitcoin_context_days: int) -> str:
    graded_days = sorted(d for d, oc in outcomes.items() if "omitted" not in oc)
    no_read = Counter(pattern(r["omitted"]) for r in reads if "omitted" in r)
    lines = ["# SPX JEV premarket lane: replay over the saved nights", "",
             f"Replayed {len(days)} nights ({days[0]} to {days[-1]}) at every checkpoint, read only; JEV was not asked. "
             f"Every premarket question was replayed as live whatever its status, answered by the code (its label's verdict). "
             f"Outcomes are in the pre-open ruler (the median morning anchor of the last sessions), from the settled open "
             f"(the 09:34 close). {len(graded_days)} nights have an outcome"
             + (f" ({graded_days[0]} to {graded_days[-1]})." if graded_days else "."), ""]
    if no_read:
        lines += ["Reads that built nothing: " + "; ".join(f"{why} ({n})" for why, n in no_read.most_common()), ""]
    verdicts = Counter(j["verdict"] for j in judged)
    lines += [f"Of {len(judged)} questions at their reads: " + ", ".join(f"{v} {verdicts[v]}" for v in ("holds", "noise", "too few")) + ".",
              ""]
    tested = sum(1 for j in judged if "p" in j)
    lines += [f"Verdict: **holds** when a permutation p, adjusted by Holm's method for the {tested} questions and reads "
              f"tested together, is under {P_HOLDS} and the effect keeps its sign (or its best answer) in both halves of "
              f"the nights; **too few** under {MIN_NIGHTS} graded nights or under two answers given on {MIN_PER_ANSWER} "
              f"nights each; **noise** otherwise. A sign-free question's outcome is counted along its own reference "
              f"(the question's ref_side: the night's net move, the first leg, the report's reaction, bitcoin's weekend "
              f"leg ...), named under each question.", "",
              "| Question | Read | Asked | Asleep | Missing | Graded | Judged on | Statistic | Verdict |",
              "|---|---|---|---|---|---|---|---|---|"]
    for j in judged:
        f = j["fates"]
        lines.append(f"| {j['question']} | {j['checkpoint']} | {f.get('asked', 0)} | {f.get('asleep', 0)} | {f.get('missing', 0)} | "
                     f"{j['graded']} | {j['judged_on']} | {j['basis']} | {j['verdict']} |")
    for j in judged:
        lines += ["", f"## {j['question']} at {j['checkpoint']} ({j['status']})", "",
                  f"Judged on {KIND_WORDS[j['kind']].format(reference=j['reference'])}: {j['verdict']} ({j['basis']}).",
                  f"Fates: {', '.join(f'{k} {v}' for k, v in sorted(j['fates'].items()))}. "
                  f"Answers: {', '.join(f'{k} {v}' for k, v in sorted(j['answers'].items())) or 'none'}."]
        if j["unanswered"]:
            lines.append(f"Asked on {j['unanswered']} nights with no code answer: "
                         "no label of the question's carried a verdict among its options.")
        if j["unfolded"]:
            lines.append(f"Answered on {j['unfolded']} nights where the read did not measure {j['reference']}: not graded.")
        for fate, top in j["reasons"].items():
            if top:
                lines.append(f"Why {fate}: " + "; ".join(f"{why} ({n})" for why, n in top) + ".")
        share = "went the reference's way" if j["kind"] == "sign_free" else "above zero"
        lines += ["", f"| Answer | n | gap | to 09:44 | to 10:04 | to 10:34 | to close | range 30 | range 60 | share {share} at 10:04 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for a, row in j["by_answer"].items():
            cells = [f"{row[name]['mean']:+.3f}" if row.get(name) else "-" for name in ["gap", *MOVES, *RANGES]]
            share_cell = f"{row['open_30']['above_zero']:.0%}" if row.get("open_30") else "-"
            lines.append(f"| {a} | {row['n']} | " + " | ".join(cells) + f" | {share_cell} |")
    lines += ["", "## Bitcoin session questions", "",
              f"btc_gap_30m, btc_gap_streak, btc_link_today and btc_five_day_lead read today's /MBT quotes in the market "
              f"context; {bitcoin_context_days} saved session days carry them, so they are not replayed here"
              + (" (their replay belongs to the 30-minute lane's)." if bitcoin_context_days else " (no inputs on disk).")]
    return "\n".join(lines) + "\n"


def bitcoin_context_days(state_dir: Path) -> int:
    """How many saved market-context days quote /MBT (either file of the day)."""
    folder = Path(state_dir) / "spx_jev" / "context"
    days = set()
    for path in [*folder.glob("????-??-??.jsonl"), *(folder / "bars").glob("????-??-??.jsonl")]:
        if '"/MBT' in path.read_text(encoding="utf-8"):
            days.add(path.stem)
    return len(days)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay the premarket lane over the saved nights (read only).")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out", required=True, help="a folder outside the state dir for reads.jsonl and outcomes.jsonl")
    ap.add_argument("--report", help="the report's path (default {out}/report.md)")
    ap.add_argument("--from", dest="first", type=date.fromisoformat)
    ap.add_argument("--to", dest="last", type=date.fromisoformat)
    ap.add_argument("--workers", type=int, default=1, help="processes to spread the nights over")
    args = ap.parse_args(argv)
    state_dir, out = Path(args.state_dir), Path(args.out)
    if out.resolve().is_relative_to(state_dir.resolve()):
        ap.error(f"--out {out} is inside the state dir {state_dir}; the replay never writes there")
    days = replayable_days(state_dir, args.first, args.last, datetime.now(ET))
    if not days:
        ap.error(f"no finished nights under {state_dir / overnight.OVERNIGHT_SUBDIR}")
    doc = premarket_doc()
    reads, outcomes = [], {}
    with ProcessPoolExecutor(args.workers) if args.workers > 1 else nullcontext() as pool:
        run = pool.map if pool else map
        nights = run(replay_day, repeat(state_dir), repeat(doc), days)
        for day, (mine, oc) in zip(days, nights):
            reads += mine
            outcomes[day.isoformat()] = oc
            print(f"{day}: {sum(1 for r in mine if 'questions' in r)} of {len(mine)} reads built", file=sys.stderr)
    judged = holm([judge(qid, q, c, reads, outcomes) for g in doc["groups"] for qid, q in g["questions"].items()
                   for c in PREMARKET.schedule if q.get("schedule") is None or asks_at(q["schedule"], c, days[-1], PREMARKET.schedule)])
    out.mkdir(parents=True, exist_ok=True)
    (out / "reads.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reads), encoding="utf-8")
    (out / "outcomes.jsonl").write_text("".join(json.dumps(o) + "\n" for o in outcomes.values()), encoding="utf-8")
    (out / "judged.json").write_text(json.dumps(judged, indent=1) + "\n", encoding="utf-8")
    report_path = Path(args.report) if args.report else out / "report.md"
    report_path.write_text(report(judged, reads, outcomes, days, bitcoin_context_days(state_dir)), encoding="utf-8")
    for j in judged:
        print(f"  {j['question']} at {j['checkpoint']}: {j['verdict']} ({j['basis']})")
    print(f"{len(days)} nights, {len(reads)} reads -> {out}; report {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
