"""The volatility family: each label's sentence at every verdict, its omissions, and point in time."""
from __future__ import annotations

import gzip
import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import DAY, at, bars_from_closes, flat_bars, make_row
from spx_jev import events
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.labels.vol import build_vol_labels
from spx_jev.labels.vol_sources import black_price
from spx_jev.state_builder import MarketContext

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


def test_the_first_opening_read_measures_from_the_opens_first_print(scene_factory):
    now = at(9, 40)
    scene = scene_factory(now, flat_bars(10), row_over={"range_ruler": ruler_block(20.0)}, bar_clock=True,
                          rows_before=[diary_row(at(9, 30, ss=28), 19.90)])
    assert labels(scene)[0]["vol.vix_change_30"].startswith("over the last 10 minutes VIX rose 0.10 points")


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
    (15.00, "VIX ended 0.22 points (1.44% of VIX) above what price explains, past the 1.3% line: it rose 0.35 points, and the "
            "0.12 sigma SPX drop alone would lift it about 0.13"),
    (15.20, "VIX ended 0.02 points (0.13% of VIX) above what price explains, inside the 1.3% line: it rose 0.15 points, and the "
            "0.12 sigma SPX drop alone would lift it about 0.13"),
    (15.60, "VIX ended 0.38 points (2.47% of VIX) below what price explains, past the 1.3% line: it fell 0.25 points, and the "
            "0.12 sigma SPX drop alone would lift it about 0.13"),
    (15.40, "VIX ended 0.18 points (1.17% of VIX) below what price explains, inside the 1.3% line: it fell 0.05 points, and the "
            "0.12 sigma SPX drop alone would lift it about 0.13"),
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
    assert labels(scene)[0]["vol.vix_vs_price"].endswith("would lift it about 0.13; ruler estimated")


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


# ---- vol.vix_on_shock: VIX since just before the shock family's burst

def shocked(now_min: int = 151, drop: float = 18.0) -> list[dict]:
    """Closes jittering half a point from the open, then an ``drop``-point fall over the five bars from 11:37,
    then jittering again: a 0.24 sigma burst many times the hour before's minute-to-minute movement."""
    closes = []
    for i in range(now_min):
        base = 7700.0 - drop * min(max(i - 126, 0), 5) / 5
        closes.append(base + 0.5 * (i % 2))
    return bars_from_closes(closes)


@pytest.mark.parametrize("vix_now, words", [
    (15.52, "VIX ended 0.26 points above what price explains, past the 0.15-point rule: it rose 0.52 points, and the 0.24 sigma "
            "drop alone would lift it 0.26"),
    (15.30, "VIX ended 0.04 points above what price explains, within the 0.15-point rule: it rose 0.30 points, and the 0.24 sigma "
            "drop alone would lift it 0.26"),
    (15.05, "VIX ended 0.21 points below what price explains, past the 0.15-point rule: it rose 0.05 points, and the 0.24 sigma "
            "drop alone would lift it 0.26"),
])
def test_the_vix_reaction_to_a_shock_is_judged_beyond_what_the_drop_explains(scene_factory, vix_now, words):
    now = at(12, 1)
    rows = [morning(), diary_row(at(11, 30, ss=40), 15.0), diary_row(at(11, 33), 15.4)]     # the 11:33 row is after 11:31
    scene = scene_factory(now, shocked(), row_over={"range_ruler": ruler_block(vix_now)}, rows_before=rows)
    assert labels(scene)[0]["vol.vix_on_shock"] == f"since just before the shock, at 11:31, {words}"


def test_the_vix_reaction_is_omitted_without_a_shock_in_the_last_hour(scene_factory):
    quiet = scene_factory(at(12, 1), shocked(drop=0.0), rows_before=[morning()])
    assert labels(quiet)[1]["vol.vix_on_shock"] == "no shock in the last 60 minutes (shock.burst)"
    later = scene_factory(at(12, 50), shocked(now_min=200), rows_before=[morning(), diary_row(at(11, 30, ss=40), 15.0)])
    assert labels(later)[1]["vol.vix_on_shock"] == "no shock in the last 60 minutes (shock.burst)"


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


FOMC_DAY = "2026-10-28"
EVENT_PRIOR_DAYS = [f"2026-10-{d:02d}" for d in range(27, 17, -1)]
FOMC_AND_PRESSER = [("14:00", "FOMC", 1), ("14:30", "FOMC_PRESSER", 1)]
ON_DAY, UNCOVERED = events.on_day, events.uncovered
TAPE_30 = 1.0 * math.sqrt(6)                           # flat bars with a 1-point wick: every 5-minute slice ranges 1 point


def calendar(tmp_path, monkeypatch, day: str, rows: list[tuple[str, str, object]], covers_through: str = "2026-12-31"):
    """``day``'s calendar rows as (time, kind, tier), read in place of the shipped calendar."""
    path = tmp_path / "events.json"
    path.write_text(json.dumps({"covers_through": covers_through,
                                "events": [{"date": day, "time_et": t, "kind": k, "tier": tier} for t, k, tier in rows]}))
    events._load.cache_clear()
    monkeypatch.setattr(events, "on_day", lambda d: ON_DAY(d, path))
    monkeypatch.setattr(events, "uncovered", lambda d: UNCOVERED(d, path))


def straddle_for_load(load: float, t) -> float:
    """The straddle left at ``t`` whose 30-minute share is ``load`` times what a flat tape delivers."""
    return load * TAPE_30 * math.sqrt(minutes_left(t) / 30.0)


