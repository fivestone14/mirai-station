"""One moment of SPX as the labeller may see it: the diary rows, the minute bars, the market context and
the options tape, loaded point in time into a Scene. The labels themselves are built by the families
under ``labels/`` (labels.registry.build_labels).

* Read-only. Rows come from the left-eye scanner's diary ``state/reversion/{day}.jsonl``
  (cut down by row_adapter), bars from ``state/reversion/bars/{day}-SPX.json`` for past
  sessions and ``state/spx_jev/bars/{day}.jsonl`` for today (bars.py), the market around
  SPX from ``state/spx_jev/context/`` (market_context.py), the signed 0DTE options tape from
  the lob-flow collector's ``state/lob_flow/agg/{day}.jsonl`` and the SPY volume at wall touches
  from the row's ``siege`` block. Nothing is written here.
* Point in time. ``now`` is the row's own timestamp, not the wall clock. Only
  bars that finished before ``now`` count, so a replay never sees the
  unfinished minute, and prior sessions are only days before ``day``.
"""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .labels.measures import is_num
from .labels.rulers import tape_unit
from .row_adapter import SYMBOL, labeller_row
from .sessions import session_close, session_open

DEFAULT_STATE_DIR = Path.home() / ".claude" / "plugins" / "mirai-station" / "state"
ROWS_SUBDIR = Path("reversion")                    # the left-eye scanner's SPX diary
SESSION_BARS_SUBDIR = Path("reversion") / "bars"   # a finished session's bars, saved after the close
LIVE_BARS_SUBDIR = Path("spx_jev") / "bars"        # today's bars as they finish (bars.py)
CONTEXT_SUBDIR = Path("spx_jev") / "context"       # the market around SPX (market_context.py)
OPTIONS_TAPE_SUBDIR = Path("lob_flow") / "agg"     # the lob-flow collector's per-minute record (skills/lob-flow)
ET = ZoneInfo("America/New_York")

HORIZON = "the next 30 minutes"

# Baselines.
MAX_BASELINE_SESSIONS = 20
MIN_BARS_FOR_A_SESSION = 300
# The 0DTE options tape (options.aggressor_side): the collector signs the last 15 minutes of trades
# (lob_flow.daemon.TAPE_WINDOW_MIN) and writes a line a minute, so an older newest line means it stopped.
OPTIONS_TAPE_WINDOW_MIN = 15
OPTIONS_TAPE_MAX_AGE_MIN = 3


# ----------------------------------------------------------------------------- loading

def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def load_rows(state_dir: Path, day: str) -> list[dict]:
    """The day's SPX diary rows as the labeller reads them (row_adapter.labeller_row), oldest first."""
    rows = [r for raw in load_jsonl(Path(state_dir) / ROWS_SUBDIR / f"{day}.jsonl") if (r := labeller_row(raw))]
    rows.sort(key=lambda r: r["ts"])
    return rows


def _usable_bars(raw: Any) -> list[dict]:
    bars = [b for b in raw if isinstance(b, dict) and isinstance(b.get("ts"), str)
            and all(is_num(b.get(k)) for k in ("open", "high", "low", "close"))] if isinstance(raw, list) else []
    bars.sort(key=lambda b: b["ts"])
    return bars


