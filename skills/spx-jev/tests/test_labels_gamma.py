"""The gamma family's labels of the final set (gex.*) and its two sleep gates, on rows shaped like the SPX diary's:
each verdict and its boundary, the omissions, and point in time (a later bar or diary row never counts)."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

from conftest import at, bars_from_closes, flat_bars, make_row
from spx_jev.cuts import (BOX_TIGHT_SIGMA, BOX_WIDE_SIGMA, CHARM_NEAR_HI, CHARM_NEAR_LO, FLIP_FAR_SIGMA, MAGNET_NEAR_SIGMA,
                          MAGNET_SEAT_SIGMA, MIN_RANK_SESSIONS, PIN_HUG_HI, PIN_REACH_FAR, SETTLE_SEAT_SIGMA)
from spx_jev.labels.gamma import ROW_SLACK_MIN, build_gamma_labels
from spx_jev.labels.rulers import NO_ANCHOR
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.row_adapter import labeller_row

SIGMA = 75.0
NOW = at(12, 32, ss=10)
DEALERS = "under the usual assumption that dealers hold the calls and are short the puts"
# today's same-day book as the scanner writes it on 2026-09-25 around 12:30: price above the flip, call-heavy by
# both reads, the magnet a strike above price, the 1-to-7-day book weighted above price
BOOK = {"flip": 7655.0, "regime": "long_gamma", "regime_0dte_vol": "long_gamma", "regime_source": "0dte", "magnet": 7705.0,
        "pin_top_share": 0.09, "pin_centroid": 7702.0, "charm_wall": 7715.0, "call_wall_gamma": 7710.0, "put_wall_gamma": 7695.0,
        "gamma_above_spot": 480.0, "gamma_below_spot": 320.0,
        "net_by_strike_tenor": [[7600.0, -4.0], [7650.0, -2.0], [7700.0, 9.0], [7750.0, 6.0], [7800.0, 3.0]]}


def book(**over) -> dict:
    return {**make_row(NOW, 7700.0)["gex_views"], **BOOK, **over}


def diary_row(ts, spot: float = 7700.0, **over) -> dict:
    return make_row(ts, spot, SIGMA, gex_views=book(**over))


def gamma_scene(scene_factory, now=NOW, spot: float = 7700.0, then: dict | None = None, bars=None, **over):
    """A read at ``now`` with the morning's first row (the anchor) and a row 30 minutes earlier carrying ``then``."""
    rows_before = [diary_row(at(9, 31), spot), diary_row(now - timedelta(minutes=30, seconds=40), spot, **{**over, **(then or {})})]
    return scene_factory(now, bars if bars is not None else flat_bars(int((now - at(9, 30)).total_seconds() // 60), price=spot),
                         row_over={"gex_views": book(**over)}, rows_before=rows_before, spot=spot)


def labels(scene):
    ls = build_gamma_labels(scene)
    return ls.state.get("gex", {}), ls.omitted, ls.gates


def write_diary(root, day: str, rows: list[dict]) -> None:
    (root / "reversion").mkdir(parents=True, exist_ok=True)
    (root / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


# ---- gex.book_balance

def test_book_balance_both_reads_agree_and_the_open_interest_balance_tips_at_the_flip(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory))
    assert state["book_balance"] == (f"at the current price, same-day call positions carry more gamma than put positions, both by open interest "
                                     f"and by today's volume, {DEALERS}; the open-interest balance would tip to puts 0.60 sigma lower")
    state, _, _ = labels(gamma_scene(scene_factory, flip=7745.0, regime="short_gamma", regime_0dte_vol="short_gamma"))
    assert state["book_balance"].startswith("at the current price, same-day put positions carry more gamma than call positions, both")
    assert state["book_balance"].endswith("; the open-interest balance would tip to calls 0.60 sigma higher")


def test_book_balance_says_when_the_two_reads_differ_or_are_too_close_to_call(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, regime="short_gamma", flip=7745.0))
    assert state["book_balance"] == (f"at the current price, today's same-day book reads two ways: by open interest put positions carry more "
                                     f"gamma than call positions, while by today's volume call positions carry more gamma than put positions, "
                                     f"{DEALERS}; the open-interest balance would tip to calls 0.60 sigma higher")
    state, _, _ = labels(gamma_scene(scene_factory, regime_0dte_vol="uncertain"))
    assert "while by today's volume the two are too close to call" in state["book_balance"]
    state, _, _ = labels(gamma_scene(scene_factory, regime="uncertain", regime_0dte_vol="uncertain", flip=7702.0))
    assert state["book_balance"] == ("at the current price, today's same-day book is too close to call both by open interest and by today's "
                                     "volume; the gamma flip sits 0.03 sigma above price")


def test_book_balance_is_omitted_without_a_same_day_book_or_a_volume_read(scene_factory):
    _, omitted, _ = labels(gamma_scene(scene_factory, regime_source="blended_fallback"))
    assert omitted["gex.book_balance"] == "today's same-day book could not be read on this row, so the scanner fell back to the blended book"
    _, omitted, _ = labels(gamma_scene(scene_factory, regime_0dte_vol="unknown"))
    assert omitted["gex.book_balance"] == "row carries no balance for today's same-day book by both open interest and today's volume"


# ---- gex.balance_vs_yesterday (DAY, 2026-09-18, is a Friday: the previous session is Thursday 17 September)

def test_balance_vs_yesterday_reads_the_previous_sessions_last_row_by_its_close(scene_factory, tmp_path):
    write_diary(tmp_path, "2026-09-17", [diary_row(at(15, 58, "2026-09-17"), regime="short_gamma"),
                                         diary_row(at(16, 5, "2026-09-17"), regime="long_gamma")])
    scene = replace(gamma_scene(scene_factory), state_dir=tmp_path)
    state, _, _ = labels(scene)
    assert state["balance_vs_yesterday"] == (f"by open interest the same-day book leaned to puts at yesterday's close and leans to calls now, "
                                             f"{DEALERS}, so it has tipped overnight")
    write_diary(tmp_path, "2026-09-17", [diary_row(at(15, 58, "2026-09-17"))])
    state, _, _ = labels(scene)
    assert state["balance_vs_yesterday"] == f"by open interest the same-day book leaned to calls at yesterday's close too, {DEALERS}, so it has not tipped overnight"


def test_balance_vs_yesterday_when_either_close_is_too_close_to_call(scene_factory, tmp_path):
    scene = replace(gamma_scene(scene_factory), state_dir=tmp_path)
    write_diary(tmp_path, "2026-09-17", [diary_row(at(15, 58, "2026-09-17"), regime="uncertain")])
    assert labels(scene)[0]["balance_vs_yesterday"] == (f"by open interest the same-day book was too close to call at yesterday's close and "
                                                        f"leans to calls now, {DEALERS}, so it has tipped overnight")
    scene = replace(gamma_scene(scene_factory, regime="uncertain"), state_dir=tmp_path)
    assert labels(scene)[0]["balance_vs_yesterday"] == (f"by open interest the same-day book was too close to call at yesterday's close too, "
                                                        f"{DEALERS}, so it is still too close to call")
    write_diary(tmp_path, "2026-09-17", [diary_row(at(15, 58, "2026-09-17"), regime="short_gamma")])
    assert labels(scene)[0]["balance_vs_yesterday"] == (f"by open interest the same-day book leaned to puts at yesterday's close and is too "
                                                        f"close to call now, {DEALERS}, so its lean has faded overnight")


def test_balance_vs_yesterday_is_omitted_without_the_previous_sessions_close(scene_factory, tmp_path):
    scene = gamma_scene(scene_factory)
    assert labels(scene)[1]["gex.balance_vs_yesterday"] == "no state folder to read yesterday's diary from"
    scene = replace(scene, state_dir=tmp_path)
    write_diary(tmp_path, "2026-09-16", [diary_row(at(15, 58, "2026-09-16"))])
    assert labels(scene)[1]["gex.balance_vs_yesterday"] == "the diary has no rows from the previous session; its newest earlier day is 2026-09-16"
    write_diary(tmp_path, "2026-09-17", [diary_row(at(15, 59 - ROW_SLACK_MIN - 2, "2026-09-17"))])
    assert labels(scene)[1]["gex.balance_vs_yesterday"] == "no diary row within 5 minutes of yesterday's close carries the book's balance"


# ---- gex.flip_distance

def test_flip_distance_near_and_far_at_the_far_line(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, flip=7700.0 - FLIP_FAR_SIGMA * SIGMA))
    assert state["flip_distance"] == (f"price is 0.50 sigma above the gamma flip, the level where today's same-day book switches between "
                                      f"call-heavy and put-heavy gamma {DEALERS}; within the 0.5 sigma far line; it has not crossed the flip "
                                      f"in the last 30 minutes")
    state, _, _ = labels(gamma_scene(scene_factory, flip=7662.0))
    assert "price is 0.51 sigma above the gamma flip" in state["flip_distance"] and "; beyond the 0.5 sigma far line;" in state["flip_distance"]


