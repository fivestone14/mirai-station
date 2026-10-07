"""pool_v2's mix and update against pool_v1's own rules; the answer matrix's columns; the voices."""
from __future__ import annotations

import math

import pytest

from spx_jev import pool as pool_v1
from spx_jev.mirai_prediction import pool_v2
from spx_jev.mirai_prediction.answer_matrix import Row, column_catalog, row_answers
from spx_jev.mirai_prediction.code_features import load_catalog
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

def test_row_answers_keys_and_silence():
    out = row_answers({"TREND-01": "big up", "LEVELS-01": None}, {"q1": "yes"})
    assert out["code:TREND-01"] == "big up" and out["code:LEVELS-01"] is None and out["jev:q1"] == "yes"
    assert {c for c in out if c.startswith("code:")} == {f"code:{q['id']}" for q in load_catalog()}      # every question, answered or not
    assert not any(c.startswith(("level:", "fact:")) for c in out)


def test_column_catalog_layers_and_groups_come_from_the_catalog():
    cols = column_catalog({"q1", "q2"}, {"q1": "g"})
    assert cols["code:TREND-01"] == {"layer": 1, "group": "trend/last_30m_move", "family": "label"}
    assert cols["code:TREND-10"]["layer"] == 2 and cols["code:LEVELS-03"]["group"] == cols["code:TREND-10"]["group"]
    assert cols["jev:q1"] == {"layer": 3, "group": "jev:g", "family": "jev"} and cols["jev:q2"]["group"] == "jev:q2"
    assert not any(c.startswith(("level:", "fact:")) for c in cols)


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


def test_overlapping_reads_count_by_their_coverage():
    # 60-minute reads every 30 minutes: the first covers its whole window, each later one only its new half
    times = ["2026-10-01T10:00:00-04:00", "2026-10-01T10:30:00-04:00", "2026-10-01T11:00:00-04:00"]
    assert pool_v2.read_coverage(times, 60) == pytest.approx([1.0, 0.5, 0.5])
    assert pool_v2.read_coverage(list(reversed(times)), 60) == pytest.approx([0.5, 0.5, 1.0])    # by time, not by list order
    assert pool_v2.read_coverage(times, 30) == pytest.approx([1.0, 1.0, 1.0])                    # back to back: no overlap
    assert pool_v2.read_coverage([None, None], 60) == [1.0, 1.0]


def test_the_day_step_uses_the_counted_reads_like_pool_v1():
    times = ["2026-10-01T10:00:00-04:00", "2026-10-01T10:30:00-04:00", "2026-10-01T11:00:00-04:00"]
    reads = [(F, "flat"), (F, "up"), (F, "flat")]
    state = pool_v2.new_pool_v2("next_60", list(F))
    logs_before = dict(state["voice_log_weights"]["move"])
    c = [1.0, 0.5, 0.5]
    day_loss = {v: sum(ci * losses(floored(f[v]), y)[0] for ci, (f, y) in zip(c, reads)) / sum(c) for v in F}
    theirs, _ = pool_v1.weights_step(logs_before, day_loss, "historical_odds", state["start_weights"], sum(c))
    state, line = pool_v2.update_pool_v2_after_day(state, "2026-10-01", reads, times, 60)
    mine = pool_v2.weight_shares(state, "move")
    for v in F:
        assert mine[v] == pytest.approx(theirs[v], abs=1e-9)
    assert line["counted_reads"] == pytest.approx(2.0) and line["reads"] == 3


def test_the_freeze_watches_the_existing_voices_only():
    calm = {k: v for k, v in F.items() if k != "jev_own"}                    # no existing voice far from the odds on a flat read
    wild = {**calm, "matcher": {"up": 0.98, "flat": 0.01, "down": 0.01}}       # a newcomer hits the clip every day
    state = pool_v2.new_pool_v2("average_30", list(wild))
    for d in range(pool_v2.FREEZE_WINDOW_DAYS + 2):
        state, line = pool_v2.update_pool_v2_after_day(state, f"2026-08-{d + 1:02d}", [(wild, "flat")])
    assert state["frozen"] is None and line["applied"]                       # the newcomer never freezes the pool


