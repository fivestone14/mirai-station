"""After the close: save the day's full 1-minute bars, the market feed's and SPX's own, before Schwab forgets them.

    python3 -m spx_jev.save_day                    # the launchd job's run: today once it has closed, and any missed day
    python3 -m spx_jev.save_day --day 2026-09-25   # one past session by hand

Schwab keeps about 34 sessions of 1-minute history. The live snapshots (market_context.py) keep one
finished bar a minute for the breadth symbols and a quote for the rest; this job keeps every history
symbol's whole day, one line per minute, in ``state/spx_jev/context/bars/{day}.jsonl``: the file the
backfill writes and the labeller reads (market_context.backfill_day). A day already on disk is never
fetched again, so a rerun costs nothing, and a missed night is caught up by the next run that
succeeds, up to CATCH_UP_DAYS back.

SPX's own day. The gex-polarity job saves ``state/reversion/bars/{day}-SPX.json`` at 16:15 ET (every
diary day from 2026-07-06 to 2026-09-25 has one), but only when that day's diary carries its engines'
reads and the bars have real ranges, and a failed write is swallowed. So this job does not lean on it: when
the bars feed's own file, ``state/spx_jev/bars/{day}.jsonl``, holds fewer than the session's minutes,
it fetches the session once and appends what is missing (bars.append_day). A full file costs no call.

Market days only (sessions.is_trading_day; a calendar that cannot be read falls back to Monday to
Friday, and a holiday fetched that way writes nothing, since Schwab has no bars for it). A session is
saved only once it is SAVE_AFTER_CLOSE_MIN past its close (13:00 on a half day): a day written half
way would be taken as on disk and never completed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import bars, market_context
from .sessions import is_trading_day, session_close, session_minutes
from .state_builder import CONTEXT_SUBDIR, DEFAULT_STATE_DIR, load_jsonl

ET = ZoneInfo("America/New_York")
CATCH_UP_DAYS = 7              # a run also saves any market day this many calendar days back that is not on disk
SAVE_AFTER_CLOSE_MIN = 5       # the session's last bar (15:59) has finished and Schwab has served it


def days_to_save(now: datetime, catch_up: int = CATCH_UP_DAYS) -> list[date]:
    """The market days from ``catch_up`` days back to today, today only once it has closed."""
    today = now.date()
    last = today if now >= session_close(now) + timedelta(minutes=SAVE_AFTER_CLOSE_MIN) else today - timedelta(days=1)
    return [d for k in range(catch_up, -1, -1) if (d := today - timedelta(days=k)) <= last and is_trading_day(d)]


def session_bars_short(state_dir: Path, day: date) -> bool:
    """True when the bars feed's file for ``day`` holds fewer bars than the session has minutes."""
    noon = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=12)
    return len(load_jsonl(bars.bars_path(state_dir, day.isoformat()))) < session_minutes(noon)


class SaveFailed(RuntimeError):
    """One or both of a day's saves failed; the other ran all the same."""


def save_day(state_dir: Path, day: date, now: datetime) -> dict:
    """One session: SPX's minute bars, when its file is short, and the market feed's, unless on disk.
    Each save runs whatever the other did, so one market symbol Schwab refuses never costs SPX its day;
    a failure in either is raised (SaveFailed) once both have run.
    ``{"day", "context": path or None, "spx_bars_added": n}``."""
    failed = []
    added, written = 0, None
    try:
        if session_bars_short(state_dir, day):
            added = bars.append_day(state_dir, day.isoformat(), bars.fetch_session(day), now)
    except Exception as e:
        failed.append(f"SPX bars: {type(e).__name__}: {e}")
    try:
        written = market_context.backfill_day(state_dir, day)
    except Exception as e:
        failed.append(f"market bars: {type(e).__name__}: {e}")
    if failed:
        raise SaveFailed("; ".join(failed))
    return {"day": day.isoformat(), "context": str(written) if written else None, "spx_bars_added": added}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Save the day's full 1-minute bars: the market feed's symbols and SPX.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--day", help="YYYY-MM-DD, one finished session; default today once closed, and any missed day before it")
    args = ap.parse_args(argv)
    state_dir, now = Path(args.state_dir), datetime.now(ET)
    days = [date.fromisoformat(args.day)] if args.day else days_to_save(now)
    if not days:
        print("spx-jev-save-day :: no finished market day to save")
        return 0
    failed = 0
    for d in days:
        try:
            r = save_day(state_dir, d, now)
        except Exception as e:  # one day's failed fetch must not cost the other days; the next run retries it
            failed += 1
            print(f"spx-jev-save-day :: {d} failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        context = f"market bars -> {r['context']}" if r["context"] else "market bars already on disk or none served"
        print(f"spx-jev-save-day :: {d} {context}; SPX +{r['spx_bars_added']} bars")
    print(f"spx-jev-save-day :: {len(days) - failed} of {len(days)} days saved under {state_dir / CONTEXT_SUBDIR / 'bars'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