def ruler_scene(scene_factory, tmp_path, day: str, clock: tuple[int, int], load: float, swell: float, rows_before=(), prior_days=None,
                prior_before_fed: float | None = None):
    """A read at ``clock`` on ``day`` whose straddle prices ``load`` times the tape and whose anchor is ``swell``
    times the prior sessions' (75); the prior sessions priced 0.5 to 1.4 times their tape at the clock and, with
    ``prior_before_fed``, that many times it just before 14:00."""
    prior_days = prior_days or [f"{day[:8]}{int(day[8:]) - k:02d}" for k in range(1, 11)]
    now = at(*clock, day=day)
    rows = {}
    for k, d in enumerate(prior_days):
        t, before = at(*clock, day=d), at(13, 59, day=d, ss=30)
        rows[d] = [straddle_row(at(9, 31, day=d), 22.06), straddle_row(t, straddle_for_load(0.5 + 0.1 * k, t))]
        if prior_before_fed is not None:
            rows[d].insert(1, straddle_row(before, straddle_for_load(prior_before_fed, before)))
    prior = {d: flat_bars(390, day=d, price=7700.0) for d in prior_days}
    first = make_row(at(9, 31, day=day), 7700.0, sigma=75.0 * swell)
    scene = scene_factory(now, flat_bars(int(minutes_left(at(9, 30, day=day)) - minutes_left(now)), day=day),
                          row_over={"range_ruler": ruler_block(em_points=straddle_for_load(load, now)), "sigma": 75.0 * swell},
                          rows_before=[first, *rows_before], prior_bars=prior)
    return replace(scene, state_dir=write_prior_diaries(tmp_path, rows), prior_rulers={d: SigmaRuler(75.0, "anchor") for d in prior_days})


def test_a_straddle_loaded_before_the_fed_reads_loaded(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_DAY, FOMC_AND_PRESSER)
    scene = ruler_scene(scene_factory, tmp_path, FOMC_DAY, (11, 30), load=1.92, swell=1.88, prior_days=EVENT_PRIOR_DAYS)
    assert labels(scene)[0]["vol.ruler_event_load"] == (
        "loaded before an event: the same-day straddle prices a 30-minute move 1.92 times what the tape's recent 5-minute ranges "
        "scale to, past the 1.5 loaded line, higher than 10 of the last 10 sessions at 11:30 (top fifth); on the event calendar "
        "today: the Fed's rate decision at 14:00, the Fed chair's press conference at 14:30; the Fed's rate decision is still ahead; "
        "this morning's sigma ruler is 1.88 times its 10-session median, past the 1.3 swollen line")


def test_a_swollen_ruler_with_the_fed_ahead_but_a_straddle_not_loaded_reads_normal(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_DAY, FOMC_AND_PRESSER)
    got = labels(ruler_scene(scene_factory, tmp_path, FOMC_DAY, (11, 30), load=1.0, swell=1.40, prior_days=EVENT_PRIOR_DAYS))[0]
    assert got["vol.ruler_event_load"].startswith("normal: the same-day straddle prices a 30-minute move 1.00 times")
    assert ("; the Fed's rate decision is still ahead; this morning's sigma ruler is 1.40 times its 10-session median, past the 1.3 "
            "swollen line") in got["vol.ruler_event_load"]


def test_a_fed_event_past_the_digest_window_is_neither_released_nor_ahead(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_DAY, [("10:00", "FED_CHAIR_TESTIMONY", 1)])
    got = labels(ruler_scene(scene_factory, tmp_path, FOMC_DAY, (12, 32), load=1.0, swell=1.40, prior_days=EVENT_PRIOR_DAYS))[0]
    assert got["vol.ruler_event_load"].startswith("normal: ")
    assert ("; on the event calendar today: the Fed chair's testimony at 10:00; the Fed chair's testimony came out 152 minutes ago, "
            "past the 120-minute digest window; ") in got["vol.ruler_event_load"]


def released_scene(scene_factory, tmp_path, monkeypatch, prior_before_fed: float | None, before_fed: bool = True):
    """A 14:32 read after the Fed's 14:00 decision whose straddle just before it priced 0.8 times today's tape."""
    calendar(tmp_path, monkeypatch, FOMC_DAY, FOMC_AND_PRESSER)
    before = [straddle_row(at(13, 59, day=FOMC_DAY, ss=30), straddle_for_load(0.8, at(13, 59, day=FOMC_DAY, ss=30)))] if before_fed else []
    return ruler_scene(scene_factory, tmp_path, FOMC_DAY, (14, 32), load=0.9, swell=1.4, rows_before=before, prior_days=EVENT_PRIOR_DAYS,
                       prior_before_fed=prior_before_fed)


def test_a_tape_outrunning_its_usual_against_the_pre_release_straddle_reads_released(scene_factory, tmp_path, monkeypatch):
    assert labels(released_scene(scene_factory, tmp_path, monkeypatch, prior_before_fed=1.0))[0]["vol.ruler_event_load"] == (
        "released after an event: the same-day straddle prices a 30-minute move 0.90 times what the tape's recent 5-minute ranges "
        "scale to, under the 1.5 loaded line, higher than 4 of the last 10 sessions at 14:32 (between the fifths); on the event "
        "calendar today: the Fed's rate decision at 14:00, the Fed chair's press conference at 14:30; the Fed's rate decision came "
        "out 32 minutes ago, inside the 120-minute digest window; the tape now moves 1.25 times its usual against the straddle "
        "priced just before it, over the one-to-one line; this morning's sigma ruler is 1.40 times its 10-session median, "
        "past the 1.3 swollen line")


def test_a_tape_that_usually_outruns_the_straddle_this_much_is_not_released(scene_factory, tmp_path, monkeypatch):
    """The prior sessions' tape ran twice their straddle at 14:00, today's only 1.25 times: a quieter tape than usual."""
    got = labels(released_scene(scene_factory, tmp_path, monkeypatch, prior_before_fed=0.5))[0]["vol.ruler_event_load"]
    assert got.startswith("normal: ") and ("; the tape now moves 0.62 times its usual against the straddle priced just before it, "
                                           "at or under the one-to-one line; ") in got


