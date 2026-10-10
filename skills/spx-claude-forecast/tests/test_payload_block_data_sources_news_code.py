"""The data_sources, news and code blocks: every value from the saved files at the cut, every absence named."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from conftest import write_jsonl
from payload_fixtures import DAY, SPOT, at, fake_inputs, make_row, station_state_dir

from spx_claude_forecast import jsonl_store, station_stores
from spx_claude_forecast.paths import ensure_folders
from spx_claude_forecast.payload.blocks import code, news
from spx_claude_forecast.payload.blocks.code import build_code_block
from spx_claude_forecast.payload.blocks.data_sources import build_data_sources_block
from spx_claude_forecast.payload.blocks.news import build_news_block
from spx_claude_forecast.payload.frozen_inputs import load_frozen_inputs

READ_ID = "live:2026-10-09T14:30:12.458122-04:00"


def headlines_file(state_dir: Path, day: str) -> Path:
    return station_stores.spx_jev_dir(state_dir) / "headlines" / f"{day}.jsonl"


def headline(captured: datetime, title: str, feed: str = "google_news", source: str | None = None) -> dict:
    """One line as the station's poll writes it."""
    return {"captured_at": captured.isoformat(timespec="seconds"), "pub_claimed": None, "feed": feed, "source": source or feed,
            "title": title, "url": f"https://example.test/{abs(hash(title))}", "guid": None}


def fold(when: datetime, trades: int | None) -> dict:
    """One lob_flow line of the collector's record, with its 15-minute trade count."""
    snapshot = {"tilt": 0.1, "determinate_share": 0.5}
    if trades is not None:
        snapshot["tape_trades"] = trades
    return {"ts": when.isoformat(), "engine": "lob_flow", "snapshot": snapshot, "baseline_rows": []}


def absences(block) -> dict[str, str]:
    return {a["path"]: a["why"] for a in block.absent}


# --- data_sources ---------------------------------------------------------------------------------------

def test_data_sources_ages_each_feed_from_the_saved_files(tmp_path):
    cut = at(14, 30, 12)
    row = make_row(cut, gex_source="native", dex_views={"dex_above_spot": 0.6})
    inputs = fake_inputs(tmp_path, cut=cut, row=row, dated_book={"as_of": at(8, 17).isoformat(), "bands": []})
    inputs.load_notes.append("archive read: no line for this read_id")
    state_dir = inputs.state_dir
    write_jsonl(station_stores.context_file(state_dir, DAY), [
        {"ts": at(14, 28, 55).isoformat(), "quotes": {"$VIX": {"last": 14.9}}, "bars": {}, "failed": []},
        {"ts": at(14, 30, 0).isoformat(), "bars": {"$TICK": {"ts": at(14, 29).isoformat(), "close": 151.0}}},   # bars only: not a snapshot
        {"ts": at(14, 30, 5).isoformat(), "quotes": {"$VIX": {"last": 14.87}}, "bars": {}, "failed": ["$ADD: empty", "quotes: HTTPError"]},
        {"ts": at(14, 31, 15).isoformat(), "quotes": {"$VIX": {"last": 14.8}}, "bars": {}, "failed": []},        # after the cut
    ])
    write_jsonl(station_stores.lob_flow_agg_file(state_dir, DAY), [
        fold(at(14, 29, 27), 1500),
        {"ts": at(14, 29, 28).isoformat(), "engine": "spy_depth", "snapshot": {}, "baseline_rows": []},
        fold(at(14, 30, 30), 1600),
    ])
    write_jsonl(headlines_file(state_dir, DAY), [headline(at(14, 26, 25), "A story"), headline(at(14, 32, 0), "A later story")])

    block = build_data_sources_block(inputs)

    assert block.data["cut_at"] == "14:30:12"
    assert block.data["spx_bars"] == {"last_finished": "14:29", "age_s": 12}
    assert block.data["options_diary"] == {"row_at": "14:30:12", "age_s": 0, "book": "native", "coverage": "complete"}
    assert block.data["market_feed"] == {"snapshot_at": "14:30:05", "age_s": 7, "failed": ["$ADD", "quotes"]}
    assert block.data["tape"] == {"fold_at": "14:29:27", "age_s": 45, "content": "ok"}
    assert block.data["headlines"] == {"last_capture": "14:26:25", "age_s": 227}
    assert block.data["dated_book"] == {"as_of": "08:17", "age_h": 6.2}
    assert block.data["load_notes"] == ["archive read: no line for this read_id"]
    assert "schwab_login_days_left" not in block.data
    assert absences(block) == {"data_sources.schwab_login_days_left": "not_readable",
                               "data_sources.tape.trades_15m_vs_usual_x": "too_few_prior_sessions"}


