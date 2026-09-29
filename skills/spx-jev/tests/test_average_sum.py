"""The average-price question: JEV is asked where the average price over the window will sit against the read, up,
flat or down with no unsure and the flat edge in points, in a request of its own beside the end-price sums. The phone's
call is its answer, the average-price grade grades its pick and scores its probabilities, the time-of-day blend on it is
counted on the average price alone, and a record, a line or a card from before it still reads."""
from __future__ import annotations

import json
import math

import pytest

from conftest import DAY, at
from spx_jev import archive, grade, service
from spx_jev.ask import jev_only
from spx_jev.cuts import NEXT_30_FLAT_BAND_SIGMA
from spx_jev.hour import average_request, average_summary, average_window, hour_request, load_hour_doc
from spx_jev.lane import LANES, LIVE, PREMARKET, TAPE
from test_integral import SCENARIO_DAY, SCENARIOS, _bars
from test_old_sums_unchanged import AVERAGE, run_fixture

SENTENCES = {"q_dir": "Did price rise, fall, or go nowhere? rising, JEV was 85% sure"}


# ---- the question

@pytest.mark.parametrize("lane", LANES.values(), ids=list(LANES))
def test_every_lane_asks_the_average_with_three_options_and_no_unsure_and_keeps_unsure_on_its_end_price_sums(lane):
    doc = load_hour_doc(lane=lane)
    q = doc["questions"][lane.average]
    assert list(q["criteria"]) == ["up", "down", "flat"] and q["instructions"].endswith("Answer up, down or flat.")
    assert "unsure" not in json.dumps(jev_only(q)) and "average price" in q["ask"] and "`context.average`" in q["instructions"]
    assert all("unsure" in doc["questions"][h]["criteria"] for h in lane.horizons)      # the old sums are asked as they were
    assert lane.horizons[lane.primary][0] == int(q["ask"].split(" minutes")[0].split()[-1])   # it forecasts the primary's window


def test_the_end_price_request_leaves_the_average_out_and_the_average_request_gives_the_edge_in_points():
    """S1's box: a 5.39-point flat band narrows to the owner's 3.19 on the average over 30 minutes; S9's 2.56 to 1.59
    over 10. JEV is told that edge in points, and in one plain sentence what the average over the window is."""
    assert set(hour_request(SENTENCES, lane=LIVE)["questions"]) == {"next_30", "next_60"}
    assert set(hour_request(SENTENCES, lane=TAPE, ruler={"unit_points": 6.0})["questions"]) == {"next_10"}
    live = average_window(30, SCENARIOS["S1"][2])
    assert live == {"minutes": 30, "flat_points": 5.39, "edge_points": 3.19}
    req = average_request(SENTENCES, live, lane=LIVE)
    assert req["id"] == "average" and list(req["questions"]) == ["average_30"] and req["state"]["answers"] == SENTENCES
    words = req["state"]["context"]["average"]
    assert "counts every minute's closing price equally, so an early move counts for longer than a late one" in words
    assert "flat when it sits within 3.19 points of the price now either way" in words and "more than 3.19 points below it" in words
    assert set(req["questions"]["average_30"]) == {"type", "instructions", "criteria"}          # what JEV is sent, nothing more
    tape = average_request(SENTENCES, average_window(10, SCENARIOS["S9"][2]), lane=TAPE)
    assert "within 1.59 points of the price now" in tape["state"]["context"]["average"]
    assert "unit" not in tape["state"]["context"]                   # the tape unit's flat band is the end price's, not this one's
    pre = average_request(SENTENCES, average_window(30, 4.38), context={"horizon": "after the settled open"}, lane=PREMARKET)
    assert "over the 30 minutes after the settled open" in pre["state"]["context"]["average"]
    assert "of the settled open either way" in pre["state"]["context"]["average"] and pre["state"]["context"]["horizon"] == "after the settled open"


def test_the_average_summary_is_the_call_and_a_missing_or_unasked_one_says_why():
    window = average_window(30, 5.39)
    s = average_summary({"model": "m", "answers": {"average_30": AVERAGE}}, window, LIVE)
    assert (s["pick"], s["probabilities"], s["primary"], s["box"], s["edge_points"]) == ("down", AVERAGE["probabilities"], "average_30", "next_30", 3.19)
    assert s["by"]["average_30"]["pick"] == "down"
    assert average_summary({"error": "HTTP 529"}, window, LIVE)["error"] == "HTTP 529"
    assert average_summary({"answers": {"next_30": AVERAGE}}, window, LIVE)["error"] == "no average_30 answer"
    unasked = service.with_average({"pick": "flat"}, LIVE, "its window ends past the close, so it can never be graded", None)
    assert unasked["pick"] == "flat" and unasked["average"]["error"].startswith("not asked: its window ends past the close")


