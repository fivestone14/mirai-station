"""pool_v2's mix and update against pool_v1's own rules; the answer matrix's labels and walk-forward; the voices."""
from __future__ import annotations

import math

import pytest

from spx_jev import pool as pool_v1
from spx_jev.mirai_prediction import pool_v2
from spx_jev.mirai_prediction.answer_matrix import Row, categorical_label_names, level_days, rank_label, row_answers
from spx_jev.mirai_prediction.name_map import rename_voice, store_path
from spx_jev.mirai_prediction.voices import existing_voice_forecasts
from spx_jev.scores import floored, losses

F = {"historical_odds": {"up": 0.2, "flat": 0.6, "down": 0.2}, "jev_own": {"up": 0.5, "flat": 0.2, "down": 0.3},
     "blend_50_50": {"up": 0.35, "flat": 0.4, "down": 0.25}, "additive_scorer": {"up": 0.25, "flat": 0.5, "down": 0.25},
     "matcher": {"up": 0.3, "flat": 0.5, "down": 0.2}, "jev_corrected": {"up": 0.22, "flat": 0.58, "down": 0.2}}


def test_start_weights_give_learners_three_percent_and_never_zero():
    w = pool_v2.start_weights(list(F))
    assert sum(w.values()) == pytest.approx(1.0)
    for v in ("additive_scorer", "matcher", "jev_corrected"):
        assert w[v] == pytest.approx(pool_v2.NEW_VOICE_START_WEIGHT)
    assert all(x > 0 for x in w.values())
    assert w["blend_50_50"] > w["historical_odds"]      # pool_v1's starting shape is kept among the existing voices


def test_the_pools_are_never_mixed_as_voices():
    state = pool_v2.new_pool_v2("average_30", list(F) + ["pool_v1"])
    assert "pool_v1" not in state["start_weights"]
    mixed = pool_v2.mix_pool_v2(state, {**F, "pool_v1": {"up": 0.9, "flat": 0.05, "down": 0.05}})
    assert mixed["up"] < 0.5


def test_mix_follows_pool_v1s_rule():
    state = pool_v2.new_pool_v2("average_30", list(F))
    mine = pool_v2.mix_pool_v2(state, F)
    logs = {"M": state["voice_log_weights"]["move"], "D": state["voice_log_weights"]["direction"]}
    theirs = floored(pool_v1.mixed({v: floored(p) for v, p in F.items()}, logs))
    for k in mine:
        assert mine[k] == pytest.approx(theirs[k], abs=1e-9)


def test_an_absent_voice_is_asleep_for_the_read():
    state = pool_v2.new_pool_v2("average_30", list(F))
    awake = {k: v for k, v in F.items() if k != "matcher"}
    mixed = pool_v2.mix_pool_v2(state, awake)
    assert mixed is not None
    state, line = pool_v2.update_pool_v2_after_day(state, "2026-10-01", [(awake, "flat")] * 5)
    assert line["applied"] and "matcher" not in line["steps"]            # asleep on every read: no step of its own
    assert all(step["move"]["reads"] == 5 for step in line["steps"].values())


def test_a_voice_asleep_on_some_reads_is_measured_against_the_odds_on_its_own_reads():
    state = pool_v2.new_pool_v2("average_30", list(F))
    # the matcher speaks on one read only, and there it matches the odds exactly: its gap must be zero, whatever the
    # other reads (where it was asleep) cost the odds
    awake = {**F, "matcher": F["historical_odds"]}
    asleep = {k: v for k, v in F.items() if k != "matcher"}
    state, line = pool_v2.update_pool_v2_after_day(state, "2026-10-01", [(awake, "flat"), (asleep, "up"), (asleep, "up")])
    assert line["steps"]["matcher"]["move"] == {"gap": 0.0, "clipped": False, "reads": 1}


def test_a_voice_joining_later_keeps_the_starting_weights_summing_to_one():
    state = pool_v2.new_pool_v2("average_30", ["historical_odds", "jev_own"])
    state = pool_v2.add_missing_voices(state, ["blend_50_50", "matcher"])
    assert sum(state["start_weights"].values()) == pytest.approx(1.0)
    assert state["start_weights"]["matcher"] < state["start_weights"]["blend_50_50"]


def test_update_matches_pool_v1s_weights_step_on_one_day():
    state = pool_v2.new_pool_v2("average_30", list(F))
    reads = [(F, "flat"), (F, "up"), (F, "flat")]
    logs_before = dict(state["voice_log_weights"]["move"])
    # pool_v1's rule on the same inputs: day-mean move losses, step against the reference, Fixed-Share toward the prior
    day_loss = {v: sum(losses(floored(p), y)[0] for f, y in reads for p in [f[v]]) / len(reads) for v in F}
    theirs, _ = pool_v1.weights_step(logs_before, day_loss, "historical_odds", state["start_weights"], len(reads))
    state, line = pool_v2.update_pool_v2_after_day(state, "2026-10-01", reads)
    mine = pool_v2.weight_shares(state, "move")
    for v in F:
        assert mine[v] == pytest.approx(theirs[v], abs=1e-9)
    assert line["applied"] and line["reads"] == 3


