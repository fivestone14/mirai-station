"""The gap and open family (labels/gap_open.py): each label's sentence at its verdicts and their boundaries,
its omissions, the gap_fill_next_hour gate both ways, and that a bar finishing after the read never counts.
The gap's size, and so whether it is real, is ranked against prior sessions whose first diary row is written
into a tmp folder; the open's crossings and path against prior sessions' bars to the same minute.

Every scene carries a 09:31 diary row, so the morning anchor is trusted (SIGMA points, 80 so that the cuts
land on exact figures), and yesterday's close is PRIOR_CLOSE. Bars start at 09:30, one close a minute;
the fifth close is the settled open."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, time, timedelta

import pytest

from conftest import DAY, at, bars_from_closes, flat_bars, make_row, write_state
from spx_jev.cuts import GIVEBACK_THIRD, RANGE_TOP_SHARE, RULER_FLOOR_SIGMA, SAME_CLOCK_MIN_SESSIONS
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


def gapped(scene_factory, root, now, closes, gaps=TWENTY, **kw):
    """A read as scene() makes it, with ``gaps`` as the prior sessions' gaps, so the gap is ranked and a real gap
    is one above their bottom third."""
    bars, trusted = gap_history(root, gaps)
    return replace(scene(scene_factory, now, closes, prior_bars=bars, **kw), prior_rulers=trusted, state_dir=root)


def sized(scene_factory, root, settled, gaps=TWENTY, rulers=None, now=None, **kw):
    """The 09:35 read (or one at ``now``) on a day settling its open at ``settled``, with ``gaps`` as the prior sessions' gaps."""
    sc = gapped(scene_factory, root, now or at(9, 35), opening(settled, [settled] * 30), gaps, **kw)
    return replace(sc, prior_rulers={**sc.prior_rulers, **(rulers or {})})


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
    (7700.8, "0.01 sigma is no larger than any of the last 20 days' gaps, bottom third: price opened above yesterday's close, "
             f"{OPENED}; 0.0 times this morning's same-day straddle"),
])
def test_the_gaps_size_is_its_third_among_the_prior_sessions_gaps_whichever_way_it_went(scene_factory, tmp_path, settled, verdict):
    got, _, _ = labels(sized(scene_factory, tmp_path, settled))
    assert got["gap.size"] == f"this morning's gap of {verdict}"


def test_the_gaps_size_needs_ten_usable_prior_sessions_and_leaves_out_estimated_rulers(scene_factory, tmp_path):
    assert SAME_CLOCK_MIN_SESSIONS == 10
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

def test_a_gap_that_keeps_the_half_line_untouched_is_still_open_and_its_fill_question_sleeps(scene_factory, tmp_path):
    got, _, gates = labels(gapped(scene_factory, tmp_path, at(11, 0), opening(7735.2, [7725.0] * 90)))
    assert got["gap.fill_progress"] == (
        "the gap up is still open after 85 minutes: price sits 0.31 sigma above yesterday's close and keeps 71% of the 0.44 sigma gap, "
        "at or above the half line; it has not touched yesterday's close (within 0.02 sigma); the gap was larger than 20 of the last 20 days' "
        "gaps, top third, a real gap")
    assert gates["gap_fill_next_hour"] == "the gap up is still open, not losing ground"


