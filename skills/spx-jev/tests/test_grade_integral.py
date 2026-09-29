"""The average-price grade beside the end-price grade: every graded horizon of every lane gets one line in its own
file, once, filled for past days by the backfill, while grades.jsonl, the weights and the end-price loop's state stay exactly
what the grader wrote without it."""
from __future__ import annotations

import fcntl
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

import pytest

from conftest import DAY, at, bars_from_closes, make_row, write_state
from spx_jev import archive, grade, integral, integral_loop, pool
from spx_jev.grade import INTEGRAL_LOCK, INTEGRAL_NAME, grade_one, integral_line, integral_report, integral_run, main, run
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.lane import PREMARKET, TAPE

SIGMA = 75.0
ALLOWED = {"q1": {"rising", "falling"}}


def _frozen(day: str, hh: int = 12):
    """A clock stopped at ``hh``:00 New York time on ``day``, so two runs stamp their logs alike and "today" is ``day``."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            t = datetime.fromisoformat(f"{day}T{hh:02d}:00:00-04:00")
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    return Frozen


@pytest.fixture
def clock(monkeypatch):
    """Stop every clock the grader, the loop and the archive stamp with; call it again to move to another day."""
    def set_day(day: str = "2026-09-21"):
        for module in (grade, pool, integral_loop, archive):
            monkeypatch.setattr(module, "datetime", _frozen(day))
    set_day()
    return set_day


def _rows():
    return [make_row(at(9 + (30 + m) // 60, (30 + m) % 60), 7700.0, sigma=SIGMA, sigma_anchor=SIGMA, sigma_live=SIGMA) for m in range(1, 390, 5)]


def _climb():
    """Flat to 11:00, then 30 points up by 12:00, then flat."""
    return bars_from_closes([7700.0] * 90 + [7700.0 + 30.0 * (i + 1) / 60 for i in range(60)] + [7730.0] * 240)


def _rec(hh, mm, pick30="up", pick60="flat", spot=7700.0):
    by = {"next_30": {"pick": pick30, "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.1, "unsure": 0.1}},
          "next_60": {"pick": pick60, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.1, "unsure": 0.1}}}
    return {"row_ts": at(hh, mm).isoformat(), "spot": spot, "sigma": SIGMA, "by": by, "used": {"q1": "rising"}, "fresh": {"q1": "rising"}}


def _state(root: Path, bars=None, recs=None) -> Path:
    state = write_state(root, DAY, _rows(), _climb() if bars is None else bars)
    (state / "spx_jev" / "hour").mkdir(parents=True)
    (state / "spx_jev" / "hour" / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs or [_rec(11, 0), _rec(12, 30, "unsure", spot=7730.0)]))
    return state


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` but the side file and its lock."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.name not in (INTEGRAL_NAME, INTEGRAL_LOCK)}


def test_every_graded_horizon_gets_one_integral_line_and_a_second_run_writes_none(tmp_path, clock):
    state = _state(tmp_path)
    out = state / "spx_jev"
    run(state, out, ALLOWED)
    lines = _lines(out / INTEGRAL_NAME)
    graded = [(g["row_ts"], q) for g in _lines(out / "grades.jsonl") for q in g.get("horizons") or []]
    assert [(g["row_ts"], g["horizon"]) for g in lines] == graded and len(graded) == 4
    assert all(g["rule_version"] == integral.RULE_VERSION and g["graded"] is True for g in lines)
    climb = lines[0]                                               # 11:00, up: the climb's first half hour
    assert (climb["minutes"], climb["f"], climb["factor"]) == (30, round(0.07 * SIGMA, 4), 0.5918)
    assert climb["label"] == climb["end_label"] == "up" and climb["verdict"] == "right" and climb["stale_read"] is False
    assert climb["g"] == round(sum(30.0 * (i + 1) / 60 for i in range(30)) / 30, 2)
    passed = next(g for g in lines if g["row_ts"] == at(12, 30).isoformat() and g["horizon"] == "next_30")
    assert passed["verdict"] == "passed" and passed["lean"] == {"direction": "up", "p": 0.5} and passed["label"] == "flat"
    before = (out / INTEGRAL_NAME).read_bytes()
    run(state, out, ALLOWED)
    integral_run(state, out)
    assert (out / INTEGRAL_NAME).read_bytes() == before            # keyed by read, horizon and rule version: never twice


def test_grades_weights_and_the_loops_state_are_byte_for_byte_what_the_grader_writes_without_it(tmp_path, clock, monkeypatch):
    """The same state graded twice, once as the code stood before the integral grade (its pass a no-op) and once
    with it: every file under the state dir but the side file is the same to the byte."""
    with_it, without = _state(tmp_path / "with"), tmp_path / "without"
    shutil.copytree(with_it, without)
    run(with_it, with_it / "spx_jev", ALLOWED)
    monkeypatch.setattr(grade, "integral_run", lambda *a, **k: [])
    run(without, without / "spx_jev", ALLOWED)
    assert _lines(with_it / "spx_jev" / INTEGRAL_NAME) and not (without / "spx_jev" / INTEGRAL_NAME).exists()
    after = _tree(with_it)
    assert {"spx_jev/grades.jsonl", "spx_jev/weights.json", "spx_jev/weights_log.jsonl", "spx_jev/pool_30.json", "spx_jev/pool_60.json"} <= set(after)
    assert after == _tree(without)


def test_a_hole_waits_today_and_is_written_not_graded_once_the_day_is_over(tmp_path, clock):
    holes = {at(11, 10).isoformat(), at(11, 20).isoformat()}
    state = _state(tmp_path, bars=[b for b in _climb() if b["ts"] not in holes], recs=[_rec(11, 0)])
    out = state / "spx_jev"
    clock(DAY)
    run(state, out, ALLOWED)
    assert _lines(out / "grades.jsonl")[0]["horizons"] == ["next_30", "next_60"]      # the end-price grade reads only the mark
    assert not (out / INTEGRAL_NAME).exists()                                          # this one waits: the bars may still come
    clock("2026-09-21")
    run(state, out, ALLOWED)
    held = _lines(out / INTEGRAL_NAME)
    assert [(g["horizon"], g["graded"], g["reason"], g["missing"]) for g in held] == [
        ("next_30", False, integral.NOT_GRADED, [11, 21]), ("next_60", False, integral.NOT_GRADED, [11, 21])]
    run(state, out, ALLOWED)
    assert _lines(out / INTEGRAL_NAME) == held


def test_the_backfill_fills_past_days_from_their_bars_and_touches_nothing_else(tmp_path, clock, monkeypatch):
    live = _state(tmp_path / "live")
    shutil.copytree(live, tmp_path / "past")
    past = tmp_path / "past"
    run(live, live / "spx_jev", ALLOWED)                           # graded with the integral grade from the start
    with monkeypatch.context() as m:
        m.setattr(grade, "integral_run", lambda *a, **k: [])
        run(past, past / "spx_jev", ALLOWED)                       # graded before it existed
    before = _tree(past)
    assert main(["--integral-backfill", "--state-dir", str(past)]) == 0
    assert _tree(past) == before
    filled = (past / "spx_jev" / INTEGRAL_NAME).read_bytes()
    assert filled == (live / "spx_jev" / INTEGRAL_NAME).read_bytes()      # the same lines the run writes
    assert main(["--integral-backfill", "--state-dir", str(past), "--day", DAY]) == 0
    assert (past / "spx_jev" / INTEGRAL_NAME).read_bytes() == filled      # idempotent


def test_a_record_horizon_carries_the_five_band_size_beside_the_direction():
    """The opening lane: its flat band is the record's, and the old five-band grade at the end price is the size line."""
    t0 = at(10, 30)
    closes = [7703.59, 7702.66, 7700.86, 7701.56, 7700.65, 7700.67, 7699.33, 7700.32, 7697.34, 7693.9, 7693.44]   # S9, from the bar before
    bars = bars_from_closes([7703.59] * 59 + closes + [7693.44] * 320)
    by = {"next_10": {"pick": "down_small", "probabilities": {"down_small": 0.5, "flat": 0.3, "down_big": 0.2}}}
    rec = {"row_ts": t0.isoformat(), "spot": 7703.59, "sigma": SIGMA, "lane": "tape", "by": by, "used": {}, "fresh": {},
           "ruler": {"unit_points": 6.1}, "band": {"flat_points": 2.56, "big_points": 5.42}}
    g = grade_one(rec, bars, lane=TAPE)
    line = integral_line(g, "next_10", rec, bars, {}, TAPE)
    assert (line["g"], line["edge"], line["label"], line["verdict"]) == (-4.52, 1.59, "down", "right")
    assert line["end_label"] == "down" and line["size"] == {"band": "down_big", "size": "big", "call": "small", "right": False}


