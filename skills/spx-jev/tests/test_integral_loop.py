"""The learning loop's switch to the average-price grade, on for the live lane: on, the loop learns from the
average-price label alone, into files of its own, the end-price loop learning beside it exactly as it does off; flipped
back off, the grader and the loop are exactly what they were and no file of the switched loop is made or read; and no
flip of the switch ever mixes the two histories. The dry run builds it from the graded history without writing
anything live."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import at
from spx_jev import grade, integral, integral_loop, lane, pool
from spx_jev.scores import floored
from spx_jev.clock import INTEGRAL_CACHE_NAME, MIN_SESSIONS, PHASES, _integral_rule_key
from spx_jev.grade import INTEGRAL_NAME, weights_from
from spx_jev.lane import LANES, LIVE

ON, OFF = LIVE, replace(LIVE, integral_loop=False)
ALLOWED = {"q_a": {"yes", "no"}}
MEMBERS = {"q_a": "v1"}
DAYS = ("2026-09-14", "2026-09-15", "2026-09-16")
TODAY = "2026-09-17"
END_PRICE_FILES = ("pool_30.json", "pool_60.json", pool.LOG_NAME)
INTEGRAL_FILES = ("pool_30_integral.json", integral_loop.LOG_NAME)
# the average-price odds as the phone showed them, JEV's own and the time-of-day odds on the average price they blend
JEV_AVG = {"up": 0.6, "flat": 0.3, "down": 0.1}
CLOCK_AVG = {"up": 0.2, "flat": 0.6, "down": 0.2}
SHOWN_AVG = {k: 0.5 * JEV_AVG[k] + 0.5 * CLOCK_AVG[k] for k in JEV_AVG}
# per session, per read: (the average-price label, the end-price band at 30 minutes, q_a's chance of "yes")
SESSIONS = {"2026-09-14": [("up", "flat", 0.9), ("up", "down", 0.8), ("flat", "flat", 0.3)],
            "2026-09-15": [("down", "up", 0.2), ("flat", "up", 0.4), ("up", "up", 0.9)],
            "2026-09-16": [("up", "down", 0.7), ("down", "down", 0.1), ("flat", "up", 0.5)]}


class FakeBaseline:
    version = "v1:test"

    def clock(self, h, now):
        return {"up": 0.25, "flat": 0.5, "down": 0.25}

    def whole_day(self, h):
        return {"up": 0.25, "flat": 0.5, "down": 0.25}


def _frozen(day: str):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            t = datetime.fromisoformat(f"{day}T12:00:00-04:00")
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    return Frozen


@pytest.fixture
def clock(monkeypatch):
    """Every clock the grader and both loops stamp with, stopped at noon on ``day``, so two runs log alike."""
    def set_day(day: str = TODAY):
        for module in (grade, pool, integral_loop):
            monkeypatch.setattr(module, "datetime", _frozen(day))
    set_day()
    return set_day


def _counts(up: int, flat: int, down: int) -> dict:
    return {p[0]: {"up": up, "flat": flat, "down": down} for p in PHASES}


def _cache(out: Path, days: dict[str, dict]) -> None:
    """The average-price clock's stored counts, under its own rule, as clock.integral_odds leaves them."""
    (out / INTEGRAL_CACHE_NAME).write_text(json.dumps({"rule": _integral_rule_key(LIVE), "days": {d: {"counts": c} for d, c in days.items()}}))


def _prior_counts(n: int = 12) -> dict[str, dict]:
    return {f"2026-08-{10 + k:02d}": _counts(1, 2, 1) for k in range(n)}