def test_tape_trades_are_set_against_the_prior_sessions_median_at_the_same_clock(tmp_path):
    cut = at(14, 30, 12)
    prior_days = ["2026-10-08", "2026-10-07", "2026-10-06", "2026-10-05", "2026-10-02", "2026-10-01", "2026-09-30"]
    inputs = fake_inputs(tmp_path, cut=cut, prior_bars={d: [] for d in prior_days})
    agg = lambda day: station_stores.lob_flow_agg_file(inputs.state_dir, day)
    write_jsonl(agg(DAY), [fold(at(14, 29, 27), 1500)])
    for day, trades in zip(prior_days[:5], (1000, 2000, 3000, 4000, 5000)):
        write_jsonl(agg(day), [fold(at(14, 29, 50, day=day), trades), fold(at(14, 31, 0, day=day), 99_999)])   # the later fold is past the clock
    write_jsonl(agg("2026-10-01"), [fold(at(14, 29, 0, day="2026-10-01"), 0)])         # a dead feed is not a usual
    write_jsonl(agg("2026-09-30"), [fold(at(14, 20, 0, day="2026-09-30"), 7000)])      # older than the tape age limit at the clock

    block = build_data_sources_block(inputs)
    assert block.data["tape"]["trades_15m_vs_usual_x"] == 0.5                          # 1500 over the median 3000

    write_jsonl(agg("2026-10-02"), [])                                                  # four usable sessions are too few
    block = build_data_sources_block(inputs)
    assert "trades_15m_vs_usual_x" not in block.data["tape"]
    assert absences(block)["data_sources.tape.trades_15m_vs_usual_x"] == "too_few_prior_sessions"


def test_tape_content_is_stale_past_the_age_limit_and_empty_without_trades(tmp_path):
    cut = at(14, 30, 12)
    inputs = fake_inputs(tmp_path, cut=cut)
    agg = station_stores.lob_flow_agg_file(inputs.state_dir, DAY)
    write_jsonl(agg, [fold(at(14, 26, 0), 1500)])                                       # 4 minutes old: past the station's 3
    assert build_data_sources_block(inputs).data["tape"]["content"] == "stale"
    write_jsonl(agg, [fold(at(14, 29, 50), 0)])
    assert build_data_sources_block(inputs).data["tape"]["content"] == "empty"
    write_jsonl(agg, [fold(at(14, 29, 50), None)])
    block = build_data_sources_block(inputs)
    assert block.data["tape"]["content"] == "empty"
    assert absences(block)["data_sources.tape.trades_15m_vs_usual_x"] == "no_trade_count"


def test_a_stand_in_book_and_a_row_missing_a_view_are_said_so(tmp_path):
    row = make_row(at(14, 28, 50), gex_source="spy_proxy×10.0346")                      # no dex_views
    block = build_data_sources_block(fake_inputs(tmp_path, cut=at(14, 30, 12), row=row))
    assert block.data["options_diary"] == {"row_at": "14:28:50", "age_s": 82, "book": "stand_in", "coverage": "partial"}


