"""The data_sources, news and code blocks, and the loader fields they read: every value from the saved files at the
cut, every absence named."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from payload_fixtures import (DAY, SPOT, absent_reasons, assert_no_leak, at, context_line, fake_inputs, make_row, prior_days,
                              tape_fold_line, write_jsonl, write_tape_folds)

from spx_claude_forecast import jsonl_store, station_stores
from spx_claude_forecast.payload.blocks import code, news
from spx_claude_forecast.payload.blocks.code import build_code_block
from spx_claude_forecast.payload.blocks.data_sources import build_data_sources_block
from spx_claude_forecast.payload.blocks.news import build_news_block
from spx_claude_forecast.payload.frozen_inputs import TapeFold, load_market_snapshot, load_raw_diary_rows, load_tape_folds, options_book_of

CUT = at(14, 30, 12)


def headlines_file(state_dir: Path, day: str) -> Path:
    return station_stores.spx_jev_dir(state_dir) / "headlines" / f"{day}.jsonl"


def headline(captured: datetime, title: str, feed: str = "google_news", source: str | None = None) -> dict:
    """One line as the station's poll writes it."""
    return {"captured_at": captured.isoformat(timespec="seconds"), "pub_claimed": None, "feed": feed, "source": source or feed,
            "title": title, "url": f"https://example.test/{abs(hash(title))}", "guid": None}


def fold(when: datetime, trades: int | None) -> TapeFold:
    """The fold the loader reads off a ``tape_fold_line`` written with the defaults."""
    return TapeFold(when, 0.054, 0.54, trades)


# --- the loader's fields --------------------------------------------------------------------------------

def test_the_loader_cuts_the_snapshots_and_the_folds_at_the_read_and_keeps_a_prior_close_from_an_earlier_snapshot(tmp_path):
    state_dir = tmp_path / "state"
    write_jsonl(station_stores.context_file(state_dir, DAY), [
        context_line(at(14, 28, 55), {"$VIX": {"last": 14.9, "close": 15.41}, "$VIX3M": {"last": 17.9, "close": 18.08}}),
        context_line(at(14, 30, 0), bars={"$TICK": {"ts": at(14, 29).isoformat(), "close": 151.0}}),   # bars only: not a snapshot
        context_line(at(14, 30, 5), {"$VIX": {"last": 14.87, "close": 0}}, failed=["$VIX3M: HTTPError"]),
        context_line(at(14, 31, 15), {"$VIX": {"last": 14.8, "close": 15.41}}),                           # after the cut
    ])
    write_tape_folds(state_dir, DAY, [tape_fold_line(at(14, 29, 27), trades=1500), tape_fold_line(at(14, 29, 28), trades=None, engine="spy_depth"),
                                      tape_fold_line(at(14, 30, 30), trades=1600)])
    write_tape_folds(state_dir, "2026-10-08", [tape_fold_line(at(15, 0, day="2026-10-08"), trades=1200)])
    notes: list[str] = []

    snapshot, prior_closes = load_market_snapshot(state_dir, DAY, CUT, notes)
    assert snapshot["ts"] == at(14, 30, 5).isoformat() and prior_closes == {"$VIX": 15.41, "$VIX3M": 18.08}
    folds = load_tape_folds(state_dir, DAY, ["2026-10-08", "2026-10-07"], CUT)
    assert folds == {DAY: [fold(at(14, 29, 27), 1500)], "2026-10-08": [fold(at(15, 0, day="2026-10-08"), 1200)], "2026-10-07": []}
    assert notes == []
    assert load_market_snapshot(tmp_path / "none", DAY, CUT, notes) == (None, {}) and notes == ["market snapshot: none at or before the cut"]