def test_the_freeze_stops_the_weights_and_an_unfreeze_starts_them_again():
    awful = {**F, "jev_own": {"up": 0.98, "flat": 0.01, "down": 0.01}}       # an existing voice clipped every day
    state = pool_v2.new_pool_v2("average_30", list(F))
    for d in range(pool_v2.FREEZE_WINDOW_DAYS):
        state, line = pool_v2.update_pool_v2_after_day(state, f"2026-08-{d + 1:02d}", [(awful, "flat")])
    assert state["frozen"] and "frozen" in line
    before = dict(state["voice_log_weights"]["move"])
    state, line = pool_v2.update_pool_v2_after_day(state, "2026-09-01", [(awful, "flat")])
    assert not line["applied"] and state["voice_log_weights"]["move"] == before and "2026-09-01" in state["days_learned"]
    state = pool_v2.unfreeze_pool_v2(state)
    state, line = pool_v2.update_pool_v2_after_day(state, "2026-09-02", [(awful, "flat")])
    assert line["applied"] and state["voice_log_weights"]["move"] != before


def test_the_volume_grid_has_the_small_steps():
    from spx_jev.mirai_prediction.additive_scorer import LAYER_VOLUME_GRID, VOLUME_SEARCH_PASSES
    assert LAYER_VOLUME_GRID[:9] == [0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15]
    assert LAYER_VOLUME_GRID[9] == 0.2 and LAYER_VOLUME_GRID[-1] == 1.5 and VOLUME_SEARCH_PASSES == 3
    assert LAYER_VOLUME_GRID == sorted(set(LAYER_VOLUME_GRID))


def test_a_fit_after_the_cut_over_drops_the_jev_columns_last_answered_before_it_and_keeps_every_code_column():
    """From the cut-over the live lane asks only the judgment questions, so a pre-merge question's column goes silent; the
    matcher reads a silent column as half a mismatch, so a fit for the cut-over day or later leaves those columns out."""
    from spx_jev.mirai_prediction import answer_matrix
    from spx_jev.mirai_prediction.name_map import CUT_OVER_DAY
    assert CUT_OVER_DAY == "2026-10-08"
    last = {"price_move_5way": "2026-10-07", "news_reaction": "2026-10-08", "quiet_coiled_or_resting": "2026-10-02"}
    ids = set(last) | {"never_answered"}
    cols = column_catalog(ids, {}, last, "2026-10-07")                                   # the eve: every column still there
    assert {c for c in cols if c.startswith("jev:")} == {f"jev:{q}" for q in ids}
    for before_day in ("2026-10-08", "2026-10-09"):                                        # from the cut-over day itself
        cols = column_catalog(ids, {}, last, before_day)
        assert {c for c in cols if c.startswith("jev:")} == {"jev:news_reaction", "jev:never_answered"}
    assert {c for c in cols if c.startswith("code:")} == {f"code:{q['id']}" for q in load_catalog()}
    assert column_catalog(ids, {}) == column_catalog(ids, {}, last, "2026-10-07")      # without a day nothing is dropped


def test_the_rows_of_a_matrix_built_after_the_cut_over_lose_the_dropped_columns_too(tmp_path, monkeypatch):
    from spx_jev.mirai_prediction import answer_matrix, paths
    from spx_jev.mirai_prediction.answer_matrix import build_answer_matrix

    def row(read_id, day):
        return Row(read_id=read_id, row_ts=f"{day}T11:00:00-04:00", day=day, sum_id="average_30",
                   historical_odds_probs=F["historical_odds"], jev_own_probs=None, shown_probs=None, pool_v1_probs=None,
                   outcome="flat", learn_exclude=False)
    paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(answer_matrix, "load_base_rows", lambda *a: [row("live:a", "2026-10-07"), row("live:b", "2026-10-08")])
    monkeypatch.setattr(answer_matrix, "load_jev_answers",
                        lambda *a: ({"live:a": {"old": "up", "news_reaction": "shrugging_off"}, "live:b": {"news_reaction": "overreacting"}},
                                    {"old": "g1", "news_reaction": "judgment"}, {"old": "2026-10-07", "news_reaction": "2026-10-08"}))
    m = build_answer_matrix(tmp_path, "live", "average_30", before_day="2026-10-09")
    assert "jev:old" not in m.columns and m.columns["jev:news_reaction"]["group"] == "jev:news_reaction"   # each judgment question votes on its own
    a, b = m.rows
    assert set(a.answers) == set(b.answers) == set(m.columns)
    assert a.answers["jev:news_reaction"] == "shrugging_off" and b.answers["jev:news_reaction"] == "overreacting"
    kept = build_answer_matrix(tmp_path, "live", "average_30", before_day="2026-10-07")
    assert "jev:old" in kept.columns and kept.rows[0].answers["jev:old"] == "up"