def load_bars(state_dir: Path, day: str) -> list[dict]:
    """The day's minute bars: the finished session's saved file when it exists (written after the
    close, a full day), else today's live file (bars.py), which grows a minute at a time."""
    session = Path(state_dir) / SESSION_BARS_SUBDIR / f"{day}-{SYMBOL}.json"
    if session.exists():
        try:
            return _usable_bars(json.loads(session.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return _usable_bars(load_jsonl(Path(state_dir) / LIVE_BARS_SUBDIR / f"{day}.jsonl"))


def row_days(state_dir: Path) -> list[str]:
    return sorted(p.stem for p in (Path(state_dir) / ROWS_SUBDIR).glob("????-??-??.jsonl"))


def bar_days(state_dir: Path) -> list[str]:
    session = {p.name[:10] for p in (Path(state_dir) / SESSION_BARS_SUBDIR).glob(f"????-??-??-{SYMBOL}.json")}
    live = {p.stem for p in (Path(state_dir) / LIVE_BARS_SUBDIR).glob("????-??-??.jsonl")}
    return sorted(session | live)


def prior_bar_days(state_dir: Path, day: str, limit: int = MAX_BASELINE_SESSIONS) -> dict[str, list[dict]]:
    """The most recent full sessions of bars strictly before ``day``, newest first."""
    out: dict[str, list[dict]] = {}
    for d in reversed(bar_days(state_dir)):
        if d >= day:
            continue
        bars = load_bars(state_dir, d)
        if len(bars) >= MIN_BARS_FOR_A_SESSION:
            out[d] = bars
        if len(out) >= limit:
            break
    return out


@dataclass
class MarketContext:
    """The market around SPX for one day (market_context.py's files), per symbol, keyed by when each
    value became known: a bar once its minute finished, a quote once its snapshot was taken."""
    known: dict[str, list[tuple[datetime, float]]] = field(default_factory=dict)

    def last(self, symbol: str, now: datetime) -> float | None:
        """The newest value known at or before ``now``."""
        pts = self.known.get(symbol) or []
        k = bisect.bisect_right([t for t, _ in pts], now)
        return pts[k - 1][1] if k else None

    def first(self, symbol: str) -> float | None:
        """The day's first value: where the symbol stood at the open."""
        pts = self.known.get(symbol) or []
        return pts[0][1] if pts else None

    def between(self, symbol: str, start: datetime, end: datetime) -> list[float]:
        """Values that became known after ``start`` and at or before ``end``."""
        return [v for t, v in self.known.get(symbol) or [] if start < t <= end]

    def at(self, now: datetime) -> dict[str, dict]:
        """Every symbol's newest value known at ``now`` and when it became known: what a read used."""
        out = {}
        for symbol, pts in self.known.items():
            k = bisect.bisect_right([t for t, _ in pts], now)
            if k:
                out[symbol] = {"value": pts[k - 1][1], "known_at": pts[k - 1][0].isoformat()}
        return out


def load_market_context(state_dir: Path, day: str) -> MarketContext | None:
    """The day's live snapshots and backfilled bars as one MarketContext, or None when neither exists.
    A quote of zero is an empty shell (the breadth symbols' quotes) and is not a value."""
    folder = Path(state_dir) / CONTEXT_SUBDIR
    snapshots, backfill = load_jsonl(folder / f"{day}.jsonl"), load_jsonl(folder / "bars" / f"{day}.jsonl")
    if not snapshots and not backfill:
        return None
    known: dict[str, dict[datetime, float]] = {}
    for line in backfill + snapshots:
        for symbol, bar in (line.get("bars") or {}).items():
            if isinstance(bar, dict) and isinstance(bar.get("ts"), str) and is_num(bar.get("close")):
                known.setdefault(symbol, {})[parse_ts(bar["ts"]) + timedelta(minutes=1)] = float(bar["close"])
    for line in snapshots:
        if not isinstance(line.get("ts"), str):
            continue
        taken = parse_ts(line["ts"])
        for symbol, q in (line.get("quotes") or {}).items():
            if isinstance(q, dict) and is_num(q.get("last")) and q["last"] != 0:
                known.setdefault(symbol, {})[taken] = float(q["last"])
    return MarketContext({s: sorted(pts.items()) for s, pts in known.items()})


@dataclass
class OptionsTape:
    """The lob-flow collector's signed 0DTE options tape, per day: ``[(written at, tilt, determinate
    share)]`` oldest first. The tilt runs from -1 (buying puts and selling calls) to +1 (buying calls
    and selling puts), each trade weighted by its delta times its contracts; the determinate share is
    the part of that flow whose side could be told from where it printed inside the quote."""
    days: dict[str, list[tuple[datetime, float, float]]] = field(default_factory=dict)

    def at(self, t: datetime) -> tuple[float, float] | None:
        """``(tilt, determinate share)`` of the newest line written at or before ``t`` on ``t``'s day,
        or None when there is none within OPTIONS_TAPE_MAX_AGE_MIN: the collector was not running."""
        pts = self.days.get(t.astimezone(ET).date().isoformat()) or []
        k = bisect.bisect_right([w for w, _, _ in pts], t)
        if not k or t - pts[k - 1][0] > timedelta(minutes=OPTIONS_TAPE_MAX_AGE_MIN):
            return None
        return pts[k - 1][1], pts[k - 1][2]


def load_options_tape(state_dir: Path, days: list[str]) -> OptionsTape | None:
    """The lob-flow collector's tape readings for ``days``, or None when none of them has any. A line
    that signed no trade (no determinate share) is not a reading."""
    out: dict[str, list[tuple[datetime, float, float]]] = {}
    for d in days:
        pts = []
        for line in load_jsonl(Path(state_dir) / OPTIONS_TAPE_SUBDIR / f"{d}.jsonl"):
            snap = line.get("snapshot") if line.get("engine") == "lob_flow" else None
            if isinstance(snap, dict) and isinstance(line.get("ts"), str) and is_num(snap.get("tilt")) \
                    and is_num(snap.get("determinate_share")) and snap["determinate_share"] > 0:
                pts.append((parse_ts(line["ts"]), float(snap["tilt"]), float(snap["determinate_share"])))
        if pts:
            out[d] = sorted(pts)
    return OptionsTape(out) if out else None


# ----------------------------------------------------------------------------- scene

@dataclass
class Scene:
    """Everything the builder is allowed to see at one moment."""
    row: dict
    rows_today: list[dict]                 # rows up to and including ``row``
    bars: list[dict]                       # today's bars that finished before ``now``
    prior_bars: dict[str, list[dict]]      # full prior sessions, newest first
    now: datetime
    sigma: float
    market: MarketContext | None = None    # the market around SPX today; read point in time through ``now``
    options_tape: OptionsTape | None = None   # the signed 0DTE options tape today and on the prior sessions
    bar_clock: bool = False                # the read is stamped at a bar close (the tape lane): the stretch labels are written
    last_read: datetime | None = None      # the lane's previous read today, set by the service; None on the day's first read
    unit: dict | None = None               # the tape unit for this read (tape_unit), only on the bar clock
    horizon: str = HORIZON                 # the words context.horizon carries: the lane's sum horizon

    @property
    def spot(self) -> float:
        return float(self.row["spot"])

    @property
    def session_open(self) -> datetime:
        return session_open(self.now)

    @property
    def session_close(self) -> datetime:
        """The day's real close: 13:00 on a half day."""
        return session_close(self.now)

    @property
    def minutes_since_open(self) -> float:
        return (self.now - self.session_open).total_seconds() / 60.0

    @property
    def minutes_to_close(self) -> float:
        return (self.session_close - self.now).total_seconds() / 60.0


# A read on the bar clock takes from the diary row only what the tape cannot give: the walls, vwap
# and the options book (sigma comes pinned from the day's first row). Its time and spot are the bar's.
BAR_CLOCK_ROW_KEYS = ("call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor", "vwap", "atm_iv", "vix_ts",
                      "adaptive_em", "dex_views", "profile_ladder", "gex_views", "dated_gex", "siege")


def bar_clock_row(rows: list[dict], bars: list[dict], cutoff: datetime | None = None) -> dict:
    """The read stamped at the newest finished bar (by ``cutoff``, else the newest on file): its close
    time is the row's time and its close the spot, so the read stands where the tape stands rather
    than where the scanner last looked. Sigma is the day's first row's, so a tape unit measured in
    sigma means the same at 10:30 as at 09:35; the rest comes from the newest diary row."""
    one = timedelta(minutes=1)
    done = [b for b in bars if cutoff is None or parse_ts(b["ts"]) + one <= cutoff]
    if not done:
        raise ValueError("no finished bar to stamp the read on")
    last, src = done[-1], rows[-1]
    row = {"ts": (parse_ts(last["ts"]) + one).isoformat(), "spot": float(last["close"]), "sigma": rows[0].get("sigma")}
    row.update({k: src[k] for k in BAR_CLOCK_ROW_KEYS if k in src})
    return row


def make_scene(state_dir: Path | str = DEFAULT_STATE_DIR, day: str | None = None,
               at: time | None = None, bar_clock: bool = False, horizon: str = HORIZON) -> Scene:
    """One moment of the SPX diary: the newest row of ``day`` (at or before ``at``), or with
    ``bar_clock`` the newest finished bar with the row's walls and book beside it (bar_clock_row),
    carrying the tape unit for the read (tape_unit). ``horizon`` is what context.horizon says."""
    state_dir = Path(state_dir)
    if day is None:
        days = row_days(state_dir)
        if not days:
            raise ValueError(f"no SPX diary rows under {state_dir / ROWS_SUBDIR}")
        day = days[-1]
    rows = load_rows(state_dir, day)
    if not rows:
        raise ValueError(f"no usable SPX diary rows for {day}")
    cutoff = None
    if at is not None:
        tz = parse_ts(rows[0]["ts"]).tzinfo
        cutoff = datetime.combine(date.fromisoformat(day), at, tzinfo=tz)
        rows = [r for r in rows if parse_ts(r["ts"]) <= cutoff]
        if not rows:
            raise ValueError(f"no SPX diary row at or before {at} on {day}")
    all_bars = load_bars(state_dir, day)
    if bar_clock:
        rows = rows + [bar_clock_row(rows, all_bars, cutoff)]
    row = rows[-1]
    now = parse_ts(row["ts"])
    sigma = row.get("sigma")
    if not is_num(sigma) or sigma <= 0:
        raise ValueError(f"row at {row['ts']} carries no sigma ruler")
    bars = [b for b in all_bars if parse_ts(b["ts"]) + timedelta(minutes=1) <= now]
    prior = prior_bar_days(state_dir, day)
    return Scene(row=row, rows_today=rows, bars=bars, prior_bars=prior, now=now, sigma=float(sigma),
                 market=load_market_context(state_dir, day), options_tape=load_options_tape(state_dir, [day, *prior]),
                 bar_clock=bar_clock,
                 unit=tape_unit(bars, float(sigma), now, prior) if bar_clock else None, horizon=horizon)
