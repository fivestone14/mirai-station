"""The volatility family: each label's sentence at every verdict, its omissions, and point in time."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row
from spx_jev.labels.rulers import SigmaRuler
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


# ---- the straddle: vol.straddle_reprice_30, vol.straddle_vs_clock, vol.ruler_event_load

def minutes_left(t) -> float:
    return (t.replace(hour=16, minute=0, second=0) - t).total_seconds() / 60.0


def straddle_for_share(share: float, t, em_open: float = 22.06) -> float:
    """The straddle left at ``t`` that is ``share`` times what the clock alone leaves of the opening straddle."""
    return em_open * share * math.sqrt(minutes_left(t) / 390.0)


def straddle_row(t, em_points: float, **over) -> dict:
    return make_row(t, 7700.0, range_ruler=ruler_block(em_points=em_points), **over)


def write_prior_diaries(root, rows_by_day: dict[str, list[dict]]):
    (root / "reversion").mkdir(parents=True, exist_ok=True)
    for day, rows in rows_by_day.items():
        (root / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return root


@pytest.mark.parametrize("em_then, words", [
    (15.00, "ended 6.1% richer than the clock alone would have left it, past the 5% repricing line"),
    (16.00, "ended 0.5% cheaper than the clock alone would have left it, within the 5% repricing line"),
    (17.50, "ended 9.1% cheaper than the clock alone would have left it, past the 5% repricing line"),
])
def test_the_straddle_is_repriced_against_what_the_clock_alone_takes_off_it(scene_factory, em_then, words):
    now = at(12, 30)                                   # 210 minutes left now, 240 then
    scene = scene_factory(now, flat_bars(180), row_over={"range_ruler": ruler_block(em_points=15.0 * math.sqrt(210 / 240) * 1.061)},
                          rows_before=[morning(), straddle_row(now - timedelta(minutes=30), em_then)])
    assert labels(scene)[0]["vol.straddle_reprice_30"] == f"over the last 30 minutes the same-day straddle for the rest of today {words}"


def test_the_repricing_is_omitted_without_the_straddle_30_minutes_ago(scene_factory):
    now = at(12, 30)
    scene = scene_factory(now, flat_bars(180), rows_before=[morning(), straddle_row(now - timedelta(minutes=50), 16.0)])
    assert labels(scene)[1]["vol.straddle_reprice_30"] == "no straddle left on the rows now and about 30 minutes ago (range_ruler.em_points)"


PRIOR_DAYS = [f"2026-09-{d:02d}" for d in range(17, 7, -1)]


def usual_straddle_state(tmp_path, clock=(11, 40), shares=(0.9, 0.95, 1.0, 1.05, 1.1)):
    """Prior diaries whose straddle at the clock is each of ``shares`` of the clock-scaled opening straddle,
    and a later row on each at twice that, which a read at the clock must not see."""
    rows = {}
    for day, share in zip(PRIOR_DAYS, shares * 2):
        t = at(*clock, day=day)
        rows[day] = [straddle_row(at(9, 31, day=day), 22.06), straddle_row(t, straddle_for_share(share, t)),
                     straddle_row(t + timedelta(minutes=1), 2 * straddle_for_share(share, t))]
    return write_prior_diaries(tmp_path, rows)


@pytest.mark.parametrize("share, words", [
    (0.78, "0.78 times its usual share of the opening straddle at 11:40 (its median on the last 10 sessions), under the 0.85 cheap line"),
    (1.00, "1.00 times its usual share of the opening straddle at 11:40 (its median on the last 10 sessions), between the 0.85 cheap and 1.15 rich lines"),
    (1.30, "1.30 times its usual share of the opening straddle at 11:40 (its median on the last 10 sessions), over the 1.15 rich line"),
])
def test_the_straddle_left_is_judged_against_its_usual_share_at_this_minute(scene_factory, tmp_path, share, words):
    now = at(11, 40)
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    scene = scene_factory(now, flat_bars(130), row_over={"range_ruler": ruler_block(em_points=straddle_for_share(share, now))},
                          rows_before=[morning()], prior_bars=prior)
    scene = replace(scene, state_dir=usual_straddle_state(tmp_path))
    assert labels(scene)[0]["vol.straddle_vs_clock"] == f"the same-day straddle for the rest of today is {words}"


def test_the_straddle_against_the_clock_is_omitted_without_its_history(scene_factory, tmp_path):
    now = at(11, 40)
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    scene = scene_factory(now, flat_bars(130), rows_before=[morning()], prior_bars=prior)
    assert labels(scene)[1]["vol.straddle_vs_clock"] == "no state folder to read the prior sessions' diaries from"
    thin = replace(scene, state_dir=usual_straddle_state(tmp_path), prior_bars={d: prior[d] for d in PRIOR_DAYS[:4]})
    assert labels(thin)[1]["vol.straddle_vs_clock"] == "needs 5 prior sessions with a straddle at this minute, have 4"
    estimated = replace(scene, state_dir=tmp_path, prior_rulers={d: SigmaRuler(75.0, "vix") for d in PRIOR_DAYS[:6]})
    assert labels(estimated)[1]["vol.straddle_vs_clock"] == "needs 5 prior sessions with a straddle at this minute, have 4"


FOMC_DAY = "2026-10-28"                                # the shipped calendar's FOMC decision, 14:00
EVENT_PRIOR_DAYS = [f"2026-10-{d:02d}" for d in range(27, 17, -1)]
TAPE_30 = 1.0 * math.sqrt(6)                           # flat bars with a 1-point wick: every 5-minute slice ranges 1 point


def straddle_for_load(load: float, t) -> float:
    """The straddle left at ``t`` whose 30-minute share is ``load`` times what a flat tape delivers."""
    return load * TAPE_30 * math.sqrt(minutes_left(t) / 30.0)


def ruler_scene(scene_factory, tmp_path, day: str, clock: tuple[int, int], load: float, swell: float, rows_before=(), prior_days=None):
    """A read at ``clock`` on ``day`` whose straddle prices ``load`` times the tape and whose anchor is ``swell``
    times the prior sessions' (75); the prior sessions priced 0.5 to 1.4 times their tape at the clock."""
    prior_days = prior_days or [f"{day[:8]}{int(day[8:]) - k:02d}" for k in range(1, 11)]
    now = at(*clock, day=day)
    rows = {}
    for k, d in enumerate(prior_days):
        t = at(*clock, day=d)
        rows[d] = [straddle_row(at(9, 31, day=d), 22.06), straddle_row(t, straddle_for_load(0.5 + 0.1 * k, t))]
    prior = {d: flat_bars(390, day=d, price=7700.0) for d in prior_days}
    first = make_row(at(9, 31, day=day), 7700.0, sigma=75.0 * swell)
    scene = scene_factory(now, flat_bars(int(minutes_left(at(9, 30, day=day)) - minutes_left(now)), day=day),
                          row_over={"range_ruler": ruler_block(em_points=straddle_for_load(load, now)), "sigma": 75.0 * swell},
                          rows_before=[first, *rows_before], prior_bars=prior)
    return replace(scene, state_dir=write_prior_diaries(tmp_path, rows), prior_rulers={d: SigmaRuler(75.0, "anchor") for d in prior_days})


