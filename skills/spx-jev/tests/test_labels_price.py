"""The price family's final-set labels and its two gates: the half hour's move, where price sits in the day and
against its average and yesterday's close, the afternoon leg, the day's character, its move from the settled
open and from yesterday's close, the hour's one-way test, the half hour's shape, the push toward yesterday's
close, a fresh session extreme and the reach to the day's average. Every size is ranked against the same minute
on the prior sessions (ranks.rank_sessions), so the reads here carry ten prior sessions whose sizes are known."""
from __future__ import annotations

import json
import random
import statistics
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row, prior_sessions, write_state
from spx_jev.labels import price
from spx_jev.labels.price import ANCHORED, GATES, NO_ANCHOR, build_price_labels
from spx_jev.labels.ranks import rank_sessions, same_clock_values
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.state_builder import make_scene

SIGMA = 75.0
PRIOR_DAYS = [f"2026-09-{d:02d}" for d in range(17, 7, -1)]


def _scene(scene_factory, now, closes, wick=0.5, **kw):
    """A read on bars closing at ``closes`` from 09:30, whose morning anchor is trusted (the day's first row is
    stamped 09:31, with the sigma the fixtures use)."""
    bars = bars_from_closes(closes, wick=wick)
    return scene_factory(now, bars, rows_before=[make_row(at(9, 31), closes[0])], **kw)


def _labels(scene):
    ls = build_price_labels(scene)
    return {f"{g}.{k}": v for g, labels in ls.state.items() for k, v in labels.items()}, ls.omitted, ls.gates


def _stepped(minute: int):
    """Prior session k (1 to 10) flat at 7700 until the bar ``minute`` minutes after 09:30, then 1.5k points higher: every
    move over a window holding that bar is 0.02k sigma, so a move of 0.02k sigma or less beats k - 1 of them."""
    return lambda k: [7700.0 + (1.5 * k if i >= minute else 0.0) for i in range(390)]


def _zigzag_then_ramp(k: int) -> list[float]:
    """Prior session k flat to 10:30, then ten minutes zigzagging a point either way, then a 20-minute climb of 1.5k
    points: the half hour to 11:00 moved 0.02k sigma with a path efficiency of 1.5k / (1.5k + 10)."""
    return [7700.0] * 60 + [7701.0, 7700.0] * 5 + [7700.0 + 1.5 * k * (s + 1) / 20 for s in range(20)] + [7700.0 + 1.5 * k] * 300


def _ranked(scene, shape=_stepped(75), days=PRIOR_DAYS, estimated=()):
    """The read with ten prior sessions shaped by ``shape`` (the step at 10:45 by default), each on a trusted
    75-point ruler but ``estimated`` ones."""
    prior = {d: bars_from_closes(shape(k), day=d) for k, d in enumerate(days, start=1)}
    rulers = {d: SigmaRuler(SIGMA, "vix" if d in estimated else "anchor") for d in days}
    return replace(scene, prior_bars=prior, prior_rulers=rulers)


def _with_diaries(scene, root, days=PRIOR_DAYS, **levels):
    """The read with a state folder holding, for prior session k, a diary row a minute before the read's clock whose
    spot sits 1.5k points above yesterday's close and the day's average: each distance is 0.02k sigma."""
    clock = scene.now.time()
    for k, d in enumerate(days, start=1):
        t = datetime.combine(datetime.fromisoformat(d).date(), clock, tzinfo=scene.now.tzinfo) - timedelta(minutes=1)
        row = make_row(t, 7700.0, prior_close=7700.0 - 1.5 * k, vwap=7700.0 - 1.5 * k, **levels)
        (root / "reversion").mkdir(parents=True, exist_ok=True)
        (root / "reversion" / f"{d}.jsonl").write_text(json.dumps(row) + "\n")
    return replace(scene, state_dir=root)


# ---- price.recent_move

def _move_scene(scene_factory, up, **kw):
    """A read at 11:00 whose last half hour stepped ``up`` points at its first minute and held."""
    return _scene(scene_factory, at(11, 0, ss=5), [7700.0] * 60 + [7700.0 + up] * 30, **kw)


@pytest.mark.parametrize("up, sentence", [
    (3.75, "over the last 30 minutes price moved +0.05 sigma, larger than 2 of the last 10 sessions at this minute, bottom third: no real move"),
    (10.5, "over the last 30 minutes price rose 0.14 sigma, larger than 6 of the last 10 sessions at this minute, middle third: a move"),
    (15.0, "over the last 30 minutes price rose 0.20 sigma, larger than 9 of the last 10 sessions at this minute, top third: a strong move"),
    (22.5, "over the last 30 minutes price rose 0.30 sigma, larger than 10 of the last 10 sessions at this minute, top third: a strong move"),
    (-10.5, "over the last 30 minutes price fell 0.14 sigma, larger than 6 of the last 10 sessions at this minute, middle third: a move"),
])
def test_the_half_hours_move_is_sized_by_its_third_at_this_minute(scene_factory, up, sentence):
    state, _, _ = _labels(_ranked(_move_scene(scene_factory, up)))
    where = "top" if up > 0 else "bottom"
    assert state["price.recent_move"] == f"{sentence}; the half hour closed in the {where} fifth of its own range"


def test_the_moves_figure_shades_the_bottom_third_as_its_flat_band(scene_factory):
    """The phone's gauge shades the band its verdict is judged against: here the prior moves are 0.02 to 0.20 sigma,
    so a move up to 0.08 sigma is in the bottom third and one past 0.14 sigma in the top third."""
    ls = build_price_labels(_ranked(_move_scene(scene_factory, 10.5)))
    assert ls.figures["price.recent_move"] == {"kind": "signed", "value": 0.14, "band": 0.08, "strong": 0.14, "unit": "sigma", "verdict": "rising"}
    ls = build_price_labels(_ranked(_move_scene(scene_factory, -6.0)))
    assert ls.figures["price.recent_move"]["verdict"] == "going_nowhere"


