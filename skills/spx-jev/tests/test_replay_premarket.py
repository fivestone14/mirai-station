"""The premarket replay (spec/replay_premarket.py), offline: two synthetic nights replayed through the harness
(the lane's scene and labels stood in for) without writing under the state dir, one read through the lane's own
label families, the outcomes from the settled open in the pre-open ruler, each sign-free question's reference,
and the judging on made-up answers: a real link holds, an unrelated one is noise, a thin sample is too few,
and a batch of unrelated ones names none as holding once adjusted for the batch."""
from __future__ import annotations

import importlib.util
import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from conftest import at, flat_bars, night_row
from spx_jev import events, overnight
from spx_jev.labels.label_set import LabelSet
from spx_jev.labels.measures import ET
from spx_jev.sessions import previous_trading_day
from spx_jev.state_builder import load_jsonl

SPEC = Path(__file__).resolve().parent.parent / "spec" / "replay_premarket.py"
_spec = importlib.util.spec_from_file_location("replay_premarket", SPEC)
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)

NIGHTS = (date(2026, 9, 17), date(2026, 9, 18))           # a Thursday and a Friday; 09-16 is the first one's prior session
MOVE = {"2026-09-17": 15.0, "2026-09-18": -30.0}           # SPX points from the settled open to 10:04
VERDICT = {"2026-09-17": "up", "2026-09-18": "big_down"}   # the code's answer to overnight_move_vs_expected


def _session(day: str, move: float) -> list[dict]:
    """A full session at 7700 whose bars from 09:35 on stand ``move`` points away."""
    bars = flat_bars(390, day=day)
    for b in bars[5:]:
        b.update(open=7700.0 + move, high=7700.5 + move, low=7699.5 + move, close=7700.0 + move)
    return bars


@pytest.fixture
def state(tmp_path):
    state_dir = tmp_path / "state"
    folder = state_dir / "reversion" / "bars"
    folder.mkdir(parents=True)
    for day, move in {"2026-09-16": 0.0, **MOVE}.items():
        (folder / f"{day}-SPX.json").write_text(json.dumps(_session(day, move)), encoding="utf-8")
    for night in NIGHTS:
        start = overnight.night_window(night)[0]
        overnight.write_night(state_dir, night.isoformat(), [night_row("/ES", start + timedelta(minutes=k), 7750.0, minutes=5,
                                                                       day=night.isoformat())
                                                             for k in range(0, 17 * 60, 5)])
    return state_dir


@pytest.fixture
def stubbed(monkeypatch, premarket_scene_factory):
    """The lane's scene and labels stood in for: the ruler 75 points, /ES up on the night, and only
    overnight.es_move written, with the day's verdict; gap_origin asleep."""
    def scene(state_dir, now):
        rows = load_jsonl(overnight.night_path(state_dir, now.date().isoformat()))
        rows.append(night_row("/ES", now - timedelta(minutes=2), 7760.0, day=now.date().isoformat()))
        return premarket_scene_factory(now, rows, sigma=75.0, state_dir=state_dir)

    def labels(scene):
        got = LabelSet()
        day = scene.now.date().isoformat()
        got.put("overnight.es_move", "S&P futures are up since their 16:00 price.",
                figure={"kind": "rank", "value": 1.0, "cut": "middle third", "verdict": VERDICT[day]})
        got.sleep("gap_origin", "the night's net move ranks in the bottom third")
        return got

    monkeypatch.setattr(replay.premarket, "make_premarket_scene", scene)
    monkeypatch.setattr(replay, "build_labels", labels)


def _files(folder: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime_ns for p in folder.rglob("*")}


