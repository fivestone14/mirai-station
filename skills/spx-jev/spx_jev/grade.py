"""Step 6: the bars grade the sums, and the grades are handed to the question weights.

    python3 -m spx_jev.grade                # grade every ungraded record, rewrite weights.json
    python3 -m spx_jev.grade --day 2026-09-29
    python3 -m spx_jev.grade --lane tape    # the opening lane, under state/spx_jev/lanes/tape/
    python3 -m spx_jev.grade --lane premarket   # the reads before the open, under state/spx_jev/lanes/premarket/
    python3 -m spx_jev.grade --integral-backfill [--day D]   # the average-price grade of past graded horizons, from their bars
    python3 -m spx_jev.grade --integral-report  # per box, the flat share on the average price against the end price
    python3 -m spx_jev.grade --integral-loop-dry-run   # what the loop would learn from the average-price grade, built in a scratch folder

Two horizons are graded from the same record, each against its own band (lane.LIVE.horizons, from cuts.py):
    next_30   30 minutes, flat within NEXT_30_FLAT_BAND_SIGMA   (the primary: the end-price box the weights and pools learn from)
    next_60   60 minutes, flat within NEXT_60_FLAT_BAND_SIGMA   (graded beside it, for the comparison)

How a horizon is graded
    realized = (close h minutes after the row - spot at the row) / sigma_anchor
    band     = up if realized > flat band, down if realized < -flat band, else flat
    hit      = the shown sum's pick (the blend) == band; jev_hit the same for JEV's own pick
    brier    = sum over {up, flat, down} of (p - 1[band]) ** 2      (0 is perfect, 2 is worst)
    Each horizon is graded as soon as its own mark has a bar, so the primary never waits for the
    slower one. A record therefore gets up to one line per horizon in grades.jsonl (one line when
    both marks are already in). A horizon whose end falls up to CLOSE_GRACE_MIN past the close is
    graded at the closing bar; one that ends later is skipped for good and said so in the line. A
    record none of whose horizons can ever be graded is written as not graded, so it is never
    retried. A horizon graded once is never graded twice.

The ruler
    sigma_anchor is the day's morning anchor (labels.rulers.morning_ruler) as the read could know it:
    from the day's diary rows up to the read and the bars finished by then (read_anchor). Never the
    record's ``sigma``, which ratchets up with the live sigma through the day, so a mid-day spike
    cannot move a flat band or turn an outcome. The line keeps the ruler it was graded in
    (``anchor``: points and source); a read with no anchor at all is skipped for good.

What is graded
    The end-price sum a record carries is the one the card kept beside the call: JEV's sum blended
    with the time-of-day odds (see clock.py). The call itself, the average-price sum, is graded apart
    (the average-price grade, below). When the record also carries JEV's own sum and the clock's odds, each
    is scored beside it on the same outcome (``jev_brier``, ``jev_pick``, ``jev_hit``,
    ``clock_brier``), so the blend keeps having to earn its place against both of its parts.

Scheduled events and finished days
    A read carrying a tier-1 event due within 30 minutes (events.py) is graded like any other, and
    its line says so (``event_within_30``), but it is never handed to the question weights: a Fed
    minute must not teach a question what an ordinary half hour looks like. On a past day whose bars
    reach the close, a mark whose bars are missing (a halt, an outage) is closed out as a halted
    window instead of waiting forever for bars no run will ever add; today, a hole still waits.

The question weights
    Every graded line of the primary horizon, with the picks each live question gave afresh on that
    read, goes to the question weights' learn, the one seam a learning method plugs into. The live
    lane learns the loop there (pool.PoolWeights: every newly sealed session applied, each question's
    standing reported, every weight still 1.0); the tape and premarket lanes' weights are neutral. With the lane's
    integral_loop switch on (on the live lane) a second loop learns from the average-price grade beside it
    (integral_loop.IntegralPoolWeights), in state of its own, reported beside the end-price loop's under
    ``pool_integral``; the end-price loop keeps learning and its report stays where it was. Neither loop's pool
    reaches the phone (the promotion was retired on 2026-10-07): the call and the end-price sums keep their exact
    blend (integral_loop.shown, pool.shown).

A lane (lane.py) grades by its own settings. The tape lane's one horizon is banded from the record
itself: the tape unit measured at the read prices a flat and a big band in index points, and the
realized move lands in one of five bands (down_big, down_small, flat, up_small, up_big), read also
as a direction and a size (big or small), each scored against the matching view of the sum. Its
mark needs the exact bar (bar_gap_min 0), and a finished day's bars that stop close its records out.

The premarket lane (graded_from_settled_open) reads before the open, so its read's own spot already
knows the gap: its horizons run from the settled open instead, the close of the 09:34 bar, finished
at 09:35, to the closes at 09:45 and 10:05 (the 09:44 and 10:04 bars), in the pre-open ruler stamped
on the record (``ruler.points``). The line says where it was measured from (``from``), and its event
tag is taken at the settled open, over the window it is graded on, not at the read. It waits
while the settled open or a mark has no bar, and a finished day without them closes it out. On
every other lane a record stamped before its session's open is never graded from its spot: it is
written as not graded.

The average-price grade
    After every run, each horizon graded above is graded once more on the average price over its window
    (integral.py), from the same spot or settled open to the same mark, against the same flat band narrowed
    by integral.factor. On the box a lane's average-price sum forecasts (lane.average, the primary's) it
    grades that sum's call where JEV answered it, and scores its odds against the average-price label
    (integral_scores); elsewhere, and on a read from before the sum, it grades the end-price sum's own
    call. Each line names the sum it graded (``sum``). A call whose end-price sums got no answer
    (average_alone) has no horizon graded above; it is graded from its own window all the same
    (alone_line), its line saying so under ``end_price``. It is written to its own file: grades.jsonl, the
    question weights and the end-price loop's state are exactly what they were without it, while the card
    grades the phone's call on it and the average-price loop (integral_loop.py) learns from it. A window with
    bars missing waits today and is written as not graded on a finished day; ``--integral-backfill`` fills
    the file for past days from their saved bars without grading anything else, and ``--integral-report``
    prints the flat share on both grades over the same windows, which is how the factor is judged.

Outputs, all under the lane's folder (state/spx_jev/ for the live lane)
    grades.jsonl       one line per graded horizon of a record (append only, keyed by row_ts and
                       the ``horizons`` the line carries; the primary's line has its fields flat on top)
    weights.json       {"graded_runs": reads whose primary mark is graded, "primary",
                        "sums": {qid: {"n", "hit_rate", "committed_calls", "committed_hit_rate", "always_flat_hit_rate", "mean_brier", "bands",
                                       "blended": {"n", "mean_brier_blend", "mean_brier_jev", "mean_brier_clock", "jev_hit_rate"},
                                       "event_reads": {"n", "mean_brier"}},
                                 the lane's average-price sum, the call: {"n", "graded_on", "hit_rate", "committed_calls",
                                       "committed_hit_rate", "always_flat_hit_rate", "mean_brier", "mean_log_loss", "bands",
                                       "blended": {"n", "mean_brier_blend", "mean_brier_jev", "mean_brier_clock"},
                                       "event_reads"} (average_tally, after the run's average-price grade)},
                        "method", "min_weight", "questions": {qid: {"weight", "n", "in_step_3", "why"}},
                        on the live lane "pool" (the end-price loop's) and, its switch on, "pool_integral" (the
                        average-price loop's method, questions and pool, or {"failed": why}),
                        "new_this_run", "new_by_horizon", "closed_out"}
    weights_log.jsonl  one line per grading run that graded something: the tally and every weight that moved
    and every new line, once more, in the raw archive (archive.GradeRecord, keyed to its read)
    integral_grades.jsonl  one line per graded horizon, the average-price grade (append only, keyed by row_ts,
                       ``horizon`` and ``rule_version``): the sum graded (``sum``), integral.grade_window's fields,
                       the end-price label (``end_label``), on a RECORD horizon the five-band size line (``size``),
                       and for the average-price sum's call its ``scores`` and the edge JEV was told
                       (``edge_told``); beside it integral_grades.jsonl.lock, held while it is read and appended
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import archive, events, integral, scores
from .ask import load_questions, retired
from .hour import FIVE, average_probabilities
from .labels.measures import SETTLED_OPEN_BAR, close_at, settled_open
from .labels.rulers import SigmaRuler, morning_ruler, vix_at_settled_open
from .lane import LANES, LIVE, RECORD, Lane
from .pool import PoolWeights
from .sessions import session_close, session_open
from .state_builder import (DEFAULT_STATE_DIR, MarketContext, load_bars, load_jsonl, load_market_context, load_rows, parse_ts,
                            prior_bar_days)
from .weights import WEIGHTS_NAME, QuestionWeights

ET = ZoneInfo("America/New_York")

CLOSE_GRACE_MIN = 2                 # a horizon ending this far past the close is graded at the closing bar
BANDS = ("up", "flat", "down")      # "unsure" is a pick, never an outcome: its probability counts against the Brier
SIZES = ("big", "small")
WEIGHTS_LOG = "weights_log.jsonl"
INTEGRAL_NAME = "integral_grades.jsonl"
INTEGRAL_LOCK = f"{INTEGRAL_NAME}.lock"     # held while a run or a backfill reads, dedupes and appends the side file
BAD_PROBABILITIES = "not graded: bad probabilities"


def _bar_before(bars: list[dict], t: datetime) -> dict:
    """The last bar that had finished by ``t`` (the caller has checked one exists)."""
    one = timedelta(minutes=1)
    return [b for b in bars if parse_ts(b["ts"]) + one <= t][-1]


def settled_open_at(day: date) -> datetime:
    """When the day's settled open is known: the finish of its 09:34 bar, 09:35 ET."""
    return datetime.combine(day, SETTLED_OPEN_BAR, tzinfo=ET) + timedelta(minutes=1)


