"""sndk_deadman.py — the dead-man's switch for the SNDK scanner.

Plain English:
This job's only question is "is the SNDK scanner still alive?" It looks at the
one artifact that cannot be faked — today's diary file — and pages the phone
when that file stops growing during market hours, or never gets its first row
after the open. It reads no market data and calls no model, so it cannot fail
for the same reason the scanner failed.

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

WHAT IT ASSERTS, both measured off the diary file itself:
  1. FIRST ROW BY 09:35 ET. The scanner fires every ~2 minutes from the open,
     so a session with no row five minutes in never started.
  2. STILL GROWING. The newest row must be younger than SNDK_SILENT_MIN. The
     scan cadence is ~2 minutes and the reader already refuses a book older
     than 6, so 6 is the age at which the data has stopped being usable — not
     an arbitrary patience setting.

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
from typing import Any, Dict, Optional
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

_DIARY_SUBDIR = "sndk_reversion"

# The two ways a session can be dead. Both are cleared by a delivered recovery
# page, which is what re-arms the switch for a SECOND outage the same day — the
# first draft added a key and never removed one, so a scanner that blipped at
# 10:00 and recovered went permanently deaf and slept through a 90-minute
# afternoon hole. "One page per condition per OUTAGE" was the intent all along;
# "one page per condition per day" was the accident.
_OUTAGE_KEYS = frozenset(("no_first_row", "silent"))


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "sndk_reads" / "deadman_state.json"


def _load_state(state_dir: Path, date_iso: str) -> Dict[str, Any]:
    fresh = {"date": date_iso, "paged": [], "down_since": None}
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


def _newest_row_ts(path: Path) -> Optional[datetime]:
    """The timestamp of the last parseable SNDK row in today's diary.

    Reads the file, not the mtime: a file can be touched, re-flushed or copied
    without a row landing in it, and the claim being made is about ROWS. Walks
    backwards so a long session costs one pass over the tail, and tolerates a
    torn final line — the writer appends, so the last line is the one most
    likely to be half-written."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        # a line that parses to something other than an object is still garbage:
        # `json.loads` succeeds on a bare string or number, and .get() on one is
        # an AttributeError that would take the whole watchdog down over a torn
        # write. Caught by this module's own test before it ever ran live.
        if not isinstance(r, dict):
            continue
        if r.get("ticker") != "SNDK" or (r.get("meta") or {}).get("forced"):
            continue
        try:
            t = datetime.fromisoformat(str(r.get("ts")))
        except (TypeError, ValueError):
            continue
        return t if t.tzinfo else t.replace(tzinfo=ET)
    return None


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
                        f"Watching {_DIARY_SUBDIR}/{date_iso}.jsonl, "
                        f"silence ceiling {SNDK_SILENT_MIN:g}m.", tag="sndk-deadman")
        ok = bool(rec.get("dispatched"))
        return {"test_fire": True, "delivered": ok, "paged": 1 if ok else 0,
                "error": None if ok else (rec.get("error") or "did not dispatch")}

    out: Dict[str, Any] = {"checked": False, "paged": 0, "alive": None,
                           "age_min": None, "reason": None}
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

    st["paged"] = sorted(paged)
    _save_state(state_dir, st)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SNDK scanner dead-man's switch")
    ap.add_argument("--test-fire", action="store_true",
                    help="send one proof-of-life page and exit")
    args = ap.parse_args(argv)
    res = run(test_fire=args.test_fire)
    print("sndk-deadman :: " + json.dumps(res, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
