"""The gap and open family (labels/gap_open.py): each label's sentence at its verdicts and their boundaries,
its omissions, the gap_fill_next_hour gate both ways, and that a bar finishing after the read never counts.
The gap's size is ranked against prior sessions whose first diary row is written into a tmp folder.

Every scene carries a 09:31 diary row, so the morning anchor is trusted (SIGMA points, 80 so that the cuts
land on exact figures), and yesterday's close is PRIOR_CLOSE. Bars start at 09:30, one close a minute;
the fifth close is the settled open."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, time, timedelta

import pytest

from conftest import DAY, at, bars_from_closes, flat_bars, make_row, prior_sessions, write_state
from spx_jev.cuts import (GAP_RANK_MIN_SESSIONS, GAP_RULE_SIGMA, GIVEBACK_THIRD, NOISE_EDGE_SIGMA, NOISE_LOOKBACK, OPEN_CONTESTED_CROSSES,
                          RANGE_TOP_SHARE, RULER_FLOOR_SIGMA)
from spx_jev.labels.gap_open import build_gap_open_labels
from spx_jev.labels.measures import bar_time
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.state_builder import make_scene

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

# Twenty prior gaps, 0.02 to 0.40 sigma: beating 6 of them is the bottom third, 7 to 13 the middle, 14 the top.
TWENTY = [0.02 * k for k in range(1, 21)]


def write_first_row(root, day: str, **over) -> None:
    (root / "reversion").mkdir(parents=True, exist_ok=True)
    (root / "reversion" / f"{day}.jsonl").write_text(json.dumps(make_row(at(9, 31, day), 7700.0, sigma=SIGMA, **over)) + "\n")


def gap_history(root, gaps: list[float], newest: date = date(2026, 9, 17)) -> tuple[dict, dict]:
    """Prior sessions, newest first from ``newest``, each settling its open at 7700 over a close ``gap`` sigma away (alternately
    below and above it), with a first diary row carrying that close; their bars and trusted rulers."""
    bars, rulers = {}, {}
    for k, g in enumerate(gaps):
        day = (newest - timedelta(days=k)).isoformat()
        bars[day] = flat_bars(390, day=day)
        write_first_row(root, day, prior_close=7700.0 + (1 if k % 2 else -1) * g * SIGMA)
        rulers[day] = SigmaRuler(SIGMA, "anchor")
    return bars, rulers


def sized(scene_factory, root, settled, gaps=TWENTY, rulers=None, now=None, **kw):
    """The 09:35 read (or one at ``now``) on a day settling its open at ``settled``, with ``gaps`` as the prior sessions' gaps."""
    bars, trusted = gap_history(root, gaps)
    sc = scene(scene_factory, now or at(9, 35), opening(settled, [settled] * 30), prior_bars=bars, **kw)
    return replace(sc, prior_rulers={**trusted, **(rulers or {})}, state_dir=root)


@pytest.mark.parametrize("settled, verdict", [
    (7710.4, "0.13 sigma is larger than 6 of the last 20 days' gaps, bottom third: price opened above yesterday's close, "
             f"{OPENED}; 0.5 times this morning's same-day straddle"),
    (7712.0, "0.15 sigma is larger than 7 of the last 20 days' gaps, middle third: price opened above yesterday's close, "
             f"{OPENED}; 0.5 times this morning's same-day straddle"),
    (7678.4, "0.27 sigma is larger than 13 of the last 20 days' gaps, middle third: price opened below yesterday's close, "
             f"{OPENED}; 1.0 times this morning's same-day straddle"),
    (7676.8, "0.29 sigma is larger than 14 of the last 20 days' gaps, top third: price opened below yesterday's close, "
             f"{OPENED}; 1.1 times this morning's same-day straddle"),
    (7735.2, "0.44 sigma is larger than 20 of the last 20 days' gaps, top third: price opened above yesterday's close, "
             f"{OPENED}; 1.6 times this morning's same-day straddle"),
    (7708.0, "0.10 sigma is larger than 4 of the last 20 days' gaps, bottom third: price opened above yesterday's close, "
             f"{OPENED}; 0.4 times this morning's same-day straddle"),              # level with the 0.10 gap: not larger than it
])
def test_the_gaps_size_is_its_third_among_the_prior_sessions_gaps_whichever_way_it_went(scene_factory, tmp_path, settled, verdict):
    got, _, _ = labels(sized(scene_factory, tmp_path, settled))
    assert got["gap.size"] == f"this morning's gap of {verdict}"