def test_the_loader_reads_the_reads_own_diary_row_and_the_days_first_as_written(tmp_path):
    state_dir = tmp_path / "state"
    write_jsonl(station_stores.reversion_rows_file(state_dir, DAY), [
        {"ts": at(9, 30, 54).isoformat(), "spot": SPOT - 40, "gex_source": "spy_proxy×10.0346", "net_exposure": 1.0},
        {"ts": CUT.isoformat(), "spot": SPOT, "gex_source": "native", "net_exposure": 2.0},
        {"ts": at(14, 31, 30).isoformat(), "spot": SPOT, "gex_source": "spy_proxy×10.0346"},
    ])
    notes: list[str] = []
    own, first = load_raw_diary_rows(state_dir, DAY, CUT.isoformat(), notes)
    assert own["net_exposure"] == 2.0 and first["net_exposure"] == 1.0 and notes == []
    assert options_book_of(own) == "native" and options_book_of(first) == "stand_in" and options_book_of({"ts": "x"}) is None
    assert load_raw_diary_rows(state_dir, DAY, at(14, 32).isoformat(), notes) == (None, first)
    assert notes == ["diary row: the read's raw diary row was not found"]
    assert options_book_of(None) is None


# --- data_sources ---------------------------------------------------------------------------------------

def test_data_sources_ages_each_feed_from_the_loaded_stores(tmp_path):
    row = make_row(CUT, gex_source="native", dex_views={"dex_above_spot": 0.6})
    snapshot = {"ts": at(14, 30, 5).isoformat(), "quotes": {"$VIX": {"last": 14.87}}, "failed": ["$ADD: empty", "quotes: HTTPError"]}
    inputs = fake_inputs(tmp_path, cut=CUT, row=row, raw_diary_row=row, market_snapshot=snapshot,
                         tape_folds={DAY: [fold(at(14, 29, 27), 1500)]}, dated_book={"as_of": at(8, 17).isoformat(), "bands": []})
    write_jsonl(headlines_file(inputs.state_dir, DAY), [headline(at(14, 26, 25), "A story"), headline(at(14, 32, 0), "A later story")])

    block = build_data_sources_block(inputs)

    assert block.data == {
        "cut_at": "14:30:12",
        "spx_bars": {"last_finished": "14:29", "age_s": 12},
        "options_diary": {"row_at": "14:30:12", "age_s": 0, "book": "native", "coverage": "complete"},
        "market_feed": {"snapshot_at": "14:30:05", "age_s": 7, "failed": ["$ADD", "quotes"]},
        "tape": {"fold_at": "14:29:27", "age_s": 45, "content": "ok"},
        "headlines": {"last_capture": "14:26:25", "age_s": 227},
        "dated_book": {"as_of": "08:17", "age_h": 6.2},
    }
    assert absent_reasons(block) == {"data_sources.schwab_login_days_left": "not_readable",
                                     "data_sources.tape.trades_15m_vs_usual_x": "too_few_prior_sessions"}


def test_tape_trades_are_set_against_the_prior_sessions_median_at_the_same_clock(tmp_path):
    days = prior_days(7)
    folds = {DAY: [fold(at(14, 29, 27), 1500)]}
    for day, trades in zip(days[:5], (1000, 2000, 3000, 4000, 5000)):
        folds[day] = [fold(at(14, 29, 50, day=day), trades), fold(at(14, 31, 0, day=day), 99_999)]   # the later fold is past the clock
    folds[days[5]] = [fold(at(14, 29, 0, day=days[5]), 0)]          # a dead feed is not a usual
    folds[days[6]] = [fold(at(14, 20, 0, day=days[6]), 7000)]       # older than the tape age limit at the clock
    inputs = fake_inputs(tmp_path, cut=CUT, prior_bars={d: [] for d in days}, tape_folds=folds)

    assert build_data_sources_block(inputs).data["tape"]["trades_15m_vs_usual_x"] == 0.5     # 1500 over the median 3000

    folds[days[4]] = []                                                                    # four usable sessions are too few
    block = build_data_sources_block(inputs)
    assert "trades_15m_vs_usual_x" not in block.data["tape"]
    assert absent_reasons(block)["data_sources.tape.trades_15m_vs_usual_x"] == "too_few_prior_sessions"


