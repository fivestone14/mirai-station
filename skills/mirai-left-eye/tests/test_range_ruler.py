"""Tests for lefteye_range_ruler — THE RANGE RULER (expected-move yardstick).

Pure math over injected data, no network, no writes. Covers the per-leg price
ladder (mid → mark → IV fallback → missing), the both-rights ATM pick, open
anchoring + the re-anchor grace window, late-day quality, the one-sided
em_consumed gauge, the diary rebuild helper, and pressure/garbage inputs."""
import math
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_range_ruler as rr  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 8, 10, 0, tzinfo=ET)          # 10:00 ET, mid-morning
NOW_931 = datetime(2026, 7, 8, 9, 31, tzinfo=ET)      # just after the open
NOW_1300 = datetime(2026, 7, 8, 13, 0, tzinfo=ET)     # deep past the grace window

SPOT = 7500.0


def _c(k, right, *, dte=0, bid=None, ask=None, mark=None, iv=None):
    return {"strike": k, "right": right, "dte": dte,
            "bid": bid, "ask": ask, "mark": mark, "iv": iv}


def _book(spot=SPOT):
    """A clean 0DTE straddle book: sane quotes at ATM, neighbors both sides,
    plus a dated pair the 0DTE filter must ignore."""
    return [
        _c(spot - 5, "call", bid=27.0, ask=29.0, iv=0.15),
        _c(spot - 5, "put", bid=21.0, ask=23.0, iv=0.15),
        _c(spot, "call", bid=24.0, ask=26.0, iv=0.15),      # mid 25.0
        _c(spot, "put", bid=23.0, ask=25.0, iv=0.15),       # mid 24.0
        _c(spot + 5, "call", bid=21.0, ask=23.0, iv=0.15),
        _c(spot + 5, "put", bid=26.0, ask=28.0, iv=0.15),
        _c(spot, "call", dte=1, bid=60.0, ask=62.0, iv=0.16),
        _c(spot, "put", dte=1, bid=58.0, ask=60.0, iv=0.16),
    ]


# --------------------------------------------------------------------- ladder
class TestPriceLadder:
    def test_clean_book_straddle_mid(self):
        out = rr.read(_book(), SPOT, NOW, em_open=41.65, minutes_to_close=330)
        assert out["em_points"] == round(rr.DEBIAS * (25.0 + 24.0), 2)  # 41.65
        assert out["em_pct"] == round(out["em_points"] / SPOT, 5)
        assert out["anchor"] == "straddle_mid"
        assert out["quality"] == "ok"
        assert out["source"] == "native_chain"

    def test_nearest_strike_missing_put_falls_to_next_complete_strike(self):
        # 7500 has only a call (absurdly priced to prove it is NOT used);
        # 7505 is the nearest strike with BOTH legs → it wins.
        book = [
            _c(7500, "call", bid=99.0, ask=101.0),
            _c(7505, "call", bid=22.0, ask=24.0),   # mid 23.0
            _c(7505, "put", bid=25.0, ask=27.0),    # mid 26.0
        ]
        out = rr.read(book, 7500.0, NOW, em_open=50.0, minutes_to_close=330)
        assert out["em_points"] == round(rr.DEBIAS * (23.0 + 26.0), 2)
        assert out["anchor"] == "straddle_mid"

    def test_crossed_quote_leg_uses_mark_and_degrades_quality(self):
        # call quote is CROSSED (bid > ask) → the leg drops to mark; the anchor
        # reports the worst leg used and quality degrades to fallback.
        book = [
            _c(SPOT, "call", bid=26.0, ask=24.0, mark=25.0),
            _c(SPOT, "put", bid=23.0, ask=25.0),            # clean mid 24.0
        ]
        out = rr.read(book, SPOT, NOW, em_open=41.65, minutes_to_close=330)
        assert out["em_points"] == round(rr.DEBIAS * (25.0 + 24.0), 2)
        assert out["anchor"] == "straddle_mark"
        assert out["quality"] == "fallback"

    def test_iv_fallback_exact_brenner_subrahmanyam_math(self):
        # no quotes, no marks — only IV survives; assert the exact formula,
        # T_yr = (minutes_to_close/390)/252 on the residual clock.
        mtc = 195.0
        book = [
            _c(SPOT, "call", iv=0.14),
            _c(SPOT, "put", iv=0.16),
        ]
        out = rr.read(book, SPOT, NOW, em_open=None, minutes_to_close=mtc)
        iv_atm = (0.14 + 0.16) / 2.0
        t_yr = (mtc / 390.0) / 252.0
        expected = round(rr.DEBIAS * rr.BS_FACTOR * SPOT * iv_atm * math.sqrt(t_yr), 2)
        assert out["em_points"] == expected
        assert out["anchor"] == "iv_sqrt_t"
        assert out["quality"] == "fallback"

    def test_no_0dte_book_is_missing_not_a_raise(self):
        dated = [_c(SPOT, "call", dte=1, bid=60.0, ask=62.0, iv=0.16),
                 _c(SPOT, "put", dte=3, bid=58.0, ask=60.0, iv=0.16)]
        out = rr.read(dated, SPOT, NOW, em_open=60.0, minutes_to_close=330)
        assert out["em_points"] is None
        assert out["anchor"] is None
        assert out["quality"] == "missing"
        assert out["em_open"] == 60.0                    # passthrough survives


