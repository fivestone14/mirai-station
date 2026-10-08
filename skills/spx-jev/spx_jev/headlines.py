"""The headline feed: the public RSS feeds polled every few minutes, each new item stamped with the clock it was seen on.

    python3 -m spx_jev.headlines            # one poll, inside the capture window (the launchd job's run)
    python3 -m spx_jev.headlines --now      # one poll whatever the clock says (the first fill)

The judgment questions (judgment.py) ask what the news should do to stocks, so the one fact that matters about a
headline is when this station could have read it. ``captured_at`` is the poll's own wall clock, the truth a question is
cut on; the feed's ``pubDate`` is kept raw as ``pub_claimed`` and never trusted (feeds back-date, re-date and omit it).
One JSON line per NEW item under ``state/spx_jev/headlines/``:

    {day}.jsonl     {"captured_at": ISO ET, "pub_claimed": the raw pubDate string or null, "feed": the feed's name,
                     "source": the outlet (Google News' <source> tag, else the feed), "title", "url", "guid"}
    seen.json       what has been captured, sha1(url) and sha1(lower(title)), each with when, pruned to SEEN_HOURS; so an
                    item every feed carries, or one a feed re-dates, is written once
    errors.jsonl    one line per feed that failed on a poll, with the error; the other feeds are still read
    .lock           held while a poll runs, so two polls never interleave

Stdlib only (urllib, xml.etree). The feeds are read with a 10-second timeout each and a plain user agent. The poll gates
itself to CAPTURE_FROM-CAPTURE_TO ET on weekdays; ``--now`` runs regardless.

``headlines_before(state_dir, row_ts, minutes)`` is the questions' reader: the titles captured in the ``minutes`` ending
HEADLINE_CUT_MIN minutes before the read, newest first, never one captured after it, each story once: a copy of a title
captured earlier (the same words under another outlet, Google News' " - Outlet" tail, case or punctuation; normalized_title)
is dropped, so a story counts from when the station first captured it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET_
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .cuts import HEADLINE_CUT_MIN
from .feed_log import log
from .state_builder import DEFAULT_STATE_DIR, load_jsonl, parse_ts

ET = ZoneInfo("America/New_York")
JOB = "spx-jev-headlines"
HEADLINES_SUBDIR = Path("spx_jev") / "headlines"
FEEDS = (
    ("cnbc_top", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("google_news", "https://news.google.com/rss/search?q=%22S%26P+500%22+OR+Nvidia+OR+Fed+OR+tariff+OR+%22Treasury+yields%22"
                    "+when:1h&hl=en-US&gl=US&ceid=US:en"),
    ("marketwatch_top", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("fed_press", "https://www.federalreserve.gov/feeds/press_all.xml"),
)
USER_AGENT = "mirai-station/1.0 (private market-research station; RSS polling every 3 minutes)"   # honest, never a browser string
TIMEOUT_S = 10.0
SEEN_HOURS = 48                  # an item seen longer ago than this may be captured again: the feeds hold nothing that old
CAPTURE_FROM, CAPTURE_TO = "07:00", "16:30"     # ET, weekdays
LOCK_STALE_S = 600               # a lock older than this was left by a poll that died: taken over
MAX_TITLES = 25                  # the most titles one label carries


def folder(state_dir: Path | str) -> Path:
    return Path(state_dir) / HEADLINES_SUBDIR


def in_capture_window(now: datetime) -> bool:
    """Weekdays, CAPTURE_FROM to CAPTURE_TO ET."""
    t = now.astimezone(ET)
    return t.weekday() < 5 and CAPTURE_FROM <= t.strftime("%H:%M") <= CAPTURE_TO


def fetch(url: str, timeout: float = TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _text(item, tag: str) -> str | None:
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def parse_items(body: bytes, feed: str) -> list[dict]:
    """Every <item> of an RSS body as {title, url, guid, pub_claimed, source}: the outlet from the item's <source> tag
    (Google News), else the feed's own name. An item with no title is skipped."""
    root = ET_.fromstring(body)
    out = []
    for item in root.iter("item"):
        title = _text(item, "title")
        if not title:
            continue
        out.append({"title": title, "url": _text(item, "link"), "guid": _text(item, "guid"), "pub_claimed": _text(item, "pubDate"),
                    "source": _text(item, "source") or feed})
    return out


def keys_of(item: dict) -> list[str]:
    """What marks an item as seen: its url and its title, lower-cased, each hashed."""
    keys = [hashlib.sha1(item["title"].lower().encode("utf-8")).hexdigest()]
    if item.get("url"):
        keys.insert(0, hashlib.sha1(item["url"].encode("utf-8")).hexdigest())
    return keys


def load_seen(state_dir: Path | str) -> dict[str, str]:
    path = folder(state_dir) / "seen.json"
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return seen if isinstance(seen, dict) else {}


def prune_seen(seen: dict[str, str], now: datetime, hours: int = SEEN_HOURS) -> dict[str, str]:
    floor = (now - timedelta(hours=hours)).isoformat()
    return {k: when for k, when in seen.items() if isinstance(when, str) and when >= floor}