@pytest.mark.parametrize("prior_before_fed, before_fed, why", [
    (None, True, "needs 5 prior sessions with a straddle at 14:00 and a tape at this minute to judge the tape against, have 0"),
    (1.0, False, "no straddle on the rows just before 14:00 to judge the tape against"),
])
def test_a_release_that_cannot_be_judged_says_why_and_is_not_released(scene_factory, tmp_path, monkeypatch, prior_before_fed, before_fed, why):
    got = labels(released_scene(scene_factory, tmp_path, monkeypatch, prior_before_fed, before_fed))[0]["vol.ruler_event_load"]
    assert got.startswith("normal: ") and f"inside the 120-minute digest window; {why}; this morning's" in got


@pytest.mark.parametrize("load, swell, verdict, words", [
    (1.0, 1.40, "swollen with no event", "1.40 times its 10-session median, past the 1.3 swollen line"),
    (1.0, 0.75, "compressed", "0.75 times its 10-session median, at or under the 0.8 compressed line"),
    (1.0, 1.29, "normal", "1.29 times its 10-session median, between the 0.8 compressed and 1.3 swollen lines"),
    (1.92, 1.00, "normal", "1.00 times its 10-session median, between the 0.8 compressed and 1.3 swollen lines"),
])
def test_without_an_event_the_ruler_reads_swollen_compressed_or_normal(scene_factory, tmp_path, monkeypatch, load, swell, verdict, words):
    calendar(tmp_path, monkeypatch, "2026-09-18", [])
    scene = ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=load, swell=swell)
    got = labels(scene)[0]["vol.ruler_event_load"]
    assert got.startswith(f"{verdict}: ") and "; nothing is on the event calendar today; " in got and got.endswith(f"sigma ruler is {words}")


def test_a_swollen_ruler_on_a_release_day_is_not_swollen_with_no_event(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, "2026-09-18", [("08:30", "CPI", "pre_open"), ("10:00", "UMICH_SENTIMENT", "data_10am")])
    got = labels(ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=1.0, swell=1.40))[0]["vol.ruler_event_load"]
    assert got.startswith("normal: ") and ("; on the event calendar today: the consumer price report at 08:30, the University of "
                                           "Michigan's consumer sentiment report at 10:00; this morning's sigma ruler is 1.40 times") in got


def test_the_ruler_is_omitted_past_the_calendars_last_kept_day(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, "2026-09-18", [], covers_through="2026-09-17")
    got = labels(ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=1.0, swell=1.40))[1]["vol.ruler_event_load"]
    assert got == "the event calendar (calendar/events.json) is kept only through 2026-09-17: extend it"


def test_the_ruler_rank_reads_only_what_each_prior_session_had_by_the_clock(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, "2026-09-18", [])
    scene = ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=1.0, swell=1.0)
    for bars in scene.prior_bars.values():
        for b in bars[120:]:                           # from the 11:30 bar on, a wild tape the 11:30 read must not see
            b["high"], b["low"] = b["close"] + 50.0, b["close"] - 50.0
    assert "higher than 5 of the last 10 sessions at 11:30" in labels(scene)[0]["vol.ruler_event_load"]


def test_the_ruler_is_omitted_without_its_parts(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, "2026-09-18", [])
    scene = ruler_scene(scene_factory, tmp_path, "2026-09-18", (11, 30), load=1.0, swell=1.0)
    assert labels(replace(scene, state_dir=None))[1]["vol.ruler_event_load"] == "no state folder to read the prior sessions' diaries from"
    assert labels(replace(scene, prior_rulers={}))[1]["vol.ruler_event_load"] == (
        "needs the morning sigma ruler and 5 prior sessions' trusted anchors for its normal")
    assert labels(replace(scene, bars=scene.bars[:-5]))[1]["vol.ruler_event_load"] == (
        "needs the straddle left on the row, 30 minutes of session left and six finished 5-minute slices")


# ---- vol.realized_vs_clock and vol.realized_vs_clock_rank

def swinging(step: float, n: int = 180, day: str = "2026-09-18") -> list[dict]:
    """Closes that swing ``step`` points every minute and go nowhere: a realized swing of step x sqrt(30) / 75
    sigma over 30 minutes, and no real move."""
    return bars_from_closes([7700.0 + (step if i % 2 else 0.0) for i in range(n)], day=day)


def drift(closes: list[float], start: int, points: float = 7.0, minutes: int = 10) -> list[float]:
    """``closes`` with a steady climb of ``points`` over ``minutes`` from index ``start``, held after."""
    out = list(closes)
    for i in range(start, len(out)):
        out[i] += points * min(i - start + 1, minutes) / minutes
    return out


def realized_scene(scene_factory, today: list[dict], steps=range(1, 11)):
    """A read at 12:30 against ten prior sessions that swung 1 to 10 points a minute, each with a trusted anchor."""
    prior = {d: swinging(step, n=390, day=d) for d, step in zip(PRIOR_DAYS, steps)}
    scene = scene_factory(at(12, 30), today, rows_before=[morning()], prior_bars=prior)
    return replace(scene, prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in prior})


@pytest.mark.parametrize("step, standing", [
    (5.5, "more than 5 of the last 10 sessions at this time of day, between the bottom and top fifths"),
    (9.5, "more than 9 of the last 10 sessions at this time of day, top fifth"),
    (12.0, "more than every one of the last 10 sessions at this time of day"),
])
def test_the_realized_swing_is_ranked_against_the_same_half_hour(scene_factory, step, standing):
    swing = step * math.sqrt(30) / SIGMA
    assert labels(realized_scene(scene_factory, swinging(step)))[0]["vol.realized_vs_clock"] == (
        f"over the last 30 minutes SPX's realized swing was {swing:.2f} sigma, {standing}; price has made no real move "
        "(0.09 sigma within 10 minutes) today, none for at least the last 30 minutes")


