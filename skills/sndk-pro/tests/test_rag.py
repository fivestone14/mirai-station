"""sndk_rag — the on-demand memory: two-face records, tiers, hybrid retrieval.

State rides MIRAI_STATE_DIR (conftest → tmp), so every test builds its own
little history and queries it. The ranker tests pin the LEXICAL path (the
embedder is an optional upgrade the suite must not depend on — forced off via
a broken import path is not needed: _rank falls back on any failure, and
these tests assert on content the lexical scorer must find)."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import sndk_rag as RAG

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 31, 14, 30, tzinfo=ET)


def _read_out(line="rejected the call wall and faded", vector="down", mag=0.12,
              wake="price ran"):
    return {"era": "sr-2", "wake": wake,
            "reading": {"line": line, "breaks_if": "1300 breaks",
                        "vector": vector, "magnitude_sigma": mag},
            "arrow": {"dir": None},
            "magnet_band": {"top": [[1300.0, 9.5], [1100.0, 8.7]],
                            "gap_pp": 0.8}}


def _row(spot=1213.7):
    return {"spot": spot, "sigma": 129.9, "gamma_sign": "negative",
            "regime": "trending", "vwap": 1220.0,
            "profile_ladder": {"ct": 1254.0, "hvl": 1221.0, "pt": 1189.0}}


def _scene():
    return {"walls": {"call": [{"strike": 1220.0}, {"strike": 1250.0}],
                      "put": [{"strike": 1200.0}]}}


def _diary_day(tmp_path, day, spots, magnet=1300.0):
    p = tmp_path / "sndk_reversion"
    p.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, s in enumerate(spots):
        rows.append(json.dumps({
            "ts": f"{day}T{9 + i:02d}:30:00-04:00", "ticker": "SNDK",
            "spot": s, "gamma_sign": "negative", "regime": "trending",
            "call_wall": 1300.0, "put_wall": 1100.0,
            "gex_views": {"magnet": magnet}}))
    (p / f"{day}.jsonl").write_text("\n".join(rows) + "\n")


# --- one record, two faces --------------------------------------------------
def test_record_slice_writes_both_faces(tmp_path):
    RAG.record_slice(_row(), _read_out(), _scene(), T0)
    recs = RAG._read_jsonl(RAG._slices_path("2026-07-31"))
    assert len(recs) == 1
    r = recs[0]
    assert r["kind"] == "slice" and r["era"] == "sr-2"
    # Face B: the model's own sentence, breaks_if folded in
    assert r["narrative"].startswith("rejected the call wall")
    assert "Changes if 1300 breaks" in r["narrative"]
    # Face A: exact-filterable metadata, numbers never embedded
    m = r["meta"]
    assert m["spot"] == 1213.7 and m["vector"] == "down"
    assert m["walls_call"] == [1220.0, 1250.0]
    assert m["magnet_top"] == [[1300.0, 9.5], [1100.0, 8.7]]
    assert m["time"] == "14:30"


def test_record_slice_without_a_sentence_stores_nothing(tmp_path):
    out = _read_out()
    out["reading"] = None
    RAG.record_slice(_row(), out, _scene(), T0)
    assert RAG._read_jsonl(RAG._slices_path("2026-07-31")) == []


# --- retrieval: metadata gates FIRST, then narrative ranks ------------------
def _seed_slices():
    RAG.record_slice(_row(1213.7), _read_out("rejected the call wall and faded",
                                             wake="price ran"),
                     _scene(), T0)
    RAG.record_slice(_row(1195.0), _read_out("pinned to vwap all afternoon",
                                             vector="none", mag=None),
                     _scene(), T0 + timedelta(minutes=40))
    RAG.record_slice(_row(1130.0),
                     _read_out("broke the floor and kept going", "down", 0.3),
                     {"walls": {"put": [{"strike": 1100.0}]}},
                     T0 + timedelta(minutes=80))


def test_near_strike_filter_gates_before_ranking(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-07-31", near_strike=1100.0,
                    tolerance=15.0)
    # only slices TOUCHING 1100 (magnet_top carries 1100 on all three;
    # tighten to the wall) — the 1130-spot slice matches on walls_put + spot
    assert out["n_matched"] >= 1
    assert any("broke the floor" in r["narrative"] for r in out["results"])


def test_time_window_filter(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-07-31",
                    t_from="15:00", t_to="16:00")
    times = [r["time"] for r in out["results"]]
    assert times and all("15:00" <= t <= "16:00" for t in times)


def test_text_ranking_surfaces_the_meaningful_match(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-07-31",
                    text="price stuck to vwap", limit=1)
    assert out["rank"] in ("semantic", "lexical")
    assert "vwap" in out["results"][0]["narrative"]


# --- day summaries + sentiment ---------------------------------------------
def test_rollup_builds_a_sentiment_summary(tmp_path):
    _diary_day(tmp_path, "2026-07-30", [1200.0, 1150.0, 1120.0])
    done = RAG.rollup()
    assert "2026-07-30" in done
    s = RAG._summaries()[-1]
    assert s["date"] == "2026-07-30"
    assert "more bearish than it opened" in s["narrative"]
    assert s["meta"]["pct_open_to_close"] == pytest.approx(-6.67, abs=0.01)
    # idempotent: a second rollup does not duplicate
    assert RAG.rollup() == []
    assert len([x for x in RAG._summaries() if x["date"] == "2026-07-30"]) == 1


def test_rollup_never_summarizes_a_live_day(tmp_path):
    today = datetime.now(ET).date().isoformat()
    _diary_day(tmp_path, today, [1200.0, 1210.0])
    assert today not in RAG.rollup()


# --- v2: the stitched day narrative -----------------------------------------
def test_summary_narrative_is_dated_and_stitched_from_slices(tmp_path):
    """v2: date prefix + numeric spine + the model's OWN sentences (first,
    biggest-move, last), 'Changes if' clauses stripped — uniqueness for free,
    no model call (narrative-uniqueness pressure test 08-05)."""
    _seed_slices()
    _diary_day(tmp_path, "2026-07-31", [1213.7, 1195.0, 1130.0])
    s = RAG.build_summary("2026-07-31")
    n = s["narrative"]
    assert n.startswith("2026-07-31: SNDK closed")          # spine, dated
    assert "rejected the call wall and faded." in n         # first read
    assert "broke the floor and kept going." in n           # last read
    assert "Changes if" not in n                            # stale live-read bit
    assert s["source"] == "slices+diary"


def test_summary_without_slices_keeps_the_dated_spine(tmp_path):
    _diary_day(tmp_path, "2026-07-30", [1200.0, 1150.0, 1120.0])
    s = RAG.build_summary("2026-07-30")
    assert s["narrative"].startswith("2026-07-30: SNDK closed")
    assert s["source"] == "diary"


# --- v2: days-tier Face-A filters -------------------------------------------
def _seed_summaries():
    rows = [("2026-07-28", -5.7, 1100.0), ("2026-07-29", -7.2, 1100.0),
            ("2026-07-30", 12.4, 1100.0), ("2026-07-31", -13.5, 1300.0),
            ("2026-08-03", 13.3, 1370.0), ("2026-08-04", 4.6, 1370.0)]
    RAG._rag_dir().mkdir(parents=True, exist_ok=True)
    RAG._summaries_path().write_text("\n".join(json.dumps(
        {"kind": "day_summary", "date": d, "rag_v": 2,
         "meta": {"open": 1200.0, "close": 1200.0 * (1 + p / 100),
                  "low": 1150.0, "high": 1250.0, "pct_open_to_close": p,
                  "magnet_mode": f"{m:g}"},
         "narrative": f"{d}: SNDK moved {p}%."}) for d, p, m in rows) + "\n")


def test_days_exact_date_beats_the_days_back_page(tmp_path):
    """--date must reach ALL history — the old code cut to the last N first,
    silently dropping any older day (the ordering trap)."""
    _seed_summaries()
    out = RAG.query(tier="days", date="2026-07-28", days_back=2)
    assert out["n_matched"] == 1
    assert out["results"][0]["date"] == "2026-07-28"


def test_days_min_move_finds_the_crash_no_embedding_needed(tmp_path):
    """'biggest crash' is an ordinal ask — the numbers answer it, not cosine
    similarity (which ranked the +12.4% rally first on the real corpus)."""
    _seed_summaries()
    out = RAG.query(tier="days", min_move=-10.0)
    assert [r["date"] for r in out["results"]] == ["2026-07-31"]
    up = RAG.query(tier="days", min_move=10.0)
    assert {r["date"] for r in up["results"]} == {"2026-07-30", "2026-08-03"}


def test_days_date_range_and_near_strike_gate_before_ranking(tmp_path):
    _seed_summaries()
    rng = RAG.query(tier="days", d_from="2026-07-29", d_to="2026-07-31")
    assert [r["date"] for r in rng["results"]] == [
        "2026-07-29", "2026-07-30", "2026-07-31"]
    near = RAG.query(tier="days", near_strike=1370.0, tolerance=5.0)
    assert {r["date"] for r in near["results"]} == {"2026-08-03", "2026-08-04"}


def test_days_unfiltered_keeps_the_days_back_page(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", days_back=2)
    assert out["n_matched"] == 2
    assert [r["date"] for r in out["results"]] == ["2026-08-03", "2026-08-04"]


# --- 25-agent verification round (08-05): confirmed-defect regressions ------
def test_min_move_zero_keeps_down_sessions_too(tmp_path):
    """0 = 'any recorded move' per its own help — it silently kept only
    flat-or-up days, a directionally biased memory."""
    _seed_summaries()
    out = RAG.query(tier="days", min_move=0.0, limit=10)
    assert out["n_matched"] == 6            # every session with a recorded pct


def test_near_day_matches_a_session_that_traded_through_the_strike(tmp_path):
    """'has 1250 held before?' must match a day that printed 1250 mid-range,
    even when every endpoint sits outside tolerance."""
    assert RAG._near_day({"open": 1400.0, "close": 1300.0,
                          "low": 1197.0, "high": 1403.0}, 1250.0, 5.0)
    assert not RAG._near_day({"open": 1400.0, "close": 1300.0,
                              "low": 1290.0, "high": 1403.0}, 1250.0, 5.0)


def test_date_args_normalize_unpadded_and_reject_garbage(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", date="2026-7-31")       # unpadded → padded
    assert [r["date"] for r in out["results"]] == ["2026-07-31"]
    with pytest.raises(ValueError):
        RAG.query(tier="days", d_from="08/01/2026")
    with pytest.raises(ValueError):
        RAG.query(tier="slices", date="../../etc/passwd")


def test_blank_date_flag_is_absent_not_filtered(tmp_path):
    """--date "" (an unset shell variable) must not disarm the days_back
    page and dump all history as a 'match'."""
    _seed_summaries()
    out = RAG.query(tier="days", date="", days_back=2)
    assert out["n_matched"] == 2


def test_text_only_days_ask_reaches_the_month_window(tmp_path):
    """A semantic ask was silently confined to the 5-day page — 'magnet
    camped at 1100' lived on a day just off it."""
    _seed_summaries()
    out = RAG.query(tier="days", text="SNDK moved -5.7", days_back=2, limit=10)
    assert out["n_matched"] == 6            # month window, not the 2-day page
    assert any(r["date"] == "2026-07-28" for r in out["results"])