def _write(out: Path, sessions: dict = SESSIONS, stale_at=None, event_at=None, average=True, integral_lines=True, blended=True) -> Path:
    """Three live reads a session at 10:02, 10:32 and 11:02: each sum record with its end-price sums, the end-price loop's
    snapshots (whose answers the switched loop reads) and the average-price call, its grades.jsonl line and its
    average-price line, and the clock's stored counts on the average price for twelve sessions before them."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "hour").mkdir(exist_ok=True)
    _cache(out, _prior_counts())
    for day, reads in sessions.items():
        recs, grades, lines = [], [], []
        for k, (label, band, yes) in enumerate(reads):
            t = at(10, 2, day=day) + timedelta(minutes=30 * k)
            answers = {"q_a": {"yes": yes, "no": round(1 - yes, 4)}}
            rec = {"row_ts": t.isoformat(), "spot": 7700.0, "sigma": 75.0, "event": {"within_30": True} if event_at == (day, k) else None,
                   "by": {h: {"pick": "up", "probabilities": {"up": 0.4, "flat": 0.4, "down": 0.1, "unsure": 0.1}} for h in ("next_30", "next_60")},
                   "fresh": {"q_a": "yes"},
                   "pool": {h: pool.snapshot(pool.cold_state(), FakeBaseline(), h, t, {"up": 0.5, "flat": 0.3, "down": 0.2},
                                             {"up": 0.2, "flat": 0.6, "down": 0.2}, {"up": 0.35, "flat": 0.45, "down": 0.2}, answers,
                                             MEMBERS, {"q_a"}) for h in ("next_30", "next_60")}}
            if average and blended:
                rec["average"] = {"primary": "average_30", "box": "next_30", "pick": max(SHOWN_AVG, key=SHOWN_AVG.get), "probabilities": SHOWN_AVG,
                                  "blend": {"used": True}, "jev": {"pick": "up", "probabilities": JEV_AVG},
                                  "clock": {"pick": "flat", "probabilities": CLOCK_AVG}, "edge_points": 3.11}
            elif average:
                rec["average"] = {"primary": "average_30", "box": "next_30", "pick": "up", "probabilities": JEV_AVG,
                                  "blend": {"used": False, "why": "only 9 prior sessions"}, "edge_points": 3.11}
            recs.append(rec)
            grades.append({"row_ts": t.isoformat(), "horizons": ["next_30", "next_60"], "pending": [], "skipped": {}, "fresh": {"q_a": "yes"},
                           "band": band, "next_30": {"band": band, "pick": "up", "hit": band == "up", "brier": 0.5},
                           "next_60": {"band": band, "pick": "up", "hit": band == "up", "brier": 0.5}})
            lines.append({"row_ts": t.isoformat(), "horizon": "next_30", "rule_version": integral.RULE_VERSION,
                          "sum": "average_30" if average else "next_30", "graded": True, "label": label, "stale_read": stale_at == (day, k)})
            lines.append({"row_ts": t.isoformat(), "horizon": "next_60", "rule_version": integral.RULE_VERSION, "sum": "next_60",
                          "graded": True, "label": "down", "stale_read": False})
        (out / "hour" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        with open(out / "grades.jsonl", "a") as f:
            f.write("".join(json.dumps(g) + "\n" for g in grades))
        if integral_lines:
            with open(out / INTEGRAL_NAME, "a") as f:
                f.write("".join(json.dumps(g) + "\n" for g in lines))
    return out


def _edit(path: Path, change) -> None:
    """Rewrite a jsonl file with ``change`` applied to each line."""
    lines = [change(json.loads(line)) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _learn(out: Path, lane_=ON) -> dict:
    return weights_from(grade.load_jsonl(out / "grades.jsonl"), ALLOWED, lane_, out)


def _log(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / integral_loop.LOG_NAME).read_text().splitlines()]


# ---- the switch

def test_the_switch_is_on_for_the_live_lane_alone():
    """The live lane, the one lane that learns the loop, learns it from the average-price grade too; the other lanes
    keep no loop to switch. Its results are trusted, and the pool promoted, only after the gate (README 6b)."""
    assert [name for name, x in LANES.items() if x.integral_loop] == ["live"] and LIVE.integral_loop is True
    assert LIVE.pool and not any(x.pool for name, x in LANES.items() if name != "live")


# ---- flipped back off

def test_with_the_switch_off_no_file_of_the_switched_loop_is_made_or_read(tmp_path, clock, monkeypatch):
    """The grader learns the end-price loop exactly as before: the same weights and files with a switched loop's state
    lying beside them or not, and nothing of the switched loop is called."""
    plain, beside = _write(tmp_path / "plain"), _write(tmp_path / "beside")
    (beside / "pool_30_integral.json").write_text("{not a state")
    for name in ("update", "load_state", "dry_run"):
        monkeypatch.setattr(integral_loop, name, lambda *a, **k: pytest.fail("the switched loop was called with the switch off"))
    w_plain, w_beside = _learn(plain, OFF), _learn(beside, OFF)
    assert w_plain == w_beside and w_plain["method"] == "pool_v1" and w_plain["pool"]["last_session_applied"] == DAYS[-1]
    assert (beside / "pool_30_integral.json").read_text() == "{not a state"
    assert not any((plain / name).exists() for name in INTEGRAL_FILES) and not (beside / integral_loop.LOG_NAME).exists()
    after_plain, after_beside = _tree(plain), _tree(beside)
    del after_beside["pool_30_integral.json"]
    assert after_plain == after_beside


def test_with_the_switch_off_the_grader_never_imports_the_switched_loop(tmp_path):
    """In a fresh interpreter, the service, the clock and a grading run's learning leave the module unloaded."""
    code = ("import sys; from dataclasses import replace; from pathlib import Path; from spx_jev import clock, grade, service; "
            "from spx_jev.lane import LIVE; "
            f"grade.weights_from([], {{}}, replace(LIVE, integral_loop=False), Path({str(tmp_path)!r})); "
            "print('spx_jev.integral_loop' in sys.modules)")
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False" and (tmp_path / "pool_30.json").exists()


