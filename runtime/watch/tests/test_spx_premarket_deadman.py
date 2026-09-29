"""The dead-man's switch for the SPX pre-market lane.

Each case is one way a night can go wrong without anyone seeing it: the Mac
asleep at the first checkpoint, one checkpoint dropped, a close-out that never
landed. The channel is injected, so nothing here touches the network;
`market_status` is the real one, so the market-day gate is exercised.
"""
import json
from datetime import datetime

import pytest

from watch.intraday import spx_premarket_deadman as D

DAY = "2026-09-28"                                        # a Monday
READS = ("02:35", "03:35", "08:05", "08:48", "09:05", "09:28")


def _at(hhmm, day=DAY):
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=D.ET)


@pytest.fixture
def lane(tmp_path):
    """A state dir with the pre-market lane's folder, a sent-message list, and writers for its card and reads."""
    folder = tmp_path / "spx_jev" / "lanes" / "premarket"
    folder.mkdir(parents=True)
    sent: list = []

    def card(day=DAY, reads=READS, closed=False, tally=None):
        (folder / "latest.json").write_text(json.dumps(
            {"day": day, "lane": "premarket", "closed_out_at": f"{day}T14:06:04+00:00" if closed else None,
             "schedule": {"reads": [f"{day}T{c}:00-04:00" for c in reads], "close_out": f"{day}T10:06:00-04:00"},
             **({"tally": tally} if tally else {})}))

    def read(*checkpoints, day=DAY):
        (folder / f"{day}.jsonl").write_text("".join(
            json.dumps({"row_ts": f"{day}T{c}:04-04:00", "checkpoint": c}) + "\n" for c in checkpoints))

    def run(now, **kw):
        return D.run(now, state_dir=tmp_path, channel=kw.pop("channel", sent.append), **kw)

    return type("Lane", (), {"sent": sent, "card": staticmethod(card), "read": staticmethod(read),
                             "run": staticmethod(run), "dir": tmp_path})


def test_a_night_with_every_read_and_the_close_out_says_nothing(lane):
    lane.card(closed=True)
    lane.read(*READS)
    out = lane.run(_at("10:30"))
    assert out["checked"] is True and out["missed"] == [] and lane.sent == []


def test_nothing_is_owed_before_the_first_checkpoints_read_is(lane):
    lane.card(day="2026-09-25")                                               # Friday's card
    out = lane.run(_at("02:49"))
    assert out["checked"] is False and lane.sent == []


def test_a_night_the_job_never_ran_pages_once_and_not_again_for_the_same_checkpoint(lane):
    """The Mac asleep at 02:35: the card is still Friday's at 02:50. When the 03:35 read lands, the 02:35
    checkpoint it missed has already been paged."""
    lane.card(day="2026-09-25")
    out = lane.run(_at("02:50"))
    assert out["missed"] == ["02:35"] and len(lane.sent) == 1
    assert "no pre-market read today" in lane.sent[0] and "02:50 ET" in lane.sent[0]
    assert lane.run(_at("02:55"))["paged"] == 0
    lane.card()
    lane.read("03:35")
    out = lane.run(_at("03:55"))
    assert out["missed"] == ["02:35"] and out["paged"] == 0 and len(lane.sent) == 1


def test_a_dropped_checkpoint_is_paged_once_its_read_is_owed(lane):
    lane.card()
    lane.read("02:35", "03:35", "08:48")
    assert lane.run(_at("08:19"))["missed"] == [] and lane.sent == []           # 08:05 is owed from 08:20
    out = lane.run(_at("08:20"))
    assert out["missed"] == ["08:05"] and len(lane.sent) == 1 and "no read at the 08:05 ET checkpoint" in lane.sent[0]
    assert lane.run(_at("09:00"))["paged"] == 0


