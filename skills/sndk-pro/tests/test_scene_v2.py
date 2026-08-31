"""Scene payload v2 (sr-2, 2026-08-02 blueprint) — the new blocks.

Every block is measured-or-absent: these tests pin both faces — the block
appears with clean sources, and VANISHES (never nulls, never zeros) without
them. That is the omit-never-null directive as executable rule.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import re
from pathlib import Path

import pytest

import sndk_read as SR

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 31, 12, 0, tzinfo=ET)


def rich_row(ts=None, spot=1200.0, sigma=100.0, **kw):
    """A ROW_V 3 diary row with every payload-v2 source populated."""
    row = {
        "ts": (ts or T0).isoformat(), "ticker": "SNDK", "spot": spot,
        "sigma": sigma, "sigma_live": sigma, "atm_iv": kw.pop("atm_iv", 1.5875),
        "regime": "trending", "gamma_sign": "negative",
        "gamma_flip": kw.pop("gamma_flip", 1206.0),
        "call_wall": 1300.0, "put_wall": 1100.0,
        "prior_close": 1250.0,
        "vwap": kw.pop("vwap", 1212.0),
        "iv_skew": kw.pop("iv_skew", {"down_share": 0.55, "skew_pts": 8.0,
                                      "put_side_iv": 1.7, "call_side_iv": 1.5,
                                      "n_down": 5, "n_up": 5, "skew_v": 1}),
        "flows_front": kw.pop("flows_front",
                              {"cex": -4_276_191.0, "vex": 255_992.0,
                               "charm_wall": 1370.0, "vanna_wall": 1300.0,
                               "basis": "open_interest", "n": 200,
                               "clock": "front_book", "flows_v": 1}),
        "range_ruler": kw.pop("range_ruler",
                              {"em_points": 40.0, "quality": "ok"}),
        "profile_ladder": kw.pop("profile_ladder",
                                 {"ct": 1231.0, "hvl": 1206.0, "pt": 1181.0,
                                  "state": "negative transition"}),
        "dex_views": kw.pop("dex_views", {"net_dex_total": 3.93e9}),
        "gex_views": {
            "magnet": 1300.0, "front_dte": 3,
            "mass_by_strike": kw.pop("mass", [[1300, 60.0], [1100, 30.0],
                                              [1200, 10.0]]),
            "vol_gross_by_strike": kw.pop("vol_gross", [[1300, 5000],
                                                        [1100, 2000]]),
            # a realistic $5 grid: the meaningful walls ride on a dense field
            # of sub-floor strikes so cluster_walls infers the true step
            "net_by_strike": kw.pop("nbs", (
                [[1100, -6.0], [1150, -7.0], [1240, 8.0], [1245, 7.0],
                 [1300, 6.5]]
                + [[k, 0.1] for k in range(1105, 1300, 5)
                   if k not in (1150, 1240, 1245)])),
            "shove": {"shove_up_margin": 2.0, "shove_down_margin": 0.2},
        },
    }
    row.update(kw)
    return row


def scene_of(row, rows=None):
    rows = rows or [row]
    return SR.build_scene(row, SR.magnet_band(row), [], rows, T0)


# --- scale.expected_move_today_asym -----------------------------------------
def test_asym_expected_move_splits_the_priced_budget_by_iv_skew():
    sc = scene_of(rich_row())
    aem = sc["scale"]["expected_move_today_asym"]
    # asymmetry REALLOCATES the ruler's 2·em budget, never adds to it
    assert aem["down_dollars"] + aem["up_dollars"] == pytest.approx(80.0)
    assert aem["down_dollars"] == pytest.approx(44.0)   # d=0.55
    assert aem["skewed_toward"] == "downside"
    # sr-8: `derived_from` was one frozen string; the doctrine names the source
    assert "derived_from" not in aem
    assert "put/call IV skew" in SR._DOCTRINE


def test_asym_expected_move_absent_without_skew_or_em():
    sc = scene_of(rich_row(iv_skew=None))
    assert "expected_move_today_asym" not in sc["scale"]
    sc = scene_of(rich_row(range_ruler={"em_points": None}))
    assert "expected_move_today_asym" not in sc["scale"]


def test_asym_expected_move_balanced_band_is_named_balanced():
    sc = scene_of(rich_row(iv_skew={"down_share": 0.5}))
    assert sc["scale"]["expected_move_today_asym"]["skewed_toward"] == "balanced"


# --- price.vwap_minus_live_spot_sigma ---------------------------------------
def test_vwap_ships_as_sigma_distance():
    """sr-7: vwap is a live-tape level, so it is the one distance still measured
    from the LIVE spot while the book-derived blocks moved to the book's own
    spot. sr-8: the name now carries the SUBTRACTION as well as the ruler —
    vwap 1212 over spot 1200 on a 100 sigma is +0.12, positive because vwap is
    ABOVE price."""
    sc = scene_of(rich_row(vwap=1212.0))
    assert sc["price"]["vwap_minus_live_spot_sigma"] == pytest.approx(0.12)
    # ...and the sign is the opposite sense to a 30-min move, which is what
    # 13 of 15 reviewers got backwards under the old name
    below = scene_of(rich_row(vwap=1188.0))
    assert below["price"]["vwap_minus_live_spot_sigma"] == pytest.approx(-0.12)


def test_vwap_absent_when_unmeasured():
    sc = scene_of(rich_row(vwap=None))
    assert "vwap_minus_live_spot_sigma" not in sc["price"]


# --- regime.vol_trend -------------------------------------------------------
def _iv_rows(iv_then, iv_now):
    rows = [rich_row(ts=T0 - timedelta(minutes=35), atm_iv=iv_then),
            rich_row(ts=T0 - timedelta(minutes=15), atm_iv=(iv_then + iv_now) / 2),
            rich_row(ts=T0, atm_iv=iv_now)]
    return rows


def test_vol_trend_reads_rising_and_falling():
    rows = _iv_rows(1.50, 1.60)                     # +10 vol pts in ~35 min
    assert SR.vol_trend(rows, T0) == {"direction": "rising",
                                      "iv_change_last_30min": 10.0}
    rows = _iv_rows(1.60, 1.50)
    assert SR.vol_trend(rows, T0)["direction"] == "falling"


def test_vol_trend_flat_band_and_missing_history():
    rows = _iv_rows(1.50, 1.51)                     # +1 pt < flat band 2.5
    assert SR.vol_trend(rows, T0)["direction"] == "flat"
    assert SR.vol_trend([rich_row(ts=T0)], T0) is None    # no 30-min history


def test_vol_trend_derives_iv_for_pre_v3_rows():
    """Older rows carry no atm_iv — sigma_live·√252/spot recovers it exactly."""
    rows = _iv_rows(1.50, 1.60)
    for r in rows:
        r["sigma_live"] = r.pop("atm_iv") * r["spot"] / (252 ** 0.5)
    vt = SR.vol_trend(rows, T0)
    assert vt and vt["direction"] == "rising"


def test_vol_trend_omitted_on_expiry_late_day():
    """The τ→0 solve balloon (recorded +59 pts at the 07-31 bell) is a clock
    artifact, not a vol signal."""
    rows = _iv_rows(1.50, 2.30)
    rows[-1]["gex_views"]["front_dte"] = 0
    rows[-1]["range_ruler"] = {"em_points": 11.0, "quality": "late_day"}
    assert SR.vol_trend(rows, T0) is None


# --- regime.flip ------------------------------------------------------------
def test_flip_block_edges_center_and_position():
    row = rich_row()
    row["_ran_30m_sigma"] = -0.25
    fb = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)["regime"]["flip"]
    # sr-8: the band ships as ONE measured level plus a width. The edges were
    # the centre restated — ct == centre+0.25 and pt == centre-0.25 on 2,814 of
    # 2,814 recorded scans carrying a band — so they are computed, not sent.
    assert fb["band_center_is_gamma_flip_sigma"] == pytest.approx(0.06)
    assert fb["edges_are_center_plus_minus_sigma"] == pytest.approx(0.25)
    assert "band_upper_edge_ct_sigma" not in fb
    assert "band_lower_edge_pt_sigma" not in fb
    half = fb["edges_are_center_plus_minus_sigma"]
    ctr = fb["band_center_is_gamma_flip_sigma"]
    assert (ctr + half, ctr - half) == (pytest.approx(0.31),
                                        pytest.approx(-0.19))
    assert "gamma-negative side" in fb["live_price_vs_band"]
    assert "drifting toward pt" in fb["live_price_vs_band"]


@pytest.mark.parametrize("ladder, kept, gone", [
    ({"ct": 1231.0, "state": "positive transition"},
     "band_upper_edge_ct_sigma", "band_lower_edge_pt_sigma"),
    ({"pt": 1181.0, "state": "negative transition"},
     "band_lower_edge_pt_sigma", "band_upper_edge_ct_sigma"),
])
def test_a_partial_band_keeps_the_raw_edge_it_has(ladder, kept, gone):
    """The width is only computable when BOTH edges exist, so a one-edge band
    has nothing to reconstruct from and the raw edge is the only reading there
    is. Unreached on the recorded tape, reachable in the code — and checked in
    both directions, since ct-only and pt-only are separate paths."""
    row = rich_row()
    row["profile_ladder"] = ladder
    fb = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)["regime"]["flip"]
    assert fb[kept] is not None
    assert "edges_are_center_plus_minus_sigma" not in fb
    assert gone not in fb


def test_the_width_never_outlives_the_centre_it_is_measured_from():
    """Both edges, no `gamma_flip`. Dropping them here would ship a half-width
    measured from a centre the model cannot see — the old code's own warning
    ("the edge width ... cannot survive alone") pointed at the field that
    inherited the risk when sr-8 made the width the survivor."""
    row = rich_row(gamma_flip=None)
    fb = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)["regime"]["flip"]
    assert "band_center_is_gamma_flip_sigma" not in fb
    # so the edges STAY: they are the only levels left in the block
    assert fb["band_upper_edge_ct_sigma"] == pytest.approx(0.31)
    assert fb["band_lower_edge_pt_sigma"] == pytest.approx(-0.19)


def test_flip_drift_clause_needs_actual_motion():
    row = rich_row()
    row["_ran_30m_sigma"] = 0.01                    # under the 0.05σ floor
    fb = SR.flip_block(row, lambda v: round((v - 1200.0) / 100.0, 2)
                       if isinstance(v, (int, float)) else None, 0.01)
    assert "drifting" not in fb["live_price_vs_band"]


def test_flip_absent_without_a_ladder():
    """sr-5 rewrote this pin: a missing ladder on a MEASURED book is now a
    stated finding (no_flip_anywhere_on_board), not an absence — absence is
    reserved for the case where the book itself was never read (see the sr-5
    tests)."""
    row = rich_row(profile_ladder=None, gamma_flip=None)
    assert scene_of(row)["regime"]["flip"] == {"no_flip_anywhere_on_board": True}


# --- regime.charm (N11: magnitude + target, never a direction) --------------
def test_charm_magnitude_and_drift_target_no_direction_word():
    sc = scene_of(rich_row())
    ch = sc["regime"]["charm"]
    assert ch == {
        "magnitude_musd_per_day_uncalibrated": pytest.approx(4.28, abs=0.01),
        "drifts_toward_strike": 1370.0}
    assert "sign" not in ch and "direction" not in ch


def test_charm_absent_without_front_flows():
    sc = scene_of(rich_row(flows_front=None))
    assert "charm" not in sc["regime"]


# --- momentum ---------------------------------------------------------------
def _mom_rows(shift_pp=8.0, vol_add=340):
    """6 rows; the top strike's gamma share and gross volume grow into the
    newest row so the 5-scan window sees a build."""
    rows = []
    for i in range(6):
        f = i / 5.0
        rows.append(rich_row(
            ts=T0 - timedelta(minutes=(5 - i) * 2),
            mass=[[1300, 60.0 + shift_pp * f], [1100, 30.0], [1200, 10.0]],
            vol_gross=[[1300, 5000 + int(vol_add * f)], [1100, 2000]]))
    return rows


def _verdict_words_in(momentum):
    """Any surviving trace of momentum's deleted `read` (building / fading /
    steady). It was MOMENTUM_BUILD_PP — a 0.5pp constant fitted in July and
    shipped to the model as a finished judgement, the same pattern already cut
    from is_a_tie and dex_word. sr-7 removed it: the change itself is the
    evidence, and no word may ride beside it under any spelling."""
    blob = json.dumps(momentum)
    return [w for w in ("read", "building", "fading", "steady") if w in blob]


def test_momentum_ships_the_rising_share_and_its_volume():
    rows = _mom_rows()
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    m = sc["momentum"]["by_strike"]["1300"]
    assert m["share_of_book_gamma_change_pp"] > 0.5
    assert m["gross_volume_change_contracts"] == 340
    assert _verdict_words_in(sc["momentum"]) == []
    # sr-7: the window is told on the BOOK clock, because these are differences
    # between two books — the old "last 5 scans (10m)" label was the scan clock
    w = sc["momentum"]["window_between_books"]
    assert w["books_compared"] == 6 and w["span_min"] == 10.0
    assert "oi_d" not in m                        # static intraday — never faked
    assert "cvd" not in json.dumps(sc)            # no aggressor tape — absent


def test_momentum_absent_without_enough_rows():
    rows = _mom_rows()[:3]
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    assert "momentum" not in sc


def test_momentum_skips_strikes_outside_both_windows():
    rows = _mom_rows()
    for r in rows[:-1]:
        r["gex_views"]["mass_by_strike"] = [[1100, 30.0], [1200, 10.0]]
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    assert "1300" not in ((sc.get("momentum") or {}).get("by_strike") or {})


# --- dealer_positioning ------------------------------------------------------
def test_dealer_positioning_dex_and_vanna():
    df = scene_of(rich_row())["dealer_positioning"]
    # NO lean word (adversarial audit: sign-derived word was constant on
    # 756/756 scenes — the banned pattern)
    assert df["net_delta_bn"] == 3.93
    assert "lean" not in df
    # sr-3 put the sign caveat on the field; sr-8 took it back off. `True` on
    # 4,149 of 4,149 scans is a constant, and the doctrine states the caveat at
    # more length than a flag can. What must not happen is the caveat vanishing
    # with the field, so that is what is checked.
    assert "net_delta_sign_is_formula_artifact" not in df
    assert "ARTIFACT OF THE FORMULA" in SR._DOCTRINE
    assert df["net_vanna_musd_per_vol_point"] == pytest.approx(0.26, abs=0.01)


def test_dealer_positioning_ships_the_measured_30min_change():
    """The doctrine says the change is the news — so the change must actually
    be obtainable, timestamp-true, outage-refusing."""
    rows = [rich_row(ts=T0 - timedelta(minutes=35),
                     dex_views={"net_dex_total": 3.00e9}),
            rich_row(ts=T0, dex_views={"net_dex_total": 3.93e9})]
    df = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows,
                        T0)["dealer_positioning"]
    assert df["net_delta_change_30min_bn"] == pytest.approx(0.93)
    # outage-shaped reference → no change claim
    rows[0] = rich_row(ts=T0 - timedelta(hours=3),
                       dex_views={"net_dex_total": 3.00e9})
    df = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows,
                        T0)["dealer_positioning"]
    assert "net_delta_change_30min_bn" not in df


def test_dealer_positioning_block_dropped_when_empty():
    sc = scene_of(rich_row(dex_views=None, flows_front=None))
    assert "dealer_positioning" not in sc


def test_dealer_positioning_partial_keeps_only_the_measured_field():
    # sr-7 flattened dex{} and vanna{} into named leaves, so "only the measured
    # field survives" is now checked leaf by leaf rather than sub-block by
    # sub-block — an unmeasured vanna must take its caveat with it.
    df = scene_of(rich_row(flows_front=None))["dealer_positioning"]
    assert "net_delta_bn" in df
    assert "net_vanna_musd_per_vol_point" not in df
    assert "vanna_matters_when" not in df


# --- walls ladder -----------------------------------------------------------
def test_walls_two_per_side_nearest_first():
    sc = scene_of(rich_row())
    w = sc["walls"]
    assert [x["strike"] for x in w["call"]] == [1240.0, 1300.0]  # nearest first
    assert [x["strike"] for x in w["put"]] == [1150.0, 1100.0]
    for x in w["call"] + w["put"]:
        # share of book |γ|, percentage points
        assert 0 < x["share_of_book_gamma_pp"] <= 100
    assert w["call"][0]["sigma"] == pytest.approx(0.4)


def test_walls_absent_without_a_surface():
    sc = scene_of(rich_row(nbs=None))
    assert "walls" not in sc


def test_gwc_gwp_live_in_the_walls_ladder():
    row = rich_row()
    row["profile_ladder"].update({"gwc": 1240.0, "gwp": 1150.0})
    sc = scene_of(row)
    assert sc["walls"]["call"][0]["strike"] == 1240.0
    assert sc["walls"]["put"][0]["strike"] == 1150.0


def test_flip_band_is_told_once(monkeypatch):
    """sr-3: the flip family speaks through flip_block alone. hvl IS the flip
    and ct/pt are that centre ±0.25σ (923/923 recorded rows), so shipping them
    a second time as named levels dressed one measurement as three witnesses."""
    sc = scene_of(rich_row())
    assert "named_levels_sigma_from_spot" not in sc
    assert "lowest_named_level" not in sc
    assert set(sc["regime"]["flip"]) <= {"band_upper_edge_ct_sigma",
                                         "band_lower_edge_pt_sigma",
                                         "band_center_is_gamma_flip_sigma",
                                         "edges_are_center_plus_minus_sigma",
                                         "live_price_vs_band"}


# --- history flags (the under-pull guard) -----------------------------------
def test_level_unseen_earlier_today_flags_new_session_ground():
    rows = [rich_row(ts=T0 - timedelta(minutes=(40 - i)), spot=1200.0 + (i % 5))
            for i in range(35)]
    row = rich_row(ts=T0, spot=1260.0)            # above everything prior
    sc = SR.build_scene(row, SR.magnet_band(row), [], rows + [row], T0)
    assert sc["history"]["price_at_level_unseen_earlier_today"] is True


def test_tape_abnormal_flag_is_sigma_relative_not_a_fixed_pct():
    """Adversarial audit: a fixed 8% bar is ~1σ on a 10%-σ name and fired on
    56% of recorded scans — 'extreme' must be measured on the stock's own
    ruler (1.5σ), so a 12% day here is ordinary and a 21% day is abnormal."""
    ordinary = rich_row(spot=1100.0, prior_close=1250.0)   # −12% ≈ 1.3σ
    assert "history" not in scene_of(ordinary)
    extreme = rich_row(spot=1100.0, prior_close=1400.0)    # −21.4% ≈ 2.4σ
    assert scene_of(extreme)["history"]["tape_abnormal_vs_own_history"] is True


def test_history_block_absent_on_a_quiet_tape():
    sc = scene_of(rich_row())
    assert "history" not in sc


# --- the doctrine carries the on-demand signposts ---------------------------
def test_the_read_has_no_tools_and_the_doctrine_does_not_offer_any():
    """obs-1 revoked the grant, and the two halves have to move together — a
    doctrine that still signposts a tool the model cannot call is the
    frozen_do_not_cite bug in its purest form.

    Zero uses across 391 production calls would not on its own settle it: the
    doctrine's own named triggers fired 94 times and the model never reached,
    which is the prompt failing rather than the tool. What settles it is the
    contract. Every claim must resolve to a pointer INTO the scene, so anything
    fetched from outside could not be pointed at and could not survive the
    gate — the grant buys latency and licence with no reachable upside."""
    assert SR._ALLOWED_TOOLS == ()
    assert "--allowedTools" not in Path(SR.__file__).read_text()
    for gone in ("sndk_rag.py query", "WebSearch", "Outside world"):
        assert gone not in SR._DOCTRINE, gone
    assert "YOU HAVE NO TOOLS" in SR._DOCTRINE
    # ...and the write side is untouched: a slice is still filed per read
    assert "sndk_rag" in Path(SR.__file__).read_text()


# --- SE-review regressions (08-02) ------------------------------------------
def test_wall_gamma_share_is_of_the_full_surface():
    """The denominator is Σ|γ| over ALL of net_by_strike, not the surviving
    clusters — a cluster-only total shifts as strikes cross the concentration
    floor, so 'the wall's share rose' could be pure denominator churn."""
    sc = scene_of(rich_row())
    # full surface: 6+7+8+7+6.5 walls + 36 sub-floor strikes at 0.1 = 38.1
    assert sc["walls"]["call"][0]["share_of_book_gamma_pp"] == pytest.approx(
        15.0 / 38.1 * 100, abs=0.2)
    assert sum(w["share_of_book_gamma_pp"] for s in ("call", "put")
               for w in sc["walls"].get(s, [])) < 100.0