def test_the_gaps_size_needs_ten_usable_prior_sessions_and_leaves_out_estimated_rulers(scene_factory, tmp_path):
    assert GAP_RANK_MIN_SESSIONS == 10
    got, _, _ = labels(sized(scene_factory, tmp_path, 7712.0, gaps=TWENTY[:10]))
    assert got["gap.size"].startswith("this morning's gap of 0.15 sigma is larger than 7 of the last 10 days' gaps, top third:")
    need = "its rank needs 10 prior sessions with a trusted morning ruler, a settled open and yesterday's close, have 9"
    _, omitted, _ = labels(sized(scene_factory, tmp_path, 7712.0, gaps=TWENTY[:9]))
    assert omitted["gap.size"] == need
    # the two smallest gaps sit on days whose ruler was estimated: they are neither counted nor beaten
    estimated = {"2026-09-17": SigmaRuler(SIGMA, "live"), "2026-09-16": SigmaRuler(SIGMA, "vix")}
    got, _, _ = labels(sized(scene_factory, tmp_path, 7712.0, gaps=TWENTY[:12], rulers=estimated))
    assert got["gap.size"].startswith("this morning's gap of 0.15 sigma is larger than 5 of the last 10 days' gaps, middle third:")
    _, omitted, _ = labels(sized(scene_factory, tmp_path, 7712.0, gaps=TWENTY[:11], rulers=estimated))
    assert omitted["gap.size"] == need
    # a day with no prior close on its first diary row, or no diary at all, is left out too
    sc = sized(scene_factory, tmp_path, 7712.0, gaps=TWENTY[:11])
    write_first_row(tmp_path, "2026-09-17", prior_close=None)
    assert "is larger than 6 of the last 10 days' gaps, middle third:" in labels(sc)[0]["gap.size"]
    (tmp_path / "reversion" / "2026-09-16.jsonl").unlink()
    assert labels(sc)[1]["gap.size"] == need
    _, omitted, _ = labels(replace(sc, state_dir=None))
    assert omitted["gap.size"] == "no state folder to read the prior sessions' closes from"


def test_the_gap_waits_for_the_settled_open_and_says_when_its_ruler_is_estimated(scene_factory, tmp_path):
    closes = opening(7735.2, [7735.0] * 30)
    _, omitted, gates = labels(scene(scene_factory, at(9, 34, ss=59), closes, spot=7735.2))     # the 09:34 bar has not finished
    assert omitted["gap.size"] == "no settled open yet: the 09:34 bar has not finished"
    assert gates["gap_fill_next_hour"] == "no gap to fill: no settled open yet: the 09:34 bar has not finished"
    _, omitted, _ = labels(scene(scene_factory, at(10, 0), closes, row_over={"prior_close": None}))
    assert omitted["gap.size"] == omitted["gap.fill_progress"] == "row carries no prior close"
    got, _, _ = labels(sized(scene_factory, tmp_path, 7735.2, now=at(10, 0), anchored=False))   # no row by 09:40: the live sigma stands in
    assert got["gap.size"].endswith("1.6 times this morning's same-day straddle (ruler estimated)")


def test_the_gaps_size_is_ranked_only_against_sessions_before_today(tmp_path):
    """Built from disk: a session after today, with the largest gap of all, is never one of the prior sessions."""
    prior, _ = gap_history(tmp_path, TWENTY[:10])
    later, _ = gap_history(tmp_path, [1.0], newest=date(2026, 9, 21))
    rows = [make_row(at(9, 31), 7700.0, sigma=SIGMA, prior_close=PRIOR_CLOSE),
            make_row(at(9, 35), 7735.2, sigma=SIGMA, prior_close=PRIOR_CLOSE)]
    write_state(tmp_path, DAY, rows, bars_from_closes(opening(7735.2, [7735.0] * 30)), {**prior, **later})
    got, _, _ = labels(make_scene(tmp_path, DAY, time(9, 35)))
    assert got["gap.size"].startswith("this morning's gap of 0.44 sigma is larger than 10 of the last 10 days' gaps, top third:")


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
    stalled = scene(scene_factory, at(11, 32), closes)
    stalled.bars[:] = [b for b in stalled.bars if bar_time(b) < at(11, 20)]                   # the 11:2x bars never arrived
    _, omitted, _ = labels(stalled)
    assert omitted["gap.morning_vs_gap"] == "the morning runs to 11:30 and its last bar has not finished"


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

# settled open 7700; 09:35 dips to 7699, then up a point a minute to 7718 by 09:53, back to 7714: each of the last
# three 5-minute slices at 09:55 ranges 5 to 6 points, so the tape unit is 6 points (0.075 sigma)
PATH = [7700.0] * 5 + [7699.0, 7701.0] + [7701.0 + i for i in range(1, 18)] + [7714.0] * 30
UNIT = "(one tape unit = the typical 5-minute swing right now, 0.07 sigma)"