def test_the_book_source_is_read_from_the_raw_diary_when_the_scene_row_lacks_it(tmp_path):
    cut = at(14, 30, 12)
    inputs = fake_inputs(tmp_path, cut=cut)                                              # make_row carries no gex_source
    write_jsonl(station_stores.reversion_rows_file(inputs.state_dir, DAY), [
        {"ts": at(14, 28, 50).isoformat(), "ticker": "SPX", "spot": SPOT, "gex_source": "spy_proxy×10.0346"},
        {"ts": cut.isoformat(), "ticker": "SPX", "spot": SPOT, "gex_source": "native"},
        {"ts": at(14, 31, 30).isoformat(), "ticker": "SPX", "spot": SPOT, "gex_source": "spy_proxy×10.0346"},
    ])
    assert build_data_sources_block(inputs).data["options_diary"]["book"] == "native"

    block = build_data_sources_block(fake_inputs(tmp_path / "no_diary", cut=cut))
    assert "book" not in block.data["options_diary"]
    assert absences(block)["data_sources.options_diary.book"] == "no_gex_source"


def test_missing_feeds_are_declared_not_guessed(tmp_path):
    inputs = fake_inputs(tmp_path, bars=[], dated_book=None)
    block = build_data_sources_block(inputs)
    assert set(block.data) == {"cut_at", "options_diary", "load_notes"}
    assert absences(block) == {
        "data_sources.spx_bars": "no_finished_bar", "data_sources.options_diary.book": "no_gex_source",
        "data_sources.market_feed": "no_snapshot_before_cut", "data_sources.tape": "no_fold_before_cut",
        "data_sources.headlines": "no_headline_feed", "data_sources.dated_book": "none_known_at_cut",
        "data_sources.schwab_login_days_left": "not_readable",
    }
    write_jsonl(headlines_file(inputs.state_dir, DAY), [headline(at(14, 32, 0), "A later story")])
    inputs.dated_book = {"bands": []}
    block = build_data_sources_block(inputs)
    assert absences(block)["data_sources.headlines"] == "no_capture_before_cut"
    assert absences(block)["data_sources.dated_book"] == "as_of_unreadable"


def test_load_notes_are_short_and_carry_no_day(tmp_path):
    inputs = fake_inputs(tmp_path)
    inputs.load_notes.append("events calendar: OSError: /state/calendar/2026-10-09.json " + "x" * 200)
    note = build_data_sources_block(inputs).data["load_notes"][0]
    assert "2026-10-09" not in note and "a day" in note and len(note) <= 120


# --- news -----------------------------------------------------------------------------------------------

def test_news_counts_the_hours_captures_movers_kinds_and_repeats(tmp_path):
    cut = at(14, 30, 12)
    inputs = fake_inputs(tmp_path, cut=cut)
    state_dir = inputs.state_dir
    write_jsonl(headlines_file(state_dir, "2026-10-08"), [headline(at(23, 0, day="2026-10-08"), "Old story from last night")])
    write_jsonl(headlines_file(state_dir, DAY), [
        headline(at(13, 0), "Morning story"),                                                       # before the hour
        headline(at(13, 40), "Old story from last night", feed="cnbc_top"),                         # yesterday's story again: dropped
        headline(at(13, 45), "Morning story - Reuters", source="Reuters"),                         # a copy under another outlet: dropped
        headline(at(14, 10, 12), "Treasury yields climb after the auction", feed="marketwatch_top"),   # mover: yields
        headline(at(14, 20), "Fed's Powell sees cuts ahead"),                                       # Google News is not a preferred feed
        headline(at(14, 23, 12), "Powell: the Fed will wait", feed="cnbc_top"),                     # mover: fed
        headline(at(14, 25), "Nvidia lifts its guidance", feed="cnbc_top"),                         # mover: a heavyweight's results
        headline(at(14, 29), "A quiet local story"),
        headline(at(14, 31), "After the cut", feed="cnbc_top"),
    ])
    block = build_news_block(inputs)
    assert block.data == {"titles": "withheld", "captured_60m": 7, "index_mover_items_60m": 3,
                          "kinds": ["fed", "megacap_results", "yields"], "newest_index_mover_min_ago": 5, "stale_dropped": 2}
    assert block.absent == []
    assert "Powell" not in json.dumps(block.data)