@pytest.mark.parametrize("n", range(10, 21))
def test_the_band_edges_place_a_value_as_its_rank_does(n):
    rnd = random.Random(n)
    base = [round(rnd.uniform(0.0, 0.3), 2) for _ in range(n)]
    lo, hi = price._band_edges(base)
    for v in [*base, *(round(rnd.uniform(0.0, 0.3), 3) for _ in range(50))]:
        band = rank_sessions(v, base, "")[0].band
        assert (band == "bottom third") == (v <= lo) and (band == "top third") == (v > hi), (v, band)


def test_the_move_needs_ten_sessions_and_an_estimated_session_sits_out(scene_factory):
    scene = _move_scene(scene_factory, 10.5)
    _, omitted, _ = _labels(_ranked(scene, estimated=PRIOR_DAYS[:1]))
    assert omitted["price.recent_move"] == "its rank needs 10 prior sessions with a 30-minute move at this minute, have 9"
    _, omitted, _ = _labels(scene)
    assert omitted["price.recent_move"] == "its rank needs 10 prior sessions with a 30-minute move at this minute, have 0"
    assert omitted["price.move_shape"].startswith("no 30-minute move to shape: its rank needs 10 prior sessions")


def test_the_move_needs_half_an_hour_and_a_bar_from_its_start(scene_factory):
    _, omitted, _ = _labels(_scene(scene_factory, at(9, 50), [7700.0] * 20))
    assert omitted["price.recent_move"] == "needs 30 minutes of session"
    late = [b for b in bars_from_closes([7700.0] * 90) if b["ts"] >= at(10, 31).isoformat()]
    scene = scene_factory(at(11, 0, ss=5), late, rows_before=[make_row(at(9, 31), 7700.0)])
    assert _labels(scene)[1]["price.recent_move"] == "no finished bar 30 minutes ago"


def test_a_stopped_tape_leaves_the_half_hour_unmeasured(scene_factory, tmp_path):
    """Live, spot is the row's while the bars can stop: bars to 14:30 and a read at 15:02 would otherwise give a
    62-minute move as the half hour's, and wake prior_close_push_fade on it."""
    scene = scene_factory(at(15, 2, ss=5), bars_from_closes([7700.0] * 300), rows_before=[make_row(at(9, 31), 7700.0)], spot=7712.0,
                          row_over={"prior_close": 7713.0})
    state, omitted, gates = _labels(_with_diaries(_ranked(scene), tmp_path))
    stopped = "the bars have stopped: no bar finished in the minute before the last 30 minutes"
    assert omitted["price.recent_move"] == stopped
    assert omitted["price.move_shape"] == f"no 30-minute move to shape: {stopped}"
    assert omitted["price.prior_close_push"] == f"no 30-minute move to judge: {stopped}" and gates["prior_close_push_fade"] is None
    assert state["price.afternoon_leg"].startswith("since 14:00 price has risen") and "last 30 minutes" not in state["price.afternoon_leg"]


def test_the_move_needs_20_bars_in_its_half_hour(scene_factory):
    bars = [b for b in bars_from_closes([7700.0] * 90) if not at(10, 35).isoformat() <= b["ts"] < at(10, 46).isoformat()]
    scene = scene_factory(at(11, 0, ss=5), bars, rows_before=[make_row(at(9, 31), 7700.0)])
    assert _labels(scene)[1]["price.recent_move"] == "needs 20 finished bars in the last 30 minutes, have 19"


# ---- price.day_range_position

@pytest.mark.parametrize("spot, sentence", [
    (7750.0, "price is in the top band of today's range so far (at or past the 75% line), 75% of the way up"),
    (7790.0, "price is in the top band of today's range so far (at or past the 75% line), 95% of the way up"),
    (7700.0, "price is between the bands of today's range so far (between the 25% and 75% lines), 50% of the way up"),
    (7650.0, "price is in the bottom band of today's range so far (at or under the 25% line), 25% of the way up"),
])
def test_where_price_sits_in_the_days_range(scene_factory, spot, sentence):
    state, _, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), [7700.0, 7800.0, 7600.0] + [7700.0] * 87, wick=0.0, spot=spot))
    assert state["price.day_range_position"] == sentence


def test_the_range_position_needs_a_range(scene_factory):
    assert _labels(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 90, wick=0.0))[1]["price.day_range_position"] == "today's range is zero so far"
    scene = scene_factory(at(9, 30, ss=30), [], rows_before=[make_row(at(9, 30, ss=10), 7700.0)])
    assert _labels(scene)[1]["price.day_range_position"] == "no finished bars yet"


# ---- price.vs_vwap

def _vwap_scene(scene_factory, root, vwap):
    """A read at 11:00 that stepped from 7700 to 7715 at 10:30 (the 10:30 bar's low still reaches 7699.5), with the
    day's average at ``vwap`` since the morning's row, and prior rows 0.02k sigma from theirs."""
    scene = scene_factory(at(11, 0, ss=5), bars_from_closes([7700.0] * 60 + [7715.0] * 30), row_over={"vwap": vwap},
                          rows_before=[make_row(at(9, 31), 7700.0, vwap=vwap)])
    return _with_diaries(_ranked(scene), root)


@pytest.mark.parametrize("vwap, sentence", [
    (7700.0, "price is 0.20 sigma above the day's volume-weighted average price (VWAP), further from it than 9 of the last 10 sessions at this "
             "minute, top third: far from it; it last traded at VWAP 29 minutes ago"),
    (7708.25, "price is 0.09 sigma above the day's volume-weighted average price (VWAP), further from it than 4 of the last 10 sessions at this "
              "minute, middle third: away from it; it last traded at VWAP 29 minutes ago"),
    (7712.0, "price is 0.04 sigma above the day's volume-weighted average price (VWAP), further from it than 1 of the last 10 sessions at this "
             "minute, bottom third: at VWAP; it last traded at VWAP 29 minutes ago"),
    (7730.0, "price is 0.20 sigma below the day's volume-weighted average price (VWAP), further from it than 9 of the last 10 sessions at this "
             "minute, top third: far from it; it has not traded at VWAP today"),
])
def test_price_against_the_days_average(scene_factory, tmp_path, vwap, sentence):
    state, _, _ = _labels(_vwap_scene(scene_factory, tmp_path, vwap))
    assert state["price.vs_vwap"] == sentence


