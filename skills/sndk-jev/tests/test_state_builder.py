from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest

from conftest import DAY, at, bars_from_closes, flat_bars, make_row
from sndk_jev.state_builder import (MOVE_RULE_SIGMA, Scene, build_ab, build_state, make_scene, wilder_rsi)


def labels(scene) -> tuple[dict, dict]:
    return build_state(scene)


# ---------------------------------------------------------------- price

def test_recent_move_rising_falling_and_flat(scene_factory):
    sigma = 45.0
    # 09:30 to 12:30 flat, then a 0.4 sigma climb over the last 30 minutes
    closes = [1700.0] * 150 + [1700.0 + sigma * 0.4 * (i + 1) / 30 for i in range(30)]
    s = scene_factory(at(12, 30, ss=20), bars_from_closes(closes))
    state, _ = labels(s)
    assert "rose 0.40 sigma" in state["price"]["recent_move"]

    closes = [1700.0] * 150 + [1700.0 - sigma * 0.4 * (i + 1) / 30 for i in range(30)]
    state, _ = labels(scene_factory(at(12, 30, ss=20), bars_from_closes(closes)))
    assert "fell 0.40 sigma" in state["price"]["recent_move"]

    state, _ = labels(scene_factory(at(12, 30, ss=20), flat_bars(180)))
    assert f"stayed within {MOVE_RULE_SIGMA} sigma" in state["price"]["recent_move"]


def test_recent_move_needs_thirty_minutes(scene_factory):
    state, omitted = labels(scene_factory(at(9, 45), flat_bars(15)))
    assert "recent_move" not in state.get("price", {})
    assert "30 minutes" in omitted["price.recent_move"]


def test_day_range_position_thirds(scene_factory):
    bars = bars_from_closes([1700.0, 1720.0, 1740.0, 1760.0, 1780.0] + [1780.0] * 40)
    state, _ = labels(scene_factory(at(10, 30), bars, spot=1779.0))
    assert state["price"]["day_range_position"].startswith("price is in the top third")
    state, _ = labels(scene_factory(at(10, 30), bars, spot=1701.0))
    assert state["price"]["day_range_position"].startswith("price is in the bottom third")


def test_vs_vwap_bands(scene_factory):
    bars = flat_bars(60)
    state, _ = labels(scene_factory(at(10, 30), bars, row_over={"vwap": 1700.0 - 45.0 * 1.1}))
    assert "1.10 sigma above" in state["price"]["vs_vwap"]
    state, _ = labels(scene_factory(at(10, 30), bars, row_over={"vwap": 1700.0 + 45.0 * 0.05}))
    assert "within 0.15 sigma" in state["price"]["vs_vwap"]


# ---------------------------------------------------------------- range

def test_opening_box_still_forming_then_inside_then_break(scene_factory):
    state, _ = labels(scene_factory(at(9, 50), flat_bars(20)))
    assert state["range"]["box_status"].startswith("the opening box is still forming")

    # a 20-point box in the first 30 minutes, then flat inside it
    closes = [1700.0 + (i % 2) * 20.0 for i in range(30)] + [1710.0] * 60
    state, _ = labels(scene_factory(at(11, 0), bars_from_closes(closes)))
    assert state["range"]["box_status"].startswith("price is inside the opening box")
    assert "stayed inside it all day" in state["range"]["box_status"]

    # then a break well beyond the move bar
    state, _ = labels(scene_factory(at(11, 0), bars_from_closes(closes), spot=1760.0))
    assert state["range"]["box_status"].startswith("price has broken above the opening box")
    state, _ = labels(scene_factory(at(11, 0), bars_from_closes(closes), spot=1650.0))
    assert state["range"]["box_status"].startswith("price has broken below the opening box")


