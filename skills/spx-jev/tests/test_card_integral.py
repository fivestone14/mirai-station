"""The phone card's calls carry their grade on the average price over the window (integral_grades.jsonl): the label,
the grade in points against its edge, the verdict with the call's direction deciding and an unsure call passed, the
size and the path beside it, and the strength tier once the box has ten sessions to rank it against. The end-price
grade rides beside it as ``end_price`` for the side-by-side weeks."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from conftest import DAY, at
from spx_jev import integral, service
from spx_jev.grade import INTEGRAL_NAME
from spx_jev.lane import LIVE, TAPE
from test_integral import SCENARIOS, _bars, _scenario

EDGE = 3.19                                                         # the 30-minute box's edge on the owner's scenarios


def _line(ts: str, pick: str, g: float, edge: float = EDGE, horizon: str = "next_30", **over) -> dict:
    """An integral_grades.jsonl line as grade.integral_run writes it, its label, verdict and margin the module's own."""
    lab = integral.label(g, edge)
    return {"row_ts": ts, "horizon": horizon, "rule_version": integral.RULE_VERSION, "graded": True, "minutes": 30, "g": g,
            "edge": edge, "label": lab, "pick": pick, "direction": integral.direction(pick), "verdict": integral.verdict(pick, lab),
            "margin": integral.margin(pick, g, edge), "running": [lab] * 30, "best": {"points": g, "minute": 30},
            "worst": {"points": 0.0, "minute": 1}, "filled": [], "bad_ticks": [], "stale_read": False, **over}