def test_a_day_is_learned_once():
    state = pool_v2.new_pool_v2("average_30", list(F))
    state, first = pool_v2.update_pool_v2_after_day(state, "2026-10-01", [(F, "flat")])
    state, again = pool_v2.update_pool_v2_after_day(state, "2026-10-01", [(F, "flat")])
    assert first["applied"] and not again["applied"]


def test_zero_start_weight_is_refused():
    with pytest.raises(ValueError):
        pool_v2.start_weights(["additive_scorer"])       # learners alone: no existing voice to carry the rest


# ---------------------------------------------------------------- answer matrix

def test_rank_label_uses_only_earlier_days():
    history = [(f"2026-09-{d:02d}T10:00:00", float(d)) for d in range(1, 29)]
    days = level_days({"x": history})["x"]
    assert rank_label(history, days, "2026-09-29T10:00:00", 1.0) == "low"
    assert rank_label(history, days, "2026-09-29T10:00:00", 28.0) == "high"
    assert rank_label(history, days, "2026-09-29T10:00:00", 14.0) == "middle"
    assert rank_label(history, days, "2026-09-05T10:00:00", 2.0) is None           # too few earlier values
    assert rank_label(history, days, "2026-09-10T10:00:00", 100.0) is None         # still under the minimum history
    # the same day's earlier reads never count: a 09:00 read on 09-28 sees only 27 values, the 10:00 read's included
    two_a_day = sorted(history + [(f"2026-09-{d:02d}T09:00:00", 100.0) for d in range(1, 29)])
    days = level_days({"x": two_a_day})["x"]
    assert rank_label(two_a_day, days, "2026-09-28T09:30:00", 50.0) == rank_label(two_a_day, days, "2026-09-28T15:30:00", 50.0)


def test_rank_label_keeps_only_the_trailing_sessions():
    from spx_jev.mirai_prediction.answer_matrix import RANK_HISTORY_SESSIONS
    old = [(f"2025-01-{d:02d}T10:00:00", 1000.0) for d in range(1, 29)]                 # far earlier, huge values
    recent = [(f"2026-0{m}-{d:02d}T10:00:00", float(d)) for m in (7, 8, 9) for d in range(1, 29)]
    history = old + recent
    days = level_days({"x": history})["x"]
    assert len(recent) // 28 * 28 > 0 and len({ts[:10] for ts, _ in recent}) > RANK_HISTORY_SESSIONS
    assert rank_label(history, days, "2026-10-01T10:00:00", 27.0) == "high"           # the old 1000s are out of the window


def test_categorical_labels_are_the_ones_with_few_distinct_texts():
    labels = {f"r{i}": {"gap": "no gap" if i % 2 else "gap up", "note": f"text {i}"} for i in range(30)}
    assert categorical_label_names(labels) == {"gap"}


def test_row_answers_keys_and_silence():
    history = {"$VIX": [(f"2026-09-{d:02d}T10:00:00", 15.0 + d) for d in range(1, 29)]}
    out = row_answers("2026-09-30T10:00:00", {"q1": "yes"}, {"gap": "no gap"}, {"gap", "burst"}, {"$VIX": 40.0}, history, level_days(history))
    assert out == {"fact:gap": "no gap", "fact:burst": None, "level:$VIX": "high", "jev:q1": "yes"}


def test_store_names_map_to_the_live_folders(tmp_path):
    assert store_path(tmp_path, "graded_results").name == "average_grades"
    assert store_path(tmp_path, "forecasts_at_read_time").name == "calls"
    with pytest.raises(KeyError):
        store_path(tmp_path, "no_such_table")


def test_voice_names_follow_the_diagram():
    assert rename_voice("baseline") == "historical_odds"
    assert rename_voice("jev_share_0.4") == "jev_mix_40_percent"
    assert rename_voice("questions") == "question_tilt"


def test_existing_voices_for_an_end_price_sum_come_from_pool_v1s_snapshot():
    row = Row(read_id="live:t", row_ts="t", day="2026-10-01", sum_id="next_60", historical_odds_probs=F["historical_odds"],
              jev_own_probs=F["jev_own"], shown_probs=F["blend_50_50"], pool_v1_probs=None, outcome=None, learn_exclude=False)
    snap = {"next_60": {"experts": {"baseline": F["historical_odds"], "jev_share_0.2": F["blend_50_50"], "questions": F["matcher"]},
                        "pool": F["additive_scorer"]}}
    out = existing_voice_forecasts(row, snap)
    # JEV's own call is already in the snapshot (jev_share_1.0), so the row's copy is not added a second time
    assert set(out) == {"historical_odds", "jev_mix_20_percent", "question_tilt", "pool_v1"}


def test_existing_voices_for_the_average_sum_come_from_the_row():
    row = Row(read_id="live:t", row_ts="t", day="2026-10-01", sum_id="average_30", historical_odds_probs=F["historical_odds"],
              jev_own_probs=F["jev_own"], shown_probs=F["blend_50_50"], pool_v1_probs=None, outcome=None, learn_exclude=False)
    assert set(existing_voice_forecasts(row, None)) == {"historical_odds", "jev_own", "blend_50_50"}