def test_flip_distance_says_price_crossed_it_since_the_row_30_minutes_ago(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, then={"flip": 7710.0}))
    assert state["flip_distance"].endswith("; it has crossed the flip in the last 30 minutes")


def test_flip_distance_with_no_flip_or_a_balanced_book_is_a_fact_not_a_gap(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, flip=None))
    assert state["flip_distance"] == "no gamma flip was found in today's same-day book"
    state, _, _ = labels(gamma_scene(scene_factory, regime="uncertain"))
    assert state["flip_distance"] == "today's same-day book is too close to call at the current price, so no gamma flip stands out"


def test_flip_distance_is_omitted_without_the_row_30_minutes_ago(scene_factory):
    scene = gamma_scene(scene_factory)
    scene = replace(scene, rows_today=[scene.rows_today[0], scene.row])
    assert labels(scene)[1]["gex.flip_distance"] == "no diary row from 30 minutes ago carries a gamma flip"


def test_a_ruler_that_is_not_the_morning_anchor_is_flagged_and_no_ruler_omits(scene_factory):
    scene = gamma_scene(scene_factory)
    late = replace(scene, rows_today=scene.rows_today[1:])      # first row after 09:40: the earliest live sigma stands in
    assert labels(late)[0]["flip_distance"].endswith("in the last 30 minutes; ruler estimated")
    no_ruler = [labeller_row(make_row(NOW - timedelta(minutes=30, seconds=40), 7700.0, sigma_live=None, gex_views=book())),
                labeller_row(make_row(NOW, 7700.0, sigma_live=None, gex_views=book()))]
    _, omitted, _ = labels(replace(scene, rows_today=no_ruler, row=no_ruler[-1]))
    assert omitted["gex.flip_distance"] == NO_ANCHOR and omitted["gex.magnet_distance"] == NO_ANCHOR