# ---------------------------------------------------------------- anchoring
class TestAnchorAndConsumption:
    def test_open_anchor_ok_early_reanchored_past_grace(self):
        early = rr.read(_book(), SPOT, NOW_931, em_open=None, minutes_to_close=389)
        assert early["em_open"] == early["em_points"]
        assert early["quality"] == "ok"                  # inside the 45-min grace
        late = rr.read(_book(), SPOT, NOW_1300, em_open=None, minutes_to_close=180)
        assert late["em_open"] == late["em_points"]
        assert late["quality"] == "reanchored"           # mid-day ruler is suspect

    def test_em_consumed_is_max_one_sided_excursion(self):
        out = rr.read(_book(), SPOT, NOW, em_open=60.0, minutes_to_close=300,
                      day_open=7400.0, day_high=7450.0, day_low=7380.0)
        assert out["spent_pts"] == 50.0                  # max(50 up, 20 down)
        assert out["em_consumed"] == 0.8333              # unclamped, probationary

    def test_spent_pts_rounded_for_diary(self):
        # float subtraction noise (7450.1 - 7400.03 = 50.06999999999971) must
        # not reach the diary raw
        out = rr.read(_book(), SPOT, NOW, em_open=60.0, minutes_to_close=300,
                      day_open=7400.03, day_high=7450.1, day_low=7380.0)
        assert out["spent_pts"] == 50.07

    def test_late_day_quality_even_on_clean_quotes(self):
        out = rr.read(_book(), SPOT, NOW, em_open=41.65, minutes_to_close=30)
        assert out["quality"] == "late_day"
        assert out["em_points"] == round(rr.DEBIAS * 49.0, 2)   # still priced


# ------------------------------------------------------------- diary rebuild
class TestEmOpenFromRows:
    def test_recovers_first_valid_spx_em_open_past_torn_rows(self):
        rows = [
            {"ticker": "QQQ", "range_ruler": {"em_open": 40.0}},   # wrong ticker
            "torn-row",                                            # not a dict
            {"ticker": "SPX"},                                     # no range_ruler
            {"ticker": "SPX", "range_ruler": "torn"},              # wrong shape
            {"ticker": "SPX", "range_ruler": {"em_open": None}},   # unanchored scan
            {"ticker": "SPX", "range_ruler": {"em_open": 52.5}},   # first valid
            {"ticker": "SPX", "range_ruler": {"em_open": 61.0}},   # later, ignored
        ]
        assert rr.em_open_from_rows(rows) == 52.5
        assert rr.em_open_from_rows([]) is None


# ----------------------------------------------------------------- pressure
class TestPassthroughsAndGarbage:
    def test_event_severity_vol_carry_and_garbage_contracts(self):
        garbage = ["junk", None, 42, {"strike": "x", "dte": 0},
                   {"right": "call", "dte": 0}]
        out = rr.read(garbage, SPOT, NOW, em_open=None,
                      event_kinds=["OPEX", "CPI"], vix=16.0, vix3m=18.0)
        assert out["quality"] == "missing"               # nothing priceable
        assert out["em_points"] is None
        assert out["event_flag"] == "CPI"                # CPI outranks OPEX
        assert out["vol_carry"] == {"vix": 16.0, "vix3m": 18.0,
                                    "ratio": round(16.0 / 18.0, 3)}
