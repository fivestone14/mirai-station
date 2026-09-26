"""Step 6: the bars grade the sums, and the grades move each question's weight.

    python3 -m sndk_jev.grade                # grade every ungraded record, rewrite weights.json
    python3 -m sndk_jev.grade --day 2026-09-22
    python3 -m sndk_jev.grade --lane tape    # the opening lane, under state/jev/lanes/tape/

Two horizons are graded from the same record, each against its own band:
    next_30   30 minutes, flat within 0.12 sigma   (the primary: the phone's sum, the weights' teacher)
    next_60   60 minutes, flat within 0.17 sigma   (graded beside it, for the comparison)

How a horizon is graded
    realized = (close h minutes after the row - spot at the row) / sigma
    band     = up if realized > flat band, down if realized < -flat band, else flat
    hit      = the shown sum's pick (the blend) == band; jev_hit the same for JEV's own pick
    brier    = sum over {up, flat, down} of (p - 1[band]) ** 2      (0 is perfect, 2 is worst)
    Each horizon is graded as soon as its own mark has a bar: the 30-minute sum at 30 minutes, the
    60-minute sum at 60, so the primary never waits for the slower one. A record therefore gets up
    to one line per horizon in grades.jsonl (one line when both marks are already in). A horizon
    whose end falls up to CLOSE_GRACE_MIN past the close is graded at the closing bar; one that
    ends later is skipped for good and said so in the line. A record none of whose horizons can
    ever be graded is written as not graded, so it is never retried. A horizon graded once is
    never graded twice.

What is graded
    The sum a record carries is the one the phone showed: JEV's sum blended with the time-of-day
    odds (see clock.py). When the record also carries JEV's own sum and the clock's odds, each
    is scored beside it on the same outcome (``jev_brier``, ``jev_pick``, ``jev_hit``,
    ``clock_brier``), so the blend keeps having to earn its place against both of its parts.

Scheduled events and finished days
    A read carrying a tier-1 event due within 30 minutes (events.py) is graded like any other, and
    its line says so (``event_within_30``), but it never pairs into a question's weight: a Fed minute
    must not teach a question what an ordinary half hour looks like. On a past day whose bars reach
    the close, a mark whose bars are missing (a halt, an outage) is closed out as a halted window
    instead of waiting forever for bars no run will ever add; today, a hole still waits.

How a weight moves
    For each LIVE step-2 question, every graded record where the question was answered AFRESH, with
    a pick from one of its current options, gives one pair (what it picked, what the primary horizon did); a held answer never pairs, since it
    was given before the outcome it would be judged on. Its weight is the mutual information
    between those two, divided by the largest MI among the questions that have MIN_GRADED pairs
    of their own, so the most telling question has weight 1.0. A question with fewer than
    MIN_GRADED pairs keeps 1.0: no evidence, no exclusion. Under hour.MIN_WEIGHT the question's
    sentence is left out of step 3, but it is still asked every read, so it keeps earning pairs
    and can climb back.

A lane (lane.py) grades by its own settings; LIVE is everything above, unchanged. The tape lane's
one horizon is banded from the record itself: the tape unit measured at the read prices a flat and
a big band in dollars, and the realized move lands in one of five bands (down_big, down_small,
flat, up_small, up_big), read also as a direction and a size (big or small), each scored against
the matching view of the sum. Its mark needs the exact bar (bar_gap_min 0), a finished day's bars
that stop close its records out, a question pairs only with reads pair_gap_min apart, and a ready
question's information must beat the 99th percentile of ``shuffles`` day-block shuffles of the
outcomes (whole days moved as blocks, seed fixed) or its weight is 0; nothing is cut from step 3
until CUT_AFTER_PASSED questions have passed.

Outputs, all under state/jev/
    grades.jsonl       one line per graded horizon of a record (append only, keyed by row_ts and
                       the ``horizons`` the line carries; the primary's line has its fields flat on top)
    weights.json       {"graded_runs": reads whose primary mark is graded, "min_graded", "min_weight", "primary",
                        "sums": {qid: {"n", "hit_rate", "always_flat_hit_rate", "mean_brier", "bands",
                                       "blended": {"n", "mean_brier_blend", "mean_brier_jev", "mean_brier_clock", "jev_hit_rate"},
                                       "event_reads": {"n", "mean_brier"}}},
                        "questions": {qid: {"weight", "mi", "n", "in_step_3", "why"}},
                        "new_this_run", "new_by_horizon", "closed_out"}
    weights_log.jsonl  one line per grading run that graded something: the tally and every weight that moved
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .hour import FIVE, HOUR_QUESTIONS, MIN_WEIGHT, PRIMARY, WEIGHTS_NAME  # noqa: F401  (HOUR_QUESTIONS, PRIMARY: kept for callers)
from .lane import LANES, LIVE, RECORD, Lane, horizons  # noqa: F401  (horizons: the doc reader, kept for callers)
from .state_builder import DEFAULT_STATE_DIR, close_at, load_bars, load_jsonl, parse_ts, session_close

ET = ZoneInfo("America/New_York")

MIN_GRADED = LIVE.min_graded        # fresh pairs a question needs before its own weight can move away from 1.0
CLOSE_GRACE_MIN = 2                 # a horizon ending this far past the close is graded at the closing bar
BAR_GAP_MAX_MIN = LIVE.bar_gap_min  # the bar standing for a mark may be at most this many minutes before it
BANDS = ("up", "flat", "down")      # "unsure" is a pick, never an outcome: its probability counts against the Brier
SIZES = ("big", "small")
SHUFFLE_SEED = 20260925             # the day the lane was planned; fixed, so a rerun scores the same
CUT_AFTER_PASSED = 3                # a shuffling lane cuts no question from step 3 until this many have passed
WEIGHTS_LOG = "weights_log.jsonl"


def _bar_before(bars: list[dict], t: datetime) -> dict:
    """The last bar that had finished by ``t`` (the caller has checked one exists)."""
    one = timedelta(minutes=1)
    return [b for b in bars if parse_ts(b["ts"]) + one <= t][-1]


HORIZONS = LIVE.horizons


def realized_band(x: float, flat: float) -> str:
    return "up" if x > flat else "down" if x < -flat else "flat"


def realized_bands(x: float, flat: float, big: float) -> tuple[str, str, str]:
    """A move in dollars against a record's bands: the five-way band, its direction and its size."""
    band = ("up_big" if x > big else "up_small" if x > flat else
            "down_big" if x < -big else "down_small" if x < -flat else "flat")
    return band, band.split("_")[0], "big" if band.endswith("big") else "small"


