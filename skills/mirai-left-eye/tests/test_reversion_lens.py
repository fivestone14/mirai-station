"""Tests for the BETA reversion lens — pure logic only (no network).

Anchored to the 2026-06-23 session: the lens must catch the two trades the
existing momentum signals missed (the 7347.6 gap-down capitulation LONG and the
7424 call-wall blow-off SHORT) and must NOT fade in a trending regime.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import reversion_lens as R  # noqa: E402
import lefteye_gex_box as G  # noqa: E402

NOW = datetime.datetime(2026, 6, 23, 10, 30, tzinfo=ZoneInfo("America/New_York"))


def test_beta_is_shadow_by_default():
    # Safety: the lens must ship OFF (records only, no confluence vote).
    assert R.REVERSION_LIVE is False


def test_variance_ratio_directions():
    trend = [100 + i * (i + 1) // 2 for i in range(40)]      # accelerating trend → VR > 1
    assert R.variance_ratio(trend) > 1.0
    revert = [100 + (1 if i % 2 else -1) for i in range(40)]  # zig-zag → VR < 1
    assert R.variance_ratio(revert) < 1.0
    assert R.variance_ratio([1, 2, 3]) is None      # too short → None


def test_classify_regime_needs_two_agreeing():
    assert R.classify_regime("positive", 0.6, 0.8, 0.92)["regime"] == "pinning"
    assert R.classify_regime("negative", 1.2, 1.3, 1.05)["regime"] == "trending"
    assert R.classify_regime("positive", None, None, None)["regime"] == "neutral"  # 1 vote
    # split 2-2 → neutral (no majority)
    assert R.classify_regime("positive", 0.6, 1.3, 1.05)["regime"] == "neutral"


def test_classify_regime_tie_guard_blocks_pinning_on_balanced_book():
    # H3 caveat (2026-07-12): a BALANCED book is not a NEUTRAL book — net gamma at
    # spot ~0 is a hair-trigger. With the gamma vote abstaining on a tie, two quiet
    # tape voters must NOT stamp "pinning" on the highest-breakout-risk book.
    out = R.classify_regime("unknown", 0.6, 0.8, None)   # two pin votes, no gamma
    assert out["regime"] == "neutral" and out["tie_guard"] is True
    assert out["confidence"] <= 0.5
    # a real trending read on a tie book still speaks (the guard is pin-specific)
    out2 = R.classify_regime("unknown", 1.2, 1.3, 1.05)
    assert out2["regime"] == "trending" and out2["tie_guard"] is False
    assert out2["confidence"] <= 0.5                     # ...but never over-confident
    # with a known sign nothing changes
    out3 = R.classify_regime("positive", 0.6, 0.8, 0.92)
    assert out3["regime"] == "pinning" and out3["tie_guard"] is False


def test_sigma_ruler_never_shrinks_on_decay():
    # N10: the same 40 points must not read 0.49σ at 9:30 and 1.35σ at 3:55 when
    # nothing moved but theta decay — the ruler holds the day's opening yardstick...
    assert R.sigma_ruler(81.0, 29.5) == 81.0
    # ...but a REAL vol spike widens it (a 2pm crash is a genuinely bigger day)
    assert R.sigma_ruler(81.0, 120.0) == 120.0
    # first scan of the day: the live σ IS the anchor
    assert R.sigma_ruler(None, 81.0) == 81.0
    assert R.sigma_ruler(None, None) is None


def test_detect_breach_fence_ledger_first_crossing_only():
    # N1 v2: the fence LEDGER — the event fires on the first scan whose spot stands
    # beyond ANY wall recorded today that no earlier spot had exceeded.
    rows = [{"spot": 7540.0, "call_wall": 7550.0, "put_wall": 7450.0}]
    wb = R.detect_breach(rows, 7556.0, 80.0)
    assert wb and wb["side"] == "call" and wb["wall"] == 7550.0
    assert abs(wb["overshoot_sigma"] - 0.075) < 1e-6
    # a continuation scan (an earlier spot already beyond that fence) is not a new event
    rows2 = rows + [{"spot": 7556.0, "call_wall": 7565.0}]
    assert R.detect_breach(rows2, 7560.0, 80.0) is None
    # put-side mirror
    wb3 = R.detect_breach([{"spot": 7460.0, "put_wall": 7450.0}], 7442.0, 80.0)
    assert wb3 and wb3["side"] == "put" and wb3["wall"] == 7450.0
    assert R.detect_breach([], 7500.0, 80.0) is None


def test_detect_breach_catches_the_grinding_breakout():
    # THE GRIND-THROUGH HOLE (verify round): the operative wall re-places OUTWARD as
    # price approaches (0.35σ placement floor), so v1's prev-row-only check needed a
    # flash-crash-class one-minute jump. The ledger remembers the 7550 fence from the
    # morning even after the wall has retreated to 7562 ahead of the grind.
    rows = [{"spot": 7530.0, "call_wall": 7550.0},
            {"spot": 7542.0, "call_wall": 7558.0},
            {"spot": 7548.0, "call_wall": 7562.0}]     # fence retreating ahead of price
    wb = R.detect_breach(rows, 7551.0, 80.0)           # a 3-pt creep, not a jump
    assert wb and wb["side"] == "call" and wb["wall"] == 7550.0
    # ...and once crossed, that fence never re-fires
    rows.append({"spot": 7551.0, "call_wall": 7565.0, "wall_breach": wb})
    assert R.detect_breach(rows, 7553.0, 80.0) is None


def test_road_v2_opens_holds_from_prev_row_and_cannot_resurrect():
    # N16 v2: scan-to-scan continuity, TTL from the ORIGINAL open, no resurrection
    ET_ = ZoneInfo("America/New_York")
    now = datetime.datetime(2026, 7, 13, 11, 0, tzinfo=ET_)
    breach = {"side": "call", "wall": 7550.0, "spot": 7556.0}
    opened = R.road_state([], 7556.0, now, breach)
    assert opened["kind"] == "breach" and opened["opened"] == now.isoformat()
    ten_ago = (now - datetime.timedelta(minutes=10)).isoformat()
    rows = [{"ts": ten_ago,
             "regime_road": {"kind": "breach", "side": "call", "level": 7550.0,
                             "opened": ten_ago}}]
    held = R.road_state(rows, 7558.0, now, None)
    assert held and held["kind"] == "held" and held["opened"] == ten_ago  # stamp carried
    # price reclaimed the calm side → closes
    assert R.road_state(rows, 7544.0, now, None) is None
    # NO RESURRECTION: the previous row recorded the closure (None); an older open
    # road two rows back must never re-open the road on a later poke beyond
    rows_closed = rows + [{"ts": now.isoformat(), "regime_road": None}]
    assert R.road_state(rows_closed, 7558.0, now, None) is None
    # NO SLIDING RENEWAL: a held row with a fresh ts but an old `opened` stamp expires
    old_open = (now - datetime.timedelta(minutes=60)).isoformat()
    rows_slid = [{"ts": ten_ago,
                  "regime_road": {"kind": "held", "side": "call", "level": 7550.0,
                                  "opened": old_open}}]
    assert R.road_state(rows_slid, 7558.0, now, None) is None


def test_reversion_extreme_catches_capitulation_long():
    # 7347.6, prior 7472.79, sigma 95, put_wall 7300, pinning, last bar turning up
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7345, "close": 7351}])
    assert rev["direction"] == "call" and rev["armed"] and rev["fired"]
    assert rev["gap_stretch"] < -1.0


def test_reversion_extreme_fades_blowoff_short_with_overshoot():
    # 7424 is 24 pts PAST the 7400 call wall — must still arm a SHORT fade.
    rev = R.reversion_extreme(7424.0, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7426, "close": 7420}])
    assert rev["direction"] == "put" and rev["armed"] and rev["fired"]
    assert rev["wall_dist_call"] < 0   # negative = overshot the wall


def test_reversion_extreme_blocked_in_trend():
    # Same capitulation, but a TRENDING regime must NOT fire a fade.
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "trending",
                              [{"open": 7345, "close": 7351}])
    assert rev["armed"] is True and rev["fired"] is False


def test_reversion_extreme_quiet_when_not_stretched():
    rev = R.reversion_extreme(7400, 7405, 95.0, 7500, 7300, "pinning", [])
    assert rev["direction"] == "none" and rev["armed"] is False


def test_reversion_extreme_arms_on_vwap_stretch():
    # No gap (spot == prior_close) and no wall nearby, but a deep NEGATIVE VWAP
    # stretch must still arm a LONG fade (the second stretch leg) — and label it.
    rev = R.reversion_extreme(7400, 7400, 95.0, 7600, 7200, "pinning",
                              [{"open": 7398, "close": 7402}], vwap_stretch=-1.6)
    assert rev["direction"] == "call" and rev["armed"] and rev["arm_reason"] == "stretch"
    assert rev["vwap_stretch"] == -1.6


def test_runway_gate_blocks_a_fire_with_no_room_to_the_pin():
    # Same capitulation-long, but the fresh gamma pin sits just above spot → no room
    # to reach target before it stalls. Still ARMED and would-have-fired pre-gate,
    # but the runway gate makes it a non-fire (recorded, not counted).
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7345, "close": 7351}], gamma_flip=7355.0)
    assert rev["armed"] and rev["fired_pre_runway"]            # passed the old gate
    assert rev["runway_ok"] is False and rev["fired"] is False  # runway gate blocks it
    assert rev["runway_sigma"] is not None and rev["runway_sigma"] < 0.30
    assert rev["magnet"] == 7355.0


def test_runway_gate_allows_a_fire_with_room_to_the_pin():
    # Pin far enough above spot → runway ≥ RUNWAY_MIN_SIGMA → counts as a would-fire.
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7345, "close": 7351}], gamma_flip=7390.0)
    assert rev["runway_ok"] is True and rev["fired"] is True
    assert rev["runway_sigma"] >= 0.30 and rev["magnet"] == 7390.0


def test_runway_falls_back_to_opposing_wall_without_a_pin():
    # No gamma_flip (degraded GEX): runway falls back to the opposing wall.
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7345, "close": 7351}])
    assert rev["magnet"] == 7400 and rev["runway_ok"] is True   # 0.55σ to the call wall


def test_runway_in_band_pin_is_not_clamped():
    # A pin already inside [put_wall, call_wall] is left untouched: no flag, no change.
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7345, "close": 7351}], pin_magnet=7390.0)
    assert rev["magnet"] == 7390.0 and rev["magnet_raw"] == 7390.0
    assert rev["magnet_out_of_band"] is False


def test_runway_clamps_out_of_band_pin_to_the_wall_it_reverts_toward():
    # F3: capitulation-long whose fresh pin prints ABOVE the call wall (thin/early OI).
    # Runway must be measured to the wall it can actually reach, not the phantom pin.
    # The raw pin is preserved and the row is flagged.
    rev = R.reversion_extreme(7347.6, 7472.79, 95.0, 7400, 7300, "pinning",
                              [{"open": 7345, "close": 7351}], pin_magnet=7600.0)
    assert rev["direction"] == "call"
    assert rev["magnet_raw"] == 7600.0                 # raw out-of-band pin, preserved
    assert rev["magnet"] == 7400                        # clamped to the call wall above
    assert rev["magnet_out_of_band"] is True
    assert rev["runway_sigma"] == round((7400 - 7347.6) / 95.0, 3)  # honest room to wall


def test_runway_clamp_never_manufactures_a_fire_on_wrong_side_pin():
    # Adversarial (the review's counterexample): a fade-SHORT whose pin prints ABOVE
    # the call wall — the WRONG side for a down-reverting trade. A symmetric clamp
    # would pull it down to the wall and invent a positive downside runway → a phantom
    # fire. The direction-aware clamp leaves a short's pin unclamped unless it is below
    # the put wall, so runway stays negative and it never fires.
    rev = R.reversion_extreme(7560.0, 7472.79, 20.0, 7550, 7450, "pinning",
                              [{"open": 7565, "close": 7558}], pin_magnet=7580.0)
    assert rev["direction"] == "put"
    assert rev["magnet_out_of_band"] is False           # wrong-side pin is NOT clamped
    assert rev["runway_sigma"] < 0 and rev["fired"] is False


def test_level_reclaim_break_with_volume():
    # prior_close 7400 is the nearest level; price breaks clearly above it on 3× volume.
    bars = [{"open": 7398, "close": 7399, "volume": 100} for _ in range(5)]
    bars.append({"open": 7401, "close": 7406, "volume": 300})   # break up on 3× volume
    lvl = R.level_reclaim(7406, 95.0, 7400, 7460, 7340, 7450, 7300, 50.0, bars)
    assert lvl["direction"] == "call" and lvl["fired"]


def test_render_learning_view_handles_empty():
    # Must not crash with no telemetry yet.
    out = R.render_learning_view([], NOW)
    assert "Fade-the-Bounce" in out and "No readings recorded" in out
    assert "How it decides" in out and "What we learned" in out   # new sections present


def test_prospects_table_lists_every_ticker_with_a_status():
    # Empty telemetry → each prospect still appears, marked 'no data yet'.
    table = R._prospects_table([])
    assert "Picklist — prospects & status" in table
    for tk in R.TICKERS:
        assert f"**{tk}**" in table
    assert table.count("⚪ no data yet") == len(R.TICKERS)


def test_prospect_status_mirrors_the_fire_verdict():
    fired = {"regime": "pinning",
             "reversion_extreme": {"armed": True, "fired": True,
                                   "direction": "call", "gap_stretch": 1.4}}
    status, side, stretch = R._prospect_status(fired)
    assert status == "🎯 FIRE (paper)" and side == "LONG" and stretch == "1.4σ"

    no_runway = {"reversion_extreme": {"armed": True, "fired": False,
                                       "fired_pre_runway": True, "direction": "put"}}
    assert R._prospect_status(no_runway)[0] == "⏭️ skip — no runway"

    idle = {"reversion_extreme": {"armed": False}}
    assert R._prospect_status(idle) == ("💤 stand by", "—", "—")


def test_render_includes_the_picklist_at_the_top():
    out = R.render_learning_view([], NOW)
    # Picklist must sit ABOVE the pipeline section so it's the first glance.
    assert out.index("Picklist — prospects & status") < out.index("How it decides")


def _gex_row(ticker, spot, flip, pw=None, cw=None, sign="positive", src="native:1.2e9"):
    return {"ticker": ticker, "spot": spot, "gamma_flip": flip,
            "put_wall": pw, "call_wall": cw, "gamma_sign": sign, "gex_source": src,
            "regime": "pinning", "reversion_extreme": {}}


def test_gex_heatmap_splits_green_above_flip_red_below_and_marks_spot():
    strip = R._gex_heatmap(_gex_row("SPX", 7392, 7350, pw=7300, cw=7450))
    assert "🟩" in strip and "🟥" in strip          # both gamma zones present
    assert "🔵" in strip                             # spot is marked
    assert "🟨" in strip                             # flip transition cell
    assert strip.startswith("**7300**") and strip.endswith("**7450**")


def test_gex_heatmap_degrades_without_wall_structure():
    assert "no wall structure" in R._gex_heatmap(
        {"spot": 7392, "call_wall": None, "put_wall": None})


def test_gex_section_shows_status_metric_and_meaning_per_ticker():
    long_sec = R._gex_section([_gex_row("SPX", 7392, 7350, pw=7300, cw=7450, sign="positive")])
    assert "GEX read — dealer-gamma heat map" in long_sec
    assert "LONG-gamma" in long_sec                         # posture metric
    assert "live GEX" in long_sec                           # provenance surfaced
    short_sec = R._gex_section([_gex_row("SPX", 7392, 7350, pw=7300, cw=7450, sign="negative")])
    assert "SHORT-gamma" in short_sec                       # the other posture


def test_render_includes_gex_at_the_top():
    out = R.render_learning_view([], NOW)
    assert out.index("GEX read") < out.index("How it decides")


def _wf(ts, spot, direction="call", sigma=95):
    """A fresh would-fire telemetry row."""
    return {"ticker": "SPX", "ts": ts, "spot": spot, "sigma": sigma,
            "reversion_extreme": {"fired": True, "direction": direction}}


def _bars(seq):
    """A bars_lookup returning 1-min (high, low) bars for SPX from (HH:MM, hi, lo)."""
    bars = [{"ts": f"2026-06-23T{hm}:00-04:00", "high": h, "low": l} for (hm, h, l) in seq]
    return lambda tk: bars if tk == "SPX" else []


def test_beta_resolver_win_on_target_touch():
    # entry 7350 σ95 LONG → target 7373.75; a 10:20 bar HIGH 7400 touches it.
    rows = [_wf("2026-06-23T10:00:00-04:00", 7350)]
    bars = _bars([("10:05", 7360, 7348), ("10:20", 7400, 7362)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert len(resolved) == 1 and resolved[0]["outcome"] == "win" and resolved[0]["ttr_min"] == 20


def test_beta_resolver_loss_when_stop_touched_first():
    # LONG, stop 7316.75; a 10:10 bar LOW 7310 trips it before any target.
    rows = [_wf("2026-06-23T10:00:00-04:00", 7350)]
    bars = _bars([("10:10", 7355, 7310)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert resolved and resolved[0]["outcome"] == "loss" and resolved[0]["ttr_min"] == 10


def test_beta_resolver_intrabar_spike_caught_by_high():
    # The 5-min CLOSE never clears target, but a bar's HIGH spikes through it → win.
    rows = [_wf("2026-06-23T10:00:00-04:00", 7350)]
    bars = _bars([("10:05", 7380, 7349)])   # high 7380 > target 7373.75; close would be lower
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert resolved and resolved[0]["outcome"] == "win"


def test_beta_resolver_ambiguous_bar_is_pessimistic_loss():
    # One bar touches BOTH lines → scored as a loss (worst-case).
    rows = [_wf("2026-06-23T10:00:00-04:00", 7350)]
    bars = _bars([("10:05", 7400, 7310)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert resolved and resolved[0]["outcome"] == "loss"


def test_beta_resolver_scratch_when_neither_line_touched():
    # Late-day entry; 1-min bars run to the close inside the band → scratch.
    rows = [_wf("2026-06-23T15:00:00-04:00", 7350)]
    bars = _bars([("15:10", 7360, 7345), ("15:30", 7362, 7344), ("15:55", 7358, 7346)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert resolved and resolved[0]["outcome"] == "scratch"


def test_beta_resolver_pending_and_drops_afterhours():
    # After-hours entry dropped; an RTH entry with bars only ~10 min in → pending.
    rows = [_wf("2026-06-23T20:00:00-04:00", 7350),     # after-hours → filtered out
            _wf("2026-06-23T15:00:00-04:00", 7350)]
    bars = _bars([("15:05", 7360, 7345), ("15:10", 7361, 7346)])
    resolved, pending = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert resolved == [] and pending == 1


def test_gamma_vote_respects_flow_downgrade():
    # a flow-flagged (uncertain) pin read must ABSTAIN, not vote pin
    assert R.gamma_vote_sign({"regime": "uncertain", "regime_raw": "long_gamma"}) == "unknown"
    # a CONFIRMED pin (tape seen, agrees) votes its sign-at-spot
    assert R.gamma_vote_sign({"regime": "long_gamma", "regime_raw": "long_gamma",
                              "sign_agrees": True}) == "positive"
    # short_gamma (trend) votes regardless of the tape — it's not the fragile case
    assert R.gamma_vote_sign({"regime": "short_gamma", "regime_raw": "short_gamma"}) == "negative"
    # missing/empty engine read abstains
    assert R.gamma_vote_sign({}) == "unknown"
    assert R.gamma_vote_sign(None) == "unknown"


def test_gamma_vote_fails_safe_on_blind_pin():
    # L1 fix: a long-gamma pin whose tape was never SEEN (sign_agrees None → flow
    # unknown) must ABSTAIN, not vote pin at full confidence (the old fail-open).
    assert R.gamma_vote_sign({"regime": "long_gamma", "regime_raw": "long_gamma",
                              "sign_agrees": None}) == "unknown"
    # absent the key entirely (defensive) also abstains
    assert R.gamma_vote_sign({"regime": "long_gamma", "regime_raw": "long_gamma"}) == "unknown"
    # a blind read then cannot carry a pin verdict alone (needs the other reads)
    v = R.classify_regime(
        R.gamma_vote_sign({"regime": "long_gamma", "regime_raw": "long_gamma",
                           "sign_agrees": None}),
        range_em=0.6, vr=None, vix_ts=None)          # only the (abstaining) gamma read
    assert v["regime"] != "pinning"


def test_veto_flow_prefers_fresh_lob_then_options_then_none():
    # M5/M9 fix: the pin-veto reads the fresh order-book tape first (catches an
    # afternoon flip the whole-day options number misses). determinate_share is a
    # COVERAGE GATE, not a multiplier: above the floor the value is the PURE tilt.
    f, src = R._veto_flow(0.05, {"lob_flow": {"tilt": -0.9, "determinate_share": 0.8}})
    assert src == "lob" and abs(f - (-0.9)) < 1e-9         # pure tilt, not tilt×det
    # thin coverage → too little tape to trust → None (→ blind fail-safe), NOT a
    # small number that would falsely read as "tape confirms the pin"
    assert R._veto_flow(0.30, {"lob_flow": {"tilt": 0.9,
                                            "determinate_share": 0.30}}) == (None, "lob_thin")
    # LOB absent or field-missing → fall back to the whole-day options read
    assert R._veto_flow(0.30, None) == (0.30, "options")
    assert R._veto_flow(0.30, {"lob_flow": {"tilt": None,
                                            "determinate_share": 0.8}}) == (0.30, "options")
    # both blind → None (which makes gamma_vote_sign fail safe upstream)
    assert R._veto_flow(None, None) == (None, "none")
    # NaN never reads as max pressure — it fails the finite guard → options/None
    assert R._veto_flow(None, {"lob_flow": {"tilt": float("nan"),
                                            "determinate_share": 0.8}}) == (None, "none")
    # clamped to [-1, 1]
    assert R._veto_flow(None, {"lob_flow": {"tilt": 1.4,
                                            "determinate_share": 1.0}}) == (1.0, "lob")


def test_lob_veto_threshold_is_source_scaled_and_can_actually_trip():
    # the whole point of the calibration fix: a genuinely heavy order-book tape
    # (|tilt| ~0.3, above FLOW_CONFLICT_LOB 0.25) MUST trip the pin downgrade —
    # under the old options-scaled 0.6 it never could (tilt tops out ~0.26).
    # H5: the lens now also passes flow_kind="lob" (the tape's semantics), and
    # the lob branch trips on EITHER direction (freight-train test).
    heavy = G.reconcile_sign("long_gamma", 0.30, G.FLOW_CONFLICT_LOB, "lob")
    assert heavy["sign"] == "uncertain" and heavy["agrees"] is False
    heavy_dn = G.reconcile_sign("long_gamma", -0.30, G.FLOW_CONFLICT_LOB, "lob")
    assert heavy_dn["sign"] == "uncertain" and heavy_dn["agrees"] is False
    # a calm well-covered tape genuinely CONFIRMS (agrees True → pin vote stands)
    calm = G.reconcile_sign("long_gamma", 0.03, G.FLOW_CONFLICT_LOB, "lob")
    assert calm["sign"] == "long_gamma" and calm["agrees"] is True
    # and that same 0.30 tape would NOT trip the options-scaled 0.6 (proves the
    # two sources are on different scales and must not share a threshold)
    assert G.reconcile_sign("long_gamma", 0.30, G.FLOW_CONFLICT)["agrees"] is True


def test_operative_wall_placement_guards():
    # H2 v2: the operative wall is the FIRST candidate placed like a wall —
    # ≥ the fire floor away (the 0DTE gamma wall hugs spot: an ATM artifact,
    # not a barrier) and ≤ 3σ reach (the far crash strata never return via
    # the tenor fallback). None = honest absence.
    spot, sigma = 7550.0, 50.0
    # spot-hugging gamma wall (0.2σ) rejected → OI wall (1.0σ) wins
    assert R.operative_wall((7560.0, 7600.0, 8000.0), spot, sigma, "call") == 7600.0
    # everything near rejected, tenor in reach (2σ) wins
    assert R.operative_wall((7560.0, None, 7650.0), spot, sigma, "call") == 7650.0
    # tenor out of reach (9σ) → honest None, never the crash wall
    assert R.operative_wall((7560.0, None, 8000.0), spot, sigma, "call") is None
    # put side mirrors; an overshot (wrong-side) wall stops qualifying
    assert R.operative_wall((7545.0, 7500.0, 7000.0), spot, sigma, "put") == 7500.0
    assert R.operative_wall((7580.0,), spot, sigma, "put") is None   # wall above spot
    # degenerate σ → None
    assert R.operative_wall((7600.0,), spot, 0.0, "call") is None


def test_uncertain_gamma_cannot_carry_a_pin_verdict_alone():
    # with gamma abstaining, one lone pin vote (fear curve) is NOT enough
    v = R.classify_regime(R.gamma_vote_sign({"regime": "uncertain",
                                             "regime_raw": "long_gamma"}),
                          range_em=1.05, vr=None, vix_ts=0.90)
    assert v["regime"] != "pinning"          # 1 pin vs 1 trend → neutral


def test_pre_window_fire_does_not_suppress_first_rth_entry():
    # a trigger that lit up at 9:40 and stays lit must still ENTER at 9:47
    import datetime as _dt
    et = _dt.timezone(_dt.timedelta(hours=-4))
    def row(hh, mm, fired=True):
        return {"ticker": "SPX", "ts": _dt.datetime(2026, 7, 6, hh, mm, tzinfo=et).isoformat(),
                "spot": 7490, "sigma": 10,
                "reversion_extreme": {"fired": fired, "direction": "call"}}
    fires = R._fresh_would_fires([row(9, 40), row(9, 47), row(9, 52)], "SPX")
    assert len(fires) == 1                      # one idea, entered at 9:47
    assert fires[0][0].endswith("09:47:00-04:00")


def test_corrupt_ts_neither_enters_nor_consumes():
    import datetime as _dt
    et = _dt.timezone(_dt.timedelta(hours=-4))
    good = {"ticker": "SPX", "ts": _dt.datetime(2026, 7, 6, 10, 5, tzinfo=et).isoformat(),
            "spot": 7490, "sigma": 10,
            "reversion_extreme": {"fired": True, "direction": "call"}}
    bad = {"ticker": "SPX", "ts": "not-a-timestamp",
           "reversion_extreme": {"fired": True, "direction": "call"}}
    fires = R._fresh_would_fires([bad, good], "SPX")
    assert len(fires) == 1                      # the corrupt row didn't eat the entry


def test_runway_prefers_fill_confirmed_pin_over_flip():
    # COUPLING FIX: the runway's magnet witness is the fill-confirmed pin, so the
    # flip (which the gamma vote leans on) can no longer cast a second vote.
    rev = R.reversion_extreme(spot=7435.0, prior_close=7500.0, sigma=50.0,
                              call_wall=7520.0, put_wall=7430.0, regime="pinning",
                              last_bars=[{"open": 7433, "close": 7436}],
                              gamma_flip=7470.0, pin_magnet=7455.0)
    assert rev["magnet"] == 7455.0                      # pin wins over flip
    assert abs(rev["runway_sigma"] - 0.4) < 1e-9        # (7455-7435)/50
    assert rev["runway_ok"] and rev["fired"]


def test_runway_falls_back_to_flip_then_wall():
    # no pin (e.g. a no-0DTE day) → flip; no flip either → the opposing wall
    rev = R.reversion_extreme(spot=7435.0, prior_close=7500.0, sigma=50.0,
                              call_wall=7520.0, put_wall=7430.0, regime="pinning",
                              last_bars=[], gamma_flip=7470.0, pin_magnet=None)
    assert rev["magnet"] == 7470.0
    rev = R.reversion_extreme(spot=7435.0, prior_close=7500.0, sigma=50.0,
                              call_wall=7520.0, put_wall=7430.0, regime="pinning",
                              last_bars=[], gamma_flip=None, pin_magnet=None)
    assert rev["magnet"] == 7520.0


def test_live_allowed_fails_closed(monkeypatch):
    # shadow switch off → paper, whatever the record says
    ok, why = R.live_allowed()
    assert ok is False and "shadow" in why
    # hand switch flipped but the record is thin → STILL paper (H4: the flip
    # alone can no longer arm anything)
    monkeypatch.setattr(R, "REVERSION_LIVE", True)
    ok, why = R.live_allowed()
    assert ok is False


# --- the outcome vector (2026-07-05): r + full-window MFE/MAE + move marks ----
def _vbars(seq):
    """bars with closes: (HH:MM, hi, lo, close)"""
    bars = [{"ts": f"2026-06-23T{hm}:00-04:00", "high": h, "low": l, "close": c}
            for (hm, h, l, c) in seq]
    return lambda tk: bars if tk == "SPX" else []


def test_outcome_vector_win_keeps_walking_past_the_exit():
    # entry 7350 σ95 LONG → target 7378.5 hit at 10:20 (r=+1), but the walk
    # continues: the 11:00 bar runs to 7500 → full-window MFE must see the
    # $150-style runner the exit clipped; the 10:05 dip to 7348 is the MAE.
    rows = [_wf("2026-06-23T10:00:00-04:00", 7350)]
    bars = _vbars([("10:05", 7360, 7348, 7355),
                   ("10:20", 7400, 7362, 7390),
                   ("11:00", 7500, 7390, 7480)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    t = resolved[0]
    assert t["outcome"] == "win" and t["r"] == 1.0 and t["exit_sigma"] == 0.3
    assert t["mfe_sigma"] == round((7500 - 7350) / 95, 3)     # full window, not exit-clipped
    assert t["mae_sigma"] == round((7348 - 7350) / 95, 3)     # signed ≤ 0
    assert t["move_30m"] == round((7390 - 7350) / 95, 3)      # last close ≤ 30 min
    assert t["move_60m"] == round((7480 - 7350) / 95, 3)
    assert t["move_120m"] is None                             # window never got there
    assert t["hold_min"] == 20                                # exit time, not walk end


def test_outcome_vector_loss_is_minus_one_r():
    rows = [_wf("2026-06-23T10:00:00-04:00", 7350)]
    bars = _vbars([("10:10", 7355, 7310, 7315)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    t = resolved[0]
    assert t["outcome"] == "loss" and t["r"] == -1.0 and t["exit_sigma"] == -0.3


def test_outcome_vector_scratch_gets_fractional_r():
    # never touches a line; exits at the last in-window close 7360 → r = +0.35
    rows = [_wf("2026-06-23T15:00:00-04:00", 7350)]
    bars = _vbars([("15:10", 7362, 7345, 7352), ("15:30", 7362, 7344, 7358),
                   ("15:55", 7361, 7346, 7360)])
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    t = resolved[0]
    assert t["outcome"] == "scratch"
    assert t["exit_sigma"] == round((7360 - 7350) / 95, 3)
    assert t["r"] == round(t["exit_sigma"] / 0.30, 2)
    assert t["hold_min"] == 55


def test_resolver_grades_the_watchtower_via_signal_key():
    # Head B's fires live under "watchtower" but grade on the same rules
    rows = [{"ticker": "SPX", "ts": "2026-06-23T10:00:00-04:00", "spot": 7350,
             "sigma": 95, "reversion_extreme": {"fired": False, "direction": None},
             "watchtower": {"fired": True, "direction": "call"}}]
    bars = _vbars([("10:20", 7400, 7362, 7390)])
    none_resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars)
    assert none_resolved == []                        # the gates never fired
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars,
                                         signal_key="watchtower")
    assert resolved and resolved[0]["outcome"] == "win" and resolved[0]["r"] == 1.0


# --- BREAK LENS (offense, 2026-07-05): the two-stage cock→fire machine -------
# State is derived FRESH each call from today's diary rows (no state file);
# these tests hand-build the rows the way record() would persist them.
_ET = ZoneInfo("America/New_York")


def _bnow(hh, mm):
    return datetime.datetime(2026, 7, 6, hh, mm, tzinfo=_ET)


def _brow(hh, mm, lr):
    """A diary row carrying break fields under the level_reclaim key."""
    return {"ticker": "SPX", "ts": _bnow(hh, mm).isoformat(), "level_reclaim": lr}


GOOD_TAPE_DOWN = {"tilt": -0.8, "determinate_share": 0.9,
                  "purge_flag": False, "calendar_blocked": False}


def _bstate(prior_rows, **kw):
    """break_lens_state with a default STORM-SIDE fade-long-rejection scene:
    gamma negative, regime trending, fade lens armed LONG (so the continuation
    cock is a PUT), one-way bearish tape, walls at 7450/7370."""
    args = dict(now=_bnow(10, 0), spot=7400.0, sigma=50.0,
                gamma_sign="negative", regime="trending",
                rev={"armed": True, "direction": "call"},
                lvl={"fired": False, "level": None, "level_kind": None},
                lob_flow=GOOD_TAPE_DOWN,
                shelf_up=(7450.0, "call_wall_gamma"),
                shelf_dn=(7370.0, "put_wall_gamma"),
                last_bars=[])
    args.update(kw)
    return R.break_lens_state(prior_rows, **args)


def test_break_cocks_on_storm_side_rejection():
    bl = _bstate([])
    assert bl["break_state"] == "cocked"
    assert bl["cock_direction"] == "put"          # continuation of a rejected fade-long
    assert bl["cock_level"] == 7370.0 and bl["cock_level_kind"] == "put_wall_gamma"
    assert bl["cocked_at"] is not None and bl["cock_count_today"] == 1
    # NEVER a fire (either twin) on the cocking scan
    assert bl["fired"] is False and bl["fired_pre_gates"] is False
    assert bl["fire_gates"]["storm_side"] is True  # snapshot rides while cocked


def test_break_does_not_cock_on_calm_side_or_unarmed():
    assert _bstate([], gamma_sign="positive")["break_state"] == "idle"
    assert _bstate([], gamma_sign="unknown")["break_state"] == "idle"
    assert _bstate([], rev={"armed": False, "direction": "none"})["break_state"] == "idle"


def test_break_anchor_prefers_reclaim_level_then_arming_wall():
    bl = _bstate([], lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    assert bl["cock_level"] == 7400.0 and bl["cock_level_kind"] == "round"
    # a fade-SHORT arm → up-continuation cock anchored at the call-side wall
    bl = _bstate([], rev={"armed": True, "direction": "put"})
    assert bl["cock_direction"] == "call" and bl["cock_level"] == 7450.0
    # no anchor resolvable anywhere → no cock
    bl = _bstate([], shelf_up=None, shelf_dn=None)
    assert bl["break_state"] == "idle" and bl["cocked_at"] is None


def test_break_fires_only_through_all_four_gates_then_is_spent():
    cock = _bstate([], now=_bnow(10, 0), shelf_dn=(7350.0, "put_wall_gamma"),
                   lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    row1 = _brow(10, 0, cock)
    # 10:10 — bar decisively closes below the 7400 cock level; trending +
    # storm-side + one-way bearish tape + 0.7σ of runway to the 7350 shelf
    fire = _bstate([row1], now=_bnow(10, 10), spot=7385.0,
                   shelf_dn=(7350.0, "put_wall_gamma"),
                   last_bars=[{"open": 7392, "close": 7385, "volume": 100}])
    assert fire["break_state"] == "fired" and fire["fired"] and fire["fired_pre_gates"]
    assert fire["direction"] == "put"             # trigger scans carry cock_direction
    g = fire["fire_gates"]
    assert (g["trending"] and g["storm_side"] and g["tape_ok"]
            and g["runway_ok"] and g["base_trigger"])
    assert g["tape_one_way"] == -0.72 and g["shelf"] == 7350.0
    assert g["runway_sigma"] == 0.7
    # ONE SHOT: the fire consumed the cock — the next scan derives idle
    row2 = _brow(10, 10, fire)
    after = _bstate([row1, row2], now=_bnow(10, 15), spot=7385.0,
                    rev={"armed": False, "direction": "none"},
                    last_bars=[{"open": 7392, "close": 7385}])
    assert after["fired"] is False and after["break_state"] == "idle"


def test_break_holds_through_neutral_and_pre_gates_is_one_shot():
    cock = _bstate([], now=_bnow(10, 0),
                   lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    row1 = _brow(10, 0, cock)
    bars = [{"open": 7392, "close": 7385, "volume": 100}]
    hold = _bstate([row1], now=_bnow(10, 10), spot=7385.0, regime="neutral",
                   last_bars=bars)
    assert hold["break_state"] == "cocked"        # HOLD — neutral never decocks
    assert hold["fired"] is False                 # ...and never fires the gated twin
    assert hold["fired_pre_gates"] is True        # the ungated twin spends its shot
    assert hold["cocked_at"] == cock["cocked_at"]  # identity carried forward
    assert hold["cock_age_min"] == 10.0
    row2 = _brow(10, 10, hold)
    again = _bstate([row1, row2], now=_bnow(10, 15), spot=7385.0, regime="neutral",
                    last_bars=bars)
    assert again["break_state"] == "cocked" and again["fired_pre_gates"] is False


def test_break_tape_gate_fails_closed():
    # THE ONLY FAIL-CLOSED RULE: missing/stale/vetoed tape → tape_ok False → NO FIRE
    cock = _bstate([], now=_bnow(10, 0), shelf_dn=(7350.0, "put_wall_gamma"),
                   lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    row1 = _brow(10, 0, cock)
    kw = dict(now=_bnow(10, 10), spot=7385.0, shelf_dn=(7350.0, "put_wall_gamma"),
              last_bars=[{"open": 7392, "close": 7385, "volume": 100}])
    for bad_tape in (None,                                        # fold absent/stale
                     {"tilt": None, "determinate_share": 0.9},    # no tilt
                     {"tilt": -0.8, "determinate_share": None},   # no share
                     {**GOOD_TAPE_DOWN, "purge_flag": True},      # purge veto
                     {**GOOD_TAPE_DOWN, "calendar_blocked": True},
                     {"tilt": 0.8, "determinate_share": 0.9},     # bullish on a PUT cock
                     {"tilt": -0.1, "determinate_share": 0.9}):   # |−0.09| < BREAK_TAPE_MIN
        st = _bstate([row1], lob_flow=bad_tape, **kw)
        assert st["fired"] is False and st["break_state"] == "cocked"
        assert st["fire_gates"]["tape_ok"] is False
        assert st["fired_pre_gates"] is True      # the UNGATED twin still records


def test_break_decocks_when_calm_side_reclaimed():
    cock = _bstate([], now=_bnow(10, 0))
    row1 = _brow(10, 0, cock)
    st = _bstate([row1], now=_bnow(10, 5), gamma_sign="positive",
                 last_bars=[{"open": 7368, "close": 7360}])   # would-be trigger bar
    assert st["break_state"] == "decocked" and st["fired"] is False
    assert st["fired_pre_gates"] is False         # a dead cock cannot trigger
    assert st["fire_gates"] is None               # snapshot only while cocked/fired


def test_break_expires_after_ttl():
    cock = _bstate([], now=_bnow(10, 0))
    row1 = _brow(10, 0, cock)
    st = _bstate([row1], now=_bnow(10, 50))       # 50 min > BREAK_COCK_EXPIRE_MIN
    assert st["break_state"] == "expired" and st["cock_age_min"] == 50.0
    assert st["fired"] is False and st["fire_gates"] is None


def test_break_gamma_unknown_holds_no_decock_no_gated_fire():
    cock = _bstate([], now=_bnow(10, 0), shelf_dn=(7350.0, "put_wall_gamma"),
                   lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    row1 = _brow(10, 0, cock)
    st = _bstate([row1], now=_bnow(10, 10), spot=7385.0, gamma_sign="unknown",
                 shelf_dn=(7350.0, "put_wall_gamma"),
                 last_bars=[{"open": 7392, "close": 7385}])
    assert st["break_state"] == "cocked"          # unknown = HOLD, not decock
    assert st["fired"] is False                   # storm-side gate needs 'negative'
    assert st["fired_pre_gates"] is True
    assert st["fire_gates"]["storm_side"] is False


def test_break_max_two_cocks_per_level_bucket_per_day():
    # two spent cocks in the same 5-pt bucket already on the diary
    r1 = _brow(9, 50, {"break_state": "expired", "cock_level": 7370.0,
                       "cocked_at": "2026-07-06T09:50:00-04:00"})
    r2 = _brow(10, 40, {"break_state": "expired", "cock_level": 7371.0,   # same bucket
                        "cocked_at": "2026-07-06T10:40:00-04:00"})
    st = _bstate([r1, r2], now=_bnow(11, 0))
    assert st["break_state"] == "idle" and st["cock_count_today"] == 2
    # a DIFFERENT level still cocks — the cap is per level, not per day
    st = _bstate([r1, r2], now=_bnow(11, 0),
                 lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    assert st["break_state"] == "cocked" and st["cock_count_today"] == 1


def test_break_runway_gate_blocks_a_fire_into_a_close_shelf():
    cock = _bstate([], now=_bnow(10, 0),
                   lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    row1 = _brow(10, 0, cock)
    bars = [{"open": 7392, "close": 7385, "volume": 100}]
    st = _bstate([row1], now=_bnow(10, 10), spot=7385.0,
                 shelf_dn=(7380.0, "put_wall_gamma"),      # 0.1σ away — no room
                 last_bars=bars)
    assert st["fired"] is False and st["break_state"] == "cocked"
    assert st["fire_gates"]["runway_ok"] is False
    assert st["fire_gates"]["runway_sigma"] == 0.1
    # a MISSING shelf also fails the runway gate (never a free pass)
    st = _bstate([row1], now=_bnow(10, 10), spot=7385.0, shelf_dn=None,
                 last_bars=bars)
    assert st["fire_gates"]["runway_ok"] is False and st["fire_gates"]["shelf"] is None


def test_break_keeps_the_legacy_vol_pop_verdict():
    # `fired` is REDEFINED as the gated Break fire; the old 1.3× volume-pop
    # detector's verdict survives under vol_pop_fired for continuity.
    bl = _bstate([], lvl={"fired": True, "level": 7400.0, "level_kind": "round"})
    assert bl["vol_pop_fired"] is True and bl["fired"] is False


def test_break_late_day_tailwind_is_recorded_not_gating():
    # after ~15:15 ET the pin's grip is drained (a break TAILWIND) — recorded
    # in the gates snapshot only; it must not fire anything by itself
    late = _bstate([], now=_bnow(15, 20))
    assert late["fire_gates"]["late_day_tailwind"] is True
    assert late["fired"] is False
    early = _bstate([], now=_bnow(10, 0))
    assert early["fire_gates"]["late_day_tailwind"] is False


def test_vanna_afterburner_ships_inactive():
    # INACTIVE HOOK: must stay 1.0 until the polarity cards settle VEX signs
    assert R.VANNA_AFTERBURNER_DOWN == 1.0


def test_resolver_grades_break_fires_via_level_reclaim_key():
    # the nightly break_lens card reads the same resolver with
    # signal_key="level_reclaim" — a one-shot gated fire grades like any trade
    rows = [{"ticker": "SPX", "ts": "2026-06-23T10:00:00-04:00", "spot": 7350,
             "sigma": 95, "reversion_extreme": {"fired": False, "direction": None},
             "level_reclaim": {"fired": True, "fired_pre_gates": True,
                               "direction": "put", "break_state": "fired"}}]
    bars = _vbars([("10:20", 7340, 7300, 7310)])   # put target 7321.5 touched
    resolved, _ = R._resolve_beta_trades(rows, bars_lookup=bars,
                                         signal_key="level_reclaim")
    assert resolved and resolved[0]["outcome"] == "win" and resolved[0]["dir"] == "put"


def test_break_carry_forward_skips_break_field_less_rows():
    # AMNESIA GUARD: a scan whose break block fail-opened writes the LEGACY
    # level_reclaim dict (no break fields). It must be TRANSPARENT to the state
    # machine — reading it as "idle" would re-cock the same rejection scene
    # with a fresh cocked_at (reset TTL, an extra cap slot, and a duplicate
    # fired_pre_gates one-shot for a single physical episode).
    cock = _bstate([], now=_bnow(10, 0))
    row1 = _brow(10, 0, cock)
    legacy = _brow(10, 5, {"score": 0.0, "confidence": 0.0, "fired": False,
                           "direction": "none", "level": None,
                           "level_kind": None, "flips_regime": False})
    later = _bstate([row1, legacy], now=_bnow(10, 10))
    assert later["break_state"] == "cocked"
    assert later["cocked_at"] == cock["cocked_at"]   # the ORIGINAL cock survives
    assert later["cock_count_today"] == 1            # no phantom second cock


def test_break_tape_gate_fails_closed_on_malformed_fold():
    # a malformed fold value (the sensor's first live day) must fail CLOSED —
    # tape_ok False with everything else intact — never raise into the outer
    # fail-open and take the whole break block down with it
    cock = _bstate([], now=_bnow(10, 0),
                   lvl={"fired": False, "level": 7400.0, "level_kind": "round"})
    row1 = _brow(10, 0, cock)
    bars = [{"open": 7392, "close": 7385, "volume": 100}]
    bad = dict(GOOD_TAPE_DOWN, tilt="hot garbage")
    st = _bstate([row1], now=_bnow(10, 10), spot=7385.0, lob_flow=bad,
                 shelf_dn=(7350.0, "put_wall_gamma"), last_bars=bars)
    g = st["fire_gates"]
    assert g["tape_one_way"] is None and g["tape_ok"] is False
    assert st["fired"] is False and st["break_state"] == "cocked"   # HOLD, not dead
    assert st["fired_pre_gates"] is True             # the ungated twin is unaffected
    # a NUMERIC STRING is coerced, not vetoed (defensive, not paranoid)
    coerced = dict(GOOD_TAPE_DOWN, tilt="-0.8")
    st2 = _bstate([row1], now=_bnow(10, 10), spot=7385.0, lob_flow=coerced,
                  shelf_dn=(7350.0, "put_wall_gamma"), last_bars=bars)
    assert st2["fire_gates"]["tape_one_way"] == -0.72
    assert st2["fire_gates"]["tape_ok"] is True


def test_break_runway_signed_twin_records_overshoot():
    # the |·| runway gate reads overshoot-behind as room-ahead when the shelf
    # IS the broken wall — the SIGNED twin keeps the true geometry auditable
    # in the diary (runway_ok itself is locked, unchanged)
    cock = _bstate([], now=_bnow(10, 0))             # put cock anchored at 7370
    row1 = _brow(10, 0, cock)
    st = _bstate([row1], now=_bnow(10, 10), spot=7350.0,   # 0.4σ PAST the shelf
                 last_bars=[{"open": 7360, "close": 7350, "volume": 100}])
    g = st["fire_gates"]
    assert g["runway_sigma"] == 0.4 and g["runway_ok"] is True
    assert g["runway_signed_sigma"] == -0.4          # negative = shelf BEHIND spot
    # shelf genuinely ahead in the break direction → the twins agree
    st2 = _bstate([row1], now=_bnow(10, 10), spot=7400.0,
                  shelf_dn=(7350.0, "put_wall_gamma"), last_bars=[])
    g2 = st2["fire_gates"]
    assert g2["runway_sigma"] == 1.0 and g2["runway_signed_sigma"] == 1.0


def test_read_today_telemetry_skips_torn_line(tmp_path, monkeypatch):
    # a crash/disk-full mid-append leaves one torn line — every row AFTER it
    # must still reach the state machine (a frozen memory re-derives the same
    # cock all day: duplicate one-shots, wedged TTL)
    import json
    monkeypatch.setattr(R, "SKILL_DIR", tmp_path / "skills" / "x")
    d = tmp_path / "state" / "reversion"
    d.mkdir(parents=True)
    good1 = json.dumps({"ticker": "SPX", "ts": NOW.isoformat()})
    good2 = json.dumps({"ticker": "SPX", "ts": NOW.isoformat(), "spot": 7400.0})
    (d / f"{NOW.date().isoformat()}.jsonl").write_text(
        good1 + "\n" + '{"torn": tru' + "\n" + good2 + "\n")
    rows = R._read_today_telemetry(NOW)
    assert len(rows) == 2 and rows[1]["spot"] == 7400.0
