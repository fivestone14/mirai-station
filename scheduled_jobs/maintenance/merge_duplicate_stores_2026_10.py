"""Cleanup phase 5, the data side: fold the five interim duplicate stores into one copy each, once, on a data root.

    python scheduled_jobs/maintenance/merge_duplicate_stores_2026_10.py --data-root ~/mirai_awakening/data --dry-run
    python scheduled_jobs/maintenance/merge_duplicate_stores_2026_10.py --data-root ~/mirai_awakening/data

Run it after the phase 5 code is in place and at a quiet time (no job writes any of these stores overnight; the wall
sensor and the bars feed write theirs in market hours). ``--dry-run`` lists every action and changes nothing. The real
run is idempotent (a second run finds nothing left to do) and never deletes: whatever a merge supersedes is MOVED to
``<data root>/archive/2026-10-12_merged_duplicates_retired/`` under its old path, with a MANIFEST.md, and the run ends
with a self-check that reads the archived copies back against the merged stores.

The five merges, each leaving the store station_paths.py now names as the only one:

1. SPX minute bars. The scanner grader's saved sessions (``market/spx_price_bars_by_minute_scanner_copy/{day}-SPX.json``,
   a JSON list of bars) are folded into the bars feed's day files (``market/spx_price_bars_by_minute/{day}.jsonl``, one
   bar a line): a day with no file gets one written from the list; a day with both gets any minute the file lacks
   added (the file rewritten in time order); a minute present in both with different values is a CONFLICT, reported and
   left as the file has it. Then the copy's folder goes to the archive.
2. The raw same-day options tape. The recorder's gzip copies (``market/spx_same_day_options_raw_day_folders_recorder_copy/
   {day}/{stream}.jsonl.gz`` with a ``_complete.json`` manifest) are compared stream by stream, decompressed, with the
   collector's day folders (``market/spx_same_day_options_raw_day_folders/{day}/``, plain or .gz): equal streams need
   nothing, a stream the collector lacks is copied in, a differing one is a CONFLICT (the collector's kept). Nothing
   reads ``_complete.json``, so the manifests stay with the archived copy. Then the copy's folder goes to the archive.
3. SPY's volume per minute. Every day of the wall sensor's one-file memory (``spx/scanner/spy_volume_baselines_state.json``,
   ``{"days": {day: {minute: shares}}}``) is compared with its day file (``market/spy_volume_by_minute/{day}.json``): equal
   needs nothing, a missing day file is written, a differing one is a CONFLICT (the day file kept). Then the state file
   goes to the archive, and the self-check proves the baseline the sensor now reads (the newest 60 day files) is the
   memory it had.
4. The open-interest book. The latest book (``market/spx_monthly_quarterly_open_interest_latest.json``) must have its
   copy in the history the dated-book job now writes itself (``market/spx_monthly_quarterly_open_interest_by_day/
   {stamp}.json``): written if missing, compared if there. Nothing is archived.
5. The event calendar. Every row of the collector's copy (``market/market_event_calendar_by_event.json``) must be in the
   station's one calendar (``settings/market_event_calendar_by_event.json``, versioned in the repo and already unioned by
   the phase 5 commit); a row that is not is a CONFLICT to add by hand (this command never edits the repo). Then the
   copy goes to the archive.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = str(Path(__file__).resolve().parents[2])      # station_paths.py and station_store_files.py sit at the repo root
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
import station_paths  # noqa: E402
import station_store_files  # noqa: E402

ET = ZoneInfo("America/New_York")
ARCHIVE_FOLDER = "archive/2026-10-12_merged_duplicates_retired"
# The superseded copies, by their paths under the data root (their station_paths constants are gone).
SCANNER_BARS_COPY = "market/spx_price_bars_by_minute_scanner_copy"
RECORDER_TAPE_COPY = "market/spx_same_day_options_raw_day_folders_recorder_copy"
SPY_VOLUME_STATE = "spx/scanner/spy_volume_baselines_state.json"
CALENDAR_DATA_COPY = "market/market_event_calendar_by_event.json"
BAR_KEYS = ("open", "high", "low", "close", "volume")


class Run:
    """One run's log: actions taken (or, dry, that would be), conflicts found, and the self-check's findings."""

    def __init__(self, root: Path, dry_run: bool) -> None:
        self.root = root
        self.dry_run = dry_run
        self.archive = root / ARCHIVE_FOLDER
        self.actions: list[str] = []
        self.conflicts: list[str] = []
        self.checks: list[tuple[bool, str]] = []

    def act(self, merge: int, what: str) -> bool:
        """Log an action; True when the caller should perform it (a real run)."""
        self.actions.append(f"[{merge}] {'would ' if self.dry_run else ''}{what}")
        return not self.dry_run

    def conflict(self, merge: int, what: str) -> None:
        self.conflicts.append(f"[{merge}] CONFLICT {what}")

    def check(self, ok: bool, what: str) -> None:
        self.checks.append((ok, what))

    def retire(self, merge: int, relative: str) -> None:
        """Move a superseded file or folder under the archive, keeping its path; a second run finds it gone."""
        source, target = self.root / relative, self.archive / relative
        if not source.exists():
            self.actions.append(f"[{merge}] {relative}: already retired" + (" (in the archive)" if target.exists() else " (not on disk)"))
            return
        if target.exists():
            self.conflict(merge, f"{relative}: both the live copy and an archived one exist; moved nothing")
            return
        if self.act(merge, f"move {relative} -> {ARCHIVE_FOLDER}/{relative}"):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

    def archived_or_live(self, relative: str) -> Path | None:
        """Where a superseded copy is now: the archive after the real run, its old place before it."""
        for place in (self.archive / relative, self.root / relative):
            if place.exists():
                return place
        return None


# --- 1. SPX minute bars ----------------------------------------------------------------------------------------------

def _bar_values(bar: dict) -> tuple:
    return tuple(float(bar.get(k) or 0.0) for k in BAR_KEYS)


def _bar_line(bar: dict) -> str:
    """One bar as the bars feed writes it (bars.append_day): the station's bar shape, floats throughout."""
    return json.dumps({"ts": bar["ts"], **{k: float(bar.get(k) or 0.0) for k in BAR_KEYS}})