def _parts(rec: dict) -> dict:
    """``{qid: {"jev": probabilities, "jev_pick": pick, "clock": probabilities}}`` for sums that were
    blended; an unblended record has none."""
    by = rec.get("by") if isinstance(rec.get("by"), dict) else {}
    out = {}
    for q, v in by.items():
        if not isinstance(v, dict):
            continue
        j, c = v.get("jev"), v.get("clock")
        part = {}
        if isinstance(j, dict) and isinstance(j.get("probabilities"), dict):
            part["jev"], part["jev_pick"] = j["probabilities"], j.get("pick")
        if isinstance(c, dict) and isinstance(c.get("probabilities"), dict):
            part["clock"] = c["probabilities"]
        if part:
            out[q] = part
    return out


def _brier(p: dict, band: str, bands: tuple[str, ...] = BANDS) -> float:
    return round(sum((float(p.get(b, 0.0)) - (1.0 if b == band else 0.0)) ** 2 for b in bands), 4)


def _picks(rec: dict) -> tuple[dict, dict]:
    """Picks and probabilities per sum, from the record's ``by`` block. A record without one is
    from before the two-sum switch (a 60-minute forecast against a 0.24 band): it is never graded,
    because grading it as a 30-minute forecast would teach the weights from the wrong question."""
    by = rec.get("by") if isinstance(rec.get("by"), dict) else None
    if not by:
        return {}, {}
    return ({q: v.get("pick") for q, v in by.items() if isinstance(v, dict)},
            {q: (v.get("probabilities") or {}) for q, v in by.items() if isinstance(v, dict)})


def _has_band(rec: dict) -> bool:
    b = rec.get("band")
    return isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in ("flat_dollars", "big_dollars"))


