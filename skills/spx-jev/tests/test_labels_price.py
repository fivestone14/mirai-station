"""The price family's final-set labels and its two gates: the half hour's move, where price sits in the day and
against its average and yesterday's close, the afternoon leg, the day's character, its move from the settled
open and from yesterday's close, the hour's one-way test, the half hour's shape, the push toward yesterday's
close, a fresh session extreme and the reach to the day's average."""
from __future__ import annotations

import json
import random
import re
import statistics
from dataclasses import replace
from datetime import time, timedelta

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row, prior_sessions, write_state
from spx_jev.labels import price
from spx_jev.labels.price import ANCHORED, GATES, NO_ANCHOR, build_price_labels
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.state_builder import make_scene

SIGMA = 75.0


def _scene(scene_factory, now, closes, wick=0.5, **kw):
    """A read on bars closing at ``closes`` from 09:30, whose morning anchor is trusted (the day's first row is
    stamped 09:31, with the sigma the fixtures use)."""
    bars = bars_from_closes(closes, wick=wick)
    return scene_factory(now, bars, rows_before=[make_row(at(9, 31), closes[0])], **kw)


def _labels(scene):
    ls = build_price_labels(scene)
    return {f"{g}.{k}": v for g, labels in ls.state.items() for k, v in labels.items()}, ls.omitted, ls.gates


# ---- price.recent_move

def _move_scene(scene_factory, up, **kw):
    """A read at 11:00 whose last half hour stepped ``up`` points at its first minute and held."""
    return _scene(scene_factory, at(11, 0, ss=5), [7700.0] * 60 + [7700.0 + up] * 30, **kw)


@pytest.mark.parametrize("up, sentence", [
    (3.75, "over the last 30 minutes price stayed within 0.09 sigma of where it was, moving +0.05 sigma"),
    (6.75, "over the last 30 minutes price stayed within 0.09 sigma of where it was, moving +0.09 sigma"),
    (10.5, "over the last 30 minutes price rose 0.14 sigma, more than the 0.09 sigma move rule and short of the 0.20 sigma strong line"),
    (15.0, "over the last 30 minutes price rose 0.20 sigma, more than the 0.09 sigma move rule and short of the 0.20 sigma strong line"),
    (22.5, "over the last 30 minutes price rose 0.30 sigma, past the 0.20 sigma strong line"),
    (-10.5, "over the last 30 minutes price fell 0.14 sigma, more than the 0.09 sigma move rule and short of the 0.20 sigma strong line"),
])
def test_the_half_hours_move_against_the_move_rule_and_the_strong_line(scene_factory, up, sentence):
    state, _, _ = _labels(_move_scene(scene_factory, up))
    where = "top" if up > 0 else "bottom"
    assert state["price.recent_move"] == f"{sentence}; the half hour closed in the {where} fifth of its own range"


def test_the_move_is_ranked_against_the_same_half_hour_and_carries_its_figure(scene_factory):
    prior = prior_sessions()
    scene = replace(_move_scene(scene_factory, 45.0, prior_bars=prior), prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in prior})
    ls = build_price_labels(scene)
    assert ls.state["price"]["recent_move"] == ("over the last 30 minutes price rose 0.60 sigma, past the 0.20 sigma strong line; bigger than "
                                                "10 of the last 10 sessions in this half hour; the half hour closed in the top fifth of its own range")
    assert ls.figures["price.recent_move"] == {"kind": "signed", "value": 0.6, "band": 0.09, "strong": 0.2, "unit": "sigma", "verdict": "rising"}
    state, _, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 90))
    assert state["price.recent_move"].endswith("moving +0.00 sigma; the half hour closed between the top and bottom fifths of its own range")


def test_the_move_needs_half_an_hour_and_a_bar_from_its_start(scene_factory):
    _, omitted, _ = _labels(_scene(scene_factory, at(9, 50), [7700.0] * 20))
    assert omitted["price.recent_move"] == "needs 30 minutes of session"
    late = [b for b in bars_from_closes([7700.0] * 90) if b["ts"] >= at(10, 31).isoformat()]
    scene = scene_factory(at(11, 0, ss=5), late, rows_before=[make_row(at(9, 31), 7700.0)])
    assert _labels(scene)[1]["price.recent_move"] == "no finished bar 30 minutes ago"


