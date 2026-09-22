"""The phone card names a Score answer's levels by their words, in plain units."""
from __future__ import annotations

from sndk_jev.service import named_probabilities


def test_score_levels_are_named_in_plain_words():
    a = {"type": "score", "score": 1.41, "confidence": 0.11,
         "legend": {"0": "price stays within 0.24 sigma of where it is now the whole time",
                    "1": "price gets between 0.24 and 0.50 sigma away at its furthest point",
                    "2": "price gets more than 0.50 sigma away at its furthest point"},
         "probabilities": {"0": 0.06, "1": 0.46, "2": 0.48}}
    out = named_probabilities(a)
    assert out == {"price stays within 0.24 of a normal day's move of where it is now the whole time": 0.06,
                   "price gets between 0.24 and 0.50 of a normal day's move away at its furthest point": 0.46,
                   "price gets more than 0.50 of a normal day's move away at its furthest point": 0.48}


def test_choice_probabilities_pass_through():
    a = {"type": "choice", "choice": "above", "probabilities": {"above": 0.9, "below": 0.1}, "confidence": 0.8}
    assert named_probabilities(a) == {"above": 0.9, "below": 0.1}
    assert named_probabilities({"type": "noul", "noul": 0.4}) is None
