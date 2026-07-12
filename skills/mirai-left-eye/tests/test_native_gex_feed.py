import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import native_gex_feed as tf


def test_fill_gamma_from_iv_only_when_missing():
    contracts = [
        {"strike": 7500, "iv": 0.15, "dte": 0, "gamma": None},      # filled
        {"strike": 7400, "iv": 0.15, "dte": 3, "gamma": None},      # filled
        {"strike": 7600, "iv": 0.15, "dte": 3, "gamma": 0.00123},   # provider kept
        {"strike": 7700, "iv": 0.0, "dte": 3, "gamma": None},       # no iv → stays None
    ]
    tf._fill_gamma(contracts, spot=7500.0)
    assert contracts[0]["gamma"] and contracts[0]["gamma"] > 0
    assert contracts[0].get("gamma_src") == "bs_iv"
    assert contracts[2]["gamma"] == 0.00123 and "gamma_src" not in contracts[2]
    assert contracts[3]["gamma"] is None


def test_native_chain_normalizes_and_fills(monkeypatch):
    fake = {"spot": 7500.0, "contracts": [
        {"right": "call", "strike": 7500, "dte": 0, "iv": 0.12, "gamma": None,
         "open_interest": 100, "volume": 5000},
    ]}
    monkeypatch.setattr(tf, "_run", lambda code: fake)
    out = tf.native_chain("SPX")
    assert out["spot"] == 7500.0
    c = out["contracts"][0]
    assert c["gamma"] and c["gamma"] > 0        # recomputed from iv
    assert c["right"] == "call" and c["strike"] == 7500


def test_native_chain_none_on_empty(monkeypatch):
    monkeypatch.setattr(tf, "_run", lambda code: {"spot": None, "contracts": []})
    assert tf.native_chain("SPX") is None
    monkeypatch.setattr(tf, "_run", lambda code: None)
    assert tf.native_chain("SPX") is None


def test_aggressor_flow_parse_clamp_and_none(monkeypatch):
    monkeypatch.setattr(tf, "_run", lambda code: {"available": True, "flow": 0.42, "gross": 1e6})
    assert tf.aggressor_flow("SPX", 7500.0) == 0.42
    monkeypatch.setattr(tf, "_run", lambda code: {"available": True, "flow": 3.5})
    assert tf.aggressor_flow("SPX", 7500.0) == 1.0        # clamped to [-1, 1]
    monkeypatch.setattr(tf, "_run", lambda code: {"available": False})
    assert tf.aggressor_flow("SPX", 7500.0) is None
    monkeypatch.setattr(tf, "_run", lambda code: None)
    assert tf.aggressor_flow("SPX", 7500.0) is None
    assert tf.aggressor_flow("SPX", 0.0) is None          # no spot → no query


