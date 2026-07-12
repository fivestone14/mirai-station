"""Tests for lefteye_gex_box — the 6-slide GEX engine (BETA, shadow).

Pure logic + caching, no network (all lookups injected). Covers the two verified
fixes (Absolute-Gamma pin that never cancels; BS-repriced root-find flip that doesn't
teleport), the sign cross-check, the tenor split, the trigger-gated cache, the SPY
rescale, and pressure/degenerate inputs."""
import math
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_gex_box as gv  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 6, 26, 11, 0, tzinfo=ET)


def _c(k, right, dte, g, oi=0, vol=0, iv=0.15):
    return {"strike": k, "right": right, "dte": dte, "gamma": g,
            "open_interest": oi, "volume": vol, "iv": iv}


def _chain(spot=5000.0):
    """A realistic asymmetric chain: 0DTE (volume) + 1-7DTE (OI), gamma peaks ATM."""
    ch = []
    for k in range(4880, 5121, 10):
        g = 0.003 * math.exp(-((k - spot) / 40) ** 2)
        pheavy = 1.0 if k < spot else 0.4
        cheavy = 1.0 if k > spot else 0.4
        ch.append(_c(k, "call", 3, g, oi=int(1500 * cheavy + 200), vol=100))
        ch.append(_c(k, "put", 3, g, oi=int(1500 * pheavy + 200), vol=100))
        cv = 4000 if k == 5010 else int(2000 * cheavy + 100)
        ch.append(_c(k, "call", 0, g, oi=0, vol=cv))
        ch.append(_c(k, "put", 0, g, oi=0, vol=int(2000 * pheavy + 100)))
    return ch


# --------------------------------------------------------------------------- pure
class TestTenorAndBasis:
    def test_split_tenor(self):
        zero, near = gv.split_tenor(_chain())
        assert zero and near
        assert all(c["dte"] == 0 for c in zero)
        assert all(1 <= c["dte"] <= 7 for c in near)

    def test_slide_tenor_walls_per_strike_field(self):
        # 2026-07-13: slide C carries the 1-7DTE per-strike signed net field
        # (calls +, puts −, STORED gamma, ±NBS_WINDOW window) for the tablet's
        # terrain outlines — separate from the 0DTE field, never blended
        zero, near = gv.split_tenor(_chain())
        out = gv.slide_tenor_walls(near, 5000.0)
        nbs = out["net_by_strike"]
        assert isinstance(nbs, list) and nbs
        ks = [k for k, _v in nbs]
        assert min(ks) >= 5000.0 * (1 - gv.NBS_WINDOW) - 1e-9
        assert max(ks) <= 5000.0 * (1 + gv.NBS_WINDOW) + 1e-9
        # fixture: calls heavier above spot, puts heavier below → signs split
        by = dict(nbs)
        assert by[5100.0] > 0 and by[4900.0] < 0
        # empty tenor book → key stays None (fail-open)
        assert gv.slide_tenor_walls([], 5000.0)["net_by_strike"] is None

    def test_pick_basis_oi_then_volume_then_none(self):
        oi_rich = [_c(5000, "call", 3, 0.002, oi=2000), _c(5000, "put", 3, 0.002, oi=2000)]
        assert gv.pick_basis(oi_rich, 5000) == "open_interest"
        vol_only = [_c(5000, "call", 0, 0.002, oi=0, vol=2000), _c(5000, "put", 0, 0.002, oi=0, vol=2000)]
        assert gv.pick_basis(vol_only, 5000) == "volume"
        empty = [_c(5000, "call", 0, 0.002, oi=0, vol=0)]
        assert gv.pick_basis(empty, 5000) is None
        assert gv.pick_basis([], 5000) is None


class TestPinAbsoluteGamma:
    def test_pin_is_max_total_gamma_strike(self):
        out = gv.slide_0dte(*( _split0(_chain()) ))
        # MAGNET ZONES (07-09): the 5010-5120 band clusters into ONE 110-pt
        # smear — wider than ZONE_MAX_WIDTH, so the MEGA-ZONE GUARD keeps the
        # LIVE pin on the argmax; the zone still rides for the A/B + tablet.
        assert out["pin_argmax"] == 5010
        assert out["pin"] == 5010                    # guard: smear can't steer
        z = out["pin_zones"]
        assert z and z[0]["lo"] <= 5010 <= z[0]["hi"] and z[0]["share"] > 0.5
        assert (z[0]["hi"] - z[0]["lo"]) > gv.ZONE_MAX_WIDTH

    def test_pin_does_not_cancel_on_balanced_gamma(self):
        # huge BALANCED call/put gamma at 5000 — a net centroid cancels this to None;
        # Absolute Gamma must return it as the STRONGEST pin.
        bal = [_c(5000, "call", 0, 0.004, vol=9000), _c(5000, "put", 0, 0.004, vol=9000)]
        for k in (4980, 5020):
            bal += [_c(k, "call", 0, 0.002, vol=1000), _c(k, "put", 0, 0.002, vol=1000)]
        assert gv._pin_profile(bal, "volume")[0] == 5000

    def test_pin_none_on_empty(self):
        assert gv._pin_profile([], "volume")[0] is None
        assert gv.slide_0dte([], 5000)["pin"] is None

    def test_net_magnet_cancels_balanced_strike_and_falls_back(self):
        # the huge but perfectly BALANCED 5000 strike exerts no net pull — the signed
        # magnet must skip it (the unsigned pin_abs still names it, for the diary)
        bal = [_c(5000, "call", 0, 0.004, vol=9000), _c(5000, "put", 0, 0.004, vol=9000),
               _c(5020, "call", 0, 0.002, vol=1400), _c(5020, "put", 0, 0.002, vol=1000)]
        out = gv.slide_0dte(bal, 5000.0)
        assert out["pin"] == 5020                    # the only strike with positive net
        assert out["pin_abs"] == 5000                # legacy read still sees the tower
        # an ALL-repelling book has no attractive strike → pin None (magnet→flip upstream)
        rep = [_c(5000, "put", 0, 0.004, vol=9000), _c(5020, "put", 0, 0.002, vol=1000)]
        assert gv.slide_0dte(rep, 5000.0)["pin"] is None

    def test_net_pin_sharpens_on_the_live_clock(self):
        # same book, 15 minutes to close: far strikes lose gamma faster than ATM,
        # so the pin strike's share of the pull must GROW as the clock runs down
        cs = [_c(5000, "call", 0, None, vol=3000), _c(5050, "call", 0, None, vol=3000)]
        _, _, share_floor = gv._net_pin_profile(cs, "volume", 5000.0, None)
        _, _, share_bell = gv._net_pin_profile(cs, "volume", 5000.0, 15.0)
        assert share_bell > share_floor