# ---- on

def test_on_the_loop_learns_from_the_average_price_label_beside_the_end_price_loop_learning_as_it_does_off(tmp_path, clock):
    """Switched on, the end-price loop, whose pool the phone shows and demotes, keeps learning to the byte what it learns
    switched off; the switched loop writes only its own two files beside it."""
    out, off = _write(tmp_path / "out"), _write(tmp_path / "off")
    before = _tree(out)
    w = _learn(out, ON)
    _learn(off, OFF)
    after = _tree(out)
    assert set(after) - set(before) == set(INTEGRAL_FILES) | set(END_PRICE_FILES) and all(after[k] == v for k, v in before.items())
    assert all(after[name] == (off / name).read_bytes() for name in END_PRICE_FILES)
    assert set(after) - set(_tree(off)) == set(INTEGRAL_FILES)                  # the switched loop's own two files, and no other
    assert json.loads(after["pool_30.json"])["last_session_applied"] == DAYS[-1]
    # the weights keep the end-price loop's report, as with the switch off, and the switched loop's is beside it
    assert {k: v for k, v in w.items() if k != "pool_integral"} == _learn(_write(tmp_path / "off2"), OFF)
    w = w["pool_integral"]
    assert w["method"] == "pool_v1_integral" and w["pool"]["last_session_applied"] == DAYS[-1] and w["pool"]["phone"]["on_phone"] is False
    assert w["questions"]["q_a"]["days"] == 3 and w["questions"]["q_a"]["weight"] == 1.0
    state = json.loads((out / "pool_30_integral.json").read_text())
    assert state["constants_hash"] == integral_loop.CONSTANTS_HASH != pool.CONSTANTS_HASH
    assert state["reference_version"].startswith("integral_clock:")
    applied = [x for x in _log(out) if x["applied"]]
    assert [x["session"] for x in applied] == list(DAYS) and all(len(x["manifest"]["included"]) == 3 for x in applied)


