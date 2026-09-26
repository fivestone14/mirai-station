"""The lane settings, the bar clock and the tape unit, and the tape lane's sums request and summary."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from conftest import DAY, at, bars_from_closes, flat_bars, make_row
from sndk_jev.hour import HOUR_QIDS, band_of, hour_request, hour_summary, load_hour_doc, unit_line, views_of
from sndk_jev.lane import LANES, LIVE, RECORD, TAPE, Lane
from sndk_jev.state_builder import BAR_CLOCK_ROW_KEYS, make_scene, ruler

UNIT = {"unit_dollars": 24.0, "unit_sigma": 0.4, "slices_used": 0, "source": "held"}


def test_the_live_lane_is_todays_service_and_the_tape_lane_is_the_plan():
    assert LANES == {"live": LIVE, "tape": TAPE}
    assert LIVE.out_dir == "jev" and LIVE.questions.name == "sndk_pro.json" and LIVE.hour_doc.name == "sndk_hour.json"
    assert LIVE.horizons == {"next_30": (30, 0.12), "next_60": (60, 0.17)} and LIVE.primary == "next_30"
    assert (LIVE.min_graded, LIVE.pair_gap_min, LIVE.cadence, LIVE.tag) == (40, 0, True, None)
    assert (LIVE.bar_clock, LIVE.clock_blend, LIVE.bar_gap_min, LIVE.shuffles) == (False, True, 2, 0)
    assert TAPE.out_dir == "jev/lanes/tape" and TAPE.questions.name == "sndk_lane_tape.json" and TAPE.hour_doc.name == "sndk_lane_hour.json"
    assert TAPE.horizons == {"next_10": (10, RECORD)} and TAPE.primary == "next_10"
    assert (TAPE.min_graded, TAPE.pair_gap_min, TAPE.cadence, TAPE.tag) == (120, 10, False, "tape")
    assert (TAPE.bar_clock, TAPE.clock_blend, TAPE.bar_gap_min, TAPE.shuffles) == (True, False, 0, 1000)


def test_a_tagged_lane_refuses_to_run_without_a_folder_of_its_own(tmp_path):
    assert LIVE.folder(tmp_path) == tmp_path / "jev" and LIVE.folder(tmp_path, tmp_path / "x") == tmp_path / "x"
    assert TAPE.folder(tmp_path) == tmp_path / "jev" / "lanes" / "tape"
    with pytest.raises(ValueError, match="no folder of its own"):
        replace(TAPE, out_dir=None).folder(tmp_path)
    with pytest.raises(ValueError, match="must not write into"):
        TAPE.folder(tmp_path, tmp_path / "jev")


def test_the_ruler_holds_before_0945_then_measures_the_tape_with_a_floor():
    sigma = 45.0
    held = ruler(flat_bars(15), sigma, at(9, 44))
    assert held == {"unit_dollars": 18.0, "unit_sigma": 0.4, "slices_used": 0, "source": "held"}
    # 09:45 exactly: three slices exist. Flat bars with a half-point wick range 1 point a slice: under the floor
    floor = ruler(flat_bars(15), sigma, at(9, 45))
    assert floor == {"unit_dollars": 3.6, "unit_sigma": 0.08, "slices_used": 3, "source": "floor"}
    # a wick of 3 points ranges 6 a slice; the middle slice wider still, so the median, not the mean, is the unit
    bars = bars_from_closes([1700.0] * 15, wick=3.0)
    bars[7]["high"] = 1730.0
    tape = ruler(bars, sigma, at(9, 45))
    assert tape == {"unit_dollars": 6.0, "unit_sigma": round(6.0 / 45.0, 3), "slices_used": 3, "source": "tape"}
    assert ruler(bars_from_closes([1700.0] * 30, wick=30.0), sigma, at(10, 0))["unit_dollars"] == 60.0   # no cap


def test_the_ruler_is_omitted_when_the_bars_stop_and_counts_the_slices_it_has():
    bars = bars_from_closes([1700.0] * 30, wick=3.0)                      # 09:30 .. 09:59
    assert ruler(bars, 45.0, at(10, 10)) is None                          # the newest slice, 10:05 to 10:10, has no bars
    holed = [b for b in bars if not (at(9, 50) <= at(int(b["ts"][11:13]), int(b["ts"][14:16])) < at(9, 55))]
    assert ruler(holed, 45.0, at(10, 0))["slices_used"] == 2               # a hole in the middle: two slices, still a unit


def _state(tmp_path, rows, bars):
    (tmp_path / "sndk_reversion").mkdir(exist_ok=True)
    (tmp_path / "sndk_bars").mkdir(exist_ok=True)
    (tmp_path / "sndk_reversion" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "sndk_bars" / f"{DAY}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")
    return tmp_path


def test_the_bar_clock_stamps_the_read_at_the_newest_finished_bar(tmp_path):
    """The read's time and spot are the newest bar's; sigma is pinned to the day's first row; the walls,
    vwap and the book come from the newest diary row and nothing else does."""
    rows = [make_row(at(9, 58), 1700.0, sigma=45.0), make_row(at(10, 0, ss=30), 1701.0, sigma=50.0, vwap=1690.0)]
    bars = bars_from_closes([1700.0 + i for i in range(40)])             # 09:30 .. 10:09, closes 1700 .. 1739
    state = _state(tmp_path, rows, bars)
    scene = make_scene(state, DAY, bar_clock=True)
    assert scene.row["ts"] == at(10, 10).isoformat() and scene.now == at(10, 10)
    assert scene.row["spot"] == 1739.0 and scene.sigma == 45.0            # the 10:09 bar's close; the first row's sigma
    assert scene.row["vwap"] == 1690.0 and scene.row["call_wall"] == rows[1]["call_wall"] and scene.row["meta"] == rows[1]["meta"]
    assert "prior_close" not in scene.row and "range_ruler" not in scene.row
    assert set(scene.row) == {"ts", "spot", "sigma"} | {k for k in BAR_CLOCK_ROW_KEYS if k in rows[1]}
    assert len(scene.bars) == 40 and scene.rows_today[-1] is scene.row and scene.rows_today[0] is not scene.row
    earlier = make_scene(state, DAY, at=at(10, 5).time(), bar_clock=True)
    assert earlier.row["ts"] == at(10, 5).isoformat() and earlier.row["spot"] == 1734.0 and earlier.row["vwap"] == 1690.0
    plain = make_scene(state, DAY)
    assert plain.row["ts"] == rows[1]["ts"] and plain.row["spot"] == 1701.0        # without the bar clock: the row as it was


def test_the_lane_hour_doc_and_the_priced_context_line():
    d = load_hour_doc(lane=TAPE)
    assert list(d["questions"]) == ["next_10"] and d["primary"] == "next_10"
    assert list(d["questions"]["next_10"]["criteria"]) == ["down_big", "down_small", "flat", "up_small", "up_big", "unsure"]
    band = band_of(UNIT, d, "next_10")
    assert band == {"flat_dollars": 8.4, "big_dollars": 16.8, "flat_units": 0.35, "big_units": 0.7}
    assert unit_line(UNIT, band) == d["context_line_example"] == (
        "one tape unit is $24; flat is within $8 either way (0.35 of a unit); small is $8 to $17; big is more than $17 (0.7 of a unit)")
    req = hour_request({"q": "a sentence"}, lane=TAPE, ruler=UNIT)
    assert req["state"]["context"]["unit"] == d["context_line_example"] and req["state"]["context"]["horizon"] == "the next 10 minutes"
    assert list(req["questions"]) == ["next_10"] and set(req["questions"]["next_10"]) == {"type", "instructions", "criteria"}
    live = hour_request({"q": "a sentence"})
    assert "unit" not in live["state"]["context"] and live["state"]["context"]["horizon"] == "the next 30 minutes, and the next 60 minutes"
    assert list(live["questions"]) == list(HOUR_QIDS)
    with pytest.raises(ValueError, match="needs questions"):
        load_hour_doc(LIVE.hour_doc, lane=TAPE)


def test_the_five_way_sum_is_read_three_ways_and_unsure_is_never_spread():
    p = {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.2, "up_big": 0.15, "unsure": 0.2}
    v = views_of(p)
    assert v["direction"] == {"pick": "up", "probabilities": {"up": 0.35, "flat": 0.3, "down": 0.15, "unsure": 0.2}}
    assert v["size"] == {"pick": "small", "probabilities": {"big": 0.2, "small": 0.6, "unsure": 0.2}}
    s = hour_summary({"model": "jev-1", "answers": {"next_10": {"type": "choice", "choice": "flat", "confidence": 0.4, "probabilities": p}}}, TAPE)
    assert s["primary"] == "next_10" and s["pick"] == "flat" and s["views"]["size"]["pick"] == "small" and s["by"]["next_10"]["views"] == v
    live = hour_summary({"answers": {"next_30": {"type": "choice", "choice": "flat", "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.2}}}})
    assert "views" not in live and "views" not in live["by"]["next_30"]
    assert hour_summary({"answers": {"next_30": {"type": "choice", "choice": "flat"}}}, TAPE) == {"error": "no answer"}


def test_the_tape_plist_fires_at_the_lanes_reads_and_its_close_out():
    """The job's calendar is the lane's schedule plus the close-out, in the box's Pacific time (market time
    minus 3 hours), in the plist that is installed and in its template alike."""
    import plistlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    want = [(int(t[:2]) - 3, int(t[3:])) for t in TAPE.schedule + (TAPE.close_out,)]
    assert len(TAPE.schedule) == 12 and TAPE.schedule[0] == "09:35" and TAPE.schedule[-1] == "10:30" and TAPE.close_out == "10:42"
    for path in (root / "runtime/launchd/com.mirai-station.sndk-jev-tape.plist",
                 root / "skills/sndk-jev/launchd/com.mirai-station.sndk-jev-tape.plist.template"):
        fires = [(e["Hour"], e["Minute"]) for e in plistlib.loads(path.read_bytes())["StartCalendarInterval"]]
        assert fires == want, path.name
    assert LIVE.schedule == () and LIVE.close_out is None
