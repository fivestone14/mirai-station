"""The story family (labels/story.py), offline: each premarket.* label and its gate on synthetic nights
(built, held, faded and reversed; one way, split and latest against; added, gave back and crossed; the
report window crossed, unwound and extended; the night carried on or turned against yesterday's last hour),
their sleep reasons, a missed-bar merge, a roll night, too few nights, and a label built at 09:28 equal to
the same label rebuilt later from the same bars.

Twenty prior nights of /ES 5-minute bars are written into a tmp state folder, each a small seeded random walk
(a stretch moves a few hundredths of a percent), so a move of a percent tonight ranks in the top third and a
flat one in the bottom. Tonight's /ES is built stretch by stretch from ``path`` (each stretch's move in percent,
spread evenly over its minutes) plus one-minute ``jumps``. The ruler is 75 points on a 7500 prior close, so one
percent is one sigma."""
from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta

import pytest

from conftest import bars_from_closes, night_row
from spx_jev import events, overnight, rolls, story
from spx_jev.labels.story import LABELS, build_story_labels, previous_checkpoint
from spx_jev.sessions import previous_trading_day

DAY = date(2026, 9, 24)                     # a Thursday; its 20 prior nights run back past Labor Day
ET = overnight.ET
PRIOR_CLOSE, SIGMA = 7500.0, 75.0           # one percent of the prior close is one sigma
ES_AT_CLOSE = 6600.0
MAX_WORDS = 45


def _prior_days(n: int = 20) -> list[date]:
    out, d = [], DAY
    while len(out) < n:
        d = previous_trading_day(d)
        out.append(d)
    return out


def _es_bar(ts: datetime, close: float, day: date, minutes: int) -> dict:
    return night_row("/ES", ts, close, minutes=minutes, day=day.isoformat())


def _prior_night(day: date, seed: int) -> list[dict]:
    """A random walk of 5-minute /ES bars through the night into ``day``, to 09:30, only while the market trades."""
    rng, price, out = random.Random(seed), ES_AT_CLOSE, []
    for t in overnight.expected_slots("/ES", 5, overnight.night_window(day)[0], datetime.combine(day, time(9, 30), tzinfo=ET)):
        price *= 1 + rng.gauss(0, 0.0002)
        out.append(_es_bar(t, round(price, 2), day, 5))
    return out


def _write_nights(root, days: list[date]) -> None:
    for k, d in enumerate(days):
        overnight.write_night(root, d.isoformat(), _prior_night(d, k))


@pytest.fixture(scope="module")
def nights_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("state")
    _write_nights(root, _prior_days())
    return root


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    """Today's pre-open reports as a test sets them (``set_rows([(time, kind), ...])``); none by default."""
    rows: list[events.Event] = []
    monkeypatch.setattr(events, "on_day", lambda day, path=None: [e for e in rows if e.start.date() == day])
    monkeypatch.setattr(events, "uncovered", lambda day, path=None: None)

    def set_rows(today: list[tuple[str, str]]) -> None:
        rows[:] = [events.Event(datetime.combine(DAY, time.fromisoformat(t), tzinfo=ET), None, kind, events.PRE_OPEN) for t, kind in today]
    return set_rows


def tonight(path: dict[str, float], jumps: dict[time, float] | None = None, skip: tuple[time, time] | None = None) -> list[dict]:
    """1-minute /ES bars for the night into DAY, to 10:00: each stretch moves ``path[name]`` percent (0 when absent)
    evenly over its trading minutes, each ``jumps`` minute adds its percent, and ``skip`` leaves out the bars
    starting in that clock span on DAY. The report window starts at the calendar's release minute."""
    edges = story.edges(DAY, story.release_minute(DAY))
    jumps = {datetime.combine(DAY, c, tzinfo=ET): p for c, p in (jumps or {}).items()}
    start, end = overnight.night_window(DAY)[0], datetime.combine(DAY, time(10), tzinfo=ET)
    price, out = ES_AT_CLOSE, []
    for t in overnight.expected_slots("/ES", 1, start, end):
        stretch = next(((name, a, b) for name, a, b in edges if a <= t < b), None)
        if stretch:
            minutes = len(overnight.expected_slots("/ES", 1, stretch[1], stretch[2]))
            price *= (1 + path.get(stretch[0], 0.0) / 100) ** (1 / minutes)
        price *= 1 + jumps.get(t, 0.0) / 100
        out.append(_es_bar(t, round(price, 4), DAY, 1))
    if skip:
        lo, hi = (datetime.combine(DAY, c, tzinfo=ET) for c in skip)
        out = [r for r in out if not lo <= datetime.fromisoformat(r["ts"]) < hi]
    return out