def test_on_the_end_price_bands_teach_it_nothing_and_the_average_price_labels_everything(tmp_path, clock):
    """The same reads with every end-price band turned round learn the same state to the byte; with the average-price
    labels turned round, another one."""
    turn = {"up": "down", "down": "up", "flat": "up"}
    base = _write(tmp_path / "base")
    other_end = _write(tmp_path / "end", {d: [(lab, turn[band], yes) for lab, band, yes in reads] for d, reads in SESSIONS.items()})
    other_avg = _write(tmp_path / "avg", {d: [(turn[lab], band, yes) for lab, band, yes in reads] for d, reads in SESSIONS.items()})
    for out in (base, other_end, other_avg):
        integral_loop.update(out, TODAY)
    state = (base / "pool_30_integral.json").read_bytes()
    assert (other_end / "pool_30_integral.json").read_bytes() == state
    assert (other_avg / "pool_30_integral.json").read_bytes() != state


def test_on_a_stale_read_and_an_event_read_are_left_out_as_the_loop_leaves_them_out(tmp_path, clock):
    out = _write(tmp_path, stale_at=(DAYS[0], 1), event_at=(DAYS[1], 0))
    integral_loop.update(out, TODAY)
    excluded = {x["session"]: x["manifest"]["excluded"] for x in _log(out) if x.get("applied")}
    assert list(excluded[DAYS[0]].values()) == ["a stale read: no bar traded its spot in the minutes up to it"]
    assert list(excluded[DAYS[1]].values()) == ["a scheduled event inside the 30-minute window"]
    assert excluded[DAYS[2]] == {}


def test_on_its_reference_is_the_average_price_clock_of_the_sessions_before_each_one(tmp_path, clock):
    """A session's forecasts are formed from the stored counts of the sessions before it, never of its own day or
    later."""
    out = _write(tmp_path / "out")
    ref = integral_loop.reference(json.loads((out / INTEGRAL_CACHE_NAME).read_text())["days"], DAYS[0], "v")
    assert ref.clock("next_30", at(10, 2, day=DAYS[0])) == pytest.approx({"up": 0.25, "flat": 0.5, "down": 0.25})
    later = _write(tmp_path / "later")
    _cache(later, {**_prior_counts(), DAYS[0]: _counts(9, 0, 0), DAYS[1]: _counts(9, 0, 0)})
    integral_loop.update(out, "2026-09-15")
    integral_loop.update(later, "2026-09-15")
    assert (later / "pool_30_integral.json").read_bytes() == (out / "pool_30_integral.json").read_bytes()


def test_on_blended_reads_with_no_reference_on_file_stop_the_update_until_the_counts_are_back(tmp_path, clock):
    """Counts recounted under a new rule, and not yet for the days before a session, must not pass that session over
    for good: the update stops where it is, and applies it once the counts are back. An unblended read had no odds
    to stand on, so it is left out and said so."""
    out = _write(tmp_path / "out")
    for counts in ({}, _prior_counts(MIN_SESSIONS - 1)):
        _cache(out, counts)
        assert integral_loop.update(out, TODAY) == {"next_30": f"{DAYS[0]}: no reference on file; stopped"}
        assert json.loads((out / "pool_30_integral.json").read_text())["last_session_applied"] is None
    assert _log(out)[-1] == {**_log(out)[-1], "applied": False, "session": DAYS[0],
                             "why": "no reference on file for reads blended with one: stopped, fail closed"}
    _cache(out, _prior_counts())
    assert integral_loop.update(out, TODAY) == {"next_30": f"{DAYS[-1]}: applied, 3 reads"}
    unblended = _write(tmp_path / "unblended", blended=False)
    _cache(unblended, {})
    integral_loop.update(unblended, TODAY)
    reasons = {why for x in _log(unblended) for why in (x.get("manifest") or {}).get("excluded", {}).values()}
    assert reasons == {f"no snapshot: fewer than {MIN_SESSIONS} sessions of time-of-day odds on the average price before this one"}
    assert json.loads((unblended / "pool_30_integral.json").read_text())["last_session_applied"] == DAYS[-1]