def test_the_average_figure_shades_the_bottom_third_of_the_same_distance(scene_factory, tmp_path):
    ls = build_price_labels(_vwap_scene(scene_factory, tmp_path, 7712.0))
    assert ls.figures["price.vs_vwap"] == {"kind": "signed", "value": 0.04, "band": 0.08, "unit": "sigma", "verdict": "at_it"}


def test_each_bar_is_judged_against_the_average_known_when_it_finished(scene_factory, tmp_path):
    """Price stepped 7700, 7720, 7740 at 10:00 and 10:30 while the average drifted up behind it to 7720: the bars
    at 7720 sat on today's average, but the average was 7700 or 7706 then, so the last touch is the 10:00 bar."""
    rows = [make_row(at(9, 31), 7700.0, vwap=7700.0), make_row(at(10, 5), 7720.0, vwap=7706.0), make_row(at(10, 35), 7740.0, vwap=7712.0)]
    scene = scene_factory(at(11, 0, ss=5), bars_from_closes([7700.0] * 30 + [7720.0] * 30 + [7740.0] * 30), row_over={"vwap": 7720.0},
                          rows_before=rows)
    assert _labels(_with_diaries(_ranked(scene), tmp_path))[0]["price.vs_vwap"] == (
        "price is 0.27 sigma above the day's volume-weighted average price (VWAP), further from it than 10 of the last 10 sessions at this minute, "
        "top third: far from it; it last traded at VWAP 59 minutes ago")


def test_no_vwap_on_the_row_omits_the_average_labels_and_leaves_the_reach_question_missing_them(scene_factory, tmp_path):
    _, omitted, gates = _labels(_vwap_scene(scene_factory, tmp_path, None))
    assert omitted["price.vs_vwap"] == omitted["price.vwap_reach"] == "row carries no vwap" and gates["average_reach_30"] is None


def test_the_distances_need_ten_prior_rows_at_this_minute(scene_factory, tmp_path):
    scene = _ranked(_move_scene(scene_factory, 15.0))
    _, omitted, gates = _labels(scene)
    assert omitted["price.vs_vwap"] == "no state folder to read the prior sessions' diaries from" and gates["average_reach_30"] is None
    _, omitted, gates = _labels(_with_diaries(scene, tmp_path, days=PRIOR_DAYS[:9]))
    assert gates["prior_close_push_fade"] is None and omitted["price.vs_prior_close"] == (
        "its rank needs 10 prior sessions with yesterday's close on a diary row at this minute, have 9")
    assert omitted["price.vs_vwap"] == "its rank needs 10 prior sessions with the day's VWAP on a diary row at this minute, have 9"


def test_an_estimated_session_sits_out_of_the_distances(scene_factory, tmp_path):
    """A distance in sigma is sized in each session's own ruler, so a session whose ruler was estimated sits out, as it
    does of the move, though its diary row is on file."""
    _, omitted, _ = _labels(_with_diaries(_ranked(_move_scene(scene_factory, 15.0), estimated=PRIOR_DAYS[:1]), tmp_path))
    assert omitted["price.vs_prior_close"] == "its rank needs 10 prior sessions with yesterday's close on a diary row at this minute, have 9"
    assert omitted["price.vs_vwap"] == "its rank needs 10 prior sessions with the day's VWAP on a diary row at this minute, have 9"


# ---- price.vs_prior_close

@pytest.mark.parametrize("prior_close, sentence", [
    (7680.0, "price is 0.47 sigma above yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: far from it"),
    (7707.5, "price is 0.10 sigma above yesterday's close, further from it than 4 of the last 10 sessions at this minute, middle third: away from it"),
    (7720.0, "price is 0.07 sigma below yesterday's close, further from it than 3 of the last 10 sessions at this minute, bottom third: near it"),
    (7745.0, "price is 0.40 sigma below yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: far from it"),
])
def test_price_against_yesterdays_close(scene_factory, tmp_path, prior_close, sentence):
    state, _, _ = _labels(_with_diaries(_ranked(_move_scene(scene_factory, 15.0, row_over={"prior_close": prior_close})), tmp_path))
    assert state["price.vs_prior_close"] == sentence


def test_no_prior_close_omits_every_label_that_reads_it(scene_factory, tmp_path):
    _, omitted, gates = _labels(_with_diaries(_ranked(_move_scene(scene_factory, 15.0, row_over={"prior_close": None})), tmp_path))
    assert {p: omitted[p] for p in ("price.vs_prior_close", "price.day_move_split", "price.prior_close_push")} == {
        p: "row carries no prior close" for p in ("price.vs_prior_close", "price.day_move_split", "price.prior_close_push")}
    assert gates["prior_close_push_fade"] is None


# ---- price.afternoon_leg

def _afternoon_scene(scene_factory, leg, now=None, **kw):
    """Flat at 7700 through the 13:59 bar, then ``leg`` points higher from 14:00 on; the prior sessions step at 14:30."""
    now = now or at(15, 2, ss=5)
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    return _ranked(_scene(scene_factory, now, ([7700.0] * 270 + [7700.0 + leg] * 200)[:minutes], **kw), _stepped(300))


@pytest.mark.parametrize("leg, lead, where", [
    (15.0, "since 14:00 price has risen 0.20 sigma, larger than 9 of the last 10 sessions at this minute, top third: a strong move", "97%"),
    (6.0, "since 14:00 price has moved +0.08 sigma, larger than 3 of the last 10 sessions at this minute, bottom third: no real move", "93%"),
    (-10.0, "since 14:00 price has fallen 0.13 sigma, larger than 6 of the last 10 sessions at this minute, middle third: a move", "5%"),
])
def test_the_afternoon_leg_from_1400_with_the_day_around_it(scene_factory, leg, lead, where):
    state, _, _ = _labels(_afternoon_scene(scene_factory, leg, row_over={"prior_close": 7700.0 + leg - 15.0}))
    assert state["price.afternoon_leg"] == (f"{lead}; the day is 0.20 sigma above yesterday's close and price sits at {where} of today's "
                                            f"range; the last 30 minutes did not move")


