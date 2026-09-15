"""Row schema (the pinned viewstation contract) + the scanner's kill switch."""
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
# ROW_V 3 (2026-08-02): + atm_iv, vwap, iv_skew, flows_front (payload-v2
# sources — additive, nothing pre-existing re-based).
# ROW_V 4 (2026-08-19): + range_em, and regime/regime_conf RE-BASED onto it
# (the tape voter was the probationary, un-prorated em_consumed).
ROW_KEYS = {"ts", "ticker", "spot", "gex_source", "gamma_sign", "gamma_flip",
            "call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor",
            "sigma", "sigma_live", "sigma_anchor", "prior_close",
            "atm_iv", "vwap", "iv_skew", "flows_front",
            "range_em", "regime", "regime_conf", "regime_reads",
            "gex_views", "dex_views", "profile_ladder", "adaptive_em",
            "net_exposure", "range_ruler", "meta"}
GEX_VIEWS_KEYS = {"magnet", "flip", "regime", "net_by_strike", "oi_by_strike",
                  "vol_by_strike", "oi_side_by_strike", "vol_side_by_strike",
                  "vol_gross_by_strike", "mass_by_strike", "pin_centroid",
                  "pin_zones", "net_by_strike_tenor", "source", "front_dte"}
META_KEYS = {"expiries", "spot_source", "coverage"}


def _row(book=None, **kw):
    return sndk_views.build_row(
        book or synth.prepared_book(), synth.SPOT, NOW,
        spot_source="schwab_quote", chain_meta=CHAIN_META, chain_spot=1088.5,
        prior_close=1240.0, day_open=1245.0, day_high=1260.0, day_low=1235.0,
        **kw)


def _leaning_book():
    """The stub book with its open interest leaning: calls above spot, puts
    below. The even book nets dealer gamma to nothing and draws no flip; this
    one changes sign at spot, so the flip and the ladder hung off it exist."""
    book = synth.prepared_book()
    for c in book:
        above = c["strike"] > synth.SPOT
        c["open_interest"] = 400 if above == (c["right"] == "call") else 100
    return book


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
    # walls are part of "a full map": a Monday row that lost them would still
    # pass an `is None or` check, so both must be there and on their own side
    assert row["call_wall"] is not None and row["call_wall"] >= synth.SPOT - 5
    assert row["put_wall"] is not None and row["put_wall"] <= synth.SPOT + 5
    # σ derived from the rebuilt ATM IV: spot·iv/√252 ≈ 1250·0.5/15.87 ≈ 39
    assert row["sigma"] and 20 < row["sigma"] < 80


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
    from lefteye_range_ruler import DEBIAS, _atm_strike, _leg_price
    row = _row(book=_leaning_book())
    assert row["dex_views"] and row["dex_views"]["net_dex_by_strike"]
    # the ladder hangs off the row's own flip, drawn in SNDK price space
    assert abs(row["gamma_flip"] - synth.SPOT) < 0.10 * synth.SPOT
    assert row["profile_ladder"]["ladder_v"] == 1
    assert row["profile_ladder"]["hvl"] == row["gamma_flip"]
    rr = row["range_ruler"]
    assert rr["em_points"] and rr["em_points"] > 0
    # √time scaling: the 4-DTE straddle must be scaled DOWN to today's move.
    # (The exact factor rests on the holiday calendar at run time; the Thursday
    # and expiry-day tests pin it where the calendar cannot move it.)
    assert rr["anchor"].endswith("_scaled")
    front = [c for c in synth.prepared_book() if c["dte"] == 4]
    _k, call, put = _atm_strike(front, synth.SPOT)
    assert rr["em_points"] < DEBIAS * (_leg_price(call)[0] + _leg_price(put)[0])
    # day OHL provided → consumption reads: the larger leg off the 1245 open
    # (1260 - 1245 = 15) over the morning's anchor, which on the first row of
    # the day is this row's own expected move
    assert rr["spent_pts"] == 15.0
    assert rr["em_open"] == rr["em_points"]
    assert rr["em_consumed"] == round(15.0 / rr["em_open"], 4)


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


# --- the scanner's kill switch (its live edges are in test_live_edges.py) ------
def test_hunter_kill_switch(monkeypatch):
    """SNDK_PRO_DISABLE=1 stops the scanner in Python too: even forced, with a
    live quote to hand, a tick asks for no quote, pulls no book and writes no row."""
    monkeypatch.setenv("SNDK_PRO_DISABLE", "1")
    asked = []
    monkeypatch.setattr(sndk_hunter, "_quote", lambda: asked.append("quote") or {
        "spot": 1250.0, "open": 1245.0, "high": 1260.0, "low": 1235.0,
        "prior_close": 1240.0})
    monkeypatch.setattr(sndk_feed, "sndk_chain",
                        lambda now, live_spot=None: asked.append("chain"))
    assert sndk_hunter.tick(NOW, force=True) == 0
    assert asked == []
    assert not (sndk_hunter._diary_dir() / f"{NOW.date().isoformat()}.jsonl").exists()


