"""The price-only baseline: the clock's and the movement-so-far odds, the one-day-out validation, the frozen file."""
from __future__ import annotations

import json
import math

import pytest

from conftest import FixedZones, at, bars_from_closes, make_row
from spx_jev import baseline, scores
from spx_jev.baseline import BASELINE_FILE, Baseline, clock_odds, fit, leave_one_day_out, move_so_far, state_odds, with_move
from spx_jev.clock import SHRINK, replayed_reads
from spx_jev.lane import LIVE
from spx_jev.row_adapter import labeller_row

H = list(LIVE.horizons)


def _day(day: str, closes: list[float]) -> list[dict]:
    rows = [labeller_row(make_row(at(9 + (30 + m) // 60, (30 + m) % 60, day=day), closes[m])) for m in range(0, 390, 5)]
    bars = bars_from_closes(closes, day=day)
    return with_move(replayed_reads(bars, rows, FixedZones(".", day)), float(bars[0]["open"]))


def test_the_move_so_far_is_scaled_to_the_share_of_the_session_gone():
    assert move_so_far(7737.5, 7700.0, 75.0, at(9, 30)) is None
    assert math.isclose(move_so_far(7737.5, 7700.0, 75.0, at(12, 45)), 0.5 / math.sqrt(0.5))
    assert math.isclose(move_so_far(7662.5, 7700.0, 75.0, at(16, 0)), 0.5)


def test_the_tables_count_each_graded_read_once_and_the_odds_shrink_as_the_clock_does():
    quiet = {f"2026-09-{d:02d}": _day(f"2026-09-{d:02d}", [7700.0] * 390) for d in (14, 15)}
    tables = fit(quiet, H)
    n = sum(sum(c.values()) for c in tables["clock"]["next_30"].values())
    assert n == sum(1 for reads in quiet.values() for r in reads if "next_30" in r["bands"]) and n > 0
    assert tables["clock"]["next_30"]["lunch"]["flat"] == sum(tables["clock"]["next_30"]["lunch"].values())
    assert clock_odds(tables, "next_30", "lunch")["flat"] == 1.0
    assert state_odds(tables, "next_30", "lunch", None) == clock_odds(tables, "next_30", "lunch")
    cell = tables["state"]["next_30"]["lunch"]["low"]
    ph = clock_odds(tables, "next_30", "lunch")
    assert state_odds(tables, "next_30", "lunch", 0.0) == {o: (cell[o] + SHRINK * ph[o]) / (sum(cell.values()) + SHRINK) for o in ph}


def test_each_day_is_scored_by_tables_that_never_saw_it():
    days = {"2026-09-14": _day("2026-09-14", [7700.0] * 390),
            "2026-09-15": _day("2026-09-15", [7700.0 + 0.5 * i for i in range(390)])}   # a steady climb: every window up
    lodo = leave_one_day_out(days, H)
    # the quiet day is scored by the climbing day's tables alone: its flat outcomes cost the floored flat odds
    quiet = lodo["next_30"]["2026-09-14"]
    assert quiet["clock"]["log_loss"] == pytest.approx(-math.log(scores.floored({"up": 1.0, "flat": 0.0, "down": 0.0})["flat"]))
    assert quiet["reads"] > 0 and set(quiet) == {"whole_day", "clock", "state", "reads"}


def test_the_frozen_file_is_whole_and_names_its_reference():
    doc = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert doc["rule_hash"] == baseline.rule_hash(doc) and len(doc["sessions"]) >= 30
    # the loop reads the calibrated E_clock as its reference: a refit that makes E_state the winner must change the loop too
    assert doc["reference"] == {h: "clock" for h in H}
    b = Baseline.load()
    p = b.clock("next_30", at(12, 2))
    assert math.isclose(sum(p.values()), 1.0) and b.version.endswith(doc["rule_hash"])
    assert math.isclose(sum(b.whole_day("next_60").values()), 1.0)


def test_the_frozen_file_was_counted_the_way_the_clock_and_the_grader_count_today():
    """The loop's reference and its clock experts must forecast the outcome the sums are graded on. The file was once
    left counted in each row's sigma after the grader moved to the morning anchor, calling flat too often: whenever
    the clock's counting rule moves on (clock.RULE_VERSION, the bands, the phases, the read grid), this fails until
    the file is refitted (spec/fit_baseline.py) on the sessions before the trial."""
    from spx_jev.clock import _rule_key
    doc = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert doc["clock_rule"] == json.loads(_rule_key(LIVE.horizons))
    assert doc["sessions"] and max(doc["sessions"]) <= doc["through"] < "2026-09-28"     # the trial's first session is never in it
