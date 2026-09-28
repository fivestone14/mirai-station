"""The macro family: bonds, oil and the pooled outside markets against their usual multiple of the index,
ranked by the clock, with how tightly bonds moved with stocks this hour ranked the same way, point in time; the
flows no feed carries."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from conftest import at, bars_from_closes
from spx_jev.cuts import MIN_RANK_SESSIONS, SAME_CLOCK_MIN_SESSIONS
from spx_jev.labels import macro
from spx_jev.labels.macro import REBALANCE_SIDE_UNMEASURED, bond_market_closed, build_macro_labels, minute_link
from spx_jev.labels.registry import build_labels
from spx_jev.labels.usual_link import Session
from spx_jev.state_builder import MarketContext
import usual_link_fixtures
from usual_link_fixtures import DRIFT_STEP, NOW, READ_MINUTE, follow, index_closes, scene_with, shape

JUMP = 175
MULTIPLES = {"TLT": -0.3, "/ZN": -0.2, "HYG": 0.3, "USO": 0.5, "GLD": 0.1, "/6E": 0.1}
# TLT wiggles k steps of its own on prior day k, so its minute-by-minute link to the index loosens day by day and
# today's ranks with a spread; LOOSE is today's wiggle for a link looser than all but one of them.
LINK_STEP = 0.0001
LOOSE = 0.0008


def read(jumps: dict[str, dict[int, float]] | None = None, drop: tuple[str, ...] = (), loose: bool = False, move_points: float = 10.5):
    closes = index_closes(move_points)
    today = {s: follow(closes, m, jumps=(jumps or {}).get(s), wiggle=LOOSE if loose and s == "TLT" else 0.0)
             for s, m in MULTIPLES.items() if s not in drop}
    return scene_with(closes, today, MULTIPLES, wiggle_steps={"TLT": LINK_STEP})


def labels(scene):
    ls = build_macro_labels(scene)
    return {f"{g}.{k}": s for g, v in ls.state.items() for k, s in v.items()}, ls.omitted


def test_without_the_market_context_every_measured_label_is_omitted():
    _, omitted = labels(replace(read(), market=None))
    assert omitted == {"xasset.bond_gap_30min": "no market-context snapshot today", "xasset.macro_gap_30min": "no market-context snapshot today",
                       "xasset.oil_gap_30min": "no market-context snapshot today", "flows.rebalance_side": REBALANCE_SIDE_UNMEASURED}


def test_the_flows_no_feed_carries_are_omitted_with_why(full_scene):
    omitted = build_labels(full_scene).omitted
    assert omitted["flows.rebalance_side"] == REBALANCE_SIDE_UNMEASURED
    assert omitted["flows.etf_creations"] == "dark: no intraday ETF creation feed"
    assert omitted["close.moc_imbalance"].startswith("dark: no closing-auction imbalance feed")


# ---- bonds

BOND = ("over the last # minutes long Treasury prices (TLT) {moved} than the index's move usually brings them, {fifth} for this half "
        "hour, higher than # of the last # sessions at this minute; this hour bond prices and stocks moved in opposite directions "
        "minute by minute (link #, by size higher than # of the last # sessions at this minute, {third} third: tight enough to read): "
        "bond prices up has gone with stocks down; {verdict}")


@pytest.mark.parametrize("jump, moved, fifth, third, verdict", [
    (0.002, "rose more", "in the top fifth", "middle", "so bonds are ahead in the stocks-down direction; the Treasury cash market is open"),
    (-0.002, "fell more", "in the bottom fifth", "middle", "so bonds are ahead in the stocks-up direction; the Treasury cash market is open"),
    (0.0, "rose more", "between the top and bottom fifths", "top", "bonds are in line with the index; the Treasury cash market is open"),
])
def test_bonds_ahead_of_the_index_either_way_or_in_line(jump, moved, fifth, third, verdict):
    """A link above the bottom third of the same hour's on the prior sessions is tight enough to say which way the gap points."""
    got, _ = labels(read(jumps={"TLT": {JUMP: jump}}))
    s = got["xasset.bond_gap_30min"]
    assert shape(s) == BOND.format(moved=moved, fifth=fifth, third=third, verdict=verdict)


@pytest.mark.parametrize("jump, fifth", [(0.002, "in the top fifth"), (0.0, "between the top and bottom fifths")])
def test_bonds_on_a_loose_link_say_it_is_too_loose(jump, fifth):
    """A link in the bottom third is too loose to say, whether or not the move reached a fifth: bond_catchup's in_line needs a
    tight link."""
    scene = read(jumps={"TLT": {JUMP: jump}}, loose=True)
    link = minute_link(Session(scene.bars, scene.market), "TLT", NOW)
    assert -0.45 < link < -0.35                                  # moved together more than not, yet looser than 9 of the 10 prior hours
    got, _ = labels(scene)
    s = got["xasset.bond_gap_30min"]
    assert f"{fifth} for this half hour" in s and "by size higher than 1 of the last 10 sessions at this minute, bottom third: loose)" in s
    assert s.endswith("; the link is too loose to say which way that points; the Treasury cash market is open")


