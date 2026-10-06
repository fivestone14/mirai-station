"""The additive scorer: each answer pushes the historical odds, one vote per group, a layer volume per layer and stage.

How one answer becomes a push (the design page's three steps), per stage ("move": will it move?; "direction": up, given a move?):
    blend = (moves + ANSWER_PRIOR_READS x normal) / (reads + ANSWER_PRIOR_READS)   the answer's record, pulled toward its own normal
    score = ln(blend / (1 - blend))                                               on the score line, where 0 is a coin flip
    push  = score - ln(normal / (1 - normal))                                     how far above its own normal the answer sits
where ``normal`` is the mean historical odds of the rows that carried that answer (so time of day is not counted twice).

Forecast: P(move) = sigmoid( logit(historical P(move)) + sum over layers of layer_volume[layer] x sum over the layer's groups of
the group's vote ), a group's vote being the average push of its answered members. P(up | move) likewise on the direction
stage; the two combine into {up, flat, down}.

Each layer volume (per stage) is chosen by an inner walk-forward by day: pushes are fitted on the days before each day, that
day's reads are scored, and the grid value with the lowest mean penalty wins (0 when nothing helps). Nothing here ever sees
its own day.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from ..scores import floored
from .name_map import STAGES

ANSWER_PRIOR_READS = 40                      # the pretend normal reads blended into every answer's record
LAYER_VOLUME_GRID = [round(0.05 * i, 2) for i in range(31)]   # 0.0 .. 1.5
MIN_TRAIN_ROWS = 10                          # fewer graded rows than this: no pushes, and the scorer is asleep
MIN_SCORED_READS_FOR_VOLUME = 20             # a layer volume is only chosen once this many walk-forward reads were scored
VOLUME_SEARCH_PASSES = 2                     # coordinate passes over the layers when choosing volumes
PROB_CLIP = 0.005                            # the same clip the matcher and corrected JEV use


def logit(p: float) -> float:
    p = min(max(p, PROB_CLIP), 1 - PROB_CLIP)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def stage_target(outcome: str, stage: str) -> int | None:
    """1 / 0 for the stage's question, None when the row does not belong to the stage (flat rows for direction)."""
    if stage == "move":
        return 0 if outcome == "flat" else 1
    if outcome == "flat":
        return None
    return 1 if outcome == "up" else 0


def stage_odds(probs: dict, stage: str) -> float:
    """The historical odds' own answer to the stage's question."""
    p = floored(probs)
    if stage == "move":
        return 1 - p["flat"]
    return p["up"] / (p["up"] + p["down"])


@dataclass
class AdditiveScorerFit:
    pushes: dict[str, dict[str, dict[str, float]]]           # stage -> column -> label -> push
    answer_normal_odds: dict[str, dict[str, dict[str, float]]]   # stage -> column -> label -> that answer's own normal
    layer_volume: dict[str, dict[int, float]]                # stage -> layer -> volume
    columns: dict[str, dict]                                 # column -> {"layer", "group"}
    rows_used: int = 0
    max_day_used: str | None = None
    notes: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"pushes": self.pushes, "answer_normal_odds": self.answer_normal_odds,
                "layer_volume": {s: {str(k): v for k, v in d.items()} for s, d in self.layer_volume.items()},
                "columns": self.columns, "rows_used": self.rows_used, "max_day_used": self.max_day_used, "notes": self.notes}

    @classmethod
    def from_json(cls, d: dict) -> "AdditiveScorerFit":
        return cls(pushes=d["pushes"], answer_normal_odds=d["answer_normal_odds"],
                   layer_volume={s: {int(k): float(v) for k, v in dd.items()} for s, dd in d["layer_volume"].items()},
                   columns=d["columns"], rows_used=int(d.get("rows_used", 0)), max_day_used=d.get("max_day_used"), notes=d.get("notes", {}))


# ---------------------------------------------------------------- pushes

def fit_pushes(rows, stage: str) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """(pushes, answer_normal_odds) for one stage from trainable rows: column -> label -> value."""
    hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    odds_sum: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        target = stage_target(row.outcome, stage)
        if target is None:
            continue
        own = stage_odds(row.historical_odds_probs, stage)
        for col, label in row.answers.items():
            if label is None:
                continue
            seen[col][label] += 1
            hits[col][label] += target
            odds_sum[col][label] += own
    pushes: dict[str, dict[str, float]] = {}
    normals: dict[str, dict[str, float]] = {}
    for col, labels in seen.items():
        pushes[col], normals[col] = {}, {}
        for label, n in labels.items():
            normal = odds_sum[col][label] / n
            blend = (hits[col][label] + ANSWER_PRIOR_READS * normal) / (n + ANSWER_PRIOR_READS)
            normals[col][label] = normal
            pushes[col][label] = logit(blend) - logit(normal)
    return pushes, normals