def test_on_each_questions_count_is_over_the_reads_this_loop_learned_from_not_the_end_price_loops(tmp_path, clock):
    """A stale read teaches the end-price loop and not this one, and a session still waiting on its average-price lines
    teaches this one nothing yet: the counts say so, each loop's over its own reads."""
    w = _learn(_write(tmp_path / "stale", stale_at=(DAYS[0], 0)))
    assert w["questions"]["q_a"]["n"] == 9 and w["pool_integral"]["questions"]["q_a"]["n"] == 8
    w = _learn(_write(tmp_path / "waiting", integral_lines=False))
    assert w["questions"]["q_a"]["n"] == 9 and w["pool_integral"]["questions"]["q_a"]["n"] == 0
    assert w["pool_integral"]["pool"]["last_session_applied"] is None


def _alone(out: Path, day: str, k: int, answers: bool = True) -> str:
    """Read ``k`` of ``day`` as it is when only the end-price request failed: no end-price sums and no grades.jsonl
    line, its end-price snapshot left out with, or without, the questions' answers, its average-price call graded."""
    ts = (at(10, 2, day=day) + timedelta(minutes=30 * k)).isoformat()

    def strip(r):
        if r["row_ts"] != ts:
            return r
        del r["by"]
        left = {"left_out": "JEV gave no probabilities for next_30"}
        r["pool"] = {"next_30": {**left, **pool.answered({"q_a": {"yes": 0.5, "no": 0.5}}, MEMBERS, {"q_a"})} if answers else left}
        return {**r, "error": "JEV returned HTTP 529 for group hour"}
    _edit(out / "hour" / f"{day}.jsonl", strip)
    lines = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in lines if g["row_ts"] != ts))
    return ts


def test_on_a_call_whose_end_price_sums_got_no_answer_is_learned_once_its_own_line_is_on_file(tmp_path, clock):
    """The call the phone showed is learned from like any other: its session waits for its average-price line, and it
    is then included on the answers its snapshot kept; without them it is left out, saying why."""
    out = _write(tmp_path / "out", {DAYS[0]: SESSIONS[DAYS[0]]})
    ts = _alone(out, DAYS[0], 1)
    held = [json.loads(line) for line in (out / INTEGRAL_NAME).read_text().splitlines()]
    (out / INTEGRAL_NAME).write_text("".join(json.dumps(g) + "\n" for g in held if g["row_ts"] != ts))
    assert integral_loop.update(out, TODAY) == {"next_30": f"{DAYS[0]}: unsealed; stopped"}
    with open(out / INTEGRAL_NAME, "a") as f:
        f.write("".join(json.dumps(g) + "\n" for g in held if g["row_ts"] == ts))
    assert integral_loop.update(out, TODAY) == {"next_30": f"{DAYS[0]}: applied, 3 reads"}
    assert ts in _log(out)[-1]["manifest"]["included"]
    bare = _write(tmp_path / "bare", {DAYS[0]: SESSIONS[DAYS[0]]})
    ts = _alone(bare, DAYS[0], 1, answers=False)
    assert integral_loop.update(bare, TODAY) == {"next_30": f"{DAYS[0]}: applied, 2 reads"}
    assert _log(bare)[-1]["manifest"]["excluded"] == {ts: "no answers on file: the read's end-price snapshot was left out"}
    assert pool.update(bare, TODAY)["next_30"] == f"{DAYS[0]}: applied, 2 reads"    # the end-price loop never had it


def test_on_a_session_before_the_question_is_passed_over_and_an_ungraded_read_stops_the_update(tmp_path, clock):
    out = _write(tmp_path, {DAYS[0]: SESSIONS[DAYS[0]]}, average=False)
    _write(tmp_path, {DAYS[1]: SESSIONS[DAYS[1]]}, integral_lines=False)
    assert integral_loop.update(out, TODAY) == {"next_30": f"{DAYS[1]}: unsealed; stopped"}
    assert [x["why"] for x in _log(out)] == ["no read carries an average-price call: before the question",
                                            "unsealed: a read still has its average-price grade to come"]
    assert json.loads((out / "pool_30_integral.json").read_text())["last_session_applied"] == DAYS[0]


