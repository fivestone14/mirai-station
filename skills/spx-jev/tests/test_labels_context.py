"""The context family's last strong move: the newest minute today whose 10-minute move ranks in the top third of the
same 10 minutes on the prior sessions (ranks.rank_sessions), each session in its own ruler and an estimated one left out."""
from __future__ import annotations

from dataclasses import replace

from conftest import at, bars_from_closes, make_row
from spx_jev.labels.context import build_context_labels
from spx_jev.labels.rulers import SigmaRuler

SIGMA = 75.0
PRIOR_DAYS = [f"2026-09-{d:02d}" for d in range(17, 7, -1)]


def _prior(k: int) -> list[float]:
    """Prior session k (1 to 10) climbing 0.15k points a minute all day: every 10-minute move is 0.02k sigma, so a
    10-minute move of 0.02k sigma or less beats k - 1 of them."""
    return [7700.0 + 0.15 * k * i for i in range(390)]


def _read(scene_factory, closes, now, days=PRIOR_DAYS, estimated=()):
    """A read on bars closing at ``closes`` from 09:30, on a trusted morning anchor, with the prior sessions of _prior."""
    scene = scene_factory(now, bars_from_closes(closes), rows_before=[make_row(at(9, 31), closes[0])])
    prior = {d: bars_from_closes(_prior(k), day=d) for k, d in enumerate(days, start=1)}
    rulers = {d: SigmaRuler(SIGMA, "vix" if d in estimated else "anchor") for d in days}
    return replace(scene, prior_bars=prior, prior_rulers=rulers)


def _label(scene):
    ls = build_context_labels(scene)
    return ls.state.get("context", {}).get("time_since_last_move"), ls.omitted.get("context.time_since_last_move")


def _stepped(points: float) -> list[float]:
    """Today flat at 7700 for the first hour, then ``points`` higher from the 10:30 bar on: the 10-minute moves to
    10:30 through 10:39 are each ``points``, every other one nothing."""
    return [7700.0] * 60 + [7700.0 + points] * 30


def test_the_last_strong_move_is_the_newest_minute_in_the_top_third_of_its_minute(scene_factory):
    text, why = _label(_read(scene_factory, _stepped(11.25), at(11, 0, ss=5)))
    assert why is None
    assert text == ("price last made a strong 10-minute move for its minute 20 minutes ago, between 10 and 60 minutes ago: "
                    "0.15 sigma, larger than 7 of the last 10 sessions at that minute, top third")


def test_a_move_short_of_the_top_third_is_no_strong_move(scene_factory):
    text, _ = _label(_read(scene_factory, _stepped(9.0), at(11, 0, ss=5)))
    assert text == ("price has made no strong 10-minute move for its minute today: "
                    "none in the top third of the same 10 minutes on the prior sessions")


def test_each_prior_session_is_sized_in_its_own_ruler(scene_factory):
    scene = _read(scene_factory, _stepped(12.75), at(11, 0, ss=5))
    assert "0.17 sigma, larger than 8 of the last 10 sessions" in _label(scene)[0]
    # the newest session's 1.5-point 10-minute moves on a 5-point ruler are 0.3 sigma, now above today's 0.17
    scene = replace(scene, prior_rulers={**scene.prior_rulers, PRIOR_DAYS[0]: SigmaRuler(5.0, "anchor")})
    assert "0.17 sigma, larger than 7 of the last 10 sessions" in _label(scene)[0]


def test_the_move_needs_ten_sessions_and_an_estimated_session_sits_out(scene_factory):
    text, why = _label(_read(scene_factory, _stepped(11.25), at(11, 0, ss=5), estimated=PRIOR_DAYS[:1]))
    assert text is None
    assert why == "its rank needs 10 prior sessions with a 10-minute move at that minute, have 9"