def test_the_opening_path_firm_fading_and_rotating_in_tape_units(scene_factory):
    got, _, _ = labels(scene(scene_factory, at(9, 55), PATH, row_over={"prior_close": 7680.0}))
    assert got["open.path"] == (
        "20 minutes after the settled open, price reached 0.23 sigma above it and 0.02 sigma below it, crossed it once, and gave back "
        f"24% of its high, under the stall line; price is now 2.3 tape units above the settled open {UNIT}, more than one tape unit "
        "away on the up side, on the gap's side")
    got, _, _ = labels(scene(scene_factory, at(9, 55), PATH, spot=7712.0, row_over={"prior_close": 7720.0}))   # 6.5 of 18.5 points back
    assert ("gave back 35% of its high, at or past the stall line" in got["open.path"] and GIVEBACK_THIRD == 0.33
            and got["open.path"].endswith("more than one tape unit away on the up side, against the gap's side"))
    down = [7700.0] * 5 + [7700.0 - i for i in range(1, 21)]
    got, _, _ = labels(scene(scene_factory, at(9, 55), down, row_over={"prior_close": 7699.0}))
    assert got["open.path"].endswith(f"gave back 2% of its low, under the stall line; price is now 3.3 tape units below the settled open "
                                     f"{UNIT}, more than one tape unit away on the down side, with no real gap this morning")


@pytest.mark.parametrize("spot, verdict", [
    (7706.0, f"and crossed it once; price is now 1.0 tape units above the settled open {UNIT}, within one tape unit"),
    (7694.0, f"and crossed it once; price is now 1.0 tape units below the settled open {UNIT}, within one tape unit"),
    (7706.01, f"at or past the stall line; price is now 1.0 tape units above the settled open {UNIT}, more than one tape unit away on "
              "the up side, with no real gap this morning"),
])
def test_the_opening_path_is_rotating_at_exactly_one_tape_unit_and_away_past_it(scene_factory, spot, verdict):
    got, _, _ = labels(scene(scene_factory, at(9, 55), PATH, spot=spot, row_over={"prior_close": 7699.0}))
    assert got["open.path"].endswith(verdict)


def test_the_opening_path_unit_is_the_one_at_the_read(scene_factory):
    # a 20-point bar at 09:55 has not finished at the 09:55 read: the unit and the sentence ignore it
    wild = PATH[:25] + [7734.0] + PATH[26:]
    got, _, _ = labels(scene(scene_factory, at(9, 55), PATH, spot=7714.0))
    assert labels(scene(scene_factory, at(9, 55), wild, spot=7714.0))[0]["open.path"] == got["open.path"]


def test_the_opening_path_unit_at_its_floor_says_so(scene_factory):
    got, _, _ = labels(scene(scene_factory, at(9, 55), [7700.0] * 55, spot=7703.0))
    assert RULER_FLOOR_SIGMA == 0.03 and got["open.path"].endswith(
        "price is now 1.2 tape units above the settled open (one tape unit = the typical 5-minute swing right now, 0.03 sigma, at its floor), "
        "more than one tape unit away on the up side, with no real gap this morning")


def test_the_opening_path_is_omitted_without_a_live_tape_unit(scene_factory):
    _, omitted, _ = labels(scene(scene_factory, at(9, 44), PATH))
    assert omitted["open.path"] == "the tape unit is held until 09:45, when three 5-minute slices first exist"
    _, omitted, _ = labels(scene(scene_factory, at(9, 55), PATH[:19]))                          # no bar since 09:48
    assert omitted["open.path"] == "no tape unit this read: the bars have stopped"


def test_the_opening_path_never_reaches_a_negative_distance(scene_factory):
    sc = scene(scene_factory, at(9, 45), [7700.0] * 5 + [7702.0, 7703.0, 7704.0, 7705.0, 7706.0] + [7706.0] * 5)
    sc.bars[5].update(open=7701.0, low=7700.5)                                                   # the 09:35 bar opens above the settled open
    got, _, _ = labels(sc)
    assert got["open.path"].startswith("10 minutes after the settled open, price reached 0.08 sigma above it and 0.00 sigma below it, "
                                       "never crossed it")


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

def test_the_overnight_bonds_question_sleeps_while_its_lane_is_dark(scene_factory):
    _, omitted, gates = labels(scene(scene_factory, at(10, 0), opening(7735.2, [7735.0] * 30)))
    assert gates["overnight_bonds_vs_gap"].startswith("data missing: no premarket lane: /ZN and bitcoin (/MBT)")
    assert not any(path.startswith("overnight.") for path in omitted)                          # the registry omits them as dark