def test_terrain_cache_from_an_older_schema_era_is_rebuilt(tmp_path):
    _diary_day(tmp_path, "2026-07-30", [1200.0, 1210.0])
    t = RAG.terrain(rebuild=True)
    stale = dict(t, rag_v=RAG.RAG_V - 1)
    RAG._terrain_path().write_text(json.dumps(stale))
    assert RAG.terrain()["rag_v"] == RAG.RAG_V           # not served stale


def test_story_pick_ignores_off_hours_slices(tmp_path):
    """A forced warmup read must not narrate the session — same live-tape
    rule the diary side already applies."""
    warm = _read_out("midnight warmup read, ignore me")
    RAG.record_slice(_row(), warm, _scene(),
                     datetime(2026, 7, 31, 0, 5, tzinfo=ET))
    _seed_slices()
    _diary_day(tmp_path, "2026-07-31", [1213.7, 1195.0, 1130.0])
    n = RAG.build_summary("2026-07-31")["narrative"]
    assert "midnight warmup" not in n
    assert "rejected the call wall and faded." in n


def test_story_bit_cap_never_cuts_mid_word_unpunctuated():
    long = "word " * 60                                   # > STORY_BIT_CHARS
    bit = RAG._story_bit(long)
    assert len(bit) <= RAG.STORY_BIT_CHARS + 1
    assert bit.endswith(".")