class TestCoverageGuard:
    """N7 (2026-07-12) — the minimum-coverage guard. One empty expiry×side chunk is a
    DOCUMENTED server behavior, and before this guard the half-book still had a spot,
    still had thousands of contracts, still passed every check and still said 'native'
    while the regime flipped on the missing half."""

    def _today(self):
        return tf._today_iso()

    def test_dead_0dte_side_rejects(self, monkeypatch):
        today = self._today()
        fake = {"spot": 7500.0,
                "contracts": [{"right": "call", "strike": 7500, "dte": 0, "iv": 0.12,
                               "gamma": None, "open_interest": 100, "volume": 5000}],
                "sides": [{"root": "SPXW", "exp": today, "side": "call", "rows": 120, "dte": 0},
                          {"root": "SPXW", "exp": today, "side": "put", "rows": 0, "dte": 0}]}
        monkeypatch.setattr(tf, "_run", lambda code: fake)
        assert tf.native_chain("SPX") is None            # the half-book must not serve
        assert tf.LAST_REJECT and tf.LAST_REJECT["reason"] == "coverage"
        assert any(m["side"] == "put" for m in tf.LAST_REJECT["missing"])

    def test_thin_0dte_side_vs_fat_twin_rejects(self, monkeypatch):
        # 3 puts against 120 calls is an amputated fetch, not a thin book
        today = self._today()
        fake = {"spot": 7500.0,
                "contracts": [{"right": "call", "strike": 7500, "dte": 0, "iv": 0.12,
                               "gamma": None, "open_interest": 100, "volume": 5000}],
                "sides": [{"root": "SPXW", "exp": today, "side": "call", "rows": 120, "dte": 0},
                          {"root": "SPXW", "exp": today, "side": "put", "rows": 3, "dte": 0}]}
        monkeypatch.setattr(tf, "_run", lambda code: fake)
        assert tf.native_chain("SPX") is None

    def test_missing_tenor_side_degrades_not_rejects(self, monkeypatch):
        # a dead 3DTE side must NOT kill the book — it ships flagged incomplete
        today = self._today()
        fake = {"spot": 7500.0,
                "contracts": [{"right": "call", "strike": 7500, "dte": 0, "iv": 0.12,
                               "gamma": None, "open_interest": 100, "volume": 5000}],
                "sides": [{"root": "SPXW", "exp": today, "side": "call", "rows": 120, "dte": 0},
                          {"root": "SPXW", "exp": today, "side": "put", "rows": 118, "dte": 0},
                          {"root": "SPXW", "exp": "2099-01-08", "side": "call", "rows": 90, "dte": 3},
                          {"root": "SPXW", "exp": "2099-01-08", "side": "put", "rows": 0, "dte": 3}]}
        monkeypatch.setattr(tf, "_run", lambda code: fake)
        out = tf.native_chain("SPX")
        assert out is not None
        cov = out["meta"]["coverage"]
        assert cov["complete"] is False and cov["zero_dte_dead"] is False
        assert any(m["exp"] == "2099-01-08" and m["side"] == "put" for m in cov["missing"])

    def test_healthy_book_is_complete(self, monkeypatch):
        today = self._today()
        fake = {"spot": 7500.0,
                "contracts": [{"right": "call", "strike": 7500, "dte": 0, "iv": 0.12,
                               "gamma": None, "open_interest": 100, "volume": 5000}],
                "sides": [{"root": "SPXW", "exp": today, "side": "call", "rows": 120, "dte": 0},
                          {"root": "SPXW", "exp": today, "side": "put", "rows": 118, "dte": 0}]}
        monkeypatch.setattr(tf, "_run", lambda code: fake)
        out = tf.native_chain("SPX")
        assert out["meta"]["coverage"] == {"complete": True, "missing": None,
                                           "zero_dte_dead": False, "no_zero_dte": False}

    def test_pre_sides_responses_stay_served(self, monkeypatch):
        # backward compat: an older server response without `sides` must not be rejected
        fake = {"spot": 7500.0,
                "contracts": [{"right": "call", "strike": 7500, "dte": 0, "iv": 0.12,
                               "gamma": None, "open_interest": 100, "volume": 5000}]}
        monkeypatch.setattr(tf, "_run", lambda code: fake)
        out = tf.native_chain("SPX")
        assert out is not None
        assert out["meta"]["coverage"]["complete"] is True

    def test_no_zero_dte_expiry_is_flagged_not_rejected(self, monkeypatch):
        # weekend/holiday: discovery legitimately returns no 0DTE expiry — flag, don't kill
        fake = {"spot": 7500.0,
                "contracts": [{"right": "call", "strike": 7500, "dte": 2, "iv": 0.12,
                               "gamma": None, "open_interest": 100, "volume": 5000}],
                "sides": [{"root": "SPXW", "exp": "2099-01-07", "side": "call", "rows": 90, "dte": 2},
                          {"root": "SPXW", "exp": "2099-01-07", "side": "put", "rows": 88, "dte": 2}]}
        monkeypatch.setattr(tf, "_run", lambda code: fake)
        out = tf.native_chain("SPX")
        assert out is not None
        assert out["meta"]["coverage"]["no_zero_dte"] is True
        assert out["meta"]["coverage"]["zero_dte_dead"] is False


def test_root_mapping_uses_spxw():
    assert tf.ROOT["SPX"] == "SPXW"      # 0DTE dailies live under SPXW, not SPX
    assert set(tf.ROOT) == {"SPX"}       # SPX-only system (mini-index twin retired 2026-07-04)
    assert tf.MONTHLY_ROOT["SPX"] == "SPX"   # AM-settled monthlies join on OpEx weeks


def test_parity_iv_fallback_rescues_itm_puts():
    contracts = [
        {"right": "call", "strike": 7500, "expiry": "2026-07-06", "iv": 0.12},
        {"right": "put",  "strike": 7500, "expiry": "2026-07-06", "iv": 0.0},   # rescued
        {"right": "put",  "strike": 7400, "expiry": "2026-07-06", "iv": None},  # no twin → dropped
        {"right": "put",  "strike": 7450, "expiry": "2026-07-06", "iv": 0.13},  # healthy, untouched
    ]
    meta = tf._parity_iv_fallback(contracts)
    assert contracts[1]["iv"] == 0.12 and contracts[1]["iv_src"] == "parity_twin"
    assert not contracts[2]["iv"]
    assert contracts[3]["iv"] == 0.13 and "iv_src" not in contracts[3]
    assert meta == {"iv_rescued": 1, "iv_dropped": 1}