def test_vol_trend_refuses_an_outage_shaped_window():
    """After a scan gap the nearest ≥28-min reference can be hours old — a Δ
    shipped under a 30-min name would lie about its window."""
    rows = [rich_row(ts=T0 - timedelta(hours=3), atm_iv=1.50),
            rich_row(ts=T0, atm_iv=1.60)]
    assert SR.vol_trend(rows, T0) is None


def test_momentum_shares_use_the_window_intersection():
    """A strike entering the spot-relative frame must not fabricate a fade in
    the strikes that never moved (the telescope artifact)."""
    rows = _mom_rows(shift_pp=0.0, vol_add=0)          # nothing changes...
    rows[-1]["gex_views"]["mass_by_strike"] = [[1300, 60.0], [1100, 30.0],
                                               [1200, 10.0], [1350, 80.0]]
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    by = (sc.get("momentum") or {}).get("by_strike") or {}
    # 1300's share within the common strikes is unchanged → ~0pp
    assert "1300" in by
    assert abs(by["1300"]["share_of_book_gamma_change_pp"]) < 0.05
    assert _verdict_words_in(sc["momentum"]) == []


def test_no_nulls_anywhere_in_the_scene():
    """Fidelity audit #5: omit-never-null holds INSIDE blocks too — an
    early-session row with no prior_close / no 30-min history must produce
    absent keys, never null ones (the doctrine promises missing = unmeasured)."""
    row = rich_row(prior_close=None)
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)

    def walk(x, path="scene"):
        if x is None:
            raise AssertionError(f"null at {path}")
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
    walk(sc)
    assert "vs_prior_close_pct" not in sc["price"]
    assert "moved_last_30min_sigma" not in sc["price"]