def _write(out: Path, day: str, picks: dict[str, str], lines: list[dict], lane=LIVE, graded: dict[str, dict] | None = None) -> None:
    """The day's sum records (``picks`` by read), the end-price grades (``graded`` by read) and the integral lines."""
    (out / "hour").mkdir(parents=True, exist_ok=True)
    with open(out / "hour" / f"{day}.jsonl", "a", encoding="utf-8") as f:
        for ts, pick in picks.items():
            f.write(json.dumps({"row_ts": ts, "pick": pick, "probabilities": {pick: 0.5, "flat": 0.3, "unsure": 0.2}}) + "\n")
    with open(out / "grades.jsonl", "a", encoding="utf-8") as f:
        for ts, res in (graded or {}).items():
            f.write(json.dumps({"row_ts": ts, "horizons": [lane.primary], lane.primary: res}) + "\n")
    with open(out / INTEGRAL_NAME, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _sessions(n: int, before: str = DAY, headrooms=(0.1, 0.2, 0.5, 1.5), wrong: int = 1, **over) -> list[dict]:
    """``n`` prior weekdays, newest first, each with a right up call clearing the edge by each of ``headrooms`` edges
    and ``wrong`` wrong ones, at 11:02."""
    d, out = date.fromisoformat(before), []
    while len(out) < (len(headrooms) + wrong) * n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            ts = at(11, 2, day=d.isoformat()).isoformat()
            out += [_line(ts, "up", round((1 + h) * EDGE, 4), **over) for h in headrooms] + [_line(ts, "down", 5.0, **over)] * wrong
    return out


def test_a_call_carries_its_average_price_grade_and_its_end_price_beside_it(tmp_path):
    """The owner's S9 on the opening lane: down_small, the average -4.52 against 1.59, the end price down big. The
    call is right on the average, its direction deciding; the size is a line beside it, and the end price wrong on
    the size is kept as ``end_price``, never deciding."""
    ts = at(10, 30).isoformat()
    s9 = {"row_ts": ts, "horizon": "next_10", "rule_version": integral.RULE_VERSION, **_scenario("S9"), "end_label": "down",
          "size": {"band": "down_big", "size": "big", "call": "small", "right": False}}
    _write(tmp_path, DAY, {ts: "down_small"}, [s9], TAPE, {ts: {"band": "down_big", "hit": False, "realized_points": -10.15}})
    [call] = service.day_calls(tmp_path, DAY, TAPE)
    g = call["integral"]
    assert (g["label"], g["g"], g["edge"], g["verdict"]) == ("down", -4.52, 1.59, "right")
    assert g["size"] == {"band": "down_big", "size": "big", "call": "small", "right": False}
    assert g["best"]["minute"] == 1 and g["worst"] == {"points": -10.15, "minute": 10} and len(g["running"]) == 10
    assert g["sharp_move"]["note"].startswith("too little history") and "tier" not in g
    assert call["end_price"] == {"outcome": "down_big", "hit": False, "moved": {"realized_points": -10.15}, "pick": "down_small", "p": 0.5}
    assert "outcome" not in call and "hit" not in call                # one grade decides; the end price is only kept
    # the grader's working stays in its file
    assert not {"f", "factor", "from", "filled", "bad_ticks", "pick", "direction", "row_ts", "rule_version"} & set(g)
    assert service.calls_block([call])["tally"] == {"calls": 1, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 0}
    assert "end_price_only" not in call and call["integral"]["stale_read"] is False


def test_an_unsure_call_is_passed_with_its_lean_and_a_window_still_open_carries_no_grade(tmp_path):
    ts, later = at(9, 55).isoformat(), at(10, 5).isoformat()
    s4 = {"row_ts": ts, "horizon": "next_10", "rule_version": integral.RULE_VERSION, **_scenario("S4")}
    _write(tmp_path, DAY, {ts: "unsure", later: "flat"}, [s4], TAPE, {ts: {"band": "down_small", "hit": False}})
    first, second = service.day_calls(tmp_path, DAY, TAPE)
    assert first["integral"]["verdict"] == "passed" and first["integral"]["lean"] == {"direction": "flat", "p": 0.45}
    assert "integral" not in second and "end_price" not in second     # open: nothing graded yet
    assert service.calls_block([first, second])["tally"] == {"calls": 2, "graded": 1, "right": 0, "passed": 1, "end_price_only": 0, "closed": 0}


def test_a_call_with_no_average_price_grade_stands_on_its_end_price_until_a_later_card_finds_one(tmp_path):
    """The average-price grade failed, or the window missed bars: the call is not left waiting for ever. It is marked
    end price only and counted on its end price, an unsure one passed; the next card to read the file, once the
    line is written, carries the average-price grade instead."""
    first, second, third = at(10, 2).isoformat(), at(10, 32).isoformat(), at(11, 2).isoformat()
    _write(tmp_path, DAY, {first: "up", second: "unsure", third: "down"},
           [_line(third, "down", 4.0, graded=False, reason="not graded: bars missing")],
           graded={first: {"band": "up", "hit": True}, second: {"band": "down", "hit": False}, third: {"band": "down", "hit": True}})
    calls = service.day_calls(tmp_path, DAY, LIVE)
    assert [c.get("end_price_only") for c in calls] == [True, True, True]
    assert [service.call_verdict(c) for c in calls] == ["right", "passed", "right"]
    tally = service.calls_block(calls)["tally"]
    assert tally == {"calls": 3, "graded": 3, "right": 2, "passed": 1, "end_price_only": 3, "closed": 0}
    assert service.tally_words(tally) == "2 of 2 calls right · 1 passed · 3 on the end price only"
    _write(tmp_path, DAY, {}, [_line(first, "up", -4.0)])                    # the late line: down on the average
    again = service.day_calls(tmp_path, DAY, LIVE)
    assert "end_price_only" not in again[0] and service.call_verdict(again[0]) == "wrong"
    assert service.calls_block(again)["tally"] == {"calls": 3, "graded": 3, "right": 1, "passed": 1, "end_price_only": 2, "closed": 0}


def test_a_call_whose_end_price_sums_got_no_answer_is_listed_graded_and_tallied_on_its_average(tmp_path):
    """On 09-28 JEV timed out on single requests: when only the end-price one fails, the phone shows the average-price
    call. It is a call in its own right, listed, graded on the average price over its window and counted, saying why
    it has no end price; one whose window can never be graded is closed for good, not still to grade."""
    graded_ts, closed_ts, open_ts = at(10, 2).isoformat(), at(15, 32).isoformat(), at(11, 2).isoformat()
    (tmp_path / "hour").mkdir()
    error = "JEV unreachable for group hour after 3 tries: TimeoutError: The read operation timed out"
    with open(tmp_path / "hour" / f"{DAY}.jsonl", "w", encoding="utf-8") as f:
        for ts in (graded_ts, open_ts, closed_ts):
            f.write(json.dumps({"row_ts": ts, "error": error, "average": {
                "pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}, "primary": "average_30", "box": "next_30"}}) + "\n")
    _write(tmp_path, DAY, {}, [_line(graded_ts, "up", 4.0, end_price="no end-price answer"),
                               {"row_ts": closed_ts, "horizon": "next_30", "rule_version": integral.RULE_VERSION, "graded": False,
                                "reason": "not graded: ends past the close"}])
    first, second, third = service.day_calls(tmp_path, DAY, LIVE)
    assert (first["pick"], first["sum"], first["p"], service.call_verdict(first)) == ("up", "average_30", 0.6, "right")
    assert first["end_price_missing"] == error and "end_price" not in first and "end_price_only" not in first
    assert "integral" not in second and "closed" not in second                        # its window is open
    assert third["closed"] == "not graded: ends past the close" and service.call_verdict(third) is None
    tally = service.calls_block([first, second, third])["tally"]
    assert tally == {"calls": 3, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 1}
    assert service.still_to_grade(tally) == 1


def test_an_open_call_carries_its_average_so_far_against_its_whole_windows_edge(tmp_path):
    """Five minutes into S9's ten: the average of the five closes so far against the read, set against the edge
    the whole window is graded on, and the minute it stands at. Before a minute has finished there is none, and a
    graded call carries its grade, never a partial one."""
    hhmm, spot, f, pick, closes = SCENARIOS["S9"]
    t0 = at(10, 30)
    ts, later = t0.isoformat(), at(10, 35).isoformat()
    (tmp_path / "hour").mkdir()
    (tmp_path / "hour" / f"{DAY}.jsonl").write_text("".join(json.dumps(
        {"row_ts": r, "spot": spot, "band": {"flat_points": f}, "pick": pick, "probabilities": {pick: 0.5}}) + "\n" for r in (ts, later)))
    scene = SimpleNamespace(bars=_bars(t0, spot, closes[:5]), rows_today=[], market=None)
    first, second = service.day_calls(tmp_path, DAY, TAPE, scene)
    x = [c - spot for c in closes[:5]]
    assert first["so_far"] == {"g": round(sum(x) / 5, 2), "edge": 1.59, "label": "down", "minutes": 5, "of": 10,
                               "as_of": at(10, 35).isoformat()}
    assert "so_far" not in second                                            # its first minute has not finished
    assert "so_far" not in service.day_calls(tmp_path, DAY, TAPE)[0]         # a card built without bars says nothing so far


def test_only_this_box_and_this_rule_version_reach_the_card(tmp_path):
    ts = at(11, 2).isoformat()
    _write(tmp_path, DAY, {ts: "up"}, [_line(ts, "up", 9.0, horizon="next_60"), _line(ts, "up", -9.0, rule_version=0),
                                        _line(ts, "up", 4.0), _line(ts, "up", -4.0)])
    [call] = service.day_calls(tmp_path, DAY, LIVE)
    assert (call["integral"]["g"], call["integral"]["verdict"]) == (4.0, "right")      # the first line of the read is the one


def test_no_tier_until_the_box_has_ten_sessions_and_ten_right_calls(tmp_path):
    ts = at(11, 2).isoformat()
    _write(tmp_path, DAY, {ts: "down"}, _sessions(9) + [_line(ts, "down", -20.0)])
    assert service.day_calls(tmp_path, DAY, LIVE)[0]["integral"]["verdict"] == "right"
    assert "tier" not in service.day_calls(tmp_path, DAY, LIVE)[0]["integral"]
    # a tenth session whose only read was stale is not one to rank against
    tenth = at(11, 2, day="2026-09-04").isoformat()
    _write(tmp_path, DAY, {}, [_line(tenth, "up", 9.0, stale_read=True)])
    assert "tier" not in service.day_calls(tmp_path, DAY, LIVE)[0]["integral"]


def test_ten_sessions_holding_fewer_than_ten_right_calls_rank_nothing(tmp_path):
    ts = at(11, 2).isoformat()
    # twelve sessions: nine with a right call, the three before them only wrong ones
    thin = _sessions(9, headrooms=(0.5,)) + _sessions(3, before="2026-09-07", headrooms=())
    _write(tmp_path, DAY, {ts: "down"}, thin + [_line(ts, "down", -20.0)])
    assert "tier" not in service.day_calls(tmp_path, DAY, LIVE)[0]["integral"]
    _write(tmp_path, DAY, {}, [_line(at(11, 32, day="2026-09-17").isoformat(), "up", 1.5 * EDGE)])     # a tenth right call
    assert service.day_calls(tmp_path, DAY, LIVE)[0]["integral"]["tier"] == "Strong right"


def test_with_ten_sessions_a_right_call_is_ranked_among_the_boxs_right_calls_and_a_wrong_one_is_wrong(tmp_path):
    """Twenty prior sessions of right calls clearing the edge by 0.1, 0.2, 0.5 and 1.5 edges, and ten wrong ones each,
    which are never ranked against. S9's call, 1.84 edges past its edge, beats them all: Strong right. A flat call a
    tenth of an edge inside beats none: Weak right. An up call 0.3 past beats half: Right. Wrong stays Wrong; a pass
    has no tier. Nothing else moves them: a 21st session back, the 60-minute box's lines and lines under an older
    rule, each full of calls far past the edge that would put S9 in the middle were they read."""
    reads = {at(10, 2).isoformat(): ("down_small", -4.52 / 1.59 * EDGE), at(10, 32).isoformat(): ("flat", 0.9 * EDGE),
             at(11, 2).isoformat(): ("up", 1.3 * EDGE), at(11, 32).isoformat(): ("up", -5.0), at(12, 2).isoformat(): ("unsure", 5.0)}
    far = (40.0,) * 60
    history = (_sessions(20, wrong=10) + _sessions(1, before="2026-08-21", headrooms=far) + _sessions(20, headrooms=far, horizon="next_60")
               + _sessions(20, headrooms=far, rule_version=0))
    _write(tmp_path, DAY, {ts: p for ts, (p, _) in reads.items()}, history + [_line(ts, p, round(g, 4)) for ts, (p, g) in reads.items()])
    calls = service.day_calls(tmp_path, DAY, LIVE)
    assert [c["integral"]["verdict"] for c in calls] == ["right", "right", "right", "wrong", "passed"]
    assert [c["integral"].get("tier") for c in calls] == ["Strong right", "Weak right", "Right", "Wrong", None]
    assert service.tally_words(service.calls_block(calls)["tally"]) == "3 of 4 calls right · 1 passed"


def test_a_right_calls_headroom_puts_a_flat_call_and_a_sided_one_on_one_scale():
    """A sided call turns right where its margin passes 1 and a flat call where its margin passes 0: each is ranked
    by how far past that line it went, so a flat call dead on the read's price clears it by a whole edge, as an up
    call at twice the edge does."""
    assert integral.headroom(_line("t", "flat", 0.0)) == 1.0 == integral.headroom(_line("t", "up", 2 * EDGE))
    assert integral.headroom(_line("t", "down_big", -EDGE * 1.5)) == 0.5 and integral.headroom(_line("t", "up", -EDGE)) == -2.0
    assert integral.headroom(_line("t", "unsure", 9.0)) is None
    base = [[0.1, 0.9, 2.0]] * 10
    assert integral.strength(_line("t", "up", 9.0), base[:9]) is None           # nine sessions: label only
    assert integral.strength(_line("t", "up", -9.0), base) == "Wrong" and integral.strength(_line("t", "unsure", 9.0), base) is None
    assert integral.strength(_line("t", "up", 1.05 * EDGE), [[]] * 10) is None   # ten sessions with no right call: nothing to rank on
    assert integral.strength(_line("t", "up", 9.0), [[0.5]] * 9 + [[]] * 3) is None    # twelve sessions, nine right calls
    assert integral.strength(_line("t", "up", 9.0), [[0.5]] * 10) == "Strong right"
