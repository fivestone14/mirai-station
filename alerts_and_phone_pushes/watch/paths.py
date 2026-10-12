"""Single source of truth for every path Mirai Watch touches.

All paths are resolved at import time, with sensible defaults derived from the
repo root (this file's location). Nothing here is hardcoded to a specific
user's home directory — copying the repo to a different Mac and running
`venv-bootstrap.sh` is enough to bring everything online.

Environment variables (set in `scheduled_jobs/job_start_scripts/env.sh`):

    MIRAI_STATION_ROOT        the repo root            (default: derived from __file__)
    MIRAI_STATION_STATE       the data root            (default: $ROOT/data; station_paths.py decides)
    MIRAI_STATION_LOGS        log dir                  (default: $STATE/operations/logs/station_runtime_log)

The data stores themselves are named in station_paths.py; the jobs here
ask it for each file by name.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------- Repo root ----------
# This file lives at $ROOT/alerts_and_phone_pushes/watch/paths.py — go up 2 dirs to get $ROOT.
_THIS_FILE = Path(__file__).resolve()
PLUGIN_ROOT: Path = Path(
    os.environ.get("MIRAI_STATION_ROOT") or _THIS_FILE.parents[2]
).expanduser().resolve()

_REPO_ROOT = str(_THIS_FILE.parents[2])        # the repo root, where station_paths lives
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402

# ---------- The folders the jobs reach into ----------
SETTINGS_DIR: Path = PLUGIN_ROOT / "settings"
RIGHT_EYE_SKILL: Path = PLUGIN_ROOT / "pipelines" / "shared_text_embedder_for_note_search"
LEFT_EYE_SKILL: Path = PLUGIN_ROOT / "pipelines" / "spx_options_scanner"
WATCH_DIR: Path = PLUGIN_ROOT / "alerts_and_phone_pushes" / "watch"

# ---------- State / logs ----------
STATE_DIR: Path = station_paths.state_root()
LOG_DIR: Path = Path(
    os.environ.get("MIRAI_STATION_LOGS") or station_paths.STATION_RUNTIME_LOG.folder(STATE_DIR)
).expanduser()


def diagnostic() -> list[dict]:
    """Return a list of resolved paths + existence checks for `mirai-watch doctor`."""
    items = [
        ("PLUGIN_ROOT", PLUGIN_ROOT, "required"),
        ("SETTINGS_DIR", SETTINGS_DIR, "required"),
        ("RIGHT_EYE_SKILL", RIGHT_EYE_SKILL, "required"),
        ("LEFT_EYE_SKILL", LEFT_EYE_SKILL, "required"),
        ("WATCH_DIR", WATCH_DIR, "required"),
        ("STATE_DIR", STATE_DIR, "created"),
        ("LOG_DIR", LOG_DIR, "created"),
    ]
    return [
        {"name": name, "path": str(p), "exists": p.exists(), "kind": kind}
        for name, p, kind in items
    ]