def test_tape_content_is_stale_past_the_age_limit_and_empty_without_trades(tmp_path):
    def tape_of(*folds: TapeFold) -> dict:
        return build_data_sources_block(fake_inputs(tmp_path, cut=CUT, tape_folds={DAY: list(folds)})).data["tape"]

    assert tape_of(fold(at(14, 26, 0), 1500))["content"] == "stale"                        # 4 minutes old: past the station's 3
    assert tape_of(fold(at(14, 29, 50), 0))["content"] == "empty"
    block = build_data_sources_block(fake_inputs(tmp_path, cut=CUT, tape_folds={DAY: [fold(at(14, 29, 50), None)]}))
    assert block.data["tape"]["content"] == "empty"
    assert absent_reasons(block)["data_sources.tape.trades_15m_vs_usual_x"] == "no_trade_count"


def test_the_diary_row_says_its_book_its_age_and_a_missing_view(tmp_path):
    row = make_row(at(14, 28, 50), gex_source="spy_proxy×10.0346")                       # no dex_views
    block = build_data_sources_block(fake_inputs(tmp_path, cut=CUT, row=row, raw_diary_row=row, options_book="stand_in"))
    assert block.data["options_diary"] == {"row_at": "14:28:50", "age_s": 82, "book": "stand_in", "coverage": "partial"}

    block = build_data_sources_block(fake_inputs(tmp_path, cut=CUT, options_book=None))   # the raw row was not found: the scene's row stands in
    assert block.data["options_diary"] == {"row_at": "14:30:12", "age_s": 0, "coverage": "partial"}
    assert absent_reasons(block)["data_sources.options_diary.book"] == "no_gex_source"


def test_missing_feeds_are_declared_not_guessed(tmp_path):
    inputs = fake_inputs(tmp_path, bars=[], options_book=None, dated_book=None)
    block = build_data_sources_block(inputs)
    assert set(block.data) == {"cut_at", "options_diary"}
    assert absent_reasons(block) == {
        "data_sources.spx_bars": "no_finished_bar", "data_sources.options_diary.book": "no_gex_source",
        "data_sources.market_feed": "no_snapshot_before_cut", "data_sources.tape": "no_fold_before_cut",
        "data_sources.headlines": "no_headline_feed", "data_sources.dated_book": "none_known_at_cut",
        "data_sources.schwab_login_days_left": "not_readable",
    }
    write_jsonl(headlines_file(inputs.state_dir, DAY), [headline(at(14, 32, 0), "A later story")])
    inputs.dated_book = {"bands": []}
    block = build_data_sources_block(inputs)
    assert absent_reasons(block)["data_sources.headlines"] == "no_capture_before_cut"
    assert absent_reasons(block)["data_sources.dated_book"] == "as_of_unreadable"


# --- news -----------------------------------------------------------------------------------------------

def test_news_counts_the_hours_captures_movers_kinds_and_repeats(tmp_path):
    inputs = fake_inputs(tmp_path, cut=CUT)
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


def _synthetic_catalog(*groups: tuple[str, int]) -> tuple[dict, ...]:
    """``("a/x", 3)`` gives questions A-X-0, A-X-1, A-X-2 in group a/x; the groups in the order given."""
    return tuple({"id": f"{group.replace('/', '-').upper()}-{i}", "group": group, "options": ["a", "b"]}
                 for group, count in groups for i in range(count))