def save_seen(state_dir: Path | str, seen: dict[str, str]) -> None:
    path = folder(state_dir) / "seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(seen, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _append(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))


def record_error(state_dir: Path | str, now: datetime, feed: str, error: str) -> None:
    _append(folder(state_dir) / "errors.jsonl", [{"at": now.isoformat(timespec="seconds"), "feed": feed, "error": error}])


class PollRunning(RuntimeError):
    """Another poll holds the lock."""


def take_lock(state_dir: Path | str, now: datetime) -> Path:
    """The lock file, created exclusively; one left by a poll that died (older than LOCK_STALE_S) is taken over."""
    path = folder(state_dir) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - path.stat().st_mtime > LOCK_STALE_S:
            path.unlink()
    except FileNotFoundError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PollRunning(f"another poll holds {path}") from None
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {now.isoformat(timespec='seconds')}\n")
    return path


def poll(state_dir: Path | str, now: datetime, fetcher=None) -> dict[str, int | str]:
    """One poll of every feed: each new item written to the day's file, stamped ``now``; a feed that fails is logged
    under errors.jsonl and costs only its own items. Returns the new items per feed, a failed feed as its error."""
    fetcher = fetcher or fetch
    seen = prune_seen(load_seen(state_dir), now)
    counts: dict[str, int | str] = {}
    new: list[dict] = []
    for feed, url in FEEDS:
        try:
            items = parse_items(fetcher(url), feed)
        except Exception as e:  # one feed down must not cost the others
            counts[feed] = f"{type(e).__name__}: {str(e)[:200]}"
            record_error(state_dir, now, feed, counts[feed])
            log(JOB, f"{feed} failed: {counts[feed]}", err=True)
            continue
        n = 0
        for item in items:
            keys = keys_of(item)
            if any(k in seen for k in keys):
                continue
            for k in keys:
                seen[k] = now.isoformat(timespec="seconds")
            new.append({"captured_at": now.isoformat(timespec="seconds"), "pub_claimed": item["pub_claimed"], "feed": feed,
                        "source": item["source"], "title": item["title"], "url": item["url"], "guid": item["guid"]})
            n += 1
        counts[feed] = n
    if new:
        _append(folder(state_dir) / f"{now.astimezone(ET).date().isoformat()}.jsonl", new)
    save_seen(state_dir, seen)
    return counts


def normalized_title(line: dict) -> str:
    """A title as the copies of one story share it: without a trailing " - <outlet>" (Google News' tail), lower-cased,
    every run of anything but letters and digits one space."""
    title = line["title"]
    source = line.get("source")
    if isinstance(source, str) and source and title.endswith(f" - {source}"):
        title = title[: -len(source) - 3]
    return re.sub(r"[^0-9a-z]+", " ", title.lower()).strip()


def headlines_before(state_dir: Path | str, row_ts: str | datetime, minutes: int, cut_min: int = HEADLINE_CUT_MIN) -> list[dict]:
    """The headlines a read at ``row_ts`` may know: first captured within the ``minutes`` that end ``cut_min`` minutes
    before the read, newest first; never one captured after that cut, whatever its feed claimed, and a copy of a story
    captured earlier (normalized_title, over the day before and the day to the cut) never. Each is the stored line."""
    end = (parse_ts(row_ts) if isinstance(row_ts, str) else row_ts) - timedelta(minutes=cut_min)
    start = end - timedelta(minutes=minutes)
    day = end.astimezone(ET).date()
    lines = []
    for d in (day - timedelta(days=1), day):
        for line in load_jsonl(folder(state_dir) / f"{d.isoformat()}.jsonl"):
            try:
                t = parse_ts(line["captured_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if t <= end and isinstance(line.get("title"), str):
                lines.append((t, line))
    first: dict[str, tuple[datetime, dict]] = {}
    for t, line in sorted(lines, key=lambda x: x[0]):
        first.setdefault(normalized_title(line), (t, line))
    return sorted((line for t, line in first.values() if start < t), key=lambda h: h["captured_at"], reverse=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Poll the headline feeds once into state/spx_jev/headlines/.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--now", action="store_true", help=f"poll whatever the clock says (else only {CAPTURE_FROM}-{CAPTURE_TO} ET on weekdays)")
    args = ap.parse_args(argv)
    now = datetime.now(ET)
    if not args.now and not in_capture_window(now):
        log(JOB, f"outside the capture window ({CAPTURE_FROM}-{CAPTURE_TO} ET weekdays), skipping")
        return 0
    state_dir = Path(args.state_dir)
    try:
        lock = take_lock(state_dir, now)
    except PollRunning as e:
        log(JOB, f"{e}; skipping this poll")
        return 0
    try:
        counts = poll(state_dir, now)
    finally:
        lock.unlink(missing_ok=True)
    log(JOB, "new items " + ", ".join(f"{feed} {n}" for feed, n in counts.items()) + f" -> {folder(state_dir)}")
    return 0 if any(isinstance(n, int) for n in counts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