def test_native_chain_ttl_cache_serves_second_caller(monkeypatch):
    calls = {"n": 0}
    def fake_run(code):
        calls["n"] += 1
        return {"spot": 7500.0, "contracts": [
            {"right": "call", "strike": 7500, "dte": 0, "expiry": "2026-07-06", "iv": 0.12}]}
    monkeypatch.setattr(tf, "_run", fake_run)
    a = tf.native_chain("SPX")
    n_cold = calls["n"]                    # cold fetch: chain + per-strike signed-flow (Phase 0)
    b = tf.native_chain("SPX")             # second caller must be served from the TTL cache
    assert a is b and calls["n"] == n_cold  # cache serves the second caller — zero re-fetch
    assert a["meta"]["iv_rescued"] == 0 and a["meta"]["iv_dropped"] == 0


def _now():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def test_read_returns_views_within_budget(monkeypatch):
    chain = {"spot": 7500.0, "contracts": [
        {"right": r, "strike": k, "dte": 0, "iv": 0.12, "gamma": 0.001,
         "open_interest": 100, "volume": 5000}
        for k in (7480, 7500, 7520) for r in ("call", "put")]}
    monkeypatch.setattr(tf, "native_chain", lambda t: chain)
    monkeypatch.setattr(tf, "aggressor_flow", lambda t, s: 0.2)
    v = tf.read("SPX", 7500.0, _now(), budget_s=3.0)
    assert v is not None and "slides" in v and v.get("aggressor_flow") == 0.2


def test_read_none_when_no_chain(monkeypatch):
    monkeypatch.setattr(tf, "native_chain", lambda t: None)
    assert tf.read("SPX", 7500.0, _now(), budget_s=2.0) is None


def test_read_reuses_provided_flow_without_refetch(monkeypatch):
    chain = {"spot": 7500.0, "contracts": [
        {"right": r, "strike": k, "dte": 0, "iv": 0.12, "gamma": 0.001,
         "open_interest": 100, "volume": 5000}
        for k in (7480, 7500, 7520) for r in ("call", "put")]}
    monkeypatch.setattr(tf, "native_chain", lambda t: chain)
    def _boom(t, s):
        raise AssertionError("flow must not be re-fetched when the caller provides it")
    monkeypatch.setattr(tf, "aggressor_flow", _boom)
    v = tf.read("SPX", 7500.0, _now(), budget_s=3.0, flow=-0.7)
    assert v is not None and v.get("aggressor_flow") == -0.7
    # heavy one-way flow against a long-gamma read → slide E downgrades to uncertain
    assert v["regime"] in ("uncertain", "short_gamma")


def test_native_chain_drops_am_settled_monthly_0dte(monkeypatch):
    # OpEx Friday: the PM-settled SPXW daily (tradeable 0DTE) and the AM-settled SPX
    # monthly (dead after the 9:30 open, ~10-50x the OI) both carry dte==0. The
    # AM-settled SPX-root 0DTE must be dropped; SPXW 0DTE and any live SPX dated leg kept.
    fake = {"spot": 6000.0, "contracts": [
        {"right": "call", "strike": 6000, "dte": 0, "root": "SPXW",
         "iv": 0.15, "gamma": 0.01, "open_interest": 2000, "volume": 5000},
        {"right": "put", "strike": 6000, "dte": 0, "root": "SPXW",
         "iv": 0.15, "gamma": 0.01, "open_interest": 2000, "volume": 5000},
        {"right": "call", "strike": 6000, "dte": 0, "root": "SPX",   # AM-settled → dead
         "iv": 0.15, "gamma": 0.01, "open_interest": 55000, "volume": 10},
        {"right": "call", "strike": 6050, "dte": 3, "root": "SPX",   # live dated leg → keep
         "iv": 0.15, "gamma": 0.01, "open_interest": 40000, "volume": 5},
    ]}
    monkeypatch.setattr(tf, "_run", lambda code: fake)
    out = tf.native_chain("SPX")
    roots_dtes = {(c["root"], c["dte"]) for c in out["contracts"]}
    assert ("SPX", 0) not in roots_dtes       # dead AM-settled 0DTE dropped
    assert ("SPXW", 0) in roots_dtes          # tradeable PM-settled 0DTE kept
    assert ("SPX", 3) in roots_dtes           # live dated monthly leg kept
    assert out["meta"]["am_settled_dropped"] == 1


def test_native_chain_keeps_all_on_normal_day(monkeypatch):
    # No AM-settled monthly in the window (typical day) → nothing dropped, no-op filter.
    fake = {"spot": 6000.0, "contracts": [
        {"right": "call", "strike": 6000, "dte": 0, "root": "SPXW",
         "iv": 0.15, "gamma": 0.01, "open_interest": 2000, "volume": 5000},
    ]}
    monkeypatch.setattr(tf, "_run", lambda code: fake)
    out = tf.native_chain("SPX")
    assert len(out["contracts"]) == 1
    assert out["meta"]["am_settled_dropped"] == 0