def test_the_afternoon_leg_waits_for_the_1359_bar_to_finish(scene_factory):
    _, omitted, _ = _labels(_afternoon_scene(scene_factory, 15.0, now=at(13, 59, ss=59)))
    assert omitted["price.afternoon_leg"] == "no finished 13:59 bar: the afternoon leg starts at 14:00"
    state, _, _ = _labels(_afternoon_scene(scene_factory, 15.0, now=at(14, 0, ss=5), row_over={"prior_close": None}))
    assert state["price.afternoon_leg"] == ("since 14:00 price has moved +0.00 sigma, larger than 0 of the last 10 sessions at this minute, "
                                            "bottom third: no real move; price sits at 50% of today's range; the last 30 minutes did not move")


def test_the_afternoon_leg_is_ranked_over_the_minutes_since_1400(scene_factory):
    _, omitted, _ = _labels(replace(_afternoon_scene(scene_factory, 15.0), prior_bars={}))
    assert omitted["price.afternoon_leg"] == "its rank needs 10 prior sessions with a 62-minute move at this minute, have 0"


# ---- price.day_character

def test_the_days_character_is_first_asked_at_the_first_live_read_that_can_measure_it():
    """Its label needs an hour of 5-minute returns from 10:00, so a read before that is always missing it."""
    from spx_jev.lane import LIVE
    doc = json.loads((Path(__file__).resolve().parent.parent / "spec" / "question_set.json").read_text(encoding="utf-8"))
    q = next(q for g in doc["groups"] for q in g["questions"] if q["id"] == "day_character")
    ready = (datetime.combine(datetime.now().date(), price.CHARACTER_FROM)
             + timedelta(minutes=price.CHARACTER_MIN_STEPS * price.CHARACTER_STEP_MIN)).time()
    assert q["schedule"]["thirty_minute"]["from"] == next(t for t in LIVE.read_times() if time.fromisoformat(t) > ready)


BUILDING = ([2.0] * 6 + [-2.0] * 6) * 2
CANCELLING = [2.0, -2.0] * 12
MIXED = [-1.0, -1.0, 2.0, -2.0, -2.0, 1.0, 2.0, -2.0, -2.0, -1.0, -1.0, 1.0, -2.0, -2.0, 2.0, -2.0, -2.0, 2.0, 1.0, -1.0, 1.0, 2.0, 2.0, -1.0]


def _walked(steps: list[float]) -> list[float]:
    """Flat to 10:00, then one 5-minute return per step, each walked in a straight line over its five minutes."""
    closes = [7700.0] * 30
    for r in steps:
        closes += [closes[-1] + r * (k + 1) / 5 for k in range(5)]
    return closes


def _random_walk(k: int) -> list[float]:
    rnd = random.Random(k)
    return (_walked([rnd.gauss(0.0, 2.0) for _ in range(72)]) + [7700.0] * 390)[:390]


def _character_scene(scene_factory, steps):
    scene = _scene(scene_factory, at(10, 0) + timedelta(minutes=5 * len(steps), seconds=5), _walked(steps))
    return _ranked(scene, _random_walk)


@pytest.mark.parametrize("steps, band", [(BUILDING, "top third: building"), (MIXED, None), (CANCELLING, "bottom third: cancelling")])
def test_the_days_character_is_its_variance_ratio_against_the_same_minute(scene_factory, steps, band):
    scene = _character_scene(scene_factory, steps)
    ratio = price._variance_ratio(scene.bars, scene.now)[0]
    rank, _ = rank_sessions(ratio, same_clock_values(scene, lambda bars, then, sigma: price._variance_ratio(bars, then)[0]), "")
    word = {"top third": "building", "middle third": "mixed", "bottom third": "cancelling"}[rank.band]
    assert _labels(scene)[0]["price.day_character"] == (f"so far today, 30-minute stretches have been {ratio:.2f} times what their 5-minute "
                                                        f"pieces would suggest, higher than {rank.higher_than} of the last 10 sessions at "
                                                        f"this minute, {rank.band}: {word}")
    assert band is None or f"{rank.band}: {word}" == band


@pytest.mark.parametrize("n_steps", [12, 18, 30])
def test_a_random_walk_reads_one_on_average_at_any_hour(n_steps):
    """Without the small-sample correction a random walk read about 0.4 at 11:00 and under one on most days, so the
    ratio answered the clock rather than the day."""
    rnd = random.Random(n_steps)
    now = at(10, 0) + timedelta(minutes=5 * n_steps, seconds=5)
    ratios = [price._variance_ratio(bars_from_closes(_walked([rnd.gauss(0.0, 2.0) for _ in range(n_steps)])), now)[0] for _ in range(300)]
    assert statistics.fmean(ratios) == pytest.approx(1.0, abs=0.1)


def test_the_character_needs_an_hour_of_varied_returns_and_ten_sessions(scene_factory):
    assert _labels(_character_scene(scene_factory, BUILDING[:11]))[1]["price.day_character"] == "needs 12 5-minute returns since 10:00, have 11"
    assert _labels(_character_scene(scene_factory, [0.0] * 12))[1]["price.day_character"] == "the 5-minute returns since 10:00 have not varied"
    scene = _character_scene(scene_factory, BUILDING)
    assert _labels(replace(scene, prior_bars=dict(list(scene.prior_bars.items())[:9])))[1]["price.day_character"] == (
        "its rank needs 10 prior sessions with an hour of 5-minute returns since 10:00 at this minute, have 9")


# ---- price.day_move