def test_a_bottom_fifth_swing_says_when_the_last_real_move_ended(scene_factory):
    flat = [7700.0] * 180
    coiled = labels(realized_scene(scene_factory, bars_from_closes(flat, wick=0.0)))[0]["vol.realized_vs_clock"]
    assert coiled == ("over the last 30 minutes SPX's realized swing was 0.00 sigma, more than 0 of the last 10 sessions at this time "
                      "of day, bottom fifth; price has made no real move (0.09 sigma within 10 minutes) today, none for at least the last "
                      "30 minutes")
    once = labels(realized_scene(scene_factory, bars_from_closes(drift(flat, 165))))[0]["vol.realized_vs_clock"]
    assert once.endswith("bottom fifth; the last real move (0.09 sigma within 10 minutes) ended 5 minutes ago, inside the last 30 "
                         "minutes; before that there was none since the open")
    twice = labels(realized_scene(scene_factory, bars_from_closes(drift(drift(flat, 100), 165))))[0]["vol.realized_vs_clock"]
    assert twice.endswith("ended 5 minutes ago, inside the last 30 minutes; before that there was none for 65 minutes")
    long_ago = labels(realized_scene(scene_factory, bars_from_closes(drift(flat, 100))))[0]["vol.realized_vs_clock"]
    assert "ended 70 minutes ago, at least 30 minutes ago" in long_ago


@pytest.mark.parametrize("step, words", [
    (8.0, "1.45 times the usual pace for this half hour (its median on the last 10 sessions at this time of day), past the 1.3 hot line"),
    (7.0, "1.27 times the usual pace for this half hour (its median on the last 10 sessions at this time of day), at or under the 1.3 hot line"),
])
def test_the_pace_is_the_swing_over_its_same_clock_median(scene_factory, step, words):
    assert labels(realized_scene(scene_factory, swinging(step)))[0]["vol.realized_vs_clock_rank"] == f"the last 30 minutes moved {words}"


def test_the_prior_sessions_count_only_what_they_had_by_the_clock(scene_factory):
    scene = realized_scene(scene_factory, swinging(5.5))
    for bars in scene.prior_bars.values():
        for i, b in enumerate(bars[180:]):            # from the 12:30 bar on: huge swings the 12:30 read must not see
            b["close"] = 7700.0 + 500.0 * (i % 2)
    assert "more than 5 of the last 10 sessions" in labels(scene)[0]["vol.realized_vs_clock"]


def test_the_realized_swing_is_omitted_without_its_bars_or_its_sessions(scene_factory):
    early = scene_factory(at(9, 50), swinging(3.0, n=20), rows_before=[morning()])
    got = labels(early)[1]
    assert got["vol.realized_vs_clock"] == got["vol.realized_vs_clock_rank"] == (
        "needs the morning sigma ruler and 25 finished bars in the last 30 minutes")
    thin = realized_scene(scene_factory, swinging(3.0), steps=range(1, 5))
    thin = replace(thin, prior_bars=dict(list(thin.prior_bars.items())[:4]))
    assert labels(thin)[1]["vol.realized_vs_clock"] == "needs 5 prior sessions with bars at this minute, have 4"


# ---- the VIX family from the context job: vol.front_fear_shift, vol.vvix_with_move, vol.vvix_vs_vix, vol.term_structure

def vix_family(now, **series) -> MarketContext:
    """A MarketContext whose symbols ($ dropped from the keyword) take each value at the minutes before ``now`` given."""
    return MarketContext({f"${name}": sorted((now - timedelta(minutes=ago), v) for ago, v in points.items())
                          for name, points in series.items()})


@pytest.mark.parametrize("front_now, words", [
    (14.95, "fell 0.21 points against the 30-day VIX, from 0.84 under it to 1.05 under it, beyond the 0.15-point rule"),
    (15.21, "rose 0.05 points against the 30-day VIX, from 0.84 under it to 0.79 under it, within the 0.15-point rule"),
    (16.26, "rose 1.10 points against the 30-day VIX, from 0.84 under it to 0.26 over it, beyond the 0.15-point rule"),
])
def test_the_front_of_the_curve_is_judged_on_its_shift_against_vix(scene_factory, front_now, words):
    now = at(12, 30, ss=10)
    market = vix_family(now, VIX={30: 16.0, 1: 16.0}, VIX9D={30: 15.16, 1: front_now, -1: 30.0})    # the 12:31 value is not known yet
    scene = scene_factory(now, flat_bars(180), rows_before=[morning()], market=market)
    assert labels(scene)[0]["vol.front_fear_shift"] == f"over the last 30 minutes nine-day VIX {words}"


def test_the_front_shift_is_omitted_when_the_context_job_stopped(scene_factory):
    now = at(12, 30, ss=10)
    stale = scene_factory(now, flat_bars(180), market=vix_family(now, VIX={30: 16.0, 1: 16.0}, VIX9D={30: 15.16, 20: 15.0}))
    assert labels(stale)[1]["vol.front_fear_shift"] == "no $VIX9D and $VIX in the market context now and 30 minutes ago (the context job)"
    assert "vol.front_fear_shift" in labels(scene_factory(now, flat_bars(180)))[1]


@pytest.mark.parametrize("drop, vvix_now, words", [
    (0.12, 88.9, "SPX fell 0.12 sigma, past the 0.05 sigma pairing rule, and VVIX rose 0.80 points, from 88.10 to 88.90"),
    (0.12, 88.1, "SPX fell 0.12 sigma, past the 0.05 sigma pairing rule, and VVIX held at 88.10"),
    (0.03, 87.6, "SPX fell 0.03 sigma, within the 0.05 sigma pairing rule, and VVIX fell 0.50 points, from 88.10 to 87.60"),
])
def test_the_spx_move_is_paired_with_the_way_vvix_went(scene_factory, drop, vvix_now, words):
    now = at(12, 30, ss=10)
    scene = scene_factory(now, fall_then_now(now, drop), rows_before=[morning()], market=vix_family(now, VVIX={30: 88.1, 1: vvix_now}))
    assert labels(scene)[0]["vol.vvix_with_move"] == f"over the last 30 minutes {words}"


