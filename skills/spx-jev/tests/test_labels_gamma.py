"""The gamma family's labels of the final set (gex.*) and its two sleep gates, on rows shaped like the SPX diary's:
each verdict and its boundary, the omissions, and point in time (a later bar or diary row never counts).

A distance, width or share the set judges is ranked against the same measure on the prior sessions' diary rows at
the read's minute (``history``): ten sessions whose k-th (0 the newest) sits its flip, charm wall and box width
0.1 (k + 1) sigma from price, its magnet 0.05 (k + 1), its books' shares above price 0.05 + 0.09 k, so a value
beating 3 of them is the bottom third, 4 to 6 the middle and 7 or more the top."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import at, bars_from_closes, flat_bars, make_row
from spx_jev.cuts import SAME_CLOCK_MIN_SESSIONS, SETTLE_SEAT_SIGMA
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


def labels(scene, history: dict | None = None):
    ls = build_gamma_labels(replace(scene, **history) if history else scene)
    return ls.state.get("gex", {}), ls.omitted, ls.gates


def write_diary(root, day: str, rows: list[dict]) -> None:
    (root / "reversion").mkdir(parents=True, exist_ok=True)
    (root / "reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


# the minutes the tests read at, a diary row a minute before each on every prior session
CLOCKS = ((11, 29), (12, 31), (14, 1), (15, 31))


def prior_book(k: int) -> dict:
    """The k-th prior session's book (0 the newest), its spot 7700."""
    share = 0.05 + 0.09 * k
    return {"flip": 7700.0 - 7.5 * (k + 1), "magnet": 7700.0 + 3.75 * (k + 1), "charm_wall": 7700.0 + 7.5 * (k + 1),
            "call_wall_gamma": 7700.0 + 7.5 * (k + 1), "put_wall_gamma": 7700.0, "pin_top_share": 0.067 - 0.001 * k,
            "gamma_above_spot": 1000.0 * share, "gamma_below_spot": 1000.0 * (1.0 - share),
            "net_by_strike_tenor": [[7650.0, -(1.0 - share)], [7700.0, 5.0], [7750.0, share]]}


def book_history(root, n: int = 10, rulers: dict | None = None) -> dict:
    """``n`` prior sessions, 2026-09-17 back: their diaries (a row at each of CLOCKS carrying prior_book), flat bars at
    7700 and trusted rulers, as the Scene's fields; ``rulers`` overrides some sessions' rulers."""
    days = [f"2026-09-{d:02d}" for d in range(17, 17 - n, -1)]
    for k, day in enumerate(days):
        write_diary(root, day, [make_row(at(h, m, day), 7700.0, SIGMA, gex_views=book(**prior_book(k)),
                                         dex_views={"dex_above_spot": 0.05 + 0.09 * k}) for h, m in CLOCKS])
    return {"state_dir": root, "prior_rulers": {**{d: SigmaRuler(SIGMA, "anchor") for d in days}, **(rulers or {})},
            "prior_bars": {d: flat_bars(390, day=d) for d in days}}


@pytest.fixture
def history(tmp_path) -> dict:
    return book_history(tmp_path)


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

def test_flip_distance_near_and_far_by_the_prior_sessions_third(scene_factory, history):
    state, _, _ = labels(gamma_scene(scene_factory), history)
    assert state["flip_distance"] == (f"price is 0.60 sigma above the gamma flip, the level where today's same-day book switches between "
                                      f"call-heavy and put-heavy gamma {DEALERS}; farther from price than on 5 of the last 10 sessions at "
                                      f"this minute, middle third: near the flip; it has not crossed the flip in the last 30 minutes")
    near = labels(gamma_scene(scene_factory, flip=7700.0 - 0.7 * SIGMA), history)[0]["flip_distance"]
    assert "; farther from price than on 6 of the last 10 sessions at this minute, middle third: near the flip;" in near
    far = labels(gamma_scene(scene_factory, flip=7700.0 - 0.71 * SIGMA), history)[0]["flip_distance"]
    assert "; farther from price than on 7 of the last 10 sessions at this minute, top third: far from the flip;" in far
    close = labels(gamma_scene(scene_factory, flip=7700.0 - 0.4 * SIGMA), history)[0]["flip_distance"]
    assert "; farther from price than on 3 of the last 10 sessions at this minute, bottom third: near the flip;" in close


