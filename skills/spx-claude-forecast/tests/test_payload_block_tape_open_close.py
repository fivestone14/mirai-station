"""The tape, open_signals and close_signals blocks: every value from the frozen inputs, every gap declared, no price leaks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from payload_fixtures import DAY, SIGMA, SPOT, absent_reasons, assert_no_leak, at, fake_inputs, flat_bars, make_row, prior_days, tape_fold_line, write_tape_folds

from spx_claude_forecast.payload import units
from spx_claude_forecast.payload.blocks.close_signals import build_close_signals_block
from spx_claude_forecast.payload.blocks.open_signals import build_open_signals_block
from spx_claude_forecast.payload.blocks.tape import build_tape_block

PRIOR_DAYS = prior_days(10)          # ten full sessions before the day, newest first, as Scene.prior_bars orders them


def ramp(price: float = SPOT - 149.5, step: float = 0.5, until=(14, 29)) -> list[dict]:
    """Bars rising ``step`` a minute from 09:30, ending at ``until``; the default ends on SPOT at 14:29."""
    return flat_bars(until_hh=until[0], until_mm=until[1], price=price, step=step)


def prior_sessions(price: float, step: float, days=PRIOR_DAYS) -> dict:
    """Full prior sessions of ramped bars with a trusted ruler each, as the ranks need them."""
    bars = {d: flat_bars(day=d, until_hh=15, until_mm=59, price=price, step=step) for d in days}
    rulers = {d: SimpleNamespace(points=SIGMA, estimated=False) for d in days}
    return {"prior_bars": bars, "prior_rulers": rulers}


# --- tape -----------------------------------------------------------------------------------------------------

def test_a_steady_ramp_reads_as_a_rising_one_way_day_at_the_top_of_its_range(tmp_path):
    inputs = fake_inputs(tmp_path, bars=ramp())
    tape = build_tape_block(inputs).data
    assert tape["m30_sig"] == {"v": pytest.approx(15 / SIGMA, abs=0.005)}          # 30 minutes of 0.5-point steps
    assert tape["m60_sig"] == {"v": pytest.approx(30 / SIGMA, abs=0.005)}
    assert tape["range_sig"]["v"] == pytest.approx(150.5 / SIGMA, abs=0.005)
    assert tape["range_pos"] == pytest.approx(150 / 150.5, abs=0.005)      # spot is the last close, half a point under the high
    assert tape["price_minus_close_sig"]["v"] == pytest.approx(47 / SIGMA, abs=0.005)
    assert tape["price_minus_open_sig"]["v"] == pytest.approx(147.5 / SIGMA, abs=0.005)
    assert tape["high"] == {"minus_price_sig": pytest.approx(0.5 / SIGMA, abs=0.005), "at": "14:30", "min_ago": 0}
    assert tape["low"]["at"] == "09:31" and tape["low"]["min_ago"] == 299 and tape["low"]["minus_price_sig"] < -1.9
    assert tape["path_eff_since_open"] == 1.0
    assert tape["open_crosses"] == {"v": 0}
    assert tape["last_6x5m_sig"] == [pytest.approx(2.5 / SIGMA, abs=0.001)] * 6
    halves = tape["half_hours_vs_close_sig"]
    assert len(halves) == 10 and halves == sorted(halves) and halves[-1] == tape["price_minus_close_sig"]["v"]


def test_without_prior_sessions_the_ranked_fields_carry_no_rank_and_the_session_fields_are_declared(tmp_path):
    result = build_tape_block(fake_inputs(tmp_path, bars=ramp()))
    assert "r" not in result.data["m30_sig"] and "r" not in result.data["range_sig"]
    gaps = absent_reasons(result)
    assert gaps["tape.five_session_pos"] == "needs_3_prior_sessions"
    assert gaps["tape.prior_high"] == "no_prior_session_bars"
    assert gaps["tape.rv30_vs_clock_x"] == "too_few_sessions"


def test_a_rank_scores_the_measured_value_not_the_rounded_one():
    # a move of 0.274 sig is shown as 0.27 and still beats a prior session's 0.272; a signed move ranks by its size
    assert units.ranked_sig(-0.274 * SIGMA, SIGMA, [0.272] * 10) == {"v": -0.27, "r": [10, 10]}
    assert units.ranked(57.6, [57.2] * 10, decimals=0) == {"v": 58, "r": [10, 10]}


def test_before_the_settled_open_the_open_fields_are_absent_with_their_clock(tmp_path):
    cut = at(9, 33, 10)
    result = build_tape_block(fake_inputs(tmp_path, cut=cut, bars=flat_bars(until_hh=9, until_mm=32)))
    gaps = absent_reasons(result)
    for field in ("price_minus_open_sig", "path_eff_since_open", "open_crosses"):
        assert gaps[f"tape.{field}"] == "not_until_09:35"
    assert gaps["tape.m30_sig"] == "needs_30_min_of_bars" and gaps["tape.last_6x5m_sig"] == "needs_30_min_of_bars"
    assert gaps["tape.half_hours_vs_close_sig"] == "not_until_10:00"
    assert {"price_minus_close_sig", "range_sig", "range_pos", "high", "low"} <= set(result.data)


def test_ranks_count_the_prior_sessions_the_value_beats_at_the_same_clock(tmp_path):
    inputs = fake_inputs(tmp_path, bars=ramp(), **prior_sessions(price=SPOT - 20, step=0.1))
    tape = build_tape_block(inputs).data
    assert tape["m30_sig"]["r"] == [10, 10] and tape["m60_sig"]["r"] == [10, 10]
    assert tape["range_sig"]["r"] == [10, 10] and tape["price_minus_open_sig"]["r"] == [10, 10]
    assert tape["open_crosses"] == {"v": 0, "r": [0, 10]}
    # the prior sessions' 30-minute swing is 0.1 a minute, today's 0.5: five times the usual, above every session
    assert tape["rv30_vs_clock_x"] == {"v": pytest.approx(5.0, abs=0.01), "r": [10, 10]}
    # the five prior sessions ran from SPOT-20.5 to SPOT+19.4; spot sits 20.5 points up that 39.9-point range
    assert tape["five_session_pos"] == pytest.approx(20.5 / 39.9, abs=0.01)


def test_vwap_is_the_median_of_the_last_three_rows_and_the_touch_is_judged_against_the_vwap_known_then(tmp_path):
    cut = at(14, 30, 12)
    row = make_row(cut, vwap=SPOT - 10.0)
    rows = [make_row(at(9, 30, 54), vwap=SPOT - 15.0), make_row(at(12, 0), vwap=SPOT - 20.0), make_row(at(14, 0), vwap=SPOT - 15.0), row]
    tape = build_tape_block(fake_inputs(tmp_path, cut=cut, bars=ramp(), row=row, rows_today=rows)).data
    assert tape["price_minus_vwap_sig"] == {"v": pytest.approx(15 / SIGMA, abs=0.005)}
    # the ramp passed SPOT-15 in the 13:59 and 14:00 bars, when the 14:00 row's VWAP of SPOT-15 was the one known
    assert tape["vwap_last_touch_min_ago"] == 29
    first_read = build_tape_block(fake_inputs(tmp_path, cut=cut, bars=ramp(), row=row, rows_today=[row])).data
    assert first_read["price_minus_vwap_sig"] == {"v": pytest.approx(10 / SIGMA, abs=0.005)}     # one row: its own VWAP
    no_vwap = build_tape_block(fake_inputs(tmp_path, cut=cut, bars=ramp(), row=make_row(cut, vwap=None)))
    assert absent_reasons(no_vwap)["tape.price_minus_vwap_sig"] == "no_vwap"


def test_the_half_hour_marks_are_left_out_whole_when_the_bars_start_late(tmp_path):
    late_feed = [b for b in ramp() if b["ts"][11:16] >= "10:05"]
    result = build_tape_block(fake_inputs(tmp_path, bars=late_feed))
    assert absent_reasons(result)["tape.half_hours_vs_close_sig"] == "no_bar_by_10:00"


def test_prior_high_counts_the_minutes_closed_above_it(tmp_path):
    yesterday = {"2026-10-08": flat_bars(day="2026-10-08", until_hh=15, until_mm=59, price=SPOT - 10.0)}
    tape = build_tape_block(fake_inputs(tmp_path, prior_bars=yesterday)).data
    assert tape["prior_high"] == {"minus_price_sig": pytest.approx(-9.5 / SIGMA, abs=0.005), "price_above_for_min": 300}
    below = build_tape_block(fake_inputs(tmp_path, row=make_row(at(14, 30, 12), spot=SPOT - 12.0), prior_bars=yesterday)).data
    assert below["prior_high"]["price_above_for_min"] == 0 and below["prior_high"]["minus_price_sig"] > 0


# --- open_signals ---------------------------------------------------------------------------------------------

def gap_fill_history() -> list[dict]:
    """Four print gaps: three of 0.30% (two filled by the day's low, one not) and one of 0.10%."""
    return [
        {"day": "2026-09-28", "open": 7690.0, "high": 7705.0, "low": 7685.0, "close": 7700.0},
        {"day": "2026-09-29", "open": 7723.10, "high": 7730.0, "low": 7690.0, "close": 7710.0},
        {"day": "2026-09-30", "open": 7733.13, "high": 7740.0, "low": 7720.0, "close": 7725.0},
        {"day": "2026-10-01", "open": 7748.18, "high": 7750.0, "low": 7724.0, "close": 7730.0},
        {"day": "2026-10-02", "open": 7737.73, "high": 7745.0, "low": 7735.0, "close": 7740.0},
    ]


def test_the_gap_is_named_two_ways_and_its_fill_rate_comes_from_days_with_the_same_print_gap(tmp_path):
    cut = at(14, 30, 12)
    bars = flat_bars(price=7800.0, step=0.1)                       # opens 7800.0, settles 7800.4, ends 7829.9
    row = make_row(cut, spot=7829.9, prior_close=7800.0 / 1.003)   # a 0.30% print gap up
    gap = build_open_signals_block(fake_inputs(tmp_path, cut=cut, bars=bars, row=row, daily_closes={"$SPX": gap_fill_history()})).data["gap"]
    assert gap["print_pct"] == 0.3 and gap["settled_pct"] == 0.31
    assert gap["settled_sig"] == {"v": pytest.approx(23.73 / SIGMA, abs=0.005)}
    assert gap["x_straddle"] == pytest.approx(23.73 / 21.0, abs=0.05)
    assert gap["filled"] is False and gap["kept_x"] == 2.24
    assert gap == {**gap, "fill_by_close_base_pct": 67, "fill_base_n": 3, "base_on": "print_gap_size"}


def test_the_gap_waits_for_the_settled_open_and_the_fill_rate_for_a_history(tmp_path):
    early = build_open_signals_block(fake_inputs(tmp_path, cut=at(9, 33, 10), bars=flat_bars(until_hh=9, until_mm=32)))
    assert absent_reasons(early)["open_signals.gap"] == "not_until_09:35"
    no_history = build_open_signals_block(fake_inputs(tmp_path, bars=ramp()))
    assert absent_reasons(no_history)["open_signals.gap.fill_by_close_base_pct"] == "no_daily_closes"
    assert "fill_base_n" not in no_history.data["gap"] and no_history.data["gap"]["base_on"] == "print_gap_size"


def test_kept_is_left_out_for_a_gap_inside_the_touch_tolerance(tmp_path):
    cut = at(14, 30, 12)
    hair = build_open_signals_block(fake_inputs(tmp_path, cut=cut, bars=flat_bars(price=7800.0), row=make_row(cut, spot=7810.0, prior_close=7799.5)))
    assert absent_reasons(hair)["open_signals.gap.kept_x"] == "gap_within_touch_tolerance" and "kept_x" not in hair.data["gap"]
    assert hair.data["gap"]["settled_sig"]["v"] == 0.01


def test_the_noise_band_averages_the_prior_sessions_move_from_the_open_and_places_price_against_it(tmp_path):
    cut = at(14, 30, 12)
    prior = prior_sessions(price=7800.0, step=0.1)                 # by 14:30 each had moved 29.9 points off 7800: 0.383%
    inside = build_open_signals_block(fake_inputs(tmp_path, cut=cut, bars=flat_bars(price=7800.0),
                                                  row=make_row(cut, spot=7800.0), **prior)).data["noise_band"]
    assert inside == {"band_pct": pytest.approx(0.383, abs=0.001), "side": "inside", "dist_bp": pytest.approx(-38.3, abs=0.1),
                      "open_basis": "0930_bar"}
    above = build_open_signals_block(fake_inputs(tmp_path, cut=cut, bars=flat_bars(price=7800.0, step=0.2),
                                                 row=make_row(cut, spot=7859.8), **prior)).data["noise_band"]
    assert above["side"] == "above" and above["dist_bp"] == pytest.approx(38.3, abs=0.1)
    alone = build_open_signals_block(fake_inputs(tmp_path, cut=cut, bars=flat_bars(price=7800.0), row=make_row(cut, spot=7800.0)))
    assert absent_reasons(alone)["open_signals.noise_band"] == "needs_5_prior_sessions"


def test_the_opening_imbalance_is_the_collectors_fold_at_0940(tmp_path):
    inputs = fake_inputs(tmp_path)
    assert absent_reasons(build_open_signals_block(inputs))["open_signals.opt_imbalance_10m"] == "tape_missing"
    write_tape_folds(inputs.state_dir, DAY, [
        tape_fold_line(at(9, 38, 57), tilt=0.0223, told=0.615, trades=15418),
        tape_fold_line(at(9, 39, 57), engine="spy_depth", trades=None),                 # the depth line beside the fold is not a fold
        tape_fold_line(at(9, 39, 57), tilt=-0.0412, told=0.63, trades=16002),
        tape_fold_line(at(9, 40, 58), tilt=0.0385, told=0.6, trades=18475),
    ])
    assert build_open_signals_block(inputs).data["opt_imbalance_10m"] == {"tilt": -0.041, "trades": 16002, "feed_ok": True}
    too_early = fake_inputs(tmp_path, cut=at(9, 36, 5), bars=flat_bars(until_hh=9, until_mm=35))
    assert absent_reasons(build_open_signals_block(too_early))["open_signals.opt_imbalance_10m"] == "not_until_09:40"


def test_a_stale_fold_at_0940_is_flagged_not_hidden(tmp_path):
    inputs = fake_inputs(tmp_path)
    write_tape_folds(inputs.state_dir, DAY, [tape_fold_line(at(9, 33, 0), tilt=0.1, told=0.7, trades=900)])
    assert build_open_signals_block(inputs).data["opt_imbalance_10m"] == {"tilt": 0.1, "trades": 900, "feed_ok": False}


# --- close_signals --------------------------------------------------------------------------------------------

def test_r1_runs_from_the_settled_open_to_the_close_known_at_1000(tmp_path):
    bars = ramp()
    r1 = build_close_signals_block(fake_inputs(tmp_path, bars=bars)).data["r1"]
    settled, at_ten = bars[4]["close"], bars[29]["close"]           # the 09:34 close and the 09:59 bar's close
    assert r1 == {"bp": round((at_ten / settled - 1) * 1e4, 1), "to": "10:00"}
    early = build_close_signals_block(fake_inputs(tmp_path, cut=at(9, 45), bars=flat_bars(until_hh=9, until_mm=44)))
    assert absent_reasons(early)["close_signals.r1"] == "not_until_10:00"


def test_a_1529_read_has_no_r12_or_rrod_and_the_1530_read_has_both(tmp_path):
    before = build_close_signals_block(fake_inputs(tmp_path, cut=at(15, 29, 17), bars=ramp(until=(15, 28))))
    gaps = absent_reasons(before)
    assert gaps["close_signals.r12_bp"] == "not_until_15:30" and gaps["close_signals.rrod_bp"] == "not_until_15:30"
    assert "r1" in before.data and gaps["close_signals.letf"] == "include_later"
    bars, cut = ramp(until=(15, 29)), at(15, 30, 12)
    row = make_row(cut, spot=bars[-1]["close"])
    late = build_close_signals_block(fake_inputs(tmp_path, cut=cut, bars=bars, row=row)).data
    at_three, at_half_past = bars[329]["close"], bars[359]["close"]  # the 14:59 and 15:29 bars' closes: what was known at 15:00 and 15:30
    assert late["r12_bp"] == round((at_half_past / at_three - 1) * 1e4, 1)
    assert late["rrod_bp"] == round((at_half_past / row["prior_close"] - 1) * 1e4, 1)
    assert late["late_to"] == "15:30"


def opening_row(source: str, regime: str) -> dict:
    views = {"regime": regime, "flip": SPOT + 30.0, "net_gex": -2.5e9, "net_gex_tenor": 1.23e10}
    return make_row(at(9, 30, 54), spot=SPOT - 5.0, gex_source=source, gex_views=views)


def test_gamma_open_reads_the_days_first_row_and_sets_the_rrod_gate(tmp_path):
    cut = at(14, 30, 12)
    row = make_row(cut)
    short = build_close_signals_block(fake_inputs(tmp_path, cut=cut, row=row, rows_today=[opening_row("native", "short_gamma"), row])).data
    assert short["gamma_open"] == {"regime": "short", "net_gex_usd_bn_per_1pct": {"0dte": -2.5, "0_7dte": 12.3},
                                   "price_minus_flip_sig": pytest.approx(-35 / SIGMA, abs=0.005), "book": "native", "at": "09:30"}
    assert short["rrod_gate"] == "open_short_gamma"
    long = build_close_signals_block(fake_inputs(tmp_path, cut=cut, row=row, rows_today=[opening_row("native", "long_gamma"), row])).data
    assert long["gamma_open"]["regime"] == "long" and long["rrod_gate"] == "closed_long_gamma"


def test_a_stand_in_first_row_drops_gamma_open_and_the_gate_together(tmp_path):
    cut = at(14, 30, 12)
    row = make_row(cut, gex_source="native")                       # a native book NOW does not rescue a stand-in open
    result = build_close_signals_block(fake_inputs(tmp_path, cut=cut, row=row, rows_today=[opening_row("spy_proxy×10.0346", "long_gamma"), row]))
    gaps = absent_reasons(result)
    assert gaps["close_signals.gamma_open"] == "stand_in_book" and gaps["close_signals.rrod_gate"] == "stand_in_book"
    assert "gamma_open" not in result.data and "rrod_gate" not in result.data
    unsourced = build_close_signals_block(fake_inputs(tmp_path, cut=cut))     # the fixture's row names no book source
    assert absent_reasons(unsourced)["close_signals.gamma_open"] == "no_gex_source"


# --- the real store -------------------------------------------------------------------------------------------

def test_the_real_1430_read_gives_the_tape_the_open_and_the_close_signals_with_no_price_level_or_date(real_inputs):
    tape, opened, closing = build_tape_block(real_inputs), build_open_signals_block(real_inputs), build_close_signals_block(real_inputs)
    assert {"price_minus_close_sig", "price_minus_open_sig", "m30_sig", "m60_sig", "range_sig", "range_pos", "five_session_pos",
            "price_minus_vwap_sig", "high", "low", "prior_high", "path_eff_since_open", "open_crosses", "rv30_vs_clock_x",
            "last_6x5m_sig", "half_hours_vs_close_sig"} <= set(tape.data)
    assert tape.data["price_minus_close_sig"]["r"][1] >= 10 and len(tape.data["half_hours_vs_close_sig"]) == 10
    assert 0.0 <= tape.data["range_pos"] <= 1.0 and len(tape.data["last_6x5m_sig"]) == 6
    gap = opened.data["gap"]
    assert gap["base_on"] == "print_gap_size" and gap["fill_base_n"] > 100 and 0 <= gap["fill_by_close_base_pct"] <= 100
    assert opened.data["noise_band"]["side"] in {"above", "below", "inside"}
    assert opened.data["opt_imbalance_10m"]["feed_ok"] is True
    assert closing.data["r1"]["to"] == "10:00" and closing.data["gamma_open"]["book"] == "native"
    assert closing.data["rrod_gate"] in {"open_short_gamma", "closed_long_gamma"}
    gaps = absent_reasons(closing)
    assert gaps["close_signals.r12_bp"] == "not_until_15:30" and gaps["close_signals.rrod_bp"] == "not_until_15:30"
    for name, result in (("tape", tape), ("open_signals", opened), ("close_signals", closing)):
        assert_no_leak(name, result)
