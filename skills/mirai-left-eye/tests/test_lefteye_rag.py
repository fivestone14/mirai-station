"""lefteye_rag — the SPX Watchtower's on-demand memory (sndk_rag v2 twin).

State rides MIRAI_STATE_DIR (fixture → tmp), so every test builds its own
little history and queries it. The ranker tests pin the LEXICAL path (the
embedder is an optional upgrade the suite must not depend on: _rank falls
back on any failure, and these tests assert on content the lexical scorer
must find). The twin doctrine: a retrieval defect fixed here should be
checked against sndk_rag too, and vice versa — the shapes are kept parallel
on purpose (shared RAG_V)."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import lefteye_rag as RAG

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 8, 7, 14, 30, tzinfo=ET)


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    yield


def _wt(scene="rejected the call wall and pinned", direction="put", fired=True,
        mag=0.3, interest="vwap_stretch +0.82σ"):
    return {"prompt_version": "wt-11", "fired": fired, "direction": direction,
            "magnitude_sigma": mag, "stance": "settle",
            "scene": scene, "would_change_mind": "7900 breaks and holds",
            "interest": interest}


def _row(spot=7747.3):
    return {"spot": spot, "sigma": 84.4, "gamma_sign": "positive",
            "regime": "pinning", "vwap": 7752.0,
            "call_wall": 7900.0, "put_wall": 7550.0,
            "profile_ladder": {"ct": 7766.6, "hvl": 7745.5, "pt": 7724.4},
            "gex_views": {"magnet": 7750.0,
                          "mass_by_strike": [[7750.0, 90.0], [7700.0, 55.0]]}}


def _diary_day(tmp_path, day, spots, magnet=7750.0):
    p = tmp_path / "reversion"
    p.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, s in enumerate(spots):
        rows.append(json.dumps({
            "ts": f"{day}T{9 + i:02d}:30:00-04:00", "ticker": "SPX",
            "spot": s, "gamma_sign": "positive", "regime": "pinning",
            "call_wall": 7900.0, "put_wall": 7550.0,
            "gex_views": {"magnet": magnet}}))
    (p / f"{day}.jsonl").write_text("\n".join(rows) + "\n")


# --- one record, two faces --------------------------------------------------
def test_record_slice_writes_both_faces(tmp_path):
    RAG.record_slice(_row(), _wt(), T0)
    recs = RAG._read_jsonl(RAG._slices_path("2026-08-07"))
    assert len(recs) == 1
    r = recs[0]
    assert r["kind"] == "slice" and r["era"] == "wt-11"
    # Face B: the tower's own sentence, would_change_mind folded in
    assert r["narrative"].startswith("rejected the call wall")
    assert "Changes if 7900 breaks and holds" in r["narrative"]
    # Face A: exact-filterable metadata, numbers never embedded
    m = r["meta"]
    assert m["spot"] == 7747.3 and m["vector"] == "down"
    assert m["walls_call"] == [7900.0] and m["walls_put"] == [7550.0]
    assert m["magnet_top"] == [[7750.0, 62.07], [7700.0, 37.93]]
    assert m["time"] == "14:30" and m["stance"] == "settle"


def test_record_slice_without_a_sentence_stores_nothing(tmp_path):
    wt = _wt()
    wt["scene"] = None
    RAG.record_slice(_row(), wt, T0)
    assert RAG._read_jsonl(RAG._slices_path("2026-08-07")) == []


def test_a_stand_down_records_vector_none(tmp_path):
    """fired=false is a judged read too — the sentence is memorable, the
    vector is honestly 'none', and no direction leaks from a dead call."""
    RAG.record_slice(_row(), _wt(scene="two-sided pin, no edge",
                                 fired=False, direction=None), T0)
    m = RAG._read_jsonl(RAG._slices_path("2026-08-07"))[-1]["meta"]
    assert m["vector"] == "none"


def test_slices_never_store_the_gates_verdict(tmp_path):
    """The 08-02 audit rule, ported: Head A's decision is the pre-baked
    conclusion the blind pass hides — it must not be one Bash call away.
    Gate-naming interest strings neutralize to 'gate event'."""
    RAG.record_slice(_row(), _wt(interest="gates active"), T0)
    rec = RAG._read_jsonl(RAG._slices_path("2026-08-07"))[-1]
    assert rec["meta"]["wake"] == "gate event"
    blob = json.dumps(rec)
    assert "gates active" not in blob and "armed" not in blob
    # ordinary interest reasons pass through untouched
    RAG.record_slice(_row(), _wt(interest="hourly heartbeat"),
                     T0 + timedelta(minutes=9))
    rec2 = RAG._read_jsonl(RAG._slices_path("2026-08-07"))[-1]
    assert rec2["meta"]["wake"] == "hourly heartbeat"


# --- retrieval: metadata gates FIRST, then narrative ranks ------------------
def _seed_slices():
    RAG.record_slice(_row(7747.3), _wt("rejected the call wall and pinned"), T0)
    RAG.record_slice(_row(7735.0), _wt("pinned to vwap all afternoon",
                                       fired=False, direction=None, mag=None),
                     T0 + timedelta(minutes=40))
    r3 = _row(7540.0)
    r3["put_wall"] = 7550.0
    RAG.record_slice(r3, _wt("broke the floor and kept going", "put", 0.6),
                     T0 + timedelta(minutes=80))


def test_near_strike_filter_gates_before_ranking(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-08-07", near_strike=7550.0,
                    tolerance=15.0)
    assert out["n_matched"] >= 1
    assert any("broke the floor" in r["narrative"] for r in out["results"])


def test_time_window_filter(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-08-07",
                    t_from="15:00", t_to="16:00")
    times = [r["time"] for r in out["results"]]
    assert times and all("15:00" <= t <= "16:00" for t in times)


def test_text_ranking_surfaces_the_meaningful_match(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-08-07",
                    text="price stuck to vwap", limit=1)
    assert out["rank"] in ("semantic", "lexical")
    assert "vwap" in out["results"][0]["narrative"]


# --- day summaries + sentiment ---------------------------------------------
def test_rollup_builds_a_sentiment_summary(tmp_path):
    _diary_day(tmp_path, "2026-08-06", [7800.0, 7720.0, 7690.0])
    done = RAG.rollup()
    assert "2026-08-06" in done
    s = RAG._summaries()[-1]
    assert s["date"] == "2026-08-06"
    assert "more bearish than it opened" in s["narrative"]
    assert s["meta"]["pct_open_to_close"] == pytest.approx(-1.41, abs=0.01)
    # idempotent: a second rollup does not duplicate
    assert RAG.rollup() == []
    assert len([x for x in RAG._summaries() if x["date"] == "2026-08-06"]) == 1


def test_rollup_never_summarizes_a_live_day(tmp_path):
    today = datetime.now(ET).date().isoformat()
    _diary_day(tmp_path, today, [7700.0, 7710.0])
    assert today not in RAG.rollup()
    assert RAG.rollup(today) == []          # explicit --date included
    assert all(s["date"] != today for s in RAG._summaries())


def test_summary_narrative_is_dated_and_stitched_from_slices(tmp_path):
    """v2 shape: date prefix + numeric spine + the tower's OWN sentences
    (first, biggest-move, last), 'Changes if' clauses stripped."""
    _seed_slices()
    _diary_day(tmp_path, "2026-08-07", [7747.3, 7735.0, 7540.0])
    s = RAG.build_summary("2026-08-07")
    n = s["narrative"]
    assert n.startswith("2026-08-07: SPX closed")            # spine, dated
    assert "rejected the call wall and pinned." in n         # first read
    assert "broke the floor and kept going." in n            # last read
    assert "Changes if" not in n                             # stale live bit
    assert s["source"] == "slices+diary"


def test_summary_without_slices_keeps_the_dated_spine(tmp_path):
    _diary_day(tmp_path, "2026-08-06", [7800.0, 7720.0, 7690.0])
    s = RAG.build_summary("2026-08-06")
    assert s["narrative"].startswith("2026-08-06: SPX closed")
    assert s["source"] == "diary"


# --- days-tier Face-A filters ------------------------------------------------
def _seed_summaries():
    rows = [("2026-07-28", -0.7, 7600.0), ("2026-07-29", -1.2, 7600.0),
            ("2026-07-30", 1.4, 7600.0), ("2026-07-31", -2.5, 7750.0),
            ("2026-08-03", 1.3, 7800.0), ("2026-08-04", 0.6, 7800.0)]
    RAG._rag_dir().mkdir(parents=True, exist_ok=True)
    RAG._summaries_path().write_text("\n".join(json.dumps(
        {"kind": "day_summary", "date": d, "rag_v": 2,
         "meta": {"open": 7700.0, "close": 7700.0 * (1 + p / 100),
                  "low": 7600.0, "high": 7800.0, "pct_open_to_close": p,
                  "magnet_mode": f"{m:g}"},
         "narrative": f"{d}: SPX moved {p}%."}) for d, p, m in rows) + "\n")


def test_days_exact_date_beats_the_days_back_page(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", date="2026-07-28", days_back=2)
    assert out["n_matched"] == 1
    assert out["results"][0]["date"] == "2026-07-28"


def test_days_min_move_is_signed_and_zero_means_any(tmp_path):
    """Ordinal asks go through the numbers, not cosine similarity — and 0
    keeps DOWN sessions too (a flat-or-up-only memory is a biased one)."""
    _seed_summaries()
    out = RAG.query(tier="days", min_move=-2.0)
    assert [r["date"] for r in out["results"]] == ["2026-07-31"]
    up = RAG.query(tier="days", min_move=1.0)
    assert {r["date"] for r in up["results"]} == {"2026-07-30", "2026-08-03"}
    anym = RAG.query(tier="days", min_move=0.0, limit=10)
    assert anym["n_matched"] == 6


def test_days_date_range_and_near_strike_gate_before_ranking(tmp_path):
    _seed_summaries()
    rng = RAG.query(tier="days", d_from="2026-07-29", d_to="2026-07-31")
    assert [r["date"] for r in rng["results"]] == [
        "2026-07-29", "2026-07-30", "2026-07-31"]
    near = RAG.query(tier="days", near_strike=7800.0, tolerance=5.0)
    # every seeded session printed 7800 as its high (range containment) —
    # the strike-mode gate narrows nothing here, the range gate answers
    assert near["n_matched"] == 6


def test_days_unfiltered_keeps_the_days_back_page(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", days_back=2)
    assert out["n_matched"] == 2
    assert [r["date"] for r in out["results"]] == ["2026-08-03", "2026-08-04"]


def test_near_day_matches_a_session_that_traded_through_the_strike(tmp_path):
    assert RAG._near_day({"open": 7800.0, "close": 7700.0,
                          "low": 7597.0, "high": 7803.0}, 7650.0, 5.0)
    assert not RAG._near_day({"open": 7800.0, "close": 7700.0,
                              "low": 7690.0, "high": 7803.0}, 7650.0, 5.0)


def test_date_args_normalize_unpadded_and_reject_garbage(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", date="2026-7-31")       # unpadded → padded
    assert [r["date"] for r in out["results"]] == ["2026-07-31"]
    with pytest.raises(ValueError):
        RAG.query(tier="days", d_from="08/01/2026")
    with pytest.raises(ValueError):
        RAG.query(tier="slices", date="../../etc/passwd")


def test_blank_date_flag_is_absent_not_filtered(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", date="", days_back=2)
    assert out["n_matched"] == 2


def test_text_only_days_ask_reaches_the_month_window(tmp_path):
    _seed_summaries()
    out = RAG.query(tier="days", text="SPX moved -0.7", days_back=2, limit=10)
    assert out["n_matched"] == 6            # month window, not the 2-day page
    assert any(r["date"] == "2026-07-28" for r in out["results"])


def test_terrain_cache_from_an_older_schema_era_is_rebuilt(tmp_path):
    _diary_day(tmp_path, "2026-08-06", [7700.0, 7710.0])
    t = RAG.terrain(rebuild=True)
    stale = dict(t, rag_v=RAG.RAG_V - 1)
    RAG._terrain_path().write_text(json.dumps(stale))
    assert RAG.terrain()["rag_v"] == RAG.RAG_V           # not served stale


def test_story_pick_ignores_off_hours_slices(tmp_path):
    warm = _wt("midnight warmup read, ignore me")
    RAG.record_slice(_row(), warm, datetime(2026, 8, 7, 0, 5, tzinfo=ET))
    _seed_slices()
    _diary_day(tmp_path, "2026-08-07", [7747.3, 7735.0, 7540.0])
    n = RAG.build_summary("2026-08-07")["narrative"]
    assert "midnight warmup" not in n
    assert "rejected the call wall and pinned." in n


def test_story_bit_cap_never_cuts_mid_word_unpunctuated():
    long = "word " * 60                                   # > STORY_BIT_CHARS
    bit = RAG._story_bit(long)
    assert len(bit) <= RAG.STORY_BIT_CHARS + 1
    assert bit.endswith(".")


def test_null_slice_time_never_crashes_a_rollup(tmp_path):
    _seed_slices()
    p = RAG._slices_path("2026-08-07")
    rec = json.loads(p.read_text().splitlines()[0])
    rec["meta"]["time"] = None                            # explicit null
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    _diary_day(tmp_path, "2026-08-07", [7747.3, 7735.0, 7540.0])
    assert RAG.build_summary("2026-08-07") is not None


def test_stale_era_summaries_self_migrate_but_never_delete(tmp_path):
    _diary_day(tmp_path, "2026-08-06", [7700.0, 7660.0])
    RAG._rag_dir().mkdir(parents=True, exist_ok=True)
    RAG._summaries_path().write_text("\n".join(json.dumps(r) for r in [
        {"kind": "day_summary", "date": "2026-08-06", "rag_v": RAG.RAG_V - 1,
         "meta": {"open": 1.0}, "narrative": "old era"},
        {"kind": "day_summary", "date": "2026-07-01", "rag_v": RAG.RAG_V - 1,
         "meta": {"open": 1.0}, "narrative": "diary long gone"}]) + "\n")
    assert RAG.rollup() == ["2026-08-06"]
    sums = {s["date"]: s for s in RAG._summaries()}
    assert sums["2026-08-06"]["rag_v"] == RAG.RAG_V      # migrated
    assert sums["2026-07-01"]["narrative"] == "diary long gone"  # preserved


# --- month tier: standing terrain, context not trigger ----------------------
def test_terrain_counts_recurring_walls(tmp_path):
    for i, day in enumerate(("2026-08-04", "2026-08-05", "2026-08-06")):
        _diary_day(tmp_path, day, [7700.0 + i, 7710.0 + i])
    t = RAG.terrain(rebuild=True)
    assert t["sessions"] == 3
    assert t["standing"]["all_time"]["magnets"].get("7750") == 3
    assert t["standing"]["all_time"]["call_walls"].get("7900") == 3
    assert "context, never the trigger" in t["note"]
    # cached for the day after the first build
    assert RAG.terrain()["built"] == t["built"]


def test_terrain_recent_outranks_stale_alltime_levels(tmp_path):
    days = [f"2026-07-{d:02d}" for d in range(1, 9 + RAG.RECENT_SESSIONS)]
    for i, day in enumerate(days):
        old = i < 8
        _diary_day(tmp_path, day, [7700.0 + i, 7710.0 + i],
                   magnet=7600.0 if old else 7800.0)
        if not old:                       # recent sessions live at 7800 walls
            p = tmp_path / "reversion" / f"{day}.jsonl"
            rows = [json.loads(ln) for ln in p.read_text().splitlines()]
            for r in rows:
                r["call_wall"], r["put_wall"] = 7850.0, 7700.0
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    t = RAG.terrain(rebuild=True)
    rec = t["standing"]["recent"]
    assert rec["magnets"].get("7800") == RAG.RECENT_SESSIONS
    assert "7600" not in rec["magnets"] and "7600" not in rec["call_walls"]
    assert t["standing"]["all_time"]["magnets"].get("7600") == 8
    assert "7800 has been the magnet" in t["narrative"]


# --- the numeric series (the diary IS the plain store) ----------------------
def test_series_buckets_the_diary_by_time(tmp_path):
    _diary_day(tmp_path, "2026-08-06", [7700.0, 7660.0, 7640.0])
    out = RAG.series(date="2026-08-06", t_from="09:00", t_to="12:00",
                     step_min=10)
    assert [r["spot"] for r in out["rows"]] == [7700.0, 7660.0, 7640.0]
    assert all(r["magnet"] == 7750.0 for r in out["rows"])


def test_series_is_queryable_by_strike_too(tmp_path):
    p = tmp_path / "reversion"
    p.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps({
        "ts": f"2026-08-06T{10 + i:02d}:00:00-04:00", "ticker": "SPX",
        "spot": 7700.0 + i, "call_wall": 7900.0, "put_wall": 7550.0,
        "gex_views": {"magnet": 7750.0,
                      "mass_by_strike": [[7750.0, 50.0 + i]],
                      "vol_gross_by_strike": [[7750.0, 1000 * (i + 1)]]}})
        for i in range(3)]
    (p / "2026-08-06.jsonl").write_text("\n".join(rows) + "\n")
    out = RAG.series(date="2026-08-06", strike=7750.0)
    assert out["strike"] == 7750.0
    assert [r["gamma_mass_at_strike"] for r in out["rows"]] == [50.0, 51.0, 52.0]
    assert [r["vol_gross_at_strike"] for r in out["rows"]] == [1000, 2000, 3000]
    # a strike outside the recorded window stays absent, never zero
    out2 = RAG.series(date="2026-08-06", strike=7800.0)
    assert all("gamma_mass_at_strike" not in r for r in out2["rows"])


def test_diary_day_keeps_only_spx_rth_rows(tmp_path):
    """History must never hand the model tape the reader ignores: another
    ticker's rows and off-RTH rows stay out of summaries/series."""
    p = tmp_path / "reversion"
    p.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-08-06T23:31:00-04:00", "ticker": "SPX", "spot": 7800.0,
         "gex_views": {"magnet": 7750.0}},               # off-hours row
        {"ts": "2026-08-06T10:31:00-04:00", "ticker": "QQQ", "spot": 500.0,
         "gex_views": {"magnet": 505.0}},                # another ticker
        {"ts": "2026-08-06T09:31:00-04:00", "ticker": "SPX", "spot": 7712.7,
         "gex_views": {"magnet": 7750.0}},
        {"ts": "2026-08-06T15:59:00-04:00", "ticker": "SPX", "spot": 7696.4,
         "gex_views": {"magnet": 7750.0}},
    ]
    (p / "2026-08-06.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    live = RAG._diary_day("2026-08-06")
    assert [r["spot"] for r in live] == [7712.7, 7696.4]
    s = RAG.build_summary("2026-08-06")
    assert s["meta"]["open"] == 7712.7 and s["meta"]["high"] == 7712.7


# --- misc guards ------------------------------------------------------------
def test_time_filters_pad_unpadded_hours(tmp_path):
    assert RAG._hhmm("9:30") == "09:30"
    assert RAG._hhmm("15:00") == "15:00"
    assert RAG._hhmm(None) is None
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-08-07", t_from="9:30")
    assert out["n_matched"] == 3


def test_n_matched_reports_the_filter_count_not_the_page(tmp_path):
    _seed_slices()
    out = RAG.query(tier="slices", date="2026-08-07", limit=1)
    assert out["n_matched"] == 3 and len(out["results"]) == 1


def test_lexical_scorer_prefers_overlap():
    a = RAG._lex_score("rejected the call wall", "rejected the call wall and pinned")
    b = RAG._lex_score("rejected the call wall", "pinned to vwap all afternoon")
    assert a > b


def test_forced_rebuild_retires_a_phantom_session(tmp_path):
    p = tmp_path / "reversion"
    p.mkdir(parents=True, exist_ok=True)
    (p / "2026-08-05.jsonl").write_text(json.dumps(
        {"ts": "2026-08-05T23:31:00-04:00", "ticker": "SPX", "spot": 7800.0,
         "gex_views": {"magnet": 7750.0}}) + "\n")
    RAG._rag_dir().mkdir(parents=True, exist_ok=True)
    RAG._summaries_path().write_text(json.dumps(
        {"kind": "day_summary", "date": "2026-08-05", "rag_v": 1,
         "meta": {"open": 7800.0}, "narrative": "phantom"}) + "\n")
    assert RAG.rollup("2026-08-05", force=True) == ["2026-08-05"]
    assert all(s["date"] != "2026-08-05" for s in RAG._summaries())
