"""Push notification dispatcher — delivered via ntfy (push_ntfy).

The plan explicitly leaves the concrete push mechanism unspecified. This module
exposes a single `send(msg)` entrypoint and a `set_channel(callable)` hook so
the channel can be wired in a later step without touching node code.

Until a channel is configured, `send()` logs to stdout and the daily JSONL log
so dry-runs and tests can verify the push *intent* without dispatching anywhere.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Callable, Optional

try:                                    # same guard market_status uses: a missing
    from zoneinfo import ZoneInfo       # tzdata must never stop a page going out
    _ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover - tzdata absent
    _ET = None

from . import paths

_channel: Optional[Callable[[str], None]] = None

_LOG_DIR = str(paths.LOG_DIR)


def _now() -> datetime:
    """The pager's clock, in market time. The daily log is named for the ET day
    and the record is stamped from the same instant, so an evening page files
    under the session it belongs to: before this, utcnow() put every page after
    20:00 ET into the NEXT day's file, under a stamp that read as tomorrow."""
    return datetime.now(_ET) if _ET is not None else datetime.now().astimezone()


def set_channel(fn: Callable[[str], None]) -> None:
    """Register a concrete push function. Called once at startup if configured."""
    global _channel
    _channel = fn


def send(msg: str, *, tag: str = "push") -> dict:
    """Dispatch a push notification. Always logs; dispatches only if channel configured.

    Returns the record dict for inclusion in graph state / logs.
    """
    now = _now()
    ts = now.isoformat()
    record = {"ts": ts, "tag": tag, "msg": msg, "dispatched": False, "channel": None}

    if _channel is not None:
        try:
            _channel(msg)
            record["dispatched"] = True
            record["channel"] = getattr(_channel, "__name__", "channel")
        except Exception as e:
            record["error"] = str(e)

    # Always log (so dry-runs leave a trail). The trail is a by-product: a log
    # that cannot be written costs the record, never the notification — and the
    # page has already gone out by here. makedirs is inside the guard too, since
    # an unusable path raises there (ValueError) before open() is ever reached.
    log_path = os.path.join(_LOG_DIR, f"watch-pushes-{now.strftime('%Y-%m-%d')}.jsonl")
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, ValueError):
        pass

    # Mirror to stdout when interactive (TTY).
    if sys.stdout.isatty() or os.environ.get("MIRAI_WATCH_VERBOSE"):
        print(f"[push:{tag}] {msg}")

    return record
