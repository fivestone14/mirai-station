"""After the close: save the day's full 1-minute bars, the market feed's and SPX's own, before Schwab forgets them,
and the daily closes of the few symbols kept years back.

    python3 -m spx_jev.save_day                    # the launchd job's run: today once it has closed, and any missed day
    python3 -m spx_jev.save_day --day 2026-09-25   # one past session by hand

Schwab keeps about 34 sessions of 1-minute history. The live snapshots (market_context.py) keep one
finished bar a minute for the breadth symbols and a quote for the rest; this job keeps every history
symbol's whole day, one line per minute, in ``state/spx_jev/context/bars/{day}.jsonl``: the file the
backfill writes and the labeller reads (market_context.backfill_day). A day already on disk is never
fetched again, so a rerun costs nothing, and a missed night is caught up by the next run that
succeeds, up to CATCH_UP_DAYS back. The one exception is the breadth: Schwab serves a session's NYSE
breadth wrong until after it, so a day saved on its own evening has its breadth fetched again by a later
run, once Schwab has put it right (market_context.refresh_breadth).

SPX's own day. The gex-polarity job saves ``state/reversion/bars/{day}-SPX.json`` at 16:15 ET (every
diary day from 2026-07-06 to 2026-09-25 has one), but only when that day's diary carries its engines'
reads and the bars have real ranges, and a failed write is swallowed. So this job does not lean on it: when
the bars feed's own file, ``state/spx_jev/bars/{day}.jsonl``, holds fewer than the session's minutes,
it fetches the session once and appends what is missing (bars.append_day). A full file costs no call.

Market days only (sessions.is_trading_day; a calendar that cannot be read falls back to Monday to
Friday, and a holiday fetched that way writes nothing, since Schwab has no bars for it). A session is
saved only once it is SAVE_AFTER_CLOSE_MIN past its close (13:00 on a half day): a day written half
way would be taken as on disk and never completed.

Daily closes. The same run then saves each daily-closes symbol's whole history through the last closed session
(daily_closes.save: one call a symbol, the file rewritten whole), after the days above and never in their way: a
failed symbol is reported and the run exits 1, so the next run fetches it again.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import bars, daily_closes, market_context
from .sessions import is_trading_day, session_close, session_minutes
from .state_builder import CONTEXT_SUBDIR, DEFAULT_STATE_DIR, load_jsonl

ET = ZoneInfo("America/New_York")
CATCH_UP_DAYS = 7              # a run also saves any market day this many calendar days back that is not on disk
SAVE_AFTER_CLOSE_MIN = 5       # the session's last bar (15:59) has finished and Schwab has served it


def last_closed(now: datetime) -> date:
    """Today once it is SAVE_AFTER_CLOSE_MIN past its close, else the day before (a market day or not)."""
    today = now.date()
    return today if now >= session_close(now) + timedelta(minutes=SAVE_AFTER_CLOSE_MIN) else today - timedelta(days=1)


def days_to_save(now: datetime, catch_up: int = CATCH_UP_DAYS) -> list[date]:
    """The market days from ``catch_up`` days back to today, today only once it has closed."""
    today, last = now.date(), last_closed(now)
    return [d for k in range(catch_up, -1, -1) if (d := today - timedelta(days=k)) <= last and is_trading_day(d)]


def session_bars_short(state_dir: Path, day: date) -> bool:
    """True when the bars feed's file for ``day`` holds fewer bars than the session has minutes."""
    noon = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=12)
    return len(load_jsonl(bars.bars_path(state_dir, day.isoformat()))) < session_minutes(noon)


class SaveFailed(RuntimeError):
    """One or both of a day's saves failed; the other ran all the same."""


def save_day(state_dir: Path, day: date, now: datetime) -> dict:
    """One session: SPX's minute bars, when its file is short, and the market feed's, unless on disk; a
    session before ``now``'s day also has its breadth fetched again when it was saved on its own day.
    Each save runs whatever the other did, so one market symbol Schwab refuses never costs SPX its day;
    a failure in either is raised (SaveFailed) once both have run.
    ``{"day", "context": path or None, "breadth": path or None, "spx_bars_added": n}``."""
    failed = []
    added, written, breadth = 0, None, None
    try:
        if session_bars_short(state_dir, day):
            added = bars.append_day(state_dir, day.isoformat(), bars.fetch_session(day), now)
    except Exception as e:
        failed.append(f"SPX bars: {type(e).__name__}: {e}")
    try:
        written = market_context.backfill_day(state_dir, day)
        if day < now.date():
            breadth = market_context.refresh_breadth(state_dir, day)
    except Exception as e:
        failed.append(f"market bars: {type(e).__name__}: {e}")
    if failed:
        raise SaveFailed("; ".join(failed))
    return {"day": day.isoformat(), "context": str(written) if written else None, "breadth": str(breadth) if breadth else None,
            "spx_bars_added": added}


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
        breadth = "; breadth fetched again, put right" if r["breadth"] else ""
        print(f"spx-jev-save-day :: {d} {context}{breadth}; SPX +{r['spx_bars_added']} bars")
    print(f"spx-jev-save-day :: {len(days) - failed} of {len(days)} days saved under {state_dir / CONTEXT_SUBDIR / 'bars'}")
    closes = save_daily_closes(state_dir, now)
    return 1 if failed or closes["failed"] else 0


def save_daily_closes(state_dir: Path, now: datetime) -> dict:
    """daily_closes.save through the last closed session, each symbol's count printed, a failed one named; a
    failure it does not catch itself is reported as every symbol's."""
    try:
        r = daily_closes.save(state_dir, last_closed(now), now)
    except Exception as e:  # the days above are saved; the next run fetches the closes again
        print(f"spx-jev-save-day :: daily closes failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {"saved": {}, "failed": {s: type(e).__name__ for s in daily_closes.SYMBOLS}}
    for symbol, why in r["failed"].items():
        print(f"spx-jev-save-day :: daily closes of {symbol} not saved: {why}", file=sys.stderr)
    print("spx-jev-save-day :: daily closes " + (", ".join(f"{s} {n} sessions" for s, n in r["saved"].items()) or "none saved")
          + f" under {state_dir / daily_closes.DAILY_CLOSES_SUBDIR}")
    return r


if __name__ == "__main__":
    raise SystemExit(main())