def test_the_half_line_and_the_touch_decide_whether_the_gap_is_losing_ground(scene_factory, tmp_path):
    now = at(11, 0)
    got, _, gates = labels(gapped(scene_factory, tmp_path, now, opening(7732.0, [7716.0] * 90)))            # keeps exactly half
    assert "keeps 50% of the 0.40 sigma gap, at or above the half line" in got["gap.fill_progress"]
    assert got["gap.fill_progress"].startswith("the gap up is still open") and gates["gap_fill_next_hour"] == "the gap up is still open, not losing ground"
    got, _, gates = labels(gapped(scene_factory, tmp_path, now, opening(7732.0, [7715.9] * 90)))
    assert got["gap.fill_progress"].startswith("the gap up is losing ground after 85 minutes: price sits 0.20 sigma above yesterday's "
                                               "close and keeps 50% of the 0.40 sigma gap, below the half line;")
    assert gates["gap_fill_next_hour"] is None
    # a bar low 1.6 points (0.02 sigma) above the close touches it; 1.7 does not
    touched = opening(7732.0, [7720.0] * 30 + [7702.1] + [7730.0] * 59)                        # the 10:05 bar's low is 7701.6
    got, _, gates = labels(gapped(scene_factory, tmp_path, now, touched))
    assert got["gap.fill_progress"] == (
        "the gap up is losing ground after 85 minutes: price sits 0.38 sigma above yesterday's close and keeps 94% of the 0.40 sigma gap, "
        "at or above the half line; it first touched yesterday's close (within 0.02 sigma) 54 minutes ago; the gap was larger than 19 "
        "of the last 20 days' gaps, top third, a real gap")
    assert gates["gap_fill_next_hour"] is None
    untouched = opening(7732.0, [7720.0] * 30 + [7702.2] + [7730.0] * 59)
    got, _, _ = labels(gapped(scene_factory, tmp_path, now, untouched))
    assert got["gap.fill_progress"].startswith("the gap up is still open")


def test_a_gap_down_filled_through_the_close_and_a_day_with_no_real_gap(scene_factory, tmp_path):
    got, _, gates = labels(gapped(scene_factory, tmp_path, at(11, 0), opening(7668.0, [7690.0] * 60 + [7712.0] * 30)))
    assert got["gap.fill_progress"] == (
        "the gap down is losing ground after 85 minutes: price sits 0.15 sigma above yesterday's close, through it, and keeps none of the "
        "0.40 sigma gap, below the half line; it first touched yesterday's close (within 0.02 sigma) 24 minutes ago; the gap was larger than "
        "19 of the last 20 days' gaps, top third, a real gap")
    assert gates["gap_fill_next_hour"] is None
    got, omitted, gates = labels(gapped(scene_factory, tmp_path, at(11, 0), opening(7708.0, [7712.0] * 90)))
    bottom = "larger than 4 of the last 20 days' gaps, bottom third"
    assert got["gap.fill_progress"] == (f"there was no real gap: price opened 0.10 sigma above yesterday's close, a gap {bottom}; "
                                        "it now sits 0.15 sigma above yesterday's close")
    assert gates["gap_fill_next_hour"] == f"no real gap: the settled open's gap was {bottom}"
    assert omitted["gap.morning_vs_gap"] == f"no real gap this morning: the settled open's gap was {bottom}"


@pytest.mark.parametrize("settled, real", [(7710.4, False), (7712.0, True), (7689.6, False), (7688.0, True)])
def test_a_real_gap_is_one_above_the_bottom_third_of_the_prior_sessions_gaps(scene_factory, tmp_path, settled, real):
    # 0.13 sigma beats 6 of the twenty prior gaps (bottom third), 0.15 beats 7 (middle third), either way
    sc = gapped(scene_factory, tmp_path, at(12, 0), opening(settled, [settled] * 150))
    got, omitted, gates = labels(sc)
    assert got["gap.fill_progress"].startswith("the gap" if real else "there was no real gap")
    assert (gates["gap_fill_next_hour"] or "").startswith("no real gap") is not real
    assert ("gap.morning_vs_gap" in got) is real and ("gap.morning_vs_gap" in omitted) is not real


def test_no_real_gap_ends_the_morning_label_so_gap_reasserts_sleeps_but_an_unranked_gap_does_not(scene_factory, tmp_path):
    small = build_gap_open_labels(gapped(scene_factory, tmp_path / "small", at(12, 0), opening(7710.4, [7710.4] * 150)))
    unranked = build_gap_open_labels(gapped(scene_factory, tmp_path / "unranked", at(12, 0), opening(7732.0, [7736.0] * 160),
                                            gaps=TWENTY[:9]))
    assert "gap.morning_vs_gap" in small.ended and "gap.morning_vs_gap" not in unranked.ended