def merge_spx_bars(run: Run) -> None:
    folder = run.root / SCANNER_BARS_COPY
    if not folder.is_dir():
        run.actions.append(f"[1] {SCANNER_BARS_COPY}/: not on disk; nothing to fold")
        run.retire(1, SCANNER_BARS_COPY)
        return
    files = sorted(folder.glob("????-??-??-SPX.json"))
    others = sorted(p.name for p in folder.iterdir() if p not in files)
    if others:
        run.actions.append(f"[1] {SCANNER_BARS_COPY}/ also holds {others}: archived with the folder, folded nowhere")
    written = added = equal = 0
    for f in files:
        day = f.name[:10]
        try:
            saved = [b for b in json.loads(f.read_text(encoding="utf-8")) if station_store_files.is_bar(b)]
        except (OSError, ValueError, TypeError):
            run.conflict(1, f"{f.name}: not a JSON list of bars; left in the archive, folded nowhere")
            continue
        have = {b["ts"][:16]: b for b in station_store_files.read_spx_price_bars(run.root, day)}
        missing, differ = [], []
        for b in saved:
            mine = have.get(b["ts"][:16])
            if mine is None:
                missing.append(b)
            elif _bar_values(mine) != _bar_values(b):
                differ.append((b["ts"], _bar_values(b), _bar_values(mine)))
        for ts, theirs, mine in differ[:3]:
            run.conflict(1, f"{day} {ts}: the scanner copy has {theirs}, the bars file {mine}; the file is kept")
        if len(differ) > 3:
            run.conflict(1, f"{day}: {len(differ) - 3} more minutes differ")
        if not missing:
            equal += 1
            continue
        target = station_paths.SPX_PRICE_BARS_BY_MINUTE.path(run.root, day=day)
        if not have:
            written += 1
            if run.act(1, f"write {target.relative_to(run.root)} from {f.name} ({len(missing)} bars)"):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("".join(_bar_line(b) + "\n" for b in sorted(missing, key=lambda b: b["ts"])), encoding="utf-8")
        else:
            added += 1
            if run.act(1, f"add {len(missing)} missing minutes from {f.name} to {target.relative_to(run.root)} (rewritten in time order)"):
                merged = sorted([*have.values(), *missing], key=lambda b: b["ts"])
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_text("".join(_bar_line(b) + "\n" for b in merged), encoding="utf-8")
                tmp.replace(target)
    run.actions.append(f"[1] {len(files)} saved sessions: {equal} already whole in the bars store, {written} day files written, {added} topped up")
    run.retire(1, SCANNER_BARS_COPY)