def test_range_vs_normal_needs_prior_sessions(scene_factory):
    bars = bars_from_closes([1700.0 + i for i in range(90)])
    prior = {f"2026-09-{d:02d}": bars_from_closes([1700.0 + 0.2 * i for i in range(390)], day=f"2026-09-{d:02d}") for d in (15, 16)}
    state, omitted = labels(scene_factory(at(11, 0), bars, prior_bars=prior))
    assert "today_vs_normal" not in state.get("range", {})
    assert "prior sessions" in omitted["range.today_vs_normal"]

    prior = {f"2026-09-{d:02d}": bars_from_closes([1700.0 + 0.2 * i for i in range(390)], day=f"2026-09-{d:02d}") for d in (11, 12, 15, 16, 17)}
    state, _ = labels(scene_factory(at(11, 0), bars, prior_bars=prior))
    assert state["range"]["today_vs_normal"].startswith("today's range so far is in the top third of the last 5 sessions")


# ---------------------------------------------------------------- implied volatility

def test_iv_trend_and_skew_and_em(scene_factory):
    now = at(12, 0)
    earlier = make_row(now - timedelta(minutes=30), 1700.0, atm_iv=0.40)
    earlier["meta"]["book_asof"] = earlier["ts"]
    state, _ = labels(scene_factory(now, flat_bars(150), rows_before=[earlier], row_over={"atm_iv": 0.435}))
    assert "rose 3.5 vol points" in state["iv"]["trend_30min"]
    state, _ = labels(scene_factory(now, flat_bars(150), rows_before=[earlier], row_over={"atm_iv": 0.41}))
    assert "stayed within the 2 vol point flat band" in state["iv"]["trend_30min"]

    state, _ = labels(scene_factory(now, flat_bars(150), row_over={"iv_skew": {"skew_pts": 2.4}}))
    assert state["iv"]["skew"].startswith("puts carry a higher implied volatility")
    state, _ = labels(scene_factory(now, flat_bars(150), row_over={"iv_skew": {"skew_pts": -1.5}}))
    assert state["iv"]["skew"].startswith("calls carry a higher implied volatility")

    state, _ = labels(scene_factory(now, flat_bars(150), row_over={"range_ruler": {"em_consumed": 1.7}}))
    assert "more than all of it" in state["iv"]["expected_move_used"]
    state, _ = labels(scene_factory(now, flat_bars(150), row_over={"range_ruler": {"em_consumed": 0.3}}))
    assert "under half of it" in state["iv"]["expected_move_used"]


def test_iv_trend_needs_a_row_thirty_minutes_back(scene_factory):
    state, omitted = labels(scene_factory(at(12, 0), flat_bars(150)))
    assert "trend_30min" not in state.get("iv", {})
    assert "30 minutes ago" in omitted["iv.trend_30min"]


# ---------------------------------------------------------------- gamma map and options

def test_gex_weight_and_walls(scene_factory):
    bars = flat_bars(120)
    state, _ = labels(scene_factory(at(11, 30), bars))
    assert state["gex"]["weight_side"].startswith("most of the options weight sits above price, 75%")
    # the fixture's put wall sits 1.0 sigma below and its call wall 1.2 sigma above, so the put wall is nearest
    assert state["gex"]["air_to_wall"].startswith("the nearest heavy strike is the put wall 1.00 sigma below price, more than 0.5 sigma away")

    state, _ = labels(scene_factory(at(11, 30), bars, row_over={"call_wall": 1700.0 + 45.0 * 0.3}))
    assert state["gex"]["air_to_wall"].startswith("a heavy strike sits within 0.5 sigma of price: the call wall 0.30 sigma above")

    state, _ = labels(scene_factory(at(11, 30), bars, row_over={"call_wall": None, "put_wall": None}))
    assert state["gex"]["air_to_wall"].startswith("no heavy strike sits within reach")

    state, _ = labels(scene_factory(at(11, 30), bars, row_over={"gex_views": {"gamma_above_spot": 1.0, "gamma_below_spot": 1.0}}))
    assert state["gex"]["weight_side"].startswith("the options weight is split about evenly")