def last_hours(yesterday_pct: float) -> dict[str, list[dict]]:
    """SPX sessions before DAY, newest first: yesterday's last hour moves ``yesterday_pct`` percent, the 19 before it
    0.02 to 0.38 percent, alternately up and down."""
    out = {}
    for k, d in enumerate(_prior_days()):
        move = yesterday_pct if k == 0 else 0.02 * k * (-1) ** k
        closes = [PRIOR_CLOSE] * 330 + [PRIOR_CLOSE * (1 + move / 100 * (m + 1) / 60) for m in range(60)]
        out[d.isoformat()] = bars_from_closes(closes, day=d.isoformat())
    return out


def scene(factory, state_dir, path, at=time(9, 28), yesterday_pct=0.0, night=None, **kw):
    now = datetime.combine(DAY, at, tzinfo=ET)
    return factory(now, night if night is not None else tonight(path, **kw), prior_close=PRIOR_CLOSE, sigma=SIGMA,
                   state_dir=state_dir, prior_bars=last_hours(yesterday_pct))


def read(factory, state_dir, path, **kw):
    return build_story_labels(scene(factory, state_dir, path, **kw))


def said(ls, path: str) -> str:
    group, key = path.split(".", 1)
    return ls.state[group][key]


def verdict(ls, path: str) -> str | None:
    return ls.figures[path]["verdict"]


# ---- scene guards -------------------------------------------------------------------------------------

def test_a_session_read_tells_no_story(full_scene):
    ls = build_story_labels(full_scene)
    assert not ls.paths() and not ls.gates


def test_without_a_state_folder_every_label_is_left_out_and_every_gate_sleeps(premarket_scene):
    ls = build_story_labels(premarket_scene)
    assert set(ls.omitted) == set(LABELS) and set(ls.omitted.values()) == {"no state folder to read the last nights from"}
    assert all(ls.gates.values())


def test_too_few_nights_on_file_leaves_the_story_out_with_the_reason(premarket_scene_factory, tmp_path):
    _write_nights(tmp_path, _prior_days(5))
    ls = read(premarket_scene_factory, tmp_path, {"asia": 1.0})
    assert set(ls.omitted) == set(LABELS)
    assert ls.omitted["premarket.where_now"] == ("futures against their 16:00 price: not ranked: only 5 usable of the last 20 nights "
                                                 "(holiday 1, unmeasured 14 left out), fewer than 10")


def test_a_night_across_a_roll_is_not_told(premarket_scene_factory, tmp_path):
    _write_nights(tmp_path, _prior_days())
    at = datetime.combine(previous_trading_day(DAY), time(20), tzinfo=ET)
    rolls.save(tmp_path / overnight.OVERNIGHT_SUBDIR, {"schema_version": 1, "current": {"/ES": "/ESZ26"},
                                                       "rolls": [{"symbol": "/ES", "day": at.date().isoformat(), "at": at.isoformat(),
                                                                  "from": "/ESU26", "to": "/ESZ26"}]})
    ls = read(premarket_scene_factory, tmp_path, {"asia": 1.0})
    assert set(ls.omitted) == set(LABELS) and all(ls.gates.values())
    assert "the futures rolled to the next contract between 16:00 and 09:28" in ls.omitted["premarket.legs"]


def test_a_roll_the_quote_shows_before_the_table_locates_it_stops_the_story(premarket_scene_factory, tmp_path):
    _write_nights(tmp_path, _prior_days())
    rolls.save(tmp_path / overnight.OVERNIGHT_SUBDIR, {"schema_version": 1, "current": {"/ES": "/ESZ26"}, "rolls": []})
    saved = datetime.combine(DAY, time(9, 26), tzinfo=ET)
    overnight.manifest_path(tmp_path).write_text(json.dumps({"day": DAY.isoformat(), "saved_at": saved.isoformat(),
                                                             "symbols": {"/ES": {"contract_quoted": "/ESH27"}}}) + "\n")
    ls = read(premarket_scene_factory, tmp_path, {"asia": 1.0})
    assert ls.omitted["premarket.where_now"].startswith("futures against their 16:00 price: Schwab quotes /ESH27 but the roll table "
                                                        "is still on /ESZ26")
    earlier = read(premarket_scene_factory, tmp_path, {"asia": 1.0}, at=time(9, 5))       # the 09:26 save is after the read: unseen
    assert "premarket.where_now" not in earlier.omitted


