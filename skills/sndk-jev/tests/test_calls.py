"""The calls the card carries for the phone's "calls in play": every primary sum of the day with its grade,
read from the hour records and grades.jsonl exactly as the grader wrote them."""
from __future__ import annotations

import json

from sndk_jev.lane import LIVE, TAPE
from sndk_jev.service import CALLS_SHOWN, calls_block, day_calls

DAY = "2026-09-25"


def _write(folder, hour_rows, grade_rows):
    (folder / "hour").mkdir(parents=True)
    (folder / "hour" / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in hour_rows))
    (folder / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grade_rows))


def test_live_calls_take_the_shown_pick_the_graders_mark_and_each_kind_of_grade(tmp_path):
    t = lambda hm, s="00": f"{DAY}T{hm}:{s}-04:00"
    hour = [
        {"row_ts": t("10:02", "41"), "pick": "flat", "probabilities": {"up": 0.1, "down": 0.1, "flat": 0.8}},
        {"row_ts": t("10:02", "41"), "pick": "flat", "probabilities": {"up": 0.1, "down": 0.1, "flat": 0.8}},   # written twice by hand
        {"row_ts": t("10:32", "12"), "error": "no answer"},                                                     # no sum: not a call
        {"row_ts": t("11:02", "05"), "pick": "up", "probabilities": {"up": 0.5, "down": 0.2, "flat": 0.3}},
        {"row_ts": t("15:32", "30"), "pick": "down", "probabilities": {"up": 0.2, "down": 0.45, "flat": 0.35}},
        {"row_ts": t("15:48", "00"), "pick": "flat", "probabilities": {"flat": 0.9}},
    ]
    grades = [
        {"row_ts": t("10:02", "41"), "horizons": ["next_30"], "next_30": {"band": "flat", "hit": True, "realized_sigma": -0.04}, "band": "flat", "hit": True},
        {"row_ts": t("10:02", "41"), "horizons": ["next_60"], "next_60": {"band": "up", "hit": False}},
        {"row_ts": t("11:02", "05"), "horizons": [], "pending": [], "skipped": {"next_30": "halted window: bars missing around the mark on a finished day"}},
        {"row_ts": t("15:48", "00"), "graded": False, "reason": "every horizon ends past the close"},
        {"row_ts": "2026-09-24T10:02:00-04:00", "horizons": ["next_30"], "next_30": {"band": "up", "hit": False}},   # another day
    ]
    _write(tmp_path, hour, grades)
    calls = day_calls(tmp_path, DAY, LIVE)
    assert [c["read"][11:16] for c in calls] == ["10:02", "11:02", "15:32", "15:48"]
    assert calls[0] == {"read": t("10:02", "41"), "mark": f"{DAY}T10:32:00-04:00", "minutes": 30, "pick": "flat", "p": 0.8,
                        "odds": {"up": 0.1, "down": 0.1, "flat": 0.8},
                        "outcome": "flat", "hit": True, "moved": {"realized_sigma": -0.04}}   # the 60-minute line never stands for the primary
    assert calls[1]["closed"].startswith("halted window") and "outcome" not in calls[1]
    assert "outcome" not in calls[2] and "closed" not in calls[2]         # its mark has not been graded yet
    assert calls[2]["mark"] == f"{DAY}T16:00:00-04:00"                     # 16:02 is inside the grace: the closing bar stands for it
    assert calls[3]["closed"] == "every horizon ends past the close" and calls[3]["mark"] is None
    block = calls_block(calls)
    assert [c["read"][11:16] for c in block["calls"]] == ["15:48", "15:32", "11:02", "10:02"][:CALLS_SHOWN]
    assert block["tally"] == {"calls": 4, "graded": 1, "right": 1}


def test_lane_calls_are_ten_minutes_and_five_way(tmp_path):
    _write(tmp_path, [{"row_ts": f"{DAY}T09:40:00-04:00", "pick": "up_small",
                       "probabilities": {"up_small": 0.38, "flat": 0.3, "down_big": 0.32}}],
           [{"row_ts": f"{DAY}T09:40:00-04:00", "horizons": ["next_10"],
             "next_10": {"band": "up_big", "hit": False, "direction": "up", "realized_dollars": 21.4, "realized_units": 0.973}}])
    assert day_calls(tmp_path, DAY, TAPE) == [{"read": f"{DAY}T09:40:00-04:00", "mark": f"{DAY}T09:50:00-04:00", "minutes": 10,
                                               "pick": "up_small", "p": 0.38, "odds": {"up_small": 0.38, "flat": 0.3, "down_big": 0.32},
                                               "outcome": "up_big", "hit": False, "moved": {"realized_dollars": 21.4, "realized_units": 0.973}}]
    assert day_calls(tmp_path / "nowhere", DAY, TAPE) == []


def test_mark_at_is_the_graders_minute():
    from sndk_jev.grade import mark_at
    assert mark_at(f"{DAY}T10:02:41-04:00", 30).isoformat() == f"{DAY}T10:32:00-04:00"
    assert mark_at(f"{DAY}T15:31:56-04:00", 30).isoformat() == f"{DAY}T16:00:00-04:00"   # the closing bar
    assert mark_at(f"{DAY}T15:31:56-04:00", 60) is None                                  # ends past the close: never graded
    assert mark_at("2026-11-27T12:31:00-05:00", 30).isoformat() == "2026-11-27T13:00:00-05:00"   # a half day's close
    assert mark_at("2026-11-27T12:40:00-05:00", 30) is None
