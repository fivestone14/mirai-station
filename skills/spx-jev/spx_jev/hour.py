"""Steps 3 and 4 of the pipeline: the live answers become sentences, the sum questions sum them.

    answers (step 2)  ->  answer_sentences()  ->  hour_request()  ->  JEV  ->  one reply, one answer per sum

``next_30``, blended half and half with the time-of-day odds (clock.py), is the lane's ``primary``:
the end-price box the question weights and the learning loop learn from, kept beside the phone's
call (the average-price sum, below); ``next_60`` rides on the same sentences in the same request,
is blended the same way, and is graded beside it so the two horizons can be compared.

The question weights (weights.QuestionWeights, written by the grader) decide which answers are
worth a sentence: a question whose weight is under weights.MIN_WEIGHT is left out, with the reason
kept. The weights are neutral for now, so nothing is left out on their account.

Every sum's flat zone is sized for the read in index points (flat_zone.py) and filled into its text (PER_READ:
"within {zone_points} points"), with one context line naming every box's zone (zone_line), so JEV reads the number
the grader will use. A lane (lane.py) brings its own sums doc and horizons; the tape lane's one sum is five-way
(down_big, down_small, flat, up_small, up_big, unsure), its big line filled in beside its flat zone, its request
carrying the tape unit in points too (unit_line, for the stretch labels in units), and its summary is read three ways
for grading: raw, direction and size. Everything defaults to LIVE.

The average-price sum (the lane's ``average``) rides on the same sentences in a request of its own,
so the end-price sums' request is exactly what it was: where the average price over the primary's
window sits against the read, up, flat or down, with no unsure. Its context line says what the
average is and gives its flat zone in index points for the read (average_window: the edge the
average-price grade sets it against). Its summary (average_summary) rides on the hour summary
under ``average``: the phone's call.

From the cut-over (cuts.CUT_OVER_DAY) the live lane's sentences are no longer the live questions' answers, which are
retired that day (ask.retired): cut_over_sentences writes, in order, the judgment questions' answers given afresh on
the read (the one group kept, its shadow status no bar here) and the read's code-feature answers as plain sentences
("<catalog title>: <answer>", keyed ``code:<question id>``), so JEV sums the market state the code measured. The
request is shaped as before; its units line says what the lines are. A read with no sentence at all still asks the
sums, over an empty answers map. Before the cut-over nothing of this runs.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from .ask import fill_question, jev_only
from .judgment import GATES
from .lane import LIVE, RECORD, Lane
from .mirai_prediction.code_features import column_name, load_catalog
from .weights import MIN_WEIGHT, QuestionWeights

FIVE = ("down_big", "down_small", "flat", "up_small", "up_big")   # a RECORD horizon's outcomes, in order
# the names in a sum's text the code fills at each read: its window's minutes, its flat zone in points, a five-way sum's big line
PER_READ = ("window_minutes", "zone_points", "big_points")
AVERAGE_OUTCOMES = ("up", "flat", "down")
UNITS = ("sigma is today's expected move for the S&P 500 index. Each answer below was given by JEV about this moment, "
         "except those marked held, which were given at the time shown and carried forward unchanged")
# the units line from the cut-over: the lines are the code's measurements, and JEV's own judgments where it was asked one
CUT_OVER_UNITS = ("sigma is today's expected move for the S&P 500 index. Each line below is a measurement the code made about this "
                  "moment, named by what it measures, except those that say JEV was sure, which are judgments JEV gave about this moment")


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
    d["questions"] = {qid: fill_question(q, f"{Path(path).name} {qid}", PER_READ) for qid, q in qs.items()}
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


def code_sentence(question: dict, answer: str) -> str:
    """'<the catalog title>: <the answer>', one code-feature answer as JEV reads it."""
    return f"{question['title']}: {answer}"


def cut_over_sentences(doc: dict, fresh: dict[str, dict], code_answers: dict[str, str | None] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Step 3 from the cut-over. The judgment questions' answers given afresh on this read come first, each the sentence
    answer_sentences would write (one_sentence), then every code-feature answer the read has, in the catalog's order, as
    code_sentence under ``code:<question id>``. Returns the sentences and what was left out and why: a fresh answer to
    any other question (none is asked from the cut-over; one that is, is not an input), or a judgment answer with no
    pick. A code question the builder could not answer (None) is left out silently, as the matrix holds it."""
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    sentences: dict[str, str] = {}
    left_out: dict[str, str] = {}
    for qid, a in fresh.items():
        q = by_id.get(qid)
        if q is None or qid not in GATES:
            left_out[qid] = "not a judgment question: not an input from the cut-over"
            continue
        s = one_sentence(q, a)
        if s is None:
            left_out[qid] = "no pick in the answer"
            continue
        sentences[qid] = s
    for q in load_catalog():
        answer = (code_answers or {}).get(q["id"])
        if answer is not None:
            sentences[column_name(q["id"])] = code_sentence(q, str(answer))
    return sentences, left_out


