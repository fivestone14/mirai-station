"""Step 6: the bars grade both sums; the grades move the weights, but only once there is evidence."""
from __future__ import annotations

import json

from conftest import at, bars_from_closes
from sndk_jev.grade import HORIZONS, MIN_GRADED, grade_one, graded_horizons, mutual_information, realized_band, run, weights_from


def _rec(hh, mm, spot, by, used):
    return {"row_ts": at(hh, mm).isoformat(), "spot": spot, "sigma": 50.0, "by": by, "used": used}


def _by(p30, pick30, p60=None, pick60=None):
    by = {"next_30": {"pick": pick30, "probabilities": p30, "confidence": 0.5}}
    if p60:
        by["next_60"] = {"pick": pick60, "probabilities": p60, "confidence": 0.5}
    return by


def test_horizons_come_from_the_doc():
    assert HORIZONS == {"next_30": (30, 0.12), "next_60": (60, 0.17)}


def test_realized_band_uses_the_horizon_band():
    assert realized_band(0.13, 0.12) == "up" and realized_band(-0.13, 0.12) == "down" and realized_band(0.10, 0.12) == "flat"
    assert realized_band(0.13, 0.17) == "flat"


def test_grade_one_reads_both_horizons():
    # flat until 11:00, then a climb of 20 points (0.4 sigma at sigma 50) by 12:00: 0.2 sigma at 11:30
    closes = [1700.0] * 90 + [1700.0 + 20.0 * (i + 1) / 60 for i in range(60)] + [1720.0] * 240
    bars = bars_from_closes(closes)
    rec = _rec(11, 0, 1700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.2, "flat": 0.7, "down": 0.1}, "flat"), {"q1": "rising"})
    g = grade_one(rec, bars)
    assert g["next_30"]["band"] == "up" and g["next_30"]["realized_sigma"] == 0.2 and g["next_30"]["hit"] is True
    assert g["next_60"]["band"] == "up" and g["next_60"]["realized_sigma"] == 0.4 and g["next_60"]["hit"] is False
    assert g["band"] == "up" and g["hit"] is True                      # the primary, flat on top
    assert g["brier"] == round((0.6 - 1) ** 2 + 0.3 ** 2 + 0.1 ** 2, 4)


