"""Step 6: the bars grade both sums at their own marks, and the grades go to the question weights."""
from __future__ import annotations

import json
import math

from conftest import DAY, at, bars_from_closes, flat_bars, make_row, write_state
from spx_jev.ask import load_questions
from spx_jev.cuts import NEXT_30_FLAT_BAND_SIGMA, NEXT_60_FLAT_BAND_SIGMA
from spx_jev.grade import grade_one as grade_in, graded_horizons, live_options, mark_at, read_anchor, realized_band, run, weights_from
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.lane import LIVE, TAPE
from spx_jev.row_adapter import labeller_row
from spx_jev.state_builder import MarketContext

SIGMA = 75.0
ANCHOR = SigmaRuler(SIGMA, "anchor")
ALLOWED = {"q1": {"rising", "falling", "going_nowhere", "unsure"}}


def _rec(hh, mm, spot, by, fresh=None):
    return {"row_ts": at(hh, mm).isoformat(), "spot": spot, "sigma": SIGMA, "by": by, "used": dict(fresh or {}), "fresh": dict(fresh or {})}


def grade_one(rec, bars, done=frozenset(), final=False):
    return grade_in(rec, bars, done, final, anchor=ANCHOR)


def _rows(sigma_after_noon=SIGMA):
    """The day's diary rows every 5 minutes from 09:31, anchored at SIGMA; from noon on the row's sigma
    ratchets up to ``sigma_after_noon`` with the live one, as the scanner's does on a spike."""
    out = []
    for m in range(1, 390, 5):
        t = at(9 + (30 + m) // 60, (30 + m) % 60)
        sigma = sigma_after_noon if t >= at(12, 0) else SIGMA
        out.append(make_row(t, 7700.0, sigma=sigma, sigma_anchor=SIGMA, sigma_live=sigma))
    return out


def _by(p30, pick30, p60=None, pick60=None):
    by = {"next_30": {"pick": pick30, "probabilities": p30, "confidence": 0.5}}
    if p60:
        by["next_60"] = {"pick": pick60, "probabilities": p60, "confidence": 0.5}
    return by


def _climb():
    """Flat to 11:00, then a climb of 30 points (0.4 sigma) by 12:00, then flat."""
    return bars_from_closes([7700.0] * 90 + [7700.0 + 30.0 * (i + 1) / 60 for i in range(60)] + [7730.0] * 240)


def test_the_horizons_are_the_measured_spx_bands():
    assert LIVE.horizons == {"next_30": (30, NEXT_30_FLAT_BAND_SIGMA), "next_60": (60, NEXT_60_FLAT_BAND_SIGMA)}
    assert realized_band(0.08, 0.07) == "up" and realized_band(-0.08, 0.07) == "down" and realized_band(0.07, 0.07) == "flat"


def test_grade_one_reads_both_horizons_and_lifts_the_primary():
    rec = _rec(11, 0, 7700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.2, "flat": 0.7, "down": 0.1}, "flat"), {"q1": "rising"})
    g = grade_one(rec, _climb())
    assert g["next_30"]["band"] == "up" and g["next_30"]["realized_sigma"] == 0.2 and g["next_30"]["hit"] is True
    assert g["next_60"]["band"] == "up" and g["next_60"]["realized_sigma"] == 0.4 and g["next_60"]["hit"] is False
    assert g["band"] == "up" and g["brier"] == round(0.4 ** 2 + 0.3 ** 2 + 0.1 ** 2, 4) and g["fresh"] == {"q1": "rising"}