def test_neither_loop_ever_loads_the_others_state(tmp_path, clock):
    """An end-price state where the switched loop keeps its own stops the switched loop, and the other way round,
    before anything is applied: the constants each is keyed by tell them apart."""
    out = _write(tmp_path)
    pool.update(out, today=DAYS[1])
    shutil.copy(out / "pool_30.json", out / "pool_30_integral.json")
    planted = (out / "pool_30_integral.json").read_bytes()
    assert integral_loop.update(out, TODAY) == {"next_30": "constants changed; stopped"}
    assert (out / "pool_30_integral.json").read_bytes() == planted and _log(out)[-1]["applied"] is False
    other = _write(tmp_path / "other")
    integral_loop.update(other, DAYS[1])
    for name in ("pool_30.json", "pool_60.json"):
        shutil.copy(other / "pool_30_integral.json", other / name)
    assert pool.update(other, today=TODAY)["next_30"] == "constants changed; stopped"


def test_flipping_the_switch_on_off_and_on_again_never_mixes_the_histories(tmp_path, clock):
    """Each loop picks up at its own watermark: after ON, OFF, ON the switched loop's state is the one it reaches kept
    on from the start, and the end-price loop, learning whichever way the switch stands, the one it reaches kept off,
    to the byte."""
    flipped, always_on, always_off = (_write(tmp_path / name) for name in ("flipped", "on", "off"))
    clock(DAYS[1])
    _learn(flipped, ON)
    clock(DAYS[2])
    _learn(flipped, OFF)
    clock(TODAY)
    _learn(flipped, ON)
    for day in (DAYS[1], DAYS[2], TODAY):
        clock(day)
        _learn(always_on, ON)
        _learn(always_off, OFF)
    assert (flipped / "pool_30_integral.json").read_bytes() == (always_on / "pool_30_integral.json").read_bytes()
    assert json.loads((flipped / "pool_30_integral.json").read_text())["last_session_applied"] == DAYS[2]
    for name in END_PRICE_FILES:
        assert (flipped / name).read_bytes() == (always_off / name).read_bytes()
    assert json.loads((flipped / "pool_30.json").read_text())["last_session_applied"] == DAYS[2]


def test_on_a_change_to_the_reference_odds_constants_starts_the_calibration_again(tmp_path, clock, monkeypatch):
    """The odds' own numbers are part of the reference's version: a new shrink is a new reference, logged, and the
    calibration after it is the one learned from that session alone."""
    out = _write(tmp_path / "out")
    integral_loop.update(out, DAYS[2])
    monkeypatch.setattr("spx_jev.clock.SHRINK", 5.0)
    integral_loop.update(out, TODAY)
    last = _log(out)[-1]
    assert last["session"] == DAYS[2] and last["reference_changed"]["from"] != last["reference_changed"]["to"]
    alone = _write(tmp_path / "alone", {DAYS[2]: SESSIONS[DAYS[2]]})
    integral_loop.update(alone, TODAY)
    assert json.loads((out / "pool_30_integral.json").read_text())["cal"] == json.loads((alone / "pool_30_integral.json").read_text())["cal"]


def test_on_a_half_day_teaches_it_nothing(tmp_path, clock):
    out = _write(tmp_path, {"2026-11-27": SESSIONS[DAYS[0]]})
    integral_loop.update(out, "2026-11-30")
    (line,) = _log(out)
    assert line["manifest"]["included"] == [] and set(line["manifest"]["excluded"].values()) == {"a half day"}


def test_on_only_the_current_rule_versions_label_of_the_box_is_learned(tmp_path, clock):
    """An older rule's line, or the 60-minute box's, filed first for the same read teaches nothing: the state is the
    same to the byte as with neither."""
    turn = {"up": "down", "down": "up", "flat": "up"}
    base, noisy = _write(tmp_path / "base"), _write(tmp_path / "noisy")
    lines = [json.loads(line) for line in (noisy / INTEGRAL_NAME).read_text().splitlines()]
    older = [{**g, "rule_version": integral.RULE_VERSION - 1, "label": turn[g["label"]]} for g in lines if g["horizon"] == "next_30"]
    other_box = [{**g, "label": "up" if g["label"] == "down" else "down"} for g in lines if g["horizon"] == "next_60"]
    (noisy / INTEGRAL_NAME).write_text("".join(json.dumps(g) + "\n" for g in older + other_box + lines))
    for out in (base, noisy):
        integral_loop.update(out, TODAY)
    assert (noisy / "pool_30_integral.json").read_bytes() == (base / "pool_30_integral.json").read_bytes()


