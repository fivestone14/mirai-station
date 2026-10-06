"""The three learners on synthetic answer matrices: pushes, matching, correction. No host state."""
from __future__ import annotations

import math
import random

import pytest

from spx_jev.mirai_prediction.additive_scorer import (ANSWER_PRIOR_READS, AdditiveScorerFit, fit_additive_scorer, fit_pushes,
                                                       forecast_with_additive_scorer, logit)
from spx_jev.mirai_prediction.answer_matrix import AnswerMatrix, Row
from spx_jev.mirai_prediction.jev_corrected import MIN_GRADED_ROWS, correct_jev_call, fit_jev_corrected, forecast_with_jev_corrected
from spx_jev.mirai_prediction.matcher import (MATCHER_NEIGHBOR_COUNT, MATCHER_PRIOR_READS, fit_matcher, forecast_with_matcher, mismatch)
from spx_jev.scores import floored, log_loss

ODDS = {"up": 0.2, "flat": 0.55, "down": 0.25}
DAYS = [f"2026-09-{d:02d}" for d in range(1, 29)] + [f"2026-10-{d:02d}" for d in range(1, 10)]


def make_row(i: int, day: str, answers: dict, outcome: str | None, jev=None, odds=ODDS, exclude=False) -> Row:
    ts = f"{day}T{10 + i % 6:02d}:{(i * 7) % 60:02d}:00-04:00"
    return Row(read_id=f"live:{ts}", row_ts=ts, day=day, sum_id="average_30", historical_odds_probs=dict(odds),
               jev_own_probs=jev, shown_probs=None, pool_v1_probs=None, outcome=outcome, learn_exclude=exclude, answers=answers)


def make_matrix(rows: list[Row], columns: dict, before_day: str = "2026-10-10") -> AnswerMatrix:
    return AnswerMatrix(lane="live", sum_id="average_30", built_for_day=before_day, rows=rows, columns=columns, level_history={})


COLUMNS = {"fact:a": {"layer": 2, "group": "fact:a", "family": "t"}, "level:b": {"layer": 1, "group": "level:b", "family": "m"},
           "jev:q": {"layer": 3, "group": "jev:q", "family": "jev"}}


def signal_rows(n: int = 240, seed: int = 1) -> list[Row]:
    """'fact:a' = "hot" precedes a move 80% of the time, "cold" 20%; the other columns are noise."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        day = DAYS[i * len(DAYS) // n]
        hot = rng.random() < 0.5
        moved = rng.random() < (0.8 if hot else 0.2)
        outcome = ("up" if rng.random() < 0.5 else "down") if moved else "flat"
        answers = {"fact:a": "hot" if hot else "cold", "level:b": rng.choice(["low", "middle", "high"]), "jev:q": rng.choice(["yes", "no"])}
        rows.append(make_row(i, day, answers, outcome, jev={"up": 0.4, "flat": 0.2, "down": 0.4}))
    return rows


# ---------------------------------------------------------------- additive scorer

def test_push_is_distance_from_the_answers_own_normal():
    rows = [make_row(i, DAYS[i % 5], {"fact:a": "hot"}, "up" if i % 4 else "flat") for i in range(40)]
    pushes, bases = fit_pushes(rows, "move")
    n, k = 40, 30
    base = 1 - ODDS["flat"]
    blend = (k + ANSWER_PRIOR_READS * base) / (n + ANSWER_PRIOR_READS)
    assert pushes["fact:a"]["hot"] == pytest.approx(logit(blend) - logit(base))
    assert bases["fact:a"]["hot"] == pytest.approx(base)


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
    assert fit.pushes["move"]["fact:a"]["hot"] > 0 > fit.pushes["move"]["fact:a"]["cold"]
    assert fit.layer_volume["move"][2] > 0           # the layer with the signal is turned up
    hot = forecast_with_additive_scorer(fit, make_row(999, "2026-10-09", {"fact:a": "hot"}, None))
    cold = forecast_with_additive_scorer(fit, make_row(998, "2026-10-09", {"fact:a": "cold"}, None))
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
    w = {c: 1.0 for c in ("q1", "q2", "q3", "q4", "q5")}
    today = {"q1": "a", "q2": "b", "q3": "c", "q4": "d", "q5": "e"}
    past = {"q1": "a", "q2": "x", "q3": "c", "q4": "y"}          # q5 silent
    d, shared = mismatch(today, past, w)
    assert d == pytest.approx((0 + 1 + 0 + 1 + 0.5) / 5) and shared == 4


def test_matcher_keeps_k_closest_and_uses_the_residual_form():
    rows = signal_rows(n=120)
    matrix = make_matrix(rows, COLUMNS)
    fit = fit_matcher(matrix)
    today = make_row(500, "2026-10-09", {"fact:a": "hot", "level:b": "low", "jev:q": "yes"}, None)
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
    today = make_row(500, "2026-10-09", {"fact:a": "hot"}, None)
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
