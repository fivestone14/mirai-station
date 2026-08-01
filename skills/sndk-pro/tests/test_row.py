"""Row schema (the pinned viewstation contract) + hunter end-to-end."""
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sndk_feed
import sndk_hunter
import sndk_views
import synth

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 27, 11, 0, tzinfo=ET)           # Monday: front_dte = 4

CHAIN_META = {"expiries": [{"date": "2026-07-31", "dte": 4},
                           {"date": "2026-08-07", "dte": 11}],
              "coverage": {"complete": True, "missing": None,
                           "front_expiry": "2026-07-31", "front_dead": False,
                           "expiry_today": False},
              "chunks": 4, "iv_rebuilt": 100, "iv_kept_provider": 0,
              "iv_clamped": 0, "iv_dropped": 0}

# THE PINNED CONTRACT — the frontend agent builds against these names.
ROW_KEYS = {"ts", "ticker", "spot", "gex_source", "gamma_sign", "gamma_flip",
            "call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor",
            "sigma", "sigma_live", "sigma_anchor", "prior_close",
            "regime", "regime_conf", "regime_reads",
            "gex_views", "dex_views", "profile_ladder", "adaptive_em",
            "net_exposure", "range_ruler", "meta"}
GEX_VIEWS_KEYS = {"magnet", "flip", "regime", "net_by_strike", "oi_by_strike",
                  "vol_by_strike", "oi_side_by_strike", "vol_side_by_strike",
                  "vol_gross_by_strike", "mass_by_strike", "pin_centroid",
                  "pin_zones", "net_by_strike_tenor", "source", "front_dte"}
META_KEYS = {"expiries", "spot_source", "coverage"}


def _row(**kw):
    return sndk_views.build_row(
        synth.prepared_book(), synth.SPOT, NOW,
        spot_source="schwab_quote", chain_meta=CHAIN_META, chain_spot=1088.5,
        prior_close=1240.0, day_open=1245.0, day_high=1260.0, day_low=1235.0,
        **kw)


def test_row_schema_golden():
    row = _row()
    assert set(row.keys()) == ROW_KEYS
    assert GEX_VIEWS_KEYS <= set(row["gex_views"].keys())
    assert META_KEYS <= set(row["meta"].keys())
    assert row["ticker"] == "SNDK"
    assert row["meta"]["spot_source"] == "schwab_quote"
    assert row["meta"]["expiries"][0] == {"date": "2026-07-31", "dte": 4}
    json.dumps(row)                          # every field must serialize


def test_off_expiry_map_is_populated():
    # THE core adaptation: Monday (no 0DTE) must still draw a full map off the
    # front weekly — panes, magnet, walls, sigma all live, all in SNDK scale
    row = _row()
    gv = row["gex_views"]
    assert gv["front_dte"] == 4
    for key in ("net_by_strike", "oi_by_strike", "vol_by_strike",
                "mass_by_strike", "oi_side_by_strike", "vol_side_by_strike"):
        pane = gv[key]
        assert pane and len(pane) >= 5, key  # a handful of $5 strikes minimum
        for entry in pane:
            assert 1000.0 < entry[0] < 1500.0    # SNDK price space, never SPX
    assert gv["magnet"] is not None
    assert abs(gv["magnet"] - synth.SPOT) < 0.10 * synth.SPOT
    assert row["call_wall"] is None or row["call_wall"] >= synth.SPOT - 5
    assert row["put_wall"] is None or row["put_wall"] <= synth.SPOT + 5
    # σ derived from the rebuilt ATM IV: spot·iv/√252 ≈ 1250·0.5/15.87 ≈ 39
    assert row["sigma"] and 20 < row["sigma"] < 80
    assert row["gamma_flip"] is None or 1000 < row["gamma_flip"] < 1500


