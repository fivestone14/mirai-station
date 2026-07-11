"""Tests for dated_gex_feed — the DATED BOOK SIDECAR (nightly far-dated OI walls).

Pure logic + store semantics, no network (the one cass_market_run round-trip is
monkeypatched). Covers the calendar rolls the design review specified, the
uw_periscope store semantics (never-overwrite-good-with-bad, never-regress,
staleness floor), the kill switch on BOTH sides, the magnitude-only fence, and
the blast-radius guarantees (native chain cache + build_views untouched)."""
import json
import math
import os
import sys
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dated_gex_feed as dg  # noqa: E402

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("DATED_BOOK_DISABLE", raising=False)


def _dt(y, m, d, hh=5, mm=17):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# --- calendar -----------------------------------------------------------------

def test_third_friday_rolls_across_opex():
    # design-review spec: 07-16/07-17/07-18 => [Jul17,Aug21]/[Jul17,Aug21]/[Aug21,Sep18]
    assert dg.third_fridays(_dt(2026, 7, 16)) == [date(2026, 7, 17), date(2026, 8, 21)]
    assert dg.third_fridays(_dt(2026, 7, 17)) == [date(2026, 7, 17), date(2026, 8, 21)]
    assert dg.third_fridays(_dt(2026, 7, 18)) == [date(2026, 8, 21), date(2026, 9, 18)]


def test_opex_day_monthly_dropped_after_the_open():
    # the AM monthly settles at the 9:30 print — the Friday-evening roll re-pull
    # (14:10 PT fire) must fetch the NEW front monthlies, not the dead one
    assert dg.third_fridays(_dt(2026, 7, 17, 5, 17))[0] == date(2026, 7, 17)
    assert dg.third_fridays(_dt(2026, 7, 17, 14, 10)) == [date(2026, 8, 21), date(2026, 9, 18)]


def test_third_friday_december_wraps_to_next_year():
    assert dg.third_fridays(_dt(2026, 12, 20)) == [date(2027, 1, 15), date(2027, 2, 19)]


def test_third_friday_holiday_steps_back(monkeypatch):
    # synthetic: declare the computed 3rd Friday a full-closure holiday
    tf = date(2026, 8, 21)
    monkeypatch.setattr(dg, "_holidays", lambda y: frozenset({tf}))
    assert dg._third_friday(2026, 8) == date(2026, 8, 20)   # Thursday prior


def test_quarter_end_expiry():
    assert dg.quarter_end_expiry(_dt(2026, 7, 10)) == date(2026, 9, 30)   # Wednesday
    assert dg.quarter_end_expiry(_dt(2026, 10, 2)) == date(2026, 12, 31)  # Thursday
    # 2027-01-02: Q1-2027 ends Wed Mar 31 2027
    assert dg.quarter_end_expiry(_dt(2027, 1, 4)) == date(2027, 3, 31)


# --- pull + store semantics -----------------------------------------------------

_ALL_BANDS = (("monthly", "2026-07-17"), ("monthly", "2026-08-21"),
              ("quarter_end", "2026-09-30"))


def _synthetic_result(spot=7550.0, bands=_ALL_BANDS, strikes=(6500.0, 7000.0, 7500.0, 7550.0, 8000.0, 8500.0)):
    contracts = []
    for band, exp in bands:
        root = "SPX" if band == "monthly" else "SPXW"
        for k in strikes:
            contracts.append({"right": "put", "strike": k, "expiry": exp, "root": root,
                              "band": band, "iv": 0.18,
                              "open_interest": 400000 if k in (7000.0, 8000.0) else 5000})
            contracts.append({"right": "call", "strike": k, "expiry": exp, "root": root,
                              "band": band, "iv": 0.17,
                              "open_interest": 300000 if k == 8000.0 else 4000})
    return {"spot": spot, "contracts": contracts, "calls": 9,
            "expiry_map": {e: e for _b, e in bands}}