def test_the_verdict_is_the_averages_never_the_end_prices():
    """The owner's S1 on the 30-minute box: a flat call, the end price back to flat (+3.18 against a 5.39 band), the
    average up (+4.45 against 3.19) after the run to +19. The line keeps the end price's flat beside it, and the call is
    wrong on the average."""
    from test_integral import SCENARIOS
    _, spot, f, pick, closes = SCENARIOS["S1"]
    bars = bars_from_closes([spot] * 211 + closes + [closes[-1]] * (390 - 211 - len(closes)))     # 13:01 is 211 minutes on
    rec = _rec(13, 1, pick30=pick, spot=spot)
    g = grade_one(rec, bars, anchor=SigmaRuler(f / 0.07, "anchor"))
    assert g["next_30"]["band"] == "flat" and g["next_30"]["hit"] is True
    line = integral_line(g, "next_30", rec, bars, {})
    assert (line["g"], line["edge"], line["label"]) == (4.45, 3.19, "up")
    assert line["end_label"] == "flat" and line["verdict"] == "wrong"


def test_a_premarket_horizon_runs_from_the_settled_open_in_the_stamped_ruler():
    bars = bars_from_closes([7700.0] * 5 + [7700.0 + k for k in range(1, 386)])
    by = {"open_10": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}},
          "open_30": {"pick": "flat", "probabilities": {"up": 0.3, "flat": 0.5, "down": 0.2}}}
    rec = {"row_ts": at(9, 28).isoformat(), "spot": 7600.0, "sigma": 100.0, "lane": "premarket", "ruler": {"kind": "pre_open", "points": 100.0},
           "by": by, "used": {}, "fresh": {}}
    g = grade_one(rec, bars, lane=PREMARKET, anchor=SigmaRuler(100.0, "pre_open"))
    ten = integral_line(g, "open_10", rec, bars, {}, PREMARKET)
    assert (ten["from"], ten["minutes"], ten["f"], ten["g"]) == (7700.0, 10, 4.0, 5.5) and ten["stale_read"] is False
    assert ten["edge"] == round(4.0 * integral.factor(10), 2) and ten["label"] == "up" and ten["verdict"] == "right" and "size" not in ten