@pytest.mark.parametrize("up, sentence", [
    (3.75, "since the settled 09:35 open SPX has moved +0.05 sigma, larger than 2 of the last 10 sessions at this minute, bottom third: "
           "no real move"),
    (7.5, "since the settled 09:35 open SPX has risen 0.10 sigma, larger than 4 of the last 10 sessions at this minute, middle third: a move"),
    (34.5, "since the settled 09:35 open SPX has risen 0.46 sigma, larger than 10 of the last 10 sessions at this minute, top third: "
           "a strong move"),
    (-12.0, "since the settled 09:35 open SPX has fallen 0.16 sigma, larger than 7 of the last 10 sessions at this minute, top third: "
            "a strong move"),
])
def test_the_days_move_from_the_settled_open(scene_factory, up, sentence):
    """Each prior session's move is measured from its own 09:34 close: the 09:30 print is not the open."""
    closes = [7690.0] * 4 + [7700.0] * 56 + [7700.0 + up] * 30
    prior = lambda k: [7650.0] * 4 + _stepped(75)(k)[4:]        # a 09:30-09:33 print far off every session's settled open
    assert _labels(_ranked(_scene(scene_factory, at(11, 0, ss=5), closes), prior))[0]["price.day_move"] == sentence


def test_the_day_move_waits_for_the_0934_bar(scene_factory):
    assert _labels(_scene(scene_factory, at(9, 34, ss=30), [7700.0] * 5))[1]["price.day_move"] == \
        "no settled open yet: the 09:34 bar has not finished"


# ---- price.day_move_split

@pytest.mark.parametrize("opened, spot, sentence", [
    (7722.5, 7734.5, "SPX is 0.46 sigma above yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: "
                     "far from it; 0.30 sigma up came from the opening gap and 0.16 sigma up from trading since the open, so the gap is 65% "
                     "of the day's move, at least half: most of it came from the gap"),
    (7715.0, 7730.0, "SPX is 0.40 sigma above yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: "
                     "far from it; 0.20 sigma up came from the opening gap and 0.20 sigma up from trading since the open, so the gap is 50% "
                     "of the day's move, at least half: most of it came from the gap"),
    (7707.5, 7734.5, "SPX is 0.46 sigma above yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: "
                     "far from it; 0.10 sigma up came from the opening gap and 0.36 sigma up from trading since the open, so the gap is 22% "
                     "of the day's move, under half: most of it was built since the open"),
    (7692.5, 7730.0, "SPX is 0.40 sigma above yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: "
                     "far from it; 0.10 sigma down came from the opening gap and 0.50 sigma up from trading since the open, so the gap ran "
                     "against the day's move: all of it was built since the open"),
    (7677.5, 7670.0, "SPX is 0.40 sigma below yesterday's close, further from it than 10 of the last 10 sessions at this minute, top third: "
                     "far from it; 0.30 sigma down came from the opening gap and 0.10 sigma down from trading since the open, so the gap is "
                     "75% of the day's move, at least half: most of it came from the gap"),
    (7704.0, 7707.5, "SPX is 0.10 sigma above yesterday's close, further from it than 4 of the last 10 sessions at this minute, middle "
                     "third: away from it; 0.05 sigma up came from the opening gap and 0.05 sigma up from trading since the open, so the "
                     "gap is 53% of the day's move, at least half: most of it came from the gap"),
    (7703.0, 7705.0, "SPX is 0.07 sigma above yesterday's close, further from it than 3 of the last 10 sessions at this minute, bottom "
                     "third: near it; 0.04 sigma up came from the opening gap and 0.03 sigma up from trading since the open"),
])
def test_the_day_from_yesterdays_close_split_into_gap_and_trading(scene_factory, tmp_path, opened, spot, sentence):
    closes = [opened] * 5 + [spot] * 85
    state, _, _ = _labels(_with_diaries(_ranked(_scene(scene_factory, at(11, 0, ss=5), closes, row_over={"prior_close": 7700.0})), tmp_path))
    assert state["price.day_move_split"] == sentence


def test_the_split_waits_for_the_settled_open(scene_factory, tmp_path):
    scene = _with_diaries(_scene(scene_factory, at(9, 33, ss=30), [7700.0] * 3), tmp_path)
    assert _labels(scene)[1]["price.day_move_split"] == "no settled open yet: the 09:34 bar has not finished"


# ---- price.hour_one_way

def _hour_scene(scene_factory, window):
    """A read at 11:30 whose last 60 bars close at ``window`` after an hour flat at 7700; each prior session stepped once
    in the hour, so it crossed its own hourly average once."""
    return _ranked(_scene(scene_factory, at(11, 30, ss=5), [7700.0] * 60 + window))


@pytest.mark.parametrize("window, sentence", [
    ([7700.0 + 0.5 * (k + 1) for k in range(60)],
     "over the last 60 minutes price rose 0.40 sigma, larger than 10 of the last 10 sessions at this minute, top third: a strong move; it "
     "crossed its own hourly average 1 time, more often than 0 of the last 10 sessions at this minute, bottom third: one-way"),
    ([7700.0, 7720.0] * 30,
     "over the last 60 minutes price rose 0.27 sigma, larger than 10 of the last 10 sessions at this minute, top third: a strong move; it "
     "crossed its own hourly average 59 times, more often than 10 of the last 10 sessions at this minute, top third: two-way"),
    ([7700.0 - 0.25 * (k + 1) for k in range(60)],
     "over the last 60 minutes price fell 0.20 sigma, larger than 9 of the last 10 sessions at this minute, top third: a strong move; it "
     "crossed its own hourly average 1 time, more often than 0 of the last 10 sessions at this minute, bottom third: one-way"),
    ([7700.0 + (k % 20) * 0.5 for k in range(60)],
     "over the last 60 minutes price rose 0.13 sigma, larger than 6 of the last 10 sessions at this minute, middle third: a move; it "
     "crossed its own hourly average 5 times, more often than 10 of the last 10 sessions at this minute, top third: two-way"),
    ([7700.0] * 60,
     "over the last 60 minutes price did not move, larger than 0 of the last 10 sessions at this minute, bottom third: no real move; it "
     "crossed its own hourly average 0 times, more often than 0 of the last 10 sessions at this minute, bottom third: one-way"),
])
def test_the_hours_move_and_how_often_it_crossed_its_own_average(scene_factory, window, sentence):
    assert _labels(_hour_scene(scene_factory, window))[0]["price.hour_one_way"] == sentence


