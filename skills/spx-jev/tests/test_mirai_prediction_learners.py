"""The three learners on synthetic answer matrices: pushes, matching, correction. No host state."""
from __future__ import annotations

import random

import pytest

from spx_jev.mirai_prediction.additive_scorer import (ANSWER_PRIOR_READS, AdditiveScorerFit, fit_additive_scorer, fit_pushes,
                                                       forecast_with_additive_scorer, layer_sums, logit)
from spx_jev.mirai_prediction.answer_matrix import AnswerMatrix, Row
from spx_jev.mirai_prediction.code_features import PROVIDER_QUESTIONS, column_name, load_catalog
from spx_jev.mirai_prediction.jev_corrected import MIN_GRADED_ROWS, correct_jev_call, fit_jev_corrected, forecast_with_jev_corrected
from spx_jev.mirai_prediction.matcher import (MATCHER_NEIGHBOR_COUNT, fit_matcher, forecast_with_matcher, mismatch)
from spx_jev.scores import floored, log_loss

ODDS = {"up": 0.2, "flat": 0.55, "down": 0.25}
DAYS = [f"2026-09-{d:02d}" for d in range(1, 29)] + [f"2026-10-{d:02d}" for d in range(1, 10)]


def make_row(i: int, day: str, answers: dict, outcome: str | None, jev=None, odds=ODDS, exclude=False) -> Row:
    ts = f"{day}T{10 + i % 6:02d}:{(i * 7) % 60:02d}:00-04:00"
    return Row(read_id=f"live:{ts}", row_ts=ts, day=day, sum_id="average_30", historical_odds_probs=dict(odds),
               jev_own_probs=jev, shown_probs=None, pool_v1_probs=None, outcome=outcome, learn_exclude=exclude, answers=answers)


def make_matrix(rows: list[Row], columns: dict, before_day: str = "2026-10-10") -> AnswerMatrix:
    return AnswerMatrix(lane="live", sum_id="average_30", built_for_day=before_day, rows=rows, columns=columns)


COLUMNS = {"code:A": {"layer": 2, "group": "code:A", "family": "t"}, "code:B": {"layer": 1, "group": "code:B", "family": "m"},
           "jev:q": {"layer": 3, "group": "jev:q", "family": "jev"}}