def test_two_nights_replay_through_the_harness_without_writing_under_the_state_dir(state, stubbed, tmp_path):
    before = _files(state)
    out = tmp_path / "replay"
    assert replay.main(["--state-dir", str(state), "--out", str(out), "--report", str(tmp_path / "report.md")]) == 0
    assert _files(state) == before

    reads = [json.loads(line) for line in (out / "reads.jsonl").read_text(encoding="utf-8").splitlines()]
    checkpoints = {d: replay.premarket.checkpoints(d) for d in NIGHTS}
    assert [(r["day"], r["checkpoint"]) for r in reads] == [(d.isoformat(), c) for d in NIGHTS for c in checkpoints[d]]
    last = {r["day"]: r for r in reads if r["checkpoint"] == "09:28"}
    for day, r in last.items():
        assert r["ruler"] == 75.0
        assert r["questions"]["overnight_move_vs_expected"] == {"answer": VERDICT[day], "fate": "asked"}
        assert r["questions"]["gap_origin"]["fate"] == "asleep" and r["questions"]["gap_origin"]["side"] == 1
        assert r["questions"]["pm_overnight_session"]["fate"] == "missing"
    early = next(r for r in reads if r["checkpoint"] == "02:35")
    assert early["questions"]["overnight_move_vs_expected"]["fate"] == "not_due"

    outcomes = {o["day"]: o for o in map(json.loads, (out / "outcomes.jsonl").read_text(encoding="utf-8").splitlines())}
    for day, move in MOVE.items():
        o = outcomes[day]
        assert o["settled_open"] == 7700.0                                  # the 09:34 close; the move starts at 09:35
        assert o["open_10"] == o["open_30"] == o["open_60"] == o["to_close"] == pytest.approx(move / 75.0, abs=1e-4)
        assert o["range_30"] == pytest.approx(1.0 / 75.0, abs=1e-4)
    assert outcomes["2026-09-17"]["gap"] == 0.0
    assert outcomes["2026-09-18"]["gap"] == pytest.approx(-15.0 / 75.0, abs=1e-4)   # from the 09-17 close, 15 points up

    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    for qid in {qid for g in replay.premarket_doc()["groups"] for qid in g["questions"]}:
        assert f"| {qid} |" in text
    assert "too few" in text and "Bitcoin session questions" in text


def test_the_replay_refuses_to_write_inside_the_state_dir(state, stubbed):
    with pytest.raises(SystemExit):
        replay.main(["--state-dir", str(state), "--out", str(state / "spx_jev" / "replay")])
    assert not (state / "spx_jev" / "replay").exists()


def test_an_unfinished_night_is_not_replayed(state):
    assert replay.replayable_days(state, None, None, at(12, 0, day="2026-09-18")) == [NIGHTS[0]]


def test_a_mark_stands_on_the_newest_bar_up_to_the_lane_gap_before_it():
    bars = [b for b in flat_bars(60) if b["ts"][11:16] not in ("10:02", "10:03", "10:04")]
    assert replay.close_by_mark(bars, at(10, 4)) is None
    bars = [b for b in flat_bars(60) if b["ts"][11:16] != "10:04"]
    assert replay.close_by_mark(bars, at(10, 4)) == 7700.0


# ---- judging --------------------------------------------------------------------------------------

NOISE_SEED = 0   # the batch from this seed holds one false link on its own p (0.006)
ORDERED_Q = {"options": list(replay.ORDERED["overnight_move_vs_expected"]), "serves": "direction", "replayed_from": "dark"}
SIGN_FREE_Q = {"options": ["built", "faded"], "serves": "direction", "replayed_from": "dark"}


def _nights(qid: str, answers: list[str], moves: list[float], sides: list[int] | None = None) -> tuple[list[dict], dict]:
    reads, outcomes = [], {}
    for k, (a, m) in enumerate(zip(answers, moves)):
        day = (date(2026, 7, 1) + timedelta(days=k)).isoformat()
        reads.append({"day": day, "checkpoint": "09:28", "ruler": 75.0,
                      "questions": {qid: {"answer": a, "fate": "asked", "side": (sides or [1] * len(answers))[k]}}})
        outcomes[day] = {"gap": 0.1, "open_10": m / 2, "open_30": m, "open_60": m, "to_close": m, "range_30": abs(m), "range_60": abs(m)}
    return reads, outcomes


def test_ordered_answers_that_line_up_with_the_move_hold():
    order = replay.ORDERED["overnight_move_vs_expected"]
    answers = [order[k % 5] for k in range(40)]
    moves = [0.1 * order.index(a) + 0.01 * (k % 3) for k, a in enumerate(answers)]
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28", *_nights("overnight_move_vs_expected", answers, moves))
    assert got["verdict"] == "holds" and got["rho"] > 0.9 and got["p"] < replay.P_HOLDS and all(h > 0 for h in got["halves"])
    assert got["p_holm"] == got["p"]                                    # tested alone, Holm leaves p as it is
    assert got["by_answer"]["big_up"]["n"] == 8 and got["by_answer"]["big_up"]["open_30"]["above_zero"] == 1.0


