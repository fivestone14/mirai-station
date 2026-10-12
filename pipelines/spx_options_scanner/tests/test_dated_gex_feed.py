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
              ("quarter_end", "2026-09-30"), ("quarter_end", "2026-12-31"))


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
        assert all(len(r) == 7 for r in b["strikes"])   # +call_iv, +put_iv (2026-07-12)
        assert all(r[3] >= 0 and r[4] >= 0 for r in b["strikes"])
        gex_sum = sum(r[3] + r[4] for r in b["strikes"])
        assert b["gamma_mass"] is not None
        assert abs(gex_sum - b["gamma_mass"]) / max(b["gamma_mass"], 1) < 1e-6
        # independent recompute of one strike (calls side, k=7000, iv .17):
        from lefteye_gex_box import _bs_gamma, _TRADING_DAYS, _sessions_ahead
        exp_d = date.fromisoformat(b["expiry"])
        # TRADING time: sessions/252. This recompute used to use a CALENDAR numerator —
        # the bug _sessions_ahead fixed (tau ran up to 1.45x hot, worst over weekends).
        today = date(2026, 7, 10)
        tau = _sessions_ahead((exp_d - today).days, today) / _TRADING_DAYS
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
    assert st["ok"] and st["bands"] == 4
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
    # BOTH expected quarterlies (H8) — coverage now means the pair; the wall/age
    # params exercise the FRONT band, the forward band stays far and fresh
    def _band(exp, tc, tp):
        return {"band": "quarter_end", "expiry": exp, "as_of": as_of + "T05:17:00-04:00",
                "top_calls": tc, "top_puts": tp}
    return {"spot": spot,
            "bands": [_band("2026-09-30",
                            top_calls if top_calls is not None else [[8600.0, 9000]],
                            top_puts if top_puts is not None else [[6100.0, 8000]]),
                      _band("2026-12-31", [[8800.0, 7000]], [[6000.0, 6000]])]}


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
    # H8: a fresh FRONT quarter must not mask a missing FORWARD one
    front_only = _qprev("2026-07-06")
    front_only["bands"] = front_only["bands"][:1]
    assert dg._quarterly_due(_dt(2026, 7, 8), front_only) is True


def test_quarter_end_expiries_pair():
    # H8: the next TWO quarter-ends, so the forward collar never vanishes
    assert dg.quarter_end_expiries(_dt(2026, 7, 10)) == [date(2026, 9, 30), date(2026, 12, 31)]
    assert dg.quarter_end_expiries(_dt(2026, 10, 2)) == [date(2026, 12, 31), date(2027, 3, 31)]


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


def test_summary_per_band_age_gate_and_label(monkeypatch):
    # H9: every band is judged on its OWN pull stamp — a carried week-old wall
    # must not wear the fresh book's sticker. Monthly floor 5d, quarterly 10d;
    # the top-level label reads the OLDEST surviving band, not the book.
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    book = json.load(open(dg._book_path()))
    for b in book["bands"]:               # simulate carried bands: age 8d vs book fresh
        if b["expiry"] in ("2026-08-21", "2026-09-30"):
            b["as_of"] = "2026-07-02T05:17:00-04:00"
    book["bands"] = [b for b in book["bands"] if b["expiry"] != "2026-07-17"] + \
                    [dict(b, as_of=None) for b in book["bands"] if b["expiry"] == "2026-07-17"]
    open(dg._book_path(), "w").write(json.dumps(book))
    s = dg.diary_summary(_dt(2026, 7, 10, 11, 0))
    by_exp = {b["expiry"]: b for b in s["bands"]}
    assert "2026-08-21" not in by_exp                 # monthly @8d > 5d floor → dropped
    assert "2026-07-17" not in by_exp                 # unstamped → unverifiable → dropped
    assert by_exp["2026-09-30"]["age_days"] == 8      # quarterly @8d ≤ 10d floor → kept
    assert by_exp["2026-12-31"]["age_days"] == 0      # fresh band untouched
    assert s["oldest_band_days"] == 8                 # label reads the OLDEST band
    assert s["staleness"] == "stale_8d" and s["stale_days"] == 0


def test_summary_drops_expired_bands(monkeypatch):
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    s = dg.diary_summary(_dt(2026, 7, 14))
    assert {b["expiry"] for b in s["bands"]} == {"2026-07-17", "2026-08-21", "2026-09-30", "2026-12-31"}
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
    assert st["ok"] and st["bands"] == 4                      # carried, not deleted
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
    assert st["ok"] and st["bands"] == 4


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


# --- the HEDGE RAIL profile (2026-07-12) ----------------------------------------
# net dealer GEX ($B per 1% move) repriced across a +/-6% grid, plus the flip and an
# explicit sign-robustness verdict. Computed feed-side because the true per-strike IV
# only exists here — gamma is non-monotone in sigma, so a magnitude-only reader cannot
# recover it without guessing a branch.

def test_profile_shape_and_units():
    legs = [(7500.0, "call", 1000, 0.17), (7500.0, "put", 1000, 0.17),
            (7700.0, "call", 5000, 0.16), (7300.0, "put", 5000, 0.19)]
    p = dg._profile(legs, 7575.0, 30.0 / 252.0)
    assert p is not None
    assert len(p["grid"]) == len(p["gex"]) == dg._PROF_STEPS + 1
    assert p["grid"] == sorted(p["grid"])                     # ascending
    assert abs(p["grid"][0] / 7575.0 - 0.94) < 1e-6           # -6%
    assert abs(p["grid"][-1] / 7575.0 - 1.06) < 1e-6          # +6%
    assert isinstance(p["contested"], bool)                   # REQUIRED, never absent
    assert "flip" in p
    lo, hi = p["h1_range"]                                   # the beta sweep's span
    assert lo <= p["h1"] <= hi or hi <= p["h1"] <= lo
    assert p["contested"] == (lo < 0 < hi)                   # contested IFF the span straddles 0


