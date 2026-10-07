"""The code feature builder: the catalog, the label parsers against the mapping's real archive sentences (tests/data/
ready_mapping.json), the bars questions on synthetic bars, silence on unknown templates, the matrix's code columns, the
raw-first write in the hook, and the backfill. No host state."""
from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import PRIOR_DAYS, at, bars_from_closes

from spx_jev.mirai_prediction import answer_matrix, code_feature_inputs, paths, read_hook
from spx_jev.mirai_prediction.answer_matrix import Row, build_answer_matrix
from spx_jev.mirai_prediction.backfill_code_features import run_backfill
from spx_jev.mirai_prediction.code_features import (BARS_ANSWERERS, LABEL_PARSERS, PREMARKET_QUESTIONS, MarketHistory, answer_code_features,
                                                    bars_up_to, load_catalog)
from spx_jev.mirai_prediction.code_features_market import MARKET_ANSWERERS
from spx_jev.mirai_prediction.voice_fits import DayFits

MAPPING = [m for name in ("ready_mapping.json", "phase2_mapping.json")
           for m in json.loads((Path(__file__).parent / "data" / name).read_text(encoding="utf-8"))]
CATALOG = {q["id"]: q for q in load_catalog()}
DAY = "2026-09-18"
SIGMA = 75.0


def nested(labels: dict[str, str]) -> dict:
    out: dict = {}
    for key, sentence in labels.items():
        group, name = key.split(".", 1)
        out.setdefault(group, {})[name] = sentence
    return out


def record(row_ts: str, labels: dict[str, str] | None = None, **more) -> dict:
    return {"read_id": f"live:{row_ts}", "lane": "live", "row_ts": row_ts, "sigma": SIGMA, "labels": nested(labels or {}), **more}


# ---------------------------------------------------------------- the catalog

def test_catalog_has_the_90_questions_with_valid_layers_groups_and_options():
    rows = load_catalog()
    assert len(rows) == 90 and len({q["id"] for q in rows}) == 90
    for q in rows:
        assert q["layer"] in (1, 2) and isinstance(q["group"], str) and "/" in q["group"]
        assert q["options"] and len(set(q["options"])) == len(q["options"]) and all(isinstance(o, str) and o for o in q["options"])
        assert q["method"] in ("label", "bars", "market_value") and q["title"] and q["notes"]
        registered = sum((q["id"] in LABEL_PARSERS, q["id"] in BARS_ANSWERERS, q["id"] in MARKET_ANSWERERS))
        if q.get("status") == "needs_new_feed":
            assert registered == 0 and "needs_new_feed" in q["notes"]             # in the catalog, silent until its feed exists
        else:
            assert registered == 1
        if q["method"] == "label":
            assert q["label_keys"]
    assert {q["id"] for q in rows if q.get("status") == "needs_new_feed"} == {"OPTIONS-12", "FLOW-09"}
    assert {q["new_id"] for q in MAPPING} == set(CATALOG)


# ---------------------------------------------------------------- the label parsers on the mapping's real sentences

# the prototype's three out-of-option strings and what the builder answers instead
EXPECTED_INSTEAD = {"inside (old 09-28 template, side not stated)": None, "flat (old template, from rank)": "flat",
                    "ahead down (contrarian: lean up)": "ahead down", "ahead up (contrarian: lean down)": "ahead up"}
LABEL_EXAMPLES = [(m["new_id"], ex) for m in MAPPING if m["method"] == "label" for ex in m["examples"]]


@pytest.mark.parametrize("qid,example", LABEL_EXAMPLES, ids=[f"{q}:{e['read']}" for q, e in LABEL_EXAMPLES])
def test_each_label_parser_reproduces_the_archived_example(qid, example):
    expected = EXPECTED_INSTEAD.get(example["answer"], example["answer"])
    rec = record(example["row_ts"], example["labels"])
    history = MarketHistory(premarket_reads=[{**rec, "lane": "premarket"}]) if qid in PREMARKET_QUESTIONS else MarketHistory()
    answers = answer_code_features(rec, [], history)
    assert answers[qid] == expected
    assert expected is None or expected in CATALOG[qid]["options"]