def test_flip_distance_says_price_crossed_it_since_the_row_30_minutes_ago(scene_factory, history):
    state, _, _ = labels(gamma_scene(scene_factory, then={"flip": 7710.0}), history)
    assert state["flip_distance"].endswith("; it has crossed the flip in the last 30 minutes")


def test_flip_distance_with_no_flip_or_a_balanced_book_is_a_fact_not_a_gap(scene_factory):
    state, _, _ = labels(gamma_scene(scene_factory, flip=None))
    assert state["flip_distance"] == "no gamma flip was found in today's same-day book"
    state, _, _ = labels(gamma_scene(scene_factory, regime="uncertain"))
    assert state["flip_distance"] == "today's same-day book is too close to call at the current price, so no gamma flip stands out"


def test_flip_distance_is_omitted_without_the_row_30_minutes_ago(scene_factory, history):
    scene = gamma_scene(scene_factory)
    scene = replace(scene, rows_today=[scene.rows_today[0], scene.row])
    assert labels(scene, history)[1]["gex.flip_distance"] == "no diary row from 30 minutes ago carries a gamma flip"


def test_the_ranks_need_ten_trusted_prior_sessions_and_a_state_folder(scene_factory, tmp_path):
    scene = gamma_scene(scene_factory)
    assert labels(scene)[1]["gex.flip_distance"] == "no state folder to read the prior sessions' diaries from"
    few = book_history(tmp_path, SAME_CLOCK_MIN_SESSIONS - 1)
    assert labels(scene, few)[1]["gex.flip_distance"] == "its rank needs 10 prior sessions with a gamma flip at this minute, have 9"
    estimated = book_history(tmp_path, 11, rulers={"2026-09-17": SigmaRuler(SIGMA, "live"), "2026-09-16": SigmaRuler(SIGMA, "vix")})
    _, omitted, _ = labels(scene, estimated)
    assert omitted["gex.flip_distance"] == "its rank needs 10 prior sessions with a gamma flip at this minute, have 9"
    assert omitted["gex.wall_box"] == ("its rank needs 10 prior sessions with the heaviest call-side and put-side strikes at this "
                                       "minute, have 9")


def test_a_ruler_that_is_not_the_morning_anchor_is_flagged_and_no_ruler_omits(scene_factory, history):
    scene = gamma_scene(scene_factory)
    late = replace(scene, rows_today=scene.rows_today[1:])      # first row after 09:40: the earliest live sigma stands in
    assert labels(late, history)[0]["flip_distance"].endswith("in the last 30 minutes; ruler estimated")
    no_ruler = [labeller_row(make_row(NOW - timedelta(minutes=30, seconds=40), 7700.0, sigma_live=None, gex_views=book())),
                labeller_row(make_row(NOW, 7700.0, sigma_live=None, gex_views=book()))]
    _, omitted, _ = labels(replace(scene, rows_today=no_ruler, row=no_ruler[-1]), history)
    assert omitted["gex.flip_distance"] == NO_ANCHOR and omitted["gex.magnet_distance"] == NO_ANCHOR


# ---- gex.weight_both_books

def test_weight_both_books_on_the_same_side(scene_factory, history):
    state, _, _ = labels(gamma_scene(scene_factory), history)
    assert state["weight_both_books"] == ("today's same-day options gamma has 60% of it above price, a larger share than on 7 of the last 10 "
                                          "sessions at this minute, top third: more of it above price than usual; the 1-to-7-day book "
                                          "(today's expiry excluded) has 60% of it above price, a larger share than on 7 of the last 10 "
                                          "sessions at this minute, top third: also more of it above price than usual")
    state, _, _ = labels(gamma_scene(scene_factory, gamma_above_spot=300.0, gamma_below_spot=700.0,
                                     net_by_strike_tenor=[[7650.0, -6.9], [7700.0, 50.0], [7710.0, 3.1]]), history)
    assert state["weight_both_books"] == ("today's same-day options gamma has 30% of it above price, a larger share than on 3 of the last 10 "
                                          "sessions at this minute, bottom third: more of it below price than usual; the 1-to-7-day book "
                                          "(today's expiry excluded) has 31% of it above price, a larger share than on 3 of the last 10 "
                                          "sessions at this minute, bottom third: also more of it below price than usual")


