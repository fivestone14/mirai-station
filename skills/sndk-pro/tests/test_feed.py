"""Feed behaviors: discovery caching (incl. the M2 probe-error teeth),
no-expiry-today normality, coverage teeth on the FRONT expiry, the fail-closed
live-quote anchor (M3), the vol-adaptive window (M4), the disk book cache (M5)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import atomic_io
import sndk_feed
import synth

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 27, 10, 30, tzinfo=ET)          # Monday: NO expiry today
EXPS = ["2026-07-31", "2026-08-07"]


def _stub_probe(monkeypatch, counter, found=EXPS, spot=1088.5, errors=()):
    def run(code):
        counter.append(code)
        return {"spot": spot, "found": list(found), "errors": list(errors)}
    monkeypatch.setattr(sndk_feed._nf, "_run", run)


def _sides(front_put_rows=30):
    return [
        {"exp": "2026-07-31", "side": "call", "rows": 30, "dte": 4},
        {"exp": "2026-07-31", "side": "put", "rows": front_put_rows, "dte": 4},
        {"exp": "2026-08-07", "side": "call", "rows": 30, "dte": 11},
        {"exp": "2026-08-07", "side": "put", "rows": 30, "dte": 11},
    ]


def _stub_pull(monkeypatch, calls, sides=None, contracts=None):
    def run(code):
        calls.append(code)
        return {"spot": 1088.5, "chunks": 4, "sides": sides or _sides(),
                "contracts": contracts if contracts is not None else synth.book()}
    monkeypatch.setattr(sndk_feed._nf, "_run", run)


# --- discovery caching -------------------------------------------------------

def test_discovery_probes_once_per_day(monkeypatch):
    calls = []
    _stub_probe(monkeypatch, calls)
    assert sndk_feed.discover_expiries(NOW) == EXPS
    assert len(calls) == 1
    assert sndk_feed.discover_expiries(NOW) == EXPS
    assert len(calls) == 1                  # served from the day cache


def test_failed_discovery_not_cached(monkeypatch):
    calls = []
    _stub_probe(monkeypatch, calls, found=[])
    assert sndk_feed.discover_expiries(NOW) == []
    assert sndk_feed.discover_expiries(NOW) == []
    assert len(calls) == 2                  # an empty verdict must retry, not stick


# --- M2: a probe ERROR is an unknown, never an empty -------------------------

def test_front_probe_error_degrades_and_retries(monkeypatch):
    # the 07-31 (front-candidate) probe hiccups while 08-07 succeeds: the day
    # must NOT run cached on next week's book as "front" — degraded, re-probed
    calls = []
    _stub_probe(monkeypatch, calls, found=["2026-08-07"], errors=["2026-07-31"])
    assert sndk_feed.discover_expiries(NOW) == ["2026-08-07"]
    assert sndk_feed.LAST_DISCOVERY == "degraded"
    assert sndk_feed.discover_expiries(NOW) == ["2026-08-07"]
    assert len(calls) == 2                  # not day-cached → retried next tick


def test_clean_empty_friday_still_cached(monkeypatch):
    # 07-31 answers cleanly with no contracts (a real non-listing): that IS a
    # complete verdict — cached normally, no degradation
    calls = []
    _stub_probe(monkeypatch, calls, found=["2026-08-07"], errors=[])
    assert sndk_feed.discover_expiries(NOW) == ["2026-08-07"]
    assert sndk_feed.LAST_DISCOVERY == "ok"
    assert sndk_feed.discover_expiries(NOW) == ["2026-08-07"]
    assert len(calls) == 1                  # served from the day cache


def test_probe_error_behind_front_does_not_degrade(monkeypatch):
    # an errored candidate AFTER the earliest found expiry (08-14) or a plain
    # non-expiry weekday (07-28) cannot hide the front book → complete verdict
    calls = []
    _stub_probe(monkeypatch, calls, found=list(EXPS),
                errors=["2026-07-28", "2026-08-14"])
    assert sndk_feed.discover_expiries(NOW) == EXPS
    assert sndk_feed.LAST_DISCOVERY == "ok"
    assert sndk_feed.discover_expiries(NOW) == EXPS
    assert len(calls) == 1


def test_degraded_discovery_rides_row_meta(monkeypatch):
    def disc(now):
        sndk_feed.LAST_DISCOVERY = "degraded"
        return list(EXPS)
    monkeypatch.setattr(sndk_feed, "discover_expiries", disc)
    calls = []
    _stub_pull(monkeypatch, calls)
    ch = sndk_feed.sndk_chain(NOW, live_spot=1250.0)
    assert ch["meta"]["discovery"] == "degraded"


# --- chain: no expiry today is NORMAL ----------------------------------------

def test_no_expiry_today_is_not_an_error(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls)
    ch = sndk_feed.sndk_chain(NOW, live_spot=1250.0)
    assert ch is not None
    cov = ch["meta"]["coverage"]
    assert cov["expiry_today"] is False     # recorded, never rejected
    assert cov["front_dead"] is False
    assert cov["complete"] is True
    assert sndk_feed.LAST_REJECT is None
    assert [e["dte"] for e in ch["meta"]["expiries"]] == [4, 11]


def test_dead_front_side_rejects(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls, sides=_sides(front_put_rows=0))
    assert sndk_feed.sndk_chain(NOW, live_spot=1250.0) is None
    assert (sndk_feed.LAST_REJECT or {}).get("reason") == "coverage"


def test_dead_second_expiry_degrades_not_rejects(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    sides = _sides()
    sides[3] = {"exp": "2026-08-07", "side": "put", "rows": 0, "dte": 11}
    calls = []
    _stub_pull(monkeypatch, calls, sides=sides)
    ch = sndk_feed.sndk_chain(NOW, live_spot=1250.0)
    assert ch is not None                   # ships, but says so
    cov = ch["meta"]["coverage"]
    assert cov["complete"] is False
    assert cov["missing"] and cov["missing"][0]["exp"] == "2026-08-07"


# --- M3: the live quote is the ONLY honest anchor ----------------------------

def test_live_spot_anchors_the_window(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls)
    ch = sndk_feed.sndk_chain(NOW, live_spot=1250.0)
    assert ch["anchor_spot"] == 1250.0      # NOT the stale 1088.5 chain spot
    assert ch["spot"] == 1088.5             # the stale spot rides for the record
    # no vol hint yet → the ±8% floor window off the LIVE anchor
    assert ch["meta"]["window"] == 0.08
    assert str(round(1250.0 * 0.92, 2)) in calls[-1]
    assert str(round(1250.0 * 1.08, 2)) in calls[-1]


def test_no_live_quote_no_book(monkeypatch):
    # the old path anchored (and wrote) the known-stale chain spot — now the
    # tick fails closed before any discovery or pull happens
    atomic_io.write_json_atomic(sndk_feed._state_dir() / "discovery.json",
                                {"date": NOW.date().isoformat(),
                                 "expiries": EXPS, "spot_hint": 1088.5})
    calls = []
    _stub_pull(monkeypatch, calls)
    assert sndk_feed.sndk_chain(NOW, live_spot=None) is None
    assert (sndk_feed.LAST_REJECT or {}).get("reason") == "no_live_quote"
    assert not calls                        # no network on the stale anchor


# --- M4: vol-adaptive strike window ------------------------------------------

def test_window_adapts_to_vol_hint(monkeypatch):
    # SNDK live case: σ≈90 on spot 1246 (~114% IV) — the fixed ±8% is ≈±1.1σ
    # and truncates the magnet's ±1.5σ reach; the hinted window must cover it
    sdf = round(90.0 / 1246.0, 5)
    atomic_io.write_json_atomic(sndk_feed._state_dir() / "vol_hint.json",
                                {"sigma_daily_frac": sdf, "ts": NOW.isoformat()})
    w = sndk_feed._window_frac()
    assert w >= 1.5 * sdf                   # MAGNET_V3 reach + headroom inside
    assert w <= sndk_feed._WINDOW_MAX
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls)
    ch = sndk_feed.sndk_chain(NOW, live_spot=1246.0)
    assert ch["meta"]["window"] == round(w, 4)
    assert str(round(1246.0 * (1.0 - w), 2)) in calls[-1]
    assert str(round(1246.0 * (1.0 + w), 2)) in calls[-1]


def test_window_clamps_sane():
    atomic_io.write_json_atomic(sndk_feed._state_dir() / "vol_hint.json",
                                {"sigma_daily_frac": 0.20, "ts": NOW.isoformat()})
    assert sndk_feed._window_frac() == sndk_feed._WINDOW_MAX
    atomic_io.write_json_atomic(sndk_feed._state_dir() / "vol_hint.json",
                                {"sigma_daily_frac": 0.01, "ts": NOW.isoformat()})
    assert sndk_feed._window_frac() == sndk_feed._WINDOW_LIVE   # floor holds


def test_vol_hint_persisted_after_build(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls)
    assert sndk_feed.sndk_chain(NOW, live_spot=1250.0) is not None
    hint = atomic_io.read_json_or(sndk_feed._state_dir() / "vol_hint.json", None)
    # synth book rebuilds to ~0.5 IV → σ_daily/spot ≈ 0.5/√252 ≈ 0.0315
    assert hint and 0.02 < hint["sigma_daily_frac"] < 0.05


# --- M5: the raw book persists across process relaunches ---------------------

def test_fresh_book_cache_skips_network(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls)
    ch1 = sndk_feed.sndk_chain(NOW, live_spot=1250.0)
    assert ch1 is not None and len(calls) == 1
    assert ch1["meta"]["book_source"] == "pull"
    # a NEW process 100s later (same disk state): no network, repriced anchor
    def boom(code):
        raise AssertionError("network call on a fresh cache")
    monkeypatch.setattr(sndk_feed._nf, "_run", boom)
    ch2 = sndk_feed.sndk_chain(NOW + timedelta(seconds=100), live_spot=1252.0)
    assert ch2 is not None
    assert ch2["anchor_spot"] == 1252.0     # repriced at the fresh quote
    m = ch2["meta"]
    assert m["book_source"] == "disk_cache"
    assert 0 < m["cache_age_s"] < sndk_feed._CHAIN_TTL_S
    assert m["book_asof"] == NOW.isoformat()
    assert m["iv_rebuilt"] > 0              # IVs re-solved vs the new anchor


def test_stale_book_cache_pulls(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    calls = []
    _stub_pull(monkeypatch, calls)
    assert sndk_feed.sndk_chain(NOW, live_spot=1250.0) is not None
    later = NOW + timedelta(seconds=sndk_feed._CHAIN_TTL_S + 1)
    ch = sndk_feed.sndk_chain(later, live_spot=1251.0)
    assert ch["meta"]["book_source"] == "pull"
    assert len(calls) == 2                  # stale → honest re-pull


def test_corrupt_book_cache_pulls(monkeypatch):
    monkeypatch.setattr(sndk_feed, "discover_expiries", lambda now: list(EXPS))
    path = sndk_feed._state_dir() / "chain_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn json")
    calls = []
    _stub_pull(monkeypatch, calls)
    ch = sndk_feed.sndk_chain(NOW, live_spot=1250.0)
    assert ch is not None and len(calls) == 1
    assert ch["meta"]["book_source"] == "pull"


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("SNDK_PRO_DISABLE", "1")
    assert sndk_feed.sndk_chain(NOW, live_spot=1250.0) is None