def test_premarket_questions_take_the_days_last_premarket_read_before_the_read():
    night = {"overnight.range_vs_normal": "the night's range is in the top third of prior nights", "premarket.legs": "x so far. net move is quiet"}
    earlier = {**record(f"{DAY}T08:05:00-04:00", {"overnight.range_vs_normal": "bottom third"}), "lane": "premarket"}
    latest = {**record(f"{DAY}T09:28:00-04:00", night), "lane": "premarket"}
    later = {**record(f"{DAY}T12:00:00-04:00", {"overnight.range_vs_normal": "middle third"}), "lane": "premarket"}
    answers = answer_code_features(record(f"{DAY}T10:02:00-04:00"), [], MarketHistory(premarket_reads=[earlier, latest, later]))
    assert answers["LEVELS-08"] == "large" and answers["MACRO-09"] == "night quiet" and answers["MACRO-10"] is None
    assert answer_code_features(record(f"{DAY}T10:02:00-04:00"), [], MarketHistory())["LEVELS-08"] is None


# ---------------------------------------------------------------- silence, never an error

CALENDAR_QUESTIONS = ("EVENTS-01", "EVENTS-02", "EVENTS-03", "EVENTS-05")      # read the skill's own calendar, which covers DAY


def silent_values(answers: dict) -> set:
    """Every answer but the calendar questions', which answer from calendar/events.json even with no other data."""
    assert all(answers[q] is None or answers[q] in CATALOG[q]["options"] for q in CALENDAR_QUESTIONS)
    return {v for q, v in answers.items() if q not in CALENDAR_QUESTIONS}


def test_unknown_templates_and_missing_data_answer_none_without_raising():
    keys = {k for q in load_catalog() for k in q["label_keys"]}
    garbage = record(f"{DAY}T11:00:00-04:00", {k: "lorem ipsum dolor sit amet" for k in keys})
    assert silent_values(answer_code_features(garbage, [], MarketHistory())) == {None}
    assert silent_values(answer_code_features({"row_ts": f"{DAY}T11:00:00-04:00"}, [], None)) == {None}
    broken_bars = [{"ts": f"{DAY}T09:30:00-04:00"}] * 100
    assert silent_values(answer_code_features({"row_ts": f"{DAY}T11:00:00-04:00", "sigma": "?"}, broken_bars, MarketHistory())) == {None}
    assert set(answer_code_features(record(f"{DAY}T11:00:00-04:00"), [], MarketHistory()).keys()) == set(CATALOG)


# ---------------------------------------------------------------- the bars questions on synthetic bars

