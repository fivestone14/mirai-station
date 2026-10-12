import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reversion_lens as rl


def _full_row():
    return {
        "gamma_sign": "negative", "gamma_flip": 7400.0,
        "call_wall": 7500.0, "put_wall": 7300.0,
        "gex_source": "spy_proxy",
        "reversion_extreme": {"magnet": 7450.0},
        "gex_views": {"regime": "long_gamma", "flip": 7448.0,
                      "flip_band": [7430.0, 7480.0], "magnet": 7488.0,
                      "source": "spy_proxy×10.0370",
                      "vex_sign": "positive", "cex_sign": "negative",
                      "call_wall_gamma": 7487.0, "put_wall_gamma": 7477.0},
        "gex_theta": {"regime": "short_gamma", "regime_agree": False,
                      "magnet": 7500.0, "magnet_vs_proxy": 12.0,
                      "aggressor_flow": -0.03, "sign_agrees": True,
                      "sign_confidence": 1.0},
    }


def test_resolver_prefers_gex_views_over_legacy():
    dm = rl.resolve_dealer_map(_full_row())
    assert dm["regime"] == "long_gamma" and dm["regime_source"] == "gex_views"
    assert dm["flip"] == 7448.0 and dm["flip_source"] == "gex_views"
    assert dm["magnet"] == 7488.0 and dm["magnet_source"] == "gex_views"
    assert dm["flip_band"] == [7430.0, 7480.0]
    assert dm["call_wall"] == 7500.0 and dm["put_wall"] == 7300.0   # legacy band bounds
    assert dm["source"].startswith("spy_proxy")


def test_resolver_surfaces_native_challenger():
    dm = rl.resolve_dealer_map(_full_row())
    nat = dm["native"]
    assert nat["regime"] == "short_gamma" and nat["regime_agree"] is False
    assert nat["magnet_delta"] == 12.0 and nat["flow"] == -0.03


def test_resolver_falls_back_to_legacy_fields():
    dm = rl.resolve_dealer_map({
        "gamma_sign": "negative", "gamma_flip": 7400.0,
        "call_wall": 7500.0, "put_wall": 7300.0, "gex_source": "oracle",
        # the resolver's magnet fallback must use the PRE-CLAMP pin (magnet_raw),
        # never the wall-clamped revert-target — a clamped wall drawn as "the
        # magnet" would narrate a fade target as dealer gravity (2026-07-10 audit)
        "reversion_extreme": {"magnet": 7500.0, "magnet_raw": 7450.0,
                              "magnet_out_of_band": True},
    })
    assert dm["regime"] == "short_gamma" and dm["regime_source"] == "legacy"
    assert dm["flip"] == 7400.0 and dm["flip_source"] == "legacy"
    assert dm["magnet"] == 7450.0 and dm["magnet_source"] == "reversion_raw"
    assert dm["native"] is None and dm["source"] == "oracle"


def test_resolver_magnet_prefers_theta_challenger_before_reversion():
    dm = rl.resolve_dealer_map({
        "gex_views": {},                        # leader has no magnet this row
        "gex_theta": {"magnet": 7480.0},        # challenger carries the raw pin
        "reversion_extreme": {"magnet_raw": 7450.0},
    })
    assert dm["magnet"] == 7480.0 and dm["magnet_source"] == "gex_theta"


def test_resolver_handles_empty_input():
    assert rl.resolve_dealer_map(None) is None
    assert rl.resolve_dealer_map({}) is None   # no row → no map (callers `or {}`)