def test_thursday_open_em_scaled_down():
    # M1: Thu 09:30, front dte=1 → ~2 sessions of front-book life. em_today =
    # straddle × √(τ_day/τ_front) ≈ ×0.707 — the engine's close-anchored τ
    # made the scale 1.0 (EM ×1.41 inflated) at exactly this open.
    from lefteye_range_ruler import DEBIAS, _atm_strike, _leg_price
    now = datetime(2026, 7, 30, 9, 30, tzinfo=ET)        # Thursday, mtc=390
    book = synth.prepared_book(front_dte=1, front_expiry="2026-07-31",
                               second_dte=8, second_expiry="2026-08-07",
                               mtc=390.0)
    row = sndk_views.build_row(book, synth.SPOT, now,
                               spot_source="schwab_quote", chain_meta=None)
    front = [c for c in book if c["dte"] == 1]
    _k, call, put = _atm_strike(front, synth.SPOT)
    straddle = _leg_price(call)[0] + _leg_price(put)[0]
    em = row["range_ruler"]["em_points"]
    assert em is not None
    assert abs(em / (DEBIAS * straddle) - math.sqrt(0.5)) < 0.01


def test_expiry_day_em_unscaled():
    # M1 guard: on expiry Friday the front book IS the 0DTE book — τ_front is
    # the engine's own live clock and the scale factor is exactly 1.0
    from lefteye_range_ruler import DEBIAS, _atm_strike, _leg_price
    now = datetime(2026, 7, 31, 11, 0, tzinfo=ET)        # Friday, mtc=300
    book = synth.prepared_book(front_dte=0, front_expiry="2026-07-31",
                               second_dte=7, second_expiry="2026-08-07",
                               mtc=300.0)
    row = sndk_views.build_row(book, synth.SPOT, now,
                               spot_source="schwab_quote", chain_meta=None)
    front = [c for c in book if c["dte"] == 0]
    _k, call, put = _atm_strike(front, synth.SPOT)
    straddle = _leg_price(call)[0] + _leg_price(put)[0]
    em = row["range_ruler"]["em_points"]
    assert em is not None
    assert abs(em - DEBIAS * straddle) < 0.05            # scale is 1.0, SPX verbatim


def test_dex_ladder_em_ride_the_row():
    row = _row()
    assert row["dex_views"] and row["dex_views"]["net_dex_by_strike"]
    assert row["profile_ladder"] is None or row["profile_ladder"].get("ladder_v") == 1
    rr = row["range_ruler"]
    assert rr["em_points"] and rr["em_points"] > 0
    # √time scaling: the 4-DTE straddle must be scaled DOWN to a 1-day move
    assert rr["anchor"].endswith("_scaled")
    assert rr["em_points"] < 0.6 * synth.SPOT * 0.5 / math.sqrt(252) * 2 * 3
    assert rr["em_consumed"] is not None     # day OHL provided → consumption reads


def test_adaptive_em_from_intraday_bars():
    bars = [{"close": 1245.0 + (i % 7) - 3 - (0.5 * i if i > 30 else 0)}
            for i in range(60)]
    row = _row(intraday_bars=bars)
    aem = row["adaptive_em"]
    assert aem is not None
    assert aem["em_down_pts"] > aem["em_up_pts"]     # sell-off bars → down skew
    row2 = _row(intraday_bars=None)
    assert row2["adaptive_em"] is None       # no bars → honest absence


def test_expiry_day_collapses_to_zero_dte():
    book = synth.prepared_book(front_dte=0, front_expiry="2026-07-31",
                               second_dte=7, second_expiry="2026-08-07")
    row = sndk_views.build_row(book, synth.SPOT,
                               datetime(2026, 7, 31, 11, 0, tzinfo=ET),
                               spot_source="schwab_quote", chain_meta=None)
    gv = row["gex_views"]
    assert gv["front_dte"] == 0
    assert gv["regime_source"] == "0dte"     # the engine's own 0DTE authority
    assert gv["net_by_strike"]


# --- hunter end-to-end (stubbed feed + quote, tmp state) ---------------------

def _stub_chain():
    return {"spot": 1088.5, "anchor_spot": 1250.0,
            "contracts": synth.prepared_book(), "meta": CHAIN_META}


