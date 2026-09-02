"""SNDK minute bars — the SIDECAR (2026-09-02).

One line per completed minute of the regular session, appended to
state/sndk_bars/<day>.jsonl by its own launchd job (com.mirai-station.sndk-bars,
every 60 s, RTH plus a twelve-minute catch-up window after the close). The
scanner already fetched today's bars on every 2-minute tick and kept none of
them — it used them for VWAP and threw them away — so the day's highs and lows,
the opening box, and every box break the reader judged were 2-minute SAMPLES:
measured over 23 sessions against these bars, the session extremes sat a median
0.10 sigma (~$7) short, the opening box ~$8 too narrow, and one box break in
three was missed or invented (scratchpad thin_measure.py, 09-02).

WHY A SIDECAR AND NOT THE DIARY ROW. Schwab hands back the whole session in one
call, so a run at any minute writes every completed minute it has not seen —
a gap left by a stuck scanner, a lapsed login or a dead machine is filled by
the next run that succeeds, and a past session can be filled by hand with
--day. Bars stored on diary rows only exist for minutes when a scan actually
ran, and the scanner's absences (8.4% of session minutes, in whole-hour blocks)
were the larger measured loss. The file is a plain minute series any consumer
can read directly; the diary keeps its one-scan-one-row shape.

HONESTY. Only COMPLETED minutes are written — a bar whose minute is still
running is a partial and never lands. The reader (sndk_read) falls back to the
2-minute spots when the file is absent or thin and LABELS which it used
(`price.extremes_from`, `context.ranges.measured_from`); absence of this file
never fabricates an extreme.

Shape (same keys as lefteye_fetcher.intraday_bars and the grader's
state/reversion/bars files, so nothing has to translate):
  {"ts": "<ISO, America/New_York>", "open", "high", "low", "close", "volume"}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_LEFT_EYE = str(_SKILL_DIR.parent / "mirai-left-eye")
if _LEFT_EYE not in sys.path:
    sys.path.insert(0, _LEFT_EYE)
import atomic_io  # noqa: E402

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc
TICKER = "SNDK"
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
BARS_PER_SESSION = 390          # 09:30-16:00 at one a minute
COMPLETE_SESSION_MIN_BARS = 300  # a prior day's file this full is trusted for
                                 # its extremes; thinner, the 2-min scans win


def _state_dir() -> Path:
    env = os.environ.get("MIRAI_STATE_DIR")
    return Path(env) if env else (_SKILL_DIR.parent.parent / "state")


def bars_dir() -> Path:
    return _state_dir() / "sndk_bars"


def bars_path(day: str) -> Path:
    return bars_dir() / f"{day}.jsonl"


def health_path() -> Path:
    return bars_dir() / "health.json"


def _parse_ts(s) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_ET)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def read_bars(day: str) -> list[dict]:
    """Every bar on disk for `day`, one per minute (a later duplicate wins),
    sorted by time. A torn line is skipped, never fatal; a missing file is
    an empty list — the reader treats both as 'no bars', never as zeros."""
    p = bars_path(day)
    if not p.exists():
        return []
    by_min: dict[str, dict] = {}
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            b = json.loads(ln)
        except ValueError:
            continue
        t = _parse_ts(b.get("ts")) if isinstance(b, dict) else None
        if t is None:
            continue
        vals = {k: _num(b.get(k)) for k in ("open", "high", "low", "close")}
        if any(v is None for v in vals.values()):
            continue
        key = t.astimezone(_ET).strftime("%Y-%m-%dT%H:%M")
        by_min[key] = {"ts": t.astimezone(_ET).isoformat(), **vals,
                       "volume": _num(b.get("volume")) or 0.0}
    return [by_min[k] for k in sorted(by_min)]


def completed(bars: list[dict], now: datetime) -> list[dict]:
    """Bars whose minute has fully elapsed. The bar for the running minute is
    a partial — its high and low are still being written — and must not land."""
    out = []
    for b in bars:
        t = _parse_ts(b.get("ts"))
        if t is not None and t + timedelta(minutes=1) <= now:
            out.append(b)
    return out


def fetch_session(day: date, ticker: str = TICKER) -> list[dict]:
    """Schwab 1-minute candles for one regular session, 09:30-16:00 ET, in the
    fetcher's own bar shape. The whole session comes back in one call, which is
    what makes a gap self-healing: every run sees every minute it lacks."""
    import lefteye_fetcher as F
    start = datetime.combine(day, SESSION_OPEN, tzinfo=_ET)
    end = datetime.combine(day, SESSION_CLOSE, tzinfo=_ET) + timedelta(minutes=1)
    resp = F._client().get_price_history_every_minute(
        symbol=F._schwab_symbol(ticker),
        start_datetime=start.astimezone(_UTC),
        end_datetime=end.astimezone(_UTC),
        need_extended_hours_data=False,
    )
    resp.raise_for_status()
    candles = (resp.json() or {}).get("candles") or []
    bars = [b for c in candles if (b := F._candle_to_bar(c)) is not None]
    # RTH only, by the clock and not by trust in the flag
    lo, hi = start, datetime.combine(day, SESSION_CLOSE, tzinfo=_ET)
    return [b for b in bars if (t := _parse_ts(b["ts"])) is not None and lo <= t < hi]


def write_day(day: str, bars: list[dict], now: datetime) -> dict:
    """Append every completed bar not already on disk. Idempotent: a re-run
    with the same session appends nothing. Returns a summary and writes it to
    health.json so a watcher can see the sidecar breathing."""
    have = {b["ts"][:16] for b in read_bars(day)}
    fresh = []
    for b in completed(bars, now):
        t = _parse_ts(b.get("ts"))
        if t is None:
            continue
        key = t.astimezone(_ET).isoformat()[:16]
        if key in have:
            continue
        have.add(key)
        fresh.append({"ts": t.astimezone(_ET).isoformat(),
                      "open": b["open"], "high": b["high"], "low": b["low"],
                      "close": b["close"], "volume": b.get("volume") or 0.0})
    fresh.sort(key=lambda b: b["ts"])
    for b in fresh:
        atomic_io.append_jsonl(bars_path(day), b)
    on_disk = read_bars(day)
    summary = {"ts": now.isoformat(), "day": day, "appended": len(fresh),
               "bars_on_disk": len(on_disk),
               "last_bar_at": on_disk[-1]["ts"] if on_disk else None,
               "source": "schwab_price_history_1min"}
    try:
        atomic_io.write_json_atomic(health_path(), summary)
    except OSError:
        pass
    return summary


def run(day: Optional[str] = None, now: Optional[datetime] = None,
        ticker: str = TICKER) -> int:
    now = now or datetime.now(_ET)
    d = date.fromisoformat(day) if day else now.date()
    if d.weekday() > 4:
        print(f"sndk-bars :: {d} is a weekend — nothing to fetch")
        return 0
    try:
        bars = fetch_session(d, ticker)
    except Exception as e:                      # a failed fetch is a skipped run, never a crash loop
        print(f"sndk-bars :: fetch failed for {d}: {e!r}")
        try:
            atomic_io.write_json_atomic(health_path(), {
                "ts": now.isoformat(), "day": d.isoformat(), "appended": 0,
                "error": repr(e)[:200], "source": "schwab_price_history_1min"})
        except OSError:
            pass
        return 1
    s = write_day(d.isoformat(), bars, now)
    print(f"sndk-bars :: {s['day']} +{s['appended']} bars, {s['bars_on_disk']} on disk, "
          f"last {s['last_bar_at']}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SNDK minute-bar sidecar")
    ap.add_argument("--day", help="YYYY-MM-DD session to fetch (default today); "
                                  "any past session backfills")
    ap.add_argument("--ticker", default=TICKER)
    a = ap.parse_args()
    sys.exit(run(a.day, ticker=a.ticker))