def test_since_last_book_compares_the_previous_distinct_book(scene_factory):
    now = at(11, 30)
    prev = make_row(now - timedelta(minutes=2), 1700.0, put_wall=None, gamma_sign="negative")
    prev["meta"]["book_asof"] = prev["ts"]
    prev["gex_views"]["mass_by_strike"] = [[1700.0, 9000.0], [1750.0, 1000.0]]
    same_book = make_row(now - timedelta(minutes=1), 1700.0)          # same book_asof as the current row
    same_book["meta"]["book_asof"] = now.isoformat()
    state, _ = labels(scene_factory(now, flat_bars(120), rows_before=[prev, same_book]))
    s = state["gex"]["since_last_book"]
    assert s.startswith("since the last book, 2 minutes earlier:")
    assert "the heaviest strike now is a different strike" in s
    assert "the nearest call wall is the same strike" in s
    assert "the nearest put wall appeared" in s
    assert "the gamma sign now differs" in s


def test_since_last_book_omitted_without_an_earlier_book(scene_factory):
    state, omitted = labels(scene_factory(at(11, 30), flat_bars(120)))
    assert "since_last_book" not in state.get("gex", {})
    assert "earlier distinct" in omitted["gex.since_last_book"]


def test_options_activity_concentration(scene_factory):
    state, _ = labels(scene_factory(at(11, 30), flat_bars(120)))
    assert state["options"]["activity"].startswith("the three busiest strikes hold 90%")
    spread = {"gex_views": {"vol_gross_by_strike": [[1600.0 + 10 * i, 100] for i in range(20)]}}
    state, _ = labels(scene_factory(at(11, 30), flat_bars(120), row_over=spread))
    assert "less than half" in state["options"]["activity"]


# ---------------------------------------------------------------- volume

def _prior_days(volume: float, n: int = 6) -> dict:
    return {f"2026-09-{d:02d}": flat_bars(390, day=f"2026-09-{d:02d}", volume=volume) for d in range(1, n + 1)}


def test_volume_needs_a_baseline(scene_factory):
    state, omitted = labels(scene_factory(at(11, 30), flat_bars(120)))
    assert "now" not in state.get("volume", {})      # by_direction and at_price need no baseline and may still write
    assert "prior sessions" in omitted["volume.now"]


def test_volume_bands_against_same_time_of_day(scene_factory):
    heavy_then_light = [5000.0] * 90 + [100.0] * 30           # 09:30-11:00 heavy, last 30 min light
    s = scene_factory(at(11, 30), flat_bars(120, volume=heavy_then_light), prior_bars=_prior_days(1000.0))
    state, _ = labels(s)
    assert state["volume"]["now"].startswith("volume over the last 30 minutes is in the bottom fifth")
    assert "the last 30 minutes, is in the bottom fifth" in state["volume"]["on_move"]
    assert "the 30 minutes before that, is in the top fifth" in state["volume"]["on_move"]


# ---------------------------------------------------------------- momentum

def test_wilder_rsi_extremes():
    assert wilder_rsi([1.0 + i for i in range(20)]) == 100.0
    assert wilder_rsi([1.0] * 5) is None
    assert 0 <= wilder_rsi([100 + (i % 2) for i in range(30)]) <= 100


def test_momentum_labels_on_a_clean_climb(scene_factory):
    sigma = 45.0
    # flat until 12:00, then a straight climb of 0.6 sigma over 30 minutes with no pullback
    closes = [1700.0] * 150 + [1700.0 + sigma * 0.6 * (i + 1) / 30 for i in range(30)]
    state, _ = labels(scene_factory(at(12, 30, ss=10), bars_from_closes(closes, wick=0.0)))
    m = state["momentum"]
    assert m["rsi_1min"].endswith("above 70")
    assert "between two thirds and 1.5 times" in m["pace"]
    assert m["closes"].startswith("of the last five 1-minute bars, 5 closed in the direction of the move, which is up")
    assert m["pauses"].startswith("during the last 30 minutes the move up ran without a pause")


