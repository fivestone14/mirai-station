"""Offline fixtures: a temp state root with synthetic station files. No network, no host state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spx_claude_forecast import jsonl_store, paths as forecast_paths  # noqa: E402

DAY = "2026-10-09"
ET_OFFSET = "-04:00"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """A station state root with the forecast folder created and writable."""
    root = tmp_path / "state"
    paths = forecast_paths.ensure_folders(root)
    jsonl_store.allow_writes_under(paths.root, tmp_path / "backup")
    return root


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path
