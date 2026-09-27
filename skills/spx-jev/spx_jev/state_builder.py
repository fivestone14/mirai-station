"""Turn the SPX diary rows, minute bars and market context into the plain-English labels JEV reads.

JEV reads words and cannot compare numbers, so every comparison happens here and
is written out as a sentence with the threshold in it. The rules:

* Read-only. Rows come from the left-eye scanner's diary ``state/reversion/{day}.jsonl``
  (cut down by row_adapter), bars from ``state/reversion/bars/{day}-SPX.json`` for past
  sessions and ``state/spx_jev/bars/{day}.jsonl`` for today (bars.py), the market around
  SPX from ``state/spx_jev/context/`` (market_context.py). Nothing is written here.
* Omit, never null. A label that cannot be measured is left out and the reason
  is recorded in ``omitted``; the request packer then skips every question
  that needs it.
* Point in time. ``now`` is the row's own timestamp, not the wall clock. Only
  bars that finished before ``now`` count, so a replay never sees the
  unfinished minute, and prior sessions are only days before ``day``.
* No prices in labels. Distances are in sigma (today's expected move for the
  index), shares are percentages, and strikes are described, never named. The
  tape lane's stretch labels are the exception: their unit is index points.
* Every threshold lives in cuts.py: measured on SPX, or declared where it is a
  split or ratio whose meaning is its own words ("twice a typical minute").
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .cuts import (BUSIEST_STRIKE_SHARE, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, GRIP_CONCENTRATED_SHARE, GRIP_SPREAD_SHARE,
                   IV_FLAT_BAND_PTS, MINUTE_WIDTH_CUT, MOVE_RULE_SIGMA, OUTLIER_DAY_SIGMA, PACE_BIGGER, PACE_SMALLER,
                   PATH_CHOPPY, PATH_ORDERLY, PAUSE_BRIEF_MIN, PAUSE_LONG_MIN, PULLBACK_SHARE, REALIZED_QUIET_RATIO,
                   REALIZED_WILD_RATIO, RSI_OVERBOUGHT, RSI_OVERSOLD, RULER_FLOOR_SIGMA, RULER_HOLD_SIGMA, SHAPE_CUT_SIGMA,
                   TAPE_BIG_UNITS, TAPE_FLAT_UNITS, TURNOVER_HIGH, TURNOVER_LOW, VIX_CURVE_FLAT, WALL_NEAR_SIGMA,
                   WALL_THICK_SHARE, WALL_THIN_SHARE, ZERO_DTE_LAST_HOUR_MIN)
from .expiry import expiry_kinds, next_monthly, todays_settle, trading_days_between
from .row_adapter import SYMBOL, labeller_row
from .sessions import session_close, session_minutes, session_open

DEFAULT_STATE_DIR = Path.home() / ".claude" / "plugins" / "mirai-station" / "state"
ROWS_SUBDIR = Path("reversion")                    # the left-eye scanner's SPX diary
SESSION_BARS_SUBDIR = Path("reversion") / "bars"   # a finished session's bars, saved after the close
LIVE_BARS_SUBDIR = Path("spx_jev") / "bars"        # today's bars as they finish (bars.py)
CONTEXT_SUBDIR = Path("spx_jev") / "context"       # the market around SPX (market_context.py)

HORIZON = "the next 30 minutes"
UNITS = ("all distances are in sigma, today's expected move for the S&P 500 index; "
         "a plus sign means above price and a minus sign means below price. "
         "Options weight means the hedging exposure of dealers, the market makers on the other side of the options, at each strike; "
         "a wall is a strike where that weight piles up, the nearest one standing in today's 0DTE book, else in the 1-to-7-day book; "
         "0DTE means the options that expire today; the opening box is the first half hour's price range; "
         f"RSI is a 0 to 100 gauge of overbought (above {RSI_OVERBOUGHT}) or oversold (below {RSI_OVERSOLD}); "
         "the NYSE tick is how many NYSE stocks last traded up minus how many last traded down")
# Added to the gloss on the tape lane, where the stretch labels are sized by the tape unit.
TAPE_UNITS = ("; a tape unit is the middle of the last three 5-minute price ranges, in index points, "
              "measured afresh at every read, and the labels that use it state it")

RSI_PERIOD = 14                 # Wilder's RSI over 14 closes
# Baselines.
MAX_BASELINE_SESSIONS = 20
MIN_BARS_FOR_A_SESSION = 300
MIN_RANK_SESSIONS = 5           # prior sessions a rank against the same minutes needs
RANGE_PRIOR_SESSIONS = 5        # today's range against the prior sessions at the same time of day
MIN_RANGE_SESSIONS = 3
# The opening box is the first 30 minutes; a break is past the move bar, two typical minutes
# (twice the median 1-minute range of the last 30 bars).
OPENING_BOX_MIN = 30
MOVE_BAR_MINUTES = 2
FULL_SESSION_MIN = 390          # a full session's minutes: sigma is a full day's expected move
# Realized against priced movement over the last 30 minutes: the realized path from the 1-minute
# closes, against sigma scaled to 30 minutes by the square root of time.
REALIZED_WINDOW_MIN = 30
REALIZED_MIN_BARS = 25
# The market context labels (breadth.*): a majority of the last 30 minutes' tick closes, and of the sectors.
TICK_WINDOW_MIN = 30
MIN_TICK_MINUTES = 20
MIN_SECTORS = 8
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLC", "XLP", "XLU", "XLB", "XLRE")


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


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_rows(state_dir: Path, day: str) -> list[dict]:
    """The day's SPX diary rows as the labeller reads them (row_adapter.labeller_row), oldest first."""
    rows = [r for raw in load_jsonl(Path(state_dir) / ROWS_SUBDIR / f"{day}.jsonl") if (r := labeller_row(raw))]
    rows.sort(key=lambda r: r["ts"])
    return rows


def _usable_bars(raw: Any) -> list[dict]:
    bars = [b for b in raw if isinstance(b, dict) and isinstance(b.get("ts"), str)
            and all(_is_num(b.get(k)) for k in ("open", "high", "low", "close"))] if isinstance(raw, list) else []
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
            if isinstance(bar, dict) and isinstance(bar.get("ts"), str) and _is_num(bar.get("close")):
                known.setdefault(symbol, {})[parse_ts(bar["ts"]) + timedelta(minutes=1)] = float(bar["close"])
    for line in snapshots:
        if not isinstance(line.get("ts"), str):
            continue
        taken = parse_ts(line["ts"])
        for symbol, q in (line.get("quotes") or {}).items():
            if isinstance(q, dict) and _is_num(q.get("last")) and q["last"] != 0:
                known.setdefault(symbol, {})[taken] = float(q["last"])
    return MarketContext({s: sorted(pts.items()) for s, pts in known.items()})


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
    bar_clock: bool = False                # the read is stamped at a bar close (the tape lane): the stretch labels are written
    last_read: datetime | None = None      # the lane's previous read today, set by the service; None on the day's first read
    unit: dict | None = None               # the tape unit for this read (tape_unit), only on the bar clock
    horizon: str = HORIZON                 # the words context.horizon carries: the lane's sum horizon


# A read on the bar clock takes from the diary row only what the tape cannot give: the walls, vwap
# and the options book (sigma comes pinned from the day's first row). Its time and spot are the bar's.
BAR_CLOCK_ROW_KEYS = ("call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor", "vwap", "atm_iv", "vix_ts",
                      "adaptive_em", "dex_views", "profile_ladder", "gex_views", "dated_gex")


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
    if not _is_num(sigma) or sigma <= 0:
        raise ValueError(f"row at {row['ts']} carries no sigma ruler")
    bars = [b for b in all_bars if parse_ts(b["ts"]) + timedelta(minutes=1) <= now]
    prior = prior_bar_days(state_dir, day)
    return Scene(row=row, rows_today=rows, bars=bars, prior_bars=prior, now=now, sigma=float(sigma),
                 market=load_market_context(state_dir, day), bar_clock=bar_clock,
                 unit=tape_unit(bars, float(sigma), now, prior) if bar_clock else None, horizon=horizon)


# ----------------------------------------------------------------------------- helpers

