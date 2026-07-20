"""Shared test guards for the left-eye suite."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# lob_bridge imports the lob_flow box lazily; the runtime provides it via an
# editable install, but a bare pytest run needs the package dir on sys.path so
# the test_lob_bridge suite can import it (mirrors siege_bridge's own insert).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "lob-flow"))
import lefteye_fill_ledger


@pytest.fixture(autouse=True)
def _isolate_fill_ledger(tmp_path, monkeypatch):
    """The FILL LEDGER persists to the real state/ dir by default, and GexBox /
    native read() annotate as a side effect — so ANY test that exercises those
    paths with synthetic chains would write fake fills into production state
    (and a midday pytest run would reset the live day's decayed history).
    Redirect every test's ledger IO to a throwaway dir."""
    monkeypatch.setattr(lefteye_fill_ledger, "_DEFAULT_DIR", tmp_path / "gex_fills")


@pytest.fixture(autouse=True)
def _isolate_bars_store(tmp_path, monkeypatch):
    """grade_day now PERSISTS real-range bars as a side effect — redirect the
    bars store to a throwaway dir so tests never write production state."""
    import gex_polarity_ab
    monkeypatch.setattr(gex_polarity_ab, "_bars_dir", lambda: tmp_path / "bars")


@pytest.fixture(autouse=True)
def _isolate_lob_state(tmp_path, monkeypatch):
    """lob_bridge writes gex_context.json (and reads latest.json) under the
    real state/lob_flow by default; any test driving evaluate() end-to-end
    would stamp production state. Redirect every test's lob IO to a
    throwaway dir (import guarded: the box may be absent in a bare checkout)."""
    try:
        import lob_bridge
        monkeypatch.setattr(lob_bridge, "STATE_DIR", tmp_path / "lob_flow")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_siege_state(tmp_path, monkeypatch):
    """siege_bridge writes latest/cards/baseline under the real state/siege by
    default; any test driving evaluate() end-to-end would stamp production
    state (and fold fake bars into the live volume baseline). Redirect every
    test's siege IO to a throwaway dir (import guarded like the lob twin)."""
    try:
        import siege_bridge
        monkeypatch.setattr(siege_bridge, "STATE_DIR", tmp_path / "siege")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_chain_caches():
    """native_chain keeps a short TTL cache (one fetch serves both the flow-sensor
    read and the gravity engine within a scan) — between tests it would serve one
    test's stubbed chain to the next, so drop it around every test."""
    import native_gex_feed
    native_gex_feed.reset_chain_cache()
    yield
    native_gex_feed.reset_chain_cache()


@pytest.fixture(autouse=True)
def _disarm_watchtower(monkeypatch):
    """HEAD B (watchtower.py) shells out to `claude -p` from inside evaluate();
    a test driving the scan end-to-end must never spawn a real model call
    (slow, costly, non-deterministic). The env kill-switch makes observe()
    return None. Tests that exercise the tower itself delenv this."""
    monkeypatch.setenv("WATCHTOWER_DISABLE", "1")
