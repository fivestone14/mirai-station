"""HEAD B (the Watchtower) — every test runs offline: the model call is always
monkeypatched, so the suite can never spawn a real `claude -p` subprocess."""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watchtower as wt

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 6, 11, 38, tzinfo=ET)


def _telemetry(fired=True, direction="put", armed=True):
    """A realistic diary row at decision time (spot 7450, σ 106.9)."""
    return {
        "ts": NOW.isoformat(), "ticker": "SPX", "spot": 7450.29, "sigma": 106.9,
        "gamma_sign": "negative", "gamma_flip": 7480.0,
        "call_wall": 7550.0, "put_wall": 7350.0,
        "range_em": 0.62, "variance_ratio": 0.88, "vix_ts": 0.93,
        "regime": "pinning",
        "reversion_extreme": {"fired": fired, "armed": armed, "direction": direction,
                              "gap_stretch": -1.13, "vwap_stretch": -1.2,
                              "reaction": True, "runway_ok": True,
                              "wall_dist_call": 0.93, "wall_dist_put": -0.94,
                              "magnet": 7420.0},
        "gex_views": {"aggressor_flow": -0.4, "pin_top_share": 0.31,
                      "vex_sign": "negative", "cex_sign": "positive",
                      "magnet": 7420.0},   # TRUE magnet — always-on in real rows
    }


def _verdict(fired=True, direction="put", conviction=0.8):
    return {"fired": fired, "direction": direction, "magnitude_sigma": 0.6,
            "range_sigma": [-0.2, 0.9], "horizon_min": 60,
            "conviction": conviction, "scene": "stretched into the put wall",
            "would_change_mind": "flow flips positive"}


@pytest.fixture(autouse=True)
def _isolated_diary(monkeypatch, tmp_path):
    """build_payload now reads the diary (the wt-8 soft-side stuck-vane guard) —
    every test must see a throwaway state dir, never the production one."""
    monkeypatch.setattr(wt, "STATE_DIR", tmp_path / "state")


