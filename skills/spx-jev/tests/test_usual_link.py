"""The usual link of a market beside the index: its multiple from the prior sessions, a symbol's own usual
move at this minute, and prices read point in time."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import DAY, at
from spx_jev.cuts import MIN_RANK_SESSIONS, SAME_CLOCK_MIN_SESSIONS
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.labels.usual_link import SPX, AgainstIndex, OwnMoves, Session, link_windows, same_clock_sessions, usual_link
from spx_jev.state_builder import MarketContext
from usual_link_fixtures import DRIFT_STEP, NOW, SIGMA, index_closes, scene_with


def test_the_usual_multiple_comes_back_from_the_prior_sessions():
    scene = scene_with(index_closes(), {}, {"SMH": 1.6, "XLU": -0.4})
    smh, xlu = usual_link(scene, "SMH"), usual_link(scene, "XLU")
    assert smh.multiple == pytest.approx(1.6, abs=0.01) and smh.sessions == 10
    assert xlu.multiple == pytest.approx(-0.4, abs=0.01)
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


def test_a_symbols_own_move_is_ranked_against_its_own_at_this_minute(monkeypatch):
    """Prior day k drifts (k + 1) steps a minute past its multiple of the index's 30-minute fall, so its move is larger
    by day: a move between day k's and day k + 1's is above the bottom third of its own from beating 4 of the 10."""
    import usual_link_fixtures
    monkeypatch.setattr(usual_link_fixtures, "prior_drift", lambda k: -(k + 1) * DRIFT_STEP)
    scene = scene_with(index_closes(), {}, {"SMH": 1.6})
    own = OwnMoves(scene, ["SMH"], 30)
    sizes = sorted(own.sizes["SMH"])
    assert sizes == own.sizes["SMH"][::-1]                               # newest first, and the newest prior day drifted most
    between = (sizes[3] + sizes[4]) / 2
    assert own.rank("SMH", -between)[0].higher_than == 4 and own.above_bottom_third("SMH", -between) is True
    assert own.above_bottom_third("SMH", (sizes[2] + sizes[3]) / 2) is False     # beats 3 of 10: the bottom third, either way
    thin = replace(scene, prior_markets=dict(list(scene.prior_markets.items())[1:]))
    assert OwnMoves(thin, ["SMH"], 30).rank("SMH", between) == (
        None, f"its rank needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with a 30-minute move of SMH at this minute, have 9")
    assert OwnMoves(thin, ["SMH"], 30).above_bottom_third("SMH", between) is None


def test_a_measure_in_no_ruler_keeps_the_sessions_a_sigma_measure_leaves_out():
    """The newest prior day's ruler is estimated and the next one's diary is not on file: a move in SPX sigma
    (AgainstIndex.same_clock) leaves both out; a return, a count or a link (same_clock_sessions) keeps every session,
    newest first, each read at this minute."""
    scene = scene_with(index_closes(), {}, {"SMH": 1.6})
    days = list(scene.prior_bars)
    rulers = {**scene.prior_rulers, days[0]: SigmaRuler(SIGMA, "vix")}
    del rulers[days[1]]
    scene = replace(scene, prior_rulers=rulers)
    half = timedelta(minutes=30)
    in_sigma = AgainstIndex(scene, SigmaRuler(SIGMA, "anchor")).same_clock(lambda s, then, share: s.move("SMH", then - half, then) / share)
    returns = same_clock_sessions(scene, lambda s, then: s.move("SMH", then - half, then))
    assert len(in_sigma) == len(days) - 2 and len(returns) == len(days)
    assert same_clock_sessions(scene, lambda s, then: s.price(SPX, then)) == [
        Session(scene.prior_bars[d], None).price(SPX, at(NOW.hour, NOW.minute, d)) for d in days]


def _bar(start, close):
    return {"ts": start.isoformat(), "open": close, "high": close, "low": close, "close": close, "volume": 0.0}


def test_both_legs_of_a_move_are_priced_at_one_instant():
    """09-28 09:40: SMH's 09:30:05 quote was set against SPX's 09:31 bar close, so the window's start read the two at
    different moments and SMH's move beyond its link doubled. Live, every symbol and the index come from the newest
    snapshot that quoted the index; a saved day reads every symbol at its bar close, as SPX's."""
    spx_bars = [_bar(at(9, 30), 7710.0), _bar(at(9, 31), 7715.0), _bar(at(9, 32), 7720.0)]
    first, second = at(9, 30, ss=5), at(9, 31, ss=16)
    live = MarketContext({SPX: [(first, 7700.0), (second, 7707.7)], "SMH": [(first, 605.83), (second, 605.83 * 1.001)],
                          "$TICK": [(at(9, 31), 13.0)]}, {"$TICK": [(at(9, 31), _bar(at(9, 30), 13.0))]})
    s = Session(spx_bars, live)
    assert s.price(SPX, at(9, 31)) == 7700.0 and s.price("SMH", at(9, 31)) == 605.83
    assert s.move(SPX, at(9, 31), at(9, 32)) == pytest.approx(0.001) and s.move("SMH", at(9, 31), at(9, 32)) == pytest.approx(0.001)
    assert s.price("XLK", at(9, 32)) is None and s.price(SPX, at(9, 37)) is None          # not quoted then; a stopped feed
    saved = MarketContext({"SMH": [(first, 605.83), (at(9, 31), 604.705), (second, 606.435), (at(9, 32), 605.3)]},
                          {"SMH": [(at(9, 31), _bar(at(9, 30), 604.705)), (at(9, 32), _bar(at(9, 31), 605.3))]})
    s = Session(spx_bars, saved)
    assert s.price("SMH", at(9, 31, ss=30)) == 604.705 and s.price(SPX, at(9, 31, ss=30)) == 7710.0
    assert s.move("SMH", at(9, 31), at(9, 32)) == pytest.approx(605.3 / 604.705 - 1)