class TestFlipRootfind:
    def test_bs_gamma_sane(self):
        atm = gv._bs_gamma(100, 100, 0.2, 0.1)
        otm = gv._bs_gamma(100, 130, 0.2, 0.1)
        assert atm > otm > 0                       # gamma peaks ATM
        assert gv._bs_gamma(100, 100, 0.2, 0) == 0.0   # tau=0 → 0 (no blow-up)
        assert gv._bs_gamma(100, 100, 0, 0.1) == 0.0   # sigma=0 → 0

    def test_flip_found_and_stable(self):
        ch = _chain()
        f1 = gv.slide_flip(ch, 5000.0)["flip"]
        f2 = gv.slide_flip(ch, 5000.5)["flip"]
        assert f1 is not None and f2 is not None
        assert abs(f1 - f2) < 1.0                   # a sub-1pt move must NOT teleport the flip

    def test_flip_band_brackets_the_root(self):
        s = gv.slide_flip(_chain(), 5000.0)
        lo, hi = s["flip_band"]
        assert lo <= s["flip"] <= hi

    def test_flip_none_when_no_crossing(self):
        # all calls, same sign everywhere → GEX never crosses zero
        allcalls = [_c(k, "call", 1, 0.002, oi=1000, iv=0.15) for k in range(4900, 5101, 10)]
        assert gv.slide_flip(allcalls, 5000)["flip"] is None

    def test_regime_reads_gamma_sign_at_spot_not_spot_vs_flip(self):
        # Regression for the "stuck short_gamma" bug: a long-gamma ISLAND — calls
        # dominate at/below spot, puts dominate above — so net GEX at spot is POSITIVE
        # while the nearest zero-crossing (flip) sits ABOVE spot. The old spot-vs-flip
        # test mislabeled this short_gamma; the fix reads the sign AT SPOT → long_gamma.
        ch = []
        for k in range(4900, 5101, 10):
            g = 0.004 * math.exp(-((k - 5000) / 40) ** 2)
            call_oi, put_oi = (4000, 500) if k <= 5000 else (500, 4000)
            ch.append(_c(k, "call", 3, g, oi=call_oi))
            ch.append(_c(k, "put", 3, g, oi=put_oi))
        s = gv.slide_flip(ch, 5000.0)
        assert s["net_gex"] is not None and s["net_gex"] > 0      # long gamma AT spot
        assert s["flip"] is not None and s["flip"] > 5000.0       # yet the flip sits ABOVE
        assert s["regime"] == "long_gamma"                        # regime follows sign-at-spot

    def test_regime_matches_sign_of_net_gex_invariant(self):
        # General invariant the fix guarantees: regime is the sign of net_gex —
        # EXCEPT inside the tie deadband (H3), where a rounding-size residual of
        # two cancelling sides honestly reads "uncertain" instead of flipping.
        s = gv.slide_flip(_chain(), 5000.0)
        if s["net_gex"]:
            if s["regime"] == "uncertain":
                gross = gv._gross_gex_at(_chain(), 5000.0, s["basis"])
                assert abs(s["net_gex"]) < gv.REGIME_TIE_BAND * gross
            else:
                assert s["regime"] == ("long_gamma" if s["net_gex"] > 0 else "short_gamma")

    def test_regime_tie_deadband_names_a_tie(self):
        # H3: a balanced book (equal call/put mass at every strike) sums to a
        # rounding-size net whose SIGN is noise — the regime must abstain, not
        # stamp long/short off a 0.001 imbalance (strict >0 even read an exact
        # 0.0 as "short_gamma").
        ch = []
        for k in range(4900, 5101, 10):
            g = 0.004 * math.exp(-((k - 5000) / 40) ** 2)
            ch.append(_c(k, "call", 3, g, oi=2000))
            ch.append(_c(k, "put", 3, g, oi=2000))     # perfect mirror → net ≈ 0
        s = gv.slide_flip(ch, 5000.0)
        assert s["regime"] == "uncertain"
        assert s["net_gex"] is not None                # a READ — not the blackout "unknown"

    def test_regime_short_gamma_island_flip_below_spot(self):
        # MIRROR of the long-gamma island (the other polarity of the same bug): calls
        # dominate BELOW spot, puts dominate at/above spot, so net GEX at spot is
        # NEGATIVE while the nearest zero-crossing (flip) sits BELOW spot. The old
        # spot-vs-flip logic (spot >= flip → long) would mislabel this long_gamma; the
        # fix reads the sign AT SPOT → short_gamma. Guards the symmetric direction so a
        # regression can't reappear on only one side.
        ch = []
        for k in range(4900, 5101, 10):
            g = 0.004 * math.exp(-((k - 5000) / 40) ** 2)
            call_oi, put_oi = (4000, 500) if k < 5000 else (500, 4000)
            ch.append(_c(k, "call", 3, g, oi=call_oi))
            ch.append(_c(k, "put", 3, g, oi=put_oi))
        s = gv.slide_flip(ch, 5000.0)
        assert s["net_gex"] is not None and s["net_gex"] < 0      # short gamma AT spot
        assert s["flip"] is not None and s["flip"] < 5000.0       # yet the flip sits BELOW
        assert s["regime"] == "short_gamma"                       # regime follows sign-at-spot


class TestSignCrossCheck:
    def test_long_gamma_with_heavy_flow_marked_uncertain(self):
        r = gv.reconcile_sign("long_gamma", 0.8)
        assert r["sign"] == "uncertain" and r["agrees"] is False and r["confidence"] == 0.5

    def test_long_gamma_with_light_flow_ok(self):
        r = gv.reconcile_sign("long_gamma", 0.2)
        assert r["sign"] == "long_gamma" and r["agrees"] is True

    def test_short_gamma_downgraded_by_inverting_flow(self):
        # H5: +flow (customers BUYING calls / SELLING puts) inverts the assumed
        # dealer sign under EITHER regime — short_gamma is no longer exempt
        r = gv.reconcile_sign("short_gamma", 0.9)
        assert r["sign"] == "uncertain" and r["agrees"] is False

    def test_confirming_flow_never_trips(self):
        # H5: heavy NEGATIVE flow (customers selling calls / buying puts) CONFIRMS
        # the assumed dealer sign — the old abs() test read it as a conflict and
        # downgraded exactly the reads the tape had just corroborated
        r = gv.reconcile_sign("long_gamma", -0.9)
        assert r["sign"] == "long_gamma" and r["agrees"] is True and r["confidence"] == 1.0

    def test_lob_kind_keeps_unsigned_freight_train_test(self):
        # LOB tape: its sign carries no dealer-positioning info — heavy one-way
        # pressure EITHER way threatens a pin (old semantics, now lob-scoped)
        r = gv.reconcile_sign("long_gamma", -0.3, gv.FLOW_CONFLICT_LOB, "lob")
        assert r["sign"] == "uncertain" and r["agrees"] is False
        r2 = gv.reconcile_sign("short_gamma", 0.3, gv.FLOW_CONFLICT_LOB, "lob")
        assert r2["sign"] == "short_gamma"             # lob test stays long-gamma-scoped

    def test_unknown_flow_does_not_penalize(self):
        r = gv.reconcile_sign("long_gamma", None)
        assert r["agrees"] is None and r["confidence"] == 1.0


class TestBuildViews:
    def test_build_views_full_shape(self):
        v = gv.build_views(_chain(), 5000.0, aggressor_flow=0.2)
        # 07-09 zones swap: this fixture's zone-1 is a 110-pt smear, so the
        # mega-zone guard keeps the magnet on the argmax. "uncertain" allowed
        # since H3: this fixture's book is near-balanced at spot (a real tie).
        assert v["magnet"] == 5010 and v["regime"] in ("long_gamma", "short_gamma", "uncertain")
        assert v["slides"]["B_0dte"]["pin_argmax"] == 5010
        for key in ("A_flip", "B_0dte", "C_tenor", "D_volume", "E_sign"):
            assert key in v["slides"]

    def test_magnet_falls_back_to_flip_without_0dte(self):
        near_only = [c for c in _chain() if c["dte"] != 0]
        v = gv.build_views(near_only, 5000.0)
        assert v["slides"]["B_0dte"]["pin"] is None
        assert v["magnet"] == v["flip"]            # falls back to the flip

    def test_heavy_flow_downgrades_a_pin_regime(self):
        # force a long-gamma read, then a heavy flow must flip regime → uncertain
        v = gv.build_views(_chain(), 5000.0, aggressor_flow=0.9)
        if v["regime_raw"] == "long_gamma":
            assert v["regime"] == "uncertain" and v["sign_confidence"] == 0.5