# ---- gex.weight_both_books

def test_weight_both_books_on_the_same_side(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, gamma_above_spot=300.0, gamma_below_spot=500.0,
                                     net_by_strike_tenor=[[7650.0, -6.1], [7690.0, 0.0], [7700.0, 9.0], [7710.0, 3.9]]))
    assert state["weight_both_books"] == ("today's same-day options gamma sits 62% below price, past the 40%-60% even band on the below side; "
                                          "the 1-to-7-day book (today's expiry excluded) also sits 61% below price, past the 40%-60% even band "
                                          "on the below side")


def test_weight_both_books_at_the_even_band_edges_and_the_strike_at_price_on_neither_side(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, gamma_above_spot=400.0, gamma_below_spot=600.0,
                                     net_by_strike_tenor=[[7650.0, -2.0], [7700.0, 50.0], [7750.0, 3.0]]))
    assert state["weight_both_books"] == ("today's same-day options gamma splits 40% above and 60% below price, inside the 40%-60% even band; "
                                          "the 1-to-7-day book (today's expiry excluded) sits 60% above price, past the 40%-60% even band on "
                                          "the above side")


def test_weight_both_books_is_omitted_without_the_week_book(scene_factory):
    _, omitted, _ = labels(gamma_scene(scene_factory, net_by_strike_tenor=None))
    assert omitted["gex.weight_both_books"] == "row carries no gamma by strike for the 1-to-7-day book"


