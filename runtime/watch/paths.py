"""Single source of truth for every path Mirai Watch touches.

All paths are resolved at import time from environment variables, with sensible
defaults derived from the plugin root (this file's location). Nothing here is
hardcoded to a specific user's home directory — copying the plugin to a
different Mac and running `setup-watch.sh` is enough to bring everything online.

Environment variables (set in `runtime/scripts/env.sh`):

    MIRAI_STATION_ROOT        plugin root              (default: derived from __file__)
    MIRAI_STATION_STATE       state dir                (default: $ROOT/state)
    MIRAI_STATION_LOGS        log dir                  (default: $STATE/logs)
    MIRAI_STATION_STORE       LanceDB dir              (default: $STATE/right_eye.lance)
    MIRAI_STATION_VAULT       user's investments vault (default: ~/Documents/Investments)

"""
from __future__ import annotations

import os
from pathlib import Path

# ---------- Plugin root ----------
# This file lives at $ROOT/runtime/watch/paths.py — go up 2 dirs to get $ROOT.
_THIS_FILE = Path(__file__).resolve()
PLUGIN_ROOT: Path = Path(
    os.environ.get("MIRAI_STATION_ROOT") or _THIS_FILE.parents[2]
).expanduser().resolve()

# ---------- Bundled inside the plugin ----------
SKILLS_DIR: Path = PLUGIN_ROOT / "skills"
RIGHT_EYE_SKILL: Path = SKILLS_DIR / "mirai-right-eye"
LEFT_EYE_SKILL: Path = SKILLS_DIR / "mirai-left-eye"
WATCH_DIR: Path = PLUGIN_ROOT / "runtime" / "watch"

# ---------- State / logs / store ----------
STATE_DIR: Path = Path(
    os.environ.get("MIRAI_STATION_STATE") or (PLUGIN_ROOT / "state")
).expanduser()
LOG_DIR: Path = Path(
    os.environ.get("MIRAI_STATION_LOGS") or (STATE_DIR / "logs")
).expanduser()
STORE_DIR: Path = Path(
    os.environ.get("MIRAI_STATION_STORE") or (STATE_DIR / "right_eye.lance")
).expanduser()
# ---------- User-specific (machine-configurable) ----------
VAULT_DIR: Path = Path(
    os.environ.get("MIRAI_STATION_VAULT") or "~/Documents/Investments"
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
        ("STORE_DIR", STORE_DIR, "created"),
        ("VAULT_DIR", VAULT_DIR, "user"),
    ]
    return [
        {"name": name, "path": str(p), "exists": p.exists(), "kind": kind}
        for name, p, kind in items
    ]
