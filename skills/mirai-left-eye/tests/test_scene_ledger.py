"""N20 · SCENE LEDGER — Head B's second scoreboard (gex_polarity_ab._grade_watchtower_scene):
Watchtower calls graded as scene-scoped EPISODES beside the Trade Ledger."""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gex_polarity_ab as pab
import watchtower

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 1, 10, 0, tzinfo=ET)


def _row(minute, direction=None, magnitude=0.5, judged=False,
         gamma_sign="positive", regime="pinning", road=None,
         magnet=7500.0, flow=None, vix_ts=0.85, spot=7490.0, sigma=10.0):
    """A diary row carrying the scene axes; direction set = a judged issuance."""
    row = {"ticker": "SPX", "ts": (T0 + timedelta(minutes=minute)).isoformat(),
           "spot": spot, "sigma": sigma,
           "gamma_sign": gamma_sign, "regime": regime, "regime_road": road,
           "vix_ts": vix_ts,
           "gex_views": {"magnet": magnet, "aggressor_flow": flow}}
    if judged or direction:
        row["watchtower"] = {"fired": direction is not None, "direction": direction,
                             "magnitude_sigma": magnitude, "prompt_version": "wt-6"}
    return row


def _bars(*specs):
    # specs: (minutes_after_t0, high, low, close)
    return [{"ts": (T0 + timedelta(minutes=m)).isoformat(),
             "high": hi, "low": lo, "close": cl} for (m, hi, lo, cl) in specs]


def _grade(rows, bars):
    return pab._grade_watchtower_scene(rows, lambda t: bars if t == "SPX" else [])


# quiet flat bars every 10 min through the close (mfe 0.04σ — never moved)
_QUIET = _bars(*[(m, 7490.4, 7489.6, 7490.0) for m in range(1, 360, 10)])


def test_same_direction_calls_collapse_into_one_episode():
    rows = [_row(0, "call"), _row(5, "call"), _row(10, "call")]
    ws = _grade(rows, _QUIET)
    assert len(ws["episodes"]) == 1
    ep = ws["episodes"][0]
    assert ep["dir"] == "call" and ep["t_open"] == rows[0]["ts"]
    assert ep["close_reason"] == "session_close"


def test_target_touch_closes_as_win_with_time_to_target():
    # spot 7490, called 0.5σ → target 7495; touched at :03
    rows = [_row(0, "call")]
    ws = _grade(rows, _bars((3, 7495.2, 7491.0, 7494.0)))
    ep = ws["episodes"][0]
    assert ep["outcome"] == "win" and ep["close_reason"] == "target"
    assert ep["tt_target_min"] == 3 and ep["kept_sigma"] == 0.5
    assert ws["wins"] == 1 and ws["n"] == 1 and ws["hit_rate"] == 1.0


def test_adverse_touch_closes_as_loss():
    # adverse mark 7485 for a 0.5σ call
    rows = [_row(0, "call")]
    ws = _grade(rows, _bars((4, 7491.0, 7484.8, 7485.5)))
    ep = ws["episodes"][0]
    assert ep["outcome"] == "loss" and ep["close_reason"] == "adverse"
    assert ep["kept_sigma"] == -0.5 and ep["tt_target_min"] is None


def test_ambiguous_bar_touching_both_lines_is_a_loss():
    rows = [_row(0, "call")]
    ws = _grade(rows, _bars((4, 7495.5, 7484.5, 7490.0)))
    assert ws["episodes"][0]["outcome"] == "loss"
    assert ws["episodes"][0]["close_reason"] == "adverse"


def test_scene_change_close_grades_the_kept_move_hold_scaled():
    # gamma sign flips at :30; price kept +0.20σ ≥ the hold-scaled bar
    # (0.30·√(30/390) ≈ 0.083σ) → win via kept move, no tt_target
    rows = [_row(0, "call"), _row(30, gamma_sign="negative")]
    ws = _grade(rows, _bars((5, 7492.2, 7489.8, 7492.0),
                            (25, 7492.2, 7491.8, 7492.0)))
    ep = ws["episodes"][0]
    assert ep["close_reason"] == "scene_change" and ep["scene_trigger"] == "gamma_flip"
    assert ep["outcome"] == "win" and ep["tt_target_min"] is None
    assert abs(ep["kept_sigma"] - 0.2) < 1e-9
    assert ep["t_close"] == rows[1]["ts"]