def signal_rows(n: int = 240, seed: int = 1) -> list[Row]:
    """'code:A' = "hot" precedes a move 80% of the time, "cold" 20%; the other columns are noise."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        day = DAYS[i * len(DAYS) // n]
        hot = rng.random() < 0.5
        moved = rng.random() < (0.8 if hot else 0.2)
        outcome = ("up" if rng.random() < 0.5 else "down") if moved else "flat"
        answers = {"code:A": "hot" if hot else "cold", "code:B": rng.choice(["low", "middle", "high"]), "jev:q": rng.choice(["yes", "no"])}
        rows.append(make_row(i, day, answers, outcome, jev={"up": 0.4, "flat": 0.2, "down": 0.4}))
    return rows


# ---------------------------------------------------------------- additive scorer

def test_push_is_distance_from_the_answers_own_normal():
    rows = [make_row(i, DAYS[i % 5], {"code:A": "hot"}, "up" if i % 4 else "flat") for i in range(40)]
    pushes, bases = fit_pushes(rows, "move")
    n, k = 40, 30
    base = 1 - ODDS["flat"]
    blend = (k + ANSWER_PRIOR_READS * base) / (n + ANSWER_PRIOR_READS)
    assert pushes["code:A"]["hot"] == pytest.approx(logit(blend) - logit(base))
    assert bases["code:A"]["hot"] == pytest.approx(base)


def test_volume_zero_reproduces_the_historical_odds():
    rows = signal_rows()
    fit = fit_additive_scorer(make_matrix(rows, COLUMNS))
    fit.layer_volume = {s: {1: 0.0, 2: 0.0, 3: 0.0} for s in ("move", "direction")}
    f = forecast_with_additive_scorer(fit, rows[-1])
    for k in ("up", "flat", "down"):
        assert f[k] == pytest.approx(floored(ODDS)[k], abs=1e-6)


def test_a_signal_column_gets_a_positive_push_and_its_layer_a_volume():
    rows = signal_rows()
    fit = fit_additive_scorer(make_matrix(rows, COLUMNS))
    assert fit.pushes["move"]["code:A"]["hot"] > 0 > fit.pushes["move"]["code:A"]["cold"]
    assert fit.layer_volume["move"][2] > 0           # the layer with the signal is turned up
    hot = forecast_with_additive_scorer(fit, make_row(999, "2026-10-09", {"code:A": "hot"}, None))
    cold = forecast_with_additive_scorer(fit, make_row(998, "2026-10-09", {"code:A": "cold"}, None))
    assert 1 - hot["flat"] > 1 - cold["flat"]


def test_too_few_rows_means_the_scorer_is_asleep():
    rows = signal_rows(n=6)
    fit = fit_additive_scorer(make_matrix(rows, COLUMNS))
    assert fit.pushes == {} and "why" in fit.notes
    assert forecast_with_additive_scorer(fit, rows[0]) is None          # not an echo of the odds, which would double their weight


def test_excluded_and_ungraded_rows_are_never_trained_on():
    rows = signal_rows(n=60)
    rows[0].learn_exclude = True
    rows[1].outcome = None
    m = make_matrix(rows, COLUMNS)
    assert len(m.trainable_rows()) == 58


def test_fit_round_trips_through_json():
    fit = fit_additive_scorer(make_matrix(signal_rows(), COLUMNS))
    again = AdditiveScorerFit.from_json(fit.to_json())
    assert again.layer_volume == fit.layer_volume and again.pushes == fit.pushes


# ---------------------------------------------------------------- matcher

def test_mismatch_counts_same_different_and_silent():
    groups = {c: [c] for c in ("q1", "q2", "q3", "q4", "q5")}            # each question its own group
    w = {g: 1.0 for g in groups}
    today = {"q1": "a", "q2": "b", "q3": "c", "q4": "d", "q5": "e"}
    past = {"q1": "a", "q2": "x", "q3": "c", "q4": "y"}          # q5 silent
    d, shared = mismatch(today, past, groups, w)
    assert d == pytest.approx((0 + 1 + 0 + 1 + 0.5) / 5) and shared == 4


def test_a_big_group_counts_once_in_the_matcher():
    # eight questions on one topic all differ; one question on another topic matches: each group is half the distance
    groups = {"big": [f"b{i}" for i in range(8)], "small": ["s"]}
    today = {**{f"b{i}": "x" for i in range(8)}, "s": "same"}
    past = {**{f"b{i}": "y" for i in range(8)}, "s": "same"}
    d, shared = mismatch(today, past, groups, {"big": 1.0, "small": 1.0})
    assert d == pytest.approx(0.5) and shared == 9                         # per column it would have been 8/9


def test_matcher_keeps_k_closest_and_uses_the_residual_form():
    rows = signal_rows(n=120)
    matrix = make_matrix(rows, COLUMNS)
    fit = fit_matcher(matrix)
    today = make_row(500, "2026-10-09", {"code:A": "hot", "code:B": "low", "jev:q": "yes"}, None)
    probs, diag = forecast_with_matcher(fit, today, matrix.trainable_rows())
    earlier_days = sum(1 for r in rows if r.day < today.day)              # a read from today's own day is never a candidate
    assert diag["kept_count"] == MATCHER_NEIGHBOR_COUNT and diag["candidates"] == earlier_days < len(rows)
    assert "past" not in fit.to_json()                                     # the candidates stay in the matrix, never copied into the fit
    assert sum(probs.values()) == pytest.approx(1.0)
    # look-alikes of a "hot" read moved more than their odds said, so the residual lifts the move chance
    assert 1 - probs["flat"] > 1 - ODDS["flat"]


def test_matcher_never_looks_at_its_own_day_or_later():
    rows = signal_rows(n=120)
    matrix = make_matrix(rows, COLUMNS)
    fit = fit_matcher(matrix)
    first = rows[0]
    probs, diag = forecast_with_matcher(fit, make_row(1, first.day, first.answers, None), matrix.trainable_rows())
    assert probs is None and "why" in diag


def test_matcher_prior_reads_pull_toward_the_odds():
    rows = signal_rows(n=40)
    matrix = make_matrix(rows, COLUMNS)
    fit = fit_matcher(matrix)
    today = make_row(500, "2026-10-09", {"code:A": "hot"}, None)
    probs, diag = forecast_with_matcher(fit, today, matrix.trainable_rows())
    assert probs is not None and diag["effective_reads"] <= MATCHER_NEIGHBOR_COUNT
    assert abs(probs["flat"] - ODDS["flat"]) < 0.5      # pulled, not a hard vote


# ---------------------------------------------------------------- corrected JEV

def test_identity_correction_returns_jev_unchanged():
    jev = {"up": 0.3, "flat": 0.5, "down": 0.2}
    out = correct_jev_call(jev, ODDS, temperature=1.0, flat_offset=0.0, down_offset=0.0, historical_odds_weight=0.0)
    for k in jev:
        assert out[k] == pytest.approx(jev[k], abs=1e-6)


def test_asleep_below_thirty_graded_rows():
    rows = signal_rows(n=MIN_GRADED_ROWS - 1)
    fit = fit_jev_corrected(make_matrix(rows, COLUMNS))
    assert fit.asleep and forecast_with_jev_corrected(fit, rows[0]) is None


def test_fit_lowers_the_penalty_when_jev_over_calls_moves():
    rows = signal_rows(n=120)
    m = make_matrix(rows, COLUMNS)
    fit = fit_jev_corrected(m)
    assert not fit.asleep
    before = sum(log_loss(floored(r.jev_own_probs), r.outcome) for r in m.trainable_rows()) / len(m.trainable_rows())
    after = sum(log_loss(forecast_with_jev_corrected(fit, r), r.outcome) for r in m.trainable_rows()) / len(m.trainable_rows())
    assert after < before
    assert fit.flat_offset > 0          # it learned to give flat more


# ---------------------------------------------------------------- non-voting columns (catalog "votes": false)

def test_a_non_voting_column_stays_in_the_matrix_and_neither_learner_counts_it():
    """The signal column marked "votes": false: no push, no layer volume, no group in the matcher; its answers stay on the rows."""
    rows = signal_rows()
    columns = {**COLUMNS, "code:A": {**COLUMNS["code:A"], "votes": False}}
    matrix = make_matrix(rows, columns)
    fit = fit_additive_scorer(matrix)
    assert "code:A" not in fit.columns and all("code:A" not in pushes for pushes in fit.pushes.values())
    assert 2 not in fit.layer_volume["move"]                            # code:A was layer 2's only column: the layer has no volume to choose
    hot = forecast_with_additive_scorer(fit, make_row(999, "2026-10-09", {"code:A": "hot", "code:B": "low"}, None))
    cold = forecast_with_additive_scorer(fit, make_row(998, "2026-10-09", {"code:A": "cold", "code:B": "low"}, None))
    assert hot == cold                                                   # the column moves nothing
    matcher = fit_matcher(matrix)
    assert "code:A" not in matcher.group_of_column and set(matcher.group_of_column) == {"code:B", "jev:q"}
    assert all(r.answers["code:A"] in ("hot", "cold") for r in matrix.rows)          # kept as context


def test_a_non_voting_column_weighs_nothing_in_a_match():
    today = {"code:A": "hot", "code:B": "low"}
    columns = {"code:A": {"layer": 2, "group": "g:a", "family": "t", "votes": False}, "code:B": {"layer": 1, "group": "g:b", "family": "m"}}
    past = [make_row(i, "2026-10-01", {"code:A": "cold" if i % 2 else "hot", "code:B": "low"}, "up") for i in range(20)]
    fit = fit_matcher(make_matrix(past, columns))
    by_group = fit.columns_by_group()
    assert [mismatch(today, p.answers, by_group, fit.group_weights)[0] for p in past] == [0.0] * 20


def test_the_fit_file_names_the_columns_that_do_not_vote(tmp_path, monkeypatch):
    import json
    from spx_jev.mirai_prediction import voice_fits
    columns = {**COLUMNS, "code:A": {**COLUMNS["code:A"], "votes": False}}
    monkeypatch.setattr(voice_fits, "build_answer_matrix", lambda *a, **k: make_matrix(signal_rows(), columns, "2026-10-10"))
    _, path = voice_fits.fit_voices_for_day(tmp_path, tmp_path, "live", "average_30", "2026-10-10")
    doc = json.loads(path.read_text())
    assert doc["non_voting_columns"] == ["code:A"] and doc["columns_by_layer"] == {"1": 1, "2": 1, "3": 1}
    assert doc["voting_columns_by_layer"] == {"1": 1, "2": 0, "3": 1} and "code:A" not in doc["additive_scorer"]["columns"]


# ---------------------------------------------------------------- an options-provider outage (2026-10-06..08)

def test_a_read_with_the_provider_questions_blank_pushes_nothing_for_them_and_leaves_the_rest_alone():
    """Blank days, choice B: on an outage read the 14 provider questions are skipped, never counted as an answer of their own
    ("no signal"). The read moves none of their pushes, every other column's push is what the read answered would make it,
    and in a forecast or a match the blanks weigh as if the read never had those columns."""
    catalog = load_catalog()
    columns = {column_name(q["id"]): {"layer": q["layer"], "group": q["group"]} for q in catalog}
    blank = {column_name(qid) for qid in PROVIDER_QUESTIONS}
    rng = random.Random(7)

    def answered(i: int, day: str, outcome: str) -> Row:
        return make_row(i, day, {column_name(q["id"]): rng.choice(q["options"]) for q in catalog}, outcome)

    normal = [answered(i, DAYS[i % 20], rng.choice(["up", "flat", "down"])) for i in range(60)]
    full = answered(99, "2026-10-07", "up")
    outage = make_row(99, "2026-10-07", {c: None if c in blank else a for c, a in full.answers.items()}, "up")
    for stage in ("move", "direction"):
        before, _ = fit_pushes(normal, stage, columns)
        with_outage, _ = fit_pushes(normal + [outage], stage, columns)
        as_answered, _ = fit_pushes(normal + [full], stage, columns)
        assert blank <= set(before) and {c: with_outage[c] for c in blank} == {c: before[c] for c in blank}
        assert {c: p for c, p in with_outage.items() if c not in blank} == {c: p for c, p in as_answered.items() if c not in blank}
        assert all(None not in labels for labels in with_outage.values())
    pushes, _ = fit_pushes(normal, "move", columns)
    absent = make_row(98, "2026-10-08", {c: a for c, a in outage.answers.items() if c not in blank}, None)
    assert layer_sums(outage, pushes, columns) == layer_sums(absent, pushes, columns)
    matcher = fit_matcher(make_matrix(normal, columns))
    assert mismatch(outage.answers, full.answers, matcher.columns_by_group(), matcher.group_weights)[1] == len(catalog) - len(blank)
