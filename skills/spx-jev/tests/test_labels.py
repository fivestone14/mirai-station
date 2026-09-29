"""The label families: each owns its labels and only those, and one family's failure costs only its own."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from spx_jev import row_adapter
from spx_jev.labels import registry
from spx_jev.labels.label_set import LabelSet
from spx_jev.labels.registry import FAMILIES, GATE_NOT_BUILT, NOT_BUILT, build_labels

QUESTION_SET = json.loads((Path(__file__).resolve().parent.parent / "spec" / "question_set.json").read_text())


def test_every_label_has_exactly_one_family_and_every_builder_is_named_for_its_family():
    owners: dict[str, str] = {}
    for family in FAMILIES:
        assert family.build.__name__ == f"build_{family.name}_labels"
        for path in family.labels:
            assert path not in owners, f"{path} is owned by both {owners[path]} and {family.name}"
            owners[path] = family.name


def test_every_label_the_questions_read_and_every_gate_they_sleep_on_has_a_family():
    owned = {p for f in FAMILIES for p in f.labels}
    assert {lab["name"] for lab in QUESTION_SET["labels"]} <= owned
    questions = [q for g in QUESTION_SET["groups"] for q in g["questions"]]
    assert {lab["name"] for q in questions for lab in q["labels_needed"]} <= owned
    gates = [qid for f in FAMILIES for qid in f.gates]
    assert sorted(gates) == sorted(q["id"] for q in questions if q.get("sleep_when"))
    assert {p: f.name for f in FAMILIES for p in f.dark} == {
        lab["name"]: next(f.name for f in FAMILIES if lab["name"] in f.labels) for lab in QUESTION_SET["labels"] if lab["availability"] == "dark"}


def test_the_options_labels_belong_to_the_options_flow_family_and_no_family_is_named_for_the_tape():
    """"Tape" is the opening lane's name and its price unit's; the family reading the 0DTE options trades is not it."""
    owner = {p: f.name for f in FAMILIES for p in f.labels}
    assert {owner[p] for p in owner if p.startswith("options.")} == {"options_flow"}
    assert not [f.name for f in FAMILIES if "tape" in f.name]


def test_a_renamed_label_says_what_it_measures_and_its_old_path_maps_to_the_new():
    owned = {p for f in FAMILIES for p in f.labels}
    assert registry.RENAMED == {"breadth.upvol_share_30m": "breadth.net_volume_change_30m", "vol.vix_change_30": "vol.vix_change"}
    assert set(registry.RENAMED.values()) <= owned and not set(registry.RENAMED) & owned


def test_the_row_adapter_has_one_line_of_extra_fields_per_family():
    assert set(row_adapter.FAMILY_FIELDS) == {f.name for f in FAMILIES}


def test_a_full_read_accounts_for_every_owned_label_and_gate(full_scene):
    """Every label is written or omitted with a reason, and every gate decided: the ones no code builds
    yet as not built, the ones no feed carries as dark."""
    labels = build_labels(full_scene)
    owned = {p for f in FAMILIES for p in f.labels}
    bar_clock_only = {p for f in FAMILIES for p in f.bar_clock_only}
    premarket_only = {p for f in FAMILIES for p in f.premarket_only}
    assert labels.paths() == owned - bar_clock_only - premarket_only  # a live read: the opening and premarket lanes' labels are not its own
    assert bar_clock_only == {"tape.move_since_read", "tape.range_since_read", "tape.unit_vs_normal"}
    assert all(labels.omitted[p].startswith("dark: ") for f in FAMILIES for p in f.dark)
    assert set(labels.gates) == {qid for f in FAMILIES for qid in f.gates}


def test_a_read_on_the_bar_clock_accounts_for_the_opening_lanes_labels_too(lane_scene):
    labels = build_labels(lane_scene)
    assert labels.paths() == {p for f in FAMILIES for p in f.labels} - {p for f in FAMILIES for p in f.premarket_only}
    assert NOT_BUILT not in {labels.omitted.get(p) for f in FAMILIES for p in f.bar_clock_only}


def test_a_premarket_read_runs_only_the_families_that_serve_it_and_accounts_for_their_premarket_labels(premarket_scene):
    labels = build_labels(premarket_scene)
    serving = [f for f in FAMILIES if f.premarket]
    assert {f.name for f in serving} == {"context", "premarket", "story", "bitcoin"}
    assert labels.paths() == {p for f in serving for p in f.premarket}
    assert all(set(f.premarket_only) <= set(f.premarket) <= set(f.labels) for f in FAMILIES)
    assert set(labels.gates) == {qid for f in serving for qid in f.gates}


def test_a_label_and_a_gate_no_code_writes_are_omitted_and_asleep_as_not_built(full_scene, monkeypatch):
    families = tuple(replace(f, build=lambda scene: LabelSet()) if f.name == "gap_open" else f for f in FAMILIES)
    monkeypatch.setattr(registry, "FAMILIES", families)
    labels = build_labels(full_scene)
    assert labels.omitted["gap.size"] == NOT_BUILT and labels.gates["gap_fill_next_hour"] == GATE_NOT_BUILT


def test_a_family_that_writes_a_label_it_does_not_own_stops_the_read(full_scene, monkeypatch):
    def rogue(scene):
        ls = LabelSet()
        ls.put("price.recent_move", "not mine")
        return ls
    families = tuple(replace(f, build=rogue) if f.name == "breadth" else f for f in FAMILIES)
    monkeypatch.setattr(registry, "FAMILIES", families)
    with pytest.raises(ValueError, match="breadth family wrote labels or gates it does not own"):
        build_labels(full_scene)


def test_a_family_that_fails_omits_only_its_own_labels_with_the_failure(full_scene, monkeypatch):
    def broken(scene):
        raise KeyError("gex_views")
    families = tuple(replace(f, build=broken) if f.name == "gamma" else f for f in FAMILIES)
    monkeypatch.setattr(registry, "FAMILIES", families)
    labels = build_labels(full_scene)
    gamma = next(f for f in FAMILIES if f.name == "gamma")
    assert all(labels.omitted[p] == "the gamma labels failed this read: KeyError: 'gex_views'" for p in gamma.labels)
    assert "gex" not in labels.state and labels.state["price"]["recent_move"]