def test_a_stopped_tape_leaves_the_half_hour_unmeasured(scene_factory):
    """Live, spot is the row's while the bars can stop: bars to 14:30 and a read at 15:02 would otherwise give a
    62-minute move as the half hour's, and wake prior_close_push_fade on it."""
    scene = scene_factory(at(15, 2, ss=5), bars_from_closes([7700.0] * 300), rows_before=[make_row(at(9, 31), 7700.0)], spot=7712.0,
                          row_over={"prior_close": 7713.0})
    state, omitted, gates = _labels(scene)
    stopped = "the bars have stopped: no bar finished in the minute before the last 30 minutes"
    assert omitted["price.recent_move"] == stopped
    assert omitted["price.move_shape"] == f"no 30-minute move to shape: {stopped}"
    assert omitted["price.prior_close_push"] == gates["prior_close_push_fade"] == f"no 30-minute move to judge: {stopped}"
    assert state["price.afternoon_leg"].startswith("since 14:00 price has risen") and "last 30 minutes" not in state["price.afternoon_leg"]


def test_the_move_needs_20_bars_in_its_half_hour(scene_factory):
    bars = [b for b in bars_from_closes([7700.0] * 90) if not at(10, 35).isoformat() <= b["ts"] < at(10, 46).isoformat()]
    scene = scene_factory(at(11, 0, ss=5), bars, rows_before=[make_row(at(9, 31), 7700.0)])
    assert _labels(scene)[1]["price.recent_move"] == "needs 20 finished bars in the last 30 minutes, have 19"


# ---- price.day_range_position

@pytest.mark.parametrize("spot, sentence", [
    (7750.0, "price is in the top band of today's range so far (past the 75% line), 75% of the way up"),
    (7790.0, "price is in the top band of today's range so far (past the 75% line), 95% of the way up"),
    (7700.0, "price is between the bands of today's range so far (between the 25% and 75% lines), 50% of the way up"),
    (7650.0, "price is in the bottom band of today's range so far (below the 25% line), 25% of the way up"),
])
def test_where_price_sits_in_the_days_range(scene_factory, spot, sentence):
    state, _, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), [7700.0, 7800.0, 7600.0] + [7700.0] * 87, wick=0.0, spot=spot))
    assert state["price.day_range_position"] == sentence


def test_the_range_position_needs_a_range(scene_factory):
    assert _labels(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 90, wick=0.0))[1]["price.day_range_position"] == "today's range is zero so far"
    scene = scene_factory(at(9, 30, ss=30), [], rows_before=[make_row(at(9, 30, ss=10), 7700.0)])
    assert _labels(scene)[1]["price.day_range_position"] == "no finished bars yet"


# ---- price.vs_vwap

def _vwap_scene(scene_factory, vwap):
    """A read at 11:00 that stepped from 7700 to 7715 at 10:30 (the 10:30 bar's low still reaches 7699.5)."""
    return _move_scene(scene_factory, 15.0, row_over={"vwap": vwap})


@pytest.mark.parametrize("vwap, sentence", [
    (7700.0, "price is 0.20 sigma above the day's volume-weighted average price, more than the 0.09 sigma move rule, short of the 0.25 "
             "sigma stretch line; it last traded at the average 29 minutes ago"),
    (7696.25, "price is 0.25 sigma above the day's volume-weighted average price, more than the 0.09 sigma move rule, past the 0.25 "
              "sigma stretch line; it has not traded at the average today"),
    (7708.25, "price is 0.09 sigma above the day's volume-weighted average price, more than the 0.09 sigma move rule, short of the 0.25 "
              "sigma stretch line; it last traded at the average 29 minutes ago"),
    (7712.0, "price is within 0.09 sigma of the day's volume-weighted average price, +0.04 sigma from it; it last traded at the average "
             "29 minutes ago"),
    (7730.0, "price is 0.20 sigma below the day's volume-weighted average price, more than the 0.09 sigma move rule, short of the 0.25 "
             "sigma stretch line; it has not traded at the average today"),
])
def test_price_against_the_days_average(scene_factory, vwap, sentence):
    state, _, _ = _labels(_vwap_scene(scene_factory, vwap))
    assert state["price.vs_vwap"] == sentence