def test_whether_the_gap_is_real_waits_for_its_rank(scene_factory, tmp_path):
    need = "its rank needs 10 prior sessions with a trusted morning ruler, a settled open and yesterday's close, have 9"
    _, omitted, gates = labels(gapped(scene_factory, tmp_path, at(12, 0), opening(7732.0, [7736.0] * 160), gaps=TWENTY[:9]))
    assert omitted["gap.fill_progress"] == omitted["gap.morning_vs_gap"] == omitted["gap.size"] == need
    assert gates["gap_fill_next_hour"] is None


def test_a_touch_in_a_bar_that_has_not_finished_does_not_count(scene_factory, tmp_path):
    closes = opening(7732.0, [7720.0] * 85 + [7700.0] + [7720.0] * 10)                         # the 11:00 bar dips to the close
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(11, 0, ss=30), closes, spot=7720.0))
    assert "it has not touched yesterday's close" in got["gap.fill_progress"]
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(11, 1), closes, spot=7720.0))
    assert "it first touched yesterday's close (within 0.02 sigma) within the last minute" in got["gap.fill_progress"]


# ---- gap.morning_vs_gap

def test_the_morning_against_the_gap_from_the_settled_open_to_1130(scene_factory, tmp_path):
    with_gap = opening(7732.0, [7736.0] * 115 + [7730.0] * 30)                                    # 11:29 closes at 7736
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(12, 0), with_gap))
    assert got["gap.morning_vs_gap"] == (
        "from the settled open to 11:30 price rose 0.05 sigma, with this morning's 0.40 sigma gap up (larger than 19 of the last 20 days' gaps, top "
        "third), "
        "and did not touch yesterday's close; it now sits 0.38 sigma above it")
    faded = opening(7732.0, [7701.0] + [7716.0] * 114 + [7720.0] * 30)
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(12, 0), faded))
    assert got["gap.morning_vs_gap"] == (
        "from the settled open to 11:30 price fell 0.20 sigma, against this morning's 0.40 sigma gap up (larger than 19 of the last 20 days' gaps, top "
        "third), "
        "and touched yesterday's close; it now sits 0.25 sigma above it")


def test_the_morning_counts_once_its_1129_bar_has_finished(scene_factory, tmp_path):
    closes = opening(7732.0, [7736.0] * 145)
    _, omitted, _ = labels(gapped(scene_factory, tmp_path, at(11, 29, ss=59), closes))
    assert omitted["gap.morning_vs_gap"] == "the morning runs to 11:30 and its last bar has not finished"
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(11, 30), closes))
    assert got["gap.morning_vs_gap"].startswith("from the settled open to 11:30 price rose 0.05 sigma, with")
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(11, 30), opening(7732.0, [7732.0] * 145)))
    assert got["gap.morning_vs_gap"].startswith("from the settled open to 11:30 price rose 0.00 sigma, neither with nor against this morning's")
    late_touch = opening(7732.0, [7736.0] * 115 + [7700.0] + [7736.0] * 29)                     # the 11:30 bar is after the morning
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(12, 0), late_touch))
    assert "and did not touch yesterday's close" in got["gap.morning_vs_gap"]
    stalled = gapped(scene_factory, tmp_path, at(11, 32), closes)
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

# Twenty prior sessions whose price stood 0.05% to 1.00% from their settled open of 7700 from 09:35 on, newest the
# smallest: a stretch of 0.71% beats 14 of them (top third), 0.36% beats 7 (middle), 0.16% beats 3 (bottom).
MOVES = [0.0005 * k for k in range(1, 21)]


def moved_sessions(moves: list[float]) -> dict[str, list[dict]]:
    out = {}
    for k, m in enumerate(moves):
        day = (date(2026, 9, 17) - timedelta(days=k)).isoformat()
        out[day] = bars_from_closes([7700.0] * 5 + [7700.0 * (1 + m)] * 385, day=day)
    return out


