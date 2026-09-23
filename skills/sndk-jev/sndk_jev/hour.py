"""Steps 3 and 4 of the pipeline: the live answers become sentences, two questions sum them.

    answers (step 2)  ->  answer_sentences()  ->  hour_request()  ->  JEV  ->  one reply, two answers

``next_30`` is the sum the phone shows and the one the weights learn from; ``next_60`` rides on
the same sentences in the same request so the two horizons can be compared once graded.

The weights file written by ``grade.py`` (step 6) decides which answers are worth a sentence:
a question whose weight has fallen below ``MIN_WEIGHT`` is left out, with the reason kept.
"""
from __future__ import annotations

import json
from pathlib import Path

from .ask import jev_only

HOUR_QUESTIONS = Path(__file__).resolve().parent.parent / "questions" / "sndk_hour.json"
WEIGHTS_NAME = "weights.json"
MIN_WEIGHT = 0.5        # a question below this is left out of step 3
PRIMARY = "next_30"     # the sum on the phone, and the one the weights learn from
HOUR_QIDS = ("next_30", "next_60")


def load_hour_doc(path: Path | str = HOUR_QUESTIONS) -> dict:
    """The sums' doc for step 4. Flat: ``{"questions": {"next_30": {...}, "next_60": {...}}}``, no groups."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    qs = d.get("questions")
    if not isinstance(qs, dict) or any(q not in qs for q in HOUR_QIDS):
        raise ValueError(f"{path}: needs questions {HOUR_QIDS}")
    if d.get("primary", PRIMARY) != PRIMARY:
        raise ValueError(f"{path}: primary is {d.get('primary')!r} but the code sums for {PRIMARY!r}")
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


def hour_request(sentences: dict[str, str], hour_doc: dict | None = None, context: dict | None = None) -> dict:
    """Step 4's request: the sentences are the state, both sums ride on top in one call."""
    hour_doc = hour_doc or load_hour_doc()
    ctx = {"symbol": "SNDK", "horizon": "the next 30 minutes, and the next 60 minutes",
           "units": ("sigma is today's expected move. Each answer below was given by JEV about this moment, "
                     "except those marked held, which were given at the time shown and carried forward unchanged")}
    ctx.update(context or {})
    return {"id": "hour", "state": {"context": ctx, "answers": sentences},
            "questions": {qid: jev_only(q) for qid, q in hour_doc["questions"].items()}}


def _one(a: dict | None) -> dict | None:
    if not isinstance(a, dict):
        return None
    probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else None
    pick = a.get("choice") or (max(probs, key=probs.get) if probs else None)
    return {"pick": pick, "probabilities": probs, "confidence": a.get("confidence")}


def hour_summary(answer: dict | None) -> dict | None:
    """What the card and the hour record keep from JEV's reply to step 4: the primary sum flat on top
    (pick, probabilities, confidence), every sum under ``by`` keyed by question, and the model."""
    if not isinstance(answer, dict):
        return None
    answers = answer.get("answers") or {}
    by = {qid: _one(answers.get(qid)) for qid in HOUR_QIDS if isinstance(answers.get(qid), dict)}
    if not by:
        return {"error": answer.get("error", "no answer")}
    prim = by.get(PRIMARY)
    if prim is None:
        # the sum on the phone is missing: say so rather than lift the other sum into its place
        return {"error": f"no {PRIMARY} answer", "primary": PRIMARY, "by": by, "model": answer.get("model")}
    return {**prim, "primary": PRIMARY, "by": by, "model": answer.get("model")}