def test_hunter_appends_row(monkeypatch, tmp_path):
    monkeypatch.setattr(sndk_feed, "sndk_chain", lambda now, live_spot=None: _stub_chain())
    monkeypatch.setattr(sndk_hunter, "_quote", lambda: {
        "spot": 1250.0, "open": 1245.0, "high": 1260.0, "low": 1235.0,
        "prior_close": 1240.0})
    monkeypatch.setattr(sndk_hunter, "_market_live", lambda: True)
    import lefteye_fetcher
    monkeypatch.setattr(lefteye_fetcher, "intraday_bars", lambda t: [])
    assert sndk_hunter.tick(NOW) == 0
    path = sndk_hunter._diary_dir() / f"{NOW.date().isoformat()}.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SNDK"
    assert rows[0]["meta"]["spot_source"] == "schwab_quote"
    assert rows[0]["meta"]["forced"] is False            # live tick: not forced (m9)
    assert rows[0]["spot"] == 1250.0
    # second tick appends (and reads the first row as day-memory)
    assert sndk_hunter.tick(NOW) == 0
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[1]["sigma_anchor"] == rows[0]["sigma_anchor"]


def test_hunter_no_quote_no_row(monkeypatch):
    # M3: Schwab down → fail closed. No chain fetch, no row — never a diary
    # entry anchored on (or carrying) the known-stale chain spot.
    called = []
    monkeypatch.setattr(sndk_feed, "sndk_chain",
                        lambda now, live_spot=None: called.append(1) or _stub_chain())
    monkeypatch.setattr(sndk_hunter, "_quote", lambda: {})   # Schwab down
    monkeypatch.setattr(sndk_hunter, "_market_live", lambda: True)
    assert sndk_hunter.tick(NOW) == 0
    assert not called                        # skipped before any chain work
    assert not (sndk_hunter._diary_dir() / f"{NOW.date().isoformat()}.jsonl").exists()


def test_forced_after_hours_row_marked(monkeypatch):
    # m9: a --force run outside RTH must say so — meta.forced separates it
    # from the live pool the viewstation and any grader read
    monkeypatch.setattr(sndk_feed, "sndk_chain", lambda now, live_spot=None: _stub_chain())
    monkeypatch.setattr(sndk_hunter, "_quote", lambda: {
        "spot": 1250.0, "open": None, "high": None, "low": None,
        "prior_close": 1240.0})
    monkeypatch.setattr(sndk_hunter, "_market_live", lambda: False)
    import lefteye_fetcher
    monkeypatch.setattr(lefteye_fetcher, "intraday_bars", lambda t: [])
    assert sndk_hunter.tick(NOW, force=True) == 0
    path = sndk_hunter._diary_dir() / f"{NOW.date().isoformat()}.jsonl"
    row = json.loads(path.read_text().splitlines()[-1])
    assert row["meta"]["forced"] is True


def test_hunter_kill_switch(monkeypatch):
    monkeypatch.setenv("SNDK_PRO_DISABLE", "1")
    called = []
    monkeypatch.setattr(sndk_feed, "sndk_chain",
                        lambda now, live_spot=None: called.append(1))
    assert sndk_hunter.tick(NOW, force=True) == 0
    assert not called


def test_hunter_rth_gate(monkeypatch):
    monkeypatch.setattr(sndk_hunter, "_market_live", lambda: False)
    called = []
    monkeypatch.setattr(sndk_feed, "sndk_chain",
                        lambda now, live_spot=None: called.append(1))
    assert sndk_hunter.tick(NOW) == 0
    assert not called                        # closed → no fetch, no row
    # --force bypasses (the manual proof-run path)
    monkeypatch.setattr(sndk_hunter, "_quote", lambda: {
        "spot": 1250.0, "open": None, "high": None, "low": None,
        "prior_close": 1240.0})
    monkeypatch.setattr(sndk_feed, "sndk_chain", lambda now, live_spot=None: _stub_chain())
    import lefteye_fetcher
    monkeypatch.setattr(lefteye_fetcher, "intraday_bars", lambda t: [])
    assert sndk_hunter.tick(NOW, force=True) == 0
    assert (sndk_hunter._diary_dir() / f"{NOW.date().isoformat()}.jsonl").exists()
