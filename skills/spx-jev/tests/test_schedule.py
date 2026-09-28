"""The machine schedule: which read a moment stands for, and which questions each read asks."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import at
from spx_jev.ask import load_questions
from spx_jev.lane import LIVE, TAPE
from spx_jev.schedule import asks_at, check, not_due, read_slot

LIVE_DOC = load_questions(LIVE.questions, LIVE.key)
TAPE_DOC = load_questions(TAPE.questions, TAPE.key)
DAY = date(2026, 9, 18)
FOMC_DAY = date(2026, 10, 28)                  # calendar/events.json: the decision at 14:00, the press conference at 14:30


def test_a_read_stands_for_its_lanes_latest_read_time_with_the_lanes_grace():
    assert read_slot(LIVE, at(10, 1, ss=40)) == "10:02" and read_slot(LIVE, at(9, 57)) == "10:02"   # the row before the fire
    assert read_slot(LIVE, at(12, 45)) == "12:32" and read_slot(LIVE, at(9, 20)) is None
    assert read_slot(TAPE, at(9, 35)) == "09:35" and read_slot(TAPE, at(9, 34)) == "09:35" and read_slot(TAPE, at(9, 37)) == "09:35"
    assert read_slot(TAPE, at(10, 34)) == "10:30" and read_slot(TAPE, at(10, 35)) is None and read_slot(LIVE, at(16, 20)) == "16:02"


def test_a_live_send_never_stands_for_a_read_time_its_job_had_not_reached():
    """The grace is for a row stamped before its job fired, so a live send caps it at the fire: a 10:26:30 row
    sent at 10:27 is a late 10:02 read and a 09:38 bar read at 09:38 the 09:35 read, not the reads to come.
    A replay has only the row's clock; a fire a few seconds early is still its read."""
    assert read_slot(LIVE, at(10, 26, ss=30)) == "10:32" and read_slot(TAPE, at(9, 38)) == "09:40"         # a replay
    assert read_slot(LIVE, at(10, 26, ss=30), fired=at(10, 27)) == "10:02"
    assert read_slot(TAPE, at(9, 38), fired=at(9, 38)) == "09:35"
    assert read_slot(LIVE, at(9, 57), fired=at(10, 2)) == "10:02"                                          # the row before the fire
    assert read_slot(LIVE, at(10, 1, ss=40), fired=at(10, 1, ss=58)) == "10:02"                            # launchd a little early
    assert read_slot(LIVE, at(10, 1, ss=0), fired=at(10, 1, ss=0)) == "09:32"
    assert "vol_curve_vs_usual" in not_due(LIVE_DOC, LIVE, at(10, 26, ss=30))                             # held as if at 10:32
    assert "vol_curve_vs_usual" not in not_due(LIVE_DOC, LIVE, at(10, 26, ss=30), fired=at(10, 27))       # asked at 10:02
    assert "gap_size" not in not_due(TAPE_DOC, TAPE, at(9, 38), fired=at(9, 38))


def test_every_kind_of_entry():
    reads = LIVE.read_times()
    every = {"every_min": 60, "from": "10:02", "to": "15:32"}
    assert [s for s in reads if asks_at(every, s, DAY, reads)] == ["10:02", "11:02", "12:02", "13:02", "14:02", "15:02"]
    assert asks_at({"at": ["15:02", "15:32"]}, "15:32", DAY, reads) and not asks_at({"at": ["15:02"]}, "15:32", DAY, reads)
    assert not any(asks_at({"hold_until": "11:32"}, s, DAY, reads) for s in reads)
    tape = TAPE.read_times()
    assert [s for s in tape if asks_at({"every_min": 5, "from": "09:45", "to": "10:30"}, s, DAY, tape)][:2] == ["09:45", "09:50"]


def test_an_fomc_only_entry_starts_at_the_press_conference_and_only_on_fomc_days():
    reads = LIVE.read_times()
    presser = {"every_min": 30, "from": "after the press conference starts", "to": "15:32", "days": "FOMC only"}
    assert [s for s in reads if asks_at(presser, s, FOMC_DAY, reads)] == ["14:32", "15:02", "15:32"]
    assert not any(asks_at(presser, s, DAY, reads) for s in reads)


def test_what_a_read_leaves_out_and_why():
    at_1002 = not_due(LIVE_DOC, LIVE, at(10, 2))
    assert "price_move_5way" not in at_1002 and "vol_curve_vs_usual" not in at_1002
    assert at_1002["one_way_hour"] == "not on its schedule at the 10:02 ET read"                   # from 10:32
    assert at_1002["open_vs_prior_range"] == "asked on its other lane and held here until 11:32 ET"
    assert "overnight_range_position" not in at_1002                                                  # dark: the packer's
    at_1232 = not_due(LIVE_DOC, LIVE, at(12, 32))
    assert at_1232["vol_curve_vs_usual"] == "a day constant: asked at 10:02 ET and held"
    assert at_1232["open_vs_prior_range"] == "held from its other lane only until 11:32 ET"
    assert at_1232["flow_vs_price"] == "not on its schedule at the 12:32 ET read"                    # shadow, hourly from 10:02
    assert set(not_due(LIVE_DOC, LIVE, at(9, 32))) >= {"price_move_5way", "fresh_extreme_5m"}         # 30-minute reads start at 10:02
    first = not_due(TAPE_DOC, TAPE, at(9, 35))
    assert "gap_size" not in first and first["opening_side"] == "not on its schedule at the 09:35 ET read"
    assert not_due(TAPE_DOC, TAPE, at(9, 40))["gap_size"] == "a day constant: asked at 09:35 ET and held"
    assert not_due(TAPE_DOC, TAPE, at(9, 20))["gap_size"] == "no tape lane read at 09:20 ET"
    assert not_due(TAPE_DOC, TAPE, at(12, 30))["gap_size"] == "no tape lane read at 12:30 ET"


@pytest.mark.parametrize("entry", [{"every_min": 30, "from": "10:02"}, {"every_min": 30, "from": "whenever", "to": "15:32"},
                                   {"at": []}, {"at": ["9:3x"]}, {"hold_until": "11:32", "at": ["09:35"]},
                                   {"every_min": 30, "from": "10:02", "to": "15:32", "days": "Tuesdays"}, {"every": 30}])
def test_an_entry_the_code_cannot_read_stops_the_load(entry):
    with pytest.raises(ValueError, match="q1"):
        check(entry, "q1")