def _grade_units(rec: dict, qid: str, realized: float, pick, p: dict) -> dict:
    """A RECORD horizon: the move in dollars against the bands stored on the record, and the sum's
    direction and size views (hour.views_of) each scored against the outcome they forecast."""
    band, direction, size = realized_bands(realized, float(rec["band"]["flat_dollars"]), float(rec["band"]["big_dollars"]))
    unit = (rec.get("ruler") or {}).get("unit_dollars")
    out = {"realized_dollars": round(realized, 2), "realized_units": round(realized / float(unit), 3) if isinstance(unit, (int, float)) and unit else None,
           "band": band, "direction": direction, "size": size, "pick": pick, "hit": pick == band,
           "brier": _brier(p, band, FIVE), "p_band": p.get(band)}
    views = ((rec.get("by") or {}).get(qid) or {}).get("views") or {}
    for view, outcome, bands in (("direction", direction, BANDS), ("size", size, SIZES)):
        v = views.get(view) or {}
        if isinstance(v.get("probabilities"), dict):
            out.update({f"{view}_pick": v.get("pick"), f"{view}_hit": v.get("pick") == outcome,
                        f"{view}_brier": _brier(v["probabilities"], outcome, bands)})
    return out


# the primary's fields lifted flat on top of its line, in this order; a line carries only those its horizon has
TOP_KEYS = ("realized_sigma", "realized_dollars", "realized_units", "band", "direction", "size", "pick", "hit", "brier", "p_band",
            "jev_pick", "jev_hit", "jev_brier", "clock_brier",
            "direction_pick", "direction_hit", "direction_brier", "size_pick", "size_hit", "size_brier")


def grade_one(rec: dict, bars: list[dict], done: set[str] | frozenset[str] = frozenset(), final: bool = False,
              lane: Lane = LIVE) -> dict | None:
    """One record against the bars of its day: every horizon not in ``done`` whose mark has a bar.

    Returns the line to append, None when nothing new can be graded yet (a mark still ahead, or
    bars missing), or a ``graded: False`` line when no horizon of the record can ever be graded.
    The line names the horizons it carries, the ones still ``pending`` and the ones ``skipped``
    for good, so the next run grades only what is left."""
    t0 = parse_ts(rec["row_ts"]).replace(second=0, microsecond=0)   # the read's minute: a 15:32:00.4 read is a 15:32 read
    close = session_close(t0)
    spot, sigma = float(rec["spot"]), float(rec["sigma"])
    if sigma <= 0:
        return {"row_ts": rec["row_ts"], "graded": False, "reason": f"sigma {sigma} cannot scale a move"}
    if not bars:
        return None
    picks, probs = _picks(rec)
    if not picks:
        return None
    parts = _parts(rec)
    last_done = parse_ts(bars[-1]["ts"]) + timedelta(minutes=1)
    # a hole is closed out only when it can never be filled: the day is over (``final``, from the
    # calendar) and its bars reach the close, so the gap is a halt or an outage, not bars still coming.
    # A lane on the bar clock reads its day off the bars: when a finished day's bars stop, so did
    # its reads, and a mark past the last bar will never come
    final = final and (lane.bar_clock or last_done >= close - timedelta(minutes=CLOSE_GRACE_MIN))
    graded, pending, skipped = {}, [], {}
    for qid, (h, flat) in lane.horizons.items():
        if qid in done:
            continue
        if picks.get(qid) is None:
            skipped[qid] = "no answer for this sum"    # JEV did not answer it on this read
            continue
        if flat == RECORD and not _has_band(rec):
            skipped[qid] = "no band on the record"     # no tape unit was measured at the read: nothing to grade against
            continue
        t1 = t0 + timedelta(minutes=h)
        if t1 > close + timedelta(minutes=CLOSE_GRACE_MIN):
            skipped[qid] = "ends past the close"      # can never be graded
            continue
        t1 = min(t1, close)                           # inside the grace: the closing bar stands for the mark
        c1 = close_at(bars, t1)
        if c1 is None or last_done < t1:
            if final:
                skipped[qid] = "halted window: no bar at the mark on a finished day"   # it will never come
            else:
                pending.append(qid)                   # its mark is still ahead; a later run grades it
            continue
        if last_done < t1 - timedelta(minutes=lane.bar_gap_min) or parse_ts(_bar_before(bars, t1)["ts"]) < t1 - timedelta(minutes=lane.bar_gap_min + 1):
            if final:
                skipped[qid] = "halted window: bars missing around the mark on a finished day"
            else:
                pending.append(qid)                   # a hole in the bars around the mark: wait for them
            continue
        p = probs.get(qid) or {}
        if flat == RECORD:
            graded[qid] = _grade_units(rec, qid, c1 - spot, picks.get(qid), p)
            continue
        realized = (c1 - spot) / sigma
        band = realized_band(realized, flat)
        brier = sum((float(p.get(b, 0.0)) - (1.0 if b == band else 0.0)) ** 2 for b in BANDS)
        graded[qid] = {"realized_sigma": round(realized, 3), "band": band, "pick": picks.get(qid),
                       "hit": picks.get(qid) == band, "brier": round(brier, 4), "p_band": p.get(band)}
        part = parts.get(qid) or {}
        if "jev" in part:
            graded[qid].update({"jev_pick": part.get("jev_pick"), "jev_hit": part.get("jev_pick") == band,
                                "jev_brier": _brier(part["jev"], band)})
        if "clock" in part:
            graded[qid]["clock_brier"] = _brier(part["clock"], band)
    if not graded:
        if pending:
            return None                               # a mark is still ahead
        closed = {q: v for q, v in skipped.items() if str(v).startswith(("halted window", "no band"))}
        if closed:
            # closed out for good; when the other horizon was graded on an earlier run this line carries
            # only the halted one, so the read is never retried
            return {"row_ts": rec["row_ts"], "horizons": [], "pending": [], "skipped": skipped,
                    "reason": "; ".join(f"{q}: {v}" for q, v in closed.items())}
        if done:
            return None                               # nothing left to do
        return {"row_ts": rec["row_ts"], "graded": False, "reason": "every horizon ends past the close"}
    out = {"row_ts": rec["row_ts"], "used": rec.get("used", {}), "fresh": rec.get("fresh", rec.get("used", {})),
           "horizons": list(graded), "pending": pending, "skipped": skipped, **graded}
    ev = rec.get("event") if isinstance(rec.get("event"), dict) else None
    if ev:
        out["event_within_30"] = bool(ev.get("within_30"))
        out["event"] = ev.get("sentence")
    if lane.primary in graded:
        for k in TOP_KEYS:
            if k in graded[lane.primary]:
                out[k] = graded[lane.primary][k]      # the primary, flat on top, is what the weights read
    return out