def test_profile_flip_is_a_real_zero_crossing():
    """A book that is short gamma low and long gamma high must cross zero exactly once."""
    legs = [(7200.0, "put", 40000, 0.20), (7800.0, "call", 40000, 0.15)]
    p = dg._profile(legs, 7575.0, 30.0 / 252.0)
    f = p["flip"]
    assert f is not None and p["grid"][0] < f < p["grid"][-1]
    lo = [g for g, x in zip(p["grid"], p["gex"]) if g < f]
    hi = [x for g, x in zip(p["grid"], p["gex"]) if g > f]
    assert lo and hi
    # sign genuinely inverts across the flip
    below = [x for g, x in zip(p["grid"], p["gex"]) if g < f]
    assert (below[0] < 0) != (hi[-1] < 0)


def test_profile_is_none_without_legs():
    assert dg._profile([], 7575.0, 0.1) is None
    assert dg._profile([(7500.0, "call", 1, 0.2)], 0.0, 0.1) is None
    assert dg._profile([(7500.0, "call", 1, 0.2)], 7575.0, 0.0) is None


def test_profile_rides_the_band(monkeypatch):
    """pull() attaches a profile with all four required keys to every band, and the
    profile's value AT SPOT reproduces the net of the stored per-strike components."""
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    book = json.load(open(dg._book_path()))
    spot = book["spot"]
    for b in book["bands"]:
        p = b["profile"]
        assert p is not None
        assert set(p) == {"grid", "gex", "flip", "contested", "h1", "h1_range"}
        assert isinstance(p["contested"], bool)     # the rail refuses a band without it
        # net GEX at spot, straight from the stored magnitudes (no sigma needed) —
        # the profile must land on it, since it is the same book at the same price
        net = sum(r[3] - r[4] for r in b["strikes"]) / 1e9
        i = min(range(len(p["grid"])), key=lambda j: abs(p["grid"][j] - spot))
        assert abs(p["gex"][i] - net) / max(abs(net), 1e-6) < 0.02


def test_stored_iv_removes_the_sigma_guess(monkeypatch):
    """Every strike row carries the IV that produced its gex, so no reader ever has to
    invert a non-monotone gamma to recover it."""
    monkeypatch.setattr(dg, "_run_code", lambda code: _synthetic_result())
    dg.pull(_dt(2026, 7, 10))
    book = json.load(open(dg._book_path()))
    for b in book["bands"]:
        for k, coi, poi, cg, pg, civ, piv in b["strikes"]:
            if cg > 0:
                assert civ == 0.17          # the call IV the synthetic chain shipped
            if pg > 0:
                assert piv == 0.18



# --- m7: the nearest STRONG wall each side (2026-07-12, local-king rule) -----------
def test_near_wall_marks_support_near_price_not_the_crash_bet():
    # put OI: the 7000 crash bet is the MAX (7% below spot) but 7480 is the biggest
    # pile within reach (±3.5%) — 7480 is the support that bounds today's tape.
    puts = [[7000.0, 5865.0], [7140.0, 3048.0], [7480.0, 3500.0], [7450.0, 2000.0]]
    assert dg.near_wall(puts, 7520.0, "put") == 7480.0
    # calls: the local king above spot within reach
    calls = [[8000.0, 4000.0], [7600.0, 2200.0], [7550.0, 1000.0]]
    assert dg.near_wall(calls, 7520.0, "call") == 7600.0   # 8000 is out of reach (+6.4%)
    # side discipline: a strong wall BEHIND price is never "the wall ahead"
    assert dg.near_wall(puts, 6900.0, "put") is None       # spot gapped below the book
    assert dg.near_wall([], 7520.0, "call") is None
    assert dg.near_wall([[7600.0, 0.0]], 7520.0, "call") is None   # zero-OI dust


def test_near_wall_on_the_real_book_shape_never_crowns_the_crash_bet():
    # THE VERIFY-ROUND REFUTATION, pinned: on the real 07-10 monthlies the crash bet
    # IS the side max (7000P = 100%; nearby support carried 13-29%), so the v1
    # ≥50%-of-max rule degenerated straight back to 7000 — the card's exact harm.
    # The local-king rule picks the biggest pile within ±3.5% of spot instead.
    real_puts = [[7000.0, 5865.0], [7140.0, 3048.0], [7200.0, 900.0],
                 [7450.0, 800.0], [7500.0, 1700.0]]          # 29% / 13.6% of the king
    got = dg.near_wall(real_puts, 7575.0, "put")
    assert got == 7500.0 and got != 7000.0
    # ...but DUST is never crowned: a lone 3%-of-king strike within reach reads None
    dusty = [[7000.0, 5865.0], [7500.0, 150.0]]
    assert dg.near_wall(dusty, 7575.0, "put") is None        # honest absence
    # crash-only book: nothing within reach at all → None (consumers keep the king,
    # explicitly FAR-labeled by the σ distance)
    assert dg.near_wall([[7000.0, 5865.0]], 7575.0, "put") is None
