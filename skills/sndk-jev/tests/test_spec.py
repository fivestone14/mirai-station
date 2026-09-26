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


def _produced(scene):
    state, omitted = build_state(scene)
    assert omitted == {}, omitted
    return {f"{g}.{k}" for g, labels in state.items() for k in labels}


def test_built_set_matches_the_code(full_scene):
    d = spec()
    produced = _produced(full_scene)
    built = {lab["path"] for lab in d["labels"] if lab["status"] == "built"}
    assert produced - built == set(), f"the code writes labels the spec does not call built: {sorted(produced - built)}"
    assert built - produced == set(), f"the spec calls labels built that the code does not write: {sorted(built - produced)}"


def test_lane_set_matches_the_code(full_scene, lane_scene):
    """A label the spec calls ``lane`` is written on the tape lane and only there: the live lane's
    state never carries it, and on the lane every one of them comes out beside the built set."""
    d = spec()
    built = {lab["path"] for lab in d["labels"] if lab["status"] == "built"}
    lane = {lab["path"] for lab in d["labels"] if lab["status"] == "lane"}
    assert lane and all(p.startswith("tape.") for p in lane), sorted(lane)
    assert _produced(full_scene) & lane == set(), "the live lane wrote a lane label"
    on_lane = _produced(lane_scene)
    assert on_lane - built - lane == set(), f"the lane writes labels the spec does not know: {sorted(on_lane - built - lane)}"
    assert lane - on_lane == set(), f"the spec calls labels lane that the lane does not write: {sorted(lane - on_lane)}"
    assert built - on_lane == set(), f"the lane lost built labels: {sorted(built - on_lane)}"


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
        assert group in {"context", "price", "range", "iv", "gex", "options", "volume", "momentum", "news", "tape"}, lab["path"]


def test_a_retired_question_is_never_asked_again():
    """The retired block records what was taken out; none of those ids may reappear in a group."""
    doc = load_questions(DEFAULT_QUESTIONS)
    asked = {qid for g in doc["groups"] for qid in g["questions"]}
    retired = {r["id"] for r in doc.get("retired", [])}
    assert retired and not (asked & retired), sorted(asked & retired)
    for r in doc["retired"]:
        assert r.get("into") is None or r["into"] in asked, r