# ---- premarket.where_now ------------------------------------------------------------------------------

def test_where_now_states_futures_against_their_1600_price_first_ranked_against_the_last_nights(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.5, "europe_morning": -0.5})
    assert said(ls, "premarket.where_now") == ("at 09:28 S&P futures are 0.99 sigma above their 16:00 price, larger than 19 of the "
                                               "last 19 nights' moves to this time, top third; 66% of the way up the night's range")
    assert ls.figures["premarket.where_now"] == {"kind": "rank", "value": 0.992, "cut": "top third", "verdict": "up"}


def test_a_quiet_night_leans_flat(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {})
    assert verdict(ls, "premarket.where_now") == "flat" and "bottom third" in said(ls, "premarket.where_now")


# ---- premarket.legs and night_legs_agree ----------------------------------------------------------------

@pytest.mark.parametrize("path, code, tail", [
    ({"asia": 0.8, "europe_morning": 0.7}, "one_way", "2 moved, all with the night's rise: one way"),
    ({"asia": 1.0, "europe_open": -0.5, "europe_morning": 0.6}, "split_latest_with", "3 moved, split; the latest with the night's rise"),
    ({"asia": 1.0, "europe_morning": 0.6, "last_stretch": -0.4}, "latest_against", "3 moved; the latest against the night's rise"),
    ({"asia": -1.0, "europe_morning": -0.6, "last_stretch": 0.4}, "latest_against", "3 moved; the latest against the night's fall"),
])
def test_the_legs_that_moved_agree_split_or_turn(premarket_scene_factory, nights_dir, path, code, tail):
    ls = read(premarket_scene_factory, nights_dir, path)
    assert said(ls, "premarket.legs").endswith(tail)
    assert verdict(ls, "premarket.legs") == code and ls.gates["night_legs_agree"] is None


def test_the_legs_run_oldest_first_each_quiet_run_named_once_and_the_running_leg_so_far(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.0, "europe_morning": 0.6, "last_stretch": -0.4})
    assert said(ls, "premarket.legs") == ("oldest first: after hours quiet; Asia up 1.00 sigma; Europe's open quiet; Europe's morning "
                                          "up 0.60; 08:00-08:30 and 08:30-08:45 quiet; since 08:45 down 0.38 so far. 3 moved; the "
                                          "latest against the night's rise")


def test_one_finished_leg_that_moved_is_no_sequence(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.0})
    assert ls.gates["night_legs_agree"] == "only one finished leg of the night moved"
    assert said(ls, "premarket.legs").endswith("Only one finished leg of the night moved")
    assert verdict(ls, "premarket.legs") is None


def test_a_night_whose_net_is_quiet_sleeps_the_legs_question(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.0, "europe_morning": -0.99})
    assert ls.gates["night_legs_agree"].startswith("the night's net move is quiet, bottom third")


def test_a_missing_edge_price_tells_two_stretches_together_and_says_so(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.0, "europe_morning": 0.6}, skip=(time(2, 15), time(2, 45)))
    assert "; Asia and Europe's open together, bars missing up 1.00 sigma; Europe's morning up 0.60;" in said(ls, "premarket.legs")
    assert verdict(ls, "premarket.legs") == "one_way"


# ---- premarket.arc and overnight_arc ---------------------------------------------------------------

@pytest.mark.parametrize("path, code, end", [
    ({"asia": 0.5, "europe_morning": 0.5}, "built", "up 0.50 sigma, top third; they keep 201% of it: later trading built on it"),
    ({"asia": 1.0, "europe_morning": -0.2}, "held", "up 1.00 sigma, top third; they keep 80% of it: the first move held"),
    ({"asia": 1.0, "europe_morning": -0.7}, "faded", "up 1.00 sigma, top third; they keep 29% of it: the first move faded"),
    ({"asia": 1.0, "europe_morning": -2.0}, "reversed", "up 1.00 sigma, top third; they keep none of it: the night reversed its first move"),
])
def test_the_arc_from_the_first_leg_that_moved(premarket_scene_factory, nights_dir, path, code, end):
    ls = read(premarket_scene_factory, nights_dir, path)
    text = said(ls, "premarket.arc")
    assert text.startswith("futures are ") and "; the night's first leg that moved was Asia, " in text and text.endswith(end)
    assert verdict(ls, "premarket.arc") == code and ls.gates["overnight_arc"] is None