# Orthogonal patterns: the SPX moves, VIX changes and leftovers of each four half hours of the fit history
# are mutually uncorrelated and sum to nothing, so the fit finds VVIX = 1 + 2 x SPX + 3 x VIX exactly and
# its leftovers are the fixture's own: four each of 0.01 to 0.22.
SPX_PATTERN, VIX_PATTERN, LEFT_PATTERN = (1, 1, -1, -1), (1, -1, 1, -1), (1, -1, -1, 1)


def vvix_history(days: list[str]):
    bars, markets, g = {}, {}, 0
    for day in days:
        level, vix, vvix = [7700.0], [15.0], [90.0]           # at 10:00, then each half hour to 15:30
        for _ in range(11):
            x1, x2, e = 0.1 * SPX_PATTERN[g % 4], 0.2 * VIX_PATTERN[g % 4], 0.01 * (g // 4 + 1) * LEFT_PATTERN[g % 4]
            level.append(level[-1] + SIGMA * x1)
            vix.append(vix[-1] + x2)
            vvix.append(vvix[-1] + 1 + 2 * x1 + 3 * x2 + e)
            g += 1
        marks = [at(10, 0, day=day) + timedelta(minutes=30 * m) for m in range(12)]
        bars[day] = bars_from_closes([level[min(max((i + 1) // 30 - 1, 0), 11)] for i in range(390)], day=day)
        markets[day] = MarketContext({"$VIX": list(zip(marks, vix)), "$VVIX": list(zip(marks, vvix))})
    return bars, markets


@pytest.mark.parametrize("drop, vix_now, left, words", [
    (0.12, 15.35, 0.70, "VVIX rose 2.51 points; VIX's and SPX's moves explain a rise of about 1.81; the 0.70 left over is bigger than 88 of 88 "
                        "half hours on the last 8 sessions, top fifth, while SPX or VIX also moved (SPX at or past the 0.09 sigma move rule, "
                        "or VIX at or past the 0.6% still line)"),
    (0.0, 15.05, 0.70, "VVIX rose 1.85 points; VIX's and SPX's moves explain a rise of about 1.15; the 0.70 left over is bigger than 88 of 88 "
                       "half hours on the last 8 sessions, top fifth, while SPX and VIX barely moved (SPX under the 0.09 sigma move rule, "
                       "VIX under the 0.6% still line)"),
    (0.0, 15.05, -0.155, "VVIX rose 1.00 points; VIX's and SPX's moves explain a rise of about 1.15; the 0.15 left over is bigger than 60 of "
                         "88 half hours on the last 8 sessions, at or above the median, short of the top fifth, while SPX and VIX barely moved "
                         "(SPX under the 0.09 sigma move rule, VIX under the 0.6% still line)"),
    (0.0, 15.05, 0.057, "VVIX rose 1.21 points; VIX's and SPX's moves explain a rise of about 1.15; the 0.06 left over is bigger than 20 of "
                        "88 half hours on the last 8 sessions, under the median, while SPX and VIX barely moved (SPX under the 0.09 sigma "
                        "move rule, VIX under the 0.6% still line)"),
])
def test_vvix_is_judged_on_what_spx_and_vix_leave_unexplained(scene_factory, drop, vix_now, left, words):
    now = at(12, 30)
    days = PRIOR_DAYS[:8]
    bars, markets = vvix_history(days)
    dvvix = 1 + 2 * -drop + 3 * (vix_now - 15.0) + left
    market = vix_family(now, VIX={30: 15.0, 0: vix_now}, VVIX={30: 90.0, 0: 90.0 + dvvix})
    scene = scene_factory(now, fall_then_now(now, drop) if drop else flat_bars(180), rows_before=[morning()], market=market, prior_bars=bars)
    scene = replace(scene, prior_markets=markets, prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in days})
    assert labels(scene)[0]["vol.vvix_vs_vix"] == f"over the last 30 minutes {words}"


def test_vvix_is_omitted_without_its_history_or_todays_quotes(scene_factory):
    now = at(12, 30)
    days = PRIOR_DAYS[:4]
    bars, markets = vvix_history(days)
    market = vix_family(now, VIX={30: 15.0, 0: 15.1}, VVIX={30: 90.0, 0: 91.0})
    scene = scene_factory(now, flat_bars(180), rows_before=[morning()], market=market, prior_bars=bars)
    scene = replace(scene, prior_markets=markets, prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in days})
    assert labels(scene)[1]["vol.vvix_vs_vix"] == "needs 5 prior sessions of $VIX and $VVIX to fit against, have 4"
    assert labels(replace(scene, market=None))[1]["vol.vvix_vs_vix"].startswith("needs the morning sigma ruler, bars 30 minutes apart")


def curve_state(tmp_path, clock=(10, 2)):
    """Ten prior diaries whose VIX curve at the clock runs 0.78 to 0.87; one inverted late in its day."""
    rows = {}
    for k, day in enumerate(PRIOR_DAYS):
        rows[day] = [make_row(at(9, 31, day=day), 7700.0), make_row(at(*clock, day=day), 7700.0, vix_ts=0.78 + 0.01 * k),
                     make_row(at(15, 0, day=day), 7700.0, vix_ts=1.01 if k == 3 else 0.8)]
    return write_prior_diaries(tmp_path, rows)


@pytest.mark.parametrize("ratio, front, words", [
    (0.835, 15.2, "VIX is 0.83 times three-month VIX, under the 0.95 near-flat line; flatter than 6 of the last 10 sessions at this time, "
                  "between the fifths; nine-day VIX is 0.95 of VIX, under the inversion line"),
    (0.77, 15.2, "VIX is 0.77 times three-month VIX, under the 0.95 near-flat line; flatter than 0 of the last 10 sessions at this time, "
                 "bottom fifth; nine-day VIX is 0.95 of VIX, under the inversion line"),
    (0.96, 15.2, "VIX is 0.96 times three-month VIX, at or over the 0.95 near-flat line and under the 1.00 inversion line; flatter than 10 "
                 "of the last 10 sessions at this time, top fifth; nine-day VIX is 0.95 of VIX, under the inversion line"),
    (1.02, 16.5, "VIX is 1.02 times three-month VIX, at or over the 1.00 inversion line; flatter than 10 of the last 10 sessions at this "
                 "time, top fifth; nine-day VIX is 1.03 of VIX, at or over the inversion line"),
])
def test_the_vix_curve_reads_its_fixed_lines_then_its_rank(scene_factory, tmp_path, ratio, front, words):
    now = at(10, 2)
    scene = scene_factory(now, flat_bars(32), row_over={"vix_ts": ratio}, rows_before=[morning()],
                          prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS}, market=vix_family(now, VIX={1: 16.0}, VIX9D={1: front}))
    got = labels(replace(scene, state_dir=curve_state(tmp_path)))[0]["vol.term_structure"]
    assert got == f"{words}; the curve inverted on 1 of the last 10 sessions"


def test_the_vix_curve_is_omitted_without_its_parts(scene_factory, tmp_path):
    now = at(10, 2)
    prior = {d: flat_bars(390, day=d) for d in PRIOR_DAYS}
    market = vix_family(now, VIX={1: 16.0}, VIX9D={1: 15.2})
    scene = replace(scene_factory(now, flat_bars(32), rows_before=[morning()], prior_bars=prior, market=market), state_dir=curve_state(tmp_path))
    assert labels(replace(scene, market=None))[1]["vol.term_structure"] == "no $VIX9D and $VIX in the market context (the context job)"
    assert labels(replace(scene, state_dir=None))[1]["vol.term_structure"] == "no state folder to read the prior sessions' diaries from"
    no_ts = replace(scene, rows_today=[*scene.rows_today[:-1], {k: v for k, v in scene.row.items() if k != "vix_ts"}])
    assert labels(no_ts)[1]["vol.term_structure"] == "row carries no VIX against three-month VIX (vix_ts)"
    assert labels(replace(scene, prior_bars=dict(list(prior.items())[:3])))[1]["vol.term_structure"] == (
        "needs 5 prior sessions with the VIX curve at this minute, have 3")


# ---- vol.stress_path

def tick_tape(now, lows_by_minutes_ago: dict[int, float]) -> MarketContext:
    """NYSE TICK's minute bars, each finished the given minutes before ``now`` (a negative one after it)."""
    return MarketContext({}, {"$TICK": sorted((now - timedelta(minutes=ago), {"high": 300.0, "low": low})
                                              for ago, low in lows_by_minutes_ago.items())})


SELLING = {3: -1100.0, 7: -1050.0, 12: -1300.0, 18: -1000.0, 25: -1150.0, 40: -1200.0, -1: -1500.0, 5: -600.0}


def stress_scene(scene_factory, vix_open=20.7, vix_now=24.1, off_low=16.5, ticks=None, **row_over):
    """A 12:30 read after a selloff: SPX's low made by the 11:39 bar, VIX's session high 25.0 at 11:42."""
    now = at(12, 30)
    low = 7679.5                                        # the 11:39 bar's close of 7680 less its wick
    bars = bars_from_closes([7700.0] * 129 + [7680.0] + [low + off_low] * 51)
    rows = [diary_row(at(9, 31), vix_open), diary_row(at(11, 42), 25.0), diary_row(at(12, 0), 24.5)]
    scene = scene_factory(now, bars, row_over={"range_ruler": ruler_block(vix_now), **row_over}, rows_before=rows,
                          market=tick_tape(now, SELLING if ticks is None else ticks))
    return replace(scene, prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in PRIOR_DAYS[:5]})


def test_a_selloff_reads_its_vix_retreat_its_bounce_and_its_tick_cluster(scene_factory):
    assert labels(stress_scene(scene_factory))[0]["vol.stress_path"] == (
        "VIX 24.1, up 3.4 points since the open; session high 25.0 at 11:42, it has given back 21% of its rise from there, short of "
        "the 30% retreat share and at or past the 10% hold share; SPX sits 0.22 normal-day sigma above its session low from 11:40, past "
        "the 0.15 bounce line; NYSE TICK printed at or below -1000 5 times in 30 minutes, at least the 3-reading cluster")


@pytest.mark.parametrize("vix_now, words", [
    (23.4, "it has given back 37% of its rise from there, past the 30% retreat share"),
    (24.8, "it has given back 5% of its rise from there, short of the 10% hold share"),
    (25.0, "it has given back 0% of its rise from there, short of the 10% hold share, and is at its session high now"),
])
def test_the_vix_retreat_is_judged_on_the_retreat_and_hold_shares(scene_factory, vix_now, words):
    assert words in labels(stress_scene(scene_factory, vix_now=vix_now))[0]["vol.stress_path"]


@pytest.mark.parametrize("off_low, words", [
    (6.0, "0.08 normal-day sigma above its session low from 11:40, within the 0.1 near-low line"),
    (9.0, "0.12 normal-day sigma above its session low from 11:40, beyond the 0.1 near-low line and short of the 0.15 bounce line"),
])
def test_the_distance_off_the_low_is_judged_on_the_near_low_and_bounce_lines(scene_factory, off_low, words):
    assert f"SPX sits {words};" in labels(stress_scene(scene_factory, off_low=off_low))[0]["vol.stress_path"]


def test_spot_under_the_finished_bars_low_is_at_its_session_low_now(scene_factory):
    scene = stress_scene(scene_factory, off_low=0.5)
    row = {**scene.row, "spot": 7678.0}                                   # under the 11:39 bar's low of 7679.5
    under = replace(scene, row=row, rows_today=[*scene.rows_today[:-1], row])
    assert "; SPX sits at its session low now, within the 0.1 near-low line; " in labels(under)[0]["vol.stress_path"]


def test_a_stress_day_with_vix_never_above_its_open_says_so(scene_factory):
    got = labels(stress_scene(scene_factory, vix_open=26.0, vix_ts=0.99))[0]["vol.stress_path"]
    assert got.startswith("VIX 24.1, down 1.9 points since the open; VIX has not been above its open today; SPX sits ")


def test_only_tick_bars_finished_in_the_last_30_minutes_count(scene_factory):
    got = labels(stress_scene(scene_factory, ticks={3: -1100.0, 12: -1300.0, 40: -1200.0, -1: -1500.0}))[0]["vol.stress_path"]
    assert got.endswith("NYSE TICK printed at or below -1000 2 times in 30 minutes, short of the 3-reading cluster")


def test_a_day_is_stressed_by_its_vix_rise_its_prior_high_or_its_curve(scene_factory, tmp_path):
    calm = stress_scene(scene_factory, vix_open=23.1)
    assert labels(calm)[1]["vol.stress_path"] == ("not a stress day: VIX is +1.00 points from its open, under 2, with no prior diaries to "
                                                  "compare it with, and 0.83 times three-month VIX, under 0.98")
    assert "vol.stress_path" in labels(stress_scene(scene_factory, vix_open=23.1, vix_ts=0.99))[0]
    history = write_prior_diaries(tmp_path, {d: [diary_row(at(10, 0, day=d), 22.0)] for d in PRIOR_DAYS[:5]})
    over_prior_high = replace(calm, state_dir=history, prior_bars={d: flat_bars(390, day=d) for d in PRIOR_DAYS[:5]})
    assert "vol.stress_path" in labels(over_prior_high)[0]
    under = write_prior_diaries(tmp_path / "under", {d: [diary_row(at(10, 0, day=d), 26.0)] for d in PRIOR_DAYS[:5]})
    assert labels(replace(over_prior_high, state_dir=under))[1]["vol.stress_path"] == (
        "not a stress day: VIX is +1.00 points from its open, under 2, at or under its 26.00 high of the prior sessions, and 0.83 times "
        "three-month VIX, under 0.98")


def test_a_stress_day_without_tick_or_a_normal_day_sigma_is_omitted(scene_factory):
    stressed = stress_scene(scene_factory)
    assert labels(replace(stressed, market=None))[1]["vol.stress_path"] == (
        "no NYSE TICK in the market context over the last 30 minutes (the context job)")
    assert labels(replace(stressed, prior_rulers={}))[1]["vol.stress_path"] == (
        "needs today's bars and 5 prior sessions' trusted anchors for the normal-day sigma")


# ---- the same-day skew from the lob-flow tape: skew.put_tilt_vs_usual (and its gate), skew.shift_vs_price

def smile_quotes(end, slope: float, forward: float = 7700.0, atm: float = 0.15) -> list[dict]:
    """Tape lines 20 seconds before ``end`` quoting every 5-point strike from 7600 to 7800 at a smile whose
    vol rises ``slope`` points of vol per 100% of moneyness below the forward, 10 cents wide."""
    years = (end.replace(hour=16, minute=0, second=0) - end).total_seconds() / (365 * 24 * 3600)
    ts = int((end - timedelta(seconds=20)).timestamp() * 1000)
    out = []
    for k in range(7600, 7805, 5):
        iv = atm + slope * (forward - k) / forward
        for right in ("call", "put"):
            price = black_price(forward, float(k), iv, years, right)
            if price > 0.15:
                out.append({"ts_ms": ts, "strike": float(k), "right": right, "price": round(price, 2), "size": 1,
                            "bid": round(price - 0.05, 2), "ask": round(price + 0.05, 2), "bid_size": 5, "ask_size": 5, "condition": 18})
    return out


def write_tape(root, day: str, lines: list[dict], archived: bool = False):
    folder = root / "lob_flow" / "raw" / day
    folder.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(x) + "\n" for x in lines)
    if archived:
        with gzip.open(folder / "tape.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        (folder / "tape.jsonl").write_text(text)


def skew_scene(scene_factory, tmp_path, slope: float, clock=(9, 50), prior_slopes=tuple(0.5 * k for k in range(2, 12))):
    """A read at ``clock`` on a tape quoting today's smile at ``slope``, the prior sessions' at ``prior_slopes``
    (their tapes archived), each prior session with a trusted anchor."""
    now = at(*clock, ss=15)
    days = PRIOR_DAYS[:len(prior_slopes)]
    for d, s in zip(days, prior_slopes):
        write_tape(tmp_path, d, smile_quotes(at(*clock, day=d), s), archived=True)
    write_tape(tmp_path, DAY, smile_quotes(at(*clock), slope))
    scene = scene_factory(now, flat_bars(int((now - at(9, 30)).total_seconds() // 60)), rows_before=[morning()],
                          prior_bars={d: flat_bars(390, day=d) for d in days})
    return replace(scene, state_dir=tmp_path, prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in days})


def tilt_and_gate(scene):
    got = build_vol_labels(scene)
    return got.state.get("skew", {}).get("put_tilt_vs_usual"), got.omitted.get("skew.put_tilt_vs_usual"), got.gates["put_tilt_vs_clock"]




@pytest.mark.parametrize("slope, words", [
    (5.0, "same-day 25-delta puts are priced 2.7 vol points above 25-delta calls, 0.18 of the at-the-money level; steeper than 8 of the "
          "last 10 sessions at 09:50, past the steep rank; far puts (10-delta) are at a steeper premium than usual to 25-delta puts, "
          "past the steep rank"),
    (3.0, "same-day 25-delta puts are priced 1.6 vol points above 25-delta calls, 0.11 of the at-the-money level; steeper than 4 of the "
          "last 10 sessions at 09:50, within the usual range; far puts (10-delta) are at a usual premium to 25-delta puts"),
    (1.2, "same-day 25-delta puts are priced 0.6 vol points above 25-delta calls, 0.04 of the at-the-money level; steeper than 1 of the "
          "last 10 sessions at 09:50, past the flat rank; far puts (10-delta) are at a flatter premium than usual to 25-delta puts, "
          "past the flat rank"),
    (-1.0, "same-day 25-delta calls are priced 0.5 vol points above 25-delta puts, a call tilt; steeper than 0 of the last 10 sessions "
           "at 09:50, past the flat rank; far puts (10-delta) are at a flatter premium than usual to 25-delta puts, past the flat rank"),
])
def test_the_put_tilt_is_ranked_against_the_same_minute_and_wakes_its_question(scene_factory, tmp_path, slope, words):
    assert tilt_and_gate(skew_scene(scene_factory, tmp_path, slope)) == (words, None, None)


def test_far_puts_without_a_fresh_quote_are_said_to_have_none_to_compare(scene_factory, tmp_path):
    scene = skew_scene(scene_factory, tmp_path, 3.0)
    write_tape(tmp_path, DAY, [q for q in smile_quotes(at(9, 50), 3.0) if q["right"] == "call" or q["strike"] >= 7670])
    assert tilt_and_gate(scene)[0].endswith("within the usual range; far puts (10-delta) have no fresh quote or no history at this "
                                            "minute to compare")


def test_the_tilt_reads_the_newest_quote_in_the_minute_whatever_the_line_order(scene_factory, tmp_path):
    later = smile_quotes(at(9, 51), -5.0)                        # quoted at 09:50:40, after the read's minute
    stale = [dict(q, ts_ms=q["ts_ms"] - 30000, bid=q["bid"] + 20, ask=q["ask"] + 20) for q in smile_quotes(at(9, 50), 3.0)]
    tape = [*later, *smile_quotes(at(9, 50), 3.0), *stale]      # out of time order, as the collector writes them
    scene = skew_scene(scene_factory, tmp_path, 3.0)
    write_tape(tmp_path, DAY, tape)
    assert tilt_and_gate(scene)[0].startswith("same-day 25-delta puts are priced 1.6 vol points above 25-delta calls")


def test_the_put_tilt_sleeps_its_question_with_the_reason_it_is_omitted(scene_factory, tmp_path):
    thin = skew_scene(scene_factory, tmp_path / "thin", 3.0, prior_slopes=(1.0, 2.0, 3.0, 4.0))
    why = "needs 5 prior sessions with 25-delta quotes on the tape at 09:50, have 4"
    assert tilt_and_gate(thin) == (None, why, why)
    later = skew_scene(scene_factory, tmp_path / "later", 3.0, clock=(9, 50))
    later = replace(later, now=at(10, 20, ss=15))
    why = "no fresh 25-delta put and call quotes on the lob-flow tape in the minute to 10:20"
    assert tilt_and_gate(later) == (None, why, why)
    why = f"no lob-flow tape for {DAY} (state/lob_flow/raw)"
    assert tilt_and_gate(replace(thin, state_dir=None)) == (None, why, why)
    assert labels(replace(thin, state_dir=None))[1]["skew.shift_vs_price"] == why


def shift_scene(scene_factory, tmp_path, slope_then: float, slope_now: float, drop: float):
    now = at(12, 30, ss=15)
    write_tape(tmp_path, DAY, [*smile_quotes(at(12, 0), slope_then), *smile_quotes(at(12, 30), slope_now)])
    return replace(scene_factory(now, fall_then_now(now, drop), rows_before=[morning()]), state_dir=tmp_path)


@pytest.mark.parametrize("slope_then, slope_now, drop, words", [
    (3.0, 4.5, 0.10, "widened 0.052 of at-the-money vol while price fell 0.10 sigma; price explains a widening of 0.019, so puts got "
                     "dearer by 0.033 beyond the move, past the 0.02 line"),
    (3.0, 3.5, 0.10, "widened 0.012 of at-the-money vol while price fell 0.10 sigma; price explains a widening of 0.019, so puts got "
                     "cheaper by 0.007 beyond the move, inside the 0.02 line"),
    (3.0, 2.0, 0.10, "narrowed 0.047 of at-the-money vol while price fell 0.10 sigma; price explains a widening of 0.019, so puts got "
                     "cheaper by 0.066 beyond the move, past the 0.02 line"),
    (3.0, 3.0, -0.20, "narrowed 0.008 of at-the-money vol while price rose 0.20 sigma; price explains a narrowing of 0.038, so puts got "
                      "dearer by 0.030 beyond the move, past the 0.02 line"),
])
def test_the_skew_shift_is_judged_beyond_what_the_price_move_explains(scene_factory, tmp_path, slope_then, slope_now, drop, words):
    got = labels(shift_scene(scene_factory, tmp_path, slope_then, slope_now, drop))[0]["skew.shift_vs_price"]
    assert got == f"over the last 30 minutes the gap between same-day put and call prices {words}"


def test_the_skew_shift_is_omitted_without_quotes_30_minutes_ago(scene_factory, tmp_path):
    now = at(12, 30, ss=15)
    write_tape(tmp_path, DAY, smile_quotes(at(12, 30), 3.0))
    scene = replace(scene_factory(now, fall_then_now(now, 0.1), rows_before=[morning()]), state_dir=tmp_path)
    assert labels(scene)[1]["skew.shift_vs_price"] == ("needs quotes one remaining standard deviation either side on the lob-flow tape "
                                                       "at 12:00 and 12:30, the bars then and the morning sigma ruler")


def test_the_opening_vix_never_takes_an_older_session_for_yesterday(scene_factory, tmp_path):
    scene = scene_factory(at(9, 35, ss=20), flat_bars(6, price=7733.0), row_over={"prior_close": 7700.0},
                          rows_before=[diary_row(at(9, 30, ss=28), 15.29, prior_close=7700.0)])
    assert labels(with_prior_diary(tmp_path, scene, "2026-09-16", 14.74))[1]["vol.vix_overnight_surprise"] == (
        "the previous session's diary is not on file: its newest earlier day is 2026-09-16")