def points(x: float) -> str:
    """A zone as the sums' text and context carry it, to the cent the record stamps."""
    return f"{float(x):.2f}"


def zone_line(zones: dict[str, float], lane: Lane = LIVE) -> str:
    """The context line naming every end-price box's flat zone in index points for this read: the same numbers the
    questions' text carries, the grader reads and the record stamps (flat_zone.py). A five-way box names its three bands."""
    if lane.graded_from_settled_open:
        return "flat is " + ", and ".join(f"within {points(zones[qid])} points of the settled open either way {m} minutes after it"
                                          for qid, (m, _) in lane.horizons.items())
    clauses = []
    for qid, (m, flat) in lane.horizons.items():
        if flat == RECORD:
            clauses.append(f"over the next {m} minutes flat is within {points(zones[qid])} points of the price now either way; small is "
                           f"{points(zones[qid])} to {points(zones['big'])} points; big is more than {points(zones['big'])} points")
        else:
            clauses.append(f"over the next {m} minutes flat is within {points(zones[qid])} points of the price now either way")
    return "; ".join(clauses)


def unit_line(ruler: dict) -> str:
    """The context line that prices the tape unit, which the stretch labels measure in."""
    rank = ruler.get("rank")      # the unit against the same minute on the prior sessions (labels.rulers.unit_rank), when there is one
    placed = f", in the {rank['band']} for this minute, wider than {rank['higher_than']} of {rank['of']} prior sessions" if rank else ""
    return f"one tape unit is {float(ruler['unit_points']):.1f} points{placed}"


def per_read(value, fills: dict):
    """A sum's text with its per-read names (PER_READ) filled from ``fills``."""
    if isinstance(value, str):
        for name, v in fills.items():
            value = value.replace("{" + name + "}", str(v))
        return value
    if isinstance(value, dict):
        return {k: per_read(v, fills) for k, v in value.items()}
    if isinstance(value, list):
        return [per_read(v, fills) for v in value]
    return value


def hour_request(sentences: dict[str, str], zones: dict[str, float], hour_doc: dict | None = None, context: dict | None = None,
                 lane: Lane = LIVE, ruler: dict | None = None) -> dict:
    """Step 4's request: the sentences are the state, the lane's end-price sums ride on top in one call (the
    average-price sum goes in its own, average_request), each with its flat zone for this read filled into its text
    (``zones``, flat_zone.Zones.at) and the context naming every box's (zone_line). With a ``ruler`` (a lane on the
    tape) the context also prices the unit."""
    hour_doc = hour_doc or load_hour_doc(lane=lane)
    ctx = {"symbol": "SPX", "horizon": ", and ".join(f"the next {m} minutes" for m, _ in lane.horizons.values()), "units": UNITS,
           "flat_zone": zone_line(zones, lane)}
    if ruler:
        ctx["unit"] = unit_line(ruler)
    ctx.update(context or {})
    questions = {}
    for qid, (minutes, flat) in lane.horizons.items():
        fills = {"window_minutes": minutes, "zone_points": points(zones[qid]), **({"big_points": points(zones["big"])} if flat == RECORD else {})}
        questions[qid] = per_read(jev_only(hour_doc["questions"][qid]), fills)
    return {"id": "hour", "state": {"context": ctx, "answers": sentences}, "questions": questions}


def average_window(minutes: int, flat_points: float, edge_points: float, price: float | None = None) -> dict:
    """The window the average-price sum forecasts: its minutes, the end-price box's flat zone in points, the edge the
    average is set against (the sum's own zone, flat_zone.py, to the cent JEV is told; the grade sets the average against
    this same edge, grade.integral_line), and the read's price when there is one."""
    out = {"minutes": minutes, "flat_points": round(flat_points, 2), "edge_points": round(edge_points, 2)}
    return {**out, "price": round(float(price), 2)} if isinstance(price, (int, float)) else out


def average_line(window: dict, lane: Lane = LIVE) -> str:
    """The context line of the average-price request: what the average over the window is, in one plain sentence,
    the read's price and the flat edge in points for this read, measured from the read's price or, on a lane graded
    from it, the settled open (not yet known at a read before the open, whose price is the futures' guide to it).
    The same request's answers can speak of the day's volume-weighted average, so the line says this average is the
    window's minute closes and not that one."""
    edge, price = points(window["edge_points"]), window.get("price")
    if lane.graded_from_settled_open:
        span, ref = f"the {window['minutes']} minutes after the settled open", "the settled open"
        where = (f"before the open the index stands near {price:.2f} on S&P futures; the settled open it is measured from is the close "
                 f"of the 09:34 bar, not known until then; " if price is not None else "")
    else:
        span, ref = f"the next {window['minutes']} minutes", "the price now"
        where = f"the price now is {price:.2f}; " if price is not None else ""
    return (f"{where}the average price over {span} is the average of those minutes' closing prices, not the day's volume-weighted "
            f"average price (VWAP); it counts every minute's closing price equally, so an early move counts for longer "
            f"than a late one; the average is flat when it sits within {edge} points of {ref} either way, up when it sits more than "
            f"{edge} points above it, down when it sits more than {edge} points below it")


