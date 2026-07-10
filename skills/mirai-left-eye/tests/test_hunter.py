"""Tests for hunter.py — the cross-lens referee (Break Lens P7, inert marker).

Two things must hold at once:
  1. When the Fade Lens (reversion_extreme, pull toward GRAVITY) and the Break
     Lens (level_reclaim, riding the FLOW) would fire OPPOSITE directions on
     the same scan, BOTH get marked stood_down on the diary row + heartbeat.
  2. The alert path (keyed on reversion_extreme only) stays byte-for-byte
     unchanged — the referee is a marker, never a suppressor.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

import hunter as H  # noqa: E402

# Monday 2026-07-06 10:35 ET — a weekday inside RTH so the time gate is open.
NOW = datetime.datetime(2026, 7, 6, 10, 35, tzinfo=ZoneInfo("America/New_York"))


def _telemetry(ticker="SPX", rev_fired=False, rev_dir="none",
               lvl_fired=False, lvl_dir=None):
    """A minimal diary-row telemetry dict with both lens blocks."""
    return {
        "ts": NOW.isoformat(), "ticker": ticker, "spot": 6300.0,
        "regime": "pinning", "sigma": 40.0, "gex_source": "proxy",
        "reversion_extreme": {
            "score": 2.0, "confidence": 0.7, "fired": rev_fired,
            "armed": rev_fired, "direction": rev_dir, "magnet": 6320.0,
            "gap_stretch": -1.4, "runway_sigma": 0.5,
        },
        "level_reclaim": {
            "score": 1.0, "confidence": 0.5, "fired": lvl_fired,
            "direction": lvl_dir, "level": 6290.0,
            "level_kind": "prior_low", "flips_regime": False,
        },
        "gex_views": {"magnet": 6320.0, "regime": "long_gamma"},
    }


# ---------------------------------------------------------------------------
# referee_guard — the pure conflict read
# ---------------------------------------------------------------------------


def test_referee_marks_opposing_fires():
    t = _telemetry(rev_fired=True, rev_dir="call", lvl_fired=True, lvl_dir="put")
    ref = H.referee_guard(t)
    assert ref == {
        "stood_down": True,
        "reason": "opposing_break_and_fade",
        "directions": {"reversion_extreme": "call", "level_reclaim": "put"},
        "ts": NOW.isoformat(),
    }


def test_referee_marks_opposing_fires_other_polarity():
    # Fade SHORT vs break LONG must trip the guard too (both orders).
    t = _telemetry(rev_fired=True, rev_dir="put", lvl_fired=True, lvl_dir="call")
    ref = H.referee_guard(t)
    assert ref is not None and ref["stood_down"] is True
    assert ref["directions"] == {"reversion_extreme": "put", "level_reclaim": "call"}


def test_referee_marks_fade_vs_ungated_break_conflict():
    # the collision that CAN legitimately occur: a fade fire (pinning regime)
    # opposite the UNGATED break trigger (fired_pre_gates carries no regime
    # gate) — the reason keeps the two conflict kinds distinguishable, and this
    # is the one that matters once shadow-alert privileges ride the ungated
    # record. Marker only: nothing suppressed.
    t = _telemetry(rev_fired=True, rev_dir="call", lvl_fired=False, lvl_dir="put")
    t["level_reclaim"]["fired_pre_gates"] = True
    ref = H.referee_guard(t)
    assert ref == {
        "stood_down": True,
        "reason": "opposing_break_pre_gates_and_fade",
        "directions": {"reversion_extreme": "call", "level_reclaim": "put"},
        "ts": NOW.isoformat(),
    }


def test_referee_gated_conflict_outranks_pre_gates():
    # when both twins triggered on one scan the GATED conflict names the marker
    t = _telemetry(rev_fired=True, rev_dir="call", lvl_fired=True, lvl_dir="put")
    t["level_reclaim"]["fired_pre_gates"] = True
    assert H.referee_guard(t)["reason"] == "opposing_break_and_fade"


def test_referee_quiet_on_same_direction_pre_gates():
    t = _telemetry(rev_fired=True, rev_dir="put", lvl_fired=False, lvl_dir="put")
    t["level_reclaim"]["fired_pre_gates"] = True
    assert H.referee_guard(t) is None


def test_referee_quiet_on_same_direction():
    # Both lenses agreeing is confluence, not conflict — no marker.
    t = _telemetry(rev_fired=True, rev_dir="call", lvl_fired=True, lvl_dir="call")
    assert H.referee_guard(t) is None


def test_referee_quiet_when_only_one_lens_fires():
    only_fade = _telemetry(rev_fired=True, rev_dir="call", lvl_fired=False, lvl_dir="put")
    only_break = _telemetry(rev_fired=False, rev_dir="call", lvl_fired=True, lvl_dir="put")
    neither = _telemetry()
    assert H.referee_guard(only_fade) is None
    assert H.referee_guard(only_break) is None
    assert H.referee_guard(neither) is None


def test_referee_quiet_on_bad_directions():
    # Fired flags without two clean call/put directions → no marker.
    for rev_dir, lvl_dir in (("none", "put"), ("call", None), (None, None)):
        t = _telemetry(rev_fired=True, rev_dir=rev_dir, lvl_fired=True, lvl_dir=lvl_dir)
        assert H.referee_guard(t) is None


def test_referee_survives_missing_blocks():
    # Rows before either lens landed (or a failed sub-block) must read as quiet.
    assert H.referee_guard({}) is None
    assert H.referee_guard({"reversion_extreme": None, "level_reclaim": None}) is None


def test_referee_is_a_pure_read():
    # The guard itself never mutates telemetry (scan() attaches the marker).
    t = _telemetry(rev_fired=True, rev_dir="call", lvl_fired=True, lvl_dir="put")
    import copy
    before = copy.deepcopy(t)
    H.referee_guard(t)
    assert t == before


# ---------------------------------------------------------------------------
# scan() wiring — marker on the row + heartbeat, alert path untouched
# ---------------------------------------------------------------------------


@pytest.fixture
def wired_scan(monkeypatch):
    """Stub the scan's collaborators; returns capture dicts. The telemetry the
    fake evaluate() serves is set via captured['telemetry']."""
    captured = {"telemetry": None, "recorded": [], "state": None}

    def fake_evaluate(ticker, now):
        return {"telemetry": captured["telemetry"]}

    def fake_load_state():
        captured["state"] = {"date": NOW.date().isoformat(), "fires": []}
        return captured["state"]

    monkeypatch.setattr(H.reversion, "evaluate", fake_evaluate)
    monkeypatch.setattr(H.reversion, "record",
                        lambda t, now=None: captured["recorded"].append(t))
    monkeypatch.setattr(H.reversion, "live_allowed",
                        lambda: (False, "shadow record unproven"))
    monkeypatch.setattr(H, "load_state", fake_load_state)
    monkeypatch.setattr(H, "load_config", lambda: {"push_to_phone": False})
    monkeypatch.setattr(H, "_persist_scan", lambda hb, st, now: None)
    # the UW shadow bridge is NOT under test here — without this stub the suite
    # fires live Periscope fetches and overwrites production state/gex_uw files
    # with fixture-clock records (observed 2026-07-09).
    monkeypatch.setattr(H.uw_shadow, "run", lambda *a, **k: None)
    return captured


def test_scan_attaches_referee_to_diary_row_and_heartbeat(wired_scan):
    wired_scan["telemetry"] = _telemetry(rev_fired=True, rev_dir="call",
                                         lvl_fired=True, lvl_dir="put")
    hb = H.scan(tickers=["SPX"], now=NOW)

    # Marker rides the recorded diary row (attached BEFORE reversion.record).
    assert len(wired_scan["recorded"]) == 1
    row = wired_scan["recorded"][0]
    assert row["referee"]["stood_down"] is True
    assert row["referee"]["reason"] == "opposing_break_and_fade"
    assert row["referee"]["directions"] == {
        "reversion_extreme": "call", "level_reclaim": "put"}

    # ... and the heartbeat carries the ticker-tagged copy.
    assert hb["stood_down"] == [{"ticker": "SPX", **row["referee"]}]


def test_scan_referee_is_marker_only_alert_still_fires(wired_scan):
    # NOTHING is suppressed: the opposing-break scan still produces the
    # reversion_extreme paper fire, dedup disarm and all.
    wired_scan["telemetry"] = _telemetry(rev_fired=True, rev_dir="call",
                                         lvl_fired=True, lvl_dir="put")
    hb = H.scan(tickers=["SPX"], now=NOW)
    assert len(hb["fires"]) == 1
    assert hb["fires"][0]["ticker"] == "SPX"
    assert hb["fires"][0]["direction"] == "call"
    assert hb["fires"][0]["paper"] is True
    armed = wired_scan["state"]["armed"]["SPX:call"]
    assert armed["disarmed"] is True and armed["fired_signals"] == ["reversion_extreme"]


def test_scan_alert_path_byte_for_byte_unaffected(wired_scan):
    # Same reversion_extreme fire, with vs without the opposing break: the
    # heartbeat's evaluated/fires entries and the dedup state must be EQUAL —
    # the referee adds a key, it never changes the alert path's output.
    wired_scan["telemetry"] = _telemetry(rev_fired=True, rev_dir="call",
                                         lvl_fired=True, lvl_dir="put")
    hb_conflict = H.scan(tickers=["SPX"], now=NOW)
    state_conflict = wired_scan["state"]

    wired_scan["telemetry"] = _telemetry(rev_fired=True, rev_dir="call",
                                         lvl_fired=False, lvl_dir=None)
    hb_quiet = H.scan(tickers=["SPX"], now=NOW)
    state_quiet = wired_scan["state"]

    assert hb_conflict["fires"] == hb_quiet["fires"]
    assert hb_conflict["evaluated"] == hb_quiet["evaluated"]
    assert state_conflict == state_quiet
    # The only delta is the marker itself.
    assert "stood_down" in hb_conflict and "stood_down" not in hb_quiet


def test_scan_no_marker_without_conflict(wired_scan):
    # Same-direction double fire = confluence: no referee key anywhere.
    wired_scan["telemetry"] = _telemetry(rev_fired=True, rev_dir="put",
                                         lvl_fired=True, lvl_dir="put")
    hb = H.scan(tickers=["SPX"], now=NOW)
    assert "stood_down" not in hb
    assert "referee" not in wired_scan["recorded"][0]
    assert len(hb["fires"]) == 1     # the fade alert itself is untouched


def test_scan_referee_fails_open(wired_scan, monkeypatch):
    # A blown guard must cost NOTHING: row recorded without the marker, the
    # reversion_extreme alert fires exactly as before (Monday containment rule).
    def boom(telemetry):
        raise RuntimeError("referee exploded")

    monkeypatch.setattr(H, "referee_guard", boom)
    wired_scan["telemetry"] = _telemetry(rev_fired=True, rev_dir="call",
                                         lvl_fired=True, lvl_dir="put")
    hb = H.scan(tickers=["SPX"], now=NOW)
    assert "stood_down" not in hb
    assert "referee" not in wired_scan["recorded"][0]
    assert len(hb["fires"]) == 1 and hb["fires"][0]["direction"] == "call"
