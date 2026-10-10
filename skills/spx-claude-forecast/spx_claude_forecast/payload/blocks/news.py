"""The news block: how much news reached the station in the hour before the cut, and how much of it the
station's own classifier calls an index mover, with every title withheld.

A headline counts from the clock the station first captured it (``captured_at``), never from what the feed
claimed, which is the station's own rule (spx_jev.headlines). The block reads the day's headline file and
the day before's, the way the station's reader does, and uses that reader (``headlines_before``) for the
stories it keeps: a copy of a story captured earlier, under another outlet or with Google News' " - Outlet"
tail, is dropped by the station's normalisation and counted here as ``stale_dropped``. Which items are index
movers is the station's ``judgment.index_news`` (a preferred feed naming the Fed, a major release, trade,
Treasury yields, or a heavyweight's results); the ``kinds`` are those five categories, which the station
keeps in one flat pattern and this module names one by one. Before the feed existed the whole block is
absent, so a read rebuilt from before 2026-10-07 never carries a zero that looks like a quiet hour.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from ... import jsonl_store, station_stores
from ...control import ET
from .. import units
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs

TITLES = "withheld"            # titles never reach Claude in this version: a title is a fact it could recall or be led by
WINDOW_MIN = 60                # the hour before the cut; the station's HEADLINE_WINDOW_MIN, and the field names carry it
# The station cuts JEV's window HEADLINE_CUT_MIN (2) minutes before a read, for a question answered live. The payload is
# built from saved files after the fact, so it may know everything the station had captured by the cut itself.
CUT_MIN = 0
# The station's INDEX_NEWS is one flat pattern; its comment names these categories, and the words here are that
# pattern's alternatives, grouped. A test holds every alternative of the station's pattern to exactly one kind.
KIND_WORDS: dict[str, tuple[str, ...]] = {
    "fed": ("Fed", "FOMC", "Federal Open Market Committee", "Powell"),
    "release": ("CPI", "PPI", "jobs report", "payrolls", "GDP", "PCE", "retail sales"),
    "trade": ("tariff", "tariffs", "trade war", "trade deal", "trade talks"),
    "yields": ("Treasury yield", "Treasury yields", "10-year yield", "bond yield", "bond yields"),
}
MEGACAP_RESULTS_KIND = "megacap_results"   # the station's MEGACAP_RESULTS pattern: a heavyweight's earnings or guidance
OTHER_KIND = "other"                       # an index mover none of the kinds above name: the station's pattern grew
KIND_PATTERNS = {kind: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
                 for kind, words in KIND_WORDS.items()}
DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")


def build_news_block(inputs: FrozenInputs) -> BlockResult:
    """Counts of the hour's headlines, the index movers among them and their kinds; titles withheld."""
    if not feed_known_on(inputs.state_dir, inputs.day):
        return whole_block_absent("news", "no_headline_feed")
    result = BlockResult()
    captured = captured_lines_before(inputs.state_dir, inputs.cut, WINDOW_MIN)
    stories = stories_before(inputs.state_dir, inputs.cut, WINDOW_MIN)
    movers = [story for story in stories if is_index_mover(story)]
    newest = max((captured_at(story) for story in movers), default=None)
    if newest is None:
        result.leave_out("news.newest_index_mover_min_ago", "no_index_mover_in_60m")
    result.data = units.clean({
        "titles": TITLES,
        "captured_60m": len(captured),
        "index_mover_items_60m": len(movers),
        "kinds": sorted({kind for story in movers for kind in kinds_of(story)}),
        "newest_index_mover_min_ago": units.minutes_ago(newest, inputs.cut),
        "stale_dropped": len(captured) - len(stories),
    })
    return result


def feed_days(state_dir: Path) -> list[str]:
    """The days the headline feed has a file for, oldest first."""
    folder = _headlines().folder(state_dir)
    return sorted(p.name[:10] for p in folder.iterdir() if DAY_FILE.match(p.name)) if folder.is_dir() else []


def feed_known_on(state_dir: Path, day: str) -> bool:
    """Whether the feed existed by ``day``: it has a file for that day or an earlier one."""
    return any(d <= day for d in feed_days(state_dir))


def captured_lines_before(state_dir: Path, cut: datetime, minutes: int) -> list[dict]:
    """Every headline line captured in the ``minutes`` ending at ``cut``, repeats included, newest first: the lines
    on disk, read from the cut's day file and the day before's as the station's reader does."""
    start = cut - timedelta(minutes=minutes)
    lines = [(at, line) for line in _lines_of_days(state_dir, cut) if (at := captured_at(line)) is not None and start < at <= cut]
    return [line for _, line in sorted(lines, key=lambda pair: pair[0], reverse=True)]


def stories_before(state_dir: Path, cut: datetime, minutes: int) -> list[dict]:
    """The stories the station's reader keeps for the window: each story once, from its first capture."""
    return _headlines().headlines_before(state_dir, cut, minutes, cut_min=CUT_MIN)


def newest_capture_before(state_dir: Path, cut: datetime) -> datetime | None:
    """When the feed last captured anything at or before ``cut``, on the cut's day or the day before."""
    return max((at for line in _lines_of_days(state_dir, cut) if (at := captured_at(line)) is not None and at <= cut), default=None)


def captured_at(line: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(line["captured_at"]).astimezone(ET)
    except (KeyError, TypeError, ValueError):
        return None


def is_index_mover(line: dict) -> bool:
    return bool(_judgment().index_news(line))


def kinds_of(line: dict) -> list[str]:
    """The kinds an index mover names, by the station's categories; ``other`` when its pattern names one this
    module does not know yet."""
    title = line.get("title") or ""
    found = [kind for kind, pattern in KIND_PATTERNS.items() if pattern.search(title)]
    if _judgment().MEGACAP_RESULTS.search(title):
        found.append(MEGACAP_RESULTS_KIND)
    return found or [OTHER_KIND]


def _lines_of_days(state_dir: Path, cut: datetime) -> list[dict]:
    folder = _headlines().folder(state_dir)
    day = cut.astimezone(ET).date()
    return [line for d in (day - timedelta(days=1), day) for line in jsonl_store.iter_json_lines(folder / f"{d.isoformat()}.jsonl")]


def _headlines():
    return station_stores.import_spx_jev("headlines")


def _judgment():
    return station_stores.import_spx_jev("judgment")