def horizon_start(row_ts: str, lane: Lane = LIVE) -> datetime:
    """Where the sums of the read at ``row_ts`` are measured from: the read's minute (a 15:32:00.4 read
    is a 15:32 read), or the settled open on a lane graded from it."""
    if lane.graded_from_settled_open:
        return settled_open_at(parse_ts(row_ts).astimezone(ET).date())
    return parse_ts(row_ts).replace(second=0, microsecond=0)


def mark_at(row_ts: str, minutes: int, lane: Lane = LIVE) -> datetime | None:
    """Where a horizon of the read at ``row_ts`` is measured: its start (horizon_start) plus ``minutes``,
    or the closing bar when that lands within CLOSE_GRACE_MIN past the close. None when it ends later
    than that: such a horizon is never graded. grade_one measures here, and the card shows the same minute."""
    t0 = horizon_start(row_ts, lane)
    close = session_close(t0)
    t1 = t0 + timedelta(minutes=minutes)
    if t1 > close + timedelta(minutes=CLOSE_GRACE_MIN):
        return None
    return min(t1, close)


def read_anchor(rows: list[dict], bars: list[dict], market: MarketContext | None, row_ts: str) -> SigmaRuler | None:
    """The morning anchor the read at ``row_ts`` could know: the day's diary rows stamped by then and the
    bars finished by then, through the anchor guard and its fallbacks."""
    t = parse_ts(row_ts)
    known = [b for b in bars if parse_ts(b["ts"]) + timedelta(minutes=1) <= t]
    return morning_ruler([r for r in rows if parse_ts(r["ts"]) <= t], vix_at_settled_open(market, row_ts[:10]), settled_open(known))


