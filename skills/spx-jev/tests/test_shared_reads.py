"""The reads every label family shares: the sigma rulers, the same-clock rank, the settled open, the session
extremes and the market context's symbols."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import DAY, at, bars_from_closes, context_line, flat_bars, make_row, prior_sessions, write_state
from spx_jev.cuts import MIN_RANK_SESSIONS
from spx_jev.labels.measures import session_extremes, settled_open
from spx_jev.labels.ranks import rank_against, same_clock_values
from spx_jev.labels.rulers import (SigmaRuler, morning_ruler, normal_day_sigma, remaining_straddles, sigma_anchor, sigma_live,
                                   straddle_left)
from spx_jev.row_adapter import labeller_row
from spx_jev.state_builder import MarketContext, load_market_context, make_scene


def _rows(*stamps_and_over):
    return [labeller_row(make_row(t, 7700.0, **over)) for t, over in stamps_and_over]


# ---- the sigma rulers

def test_the_anchor_counts_only_from_a_row_stamped_by_0940():
    on_time = _rows((at(9, 31), {"sigma": 80.0, "sigma_anchor": 74.0, "sigma_live": 80.0}))
    assert morning_ruler(on_time, None, None) == SigmaRuler(74.0, "anchor") and not SigmaRuler(74.0, "anchor").estimated
    late = _rows((at(9, 45), {"sigma_anchor": 90.0, "sigma_live": 77.0}), (at(9, 50), {"sigma_live": 78.0}))
    assert morning_ruler(late, 15.0, 7700.0) == SigmaRuler(77.0, "live")            # the late anchor is not trusted
    no_live = [{k: v for k, v in r.items() if k != "sigma_live"} for r in late]
    vix = morning_ruler(no_live, 15.0, 7700.0)
    assert vix.source == "vix" and vix.estimated and vix.points == pytest.approx(7700.0 * 0.15 / math.sqrt(252))
    assert morning_ruler([], None, 7700.0) is None


def test_the_scenes_anchor_live_sigma_and_straddle(scene_factory):
    now = at(11, 0)
    scene = scene_factory(now, flat_bars(90), row_over={"sigma": 81.0, "sigma_anchor": 75.0, "sigma_live": 81.0},
                          rows_before=[make_row(at(9, 32), 7700.0, sigma=75.0, sigma_anchor=75.0)])
    assert sigma_anchor(scene) == SigmaRuler(75.0, "anchor")                         # never the row's ratcheted 81
    assert sigma_live(scene) == 81.0
    no_field = replace(scene, row={k: v for k, v in scene.row.items() if k != "sigma_live"})
    assert sigma_live(no_field) == pytest.approx(7700.0 * 0.155 / math.sqrt(252))
    assert straddle_left(scene) == 16.4 and remaining_straddles(scene, -24.6) == pytest.approx(1.5)
    no_em = replace(scene, row={**scene.row, "range_ruler": {"em_open": 22.0}})
    assert straddle_left(no_em) is None and remaining_straddles(no_em, 5.0) is None


def test_the_anchor_falls_back_to_the_vix_at_the_settled_open(scene_factory):
    now = at(10, 30)
    market = MarketContext({"$VIX": [(at(9, 33), 14.0), (at(9, 35), 16.0), (at(10, 0), 30.0)]})
    scene = scene_factory(now, flat_bars(60, price=7650.0), row_over={"sigma_live": None}, market=market)
    scene = replace(scene, rows_today=[{k: v for k, v in r.items() if k not in ("sigma_anchor", "sigma_live")}
                                       for r in [dict(scene.row, ts=at(9, 50).isoformat())]])
    assert sigma_anchor(scene) == SigmaRuler(pytest.approx(7650.0 * 0.16 / math.sqrt(252)), "vix")


def test_the_normal_day_sigma_is_the_median_of_trusted_anchors(scene_factory):
    scene = scene_factory(at(11, 0), flat_bars(90))
    anchors = {f"2026-09-{d:02d}": SigmaRuler(60.0 + d, "anchor") for d in range(10, 10 + MIN_RANK_SESSIONS)}
    assert normal_day_sigma(replace(scene, prior_rulers=anchors)) == 72.0
    one_short = dict(list(anchors.items())[1:], **{"2026-09-09": SigmaRuler(500.0, "vix"), "2026-09-08": None})
    assert normal_day_sigma(replace(scene, prior_rulers=one_short)) is None           # estimated and missing days do not count


# ---- the same-clock rank

def test_a_value_is_ranked_against_the_same_minute_of_trusted_sessions(scene_factory):
    prior = prior_sessions(8)
    now = at(11, 0, ss=30)
    days = list(prior)
    rulers = {d: SigmaRuler(75.0, "anchor") for d in days}
    rulers[days[0]] = SigmaRuler(75.0, "live")                                        # an estimated day sits out
    scene = replace(scene_factory(now, flat_bars(90), prior_bars=prior), prior_rulers=rulers)
    seen = []

    def last_close_in_sigma(bars, then, sigma):
        seen.append((then, len(bars), sigma))
        return float(bars[-1]["close"]) / sigma
    values = same_clock_values(scene, last_close_in_sigma)
    assert len(values) == 7 and all(n == 90 and s == 75.0 for _, n, s in seen)       # 09:30..10:59 finished by 11:00:30
    assert seen[0][0].isoformat() == f"{days[1]}T11:00:30-04:00"
    rank = rank_against(max(values) + 1, values)
    assert (rank.higher_than, rank.of, rank.share) == (7, 7, 1.0)
    assert rank.words() == "higher than 7 of the last 7 sessions at this minute"
    assert rank_against(1.0, values[:MIN_RANK_SESSIONS - 1]) is None


# ---- the settled open and the session extremes

def test_the_settled_open_is_the_close_of_the_0934_bar():
    bars = bars_from_closes([7700.0, 7701.0, 7702.0, 7703.0, 7709.0, 7710.0])
    assert settled_open(bars) == 7709.0
    assert settled_open(bars[:4]) is None                                             # the 09:34 bar has not finished


def test_the_session_extremes_say_when_each_was_first_made():
    bars = bars_from_closes([7700.0, 7705.0, 7705.0, 7690.0, 7698.0], wick=0.0)
    ex = session_extremes(bars)
    assert (ex.high, ex.high_at, ex.low, ex.low_at) == (7705.0, at(9, 32), 7690.0, at(9, 34))
    assert session_extremes(bars, since=at(9, 33)).high == 7705.0 and session_extremes(bars, since=at(9, 40)) is None


# ---- the market context's symbols

def test_futures_read_under_their_root_and_yields_in_percent(tmp_path):
    (tmp_path / "spx_jev" / "context" / "bars").mkdir(parents=True)
    lines = [context_line(at(10, 0, ss=5), quotes={"/ESZ26": 7805.75, "$TNX": 51.84, "XLK": 200.0}),
             context_line(at(10, 5, ss=5), quotes={"/ESZ26": 7807.0, "$TNX": 51.9})]
    (tmp_path / "spx_jev" / "context" / f"{DAY}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    backfill = {"ts": at(9, 31).isoformat(), "bars": {"$TNX": {"ts": at(9, 30).isoformat(), "open": 51.0, "high": 51.5, "low": 50.9, "close": 51.2},
                                                      "/ES": {"ts": at(9, 30).isoformat(), "open": 7800.0, "high": 7801.0, "low": 7799.0, "close": 7800.5}}}
    (tmp_path / "spx_jev" / "context" / "bars" / f"{DAY}.jsonl").write_text(json.dumps(backfill) + "\n")
    mk = load_market_context(tmp_path, DAY)
    assert "/ESZ26" not in mk.known and mk.first("/ES") == 7800.5 and mk.last("/ES", at(10, 6)) == 7807.0
    assert mk.last("$TNX", at(10, 1)) == pytest.approx(5.184) and mk.first("$TNX") == pytest.approx(5.12)
    assert mk.change("$TNX", at(10, 1), at(10, 6)) == pytest.approx(0.006)            # 0.6 basis points
    assert mk.bars_between("$TNX", at(9, 30), at(9, 31)) == [{**backfill["bars"]["$TNX"], "open": pytest.approx(5.1),
                                                               "high": pytest.approx(5.15), "low": pytest.approx(5.09), "close": pytest.approx(5.12)}]
    assert mk.last("XLK", at(10, 20)) == 200.0 and mk.last("XLK", at(10, 20), max_age_min=10) is None


# ---- the scene carries the prior sessions' rulers and markets

def test_the_scene_loads_each_prior_sessions_morning_ruler_and_market(tmp_path):
    prior = {d: flat_bars(390, day=d) for d in ("2026-09-16", "2026-09-17")}
    state = write_state(tmp_path, DAY, [make_row(at(10, 5), 7700.0)], flat_bars(40), prior)
    (state / "reversion" / "2026-09-17.jsonl").write_text(
        json.dumps(make_row(at(9, 31, day="2026-09-17"), 7700.0, sigma_anchor=71.0)) + "\n" + json.dumps({"broken": True}) + "\n")
    (state / "reversion" / "2026-09-16.jsonl").write_text(json.dumps(make_row(at(10, 15, day="2026-09-16"), 7700.0, sigma_live=69.0)) + "\n")
    (state / "spx_jev" / "context").mkdir(parents=True)
    (state / "spx_jev" / "context" / "2026-09-17.jsonl").write_text(json.dumps(context_line(at(10, 0, day="2026-09-17"), quotes={"$VIX": 15.0})) + "\n")
    scene = make_scene(state, DAY)
    assert scene.prior_rulers == {"2026-09-17": SigmaRuler(71.0, "anchor"), "2026-09-16": SigmaRuler(69.0, "live")}
    assert list(scene.prior_markets) == ["2026-09-17"] and scene.prior_markets["2026-09-17"].first("$VIX") == 15.0
    assert scene.spot == 7700.0 and scene.minutes_since_open == 35.0 and scene.minutes_to_close == 355.0
    assert scene.day == DAY and scene.state_dir == state
    assert scene.session_close - scene.now == timedelta(minutes=355)
