"""The judgment group: each gate against the code's answers (None never fires), the skipped reason and no request when a
gate is off, the group's labels with the headline label and its cut, the ten-year yield's rank, the answers reaching
the Mirai Prediction System's matrix as columns, and the old loop keeping away from them. No JEV, no network."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import DAY, PRIOR_DAYS, at

from spx_jev import cadence, grade, hour, judgment, store
from spx_jev.ask import GATED, build_requests, load_questions
from spx_jev.cuts import HEADLINE_FRESH_MIN, HEADLINE_WINDOW_MIN
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


def _ago(minutes: int) -> str:
    """A capture time this many minutes before ROW."""
    return (at(10, 32, ss=15) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _news(title: str, minutes_before: int, feed: str = "cnbc_top") -> dict:
    return {"title": title, "captured_at": _ago(minutes_before), "feed": feed, "source": feed}


# ---------------------------------------------------------------- the gates

DROPPED = {"push_blowoff_or_fresh", "quiet_coiled_or_resting"}       # dark since the question pressure test of 2026-10-07


def test_every_judgment_question_in_the_set_is_shadow_gated_and_known_here():
    g = _judgment_group()
    assert set(g["questions"]) == set(GATES) and gated_questions(_doc()).keys() == set(GATES) - DROPPED
    for qid, q in g["questions"].items():
        assert q["status"] == ("dark" if qid in DROPPED else "shadow") and q["sleep_when"] and q["lanes"] == ["thirty_minute"] and q["merged_id"]
        assert "shadow_proof" in q and "never graded and never weighted" in g["purpose"]
    assert all(g["questions"][qid]["dark_reason"] == "dropped in the question pressure test 2026-10-07" for qid in DROPPED)
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
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": "in play up, rest followed"}), True),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": "in play down, rest did not"}), True),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": "none in play"}), False),
    ("heavyweight_catalyst_or_flow", Facts(code={"BREADTH-09": None}), False),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "up"}), False),             # the top fifth only
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "down"}), False),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "far up"}), True),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "far down"}), True),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "far up"}, answered_code={"MACRO-02": "up"}), True),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "far up"}, answered_code={"MACRO-02": "far down"}), True),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "far up"}, answered_code={"MACRO-02": "far up"}), False),   # once until it changes
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": "nowhere"}), False),
    ("macro_gap_equity_reason", Facts(code={"MACRO-02": None}), False),
    ("yield_move_meaning", Facts(code={"EVENTS-03": "no release"}, yield_move=_fired()), True),
    ("yield_move_meaning", Facts(code={"EVENTS-03": None}, yield_move=_fired()), True),
    ("yield_move_meaning", Facts(code={"EVENTS-03": "up then extended"}, yield_move=_fired()), False),     # the release names the cause
    ("yield_move_meaning", Facts(code={"EVENTS-03": "reversed down"}, yield_move=_fired()), False),
    ("yield_move_meaning", Facts(code={"EVENTS-03": "no release"}, yield_move=_fired(SameClockRank(10, 20))), False),
    ("yield_move_meaning", Facts(code={"EVENTS-03": "no release"}, yield_move=YieldMove(None, None, "no quote")), False),
    ("yield_move_meaning", Facts(code={"EVENTS-03": "no release"}), False),
    ("news_reaction", Facts(headlines=[_news("Treasury yields jump after hot CPI", 5)], row_ts=ROW), True),
    ("news_reaction", Facts(headlines=[_news("Nvidia raises its guidance", 31)], row_ts=ROW), True),        # a heavyweight's results
    ("news_reaction", Facts(headlines=[_news("Nvidia unveils a chip", 5)], row_ts=ROW), False),              # a heavyweight, no results
    ("news_reaction", Facts(headlines=[_news("Stocks slide as yields jump", 5)], row_ts=ROW), False),        # not an index-wide mover
    ("news_reaction", Facts(headlines=[_news("Fed's Waller sees room to cut", 5, "google_news")], row_ts=ROW), False),   # not a preferred feed
    ("news_reaction", Facts(headlines=[_news("Fed's Waller sees room to cut", 33)], row_ts=ROW), False),     # not fresh
    ("news_reaction", Facts(headlines=[_news("Fed's Waller sees room to cut", 10)], row_ts=ROW, news_seen_until=_ago(8)), False),   # seen by the last ask
    ("news_reaction", Facts(headlines=[_news("Fed's Waller sees room to cut", 6)], row_ts=ROW, news_seen_until=_ago(8)), True),
    ("news_reaction", Facts(headlines=[_news("Fed's Waller sees room to cut", 5)]), False),                  # no read time
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
    stale = verdicts(["news_reaction", "macro_gap_equity_reason"], Facts(code={"MACRO-02": "far down"}, answered_code={"MACRO-02": "far down"},
                                                                         headlines=[_news("Fed's Waller sees room to cut", 40)], row_ts=ROW))
    assert stale["news_reaction"] == (f"{GATED} headlines not fired: no new index-wide headline (CNBC, MarketWatch or the Fed: the Fed, a major "
                                      f"release, trade, Treasury yields, a heavyweight's results) first captured in the {HEADLINE_FRESH_MIN} minutes "
                                      f"before the cut and since the last ask, of 1 in the {HEADLINE_WINDOW_MIN}")
    assert stale["macro_gap_equity_reason"] == (f"{GATED} MACRO-02 not fired: MACRO-02 'far down' as on the last read JEV answered it on "
                                                f"(answered once until it changes)")
    assert v["yield_move_meaning"].startswith(f"{GATED} $TNX or EVENTS-03 not fired: the ten-year yield rose 1.0 basis points since the prior close, not ranked")
    assert all(why.startswith(GATED) for why in v.values())
    monkeypatch.setitem(GATES, "news_reaction", judgment.Gate(("headlines",), lambda f: 1 / 0))
    assert verdicts(["news_reaction"], Facts(headlines=[{}]))["news_reaction"] == f"{GATED} headlines not fired: the gate failed (ZeroDivisionError)"


def test_a_question_whose_gate_is_off_is_skipped_on_the_gates_reason_and_costs_no_request():
    """The packer keeps a gate's reason as it is (never 'asleep: gate: ...'), and the group sends nothing for it."""
    doc = _doc()
    labels = build_judgment_labels(Facts(code={"BREADTH-09": "in play up, rest followed"}, headlines=[]))
    state = {"context": {"symbol": "SPX"}, **labels.state}
    gates = verdicts(GATES, Facts(code={"BREADTH-09": "in play up, rest followed"}, headlines=[]))
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
                        "EVENTS-03": "no release"}, yield_move=_fired())
    ls = build_judgment_labels(facts)
    assert set(ls.paths()) == set(judgment.LABELS) and not ls.omitted
    s = ls.state["judgment"]
    assert s["push_exhaustion"].startswith("the code's 60-minute range sweep reads 'up failed' (TREND-10); its volume-spike follow-through is not measured this read (TREND-11)")
    assert s["quiet"].startswith("the last 30 minutes' range against the half hour before reads 'compressed' (VOLATILITY-04)")
    assert s["quiet"].endswith("the ten-year yield rose 4.0 basis points since the prior close, higher than 15 of the last 20 sessions at this minute, top third")
    assert s["heavyweight"] == "the heavyweights since the close reads 'none in play' (BREADTH-09)"
    assert s["macro_gap"] == "SPX against what bonds imply since the close reads 'up' (MACRO-02): 'up' is SPX short of what bonds imply, 'down' past it, 'far' the top fifth"
    assert s["yield_move"].endswith("; the latest release's reaction reads 'no release' (EVENTS-03)")
    assert s["headlines"] == f"no headlines captured in the {HEADLINE_WINDOW_MIN} minutes before the read"


