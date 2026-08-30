"""The SNDK scanner's dead-man's switch.

A watchdog nobody has watched fire is indistinguishable from one that cannot
fire — that is the whole reason this module exists, and it applies to the module
itself. Every case below is one way the August outages could have looked.

The channel is injected, so nothing here touches the network; `market_status` is
the real one, so the RTH gate is exercised rather than mocked away.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from watch.intraday import sndk_deadman as D

ET = ZoneInfo("America/New_York")
# A Thursday, mid-session — a real trading day, so market_status agrees.
LIVE = datetime(2026, 8, 27, 11, 4, tzinfo=ET)


@pytest.fixture
def desk(tmp_path):
    """A state dir, a sent-message list, and a writer for today's diary."""
    (tmp_path / "sndk_reversion").mkdir(parents=True)
    (tmp_path / "sndk_reads").mkdir(parents=True)
    sent: list = []

    def write(*rows):
        p = tmp_path / "sndk_reversion" / f"{LIVE.date().isoformat()}.jsonl"
        p.write_text("".join(
            (r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))

    def row(minutes_ago, **kw):
        return {"ticker": "SNDK", "spot": 1500.0,
                "ts": (LIVE - timedelta(minutes=minutes_ago)).isoformat(), **kw}

    def run(now=LIVE, **kw):
        return D.run(now, state_dir=tmp_path, channel=sent.append, **kw)

    return type("Desk", (), {"sent": sent, "write": staticmethod(write),
                             "row": staticmethod(row), "run": staticmethod(run),
                             "dir": tmp_path})


def test_a_live_scanner_says_nothing(desk):
    desk.write(desk.row(2))
    out = desk.run()
    assert out["alive"] is True and out["age_min"] == 2.0
    assert desk.sent == []


def test_a_silent_scanner_pages_once_and_names_the_frozen_time(desk):
    """The 08-07 / 08-14 / 08-17 / 08-24 shape: rows stop, every screen keeps
    drawing the last board, and nothing anywhere says so."""
    desk.write(desk.row(41))
    out = desk.run()
    assert out["alive"] is False and out["reason"] == "silent"
    assert len(desk.sent) == 1
    assert "41m" in desk.sent[0] and "10:23" in desk.sent[0]

    # a dead afternoon must cost one notification, not one every five minutes
    assert desk.run()["paged"] == 0
    assert len(desk.sent) == 1


def test_recovery_is_paged_too(desk):
    """Without this the last thing the phone ever said about SNDK is 'silent',
    and a stale alarm teaches the habit the alarm exists to prevent."""
    desk.write(desk.row(41))
    desk.run()
    desk.write(desk.row(41), desk.row(1))
    out = desk.run()
    assert out["alive"] is True
    assert len(desk.sent) == 2 and desk.sent[1].startswith("🟢")
    assert "silent since 10:23" in desk.sent[1]
    # and it recovers exactly once
    assert desk.run()["paged"] == 0


def test_no_row_after_0935_is_a_session_that_never_started(desk):
    out = desk.run()                       # no diary file at all
    assert out["reason"] == "no rows yet"
    assert len(desk.sent) == 1 and "never started" in desk.sent[0]


def test_no_row_before_0935_is_patience_not_a_failure(desk):
    """The scanner's first row lands within ~2 minutes of the open on every
    recorded session; five minutes is the allowance, and it is an allowance,
    not a blind spot — 09:36 pages."""
    assert desk.run(LIVE.replace(hour=9, minute=32))["paged"] == 0
    assert desk.sent == []
    assert desk.run(LIVE.replace(hour=9, minute=36))["paged"] == 1


def test_a_forced_warmup_row_does_not_count_as_a_session(desk):
    """`meta.forced` rows are off-hours manual runs. The reader ignores them
    everywhere else and so must this: one midnight warmup row must not make a
    dead morning look like a live one."""
    desk.write(desk.row(2, meta={"forced": True}))
    out = desk.run()
    assert out["reason"] == "no rows yet" and out["paged"] == 1


def test_a_torn_final_line_cannot_take_the_watchdog_down(desk):
    """The writer appends, so the last line is the one most likely to be
    half-written — and a line that PARSES to a bare string still is not a row.
    This case crashed the first draft with an AttributeError."""
    desk.write(desk.row(1), '"{torn half-written line')
    out = desk.run()
    assert out["alive"] is True and desk.sent == []


def test_a_closed_market_is_not_an_outage(desk):
    """Sunday. No rows is the correct state and pages nothing."""
    out = desk.run(datetime(2026, 8, 30, 11, 4, tzinfo=ET))
    assert out["checked"] is False and out["reason"] == "market closed"
    assert desk.sent == []


def test_the_ledger_does_not_leak_across_days(desk):
    """Yesterday's 'already paged' must not silence today."""
    desk.write(desk.row(41))
    desk.run()
    stale = json.loads((desk.dir / "sndk_reads" / "deadman_state.json").read_text())
    assert stale["date"] == "2026-08-27" and "silent" in stale["paged"]
    # same file, a later session: the state resets rather than suppressing
    tomorrow = LIVE + timedelta(days=1)
    (desk.dir / "sndk_reversion" / f"{tomorrow.date().isoformat()}.jsonl").write_text(
        json.dumps({"ticker": "SNDK", "spot": 1500.0,
                    "ts": (tomorrow - timedelta(minutes=41)).isoformat()}) + "\n")
    assert D.run(tomorrow, state_dir=desk.dir, channel=desk.sent.append)["paged"] == 1


def test_the_ceiling_is_the_readers_own_stale_book_line():
    """Not a patience setting. Six minutes is the age at which sndk_read stops
    spending a model call on the book, so the pager and the reader agree about
    when the data stopped being usable."""
    import sys
    from pathlib import Path
    skill = Path(__file__).resolve().parents[3] / "skills" / "sndk-pro"
    sys.path.insert(0, str(skill))
    import sndk_read
    assert D.SNDK_SILENT_MIN == float(sndk_read.STALE_BOOK_MIN)


def test_test_fire_proves_the_pager_without_touching_the_diary(desk):
    out = desk.run(test_fire=True)
    assert out["test_fire"] is True and len(desk.sent) == 1
    assert "TEST FIRE" in desk.sent[0]


# --- 2026-08-30, from the adversarial review of the sr-7 diff -----------------
# Three ways the first draft went quiet after doing its job once. Each of these
# is a day the switch would have been installed, fired correctly, and then slept
# through the outage that mattered.

def test_a_second_outage_the_same_day_still_pages(desk):
    """The first draft added a key to `paged` and never removed one, so the
    ledger that stops five-minute spam also stopped the afternoon. Replayed
    against a synthetic day built from the real 08-26 diary with two holes, it
    paged the 10:00 blip and slept through a 90-minute hole after lunch."""
    desk.write(desk.row(41))
    desk.run()                                    # outage 1 → paged
    desk.write(desk.row(41), desk.row(1))
    desk.run()                                    # recovered → re-armed
    assert len(desk.sent) == 2

    desk.write(desk.row(41), desk.row(1), desk.row(0.1))
    desk.write(*[desk.row(m) for m in (41, 20, 12)])   # silent again
    out = desk.run()
    assert out["reason"] == "silent" and out["paged"] == 1
    assert len(desk.sent) == 3 and desk.sent[2].startswith("🔴")


def test_a_false_never_started_is_retracted(desk):
    """Real shape: 2026-08-10's first row landed 09:44:18 and 08-07's at
    10:17:14. Both would have been paged "the session never started" at 09:35
    and never corrected — the recovery branch only looked for `silent`, so the
    phone's last word about SNDK for the day was a false statement, which the
    module's own docstring names as the reason recovery pages exist."""
    assert desk.run(LIVE.replace(hour=9, minute=36))["paged"] == 1
    assert "never started" in desk.sent[0]

    desk.write(desk.row(2))
    out = desk.run(LIVE.replace(hour=9, minute=46))
    assert out["alive"] is True and out["paged"] == 1
    assert desk.sent[1].startswith("🟢") and "wrong" in desk.sent[1]


def test_an_undelivered_page_is_retried_not_remembered(desk, monkeypatch):
    """The ledger must record DELIVERY, not intent. `push.send` catches the
    channel's failure and reports `dispatched: False` — an unreachable ntfy, a
    revoked topic — and a failure like that is plausibly correlated with the
    outage being paged about. Marking it sent would swallow the one
    notification the outage was ever going to get."""
    def dead(_text):
        raise RuntimeError("no ntfy topic configured")

    desk.write(desk.row(41))
    out = D.run(LIVE, state_dir=desk.dir, channel=dead)
    assert out["paged"] == 0
    assert out["undelivered"][0]["key"] == "silent"

    # nothing was remembered, so the next tick tries again — and lands
    out = desk.run()
    assert out["paged"] == 1 and len(desk.sent) == 1


def test_test_fire_reports_whether_it_actually_left_the_machine(desk):
    def dead(_text):
        raise RuntimeError("ntfy unreachable")
    out = D.run(LIVE, state_dir=desk.dir, channel=dead, test_fire=True)
    assert out["delivered"] is False and out["paged"] == 0 and out["error"]
    assert desk.run(test_fire=True)["delivered"] is True