def test_weight_both_books_at_the_third_edges_and_the_strike_at_price_on_neither_side(scene_factory, history):
    # the prior shares run 0.05 to 0.86 by 0.09: 0.33 beats four of them (the middle third), 0.31 three (the bottom)
    state, _, _ = labels(gamma_scene(scene_factory, gamma_above_spot=330.0, gamma_below_spot=670.0,
                                     net_by_strike_tenor=[[7650.0, -3.2], [7700.0, 50.0], [7750.0, 6.8]]), history)
    assert state["weight_both_books"] == ("today's same-day options gamma has 33% of it above price, a larger share than on 4 of the last 10 "
                                          "sessions at this minute, middle third: about as usual; the 1-to-7-day book (today's expiry "
                                          "excluded) has 68% of it above price, a larger share than on 7 of the last 10 sessions at this "
                                          "minute, top third: more of it above price than usual")
    state, _, _ = labels(gamma_scene(scene_factory, gamma_above_spot=310.0, gamma_below_spot=690.0), history)
    assert state["weight_both_books"].startswith("today's same-day options gamma has 31% of it above price, a larger share than on 3 of the "
                                                 "last 10 sessions at this minute, bottom third: more of it below price than usual;")


def test_weight_both_books_never_names_a_side_of_price_its_share_contradicts(scene_factory, tmp_path):
    # prior shares of 0.02 to 0.38: 0.48 beats all ten, the top third, with most of the gamma still below price
    history = book_history(tmp_path)
    for k, day in enumerate(sorted(history["prior_bars"], reverse=True)):
        low = 0.02 + 0.04 * k
        write_diary(tmp_path, day, [make_row(at(h, m, day), 7700.0, SIGMA, gex_views=book(**{
            **prior_book(k), "gamma_above_spot": 1000.0 * low, "gamma_below_spot": 1000.0 * (1.0 - low),
            "net_by_strike_tenor": [[7650.0, -(1.0 - low)], [7700.0, 5.0], [7750.0, low]]})) for h, m in CLOCKS])
    state, _, _ = labels(gamma_scene(scene_factory, gamma_above_spot=480.0, gamma_below_spot=520.0), history)
    assert state["weight_both_books"].startswith("today's same-day options gamma has 48% of it above price, a larger share than on 10 of "
                                                 "the last 10 sessions at this minute, top third: more of it above price than usual;")


def test_weight_both_books_is_omitted_without_the_week_book(scene_factory, history):
    _, omitted, _ = labels(gamma_scene(scene_factory, net_by_strike_tenor=None), history)
    assert omitted["gex.weight_both_books"] == "row carries no gamma by strike for the 1-to-7-day book"


# ---- gex.magnet_distance (the prior magnets sit 0.05 to 0.50 sigma from price, and so do their half hours' closes)

def magnet_scene(scene_factory, closes=None, **over):
    return gamma_scene(scene_factory, bars=bars_from_closes(closes if closes is not None else [7700.0] * 182), **over)


def test_magnet_distance_held_on_the_magnet(scene_factory, history):
    state, _, _ = labels(magnet_scene(scene_factory), history)
    assert state["magnet_distance"] == ("today's heaviest same-day strike (the magnet) sits 0.07 sigma above price, farther from price than "
                                        "on 1 of the last 10 sessions at this minute, bottom third: seated on it; its grip (top-strike share) "
                                        "is stronger than on 10 of the last 10 sessions at 12:32 ET, at or above the median; it is the same "
                                        "strike as 30 minutes ago; over the last 30 minutes price sat 0.07 sigma from it on average, farther "
                                        "than on 1 of the last 10 sessions at this minute, bottom third: held there")


def test_magnet_distance_seated_near_and_away_by_the_prior_sessions_third(scene_factory, history):
    seat = labels(magnet_scene(scene_factory, magnet=7700.0 + 0.2 * SIGMA), history)[0]["magnet_distance"]
    assert "sits 0.20 sigma above price, farther from price than on 3 of the last 10 sessions at this minute, bottom third: seated on it;" in seat
    near = labels(magnet_scene(scene_factory, magnet=7700.0 - 0.21 * SIGMA), history)[0]["magnet_distance"]
    assert "sits 0.21 sigma below price, farther from price than on 4 of the last 10 sessions at this minute, middle third: near it;" in near
    away = labels(magnet_scene(scene_factory, magnet=7700.0 + 0.36 * SIGMA), history)[0]["magnet_distance"]
    assert "sits 0.36 sigma above price, farther from price than on 7 of the last 10 sessions at this minute, top third: away from it;" in away


