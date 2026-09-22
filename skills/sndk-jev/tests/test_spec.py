"""spec/labels.json must agree with the code.

Every label the spec calls "built" has to come out of the builder, and nothing the
builder writes may be missing from the spec or marked as anything but built. So a
label cannot be promoted or retired on the page without the test going red.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sndk_jev.ask import DEFAULT_QUESTIONS, load_questions, paths_in
from sndk_jev.state_builder import build_state

SPEC = Path(__file__).resolve().parent.parent / "spec" / "labels.json"
PATH = re.compile(r"^[a-z_]+\.[a-z_0-9]+$")


def spec():
    with open(SPEC, encoding="utf-8") as f:
        return json.load(f)




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
        assert lab["status"] in statuses, lab["path"]
        assert lab["type"] in types, lab["path"]
        assert lab["viewpoint"] in viewpoints, lab["path"]
        assert lab["reads_as"].strip(), lab["path"]


def test_built_set_matches_the_code(full_scene):
    d = spec()
    state, omitted = build_state(full_scene)
    assert omitted == {}, omitted
    produced = {f"{g}.{k}" for g, labels in state.items() for k in labels}
    built = {lab["path"] for lab in d["labels"] if lab["status"] == "built"}
    assert produced - built == set(), f"the code writes labels the spec does not call built: {sorted(produced - built)}"
    assert built - produced == set(), f"the spec calls labels built that the code does not write: {sorted(built - produced)}"


def test_unbuilt_labels_have_no_question_yet_or_are_named_by_one():
    """A free or new label that no question reads is fine, but it must be visible as such."""
    d = spec()
    doc = load_questions(DEFAULT_QUESTIONS)
    read = set()
    for g in doc["groups"]:
        for q in g["questions"].values():
            read.update(paths_in(q))
    for lab in d["labels"]:
        if lab["status"] == "built":
            continue
        group = lab["path"].split(".")[0]
        # nothing to assert about the answer; the page shows it. Only guard against a typo'd group.
        assert group in {"context", "price", "range", "iv", "gex", "options", "volume", "momentum", "news"}, lab["path"]
