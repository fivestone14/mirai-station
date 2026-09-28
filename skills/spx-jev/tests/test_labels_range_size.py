"""The range family's final-set labels: the first hour's edges, the last hour against the clock, the day's
range against its priced pace, the flat band's reach and the tape unit against the prior sessions, each size
ranked against the same minute on the last sessions."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, time, timedelta

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row, prior_sessions, write_state
from spx_jev.labels.range_size import build_range_size_labels
from spx_jev.labels.rulers import NO_ANCHOR, SigmaRuler, tape_unit
from spx_jev.state_builder import make_scene

SIGMA = 75.0
SET_LABELS = ("range.first_hour", "range.hour_vs_clock", "range.pace_vs_priced", "ruler.flat_band_reach", "tape.unit_vs_normal")


def _scene(scene_factory, now, bars, **kw):
    """A read whose morning anchor is trusted: the day's first row is stamped 09:31, with the sigma the fixtures use."""
    first = make_row(at(9, 31), float(bars[0]["close"]))
    return scene_factory(now, bars, rows_before=[first], **kw)


def _labels(scene):
    ls = build_range_size_labels(scene)
    return {f"{g}.{k}": v for g, labels in ls.state.items() for k, v in labels.items()}, ls.omitted


def _anchored(prior: dict[str, list[dict]]) -> dict[str, SigmaRuler]:
    return {d: SigmaRuler(SIGMA, "anchor") for d in prior}


def _swinging_sessions(amplitudes: list[float]) -> dict[str, list[dict]]:
    """Prior sessions, newest first, each swinging evenly above and below 7700 by its own amplitude every
    minute, so any hour or 5-minute slice of the day spans twice it."""
    return {f"2026-09-{17 - k:02d}": bars_from_closes([7700.0 + (a if i % 2 else -a) for i in range(390)], day=f"2026-09-{17 - k:02d}", wick=0.0)
            for k, a in enumerate(amplitudes)}


# ---- range.first_hour

FIRST_HOUR = [7700.0] + [7690.0, 7710.0] * 29 + [7700.0]          # 60 bars from 09:30: high 7710, low 7690, 0.27 sigma
# How far past the first hour's high each prior session had gone by 11:00, in points; 0 is no break.
PRIOR_REACHES = [0.0, 0.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 35.0, 40.0]


def _breaking_sessions(reaches: list[float]) -> dict[str, list[dict]]:
    """Prior sessions, newest first, with today's first hour, each then holding ``reach`` points above that hour's high from 10:30."""
    return {f"2026-09-{17 - k:02d}": bars_from_closes(FIRST_HOUR + [7710.0 + r] * 330, day=f"2026-09-{17 - k:02d}", wick=0.0)
            for k, r in enumerate(reaches)}


def _first_hour_scene(scene_factory, later, spot=None, prior=None):
    prior = _breaking_sessions(PRIOR_REACHES) if prior is None else prior
    scene = _scene(scene_factory, at(11, 0, ss=5), bars_from_closes(FIRST_HOUR + later, wick=0.0), spot=spot, prior_bars=prior)
    return replace(scene, prior_rulers=_anchored(prior))


def test_the_first_hour_says_where_price_is_then_ranks_how_far_it_went_past_an_edge(scene_factory):
    ramp = [7710.0 + i for i in range(1, 31)]                         # up to 7740: first past the 0.03 sigma buffer at 10:32
    state, _ = _labels(_first_hour_scene(scene_factory, ramp))
    assert state["range.first_hour"] == ("price is above the first hour's high, 0.40 sigma beyond it; the first hour spanned 0.27 sigma; "
                                         "since 10:30 price broke above its high 27 minutes ago, reached 0.40 sigma beyond it, further "
                                         "than 8 of the last 10 sessions had gone past their first hour by this minute, top third, and "
                                         "never broke its low")
    stalled = [7710.0 + i for i in range(1, 11)] + [7720.0] * 20      # 10 points past: beyond the three sessions that never broke and the 5
    state, _ = _labels(_first_hour_scene(scene_factory, stalled))
    assert state["range.first_hour"].endswith("reached 0.13 sigma beyond it, further than 4 of the last 10 sessions had gone past their "
                                              "first hour by this minute, middle third, and never broke its low")


