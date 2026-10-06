"""The matcher: the past reads most like today, tallied by how far each beat its own time-of-day odds.

    compare    per column: 0 when both reads gave the same answer, 1 when they differ, 0.5 when either is silent;
               the mismatch is the weighted mean over the columns (equal weights until the scorer's weights are trusted)
    keep       the MATCHER_NEIGHBOR_COUNT closest past reads, from any earlier day; ties go to the newer read
    fade       an older read counts less: 0.5 ** (reads since it / MATCHER_HALF_LIFE_READS)
    tally      RESIDUAL form, so a midday read is not skewed by look-alikes from the open:
                   p_b = odds_today_b + sum fade_r ([outcome_r = b] - odds_r_b) / (sum fade_r + MATCHER_PRIOR_READS)
               then clipped and renormalised; MATCHER_PRIOR_READS pretend reads pull the tally toward today's odds.

The candidates are the answer matrix's trainable rows (the matrix is saved beside the fits each night), so the fit itself
holds only the column weights: nothing is copied into a file twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..scores import OUTCOMES, floored

MATCHER_NEIGHBOR_COUNT = 20
MATCHER_PRIOR_READS = 10
MATCHER_HALF_LIFE_READS = 300
MIN_SHARED_COLUMNS = 5           # a past read sharing fewer answered columns than this with today is not a candidate
PROB_CLIP = 0.005


@dataclass
class MatcherFit:
    column_weights: dict[str, float]                  # column -> weight (1.0 each for now)
    rows_used: int = 0
    max_day_used: str | None = None
    notes: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"column_weights": self.column_weights, "rows_used": self.rows_used, "max_day_used": self.max_day_used, "notes": self.notes}

    @classmethod
    def from_json(cls, d: dict) -> "MatcherFit":
        return cls(column_weights=d["column_weights"], rows_used=int(d.get("rows_used", 0)), max_day_used=d.get("max_day_used"),
                   notes=d.get("notes", {}))


def fit_matcher(matrix, column_weights: dict[str, float] | None = None) -> MatcherFit:
    """The matcher's fit is its column weights (equal for now; later the scorer's). The candidates stay in the matrix."""
    rows = matrix.trainable_rows()
    weights = column_weights or {c: 1.0 for c in matrix.columns}
    return MatcherFit(column_weights=weights, rows_used=len(rows), max_day_used=max((r.day for r in rows), default=None))


def mismatch(today: dict[str, str | None], past: dict[str, str | None], weights: dict[str, float]) -> tuple[float, int]:
    """(weighted mean mismatch over the weighted columns, number of columns both answered)."""
    total, weight_sum, shared = 0.0, 0.0, 0
    for col, w in weights.items():
        if w <= 0:
            continue
        a, b = today.get(col), past.get(col)
        if a is None or b is None:
            d = 0.5
        elif a == b:
            d, shared = 0.0, shared + 1
        else:
            d, shared = 1.0, shared + 1
        total += w * d
        weight_sum += w
    return (total / weight_sum if weight_sum else 1.0), shared


def forecast_with_matcher(fit: MatcherFit, row, candidates) -> tuple[dict[str, float] | None, dict]:
    """({up, flat, down}, diagnostics) for one row against ``candidates`` (the matrix's trainable rows, oldest first);
    (None, why) when there is nothing to compare against. Only reads from earlier days are compared."""
    if row.historical_odds_probs is None:
        return None, {"why": "no historical odds for this read"}
    earlier = [p for p in sorted(candidates, key=lambda r: r.row_ts) if p.day < row.day]
    if not earlier:
        return None, {"why": "no graded reads from earlier days"}
    required = min(MIN_SHARED_COLUMNS, max(1, len(fit.column_weights) // 2))   # a small table cannot share five columns
    scored = []
    for position, past in enumerate(earlier):
        distance, shared = mismatch(row.answers, past.answers, fit.column_weights)
        if shared >= required:
            scored.append((distance, -position, past))
    if not scored:
        return None, {"why": f"no earlier read shares {required} answered columns with this one"}
    scored.sort(key=lambda t: (t[0], t[1]))
    kept = scored[:MATCHER_NEIGHBOR_COUNT]
    total = len(earlier)
    odds_today = floored(row.historical_odds_probs)
    fade_sum, residual = 0.0, {k: 0.0 for k in OUTCOMES}
    for distance, negative_position, past in kept:
        reads_since = total - (-negative_position)
        fade = 0.5 ** (reads_since / MATCHER_HALF_LIFE_READS)
        fade_sum += fade
        odds_then = floored(past.historical_odds_probs)
        for k in OUTCOMES:
            residual[k] += fade * ((1.0 if past.outcome == k else 0.0) - odds_then[k])
    probs = {k: odds_today[k] + residual[k] / (fade_sum + MATCHER_PRIOR_READS) for k in OUTCOMES}
    probs = {k: min(max(v, PROB_CLIP), 1 - PROB_CLIP) for k, v in probs.items()}
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    diagnostics = {"nearest_mismatch": round(kept[0][0], 4), "mean_mismatch": round(sum(t[0] for t in kept) / len(kept), 4),
                   "kept_count": len(kept), "effective_reads": round(fade_sum, 3), "candidates": total}
    return probs, diagnostics
