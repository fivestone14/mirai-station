"""Config loader. Single source of truth: config.json next to this module.

Cached per process. `load(force=True)` reloads from disk.
"""
from __future__ import annotations

import json
import os
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


def store_path(config: dict[str, Any]) -> str:
    return os.path.expanduser(config["store_path"])


def audit_log_dir(config: dict[str, Any]) -> str:
    p = os.path.expanduser(config["audit_log_dir"])
    Path(p).mkdir(parents=True, exist_ok=True)
    return p


def vector_dim(config: dict[str, Any]) -> int:
    return int(config["embedding_model"]["dimension"])


def model_id(config: dict[str, Any]) -> str:
    em = config["embedding_model"]
    return f"{em['provider']}:{em['name']}"