def test_a_reach_ties_with_a_session_that_went_as_far_and_the_break_buffer_is_no_break(scene_factory):
    as_far = [7710.0 + i for i in range(1, 21)] + [7730.0] * 10       # 20 points past: level with one session, beyond six
    state, _ = _labels(_first_hour_scene(scene_factory, as_far))
    assert "reached 0.27 sigma beyond it, further than 6 of the last 10 sessions had gone past their first hour by this minute, middle third" \
        in state["range.first_hour"]
    on_buffer = [7712.25] * 30                                        # exactly the 0.03 sigma buffer past the high: no break
    state, _ = _labels(_first_hour_scene(scene_factory, on_buffer, spot=7705.0, prior={}))
    assert state["range.first_hour"] == ("price is inside the first hour's range; the first hour spanned 0.27 sigma; since 10:30 no bar "
                                         "has gone more than the 0.03 sigma break buffer past either edge")


def test_a_break_back_inside_and_a_break_both_ways(scene_factory):
    failed = [7710.0 + i for i in range(1, 11)] + [7700.0] * 20
    state, _ = _labels(_first_hour_scene(scene_factory, failed))
    assert state["range.first_hour"].startswith("price is back inside the first hour's range; the first hour spanned 0.27 sigma; "
                                                "since 10:30 price broke above its high 27 minutes ago")
    both = [7710.0 + i for i in range(1, 11)] + [7700.0 - i for i in range(1, 21)]
    state, _ = _labels(_first_hour_scene(scene_factory, both))
    assert state["range.first_hour"] == ("price is below the first hour's low, 0.13 sigma beyond it; the first hour spanned 0.27 sigma; "
                                         "since 10:30 price broke both edges: above its high 27 minutes ago, reached 0.13 sigma beyond it, "
                                         "further than 4 of the last 10 sessions had gone past their first hour by this minute, middle "
                                         "third; and below its low 7 minutes ago, reached 0.13 sigma beyond it, further than 4 of the last "
                                         "10 sessions had gone past their first hour by this minute, middle third")


def test_a_break_waits_for_ten_ranked_sessions_and_the_first_hour_for_its_sixty_bars(scene_factory):
    ramp = [7710.0 + i for i in range(1, 31)]
    nine = _breaking_sessions(PRIOR_REACHES[:9])
    _, omitted = _labels(_first_hour_scene(scene_factory, ramp, prior=nine))
    assert omitted["range.first_hour"] == "its rank needs 10 prior sessions with a morning ruler and a first hour of bars at this minute, have 9"
    _, omitted = _labels(_scene(scene_factory, at(10, 20, ss=5), flat_bars(50)))
    assert omitted["range.first_hour"] == "needs the first hour's 60 finished bars, have 50"


# ---- range.hour_vs_clock

def _hour_scene(scene_factory, amplitude, prior, rulers=None):
    """A read at 11:00 whose last hour swings by ``amplitude`` either side of 7700."""
    bars = bars_from_closes([7700.0 + (amplitude if i % 2 else -amplitude) for i in range(90)], wick=0.0)
    scene = _scene(scene_factory, at(11, 0, ss=5), bars, prior_bars=prior)
    return replace(scene, prior_rulers=_anchored(prior) if rulers is None else rulers)


def test_the_last_hour_is_ranked_against_the_same_minute_of_the_prior_sessions(scene_factory):
    prior = _swinging_sessions([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0])
    state, _ = _labels(_hour_scene(scene_factory, 11.5, prior))       # 23 points: wider than 7 of the 10
    assert state["range.hour_vs_clock"] == ("the last hour's high-low range is 0.31 sigma, wider than 7 of the last 10 sessions at this "
                                            "time of day, top third")
    state, _ = _labels(_hour_scene(scene_factory, 10.5, prior))
    assert state["range.hour_vs_clock"].endswith("wider than 6 of the last 10 sessions at this time of day, middle third")


@pytest.mark.parametrize("amplitude, band", [(8.5, "wider than 4 of the last 15 sessions at this time of day, bottom third"),
                                             (9.5, "wider than 5 of the last 15 sessions at this time of day, middle third"),
                                             (14.5, "wider than 10 of the last 15 sessions at this time of day, middle third"),
                                             (15.5, "wider than 11 of the last 15 sessions at this time of day, top third")])
