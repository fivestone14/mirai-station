"""Tests for gw_vocab — the GW notation standard (docs/gw-vocab.md).

Pure math over injected data, no network, no writes. Covers the prime tier
boundaries + hysteresis, MagP wall-tier inheritance (and its off-wall default),
label formatting (tenor prefixes incl. ISO date → MMMDD), the clustering rule
(25% concentration floor, gap tolerance, sign split, noise floor, peak
selection), the never-force-3 nearest picks, and pin hit/miss."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gw_vocab as gw  # noqa: E402


# --------------------------------------------------------------------- tiers
class TestPrimeTier:
    def test_boundaries(self):
        assert gw.prime_tier(1.0) == "'''"
        assert gw.prime_tier(0.60) == "'''"     # ≥0.60 is dominant, inclusive
        assert gw.prime_tier(0.5999) == "''"
        assert gw.prime_tier(0.25) == "''"      # ≥0.25 is significant, inclusive
        assert gw.prime_tier(0.2499) == "'"
        assert gw.prime_tier(0.0) == "'"

    def test_hysteresis_promote_needs_cut_plus_007(self):
        assert gw.prime_tier(0.65, prev_tier="''") == "''"    # < 0.67 → hold
        assert gw.prime_tier(0.68, prev_tier="''") == "'''"   # cleared 0.67
        assert gw.prime_tier(0.30, prev_tier="'") == "'"      # < 0.32 → hold
        assert gw.prime_tier(0.33, prev_tier="'") == "''"

    def test_hysteresis_demote_needs_cut_minus_007(self):
        assert gw.prime_tier(0.55, prev_tier="'''") == "'''"  # ≥ 0.53 → hold
        assert gw.prime_tier(0.52, prev_tier="'''") == "''"   # below 0.53
        assert gw.prime_tier(0.20, prev_tier="''") == "''"    # ≥ 0.18 → hold
        assert gw.prime_tier(0.17, prev_tier="''") == "'"

    def test_hysteresis_can_jump_two_tiers(self):
        assert gw.prime_tier(0.10, prev_tier="'''") == "'"
        assert gw.prime_tier(0.90, prev_tier="'") == "'''"


# ------------------------------------------------------------------ clustering
# A 5-pt grid: a dominant call block above spot 7505, a significant put block
# below, a minor lone call strike, and a sub-floor speck. Strongest strike is
# 7545 (|γ|=20) → concentration floor 5.0.
SURFACE = [
    [7450, -5.0], [7455, -6.0], [7460, -5.0],     # put cluster, peak 7455 (Σ16)
    [7500, 6.0],                                  # lone call strike near spot
    [7540, 5.0], [7545, 20.0], [7550, 8.0],       # dominant call cluster (Σ33)
    [7600, 0.4],                                  # < 25% floor → dropped
]
STEP = 5.0


class TestClustering:
    def test_groups_sides_peaks_and_tiers(self):
        cl = gw.cluster_walls(SURFACE, spot=7505.0)
        assert [c["side"] for c in cl] == ["put", "call", "call"]
        big = max(cl, key=lambda c: c["strength"])
        assert big["peak"] == 7545 and big["lo"] == 7540 and big["hi"] == 7550
        assert big["strength"] == 33.0 and big["share"] == 1.0 and big["tier"] == "'''"
        assert all(c["peak"] != 7600 for c in cl)          # sub-floor speck gone
        put = cl[0]
        assert put["peak"] == 7455 and put["above"] is False
        assert put["tier"] == "''"                         # 16/33 = 0.48
        assert cl[1]["tier"] == "'"                        # 6/33 = 0.18

    def test_concentration_floor_yields_narrow_walls_not_one_blob(self):
        # A contiguous same-sign shelf with two real peaks: without the 25%
        # per-strike floor this grouped into ONE 35-pt blob.
        shelf = [[7500, 2.0], [7505, 3.0], [7510, 20.0], [7515, 4.0],
                 [7520, 2.0], [7525, 3.0], [7530, 15.0], [7535, 2.0]]
        cl = gw.cluster_walls(shelf, spot=7490.0, step=STEP)
        assert [c["peak"] for c in cl] == [7510, 7530]     # two narrow walls
        assert all(c["lo"] == c["hi"] == c["peak"] for c in cl)

    def test_cluster_noise_floor_drops_under_5pct(self):
        # A 10-strike heavyweight cluster (Σ200) vs a lone floor-surviving
        # strike whose cluster share is 5/200 = 2.5% → dropped.
        surf = [[7500 + 5 * i, 20.0] for i in range(10)] + [[7400, 5.0]]
        cl = gw.cluster_walls(surf, spot=7495.0, step=STEP)
        assert [c["peak"] for c in cl] == [7500]

    def test_one_missing_step_stays_one_cluster_two_splits(self):
        # 7500→7510 skips one 5-pt strike (allowed); 7510→7525 skips two (split).
        surf = [[7500, 3.0], [7510, 4.0], [7525, 5.0]]
        cl = gw.cluster_walls(surf, spot=7490.0, step=STEP)
        assert len(cl) == 2
        assert (cl[0]["lo"], cl[0]["hi"]) == (7500, 7510)
        assert cl[1]["peak"] == 7525

    def test_sign_flip_splits_adjacent_strikes(self):
        surf = [[7500, 3.0], [7505, -3.0]]
        cl = gw.cluster_walls(surf, spot=7502.0, step=STEP)
        assert [c["side"] for c in cl] == ["call", "put"]

    def test_step_inferred_from_modal_spacing(self):
        # mostly 5-pt spacing with one 10-pt hole → modal step 5, hole allowed
        surf = [[7500, 3.0], [7505, 4.0], [7515, 3.0], [7520, 2.0]]
        cl = gw.cluster_walls(surf, spot=7500.0)
        assert len(cl) == 1 and cl[0]["hi"] == 7520

    def test_zero_gamma_and_empty_inputs(self):
        assert gw.cluster_walls([], spot=7500.0) == []
        assert gw.cluster_walls([[7500, 0.0]], spot=7500.0) == []
        assert gw.cluster_walls(None, spot=7500.0) == []      # absent surface, JS-twin parity

    def test_single_strike_surface_is_its_own_dominant_wall(self):
        cl = gw.cluster_walls([[7500, 4.0]], spot=7495.0)
        assert len(cl) == 1
        c = cl[0]
        assert (c["lo"], c["hi"], c["peak"]) == (7500, 7500, 7500)
        assert c["share"] == 1.0 and c["tier"] == "'''" and c["side"] == "call"

    def test_tied_strongest_clusters_are_both_dominant(self):
        # equal-strength call and put clusters: share is vs the strongest, so a
        # tie means BOTH read share 1.0 → both ''' (no arbitrary winner)
        surf = [[7450, -10.0], [7550, 10.0]]
        cl = gw.cluster_walls(surf, spot=7500.0, step=STEP)
        assert [c["tier"] for c in cl] == ["'''", "'''"]
        assert [c["share"] for c in cl] == [1.0, 1.0]

    def test_nearest_walls_ranks_against_the_passed_spot(self):
        # the spot handed to nearest_walls wins over the clustering-time flag
        # (JS-twin parity: the JS helper recomputes above/below from spot)
        cl = gw.cluster_walls(SURFACE, spot=7505.0)
        near = gw.nearest_walls(cl, spot=7460.0)
        assert [c["peak"] for c in near["above"]] == [7500, 7545]
        assert [c["peak"] for c in near["below"]] == [7455]


# ---------------------------------------------------------------- nearest/pin
class TestNearestAndPin:
    def test_nearest_walls_sorted_and_never_forced_to_three(self):
        cl = gw.cluster_walls(SURFACE, spot=7505.0)
        near = gw.nearest_walls(cl, spot=7505.0)
        assert [c["peak"] for c in near["above"]] == [7545]   # only 1 above → 1 shown
        assert [c["peak"] for c in near["below"]] == [7500, 7455]

    def test_nearest_walls_caps_per_side(self):
        surf = [[7510, 5.0], [7530, 5.0], [7550, 5.0], [7570, 5.0]]
        cl = gw.cluster_walls(surf, spot=7500.0, step=STEP)
        near = gw.nearest_walls(cl, spot=7500.0)
        assert [c["peak"] for c in near["above"]] == [7510, 7530, 7550]
        assert near["below"] == []

    def test_pin_status_hit_inside_span_plus_one_step(self):
        cl = gw.cluster_walls(SURFACE, spot=7505.0, step=STEP)
        pinned = gw.pin_status(cl, spot=7538.0, step=STEP)    # 7540 − one step
        assert pinned is not None and pinned["peak"] == 7545

    def test_pin_status_miss_in_the_open(self):
        cl = gw.cluster_walls(SURFACE, spot=7505.0, step=STEP)
        assert gw.pin_status(cl, spot=7520.0, step=STEP) is None


# --------------------------------------------------------------------- MagP
class TestMagpTier:
    def test_inherits_the_host_walls_tier(self):
        cl = gw.cluster_walls(SURFACE, spot=7505.0, step=STEP)
        assert gw.magp_tier(7546.0, cl, STEP) == "'''"        # on the dominant wall
        assert gw.magp_tier(7455.0, cl, STEP) == "''"         # on the put wall
        assert gw.magp_tier(7500.0, cl, STEP) == "'"          # on the minor wall

    def test_magnet_one_step_from_two_walls_nearest_peak_wins(self):
        # adjacent opposite-sign walls (a same-sign gap this small would merge)
        # BOTH claim a magnet inside their span±1-step reach: the nearer PEAK
        # hosts — deterministic, exact ties break to the lower strike (min on
        # |peak − magnet| keeps the first, clusters come in strike order)
        surf = [[7500, 20.0], [7505, -8.0]]
        cl = gw.cluster_walls(surf, spot=7490.0, step=STEP)
        assert [c["side"] for c in cl] == ["call", "put"]
        assert gw.magp_tier(7501.0, cl, STEP) == "'''"        # nearer 7500 (''')
        assert gw.magp_tier(7504.0, cl, STEP) == "''"         # nearer 7505 ('')
        assert gw.magp_tier(7502.5, cl, STEP) == "'''"        # exact tie → lower strike hosts

    def test_off_wall_and_missing_magnet_default(self):
        cl = gw.cluster_walls(SURFACE, spot=7505.0, step=STEP)
        assert gw.magp_tier(7520.0, cl, STEP) == "''"         # open field → default
        assert gw.magp_tier(None, cl, STEP) == "''"
        assert gw.magp_tier(7500.0, [], STEP) == "''"         # no walls at all


# --------------------------------------------------------------------- labels
class TestLabels:
    def test_untenored_and_intraday_tenors(self):
        assert gw.gw_label("call", "'''") == "GWc'''"
        assert gw.gw_label("put", "'") == "GWp'"
        assert gw.gw_label("call", "'''", "0DTE") == "[0DTE]GWc'''"
        assert gw.gw_label("put", "''", "1-7DTE") == "[1-7DTE]GWp''"

    def test_dated_book_tenor_formats_as_mmmdd(self):
        assert gw.gw_label("call", "''", "2026-08-21") == "[AUG21]GWc''"
        assert gw.gw_label("put", "'", "2026-12-05") == "[DEC05]GWp'"

    def test_bad_side_raises(self):
        with pytest.raises(ValueError):
            gw.gw_label("straddle", "''")