def test_momentum_pullback_and_pause(scene_factory):
    sigma = 45.0
    up = [1700.0 + sigma * 0.6 * (i + 1) / 15 for i in range(15)]            # climb 0.6 sigma in 15 minutes
    back = [up[-1] - sigma * 0.6 * 0.4 * (i + 1) / 15 for i in range(15)]    # give back 40% over 15 minutes
    closes = [1700.0] * 150 + up + back
    state, _ = labels(scene_factory(at(12, 30, ss=10), bars_from_closes(closes, wick=0.0)))
    assert "gave back 40% of itself" in state["momentum"]["pauses"]

    stall = [up[-1]] * 6                                                      # a 6-minute stall, then on again
    on = [up[-1] + sigma * 0.1 * (i + 1) / 9 for i in range(9)]
    closes = [1700.0] * 150 + up + stall + on
    state, _ = labels(scene_factory(at(12, 30, ss=10), bars_from_closes(closes, wick=0.0)))
    assert "paused for 6 minutes, between 3 and 10 minutes" in state["momentum"]["pauses"]


def test_momentum_pullback_is_a_share_of_the_whole_move(scene_factory):
    """An early wobble must never read as a thousand-percent pullback (seen on the 2026-09-17 replay)."""
    sigma = 45.0
    wobble = [1700.5, 1690.0]                                                # up half a point, then a 10-point dip
    climb = [1690.0 + (27.0 + 10.0) * (i + 1) / 28 for i in range(28)]       # then on to +0.6 sigma over the window
    closes = [1700.0] * 150 + wobble + climb
    state, _ = labels(scene_factory(at(12, 30, ss=10), bars_from_closes(closes, wick=0.0)))
    assert "gave back 39% of itself" in state["momentum"]["pauses"]
    assert not re.search(r"\d{3,}%", state["momentum"]["pauses"])


def test_momentum_skips_closes_and_pauses_without_a_move(scene_factory):
    state, omitted = labels(scene_factory(at(12, 30, ss=10), flat_bars(180)))
    assert "closes" not in state["momentum"] and "pauses" not in state["momentum"]
    assert "no move to judge" in omitted["momentum.closes"]


# ---------------------------------------------------------------- time

def test_session_progress_and_expiry(scene_factory):
    state, _ = labels(scene_factory(at(9, 45), flat_bars(15)))
    assert state["context"]["session_progress"].startswith("4% of the session has passed, the first fifth; the first hour")
    assert "session_phase" not in state["context"]          # folded into session_progress
    state, _ = labels(scene_factory(at(15, 20), flat_bars(350)))
    assert "the last fifth; the last hour, 40 minutes before the close" in state["context"]["session_progress"]
    state, _ = labels(scene_factory(at(12, 0), flat_bars(150)))
    assert "the middle of the session, 150 minutes after the open" in state["context"]["session_progress"]


def test_week_position_carries_edges_and_a_verdict(scene_factory):
    prior = {f"2026-09-{d:02d}": bars_from_closes([1700.0 + (i % 40) for i in range(390)], day=f"2026-09-{d:02d}") for d in range(12, 18)}
    state, _, _, verdicts = build_ab(scene_factory(at(11, 0), flat_bars(90, 1800.0), prior_bars=prior))
    assert state["price"]["multi_day_position"].startswith("price is above the last 5 sessions' high, in new ground, 1.34 sigma above that high")
    assert verdicts["price.multi_day_position"] == "new_ground_above"
    assert "vs_week_extremes" not in state["price"]         # folded into multi_day_position
    state, _, _, verdicts = build_ab(scene_factory(at(11, 0), flat_bars(90, 1720.0), prior_bars=prior))
    assert verdicts["price.multi_day_position"] == "inside_the_week"