def test_no_vwap_on_the_row_omits_the_average_labels_and_sleeps_the_reach_question(scene_factory):
    _, omitted, gates = _labels(_vwap_scene(scene_factory, None))
    assert omitted["price.vs_vwap"] == omitted["price.vwap_reach"] == gates["average_reach_30"] == "row carries no vwap"


# ---- price.vs_prior_close

@pytest.mark.parametrize("prior_close, sentence", [
    (7680.0, "price is 0.47 sigma above yesterday's close, more than the 0.20 sigma day-side rule"),
    (7700.0, "price is 0.20 sigma above yesterday's close, within the 0.20 sigma day-side rule"),
    (7720.0, "price is 0.07 sigma below yesterday's close, within the 0.20 sigma day-side rule"),
    (7745.0, "price is 0.40 sigma below yesterday's close, more than the 0.20 sigma day-side rule"),
])
def test_price_against_yesterdays_close(scene_factory, prior_close, sentence):
    state, _, _ = _labels(_move_scene(scene_factory, 15.0, row_over={"prior_close": prior_close}))
    assert state["price.vs_prior_close"] == sentence


def test_no_prior_close_omits_every_label_that_reads_it(scene_factory):
    _, omitted, gates = _labels(_move_scene(scene_factory, 15.0, row_over={"prior_close": None}))
    assert {p: omitted[p] for p in ("price.vs_prior_close", "price.day_move_split", "price.prior_close_push")} == {
        p: "row carries no prior close" for p in ("price.vs_prior_close", "price.day_move_split", "price.prior_close_push")}
    assert gates["prior_close_push_fade"] == "row carries no prior close"


# ---- price.afternoon_leg

def _afternoon_scene(scene_factory, leg, now=None, **kw):
    """Flat at 7700 through the 13:59 bar, then ``leg`` points higher from 14:00 on."""
    now = now or at(15, 2, ss=5)
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    return _scene(scene_factory, now, ([7700.0] * 270 + [7700.0 + leg] * 200)[:minutes], **kw)


@pytest.mark.parametrize("leg, lead, where", [
    (15.0, "since 14:00 price has risen 0.20 sigma, more than the 0.08 sigma afternoon-leg rule", "97%"),
    (6.0, "since 14:00 price has stayed within the 0.08 sigma afternoon-leg rule, moving +0.08 sigma", "93%"),
    (-10.0, "since 14:00 price has fallen 0.13 sigma, more than the 0.08 sigma afternoon-leg rule", "5%"),
])
def test_the_afternoon_leg_from_1400_with_the_day_around_it(scene_factory, leg, lead, where):
    state, _, _ = _labels(_afternoon_scene(scene_factory, leg, row_over={"prior_close": 7700.0 + leg - 15.0}))
    assert state["price.afternoon_leg"] == (f"{lead}; the day is 0.20 sigma above yesterday's close and price sits at {where} of today's "
                                            f"range; the last 30 minutes did not move")


def test_the_afternoon_leg_waits_for_the_1359_bar_to_finish(scene_factory):
    _, omitted, _ = _labels(_afternoon_scene(scene_factory, 15.0, now=at(13, 59, ss=59)))
    assert omitted["price.afternoon_leg"] == "no finished 13:59 bar: the afternoon leg starts at 14:00"
    state, _, _ = _labels(_afternoon_scene(scene_factory, 15.0, now=at(14, 0, ss=5), row_over={"prior_close": None}))
    assert state["price.afternoon_leg"] == ("since 14:00 price has stayed within the 0.08 sigma afternoon-leg rule, moving +0.00 sigma; price "
                                            "sits at 50% of today's range; the last 30 minutes did not move")