def test_on_a_read_is_forecast_on_jevs_own_average_odds_the_shown_blend_and_its_fresh_answers(tmp_path):
    """JEV's own odds, not the blend the phone showed, are the JEV side of the mixes; the blend is blend50 as shown;
    the questions' answers and which were fresh are the read's own."""
    out = _write(tmp_path)
    recs = grade.load_jsonl(out / "hour" / f"{DAYS[0]}.jsonl")
    ref = integral_loop.reference(json.loads((out / INTEGRAL_CACHE_NAME).read_text())["days"], DAYS[0], "v")
    reads, excluded = integral_loop._session_reads(recs, integral_loop.average_lines(out), integral_loop.cold_state(), ref, LIVE)
    snap = reads[0]["snapshot"]
    assert excluded == {} and [r["outcome"] for r in reads] == [lab for lab, _, _ in SESSIONS[DAYS[0]]]
    assert snap["experts"]["jev_share_1.0"] == pytest.approx(floored(JEV_AVG), abs=1e-4)
    assert snap["experts"]["jev_share_1.0"] != pytest.approx(floored(SHOWN_AVG), abs=1e-3)
    assert snap["blend50_exact"] == pytest.approx(SHOWN_AVG) and snap["raw_clock"] == pytest.approx({"up": 0.25, "flat": 0.5, "down": 0.25})
    assert snap["fresh"] == snap["awake"] == ["q_a"] and snap["q_probs"] == {"q_a": {"yes": 0.9, "no": 0.1}}
    # its reference is the live time-of-day odds, and its experts and its state say so by name
    assert {"clock", "clock_cal"} <= set(snap["experts"]) and "baseline" not in snap["experts"] and snap["reference_version"] == "v"
    assert set(integral_loop.cold_state()["top"]["M"]) == set(snap["experts"])


def test_on_the_calibration_counts_what_happened_and_forgets_it_by_the_decay(tmp_path, clock):
    """Worked by hand: three reads a session on windows that never overlap, each counting a third of its day; the
    reference expected up a quarter of the time; each session's count is kept at 0.95 of itself the next."""
    out = _write(tmp_path)
    integral_loop.update(out, DAYS[1])
    cal = json.loads((out / "pool_30_integral.json").read_text())["cal"]
    assert cal["O"] == pytest.approx({"up": 2 / 3, "flat": 1 / 3, "down": 0.0}) and cal["E"]["up"] == pytest.approx(0.25)
    integral_loop.update(out, TODAY)
    cal = json.loads((out / "pool_30_integral.json").read_text())["cal"]
    up = (0.95 * 2 / 3 + 1 / 3) * 0.95 + 1 / 3           # 14th: up, up, flat; 15th: down, flat, up; 16th: up, down, flat
    assert cal["O"]["up"] == pytest.approx(up, abs=1e-9) and cal["E"]["up"] == pytest.approx((0.95 * 0.25 + 0.25) * 0.95 + 0.25, abs=1e-9)


def test_on_the_reference_takes_the_newest_twenty_sessions_and_counts_none_that_is_thin():
    """As clock.integral_odds counts them: the newest twenty sessions before the day, a thin one (under
    MIN_SCORED_READS reads) holding its place without being counted, and none older reached for in its stead."""
    even, skewed, thin = _counts(1, 2, 1), _counts(9, 0, 0), _counts(3, 0, 0)          # 20, 45 and 15 reads a session

    def ref(days):
        return integral_loop.reference({d: {"counts": c} for d, c in days.items()}, "2026-09-14", "v")
    older, newest = {f"2026-07-{k:02d}": skewed for k in range(1, 6)}, {f"2026-08-{k:02d}": even for k in range(1, 21)}
    for days, n in (({**older, **newest}, 20), ({**older, **newest, "2026-08-31": thin}, 19)):
        assert len(ref(days).days) == n and ref(days).whole_day("next_30") == pytest.approx({"up": 0.25, "flat": 0.5, "down": 0.25})
    nine = {f"2026-08-{k:02d}": even for k in range(1, 10)}
    assert ref({**nine, **{f"2026-08-{k:02d}": thin for k in range(10, 14)}}) is None
    assert ref({**nine, "2026-08-10": _counts(1, 1, 2)}) is not None                    # 20 reads: counted


