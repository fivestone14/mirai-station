"""Loader for the bundled mirai-right-eye skill package.

The skill directory name contains a hyphen and its modules import each other
relatively (``from . import _config``), so it cannot be imported via a bare
sys.path entry — that yields "attempted relative import with no known parent
package". It must be registered as a real package under a valid module alias.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys

from . import paths

_PKG = "mirai_right_eye"


def load(submodule: str, attr: str):
    """Return ``attr`` from the skill's ``submodule``, loading the package on demand.

    Example: ``embed = load("embed", "embed_text")``. Raises ImportError
    (or whatever the skill raises at import time) — callers catch and degrade.
    """
    if _PKG not in sys.modules:
        skill_dir = paths.RIGHT_EYE_SKILL
        spec = importlib.util.spec_from_file_location(
            _PKG,
            str(skill_dir / "__init__.py"),
            submodule_search_locations=[str(skill_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"right-eye skill package not found at {skill_dir}")
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = pkg
        try:
            spec.loader.exec_module(pkg)
        except BaseException:
            del sys.modules[_PKG]
            raise
    return getattr(importlib.import_module(f"{_PKG}.{submodule}"), attr)