def test_per_strike_gex_components(monkeypatch):
    """2026-07-10: strikes rows carry UNSIGNED call/put dollar-gamma components
    ([[k, call_oi, put_oi, call_gex, put_gex]]) whose sum must reproduce the
    band's gamma_mass total — same formula, split by side, no signed field."""
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    book = json.load(open(dg._book_path()))
    for b in book["bands"]:
        assert all(len(r) == 5 for r in b["strikes"])
        assert all(r[3] >= 0 and r[4] >= 0 for r in b["strikes"])
        gex_sum = sum(r[3] + r[4] for r in b["strikes"])
        assert b["gamma_mass"] is not None
        assert abs(gex_sum - b["gamma_mass"]) / max(b["gamma_mass"], 1) < 1e-6
        # independent recompute of one strike (calls side, k=7000, iv .17):
        from lefteye_gex_box import _bs_gamma, _TRADING_DAYS
        exp_d = date.fromisoformat(b["expiry"])
        tau = max((exp_d - date(2026, 7, 10)).days, 1) / _TRADING_DAYS
        spot = book["spot"]
        row = [r for r in b["strikes"] if r[0] == 7000.0][0]
        want = abs(_bs_gamma(spot, 7000.0, 0.17, tau)) * row[1] * 100.0 * spot * spot * 0.01
        assert abs(row[3] - want) / max(want, 1) < 1e-6


def test_parity_iv_rescue_fills_itm_gex(monkeypatch):
    """The 07-10 audit case: a deep-ITM put arrives iv=0.0 with big OI — its gex
    must be rescued from the same-strike call twin, never silently zeroed."""
    res = _synthetic_result()
    for c in res["contracts"]:
        if c["right"] == "put" and c["strike"] == 8000.0 and c["expiry"] == "2026-07-17":
            c["iv"] = 0.0                                 # the broken quote
    monkeypatch.setattr(dg, "_run_code", lambda code: res)
    dg.pull(_dt(2026, 7, 10))
    book = json.load(open(dg._book_path()))
    assert book["meta"]["iv_rescued"] == 1
    b = [x for x in book["bands"] if x["expiry"] == "2026-07-17"][0]
    row = [r for r in b["strikes"] if r[0] == 8000.0][0]
    assert row[4] > 0                                     # put gex present (twin iv 0.17)