# ---- a read, end to end

@pytest.fixture
def fixture_run(tmp_path, monkeypatch):
    run_fixture(tmp_path, monkeypatch)
    return tmp_path / "spx_jev", tmp_path / "spx_jev" / "lanes" / "tape"


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_live_read_calls_the_average_and_its_grade_grades_that_pick_with_its_scores(fixture_run):
    """The 10:02 read of a day that climbs: JEV's end-price sum said up at 50%, its average-price sum down at 60%. The
    phone's call is the average's; the average over the window sat up, 6.68 against the 3.11-point edge JEV was told
    (0.07 of the 75-point anchor, narrowed by 0.5918), so the call is wrong, and its odds score a Brier of 1.04 and a
    log loss of ln 5. The end-price sum's own pick and its grade ride beside it."""
    live, _ = fixture_run
    rec = _lines(live / "hour" / f"{DAY}.jsonl")[0]
    assert rec["average_request"]["state"]["answers"] == rec["request"]["state"]["answers"]    # the same sentences, two requests
    assert rec["average"]["edge_points"] == 3.11 and rec["average"]["pick"] == "down"
    assert rec["pick"] == "up" and rec["primary"] == "next_30"                             # the end-price sum, flat on top as before
    (line,) = [g for g in _lines(live / grade.INTEGRAL_NAME) if g["horizon"] == "next_30"]
    assert (line["rule_version"], line["sum"], line["pick"], line["label"], line["g"], line["edge"], line["verdict"]) == \
        (2, "average_30", "down", "up", 6.68, 3.11, "wrong")
    assert line["edge_told"] == line["edge"]
    assert line["scores"] == {"brier": 1.04, "log_loss": round(math.log(5), 4)}
    (sixty,) = [g for g in _lines(live / grade.INTEGRAL_NAME) if g["horizon"] == "next_60"]
    assert sixty["sum"] == "next_60" and sixty["pick"] == "flat" and "scores" not in sixty    # no average-price sum for this box
    [call] = service.day_calls(live, DAY, LIVE)
    assert (call["pick"], call["p"], call["odds"], call["sum"]) == ("down", 0.6, AVERAGE["probabilities"], "average_30")
    assert call["end_price"]["pick"] == "up" and call["end_price"]["p"] == 0.5 and call["end_price"]["hit"] is True
    assert service.call_verdict(call) == "wrong" and call["integral"]["sum"] == "average_30"
    card = json.loads((live / "latest.json").read_text())
    assert card["hour"]["average"]["pick"] == "down" and card["hour"]["pick"] == "up"
    (read,) = [r for r in _lines(live / "archive" / f"{DAY}.jsonl") if r["kind"] == "read" and r["lane"] == "live"]
    assert read["average_request"]["id"] == "average" and read["average_response"]["answers"]["average_30"] == AVERAGE
    assert read["schema_version"] == archive.SCHEMA_VERSION == 5


def test_the_opening_call_is_the_average_and_its_size_stays_the_end_prices_second_line(fixture_run):
    _, tape = fixture_run
    (line,) = _lines(tape / grade.INTEGRAL_NAME)
    assert (line["sum"], line["pick"], line["label"], line["verdict"]) == ("average_10", "down", "flat", "wrong")
    assert line["size"]["call"] == "small"                          # named by the five-way end-price sum, up_small, never asked again
    assert line["edge_told"] == line["edge"] == 1.66 and "brier" in line["scores"]
    rec = _lines(tape / "hour" / f"{DAY}.jsonl")[0]
    assert "unit" in rec["request"]["state"]["context"] and "unit" not in rec["average_request"]["state"]["context"]


# ---- the grade, line by line

def _s1(average=None):
    """S1 on the live box: a 13:01 read at 7698.56 that spiked up and ended flat, 5.39 points of flat band (the anchor
    that gives it), the end-price sum having called flat."""
    hhmm, spot, f, pick, closes = SCENARIOS["S1"]
    t0 = at(13, 1, day=SCENARIO_DAY)
    ts = t0.isoformat()
    line = {"row_ts": ts, "horizons": ["next_30"], "anchor": {"points": f / NEXT_30_FLAT_BAND_SIGMA}, "next_30": {"pick": "flat", "band": "flat"}}
    rec = {"row_ts": ts, "spot": spot, "by": {"next_30": {"pick": "flat", "probabilities": {"flat": 0.6, "up": 0.2, "down": 0.1, "unsure": 0.1}}},
           **({"average": average} if average else {})}
    return grade.integral_line(line, "next_30", rec, _bars(t0, spot, closes), {}, LIVE)


