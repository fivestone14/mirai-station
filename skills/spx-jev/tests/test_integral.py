"""The integral grade: a call is graded on the average price over its window against a narrowed flat band, its
direction decides, an unsure call is passed, and the guards keep a hole, a bad tick or a stale spot from grading it."""
from __future__ import annotations

import math
from datetime import timedelta

import pytest

from conftest import at, bars_from_closes
from spx_jev.integral import NOT_GRADED, factor, grade_window, label, margin, verdict

SCENARIO_DAY = "2026-09-28"
# The owner's nine scenarios (real minute closes): read minute, spot at the read, flat band f in points, the
# call, and the closes from the read minute to the mark bar. Box 30 is the live sum, box 10 the opening lane's.
SCENARIOS = {
    "S1": ("13:01", 7698.56, 5.39, "flat", [7698.65, 7696.9, 7697.11, 7697.58, 7696.58, 7696.81, 7695.13, 7695.07, 7695.88, 7695.54, 7716.98, 7714.94, 7717.39, 7717.53, 7713.67, 7713.88, 7709.61, 7705.93, 7703.05, 7701.31, 7701.79, 7702.25, 7701.43, 7703.13, 7703.46, 7699.04, 7698.39, 7699.99, 7699.67, 7701.74]),  # spiked up, pulled back, ended flat
    "S2": ("10:00", 7671.56, 5.39, None, [7669.86, 7666.54, 7666.34, 7668.75, 7668.95, 7669.86, 7669.56, 7670.76, 7671.97, 7671.26, 7670.18, 7667.15, 7668.15, 7669.61, 7669.33, 7666.95, 7663.23, 7663.23, 7661.02, 7661.07, 7659.97, 7659.32, 7659.72, 7658.51, 7655.7, 7661.52, 7661.67, 7664.84, 7669.18, 7670.36]),  # dropped, recovered, ended flat
    "S3": ("13:30", 7701.74, 5.39, "flat", [7701.74, 7703.18, 7702.52, 7699.98, 7700.85, 7702.46, 7701.97, 7698.13, 7700.51, 7700.96, 7700.94, 7702.34, 7700.94, 7701.19, 7701.04, 7700.61, 7701.49, 7700.88, 7702.99, 7702.9, 7702.06, 7702.3, 7702.41, 7702.23, 7703.51, 7702.49, 7701.94, 7701.74, 7701.78, 7702.36]),  # chopped around the read
    "S4": ("09:55", 7711.37, 3.24, "unsure", [7708.53, 7705.4, 7709.2, 7711.07, 7712.36, 7711.93, 7711.04, 7711.52, 7710.02, 7706.28]),  # chop that ended just past an edge
    "S5": ("14:00", 7699.98, 5.39, None, [7698.37, 7694.35, 7690.94, 7688.78, 7687.53, 7689.85, 7694.96, 7695.16, 7698.07, 7698.27, 7697.47, 7699.27, 7697.47, 7698.67, 7700.83, 7700.68, 7703.19, 7703.89, 7702.79, 7703.29, 7699.27, 7701.08, 7700.68, 7702.89, 7702.59, 7706.2, 7709.01, 7707.61, 7707.81, 7706.8]),  # fell hard, ended up
    "S6": ("12:30", 7717.32, 5.39, "up", [7713.15, 7712.47, 7706.36, 7707.42, 7707.88, 7708.62, 7710.9, 7710.19, 7704.36, 7704.15, 7702.7, 7699.84, 7697.56, 7698.43, 7699.54, 7699.97, 7700.9, 7699.44, 7696.91, 7697.73, 7698.11, 7697.73, 7698.92, 7700.2, 7701.25, 7701.42, 7701.63, 7701.45, 7701.03, 7701.24]),  # clean one-way move
    "S7": ("10:05", 7706.28, 3.24, None, [7704.27, 7706.7, 7707.44, 7707.37, 7706.72, 7706.27, 7703.51, 7704.38, 7704.15, 7701.61]),  # flat until the last minute
    "S8": ("09:35", 7706.39, 3.88, "unsure", [7700.54, 7700.68, 7701.75, 7702.21, 7703.1, 7702.18, 7698.96, 7703.97, 7705.29, 7704.94]),  # the call said unsure
    "S9": ("10:30", 7703.59, 2.56, "down_small", [7702.66, 7700.86, 7701.56, 7700.65, 7700.67, 7699.33, 7700.32, 7697.34, 7693.9, 7693.44]),  # right direction, wrong size
}
# The owner's expected integral grade and edge, and the label they give
EXPECTED = {"S1": (4.45, 3.19, "up"), "S2": (-5.74, 3.19, "down"), "S3": (-0.06, 3.19, "flat"), "S4": (-1.63, 2.01, "flat"),
            "S5": (-0.39, 3.19, "flat"), "S6": (-14.60, 3.19, "down"), "S7": (-1.04, 2.01, "flat"), "S8": (-4.03, 2.41, "down"),
            "S9": (-4.52, 1.59, "down")}
UNSURE_LEANING_FLAT = {"up": 0.15, "flat": 0.45, "down": 0.1, "unsure": 0.3}


