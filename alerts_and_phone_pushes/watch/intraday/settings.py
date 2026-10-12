"""Load the routine's tunable numbers from settings/*.json.

Plain English: this is the one place the code reads its settings from. A human
edits the JSON files in the config folder; this module hands those values to the
rest of the routine. If a config file is missing or a key was deleted, we fall
back to the documented default instead of crashing (graceful degradation).

Keys beginning with "_" in the JSON are documentation for humans and are ignored
by the code, so writers can annotate freely without breaking anything.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

# The settings folder sits at the repo root (this file is alerts_and_phone_pushes/watch/intraday/settings.py): settings/
_CONFIG_DIR = Path(__file__).resolve().parents[3] / "settings"

# Defaults mirror the JSON files. They exist so the routine still runs sensibly
# if a config file is ever missing on a fresh machine.
# Trimmed 2026-07-04: the voter/bet-era knobs (watchlist, alert_throttle, tick,
# right_eye_gate, tilt, lifecycle, peaks, decay, retired model task names) had no
# remaining callers — the gex-only system reads exactly these. Trimmed again
# 2026-10-11: the bayes prior and the model tiers went with the macro brief (the
# Watchtower reads its own model pin straight from limits-and-cooldowns.json).
_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "limits-and-cooldowns": {
        "wall_breach": {
            "buffer_frac": 0.10,
            "cooldown_min": 20,
        },
    },
}


def _strip_doc_keys(value: Any) -> Any:
    """Recursively drop keys starting with '_' (human-only documentation)."""
    if isinstance(value, dict):
        return {k: _strip_doc_keys(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_strip_doc_keys(v) for v in value]
    return value


@lru_cache(maxsize=None)
def _load_file(name: str) -> Dict[str, Any]:
    """Read one config file by stem (e.g. 'limits-and-cooldowns'); cached after first read."""
    path = _CONFIG_DIR / f"{name}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Missing or unparseable -> use the documented defaults rather than crash.
        return dict(_DEFAULTS.get(name, {}))
    return _strip_doc_keys(raw)


def reload() -> None:
    """Forget cached config so the next read picks up edited files (used by tests)."""
    _load_file.cache_clear()


def _get(name: str, *path: str, default: Any = None) -> Any:
    """Walk a dotted path into a config file, returning `default` if any hop is absent."""
    node: Any = _load_file(name)
    fallback: Any = _DEFAULTS.get(name, {})
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            node = None
        if isinstance(fallback, dict):
            fallback = fallback.get(key)
    if node is None:
        return fallback if fallback is not None else default
    return node


# ---------- Typed accessors (the rest of the routine calls these) ----------

# ---------- Auth keep-alive (Schwab 7-day token) ----------

def auth_warn_after_days() -> float:
    return float(_get("limits-and-cooldowns", "auth", "warn_after_days", default=6))


def auth_hard_limit_days() -> float:
    return float(_get("limits-and-cooldowns", "auth", "hard_limit_days", default=7))


# ---------- ntfy push channel ----------

def ntfy_topic() -> str:
    """The ntfy topic to publish to. The env var MIRAI_NTFY_TOPIC wins over config
    so a secret topic can be supplied without editing a file."""
    import os
    return os.environ.get("MIRAI_NTFY_TOPIC") or str(
        _get("limits-and-cooldowns", "ntfy", "topic", default="") or ""
    )


def ntfy_server() -> str:
    return str(_get("limits-and-cooldowns", "ntfy", "server", default="https://ntfy.sh"))


# ---------- The wall-breach bell (intraday/gex_alerts.py) ----------


def wall_breach_buffer_frac() -> float:
    """The bell's fallback check rings only when spot pushes past a gamma wall by this
    fraction of the implied daily move — filters ordinary in-wall pullbacks."""
    return float(_get("limits-and-cooldowns", "wall_breach", "buffer_frac", default=0.10))


def wall_breach_cooldown_min() -> float:
    """Min minutes between two pages for the same ticker+wall."""
    return float(_get("limits-and-cooldowns", "wall_breach", "cooldown_min", default=20))
