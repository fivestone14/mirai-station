"""Steps 3 and 4 of the pipeline: the live answers become sentences, the sum questions sum them.

    answers (step 2)  ->  answer_sentences()  ->  hour_request()  ->  JEV  ->  one reply, one answer per sum

``next_30``, blended half and half with the time-of-day odds (clock.py), is the sum the phone
shows and the one the question weights learn from; ``next_60`` rides on the same sentences in the
same request, is blended the same way, and is graded beside it so the two horizons can be compared.

The question weights (weights.QuestionWeights, written by the grader) decide which answers are
worth a sentence: a question whose weight is under weights.MIN_WEIGHT is left out, with the reason
kept. The weights are neutral for now, so nothing is left out on their account.

A lane (lane.py) brings its own sums doc and horizons; the tape lane's one sum is five-way
(down_big, down_small, flat, up_small, up_big, unsure) in tape units, so its request carries a
context line that prices the unit and the bands for the read, and its summary is read three ways
for grading: raw, direction and size. Everything defaults to LIVE.

The average-price sum (the lane's ``average``) rides on the same sentences in a request of its own,
so the end-price sums' request is exactly what it was: where the average price over the primary's
window sits against the read, up, flat or down, with no unsure. Its context line says what the
average is and gives the flat edge in index points for the read (average_window: the end price's
flat band narrowed by integral.factor, the edge the average-price grade sets it against). Its
summary (average_summary) rides on the hour summary under ``average``: the phone's call.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import integral
from .ask import fill_question, jev_only
from .cuts import TAPE_BIG_UNITS, TAPE_FLAT_UNITS
from .lane import LIVE, RECORD, Lane
from .weights import MIN_WEIGHT, QuestionWeights

FIVE = ("down_big", "down_small", "flat", "up_small", "up_big")   # a RECORD horizon's outcomes, in order
UNITS = ("sigma is today's expected move for the S&P 500 index. Each answer below was given by JEV about this moment, "
         "except those marked held, which were given at the time shown and carried forward unchanged")


def load_hour_doc(path: Path | str | None = None, lane: Lane = LIVE) -> dict:
    """The sums' doc for step 4, its constants filled. Flat: ``{"primary", "questions": {qid: {...}}}``, no groups."""
    path = path or lane.hour_doc
    qids = tuple(lane.horizons) + ((lane.average,) if lane.average else ())
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    qs = d.get("questions")
    if not isinstance(qs, dict) or any(q not in qs for q in qids):
        raise ValueError(f"{path}: needs questions {qids}")
    if d.get("primary", lane.primary) != lane.primary:
        raise ValueError(f"{path}: primary is {d.get('primary')!r} but the code sums for {lane.primary!r}")
    if d.get("average", lane.average) != lane.average:
        raise ValueError(f"{path}: average is {d.get('average')!r} but the code asks {lane.average!r}")
    d["questions"] = {qid: fill_question(q, f"{Path(path).name} {qid}") for qid, q in qs.items()}
    return d


def named_levels(q: dict, answer: dict) -> dict:
    """A JEV score answer with its levels named as the question's ``options`` gives them (level n is
    options[n], the set's convention), so its pick and probabilities read like a choice's in the sums,
    the question weights and the learning loop. Any other answer, or one naming a level the question
    does not have, is returned as it came."""
    probs, names = answer.get("probabilities"), q.get("options") or []
    if answer.get("type") != "score" or not probs:
        return answer
    levels = list(enumerate(probs)) if isinstance(probs, list) else [(str(k), v) for k, v in probs.items()]
    if not all(str(n).isdigit() and int(n) < len(names) for n, _ in levels):
        return answer
    return {**{k: v for k, v in answer.items() if k != "legend"}, "probabilities": {names[int(n)]: p for n, p in levels}}


def _sure(answer: dict) -> float | None:
    """How much probability JEV put on the option it picked."""
    p = answer.get("probabilities")
    pick = answer.get("pick")
    if isinstance(p, dict) and pick in p and isinstance(p[pick], (int, float)):
        return float(p[pick])
    n = answer.get("noul")
    if isinstance(n, (int, float)):
        return float(n) if pick == "true" else 1.0 - float(n)
    return None


def held_clock(held_from: str) -> str:
    """A held answer's time for a sentence JEV reads: the market clock it was given at."""
    return datetime.fromisoformat(held_from).strftime("%H:%M ET")