# ---- price.day_character

BUILDING = ([2.0] * 6 + [-2.0] * 6) * 2
CANCELLING = [2.0, -2.0] * 12
MIXED = [-1.0, -1.0, 2.0, -2.0, -2.0, 1.0, 2.0, -2.0, -2.0, -1.0, -1.0, 1.0, -2.0, -2.0, 2.0, -2.0, -2.0, 2.0, 1.0, -1.0, 1.0, 2.0, 2.0, -1.0]


def _character_scene(scene_factory, steps):
    """Flat to 10:00, then one 5-minute return per step, each walked in a straight line over its five minutes."""
    closes = [7700.0] * 30
    for r in steps:
        closes += [closes[-1] + r * (k + 1) / 5 for k in range(5)]
    return _scene(scene_factory, at(10, 0) + timedelta(minutes=5 * len(steps), seconds=5), closes)


@pytest.mark.parametrize("steps, sentence", [
    (BUILDING, "so far today, 30-minute stretches have been 2.96 times larger than their 5-minute pieces would suggest, above the 1.10 "
               "building line"),
    (MIXED, "so far today, 30-minute stretches have been 1.00 times what their 5-minute pieces would suggest, between the 0.90 "
            "cancelling line and the 1.10 building line"),
    (CANCELLING, "so far today, 30-minute stretches have been 0.00 times as large as their 5-minute pieces would suggest, below the 0.90 "
                 "cancelling line"),
])
def test_the_days_character_from_its_5_minute_and_30_minute_returns(scene_factory, steps, sentence):
    assert _labels(_character_scene(scene_factory, steps))[0]["price.day_character"] == sentence


def test_the_character_lines_belong_to_mixed(scene_factory, monkeypatch):
    scene = _character_scene(scene_factory, MIXED)
    monkeypatch.setattr(price, "VR_TREND", 1.0040799673602612)
    assert "between the 0.90 cancelling line and the 1.00 building line" in _labels(scene)[0]["price.day_character"]
    monkeypatch.setattr(price, "VR_PIN", 1.0040799673602612)
    assert "between the 1.00 cancelling line" in _labels(scene)[0]["price.day_character"]


@pytest.mark.parametrize("n_steps", [12, 18, 30])
def test_a_random_walk_reads_one_on_average_at_any_hour(scene_factory, n_steps):
    """Without the small-sample correction a random walk read about 0.4 at 11:00 and under the cancelling line
    on most days, so the label answered the clock rather than the day."""
    rnd = random.Random(n_steps)
    ratios = []
    for _ in range(300):
        text = _labels(_character_scene(scene_factory, [rnd.gauss(0.0, 2.0) for _ in range(n_steps)]))[0]["price.day_character"]
        ratios.append(float(re.search(r"have been ([\d.]+) times", text).group(1)))
    assert statistics.fmean(ratios) == pytest.approx(1.0, abs=0.1)


def test_the_character_needs_an_hour_of_varied_returns(scene_factory):
    assert _labels(_character_scene(scene_factory, BUILDING[:11]))[1]["price.day_character"] == "needs 12 5-minute returns since 10:00, have 11"
    assert _labels(_character_scene(scene_factory, [0.0] * 12))[1]["price.day_character"] == "the 5-minute returns since 10:00 have not varied"


# ---- price.day_move