def test_news_is_absent_before_the_feed_existed_and_quiet_after(tmp_path):
    inputs = fake_inputs(tmp_path)
    block = build_news_block(inputs)
    assert block.is_empty and block.absent == [{"path": "news", "why": "no_headline_feed"}]
    write_jsonl(headlines_file(inputs.state_dir, "2026-10-12"), [headline(at(9, 0, day="2026-10-12"), "Later")])   # first file after the day
    assert build_news_block(inputs).absent == [{"path": "news", "why": "no_headline_feed"}]
    write_jsonl(headlines_file(inputs.state_dir, "2026-10-07"), [headline(at(9, 0, day="2026-10-07"), "Earlier")])
    block = build_news_block(inputs)
    assert block.data == {"titles": "withheld", "captured_60m": 0, "index_mover_items_60m": 0, "kinds": [], "stale_dropped": 0}
    assert block.absent == [{"path": "news.newest_index_mover_min_ago", "why": "no_index_mover_in_60m"}]


def _alternatives(pattern: str) -> list[str]:
    """Sample phrases, one per alternative of the station's flat index-mover pattern."""
    body = pattern.removeprefix(r"\b(").removesuffix(r")\b")
    parts, depth, current = [], 0, ""
    for ch in body:
        depth += (ch == "(") - (ch == ")")
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    samples = []
    for part in parts:
        group = re.search(r"\(\?:([^)]*)\)", part)
        variants = [part[:group.start()] + option + part[group.end():] for option in group.group(1).split("|")] if group else [part]
        for variant in variants:
            samples += [variant[:-1], variant[:-2]] if variant.endswith("?") else [variant]
    return samples


def test_the_kinds_name_every_alternative_of_the_stations_index_mover_pattern():
    judgment = station_stores.import_spx_jev("judgment")
    samples = _alternatives(judgment.INDEX_NEWS.pattern)
    assert len(samples) >= 16
    for sample in samples:
        assert judgment.INDEX_NEWS.search(sample), sample
        assert [kind for kind, pattern in news.KIND_PATTERNS.items() if pattern.search(sample)] != [], sample
    for kind, words in news.KIND_WORDS.items():
        for word in words:
            assert judgment.INDEX_NEWS.search(word), (kind, word)
    assert news.kinds_of({"title": "Nvidia raises guidance", "feed": "cnbc_top"}) == ["megacap_results"]
    assert news.kinds_of({"title": "Fed holds as Treasury yields slip", "feed": "cnbc_top"}) == ["fed", "yields"]
    assert news.kinds_of({"title": "Something the station flags that this module does not know"}) == ["other"]


# --- code -----------------------------------------------------------------------------------------------

def _catalog() -> tuple[dict, ...]:
    return station_stores.import_spx_jev("mirai_prediction.code_features").load_catalog()


def test_code_keeps_the_uncovered_groups_in_catalog_order_capped_at_twelve(tmp_path):
    catalog = _catalog()
    answers = {q["id"]: q["options"][0] for q in catalog}
    block = build_code_block(fake_inputs(tmp_path, code_answers=answers))
    kept = [q for q in catalog if q["group"] in code.CODE_GROUPS][:code.MAX_CODE_ANSWERS]
    assert len(block.data) == 12
    assert block.data == {q["id"]: q["options"][0] for q in kept}
    assert list(block.data) == [q["id"] for q in kept]
    assert block.absent == []


def test_code_leaves_out_nulls_unknown_qids_and_words_off_the_catalog(tmp_path):
    answers = {"TREND-05": None, "TREND-06": "reversed", "NOPE-01": "x", "TREND-10": "not a word", "TREND-02": "flat"}
    block = build_code_block(fake_inputs(tmp_path, code_answers=answers))
    assert block.data == {"TREND-06": "reversed"}                                        # TREND-02's group is covered by other blocks
    assert block.absent == [{"path": "code.NOPE-01", "why": "qid_not_in_catalog"},
                            {"path": "code.TREND-10", "why": "answer_not_in_catalog_options"}]


