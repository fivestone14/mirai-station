"""The usual link of a market beside the index: its multiple and scale from the prior sessions, and prices
read point in time."""
from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import DAY, at
from spx_jev.cuts import MIN_RANK_SESSIONS
from spx_jev.labels.usual_link import SPX, Session, link_windows, usual_link
from spx_jev.state_builder import MarketContext
from usual_link_fixtures import NOW, index_closes, scene_with


def test_the_usual_multiple_and_scale_come_back_from_the_prior_sessions():
    scene = scene_with(index_closes(), {}, {"SMH": 1.6, "XLU": -0.4})
    smh, xlu = usual_link(scene, "SMH"), usual_link(scene, "XLU")
    assert smh.multiple == pytest.approx(1.6, abs=0.01) and smh.scale == pytest.approx(1.6, abs=0.02) and smh.sessions == 10
    assert xlu.multiple == pytest.approx(-0.4, abs=0.01) and xlu.scale == pytest.approx(0.4, abs=0.02)
    assert smh.spread > 0                                        # each prior day drifts its own way
    assert len(link_windows(DAY)) == 12 and link_windows(DAY)[0] == (at(10, 0), at(10, 30))


def test_no_usual_multiple_under_the_session_floor():
    scene = scene_with(index_closes(), {}, {"SMH": 1.6})
    kept = dict(list(scene.prior_markets.items())[:MIN_RANK_SESSIONS - 1])
    assert usual_link(replace(scene, prior_markets=kept), "SMH") is None
    assert usual_link(scene, "XLK") is None                      # never on file


def test_a_price_is_read_as_it_was_known_and_a_stopped_feed_counts_as_none():
    scene = scene_with(index_closes(), {"SMH": [100.0 + k for k in range(390)]}, {"SMH": 1.6})
    today = Session(scene.bars, scene.market)
    assert today.price("SMH", at(9, 31)) == 100.0 and today.price("SMH", at(9, 31, ss=59)) == 100.0
    assert today.price("SMH", at(9, 30, ss=59)) is None          # the first bar has not finished
    assert today.price(SPX, NOW) == float(scene.bars[-1]["close"])
    stopped = Session(scene.bars, MarketContext({"SMH": [(at(12, 20), 101.0)]}))
    assert stopped.price("SMH", at(12, 25)) == 101.0 and stopped.price("SMH", at(12, 26)) is None
    assert stopped.move("SMH", at(12, 20), at(12, 25)) == 0.0 and stopped.move("SMH", at(12, 0), at(12, 25)) is None