def test_answers_unrelated_to_the_move_are_noise():
    order = replay.ORDERED["overnight_move_vs_expected"]
    rng = random.Random(7)
    answers = [order[k % 5] for k in range(40)]
    moves = [rng.gauss(0, 1) for _ in answers]
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28", *_nights("overnight_move_vs_expected", answers, moves))
    assert got["verdict"] == "noise" and got["graded"] == 40


def test_a_batch_of_questions_with_no_link_names_none_as_holding(monkeypatch):
    """Fourteen pairs of made-up answers against unrelated moves: on its own p one of them would hold, adjusted
    for the fourteen tested together none does."""
    monkeypatch.setattr(replay, "SHUFFLES", 1_000)
    order = replay.ORDERED["overnight_move_vs_expected"]
    judged = []
    for k in range(14):
        rng = random.Random(NOISE_SEED + k)
        answers = [order[m % 5] for m in range(40)]
        moves = [rng.gauss(0, 1) for _ in answers]
        judged.append(replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28", *_nights("overnight_move_vs_expected", answers, moves)))
    assert any(j["verdict"] == "holds" for j in judged)
    adjusted = replay.holm(judged)
    assert not any(j["verdict"] == "holds" for j in adjusted)
    assert all(a["p_holm"] >= j["p"] and f"Holm {a['p_holm']:.3f}" in a["basis"] for a, j in zip(adjusted, judged))


def test_a_thin_sample_is_too_few():
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28",
                       *_nights("overnight_move_vs_expected", ["up", "down"] * 5, [0.1, -0.1] * 5))
    assert got["verdict"] == "too few" and "10 graded nights" in got["basis"]


def test_sign_free_answers_are_judged_along_their_reference():
    """"built" carries on its reference's way whichever way that went: where the reference fell the index fell."""
    answers = ["built", "faded"] * 15
    sides = [1 if k % 4 < 2 else -1 for k in range(30)]
    moves = [(0.2 if a == "built" else -0.2) * s for a, s in zip(answers, sides)]
    got = replay.judge("overnight_arc", SIGN_FREE_Q, "09:28", *_nights("overnight_arc", answers, moves, sides))
    assert got["kind"] == "sign_free" and got["verdict"] == "holds" and got["halves"] == ["built", "built"]
    assert got["reference"] == "the night's first leg that moved"
    assert got["by_answer"]["built"]["open_30"] == {"mean": 0.2, "above_zero": 1.0}
    assert replay.judged_value("sign_free", "range_30", {"range_30": 0.3}, -1) == 0.3
    assert replay.judged_value("sign_free", "open_30", {"open_30": 0.3}, None) is None


def test_a_night_whose_reference_was_not_measured_is_counted_not_graded():
    answers = ["built", "faded"] * 12
    sides = [None, None] + [1] * 22
    got = replay.judge("overnight_arc", SIGN_FREE_Q, "09:28", *_nights("overnight_arc", answers, [0.2] * 24, sides))
    assert got["unfolded"] == 2 and got["graded"] == 22
    assert sum(row["n"] for row in got["by_answer"].values()) == got["graded"]


def test_every_sign_free_premarket_question_has_a_reference():
    doc = replay.premarket_doc()
    sign_free = {qid for g in doc["groups"] for qid, q in g["questions"].items() if replay.kind_of(q, qid) == "sign_free"}
    assert sign_free == set(replay.REFERENCES)


def _figure(value: float, verdict: str | None = None) -> dict:
    return {"kind": "rank", "value": value, "cut": "top third", "verdict": verdict}


def _side(qid: str, figures: dict, scene=None) -> int | None:
    return replay.REFERENCES[qid][1](scene, figures)


def test_the_report_questions_fold_on_the_report_not_on_the_night():
    """Futures up on the night, the report sending them down: the reaction's side is the report's, and the
    night before the report is up whether the report unwound it or carried futures across their 16:00 price."""
    figures = {"overnight.es_move": _figure(0.8, "big_up"), "overnight.release_reaction": _figure(-0.4, "extended"),
               "premarket.release_vs_night": _figure(-0.4, "unwound_night")}
    assert _side("gap_origin", figures) == 1
    assert _side("release_reaction_path", figures) == -1
    assert _side("release_vs_night", figures) == 1
    assert _side("release_vs_night", {"premarket.release_vs_night": _figure(-1.2, "crossed_price")}) == 1
    assert _side("release_vs_night", {"premarket.release_vs_night": _figure(-0.4, "extended_night")}) == -1