def average_request(sentences: dict[str, str], window: dict, hour_doc: dict | None = None, context: dict | None = None,
                    lane: Lane = LIVE) -> dict:
    """The average-price sum's request: the same sentences as the end-price sums', its one question with the window's
    minutes and its flat zone filled in (per_read), and a context that names its window and gives the read's price and
    the flat edge (average_line). It carries no tape unit line."""
    hour_doc = hour_doc or load_hour_doc(lane=lane)
    ctx = {"symbol": "SPX", "horizon": f"the next {window['minutes']} minutes", "units": UNITS}
    ctx.update(context or {})
    ctx["average"] = average_line(window, lane)
    fills = {"window_minutes": window["minutes"], "zone_points": points(window["edge_points"])}
    return {"id": "average", "state": {"context": ctx, "answers": sentences},
            "questions": {lane.average: per_read(jev_only(hour_doc["questions"][lane.average]), fills)}}


def views_of(probs: dict) -> dict:
    """The five-way odds read two more ways for grading: direction (up is up_small + up_big, down the
    same, flat) and size (big is up_big + down_big, small the rest). ``unsure`` keeps its own mass in
    both views, never spread over the others, so a view is never surer than JEV was, and a view whose
    likeliest mass ties with unsure picks unsure: a tie is not a committed call."""
    p = {k: float(v) for k, v in probs.items() if isinstance(v, (int, float))}
    direction = {"up": p.get("up_small", 0.0) + p.get("up_big", 0.0), "flat": p.get("flat", 0.0),
                 "down": p.get("down_small", 0.0) + p.get("down_big", 0.0)}
    size = {"big": p.get("up_big", 0.0) + p.get("down_big", 0.0),
            "small": p.get("up_small", 0.0) + p.get("down_small", 0.0) + p.get("flat", 0.0)}
    if "unsure" in p:
        direction["unsure"] = size["unsure"] = p["unsure"]
    out = {}
    for name, v in (("direction", direction), ("size", size)):
        odds = {k: round(x, 4) for k, x in v.items()}
        top = max(odds.values())
        out[name] = {"pick": "unsure" if odds.get("unsure") == top else max(odds, key=odds.get), "probabilities": odds}
    return out


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
        # the primary end-price sum is missing: say so rather than lift the other sum into its place
        return {"error": f"no {lane.primary} answer", "primary": lane.primary, "by": by, "model": answer.get("model")}
    return {**prim, "primary": lane.primary, "by": by, "model": answer.get("model")}


def average_probabilities(p) -> dict[str, float] | None:
    """The average-price sum's odds as floats when they are odds at all: a dict over up, flat and down alone, each a
    finite number from 0 to 1, not all zero; None for anything else (text, a null, an option it does not have)."""
    if not isinstance(p, dict) or not p or not set(p) <= set(AVERAGE_OUTCOMES):
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0.0 <= v <= 1.0 for v in p.values()):
        return None
    return {k: float(v) for k, v in p.items()} if any(p.values()) else None


def _average_one(a) -> dict | None:
    """One JEV answer to the average-price sum, checked: its pick (JEV's choice when it is one of the odds' options,
    else the likeliest), its odds (average_probabilities) and its confidence; None when it is not a readable answer."""
    probs = average_probabilities(a.get("probabilities")) if isinstance(a, dict) else None
    if probs is None:
        return None
    choice = a.get("choice")
    conf = a.get("confidence")
    return {"pick": choice if choice in probs else max(probs, key=probs.get), "probabilities": probs,
            "confidence": float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None}


def average_summary(answer: dict | None, window: dict, lane: Lane = LIVE) -> dict | None:
    """What the card and the hour record keep of JEV's reply to the average-price request, under ``average``: the
    sum's pick, probabilities and confidence flat on top and under ``by``, with ``primary`` naming it (so clock.blend
    takes it as it takes the hour summary), the box it forecasts (``box``), its window and the model; an ``error``
    in their place when it got no answer or one that cannot be read (_average_one)."""
    head = {"primary": lane.average, "box": lane.primary, **window}
    if not isinstance(answer, dict):
        return {"error": "the average-price reply could not be read: it is not an object", **head}
    answers = answer.get("answers")
    if not isinstance(answers, dict) or answers.get(lane.average) is None:
        return {"error": str(answer.get("error") or f"no {lane.average} answer"), **head}
    one = _average_one(answers[lane.average])
    if one is None:
        return {"error": f"the {lane.average} answer could not be read: it has no odds over up, flat and down", **head}
    model = answer.get("model")
    return {**one, **head, "by": {lane.average: one}, "model": model if isinstance(model, str) else None}
