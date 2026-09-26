"""The tape lane's grader: dollars against the record's bands, the exact bar, day-end close-outs,
the pair gap and the day-block shuffle. The live sums are never touched."""
from __future__ import annotations

import random
from dataclasses import replace

from conftest import at, bars_from_closes
from sndk_jev.grade import CUT_AFTER_PASSED, grade_one, shuffle_p99, weights_from
from sndk_jev.hour import views_of
from sndk_jev.lane import LIVE, TAPE

FIVE = {"down_big": 0.05, "down_small": 0.1, "flat": 0.5, "up_small": 0.2, "up_big": 0.1, "unsure": 0.05}
BAND = {"flat_dollars": 8.4, "big_dollars": 16.8, "flat_units": 0.35, "big_units": 0.7}
RULER = {"unit_dollars": 24.0, "unit_sigma": 0.48, "slices_used": 3, "source": "tape"}


def _rec(hh, mm, spot=1700.0, probs=FIVE, pick="flat", fresh=None, band=BAND):
    rec = {"row_ts": at(hh, mm).isoformat(), "spot": spot, "sigma": 50.0, "lane": "tape", "ruler": RULER,
           "by": {"next_10": {"pick": pick, "probabilities": probs, "confidence": 0.5, "views": views_of(probs)}},
           "used": fresh or {}, "fresh": fresh or {}}
    if band is not None:
        rec["band"] = band
    return rec


