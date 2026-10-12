"""Single source of truth for every path Mirai Watch touches.

All paths are resolved at import time, with sensible defaults derived from the
plugin root (this file's location). Nothing here is hardcoded to a specific
user's home directory — copying the plugin to a different Mac and running
`venv-bootstrap.sh` is enough to bring everything online.

Environment variables (set in `runtime/scripts/env.sh`):

    MIRAI_STATION_ROOT        plugin root              (default: derived from __file__)
    MIRAI_STATION_STATE       state dir                (default: $ROOT/state; runtime/station_paths.py decides)
    MIRAI_STATION_LOGS        log dir                  (default: $STATE/operations/logs/station_runtime_log)

The data stores themselves are named in runtime/station_paths.py; the jobs here
ask it for each file by name.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------- Plugin root ----------
# This file lives at $ROOT/runtime/watch/paths.py — go up 2 dirs to get $ROOT.
_THIS_FILE = Path(__file__).resolve()
PLUGIN_ROOT: Path = Path(
    os.environ.get("MIRAI_STATION_ROOT") or _THIS_FILE.parents[2]
).expanduser().resolve()

_RUNTIME = str(_THIS_FILE.parents[1])          # runtime/, where station_paths lives (already first on sys.path under -m watch)
if _RUNTIME not in sys.path:
    sys.path.append(_RUNTIME)
import station_paths  # noqa: E402

# ---------- Bundled inside the plugin ----------
SKILLS_DIR: Path = PLUGIN_ROOT / "skills"
RIGHT_EYE_SKILL: Path = SKILLS_DIR / "mirai-right-eye"
LEFT_EYE_SKILL: Path = SKILLS_DIR / "mirai-left-eye"
WATCH_DIR: Path = PLUGIN_ROOT / "runtime" / "watch"

# ---------- State / logs ----------
STATE_DIR: Path = station_paths.state_root()
LOG_DIR: Path = Path(
    os.environ.get("MIRAI_STATION_LOGS") or station_paths.STATION_RUNTIME_LOG.folder(STATE_DIR)
).expanduser()


def diagnostic() -> list[dict]:
    """Return a list of resolved paths + existence checks for `mirai-watch doctor`."""
    items = [
        ("PLUGIN_ROOT", PLUGIN_ROOT, "required"),
        ("SKILLS_DIR", SKILLS_DIR, "required"),
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