def test_a_rank_at_a_third_or_two_thirds_is_the_middle_third(scene_factory, amplitude, band):
    prior = _swinging_sessions([5.0 + k for k in range(15)])
    assert _labels(_hour_scene(scene_factory, amplitude, prior))[0]["range.hour_vs_clock"].endswith(band)


def test_the_hour_rank_skips_estimated_and_unruled_sessions_and_later_bars(scene_factory):
    prior = _swinging_sessions([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
    days = list(prior)
    rulers = {**_anchored(prior), days[0]: SigmaRuler(SIGMA, "live"), days[1]: None}
    spiked = {d: bars + [{**bars[-1], "ts": at(11, 30, day=d).isoformat(), "high": 9000.0}] for d, bars in prior.items()}
    spiked = {d: sorted(b, key=lambda x: x["ts"]) for d, b in spiked.items()}
    state, _ = _labels(_hour_scene(scene_factory, 11.5, spiked, rulers))
    assert "wider than 5 of the last 10 sessions at this time of day, middle third" in state["range.hour_vs_clock"]   # not 7 of 12
    _, omitted = _labels(_hour_scene(scene_factory, 11.5, prior, rulers={}))
    assert omitted["range.hour_vs_clock"] == "its rank needs 10 prior sessions with a morning ruler and an hour of bars at this minute, have 0"
    _, omitted = _labels(_scene(scene_factory, at(10, 20, ss=5), flat_bars(50)))
    assert omitted["range.hour_vs_clock"] == "needs 60 minutes of session"
    gappy = [b for b in flat_bars(90) if not "10:10" <= b["ts"][11:16] < "10:35"]
    _, omitted = _labels(_scene(scene_factory, at(11, 0, ss=5), gappy))
    assert omitted["range.hour_vs_clock"] == "needs 40 finished bars in the last 60 minutes"


# ---- range.pace_vs_priced

# Prior sessions whose day by 11:07 spanned 6, 12, ... 60 points: 0.16 to 1.60 of a one-sigma move for the time.
PACE_SESSIONS = [3.0 * k for k in range(1, 11)]


def _pace_scene(scene_factory, range_points, prior=None):
    """A read 97.5 minutes after the open (a quarter of a session: one sigma for the time is half of it, 37.5
    points) whose day has spanned ``range_points``."""
    prior = _swinging_sessions(PACE_SESSIONS) if prior is None else prior
    closes = [7700.0, 7700.0 + range_points] + [7700.0 + range_points] * 95
    scene = _scene(scene_factory, at(11, 7, ss=30), bars_from_closes(closes, wick=0.0), prior_bars=prior)
    return replace(scene, prior_rulers=_anchored(prior))


@pytest.mark.parametrize("points, band", [
    (15.0, "0.40 of a one-sigma move for the 97 minutes since the open, larger than 2 of the last 10 sessions at this time of day, "
           "bottom third: under the usual pace"),
    (30.0, "0.80 of a one-sigma move for the 97 minutes since the open, larger than 4 of the last 10 sessions at this time of day, "
           "middle third: near the usual pace"),
    (45.0, "1.20 of a one-sigma move for the 97 minutes since the open, larger than 7 of the last 10 sessions at this time of day, "
           "top third: over the usual pace"),
    (60.0, "1.60 of a one-sigma move for the 97 minutes since the open, larger than 9 of the last 10 sessions at this time of day, "
           "top third and in the top fifth: far over the usual pace"),
])
def test_the_days_range_for_the_time_is_ranked_against_the_same_minute(scene_factory, points, band):
    state, _ = _labels(_pace_scene(scene_factory, points))
    assert state["range.pace_vs_priced"] == f"today's range so far is {band}"


def test_the_top_fifth_counts_at_its_edge_and_the_pace_needs_ten_sessions(scene_factory):
    assert _labels(_pace_scene(scene_factory, 50.0))[0]["range.pace_vs_priced"].endswith(
        "larger than 8 of the last 10 sessions at this time of day, top third and in the top fifth: far over the usual pace")
    _, omitted = _labels(_pace_scene(scene_factory, 30.0, prior=_swinging_sessions(PACE_SESSIONS[:9])))
    assert omitted["range.pace_vs_priced"] == "its rank needs 10 prior sessions with a morning ruler and bars at this minute, have 9"


def test_the_pace_needs_a_finished_bar(scene_factory):
    _, omitted = _labels(replace(_pace_scene(scene_factory, 30.0), bars=[]))
    assert omitted["range.pace_vs_priced"] == "no finished bars yet"


# ---- ruler.flat_band_reach

# The prior sessions' 5-minute slices each span twice their wick: a wider tape, a larger typical move, a smaller share for the band.
REACH_WICKS = [1.0, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8, 2.0, 2.2, 2.4]


def _reach_days(state_dir, today: str, wicks: list[float], em_points: float = 16.4) -> dict[str, list[dict]]:
    """Prior sessions before ``today``, newest first, of flat tape with their ``wicks``, each with a diary row every 5 minutes
    carrying ``em_points`` of straddle left, written under ``state_dir``."""
    (state_dir / "reversion").mkdir(parents=True, exist_ok=True)
    first = date.fromisoformat(today)
    prior = {}
    for k, wick in enumerate(wicks):
        day = (first - timedelta(days=k + 1)).isoformat()
        prior[day] = flat_bars(390, day=day, wick=wick)
        rows = [make_row(at(9, 31, day=day) + timedelta(minutes=5 * m), 7700.0, range_ruler={"em_points": em_points}) for m in range(78)]
        (state_dir / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return prior


def _reach_scene(scene_factory, wick, em_points=16.4, day="2026-09-18", now=None, state_dir=None, wicks=REACH_WICKS):
    """A flat tape whose 5-minute slices each span twice ``wick``, with ``em_points`` of straddle left, ranked against prior
    sessions written under ``state_dir`` (none without it)."""
    now = now or at(12, 30, day=day, ss=10)
    minutes = int((now - at(9, 30, day=day)).total_seconds() // 60)
    bars = flat_bars(minutes, day=day, wick=wick)
    first = make_row(at(9, 31, day=day), 7700.0)
    prior = _reach_days(state_dir, day, wicks) if state_dir else {}
    scene = scene_factory(now, bars, rows_before=[first], row_over={"range_ruler": {"em_points": em_points}}, prior_bars=prior)
    return replace(scene, prior_rulers=_anchored(prior), state_dir=state_dir)


def test_the_flat_band_against_a_typical_half_hour_and_hour_ranked_against_the_same_minute(scene_factory, tmp_path):
    # 30 minutes: tape 3 points x sqrt(6) = 7.35, straddle 16.4 / 0.68 x sqrt(30 / 209.8) = 9.12: a typical 8.19 points, 0.11 sigma
    # 60 minutes: tape 3 points x sqrt(12) = 10.39, straddle 16.4 / 0.68 x sqrt(60 / 209.8) = 12.90: a typical 11.58 points, 0.15 sigma
    # the prior sessions with a wider tape (wicks past 1.5) have a larger typical move, so the band covers less of theirs
    state, _ = _labels(_reach_scene(scene_factory, 1.5, state_dir=tmp_path))
    assert state["ruler.flat_band_reach"] == ("the next-30-minute flat band is 0.07 sigma; a typical 30-minute move now (tape and straddle "
                                              "combined) is 0.11 sigma, so the band covers 0.64 of it, more than on 6 of the last 10 "
                                              "sessions at this time of day, middle third; the next-60-minute flat band is 0.11 sigma; a "
                                              "typical 60-minute move is 0.15 sigma, so the band covers 0.71 of it, more than on 6 of the "
                                              "last 10 sessions at this time of day, middle third")
    busy = _labels(_reach_scene(scene_factory, 4.0, state_dir=tmp_path))[0]["ruler.flat_band_reach"]
    assert "so the band covers 0.39 of it, more than on 0 of the last 10 sessions at this time of day, bottom third; " in busy
    dead = _labels(_reach_scene(scene_factory, 1.2, em_points=8.0, state_dir=tmp_path))[0]["ruler.flat_band_reach"]
    assert "so the band covers 1.03 of it, more than on 10 of the last 10 sessions at this time of day, top third; " in dead


def test_the_reach_needs_ten_prior_sessions_with_a_typical_move_then(scene_factory, tmp_path):
    _, omitted = _labels(_reach_scene(scene_factory, 1.5))
    assert omitted["ruler.flat_band_reach"] == "its rank needs 10 prior sessions with a typical 30-minute move at this minute, have 0"
    _, omitted = _labels(_reach_scene(scene_factory, 1.5, state_dir=tmp_path, wicks=REACH_WICKS[:9]))
    assert omitted["ruler.flat_band_reach"] == "its rank needs 10 prior sessions with a typical 30-minute move at this minute, have 9"


def test_with_a_scheduled_event_still_ahead_the_tape_stands_alone(scene_factory, tmp_path):
    # 2026-09-16 carries the Fed's decision at 14:00 and its press conference at 14:30 (calendar/events.json)
    before = _labels(_reach_scene(scene_factory, 1.5, day="2026-09-16", state_dir=tmp_path))[0]["ruler.flat_band_reach"]
    assert before.startswith("the next-30-minute flat band is 0.07 sigma; a typical 30-minute move now (the tape alone, with a scheduled "
                             "event still ahead today) is 0.10 sigma, so the band covers 0.71 of it")
    after = _labels(_reach_scene(scene_factory, 1.5, day="2026-09-16", now=at(14, 45, day="2026-09-16", ss=10),
                                 state_dir=tmp_path))[0]["ruler.flat_band_reach"]
    assert "(tape and straddle combined)" in after


def test_the_reach_is_omitted_without_a_straddle_or_a_running_tape(scene_factory, tmp_path):
    scene = _reach_scene(scene_factory, 1.5, state_dir=tmp_path)
    no_em = replace(scene, row={**scene.row, "range_ruler": {"em_open": 22.0}})
    assert _labels(no_em)[1]["ruler.flat_band_reach"] == "row carries no straddle left (range_ruler.em_points)"
    stopped = replace(scene, bars=[b for b in scene.bars if b["ts"] < at(12, 0).isoformat()])
    assert _labels(stopped)[1]["ruler.flat_band_reach"] == "no tape unit this read: the bars have stopped"


# ---- tape.unit_vs_normal

def _unit_scene(scene_factory, now, wick, prior, rulers=None):
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    scene = _scene(scene_factory, now, flat_bars(minutes, wick=wick), prior_bars=prior, bar_clock=True, last_read=now - timedelta(minutes=5))
    return replace(scene, prior_rulers=_anchored(prior) if rulers is None else rulers)


def test_the_tape_unit_is_ranked_in_sigma_against_the_same_minute(scene_factory):
    prior = _swinging_sessions([2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5])   # 5-minute slices spanning 4 to 13 points
    state, omitted = _labels(_unit_scene(scene_factory, at(10, 5), 2.6, prior))      # 5.2 points
    assert state["tape.unit_vs_normal"] == ("the tape unit (the middle of the last three 5-minute ranges) is 0.07 sigma, wider than 2 of "
                                            "the last 10 sessions at 10:05, in the bottom third")
    state, _ = _labels(_unit_scene(scene_factory, at(10, 5), 7.0, prior))
    assert state["tape.unit_vs_normal"].endswith("is 0.19 sigma, wider than all 10 of the last 10 sessions at 10:05, in the top third")
    _, omitted = _labels(_unit_scene(scene_factory, at(10, 5), 2.6, prior, rulers={}))
    assert omitted["tape.unit_vs_normal"] == "needs 10 prior sessions with a morning ruler at this minute, have 0"


def test_the_context_line_carries_the_same_unit_rank_as_the_label(scene_factory):
    """One rank of the unit: the one the sum's context line carries (tape_unit) is the label's, in each
    session's own sigma with an estimated session left out, not a rank in points."""
    prior = _swinging_sessions([2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0])
    days = list(prior)
    rulers = {d: SigmaRuler(SIGMA * 2 if k < 3 else SIGMA, "anchor") for k, d in enumerate(days)}
    rulers[days[-1]] = SigmaRuler(SIGMA, "vix")
    scene = _unit_scene(scene_factory, at(10, 5), 2.6, prior, rulers=rulers)
    rank = tape_unit(scene)["rank"]
    assert rank == {"band": "bottom third", "higher_than": 3, "of": 10}
    assert _labels(scene)[0]["tape.unit_vs_normal"].endswith("wider than 3 of the last 10 sessions at 10:05, in the bottom third")
    fewer = {d: rulers[d] for d in days[1:]}                                        # nine sessions left to rank against
    assert "rank" not in tape_unit(_unit_scene(scene_factory, at(10, 5), 2.6, {d: prior[d] for d in days[1:]}, rulers=fewer))


def test_the_tape_unit_is_not_described_while_held_or_off_the_bar_clock(scene_factory):
    prior = _swinging_sessions([2.0, 2.5, 3.0, 3.5, 4.0, 4.5])
    _, omitted = _labels(_unit_scene(scene_factory, at(9, 44), 2.6, prior))
    assert omitted["tape.unit_vs_normal"] == "the tape unit is held until 09:45, when three 5-minute slices first exist"
    stopped = _unit_scene(scene_factory, at(10, 5), 2.6, prior)
    stopped = replace(stopped, bars=[b for b in stopped.bars if b["ts"] < at(10, 0).isoformat()])
    assert _labels(stopped)[1]["tape.unit_vs_normal"] == "no tape unit this read: the bars have stopped"
    live = replace(_unit_scene(scene_factory, at(10, 5), 2.6, prior), bar_clock=False)
    state, omitted = _labels(live)
    assert "tape.unit_vs_normal" not in state and "tape.unit_vs_normal" not in omitted


# ---- range.today_vs_normal

def test_todays_range_against_the_last_sessions_at_this_time(scene_factory):
    state, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), flat_bars(90, wick=40.0), prior_bars=prior_sessions(10)))
    assert state["range.today_vs_normal"] == ("today's range so far is 1.04% of its opening price, larger than 10 of the last 10 sessions "
                                              "at this time of day, top third")
    swinging = _swinging_sessions([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0])      # 10 to 28 points by 11:00
    state, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), flat_bars(90, wick=9.5), prior_bars=swinging))
    assert state["range.today_vs_normal"].endswith("larger than 5 of the last 10 sessions at this time of day, middle third")
    _, omitted = _labels(_scene(scene_factory, at(11, 0, ss=5), flat_bars(90), prior_bars=prior_sessions(9)))
    assert omitted["range.today_vs_normal"] == "its rank needs 10 prior sessions with bars at this time of day, have 9"


