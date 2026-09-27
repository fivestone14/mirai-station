"""Scores for a three-way forecast of where price ends a horizon: up, flat or down.

Every forecast the learning loop reads (pool.py) and the baseline it is measured against
(baseline.py) is scored here, one way:

    floored(p)       each of up, flat and down at least EPS, then renormalised, so the worst single
                     read costs ln(1 / EPS), 3.9 nats; unsure mass is spread by the caller first
    move_of(p)       m = 1 - p(flat), the chance of a move
    direction_of(p)  d = p(up) / (p(up) + p(down)), the chance it is up, given a move
    losses(p, y)     (move loss, direction loss): -ln m if it moved, else -ln(1 - m); -ln d if up,
                     -ln(1 - d) if down, 0 if flat. Their sum is the three-way log loss -ln p(y)
                     exactly, so being right about the quiet windows (most of them) scores as move
                     skill and can never pass for skill at calling the direction.
"""
from __future__ import annotations

import math

OUTCOMES = ("up", "flat", "down")
EPS = 0.02


def floored(p: dict) -> dict[str, float]:
    """``p`` over up, flat and down only, each at least EPS, summing to one. A forecast with no mass on
    any of them is uniform."""
    raw = {k: max(float(p.get(k) or 0.0), 0.0) for k in OUTCOMES}
    total = sum(raw.values())
    base = {k: raw[k] / total for k in OUTCOMES} if total > 0 else {k: 1.0 / 3 for k in OUTCOMES}
    lifted = {k: max(v, EPS) for k, v in base.items()}
    s = sum(lifted.values())
    return {k: v / s for k, v in lifted.items()}


def move_of(p: dict) -> float:
    return 1.0 - p["flat"]


def direction_of(p: dict) -> float:
    return p["up"] / (p["up"] + p["down"])


def from_move_direction(m: float, d: float) -> dict[str, float]:
    """The three-way forecast with chance of a move ``m`` and chance up given a move ``d``."""
    return {"up": m * d, "flat": 1.0 - m, "down": m * (1.0 - d)}


def losses(p: dict, y: str) -> tuple[float, float]:
    """(move loss, direction loss) of the floored forecast ``p`` for outcome ``y``."""
    m, d = move_of(p), direction_of(p)
    move = -math.log(1.0 - m) if y == "flat" else -math.log(m)
    direction = 0.0 if y == "flat" else -math.log(d) if y == "up" else -math.log(1.0 - d)
    return move, direction


def log_loss(p: dict, y: str) -> float:
    return -math.log(p[y])
