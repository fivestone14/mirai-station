"""The judgment group: each gate against the code's answers (None never fires), the skipped reason and no request when a
gate is off, the group's labels with the headline label and its cut, the ten-year yield's rank, the answers reaching
the Mirai Prediction System's matrix as columns, and the old loop keeping away from them. No JEV, no network."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from conftest import DAY, PRIOR_DAYS, at

from spx_jev import cadence, grade, hour, judgment, store
from spx_jev.ask import GATED, build_requests, load_questions
from spx_jev.cuts import HEADLINE_WINDOW_MIN
from spx_jev.headlines import MAX_TITLES, folder as headlines_folder
from spx_jev.judgment import GATES, Facts, YieldMove, build_judgment_labels, gated_questions, verdicts, yield_move
from spx_jev.labels.ranks import SameClockRank
from spx_jev.lane import LIVE, QUESTIONS
from spx_jev.mirai_prediction.answer_matrix import column_catalog, row_answers

ROW = at(10, 32, ss=15).isoformat()


def _doc():
    return load_questions(QUESTIONS, LIVE.key)


def _judgment_group():
    return next(g for g in _doc()["groups"] if g["id"] == "judgment")


def _fired(rank: SameClockRank | None = SameClockRank(15, 20)) -> YieldMove:
    return YieldMove(4.0, rank, None)


# ---------------------------------------------------------------- the gates

def test_every_judgment_question_in_the_set_is_shadow_gated_and_known_here():
    g = _judgment_group()
    assert set(g["questions"]) == set(GATES) and gated_questions(_doc()).keys() == set(GATES)
    for qid, q in g["questions"].items():
        assert q["status"] == "shadow" and q["sleep_when"] and q["lanes"] == ["thirty_minute"] and q["merged_id"]
        assert "shadow_proof" in q and "never graded and never weighted" in g["purpose"]
    assert {q["merged_id"] for q in g["questions"].values()} == {"TREND-13", "VOLATILITY-15", "BREADTH-10", "MACRO-03", "EVENTS-06", "EVENTS-07"}


@pytest.mark.parametrize("qid, facts, fired", [
    ("push_blowoff_or_fresh", Facts(code={"TREND-10": "up held", "TREND-11": "none"}), True),
    ("push_blowoff_or_fresh", Facts(code={"TREND-10": "none", "TREND-11": "spike faded"}), True),
    ("push_blowoff_or_fresh", Facts(code={"TREND-10": "none", "TREND-11": "none"}), False),
    ("push_blowoff_or_fresh", Facts(code={"TREND-10": None, "TREND-11": None}), False),
    ("push_blowoff_or_fresh", Facts(), False),
    ("quiet_coiled_or_resting", Facts(code={"VOLATILITY-04": "compressed"}), True),
    ("quiet_coiled_or_resting", Facts(code={"VOLATILITY-04": "expanded"}), False),
    ("quiet_coiled_or_resting", Facts(code={"VOLATILITY-04": None}), False),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": "in play, rest followed"}), True),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": "in play, rest did not"}), True),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": "none in play"}), False),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": None}), False),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "up"}), True),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "down"}), True),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "nowhere"}), False),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": None}), False),
    ("yield_move_meaning", Facts(code={"EVENTS-05": "small"}, yield_move=_fired()), True),
    ("yield_move_meaning", Facts(code={"EVENTS-05": None}, yield_move=_fired()), True),
    ("yield_move_meaning", Facts(code={"EVENTS-05": "large, yields up"}, yield_move=_fired()), False),
    ("yield_move_meaning", Facts(code={"EVENTS-05": "small"}, yield_move=_fired(SameClockRank(10, 20))), False),
    ("yield_move_meaning", Facts(code={"EVENTS-05": "small"}, yield_move=YieldMove(None, None, "no quote")), False),
    ("yield_move_meaning", Facts(code={"EVENTS-05": "small"}), False),
    ("news_reaction", Facts(headlines=[{"title": "x", "captured_at": ROW}]), True),
    ("news_reaction", Facts(), False),
])
def test_the_gate_table(qid, facts, fired):
    """A gate fires on the code's answer alone; a None answer, or no answer at all, never fires."""
    assert (verdicts([qid], facts)[qid] is None) is fired


