"""The night's stretches: fixed clock edges on each exchange's own clock, the release minute, the read's cut."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from spx_jev import story

ET = ZoneInfo("America/New_York")


def _clocks(day: date, release: time = story.NO_REPORT_MINUTE) -> list[tuple[str, str, str]]:
    return [(name, f"{start:%m-%d %H:%M}", f"{end:%H:%M}") for name, start, end in story.edges(day, release)]


def test_the_stretches_cover_the_night_from_the_prior_close_to_the_open_without_a_gap():
    assert _clocks(date(2026, 9, 29)) == [
        ("after_close", "09-28 16:00", "18:00"), ("asia", "09-28 18:00", "02:30"), ("europe_open", "09-29 02:30", "03:30"),
        ("europe_morning", "09-29 03:30", "08:00"), ("pre_report", "09-29 08:00", "08:30"), ("report_window", "09-29 08:30", "08:45"),
        ("last_stretch", "09-29 08:45", "09:30")]
    edges = story.edges(date(2026, 9, 28), time(8, 15))
    assert edges[1][1:] == (datetime(2026, 9, 25, 18, 0, tzinfo=ET), datetime(2026, 9, 28, 2, 30, tzinfo=ET))   # the weekend is Asia's
    assert all(a[2] == b[1] for a, b in zip(edges, edges[1:])) and edges[4][2].time() == time(8, 15)


def test_each_edge_follows_its_own_exchange_through_the_clock_changes():
    assert _clocks(date(2026, 10, 27))[2] == ("europe_open", "10-27 02:30", "04:30")    # Frankfurt on winter time, New York not yet
    assert _clocks(date(2026, 11, 2))[1:3] == [("asia", "10-30 18:00", "01:30"), ("europe_open", "11-02 01:30", "03:30")]


def test_a_read_sees_the_stretches_begun_the_last_one_cut_at_the_read():
    got = story.stretches(date(2026, 9, 29), datetime(2026, 9, 29, 3, 35, tzinfo=ET), time(8, 30))
    assert [(s.name, s.finished) for s in got] == [("after_close", True), ("asia", True), ("europe_open", True), ("europe_morning", False)]
    assert got[-1].end == datetime(2026, 9, 29, 3, 35, tzinfo=ET)


def test_the_same_stretch_on_a_prior_night_is_cut_at_the_reads_clock():
    window = story.stretch_window("last_stretch", until=time(9, 28), release=time(8, 30))
    assert window(date(2026, 9, 25)) == (datetime(2026, 9, 25, 8, 45, tzinfo=ET), datetime(2026, 9, 25, 9, 28, tzinfo=ET))
    assert story.stretch_window("asia")(date(2026, 9, 25))[1] == datetime(2026, 9, 25, 2, 30, tzinfo=ET)


def test_the_report_window_starts_at_the_first_release_before_the_open(tmp_path):
    cal = tmp_path / "events.json"
    cal.write_text('{"covers_from": "2026-09-01", "covers_through": "2026-12-31", "events": ['
                   '{"date": "2026-09-30", "time_et": "08:15", "kind": "ADP", "tier": "pre_open"},'
                   '{"date": "2026-09-30", "time_et": "08:30", "kind": "GDP", "tier": "pre_open"},'
                   '{"date": "2026-09-30", "time_et": "10:00", "kind": "JOLTS", "tier": "data_10am"}]}')
    assert story.release_minute(date(2026, 9, 30), cal) == time(8, 15)
    assert story.release_minute(date(2026, 10, 1), cal) == story.NO_REPORT_MINUTE