def test_the_bond_link_rank_needs_ten_prior_hours():
    """A prior session missing TLT's minutes before the read has no link to rank against: with 9 left the link is not
    ranked and the label falls to the ten-year future."""
    scene = read(drop=("/ZN",))
    oldest = list(scene.prior_markets)[-1]
    gapped = {s: [(t, v) for t, v in pts if s != "TLT" or not at(11, 35, oldest) < t < at(12, 0, oldest)]
              for s, pts in scene.prior_markets[oldest].known.items()}
    _, omitted = labels(replace(scene, prior_markets={**scene.prior_markets, oldest: MarketContext(gapped)}))
    assert omitted["xasset.bond_gap_30min"] == (
        f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with a minute-by-minute link of TLT and $SPX at this minute, "
        f"have {SAME_CLOCK_MIN_SESSIONS - 1}; or needs a price for /ZN now and 30 minutes ago")


def test_bonds_on_a_bond_market_holiday(monkeypatch):
    monkeypatch.setattr(macro, "bond_market_closed", lambda day: True)
    got, _ = labels(read(jumps={"TLT": {JUMP: 0.002}}))
    assert got["xasset.bond_gap_30min"].endswith(
        "the Treasury cash market is closed today for a bond-market holiday, so the link is too loose to read")


@pytest.mark.parametrize("day, closed", [
    ("2026-10-12", True), ("2026-10-05", False), ("2026-10-19", False),       # Columbus Day is the second Monday of October
    ("2026-11-11", True), ("2026-11-10", False),                               # Veterans Day on a Wednesday
    ("2028-11-10", True), ("2029-11-12", True), ("2026-09-18", False),         # kept on the Friday or the Monday
])
def test_the_bond_market_holidays(day, closed):
    assert bond_market_closed(date.fromisoformat(day)) is closed


def test_bonds_fall_back_to_the_ten_year_future_then_say_why_neither():
    got, _ = labels(read(drop=("TLT",)))
    assert got["xasset.bond_gap_30min"].startswith("over the last 30 minutes ten-year Treasury futures (/ZN)")
    _, omitted = labels(read(drop=("TLT", "/ZN")))
    assert omitted["xasset.bond_gap_30min"] == ("needs a price for TLT now and 30 minutes ago; "
                                                "or needs a price for /ZN now and 30 minutes ago")


def test_bonds_need_an_hour_of_minutes_to_read_the_link():
    scene = read(drop=("/ZN",))
    thin = {s: [(t, v) for t, v in pts if s != "TLT" or not at(11, 25) < t < at(12, 2)] for s, pts in scene.market.known.items()}
    _, omitted = labels(replace(scene, market=MarketContext(thin)))
    assert omitted["xasset.bond_gap_30min"] == "needs 45 of the last 60 minutes with TLT and $SPX moving; " \
                                               "or needs a price for /ZN now and 30 minutes ago"


def test_the_first_read_measures_the_link_over_the_session_so_far():
    """At 10:02 the hour reaches back before the first finished minute (09:31): the link runs over the 31 minutes
    since, on today and on each prior day alike, and needs the same three quarters of them."""
    closes = index_closes()
    scene = scene_with(closes, {s: follow(closes, m) for s, m in MULTIPLES.items() if s != "/ZN"}, MULTIPLES, now=at(10, 2, ss=10))
    got, _ = labels(scene)
    assert set(got) == {"xasset.bond_gap_30min", "xasset.oil_gap_30min", "xasset.macro_gap_30min"}
    assert "higher than 5 of the last 10 sessions at this minute; since 09:31 bond prices and stocks moved" in got["xasset.bond_gap_30min"]
    assert "each signed by how it moved with stocks since 09:31," in got["xasset.macro_gap_30min"]
    thin = {s: [(t, v) for t, v in pts if s != "TLT" or not at(9, 40) < t < at(9, 52)] for s, pts in scene.market.known.items()}
    _, omitted = labels(replace(scene, market=MarketContext(thin)))
    assert omitted["xasset.bond_gap_30min"] == ("needs 24 of the last 31 minutes with TLT and $SPX moving; "
                                                "or needs a price for /ZN now and 30 minutes ago")


# ---- oil

@pytest.mark.parametrize("jump, verdict", [
    (0.002, "rose more than the index's move usually brings it, oil ran up: in the top fifth"),
    (-0.002, "fell more than the index's move usually brings it, oil ran down: in the bottom fifth"),
    (0.00005, "rose more than the index's move usually brings it, an ordinary amount given the index: between the top and bottom fifths"),
])
def test_oil_against_its_usual_multiple(jump, verdict):
    got, _ = labels(read(jumps={"USO": {JUMP: jump}}))
    assert shape(got["xasset.oil_gap_30min"]) == (f"over the last # minutes crude oil (USO) {verdict} for this half hour, "
                                                 "higher than # of the last # sessions at this minute")


