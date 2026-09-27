"""spec/labels.json must agree with the code.

Every label the spec calls "built" has to come out of the builder, and nothing the builder writes
may be missing from the spec; "lane" labels come out on the tape lane only. So a label cannot be
added or dropped on the page without the test going red.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from spx_jev.labels.registry import build_labels

SPEC = Path(__file__).resolve().parent.parent / "spec" / "labels.json"
PATH = re.compile(r"^[a-z_]+\.[a-z_0-9]+$")


def spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _produced(scene):
    labels = build_labels(scene)
    state, omitted = labels.state, labels.omitted
    assert omitted == {}, omitted
    return {f"{g}.{k}" for g, labels in state.items() for k in labels}


def test_spec_is_well_formed():
    d = spec()
    statuses, types = set(d["statuses"]), set(d["types"])
    viewpoints = {v["id"] for v in d["viewpoints"]}
    seen = set()
    for lab in d["labels"]:
        for key in ("path", "viewpoint", "status", "type", "from", "logic", "cut", "reads_as", "real"):
            assert key in lab, f"{lab.get('path')}: missing {key}"
        assert PATH.match(lab["path"]), lab["path"]
        assert lab["path"] not in seen, f"duplicate {lab['path']}"
        seen.add(lab["path"])
        assert lab["status"] in statuses and lab["type"] in types and lab["viewpoint"] in viewpoints, lab["path"]
        assert lab["reads_as"].strip(), lab["path"]


def test_built_set_matches_the_code(full_scene):
    built = {lab["path"] for lab in spec()["labels"] if lab["status"] == "built"}
    produced = _produced(full_scene)
    assert produced - built == set(), f"the code writes labels the spec does not call built: {sorted(produced - built)}"
    assert built - produced == set(), f"the spec calls labels built that the code does not write: {sorted(built - produced)}"


def test_lane_set_matches_the_code(full_scene, lane_scene):
    """A label the spec calls ``lane`` is written on the tape lane and only there."""
    d = spec()
    built = {lab["path"] for lab in d["labels"] if lab["status"] == "built"}
    lane = {lab["path"] for lab in d["labels"] if lab["status"] == "lane"}
    assert lane and all(p.startswith("tape.") for p in lane), sorted(lane)
    assert _produced(full_scene) & lane == set(), "the live lane wrote a lane label"
    assert _produced(lane_scene) == built | lane


def test_every_cut_the_spec_names_is_a_constant():
    import importlib
    import pkgutil
    from spx_jev import cuts, labels
    modules = [cuts] + [importlib.import_module(f"spx_jev.labels.{m.name}") for m in pkgutil.iter_modules(labels.__path__)]
    for lab in spec()["labels"]:
        for name in re.findall(r"\b[A-Z][A-Z_0-9]{3,}\b", lab["cut"]):
            assert any(hasattr(m, name) for m in modules), f"{lab['path']} names {name}, which the code does not define"