def graded_horizons(lines: list[dict], lane: Lane = LIVE) -> dict[str, set[str]]:
    """``{row_ts: horizons already graded or skipped for good}`` from the grades file. A line from
    before horizons were graded separately carried every horizon that could ever be graded, so it
    counts as complete; so does a closed-out line."""
    done: dict[str, set[str]] = defaultdict(set)
    for g in lines:
        if g.get("graded") is False or "horizons" not in g:
            done[g["row_ts"]] |= set(lane.horizons)
        else:
            done[g["row_ts"]] |= set(g.get("horizons") or []) | set(g.get("skipped") or {})
    return done


def mutual_information(pairs: list[tuple[str, str]]) -> float:
    n = len(pairs)
    if n == 0:
        return 0.0
    ca, cb, cab = Counter(a for a, _ in pairs), Counter(b for _, b in pairs), Counter(pairs)
    return sum(v / n * math.log((v / n) / ((ca[a] / n) * (cb[b] / n))) for (a, b), v in cab.items())


def shuffle_p99(pairs: list[tuple[str, str]], days: list[str], shuffles: int, seed: int = SHUFFLE_SEED) -> float:
    """The 99th percentile of the mutual information over ``shuffles`` day-block shuffles: the picks
    stay put, the outcomes move between days as whole blocks (each day's run of outcomes kept in
    order, so the tape's within-day clustering survives the shuffle), and the MI is measured again.
    A question's real MI has to clear this to count as information rather than luck."""
    picks = [p for p, _ in pairs]
    blocks: dict[str, list[str]] = defaultdict(list)
    for (_, band), day in zip(pairs, days):
        blocks[day].append(band)
    order = list(blocks.values())
    rng = random.Random(seed)
    mis = []
    for _ in range(shuffles):
        rng.shuffle(order)
        mis.append(mutual_information(list(zip(picks, [b for block in order for b in block]))))
    mis.sort()
    return mis[math.ceil(0.99 * len(mis)) - 1]


