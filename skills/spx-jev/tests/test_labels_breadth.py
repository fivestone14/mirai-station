"""The breadth family (labels/breadth.py): each label's sentence at its verdicts and their boundaries, its
omissions, and that a value known after the read never counts.

The market context is built as the context job saves it (market_context.py): $TICK and $TRIN a reading a
minute, $UVOL and $DVOL (thousands of shares) and $VOLD and $VOLSPD (shares) running totals since 09:30,
each value known once its minute has finished."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta

from conftest import DAY, at, bars_from_closes, flat_bars, make_row
from spx_jev import events
from spx_jev.labels.breadth import build_breadth_labels
from spx_jev.labels.price import build_price_labels
from spx_jev.state_builder import MarketContext

PRIOR_DAYS = [f"2026-09-{d:02d}" for d in range(17, 7, -1)]


def minute_ends(start: datetime, n: int) -> list[datetime]:
    """When each of ``n`` one-minute bars starting at ``start`` finished."""
    return [start + timedelta(minutes=i + 1) for i in range(n)]


def running_totals(per_minute: list[float], day: str | None = None) -> list[tuple[datetime, float]]:
    """A running total since 09:30 from what each minute added."""
    out, total = [], 0.0
    for t, v in zip(minute_ends(at(9, 30, **({"day": day} if day else {})), len(per_minute)), per_minute):
        total += v
        out.append((t, total))
    return out


def upvol(up: list[float], down: list[float]) -> dict[str, list[tuple[datetime, float]]]:
    return {"$UVOL": running_totals(up), "$DVOL": running_totals(down)}


def readings(end: datetime, values: list[float], day: str | None = None) -> list[tuple[datetime, float]]:
    """One reading a minute, the last known at ``end``."""
    n = len(values)
    if day is not None:
        end = datetime.combine(datetime.fromisoformat(day).date(), end.timetz())
    return [(end - timedelta(minutes=n - 1 - i), v) for i, v in enumerate(values)]


def read(scene_factory, now: datetime, known: dict, prior_known: dict[str, dict] | None = None):
    scene = scene_factory(now, flat_bars(int((now - at(9, 30)).total_seconds() // 60)), market=MarketContext(known))
    if prior_known is not None:
        scene = replace(scene, prior_markets={d: MarketContext(k) for d, k in prior_known.items()})
    return build_breadth_labels(scene)


def sentence(ls, path: str) -> str:
    group, key = path.split(".", 1)
    assert path not in ls.omitted, ls.omitted.get(path)
    return ls.state[group][key]


# ---- breadth.upvol_share_30m

NOW = at(12, 32, ss=10)
MINUTES_TO_NOW = 182          # bars finished by 12:32:10


def upvol_last_30(up_share: float, extra: tuple[float, float] | None = None) -> dict:
    """Even volume until the last 30 minutes, then ``up_share`` of 10,000 a minute; ``extra`` is one more
    minute after the read."""
    up = [5000.0] * (MINUTES_TO_NOW - 30) + [10000.0 * up_share] * 30
    down = [5000.0] * (MINUTES_TO_NOW - 30) + [10000.0 * (1 - up_share)] * 30
    if extra:
        up.append(extra[0])
        down.append(extra[1])
    return upvol(up, down)


def test_the_30_minute_up_volume_share_and_its_lean_lines(scene_factory):
    assert sentence(read(scene_factory, NOW, upvol_last_30(0.61)), "breadth.upvol_share_30m") == \
        "over the last 30 minutes 61% of NYSE volume traded in rising stocks, past the 60% lean line on the buy side"
    assert sentence(read(scene_factory, NOW, upvol_last_30(0.38)), "breadth.upvol_share_30m") == \
        "over the last 30 minutes 38% of NYSE volume traded in rising stocks, past the 40% lean line on the sell side"
    assert sentence(read(scene_factory, NOW, upvol_last_30(0.52)), "breadth.upvol_share_30m") == \
        "over the last 30 minutes 52% of NYSE volume traded in rising stocks, inside the 40% to 60% even band"


def test_the_lean_lines_are_judged_as_the_sentence_prints_the_share(scene_factory):
    on_the_line = sentence(read(scene_factory, NOW, upvol_last_30(0.604)), "breadth.upvol_share_30m")
    assert on_the_line.endswith("60% of NYSE volume traded in rising stocks, inside the 40% to 60% even band")
    assert sentence(read(scene_factory, NOW, upvol_last_30(0.4)), "breadth.upvol_share_30m").endswith("inside the 40% to 60% even band")
    assert sentence(read(scene_factory, NOW, upvol_last_30(0.606)), "breadth.upvol_share_30m").endswith("61% of NYSE volume traded "
                                                                                                       "in rising stocks, past the 60% lean line on the buy side")


def test_the_30_minute_share_is_omitted_when_the_feed_stopped(scene_factory):
    stopped = {s: [(t, v) for t, v in pts if t <= NOW - timedelta(minutes=10)] for s, pts in upvol_last_30(0.61).items()}
    ls = read(scene_factory, NOW, stopped)
    assert ls.omitted["breadth.upvol_share_30m"] == ("no NYSE up and down volume known both 30 minutes ago and now, within 5 minutes "
                                                     "of each: the market-context job stopped or has not saved them")


def test_a_minute_that_finishes_after_the_read_does_not_count_toward_the_share(scene_factory):
    selling_after = upvol_last_30(0.61, extra=(0.0, 900000.0))           # its bar finishes at 12:33
    assert sentence(read(scene_factory, NOW, selling_after), "breadth.upvol_share_30m").startswith("over the last 30 minutes 61%")


# ---- breadth.volume_vs_count_30m

def trin_read(scene_factory, values: list[float]):
    return read(scene_factory, NOW, {"$TRIN": readings(at(12, 32), values)})


def test_trin_over_30_minutes_and_its_two_lines(scene_factory):
    assert sentence(trin_read(scene_factory, [0.78] * 30), "breadth.volume_vs_count_30m") == (
        "over the last 30 minutes NYSE TRIN averaged 0.78, under the 0.85 line: volume per rising stock ran heavier than volume "
        "per falling stock")
    assert sentence(trin_read(scene_factory, [1.1, 1.3] * 15), "breadth.volume_vs_count_30m") == (
        "over the last 30 minutes NYSE TRIN averaged 1.20, over the 1.15 line: volume per falling stock ran heavier than volume "
        "per rising stock")
    assert sentence(trin_read(scene_factory, [0.99] * 30), "breadth.volume_vs_count_30m") == (
        "over the last 30 minutes NYSE TRIN averaged 0.99, between the 0.85 and 1.15 lines: volume per rising stock and per "
        "falling stock matched")


def test_trin_on_either_line_is_matched(scene_factory):
    assert "averaged 0.85, between the 0.85 and 1.15 lines" in sentence(trin_read(scene_factory, [0.85] * 30), "breadth.volume_vs_count_30m")
    assert "averaged 1.15, between the 0.85 and 1.15 lines" in sentence(trin_read(scene_factory, [1.15] * 30), "breadth.volume_vs_count_30m")
    assert "averaged 0.84, under the 0.85 line" in sentence(trin_read(scene_factory, [0.844] * 30), "breadth.volume_vs_count_30m")


def test_trin_needs_20_readings_and_never_one_after_the_read(scene_factory):
    ls = trin_read(scene_factory, [0.78] * 19)
    assert ls.omitted["breadth.volume_vs_count_30m"] == "needs 20 NYSE TRIN readings in the last 30 minutes, have 19"
    later = {"$TRIN": readings(at(12, 32), [0.99] * 30) + [(at(12, 33), 9.0)]}
    assert "averaged 0.99" in sentence(read(scene_factory, NOW, later), "breadth.volume_vs_count_30m")


# ---- breadth.tick_side_vs_usual

def ticks(above: int, n: int = 30) -> list[float]:
    """``n`` TICK closes, ``above`` of them above zero, spread through the window."""
    return [310.0 if i < above else -240.0 for i in range(n)]


def prior_ticks(shares_above: list[int]) -> dict[str, dict]:
    """Each prior session's last 30 minutes of TICK closes at this clock, ``k`` of 30 above zero."""
    return {d: {"$TICK": readings(at(12, 32), ticks(k), day=d)} for d, k in zip(PRIOR_DAYS, shares_above)}


