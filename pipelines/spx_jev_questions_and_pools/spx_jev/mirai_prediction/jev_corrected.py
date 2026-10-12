"""Corrected JEV: JEV's own call fixed with four fitted numbers, so it stops calling moves too often and leaning down.

    z_b = temperature x ln(jev_b) + historical_odds_weight x ln(odds_b) + offset_b      offsets: flat_offset on flat, down_offset on down
    corrected = softmax(z)

The four numbers are chosen by a coordinate grid search that minimises the mean three-way penalty on the graded rows
(the score the whole system uses: spx_jev.scores). Below MIN_GRADED_ROWS graded rows the voice is asleep: it gives no
forecast, so pool_v2 neither mixes nor updates it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..scores import OUTCOMES, floored, log_loss

MIN_GRADED_ROWS = 30
TEMPERATURE_RANGE = (0.05, 2.0)
HISTORICAL_ODDS_WEIGHT_RANGE = (0.0, 2.0)
OFFSET_RANGE = (-2.0, 2.0)
GRID_STEPS = 21
SEARCH_ROUNDS = 3
PROB_CLIP = 0.005


@dataclass
class JevCorrectedFit:
    temperature: float = 1.0
    flat_offset: float = 0.0
    down_offset: float = 0.0
    historical_odds_weight: float = 0.0
    asleep: bool = True
    rows_used: int = 0
    max_day_used: str | None = None
    notes: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, d: dict) -> "JevCorrectedFit":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def correct_jev_call(jev_probs: dict, odds_probs: dict | None, temperature: float, flat_offset: float, down_offset: float,
                     historical_odds_weight: float) -> dict[str, float]:
    """JEV's call with the four numbers applied: the formula in the module docstring."""
    jev = floored(jev_probs)
    odds = floored(odds_probs) if odds_probs else {k: 1 / 3 for k in OUTCOMES}
    z = {}
    for k in OUTCOMES:
        z[k] = temperature * math.log(min(max(jev[k], PROB_CLIP), 1 - PROB_CLIP)) + historical_odds_weight * math.log(odds[k])
    z["flat"] += flat_offset
    z["down"] += down_offset
    top = max(z.values())
    e = {k: math.exp(v - top) for k, v in z.items()}
    s = sum(e.values())
    return {k: v / s for k, v in e.items()}


def _grid(lo: float, hi: float) -> list[float]:
    return [round(lo + (hi - lo) * i / (GRID_STEPS - 1), 4) for i in range(GRID_STEPS)]


def fit_jev_corrected(matrix) -> JevCorrectedFit:
    rows = [r for r in matrix.trainable_rows() if r.jev_own_probs is not None]
    fit = JevCorrectedFit(rows_used=len(rows), max_day_used=max((r.day for r in rows), default=None))
    if len(rows) < MIN_GRADED_ROWS:
        fit.asleep = True
        fit.notes["why"] = f"only {len(rows)} graded rows with JEV's own call; asleep until {MIN_GRADED_ROWS}"
        return fit

    def penalty(temperature: float, flat_offset: float, down_offset: float, historical_odds_weight: float) -> float:
        return sum(log_loss(correct_jev_call(r.jev_own_probs, r.historical_odds_probs, temperature, flat_offset, down_offset,
                                             historical_odds_weight), r.outcome) for r in rows) / len(rows)

    params = {"temperature": 1.0, "flat_offset": 0.0, "down_offset": 0.0, "historical_odds_weight": 0.0}
    ranges = {"temperature": TEMPERATURE_RANGE, "flat_offset": OFFSET_RANGE, "down_offset": OFFSET_RANGE,
              "historical_odds_weight": HISTORICAL_ODDS_WEIGHT_RANGE}
    before = best = penalty(**params)
    for _ in range(SEARCH_ROUNDS):
        for name, (lo, hi) in ranges.items():
            for v in _grid(lo, hi):
                trial = {**params, name: v}
                pen = penalty(**trial)
                if pen < best - 1e-12:
                    best, params = pen, trial
    fit.temperature, fit.flat_offset = params["temperature"], params["flat_offset"]
    fit.down_offset, fit.historical_odds_weight = params["down_offset"], params["historical_odds_weight"]
    fit.asleep = False
    fit.notes = {"mean_penalty_before": round(before, 5), "mean_penalty_after": round(best, 5)}
    return fit


def forecast_with_jev_corrected(fit: JevCorrectedFit, row) -> dict[str, float] | None:
    """{up, flat, down}, or None while asleep or when the read has no JEV call."""
    if fit.asleep or row.jev_own_probs is None:
        return None
    return correct_jev_call(row.jev_own_probs, row.historical_odds_probs, fit.temperature, fit.flat_offset, fit.down_offset,
                            fit.historical_odds_weight)