def test_the_headline_label_lists_the_titles_newest_first_with_outlet_and_minute_capped_at_twenty_five():
    facts = Facts(headlines=[_headline(3, "Nvidia slips"), _headline(20, "Yields climb", "Reuters")])
    label = build_judgment_labels(facts).state["judgment"]["headlines"]
    assert label == ("2 headlines captured from 10:12 to 10:29 ET, all before the read, newest first: Nvidia slips (CNBC, 10:29 ET); "
                     "Yields climb (Reuters, 10:12 ET)")
    many = Facts(headlines=[_headline(k + 2, f"title {k}") for k in range(30)])
    label = build_judgment_labels(many).state["judgment"]["headlines"]
    assert label.startswith("30 headlines captured from 10:01 to 10:30 ET") and "(5 more not shown; " in label and label.count(" ET)") == MAX_TITLES
    assert "title 24 (" in label and "title 25 (" not in label
    # more than a label holds: the CNBC, MarketWatch and Fed titles and the market news go first, whatever their age
    noisy = [{**_headline(k + 2, f"noise {k}"), "feed": "google_news"} for k in range(30)]
    older = [{**_headline(50, "Treasury yields climb"), "feed": "google_news"}, {**_headline(55, "a CNBC feature"), "feed": "cnbc_top"}]
    label = build_judgment_labels(Facts(headlines=noisy + older)).state["judgment"]["headlines"]
    assert "Treasury yields climb (" in label and "a CNBC feature (" in label and "noise 22 (" in label and "noise 23 (" not in label
    assert label.index("noise 0 (") < label.index("Treasury yields climb (") < label.index("a CNBC feature (")          # still newest first
    feed_down = Facts(headlines=[], headlines_why="the headline feed could not be read: OSError")
    assert build_judgment_labels(feed_down).state["judgment"]["headlines"].endswith("(the headline feed could not be read: OSError)")


