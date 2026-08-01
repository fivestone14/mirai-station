"""Shared test guards for the sndk-pro suite (left-eye conftest discipline:
every state write redirected to tmp_path, every network path stubbed-or-dead,
module caches dropped between tests)."""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
_SKILLS = os.path.dirname(_SKILL)
sys.path.insert(0, _SKILL)
sys.path.insert(0, os.path.join(_SKILLS, "mirai-left-eye"))

import lefteye_fill_ledger  # noqa: E402
import sndk_feed  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """sndk_feed persists discovery/fetch-log under state/sndk_gex and the
    hunter appends diary rows under state/sndk_reversion — both honor
    MIRAI_STATE_DIR, so every test's IO lands in a throwaway dir."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolate_fill_ledger(tmp_path, monkeypatch):
    """The engine's slide paths can annotate the fill ledger as a side effect
    (left-eye conftest verbatim) — never let a test stamp production state."""
    monkeypatch.setattr(lefteye_fill_ledger, "_DEFAULT_DIR", tmp_path / "gex_fills")


@pytest.fixture(autouse=True)
def _reset_chain_caches():
    """sndk_feed keeps a 240s TTL chain cache — between tests it would serve
    one test's stubbed chain to the next."""
    sndk_feed.reset_chain_cache()
    yield
    sndk_feed.reset_chain_cache()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test may reach the MCP transport: sndk_feed routes every server call
    through native_gex_feed._run — dead-end it by default; tests that need a
    server stub monkeypatch sndk_feed._nf._run themselves."""
    monkeypatch.setattr(sndk_feed._nf, "_run",
                        lambda code: (_ for _ in ()).throw(
                            AssertionError("network call during test")))