def test_each_horizon_is_graded_at_its_own_mark_and_one_past_the_close_is_skipped():
    closes = [1700.0] * 390
    bars = bars_from_closes(closes)
    rec = _rec(11, 0, 1700.0, _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat"), {})
    assert grade_one(rec, bars[:80]) is None                           # 11:30 not yet: nothing to grade
    first = grade_one(rec, bars[:120])                                 # 30 done, 60 not yet: the 30 is graded now
    assert first["horizons"] == ["next_30"] and first["pending"] == ["next_60"] and first["band"] == "flat"
    assert grade_one(rec, bars[:120], {"next_30"}) is None             # already have the 30, the 60 still ahead
    second = grade_one(rec, bars, {"next_30"})                         # the 60 at its own mark, nothing flat on top
    assert second["horizons"] == ["next_60"] and "next_30" not in second and "band" not in second
    assert grade_one(rec, bars, {"next_30", "next_60"}) is None        # nothing left
    both = grade_one(rec, bars)                                        # a run after both marks: one line
    assert both["horizons"] == ["next_30", "next_60"] and both["pending"] == []
    late = _rec(15, 20, 1700.0, _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat"), {})
    g = grade_one(late, bars)                                          # 60 runs past the close: 30 alone, 60 skipped for good
    assert "next_30" in g and "next_60" not in g and g["band"] == "flat" and g["skipped"] == {"next_60": "ends past the close"}
    edge = _rec(15, 31, 1700.0, _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat"), {})
    g = grade_one(edge, bars)                                          # 16:01 is inside the grace: the close stands in
    assert "next_30" in g and "next_60" not in g and g["band"] == "flat"
    done = grade_one(_rec(15, 45, 1700.0, _by({"flat": 1.0}, "flat"), {}), bars)      # nothing can ever be graded
    assert done == {"row_ts": at(15, 45).isoformat(), "graded": False, "reason": "every horizon ends past the close"}


def test_old_lines_and_closed_out_lines_count_as_complete():
    lines = [{"row_ts": "a", "band": "flat", "next_30": {}, "next_60": {}},                       # before horizons were split
             {"row_ts": "b", "graded": False, "reason": "every horizon ends past the close"},
             {"row_ts": "c", "horizons": ["next_30"], "pending": ["next_60"], "skipped": {}},
             {"row_ts": "d", "horizons": ["next_30"], "pending": [], "skipped": {"next_60": "ends past the close"}}]
    done = graded_horizons(lines)
    assert done["a"] == {"next_30", "next_60"} and done["b"] == {"next_30", "next_60"}
    assert done["c"] == {"next_30"} and done["d"] == {"next_30", "next_60"}


def test_a_record_from_before_the_switch_is_never_graded():
    bars = bars_from_closes([1700.0] * 390)
    old = {"row_ts": at(11, 0).isoformat(), "spot": 1700.0, "sigma": 50.0, "pick": "flat", "probabilities": {"flat": 1.0}, "used": {"q1": "a"}}
    assert grade_one(old, bars) is None


def test_weights_stay_at_one_until_a_question_has_its_own_evidence():
    grades = [{"row_ts": str(i), "band": "flat", "pick": "flat", "hit": True, "brier": 0.1, "used": {"q1": "a", "q2": "b"},
               "fresh": {"q1": "a", "q2": "b"}, "next_30": {"band": "flat", "hit": True, "brier": 0.1}} for i in range(MIN_GRADED - 1)]
    w = weights_from(grades)
    assert all(v["weight"] == 1.0 and v["in_step_3"] for v in w["questions"].values())
    assert w["sums"]["next_30"]["hit_rate"] == 1.0 and w["sums"]["next_30"]["n"] == MIN_GRADED - 1 and w["sums"]["next_60"]["n"] == 0


def test_the_floor_is_per_question_and_held_answers_never_pair():
    grades = []
    for i in range(MIN_GRADED):
        band = "up" if i % 2 else "flat"
        g = {"row_ts": str(i), "band": band, "pick": band, "hit": True, "brier": 0.0,
             "used": {"q_tell": "x" if band == "up" else "y", "q_sparse": "same", "q_held": "x" if band == "up" else "y"},
             "fresh": {"q_tell": "x" if band == "up" else "y"},
             "next_30": {"band": band, "hit": True, "brier": 0.0}}
        if i < 5:
            g["fresh"]["q_sparse"] = "same"          # asked afresh only five times: no verdict on it yet
        grades.append(g)
    w = weights_from(grades)
    assert w["questions"]["q_tell"]["weight"] == 1.0 and w["questions"]["q_tell"]["n"] == MIN_GRADED
    assert w["questions"]["q_sparse"]["weight"] == 1.0 and w["questions"]["q_sparse"]["n"] == 5 and "under" in w["questions"]["q_sparse"]["why"]
    assert "q_held" not in w["questions"]                            # only ever held: it never pairs


def test_a_telling_question_outweighs_a_blind_one():
    grades = []
    for i in range(MIN_GRADED):
        band = "up" if i % 2 else "flat"
        grades.append({"row_ts": str(i), "band": band, "pick": band, "hit": True, "brier": 0.0,
                       "used": {"q_tell": "x" if band == "up" else "y", "q_blind": "same"},
                       "next_30": {"band": band, "hit": True, "brier": 0.0}})
    w = weights_from(grades)
    assert w["questions"]["q_tell"]["weight"] == 1.0 and w["questions"]["q_tell"]["in_step_3"]
    assert w["questions"]["q_blind"]["weight"] == 0.0 and not w["questions"]["q_blind"]["in_step_3"]
    assert mutual_information([("a", "up"), ("a", "flat")]) == 0.0


def test_run_appends_grades_writes_weights_and_logs_once(tmp_path):
    state = tmp_path / "state"
    (state / "sndk_bars").mkdir(parents=True)
    closes = [1700.0] * 90 + [1700.0 + 20.0 * (i + 1) / 60 for i in range(60)] + [1720.0] * 240
    with open(state / "sndk_bars" / "2026-09-18.jsonl", "w") as f:
        for b in bars_from_closes(closes):
            f.write(json.dumps(b) + "\n")
    out = state / "jev"
    (out / "hour").mkdir(parents=True)
    with open(out / "hour" / "2026-09-18.jsonl", "w") as f:
        f.write(json.dumps(_rec(11, 0, 1700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.5, "flat": 0.4, "down": 0.1}, "up"), {"q1": "rising"})) + "\n")
        f.write(json.dumps(_rec(15, 45, 1700.0, _by({"flat": 1.0}, "flat"), {"q1": "flat"})) + "\n")   # past the close
    with open(out / "hour" / "2026-09-18.jsonl", "a") as f:                                # the same row written twice, as a run by hand does
        f.write(json.dumps(_rec(11, 0, 1700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.5, "flat": 0.4, "down": 0.1}, "up"), {"q1": "rising"})) + "\n")
    w = run(state, out)
    assert w["graded_runs"] == 1 and w["new_this_run"] == 1 and w["closed_out"] == 1     # the past-the-close row is closed out, never retried
    assert w["sums"]["next_30"]["n"] == 1 and w["sums"]["next_60"]["n"] == 1
    lines = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 2 and lines[1]["graded"] is False
    assert json.loads((out / "weights.json").read_text())["questions"]["q1"]["weight"] == 1.0
    w2 = run(state, out)                       # idempotent: nothing new to grade
    assert w2["graded_runs"] == 1 and w2["new_this_run"] == 0
    log = [json.loads(l) for l in (out / "weights_log.jsonl").read_text().splitlines() if l.strip()]
    assert len(log) == 1 and log[0]["graded_runs"] == 1 and log[0]["new"] == 1 and log[0]["hit_rate"] == 1.0
    assert log[0]["changes"] == [{"question": "q1", "before": None, "after": 1.0, "mi": 0.0, "n": 1, "in_step_3": True}]