def test_the_facts_read_the_headlines_captured_at_least_two_minutes_before_the_read(tmp_path, monkeypatch):
    """The read's label holds what the station captured by the cut, never a headline captured in the last two minutes."""
    monkeypatch.setattr(judgment, "code_answers_for", lambda *a, **k: {})
    hf = headlines_folder(tmp_path)
    hf.mkdir(parents=True)
    lines = [_headline(1, "Fed too fresh"), _headline(2, "Fed at the cut"), _headline(61, "Fed kept, 59 minutes before the cut"),
             _headline(63, "Fed too old")]
    (hf / f"{DAY}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    facts = judgment.facts_for(tmp_path, "live", DAY, {"read_id": f"live:{ROW}", "row_ts": ROW}, record=False)
    assert [h["title"] for h in facts.headlines] == ["Fed at the cut", "Fed kept, 59 minutes before the cut"] and facts.row_ts == ROW
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
    assert verdicts(["yield_move_meaning"], Facts(yield_move=ym))["yield_move_meaning"].startswith(f"{GATED} $TNX or EVENTS-03 not fired: the ten-year yield rose 20.0 basis points")


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
    assert column_catalog(set(jev), {"news_reaction": "judgment"})["jev:news_reaction"] == {"layer": 3, "group": "jev:news_reaction", "family": "jev"}   # its own vote
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


# ---------------------------------------------------------------- the round-2 fixes (step 2, 2026-10-08)

def test_the_heavyweight_label_names_the_stock_its_side_and_pull_and_the_headlines_naming_it():
    facts = Facts(code={"BREADTH-09": "in play down, rest did not"}, heavyweight=("NVDA", -0.0012, 0.0005),
                  headlines=[_headline(3, "Nvidia shares slip as export curbs widen"), _headline(9, "Oil climbs"), _headline(12, "NVDA options swell")])
    label = build_judgment_labels(facts).state["judgment"]["heavyweight"]
    assert label == ("the heavyweights since the close reads 'in play down, rest did not' (BREADTH-09): NVDA pulls the index down, its move since "
                     "the prior close beyond its usual link to the index worth -0.12% of the index; the rest of the index moved +0.05% since the "
                     "close; headlines naming NVDA or Nvidia, newest first: Nvidia shares slip as export curbs widen (CNBC, 10:29 ET); "
                     "NVDA options swell (CNBC, 10:20 ET)")
    alone = build_judgment_labels(Facts(code={"BREADTH-09": "in play up, rest followed"}, heavyweight=("BRK/B", 0.0004, 0.001))).state["judgment"]["heavyweight"]
    assert alone.endswith("no captured headline names BRK/B or Berkshire or BRK.B")
    quiet = build_judgment_labels(Facts(code={"BREADTH-09": "none in play"}, heavyweight=("NVDA", 0.0001, 0.0))).state["judgment"]["heavyweight"]
    assert quiet == "the heavyweights since the close reads 'none in play' (BREADTH-09)"


def test_the_macro_gap_label_states_the_gaps_size_and_side_in_its_top_fifth():
    far = build_judgment_labels(Facts(code={"MACRO-02": "far up"}, macro_gap=(-0.0042, -32.6))).state["judgment"]["macro_gap"]
    assert far.endswith("; SPX sits 0.42% (about 33 points) below what the ten-year note's move since the prior close implies, so the gap "
                        "closing would move SPX up")
    past = build_judgment_labels(Facts(code={"MACRO-02": "far down"}, macro_gap=(0.003, 23.1))).state["judgment"]["macro_gap"]
    assert "0.30% (about 23 points) above" in past and past.endswith("would move SPX down")
    near = build_judgment_labels(Facts(code={"MACRO-02": "up"}, macro_gap=(-0.001, -7.7))).state["judgment"]["macro_gap"]
    assert near == "SPX against what bonds imply since the close reads 'up' (MACRO-02): 'up' is SPX short of what bonds imply, 'down' past it, 'far' the top fifth"


def _archived_read(hhmm: tuple[int, int], asked: tuple[str, ...] = (), answered: dict | None = None, lost: bool = False) -> dict:
    row_ts = at(*hhmm).isoformat()
    replies = {"judgment": {"error": "JEV unreachable"}} if lost else {
        "judgment": {"answers": {q: {"type": "choice", "choice": c} for q, c in (answered or {}).items()}}}
    return {"kind": "read", "read_id": f"live:{row_ts}", "lane": "live", "row_ts": row_ts,
            "requests": [{"id": "judgment", "questions": {q: {} for q in asked}}], "responses": replies}


def _day_on_file(tmp_path, reads: list[dict], code: dict[str, str]):
    from spx_jev.lane import LANES
    from spx_jev.mirai_prediction.paths import data_root, raw_code_features_file
    archive = Path(LANES["live"].archive_folder(tmp_path)) / f"{DAY}.jsonl"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("".join(json.dumps(r) + "\n" for r in reads))
    raw = raw_code_features_file(data_root(tmp_path), DAY)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("".join(json.dumps({"read_id": r["read_id"], "lane": "live", "row_ts": r["row_ts"], "answers": {"MACRO-02": code[r["row_ts"]]}}) + "\n"
                           for r in reads))


def test_the_bond_gap_is_answered_once_until_it_changes_and_a_lost_ask_is_retried(tmp_path):
    """The once-only rule reads the last read JEV ANSWERED the question on, never one whose ask was lost."""
    q = "macro_gap_equity_reason"
    first, lost = _archived_read((10, 1), (q,), {q: "no_reason"}), _archived_read((10, 31), (q,), lost=True)
    _day_on_file(tmp_path, [first, lost], {first["row_ts"]: "far down", lost["row_ts"]: "far down"})
    facts = Facts(code={"MACRO-02": "far down"})
    judgment.asks_before(facts, tmp_path, "live", DAY, at(11, 1).isoformat())
    assert facts.answered_code == {"MACRO-02": "far down"} and verdicts([q], facts)[q] is not None          # answered at 10:01: held
    _day_on_file(tmp_path, [_archived_read((10, 1), (q,), lost=True)], {at(10, 1).isoformat(): "far down"})
    retry = Facts(code={"MACRO-02": "far down"})
    judgment.asks_before(retry, tmp_path, "live", DAY, at(10, 31).isoformat())
    assert retry.answered_code == {} and verdicts([q], retry)[q] is None                                 # the lost ask is asked again
    changed = Facts(code={"MACRO-02": "far up"}, answered_code={"MACRO-02": "far down"})
    assert verdicts([q], changed)[q] is None


def test_the_news_gate_starts_after_the_cut_of_the_last_read_that_asked_it(tmp_path):
    asked = _archived_read((10, 1), ("news_reaction",), {"news_reaction": "in_proportion"})
    _day_on_file(tmp_path, [asked, _archived_read((10, 31))], {asked["row_ts"]: "up", at(10, 31).isoformat(): "up"})
    facts = Facts()
    judgment.asks_before(facts, tmp_path, "live", DAY, at(11, 1).isoformat())
    assert facts.news_seen_until == at(9, 59).isoformat()


def test_the_matrix_signs_the_picks_that_point_a_way_only_with_the_codes_side():
    code = {"BREADTH-09": "in play down, rest followed", "MACRO-02": "far up"}
    row = row_answers(code, {"heavyweight_catalyst_or_flow": "no_clear_cause", "macro_gap_equity_reason": "no_reason", "news_reaction": "fading_up_move"})
    assert (row["jev:heavyweight_catalyst_or_flow"], row["jev:macro_gap_equity_reason"], row["jev:news_reaction"]) == (
        "no_clear_cause:down", "no_reason:up", "fading_up_move")
    assert row_answers(code, {"macro_gap_equity_reason": "equity_reason"})["jev:macro_gap_equity_reason"] == "equity_reason"
    assert row_answers({}, {"heavyweight_catalyst_or_flow": "material_cause"})["jev:heavyweight_catalyst_or_flow"] == "material_cause"   # no side known


def test_a_judgment_pick_renamed_out_of_its_options_is_left_out_of_the_matrix(monkeypatch):
    from spx_jev.mirai_prediction import answer_matrix
    from spx_jev.mirai_prediction.answer_matrix import judgment_options
    stored = [{"read_id": "live:a", "question_id": "news_reaction", "pick": "shrugging_off", "group_id": "judgment", "day": "2026-10-07"},
              {"read_id": "live:b", "question_id": "news_reaction", "pick": "fading_up_move", "group_id": "judgment", "day": "2026-10-08"},
              {"read_id": "live:a", "question_id": "q_old", "pick": "rising", "group_id": "price_move", "day": "2026-10-07"}]
    monkeypatch.setattr(answer_matrix, "_parquet_glob", lambda folder: "answers")
    monkeypatch.setattr(answer_matrix, "_query", lambda sql: stored)
    picks, groups, last = answer_matrix.load_jev_answers("state", "live", "2026-10-09")
    assert picks == {"live:a": {"q_old": "rising"}, "live:b": {"news_reaction": "fading_up_move"}} and last["news_reaction"] == "2026-10-08"
    options = judgment_options()
    assert options["news_reaction"] == {"priced_lean_up", "priced_lean_down", "fading_up_move", "fading_down_move", "in_proportion", "unclear"}
    assert options["yield_move_meaning"] == {"stock_tailwind", "stock_headwind", "unclear"} and "shrugging_off" not in options["news_reaction"]
