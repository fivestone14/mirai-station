"""The breadth family (labels/breadth.py): each label's sentence at its verdicts and their boundaries, its
omissions, and that a value known after the read never counts.

The market context is built as the context job saves it (market_context.py): $TICK and $TRIN a reading a
minute; $UVOL and $DVOL (thousands of shares) the day's volume in the stocks up and down on the day, $VOLD
(shares) their difference and $VOLSPD (shares) the same in S&P 500 members, all from 0 at 09:30; each value
known once its minute has finished. Every verdict is a rank against the same measure at the same
minute on the last 20 prior sessions (needing 10), so each fixture carries 20 prior sessions whose measure
steps evenly, and a rank's third turns between 6 and 7 sessions beaten (bottom to middle) and between 13 and 14
(middle to top)."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta

from conftest import DAY, at, bars_from_closes, flat_bars, make_row
from spx_jev import events
from spx_jev.labels.breadth import build_breadth_labels
from spx_jev.labels.price import build_price_labels
from spx_jev.labels.ranks import move_rank
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.state_builder import MarketContext

PRIOR_DAYS = [(date(2026, 9, 17) - timedelta(days=k)).isoformat() for k in range(20)]
SIGMA = 75.0


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


def upvol(up: list[float], down: list[float], day: str | None = None) -> dict[str, list[tuple[datetime, float]]]:
    """Up and down volume from what each minute added, no stock turning, and NYSE net volume, their difference in shares."""
    return {"$UVOL": running_totals(up, day), "$DVOL": running_totals(down, day),
            "$VOLD": running_totals([(u - d) * 1000 for u, d in zip(up, down)], day)}


def even_prior_upvol(shares: list[float]) -> dict[str, dict]:
    """Each prior session trading 10,000 shares a minute all day, share ``s`` of it in rising stocks."""
    return {d: upvol([10000.0 * s] * 390, [10000.0 * (1 - s)] * 390, day=d) for d, s in zip(PRIOR_DAYS, shares)}


# The prior sessions' shares of volume in rising stocks step from 40.5% to 59.5%, clear of any share a test prints.
USUAL_SHARES = [0.405 + 0.01 * k for k in range(20)]


def prior_spx(moves: list[float], from_bar: int) -> dict[str, list[dict]]:
    """Each prior session's SPX bars: flat at 7700 until bar ``from_bar``, then ``m`` points away all day."""
    return {d: bars_from_closes([7700.0] * from_bar + [7700.0 + m] * (390 - from_bar), day=d) for d, m in zip(PRIOR_DAYS, moves)}


def with_prior(scene, markets: dict[str, dict | MarketContext], spx: dict[str, list[dict]] | None = None):
    """``scene`` with the prior sessions' market context and, for a rank of SPX's own move, their bars and a
    trusted morning ruler each."""
    scene = replace(scene, prior_markets={d: m if isinstance(m, MarketContext) else MarketContext(m) for d, m in markets.items()})
    if spx is not None:
        scene = replace(scene, prior_bars=spx, prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in spx})
    return scene


def readings(end: datetime, values: list[float], day: str | None = None) -> list[tuple[datetime, float]]:
    """One reading a minute, the last known at ``end``."""
    n = len(values)
    if day is not None:
        end = datetime.combine(datetime.fromisoformat(day).date(), end.timetz())
    return [(end - timedelta(minutes=n - 1 - i), v) for i, v in enumerate(values)]