# --- adversarial-audit regressions (08-02) ----------------------------------
def test_empty_book_ships_no_magnet_claim():
    """A bare {"is_a_tie": true} from an absent book was a manufactured
    'no strike in charge' — no measured book, no magnet block at all."""
    sc = scene_of(rich_row(mass=[]))
    assert "magnet" not in sc


def test_single_strike_book_makes_no_tie_claim():
    """One strike holding 100% of mass is maximal dominance — reading it as a
    TIE was inverted truth; with no runner-up the question is unanswerable."""
    sc = scene_of(rich_row(mass=[[1300, 60.0]]))
    m = sc["magnet"]
    assert m["top_strikes"] == [{"strike": 1300.0,
                                 "share_of_book_gamma_pp": 100.0}]
    assert "is_a_tie" not in m and "top_strike_lead_pp" not in m


def test_nan_masses_cannot_fabricate_confidence():
    """NaN < 5.0 is False — corrupt masses rendered as the confident 'a
    strike IS in charge'. NaN pairs are skipped like any torn input."""
    nan = float("nan")
    sc = scene_of(rich_row(mass=[[1300, nan], [1100, nan]]))
    assert "magnet" not in sc                     # nothing measured survives
    import json as _j
    assert "NaN" not in _j.dumps(sc)