def _t(b: dict) -> datetime:
    return parse_ts(b["ts"])


def _mod(t: datetime) -> int:
    """Minute of day."""
    return t.hour * 60 + t.minute


def bars_between(bars: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Bars that started in [start, end)."""
    return [b for b in bars if start <= _t(b) < end]


def bars_finished_between(bars: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Bars that finished in (start, end], so a window never leans on an unfinished minute."""
    one = timedelta(minutes=1)
    return [b for b in bars if start < _t(b) + one <= end]


def close_at(bars: list[dict], t: datetime) -> float | None:
    """Close of the last bar that had finished by ``t``: where price stood at that moment."""
    one = timedelta(minutes=1)
    cands = [b for b in bars if _t(b) + one <= t]
    return float(cands[-1]["close"]) if cands else None


def slot(bars: list[dict], start_min: int, end_min: int) -> list[dict]:
    """Bars whose minute of day falls in [start_min, end_min)."""
    return [b for b in bars if start_min <= _mod(_t(b)) < end_min]


# The tape unit: the median high-to-low range of the last three finished 5-minute slices. Held at
# RULER_HOLD_SIGMA until 09:45, when three slices first exist; floored at RULER_FLOOR_SIGMA so a dead
# tape still has a unit; never capped, so a wild open is measured as wild.
RULER_SLICE_MIN = 5
RULER_SLICES = 3
RULER_HOLD_UNTIL = time(9, 45)


def ruler(bars: list[dict], sigma: float, now: datetime) -> dict | None:
    """The tape unit for a read at ``now``: ``unit_points``, ``unit_sigma``, the ``slices_used`` and
    its ``source`` (held, tape or floor). None when the newest slice has no bars: the tape has
    stopped, and a unit from an older stretch would pass off the last move as the current one."""
    if now.time() < RULER_HOLD_UNTIL:
        return {"unit_points": round(RULER_HOLD_SIGMA * sigma, 2), "unit_sigma": RULER_HOLD_SIGMA, "slices_used": 0, "source": "held"}
    ranges = []
    for k in range(RULER_SLICES):
        end = now - timedelta(minutes=RULER_SLICE_MIN * k)
        sl = bars_finished_between(bars, end - timedelta(minutes=RULER_SLICE_MIN), end)
        if sl:
            ranges.append(max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl))
        elif k == 0:
            return None
    unit = statistics.median(ranges)
    if unit < RULER_FLOOR_SIGMA * sigma:
        return {"unit_points": round(RULER_FLOOR_SIGMA * sigma, 2), "unit_sigma": RULER_FLOOR_SIGMA, "slices_used": len(ranges), "source": "floor"}
    return {"unit_points": round(unit, 2), "unit_sigma": round(unit / sigma, 3), "slices_used": len(ranges), "source": "tape"}


def slice_range(bars: list[dict], end_min: int) -> float | None:
    """High-to-low range of the 5-minute slice ending at ``end_min`` (a minute of day); None with no bars."""
    sl = slot(bars, end_min - RULER_SLICE_MIN, end_min)
    return max(float(b["high"]) for b in sl) - min(float(b["low"]) for b in sl) if sl else None


def unit_rank(unit: dict, prior_bars: dict[str, list[dict]], now: datetime) -> dict | None:
    """The tape unit against the same minute on the prior sessions: the same three slices measured on
    each prior day's bars, placed in thirds (rank_at_slot). None while the unit is held (before 09:45
    every day's unit is the same number) and when fewer than MIN_RANK_SESSIONS prior sessions carry
    all three slices."""
    if unit.get("source") == "held":
        return None
    end_min = _mod(now)
    base = []
    for pbars in prior_bars.values():
        ranges = [slice_range(pbars, end_min - RULER_SLICE_MIN * k) for k in range(RULER_SLICES)]
        if all(r is not None for r in ranges):
            base.append(statistics.median(ranges))
    return rank_at_slot(float(unit["unit_points"]), base)


def tape_unit(bars: list[dict], sigma: float, now: datetime, prior_bars: dict[str, list[dict]]) -> dict | None:
    """The unit for a read on the tape lane: ruler() with its rank against the prior sessions at this
    minute under ``rank`` when there is one. What the record, the card and the sum's context line carry."""
    unit = ruler(bars, sigma, now)
    if unit:
        rank = unit_rank(unit, prior_bars, now)
        if rank:
            unit["rank"] = rank
    return unit


def stretch(bars: list[dict], start_min: int, end_min: int) -> tuple[float | None, list[dict]]:
    """The bars of a day in the clock window [start_min, end_min) and the close just before it: the
    anchor a move is measured from."""
    before = [b for b in bars if _mod(_t(b)) < start_min]
    return (float(before[-1]["close"]) if before else None), slot(bars, start_min, end_min)


def stretch_range(win: list[dict]) -> float:
    return max(float(b["high"]) for b in win) - min(float(b["low"]) for b in win)


def units_of(x: float, unit_points: float) -> str:
    """``x`` tape units in words, with what one unit is in points, rounded as the sum's context line rounds it."""
    return f"{x:.2f} of a tape unit ({unit_points:.1f} points)" if x <= 1 else f"{x:.2f} tape units (one is {unit_points:.1f} points)"


def sig(x: float) -> str:
    return f"{x:.2f} sigma"


def signed(x: float, nd: int = 2) -> str:
    """A signed number with no '-0.00': a tiny negative rounds to a plain +0.00."""
    r = round(x, nd) or 0.0
    return f"{r:+.{nd}f}"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def third(frac: float) -> str:
    if frac < 1 / 3:
        return "bottom"
    if frac > 2 / 3:
        return "top"
    return "middle"


def rank_at_slot(value: float, base: list[float]) -> dict | None:
    """Where ``value`` sits against the same clock window on the prior sessions (``base``, one number per
    session): the third and the count, as the label words them ("in the top third for this minute,
    higher than 15 of 20 prior sessions"). None under MIN_RANK_SESSIONS sessions: too thin to call a
    third, so the label is omitted and the packer skips its question rather than guess."""
    if len(base) < MIN_RANK_SESSIONS:
        return None
    under = sum(1 for b in base if b < value)
    return {"band": f"{third(under / len(base))} third", "higher_than": under, "of": len(base)}


def wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


# ----------------------------------------------------------------------------- builder

class _Out:
    """Collects labels by group and the reasons for anything left out.

    A labeller may also hand over the ``figure`` behind a sentence: its kind, its number, its cut and
    the verdict it landed on, so the phone can draw the fact rather than print it. JEV never sees a
    figure; only the sentence is sent.
    """

    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.omitted: dict[str, str] = {}
        self.figures: dict[str, dict] = {}

    def put(self, group: str, key: str, value: Any, *, figure: dict | None = None) -> None:
        self.state.setdefault(group, {})[key] = value
        if figure is not None:
            self.figures[f"{group}.{key}"] = figure

    def skip(self, group: str, key: str, reason: str) -> None:
        self.omitted[f"{group}.{key}"] = reason


def build_state(scene: Scene, figures: dict | None = None) -> tuple[dict, dict]:
    """Return ``(state, omitted)``. ``state`` holds only what could be measured. Pass a dict as
    ``figures`` to have it filled with the figures behind the labels that hand one over (_Out.put)."""
    out = _Out()
    b = _Builder(scene, out)
    for step in (b.context, b.price, b.range, b.iv, b.gex, b.options, b.expiry, b.momentum, b.breadth, b.tape):
        step()
    if figures is not None:
        figures.update(out.figures)
    return out.state, out.omitted