USUAL_14_OF_30 = [10, 12, 14, 14, 14, 16, 18, 20]     # median 14 of 30


def tick_side(scene_factory, above: int, prior=USUAL_14_OF_30, extra: list | None = None):
    today = readings(at(12, 32), ticks(above)) + (extra or [])
    return read(scene_factory, NOW, {"$TICK": today}, prior_ticks(prior))


def test_the_tick_side_against_the_usual_for_this_half_hour(scene_factory):
    assert sentence(tick_side(scene_factory, 21), "breadth.tick_side_vs_usual") == (
        "over the last 30 minutes NYSE TICK sat above zero 21 of 30 minutes, 0.23 more than usual for this half hour: "
        "past the usual band (0.15), inside the far band (0.30)")
    assert sentence(tick_side(scene_factory, 5), "breadth.tick_side_vs_usual") == (
        "over the last 30 minutes NYSE TICK sat above zero 5 of 30 minutes, 0.30 less than usual for this half hour: "
        "past the usual band (0.15), inside the far band (0.30)")
    assert sentence(tick_side(scene_factory, 14), "breadth.tick_side_vs_usual") == (
        "over the last 30 minutes NYSE TICK sat above zero 14 of 30 minutes, the same as usual for this half hour: "
        "within the usual band (0.15)")
    assert sentence(tick_side(scene_factory, 28), "breadth.tick_side_vs_usual").endswith(
        "0.47 more than usual for this half hour: past the far band (0.30)")


def test_a_gap_on_a_band_is_inside_it(scene_factory):
    usual_half = [15] * 6                                  # the prior sessions sat above zero 15 of 30 minutes
    on_usual = read(scene_factory, NOW, {"$TICK": readings(at(12, 32), ticks(13, 20))}, prior_ticks(usual_half))
    assert sentence(on_usual, "breadth.tick_side_vs_usual").endswith(
        "above zero 13 of 20 minutes, 0.15 more than usual for this half hour: within the usual band (0.15)")
    on_far = read(scene_factory, NOW, {"$TICK": readings(at(12, 32), ticks(4, 20))}, prior_ticks(usual_half))
    assert sentence(on_far, "breadth.tick_side_vs_usual").endswith(
        "above zero 4 of 20 minutes, 0.30 less than usual for this half hour: past the usual band (0.15), inside the far band (0.30)")


def test_the_tick_side_needs_its_readings_and_the_prior_sessions(scene_factory):
    few = read(scene_factory, NOW, {"$TICK": readings(at(12, 32), ticks(10, 19))}, prior_ticks(USUAL_14_OF_30))
    assert few.omitted["breadth.tick_side_vs_usual"] == "needs 20 NYSE TICK readings in the last 30 minutes, have 19"
    thin = tick_side(scene_factory, 21, prior=[14] * 4)
    assert thin.omitted["breadth.tick_side_vs_usual"] == "needs 5 prior sessions with NYSE TICK readings in this half hour, have 4"