def test_the_report_gives_each_box_its_flat_share_on_both_grades(tmp_path, capsys):
    out = tmp_path / "spx_jev"
    out.mkdir()
    lines = [{"row_ts": f"r{k}", "horizon": "next_30", "rule_version": integral.RULE_VERSION, "graded": True, "label": lab, "end_label": end,
              "stale_read": k == 0} for k, (lab, end) in enumerate((("flat", "flat"), ("flat", "up"), ("up", "up"), ("flat", "down")))]
    lines += [{"row_ts": "r9", "horizon": "next_30", "rule_version": integral.RULE_VERSION, "graded": False, "reason": integral.NOT_GRADED},
              {"row_ts": "r8", "horizon": "next_30", "rule_version": integral.RULE_VERSION + 1, "graded": True, "label": "up", "end_label": "up"}]
    (out / INTEGRAL_NAME).write_text("".join(json.dumps(g) + "\n" for g in lines))
    assert integral_report(out) == {"next_30": {"n": 4, "flat_integral": 0.75, "flat_end": 0.25, "stale": 1, "not_graded": 1},
                                    "next_60": {"n": 0, "flat_integral": None, "flat_end": None, "stale": 0, "not_graded": 0}}
    assert main(["--integral-report", "--state-dir", str(tmp_path)]) == 0
    said = capsys.readouterr().out.splitlines()
    assert said[0] == "live next_30: 4 windows graded; flat 75.0% on the average price against 25.0% on the end price; 1 stale reads, 1 not graded"
    assert said[1] == "live next_60: no window graded on the average price yet; 0 not graded"
    assert [s.split(":")[0] for s in said[2:]] == ["tape next_10", "premarket open_10", "premarket open_30"]



