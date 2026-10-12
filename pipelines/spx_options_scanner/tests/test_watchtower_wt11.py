"""wt-11 — the SNDK sr-2→sr-5 port: rows-derived scene blocks, the two-tool
grant, and the honesty rules (measured-or-absent, measured-empty ≠ unmeasured,
no constants wearing signal clothes).

Every threshold asserted here was pre-registered off THIS tape's recorded
distributions (see the constants' comments) — these tests pin the shapes, the
fail-open paths, and the bans."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import watchtower as wt

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 7, 14, 30, tzinfo=ET)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path / "state"))


def _t(**over):
    t = {
        "ts": NOW.isoformat(), "ticker": "SPX", "spot": 7450.29, "sigma": 106.9,
        "gamma_sign": "negative", "gamma_flip": 7480.0,
        "call_wall": 7550.0, "put_wall": 7350.0, "prior_close": 7460.0,
        "range_em": 0.62, "variance_ratio": 0.88, "vix_ts": 0.93,
        "regime": "pinning",
        "reversion_extreme": {"fired": False, "armed": False, "direction": None,
                              "gap_stretch": -0.1, "vwap_stretch": -0.1,
                              "reaction": False, "runway_ok": False,
                              "wall_dist_call": 0.93, "wall_dist_put": -0.94,
                              "magnet": 7420.0},
        "gex_views": {"aggressor_flow": -0.4, "pin_top_share": 0.31,
                      "magnet": 7420.0},
    }
    t.update(over)
    return t


def _row(minutes_ago, spot=7450.0, sigma_live=37.6, net_dex=1.7e11,
         mass=None, volg=None, quality="ok"):
    ts = NOW - timedelta(minutes=minutes_ago)
    r = {"ts": ts.isoformat(), "ticker": "SPX", "spot": spot,
         "sigma": 106.9, "sigma_live": sigma_live,
         "range_ruler": {"quality": quality},
         "dex_views": {"net_dex_total": net_dex},
         "gex_views": {}}
    if mass is not None:
        r["gex_views"]["mass_by_strike"] = mass
    if volg is not None:
        r["gex_views"]["vol_gross_by_strike"] = volg
    return r


# --- clock + scale -----------------------------------------------------------
def test_clock_carries_the_date_and_no_absolute_levels_leak():
    p = wt.build_payload(_t())
    assert p["clock"]["date"] == "2026-08-07"
    # the amendment covers DATES only — the no-absolute-levels rule stands:
    # no raw index level from the row may appear anywhere in the scene
    blob = json.dumps(p)
    for lvl in ("7450", "7550", "7350", "7480", "7420", "7460"):
        assert lvl not in blob


def test_scale_ships_the_measured_move_spread():
    sc = wt.build_payload(_t())["scale"]
    sp = sc["move_30min_sigma"]
    assert sp["half_under"] == 0.08 and sp["one_in_twenty_over"] == 0.27
    assert sp["sessions_measured"] == 30       # n rides the claim's face


def test_aem_ships_measured_splits_but_never_the_towers_own_range():
    # a realized-tape split is evidence...
    t = _t(adaptive_em={"asym_source": "speedometer", "em_up_pts": 32.0,
                        "em_down_pts": 43.0, "em_v": 1})
    aem = wt.build_payload(t)["scale"]["aem"]
    assert aem["down_sigma"] == pytest.approx(0.4, abs=0.01)
    assert "speedometer" in aem["source"]
    # ...the tower's own called range fed back is a feedback loop, not evidence
    t2 = _t(adaptive_em={"asym_source": "watchtower", "em_up_pts": 32.0,
                         "em_down_pts": 43.0, "em_v": 1})
    assert "aem" not in wt.build_payload(t2)["scale"]


# --- magnet band -------------------------------------------------------------
def test_magnet_band_tells_the_tie_in_sigma_space():
    t = _t()
    t["gex_views"]["mass_by_strike"] = [[7450.0, 90.0], [7500.0, 85.0],
                                        [7400.0, 25.0]]
    mb = wt.build_payload(t)["dealer_map_gravity"]["magnet_band"]
    assert mb["gap_pp"] == 2.5                 # (90-85)/200 in pp
    assert len(mb["top_strikes"]) == 3
    # σ-from-spot, never the strike itself
    assert mb["top_strikes"][0][0] == pytest.approx(0.0, abs=0.01)
    assert mb["top_strikes"][1][0] == pytest.approx(0.47, abs=0.01)
    assert "TIE" in mb["note"]


def test_magnet_band_absent_without_a_measured_book():
    assert "magnet_band" not in wt.build_payload(_t())["dealer_map_gravity"]
    t = _t()
    t["gex_views"]["mass_by_strike"] = [[7450.0, float("nan")]]
    assert "magnet_band" not in wt.build_payload(t)["dealer_map_gravity"]


# --- vol trend ---------------------------------------------------------------
def test_vol_trend_reads_the_30min_iv_change():
    # iv derives exactly from sigma_live: iv = sl·√252/spot → +Δ = rising
    # (sl 35→45 at spot 7450 ≈ +2.1 vol pts, just over the 2.0 flat band)
    rows = [_row(31, sigma_live=35.0), _row(2, sigma_live=45.0)]
    vt = wt.build_payload(_t(), rows=rows)["tape"]["vol_trend"]
    assert vt["direction"] == "rising"
    assert vt["iv_change_last_30min"] > wt.VOL_TREND_FLAT


def test_vol_trend_flat_band_and_no_history_are_honest():
    rows = [_row(31, sigma_live=37.55), _row(2, sigma_live=37.6)]
    vt = wt.build_payload(_t(), rows=rows)["tape"]["vol_trend"]
    assert vt["direction"] == "flat"
    assert "vol_trend" not in wt.build_payload(_t())["tape"]


def test_vol_trend_omitted_on_a_late_day_ruler_and_outage_windows():
    # τ→0: the ATM solve balloons on clock mechanics — not a vol signal. The
    # guard reads the CURRENT scan's ruler (the newest row in the window).
    rows = [_row(31, sigma_live=35.0), _row(2, sigma_live=44.0)]
    t = _t(range_ruler={"quality": "late_day"})
    assert "vol_trend" not in wt.build_payload(t, rows=rows)["tape"]
    # a reference hours old is an outage artifact, not a 30-min window
    rows2 = [_row(180, sigma_live=35.0), _row(2, sigma_live=45.0)]
    assert "vol_trend" not in wt.build_payload(_t(), rows=rows2)["tape"]


# --- momentum ----------------------------------------------------------------
def test_momentum_reads_building_on_the_top_strike():
    mass_ref = [[7450.0, 80.0], [7500.0, 80.0]]
    mass_cur = [[7450.0, 95.0], [7500.0, 80.0]]
    volg_ref = [[7450.0, 1000.0], [7500.0, 500.0]]
    volg_cur = [[7450.0, 4000.0], [7500.0, 600.0]]
    rows = ([_row(12, mass=mass_ref, volg=volg_ref)]
            + [_row(10 - i, mass=mass_ref, volg=volg_ref) for i in range(4)])
    t = _t()
    t["gex_views"]["mass_by_strike"] = mass_cur
    t["gex_views"]["vol_gross_by_strike"] = volg_cur
    mom = wt.build_payload(t, rows=rows)["momentum"]
    assert "last 5 scans" in mom["window"]
    top = mom["by_strike"]["strike_at_+0.00sigma"]
    assert top["read"] == "building" and top["gex_share_d_pp"] > 0
    assert top["vol_d"] == 3000
    # keys are σ-distances — no absolute strike leaks
    assert "7450" not in json.dumps(mom)


def test_momentum_absent_without_enough_scans():
    rows = [_row(4, mass=[[7450.0, 80.0]]), _row(2, mass=[[7450.0, 80.0]])]
    t = _t()
    t["gex_views"]["mass_by_strike"] = [[7450.0, 82.0]]
    assert "momentum" not in wt.build_payload(t, rows=rows)


# --- dex change + the 30-min path -------------------------------------------
def test_dex_change_30min_is_timestamp_true():
    rows = [_row(31, net_dex=1.5e11), _row(2, net_dex=1.6e11)]
    t = _t()
    t["dex_views"] = {"net_dex_total": 1.73e11, "dex_above_spot": 0.18,
                      "dex_below_spot": 0.82}
    blk = wt.build_payload(t, rows=rows)["dealer_delta_dex"]
    assert blk["net_change_30min_bn"] == pytest.approx(23.0, abs=0.01)
    # no honest ~30-min reference → the field stays absent, never a guess
    blk2 = wt.build_payload(t, rows=[_row(180, net_dex=1.5e11)])["dealer_delta_dex"]
    assert "net_change_30min_bn" not in blk2


def test_moved_last_30min_is_timestamp_true_and_needs_history():
    rows = [_row(35, spot=7430.0), _row(2, spot=7449.0)]
    tape = wt.build_payload(_t(), rows=rows)["tape"]
    assert tape["moved_last_30min_sigma"] == pytest.approx(0.19, abs=0.005)
    # a 5-minute-old day has no 30-min read (never a silently shortened window)
    assert "moved_last_30min_sigma" not in wt.build_payload(
        _t(), rows=[_row(5, spot=7430.0)])["tape"]
    # SE review + adversarial replay (08-10, convergent finding): after a scan
    # outage the nearest old-enough row can be HOURS old — a 3-hour drift must
    # not ship under a 30-minute name, and must not false-trip abnormal_tape
    p = wt.build_payload(_t(), rows=[_row(180, spot=7390.0)])
    assert "moved_last_30min_sigma" not in p["tape"]
    assert "history" not in p                 # the fake 0.56σ sprint is gone


def test_atm_iv_null_key_is_honest_absence_never_a_derived_constant():
    # SE review 08-10 (HIGH): a wt-11 row carrying atm_iv: null RECORDS the
    # 1%-of-spot fallback — deriving an "IV" back out of sigma_live returns
    # the constant ~15.9%, and a native→fallback seam then reads as a fake
    # 8-vol-pt crash that arms vanna/charm. The key itself is the era gate.
    r_fallback = _row(2); r_fallback["atm_iv"] = None
    assert wt._row_atm_iv(r_fallback) is None       # never derived
    r_old = _row(2)                                 # pre-wt-11 row: no key
    assert "atm_iv" not in r_old
    assert wt._row_atm_iv(r_old) == pytest.approx(
        37.6 * (252.0 ** 0.5) / 7450.0, rel=1e-6)   # derivation still exact
    r_live = _row(2); r_live["atm_iv"] = 0.14
    assert wt._row_atm_iv(r_live) == 0.14
    # end to end: a native→fallback seam ships NO vol_trend, not a crash read
    rows = [_row(31, sigma_live=35.0)]
    t = _t(atm_iv=None, sigma_live=18.0)            # fallback-priced current scan
    assert "vol_trend" not in wt.build_payload(t, rows=rows)["tape"]


def test_nan_dex_net_is_absent_never_a_fabricated_change():
    # SE review 08-10 (MED): NaN rode the `is not None` gate, printed "$nanbn",
    # and the old `or 0.0` fabricated a ±150bn 30-min change from thin air
    rows = [_row(31, net_dex=1.5e11), _row(2, net_dex=1.6e11)]
    t = _t()
    t["dex_views"] = {"net_dex_total": float("nan"), "dex_above_spot": 0.2,
                      "dex_below_spot": 0.8}
    assert "dealer_delta_dex" not in wt.build_payload(t, rows=rows)


# --- history flags (the under-pull guard) ------------------------------------
def test_history_flags_level_unseen_and_abnormal_day():
    rows = [_row(200 - i, spot=7400.0 + (i % 5)) for i in range(35)]
    t = _t(spot=7449.0)                        # above every prior scan
    h = wt.build_payload(t, rows=rows)["history"]
    assert h["level_unseen_today"] is True
    t2 = _t(spot=7250.0, prior_close=7460.0)   # ~2σ day move
    assert wt.build_payload(t2, rows=rows)["history"]["abnormal_tape"] is True
    # a quiet in-range scan carries no history block at all
    t3 = _t(spot=7401.0)
    assert "history" not in wt.build_payload(t3, rows=rows[:20])


# --- measured-empty vs unmeasured (sr-5) -------------------------------------
def test_one_sided_cluster_surface_ships_a_clear_flag():
    # a real one-sided book: heavy put-side gamma below spot, only noise-floor
    # dust above — the call side was MEASURED (the window reaches past spot)
    # and holds nothing
    nbs = [[7300.0 + 10 * i, -4e9 if i < 6 else -6e9] for i in range(10)] \
        + [[7460.0, -1e7], [7470.0, -5e6]]     # window covers above; no cluster
    t = _t()
    t["gex_views"]["net_by_strike"] = nbs
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert "wall_cluster_above" not in dm
    assert dm["wall_cluster_above_clear"].startswith("MEASURED CLEAR")
    assert "wall_cluster_below" in dm          # the measured side still ships
    # NO surface at all → both flags absent (absence = not measured)
    dm2 = wt.build_payload(_t())["dealer_map_gravity"]
    assert "wall_cluster_above_clear" not in dm2
    assert "wall_cluster_below_clear" not in dm2


def test_clear_flag_never_speaks_past_a_clipped_window():
    # SE review 08-10: a clipped book whose strikes STOP at spot did not
    # measure the far side — "clear" there is the exact sr-5 inversion. The
    # side stays honestly absent instead.
    nbs = [[7300.0 + 10 * i, -4e9 if i < 6 else -6e9] for i in range(10)]
    t = _t()                                    # spot 7450.29 — no strike above
    t["gex_views"]["net_by_strike"] = nbs
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert "wall_cluster_above" not in dm
    assert "wall_cluster_above_clear" not in dm  # unmeasured, not clear


# --- the doctrine + the grant ------------------------------------------------
def test_doctrine_teaches_the_new_blocks_and_the_reach():
    head, _ = wt._prompt_parts(wt.build_payload(_t()))
    assert "lefteye_rag.py" in head            # the history CLI is named
    assert "WebSearch" in head
    assert "NEVER the trigger" in head         # history stays subordinate
    assert "MEASURED-EMPTY" in head.upper()
    assert "move_30min_sigma" in head
    assert "magnet_band" in head
    assert "vol_trend" in head
    # the dead words are truly gone from the room
    assert "charm_drift_into_close" not in head
    assert "naive_net_sign" not in head


def test_rag_cmd_pins_the_venv_python_and_the_skill_path():
    assert wt._RAG_CMD.endswith("lefteye_rag.py")
    assert wt._ALLOWED_TOOLS[0].startswith("Bash(") and \
        wt._ALLOWED_TOOLS[0].endswith(":*)")
    assert wt._ALLOWED_TOOLS[1] == "WebSearch"


# --- observe records a memory slice ------------------------------------------
def test_observe_records_a_rag_slice_fail_open(monkeypatch, tmp_path):
    import lefteye_rag
    monkeypatch.delenv("WATCHTOWER_DISABLE", raising=False)
    monkeypatch.setattr(wt, "CONFIG", tmp_path / "nope.json")
    monkeypatch.setattr(wt, "_ask_claude", lambda *a, **k: (
        {"fired": True, "direction": "put", "magnitude_sigma": 0.4,
         "range_sigma": [-0.1, 0.6], "horizon_min": 60, "conviction": 0.7,
         "stance": "settle", "scene": "stretched into the put wall",
         "would_change_mind": "flow flips"}, None, 1.0))
    t = _t()
    t["reversion_extreme"].update({"fired": True, "armed": True,
                                   "direction": "put"})
    rec = wt.observe(t, now=NOW)
    assert rec and rec["fired"] is True
    slices = lefteye_rag._read_jsonl(lefteye_rag._slices_path("2026-08-07"))
    assert len(slices) == 1
    assert slices[0]["era"] == "wt-11"
    assert slices[0]["meta"]["vector"] == "down"
    assert slices[0]["narrative"].startswith("stretched into the put wall")
    # and a broken memory must never cost the scan row (fail-open)
    monkeypatch.setattr(lefteye_rag, "record_slice",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    rec2 = wt.observe(t, now=NOW + timedelta(minutes=20))
    assert rec2 and "error" not in rec2


def test_memory_stores_the_blind_read_never_a_reveal_sentence(monkeypatch, tmp_path):
    """SE review 08-10 (MED): a sentence authored on the REVEAL pass was
    written with Head A's verdict visible — recallable "the gates fired put;
    agreeing" served into a future blind pass is exactly the anchoring the
    08-02 rule blocks. The diary row ships the revised verdict; the MEMORY
    keeps the blind one."""
    import lefteye_rag
    monkeypatch.delenv("WATCHTOWER_DISABLE", raising=False)
    monkeypatch.setattr(wt, "CONFIG", tmp_path / "nope.json")
    calls = {"n": 0}

    def fake_ask(prompt, model, timeout=None, system=None):
        # gates fired → the tower takes 3 BLIND votes, then (on disagreement
        # with a non-unanimous majority) one REVEAL pass that may revise.
        calls["n"] += 1
        if calls["n"] in (1, 3):               # blind majority: stand down
            return ({"fired": False, "direction": None, "magnitude_sigma": None,
                     "range_sigma": [-0.2, 0.2], "horizon_min": 60,
                     "conviction": 0.4, "stance": "settle",
                     "scene": "blind read: two-sided pin, no edge",
                     "would_change_mind": "a fired break"}, None, 1.0)
        if calls["n"] == 2:                    # one stray blind fire → NOT unanimous
            return ({"fired": True, "direction": "call", "magnitude_sigma": 0.3,
                     "range_sigma": [-0.2, 0.6], "horizon_min": 60,
                     "conviction": 0.5, "stance": "settle",
                     "scene": "blind stray vote", "would_change_mind": "x"}, None, 1.0)
        return ({"fired": True, "direction": "put", "magnitude_sigma": 0.5,   # reveal: capitulates
                 "range_sigma": [-0.2, 0.8], "horizon_min": 60,
                 "conviction": 0.8, "stance": "settle",
                 "scene": "the gates fired put and I agree with them",
                 "would_change_mind": "gates stand down",
                 "overrides": []}, None, 1.0)

    monkeypatch.setattr(wt, "_ask_claude", fake_ask)
    t = _t()
    t["reversion_extreme"].update({"fired": True, "armed": True,
                                   "direction": "put"})
    rec = wt.observe(t, now=NOW)
    assert rec["fired"] is True and rec["revised"] is True   # the row revised
    sl = lefteye_rag._read_jsonl(lefteye_rag._slices_path("2026-08-07"))
    assert len(sl) == 1
    assert sl[0]["narrative"].startswith("blind read:")      # memory stayed blind
    assert "gates fired put" not in sl[0]["narrative"]
    assert sl[0]["meta"]["vector"] == "none"                 # the blind stand-down