def test_a_tick_reading_after_the_read_does_not_count(scene_factory):
    after = [(at(12, 33), 500.0), (at(12, 34), 500.0)]
    assert "above zero 21 of 30 minutes" in sentence(tick_side(scene_factory, 21, extra=after), "breadth.tick_side_vs_usual")


def test_no_market_context_omits_every_breadth_label_but_the_dark_one(scene_factory):
    ls = build_breadth_labels(scene_factory(NOW, flat_bars(MINUTES_TO_NOW)))
    assert set(ls.omitted.values()) == {"no market-context snapshot today"}
    assert "breadth.nasdaq_net_volume" not in ls.omitted and "breadth.upvol_share_30m" in ls.omitted


# ---- breadth.day_upvol_share

def day_share(scene_factory, blocks: list[tuple[int, float, float]], now: datetime = NOW, extra: tuple[float, float] | None = None):
    """Up and down volume from 09:30 in blocks of (minutes, volume a minute, share of it in rising stocks)."""
    up = [per * share for n, per, share in blocks for _ in range(n)]
    down = [per * (1 - share) for n, per, share in blocks for _ in range(n)]
    if extra:
        up.append(extra[0])
        down.append(extra[1])
    return read(scene_factory, now, upvol(up, down))


def test_a_one_sided_day(scene_factory):
    assert sentence(day_share(scene_factory, [(182, 10000.0, 0.85)]), "breadth.day_upvol_share") == (
        "since the open 85% of NYSE volume went into rising stocks, past the 80% one-sided share on the buy side; "
        "the 30-minute share has not crossed 50% today, under the 4-crossing rotation rule")
    assert sentence(day_share(scene_factory, [(182, 10000.0, 0.18)]), "breadth.day_upvol_share").startswith(
        "since the open 18% of NYSE volume went into rising stocks, past the 20% one-sided share on the sell side; ")


def test_the_one_sided_line_is_judged_as_the_sentence_prints_the_share(scene_factory):
    assert "80% of NYSE volume went into rising stocks, past the 80% one-sided share on the buy side" in \
        sentence(day_share(scene_factory, [(182, 10000.0, 0.796)]), "breadth.day_upvol_share")
    assert "79% of NYSE volume went into rising stocks, short of the 20% and 80% one-sided shares, past the 60% lean line on the buy side" in \
        sentence(day_share(scene_factory, [(182, 10000.0, 0.794)]), "breadth.day_upvol_share")
    assert "20% of NYSE volume went into rising stocks, past the 20% one-sided share on the sell side" in \
        sentence(day_share(scene_factory, [(182, 10000.0, 0.2)]), "breadth.day_upvol_share")


def test_a_day_that_faded_from_one_side_says_where_it_stood(scene_factory):
    faded = day_share(scene_factory, [(60, 10000.0, 0.9), (122, 20000.0, 0.4)])
    assert sentence(faded, "breadth.day_upvol_share") == (
        "since the open 50% of NYSE volume went into rising stocks, short of the 20% and 80% one-sided shares, inside the 40% to 60% "
        "even band; earlier today it stood past a one-sided share, at 90% at 10:00; the 30-minute share crossed 50% once today, "
        "under the 4-crossing rotation rule")
    leaning = day_share(scene_factory, [(182, 10000.0, 0.66)])
    assert sentence(leaning, "breadth.day_upvol_share") == (
        "since the open 66% of NYSE volume went into rising stocks, short of the 20% and 80% one-sided shares, past the 60% lean line "
        "on the buy side; it has not stood past a one-sided share since 10:00; the 30-minute share has not crossed 50% today, "
        "under the 4-crossing rotation rule")


def test_a_rotating_day_and_the_rotation_rule(scene_factory):
    rotating = [(30, 10000.0, 0.7 if k % 2 == 0 else 0.3) for k in range(7)]
    assert sentence(day_share(scene_factory, rotating), "breadth.day_upvol_share").endswith(
        "the 30-minute share crossed 50% 5 times today, at or past the 4-crossing rotation rule")
    assert sentence(day_share(scene_factory, rotating, now=at(12, 2, ss=10)), "breadth.day_upvol_share").endswith(
        "the 30-minute share crossed 50% 4 times today, at or past the 4-crossing rotation rule")
    assert sentence(day_share(scene_factory, rotating, now=at(11, 32, ss=10)), "breadth.day_upvol_share").endswith(
        "the 30-minute share crossed 50% 3 times today, under the 4-crossing rotation rule")


def test_rotation_is_counted_between_whole_half_hours_not_minute_by_minute(scene_factory):
    hovering = [(1, 10000.0, 0.53 if i % 2 == 0 else 0.47) for i in range(182)]
    assert sentence(day_share(scene_factory, hovering), "breadth.day_upvol_share").endswith(
        "the 30-minute share has not crossed 50% today, under the 4-crossing rotation rule")


def test_the_day_share_waits_half_an_hour_and_for_a_live_feed(scene_factory):
    early = day_share(scene_factory, [(29, 10000.0, 0.85)], now=at(9, 59, ss=10))
    assert early.omitted["breadth.day_upvol_share"] == "needs 30 minutes of session"
    stopped = read(scene_factory, NOW, {s: [(t, v) for t, v in pts if t <= at(12, 20)]
                                        for s, pts in upvol([8500.0] * 182, [1500.0] * 182).items()})
    assert stopped.omitted["breadth.day_upvol_share"] == ("no NYSE up and down volume known within 5 minutes of now: the market-context "
                                                         "job stopped or has not saved them")


def test_a_minute_after_the_read_does_not_count_toward_the_day(scene_factory):
    late_selling = day_share(scene_factory, [(182, 10000.0, 0.85)], extra=(0.0, 5_000_000.0))
    assert sentence(late_selling, "breadth.day_upvol_share").startswith("since the open 85% of NYSE volume")


# ---- breadth.members_net_day

