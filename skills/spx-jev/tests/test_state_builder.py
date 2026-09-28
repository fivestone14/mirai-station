"""The labeller: point in time, omit never null, every cut in the words, the SPX sources read alike."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import (DAY, SECTORS, at, bars_from_closes, context_line, flat_bars, make_row, measured, options_tape_at,
                      prior_sessions, write_state)
from spx_jev.cuts import (IV_FLAT_BAND_PTS, MOVE_RULE_SIGMA, MOVE_STRONG_SIGMA, TAPE_BIG_UNITS, TAPE_FLAT_UNITS, WALL_NEAR_SIGMA,
                           WALL_TOUCH_SIEGE_PERCENTILE)
from spx_jev.labels.registry import build_labels
from spx_jev.labels.rulers import ruler
from spx_jev.state_builder import MarketContext, load_bars, load_market_context, load_options_tape, make_scene, prior_bar_days

SIGMA = 75.0


def _labels(scene):
    labels = build_labels(scene)
    return labels.state, measured(labels.omitted)


def test_the_30_minute_move_is_judged_against_the_move_rule(scene_factory):
    now = at(11, 0, ss=5)
    up = MOVE_RULE_SIGMA * SIGMA * 1.5
    state, _ = _labels(scene_factory(now, bars_from_closes([7700.0] * 60 + [7700.0 + up] * 30)))
    assert state["price"]["recent_move"].startswith(f"over the last 30 minutes price rose {up / SIGMA:.2f} sigma, more than the {MOVE_RULE_SIGMA} sigma move rule")
    state, _ = _labels(scene_factory(now, flat_bars(90)))
    assert state["price"]["recent_move"].startswith(f"over the last 30 minutes price stayed within {MOVE_RULE_SIGMA} sigma")
    # the quiet read is a fact the momentum questions answer "no move" to, not a gap
    assert "no move to judge" in state["momentum"]["closes"] and "no move to judge" in state["momentum"]["path_efficiency"]


def test_before_30_minutes_the_move_is_omitted_with_the_reason(scene_factory):
    state, omitted = _labels(scene_factory(at(9, 50), flat_bars(20)))
    assert "recent_move" not in state.get("price", {}) and omitted["price.recent_move"] == "needs 30 minutes of session"
    assert state["range"]["box_status"] == "the opening box is still forming, 20 minutes of its 30 are in"


def test_only_finished_bars_count(scene_factory):
    """A read at 10:00:30 sees the 09:59 bar (it finished at 10:00) and never the running 10:00 bar."""
    bars = flat_bars(30) + bars_from_closes([7800.0], day=DAY)
    bars[-1]["ts"] = at(10, 0).isoformat()
    scene = scene_factory(at(10, 0, ss=30), bars, spot=7700.0)
    assert len(scene.bars) == 30 and all(b["close"] == 7700.0 for b in scene.bars)


def test_the_figures_behind_the_situation_labels(full_scene):
    figures = build_labels(full_scene).figures
    assert set(figures) == {"price.recent_move", "price.vs_vwap", "gex.air_to_wall", "iv.trend_30min"}
    assert figures["price.recent_move"] == {"kind": "signed", "value": 0.4, "band": MOVE_RULE_SIGMA, "strong": MOVE_STRONG_SIGMA, "unit": "sigma",
                                            "verdict": "rising"}
    assert figures["gex.air_to_wall"]["near"] == WALL_NEAR_SIGMA and figures["gex.air_to_wall"]["verdict"] == "heavy_strike_close"
    assert figures["iv.trend_30min"]["band"] == IV_FLAT_BAND_PTS and figures["iv.trend_30min"]["unit"] == "vol points"


def test_the_iv_trend_is_not_described_in_the_last_hour_of_the_0dte_book(scene_factory):
    now = at(15, 10, ss=5)
    earlier = make_row(now - timedelta(minutes=30), 7700.0, atm_iv=0.10)
    state, omitted = _labels(scene_factory(now, flat_bars(340), rows_before=[earlier]))
    assert "trend_30min" not in state["iv"] and "expiry clock" in omitted["iv.trend_30min"]
    state, _ = _labels(scene_factory(at(14, 50, ss=5), flat_bars(320), rows_before=[make_row(at(14, 20), 7700.0, atm_iv=0.10)]))
    assert state["iv"]["trend_30min"].startswith("over the last 30 minutes at-the-money implied volatility rose 5.50 vol points")


def test_the_vix_curve_is_calm_under_one_and_stressed_at_or_above(scene_factory):
    now = at(11, 0)
    calm, _ = _labels(scene_factory(now, flat_bars(90), row_over={"vix_ts": 0.83}))
    stressed, _ = _labels(scene_factory(now, flat_bars(90), row_over={"vix_ts": 1.05}))
    assert calm["iv"]["term_structure"].endswith("the calm shape") and stressed["iv"]["term_structure"].endswith("the stressed shape")
    _, omitted = _labels(scene_factory(now, flat_bars(90), row_over={"vix_ts": None}))
    assert omitted["iv.term_structure"] == "row carries no VIX against three-month VIX"


def test_no_wall_on_the_row_is_a_fact_not_a_gap(scene_factory):
    state, omitted = _labels(scene_factory(at(11, 0), flat_bars(90), row_over={"call_wall": None, "put_wall": None}))
    assert state["gex"]["air_to_wall"] == "no heavy strike sits within reach on either side of price"
    assert omitted["gex.wall_thickness"] == "no nearest wall with a gamma share on the row"


def test_breadth_reads_the_market_context_point_in_time(full_scene, scene_factory):
    state, _ = _labels(full_scene)
    assert state["breadth"]["advance_decline"] == "on the NYSE 326 more stocks are advancing than declining today, more up than down"
    assert state["breadth"]["tick_lean"].endswith("above zero 21 times and below zero 9 times of 30, leaning to buying")
    assert state["breadth"]["sectors_up"] == "8 of 11 sector funds are above where they opened today, most of them"
    # the same context read 15 minutes earlier: the newest $ADD value is not known yet, and only 15 ticks are
    earlier = scene_factory(full_scene.now - timedelta(minutes=15), flat_bars(165), market=full_scene.market)
    state, omitted = _labels(earlier)
    assert "advance_decline" not in state.get("breadth", {}) and omitted["breadth.advance_decline"].startswith("no NYSE advance-decline value")
    assert omitted["breadth.tick_lean"].startswith("needs 20 NYSE tick readings")


def test_no_market_context_omits_every_breadth_label(scene_factory):
    from spx_jev.labels import breadth
    _, omitted = _labels(scene_factory(at(11, 0), flat_bars(90)))
    assert {k: v for k, v in omitted.items() if k.startswith("breadth.")} == {
        p: "no market-context snapshot today" for p in breadth.LABELS if p not in breadth.DARK}


def test_a_missing_sector_or_symbol_omits_only_its_label(full_scene, scene_factory):
    known = {k: v for k, v in full_scene.market.known.items() if k not in SECTORS[:4] and k != "$ADD"}
    state, omitted = _labels(scene_factory(full_scene.now, flat_bars(180), market=MarketContext(known)))
    assert "tick_lean" in state["breadth"]
    assert omitted["breadth.sectors_up"] == "needs 8 of the 11 sector funds with a value today, have 7"
    assert omitted["breadth.advance_decline"] == "no NYSE advance-decline value at or before now"


# ---- the tape lane

def test_the_ruler_holds_then_measures_with_a_floor():
    held = ruler(flat_bars(15), SIGMA, at(9, 44))
    assert held == {"unit_points": 9.0, "unit_sigma": 0.12, "slices_used": 0, "source": "held"}
    floor = ruler(flat_bars(15), SIGMA, at(9, 45))                  # 1 point a slice, under the 2.25-point floor
    assert floor == {"unit_points": 2.25, "unit_sigma": 0.03, "slices_used": 3, "source": "floor"}
    bars = flat_bars(15, wick=3.0)
    bars[7]["high"] = 7730.0                                         # the middle slice wider still: the median, not the mean
    assert ruler(bars, SIGMA, at(9, 45)) == {"unit_points": 6.0, "unit_sigma": 0.08, "slices_used": 3, "source": "tape"}
    assert ruler(flat_bars(30, wick=3.0), SIGMA, at(10, 10)) is None  # the newest slice has no bars: the tape stopped


def test_the_move_since_the_last_read_keeps_the_sums_bands(lane_scene):
    state, _ = _labels(lane_scene)
    move = state["tape"]["move_since_read"]
    assert f"more than the {TAPE_FLAT_UNITS} cut and no more than {TAPE_BIG_UNITS}" in move and move.endswith("so it rose small")
    assert "in the bottom third for this minute, higher than 0 of 10 prior sessions" in state["tape"]["range_since_read"]


def test_the_days_first_tape_read_measures_from_the_open(lane_scene):
    from dataclasses import replace
    state, omitted = _labels(replace(lane_scene, last_read=None))
    assert omitted["tape.move_since_read"] == "the day's first read: no earlier read today to measure from"
    assert state["tape"]["range_since_read"].startswith("the range since the open, 180.167 minutes ago")


def test_the_live_lane_writes_no_tape_label_and_omits_none(full_scene):
    state, omitted = _labels(full_scene)
    assert "tape" not in state and not any(k.startswith("tape.") for k in omitted)


def test_the_options_tape_is_ranked_against_the_same_minute_and_omitted_when_the_collector_stopped(full_scene):
    from dataclasses import replace
    state, _ = _labels(full_scene)
    assert state["options"]["aggressor_side"] == (
        "over the last 15 minutes the 0DTE options tape leaned toward buying calls and selling puts, a tilt of +0.05 on a scale "
        "from -1 to +1 with each trade weighted by its delta, resting on the 60% of that flow whose side could be told; "
        "in the top third for this minute, higher than 8 of 10 prior sessions")
    prior = list(full_scene.prior_bars)
    stale = options_tape_at(full_scene.now - timedelta(minutes=3), prior_days=prior)      # its newest line is 4 minutes old
    _, omitted = _labels(replace(full_scene, options_tape=stale))
    assert omitted["options.aggressor_side"].startswith("no reading of the 0DTE options tape")
    _, omitted = _labels(replace(full_scene, options_tape=options_tape_at(full_scene.now, prior_days=prior[:4])))
    assert omitted["options.aggressor_side"] == "its rank needs 10 prior sessions with an options tape reading at this minute, have 4"


def _touch(kind, level, verdict=None, effort=None, status="engaged", outcome=None):
    return {"kind": kind, "level": level, "status": status, "effort_pct": effort, "verdict": verdict, "outcome": outcome, "near_spot": False}


def _siege(*towers, health="OK", baseline="robust"):
    return {"health": health, "baseline": baseline, "saturated": False, "towers": list(towers)}


def test_a_judged_wall_touch_is_described_from_when_the_scanner_first_saw_its_verdict(scene_factory):
    now = at(11, 0, ss=10)
    spot = 7700.0
    pending = make_row(now - timedelta(minutes=20), spot, siege=_siege(_touch("call_wall", 7715.0)))
    judged = make_row(now - timedelta(minutes=12), spot, siege=_siege(_touch("call_wall", 7715.0, "SIEGE", 83.3)))
    graded = _siege(_touch("call_wall", 7715.0, "SIEGE", 83.3, "resolved", "BREAK"))
    state, _ = _labels(scene_factory(now, flat_bars(90, price=spot), row_over={"siege": graded}, rows_before=[pending, judged], spot=spot))
    assert state["gex"]["wall_touch_volume"] == (
        "a touch of the call wall, now 0.20 sigma above price, was judged 12 minutes ago: SPY volume during it was in the 83rd "
        f"percentile of normal for that time of day, a siege, at or above the {WALL_TOUCH_SIEGE_PERCENTILE}th-percentile cut; "
        "half an hour after the touch the level had broken")
    quiet = make_row(now - timedelta(minutes=45), spot, siege=_siege(_touch("put_wall", 7690.0, "QUIET", 12.0)))
    state, _ = _labels(scene_factory(now, flat_bars(90, price=spot), rows_before=[quiet], spot=spot))
    assert state["gex"]["wall_touch_volume"] == "no touch of a wall or the magnet has had its SPY volume judged in the last 30 minutes"
    hugging = _siege(_touch("magnet", 7700.0, None, 95.0))                      # a level that hugged spot: never judged, never described
    state, _ = _labels(scene_factory(now, flat_bars(90, price=spot), row_over={"siege": hugging}, spot=spot))
    assert state["gex"]["wall_touch_volume"] == "no touch of a wall or the magnet has had its SPY volume judged in the last 30 minutes"


def test_the_wall_touch_volume_is_omitted_on_a_sick_feed_or_a_young_baseline(scene_factory):
    now = at(11, 0)
    _, omitted = _labels(scene_factory(now, flat_bars(90), row_over={"siege": _siege(health="FEED-LOST")}))
    assert omitted["gex.wall_touch_volume"] == "the siege box's SPY feed is FEED-LOST, not OK"
    _, omitted = _labels(scene_factory(now, flat_bars(90), row_over={"siege": _siege(baseline="warming")}))
    assert omitted["gex.wall_touch_volume"] == "the siege box's volume baseline is warming, not yet robust"
    _, omitted = _labels(scene_factory(now, flat_bars(90), row_over={"siege": None}))
    assert omitted["gex.wall_touch_volume"] == "row carries no siege read"


# ---- reading the station's files

def test_past_sessions_come_from_the_saved_file_and_today_from_the_live_one(tmp_path):
    prior = {"2026-09-17": flat_bars(390, day="2026-09-17"), "2026-09-16": flat_bars(200, day="2026-09-16")}
    state = write_state(tmp_path, DAY, [make_row(at(10, 0), 7700.0)], flat_bars(31), prior)
    (tmp_path / "spx_jev" / "bars" / "2026-09-17.jsonl").write_text(json.dumps(flat_bars(5, day="2026-09-17")[0]) + "\n")
    assert len(load_bars(state, "2026-09-17")) == 390                # the saved session wins over a partial live file
    assert len(load_bars(state, DAY)) == 31                          # today: the live file
    assert list(prior_bar_days(state, DAY)) == ["2026-09-17"]        # a 200-bar day is not a full session


def test_make_scene_reads_the_diary_through_the_adapter(tmp_path):
    rows = [make_row(at(10, 0), 7700.0), make_row(at(10, 5), 7701.0, ticker="SPY"), make_row(at(10, 10), 7702.0)]
    state = write_state(tmp_path, DAY, rows, flat_bars(45), prior_sessions(3))
    scene = make_scene(state, DAY)
    assert scene.row["ts"] == at(10, 10).isoformat() and "watchtower" not in scene.row and len(scene.rows_today) == 2
    assert len(scene.bars) == 40 and len(scene.prior_bars) == 3 and scene.market is None
    at_time = make_scene(state, DAY, at=at(10, 7).time())
    assert at_time.row["ts"] == at(10, 0).isoformat()
    with pytest.raises(ValueError, match="no SPX diary row at or before"):
        make_scene(state, DAY, at=at(9, 0).time())


def test_the_bar_clock_stamps_the_read_at_the_newest_finished_bar(tmp_path):
    rows = [make_row(at(9, 58), 7700.0, sigma=75.0), make_row(at(10, 0, ss=30), 7701.0, sigma=80.0, vwap=7690.0)]
    state = write_state(tmp_path, DAY, rows, bars_from_closes([7700.0 + i for i in range(40)]))
    scene = make_scene(state, DAY, bar_clock=True)
    assert scene.row["ts"] == at(10, 10).isoformat() and scene.row["spot"] == 7739.0 and scene.sigma == 75.0
    # the opening lane's gap and straddle labels read yesterday's close and the straddle from the newest row
    assert scene.row["vwap"] == 7690.0 and scene.row["prior_close"] == 7685.0 and scene.unit["source"] == "tape"
    assert scene.row["range_ruler"]["em_points"] == 16.4 and scene.rows_today[0]["sigma_anchor"] == 75.0


def test_a_bar_clock_read_leaves_out_a_diary_row_stamped_after_its_bar(tmp_path):
    rows = [make_row(at(9, 58), 7700.0, vwap=7690.0), make_row(at(10, 10, ss=2), 7701.0, vwap=7695.0)]
    state = write_state(tmp_path, DAY, rows, bars_from_closes([7700.0 + i for i in range(40)]))
    scene = make_scene(state, DAY, bar_clock=True)
    assert scene.row["ts"] == at(10, 10).isoformat() and scene.row["vwap"] == 7690.0
    assert [r["ts"] for r in scene.rows_today] == [at(9, 58).isoformat(), at(10, 10).isoformat()]


def test_the_market_context_joins_snapshots_and_backfilled_bars_by_when_each_was_known(tmp_path):
    lines = [context_line(at(10, 0, ss=20), quotes={"XLK": 200.0, "$TICK": 0.0}, bars={"$TICK": (at(9, 59), 350.0)}),
             context_line(at(10, 1, ss=20), quotes={"XLK": 201.0}, bars={"$TICK": (at(10, 0), -40.0)})]
    write_state(tmp_path, DAY, [make_row(at(10, 2), 7700.0)], flat_bars(32), context=lines)
    (tmp_path / "spx_jev" / "context" / "bars").mkdir()
    (tmp_path / "spx_jev" / "context" / "bars" / f"{DAY}.jsonl").write_text(
        json.dumps({"ts": at(9, 31).isoformat(), "bars": {"XLK": {"ts": at(9, 30).isoformat(), "close": 199.0}}}) + "\n")
    mk = load_market_context(tmp_path, DAY)
    assert mk.first("XLK") == 199.0 and mk.last("XLK", at(10, 1)) == 200.0 and mk.last("XLK", at(10, 2)) == 201.0
    assert mk.last("$TICK", at(10, 0, ss=30)) == 350.0               # the 09:59 bar finished at 10:00; a zero quote is a shell
    assert mk.between("$TICK", at(9, 0), at(10, 2)) == [350.0, -40.0]
    assert load_market_context(tmp_path, "2026-09-17") is None



def test_the_options_tape_reads_only_the_collectors_signed_lines(tmp_path):
    agg = tmp_path / "lob_flow" / "agg"
    agg.mkdir(parents=True)
    lines = [{"ts": at(10, 0).isoformat(), "engine": "lob_flow", "snapshot": {"tilt": 0.0, "determinate_share": None}},
             {"ts": at(10, 1).isoformat(), "engine": "spy_depth", "snapshot": {"tilt": 0.9, "determinate_share": 1.0}},
             {"ts": at(10, 2).isoformat(), "engine": "lob_flow", "snapshot": {"tilt": -0.04, "determinate_share": 0.55}}]
    (agg / f"{DAY}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    tape = load_options_tape(tmp_path, [DAY, "2026-09-17"])
    assert list(tape.days) == [DAY] and tape.at(at(10, 3)) == (-0.04, 0.55)
    assert tape.at(at(10, 1)) is None and tape.at(at(10, 6)) is None     # before the first signed line; past the age line
    assert load_options_tape(tmp_path, ["2026-09-17"]) is None


def test_a_half_day_before_is_yesterday_though_it_is_too_short_for_the_baselines(tmp_path):
    """Friday 2026-11-27 closes at 13:00 (210 bars): the Monday after reads it as yesterday, not the Wednesday."""
    monday = "2026-11-30"
    prior = {"2026-11-25": bars_from_closes([7600.0] * 390, day="2026-11-25"),
             "2026-11-27": bars_from_closes([7700.0 + i * 0.1 for i in range(210)], day="2026-11-27")}
    state = write_state(tmp_path, monday, [make_row(at(9, 31, day=monday), 7710.0), make_row(at(11, 32, day=monday), 7710.0)],
                        bars_from_closes([7710.0] * 122, day=monday), prior)
    scene = make_scene(state, monday)
    assert list(scene.prior_bars) == ["2026-11-25"] and scene.prior_day[0] == "2026-11-27"
    labels = build_labels(scene)
    assert labels.state["price"]["vs_yesterday"].startswith("price is inside yesterday's range")
    assert "levels.prior_day" not in labels.omitted