# ---- every set label: the anchor, and nothing after the row

def test_without_a_morning_ruler_every_set_label_says_why(scene_factory):
    scene = scene_factory(at(11, 0, ss=5), flat_bars(90), row_over={"sigma_live": None}, bar_clock=True, last_read=at(10, 55))
    _, omitted = _labels(scene)
    assert {p: omitted[p] for p in SET_LABELS} == {p: NO_ANCHOR for p in SET_LABELS}


def test_an_estimated_ruler_is_named_in_the_sentence(scene_factory):
    prior = _swinging_sessions(PACE_SESSIONS)
    state, _ = _labels(replace(scene_factory(at(11, 0, ss=5), flat_bars(90), prior_bars=prior), prior_rulers=_anchored(prior)))
    assert state["range.pace_vs_priced"].endswith("(ruler estimated)")


def test_a_bar_that_finishes_after_the_row_never_counts(tmp_path):
    """Loaded as the service loads a read at 12:30:30: the running 12:30 bar and every later one (a spike above the first
    hour, a new range, a wide slice) are on disk, and the set labels read as if they were not."""
    day, now = "2026-09-18", at(12, 30, ss=10)
    prior = _swinging_sessions([2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5])
    bars = bars_from_closes(FIRST_HOUR + [7700.0 + (i % 7) for i in range(120)], wick=0.5)
    later = bars_from_closes([7700.0] * 180 + [7900.0] * 30, wick=30.0)[180:]
    for k, b in enumerate(later):
        b["ts"] = (now.replace(second=0) + timedelta(minutes=k)).isoformat()
    rows = [make_row(at(9, 31), 7700.0), make_row(now, float(bars[-1]["close"]))]

    def labels_on(state_dir, day_bars):
        write_state(state_dir, day, rows, day_bars, prior)
        for d in prior:
            diary = [make_row(at(9, 31, day=d), 7700.0), make_row(at(12, 25, day=d), 7700.0)]
            (state_dir / "reversion" / f"{d}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in diary))
        return _labels(make_scene(state_dir, day, time(12, 30, 30), bar_clock=True))[0]

    seen, clean = labels_on(tmp_path / "all", bars + later), labels_on(tmp_path / "clean", bars)
    assert all(p in clean for p in SET_LABELS)
    assert {p: seen[p] for p in SET_LABELS} == {p: clean[p] for p in SET_LABELS}