# ---- gex.magnet_distance

def grip_history(root, n: int, share: float = 0.05, clock=(12, 32)) -> dict:
    """``n`` prior sessions' diaries with a row a minute before the read's clock carrying ``share``, and their rulers."""
    rulers = {}
    for d in range(17, 17 - n, -1):
        day = f"2026-09-{d:02d}"
        write_diary(root, day, [diary_row(at(clock[0], clock[1] - 1, day), pin_top_share=share + 0.001 * d)])
        rulers[day] = SigmaRuler(SIGMA, "anchor")
    return rulers


def magnet_scene(scene_factory, tmp_path, closes=None, **over):
    closes = closes if closes is not None else [7700.0] * 182
    scene = gamma_scene(scene_factory, bars=bars_from_closes(closes), **over)
    return replace(scene, state_dir=tmp_path, prior_rulers=grip_history(tmp_path, 10))


def test_magnet_distance_held_on_the_magnet(scene_factory, tmp_path):
    state, _, _ = labels(magnet_scene(scene_factory, tmp_path))
    assert state["magnet_distance"] == ("today's heaviest same-day strike (the magnet) sits 0.07 sigma above price, inside the 0.1 sigma seat "
                                        "distance; its grip (top-strike share) is stronger than on 10 of the last 10 sessions at 12:32 ET, at or "
                                        "above the median; it is the same strike as 30 minutes ago; price spent 30 of the last 30 minutes within "
                                        "the seat distance of it, at or past the 0.7 hug share")


def test_magnet_distance_at_the_seat_and_near_lines(scene_factory, tmp_path):
    seat = labels(magnet_scene(scene_factory, tmp_path, magnet=7700.0 + MAGNET_SEAT_SIGMA * SIGMA))[0]["magnet_distance"]
    assert "sits 0.10 sigma above price, between the 0.1 sigma seat and 0.3 sigma near distances;" in seat
    near = labels(magnet_scene(scene_factory, tmp_path, magnet=7700.0 - MAGNET_NEAR_SIGMA * SIGMA))[0]["magnet_distance"]
    assert "sits 0.30 sigma below price, beyond the 0.3 sigma near distance;" in near


def test_magnet_distance_hug_share_and_grip_median_boundaries(scene_factory, tmp_path):
    # 21 of the last 30 minutes within 7.5 points of the magnet (7705): the hug line exactly
    closes = [7700.0] * 152 + [7690.0] * 9 + [7700.0] * 21
    state, _, _ = labels(magnet_scene(scene_factory, tmp_path, closes=closes))
    assert f"price spent 21 of the last 30 minutes within the seat distance of it, at or past the {PIN_HUG_HI} hug share" in state["magnet_distance"]
    closes = [7700.0] * 152 + [7690.0] * 10 + [7700.0] * 20
    state, _, _ = labels(magnet_scene(scene_factory, tmp_path, closes=closes))
    assert "price spent 20 of the last 30 minutes within the seat distance of it, short of the 0.7 hug share" in state["magnet_distance"]
    # the prior grips run 0.058 to 0.067: beating 5 of 10 is the median exactly, 4 of 10 is below it
    state, _, _ = labels(magnet_scene(scene_factory, tmp_path, pin_top_share=0.0625))
    assert "stronger than on 5 of the last 10 sessions at 12:32 ET, at or above the median;" in state["magnet_distance"]
    state, _, _ = labels(magnet_scene(scene_factory, tmp_path, pin_top_share=0.0615))
    assert "stronger than on 4 of the last 10 sessions at 12:32 ET, below the median;" in state["magnet_distance"]