def test_magnet_distance_hug_and_grip_median_boundaries(scene_factory, history):
    # the magnet is 7705: 30 closes averaging 0.20 sigma (15 points) from it beat three sessions' half hours, 0.21 four
    held = labels(magnet_scene(scene_factory, closes=[7700.0] * 152 + [7690.0] * 30), history)[0]["magnet_distance"]
    assert held.endswith("price sat 0.20 sigma from it on average, farther than on 3 of the last 10 sessions at this minute, bottom third: held there")
    loose = labels(magnet_scene(scene_factory, closes=[7700.0] * 152 + [7689.25] * 30), history)[0]["magnet_distance"]
    assert loose.endswith("price sat 0.21 sigma from it on average, farther than on 4 of the last 10 sessions at this minute, middle third: "
                          "not held there")
    # the prior grips run 0.058 to 0.067: beating 5 of 10 is the median exactly, 4 of 10 is below it
    state, _, _ = labels(magnet_scene(scene_factory, pin_top_share=0.0625), history)
    assert "stronger than on 5 of the last 10 sessions at 12:32 ET, at or above the median;" in state["magnet_distance"]
    state, _, _ = labels(magnet_scene(scene_factory, pin_top_share=0.0615), history)
    assert "stronger than on 4 of the last 10 sessions at 12:32 ET, below the median;" in state["magnet_distance"]


def test_magnet_distance_counts_only_finished_bars_and_prior_rows_by_the_same_minute(scene_factory, history):
    scene = magnet_scene(scene_factory, closes=[7700.0] * 152 + [7690.0] * 30)
    late = {"ts": NOW.replace(second=0).isoformat(), "open": 7700.0, "high": 7700.5, "low": 7699.5, "close": 7700.0}
    assert "price sat 0.20 sigma from it on average" in labels(replace(scene, bars=scene.bars + [late]), history)[0]["magnet_distance"]
    for d in range(8, 18):
        day = f"2026-09-{d:02d}"
        rows = [json.loads(line) for line in (history["state_dir"] / "reversion" / f"{day}.jsonl").read_text().splitlines()]
        write_diary(history["state_dir"], day, rows[:2] + [diary_row(at(12, 33, day), pin_top_share=0.5, magnet=7700.0)] + rows[2:])
    assert "stronger than on 10 of the last 10 sessions" in labels(scene, history)[0]["magnet_distance"]


def test_magnet_distance_is_omitted_without_its_history_or_its_half_hour(scene_factory, tmp_path, history):
    scene = magnet_scene(scene_factory)
    assert labels(scene)[1]["gex.magnet_distance"] == "no state folder to read the prior sessions' diaries from"
    no_bars = {**history, "prior_bars": {}}
    assert labels(scene, no_bars)[1]["gex.magnet_distance"] == ("its rank needs 10 prior sessions with a heaviest same-day strike and the "
                                                               "half hour's bars at this minute, have 0")
    _, omitted, _ = labels(replace(scene, bars=[]), history)
    assert omitted["gex.magnet_distance"] == "no minute bar finished in the last 30 minutes"


# ---- gex.settle_pull (the row's straddle left is 16.4 points; the prior magnets sit 0.23 to 2.29 straddles away)

def test_settle_pull_in_reach_with_the_centre_on_the_same_side(scene_factory, history):
    now = at(15, 32, ss=10)
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=7710.0, pin_centroid=7707.0), history)
    assert state["settle_pull"] == ("today's heaviest same-day strike sits 0.13 sigma above price, outside the 0.05 sigma seat distance; that is "
                                    "0.6 remaining straddles (what today's options still price before the 16:00 settle), more than on 2 of "
                                    "the last 10 sessions at this minute, bottom third: in reach; the gamma-weighted centre of the book sits "
                                    "0.09 sigma above price, outside the seat distance, the same side; the options settle in 28 minutes")


