"""The premarket lane end to end with a fake JEV: its checkpoints and the gate that refuses a late fire, the scene
without a diary row, the snapshot reads and the JEV reads, the records, the card, the close-out, and a replay
that saves nothing into the station."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from conftest import DAY, at, bars_from_closes, make_row, night_row
from spx_jev import archive, build, events, grade, overnight, premarket, service, story
from spx_jev.labels.context import PREMARKET_UNITS, build_context_labels
from spx_jev.lane import PREMARKET
from spx_jev.premarket import NoPreOpenRead, checkpoints, due, make_premarket_scene, run_checkpoint

PRIOR_DAYS = ("2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17")
DOC = {"version": "test", "groups": [
    {"id": "pm", "reads": ["context"],
     "questions": {"pm_q": {"status": "live", "viewpoint": "premarket", "type": "choice", "ask": "Is the open near?",
                            "instructions": "Read `context.session_progress`.",
                            "criteria": {"near": "n", "far": "f", "unsure": "u"}, "schedule": {"at": ["08:48", "09:28"]}},
                   "pm_dark": {"status": "dark", "viewpoint": "premarket", "type": "choice", "ask": "Dark?",
                               "instructions": "Read `context.units`.", "criteria": {"yes": "y", "no": "n"}}}}]}
SUMS = {"open_10": {"type": "choice", "choice": "flat", "confidence": 0.6, "probabilities": {"up": 0.2, "flat": 0.6, "down": 0.15, "unsure": 0.05}},
        "open_30": {"type": "choice", "choice": "up", "confidence": 0.5, "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.15, "unsure": 0.05}}}


def _night(now: datetime) -> list[dict]:
    """/ES at 7750 to yesterday's 16:00, then 7781 (up 0.4%), and /ZN flat, one bar a minute to ``now``."""
    start = at(15, 55, day=PRIOR_DAYS[-1])
    close = at(16, 0, day=PRIOR_DAYS[-1])
    out = []
    for k in range(int((now - start).total_seconds() // 60)):
        t = start + timedelta(minutes=k)
        out.append(night_row("/ES", t, 7750.0 if t < close else 7781.0))
        out.append(night_row("/ZN", t, 112.0))
    return out


def _station(root, now=at(9, 28), anchors: int = len(PRIOR_DAYS)):
    """Six prior sessions of SPX bars closing at 7700, a morning anchor (60 to 65 points) on the last ``anchors`` of
    them, and the night's store to ``now``."""
    (root / "reversion" / "bars").mkdir(parents=True, exist_ok=True)
    for k, d in enumerate(PRIOR_DAYS):
        (root / "reversion" / "bars" / f"{d}-SPX.json").write_text(json.dumps(bars_from_closes([7700.0] * 390, day=d)))
        if k >= len(PRIOR_DAYS) - anchors:
            (root / "reversion" / f"{d}.jsonl").write_text(json.dumps(make_row(at(9, 35, day=d), 7700.0, sigma=60.0 + k)) + "\n")
    path = overnight.night_path(root, DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in _night(now)))
    return root


def _answers(seen):
    def send_all(requests, **kw):
        seen.extend(requests)
        return {r["id"]: {"model": "fake-1", "answers": {qid: {"type": "choice", "choice": "near", "confidence": 0.8,
                                                              "probabilities": {"near": 0.8, "far": 0.1, "unsure": 0.1}}
                                                        for qid in r["questions"]}} for r in requests}
    return send_all


def _sums(seen):
    def send(req, **kw):
        seen.append(req)
        return {"model": "fake-1", "answers": SUMS}
    return send


@pytest.fixture
def jev(monkeypatch):
    """A fake JEV and grader, and a day with no report: what the lane sent and whom it graded."""
    seen = {"requests": [], "sums": [], "graded": []}
    monkeypatch.setattr(premarket, "send_all", _answers(seen["requests"]))
    monkeypatch.setattr(premarket, "send", _sums(seen["sums"]))
    monkeypatch.setattr(grade, "run", lambda state_dir, out_dir, allowed, day=None, lane=None: seen["graded"].append(lane))
    monkeypatch.setattr(story, "releases", lambda day: [])
    return seen


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---- when it reads -------------------------------------------------------------------------------

def test_europes_checkpoint_follows_frankfurts_clock():
    assert checkpoints(date(2026, 9, 28)) == PREMARKET.schedule
    assert checkpoints(date(2026, 10, 27))[1] == "04:35"            # Frankfurt on winter time, New York not yet
    assert checkpoints(date(2026, 11, 3))[1] == "03:35"             # both on winter time
    assert checkpoints(date(2027, 3, 16))[1] == "04:35"             # New York on summer time, Frankfurt not yet


@pytest.mark.parametrize("hh, mm, day, want", [
    (2, 35, DAY, "02:35"), (2, 39, DAY, "02:35"), (8, 48, DAY, "08:48"), (9, 29, DAY, "09:28"), (10, 6, DAY, "10:06"),
    (2, 40, DAY, None), (7, 0, DAY, None), (9, 31, DAY, None), (10, 12, DAY, None), (1, 0, DAY, None),
    (2, 35, "2026-09-19", None),                                     # a Saturday
    (9, 28, "2026-11-26", None),                                     # Thanksgiving
    (3, 35, "2026-10-27", None), (4, 36, "2026-10-27", "04:35")])
def test_a_fire_runs_only_within_five_minutes_after_a_checkpoint_before_the_open_or_the_close_out(hh, mm, day, want):
    got, why = due(at(hh, mm, day=day))
    assert got == want, why


def test_a_refused_fire_says_why():
    assert due(at(7, 0))[1] == "07:00 ET is 205 minutes after the 03:35 ET checkpoint, past the 5-minute line: a late fire reads nothing"
    assert due(at(9, 31))[1] == "09:31 ET is after the 09:30 open: a premarket read comes before it"
    assert due(at(9, 28, day="2026-09-19"))[1] == "2026-09-19 is not a market day"


# ---- the scene -----------------------------------------------------------------------------------

def test_the_scene_prices_spx_off_the_futures_and_stamps_the_pre_open_ruler(tmp_path):
    scene = make_premarket_scene(_station(tmp_path), at(9, 28))
    assert scene.premarket and scene.bars == [] and scene.rows_today == [scene.row]
    assert scene.row["ts"] == at(9, 28).isoformat() and scene.row["prior_close"] == 7700.0 and scene.row["spot_from"] == "futures"
    assert scene.spot == round(7700.0 * 7781.0 / 7750.0, 2)
    assert scene.sigma == scene.row["sigma"] == 62.5                  # the median of the six anchors, 60 to 65 points
    assert premarket.pre_open_ruler(scene) == {"kind": "pre_open", "points": 62.5, "sessions": 6}
    assert list(scene.prior_bars) == list(reversed(PRIOR_DAYS)) and scene.prior_day[0] == PRIOR_DAYS[-1]
    assert scene.night and all(datetime.fromisoformat(r["ts"]) + timedelta(minutes=1) <= at(9, 28) for r in scene.night)


def test_the_scene_holds_the_prior_close_when_the_futures_are_stale(tmp_path):
    scene = make_premarket_scene(_station(tmp_path, now=at(9, 0)), at(9, 28))    # the newest /ES bar is 28 minutes old
    assert scene.row["spot_from"] == "prior_close" and scene.spot == 7700.0


def test_no_scene_without_enough_morning_anchors(tmp_path):
    with pytest.raises(NoPreOpenRead, match="no pre-open ruler"):
        make_premarket_scene(_station(tmp_path, anchors=4), at(9, 28))


def test_the_context_family_speaks_before_the_open(premarket_scene_factory):
    ls = build_context_labels(premarket_scene_factory(at(2, 35), []))
    assert ls.state["context"]["session_progress"] == "the session has not opened: it opens at 09:30 ET, in 6 hours and 55 minutes"
    assert ls.state["context"]["units"] == PREMARKET_UNITS and "futures' 16:00 price" in PREMARKET_UNITS
    assert ls.omitted == {"context.time_since_last_move": "before the open: the index has not traded today"}
    assert build_context_labels(premarket_scene_factory(at(9, 28), [])).state["context"]["session_progress"].endswith("in 2 minutes")
    assert build_context_labels(premarket_scene_factory(at(8, 30), [])).state["context"]["session_progress"].endswith("in 1 hour")


# ---- the reads -----------------------------------------------------------------------------------

def test_a_snapshot_read_writes_its_record_archive_and_card_and_asks_nothing(tmp_path, jev):
    state = _station(tmp_path, now=at(2, 35))
    out = PREMARKET.folder(state)
    c = run_checkpoint(state, out, DOC, True, at(2, 35), "02:35", save=False)
    assert jev["requests"] == [] and jev["sums"] == [] and jev["graded"] == []
    (read,) = _lines(out / f"{DAY}.jsonl")
    assert read["checkpoint"] == "02:35" and read["lane"] == "premarket" and read["sent"] is False and read["hour"] is None
    assert read["ruler"] == {"kind": "pre_open", "points": 62.5, "sessions": 6} and read["sigma"] == 62.5
    assert read["skipped"]["pm"]["pm_q"] == "not on its schedule at the 02:35 ET read"
    assert read["night"]["file"] == f"overnight/{DAY}.jsonl" and read["night"]["saved"] is None
    assert read["night"]["seen"]["/ES"] == {"1": 640, "5": 0, "last": at(2, 35).isoformat()}
    assert not (out / "hour").exists()
    (rec,) = _lines(state / "spx_jev" / "archive" / f"{DAY}.jsonl")
    assert rec["read_id"] == f"premarket:{at(2, 35).isoformat()}" and rec["lane"] == "premarket" and rec["checkpoint"] == "02:35"
    assert rec["schema_version"] == archive.SCHEMA_VERSION == 3 and rec["night"] == read["night"]
    assert rec["market_context"]["/ES"] == {"value": 7781.0, "known_at": at(2, 35).isoformat()}
    assert rec["cadence"] == {"from": None, "held": {}, "not_due": {"pm_q": "not on its schedule at the 02:35 ET read"}, "asked": []}
    assert json.loads((out / "latest.json").read_text()) == c
    assert c["sent"] is False and c["unsent_reason"] == "nothing to ask at the 02:35 ET read" and c["hour"] is None
    assert c["schedule"]["jev_reads"] == [at(8, 48).isoformat(), at(9, 28).isoformat()]
    assert [s["checkpoint"] for s in c["story"]] == ["02:35"] and c["situation"] == []
    assert [q["id"] for q in c["questions"]] == ["pm_q"] and c["dark"][0]["id"] == "pm_dark"
    assert not (state / "spx_jev" / "latest.json").exists()              # the live card is not the lane's


def test_a_jev_read_asks_what_is_due_sums_from_the_settled_open_and_grades(tmp_path, jev):
    state = _station(tmp_path)
    out = PREMARKET.folder(state)
    c = run_checkpoint(state, out, DOC, True, at(9, 28), "09:28", save=False)
    assert [list(r["questions"]) for r in jev["requests"]] == [["pm_q"]] and jev["graded"] == [PREMARKET]
    context = jev["sums"][0]["state"]["context"]
    assert "after the settled open" in context["horizon"] and "never from yesterday's close" in context["horizon"]
    assert "pre-open ruler" in context["units"] and set(jev["sums"][0]["questions"]) == {"open_10", "open_30"}
    (read,) = _lines(out / f"{DAY}.jsonl")
    assert read["sent"] is True and read["answers"]["pm"]["answers"]["pm_q"]["choice"] == "near"
    (s,) = _lines(out / "hour" / f"{DAY}.jsonl")
    assert s["lane"] == "premarket" and s["row_ts"] == at(9, 28).isoformat() and s["ruler"] == read["ruler"] and s["sigma"] == 62.5
    assert s["primary"] == "open_30" and set(s["by"]) == {"open_10", "open_30"} and s["fresh"] == {"pm_q": "near"}
    assert set(s["learn_exclude"]) == {"10", "30"}
    assert c["sent"] is True and "unsent_reason" not in c
    assert c["hour"]["read_at"] == at(9, 28).isoformat() and c["hour"]["primary"] == "open_30"
    assert c["story"][-1]["call"]["pick"] == c["hour"]["pick"]
    q = next(q for q in c["questions"] if q["id"] == "pm_q")
    assert q["answer"]["pick"] == "near"


def test_a_snapshot_after_a_call_carries_the_newest_call(tmp_path, jev):
    state = _station(tmp_path)
    out = PREMARKET.folder(state)
    run_checkpoint(state, out, DOC, True, at(8, 48), "08:48", save=False)
    c = run_checkpoint(state, out, DOC, True, at(9, 5), "09:05", save=False)
    assert len(jev["sums"]) == 1 and c["sent"] is False and c["hour"]["read_at"] == at(8, 48).isoformat()
    assert [(s["checkpoint"], bool(s["call"])) for s in c["story"]] == [("08:48", True), ("09:05", False)]


def test_a_read_without_a_ruler_writes_why_builds_nothing_and_asks_nothing(tmp_path, jev):
    state = _station(tmp_path, anchors=4)
    out = PREMARKET.folder(state)
    c = run_checkpoint(state, out, DOC, True, at(9, 28), "09:28", save=False)
    (read,) = _lines(out / f"{DAY}.jsonl")
    assert read["ruler"]["omitted"].startswith("no pre-open ruler") and read["state"] == {} and read["requests"] == []
    assert read["skipped"]["pm"]["pm_q"].startswith("no read before the open: no pre-open ruler") and jev["requests"] == []
    assert read["night"]["seen"]["/ES"]["1"] > 0
    assert c["ruler"] == read["ruler"] and c["unsent_reason"].startswith("nothing to ask at the 09:28 ET read: no pre-open ruler")


def test_every_time_on_the_card_is_a_full_timestamp(tmp_path, jev):
    state = _station(tmp_path)
    c = run_checkpoint(state, PREMARKET.folder(state), DOC, True, at(9, 28), "09:28", save=False)
    times = [c["generated_at"], c["row_ts"], c["checkpoint"], c["open"], c["start"], *c["marks"], c["handover"],
             *c["schedule"]["reads"], *c["schedule"]["jev_reads"], c["schedule"]["close_out"], c["hour"]["read_at"],
             *(s["at"] for s in c["story"])]
    assert all(datetime.fromisoformat(t).utcoffset() is not None for t in times)
    assert (c["open"], c["start"], c["marks"], c["handover"]) == (at(9, 30).isoformat(), at(9, 34).isoformat(),
                                                                  [at(9, 44).isoformat(), at(10, 4).isoformat()], at(9, 35).isoformat())
    assert c["checkpoint"] == at(9, 28).isoformat() and c["day"] == DAY and c["lane"] == "premarket"


def test_the_report_row_is_due_then_measured_and_names_its_releases(monkeypatch):
    claims = events.Event(at(8, 30), None, "JOBLESS_CLAIMS", events.PRE_OPEN)
    monkeypatch.setattr(story, "releases", lambda day: [claims])
    before = premarket.situation({"state": {}, "omitted": {}, "figures": {}}, at(8, 5))
    assert before == [{"key": "report", "path": premarket.REPORT, "title": "Report", "name": claims.words, "at": at(8, 30).isoformat(),
                       "verdict": "Due", "sentence": f"{claims.words} is due at 08:30 ET"}]
    record = {"state": {"overnight": {"release_reaction": "futures rose 0.20 sigma after the report", "es_move": "futures are up"}},
              "omitted": {}, "figures": {"overnight.release_reaction": {"kind": "rank", "value": 0.2, "cut": "top third", "verdict": "held"},
                                         "overnight.es_move": {"kind": "rank", "value": 0.4, "cut": "top third", "verdict": "big_up"}}}
    after = premarket.situation(record, at(8, 48))
    assert [(r["key"], r["verdict"]) for r in after] == [("futures", "Big rise"), ("report", "Held")]
    assert after[1]["sentence"] == "futures rose 0.20 of a normal day's move after the report" and "verdict" not in after[1]["figure"]
    missed = premarket.situation({"state": {}, "omitted": {premarket.REPORT: "no /ES price at 08:45"}, "figures": {}}, at(8, 48))
    assert missed[0]["verdict"] is None and missed[0]["sentence"].endswith("came out at 08:30 ET; no /ES price at 08:45")


def test_the_story_marks_the_first_read_after_the_report(tmp_path, jev, monkeypatch):
    monkeypatch.setattr(story, "releases", lambda day: [events.Event(at(8, 30), None, "CPI", events.PRE_OPEN)])
    state = _station(tmp_path)
    out = PREMARKET.folder(state)
    for hh, mm, cp in ((8, 5, "08:05"), (8, 48, "08:48"), (9, 5, "09:05")):
        c = run_checkpoint(state, out, DOC, False, at(hh, mm), cp, save=False)
    assert [(s["checkpoint"], s["report"]) for s in c["story"]] == [("08:05", False), ("08:48", True), ("09:05", False)]
    assert c["situation"][-1]["key"] == "report" and c["unsent_reason"] == service.UNSENT_DEFAULT


def test_a_missed_checkpoint_passes_the_report_mark_to_the_next_read(tmp_path, jev, monkeypatch):
    monkeypatch.setattr(story, "releases", lambda day: [events.Event(at(8, 30), None, "CPI", events.PRE_OPEN)])
    state = _station(tmp_path)
    out = PREMARKET.folder(state)
    for hh, mm, cp in ((8, 5, "08:05"), (9, 5, "09:05")):
        c = run_checkpoint(state, out, DOC, False, at(hh, mm), cp, save=False)
    assert [(s["checkpoint"], s["report"]) for s in c["story"]] == [("08:05", False), ("09:05", True)]


# ---- the command ---------------------------------------------------------------------------------

@pytest.fixture
def command(monkeypatch, jev, tmp_path_factory):
    """main() on a fixed clock with a key and DOC as its question file, the overnight save recorded rather than fetched."""
    saves = []
    questions = tmp_path_factory.mktemp("questions") / "questions.json"
    as_filed = json.loads(json.dumps(DOC))
    for q in as_filed["groups"][0]["questions"].values():
        q["lanes"] = ["premarket"]
        if "schedule" in q:
            q["schedule"] = {"premarket": q["schedule"]}
    questions.write_text(json.dumps(as_filed))
    monkeypatch.setattr(service, "load_env_file", lambda *a, **k: [])
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(overnight, "save_nights", lambda state_dir, days, now: saves.append((days, now)) or [
        {"added": 3, "failed": ["/BTC 1-min: RuntimeError: refused"]}])

    def run(now, *args):
        monkeypatch.setattr(service, "now_et", lambda: now)
        return premarket.main([*args, "--questions", str(questions)])
    run.saves = saves
    return run


def _written(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("hh, mm", [(7, 0), (9, 31)])
def test_a_late_fire_writes_nothing(tmp_path, command, hh, mm):
    state = _station(tmp_path)
    before = _written(state)
    assert command(at(hh, mm), "--state-dir", str(state), "--send") == 0
    assert _written(state) == before and command.saves == []


def test_a_fire_saves_the_night_reads_once_and_the_close_out_grades(tmp_path, command, jev):
    state = _station(tmp_path)
    out = PREMARKET.folder(state)
    assert command(at(9, 28, ss=4), "--state-dir", str(state), "--send") == 0
    assert command.saves == [([date.fromisoformat(DAY)], at(9, 28, ss=4))]
    (read,) = _lines(out / f"{DAY}.jsonl")
    assert read["night"]["saved"] == {"added": 3, "failed": ["/BTC 1-min: RuntimeError: refused"]} and len(jev["requests"]) == 1
    assert command(at(9, 29), "--state-dir", str(state), "--send") == 0          # the same checkpoint again
    assert len(_lines(out / f"{DAY}.jsonl")) == 1 and len(jev["requests"]) == 1
    assert command(at(10, 6, ss=20), "--state-dir", str(state), "--send") == 0
    c = json.loads((out / "latest.json").read_text())
    assert c["closed_out_at"] and c["tally"]["calls"] == 1 and jev["graded"] == [PREMARKET, PREMARKET]
    lines = _lines(state / "spx_jev" / "archive" / f"{DAY}.jsonl")
    assert [l["kind"] for l in lines] == ["read", "close_out"] and lines[1]["lane"] == "premarket"


def test_a_replay_saves_nothing_and_writes_only_where_it_is_told(tmp_path, command):
    state = _station(tmp_path / "station")
    before = _written(state)
    trial = tmp_path / "trial"
    assert command(at(12, 0), "--state-dir", str(state), "--day", DAY, "--at", "08:48", "--out-dir", str(trial)) == 0
    assert _written(state) == before and command.saves == []
    assert {f"{DAY}.jsonl", "latest.json", f"archive/{DAY}.jsonl"} <= set(_written(trial))
    with pytest.raises(SystemExit):
        command(at(12, 0), "--state-dir", str(state), "--day", DAY)


def test_build_reads_a_checkpoint_and_refuses_a_diary_scene(tmp_path, capsys):
    state = _station(tmp_path)
    assert build.main(["--state-dir", str(state), "--day", DAY, "--at", "09:28", "--lane", "premarket", "--compact"]) == 0
    pkg = json.loads(capsys.readouterr().out)
    assert pkg["row_ts"] == at(9, 28).isoformat() and pkg["ruler"]["kind"] == "pre_open" and pkg["bars_used"] == 0
    assert pkg["state"]["context"]["session_progress"].endswith("in 2 minutes")
    with pytest.raises(SystemExit):
        build.main(["--state-dir", str(state), "--lane", "premarket"])