def one_sentence(q: dict, answer: dict) -> str | None:
    """'<the ask> <the option, in words>, JEV was 98% sure'. The ask is the question's one short plain
    sentence (every question in the set carries one); the instructions, which name the labels JEV read
    and the order to check the options in, stand in only for a doc written without asks."""
    pick = answer.get("pick")
    if pick is None:
        return None
    ask = (q.get("ask") or q["instructions"]).strip()
    if q["type"] == "noul":
        words = "yes" if pick == "true" else "no"
    else:
        words = str(pick).replace("_", " ")
    sure = _sure(answer)
    tail = f", JEV was {round(sure * 100)}% sure" if sure is not None else ""
    held = f" (held since {held_clock(answer['held_from'])}, not re-asked)" if answer.get("held_from") else ""
    return f"{ask} {words}{tail}{held}"


def answer_sentences(doc: dict, answered: dict[str, dict], weights: QuestionWeights | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Step 3. ``answered`` is ``{qid: answer}`` as the card stores them (pick, probabilities, noul, ...).
    Returns the sentences to send and, separately, what was left out and why."""
    weights = weights or QuestionWeights()
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    sentences: dict[str, str] = {}
    left_out: dict[str, str] = {}
    for qid, a in answered.items():
        q = by_id.get(qid)
        if q is None:
            left_out[qid] = "not a question in the doc"
            continue
        if q.get("status") == "shadow":
            left_out[qid] = "a shadow forecast, never an input"
            continue
        if not weights.in_step_3(qid):
            left_out[qid] = f"weight {weights.weight(qid):.2f} is below the {MIN_WEIGHT} cut after grading"
            continue
        s = one_sentence(q, a)
        if s is None:
            left_out[qid] = "no pick in the answer"
            continue
        sentences[qid] = s
    return sentences, left_out


def band_of(ruler: dict) -> dict:
    """A RECORD horizon's bands for this read, in index points: the cuts' fractions of a tape unit times
    the unit measured on the tape (labels.rulers.tape_unit). Stored on the hour record, so the grader reads
    the same bands JEV was told."""
    unit = float(ruler["unit_points"])
    return {"flat_points": round(TAPE_FLAT_UNITS * unit, 2), "big_points": round(TAPE_BIG_UNITS * unit, 2),
            "flat_units": TAPE_FLAT_UNITS, "big_units": TAPE_BIG_UNITS}


def unit_line(ruler: dict, band: dict) -> str:
    """The context line that turns the unit into points, since JEV cannot multiply a unit by a fraction."""
    unit, flat, big = (f"{float(x):.1f}" for x in (ruler["unit_points"], band["flat_points"], band["big_points"]))
    rank = ruler.get("rank")      # the unit against the same minute on the prior sessions (labels.rulers.unit_rank), when there is one
    placed = f", in the {rank['band']} for this minute, wider than {rank['higher_than']} of {rank['of']} prior sessions" if rank else ""
    return (f"one tape unit is {unit} points{placed}; flat is within {flat} points either way ({band['flat_units']:g} of a unit); "
            f"small is {flat} to {big} points; big is more than {big} points ({band['big_units']:g} of a unit)")


def hour_request(sentences: dict[str, str], hour_doc: dict | None = None, context: dict | None = None,
                 lane: Lane = LIVE, ruler: dict | None = None) -> dict:
    """Step 4's request: the sentences are the state, the lane's end-price sums ride on top in one call (the
    average-price sum goes in its own, average_request). With a ``ruler`` (a lane on the tape) the context
    also prices the unit and the bands for this read."""
    hour_doc = hour_doc or load_hour_doc(lane=lane)
    ctx = {"symbol": "SPX", "horizon": ", and ".join(f"the next {m} minutes" for m, _ in lane.horizons.values()), "units": UNITS}
    if ruler:
        ctx["unit"] = unit_line(ruler, band_of(ruler))
    ctx.update(context or {})
    return {"id": "hour", "state": {"context": ctx, "answers": sentences},
            "questions": {qid: jev_only(q) for qid, q in hour_doc["questions"].items() if qid in lane.horizons}}


def average_window(minutes: int, flat_points: float) -> dict:
    """The window the average-price sum forecasts: its minutes, the end price's flat band for it in points, and the
    edge the average is set against, that band narrowed by integral.factor, as the average-price grade narrows it."""
    return {"minutes": minutes, "flat_points": round(flat_points, 2), "edge_points": round(integral.factor(minutes) * flat_points, 2)}


def average_line(window: dict, lane: Lane = LIVE) -> str:
    """The context line of the average-price request: what the average over the window is, in one plain sentence,
    and the flat edge in points for this read, measured from the read's price or, on a lane graded from it, the
    settled open."""
    span, ref = ((f"the {window['minutes']} minutes after the settled open", "the settled open") if lane.graded_from_settled_open
                 else (f"the next {window['minutes']} minutes", "the price now"))
    edge = f"{float(window['edge_points']):.2f}"
    return (f"the average price over {span} counts every minute's closing price equally, so an early move counts for longer than a late "
            f"one; the average is flat when it sits within {edge} points of {ref} either way, up when it sits more than {edge} points "
            f"above it, down when it sits more than {edge} points below it")


def average_request(sentences: dict[str, str], window: dict, hour_doc: dict | None = None, context: dict | None = None,
                    lane: Lane = LIVE) -> dict:
    """The average-price sum's request: the same sentences as the end-price sums', its one question, and a context
    that names its window and prices its flat edge (average_line). It carries no tape unit line, whose flat band is
    the end price's."""
    hour_doc = hour_doc or load_hour_doc(lane=lane)
    ctx = {"symbol": "SPX", "horizon": f"the next {window['minutes']} minutes", "units": UNITS}
    ctx.update(context or {})
    ctx["average"] = average_line(window, lane)
    return {"id": "average", "state": {"context": ctx, "answers": sentences},
            "questions": {lane.average: jev_only(hour_doc["questions"][lane.average])}}


def views_of(probs: dict) -> dict:
    """The five-way odds read two more ways for grading: direction (up is up_small + up_big, down the
    same, flat) and size (big is up_big + down_big, small the rest). ``unsure`` keeps its own mass in
    both views, never spread over the others, so a view is never surer than JEV was."""
    p = {k: float(v) for k, v in probs.items() if isinstance(v, (int, float))}
    direction = {"up": p.get("up_small", 0.0) + p.get("up_big", 0.0), "flat": p.get("flat", 0.0),
                 "down": p.get("down_small", 0.0) + p.get("down_big", 0.0)}
    size = {"big": p.get("up_big", 0.0) + p.get("down_big", 0.0),
            "small": p.get("up_small", 0.0) + p.get("down_small", 0.0) + p.get("flat", 0.0)}
    if "unsure" in p:
        direction["unsure"] = size["unsure"] = p["unsure"]
    return {name: {"pick": max(v, key=v.get), "probabilities": {k: round(x, 4) for k, x in v.items()}}
            for name, v in (("direction", direction), ("size", size))}


def _one(a: dict | None, five_way: bool = False) -> dict | None:
    if not isinstance(a, dict):
        return None
    probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else None
    pick = a.get("choice") or (max(probs, key=probs.get) if probs else None)
    out = {"pick": pick, "probabilities": probs, "confidence": a.get("confidence")}
    if five_way and probs:
        out["views"] = views_of(probs)
    return out


def hour_summary(answer: dict | None, lane: Lane = LIVE) -> dict | None:
    """What the card and the hour record keep from JEV's reply to step 4: the primary sum flat on top
    (pick, probabilities, confidence), every sum under ``by`` keyed by question, and the model. A
    five-way sum (a RECORD horizon) also carries its direction and size ``views``."""
    if not isinstance(answer, dict):
        return None
    answers = answer.get("answers") or {}
    by = {qid: _one(answers.get(qid), five_way=band == RECORD)
          for qid, (_, band) in lane.horizons.items() if isinstance(answers.get(qid), dict)}
    if not by:
        return {"error": answer.get("error", "no answer")}
    prim = by.get(lane.primary)
    if prim is None:
        # the sum on the phone is missing: say so rather than lift the other sum into its place
        return {"error": f"no {lane.primary} answer", "primary": lane.primary, "by": by, "model": answer.get("model")}
    return {**prim, "primary": lane.primary, "by": by, "model": answer.get("model")}


def average_summary(answer: dict | None, window: dict, lane: Lane = LIVE) -> dict | None:
    """What the card and the hour record keep of JEV's reply to the average-price request, under ``average``: the
    sum's pick, probabilities and confidence flat on top and under ``by``, with ``primary`` naming it (so clock.blend
    takes it as it takes the hour summary), the box it forecasts (``box``), its window and the model; an ``error``
    in their place when it got no answer."""
    if not isinstance(answer, dict):
        return None
    one = _one((answer.get("answers") or {}).get(lane.average))
    head = {"primary": lane.average, "box": lane.primary, **window}
    if one is None:
        return {"error": answer.get("error", f"no {lane.average} answer"), **head}
    return {**one, **head, "by": {lane.average: one}, "model": answer.get("model")}