def test_oil_above_its_multiple_but_below_the_usual_for_this_minute(monkeypatch):
    """Every prior session ran further past its multiple at this minute, so a small rise ranks in the bottom fifth:
    the sentence says why the rise still reads as oil running down."""
    monkeypatch.setattr(usual_link_fixtures, "prior_drift", lambda k: (k + 1) * DRIFT_STEP)
    closes = index_closes(10.5)
    got, _ = labels(scene_with(closes, {s: follow(closes, m, drift=0.2 * DRIFT_STEP) for s, m in MULTIPLES.items()}, MULTIPLES))
    assert got["xasset.oil_gap_30min"] == (
        "over the last 30 minutes crude oil (USO) rose more than the index's move usually brings it, below the usual for this minute, "
        "oil ran down: in the bottom fifth for this half hour, higher than 0 of the last 10 sessions at this minute")
    assert "(TLT) rose more than the index's move usually brings them, below the usual for this minute, in the bottom fifth" in got[
        "xasset.bond_gap_30min"]


def test_oil_needs_its_history_and_a_fresh_price():
    scene = read()
    nine = replace(scene, prior_markets=dict(list(scene.prior_markets.items())[1:]))
    assert labels(nine)[1]["xasset.oil_gap_30min"] == (
        f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with USO and $SPX at this minute, have {SAME_CLOCK_MIN_SESSIONS - 1}")
    no_history = replace(scene, prior_markets={d: MarketContext({s: v for s, v in m.known.items() if s != "USO"})
                                               for d, m in scene.prior_markets.items()})
    assert labels(no_history)[1]["xasset.oil_gap_30min"] == (
        f"needs {MIN_RANK_SESSIONS} prior sessions of half hours with USO and $SPX to know the usual multiple")
    stopped = {s: [(t, v) for t, v in pts if s != "USO" or t <= at(12, 25)] for s, pts in scene.market.known.items()}
    assert labels(replace(scene, market=MarketContext(stopped)))[1]["xasset.oil_gap_30min"] == (
        "needs a price for USO now and 30 minutes ago")


# ---- the outside markets together

def test_the_complex_ahead_in_the_stocks_up_direction():
    stocks_up = {"TLT": -0.002, "HYG": 0.002, "USO": 0.002, "GLD": 0.002, "/6E": 0.002}
    got, _ = labels(read(jumps={s: {JUMP: j} for s, j in stocks_up.items()}))
    s = got["xasset.macro_gap_30min"]
    assert shape(s) == ("over the last # minutes high-yield credit, oil, gold and the euro ran above and bonds ran below what the index's "
                        "# sigma rise would match: taken together, each signed by how it moved with stocks this hour, the complex is "
                        "ahead of the index in the stocks-up direction, in the top fifth for this half hour, higher than # of the last # "
                        "sessions at this minute")
    assert "higher than 10 of the last 10" in s


def test_the_complex_in_line_and_ahead_downward():
    got, _ = labels(read())
    assert "the complex is in line with the index, between the top and bottom fifths" in got["xasset.macro_gap_30min"]
    got, _ = labels(read(jumps={"HYG": {JUMP: -0.004}, "USO": {JUMP: -0.004}}))
    assert "the complex is ahead of the index in the stocks-down direction, in the bottom fifth" in got["xasset.macro_gap_30min"]


def test_the_complex_needs_every_outside_market():
    _, omitted = labels(read(drop=("GLD",)))
    assert omitted["xasset.macro_gap_30min"] == "needs a price for GLD now and 30 minutes ago"


def test_an_estimated_ruler_says_so():
    scene = read(jumps={"TLT": {JUMP: 0.002}, "USO": {JUMP: 0.002}})
    got, _ = labels(replace(scene, rows_today=scene.rows_today[1:]))    # no row by 09:40: the earliest live sigma stands in
    assert set(got) == {"xasset.bond_gap_30min", "xasset.oil_gap_30min", "xasset.macro_gap_30min"}
    assert all(s.endswith("; ruler estimated") for s in got.values())
    assert not any("ruler estimated" in s for s in labels(scene)[0].values())


def test_no_label_moves_on_values_known_after_the_read():
    scene = read(jumps={"TLT": {JUMP: 0.002}, "USO": {JUMP: -0.002}})
    later = {s: [(t, v * (1.5 if t > NOW else 1.0)) for t, v in pts] for s, pts in scene.market.known.items()}
    got, _ = labels(scene)
    assert set(got) == {"xasset.bond_gap_30min", "xasset.oil_gap_30min", "xasset.macro_gap_30min"}
    assert labels(replace(scene, market=MarketContext(later)))[0] == got
    jumped_after, _ = labels(read(jumps={"TLT": {JUMP: 0.002, READ_MINUTE: -0.05}, "USO": {JUMP: -0.002}}))
    assert jumped_after == got
    closes = index_closes(10.5)
    spx_after = [c + (40.0 if i >= READ_MINUTE else 0.0) for i, c in enumerate(closes)]
    assert labels(replace(scene, bars=bars_from_closes(spx_after)))[0] == got
