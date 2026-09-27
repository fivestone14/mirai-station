"""Every measured cut in cuts.py is the number spec/cuts.json recorded, and the measurement says how."""
from __future__ import annotations

import json
from pathlib import Path

from spx_jev import cuts

SPEC = Path(__file__).resolve().parent.parent / "spec" / "cuts.json"


def _spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_every_measured_cut_is_the_recorded_number():
    d = _spec()
    for name, entry in d["cuts"].items():
        assert getattr(cuts, name.upper()) == entry["value"], name


def test_the_base_rates_are_the_recorded_counts():
    rates = _spec()["base_rates"]
    for horizon in ("next_30", "next_60"):
        for band in ("up", "down", "flat"):
            assert getattr(cuts, f"{horizon}_{band}_pct".upper()) == rates[horizon][f"{band}_pct"], (horizon, band)
    for band in ("down_big", "down_small", "flat", "up_small", "up_big"):
        assert getattr(cuts, f"tape_{band}_pct".upper()) == rates["next_10"][f"{band}_pct"], band


def test_the_record_says_how_it_was_measured():
    d = _spec()
    assert d["rule"] and len(d["spx_sessions"]) >= 40
    for name, entry in d["cuts"].items():
        assert {"measure", "sndk_cut", "percentile", "n_sndk", "n_spx"} <= set(entry), name
        assert entry["n_spx"] > 0 and 0 <= entry["percentile"] <= 100, name


def test_the_question_constants_are_every_threshold_by_its_lower_case_name():
    assert cuts.QUESTION_CONSTANTS["move_rule_sigma"] == cuts.MOVE_RULE_SIGMA
    assert cuts.QUESTION_CONSTANTS["even_split_high"] == cuts.EVEN_SPLIT_HIGH
    assert all(k == k.lower() for k in cuts.QUESTION_CONSTANTS)
