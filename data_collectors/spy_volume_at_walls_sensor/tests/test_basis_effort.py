"""Unit coverage for the ruler (basis.py) + the effort meter (effort.py)."""
import pytest

from conftest import at, make_bars
from siege.basis import bars_by_minute, close_at, derive_ratio, minute_of_day
from siege.contracts import BASIS_SAME_DAY, BASIS_TRAILING
from siege.effort import BaselineStore, percentile, window_effort


def _store_with_days(cfg, n, vol=1000, minutes=range(570, 960)):
    days = {f"2026-06-{d:02d}": {str(m): vol for m in minutes}
            for d in range(1, n + 1)}
    return BaselineStore({"days": days}, cfg)


def test_minute_of_day_reads_et_offsets():
    assert minute_of_day("2026-07-07T10:30:00-04:00") == 630
    assert minute_of_day(at(9, 31)) == 571
    assert minute_of_day("not a timestamp") is None


def test_bars_by_minute_drops_unreadable_ts():
    bars = make_bars(572)
    bars.append({"ts": "garbage", "close": 1.0, "volume": 1})
    assert sorted(bars_by_minute(bars)) == [570, 571, 572]


def test_derive_ratio_time_matched():
    bars = make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0)
    assert derive_ratio(6398.0, bars, at(10, 0), 3) == pytest.approx(10.0)


def test_derive_ratio_none_when_no_matched_bar():
    stale = make_bars(594)                      # 6 min old at 10:00
    assert derive_ratio(6398.0, stale, at(10, 0), 3) is None
    assert derive_ratio(6398.0, [], at(10, 0), 3) is None


def test_close_at_never_reaches_forward():
    by_min = bars_by_minute(make_bars(638))
    close, m = close_at(by_min, 640, 3)         # nearest EARLIER within tol
    assert m == 638 and close == 636.0
    assert close_at(bars_by_minute(make_bars(635)), 640, 3) == (None, None)


def test_percentile_is_share_at_or_below():
    assert percentile(5.0, [1.0, 5.0, 10.0, 20.0]) == 50.0


def test_baseline_maturity_labels(cfg):
    assert _store_with_days(cfg, 0).label("2026-07-07") == "cold"
    assert _store_with_days(cfg, 5).label("2026-07-07") == "warming"
    assert _store_with_days(cfg, 21).label("2026-07-07") == "robust"


def test_baseline_today_never_grades_itself(cfg):
    store = _store_with_days(cfg, 21)
    store.fold_today("2026-07-07", {570: 1})
    assert store.days_accrued("2026-07-07") == 21


def test_window_sums_skip_sparse_days(cfg):
    minutes = list(range(590, 611))
    store = _store_with_days(cfg, 1)
    store.days["2026-06-30"] = {str(m): 1000 for m in range(590, 595)}  # 5/21
    assert store.window_sums("2026-07-07", minutes) == [21000.0]


def test_window_effort_basis_robust_vs_fallback(cfg):
    minutes = list(range(590, 611))
    vols = {m: 1000 for m in range(570, 615)}
    robust = window_effort(_store_with_days(cfg, 21), "2026-07-07",
                           minutes, vols, cfg)
    assert robust["effort_basis"] == BASIS_TRAILING
    assert robust["baseline_med"] == 21000.0
    cold = window_effort(_store_with_days(cfg, 3), "2026-07-07",
                         minutes, vols, cfg)
    assert cold["effort_basis"] == BASIS_SAME_DAY