def test_run_grades_the_30_first_and_the_60_when_its_mark_comes(tmp_path):
    state = tmp_path / "state"
    (state / "sndk_bars").mkdir(parents=True)
    closes = [1700.0] * 90 + [1700.0 + 20.0 * (i + 1) / 60 for i in range(60)] + [1720.0] * 240
    bars = bars_from_closes(closes)
    bars_path = state / "sndk_bars" / "2026-09-18.jsonl"
    out = state / "jev"
    (out / "hour").mkdir(parents=True)
    with open(out / "hour" / "2026-09-18.jsonl", "w") as f:
        f.write(json.dumps(_rec(11, 0, 1700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.5, "flat": 0.4, "down": 0.1}, "up"), {"q1": "rising"})) + "\n")
    with open(bars_path, "w") as f:                                    # the day so far: 11:30 is in, 12:00 is not
        for b in bars[:125]:
            f.write(json.dumps(b) + "\n")
    w = run(state, out)
    assert w["new_this_run"] == 1 and w["new_by_horizon"] == {"next_30": 1, "next_60": 0}
    assert w["sums"]["next_30"]["n"] == 1 and w["sums"]["next_60"]["n"] == 0 and w["graded_runs"] == 1
    assert run(state, out)["new_this_run"] == 0                        # the 60 is still ahead: nothing new
    with open(bars_path, "w") as f:                                    # the marks pass
        for b in bars:
            f.write(json.dumps(b) + "\n")
    w = run(state, out)
    assert w["new_this_run"] == 0 and w["new_by_horizon"] == {"next_30": 0, "next_60": 1}
    assert w["sums"]["next_30"]["n"] == 1 and w["sums"]["next_60"]["n"] == 1 and w["graded_runs"] == 1
    lines = [json.loads(l) for l in (out / "grades.jsonl").read_text().splitlines() if l.strip()]
    assert [l["horizons"] for l in lines] == [["next_30"], ["next_60"]] and lines[1]["next_60"]["band"] == "up"
    assert run(state, out)["new_by_horizon"] == {"next_30": 0, "next_60": 0}   # done: never graded twice


def test_a_sum_jev_did_not_answer_is_skipped_not_graded_as_a_miss():
    bars = bars_from_closes([1700.0] * 390)
    rec = _rec(11, 0, 1700.0, _by({"flat": 1.0}, "flat"), {})                # next_60 never answered
    g = grade_one(rec, bars)
    assert "next_30" in g and "next_60" not in g and g["skipped"] == {"next_60": "no answer for this sum"}