# --------------------------------------------------------------------------- cache
class TestCache:
    def test_should_refresh_triggers(self):
        c = gv._Cache(contracts=[], spot=5000, sigma=20, anchor_spot=5000, ts=NOW, source="x")
        assert gv.should_refresh(None, NOW, 5000) is True                 # no cache
        assert gv.should_refresh(c, NOW, 5000) is False                   # fresh, no move
        old = NOW + timedelta(minutes=gv.REFRESH_AGE_MIN + 1)
        assert gv.should_refresh(c, old, 5000) is True                    # stale
        moved = 5000 + gv.REFRESH_MOVE_SIGMA * 20 + 1
        assert gv.should_refresh(c, NOW, moved) is True                   # big move

    def test_orchestrator_pulls_once_then_reprices(self):
        calls = {"n": 0}
        def lookup(t):
            calls["n"] += 1
            return {"contracts": _chain(), "spot": 5000.0}
        store = {}
        v1 = gv.GexBox("QQQ", now=NOW, live_spot=5000.0, chain_lookup=lookup,
                          spot_lookup=lambda t: 5000.0, cache=store)
        assert v1 and calls["n"] == 1
        # a tiny move within the band → re-price, NO new pull
        v2 = gv.GexBox("QQQ", now=NOW + timedelta(minutes=5), live_spot=5001.0,
                          chain_lookup=lookup, spot_lookup=lambda t: 5000.0, cache=store)
        assert v2 and calls["n"] == 1 and v2["meta"]["repriced"] is True
        # a big move → fresh pull
        big = 5000 + gv.REFRESH_MOVE_SIGMA * v1["meta"]["sigma"] + 5
        gv.GexBox("QQQ", now=NOW + timedelta(minutes=6), live_spot=big,
                     chain_lookup=lookup, spot_lookup=lambda t: 5000.0, cache=store)
        assert calls["n"] == 2

    def test_spy_proxy_rescales_strikes_into_index_space(self):
        # SPY chain at spot 500; index spot 5000 → scale 10 → magnet in index space.
        def spy_chain(t):
            assert t == "SPY"
            ch = []
            for k in range(488, 513):
                ch.append(_c(float(k), "call", 0, 0.02, vol=(4000 if k == 501 else 300)))
                ch.append(_c(float(k), "put", 0, 0.02, vol=300))
            return {"contracts": ch, "spot": 500.0}
        v = gv.GexBox("SPX", now=NOW, live_spot=5000.0, chain_lookup=spy_chain,
                         spot_lookup=lambda t: 5000.0, cache={})
        assert v and 4880 <= (v["magnet"] or 0) <= 5120     # rescaled into index strikes
        assert "spy_proxy" in v["meta"]["gex_source"]

    def test_orchestrator_none_when_chain_unavailable(self):
        assert gv.GexBox("QQQ", now=NOW, chain_lookup=lambda t: None,
                            spot_lookup=lambda t: None, cache={}) is None

    def test_atm_iv_prefers_call_side(self):
        # nearest-the-money contract is a (stale-quoted) put — the call must win
        cs = [_c(5000.0, "put", 0, 0.02, iv=0.05), _c(5002.0, "call", 0, 0.02, iv=0.11)]
        assert gv._atm_iv(cs, 5000.0) == 0.11
        # no calls carry IV → puts remain the honest fallback
        assert gv._atm_iv([_c(5000.0, "put", 0, 0.02, iv=0.09)], 5000.0) == 0.09

    def test_native_chain_primary_spy_fallback(self):
        # native lookup healthy → chain used as-is (already index space), labeled native
        def native(t):
            assert t == "SPX"
            ch = []
            for k in range(4988, 5013):
                ch.append(_c(float(k), "call", 0, 0.02, vol=(4000 if k == 5001 else 300)))
                ch.append(_c(float(k), "put", 0, 0.02, vol=300))
            return {"contracts": ch, "spot": 5000.0}
        def no_proxy(t):
            raise AssertionError("proxy path hit while native was healthy")
        v = gv.GexBox("SPX", now=NOW, live_spot=5000.0, chain_lookup=no_proxy,
                         native_lookup=native, spot_lookup=lambda t: 5000.0, cache={})
        assert v and v["meta"]["gex_source"] == "native"
        assert 4980 <= (v["magnet"] or 0) <= 5020           # unscaled index strikes
        # native pull fails → the SPY proxy serves the read, honestly labeled
        def spy_chain(t):
            assert t == "SPY"
            ch = []
            for k in range(488, 513):
                ch.append(_c(float(k), "call", 0, 0.02, vol=300))
                ch.append(_c(float(k), "put", 0, 0.02, vol=300))
            return {"contracts": ch, "spot": 500.0}
        v2 = gv.GexBox("SPX", now=NOW, live_spot=5000.0, chain_lookup=spy_chain,
                          native_lookup=lambda t: None, spot_lookup=lambda t: 5000.0, cache={})
        assert v2 and "spy_proxy" in v2["meta"]["gex_source"]


# --------------------------------------------------------------------------- pressure
class TestPressure:
    def test_large_random_chain_does_not_crash(self):
        import random
        rng = random.Random(7)
        big = []
        for _ in range(4000):
            k = rng.choice(range(4000, 6001, 5))
            big.append(_c(float(k), rng.choice(["call", "put"]), rng.randint(0, 7),
                          rng.random() * 0.004, oi=rng.randint(0, 5000), vol=rng.randint(0, 5000),
                          iv=0.1 + rng.random() * 0.3))
        v = gv.build_views(big, 5000.0, aggressor_flow=rng.uniform(-1, 1))
        assert "slides" in v and v["spot"] == 5000.0

    def test_degenerate_inputs_degrade_to_none_not_crash(self):
        assert gv.build_views([], 5000.0)["magnet"] is None
        assert gv.build_views([_c(5000, "call", 0, None, vol=0)], 5000.0)["magnet"] is None
        assert gv.slide_flip([_c(5000, "call", 0, 0.002)], 5000.0)["flip"] is None  # no weight
        assert gv.build_views(_chain(), 0.0)["magnet"] is None        # no spot


# --------------------------------------------------------------------- greek flows
class TestGreekFlows:
    def test_vanna_sign_by_moneyness(self):
        # OTM call (K>S) → vanna > 0 ; ITM call (K<S) → vanna < 0
        assert gv._bs_vanna(100, 130, 0.2, 0.1) > 0
        assert gv._bs_vanna(130, 100, 0.2, 0.1) < 0

    def test_greek_units_zero_on_degenerate(self):
        assert gv._bs_vanna(100, 100, 0.2, 0) == 0.0
        assert gv._bs_vanna(100, 100, 0, 0.1) == 0.0
        assert gv._bs_charm(100, 100, 0.2, 0) == 0.0
        assert gv._bs_charm(100, 100, 0, 0.1) == 0.0

    def test_dealer_sign_flips_calls_vs_puts(self):
        # identical strikes/weights/iv: puts carry the opposite dealer sign of calls
        calls = [_c(k, "call", 1, 0.002, oi=2000, iv=0.2) for k in range(4900, 5101, 10)]
        puts = [_c(k, "put", 1, 0.002, oi=2000, iv=0.2) for k in range(4900, 5101, 10)]
        vex_c = gv._net_greek_exposure_at(calls, 5000.0, "open_interest", gv._bs_vanna, gv._VEX_SCALE)
        vex_p = gv._net_greek_exposure_at(puts, 5000.0, "open_interest", gv._bs_vanna, gv._VEX_SCALE)
        assert vex_c is not None and abs(vex_c) > 0
        assert math.isclose(vex_p, -vex_c, rel_tol=1e-9)

    def test_slide_flows_shape_and_finite(self):
        f = gv.slide_flows(_chain(), 5000.0)
        assert f["vex"] is not None and f["cex"] is not None
        assert math.isfinite(f["vex"]) and math.isfinite(f["cex"])
        assert f["vex_sign"] in ("positive", "negative", "neutral")

    def test_cex_reads_0dte_volume_vex_reads_dated_oi(self):
        # CEX must come off the 0DTE/volume book, VEX off the 1-7DTE/OI book.
        f = gv.slide_flows(_chain(), 5000.0)
        assert f["cex_basis"] == "volume" and f["cex_tenor"] == "0DTE"
        assert f["vex_basis"] == "open_interest" and f["vex_tenor"] == "1-7DTE"

    def test_vex_is_none_when_only_0dte(self):
        # a 0DTE-only chain has ~no vega → VEX has no dated book to read → None
        zero_only = [c for c in _chain() if c["dte"] == 0]
        f = gv.slide_flows(zero_only, 5000.0)
        assert f["vex"] is None and f["cex"] is not None

    def test_charm_accelerates_into_the_close(self):
        # charm ∝ 1/τ: a skewed 0DTE book carries larger |CEX| at 20m to close than 200m
        zero = [_c(5010, "call", 0, 0.003, vol=5000, iv=0.2),
                _c(4990, "put", 0, 0.001, vol=500, iv=0.2)]
        near_bell = gv._net_greek_exposure_at(zero, 5000.0, "volume", gv._bs_charm, gv._CEX_SCALE, 20)
        midday = gv._net_greek_exposure_at(zero, 5000.0, "volume", gv._bs_charm, gv._CEX_SCALE, 200)
        assert abs(near_bell) > abs(midday)

    def test_flows_degrade_to_none(self):
        f = gv.slide_flows([], 5000.0)
        assert f["vex"] is None and f["vex_sign"] == "unknown"
        assert f["cex"] is None and f["cex_sign"] == "unknown"
        assert gv.slide_flows(_chain(), 0.0)["cex"] is None


# --------------------------------------------------------------------- gamma walls
class TestGammaWalls:
    def test_gamma_walls_on_correct_side(self):
        ch = _chain()
        cw = gv._gamma_wall(ch, 5000.0, "call", "open_interest")
        pw = gv._gamma_wall(ch, 5000.0, "put", "open_interest")
        assert cw is not None and cw >= 5000.0
        assert pw is not None and pw <= 5000.0

    def test_gamma_walls_and_flows_surface_in_build_views(self):
        v = gv.build_views(_chain(), 5000.0, aggressor_flow=0.2)
        assert "F_flows" in v["slides"]
        assert "vex" in v and "cex" in v and "vex_sign" in v
        b = v["slides"]["B_0dte"]
        assert "call_wall_gamma" in b and "put_wall_gamma" in b
        c = v["slides"]["C_tenor"]
        assert c["call_wall_gamma"] is None or c["call_wall_gamma"] >= 5000.0
        assert c["put_wall_gamma"] is None or c["put_wall_gamma"] <= 5000.0