def noise_scene(scene_factory, stretch: float, moves: list[float] = MOVES, rulers=None):
    """Today opened at 7700 over a 7690 close; at 11:00 price is ``stretch`` (a share) above 7700, or below 7690
    when negative."""
    spot = 7700.0 * (1 + stretch) if stretch >= 0 else 7690.0 * (1 + stretch)
    sc = scene(scene_factory, at(11, 0), [7700.0] * 90, spot=spot, row_over={"prior_close": 7690.0}, prior_bars=moved_sessions(moves))
    return replace(sc, prior_rulers=rulers or {})


def test_the_noise_band_ranks_the_stretch_past_the_open_and_yesterdays_close_against_the_same_minute(scene_factory):
    band = "the usual move-from-the-open band for 11:00"
    got, _, _ = labels(noise_scene(scene_factory, 0.0071))
    assert got["open.noise_band"] == ("price is 0.68 sigma above the higher of the settled open and yesterday's close, a stretch bigger "
                                      f"than 14 of the last 20 sessions had moved from their settled open by this minute, top third: above {band}")
    got, _, _ = labels(noise_scene(scene_factory, -0.0071))
    assert got["open.noise_band"] == ("price is 0.68 sigma below the lower of the settled open and yesterday's close, a stretch bigger "
                                      f"than 14 of the last 20 sessions had moved from their settled open by this minute, top third: below {band}")
    got, _, _ = labels(noise_scene(scene_factory, 0.0036))
    assert got["open.noise_band"].endswith(f"bigger than 7 of the last 20 sessions had moved from their settled open by this minute, middle third: at the edge of {band}")
    got, _, _ = labels(noise_scene(scene_factory, 0.0016))
    assert got["open.noise_band"].endswith(f"bigger than 3 of the last 20 sessions had moved from their settled open by this minute, bottom third: inside {band}")
    # a stretch equal to a session's move does not beat it
    got, _, _ = labels(noise_scene(scene_factory, 0.0005))
    assert "no bigger than any of the last 20 sessions" in got["open.noise_band"]


def test_between_the_open_and_yesterdays_close_price_is_inside_the_noise_band(scene_factory):
    sc = noise_scene(scene_factory, 0.0)
    got, _, _ = labels(replace(sc, row={**sc.row, "spot": 7696.0}))
    assert got["open.noise_band"] == ("price sits between the settled open and yesterday's close, 0.05 sigma below the higher and 0.07 sigma "
                                      "above the lower, stretched past neither: inside the usual move-from-the-open band for 11:00")


def test_the_noise_band_needs_ten_sessions_with_a_trusted_ruler(scene_factory):
    assert SAME_CLOCK_MIN_SESSIONS == 10
    _, omitted, _ = labels(noise_scene(scene_factory, 0.0071, moves=MOVES[:9]))
    assert omitted["open.noise_band"] == "its rank needs 10 prior sessions with a move from their settled open at this minute, have 9"
    # an estimated ruler leaves its session out: ten sessions, one estimated, are not enough
    estimated = {"2026-09-17": SigmaRuler(75.0, "live")}
    _, omitted, _ = labels(noise_scene(scene_factory, 0.0071, moves=MOVES[:10], rulers=estimated))
    assert omitted["open.noise_band"] == "its rank needs 10 prior sessions with a move from their settled open at this minute, have 9"
    got, _, _ = labels(noise_scene(scene_factory, 0.0071, moves=MOVES[:11], rulers=estimated))
    assert "bigger than 10 of the last 10 sessions" in got["open.noise_band"]
    _, omitted, _ = labels(scene(scene_factory, at(9, 35), [7700.0] * 10))
    assert omitted["open.noise_band"] == "no finished bar after the settled open yet"


# ---- open.path and open.settled_open_crosses

# settled open 7700; 09:35 dips to 7699, then up a point a minute to 7718 by 09:53, back to 7714: each of the last
# three 5-minute slices at 09:55 ranges 5 to 6 points, so the tape unit is 6 points (0.075 sigma)
PATH = [7700.0] * 5 + [7699.0, 7701.0] + [7701.0 + i for i in range(1, 18)] + [7714.0] * 30
UNIT = "(one tape unit = the typical 5-minute swing right now, 0.07 sigma)"


