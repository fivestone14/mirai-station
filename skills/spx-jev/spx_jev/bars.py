"""Today's SPX minute bars, on disk as they finish: the live-bars sidecar.

    python3 -m spx_jev.bars                   # append today's finished minutes (the launchd job's run)
    python3 -m spx_jev.bars --day 2026-09-25  # fill a past session by hand

Past sessions' bars are saved once a day after the close (``state/reversion/bars/{day}-SPX.json``,
the gex-polarity job); nothing kept today's until this job. The scanner fetches them on every scan
and keeps none, so the opening lane, which stamps each read at the newest finished bar, and the
grader, which reads the bar at each mark, would have nothing to read before 16:15 ET. Every run
fetches the whole session in one call (Schwab's 1-minute history, the station's own client) and
appends each finished minute not yet on disk to ``state/spx_jev/bars/{day}.jsonl``, so a gap left by
a missed run is filled by the next one that succeeds.

Only finished minutes are written: the running minute's high and low are still moving. The shape
is the station's bar shape, the one the past-session files use, so the labeller reads both alike:
    {"ts": "<ISO, America/New_York, the bar's start>", "open", "high", "low", "close", "volume"}
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import schwab
from .feed_log import failure, log
from .sessions import SESSION_CLOSE, SESSION_OPEN
from .state_builder import DEFAULT_STATE_DIR, LIVE_BARS_SUBDIR, load_jsonl

ET = ZoneInfo("America/New_York")
SYMBOL = "SPX"
JOB = "spx-jev-bars"


def bars_path(state_dir: Path, day: str) -> Path:
    return Path(state_dir) / LIVE_BARS_SUBDIR / f"{day}.jsonl"


def finished(bars: list[dict], now: datetime) -> list[dict]:
    """Bars whose minute has fully elapsed by ``now``."""
    return [b for b in bars if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= now]


def fetch_session(day: date) -> list[dict]:
    """The regular session's 1-minute bars, 09:30 to 16:00 ET, by the clock and not by trust in a flag."""
    start = datetime.combine(day, SESSION_OPEN, tzinfo=ET)
    end = datetime.combine(day, SESSION_CLOSE, tzinfo=ET)
    return [b for b in schwab.minute_bars(SYMBOL, start, end + timedelta(minutes=1))
            if start <= datetime.fromisoformat(b["ts"]) < end]


def append_day(state_dir: Path, day: str, bars: list[dict], now: datetime) -> int:
    """Append every finished bar whose minute is not on disk yet; returns how many. A rerun with the
    same session appends nothing."""
    path = bars_path(state_dir, day)
    have = {b["ts"][:16] for b in load_jsonl(path) if isinstance(b.get("ts"), str)}
    fresh = []
    for b in sorted(finished(bars, now), key=lambda b: b["ts"]):
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET).isoformat()
        if ts[:16] in have:
            continue
        have.add(ts[:16])
        fresh.append({"ts": ts, **{k: float(b[k]) for k in ("open", "high", "low", "close")}, "volume": float(b.get("volume") or 0.0)})
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for b in fresh:
                f.write(json.dumps(b) + "\n")
    return len(fresh)


def run(state_dir: Path, day: str | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now(ET)
    d = date.fromisoformat(day) if day else now.date()
    try:
        bars = fetch_session(d)
    except Exception as e:  # a failed fetch is a skipped run; the next run fills the gap
        log(JOB, f"fetch failed for {d}: {failure(e)}", err=True)
        return 1
    n = append_day(state_dir, d.isoformat(), bars, now)
    log(JOB, f"{d} +{n} bars -> {bars_path(state_dir, d.isoformat())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Append today's finished SPX minute bars to state/spx_jev/bars/.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--day", help="YYYY-MM-DD session to fetch, default today; a past session backfills")
    args = ap.parse_args(argv)
    return run(Path(args.state_dir), args.day)


if __name__ == "__main__":
    raise SystemExit(main())