def test_a_straddle_loaded_before_the_fed_reads_loaded(scene_factory, tmp_path):
    scene = ruler_scene(scene_factory, tmp_path, FOMC_DAY, (11, 30), load=1.92, swell=1.88, prior_days=EVENT_PRIOR_DAYS)
    assert labels(scene)[0]["vol.ruler_event_load"] == (
        "loaded before an event: the same-day straddle prices a 30-minute move 1.92 times what the tape's recent 5-minute ranges "
        "scale to, past the 1.5 loaded line, higher than 10 of the last 10 sessions at 11:30 (top fifth); the Fed's rate decision "
        "is at 14:00, still ahead; this morning's sigma ruler is 1.88 times its 10-session median, past the 1.3 swollen line")


def test_a_tape_outrunning_the_pre_release_straddle_reads_released(scene_factory, tmp_path):
    before = straddle_row(at(13, 59, day=FOMC_DAY, ss=30), straddle_for_load(0.8, at(13, 59, day=FOMC_DAY, ss=30)))
    scene = ruler_scene(scene_factory, tmp_path, FOMC_DAY, (14, 32), load=0.9, swell=1.4, rows_before=[before], prior_days=EVENT_PRIOR_DAYS)
    assert labels(scene)[0]["vol.ruler_event_load"] == (
        "released after an event: the same-day straddle prices a 30-minute move 0.90 times what the tape's recent 5-minute ranges "
        "scale to, under the 1.5 loaded line, higher than 4 of the last 10 sessions at 14:32 (between the fifths); the Fed's rate "
        "decision came out at 14:00, 32 minutes ago, inside the 120-minute digest window; the tape now moves 1.25 times what the "
        "straddle priced just before it, over the one-to-one line; this morning's sigma ruler is 1.40 times its 10-session median, "
        "past the 1.3 swollen line")


@pytest.mark.parametrize("load, swell, verdict, words", [
    (1.0, 1.40, "swollen with no event", "1.40 times its 10-session median, past the 1.3 swollen line"),
    (1.0, 0.75, "compressed", "0.75 times its 10-session median, at or under the 0.8 compressed line"),
    (1.0, 1.29, "normal", "1.29 times its 10-session median, between the 0.8 compressed and 1.3 swollen lines"),
    (1.92, 1.00, "normal", "1.00 times its 10-session median, between the 0.8 compressed and 1.3 swollen lines"),
])
def test_without_an_event_the_ruler_reads_swollen_compressed_or_normal(scene_factory, tmp_path, load, swell, verdict, words):
    scene = ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=load, swell=swell)
    got = labels(scene)[0]["vol.ruler_event_load"]
    assert got.startswith(f"{verdict}: ") and "no Fed event is on the calendar today" in got and got.endswith(f"sigma ruler is {words}")


def test_the_ruler_rank_reads_only_what_each_prior_session_had_by_the_clock(scene_factory, tmp_path):
    scene = ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=1.0, swell=1.0)
    for bars in scene.prior_bars.values():
        for b in bars[120:]:                           # from the 11:30 bar on, a wild tape the 11:30 read must not see
            b["high"], b["low"] = b["close"] + 50.0, b["close"] - 50.0
    assert "higher than 5 of the last 10 sessions at 11:30" in labels(scene)[0]["vol.ruler_event_load"]


def test_the_ruler_is_omitted_without_its_parts(scene_factory, tmp_path):
    scene = ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=1.0, swell=1.0)
    assert labels(replace(scene, state_dir=None))[1]["vol.ruler_event_load"] == "no state folder to read the prior sessions' diaries from"
    assert labels(replace(scene, prior_rulers={}))[1]["vol.ruler_event_load"] == (
        "needs the morning sigma ruler and 5 prior sessions' trusted anchors for its normal")
    assert labels(replace(scene, bars=scene.bars[:-5]))[1]["vol.ruler_event_load"] == (
        "needs the straddle left on the row, 30 minutes of session left and six finished 5-minute slices")
