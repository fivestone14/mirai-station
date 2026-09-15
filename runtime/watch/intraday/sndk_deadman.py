"""sndk_deadman.py — the dead-man's switch for the SNDK scanner and its reader.

Plain English:
This job's only question is "are the SNDK scanner and its reader still alive?"
It looks at the two artifacts that cannot be faked — today's diary file and
today's read file — and pages the phone when the diary stops growing during
market hours, never gets its first row after the open, or keeps growing while
the read file does not. It reads no market data and calls no model, so it cannot
fail for the same reason the scanner or the reader failed.

WHY THIS EXISTS (2026-08-30). gex_alerts already runs a scanner-silence siren,
but its `LENS_TICKERS = ("SPX",)` means it has only ever watched the index. SNDK
had no watchdog at all, and four whole sessions died unnoticed in August alone —
08-07, 08-14, 08-17, 08-24 — each one a day where the chart, the phone and the
reader all showed a frozen board that looked exactly like a quiet tape. Silence
was indistinguishable from calm, which is the same failure the 07-13 0DTE
outage taught and the same one the gamma-tape guard taught: a check that cannot
say no is not a check.

WHY IT IS ITS OWN LAUNCHD JOB rather than another pass inside gex_alerts:
gex_alerts runs from the left-eye tick. If the left-eye pipeline is what died,
the siren dies with it and the silence is total. A dead-man's switch that shares
a process with the thing it watches is not a dead-man's switch. This one runs
on its own timer, in its own process, and touches nothing the scanner touches.

WHAT IT ASSERTS, each measured off the files themselves:
  1. FIRST ROW BY 09:35 ET. The scanner fires every ~2 minutes from the open,
     so a session with no row five minutes in never started.
  2. STILL GROWING. The newest row must be younger than SNDK_SILENT_MIN. The
     scan cadence is ~2 minutes and the reader already refuses a book older
     than 6, so 6 is the age at which the data has stopped being usable — not
     an arbitrary patience setting.
  3. THE READER KEEPS UP. While the scanner is alive, the newest read row must
     be younger than READER_SILENT_MIN, counted from when the scanner's current
     run of rows began if that is later. The reader writes nothing until there
     is a diary row to read, so a reader stopped by a dead scanner is the
     scanner's outage and is paged once, under the scanner's name.

One page per condition per day, plus one recovery page when it comes back, so a
dead afternoon costs two notifications rather than fifty. `--test-fire` sends a
single proof-of-life page: a watchdog nobody has ever seen fire is indistinguish-
able from a watchdog that cannot fire, and this one is meant to be tested on a
quiet day rather than trusted on a bad one.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from zoneinfo import ZoneInfo

from .. import paths, push
from . import market_status, push_ntfy

ET = ZoneInfo("America/New_York")

# The scanner writes every ~2 min; the reader refuses a book older than 6 min
# (sndk_read.STALE_BOOK_MIN). Past that the rows are not merely late, they are
# no longer good enough to read — so that is the line the pager uses too.
SNDK_SILENT_MIN = 6.0

# Five minutes after the open. The scanner's first row lands within ~2 minutes
# of 09:30 on every recorded session; 09:35 leaves room for one missed tick
# without inventing patience for a session that never began.
FIRST_ROW_BY_MIN = 9 * 60 + 35

# A read row is stamped when the read STARTS and lands when it ENDS, and one
# read may run the model call to its timeout and then the semantic guard to its
# own: sndk_read's max(CALL_TIMEOUT_S, CALL_TIMEOUT_STRIKES_S) +
# SEMANTIC_GUARD_TIMEOUT_S. So the reader is held to the scanner's line plus
# that one read, and not a minute more.
READ_MAX_RUN_S = 220.0
READER_SILENT_MIN = SNDK_SILENT_MIN + READ_MAX_RUN_S / 60.0

_DIARY_SUBDIR = "sndk_reversion"
_READS_SUBDIR = "sndk_reads"

# The two ways a session can be dead. Both are cleared by a delivered recovery
# page, which is what re-arms the switch for a SECOND outage the same day — the
# first draft added a key and never removed one, so a scanner that blipped at
# 10:00 and recovered went permanently deaf and slept through a 90-minute
# afternoon hole. "One page per condition per OUTAGE" was the intent all along;
# "one page per condition per day" was the accident.
_OUTAGE_KEYS = frozenset(("no_first_row", "silent"))
# The reader's outage, re-armed the same way by its own recovery page.
_READER_KEYS = frozenset(("reader_silent",))


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / _READS_SUBDIR / "deadman_state.json"


def _load_state(state_dir: Path, date_iso: str) -> Dict[str, Any]:
    fresh = {"date": date_iso, "paged": [], "down_since": None,
             "reader_down_since": None}
    try:
        blob = json.loads(_state_path(state_dir).read_text())
    except (OSError, ValueError):
        return fresh
    return blob if blob.get("date") == date_iso else fresh


def _save_state(state_dir: Path, st: Dict[str, Any]) -> None:
    p = _state_path(state_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st))
    except OSError:
        pass          # a watchdog that cannot write its ledger still pages


def _is_scan(r: Dict[str, Any]) -> bool:
    """A scheduled SNDK diary row; `meta.forced` rows are off-hours manual runs."""
    return r.get("ticker") == "SNDK" and not (r.get("meta") or {}).get("forced")


def _is_read(r: Dict[str, Any]) -> bool:
    """A scheduled read row; a `--force` read is a human's, not the reader's."""
    return not r.get("forced")


def _row_stamps(path: Path, counts) -> Iterator[datetime]:
    """The timestamps of the parseable rows in `path` that `counts`, newest first.

    Reads the file, not the mtime: a file can be touched, re-flushed or copied
    without a row landing in it, and the claim being made is about ROWS. Walks
    backwards so the newest row costs one pass over the tail, and tolerates a
    torn final line — the writer appends, so the last line is the one most
    likely to be half-written."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        # a line that parses to something other than an object is still garbage:
        # `json.loads` succeeds on a bare string or number, and .get() on one is
        # an AttributeError that would take the whole watchdog down over a torn
        # write. Caught by this module's own test before it ever ran live.
        if not isinstance(r, dict) or not counts(r):
            continue
        try:
            t = datetime.fromisoformat(str(r.get("ts")))
        except (TypeError, ValueError):
            continue
        yield t if t.tzinfo else t.replace(tzinfo=ET)