def check_spx_bars(run: Run) -> None:
    folder = run.archived_or_live(SCANNER_BARS_COPY)
    if folder is None:
        run.check(True, "[1] no scanner copy anywhere: nothing to compare")
        return
    bad = 0
    files = sorted(folder.glob("????-??-??-SPX.json"))
    for f in files:
        day = f.name[:10]
        try:
            saved = [b for b in json.loads(f.read_text(encoding="utf-8")) if station_store_files.is_bar(b)]
        except (OSError, ValueError, TypeError):
            continue
        have = {b["ts"][:16]: _bar_values(b) for b in station_store_files.read_spx_price_bars(run.root, day)}
        if any(b["ts"][:16] not in have for b in saved):
            bad += 1
    run.check(bad == 0, f"[1] every minute of the {len(files)} saved sessions is in the bars store ({bad} days short)")
    run.check(not (run.root / SCANNER_BARS_COPY).exists(), f"[1] {SCANNER_BARS_COPY}/ is gone from its old place")


# --- 2. the raw options tape -------------------------------------------------------------------------------------------

def _stream_digest(path: Path) -> str:
    digest = hashlib.sha256()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collector_stream(day_folder: Path, gz_name: str) -> Path | None:
    """The collector's copy of a stream: its .gz, else the plain file (today's, or a day not yet compressed)."""
    for candidate in (day_folder / gz_name, day_folder / gz_name[: -len(".gz")]):
        if candidate.is_file():
            return candidate
    return None


def merge_raw_tape(run: Run) -> None:
    source = run.root / RECORDER_TAPE_COPY
    collector = station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.folder(run.root)
    if not source.is_dir():
        run.actions.append(f"[2] {RECORDER_TAPE_COPY}/: not on disk; nothing to fold")
        run.retire(2, RECORDER_TAPE_COPY)
        return
    equal = copied = 0
    for day_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        for g in sorted(day_dir.glob("*.gz")):
            mine = _collector_stream(collector / day_dir.name, g.name)
            if mine is None:
                copied += 1
                if run.act(2, f"copy {day_dir.name}/{g.name} into the collector's day folder (the collector has no such stream)"):
                    (collector / day_dir.name).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(g, collector / day_dir.name / g.name)
            elif _stream_digest(mine) == _stream_digest(g):
                equal += 1
            else:
                run.conflict(2, f"{day_dir.name}/{g.name}: the recorder's copy and the collector's {mine.name} differ; the collector's is kept")
    run.actions.append(f"[2] {equal} streams equal in both copies, {copied} copied in; the _complete.json manifests stay with the archived copy (nothing reads them)")
    run.retire(2, RECORDER_TAPE_COPY)


def check_raw_tape(run: Run) -> None:
    folder = run.archived_or_live(RECORDER_TAPE_COPY)
    collector = station_paths.SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER.folder(run.root)
    if folder is None:
        run.check(True, "[2] no recorder copy anywhere: nothing to compare")
        return
    streams = bad = 0
    for day_dir in sorted(p for p in folder.iterdir() if p.is_dir()):
        for g in sorted(day_dir.glob("*.gz")):
            streams += 1
            mine = _collector_stream(collector / day_dir.name, g.name)
            if mine is None or _stream_digest(mine) != _stream_digest(g):
                bad += 1
    run.check(bad == 0, f"[2] every one of the {streams} archived tape streams equals the collector's ({bad} differ or are missing)")
    run.check(not (run.root / RECORDER_TAPE_COPY).exists(), f"[2] {RECORDER_TAPE_COPY}/ is gone from its old place")


# --- 3. SPY's volume per minute ------------------------------------------------------------------------------------------

def _minutes(raw: dict) -> dict[str, int]:
    return {str(int(m)): int(v) for m, v in raw.items() if str(m).isdigit() and isinstance(v, (int, float))}


def _state_days(path: Path) -> dict[str, dict[str, int]]:
    try:
        days = (json.loads(path.read_text(encoding="utf-8")) or {}).get("days") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {d: _minutes(m) for d, m in days.items() if isinstance(m, dict) and station_paths.DAY_PATTERN.match(d)}


def merge_spy_volume(run: Run, now: datetime) -> None:
    state = run.root / SPY_VOLUME_STATE
    if not state.is_file():
        run.actions.append(f"[3] {SPY_VOLUME_STATE}: not on disk; nothing to fold")
        run.retire(3, SPY_VOLUME_STATE)
        return
    equal = written = 0
    for day, minutes in sorted(_state_days(state).items()):
        mine = station_store_files.read_spy_volume_by_minute(run.root, day)
        if not mine:
            written += 1
            if run.act(3, f"write {station_paths.SPY_VOLUME_BY_MINUTE.path(run.root, day=day).relative_to(run.root)} ({len(minutes)} minutes) from the state file"):
                station_store_files.write_spy_volume_by_minute(run.root, day, minutes, now)
        elif _minutes(mine) == minutes:
            equal += 1
        else:
            run.conflict(3, f"{day}: the state file's minutes and the day file's differ; the day file is kept")
    run.actions.append(f"[3] {equal} days equal in both copies, {written} day files written")
    run.retire(3, SPY_VOLUME_STATE)


