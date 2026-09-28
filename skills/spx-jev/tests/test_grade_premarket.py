"""The premarket lane's sums are graded from the settled open, in the ruler the read stamped, and no read
before the open is ever graded from its own spot."""
from __future__ import annotations

import json
from functools import partial

import pytest

from conftest import DAY, at, bars_from_closes, flat_bars, write_state
from spx_jev import events, service
from spx_jev.grade import grade_one, horizon_start, mark_at, run
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.lane import LIVE, PREMARKET, TAPE

RULER = 100.0
ALLOWED = {"q1": {"up", "down"}}
TAG = events.tag


def _climb():
    """Flat at 7700 to the 09:34 bar, then a point a minute: the 09:44 bar closes 10 points above the
    settled open, the 10:04 bar 30."""
    return bars_from_closes([7700.0] * 5 + [7700.0 + k for k in range(1, 386)])


def _rec(hh=9, mm=28, spot=7600.0, ruler=RULER):
    """A premarket sum record: its spot is the night's synthetic price, 100 points under the settled open."""
    by = {"open_10": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}},
          "open_30": {"pick": "flat", "probabilities": {"up": 0.3, "flat": 0.5, "down": 0.2}}}
    return {"row_ts": at(hh, mm).isoformat(), "spot": spot, "sigma": ruler, "lane": "premarket",
            "ruler": {"kind": "pre_open", "points": ruler, "sessions": 20} if ruler else {"omitted": "too few sessions"},
            "primary": "open_30", **by["open_30"], "by": by, "used": {"q1": "up"}, "fresh": {"q1": "up"}}


def _anchor(rec):
    points = (rec.get("ruler") or {}).get("points")
    return SigmaRuler(points, "pre_open") if points else None


def test_the_sums_run_from_the_settled_open_to_the_0944_and_1004_bars():
    assert horizon_start(at(2, 35).isoformat(), PREMARKET).isoformat() == f"{DAY}T09:35:00-04:00"
    assert mark_at(at(8, 48).isoformat(), 10, PREMARKET).isoformat() == f"{DAY}T09:45:00-04:00"
    assert mark_at(at(9, 28).isoformat(), 30, PREMARKET).isoformat() == f"{DAY}T10:05:00-04:00"
    assert mark_at("2026-12-01T09:05:00-05:00", 30, PREMARKET).isoformat() == "2026-12-01T10:05:00-05:00"   # winter time
    assert mark_at(at(9, 28).isoformat(), 30).isoformat() == f"{DAY}T09:58:00-04:00"                      # the live lane: the read's minute


def test_a_premarket_record_is_graded_from_the_settled_open_in_its_stamped_ruler():
    rec = _rec()
    g = grade_one(rec, _climb(), lane=PREMARKET, anchor=_anchor(rec))
    assert g["horizons"] == ["open_10", "open_30"] and g["pending"] == [] and g["skipped"] == {}
    assert g["open_10"]["realized_sigma"] == 0.1 and g["open_10"]["band"] == "up" and g["open_10"]["hit"] is True
    assert g["open_30"]["realized_sigma"] == 0.3 and g["open_30"]["band"] == "up" and g["open_30"]["hit"] is False
    assert g["from"] == {"settled_open": 7700.0, "at": f"{DAY}T09:35:00-04:00"}
    assert g["anchor"] == {"points": RULER, "source": "pre_open"}
    assert g["band"] == "up" and g["pick"] == "flat" and g["fresh"] == {"q1": "up"}               # the primary, flat on top


def test_the_same_record_is_measured_the_same_whatever_its_spot_or_read_time():
    bars = _climb()
    lines = [grade_one(r, bars, lane=PREMARKET, anchor=_anchor(r)) for r in (_rec(2, 35, 7500.0), _rec(8, 48, 7650.0), _rec(9, 28, 7710.0))]
    assert {(g["open_10"]["realized_sigma"], g["open_30"]["realized_sigma"]) for g in lines} == {(0.1, 0.3)}


def test_each_sum_waits_for_its_bars_today():
    bars, rec = _climb(), _rec()
    assert grade_one(rec, [], lane=PREMARKET, anchor=_anchor(rec)) is None                       # the session has not opened
    assert grade_one(rec, bars[:4], lane=PREMARKET, anchor=_anchor(rec)) is None                 # no settled open yet
    first = grade_one(rec, bars[:20], lane=PREMARKET, anchor=_anchor(rec))
    assert first["horizons"] == ["open_10"] and first["pending"] == ["open_30"]
    assert grade_one(rec, bars, {"open_10"}, lane=PREMARKET, anchor=_anchor(rec))["horizons"] == ["open_30"]


def test_a_finished_day_without_the_settled_open_or_the_ruler_is_closed_for_good():
    rec = _rec()
    holed = [b for b in flat_bars(390) if b["ts"] != at(9, 34).isoformat()]
    assert grade_one(rec, holed, lane=PREMARKET, anchor=_anchor(rec)) is None                    # today: the bar may still come
    g = grade_one(rec, holed, final=True, lane=PREMARKET, anchor=_anchor(rec))
    assert g["horizons"] == [] and g["skipped"]["open_30"].startswith("halted window: no settled open")
    bare = _rec(ruler=None)
    g = grade_one(bare, _climb(), lane=PREMARKET, anchor=_anchor(bare))
    assert g["horizons"] == [] and g["skipped"] == {"open_10": "no pre-open ruler on the record", "open_30": "no pre-open ruler on the record"}


