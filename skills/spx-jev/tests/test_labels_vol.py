"""The volatility family: each label's sentence at every verdict, its omissions, and point in time."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row
from spx_jev.labels.vol import build_vol_labels

SIGMA = 75.0          # the morning anchor every fixture row carries


def ruler_block(vix: float = 15.0, **over) -> dict:
    """A diary row's range_ruler as the scanner writes it (2026-09-25 11:51: em_points 18.45, em_open 22.06, vix 15.47)."""
    return {"em_points": 18.45, "em_open": 22.06, "em_consumed": 1.08, "vol_carry": {"vix": vix, "vix3m": 18.3, "ratio": 0.845}, **over}


def diary_row(t, vix: float = 15.0, spot: float = 7700.0, **over) -> dict:
    return make_row(t, spot, range_ruler=ruler_block(vix), **over)


def morning() -> dict:
    """The day's first row, stamped before the 09:40 guard, so the anchor is trusted."""
    return diary_row(at(9, 31))


def labels(scene):
    got = build_vol_labels(scene)
    return {f"{g}.{k}": v for g, ks in got.state.items() for k, v in ks.items()}, got.omitted


# ---- vol.vix_change_30

@pytest.mark.parametrize("then, words", [
    (19.90, "VIX rose 0.10 points, 0.50% of its 20.0 level, under the 0.6% still line"),
    (19.80, "VIX rose 0.20 points, 1.00% of its 20.0 level, at or past the 0.6% still line and short of the 1.3% moving line"),
    (19.70, "VIX rose 0.30 points, 1.50% of its 20.0 level, at or past the 1.3% moving line and short of the 2.2% jump line"),
    (20.50, "VIX fell 0.50 points, 2.50% of its 20.0 level, at or past the 2.2% jump line"),
])
def test_the_vix_change_is_a_share_of_its_level_against_the_30_minute_lines(scene_factory, then, words):
    now = at(12, 30, ss=10)
    scene = scene_factory(now, flat_bars(180), row_over={"range_ruler": ruler_block(20.0)},
                          rows_before=[morning(), diary_row(now - timedelta(minutes=31), then)])
    assert labels(scene)[0]["vol.vix_change_30"] == f"over the last 30 minutes {words}"


def test_the_opening_lane_reads_ten_minutes_against_the_ten_minute_lines(scene_factory):
    now = at(9, 50)
    scene = scene_factory(now, flat_bars(20), row_over={"range_ruler": ruler_block(20.0)}, bar_clock=True,
                          rows_before=[morning(), diary_row(at(9, 40), 19.90)])
    assert labels(scene)[0]["vol.vix_change_30"] == ("over the last 10 minutes VIX rose 0.10 points, 0.50% of its 20.0 level, "
                                                     "at or past the 0.35% still line and short of the 0.75% moving line")


def test_a_share_on_a_line_reads_at_or_past_it_and_never_shows_across_it(scene_factory):
    now = at(12, 30, ss=10)
    on_line = scene_factory(now, flat_bars(180), row_over={"range_ruler": ruler_block(20.0)},
                            rows_before=[morning(), diary_row(now - timedelta(minutes=30), 20.26)])
    assert "at or past the 1.3% moving line" in labels(on_line)[0]["vol.vix_change_30"]
    just_under = scene_factory(now, flat_bars(180), row_over={"range_ruler": ruler_block(20.0)},
                               rows_before=[morning(), diary_row(now - timedelta(minutes=30), 19.7401)])
    assert "1.29% of its 20.0 level, at or past the 0.6% still line and short of the 1.3% moving line" in labels(just_under)[0]["vol.vix_change_30"]


def test_the_vix_change_is_omitted_without_a_row_near_the_window_start(scene_factory):
    now = at(12, 30, ss=10)
    stale = scene_factory(now, flat_bars(180), rows_before=[morning(), diary_row(now - timedelta(minutes=45), 14.0)])
    assert labels(stale)[1]["vol.vix_change_30"] == "no diary VIX now and about 30 minutes ago (range_ruler.vol_carry.vix)"
    no_vix = scene_factory(now, flat_bars(180), row_over={"range_ruler": {"em_points": 18.45}},
                           rows_before=[morning(), diary_row(now - timedelta(minutes=30), 14.0)])
    assert "vol.vix_change_30" in labels(no_vix)[1]


# ---- vol.vix_since_1400

