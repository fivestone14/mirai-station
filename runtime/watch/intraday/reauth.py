"""No-password Schwab re-auth — build the one-tap login link and the ping.

Plain English:
When the login is about to expire, we don't store a password and log in for you
(that's the constraint, and Schwab blocks it anyway). Instead we build the link
to Schwab's OWN login page and send it to your phone. You tap it, log in on
Schwab's site (where you're likely already signed in — a session cookie or
passkey, no password typed), and Schwab hands a fresh login back. Nothing secret
of yours is stored anywhere in this flow.

This module builds two pure things (both unit-testable):
  - the Schwab authorize URL (the link you tap)
  - the alert text + the tap-action for the push

The actual token capture after you tap uses schwab-py's client_from_manual_flow
and runs in `auth_check.py` / on the mini (it needs schwab-py + your input).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from .token_watcher import AuthStatus

# Schwab's OAuth2 authorization endpoint (the page the user logs in on).
SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"


def build_authorize_url(api_key: str, callback_url: str) -> str:
    """Construct the Schwab login link. Standard OAuth2 auth-code request:
    client_id = your App Key, redirect_uri = your registered callback.

    The user logs in ON SCHWAB'S DOMAIN; no credential of theirs touches us.
    """
    query = urlencode({
        "client_id": api_key,
        "redirect_uri": callback_url,
        "response_type": "code",
    })
    return f"{SCHWAB_AUTHORIZE_URL}?{query}"


@dataclass
class ReauthPing:
    """Everything needed to send the re-auth notification."""
    title: str
    message: str
    click_url: Optional[str]   # tapping the notification opens this (the login link)
    priority: str              # ntfy priority: "default" | "high" | "urgent"


def build_reauth_ping(status: AuthStatus, authorize_url: Optional[str]) -> ReauthPing:
    """Compose the phone ping for a given auth status.

    'expired' is urgent (the routine is already blind); 'warn'/'unknown' are a
    high-priority heads-up with a day of runway.
    """
    if status.state == "expired":
        title = "🔑 Schwab login expired — re-auth now"
        priority = "urgent"
    else:  # warn / unknown
        days = f"{max(status.days_left, 0):.0f}" if status.days_left is not None else "?"
        title = f"🔑 Schwab login expires in ~{days}d — tap to renew"
        priority = "high"

    body = status.reason
    if authorize_url:
        body += "\nTap to log in on Schwab (no password stored)."
    else:
        body += "\nRun enrollment on the mini to renew."

    return ReauthPing(title=title, message=body, click_url=authorize_url, priority=priority)