def test_magnet_distance_counts_only_finished_bars_and_prior_rows_by_the_same_minute(scene_factory, tmp_path):
    scene = magnet_scene(scene_factory, tmp_path, closes=[7700.0] * 152 + [7690.0] * 30)
    late = {"ts": NOW.replace(second=0).isoformat(), "open": 7700.0, "high": 7700.5, "low": 7699.5, "close": 7700.0}
    assert "price spent 0 of the last 30 minutes" in labels(replace(scene, bars=scene.bars + [late]))[0]["magnet_distance"]
    for d in range(8, 18):
        day = f"2026-09-{d:02d}"
        rows = (tmp_path / "reversion" / f"{day}.jsonl").read_text()
        write_diary(tmp_path, day, [json.loads(rows), diary_row(at(12, 33, day), pin_top_share=0.5)])
    assert "stronger than on 10 of the last 10 sessions" in labels(scene)[0]["magnet_distance"]


def test_magnet_distance_is_omitted_without_its_history(scene_factory, tmp_path):
    scene = gamma_scene(scene_factory, bars=flat_bars(182))
    assert labels(scene)[1]["gex.magnet_distance"] == "no state folder to read the prior sessions' diaries from"
    few = replace(scene, state_dir=tmp_path, prior_rulers=grip_history(tmp_path, MIN_RANK_SESSIONS - 1))
    assert labels(few)[1]["gex.magnet_distance"] == "its grip needs 5 prior sessions' diaries at this minute, have 4"
    estimated = replace(scene, state_dir=tmp_path, prior_rulers={**grip_history(tmp_path, 6), "2026-09-17": SigmaRuler(SIGMA, "live"),
                                                                 "2026-09-16": SigmaRuler(SIGMA, "vix")})
    assert labels(estimated)[1]["gex.magnet_distance"] == "its grip needs 5 prior sessions' diaries at this minute, have 4"
    _, omitted, _ = labels(replace(magnet_scene(scene_factory, tmp_path), bars=[]))
    assert omitted["gex.magnet_distance"] == "no minute bar finished in the last 30 minutes"


# ---- gex.settle_pull (the row's straddle left is 16.4 points)

def test_settle_pull_in_reach_with_the_centre_on_the_same_side(scene_factory):
    now = at(15, 32, ss=10)
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=7710.0, pin_centroid=7707.0))
    assert state["settle_pull"] == ("today's heaviest same-day strike sits 0.13 sigma above price, outside the 0.05 sigma seat distance; that is "
                                    "0.6 remaining straddles (what today's options still price before the 16:00 settle), within the 1.5 reach "
                                    "line; the gamma-weighted centre of the book sits 0.09 sigma above price, outside the seat distance, the "
                                    "same side; the options settle in 28 minutes")


def test_settle_pull_at_the_reach_line_and_split_by_the_centre(scene_factory):
    now = at(14, 2, ss=10)
    magnet = 7700.0 - PIN_REACH_FAR * 16.4
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=magnet, pin_centroid=7700.0 + SETTLE_SEAT_SIGMA * SIGMA + 1.0))
    assert "that is 1.5 remaining straddles (what today's options still price before the 16:00 settle), at or beyond the 1.5 reach line;" in state["settle_pull"]
    assert "the gamma-weighted centre of the book sits 0.06 sigma above price, outside the seat distance, the other side;" in state["settle_pull"]
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=7700.0 + SETTLE_SEAT_SIGMA * SIGMA, pin_centroid=None))
    assert state["settle_pull"].startswith("today's heaviest same-day strike sits 0.05 sigma above price, inside the 0.05 sigma seat distance;")
    assert "; no strike of the book pulls, so it has no gamma-weighted centre;" in state["settle_pull"]