def test_the_average_price_grade_grades_the_average_sums_pick_and_says_so():
    up = {"pick": "up", "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.2}, "edge_points": 3.19}
    g = _s1(up)
    assert (g["sum"], g["pick"], g["label"], g["g"], g["edge"], g["verdict"]) == ("average_30", "up", "up", 4.45, 3.19, "right")
    assert g["scores"] == {"brier": 0.38, "log_loss": 0.6931} and g["edge_told"] == 3.19
    # a record from before the question, or a read whose average-price sum got no answer: the end-price sum's own call, unscored
    for old in (_s1(), _s1({"error": "HTTP 529", "primary": "average_30"})):
        assert (old["sum"], old["pick"], old["verdict"]) == ("next_30", "flat", "wrong") and "scores" not in old and "edge_told" not in old


def test_a_blended_average_is_scored_with_jevs_own_odds_and_the_clocks_beside_it():
    blended = {"pick": "flat", "probabilities": {"up": 0.35, "flat": 0.45, "down": 0.2}, "blend": {"used": True},
               "jev": {"probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}}, "clock": {"probabilities": {"up": 0.1, "flat": 0.6, "down": 0.3}}}
    s = _s1(blended)["scores"]
    assert s == {"brier": 0.665, "log_loss": 1.0498, "jev_brier": 0.26, "jev_log_loss": 0.5108, "clock_brier": 1.26, "clock_log_loss": 2.3026}
    assert "jev_brier" not in _s1({**blended, "blend": {"used": False, "why": "too few sessions"}})["scores"]


def test_a_zero_on_the_label_costs_what_the_learning_loop_charges_it():
    """The log loss floors each outcome as scores.py does for the loop, so a zero on the label costs ln(1 / EPS)-ish,
    never an infinity."""
    s = _s1({"pick": "flat", "probabilities": {"up": 0.0, "flat": 1.0, "down": 0.0}})["scores"]
    assert s["brier"] == 2.0 and s["log_loss"] == pytest.approx(math.log(1.04 / 0.02), abs=1e-4)


# ---- what still reads

def test_a_read_graded_under_both_rules_stands_on_the_newest_and_a_first_rule_line_alone_still_reads(tmp_path):
    ts, older = at(11, 2).isoformat(), at(10, 32).isoformat()
    (tmp_path / "hour").mkdir()
    (tmp_path / "hour" / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
        {"row_ts": older, "pick": "up", "probabilities": {"up": 0.5, "flat": 0.3, "unsure": 0.2}},
        {"row_ts": ts, "pick": "up", "probabilities": {"up": 0.5, "flat": 0.3, "unsure": 0.2},
         "average": {"pick": "down", "probabilities": {"up": 0.1, "flat": 0.3, "down": 0.6}}})))
    first = {"horizon": "next_30", "graded": True, "label": "up", "g": 4.0, "edge": 3.19, "running": ["up"]}
    (tmp_path / grade.INTEGRAL_NAME).write_text("".join(json.dumps(line) + "\n" for line in (
        {**first, "row_ts": older, "rule_version": 1, "pick": "up", "verdict": "right"},
        {**first, "row_ts": ts, "rule_version": 1, "pick": "up", "verdict": "right"},
        {**first, "row_ts": ts, "rule_version": 2, "sum": "average_30", "pick": "down", "verdict": "wrong"})))
    old_call, new_call = service.day_calls(tmp_path, DAY, LIVE)
    assert (old_call["pick"], old_call["sum"], old_call["integral"]["verdict"]) == ("up", "next_30", "right")
    assert (new_call["pick"], new_call["sum"], new_call["integral"]["verdict"], new_call["integral"]["sum"]) == ("down", "average_30", "wrong", "average_30")


def test_the_end_price_verdict_is_the_end_price_sums_own_and_an_old_card_falls_back_on_the_call():
    assert service.end_price_verdict({"hit": False, "pick": "unsure"}, "up") == "passed"
    assert service.end_price_verdict({"hit": True, "pick": "up"}, "down") == "right"
    assert service.end_price_verdict({"hit": False}, "unsure") == "passed"        # a card from before names no pick on it
    call = {"pick": "down", "end_price": {"outcome": "flat", "hit": False, "pick": "unsure"}, "end_price_only": True}
    assert service.call_verdict(call) == "passed"
    tally = service.calls_block([call])["tally"]
    assert tally["passed"] == 1 and tally["right"] == 0
    v4 = {"schema_version": 4, "kind": "close_out", "calls": [{"pick": "unsure", "end_price": {"outcome": "up", "hit": False}}], "tally": {}}
    assert archive.read_close_out(v4) == v4                          # a version 4 close-out reads as it was written