def test_pull_writes_book_with_walls(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    st = dg.pull(_dt(2026, 7, 10))
    assert st["ok"] and st["bands"] == 3
    book = json.load(open(dg._book_path()))
    assert book["ok"] and book["landed_date"] == "2026-07-10"
    m = [b for b in book["bands"] if b["expiry"] == "2026-07-17"][0]
    assert m["put_wall"] in (7000.0, 8000.0)     # biggest put-OI strike
    assert m["call_wall"] == 8000.0
    assert m["gamma_mass"] is not None and m["gamma_mass"] >= 0
    assert m["vanna_mass"] is not None and m["vanna_mass"] >= 0
    assert book["meta"]["round_strike_check"]["missing"] == []


def test_pull_failure_keeps_previous_good_book(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    good = json.load(open(dg._book_path()))
    monkeypatch.setattr(dg, "_run_code", lambda code: None)   # dead feed
    st = dg.pull(_dt(2026, 7, 13))
    assert not st["ok"] and st["kept_previous"]
    assert json.load(open(dg._book_path())) == good           # untouched


def test_pull_never_regresses_to_older(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 13))
    st = dg.pull(_dt(2026, 7, 10))                            # replayed older job
    assert not st["ok"] and st.get("regress_blocked")
    assert json.load(open(dg._book_path()))["as_of"].startswith("2026-07-13")


def test_pull_landed_date_stamped_once(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    dg.pull(_dt(2026, 7, 13))
    assert json.load(open(dg._book_path()))["landed_date"] == "2026-07-10"


def test_pull_skips_weekend_and_disable(monkeypatch):
    called = []
    monkeypatch.setattr(dg, "_run_code", lambda code: called.append(1))
    assert dg.pull(_dt(2026, 7, 11))["skipped"] == "market_closed"   # Saturday
    monkeypatch.setenv("DATED_BOOK_DISABLE", "1")
    assert dg.pull(_dt(2026, 7, 10))["skipped"] == "disabled"
    assert not called                                                # zero network


def _qprev(as_of="2026-07-06", top_calls=None, top_puts=None, spot=7550.0):
    return {"spot": spot, "bands": [{"band": "quarter_end", "expiry": "2026-09-30",
                                     "as_of": as_of + "T05:17:00-04:00",
                                     "top_calls": top_calls if top_calls is not None else [[8600.0, 9000]],
                                     "top_puts": top_puts if top_puts is not None else [[6100.0, 8000]]}]}


def test_quarterly_cadence():
    assert dg._quarterly_due(_dt(2026, 7, 8), {}) is True                    # no book
    assert dg._quarterly_due(_dt(2026, 7, 8), _qprev("2026-07-06")) is False  # covered, 2d old
    assert dg._quarterly_due(_dt(2026, 7, 13), _qprev("2026-07-06")) is True  # age >= 7d (weekly)
    # midweek WITHIN the final-2-weeks window — the dte<=14 branch alone fires
    # (2026-09-23 is a Wednesday, dte 7 to the 09-30 expiry, age only 2d)
    assert dg._quarterly_due(_dt(2026, 9, 23), _qprev("2026-09-21")) is True
    assert dg._quarterly_due(_dt(2026, 7, 8),
                             _qprev("2026-07-06", top_calls=[[7600.0, 9000]], top_puts=[])) is True  # wall ≤1%
    assert dg._quarterly_due(_dt(2026, 7, 8), _qprev("2026-07-06", spot=None)) is True   # unknown spot → refresh
    unstamped = {"spot": 7550.0, "bands": [{"band": "quarter_end", "expiry": "2026-09-30",
                                            "top_calls": [], "top_puts": []}]}
    assert dg._quarterly_due(_dt(2026, 7, 8), unstamped) is True             # unstamped band → refresh


# --- diary_summary ---------------------------------------------------------------

def test_summary_shape_and_staleness(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    s = dg.diary_summary(_dt(2026, 7, 10, 11, 0))
    assert s["staleness"] == "fresh" and s["landed_date"] == "2026-07-10"
    assert {b["band"] for b in s["bands"]} == {"monthly", "quarter_end"}
    assert all(b["dte"] >= 0 for b in s["bands"])
    s1 = dg.diary_summary(_dt(2026, 7, 11))
    assert s1["staleness"] == "stale_1d"
    assert dg.diary_summary(_dt(2026, 7, 20)) is None        # > 5d floor => absent


def test_summary_drops_expired_bands(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    s = dg.diary_summary(_dt(2026, 7, 14))
    assert {b["expiry"] for b in s["bands"]} == {"2026-07-17", "2026-08-21", "2026-09-30"}
    s2 = dg.diary_summary(_dt(2026, 7, 15))                  # within floor, all live
    assert s2 is not None
    # July monthly gone once past it (book 5d stale on 07-15; use fresh re-pull instead)
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 20))
    s3 = dg.diary_summary(_dt(2026, 7, 20))
    assert "2026-07-17" not in {b["expiry"] for b in s3["bands"]}


def test_summary_kill_switch_and_missing(monkeypatch):
    assert dg.diary_summary(_dt(2026, 7, 10)) is None        # no book on disk
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    monkeypatch.setenv("DATED_BOOK_DISABLE", "1")
    assert dg.diary_summary(_dt(2026, 7, 10)) is None        # reader side silenced


def test_summary_never_fetches(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    def _boom(code):
        raise AssertionError("diary_summary must never reach the network")
    monkeypatch.setattr(dg, "_run_code", _boom)
    assert dg.diary_summary(_dt(2026, 7, 10)) is not None    # disk-only


def test_summary_survives_corrupt_book(monkeypatch):
    p = dg._book_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert dg.diary_summary(_dt(2026, 7, 10)) is None


# --- magnitude-only fence + blast radius ----------------------------------------

_BANNED_KEY_PARTS = ("sign", "net_gex", "direction", "bias", "regime")


def _walk_keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k).lower())
            _walk_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_keys(v, out)


def test_magnitude_only_fence(monkeypatch):
    """HARD FENCE: no signed-conviction field anywhere in the book or the diary
    key (assumed-sign disagrees with flow-inferred sign ~44% — dated data is
    location+magnitude only until a gated A/B says otherwise)."""
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    keys = []
    _walk_keys(json.load(open(dg._book_path())), keys)
    _walk_keys(dg.diary_summary(_dt(2026, 7, 10)), keys)
    for k in keys:
        assert not any(p in k for p in _BANNED_KEY_PARTS), f"signed-ish key leaked: {k}"
    # and every magnitude is non-negative
    s = dg.diary_summary(_dt(2026, 7, 10))
    for b in s["bands"]:
        for fld in ("gamma_mass", "vanna_mass", "call_oi_total", "put_oi_total"):
            assert b[fld] is None or b[fld] >= 0


def test_blast_radius_native_cache_untouched(monkeypatch):
    import native_gex_feed as nf
    nf.reset_chain_cache()
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    assert nf._CHAIN_CACHE == {}                              # sidecar never touches it


def test_blast_radius_build_views_unchanged(monkeypatch):
    """build_views output must be byte-identical with and without a populated
    dated book on disk — the dated store has no path into the live engine."""
    import lefteye_gex_box as gv
    chain = []
    for k in range(7400, 7701, 10):
        g = 0.003 * math.exp(-((k - 7550) / 40) ** 2)
        for right in ("call", "put"):
            chain.append({"right": right, "strike": float(k), "dte": 0, "gamma": g,
                          "open_interest": 200, "volume": 300, "iv": 0.15})
    before = gv.build_views(chain, 7550.0, minutes_to_close=120.0)
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    after = gv.build_views(chain, 7550.0, minutes_to_close=120.0)
    assert before == after


def test_dated_expiries_beyond_live_window_after_opex_roll():
    """Post-roll property: on the first day after a monthly OpEx, every sidecar
    expiry sits beyond the live 0-7DTE window. (During OpEx week the front
    monthly IS in-window — but it reaches the live engine only via
    native_gex_feed's own fetch; the store-isolation fence is carried by
    test_blast_radius_build_views_unchanged, not by this date property.)"""
    exps = dg.third_fridays(_dt(2026, 7, 20), n=2) + [dg.quarter_end_expiry(_dt(2026, 7, 20))]
    assert all((e - date(2026, 7, 20)).days > 7 for e in exps)


# --- verification-fleet fix batch (2026-07-10) ----------------------------------

def test_pull_carries_forward_undue_quarterly(monkeypatch):
    """MEDIUM fix: a not-due pull must MERGE, not replace — the quarterly band
    rides forward with its own as_of instead of being deleted until re-due."""
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))                                 # Friday: all 3 bands
    monkeypatch.setattr(dg, "_run_code",
                        lambda code: _synthetic_result(bands=_ALL_BANDS[:2]))  # monthlies only
    st = dg.pull(_dt(2026, 7, 14))                            # Tuesday: quarterly not due
    assert st["ok"] and st["bands"] == 3                      # carried, not deleted
    book = json.load(open(dg._book_path()))
    q = [b for b in book["bands"] if b["band"] == "quarter_end"][0]
    assert q["as_of"].startswith("2026-07-10")                # original pull stamp intact
    m = [b for b in book["bands"] if b["expiry"] == "2026-08-21"][0]
    assert m["as_of"].startswith("2026-07-14")                # re-fetched band restamped


