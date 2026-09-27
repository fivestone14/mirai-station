"""The premarket replay (spec/replay_premarket.py), offline: two synthetic nights replayed end to end without
writing under the state dir, the outcomes from the settled open in the pre-open ruler, and the judging on
made-up answers: a real link holds, an unrelated one is noise, a thin sample is too few."""
from __future__ import annotations

import importlib.util
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from conftest import at, flat_bars, night_row
from spx_jev import overnight
from spx_jev.labels.label_set import LabelSet
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
    monkeypatch.setattr(replay.premarket, "checkpoints", lambda day: replay.PREMARKET.schedule, raising=False)  # W8-INTERIM until W1 lands


def _files(folder: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime_ns for p in folder.rglob("*")}


def test_two_nights_replay_end_to_end_without_writing_under_the_state_dir(state, stubbed, tmp_path):
    before = _files(state)
    out = tmp_path / "replay"
    assert replay.main(["--state-dir", str(state), "--out", str(out), "--report", str(tmp_path / "report.md")]) == 0
    assert _files(state) == before

    reads = [json.loads(line) for line in (out / "reads.jsonl").read_text(encoding="utf-8").splitlines()]
    checkpoints = {d: replay.premarket.checkpoints(d) for d in NIGHTS}
    assert [(r["day"], r["checkpoint"]) for r in reads] == [(d.isoformat(), c) for d in NIGHTS for c in checkpoints[d]]
    last = {r["day"]: r for r in reads if r["checkpoint"] == "09:28"}
    for day, r in last.items():
        assert r["ruler"] == 75.0 and r["side"] == 1
        assert r["questions"]["overnight_move_vs_expected"] == {"answer": VERDICT[day], "fate": "asked"}
        assert r["questions"]["gap_origin"]["fate"] == "asleep"
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

ORDERED_Q = {"options": list(replay.ORDERED["overnight_move_vs_expected"]), "serves": "direction", "replayed_from": "dark"}
SIGN_FREE_Q = {"options": ["built", "faded"], "serves": "direction", "replayed_from": "dark"}


def _nights(qid: str, answers: list[str], moves: list[float], sides: list[int] | None = None) -> tuple[list[dict], dict]:
    reads, outcomes = [], {}
    for k, (a, m) in enumerate(zip(answers, moves)):
        day = (date(2026, 7, 1) + timedelta(days=k)).isoformat()
        reads.append({"day": day, "checkpoint": "09:28", "ruler": 75.0, "side": (sides or [1] * len(answers))[k],
                      "questions": {qid: {"answer": a, "fate": "asked"}}})
        outcomes[day] = {"gap": 0.1, "open_10": m / 2, "open_30": m, "open_60": m, "to_close": m, "range_30": abs(m), "range_60": abs(m)}
    return reads, outcomes


def test_ordered_answers_that_line_up_with_the_move_hold():
    order = replay.ORDERED["overnight_move_vs_expected"]
    answers = [order[k % 5] for k in range(40)]
    moves = [0.1 * order.index(a) + 0.01 * (k % 3) for k, a in enumerate(answers)]
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28", *_nights("overnight_move_vs_expected", answers, moves))
    assert got["verdict"] == "holds" and got["rho"] > 0.9 and got["p"] < replay.P_HOLDS and all(h > 0 for h in got["halves"])
    assert got["by_answer"]["big_up"]["n"] == 8 and got["by_answer"]["big_up"]["open_30"]["above_zero"] == 1.0


def test_answers_unrelated_to_the_move_are_noise():
    order = replay.ORDERED["overnight_move_vs_expected"]
    rng = random.Random(7)
    answers = [order[k % 5] for k in range(40)]
    moves = [rng.gauss(0, 1) for _ in answers]
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28", *_nights("overnight_move_vs_expected", answers, moves))
    assert got["verdict"] == "noise" and got["graded"] == 40


def test_a_thin_sample_is_too_few():
    got = replay.judge("overnight_move_vs_expected", ORDERED_Q, "09:28",
                       *_nights("overnight_move_vs_expected", ["up", "down"] * 5, [0.1, -0.1] * 5))
    assert got["verdict"] == "too few" and "10 graded nights" in got["basis"]


def test_sign_free_answers_are_judged_the_nights_way():
    """"built" carries on the night's way whichever way the night went: on a down night the index fell."""
    answers = ["built", "faded"] * 15
    sides = [1 if k % 4 < 2 else -1 for k in range(30)]
    moves = [(0.2 if a == "built" else -0.2) * s for a, s in zip(answers, sides)]
    got = replay.judge("gap_origin", SIGN_FREE_Q, "09:28", *_nights("gap_origin", answers, moves, sides))
    assert got["kind"] == "with_night" and got["verdict"] == "holds" and got["halves"] == ["built", "built"]
    assert got["by_answer"]["built"]["open_30"] == {"mean": 0.2, "above_zero": 1.0}
    assert replay.judged_value("with_night", "range_30", {"range_30": 0.3}, -1) == 0.3
    assert replay.judged_value("with_night", "open_30", {"open_30": 0.3}, None) is None


def test_ranks_share_ties_and_the_rank_correlation_reads_them():
    assert replay.ranks([3.0, 1.0, 3.0, 2.0]) == [3.5, 1.0, 3.5, 2.0]
    assert replay.spearman([0, 1, 2, 3], [1.0, 5.0, 9.0, 20.0]) == pytest.approx(1.0)
    assert replay.spearman([0, 0, 0], [1.0, 2.0, 3.0]) is None


def test_reasons_count_once_whatever_their_numbers():
    assert replay.pattern("only 9 usable of the last 20 nights, fewer than 10") == "only # usable of the last # nights, fewer than #"


def test_the_replay_answers_from_the_questions_own_label():
    q = {"labels": ["premarket.where_now", "premarket.arc"], "options": ["reversed", "faded", "built", "held"]}
    figures = {"premarket.where_now": {"verdict": "up"}, "premarket.arc": {"verdict": "built"}}
    assert replay.own_label(q, figures) == "premarket.arc"
    assert replay.own_label(q, {"premarket.where_now": {"verdict": "up"}}) is None


def test_every_premarket_question_is_replayed_as_live():
    doc = replay.premarket_doc()
    qs = [q for g in doc["groups"] for q in g["questions"].values()]
    assert len(qs) == 12 and all(q["status"] == "live" and q["replayed_from"] in ("dark", "live", "shadow") for q in qs)
