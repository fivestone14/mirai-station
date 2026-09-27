"""The range family's final-set labels: the first hour's edges, the last hour against the clock, the day's
range against its priced pace, the flat band's reach and the tape unit against the prior sessions."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import time, timedelta

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row, prior_sessions, write_state
from spx_jev.labels import range_size
from spx_jev.labels.range_size import NO_ANCHOR, build_range_size_labels
from spx_jev.labels.rulers import SigmaRuler
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


def _first_hour_scene(scene_factory, later, spot=None):
    return _scene(scene_factory, at(11, 0, ss=5), bars_from_closes(FIRST_HOUR + later, wick=0.0), spot=spot)


def test_the_first_hour_says_where_price_is_then_what_it_did_with_the_edges(scene_factory):
    ramp = [7710.0 + i for i in range(1, 31)]                         # up to 7740: first past the 0.03 sigma buffer at 10:32
    state, _ = _labels(_first_hour_scene(scene_factory, ramp))
    assert state["range.first_hour"] == ("price is above the first hour's high, 0.40 sigma beyond it; the first hour spanned 0.27 sigma; "
                                         "since 10:30 price broke above its high 27 minutes ago, reached 0.40 sigma beyond it, at or past "
                                         "the 0.25 sigma extension line, and never broke its low")
    stalled = [7710.0 + i for i in range(1, 11)] + [7720.0] * 20
    state, _ = _labels(_first_hour_scene(scene_factory, stalled))
    assert state["range.first_hour"].endswith("reached 0.13 sigma beyond it, short of the 0.25 sigma extension line, and never broke its low")


def test_the_extension_line_counts_at_it_and_the_break_buffer_does_not(scene_factory):
    at_line = [7710.0 + i for i in range(1, 19)] + [7728.75] * 12      # 18.75 points past the high: exactly 0.25 sigma
    state, _ = _labels(_first_hour_scene(scene_factory, at_line))
    assert "reached 0.25 sigma beyond it, at or past the 0.25 sigma extension line" in state["range.first_hour"]
    on_buffer = [7712.25] * 30                                        # exactly the 0.03 sigma buffer past the high: no break
    state, _ = _labels(_first_hour_scene(scene_factory, on_buffer, spot=7705.0))
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
                                         "short of the 0.25 sigma extension line; and below its low 7 minutes ago, reached 0.13 sigma "
                                         "beyond it, short of the 0.25 sigma extension line")


def test_the_first_hour_waits_for_its_sixty_bars(scene_factory):
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
                                            "time of day (70%), past the 67% wide line")
    state, _ = _labels(_hour_scene(scene_factory, 10.5, prior))
    assert state["range.hour_vs_clock"].endswith("wider than 6 of the last 10 sessions at this time of day (60%), short of the 67% wide line")


def test_the_wide_line_counts_at_it(scene_factory, monkeypatch):
    monkeypatch.setattr(range_size, "THIRD_HI", 0.6)
    prior = _swinging_sessions([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0])
    state, _ = _labels(_hour_scene(scene_factory, 10.5, prior))
    assert state["range.hour_vs_clock"].endswith("(60%), past the 60% wide line")


def test_the_hour_rank_skips_estimated_and_unruled_sessions_and_later_bars(scene_factory):
    prior = _swinging_sessions([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0])
    days = list(prior)
    rulers = {**_anchored(prior), days[0]: SigmaRuler(SIGMA, "live"), days[1]: None}
    spiked = {d: bars + [{**bars[-1], "ts": at(11, 30, day=d).isoformat(), "high": 9000.0}] for d, bars in prior.items()}
    spiked = {d: sorted(b, key=lambda x: x["ts"]) for d, b in spiked.items()}
    state, _ = _labels(_hour_scene(scene_factory, 11.5, spiked, rulers))
    assert "wider than 5 of the last 8 sessions at this time of day (62%)" in state["range.hour_vs_clock"]
    _, omitted = _labels(_hour_scene(scene_factory, 11.5, prior, rulers={}))
    assert omitted["range.hour_vs_clock"] == "needs 5 prior sessions with a morning ruler at this minute, have 0"
    _, omitted = _labels(_scene(scene_factory, at(10, 20, ss=5), flat_bars(50)))
    assert omitted["range.hour_vs_clock"] == "needs 60 minutes of session"
    gappy = [b for b in flat_bars(90) if not "10:10" <= b["ts"][11:16] < "10:35"]
    _, omitted = _labels(_scene(scene_factory, at(11, 0, ss=5), gappy))
    assert omitted["range.hour_vs_clock"] == "needs 40 finished bars in the last 60 minutes"


# ---- range.pace_vs_priced

def _pace_scene(scene_factory, range_points):
    """A read 97.5 minutes after the open (a quarter of a session: one sigma for the time is half of it, 37.5
    points) whose day has spanned ``range_points``."""
    closes = [7700.0, 7700.0 + range_points] + [7700.0 + range_points] * 95
    return _scene(scene_factory, at(11, 7, ss=30), bars_from_closes(closes, wick=0.0))


@pytest.mark.parametrize("points, band", [
    (15.0, "0.40 of a one-sigma move for the 97 minutes since the open: under the line, below the 0.7 under line"),
    (30.0, "0.80 of a one-sigma move for the 97 minutes since the open: near the line, between the 0.7 under line and 1"),
    (37.5, "1.00 of a one-sigma move for the 97 minutes since the open: over the line, between 1 and the 1.4 far-over line"),
    (60.0, "1.60 of a one-sigma move for the 97 minutes since the open: far over the line, past the 1.4 far-over line"),
])
def test_the_days_range_against_a_one_sigma_move_for_the_time(scene_factory, points, band):
    state, _ = _labels(_pace_scene(scene_factory, points))
    assert state["range.pace_vs_priced"] == f"today's range so far is {band}"


def test_each_pace_line_belongs_to_the_band_above_it(scene_factory, monkeypatch):
    monkeypatch.setattr(range_size, "RANGE_PACE_SLEEPY", 0.8)
    monkeypatch.setattr(range_size, "RANGE_PACE_WILD", 1.6)
    assert "near the line" in _labels(_pace_scene(scene_factory, 30.0))[0]["range.pace_vs_priced"]
    assert "far over the line" in _labels(_pace_scene(scene_factory, 60.0))[0]["range.pace_vs_priced"]


def test_the_pace_needs_a_finished_bar(scene_factory):
    _, omitted = _labels(replace(_pace_scene(scene_factory, 30.0), bars=[]))
    assert omitted["range.pace_vs_priced"] == "no finished bars yet"


# ---- ruler.flat_band_reach

def _reach_scene(scene_factory, wick, em_points=16.4, day="2026-09-18", now=None):
    """A flat tape whose 5-minute slices each span twice ``wick``, with ``em_points`` of straddle left."""
    now = now or at(12, 30, day=day, ss=10)
    minutes = int((now - at(9, 30, day=day)).total_seconds() // 60)
    bars = flat_bars(minutes, day=day, wick=wick)
    first = make_row(at(9, 31, day=day), 7700.0)
    return scene_factory(now, bars, rows_before=[first], row_over={"range_ruler": {"em_points": em_points}})


def test_the_flat_band_against_a_typical_half_hour_of_tape_and_straddle(scene_factory):
    # tape 3 points x sqrt(6) = 7.35, straddle 16.4 / 0.68 x sqrt(30 / 209.8) = 9.12: a typical 8.19 points, 0.11 sigma
    state, _ = _labels(_reach_scene(scene_factory, 1.5))
    assert state["ruler.flat_band_reach"] == ("the next-30-minute flat band is 0.07 sigma; a typical 30-minute move now (tape and straddle "
                                              "combined) is 0.11 sigma, so the band covers 0.64 of it, between the 0.53 narrow line and the "
                                              "0.70 wide line")
    assert state["ruler.flat_band_reach"] != _labels(_reach_scene(scene_factory, 1.5, em_points=40.0))[0]["ruler.flat_band_reach"]
    assert _labels(_reach_scene(scene_factory, 4.0))[0]["ruler.flat_band_reach"].endswith("so the band covers 0.39 of it, under the 0.53 narrow line")
    assert _labels(_reach_scene(scene_factory, 1.2, em_points=8.0))[0]["ruler.flat_band_reach"].endswith(
        "so the band covers 1.03 of it, over the 0.70 wide line")


def test_the_reach_lines_belong_to_the_middle(scene_factory, monkeypatch):
    scene = _reach_scene(scene_factory, 1.5)
    cover = range_size.NEXT_30_FLAT_BAND_SIGMA / (range_size.typical_move(scene, SigmaRuler(SIGMA, "anchor"), 30)[0] / SIGMA)
    monkeypatch.setattr(range_size, "FLAT_REACH_WIDE_30", cover)
    assert "between the 0.53 narrow line" in _labels(scene)[0]["ruler.flat_band_reach"]
    monkeypatch.setattr(range_size, "FLAT_REACH_NARROW_30", cover)
    assert "between the" in _labels(scene)[0]["ruler.flat_band_reach"]


def test_with_a_scheduled_event_still_ahead_the_tape_stands_alone(scene_factory):
    # 2026-09-16 carries the Fed's decision at 14:00 and its press conference at 14:30 (calendar/events.json)
    before = _labels(_reach_scene(scene_factory, 1.5, day="2026-09-16"))[0]["ruler.flat_band_reach"]
    assert before == ("the next-30-minute flat band is 0.07 sigma; a typical 30-minute move now (the tape alone, with a scheduled event "
                      "still ahead today) is 0.10 sigma, so the band covers 0.71 of it, over the 0.70 wide line")
    after = _labels(_reach_scene(scene_factory, 1.5, day="2026-09-16", now=at(14, 45, day="2026-09-16", ss=10)))[0]["ruler.flat_band_reach"]
    assert "(tape and straddle combined)" in after


def test_the_reach_is_omitted_without_a_straddle_or_a_running_tape(scene_factory):
    scene = _reach_scene(scene_factory, 1.5)
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
    assert omitted["tape.unit_vs_normal"] == "needs 5 prior sessions with a morning ruler at this minute, have 0"


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

def test_todays_range_against_the_last_five_sessions_at_this_time(scene_factory):
    state, _ = _labels(_scene(scene_factory, at(11, 0, ss=5), flat_bars(90, wick=40.0), prior_bars=prior_sessions(6)))
    assert state["range.today_vs_normal"] == "today's range so far is in the top third of the last 5 sessions at this time of day, larger than 5 of them"
    _, omitted = _labels(_scene(scene_factory, at(11, 0, ss=5), flat_bars(90), prior_bars=prior_sessions(2)))
    assert omitted["range.today_vs_normal"] == "needs 3 prior sessions of bars at this time of day, have 2"


# ---- every set label: the anchor, and nothing after the row

def test_without_a_morning_ruler_every_set_label_says_why(scene_factory):
    scene = scene_factory(at(11, 0, ss=5), flat_bars(90), row_over={"sigma_live": None}, bar_clock=True, last_read=at(10, 55))
    _, omitted = _labels(scene)
    assert {p: omitted[p] for p in SET_LABELS} == {p: NO_ANCHOR for p in SET_LABELS}


def test_an_estimated_ruler_is_named_in_the_sentence(scene_factory):
    state, _ = _labels(scene_factory(at(11, 0, ss=5), flat_bars(90)))
    assert state["range.pace_vs_priced"].endswith("(ruler estimated)")


def test_a_bar_that_finishes_after_the_row_never_counts(tmp_path):
    """Loaded as the service loads a read at 12:30:30: the running 12:30 bar and every later one (a spike above the first
    hour, a new range, a wide slice) are on disk, and the set labels read as if they were not."""
    day, now = "2026-09-18", at(12, 30, ss=10)
    prior = _swinging_sessions([2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0])
    bars = bars_from_closes(FIRST_HOUR + [7700.0 + (i % 7) for i in range(120)], wick=0.5)
    later = bars_from_closes([7700.0] * 180 + [7900.0] * 30, wick=30.0)[180:]
    for k, b in enumerate(later):
        b["ts"] = (now.replace(second=0) + timedelta(minutes=k)).isoformat()
    rows = [make_row(at(9, 31), 7700.0), make_row(now, float(bars[-1]["close"]))]

    def labels_on(state_dir, day_bars):
        write_state(state_dir, day, rows, day_bars, prior)
        for d in prior:
            (state_dir / "reversion" / f"{d}.jsonl").write_text(json.dumps(make_row(at(9, 31, day=d), 7700.0)) + "\n")
        return _labels(make_scene(state_dir, day, time(12, 30, 30), bar_clock=True))[0]

    seen, clean = labels_on(tmp_path / "all", bars + later), labels_on(tmp_path / "clean", bars)
    assert all(p in clean for p in SET_LABELS)
    assert {p: seen[p] for p in SET_LABELS} == {p: clean[p] for p in SET_LABELS}
