"""The levels family (labels/levels.py): each label's sentence at its verdicts and their boundaries, its
omissions, the break_armed gate both ways, and that a bar finishing after the read never counts.

Every scene carries a 09:31 diary row, so the morning anchor is trusted (SIGMA points, 80 so that the cuts
land on exact figures). Yesterday (2026-09-17, the trading day before DAY) ranges from 7690 to 7710 every
minute unless a test says otherwise. The level_reclaim and siege blocks are shaped as the left-eye
scanner writes them."""
from __future__ import annotations

from datetime import timedelta

from conftest import at, bars_from_closes, make_row
from spx_jev.cuts import ACCEPT_MINUTES, WINDOW_30_MIN
from spx_jev.labels.levels import build_levels_labels, value_area

SIGMA = 80.0
YESTERDAY = "2026-09-17"


def session(ranges: list[tuple[float, float]], day: str = YESTERDAY) -> list[dict]:
    """One bar per minute from 09:30 with the given (low, high)."""
    start = at(9, 30, day)
    return [{"ts": (start + timedelta(minutes=i)).isoformat(), "open": lo, "high": hi, "low": lo, "close": hi, "volume": 0.0}
            for i, (lo, hi) in enumerate(ranges)]


def scene(scene_factory, now, closes, spot=None, row_over=None, rows_before=None, yesterday=None, **kw):
    """A read at ``now`` over today's ``closes`` from 09:30 (bars after ``now`` are on file but unfinished)."""
    over = {"sigma": SIGMA, **(row_over or {})}
    early = [make_row(at(9, 31), closes[0], sigma=SIGMA)]
    spot = spot if spot is not None else closes[min(len(closes), int((now - at(9, 30)).total_seconds() // 60)) - 1]
    prior = {YESTERDAY: session([(7690.0, 7710.0)] * 390)} if yesterday is None else yesterday
    return scene_factory(now, bars_from_closes(closes), row_over=over, rows_before=early + (rows_before or []), spot=spot,
                         prior_bars=prior, **kw)


def labels(sc):
    ls = build_levels_labels(sc)
    return {f"{g}.{k}": v for g, group in ls.state.items() for k, v in group.items()}, ls.omitted, ls.gates


# ---- levels.open_vs_prior_range

def test_the_settled_open_against_yesterdays_high_and_low_with_the_buffer(scene_factory):
    width = "yesterday's range was 0.25 sigma"
    cases = {
        7714.0: f"the settled open is 0.05 sigma above yesterday's high of the day, past the 0.03 sigma buffer; {width}",
        7711.0: ("the settled open is 0.01 sigma above yesterday's high of the day, within the 0.03 sigma buffer, so it counts as the "
                 f"upper half of yesterday's range; {width}"),
        7702.0: f"the settled open is inside yesterday's range, in its upper half, 0.10 sigma below yesterday's high of the day; {width}",
        7700.0: f"the settled open is inside yesterday's range, in its upper half, 0.12 sigma below yesterday's high of the day; {width}",
        7699.0: f"the settled open is inside yesterday's range, in its lower half, 0.11 sigma above yesterday's low of the day; {width}",
        7689.0: ("the settled open is 0.01 sigma below yesterday's low of the day, within the 0.03 sigma buffer, so it counts as the "
                 f"lower half of yesterday's range; {width}"),
        7680.0: f"the settled open is 0.12 sigma below yesterday's low of the day, past the 0.03 sigma buffer; {width}",
    }
    for settled, sentence in cases.items():
        got, _, _ = labels(scene(scene_factory, at(9, 35), [settled] * 5))
        assert got["levels.open_vs_prior_range"] == sentence


def test_the_open_waits_for_the_0934_bar_and_for_yesterday(scene_factory):
    _, omitted, _ = labels(scene(scene_factory, at(9, 34, ss=59), [7714.0] * 10))
    assert omitted["levels.open_vs_prior_range"] == "no settled open yet: the 09:34 bar has not finished"
    older = {"2026-09-16": session([(7690.0, 7710.0)] * 390, day="2026-09-16")}
    _, omitted, _ = labels(scene(scene_factory, at(10, 0), [7714.0] * 30, yesterday=older))
    assert omitted["levels.open_vs_prior_range"] == omitted["levels.prior_day"] == omitted["levels.prior_value"] == (
        "yesterday's bars are not on file: the newest stored session is 2026-09-16")
    _, omitted, _ = labels(scene(scene_factory, at(10, 0), [7714.0] * 30, yesterday={}))
    assert omitted["levels.prior_day"] == "no prior session's bars on file"


# ---- levels.prior_day

def test_price_beyond_yesterdays_high_is_accepted_after_20_minutes(scene_factory):
    closes = [7700.0] * 30 + [7715.0] * 60                                                      # closes above from the 10:00 bar
    got, _, _ = labels(scene(scene_factory, at(10, 40), closes))
    assert got["levels.prior_day"] == ("price crossed above yesterday's high 40 minutes ago and has spent 40 of those minutes above it, "
                                       "at or past the 20-minute acceptance time; it now sits 0.06 sigma above it, beyond the 0.05 sigma reach")
    got, _, _ = labels(scene(scene_factory, at(10, ACCEPT_MINUTES), closes))
    assert "has spent 20 of those minutes above it, at or past the 20-minute acceptance time" in got["levels.prior_day"]
    got, _, _ = labels(scene(scene_factory, at(10, ACCEPT_MINUTES - 1, ss=59), closes))        # the 10:19 bar has not finished
    assert "has spent 19 of those minutes above it, short of the 20-minute acceptance time" in got["levels.prior_day"]
    got, _, _ = labels(scene(scene_factory, at(10, 0, ss=30), [7708.0] * 40, spot=7711.0))
    assert got["levels.prior_day"] == ("price is above yesterday's high now and no finished minute has closed above it yet; it now sits "
                                       "0.01 sigma above it, within the 0.05 sigma reach")


def test_price_inside_yesterdays_range_back_near_or_away(scene_factory):
    back = [7700.0] * 30 + [7715.0] * 10 + [7706.0] * 20                                        # the 10:10 bar opens at 7715
    got, _, _ = labels(scene(scene_factory, at(10, 30), back))
    assert got["levels.prior_day"] == "price is back inside yesterday's range, 0.05 sigma below yesterday's high, after trading above it 19 minutes ago"
    got, _, _ = labels(scene(scene_factory, at(10, 30), [7706.8] * 60))
    assert got["levels.prior_day"] == ("price is inside yesterday's range, 0.04 sigma below yesterday's high, within the 0.05 sigma "
                                       "reach, and has not traded above it today")
    got, _, _ = labels(scene(scene_factory, at(10, 30), [7702.0] * 60))
    assert got["levels.prior_day"] == ("price is inside yesterday's range, 0.10 sigma below its high and 0.15 sigma above its low, "
                                       "beyond the 0.05 sigma reach of both, and has not traded outside it today")
    unfinished = [7702.0] * 60 + [7715.0] + [7702.0] * 10                                      # the 10:30 bar pokes above
    got, _, _ = labels(scene(scene_factory, at(10, 30, ss=40), unfinished, spot=7702.0))
    assert got["levels.prior_day"].startswith("price is inside yesterday's range, 0.10 sigma below its high")


# ---- levels.prior_value

def test_the_value_area_grows_from_the_busiest_bin_to_70_percent_of_the_minutes():
    bars = session([(7700.0, 7701.0)] * 6 + [(7701.0, 7702.0)] * 2 + [(7699.0, 7700.0)] + [(7702.0, 7703.0)])
    assert value_area(bars) == (7700.0, 7702.0, 7700.5)
    apart = session([(7690.0, 7691.0)] * 3 + [(7710.0, 7711.0)] * 7)                             # two clusters with nothing between
    assert value_area(apart) == (7710.0, 7711.0, 7710.5)
    assert value_area(session([(7700.0, 7700.0)] * 5)) is None


def test_price_against_yesterdays_value_area(scene_factory):
    y = {YESTERDAY: session([(7700.0, 7701.0)] * 6 + [(7701.0, 7702.0)] * 2 + [(7699.0, 7700.0)] + [(7702.0, 7703.0)])}
    band = "yesterday's value area (the band where SPX spent 70% of yesterday's session, 0.03 sigma wide)"
    cases = {
        7710.0: f"price is 0.10 sigma above the high of {band}, beyond the 0.02 sigma edge distance; yesterday's busiest level is 0.12 sigma below price",
        7703.7: f"price is 0.02 sigma above the high of {band}, beyond the 0.02 sigma edge distance; yesterday's busiest level is 0.04 sigma below price",
        7703.5: f"price is 0.02 sigma above the high of {band}, within the 0.02 sigma edge distance; yesterday's busiest level is 0.04 sigma below price",
        7701.0: f"price is inside {band}, 0.01 sigma above its low and 0.01 sigma below its high; yesterday's busiest level is 0.01 sigma below price",
        7690.0: f"price is 0.12 sigma below the low of {band}, beyond the 0.02 sigma edge distance; yesterday's busiest level is 0.13 sigma above price",
    }
    for spot, sentence in cases.items():
        got, _, _ = labels(scene(scene_factory, at(10, 30), [7700.0] * 90, spot=spot, yesterday=y))
        assert got["levels.prior_value"] == sentence
    _, omitted, _ = labels(scene(scene_factory, at(10, 30), [7700.0] * 60, yesterday={YESTERDAY: session([(7700.0, 7700.0)] * 5)}))
    assert omitted["levels.prior_value"] == "yesterday's bars have no range to profile"


# ---- levels.round_number

def test_a_round_level_pushed_through_from_beyond_the_near_distance(scene_factory):
    up = [7692.0] * 70 + [7693.0 + i for i in range(10)] + [7704.0] * 10                     # 7701 first closes above, at 10:49
    got, _, _ = labels(scene(scene_factory, at(11, 0), up))
    assert got["levels.round_number"] == ("11 minutes ago price rose through a round 100-point level and now sits 0.05 sigma above it; "
                                          "20 minutes ago it was 0.10 sigma below, past the 0.06 sigma near distance")
    down = [7758.0] * 80 + [7746.0] * 10
    got, _, _ = labels(scene(scene_factory, at(11, 0), down))
    assert got["levels.round_number"] == ("9 minutes ago price fell through a round 50-point level and now sits 0.05 sigma below it; "
                                          "20 minutes ago it was 0.10 sigma above, past the 0.06 sigma near distance")


def test_a_round_level_pressed_but_not_pushed_through(scene_factory):
    # 20 minutes ago 4.9 points under 7700 counts as starting beyond the 4.8-point near distance; 4.7 does not
    got, _, _ = labels(scene(scene_factory, at(11, 0), [7695.1] * 70 + [7704.0] * 20))
    assert got["levels.round_number"].startswith("19 minutes ago price rose through a round 100-point level")
    got, _, _ = labels(scene(scene_factory, at(11, 0), [7695.3] * 70 + [7704.0] * 20))
    assert got["levels.round_number"] == ("price sits 0.05 sigma above a round 100-point level, within the 0.06 sigma near distance, "
                                          "pressing it from above without a push through in the last 20 minutes; 20 minutes ago it was "
                                          "0.06 sigma below it")
    got, _, _ = labels(scene(scene_factory, at(11, 0), [7696.8] * 90))
    assert got["levels.round_number"] == ("price sits 0.04 sigma below a round 100-point level, within the 0.06 sigma near distance, "
                                          "pressing it from below without a push through in the last 20 minutes; 20 minutes ago it was "
                                          "0.04 sigma below it")
    got, _, _ = labels(scene(scene_factory, at(9, 40), [7696.8] * 10))
    assert got["levels.round_number"].endswith("in the last 20 minutes; at the open it was 0.04 sigma below it")


def test_no_round_level_in_play_and_an_unfinished_crossing(scene_factory):
    _, omitted, _ = labels(scene(scene_factory, at(11, 0), [7692.0] * 90))
    assert omitted["levels.round_number"] == ("no round level in play: the nearest is 0.10 sigma above price, beyond the 0.06 sigma near "
                                              "distance, and none was pushed through in the last 20 minutes")
    unfinished = [7692.0] * 90 + [7704.0] * 5                                                   # the 11:00 bar crosses
    _, omitted, _ = labels(scene(scene_factory, at(11, 0, ss=30), unfinished, spot=7692.0))
    assert omitted["levels.round_number"].startswith("no round level in play")


# ---- levels.break_armed and its gate

def reclaim(state: str, **over) -> dict:
    return {"level_reclaim": {"score": 0.3, "confidence": 0.4, "fired": False, "direction": "call", "level": 7700.0, "level_kind": "round",
                              "break_state": state, "cocked_at": None, "cock_level": None, "cock_level_kind": None, "cock_direction": None,
                              "cock_count_today": 1, "cock_age_min": None, "fire_gates": None, **over}}


def test_an_armed_break_names_its_way_its_level_and_its_clock(scene_factory):
    now = at(11, 0)
    armed = reclaim("cocked", cocked_at=(now - timedelta(minutes=14)).isoformat(), cock_level=7700.0, cock_level_kind="prior_close",
                    cock_direction="call", cock_age_min=14.0)
    got, _, gates = labels(scene(scene_factory, now, [7690.4] * 90, row_over=armed))
    assert got["levels.break_armed"] == ("a break upward is armed: 14 minutes ago price was turned back at yesterday's close while the book "
                                         "leaned to puts, and it now sits 0.12 sigma below that level; it expires in 31 minutes if price "
                                         "does not close through that level")
    assert gates["break_armed"] is None
    down = reclaim("cocked", cocked_at=(now - timedelta(minutes=44)).isoformat(), cock_level=7750.0, cock_level_kind="round",
                   cock_direction="put")
    got, _, _ = labels(scene(scene_factory, now, [7752.0] * 90, row_over=down))
    assert got["levels.break_armed"].startswith("a break downward is armed: 44 minutes ago price was turned back at a round 50-point level")
    assert got["levels.break_armed"].endswith("it expires in 1 minute if price does not close through that level")


def test_a_break_that_ended_in_the_last_30_minutes_keeps_the_question_awake(scene_factory):
    now = at(11, 0)
    ended = {"cocked_at": (now - timedelta(minutes=55)).isoformat(), "cock_level": 7650.0, "cock_level_kind": "put_wall", "cock_direction": "put"}
    for ago, state, how in ((10, "expired", "expired without firing"), (WINDOW_30_MIN, "decocked", "was called off")):
        rows = [make_row(now - timedelta(minutes=ago), 7700.0, **reclaim(state, **ended))]
        got, _, gates = labels(scene(scene_factory, now, [7700.0] * 90, row_over=reclaim("idle"), rows_before=rows))
        assert got["levels.break_armed"] == (f"no break is armed now: a break downward armed at the put-side heavy strike {how} "
                                             f"{ago} minutes ago, within the last 30 minutes")
        assert gates["break_armed"] is None
    rows = [make_row(now - timedelta(minutes=WINDOW_30_MIN, seconds=1), 7700.0, **reclaim("expired", **ended))]
    got, _, gates = labels(scene(scene_factory, now, [7700.0] * 90, row_over=reclaim("idle"), rows_before=rows))
    assert got["levels.break_armed"] == "no break is armed, and none expired or was called off in the last 30 minutes"
    assert gates["break_armed"] == "no break armed, and none expired or was called off in the last 30 minutes"


def test_a_row_without_a_break_read_omits_the_label_and_sleeps_the_question(scene_factory):
    _, omitted, gates = labels(scene(scene_factory, at(11, 0), [7700.0] * 90))
    assert omitted["levels.break_armed"] == "row carries no break read (level_reclaim.break_state)"
    assert gates["break_armed"] == "row carries no break read"
    _, omitted, gates = labels(scene(scene_factory, at(11, 0), [7700.0] * 90, row_over=reclaim("cocked")))
    assert omitted["levels.break_armed"] == "the armed break carries no direction, level or arming time" and gates["break_armed"] is None


# ---- levels.wall_touch_effort

def tower(kind, level, status="engaged", verdict=None, effort=None, outcome=None):
    return {"kind": kind, "level": level, "status": status, "effort_pct": effort, "verdict": verdict, "outcome": outcome, "near_spot": False}


def siege(*towers, health="OK", baseline="robust"):
    return {"siege": {"health": health, "baseline": baseline, "saturated": False, "ratio": 10.04, "towers": list(towers)}}


def touch_scene(scene_factory, spot, kind="call_wall", level=7705.0, effort=83.3, verdict="SIEGE", began=12):
    now = at(11, 0)
    rows = [make_row(now - timedelta(minutes=began + 5), spot, **siege(tower(kind, level, "watching"))),
            make_row(now - timedelta(minutes=began), spot, **siege(tower(kind, level)))]
    judged = siege(tower(kind, level, verdict=verdict, effort=effort))
    return scene(scene_factory, now, [spot] * 90, row_over=judged, rows_before=rows)


def test_a_touch_still_touching_says_how_hard_spy_traded(scene_factory):
    got, _, _ = labels(touch_scene(scene_factory, 7700.0))
    assert got["levels.wall_touch_effort"] == (
        "price tested the call-side heavy strike 12 minutes ago and is now 0.06 sigma back from it, not back by more than the 0.10 sigma "
        "touch rule, so it is still touching; SPY volume during the touch was in the 83rd percentile of normal, the top band for those "
        "minutes (at or above the 70th percentile)")
    got, _, _ = labels(touch_scene(scene_factory, 7697.0, effort=70.0))                          # exactly the touch rule back
    assert ("0.10 sigma back from it, not back by more than the 0.10 sigma touch rule, so it is still touching; SPY volume during the "
            "touch was in the 70th percentile of normal, the top band for those minutes") in got["levels.wall_touch_effort"]
    got, _, _ = labels(touch_scene(scene_factory, 7700.0, effort=69.0, verdict="NEUTRAL"))
    assert got["levels.wall_touch_effort"].endswith("in the 69th percentile of normal, the middle band for those minutes "
                                                    "(between the 30th and 70th percentiles)")
    got, _, _ = labels(touch_scene(scene_factory, 7700.0, effort=30.0, verdict="QUIET"))
    assert got["levels.wall_touch_effort"].endswith("the bottom band for those minutes (at or below the 30th percentile)")


def test_a_strike_that_held_or_broke(scene_factory):
    got, _, _ = labels(touch_scene(scene_factory, 7690.0))
    assert got["levels.wall_touch_effort"].startswith("price tested the call-side heavy strike 12 minutes ago and is now 0.19 sigma back "
                                                      "from it, more than the 0.10 sigma touch rule, so the strike held; SPY volume")
    got, _, _ = labels(touch_scene(scene_factory, 7700.0, kind="put_wall", level=7710.0, effort=None, verdict=None))
    assert got["levels.wall_touch_effort"] == ("price went through the put-side heavy strike, first touched 12 minutes ago, and sits "
                                               "0.12 sigma past it, more than the 0.10 sigma touch rule, so it broke through")


def test_a_touch_that_began_before_the_half_hour_counts_while_it_lasts(scene_factory):
    got, _, _ = labels(touch_scene(scene_factory, 7700.0, began=WINDOW_30_MIN + 10))             # still engaged at the read
    assert got["levels.wall_touch_effort"].startswith("price tested the call-side heavy strike 40 minutes ago and is now 0.06 sigma "
                                                      "back from it, not back by more than the 0.10 sigma touch rule, so it is still touching")
    now = at(11, 0)
    rows = [make_row(now - timedelta(minutes=45), 7700.0, **siege(tower("put_wall", 7710.0))),
            make_row(now - timedelta(minutes=20), 7700.0, **siege(tower("put_wall", 7710.0))),
            make_row(now - timedelta(minutes=15), 7700.0, **siege(tower("put_wall", 7710.0, "resolved")))]
    over = siege(tower("put_wall", 7710.0, "resolved", outcome="BREAK"))
    got, _, _ = labels(scene(scene_factory, now, [7700.0] * 90, row_over=over, rows_before=rows))
    assert got["levels.wall_touch_effort"] == ("price went through the put-side heavy strike, first touched 45 minutes ago, and sits "
                                               "0.12 sigma past it, more than the 0.10 sigma touch rule, so it broke through")


def test_the_wall_touch_is_omitted_without_a_recent_judged_touch_or_a_sound_feed(scene_factory):
    _, omitted, _ = labels(touch_scene(scene_factory, 7700.0, effort=None, verdict=None))
    assert omitted["levels.wall_touch_effort"] == ("price is still touching the call-side heavy strike and the siege box has not judged the "
                                                   "touch's SPY volume: its window is still open, or the strike hugged price all along")
    now = at(11, 0)
    ended = [make_row(now - timedelta(minutes=45), 7700.0, **siege(tower("call_wall", 7705.0))),
             make_row(now - timedelta(minutes=WINDOW_30_MIN, seconds=1), 7700.0, **siege(tower("call_wall", 7705.0, "resolved")))]
    over = siege(tower("call_wall", 7705.0, "resolved", verdict="SIEGE", effort=83.3, outcome="HOLD"))
    _, omitted, _ = labels(scene(scene_factory, now, [7700.0] * 90, row_over=over, rows_before=ended))
    assert omitted["levels.wall_touch_effort"] == "no heavy strike was touched in the last 30 minutes"
    _, omitted, _ = labels(touch_scene(scene_factory, 7700.0, kind="magnet", level=7700.0))
    assert omitted["levels.wall_touch_effort"] == "no heavy strike was touched in the last 30 minutes"
    for over, reason in ((siege(health="FEED-LOST"), "the siege box's SPY feed is FEED-LOST, not OK"),
                         (siege(baseline="warming"), "the siege box's volume baseline is warming, not yet robust"),
                         ({"siege": None}, "row carries no siege read")):
        _, omitted, _ = labels(scene(scene_factory, at(11, 0), [7700.0] * 90, row_over=over))
        assert omitted["levels.wall_touch_effort"] == reason