def test_an_estimated_session_sits_out_of_the_hours_move_but_not_its_crossings(scene_factory):
    """A crossing count is the day's own price, which no ruler scales: an eleventh session on an estimated ruler
    counts for the crossings, as it does for the half hour's path and the day's variance ratio, and not for the move."""
    days = [f"2026-09-{d:02d}" for d in range(17, 6, -1)]
    scene = _ranked(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 30 + [7700.0 + 0.5 * (k + 1) for k in range(60)]),
                    days=days, estimated=days[:1])
    text = _labels(scene)[0]["price.hour_one_way"]
    assert "larger than 10 of the last 10 sessions at this minute, top third: a strong move" in text
    assert text.endswith("more often than 0 of the last 11 sessions at this minute, bottom third: one-way")


def test_the_one_way_hour_needs_an_hour(scene_factory):
    assert _labels(_scene(scene_factory, at(10, 20, ss=5), [7700.0] * 50))[1]["price.hour_one_way"] == \
        "needs 40 finished bars in the last 60 minutes and one before them"


# ---- price.move_shape

@pytest.mark.parametrize("window, sentence", [
    ([7700.0] * 20 + [7712.0] * 10,
     "the last half hour rose 0.16 sigma, larger than 7 of the last 10 sessions at this minute, top third: a strong move; one 5-minute "
     "stretch covered 100% of the half hour's 5-minute travel, past the 80% burst line; its net move was 1.00 of the distance travelled, "
     "more one-way than 10 of the last "
     "10 sessions at this minute, top third"),
    ([7700.0 + 0.4 * (k + 1) for k in range(30)],
     "the last half hour rose 0.16 sigma, larger than 7 of the last 10 sessions at this minute, top third: a strong move; one 5-minute "
     "stretch covered 17% of the half hour's 5-minute travel, short of the 80% burst line; its net move was 1.00 of the distance "
     "travelled, more one-way than 10 of the "
     "last 10 sessions at this minute, top third"),
    ([7700.4, 7700.8, 7701.2, 7701.6, 7702.0] + [7702.0] * 15 + [7710.0] * 10,
     "the last half hour rose 0.13 sigma, larger than 6 of the last 10 sessions at this minute, middle third: a move; one 5-minute "
     "stretch covered 80% of the half hour's 5-minute travel, past the 80% burst line; its net move was 1.00 of the distance travelled, "
     "more one-way than 10 of the last "
     "10 sessions at this minute, top third"),
    ([7712.0 if k % 2 else 7690.0 for k in range(30)],
     "the last half hour rose 0.16 sigma, larger than 7 of the last 10 sessions at this minute, top third: a strong move; one 5-minute "
     "stretch covered 18% of the half hour's 5-minute travel, short of the 80% burst line; its net move was 0.02 of the distance "
     "travelled, more one-way than 0 of the last "
     "10 sessions at this minute, bottom third: choppy"),
    ([7700.0] * 30, "the last half hour moved +0.00 sigma, larger than 0 of the last 10 sessions at this minute, bottom third: no real move "
                    "to shape"),
])
def test_how_the_half_hours_move_was_made(scene_factory, window, sentence):
    # the zigzag's largest up stretch is under a fifth of the ground its stretches covered: choppy, never a burst of 183% of the move
    state, _, _ = _labels(_ranked(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 60 + window), _zigzag_then_ramp))
    assert state["price.move_shape"] == sentence


def test_a_path_is_judged_against_the_same_half_hour_on_the_prior_sessions(scene_factory):
    """The prior half hours' paths ran 13% to 60% of their distance travelled: one at 50% beats the six under it."""
    window = [7700.0 + 0.6 * (k + 1) for k in range(10)] + [7706.0 - 0.6 * (k + 1) for k in range(10)] + [7700.0 + 1.2 * (k + 1) for k in range(10)]
    state, _, _ = _labels(_ranked(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 60 + window), _zigzag_then_ramp))
    assert state["price.move_shape"].endswith("its net move was 0.50 of the distance travelled, more one-way than 6 of the last 10 sessions "
                                              "at this minute, middle third")


def test_the_path_is_measured_on_the_finished_bars_as_the_prior_sessions_are(scene_factory):
    """The row's spot runs past the last bar: 3 points back from its 7712 close would add travel today's path is not
    ranked on, since each prior session's half hour ends at a bar close."""
    window = [7700.0 + 0.6 * (k + 1) for k in range(10)] + [7706.0 - 0.6 * (k + 1) for k in range(10)] + [7700.0 + 1.2 * (k + 1) for k in range(10)]
    scene = _ranked(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 60 + window, spot=7709.0), _zigzag_then_ramp)
    assert _labels(scene)[0]["price.move_shape"].endswith("its net move was 0.50 of the distance travelled, more one-way than 6 of the "
                                                          "last 10 sessions at this minute, middle third")


def test_the_shape_needs_a_half_hour(scene_factory):
    assert _labels(_scene(scene_factory, at(9, 50), [7700.0] * 20))[1]["price.move_shape"] == \
        "no 30-minute move to shape: needs 30 minutes of session"


# ---- price.prior_close_push and its gate

def _push_scene(scene_factory, root, window, prior_close, morning=None):
    """A read at 14:32 whose last half hour closed at ``window``, after a day flat at 7700 (or ``morning``); each prior
    session stepped 0.02k sigma at 14:15 and sat 0.02k sigma from its own close at this minute."""
    day = (morning or []) + [7700.0] * (272 - len(morning or []))
    scene = _scene(scene_factory, at(14, 32, ss=5), day + window, row_over={"prior_close": prior_close})
    return _with_diaries(_ranked(scene, _stepped(285)), root)


RAMP_TO_7709 = [7700.0 + 0.3 * (k + 1) for k in range(30)]
NEAR = "it now sits {} sigma {} it, further from it than {} of the last 10 sessions at this minute, {}"
MOVE = "over the last 30 minutes price {} yesterday's close, larger than {} of the last 10 sessions at this minute, {}; "


