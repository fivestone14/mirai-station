"""Put the viewstation dir on sys.path so `import server` resolves the same way
it does under launchd (server.py inserts its own dir for snapshot/pipeline),
start every test from what the modules held at import, and say so loudly when
the tests that run the page's JavaScript could not."""
import ast
import copy
import importlib
import os
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_VIEWSTATION = os.path.dirname(_HERE)
sys.path.insert(0, _VIEWSTATION)

# Methods that fill a module-level container in place.
_FILLS = {"append", "appendleft", "extend", "insert", "update", "setdefault",
          "add", "pop", "popitem", "remove", "discard", "clear"}


def _kept_between_calls(source: str) -> set:
    """Module-level names the code rebinds (`global`) or fills in place (an
    item set, an append, an update) — its caches and request records. Read off
    the source, so a cache added later is reset without a list to update."""
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
    """{(module, name): its value at import} for every viewstation module's kept state."""
    state = {}
    for path in sorted(Path(_VIEWSTATION).glob("*.py")):
        for name in _kept_between_calls(path.read_text()):
            module = importlib.import_module(path.stem)
            state[(module, name)] = copy.deepcopy(getattr(module, name))
    return state


_MODULE_STATE = _as_imported()


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """server memoizes the last snapshot for five seconds and keeps a ring of
    recent requests; snapshot caches the last spot quote and the tape's append
    floor. A test run inside those windows would be answered from the previous
    test's snapshot, quote or request. Each test starts from the values the
    modules were imported with."""
    for (module, name), value in _MODULE_STATE.items():
        monkeypatch.setattr(module, name, copy.deepcopy(value))


def pytest_terminal_summary(terminalreporter):
    """The tests that run the page's real JavaScript skip when node is missing,
    and a skip reads as a pass in a quiet run. End the run by saying which code
    went unchecked."""
    skipped = [r for r in terminalreporter.stats.get("skipped", [])
               if "node" in str(r.longrepr)]
    if skipped:
        terminalreporter.write_line(
            f"node is not installed: {len(skipped)} tests that run the phone page's "
            f"JavaScript were skipped, so that code was not checked", red=True, bold=True)
