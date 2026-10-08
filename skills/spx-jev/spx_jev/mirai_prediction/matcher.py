"""The matcher: the past reads most like today, tallied by how far each beat its own time-of-day odds.

    compare    per column: 0 when both reads gave the same answer, 1 when they differ, 0.5 when either is silent;
               averaged within each group (the questions JEV was asked together, as the scorer groups them), so a group
               of eight questions on one topic counts once, like a group of one; the mismatch is the weighted mean over
               the groups (equal weights until the scorer's group weights are trusted)
    keep       the MATCHER_NEIGHBOR_COUNT closest past reads, from any earlier day; ties go to the newer read
    fade       an older read counts less: 0.5 ** (reads since it / MATCHER_HALF_LIFE_READS)
    tally      RESIDUAL form, so a midday read is not skewed by look-alikes from the open:
                   p_b = odds_today_b + sum fade_r ([outcome_r = b] - odds_r_b) / (sum fade_r + MATCHER_PRIOR_READS)
               then clipped and renormalised; MATCHER_PRIOR_READS pretend reads pull the tally toward today's odds.

The candidates are the answer matrix's trainable rows (the matrix is saved beside the fits each night), so the fit itself
holds only each column's group and the group weights: nothing is copied into a file twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..scores import OUTCOMES, floored
from .answer_matrix import voting_columns

MATCHER_NEIGHBOR_COUNT = 20
MATCHER_PRIOR_READS = 10
MATCHER_HALF_LIFE_READS = 300
MIN_SHARED_COLUMNS = 5           # a past read sharing fewer answered columns than this with today is not a candidate
PROB_CLIP = 0.005


@dataclass
class MatcherFit:
    group_of_column: dict[str, str]                   # column -> its group (the answer matrix's)
    group_weights: dict[str, float]                   # group -> weight (1.0 each for now; later the scorer's)
    rows_used: int = 0
    max_day_used: str | None = None
    notes: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"group_of_column": self.group_of_column, "group_weights": self.group_weights, "rows_used": self.rows_used,
                "max_day_used": self.max_day_used, "notes": self.notes}

    @classmethod
    def from_json(cls, d: dict) -> "MatcherFit":
        return cls(group_of_column=d["group_of_column"], group_weights=d["group_weights"], rows_used=int(d.get("rows_used", 0)),
                   max_day_used=d.get("max_day_used"), notes=d.get("notes", {}))

    def columns_by_group(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for col, group in self.group_of_column.items():
            out.setdefault(group, []).append(col)
        return out


def fit_matcher(matrix, group_weights: dict[str, float] | None = None) -> MatcherFit:
    """The matcher's fit is each voting column's group and the group weights (equal for now; later the scorer's). The
    candidates stay in the matrix; a column that does not vote is no part of any distance."""
    rows = matrix.trainable_rows()
    group_of_column = {c: m["group"] for c, m in voting_columns(matrix.columns).items()}
    weights = group_weights or {g: 1.0 for g in set(group_of_column.values())}
    return MatcherFit(group_of_column=group_of_column, group_weights=weights, rows_used=len(rows),
                      max_day_used=max((r.day for r in rows), default=None))


def column_distance(a: str | None, b: str | None) -> float:
    """0 when both reads gave the same answer, 1 when they differ, 0.5 when either is silent."""
    if a is None or b is None:
        return 0.5
    return 0.0 if a == b else 1.0


def mismatch(today: dict[str, str | None], past: dict[str, str | None], columns_by_group: dict[str, list[str]],
             group_weights: dict[str, float]) -> tuple[float, int]:
    """(weighted mean over the groups of each group's mean column distance, number of columns both answered)."""
    total, weight_sum, shared = 0.0, 0.0, 0
    for group, cols in columns_by_group.items():
        w = group_weights.get(group, 0.0)
        if w <= 0 or not cols:
            continue
        distances = []
        for col in cols:
            a, b = today.get(col), past.get(col)
            distances.append(column_distance(a, b))
            shared += a is not None and b is not None
        total += w * sum(distances) / len(distances)
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
    required = min(MIN_SHARED_COLUMNS, max(1, len(fit.group_of_column) // 2))   # a small table cannot share five columns
    columns_by_group = fit.columns_by_group()
    scored = []
    for position, past in enumerate(earlier):
        distance, shared = mismatch(row.answers, past.answers, columns_by_group, fit.group_weights)
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