def test_malformed_rows_degrade_instead_of_crashing():
    """One poisoned upstream row must not silence the reader AND the arrow."""
    row = rich_row(mass=[[1300, None], ["junk", 5], [1100, 30.0]])
    row["profile_ladder"] = "corrupted-string"
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row], T0)
    assert sc["magnet"]["top_strikes"] == [{"strike": 1100.0,
                                            "share_of_book_gamma_pp": 100.0}]
    # torn ladder → no ct/pt/position, and with one edge missing the width is
    # unanswerable too; the row's own measured flip center (gamma_flip is
    # intact) rightly survives
    assert set(sc["regime"]["flip"]) == {"band_center_is_gamma_flip_sigma"}


def test_moved_30min_uses_the_timestamp_true_ruler():
    """price.moved_last_30min_sigma must be the same window with_path
    measured — never the count-based path[-15] that shortens across gaps."""
    row = rich_row()
    row["_ran_30m_sigma"] = -1.5
    sc = SR.build_scene(row, SR.magnet_band(row), [], [row] * 20, T0)
    assert sc["price"]["moved_last_30min_sigma"] == -1.5
    row2 = rich_row()                              # no honest window → absent
    sc2 = SR.build_scene(row2, SR.magnet_band(row2), [], [row2] * 20, T0)
    assert "moved_last_30min_sigma" not in sc2["price"]


def test_momentum_ships_a_bleeding_share_the_same_way():
    """The deleted `read` hedged a fade with "(vol unconfirmed)" — an
    evidentiary standard the model was handed instead of the evidence. sr-7
    ships no word in either direction, so the SIGN of the raw change is the
    whole reading and it must survive symmetrically."""
    rows = _mom_rows(shift_pp=-8.0, vol_add=0)
    for r in rows:                                 # volume flat → zero change
        r["gex_views"]["vol_gross_by_strike"] = [[1300, 5000], [1100, 2000]]
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    # 1300 is the strike whose share bled (−3.5pp) with zero volume change
    m = sc["momentum"]["by_strike"]["1300"]
    assert m["share_of_book_gamma_change_pp"] < -0.5
    assert m["gross_volume_change_contracts"] == 0
    assert _verdict_words_in(sc["momentum"]) == []


# --- final verification pass regressions (08-02) ----------------------------
def test_momentum_survives_nan_and_junk_pairs():
    """A NaN at a non-top strike must not poison the shared denominator into
    confident NaN reads, and a junk pair must not crash the whole read."""
    nan = float("nan")
    rows = _mom_rows()
    for r in rows:
        r["gex_views"]["mass_by_strike"] = (
            r["gex_views"]["mass_by_strike"] + [[1200, nan], ["junk", 5]])
        r["gex_views"]["vol_gross_by_strike"] = (
            r["gex_views"]["vol_gross_by_strike"] + [[1100, nan]])
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    m = sc["momentum"]["by_strike"]["1300"]
    assert m["share_of_book_gamma_change_pp"] > 0.5
    assert m["gross_volume_change_contracts"] == 340
    assert "NaN" not in json.dumps(sc)


def test_walls_survive_nan_in_the_surface():
    row = rich_row()
    row["gex_views"]["net_by_strike"] = (
        row["gex_views"]["net_by_strike"] + [[1260, float("nan")]])
    sc = scene_of(row)
    for side in ("call", "put"):
        for w in (sc.get("walls") or {}).get(side, []):
            share = w["share_of_book_gamma_pp"]
            assert share == share                # never NaN
    assert "NaN" not in json.dumps(sc)


# --- sr-3: gates become numbers, guards point at what the model can see ------
def test_magnet_ships_the_lead_not_a_tie_verdict():
    """`is_a_tie` was `gap_pp < MAGNET_SEP_PP` — a July constant shipped as a
    finished verdict, and true on ~95% of August scans. The top strike's lead
    over the runner-up is the evidence; a threshold is not."""
    m = scene_of(rich_row())["magnet"]
    assert "is_a_tie" not in m
    assert m["top_strike_lead_pp"] is not None


def test_breadth_ships_a_number_and_never_a_direction():
    b = scene_of(rich_row())["breadth"]
    # |2.0-0.2|/2.0
    assert b["lopsidedness_0_is_even"] == pytest.approx(0.9, abs=0.01)
    assert b["heavier_side"] in ("up", "down")   # a fact about mass...
    assert "dir" not in b and "vector" not in b  # ...never a lean
    # sr-8: the shared-witness warning was a frozen string in the payload and a
    # sentence in the doctrine. The duplicate went; the warning did not.
    assert "measures_same_gamma_pile_as" not in b
    assert "SAME GAMMA PILE" in SR._DOCTRINE


def test_breadth_absent_without_a_shove_read():
    row = rich_row()
    row["gex_views"].pop("shove")
    assert "breadth" not in scene_of(row)


def test_percentile_omitted_below_the_session_floor():
    """A percentile is a claim about a distribution; the tmp state dir holds no
    prior sessions, so the word must be ABSENT rather than guessed."""
    sc = scene_of(rich_row())
    assert "top_strike_lead_vs_own_history" not in sc["magnet"]
    assert "lopsidedness_vs_own_history" not in sc["breadth"]


def test_walls_age_on_the_numbers_the_scene_ships():
    """The staleness guard used to probe the diary's call_wall/put_wall while
    the scene shipped cluster_walls output — it warned about strikes absent
    from the payload. Ages now ride the wall entries themselves."""
    rows = [rich_row(ts=T0 - timedelta(minutes=(60 - i))) for i in range(60)]
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]),
                        [{"field": "put wall", "value": 1100.0, "for_min": 300},
                         {"field": "regime", "value": "trending", "for_min": 90}],
                        rows, T0)
    assert "put wall" not in " ".join(sc["frozen_do_not_cite"])
    assert "regime unchanged 90m" in sc["frozen_do_not_cite"]
    w = sc["walls"]["put"][0]
    assert "unchanged_for_min" in w or "unchanged_for_at_least_min" in w


def test_heaviest_wall_ships_when_the_ladder_would_hide_it():
    """The ladder is ordered by DISTANCE, so a side's heaviest cluster can sit
    third and never ship — measured live on 2026-08-06, where put 1150 (the
    heaviest) was cut while walls.put[0] was called 'the strongest'."""
    sc = scene_of(rich_row())
    puts = sc["walls"]["put"]
    hb = sc["walls"].get("put_heaviest_wall_behind_the_ladder")
    if hb:                                   # only when it is not in the ladder
        assert hb["share_of_book_gamma_pp"] >= max(
            p["share_of_book_gamma_pp"] for p in puts)
        assert hb["strike"] not in {p["strike"] for p in puts}