@pytest.mark.parametrize("window, prior_close, sentence, gate", [
    (RAMP_TO_7709, 7712.0,
     MOVE.format("rose 0.12 sigma toward", 5, "middle third: a move") + NEAR.format("0.04", "below", 1, "bottom third: near it")
     + "; it has not crossed it in the last 30 minutes or today", None),
    (RAMP_TO_7709, 7705.0,
     MOVE.format("rose 0.12 sigma toward", 5, "middle third: a move") + NEAR.format("0.05", "above", 2, "bottom third: near it")
     + "; it crossed it in the last 30 minutes and is still through it", None),
    ([7700.0 + k for k in range(1, 21)] + [7710.0] * 10, 7715.0,
     MOVE.format("rose 0.13 sigma toward", 6, "middle third: a move") + NEAR.format("0.07", "below", 3, "bottom third: near it")
     + "; it crossed it in the last 30 minutes and is back on its starting side", None),
    (RAMP_TO_7709, 7720.25,
     MOVE.format("rose 0.12 sigma toward", 5, "middle third: a move") + NEAR.format("0.15", "below", 7, "top third: far from it")
     + "; it has not crossed it in the last 30 minutes or today",
     "price is not near yesterday's close (out of the bottom third of the same distance on the prior sessions) and has not crossed it "
     "in the last 30 minutes"),
    ([7700.0 - 0.3 * (k + 1) for k in range(30)], 7712.0,
     MOVE.format("fell 0.12 sigma away from", 5, "middle third: a move") + NEAR.format("0.28", "below", 10, "top third: far from it")
     + "; it has not crossed it in the last 30 minutes or today",
     "the last 30 minutes made no real move toward yesterday's close: none toward it, or one in the bottom third of the same half hour "
     "on the prior sessions"),
    ([7700.0 + 0.1 * (k + 1) for k in range(30)], 7710.0,
     MOVE.format("rose 0.04 sigma toward", 1, "bottom third: no real move") + NEAR.format("0.09", "below", 4, "middle third: away from it")
     + "; it has not crossed it in the last 30 minutes or today",
     "the last 30 minutes made no real move toward yesterday's close: none toward it, or one in the bottom third of the same half hour "
     "on the prior sessions"),
])
def test_the_push_toward_yesterdays_close_and_when_its_question_wakes(scene_factory, tmp_path, window, prior_close, sentence, gate):
    state, _, gates = _labels(_push_scene(scene_factory, tmp_path, window, prior_close))
    assert state["price.prior_close_push"] == sentence and gates["prior_close_push_fade"] == gate


def test_a_cross_earlier_today_is_named_apart_from_the_half_hour(scene_factory, tmp_path):
    state, _, _ = _labels(_push_scene(scene_factory, tmp_path, RAMP_TO_7709, 7712.0, morning=[7720.0] * 30))
    assert state["price.prior_close_push"].endswith("it has not crossed it in the last 30 minutes, though it did earlier today")


def test_the_push_needs_a_half_hour(scene_factory, tmp_path):
    _, omitted, gates = _labels(_with_diaries(_scene(scene_factory, at(9, 50), [7700.0] * 20), tmp_path))
    assert omitted["price.prior_close_push"] == gates["prior_close_push_fade"] == "no 30-minute move to judge: needs 30 minutes of session"


# ---- price.session_extreme_recent

def _extreme_scene(scene_factory, marks, now=None):
    """Bars at 7700 from 09:30 to 10:59 but for ``{bar index: close}``."""
    closes = [marks.get(i, 7700.0) for i in range(90)]
    return _scene(scene_factory, now or at(11, 0, ss=5), closes, wick=0.0)


def test_a_new_session_high_or_low_in_the_last_15_minutes(scene_factory):
    state, _, _ = _labels(_extreme_scene(scene_factory, {50: 7710.0, 85: 7713.75}))
    assert state["price.session_extreme_recent"] == "SPX set a new session high 4 minutes ago, 0.05 sigma above the earlier high from 10:20"
    state, _, _ = _labels(_extreme_scene(scene_factory, {50: 7690.0, 85: 7686.25, 88: 7713.75}))
    assert state["price.session_extreme_recent"] == ("SPX set a new session high 1 minute ago, 0.18 sigma above the earlier high "
                                                     "from 09:30; SPX set a new session low 4 minutes ago, 0.05 sigma below the earlier low "
                                                     "from 10:20")


def test_an_extreme_older_than_the_window_is_not_news(scene_factory):
    _, omitted, _ = _labels(_extreme_scene(scene_factory, {50: 7710.0, 74: 7713.75}, now=at(11, 0)))
    assert omitted["price.session_extreme_recent"] == "no new session high or low in the last 15 minutes"
    assert "price.session_extreme_recent" in build_price_labels(_extreme_scene(scene_factory, {50: 7710.0, 74: 7713.75}, now=at(11, 0))).ended
    state, _, _ = _labels(_extreme_scene(scene_factory, {50: 7710.0, 75: 7713.75}, now=at(11, 0)))
    assert state["price.session_extreme_recent"].startswith("SPX set a new session high 14 minutes ago")
    state, _, _ = _labels(_extreme_scene(scene_factory, {50: 7710.0, 89: 7713.75}))
    assert state["price.session_extreme_recent"].startswith("SPX set a new session high within the last minute")
    _, omitted, _ = _labels(_scene(scene_factory, at(9, 44), [7700.0] * 14))
    assert omitted["price.session_extreme_recent"] == "needs bars from before the last 15 minutes"


# ---- price.vwap_reach and its gate

def _reach_scene(scene_factory, root, vwap_below, **row):
    """A flat tape at 12:30 whose 5-minute slices span 3 points (a typical half hour of 8.19 points with the
    straddle's 16.4 left), the day's average ``vwap_below`` points under price."""
    scene = _scene(scene_factory, at(12, 30, ss=10), [7700.0] * 180, wick=1.5, row_over={"vwap": 7700.0 - vwap_below, **row})
    return _with_diaries(_ranked(scene), root)