# --- ROW_V 3 payload-v2 sources (2026-08-02) --------------------------------
def test_vwap_rides_the_row_when_bars_carry_volume():
    # lopsided wicks, so the typical price (H+L+C)/3 is not the close: a VWAP
    # weighted on closes would read 1257.5 here
    bars = [{"high": 1256.0, "low": 1248.0, "close": 1250.0, "volume": 100},
            {"high": 1262.0, "low": 1255.0, "close": 1260.0, "volume": 300}]
    row = _row(intraday_bars=bars)
    typical = ((1256 + 1248 + 1250) / 3 * 100 + (1262 + 1255 + 1260) / 3 * 300) / 400
    assert row["vwap"] == round(typical, 4)      # 1257.0833
    # a bar with no volume carries no weight rather than dragging the average
    mixed = bars + [{"high": 1300.0, "low": 1290.0, "close": 1295.0, "volume": 0}]
    assert _row(intraday_bars=mixed)["vwap"] == round(typical, 4)
    assert _row(intraday_bars=None)["vwap"] is None
    unvolumed = [{"high": 1252.0, "low": 1248.0, "close": 1250.0, "volume": 0}]
    assert _row(intraday_bars=unvolumed)["vwap"] is None


def test_iv_skew_reads_the_rebuilt_smile():
    """The synth book prices every quote at ONE flat IV, so the rebuilt smile
    must read ~balanced — a skew appearing here would be manufactured."""
    row = _row()
    sk = row["iv_skew"]
    assert sk is not None and sk["skew_v"] == 1
    assert sk["n_down"] >= 3 and sk["n_up"] >= 3
    assert abs(sk["down_share"] - 0.5) < 0.03    # flat smile → ~0.5 split
    assert sk["source"] == "bs_mid quotes"


def test_iv_skew_absent_without_clean_solves():
    b = synth.prepared_book()
    for c in b:
        c.pop("iv_src", None)                    # no clean quote solves at all
    row = sndk_views.build_row(b, synth.SPOT, NOW, spot_source="schwab_quote",
                               chain_meta=CHAIN_META)
    assert row["iv_skew"] is None                # absent, never a 0.5 stand-in


def test_flows_front_charm_vanna_on_the_front_book():
    """A call and a put at one strike carry the same charm and vanna (r = 0),
    so the stub book's equal open interest on both rights nets both totals to
    exactly zero — which any float passes. Put all the open interest on one
    right and the dealer sign convention (calls +, puts -) must flip both
    totals, and only the front weekly's contracts may be counted."""
    def flows(empty_right):
        book = synth.prepared_book()
        for c in book:
            if c["right"] == empty_right:
                c["open_interest"] = 0
        return sndk_views.build_row(book, synth.SPOT, NOW, spot_source="schwab_quote",
                                    chain_meta=CHAIN_META)["flows_front"]
    calls, puts = flows("put"), flows("call")
    n_front_strikes = len({c["strike"] for c in synth.prepared_book() if c["dte"] == 4})
    for fl in (calls, puts):
        assert fl["clock"] == "front_book" and fl["flows_v"] == 1
        assert fl["basis"] == "open_interest"
        assert fl["n"] == n_front_strikes        # one right per strike, next week never
        assert fl["charm_wall"] is not None
        json.dumps(fl)                           # uncalibrated, but it must serialize
    assert calls["cex"] != 0.0 and calls["vex"] != 0.0
    assert abs(calls["cex"] + puts["cex"]) < 0.05
    assert abs(calls["vex"] + puts["vex"]) < 0.05


def test_atm_iv_recorded_beside_sigma():
    """Every quote is priced at synth.TRUE_IV, so the recorded ATM IV is that
    rebuilt vol and never the provider's (0.52 and 0.55 at the money here).
    sigma_live = spot·iv/√252 off it: both are kept to 4 places, which on a
    1250 stock moves the product by under 0.004."""
    row = _row()
    assert abs(row["atm_iv"] - synth.TRUE_IV) < 0.01
    assert abs(row["sigma_live"] - row["spot"] * row["atm_iv"] / math.sqrt(252.0)) < 0.005


# --- range_em: the regime stack's tape voter (ROW_V 4) ----------------------