def _tally(rows: list[dict]) -> dict:
    """A sum's record: the shown sum's hit rate and Brier, and, over the reads that were blended,
    the same Brier for the blend, for JEV's own sum and for the clock alone, side by side."""
    n = len(rows)
    if not n:
        return {"n": 0, "hit_rate": None, "always_flat_hit_rate": None, "mean_brier": None, "bands": {}}
    out = {"n": n, "hit_rate": round(sum(1 for g in rows if g["hit"]) / n, 3),
           "always_flat_hit_rate": round(sum(1 for g in rows if g["band"] == "flat") / n, 3),
           "mean_brier": round(sum(g["brier"] for g in rows) / n, 4),
           "bands": dict(Counter(g["band"] for g in rows))}
    return _with_views(rows, _with_parts(rows, out))


def _with_parts(rows: list[dict], out: dict) -> dict:
    blended = [g for g in rows if "jev_brier" in g and "clock_brier" in g]
    if blended:
        k = len(blended)
        out["blended"] = {"n": k, "mean_brier_blend": round(sum(g["brier"] for g in blended) / k, 4),
                          "mean_brier_jev": round(sum(g["jev_brier"] for g in blended) / k, 4),
                          "mean_brier_clock": round(sum(g["clock_brier"] for g in blended) / k, 4),
                          "jev_hit_rate": round(sum(1 for g in blended if g.get("jev_hit")) / k, 3)}
    return out


def _with_views(rows: list[dict], out: dict) -> dict:
    """A five-way sum's direction and size views, each beside the constant forecast of its own largest
    class: the bar the plan set for the lane."""
    for view in ("direction", "size"):
        scored = [g for g in rows if f"{view}_brier" in g]
        if not scored:
            continue
        k = len(scored)
        outcomes = Counter(g[view] for g in scored)
        out.setdefault("views", {})[view] = {
            "n": k, "hit_rate": round(sum(1 for g in scored if g[f"{view}_hit"]) / k, 3),
            "largest_class_hit_rate": round(max(outcomes.values()) / k, 3),
            "mean_brier": round(sum(g[f"{view}_brier"] for g in scored) / k, 4), "outcomes": dict(outcomes)}
    return out


def _events(grades: list[dict], qid: str) -> dict:
    """The reads a scheduled event sat inside, tallied apart so their share of the record stays visible."""
    rows = [g[qid] for g in grades if g.get("event_within_30") and isinstance(g.get(qid), dict)]
    return {"n": len(rows), "mean_brier": round(sum(r["brier"] for r in rows) / len(rows), 4) if rows else None}


def live_options(doc: dict | Path | str | None = None) -> dict[str, set[str]]:
    """``{qid: the picks it can give today}`` for every live question of ``doc`` (the question doc the
    service runs with, or a path to one; the shipped doc by default): a retired question, or a pick
    from an option a question no longer has, must not pair into today's weights."""
    from .ask import DEFAULT_QUESTIONS, load_questions
    if not isinstance(doc, dict):
        doc = load_questions(doc or DEFAULT_QUESTIONS)
    out = {}
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            if q.get("status") != "live":
                continue
            crit = q.get("criteria")
            out[qid] = {"true", "false"} if q.get("type") == "noul" else set(crit) if isinstance(crit, dict) else set()
    return out


