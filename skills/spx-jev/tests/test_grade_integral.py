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

from conftest import DAY, FixedZones, at, bars_from_closes, make_row, write_state
from spx_jev import archive, grade, integral, integral_loop, pool
from spx_jev.grade import INTEGRAL_LOCK, INTEGRAL_NAME, grade_one, integral_line, integral_report, integral_run, main, run
from spx_jev.lane import PREMARKET, TAPE

SIGMA = 75.0
ZONES = FixedZones.POINTS["live"]
ALLOWED = {"q1": {"rising", "falling"}}


@pytest.fixture(autouse=True)
def _zones(fixed_zones):
    """Every read, grade and replay here runs on a fixture too short to size a flat zone: the zones are pinned (conftest.FixedZones)."""


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
    assert (climb["minutes"], climb["f"], climb["factor"]) == (30, 5.25, 0.5918)
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
    """The opening lane: its flat line is its call's zone widened back, and the old five-band grade at the end price is the size line."""
    t0 = at(10, 30)
    closes = [7703.59, 7702.66, 7700.86, 7701.56, 7700.65, 7700.67, 7699.33, 7700.32, 7697.34, 7693.9, 7693.44]   # S9, from the bar before
    bars = bars_from_closes([7703.59] * 59 + closes + [7693.44] * 320)
    by = {"next_10": {"pick": "down_small", "probabilities": {"down_small": 0.5, "flat": 0.3, "down_big": 0.2}}}
    rec = {"row_ts": t0.isoformat(), "spot": 7703.59, "sigma": SIGMA, "lane": "tape", "by": by, "used": {}, "fresh": {},
           "ruler": {"unit_points": 6.1}, "band": {"flat_points": 2.56, "big_points": 5.42}}
    zones = {"next_10": 2.56, "average_10": 1.59, "big": 5.42}
    g = grade_one(rec, bars, lane=TAPE, zones=zones)
    line = integral_line(g, "next_10", rec, bars, {}, TAPE, zones)
    assert (line["g"], line["edge"], line["label"], line["verdict"]) == (-4.52, 1.59, "down", "right")
    assert line["end_label"] == "down" and line["size"] == {"band": "down_big", "size": "big", "call": "small", "right": False}


def test_the_verdict_is_the_averages_never_the_end_prices():
    """The owner's S1 on the 30-minute box: a flat call, the end price back to flat (+3.18 against a 5.39 zone), the
    average up (+4.45 against 3.19) after the run to +19. The line keeps the end price's flat beside it, and the call is
    wrong on the average."""
    from test_integral import SCENARIOS
    _, spot, f, pick, closes = SCENARIOS["S1"]
    bars = bars_from_closes([spot] * 211 + closes + [closes[-1]] * (390 - 211 - len(closes)))     # 13:01 is 211 minutes on
    rec = _rec(13, 1, pick30=pick, spot=spot)
    zones = {"next_30": f, "next_60": 8.25, "average_30": 3.19}
    g = grade_one(rec, bars, zones=zones)
    assert g["next_30"]["band"] == "flat" and g["next_30"]["hit"] is True
    line = integral_line(g, "next_30", rec, bars, {}, zones=zones)
    assert (line["g"], line["edge"], line["label"]) == (4.45, 3.19, "up")
    assert line["end_label"] == "flat" and line["verdict"] == "wrong"


AVERAGE_CALL = {"primary": "average_30", "box": "next_30", "pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1},
                "minutes": 30, "flat_points": 5.25, "edge_points": 3.11}


def test_a_call_whose_end_price_sums_got_no_answer_is_graded_on_its_own_window(tmp_path, clock):
    """Only the end-price request failed: the phone shows the average-price call, so it is graded from its own window
    as any call is, though grades.jsonl has nothing to grade; one whose window ends past the close says so for good."""
    failed = {"row_ts": at(11, 0).isoformat(), "spot": 7700.0, "sigma": SIGMA, "error": "JEV returned HTTP 529 for group hour",
              "average": AVERAGE_CALL, "used": {"q1": "rising"}, "fresh": {"q1": "rising"}}
    half = {**_rec(12, 30), "error": "no next_30 answer", "average": AVERAGE_CALL}
    del half["by"]["next_30"]
    late = {**failed, "row_ts": at(15, 45).isoformat()}
    state = _state(tmp_path, recs=[failed, half, late])
    out = state / "spx_jev"
    run(state, out, ALLOWED)
    assert [(g["row_ts"], g["horizons"], g["skipped"]) for g in _lines(out / "grades.jsonl")] == [
        (half["row_ts"], ["next_60"], {"next_30": "no answer for this sum"})]        # the end-price grade, as before
    lines = {(g["row_ts"], g["horizon"]): g for g in _lines(out / INTEGRAL_NAME)}
    assert set(lines) == {(failed["row_ts"], "next_30"), (half["row_ts"], "next_30"), (half["row_ts"], "next_60"), (late["row_ts"], "next_30")}
    for ts in (failed["row_ts"], half["row_ts"]):
        g = lines[(ts, "next_30")]
        assert (g["sum"], g["graded"], g["pick"], g["label"], g["verdict"], g["edge"], g["edge_told"]) == ("average_30", True, "up", "up", "right", 3.11, 3.11)
        assert g["end_price"] == grade.ALONE and "end_label" not in g and g["scores"]["brier"] == 0.26 and g["f"] == 5.25
    assert lines[(half["row_ts"], "next_60")]["sum"] == "next_60"
    assert (lines[(late["row_ts"], "next_30")]["graded"], lines[(late["row_ts"], "next_30")]["reason"]) == (False, "not graded: ends past the close")
    before = (out / INTEGRAL_NAME).read_bytes()
    run(state, out, ALLOWED)
    assert (out / INTEGRAL_NAME).read_bytes() == before                             # graded once, as every window is
    # the report has no end price to set them against; the 12:30 read's spot is 30 points under where price traded
    assert integral_report(out)["next_30"] == {"n": 2, "flat_integral": 0.0, "flat_end": None, "stale": 1, "not_graded": 1}