def test_a_gate_not_fired_says_so_with_what_it_read_and_a_failed_gate_is_not_fired(monkeypatch):
    v = verdicts(GATES, Facts(code={"TREND-10": "none", "VOLATILITY-04": None}, yield_move=YieldMove(1.0, None, "its rank needs 10 prior sessions")))
    assert v["push_blowoff_or_fresh"] == f"{GATED} TREND-10 or TREND-11 not fired: TREND-10 'none', TREND-11 not measured this read"
    assert v["quiet_coiled_or_resting"] == f"{GATED} VOLATILITY-04 not fired: VOLATILITY-04 not measured this read"
    assert v["news_reaction"] == f"{GATED} headlines not fired: no headlines captured in the {HEADLINE_WINDOW_MIN} minutes before the read"
    assert v["yield_move_meaning"].startswith(f"{GATED} $TNX or EVENTS-05 not fired: the ten-year yield rose 1.0 basis points since the prior close, not ranked")
    assert all(why.startswith(GATED) for why in v.values())
    monkeypatch.setitem(GATES, "news_reaction", judgment.Gate(("headlines",), lambda f: 1 / 0))
    assert verdicts(["news_reaction"], Facts(headlines=[{}]))["news_reaction"] == f"{GATED} headlines not fired: the gate failed (ZeroDivisionError)"


def test_a_question_whose_gate_is_off_is_skipped_on_the_gates_reason_and_costs_no_request():
    """The packer keeps a gate's reason as it is (never 'asleep: gate: ...'), and the group sends nothing for it."""
    doc = _doc()
    labels = build_judgment_labels(Facts(code={"BREADTH-09": "in play, rest followed"}, headlines=[]))
    state = {"context": {"symbol": "SPX"}, **labels.state}
    gates = verdicts(GATES, Facts(code={"BREADTH-09": "in play, rest followed"}, headlines=[]))
    requests, skipped = build_requests(state, doc, gates=gates)
    mine = next(r for r in requests if r["id"] == "judgment")
    assert list(mine["questions"]) == ["heavyweight_catalyst_or_flow"] and "judgment" in mine["state"]
    assert skipped["judgment"]["news_reaction"].startswith(f"{GATED} headlines not fired") and "asleep" not in skipped["judgment"]["news_reaction"]
    assert {qid for qid in skipped["judgment"]} == set(GATES) - {"heavyweight_catalyst_or_flow"}
    # with every gate off the group sends nothing at all
    requests, skipped = build_requests(state, doc, gates=verdicts(GATES, Facts()))
    assert not [r for r in requests if r["id"] == "judgment"] and skipped["judgment"]["*"] == "nothing to ask in this group this read"
    # a gate nobody decided (the judgment module did not run) leaves the question asleep, never asked
    requests, skipped = build_requests(state, doc)
    assert not [r for r in requests if r["id"] == "judgment"] and skipped["judgment"]["news_reaction"] == "asleep: no label family decides its gate"


def test_the_store_files_a_gated_skip_as_asleep():
    assert store._skip_status("news_reaction", f"{GATED} headlines not fired: nothing captured", {}) == "asleep"


# ---------------------------------------------------------------- the labels

def _headline(minutes_before: int, title: str, source: str = "CNBC") -> dict:
    return {"captured_at": (at(10, 32, ss=15) - timedelta(minutes=minutes_before)).isoformat(timespec="seconds"), "title": title,
            "source": source, "feed": "cnbc_top"}


def test_the_labels_say_each_code_answer_the_ask_names_and_not_measured_for_a_none():
    facts = Facts(code={"TREND-10": "up failed", "TREND-11": None, "VOLATILITY-04": "compressed", "BREADTH-09": "none in play", "MACRO-02": "up",
                        "EVENTS-05": "small"}, yield_move=_fired())
    ls = build_judgment_labels(facts)
    assert set(ls.paths()) == set(judgment.LABELS) and not ls.omitted
    s = ls.state["judgment"]
    assert s["push_exhaustion"].startswith("the code's 60-minute range sweep reads 'up failed' (TREND-10); its volume-spike follow-through is not measured this read (TREND-11)")
    assert s["quiet"].startswith("the last 30 minutes' range against the half hour before reads 'compressed' (VOLATILITY-04)")
    assert s["quiet"].endswith("the ten-year yield rose 4.0 basis points since the prior close, higher than 15 of the last 20 sessions at this minute, top third")
    assert s["heavyweight"] == "the heavyweights since the close reads 'none in play' (BREADTH-09)"
    assert s["macro_gap"] == "SPX against what bonds imply since the close reads 'up' (MACRO-02): 'up' is SPX short of what bonds imply, 'down' past it"
    assert s["yield_move"].endswith("; the latest release's rates surprise reads 'small' (EVENTS-05)")
    assert s["headlines"] == f"no headlines captured in the {HEADLINE_WINDOW_MIN} minutes before the read"


