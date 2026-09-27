"""The label families: each owns its labels and only those, and one family's failure costs only its own."""
from __future__ import annotations

from dataclasses import replace

import pytest

from spx_jev.labels import registry
from spx_jev.labels.label_set import LabelSet
from spx_jev.labels.registry import FAMILIES, build_labels


def test_every_label_has_exactly_one_family_and_every_builder_is_named_for_its_family():
    owners: dict[str, str] = {}
    for family in FAMILIES:
        assert family.build.__name__ == f"build_{family.name}_labels"
        for path in family.labels:
            assert path not in owners, f"{path} is owned by both {owners[path]} and {family.name}"
            owners[path] = family.name


def test_a_full_read_writes_or_omits_only_owned_labels(full_scene):
    labels = build_labels(full_scene)
    owned = {p for f in FAMILIES for p in f.labels}
    assert labels.paths() <= owned


def test_a_family_that_writes_a_label_it_does_not_own_stops_the_read(full_scene, monkeypatch):
    def rogue(scene):
        ls = LabelSet()
        ls.put("price.recent_move", "not mine")
        return ls
    families = tuple(replace(f, build=rogue) if f.name == "breadth" else f for f in FAMILIES)
    monkeypatch.setattr(registry, "FAMILIES", families)
    with pytest.raises(ValueError, match="breadth family wrote labels it does not own"):
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
