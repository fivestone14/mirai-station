"""Schwab login keep-alive — detect when the 7-day token is about to die.

Plain English:
Schwab's API login (the "refresh token") stops working exactly 7 days after the
interactive browser login that created it. There is NO way to renew it with code
alone — Schwab requires a fresh human login every 7 days (confirmed by research).
So the routine can't avoid that tap; it can only make it painless by pinging you
BEFORE the login dies, instead of discovering it's dead mid-session.

This module is the detector. Once a day a small job calls `classify()`, which
reads when the token was created and reports one of:
    ok       - plenty of time left, do nothing
    warn     - day 6+, time to ping the phone to re-auth
    expired  - day 7+, the login is already dead, ping urgently
    unknown  - couldn't read the creation time, treat as needs-reauth (fail safe)

The age math is pure (timestamp in, status out) so it is fully unit-testable
here without any Schwab connection. The thin I/O wrapper that decrypts the real
token and sends the ping lives in `auth_check.py`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from . import settings


@dataclass
class AuthStatus:
    state: str                    # ok | warn | expired | unknown
    age_days: Optional[float]     # how old the login is, in days
    days_left: Optional[float]    # days until the 7-day wall (negative if past)
    reason: str                   # plain-English explanation


def creation_timestamp_from_token(token: dict) -> Optional[int]:
    """Pull the login-creation time (unix seconds) out of a schwab-py token dict.

    schwab-py wraps the OAuth data as {"creation_timestamp": <int>, "token": {...}}
    and stamps creation_timestamp at the original interactive login — that is the
    anchor for the 7-day clock. We also look one level in, defensively, in case a
    future token shape nests it. Returns None if it can't be found (-> unknown).
    """
    if not isinstance(token, dict):
        return None
    ts = token.get("creation_timestamp")
    if ts is None:
        inner = token.get("token")
        if isinstance(inner, dict):
            ts = inner.get("creation_timestamp")
    try:
        return int(ts) if ts is not None else None
    except (TypeError, ValueError):
        return None


def token_age_days(creation_timestamp: Optional[int], now: Optional[float] = None) -> Optional[float]:
    """Age of the login in days. None if we don't know when it was created.

    `now` (unix seconds) is injected so the calculation is deterministic and
    testable; the real runner passes time.time().
    """
    if creation_timestamp is None:
        return None
    now = now if now is not None else time.time()
    return (now - float(creation_timestamp)) / 86400.0


def classify(creation_timestamp: Optional[int], now: Optional[float] = None) -> AuthStatus:
    """Decide what to do about the login, given when it was created."""
    warn = settings.auth_warn_after_days()
    hard = settings.auth_hard_limit_days()
    age = token_age_days(creation_timestamp, now)

    if age is None:
        return AuthStatus("unknown", None, None,
                          "Can't read the login's creation time — re-auth to be safe.")
    days_left = hard - age
    if age >= hard:
        return AuthStatus("expired", age, days_left,
                          "Schwab login has expired (past 7 days) — re-auth required now.")
    if age >= warn:
        return AuthStatus("warn", age, days_left,
                          f"Schwab login expires in ~{max(days_left, 0):.1f} day(s) — re-auth soon.")
    return AuthStatus("ok", age, days_left,
                      f"Schwab login healthy (~{days_left:.1f} day(s) left).")


def needs_reauth(status: AuthStatus) -> bool:
    """True when the phone should be pinged. 'unknown' counts (fail safe — better
    to ping unnecessarily than to silently run on a dead login)."""
    return status.state in ("warn", "expired", "unknown")