def _split0(chain):
    zero, _ = gv.split_tenor(chain)
    return zero, 5000.0


def test_pin_profile_strength_and_concentration():
    cs = [{"strike": 7500, "right": "call", "gamma": 0.01, "volume": 5600, "dte": 0, "iv": .2},
          {"strike": 7480, "right": "put", "gamma": 0.01, "volume": 1400, "dte": 0, "iv": .2},
          {"strike": 7460, "right": "call", "gamma": 0.01, "volume": 2000, "dte": 0, "iv": .2}]
    pin, total, share = gv._pin_profile(cs, "volume")
    assert pin == 7500
    assert abs(total - 90.0) < 1e-9                 # Σ|γ·w| = .01×9000
    assert abs(share - 56.0 / 90.0) < 1e-9          # magnet's share of the pull
    out = gv.slide_0dte(cs, spot=7490.0)
    npin, ntotal, nshare = gv._net_pin_profile(cs, "volume", 7490.0)
    # ZONES (07-09): 7460+7500 calls join one zone (gap 40 = 2x median step 20)
    # whose volume-weighted center is 7490.18; the argmax stays 7500.
    assert npin == 7500                             # signed net argmax unchanged
    assert out["pin_argmax"] == 7500
    assert out["pin"] == 7490.18                    # LIVE pin = zone-1 center
    assert out["pin_total_gamma"] == round(ntotal, 4)
    assert abs(out["pin_top_share"] - nshare) < 1e-3
    assert out["pin_abs"] == 7500                   # unsigned read recorded alongside


# -------------------------------------------------- motion-pack raw material (P4)
class TestPinFieldStats:
    def _cs(self):
        # tiny deterministic book (iv=None → stored gamma, no BS reprice):
        # net map: 5000 call +12, 5020 call +2, 4980 put −2
        return [_c(5000, "call", 0, 0.004, vol=3000, iv=None),
                _c(5020, "call", 0, 0.002, vol=1000, iv=None),
                _c(4980, "put", 0, 0.002, vol=1000, iv=None)]

    def test_net_gex_by_strike_signed_map(self):
        cs = [_c(5000, "call", 0, 0.004, vol=1000, iv=None),
              _c(5000, "put", 0, 0.001, vol=1000, iv=None)]
        m = gv._net_gex_by_strike(cs, "volume", 5000.0)
        assert abs(m[5000] - 3.0) < 1e-9          # calls +, puts − on one strike
        assert gv._net_gex_by_strike(cs, "volume", 0.0) == {}
        assert gv._net_gex_by_strike([], "volume", 5000.0) == {}

    def test_net_pin_profile_unchanged_via_extraction(self):
        # the 3-tuple contract survives the _net_gex_by_strike refactor
        pin, total, share = gv._net_pin_profile(self._cs(), "volume", 5000.0)
        assert pin == 5000 and total == 16.0 and abs(share - 12.0 / 16.0) < 1e-9

    def test_wall_shares_exact(self):
        out = gv.slide_0dte(self._cs(), 5000.0)
        assert out["call_wall_gamma"] == 5000 and out["put_wall_gamma"] == 4980
        # total |γ·w| both sides = 12+2+2 = 16 → wall SHARES of the whole field
        assert out["call_wall_gamma_share"] == 0.75
        assert out["put_wall_gamma_share"] == 0.125

    def test_wall_share_invariant_to_volume_growth(self):
        # THE melt rationale: raw 0DTE gamma mass only grows with session volume —
        # doubling every weight must leave the SHARE untouched (raw mass doubles)
        zero, spot = _split0(_chain())
        a = gv.slide_0dte(zero, spot)
        doubled = [dict(c, volume=(c["volume"] or 0) * 2) for c in zero]
        b = gv.slide_0dte(doubled, spot)
        assert a["call_wall_gamma_share"] == b["call_wall_gamma_share"]
        assert a["put_wall_gamma_share"] == b["put_wall_gamma_share"]

    def test_pin_centroid_weighted_over_attractive_strikes(self):
        out = gv.slide_0dte(self._cs(), 5000.0)
        # centroid over net>0 strikes only: (5000·12 + 5020·2)/14 — the repelling
        # 4980 put never drags the magnet read
        assert out["pin_centroid"] == round(70040.0 / 14.0, 4)
        assert 5000 < out["pin_centroid"] < 5020

    def test_pin_centroid_none_when_all_repelling(self):
        rep = [_c(5000, "put", 0, 0.004, vol=9000, iv=None),
               _c(5020, "put", 0, 0.002, vol=1000, iv=None)]
        out = gv.slide_0dte(rep, 5000.0)
        assert out["pin_centroid"] is None
        # side sums still recorded: 5020 above (|−2|), K == spot excluded → below 0
        assert out["gamma_above_spot"] == 2.0 and out["gamma_below_spot"] == 0.0

    def test_pin_centroid_thin_support_reads_none(self):
        # storm-side (mostly repelling) book: the attract set is one near-cancel
        # strike carrying ~2.6% of the field — a centroid on that support flickers
        # on tiny prints (the argmax teleport reborn), so the read must go dark
        thin = [_c(5000, "put", 0, 0.004, vol=9000, iv=None),   # −36 repel
                _c(5020, "put", 0, 0.002, vol=1000, iv=None),   # −2 repel
                _c(5040, "call", 0, 0.001, vol=1000, iv=None)]  # +1 attract
        out = gv.slide_0dte(thin, 5000.0)
        assert out["pin_centroid"] is None
        # only the magnet read abstains — the side sums still record
        assert out["gamma_above_spot"] == 3.0 and out["gamma_below_spot"] == 0.0

    def test_gamma_above_below_excludes_spot_strike(self):
        out = gv.slide_0dte(self._cs(), 5000.0)
        # the big 5000 strike sits ON spot → excluded from both side sums
        assert out["gamma_above_spot"] == 2.0
        assert out["gamma_below_spot"] == 2.0

    def test_degenerate_keys_present_and_none(self):
        for out in (gv.slide_0dte([], 5000.0), gv.slide_0dte(self._cs(), 0.0)):
            for k in ("call_wall_gamma_share", "put_wall_gamma_share",
                      "pin_centroid", "gamma_above_spot", "gamma_below_spot"):
                assert k in out and out[k] is None


# ------------------------------------------------------------- shove test (P5)
class TestShove:
    def test_shove_none_without_sigma(self):
        assert gv.build_views(_chain(), 5000.0)["shove"] is None

    def test_shove_signed_margins_match_net_gex_at(self):
        ch = _chain()
        v = gv.build_views(ch, 5000.0, sigma=40.0)
        sh = v["shove"]
        assert sh is not None and sh["shove_sigma"] == gv.SHOVE_SIGMA
        basis = v["slides"]["A_flip"]["basis"]
        net0 = gv._net_gex_at(ch, 5000.0, basis)
        up = gv._net_gex_at(ch, 5020.0, basis)      # spot + ½σ
        dn = gv._net_gex_at(ch, 4980.0, basis)      # spot − ½σ
        assert sh["net_gex_spot"] == round(net0, 2)
        assert sh["shove_up_net_gex"] == round(up, 2)
        assert sh["shove_down_net_gex"] == round(dn, 2)
        # margins are the SIGNED distance from flipping, per |GEX at spot|
        assert sh["shove_up_margin"] == round(up / abs(net0), 4)
        assert sh["shove_down_margin"] == round(dn / abs(net0), 4)

    def test_shove_flip_detection_long_gamma_island(self):
        # calls dominate at/below spot, puts above (the island chain): a +½σ shove
        # lands deep in put-dominated territory and flips the book's sign
        ch = []
        for k in range(4900, 5101, 10):
            g = 0.004 * math.exp(-((k - 5000) / 40) ** 2)
            call_oi, put_oi = (4000, 500) if k <= 5000 else (500, 4000)
            ch.append(_c(k, "call", 3, g, oi=call_oi))
            ch.append(_c(k, "put", 3, g, oi=put_oi))
        v = gv.build_views(ch, 5000.0, sigma=120.0)   # +½σ = 5060
        sh = v["shove"]
        assert sh["net_gex_spot"] > 0                 # calming at spot (GRAVITY holds)
        assert sh["shove_up_net_gex"] < 0 and sh["up_flips"] is True
        assert sh["shove_up_margin"] < 0              # signed: already past the flip
        assert sh["down_flips"] is False and sh["shove_down_margin"] > 0

    def test_shove_margins_none_when_spot_gex_zero(self):
        # a perfectly balanced strike → net GEX exactly 0 at any S: margins have
        # no denominator, flips have no reference sign
        cs = [_c(5000, "call", 0, 0.002, vol=2000), _c(5000, "put", 0, 0.002, vol=2000)]
        sh = gv.build_views(cs, 5000.0, sigma=40.0)["shove"]
        assert sh is not None and sh["net_gex_spot"] == 0.0
        assert sh["shove_up_margin"] is None and sh["shove_down_margin"] is None
        assert sh["up_flips"] is False and sh["down_flips"] is False

    def test_gexbox_arms_shove_with_cache_sigma(self):
        v = gv.GexBox("QQQ", now=NOW, live_spot=5000.0,
                      chain_lookup=lambda t: {"contracts": _chain(), "spot": 5000.0},
                      spot_lookup=lambda t: 5000.0, cache={})
        assert v["shove"] is not None
        assert v["shove"]["shove_sigma"] == gv.SHOVE_SIGMA