@pytest.mark.parametrize("up, sentence", [
    (3.75, "since the settled 09:35 open SPX has moved +0.05 sigma, within the 0.09 sigma move rule and the 0.10 sigma open-day line"),
    (6.75, "since the settled 09:35 open SPX has moved +0.09 sigma, within the 0.09 sigma move rule and the 0.10 sigma open-day line"),
    (7.5, "since the settled 09:35 open SPX has risen 0.10 sigma, more than the 0.09 sigma move rule and within the 0.10 sigma open-day line"),
    (34.5, "since the settled 09:35 open SPX has risen 0.46 sigma, more than the 0.09 sigma move rule and past the 0.10 sigma open-day line"),
    (-12.0, "since the settled 09:35 open SPX has fallen 0.16 sigma, more than the 0.09 sigma move rule and past the 0.10 sigma open-day line"),
])
def test_the_days_move_from_the_settled_open(scene_factory, up, sentence):
    closes = [7690.0] * 4 + [7700.0] * 56 + [7700.0 + up] * 30       # the 09:34 bar closes at 7700; the 09:30 print is not the open
    assert _labels(_scene(scene_factory, at(11, 0, ss=5), closes))[0]["price.day_move"] == sentence


def test_the_day_move_waits_for_the_0934_bar(scene_factory):
    assert _labels(_scene(scene_factory, at(9, 34, ss=30), [7700.0] * 5))[1]["price.day_move"] == \
        "no settled open yet: the 09:34 bar has not finished"


# ---- price.day_move_split

@pytest.mark.parametrize("opened, spot, sentence", [
    (7722.5, 7734.5, "SPX is 0.46 sigma above yesterday's close, more than the 0.20 sigma day-side rule: 0.30 sigma up came from the "
                     "opening gap and 0.16 sigma up from trading since the open, so the gap is 65% of the day's move, at least half: most "
                     "of it came from the gap"),
    (7715.0, 7730.0, "SPX is 0.40 sigma above yesterday's close, more than the 0.20 sigma day-side rule: 0.20 sigma up came from the "
                     "opening gap and 0.20 sigma up from trading since the open, so the gap is 50% of the day's move, at least half: most "
                     "of it came from the gap"),
    (7707.5, 7734.5, "SPX is 0.46 sigma above yesterday's close, more than the 0.20 sigma day-side rule: 0.10 sigma up came from the "
                     "opening gap and 0.36 sigma up from trading since the open, so the gap is 22% of the day's move, under half: most of "
                     "it was built since the open"),
    (7692.5, 7730.0, "SPX is 0.40 sigma above yesterday's close, more than the 0.20 sigma day-side rule: 0.10 sigma down came from the "
                     "opening gap and 0.50 sigma up from trading since the open, so the gap ran against the day's move: all of it was "
                     "built since the open"),
    (7677.5, 7670.0, "SPX is 0.40 sigma below yesterday's close, more than the 0.20 sigma day-side rule: 0.30 sigma down came from the "
                     "opening gap and 0.10 sigma down from trading since the open, so the gap is 75% of the day's move, at least half: "
                     "most of it came from the gap"),
    (7710.0, 7715.0, "SPX is 0.20 sigma above yesterday's close, within the 0.20 sigma day-side rule: 0.13 sigma up came from the opening "
                     "gap and 0.07 sigma up from trading since the open"),
])
def test_the_day_from_yesterdays_close_split_into_gap_and_trading(scene_factory, opened, spot, sentence):
    closes = [opened] * 5 + [spot] * 85
    state, _, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), closes, row_over={"prior_close": 7700.0}))
    assert state["price.day_move_split"] == sentence


def test_the_split_waits_for_the_settled_open(scene_factory):
    assert _labels(_scene(scene_factory, at(9, 33, ss=30), [7700.0] * 3))[1]["price.day_move_split"] == \
        "no settled open yet: the 09:34 bar has not finished"


# ---- price.hour_one_way

def _hour_scene(scene_factory, window):
    """A read at 11:30 whose last 60 bars close at ``window`` after an hour flat at 7700."""
    return _scene(scene_factory, at(11, 30, ss=5), [7700.0] * 60 + window)


