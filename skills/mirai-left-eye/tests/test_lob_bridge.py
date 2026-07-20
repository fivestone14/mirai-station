"""lob_bridge — the Mirai<->Layer-2 seam: handshakes, isolation, dormancy."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import gex_polarity_ab
import lob_bridge

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)


@pytest.fixture(autouse=True)
def _isolate_lob_state(tmp_path, monkeypatch):
    monkeypatch.setattr(lob_bridge, "STATE_DIR", tmp_path / "lob_flow")


def _write_latest(payload: dict, at: datetime) -> None:
    sd = lob_bridge._state()
    sd.write_latest(payload, at)


# --- tick-side handshakes -----------------------------------------------------


def test_gex_context_roundtrip():
    lob_bridge.write_gex_context("SPX", magnet=6300.0, call_wall=6350.0,
                                 put_wall=6250.0, gamma_flip=6280.0,
                                 sigma=40.0, now=NOW)
    obj = json.loads(lob_bridge._state().gex_context.read_text())
    assert obj["magnet"] == 6300.0 and obj["ticker"] == "SPX"
    assert obj["ts"] == NOW.isoformat()


def test_read_latest_fresh_stale_absent():
    assert lob_bridge.read_latest("SPX", NOW) is None                 # absent
    _write_latest({"lob_flow": {"regime": None}, "spy_depth": {}}, NOW)
    got = lob_bridge.read_latest("SPX", NOW + timedelta(seconds=60))
    assert got and "lob_flow" in got
    late = NOW + timedelta(seconds=700)                               # > 600s
    assert lob_bridge.read_latest("SPX", late) is None


def test_attach_telemetry_adds_keys_only():
    _write_latest({"lob_flow": {"regime": "short_gamma", "scatter": True},
                   "spy_depth": {"regime": None}}, NOW)
    telemetry = {"ts": NOW.isoformat(), "spot": 6300.0}
    lob_bridge.attach_telemetry(telemetry, "SPX", NOW, magnet=6300.0,
                                sigma=40.0)
    assert telemetry["lob_flow"]["scatter"] is True
    assert telemetry["spot"] == 6300.0                                # untouched
    assert lob_bridge._state().gex_context.exists()                   # handoff wrote


def test_attach_telemetry_isolates_failures(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("collector on fire")
    monkeypatch.setattr(lob_bridge, "read_latest", boom)
    telemetry = {"spot": 6300.0}
    lob_bridge.attach_telemetry(telemetry, "SPX", NOW)                # must not raise
    assert telemetry == {"spot": 6300.0}                              # untouched


def test_attach_telemetry_absent_collector_leaves_no_keys():
    telemetry = {}
    lob_bridge.attach_telemetry(telemetry, "SPX", NOW)
    assert "lob_flow" not in telemetry and "spy_depth" not in telemetry


def test_attach_telemetry_exact_key_set():
    """Only the two engine keys may attach — a future latest.json field
    (written_at, age_s, tier, ticker...) must never leak into the diary row."""
    _write_latest({"lob_flow": {"regime": None}, "spy_depth": {"regime": None},
                   "tier": "mcp", "layer_views": []}, NOW)
    telemetry = {"spot": 1.0}
    lob_bridge.attach_telemetry(telemetry, "SPX", NOW)
    assert set(telemetry) == {"spot", "lob_flow", "spy_depth"}


def test_read_latest_rejects_foreign_ticker():
    _write_latest({"ticker": "QQQ", "lob_flow": {"regime": None},
                   "spy_depth": {}}, NOW)
    assert lob_bridge.read_latest("SPX", NOW) is None


def test_tick_path_stays_network_free():
    """attach_telemetry must never pull the network-facing modules into the
    tick — the feeds (and their httpx client) load only in the collector."""
    import sys
    banned = ("lob_flow.options_feed", "lob_flow.spy_stream", "lob_flow.daemon")
    # A feed/daemon test earlier in the session may have left these in
    # sys.modules; drop them so the assertion measures THIS call's imports only.
    for name in banned:
        sys.modules.pop(name, None)
    _write_latest({"lob_flow": {"regime": None}, "spy_depth": {}}, NOW)
    lob_bridge.attach_telemetry({}, "SPX", NOW)
    for name in banned:
        assert name not in sys.modules


def test_nightly_smoke_no_diary(monkeypatch, capsys):
    """A day with no diary and no bars still completes: card written, fold
    refused (unknown-territory day is dirty), gate reported."""
    monkeypatch.setattr(gex_polarity_ab, "_default_bars", lambda tk, day: [])
    lob_bridge.nightly("2099-01-01")
    out = capsys.readouterr().out
    assert "promotion gate -> False" in out
    assert lob_bridge._state().card("2099-01-01").exists()


# --- P5 dormancy ---------------------------------------------------------------


def test_lob_live_is_dormant():
    assert lob_bridge.LOB_LIVE is False


def test_promotion_gate_fail_closed_without_history():
    ok, reason = lob_bridge.promotion_gate()
    assert ok is False and reason


def test_apply_confidence_unchanged_while_dormant():
    _write_latest({"lob_flow": {"scatter": True, "magnet_source": "defense"},
                   "spy_depth": {}}, NOW)
    assert lob_bridge.apply_confidence(0.7, "SPX", NOW) == 0.7


def test_apply_confidence_clamped_even_if_forced_live(monkeypatch):
    """Flip every switch: the influence still cannot leave [0.85, 1.15]."""
    monkeypatch.setattr(lob_bridge, "LOB_LIVE", True)
    monkeypatch.setattr(lob_bridge, "promotion_gate", lambda: (True, "forced"))
    _write_latest({"lob_flow": {"scatter": True}, "spy_depth": {}}, NOW)
    scaled = lob_bridge.apply_confidence(1.0, "SPX", NOW)
    assert 0.85 <= scaled < 1.0                                       # trim, never veto
    _write_latest({"lob_flow": {"scatter": False, "magnet_source": "defense"},
                   "spy_depth": {}}, NOW)
    boosted = lob_bridge.apply_confidence(1.0, "SPX", NOW)
    assert 1.0 < boosted <= 1.15


def test_apply_confidence_fail_closed(monkeypatch):
    monkeypatch.setattr(lob_bridge, "LOB_LIVE", True)
    monkeypatch.setattr(lob_bridge, "promotion_gate",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    assert lob_bridge.apply_confidence(0.9, "SPX", NOW) == 0.9


def test_trading_path_never_calls_apply_confidence():
    """The whisper stays UNWIRED until promotion: no trading module may
    reference it. (Wiring it is the explicit, gated promotion step.)"""
    import pathlib
    skill = pathlib.Path(lob_bridge.__file__).parent
    for mod in ("reversion_lens.py", "lefteye_gex_box.py", "hunter.py"):
        assert "apply_confidence" not in (skill / mod).read_text()


# --- host grader compatibility ----------------------------------------------------


def _row(ts, spot=6300.0, sigma=40.0, **engines):
    return {"ts": ts.isoformat(), "ticker": "SPX", "spot": spot, "sigma": sigma,
            **engines}


def test_grade_day_scores_all_four_engines():
    t = NOW
    rows = []
    for i in range(3):
        rows.append(_row(
            t + timedelta(minutes=5 * i),
            gex_views={"regime": "long_gamma", "magnet": 6330.0},
            gex_theta={"regime": "long_gamma", "magnet": 6330.0},
            lob_flow={"regime": "long_gamma", "magnet": 6330.0,
                      "magnet_source": "defense"},
            spy_depth={"regime": "long_gamma", "magnet": 6330.0,
                       "magnet_source": "gravity"}))
    bars = [{"ts": (t + timedelta(minutes=m)).isoformat(),
             "high": 6300 + m, "low": 6299 + m} for m in range(90)]
    res = gex_polarity_ab.grade_day("2026-07-07", rows=rows,
                                    bars_lookup=lambda tk: bars)
    assert set(res["engines"]) == {"gex_views", "gex_theta",
                                   "lob_flow", "spy_depth"}


def test_grade_day_tolerates_pre_lob_rows():
    rows = [_row(NOW, gex_views={"regime": "long_gamma", "magnet": 6330.0})]
    bars = [{"ts": (NOW + timedelta(minutes=m)).isoformat(),
             "high": 6300 + m, "low": 6299 + m} for m in range(90)]
    res = gex_polarity_ab.grade_day("2026-07-07", rows=rows,
                                    bars_lookup=lambda tk: bars)
    assert "gex_views" in res["engines"]
    assert "lob_flow" not in res["engines"]                           # skipped, no crash