# ------------------------------------------------------------ motion diffs (P4)
class TestGexMotion:
    """gex_motion — pure diff over diary rows; NOW (11:00 ET) is past the guard."""

    def _rec(self, **kw):
        base = {"source": "native", "flip": 5000.0, "pin_centroid": 5000.0,
                "pin_top_share": 0.30, "pin_total_gamma": 500.0,
                "call_wall_gamma": 5050.0, "put_wall_gamma": 4950.0,
                "call_wall_gamma_share": 0.20, "put_wall_gamma_share": 0.18,
                "gamma_above_spot": 30.0, "gamma_below_spot": 70.0}
        base.update(kw)
        return base

    def _row(self, minutes_ago, ticker="SPX", **kw):
        return {"ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
                "ticker": ticker, "gex_views": self._rec(**kw)}

    def test_first_tick_returns_none(self):
        assert gv.gex_motion([], self._rec(), sigma=20.0, now=NOW) is None

    def test_no_row_inside_lookback_window_returns_none(self):
        rows = [self._row(2.0), self._row(45.0)]   # too fresh / too old
        assert gv.gex_motion(rows, self._rec(), sigma=20.0, now=NOW) is None

    def test_source_prefix_mismatch_returns_none(self):
        # a native↔proxy degrade swap must never masquerade as motion
        rows = [self._row(10.0, source="spy_proxy×10.0332")]
        assert gv.gex_motion(rows, self._rec(source="native"), sigma=20.0, now=NOW) is None

    def test_source_prefix_matches_across_proxy_rescale(self):
        # the proxy scale drifts every pull — the prefix (before ×) is the identity
        rows = [self._row(10.0, source="spy_proxy×10.0332")]
        cur = self._rec(source="spy_proxy×10.0410", flip=5002.0)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=NOW)
        assert m is not None and m["flip_creep_sigma"] == 0.1

    def test_full_motion_diffs(self):
        rows = [self._row(10.0)]
        cur = self._rec(flip=5002.0, pin_centroid=5004.0, pin_top_share=0.35,
                        call_wall_gamma_share=0.16,
                        put_wall_gamma=4940.0, put_wall_gamma_share=0.30)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=NOW)
        assert m["lookback_min"] == 10.0 and m["morning_guard"] is False
        assert m["flip_creep_sigma"] == 0.1        # Δflip/σ = 2/20
        assert m["magnet_walk_sigma"] == 0.2       # Δcentroid/σ = 4/20
        assert m["grip_slope"] == 0.05             # concentration building
        assert m["call_wall_relocated"] is False
        assert m["call_wall_melt_share"] == -0.04  # negative = melting
        assert m["put_wall_relocated"] is True     # 4950 → 4940: a WALK
        assert m["put_wall_melt_share"] is None    # never read melt through a walk
        assert m["gamma_above_share"] == 0.3       # 30/(30+70)
        assert m["soft_side"] == "up"              # less mass above → runway up

    def test_picks_latest_qualifying_row(self):
        rows = [self._row(25.0, flip=4990.0), self._row(8.0, flip=4998.0)]
        m = gv.gex_motion(rows, self._rec(flip=5000.0), sigma=20.0, now=NOW)
        assert m["lookback_min"] == 8.0
        assert m["flip_creep_sigma"] == 0.1        # vs the 8-min row, not the 25-min

    def test_morning_guard_nulls_all_but_flip_creep(self):
        early = NOW.replace(hour=10, minute=0)     # 30 min after the open
        rows = [{"ts": (early - timedelta(minutes=10)).isoformat(),
                 "ticker": "SPX", "gex_views": self._rec()}]
        cur = self._rec(flip=5002.0, pin_centroid=5006.0, pin_top_share=0.40)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=early)
        assert m["morning_guard"] is True
        assert m["flip_creep_sigma"] == 0.1        # OI-backed flip survives the guard
        for k in ("call_wall_melt_share", "put_wall_melt_share",
                  "call_wall_relocated", "put_wall_relocated",
                  "magnet_walk_sigma", "grip_slope",
                  "soft_side", "gamma_above_share"):
            assert m[k] is None

    def test_missing_pin_total_gamma_raises_the_guard(self):
        # historical rows carry no pin_total_gamma — ratio reads stay dark, honestly
        rows = [self._row(10.0)]
        cur = self._rec(pin_total_gamma=None, flip=5001.0)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=NOW)
        assert m["morning_guard"] is True
        assert m["flip_creep_sigma"] == 0.05 and m["magnet_walk_sigma"] is None

    def test_non_spx_and_malformed_rows_skipped(self):
        rows = [self._row(10.0, ticker="XSP", flip=500.0),  # other price space
                {"garbage": True},                          # no ts / no gex_views
                {"ts": "not-a-time", "ticker": "SPX", "gex_views": self._rec()}]
        assert gv.gex_motion(rows, self._rec(), sigma=20.0, now=NOW) is None

    def test_missing_fields_degrade_to_none_not_crash(self):
        # pre-07-06 diary rows lack the new share/centroid fields entirely
        rows = [self._row(10.0, pin_centroid=None, call_wall_gamma=None)]
        cur = self._rec(gamma_above_spot=None)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=NOW)
        assert m is not None
        assert m["magnet_walk_sigma"] is None
        assert m["call_wall_relocated"] is None and m["call_wall_melt_share"] is None
        assert m["gamma_above_share"] is None and m["soft_side"] is None

    def test_sigma_missing_nulls_only_the_sigma_reads(self):
        rows = [self._row(10.0)]
        cur = self._rec(flip=5002.0, pin_centroid=5004.0)
        m = gv.gex_motion(rows, cur, sigma=None, now=NOW)
        assert m is not None
        assert m["flip_creep_sigma"] is None and m["magnet_walk_sigma"] is None
        assert m["grip_slope"] == 0.0              # non-σ reads still land

    def test_wall_relocation_tolerates_proxy_scale_drift(self):
        # spy_proxy strikes are rescaled by a FRESH scale every pull, so the
        # SAME physical strike drifts <1.5 pts of float between rows (07-01
        # diary: 37/37 consecutive proxy walls differed) — sub-tolerance drift
        # must read UNCHANGED or melt is permanently dead on degrade days
        rows = [self._row(10.0, source="spy_proxy×10.0332",
                          call_wall_gamma=5049.61)]
        cur = self._rec(source="spy_proxy×10.0410",
                        call_wall_gamma=5049.74, call_wall_gamma_share=0.16)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=NOW)
        assert m["call_wall_relocated"] is False
        assert m["call_wall_melt_share"] == -0.04
        # a genuine one-strike (>= 5 pt) move is still a WALK, never a melt
        cur2 = self._rec(source="spy_proxy×10.0410",
                         call_wall_gamma=5055.0, call_wall_gamma_share=0.16)
        m2 = gv.gex_motion(rows, cur2, sigma=20.0, now=NOW)
        assert m2["call_wall_relocated"] is True
        assert m2["call_wall_melt_share"] is None

    def test_flip_creep_crossing_swap_reads_none(self):
        # multi-crossing 0DTE curves: the nearest-spot flip TELEPORTS between
        # island borders when spot drifts across a midline — an over-cap jump
        # is a REGIME-BORDER swap, not creep, and it must not ride through the
        # morning guard as velocity
        rows = [self._row(10.0)]
        swap = self._rec(flip=5090.0)              # 90 pts / σ20 = 4.5σ "creep"
        m = gv.gex_motion(rows, swap, sigma=20.0, now=NOW)
        assert m is not None and m["flip_creep_sigma"] is None
        crawl = self._rec(flip=5008.0)             # 0.4σ — plausible creep
        m2 = gv.gex_motion(rows, crawl, sigma=20.0, now=NOW)
        assert m2["flip_creep_sigma"] == 0.4

    def test_morning_guard_covers_the_prior_endpoint(self):
        # 10:31 vs a 10:05 prior: NOW clears the 60-min guard but the COMPARED
        # row was computed on the thin book the guard exists to distrust — the
        # diff must stay guarded or the first ~30 post-guard minutes read pure
        # volume fill-in as melt/walk every session
        late_now = NOW.replace(hour=10, minute=31)
        rows = [{"ts": late_now.replace(minute=5).isoformat(),
                 "ticker": "SPX", "gex_views": self._rec()}]
        cur = self._rec(flip=5002.0, call_wall_gamma_share=0.16)
        m = gv.gex_motion(rows, cur, sigma=20.0, now=late_now)
        assert m["morning_guard"] is True
        assert m["flip_creep_sigma"] == 0.1        # OI-backed flip still reads
        assert m["call_wall_melt_share"] is None