def _pairs(grades: list[dict], allowed: dict[str, set[str]] | None, lane: Lane) -> tuple[dict, dict]:
    """``{qid: [(pick, band)]}`` and ``{qid: [day per pair]}`` over the primary horizon's grades. With
    a pair gap, a question pairs only with reads at least that far after its last paired read, so
    reads whose marks overlap never count as two pieces of evidence."""
    pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    days: dict[str, list[str]] = defaultdict(list)
    last_paired: dict[str, datetime] = {}
    reads = sorted(grades, key=lambda g: parse_ts(g["row_ts"])) if lane.pair_gap_min else grades
    for g in reads:
        if g.get("event_within_30"):
            continue                                  # a scheduled event inside the horizon: graded, never paired
        # only answers given afresh on that read pair with its outcome; a held answer predates it.
        # A read on which every answer was held has an empty fresh block and pairs nothing; only
        # a line written before the block existed falls back to everything it used.
        fresh = g["fresh"] if isinstance(g.get("fresh"), dict) else (g.get("used") or {})
        for qid, pick in fresh.items():
            if allowed is not None and (qid not in allowed or str(pick) not in allowed[qid]):
                continue                              # retired, or an option the question no longer has
            if lane.pair_gap_min:
                t = parse_ts(g["row_ts"])
                if qid in last_paired and t - last_paired[qid] < timedelta(minutes=lane.pair_gap_min):
                    continue
                last_paired[qid] = t
            pairs[qid].append((str(pick), g["band"]))
            days[qid].append(str(g["row_ts"])[:10])
    return pairs, days


def weights_from(grades: list[dict], allowed: dict[str, set[str]] | None = None, lane: Lane = LIVE) -> dict:
    """Every step-2 question's weight from the primary horizon's grades, plus each sum's own tally.
    With ``allowed`` (``live_options()``), only live questions pair, and only with picks from their
    current options."""
    primary = [g for g in grades if g.get("band")]
    pairs, days = _pairs(primary, allowed, lane)
    n_runs = len(primary)
    mi = {qid: mutual_information(p) for qid, p in pairs.items()}
    ready = {qid for qid, p in pairs.items() if len(p) >= lane.min_graded}
    # a shuffling lane: a ready question counts only once its MI beats the shuffled outcomes' 99th percentile
    p99 = {qid: shuffle_p99(pairs[qid], days[qid], lane.shuffles) for qid in ready} if lane.shuffles else {}
    passed = {qid for qid in ready if not lane.shuffles or mi[qid] > p99[qid]}
    top = max((mi[q] for q in passed), default=0.0)
    questions = {}
    for qid, p in pairs.items():
        if qid not in ready:
            w, why = 1.0, f"{len(p)} fresh pairs, under the {lane.min_graded} needed; keeps weight 1.0"
        elif qid not in passed:
            w = 0.0
            why = (f"mutual information {mi[qid]:.4f} over {len(p)} fresh pairs is not above the 99th percentile "
                   f"({p99[qid]:.4f}) of {lane.shuffles} day-block shuffles of the outcomes: luck, not information")
        elif top <= 0:
            w, why = 1.0, "no question's picks carry information about the outcome yet; keeps weight 1.0"
        else:
            w = mi[qid] / top
            why = f"mutual information with the {lane.primary} outcome over {len(p)} fresh pairs, relative to the best question"
        entry = {"weight": round(w, 3), "mi": round(mi[qid], 4), "n": len(p), "why": why, "in_step_3": w >= MIN_WEIGHT}
        if lane.shuffles:
            entry["shuffle_p99"] = round(p99[qid], 4) if qid in p99 else None
            if not entry["in_step_3"] and len(passed) < CUT_AFTER_PASSED:
                # too early to cut anyone: until three questions have proven themselves, the best of them may be the lucky one
                entry["in_step_3"] = True
                entry["why"] += f"; kept in step 3 until {CUT_AFTER_PASSED} questions have passed the shuffle"
        questions[qid] = entry
    sums = {qid: {**_tally([g[qid] for g in grades if isinstance(g.get(qid), dict)]), "event_reads": _events(grades, qid)}
            for qid in lane.horizons}
    out = {"graded_runs": n_runs, "min_graded": lane.min_graded, "min_weight": MIN_WEIGHT, "primary": lane.primary,
           "sums": sums, "questions": questions}
    if lane.tag:
        out.update({"lane": lane.tag, "pair_gap_min": lane.pair_gap_min, "shuffles": lane.shuffles, "passed": len(passed)})
    return out


_read_jsonl = load_jsonl    # one reader for every jsonl on disk: a torn or non-object line is dropped