def test_yesterday_close_lives_in_one_label_only(scene_factory):
    prior = {"2026-09-17": bars_from_closes([1700.0 + (i % 40) for i in range(390)], day="2026-09-17")}
    state, _ = labels(scene_factory(at(11, 0), flat_bars(90, 1720.0), prior_bars=prior))
    assert "close" not in state["price"]["vs_yesterday"]
    assert "last night's close" in state["price"]["vs_prior_close"]
    assert "minutes within reach of yesterday's high" in state["range"]["prior_level_touches"]

    state, _ = labels(scene_factory(at(12, 0), flat_bars(150), row_over={"meta": {"book_asof": "x", "expiries": [{"date": DAY, "dte": 0}]}}))
    assert state["context"]["expiry"] == "the front weekly expires today"
    state, _ = labels(scene_factory(at(12, 0), flat_bars(150)))       # 2026-09-25 is next Friday
    assert state["context"]["expiry"].startswith("the front weekly expires next week or later, on Friday, 7 days away")
    state, _ = labels(scene_factory(at(12, 0), flat_bars(150), row_over={"meta": {"book_asof": "x", "expiries": []}}))
    assert "expiry" not in state["context"]


# ---------------------------------------------------------------- news

def test_news_labels_and_price_reaction(scene_factory):
    sigma = 45.0
    closes = [1700.0] * 140 + [1700.0 + sigma * 0.3 * (i + 1) / 10 for i in range(10)]
    item = {"headline": "SanDisk raises full-year outlook", "source": "company release", "excerpt": "The company raised guidance.",
            "arrived": at(11, 50).isoformat(), "recent_headlines": [{"text": "SanDisk to report next week", "age": "2 hours ago"}]}
    state, _ = labels(scene_factory(at(12, 0, ss=30), bars_from_closes(closes), news=item))
    n = state["news"]
    assert n["headline"] == "SanDisk raises full-year outlook"
    assert n["age"] == "the item arrived 10 minutes ago"
    assert n["price_reaction"].startswith("price has risen 0.30 sigma since the item arrived")
    assert n["recent_headlines"] == ["2 hours ago: SanDisk to report next week"]
    assert "expectation" not in n


def test_no_news_means_no_news_group(scene_factory):
    state, _ = labels(scene_factory(at(12, 0), flat_bars(150)))
    assert "news" not in state


# ---------------------------------------------------------------- hygiene

def test_labels_carry_no_prices(scene_factory):
    sigma = 45.0
    closes = [1700.0 + (i % 7) * 3.0 for i in range(150)] + [1720.0 + sigma * 0.5 * (i + 1) / 30 for i in range(30)]
    s = scene_factory(at(12, 30, ss=10), bars_from_closes(closes), prior_bars=_prior_days(1000.0))
    state, _ = labels(s)
    text = json.dumps(state)
    assert "$" not in text
    assert not re.search(r"\b\d{4,}(\.\d+)?\b", text), "a four-digit number leaked into a label"


def test_make_scene_uses_only_finished_bars_and_prior_days(tmp_path):
    (tmp_path / "sndk_reversion").mkdir()
    (tmp_path / "sndk_bars").mkdir()
    now = at(10, 0, ss=30)
    rows = [make_row(at(9, 58), 1700.0), make_row(now, 1701.0)]
    (tmp_path / "sndk_reversion" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    bars = flat_bars(40)                                    # 09:30 .. 10:09, later than now on purpose
    (tmp_path / "sndk_bars" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")
    (tmp_path / "sndk_bars" / "2026-09-17.jsonl").write_text("\n".join(json.dumps(b) for b in flat_bars(390, day="2026-09-17")) + "\n")
    (tmp_path / "sndk_bars" / "2026-09-19.jsonl").write_text("\n".join(json.dumps(b) for b in flat_bars(390, day="2026-09-19")) + "\n")
    scene = make_scene(tmp_path, DAY)
    assert scene.now == now
    assert len(scene.bars) == 30, "only bars that finished before the row's timestamp count"
    assert list(scene.prior_bars) == ["2026-09-17"], "a later day never counts as a prior session"
    earlier = make_scene(tmp_path, DAY, at=at(9, 59).time())
    assert earlier.row["ts"] == at(9, 58).isoformat()