def test_the_opening_path_firm_fading_and_rotating_in_tape_units(scene_factory, tmp_path):
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(9, 55), PATH, row_over={"prior_close": 7680.0}))
    assert got["open.path"] == (
        "20 minutes after the settled open, price reached 0.23 sigma above it and 0.02 sigma below it, crossed it once, and gave back "
        f"24% of its high, under the stall line; price is now 2.3 tape units above the settled open {UNIT}, more than one tape unit "
        "away on the up side, on the gap's side")
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(9, 55), PATH, spot=7712.0, row_over={"prior_close": 7720.0}))   # 6.5 of 18.5 points back
    assert ("gave back 35% of its high, at or past the stall line" in got["open.path"] and GIVEBACK_THIRD == 0.33
            and got["open.path"].endswith("more than one tape unit away on the up side, against the gap's side"))
    down = [7700.0] * 5 + [7700.0 - i for i in range(1, 21)]
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(9, 55), down, row_over={"prior_close": 7699.0}))
    assert got["open.path"].endswith(f"gave back 2% of its low, under the stall line; price is now 3.3 tape units below the settled open "
                                     f"{UNIT}, more than one tape unit away on the down side, with no real gap this morning")


@pytest.mark.parametrize("spot, verdict", [
    (7706.0, f"and crossed it once; price is now 1.0 tape units above the settled open {UNIT}, within one tape unit"),
    (7694.0, f"and crossed it once; price is now 1.0 tape units below the settled open {UNIT}, within one tape unit"),
    (7706.01, f"at or past the stall line; price is now 1.0 tape units above the settled open {UNIT}, more than one tape unit away on "
              "the up side, with no real gap this morning"),
])
def test_the_opening_path_is_rotating_at_exactly_one_tape_unit_and_away_past_it(scene_factory, tmp_path, spot, verdict):
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(9, 55), PATH, spot=spot, row_over={"prior_close": 7699.0}))
    assert got["open.path"].endswith(verdict)


def test_the_opening_path_unit_is_the_one_at_the_read(scene_factory):
    # a 20-point bar at 09:55 has not finished at the 09:55 read: the unit and the sentence ignore it
    wild = PATH[:25] + [7734.0] + PATH[26:]
    got, _, _ = labels(scene(scene_factory, at(9, 55), PATH, spot=7714.0))
    assert labels(scene(scene_factory, at(9, 55), wild, spot=7714.0))[0]["open.path"] == got["open.path"]


def test_the_opening_path_unit_at_its_floor_says_so(scene_factory, tmp_path):
    got, _, _ = labels(gapped(scene_factory, tmp_path, at(9, 55), [7700.0] * 55, spot=7703.0))
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


def crossing_closes(n: int) -> list[float]:
    """Settling the open at 7700, then crossing it ``n`` times a minute apart, then holding on the last side."""
    return [7700.0] * 5 + [7702.0 if k % 2 == 0 else 7698.0 for k in range(n + 1)] + [7700.0 + (1 if n % 2 == 0 else -1) * 2] * 60


def prior_days(n: int) -> list[str]:
    return [(date(2026, 9, 17) - timedelta(days=k)).isoformat() for k in range(n)]


def history(paths: list[list[float]]) -> dict[str, list[dict]]:
    """Prior sessions, newest first from 2026-09-17, one per path of closes from 09:30."""
    return {day: bars_from_closes(closes, day=day) for day, closes in zip(prior_days(len(paths)), paths)}


# Twenty prior sessions crossing their settled open 0 to 19 times by 09:56: 6 crossings are more than 6 of them (bottom
# third), 7 more than 7 (middle third), 14 more than 14 (top third); a count level with a session's does not beat it.
CROSSED = history([crossing_closes(n) for n in range(20)])


