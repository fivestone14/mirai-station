"""Schwab -999 failed-solve sentinel must be scrubbed at the ingest boundary, so it
can never masquerade as a negative IV (inverting σ) or a giant fake gamma magnet."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iv_fetcher as F  # noqa: E402


def test_clean_greek_drops_sentinel_and_impossible_negatives():
    assert F._clean_greek(-999.0) is None
    assert F._clean_greek(-999.0, nonneg=True) is None
    assert F._clean_greek(-0.35) == -0.35            # legit put delta / theta kept
    assert F._clean_greek(-0.35, nonneg=True) is None  # but not on iv/gamma/vega
    assert F._clean_greek(0.012) == 0.012
    assert F._clean_greek(None) is None


def _chain_with_sentinel():
    """One healthy ATM call and one deep-ITM put whose greeks all came back -999."""
    return {
        "underlying": {"last": 5000.0},
        "callExpDateMap": {
            "2026-07-17:0": {
                "5000.0": [{
                    "bid": 40.0, "ask": 42.0, "totalVolume": 100, "openInterest": 500,
                    "volatility": 15.0, "delta": 0.5, "gamma": 0.01, "theta": -1.0, "vega": 2.0,
                }],
            },
        },
        "putExpDateMap": {
            "2026-07-17:0": {
                "4000.0": [{  # deep-ITM put: Schwab solver failed → -999 everywhere
                    "bid": 999.0, "ask": 1001.0, "totalVolume": 10, "openInterest": 8000,
                    "volatility": -999.0, "delta": -999.0, "gamma": -999.0,
                    "theta": -999.0, "vega": -999.0,
                }],
            },
        },
    }


def test_normalize_chain_scrubs_999():
    out = F._normalize_chain(_chain_with_sentinel())
    put = next(c for c in out["contracts"] if c["right"] == "put")
    assert put["iv"] is None                 # -999 vol → None, NOT -9.99
    assert put["gamma"] is None              # -999 gamma → None, not a fake magnet
    assert put["delta"] is None and put["theta"] is None and put["vega"] is None
    call = next(c for c in out["contracts"] if c["right"] == "call")
    assert call["iv"] == 0.15 and call["gamma"] == 0.01   # healthy leg untouched


def test_gex_profile_no_phantom_magnet_from_sentinel():
    out = F._normalize_chain(_chain_with_sentinel())
    profile = F._gex_profile(out["contracts"], spot=5000.0)
    # the deep-ITM put's -999 gamma must NOT appear as a huge exposure at 4000
    assert 4000.0 not in profile or profile.get(4000.0, 0.0) == 0.0


def test_clean_greek_drops_nan_and_inf():
    assert F._clean_greek(float("nan")) is None
    assert F._clean_greek(float("nan"), nonneg=True) is None
    assert F._clean_greek(float("inf")) is None
    assert F._clean_greek(float("-inf")) is None
    assert F._clean_greek(0.02) == 0.02        # finite value still kept
