"""Scheduled events near a read (events.py): tier-1 only, within the hour, the 30-minute hold-out inclusive."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from spx_jev import events

ET = ZoneInfo("America/New_York")


def _cal(tmp_path, rows):
    p = tmp_path / "events.json"
    p.write_text(json.dumps({"events": rows}))
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
    rows = events._load(str(events.CALENDAR))
    assert rows and all(k in events.WORDS for _, _, k in rows)
    assert not any("SNDK" in k for _, _, k in rows)


def test_a_broken_calendar_tags_nothing_and_never_raises(tmp_path):
    for doc in (["oops"], {"events": {"a": 1}}, {"events": ["oops", {"date": "bad", "time_et": "x", "tier": 1}]}):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(doc))
        events._load.cache_clear()
        assert events.tag(t("2026-10-28T13:32"), p) is None