def test_each_horizon_is_graded_at_its_own_mark_and_one_past_the_close_is_skipped():
    bars = flat_bars(390)
    rec = _rec(11, 0, 7700.0, _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat"))
    assert grade_one(rec, bars[:80]) is None
    first = grade_one(rec, bars[:120])
    assert first["horizons"] == ["next_30"] and first["pending"] == ["next_60"]
    assert grade_one(rec, bars, {"next_30"})["horizons"] == ["next_60"]
    late = grade_one(_rec(15, 20, 7700.0, _by({"flat": 1.0}, "flat", {"flat": 1.0}, "flat")), bars)
    assert late["skipped"] == {"next_60": "ends past the close"} and late["band"] == "flat"
    assert grade_one(_rec(15, 45, 7700.0, _by({"flat": 1.0}, "flat")), bars) == {
        "row_ts": at(15, 45).isoformat(), "graded": False, "reason": "every horizon ends past the close"}
    assert graded_horizons([{"row_ts": "b", "graded": False}])["b"] == {"next_30", "next_60"}


def test_mark_at_is_the_minute_plus_the_horizon_or_the_closing_bar():
    assert mark_at(f"{DAY}T10:02:41-04:00", 30).isoformat() == f"{DAY}T10:32:00-04:00"
    assert mark_at(f"{DAY}T15:31:56-04:00", 30).isoformat() == f"{DAY}T16:00:00-04:00"
    assert mark_at(f"{DAY}T15:31:56-04:00", 60) is None
    assert mark_at("2026-11-27T12:31:00-05:00", 30).isoformat() == "2026-11-27T13:00:00-05:00"


def test_a_hole_waits_today_and_closes_out_on_a_finished_day():
    bars = flat_bars(390)
    holed = [b for b in bars if not (at(11, 40).isoformat() <= b["ts"] <= at(11, 50).isoformat())]
    rec = _rec(11, 15, 7700.0, _by({"flat": 1.0}, "flat"))
    assert grade_one(rec, holed) is None
    g = grade_one(rec, holed, final=True)
    assert g["horizons"] == [] and g["skipped"]["next_30"].startswith("halted window")


def test_a_blended_sum_is_graded_beside_jevs_own_and_the_clocks():
    blend = {"pick": "flat", "probabilities": {"up": 0.2, "down": 0.2, "flat": 0.55, "unsure": 0.05},
             "jev": {"pick": "up", "probabilities": {"up": 0.6, "down": 0.1, "flat": 0.2, "unsure": 0.1}},
             "clock": {"pick": "flat", "probabilities": {"up": 0.1, "down": 0.1, "flat": 0.8}, "n": 90}}
    g = grade_one(_rec(11, 0, 7700.0, {"next_30": blend}), flat_bars(390))
    assert g["hit"] is True and g["jev_hit"] is False and g["clock_brier"] == round(0.01 + 0.01 + 0.04, 4)
    b = weights_from([g], ALLOWED)["sums"]["next_30"]["blended"]
    assert b["n"] == 1 and b["mean_brier_blend"] == g["brier"] and b["mean_brier_jev"] == g["jev_brier"]


def test_every_weight_is_one_and_an_event_read_never_reaches_them():
    bars = flat_bars(390)
    ev = {"within_30": True, "sentence": "a scheduled event is ahead"}
    g1 = grade_one({**_rec(13, 32, 7700.0, _by({"flat": 1.0}, "flat"), {"q1": "rising"}), "event": ev}, bars)
    g2 = grade_one(_rec(11, 0, 7700.0, _by({"flat": 1.0}, "flat"), {"q1": "falling", "q_retired": "x"}), bars)
    w = weights_from([g1, g2], ALLOWED)
    # the live lane learns the loop, on the end price and, its switch on, on the average price, reported beside it
    assert g1["event_within_30"] is True and w["method"] == "pool_v1" and w["pool_integral"]["method"] == "pool_v1_integral"
    assert weights_from([g1, g2], ALLOWED, TAPE)["method"] == "neutral"
    assert w["questions"] == {"q1": {"weight": 1.0, "n": 1, "in_step_3": True, "why": w["questions"]["q1"]["why"]}}
    assert w["sums"]["next_30"]["n"] == 2 and w["sums"]["next_30"]["event_reads"]["n"] == 1


def test_run_appends_grades_writes_weights_and_logs_once(tmp_path):
    state = write_state(tmp_path, DAY, _rows(), _climb())
    out = state / "spx_jev"
    (out / "hour").mkdir(parents=True)
    rec = _rec(11, 0, 7700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.5, "flat": 0.4, "down": 0.1}, "up"), {"q1": "rising"})
    (out / "hour" / f"{DAY}.jsonl").write_text(json.dumps(rec) + "\n" + json.dumps(_rec(15, 45, 7700.0, _by({"flat": 1.0}, "flat"))) + "\n"
                                               + json.dumps(rec) + "\n")
    w = run(state, out, ALLOWED)
    assert w["graded_runs"] == 1 and w["new_this_run"] == 1 and w["closed_out"] == 1
    assert json.loads((out / "weights.json").read_text())["questions"]["q1"] == w["questions"]["q1"]
    assert run(state, out, ALLOWED)["new_this_run"] == 0
    log = [json.loads(l) for l in (out / "weights_log.jsonl").read_text().splitlines() if l.strip()]
    assert len(log) == 1 and log[0]["changes"] == [{"question": "q1", "before": None, "after": 1.0, "n": 1, "in_step_3": True}]