@pytest.mark.parametrize("n, verdict", [
    (1, "crossed it once, more often than 1 of the last 20 sessions had by this minute, bottom third: one-sided"),
    (6, "crossed it 6 times, more often than 6 of the last 20 sessions had by this minute, bottom third: one-sided"),
    (7, "crossed it 7 times, more often than 7 of the last 20 sessions had by this minute, middle third: some crossing"),
    (13, "crossed it 13 times, more often than 13 of the last 20 sessions had by this minute, middle third: some crossing"),
    (14, "crossed it 14 times, more often than 14 of the last 20 sessions had by this minute, top third: contested"),
])
def test_the_settled_open_crossings_are_ranked_against_the_prior_sessions_at_this_minute(scene_factory, n, verdict):
    got, _, _ = labels(scene(scene_factory, at(9, 56), crossing_closes(n), prior_bars=CROSSED))
    side = "above" if n % 2 == 0 else "below"
    assert got["open.settled_open_crosses"] == f"price sits 0.03 sigma {side} the settled open; since it was set 21 minutes ago price has {verdict}"


def test_the_crossings_count_a_close_on_the_open_as_its_side_and_never_an_unfinished_bar(scene_factory):
    level = [7700.0] * 5 + [7702.0, 7700.0, 7702.0] + [7702.0] * 18 + [7698.0] * 10              # the 09:56 bar crosses
    got, _, _ = labels(scene(scene_factory, at(9, 56), level, spot=7702.0, prior_bars=CROSSED))
    assert got["open.settled_open_crosses"] == ("price sits 0.03 sigma above the settled open; since it was set 21 minutes ago price has "
                                                "never crossed it, no more often than any of the last 20 sessions had by this minute, "
                                                "bottom third: one-sided")
    got, _, _ = labels(scene(scene_factory, at(9, 57), level, spot=7698.0, prior_bars=CROSSED))
    assert "crossed it once, more often than 1 of the last 20 sessions had by this minute, bottom third" in got["open.settled_open_crosses"]
    _, omitted, _ = labels(scene(scene_factory, at(9, 35), level, prior_bars=CROSSED))
    assert omitted["open.settled_open_crosses"] == omitted["open.path"] == "no finished bar after the settled open yet"


def test_the_crossings_rank_needs_ten_prior_sessions_and_keeps_those_with_an_estimated_ruler(scene_factory):
    need = "its rank needs 10 prior sessions with bars from their settled open to this minute, have 9"
    _, omitted, _ = labels(scene(scene_factory, at(9, 56), crossing_closes(7), prior_bars=history([crossing_closes(n) for n in range(9)])))
    assert omitted["open.settled_open_crosses"] == need
    # a crossing count is the day's own price, which no ruler scales: an estimated ruler keeps the session in
    sc = scene(scene_factory, at(9, 56), crossing_closes(7), prior_bars=CROSSED)
    got, _, _ = labels(replace(sc, prior_rulers={day: SigmaRuler(SIGMA, "live") for day in CROSSED}))
    assert "more often than 7 of the last 20 sessions had by this minute, middle third" in got["open.settled_open_crosses"]


# ---- open.chop

def one_way_share(k: int) -> list[float]:
    """Settling the open at 7700, then forty one-point minutes, 20 + k up and 20 - k down: the path nets 2k of the
    40 points it travels by 10:15 (path efficiency k / 20), then holds."""
    return [7700.0] * 5 + [7700.0 + i for i in range(1, 21 + k)] + [7720.0 + k - i for i in range(1, 21 - k)] + [7700.0 + 2 * k] * 30


# Twenty prior paths to 10:15 with efficiencies 0 to 0.95: 0.30 is more one-way than 6 of them (bottom third), 0.35
# than 7 (middle third), 0.70 than 14 (top third); an efficiency level with a session's does not beat it.
PATHS = history([one_way_share(k) for k in range(20)])