def _row_at(now, **kw):
    return sndk_views.build_row(
        synth.prepared_book(), synth.SPOT, now,
        spot_source="schwab_quote", chain_meta=CHAIN_META,
        prior_close=1240.0, day_open=1245.0, **kw)


def test_range_em_prorates_against_elapsed_session():
    # NOW is 11:00 ET → 90 min elapsed; 80 pts of range on a 100-pt σ ruler
    v = sndk_views._range_em(1300.0, 1220.0, 100.0, NOW)
    assert abs(v - 80.0 / (100.0 * math.sqrt(90.0 / 390.0))) < 1e-9


def test_range_em_abstains_inside_the_warmup_window():
    early = datetime(2026, 7, 27, 9, 45, tzinfo=ET)          # 15 min in
    assert sndk_views._range_em(1300.0, 1220.0, 100.0, early) is None
    at_bar = datetime(2026, 7, 27, 9, 50, tzinfo=ET)         # exactly 20 min
    assert sndk_views._range_em(1300.0, 1220.0, 100.0, at_bar) is not None


def test_range_em_degrades_to_none_never_to_zero():
    for bad in ((None, 1220.0, 100.0), (1300.0, None, 100.0),
                (1300.0, 1220.0, None), (1300.0, 1220.0, 0.0),
                (1220.0, 1300.0, 100.0)):        # inverted high/low
        assert sndk_views._range_em(*bad, NOW) is None


def test_regime_no_longer_gates_on_probationary_em_consumed():
    """2026-08-19: em_consumed carries no time proration, so it starts near zero
    every morning and only grows — it voted "pin" all morning whatever the tape
    did, printing regime "pinning" at confidence 1.0 while SNDK fell 42 points
    (-2.47%) in four minutes. The tape voter is range_em now, and inside the
    warm-up window it abstains rather than guessing off three minutes of tape."""
    open_tick = datetime(2026, 7, 27, 9, 33, tzinfo=ET)      # 3 min in
    row = _row_at(open_tick, day_high=1260.0, day_low=1180.0)
    assert row["range_ruler"]["em_consumed"] is not None     # the old voter spoke…
    assert row["range_em"] is None                           # …the new one abstains
    assert row["regime_reads"]["range_em"] is None
    # gamma alone cannot reach the ">=2 reads must agree" bar
    assert row["regime"] == "neutral"


def test_range_em_votes_trend_on_an_expanded_tape():
    row = _row_at(NOW, day_high=1400.0, day_low=1100.0)
    assert row["range_em"] > 1.0
    assert row["regime_reads"]["range_em"] == "trend"


def test_range_em_votes_pin_on_a_compressed_tape():
    row = _row_at(NOW, day_high=1252.0, day_low=1248.0)
    assert row["range_em"] <= 0.70
    assert row["regime_reads"]["range_em"] == "pin"



# ---------------------------------------------------------------- history-1 (2026-09-05): the next book
def test_the_next_books_panes_ride_the_row():
    """The stub prices every contract with the same counts, so next week's are
    marked here: a pane read off the wrong expiry cannot pass."""
    book = synth.prepared_book()
    for c in book:
        if c["dte"] == 11:
            c["open_interest"], c["volume"] = 777, 333
    row = sndk_views.build_row(book, synth.SPOT, NOW, spot_source="schwab_quote",
                               chain_meta=CHAIN_META)
    gv = row["gex_views"]
    assert gv["next_dte"] == 11
    assert gv["oi_side_by_strike_next"] and gv["vol_side_by_strike_next"]
    # [strike, calls, puts]: next week's own counts, and the front book's untouched
    assert {(c, p) for _, c, p in gv["oi_side_by_strike_next"]} == {(777, 777)}
    assert {(c, p) for _, c, p in gv["vol_side_by_strike_next"]} == {(333, 333)}
    assert {(c, p) for _, c, p in gv["oi_side_by_strike"]} == {(200, 200)}
    # the same grid, windowed the same way
    assert ({k for k, _, _ in gv["oi_side_by_strike_next"]}
            == {k for k, _, _ in gv["oi_side_by_strike"]})


def test_a_one_expiry_chain_has_no_next_book():
    book = [c for c in synth.prepared_book() if c.get("dte") == 4]
    row = sndk_views.build_row(book, synth.SPOT, NOW, spot_source="schwab_quote",
                               chain_meta=CHAIN_META, chain_spot=1088.5, prior_close=1240.0,
                               day_open=1245.0, day_high=1260.0, day_low=1235.0)
    gv = row["gex_views"]
    assert gv["next_dte"] is None and gv["oi_side_by_strike_next"] is None and gv["vol_side_by_strike_next"] is None