def test_the_story_questions_fold_on_the_leg_or_the_hour_they_name():
    up_night = {"premarket.where_now": _figure(0.6, "up")}
    assert _side("overnight_arc", {**up_night, "premarket.arc": _figure(1.4, "built")}) == 1
    assert _side("overnight_arc", {**up_night, "premarket.arc": _figure(-0.5, "reversed")}) == -1
    assert _side("latest_leg_vs_night", {"premarket.since_checkpoint": _figure(0.3, "added")}) == 1
    assert _side("latest_leg_vs_night", {"premarket.since_checkpoint": _figure(-0.3, "gave_back")}) == 1
    assert _side("latest_leg_vs_night", {"premarket.since_checkpoint": _figure(-0.9, "crossed")}) == 1
    assert _side("night_vs_last_hour", {**up_night, "premarket.vs_last_hour": _figure(-0.2, "turned_against")}) == -1
    assert _side("night_legs_agree", {"premarket.legs": _figure(-0.6, "one_way")}) == -1


def test_a_roll_night_folds_on_nothing():
    """The lane refuses the night's move across a roll, so no figure carries it and no side is taken from the
    raw stitched series."""
    for qid in ("gap_origin", "night_legs_agree", "overnight_arc", "latest_leg_vs_night"):
        assert _side(qid, {}) is None


def test_bitcoins_weekend_path_folds_on_bitcoins_weekend_leg(premarket_scene_factory):
    """Bitcoin fell over the weekend while S&P futures rose: the fold is bitcoin's side."""
    day = date(2026, 9, 21)                                     # a Monday
    close, reopen = replay.bitcoin.weekend_edges(day)
    now = at(9, 28, day=day.isoformat())
    night = [night_row("/MBT", close - timedelta(minutes=1), 90000.0, day=day.isoformat()),
             night_row("/MBT", reopen - timedelta(minutes=1), 88000.0, day=day.isoformat()),
             night_row("/MBT", now - timedelta(minutes=2), 88500.0, day=day.isoformat())]
    scene = premarket_scene_factory(now, night)
    figures = {"overnight.es_move": _figure(0.8, "big_up"), "weekend.btc_path": _figure(0.3, "held")}
    assert _side("btc_weekend_path", figures, scene) == -1
    assert _side("btc_weekend_path", {}, scene) is None


def test_ranks_share_ties_and_the_rank_correlation_reads_them():
    assert replay.ranks([3.0, 1.0, 3.0, 2.0]) == [3.5, 1.0, 3.5, 2.0]
    assert replay.spearman([0, 1, 2, 3], [1.0, 5.0, 9.0, 20.0]) == pytest.approx(1.0)
    assert replay.spearman([0, 0, 0], [1.0, 2.0, 3.0]) is None


def test_reasons_count_once_whatever_their_numbers():
    assert replay.pattern("only 9 usable of the last 20 nights, fewer than 10") == "only # usable of the last # nights, fewer than #"
    assert replay.pattern("since 16:00, -0.25% by 2026-09-01 in 08:30-08:45") == "since 16:00, #% by 2026-09-01 in 08:30-08:45"


def test_the_replay_answers_from_the_questions_own_label():
    q = {"labels": ["premarket.where_now", "premarket.arc"], "options": ["reversed", "faded", "built", "held"]}
    figures = {"premarket.where_now": {"verdict": "up"}, "premarket.arc": {"verdict": "built"}}
    assert replay.own_label(q, figures) == "premarket.arc"
    assert replay.own_label(q, {"premarket.where_now": {"verdict": "up"}}) is None


def test_every_premarket_question_is_replayed_as_live():
    doc = replay.premarket_doc()
    qs = [q for g in doc["groups"] for q in g["questions"].values()]
    assert len(qs) == 12 and all(q["status"] == "live" and q["replayed_from"] in ("dark", "live", "shadow") for q in qs)


def test_no_ruler_no_outcome(state):
    for ruler in (None, 0.0, float("nan")):
        assert replay.outcome(state, NIGHTS[0], ruler) == {"omitted": "no pre-open ruler"}


def test_an_ask_with_no_code_answer_is_counted_not_graded():
    reads, outcomes = _nights("overnight_move_vs_expected", ["up"] * 3, [0.1] * 3)
    reads[0]["questions"]["overnight_move_vs_expected"]["answer"] = None
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28", reads, outcomes)
    assert got["unanswered"] == 1 and got["graded"] == 2


