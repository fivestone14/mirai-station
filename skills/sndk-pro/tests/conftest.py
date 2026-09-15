"""Shared test guards for the sndk-pro suite (left-eye conftest discipline:
every state write redirected to tmp_path, every network path stubbed-or-dead,
module caches dropped between tests)."""
import ast
import copy
import importlib
import os
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
_SKILLS = os.path.dirname(_SKILL)
sys.path.insert(0, _SKILL)
sys.path.insert(0, os.path.join(_SKILLS, "mirai-left-eye"))

import lefteye_fill_ledger  # noqa: E402
import sndk_feed  # noqa: E402
import sndk_board  # noqa: E402
import sndk_read  # noqa: E402
import synth  # noqa: E402 — first imported here, before any guard, so it holds the real model calls

# Methods that fill a module-level container in place.
_FILLS = {"append", "appendleft", "extend", "insert", "update", "setdefault",
          "add", "pop", "popitem", "remove", "discard", "clear"}


def _kept_between_calls(source: str) -> set:
    """Module-level names the code rebinds (`global`) or fills in place (an
    item set, an append, an update) — its caches and last-call records. Read
    off the source, so a cache added later is reset without a list to update."""
    tree = ast.parse(source)
    top = {t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
           for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
           if isinstance(t, ast.Name)}
    kept = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            kept.update(node.names)
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            kept.update(t.value.id for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in _FILLS and isinstance(node.func.value, ast.Name)):
            kept.add(node.func.value.id)
    return kept & top


def _as_imported() -> dict:
    """{(module, name): its value at import} for every sndk_* module's kept state."""
    state = {}
    for path in sorted(Path(_SKILL).glob("sndk_*.py")):
        for name in _kept_between_calls(path.read_text()):
            module = importlib.import_module(path.stem)
            state[(module, name)] = copy.deepcopy(getattr(module, name))
    return state


_MODULE_STATE = _as_imported()


def pytest_collection_modifyitems(session, config, items):
    """A helper two test files share lives in synth.py. A test file importing
    another breaks the day that file is renamed, split or deleted, so the run
    stops here instead of on that day."""
    for path in sorted(Path(_HERE).glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.split(".")[0].startswith("test_"):
                    raise pytest.UsageError(
                        f"{path.name} imports from {module}.py — move what they "
                        f"share into synth.py and import it from there")


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
    """sndk_feed keeps a TTL chain cache (_CHAIN_TTL_S) — between tests it would serve
    one test's stubbed chain to the next."""
    sndk_feed.reset_chain_cache()
    yield
    sndk_feed.reset_chain_cache()


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """The real call path leaves state on its modules: sndk_board's prior-session
    volume baseline and sndk_read's closed-day extremes are memoized by date,
    sndk_read keeps the last call's bill, sndk_feed the last discovery verdict
    and reject. Every test here shares a handful of dates, so one test's session
    would answer the next one's question. Each test starts from the values the
    modules were imported with."""
    for (module, name), value in _MODULE_STATE.items():
        monkeypatch.setattr(module, name, copy.deepcopy(value))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test may reach the MCP transport: sndk_feed routes every server call
    through native_gex_feed._run — dead-end it by default; tests that need a
    server stub monkeypatch sndk_feed._nf._run themselves."""
    monkeypatch.setattr(sndk_feed._nf, "_run",
                        lambda code: (_ for _ in ()).throw(
                            AssertionError("network call during test")))


@pytest.fixture(autouse=True)
def _no_model_call(monkeypatch):
    """No test may spend a real model call. Three did — two in test_side that
    only wanted the side packet, and the cost test, which stubs the subprocess
    but not the call above it — so every run of this suite was billed three
    times while the promise above said no network. A test that needs a reply
    monkeypatches call_the_model itself; a test of the real command line puts
    back synth._REAL_CALL_THE_MODEL(_V2) with the subprocess faked under it;
    anything else fails loudly here."""
    def spent(*a, **k):
        raise AssertionError("a test spent a real model call — stub "
                             "sndk_read.call_the_model in the test itself")
    monkeypatch.setattr(sndk_read, "call_the_model", spent)
    monkeypatch.setattr(sndk_board, "call_the_model_v2", spent)


@pytest.fixture(autouse=True)
def _no_semantic_reviewer(monkeypatch):
    """obs-4: the semantic reviewer is a real `claude -p` call. Off for every
    test — the tests that exercise it inject a judge — so the suite never
    spawns the CLI, never pays for a call, and never waits on one."""
    monkeypatch.setattr(sndk_read, "SEMANTIC_GUARD", False)
