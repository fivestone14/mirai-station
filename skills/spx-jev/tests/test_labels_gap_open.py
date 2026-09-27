"""The gap and open family (labels/gap_open.py): each label's sentence at its verdicts and their boundaries,
its omissions, the gap_fill_next_hour gate both ways, and that a bar finishing after the read never counts.

Every scene carries a 09:31 diary row, so the morning anchor is trusted (SIGMA points, 80 so that the cuts
land on exact figures), and yesterday's close is PRIOR_CLOSE. Bars start at 09:30, one close a minute;
the fifth close is the settled open."""
from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import DAY, at, bars_from_closes, make_row, prior_sessions
from spx_jev.cuts import (GAP_LARGE_SIGMA, GAP_RULE_SIGMA, GIVEBACK_THIRD, NOISE_EDGE_SIGMA, NOISE_LOOKBACK, OPEN_CONTESTED_CROSSES,
                          OPEN_MOVE_SIGMA, RANGE_TOP_SHARE)
from spx_jev.labels.gap_open import build_gap_open_labels
from spx_jev.labels.rulers import SigmaRuler

SIGMA = 80.0
PRIOR_CLOSE = 7700.0
OPENED = "measured at 09:35 because the 09:30 print uses stale prices"


def scene(scene_factory, now, closes, spot=None, row_over=None, anchored=True, day=DAY, **kw):
    """A read at ``now`` over ``closes`` from 09:30 (bars after ``now`` are on file but unfinished)."""
    over = {"sigma": SIGMA, "prior_close": PRIOR_CLOSE, **(row_over or {})}
    early = [make_row(at(9, 31, day), closes[0], **over)] if anchored else []
    spot = spot if spot is not None else closes[min(len(closes), int((now - at(9, 30, day)).total_seconds() // 60)) - 1]
    return scene_factory(now, bars_from_closes(closes, day=day), row_over=over, rows_before=early, spot=spot, **kw)


def labels(sc):
    ls = build_gap_open_labels(sc)
    return {f"{g}.{k}": v for g, group in ls.state.items() for k, v in group.items()}, ls.omitted, ls.gates


def opening(settled: float, then: list[float]) -> list[float]:
    """Four minutes climbing to the settled open (the 09:34 close), then ``then``."""
    return [PRIOR_CLOSE + (settled - PRIOR_CLOSE) * (i + 1) / 5 for i in range(5)] + then


# ---- gap.size

@pytest.mark.parametrize("settled, verdict", [
    (7735.2, f"0.44 sigma above yesterday's close, {OPENED}; past the 0.15 sigma gap rule and past the 0.40 sigma large-gap line; "
             "1.6 times this morning's same-day straddle"),
    (7732.0, f"0.40 sigma above yesterday's close, {OPENED}; past the 0.15 sigma gap rule and past the 0.40 sigma large-gap line; "
             "1.5 times this morning's same-day straddle"),
    (7731.9, f"0.40 sigma above yesterday's close, {OPENED}; past the 0.15 sigma gap rule and under the 0.40 sigma large-gap line; "
             "1.4 times this morning's same-day straddle"),
    (7688.0, f"0.15 sigma below yesterday's close, {OPENED}; past the 0.15 sigma gap rule and under the 0.40 sigma large-gap line; "
             "0.5 times this morning's same-day straddle"),
    (7688.1, f"0.15 sigma below yesterday's close, {OPENED}; within the 0.15 sigma gap rule, so no real gap; "
             "0.5 times this morning's same-day straddle"),
])
def test_the_gap_is_the_settled_open_against_yesterdays_close_judged_against_both_lines(scene_factory, settled, verdict):
    got, _, _ = labels(scene(scene_factory, at(9, 35), opening(settled, [])))
    assert got["gap.size"] == f"price opened {verdict}"
    assert (GAP_RULE_SIGMA, GAP_LARGE_SIGMA) == (0.15, 0.40)


def test_the_gap_waits_for_the_settled_open_and_says_when_its_ruler_is_estimated(scene_factory):
    closes = opening(7735.2, [7735.0] * 30)
    _, omitted, gates = labels(scene(scene_factory, at(9, 34, ss=59), closes, spot=7735.2))     # the 09:34 bar has not finished
    assert omitted["gap.size"] == "no settled open yet: the 09:34 bar has not finished"
    assert gates["gap_fill_next_hour"] == "no gap to fill: no settled open yet: the 09:34 bar has not finished"
    _, omitted, _ = labels(scene(scene_factory, at(10, 0), closes, row_over={"prior_close": None}))
    assert omitted["gap.size"] == omitted["gap.fill_progress"] == "row carries no prior close"
    got, _, _ = labels(scene(scene_factory, at(10, 0), closes, anchored=False))              # no row by 09:40: the live sigma stands in
    assert got["gap.size"].endswith("1.6 times this morning's same-day straddle (ruler estimated)")


# ---- gap.fill_progress and the gap_fill_next_hour gate

def test_a_gap_that_keeps_the_half_line_untouched_is_still_open_and_its_fill_question_sleeps(scene_factory):
    got, _, gates = labels(scene(scene_factory, at(11, 0), opening(7735.2, [7725.0] * 90)))
    assert got["gap.fill_progress"] == (
        "the gap up is still open after 85 minutes: price sits 0.31 sigma above yesterday's close and keeps 71% of the 0.44 sigma gap, "
        "at or above the half line; it has not touched yesterday's close (within 0.02 sigma); the gap was past the 0.15 sigma gap rule")
    assert gates["gap_fill_next_hour"] == "the gap up is still open, not losing ground"


def test_the_half_line_and_the_touch_decide_whether_the_gap_is_losing_ground(scene_factory):
    now = at(11, 0)
    got, _, gates = labels(scene(scene_factory, now, opening(7732.0, [7716.0] * 90)))            # keeps exactly half
    assert "keeps 50% of the 0.40 sigma gap, at or above the half line" in got["gap.fill_progress"]
    assert got["gap.fill_progress"].startswith("the gap up is still open") and gates["gap_fill_next_hour"] == "the gap up is still open, not losing ground"
    got, _, gates = labels(scene(scene_factory, now, opening(7732.0, [7715.9] * 90)))
    assert got["gap.fill_progress"].startswith("the gap up is losing ground after 85 minutes: price sits 0.20 sigma above yesterday's "
                                               "close and keeps 50% of the 0.40 sigma gap, below the half line;")
    assert gates["gap_fill_next_hour"] is None
    # a bar low 1.6 points (0.02 sigma) above the close touches it; 1.7 does not
    touched = opening(7732.0, [7720.0] * 30 + [7702.1] + [7730.0] * 59)                        # the 10:05 bar's low is 7701.6
    got, _, gates = labels(scene(scene_factory, now, touched))
    assert got["gap.fill_progress"] == (
        "the gap up is losing ground after 85 minutes: price sits 0.38 sigma above yesterday's close and keeps 94% of the 0.40 sigma gap, "
        "at or above the half line; it first touched yesterday's close (within 0.02 sigma) 54 minutes ago; the gap was past the 0.15 "
        "sigma gap rule")
    assert gates["gap_fill_next_hour"] is None
    untouched = opening(7732.0, [7720.0] * 30 + [7702.2] + [7730.0] * 59)
    got, _, _ = labels(scene(scene_factory, now, untouched))
    assert got["gap.fill_progress"].startswith("the gap up is still open")


def test_a_gap_down_filled_through_the_close_and_a_day_with_no_real_gap(scene_factory):
    got, _, gates = labels(scene(scene_factory, at(11, 0), opening(7668.0, [7690.0] * 60 + [7712.0] * 30)))
    assert got["gap.fill_progress"] == (
        "the gap down is losing ground after 85 minutes: price sits 0.15 sigma above yesterday's close, through it, and keeps none of the "
        "0.40 sigma gap, below the half line; it first touched yesterday's close (within 0.02 sigma) 24 minutes ago; the gap was past the "
        "0.15 sigma gap rule")
    assert gates["gap_fill_next_hour"] is None
    got, omitted, gates = labels(scene(scene_factory, at(11, 0), opening(7708.0, [7712.0] * 90)))
    assert got["gap.fill_progress"] == ("there was no real gap: price opened 0.10 sigma above yesterday's close, within the 0.15 sigma gap "
                                        "rule; it now sits 0.15 sigma above yesterday's close")
    assert gates["gap_fill_next_hour"] == "no real gap: the settled open was within the 0.15 sigma gap rule"
    assert omitted["gap.morning_vs_gap"] == "no real gap this morning: the settled open was within the 0.15 sigma gap rule"


def test_a_touch_in_a_bar_that_has_not_finished_does_not_count(scene_factory):
    closes = opening(7732.0, [7720.0] * 85 + [7700.0] + [7720.0] * 10)                         # the 11:00 bar dips to the close
    got, _, _ = labels(scene(scene_factory, at(11, 0, ss=30), closes, spot=7720.0))
    assert "it has not touched yesterday's close" in got["gap.fill_progress"]
    got, _, _ = labels(scene(scene_factory, at(11, 1), closes, spot=7720.0))
    assert "it first touched yesterday's close (within 0.02 sigma) within the last minute" in got["gap.fill_progress"]


# ---- gap.morning_vs_gap

def test_the_morning_against_the_gap_from_the_settled_open_to_1130(scene_factory):
    with_gap = opening(7732.0, [7736.0] * 115 + [7730.0] * 30)                                    # 11:29 closes at 7736
    got, _, _ = labels(scene(scene_factory, at(12, 0), with_gap))
    assert got["gap.morning_vs_gap"] == (
        "from the settled open to 11:30 price rose 0.05 sigma, with this morning's 0.40 sigma gap up (past the 0.15 sigma gap rule), "
        "and did not touch yesterday's close; it now sits 0.38 sigma above it")
    faded = opening(7732.0, [7701.0] + [7716.0] * 114 + [7720.0] * 30)
    got, _, _ = labels(scene(scene_factory, at(12, 0), faded))
    assert got["gap.morning_vs_gap"] == (
        "from the settled open to 11:30 price fell 0.20 sigma, against this morning's 0.40 sigma gap up (past the 0.15 sigma gap rule), "
        "and touched yesterday's close; it now sits 0.25 sigma above it")


def test_the_morning_counts_once_its_1129_bar_has_finished(scene_factory):
    closes = opening(7732.0, [7736.0] * 145)
    _, omitted, _ = labels(scene(scene_factory, at(11, 29, ss=59), closes))
    assert omitted["gap.morning_vs_gap"] == "the morning runs to 11:30 and its last bar has not finished"
    got, _, _ = labels(scene(scene_factory, at(11, 30), closes))
    assert got["gap.morning_vs_gap"].startswith("from the settled open to 11:30 price rose 0.05 sigma, with")
    got, _, _ = labels(scene(scene_factory, at(11, 30), opening(7732.0, [7732.0] * 145)))
    assert got["gap.morning_vs_gap"].startswith("from the settled open to 11:30 price rose 0.00 sigma, neither with nor against this morning's")
    late_touch = opening(7732.0, [7736.0] * 115 + [7700.0] + [7736.0] * 29)                     # the 11:30 bar is after the morning
    got, _, _ = labels(scene(scene_factory, at(12, 0), late_touch))
    assert "and did not touch yesterday's close" in got["gap.morning_vs_gap"]


# ---- gap.reach_distance

def test_yesterdays_close_in_sigma_and_in_typical_hour_moves(scene_factory):
    # flat 1-point bars: the tape unit is floored at 0.03 sigma, 2.4 points, so an hour's tape reach is 2.4 * sqrt(12);
    # the straddle left, 16.4 points over 240 minutes, reaches 16.4 / 0.68 * sqrt(60 / 240) in an hour
    closes = opening(7724.0, [7724.0] * 160)
    got, _, _ = labels(scene(scene_factory, at(12, 0), closes))
    reach = (2.4 * 12 ** 0.5 * 16.4 / 0.68 * 0.5) ** 0.5
    assert got["gap.reach_distance"] == f"yesterday's close is 0.30 sigma below price, {24.0 / reach:.1f} typical 60-minute moves away"
    assert f"{24.0 / reach:.1f}" == "2.4"
    fomc = "2026-10-28"                                                                       # the Fed at 14:00: the tape alone
    got, _, _ = labels(scene(scene_factory, at(12, 0, fomc), closes, day=fomc))
    assert got["gap.reach_distance"] == "yesterday's close is 0.30 sigma below price, 2.9 typical 60-minute moves away"


def test_the_reach_distance_needs_the_straddle_and_a_running_tape(scene_factory):
    closes = opening(7724.0, [7724.0] * 160)
    _, omitted, _ = labels(scene(scene_factory, at(12, 0), closes, row_over={"range_ruler": {"em_open": 22.0}}))
    assert omitted["gap.reach_distance"] == "row carries no straddle left (range_ruler.em_points)"
    _, omitted, _ = labels(scene(scene_factory, at(12, 0), closes[:140], spot=7724.0))            # the last bar finished at 11:50
    assert omitted["gap.reach_distance"] == "no tape unit this read: the bars have stopped"


# ---- open.fresh_extreme

def morning_then(last30: list[float]) -> list[float]:
    """09:30-10:29 at 7705 with one push to 7710 at 09:52, then the last 30 minutes before 11:00."""
    return [7710.0 if i == 22 else 7705.0 for i in range(60)] + last30


def test_a_new_high_in_the_window_against_the_session_high_before_it(scene_factory):
    got, _, _ = labels(scene(scene_factory, at(11, 0), morning_then([7705.0 + 0.3 * i for i in range(30)])))
    assert got["open.fresh_extreme"] == (
        "in the last 30 minutes price set a new session high, 0.05 sigma above the earlier high from 09:53; it now sits 0.01 sigma "
        "below that high; price is in the top band of today's 0.12 sigma range (95% of the way up, at or past the 75% line); "
        "it is above the first-15-minute range, which it first broke at 09:53")


def test_both_new_extremes_and_the_stalled_bands(scene_factory):
    both = morning_then([7712.0] * 10 + [7700.0] * 10 + [7706.0] * 10)
    got, _, _ = labels(scene(scene_factory, at(11, 0), both))
    assert got["open.fresh_extreme"].startswith(
        "in the last 30 minutes price set both a new session high, 0.03 sigma above the earlier high from 09:53, and a new session low, "
        "0.06 sigma below the earlier low from 09:31; price is between the bands of today's 0.16 sigma range")
    # no new extreme: the day's range is 7704.5 to 7710.5, so 7709.0 is exactly the 75% line
    top = morning_then([7709.0] * 30)
    got, _, _ = labels(scene(scene_factory, at(11, 0), top))
    assert got["open.fresh_extreme"] == (
        "in the last 30 minutes price set no new session high or low; the high from 09:53 is 0.02 sigma above price and the low from "
        f"09:31 is 0.06 sigma below it; price is in the top band of today's 0.07 sigma range (75% of the way up, at or past the "
        f"{round(RANGE_TOP_SHARE * 100)}% line); it is above the first-15-minute range, which it first broke at 09:53")
    got, _, _ = labels(scene(scene_factory, at(11, 0), morning_then([7708.9] * 30)))
    assert "between the bands of today's 0.07 sigma range (73% of the way up, between the 25% and 75% lines)" in got["open.fresh_extreme"]
    got, _, _ = labels(scene(scene_factory, at(11, 0), morning_then([7706.0] * 30)))
    assert "in the bottom band of today's 0.07 sigma range (25% of the way up, at or under the 25% line)" in got["open.fresh_extreme"]


def test_the_opening_lane_looks_back_5_minutes_and_an_unfinished_bar_never_counts(scene_factory):
    closes = morning_then([7705.0] * 25 + [7707.0, 7708.0, 7709.0, 7711.0, 7715.0])            # the 10:59 bar makes the high
    got, _, _ = labels(scene(scene_factory, at(10, 59, ss=30), closes, spot=7711.0))
    assert got["open.fresh_extreme"].startswith("in the last 30 minutes price set a new session high, 0.01 sigma above")
    got, _, _ = labels(scene(scene_factory, at(10, 59), closes, spot=7711.0, bar_clock=True))
    assert got["open.fresh_extreme"].startswith("in the last 5 minutes price set a new session high, 0.01 sigma above the earlier high "
                                                "from 09:53")
    _, omitted, _ = labels(scene(scene_factory, at(9, 35), closes, bar_clock=True))
    assert omitted["open.fresh_extreme"] == "needs finished bars both before and inside the last 5 minutes"


# ---- open.noise_band

def noise_scene(scene_factory, spot_sigma_from_upper: float, sessions: int = NOISE_LOOKBACK, rulers=None):
    """Today opened at 7700 over a 7690 close; the prior sessions all moved the same share from their settled open by 11:00."""
    prior = prior_sessions(sessions)
    first = next(iter(prior.values()))
    usual = abs(first[89]["close"] / first[4]["close"] - 1.0)
    upper = 7700.0 * (1 + usual)
    spot = upper + spot_sigma_from_upper * SIGMA
    sc = scene(scene_factory, at(11, 0), [7700.0] * 90, spot=spot, row_over={"prior_close": 7690.0}, prior_bars=prior)
    return replace(sc, prior_rulers=rulers or {}), upper, 7690.0 * (1 - usual)


def test_the_noise_band_edges_and_price_against_them(scene_factory):
    how = f"(edges anchored at the higher and lower of the open and yesterday's close, {NOISE_LOOKBACK}-session average move)"
    sc, _, _ = noise_scene(scene_factory, 0.07)
    got, _, _ = labels(sc)
    assert got["open.noise_band"] == f"price is 0.07 sigma above the upper edge of the usual move-from-the-open band for 11:00, beyond the 0.05 sigma edge distance {how}"
    sc, _, _ = noise_scene(scene_factory, NOISE_EDGE_SIGMA - 0.001)
    got, _, _ = labels(sc)
    assert got["open.noise_band"] == f"price is at the upper edge of the usual move-from-the-open band for 11:00, 0.05 sigma above it, within the 0.05 sigma edge distance {how}"
    sc, upper, lower = noise_scene(scene_factory, -0.1)
    got, _, _ = labels(sc)
    assert got["open.noise_band"] == (f"price is inside the usual move-from-the-open band for 11:00, 0.10 sigma below its upper edge and "
                                      f"{(sc.spot - lower) / SIGMA:.2f} sigma above its lower edge, more than the 0.05 sigma edge distance from both {how}")
    sc, upper, lower = noise_scene(scene_factory, 0.0)
    below = replace(sc, row={**sc.row, "spot": lower - 0.2 * SIGMA})
    got, _, _ = labels(below)
    assert got["open.noise_band"].startswith("price is 0.20 sigma below the lower edge of the usual move-from-the-open band for 11:00, beyond")


def test_the_noise_band_needs_14_sessions_with_a_trusted_ruler(scene_factory):
    sc, _, _ = noise_scene(scene_factory, 0.0, sessions=NOISE_LOOKBACK - 1)
    _, omitted, _ = labels(sc)
    assert omitted["open.noise_band"] == "needs 14 prior sessions with bars at this minute, have 13"
    estimated = {"2026-09-17": SigmaRuler(75.0, "live")}
    sc, _, _ = noise_scene(scene_factory, 0.0, rulers=estimated)
    _, omitted, _ = labels(sc)
    assert omitted["open.noise_band"] == "needs 14 prior sessions with bars at this minute, have 13"
    _, omitted, _ = labels(scene(scene_factory, at(9, 35), [7700.0] * 10))
    assert omitted["open.noise_band"] == "no finished bar after the settled open yet"


# ---- open.path and open.settled_open_crosses

def test_the_opening_path_firm_fading_and_rotating(scene_factory):
    # settled open 7700; 09:35 dips to 7699, then up to 7718 by 09:54, back to 7714
    path = [7700.0] * 5 + [7699.0, 7701.0] + [7701.0 + i for i in range(1, 18)] + [7714.0] * 30
    got, _, _ = labels(scene(scene_factory, at(9, 55), path, row_over={"prior_close": 7680.0}))
    assert got["open.path"] == (
        "20 minutes after the settled open, price reached 0.23 sigma above it and 0.02 sigma below it, crossed it once, and gave back "
        "24% of its high, under the stall line; it now sits 0.17 sigma above it, beyond the 0.10 sigma opening move rule on the up side, "
        "on the gap's side")
    got, _, _ = labels(scene(scene_factory, at(9, 55), path, spot=7712.0, row_over={"prior_close": 7720.0}))   # 6.5 of 18.5 points back
    assert ("gave back 35% of its high, at or past the stall line" in got["open.path"] and GIVEBACK_THIRD == 0.33
            and got["open.path"].endswith("on the up side, against the gap's side"))
    got, _, _ = labels(scene(scene_factory, at(9, 55), path, spot=7700.0 + OPEN_MOVE_SIGMA * SIGMA, row_over={"prior_close": 7699.0}))
    assert got["open.path"] == ("20 minutes after the settled open, price reached 0.23 sigma above it and 0.02 sigma below it and crossed "
                                "it once; it now sits 0.10 sigma above it, within the 0.10 sigma opening move rule")
    down = [7700.0] * 5 + [7700.0 - i for i in range(1, 21)]
    got, _, _ = labels(scene(scene_factory, at(9, 55), down, row_over={"prior_close": 7699.0}))
    assert got["open.path"].endswith("gave back 2% of its low, under the stall line; it now sits 0.25 sigma below it, beyond the "
                                     "0.10 sigma opening move rule on the down side, with no real gap this morning")


def test_the_settled_open_crossings_and_the_contested_rule(scene_factory):
    def crossings(n: int) -> list[float]:
        return [7700.0] * 5 + [7702.0 if k % 2 == 0 else 7698.0 for k in range(n + 1)] + [7700.0 + (1 if n % 2 == 0 else -1) * 2] * 30

    got, _, _ = labels(scene(scene_factory, at(9, 56), crossings(1)))
    assert got["open.settled_open_crosses"] == ("since the settled open 21 minutes ago price has crossed it 1 time, no more than 1 cross, "
                                                "one-sided; it now sits 0.03 sigma below it")
    got, _, _ = labels(scene(scene_factory, at(9, 56), crossings(OPEN_CONTESTED_CROSSES - 1)))
    assert "crossed it 5 times, more than 1 but under the 6-cross contested rule" in got["open.settled_open_crosses"]
    got, _, _ = labels(scene(scene_factory, at(9, 56), crossings(OPEN_CONTESTED_CROSSES)))
    assert got["open.settled_open_crosses"] == ("since the settled open 21 minutes ago price has crossed it 6 times, at or past the 6-cross "
                                                "contested rule; it now sits 0.03 sigma above it")
    # a close back on the open keeps its side; a crossing bar that has not finished does not count
    level = [7700.0] * 5 + [7702.0, 7700.0, 7702.0] + [7702.0] * 18 + [7698.0] * 10              # the 09:56 bar crosses
    got, _, _ = labels(scene(scene_factory, at(9, 56), level, spot=7702.0))
    assert "crossed it 0 times, no more than 1 cross, one-sided" in got["open.settled_open_crosses"]
    got, _, _ = labels(scene(scene_factory, at(9, 57), level, spot=7698.0))
    assert "crossed it 1 time, no more than 1 cross, one-sided" in got["open.settled_open_crosses"]
    _, omitted, _ = labels(scene(scene_factory, at(9, 35), level))
    assert omitted["open.settled_open_crosses"] == omitted["open.path"] == "no finished bar after the settled open yet"


# ---- the overnight labels

def test_the_overnight_bonds_question_sleeps_while_its_feed_is_dark(scene_factory):
    _, omitted, gates = labels(scene(scene_factory, at(10, 0), opening(7735.2, [7735.0] * 30)))
    assert gates["overnight_bonds_vs_gap"].startswith("data missing: no overnight feed for /ZNZ26 or /BTCV26")
    assert not any(path.startswith("overnight.") for path in omitted)                          # the registry omits them as dark
