"""Scene payload v2 (sr-2, 2026-08-02 blueprint) — the new blocks.

Every block is measured-or-absent: these tests pin both faces — the block
appears with clean sources, and VANISHES (never nulls, never zeros) without
them. That is the omit-never-null directive as executable rule.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


# --- scale.aem --------------------------------------------------------------
def test_aem_splits_the_priced_budget_by_iv_skew():
    sc = scene_of(rich_row())
    aem = sc["scale"]["aem"]
    # asymmetry REALLOCATES the ruler's 2·em budget, never adds to it
    assert aem["down_dollars"] + aem["up_dollars"] == pytest.approx(80.0)
    assert aem["down_dollars"] == pytest.approx(44.0)   # d=0.55
    assert aem["skew"] == "downside"
    assert aem["source"] == "put/call IV skew"


def test_aem_absent_without_skew_or_em():
    sc = scene_of(rich_row(iv_skew=None))
    assert "aem" not in sc["scale"]
    sc = scene_of(rich_row(range_ruler={"em_points": None}))
    assert "aem" not in sc["scale"]


def test_aem_balanced_band_is_named_balanced():
    sc = scene_of(rich_row(iv_skew={"down_share": 0.5}))
    assert sc["scale"]["aem"]["skew"] == "balanced"


# --- price.vwap_dist_sigma --------------------------------------------------
def test_vwap_ships_as_sigma_distance():
    sc = scene_of(rich_row(vwap=1212.0))
    assert sc["price"]["vwap_dist_sigma"] == pytest.approx(0.12)


def test_vwap_absent_when_unmeasured():
    sc = scene_of(rich_row(vwap=None))
    assert "vwap_dist_sigma" not in sc["price"]


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
    assert fb["ct_sigma"] == pytest.approx(0.31)
    assert fb["pt_sigma"] == pytest.approx(-0.19)
    assert fb["center_sigma"] == pytest.approx(0.06)   # the flip IS the center
    assert "gamma-negative side" in fb["price_in_band"]
    assert "drifting toward pt" in fb["price_in_band"]


def test_flip_drift_clause_needs_actual_motion():
    row = rich_row()
    row["_ran_30m_sigma"] = 0.01                    # under the 0.05σ floor
    fb = SR.flip_block(row, lambda v: round((v - 1200.0) / 100.0, 2)
                       if isinstance(v, (int, float)) else None, 0.01)
    assert "drifting" not in fb["price_in_band"]


def test_flip_absent_without_a_ladder():
    row = rich_row(profile_ladder=None, gamma_flip=None)
    sc = scene_of(row)
    assert "flip" not in sc["regime"]


# --- regime.charm (N11: magnitude + target, never a direction) --------------
def test_charm_magnitude_and_drift_target_no_direction_word():
    sc = scene_of(rich_row())
    ch = sc["regime"]["charm"]
    assert ch == {"magnitude": pytest.approx(4.28, abs=0.01),
                  "drift_toward": 1370.0}
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


def test_momentum_reads_building_with_volume_confirming():
    rows = _mom_rows()
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    m = sc["momentum"]["by_strike"]["1300"]
    assert m["gex_share_d_pp"] > 0.5 and m["vol_d"] == 340
    assert m["read"] == "building"
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


# --- dealer_flow ------------------------------------------------------------
def test_dealer_flow_dex_and_vanna():
    df = scene_of(rich_row())["dealer_flow"]
    # NO lean word (adversarial audit: sign-derived word was constant on
    # 756/756 scenes — the banned pattern)
    assert df["dex"]["net"] == 3.93
    assert "lean" not in df["dex"]
    # sr-3: net_dex_total is > 0 BY CONSTRUCTION (1,675/1,675 recorded rows),
    # so the field must say so where the model reads the number — a caveat that
    # lives only in the cached doctrine is a caveat the number travels without.
    assert "net_change_30min" in df["dex"]["note"]
    assert df["vanna"]["net"] == pytest.approx(0.26, abs=0.01)


def test_dealer_flow_ships_the_measured_30min_change():
    """The doctrine says the change is the news — so the change must actually
    be obtainable, timestamp-true, outage-refusing."""
    rows = [rich_row(ts=T0 - timedelta(minutes=35),
                     dex_views={"net_dex_total": 3.00e9}),
            rich_row(ts=T0, dex_views={"net_dex_total": 3.93e9})]
    df = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)["dealer_flow"]
    assert df["dex"]["net_change_30min"] == pytest.approx(0.93)
    # outage-shaped reference → no change claim
    rows[0] = rich_row(ts=T0 - timedelta(hours=3),
                       dex_views={"net_dex_total": 3.00e9})
    df = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)["dealer_flow"]
    assert "net_change_30min" not in df["dex"]


def test_dealer_flow_block_dropped_when_empty():
    sc = scene_of(rich_row(dex_views=None, flows_front=None))
    assert "dealer_flow" not in sc


def test_dealer_flow_partial_keeps_only_the_measured_field():
    df = scene_of(rich_row(flows_front=None))["dealer_flow"]
    assert "dex" in df and "vanna" not in df


# --- walls ladder -----------------------------------------------------------
def test_walls_two_per_side_nearest_first():
    sc = scene_of(rich_row())
    w = sc["walls"]
    assert [x["strike"] for x in w["call"]] == [1240.0, 1300.0]  # nearest first
    assert [x["strike"] for x in w["put"]] == [1150.0, 1100.0]
    for x in w["call"] + w["put"]:
        assert 0 < x["gex"] <= 100                # share of book |γ|, percent
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
    assert set(sc["regime"]["flip"]) <= {"ct_sigma", "pt_sigma",
                                         "center_sigma", "price_in_band"}


# --- history flags (the under-pull guard) -----------------------------------
def test_level_unseen_today_flags_new_session_ground():
    rows = [rich_row(ts=T0 - timedelta(minutes=(40 - i)), spot=1200.0 + (i % 5))
            for i in range(35)]
    row = rich_row(ts=T0, spot=1260.0)            # above everything prior
    sc = SR.build_scene(row, SR.magnet_band(row), [], rows + [row], T0)
    assert sc["history"]["level_unseen_today"] is True


def test_abnormal_tape_is_sigma_relative_not_a_fixed_pct():
    """Adversarial audit: a fixed 8% bar is ~1σ on a 10%-σ name and fired on
    56% of recorded scans — 'extreme' must be measured on the stock's own
    ruler (1.5σ), so a 12% day here is ordinary and a 21% day is abnormal."""
    ordinary = rich_row(spot=1100.0, prior_close=1250.0)   # −12% ≈ 1.3σ
    assert "history" not in scene_of(ordinary)
    extreme = rich_row(spot=1100.0, prior_close=1400.0)    # −21.4% ≈ 2.4σ
    assert scene_of(extreme)["history"]["abnormal_tape"] is True


def test_history_block_absent_on_a_quiet_tape():
    sc = scene_of(rich_row())
    assert "history" not in sc


# --- the doctrine carries the on-demand signposts ---------------------------
def test_doctrine_signposts_history_and_websearch():
    assert "sndk_rag.py" in SR._DOCTRINE
    assert "level_unseen_today" in SR._DOCTRINE
    assert "WebSearch" in SR._DOCTRINE
    assert "none" in SR._DOCTRINE                 # an honest "no vector" exists


def test_allowed_tools_are_exactly_the_two_on_demand_paths():
    assert any(t.startswith("Bash(") and "sndk_rag.py" in t
               for t in SR._ALLOWED_TOOLS)
    assert "WebSearch" in SR._ALLOWED_TOOLS
    assert "Bash" in " ".join(SR._ALLOWED_TOOLS)  # restricted prefix, not bare
    assert "Bash" not in SR._NO_TOOLS and "WebSearch" not in SR._NO_TOOLS


# --- SE-review regressions (08-02) ------------------------------------------
def test_walls_gex_share_is_of_the_full_surface():
    """The denominator is Σ|γ| over ALL of net_by_strike, not the surviving
    clusters — a cluster-only total shifts as strikes cross the concentration
    floor, so 'the wall's share rose' could be pure denominator churn."""
    sc = scene_of(rich_row())
    # full surface: 6+7+8+7+6.5 walls + 36 sub-floor strikes at 0.1 = 38.1
    assert sc["walls"]["call"][0]["gex"] == pytest.approx(15.0 / 38.1 * 100, abs=0.2)
    assert sum(w["gex"] for s in ("call", "put")
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
    # 1300's share within the common strikes is unchanged → steady, ~0pp
    assert "1300" in by
    assert abs(by["1300"]["gex_share_d_pp"]) < 0.05
    assert by["1300"]["read"] == "steady"


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
    assert m["top_strikes"] == [[1300.0, 100.0]]
    assert "is_a_tie" not in m and "gap_pp" not in m


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
    assert sc["magnet"]["top_strikes"] == [[1100.0, 100.0]]  # the clean pair
    # torn ladder → no ct/pt/position; the row's own measured flip center
    # (gamma_flip is intact) rightly survives
    assert set(sc["regime"]["flip"]) == {"center_sigma"}


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


def test_momentum_fading_earns_its_hedge_symmetrically():
    rows = _mom_rows(shift_pp=-8.0, vol_add=0)
    for r in rows:                                 # volume flat → unconfirmed
        r["gex_views"]["vol_gross_by_strike"] = [[1300, 5000], [1100, 2000]]
    sc = SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, T0)
    # 1300 is the strike whose share bled (−3.5pp) with zero volume change —
    # it must read fading AND carry the same hedge building would
    assert sc["momentum"]["by_strike"]["1300"]["read"] == "fading (vol unconfirmed)"


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
    assert m["read"] == "building" and m["vol_d"] == 340
    assert "NaN" not in json.dumps(sc)


def test_walls_survive_nan_in_the_surface():
    row = rich_row()
    row["gex_views"]["net_by_strike"] = (
        row["gex_views"]["net_by_strike"] + [[1260, float("nan")]])
    sc = scene_of(row)
    for side in ("call", "put"):
        for w in (sc.get("walls") or {}).get(side, []):
            assert w["gex"] == w["gex"]          # never NaN
    assert "NaN" not in json.dumps(sc)