def test_gexbox_default_spot_lookup_regression(monkeypatch):
    # REGRESSION: the restructure once dropped _live_index_spot — every production
    # scan (which uses the DEFAULT spot_lookup) would then NameError and silently
    # go dark. This exercises GexBox exactly as production calls it.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import lefteye_fetcher
    monkeypatch.setattr(lefteye_fetcher, "live_spot", lambda t: 7500.0)
    # Asymmetric call/put OI: a perfectly symmetric book nets to an all-zero GEX curve
    # (calls and puts cancel at every strike) and would have NO real magnet or flip —
    # it only ever produced one via the old fabricated-root bug. Real chains are skewed,
    # so weight calls heavier here to give a genuine attractive magnet.
    chain = {"spot": 750.0, "contracts": [
        {"right": r, "strike": k, "dte": 0, "iv": 0.2, "gamma": 0.01,
         "open_interest": 900 if r == "call" else 300, "volume": 2000}
        for k in (748, 750, 752) for r in ("call", "put")]}
    now = datetime(2026, 7, 6, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    v = gv.GexBox("SPX", now=now, live_spot=7500.0,
                  chain_lookup=lambda t: chain, cache={})
    assert v is not None and v.get("magnet") is not None
    assert callable(gv._live_index_spot)


# --------------------------------------------------------------- HIGH-priority fixes
class TestLiveClockWalls:
    """#1 — the 0DTE gamma walls must reprice on the LIVE minutes-to-close clock,
    not freeze at the fetch-time (~12:45pm) clock."""

    def test_gamma_wall_follows_the_clock(self):
        # Same two 0DTE call strikes, volume-weighted so the 60-pt-OTM 6040 wins at the
        # half-session FLOOR clock (gamma still spread), but ATM 6005 wins into the bell
        # (gamma collapses OTM). A frozen-clock wall could never move; the live one does.
        spot = 6000.0
        zero = [_c(6005, "call", 0, g=0.001, vol=1000, iv=0.15),
                _c(6040, "call", 0, g=0.001, vol=2000, iv=0.15)]
        wall_floor = gv._gamma_wall(zero, spot, "call", "volume", minutes_to_close=None)
        wall_bell = gv._gamma_wall(zero, spot, "call", "volume", minutes_to_close=15)
        assert wall_floor == 6040        # far strike wins with a whole session of tau
        assert wall_bell == 6005         # ATM concentrates 15 min from the close
        assert wall_floor != wall_bell   # the wall genuinely tracks the clock now

    def test_live_gamma_clamps_negative_stored_sentinel(self):
        # a -999 broker sentinel in stored gamma (no IV to reprice) → 0, never fake mass
        c = {"strike": 6000, "right": "call", "dte": 0, "gamma": -999.0, "iv": 0}
        assert gv._live_gamma(c, 6000.0, None) == 0.0

    def test_live_gamma_reprices_when_iv_present(self):
        c = {"strike": 6000, "right": "call", "dte": 0, "gamma": 0.001, "iv": 0.15}
        g = gv._live_gamma(c, 6000.0, minutes_to_close=15)
        assert g > 0 and g != 0.001      # BS reprice, not the stored greek


class TestBlackoutGuard:
    """#3 — a chain with strikes but no usable IV is a data BLACKOUT, not short-gamma."""

    def test_slide_flip_blackout_returns_unknown(self):
        spot = 6000.0
        chain = []
        for k in range(5900, 6101, 10):
            chain.append(_c(k, "call", 0, g=0.0, oi=800, vol=1000, iv=0))
            chain.append(_c(k, "put", 0, g=0.0, oi=800, vol=1000, iv=0))
        out = gv.slide_flip(chain, spot, minutes_to_close=60)
        assert out["regime"] == "unknown"          # NOT a confident "short_gamma"
        assert out["flip"] is None                 # NOT a fake flip pinned to spot
        assert out["net_gex"] is None

    def test_slide_flip_with_iv_still_reads_regime(self):
        # sanity: a real IV-bearing book still produces a READ (guard isn't
        # over-eager) — "uncertain" is a read (H3 tie), "unknown" is the blackout
        out = gv.slide_flip(_chain(5000.0), 5000.0, minutes_to_close=60)
        assert out["regime"] in ("long_gamma", "short_gamma", "uncertain")


class TestFlipResolution:
    """#5 — dense-near-spot grid + bisection: resolve near-spot islands and place the
    flip tightly; never fabricate a root on an identically-zero curve."""

    def test_grid_is_dense_near_spot_and_still_wide(self):
        grid = gv._flip_grid(6000.0)
        inner = sorted(g for g in grid if abs(g - 6000.0) <= 6000.0 * gv._FLIP_INNER)
        gaps = [b - a for a, b in zip(inner, inner[1:])]
        assert max(gaps) <= 6000.0 * gv._FLIP_INNER_STEP + 0.5   # ~half a 5-pt strike
        assert min(grid) <= 6000.0 * (1 - gv._FLIP_SPAN) + 1     # full ±20% span kept
        assert max(grid) >= 6000.0 * (1 + gv._FLIP_SPAN) - 1

    def test_flip_band_is_tight_after_bisection(self):
        spot = 6000.0
        ch = []
        for k in range(5700, 6301, 10):
            ch.append(_c(k, "call", 0, g=0.0, oi=(1500 if k > spot else 200), iv=0.15))
            ch.append(_c(k, "put", 0, g=0.0, oi=(1500 if k < spot else 200), iv=0.15))
        flip, band = gv._flip_rootfind(ch, spot, "open_interest", minutes_to_close=60)
        assert flip is not None
        assert band is not None and (band[1] - band[0]) <= 5.0   # tight, not a ~46-pt cell

    def test_flip_rootfind_all_zero_curve_returns_none(self):
        spot = 6000.0
        ch = []
        for k in range(5900, 6101, 10):   # symmetric: calls == puts → net GEX ≡ 0
            ch.append(_c(k, "call", 0, g=0.0, oi=800, iv=0.15))
            ch.append(_c(k, "put", 0, g=0.0, oi=800, iv=0.15))
        flip, band = gv._flip_rootfind(ch, spot, "open_interest", minutes_to_close=60)
        assert flip is None and band is None


class TestDatedWallStability:
    """#1 follow-up — the LIVE-clock reprice is scoped to 0DTE (slide B) only. Slide-C
    dated 'terrain' walls stay on stored gamma so they don't drift intraday."""

    def test_dated_wall_uses_stored_gamma(self):
        # stored gamma favors the OTM 6040; a live BS reprice would favor ATM 6005.
        dated = [_c(6005, "call", 3, g=0.001, oi=1000, iv=0.15),
                 _c(6040, "call", 3, g=0.009, oi=1000, iv=0.15)]
        assert _gw(dated, reprice=False) == 6040     # stored basis (stable slide-C)
        assert _gw(dated, reprice=True) == 6005       # live BS basis (slide-B 0DTE)

    def test_dated_wall_ignores_clock(self):
        dated = [_c(6005, "call", 3, g=0.001, oi=1000, iv=0.15),
                 _c(6040, "call", 3, g=0.009, oi=1000, iv=0.15)]
        assert _gw(dated, reprice=False, mtc=None) == _gw(dated, reprice=False, mtc=15)

    def test_dated_wall_clamps_negative_stored_sentinel(self):
        dated = [_c(6005, "call", 3, g=-999.0, oi=1000, iv=0),
                 _c(6040, "call", 3, g=0.002, oi=1000, iv=0)]
        assert _gw(dated, reprice=False) == 6040     # -999 clamped to 0, skipped


def _gw(contracts, *, reprice, mtc=None):
    return gv._gamma_wall(contracts, 6000.0, "call", "open_interest", mtc, reprice=reprice)


# -------------------------------------------------- magnet zones (2026-07-09)
class TestPinZones:
    """pin_zones — gap-clustered magnet groups; the LIVE pin is zone-1's center."""

    def test_two_zones_ranked_with_sides(self):
        # two clusters around spot 7510: {7495,7500,7505} below, {7545,7550,7555} above
        m = {7495.0: 2.0, 7500.0: 6.0, 7505.0: 2.0,
             7545.0: 3.0, 7550.0: 8.0, 7555.0: 3.0}
        zones, contested = gv.pin_zones(m, 7510.0)
        assert len(zones) == 2
        assert zones[0]["side"] == "above" and zones[1]["side"] == "below"
        assert zones[0]["share"] + zones[1]["share"] == 1.0
        # contested: 10/24 vs 14/24 → ratio 0.714 < 0.8 → not contested
        assert contested is False

    def test_contested_when_rival_within_80pct(self):
        # full 5-pt grid (repelling in between anchors the median step at 5)
        m = {7500.0 + 5 * i: -0.5 for i in range(11)}
        m[7500.0], m[7550.0] = 9.0, 8.0          # ratio 0.89, 50 pts apart
        zones, contested = gv.pin_zones(m, 7510.0)
        assert len(zones) == 2 and contested is True

    def test_one_hole_joins_two_holes_split(self):
        # full 5-pt grid: 7500-(hole 7505 repels)-7510 joins (gap 10 = 2x step 5);
        # 7510 → 7525 (gap 15 > 10) splits
        m = {7495.0: 1.0, 7500.0: 5.0, 7505.0: -1.0, 7510.0: 4.0,
             7515.0: -1.0, 7520.0: -1.0, 7525.0: 6.0, 7530.0: -2.0}
        zones, _ = gv.pin_zones(m, 7515.0)
        lohi = sorted((z["lo"], z["hi"]) for z in zones)
        assert (7495.0, 7510.0) in lohi and (7525.0, 7525.0) in lohi

    def test_proxy_grid_spacing_survives(self):
        # SPY-proxy rescale ≈10.4-pt non-integer grid — a fixed 5-pt rule shatters it
        m = {7489.6: 3.0, 7500.0: 7.0, 7510.4: 3.0}
        zones, _ = gv.pin_zones(m, 7505.0)
        assert len(zones) == 1 and zones[0]["n"] == 3

    def test_all_repelling_empty(self):
        assert gv.pin_zones({7500.0: -5.0, 7510.0: -1.0}, 7505.0) == ([], False)

    def test_zone1_exempt_from_floor_rivals_pruned(self):
        # smear: leader holds just 9% — must still exist; 4% rival pruned by floor
        m = {7400.0 + 5 * i: 1.0 for i in range(20)}      # one big smear zone
        m[7700.0] = 21.0 / 0.96 * 0.04                    # tiny far blip (<10%)
        zones, _ = gv.pin_zones(m, 7450.0)
        assert zones[0]["share"] > 0.9                    # smear zone dominates
        assert all(z["share"] >= gv.ZONE_FLOOR for z in zones[1:])

    def test_at_side_when_spot_inside_zone(self):
        m = {7495.0: 4.0, 7500.0: 6.0, 7505.0: 4.0}
        zones, _ = gv.pin_zones(m, 7500.0)
        assert zones[0]["side"] == "at"

    def test_cap_four_zones(self):
        # dense 5-pt grid, six well-separated attractive singletons → cap at 4
        m = {7300.0 + 5 * i: -0.1 for i in range(61)}
        for base in (7300, 7360, 7420, 7480, 7540, 7600):
            m[float(base)] = 5.0
        zones, _ = gv.pin_zones(m, 7450.0)
        assert len(zones) == 4

    def test_slide_0dte_live_swap_and_argmax_recorded(self):
        cs = [{"strike": 7495, "right": "call", "gamma": 0.01, "volume": 2000, "dte": 0, "iv": .2},
              {"strike": 7500, "right": "call", "gamma": 0.01, "volume": 6000, "dte": 0, "iv": .2},
              {"strike": 7505, "right": "call", "gamma": 0.01, "volume": 2000, "dte": 0, "iv": .2}]
        out = gv.slide_0dte(cs, spot=7490.0)
        assert out["pin_argmax"] == 7500.0
        assert out["pin"] == out["pin_zones"][0]["center"]
        assert out["zone1_share"] == out["pin_zones"][0]["share"]
        assert 7495.0 < out["pin"] < 7505.0        # weighted center inside the band

    @staticmethod
    def _rival_chain(v1, v2):
        # full 5-pt grid 7500-7550: repelling puts in between anchor the median
        # step at 5 (splitting the poles into two zones), big call volume at the
        # 7500/7550 poles — the 2026-07-09 two-magnet day in miniature
        cs = [{"strike": 7500 + 5 * i, "right": "put", "gamma": 0.005,
               "volume": 300, "dte": 0, "iv": .2} for i in range(11)]
        cs.append({"strike": 7500, "right": "call", "gamma": 0.01, "volume": v1, "dte": 0, "iv": .2})
        cs.append({"strike": 7550, "right": "call", "gamma": 0.01, "volume": v2, "dte": 0, "iv": .2})
        return cs

    def test_contested_magnet_stands_between_rivals(self):
        # H4: two tight rival zones 50 pts apart with near-equal pull — the live
        # magnet must stand BETWEEN them (share-weighted), not teleport to
        # whichever zone won this scan's coin-flip
        a = gv.slide_0dte(self._rival_chain(10000, 9800), spot=7525.0)
        b = gv.slide_0dte(self._rival_chain(9800, 10000), spot=7525.0)   # ownership flips
        for out in (a, b):
            assert out["pin_contested"] is True
            assert out["pin_blended"] is not None
            assert 7500.0 < out["pin"] < 7550.0            # in the middle, not at a pole
        # ownership flip moves the blended magnet a few points, not the full gap
        assert abs(a["pin"] - b["pin"]) < 15.0

    def test_uncontested_magnet_keeps_zone1_center(self):
        # below the rival threshold the blend must not engage (pin_blended None)
        out = gv.slide_0dte(self._rival_chain(10000, 5000), spot=7525.0)
        assert out["pin_contested"] is False and out["pin_blended"] is None
        assert out["pin"] == 7500.0

    def test_churned_strike_keeps_gamma_weight(self):
        # H10: a round-tripped strike (heavy two-way flow, net ≈ 0) must keep a
        # floor of its GROSS hedge mass in the signed field, not vanish
        cs = [{"strike": 7500, "right": "call", "gamma": 0.01, "volume": 5000, "dte": 0, "iv": .2,
               "net_flow": 0.0, "flow_buy": 400000.0, "flow_sell": 400000.0},
              {"strike": 7550, "right": "call", "gamma": 0.01, "volume": 1000, "dte": 0, "iv": .2,
               "net_flow": 50000.0, "flow_buy": 60000.0, "flow_sell": 10000.0}]
        gv.slide_0dte(cs, spot=7520.0)
        assert cs[0]["signed_weight"] == gv.CHURN_FLOOR * 800000.0   # floored, not 0
        assert cs[1]["signed_weight"] == 50000.0                     # |net| still leads
        # fail-open: no buy/sell annotation → plain |net|, exactly as before
        cs2 = [{"strike": 7500, "right": "call", "gamma": 0.01, "volume": 5000, "dte": 0, "iv": .2,
                "net_flow": -1200.0}]
        gv.slide_0dte(cs2, spot=7500.0)
        assert cs2[0]["signed_weight"] == 1200.0


class TestGammaTape:
    """GAMMA-OWNERSHIP TAPE (2026-07-13, shadow) — the axis `aggressor_flow` cancels itself
    out on. These tests are the reason the axis exists; if any of them is deleted, the bug
    it names comes straight back."""

    @staticmethod
    def _agg_flow(cb, cs, pb, ps):
        """The LIVE aggressor_flow formula, verbatim (native_gex_feed.py:266), so the
        cancellation is asserted against the real thing rather than a paraphrase."""
        gross = cb + cs + pb + ps
        return ((cb - cs) + (ps - pb)) / gross if gross > 0 else None

    def test_both_wings_does_not_cancel(self):
        # THE THESIS, IN ONE ASSERTION. Customers buy 1000 calls AND 1000 puts at the money:
        # the dealer has just sold 2000 options and is maximally SHORT gamma — the canonical
        # accelerant day. aggressor_flow reads exactly 0.00 (dead centre of "no signal")
        # because customer call-buying and customer put-buying enter with OPPOSITE signs and
        # annihilate. gamma_tape, a SUM, reads +1.0.
        assert self._agg_flow(cb=500_000, cs=0, pb=500_000, ps=0) == 0.0

        cs_ = [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                "flow_buy_q": 1000, "flow_sell_q": 0, "flow_buy": 500_000, "flow_sell": 0},
               {"strike": 7500, "right": "put", "gamma": 0.01, "dte": 0, "iv": .2,
                "flow_buy_q": 1000, "flow_sell_q": 0, "flow_buy": 500_000, "flow_sell": 0}]
        t = gv.gamma_tape(cs_, spot=7500.0)
        assert t is not None
        assert t["gamma_tape"] == 1.0        # the book shed 100% of the gamma that traded

    def test_sign_convention(self):
        def tape(bq, sq):
            cs_ = [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                    "flow_buy_q": bq, "flow_sell_q": sq}]
            return gv.gamma_tape(cs_, spot=7500.0)["gamma_tape"]
        assert tape(1000, 0) > 0             # customers net BUY  → hedged book sheds gamma
        assert tape(0, 1000) < 0             # customers net SELL → hedged book absorbs gamma
        assert tape(500, 500) == 0.0         # balanced

    def test_contracts_not_dollars(self):
        # THE FATAL WEIGHTING BUG, nailed shut. A 600-pt-ITM call carries maximum premium and
        # ~zero gamma; its SAME-STRIKE put carries ~zero premium and the IDENTICAL gamma (BS Γ
        # is right-independent). Buy the call, sell the put: the DOLLAR tape reads strongly
        # positive (premium is all on the call) while the true contract-gamma tape is flat.
        cs_ = [{"strike": 6900, "right": "call", "gamma": 0.0001, "dte": 0, "iv": .2,
                "flow_buy_q": 100, "flow_sell_q": 0,
                "flow_buy": 6_800_000.0, "flow_sell": 0.0},          # huge premium
               {"strike": 6900, "right": "put", "gamma": 0.0001, "dte": 0, "iv": .2,
                "flow_buy_q": 0, "flow_sell_q": 100,
                "flow_buy": 0.0, "flow_sell": 500.0}]                # ~no premium
        t = gv.gamma_tape(cs_, spot=7500.0)
        # identical gamma, one bought and one sold in equal size ⇒ no net gamma changed hands
        assert abs(t["gamma_tape"]) < 1e-9
        # ...but the dollar weighting says the book shed nearly all its gamma. It is wrong.
        assert t["gamma_tape_dollar"] > 0.99
        assert t["gamma_tape"] != t["gamma_tape_dollar"]

    def test_not_derived_from_signed_weight(self):
        # H10's pin weight is max(|net_flow|, CHURN_FLOOR·gross) — an ABSOLUTE value. It is a
        # MAGNITUDE and destroys the very sign this axis exists to carry, so the two must never
        # be "unified" by a future refactor. Proof: flip the book from customers-buying to
        # customers-selling. The pin weight |net_flow| is IDENTICAL across the two; gamma_tape
        # flips sign. They are answering different questions.
        def book(bq, sq, net_d):
            return [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                     "flow_buy_q": bq, "flow_sell_q": sq, "net_flow": net_d,
                     "flow_buy": max(net_d, 0.0), "flow_sell": max(-net_d, 0.0)}]
        bought = gv.gamma_tape(book(1000, 0, 500_000.0), spot=7500.0)
        sold = gv.gamma_tape(book(0, 1000, -500_000.0), spot=7500.0)
        assert bought["gamma_tape"] > 0.9 and sold["gamma_tape"] < -0.9   # opposite signs
        assert abs(500_000.0) == abs(-500_000.0)          # ...while |net_flow| cannot tell them apart

    def test_marginal_is_ratio_of_deltas(self):
        # THE WIND-CHANGE SIGNAL. Cumulative (900 buy, 100 sell) → now (1000, 1000): the day's
        # LEVEL is 0/2000 = 0.0 ("nothing happening"), but everything that traded in the last
        # interval was selling — the marginal is −0.8. A delta-of-the-ratio would read ~0 and
        # the turn would be invisible. This is why the cumulative can never call a wind change.
        prev = {"ts": NOW - timedelta(minutes=10), "q": {(7500.0, "call"): (900.0, 100.0)}}
        cs_ = [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                "flow_buy_q": 1000, "flow_sell_q": 1000}]
        t = gv.gamma_tape(cs_, spot=7500.0, prev=prev, now=NOW)
        assert t["gamma_tape"] == 0.0                     # the day says "calm"
        assert t["gamma_tape_60m"] == -0.8                # the last 10 minutes say "selling"

    def test_marginal_clamps_provider_restatement(self):
        # a restatement (cumulative goes DOWN) must yield Δ=0, never negative volume
        prev = {"ts": NOW - timedelta(minutes=10), "q": {(7500.0, "call"): (5000.0, 5000.0)}}
        cs_ = [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                "flow_buy_q": 100, "flow_sell_q": 100}]
        t = gv.gamma_tape(cs_, spot=7500.0, prev=prev, now=NOW)
        assert t["gamma_tape_60m"] is None               # all deltas clamp to 0 → thin → abstain

    def test_marginal_abstains_without_a_clock(self):
        # a delta over an unknown interval is not a rate
        prev = {"ts": NOW - timedelta(minutes=10), "q": {(7500.0, "call"): (0.0, 0.0)}}
        cs_ = [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                "flow_buy_q": 1000, "flow_sell_q": 0}]
        assert gv.gamma_tape(cs_, spot=7500.0, prev=prev, now=None)["gamma_tape_60m"] is None

    def test_fail_open_never_fabricates_zero(self):
        # no tape ⇒ UNKNOWN, never a balanced 0.0 (a fabricated zero reads as "measured, calm")
        assert gv.gamma_tape([{"strike": 7500, "right": "call", "gamma": 0.01,
                               "dte": 0, "iv": .2}], spot=7500.0) is None
        assert gv.gamma_tape([], spot=7500.0) is None
        assert gv.gamma_tape([{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0,
                               "iv": .2, "flow_buy_q": 1, "flow_sell_q": 1}], spot=0) is None
        # a broker −999 failed-solve sentinel contributes no mass, it does not become a signal
        assert gv.gamma_tape([{"strike": 7500, "right": "call", "gamma": -999, "dte": 0,
                               "iv": 0, "flow_buy_q": 1000, "flow_sell_q": 0}],
                             spot=7500.0) is None

    def test_bounds_and_coverage(self):
        cs_ = [{"strike": 7500, "right": "call", "gamma": 0.01, "dte": 0, "iv": .2,
                "flow_buy_q": 700, "flow_sell_q": 300, "flow_mid_q": 200, "flow_unk_q": 100}]
        t = gv.gamma_tape(cs_, spot=7500.0)
        assert -1.0 <= t["gamma_tape"] <= 1.0
        assert t["coverage"] == round(1000 / 1300, 4)    # the censored ~30% is PUBLISHED

    def test_dormant_by_design(self):
        # the dead-man switch: nothing may read a word off an uncalibrated axis
        assert gv.GAMMA_TAPE_CONSUME is False
        assert gv.TAPE_TRIP_HI is None and gv.TAPE_TRIP_LO is None
        assert gv.tape_word({"gamma_tape_60m": 0.9, "coverage": 0.9}) is None


