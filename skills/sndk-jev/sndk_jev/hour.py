"""Steps 3 and 4 of the pipeline: the live answers become sentences, two questions sum them.

    answers (step 2)  ->  answer_sentences()  ->  hour_request()  ->  JEV  ->  one reply, two answers

``next_30``, blended half and half with the time-of-day odds (clock.py), is the sum the phone
shows and the one the weights learn from; ``next_60`` rides on the same sentences in the same
request, is blended the same way, and is graded beside it so the two horizons can be compared.

The weights file written by ``grade.py`` (step 6) decides which answers are worth a sentence:
a question whose weight has fallen below ``MIN_WEIGHT`` is left out, with the reason kept.

A lane (lane.py) brings its own sums doc and horizons; the tape lane's one sum is five-way
(down_big, down_small, flat, up_small, up_big, unsure) in tape units, so its request carries a
context line that prices the unit and the bands for the read, and its summary is read three ways
for grading: raw, direction and size. Everything defaults to LIVE.
"""
from __future__ import annotations

import json
from pathlib import Path

from .ask import jev_only
from .lane import LIVE, RECORD, Lane

HOUR_QUESTIONS = LIVE.hour_doc
WEIGHTS_NAME = "weights.json"
MIN_WEIGHT = 0.5        # a question below this is left out of step 3
PRIMARY = LIVE.primary  # the sum on the phone (as blended by clock.py), and the one the weights learn from
HOUR_QIDS = tuple(LIVE.horizons)
FIVE = ("down_big", "down_small", "flat", "up_small", "up_big")   # a RECORD horizon's outcomes, in order


def load_hour_doc(path: Path | str | None = None, lane: Lane = LIVE) -> dict:
    """The sums' doc for step 4. Flat: ``{"questions": {"next_30": {...}, "next_60": {...}}}``, no groups."""
    path = path or lane.hour_doc
    qids = tuple(lane.horizons)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    qs = d.get("questions")
    if not isinstance(qs, dict) or any(q not in qs for q in qids):
        raise ValueError(f"{path}: needs questions {qids}")
    if d.get("primary", lane.primary) != lane.primary:
        raise ValueError(f"{path}: primary is {d.get('primary')!r} but the code sums for {lane.primary!r}")
    return d


def load_weights(out_dir: Path) -> dict:
    """``{qid: {"weight": float, ...}}`` from state/jev/weights.json; missing file means every weight is 1.0."""
    p = Path(out_dir) / WEIGHTS_NAME
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return d.get("questions", {}) if isinstance(d, dict) else {}


def weight_of(weights: dict, qid: str) -> float:
    w = (weights.get(qid) or {}).get("weight", 1.0)
    return float(w) if isinstance(w, (int, float)) else 1.0


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


def one_sentence(q: dict, answer: dict) -> str | None:
    """'<the ask> <the option, in words>, JEV was 98% sure'. Score answers name their level's words."""
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
    held = f" (held since {answer['held_from']}, not re-asked)" if answer.get("held_from") else ""
    return f"{ask} {words}{tail}{held}"


def answer_sentences(doc: dict, answered: dict[str, dict], weights: dict | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Step 3. ``answered`` is ``{qid: answer}`` as the card stores them (pick, probabilities, noul, ...).
    Returns the sentences to send and, separately, what was left out and why."""
    weights = weights or {}
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
        w = weight_of(weights, qid)
        if w < MIN_WEIGHT:
            left_out[qid] = f"weight {w:.2f} is below the {MIN_WEIGHT} cut after grading"
            continue
        s = one_sentence(q, a)
        if s is None:
            left_out[qid] = "no pick in the answer"
            continue
        sentences[qid] = s
    return sentences, left_out


def band_of(ruler: dict, hour_doc: dict, qid: str) -> dict:
    """A RECORD horizon's bands for this read, in dollars: the doc's fractions of a tape unit times the
    unit measured on the tape (state_builder.ruler). Stored on the hour record, so the grader reads
    the same bands JEV was told."""
    h = hour_doc["horizons"][qid]
    flat_u, big_u = float(h["flat_band_units"]), float(h["big_band_units"])
    unit = float(ruler["unit_dollars"])
    return {"flat_dollars": round(flat_u * unit, 2), "big_dollars": round(big_u * unit, 2),
            "flat_units": flat_u, "big_units": big_u}


def unit_line(ruler: dict, band: dict) -> str:
    """The context line that turns the unit into prices, since JEV cannot multiply a unit by a fraction."""
    unit, flat, big = (round(float(x)) for x in (ruler["unit_dollars"], band["flat_dollars"], band["big_dollars"]))
    rank = ruler.get("rank")      # the unit against the same minute on the prior sessions (state_builder.unit_rank), when there is one
    placed = f", in the {rank['band']} for this minute, wider than {rank['higher_than']} of {rank['of']} prior sessions" if rank else ""
    return (f"one tape unit is ${unit}{placed}; flat is within ${flat} either way ({band['flat_units']:g} of a unit); "
            f"small is ${flat} to ${big}; big is more than ${big} ({band['big_units']:g} of a unit)")


def hour_request(sentences: dict[str, str], hour_doc: dict | None = None, context: dict | None = None,
                 lane: Lane = LIVE, ruler: dict | None = None) -> dict:
    """Step 4's request: the sentences are the state, the lane's sums ride on top in one call. With a
    ``ruler`` (a lane on the tape) the context also prices the unit and the bands for this read."""
    hour_doc = hour_doc or load_hour_doc(lane=lane)
    ctx = {"symbol": "SNDK", "horizon": ", and ".join(f"the next {m} minutes" for m, _ in lane.horizons.values()),
           "units": ("sigma is today's expected move. Each answer below was given by JEV about this moment, "
                     "except those marked held, which were given at the time shown and carried forward unchanged")}
    if ruler:
        ctx["unit"] = unit_line(ruler, band_of(ruler, hour_doc, lane.primary))
    ctx.update(context or {})
    return {"id": "hour", "state": {"context": ctx, "answers": sentences},
            "questions": {qid: jev_only(q) for qid, q in hour_doc["questions"].items()}}


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