def test_code_takes_the_cap_in_turns_across_the_catalogs_areas(tmp_path):
    catalog = _catalog()
    answers = {q["id"]: q["options"][0] for q in catalog}
    block = build_code_block(fake_inputs(tmp_path, code_answers=answers))
    assert set(block.data) == {"TREND-01", "TREND-10", "VOLATILITY-05", "LEVELS-07", "OPTIONS-05", "BREADTH-01", "FLOW-03", "MACRO-01",
                               "EVENTS-04", "SENTIMENT-01",                       # one per area, the first uncovered group of each
                               "TREND-03", "TREND-12"}                            # then trend's and shared's second groups
    assert list(block.data) == [q["id"] for q in catalog if q["id"] in block.data]  # written in catalog order
    assert block.absent == []


def test_code_turns_run_over_the_areas_then_each_areas_groups(tmp_path, monkeypatch):
    code_features = station_stores.import_spx_jev("mirai_prediction.code_features")
    monkeypatch.setattr(code_features, "load_catalog", lambda: _synthetic_catalog(("a/x", 6), ("a/y", 4), ("b/z", 6)))
    monkeypatch.setattr(code, "CODE_GROUPS", frozenset({"a/x", "a/y", "b/z"}))
    answers = {q["id"]: "b" for q in code_features.load_catalog()}
    block = build_code_block(fake_inputs(tmp_path, code_answers=answers))
    assert list(block.data) == ["A-X-0", "A-X-1", "A-X-2", "A-Y-0", "A-Y-1", "A-Y-2", "B-Z-0", "B-Z-1", "B-Z-2", "B-Z-3", "B-Z-4", "B-Z-5"]


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

def test_the_real_1430_read_loads_its_stores_cut_at_the_read_ages_every_feed_counts_the_news_and_spreads_the_code_with_no_leak(real_inputs):
    inputs = real_inputs
    sources, news_block, code_block = build_data_sources_block(inputs), build_news_block(inputs), build_code_block(inputs)

    assert inputs.raw_diary_row["ts"] == inputs.row_ts and inputs.first_raw_diary_row["ts"] < inputs.row_ts
    assert inputs.options_book == "native" and inputs.market_snapshot["ts"] <= inputs.row_ts and "$VIX3M" in inputs.snapshot_prior_closes
    assert all(fold.at <= inputs.cut for fold in inputs.tape_folds[inputs.day]) and len(inputs.tape_folds) == 1 + len(inputs.scene.prior_bars)
    assert sources.data["cut_at"] == "14:30:12"
    assert sources.data["spx_bars"] == {"last_finished": "14:29", "age_s": 12}
    assert sources.data["options_diary"] == {"row_at": "14:30:12", "age_s": 0, "book": "native", "coverage": "complete"}
    assert set(sources.data["market_feed"]) == {"snapshot_at", "age_s", "failed"}
    assert sources.data["tape"]["content"] in ("ok", "stale", "empty")
    assert isinstance(sources.data["tape"]["trades_15m_vs_usual_x"], float)
    assert set(sources.data["headlines"]) == {"last_capture", "age_s"}
    assert "load_notes" not in sources.data
    assert absent_reasons(sources)["data_sources.schwab_login_days_left"] == "not_readable"
    assert news_block.data["titles"] == "withheld" and news_block.data["captured_60m"] > 0
    assert news_block.data["stale_dropped"] >= 0 and isinstance(news_block.data["kinds"], list)
    assert 0 < len(code_block.data) <= code.MAX_CODE_ANSWERS
    assert all(re.fullmatch(r"[A-Z]+-\d{2}(-[A-Z]+)?", qid) for qid in code_block.data)
    assert len({q["group"].partition("/")[0] for q in _catalog() if q["id"] in code_block.data}) >= 5   # spread, not the catalog's front
    for name, result in (("data_sources", sources), ("news", news_block), ("code", code_block)):
        assert_no_leak(name, result)
    titles = [line["title"] for line in jsonl_store.iter_json_lines(headlines_file(inputs.state_dir, inputs.day))]
    text = json.dumps([news_block.data, news_block.absent])
    assert titles and not any(title in text for title in titles)                        # no headline title