def test_pull_omits_quarterly_when_covered(monkeypatch):
    captured = []
    def _cap(code):
        captured.append(code)
        # HONEST mock: return only the bands the rendered job list actually asked
        # for (a dishonest all-bands mock restamps the carried quarterly's as_of
        # and masks the cadence — the exact hole the verification fleet flagged).
        bands = _ALL_BANDS if "quarter_end" in code else _ALL_BANDS[:2]
        # no strike near spot 7550: the wall-proximity trigger stays quiet so
        # this test isolates the age/coverage cadence alone
        return _synthetic_result(bands=bands, strikes=(6500.0, 7000.0, 8000.0, 8500.0))
    monkeypatch.setattr(dg, "_run_code", _cap)
    dg.pull(_dt(2026, 7, 10))                                 # first pull: quarterly due (no book)
    assert "quarter_end" in captured[-1]
    dg.pull(_dt(2026, 7, 14))                                 # covered + young → omitted
    assert "quarter_end" not in captured[-1]
    dg.pull(_dt(2026, 7, 20))                                 # age >= 7d → back in
    assert "quarter_end" in captured[-1]


def test_pull_never_raises_on_malformed_rows(monkeypatch):
    res = _synthetic_result()
    res["contracts"].insert(0, {"right": "put", "strike": None, "expiry": "2026-08-21",
                                "root": "SPX", "band": "monthly", "open_interest": 100})
    res["contracts"].insert(0, {"right": "??", "strike": 7000.0, "expiry": "2026-08-21",
                                "root": "SPX", "band": "monthly", "open_interest": 100})
    monkeypatch.setattr(dg, "_run_code", lambda code: res)
    st = dg.pull(_dt(2026, 7, 10))                            # malformed rows skipped
    assert st["ok"] and st["bands"] == 3


