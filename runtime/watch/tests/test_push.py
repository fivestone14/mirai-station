"""The pager's own log — the one record of what reached the phone.

Untested until now, which is how it drifted: every page was stamped and filed
by UTC day, so a page sent at 20:30 ET landed in the NEXT day's file under a
timestamp that read as tomorrow. A page is always about the session it was sent
in, so both the name and the stamp are market time.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from watch import push

ET = ZoneInfo("America/New_York")


def _log_files(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.glob("watch-pushes-*.jsonl"))


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("when, day", [
    (datetime(2026, 9, 15, 20, 30, tzinfo=ET), "2026-09-15"),   # 00:30 UTC tomorrow
    (datetime(2026, 9, 15, 23, 59, tzinfo=ET), "2026-09-15"),   # 03:59 UTC tomorrow
    (datetime(2026, 9, 15, 9, 31, tzinfo=ET), "2026-09-15"),    # same day either way
    (datetime(2026, 1, 6, 19, 5, tzinfo=ET), "2026-01-06"),     # winter offset
])
def test_a_page_is_filed_under_the_session_it_was_sent_in(tmp_path, monkeypatch, when, day):
    """An evening page belongs to that day's log, not to the next UTC day's."""
    monkeypatch.setattr(push, "_now", lambda: when)
    push.send("a wall broke", tag="alert")
    assert _log_files(tmp_path) == [f"watch-pushes-{day}.jsonl"]


def test_the_stamp_is_market_time_and_agrees_with_the_file_it_is_in(tmp_path, monkeypatch):
    """The record's own date cannot disagree with the day it is filed under, and
    the stamp carries its offset — a bare stamp cannot say which day it means."""
    monkeypatch.setattr(push, "_now", lambda: datetime(2026, 9, 15, 20, 30, tzinfo=ET))
    rec = push.send("a wall broke")
    log = tmp_path / "watch-pushes-2026-09-15.jsonl"
    stamped = datetime.fromisoformat(rec["ts"])
    assert stamped.utcoffset() is not None, rec["ts"]
    assert stamped.strftime("%Y-%m-%d") == "2026-09-15"
    assert _rows(log)[-1]["ts"] == rec["ts"]


def test_the_real_clock_is_market_time_not_utc():
    """The seam the tests drive is the one production uses: no test may pass by
    stubbing a clock the live pager does not read."""
    now = push._now()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(ET).utcoffset()


def test_the_record_says_whether_it_left_the_machine(tmp_path, monkeypatch):
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
    assert [r["dispatched"] for r in _rows(tmp_path / "watch-pushes-2026-09-15.jsonl")] == [False, True]


def test_a_channel_that_throws_is_recorded_and_never_raised(tmp_path, monkeypatch):
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
    assert _rows(tmp_path / "watch-pushes-2026-09-15.jsonl")[-1]["error"] == rec["error"]


def test_a_log_that_cannot_be_written_never_stops_the_page(tmp_path, monkeypatch):
    """The log is a by-product. A full disk or a bad path costs the trail, never
    the notification."""
    monkeypatch.setattr(push, "_now", lambda: datetime(2026, 9, 15, 11, 0, tzinfo=ET))
    monkeypatch.setattr(push, "_LOG_DIR", str(tmp_path / "nope" / "\0bad"), raising=False)
    sent = []
    push.set_channel(sent.append)
    try:
        rec = push.send("a wall broke")
    finally:
        push.set_channel(None)
    assert rec["dispatched"] is True and sent == ["a wall broke"]