def _newest_row_ts(path: Path, counts=_is_scan) -> Optional[datetime]:
    """The timestamp of the last parseable row in `path` that `counts`."""
    return next(_row_stamps(path, counts), None)


def _run_began(path: Path) -> Optional[datetime]:
    """When the scanner's current run of rows began: the oldest diary row with no
    gap wider than SNDK_SILENT_MIN between it and the newest. A reader given no
    new rows to read is not late until it has had its ceiling after the scanner
    came back."""
    began = None
    for t in _row_stamps(path, _is_scan):
        if began is not None and (began - t).total_seconds() / 60.0 > SNDK_SILENT_MIN:
            break
        began = t
    return began


def run(now: Optional[datetime] = None, *, state_dir: Optional[Path] = None,
        channel=None, test_fire: bool = False) -> Dict[str, Any]:
    """One dead-man check. Providers injectable for tests; defaults are live."""
    now = now or datetime.now(tz=ET)
    now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    date_iso = now_et.date().isoformat()
    minutes = now_et.hour * 60 + now_et.minute
    state_dir = Path(state_dir or paths.STATE_DIR)
    push.set_channel(channel or push_ntfy.make_channel())

    if test_fire:
        # reports what actually happened. A test fire that prints "paged: 1"
        # whether or not the message left the machine tests nothing, which is
        # the failure this whole module exists to end.
        rec = push.send("🐕 SNDK dead-man's switch: TEST FIRE — the pager works. "
                        f"Watching {_DIARY_SUBDIR}/{date_iso}.jsonl and "
                        f"{_READS_SUBDIR}/{date_iso}.jsonl, silence ceilings "
                        f"{SNDK_SILENT_MIN:g}m and {READER_SILENT_MIN:.1f}m.",
                        tag="sndk-deadman")
        ok = bool(rec.get("dispatched"))
        return {"test_fire": True, "delivered": ok, "paged": 1 if ok else 0,
                "error": None if ok else (rec.get("error") or "did not dispatch")}

    out: Dict[str, Any] = {"checked": False, "paged": 0, "alive": None,
                           "age_min": None, "reason": None, "reader": None}
    if not market_status.check(now_et).is_live:
        out["reason"] = "market closed"
        return out

    st = _load_state(state_dir, date_iso)
    paged = set(st.get("paged") or [])
    path = Path(state_dir) / _DIARY_SUBDIR / f"{date_iso}.jsonl"
    newest = _newest_row_ts(path)
    out["checked"] = True

    def page(key: str, text: str) -> bool:
        """Send, and only remember it if it actually left the machine.

        The dedup ledger must be a record of DELIVERY, not of intent. push.send
        catches the channel's exception and reports `dispatched: False` (an
        unreachable ntfy, a revoked topic) — and marking the key anyway would
        mean the one notification the outage was going to get was swallowed and
        never retried, in the module whose entire job is to not be silent. A
        failed send stays un-paged so the next five-minute tick tries again;
        `undelivered` says so out loud rather than reporting a page that no
        phone ever saw."""
        if key in paged:
            return False
        rec = push.send(text, tag="sndk-deadman")
        if not rec.get("dispatched"):
            out.setdefault("undelivered", []).append(
                {"key": key, "why": rec.get("error") or "channel did not dispatch"})
            return False
        paged.add(key)
        out["paged"] += 1
        return True

    if newest is None:
        # No row at all. Before 09:35 that is an opening that has not happened
        # yet, not a failure — the scanner is allowed those five minutes.
        out["alive"] = False
        out["reason"] = "no rows yet"
        if minutes >= FIRST_ROW_BY_MIN:
            page("no_first_row",
                 f"🔴 SNDK scanner: no diary row for {date_iso} at "
                 f"{now_et:%H:%M} ET — the session never started. "
                 f"The chart and the reader are showing yesterday's board.")
    else:
        age = (now_et - newest).total_seconds() / 60.0
        out["age_min"] = round(age, 1)
        out["alive"] = age <= SNDK_SILENT_MIN
        if not out["alive"]:
            out["reason"] = "silent"
            st["down_since"] = st.get("down_since") or newest.isoformat()
            page("silent",
                 f"🔴 SNDK scanner silent {age:.0f}m — newest diary row "
                 f"{newest:%H:%M} ET, ceiling {SNDK_SILENT_MIN:g}m. "
                 f"Every number on the SNDK screen is frozen at that time.")
        else:
            out["reason"] = "alive"
            # The recovery page is not a courtesy. Without it the last thing the
            # phone ever said about SNDK is "silent" — or worse, "the session
            # never started", which on 2026-08-10 (first row 09:44) and
            # 2026-08-07 (first row 10:17) would have stood uncorrected through
            # 183 healthy rows to the close. It clears BOTH outage keys, and
            # only once the recovery itself is delivered.
            outstanding = paged & _OUTAGE_KEYS
            if outstanding:
                down = st.get("down_since")
                since = (f" (silent since {str(down)[11:16]} ET)" if down else
                         " — the earlier 'never started' page was wrong"
                         if "no_first_row" in outstanding else "")
                if page("recovered",
                        f"🟢 SNDK scanner back — rows landing again at "
                        f"{newest:%H:%M} ET{since}."):
                    paged -= (_OUTAGE_KEYS | {"recovered"})
                    st["down_since"] = None

    # The reader is judged only while the scanner is alive. With no fresh rows it
    # has nothing new to read, and the scanner's page already names that outage:
    # one page for the root cause, never a second one for its echo.
    if out["alive"]:
        began = _run_began(path)
        read = _newest_row_ts(Path(state_dir) / _READS_SUBDIR / f"{date_iso}.jsonl", _is_read)
        since = max(read, began) if read else began
        age = (now_et - since).total_seconds() / 60.0
        out["reader"] = {"alive": age <= READER_SILENT_MIN, "age_min": round(age, 1)}
        if not out["reader"]["alive"]:
            st["reader_down_since"] = st.get("reader_down_since") or since.isoformat()
            last = f"newest read row {read:%H:%M} ET" if read else "no read row today"
            page("reader_silent",
                 f"🔴 SNDK reader silent {age:.0f}m — {last}, while the scanner "
                 f"has written rows since {began:%H:%M} ET; ceiling "
                 f"{READER_SILENT_MIN:.1f}m. The reading on the chart and the "
                 f"phone is frozen.")
        elif paged & _READER_KEYS:
            # Back only once a read row lands after the outage began. The
            # scanner's own return restarts the reader's allowance, and that
            # alone is not evidence the reader is writing again.
            down = st.get("reader_down_since")
            if read and (not down or read > datetime.fromisoformat(down)):
                if page("reader_recovered",
                        f"🟢 SNDK reader back — read rows landing again at "
                        f"{read:%H:%M} ET"
                        + (f" (silent since {str(down)[11:16]} ET)." if down else ".")):
                    paged -= (_READER_KEYS | {"reader_recovered"})
                    st["reader_down_since"] = None

    st["paged"] = sorted(paged)
    _save_state(state_dir, st)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SNDK scanner and reader dead-man's switch")
    ap.add_argument("--test-fire", action="store_true",
                    help="send one proof-of-life page and exit")
    args = ap.parse_args(argv)
    res = run(test_fire=args.test_fire)
    print("sndk-deadman :: " + json.dumps(res, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