ANCHOR_ROW = make_row(at(9, 32), 7700.0, sigma=75.0, sigma_anchor=75.0)


def members_read(scene_factory, members_m: float, nyse_m: float, closes: list[float] | None = None, now: datetime = NOW,
                 prior_members=(-50, -50, 50, 50, 0), prior_nyse=(-100, -100, 100, 100, 0), today_extra: dict | None = None, age_min: int = 1):
    """Today's net volume totals ``age_min`` minutes before the read; each prior session's a minute before the
    same clock (sample spreads of 50M and 100M)."""
    known = {"$VOLSPD": [(now - timedelta(minutes=age_min), members_m * 1e6)], "$VOLD": [(now - timedelta(minutes=age_min), nyse_m * 1e6)]}
    for s, pts in (today_extra or {}).items():
        known[s] = known[s] + pts
    prior = {d: {"$VOLSPD": readings(now - timedelta(minutes=1), [m * 1e6], day=d), "$VOLD": readings(now - timedelta(minutes=1), [n * 1e6], day=d)}
             for d, m, n in zip(PRIOR_DAYS, prior_members, prior_nyse)}
    closes = closes or [7700.0] * 5 + [7734.5] * 177
    scene = scene_factory(now, bars_from_closes(closes), rows_before=[ANCHOR_ROW], market=MarketContext(known))
    return build_breadth_labels(replace(scene, prior_markets={d: MarketContext(k) for d, k in prior.items()}))


def test_members_net_volume_against_price_and_the_nyse(scene_factory):
    assert sentence(members_read(scene_factory, -37, 10), "breadth.members_net_day") == (
        "since the open S&P 500 members' net volume is -37M, 0.74 times the usual swing for 12:32 on the sell side, below zero "
        "while SPX is 0.46 sigma above its settled open, past the 0.10 sigma open-day line; NYSE net volume is +10M, 0.10 times "
        "the usual swing for 12:32 on the buy side; S&P 500 net volume runs 0.84 swings weaker than NYSE's, past the 0.5 split line")
    assert sentence(members_read(scene_factory, 45, -20), "breadth.members_net_day").endswith(
        "S&P 500 net volume runs 1.10 swings stronger than NYSE's, past the 0.5 split line")


def test_the_split_line_and_the_open_day_line_hold_their_edges(scene_factory):
    on_split = sentence(members_read(scene_factory, 30, 10), "breadth.members_net_day")
    assert on_split.endswith("S&P 500 net volume is matched with NYSE's, 0.50 swings apart, within the 0.5 split line")
    near_open = sentence(members_read(scene_factory, 30, 10, closes=[7700.0] * 5 + [7707.5] * 177), "breadth.members_net_day")
    assert "above zero while SPX is +0.10 sigma from its settled open, within the 0.10 sigma open-day line;" in near_open
    at_zero = sentence(members_read(scene_factory, 0.4, 10), "breadth.members_net_day")
    assert at_zero.startswith("since the open S&P 500 members' net volume is +0M, 0.01 times the usual swing for 12:32 on the buy side, at zero")


def test_the_open_day_line_is_judged_as_price_day_move_judges_it(scene_factory):
    just_past = [7700.0] * 5 + [7707.5225] * 177                          # 0.1003 sigma above the settled open
    members = sentence(members_read(scene_factory, 30, 10, closes=just_past), "breadth.members_net_day")
    assert "above zero while SPX is 0.10 sigma above its settled open, past the 0.10 sigma open-day line;" in members
    day_move = build_price_labels(scene_factory(NOW, bars_from_closes(just_past), rows_before=[ANCHOR_ROW])).state["price"]["day_move"]
    assert "past the 0.10 sigma open-day line" in day_move


def test_members_net_volume_omitted_without_its_series_or_the_prior_sessions(scene_factory):
    stale = members_read(scene_factory, -37, 10, age_min=6)
    assert stale.omitted["breadth.members_net_day"] == ("no S&P 500 members' net volume ($VOLSPD) known within 5 minutes of now: "
                                                        "the market-context job stopped or has not saved it")
    thin = members_read(scene_factory, -37, 10, prior_members=(-50, 50, 0, 20))
    assert thin.omitted["breadth.members_net_day"] == ("needs 5 prior sessions with S&P 500 members' net volume ($VOLSPD) at this "
                                                       "minute, have 4")


def test_a_net_volume_known_after_the_read_does_not_count(scene_factory):
    later = {"$VOLSPD": [(NOW + timedelta(seconds=50), 900e6)]}
    assert "members' net volume is -37M" in sentence(members_read(scene_factory, -37, 10, today_extra=later), "breadth.members_net_day")


# ---- breadth.at_extremes

def climb_dip_climb(peak_at: int = 89, new_peak_at: int = 170, n: int = 182, low: bool = False) -> list[float]:
    """SPX closes from 09:30: a climb to 7730 at bar ``peak_at``, a dip, and a new high of 7740 at bar
    ``new_peak_at``, then easing; mirrored below 7700 with ``low``."""
    out = []
    for i in range(n):
        if i <= peak_at:
            c = 7700.0 + 30.0 * i / peak_at
        elif i < new_peak_at - 10:
            c = 7715.0
        elif i <= new_peak_at:
            c = 7715.0 + 25.0 * (i - (new_peak_at - 10)) / 10
        else:
            c = 7736.0
        out.append(7700.0 - (c - 7700.0) if low else c)
    return out


def per_minute(values: list[float], day_start: datetime | None = None) -> list[tuple[datetime, float]]:
    return list(zip(minute_ends(day_start or at(9, 30), len(values)), values))


