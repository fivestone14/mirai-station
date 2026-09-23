"""Step 6: the bars grade the sums, and the grades move each question's weight.

    python3 -m sndk_jev.grade                # grade every ungraded record, rewrite weights.json
    python3 -m sndk_jev.grade --day 2026-09-22

Two horizons are graded from the same record, each against its own band:
    next_30   30 minutes, flat within 0.12 sigma   (the primary: the phone's sum, the weights' teacher)
    next_60   60 minutes, flat within 0.17 sigma   (graded beside it, for the comparison)

How a horizon is graded
    realized = (close h minutes after the row - spot at the row) / sigma
    band     = up if realized > flat band, down if realized < -flat band, else flat
    hit      = JEV's pick == band
    brier    = sum over {up, flat, down} of (p - 1[band]) ** 2      (0 is perfect, 2 is worst)
    A record is graded once every horizon that can finish by the close has its bars. A horizon
    whose end falls up to CLOSE_GRACE_MIN past the close is graded at the closing bar; one that
    ends later is skipped. A record none of whose horizons can be graded is written to
    grades.jsonl as not graded, so it is never retried. A row graded once is never graded twice.

How a weight moves
    For each step-2 question, every graded record where the question was answered AFRESH gives
    one pair (what it picked, what the primary horizon did); a held answer never pairs, since it
    was given before the outcome it would be judged on. Its weight is the mutual information
    between those two, divided by the largest MI among the questions that have MIN_GRADED pairs
    of their own, so the most telling question has weight 1.0. A question with fewer than
    MIN_GRADED pairs keeps 1.0: no evidence, no exclusion. Under hour.MIN_WEIGHT the question's
    sentence is left out of step 3, but it is still asked every read, so it keeps earning pairs
    and can climb back.

Outputs, all under state/jev/
    grades.jsonl       one line per graded record (append only, keyed by row_ts)
    weights.json       {"graded_runs": n, "questions": {qid: {"weight", "mi", "n", "in_step_3"}}, "sum": {...}}
    weights_log.jsonl  one line per grading run that graded something: the tally and every weight that moved
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hour import HOUR_QUESTIONS, MIN_WEIGHT, PRIMARY, WEIGHTS_NAME
from .state_builder import DEFAULT_STATE_DIR, close_at, load_bars, parse_ts, session_close

MIN_GRADED = 40         # fresh pairs a question needs before its own weight can move away from 1.0
CLOSE_GRACE_MIN = 2     # a horizon ending this far past the close is graded at the closing bar
BANDS = ("up", "flat", "down")
WEIGHTS_LOG = "weights_log.jsonl"


def horizons(doc_path: Path | str = HOUR_QUESTIONS) -> dict[str, tuple[int, float]]:
    """``{qid: (minutes, flat band)}`` from the sums' doc, so grading and asking share one number."""
    with open(doc_path, encoding="utf-8") as f:
        d = json.load(f)
    return {qid: (int(h["minutes"]), float(h["flat_band_sigma"])) for qid, h in d["horizons"].items()}


HORIZONS = horizons()


def realized_band(x: float, flat: float) -> str:
    return "up" if x > flat else "down" if x < -flat else "flat"


def _picks(rec: dict) -> tuple[dict, dict]:
    """Picks and probabilities per sum, from the record's ``by`` block. A record without one is
    from before the two-sum switch (a 60-minute forecast against a 0.24 band): it is never graded,
    because grading it as a 30-minute forecast would teach the weights from the wrong question."""
    by = rec.get("by") if isinstance(rec.get("by"), dict) else None
    if not by:
        return {}, {}
    return ({q: v.get("pick") for q, v in by.items() if isinstance(v, dict)},
            {q: (v.get("probabilities") or {}) for q, v in by.items() if isinstance(v, dict)})


def grade_one(rec: dict, bars: list[dict]) -> dict | None:
    """One record against the bars of its day. None when a horizon that will finish before the
    close has not finished yet, or bars are missing. A horizon past the close is skipped for good."""
    t0 = parse_ts(rec["row_ts"])
    close = session_close(t0)
    spot, sigma = float(rec["spot"]), float(rec["sigma"])
    if sigma <= 0 or not bars:
        return None
    picks, probs = _picks(rec)
    if not picks:
        return None
    last_done = parse_ts(bars[-1]["ts"]) + timedelta(minutes=1)
    out = {"row_ts": rec["row_ts"], "used": rec.get("used", {}), "fresh": rec.get("fresh", rec.get("used", {}))}
    for qid, (h, flat) in HORIZONS.items():
        t1 = t0 + timedelta(minutes=h)
        if t1 > close + timedelta(minutes=CLOSE_GRACE_MIN):
            continue                                  # can never be graded
        t1 = min(t1, close)                           # inside the grace: the closing bar stands for the mark
        c1 = close_at(bars, t1)
        if c1 is None or last_done < t1:
            return None                               # not yet; try on a later run
        realized = (c1 - spot) / sigma
        band = realized_band(realized, flat)
        p = probs.get(qid) or {}
        brier = sum((float(p.get(b, 0.0)) - (1.0 if b == band else 0.0)) ** 2 for b in BANDS)
        out[qid] = {"realized_sigma": round(realized, 3), "band": band, "pick": picks.get(qid),
                    "hit": picks.get(qid) == band, "brier": round(brier, 4), "p_band": p.get(band)}
    if not any(q in out for q in HORIZONS):
        return {"row_ts": rec["row_ts"], "graded": False, "reason": "every horizon ends past the close"}
    prim = out.get(PRIMARY)
    for k in ("realized_sigma", "band", "pick", "hit", "brier", "p_band"):
        out[k] = prim[k] if prim else None            # the primary, flat on top, is what the weights read
    return out