def test_settle_pull_out_of_reach_in_the_top_third_and_split_by_the_centre(scene_factory, history):
    now = at(14, 2, ss=10)
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=7700.0 - 1.5 * 16.4,
                                     pin_centroid=7700.0 + SETTLE_SEAT_SIGMA * SIGMA + 1.0), history)
    assert "that is 1.5 remaining straddles (what today's options still price before the 16:00 settle), more than on 6 of the last 10 sessions at this minute, middle third: in reach;" in state["settle_pull"]
    assert "the gamma-weighted centre of the book sits 0.06 sigma above price, outside the seat distance, the other side;" in state["settle_pull"]
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=7700.0 - 1.7 * 16.4), history)
    assert "that is 1.7 remaining straddles (what today's options still price before the 16:00 settle), more than on 7 of the last 10 sessions at this minute, top third: out of reach;" in state["settle_pull"]
    state, _, _ = labels(gamma_scene(scene_factory, now=now, magnet=7700.0 + SETTLE_SEAT_SIGMA * SIGMA, pin_centroid=None), history)
    assert state["settle_pull"].startswith("today's heaviest same-day strike sits 0.05 sigma above price, inside the 0.05 sigma seat distance;")
    assert "; no strike of the book pulls, so it has no gamma-weighted centre;" in state["settle_pull"]


def test_settle_pull_is_omitted_after_the_settle_and_without_the_straddle(scene_factory, history):
    _, omitted, _ = labels(gamma_scene(scene_factory, now=at(16, 0, ss=30)), history)
    assert omitted["gex.settle_pull"] == "today's same-day options have settled"
    scene = gamma_scene(scene_factory)
    scene = replace(scene, row={**scene.row, "range_ruler": {}})
    assert labels(scene, history)[1]["gex.settle_pull"] == "row carries no straddle left for today's same-day book"


# ---- gex.charm_wall_distance (the prior charm walls sit 0.1 to 1.0 sigma from price)

def test_charm_wall_distance_at_each_third(scene_factory, history):
    state, _, _ = labels(gamma_scene(scene_factory), history)
    assert state["charm_wall_distance"] == ("the charm wall of today's same-day book (the strike where its delta decay piles up) sits 0.20 sigma "
                                            "above price, farther from price than on 1 of the last 10 sessions at this minute, bottom third: "
                                            "at price, 0.9 remaining straddles away")
    state, _, _ = labels(gamma_scene(scene_factory, charm_wall=7700.0 - 0.45 * SIGMA), history)
    assert "sits 0.45 sigma below price, farther from price than on 4 of the last 10 sessions at this minute, middle third: near price," in state["charm_wall_distance"]
    state, _, _ = labels(gamma_scene(scene_factory, charm_wall=7700.0 + 0.75 * SIGMA), history)
    assert "sits 0.75 sigma above price, farther from price than on 7 of the last 10 sessions at this minute, top third: far from price," in state["charm_wall_distance"]


def test_charm_wall_distance_is_omitted_without_a_charm_wall(scene_factory, history):
    assert labels(gamma_scene(scene_factory, charm_wall=None), history)[1]["gex.charm_wall_distance"] == "row carries no charm wall for today's same-day book"


# ---- gex.wall_box and gex.walls_since_30min (the prior boxes are 0.1 to 1.0 sigma wide)

def test_wall_box_widths_by_the_prior_sessions_third(scene_factory, history):
    state, _, _ = labels(gamma_scene(scene_factory), history)
    assert state["wall_box"] == ("the heaviest call-side and put-side strikes of today's same-day book are 0.20 sigma apart, wider than on 1 of "
                                 "the last 10 sessions at this minute, bottom third: a tight box; both have stood at the same strikes for the "
                                 "last 30 minutes")
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7700.0 + 0.4 * SIGMA, put_wall_gamma=7700.0), history)
    assert "are 0.40 sigma apart, wider than on 3 of the last 10 sessions at this minute, bottom third: a tight box;" in state["wall_box"]
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7731.0, put_wall_gamma=7700.0), history)
    assert "are 0.41 sigma apart, wider than on 4 of the last 10 sessions at this minute, middle third: a middling box;" in state["wall_box"]
    state, _, _ = labels(gamma_scene(scene_factory, call_wall_gamma=7760.0, put_wall_gamma=7700.0), history)
    assert "are 0.80 sigma apart, wider than on 7 of the last 10 sessions at this minute, top third: a wide box;" in state["wall_box"]