def _bars(t0, spot, closes, wick=0.5):
    """One bar per minute from ``t0``, each opening at the close before it (the first at ``spot``)."""
    out, prev = [], spot
    for i, c in enumerate(closes):
        out.append({"ts": (t0 + timedelta(minutes=i)).isoformat(), "open": prev, "high": max(prev, c) + wick,
                    "low": min(prev, c) - wick, "close": c, "volume": 0.0})
        prev = c
    return out


def _scenario(sid, probs=UNSURE_LEANING_FLAT):
    hhmm, spot, f, pick, closes = SCENARIOS[sid]
    t0 = at(*map(int, hhmm.split(":")), day=SCENARIO_DAY)
    return grade_window(_bars(t0, spot, closes), {}, t0, len(closes), spot, f, pick, probs)


def _prior(moves, n=20, day="2026-08-31"):
    """``n`` prior full sessions, newest first, whose closes zigzag by ``moves`` points every minute."""
    last = int(day[-2:])
    return {f"{day[:8]}{d:02d}": bars_from_closes([7700.0 + moves * (i % 2) for i in range(390)], day=f"{day[:8]}{d:02d}")
            for d in range(last, last - n, -1)}


def test_the_factor_is_the_spread_of_an_average_against_the_end():
    assert round(factor(30), 4) == 0.5918 and round(factor(10), 4) == 0.6205
    assert factor(1) == 1.0                                        # one minute: its average is its end
    assert factor(60) < factor(30) < factor(10) and factor(10_000) == pytest.approx(1 / math.sqrt(3), abs=1e-4)


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_the_nine_scenarios_grade_as_the_owner_expects(sid):
    g, edge, lab = EXPECTED[sid]
    r = _scenario(sid)
    assert r["graded"] is True and (r["g"], r["edge"], r["label"]) == (g, edge, lab)
    assert r["factor"] == (0.5918 if len(SCENARIOS[sid][4]) == 30 else 0.6205) and r["running"][-1] == lab


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_the_average_is_every_minutes_move_weighted_by_the_minutes_it_stood(sid):
    """sum(x) / T == sum over j of d_j * (T - j + 1) / T: a move early in the window counts for longer."""
    _, spot, _, _, closes = SCENARIOS[sid]
    x = [c - spot for c in closes]
    t = len(x)
    d = [x[0]] + [x[j] - x[j - 1] for j in range(1, t)]
    assert sum(x) / t == pytest.approx(sum(dj * (t - j) / t for j, dj in enumerate(d)), abs=1e-9)
    assert _scenario(sid)["g"] == round(sum(x) / t, 2)


def test_direction_decides_and_an_unsure_call_is_passed_with_its_lean():
    assert _scenario("S9")["verdict"] == "right" and _scenario("S9")["direction"] == "down"     # down_small, fell big: right
    assert _scenario("S1")["verdict"] == "wrong"                    # a flat call on a window that sat up
    assert _scenario("S6")["verdict"] == "wrong" and _scenario("S6")["margin"] < 0
    assert _scenario("S3")["verdict"] == "right" and 0 < _scenario("S3")["margin"] <= 1
    for sid in ("S4", "S8"):
        r = _scenario(sid)
        assert r["verdict"] == "passed" and r["margin"] is None and r["lean"] == {"direction": "flat", "p": 0.45}
    assert "lean" not in _scenario("S9")
    assert [verdict(p, "down") for p in ("down_big", "down", "up_small", "flat", "unsure", None)] == [
        "right", "right", "wrong", "wrong", "passed", "passed"]


def test_the_margin_is_the_calls_side_of_the_grade_over_the_edge():
    assert margin("up", 6.0, 3.0) == 2.0 and margin("down_big", 6.0, 3.0) == -2.0
    assert margin("flat", 1.5, 3.0) == 0.5 and margin("flat", 6.0, 3.0) == -1.0 and margin("unsure", 6.0, 3.0) is None


def test_the_edge_is_strict():
    assert label(3.19, 3.19) == "flat" and label(-3.19, 3.19) == "flat"
    assert label(3.1900001, 3.19) == "up" and label(-3.1900001, 3.19) == "down"
    t0 = at(11, 0)
    on_edge = grade_window(_bars(t0, 7700.0, [7702.5]), {}, t0, 1, 7700.0, 2.5, "up")     # one minute: the edge is f itself
    past = grade_window(_bars(t0, 7700.0, [7702.51]), {}, t0, 1, 7700.0, 2.5, "up")
    assert (on_edge["label"], on_edge["verdict"]) == ("flat", "wrong") and (past["label"], past["verdict"]) == ("up", "right")


def test_the_path_shows_the_run_the_end_price_hid():
    """S1 ran +19 and ended +3: the line keeps the high and the low, and the running grade turns up after the run."""
    r = _scenario("S1")
    assert r["best"] == {"points": 18.97, "minute": 14} and r["worst"] == {"points": -3.49, "minute": 8}
    assert r["running"][:10] == ["flat"] * 10 and len(r["running"]) == 30
    assert r["sharp_move"]["points"] == 21.44 and r["sharp_move"]["minute"] == 11