def test_settle_pull_is_omitted_after_the_settle_and_without_the_straddle(scene_factory):
    _, omitted, _ = labels(gamma_scene(scene_factory, now=at(16, 0, ss=30)))
    assert omitted["gex.settle_pull"] == "today's same-day options have settled"
    scene = gamma_scene(scene_factory)
    scene = replace(scene, row={**scene.row, "range_ruler": {}})
    assert labels(scene)[1]["gex.settle_pull"] == "row carries no straddle left for today's same-day book"


# ---- gex.charm_wall_distance

def test_charm_wall_distance_at_each_line(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, charm_wall=7700.0 + CHARM_NEAR_LO * SIGMA))
    assert state["charm_wall_distance"] == ("the charm wall of today's same-day book (the strike where its delta decay piles up) sits 0.10 sigma "
                                            "above price, between the 0.1 near-low and 0.5 near-high distances, 0.5 remaining straddles away")
    state, _, _ = labels(gamma_scene(scene_factory, charm_wall=7695.0))
    assert "sits 0.07 sigma below price, within the 0.1 sigma near-low distance," in state["charm_wall_distance"]
    state, _, _ = labels(gamma_scene(scene_factory, charm_wall=7700.0 - CHARM_NEAR_HI * SIGMA))
    assert "sits 0.50 sigma below price, between the 0.1 near-low and 0.5 near-high distances," in state["charm_wall_distance"]
    state, _, _ = labels(gamma_scene(scene_factory, charm_wall=7740.0))
    assert "sits 0.53 sigma above price, beyond the 0.5 sigma near-high distance," in state["charm_wall_distance"]


def test_charm_wall_distance_is_omitted_without_a_charm_wall(scene_factory):
    assert labels(gamma_scene(scene_factory, charm_wall=None))[1]["gex.charm_wall_distance"] == "row carries no charm wall for today's same-day book"


# ---- gex.wall_box and gex.walls_since_30min

def test_wall_box_widths_at_the_tight_and_wide_lines(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7705.0, put_wall_gamma=7695.0))
    assert state["wall_box"] == ("the heaviest call-side and put-side strikes of today's same-day book are 0.13 sigma apart, under the 0.15 sigma "
                                 "tight width; both have stood at the same strikes for the last 30 minutes")
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7700.0 + BOX_TIGHT_SIGMA * SIGMA, put_wall_gamma=7700.0))
    assert "are 0.15 sigma apart, between the 0.15 sigma tight and 0.3 sigma wide widths;" in state["wall_box"]
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7700.0 + BOX_WIDE_SIGMA * SIGMA, put_wall_gamma=7700.0))
    assert "are 0.30 sigma apart, between the 0.15 sigma tight and 0.3 sigma wide widths;" in state["wall_box"]
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7725.0, put_wall_gamma=7700.0))
    assert "are 0.33 sigma apart, past the 0.3 sigma wide width;" in state["wall_box"]


def test_the_walls_against_the_row_30_minutes_ago(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, then={"call_wall_gamma": 7705.0}))
    assert state["wall_box"].endswith("; a heavy strike has changed in the last 30 minutes")
    assert state["walls_since_30min"] == ("over the last 30 minutes the call-side heavy strike of today's same-day book moved up 0.07 sigma and the "
                                          "put-side strike stayed put, so the two are further apart")
    state, _, _ = labels(gamma_scene(scene_factory, then={"call_wall_gamma": 7715.0, "put_wall_gamma": 7690.0}))
    assert state["walls_since_30min"].endswith("moved down 0.07 sigma and the put-side strike moved up 0.07 sigma, so the two are closer together")
    state, _, _ = labels(gamma_scene(scene_factory, then={"call_wall_gamma": 7700.0, "put_wall_gamma": 7685.0}))
    assert state["walls_since_30min"].endswith("moved up 0.13 sigma and the put-side strike moved up 0.13 sigma, so the pair moved up together, as far apart as before")
    state, _, _ = labels(gamma_scene(scene_factory))
    assert state["walls_since_30min"] == ("over the last 30 minutes neither heavy strike of today's same-day book has moved: the call-side and the "
                                          "put-side strikes both stayed put")