def _live_rec(hh, mm):
    return {"row_ts": at(hh, mm).isoformat(), "spot": 1700.0, "sigma": 50.0, "used": {},
            "by": {"next_30": {"pick": "flat", "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.2}}}}


def test_the_lane_grader_never_touches_the_live_sums():
    bars = bars_from_closes([1700.0] * 390)
    g = grade_one(_live_rec(11, 0), bars, lane=TAPE)                    # a live record has no next_10: nothing for the lane
    assert g["graded"] is False and "next_30" not in g
    g = grade_one(_rec(11, 0), bars, lane=TAPE)
    assert g["horizons"] == ["next_10"] and "next_30" not in g and "next_60" not in g
    w = weights_from([g], lane=TAPE)
    assert list(w["sums"]) == ["next_10"] and w["primary"] == "next_10" and w["lane"] == "tape"
    assert grade_one(_rec(11, 0), bars)["graded"] is False                # and the live grader has nothing for a lane record


def test_a_lane_record_is_graded_in_dollars_with_a_direction_and_a_size():
    """Flat until 11:00, then 2 points a minute: 20 points by 11:10, past the big band of $16.80."""
    closes = [1700.0] * 90 + [1700.0 + 2.0 * (i + 1) for i in range(10)] + [1720.0] * 290
    bars = bars_from_closes(closes)
    g = grade_one(_rec(11, 0, fresh={"q": "wide"}), bars, lane=TAPE)
    h = g["next_10"]
    assert (h["realized_dollars"], h["realized_units"], h["band"], h["direction"], h["size"]) == (20.0, round(20 / 24, 3), "up_big", "up", "big")
    assert h["pick"] == "flat" and h["hit"] is False and h["p_band"] == 0.1
    assert h["brier"] == round(0.05 ** 2 + 0.1 ** 2 + 0.5 ** 2 + 0.2 ** 2 + (0.1 - 1) ** 2, 4)      # five outcomes; unsure's 0.05 is simply lost
    assert h["direction_pick"] == "flat" and h["direction_hit"] is False and h["direction_brier"] == round((0.3 - 1) ** 2 + 0.5 ** 2 + 0.15 ** 2, 4)
    assert h["size_pick"] == "small" and h["size_hit"] is False and h["size_brier"] == round((0.15 - 1) ** 2 + 0.8 ** 2, 4)
    for k in ("realized_dollars", "band", "direction", "size", "hit", "brier", "direction_brier", "size_brier"):
        assert g[k] == h[k]                                              # the primary, flat on top
    assert "realized_sigma" not in g
    # down 5 points is inside the $8.40 flat band, 10 is small, 17 is past the $16.80 big band
    for move, want in ((-5.0, ("flat", "flat", "small")), (-10.0, ("down_small", "down", "small")), (-17.0, ("down_big", "down", "big"))):
        b = bars_from_closes([1700.0] * 90 + [1700.0 + move] * 300)
        h = grade_one(_rec(11, 0), b, lane=TAPE)["next_10"]
        assert (h["band"], h["direction"], h["size"]) == want, move
    s = weights_from([g], lane=TAPE)["sums"]["next_10"]
    assert s["bands"] == {"up_big": 1} and s["views"]["direction"] == {"n": 1, "hit_rate": 0.0, "largest_class_hit_rate": 1.0, "mean_brier": g["direction_brier"], "outcomes": {"up": 1}}
    assert s["views"]["size"]["outcomes"] == {"big": 1}


def test_the_lane_needs_the_exact_bar_and_closes_out_when_a_finished_days_bars_stop():
    bars = bars_from_closes([1700.0] * 390)
    rec = _rec(11, 0)
    without_mark = [b for b in bars if b["ts"][11:16] != "11:09"]         # the 11:10 mark's own bar is missing
    assert grade_one(rec, without_mark, lane=TAPE) is None                # the lane waits for the exact bar ...
    assert grade_one({**rec, "by": {"next_30": rec["by"]["next_10"]}}, without_mark)["band"] == "flat"   # ... where the live lane takes one two minutes early
    assert grade_one(rec, bars[:95], lane=TAPE) is None                   # today, bars to 11:05: the mark is still ahead
    g = grade_one(rec, bars[:95], final=True, lane=TAPE)                  # a finished day whose bars stopped at 11:05: it will never come
    assert g["horizons"] == [] and g["skipped"] == {"next_10": "halted window: no bar at the mark on a finished day"}
    assert grade_one(_live_rec(11, 0), bars[:95], final=True) is None     # the live lane keeps waiting on a day whose bars stop early
    g = grade_one(_rec(11, 0, band=None), bars, lane=TAPE)                # no unit was measured at the read: closed out, never retried
    assert g["horizons"] == [] and g["reason"] == "next_10: no band on the record"


def _grades(days, reads_per_day=12, picks=None):
    """One graded lane read every 5 minutes from 09:35, ``reads_per_day`` a day, with the outcomes drawn
    at random (seeded) and each question's picks from ``picks(i, band)``."""
    rng = random.Random(7)
    out = []
    for d in range(days):
        day = f"2026-{9 + d // 28:02d}-{1 + d % 28:02d}"
        for i in range(reads_per_day):
            band = rng.choice(["down_big", "down_small", "flat", "flat", "up_small", "up_big"])
            hh, mm = divmod(9 * 60 + 35 + 5 * i, 60)
            out.append({"row_ts": at(hh, mm, day=day).isoformat(), "band": band, "pick": "flat", "hit": band == "flat", "brier": 0.5,
                        "fresh": picks(len(out), band) if picks else {"q": "x"}, "next_10": {"band": band, "hit": band == "flat", "brier": 0.5}})
    return out


def test_a_question_pairs_only_with_reads_ten_minutes_apart():
    grades = _grades(1)                                                  # 09:35, 09:40 ... 10:30
    assert weights_from(grades, lane=TAPE)["questions"]["q"]["n"] == 6    # 09:35, 09:45, ..., 10:25
    assert weights_from(grades, lane=replace(TAPE, pair_gap_min=0))["questions"]["q"]["n"] == 12
    assert weights_from(grades)["questions"]["q"]["n"] == 12              # the live lane has no gap


def test_the_shuffle_zeroes_a_lucky_question_and_nobody_is_cut_until_three_have_passed():
    """q_tell picks the outcome; q_lucky's picks are the outcomes shuffled, so its information is
    whatever chance gives. The day-block shuffle keeps the first and zeroes the second."""
    outcomes = [g["band"] for g in _grades(20)]
    lucky = random.Random(3).sample(outcomes, len(outcomes))
    grades = _grades(20, picks=lambda i, band: {"q_tell": band, "q_lucky": lucky[i], "q_const": "same"})
    w = weights_from(grades, lane=TAPE)
    assert w["questions"]["q_tell"]["n"] == 120 and w["questions"]["q_tell"]["weight"] == 1.0 and w["passed"] == 1
    lucky_q = w["questions"]["q_lucky"]
    assert lucky_q["weight"] == 0.0 and lucky_q["mi"] > 0 and lucky_q["shuffle_p99"] > lucky_q["mi"] and "luck" in lucky_q["why"]
    assert w["questions"]["q_const"]["weight"] == 0.0
    assert all(q["in_step_3"] for q in w["questions"].values()) and "until 3 questions have passed" in lucky_q["why"]
    # three telling questions pass: from then on the cut applies
    grades = _grades(20, picks=lambda i, band: {"q_tell": band, "q_tell2": band, "q_tell3": band, "q_lucky": lucky[i]})
    w = weights_from(grades, lane=TAPE)
    assert w["passed"] == CUT_AFTER_PASSED == 3 and w["questions"]["q_lucky"]["in_step_3"] is False
    assert (w["pair_gap_min"], w["shuffles"], w["min_graded"]) == (10, 1000, 120)
    # the live lane never shuffles: a lucky question there is scored against the best, as before
    live = weights_from(grades, lane=replace(LIVE, min_graded=120))
    assert "shuffle_p99" not in live["questions"]["q_lucky"] and live["questions"]["q_lucky"]["weight"] > 0


def test_the_shuffle_moves_whole_days_and_is_repeatable():
    pairs = [("a", "up"), ("a", "up"), ("b", "down"), ("b", "down")]
    days = ["d1", "d1", "d2", "d2"]
    assert shuffle_p99(pairs, days, 50) == shuffle_p99(pairs, days, 50)
    # with one day there is nothing to move: every shuffle is the real order, so the real MI never clears it
    one_day = shuffle_p99(pairs, ["d1"] * 4, 50)
    from sndk_jev.grade import mutual_information
    assert one_day == mutual_information(pairs)