def test_a_read_a_fraction_past_the_minute_is_graded_at_the_close_like_its_minute():
    bars = bars_from_closes([1700.0] * 390)
    rec = {"row_ts": at(15, 32, ss=0).replace(microsecond=400000).isoformat(), "spot": 1700.0, "sigma": 50.0,
           "by": _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat"), "used": {}}
    g = grade_one(rec, bars)                                          # 16:02:00.4 is a 16:02 mark: inside the grace
    assert g["horizons"] == ["next_30"] and g["band"] == "flat"


def test_a_hole_in_the_bars_around_the_mark_waits():
    bars = bars_from_closes([1700.0] * 390)
    holed = [b for b in bars if not (at(11, 40) <= at(int(b["ts"][11:13]), int(b["ts"][14:16])) <= at(11, 50))]
    rec = _rec(11, 15, 1700.0, _by({"flat": 1.0}, "flat"), {})
    assert grade_one(rec, holed) is None                              # the 11:45 mark has no bar near it: not yet
    assert grade_one(rec, bars)["band"] == "flat"


def test_a_read_on_which_every_answer_was_held_pairs_nothing():
    grades = [{"row_ts": str(i), "band": "flat", "pick": "flat", "hit": True, "brier": 0.1,
               "used": {"q1": "a"}, "fresh": {}, "next_30": {"band": "flat", "hit": True, "brier": 0.1}} for i in range(3)]
    assert weights_from(grades)["questions"] == {}


def test_a_past_day_with_no_bars_is_closed_out(tmp_path):
    state = tmp_path / "state"
    (state / "sndk_bars").mkdir(parents=True)
    out = state / "jev"
    (out / "hour").mkdir(parents=True)
    with open(out / "hour" / "2026-09-18.jsonl", "w") as f:
        f.write(json.dumps(_rec(11, 0, 1700.0, _by({"flat": 1.0}, "flat"), {"q1": "a"})) + "\n")
    w = run(state, out)
    assert w["closed_out"] == 1 and w["graded_runs"] == 0
    line = json.loads((out / "grades.jsonl").read_text().splitlines()[0])
    assert line["graded"] is False and line["reason"] == "no bars for the day"
    assert run(state, out)["closed_out"] == 0                         # never retried


def test_a_blended_sum_is_graded_beside_jevs_own_and_the_clocks():
    """The shown sum is the blend; JEV's sum and the clock's odds are scored on the same outcome, so
    the blend keeps having to beat both of its parts."""
    bars = bars_from_closes([1700.0] * 390)
    blend = {"pick": "flat", "probabilities": {"up": 0.2, "down": 0.2, "flat": 0.55, "unsure": 0.05}, "confidence": None,
             "jev": {"pick": "up", "probabilities": {"up": 0.6, "down": 0.1, "flat": 0.2, "unsure": 0.1}, "confidence": 0.7},
             "clock": {"pick": "flat", "probabilities": {"up": 0.1, "down": 0.1, "flat": 0.8}, "n": 90}}
    g = grade_one(_rec(11, 0, 1700.0, {"next_30": blend}, {}), bars)
    assert g["band"] == "flat" and g["pick"] == "flat" and g["hit"] is True
    assert g["jev_pick"] == "up" and g["jev_hit"] is False
    assert g["jev_brier"] == round(0.6 ** 2 + 0.1 ** 2 + (0.2 - 1) ** 2, 4)
    assert g["clock_brier"] == round(0.1 ** 2 + 0.1 ** 2 + (0.8 - 1) ** 2, 4)
    w = weights_from([g])
    b = w["sums"]["next_30"]["blended"]
    assert b["n"] == 1 and b["mean_brier_jev"] == g["jev_brier"] and b["mean_brier_clock"] == g["clock_brier"]
    assert b["mean_brier_blend"] == g["brier"]


def test_a_half_day_closes_out_horizons_past_one():
    """On a 13:00 close a horizon past the grace is skipped for good, never left pending for bars
    that will never be written."""
    day = "2026-11-27"
    bars = bars_from_closes([1700.0] * 210, day=day)                     # 09:30 .. 12:59
    rec = {"row_ts": at(12, 45, day=day).isoformat(), "spot": 1700.0, "sigma": 50.0, "used": {},
           "by": _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat")}
    g = grade_one(rec, bars)
    assert g == {"row_ts": rec["row_ts"], "graded": False, "reason": "every horizon ends past the close"}
    early = {**rec, "row_ts": at(12, 20, day=day).isoformat()}
    g = grade_one(early, bars)
    assert "next_30" in g and g["skipped"] == {"next_60": "ends past the close"}


def test_the_blended_tally_counts_only_blended_reads():
    """Reads from before the blend have no JEV or clock part; the side-by-side tally is over blended reads only."""
    bars = bars_from_closes([1700.0] * 390)
    blended = {"pick": "flat", "probabilities": {"up": 0.1, "down": 0.1, "flat": 0.8},
               "jev": {"pick": "up", "probabilities": {"up": 0.7, "down": 0.1, "flat": 0.2}},
               "clock": {"pick": "flat", "probabilities": {"up": 0.2, "down": 0.2, "flat": 0.6}, "n": 50}}
    plain = {"pick": "up", "probabilities": {"up": 0.9, "down": 0.05, "flat": 0.05}}
    g1 = grade_one(_rec(11, 0, 1700.0, {"next_30": blended}, {}), bars)
    g2 = grade_one(_rec(12, 0, 1700.0, {"next_30": plain}, {}), bars)
    s = weights_from([g1, g2])["sums"]["next_30"]
    assert s["n"] == 2 and s["blended"]["n"] == 1
    assert s["blended"]["mean_brier_blend"] == g1["brier"] and s["blended"]["mean_brier_jev"] == g1["jev_brier"]
