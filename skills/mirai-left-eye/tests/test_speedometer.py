"""Tests for lefteye_speedometer — THE SPEEDOMETER (RV nowcast, derived layer).

Pure logic, no network, no real state dir: bars are synthetic GBM paths at
fixed seeds, the baseline is injected as a dict, and the loader test
monkeypatches _bars_dir + _read_json onto a tmp dir."""
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_speedometer as spd  # noqa: E402

ET = ZoneInfo("America/New_York")

SIGMA = 5e-4      # per-minute log-return sigma ≈ 1% daily vol
START = 7500.0    # SPX-ish level
BASE_DAYS = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]
TODAY = "2026-07-06"
NOON = datetime(2026, 7, 6, 12, 0, tzinfo=ET)


# ------------------------------------------------------------------ builders
def gbm_returns(n, seed, sigma=SIGMA, drift=0.0):
    """n Gaussian 1-min log-returns at a fixed seed (deterministic)."""
    rng = random.Random(seed)
    return [rng.gauss(drift, sigma) for _ in range(n)]


def bars_from_returns(day, rets, start_price=START):
    """Compound a returns list into 1-min bars from 09:30 ET — one bar per
    return, close-to-close, so injecting rets[k] injects EXACTLY one return."""
    t0 = datetime.fromisoformat(f"{day}T09:30:00").replace(tzinfo=ET)
    price, bars = start_price, []
    for i, r in enumerate(rets):
        o, price = price, price * math.exp(r)
        bars.append({"ts": (t0 + timedelta(minutes=i)).isoformat(),
                     "open": o, "high": max(o, price), "low": min(o, price),
                     "close": price, "volume": 100.0})
    return bars


def make_baseline(sigma=SIGMA, n_bars=390):
    """5 full constant-vol days, one seed per day."""
    return {day: bars_from_returns(day, gbm_returns(n_bars, seed=100 + i, sigma=sigma))
            for i, day in enumerate(BASE_DAYS)}


def today_bars(n=150, seed=100, scale=1.0, mutate=None):
    """Today truncated at 12:00 (150 bars, 09:30-11:59). seed=100 makes it
    IDENTICAL to the first baseline day; mutate edits the returns list."""
    rets = [scale * r for r in gbm_returns(390, seed=seed)[:n]]
    if mutate:
        mutate(rets)
    return bars_from_returns(TODAY, rets)


# --------------------------------------------------------------------- tests
def test_quiet_tape_pace_near_one():
    out = spd.read(today_bars(), now=NOON, baseline=make_baseline())
    assert out["quality"] == "ok"
    assert out["baseline_days"] == 5
    assert 0.85 <= out["rv_pace"] <= 1.15          # same tape as the norm
    assert out["jump_z"] < 3
    assert out["jump_sign"] is None
    assert 0.4 <= out["semivar_down_share"] <= 0.6  # symmetric Gaussian tape
    assert out["n_bars"] == 150


def test_hot_tape_pace_and_rod_scale():
    base = make_baseline()
    quiet = spd.read(today_bars(), now=NOON, baseline=base)
    hot = spd.read(today_bars(scale=2.0), now=NOON, baseline=base)
    assert 3.4 <= hot["rv_pace"] <= 4.6            # 2x returns → 4x variance
    # 4x projected variance → 2x remaining sigma → ~2x rest-of-day range
    assert 1.8 <= hot["rod_range_pts"] / quiet["rod_range_pts"] <= 2.2


def test_jump_detected_with_sign():
    # a single 8σ minute must be a real SHARE of spent RV to trip the BNS
    # ratio statistic — so test it mid-morning (60 bars ≈ 10:30), where it is;
    # by 12:00 the same minute is only ~30% of RV and z sits near 2 (that
    # dilution is a property of the statistic, not a bug)
    base = make_baseline()

    def down(rets):
        rets[45] = -8 * SIGMA                       # one -8σ minute at 10:15

    def up(rets):
        rets[45] = 8 * SIGMA

    dn = spd.read(today_bars(n=60, mutate=down), now=NOON, baseline=base)
    assert dn["jump_z"] >= 3 and dn["jump_sign"] == "down"
    upp = spd.read(today_bars(n=60, mutate=up), now=NOON, baseline=base)
    assert upp["jump_z"] >= 3 and upp["jump_sign"] == "up"


def test_smooth_gbm_no_jump():
    out = spd.read(today_bars(), now=NOON, baseline=make_baseline())
    assert out["jump_z"] is not None and out["jump_z"] < 3
    assert out["jump_sign"] is None


def test_drift_down_day_semivariance():
    def heavier_down(rets):                         # down-moves twice as large
        rets[:] = [r if r > 0 else 2.0 * r for r in rets]

    out = spd.read(today_bars(mutate=heavier_down), now=NOON, baseline=make_baseline())
    assert out["semivar_down_share"] > 0.6


def test_cold_start_no_baseline():
    out = spd.read(today_bars(), now=NOON, baseline={})
    assert out["quality"] == "cold"
    assert out["baseline_days"] == 0
    assert out["rv_pace"] is None and out["rod_range_pts"] is None
    # same-day fields still populated — cold means no HISTORY, not no tape
    assert out["rv_day_pts"] > 0
    assert out["jump_z"] is not None
    assert out["semivar_down_share"] is not None
    assert out["n_bars"] == 150