def test_the_walls_against_the_row_30_minutes_ago(scene_factory, history):
    state, _, _ = labels(gamma_scene(scene_factory, then={"call_wall_gamma": 7705.0}), history)
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


def test_wall_box_sees_a_heavy_strike_that_moved_and_came_back_inside_the_half_hour(scene_factory, history):
    scene = gamma_scene(scene_factory)
    anchor, then, now = scene.rows_today[0], scene.rows_today[1], scene.row
    moved = labeller_row(diary_row(NOW - timedelta(minutes=15), call_wall_gamma=7715.0))
    state, _, _ = labels(replace(scene, rows_today=[anchor, then, moved, now]), history)
    assert state["wall_box"].endswith("; a heavy strike changed within the last 30 minutes and is back where it was")
    assert state["walls_since_30min"].startswith("over the last 30 minutes neither heavy strike of today's same-day book has moved")
    before = labeller_row(diary_row(NOW - timedelta(minutes=45), call_wall_gamma=7715.0))
    unread = labeller_row(diary_row(NOW - timedelta(minutes=15), call_wall_gamma=None))
    state, _, _ = labels(replace(scene, rows_today=[anchor, before, then, unread, now]), history)
    assert state["wall_box"].endswith("; both have stood at the same strikes for the last 30 minutes")


def test_the_walls_ignore_a_row_too_far_from_30_minutes_ago(scene_factory):
    scene = gamma_scene(scene_factory)
    stale = labeller_row(diary_row(NOW - timedelta(minutes=30 + ROW_SLACK_MIN + 1)))
    _, omitted, _ = labels(replace(scene, rows_today=[scene.rows_today[0], stale, scene.row]))
    assert omitted["gex.wall_box"] == omitted["gex.walls_since_30min"] == \
        "no diary row from 30 minutes ago carries the heaviest call-side and put-side strikes"


# ---- gex.delta_weight_side (the prior sessions' delta shares above price run 0.05 to 0.86 by 0.09)

def test_delta_weight_side_by_the_prior_sessions_third(scene_factory, history):
    scene = gamma_scene(scene_factory)

    def sentence(above: float) -> str:
        return labels(replace(scene, row={**scene.row, "dex_views": {"dex_above_spot": above}}), history)[0]["delta_weight_side"]

    exposure = f"of the directional exposure across the 0-to-7-day books, {DEALERS}, sits at strikes above price"
    assert sentence(0.31) == f"31% {exposure}, a larger share than on 3 of the last 10 sessions at this minute, bottom third: more of it below price than usual"
    assert sentence(0.33) == f"33% {exposure}, a larger share than on 4 of the last 10 sessions at this minute, middle third: about as usual"
    assert sentence(0.60) == f"60% {exposure}, a larger share than on 7 of the last 10 sessions at this minute, top third: more of it above price than usual"


# ---- the gates

def test_book_vs_pace_sleeps_before_11_30(scene_factory):
    assert labels(gamma_scene(scene_factory, now=at(11, 29, ss=59)))[2]["book_vs_pace"] == "before 11:30 ET"
    assert labels(gamma_scene(scene_factory, now=at(11, 30)))[2]["book_vs_pace"] is None


def test_settle_pull_side_sleeps_while_price_is_seated_on_the_magnet(scene_factory, history):
    gates = labels(gamma_scene(scene_factory, magnet=7705.0), history)[2]
    assert gates["settle_pull_side"] == ("price sits 0.07 sigma from today's heaviest same-day strike, farther from price than on 1 of the last "
                                         "10 sessions at this minute, bottom third: seated on it")
    assert labels(gamma_scene(scene_factory, magnet=7700.0 - 0.21 * SIGMA), history)[2]["settle_pull_side"] is None
    assert labels(gamma_scene(scene_factory, magnet=None), history)[2]["settle_pull_side"] == "no heaviest same-day strike or no sigma ruler to place it"
    assert labels(gamma_scene(scene_factory, magnet=7705.0))[2]["settle_pull_side"] == (
        "whether price is seated on today's heaviest same-day strike is not known: no state folder to read the prior sessions' diaries from")