def test_a_sharp_move_is_ranked_against_the_same_window_and_is_only_a_note():
    t0 = at(11, 0)
    closes = [7700.0, 7700.5, 7703.5, 7703.0, 7703.5, 7703.0, 7703.5, 7703.0, 7703.5, 7703.0]
    calm, busy = _prior(1.0), _prior(5.0)
    sharp = grade_window(_bars(t0, 7700.0, closes), calm, t0, 10, 7700.0, 3.0, "up")
    assert sharp["sharp_move"]["sharp"] is True and (sharp["sharp_move"]["higher_than"], sharp["sharp_move"]["of"]) == (20, 20)
    assert sharp["sharp_move"]["note"].startswith("a sharp move: the biggest minute, +3.00 at minute 3")
    plain = grade_window(_bars(t0, 7700.0, closes), busy, t0, 10, 7700.0, 3.0, "up")
    assert plain["sharp_move"]["sharp"] is False and plain["sharp_move"]["higher_than"] == 0
    assert (plain["g"], plain["label"]) == (sharp["g"], sharp["label"])          # no boost: the note never moves the grade
    short = grade_window(_bars(t0, 7700.0, closes), _prior(1.0, n=5), t0, 10, 7700.0, 3.0, "up")
    assert short["sharp_move"]["sharp"] is None and short["sharp_move"]["note"].startswith("too little history")
    one_off = grade_window(_bars(t0, 7700.0, closes), {**calm, "2026-08-10": bars_from_closes([7700.0 + 9 * (i % 2) for i in range(390)], day="2026-08-10")},
                           t0, 10, 7700.0, 3.0, "up")
    assert one_off["sharp_move"]["of"] == 20                        # the last 20 sessions, newest first: the 21st is not read


def test_a_missing_bar_inside_is_filled_and_a_missing_mark_bar_or_two_holes_are_not_graded():
    t0 = at(11, 0)
    bars = _bars(t0, 7700.0, [7701.0 + k for k in range(10)])
    one = grade_window(bars[:4] + bars[5:], {}, t0, 10, 7700.0, 3.0, "up")
    assert one["graded"] is True and one["filled"] == [5] and one["g"] == grade_window(bars, {}, t0, 10, 7700.0, 3.0, "up")["g"]
    first = grade_window(bars[1:], {}, t0, 10, 7700.0, 3.0, "up")
    assert first["filled"] == [1] and first["graded"] is True                   # the first stands between the spot and the second
    for holed, missing in ((bars[:9], [10]), (bars[:3] + bars[4:6] + bars[7:], [4, 7])):
        r = grade_window(holed, {}, t0, 10, 7700.0, 3.0, "up")
        assert r == {"graded": False, "reason": NOT_GRADED, "missing": missing, "bad_ticks": [], "stale_read": False}


def test_a_bad_tick_is_replaced_and_recorded_once_the_history_can_rank_it():
    t0 = at(11, 0)
    closes = [7700.5, 7700.0, 7700.5, 7730.0, 7700.5, 7700.0, 7700.5, 7700.0, 7700.5, 7700.0]
    bars = _bars(t0, 7700.0, closes)
    bars[4]["open"] = 7700.1                                        # the minute after the tick opens back where it was
    r = grade_window(bars, _prior(1.0), t0, 10, 7700.0, 3.0, "flat")
    assert r["bad_ticks"] == [{"minute": 4, "ts": bars[3]["ts"], "close": 7730.0, "replaced_by": 7700.3}]
    assert r["label"] == "flat" and r["best"]["points"] == 0.5
    raw = grade_window(bars, _prior(1.0, n=5), t0, 10, 7700.0, 3.0, "flat")
    assert raw["bad_ticks"] == [] and raw["best"] == {"points": 30.0, "minute": 4}      # too little history: nothing corrected
    held = [*closes[:4], 7730.0, *closes[5:]]                       # a jump that holds is a move, not a tick
    assert grade_window(_bars(t0, 7700.0, held), _prior(1.0), t0, 10, 7700.0, 3.0, "flat")["bad_ticks"] == []
    mark = [*closes[:3], 7700.0, *closes[4:9], 7730.0]              # the mark bar has no minute after it: never a tick
    assert grade_window(_bars(t0, 7700.0, mark), _prior(1.0), t0, 10, 7700.0, 3.0, "flat")["bad_ticks"] == []


def test_a_spot_no_bar_traded_near_the_row_minute_is_a_stale_read():
    t0 = at(11, 0)
    before = _bars(t0 - timedelta(minutes=3), 7690.0, [7690.0]) + _bars(t0 - timedelta(minutes=2), 7700.0, [7700.0, 7700.2])
    window = _bars(t0, 7700.2, [7700.5 + k for k in range(10)])
    assert grade_window(before + window, {}, t0, 10, 7700.2, 3.0, "up")["stale_read"] is False
    assert grade_window(before + window, {}, t0, 10, 7690.0, 3.0, "up")["stale_read"] is True    # last traded 3 minutes back
    assert grade_window(window[1:], {}, t0, 10, 7690.0, 3.0, "up")["stale_read"] is None           # nothing on file to check
