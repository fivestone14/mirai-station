"""Push notification dispatcher — delivered via ntfy (push_ntfy).

The plan explicitly leaves the concrete push mechanism unspecified. This module
exposes a single `send(msg)` entrypoint and a `set_channel(callable)` hook so
the channel can be wired in a later step without touching node code.

Until a channel is configured, `send()` only returns the record (and mirrors it
to stdout when interactive), so dry-runs and tests can verify the push *intent*
without dispatching anywhere. A daily JSONL log of every page
(state/logs/watch-pushes-<day>.jsonl) was kept until 2026-10-11; nothing read it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Callable, Optional

try:                                    # same guard market_status uses: a missing
    from zoneinfo import ZoneInfo       # tzdata must never stop a page going out
    _ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover - tzdata absent
    _ET = None

_channel: Optional[Callable[[str], None]] = None


def _now() -> datetime:
    """The pager's clock, in market time. A page is about the session it was
    sent in, so its record is stamped in ET with its offset: before this,
    utcnow() stamped every page after 20:00 ET with a time that read as tomorrow."""
    return datetime.now(_ET) if _ET is not None else datetime.now().astimezone()


def set_channel(fn: Callable[[str], None]) -> None:
    """Register a concrete push function. Called once at startup if configured."""
    global _channel
    _channel = fn


def send(msg: str, *, tag: str = "push") -> dict:
    """Dispatch a push notification; dispatches only if a channel is configured.

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

    # Mirror to stdout when interactive (TTY).
    if sys.stdout.isatty() or os.environ.get("MIRAI_WATCH_VERBOSE"):
        print(f"[push:{tag}] {msg}")

    return record
