"""The learning loop's switch to the average-price grade: off, the grader and the loop are exactly what they were and no
file of the switched loop is made or read; on, the loop learns from the average-price label alone, into files of its
own, the end-price loop's left as they were, and no flip of the switch ever mixes the two histories."""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import at
from spx_jev import grade, integral, integral_loop, lane, pool
from spx_jev.clock import INTEGRAL_CACHE_NAME, MIN_SESSIONS, PHASES, _integral_rule_key
from spx_jev.grade import INTEGRAL_NAME, weights_from
from spx_jev.lane import LANES, LIVE

ON = replace(LIVE, integral_loop=True)
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


def _write(out: Path, sessions: dict = SESSIONS, stale_at=None, event_at=None, average=True, integral_lines=True) -> Path:
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
            if average:
                rec["average"] = {"primary": "average_30", "box": "next_30", "pick": max(SHOWN_AVG, key=SHOWN_AVG.get), "probabilities": SHOWN_AVG,
                                  "blend": {"used": True}, "jev": {"pick": "up", "probabilities": JEV_AVG},
                                  "clock": {"pick": "flat", "probabilities": CLOCK_AVG}, "edge_points": 3.11}
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


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _learn(out: Path, lane_=LIVE) -> dict:
    return weights_from(grade.load_jsonl(out / "grades.jsonl"), ALLOWED, lane_, out)


def _log(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / integral_loop.LOG_NAME).read_text().splitlines()]


# ---- off

def test_the_switch_is_off_on_every_lane():
    """Will turns it on after the gate; until then no lane learns from the average-price grade."""
    assert all(x.integral_loop is False for x in LANES.values()) and LIVE.pool and not lane.TAPE.pool


def test_with_the_switch_off_no_file_of_the_switched_loop_is_made_or_read(tmp_path, clock, monkeypatch):
    """The grader learns the end-price loop exactly as before: the same weights and files with a switched loop's state
    lying beside them or not, and nothing of the switched loop is called."""
    plain, beside = _write(tmp_path / "plain"), _write(tmp_path / "beside")
    (beside / "pool_30_integral.json").write_text("{not a state")
    for name in ("update", "load_state"):
        monkeypatch.setattr(integral_loop, name, lambda *a, **k: pytest.fail("the switched loop was called with the switch off"))
    w_plain, w_beside = _learn(plain), _learn(beside)
    assert w_plain == w_beside and w_plain["method"] == "pool_v1" and w_plain["pool"]["last_session_applied"] == DAYS[-1]
    assert (beside / "pool_30_integral.json").read_text() == "{not a state"
    assert not any((plain / name).exists() for name in INTEGRAL_FILES) and not (beside / integral_loop.LOG_NAME).exists()
    after_plain, after_beside = _tree(plain), _tree(beside)
    del after_beside["pool_30_integral.json"]
    assert after_plain == after_beside


# ---- on

def test_on_the_loop_learns_from_the_average_price_label_and_writes_only_its_own_files(tmp_path, clock):
    out = _write(tmp_path / "out")
    before = _tree(out)
    w = _learn(out, ON)
    after = _tree(out)
    assert set(after) - set(before) == set(INTEGRAL_FILES) and all(after[k] == v for k, v in before.items())
    assert not any((out / name).exists() for name in END_PRICE_FILES)
    assert w["method"] == "pool_v1_integral" and w["pool"]["last_session_applied"] == DAYS[-1] and w["pool"]["phone"]["on_phone"] is False
    assert w["questions"]["q_a"]["days"] == 3 and w["questions"]["q_a"]["weight"] == 1.0
    state = json.loads((out / "pool_30_integral.json").read_text())
    assert state["constants_hash"] == integral_loop.CONSTANTS_HASH != pool.CONSTANTS_HASH
    assert state["baseline"].startswith("integral_clock:")
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
    later; with fewer than the clock needs, its reads are left out and said so."""
    out = _write(tmp_path / "out")
    ref = integral_loop.reference(json.loads((out / INTEGRAL_CACHE_NAME).read_text())["days"], DAYS[0], "v")
    assert ref.clock("next_30", at(10, 2, day=DAYS[0])) == pytest.approx({"up": 0.25, "flat": 0.5, "down": 0.25})
    later = _write(tmp_path / "later")
    _cache(later, {**_prior_counts(), DAYS[0]: _counts(9, 0, 0), DAYS[1]: _counts(9, 0, 0)})
    integral_loop.update(out, "2026-09-15")
    integral_loop.update(later, "2026-09-15")
    assert (later / "pool_30_integral.json").read_bytes() == (out / "pool_30_integral.json").read_bytes()
    thin = _write(tmp_path / "thin")
    _cache(thin, _prior_counts(MIN_SESSIONS - 1))
    integral_loop.update(thin, TODAY)
    reasons = {why for x in _log(thin) for why in (x.get("manifest") or {}).get("excluded", {}).values()}
    assert reasons == {f"no snapshot: fewer than {MIN_SESSIONS} sessions of time-of-day odds on the average price before this one"}


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
    on from the start, and the end-price loop's the one it reaches kept off, to the byte."""
    flipped, always_on, always_off = (_write(tmp_path / name) for name in ("flipped", "on", "off"))
    clock(DAYS[1])
    _learn(flipped, ON)
    clock(DAYS[2])
    _learn(flipped, LIVE)
    clock(TODAY)
    _learn(flipped, ON)
    for day in (DAYS[1], DAYS[2], TODAY):
        clock(day)
        _learn(always_on, ON)
        _learn(always_off, LIVE)
    assert (flipped / "pool_30_integral.json").read_bytes() == (always_on / "pool_30_integral.json").read_bytes()
    assert json.loads((flipped / "pool_30_integral.json").read_text())["last_session_applied"] == DAYS[2]
    # the end-price loop, on for its one run, applied the two sessions sealed by then and nothing the switched loop learned
    stopped_at = _write(tmp_path / "stopped_at")
    pool.update(stopped_at, today=DAYS[2])
    for name in ("pool_30.json", "pool_60.json"):
        assert (flipped / name).read_bytes() == (stopped_at / name).read_bytes()
    assert json.loads((always_off / "pool_30.json").read_text())["last_session_applied"] == DAYS[2]