def test_the_headline_label_lists_the_titles_newest_first_with_outlet_and_minute_capped_at_twenty_five():
    facts = Facts(headlines=[_headline(3, "Nvidia slips"), _headline(20, "Yields climb", "Reuters")])
    label = build_judgment_labels(facts).state["judgment"]["headlines"]
    assert label == ("2 headlines captured from 10:12 to 10:29 ET, all before the read, newest first: Nvidia slips (CNBC, 10:29 ET); "
                     "Yields climb (Reuters, 10:12 ET)")
    many = Facts(headlines=[_headline(k + 2, f"title {k}") for k in range(30)])
    label = build_judgment_labels(many).state["judgment"]["headlines"]
    assert label.startswith("30 headlines captured") and "(5 more not shown)" in label and label.count(" ET)") == MAX_TITLES
    assert "title 24 (" in label and "title 25 (" not in label
    feed_down = Facts(headlines=[], headlines_why="the headline feed could not be read: OSError")
    assert build_judgment_labels(feed_down).state["judgment"]["headlines"].endswith("(the headline feed could not be read: OSError)")


def test_the_facts_read_the_headlines_captured_at_least_two_minutes_before_the_read(tmp_path, monkeypatch):
    """The read's label holds what the station captured by the cut, never a headline captured in the last two minutes."""
    monkeypatch.setattr(judgment, "code_answers_for", lambda *a, **k: {})
    hf = headlines_folder(tmp_path)
    hf.mkdir(parents=True)
    lines = [_headline(1, "too fresh"), _headline(2, "at the cut"), _headline(61, "kept, 59 minutes before the cut"), _headline(63, "too old")]
    (hf / f"{DAY}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    facts = judgment.facts_for(tmp_path, "live", DAY, {"read_id": f"live:{ROW}", "row_ts": ROW}, record=False)
    assert [h["title"] for h in facts.headlines] == ["at the cut", "kept, 59 minutes before the cut"]
    assert verdicts(["news_reaction"], facts)["news_reaction"] is None
    assert facts.yield_move.basis_points is None and "no $TNX quote" in facts.yield_move.why


# ---------------------------------------------------------------- the ten-year yield's move

def _write_tnx(state_dir, now_last: float, prior_close: float, sessions: list[tuple[str, float]]):
    """Today's $TNX quote at 10:31 and, per prior session, its 10:31 bar; every session closes at ``prior_close`` (daily_closes),
    so each session's move at the minute is its bar less that."""
    ctx = state_dir / "spx_jev" / "context"
    (ctx / "bars").mkdir(parents=True)
    (ctx / f"{DAY}.jsonl").write_text(json.dumps({"ts": at(10, 31).isoformat(), "quotes": {"$TNX": {"last": now_last, "close": prior_close}}}) + "\n"
                                      + json.dumps({"ts": at(10, 40).isoformat(), "quotes": {"$TNX": {"last": 99.0, "close": prior_close}}}) + "\n")
    oldest = min(day for day, _ in sessions)
    closes = [{"day": (date.fromisoformat(oldest) - timedelta(days=1)).isoformat(), "close": prior_close}]
    for day, at_minute in sessions:
        closes.append({"day": day, "close": prior_close})
        (ctx / "bars" / f"{day}.jsonl").write_text(json.dumps({"ts": at(10, 31, day).isoformat(), "bars": {"$TNX": {"ts": at(10, 30, day).isoformat(), "close": at_minute}}}) + "\n"
                                                   + json.dumps({"ts": at(11, 0, day).isoformat(), "bars": {"$TNX": {"ts": at(10, 59, day).isoformat(), "close": 99.0}}}) + "\n")
    dc = state_dir / "spx_jev" / "daily_closes"
    dc.mkdir(parents=True)
    (dc / "$TNX.jsonl").write_text("".join(json.dumps(r) + "\n" for r in closes))


def test_the_yields_move_is_ranked_at_the_same_minute_against_each_sessions_own_prior_close(tmp_path):
    sessions = [(d, 41.0 + 0.1 * k) for k, d in enumerate(PRIOR_DAYS)]       # moves of 0 to 0.9 ($TNX is ten times the yield)
    _write_tnx(tmp_path, 41.5, 41.0, sessions)
    ym = yield_move(tmp_path, DAY, ROW)
    assert ym.basis_points == 5.0 and ym.rank == SameClockRank(5, 10) and not ym.fired and "rose 5.0 basis points" in ym.words()
    (tmp_path / "spx_jev" / "context" / f"{DAY}.jsonl").write_text(json.dumps({"ts": at(10, 31).isoformat(), "quotes": {"$TNX": {"last": 39.6, "close": 41.0}}}) + "\n")
    ym = yield_move(tmp_path, DAY, ROW)
    assert ym.basis_points == -14.0 and ym.rank == SameClockRank(10, 10) and ym.fired and ym.words().endswith("top third")


def test_too_few_sessions_leave_the_yields_move_unranked_and_the_gate_off(tmp_path):
    _write_tnx(tmp_path, 43.0, 41.0, [(d, 41.1) for d in PRIOR_DAYS[:4]])
    ym = yield_move(tmp_path, DAY, ROW)
    assert ym.basis_points == 20.0 and ym.rank is None and "needs 10 prior sessions" in ym.why and not ym.fired
    assert verdicts(["yield_move_meaning"], Facts(yield_move=ym))["yield_move_meaning"].startswith(f"{GATED} $TNX or EVENTS-05 not fired: the ten-year yield rose 20.0 basis points")


# ---------------------------------------------------------------- the matrix takes them, the old loop does not

def _read_with_judgment_answer(answered: bool = True) -> dict:
    q = _judgment_group()["questions"]["news_reaction"]
    sent = {k: q[k] for k in ("type", "instructions", "criteria")}
    reply = {"model": "jev-1", "answers": {"news_reaction": {"type": "choice", "choice": "overreacting", "confidence": 0.7,
                                                            "probabilities": {"shrugging_off": 0.1, "overreacting": 0.7, "in_proportion": 0.1, "unclear": 0.1}}}}
    return {"read_id": f"live:{ROW}", "lane": "live", "row_ts": ROW, "sent": True, "spot": 7700.0, "sigma": 75.0, "labels": {}, "omitted": {},
            "requests": [{"id": "judgment", "state": {}, "questions": {"news_reaction": sent}}],
            "skipped": {"judgment": {"macro_gap_equity_reason": f"{GATED} MACRO-02 not fired: MACRO-02 'nowhere'"}},
            "responses": {"judgment": reply} if answered else {"judgment": {"error": "JEV unreachable"}},
            "cadence": {"from": None, "held": {}, "not_due": {}, "asked": ["news_reaction"], "reasked": {}}, "hour": None, "market_context": None, "event": None}


def test_an_answered_judgment_question_is_a_layer_three_column_of_the_matrix_and_a_gated_one_is_silent():
    parts = store._read_rows(date.fromisoformat(DAY), "live", _read_with_judgment_answer(), {}, {})
    answers = {a["question_id"]: a for a in parts["answers"]}
    assert answers["news_reaction"]["status"] == "answered" and answers["news_reaction"]["pick"] == "overreacting" and answers["news_reaction"]["group_id"] == "judgment"
    assert answers["macro_gap_equity_reason"]["status"] == "asleep" and answers["macro_gap_equity_reason"]["pick"] is None
    jev = {a["question_id"]: str(a["pick"]) for a in parts["answers"] if a["status"] == "answered" and a["pick"] is not None}
    row = row_answers({}, jev)
    assert row["jev:news_reaction"] == "overreacting" and "jev:macro_gap_equity_reason" not in row
    assert column_catalog(set(jev), {"news_reaction": "judgment"})["jev:news_reaction"] == {"layer": 3, "group": "jev:judgment", "family": "jev"}
    lost = {a["question_id"]: a for a in store._read_rows(date.fromisoformat(DAY), "live", _read_with_judgment_answer(False), {}, {})["answers"]}
    assert lost["news_reaction"]["status"] == "lost"


def test_the_old_loop_never_sums_weights_holds_or_pools_a_judgment_answer():
    doc = _doc()
    answered = {"news_reaction": {"pick": "overreacting", "probabilities": {"overreacting": 0.7}, "confidence": 0.7}}
    sentences, left_out = hour.answer_sentences(doc, answered)
    assert sentences == {} and left_out == {"news_reaction": "a shadow forecast, never an input"}
    assert not set(GATES) & set(grade.live_options(doc))                       # the grader's weights never see them
    live = {qid for g in doc["groups"] for qid, q in g["questions"].items() if q.get("status") == "live"}
    assert not set(GATES) & live                                                # pool_snapshots takes its members from the live questions
    skip, held = cadence.plan(doc, {"news_reaction": {"row_ts": ROW, "answer": answered["news_reaction"]}}, {}, at(11, 2), {}, {}, "11:02")
    assert "news_reaction" not in held and "news_reaction" not in skip         # never held: asked afresh whenever its gate fires
    assert "news_reaction" not in cadence.recount([], doc)["questions"]
