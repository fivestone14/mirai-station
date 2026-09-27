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
    assert events.covers(date(2026, 12, 31)) == date(2026, 12, 31) and events.covers(date(2027, 1, 4)) is None
    rows = events._load(str(events.CALENDAR)).events
    assert all(e.start.strftime("%H:%M") == "08:30" for e in rows if e.tier == events.PRE_OPEN)
    assert all(e.start.strftime("%H:%M") == "10:00" for e in rows if e.tier == events.DATA_10AM)
    assert all(e.start.strftime("%H:%M") == "14:00" for e in rows if e.tier == events.DATA_2PM)
    oct_1 = [(e.start.strftime("%H:%M"), e.kind) for e in events.on_day(date(2026, 10, 1))]
    assert ("10:00", "ISM_MANUFACTURING") in oct_1 and ("10:00", "FED_GOVERNOR_SPEECH") in oct_1
    for kind in ("JOBS", "CPI", "PPI", "PCE", "GDP", "RETAIL_SALES", "ISM_MANUFACTURING", "ISM_SERVICES", "JOLTS",
                 "CONSUMER_CONFIDENCE", "UMICH_SENTIMENT", "FOMC_MINUTES", "QUARTER_END"):
        assert any(e.kind == kind and e.start.month == 12 for e in rows), kind
    assert [e.start.date() for e in rows if e.kind == "QUARTER_END"] == [date(2026, 9, 30), date(2026, 12, 31)]


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
    assert events.covers(date(2026, 10, 31), p) == date(2026, 10, 31) and events.covers(date(2026, 11, 2), p) is None


def test_a_calendar_without_its_reach_covers_no_day(tmp_path):
    p = _cal(tmp_path, [{"date": "2026-10-28", "time_et": "14:00", "kind": "FOMC", "tier": 1}])
    assert events.covers(date(2026, 10, 28), p) is None


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