def test_code_is_absent_without_answers_and_without_uncovered_ones(tmp_path):
    for answers in ({}, {"TREND-05": None}):
        block = build_code_block(fake_inputs(tmp_path, code_answers=answers))
        assert block.is_empty and block.absent == [{"path": "code", "why": "no_code_answers"}]
    block = build_code_block(fake_inputs(tmp_path, code_answers={"TREND-02": "flat"}))
    assert block.is_empty and block.absent == [{"path": "code", "why": "no_uncovered_code_answers"}]


def test_the_group_lists_place_every_catalog_group_exactly_once():
    groups = {q["group"] for q in _catalog()}
    assert groups == code.CODE_GROUPS | set(code.COVERED_GROUPS)
    assert not code.CODE_GROUPS & set(code.COVERED_GROUPS)


def test_an_answer_matching_the_catalogs_usual_answer_yields_its_slot(tmp_path, monkeypatch):
    code_features = station_stores.import_spx_jev("mirai_prediction.code_features")
    synthetic = tuple({"id": f"T-{i:02d}", "group": "trend/leg_aging", "options": ["a", "b"], code.USUAL_ANSWER_KEY: "a"} for i in range(14))
    monkeypatch.setattr(code_features, "load_catalog", lambda: synthetic)
    answers = {q["id"]: ("b" if q["id"] == "T-13" else "a") for q in synthetic}        # only the last says something new
    block = build_code_block(fake_inputs(tmp_path, code_answers=answers))
    assert len(block.data) == 12 and block.data["T-13"] == "b"
    assert list(block.data) == sorted(block.data)                                      # catalog order on the way out
    assert "T-11" not in block.data and "T-12" not in block.data


# --- the real store -------------------------------------------------------------------------------------

@pytest.mark.skipif(station_state_dir() is None, reason="needs the station's state")
def test_the_real_read_builds_all_three_blocks_without_a_leak():
    state_dir = station_state_dir()
    inputs = load_frozen_inputs(state_dir, ensure_folders(state_dir), READ_ID)
    sources, news_block, code_block = build_data_sources_block(inputs), build_news_block(inputs), build_code_block(inputs)

    assert sources.data["cut_at"] == "14:30:12"
    assert sources.data["spx_bars"] == {"last_finished": "14:29", "age_s": 12}
    assert sources.data["options_diary"]["age_s"] == 0 and sources.data["options_diary"]["book"] in ("native", "stand_in")
    assert sources.data["options_diary"]["coverage"] in ("complete", "partial")
    assert set(sources.data["market_feed"]) == {"snapshot_at", "age_s", "failed"}
    assert sources.data["tape"]["content"] in ("ok", "stale", "empty")
    assert isinstance(sources.data["tape"]["trades_15m_vs_usual_x"], float)
    assert set(sources.data["headlines"]) == {"last_capture", "age_s"}
    assert isinstance(sources.data["load_notes"], list)
    assert absences(sources)["data_sources.schwab_login_days_left"] == "not_readable"
    assert news_block.data["titles"] == "withheld" and news_block.data["captured_60m"] > 0
    assert news_block.data["stale_dropped"] >= 0 and isinstance(news_block.data["kinds"], list)
    assert 0 < len(code_block.data) <= code.MAX_CODE_ANSWERS
    assert all(re.fullmatch(r"[A-Z]+-\d{2}(-[A-Z]+)?", qid) for qid in code_block.data)

    text = json.dumps([sources.data, sources.absent, news_block.data, news_block.absent, code_block.data, code_block.absent])
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)                                   # no date
    assert not re.search(r"(?<![\d.:])\d{4,}(?![\d.:])", text)                           # no whole number >= 1000 (a price level)
    titles = [line["title"] for line in jsonl_store.iter_json_lines(headlines_file(state_dir, inputs.day))]
    assert titles and not any(title in text for title in titles)                        # no headline title