@pytest.mark.parametrize("anchor, words", [
    (15.25, "since 14:00 VIX fell 0.25 points, 1.67% of its 15.0 level, past the 1.3% moving line"),
    (14.90, "since 14:00 VIX rose 0.10 points, 0.67% of its 15.0 level, inside the 1.3% moving line"),
])
def test_the_closing_vix_is_read_against_the_newest_row_by_1400(scene_factory, anchor, words):
    now = at(15, 2, ss=5)
    rows = [morning(), diary_row(at(13, 59, ss=20), anchor), diary_row(at(14, 1), 99.0)]   # the 14:01 row is after the anchor
    scene = scene_factory(now, flat_bars(330), rows_before=rows)
    assert labels(scene)[0]["vol.vix_since_1400"] == words


def test_the_closing_vix_is_omitted_before_1400_and_without_a_row_then(scene_factory):
    early = scene_factory(at(13, 32), flat_bars(240), rows_before=[morning()])
    assert labels(early)[1]["vol.vix_since_1400"] == "before 14:00"
    gap = scene_factory(at(15, 2), flat_bars(330), rows_before=[morning(), diary_row(at(13, 40), 15.3)])
    assert labels(gap)[1]["vol.vix_since_1400"] == "no diary VIX now and at 14:00 (range_ruler.vol_carry.vix)"


# ---- vol.vix_vs_price and vol.atm_iv_residual: 30-minute changes against what price explains

