"""The breadth family (labels/breadth.py): each label's sentence at its verdicts and their boundaries, its
omissions, and that a value known after the read never counts.

The market context is built as the context job saves it (market_context.py): $TICK and $TRIN a reading a
minute, $UVOL and $DVOL (thousands of shares) and $VOLD and $VOLSPD (shares) running totals since 09:30,
each value known once its minute has finished."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from conftest import at, bars_from_closes, flat_bars, make_row
from spx_jev.labels.breadth import build_breadth_labels
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
        "while SPX is 0.46 sigma above its settled open, past the 0.1 sigma open-day line; NYSE net volume is +10M, 0.10 times "
        "the usual swing for 12:32 on the buy side; S&P 500 net volume runs 0.84 swings weaker than NYSE's, past the 0.5 split line")
    assert sentence(members_read(scene_factory, 45, -20), "breadth.members_net_day").endswith(
        "S&P 500 net volume runs 1.10 swings stronger than NYSE's, past the 0.5 split line")


def test_the_split_line_and_the_open_day_line_hold_their_edges(scene_factory):
    on_split = sentence(members_read(scene_factory, 30, 10), "breadth.members_net_day")
    assert on_split.endswith("S&P 500 net volume is matched with NYSE's, 0.50 swings apart, within the 0.5 split line")
    near_open = sentence(members_read(scene_factory, 30, 10, closes=[7700.0] * 5 + [7707.5] * 177), "breadth.members_net_day")
    assert "above zero while SPX is +0.10 sigma from its settled open, within the 0.1 sigma open-day line;" in near_open
    at_zero = sentence(members_read(scene_factory, 0.4, 10), "breadth.members_net_day")
    assert at_zero.startswith("since the open S&P 500 members' net volume is +0M, 0.01 times the usual swing for 12:32 on the buy side, at zero")


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