def test_small_favorable_kept_move_grades_flat():
    # regime flips at :300 (hit bar 0.30·√(300/390) ≈ 0.263σ); mfe 0.15σ clears
    # the never-moved floor but kept 0.12σ misses the bar → FLAT
    rows = [_row(0, "call", magnitude=None), _row(300, regime="trending")]
    ws = _grade(rows, _bars((60, 7491.5, 7489.8, 7491.0),
                            (290, 7491.3, 7490.8, 7491.2)))
    ep = ws["episodes"][0]
    assert ep["outcome"] == "flat" and ep["scene_trigger"] == "regime_flip"
    assert ws["flats"] == 1 and ws["n"] == 0 and ws["hit_rate"] is None


def test_never_moved_episode_is_a_loss_not_a_flat():
    # vix_ts lurches at :30; mfe 0.09σ < the 0.10σ floor → LOSS even though the
    # kept move (+0.05σ) would otherwise read flat
    rows = [_row(0, "call"), _row(30, vix_ts=0.70)]
    ws = _grade(rows, _bars((20, 7490.9, 7489.8, 7490.5)))
    ep = ws["episodes"][0]
    assert ep["outcome"] == "loss" and ep["scene_trigger"] == "vix_ts"
    assert ep["mfe_sigma"] == 0.09


def test_direction_flip_closes_and_opens_the_next_episode():
    rows = [_row(0, "call"), _row(40, "put")]
    ws = _grade(rows, _QUIET)
    assert len(ws["episodes"]) == 2
    first, second = ws["episodes"]
    assert first["close_reason"] == "dir_flip" and first["t_close"] == rows[1]["ts"]
    assert second["dir"] == "put" and second["t_open"] == rows[1]["ts"]


def test_rearm_after_target_same_direction_opens_fresh_episode():
    # ep1 wins at :03; the SAME-direction call at :20 must open a new episode
    rows = [_row(0, "call"), _row(20, "call")]
    bars = _bars((3, 7495.2, 7491.0, 7494.0),
                 *[(m, 7490.4, 7489.6, 7490.0) for m in range(25, 360, 10)])
    ws = _grade(rows, bars)
    assert len(ws["episodes"]) == 2
    assert ws["episodes"][0]["outcome"] == "win"
    assert ws["episodes"][1]["t_open"] == rows[1]["ts"]


def test_fallback_called_sigma_when_tower_stated_none():
    rows = [_row(0, "call", magnitude=None)]
    ws = _grade(rows, _bars((3, 7493.1, 7491.0, 7493.0)))   # 0.30σ target = 7493
    ep = ws["episodes"][0]
    assert ep["called_fallback"] is True and ep["called_sigma"] == 0.3
    assert ep["outcome"] == "win"
    assert ws["params"]["fallback_target_sigma"] == 0.3


def test_magnet_jump_trigger_uses_the_watchtower_dial():
    # 6 pts on σ=10 → 0.6σ ≥ MAGNET_JUMP_SIGMA (0.5)
    rows = [_row(0, "call"), _row(30, magnet=7506.0)]
    ws = _grade(rows, _QUIET[:4])
    assert ws["episodes"][0]["scene_trigger"] == "magnet"
    assert ws["params"]["magnet_jump_sigma"] == watchtower.MAGNET_JUMP_SIGMA
    # sub-dial drift must NOT close the scene
    rows = [_row(0, "call"), _row(30, magnet=7504.0)]
    ws = _grade(rows, _QUIET)
    assert ws["episodes"][0]["close_reason"] == "session_close"


def test_flow_flip_trigger_needs_a_zero_cross_with_force():
    rows = [_row(0, "call", flow=0.5), _row(30, flow=-0.45)]
    ws = _grade(rows, _QUIET[:4])
    assert ws["episodes"][0]["scene_trigger"] == "flow"
    # a weak cross (|flow| < FLOW_FLIP_MIN) is not a scene change
    rows = [_row(0, "call", flow=0.5), _row(30, flow=-0.2)]
    ws = _grade(rows, _QUIET)
    assert ws["episodes"][0]["close_reason"] == "session_close"