def test_no_other_lane_grades_a_read_before_the_open_from_its_spot():
    for lane in (LIVE, TAPE):
        g = grade_one(_rec(), _climb(), lane=lane, anchor=SigmaRuler(RULER, "anchor"))
        assert g == {"row_ts": at(9, 28).isoformat(), "graded": False,
                     "reason": "stamped before the open: a read before the open is never graded from its spot"}


def _testimony_at(tmp_path, monkeypatch, time_et):
    """A tier-1 row at ``time_et`` today, read in place of the shipped calendar."""
    cal = tmp_path / "events.json"
    cal.write_text(json.dumps({"events": [{"date": DAY, "time_et": time_et, "kind": "FED_CHAIR_TESTIMONY", "tier": 1}]}))
    events._load.cache_clear()
    monkeypatch.setattr(events, "tag", partial(TAG, path=cal))


def test_an_event_inside_the_graded_window_is_flagged_though_the_read_came_earlier(tmp_path, monkeypatch):
    _testimony_at(tmp_path, monkeypatch, "10:00")
    rec = {**_rec(), "event": None}                   # 32 minutes after the 09:28 read, so the read's own tag missed it
    g = grade_one(rec, _climb(), lane=PREMARKET, anchor=_anchor(rec))
    assert g["event_within_30"] is True and "due in 25 minutes, at 10:00 ET" in g["event"]


def test_an_event_the_read_saw_ahead_but_over_before_the_window_is_not_flagged(tmp_path, monkeypatch):
    _testimony_at(tmp_path, monkeypatch, "09:00")
    rec = {**_rec(8, 48), "event": events.tag(at(8, 48))}
    assert rec["event"]["within_30"] is True
    assert "event_within_30" not in grade_one(rec, _climb(), lane=PREMARKET, anchor=_anchor(rec))


def _premarket_state(tmp_path, recs):
    state = write_state(tmp_path, DAY, [], _climb())
    out = PREMARKET.folder(state)
    (out / "hour").mkdir(parents=True)
    (out / "hour" / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return state, out


def test_the_lane_run_grades_into_its_own_folder_and_archive_with_neutral_weights(tmp_path):
    rec = _rec()
    state, out = _premarket_state(tmp_path, [rec, rec])
    w = run(state, out, ALLOWED, lane=PREMARKET)
    lines = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines()]
    assert len(lines) == 1 and lines[0]["from"]["settled_open"] == 7700.0 and lines[0]["open_30"]["realized_sigma"] == 0.3
    assert w["lane"] == "premarket" and w["primary"] == "open_30" and w["graded_runs"] == 1 and w["method"] == "neutral"
    assert set(w["sums"]) == {"open_10", "open_30"} and w["sums"]["open_10"]["n"] == 1
    archived = [json.loads(l) for l in (state / "spx_jev" / "archive" / f"{DAY}.jsonl").read_text().splitlines()]
    assert [(a["read_id"], a["lane"]) for a in archived] == [(f"premarket:{rec['row_ts']}", "premarket")]
    assert not list(out.glob("pool_*")) and not list((state / "spx_jev").glob("pool_*"))              # no loop runs on the lane
    assert not (state / "spx_jev" / "grades.jsonl").exists()
    assert run(state, out, ALLOWED, lane=PREMARKET)["new_this_run"] == 0


def test_the_live_grader_closes_out_a_read_before_the_open_that_reached_its_folder(tmp_path):
    state = write_state(tmp_path, DAY, [], _climb())
    out = LIVE.folder(state)
    (out / "hour").mkdir(parents=True)
    (out / "hour" / f"{DAY}.jsonl").write_text(json.dumps({**_rec(), "by": {"next_30": {"pick": "up", "probabilities": {"up": 1.0}}}}) + "\n")
    assert run(state, out, ALLOWED)["closed_out"] == 1
    assert json.loads((out / "grades.jsonl").read_text())["reason"].startswith("stamped before the open")


def test_the_days_calls_are_marked_from_the_settled_open(tmp_path):
    state, out = _premarket_state(tmp_path, [_rec(8, 48), _rec()])
    run(state, out, ALLOWED, lane=PREMARKET)
    calls = service.day_calls(out, DAY, PREMARKET)
    assert [c["mark"] for c in calls] == [f"{DAY}T10:05:00-04:00"] * 2
    assert [(c["pick"], c["outcome"], c["hit"]) for c in calls] == [("flat", "up", False)] * 2
    # both checks of every call, so the phone can give the 09:44 result beside the 10:04 one
    assert [c["checks"] for c in calls] == [{"open_10": {"outcome": "up", "hit": True, "pick": "up"},
                                              "open_30": {"outcome": "up", "hit": False, "pick": "flat"}}] * 2


def test_the_service_refuses_the_premarket_lane_with_a_pointer():
    with pytest.raises(SystemExit) as e:
        service.main(["--lane", "premarket"])
    assert e.value.code == 2