def test_the_reach_to_the_days_average_in_typical_half_hours(scene_factory, tmp_path):
    state, _, gates = _labels(_reach_scene(scene_factory, tmp_path, 13.5))
    assert state["price.vwap_reach"] == ("the day's volume-weighted average price (VWAP) is 0.18 sigma below price, further than 8 of the last 10 "
                                         "sessions at this minute, top third: far from it, 1.6 typical 30-minute moves away")
    assert gates["average_reach_30"] is None
    state, _, gates = _labels(_reach_scene(scene_factory, tmp_path, -3.0))
    assert state["price.vwap_reach"] == ("the day's volume-weighted average price (VWAP) is 0.04 sigma above price, further than 1 of the last 10 "
                                         "sessions at this minute, bottom third: near it, 0.4 typical 30-minute moves away")
    assert gates["average_reach_30"] == ("price is near the day's volume-weighted average price (VWAP): in the bottom third of the same distance "
                                         "on the prior sessions at this minute")


def test_without_a_straddle_the_reach_is_omitted_and_its_gate_still_decided(scene_factory, tmp_path):
    _, omitted, gates = _labels(_reach_scene(scene_factory, tmp_path, 13.5, range_ruler={"em_open": 22.0}))
    assert omitted["price.vwap_reach"] == "row carries no straddle left (range_ruler.em_points)" and gates["average_reach_30"] is None


# ---- every set label: the anchor, and nothing after the row

def test_without_a_morning_ruler_the_anchored_labels_say_why_and_their_gated_questions_read_as_missing(scene_factory):
    scene = scene_factory(at(15, 2, ss=5), flat_bars(332), row_over={"sigma_live": None})
    _, omitted, gates = _labels(scene)
    assert {p: omitted[p] for p in ANCHORED} == {p: NO_ANCHOR for p in ANCHORED}
    assert {q: gates[q] for q in GATES} == dict.fromkeys(GATES)


def test_the_momentum_reads_keep_the_rows_sigma_with_or_without_a_morning_ruler(scene_factory):
    """Built before the set, they judge their own half hour on the row's sigma: a 12-point climb is 0.16 sigma
    on the row's 75 whether the day has no morning ruler or one of 200, on which it would be under the move rule."""
    closes = [7700.0] * 60 + [7700.0 + 0.4 * (k + 1) for k in range(30)]
    no_ruler = scene_factory(at(11, 0, ss=5), bars_from_closes(closes), row_over={"sigma_live": None})
    wide_ruler = scene_factory(at(11, 0, ss=5), bars_from_closes(closes), rows_before=[make_row(at(9, 31), 7700.0, sigma=200.0)])
    judged = ("momentum.closes", "momentum.pauses", "momentum.path_efficiency")
    reads = [{p: _labels(scene)[0].get(p) for p in judged} for scene in (no_ruler, wide_ruler)]
    assert reads[0] == reads[1]
    assert reads[0]["momentum.closes"] == "of the last five 1-minute bars, 5 closed in the direction of the move, which is up"


def test_an_estimated_ruler_is_named_in_the_sentence(scene_factory):
    state, _, _ = _labels(_ranked(scene_factory(at(11, 0, ss=5), flat_bars(90))))
    assert state["price.recent_move"].endswith("(ruler estimated)") and state["price.day_move"].endswith("(ruler estimated)")


def test_a_bar_that_finishes_after_the_row_never_counts(tmp_path):
    """Loaded as the service loads a read at 15:02:10: the running 15:02 bar and every later one (a spike to a new
    high, a push through yesterday's close, a swing across the day's average) are on disk, and every set
    label and gate reads as if they were not."""
    day, now = "2026-09-18", at(15, 2, ss=10)
    prior = prior_sessions(10)
    closes = [7690.0 + (i % 9) for i in range(270)] + [7700.0 + 0.2 * k for k in range(62)]
    bars = bars_from_closes(closes)
    later = bars_from_closes(closes + [7760.0, 7650.0, 7760.0] * 10)[len(closes):]
    rows = [make_row(at(9, 31), 7690.0, prior_close=7725.0, vwap=7705.0),
            make_row(now, float(bars[-1]["close"]), prior_close=7725.0, vwap=7705.0)]

    def labels_on(state_dir, day_bars):
        write_state(state_dir, day, rows, day_bars, prior)
        for k, d in enumerate(prior, start=1):
            diary = [make_row(at(9, 31, day=d), 7700.0), make_row(at(15, 1, day=d), 7700.0, prior_close=7700.0 - k, vwap=7700.0 + k)]
            (state_dir / "reversion" / f"{d}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in diary))
        ls = build_price_labels(make_scene(state_dir, day, time(15, 2, 30)))
        return {f"{g}.{k}": v for g, labels in ls.state.items() for k, v in labels.items()}, ls.omitted, ls.gates

    (seen, seen_omitted, seen_gates), (clean, clean_omitted, clean_gates) = labels_on(tmp_path / "all", bars + later), labels_on(tmp_path / "clean", bars)
    assert {p: seen.get(p) for p in ANCHORED} == {p: clean.get(p) for p in ANCHORED}
    assert seen_omitted == clean_omitted and seen_gates == clean_gates
    assert all(p in clean for p in ANCHORED if p != "price.session_extreme_recent")


# ---- yesterday is the previous trading day or nothing

def test_the_yesterday_labels_never_take_an_older_session_for_yesterday(scene_factory):
    from spx_jev.labels.range_size import build_range_size_labels
    prior = {d: b for d, b in prior_sessions().items() if d != "2026-09-17"}
    scene = _scene(scene_factory, at(11, 32), [7700.0] * 122, prior_bars=prior)
    why = "yesterday's bars are not on file: the newest stored session is 2026-09-16"
    _, omitted, _ = _labels(scene)
    assert omitted["price.vs_yesterday"] == why and omitted["price.vs_day_before"] == why
    assert build_range_size_labels(scene).omitted["range.prior_level_touches"] == why