@pytest.mark.parametrize("k, verdict", [
    (6, "travelled 0.50 sigma to end 0.15 sigma above it (path efficiency 0.30, the net move over the distance travelled) and never "
        "crossed it; that path is more one-way than 6 of the last 20 sessions at this minute, bottom third: choppy"),
    (7, "travelled 0.50 sigma to end 0.17 sigma above it (path efficiency 0.35, the net move over the distance travelled) and never "
        "crossed it; that path is more one-way than 7 of the last 20 sessions at this minute, middle third: mixed"),
    (13, "travelled 0.50 sigma to end 0.33 sigma above it (path efficiency 0.65, the net move over the distance travelled) and never "
         "crossed it; that path is more one-way than 13 of the last 20 sessions at this minute, middle third: mixed"),
    (14, "travelled 0.50 sigma to end 0.35 sigma above it (path efficiency 0.70, the net move over the distance travelled) and never "
         "crossed it; that path is more one-way than 14 of the last 20 sessions at this minute, top third: one-way"),
])
def test_the_opens_path_is_ranked_against_the_prior_sessions_paths_at_this_minute(scene_factory, k, verdict):
    got, _, _ = labels(scene(scene_factory, at(10, 15), one_way_share(k), prior_bars=PATHS, bar_clock=True))
    assert got["open.chop"] == f"since the settled open 40 minutes ago price {verdict}"


def test_a_path_back_and_forth_across_the_open_is_choppy(scene_factory):
    got, _, _ = labels(scene(scene_factory, at(10, 15), crossing_closes(39), prior_bars=PATHS, bar_clock=True))
    assert got["open.chop"] == (
        "since the settled open 40 minutes ago price travelled 1.98 sigma to end 0.03 sigma below it (path efficiency 0.01, the net move "
        "over the distance travelled) and crossed it 39 times; that path is more one-way than 1 of the last 20 sessions at this minute, "
        "bottom third: choppy")


def test_the_opens_path_is_judged_from_0945_on_ten_sessions_that_moved(scene_factory):
    _, omitted, _ = labels(scene(scene_factory, at(9, 44), one_way_share(6), prior_bars=PATHS, bar_clock=True))
    assert omitted["open.chop"] == "the path since the open is judged from 09:45, when ten minutes of it exist"
    got, _, _ = labels(scene(scene_factory, at(9, 45), crossing_closes(9), prior_bars=PATHS, bar_clock=True))
    assert got["open.chop"] == ("since the settled open 10 minutes ago price travelled 0.47 sigma to end 0.03 sigma below it (path "
                                "efficiency 0.05, the net move over the distance travelled) and crossed it 9 times; that path is no more "
                                "one-way than any of the last 20 sessions at this minute, bottom third: choppy")
    # a prior session whose closes never moved has no path and is not counted; an estimated ruler keeps the session in
    moved = history([one_way_share(k) for k in range(9)] + [[7700.0] * 60] * 5)
    _, omitted, _ = labels(scene(scene_factory, at(10, 15), one_way_share(6), prior_bars=moved, bar_clock=True))
    assert omitted["open.chop"] == "its rank needs 10 prior sessions with bars from their settled open to this minute, have 9"
    sc = scene(scene_factory, at(10, 15), one_way_share(6), prior_bars=PATHS, bar_clock=True)
    got, _, _ = labels(replace(sc, prior_rulers={day: SigmaRuler(SIGMA, "vix") for day in PATHS}))
    assert got["open.chop"].endswith("more one-way than 6 of the last 20 sessions at this minute, bottom third: choppy")
    _, omitted, _ = labels(scene(scene_factory, at(10, 15), [7700.0] * 60, prior_bars=PATHS, bar_clock=True))
    assert omitted["open.chop"] == "price has not moved from the settled open, so there is no path to judge"


def test_the_nights_range_labels_are_not_built_rather_than_dark_since_their_bars_are_on_disk(full_scene):
    """Dark is for data no feed carries; the overnight store holds /ES's bars and only the reading code is missing."""
    from spx_jev.labels import gap_open
    from spx_jev.labels.registry import build_labels
    omitted = build_labels(full_scene).omitted
    for path in ("overnight.range", "overnight.price_vs_range"):
        assert path not in gap_open.DARK
        assert omitted[path].startswith("not built yet: the overnight store")
