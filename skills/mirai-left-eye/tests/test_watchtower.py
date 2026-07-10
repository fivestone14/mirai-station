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
                      "vex_sign": "negative", "cex_sign": "positive"},
    }


def _verdict(fired=True, direction="put", conviction=0.8):
    return {"fired": fired, "direction": direction, "magnitude_sigma": 0.6,
            "range_sigma": [-0.2, 0.9], "horizon_min": 60,
            "conviction": conviction, "scene": "stretched into the put wall",
            "would_change_mind": "flow flips positive"}


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
    assert p["gravity_motion"] == motion
    assert list(p)[-1] == "note"                        # payload hygiene: note stays last
    # absent ≠ zero: no motion diff this scan → no key at all
    assert "gravity_motion" not in wt.build_payload(_telemetry())
    assert "gravity_motion" not in wt.build_payload({**_telemetry(), "gex_motion": None})


def test_reveal_payload_shows_the_gates_as_evidence():
    p = wt.build_payload(_telemetry(), reveal_gates=True)
    g = p["gates_verdict_head_a"]
    assert g["fired"] is True and g["direction"] == "put"
    assert "EVIDENCE" in g["note"]


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
                                  wall_dist_put=-2.0)
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


def test_observe_disagreement_triggers_blind_then_reveal(armed_tower, monkeypatch):
    calls = []
    def fake_ask(prompt, model, timeout=60.0):
        calls.append(prompt)
        if "gates_verdict_head_a" in prompt:           # the reveal pass
            return _verdict(fired=False, direction=None), None, 8.0
        return _verdict(), None, 8.0                   # blind votes: fire put
    monkeypatch.setattr(wt, "_ask_claude", fake_ask)
    rec = wt.observe(_telemetry(fired=False, direction=None), now=NOW)  # gates passed
    assert len(calls) == wt.VOTES_ON_FIRE + 1          # 3 blind votes + 1 reveal
    assert rec["blind"]["fired"] is True               # what it thought unprompted
    assert rec["revised"] is True                      # the reveal changed its mind
    assert rec["fired"] is False and rec["agrees_with_gates"] is True


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
        {**base, "reversion_extreme": {**base["reversion_extreme"], "magnet": spot + 70}}))
    assert ch and "magnet" in why                             # relocated > MAGNET_JUMP_SIGMA
    pos = wt.scene_fingerprint({**base, "gex_views": {**base["gex_views"], "net_gex": 2.0}})
    neg = wt.scene_fingerprint({**base, "gex_views": {**base["gex_views"], "net_gex": -2.0}})
    ch, why = wt.big_change(pos, neg)
    assert ch and "net-GEX" in why
    ch, why = wt.big_change(prev, wt.scene_fingerprint({**base, "vix_ts": base["vix_ts"] + 0.12}))
    assert ch and "VIX" in why


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
