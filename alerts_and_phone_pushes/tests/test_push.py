"""The pager's record — what was sent, whether a channel took it, and when in market time.

A page is always about the session it was sent in, so the stamp is market time
with its offset: a bare or UTC stamp read an evening page as tomorrow's.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from watch import push

ET = ZoneInfo("America/New_York")


def test_the_stamp_is_market_time_and_carries_its_offset(monkeypatch):
    """The record's own date cannot read as the next UTC day, and the stamp
    carries its offset — a bare stamp cannot say which day it means."""
    monkeypatch.setattr(push, "_now", lambda: datetime(2026, 9, 15, 20, 30, tzinfo=ET))
    rec = push.send("a wall broke")
    stamped = datetime.fromisoformat(rec["ts"])
    assert stamped.utcoffset() is not None, rec["ts"]
    assert stamped.strftime("%Y-%m-%d") == "2026-09-15"


def test_the_real_clock_is_market_time_not_utc():
    """The seam the tests drive is the one production uses: no test may pass by
    stubbing a clock the live pager does not read."""
    now = push._now()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(ET).utcoffset()


def test_the_record_says_whether_it_left_the_machine(monkeypatch):
    """Both halves of the record a reader relies on: what was sent, and whether
    a channel actually took it."""
    monkeypatch.setattr(push, "_now", lambda: datetime(2026, 9, 15, 11, 0, tzinfo=ET))
    undelivered = push.send("nobody listening", tag="alert")
    assert (undelivered["dispatched"], undelivered["channel"]) == (False, None)
    assert undelivered["tag"] == "alert" and undelivered["msg"] == "nobody listening"

    def channel(msg):
        channel.seen.append(msg)
    channel.seen = []
    push.set_channel(channel)
    try:
        delivered = push.send("a wall broke")
    finally:
        push.set_channel(None)
    assert delivered["dispatched"] is True and channel.seen == ["a wall broke"]


def test_a_channel_that_throws_is_recorded_and_never_raised(monkeypatch):
    """A broken channel must not take down the job that was paging: the failure
    is the record, not an exception."""
    monkeypatch.setattr(push, "_now", lambda: datetime(2026, 9, 15, 11, 0, tzinfo=ET))

    def broken(msg):
        raise RuntimeError("ntfy unreachable")
    push.set_channel(broken)
    try:
        rec = push.send("a wall broke")
    finally:
        push.set_channel(None)
    assert rec["dispatched"] is False and "ntfy unreachable" in rec["error"]