def test_thin_open():
    out = spd.read(today_bars(n=6), now=NOON, baseline=make_baseline())
    assert out is not None and out["quality"] == "thin"
    assert out["jump_z"] is None and out["jump_sign"] is None
    assert spd.read(today_bars(n=2), now=NOON, baseline=make_baseline()) is None


def test_gaps_and_junk_bars():
    base = make_baseline()
    # one 20-min feed hole + a duplicate ts + a zero-close print: the return
    # spanning the hole is DROPPED (a ~sqrt(21)·σ move squared would fake a
    # burst), the dupe and the dead print never enter
    bars = today_bars()
    holed = bars[:60] + bars[80:]                  # 10:30-10:49 missing
    dupe = dict(holed[10]);  dupe["close"] = holed[10]["close"] * 1.01
    dead = dict(holed[20]);  dead["ts"] = holed[21]["ts"];  dead["close"] = 0.0
    # (dead reuses bar 21's ts with close=0 → dropped as non-positive, and the
    # real bar 21 survives the first-wins dedupe because sort is stable)
    out = spd.read(holed + [dupe, dead], now=NOON, baseline=base)
    assert out["n_bars"] == 130                    # 150 - 20 hole; dupe+dead gone
    assert out["jump_z"] is not None and out["jump_z"] < 3   # no fake jump
    assert out["quality"] == "ok"                  # 1 gap-drop ≤ 5
    # six 3-bar holes (4-min gaps) → 6 gap-dropped returns → degraded
    swiss = [b for i, b in enumerate(bars)
             if not any(s <= i < s + 3 for s in (20, 35, 50, 65, 80, 95))]
    out2 = spd.read(swiss, now=NOON, baseline=base)
    assert out2["quality"] == "degraded"


def test_half_day_excluded_from_baseline():
    base = make_baseline()
    base["2026-06-26"] = bars_from_returns("2026-06-26",
                                           gbm_returns(210, seed=99))  # <300 bars
    out = spd.read(today_bars(), now=NOON, baseline=base)
    assert out["baseline_days"] == 5               # the 210-bar day is dropped
    assert out["quality"] == "ok"


def test_mid_bucket_interpolation_exact_pace():
    # Constant-magnitude alternating returns: every minute spends exactly c² of
    # variance, so the TRUE pace is 1.0 at ANY minute. Full-bucket norms would
    # understate mid-bucket reads (rv 16c² vs cum_med[3] 19c² = 0.842 at 09:46);
    # the intra-bucket interpolation must make pace exactly 1.0 everywhere.
    c = SIGMA

    def flat_day(day):
        return bars_from_returns(day, [c if i % 2 else -c for i in range(390)])

    base = {day: flat_day(day) for day in BASE_DAYS}
    for n in (17, 33, 61, 150, 386):                    # mid- and end-bucket minutes
        rets = [c if i % 2 else -c for i in range(n)]
        out = spd.read(bars_from_returns(TODAY, rets), now=NOON, baseline=base)
        assert out["rv_pace"] == 1.0, f"n={n}: {out['rv_pace']}"


def test_rod_positive_before_last_bar_zero_at_close():
    # 15:55 truncation: share_cum[77] is 1.0 on FULL buckets, which used to
    # project "day over" 4 minutes early (rod 0.0). Interpolated share < 1 must
    # leave a small positive rest-of-day range; the true 15:59 bar closes it.
    base = make_baseline()
    full = today_bars(n=390)
    out_1555 = spd.read(full[:386], now=NOON, baseline=base)   # last bar 15:55
    assert out_1555["rod_range_pts"] > 0
    out_1559 = spd.read(full, now=NOON, baseline=base)         # last bar 15:59
    assert out_1559["rod_range_pts"] == 0.0


def test_loader_exclude_and_mtime_cache(tmp_path, monkeypatch):
    d = tmp_path / "bars"
    d.mkdir()
    for i, day in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"]):
        bars = bars_from_returns(day, gbm_returns(10, seed=200 + i))
        (d / f"{day}-SPX.json").write_text(json.dumps(bars))
    monkeypatch.setattr(spd, "_bars_dir", lambda: d)
    spd._BASELINE_CACHE.clear()
    opens = {"n": 0}
    real_read = spd._read_json

    def counting_read(path):
        opens["n"] += 1
        return real_read(path)

    monkeypatch.setattr(spd, "_read_json", counting_read)
    base = spd._load_baseline("SPX", exclude_day="2026-07-03")
    assert set(base) == {"2026-07-01", "2026-07-02"}   # exclude_day skipped
    assert opens["n"] == 2                             # excluded file never opened
    base2 = spd._load_baseline("SPX", exclude_day="2026-07-03")
    assert opens["n"] == 2                             # mtime cache hit: 0 re-reads
    assert base2 == base
    # a corrupt file is skipped per-file, never raises
    (d / "junk-SPX.json").write_text("{not json")
    base3 = spd._load_baseline("SPX", exclude_day="2026-07-03")
    assert set(base3) == {"2026-07-01", "2026-07-02"}
