"""The average-price question: JEV is asked where the average price over the window will sit against the read, up,
flat or down with no unsure and the flat edge in points, in a request of its own beside the end-price sums. The phone's
call is its answer, the average-price grade grades its pick and scores its probabilities, the time-of-day blend on it is
counted on the average price alone, and a record, a line or a card from before it still reads."""
from __future__ import annotations

import json
import pytest

from conftest import DAY
from spx_jev import archive, service
from spx_jev.ask import jev_only
from spx_jev.hour import average_request, average_summary, average_window, hour_request, load_hour_doc
from spx_jev.lane import LANES, LIVE, PREMARKET, TAPE
from test_integral import SCENARIOS
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


def test_a_live_read_asks_both_requests_on_the_same_sentences_and_archives_the_average(fixture_run):
    """The 10:02 read of a day that climbs: the end-price sums in one request as before, the average-price sum in its
    own on the same sentences, its window priced at 3.11 points (0.07 of the 75-point anchor, narrowed by 0.5918)."""
    live, tape = fixture_run
    rec = _lines(live / "hour" / f"{DAY}.jsonl")[0]
    assert rec["average_request"]["state"]["answers"] == rec["request"]["state"]["answers"]
    assert rec["average"]["edge_points"] == 3.11 and rec["average"]["pick"] == "down" and rec["pick"] == "up"
    assert "3.11 points" in rec["average_request"]["state"]["context"]["average"]
    (read,) = [r for r in _lines(live / "archive" / f"{DAY}.jsonl") if r["kind"] == "read" and r["lane"] == "live"]
    assert read["average_request"]["id"] == "average" and read["average_response"]["answers"]["average_30"] == AVERAGE
    assert read["schema_version"] == archive.SCHEMA_VERSION == 5
    t = _lines(tape / "hour" / f"{DAY}.jsonl")[0]
    assert "unit" in t["request"]["state"]["context"] and "unit" not in t["average_request"]["state"]["context"]
