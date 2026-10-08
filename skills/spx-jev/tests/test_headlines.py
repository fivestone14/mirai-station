"""The headline feed: the RSS items parsed, each new one stamped with the poll's clock, seen across polls, the feeds that
fail logged apart, the lock, the capture window, and the reader's cut before a read. No network: the fetcher is a stand-in."""
from __future__ import annotations

import json
import os
import time
from datetime import timedelta

import pytest
from conftest import DAY, at

from spx_jev import headlines
from spx_jev.cuts import HEADLINE_CUT_MIN
from spx_jev.headlines import (FEEDS, MAX_TITLES, PollRunning, folder, headlines_before, in_capture_window, keys_of, normalized_title,
                               parse_items, poll, prune_seen, take_lock)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>Nvidia slips as export curbs widen</title><link>https://x/1</link><guid>g1</guid><pubDate>Fri, 18 Sep 2026 13:50:00 GMT</pubDate></item>
<item><title>Yields climb after jobs data</title><link>https://x/2</link><guid>g2</guid>
  <source url="https://reuters.com">Reuters</source></item>
<item><link>https://x/3</link></item>
</channel></rss>"""


def _fetcher(bodies: dict[str, bytes | Exception]):
    def fetch(url):
        for feed, feed_url in FEEDS:
            if feed_url == url:
                body = bodies.get(feed, RSS.encode())
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(url)
    return fetch


def test_items_are_parsed_with_the_source_tag_or_the_feed_and_a_title_is_required():
    items = parse_items(RSS.encode(), "cnbc_top")
    assert [i["title"] for i in items] == ["Nvidia slips as export curbs widen", "Yields climb after jobs data"]
    assert items[0] == {"title": "Nvidia slips as export curbs widen", "url": "https://x/1", "guid": "g1",
                        "pub_claimed": "Fri, 18 Sep 2026 13:50:00 GMT", "source": "cnbc_top"}
    assert items[1]["source"] == "Reuters" and items[1]["pub_claimed"] is None


def test_a_poll_writes_each_new_item_once_stamped_with_its_own_clock_and_never_the_feeds_date(tmp_path):
    now = at(10, 3, ss=7)
    counts = poll(tmp_path, now, fetcher=_fetcher({}))
    assert counts == {feed: n for feed, n in zip((f for f, _ in FEEDS), (2, 0, 0, 0))}      # the same items on every feed: written once
    lines = [json.loads(l) for l in (folder(tmp_path) / f"{DAY}.jsonl").read_text().splitlines()]
    assert [l["title"] for l in lines] == ["Nvidia slips as export curbs widen", "Yields climb after jobs data"]
    assert all(l["captured_at"] == now.isoformat(timespec="seconds") for l in lines)
    assert lines[0]["pub_claimed"] == "Fri, 18 Sep 2026 13:50:00 GMT" and lines[0]["feed"] == "cnbc_top" and lines[1]["source"] == "Reuters"
    assert set(lines[0]) == {"captured_at", "pub_claimed", "feed", "source", "title", "url", "guid"}
    # the next poll sees nothing new: the url and the lower-cased title are both remembered
    again = RSS.replace("https://x/1", "https://x/1?utm=a").replace("Nvidia slips", "NVIDIA SLIPS")
    assert poll(tmp_path, now + timedelta(minutes=3), fetcher=_fetcher({f: again.encode() for f, _ in FEEDS})) == dict.fromkeys((f for f, _ in FEEDS), 0)
    assert len((folder(tmp_path) / f"{DAY}.jsonl").read_text().splitlines()) == 2
    seen = json.loads((folder(tmp_path) / "seen.json").read_text())
    assert set(keys_of({"title": "Nvidia slips as export curbs widen", "url": "https://x/1"})) <= set(seen)


def test_seen_is_pruned_to_48_hours_so_an_old_item_may_come_back():
    now = at(10, 0)
    seen = {"old": (now - timedelta(hours=49)).isoformat(), "young": (now - timedelta(hours=47)).isoformat(), "bad": 3}
    assert prune_seen(seen, now) == {"young": seen["young"]}


def test_a_failing_feed_is_logged_apart_and_costs_only_its_own_items(tmp_path, capsys):
    now = at(10, 3)
    counts = poll(tmp_path, now, fetcher=_fetcher({"cnbc_top": TimeoutError("timed out"), "fed_press": ValueError("not xml")}))
    assert counts["cnbc_top"] == "TimeoutError: timed out" and counts["fed_press"] == "ValueError: not xml" and counts["google_news"] == 2
    errors = [json.loads(l) for l in (folder(tmp_path) / "errors.jsonl").read_text().splitlines()]
    assert [(e["feed"], e["error"]) for e in errors] == [("cnbc_top", "TimeoutError: timed out"), ("fed_press", "ValueError: not xml")]
    assert all(e["at"] == now.isoformat(timespec="seconds") for e in errors)
    assert "cnbc_top failed" in capsys.readouterr().err


def test_the_lock_refuses_a_second_poll_and_a_stale_one_is_taken_over(tmp_path, monkeypatch):
    lock = take_lock(tmp_path, at(10, 0))
    with pytest.raises(PollRunning):
        take_lock(tmp_path, at(10, 1))
    stale = time.time() - headlines.LOCK_STALE_S - 5
    os.utime(lock, (stale, stale))
    assert take_lock(tmp_path, at(10, 2)) == lock and lock.exists()


@pytest.mark.parametrize("hh, mm, day, inside", [(6, 59, DAY, False), (7, 0, DAY, True), (12, 0, DAY, True), (16, 30, DAY, True),
                                                 (16, 31, DAY, False), (12, 0, "2026-09-19", False)])
def test_the_poll_gates_itself_to_the_capture_window_on_weekdays(hh, mm, day, inside):
    assert in_capture_window(at(hh, mm, day)) is inside


def test_main_outside_the_window_polls_nothing_unless_told_now(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(headlines, "fetch", _fetcher({}))

    class Clock:
        @staticmethod
        def now(tz=None):
            return at(6, 30)
    monkeypatch.setattr(headlines, "datetime", Clock)
    assert headlines.main(["--state-dir", str(tmp_path)]) == 0 and not (folder(tmp_path) / f"{DAY}.jsonl").exists()
    assert "outside the capture window" in capsys.readouterr().out
    assert headlines.main(["--state-dir", str(tmp_path), "--now"]) == 0
    assert len((folder(tmp_path) / f"{DAY}.jsonl").read_text().splitlines()) == 2 and not (folder(tmp_path) / ".lock").exists()
    assert "new items cnbc_top 2, google_news 0" in capsys.readouterr().out


def _write(tmp_path, stamps: list[tuple], day: str = DAY):
    lines = [{"captured_at": t.isoformat(timespec="seconds"), "pub_claimed": None, "feed": "cnbc_top", "source": "CNBC", "title": title,
              "url": None, "guid": None} for t, title in stamps]
    folder(tmp_path).mkdir(parents=True, exist_ok=True)
    with open(folder(tmp_path) / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(l) + "\n" for l in lines))


def test_the_reader_keeps_the_window_ending_two_minutes_before_the_read_newest_first(tmp_path):
    read = at(10, 32, ss=20)
    cut = read - timedelta(minutes=HEADLINE_CUT_MIN)
    _write(tmp_path, [(cut - timedelta(minutes=60), "too old: at the window's start"), (cut - timedelta(minutes=59), "oldest kept"),
                      (cut - timedelta(minutes=1), "one minute before the cut"), (cut, "at the cut"),
                      (cut + timedelta(seconds=1), "a second after the cut"), (read, "at the read"), (read + timedelta(minutes=1), "after the read")])
    got = headlines_before(tmp_path, read.isoformat(), 60)
    assert [h["title"] for h in got] == ["at the cut", "one minute before the cut", "oldest kept"]
    assert HEADLINE_CUT_MIN == 2 and headlines_before(tmp_path, read, 60) == got


def test_the_reader_spans_midnight_and_skips_malformed_lines(tmp_path):
    read = at(0, 30, "2026-09-19")
    _write(tmp_path, [(at(23, 50), "last night")], day=DAY)
    _write(tmp_path, [(at(0, 10, "2026-09-19"), "this morning")], day="2026-09-19")
    with open(folder(tmp_path) / f"{DAY}.jsonl", "a") as f:
        f.write('{"captured_at": "not a time", "title": "bad"}\n{"title": "no stamp"}\n')
    assert [h["title"] for h in headlines_before(tmp_path, read, 60)] == ["this morning", "last night"]


def test_the_reader_keeps_each_story_once_from_its_first_capture(tmp_path):
    """Google News carries one wire story under many outlets, each title ending " - <outlet>": the first capture stands, and
    a copy captured later never makes the story fresh again, even when the first is older than the window."""
    read = at(10, 32, ss=20)
    lines = [{"captured_at": at(9, 10).isoformat(timespec="seconds"), "feed": "google_news", "source": "WHTC",
              "title": "Wall St futures slip as yields rebound - WHTC"},
             {"captured_at": at(10, 12).isoformat(timespec="seconds"), "feed": "google_news", "source": "Reuters",
              "title": "Wall St futures slip as yields rebound - Reuters"},
             {"captured_at": at(10, 14).isoformat(timespec="seconds"), "feed": "cnbc_top", "source": "cnbc_top", "title": "Fed's Waller: room to cut"},
             {"captured_at": at(10, 20).isoformat(timespec="seconds"), "feed": "google_news", "source": "Axios",
              "title": "FED'S WALLER -- room to cut! - Axios"}]
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / f"{DAY}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    got = headlines_before(tmp_path, read, 60)
    assert [(h["title"], h["captured_at"][11:16]) for h in got] == [("Fed's Waller: room to cut", "10:14")]
    assert normalized_title(lines[0]) == normalized_title(lines[1]) == "wall st futures slip as yields rebound"


def test_the_label_cap_is_twenty_five_titles():
    assert MAX_TITLES == 25