# --- sr-4: the day-scoped calendar -------------------------------------------
def test_clock_carries_the_calendar():
    """sr-4: date, minutes each way, and the front-expiry cycle position —
    a Monday 4-dte book must not read like expiry Friday. sr-7 narrowed this
    block to the SESSION calendar alone: book_age_min left for data_sources,
    where how old a measurement is sits beside the measurement."""
    ck = scene_of(rich_row())["clock"]
    assert ck["session_date"] == "2026-07-31"
    # sr-8: `minutes_since_open` went — the two summed to 389 on 4,148 of 4,149
    # recorded scans, and the question actually asked of this block is whether
    # a 30-minute call has 30 minutes of tape left to resolve in.
    assert ck["minutes_to_close"] == 240
    assert "minutes_since_open" not in ck
    # rich_row carries no expiries
    assert ck["front_expiry"] == {"days_to_expiry": 3}
    assert "weekday" not in ck                    # == dte on every session; one
                                                  # fact must not wear two names
    assert "book_age_min" not in ck               # a measurement's age is not
                                                  # the session calendar


def test_front_expiry_date_rides_when_the_row_carries_it():
    row = rich_row()
    # live rows carry {"date","dte"} dicts; early fixtures carried bare
    # strings — both shapes must resolve (the first live sr-4 scan shipped a
    # stringified dict as the date because only strings were pinned here)
    row["meta"] = {"expiries": [{"date": "2026-07-25", "dte": -6},
                                {"date": "2026-08-01", "dte": 1}]}
    fe = scene_of(row)["clock"]["front_expiry"]
    # first at/after today
    assert fe == {"days_to_expiry": 3, "expiry_date": "2026-08-01"}
    row["meta"] = {"expiries": ["2026-07-25", "2026-08-01"]}
    assert scene_of(row)["clock"]["front_expiry"]["expiry_date"] == "2026-08-01"


def test_front_expiry_absent_without_a_dte():
    row = rich_row()
    del row["gex_views"]["front_dte"]
    assert "front_expiry" not in scene_of(row)["clock"]


# --- sr-5: measured-empty stops impersonating unmeasured ---------------------
def test_no_wall_flag_when_board_measured_but_side_empty():
    """Every cluster below spot → the call side is genuinely open air. That is
    a finding and must ship, not vanish like a torn feed would."""
    row = rich_row(spot=1400.0)                    # above every strike
    w = scene_of(row)["walls"]
    assert w["call_side_has_no_wall"] is True
    assert "call" not in w
    assert w["put"]                                # the put ladder still ships


def test_walls_absent_entirely_stays_absent_on_sensor_failure():
    sc = scene_of(rich_row(nbs=None))
    assert "walls" not in sc                       # unmeasured stays silent


def test_flip_says_no_flip_anywhere_when_book_is_one_signed():
    row = rich_row(profile_ladder=None, gamma_flip=None)
    sc = scene_of(row)                             # book present, no flip
    assert sc["regime"]["flip"] == {"no_flip_anywhere_on_board": True}


def test_flip_stays_absent_when_nothing_was_measured():
    row = rich_row(profile_ladder=None, gamma_flip=None, nbs=None)
    assert "flip" not in scene_of(row)["regime"]


# --- sr-7: the freshness gate DELETES, it does not label ---------------------
def _book_rows(**kw):
    """Six staggered scans — the momentum window's own minimum, so every one of
    the eight book-derived blocks is on the board and the gate has all eight to
    take away."""
    return [rich_row(ts=T0 - timedelta(minutes=(5 - i) * 2), **kw)
            for i in range(6)]


_BOOK_BLOCKS = ("scale", "price", "regime", "magnet", "breadth", "momentum",
                "dealer_positioning", "walls")


def _scene_at(rows, now, frozen=None):
    return SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), frozen or [],
                          rows, now)


def test_a_fresh_book_drops_nothing():
    sc = _scene_at(_book_rows(), T0)
    assert sc["freshness_rules"]["blocks_dropped_this_scan"] == []
    assert all(b in sc for b in _BOOK_BLOCKS)
    # sr-8: freshness_rules carries ONLY what varies. The rule, the ceiling and
    # the present-tense ban are standing facts and ride in the doctrine.
    assert list(sc["freshness_rules"]) == ["blocks_dropped_this_scan"]


def test_a_book_past_its_ceiling_takes_every_block_built_on_it():
    """Shipping an age beside a number and hoping the reader discounts it is
    the design that already failed — the doctrine told the model every number
    was N minutes stale and no reading ever discounted anything. A ceiling that
    deletes cannot be ignored, and it lands in a vocabulary the scene already
    has: absence means "not measured", which is exactly true of an observation
    too old to stand."""
    rows = _book_rows()
    sc = _scene_at(rows, T0 + timedelta(minutes=7))   # 7.0 min book, 6.0 cap
    # sr-8: `instrument` went to the doctrine, which opens on it
    assert list(sc) == ["data_sources", "clock", "freshness_rules"]
    dropped = sc["freshness_rules"]["blocks_dropped_this_scan"]
    assert [d["block"] for d in dropped] == list(_BOOK_BLOCKS)
    for d in dropped:
        assert d["source"] == SR.OPTIONS_BOOK
        assert (d["age_min"], d["max_age_min"]) == (7.0, 6.0)
    # the age it deleted on is the same age it publishes
    assert sc["data_sources"]["options_book"]["age_min"] == 7.0
    # the gate walks TOP-LEVEL blocks only, on purpose: minutes_to_close stays
    # true when the book is dead and an expiry date does not go stale in six
    # minutes, so clock keeps its book-sourced front_expiry
    # front_expiry is filed apart from `clock` in the map: it is the one thing
    # in the session calendar that is NOT the wall clock, and filing the whole
    # calendar under the book would take minutes_to_close down with a dead feed.
    # sr-8: the map is a module constant now, not a payload field — nothing but
    # the gate ever read it, and it was 269 bytes on every scan.
    assert "built_from" not in sc["data_sources"]
    assert "clock.front_expiry" in SR.BUILT_FROM[SR.OPTIONS_BOOK]
    assert SR.BUILT_FROM[SR.WALL_CLOCK] == ["clock"]


def test_a_source_with_no_ceiling_is_never_dropped_however_old():
    """_MAX_AGE_MIN holds ONE entry and it is the book's. Open interest is ~18
    hours old BY DESIGN — saying so is the fix, dropping it is not — and the
    session calendar cannot go stale at all. A source missing from that table
    is never dropped, at any age."""
    scene = {"regime": {"x": 1}, "walls": {"y": 2}}
    fr = SR._drop_stale_blocks(scene, {SR.OI_SNAPSHOT: 18 * 60.0,
                                       SR.WALL_CLOCK: 9_999.0})
    assert fr["blocks_dropped_this_scan"] == []
    assert "regime" in scene and "walls" in scene
    assert set(SR._MAX_AGE_MIN) == {SR.OPTIONS_BOOK}


def test_a_dropped_block_takes_its_own_do_not_cite_line_with_it():
    """A guardrail may only name fields the scene actually ships — the same
    rule that moved wall staleness onto the walls in sr-3. Without it the model
    is handed "magnet unchanged 387m, do not cite" about a magnet block deleted
    three lines earlier: a warning about a fact it cannot see."""
    rows = _book_rows()
    frozen = [{"field": "magnet", "value": 1300.0, "for_min": 387},
              {"field": "gamma sign", "value": "negative", "for_min": 120}]
    fresh = _scene_at(rows, T0, frozen)
    assert fresh["frozen_do_not_cite"] == ["magnet unchanged 387m",
                                           "gamma sign unchanged 120m"]
    late = _scene_at(rows, T0 + timedelta(minutes=7), frozen)
    # magnet went with the book; `gamma sign` names no block, so it stays
    assert late["frozen_do_not_cite"] == ["gamma sign unchanged 120m"]