def test_a_close_out_that_never_landed_is_paged(lane):
    lane.card()
    lane.read(*READS)
    assert lane.run(_at("10:20"))["missed"] == []
    out = lane.run(_at("10:21"))
    assert out["missed"] == ["10:06"] and lane.sent[0].startswith(
        "🔴 SPX pre-market: the 10:06 ET close-out has not landed; a later live read tries again (10:21 ET).")
    assert "never read later" not in lane.sent[0]


def test_a_close_out_that_left_the_call_ungraded_is_paged_once(lane):
    """09-28: the bars stopped from 09:56 to 10:11, so the 10:06 close-out graded the 10-minute check and left the
    30-minute one, the lane's main call, pending, and the switch said every read owed was on file. A card that still
    has a call to grade after the close-out is a miss, whether or not it says it closed out (a card from before the
    close-out waited for its grades said so anyway); a call closed for good is not one still to grade."""
    lane.read(*READS)
    lane.card(closed=True, tally={"calls": 1, "graded": 0, "right": 0})
    assert lane.run(_at("10:20"))["missed"] == []
    out = lane.run(_at("10:21"))
    assert out["missed"] == ["10:06"] and lane.sent[0] == \
        "🔴 SPX pre-market: 1 pre-market call still ungraded after the 10:06 ET close-out; a later live read tries again (10:21 ET)."
    lane.card(tally={"calls": 1, "graded": 0, "right": 0, "unsure": 0, "closed": 0})
    assert lane.run(_at("10:26"))["missed"] == ["10:06"] and len(lane.sent) == 1
    lane.card(closed=True, tally={"calls": 2, "graded": 1, "right": 1, "unsure": 0, "closed": 1})
    assert lane.run(_at("10:31"))["missed"] == [] and len(lane.sent) == 1


def test_europes_checkpoint_is_the_one_the_card_names(lane):
    """In the week Frankfurt keeps winter time the 03:35 checkpoint is 04:35: the card names it, and 03:35 is not owed."""
    week = ("02:35", "04:35", "08:05", "08:48", "09:05", "09:28")
    lane.card(reads=week, closed=True)
    lane.read(*week)
    assert lane.run(_at("10:30"))["missed"] == [] and lane.sent == []


@pytest.mark.parametrize("day", ["2026-09-26", "2026-11-26"])                 # a Saturday; Thanksgiving
def test_a_day_the_market_is_shut_is_never_checked(lane, day):
    lane.card(day="2026-09-25")
    out = lane.run(_at("10:30", day))
    assert out["checked"] is False and lane.sent == []


def test_an_undelivered_page_is_retried_not_remembered(lane):
    lane.card()
    lane.read("02:35", "03:35")

    def dead(msg):
        raise OSError("ntfy unreachable")
    out = lane.run(_at("08:20"), channel=dead)
    assert out["paged"] == 0 and out["undelivered"][0]["key"] == "08:05"
    assert lane.run(_at("08:25"))["paged"] == 1 and len(lane.sent) == 1


def test_the_ledger_starts_afresh_each_day(lane):
    lane.card()
    lane.read("02:35", "03:35")
    assert lane.run(_at("08:20"))["paged"] == 1
    lane.card(day="2026-09-29")
    lane.read("02:35", "03:35", day="2026-09-29")
    assert lane.run(_at("08:20", "2026-09-29"))["paged"] == 1 and len(lane.sent) == 2


def test_a_torn_read_line_is_not_a_read(lane):
    lane.card()
    lane.read("02:35")
    with open(lane.dir / "spx_jev" / "lanes" / "premarket" / f"{DAY}.jsonl", "a") as f:
        f.write('{"row_ts": "2026-09-28T03:35:04-04:00", "checkp')
    assert lane.run(_at("03:50"))["missed"] == ["03:35"]


def test_test_fire_proves_the_pager_without_reading_the_lane(lane):
    out = lane.run(_at("12:00"), test_fire=True)
    assert out["delivered"] is True and len(lane.sent) == 1 and "TEST FIRE" in lane.sent[0]