def closes(n: int, start: float = 7700.0, step: float = 0.0, noise: float = 0.0, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        p += step + rng.uniform(-noise, noise)
        out.append(p)
    return out


def prior_bars(days=PRIOR_DAYS, n: int = 390, noise: float = 1.0, step: float = 0.0) -> dict[str, list[dict]]:
    """Prior sessions that wander: every minute's measures have a spread to rank against."""
    return {d: bars_from_closes(closes(n, noise=noise, step=step, seed=i + 1), day=d) for i, d in enumerate(days)}


def ask(qid: str, today: list[dict], hhmm: str = "11:30", history: MarketHistory | None = None, **more):
    row_ts = f"{DAY}T{hhmm}:30-04:00"
    rec = record(row_ts, **more)
    return answer_code_features(rec, bars_up_to(today, row_ts), history or MarketHistory(spx_bars_by_day=prior_bars()))[qid]


def test_bars_up_to_keeps_only_finished_minutes():
    bars = bars_from_closes([1.0] * 5, day=DAY)
    assert len(bars_up_to(bars, f"{DAY}T09:33:00-04:00")) == 3 and len(bars_up_to(bars, f"{DAY}T09:32:59-04:00")) == 2


def test_trend_10_break_held_failed_or_none():
    base = [7700.0 + (i % 5) * 0.5 for i in range(110)]                                       # a 7700-7702 band
    assert ask("TREND-10", bars_from_closes(base + [7701.0] * 10, wick=0.1)) == "none"
    assert ask("TREND-10", bars_from_closes(base + [7720.0] * 10, wick=0.1)) == "up held"
    assert ask("TREND-10", bars_from_closes(base + [7720.0] * 5 + [7690.0] * 5, wick=0.1)) == "up failed"
    assert ask("TREND-10", bars_from_closes(base + [7680.0] * 10, wick=0.1)) == "down held"
    assert ask("TREND-10", bars_from_closes(base[:50], wick=0.1)) is None                       # under 70 bars
    assert ask("TREND-10", bars_from_closes(base + [7720.0] * 10, wick=0.1), sigma=None) is None


def test_trend_05_one_way_hour_against_the_same_minute():
    assert ask("TREND-05", bars_from_closes(closes(120, step=0.5))) == "one-way up"
    assert ask("TREND-05", bars_from_closes(closes(120, step=-0.5))) == "one-way down"
    assert ask("TREND-05", bars_from_closes([7700.0] * 120)) == "no hour move"
    zigzag = [7700.0 + (12.0 if i % 10 < 5 else 0.0) for i in range(115)] + [7703.0 + 3.0 * i for i in range(5)]
    assert ask("TREND-05", bars_from_closes(zigzag)) == "two-way up"
    assert ask("TREND-05", bars_from_closes(closes(120, step=0.5)), history=MarketHistory()) is None   # no history to rank against


def two_phase(last_step: float, day: str = DAY) -> list[dict]:
    """Rises 1 point a minute to 11:19, then ``last_step`` a minute: the pace ratio at 11:30 is exactly ``last_step``."""
    return bars_from_closes([7700.0 + i for i in range(110)] + [7809.0 + last_step * i for i in range(1, 11)], day=day)


def test_trend_08_reversing_accelerating_fading():
    history = MarketHistory(spx_bars_by_day={d: two_phase(0.2 * (i + 1), d) for i, d in enumerate(PRIOR_DAYS)})   # ratios 0.2 .. 2.0
    assert ask("TREND-08", two_phase(-0.8), history=history) == "reversing"
    assert ask("TREND-08", two_phase(-0.01), history=history) == "fading"                   # opposite but within the tolerance: ranked
    assert ask("TREND-08", two_phase(3.0), history=history) == "accelerating"
    assert ask("TREND-08", two_phase(1.0), history=history) == "steady"
    assert ask("TREND-08", two_phase(0.001), history=history) == "fading"


def test_trend_09_leg_and_pullback():
    assert ask("TREND-09", bars_from_closes([7700.0] * 120)) == "no leg"
    straight = closes(120, step=0.5)
    assert ask("TREND-09", bars_from_closes(straight)) == "up leg shallow-slow"
    fast_pullback = [7700.0] * 60 + [7700.0 + 0.8 * i for i in range(1, 51)] + [7740.0 - 1.6 * i for i in range(1, 11)]
    assert ask("TREND-09", bars_from_closes(fast_pullback)) == "up leg deep-or-fast"
    assert ask("TREND-09", bars_from_closes(closes(120, step=-0.5))) == "down leg shallow-slow"


def test_trend_12_burst_when_one_minute_dwarfs_the_median():
    calm = bars_from_closes([7700.0] * 120, wick=0.5)
    assert ask("TREND-12", calm) == "normal"
    spike = bars_from_closes([7700.0] * 119 + [7701.0], wick=0.5)
    spike[-1]["high"], spike[-1]["low"] = 7720.0, 7690.0
    assert ask("TREND-12", spike) == "burst"


def test_levels_09_first_bar_then_opening_range():
    history = MarketHistory(spx_bars_by_day=prior_bars(noise=0.5))
    wide = bars_from_closes([7700.0, 7720.0, 7690.0, 7725.0, 7700.0] + [7700.0] * 60)
    narrow = bars_from_closes([7700.0] * 65, wick=0.05)
    assert ask("LEVELS-09", wide, "09:40", history) == "very busy" and ask("LEVELS-09", narrow, "09:40", history) == "quiet"
    assert ask("LEVELS-09", wide, "09:33", history) is None                                    # the first five minutes are not over
    wide_later = bars_from_closes([7700.0] * 5 + [7700.0 + (i % 2) * 30 for i in range(60)])
    assert ask("LEVELS-09", wide_later, "10:30", history) == "wide" and ask("LEVELS-09", narrow, "10:30", history) == "narrow"


def test_flow_08_drive_test_then_drive_rotation():
    prior = prior_bars()
    prior_high = max(b["high"] for b in prior[PRIOR_DAYS[0]])
    drive = bars_from_closes([7700.0 + 1.0 * i for i in range(40)])
    assert ask("FLOW-08", drive, "10:02", MarketHistory(spx_bars_by_day=prior)) == "drive"
    assert ask("FLOW-08", drive, "09:50", MarketHistory(spx_bars_by_day=prior)) is None            # fixed from 10:00
    tested = bars_from_closes([7700.0, 7702.0, prior_high - 0.5, prior_high + 0.5] + [prior_high - 1.0 * i for i in range(1, 37)])
    assert ask("FLOW-08", tested, "10:02", MarketHistory(spx_bars_by_day=prior)) == "test-then-drive"
    chop = bars_from_closes([7700.0 + (i % 4) * 5 for i in range(40)])                      # ends mid-range
    assert ask("FLOW-08", chop, "10:02", MarketHistory(spx_bars_by_day=prior)) == "rotation"


def night(day: str, asia_range: float, late_range: float) -> list[dict]:
    """A night's /ES bars: a 18:00-02:59 stretch spanning ``asia_range`` then a 03:00-09:29 stretch spanning ``late_range``."""
    start = at(18, 0, day) - timedelta(days=1)
    out = []
    for i in range(15 * 60 + 30):
        t = start + timedelta(minutes=i)
        span = asia_range if t.date() < at(0, 0, day).date() or t.strftime("%H:%M") < "03:00" else late_range
        price = 7700.0 + span * ((i % 60) / 59)
        out.append({"ts": t.isoformat(), "open": price, "high": price + 0.25, "low": price - 0.25, "close": price})
    return out


def test_macro_08_late_or_asia_against_prior_nights():
    prior = {d: night(d, asia_range=10.0, late_range=10.0) for d in PRIOR_DAYS}
    late = MarketHistory(es_night_bars_by_day={**prior, DAY: night(DAY, asia_range=2.0, late_range=20.0)})
    asia = MarketHistory(es_night_bars_by_day={**prior, DAY: night(DAY, asia_range=20.0, late_range=2.0)})
    assert ask("MACRO-08", [], "10:00", late) == "mostly late (after 03:00)"
    assert ask("MACRO-08", [], "10:00", asia) == "mostly Asia"
    assert ask("MACRO-08", [], "10:00", MarketHistory(es_night_bars_by_day={DAY: night(DAY, 2.0, 20.0)})) is None


def test_breadth_11_signed_add_against_prior_mornings():
    days = PRIOR_DAYS + ("2026-09-02", "2026-09-01")                 # the oldest session has no prior close for its gap side
    prior = prior_bars(days=days, n=390)
    adds = {d: [{"ts": b["ts"], "value": 100.0 * (i + 1) * (1 if i % 2 else -1)} for b in prior[d]] for i, d in enumerate(days)}
    gap_up = {"gap.size": "this morning's gap of 0.20 sigma is larger than 6 of the last 20 days' gaps: price opened above yesterday's close"}
    history = MarketHistory(spx_bars_by_day=prior, add_by_day=adds)
    strong = ask("BREADTH-11", [], "10:00", history, labels=gap_up, market_context={"$ADVN": {"value": 2500.0}, "$DECN": {"value": 300.0}})
    weak = ask("BREADTH-11", [], "10:00", history, labels=gap_up, market_context={"$ADVN": {"value": 300.0}, "$DECN": {"value": 2500.0}})
    assert strong == "more than usual" and weak == "less than usual"
    assert ask("BREADTH-11", [], "10:00", history, labels=gap_up) is None                       # no breadth in the context
    assert ask("BREADTH-11", [], "10:00", history, market_context={"$ADD": {"value": 500.0}}) is None   # no gap side


# ---------------------------------------------------------------- the raw record and the matrix

def test_record_code_features_writes_once_and_rereads_the_stored_line(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(code_feature_inputs, "load_market_history", lambda *a: MarketHistory())
    rec = record(f"{DAY}T11:00:00-04:00", {"levels.break_armed": "a break upward is armed, 0.3 sigma above"})
    first = code_feature_inputs.record_code_features(root, tmp_path, "live", DAY, rec, "live")
    again = code_feature_inputs.record_code_features(root, tmp_path, "live", DAY, {**rec, "labels": {}}, "live")
    lines = paths.read_json_lines(paths.raw_code_features_file(root, DAY))
    assert first["LEVELS-11"] == "armed up" and again == first and len(lines) == 1
    assert lines[0]["read_id"] == rec["read_id"] and lines[0]["source"] == "live" and lines[0]["day"] == DAY and lines[0]["created_at"]


def base_row(read_id: str, day: str) -> Row:
    return Row(read_id=read_id, row_ts=f"{day}T11:00:00-04:00", day=day, sum_id="average_30", historical_odds_probs={"up": 0.2, "flat": 0.6, "down": 0.2},
               jev_own_probs=None, shown_probs=None, pool_v1_probs=None, outcome="flat", learn_exclude=False)


def test_matrix_takes_the_code_columns_and_leaves_a_read_without_a_line_silent(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(answer_matrix, "load_base_rows", lambda *a: [base_row("live:a", "2026-09-16"), base_row("live:b", "2026-09-17")])
    monkeypatch.setattr(answer_matrix, "load_jev_answers", lambda *a: ({"live:a": {"q1": "yes"}}, {"q1": "g"}))
    paths.append_json_line(paths.raw_code_features_file(root, "2026-09-16"), {"read_id": "live:a", "lane": "live", "answers": {"TREND-01": "big up"}})
    paths.append_json_line(paths.raw_code_features_file(root, "2026-09-18"), {"read_id": "live:b", "lane": "live", "answers": {"TREND-01": "flat"}})
    m = build_answer_matrix(tmp_path, "live", "average_30", before_day="2026-09-18")
    assert {c for c in m.columns if c.startswith("code:")} == {f"code:{q}" for q in CATALOG}
    assert not any(c.startswith(("level:", "fact:")) for c in m.columns) and m.columns["jev:q1"]["layer"] == 3
    a, b = m.rows
    assert a.answers["code:TREND-01"] == "big up" and a.answers["jev:q1"] == "yes" and a.answers["code:LEVELS-11"] is None
    assert b.answers["code:TREND-01"] is None and b.answers["jev:q1"] is None            # its line is on a day not before 09-18
    assert set(a.answers) == set(b.answers) == set(m.columns)


def test_the_hook_records_the_code_features_before_forecasting_and_never_raises(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")
    monkeypatch.setattr(read_hook, "data_root", lambda s: root)
    monkeypatch.setattr(code_feature_inputs, "load_market_history", lambda *a: MarketHistory())
    empty = answer_matrix.AnswerMatrix(lane="live", sum_id="average_30", built_for_day=DAY, rows=[], columns={})
    monkeypatch.setattr(read_hook, "day_fits_for", lambda *a: DayFits(fits={}, matrix=empty, fit_day=None))

    def boom(*a, **k):
        raise RuntimeError("the forecast step broke")
    monkeypatch.setattr(read_hook, "todays_rows", boom)
    rec = record(f"{DAY}T11:00:00-04:00", {"levels.break_armed": "no break is armed, and none expired"})
    with pytest.raises(RuntimeError):
        read_hook.forecast_read(tmp_path, "live", DAY, rec["read_id"], read_record=rec, hour_record={})
    lines = paths.read_json_lines(paths.raw_code_features_file(root, DAY))
    assert len(lines) == 1 and lines[0]["answers"]["LEVELS-11"] == "nothing armed"
    monkeypatch.setattr(read_hook, "load_archive_read", lambda *a: rec)
    result = read_hook.after_read(tmp_path, "live", rec["read_id"])                          # the detached hook: logged, not raised
    assert result["written"] == 0 and "error" in result


def test_a_code_feature_failure_is_logged_and_the_read_goes_on(tmp_path, monkeypatch):
    root = paths.ensure_folders(tmp_path / "spx_jev" / "mirai_prediction")

    def boom(*a, **k):
        raise OSError("no bars")
    monkeypatch.setattr(read_hook, "record_code_features", boom)
    assert read_hook.code_features_for_read(root, tmp_path, "live", DAY, record(f"{DAY}T11:00:00-04:00"), "live", None) == {}
    log = paths.read_json_lines(paths.job_run_log_file(root))
    assert log[-1]["job_name"] == "code_features" and log[-1]["ok"] is False and "OSError" in log[-1]["error"]


def test_backfill_writes_every_archived_live_read_once(tmp_path):
    archive = tmp_path / "spx_jev" / "archive"
    archive.mkdir(parents=True)
    reads = [record(f"{DAY}T10:02:00-04:00", {"levels.break_armed": "a break downward is armed"}, kind="read"),
             record(f"{DAY}T10:32:00-04:00", {"calendar.expiry_phase": "today is inside the 4-day opex-week window"}, kind="read"),
             {**record(f"{DAY}T09:28:00-04:00", {"premarket.legs": "x so far. net move is quiet"}, kind="read"), "lane": "premarket"}]
    (archive / f"{DAY}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reads), encoding="utf-8")
    report = run_backfill(tmp_path, "live", DAY, DAY)
    assert report["days"] == {DAY: {"reads": 2, "written": 2, "skipped": 0}}
    assert report["questions"]["LEVELS-11"] == {"answered": 1, "none": 1} and report["questions"]["SESSION-01"] == {"answered": 1, "none": 1}
    assert report["questions"]["MACRO-09"] == {"answered": 2, "none": 0}                     # the premarket read serves both live reads
    root = tmp_path / "spx_jev" / "mirai_prediction"
    lines = paths.read_json_lines(paths.raw_code_features_file(root, DAY))
    assert [l["read_id"] for l in lines] == [reads[0]["read_id"], reads[1]["read_id"]] and all(l["source"] == "backfill" for l in lines)
    assert run_backfill(tmp_path, "live", DAY, DAY)["days"][DAY] == {"reads": 2, "written": 0, "skipped": 2}
    assert len(paths.read_json_lines(paths.raw_code_features_file(root, DAY))) == 2            # idempotent


def test_a_malformed_bar_line_is_skipped_and_the_rest_still_load(tmp_path):
    from spx_jev.mirai_prediction import code_feature_inputs as inputs
    folder = tmp_path / inputs.LIVE_BARS_SUBDIR
    folder.mkdir(parents=True)
    good = {"ts": "2026-10-06T09:31:00-04:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    bad_ts = {"ts": "not a time", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    missing = {"ts": "2026-10-06T09:32:00-04:00", "open": 1.0}
    (folder / "2026-10-06.jsonl").write_text("\n".join(json.dumps(b) for b in (bad_ts, good, missing)) + "\n")
    bars = inputs.load_day_bars(tmp_path, "2026-10-06")
    assert [b["close"] for b in bars] == [1.5]


def test_market_history_loads_each_part_on_its_own(tmp_path, monkeypatch):
    from spx_jev.mirai_prediction import code_feature_inputs as inputs

    def boom(*a, **k):
        raise RuntimeError("the store cannot be read")
    monkeypatch.setattr(inputs, "load_spx_bars_by_day", boom)
    monkeypatch.setattr(inputs, "load_add_by_day", lambda *a, **k: {"2026-10-05": [{"ts": "t", "value": 1.0}]})
    history = inputs.load_market_history(tmp_path, "live", "2026-10-06")
    assert history.spx_bars_by_day == {} and history.add_by_day == {"2026-10-05": [{"ts": "t", "value": 1.0}]}