def test_the_do_not_cite_key_disappears_when_the_gate_empties_it():
    """An empty list is not the same absence: it reads as "nothing here is
    frozen" rather than "the frozen things are gone with their blocks"."""
    rows = _book_rows()
    frozen = [{"field": "magnet", "value": 1300.0, "for_min": 387},
              {"field": "regime", "value": "trending", "for_min": 95}]
    assert "frozen_do_not_cite" not in _scene_at(rows, T0 + timedelta(minutes=7),
                                           frozen)


def _every_scene_shape(tmp_path):
    """Scenes covering every conditional leaf the builder can write.

    One scene cannot reach them all — a censored wall age, a board with no flip,
    an absent chain_spot, a repeated book and a history flag are mutually
    exclusive states of the same tape — so the guard below unions them."""
    prev = T0 - timedelta(days=1)
    diary = tmp_path / "sndk_reversion"
    diary.mkdir(parents=True, exist_ok=True)
    (diary / f"{prev.date().isoformat()}.jsonl").write_text("\n".join(
        json.dumps(rich_row(ts=prev - timedelta(minutes=i * 2))) for i in range(40)) + "\n")

    def scene(rows, frozen=None):
        return SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), frozen or [],
                              rows, T0)

    moved = rich_row(); moved["_ran_30m_sigma"] = 0.31
    repeat_asof = (T0 - timedelta(minutes=3)).isoformat()
    dense = [[k, 0.1] for k in range(1105, 1300, 5)
             if k not in (1150, 1240, 1245, 1260, 1265)]
    walked = [rich_row(ts=T0 - timedelta(minutes=(5 - i) * 2),
                       nbs=([[1100, -6.0], [1150, -7.0], [1240, 8.0],
                             [1245, 7.0], [1300, 6.5]] if i else
                            [[1100, -6.0], [1150, -7.0], [1260, 9.0],
                             [1265, 7.0]]) + dense)
              for i in range(6)]
    ran = [rich_row(ts=T0 - timedelta(minutes=(40 - i) * 2), spot=1200.0 + i * 6)
           for i in range(40)]
    ran[-1]["_ran_30m_sigma"] = 0.9
    same_oi = _oi_surface(range(1100, 1165, 5))

    return [
        scene([rich_row()]),
        scene([rich_row(meta={})]),                        # the fallback ruler tag
        scene([rich_row(meta={"chain_spot": 1200.0,
                              "spot_source": "schwab_quote"})]),   # spot_feed
        scene([rich_row(meta={"chain_spot": 1190.0})]),     # both spots + the gap
        scene([moved]),                                     # moved_last_30min_sigma
        scene([rich_row(profile_ladder=None, gamma_flip=None)]),   # no flip at all
        scene([rich_row()], [{"field": "magnet", "value": 1.0, "for_min": 99}]),
        scene([rich_row(ts=T0 - timedelta(minutes=35),
                        dex_views={"net_dex_total": 3.0e9}),
               rich_row(ts=T0, dex_views={"net_dex_total": 3.93e9})]),
        scene(_iv_rows(1.50, 2.30)),                        # regime.vol_trend
        scene(_book_rows()),                                # censored wall ages
        scene(walked),                                      # an exact wall age
        scene([rich_row(ts=T0 - timedelta(minutes=2),
                        meta={"chain_spot": 1200.0, "book_asof": repeat_asof}),
               rich_row(ts=T0,
                        meta={"chain_spot": 1200.0, "book_asof": repeat_asof})]),
        scene(_oi_rows(same_oi, same_oi)),
        scene(ran),                                         # the history flags
        # a scene carrying Python-found candidates: the gamma sign differs
        # between two distinct books, which is `changed_this_scan`
        scene([rich_row(ts=T0 - timedelta(minutes=4), gamma_sign="negative",
                        meta={"chain_spot": 1200.0,
                              "book_asof": (T0 - timedelta(minutes=4)).isoformat()}),
               rich_row(ts=T0, gamma_sign="positive",
                        meta={"chain_spot": 1200.0,
                              "book_asof": T0.isoformat()})]),
    ]


# Named leaves the fixtures above cannot reach, each with the reason. A name may
# only sit here because REACHING it is expensive, never because it is doubtful.
# obs-1 OUTPUT-schema names. The doctrine necessarily speaks these aloud while
# describing the reply it wants, and they are not scene fields — checking them
# against the scene is a category error, not a caught bug.
_OUTPUT_SCHEMA_NAMES = {
    "paths", "values", "say", "quiet", "quiet_because", "notable", "unknowns",
    "watch", "novelty", "basis", "window", "what", "would_change", "path",
    "stale_min", "n_sessions", "pctile", "one_witness_with",
    "used",          # obs-1b: the model names the candidates it is speaking about
}

_UNREACHABLE_IN_FIXTURES = {
    # needs the percentile cache warm with several CLOSED sessions of distinct
    # books; test_percentile_omitted_below_the_session_floor covers the absence
    # side, and test_magnet_ships_the_lead_not_a_verdict the presence side.
    "top_strike_lead_vs_own_history",
}