def layer_sums(row, pushes: dict[str, dict[str, float]], columns: dict[str, dict]) -> dict[int, float]:
    """Per layer, the sum of the layer's group votes for this row (a group's vote = mean push of its answered members)."""
    votes: dict[tuple[int, str], list[float]] = defaultdict(list)
    for col, label in row.answers.items():
        if label is None:
            continue
        push = pushes.get(col, {}).get(label)
        if push is None:
            continue
        meta = columns.get(col)
        if meta is None:
            continue
        votes[(int(meta["layer"]), meta["group"])].append(push)
    out: dict[int, float] = defaultdict(float)
    for (layer, _group), members in votes.items():
        out[layer] += sum(members) / len(members)
    return dict(out)


def stage_probability(row, pushes: dict, columns: dict, volume: dict[int, float], stage: str) -> float:
    total = logit(stage_odds(row.historical_odds_probs, stage))
    for layer, s in layer_sums(row, pushes, columns).items():
        total += volume.get(layer, 0.0) * s
    return sigmoid(total)


def combine_stages(p_move: float, p_up_given_move: float) -> dict[str, float]:
    up = p_move * p_up_given_move
    return floored({"up": up, "down": p_move - up, "flat": 1 - p_move})


# ---------------------------------------------------------------- choosing the layer volumes

def choose_layer_volumes(rows, columns: dict[str, dict], stage: str, layers: list[int]) -> tuple[dict[int, float], dict]:
    """Inner walk-forward by day: for each day, pushes from the days before it; the volumes with the lowest mean penalty over
    all scored reads win. Coordinate search over the layers, VOLUME_SEARCH_PASSES passes. Returns (volumes, notes)."""
    days = sorted({r.day for r in rows})
    folds = []                               # (row, layer_sums for that row, stage target, own odds)
    for day in days:
        train = [r for r in rows if r.day < day]
        if len(train) < MIN_TRAIN_ROWS:
            continue
        pushes, _ = fit_pushes(train, stage)
        for r in rows:
            if r.day != day:
                continue
            target = stage_target(r.outcome, stage)
            if target is None:
                continue
            folds.append((layer_sums(r, pushes, columns), target, logit(stage_odds(r.historical_odds_probs, stage))))
    volume = {layer: 0.0 for layer in layers}
    if len(folds) < MIN_SCORED_READS_FOR_VOLUME:
        return volume, {"scored_reads": len(folds), "why": f"fewer than {MIN_SCORED_READS_FOR_VOLUME} scored reads: volumes stay 0"}

    def mean_penalty(vol: dict[int, float]) -> float:
        total = 0.0
        for sums, target, odds_logit in folds:
            p = sigmoid(odds_logit + sum(vol.get(layer, 0.0) * s for layer, s in sums.items()))
            p = min(max(p, 0.02), 0.98)
            total += -math.log(p if target == 1 else 1 - p)
        return total / len(folds)

    best = mean_penalty(volume)
    for _ in range(VOLUME_SEARCH_PASSES):
        for layer in layers:
            for v in LAYER_VOLUME_GRID:
                trial = {**volume, layer: v}
                pen = mean_penalty(trial)
                if pen < best - 1e-12:
                    best, volume = pen, trial
    return volume, {"scored_reads": len(folds), "mean_penalty": round(best, 5), "mean_penalty_at_zero": round(mean_penalty({layer: 0.0 for layer in layers}), 5)}


# ---------------------------------------------------------------- public

def fit_additive_scorer(matrix) -> AdditiveScorerFit:
    """Fit on the matrix's trainable rows (graded, not excluded). With too few rows the fit is empty and the scorer is asleep."""
    rows = matrix.trainable_rows()
    columns = {c: {"layer": int(m["layer"]), "group": m["group"]} for c, m in matrix.columns.items()}
    layers = sorted({m["layer"] for m in columns.values()})
    fit = AdditiveScorerFit(pushes={}, answer_normal_odds={}, layer_volume={s: {layer: 0.0 for layer in layers} for s in STAGES},
                            columns=columns, rows_used=len(rows), max_day_used=max((r.day for r in rows), default=None))
    if len(rows) < MIN_TRAIN_ROWS:
        fit.notes["why"] = f"only {len(rows)} trainable rows; the scorer is asleep until {MIN_TRAIN_ROWS}"
        return fit
    for stage in STAGES:
        fit.pushes[stage], fit.answer_normal_odds[stage] = fit_pushes(rows, stage)
        fit.layer_volume[stage], fit.notes[f"{stage}_volume_search"] = choose_layer_volumes(rows, columns, stage, layers)
    return fit


def forecast_with_additive_scorer(fit: AdditiveScorerFit, row) -> dict[str, float] | None:
    """{up, flat, down} for one row; None while the scorer is asleep (no pushes fitted yet, so it would only echo the
    historical odds and double their weight in pool_v2) or when the row has no historical odds to start from."""
    if row.historical_odds_probs is None or not fit.pushes:
        return None
    p_move = stage_probability(row, fit.pushes.get("move", {}), fit.columns, fit.layer_volume.get("move", {}), "move")
    p_up = stage_probability(row, fit.pushes.get("direction", {}), fit.columns, fit.layer_volume.get("direction", {}), "direction")
    return combine_stages(p_move, p_up)
