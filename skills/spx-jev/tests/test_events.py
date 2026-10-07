"""Scheduled events near a read (events.py): the tag is tier-1 only, within the hour, the 30-minute hold-out inclusive;
the other tiers, the calendar's reach and the Fed's quiet period are for the event labels."""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from spx_jev import events

ET = ZoneInfo("America/New_York")


def _cal(tmp_path, rows, covers_through=None):
    p = tmp_path / "events.json"
    p.write_text(json.dumps({"events": rows, **({"covers_through": covers_through} if covers_through else {})}))
    events._load.cache_clear()
    return p


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=ET)


def test_a_tier_one_event_within_the_hour_is_tagged_with_its_full_timestamp(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-10-28", "time_et": "14:00", "kind": "FOMC", "tier": 1},
                        {"date": "2026-10-28", "time_et": "14:30", "kind": "FOMC_PRESSER", "tier": 1},
                        {"date": "2026-10-28", "time_et": "14:00", "kind": "BEIGE_BOOK", "tier": 2}])
    g = events.tag(t("2026-10-28T13:32"), p)
    assert [e["kind"] for e in g["events"]] == ["FOMC", "FOMC_PRESSER"]
    assert g["events"][0]["at"] == "2026-10-28T14:00:00-04:00"
    assert g["soonest_min"] == 28 and g["within_30"] is True
    assert g["sentence"].startswith("a scheduled event is ahead: the Fed's rate decision, due in 28 minutes, at 14:00 ET")
    assert events.tag(t("2026-10-28T13:10"), p)["within_30"] is False