def test_the_doctrine_never_names_a_field_the_scene_stopped_shipping(tmp_path):
    """THE sr-8 GUARD, and the reason the field deletions and the doctrine edits
    had to land in the same commits.

    Two halves reach the model on every call: the scene (per-scan, full price)
    and the doctrine (byte-identical, prompt-cached). Delete a number but leave
    its sentence and the model is handed an instruction about something it
    cannot see. This module has been bitten by exactly that before —
    `_drop_stale_blocks` carries a guard for it, and the comment there calls it
    "the frozen_do_not_cite bug wearing a different hat" after the payload
    warned "magnet unchanged 387m, do not cite" about a magnet block deleted
    three lines earlier.

    The first draft of this guard checked doctrine names against every quoted
    bareword in the module source, which is not the same set as "keys the
    builder writes": it admitted 282 tokens including raw diary-row keys
    (`gex_views`, `gap_pp`, `spot`) and — the reason it was rewritten — both
    flip-band edges, which `flip_block` still constructs before popping. It
    would have passed this branch's own deletion. The allow-list is now built
    from scenes that were actually BUILT."""
    shipped = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                shipped.add(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for sc in _every_scene_shape(tmp_path):
        walk(sc)
    shipped |= _UNREACHABLE_IN_FIXTURES

    named = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", SR._DOCTRINE))
    named -= {"_pp", "_bn", "_musd", "_min", "_sigma"}   # the unit suffixes
    named -= _OUTPUT_SCHEMA_NAMES                        # the reply's own shape
    assert named, "the doctrine names no fields at all — the regex broke"
    for name in sorted(named):
        for part in name.split("."):
            assert part in shipped, (
                f"the doctrine names `{name}`, but no scene the builder can "
                f"produce carries a key {part!r} — a sentence outliving its field")


def test_the_guard_rejects_a_name_the_builder_stopped_writing():
    """The guard is only worth having if it FAILS on the thing it exists to
    catch, and its first draft did not. These four are the real shapes: two
    leaves sr-8 deleted (one of which the old guard admitted, because
    flip_block still constructs it before popping it), a whole block that left
    the payload, and a raw diary-row key a doctrine author might reach for
    believing it ships."""
    for gone in ("band_upper_edge_ct_sigma", "instrument", "built_from",
                 "gex_views"):
        assert gone not in SR._DOCTRINE, (
            f"`{gone}` is back in the doctrine — either the scene ships it "
            f"again, in which case update this test, or a sentence has "
            f"outlived its field")


def test_the_forbidden_list_names_blocks_the_scene_actually_ships():
    """PRESENT_TENSE_FORBIDDEN_FOR is published to the model — sr-7 sent it in
    freshness_rules, sr-8 sends it in the doctrine — so every name on it still
    has to resolve in the scene beside it. The sr-3 lesson about a guard that
    warned about put wall 1200, a strike absent from the whole payload, does not
    care which half of the prompt the guard rode in on."""
    sc = _scene_at(_book_rows(), T0)
    published = list(SR.PRESENT_TENSE_FORBIDDEN_FOR)
    assert ", ".join(published) in SR._DOCTRINE
    assert "present_tense_forbidden_for" not in sc["freshness_rules"]
    for name in published:
        head, _, leaf = name.partition(".")
        assert head in sc, name
        assert not leaf or leaf in sc[head], name
        # ...and every one of them is OI-derived, which is why the list exists:
        # open interest updates once overnight, so "dealers are buying" about it
        # is a false statement about time, not a debatable read
        assert head in SR.BUILT_FROM[SR.OI_SNAPSHOT], name


# --- sr-7: the ruler is the spot the BOOK was measured at --------------------
def _sigma_rulers(scene):
    """Every `sigma_measured_from` value anywhere in the scene — one scene may
    not carry two answers to which frame it is in."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "sigma_measured_from":
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(scene)
    return found


def test_every_book_derived_sigma_divides_the_books_own_spot():
    """The live spot and the chain's spot are different numbers — median $2.13
    apart on cached rows, $41.99 at worst — and dividing the live one into a
    book measured at the other was costing up to 0.9σ of distance. Here they
    sit $10 apart on a $100 sigma, so every book-derived distance moves by
    exactly 0.1σ. sr-8: the frame is the standing rule, stated in the doctrine
    and no longer stamped on three blocks — a tag that read the same word on
    4,149 of 4,149 scans was being read past, so it now speaks only for the
    exception (see the fallback test below)."""
    live = scene_of(rich_row())
    book = scene_of(rich_row(meta={"chain_spot": 1190.0}))
    assert live["walls"]["call"][0]["sigma"] == pytest.approx(0.4)   # off 1200
    assert book["walls"]["call"][0]["sigma"] == pytest.approx(0.5)   # off 1190
    assert live["magnet"]["top_strike_sigma"] == pytest.approx(1.0)
    assert book["magnet"]["top_strike_sigma"] == pytest.approx(1.1)  # 1300
    assert book["regime"]["flip"]["band_center_is_gamma_flip_sigma"] == (
        pytest.approx(0.16))                                         # flip 1206
    assert _sigma_rulers(book) == set()      # the rule holds: nothing to say
    assert "SUBTRACT `price.live_minus_book_spot_sigma`" in SR._DOCTRINE
    assert book["price"]["spot_when_book_was_measured"] == 1190.0
    # sr-8: the dollar twin went; both spots it differences are right here
    assert "live_minus_book_spot_dollars" not in book["price"]
    assert book["price"]["live_minus_book_spot_sigma"] == pytest.approx(0.1)


def test_vwap_is_the_one_distance_that_stays_on_the_live_spot():
    """Every other σ in the scene moved onto the book's spot; vwap did not,
    because vwap is a LIVE-TAPE level and measuring it against a spot the chain
    saw four minutes ago would put a live number in a stale frame. The leaf
    name is the guard: it says which ruler it used, and since sr-8 which way it
    points."""
    live = scene_of(rich_row())["price"]["vwap_minus_live_spot_sigma"]
    book = scene_of(rich_row(meta={"chain_spot": 1190.0}))["price"]
    assert live == pytest.approx(0.12)                 # (1212 − 1200) / 100
    assert book["vwap_minus_live_spot_sigma"] == pytest.approx(0.12)
    assert "vwap_dist_sigma" not in book               # never the bare name
    assert "vwap_dist_sigma_from_live_spot" not in book       # nor the old one


def test_the_offset_converts_a_book_sigma_the_way_the_doctrine_says_it_does():
    """The doctrine hands the model ONE conversion between the two frames, and
    it is the only route from a book-measured distance to a distance from where
    price actually is. Both halves are pinned together because either alone is
    unfalsifiable — the sentence the model is given, and the arithmetic it
    would do with it.

    1240 sits 0.5σ above the book's spot (1190) and 0.4σ above the live spot
    (1200); live_minus_book_spot_sigma is (1200 − 1190) / 100 = +0.10.

    IT IS A SUBTRACTION, and this test was RED for the hour it took to say so.
    live σ = (K − live) / σ = book σ − (live − book) / σ, and the field is that
    second term — so the doctrine's original "add" walked the wrong way by
    twice the gap: 0.5 + 0.10 = 0.6 here against a true 0.4. The median cached
    gap is $2.13 and the worst recorded is $41.99, i.e. up to ~0.8σ of error
    handed to a model the same doctrine tells that half of all 30-minute
    windows come in under 0.09σ.

    The verb moved and the NAME moved with it. `live_minus_book_spot_sigma`
    sits beside `live_minus_book_spot_dollars`, the identical quantity in
    dollars — same subject, same direction, same word — so nobody has to hold
    the sign in their head to use it. That is why both halves stay pinned
    together here: the sentence the model is given, and the arithmetic it would
    do with it. Either alone is unfalsifiable."""
    assert "SUBTRACT `price.live_minus_book_spot_sigma`" in SR._DOCTRINE
    assert "add `price.live_minus_book_spot_sigma`" not in SR._DOCTRINE
    sc = scene_of(rich_row(meta={"chain_spot": 1190.0}))
    book_sigma = sc["walls"]["call"][0]["sigma"]
    offset = sc["price"]["live_minus_book_spot_sigma"]
    live_sigma = round((1240.0 - sc["price"]["live_spot"]) / 100.0, 2)
    assert book_sigma - offset == pytest.approx(live_sigma)


def test_an_impossible_chain_spot_is_refused_and_the_live_spot_stands_in():
    """chain_spot is an external dependency and `_fin` returns 0.0 as happily
    as 1190.0. A zero ruler would have shipped top_strike_sigma 33.29 and walls
    at +32.54 with nothing complaining. Refused values fall back to the live
    spot rather than shipping the absurd number."""
    for absurd in (0.0, -5.0, 99999.0):
        sc = scene_of(rich_row(meta={"chain_spot": absurd}))
        assert sc["walls"]["call"][0]["sigma"] == pytest.approx(0.4)  # off 1200
        assert sc["magnet"]["top_strike_sigma"] == pytest.approx(1.0)
        assert "spot_when_book_was_measured" not in sc["price"]
        assert "live_minus_book_spot_sigma" not in sc["price"]


def test_the_drift_ceiling_cannot_reject_real_data():
    """The widest gap over 3,392 recorded August rows is $43.27 on a ~$1,480
    name — 2.9%, an order of magnitude inside the ceiling. A tighter ceiling
    would start refusing books that were merely moving."""
    worst_recorded = 43.27 / 1480.0
    assert SR.RULER_MAX_DRIFT == 0.25 and worst_recorded < SR.RULER_MAX_DRIFT
    drifted = round(1200 * (1 - worst_recorded), 2)          # 1164.9
    sc = scene_of(rich_row(meta={"chain_spot": drifted}))
    assert sc["price"]["spot_when_book_was_measured"] == drifted
    assert _sigma_rulers(sc) == set()        # accepted: the standing rule holds


def test_the_fallback_ruler_never_wears_the_books_name():
    """The one place an sr-7 provenance tag could lie. sigma_measured_from used
    to read "spot_when_book_was_measured" even when there was no chain_spot to
    measure from — and the doctrine then told the model to convert those
    distances with price.live_minus_book_spot_sigma, the one field the scene does
    not contain in exactly that case."""
    for row in (rich_row(),                          # no meta at all
                rich_row(meta={}),                   # meta, no chain_spot
                rich_row(meta={"chain_spot": None}),
                rich_row(meta={"chain_spot": 0.0}),      # refused: not positive
                rich_row(meta={"chain_spot": 99999.0})):  # refused: drift
        sc = scene_of(row)
        assert _sigma_rulers(sc) == {"live_spot_because_chain_spot_was_absent"}
        assert "live_minus_book_spot_sigma" not in sc["price"]


def test_no_wall_carries_a_sigma_that_contradicts_its_own_side():
    """sr-7 ONE-FRAME RULE: ruler_spot decides BOTH which side a cluster is
    filed under and how far away it is reported to be. The first draft moved
    only the distance onto the book's spot and left the side filter on the live
    spot, which put 52 of 10,101 recorded wall entries under a side their own
    sigma contradicted — 2026-08-05 11:58 shipped walls.call[0] = {strike:
    1400.0, sigma: -0.04} under a doctrine that calls call[0] the first thing
    price meets going UP.

    chain_spot 1250 against a live spot of 1200 straddles the 1240/1245 call
    cluster, which is the shape that produced those 52."""
    for meta in ({}, {"chain_spot": 1150.0}, {"chain_spot": 1250.0}):
        w = scene_of(rich_row(meta=meta))["walls"]
        for side, sign in (("call", 1), ("put", -1)):
            entries = list(w.get(side) or [])
            behind = w.get(side + "_heaviest_wall_behind_the_ladder")
            if behind:
                entries.append(behind)
            for e in entries:
                assert e["sigma"] * sign > 0, (meta, side, e)
    straddled = scene_of(rich_row(meta={"chain_spot": 1250.0}))["walls"]
    # in one frame 1240 is simply not above the book's spot; in two frames it
    # was walls.call[0] at −0.10σ
    assert 1240.0 not in {e["strike"] for e in straddled.get("call") or []}


# --- sr-7: a stored null is not a missing key --------------------------------
def test_a_row_with_an_explicit_null_meta_does_not_take_the_read_down():
    """`.get("meta", {})` returns a STORED None rather than the default, so a
    row written with `"meta": null` raised out of build_scene and took the read
    row AND the arrow with it — the one thing this module's torn-row rule says
    must never happen. Every other meta read in the file uses the `or {}`
    idiom; _book_is_repeat's read of the PREVIOUS row was the exception."""
    prev = rich_row(ts=T0 - timedelta(minutes=2), meta=None)
    cur = rich_row(meta={"book_asof": T0.isoformat()})
    assert SR._book_is_repeat(cur, prev) is None
    assert SR._book_is_repeat(cur, None) is None
    book = SR.build_scene(cur, SR.magnet_band(cur), [], [prev, cur],
                          T0)["data_sources"]["options_book"]
    # unanswerable, so absent — never a guessed False
    assert "is_repeat_of_previous_scan" not in book
    # ...and a null meta on the row being READ is survivable the same way
    cur["meta"] = None
    book = SR.build_scene(cur, SR.magnet_band(cur), [], [prev, cur],
                          T0)["data_sources"]["options_book"]
    assert book["measured_at_is_fallback_scan_clock"] is True
    assert book["distinct_books_so_far_today"] == 2


# --- sr-7: did open interest actually hold still today? ----------------------
def _oi_surface(strikes):
    """An oi_by_strike surface keyed BY STRIKE, so a window that walks with
    spot carries the same OI on the strikes it still contains."""
    return [[float(k), 1000 + int(k) % 97] for k in strikes]


def _oi_rows(*surfaces):
    rows = []
    for i, s in enumerate(surfaces):
        r = rich_row(ts=T0 - timedelta(minutes=(len(surfaces) - 1 - i) * 2))
        if s is not None:
            r["gex_views"]["oi_by_strike"] = s
        rows.append(r)
    return rows


def _oi_of(rows):
    return SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows,
                          T0)["data_sources"]["open_interest"]


