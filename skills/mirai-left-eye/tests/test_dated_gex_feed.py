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

def _synthetic_result(spot=7550.0, as_calls=True):
    contracts = []
    for band, exp in (("monthly", "2026-07-17"), ("monthly", "2026-08-21"),
                      ("quarter_end", "2026-09-30")):
        root = "SPX" if band == "monthly" else "SPXW"
        for k in (6500.0, 7000.0, 7500.0, 7550.0, 8000.0, 8500.0):
            contracts.append({"right": "put", "strike": k, "expiry": exp, "root": root,
                              "band": band, "iv": 0.18,
                              "open_interest": 400000 if k in (7000.0, 8000.0) else 5000,
                              "gamma": 0.0001, "vega": 1.2})
            contracts.append({"right": "call", "strike": k, "expiry": exp, "root": root,
                              "band": band, "iv": 0.17,
                              "open_interest": 300000 if k == 8000.0 else 4000,
                              "gamma": 0.0001, "vega": 1.2})
    return {"spot": spot, "contracts": contracts, "calls": 9,
            "expiry_map": {"2026-07-17": "2026-07-17", "2026-08-21": "2026-08-21",
                           "2026-09-30": "2026-09-30"}}


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


def test_quarterly_cadence(monkeypatch):
    # missing quarterly band => due; present + far + midweek => not due
    assert dg._quarterly_due(_dt(2026, 7, 8), {}) is True             # Wednesday, no book
    prev = {"spot": 7550.0, "bands": [{"band": "quarter_end", "expiry": "2026-09-30",
                                       "top_calls": [[8600.0, 9000]], "top_puts": [[6100.0, 8000]]}]}
    assert dg._quarterly_due(_dt(2026, 7, 8), prev) is False          # Wednesday, covered
    assert dg._quarterly_due(_dt(2026, 7, 6), prev) is True           # Monday
    assert dg._quarterly_due(_dt(2026, 9, 21), prev) is True          # dte<=14
    near = {"spot": 7550.0, "bands": [{"band": "quarter_end", "expiry": "2026-09-30",
                                       "top_calls": [[7600.0, 9000]], "top_puts": []}]}
    assert dg._quarterly_due(_dt(2026, 7, 8), near) is True           # wall within 1% of spot


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


def test_all_dated_contracts_capped_out_of_live_tenors():
    """The fence the whole design hangs on: dated expiries are ALWAYS dte>7 at
    pull time except the in-window front monthly — and even that one reaches the
    live engine only via native_gex_feed's own fetch, never via this store."""
    exps = dg.third_fridays(_dt(2026, 7, 20), n=2) + [dg.quarter_end_expiry(_dt(2026, 7, 20))]
    assert all((e - date(2026, 7, 20)).days > 7 for e in exps)
