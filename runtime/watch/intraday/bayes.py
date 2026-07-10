"""R8 - turn wins and losses into a smoothed confidence number (a posterior).

Plain English:
A naive hit-rate (wins / total) lies when the sample is small - one lucky win
reads as "100% reliable". To stay honest we start every pattern at a neutral
prior (50%) worth a handful of imaginary observations (the pseudocount), then
let real wins/losses pull the number away from 50% as evidence accumulates.

    posterior = (hits + alpha) / (hits + misses + alpha + beta)

where alpha = prior * pseudocount and beta = (1 - prior) * pseudocount.

Worked example: 2 hits, 0 misses, prior 0.5, pseudocount 10
    alpha = 5, beta = 5  ->  (2 + 5) / (2 + 0 + 5 + 5) = 0.58   (not 1.00)

The same update rule serves two stores (R8 "one function, two stores"):
signal posteriors in {TICKER}_memory.json and Right Eye source posteriors. Old
evidence decays so the routine keeps adapting (see `decay_counts`).

This module is pure arithmetic - no I/O, no dependencies - so it is trivially
testable and reusable everywhere a learned weight is needed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict

from . import settings


@dataclass
class Posterior:
    """A learned hit-rate plus the evidence behind it.

    `hits` and `misses` are kept as floats because decay multiplies them by a
    fraction over time (so they are no longer whole counts).
    """
    hits: float = 0.0
    misses: float = 0.0
    # Per-regime breakdown so the same signal can carry a different confidence
    # in calm vs stress markets (R8 context-conditioning).
    by_regime: Dict[str, "Posterior"] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "hits": round(self.hits, 4),
            "misses": round(self.misses, 4),
            "by_regime": {k: v.to_dict() for k, v in self.by_regime.items()},
        }
        return d

    @staticmethod
    def from_dict(d: dict) -> "Posterior":
        p = Posterior(hits=float(d.get("hits", 0.0)), misses=float(d.get("misses", 0.0)))
        for label, sub in (d.get("by_regime") or {}).items():
            p.by_regime[label] = Posterior.from_dict(sub)
        return p


def score(hits: float, misses: float,
          prior: float = None, pseudocount: float = None) -> float:
    """The core formula: smoothed win-rate from raw hit/miss evidence.

    prior/pseudocount default to the values in learning-settings.json.
    """
    if prior is None:
        prior = settings.bayes_prior()
    if pseudocount is None:
        pseudocount = settings.bayes_pseudocount()
    alpha = prior * pseudocount
    beta = (1.0 - prior) * pseudocount
    denom = hits + misses + alpha + beta
    if denom <= 0:
        return prior
    return (hits + alpha) / denom


def update(p: Posterior, won: bool, regime: str = None,
           weight: float = 1.0) -> Posterior:
    """Record one resolved outcome into the overall tally plus the supplied
    regime context dimension.

    `weight` is how much evidence this single outcome carries (default 1.0, the
    legacy one-trade-one-vote behaviour). Callers that know the trade's realized
    payoff pass a magnitude here so a +200% win moves the posterior more than a
    +5% scrape and a -100% wipeout more than a -3% scratch — expectancy, not just
    frequency. Always non-negative; a 0 weight is a no-op.

    Returns the same Posterior (mutated) for convenient chaining.
    """
    inc = max(0.0, float(weight))
    if won:
        p.hits += inc
    else:
        p.misses += inc
    for label, store in ((regime, p.by_regime),):
        if label:
            sub = store.setdefault(label, Posterior())
            if won:
                sub.hits += inc
            else:
                sub.misses += inc
    return p


