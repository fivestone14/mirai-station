"""Config loader. Single source of truth: config.json next to this module.

Cached per process. `load(force=True)` reloads from disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
_CACHED: Optional[dict[str, Any]] = None


def load(force: bool = False) -> dict[str, Any]:
    global _CACHED
    if _CACHED is None or force:
        with open(_CONFIG_PATH, "r") as f:
            _CACHED = json.load(f)
    return _CACHED