def test_null_slice_time_never_crashes_a_rollup(tmp_path):
    _seed_slices()
    p = RAG._slices_path("2026-07-31")
    rec = json.loads(p.read_text().splitlines()[0])
    rec["meta"]["time"] = None                            # explicit null
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    _diary_day(tmp_path, "2026-07-31", [1213.7, 1195.0, 1130.0])
    assert RAG.build_summary("2026-07-31") is not None


def test_stale_era_summaries_self_migrate_but_never_delete(tmp_path):
    """A rag_v bump re-rolls old rows from their diaries on the next rollup;
    a stale row whose diary is GONE is history — kept, not deleted."""
    _diary_day(tmp_path, "2026-07-30", [1200.0, 1150.0])
    RAG._rag_dir().mkdir(parents=True, exist_ok=True)
    RAG._summaries_path().write_text("\n".join(json.dumps(r) for r in [
        {"kind": "day_summary", "date": "2026-07-30", "rag_v": RAG.RAG_V - 1,
         "meta": {"open": 1.0}, "narrative": "old era"},
        {"kind": "day_summary", "date": "2026-07-01", "rag_v": RAG.RAG_V - 1,
         "meta": {"open": 1.0}, "narrative": "diary long gone"}]) + "\n")
    assert RAG.rollup() == ["2026-07-30"]
    sums = {s["date"]: s for s in RAG._summaries()}
    assert sums["2026-07-30"]["rag_v"] == RAG.RAG_V      # migrated
    assert sums["2026-07-01"]["narrative"] == "diary long gone"  # preserved


