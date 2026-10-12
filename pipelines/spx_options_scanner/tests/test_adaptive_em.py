"""Tests for lefteye_adaptive_em — the asymmetric expected move (SHADOW, em_v:1).

Pure math, no network. Covers the em_v:1 formula (total width preserved at
2·em; d = 0.5 reproduces the symmetric ruler), the source ladder (this scan's
tower call → a prior still-live call → the semivariance split), the honesty
rules (non-bracketing tower band falls through; thin/degraded tape proves
nothing; missing EM or missing every source → absent record), and the
judged-stand-down release on the prior-call walk."""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lefteye_adaptive_em as AE  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 24, 13, 0, tzinfo=ET)
SPOT = 5000.0
RR = {"em_points": 40.0, "quality": "ok"}
SPEED = {"semivar_down_share": 0.5, "quality": "ok"}


def _wt(fired=True, rng=(-0.5, 1.5), horizon=60):
    return {"fired": fired, "range_sigma": list(rng) if rng else None,
            "horizon_min": horizon, "votes": {"n": 3}}


def _row(wt, minutes_ago=10, ticker="SPX"):
    return {"ticker": ticker, "ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "watchtower": wt}


def test_live_call_splits_the_budget_by_the_band():
    # [-0.5, +1.5]: down share 0.25 → down = 2·40·0.25 = 20, up = 60
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW, watchtower=_wt())
    assert r["asym_source"] == "watchtower" and r["em_v"] == 1
    assert r["down_share"] == 0.25
    assert r["em_down_pts"] == 20.0 and r["em_up_pts"] == 60.0
    # total width preserved: asymmetry reallocates the priced budget, never adds
    assert r["em_down_pts"] + r["em_up_pts"] == 2 * RR["em_points"]
    assert r["em_down_pct"] == round(20.0 / SPOT, 5)
    assert r["em_up_pct"] == round(60.0 / SPOT, 5)
    assert r["em_points"] == 40.0 and r["em_quality"] == "ok"


def test_symmetric_semivar_reproduces_the_ruler():
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW, speedometer=SPEED)
    assert r["asym_source"] == "speedometer" and r["down_share"] == 0.5
    assert r["em_down_pts"] == r["em_up_pts"] == RR["em_points"]


def test_semivar_split_goes_through_the_square_root():
    # s = 0.64 of VARIANCE from down moves → excursion split √0.64 : √0.36 = 0.8 : 0.6
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW,
                speedometer={"semivar_down_share": 0.64, "quality": "ok"})
    assert r["down_share"] == round(0.8 / 1.4, 4)
    assert abs(r["em_down_pts"] - 2 * 40.0 * (0.8 / 1.4)) < 0.01


def test_non_bracketing_band_falls_through_never_clamped():
    # both bounds above spot = a drift forecast, not a range split
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW,
                watchtower=_wt(rng=(0.2, 0.8)), speedometer=SPEED)
    assert r["asym_source"] == "speedometer"
    # ...and with no fallback the record is honestly ABSENT
    assert AE.read(RR, SPOT, ticker="SPX", now=NOW,
                   watchtower=_wt(rng=(0.2, 0.8))) is None


def test_unfired_or_bandless_tower_is_not_a_source():
    assert AE.read(RR, SPOT, ticker="SPX", now=NOW,
                   watchtower=_wt(fired=False)) is None
    assert AE.read(RR, SPOT, ticker="SPX", now=NOW,
                   watchtower=_wt(rng=None)) is None
    assert AE.read(RR, SPOT, ticker="SPX", now=NOW,
                   watchtower=_wt(rng=(0.0, 0.0))) is None    # zero-width band


def test_prior_call_still_inside_its_horizon_is_live():
    rows = [_row(_wt(horizon=60), minutes_ago=30)]
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW, rows_today=rows)
    assert r["asym_source"] == "watchtower" and r["down_share"] == 0.25


def test_expired_or_stood_down_prior_call_releases_to_semivar():
    expired = [_row(_wt(horizon=20), minutes_ago=30)]
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW, rows_today=expired,
                speedometer=SPEED)
    assert r["asym_source"] == "speedometer"
    # a JUDGED stand-down (votes recorded, fired False) releases the call —
    # the older fired row behind it must never be resurrected
    stood_down = [_row(_wt(horizon=120), minutes_ago=40),
                  _row(_wt(fired=False), minutes_ago=5)]
    r2 = AE.read(RR, SPOT, ticker="SPX", now=NOW, rows_today=stood_down,
                 speedometer=SPEED)
    assert r2["asym_source"] == "speedometer"


def test_skipped_rows_and_foreign_tickers_are_transparent():
    rows = [_row(_wt(horizon=60), minutes_ago=30),
            _row({"skipped": "quiet"}, minutes_ago=5),            # not a verdict
            _row(_wt(fired=False), minutes_ago=2, ticker="QQQ")]  # not our tape
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW, rows_today=rows)
    assert r["asym_source"] == "watchtower"


def test_thin_or_degraded_tape_proves_nothing_cold_still_counts():
    for q in ("thin", "degraded"):
        assert AE.read(RR, SPOT, ticker="SPX", now=NOW,
                       speedometer={"semivar_down_share": 0.7, "quality": q}) is None
    # "cold" only lacks the baseline table, which the semivariance never touches
    r = AE.read(RR, SPOT, ticker="SPX", now=NOW,
                speedometer={"semivar_down_share": 0.7, "quality": "cold"})
    assert r is not None and r["asym_source"] == "speedometer"


def test_missing_em_or_spot_is_an_absent_record():
    assert AE.read(None, SPOT, ticker="SPX", now=NOW, watchtower=_wt()) is None
    assert AE.read({"em_points": None}, SPOT, ticker="SPX", now=NOW,
                   watchtower=_wt()) is None
    assert AE.read({"em_points": -3.0}, SPOT, ticker="SPX", now=NOW,
                   watchtower=_wt()) is None
    assert AE.read(RR, None, ticker="SPX", now=NOW, watchtower=_wt()) is None