def test_a_night_with_no_finished_leg_that_moved_sleeps_the_arc(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"last_stretch": 1.0})
    assert ls.gates["overnight_arc"] == "no finished leg of the night moved: a quiet night"
    assert said(ls, "premarket.arc").endswith("no finished leg of the night moved past the bottom third of the last nights")


# ---- premarket.since_checkpoint and latest_leg_vs_night -------------------------------------------------

@pytest.mark.parametrize("after, code, words", [
    (1.0, "added", "since the 09:05 checkpoint they rose 1.00 sigma, top third for 09:05-09:28 over the last 19 nights, with the "
                   "night's rise before it: the latest leg added"),
    (-0.8, "gave_back", "since the 09:05 checkpoint they fell 0.80 sigma, top third for 09:05-09:28 over the last 19 nights, against "
                        "the night's rise before it: the latest leg gave back"),
    (-2.5, "crossed", "the latest leg crossed their 16:00 price"),
])
def test_the_leg_since_the_previous_checkpoint_against_the_night_before_it(premarket_scene_factory, nights_dir, after, code, words):
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5}, jumps={time(9, 10): after})
    assert said(ls, "premarket.since_checkpoint").endswith(words)
    assert verdict(ls, "premarket.since_checkpoint") == code and ls.gates["latest_leg_vs_night"] is None


def test_a_quiet_leg_or_a_night_without_direction_sleeps_the_latest_leg_question(premarket_scene_factory, nights_dir):
    quiet_leg = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5})
    assert quiet_leg.gates["latest_leg_vs_night"].startswith("a quiet leg: bottom third for 09:05-09:28")
    no_direction = read(premarket_scene_factory, nights_dir, {}, jumps={time(9, 10): 1.0})
    assert no_direction.gates["latest_leg_vs_night"].startswith("the night had no direction at the 09:05 checkpoint")
    assert verdict(no_direction, "premarket.since_checkpoint") is None


def test_since_checkpoint_measures_from_the_scheduled_checkpoint_not_from_a_late_fire(premarket_scene_factory, nights_dir):
    late = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.0}, jumps={time(8, 20): 1.0}, at=time(8, 52))
    assert "; since the 08:05 checkpoint they rose 1.00 sigma, top third for 08:05-08:52 over the last" in said(late, "premarket.since_checkpoint")


def test_the_previous_checkpoint_follows_frankfurts_clock():
    assert previous_checkpoint(datetime(2026, 10, 27, 8, 5, tzinfo=ET)) == datetime(2026, 10, 27, 4, 35, tzinfo=ET)
    assert previous_checkpoint(datetime(2026, 10, 27, 4, 38, tzinfo=ET)) == datetime(2026, 10, 27, 2, 35, tzinfo=ET)
    assert previous_checkpoint(datetime(2026, 9, 29, 8, 5, tzinfo=ET)) == datetime(2026, 9, 29, 3, 35, tzinfo=ET)


def test_the_first_checkpoint_has_no_leg_since_the_previous(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.0}, at=time(2, 35))
    assert ls.omitted["premarket.since_checkpoint"] == ls.gates["latest_leg_vs_night"] \
        == "the first checkpoint of the night: no earlier checkpoint to measure from"


# ---- premarket.release_vs_night and release_vs_night ----------------------------------------------------

@pytest.mark.parametrize("reaction, code, end", [
    (-2.5, "crossed_price", "it carried them across their 16:00 price"),
    (-0.8, "unwound_night", "it undid part of the night's move"),
    (1.0, "extended_night", "it went the night's way"),
])
def test_the_report_window_against_the_night_before_it(premarket_scene_factory, nights_dir, calendar, reaction, code, end):
    calendar([("08:30", "JOBS")])
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5}, jumps={time(8, 35): reaction}, at=time(8, 48))
    text = said(ls, "premarket.release_vs_night")
    assert "; before the jobs report at 08:30 they were 1.50 above, top third; by 08:45 they " in text and text.endswith(end)
    assert verdict(ls, "premarket.release_vs_night") == code and ls.gates["release_vs_night"] is None