def test_road_opening_is_a_scene_change():
    rows = [_row(0, "call"),
            _row(30, road={"kind": "breach", "side": "put", "level": 7480.0})]
    ws = _grade(rows, _QUIET[:4])
    assert ws["episodes"][0]["scene_trigger"] == "road"
    assert ws["episodes"][0]["close_reason"] == "scene_change"


def test_out_of_window_calls_counted_never_silently_lost():
    early = _row(0, "call")
    early["ts"] = T0.replace(hour=9, minute=40).isoformat()   # pre-window
    late = _row(0, "call")
    late["ts"] = T0.replace(hour=16, minute=5).isoformat()    # post-close
    ws = _grade([early, late, _row(0, "call")], _QUIET)
    assert ws["n_dropped_early"] == 2
    assert len(ws["episodes"]) == 1


def test_incomplete_bars_leave_the_episode_pending():
    # session close at 16:00 but bars stop at :60 → day still running
    rows = [_row(0, "call")]
    ws = _grade(rows, _bars((60, 7490.4, 7489.6, 7490.0)))
    ep = ws["episodes"][0]
    assert ep["outcome"] == "pending"
    assert ep["t_close"] is None and ep["close_reason"] is None
    assert ws["pending"] == 1 and ws["n"] == 0


def test_empty_day_yields_a_well_formed_zero_card():
    ws = pab._grade_watchtower_scene([], lambda t: [])
    assert ws["scene_grade_v"] == 1 and ws["n"] == 0
    assert ws["wins"] == ws["losses"] == ws["flats"] == ws["pending"] == 0
    assert ws["hit_rate"] is None and ws["wilson_lo"] is None
    assert ws["median_tt_target_min"] is None and ws["sigma_per_hour"] is None
    assert ws["episodes"] == [] and ws["n_dropped_early"] == 0
    assert ws["params"]["never_moved_floor_sigma"] == 0.1
    # and grade_day carries it as a sibling of the Trade Ledger card
    res = pab.grade_day("2026-07-01", rows=[], bars_lookup=lambda t: [])
    assert res["watchtower_scene"]["n"] == 0


def test_rollup_math_excludes_flats_and_medians_the_hits():
    rows = [_row(0, "call"),            # ep1: target at :02 (tt 2)
            _row(10, "call"),           # ep2: target at :16 (tt 6)
            _row(30, "call"),           # ep3: adverse at :34
            _row(60, "call"),           # ep4: flat at the :90 scene change
            _row(90, gamma_sign="negative")]
    bars = _bars((2, 7495.1, 7491.0, 7494.0),
                 (16, 7495.2, 7491.0, 7494.0),
                 (34, 7490.5, 7484.8, 7485.5),
                 (80, 7491.2, 7489.9, 7490.2))
    ws = _grade(rows, bars)
    assert [e["outcome"] for e in ws["episodes"]] == ["win", "win", "loss", "flat"]
    assert ws["n"] == 3 and ws["wins"] == 2 and ws["losses"] == 1 and ws["flats"] == 1
    assert ws["hit_rate"] == 0.67                       # flats out of the denominator
    lo, hi = pab.wilson_bounds(2, 1)
    assert ws["wilson_lo"] == round(lo, 3) and ws["wilson_hi"] == round(hi, 3)
    assert ws["median_tt_target_min"] == 4              # median of 2 and 6
    # σ/hour over HIT episodes: median of 0.5/(2/60)=15 and 0.5/(6/60)=5
    assert ws["sigma_per_hour"] == 10.0
    assert ws["called_vs_realized"] is not None and ws["median_kept_sigma"] is not None
    assert ws["prompt_version"] == "wt-6"


def test_grade_day_attaches_the_scene_ledger_beside_the_trade_ledger():
    rows = [_row(0, "call"), _row(40, "put")]
    bars = [{"ts": (T0 + timedelta(minutes=m)).isoformat(),
             "high": 7490.4, "low": 7489.6, "close": 7490.0} for m in range(1, 360, 10)]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    assert "watchtower" in res                          # Trade Ledger untouched
    assert len(res["watchtower_scene"]["episodes"]) == 2