def extremes_read(scene_factory, net_m, small_caps, closes=None, anchored=True, extra_known: dict | None = None):
    """``net_m(i)`` and ``small_caps(i)`` give $VOLD (in millions) and IWM at the end of bar i."""
    closes = closes or climb_dip_climb()
    known = {"$VOLD": per_minute([net_m(i) * 1e6 for i in range(182)]), "IWM": per_minute([small_caps(i) for i in range(182)])}
    for s, pts in (extra_known or {}).items():
        known[s] = sorted(known.get(s, []) + pts)
    return read_bars(scene_factory, closes, known, anchored)


def read_bars(scene_factory, closes, known, anchored=True):
    scene = scene_factory(NOW, bars_from_closes(closes), rows_before=[ANCHOR_ROW] if anchored else None, market=MarketContext(known))
    return build_breadth_labels(scene)


def fading_net(i: int) -> float:
    return 540.0 if i <= 89 else 310.0


def lagging_small_caps(i: int) -> float:
    return 250.0 if i == 89 else 249.2 if i >= 150 else 249.5


def test_a_new_high_neither_breadth_nor_small_caps_confirm(scene_factory):
    assert sentence(extremes_read(scene_factory, fading_net, lagging_small_caps), "breadth.at_extremes") == (
        "when SPX made its new session high at 12:21, NYSE net volume was +310M, below the +540M where it stood at the previous "
        "high at 11:00, short of that level, and small caps (IWM) were 0.33 sigma below their own session high, past the 0.15 "
        "sigma confirm rule")


def test_a_new_low_both_confirm(scene_factory):
    ls = extremes_read(scene_factory, lambda i: -200.0 if i <= 89 else -450.0, lambda i: 249.0 if i == 170 else 250.0,
                       closes=climb_dip_climb(low=True))
    assert sentence(ls, "breadth.at_extremes") == (
        "when SPX made its new session low at 12:21, NYSE net volume was -450M, below the -200M where it stood at the previous "
        "low at 11:00, reaching that level, and small caps (IWM) were at their own session low, within the 0.15 sigma confirm rule")


def test_the_confirm_rule_and_a_level_net_volume_hold_their_edges(scene_factory):
    on_rule = 250.0 * (1 - 0.15 * 75.0 / 7740.5)
    ls = extremes_read(scene_factory, lambda i: 540.0, lambda i: 250.0 if i == 89 else on_rule if i >= 150 else 249.5)
    assert sentence(ls, "breadth.at_extremes").endswith(
        "NYSE net volume was +540M, level with the +540M where it stood at the previous high at 11:00, reaching that level, "
        "and small caps (IWM) were 0.15 sigma below their own session high, within the 0.15 sigma confirm rule")


def test_the_extremes_are_measured_on_an_estimated_ruler_and_say_so(scene_factory):
    assert sentence(extremes_read(scene_factory, fading_net, lagging_small_caps, anchored=False), "breadth.at_extremes").endswith(
        "past the 0.15 sigma confirm rule; ruler estimated")


def test_the_extremes_omitted_without_an_earlier_extreme_or_a_series(scene_factory):
    straight_up = [7700.0 + 0.2 * i for i in range(182)]
    early = read_bars(scene_factory, [7700.0 + i for i in range(20)] + [7710.0] * 162, {"$VOLD": per_minute([1e6] * 182)})
    assert early.omitted["breadth.at_extremes"] == ("the session high was made within 30 minutes of the open: no earlier high to "
                                                    "hold it against")
    no_iwm = read_bars(scene_factory, straight_up, {"$VOLD": per_minute([1e6] * 182)})
    assert no_iwm.omitted["breadth.at_extremes"] == "no small-cap (IWM) price known within 5 minutes of the SPX high at 12:32"
    no_net = read_bars(scene_factory, straight_up, {"$VOLD": per_minute([1e6] * 60), "IWM": per_minute([250.0] * 182)})
    assert no_net.omitted["breadth.at_extremes"] == ("no NYSE net volume ($VOLD) known within 5 minutes of both SPX highs, "
                                                     "12:02 and 12:32")


def test_nothing_after_the_extreme_or_the_read_counts(scene_factory):
    higher_small_caps_later = {"IWM": [(at(12, 25), 251.0)]}             # after SPX's 12:21 high, before the read
    unfinished_spx_bar = climb_dip_climb() + [7760.0]                    # the 12:32 bar finishes after the read
    ls = extremes_read(scene_factory, fading_net, lagging_small_caps, closes=unfinished_spx_bar, extra_known=higher_small_caps_later)
    assert sentence(ls, "breadth.at_extremes").startswith("when SPX made its new session high at 12:21")
    assert "small caps (IWM) were 0.33 sigma below their own session high" in sentence(ls, "breadth.at_extremes")


# ---- breadth.flip_after_release and its gate

ON_DAY = events.on_day


def calendar(tmp_path, monkeypatch, rows: list[tuple[str, str, object]]):
    """Today's calendar rows as (time, kind, tier), read in place of the shipped calendar."""
    path = tmp_path / "events.json"
    path.write_text(json.dumps({"covers_through": "2026-12-31",
                                "events": [{"date": DAY, "time_et": t, "kind": k, "tier": tier} for t, k, tier in rows]}))
    events._load.cache_clear()
    monkeypatch.setattr(events, "on_day", lambda day: ON_DAY(day, path))


FOMC_AND_PRESSER = [("14:00", "FOMC", 1), ("14:30", "FOMC_PRESSER", 1)]


def around_release(scene_factory, now: datetime, blocks: list[tuple[int, float]], extra: tuple[float, float] | None = None,
                   market: bool = True):
    """10,000 shares a minute from 09:30 in blocks of (minutes, share in rising stocks)."""
    up = [10000.0 * share for n, share in blocks for _ in range(n)]
    down = [10000.0 * (1 - share) for n, share in blocks for _ in range(n)]
    if extra:
        up.append(extra[0])
        down.append(extra[1])
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    return build_breadth_labels(scene_factory(now, flat_bars(minutes), market=MarketContext(upvol(up, down)) if market else None))