@pytest.mark.parametrize("window, sentence", [
    ([7700.0 + 0.5 * (k + 1) for k in range(60)],
     "over the last 60 minutes price rose 0.40 sigma and crossed its own hourly average 1 time, fewer than the 3-cross one-way rule, "
     "past the 0.20 sigma one-way size"),
    ([7700.0, 7720.0] * 30,
     "over the last 60 minutes price rose 0.27 sigma and crossed its own hourly average 59 times, at or past the 3-cross one-way rule, "
     "past the 0.20 sigma one-way size"),
    (([7700.0] * 15 + [7716.0] * 15) * 2,
     "over the last 60 minutes price rose 0.21 sigma and crossed its own hourly average 3 times, at or past the 3-cross one-way rule, "
     "past the 0.20 sigma one-way size"),
    ([7700.0 - 0.25 * (k + 1) for k in range(60)],
     "over the last 60 minutes price fell 0.20 sigma and crossed its own hourly average 1 time, fewer than the 3-cross one-way rule, "
     "past the 0.20 sigma one-way size"),
    ([7700.0 + (k % 20) * 0.5 for k in range(60)],
     "over the last 60 minutes price rose 0.13 sigma and crossed its own hourly average 5 times, at or past the 3-cross one-way rule, "
     "short of the 0.20 sigma one-way size"),
])
def test_the_hours_move_and_how_often_it_crossed_its_own_average(scene_factory, window, sentence):
    assert _labels(_hour_scene(scene_factory, window))[0]["price.hour_one_way"] == sentence


def test_the_one_way_hour_needs_an_hour(scene_factory):
    assert _labels(_scene(scene_factory, at(10, 20, ss=5), [7700.0] * 50))[1]["price.hour_one_way"] == \
        "needs 40 finished bars in the last 60 minutes and one before them"


# ---- price.move_shape

@pytest.mark.parametrize("window, sentence", [
    ([7700.0] * 20 + [7712.0] * 10,
     "the last half hour rose 0.16 sigma; one 5-minute stretch made 100% of it, past the 80% burst line; its net move was 1.00 of the "
     "distance travelled, above the 0.30 choppy line"),
    ([7700.0 + 0.4 * (k + 1) for k in range(30)],
     "the last half hour rose 0.16 sigma; one 5-minute stretch made 17% of it, short of the 80% burst line; its net move was 1.00 of the "
     "distance travelled, above the 0.30 choppy line"),
    ([7700.4, 7700.8, 7701.2, 7701.6, 7702.0] + [7702.0] * 15 + [7710.0] * 10,
     "the last half hour rose 0.13 sigma; one 5-minute stretch made 80% of it, past the 80% burst line; its net move was 1.00 of the "
     "distance travelled, above the 0.30 choppy line"),
    ([7712.0 if k % 2 else 7690.0 for k in range(30)],
     "the last half hour rose 0.16 sigma; one 5-minute stretch made 183% of it, past the 80% burst line; its net move was 0.02 of the "
     "distance travelled, under the 0.30 choppy line"),
    ([7700.0] * 30, "the last half hour moved +0.00 sigma, within the 0.09 sigma move rule: no move to shape"),
])
def test_how_the_half_hours_move_was_made(scene_factory, window, sentence):
    state, _, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), [7700.0] * 60 + window))
    assert state["price.move_shape"] == sentence


def test_the_shape_needs_a_half_hour(scene_factory):
    assert _labels(_scene(scene_factory, at(9, 50), [7700.0] * 20))[1]["price.move_shape"] == \
        "no 30-minute move to shape: needs 30 minutes of session"


# ---- price.prior_close_push and its gate

def _push_scene(scene_factory, window, prior_close, morning=None):
    """A read at 14:32 whose last half hour closed at ``window``, after a day flat at 7700 (or ``morning``)."""
    day = (morning or []) + [7700.0] * (272 - len(morning or []))
    return _scene(scene_factory, at(14, 32, ss=5), day + window, row_over={"prior_close": prior_close})


RAMP_TO_7709 = [7700.0 + 0.3 * (k + 1) for k in range(30)]