class TestGuardAudit:
    """The dead guard must be impossible to hide. This is the generalizable fix: the bug was
    never the 0.6 threshold — it was that a guard could sit at 0 trips for 671 rows and no
    number anywhere said so."""

    def test_declares_the_real_dead_guard(self):
        import lefteye_guard_audit as ga
        # the real live distribution: aggressor_flow tops out at 0.441 against a 0.6 trip
        rows = [{"gex_views": {"aggressor_flow": 0.02, "veto_flow_source": "options",
                               "sign_agrees": True, "sign_trip": None}} for _ in range(250)]
        rows[0]["gex_views"]["aggressor_flow"] = 0.441     # the observed maximum, ever
        v = {g["guard"]: g for g in ga.audit(rows)}["slide_E_options"]
        assert v["status"] == "DEAD"
        assert v["n_trip"] == 0
        # judged on p95, not max: the single 0.441 outlier reaches 73% of the trip level, but
        # the TYPICAL reading is nowhere near it — one lucky print must not rescue a dead guard
        assert v["max_headroom"] > 0.7 and v["p95_headroom"] < 0.5
        assert "CANNOT FIRE" in v["note"]

    def test_a_live_guard_reads_ok(self):
        import lefteye_guard_audit as ga
        rows = [{"gex_views": {"aggressor_flow": 0.1, "veto_flow_source": "options",
                               "sign_agrees": True, "sign_trip": None}} for _ in range(250)]
        for r in rows[:20]:
            r["gex_views"].update({"aggressor_flow": 0.7, "sign_agrees": False,
                                   "sign_trip": "options_sign_inversion"})
        v = {g["guard"]: g for g in ga.audit(rows)}["slide_E_options"]
        assert v["status"] == "OK" and v["n_trip"] == 20
