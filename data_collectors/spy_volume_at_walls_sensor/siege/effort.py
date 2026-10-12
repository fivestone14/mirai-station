"""
effort.py — volume EFFORT measurement + the minute-volume normalcy memory.

THE REVERSED SIGN (the finding this whole box exists for — see contracts.py):
HIGH effort at a wall touch predicts BREAK (held 39% vs 54% quiet, n=168);
QUIET predicts HOLD. effort.py only produces the percentile; it never names
the verdict — but every consumer of effort_pct inherits that doctrine.

effort_pct = percentile of the touch window's SPY volume against the same
clock-window's sums on trailing days (the backtest's clock-volume
distribution). Before the baseline is ROBUST (21 accrued prior days) it
falls back to the current day's own minute-volume distribution; which basis
produced the number is recorded on every row (effort_basis) — a grader must
never mix the two silently.

The baseline accrues day by day: each healthy scan folds today's RTH minute
volumes over today's entry, so a finished session is complete without a
nightly job. Only PRIOR days count as trailing history.
"""
from __future__ import annotations

import statistics
from typing import Optional

from siege.contracts import (BASIS_SAME_DAY, BASIS_TRAILING, LABEL_COLD,
                             LABEL_ROBUST, LABEL_WARMING, SiegeConfig)


def percentile(x: float, ref: list[float]) -> float:
    """Empirical percentile: share of the reference at or below x."""
    return 100.0 * sum(1 for v in ref if v <= x) / len(ref)


class BaselineStore:
    """Minute-volume memory: {"days": {"YYYY-MM-DD": {"<minute>": vol}}}.
    Maturity labels count accrued PRIOR days (today never grades itself)."""

    def __init__(self, data: Optional[dict], cfg: SiegeConfig):
        self.cfg = cfg
        self.days: dict = (data or {}).get("days") or {}

    def data(self) -> dict:
        return {"days": self.days}

    def fold_today(self, session: str, minute_vols: dict[int, float]) -> None:
        """Overwrite today's entry with the session so far; prune oldest past
        the retention cap."""
        self.days[session] = {str(m): int(v) for m, v in minute_vols.items()}
        for day in sorted(self.days)[:-self.cfg.baseline_keep_days]:
            del self.days[day]

    def prior_days(self, session: str) -> list[str]:
        return sorted(d for d in self.days if d != session)

    def days_accrued(self, session: str) -> int:
        return len(self.prior_days(session))

    def label(self, session: str) -> str:
        n = self.days_accrued(session)
        if n >= self.cfg.robust_days:
            return LABEL_ROBUST
        if n >= self.cfg.warming_days:
            return LABEL_WARMING
        return LABEL_COLD

    def window_sums(self, session: str, minutes: list[int]) -> list[float]:
        """Same clock-window volume sums across trailing days. Days covering
        under half the window (early close, feed hole) are skipped — a
        half-empty sum would read as a phantom quiet day."""
        keys = [str(m) for m in minutes]
        out = []
        for day in self.prior_days(session):
            vols = self.days[day]
            if sum(1 for k in keys if k in vols) < max(1, len(keys) // 2):
                continue
            out.append(float(sum(vols.get(k, 0) for k in keys)))
        return out


def window_effort(baseline: BaselineStore, session: str, minutes: list[int],
                  minute_vols: dict[int, float], cfg: SiegeConfig) -> Optional[dict]:
    """The effort read for one closed touch window. None when there is
    nothing to measure against (no window bars or an empty day)."""
    present = [m for m in minutes if m in minute_vols]
    if not present or not minute_vols:
        return None
    window_vol = float(sum(minute_vols.get(m, 0) for m in minutes))
    sums = baseline.window_sums(session, minutes)
    med = round(statistics.median(sums), 1) if sums else None
    if baseline.days_accrued(session) >= cfg.robust_days and sums:
        pct, basis = percentile(window_vol, sums), BASIS_TRAILING
    else:
        # pre-robust fallback: the window's per-minute rate against today's
        # own minute-volume distribution (units differ from the trailing
        # basis — effort_basis on the row is what keeps graders honest)
        pct = percentile(window_vol / len(present), list(minute_vols.values()))
        basis = BASIS_SAME_DAY
    return {"window_vol": window_vol, "effort_pct": round(pct, 1),
            "effort_basis": basis, "baseline_med": med}