def test_a_call_graded_alone_waits_today_for_its_bars_like_any_other(tmp_path, clock):
    failed = {"row_ts": at(11, 0).isoformat(), "spot": 7700.0, "sigma": SIGMA, "error": "timed out", "average": AVERAGE_CALL}
    state = _state(tmp_path, bars=_climb()[:110], recs=[failed])
    out = state / "spx_jev"
    clock(DAY)
    run(state, out, ALLOWED)
    assert not (out / INTEGRAL_NAME).exists()                                        # its mark is ahead: a later run grades it
    clock("2026-09-21")
    run(state, out, ALLOWED)
    (g,) = _lines(out / INTEGRAL_NAME)
    assert (g["graded"], g["reason"], g["sum"]) == (False, integral.NOT_GRADED, "average_30")


def test_a_premarket_horizon_runs_from_the_settled_open_against_its_zones():
    bars = bars_from_closes([7700.0] * 5 + [7700.0 + k for k in range(1, 386)])
    by = {"open_10": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}},
          "open_30": {"pick": "flat", "probabilities": {"up": 0.3, "flat": 0.5, "down": 0.2}}}
    rec = {"row_ts": at(9, 28).isoformat(), "spot": 7600.0, "sigma": 100.0, "lane": "premarket", "ruler": {"kind": "pre_open", "points": 100.0},
           "by": by, "used": {}, "fresh": {}}
    zones = {"open_10": 4.0, "open_30": 7.0, "open_average_30": 4.14}
    g = grade_one(rec, bars, lane=PREMARKET, zones=zones)
    ten = integral_line(g, "open_10", rec, bars, {}, PREMARKET, zones)
    assert (ten["from"], ten["minutes"], ten["f"], ten["g"]) == (7700.0, 10, 4.0, 5.5) and ten["stale_read"] is False
    assert ten["edge"] == round(4.0 * integral.factor(10), 2) and ten["label"] == "up" and ten["verdict"] == "right" and "size" not in ten


def test_the_premarket_0945_check_grades_the_call_the_card_shows_not_the_ten_minute_end_price_pick():
    """The card shows the average-price call; its 09:45 check is that same pick over the first ten minutes, against the
    ten-minute zone narrowed for them, never the told 30-minute edge and never the 30-minute odds' scores."""
    bars = bars_from_closes([7700.0] * 5 + [7700.0 + k for k in range(1, 386)])
    call = {"pick": "down", "probabilities": {"up": 0.2, "flat": 0.2, "down": 0.6}, "edge_points": 3.55, "primary": "open_average_30"}
    rec = {"row_ts": at(9, 28).isoformat(), "spot": 7600.0, "sigma": 100.0, "lane": "premarket", "ruler": {"kind": "pre_open", "points": 100.0},
           "by": {"open_10": {"pick": "up", "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}},
                  "open_30": {"pick": "flat", "probabilities": {"up": 0.3, "flat": 0.5, "down": 0.2}}},
           "average": call, "used": {}, "fresh": {}}
    zones = {"open_10": 4.0, "open_30": 7.0, "open_average_30": 3.55}
    g = grade_one(rec, bars, lane=PREMARKET, zones=zones)
    ten, thirty = (integral_line(g, h, rec, bars, {}, PREMARKET, zones) for h in ("open_10", "open_30"))
    assert (ten["sum"], ten["pick"], ten["verdict"]) == ("open_average_30", "down", "wrong") and ten["end_label"] == "up"
    assert ten["edge"] == round(4.0 * integral.factor(10), 2) and "scores" not in ten and "edge_told" not in ten
    assert (thirty["sum"], thirty["pick"], thirty["edge"], thirty["edge_told"]) == ("open_average_30", "down", 3.55, 3.55) and "scores" in thirty
    # a read whose average-price sum got no answer checks each box on its own end-price pick, as before
    alone = integral_line(g, "open_10", {**rec, "average": {"error": "HTTP 529", "primary": "open_average_30"}}, bars, {}, PREMARKET, zones)
    assert (alone["sum"], alone["pick"]) == ("open_10", "up")


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
