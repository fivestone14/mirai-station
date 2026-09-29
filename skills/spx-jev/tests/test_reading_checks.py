"""The reading checks (spx_jev/reading_checks.py): the option a question's labels name, JEV's pick against it,
and a day's archive summed by question, read only."""
from __future__ import annotations

import json

from spx_jev import reading_checks
from spx_jev.reading_checks import check_day, check_record, named_answer, summary

TILT = {"type": "choice",
        "instructions": "Read `skew.put_tilt_vs_usual`. How steeply are same-day puts priced over same-day calls right now?",
        "criteria": {"steep_put_tilt": "a steep put tilt for this minute: puts priced over calls more steeply than on most recent "
                                       "sessions at this minute",
                     "usual_tilt": "a usual tilt for this minute",
                     "flat_put_tilt": "a flat tilt for this minute: puts priced over calls less steeply than on most recent sessions "
                                      "at this minute, but still at or above calls"}}
FLAT = ("same-day 25-delta puts are priced 1.4 vol points above 25-delta calls, 0.09 of the at-the-money level; its tilt beats "
        "3 of the last 16 sessions at 10:01: a flat tilt for this minute; the far-put wing is flatter than usual")
REACTION = {"type": "choice", "instructions": "Read `event.reaction`. What has price done since the first reaction?",
            "criteria": {"holding": "kept the half line or more of the first reaction without extending",
                         "reversed": "crossed back through where the reaction started"}}


def record(state: dict, question: dict, qid: str, choice: str, read_id: str = "live:2026-09-28T10:02:00-04:00") -> dict:
    return {"read_id": read_id, "lane": "live", "requests": [{"id": "g", "state": state, "questions": {qid: question}}],
            "responses": {"g": {"model": "jev", "answers": {qid: {"type": "choice", "choice": choice}}}}}


def test_a_label_ending_its_clause_in_an_options_lead_names_that_option():
    assert named_answer(TILT, {"skew": {"put_tilt_vs_usual": FLAT}}) == "flat_put_tilt"
    realized = {"type": "choice", "instructions": "Read `vol.realized_vs_clock`.",
                "criteria": {"calm": "in the bottom fifth", "ordinary": "between the top and bottom fifths: ordinary for this half hour"}}
    said = ("over the last 30 minutes SPX's realized swing was 0.20 sigma, more than 12 of the last 19 sessions at this time of day, "
            "between the top and bottom fifths: ordinary for this half hour")
    assert named_answer(realized, {"vol": {"realized_vs_clock": said}}) == "ordinary"


def test_words_inside_a_clause_name_nothing_and_neither_do_two_options_or_no_label():
    said = "since then it has given back 12% of it, within the half line, and has not crossed back through where the reaction started"
    assert named_answer(REACTION, {"event": {"reaction": said}}) is None
    both = f"{FLAT}; so a steep put tilt for this minute: a steep put tilt for this minute"
    assert named_answer(TILT, {"skew": {"put_tilt_vs_usual": both}}) is None
    assert named_answer(TILT, {}) is None


def test_a_pick_is_checked_against_the_named_option_and_a_day_is_summed_by_question(tmp_path):
    """The put tilt read steep where its label said flat on Monday: the check the set promised would have shown it."""
    wrong = record({"skew": {"put_tilt_vs_usual": FLAT}}, TILT, "put_tilt_vs_clock", "steep_put_tilt")
    assert check_record(wrong) == [{"read_id": "live:2026-09-28T10:02:00-04:00", "lane": "live", "question": "put_tilt_vs_clock",
                                    "pick": "steep_put_tilt", "named": "flat_put_tilt", "agrees": False}]
    right = record({"skew": {"put_tilt_vs_usual": FLAT}}, TILT, "put_tilt_vs_clock", "flat_put_tilt", "live:2026-09-28T10:32:00-04:00")
    unnamed = record({"event": {"reaction": "price fell"}}, REACTION, "move_reaction_path", "holding")
    archive = tmp_path / "spx_jev" / "archive"
    archive.mkdir(parents=True)
    (archive / "2026-09-28.jsonl").write_text("\n".join([json.dumps(wrong), "not json", json.dumps(right), json.dumps(unnamed),
                                                        json.dumps({"kind": "close_out"})]) + "\n")
    by = summary(check_day(tmp_path, "2026-09-28"))
    assert by == {"put_tilt_vs_clock": {"checked": 2, "agreed": 1, "disagreed": [
        {"read_id": "live:2026-09-28T10:02:00-04:00", "pick": "steep_put_tilt", "named": "flat_put_tilt"}]}}
    assert check_day(tmp_path, "2026-09-29") == []


def test_the_command_reads_and_prints_and_writes_nothing(tmp_path, capsys):
    archive = tmp_path / "spx_jev" / "archive"
    archive.mkdir(parents=True)
    day = archive / "2026-09-28.jsonl"
    day.write_text(json.dumps(record({"skew": {"put_tilt_vs_usual": FLAT}}, TILT, "put_tilt_vs_clock", "steep_put_tilt")) + "\n")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert reading_checks.main(["--state-dir", str(tmp_path), "--day", "2026-09-28"]) == 0
    assert capsys.readouterr().out == ("put_tilt_vs_clock: 0 of 1 picks agree with the option its labels name\n"
                                       "    live:2026-09-28T10:02:00-04:00: picked steep_put_tilt, the labels name flat_put_tilt\n")
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
