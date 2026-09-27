"""The macro family: bonds, oil and the pooled outside markets against their usual multiple of the index,
ranked by the clock, point in time; the flows no feed carries."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from conftest import at
from spx_jev.cuts import MIN_RANK_SESSIONS
from spx_jev.labels import macro
from spx_jev.labels.macro import REBALANCE_SIDE_UNMEASURED, bond_market_closed, build_macro_labels, minute_link
from spx_jev.labels.registry import build_labels
from spx_jev.labels.usual_link import Session
from spx_jev.state_builder import MarketContext
from usual_link_fixtures import NOW, READ_MINUTE, follow, index_closes, scene_with, shape

JUMP = 175
MULTIPLES = {"TLT": -0.3, "/ZN": -0.2, "HYG": 0.3, "USO": 0.5, "GLD": 0.1, "/6E": 0.1}
LOOSE = 0.0004                 # a wiggle of TLT's own, with no multiple of the index, for a loose link


def read(jumps: dict[str, dict[int, float]] | None = None, drop: tuple[str, ...] = (), loose: bool = False, move_points: float = 10.5):
    closes = index_closes(move_points)
    multiples = {**MULTIPLES, "TLT": 0.0} if loose else MULTIPLES
    wiggles = {"TLT": LOOSE} if loose else None
    today = {s: follow(closes, m, jumps=(jumps or {}).get(s), wiggle=(wiggles or {}).get(s, 0.0))
             for s, m in multiples.items() if s not in drop}
    return scene_with(closes, today, multiples, wiggles=wiggles)


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
        "minute by minute (link #, tight, at least the # tight line): bond prices up has gone with stocks down; {verdict}")


@pytest.mark.parametrize("jump, moved, fifth, verdict", [
    (0.002, "rose more", "in the top fifth", "so bonds are ahead in the stocks-down direction; the Treasury cash market is open"),
    (-0.002, "fell more", "in the bottom fifth", "so bonds are ahead in the stocks-up direction; the Treasury cash market is open"),
    (0.0, "rose more", "in neither the top nor the bottom fifth", "bonds are in line with the index; the Treasury cash market is open"),
])
def test_bonds_ahead_of_the_index_either_way_or_in_line(jump, moved, fifth, verdict):
    got, _ = labels(read(jumps={"TLT": {JUMP: jump}}))
    s = got["xasset.bond_gap_30min"]
    assert shape(s) == BOND.format(moved=moved, fifth=fifth, verdict=verdict)


@pytest.mark.parametrize("jump, fifth", [(0.002, "in the top fifth"), (0.0, "in neither the top nor the bottom fifth")])
def test_bonds_on_a_loose_link_say_it_is_too_loose(jump, fifth):
    """A loose link is too loose to say, whether or not the move reached a fifth: bond_catchup's in_line needs a tight link."""
    scene = read(jumps={"TLT": {JUMP: jump}}, loose=True)
    link = minute_link(Session(scene.bars, scene.market), "TLT", NOW)
    assert abs(link) < 0.25
    got, _ = labels(scene)
    s = got["xasset.bond_gap_30min"]
    assert f"{fifth} for this half hour" in s and "loose, under the 0.25 tight line" in s
    assert s.endswith("; the link is too loose to say which way that points; the Treasury cash market is open")


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


# ---- oil

@pytest.mark.parametrize("jump, verdict", [
    (0.002, "rose more than the index's move usually brings it, oil ran up: in the top fifth"),
    (-0.002, "fell more than the index's move usually brings it, oil ran down: in the bottom fifth"),
    (0.00005, "rose more than the index's move usually brings it, an ordinary amount given the index: in neither the top nor the bottom fifth"),
])
def test_oil_against_its_usual_multiple(jump, verdict):
    got, _ = labels(read(jumps={"USO": {JUMP: jump}}))
    assert shape(got["xasset.oil_gap_30min"]) == (f"over the last # minutes crude oil (USO) {verdict} for this half hour, "
                                                 "higher than # of the last # sessions at this minute")


def test_oil_needs_its_history_and_a_fresh_price():
    scene = read()
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
    assert "the complex is in line with the index, in neither the top nor the bottom fifth" in got["xasset.macro_gap_30min"]
    got, _ = labels(read(jumps={"HYG": {JUMP: -0.004}, "USO": {JUMP: -0.004}}))
    assert "the complex is ahead of the index in the stocks-down direction, in the bottom fifth" in got["xasset.macro_gap_30min"]


def test_the_complex_needs_every_outside_market():
    _, omitted = labels(read(drop=("GLD",)))
    assert omitted["xasset.macro_gap_30min"] == "needs a price for GLD now and 30 minutes ago"


def test_no_label_moves_on_values_known_after_the_read():
    scene = read(jumps={"TLT": {JUMP: 0.002}, "USO": {JUMP: -0.002}})
    later = {s: [(t, v * (1.5 if t > NOW else 1.0)) for t, v in pts] for s, pts in scene.market.known.items()}
    got, _ = labels(scene)
    assert set(got) == {"xasset.bond_gap_30min", "xasset.oil_gap_30min", "xasset.macro_gap_30min"}
    assert labels(replace(scene, market=MarketContext(later)))[0] == got
    jumped_after, _ = labels(read(jumps={"TLT": {JUMP: 0.002, READ_MINUTE: -0.05}, "USO": {JUMP: -0.002}}))
    assert jumped_after == got
