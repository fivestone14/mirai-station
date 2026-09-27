"""The breadth family (labels/breadth.py): each label's sentence at its verdicts and their boundaries, its
omissions, and that a value known after the read never counts.

The market context is built as the context job saves it (market_context.py): $TICK and $TRIN a reading a
minute, $UVOL and $DVOL (thousands of shares) and $VOLD and $VOLSPD (shares) running totals since 09:30,
each value known once its minute has finished."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from conftest import at, flat_bars
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