def test_wall_box_sees_a_heavy_strike_that_moved_and_came_back_inside_the_half_hour(scene_factory):
    scene = gamma_scene(scene_factory)
    anchor, then, now = scene.rows_today[0], scene.rows_today[1], scene.row
    moved = labeller_row(diary_row(NOW - timedelta(minutes=15), call_wall_gamma=7715.0))
    state, _, _ = labels(replace(scene, rows_today=[anchor, then, moved, now]))
    assert state["wall_box"].endswith("; a heavy strike changed within the last 30 minutes and is back where it was")
    assert state["walls_since_30min"].startswith("over the last 30 minutes neither heavy strike of today's same-day book has moved")
    before = labeller_row(diary_row(NOW - timedelta(minutes=45), call_wall_gamma=7715.0))
    unread = labeller_row(diary_row(NOW - timedelta(minutes=15), call_wall_gamma=None))
    state, _, _ = labels(replace(scene, rows_today=[anchor, before, then, unread, now]))
    assert state["wall_box"].endswith("; both have stood at the same strikes for the last 30 minutes")


def test_the_walls_ignore_a_row_too_far_from_30_minutes_ago(scene_factory):
    scene = gamma_scene(scene_factory)
    stale = labeller_row(diary_row(NOW - timedelta(minutes=30 + ROW_SLACK_MIN + 1)))
    _, omitted, _ = labels(replace(scene, rows_today=[scene.rows_today[0], stale, scene.row]))
    assert omitted["gex.wall_box"] == omitted["gex.walls_since_30min"] == \
        "no diary row from 30 minutes ago carries the heaviest call-side and put-side strikes"


# ---- gex.delta_weight_side (reworded to the set's sentence)

def test_delta_weight_side_at_the_even_band_edges(scene_factory):
    scene = gamma_scene(scene_factory)

    def sentence(above: float) -> str:
        return labels(replace(scene, row={**scene.row, "dex_views": {"dex_above_spot": above}}))[0]["delta_weight_side"]

    assert sentence(0.37) == f"63% of the directional exposure across the 0-to-7-day books, {DEALERS}, sits at strikes below price, past the 40%-60% even band"
    assert sentence(0.60) == f"60% of the directional exposure across the 0-to-7-day books, {DEALERS}, sits at strikes above price, past the 40%-60% even band"
    assert sentence(0.40) == (f"the directional exposure across the 0-to-7-day books, {DEALERS}, splits 40% above price and 60% below, inside the "
                              f"40%-60% even band")


# ---- the gates

def test_book_vs_pace_sleeps_before_11_30(scene_factory):
    assert labels(gamma_scene(scene_factory, now=at(11, 29, ss=59)))[2]["book_vs_pace"] == "before 11:30 ET"
    assert labels(gamma_scene(scene_factory, now=at(11, 30)))[2]["book_vs_pace"] is None


def test_settle_pull_side_sleeps_while_price_is_seated_on_the_magnet(scene_factory):
    gates = labels(gamma_scene(scene_factory, magnet=7705.0))[2]
    assert gates["settle_pull_side"] == "price sits 0.07 sigma from today's heaviest same-day strike, inside the 0.1 sigma seat distance"
    assert labels(gamma_scene(scene_factory, magnet=7700.0 - MAGNET_SEAT_SIGMA * SIGMA))[2]["settle_pull_side"] is None
    assert labels(gamma_scene(scene_factory, magnet=None))[2]["settle_pull_side"] == "no heaviest same-day strike or no sigma ruler to place it"