def read(scene_factory, now: datetime, known: dict, prior_known: dict[str, dict] | None = None):
    scene = scene_factory(now, flat_bars(int((now - at(9, 30)).total_seconds() // 60)), market=MarketContext(known))
    return build_breadth_labels(with_prior(scene, prior_known or {}))


def sentence(ls, path: str) -> str:
    group, key = path.split(".", 1)
    assert path not in ls.omitted, ls.omitted.get(path)
    return ls.state[group][key]


# ---- breadth.net_volume_change_30m

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


def upvol_read(scene_factory, share: float, prior=USUAL_SHARES):
    return read(scene_factory, NOW, upvol_last_30(share), even_prior_upvol(prior))


def test_the_30_minute_up_volume_share_ranked_at_this_minute(scene_factory):
    assert sentence(upvol_read(scene_factory, 0.61), "breadth.net_volume_change_30m") == (
        "over the last 30 minutes NYSE net volume changed by +66M, higher than 20 of the last 20 sessions at this minute, "
        "top third: leaning to buying")
    assert sentence(upvol_read(scene_factory, 0.38), "breadth.net_volume_change_30m") == (
        "over the last 30 minutes NYSE net volume changed by -72M, higher than 0 of the last 20 sessions at this minute, "
        "bottom third: leaning to selling")
    assert sentence(upvol_read(scene_factory, 0.52), "breadth.net_volume_change_30m") == (
        "over the last 30 minutes NYSE net volume changed by +12M, higher than 12 of the last 20 sessions at this minute, "
        "middle third: no lean")


def test_the_up_volume_share_turns_third_at_the_rank_edges(scene_factory):
    assert sentence(upvol_read(scene_factory, 0.47), "breadth.net_volume_change_30m").endswith("higher than 7 of the last 20 sessions at this "
                                                                                        "minute, middle third: no lean")
    assert sentence(upvol_read(scene_factory, 0.46), "breadth.net_volume_change_30m").endswith("higher than 6 of the last 20 sessions at this "
                                                                                        "minute, bottom third: leaning to selling")
    assert sentence(upvol_read(scene_factory, 0.54), "breadth.net_volume_change_30m").endswith("higher than 14 of the last 20 sessions at this "
                                                                                        "minute, top third: leaning to buying")
    assert sentence(upvol_read(scene_factory, 0.53), "breadth.net_volume_change_30m").endswith("higher than 13 of the last 20 sessions at this "
                                                                                        "minute, middle third: no lean")


def test_the_30_minute_share_is_omitted_when_the_feed_stopped_or_under_ten_prior_sessions(scene_factory):
    stopped = {s: [(t, v) for t, v in pts if t <= NOW - timedelta(minutes=10)] for s, pts in upvol_last_30(0.61).items()}
    ls = read(scene_factory, NOW, stopped, even_prior_upvol(USUAL_SHARES))
    assert ls.omitted["breadth.net_volume_change_30m"] == ("no NYSE net volume ($VOLD) known both 30 minutes ago and now, within 5 minutes "
                                                     "of each: the market-context job stopped or has not saved it")
    assert upvol_read(scene_factory, 0.61, prior=USUAL_SHARES[:9]).omitted["breadth.net_volume_change_30m"] == (
        "its rank needs 10 prior sessions with NYSE net volume ($VOLD) over the 30 minutes to this minute, have 9")
    assert "higher than 10 of the last 10 sessions" in sentence(upvol_read(scene_factory, 0.61, prior=USUAL_SHARES[:10]), "breadth.net_volume_change_30m")


def test_a_series_schwab_served_empty_all_day_is_named_in_every_label_it_omits(scene_factory):
    """09-28: $UVOL and $DVOL arrived all day, $ADD, $VOLD and $VOLSPD never did. The omission names the missing series
    and does not say the job stopped, which it had not."""
    served = {s: v for s, v in upvol_last_30(0.61).items() if s != "$VOLD"}
    ls = read(scene_factory, NOW, served, even_prior_upvol(USUAL_SHARES))
    none_saved = "the market-context job has saved no {} today"
    assert ls.omitted["breadth.advance_decline"] == none_saved.format("NYSE advancers minus decliners ($ADD)")
    assert ls.omitted["breadth.members_net_day"] == none_saved.format("S&P 500 members' net volume ($VOLSPD)")
    for path in ("breadth.net_volume_change_30m", "breadth.day_upvol_share", "breadth.open_net_volume"):
        assert ls.omitted[path] == none_saved.format("NYSE net volume ($VOLD)"), path


def test_a_minute_that_finishes_after_the_read_does_not_count_toward_the_share(scene_factory):
    selling_after = upvol_last_30(0.61, extra=(0.0, 900000.0))           # its bar finishes at 12:33
    assert sentence(read(scene_factory, NOW, selling_after, even_prior_upvol(USUAL_SHARES)),
                    "breadth.net_volume_change_30m").startswith("over the last 30 minutes NYSE net volume changed by +66M")


def test_a_stock_that_turns_down_moves_its_day_from_up_to_down_volume_and_the_window_reads_net_volume(scene_factory):
    up = [5000.0] * (MINUTES_TO_NOW - 30) + [2000.0] * 30
    down = [5000.0] * (MINUTES_TO_NOW - 30) + [8000.0] * 30
    up[-20], down[-20] = up[-20] - 300000.0, down[-20] + 300000.0      # its whole day so far leaves $UVOL for $DVOL
    text = sentence(read(scene_factory, NOW, upvol(up, down), even_prior_upvol(USUAL_SHARES)), "breadth.net_volume_change_30m")
    assert text == ("over the last 30 minutes NYSE net volume changed by -780M, higher than 0 of the last 20 sessions at this minute, "
                    "bottom third: leaning to selling")


# ---- breadth.volume_vs_count_30m

# The prior sessions' mean TRIN over this half hour steps from 0.705 to 1.275.
USUAL_TRIN = [0.705 + 0.03 * k for k in range(20)]


def prior_trin(means: list[float]) -> dict[str, dict]:
    return {d: {"$TRIN": readings(at(12, 32), [m] * 30, day=d)} for d, m in zip(PRIOR_DAYS, means)}


def trin_read(scene_factory, values: list[float], prior=USUAL_TRIN, extra: list | None = None):
    return read(scene_factory, NOW, {"$TRIN": readings(at(12, 32), values) + (extra or [])}, prior_trin(prior))


def test_trin_over_30_minutes_ranked_at_this_minute(scene_factory):
    assert sentence(trin_read(scene_factory, [0.78] * 30), "breadth.volume_vs_count_30m") == (
        "over the last 30 minutes NYSE TRIN averaged 0.78, higher than 3 of the last 20 sessions at this minute, bottom third: "
        "volume ran heavier in rising stocks, against their count, than usual for this half hour")
    assert sentence(trin_read(scene_factory, [1.1, 1.3] * 15), "breadth.volume_vs_count_30m") == (
        "over the last 30 minutes NYSE TRIN averaged 1.20, higher than 17 of the last 20 sessions at this minute, top third: "
        "volume ran heavier in falling stocks, against their count, than usual for this half hour")
    assert sentence(trin_read(scene_factory, [0.99] * 30), "breadth.volume_vs_count_30m") == (
        "over the last 30 minutes NYSE TRIN averaged 0.99, higher than 10 of the last 20 sessions at this minute, middle third: "
        "volume in rising and falling stocks matched their counts as usual for this half hour")


def test_trin_turns_third_at_the_rank_edges(scene_factory):
    assert "higher than 6 of the last 20 sessions at this minute, bottom third" in sentence(trin_read(scene_factory, [0.88] * 30), "breadth.volume_vs_count_30m")
    assert "higher than 7 of the last 20 sessions at this minute, middle third" in sentence(trin_read(scene_factory, [0.90] * 30), "breadth.volume_vs_count_30m")
    assert "higher than 13 of the last 20 sessions at this minute, middle third" in sentence(trin_read(scene_factory, [1.08] * 30), "breadth.volume_vs_count_30m")
    assert "higher than 14 of the last 20 sessions at this minute, top third" in sentence(trin_read(scene_factory, [1.10] * 30), "breadth.volume_vs_count_30m")


def test_trin_needs_20_readings_ten_prior_sessions_and_never_one_after_the_read(scene_factory):
    ls = trin_read(scene_factory, [0.78] * 19)
    assert ls.omitted["breadth.volume_vs_count_30m"] == "needs 20 NYSE TRIN readings in the last 30 minutes, have 19"
    thin = trin_read(scene_factory, [0.78] * 30, prior=USUAL_TRIN[:9])
    assert thin.omitted["breadth.volume_vs_count_30m"] == "its rank needs 10 prior sessions with 20 NYSE TRIN readings in this half hour, have 9"
    assert "averaged 0.99" in sentence(trin_read(scene_factory, [0.99] * 30, extra=[(at(12, 33), 9.0)]), "breadth.volume_vs_count_30m")


# ---- breadth.tick_side_vs_usual

def ticks(above: int, n: int = 30) -> list[float]:
    """``n`` TICK closes, ``above`` of them above zero, spread through the window."""
    return [310.0 if i < above else -240.0 for i in range(n)]


def prior_ticks(shares_above: list[int]) -> dict[str, dict]:
    """Each prior session's last 30 minutes of TICK closes at this clock, ``k`` of 30 above zero."""
    return {d: {"$TICK": readings(at(12, 32), ticks(k), day=d)} for d, k in zip(PRIOR_DAYS, shares_above)}


USUAL_5_TO_24_OF_30 = list(range(5, 25))     # the prior sessions sat above zero 5 to 24 of 30 minutes, median 14.5


def tick_side(scene_factory, above: int, prior=USUAL_5_TO_24_OF_30, extra: list | None = None):
    today = readings(at(12, 32), ticks(above)) + (extra or [])
    return read(scene_factory, NOW, {"$TICK": today}, prior_ticks(prior))


def test_the_tick_side_ranked_on_five_levels_for_this_half_hour(scene_factory):
    assert sentence(tick_side(scene_factory, 21), "breadth.tick_side_vs_usual") == (
        "over the last 30 minutes NYSE TICK sat above zero 21 of 30 minutes, 0.22 more than the usual share for this half hour; "
        "higher than 16 of the last 20 sessions at this minute, in the top fifth")
    assert sentence(tick_side(scene_factory, 19), "breadth.tick_side_vs_usual").endswith(
        "higher than 14 of the last 20 sessions at this minute, in the top third, short of the top fifth")
    assert sentence(tick_side(scene_factory, 14), "breadth.tick_side_vs_usual") == (
        "over the last 30 minutes NYSE TICK sat above zero 14 of 30 minutes, 0.02 less than the usual share for this half hour; "
        "higher than 9 of the last 20 sessions at this minute, in the middle third")
    assert sentence(tick_side(scene_factory, 10), "breadth.tick_side_vs_usual").endswith(
        "higher than 5 of the last 20 sessions at this minute, in the bottom third, short of the bottom fifth")
    assert sentence(tick_side(scene_factory, 5), "breadth.tick_side_vs_usual").endswith(
        "higher than 0 of the last 20 sessions at this minute, in the bottom fifth")


def test_the_five_levels_turn_at_their_rank_edges_and_a_tie_is_not_beaten(scene_factory):
    assert sentence(tick_side(scene_factory, 20), "breadth.tick_side_vs_usual").endswith("higher than 15 of the last 20 sessions at this "
                                                                                        "minute, in the top third, short of the top fifth")
    assert sentence(tick_side(scene_factory, 18), "breadth.tick_side_vs_usual").endswith("higher than 13 of the last 20 sessions at this "
                                                                                        "minute, in the middle third")
    assert sentence(tick_side(scene_factory, 12), "breadth.tick_side_vs_usual").endswith("higher than 7 of the last 20 sessions at this "
                                                                                        "minute, in the middle third")
    assert sentence(tick_side(scene_factory, 11), "breadth.tick_side_vs_usual").endswith("higher than 6 of the last 20 sessions at this "
                                                                                        "minute, in the bottom third, short of the bottom fifth")
    assert sentence(tick_side(scene_factory, 9), "breadth.tick_side_vs_usual").endswith("higher than 4 of the last 20 sessions at this "
                                                                                       "minute, in the bottom fifth")


def test_the_tick_side_needs_its_readings_and_ten_prior_sessions(scene_factory):
    few = read(scene_factory, NOW, {"$TICK": readings(at(12, 32), ticks(10, 19))}, prior_ticks(USUAL_5_TO_24_OF_30))
    assert few.omitted["breadth.tick_side_vs_usual"] == "needs 20 NYSE TICK readings in the last 30 minutes, have 19"
    thin = tick_side(scene_factory, 21, prior=[14] * 9)
    assert thin.omitted["breadth.tick_side_vs_usual"] == "its rank needs 10 prior sessions with NYSE TICK readings in this half hour, have 9"


def test_a_tick_reading_after_the_read_does_not_count(scene_factory):
    after = [(at(12, 33), 500.0), (at(12, 34), 500.0)]
    assert "above zero 21 of 30 minutes" in sentence(tick_side(scene_factory, 21, extra=after), "breadth.tick_side_vs_usual")


def test_no_market_context_omits_every_breadth_label_but_the_dark_one(scene_factory):
    ls = build_breadth_labels(scene_factory(NOW, flat_bars(MINUTES_TO_NOW)))
    assert set(ls.omitted.values()) == {"no market-context snapshot today"}
    assert "breadth.nasdaq_net_volume" not in ls.omitted and "breadth.net_volume_change_30m" in ls.omitted


# ---- breadth.day_upvol_share

def block_upvol(blocks: list[tuple[int, float, float]], day: str | None = None, extra: tuple[float, float] | None = None) -> dict:
    """Up and down volume from 09:30 in blocks of (minutes, volume a minute, share of it in rising stocks)."""
    up = [per * share for n, per, share in blocks for _ in range(n)]
    down = [per * (1 - share) for n, per, share in blocks for _ in range(n)]
    if extra:
        up.append(extra[0])
        down.append(extra[1])
    return upvol(up, down, day)


def rotating_prior(share: float, crossings: int) -> list[tuple[int, float, float]]:
    """A prior session whose half-hour shares cross even ``crossings`` times from the open, 12 points either side
    of it and ending on ``share``'s side, then hold ``share`` all day."""
    side = 1 if share > 0.5 else -1
    swings = [(30, 10000.0, 0.5 + 0.12 * side * (-1) ** (crossings - k)) for k in range(crossings + 1)] if crossings else []
    return swings + [(390 - 30 * len(swings), 10000.0, share)]


# The prior sessions' day shares step from 40.5% up by a point (the five that crossed twice sit near two points
# further from even, from their extra half hour on their own side), and they crossed even 0, 1, 2 and 3 times by
# 12:32, five sessions each.
USUAL_DAYS = {d: block_upvol(rotating_prior(0.405 + 0.01 * k, k % 4), day=d) for k, d in enumerate(PRIOR_DAYS)}


def day_share(scene_factory, blocks: list[tuple[int, float, float]], now: datetime = NOW, extra: tuple[float, float] | None = None,
              prior: dict = USUAL_DAYS):
    return read(scene_factory, now, block_upvol(blocks, extra=extra), prior)


def test_a_one_sided_day(scene_factory):
    assert sentence(day_share(scene_factory, [(182, 10000.0, 0.85)]), "breadth.day_upvol_share") == (
        "since the open 85% of NYSE volume went into rising stocks, higher than 20 of the last 20 sessions at this minute, in the top "
        "fifth: one-sided on the buy side; the half hours' NYSE net volume changes have not switched sign today, more often than 0 of "
        "the last 20 sessions by this minute, bottom third")
    assert sentence(day_share(scene_factory, [(182, 10000.0, 0.18)]), "breadth.day_upvol_share").startswith(
        "since the open 18% of NYSE volume went into rising stocks, higher than 0 of the last 20 sessions at this minute, in the "
        "bottom fifth: one-sided on the sell side; ")


def test_one_sided_is_the_top_or_bottom_fifth_of_the_day_share_at_this_minute(scene_factory):
    top_fifth = sentence(day_share(scene_factory, [(182, 10000.0, 0.54)]), "breadth.day_upvol_share")
    assert "54% of NYSE volume went into rising stocks, higher than 16 of the last 20 sessions at this minute, in the top fifth: " \
           "one-sided on the buy side;" in top_fifth
    top_third = sentence(day_share(scene_factory, [(182, 10000.0, 0.53)]), "breadth.day_upvol_share")
    assert "53% of NYSE volume went into rising stocks, higher than 15 of the last 20 sessions at this minute, top third;" in top_third


def test_a_day_that_faded_from_one_side_says_where_it_stood(scene_factory):
    faded = day_share(scene_factory, [(60, 10000.0, 0.9), (122, 20000.0, 0.4)])
    assert sentence(faded, "breadth.day_upvol_share") == (
        "since the open 50% of NYSE volume went into rising stocks, higher than 10 of the last 20 sessions at this minute, middle third; "
        "earlier today it stood one-sided, at 90% at 10:00, in the top fifth for that minute; the half hours' NYSE net volume changes "
        "switched sign once today, more often than 5 of the last 20 sessions by this minute, bottom third")
    leaning = day_share(scene_factory, [(182, 10000.0, 0.51)], prior=even_prior_upvol(USUAL_SHARES))
    assert sentence(leaning, "breadth.day_upvol_share") == (
        "since the open 51% of NYSE volume went into rising stocks, higher than 11 of the last 20 sessions at this minute, middle third; "
        "it has not stood one-sided at a half hour's mark since 10:00; the half hours' NYSE net volume changes have not switched sign "
        "today, more often than 0 of the last 20 sessions by this minute, bottom third")


def test_a_rotating_day_is_the_top_third_of_the_crossings_by_this_minute(scene_factory):
    rotating = [(30, 10000.0, 0.7 if k % 2 == 0 else 0.3) for k in range(7)]
    assert sentence(day_share(scene_factory, rotating), "breadth.day_upvol_share").endswith(
        "the half hours' NYSE net volume changes switched sign 5 times today, more often than 20 of the last 20 sessions by this minute, top third: rotating")
    three = [(30, 10000.0, 0.7 if k % 2 == 0 else 0.3) for k in range(4)] + [(62, 10000.0, 0.3)]
    assert sentence(day_share(scene_factory, three), "breadth.day_upvol_share").endswith(
        "the half hours' NYSE net volume changes switched sign 3 times today, more often than 15 of the last 20 sessions by this minute, top third: rotating")
    twice = [(30, 10000.0, 0.7 if k % 2 == 0 else 0.3) for k in range(3)] + [(92, 10000.0, 0.7)]
    assert sentence(day_share(scene_factory, twice), "breadth.day_upvol_share").endswith(
        "the half hours' NYSE net volume changes switched sign twice today, more often than 10 of the last 20 sessions by this minute, middle third")


def test_rotation_is_counted_between_whole_half_hours_not_minute_by_minute(scene_factory):
    hovering = [(1, 10000.0, 0.53 if i % 2 == 0 else 0.47) for i in range(182)]
    assert sentence(day_share(scene_factory, hovering), "breadth.day_upvol_share").endswith(
        "the half hours' NYSE net volume changes have not switched sign today, more often than 0 of the last 20 sessions by this "
        "minute, bottom third")


def test_the_day_share_waits_half_an_hour_a_live_feed_and_ten_prior_sessions(scene_factory):
    early = day_share(scene_factory, [(29, 10000.0, 0.85)], now=at(9, 59, ss=10))
    assert early.omitted["breadth.day_upvol_share"] == "needs 30 minutes of session"
    stopped = read(scene_factory, NOW, {s: [(t, v) for t, v in pts if t <= at(12, 20)]
                                        for s, pts in upvol([8500.0] * 182, [1500.0] * 182).items()}, USUAL_DAYS)
    assert stopped.omitted["breadth.day_upvol_share"] == ("no NYSE up and down volume known within 5 minutes of now: the market-context "
                                                         "job stopped or has not saved them")
    thin = day_share(scene_factory, [(182, 10000.0, 0.85)], prior=dict(list(USUAL_DAYS.items())[:9]))
    assert thin.omitted["breadth.day_upvol_share"] == "its rank needs 10 prior sessions with NYSE up and down volume since the open at this minute, have 9"


def test_a_minute_after_the_read_does_not_count_toward_the_day(scene_factory):
    late_selling = day_share(scene_factory, [(182, 10000.0, 0.85)], extra=(0.0, 5_000_000.0))
    assert sentence(late_selling, "breadth.day_upvol_share").startswith("since the open 85% of NYSE volume")


# ---- breadth.members_net_day

ANCHOR_ROW = make_row(at(9, 32), 7700.0, sigma=SIGMA, sigma_anchor=SIGMA)
# The prior sessions' S&P 500 members' net volume at this minute steps from -95M to +95M by 10M, NYSE's from
# -190M to +190M by 20M, and SPX stood 0 to 19 points from its settled open.
USUAL_MEMBERS = [-95 + 10 * k for k in range(20)]
USUAL_NYSE = [-190 + 20 * k for k in range(20)]
OPEN_MOVES = prior_spx([float(k) for k in range(20)], from_bar=5)


def members_read(scene_factory, members_m: float, nyse_m: float, closes: list[float] | None = None, now: datetime = NOW,
                 prior_members=USUAL_MEMBERS, prior_nyse=USUAL_NYSE, today_extra: dict | None = None, age_min: int = 1, spx=OPEN_MOVES):
    """Today's net volume totals ``age_min`` minutes before the read; each prior session's a minute before the
    same clock."""
    known = {"$VOLSPD": [(now - timedelta(minutes=age_min), members_m * 1e6)], "$VOLD": [(now - timedelta(minutes=age_min), nyse_m * 1e6)]}
    for s, pts in (today_extra or {}).items():
        known[s] = known[s] + pts
    prior = {d: {"$VOLSPD": readings(now - timedelta(minutes=1), [m * 1e6], day=d), "$VOLD": readings(now - timedelta(minutes=1), [n * 1e6], day=d)}
             for d, m, n in zip(PRIOR_DAYS, prior_members, prior_nyse)}
    closes = closes or [7700.0] * 5 + [7734.5] * 177
    scene = scene_factory(now, bars_from_closes(closes), rows_before=[ANCHOR_ROW], market=MarketContext(known))
    return build_breadth_labels(with_prior(scene, prior, spx))


def test_members_net_volume_against_price_and_the_nyse(scene_factory):
    assert sentence(members_read(scene_factory, -37, 10), "breadth.members_net_day") == (
        "since the open S&P 500 members' net volume is -37M, higher than 6 of the last 20 sessions at this minute, bottom third: "
        "leaning to selling, below zero while SPX is 0.46 sigma above its settled open, a move larger than 20 of the last 20 sessions' "
        "moves from their settled opens at this minute, top third; NYSE net volume is +10M, higher than 10 of the last 20 sessions at "
        "this minute, middle third: no lean; S&P 500 net volume runs weaker than NYSE's, in a lower third (bottom third against "
        "middle third)")
    assert sentence(members_read(scene_factory, 45, -20), "breadth.members_net_day").endswith(
        "S&P 500 net volume runs stronger than NYSE's, in a higher third (top third against middle third)")


def test_the_members_split_and_the_move_from_the_open_turn_at_their_thirds(scene_factory):
    matched = sentence(members_read(scene_factory, 30, 10), "breadth.members_net_day")
    assert matched.endswith("S&P 500 net volume is matched with NYSE's, both in the middle third")
    stronger = sentence(members_read(scene_factory, 40, 10), "breadth.members_net_day")
    assert "members' net volume is +40M, higher than 14 of the last 20 sessions at this minute, top third: leaning to buying" in stronger
    assert stronger.endswith("runs stronger than NYSE's, in a higher third (top third against middle third)")
    near_open = sentence(members_read(scene_factory, 30, 10, closes=[7700.0] * 5 + [7705.5] * 177), "breadth.members_net_day")
    assert ("while SPX is 0.07 sigma above its settled open, a move larger than 6 of the last 20 sessions' moves from their settled opens "
            "at this minute, bottom third: near the open;") in near_open
    off_open = sentence(members_read(scene_factory, 30, 10, closes=[7700.0] * 5 + [7706.5] * 177), "breadth.members_net_day")
    assert "a move larger than 7 of the last 20 sessions' moves from their settled opens at this minute, middle third;" in off_open
    at_zero = sentence(members_read(scene_factory, 0.4, 10), "breadth.members_net_day")
    assert at_zero.startswith("since the open S&P 500 members' net volume is +0M, higher than 10 of the last 20 sessions at this minute, "
                              "middle third: no lean, at zero")


def test_the_move_from_the_open_is_ranked_from_the_settled_open_as_move_rank_ranks_it(scene_factory):
    stale_open = {d: bars_from_closes([7650.0] * 4 + [7700.0] * 386, day=d) for d in PRIOR_DAYS}     # 09:30-09:33 far off, then 7700
    members = sentence(members_read(scene_factory, 30, 10, spx=stale_open), "breadth.members_net_day")
    assert "a move larger than 20 of the last 20 sessions' moves from their settled opens at this minute, top third;" in members
    ls = members_read(scene_factory, 30, 10)
    scene = with_prior(scene_factory(NOW, bars_from_closes([7700.0] * 5 + [7734.5] * 177), rows_before=[ANCHOR_ROW]), {}, OPEN_MOVES)
    rank, _ = move_rank(scene, 34.5 / SIGMA, 177)                          # 09:35 to 12:32
    assert f"a move larger than {rank.higher_than} of the last {rank.of} sessions' moves" in sentence(ls, "breadth.members_net_day")


def test_members_net_volume_omitted_without_its_series_or_ten_prior_sessions(scene_factory):
    stale = members_read(scene_factory, -37, 10, age_min=6)
    assert stale.omitted["breadth.members_net_day"] == ("no S&P 500 members' net volume ($VOLSPD) known within 5 minutes of now: "
                                                        "the market-context job stopped or has not saved it")
    thin = members_read(scene_factory, -37, 10, prior_members=USUAL_MEMBERS[:9])
    assert thin.omitted["breadth.members_net_day"] == ("its rank needs 10 prior sessions with S&P 500 members' net volume ($VOLSPD) at "
                                                       "this minute, have 9")


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


def iwm_off(sigma_off: float, spx: float = 7700.0) -> float:
    """IWM's price when it stands ``sigma_off`` SPX sigma below its own high of 250, with SPX at ``spx``."""
    return 250.0 * (1 - sigma_off * SIGMA / spx)


# On each prior session IWM rose to 250 by 11:10 and stood 0.00 to 0.38 SPX sigma below that high from then on.
USUAL_IWM = {d: {"IWM": per_minute([250.0] * 100 + [iwm_off(0.02 * k)] * 290, at(9, 30, day=d))} for k, d in enumerate(PRIOR_DAYS)}
FLAT_SPX = prior_spx([0.0] * 20, from_bar=0)


def extremes_read(scene_factory, net_m, small_caps, closes=None, anchored=True, extra_known: dict | None = None, prior=USUAL_IWM):
    """``net_m(i)`` and ``small_caps(i)`` give $VOLD (in millions) and IWM at the end of bar i."""
    closes = closes or climb_dip_climb()
    known = {"$VOLD": per_minute([net_m(i) * 1e6 for i in range(182)]), "IWM": per_minute([small_caps(i) for i in range(182)])}
    for s, pts in (extra_known or {}).items():
        known[s] = sorted(known.get(s, []) + pts)
    return read_bars(scene_factory, closes, known, anchored, prior)


def read_bars(scene_factory, closes, known, anchored=True, prior=USUAL_IWM):
    scene = scene_factory(NOW, bars_from_closes(closes), rows_before=[ANCHOR_ROW] if anchored else None, market=MarketContext(known))
    return build_breadth_labels(with_prior(scene, prior, FLAT_SPX))


def fading_net(i: int) -> float:
    return 540.0 if i <= 89 else 310.0


def lagging_small_caps(i: int) -> float:
    return 250.0 if i == 89 else 249.2 if i >= 150 else 249.5


def test_a_new_high_neither_breadth_nor_small_caps_confirm(scene_factory):
    assert sentence(extremes_read(scene_factory, fading_net, lagging_small_caps), "breadth.at_extremes") == (
        "when SPX made its new session high at 12:20, NYSE net volume was +310M, below the +540M where it stood at the previous "
        "high at 10:59, short of that level, and small caps (IWM) were 0.33 sigma below their own session high, further from it than "
        "17 of the last 20 sessions at 12:20, top third: not confirming")


def test_a_new_low_both_confirm(scene_factory):
    ls = extremes_read(scene_factory, lambda i: -200.0 if i <= 89 else -450.0, lambda i: 249.0 if i == 170 else 250.0,
                       closes=climb_dip_climb(low=True))
    assert sentence(ls, "breadth.at_extremes") == (
        "when SPX made its new session low at 12:20, NYSE net volume was -450M, below the -200M where it stood at the previous "
        "low at 10:59, reaching that level, and small caps (IWM) were at their own session low, further from it than 0 of the last "
        "20 sessions at 12:20, bottom third: confirming")


def test_small_caps_confirm_in_the_bottom_third_and_a_level_net_volume_reaches(scene_factory):
    def lagging_by(sigma_off):
        return lambda i: 250.0 if i == 89 else iwm_off(sigma_off, 7740.0) if i >= 150 else 249.5
    ls = extremes_read(scene_factory, lambda i: 540.0, lagging_by(0.11))
    assert sentence(ls, "breadth.at_extremes").endswith(
        "NYSE net volume was +540M, level with the +540M where it stood at the previous high at 10:59, reaching that level, "
        "and small caps (IWM) were 0.11 sigma below their own session high, further from it than 6 of the last 20 sessions at 12:20, "
        "bottom third: confirming")
    assert sentence(extremes_read(scene_factory, lambda i: 540.0, lagging_by(0.13)), "breadth.at_extremes").endswith(
        "0.13 sigma below their own session high, further from it than 7 of the last 20 sessions at 12:20, middle third: not confirming")


def test_the_prior_sessions_are_measured_at_the_extremes_minute_not_the_reads(scene_factory):
    later_off = {d: {"IWM": per_minute([250.0] * 172 + [iwm_off(0.5)] * 218, at(9, 30, day=d))} for d in PRIOR_DAYS}   # after 12:22
    ls = extremes_read(scene_factory, fading_net, lagging_small_caps, prior=later_off)
    assert sentence(ls, "breadth.at_extremes").endswith("further from it than 20 of the last 20 sessions at 12:20, top third: not confirming")


def test_the_extremes_are_measured_on_an_estimated_ruler_and_say_so(scene_factory):
    assert sentence(extremes_read(scene_factory, fading_net, lagging_small_caps, anchored=False), "breadth.at_extremes").endswith(
        "top third: not confirming (ruler estimated)")


def test_the_small_caps_rank_needs_ten_prior_sessions(scene_factory):
    thin = extremes_read(scene_factory, fading_net, lagging_small_caps, prior=dict(list(USUAL_IWM.items())[:9]))
    assert thin.omitted["breadth.at_extremes"] == ("its rank needs 10 prior sessions with small caps (IWM), SPX bars and a trusted morning "
                                                   "ruler at 12:20, have 9")


def test_the_extremes_omitted_without_a_new_extreme_or_a_series(scene_factory):
    straight_up = [7700.0 + 0.2 * i for i in range(182)]
    too_early = scene_factory(at(9, 44, ss=10), bars_from_closes(straight_up[:14]), rows_before=[ANCHOR_ROW],
                              market=MarketContext({"$VOLD": per_minute([1e6] * 14)}))
    assert build_breadth_labels(too_early).omitted["breadth.at_extremes"] == "needs SPX bars from before the last 15 minutes"
    stale = read_bars(scene_factory, [7700.0 + i for i in range(20)] + [7710.0] * 162, {"$VOLD": per_minute([1e6] * 182)})
    assert stale.omitted["breadth.at_extremes"] == "no new session high or low in the last 15 minutes" and "breadth.at_extremes" in stale.ended
    no_iwm = read_bars(scene_factory, straight_up, {"$VOLD": per_minute([1e6] * 182)})
    assert no_iwm.omitted["breadth.at_extremes"] == "no small-cap (IWM) price known within 5 minutes of the SPX high at 12:31"
    no_net = read_bars(scene_factory, straight_up, {"$VOLD": per_minute([1e6] * 60), "IWM": per_minute([250.0] * 182)})
    assert no_net.omitted["breadth.at_extremes"] == ("no NYSE net volume ($VOLD) known within 5 minutes of both SPX highs, "
                                                     "12:16 and 12:31")


def test_the_extremes_name_the_earlier_extreme_price_names(scene_factory):
    grind = [7700.0 + 0.2 * i for i in range(182)]
    scene = with_prior(scene_factory(NOW, bars_from_closes(grind), rows_before=[ANCHOR_ROW],
                                     market=MarketContext({"$VOLD": per_minute([1e6] * 182), "IWM": per_minute([250.0] * 182)})),
                       USUAL_IWM, FLAT_SPX)
    assert "the earlier high from 12:16" in build_price_labels(scene).state["price"]["session_extreme_recent"]
    assert sentence(build_breadth_labels(scene), "breadth.at_extremes").startswith(
        "when SPX made its new session high at 12:31, NYSE net volume was +1M, level with the +1M where it stood at the previous "
        "high at 12:16,")


def test_nothing_after_the_extreme_or_the_read_counts(scene_factory):
    higher_small_caps_later = {"IWM": [(at(12, 25), 251.0)]}             # after SPX's 12:20 high, before the read
    unfinished_spx_bar = climb_dip_climb() + [7760.0]                    # the 12:32 bar finishes after the read
    ls = extremes_read(scene_factory, fading_net, lagging_small_caps, closes=unfinished_spx_bar, extra_known=higher_small_caps_later)
    assert sentence(ls, "breadth.at_extremes").startswith("when SPX made its new session high at 12:20")
    assert "small caps (IWM) were 0.33 sigma below their own session high, further from it than 17" in sentence(ls, "breadth.at_extremes")


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
                   market: bool = True, prior=USUAL_SHARES):
    """10,000 shares a minute from 09:30 in blocks of (minutes, share in rising stocks); the prior sessions each
    held one share all day (``prior``)."""
    up = [10000.0 * share for n, share in blocks for _ in range(n)]
    down = [10000.0 * (1 - share) for n, share in blocks for _ in range(n)]
    if extra:
        up.append(extra[0])
        down.append(extra[1])
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    scene = scene_factory(now, flat_bars(minutes), market=MarketContext(upvol(up, down)) if market else None)
    return build_breadth_labels(with_prior(scene, even_prior_upvol(prior)))


def test_breadth_flipped_after_the_fed(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    ls = around_release(scene_factory, at(14, 40, ss=10), [(210, 0.5), (60, 0.63), (40, 0.38)])
    assert ls.gates["breadth_flip_after_release"] is None
    assert sentence(ls, "breadth.flip_after_release") == (
        "since the Fed's rate decision at 14:00, 40 minutes ago, NYSE net volume changed by -96M, higher than 0 of the last 20 sessions "
        "over the same minutes, bottom third: leaning to selling; in the hour before it net volume changed by +156M, higher than 20 of "
        "the last 20 sessions over the same minutes, top third: leaning to buying")


def test_breadth_flipped_to_buying_and_breadth_that_kept_its_side(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    to_buying = around_release(scene_factory, at(14, 40, ss=10), [(210, 0.5), (60, 0.37), (40, 0.64)])
    assert sentence(to_buying, "breadth.flip_after_release").endswith(
        "NYSE net volume changed by +112M, higher than 20 of the last 20 sessions over the same minutes, top third: leaning to buying; "
        "in the hour before it net volume changed by -156M, higher than 0 of the last 20 sessions over the same minutes, bottom third: "
        "leaning to selling")
    kept = around_release(scene_factory, at(14, 40, ss=10), [(210, 0.5), (60, 0.35), (40, 0.3)])
    assert sentence(kept, "breadth.flip_after_release").endswith(
        "NYSE net volume changed by -160M, higher than 0 of the last 20 sessions over the same minutes, bottom third: leaning to "
        "selling; in the hour before it net volume changed by -180M, higher than 0 of the last 20 sessions over the same minutes, "
        "bottom third: leaning to selling")


def test_each_side_of_the_release_is_ranked_against_the_same_minutes_on_the_prior_sessions(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    afternoon = [0.405 if k % 2 else 0.595 for k in range(20)]      # the prior afternoons: half heavy in rising stocks, half light
    prior = even_prior_upvol(USUAL_SHARES)
    for k, d in enumerate(PRIOR_DAYS):
        prior[d] = block_upvol([(270, 10000.0, USUAL_SHARES[k]), (120, 10000.0, afternoon[k])], day=d)
    scene = scene_factory(at(14, 40, ss=10), flat_bars(310), market=MarketContext(block_upvol([(210, 10000.0, 0.5), (60, 10000.0, 0.63),
                                                                                              (40, 10000.0, 0.51)])))
    ls = build_breadth_labels(with_prior(scene, prior))
    assert sentence(ls, "breadth.flip_after_release").startswith(
        "since the Fed's rate decision at 14:00, 40 minutes ago, NYSE net volume changed by +8M, higher than 10 of the last 20 sessions "
        "over the same minutes, middle third: no lean;")
    thin = around_release(scene_factory, at(14, 40, ss=10), [(210, 0.5), (60, 0.63), (40, 0.38)], prior=USUAL_SHARES[:9])
    assert thin.omitted["breadth.flip_after_release"] == ("its rank needs 10 prior sessions with NYSE net volume ($VOLD) from 14:00 to "
                                                          "14:40, have 9")


def test_a_release_in_the_first_hour_is_held_against_the_minutes_from_the_open(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, [("10:00", "ISM_MANUFACTURING", "data_10am")])
    ls = around_release(scene_factory, at(10, 32, ss=10), [(30, 0.55), (32, 0.6)])
    assert sentence(ls, "breadth.flip_after_release") == (
        "since the ISM manufacturing report at 10:00, 32 minutes ago, NYSE net volume changed by +64M, higher than 20 of the last 20 "
        "sessions over the same minutes, top third: leaning to buying; in the 30 minutes before it, from the open, net volume changed by "
        "+30M, higher than 15 of the last 20 sessions over the same minutes, top third: leaning to buying")


def test_the_fed_chairs_jackson_hole_speech_is_a_release_and_other_fed_remarks_are_not(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, [("10:00", "FED_CHAIR_JACKSON_HOLE", 1)])
    ls = around_release(scene_factory, at(10, 32, ss=10), [(30, 0.55), (32, 0.6)])
    assert ls.gates["breadth_flip_after_release"] is None
    assert sentence(ls, "breadth.flip_after_release").startswith("since the Fed chair's Jackson Hole speech at 10:00, 32 minutes ago,")
    calendar(tmp_path, monkeypatch, [("10:00", "FED_CHAIR_SPEECH", "fed_speaker")])
    assert around_release(scene_factory, at(10, 32, ss=10), [(62, 0.5)]).gates["breadth_flip_after_release"] == (
        "no in-session release in the last 120 minutes")


def test_the_gate_sleeps_until_the_releases_first_reaction_is_over(scene_factory, tmp_path, monkeypatch):
    """At 10:02 two minutes of net volume since a 10:00 release are noise, not a side."""
    calendar(tmp_path, monkeypatch, [("10:00", "ISM_MANUFACTURING", "data_10am")])
    assert around_release(scene_factory, at(10, 2, ss=10), [(30, 0.55), (2, 0.6)]).gates["breadth_flip_after_release"] == (
        "the ISM manufacturing report came out at 10:00: its first 15 minutes are not over")
    assert around_release(scene_factory, at(10, 15, ss=10), [(30, 0.55), (15, 0.6)]).gates["breadth_flip_after_release"] is None


def test_no_change_since_the_release_leans_neither_way(scene_factory, tmp_path, monkeypatch):
    """Every prior session's net volume rose over the same minutes, so a strict rank puts no change in the bottom third,
    as selling; no change is no lean."""
    calendar(tmp_path, monkeypatch, [("10:00", "ISM_MANUFACTURING", "data_10am")])
    ls = around_release(scene_factory, at(10, 32, ss=10), [(30, 0.55), (32, 0.5)], prior=USUAL_SHARES[10:] * 2)
    assert "NYSE net volume changed by +0M, unchanged, taken as the middle third: no lean;" in sentence(ls, "breadth.flip_after_release")


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
        "since the Fed's rate decision at 14:00, 40 minutes ago, NYSE net volume changed by -96M")


def test_the_gate_is_decided_by_the_calendar_and_the_label_needs_the_volume(scene_factory, tmp_path, monkeypatch):
    calendar(tmp_path, monkeypatch, FOMC_AND_PRESSER)
    no_context = around_release(scene_factory, at(14, 40, ss=10), [], market=False)
    assert no_context.gates["breadth_flip_after_release"] is None
    assert no_context.omitted["breadth.flip_after_release"] == "no market-context snapshot today"
    stopped = around_release(scene_factory, at(14, 40, ss=10), [(260, 0.5)])
    assert stopped.omitted["breadth.flip_after_release"] == ("no NYSE net volume ($VOLD) known within 5 minutes of 14:00 and of now: "
                                                             "the market-context job stopped or has not saved it")


# ---- the opening lane: breadth.open_net_volume, breadth.opening_tick, breadth.tick_extreme_5m and its gate

OPENING = at(9, 45)                     # a read on the bar clock, stamped at the 09:44 bar's close


def tick_bar(start: datetime, high: float, low: float, close: float) -> tuple[datetime, dict]:
    return start + timedelta(minutes=1), {"ts": start.isoformat(), "open": close, "high": high, "low": low, "close": close, "volume": 0}


def prior_tick_sessions(n: int = 20, minutes: int = 60) -> dict[str, MarketContext]:
    """``n`` prior sessions whose $TICK bar at each minute from 09:30 reaches 400, 425, ... 875 (and the mirror
    below zero), so the top 5% band at every minute is 851.25 and the bottom one -851.25, and closes at -95, -85,
    ... +95, which is also its mean since the open."""
    out = {}
    for k, d in enumerate(PRIOR_DAYS[:n]):
        reach = 400.0 + 25.0 * k
        bars = [tick_bar(at(9, 30, day=d) + timedelta(minutes=i), reach, -reach, -95.0 + 10.0 * k) for i in range(minutes)]
        out[d] = MarketContext({"$TICK": [(t, b["close"]) for t, b in bars]}, {"$TICK": bars})
    return out


# SPX on each prior session: flat at 7700 until 09:40, then 0 to 4.75 points away (0.25 a session).
FIVE_MINUTE_MOVES = prior_spx([0.25 * k for k in range(20)], from_bar=10)


def opening_tick_read(scene_factory, today: list[tuple[float, float, float]], now: datetime = OPENING, closes=None,
                      prior: dict | None = None, anchored: bool = True, spx=FIVE_MINUTE_MOVES):
    """Today's $TICK bars from 09:30 as (high, low, close); SPX flat unless ``closes`` are given."""
    bars = [tick_bar(at(9, 30) + timedelta(minutes=i), h, lo, c) for i, (h, lo, c) in enumerate(today)]
    market = MarketContext({"$TICK": [(t, b["close"]) for t, b in bars]}, {"$TICK": bars})
    minutes = int((now - at(9, 30)).total_seconds() // 60)
    scene = scene_factory(now, bars_from_closes(closes or [7700.0] * minutes), rows_before=[ANCHOR_ROW] if anchored else None,
                          market=market)
    return build_breadth_labels(with_prior(scene, prior_tick_sessions() if prior is None else prior, spx))


QUIET_TICK = (300.0, -300.0, 180.0)
BUY_BURST = (900.0, -300.0, 180.0)
SELL_BURST = (300.0, -900.0, -400.0)


def test_a_one_sided_buying_open(scene_factory):
    ls = opening_tick_read(scene_factory, [BUY_BURST] * 4 + [QUIET_TICK] * 11)
    assert sentence(ls, "breadth.opening_tick") == (
        "since the open NYSE TICK averaged +180, higher than 20 of the last 20 sessions at this minute, top third: leaning to buying; "
        "its 1-minute highs reached the top 5% band for these minutes 4 times, at or past the 3-burst cluster count; its 1-minute lows "
        "never reached the bottom 5% band")


def test_the_opening_tick_mean_turns_third_at_the_rank_edges_and_the_bands_hold_theirs(scene_factory):
    on_lines = opening_tick_read(scene_factory, [(851.3, -300.0, 40.0)] * 3 + [(851.2, -851.3, 40.0)] * 2 + [(300.0, -300.0, 40.0)] * 10)
    assert sentence(on_lines, "breadth.opening_tick") == (
        "since the open NYSE TICK averaged +40, higher than 14 of the last 20 sessions at this minute, top third: leaning to buying; "
        "its 1-minute highs reached the top 5% band for these minutes 3 times, at or past the 3-burst cluster count; its 1-minute lows "
        "reached the bottom 5% band for these minutes twice, short of the 3-burst cluster count")
    tied = opening_tick_read(scene_factory, [(300.0, -300.0, 35.0)] * 15)
    assert sentence(tied, "breadth.opening_tick").startswith("since the open NYSE TICK averaged +35, higher than 13 of the last 20 sessions "
                                                              "at this minute, middle third: no lean;")
    low_edge = opening_tick_read(scene_factory, [(300.0, -300.0, -34.0)] * 15)
    assert sentence(low_edge, "breadth.opening_tick").startswith("since the open NYSE TICK averaged -34, higher than 7 of the last 20 "
                                                                  "sessions at this minute, middle third: no lean;")
    selling = opening_tick_read(scene_factory, [(300.0, -300.0, -36.0)] * 15)
    assert sentence(selling, "breadth.opening_tick").startswith("since the open NYSE TICK averaged -36, higher than 6 of the last 20 "
                                                                 "sessions at this minute, bottom third: leaning to selling;")


def test_the_opening_tick_needs_a_live_feed_and_ten_prior_sessions(scene_factory):
    stopped = opening_tick_read(scene_factory, [QUIET_TICK] * 9)                 # the newest bar finished at 09:39
    assert stopped.omitted["breadth.opening_tick"] == "no NYSE TICK bar in the last 5 minutes: the market-context job stopped or has not saved it"
    thin = opening_tick_read(scene_factory, [QUIET_TICK] * 15, prior=prior_tick_sessions(9))
    assert thin.omitted["breadth.opening_tick"] == "needs 10 prior sessions of NYSE TICK bars at each minute since the open"
    assert "higher than 10 of the last 10 sessions" in sentence(opening_tick_read(scene_factory, [QUIET_TICK] * 15, prior=prior_tick_sessions(10)),
                                                                 "breadth.opening_tick")


def test_a_tick_bar_that_finishes_after_the_read_is_not_a_burst(scene_factory):
    ls = opening_tick_read(scene_factory, [QUIET_TICK] * 15 + [(2000.0, -2000.0, 900.0)])
    assert "highs never reached the top 5% band" in sentence(ls, "breadth.opening_tick")
    assert ls.gates["tick_extreme_follow"] == "no NYSE TICK burst in the last 5 minutes"


SPX_UP_3 = [7700.0] * 10 + [7703.0] * 5               # +3.00 points from 09:40 to 09:45: 0.04 sigma


def spx_moved(points: float) -> list[float]:
    return [7700.0] * 10 + [7700.0 + points] * 5


def test_a_buying_burst_that_spx_followed(scene_factory):
    ls = opening_tick_read(scene_factory, [QUIET_TICK] * 12 + [BUY_BURST] + [QUIET_TICK] * 2, closes=SPX_UP_3)
    assert ls.gates["tick_extreme_follow"] is None
    assert sentence(ls, "breadth.tick_extreme_5m") == (
        "in the last 5 minutes NYSE TICK's 1-minute high reached the top 5% band for its minute (a buying burst) and its low did not "
        "reach the bottom band; SPX rose 0.04 sigma over the same 5 minutes, in the middle third of the last 20 sessions' 5-minute "
        "moves at this minute (larger than 12 of them): a real move")


def test_a_real_move_is_one_outside_the_bottom_third_and_each_kind_of_burst(scene_factory):
    buy = [QUIET_TICK] * 12 + [BUY_BURST] + [QUIET_TICK] * 2
    stalled = opening_tick_read(scene_factory, buy, closes=spx_moved(1.5))
    assert sentence(stalled, "breadth.tick_extreme_5m").endswith(
        "SPX rose 0.02 sigma over the same 5 minutes, in the bottom third of the last 20 sessions' 5-minute moves at this minute "
        "(larger than 6 of them): no real move")
    followed = opening_tick_read(scene_factory, buy, closes=spx_moved(1.75))
    assert sentence(followed, "breadth.tick_extreme_5m").endswith("in the middle third of the last 20 sessions' 5-minute moves at this "
                                                                  "minute (larger than 7 of them): a real move")
    sell = opening_tick_read(scene_factory, [QUIET_TICK] * 11 + [SELL_BURST] + [QUIET_TICK] * 3, closes=spx_moved(-4.0))
    assert sentence(sell, "breadth.tick_extreme_5m") == (
        "in the last 5 minutes NYSE TICK's 1-minute low reached the bottom 5% band for its minute (a selling burst) and its high did "
        "not reach the top band; SPX fell 0.05 sigma over the same 5 minutes, in the top third of the last 20 sessions' 5-minute moves "
        "at this minute (larger than 16 of them): a real move")
    both = opening_tick_read(scene_factory, [QUIET_TICK] * 10 + [(900.0, -900.0, 0.0)] + [QUIET_TICK] * 4)
    assert sentence(both, "breadth.tick_extreme_5m") == (
        "in the last 5 minutes NYSE TICK's 1-minute high reached the top 5% band for its minute and its low reached the bottom 5% "
        "band (bursts both ways); SPX did not move over the same 5 minutes, in the bottom third of the last 20 sessions' 5-minute "
        "moves at this minute (larger than 0 of them): no real move")
    assert both.gates["tick_extreme_follow"] is None


def test_a_move_against_the_burst_says_so(scene_factory):
    buy_into_fall = opening_tick_read(scene_factory, [QUIET_TICK] * 12 + [BUY_BURST] + [QUIET_TICK] * 2, closes=spx_moved(-4.0))
    assert sentence(buy_into_fall, "breadth.tick_extreme_5m").endswith(
        "(a buying burst) and its low did not reach the bottom band; SPX fell 0.05 sigma over the same 5 minutes, in the top third of "
        "the last 20 sessions' 5-minute moves at this minute (larger than 16 of them): a real move, against the burst")
    sell_into_rise = opening_tick_read(scene_factory, [QUIET_TICK] * 11 + [SELL_BURST] + [QUIET_TICK] * 3, closes=spx_moved(4.0))
    assert sentence(sell_into_rise, "breadth.tick_extreme_5m").endswith("(larger than 16 of them): a real move, against the burst")
    both_ways_fall = opening_tick_read(scene_factory, [QUIET_TICK] * 10 + [(900.0, -900.0, 0.0)] + [QUIET_TICK] * 4, closes=spx_moved(-4.0))
    assert sentence(both_ways_fall, "breadth.tick_extreme_5m").endswith(
        "(bursts both ways); SPX fell 0.05 sigma over the same 5 minutes, in the top third of the last 20 sessions' 5-minute moves at "
        "this minute (larger than 16 of them): a real move")


def test_without_a_burst_the_gate_sleeps_and_the_label_says_so(scene_factory):
    earlier_burst = opening_tick_read(scene_factory, [BUY_BURST] * 10 + [QUIET_TICK] * 5)       # 09:39 is outside the five minutes
    assert earlier_burst.gates["tick_extreme_follow"] == "no NYSE TICK burst in the last 5 minutes"
    assert sentence(earlier_burst, "breadth.tick_extreme_5m").startswith(
        "in the last 5 minutes NYSE TICK's 1-minute highs and lows stayed inside the top and bottom 5% bands for their minutes (no burst);")


def test_the_burst_label_omitted_without_what_it_needs_leaves_its_question_missing_not_asleep(scene_factory):
    stopped = opening_tick_read(scene_factory, [BUY_BURST] * 9)
    reason = "no NYSE TICK bar in the last 5 minutes: the market-context job stopped or has not saved it"
    assert stopped.omitted["breadth.tick_extreme_5m"] == reason and stopped.gates["tick_extreme_follow"] is None
    thin = opening_tick_read(scene_factory, [BUY_BURST] * 15, prior={})
    reason = "needs 10 prior sessions of NYSE TICK bars at each of the last 5 minutes"
    assert thin.omitted["breadth.tick_extreme_5m"] == reason and thin.gates["tick_extreme_follow"] is None
    few_moves = opening_tick_read(scene_factory, [BUY_BURST] * 15, closes=SPX_UP_3, spx=dict(list(FIVE_MINUTE_MOVES.items())[:9]))
    assert few_moves.gates["tick_extreme_follow"] is None
    assert few_moves.omitted["breadth.tick_extreme_5m"] == "its rank needs 10 prior sessions with a 5-minute move at this minute, have 9"
    estimated = opening_tick_read(scene_factory, [BUY_BURST] * 15, closes=SPX_UP_3, anchored=False)
    assert sentence(estimated, "breadth.tick_extreme_5m").endswith(": a real move (ruler estimated)")
    no_context = build_breadth_labels(scene_factory(OPENING, flat_bars(15)))
    assert no_context.gates["tick_extreme_follow"] is None and no_context.omitted["breadth.tick_extreme_5m"] == "no market-context snapshot today"


# The prior sessions' net volume at 09:44 steps from -95M to +95M by 10M (NYSE) and from -47.5M to +47.5M by 5M
# (S&P 500 members), and their advancers minus decliners rose 0 to 475 over the 10 minutes before it.
USUAL_OPEN_NYSE = [-95.0 + 10 * k for k in range(20)]
USUAL_OPEN_MEMBERS = [-47.5 + 5 * k for k in range(20)]
USUAL_THRUST = [25.0 * k for k in range(20)]


def open_volume_read(scene_factory, nyse_m: float, members_m: float, add: tuple[float, float] | None = (100.0, 510.0),
                     prior_add=USUAL_THRUST, prior_nyse=USUAL_OPEN_NYSE, extra: dict | None = None):
    """Today's totals a minute before the 09:45 read and $ADD 10 and 1 minutes before it; the prior sessions'
    totals at 09:44 and their $ADD 10-minute changes ``prior_add`` (None: no $ADD)."""
    known = {"$VOLD": [(at(9, 44), nyse_m * 1e6)], "$VOLSPD": [(at(9, 44), members_m * 1e6)]}
    if add:
        known["$ADD"] = [(at(9, 35), add[0]), (at(9, 44), add[1])]
    for s, pts in (extra or {}).items():
        known[s] = known[s] + pts
    prior = {}
    for d, n, m, a in zip(PRIOR_DAYS, prior_nyse, USUAL_OPEN_MEMBERS, prior_add):
        prior[d] = {"$VOLD": readings(at(9, 44), [n * 1e6], day=d), "$VOLSPD": readings(at(9, 44), [m * 1e6], day=d),
                    **({"$ADD": readings(at(9, 44), [0.0] * 9 + [a], day=d)} if a is not None else {})}
    scene = scene_factory(OPENING, flat_bars(15), market=MarketContext(known))
    return build_breadth_labels(with_prior(scene, prior))


def test_net_volume_since_the_open_both_buying(scene_factory):
    assert sentence(open_volume_read(scene_factory, 48, 21), "breadth.open_net_volume") == (
        "since 09:30 NYSE net volume is +48M, higher than 15 of the last 20 sessions at this minute, top third: leaning to buying; "
        "S&P 500 members' net volume is +21M, higher than 14 of the last 20 sessions at this minute, top third: leaning to buying; "
        "advancers minus decliners rose 410 in the last 10 minutes, higher than 17 of the last 20 sessions at this minute")


def test_net_volume_split_no_lean_and_the_third_edges(scene_factory):
    split = sentence(open_volume_read(scene_factory, -48, 17.5, add=None), "breadth.open_net_volume")
    assert split == ("since 09:30 NYSE net volume is -48M, higher than 5 of the last 20 sessions at this minute, bottom third: leaning "
                     "to selling; S&P 500 members' net volume is +18M, higher than 13 of the last 20 sessions at this minute, middle third: "
                     "no lean")
    assert "members' net volume is +18M, higher than 14 of the last 20 sessions at this minute, top third: leaning to buying" in \
        sentence(open_volume_read(scene_factory, -48, 18, add=None), "breadth.open_net_volume")
    no_lean = sentence(open_volume_read(scene_factory, 0, 0, add=None), "breadth.open_net_volume")
    assert no_lean == ("since 09:30 NYSE net volume is +0M, higher than 10 of the last 20 sessions at this minute, middle third: no lean; "
                       "S&P 500 members' net volume is +0M, higher than 10 of the last 20 sessions at this minute, middle third: no lean")
    assert "NYSE net volume is -35M, higher than 6 of the last 20 sessions at this minute, bottom third" in \
        sentence(open_volume_read(scene_factory, -35, 0, add=None), "breadth.open_net_volume")


def test_a_falling_thrust_is_ranked_by_the_size_of_its_fall(scene_factory):
    steepest = sentence(open_volume_read(scene_factory, 48, 21, add=(510.0, 100.0)), "breadth.open_net_volume")
    assert steepest.endswith("; advancers minus decliners fell 410 in the last 10 minutes, a bigger fall than 20 of the last 20 sessions "
                             "at this minute")
    among_falls = sentence(open_volume_read(scene_factory, 48, 21, add=(510.0, 100.0), prior_add=[-b for b in USUAL_THRUST]),
                           "breadth.open_net_volume")
    assert among_falls.endswith("fell 410 in the last 10 minutes, a bigger fall than 17 of the last 20 sessions at this minute")


def test_the_thrust_clause_needs_its_readings_and_ten_prior_sessions(scene_factory):
    assert sentence(open_volume_read(scene_factory, 48, 21, add=None), "breadth.open_net_volume").endswith("top third: leaning to buying")
    unranked = sentence(open_volume_read(scene_factory, 48, 21, prior_add=USUAL_THRUST[:9] + [None] * 11), "breadth.open_net_volume")
    assert unranked.endswith("; advancers minus decliners rose 410 in the last 10 minutes")


def test_net_volume_since_the_open_omitted_without_its_series_ten_prior_sessions_and_never_ahead_of_the_read(scene_factory):
    ls = open_volume_read(scene_factory, 48, 21, extra={"$VOLD": [(at(9, 46), 900e6)]})
    assert "NYSE net volume is +48M" in sentence(ls, "breadth.open_net_volume")
    scene = scene_factory(OPENING, flat_bars(15), market=MarketContext({"$VOLSPD": [(at(9, 44), 21e6)]}))
    assert build_breadth_labels(scene).omitted["breadth.open_net_volume"] == "the market-context job has saved no NYSE net volume ($VOLD) today"
    thin = open_volume_read(scene_factory, 48, 21, prior_nyse=USUAL_OPEN_NYSE[:9])
    assert thin.omitted["breadth.open_net_volume"] == "its rank needs 10 prior sessions with NYSE net volume ($VOLD) at this minute, have 9"