def log_weights(path: Path, before: dict, weights: dict, new: int) -> dict:
    """One line per grading run that graded something: the tallies, and every weight that moved."""
    changes = []
    for qid, w in sorted(weights["questions"].items()):
        old = before.get(qid, {}).get("weight")
        if old is None or abs(float(old) - float(w["weight"])) >= 0.0005:
            changes.append({"question": qid, "before": old, "after": w["weight"], "mi": w["mi"], "n": w["n"],
                            "in_step_3": w["in_step_3"]})
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "graded_runs": weights["graded_runs"],
            "new": new, **weights["sums"][weights["primary"]], "sums": weights["sums"], "changes": changes}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def run(state_dir: Path, out_dir: Path, day: str | None = None, allowed: dict[str, set[str]] | None = None,
        lane: Lane = LIVE) -> dict:
    """Grade every mark that has passed and rewrite the weights. ``allowed`` (``live_options()``) keeps
    retired questions and dropped options out of the weights; the service and the command pass it."""
    hour_dir = out_dir / "hour"
    grades_path = out_dir / "grades.jsonl"
    done = graded_horizons(_read_jsonl(grades_path), lane)
    every = set(lane.horizons)
    new = []
    days = [day] if day else sorted(p.stem for p in hour_dir.glob("*.jsonl")) if hour_dir.exists() else []
    for d in days:
        recs = [r for r in _read_jsonl(hour_dir / f"{d}.jsonl") if done.get(r.get("row_ts"), set()) != every and isinstance(r.get("by"), dict)]
        if not recs:
            continue
        bars = load_bars(state_dir, d)
        if not bars and d < datetime.now(ET).date().isoformat():
            # a past day with no bars can never be graded: close its reads out rather than retry forever
            for r in recs:
                if done.get(r["row_ts"], set()) != every:
                    new.append({"row_ts": r["row_ts"], "graded": False, "reason": "no bars for the day"})
                    done[r["row_ts"]] |= every
            continue
        for r in recs:
            if done.get(r["row_ts"], set()) == every:
                continue                              # the same row written twice (a run by hand): graded once
            g = grade_one(r, bars, done.get(r["row_ts"], set()), final=d < datetime.now(ET).date().isoformat(), lane=lane)
            if g:
                new.append(g)
                done[g["row_ts"]] |= every if g.get("graded") is False else set(g["horizons"]) | set(g["skipped"])
    if new:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(grades_path, "a", encoding="utf-8") as f:
            for g in new:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
    grades = _read_jsonl(grades_path)
    weights = weights_from(grades, allowed, lane)
    weights["new_this_run"] = sum(1 for g in new if g.get("band"))
    weights["new_by_horizon"] = {q: sum(1 for g in new if q in (g.get("horizons") or [])) for q in lane.horizons}
    weights["closed_out"] = sum(1 for g in new if g.get("graded") is False)
    weights_path = out_dir / WEIGHTS_NAME
    before = {}
    if weights_path.is_file():
        try:
            before = (json.loads(weights_path.read_text(encoding="utf-8")) or {}).get("questions", {})
        except json.JSONDecodeError:
            before = {}
    if new:
        out_dir.mkdir(parents=True, exist_ok=True)
        log_weights(out_dir / WEIGHTS_LOG, before, weights, sum(1 for g in new if g.get("band")))
    tmp = weights_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(weights, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, weights_path)                    # the page builder and step 3 never see a half file
    return weights


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grade the sums against the bars and move the question weights.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default the lane's folder under <state-dir>")
    ap.add_argument("--day", help="grade only this day's records")
    ap.add_argument("--lane", choices=sorted(LANES), default="live", help="which lane's records to grade")
    args = ap.parse_args(argv)
    lane = LANES[args.lane]
    state_dir = Path(args.state_dir)
    out_dir = lane.folder(state_dir, args.out_dir)
    w = run(state_dir, out_dir, args.day, live_options(lane.questions), lane)
    for qid, s in w["sums"].items():
        print(f"{qid}: graded {s['n']}; hit rate {s['hit_rate']} vs always-flat {s['always_flat_hit_rate']}; mean Brier {s['mean_brier']}; bands {s['bands']}",
              file=sys.stderr)
    moved = sum(1 for v in w["questions"].values() if v["weight"] < 1.0)
    print(f"graded {w['new_this_run']} new, {w['graded_runs']} in all; weights: {len(w['questions'])} questions, {moved} below 1.0, "
          f"{sum(1 for v in w['questions'].values() if not v['in_step_3'])} left out of step 3", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
