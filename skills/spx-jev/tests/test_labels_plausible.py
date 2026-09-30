"""The breadth series check (labels/plausible.py): a series that does not read like its own history over the same
minutes is taken out before any label reads it, today and on a prior session, and every label that needs it names
why. The failing series are shaped as Schwab served them on 2026-09-28: $UVOL and $DVOL in shares where the saved
sessions had thousands, $TRIN divided by them, and a $TICK floored at zero."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from conftest import at, flat_bars
from spx_jev.labels.plausible import SAME_DAY_WRONG, checked, left_out
from spx_jev.labels.registry import build_labels
from spx_jev.state_builder import MarketContext

PRIOR_DAYS = [(datetime(2026, 9, 17) - timedelta(days=k)).date().isoformat() for k in range(20)]
NOW = at(10, 30)
MINUTES = 60


def minute_ends(day: str, n: int = 390) -> list[datetime]:
    return [at(9, 31, day=day) + timedelta(minutes=i) for i in range(n)]


def usual(day: str, k: int, n: int = 390) -> dict[str, list[tuple[datetime, float]]]:
    """A saved session's breadth: $TICK swinging either side of zero, $TRIN near 1 to two decimals, and $UVOL and
    $DVOL the day's volume so far in thousands of shares."""
    ends = minute_ends(day, n)
    return {"$TICK": [(t, float((i * 37 + k * 11) % 900 - 450)) for i, t in enumerate(ends)],
            "$TRIN": [(t, round(0.8 + ((i + k) % 9) * 0.05, 2)) for i, t in enumerate(ends)],
            "$UVOL": [(t, 15000.0 + 700.0 * i + 50.0 * k) for i, t in enumerate(ends)],
            "$DVOL": [(t, 16000.0 + 650.0 * i + 40.0 * k) for i, t in enumerate(ends)]}


def served_2026_09_28(day: str, n: int) -> dict[str, list[tuple[datetime, float]]]:
    """The series as Schwab served them on 2026-09-28."""
    ends = minute_ends(day, n)
    return {"$TICK": [(t, 0.0 if i % 3 else 140.0) for i, t in enumerate(ends)],
            "$TRIN": [(t, 0.08 + 0.001 * i) for i, t in enumerate(ends)],
            "$UVOL": [(t, 1.5e9 + 2e6 * i) for i, t in enumerate(ends)],
            "$DVOL": [(t, 4e8 + 5e6 * i) for i, t in enumerate(ends)]}


def scene_with(scene_factory, today: dict, priors: dict[str, dict]):
    scene = scene_factory(NOW, flat_bars(MINUTES), market=MarketContext(today))
    return replace(scene, prior_markets={d: MarketContext(m) for d, m in priors.items()})


def test_a_series_far_off_its_own_history_is_taken_out_and_each_label_needing_it_says_why(scene_factory, breadth_served_right):
    scene = scene_with(scene_factory, served_2026_09_28("2026-09-18", MINUTES), {d: usual(d, k) for k, d in enumerate(PRIOR_DAYS)})
    ls = build_labels(scene)
    tick, up = ls.omitted["breadth.opening_tick"], ls.omitted["breadth.day_upvol_share"]
    assert tick.startswith("NYSE TICK ($TICK) read 0 on 40 of its 60 minutes, where the last 20 sessions repeated one reading on a median ")
    assert tick.endswith(": a stuck series")
    assert up.startswith("NYSE up volume ($UVOL) reads ") and up.endswith(" its usual size at these minutes on the last 20 sessions: "
                                                                          "not the series they had")
    assert ls.omitted["breadth.volume_vs_count_30m"].startswith(
        "NYSE TRIN ($TRIN) is Schwab's advancers over decliners divided by $UVOL over $DVOL, and NYSE up volume ($UVOL) reads ")
    for path in ("breadth.tick_lean", "breadth.tick_side_vs_usual", "breadth.tick_extreme_5m"):
        assert ls.omitted[path] == tick
    assert ls.gates["tick_extreme_follow"] is None


def test_the_saved_sessions_pass_and_the_labels_read_them(scene_factory, breadth_served_right):
    scene = scene_with(scene_factory, usual("2026-09-18", 3, MINUTES), {d: usual(d, k) for k, d in enumerate(PRIOR_DAYS)})
    got = checked(scene)
    assert left_out(got.market, "$TICK") is None and got.market.known == scene.market.known
    assert all(m is scene.prior_markets[d] for d, m in got.prior_markets.items())
    assert "tick_lean" in build_labels(scene).state["breadth"]


def test_a_prior_session_that_fails_sits_out_so_the_next_day_is_still_judged_against_the_usual(scene_factory, breadth_served_right):
    priors = {d: usual(d, k) for k, d in enumerate(PRIOR_DAYS)}
    priors[PRIOR_DAYS[0]] = served_2026_09_28(PRIOR_DAYS[0], 390)
    got = checked(scene_with(scene_factory, served_2026_09_28("2026-09-18", MINUTES), priors))
    bad = got.prior_markets[PRIOR_DAYS[0]]
    assert "$TICK" not in bad.known and "$UVOL" not in bad.bars and "$TRIN" not in bad.known
    assert left_out(got.market, "$TICK").startswith("NYSE TICK ($TICK) read 0 on 40 of its 60 minutes, where the last 19 sessions ")
    assert "$TICK" in got.prior_markets[PRIOR_DAYS[1]].known


def test_too_few_prior_sessions_to_judge_leaves_the_series_in(scene_factory, breadth_served_right):
    priors = {d: usual(d, k) for k, d in enumerate(PRIOR_DAYS[:9])}
    got = checked(scene_with(scene_factory, served_2026_09_28("2026-09-18", MINUTES), priors))
    assert left_out(got.market, "$UVOL") is None and "$UVOL" in got.market.known


def test_todays_nyse_breadth_is_never_read_however_usual_it_looks(scene_factory):
    """Schwab serves a session's NYSE breadth wrong until after it (2026-09-28 was put right by that night, 2026-09-29
    was not), and the check above only catches it once enough of the morning has gone: today's is left out at every
    read, with or without prior sessions, each label that reads it saying why. The prior sessions, saved again once
    put right, are still read, and today's $ADD, taken from $ADVN and $DECN, is judged like any series."""
    today = usual("2026-09-18", 3, MINUTES)
    today["$ADD"] = [(t, 300.0) for t in minute_ends("2026-09-18", MINUTES)]
    for priors in ({d: usual(d, k) for k, d in enumerate(PRIOR_DAYS)}, {}):
        scene = scene_with(scene_factory, today, priors)
        got = checked(scene)
        assert set(SAME_DAY_WRONG) == {"$TICK", "$TRIN", "$UVOL", "$DVOL", "$VOLD", "$VOLSPD"}
        assert all(s not in got.market.known for s in SAME_DAY_WRONG) and got.market.known["$ADD"] == today["$ADD"]
        assert left_out(got.market, "$TICK") == ("NYSE TICK ($TICK) is served wrong during its own session and put right only "
                                                 "after it, so today's is never read")
        assert all(m.known == scene.prior_markets[d].known for d, m in got.prior_markets.items())
        ls = build_labels(scene)
        for path in ("breadth.tick_lean", "breadth.opening_tick", "breadth.tick_extreme_5m"):
            assert ls.omitted[path] == left_out(got.market, "$TICK")
        assert ls.omitted["breadth.volume_vs_count_30m"] == left_out(got.market, "$TRIN")
        assert "tick_lean" not in ls.state.get("breadth", {})