def fall_then_now(now, drop_sigma: float, minutes: int = 180) -> list[dict]:
    """Flat at 7700 until the bar that finished 30 minutes before ``now``, then a steady walk to 7700 - drop x sigma."""
    start = int((now - timedelta(minutes=30) - at(9, 30)).total_seconds() // 60)
    target = 7700.0 - drop_sigma * SIGMA
    return bars_from_closes([7700.0] * start + [7700.0 + (target - 7700.0) * (i + 1) / (minutes - start) for i in range(minutes - start)])


@pytest.mark.parametrize("vix_then, words", [
    (15.00, "VIX rose 0.35 points; the 0.12 sigma SPX drop alone would lift it about 0.13, so fear rose 0.22 points (1.44% of VIX) "
            "more than price explains, past the 1.3% line"),
    (15.20, "VIX rose 0.15 points; the 0.12 sigma SPX drop alone would lift it about 0.13, so fear rose 0.02 points (0.13% of VIX) "
            "more than price explains, inside the 1.3% line"),
    (15.60, "VIX fell 0.25 points; the 0.12 sigma SPX drop alone would lift it about 0.13, so fear rose 0.38 points (2.47% of VIX) "
            "less than price explains, past the 1.3% line"),
])
def test_the_vix_move_is_judged_beyond_what_the_spx_move_explains(scene_factory, vix_then, words):
    now = at(12, 30, ss=10)
    scene = scene_factory(now, fall_then_now(now, 0.12), row_over={"range_ruler": ruler_block(15.35)},
                          rows_before=[morning(), diary_row(now - timedelta(minutes=30), vix_then)])
    assert labels(scene)[0]["vol.vix_vs_price"] == f"over the last 30 minutes {words}"


def test_the_price_move_starts_at_the_last_bar_finished_30_minutes_ago(scene_factory):
    now = at(12, 30, ss=10)
    bars = fall_then_now(now, 0.12)
    start = int((now - timedelta(minutes=30) - at(9, 30)).total_seconds() // 60)
    bars[start]["close"] = 7000.0          # the 12:00 bar finishes at 12:01, after the window starts: it must not be the reference
    scene = scene_factory(now, bars, row_over={"range_ruler": ruler_block(15.35)},
                          rows_before=[morning(), diary_row(now - timedelta(minutes=30), 15.2)])
    assert "the 0.12 sigma SPX drop" in labels(scene)[0]["vol.vix_vs_price"]


def test_the_vix_against_price_is_omitted_without_the_row_30_minutes_ago(scene_factory):
    now = at(12, 30, ss=10)
    scene = scene_factory(now, fall_then_now(now, 0.12), rows_before=[morning()])
    assert labels(scene)[1]["vol.vix_vs_price"] == ("needs the morning sigma ruler, a finished bar 30 minutes ago and the diary VIX "
                                                    "now and then")


def test_an_estimated_ruler_says_so(scene_factory):
    now = at(12, 30, ss=10)
    late_first = diary_row(at(9, 45))                   # after the 09:40 guard: the earliest sigma_live stands in
    scene = scene_factory(now, fall_then_now(now, 0.12), row_over={"range_ruler": ruler_block(15.35)},
                          rows_before=[late_first, diary_row(now - timedelta(minutes=30), 15.2)])
    assert labels(scene)[0]["vol.vix_vs_price"].endswith("inside the 1.3% line; ruler estimated")


@pytest.mark.parametrize("iv_then, words", [
    (0.1450, "vol rose 0.80 points while price fell 0.12 sigma; the fall alone explains a rise of 0.26, so vol ended 0.54 points "
             "above what price explains, past the 0.5 line"),
    (0.1500, "vol rose 0.30 points while price fell 0.12 sigma; the fall alone explains a rise of 0.26, so vol ended 0.04 points "
             "above what price explains, inside the 0.5 line"),
    (0.1560, "vol fell 0.30 points while price fell 0.12 sigma; the fall alone explains a rise of 0.26, so vol ended 0.56 points "
             "below what price explains, past the 0.5 line"),
])
def test_same_day_vol_is_judged_beyond_what_the_price_move_explains(scene_factory, iv_then, words):
    now = at(12, 30, ss=10)
    scene = scene_factory(now, fall_then_now(now, 0.12), row_over={"atm_iv": 0.1530},
                          rows_before=[morning(), diary_row(now - timedelta(minutes=30), atm_iv=iv_then)])
    assert labels(scene)[0]["vol.atm_iv_residual"] == f"over the last 30 minutes same-day at-the-money {words}"


def test_same_day_vol_is_not_judged_in_the_last_hour_or_without_its_rows(scene_factory):
    late = at(15, 2)
    scene = scene_factory(late, fall_then_now(late, 0.12, minutes=330), rows_before=[morning(), diary_row(late - timedelta(minutes=30))])
    assert labels(scene)[1]["vol.atm_iv_residual"].startswith("the last hour of the 0DTE book")
    now = at(12, 30, ss=10)
    no_iv = scene_factory(now, fall_then_now(now, 0.12), row_over={"atm_iv": None}, rows_before=[morning(), diary_row(now - timedelta(minutes=30))])
    assert labels(no_iv)[1]["vol.atm_iv_residual"].startswith("needs the morning sigma ruler")


# ---- vol.vix_overnight_surprise: today's first VIX against the prior session's last, less the gap

def with_prior_diary(tmp_path, scene, day: str, last_vix: float):
    folder = tmp_path / "reversion"
    folder.mkdir(parents=True, exist_ok=True)
    rows = [diary_row(at(15, 58, day=day), 99.0), diary_row(at(15, 59, day=day), last_vix)]
    (folder / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return replace(scene, state_dir=tmp_path)


@pytest.mark.parametrize("first, words", [
    (15.29, "VIX's first print today was 15.29, 0.55 above yesterday's last 14.74; after this morning's 0.44 sigma gap up it would normally "
            "sit near 13.99, so it is 1.30 points richer than the gap implies, past the 0.4 line"),
    (14.20, "VIX's first print today was 14.20, 0.54 below yesterday's last 14.74; after this morning's 0.44 sigma gap up it would normally "
            "sit near 13.99, so it is 0.21 points richer than the gap implies, within the 0.4 line"),
    (13.40, "VIX's first print today was 13.40, 1.34 below yesterday's last 14.74; after this morning's 0.44 sigma gap up it would normally "
            "sit near 13.99, so it is 0.59 points cheaper than the gap implies, past the 0.4 line"),
])
def test_the_opening_vix_is_judged_against_what_the_gap_implies(scene_factory, tmp_path, first, words):
    now = at(9, 35, ss=20)
    scene = scene_factory(now, flat_bars(6, price=7733.0), row_over={"prior_close": 7700.0},
                          rows_before=[diary_row(at(9, 30, ss=28), first, prior_close=7700.0)])
    assert labels(with_prior_diary(tmp_path, scene, "2026-09-17", 14.74))[0]["vol.vix_overnight_surprise"] == words


def test_the_opening_vix_waits_for_the_settled_open_and_needs_the_prior_diary(scene_factory, tmp_path):
    before = scene_factory(at(9, 34, ss=40), flat_bars(6, price=7733.0), rows_before=[diary_row(at(9, 30, ss=28), 15.29)])
    assert labels(with_prior_diary(tmp_path, before, "2026-09-17", 14.74))[1]["vol.vix_overnight_surprise"].startswith("needs the settled open")
    no_state = scene_factory(at(9, 35, ss=20), flat_bars(6, price=7733.0), rows_before=[diary_row(at(9, 30, ss=28), 15.29)])
    assert labels(no_state)[1]["vol.vix_overnight_surprise"] == "needs the diary VIX on today's first row and the prior session's last"