def test_open_interest_holding_still_is_measured_not_asserted():
    """The scene states outright that every structural number rests on last
    night's OI, so that claim is re-measured on every scan instead of resting
    on a one-off audit."""
    same = _oi_surface(range(1100, 1165, 5))
    oi = _oi_of(_oi_rows(same, same))
    assert oi["measured_unchanged_so_far_today"] is True
    assert oi["strikes_compared_today"] == 13
    # sr-8: `snapshot_is` and `updates_intraday` described the FEED, not today.
    # They were a fixed string and a fixed False; the doctrine makes the point
    # where it bites, in the present-tense ban.
    assert "snapshot_is" not in oi and "updates_intraday" not in oi
    assert "PRIOR SESSION'S CLOSE" in SR._DOCTRINE


def test_a_single_changed_strike_is_enough_to_say_no():
    moved = _oi_surface(range(1100, 1165, 5))
    moved[3] = [moved[3][0], moved[3][1] + 1]
    oi = _oi_of(_oi_rows(_oi_surface(range(1100, 1165, 5)), moved))
    assert oi["measured_unchanged_so_far_today"] is False
    assert oi["strikes_compared_today"] == 13


def test_only_the_intersection_is_compared_because_the_window_walks():
    """The strike window is spot-relative and moves with price, so the two
    surfaces list different strikes. Comparing the raw lists answers a question
    about the telescope, not about the book — these two share nine strikes,
    carry identical OI on all nine, and differ in four at each end."""
    first = _oi_surface(range(1100, 1165, 5))       # 1100..1160
    last = _oi_surface(range(1120, 1185, 5))        # 1120..1180
    assert [k for k, _ in first] != [k for k, _ in last]
    oi = _oi_of(_oi_rows(first, last))
    assert oi["measured_unchanged_so_far_today"] is True
    assert oi["strikes_compared_today"] == 9


def test_too_few_common_strikes_says_nothing_rather_than_yes():
    """Below OI_MIN_STRIKES_COMPARED the claim is not worth making, and an
    absent answer is the scene's word for unanswerable — a guessed "unchanged"
    would be the reader inventing the reassurance it was asked for."""
    thin = _oi_rows(_oi_surface(range(1100, 1125, 5)),   # 1100..1120
                    _oi_surface(range(1115, 1140, 5)))   # 1115..1135, 2 shared
    assert SR._oi_unchanged_today(thin) == (None, None)
    # sr-8: with the two constants gone, an open_interest block that can say
    # nothing measurable has nothing left to ship — and under omit-never-null
    # the block itself vanishes rather than shipping a hollow shell. (Here the
    # tmp state dir holds no closed session either, so prior_session_date is
    # absent too; in production it is the field that keeps the block alive.)
    sc = SR.build_scene(thin[-1], SR.magnet_band(thin[-1]), [], thin, T0)
    assert "open_interest" not in sc["data_sources"]
    assert SR.OI_MIN_STRIKES_COMPARED == 5


def test_a_warmup_row_with_no_surface_is_skipped_not_fatal():
    """The first row of a session often carries no oi_by_strike at all, so the
    comparison starts at the first row that HAS one — starting at rows[0]
    turned every session's opening scan into "unanswerable" for the whole
    day."""
    same = _oi_surface(range(1100, 1165, 5))
    assert SR._oi_unchanged_today(_oi_rows(None, same, same)) == (True, 13)
    oi = _oi_of(_oi_rows(None, same, same))
    assert oi["measured_unchanged_so_far_today"] is True