class _Builder:
    def __init__(self, scene: Scene, out: _Out) -> None:
        self.s = scene
        self.o = out
        self.row = scene.row
        self.bars = scene.bars
        self.now = scene.now
        self.sigma = scene.sigma
        self.spot = float(scene.row["spot"])
        self.open_t = session_open(scene.now)
        self.close_t = session_close(scene.now)
        self.since_open = (scene.now - self.open_t).total_seconds() / 60.0
        self.to_close = (self.close_t - scene.now).total_seconds() / 60.0
        self.move30: float | None = None      # signed 30-minute move in sigma, set by price()

    # ---- shared measures
    def _move_bar(self) -> float | None:
        """Two typical minutes: twice the median 1-minute range of the last 30 bars."""
        recent = self.bars[-30:]
        if len(recent) < 10:
            return None
        return MOVE_BAR_MINUTES * statistics.median(float(x["high"]) - float(x["low"]) for x in recent)

    def _session(self, back: int) -> list[dict] | None:
        """Prior session bars, ``back`` sessions ago (1 = yesterday)."""
        days = list(self.s.prior_bars.values())
        return days[back - 1] if len(days) >= back else None

    @staticmethod
    def _hlc(bars: list[dict]) -> tuple[float, float, float]:
        return (max(float(x["high"]) for x in bars), min(float(x["low"]) for x in bars), float(bars[-1]["close"]))

    def _day_high_low(self) -> tuple[float, float]:
        return (max(max(float(x["high"]) for x in self.bars), self.spot), min(min(float(x["low"]) for x in self.bars), self.spot))

    def _walls(self) -> list[tuple[str, float, str]]:
        """``(name, signed distance in sigma, row key)`` of each wall the row names."""
        return [(name, (float(self.row[key]) - self.spot) / self.sigma, key)
                for name, key in (("call wall", "call_wall"), ("put wall", "put_wall")) if _is_num(self.row.get(key))]

    # ---- context
    def context(self) -> None:
        o = self.o
        o.put("context", "symbol", SYMBOL)
        o.put("context", "units", UNITS + TAPE_UNITS if self.s.bar_clock else UNITS)
        o.put("context", "horizon", self.s.horizon)

        frac = max(0.0, min(1.0, self.since_open / session_minutes(self.now)))
        words = ["first", "second", "third", "fourth", "last"][min(4, int(frac * 5))]
        m, k = max(round(self.since_open), 0), max(round(self.to_close), 0)
        if self.since_open < 60:
            phase = f"the first hour, {plural(m, 'minute')} after the open"
        elif self.to_close <= 60:
            phase = f"the last hour, {plural(k, 'minute')} before the close"
        else:
            phase = f"the middle of the session, {plural(m, 'minute')} after the open and {plural(k, 'minute')} before the close"
        o.put("context", "session_progress", f"{pct(frac)} of the session has passed, the {words} fifth; {phase}")

        if len(self.bars) < 11:
            o.skip("context", "time_since_last_move", "needs 10 minutes of finished bars")
            return
        closes = [float(x["close"]) for x in self.bars]
        last_idx = None
        for i in range(10, len(closes)):
            if abs(closes[i] - closes[i - 10]) / self.sigma >= MOVE_RULE_SIGMA:
                last_idx = i
        if last_idx is None:
            o.put("context", "time_since_last_move", f"price has made no real move of {MOVE_RULE_SIGMA} sigma within 10 minutes at any point today, so the last real move is over 60 minutes ago or never")
            return
        ago = (self.now - (_t(self.bars[last_idx]) + timedelta(minutes=1))).total_seconds() / 60.0
        band = "under 10 minutes ago" if ago < 10 else "between 10 and 60 minutes ago" if ago <= 60 else "over 60 minutes ago"
        o.put("context", "time_since_last_move", f"price last made a real move of {MOVE_RULE_SIGMA} sigma within 10 minutes {plural(round(ago), 'minute')} ago, {band}")

    # ---- price
    def price(self) -> None:
        o = self.o
        if self.since_open < 30:
            o.skip("price", "recent_move", "needs 30 minutes of session")
        elif (ref := close_at(self.bars, self.now - timedelta(minutes=30))) is None:
            o.skip("price", "recent_move", "no finished bar 30 minutes ago")
        else:
            d = (self.spot - ref) / self.sigma
            self.move30 = d
            verdict = "going_nowhere" if abs(d) < MOVE_RULE_SIGMA else "rising" if d > 0 else "falling"
            fig = {"kind": "signed", "value": round(d, 3), "band": MOVE_RULE_SIGMA, "unit": "sigma", "verdict": verdict}
            if verdict == "going_nowhere":
                text = f"over the last 30 minutes price stayed within {MOVE_RULE_SIGMA} sigma of where it was, moving {signed(d)} sigma"
            else:
                text = f"over the last 30 minutes price {'rose' if d > 0 else 'fell'} {sig(abs(d))}, more than the {MOVE_RULE_SIGMA} sigma move rule"
            o.put("price", "recent_move", text, figure=fig)

        if not self.bars:
            o.skip("price", "day_range_position", "no finished bars yet")
        else:
            hi, lo = self._day_high_low()
            if hi - lo <= 0:
                o.skip("price", "day_range_position", "today's range is zero so far")
            else:
                pos = (self.spot - lo) / (hi - lo)
                o.put("price", "day_range_position",
                      f"price is in the {third(pos)} third of today's range so far, {pct(pos)} of the way up from the low to the high")

        vwap = self.row.get("vwap")
        if not _is_num(vwap) or vwap <= 0:
            o.skip("price", "vs_vwap", "row carries no vwap")
        else:
            d = (self.spot - float(vwap)) / self.sigma
            verdict = "at_it" if abs(d) < MOVE_RULE_SIGMA else "above" if d > 0 else "below"
            fig = {"kind": "signed", "value": round(d, 3), "band": MOVE_RULE_SIGMA, "unit": "sigma", "verdict": verdict}
            if verdict == "at_it":
                text = f"price is within {MOVE_RULE_SIGMA} sigma of the day's volume-weighted average price, {signed(d)} sigma from it"
            else:
                text = f"price is {sig(abs(d))} {verdict} the day's volume-weighted average price, more than the {MOVE_RULE_SIGMA} sigma move rule"
            o.put("price", "vs_vwap", text, figure=fig)

        pc = self.row.get("prior_close")
        if not _is_num(pc) or pc <= 0:
            o.skip("price", "vs_prior_close", "row carries no prior close")
        else:
            d = (self.spot - float(pc)) / self.sigma
            side = "above" if d > 0 else "below"
            if abs(d) < MOVE_RULE_SIGMA:
                o.put("price", "vs_prior_close", f"price is within {MOVE_RULE_SIGMA} sigma of last night's close, {signed(d)} sigma from it, an ordinary day so far")
            elif abs(d) >= OUTLIER_DAY_SIGMA:
                o.put("price", "vs_prior_close", f"price is {sig(abs(d))} {side} last night's close, more than {OUTLIER_DAY_SIGMA} sigma, an outlier day")
            else:
                o.put("price", "vs_prior_close", f"price is {sig(abs(d))} {side} last night's close, within {OUTLIER_DAY_SIGMA} sigma, an ordinary day so far")

        if len(self.bars) < 15:
            o.skip("price", "minute_width", "needs 15 finished bars")
        else:
            last = float(self.bars[-1]["high"]) - float(self.bars[-1]["low"])
            med = statistics.median(float(x["high"]) - float(x["low"]) for x in self.bars)
            if med <= 0:
                o.skip("price", "minute_width", "today's typical minute has no range")
            else:
                r = last / med
                o.put("price", "minute_width", f"this minute's range is {r:.1f} times a typical minute today, {'wider than' if r > MINUTE_WIDTH_CUT else 'within'} the {MINUTE_WIDTH_CUT:g}-times cut")

        for back, name, key in ((1, "yesterday", "vs_yesterday"), (2, "the session before yesterday", "vs_day_before")):
            bars = self._session(back)
            if not bars:
                o.skip("price", key, f"no stored session {back} back")
                continue
            hi, lo, cl = self._hlc(bars)
            dh, dl, dc = (self.spot - hi) / self.sigma, (self.spot - lo) / self.sigma, (self.spot - cl) / self.sigma
            if dh > MOVE_RULE_SIGMA:
                pos = f"price is {sig(dh)} above {name}'s high, more than the {MOVE_RULE_SIGMA} sigma move rule"
            elif abs(dh) <= MOVE_RULE_SIGMA:
                pos = f"price is within {MOVE_RULE_SIGMA} sigma of {name}'s high, at it"
            elif dl < -MOVE_RULE_SIGMA:
                pos = f"price is {sig(-dl)} below {name}'s low, more than the {MOVE_RULE_SIGMA} sigma move rule"
            elif abs(dl) <= MOVE_RULE_SIGMA:
                pos = f"price is within {MOVE_RULE_SIGMA} sigma of {name}'s low, at it"
            else:
                pos = f"price is inside {name}'s range, {sig(-dh)} below its high and {sig(dl)} above its low"
            # yesterday's close already has its own label, price.vs_prior_close; only the day before carries its close here
            close = f"; it is {sig(abs(dc))} {'above' if dc >= 0 else 'below'} {name}'s close" if back == 2 else ""
            o.put("price", key, pos + close)

        week = list(self.s.prior_bars.values())[:RANGE_PRIOR_SESSIONS]
        if len(week) < MIN_RANGE_SESSIONS:
            o.skip("price", "multi_day_position", f"needs {MIN_RANGE_SESSIONS} prior sessions, have {len(week)}")
            return
        whi = max(max(float(x["high"]) for x in b) for b in week)
        wlo = min(min(float(x["low"]) for x in b) for b in week)
        n = len(week)
        if whi - wlo <= 0:
            o.skip("price", "multi_day_position", "the prior sessions' range is zero")
            return
        dh, dl = (self.spot - whi) / self.sigma, (self.spot - wlo) / self.sigma
        edges = f"{sig(abs(dh))} {'above' if dh > 0 else 'below'} that high and {sig(abs(dl))} {'above' if dl > 0 else 'below'} their low"
        if self.spot > whi:
            o.put("price", "multi_day_position", f"price is above the last {n} sessions' high, in new ground, {edges}")
        elif self.spot < wlo:
            o.put("price", "multi_day_position", f"price is below the last {n} sessions' low, in new ground, {edges}")
        else:
            pos = (self.spot - wlo) / (whi - wlo)
            o.put("price", "multi_day_position", f"price is in the {third(pos)} third of the last {n} sessions' range, {pct(pos)} of the way up from its low to its high, {edges}")

    # ---- range
    def range(self) -> None:
        o = self.o
        opening = bars_between(self.bars, self.open_t, self.open_t + timedelta(minutes=OPENING_BOX_MIN))
        if len(opening) < OPENING_BOX_MIN:
            o.put("range", "box_status", f"the opening box is still forming, {plural(len(opening), 'minute')} of its {OPENING_BOX_MIN} are in")
        else:
            box_hi = max(float(x["high"]) for x in opening)
            box_lo = min(float(x["low"]) for x in opening)
            bar = self._move_bar()
            width = (box_hi - box_lo) / self.sigma
            if self.spot > box_hi + bar:
                status = f"price has broken above the opening box and sits {sig((self.spot - box_hi) / self.sigma)} beyond its top"
            elif self.spot < box_lo - bar:
                status = f"price has broken below the opening box and sits {sig((box_lo - self.spot) / self.sigma)} beyond its bottom"
            else:
                status = "price is inside the opening box"
            later = [x for x in self.bars if _t(x) >= self.open_t + timedelta(minutes=OPENING_BOX_MIN)]
            broke_up = any(float(x["close"]) > box_hi + bar for x in later)
            broke_dn = any(float(x["close"]) < box_lo - bar for x in later)
            history = ""
            if status.startswith("price is inside"):
                if broke_up and broke_dn:
                    history = "; earlier today it broke out both ways and came back"
                elif broke_up:
                    history = "; earlier today it broke above the box and came back inside"
                elif broke_dn:
                    history = "; earlier today it broke below the box and came back inside"
                else:
                    history = " and has stayed inside it all day"
            o.put("range", "box_status", f"{status}{history}; the box is {sig(width)} wide")

        self._today_vs_normal()
        self._prior_level_touches()
        self._session_shape()
        self._nearest_level()

    def _today_vs_normal(self) -> None:
        o = self.o
        if not self.bars:
            o.skip("range", "today_vs_normal", "no finished bars yet")
            return
        hi, lo = self._day_high_low()
        open0 = float(self.bars[0]["open"])
        if open0 <= 0:
            o.skip("range", "today_vs_normal", "no opening price")
            return
        today = (hi - lo) / open0
        cutoff = _mod(self.now)
        prior: list[float] = []
        for pbars in list(self.s.prior_bars.values())[:RANGE_PRIOR_SESSIONS]:
            same = [x for x in pbars if _mod(_t(x)) + 1 <= cutoff]
            if len(same) < 5 or float(same[0]["open"]) <= 0:
                continue
            prior.append((max(float(x["high"]) for x in same) - min(float(x["low"]) for x in same)) / float(same[0]["open"]))
        if len(prior) < MIN_RANGE_SESSIONS:
            o.skip("range", "today_vs_normal", f"needs {MIN_RANGE_SESSIONS} prior sessions of bars at this time of day, have {len(prior)}")
            return
        below = sum(1 for p in prior if p < today)
        o.put("range", "today_vs_normal",
              f"today's range so far is in the {third(below / len(prior))} third of the last {len(prior)} sessions at this time of day, larger than {below} of them")

    def _prior_level_touches(self) -> None:
        y = self._session(1)
        bar = self._move_bar()
        if not y or bar is None:
            self.o.skip("range", "prior_level_touches", "needs yesterday's bars and 10 finished bars today")
            return
        hi, lo, cl = self._hlc(y)
        counts = {name: sum(1 for x in self.bars if float(x["low"]) <= lvl + bar and float(x["high"]) >= lvl - bar)
                  for name, lvl in (("high", hi), ("low", lo), ("close", cl))}
        # a count of minutes, not of visits: a bar sitting on the level all afternoon counts every minute
        self.o.put("range", "prior_level_touches",
                   f"price has spent {plural(counts['high'], 'minute')} within reach of yesterday's high, {plural(counts['low'], 'minute')} within reach of yesterday's low and {plural(counts['close'], 'minute')} within reach of yesterday's close today, counting reach as two typical minutes of range")

    def _session_shape(self) -> None:
        o = self.o
        if self.since_open < 30 or not self.bars:
            o.skip("range", "session_shape", "needs 30 minutes of session")
            return
        open0 = float(self.bars[0]["open"])
        hi, lo = self._day_high_low()
        net = (self.spot - open0) / self.sigma
        up_ext, dn_ext = (hi - open0) / self.sigma, (open0 - lo) / self.sigma
        from_high, from_low = (hi - self.spot) / self.sigma, (self.spot - lo) / self.sigma
        cut = SHAPE_CUT_SIGMA
        if up_ext >= cut and from_high >= cut and from_high >= up_ext / 2:
            o.put("range", "session_shape", f"so far today price rose {sig(up_ext)} above the open then gave back {sig(from_high)} of it, so the session has reversed down, past the {cut} sigma cut")
        elif dn_ext >= cut and from_low >= cut and from_low >= dn_ext / 2:
            o.put("range", "session_shape", f"so far today price fell {sig(dn_ext)} below the open then recovered {sig(from_low)} of it, so the session has reversed up, past the {cut} sigma cut")
        elif net >= cut:
            o.put("range", "session_shape", f"so far today price is {sig(net)} above the open, more than the {cut} sigma cut, so the session is rising")
        elif net <= -cut:
            o.put("range", "session_shape", f"so far today price is {sig(-net)} below the open, more than the {cut} sigma cut, so the session is falling")
        else:
            o.put("range", "session_shape", f"so far today price is {sig(abs(net))} from the open, within the {cut} sigma cut, so the session is flat")

    def _nearest_level(self) -> None:
        o = self.o
        back30 = self.now - timedelta(minutes=30)
        win = bars_finished_between(self.bars, back30, self.now)
        ref = close_at(self.bars, back30)
        if self.since_open < 30 or len(win) < 20 or ref is None:
            o.skip("range", "nearest_level", "needs 30 minutes of finished bars")
            return
        levels: list[tuple[str, float]] = []
        vwap = self.row.get("vwap")
        if _is_num(vwap) and vwap > 0:
            levels.append(("the day's average price", float(vwap)))
        # the day's high and low come from before the window, so a level is never the window's own extreme
        early = bars_finished_between(self.bars, self.open_t, back30)
        if early:
            levels.append(("the day's high", max(float(x["high"]) for x in early)))
            levels.append(("the day's low", min(float(x["low"]) for x in early)))
        y = self._session(1)
        if y:
            hi, lo, cl = self._hlc(y)
            levels += [("yesterday's high", hi), ("yesterday's low", lo), ("yesterday's close", cl)]
        levels += [(f"the {name}", self.spot + d * self.sigma) for name, d, _ in self._walls()]
        if not levels:
            o.skip("range", "nearest_level", "no level to measure against")
            return
        name, lvl = min(levels, key=lambda lv: abs(lv[1] - self.spot))
        d = (self.spot - lvl) / self.sigma
        was_below, is_below = ref < lvl, self.spot < lvl
        side = "above" if is_below else "below"
        if was_below and not is_below:
            o.put("range", "nearest_level", f"price crossed {name} from below in the last 30 minutes and is {sig(d)} above it")
        elif is_below and not was_below:
            o.put("range", "nearest_level", f"price crossed {name} from above in the last 30 minutes and is {sig(-d)} below it")
        elif is_below and any(float(x["high"]) > lvl for x in win):
            o.put("range", "nearest_level", f"in the last 30 minutes price pushed above {name} and is back below it, {sig(-d)} under")
        elif not is_below and any(float(x["low"]) < lvl for x in win):
            o.put("range", "nearest_level", f"in the last 30 minutes price pushed below {name} and is back above it, {sig(d)} over")
        elif abs(d) <= MOVE_RULE_SIGMA:
            o.put("range", "nearest_level", f"the nearest level is {name}, {sig(abs(d))} {side} price, within the {MOVE_RULE_SIGMA} sigma reach, untouched in the last 30 minutes")
        elif abs(d) <= WALL_NEAR_SIGMA:
            o.put("range", "nearest_level", f"the nearest level is {name}, {sig(abs(d))} {side} price, beyond the {MOVE_RULE_SIGMA} sigma reach but within {WALL_NEAR_SIGMA} sigma, untouched in the last 30 minutes")
        else:
            o.put("range", "nearest_level", f"no level is within {WALL_NEAR_SIGMA} sigma of price; the nearest is {name}, {sig(abs(d))} {side}")

    # ---- implied volatility
    def iv(self) -> None:
        o = self.o
        iv_now = self.row.get("atm_iv")
        if not _is_num(iv_now):
            o.skip("iv", "trend_30min", "row carries no at-the-money implied volatility")
        elif self.to_close < ZERO_DTE_LAST_HOUR_MIN:
            o.skip("iv", "trend_30min", "the last hour of the 0DTE book: its implied volatility follows the expiry clock, not the market")
        else:
            target = self.now - timedelta(minutes=30)
            earlier = [r for r in self.s.rows_today if parse_ts(r["ts"]) <= target and _is_num(r.get("atm_iv"))]
            if not earlier or parse_ts(earlier[-1]["ts"]) < target - timedelta(minutes=10):
                o.skip("iv", "trend_30min", "no row with implied volatility about 30 minutes ago")
            else:
                d = (float(iv_now) - float(earlier[-1]["atm_iv"])) * 100.0
                verdict = "flat" if abs(d) <= IV_FLAT_BAND_PTS else "rising" if d > 0 else "falling"
                fig = {"kind": "signed", "value": round(d, 2), "band": IV_FLAT_BAND_PTS, "unit": "vol points", "verdict": verdict}
                if verdict == "flat":
                    text = f"over the last 30 minutes at-the-money implied volatility stayed within the {IV_FLAT_BAND_PTS:g} vol point flat band, changing {signed(d)} vol points"
                else:
                    text = f"over the last 30 minutes at-the-money implied volatility {'rose' if d > 0 else 'fell'} {abs(d):.2f} vol points, more than the {IV_FLAT_BAND_PTS:g} point flat band"
                o.put("iv", "trend_30min", text, figure=fig)

        self._realized_vs_priced()

        ruler_ = self.row.get("range_ruler") or {}
        used = ruler_.get("em_consumed")
        if not _is_num(used) and _is_num(ruler_.get("em_open")) and ruler_["em_open"] > 0 and self.bars:
            hi, lo = self._day_high_low()
            used = (hi - lo) / float(ruler_["em_open"])
        if not _is_num(used):
            o.skip("iv", "expected_move_used", "row carries no expected-move ruler")
        elif used < 0.5:
            o.put("iv", "expected_move_used", f"today's range so far has used {used:.1f} of today's expected move, under half of it")
        elif used <= 1.0:
            o.put("iv", "expected_move_used", f"today's range so far has used {used:.1f} of today's expected move, between half and all of it")
        else:
            o.put("iv", "expected_move_used", f"today's range so far has used {used:.1f} times today's expected move, more than all of it")

        ds = (self.row.get("adaptive_em") or {}).get("down_share")
        lo_cut, hi_cut = EVEN_SPLIT_LOW, EVEN_SPLIT_HIGH
        if not _is_num(ds):
            o.skip("iv", "move_sides", "row carries no expected-move split")
        elif ds > hi_cut:
            o.put("iv", "move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, skewed to the downside beyond the {pct(hi_cut)} cut")
        elif ds < lo_cut:
            o.put("iv", "move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, skewed to the upside beyond the {pct(hi_cut)} cut")
        else:
            o.put("iv", "move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, even within the {pct(lo_cut)} to {pct(hi_cut)} band")

        ratio = self.row.get("vix_ts")
        if not _is_num(ratio) or ratio <= 0:
            o.skip("iv", "term_structure", "row carries no VIX against three-month VIX")
        elif ratio >= VIX_CURVE_FLAT:
            o.put("iv", "term_structure", f"the VIX is {ratio:.2f} times the three-month VIX, at or above {VIX_CURVE_FLAT:g}: near-term fear is priced at or above three-month fear, the stressed shape")
        else:
            o.put("iv", "term_structure", f"the VIX is {ratio:.2f} times the three-month VIX, below {VIX_CURVE_FLAT:g}: near-term fear is priced under three-month fear, the calm shape")

    def _realized_vs_priced(self) -> None:
        """How much price actually moved over the last 30 minutes against the move the options
        market prices for 30 minutes. Realized is the square root of the summed squared 1-minute
        close changes, scaled to 30 minutes by the minutes the closes actually span (a change across
        a missing minute already carries that minute's movement, so a gap is neither quiet nor
        counted twice); priced is sigma times the square root of 30 over a full session's minutes."""
        o = self.o
        w = REALIZED_WINDOW_MIN
        one = timedelta(minutes=1)
        win = bars_finished_between(self.bars, self.now - timedelta(minutes=w), self.now)
        before = [x for x in self.bars if _t(x) + one <= self.now - timedelta(minutes=w)]
        if before:
            start, start_t = float(before[-1]["close"]), _t(before[-1]) + one
        elif win and self.bars and win[0] is self.bars[0]:
            start, start_t = float(self.bars[0]["open"]), _t(self.bars[0])   # the window reaches back to the bell
        else:
            start, start_t = None, None
        if len(win) < REALIZED_MIN_BARS or start is None:
            o.skip("iv", "vs_realized_30", f"needs {REALIZED_MIN_BARS} finished bars in the last {w} minutes")
            return
        span = (_t(win[-1]) + one - start_t).total_seconds() / 60.0
        closes = [start] + [float(x["close"]) for x in win]
        ss = sum((b - a) ** 2 for a, b in zip(closes[:-1], closes[1:])) * (w / span) if span > 0 else float("nan")
        realized = math.sqrt(ss) / self.sigma if ss >= 0 else float("nan")
        priced = math.sqrt(w / FULL_SESSION_MIN)
        r = realized / priced
        if not math.isfinite(r):
            o.skip("iv", "vs_realized_30", "the last 30 minutes' closes do not give a finite movement")
            return
        if r < REALIZED_QUIET_RATIO:
            shown = min(r, REALIZED_QUIET_RATIO - 0.01)
            cut = f"under the {REALIZED_QUIET_RATIO:g}-times cut, so quieter than priced"
        elif r > REALIZED_WILD_RATIO:
            shown = max(r, REALIZED_WILD_RATIO + 0.01)
            cut = f"over the {REALIZED_WILD_RATIO:g}-times cut, so wilder than priced"
        else:
            shown = min(max(r, REALIZED_QUIET_RATIO), REALIZED_WILD_RATIO)
            cut = f"between the {REALIZED_QUIET_RATIO:g}-times and {REALIZED_WILD_RATIO:g}-times cuts, so about as priced"
        # the shown ratio never crosses the cut its verdict names, whatever the rounding does
        o.put("iv", "vs_realized_30",
              f"over the last {w} minutes price's realized movement, from its 1-minute closes, was {sig(realized)}, "
              f"{shown:.2f} times the {sig(priced)} move the options market prices for {w} minutes, {cut}")

    # ---- dealer gamma map
    def gex(self) -> None:
        o = self.o
        gv = self.row.get("gex_views") or {}
        lo_cut, hi_cut = EVEN_SPLIT_LOW, EVEN_SPLIT_HIGH
        above, below = gv.get("gamma_above_spot"), gv.get("gamma_below_spot")
        if _is_num(above) and _is_num(below) and (above + below) > 0:
            share = float(above) / float(above + below)
            if share > hi_cut:
                o.put("gex", "weight_side", f"most of today's 0DTE options weight sits above price, {pct(share)} of it")
            elif share < lo_cut:
                o.put("gex", "weight_side", f"most of today's 0DTE options weight sits below price, {pct(1 - share)} of it")
            else:
                o.put("gex", "weight_side", f"today's 0DTE options weight is split about evenly above and below price, {pct(share)} above")
        else:
            o.skip("gex", "weight_side", "row carries no gamma above and below spot")

        walls = self._walls()
        if not walls:
            o.put("gex", "air_to_wall", "no heavy strike sits within reach on either side of price",
                  figure={"kind": "distance", "verdict": "no_wall_in_reach"})
        else:
            name, d, _ = min(walls, key=lambda w: abs(w[1]))
            side = "above" if d >= 0 else "below"
            close = abs(d) <= WALL_NEAR_SIGMA
            fig = {"kind": "distance", "value": round(abs(d), 3), "near": WALL_NEAR_SIGMA, "unit": "sigma", "side": side, "name": name,
                   "verdict": "heavy_strike_close" if close else "open_air"}
            if close:
                o.put("gex", "air_to_wall", f"a heavy strike sits within {WALL_NEAR_SIGMA} sigma of price: the {name} {sig(abs(d))} {side} price", figure=fig)
            else:
                o.put("gex", "air_to_wall", f"the nearest heavy strike is the {name} {sig(abs(d))} {side} price, more than {WALL_NEAR_SIGMA} sigma away, with open air between", figure=fig)

        share = None
        if walls:
            nearest = min(walls, key=lambda w: abs(w[1]))
            share = gv.get("call_wall_gamma_share" if nearest[2] == "call_wall" else "put_wall_gamma_share")
        if not walls or not _is_num(share):
            o.skip("gex", "wall_thickness", "no nearest wall with a gamma share on the row")
        else:
            band = (f"thick, at or above the {pct(WALL_THICK_SHARE)} cut" if share >= WALL_THICK_SHARE else
                    f"thin, below the {pct(WALL_THIN_SHARE)} cut" if share < WALL_THIN_SHARE else
                    f"middling, between the {pct(WALL_THIN_SHARE)} thin cut and the {pct(WALL_THICK_SHARE)} thick cut")
            o.put("gex", "wall_thickness", f"on the nearest wall's side, today's 0DTE {nearest[0]} holds {pct(share)} of today's 0DTE options weight, {band}")

        top = gv.get("pin_top_share")
        if not _is_num(top):
            o.skip("gex", "heaviest_strike_grip", "row carries no top-strike share")
        else:
            band = (f"concentrated, at or above the {pct(GRIP_CONCENTRATED_SHARE)} cut" if top >= GRIP_CONCENTRATED_SHARE else
                    f"spread thin, below the {pct(GRIP_SPREAD_SHARE)} cut" if top < GRIP_SPREAD_SHARE else
                    f"middling, between the {pct(GRIP_SPREAD_SHARE)} and {pct(GRIP_CONCENTRATED_SHARE)} cuts")
            o.put("gex", "heaviest_strike_grip", f"the single heaviest strike of today's 0DTE book holds {pct(top)} of its weight, {band}")

        above_d = (self.row.get("dex_views") or {}).get("dex_above_spot")
        if not _is_num(above_d):
            o.skip("gex", "delta_weight_side", "row carries no delta split")
        elif 1.0 - above_d > hi_cut:
            o.put("gex", "delta_weight_side", f"{pct(1.0 - above_d)} of dealers' directional exposure across the 0-to-7-day books sits at strikes below price, most of it")
        elif 1.0 - above_d < lo_cut:
            o.put("gex", "delta_weight_side", f"{pct(above_d)} of dealers' directional exposure across the 0-to-7-day books sits at strikes above price, most of it")
        else:
            o.put("gex", "delta_weight_side", f"dealers' directional exposure across the 0-to-7-day books is split about evenly, {pct(1.0 - above_d)} below price and {pct(above_d)} above")

        parts = [f"on the {side} side the 1-to-7-day book's wall is {'the same strike as' if self.row[k] == self.row[kt] else 'a different strike from'} the nearest {side} wall"
                 for side, k, kt in (("call", "call_wall", "call_wall_tenor"), ("put", "put_wall", "put_wall_tenor"))
                 if _is_num(self.row.get(k)) and _is_num(self.row.get(kt))]
        if not parts:
            o.skip("gex", "expiry_roll", "no side with both a nearest wall and a 1-to-7-day wall")
        else:
            o.put("gex", "expiry_roll", "; ".join(parts))

        st = (self.row.get("profile_ladder") or {}).get("state")
        if not isinstance(st, str) or not st:
            o.skip("gex", "ladder_state", "the gamma ladder state was not measured on this row")
        else:
            o.put("gex", "ladder_state", f"today's 0DTE gamma ladder is in the {st} state")

    # ---- options activity
    def options(self) -> None:
        o = self.o
        gv = self.row.get("gex_views") or {}
        vols = [(float(v[1]), float(v[2])) for v in gv.get("vol_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2])]
        ois = [abs(float(v[1])) + abs(float(v[2])) for v in gv.get("oi_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2])]
        calls, puts = sum(c for c, _ in vols), sum(p for _, p in vols)
        traded, standing = calls + puts, sum(ois)
        low, high = TURNOVER_LOW, TURNOVER_HIGH
        if traded <= 0 or standing <= 0:
            o.skip("options", "turnover", "no contracts traded or no standing open interest")
        else:
            r = traded / standing
            band = f"under {low:g} times it" if r < low else f"between {low:g} and {high:g} times it" if r <= high else f"more than {high:g} times it"
            o.put("options", "turnover", f"today's 0DTE contracts traded are {r:.1f} times their standing open position, {band}")
        lo_cut, hi_cut = EVEN_SPLIT_LOW, EVEN_SPLIT_HIGH
        if traded <= 0:
            o.skip("options", "call_put_split", "no contracts traded yet today")
        else:
            cs = calls / traded
            band = (f"mostly calls, beyond the {pct(hi_cut)} cut" if cs > hi_cut else
                    f"mostly puts, calls under the {pct(lo_cut)} cut" if cs < lo_cut else f"an even split within {pct(lo_cut)} to {pct(hi_cut)}")
            o.put("options", "call_put_split", f"{pct(cs)} of today's 0DTE contracts traded near price were calls, {band}")

        gross = [(float(v[0]), float(v[1])) for v in gv.get("vol_gross_by_strike") or []
                 if isinstance(v, (list, tuple)) and len(v) >= 2 and _is_num(v[0]) and _is_num(v[1])]
        total = sum(v for _, v in gross)
        if not gross or total <= 0:
            o.skip("options", "new_activity", "no contracts traded yet today")
            return
        busiest, top = max(gross, key=lambda kv: kv[1])
        share = top / total
        nearest = min(gross, key=lambda kv: abs(kv[0] - self.spot))[0]
        if share <= BUSIEST_STRIKE_SHARE:
            o.put("options", "new_activity", f"today's 0DTE option volume is spread across strikes, no strike holding more than {pct(BUSIEST_STRIKE_SHARE)} of it; the busiest holds {pct(share)}")
        elif busiest == nearest:
            o.put("options", "new_activity", f"the busiest 0DTE strike today is the one nearest price, holding {pct(share)} of the day's 0DTE option volume, more than the {pct(BUSIEST_STRIKE_SHARE)} cut")
        else:
            o.put("options", "new_activity", f"the busiest 0DTE strike today sits {'above' if busiest > self.spot else 'below'} price, holding {pct(share)} of the day's 0DTE option volume, more than the {pct(BUSIEST_STRIKE_SHARE)} cut")

    # ---- the expiries (expiry.py): which book expires when, and which book the weight sits in
    def expiry(self) -> None:
        o = self.o
        settle = todays_settle(self.now)
        if settle is None:
            o.skip("expiry", "settle_clock", "no 0DTE expiry is left today")
        else:
            left = max(round((settle - self.now).total_seconds() / 60.0), 0)
            close = "the 13:00 ET half-day close" if settle.hour == 13 else "the 16:00 ET close"
            window = (f"inside their last {ZERO_DTE_LAST_HOUR_MIN} minutes, when their gamma decays fastest" if left <= ZERO_DTE_LAST_HOUR_MIN
                      else f"before their last {ZERO_DTE_LAST_HOUR_MIN} minutes, when their gamma decays fastest")
            o.put("expiry", "settle_clock", f"today's 0DTE SPXW options settle at {close}, {plural(left, 'minute')} from now, {window}")

        today = self.now.date()
        kinds = expiry_kinds(today)
        monthly = ("the AM-settled SPX monthly options settled on this morning's opening prints "
                   "and the PM-settled SPXW monthly options settle at the close")
        if "quarterly" in kinds:
            text = f"today is the quarterly expiry: {monthly}"
        elif "monthly" in kinds:
            text = f"today is the monthly expiry: {monthly}"
        else:
            ahead = trading_days_between(today, next_monthly(self.now))
            text = f"today is an ordinary daily expiry; the next monthly expiry is {plural(ahead, 'trading day')} away"
        if "quarter_end" in kinds:
            text += "; it is also the quarter's last trading day, when the quarter-end SPXW options settle at the close"
        o.put("expiry", "opex_today", text)

        dated = self.row.get("dated_gex") or {}
        bands = [b for b in dated.get("bands") or [] if _is_num(b.get("gamma_mass")) and b["gamma_mass"] > 0 and _is_num(b.get("dte"))]
        if not bands:
            o.skip("expiry", "dated_weight", "row carries no dated books")
        elif dated.get("staleness") != "fresh":
            o.skip("expiry", "dated_weight", f"the dated books are {dated.get('staleness') or 'of unknown age'}, not this morning's")
        else:
            names = {"monthly": "monthly", "quarter_end": "quarter-end"}
            top = max(bands, key=lambda b: b["gamma_mass"])
            counts = ", ".join(f"{sum(1 for b in bands if b.get('band') == k)} {names.get(k, str(k))}"
                               for k in dict.fromkeys(b.get("band") for b in bands))
            share = top["gamma_mass"] / sum(b["gamma_mass"] for b in bands)
            o.put("expiry", "dated_weight", f"of the dated books pulled this morning ({counts}), the {names.get(top.get('band'), 'dated')} expiry "
                                            f"{plural(int(top['dte']), 'day')} out holds the most options weight, {pct(share)} of their gamma")

        gv = self.row.get("gex_views") or {}
        words = {"long_gamma": "long gamma, where dealers' hedging damps moves", "short_gamma": "short gamma, where dealers' hedging feeds moves",
                 "uncertain": "balanced, too close to call"}
        today_book, week_book = gv.get("regime"), gv.get("regime_tenor")
        if gv.get("regime_source") != "0dte":
            o.skip("expiry", "today_vs_week", "today's 0DTE book could not be read on this row, so the scanner fell back to the blended book")
        elif today_book not in words or week_book not in words:
            o.skip("expiry", "today_vs_week", "row carries no regime for both today's 0DTE book and the 0-to-7-day book")
        elif today_book == week_book:
            o.put("expiry", "today_vs_week", f"today's 0DTE book and the blended 0-to-7-day book both read {words[today_book]}, so they agree")
        else:
            o.put("expiry", "today_vs_week", f"today's 0DTE book reads {words[today_book]}, while the blended 0-to-7-day book reads {words[week_book]}, so they disagree")

    # ---- momentum
    def momentum(self) -> None:
        o = self.o
        closes = [float(x["close"]) for x in self.bars]
        for key, series, name, need in (("rsi_1min", closes, "1-minute", f"{RSI_PERIOD + 1} finished bars"),
                                        ("rsi_5min", closes[4::5], "5-minute", f"{(RSI_PERIOD + 1) * 5} minutes of finished bars")):
            rsi = wilder_rsi(series)
            if rsi is None:
                o.skip("momentum", key, f"needs {need}")
            elif rsi > RSI_OVERBOUGHT:
                o.put("momentum", key, f"the {name} RSI is {rsi:.0f}, above {RSI_OVERBOUGHT}")
            elif rsi < RSI_OVERSOLD:
                o.put("momentum", key, f"the {name} RSI is {rsi:.0f}, below {RSI_OVERSOLD}")
            else:
                o.put("momentum", key, f"the {name} RSI is {rsi:.0f}, between {RSI_OVERSOLD} and {RSI_OVERBOUGHT}")

        c0 = closes[-1] if closes else None
        c10 = close_at(self.bars, self.now - timedelta(minutes=10))
        c20 = close_at(self.bars, self.now - timedelta(minutes=20))
        if self.since_open < 20 or c0 is None or c10 is None or c20 is None:
            o.skip("momentum", "pace", "needs 20 minutes of finished bars")
        else:
            last, prev = abs(c0 - c10) / self.sigma, abs(c10 - c20) / self.sigma
            if prev <= 0 and last <= 0:
                o.put("momentum", "pace", "the last 10 minutes and the 10 minutes before both moved about the same, close to nothing")
            elif prev <= 0:
                o.put("momentum", "pace", f"the last 10 minutes moved {sig(last)} after the 10 minutes before moved close to nothing, so more than {PACE_BIGGER:g} times as far")
            else:
                ratio = last / prev
                word = (f"more than {PACE_BIGGER:g} times as far as" if ratio > PACE_BIGGER else
                        "less than two thirds as far as" if ratio < PACE_SMALLER else "between two thirds and 1.5 times as far as")
                o.put("momentum", "pace", f"the last 10 minutes moved {sig(last)}, {word} the 10 minutes before, which moved {sig(prev)}")

        if self.move30 is None:
            for key in ("closes", "pauses", "path_efficiency"):
                o.skip("momentum", key, "no 30-minute move to judge")
            return
        if abs(self.move30) < MOVE_RULE_SIGMA:
            # a quiet read is a fact, not a gap: the questions that read these labels have a
            # "no move" answer, and leaving the labels out would skip those questions instead
            quiet = f"the last 30 minutes moved {sig(abs(self.move30))}, under the {MOVE_RULE_SIGMA} sigma move rule, so there is no move to judge"
            for key in ("closes", "pauses", "path_efficiency"):
                o.put("momentum", key, quiet)
            return
        up = self.move30 > 0
        word = "up" if up else "down"
        last5 = self.bars[-5:]
        if len(last5) < 5:
            o.skip("momentum", "closes", "needs five finished bars")
        else:
            agree = sum(1 for x in last5 if (float(x["close"]) > float(x["open"])) == up and float(x["close"]) != float(x["open"]))
            o.put("momentum", "closes", f"of the last five 1-minute bars, {agree} closed in the direction of the move, which is {word}")

        win = bars_finished_between(self.bars, self.now - timedelta(minutes=30), self.now)
        start = close_at(self.bars, self.now - timedelta(minutes=30))
        if len(win) < 20 or start is None:
            o.skip("momentum", "pauses", "needs 30 minutes of finished bars")
            o.skip("momentum", "path_efficiency", "needs 30 minutes of finished bars")
            return
        ext = start                 # the move's running extreme
        last_ext = -1               # index of the bar that last pushed it
        longest_stall = 0
        deepest = 0.0               # the deepest dip from the running extreme, in points
        for i, x in enumerate(win):
            hi, lo = float(x["high"]), float(x["low"])
            # a dip is measured from the extreme reached before this bar
            deepest = max(deepest, (ext - lo) if up else (hi - ext))
            if up and hi > ext:
                ext, last_ext = hi, i
            elif (not up) and lo < ext:
                ext, last_ext = lo, i
            longest_stall = max(longest_stall, i - last_ext if last_ext >= 0 else i + 1)
        # the pullback is that dip as a share of the move's full extent over the window,
        # never of the distance travelled so far, which is tiny early on and inflates a wobble
        extent = abs(ext - start)
        retrace = deepest / extent if extent > 0 else 0.0
        if retrace > 1.0:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} gave back more than all of itself at its deepest pullback, far more than a quarter")
        elif retrace >= PULLBACK_SHARE:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} gave back {pct(retrace)} of itself at its deepest pullback, more than a quarter")
        elif longest_stall > PAUSE_LONG_MIN:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} paused for {plural(longest_stall, 'minute')} without a new extreme, longer than {PAUSE_LONG_MIN} minutes, and gave back {pct(retrace)} of itself, less than a quarter")
        elif longest_stall >= PAUSE_BRIEF_MIN:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} paused for {plural(longest_stall, 'minute')}, between {PAUSE_BRIEF_MIN} and {PAUSE_LONG_MIN} minutes, and gave back {pct(retrace)} of itself, less than a quarter")
        else:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} ran without a pause longer than {PAUSE_BRIEF_MIN} minutes and gave back {pct(retrace)} of itself, less than a quarter")

        wcloses = [float(x["close"]) for x in win]
        net = abs(wcloses[-1] - wcloses[0])
        travel = sum(abs(b - a) for a, b in zip(wcloses[:-1], wcloses[1:]))
        if net <= 0 or travel <= 0:
            o.skip("momentum", "path_efficiency", "the window's closes netted nothing, so there is no path to judge")
            return
        eff = net / travel
        lead = f"over the last 30 minutes price travelled {travel / net:.1f} times its net move, path efficiency {pct(eff)}"
        if eff >= PATH_ORDERLY:
            o.put("momentum", "path_efficiency", f"{lead}, orderly ({pct(PATH_ORDERLY)} or more)")
        elif eff >= PATH_CHOPPY:
            o.put("momentum", "path_efficiency", f"{lead}, mixed (between {pct(PATH_CHOPPY)} and {pct(PATH_ORDERLY)})")
        else:
            o.put("momentum", "path_efficiency", f"{lead}, choppy (under {pct(PATH_CHOPPY)})")

    # ---- the market around SPX (market_context.py)
    def breadth(self) -> None:
        o, mk = self.o, self.s.market
        if mk is None:
            for key in ("advance_decline", "tick_lean", "sectors_up"):
                o.skip("breadth", key, "no market-context snapshot today")
            return
        add = mk.last("$ADD", self.now)
        if add is None:
            o.skip("breadth", "advance_decline", "no NYSE advance-decline value at or before now")
        elif add == 0:
            o.put("breadth", "advance_decline", "as many NYSE stocks are advancing as declining today")
        else:
            o.put("breadth", "advance_decline", f"on the NYSE {abs(round(add))} more stocks are {'advancing than declining' if add > 0 else 'declining than advancing'} today, more {'up' if add > 0 else 'down'} than {'down' if add > 0 else 'up'}")

        ticks = mk.between("$TICK", self.now - timedelta(minutes=TICK_WINDOW_MIN), self.now)
        if len(ticks) < MIN_TICK_MINUTES:
            o.skip("breadth", "tick_lean", f"needs {MIN_TICK_MINUTES} NYSE tick readings in the last {TICK_WINDOW_MIN} minutes, have {len(ticks)}")
        else:
            up, down = sum(1 for v in ticks if v > 0), sum(1 for v in ticks if v < 0)
            lean = "leaning to buying" if up > down else "leaning to selling" if down > up else "even"
            o.put("breadth", "tick_lean", f"over the last {TICK_WINDOW_MIN} minutes the NYSE tick read above zero {plural(up, 'time')} and below zero {plural(down, 'time')} of {len(ticks)}, {lean}")

        moved = [(s, mk.last(s, self.now), mk.first(s)) for s in SECTORS]
        moved = [(s, now_v, open_v) for s, now_v, open_v in moved if now_v is not None and open_v]
        if len(moved) < MIN_SECTORS:
            o.skip("breadth", "sectors_up", f"needs {MIN_SECTORS} of the {len(SECTORS)} sector funds with a value today, have {len(moved)}")
        else:
            up = sum(1 for _, now_v, open_v in moved if now_v > open_v)
            word = "most of them" if up * 2 > len(moved) else "fewer than half" if up * 2 < len(moved) else "exactly half"
            o.put("breadth", "sectors_up", f"{up} of {len(moved)} sector funds are above where they opened today, {word}")

    # ---- the tape lane: the stretch since the last read
    def tape(self) -> None:
        """Written only on the bar clock (the tape lane); the live lane writes nothing and omits nothing.
        The stretch runs from the lane's last read today, or from the open on the day's first read, to
        this read. The move keeps the sum's bands in tape units; the range is placed against the same
        minutes on the prior sessions, in thirds, with the count in the sentence."""
        s, o = self.s, self.o
        if not s.bar_clock:
            return
        since = s.last_read if s.last_read is not None else self.open_t
        what = "the last read" if s.last_read is not None else "the open"
        gap = (self.now - since).total_seconds() / 60.0
        lead = f"since {what}, {gap:g} minutes ago"
        start_min, end_min = _mod(since), _mod(self.now)
        anchor, win = stretch(self.bars, start_min, end_min)
        if gap <= 0 or not win:
            for key in ("move_since_read", "range_since_read"):
                o.skip("tape", key, f"no finished bars {lead}")
            return
        if s.unit is None:
            for key in ("move_since_read", "range_since_read"):
                o.skip("tape", key, "no tape unit this read: the bars have stopped")
            return
        u = float(s.unit["unit_points"])
        if s.last_read is None:
            o.skip("tape", "move_since_read", "the day's first read: no earlier read today to measure from")
        elif anchor is None:
            o.skip("tape", "move_since_read", f"no finished bar at {what}")
        else:
            net = self.spot - anchor
            x = net / u
            where = "level with it" if net == 0 else f"{abs(net):.1f} points {'above' if net > 0 else 'below'} it"
            verb = "rose" if net > 0 else "fell"
            if abs(x) <= TAPE_FLAT_UNITS:
                cut, verdict = f"within the {TAPE_FLAT_UNITS} cut", "held"
            elif abs(x) <= TAPE_BIG_UNITS:
                cut, verdict = f"more than the {TAPE_FLAT_UNITS} cut and no more than {TAPE_BIG_UNITS}", f"{verb} small"
            else:
                cut, verdict = f"more than the {TAPE_BIG_UNITS} cut", f"{verb} big"
            o.put("tape", "move_since_read", f"{lead}, price ended {where}, {units_of(abs(x), u)}, {cut}, so it {verdict}")
        # the range needs every minute of the window on today's tape and on each prior session it is ranked against
        minutes = end_min - start_min
        if len(win) < minutes:
            o.skip("tape", "range_since_read", f"bars missing {lead}: {len(win)} of {minutes} minutes")
            return
        value = stretch_range(win)
        base = [stretch_range(pwin) for pbars in s.prior_bars.values() if len(pwin := stretch(pbars, start_min, end_min)[1]) >= minutes]
        rank = rank_at_slot(value, base)
        if rank is None:
            o.skip("tape", "range_since_read", f"needs {MIN_RANK_SESSIONS} prior sessions of bars at these minutes, have {len(base)}")
            return
        o.put("tape", "range_since_read", f"the range {lead}, is {value:.1f} points, {units_of(value / u, u)}, "
                                          f"in the {rank['band']} for this minute, higher than {rank['higher_than']} of {rank['of']} prior sessions")