@pytest.fixture
def armed_tower(monkeypatch, tmp_path):
    """The tower armed for offline use: kill-switch off, state dirs isolated,
    config absent (falls back to the pinned model)."""
    monkeypatch.delenv("WATCHTOWER_DISABLE", raising=False)
    monkeypatch.setattr(wt, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(wt, "CONFIG", tmp_path / "nope.json")
    return tmp_path


# --- prefilter ---------------------------------------------------------------
def test_quiet_scan_is_not_interesting():
    ok, why = wt.interesting({"fired": False, "armed": False, "gap_stretch": 0.1,
                              "vwap_stretch": -0.2, "wall_dist_call": 2.0,
                              "wall_dist_put": -1.9})
    assert not ok and why == "quiet"


def test_armed_stretched_or_wall_pressed_scans_are_interesting():
    assert wt.interesting({"armed": True})[0]
    assert wt.interesting({"gap_stretch": -0.8})[0]
    assert wt.interesting({"wall_dist_put": -0.5})[0]


# --- payload hygiene ----------------------------------------------------------
def test_blind_payload_hides_gate_verdicts_and_absolute_levels():
    p = wt.build_payload(_telemetry())
    text = json.dumps(p)
    assert "gates_verdict_head_a" not in p            # verdicts hidden (blind pass)
    assert "7450" not in text and "7550" not in text  # no absolute index levels
    assert p["dealer_map_gravity"]["magnet_sigma_from_spot"] == -0.28   # (7420-7450.29)/106.9
    assert "NEGATIVE" in p["dealer_map_gravity"]["gamma_sign_at_spot"]  # sign spelled out
    assert p["tape"]["last_bar_turned_back"] is True  # raw tape fact stays visible


def test_payload_carries_gravity_motion_when_present():
    # the motion pack's defensive half: "the fade leans on a melting wall —
    # stand down" only works if Head B can SEE the film, not just the photo
    motion = {"lookback_min": 10.0, "morning_guard": False,
              "call_wall_melt_share": -0.04, "put_wall_melt_share": None,
              "call_wall_relocated": False, "put_wall_relocated": None,
              "magnet_walk_sigma": 0.2, "flip_creep_sigma": 0.1,
              "grip_slope": 0.05, "soft_side": "up", "gamma_above_share": 0.3}
    p = wt.build_payload({**_telemetry(), "gex_motion": motion})
    # N3 (wt-5): the film arrives as WORDED facts, not a raw unlabeled dict — the
    # model must never have to guess what "melt −0.041" means
    gm = p["gravity_motion"]
    assert gm["call_wall"].startswith("MELTING")
    assert "4.0%" in gm["call_wall"] and "pre-breakout" in gm["call_wall"]
    assert gm["magnet"].startswith("WALKING UP")
    assert gm["soft_side"].startswith("UP")
    assert "put_wall" not in gm                         # a missing diff stays absent
    assert list(p)[-1] == "note"                        # payload hygiene: note stays last
    # absent ≠ zero: no motion diff this scan → no key at all
    assert "gravity_motion" not in wt.build_payload(_telemetry())
    assert "gravity_motion" not in wt.build_payload({**_telemetry(), "gex_motion": None})


def test_reveal_payload_shows_the_gates_as_evidence():
    p = wt.build_payload(_telemetry(), reveal_gates=True)
    g = p["gates_verdict_head_a"]
    assert g["fired"] is True and g["direction"] == "put"
    assert "EVIDENCE" in g["note"]


# --- wt-4 payload: charm drift + tenor terrain (H6/H2/H8) -----------------------
def test_charm_drift_word_gated_and_direction_mapping():
    # N11 (wt-5): the WORD only speaks when the engine's charm_word_ok gate is open —
    # net dealer charm said "UP into the close" on 804/806 live rows (a constant, not
    # a signal), so the gate ships False until charm earns a graded record.
    t = _telemetry()
    t["gex_views"]["cex_sign"] = "negative"
    assert "charm_drift_into_close" not in wt.build_payload(t)["dealer_map_gravity"]
    t["gex_views"]["charm_word_ok"] = False
    assert "charm_drift_into_close" not in wt.build_payload(t)["dealer_map_gravity"]
    # ...and when the gate is EARNED open, the 3/3 derivation panel mapping holds:
    # negative charm ⇒ hedge BUYING into the close ⇒ UP; positive ⇒ DOWN; unknown ⇒ absent
    t["gex_views"]["charm_word_ok"] = True
    assert wt.build_payload(t)["dealer_map_gravity"]["charm_drift_into_close"].startswith("UP")
    t["gex_views"]["cex_sign"] = "positive"
    assert wt.build_payload(t)["dealer_map_gravity"]["charm_drift_into_close"].startswith("DOWN")
    t["gex_views"]["cex_sign"] = "unknown"
    assert "charm_drift_into_close" not in wt.build_payload(t)["dealer_map_gravity"]


def test_structural_walls_reach_filtered_and_deduped():
    t = _telemetry()
    # in reach and distinct from the near wall → attached
    t["call_wall_tenor"], t["put_wall_tenor"] = 7650.0, 7250.0
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert dm["structural_call_wall_sigma_above"] is not None
    assert dm["structural_put_wall_sigma_below"] is not None
    # out of reach (9σ-class) → absent, never a far-wall anchor
    t["call_wall_tenor"] = 9000.0
    assert "structural_call_wall_sigma_above" not in wt.build_payload(t)["dealer_map_gravity"]
    # identical to the near wall (fallback landed on tenor) → suppressed, not
    # shown twice as independent confirmation
    t["call_wall_tenor"] = t["call_wall"]
    assert "structural_call_wall_sigma_above" not in wt.build_payload(t)["dealer_map_gravity"]


def test_dated_structure_far_walls_reach_the_tower_tagged():
    # H8 v2: the old ≤3σ filter dropped EVERY real dated wall EVERY day (live walls
    # sit 4.7-13.8σ out) — the tower never saw the outer terrain. Far walls now ride
    # the payload explicitly tagged FAR; in-reach walls stay plain; junk beyond the
    # 15σ sanity cap is still dropped.
    t = _telemetry()
    t["dated_gex"] = {"bands": [
        {"band": "monthly", "dte": 5, "age_days": 0, "call_wall": 7500.0, "put_wall": 7300.0},
        {"band": "quarter_end", "dte": 80, "age_days": 3, "call_wall": 8000.0, "put_wall": 7000.0}]}
    p = wt.build_payload(t)
    ds = p["dated_structure"]
    assert len(ds["bands"]) == 2                       # the far band is no longer invisible
    near = next(b for b in ds["bands"] if b["band"] == "monthly")
    far = next(b for b in ds["bands"] if b["band"] == "quarter_end")
    assert abs(near["call_wall_sigma_above"]) <= 3 and "call_wall_reach" not in near
    assert abs(far["call_wall_sigma_above"]) > 3
    assert far["call_wall_reach"].startswith("FAR")
    assert far["put_wall_reach"].startswith("FAR")
    # a wall beyond the 15σ sanity cap is still dropped (junk, not terrain)
    t2 = _telemetry()
    t2["dated_gex"] = {"bands": [{"band": "quarter_end", "dte": 80,
                                  "call_wall": 99000.0, "put_wall": 100.0}]}
    assert "dated_structure" not in wt.build_payload(t2)
    # no dated book → key absent (absence ≠ null)
    assert "dated_structure" not in wt.build_payload(_telemetry())


def test_tie_regime_silences_net_gex_sign_axis():
    # H3: when the engine called the sign a tie, the residual's sign is noise —
    # the fingerprint must not fire 'net-GEX flipped sign' interrupts on it
    t = _telemetry()
    t["gex_views"]["net_gex"] = 5e8
    t["gex_views"]["regime"] = "uncertain"
    fp = wt.scene_fingerprint(t)
    assert fp["net_gex_sign"] == 0
    t2 = _telemetry()
    t2["gex_views"]["net_gex"] = -5e8
    t2["gex_views"]["regime"] = "uncertain"
    changed, why = wt.big_change(fp, wt.scene_fingerprint(t2))
    assert "net-GEX" not in " ".join(why or [])


# --- verdict schema guard ------------------------------------------------------
def test_validate_verdict_clamps_and_normalizes():
    v = wt.validate_verdict({**_verdict(), "conviction": 3.0, "magnitude_sigma": 99})
    assert v["conviction_stated"] == 1.0 and v["magnitude_sigma"] == 5.0
    assert v["direction"] == "put" and v["fired"] is True


def test_validate_verdict_rejects_garbage():
    assert wt.validate_verdict(None) is None
    assert wt.validate_verdict({"fired": "yes"}) is None            # non-bool
    assert wt.validate_verdict({"fired": True, "direction": "up"}) is None
    ok = wt.validate_verdict({"fired": False, "direction": "call"})
    assert ok["direction"] is None                                  # no-fire → no direction


# --- vote fold -----------------------------------------------------------------
def test_majority_two_of_three_fires_with_agreement():
    votes = [wt.validate_verdict(_verdict()), wt.validate_verdict(_verdict()),
             wt.validate_verdict(_verdict(fired=False, direction=None))]
    final, agreement = wt.majority(votes)
    assert final["fired"] and final["direction"] == "put"
    assert agreement == round(2 / 3, 2)


def test_majority_stand_down_when_votes_split_against_fire():
    votes = [wt.validate_verdict(_verdict(fired=False, direction=None))] * 2 + \
            [wt.validate_verdict(_verdict())]
    final, agreement = wt.majority(votes)
    assert not final["fired"] and agreement == round(2 / 3, 2)


# --- observe(): the full judged path, model stubbed ------------------------------
def _quiet(t):
    """Neutralize the interesting() prefilter so only the hybrid-wake logic
    (heartbeat / interrupt) can decide whether a scan is judged."""
    t["reversion_extreme"].update(fired=False, armed=False, gap_stretch=0.1,
                                  vwap_stretch=0.1, wall_dist_call=2.0,
                                  wall_dist_put=-2.0,
                                  magnet=None)   # un-armed scans carry NO revert target
    return t


def test_observe_quiet_scan_writes_tiny_skip_row(armed_tower, monkeypatch):
    t = _quiet(_telemetry(fired=False, armed=False))
    # a recent look with an UNCHANGED scene → neither heartbeat nor interrupt
    monkeypatch.setattr(wt, "_last_look", lambda now: (5.0, wt.scene_fingerprint(t)))
    rec = wt.observe(t, now=NOW)
    assert rec == {"prompt_version": wt.PROMPT_VERSION, "skipped": "quiet"}


def test_observe_respects_daily_call_cap(armed_tower, monkeypatch):
    monkeypatch.setattr(wt, "_today_context", lambda now: ([], wt.DAILY_CALL_CAP))
    rec = wt.observe(_telemetry(), now=NOW)
    assert rec["skipped"] == "cap"


def test_observe_fail_open_on_timeout(armed_tower, monkeypatch):
    monkeypatch.setattr(wt, "_ask_claude", lambda *a, **k: (None, "timeout", 60.0))
    rec = wt.observe(_telemetry(), now=NOW)
    assert rec["error"] == "timeout" and "fired" not in rec


def test_observe_never_raises_even_when_the_call_layer_explodes(armed_tower, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("subprocess table on fire")
    monkeypatch.setattr(wt, "_ask_claude", _boom)
    rec = wt.observe(_telemetry(), now=NOW)
    assert "error" in rec                              # a record, not an exception


def test_observe_agreement_path_votes_and_skips_reveal(armed_tower, monkeypatch):
    calls = []
    def fake_ask(prompt, model, timeout=60.0):
        calls.append(prompt)
        return _verdict(), None, 8.0                   # tower agrees with gates (put)
    monkeypatch.setattr(wt, "_ask_claude", fake_ask)
    rec = wt.observe(_telemetry(fired=True, direction="put"), now=NOW)
    assert rec["fired"] and rec["direction"] == "put"
    assert rec["agrees_with_gates"] is True and rec["revised"] is False
    assert rec["votes"]["n"] == wt.VOTES_ON_FIRE       # fire-worthy → 3 votes
    assert len(calls) == wt.VOTES_ON_FIRE              # and NO reveal pass
    assert all("gates_verdict_head_a" not in c for c in calls)   # every vote was blind
    assert rec["conviction"] == 1.0                    # 3/3 votes agreed


def test_observe_reveal_cannot_delete_a_unanimous_majority(armed_tower, monkeypatch):
    # N19 (wt-5): 3 unanimous blind FIRE votes vs 1 capitulating reveal sample — on
    # 07-10 every one of 5 revisions was the tower abandoning its own fire to agree
    # with the gates. The majority now STANDS; the dissent is recorded, visibly.
    calls = []
    def fake_ask(prompt, model, timeout=60.0):
        calls.append(prompt)
        if "gates_verdict_head_a" in prompt:           # the reveal pass
            return _verdict(fired=False, direction=None), None, 8.0
        return _verdict(), None, 8.0                   # blind votes: fire put, 3/3
    monkeypatch.setattr(wt, "_ask_claude", fake_ask)
    rec = wt.observe(_telemetry(fired=False, direction=None), now=NOW)  # gates stood down
    assert len(calls) == wt.VOTES_ON_FIRE + 1          # 3 blind votes + 1 reveal
    assert rec["blind"]["fired"] is True               # what it thought unprompted
    assert rec["fired"] is True and rec["revised"] is False   # the majority stands
    assert rec["reveal_dissent"] == {"fired": False, "direction": None, "overrides": []}
    assert rec["conviction"] == 1.0                    # describes the SHIPPED call
    assert rec["agrees_with_gates"] is False


def test_observe_reveal_can_revise_a_split_vote_and_refolds_conviction(armed_tower, monkeypatch):
    # N19 flip side: a NON-unanimous blind verdict may still be revised — and the
    # shipped conviction then re-folds over ALL samples (votes + reveal), so it
    # describes the revised call rather than the majority it replaced.
    seq = [_verdict(), _verdict(), _verdict(fired=False, direction=None)]   # 2-1 split
    def fake_ask(prompt, model, timeout=60.0):
        if "gates_verdict_head_a" in prompt:
            return _verdict(fired=False, direction=None), None, 8.0
        return seq.pop(0), None, 8.0
    monkeypatch.setattr(wt, "_ask_claude", fake_ask)
    rec = wt.observe(_telemetry(fired=False, direction=None), now=NOW)
    assert rec["fired"] is False and rec["revised"] is True
    # 4 samples total: 2 fire-put, 2 stand-down (incl. the reveal) → conviction 0.5
    assert rec["conviction"] == 0.5
    assert "reveal_dissent" not in rec


# --- wt-5: the tower gets eyes (N2 N3 N4 N5 N6 N15 N19 + N1/N16 payload) --------
def test_validate_verdict_clamps_stance_to_the_three_honest_answers():
    # N4: fight / settle / no_read — junk or absent records as no_read, never invented
    v = wt.validate_verdict({**_verdict(), "stance": "fight"})
    assert v["stance"] == "fight"
    v = wt.validate_verdict({**_verdict(), "stance": "explode"})
    assert v["stance"] == "no_read"
    v = wt.validate_verdict(_verdict())
    assert v["stance"] == "no_read"


def test_event_clock_countdown_and_blind_calendar(monkeypatch):
    # N2: an FOMC statement 20 minutes out MUST reach the payload as a countdown
    import lefteye_event_lens as ev
    cal = [{"date": NOW.date().isoformat(), "kind": "FOMC", "time_et": "14:00"}]
    monkeypatch.setattr(ev, "load_calendar", lambda path=None: cal)
    t1340 = NOW.replace(hour=13, minute=40)
    ec = wt._event_clock(t1340, {})
    assert ec["kind"] == "FOMC" and ec["minutes_until"] == 20
    assert "erupt" in ec["note"]
    # after the print: minutes_since + the lens's surprise ratio when present
    t1420 = NOW.replace(hour=14, minute=20)
    ec2 = wt._event_clock(t1420, {"event_lens": {"surprise_ratio": 1.4}})
    assert ec2["minutes_since"] == 20 and ec2["surprise_ratio"] == 1.4
    # an EMPTY horizon is surfaced as blindness, never silently absent
    monkeypatch.setattr(ev, "load_calendar", lambda path=None: [])
    ec3 = wt._event_clock(t1340, {})
    assert ec3 and ec3["kind"] is None and "blind" in ec3["note"]
    # ...and the payload carries it
    payload = wt.build_payload({**_telemetry()})
    assert "scheduled_event" in payload


def test_flat_tape_confirms_nothing(monkeypatch):
    # N6 (wt-8 Fix 2): a dead-flat WINDOWED options tape and a dead-flat order book must
    # both read FLAT — never "buyers pressing" — and say they confirm nothing
    t = _telemetry()
    t["gex_theta"] = {"flow_recent": 0.0}
    t["lob_flow"] = {"tilt": 0.004, "regime": "balanced", "tape_trades": 100}
    p = wt.build_payload(t)
    assert "FLAT" in p["live_flow"]["tape_recent_window"]
    assert "NOTHING" in p["live_flow"]["tape_recent_window"]
    assert "FLAT" in p["order_flow"]["book_tilt"]


def test_windowed_tape_is_the_only_flow_read(monkeypatch):
    # wt-8 Fix 2: only the windowed read rides; the whole-day cumulative is gone entirely
    t = _telemetry()
    t["gex_theta"] = {"flow_recent": -0.62}
    p = wt.build_payload(t)
    assert p["live_flow"]["tape_recent_window"].startswith("-0.62")
    assert "sellers" in p["live_flow"]["tape_recent_window"]
    assert "tape_day_average" not in p["live_flow"]
    # a cold window ≠ zero flow: the whole live_flow block stays absent
    t2 = _telemetry()
    t2["gex_theta"] = {"flow_recent": None}
    assert "live_flow" not in wt.build_payload(t2)


def test_magnet_demoted_to_location_under_every_regime():
    # wt-8 (supersedes N15's short-gamma-only caution): the magnet word is a
    # LOCATION note — never a drift base case — and the demotion holds at any
    # hour under any gamma sign, not just short gamma
    for sign in ("negative", "positive", None):
        t = _telemetry()
        t["gamma_sign"] = sign
        w = wt.build_payload(t)["dealer_map_gravity"]["pull_toward_magnet"]
        assert "LOCATION/late-day pin target" in w and "~33%" in w
        assert "NOT a directional default" in w
        assert "base case" not in w and "drift" not in w


def test_scale_ruler_emitted_for_sigma_point_conversion():
    # wt-8 Fix 3a: points_per_sigma rides so the tower can reconcile σ-targets with the
    # point-range facts; it is a SCALE, not an absolute level (no index level leaks)
    p = wt.build_payload(_telemetry())
    assert p["scale"]["points_per_sigma"] == 106.9
    text = json.dumps(p)
    assert "7450" not in text and "7550" not in text


def test_net_gex_binding_is_sign_explicit_own_field_and_tie_suppressed():
    # wt-8 Fix 5: net/gross share at spot — + reads PIN, − reads AMPLIFICATION — in its
    # own field, suppressed when the gamma sign is unknown (the tie band already caps)
    t = _telemetry()                       # gamma_sign negative
    t["gex_views"]["net_gex"] = -4e9
    t["gex_views"]["gross_gex"] = 8e9
    b = wt.build_payload(t)["dealer_map_gravity"]["net_gex_binding"]
    assert "AMPLIFICATION" in b and "-50%" in b
    t2 = _telemetry(); t2["gamma_sign"] = "positive"
    t2["gex_views"]["net_gex"] = 6e9; t2["gex_views"]["gross_gex"] = 8e9
    b2 = wt.build_payload(t2)["dealer_map_gravity"]["net_gex_binding"]
    assert "PIN" in b2 and "+75%" in b2
    # unknown sign (tie band) → suppressed
    t3 = _telemetry(); t3["gamma_sign"] = None
    t3["gex_views"]["net_gex"] = 1e7; t3["gex_views"]["gross_gex"] = 8e9
    assert "net_gex_binding" not in wt.build_payload(t3)["dealer_map_gravity"]
    # missing/zero gross → fail-open absent
    t4 = _telemetry(); t4["gex_views"]["net_gex"] = -4e9
    assert "net_gex_binding" not in wt.build_payload(t4)["dealer_map_gravity"]


def test_shove_asymmetry_wording_is_non_directional_and_needs_objective_confirm():
    # wt-8 Fix 7: the accelerant side is the LOWER-RESISTANCE side, never "the cheaper
    # direction to move", and it outranks soft_side only on an OBJECTIVE confirmed move
    sw = wt._shove_words({"shove_up_margin": 0.8, "shove_down_margin": -0.9,
                          "shove_sigma": 0.4, "net_gex_spot": -3e9,
                          "up_flips": False, "down_flips": True}, -3e9)
    a = sw["asymmetry"]
    assert "LOWER-RESISTANCE side IF something pushes" in a and "not a push itself" in a
    assert "CHEAPER direction to move" not in a
    assert "never your own read of 'confirmed'" in a


def test_prompt_carries_the_wt8_corrections():
    p = wt._prompt(wt.build_payload(_telemetry()))
    # Fix 1: magnet-crossed-spot dropped from the flip triggers
    assert "magnet crossed spot" not in p
    assert "gamma sign flipped, flow flipped hard" in p
    # Fix 2: the whole-day average is no longer named
    assert "tape_day_average" not in p
    # Fix 3: pace is conviction-lowering, not a hard stand-down, with the ignition exemption
    assert "CONVICTION-LOWERING guidance, NOT a hard stand-down" in p
    assert "EXEMPTION" in p and "not-yet-elevated" in p
    # Fix 5: binding strength taught as a conviction scaler
    assert "BINDING STRENGTH IS A CONVICTION SCALER" in p
    # Fix 7: accelerant side needs objective confirmation already in the payload
    assert "OBJECTIVE confirmed move" in p and "never your own read of 'confirmed'" in p
    # Fix 8: macro_mood is named so the overnight read is consulted
    assert "day_context.macro_mood" in p


def test_breach_and_road_reach_the_payload():
    # N1/N16: containment facts — a broken fence and an open road — must be visible
    t = _telemetry()
    t["wall_breach"] = {"side": "call", "wall": 7550.0, "spot": 7556.0,
                        "overshoot_sigma": 0.06}
    t["regime_road"] = {"kind": "breach", "side": "call", "level": 7550.0}
    p = wt.build_payload(t)
    assert p["wall_breach_event"]["side"] == "call"
    assert "do not fade" in p["wall_breach_event"]["note"]
    assert "OPEN" in p["containment_road"]["state"]
    assert "wall_breach_event" not in wt.build_payload(_telemetry())


# --- wt-7 payload: siege effort-at-walls (REVERSED SIGN) -----------------------
def _siege_rec(**over):
    return {"health": "OK", "saturated": False, "baseline": "robust", "ratio": 12.05,
            "towers": [
                {"kind": "call_wall", "level": 7550.0, "status": "engaged",
                 "effort_pct": 84.0, "verdict": "SIEGE", "outcome": None,
                 "near_spot": False},
                {"kind": "put_wall", "level": 7350.0, "status": "resolved",
                 "effort_pct": 12.0, "verdict": "QUIET", "outcome": "HOLD",
                 "near_spot": False},
                {"kind": "magnet", "level": 7420.0, "status": "engaged",
                 "effort_pct": 50.0, "verdict": "NEUTRAL", "outcome": None,
                 "near_spot": False},
            ], **over}


def test_siege_reaches_payload_with_the_reversed_sign_spelled_out():
    # wt-7: a sieged wall reads BREAK, a quiet wall reads HOLD, and the payload
    # wording itself forbids the intuitive "absorption = strength" misread
    p = wt.build_payload({**_telemetry(), "siege": _siege_rec()})
    rows = p["siege_effort_at_walls"]["towers"]
    assert [r["wall"] for r in rows] == ["call_wall", "put_wall"]  # NEUTRAL absent
    assert "BREAK" in rows[0]["read"] and "REVERSED SIGN" in rows[0]["read"]
    assert "HOLD" in rows[1]["read"]
    assert rows[1]["already_resolved"] == "HOLD"      # a graded episode carries its outcome
    assert "already_resolved" not in rows[0]
    # σ-distances only — the no-absolute-levels hygiene rule holds for siege too
    assert rows[0]["level_sigma_from_spot"] == 0.93   # (7550-7450.29)/106.9
    assert "7550" not in json.dumps(p)
    # a robust baseline needs no caveat
    assert "baseline_maturity" not in p["siege_effort_at_walls"]


def test_siege_baseline_maturity_caveat_rides_until_robust():
    p = wt.build_payload({**_telemetry(), "siege": _siege_rec(baseline="cold")})
    assert "cold" in p["siege_effort_at_walls"]["baseline_maturity"]


def test_siege_absent_on_every_guard():
    # payload hygiene: degraded feed, saturated session, verdictless towers, or no
    # siege record at all — the block is ABSENT, never a zero
    assert "siege_effort_at_walls" not in wt.build_payload(_telemetry())
    for bad in (_siege_rec(health="DEGRADED"), _siege_rec(saturated=True),
                _siege_rec(towers=[{"kind": "call_wall", "level": 7550.0,
                                    "status": "watching", "effort_pct": None,
                                    "verdict": None, "outcome": None,
                                    "near_spot": True}])):
        assert "siege_effort_at_walls" not in wt.build_payload(
            {**_telemetry(), "siege": bad})


def test_siege_near_spot_verdict_gated_on_robust_baseline():
    # wt-8 Fix 4: a spot-hugging (near_spot) siege verdict reaches the payload ONLY under a
    # robust baseline; cold/warming drops it (dormant earn-trust path)
    near_tower = [{"kind": "call_wall", "level": 7550.0, "status": "engaged",
                   "effort_pct": 84.0, "verdict": "SIEGE", "outcome": None,
                   "near_spot": True}]
    robust = _siege_rec(baseline="robust", towers=near_tower)
    assert "siege_effort_at_walls" in wt.build_payload({**_telemetry(), "siege": robust})
    cold = _siege_rec(baseline="cold", towers=near_tower)
    assert "siege_effort_at_walls" not in wt.build_payload({**_telemetry(), "siege": cold})


def test_siege_prompt_is_subordinate_to_the_gamma_regime():
    # wt-8 Fix 4: heavy effort → BREAK stays, but it is subordinate — under long gamma a
    # pin can HOLD despite heavy effort
    p = wt._prompt(wt.build_payload({**_telemetry(), "siege": _siege_rec()}))
    assert "SUBORDINATE to the gamma regime" in p
    assert "a pin can HOLD despite heavy" in p
    assert "expect that wall to BREAK" in p     # the reversed sign survives


def test_observe_disabled_by_env(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_DISABLE", "1")
    assert wt.observe(_telemetry(), now=NOW) is None


def test_observe_is_silent_outside_market_hours(armed_tower, monkeypatch):
    def _no_call(*a, **k):
        raise AssertionError("the tower must not call the model off-hours")
    monkeypatch.setattr(wt, "_ask_claude", _no_call)
    assert wt.observe(_telemetry(), now=NOW.replace(hour=20, minute=0)) is None  # after close
    assert wt.observe(_telemetry(), now=NOW.replace(hour=4, minute=0)) is None   # pre-market
    assert wt.observe(_telemetry(), now=NOW.replace(day=11)) is None             # 2026-07-11 Sat
    assert wt._market_is_live(NOW) is True                                       # Mon 11:38 ET


# --- hybrid wake: scene fingerprint + change detector (Phase 2) ----------------
def test_scene_fingerprint_normalizes_and_is_none_safe():
    fp = wt.scene_fingerprint(_telemetry())
    assert fp["magnet_sd"] == -0.28 and fp["regime"] == "pinning"
    assert fp["gamma_sign"] == "negative"
    empty = wt.scene_fingerprint({})
    assert empty["magnet_sd"] is None and empty["flow"] is None


def test_big_change_none_when_no_prior_or_scene_stable():
    cur = wt.scene_fingerprint(_telemetry())
    assert wt.big_change(None, cur) == (False, "")   # first look is the heartbeat's job
    assert wt.big_change(cur, cur)[0] is False        # stable scene = no interrupt


def test_big_change_flags_regime_flip_flip_cross_and_flow():
    base = _telemetry()
    prev = wt.scene_fingerprint(base)
    changed, why = wt.big_change(prev, wt.scene_fingerprint({**base, "regime": "trending"}))
    assert changed and "regime" in why
    # flip was +0.28σ above spot; drop it well below → price crossed the flip
    changed, why = wt.big_change(prev, wt.scene_fingerprint({**base, "gamma_flip": base["spot"] - 50}))
    assert changed and "gamma flip" in why
    # aggressor flow reverses −0.4 → +0.5 (past the FLOW_FLIP_MIN force)
    flipped = {**base, "gex_views": {**base["gex_views"], "aggressor_flow": 0.5}}
    changed, why = wt.big_change(prev, wt.scene_fingerprint(flipped))
    assert changed and "flow" in why


# --- hybrid wake: observe() paths (Phase 1 heartbeat, Phase 2 interrupt) --------
_STAND_DOWN = lambda *a, **k: ({"fired": False, "direction": None,
                                "magnitude_sigma": 0.0, "range_sigma": None,
                                "horizon_min": 60, "conviction": 0.6,
                                "scene": "quiet drift under the magnet",
                                "would_change_mind": "flow turns"}, None, 6.0)


def test_observe_hourly_heartbeat_wakes_a_quiet_scan(armed_tower, monkeypatch):
    t = _quiet(_telemetry(fired=False, armed=False))
    monkeypatch.setattr(wt, "_last_look", lambda now: (75.0, wt.scene_fingerprint(t)))
    monkeypatch.setattr(wt, "_ask_claude", _STAND_DOWN)
    rec = wt.observe(t, now=NOW)
    assert "skipped" not in rec and rec["interest"] == "hourly heartbeat"
    assert rec["fired"] is False


def test_observe_first_look_of_day_wakes(armed_tower, monkeypatch):
    t = _quiet(_telemetry(fired=False, armed=False))
    monkeypatch.setattr(wt, "_last_look", lambda now: (None, None))
    monkeypatch.setattr(wt, "_ask_claude", _STAND_DOWN)
    assert wt.observe(t, now=NOW)["interest"] == "hourly heartbeat"


def test_observe_interrupt_wakes_on_big_shift(armed_tower, monkeypatch):
    t = _quiet(_telemetry(fired=False, armed=False))          # now: pinning
    prev = wt.scene_fingerprint({**t, "regime": "trending"})  # 15 min ago: trending
    monkeypatch.setattr(wt, "_last_look", lambda now: (15.0, prev))
    monkeypatch.setattr(wt, "_ask_claude", _STAND_DOWN)
    rec = wt.observe(t, now=NOW)
    assert rec["interest"].startswith("shift ·") and "regime" in rec["interest"]


def test_observe_interrupt_spam_guard_holds_a_quiet_row(armed_tower, monkeypatch):
    t = _quiet(_telemetry(fired=False, armed=False))
    prev = wt.scene_fingerprint({**t, "regime": "trending"})  # a real shift...
    monkeypatch.setattr(wt, "_last_look", lambda now: (4.0, prev))  # ...only 4 min ago
    rec = wt.observe(t, now=NOW)                              # < INTERRUPT_MIN_GAP
    assert rec == {"prompt_version": wt.PROMPT_VERSION, "skipped": "quiet"}


# --- the snippet (Phase 3): leads with the scene, ends on NEXT -----------------
def test_render_snippet_leads_with_scene_and_ends_on_next():
    rec = wt.validate_verdict(_verdict())                     # fired put
    snip = wt.render_snippet(rec, _telemetry(), NOW, "hourly heartbeat")
    assert snip.startswith("🗼")
    assert "See:" in snip and "Read:" in snip
    assert "NEXT:" in snip and "DOWN" in snip                 # put → fade DOWN
    assert "flips it" in snip                                 # would_change_mind line


def test_render_snippet_stand_down_has_no_target():
    rec = wt.validate_verdict(_verdict(fired=False, direction=None))
    snip = wt.render_snippet(rec, _telemetry(), NOW, "hourly heartbeat")
    assert "stand down" in snip and "NEXT" in snip
    assert "toward" not in snip                                # no magnet target on a no-fire


def test_big_change_flags_wall_break_magnet_netgex_and_vix():
    base = _telemetry()
    prev = wt.scene_fingerprint(base)
    spot = base["spot"]
    ch, why = wt.big_change(prev, wt.scene_fingerprint({**base, "call_wall": spot - 50}))
    assert ch and "call wall" in why                          # price crossed the call wall
    ch, why = wt.big_change(prev, wt.scene_fingerprint({**base, "put_wall": spot + 50}))
    assert ch and "put wall" in why
    ch, why = wt.big_change(prev, wt.scene_fingerprint(
        {**base, "gex_views": {**base["gex_views"], "magnet": spot + 70}}))
    assert ch and "magnet" in why                             # relocated > MAGNET_JUMP_SIGMA
    # the armed-only reversion magnet must NOT drive the jump axis (it is None on
    # ~99% of scans and wall-clamped when set — the interrupt watches gex_views)
    ch, why = wt.big_change(prev, wt.scene_fingerprint(
        {**base, "reversion_extreme": {**base["reversion_extreme"], "magnet": spot + 70}}))
    assert "magnet" not in why
    pos = wt.scene_fingerprint({**base, "gex_views": {**base["gex_views"], "net_gex": 2.0}})
    neg = wt.scene_fingerprint({**base, "gex_views": {**base["gex_views"], "net_gex": -2.0}})
    ch, why = wt.big_change(pos, neg)
    assert ch and "net-GEX" in why
    ch, why = wt.big_change(prev, wt.scene_fingerprint({**base, "vix_ts": base["vix_ts"] + 0.12}))
    assert ch and "VIX" in why


# --- magnet-source semantics (wt-3, 2026-07-10 audit) --------------------------
def test_payload_prior_anchors_on_true_magnet_when_quiet():
    """Quiet/un-armed scan: rev.magnet is None but the TRUE gex_views magnet is
    always on — the gravity prior must read it, never go UNKNOWN."""
    t = _telemetry(fired=False, armed=False)
    t["reversion_extreme"]["magnet"] = None
    p = wt.build_payload(t)
    g = p["dealer_map_gravity"]
    assert g["magnet_sigma_from_spot"] == round((7420.0 - 7450.29) / 106.9, 2)
    assert "UNKNOWN" not in g["pull_toward_magnet"]
    assert "fade_revert_target_sigma" not in g     # absent, never null (un-armed)


def test_payload_prior_prefers_true_magnet_over_clamped_target():
    """Armed scan where the fade target was wall-clamped away from the pin: the
    prior stays on the TRUE magnet; the clamped target rides as its own field."""
    t = _telemetry()
    t["gex_views"]["magnet"] = 7630.0              # raw pin above the call wall
    t["reversion_extreme"].update(magnet=7550.0, magnet_out_of_band=True)
    p = wt.build_payload(t)
    g = p["dealer_map_gravity"]
    assert g["magnet_sigma_from_spot"] == round((7630.0 - 7450.29) / 106.9, 2)
    assert g["fade_revert_target_sigma"] == round((7550.0 - 7450.29) / 106.9, 2)
    assert g["fade_target_wall_clamped"] is True


def test_payload_falls_back_to_rev_magnet_on_gx_blackout():
    """The one real gx-None corner (native all-null-IV blackout): the prior
    degrades to the reversion magnet rather than going blank."""
    t = _telemetry()
    del t["gex_views"]["magnet"]
    p = wt.build_payload(t)
    assert p["dealer_map_gravity"]["magnet_sigma_from_spot"] == \
        round((7420.0 - 7450.29) / 106.9, 2)


def test_snippet_names_the_true_magnet():
    """The display magnet and the prior must never name different magnets:
    both read gex_views first."""
    t = _telemetry()
    t["gex_views"]["magnet"] = 7630.0
    t["reversion_extreme"]["magnet"] = 7550.0      # clamped fade target
    snip = wt.render_snippet(wt.validate_verdict(_verdict()), t, NOW, "test")
    assert "magnet 7630" in snip and "7550" not in snip.split("NEXT")[0]


def test_lob_magnet_only_attached_when_book_derived():
    """A borrowed gravity magnet (magnet_source='gravity') must not be re-labeled
    as an order-book read — the model would see the same pin twice."""
    t = _telemetry()
    t["lob_flow"] = {"regime": "balanced", "tilt": 0.1,
                     "magnet": 7420.0, "magnet_source": "gravity"}
    p = wt.build_payload(t)
    assert "lob_magnet_sigma_from_spot" not in p.get("order_flow", {})
    t["lob_flow"]["magnet_source"] = "defense"     # genuinely book-derived → attach
    p = wt.build_payload(t)
    assert p["order_flow"]["lob_magnet_sigma_from_spot"] is not None


# --- hardening from the deep-dive audit ---------------------------------------
def test_validate_verdict_coerces_non_list_overrides():
    v = wt.validate_verdict({**_verdict(), "overrides": 1})    # hostile: not a list
    assert v is not None and v["overrides"] == []             # coerced, not crashed


def test_validate_verdict_rejects_non_finite_numbers():
    v = wt.validate_verdict({**_verdict(), "magnitude_sigma": float("inf"),
                             "conviction": float("nan")})      # json parses NaN/Inf
    assert v["magnitude_sigma"] is None and v["conviction_stated"] is None


def test_crossed_ignores_sub_deadband_jitter():
    assert wt._crossed(-0.01, 0.01) is False                  # jitter at the level
    assert wt._crossed(-0.30, 0.20) is True                   # a real cross
    assert wt._crossed(0.30, -0.01) is True                   # clear move to just below


def test_last_look_falls_back_over_malformed_ts(monkeypatch):
    rows = [{"ts": NOW.replace(hour=10, minute=0).isoformat(),
             "watchtower": {"votes": {"n": 1}}, "spot": 7450.0, "sigma": 100.0},
            {"ts": "not-a-date",
             "watchtower": {"votes": {"n": 1}}, "spot": 7450.0, "sigma": 100.0}]
    monkeypatch.setattr(wt, "_today_rows", lambda now: rows)
    mins, _ = wt._last_look(NOW)                               # 11:38 vs valid 10:00 call
    assert mins is not None and mins > 0                       # malformed ts didn't wipe clock


# ---------------------------------------------------------------- m8 (2026-07-13)
# The two layers the tower was blind to, and the cocked/fired distinction it never
# had. The scene below is the REAL 12:43 ET row from 2026-07-13 — the scan where the
# tower called CALL into a book whose accelerant side was DOWN, on a break that never
# fired. These tests pin that scene to the opposite reading.
_0713 = {
    "shove": {"shove_sigma": 0.3552, "shove_v": 2, "net_gex_spot": -3013538082.93,
              "shove_up_net_gex": 3509538289.01, "shove_down_net_gex": -8603662110.35,
              "shove_up_margin": 1.1646, "shove_down_margin": -2.855,
              "up_flips": True, "down_flips": False},
    "speedometer": {"rv_pace": 1.363, "rod_range_pts": 31.2, "jump_z": 2.434,
                    "jump_sign": None, "semivar_down_share": 0.658, "n_bars": 203,
                    "baseline_days": 5, "quality": "ok"},
    "motion": {"soft_side": "up", "gamma_above_share": 0.4936, "lookback_min": 10.02},
}


def test_shove_words_name_the_accelerant_side():
    w = wt._shove_words(_0713["shove"], -3013538082.93)
    assert "ACCELERANT" in w["if_price_shoves_DOWN"]
    assert "ABSORBING" in w["if_price_shoves_UP"]
    assert "DOWN is the ACCELERANT side" in w["asymmetry"]
    assert "DEEPENING to 2.9×" in w["if_price_shoves_DOWN"]
    assert "walks BACK INTO the calm zone" in w["if_price_shoves_UP"]   # up_flips
    assert "-3.01B" in w["net_gex_at_spot"]


def test_shove_words_symmetric_book_manufactures_no_edge():
    w = wt._shove_words({"shove_up_margin": 0.9, "shove_down_margin": 0.95,
                         "shove_sigma": 0.5, "net_gex_spot": 1e9}, 1e9)
    assert "SYMMETRIC" in w["asymmetry"]
    assert "ACCELERANT side" not in w["asymmetry"]


def test_shove_words_absent_never_zero():
    assert wt._shove_words({}, None) is None
    assert wt._shove_words({"shove_up_margin": 0.4}, None) is None    # half a read is no read


def test_speed_words_call_the_selling_tape():
    s = wt._speed_words(_0713["speedometer"])
    assert "SELLING-DOMINATED" in s["who_owns_the_motion"]
    assert "66%" in s["who_owns_the_motion"]
    assert "JUMPS" in s["jumps"]
    assert "31 pts" in s["rest_of_day_range_pts"]


def test_speed_words_stay_silent_on_a_dirty_feed():
    assert wt._speed_words({**_0713["speedometer"], "quality": "degraded"}) is None
    assert wt._speed_words({}) is None


def test_soft_side_carries_its_share_and_flags_a_coin_flip():
    m = wt._motion_words(_0713["motion"])
    assert "49.4% of the field's mass sits ABOVE spot" in m["soft_side"]
    assert "BARELY soft" in m["soft_side"]      # 0.4936 is a coin flip, not a thesis


def test_cocked_break_is_not_a_direction():
    t = _telemetry(fired=False, direction="none")
    t["level_reclaim"] = {"break_state": "cocked", "cock_direction": "call",
                          "cock_level": 7550.0, "cock_level_kind": "round",
                          "fired": False, "direction": "none"}
    p = wt.build_payload(t)
    bl = p["break_lens_head_c"]
    assert bl["has_it_actually_fired"] is False
    assert "COCKED, NOT FIRED" in bl["note"]
    assert "a WATCH level, not a direction" in bl["note"]
    assert "Do NOT take escape_direction as a directional call" in bl["note"]


def test_fired_break_is_a_direction():
    t = _telemetry(fired=False, direction="none")
    t["level_reclaim"] = {"break_state": "fired", "fired": True, "direction": "put",
                          "level": 7500.0, "level_kind": "prior_low"}
    p = wt.build_payload(t)
    assert p["break_lens_head_c"]["has_it_actually_fired"] is True
    assert "BREAK FIRED" in p["break_lens_head_c"]["note"]


def test_payload_carries_shove_and_speed_into_the_prompt():
    t = _telemetry()
    t["gex_views"] = {**t.get("gex_views", {}), "shove": _0713["shove"],
                      "net_gex": -3013538082.93}
    t["speedometer"] = _0713["speedometer"]
    p = wt.build_payload(t)
    assert "DOWN is the ACCELERANT side" in p["terrain_asymmetry"]["asymmetry"]
    assert "SELLING-DOMINATED" in p["tape"]["tape_speed"]["who_owns_the_motion"]
    prompt = wt._prompt(p)
    assert "COCKED IS NOT FIRED" in prompt
    assert "TERRAIN ASYMMETRY IS THE BOOK'S SLOPE, NOT A PUSH" in prompt
    assert "ACCELERANT" in prompt        # the fact reaches the model, not just the rule
    assert "flow_asymmetry" not in prompt     # wt-8: the flow framing is gone


def test_payload_omits_both_layers_when_absent():
    p = wt.build_payload(_telemetry())
    assert "terrain_asymmetry" not in p       # absent, never a null/zero read
    assert "tape_speed" not in p["tape"]


# ------------------------------------------------------------------ wt-8 (2026-07-25)
# REGIME AS PERMISSION — the 07-22/23 forensic: the same potential-energy put-lean
# (terrain slope + soft side + magnet) lost every 07-22 call under LONG gamma and won
# under SHORT gamma 07-23. The gamma sign now decides which plays are allowed at all.
def test_prompt_carries_the_permission_slip():
    prompt = wt._prompt(wt.build_payload(_telemetry()))
    assert "THE GAMMA SIGN IS THE PERMISSION SLIP" in prompt
    assert "FORBIDDEN" in prompt                       # positive gamma bans drift bets
    assert "CONFIRMED, not unknown" in prompt          # negative must be confirmed
    assert "STAND DOWN — no directional bet on an unknown regime" in prompt
    assert "07-22" in prompt and "07-23" in prompt     # the system's own ledger cited
    assert "base case UP" not in prompt                # the old magnet default is gone
    assert "GRAVITY RULES" not in prompt


def _stuck_diary(tmp_path, day, values):
    f = tmp_path / "state" / "reversion" / f"{day}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(json.dumps({"gex_motion": {"soft_side": v}})
                           for v in values) + "\n")


def test_soft_side_stuck_vane_detected_across_days(tmp_path):
    # 10 rows today + 45 yesterday, all "down" → the vane is stuck; "up" is not
    _stuck_diary(tmp_path, "2026-07-06", ["down"] * 10)
    _stuck_diary(tmp_path, "2026-07-05", ["down"] * 45)
    assert wt._soft_side_stuck(NOW, "down") is True
    assert wt._soft_side_stuck(NOW, "up") is False     # stuck the OTHER way ≠ stuck
    assert wt._soft_side_stuck(None, "down") is False  # no clock → fail-open


def test_soft_side_stuck_vane_needs_real_history_and_a_real_lean():
    # empty state dir (autouse isolation) → never stuck
    assert wt._soft_side_stuck(NOW, "down") is False


def test_soft_side_stuck_vane_thin_or_mixed_history_is_not_stuck(tmp_path):
    _stuck_diary(tmp_path, "2026-07-06", ["down"] * 20)          # < MIN_READS
    assert wt._soft_side_stuck(NOW, "down") is False
    _stuck_diary(tmp_path, "2026-07-06", ["down"] * 30 + ["up"] * 20)  # 60/40 turnable
    assert wt._soft_side_stuck(NOW, "down") is False


def test_stuck_soft_side_reads_as_terrain_fact_in_the_payload(tmp_path):
    motion = {"soft_side": "down", "gamma_above_share": 0.72, "lookback_min": 5.0}
    t = {**_telemetry(), "gex_motion": motion}
    # fresh vane: today's opinion, share attached
    p = wt.build_payload(t)
    assert p["gravity_motion"]["soft_side"].startswith("DOWN — less gravity mass")
    # stuck vane (>90% of the last 50 rows): worded as terrain, not opinion
    _stuck_diary(tmp_path, "2026-07-06", ["down"] * 49 + ["up"])
    p2 = wt.build_payload(t)
    assert p2["gravity_motion"]["soft_side"] == \
        "down (NEAR-CONSTANT for days — treat as terrain fact, not today's opinion)"


# ------------------------------------------------- wt-8 doctrine gaps (2026-07-25)
# Chop band at the flip, GW cluster dominance, profile cleanliness — the three
# audited holes between docs/wt8-doctrine.md and what the tower could actually see.
def test_permission_slip_leads_the_gravity_block():
    dm = wt.build_payload(_telemetry())["dealer_map_gravity"]
    assert list(dm)[:2] == ["gamma_sign_at_spot", "pull_toward_magnet"]


def test_chop_band_word_only_inside_the_band():
    # fixture flip 7480 sits 0.28σ out — OUTSIDE the ±0.25σ band → absent
    t = _telemetry()
    assert "regime_transition_band" not in wt.build_payload(t)["dealer_map_gravity"]
    # inside the band (0.09σ): the word appears and binds the fact
    t["gamma_flip"] = 7460.0
    w = wt.build_payload(t)["dealer_map_gravity"]["regime_transition_band"]
    assert w.startswith("IN THE CHOP BAND (0.09σ from the flip)")
    assert "NO directional play" in w
    # the band is two-sided: a flip just BELOW spot is the same chop zone
    t["gamma_flip"] = 7440.0
    w = wt.build_payload(t)["dealer_map_gravity"]["regime_transition_band"]
    assert w.startswith("IN THE CHOP BAND (0.10σ from the flip)")
    # exactly at the threshold counts as inside (≤, not <)
    t["gamma_flip"] = 7450.29 + wt.FLIP_CHOP_BAND_SIGMA * 106.9
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert "(0.25σ from the flip)" in dm["regime_transition_band"]
    # no flip read at all → absent (absence ≠ "far from the flip")
    t["gamma_flip"] = None
    assert "regime_transition_band" not in wt.build_payload(t)["dealer_map_gravity"]


# a two-cluster surface around the fixture's spot 7450.29 / σ 106.9:
# put cluster 7400-7410 (peak 7400, strength 9.8 → tier ''), call cluster
# 7500-7510 (peak 7500, strength 17 → the strongest → tier '''); the lone
# 7420/-1 and 7520/+2 strikes die at the 25% concentration floor
_SURF_CLEAN = [[7400, -5.0], [7410, -4.8], [7420, -1.0],
               [7500, 9.0], [7510, 8.0], [7520, 2.0]]


def test_gw_cluster_dominance_reaches_the_walls_block():
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = _SURF_CLEAN
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert dm["wall_cluster_above"] == {
        "label": "GWc'''", "prime_tier": "'''",
        "cluster_span_sigma": [0.47, 0.56], "peak_sigma_from_spot": 0.47}
    assert dm["wall_cluster_below"] == {
        "label": "GWp''", "prime_tier": "''",
        "cluster_span_sigma": [-0.47, -0.38], "peak_sigma_from_spot": -0.47}
    assert dm["profile_clean"].startswith("CLEAN")


def test_messy_interleave_reads_as_a_stand_down_word():
    # a put-dominant cluster peak ABOVE a call-dominant one = contradictory hierarchy
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = [[7400, 8.0], [7410, 7.0],
                                       [7500, -9.0], [7510, -8.0]]
    w = wt.build_payload(t)["dealer_map_gravity"]["profile_clean"]
    assert w.startswith("MESSY PROFILE")
    assert "ABOVE a call-dominant cluster peak" in w and "STAND DOWN" in w


def test_flat_surface_is_no_dominant_structure_not_a_clean_bill():
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = [[7400, 0.0], [7410, 0.0]]
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert "no dominant structure" in dm["profile_clean"]
    assert "STAND DOWN" in dm["profile_clean"]
    assert "wall_cluster_above" not in dm and "wall_cluster_below" not in dm


def test_gw_fields_absent_without_a_measured_surface():
    # honest: no net_by_strike → no cluster walls AND no cleanliness verdict —
    # a missing surface must never render as a clean (or messy) bill of health
    dm = wt.build_payload(_telemetry())["dealer_map_gravity"]
    assert "wall_cluster_above" not in dm and "wall_cluster_below" not in dm
    assert "profile_clean" not in dm
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = "hostile-not-a-surface"    # fail-open, not raise
    dm = wt.build_payload(t)["dealer_map_gravity"]
    assert "profile_clean" not in dm


def test_contested_magnet_word_reaches_the_payload():
    t = _telemetry()
    t["gex_views"]["pin_contested"] = True
    t["gex_views"]["pin_zones"] = [{"center": 7420.0, "share": 0.5},
                                   {"center": 7500.0, "share": 0.4}]
    w = wt.build_payload(t)["dealer_map_gravity"]["magnet_contested"]
    assert w.startswith("CONTESTED") and "+0.47σ" in w and "0.80×" in w
    assert "CHOP between them" in w
    # uncontested (or a single zone) → absent, never a null
    t["gex_views"]["pin_contested"] = False
    assert "magnet_contested" not in wt.build_payload(t)["dealer_map_gravity"]
    t["gex_views"]["pin_contested"] = True
    t["gex_views"]["pin_zones"] = [{"center": 7420.0, "share": 0.5}]
    assert "magnet_contested" not in wt.build_payload(t)["dealer_map_gravity"]


def test_prompt_teaches_chop_band_dominance_and_the_messy_gate():
    prompt = wt._prompt(wt.build_payload(_telemetry()))
    assert "IN THE CHOP BAND" in prompt
    assert f"{wt.FLIP_CHOP_BAND_SIGMA}σ of the gamma flip" in prompt
    assert "NO directional play from inside the band" in prompt
    assert "WALL DOMINANCE DECIDES WHICH WALLS MATTER — LABELS DON'T" in prompt
    assert "A ''' wall outranks a ' wall" in prompt
    assert "A MESSY PROFILE IS A STAND-DOWN GATE" in prompt
    assert "NOT a clean bill" in prompt


# ---------------------------------------------- wt-9 doctrine fold-ins (2026-07-27)
def test_prompt_version_is_wt10():
    assert wt.PROMPT_VERSION == "wt-10"


def test_regime_intensity_1d_is_worded_by_the_sign_of_todays_level():
    # gap 1 (pressure-test fix): the WORD branches on sign(cur), not just sign(Δ)
    def word(cur, prev, date="2026-07-24"):
        t = _telemetry()
        t["net_exposure"] = {"net_gamma_total": cur, "prev_close_gamma": prev,
                             "prev_close_date": date, "net_exposure_v": 1}
        return wt.build_payload(t)["dealer_map_gravity"].get("regime_intensity_1d")
    # both negative, getting more negative → amplification DEEPENING (a fast regime)
    assert "AMPLIFICATION is DEEPENING" in word(-11e6, -6e6) and "-83%" in word(-11e6, -6e6)
    # both negative, easing back toward the flip
    assert "AMPLIFICATION is EASING" in word(-6e6, -11e6)
    # STILL POSITIVE, just less so → a long-gamma PIN WEAKENING, NOT a fast regime
    w_pin = word(3e6, 8e6)
    assert "PIN is WEAKENING" in w_pin and "AMPLIFICATION" not in w_pin and "INTENSIFYING" not in w_pin
    # both positive, growing → pin ENTRENCHING
    assert "PIN is ENTRENCHING" in word(8e6, 3e6)
    # crossed zero → a flip event, named as such
    assert "FLIPPED LONG→SHORT" in word(-5e6, 2e6)
    assert "FLIPPED SHORT→LONG" in word(5e6, -2e6)
    # pct SUPPRESSED when the prior book was near-balanced (no exploding ratio on flip days)
    near = word(-11e6, 0.5)
    assert "FLIPPED LONG→SHORT" in near and "%" not in near
    # absent: no key / flat / missing date
    assert word(None, None) is None or "regime_intensity_1d" not in wt.build_payload(_telemetry())["dealer_map_gravity"]
    assert word(5e6, 5e6) is None                       # flat → absent
    assert word(-11e6, -6e6, date=None) is None         # missing prev_close_date → absent


def test_dex_tape_conflict_was_removed():
    # gap 3 (pressure-test): net_dex_total is structurally POSITIVE, so the sign-disagreement
    # collapsed to a biased flow-sign relabel that contradicted flow_signed_read — REMOVED.
    t = _telemetry()
    t["dex_views"] = {"net_dex_total": 1.2e9, "dex_flow_signed": -8e6, "dex_v": 1}
    assert "tape_vs_positioning_conflict" not in wt.build_payload(t)["dealer_delta_dex"]


def test_siege_role_is_a_regime_subordinate_lean_not_an_imperative():
    # gap 4c (pressure-test fix): leaning language, deferred to the gamma regime — no bare
    # "trade THROUGH" / "take profit" imperative that contradicts the siege-subordination rule
    t = _telemetry()
    t["siege"] = {"health": "OK", "saturated": False, "baseline": "robust", "towers": [
        {"kind": "call_wall", "level": 7550.0, "verdict": "QUIET",
         "effort_pct": 20.0, "near_spot": False},
        {"kind": "put_wall", "level": 7350.0, "verdict": "SIEGE",
         "effort_pct": 85.0, "near_spot": False}]}
    by = {r["wall"]: r["role"] for r in wt.build_payload(t)["siege_effort_at_walls"]["towers"]}
    assert "leans HOLD" in by["call_wall"] and "TERMINUS-type" in by["call_wall"]
    assert "leans BREAK" in by["put_wall"] and "PAUSE-type" in by["put_wall"]
    # the break lean must carry the long-gamma caveat, never an unconditional imperative
    assert "does NOT license" in by["put_wall"] and "trade THROUGH this wall" not in by["put_wall"]


def test_air_pocket_gated_to_same_side_and_clean_profile():
    # gap 4d (pressure-test fix): same-side gap only, and suppressed on a MESSY profile
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = [
        [7350, -8.0], [7360, -7.0],            # near put cluster below spot
        [7500, 5.0],                            # near call wall above spot
        [7610, 6.0], [7620, 7.0]]               # far call cluster — a wide same-side gap
    g = wt.build_payload(t)["dealer_map_gravity"]
    assert "AIR POCKET" in g["air_pocket_above"] and "far wall" in g["air_pocket_above"]
    assert "air_pocket_below" not in g          # only one put cluster below → no gap
    # MESSY profile (a put-dominant cluster stranded ABOVE a call-dominant one) → suppressed
    t2 = _telemetry()
    t2["gex_views"]["net_by_strike"] = [
        [7460, 5.0], [7470, 6.0],              # call cluster just above spot
        [7600, -7.0], [7610, -8.0]]            # put cluster HIGHER → MESSY hierarchy
    g2 = wt.build_payload(t2)["dealer_map_gravity"]
    assert "MESSY" in (g2.get("profile_clean") or "")
    assert "air_pocket_above" not in g2 and "air_pocket_below" not in g2
    # adjacent same-side walls (sub-threshold) → absent
    t3 = _telemetry()
    t3["gex_views"]["net_by_strike"] = [[7500, 5.0], [7520, 6.0], [7540, 7.0]]
    assert "air_pocket_above" not in wt.build_payload(t3)["dealer_map_gravity"]


def test_prompt_carries_the_wt9_foldins():
    p = wt._prompt(wt.build_payload(_telemetry()))
    assert "REGIME INTENSITY IS THE 1-DAY TREND" in p
    assert "AN AIR POCKET EXTENDS THE OBJECTIVE" in p
    assert "SUBORDINATE to the gamma sign" in p        # air pocket dormant under long gamma
    assert "target-selection LEAN" in p                # siege role, regime-subordinate
    assert "tape_vs_positioning_conflict" not in p     # the removed field never reaches the prompt


# ---------------------------------------------- wt-10 doctrine fold-ins (2026-07-27)
def _nbs_clean(flip=7480.0):
    """A net_by_strike surface with decisive short control below the flip and
    decisive long control above it, with an ambiguous band in between."""
    return [[7350.0, -1.0], [7400.0, -0.9], [7440.0, -0.05], [7460.0, 0.02],
            [7500.0, 0.08], [7520.0, 0.9], [7550.0, 1.0]]


def test_transition_fences_three_state_and_width():
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = _nbs_clean()
    tz = wt.build_payload(t)["dealer_map_gravity"]["transition_zone_measured"]
    # θ = 0.15 × 1.0 → cT = 7520 (first ≥ +θ above flip), pT = 7400 (first ≤ −θ below)
    assert tz["ct_upper_fence_sigma_from_spot"] == wt._sd(7520.0, 7450.29, 106.9)
    assert tz["pt_lower_fence_sigma_from_spot"] == wt._sd(7400.0, 7450.29, 106.9)
    assert tz["zone_width_sigma"] == round((7520.0 - 7400.0) / 106.9, 2)
    # spot 7450.29 sits between the fences → INSIDE, the measured no-regime read
    assert "INSIDE" in tz["spot_position"] and "NO REGIME" in tz["spot_position"]
    assert "never targets" in tz["note"]


def test_transition_fences_missing_fence_is_named_not_defaulted():
    t = _telemetry()
    # no strike above the flip ever nets decisively long → cT missing, pT measured
    t["gex_views"]["net_by_strike"] = [[7350.0, -1.0], [7400.0, -0.8], [7500.0, 0.05]]
    tz = wt.build_payload(t)["dealer_map_gravity"]["transition_zone_measured"]
    assert "MISSING" in tz["ct_upper_fence"]
    assert "pt_lower_fence_sigma_from_spot" in tz
    assert "zone_width_sigma" not in tz          # width needs BOTH fences


def test_transition_fences_fail_open():
    t = _telemetry()                              # no net_by_strike at all
    assert "transition_zone_measured" not in wt.build_payload(t)["dealer_map_gravity"]
    t["gex_views"]["net_by_strike"] = []
    assert "transition_zone_measured" not in wt.build_payload(t)["dealer_map_gravity"]


def _vols(wall_heavy=True):
    """A gross-volume ladder (total 100k) where the call wall 7550 is either the
    heaviest strike or a dead one; put wall 7350 always minor."""
    base = [[7300.0, 4000], [7350.0, 300], [7400.0, 9000], [7420.0, 12000],
            [7450.0, 20000], [7500.0, 14000], [7600.0, 6000]]
    return base + [[7550.0, 34700 if wall_heavy else 300],
                   [7460.0, 0 if wall_heavy else 34400]]


def test_flow_confirmation_contested_vs_untested():
    t = _telemetry()
    t["gex_views"]["vol_gross_by_strike"] = _vols(wall_heavy=True)
    t["gex_views"]["oi_by_strike"] = [[7550.0, -50000], [7350.0, 40000], [7450.0, 5000]]
    fc = wt.build_payload(t)["flow_confirmation"]
    # call wall: heavy volume + heavy standing OI → CONTESTED
    assert "CONTESTED" in fc["call_wall"]["read"]
    assert fc["call_wall"]["volume_rank"].startswith("1 ")
    # put wall: heavy OI, ~no volume today → UNTESTED standing shelf
    assert "UNTESTED" in fc["put_wall"]["read"]
    assert "never picks a direction" in fc["note"]


def test_flow_confirmation_volume_king_flagged_when_at_neither_wall():
    t = _telemetry()
    t["gex_views"]["vol_gross_by_strike"] = _vols(wall_heavy=False)  # king = 7460
    t["gex_views"]["oi_by_strike"] = []
    fc = wt.build_payload(t)["flow_confirmation"]
    assert fc["volume_king"]["level_sigma_from_spot"] == wt._sd(7460.0, 7450.29, 106.9)
    assert "HEAVIEST" in fc["volume_king"]["read"]


def test_flow_confirmation_thin_tape_abstains():
    t = _telemetry()
    t["gex_views"]["vol_gross_by_strike"] = [[k, v // 10] for k, v in _vols()]  # 10k total
    assert "flow_confirmation" not in wt.build_payload(t)


def test_expected_move_decay_is_a_size_cap_and_sleeps_the_first_hour():
    t = _telemetry()                              # NOW = 11:38 ET → 262m to close
    word = wt.build_payload(t)["tape"]["expected_move_decay"]
    assert f"~{(262 / 390) ** 0.5:.0%}" in word
    assert "SIZE guidance only" in word and "never a direction" in word
    early = dict(t, ts=NOW.replace(hour=9, minute=45).isoformat())   # 15m in → silent
    assert "expected_move_decay" not in wt.build_payload(early)["tape"]
    closed = dict(t, ts=NOW.replace(hour=16, minute=5).isoformat())  # after the bell
    assert "expected_move_decay" not in wt.build_payload(closed)["tape"]


def test_prompt_teaches_the_wt10_reads():
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = _nbs_clean()
    t["gex_views"]["vol_gross_by_strike"] = _vols()
    p = wt._prompt(wt.build_payload(t))
    assert "THE FENCES ARE THE MEASURED VERSION" in p
    assert "FLOW CONFIRMATION SEPARATES LIVE WALLS FROM PAPER SHELVES" in p
    assert "THE DAY'S REACH DECAYS" in p
    assert "boundaries of behaviour" in p


# ------------------------------------------- wt-10 §3.4 strike-tag ladder (2026-07-27)
def _sig(level):
    return wt._sd(level, 7450.29, 106.9)


def test_strike_tags_ranked_walls_mirror_the_table_rules():
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = [
        [7300.0, -0.5], [7350.0, -2.0], [7380.0, -1.0],       # puts: P1=7350, P2=7380, P3=7300
        [7500.0, 1.5], [7550.0, 3.0], [7600.0, 0.4], [7650.0, 0.2],  # calls: C1=7550, C2=7500, C3=7600
    ]
    tags = wt.build_payload(t)["strike_tags"]
    assert tags["ranked_put_walls_sigma_P1_first"] == [_sig(7350.0), _sig(7380.0), _sig(7300.0)]
    assert tags["ranked_call_walls_sigma_C1_first"] == [_sig(7550.0), _sig(7500.0), _sig(7600.0)]
    # Ab is side-agnostic: 3.0 (7550), 2.0 (7350), 1.5 (7500)
    assert tags["abs_gamma_peaks_sigma_Ab1_first"] == [_sig(7550.0), _sig(7350.0), _sig(7500.0)]
    assert "CONFLUENCE OUTRANKS BAR LENGTH" in tags["note"]


def test_strike_tags_oi_peaks_from_side_split_and_net_fallback():
    t = _telemetry()
    t["gex_views"]["oi_side_by_strike"] = [
        [7300.0, 1000, 9000],   # POI (heaviest put OI) + nPOI (most put-dominant)
        [7500.0, 8000, 500],    # COI
        [7450.0, 5500, 5500],   # AbOI (heaviest total, 11000)
    ]
    tags = wt.build_payload(t)["strike_tags"]
    assert tags["put_oi_peak_sigma_POI"] == _sig(7300.0)
    assert tags["call_oi_peak_sigma_COI"] == _sig(7500.0)
    assert tags["total_oi_peak_sigma_AbOI"] == _sig(7450.0)
    assert tags["net_put_oi_peak_sigma_nPOI"] == _sig(7300.0)
    # net-pane fallback (pre-07-27 rows): only nPOI derivable, and only when put-dominant
    t2 = _telemetry()
    t2["gex_views"]["oi_by_strike"] = [[7300.0, -4000], [7500.0, 6000]]
    tags2 = wt.build_payload(t2)["strike_tags"]
    assert tags2["net_put_oi_peak_sigma_nPOI"] == _sig(7300.0)
    assert "put_oi_peak_sigma_POI" not in tags2       # gross per-side not derivable
    # all-call-dominant book → nPOI must NOT fire (the argmaxPos mislabel guard)
    t3 = _telemetry()
    t3["gex_views"]["oi_by_strike"] = [[7300.0, 2000], [7500.0, 6000]]
    assert "net_put_oi_peak_sigma_nPOI" not in wt.build_payload(t3).get("strike_tags", {})


def test_strike_tags_volume_peaks_derive_exactly_from_net_plus_gross():
    t = _telemetry()
    # call=(g+n)/2, put=(g−n)/2: 7420 → 18k call/12k put (CV+nCV), 7400 → 6k call/14k put (PV+nPV)
    t["gex_views"]["vol_by_strike"] = [[7400.0, -8000], [7420.0, 6000]]
    t["gex_views"]["vol_gross_by_strike"] = [[7400.0, 20000], [7420.0, 30000]]
    tags = wt.build_payload(t)["strike_tags"]
    assert tags["call_volume_peak_sigma_CV"] == _sig(7420.0)
    assert tags["put_volume_peak_sigma_PV"] == _sig(7400.0)
    assert tags["net_call_volume_peak_sigma_nCV"] == _sig(7420.0)
    assert tags["net_put_volume_peak_sigma_nPV"] == _sig(7400.0)
    # engine side-split, when present, takes precedence over the derivation
    t["gex_views"]["vol_side_by_strike"] = [[7500.0, 40000, 100], [7400.0, 100, 39000]]
    tags2 = wt.build_payload(t)["strike_tags"]
    assert tags2["call_volume_peak_sigma_CV"] == _sig(7500.0)


def test_strike_tags_volume_family_respects_the_thin_tape_floor():
    t = _telemetry()
    t["gex_views"]["vol_by_strike"] = [[7400.0, -300], [7420.0, 200]]
    t["gex_views"]["vol_gross_by_strike"] = [[7400.0, 900], [7420.0, 800]]  # << floor
    tags = wt.build_payload(t).get("strike_tags", {})
    assert "call_volume_peak_sigma_CV" not in tags


def test_strike_tags_delta_pressure_peaks_need_a_genuine_sign():
    t = _telemetry()
    t["dex_views"] = {"net_dex_total": 1e9,
                      "dex_flow_by_strike": [[7400.0, 2e9], [7500.0, -3e9]]}
    tags = wt.build_payload(t)["strike_tags"]
    assert tags["delta_buy_pressure_peak_sigma_Dplus"] == _sig(7400.0)
    assert tags["delta_sell_pressure_peak_sigma_Dminus"] == _sig(7500.0)
    # an all-buy tape must not emit a D− tag
    t["dex_views"]["dex_flow_by_strike"] = [[7400.0, 2e9], [7500.0, 1e9]]
    assert "delta_sell_pressure_peak_sigma_Dminus" not in wt.build_payload(t)["strike_tags"]


def test_strike_tags_fail_open_and_prompt_teaches_confluence():
    assert "strike_tags" not in wt.build_payload(_telemetry())   # no surfaces at all
    t = _telemetry()
    t["gex_views"]["net_by_strike"] = [[7350.0, -2.0], [7550.0, 3.0]]
    p = wt._prompt(wt.build_payload(t))
    assert "TAG CONFLUENCE OUTRANKS BAR LENGTH" in p
    assert "LOCATIONS, never a direction" in p