def test_breadth_flipped_after_the_fed(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    ls = around_release(scene_factory, at(14, 40, ss=10), [(210, 0.5), (60, 0.63), (40, 0.38)])
    assert ls.gates["breadth_flip_after_release"] is None
    assert sentence(ls, "breadth.flip_after_release") == (
        "since the Fed's rate decision at 14:00, 40 minutes ago, 38% of NYSE volume went into rising stocks, past the 40% lean line "
        "on the sell side; in the hour before it the share was 63%, past the 60% lean line on the buy side")


def test_a_release_in_the_first_hour_is_held_against_the_minutes_from_the_open(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, [("10:00", "ISM_MANUFACTURING", "data_10am")])
    ls = around_release(scene_factory, at(10, 32, ss=10), [(30, 0.55), (32, 0.6)])
    assert sentence(ls, "breadth.flip_after_release") == (
        "since the ISM manufacturing report at 10:00, 32 minutes ago, 60% of NYSE volume went into rising stocks, inside the 40% to "
        "60% even band; in the 30 minutes before it, from the open, the share was 55%, inside the 40% to 60% even band")


def test_the_gate_sleeps_without_a_release_in_the_digest_window(scene_factory, tmp_path, monkeypatch):
    blocks = [(390, 0.5)]
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    assert around_release(scene_factory, at(16, 0), blocks).gates["breadth_flip_after_release"] is None       # 120 minutes on
    late = around_release(scene_factory, at(16, 0, ss=10), blocks)
    assert late.gates["breadth_flip_after_release"] == "no in-session release in the last 120 minutes"
    assert late.omitted["breadth.flip_after_release"] == "no in-session release in the last 120 minutes"
    calendar(tmp_path, monkeypatch, [("08:30", "CPI", "pre_open"), ("14:30", "FOMC_PRESSER", 1), ("13:25", "FED_GOVERNOR_SPEECH", "fed_speaker"),
                                     ("16:00", "QUARTER_END", 1)])
    assert around_release(scene_factory, at(15, 2, ss=10), blocks).gates["breadth_flip_after_release"] == (
        "no in-session release in the last 120 minutes")


def test_a_release_still_to_come_and_volume_after_the_read_do_not_count(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, [("14:00", "FOMC", 1), ("14:50", "FED_CHAIR_TESTIMONY", 1)])
    ls = around_release(scene_factory, at(14, 40, ss=10), [(210, 0.5), (60, 0.63), (40, 0.38)], extra=(900000.0, 0.0))
    assert sentence(ls, "breadth.flip_after_release").startswith(
        "since the Fed's rate decision at 14:00, 40 minutes ago, 38% of NYSE volume went into rising stocks")


def test_the_gate_is_decided_by_the_calendar_and_the_label_needs_the_volume(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    no_context = around_release(scene_factory, at(14, 40, ss=10), [], market=False)
    assert no_context.gates["breadth_flip_after_release"] is None
    assert no_context.omitted["breadth.flip_after_release"] == "no market-context snapshot today"
    stopped = around_release(scene_factory, at(14, 40, ss=10), [(260, 0.5)])
    assert stopped.omitted["breadth.flip_after_release"] == ("no NYSE up and down volume known within 5 minutes of 14:00 and of now: "
                                                             "the market-context job stopped or has not saved them")


# ---- the opening lane: breadth.open_net_volume, breadth.opening_tick, breadth.tick_extreme_5m and its gate

OPENING = at(9, 45)                     # a read on the bar clock, stamped at the 09:44 bar's close


def tick_bar(start: datetime, high: float, low: float, close: float) -> tuple[datetime, dict]:
    return start + timedelta(minutes=1), {"ts": start.isoformat(), "open": close, "high": high, "low": low, "close": close, "volume": 0}


def prior_tick_sessions(n: int = 10, minutes: int = 60) -> dict[str, MarketContext]:
    """``n`` prior sessions whose $TICK bar at each minute from 09:30 reaches 400, 450, ... 850 (and the
    mirror below zero), so the top 5% band at every minute is 827.5 and the bottom one -827.5."""
    out = {}
    for k, d in enumerate(PRIOR_DAYS[:n]):
        reach = 400.0 + 50.0 * k
        bars = [tick_bar(at(9, 30, day=d) + timedelta(minutes=i), reach, -reach, 0.0) for i in range(minutes)]
        out[d] = MarketContext({"$TICK": [(t, b["close"]) for t, b in bars]}, {"$TICK": bars})
    return out


def opening_tick_read(scene_factory, today: list[tuple[float, float, float]], now: datetime = OPENING, closes=None,
                      prior: dict | None = None, anchored: bool = True):
    """Today's $TICK bars from 09:30 as (high, low, close); SPX flat unless ``closes`` are given."""
    bars = [tick_bar(at(9, 30) + timedelta(minutes=i), h, lo, c) for i, (h, lo, c) in enumerate(today)]
    market = MarketContext({"$TICK": [(t, b["close"]) for t, b in bars]}, {"$TICK": bars})
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    scene = scene_factory(now, bars_from_closes(closes or [7700.0] * minutes), rows_before=[ANCHOR_ROW] if anchored else None,
                          market=market)
    return build_breadth_labels(replace(scene, prior_markets=prior_tick_sessions() if prior is None else prior))


QUIET_TICK = (300.0, -300.0, 180.0)
BUY_BURST = (900.0, -300.0, 180.0)


def test_a_one_sided_buying_open(scene_factory):
    ls = opening_tick_read(scene_factory, [BUY_BURST] * 4 + [QUIET_TICK] * 11)
    assert sentence(ls, "breadth.opening_tick") == (
        "since the open NYSE TICK averaged +180, past the 100 lean line on the buy side; its 1-minute highs reached the top 5% band "
        "for these minutes 4 times, at or past the 3-burst cluster count; its 1-minute lows never reached the bottom 5% band")


def test_the_opening_tick_lines_hold_their_edges(scene_factory):
    on_lines = opening_tick_read(scene_factory, [(827.5, -300.0, 100.0)] * 3 + [(827.4, -827.5, 100.0)] * 2 + [(300.0, -300.0, 100.0)] * 10)
    assert sentence(on_lines, "breadth.opening_tick") == (
        "since the open NYSE TICK averaged +100, within the 100 lean line; its 1-minute highs reached the top 5% band for these "
        "minutes 3 times, at or past the 3-burst cluster count; its 1-minute lows reached the bottom 5% band for these minutes "
        "twice, short of the 3-burst cluster count")
    selling = opening_tick_read(scene_factory, [(300.0, -300.0, -101.0)] * 15)
    assert sentence(selling, "breadth.opening_tick").startswith("since the open NYSE TICK averaged -101, past the 100 lean line on the sell side;")


def test_the_opening_tick_needs_a_live_feed_and_the_prior_sessions(scene_factory):
    stopped = opening_tick_read(scene_factory, [QUIET_TICK] * 9)                 # the newest bar finished at 09:39
    assert stopped.omitted["breadth.opening_tick"] == "no NYSE TICK bar in the last 5 minutes: the market-context job stopped or has not saved it"
    thin = opening_tick_read(scene_factory, [QUIET_TICK] * 15, prior=dict(list(prior_tick_sessions().items())[:4]))
    assert thin.omitted["breadth.opening_tick"] == "needs 5 prior sessions of NYSE TICK bars at each minute since the open"


def test_a_tick_bar_that_finishes_after_the_read_is_not_a_burst(scene_factory):
    ls = opening_tick_read(scene_factory, [QUIET_TICK] * 15 + [(2000.0, -2000.0, 900.0)])
    assert "highs never reached the top 5% band" in sentence(ls, "breadth.opening_tick")
    assert ls.gates["tick_extreme_follow"] == "no NYSE TICK burst in the last 5 minutes"


SPX_UP_3 = [7700.0] * 10 + [7703.0] * 5               # +3.00 points from 09:40 to 09:45: 0.04 sigma


def test_a_buying_burst_that_spx_followed(scene_factory):
    ls = opening_tick_read(scene_factory, [QUIET_TICK] * 12 + [BUY_BURST] + [QUIET_TICK] * 2, closes=SPX_UP_3)
    assert ls.gates["tick_extreme_follow"] is None
    assert sentence(ls, "breadth.tick_extreme_5m") == (
        "in the last 5 minutes NYSE TICK's 1-minute high reached the top 5% band for its minute (a buying burst) and its low did not "
        "reach the bottom band; SPX rose 0.04 sigma over the same 5 minutes, past the 0.03 sigma follow line")


def test_the_follow_line_and_each_kind_of_burst(scene_factory):
    stalled = opening_tick_read(scene_factory, [QUIET_TICK] * 12 + [BUY_BURST] + [QUIET_TICK] * 2, closes=[7700.0] * 10 + [7702.25] * 5)
    assert sentence(stalled, "breadth.tick_extreme_5m").endswith("SPX rose 0.03 sigma over the same 5 minutes, short of the 0.03 sigma follow line")
    sell = opening_tick_read(scene_factory, [QUIET_TICK] * 11 + [(300.0, -900.0, -400.0)] + [QUIET_TICK] * 3, closes=[7700.0] * 10 + [7696.0] * 5)
    assert sentence(sell, "breadth.tick_extreme_5m") == (
        "in the last 5 minutes NYSE TICK's 1-minute low reached the bottom 5% band for its minute (a selling burst) and its high did "
        "not reach the top band; SPX fell 0.05 sigma over the same 5 minutes, past the 0.03 sigma follow line")
    both = opening_tick_read(scene_factory, [QUIET_TICK] * 10 + [(900.0, -900.0, 0.0)] + [QUIET_TICK] * 4)
    assert sentence(both, "breadth.tick_extreme_5m") == (
        "in the last 5 minutes NYSE TICK's 1-minute high reached the top 5% band for its minute and its low reached the bottom 5% "
        "band (bursts both ways); SPX did not move over the same 5 minutes, short of the 0.03 sigma follow line")
    assert both.gates["tick_extreme_follow"] is None


def test_a_move_against_the_burst_is_short_of_the_follow_line(scene_factory):
    buy_into_fall = opening_tick_read(scene_factory, [QUIET_TICK] * 12 + [BUY_BURST] + [QUIET_TICK] * 2, closes=[7700.0] * 10 + [7696.0] * 5)
    assert sentence(buy_into_fall, "breadth.tick_extreme_5m").endswith(
        "(a buying burst) and its low did not reach the bottom band; SPX fell 0.05 sigma over the same 5 minutes, against the burst, "
        "short of the 0.03 sigma follow line")
    sell_into_rise = opening_tick_read(scene_factory, [QUIET_TICK] * 11 + [(300.0, -900.0, -400.0)] + [QUIET_TICK] * 3,
                                       closes=[7700.0] * 10 + [7704.0] * 5)
    assert sentence(sell_into_rise, "breadth.tick_extreme_5m").endswith(
        "(a selling burst) and its high did not reach the top band; SPX rose 0.05 sigma over the same 5 minutes, against the burst, "
        "short of the 0.03 sigma follow line")
    both_ways_fall = opening_tick_read(scene_factory, [QUIET_TICK] * 10 + [(900.0, -900.0, 0.0)] + [QUIET_TICK] * 4,
                                       closes=[7700.0] * 10 + [7696.0] * 5)
    assert sentence(both_ways_fall, "breadth.tick_extreme_5m").endswith(
        "(bursts both ways); SPX fell 0.05 sigma over the same 5 minutes, past the 0.03 sigma follow line")


def test_without_a_burst_the_gate_sleeps_and_the_label_says_so(scene_factory):
    earlier_burst = opening_tick_read(scene_factory, [BUY_BURST] * 10 + [QUIET_TICK] * 5)       # 09:39 is outside the five minutes
    assert earlier_burst.gates["tick_extreme_follow"] == "no NYSE TICK burst in the last 5 minutes"
    assert sentence(earlier_burst, "breadth.tick_extreme_5m").startswith(
        "in the last 5 minutes NYSE TICK's 1-minute highs and lows stayed inside the top and bottom 5% bands for their minutes (no burst);")


def test_the_burst_label_omitted_and_its_gate_asleep_without_what_it_needs(scene_factory):
    stopped = opening_tick_read(scene_factory, [BUY_BURST] * 9)
    reason = "no NYSE TICK bar in the last 5 minutes: the market-context job stopped or has not saved it"
    assert stopped.omitted["breadth.tick_extreme_5m"] == reason and stopped.gates["tick_extreme_follow"] == reason
    thin = opening_tick_read(scene_factory, [BUY_BURST] * 15, prior={})
    reason = "needs 5 prior sessions of NYSE TICK bars at each of the last 5 minutes"
    assert thin.omitted["breadth.tick_extreme_5m"] == reason and thin.gates["tick_extreme_follow"] == reason
    estimated = opening_tick_read(scene_factory, [BUY_BURST] * 15, closes=SPX_UP_3, anchored=False)
    assert sentence(estimated, "breadth.tick_extreme_5m").endswith("past the 0.03 sigma follow line; ruler estimated")
    no_context = build_breadth_labels(scene_factory(OPENING, flat_bars(15)))
    assert no_context.gates["tick_extreme_follow"] == "no market-context snapshot today"


def open_volume_read(scene_factory, nyse_m: float, members_m: float, add: tuple[float, float] | None = (100.0, 510.0),
                     prior_add=(100.0, 200.0, 300.0, 450.0, 500.0), extra: dict | None = None):
    """Today's totals a minute before the 09:45 read and $ADD 10 and 1 minutes before it; the prior sessions'
    totals at 09:44 spread 40M ($VOLD) and 25M ($VOLSPD), and their $ADD 10-minute changes ``prior_add`` (None: no $ADD)."""
    known = {"$VOLD": [(at(9, 44), nyse_m * 1e6)], "$VOLSPD": [(at(9, 44), members_m * 1e6)]}
    if add:
        known["$ADD"] = [(at(9, 35), add[0]), (at(9, 44), add[1])]
    for s, pts in (extra or {}).items():
        known[s] = known[s] + pts
    prior = {}
    for d, n, m, a in zip(PRIOR_DAYS, (-40, -40, 40, 40, 0), (-25, -25, 25, 25, 0), prior_add):
        prior[d] = MarketContext({"$VOLD": readings(at(9, 44), [n * 1e6], day=d), "$VOLSPD": readings(at(9, 44), [m * 1e6], day=d),
                                  **({"$ADD": readings(at(9, 44), [0.0] * 9 + [a], day=d)} if a is not None else {})})
    scene = scene_factory(OPENING, flat_bars(15), market=MarketContext(known))
    return build_breadth_labels(replace(scene, prior_markets=prior))


def test_net_volume_since_the_open_both_buying(scene_factory):
    assert sentence(open_volume_read(scene_factory, 48, 21), "breadth.open_net_volume") == (
        "since 09:30 NYSE net volume is +48M, 1.20 times the usual swing for 09:45 on the buy side, past the 0.75 lean line; "
        "S&P 500 members' net volume is +21M, 0.84 times the usual swing for 09:45 on the buy side, past the 0.75 lean line; "
        "advancers minus decliners rose 410 in the last 10 minutes, higher than 3 of the last 5 sessions at this minute")


def test_the_lean_line_holds_its_edge_and_the_thrust_clause_needs_its_readings(scene_factory):
    on_line = sentence(open_volume_read(scene_factory, -48, 18.75, add=None), "breadth.open_net_volume")
    assert on_line == ("since 09:30 NYSE net volume is -48M, 1.20 times the usual swing for 09:45 on the sell side, past the 0.75 lean "
                       "line; S&P 500 members' net volume is +19M, 0.75 times the usual swing for 09:45 on the buy side, within the "
                       "0.75 lean line")
    unranked = sentence(open_volume_read(scene_factory, 48, 21, prior_add=(100.0, 200.0, 300.0, 450.0, None)), "breadth.open_net_volume")
    assert unranked.endswith("; advancers minus decliners rose 410 in the last 10 minutes")


def test_net_volume_since_the_open_omitted_without_its_series_and_never_ahead_of_the_read(scene_factory):
    ls = open_volume_read(scene_factory, 48, 21, extra={"$VOLD": [(at(9, 46), 900e6)]})
    assert "NYSE net volume is +48M" in sentence(ls, "breadth.open_net_volume")
    scene = scene_factory(OPENING, flat_bars(15), market=MarketContext({"$VOLSPD": [(at(9, 44), 21e6)]}))
    assert build_breadth_labels(scene).omitted["breadth.open_net_volume"] == (
        "no NYSE net volume ($VOLD) known within 5 minutes of now: the market-context job stopped or has not saved it")