def test_on_a_session_with_one_read_before_the_question_is_learned_from_its_other_reads(tmp_path, clock):
    out = _write(tmp_path)
    _edit(out / "hour" / f"{DAYS[0]}.jsonl", lambda r: {k: v for k, v in r.items() if k != "average" or r["row_ts"] != at(10, 2, day=DAYS[0]).isoformat()})
    integral_loop.update(out, DAYS[1])
    (line,) = _log(out)
    assert line["applied"] and line["manifest"]["excluded"] == {at(10, 2, day=DAYS[0]).isoformat(): "no average-price call on this read"}


# ---- the dry run

def test_the_dry_run_writes_nothing_live_and_learns_what_switching_on_would(tmp_path, clock, capsys):
    out = _write(tmp_path / "out", stale_at=(DAYS[0], 1))
    before = _tree(out)
    run = integral_loop.dry_run(out, LIVE, TODAY)
    assert _tree(out) == before
    on = _write(tmp_path / "on", stale_at=(DAYS[0], 1))
    integral_loop.update(on, TODAY)
    assert pool._canonical(run["state"]) == (on / "pool_30_integral.json").read_text()
    assert grade.main(["--state-dir", str(tmp_path), "--out-dir", str(out), "--integral-loop-dry-run"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert _tree(out) == before
    assert printed[0].endswith(f"{DAYS[-1]}: applied, 3 reads")
    assert printed[1].startswith(f"sessions learned from: 3, the gate is {integral_loop.GATE_SESSIONS} (7 to go)")
    assert printed[2] == "reads learned from: 8; left out: 1" and printed[3] == "  1 a stale read: no bar traded its spot in the minutes up to it"
    assert any(line.startswith("  q_a: ") and "over 3 days" in line for line in printed)


def test_the_dry_run_refuses_a_lane_with_no_loop(tmp_path, capsys):
    assert grade.main(["--lane", "tape", "--state-dir", str(tmp_path), "--integral-loop-dry-run"]) == 1
    assert "keeps no learning loop" in capsys.readouterr().err


def test_the_dry_run_says_when_the_gate_is_met_and_keeps_passed_over_sessions_apart_from_left_out_ones(tmp_path, clock):
    ten = [f"2026-09-{d:02d}" for d in (14, 15, 16, 17, 18, 21, 22, 23, 24, 25)]
    for n, gate in ((10, "(met)"), (9, "(1 to go)")):
        out = _write(tmp_path / str(n), {d: SESSIONS[DAYS[0]] for d in ten[:n]})
        said = integral_loop.describe(integral_loop.dry_run(out, LIVE, "2026-09-28"))[1]
        assert said.startswith(f"sessions learned from: {n}, the gate is 10 {gate}; passed over: 0; every read left out: 0")
    mixed = _write(tmp_path / "mixed", {"2026-09-11": SESSIONS[DAYS[0]]}, average=False)
    _write(mixed, {**SESSIONS, "2026-09-17": SESSIONS[DAYS[0]]})
    _edit(mixed / "hour" / "2026-09-17.jsonl", lambda r: {**r, "event": {"within_30": True}})
    said = integral_loop.describe(integral_loop.dry_run(mixed, LIVE, "2026-09-18"))
    assert said[1].startswith("sessions learned from: 3, the gate is 10 (7 to go); passed over: 1; every read left out: 1")
    assert "promotion needs 20 after 20 days, and the simulation gates, pool.SIM_GATES_PASSED off)" in said[-2]