def check_spy_volume(run: Run, keep_days: int = 60) -> None:
    state = run.archived_or_live(SPY_VOLUME_STATE)
    if state is None:
        run.check(True, "[3] no state file anywhere: nothing to compare")
        return
    memory = _state_days(state)
    newest = sorted(memory)[-keep_days:]
    baseline = {d: _minutes(m) for d, m in station_store_files.read_spy_volume_days(run.root, newest=keep_days, through=newest[-1] if newest else None).items()}
    same = {d: memory[d] for d in newest} == baseline
    last = newest[-1] if newest else "none"
    run.check(same, f"[3] the baseline the wall sensor reads (newest {keep_days} day files through {last}) is the memory it had: "
                    f"{len(baseline)} days, {sum(len(m) for m in baseline.values())} minutes{'' if same else ' - DIFFERENT'}")
    run.check(all(station_store_files.read_spy_volume_by_minute(run.root, d) for d in memory), f"[3] every one of the {len(memory)} days in the state file has a day file")
    run.check(not (run.root / SPY_VOLUME_STATE).exists(), f"[3] {SPY_VOLUME_STATE} is gone from its old place")


# --- 4. the open-interest book -------------------------------------------------------------------------------------------

def merge_open_interest(run: Run) -> None:
    latest = station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST.path(run.root)
    if not latest.is_file():
        run.actions.append("[4] no latest book on disk; nothing to keep")
        return
    try:
        book = json.loads(latest.read_text(encoding="utf-8"))
        stamp = station_store_files.open_interest_book_stamp(book["as_of"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        run.conflict(4, f"the latest book has no usable as_of ({type(e).__name__}); no history copy made")
        return
    history = station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY.path(run.root, stamp=stamp)
    if history.is_file():
        try:
            same = json.loads(history.read_text(encoding="utf-8")) == book
        except (OSError, ValueError):
            same = False
        if same:
            run.actions.append(f"[4] the latest book (as_of {book['as_of']}) is already in the history as {history.name}")
        else:
            run.conflict(4, f"the history's {history.name} differs from the latest book of the same as_of; both kept")
        return
    if run.act(4, f"write the latest book (as_of {book['as_of']}) into the history as {history.relative_to(run.root)}"):
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(latest, history)


def check_open_interest(run: Run) -> None:
    latest = station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST.path(run.root)
    if not latest.is_file():
        run.check(True, "[4] no latest book: nothing to compare")
        return
    try:
        book = json.loads(latest.read_text(encoding="utf-8"))
        history = station_paths.SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY.path(run.root, stamp=station_store_files.open_interest_book_stamp(book["as_of"]))
        ok = history.is_file() and json.loads(history.read_text(encoding="utf-8")) == book
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    run.check(ok, "[4] the latest open-interest book has its equal copy in the history folder")


# --- 5. the event calendar ---------------------------------------------------------------------------------------------

def _calendar_rows(path: Path) -> list[dict]:
    try:
        rows = (json.loads(path.read_text(encoding="utf-8")) or {}).get("events") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in rows if isinstance(e, dict)]


def _row_key(e: dict) -> tuple:
    return (e.get("date"), e.get("kind"), e.get("time_et"))


def merge_calendar(run: Run) -> None:
    copy = run.root / CALENDAR_DATA_COPY
    if not copy.is_file():
        run.actions.append(f"[5] {CALENDAR_DATA_COPY}: not on disk; nothing to fold")
        run.retire(5, CALENDAR_DATA_COPY)
        return
    kept = {_row_key(e): e for e in _calendar_rows(station_paths.MARKET_EVENT_CALENDAR_BY_EVENT.path())}
    present = 0
    for e in _calendar_rows(copy):
        theirs = kept.get(_row_key(e))
        if theirs is None:
            run.conflict(5, f"row {_row_key(e)} of the collector's copy is not in settings/market_event_calendar_by_event.json: add it by hand")
            continue
        present += 1
        if e.get("verified") is not None and theirs.get("verified") is not None and bool(e["verified"]) != bool(theirs["verified"]):
            run.actions.append(f"[5] row {_row_key(e)}: the copy says verified={e['verified']}, the station's calendar verified={theirs['verified']} "
                               f"(source {theirs.get('source', 'none')!r}); the station's stands")
    run.actions.append(f"[5] {present} rows of the collector's copy are in the station's calendar")
    run.retire(5, CALENDAR_DATA_COPY)


def check_calendar(run: Run) -> None:
    copy = run.archived_or_live(CALENDAR_DATA_COPY)
    if copy is None:
        run.check(True, "[5] no collector copy anywhere: nothing to compare")
        return
    kept = {_row_key(e) for e in _calendar_rows(station_paths.MARKET_EVENT_CALENDAR_BY_EVENT.path())}
    rows = _calendar_rows(copy)
    missing = [_row_key(e) for e in rows if _row_key(e) not in kept]
    run.check(not missing, f"[5] every one of the {len(rows)} rows of the collector's calendar is in the station's ({len(missing)} missing)")
    run.check(not (run.root / CALENDAR_DATA_COPY).exists(), f"[5] {CALENDAR_DATA_COPY} is gone from its old place")


# --- the run ---------------------------------------------------------------------------------------------------------------

MANIFEST = """# Retired 2026-10-12 — cleanup phase 5: one copy of every store

Moved here, not deleted, by scheduled_jobs/maintenance/merge_duplicate_stores_2026_10.py on {when}. Each item keeps its
old path under data/. Its data lives on in the store named beside it; the self-check at the end of the run read every
item back against that store. Delete after two weeks if nothing broke.

| Item | Was at | Lives on in |
|---|---|---|
| the scanner grader's saved SPX sessions ({{day}}-SPX.json) | market/spx_price_bars_by_minute_scanner_copy/ | market/spx_price_bars_by_minute/{{day}}.jsonl (the bars feed's store) |
| the recorder's gzip copies of the raw options tape (with _complete.json) | market/spx_same_day_options_raw_day_folders_recorder_copy/ | market/spx_same_day_options_raw_day_folders/{{day}}/ (kept forever, gzipped in place) |
| the wall sensor's one-file memory of SPY's volume per minute | spx/scanner/spy_volume_baselines_state.json | market/spy_volume_by_minute/{{day}}.json (one file a day) |
| the tape collector's event calendar | market/market_event_calendar_by_event.json | settings/market_event_calendar_by_event.json (in the repo) |

Each run appends its conflicts below (a conflict keeps both copies); a rerun never erases an earlier run's list.
"""
RUN_NOTE = """
## Run on {when}

{conflicts}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fold the five interim duplicate stores into one copy each (cleanup phase 5).")
    parser.add_argument("--data-root", required=True, help="the data root to merge in (never ~/mirai_awakening/data while a job writes to it)")
    parser.add_argument("--dry-run", action="store_true", help="list every action; change nothing")
    args = parser.parse_args(argv)
    root = Path(args.data_root).expanduser().resolve()
    if not root.is_dir():
        print(f"no such data root: {root}", file=sys.stderr)
        return 2
    now = datetime.now(ET)
    if now.weekday() < 5 and datetime(now.year, now.month, now.day, 9, 25, tzinfo=ET) <= now <= datetime(now.year, now.month, now.day, 16, 20, tzinfo=ET):
        print("WARNING: inside market hours; the wall sensor and the bars feed may be writing the stores this run touches", file=sys.stderr)
    run = Run(root, args.dry_run)
    print(f"{'DRY RUN: ' if args.dry_run else ''}merging the duplicate stores under {root}")
    merge_spx_bars(run)
    merge_raw_tape(run)
    merge_spy_volume(run, now)
    merge_open_interest(run)
    merge_calendar(run)
    for line in run.actions:
        print(line)
    for line in run.conflicts:
        print(line)
    if args.dry_run:
        print(f"dry run: {len(run.actions)} actions listed, {len(run.conflicts)} conflicts; nothing changed")
        return 0
    run.archive.mkdir(parents=True, exist_ok=True)
    manifest = run.archive / "MANIFEST.md"
    if not manifest.exists():
        manifest.write_text(MANIFEST.format(when=now.isoformat(timespec="seconds")), encoding="utf-8")
    with open(manifest, "a", encoding="utf-8") as f:
        f.write(RUN_NOTE.format(when=now.isoformat(timespec="seconds"),
                                conflicts="\n".join(f"- {c}" for c in run.conflicts) or "- no conflicts"))
    print("self-check:")
    check_spx_bars(run)
    check_raw_tape(run)
    check_spy_volume(run)
    check_open_interest(run)
    check_calendar(run)
    for ok, what in run.checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    failed = [what for ok, what in run.checks if not ok]
    print(f"done: {len(run.actions)} actions, {len(run.conflicts)} conflicts, {len(run.checks) - len(failed)} of {len(run.checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
