"""ntfy push channel — deliver an alert (or the re-auth ping) to the phone.

Plain English:
Sending a notification is just one HTTPS POST to a public ntfy topic. The phone's
ntfy app is subscribed to the same topic, so the message pops up instantly. The
topic name is the only secret (keep it long and unguessable). Special headers
let us set a title, a priority, and — crucially — a "Click" link so tapping the
notification opens the Schwab login page.

The request building is separated from the actual sending so it can be unit-
tested without any network: `build_request(...)` returns a ready urllib Request
whose URL/headers/body we can assert on. `send(...)` performs the POST. If no
topic is configured, `send` logs and skips (so nothing breaks before setup).
"""
from __future__ import annotations

import urllib.request
from typing import Optional

from . import settings


def build_request(message: str, *, topic: str, server: str = "https://ntfy.sh",
                  title: Optional[str] = None, click_url: Optional[str] = None,
                  priority: Optional[str] = None,
                  tags: Optional[str] = None) -> urllib.request.Request:
    """Build (but don't send) the ntfy HTTP POST.

    - URL is server/topic; body is the message text (UTF-8).
    - Title/Priority/Tags/Click map to ntfy's headers.
    - Click is the important one: tapping the notification opens that URL.
    """
    url = f"{server.rstrip('/')}/{topic}"
    headers = {}
    if title:
        # ntfy reads the title from a header; encode to latin-1-safe for HTTP.
        headers["Title"] = _header_safe(title)
    if priority:
        headers["Priority"] = priority
    if tags:
        headers["Tags"] = tags
    if click_url:
        headers["Click"] = click_url
    data = message.encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers, method="POST")


def _header_safe(text: str) -> str:
    """HTTP headers must be latin-1. Emoji/unicode in a Title would raise, so we
    drop to ASCII for the header (the body keeps full unicode)."""
    return text.encode("ascii", "ignore").decode("ascii").strip() or "Mirai Watch"


def send(message: str, *, title: Optional[str] = None, click_url: Optional[str] = None,
         priority: Optional[str] = None, tags: Optional[str] = None,
         timeout: float = 10.0) -> dict:
    """POST the notification to ntfy. Returns a record dict (also used for logs).

    No-ops gracefully (dispatched=False) when no topic is configured yet.
    """
    topic = settings.ntfy_topic()
    if not topic:
        return {"dispatched": False, "reason": "no ntfy topic configured", "msg": message}
    req = build_request(message, topic=topic, server=settings.ntfy_server(),
                        title=title, click_url=click_url, priority=priority, tags=tags)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"dispatched": True, "status": resp.status, "msg": message}
    except Exception as e:  # network/HTTP error must never crash the caller
        return {"dispatched": False, "error": f"{type(e).__name__}: {e}", "msg": message}


def make_channel():
    """Return a `push.set_channel`-compatible callable that sends plain text.

    2026-08-30: the result of `send` used to be discarded. `push.send` decides
    `dispatched` by whether this callable raised, so every alert in the system —
    including the two feed sirens and the new SNDK dead-man's switch — was
    logged as delivered whether or not it left the machine, and with no ntfy
    topic configured NONE of them do. A pager whose log says "sent" while the
    phone stays quiet is worse than no pager: it is a guard that reports success
    for its own failure. Raising is what `push.send` already understands — it
    catches and records the reason on the row."""
    def _channel(text: str) -> None:
        res = send(text)
        if not res.get("dispatched"):
            raise RuntimeError(res.get("reason") or res.get("error")
                               or "ntfy did not dispatch")
    _channel.__name__ = "ntfy"
    return _channel
