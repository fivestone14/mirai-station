"""Unit tests for the pure metric math. Mandatory before any live run."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iv_metrics as M  # noqa: E402


# ---------------------------------------------------------------------------
# IV rank / percentile edge cases
# ---------------------------------------------------------------------------


def test_iv_rank_flat_history_returns_none():
    result = M.iv_rank(0.25, [0.25] * 252)
    assert result.value is None
    assert result.confidence == "none"
    assert "flat" in result.reason


def test_iv_rank_insufficient_history():
    result = M.iv_rank(0.25, [0.20, 0.22, 0.24])
    assert result.value is None
    assert result.confidence == "low"
    assert "insufficient" in result.reason


def test_iv_rank_rich_regime_tags_sell():
    history = [0.10 + (i * 0.001) for i in range(252)]
    result = M.iv_rank(0.35, history)
    assert result.bias == "SELL"
    assert result.confidence == "high"


def test_iv_rank_cheap_regime_tags_buy():
    history = [0.10 + (i * 0.001) for i in range(252)]
    result = M.iv_rank(0.105, history)
    assert result.bias == "BUY"


def test_iv_rank_none_current_iv():
    result = M.iv_rank(None, [0.1] * 252)
    assert result.value is None
    assert result.confidence == "none"


def test_iv_rank_nan_current_iv():
    result = M.iv_rank(float("nan"), [0.1] * 252)
    assert result.value is None


def test_iv_percentile_midrange_neutral():
    history = [0.10 + (i * 0.001) for i in range(252)]
    result = M.iv_percentile(0.235, history)
    assert result.bias == "NEUTRAL"


# ---------------------------------------------------------------------------
# Realized vol / VRP
# ---------------------------------------------------------------------------


def test_realized_vol_handles_split():
    closes = [100.0] * 30 + [50.0] + [50.0] * 30  # 50% drop = split-like
    hv = M.realized_vol(closes, 30)
    assert hv is not None
    assert hv < 1.0  # not astronomical


def test_realized_vol_insufficient_history():
    assert M.realized_vol([100.0] * 5, 30) is None


def test_vrp_wide_positive_spread_tags_sell():
    closes = [100.0 + i * 0.01 for i in range(60)]  # near-zero HV
    result = M.vol_risk_premium(0.30, closes, 30)
    assert result.bias == "SELL"


def test_vrp_missing_iv_returns_none():
    result = M.vol_risk_premium(None, [100.0] * 60, 30)
    assert result.value is None


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------


def test_term_structure_insufficient_expiries():
    result = M.term_structure_slope({7: 0.30})
    assert result.value is None
    assert "need" in result.reason


def test_term_structure_backwardation_sells():
    result = M.term_structure_slope({7: 0.40, 90: 0.30})
    assert result.bias == "SELL"


def test_term_structure_contango_buys():
    result = M.term_structure_slope({7: 0.20, 90: 0.25})
    assert result.bias == "BUY"


# ---------------------------------------------------------------------------
# Skew
# ---------------------------------------------------------------------------


def test_risk_reversal_thin_oi_low_confidence():
    result = M.risk_reversal_25d(0.30, 0.25, put_oi=2, call_oi=2)
    assert result.confidence == "low"
    assert result.value is None


def test_risk_reversal_missing_leg_none():
    result = M.risk_reversal_25d(None, 0.25, put_oi=100, call_oi=100)
    assert result.confidence == "none"


def test_risk_reversal_fear_tags_buy():
    result = M.risk_reversal_25d(0.30, 0.25, put_oi=100, call_oi=100)
    assert result.bias == "BUY"


def test_put_skew_insufficient_strikes():
    result = M.put_skew_steepness([100.0], [0.25], 100.0)
    assert result.value is None
    assert result.confidence == "low"


def test_put_skew_flat_fit_returns_buy():
    strikes = [90, 95, 100]
    ivs = [0.25, 0.25, 0.25]
    result = M.put_skew_steepness(strikes, ivs, 100)
    assert result.bias == "BUY"


# ---------------------------------------------------------------------------
# Contract pricing
# ---------------------------------------------------------------------------


def test_bid_ask_spread_invalid():
    assert M.bid_ask_spread_pct(None, 1.0).value is None
    assert M.bid_ask_spread_pct(1.0, 0.5).value is None  # ask < bid
    assert M.bid_ask_spread_pct(0.0, 0.0).value is None


def test_vega_theta_zero_theta_low_confidence():
    result = M.vega_theta_ratio(0.5, 0.0)
    assert result.confidence == "low"
    assert result.value is None


def test_vega_theta_high_ratio_vol_play():
    result = M.vega_theta_ratio(0.6, -0.1)
    assert result.value == pytest.approx(6.0)
    assert "vol play" in result.reason


def test_extrinsic_negative_is_low_confidence():
    # ITM call with mid < intrinsic (stale quote)
    result = M.extrinsic_pct(mid_price=4.0, spot=110.0, strike=100.0, right="call")
    assert result.confidence == "low"


def test_extrinsic_normal_otm_call():
    result = M.extrinsic_pct(mid_price=3.0, spot=100.0, strike=105.0, right="call")
    assert result.value == 100.0  # fully extrinsic


def test_break_even_missing_dte_none():
    assert M.break_even_vs_expected(105, 100, 0.25, None).value is None
    assert M.break_even_vs_expected(105, 100, 0.25, 0).value is None


def test_break_even_outside_1sd_sells():
    # spot 100, iv 25%, 30 dte => 1sd ~= 100 * 0.25 * sqrt(30/252) ~= 8.6
    result = M.break_even_vs_expected(112, 100, 0.25, 30)
    assert result.bias == "SELL"


# ---------------------------------------------------------------------------
# OI / volume flow
# ---------------------------------------------------------------------------


def test_pc_vol_no_calls():
    assert M.put_call_volume_ratio(100, 0).value is None


def test_pc_vol_put_heavy_neutral_tag():
    # ratio > 1.3 is "put-heavy" but bias is NEUTRAL (not a direction call)
    result = M.put_call_volume_ratio(200, 100)
    assert result.bias == "NEUTRAL"
    assert "put-heavy" in result.reason


def test_delta_weighted_volume_empty():
    result = M.delta_weighted_volume([])
    assert result.confidence == "none"


def test_delta_weighted_volume_bullish():
    contracts = [{"delta": 0.5, "volume": 100}, {"delta": 0.3, "volume": 50}]
    result = M.delta_weighted_volume(contracts)
    assert result.value > 0


def test_max_pain_empty():
    assert M.max_pain({}, 100.0).value is None


def test_max_pain_tie_picks_lower():
    # symmetric OI = tie between 95 and 105
    strikes_oi = {
        95: {"call_oi": 10, "put_oi": 10},
        100: {"call_oi": 100, "put_oi": 100},
        105: {"call_oi": 10, "put_oi": 10},
    }
    result = M.max_pain(strikes_oi, 100.0)
    assert result.value == 100.0  # most OI at 100


# ---------------------------------------------------------------------------
# Gamma
# ---------------------------------------------------------------------------


def test_gex_empty_chain():
    result = M.net_dealer_gex([], 100.0)
    assert result.value is None


def test_gex_positive_sells():
    contracts = [
        {"gamma": 0.02, "open_interest": 1000, "right": "call"},
    ]
    result = M.net_dealer_gex(contracts, 100.0)
    assert result.value > 0
    assert result.bias == "SELL"


def test_gamma_flip_no_sign_change():
    profile = {90: 100, 100: 200, 110: 300}
    result = M.gamma_flip_level(profile)
    assert result.value is None


def test_gamma_flip_interpolated():
    profile = {90: -100, 100: -50, 110: 200}
    result = M.gamma_flip_level(profile)
    assert result.value is not None
    assert 100 < result.value < 110


# ---------------------------------------------------------------------------
# IV crush
# ---------------------------------------------------------------------------


def test_iv_crush_no_crush_when_front_le_back():
    result = M.iv_crush_pnl_estimate(0.25, 0.30, 0.5)
    assert result.value == 0.0
    assert "no crush" in result.reason


def test_iv_crush_missing_inputs():
    assert M.iv_crush_pnl_estimate(None, 0.25, 0.5).value is None
    assert M.iv_crush_pnl_estimate(0.35, 0.25, None).value is None


def test_iv_crush_negative_pnl():
    result = M.iv_crush_pnl_estimate(0.40, 0.25, vega=0.5, contracts_count=10)
    assert result.value is not None
    assert result.value < 0  # long vega loses on crush


# ---------------------------------------------------------------------------
# Metric.counts() gate
# ---------------------------------------------------------------------------


def test_metric_counts_only_high_medium_with_direction():
    assert M.Metric(1.0, "high", "r", "BUY").counts() is True
    assert M.Metric(1.0, "medium", "r", "SELL").counts() is True
    assert M.Metric(1.0, "low", "r", "BUY").counts() is False
    assert M.Metric(1.0, "high", "r", "NEUTRAL").counts() is False
    assert M.Metric(1.0, "high", "r", None).counts() is False