def test_a_past_day_with_no_bars_is_closed_out(tmp_path):
    out = tmp_path / "spx_jev"
    (out / "hour").mkdir(parents=True)
    (out / "hour" / f"{DAY}.jsonl").write_text(json.dumps(_rec(11, 0, 7700.0, _by({"flat": 1.0}, "flat"))) + "\n")
    w = run(tmp_path, out, ALLOWED)
    assert w["closed_out"] == 1 and json.loads((out / "grades.jsonl").read_text())["reason"] == "no bars for the day"


def test_only_live_questions_and_their_current_options_are_weighed():
    allowed = live_options(load_questions(LIVE.questions, LIVE.key))
    assert "flow_vs_price" not in allowed and "news_headline" not in allowed      # shadow, dark
    assert allowed["leg_vs_day_side"] == {"quiet", "quiet_leg_day_moved", "leg_with_day", "leg_against_day", "leg_on_flat_day"}
    assert allowed["gap_fill_next_hour"] == {"true", "false"}
    assert allowed["price_move_5way"] == {"strong_down", "down", "nowhere", "up", "strong_up"}      # a score: its level names


def test_a_score_answer_reaches_the_weights_like_a_choice():
    allowed = live_options(load_questions(LIVE.questions, LIVE.key))
    g = grade_one(_rec(11, 0, 7700.0, _by({"flat": 1.0}, "flat"), {"price_move_5way": "up", "leg_vs_day_side": "quiet"}), flat_bars(390))
    w = weights_from([g], allowed, TAPE)["questions"]
    assert w["price_move_5way"]["n"] == 1 and w["leg_vs_day_side"]["n"] == 1


def test_the_morning_anchor_grades_and_a_mid_day_ratchet_changes_nothing(tmp_path):
    """A noon read on a day whose row sigma ratcheted from 75 to 150 at noon: the record carries the
    ratcheted sigma, the grade the morning anchor's, the same as on a day that never ratcheted. On the
    record's sigma the 15-point climb would have read 0.1 sigma, flat."""
    climb = bars_from_closes([7700.0] * 150 + [7700.0 + 15.0 * (i + 1) / 30 for i in range(30)] + [7715.0] * 210)
    rec = {**_rec(12, 0, 7700.0, _by({"up": 0.6, "flat": 0.3, "down": 0.1}, "up", {"up": 0.5, "flat": 0.4, "down": 0.1}, "up")), "sigma": 150.0}
    lines = []
    for name, rows in (("calm", _rows()), ("spike", _rows(sigma_after_noon=150.0))):
        state = write_state(tmp_path / name, DAY, rows, climb)
        out = state / "spx_jev"
        (out / "hour").mkdir(parents=True)
        (out / "hour" / f"{DAY}.jsonl").write_text(json.dumps(rec) + "\n")
        run(state, out, ALLOWED)
        lines.append(json.loads((out / "grades.jsonl").read_text().splitlines()[0]))
    calm, spike = lines
    assert spike["next_30"] == calm["next_30"] and spike["next_60"] == calm["next_60"]
    assert spike["next_30"]["realized_sigma"] == 0.2 and spike["band"] == "up"         # 15 points in 75, past the flat band
    assert spike["anchor"] == {"points": SIGMA, "source": "anchor"}


def test_the_anchor_is_what_the_read_could_know():
    rows = [labeller_row(r) for r in _rows()]
    assert read_anchor(rows, flat_bars(390), None, at(12, 0).isoformat()) == ANCHOR
    late = [labeller_row(make_row(at(9, 45), 7700.0, sigma=80.0, sigma_anchor=90.0, sigma_live=77.0))]
    assert read_anchor(late, flat_bars(390), None, at(12, 0).isoformat()) == SigmaRuler(77.0, "live")   # the guard: 09:40
    assert read_anchor(rows, flat_bars(390), None, at(9, 30).isoformat()) is None                         # no row yet
    g = grade_in(_rec(11, 0, 7700.0, _by({"flat": 1.0}, "flat")), flat_bars(390))
    assert g["horizons"] == [] and g["skipped"]["next_30"].startswith("no morning anchor")              # closed for good


def test_the_vix_anchor_waits_for_the_settled_open():
    """No row before the 09:40 guard and no live sigma: the anchor is the VIX on the settled open, known
    only once the 09:34 bar has finished."""
    late = [labeller_row(make_row(at(9, 45), 7700.0, sigma_live=None))]
    market = MarketContext({"$VIX": [(at(9, 31), 16.0)]})
    assert read_anchor(late, flat_bars(390), market, at(9, 34, ss=30).isoformat()) is None
    assert read_anchor(late, flat_bars(390), market, at(9, 35).isoformat()) == SigmaRuler(7700.0 * 16.0 / 100.0 / math.sqrt(252), "vix")