def test_a_claims_only_morning_is_a_report_day_and_its_minute_starts_the_window(premarket_scene_factory, nights_dir, calendar):
    calendar([("08:30", "JOBLESS_CLAIMS")])
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5}, jumps={time(8, 35): 1.0})
    assert "before the weekly jobless claims report at 08:30" in said(ls, "premarket.release_vs_night")
    calendar([("08:15", "ADP")])
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5}, jumps={time(8, 20): 1.0})
    assert "at 08:15 they were" in said(ls, "premarket.release_vs_night") and ls.gates["release_vs_night"] is None
    assert "; before the report quiet; the report window up 1.00; " in said(ls, "premarket.legs")      # 08:00-08:15, then 08:15-08:45


def test_a_morning_without_a_report_sleeps_the_release_question(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5})
    assert ls.omitted["premarket.release_vs_night"] == ls.gates["release_vs_night"] == "no report before the open today"
    assert "premarket.release_vs_night" in ls.ended


def test_the_report_window_is_judged_only_once_it_ends(premarket_scene_factory, nights_dir, calendar):
    calendar([("08:30", "CPI")])
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5}, at=time(8, 40))
    assert ls.gates["release_vs_night"] == "the report window runs to 08:45"


def test_a_quiet_report_window_sleeps_the_release_question(premarket_scene_factory, nights_dir, calendar):
    calendar([("08:30", "CPI")])
    ls = read(premarket_scene_factory, nights_dir, {"europe_morning": 1.5}, at=time(8, 48))
    assert ls.gates["release_vs_night"].startswith("a quiet report window: bottom third for 08:30-08:45")


# ---- premarket.vs_last_hour and night_vs_last_hour ------------------------------------------------------

@pytest.mark.parametrize("night, code", [(1.5, "carried_on"), (-1.5, "turned_against")])
def test_the_night_against_yesterdays_last_hour(premarket_scene_factory, nights_dir, night, code):
    ls = read(premarket_scene_factory, nights_dir, {"asia": night}, yesterday_pct=0.5)
    text = said(ls, "premarket.vs_last_hour")
    assert "top third; yesterday SPX rose 0.50 sigma in its last hour, top third of the last 19 sessions' last hours: the night " in text
    assert verdict(ls, "premarket.vs_last_hour") == code and ls.gates["night_vs_last_hour"] is None


def test_a_quiet_last_hour_sleeps_the_last_hour_question(premarket_scene_factory, nights_dir):
    ls = read(premarket_scene_factory, nights_dir, {"asia": 1.5}, yesterday_pct=0.01)
    assert ls.gates["night_vs_last_hour"] == "yesterday's last hour was quiet, bottom third of the last 19 sessions' last hours"


# ---- replay and length -----------------------------------------------------------------------------------

def test_a_label_built_at_0928_equals_the_same_label_rebuilt_later_from_the_same_bars(premarket_scene_factory, nights_dir, calendar):
    calendar([("08:30", "JOBS")])
    path, jumps = {"asia": 1.0, "europe_morning": 0.6, "last_stretch": -0.4}, {time(8, 35): -0.8, time(9, 10): 0.5}
    at_the_time = [r for r in tonight(path, jumps) if datetime.fromisoformat(r["ts"]) + timedelta(minutes=1)
                   <= datetime.combine(DAY, time(9, 28), tzinfo=ET)]
    live = build_story_labels(scene(premarket_scene_factory, nights_dir, path, night=at_the_time))
    later = build_story_labels(scene(premarket_scene_factory, nights_dir, path, night=tonight(path, jumps)))
    assert (live.state, live.figures, live.gates, live.omitted) == (later.state, later.figures, later.gates, later.omitted)


@pytest.mark.parametrize("path", [
    {"asia": 1.0, "europe_open": -0.5, "europe_morning": 0.6, "pre_report": 0.3, "last_stretch": -0.4},
    {"after_close": 0.4, "asia": -1.0, "europe_morning": 0.6, "report_window": -0.3, "last_stretch": 0.4},
])
def test_every_sentence_stays_short(premarket_scene_factory, nights_dir, calendar, path):
    calendar([("08:30", "JOBS")])
    ls = read(premarket_scene_factory, nights_dir, path, yesterday_pct=0.5, jumps={time(9, 10): 0.5})
    assert set(ls.paths()) == set(LABELS) and not ls.omitted
    assert {p: len(s.split()) for g, labels in ls.state.items() for k, s in labels.items() if len(s.split()) > MAX_WORDS
            for p in [f"{g}.{k}"]} == {}