def test_pull_store_error_logged_not_raised(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    def _boom(path, obj):
        raise OSError("disk full")
    monkeypatch.setattr(dg.atomic_io, "write_json_atomic", _boom)
    st = dg.pull(_dt(2026, 7, 10))                            # NEVER-raises contract
    assert not st["ok"] and "OSError" in st["error"]


def test_pull_skips_holiday(monkeypatch):
    called = []
    monkeypatch.setattr(dg, "_run_code", lambda code: called.append(1))
    monkeypatch.setattr(dg, "_holidays", lambda y: frozenset({date(2026, 7, 10)}))
    assert dg.pull(_dt(2026, 7, 10))["skipped"] == "market_closed"
    assert not called


def test_round_strike_check_flags_clipped_wall(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result(
        strikes=(6500.0, 7000.0, 7550.0, 8000.0, 8500.0)))    # 7500 amputated
    dg.pull(_dt(2026, 7, 10))
    book = json.load(open(dg._book_path()))
    assert book["meta"]["round_strike_check"]["missing"] == [7500.0]


def test_summary_drops_settled_bands_intraday(monkeypatch):
    """OpEx-Friday fix: the AM monthly is DEAD after the 9:30 settlement print —
    the reader must stop displaying it even before the roll re-pull lands."""
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 17, 5, 17))                          # pre-open OpEx Friday pull
    pre = dg.diary_summary(_dt(2026, 7, 17, 9, 0))            # 9:00 ET: still live
    assert "2026-07-17" in {b["expiry"] for b in pre["bands"]}
    post = dg.diary_summary(_dt(2026, 7, 17, 12, 0))          # settled at the open
    assert "2026-07-17" not in {b["expiry"] for b in post["bands"]}
    # PM-settled quarter-end: alive through the session, dead after the close
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result(bands=(("quarter_end", "2026-09-30"),)))
    dg.pull(_dt(2026, 9, 30, 5, 17))
    assert dg.diary_summary(_dt(2026, 9, 30, 15, 0)) is not None
    assert dg.diary_summary(_dt(2026, 9, 30, 16, 30)) is None  # sole band settled → None
