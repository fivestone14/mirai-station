"""spx_premarket_deadman.py — the dead-man's switch for the SPX pre-market lane.

Plain English:
The pre-market job (com.mirai-station.spx-jev-premarket) reads at the night's
checkpoints before the open and closes out at 10:06 ET. A checkpoint it misses
is never read later — the lane refuses a fire 5 minutes or more late — so a Mac
asleep at 02:35, or a run that crashed, leaves a hole nobody sees until the
health review is run by hand. This job asks one question every five minutes on
a market day: has every checkpoint that is owed by now got its read, and has the
close-out landed with every call graded? It pages the phone once per miss.

It reads only the lane's own two files, the same ones the health review's
premarket_fired check reads under the same limits: today's card (latest.json:
its day, the checkpoints it names, closed_out_at, tally) and today's read file (one
line per checkpoint read). It imports nothing from spx_jev, reads no feed and
calls no model, so it cannot fail for the reason the lane failed.

WHAT IT ASSERTS:
  1. A READ AT EVERY CHECKPOINT once OWED_AFTER_MIN has passed it. Before
     today's first read the card is the last market day's, so the first
     checkpoint is judged by the card's day alone.
  2. THE CLOSE-OUT LANDED on today's card by OWED_AFTER_MIN after 10:06, and
     left no call still to grade (the tally's calls less those graded and those
     closed for good). A close-out whose bars had not come leaves the call
     pending, and a later live read grades it again.

A missed read cannot come back, so there is no recovery page: one page per
missed checkpoint and one for the close-out, each remembered only once it is
delivered. Only a checkpoint's page says it is never read later; the live job's
reads try a close-out again.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from .. import paths, push
from . import market_status, push_ntfy

ET = ZoneInfo("America/New_York")

# The lane's first checkpoint and its close-out (skills/spx-jev/spx_jev/lane.py,
# PREMARKET.schedule[0] and .close_out), and the minutes after a checkpoint from
# which it refuses a fire (premarket.LATE_FIRE_MIN). runtime/health/
# test_live_station.py holds these to the lane's code.
FIRST_CHECKPOINT = "02:35"
CLOSE_OUT = "10:06"
LATE_FIRE_MIN = 5
# A read's worst run: the night's save, eight Schwab calls each given up after
# 30 seconds, then JEV's calls with a retry each (the health review's
# PREMARKET_RUN_MIN). A read is owed this long after its checkpoint.
RUN_MIN = 10
OWED_AFTER_MIN = LATE_FIRE_MIN + RUN_MIN
# Said only of a missed checkpoint: a close-out's miss is tried again by the live
# job's reads (service.retry_close_outs).
NEVER_READ_LATER = " The job fires on its own calendar times; a checkpoint missed is never read later."

_LANE_SUBDIR = Path("spx_jev") / "lanes" / "premarket"


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / _LANE_SUBDIR / "deadman_state.json"


def _load_state(state_dir: Path, date_iso: str) -> Dict[str, Any]:
    try:
        blob = json.loads(_state_path(state_dir).read_text())
    except (OSError, ValueError):
        blob = None
    return blob if isinstance(blob, dict) and blob.get("date") == date_iso else {"date": date_iso, "paged": []}


def _save_state(state_dir: Path, st: Dict[str, Any]) -> None:
    p = _state_path(state_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st))
    except OSError:
        pass          # a watchdog that cannot write its ledger still pages


def _card(lane_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        card = json.loads((lane_dir / "latest.json").read_text())
    except (OSError, ValueError):
        return None
    return card if isinstance(card, dict) else None


def _read_checkpoints(path: Path) -> set:
    """The checkpoints ("HH:MM") today's read file holds a line for; a torn line is skipped."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return set()
    out = set()
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("checkpoint"):
            out.add(r["checkpoint"])
    return out


def _still_to_grade(card: Dict[str, Any]) -> int:
    """The card's calls neither graded nor closed for good; 0 when it carries no tally."""
    t = card.get("tally")
    if not isinstance(t, dict):
        return 0
    try:
        return max(0, int(t.get("calls") or 0) - int(t.get("graded") or 0) - int(t.get("closed") or 0))
    except (TypeError, ValueError):
        return 0


