"""Tests for lefteye_profile_ladder — the gamma profile state ladder (SHADOW).

Pure math, no network. Covers the five rungs (GWc/cT/HVL/pT/GWp) with their
signed % distances, the zone word in every band (the wt-8 chop band verbatim),
the measured-or-absent honesty rules (no flip → no state; no surface → no GW
rungs; nothing → None), and the side/position filters on the cluster picks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lefteye_profile_ladder as PL  # noqa: E402
from watchtower import FLIP_CHOP_BAND_SIGMA  # noqa: E402

SPOT = 5000.0
SIGMA = 40.0
FLIP = 4990.0
BAND = FLIP_CHOP_BAND_SIGMA * SIGMA          # 10.0 at the 0.25σ doctrine cut

# One call fortress above spot, one put fortress below — clean two-sided surface.
SURFACE = ([[4930 + k, -900.0] for k in (0, 5, 10)]
           + [[5060 + k, 1000.0] for k in (0, 5, 10)])


def test_full_ladder_all_rungs_and_signed_pcts():
    r = PL.read(SURFACE, SPOT, SIGMA, FLIP)
    assert r["ladder_v"] == 1 and r["band_sigma"] == FLIP_CHOP_BAND_SIGMA
    assert r["hvl"] == FLIP
    assert r["ct"] == FLIP + BAND and r["pt"] == FLIP - BAND
    # GWc = peak-|gamma| strike of the call cluster above; GWp mirror below
    assert r["gwc"] == 5060.0 and r["gwp"] == 4930.0
    assert r["gwc_tier"] == "'''" and r["gwp_tier"] == "'''"
    # signed % distance from spot: + above, − below
    assert r["gwc_pct"] == round((5060 - SPOT) / SPOT * 100, 3) > 0
    assert r["gwp_pct"] == round((4930 - SPOT) / SPOT * 100, 3) < 0
    assert r["hvl_pct"] < 0 and r["ct_pct"] == 0.0 and r["pt_pct"] < 0


def test_state_words_in_every_zone():
    # spot above cT → positive; inside the band each side of the flip → the
    # transition words; below pT → negative (the wt-8 chop band, one rule)
    assert PL.read([], FLIP + BAND + 1, SIGMA, FLIP)["state"] == "positive"
    assert PL.read([], FLIP + BAND / 2, SIGMA, FLIP)["state"] == "positive transition"
    assert PL.read([], FLIP - BAND / 2, SIGMA, FLIP)["state"] == "negative transition"
    assert PL.read([], FLIP - BAND - 1, SIGMA, FLIP)["state"] == "negative"
    # boundaries: at cT still transition; exactly AT the flip = the negative side
    assert PL.read([], FLIP + BAND, SIGMA, FLIP)["state"] == "positive transition"
    assert PL.read([], FLIP, SIGMA, FLIP)["state"] == "negative transition"
    assert PL.read([], FLIP - BAND, SIGMA, FLIP)["state"] == "negative transition"


def test_no_flip_means_no_state_and_no_band_but_walls_survive():
    r = PL.read(SURFACE, SPOT, SIGMA, None)
    assert r["state"] is None and r["hvl"] is None
    assert r["ct"] is None and r["pt"] is None
    assert r["gwc"] == 5060.0 and r["gwp"] == 4930.0


def test_no_sigma_means_no_band_never_a_guessed_state():
    r = PL.read(SURFACE, SPOT, None, FLIP)
    assert r["state"] is None and r["ct"] is None and r["pt"] is None
    assert r["hvl"] == FLIP                       # the flip itself needs no σ


def test_no_surface_means_no_gw_rungs():
    r = PL.read(None, SPOT, SIGMA, FLIP)
    assert r["gwc"] is None and r["gwp"] is None and r["gwc_tier"] is None
    assert r["state"] is not None                 # band half still derivable


def test_nothing_derivable_returns_none():
    assert PL.read(None, SPOT, SIGMA, None) is None
    assert PL.read([], SPOT, None, None) is None
    assert PL.read(SURFACE, None, SIGMA, FLIP) is None      # no spot → no ladder


def test_side_and_position_filters_on_the_cluster_picks():
    # a CALL cluster BELOW spot must never be GWc, and a PUT cluster ABOVE spot
    # must never be GWp — the ladder's rungs are side-and-position, not nearest
    surf = [[4940, 800.0], [4945, 700.0],        # call cluster BELOW spot
            [5055, -900.0], [5060, -850.0]]      # put cluster ABOVE spot
    r = PL.read(surf, SPOT, SIGMA, FLIP)
    assert r["gwc"] is None and r["gwp"] is None


def test_nearest_call_cluster_wins_not_the_strongest():
    surf = [[5030, 300.0], [5035, 250.0],                   # nearer, weaker picket
            [5100, 1000.0], [5105, 900.0], [5110, 900.0]]   # farther fortress
    r = PL.read(surf, SPOT, SIGMA, FLIP)
    assert r["gwc"] == 5030.0
    assert r["gwc_tier"] == "'"                  # tier still tells the UI it is a picket


def test_torn_surface_fails_open_to_no_walls():
    r = PL.read([["junk", None]], SPOT, SIGMA, FLIP)
    assert r["gwc"] is None and r["gwp"] is None and r["state"] is not None
