"""The A/B arms have to differ by the figure and by nothing else.

If one arm happens to echo the wording of its own correct answer more than the
other does, the experiment measures phrasing rather than the figure. These tests
make that visible instead of letting it hide in the result.
"""
from __future__ import annotations

import re

import pytest

from sndk_jev.ab_test import ARMS, gradeable
from sndk_jev.ask import DEFAULT_QUESTIONS, load_questions
from sndk_jev.state_builder import build_ab, variant_state

DIGITS = re.compile(r"\d")
WORD = re.compile(r"[a-z]+")




def test_full_arm_is_the_live_label(full_scene):
    state, _, variants, _ = build_ab(full_scene)
    for path, forms in variants.items():
        group, key = path.split(".", 1)
        assert forms["full"] == state[group][key], f"{path}: the full arm drifted from the live label"


def test_no_figure_arm_drops_a_number(full_scene):
    """Deleting the measured figure must remove at least one digit."""
    _, _, variants, _ = build_ab(full_scene)
    checked = 0
    for path, forms in variants.items():
        if forms["no_figure"] == forms["full"]:
            continue
        checked += 1
        assert len(DIGITS.findall(forms["no_figure"])) < len(DIGITS.findall(forms["full"])), \
            f"{path}: the no_figure arm kept as many digits as full"
    assert checked >= 8, f"only {checked} labels carry a no_figure arm"


def test_variant_state_swaps_only_labels_with_arms(full_scene):
    state, _, variants, _ = build_ab(full_scene)
    for arm in ARMS:
        swapped = variant_state(state, variants, arm)
        assert set(swapped) == set(state)
        for group, labels in state.items():
            assert set(swapped[group]) == set(labels)
            for key, value in labels.items():
                path = f"{group}.{key}"
                expected = variants[path][arm] if path in variants else value
                assert swapped[group][key] == expected


def test_every_graded_question_has_exactly_one_code_verdict(full_scene):
    _, _, _, verdicts = build_ab(full_scene)
    doc = load_questions(DEFAULT_QUESTIONS)
    graded = gradeable(doc, verdicts)
    assert len(graded) >= 8, f"only {len(graded)} questions are gradeable: {sorted(graded)}"
    for qid, path in graded.items():
        assert path in verdicts
    assert "direction_lean" not in graded, "a forecast has no right answer yet and must not be graded"
    assert "move_size" not in graded


def test_verdict_names_a_real_answer_option(full_scene):
    """The code's verdict must be an option the question actually offers."""
    _, _, _, verdicts = build_ab(full_scene)
    doc = load_questions(DEFAULT_QUESTIONS)
    graded = gradeable(doc, verdicts)
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    for qid, path in graded.items():
        crit = by_id[qid]["criteria"]
        options = set(crit) if isinstance(crit, dict) else set()
        assert verdicts[path] in options, f"{qid}: code says {verdicts[path]!r}, options are {sorted(options)}"


def test_report_wording_overlap_between_arms(full_scene, capsys):
    """Not an assertion, a measurement: how much each arm echoes its own right answer.

    A large gap between arms would mean the test measures phrasing, not the figure.
    """
    _, _, variants, verdicts = build_ab(full_scene)
    doc = load_questions(DEFAULT_QUESTIONS)
    graded = gradeable(doc, verdicts)
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    rows = []
    for qid, path in sorted(graded.items()):
        if path not in variants:
            continue
        crit = by_id[qid]["criteria"]
        if not isinstance(crit, dict):
            continue
        answer_words = set(WORD.findall(crit[verdicts[path]].lower()))
        if not answer_words:
            continue
        shares = []
        for arm in ARMS:
            label_words = set(WORD.findall(variants[path][arm].lower()))
            shares.append(len(label_words & answer_words) / len(answer_words))
        rows.append((qid, shares))
    with capsys.disabled():
        print("\n  share of the correct option's words that appear in the label")
        print(f"  {'question':<26}" + "".join(f"{a:>12}" for a in ARMS))
        for qid, shares in rows:
            print(f"  {qid:<26}" + "".join(f"{s:>11.0%}" for s in shares))
        if rows:
            for i, arm in enumerate(ARMS):
                mean = sum(r[1][i] for r in rows) / len(rows)
                print(f"  mean {arm}: {mean:.0%}")
    assert rows