# --- month tier: standing terrain, context not trigger ----------------------
def test_terrain_counts_recurring_walls(tmp_path):
    for i, day in enumerate(("2026-07-28", "2026-07-29", "2026-07-30")):
        _diary_day(tmp_path, day, [1200.0 + i, 1210.0 + i])
    t = RAG.terrain(rebuild=True)
    assert t["sessions"] == 3
    assert t["standing"]["all_time"]["magnets"].get("1300") == 3
    assert t["standing"]["all_time"]["call_walls"].get("1300") == 3
    assert "context, never the trigger" in t["note"]
    # cached for the day after the first build
    assert RAG.terrain()["built"] == t["built"]


def test_terrain_recent_outranks_stale_alltime_levels(tmp_path):
    """A fast mover's OLD walls must not crowd the headline: 8 sessions at
    1300, then RECENT_SESSIONS at 1500 — the headline and the recent block
    speak from the map price is on NOW; all-time keeps the depth."""
    days = [f"2026-07-{d:02d}" for d in range(1, 9 + RAG.RECENT_SESSIONS)]
    for i, day in enumerate(days):
        old = i < 8
        _diary_day(tmp_path, day, [1200.0 + i, 1210.0 + i],
                   magnet=1300.0 if old else 1500.0)
        if not old:                       # recent sessions live at 1500 walls
            p = tmp_path / "sndk_reversion" / f"{day}.jsonl"
            rows = [json.loads(ln) for ln in p.read_text().splitlines()]
            for r in rows:
                r["call_wall"], r["put_wall"] = 1500.0, 1400.0
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    t = RAG.terrain(rebuild=True)
    rec = t["standing"]["recent"]
    assert rec["magnets"].get("1500") == RAG.RECENT_SESSIONS
    assert "1300" not in rec["magnets"] and "1300" not in rec["call_walls"]
    assert t["standing"]["all_time"]["magnets"].get("1300") == 8
    assert "1500 has been the magnet" in t["narrative"]


# --- the numeric series (the diary IS the plain store) ----------------------
def test_series_buckets_the_diary_by_time(tmp_path):
    _diary_day(tmp_path, "2026-07-30", [1200.0, 1150.0, 1120.0])
    out = RAG.series(date="2026-07-30", t_from="09:00", t_to="12:00",
                     step_min=10)
    assert [r["spot"] for r in out["rows"]] == [1200.0, 1150.0, 1120.0]
    assert all(r["magnet"] == 1300.0 for r in out["rows"])


# --- the lexical fallback ranker -------------------------------------------
def test_lexical_scorer_prefers_overlap():
    a = RAG._lex_score("rejected the call wall", "rejected the call wall and faded")
    b = RAG._lex_score("rejected the call wall", "pinned to vwap all afternoon")
    assert a > b


# --- SE-review regressions (08-02) ------------------------------------------
def test_rollup_refuses_an_explicit_live_day(tmp_path):
    """The rollup CLI sits inside the model's Bash grant — an explicit --date
    for today must not freeze half a session as 'the day'."""
    today = datetime.now(ET).date().isoformat()
    _diary_day(tmp_path, today, [1200.0, 1210.0])
    assert RAG.rollup(today) == []
    assert all(s["date"] != today for s in RAG._summaries())


def test_time_filters_pad_unpadded_hours(tmp_path):
    assert RAG._hhmm("9:30") == "09:30"
    assert RAG._hhmm("15:00") == "15:00"
    assert RAG._hhmm(None) is None
    _seed_slices()
    # '9:30' lexically > '15:00' — unpadded it silently emptied the window
    out = RAG.query(tier="slices", date="2026-07-31", t_from="9:30")
    assert out["n_matched"] == 3