def test_a_read_the_lane_could_not_make_is_kept_with_its_reason(state, monkeypatch):
    def no_ruler(state_dir, now):
        raise replay.premarket.NoPreOpenRead("no pre-open ruler: too few anchors")

    monkeypatch.setattr(replay.premarket, "make_premarket_scene", no_ruler)
    reads, oc = replay.replay_day(state, replay.premarket_doc(), NIGHTS[0])
    assert all(r["omitted"] == "no read: no pre-open ruler: too few anchors" for r in reads)
    assert oc == {"day": "2026-09-17", "calendar": None, "omitted": "no pre-open ruler"}


# ---- the lane's own labels ---------------------------------------------------------------------------

LABELS_DAY = date(2026, 9, 24)                                      # a Thursday; its 20 prior nights run back past Labor Day


def _es_night(day: date, pct_at, minutes: int) -> list[dict]:
    """/ES bars of ``minutes`` through the night into ``day`` while the market trades, to 09:30, each at
    6600 moved by ``pct_at(k)`` percent at its k-th bar."""
    slots = overnight.expected_slots("/ES", minutes, overnight.night_window(day)[0], datetime.combine(day, time(9, 30), tzinfo=ET))
    return [night_row("/ES", t, round(6600.0 * (1 + pct_at(k) / 100), 4), minutes=minutes, day=day.isoformat())
            for k, t in enumerate(slots)]


@pytest.fixture
def labels_state(tmp_path, monkeypatch):
    """Twenty prior nights of small seeded /ES random walks and tonight's /ES rising a percent, evenly: a night
    whose net move ranks in the top third. The calendar covers the day with no report before the open."""
    monkeypatch.setattr(events, "on_day", lambda day, path=None: [])
    monkeypatch.setattr(events, "uncovered", lambda day, path=None, tier=None: None)
    days, d = [], LABELS_DAY
    while len(days) < 20:
        d = previous_trading_day(d)
        days.append(d)
    for seed, day in enumerate(days):
        rng, walk = random.Random(seed), [0.0]
        overnight.write_night(tmp_path, day.isoformat(), _es_night(day, lambda k: walk.append(walk[-1] + rng.gauss(0, 0.02)) or walk[-1], 5))
    tonight = _es_night(LABELS_DAY, lambda k: k / 1000, 1)
    overnight.write_night(tmp_path, LABELS_DAY.isoformat(), tonight)
    return tmp_path


def test_a_read_through_the_lanes_own_labels_gives_each_question_its_fate_and_code_answer(labels_state, monkeypatch,
                                                                                         premarket_scene_factory):
    """The scene in premarket.make_premarket_scene's shape, the labels the registry's families build: every
    premarket question comes out with a fate, a reason where it is not asked, and an answer among its options
    where it is; a sign-free one answered carries the side of its reference."""
    def scene(state_dir, now):
        return premarket_scene_factory(now, load_jsonl(overnight.night_path(state_dir, now.date().isoformat())),
                                       prior_close=7500.0, sigma=75.0, state_dir=state_dir)

    monkeypatch.setattr(replay.premarket, "make_premarket_scene", scene)
    doc = replay.premarket_doc()
    questions = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    got = replay.read(labels_state, doc, LABELS_DAY, "09:28")

    assert set(got["questions"]) == set(questions)
    for qid, fate in got["questions"].items():
        assert fate["fate"] in ("asked", "asleep", "missing", "not_due")
        if fate["fate"] in ("asleep", "missing"):
            assert fate["why"], qid
        if fate["answer"] is not None:
            assert fate["answer"] in questions[qid]["options"], qid
    assert got["questions"]["overnight_move_vs_expected"] == {"answer": "big_up", "fate": "asked"}
    assert got["questions"]["pm_overnight_session"] == {"answer": "wide_night", "fate": "asked"}
    for qid, answer in {"gap_origin": "made_early", "overnight_arc": "built", "night_legs_agree": "one_way",
                        "latest_leg_vs_night": "added"}.items():
        assert got["questions"][qid] == {"answer": answer, "fate": "asked", "side": 1}, qid
    assert got["questions"]["release_reaction_path"]["fate"] == "asleep"
    assert got["figures"]["premarket.where_now"]["verdict"] == "up"