def mutual_information(pairs: list[tuple[str, str]]) -> float:
    n = len(pairs)
    if n == 0:
        return 0.0
    ca, cb, cab = Counter(a for a, _ in pairs), Counter(b for _, b in pairs), Counter(pairs)
    return sum(v / n * math.log((v / n) / ((ca[a] / n) * (cb[b] / n))) for (a, b), v in cab.items())


def _tally(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "hit_rate": None, "always_flat_hit_rate": None, "mean_brier": None, "bands": {}}
    return {"n": n, "hit_rate": round(sum(1 for g in rows if g["hit"]) / n, 3),
            "always_flat_hit_rate": round(sum(1 for g in rows if g["band"] == "flat") / n, 3),
            "mean_brier": round(sum(g["brier"] for g in rows) / n, 4),
            "bands": dict(Counter(g["band"] for g in rows))}


def weights_from(grades: list[dict]) -> dict:
    """Every step-2 question's weight from the primary horizon's grades, plus each sum's own tally."""
    primary = [g for g in grades if g.get("band")]
    pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for g in primary:
        # only answers given afresh on that read pair with its outcome; a held answer predates it
        for qid, pick in (g.get("fresh") or g.get("used") or {}).items():
            pairs[qid].append((str(pick), g["band"]))
    n_runs = len(primary)
    mi = {qid: mutual_information(p) for qid, p in pairs.items()}
    ready = {qid for qid, p in pairs.items() if len(p) >= MIN_GRADED}
    top = max((mi[q] for q in ready), default=0.0)
    questions = {}
    for qid, p in pairs.items():
        if qid not in ready:
            w, why = 1.0, f"{len(p)} fresh pairs, under the {MIN_GRADED} needed; keeps weight 1.0"
        elif top <= 0:
            w, why = 1.0, "no question's picks carry information about the outcome yet; keeps weight 1.0"
        else:
            w = mi[qid] / top
            why = f"mutual information with the {PRIMARY} outcome over {len(p)} fresh pairs, relative to the best question"
        questions[qid] = {"weight": round(w, 3), "mi": round(mi[qid], 4), "n": len(p), "why": why,
                          "in_step_3": w >= MIN_WEIGHT}
    sums = {qid: _tally([g[qid] for g in grades if isinstance(g.get(qid), dict)]) for qid in HORIZONS}
    return {"graded_runs": n_runs, "min_graded": MIN_GRADED, "min_weight": MIN_WEIGHT, "primary": PRIMARY,
            "sum": sums[PRIMARY], "sums": sums, "questions": questions}


def _read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def log_weights(path: Path, before: dict, weights: dict, new: int) -> dict:
    """One line per grading run that graded something: the tallies, and every weight that moved."""
    changes = []
    for qid, w in sorted(weights["questions"].items()):
        old = before.get(qid, {}).get("weight")
        if old is None or abs(float(old) - float(w["weight"])) >= 0.0005:
            changes.append({"question": qid, "before": old, "after": w["weight"], "mi": w["mi"], "n": w["n"],
                            "in_step_3": w["in_step_3"]})
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "graded_runs": weights["graded_runs"],
            "new": new, **weights["sum"], "sums": weights["sums"], "changes": changes}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def run(state_dir: Path, out_dir: Path, day: str | None = None) -> dict:
    hour_dir = out_dir / "hour"
    grades_path = out_dir / "grades.jsonl"
    have = {g["row_ts"] for g in _read_jsonl(grades_path)}
    new = []
    days = [day] if day else sorted(p.stem for p in hour_dir.glob("*.jsonl")) if hour_dir.exists() else []
    for d in days:
        recs = [r for r in _read_jsonl(hour_dir / f"{d}.jsonl") if r.get("row_ts") not in have and isinstance(r.get("by"), dict)]
        if not recs:
            continue
        bars = load_bars(state_dir, d)
        for r in recs:
            if r["row_ts"] in have:
                continue                              # the same row written twice (a run by hand): graded once
            g = grade_one(r, bars)
            if g:
                new.append(g)
                have.add(g["row_ts"])
    if new:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(grades_path, "a", encoding="utf-8") as f:
            for g in new:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
    grades = _read_jsonl(grades_path)
    weights = weights_from(grades)
    weights["new_this_run"] = sum(1 for g in new if g.get("band"))
    weights["closed_out"] = sum(1 for g in new if not g.get("band"))
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
    weights_path.write_text(json.dumps(weights, ensure_ascii=False, indent=1), encoding="utf-8")
    return weights


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grade the sums against the bars and move the question weights.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default <state-dir>/jev")
    ap.add_argument("--day", help="grade only this day's records")
    args = ap.parse_args(argv)
    state_dir = Path(args.state_dir)
    out_dir = Path(args.out_dir) if args.out_dir else state_dir / "jev"
    w = run(state_dir, out_dir, args.day)
    for qid, s in w["sums"].items():
        print(f"{qid}: graded {s['n']}; hit rate {s['hit_rate']} vs always-flat {s['always_flat_hit_rate']}; mean Brier {s['mean_brier']}; bands {s['bands']}",
              file=sys.stderr)
    moved = sum(1 for v in w["questions"].values() if v["weight"] < 1.0)
    print(f"graded {w['new_this_run']} new, {w['graded_runs']} in all; weights: {len(w['questions'])} questions, {moved} below 1.0, "
          f"{sum(1 for v in w['questions'].values() if not v['in_step_3'])} left out of step 3", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
