"""spec/labels.json must agree with the code.

Every label the spec calls "built" has to come out of the builder, and nothing the builder writes
may be missing from the spec; "lane" labels come out on the tape lane only. So a label cannot be
added or dropped on the page without the test going red. The final question set's own labels are
specified in spec/question_set.json and measured by their families' tests: many are written only on
some moments (a release, a shock, after 14:00), so one scene cannot measure them all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import measured
from spx_jev.labels.registry import build_labels

SPEC = Path(__file__).resolve().parent.parent / "spec" / "labels.json"
QUESTION_SET = Path(__file__).resolve().parent.parent / "spec" / "question_set.json"
PATH = re.compile(r"^[a-z_]+\.[a-z_0-9]+$")


def spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def set_labels():
    return {lab["name"] for lab in json.loads(QUESTION_SET.read_text(encoding="utf-8"))["labels"]}


def _produced(scene):
    """The labels.json labels the scene writes; each of them it omits fails, and so does any label it
    writes that neither spec names."""
    labels = build_labels(scene)
    specified = {lab["path"] for lab in spec()["labels"]}
    state, omitted = labels.state, measured(labels.omitted)
    assert {p: why for p, why in omitted.items() if p in specified} == {}, omitted
    written = {f"{g}.{k}" for g, labels in state.items() for k in labels}
    assert written - specified - set_labels() == set(), "the code writes labels neither spec names"
    return written & specified


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


def test_the_question_sets_tags_follow_its_convention():
    """A tag is one the set's conventions define, and an untagged question carries no tags field."""
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    known = {"SEQUENTIAL_READ"}
    assert all(t in d["conventions"]["tags"] for t in known)
    tagged = [q for g in d["groups"] for q in g["questions"] if "tags" in q]
    assert tagged and all(q["tags"] and set(q["tags"]) <= known for q in tagged), [q["id"] for q in tagged]


def test_the_question_sets_counts_its_opening_lane_list_and_the_readme_match_its_contents():
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    questions = [q for g in d["groups"] for q in g["questions"]]
    opening = {q["id"] for q in questions if q["status"] == "live" and "opening_five_minute" in q.get("lanes", [])}
    n, labels, constants = len(questions), len(d["labels"]), len(d["constants"])
    assert (d["counts"]["questions"], d["counts"]["labels"], d["counts"]["constants"]) == (n, labels, constants)
    assert set(d["opening_lane"]["questions"]) == opening and d["counts"]["opening_lane_questions"] == len(opening)
    readme = (QUESTION_SET.parent.parent / "README.md").read_text(encoding="utf-8")
    assert f"`spec/question_set.json`, {n} questions)" in readme
    assert f"({n} questions, {labels} labels, {constants} constants," in readme


def test_the_question_sets_counts_are_what_the_writer_works_out_from_it():
    """The counts block is the writer's tally of the set (write_question_docs.set_counts), and it claims nothing
    about which labels are built: that is the code's to say, one read at a time."""
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("write_question_docs", QUESTION_SET.parent / "write_question_docs.py")
    writer = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(writer)
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    assert d["counts"] == writer.set_counts(d)
    assert not {"labels_built", "answerable_now", "answerable_now_ids"} & set(d["counts"])
    labels = d["labels"] + [lab for g in d["groups"] for q in g["questions"] for lab in q["labels_needed"]]
    assert not [lab["name"] for lab in labels if "label_built" in lab]


def test_a_dark_question_names_what_it_waits_for_and_one_whose_data_is_never_saved_is_dark():
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    questions = {q["id"]: q for g in d["groups"] for q in g["questions"]}
    assert all(q.get("dark_reason", "").strip() for q in questions.values() if q["status"] == "dark")
    assert not [qid for qid, q in questions.items() if q["status"] != "dark" and "dark_reason" in q]
    # the index weights file, the euro futures and the daily closes are never saved, so these can never be asked
    unsaved = {"megacap_cohesion": "spx_leaders/weights.json", "leaders_vs_rest": "spx_leaders/weights.json",
               "heavyweight_gap_split": "spx_leaders/weights.json", "single_name_shock": "spx_leaders/weights.json",
               "macro_lean": "/6E", "month_turn_flow": "daily closes"}
    assert {qid: questions[qid]["status"] for qid in unsaved} == dict.fromkeys(unsaved, "dark")
    assert all(source in questions[qid]["dark_reason"] for qid, source in unsaved.items())


def test_every_cut_the_spec_names_is_a_constant():
    import importlib
    import pkgutil
    from spx_jev import cuts, labels
    modules = [cuts] + [importlib.import_module(f"spx_jev.labels.{m.name}") for m in pkgutil.iter_modules(labels.__path__)]
    for lab in spec()["labels"]:
        for name in re.findall(r"\b[A-Z][A-Z_0-9]{3,}\b", lab["cut"]):
            assert any(hasattr(m, name) for m in modules), f"{lab['path']} names {name}, which the code does not define"


def test_no_rule_promises_an_offline_grade_nothing_runs():
    """The grader scores the sums only: a question's rule says how its label counts the outcome, not that it is graded."""
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    rules = {q["id"]: json.dumps(q["criteria"]) for g in d["groups"] for q in g["questions"]}
    assert [qid for qid, rule in rules.items() if "graded offline" in rule] == []


def test_the_question_doc_names_every_lane_that_asks_from_it():
    from spx_jev.lane import LANES
    doc = json.loads((QUESTION_SET.parent.parent / "questions" / "spx_questions.json").read_text(encoding="utf-8"))
    assert doc["name"] == "SPX JEV questions, every lane"
    assert all(lane.key in doc["how_to_use"] for lane in LANES.values())


def test_shadow_means_asked_and_logged_never_graded_and_never_weighted():
    """What the grader does: it never grades a shadow question, and the weights never see one."""
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    assert "shadow = asked and logged, never graded and never weighted" in d["conventions"]["status"]
    doc = json.loads((QUESTION_SET.parent.parent / "questions" / "spx_questions.json").read_text(encoding="utf-8"))
    assert "shadow ones are asked and logged but never graded or weighted" in doc["how_to_use"]


def test_the_set_describes_the_pre_market_call_the_pre_market_sums_doc_makes():
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    sums = json.loads((QUESTION_SET.parent.parent / "questions" / "spx_premarket_hour.json").read_text(encoding="utf-8"))
    assert f"its call is the average price over the 30 minutes after the settled open ({sums['average']}" in d["conventions"]["cadence"]


def test_the_sets_own_warnings_and_notes_name_only_what_exists():
    """The warnings are what the set holds today, and no note names a gate or a parser the code does not have."""
    d = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    questions = [q for g in d["groups"] for q in g["questions"]]
    texts = json.dumps([{k: q.get(k) for k in ("ask", "instructions", "options", "criteria", "sleep_when")} for q in questions])
    needed = {lab["name"] for q in questions for lab in q["labels_needed"]}
    unused_constants = [c for c in d["constants"] if "{" + c + "}" not in texts]
    unused_labels = [lab["name"] for lab in d["labels"] if lab["name"] not in needed]
    expected = [f"constants defined but unused in question text: {', '.join(unused_constants)}"] if unused_constants else []
    expected += [f"labels defined but unused: {', '.join(unused_labels)}"] if unused_labels else []
    assert d["validation"]["warnings"] == expected
    notes = json.dumps([d["conventions"], [q.get("sleep_when") for q in questions]])
    assert not [name for name in ("session_progress", "DOC_TEXT", "parse_cadence") if name in notes]