def stamped_ruler(rec: dict) -> SigmaRuler | None:
    """The pre-open ruler a premarket read stamped on its record (``ruler.points``), which its sums were
    asked in; None when the read could not form one."""
    points = (rec.get("ruler") or {}).get("points")
    return SigmaRuler(float(points), "pre_open") if isinstance(points, (int, float)) and points > 0 else None


def realized_band(x: float, flat: float) -> str:
    return "up" if x > flat else "down" if x < -flat else "flat"


def realized_bands(x: float, flat: float, big: float) -> tuple[str, str, str]:
    """A move in points against a record's bands: the five-way band, its direction and its size."""
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
    """Picks and probabilities per sum, from the record's ``by`` block; a record without one has nothing to grade."""
    by = rec.get("by") if isinstance(rec.get("by"), dict) else None
    if not by:
        return {}, {}
    return ({q: v.get("pick") for q, v in by.items() if isinstance(v, dict)},
            {q: (v.get("probabilities") or {}) for q, v in by.items() if isinstance(v, dict)})


def _has_band(rec: dict) -> bool:
    b = rec.get("band")
    return isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in ("flat_points", "big_points"))


def _grade_units(rec: dict, qid: str, realized: float, pick, p: dict) -> dict:
    """A RECORD horizon: the move in points against the bands stored on the record, and the sum's
    direction and size views (hour.views_of) each scored against the outcome they forecast."""
    band, direction, size = realized_bands(realized, float(rec["band"]["flat_points"]), float(rec["band"]["big_points"]))
    unit = (rec.get("ruler") or {}).get("unit_points")
    out = {"realized_points": round(realized, 2), "realized_units": round(realized / float(unit), 3) if isinstance(unit, (int, float)) and unit else None,
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
TOP_KEYS = ("realized_sigma", "realized_points", "realized_units", "band", "direction", "size", "pick", "hit", "brier", "p_band",
            "jev_pick", "jev_hit", "jev_brier", "clock_brier",
            "direction_pick", "direction_hit", "direction_brier", "size_pick", "size_hit", "size_brier")


def grade_one(rec: dict, bars: list[dict], done: set[str] | frozenset[str] = frozenset(), final: bool = False,
              lane: Lane = LIVE, anchor: SigmaRuler | None = None) -> dict | None:
    """One record against the bars of its day: every horizon not in ``done`` whose mark has a bar, a
    sigma band measured in ``anchor`` (read_anchor, or stamped_ruler on a lane graded from the
    settled open), a RECORD band in the record's own points.

    Returns the line to append, None when nothing new can be graded yet (a mark still ahead, or
    bars missing), or a ``graded: False`` line when no horizon of the record can ever be graded.
    The line names the horizons it carries, the ones still ``pending`` and the ones ``skipped``
    for good, so the next run grades only what is left."""
    t0 = horizon_start(rec["row_ts"], lane)
    close = session_close(t0)
    if t0 < session_open(t0):
        # a read before the open already knows the gap: from its own spot it would be graded on a move it saw
        return {"row_ts": rec["row_ts"], "graded": False, "reason": "stamped before the open: a read before the open is never graded from its spot"}
    if not bars:
        return None
    spot = settled_open(bars) if lane.graded_from_settled_open else float(rec["spot"])
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
        if flat != RECORD and anchor is None:
            skipped[qid] = ("no pre-open ruler on the record" if lane.graded_from_settled_open
                            else "no morning anchor: no diary row, live sigma or VIX to measure the move in")
            continue
        if spot is None:                              # the settled open's bar is not on file
            if final:
                skipped[qid] = "halted window: no settled open (the 09:34 bar) on a finished day"
            else:
                pending.append(qid)
            continue
        t1 = mark_at(rec["row_ts"], h, lane)          # inside the grace past the close, the closing bar stands for the mark
        if t1 is None:
            skipped[qid] = "ends past the close"      # can never be graded
            continue
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
        realized = (c1 - spot) / anchor.points
        band = realized_band(realized, flat)
        graded[qid] = {"realized_sigma": round(realized, 3), "band": band, "pick": picks.get(qid),
                       "hit": picks.get(qid) == band, "brier": _brier(p, band), "p_band": p.get(band)}
        part = parts.get(qid) or {}
        if "jev" in part:
            graded[qid].update({"jev_pick": part.get("jev_pick"), "jev_hit": part.get("jev_pick") == band,
                                "jev_brier": _brier(part["jev"], band)})
        if "clock" in part:
            graded[qid]["clock_brier"] = _brier(part["clock"], band)
    if not graded:
        if pending:
            return None                               # a mark is still ahead
        closed = {q: v for q, v in skipped.items() if str(v).startswith(("halted window", "no band", "no morning anchor", "no pre-open ruler"))}
        if closed:
            # closed out for good; when the other horizon was graded on an earlier run this line carries
            # only the halted one, so the read is never retried
            return {"row_ts": rec["row_ts"], "horizons": [], "pending": [], "skipped": skipped,
                    "reason": "; ".join(f"{q}: {v}" for q, v in closed.items())}
        if done:
            return None                               # nothing left to do
        return {"row_ts": rec["row_ts"], "graded": False, "reason": "every horizon ends past the close"}
    out = {"row_ts": rec["row_ts"], "used": rec.get("used", {}), "fresh": rec.get("fresh", {}),
           "horizons": list(graded), "pending": pending, "skipped": skipped, **graded}
    if lane.graded_from_settled_open:
        out["from"] = {"settled_open": spot, "at": t0.isoformat()}
    if any("realized_sigma" in g for g in graded.values()):
        out["anchor"] = {"points": anchor.points, "source": anchor.source}
    # a read before the open is tagged from its own clock, so the window it is graded over is tagged here
    ev = events.tag(t0) if lane.graded_from_settled_open else rec.get("event") if isinstance(rec.get("event"), dict) else None
    if ev:
        out["event_within_30"] = bool(ev.get("within_30"))
        out["event"] = ev.get("sentence")
    if lane.primary in graded:
        for k in TOP_KEYS:
            if k in graded[lane.primary]:
                out[k] = graded[lane.primary][k]      # the primary, flat on top, is what the weights read
    return out


def graded_horizons(lines: list[dict], lane: Lane = LIVE) -> dict[str, set[str]]:
    """``{row_ts: horizons already graded or skipped for good}`` from the grades file. A not-graded
    line counts as complete."""
    done: dict[str, set[str]] = defaultdict(set)
    for g in lines:
        if g.get("graded") is False:
            done[g["row_ts"]] |= set(lane.horizons)
        else:
            done[g["row_ts"]] |= set(g.get("horizons") or []) | set(g.get("skipped") or {})
    return done


def _tally(rows: list[dict]) -> dict:
    """A sum's record: the shown sum's hit rate and Brier, and, over the reads that were blended,
    the same Brier for the blend, for JEV's own sum and for the clock alone, side by side. The hit rate
    counts an unsure pick as a miss; ``committed_hit_rate`` beside it is over the ``committed_calls``
    alone, the picks that were not unsure, as the card and the store count calls."""
    n = len(rows)
    if not n:
        return {"n": 0, "hit_rate": None, "committed_calls": 0, "committed_hit_rate": None, "always_flat_hit_rate": None,
                "mean_brier": None, "bands": {}}
    committed = [g for g in rows if g.get("pick") not in (None, "unsure")]
    out = {"n": n, "hit_rate": round(sum(1 for g in rows if g["hit"]) / n, 3), "committed_calls": len(committed),
           "committed_hit_rate": round(sum(1 for g in committed if g["hit"]) / len(committed), 3) if committed else None,
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


def live_options(doc: dict | Path | str) -> dict[str, set[str]]:
    """``{qid: the picks it can give today}`` for every live question of ``doc`` (the question doc the
    service runs with, or a path to one): a retired question, or a pick from an option a question no
    longer has, must not reach today's weights. A score's picks are its level names, as a choice's are
    its option names (hour.named_levels)."""
    if not isinstance(doc, dict):
        doc = load_questions(doc)
    out = {}
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            if q.get("status") != "live":
                continue
            out[qid] = {"true", "false"} if q.get("type") == "noul" else set(q.get("options") or q.get("criteria") or ())
    return out


def weights_from(grades: list[dict], allowed: dict[str, set[str]], lane: Lane = LIVE, out_dir: Path | None = None) -> dict:
    """Each sum's tally from every grade line, and the question weights from the lines whose primary sum
    was graded, less those a scheduled event sat inside: the learning loop's (pool.PoolWeights, which
    reads the lane's records in ``out_dir``) on a lane that learns it, else neutral; with the lane's integral_loop switch
    on, integral_loop.IntegralPoolWeights learns beside it and is reported under ``pool_integral``. ``allowed`` is
    live_options(): only live questions are weighed, and only picks from their current options count."""
    primary = [g for g in grades if g.get("band")]
    sums = {qid: {**_tally([g[qid] for g in grades if isinstance(g.get(qid), dict)]), "event_reads": _events(grades, qid)}
            for qid in lane.horizons}
    graded = [g for g in primary if not g.get("event_within_30")]
    weights = PoolWeights.learn(graded, allowed, out_dir, lane) if lane.pool else QuestionWeights.learn(graded, allowed, out_dir)
    out = {"graded_runs": len(primary), "primary": lane.primary, "sums": sums, **weights.as_json()}
    if lane.pool and lane.integral_loop:
        # the end-price loop above keeps learning and keeps its report
        try:
            from .integral_loop import IntegralPoolWeights   # only when switched on: it reads this module, through clock too
            out["pool_integral"] = IntegralPoolWeights.learn(graded, allowed, out_dir, lane).as_json()
        except Exception as e:  # the average-price loop must never cost a run its weights, the average-price grade or the card
            print(f"average-price loop failed: {type(e).__name__}: {e}", file=sys.stderr)
            out["pool_integral"] = {"failed": f"{type(e).__name__}: {e}"}
    if lane.tag:
        out["lane"] = lane.tag
    return out


def average_tally(lines: list[dict], grades: list[dict], lane: Lane = LIVE) -> dict:
    """The call's record beside the end-price sums' (_tally), from integral_grades.jsonl ``lines``: every read whose
    average-price sum was graded on the average price over its box's window, a read graded under more than one rule
    standing on its newest. Its hit rate is on the label's direction (an unsure pick, which the sum does not offer, a
    miss there and no call in ``committed_hit_rate``), the flat share of the label, and the Brier and log loss of the
    odds the phone showed; over the blended reads the same Brier for JEV's own odds and the clock's; and the reads a
    scheduled event sat inside (their grades.jsonl line's ``event_within_30``) tallied apart."""
    newest: dict[str, dict] = {}
    for g in lines:
        if g.get("horizon") == lane.primary and g.get("rule_version") in integral.READABLE_VERSIONS:
            ts = str(g.get("row_ts", ""))
            if ts not in newest or g["rule_version"] > newest[ts]["rule_version"]:
                newest[ts] = g
    rows = [g for g in newest.values() if g.get("graded") and g.get("sum") == lane.average and isinstance(g.get("scores"), dict)]
    events = {g["row_ts"] for g in grades if g.get("event_within_30")}
    inside = [g for g in rows if g["row_ts"] in events]
    n = len(rows)
    out = {"n": n, "graded_on": "the average price", "hit_rate": None, "committed_calls": 0, "committed_hit_rate": None,
           "always_flat_hit_rate": None, "mean_brier": None, "mean_log_loss": None, "bands": {},
           "event_reads": {"n": len(inside), "mean_brier": round(sum(g["scores"]["brier"] for g in inside) / len(inside), 4) if inside else None}}
    if not n:
        return out
    committed = [g for g in rows if g.get("verdict") != "passed"]
    out.update({"hit_rate": round(sum(1 for g in rows if g.get("verdict") == "right") / n, 3), "committed_calls": len(committed),
                "committed_hit_rate": round(sum(1 for g in committed if g.get("verdict") == "right") / len(committed), 3) if committed else None,
                "always_flat_hit_rate": round(sum(1 for g in rows if g["label"] == "flat") / n, 3),
                "mean_brier": round(sum(g["scores"]["brier"] for g in rows) / n, 4),
                "mean_log_loss": round(sum(g["scores"]["log_loss"] for g in rows) / n, 4), "bands": dict(Counter(g["label"] for g in rows))})
    blended = [g["scores"] for g in rows if "jev_brier" in g["scores"] and "clock_brier" in g["scores"]]
    if blended:
        k = len(blended)
        out["blended"] = {"n": k, "mean_brier_blend": round(sum(s["brier"] for s in blended) / k, 4),
                          "mean_brier_jev": round(sum(s["jev_brier"] for s in blended) / k, 4),
                          "mean_brier_clock": round(sum(s["clock_brier"] for s in blended) / k, 4)}
    return out


def log_weights(path: Path, before: dict, weights: dict, new: int) -> dict:
    """One line per grading run that graded something: the tallies, and every weight that moved."""
    changes = []
    for qid, w in sorted(weights["questions"].items()):
        old = before.get(qid, {}).get("weight")
        if old is None or abs(float(old) - float(w["weight"])) >= 0.0005:
            changes.append({"question": qid, "before": old, "after": w["weight"], "n": w["n"], "in_step_3": w["in_step_3"]})
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "graded_runs": weights["graded_runs"],
            "new": new, **weights["sums"][weights["primary"]], "sums": weights["sums"], "changes": changes}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def run(state_dir: Path, out_dir: Path, allowed: dict[str, set[str]], day: str | None = None, lane: Lane = LIVE) -> dict:
    """Grade every mark that has passed and rewrite the weights. ``allowed`` (``live_options()``) keeps
    retired questions and dropped options out of the weights."""
    hour_dir = out_dir / "hour"
    grades_path = out_dir / "grades.jsonl"
    done = graded_horizons(load_jsonl(grades_path), lane)
    every = set(lane.horizons)
    new = []
    days = [day] if day else sorted(p.stem for p in hour_dir.glob("*.jsonl")) if hour_dir.exists() else []
    for d in days:
        recs = [r for r in load_jsonl(hour_dir / f"{d}.jsonl") if done.get(r.get("row_ts"), set()) != every and isinstance(r.get("by"), dict)]
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
        # a lane banded in sigma measures every read in the day's morning anchor, or in the pre-open ruler
        # its read stamped when it is graded from the settled open
        in_anchor = any(flat != RECORD for _, flat in lane.horizons.values()) and not lane.graded_from_settled_open
        rows, market = (load_rows(state_dir, d), load_market_context(state_dir, d)) if in_anchor else ([], None)
        for r in recs:
            if done.get(r["row_ts"], set()) == every:
                continue                              # the same row written twice (a run by hand): graded once
            anchor = stamped_ruler(r) if lane.graded_from_settled_open else read_anchor(rows, bars, market, r["row_ts"]) if in_anchor else None
            g = grade_one(r, bars, done.get(r["row_ts"], set()), final=d < datetime.now(ET).date().isoformat(), lane=lane, anchor=anchor)
            if g:
                new.append(g)
                done[g["row_ts"]] |= every if g.get("graded") is False else set(g["horizons"]) | set(g["skipped"])
    if new:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(grades_path, "a", encoding="utf-8") as f:
            for g in new:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        for g in new:
            archive.append(lane.archive_folder(state_dir, out_dir), g["row_ts"][:10], archive.GradeRecord(read_id=archive.read_id(lane.name, g["row_ts"]),
                                                                            lane=lane.name, row_ts=g["row_ts"], grade=g))
    grades = load_jsonl(grades_path)
    weights = weights_from(grades, allowed, lane, out_dir)
    weights["new_this_run"] = sum(1 for g in new if g.get("band"))
    weights["new_by_horizon"] = {q: sum(1 for g in new if q in (g.get("horizons") or [])) for q in lane.horizons}
    weights["closed_out"] = sum(1 for g in new if g.get("graded") is False)
    try:
        integral_run(state_dir, out_dir, lane, day)  # the average-price grade reads what was written above and writes only its own file
    except Exception as e:  # the average-price grade must never cost a run its grades, the card or the close-out
        print(f"average-price grade failed: {type(e).__name__}: {e}", file=sys.stderr)
    if lane.average:
        # the call's lasting record, from the average-price grades this run has just brought up to date
        try:
            weights["sums"][lane.average] = average_tally(load_jsonl(out_dir / INTEGRAL_NAME), grades, lane)
        except Exception as e:  # nor may its record cost the run its weights
            print(f"the call's record failed: {type(e).__name__}: {e}", file=sys.stderr)
            weights["sums"][lane.average] = {"failed": f"{type(e).__name__}: {e}"}
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
    os.replace(tmp, weights_path)                    # step 3 never sees a half file
    return weights


def _size_line(old: dict) -> dict:
    """A RECORD horizon's size, as the end price graded it in five bands, beside the size the call named (none for flat or unsure)."""
    pick = old.get("pick")
    call = pick.split("_")[1] if isinstance(pick, str) and pick.endswith(SIZES) else None
    return {"band": old["band"], "size": old["size"], "call": call, "right": call == old["size"] if call else None}


def average_call(rec: dict | None, qid: str, lane: Lane = LIVE) -> dict | None:
    """The average-price sum's answer on a sum record, when horizon ``qid`` is the box it forecasts (the lane's primary)
    and JEV answered it with a pick and probabilities; None for any other box, a record from before the sum, or a read
    it got no answer on, or one whose odds are not odds over up, flat and down (hour.average_probabilities). What the
    average-price grade grades, and what the phone calls."""
    if not lane.average or qid != lane.primary or not isinstance(rec, dict):
        return None
    avg = rec.get("average")
    if not isinstance(avg, dict) or (probs := average_probabilities(avg.get("probabilities"))) is None or avg.get("pick") not in probs:
        return None
    return avg


def checked_call(rec: dict | None, qid: str, lane: Lane = LIVE) -> dict | None:
    """The average-price sum's answer the average-price grade of horizon ``qid`` grades: average_call on the box it
    forecasts, and on a lane graded from the settled open, whose every sum is checked at the same marks, the same
    answer at each of them, so the 09:45 check grades the pick the card shows. None where average_call has none."""
    return average_call(rec, lane.primary if lane.graded_from_settled_open and qid in lane.horizons else qid, lane)


def told_edge(avg: dict | None) -> float | None:
    """The flat edge in points the average-price sum was told (hour.average_window), which its grade is set against, so
    the two can never part at the edge; None without one."""
    e = (avg or {}).get("edge_points")
    return float(e) if isinstance(e, (int, float)) and not isinstance(e, bool) and e > 0 else None


def bad_average(rec: dict | None, qid: str, lane: Lane = LIVE) -> bool:
    """Whether the record holds an answer to the average-price sum for box ``qid`` (no ``error`` on it) that cannot be
    graded: its odds or its pick are not the sum's (average_call). Such a read is written as not graded."""
    avg = rec.get("average") if lane.average and qid == lane.primary and isinstance(rec, dict) else None
    return isinstance(avg, dict) and not avg.get("error") and average_call(rec, qid, lane) is None


def _log_loss(p: dict, band: str) -> float:
    return round(scores.log_loss(scores.floored(p), band), 4)


def integral_scores(avg: dict, label: str) -> dict:
    """The average-price sum's probabilities scored against the average-price label: the Brier over up, flat and down
    and the log loss as the learning loop scores a forecast (scores.log_loss of scores.floored, each outcome at least
    scores.EPS), for the shown sum and, when it was blended, for JEV's own and the clock's beside it, as grade_one
    scores the end-price parts."""
    out = {"brier": _brier(avg["probabilities"], label), "log_loss": _log_loss(avg["probabilities"], label)}
    if (avg.get("blend") or {}).get("used"):
        for part in ("jev", "clock"):
            if isinstance(p := (avg.get(part) or {}).get("probabilities"), dict):
                out.update({f"{part}_brier": _brier(p, label), f"{part}_log_loss": _log_loss(p, label)})
    return out


def integral_line(line: dict, qid: str, rec: dict | None, bars: list[dict], prior: dict[str, list[dict]], lane: Lane = LIVE) -> dict:
    """The average-price grade of horizon ``qid`` of a grades.jsonl ``line``, over the window the line was graded on
    (horizon_start to mark_at) and against its flat band: the line's anchor times the horizon's sigma band, or the
    record's ``band.flat_points``. Measured from the read's spot, or the settled open the line was measured from. The
    call graded is the average-price sum's where it answered (average_call), against the edge JEV was told (told_edge,
    kept as ``edge_told``) and with its scores (integral_scores), else the end-price sum's own; ``sum`` names the one
    graded. On a lane graded from the settled open a shorter check grades the same call (checked_call) against its own
    window's narrowed band, unscored, since the call's odds and edge are for the box's window. A record whose
    average-price answer cannot be graded (bad_average) is written as not graded."""
    head = {"row_ts": line["row_ts"], "horizon": qid, "rule_version": integral.RULE_VERSION}
    if rec is None:
        return {**head, "graded": False, "reason": "not graded: the read's sum record is not on file"}
    if bad_average(rec, qid, lane):
        return {**head, "sum": lane.average, "graded": False, "reason": BAD_PROBABILITIES}
    minutes, flat = lane.horizons[qid]
    t0, t1 = horizon_start(line["row_ts"], lane), mark_at(line["row_ts"], minutes, lane)
    spot = float(line["from"]["settled_open"]) if lane.graded_from_settled_open else float(rec["spot"])
    # a line graded before the morning anchor was measured in the record's sigma
    ruler = (line.get("anchor") or {}).get("points") or rec.get("sigma")
    points = float(rec["band"]["flat_points"]) if flat == RECORD else flat * float(ruler)
    old = line.get(qid) or {}                               # none for a call whose end-price sums got no answer (alone_line)
    avg = checked_call(rec, qid, lane)
    own = avg is not None and qid == lane.primary          # the call's own box: its edge and odds are this window's
    if avg:
        pick, probs = avg["pick"], avg["probabilities"]
    else:
        pick, probs = old.get("pick"), ((rec.get("by") or {}).get(qid) or {}).get("probabilities") or {}
    out = {**head, "sum": lane.average if avg else qid,
           **integral.grade_window(bars, prior, t0, int((t1 - t0).total_seconds() // 60), spot, points, pick, probs,
                                   told_edge(avg) if own else None)}
    if out["graded"]:
        if old:
            out["end_label"] = old["direction"] if flat == RECORD else old["band"]
            if flat == RECORD:
                out["size"] = _size_line(old)
        if own:
            out.update({"scores": integral_scores(avg, out["label"]), "edge_told": avg.get("edge_points")})
    return out


def average_alone(rec: dict, lane: Lane = LIVE) -> bool:
    """Whether a sum record's call stands on its average-price sum alone: JEV answered that sum (average_call) and gave
    the end-price sums no answer for the lane's primary, so grade_one never grades the read and the average-price grade
    grades the call from its own window (alone_line)."""
    return average_call(rec, lane.primary, lane) is not None and ((rec.get("by") or {}).get(lane.primary) or {}).get("pick") is None


ALONE = "no end-price answer: the end-price sums got none, so the call is graded on its own window"


def alone_line(rec: dict, bars: list[dict], prior: dict[str, list[dict]], lane: Lane = LIVE, anchor: SigmaRuler | None = None,
               qid: str | None = None) -> dict:
    """The average-price grade of a call standing on its average-price sum alone (average_alone) in box ``qid`` (the
    primary unless named), over the window grade_one would have graded that box on: from the read's spot, or the
    settled open, to the box's mark, in ``anchor`` (read_anchor, or stamped_ruler on a lane graded from the settled
    open) or the record's band, as integral_line grades any other; on a lane graded from the settled open a shorter
    box is the call's check at its own mark (checked_call). It says it had no end-price answer (``end_price``) and
    carries no end-price label. A read that can never be graded says why, as grade_one would; one whose settled open
    is not on file carries integral.NOT_GRADED, so today it waits for the bar."""
    qid = qid or lane.primary
    head = {"row_ts": rec["row_ts"], "horizon": qid, "rule_version": integral.RULE_VERSION, "sum": lane.average, "end_price": ALONE}
    minutes, flat = lane.horizons[qid]
    t0 = horizon_start(rec["row_ts"], lane)
    why = ("stamped before the open: a read before the open is never graded from its spot" if t0 < session_open(t0) else
           "ends past the close" if mark_at(rec["row_ts"], minutes, lane) is None else
           "no band on the record" if flat == RECORD and not _has_band(rec) else
           ("no pre-open ruler on the record" if lane.graded_from_settled_open else
            "no morning anchor: no diary row, live sigma or VIX to measure the move in") if flat != RECORD and anchor is None else None)
    if why:
        return {**head, "graded": False, "reason": f"not graded: {why}"}
    line: dict = {"row_ts": rec["row_ts"]}
    if lane.graded_from_settled_open:
        if (spot := settled_open(bars)) is None:
            return {**head, "graded": False, "reason": integral.NOT_GRADED}
        line["from"] = {"settled_open": spot}
    if anchor is not None:
        line["anchor"] = {"points": anchor.points}
    return {**integral_line(line, qid, rec, bars, prior, lane), "end_price": ALONE}


def integral_run(state_dir: Path, out_dir: Path, lane: Lane = LIVE, day: str | None = None) -> list[dict]:
    """Append the average-price grade of every horizon grades.jsonl has graded and integral_grades.jsonl does not
    hold under this rule_version, from the day's bars; nothing else is written. A window with bars missing waits
    today, when they can still come, and is written as not graded on a finished day, so none is retried for ever
    and none is written twice; a read whose average-price odds are not odds is written as not graded
    (BAD_PROBABILITIES), and one whose grading fails is logged and left for the next run, never holding back the
    others. A call standing on its average-price sum alone (average_alone), which grades.jsonl never grades, is graded
    from its own window (alone_line) under the same rules, and on a lane graded from the settled open at every box's
    mark, so its 09:45 check is graded as any call's is. Returns the new lines."""
    hour_dir = out_dir / "hour"
    if not (out_dir / "grades.jsonl").exists() and not hour_dir.exists():
        return []
    # a backfill by hand and a job's run can overlap: each reads what the other appended before it adds its own
    with open(out_dir / INTEGRAL_LOCK, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        path = out_dir / INTEGRAL_NAME
        have = {(g.get("row_ts"), g.get("horizon"), g.get("rule_version")) for g in load_jsonl(path)}
        todo: dict[str, list[tuple[dict, str]]] = defaultdict(list)
        for g in load_jsonl(out_dir / "grades.jsonl"):
            for qid in g.get("horizons") or []:
                if (g["row_ts"], qid, integral.RULE_VERSION) not in have and (day is None or g["row_ts"][:10] == day):
                    todo[g["row_ts"][:10]].append((g, qid))
        alone: dict[str, list[tuple[str, str]]] = defaultdict(list)
        boxes = tuple(lane.horizons) if lane.graded_from_settled_open else (lane.primary,)
        for d in [day] if day else sorted(p.stem for p in hour_dir.glob("*.jsonl")) if hour_dir.exists() else []:
            for r in load_jsonl(hour_dir / f"{d}.jsonl"):
                if average_alone(r, lane):
                    for qid in boxes:
                        if (r["row_ts"], qid, integral.RULE_VERSION) not in have and (r["row_ts"], qid) not in alone[d]:
                            alone[d].append((r["row_ts"], qid))
        today = datetime.now(ET).date().isoformat()
        new = []
        for d in sorted(set(todo) | set(alone)):
            recs: dict[str, dict] = {}
            for r in load_jsonl(hour_dir / f"{d}.jsonl"):
                if isinstance(r.get("by"), dict) or average_alone(r, lane):
                    recs.setdefault(r.get("row_ts"), r)   # the first of a row written twice is the one graded
            bars, prior = load_bars(state_dir, d), prior_bar_days(state_dir, d)
            items = [(g, qid, None) for g, qid in todo.get(d, [])]
            if alone.get(d):
                # graded in the anchor the read could know, as grade.run measures it, or in its own stamped ruler
                in_anchor = any(flat != RECORD for _, flat in lane.horizons.values()) and not lane.graded_from_settled_open
                rows, market = (load_rows(state_dir, d), load_market_context(state_dir, d)) if in_anchor else ([], None)
                for ts, qid in alone[d]:
                    anchor = stamped_ruler(recs[ts]) if lane.graded_from_settled_open else read_anchor(rows, bars, market, ts) if in_anchor else None
                    items.append(({"row_ts": ts}, qid, anchor))
            for g, qid, anchor in items:
                key = (g["row_ts"], qid, integral.RULE_VERSION)
                if key in have:
                    continue
                try:
                    line = (integral_line(g, qid, recs.get(g["row_ts"]), bars, prior, lane) if "horizons" in g else
                            alone_line(recs[g["row_ts"]], bars, prior, lane, anchor, qid))
                except Exception as e:  # one read that cannot be graded must never hold back the rest of the batch
                    print(f"average-price grade of {g['row_ts']} {qid} failed: {type(e).__name__}: {e}", file=sys.stderr)
                    continue                              # nothing written: a later run tries it again
                if line.get("reason") == integral.NOT_GRADED and d >= today:
                    continue                              # a hole today can still be filled: a later run grades it
                new.append(line)
                have.add(key)
        if new:
            with open(path, "a", encoding="utf-8") as f:
                for line in new:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return new


def integral_report(out_dir: Path, lane: Lane = LIVE) -> dict[str, dict]:
    """Per horizon of the lane, over integral_grades.jsonl under this rule_version: the windows graded, the flat share
    on the average price and on the end price over those same windows, the stale reads among them, and the
    windows not graded."""
    lines = [g for g in load_jsonl(out_dir / INTEGRAL_NAME) if g.get("rule_version") == integral.RULE_VERSION]
    out = {}
    for qid in lane.horizons:
        mine = [g for g in lines if g.get("horizon") == qid]
        graded = [g for g in mine if g.get("graded")]
        n = len(graded)
        ends = [g for g in graded if "end_label" in g]       # a call graded alone has no end-price label (alone_line)
        out[qid] = {"n": n, "flat_integral": round(sum(1 for g in graded if g["label"] == "flat") / n, 3) if n else None,
                    "flat_end": round(sum(1 for g in ends if g["end_label"] == "flat") / len(ends), 3) if ends else None,
                    "stale": sum(1 for g in graded if g.get("stale_read")), "not_graded": len(mine) - n}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grade the sums against the bars and refresh the question weights.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default the lane's folder under <state-dir>")
    ap.add_argument("--day", help="grade only this day's records")
    ap.add_argument("--lane", choices=sorted(LANES), default="live", help="which lane's records to grade")
    ap.add_argument("--integral-backfill", action="store_true", help="only fill the average-price grade of graded horizons from their bars")
    ap.add_argument("--integral-report", action="store_true", help="only print each box's flat share on the average and on the end price")
    ap.add_argument("--integral-loop-dry-run", action="store_true",
                    help="only print what the learning loop would learn from the average-price grade, built in a scratch folder")
    args = ap.parse_args(argv)
    lane = LANES[args.lane]
    state_dir = Path(args.state_dir)
    out_dir = lane.folder(state_dir, args.out_dir)
    if args.integral_report:
        # every lane's boxes from their own folders, or the one lane pointed at another folder
        for each, folder in ([(lane, out_dir)] if args.out_dir else [(x, x.folder(state_dir)) for x in LANES.values()]):
            for qid, r in integral_report(folder, each).items():
                end = f"{r['flat_end']:.1%} on the end price" if r["flat_end"] is not None else "no end price to set it against"
                print(f"{each.name} {qid}: " + (f"{r['n']} windows graded; flat {r['flat_integral']:.1%} on the average price against "
                                                f"{end}; {r['stale']} stale reads, {r['not_graded']} not graded"
                                                if r["n"] else f"no window graded on the average price yet; {r['not_graded']} not graded"))
        return 0
    if args.integral_loop_dry_run:
        if not lane.pool:
            print(f"the {lane.name} lane keeps no learning loop", file=sys.stderr)
            return 1
        from .integral_loop import describe, dry_run
        print("\n".join(describe(dry_run(out_dir, lane), lane)))
        return 0
    if args.integral_backfill:
        new = integral_run(state_dir, out_dir, lane, args.day)
        print(f"average-price grade: {sum(1 for g in new if g.get('graded'))} new windows graded, "
              f"{sum(1 for g in new if not g.get('graded'))} not graded, into {out_dir / INTEGRAL_NAME}", file=sys.stderr)
        return 0
    # a question retired from the lane by the day graded is not weighed (ask.retired), as the service's own runs do not
    day = args.day or datetime.now(ET).date().isoformat()
    w = run(state_dir, out_dir, live_options(retired(load_questions(lane.questions, lane.key), day)), args.day, lane)
    for qid, s in w["sums"].items():
        print(f"{qid}: its record failed: {s['failed']}" if "failed" in s else
              f"{qid}: graded {s['n']}; hit rate {s['hit_rate']} vs always-flat {s['always_flat_hit_rate']}; mean Brier {s['mean_brier']}; bands {s['bands']}",
              file=sys.stderr)
    left_out = sum(1 for v in w["questions"].values() if not v["in_step_3"])
    print(f"graded {w['new_this_run']} new, {w['graded_runs']} in all; {w['method']} weights on {len(w['questions'])} questions, "
          f"{left_out} left out of step 3", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