def _is_market_day(now_et: datetime) -> bool:
    day = now_et.date()
    return now_et.weekday() < 5 and day not in market_status._market_holidays(day.year)


def run(now: Optional[datetime] = None, *, state_dir: Optional[Path] = None,
        channel=None, test_fire: bool = False) -> Dict[str, Any]:
    """One check. Providers injectable for tests; defaults are live."""
    now = now or datetime.now(tz=ET)
    now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    date_iso = now_et.date().isoformat()
    state_dir = Path(state_dir or paths.STATE_DIR)
    lane_dir = state_dir / _LANE_SUBDIR
    push.set_channel(channel or push_ntfy.make_channel())

    if test_fire:
        rec = push.send("🐕 SPX pre-market dead-man's switch: TEST FIRE — the pager works. "
                        f"Watching {_LANE_SUBDIR}/latest.json and {_LANE_SUBDIR}/{date_iso}.jsonl, "
                        f"a read owed {OWED_AFTER_MIN} min after its checkpoint.", tag="spx-premarket-deadman")
        ok = bool(rec.get("dispatched"))
        return {"test_fire": True, "delivered": ok, "paged": 1 if ok else 0,
                "error": None if ok else (rec.get("error") or "did not dispatch")}

    out: Dict[str, Any] = {"checked": False, "paged": 0, "missed": [], "reason": None}
    if not _is_market_day(now_et):
        out["reason"] = f"{date_iso} is not a market day"
        return out
    owed_after = timedelta(minutes=OWED_AFTER_MIN)

    def at(hhmm: str) -> datetime:
        return datetime.combine(now_et.date(), time.fromisoformat(hhmm), tzinfo=ET)
    if now_et < at(FIRST_CHECKPOINT) + owed_after:
        out["reason"] = f"no read owed before {at(FIRST_CHECKPOINT) + owed_after:%H:%M} ET"
        return out

    out["checked"] = True
    st = _load_state(state_dir, date_iso)
    paged = set(st.get("paged") or [])
    card = _card(lane_dir)
    if card is None or card.get("day") != date_iso:
        # the card is still the last market day's: not even the first checkpoint was read
        misses = [(FIRST_CHECKPOINT, "no pre-market read today")]
    else:
        read = _read_checkpoints(lane_dir / f"{date_iso}.jsonl")
        misses = []
        for stamp in (card.get("schedule") or {}).get("reads") or []:
            try:
                t = datetime.fromisoformat(str(stamp)).astimezone(ET)
            except ValueError:
                continue
            if t + owed_after <= now_et and f"{t:%H:%M}" not in read:
                misses.append((f"{t:%H:%M}", f"no read at the {t:%H:%M} ET checkpoint"))
        if now_et >= at(CLOSE_OUT) + owed_after:
            left = _still_to_grade(card)
            if left:
                misses.append((CLOSE_OUT, f"{left} pre-market call{'s' if left > 1 else ''} still ungraded after the "
                                          f"{CLOSE_OUT} ET close-out; a later live read tries again"))
            elif not card.get("closed_out_at"):
                misses.append((CLOSE_OUT, f"the {CLOSE_OUT} ET close-out has not landed; a later live read tries again"))
    out["missed"] = [m for m, _ in misses]

    for key, what in misses:
        if key in paged:
            continue
        rec = push.send(f"🔴 SPX pre-market: {what} ({now_et:%H:%M} ET)."
                        f"{'' if key == CLOSE_OUT else NEVER_READ_LATER}", tag="spx-premarket-deadman")
        if not rec.get("dispatched"):
            # the ledger records delivery, not intent: an undelivered page is tried again next tick
            out.setdefault("undelivered", []).append(
                {"key": key, "why": rec.get("error") or "channel did not dispatch"})
            continue
        paged.add(key)
        out["paged"] += 1
    out["reason"] = "missed" if misses else "every read owed is on file"

    st["paged"] = sorted(paged)
    _save_state(state_dir, st)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SPX pre-market lane dead-man's switch")
    ap.add_argument("--test-fire", action="store_true",
                    help="send one proof-of-life page and exit")
    args = ap.parse_args(argv)
    res = run(test_fire=args.test_fire)
    print("spx-premarket-deadman :: " + json.dumps(res, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