@pytest.mark.parametrize("window, prior_close, sentence, gate", [
    (RAMP_TO_7709, 7712.0,
     "over the last 30 minutes price rose 0.12 sigma toward yesterday's close, past the 0.09 sigma move rule; it now sits 0.04 sigma below "
     "it, inside the 0.15 sigma push distance; it has not crossed it in the last 30 minutes or today", None),
    (RAMP_TO_7709, 7720.25,
     "over the last 30 minutes price rose 0.12 sigma toward yesterday's close, past the 0.09 sigma move rule; it now sits 0.15 sigma below "
     "it, inside the 0.15 sigma push distance; it has not crossed it in the last 30 minutes or today", None),
    (RAMP_TO_7709, 7705.0,
     "over the last 30 minutes price rose 0.12 sigma toward yesterday's close, past the 0.09 sigma move rule; it now sits 0.05 sigma above "
     "it, inside the 0.15 sigma push distance; it crossed it in the last 30 minutes and is still through it", None),
    ([7700.0 + k for k in range(1, 21)] + [7710.0] * 10, 7715.0,
     "over the last 30 minutes price rose 0.13 sigma toward yesterday's close, past the 0.09 sigma move rule; it now sits 0.07 sigma below "
     "it, inside the 0.15 sigma push distance; it crossed it in the last 30 minutes and is back on its starting side", None),
    (RAMP_TO_7709, 7730.0,
     "over the last 30 minutes price rose 0.12 sigma toward yesterday's close, past the 0.09 sigma move rule; it now sits 0.28 sigma below "
     "it, beyond the 0.15 sigma push distance; it has not crossed it in the last 30 minutes or today",
     "price is beyond 0.15 sigma of yesterday's close and has not crossed it in the last 30 minutes"),
    ([7700.0 - 0.3 * (k + 1) for k in range(30)], 7712.0,
     "over the last 30 minutes price fell 0.12 sigma away from yesterday's close, past the 0.09 sigma move rule; it now sits 0.28 sigma "
     "below it, beyond the 0.15 sigma push distance; it has not crossed it in the last 30 minutes or today",
     "the last 30 minutes did not move toward yesterday's close past the 0.09 sigma move rule"),
    ([7700.0 + 0.225 * (k + 1) for k in range(30)], 7710.0,
     "over the last 30 minutes price rose 0.09 sigma toward yesterday's close, within the 0.09 sigma move rule; it now sits 0.04 sigma "
     "below it, inside the 0.15 sigma push distance; it has not crossed it in the last 30 minutes or today",
     "the last 30 minutes did not move toward yesterday's close past the 0.09 sigma move rule"),
])
def test_the_push_toward_yesterdays_close_and_when_its_question_wakes(scene_factory, window, prior_close, sentence, gate):
    state, _, gates = _labels(_push_scene(scene_factory, window, prior_close))
    assert state["price.prior_close_push"] == sentence and gates["prior_close_push_fade"] == gate


def test_a_cross_earlier_today_is_named_apart_from_the_half_hour(scene_factory):
    state, _, _ = _labels(_push_scene(scene_factory, RAMP_TO_7709, 7712.0, morning=[7720.0] * 30))
    assert state["price.prior_close_push"].endswith("it has not crossed it in the last 30 minutes, though it did earlier today")


def test_the_push_needs_a_half_hour(scene_factory):
    _, omitted, gates = _labels(_scene(scene_factory, at(9, 50), [7700.0] * 20))
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
    state, _, _ = _labels(_extreme_scene(scene_factory, {50: 7710.0, 75: 7713.75}, now=at(11, 0)))
    assert state["price.session_extreme_recent"].startswith("SPX set a new session high 14 minutes ago")
    state, _, _ = _labels(_extreme_scene(scene_factory, {50: 7710.0, 89: 7713.75}))
    assert state["price.session_extreme_recent"].startswith("SPX set a new session high within the last minute")
    _, omitted, _ = _labels(_scene(scene_factory, at(9, 44), [7700.0] * 14))
    assert omitted["price.session_extreme_recent"] == "needs bars from before the last 15 minutes"


# ---- price.vwap_reach and its gate

