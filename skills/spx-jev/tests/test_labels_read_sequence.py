"""The read-sequence family (labels/read_sequence.py), offline: the day's move at the 30-minute lane's last
reads (extending, stalled, unwinding) and the rising-stock volume share beside it (fading under the move,
building behind it, tracking it), their sleep reasons, and that every earlier read is taken at its scheduled
minute from the bars, whenever this read was stamped.

The morning anchor is SIGMA points (a 09:31 diary row), so ten points are 0.10 sigma. Every day settles its
open at OPEN and moves along straight lines through the points of its ``path`` (a read minute -> points from
the settled open at that minute). The ten prior sessions each stand 20 points up at 11:02 and have given back
0% to 90% of it by 11:32, and their rising-stock volume share shifts one to ten points from 10:32 to 11:32."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest

from conftest import DAY, ET, at, bars_from_closes, make_row
from spx_jev.labels.read_sequence import build_read_sequence_labels
from spx_jev.state_builder import MarketContext

SIGMA = 100.0
OPEN = 7700.0
PRIOR_DAYS = [f"2026-09-{d:02d}" for d in range(17, 7, -1)]
MAX_WORDS = 45


def _minute(c: time) -> int:
    return (c.hour - 9) * 60 + c.minute - 30


def closes(path: dict[time, float], until: time = time(16)) -> list[float]:
    """One close a minute from 09:30: OPEN through the 09:34 bar (the settled open), then straight lines through
    ``path``, each point the close of the bar that finished at its minute, and flat after the last."""
    pts = [(4, 0.0)] + sorted((_minute(c) - 1, p) for c, p in path.items())
    out = [OPEN] * 5
    for i in range(5, _minute(until)):
        seg = next(((a, b) for a, b in zip(pts, pts[1:]) if a[0] <= i <= b[0]), None)
        out.append(OPEN + (pts[-1][1] if seg is None else seg[0][1] + (seg[1][1] - seg[0][1]) * (i - seg[0][0]) / (seg[1][0] - seg[0][0])))
    return out


def shares_market(day: str, shares: dict[time, float]) -> MarketContext:
    """$UVOL and $DVOL running totals whose rising-stock share is ``shares[c]`` at each clock ``c``."""
    known = {"$UVOL": [], "$DVOL": []}
    for c, s in sorted(shares.items()):
        t = datetime.combine(date.fromisoformat(day), c, tzinfo=ET)
        known["$UVOL"].append((t, 1e9 * s))
        known["$DVOL"].append((t, 1e9 * (1 - s)))
    return MarketContext(known)


def read(scene_factory, path: dict[time, float], now: datetime = at(11, 32), shares: dict[time, float] | None = None,
         prior_days: int = 10):
    keep = PRIOR_DAYS[:prior_days]
    prior_bars = {d: bars_from_closes(closes({time(10, 2): 5.0, time(11, 2): 20.0, time(11, 32): 20.0 * (1 - 0.1 * k)}), day=d)
                  for k, d in enumerate(keep)}
    today = closes(path)
    sc = scene_factory(now, bars_from_closes(today), row_over={"sigma": SIGMA, "sigma_anchor": SIGMA},
                       rows_before=[make_row(at(9, 31), OPEN, sigma=SIGMA)], prior_bars=prior_bars,
                       spot=today[_minute(now.time()) - 1], market=shares_market(DAY, shares) if shares else None)
    sc.prior_markets = {d: shares_market(d, {time(10, 32): 0.6, time(11, 2): 0.6, time(11, 32): 0.6 + 0.01 * (k + 1)})
                        for k, d in enumerate(keep)}
    return build_read_sequence_labels(sc)


def said(ls, path: str) -> str:
    group, key = path.split(".", 1)
    return ls.state[group][key]


UP = {time(10, 2): 10.0, time(10, 32): 20.0, time(11, 2): 40.0}


# ---- seq.day_move_by_read and seq_day_move_stage ----------------------------------------------------------

@pytest.mark.parametrize("now_points, stage, words", [
    (50.0, "extending", "this read is its furthest from the open: extending"),
    (30.0, "stalled", "25% of its furthest, at 11:02, has been given back, bottom third of the last 10 sessions for these reads: stalled"),
    (10.0, "unwinding", "75% of its furthest, at 11:02, has been given back, top third of the last 10 sessions for these reads: unwinding"),
])
def test_the_days_move_at_the_last_reads_extending_stalled_or_unwinding(scene_factory, now_points, stage, words):
    ls = read(scene_factory, {**UP, time(11, 32): now_points})
    text = said(ls, "seq.day_move_by_read")
    assert text.startswith(f"SPX is {now_points / SIGMA:.2f} sigma above the settled open; at the last 4 reads it stood +0.10 (10:02), "
                           f"+0.20 (10:32), +0.40 (11:02) and {now_points / SIGMA:+.2f} (11:32); ")
    assert text.endswith(words) and ls.gates["seq_day_move_stage"] is None


def test_a_day_below_the_open_measures_its_giveback_on_its_own_side(scene_factory):
    ls = read(scene_factory, {time(10, 2): -10.0, time(10, 32): -20.0, time(11, 2): -40.0, time(11, 32): -10.0})
    assert said(ls, "seq.day_move_by_read").startswith("SPX is 0.10 sigma below the settled open")
    assert said(ls, "seq.day_move_by_read").endswith(": unwinding")


def test_a_day_inside_the_move_rule_or_with_too_few_ranked_sessions_sleeps(scene_factory):
    flat = read(scene_factory, {**UP, time(11, 32): 5.0})
    assert flat.gates["seq_day_move_stage"] == "the day's move from the settled open is within the 0.09 sigma move rule"
    assert not said(flat, "seq.day_move_by_read").endswith(("extending", "stalled", "unwinding"))
    thin = read(scene_factory, {**UP, time(11, 32): 30.0}, prior_days=3)
    assert thin.gates["seq_day_move_stage"] == "only 3 prior sessions measured at these read minutes"


def test_fewer_than_two_earlier_reads_leave_the_sequence_out(scene_factory):
    ls = read(scene_factory, {time(10, 2): 10.0, time(10, 32): 20.0}, now=at(10, 32))
    assert ls.omitted == {p: "needs 2 earlier 30-minute reads today from 10:02, have 1" for p in ("seq.day_move_by_read", "seq.breadth_by_read")}
    assert all(ls.gates.values())


def test_the_earlier_reads_are_their_scheduled_minutes_whenever_this_read_was_stamped(scene_factory):
    ls = read(scene_factory, {**UP, time(11, 32): 30.0}, now=at(11, 30, ss=40))
    assert "; at the last 4 reads it stood +0.10 (10:02), +0.20 (10:32), +0.40 (11:02) and +0.31 (11:30); " in said(ls, "seq.day_move_by_read")


# ---- seq.breadth_by_read and seq_breadth_drift ------------------------------------------------------------

@pytest.mark.parametrize("path_now, shares, drift", [
    (50.0, (0.7, 0.6, 0.5), "fading_under_move"),
    (50.0, (0.5, 0.6, 0.7), "building_behind_move"),
    (50.0, (0.6, 0.6, 0.61), "tracking"),
    (10.0, (0.7, 0.6, 0.5), "tracking"),                      # the move shrank below its 10:32 value: breadth followed it
])
def test_breadth_across_the_last_reads_against_the_days_side(scene_factory, path_now, shares, drift):
    ls = read(scene_factory, {**UP, time(11, 32): path_now}, shares=dict(zip((time(10, 32), time(11, 2), time(11, 32)), shares)))
    text = said(ls, "seq.breadth_by_read")
    assert text.startswith(f"SPX is {path_now / SIGMA:.2f} sigma above the settled open, at 10:32, 11:02 and 11:32 +0.20, +0.40 and "
                           f"{path_now / SIGMA:+.2f}, while the day's rising-stock volume share went ")
    assert text.endswith({"fading_under_move": "fading under the move", "building_behind_move": "building behind the move",
                          "tracking": "tracking the move"}[drift])
    assert ls.gates["seq_breadth_drift"] is None


def test_the_breadth_shift_is_worded_and_ranked(scene_factory):
    ls = read(scene_factory, {**UP, time(11, 32): 50.0}, shares={time(10, 32): 0.7, time(11, 2): 0.6, time(11, 32): 0.5})
    assert said(ls, "seq.breadth_by_read").endswith("went 70%, 60% and 50%: 20 points away from the day's side, top third for these "
                                                     "reads: fading under the move")


def test_breadth_is_left_out_without_the_days_up_and_down_volume(scene_factory):
    ls = read(scene_factory, {**UP, time(11, 32): 50.0})
    assert ls.omitted["seq.breadth_by_read"].startswith("no NYSE up and down volume at every listed read")
    assert ls.gates["seq_breadth_drift"] == ls.omitted["seq.breadth_by_read"]


def test_a_session_read_accounts_for_both_labels(full_scene):
    ls = build_read_sequence_labels(full_scene)
    assert ls.paths() == {"seq.day_move_by_read", "seq.breadth_by_read"} and set(ls.gates) == {"seq_day_move_stage", "seq_breadth_drift"}


@pytest.mark.parametrize("path_now", [50.0, 10.0, -45.0])
def test_every_sentence_stays_short(scene_factory, path_now):
    ls = read(scene_factory, {**UP, time(11, 32): path_now}, shares={time(10, 32): 0.7, time(11, 2): 0.6, time(11, 32): 0.5})
    assert not ls.omitted
    assert max(len(s.split()) for labels in ls.state.values() for s in labels.values()) <= MAX_WORDS