def test_n_matched_reports_the_filter_count_not_the_page(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-07-31", limit=1)
    assert out["n_matched"] == 3 and len(out["results"]) == 1


def test_series_is_queryable_by_strike_too(tmp_path):
    """§05: the plain store is queryable by time AND strike — with --strike,
    each bucket reports that strike's own gamma mass and traded volume,
    absent when the strike sat outside that scan's window."""
    p = tmp_path / "sndk_reversion"
    p.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps({
        "ts": f"2026-07-30T{10 + i:02d}:00:00-04:00", "ticker": "SNDK",
        "spot": 1200.0 + i, "call_wall": 1300.0, "put_wall": 1100.0,
        "gex_views": {"magnet": 1300.0,
                      "mass_by_strike": [[1300.0, 50.0 + i]],
                      "vol_gross_by_strike": [[1300.0, 1000 * (i + 1)]]}})
        for i in range(3)]
    (p / "2026-07-30.jsonl").write_text("\n".join(rows) + "\n")
    out = RAG.series(date="2026-07-30", strike=1300.0)
    assert out["strike"] == 1300.0
    assert [r["gamma_mass_at_strike"] for r in out["rows"]] == [50.0, 51.0, 52.0]
    assert [r["vol_gross_at_strike"] for r in out["rows"]] == [1000, 2000, 3000]
    # a strike outside the recorded window stays absent, never zero
    out2 = RAG.series(date="2026-07-30", strike=1400.0)
    assert all("gamma_mass_at_strike" not in r for r in out2["rows"])


def test_slices_never_store_the_aggregator_arrow(tmp_path):
    """Adversarial audit: the stripped verdict must not be one Bash call
    away — no arrow_dir in Face A, and arrow-naming wake strings neutralize."""
    out = _read_out()
    out["arrow"] = {"dir": "up"}
    out["wake"] = "arrow appeared"
    RAG.record_slice(_row(), out, _scene(), T0)
    rec = RAG._read_jsonl(RAG._slices_path("2026-07-31"))[-1]
    assert "arrow_dir" not in rec["meta"]
    assert "arrow" not in json.dumps(rec)
    assert rec["meta"]["wake"] == "gate event"
    # ordinary wake reasons pass through untouched
    RAG.record_slice(_row(), _read_out(wake="price ran"), _scene(),
                     T0 + timedelta(minutes=9))
    rec2 = RAG._read_jsonl(RAG._slices_path("2026-07-31"))[-1]
    assert rec2["meta"]["wake"] == "price ran"


def test_diary_day_excludes_forced_and_off_hours_rows(tmp_path):
    """History must never hand the model tape the reader ignores: forced
    warmup rows and pre-flag midnight rows stay out of summaries/series."""
    p = tmp_path / "sndk_reversion"
    p.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-07-28T00:05:00-04:00", "ticker": "SNDK", "spot": 1246.0,
         "gex_views": {"magnet": 1300.0}, "meta": {"forced": True}},
        {"ts": "2026-07-28T23:31:00-04:00", "ticker": "SNDK", "spot": 1250.0,
         "gex_views": {"magnet": 1300.0}},          # pre-flag midnight row
        {"ts": "2026-07-28T09:31:00-04:00", "ticker": "SNDK", "spot": 1162.7,
         "gex_views": {"magnet": 1300.0}},
        {"ts": "2026-07-28T15:59:00-04:00", "ticker": "SNDK", "spot": 1096.4,
         "gex_views": {"magnet": 1300.0}},
    ]
    (p / "2026-07-28.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    live = RAG._diary_day("2026-07-28")
    assert [r["spot"] for r in live] == [1162.7, 1096.4]
    s = RAG.build_summary("2026-07-28")
    assert s["meta"]["open"] == 1162.7 and s["meta"]["high"] == 1162.7


def test_forced_rebuild_retires_a_phantom_session(tmp_path):
    """A day whose only rows fail the live-tape filter must LEAVE the
    summaries on --force, not linger as a phantom session."""
    p = tmp_path / "sndk_reversion"
    p.mkdir(parents=True, exist_ok=True)
    (p / "2026-07-27.jsonl").write_text(json.dumps(
        {"ts": "2026-07-27T23:31:00-04:00", "ticker": "SNDK", "spot": 1246.0,
         "gex_views": {"magnet": 1300.0}}) + "\n")
    # seed a stale summary the way the pre-fix code would have
    RAG._rag_dir().mkdir(parents=True, exist_ok=True)
    RAG._summaries_path().write_text(json.dumps(
        {"kind": "day_summary", "date": "2026-07-27", "rag_v": 1,
         "meta": {"open": 1246.0}, "narrative": "phantom"}) + "\n")
    assert RAG.rollup("2026-07-27", force=True) == ["2026-07-27"]
    assert all(s["date"] != "2026-07-27" for s in RAG._summaries())