def test_the_boundaries(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-10-28", "time_et": "14:00", "kind": "FOMC", "tier": 1}])
    assert events.tag(t("2026-10-28T13:30"), p)["within_30"] is True
    assert events.tag(t("2026-10-28T13:29:30"), p)["within_30"] is False
    assert events.tag(t("2026-10-28T13:00"), p)["soonest_min"] == 60
    assert events.tag(t("2026-10-28T12:59"), p) is None
    assert events.tag(t("2026-10-28T14:00"), p) is None


def test_an_event_under_way_is_tagged(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-07-14", "time_et": "10:00", "end_et": "12:30", "kind": "FED_CHAIR_TESTIMONY", "tier": "1"}])
    g = events.tag(t("2026-07-14T11:02"), p)
    assert g["within_30"] is True and g["sentence"] == "a scheduled event is ahead: the Fed chair's testimony, under way until 12:30 ET"


def test_the_shipped_calendar_loads_every_row_has_words_and_none_is_sandisks():
    rows = events._load(str(events.CALENDAR)).events
    assert rows and all(e.kind in events.WORDS for e in rows)
    assert not any("SNDK" in e.kind for e in rows)
    tiers = {events.TIER, events.PRE_OPEN, events.DATA_10AM, events.DATA_2PM, events.FED_SPEAKER}
    assert {e.tier for e in rows} == tiers


def test_the_shipped_calendar_reaches_the_year_end_with_each_tier_on_its_clock():
    events._load.cache_clear()
    assert events.uncovered(date(2026, 12, 31)) is None
    assert events.uncovered(date(2027, 1, 4)) == "the event calendar (calendar/events.json) is kept only through 2026-12-31: extend it"
    rows = events._load(str(events.CALENDAR)).events
    assert all(e.start.strftime("%H:%M") == ("08:15" if e.kind == "ADP" else "08:30") for e in rows if e.tier == events.PRE_OPEN)
    assert all(e.start.strftime("%H:%M") == "10:00" for e in rows if e.tier == events.DATA_10AM)
    assert all(e.start.strftime("%H:%M") == "14:00" for e in rows if e.tier == events.DATA_2PM)
    oct_1 = [(e.start.strftime("%H:%M"), e.kind) for e in events.on_day(date(2026, 10, 1))]
    assert ("10:00", "ISM_MANUFACTURING") in oct_1 and ("10:00", "FED_GOVERNOR_SPEECH") in oct_1
    for kind in ("JOBS", "CPI", "PPI", "PCE", "GDP", "RETAIL_SALES", "ISM_MANUFACTURING", "ISM_SERVICES", "JOLTS",
                 "CONSUMER_CONFIDENCE", "UMICH_SENTIMENT", "FOMC_MINUTES", "QUARTER_END"):
        assert any(e.kind == kind and e.start.month == 12 for e in rows), kind
    assert [e.start.date() for e in rows if e.kind == "QUARTER_END"] == [date(2026, 9, 30), date(2026, 12, 31)]


def test_every_shipped_row_carries_a_known_tier_and_the_auction_tier_is_known_with_no_rows_yet():
    """The Treasury auction tier (events.AUCTION) is defined ahead of its rows, so a row added from the published
    schedule has a shape and a name; nothing reads it yet, and no row is invented."""
    events._load.cache_clear()
    doc = json.loads(events.CALENDAR.read_text(encoding="utf-8"))
    assert {str(e["tier"]) for e in doc["events"]} <= events.TIERS
    assert events.AUCTION in events.TIERS and events.AUCTION not in events.LEARN_TIERS
    assert not [e for e in doc["events"] if str(e["tier"]) == events.AUCTION]
    assert any("tier auction" in h for h in doc["how_to_use"])
    assert events.words("AUCTION_10Y") == "the 10-year Treasury note auction"


def test_the_releases_before_the_open_reach_back_to_august_each_from_its_source():
    """August is listed ahead of covers_from, from pre_open_covers_from: the premarket lane's report labels, which ask
    events.uncovered for the pre_open tier, read them from there; every other event label from covers_from."""
    events._load.cache_clear()
    doc = json.loads(events.CALENDAR.read_text(encoding="utf-8"))
    rows = [e for e in doc["events"] if e["tier"] == events.PRE_OPEN]
    assert min(e["date"] for e in rows) == "2026-08-04"
    assert all(e["source"].strip() and isinstance(e["verified"], bool) and e["in_session"] is False for e in rows)
    assert all(e["verified"] is False for e in rows if e["kind"] == "JOBLESS_CLAIMS")    # a rule, not a published date
    thursdays = {e["date"] for e in rows if e["kind"] == "JOBLESS_CLAIMS"}
    assert {"2026-08-06", "2026-08-13", "2026-08-20", "2026-08-27"} <= thursdays
    for month in range(8, 13):
        kinds = {e["kind"] for e in rows if int(e["date"][5:7]) == month}
        assert {"JOBS", "CPI", "PPI", "RETAIL_SALES", "EMPIRE_STATE", "PHILLY_FED", "DURABLE_GOODS", "IMPORT_PRICES",
                "HOUSING_STARTS"} <= kinds, month
    assert sorted(e["date"] for e in rows if e["kind"] == "ADP") == ["2026-08-05", "2026-09-02", "2026-09-30", "2026-11-04", "2026-12-02"]
    assert [(e.start.strftime("%H:%M"), e.kind) for e in events.on_day(date(2026, 8, 5))] == [("08:15", "ADP")]
    assert ("08:30", "JOBS") in [(e.start.strftime("%H:%M"), e.kind) for e in events.on_day(date(2026, 8, 7))]


def test_the_shipped_calendars_press_conferences_testimony_and_jackson_hole_have_an_end_and_are_held_out_while_under_way():
    events._load.cache_clear()
    rows = events._load(str(events.CALENDAR)).events
    assert all(e.end is not None for e in rows if e.kind in ("FOMC_PRESSER", "FED_CHAIR_TESTIMONY", "FED_CHAIR_JACKSON_HOLE"))
    g = events.tag(t("2026-10-28T15:02"))
    assert g["within_30"] is True and g["sentence"] == "a scheduled event is ahead: the Fed chair's press conference, under way until 15:30 ET"
    assert events.tag(t("2026-10-28T15:30")) is None


def test_a_read_taken_while_a_fed_speaker_speaks_is_kept_out_of_what_the_loop_learns(tmp_path):
    """Tuesday 09-29: Governor Barr from 12:40, questions included. The speaker rows carried no end, so the 13:02 read,
    twenty minutes into the conversation, was learned from as an ordinary half hour. Every shipped speaker row now has
    an end, and a row without one runs SPEAKER_MIN."""
    events._load.cache_clear()
    shipped = [e for e in events._load(str(events.CALENDAR)).events if e.tier == events.FED_SPEAKER]
    assert shipped and all(e.end is not None for e in shipped)
    assert events.learn_exclude(t("2026-09-29T13:02"), (30, 60)) == {"30": True, "60": True}
    p = _cal(tmp_path, [{"date": "2026-10-01", "time_et": "10:00", "kind": "FED_GOVERNOR_SPEECH", "tier": "fed_speaker"}], "2026-10-31")
    assert events.on_day(date(2026, 10, 1), p)[0].end == t("2026-10-01T11:00")
    assert events.learn_exclude(t("2026-10-01T10:32"), (30,), p) == {"30": True}
    assert events.learn_exclude(t("2026-10-01T11:02"), (30,), p) == {"30": False}


def test_other_tiers_are_read_by_the_labels_with_their_flags_and_never_tagged(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-10-01", "time_et": "10:00", "kind": "ISM_MANUFACTURING", "tier": "data_10am", "verified": False},
                        {"date": "2026-10-01", "time_et": "10:00", "kind": "FED_GOVERNOR_SPEECH", "tier": "fed_speaker", "q_and_a": True},
                        {"date": "2026-10-02", "time_et": "08:30", "kind": "JOBS", "tier": "pre_open"}], covers_through="2026-10-31")
    day = events.on_day(date(2026, 10, 1), p)
    assert [(e.kind, e.tier, e.verified, e.q_and_a) for e in day] == [("ISM_MANUFACTURING", "data_10am", False, False),
                                                                     ("FED_GOVERNOR_SPEECH", "fed_speaker", True, True)]
    assert day[1].words == "a Fed governor"
    assert events.tag(t("2026-10-01T09:40"), p) is None
    assert events.starts_on(date(2026, 10, 2), "JOBS", p) is None                   # starts_on reads tier 1 only
    assert events.uncovered(date(2026, 10, 31), p) is None and events.uncovered(date(2026, 1, 2), p) is None


def test_a_calendar_that_names_no_last_day_covers_none(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-10-28", "time_et": "14:00", "kind": "FOMC", "tier": 1}])
    assert events.uncovered(date(2026, 10, 28), p) == "the event calendar (calendar/events.json) names no last kept day (covers_through)"


def test_the_quiet_period_runs_from_the_second_saturday_before_the_meeting_to_the_day_after(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-10-28", "time_et": "14:00", "kind": "FOMC", "tier": 1}])
    assert not events.in_quiet_period(date(2026, 10, 16), p)
    assert events.in_quiet_period(date(2026, 10, 17), p)                             # the meeting starts Tuesday 10-27
    assert events.in_quiet_period(date(2026, 10, 29), p)
    assert not events.in_quiet_period(date(2026, 10, 30), p)


def test_a_broken_calendar_tags_nothing_and_never_raises(tmp_path):
    for doc in (["oops"], {"events": {"a": 1}}, {"events": ["oops", {"date": "bad", "time_et": "x", "tier": 1}]}):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(doc))
        events._load.cache_clear()
        assert events.tag(t("2026-10-28T13:32"), p) is None


def test_a_day_before_the_calendars_first_kept_day_is_unknown_not_quiet():
    """The 10:00, 14:00 and speaker tiers are listed from September only, so an August morning with no rows is not an
    ordinary day for the event labels, though its releases before the open are listed."""
    events._load.cache_clear()
    assert events.uncovered(date(2026, 8, 6)) == "the event calendar (calendar/events.json) is kept only from 2026-09-01"
    assert events.uncovered(date(2026, 9, 1)) is None
    assert events.uncovered(date(2026, 8, 6), tier=events.PRE_OPEN) is None
    assert events.uncovered(date(2026, 7, 31), tier=events.PRE_OPEN) == \
        "the event calendar (calendar/events.json) is kept only from 2026-08-01"
