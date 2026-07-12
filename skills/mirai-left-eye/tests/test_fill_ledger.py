import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_fill_ledger as fl
import lefteye_gex_box as gb

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 3, 10, 0, tzinfo=ET)


def _c(strike, right, vol, dte=0, gamma=0.01):
    return {"strike": strike, "right": right, "volume": vol, "dte": dte,
            "gamma": gamma, "iv": 0.2}


def test_seed_scan_uses_cumulative_volume(tmp_path):
    cs = [_c(755, "call", 4000), _c(745, "put", 100)]
    assert fl.annotate("t1", cs, T0, state_dir=tmp_path)
    assert cs[0]["fill_weight"] == 4000 and cs[1]["fill_weight"] == 100


def test_weight_halves_after_one_halflife(tmp_path):
    fl.annotate("t2", [_c(755, "call", 4000)], T0, state_dir=tmp_path)
    cs = [_c(755, "call", 4000)]                       # no new fills
    fl.annotate("t2", cs, T0 + timedelta(minutes=fl.HALFLIFE_MIN), state_dir=tmp_path)
    assert abs(cs[0]["fill_weight"] - 2000) < 1.0


def test_magnet_migrates_to_fresh_fills(tmp_path):
    # morning: all fills at 755 → 755 wins
    fl.annotate("t3", [_c(755, "call", 4000), _c(745, "put", 0)], T0, state_dir=tmp_path)
    # 3 hours later: 755 silent, 745 prints 900 new contracts
    cs = [_c(755, "call", 4000), _c(745, "put", 900)]
    fl.annotate("t3", cs, T0 + timedelta(minutes=180), state_dir=tmp_path)
    w755, w745 = cs[0]["fill_weight"], cs[1]["fill_weight"]
    assert w745 > w755                                  # the stale-755-pin regression case
    assert gb._pin_profile(cs, "fill_weight")[0] == 745


def test_day_rollover_reseeds(tmp_path):
    fl.annotate("t4", [_c(755, "call", 4000)], T0, state_dir=tmp_path)
    cs = [_c(755, "call", 50)]                          # next day, small fresh volume
    fl.annotate("t4", cs, T0 + timedelta(days=1), state_dir=tmp_path)
    assert cs[0]["fill_weight"] == 50                   # yesterday's book forgotten


def test_failure_returns_false_and_leaves_contracts_clean():
    cs = [{"strike": 755, "right": "call", "volume": 10, "dte": 1}]  # no 0DTE rows
    assert fl.annotate("t5", cs, T0, state_dir="/nonexistent/denied") is False
    assert "fill_weight" not in cs[0]


def test_slide_0dte_prefers_fill_weight_when_present():
    # cumulative volume says 755; recency fills say 745 — the legacy ABS pin must
    # follow the fills; the signed net magnet reads the structural weight unchanged.
    cs = [_c(755, "call", 4000), _c(745, "put", 900)]
    cs[0]["fill_weight"] = 100.0
    cs[1]["fill_weight"] = 900.0
    out = gb.slide_0dte(cs, spot=750.0)
    assert out["pin_abs_basis"] == "fill_weight" and out["pin_abs"] == 745


def test_slide_0dte_falls_back_to_cumulative_without_ledger():
    cs = [_c(755, "call", 4000), _c(745, "put", 900)]
    # legacy signed path (v3 kill-switch lineage): the doctrine under test is the
    # basis fallback, and the signed-era pin is the exact strike, not a zone center
    _m = gb.MAGNET_V3
    gb.MAGNET_V3 = False
    try:
        out = gb.slide_0dte(cs, spot=750.0)
    finally:
        gb.MAGNET_V3 = _m
    assert out["pin_basis"] == "volume" and out["pin"] == 755
    # v3 twin: the sign-free mass field reads the same book as one tight 745-755
    # zone and names its volume-weighted center — both strikes attract now
    out3 = gb.slide_0dte(cs, spot=750.0)
    assert out3["pin_field_v"] == 3 and 745.0 <= out3["pin"] <= 755.0