def test_a_line_graded_before_the_morning_anchor_is_measured_in_the_records_sigma():
    rec = {**_rec(11, 0), "sigma": 80.0}
    g = grade_one(rec, _climb(), anchor=SigmaRuler(SIGMA, "anchor"))
    assert integral_line(g, "next_30", rec, _climb(), {})["f"] == round(0.07 * SIGMA, 4)
    del g["anchor"]
    assert integral_line(g, "next_30", rec, _climb(), {})["f"] == round(0.07 * 80.0, 4)


def test_the_side_file_only_ever_grows(tmp_path, clock):
    """A second day's run appends its lines after the first day's, whose bytes stay exactly as they were."""
    state = _state(tmp_path)
    out = state / "spx_jev"
    run(state, out, ALLOWED)
    first = (out / INTEGRAL_NAME).read_bytes()
    day2 = "2026-09-21"
    write_state(state, day2, [make_row(at(9, 31, day=day2), 7700.0, sigma=SIGMA, sigma_anchor=SIGMA, sigma_live=SIGMA)],
                bars_from_closes([7700.0] * 390, day=day2))
    (out / "hour" / f"{day2}.jsonl").write_text(json.dumps({**_rec(11, 0), "row_ts": at(11, 0, day=day2).isoformat()}) + "\n")
    clock("2026-09-22")
    run(state, out, ALLOWED)
    grown = (out / INTEGRAL_NAME).read_bytes()
    added = [json.loads(line) for line in grown[len(first):].decode().splitlines()]
    assert grown.startswith(first) and [(g["row_ts"][:10], g["horizon"]) for g in added] == [(day2, "next_30"), (day2, "next_60")]


def test_a_line_under_an_older_rule_version_does_not_stand_for_this_one(tmp_path, clock):
    state = _state(tmp_path, recs=[_rec(11, 0)])
    out = state / "spx_jev"
    old = {"row_ts": at(11, 0).isoformat(), "horizon": "next_30", "rule_version": integral.RULE_VERSION - 1, "graded": True, "label": "flat"}
    (out / INTEGRAL_NAME).write_text(json.dumps(old) + "\n")
    run(state, out, ALLOWED)
    lines = _lines(out / INTEGRAL_NAME)
    assert lines[0] == old and [(g["horizon"], g["rule_version"]) for g in lines[1:]] == [
        ("next_30", integral.RULE_VERSION), ("next_60", integral.RULE_VERSION)]


def test_a_stale_reads_flag_reaches_the_side_file(tmp_path, clock):
    """A record whose spot, 7690, no bar near 11:00 traded (price stood at 7700): graded, and flagged for anything that learns."""
    state = _state(tmp_path, recs=[_rec(11, 0, spot=7690.0)])
    run(state, state / "spx_jev", ALLOWED)
    assert {(g["stale_read"], g["graded"]) for g in _lines(state / "spx_jev" / INTEGRAL_NAME)} == {(True, True)}


def test_a_backfill_waits_for_a_run_holding_the_side_file(tmp_path, clock, monkeypatch):
    state = _state(tmp_path)
    out = state / "spx_jev"
    with monkeypatch.context() as m:
        m.setattr(grade, "integral_run", lambda *a, **k: [])
        run(state, out, ALLOWED)                                   # graded, the side file not yet filled
    done = []
    with open(out / INTEGRAL_LOCK, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        worker = threading.Thread(target=lambda: done.append(integral_run(state, out)))
        worker.start()
        worker.join(0.3)
        assert worker.is_alive() and not (out / INTEGRAL_NAME).exists()      # it waits for the lock
    worker.join(5)
    assert len(done[0]) == 4 and len(_lines(out / INTEGRAL_NAME)) == 4