def _reach_scene(scene_factory, vwap_below, **row):
    """A flat tape at 12:30 whose 5-minute slices span 3 points (a typical half hour of 8.19 points with the
    straddle's 16.4 left), the day's average ``vwap_below`` points under price."""
    return _scene(scene_factory, at(12, 30, ss=10), [7700.0] * 180, wick=1.5, row_over={"vwap": 7700.0 - vwap_below, **row})


def test_the_reach_to_the_days_average_in_typical_half_hours(scene_factory):
    state, _, gates = _labels(_reach_scene(scene_factory, 13.5))
    assert state["price.vwap_reach"] == ("the day's volume-weighted average price is 0.18 sigma below price, past the 0.09 sigma move rule, "
                                         "1.6 typical 30-minute moves away")
    assert gates["average_reach_30"] is None
    state, _, gates = _labels(_reach_scene(scene_factory, -6.75))
    assert state["price.vwap_reach"] == ("the day's volume-weighted average price is 0.09 sigma above price, within the 0.09 sigma move rule, "
                                         "0.8 typical 30-minute moves away")
    assert gates["average_reach_30"] == "price is within 0.09 sigma of the day's volume-weighted average price"


def test_without_a_straddle_the_reach_is_omitted_and_its_gate_still_decided(scene_factory):
    _, omitted, gates = _labels(_reach_scene(scene_factory, 13.5, range_ruler={"em_open": 22.0}))
    assert omitted["price.vwap_reach"] == "row carries no straddle left (range_ruler.em_points)" and gates["average_reach_30"] is None


# ---- every set label: the anchor, and nothing after the row

def test_without_a_morning_ruler_the_anchored_labels_say_why_and_the_gates_sleep(scene_factory):
    scene = scene_factory(at(15, 2, ss=5), flat_bars(332), row_over={"sigma_live": None})
    _, omitted, gates = _labels(scene)
    assert {p: omitted[p] for p in ANCHORED} == {p: NO_ANCHOR for p in ANCHORED}
    assert {q: gates[q] for q in GATES} == {q: NO_ANCHOR for q in GATES}


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
    state, _, _ = _labels(scene_factory(at(11, 0, ss=5), flat_bars(90)))
    assert state["price.recent_move"].endswith("(ruler estimated)") and state["price.day_move"].endswith("(ruler estimated)")


def test_a_bar_that_finishes_after_the_row_never_counts(tmp_path):
    """Loaded as the service loads a read at 15:02:10: the running 15:02 bar and every later one (a spike to a new
    high, a push through yesterday's close, a swing across the day's average) are on disk, and every set
    label and gate reads as if they were not."""
    day, now = "2026-09-18", at(15, 2, ss=10)
    prior = {d: bars for d, bars in prior_sessions(6).items()}
    closes = [7690.0 + (i % 9) for i in range(270)] + [7700.0 + 0.2 * k for k in range(62)]
    bars = bars_from_closes(closes)
    later = bars_from_closes(closes + [7760.0, 7650.0, 7760.0] * 10)[len(closes):]
    rows = [make_row(at(9, 31), 7690.0, prior_close=7725.0, vwap=7705.0),
            make_row(now, float(bars[-1]["close"]), prior_close=7725.0, vwap=7705.0)]

    def labels_on(state_dir, day_bars):
        write_state(state_dir, day, rows, day_bars, prior)
        for d in prior:
            (state_dir / "reversion" / f"{d}.jsonl").write_text(json.dumps(make_row(at(9, 31, day=d), 7700.0)) + "\n")
        ls = build_price_labels(make_scene(state_dir, day, time(15, 2, 30)))
        return {f"{g}.{k}": v for g, labels in ls.state.items() for k, v in labels.items()}, ls.omitted, ls.gates

    (seen, seen_omitted, seen_gates), (clean, clean_omitted, clean_gates) = labels_on(tmp_path / "all", bars + later), labels_on(tmp_path / "clean", bars)
    assert {p: seen.get(p) for p in ANCHORED} == {p: clean.get(p) for p in ANCHORED}
    assert seen_omitted == clean_omitted and seen_gates == clean_gates
    assert all(p in clean for p in ANCHORED if p != "price.session_extreme_recent")
