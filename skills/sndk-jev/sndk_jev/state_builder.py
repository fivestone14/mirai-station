"""Turn SNDK PRO's diary rows and minute bars into the plain-English labels JEV reads.

JEV reads words and cannot compare numbers, so every comparison happens here and
is written out as a sentence with the threshold in it. The rules:

* Read-only. Rows come from ``state/sndk_reversion/{day}.jsonl`` and bars from
  ``state/sndk_bars/{day}.jsonl``. Nothing is written into SNDK PRO's files.
* Omit, never null. A label that cannot be measured is left out and the reason
  is recorded in ``omitted``; the request packer then skips every question
  that needs it (SNDK PRO's own omit-never-null rule).
* Point in time. ``now`` is the row's own timestamp, not the wall clock. Only
  bars that finished before ``now`` count, so a replay never sees the
  unfinished minute, and prior sessions are only days before ``day``.
* No prices in labels. Distances are in sigma (today's expected-move unit for
  SanDisk), shares are percentages, and strikes are described, never named.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".claude" / "plugins" / "mirai-station" / "state"
ROWS_SUBDIR = "sndk_reversion"
BARS_SUBDIR = "sndk_bars"

SYMBOL = "SNDK"
HORIZON = "the next 30 minutes"
UNITS = ("all distances are in sigma, today's expected-move unit for SanDisk; "
         "a plus sign means above price and a minus sign means below price. "
         "Options weight means the hedging exposure of dealers, the market makers on the other side of the options, at each strike; "
         "a wall is a strike where that weight piles up; the opening box is the first half hour's price range; "
         "RSI is a 0 to 100 gauge of overbought (above 70) or oversold (below 30)")

# SNDK PRO's own move rule (frame_is: a move at or past 0.15 sigma).
MOVE_RULE_SIGMA = 0.15
# The Fade Lens near-wall condition, reused as the "heavy strike close" cut.
WALL_NEAR_SIGMA = 0.5
# SNDK PRO's vol_trend flat band, in vol points.
IV_FLAT_BAND_PTS = 2.0
# Below this many vol points of put-minus-call IV the skew reads as even.
SKEW_EVEN_BAND_PTS = 1.0
# Options weight above price: below the low cut reads "below", above the high cut "above".
WEIGHT_EVEN_BAND = (0.40, 0.60)
# Three strikes holding at least this share of today's contracts reads "concentrated".
CONCENTRATED_SHARE = 0.50
# One strike holding more than a fifth of today's option volume is the busiest strike.
BUSIEST_STRIKE_SHARE = 0.20
# The session's shape: an extension or giveback from the open at or past this reads as a real leg.
SHAPE_CUT = 0.30
# Path efficiency of a 30-minute move: net move over distance travelled.
PATH_ORDERLY = 0.60
PATH_CHOPPY = 0.30
# Pace of the last 10 minutes against the 10 before.
PACE_BIGGER = 1.5
PACE_SMALLER = 2.0 / 3.0
# Pauses inside a 30-minute move.
PAUSE_BRIEF_MIN = 3
PAUSE_LONG_MIN = 10
PULLBACK_SHARE = 0.25
# Volume windows and the baseline they rank against.
VOLUME_WINDOW_MIN = 30
MIN_VOLUME_SESSIONS = 5
MAX_BASELINE_SESSIONS = 20
TOP_FIFTH = 0.80
BOTTOM_FIFTH = 0.20
# Today's range against the prior sessions at the same time of day.
RANGE_PRIOR_SESSIONS = 5
MIN_RANGE_SESSIONS = 3
# The opening box is the first 30 minutes; a break is past the move bar,
# which SNDK PRO defines as two typical minutes (twice the median 1-minute range).
OPENING_BOX_MIN = 30
MOVE_BAR_MINUTES = 2
RSI_PERIOD = 14
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)       # NYSE half days (the day after Thanksgiving, some July 3rds and Christmas Eves)
FULL_SESSION_MIN = 390          # a full session's minutes: sigma is a full day's expected move
# Realized against priced movement over the last 30 minutes: the realized path from the 1-minute
# closes, against sigma scaled to 30 minutes by the square root of time.
REALIZED_WINDOW_MIN = 30
REALIZED_MIN_BARS = 25
REALIZED_QUIET = 0.7
REALIZED_WILD = 1.3
# Where price sits in today's volume profile: the smoothed 0.1-sigma bins at price against the
# heaviest bin of the day.
PROFILE_THIN = 0.25
PROFILE_THICK = 0.50
MIN_BARS_FOR_A_SESSION = 300


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
    rows = [r for r in load_jsonl(state_dir / ROWS_SUBDIR / f"{day}.jsonl")
            if _is_num(r.get("spot")) and isinstance(r.get("ts"), str)]
    rows.sort(key=lambda r: r["ts"])
    return rows


def load_bars(state_dir: Path, day: str) -> list[dict]:
    bars = [b for b in load_jsonl(state_dir / BARS_SUBDIR / f"{day}.jsonl")
            if isinstance(b.get("ts"), str) and all(_is_num(b.get(k)) for k in ("open", "high", "low", "close"))]
    bars.sort(key=lambda b: b["ts"])
    return bars


def available_days(state_dir: Path, subdir: str) -> list[str]:
    return sorted(p.stem for p in (state_dir / subdir).glob("????-??-??.jsonl"))


def prior_bar_days(state_dir: Path, day: str, limit: int = MAX_BASELINE_SESSIONS) -> dict[str, list[dict]]:
    """The most recent full sessions of bars strictly before ``day``, newest first."""
    out: dict[str, list[dict]] = {}
    for d in reversed(available_days(state_dir, BARS_SUBDIR)):
        if d >= day:
            continue
        bars = load_bars(state_dir, d)
        if len(bars) >= MIN_BARS_FOR_A_SESSION:
            out[d] = bars
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------------- scene

SIDE_SUBDIR = "sndk_side"
CHAIN_CACHE = Path("sndk_gex") / "chain_cache.json"
CHAIN_CACHE_MAX_AGE_MIN = 6.0    # the cache is only the latest book; use it only when it is this row's book


@dataclass
class Scene:
    """Everything the builder is allowed to see at one moment."""
    row: dict
    rows_today: list[dict]                 # rows up to and including ``row``
    bars: list[dict]                       # today's bars that finished before ``now``
    prior_bars: dict[str, list[dict]]      # full prior sessions, newest first
    now: datetime
    sigma: float
    news: dict | None = None
    side_packet: dict | None = None        # SNDK PRO's side packet at or before now: level visits, RSI episodes
    chain_cache: dict | None = None        # the latest options book, only when it is this row's book
    prev_last_row: dict | None = None      # the last diary row of the most recent earlier session


def load_side_packet(state_dir: Path, day: str, minutes_since_open: float) -> dict | None:
    """The last side packet whose bars all lie at or before ``now``. Packets carry bar indices, not clocks."""
    best = None
    for pk in load_jsonl(state_dir / SIDE_SUBDIR / f"{day}.jsonl"):
        bars_seen = [lv.get("interactions_covered_to_bar") for lv in pk.get("levels") or []]
        bars_seen += [ep.get("to_bar") for ep in pk.get("episodes") or []]
        bars_seen += [rd.get("at_bar") for rd in pk.get("readings") or []]
        bars_seen = [b for b in bars_seen if _is_num(b)]
        if not bars_seen:
            continue
        # bar indices are 0-based: bar k finishes k + 1 minutes after the open
        if max(bars_seen) + 1 <= minutes_since_open:
            best = pk
    return best


def load_chain_cache(state_dir: Path, now: datetime) -> dict | None:
    path = state_dir / CHAIN_CACHE
    if not path.exists():
        return None
    try:
        cc = json.loads(path.read_text(encoding="utf-8"))
        ts = parse_ts(cc["ts"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    age = (now - ts).total_seconds() / 60.0
    if age < -1.0 or age > CHAIN_CACHE_MAX_AGE_MIN or not isinstance(cc.get("contracts"), list):
        return None
    return cc


def load_prev_last_row(state_dir: Path, day: str) -> dict | None:
    for d in reversed(available_days(state_dir, ROWS_SUBDIR)):
        if d >= day:
            continue
        rows = load_rows(state_dir, d)
        if rows:
            return rows[-1]
    return None


def make_scene(state_dir: Path | str = DEFAULT_STATE_DIR, day: str | None = None,
               at: time | None = None, news: dict | None = None) -> Scene:
    state_dir = Path(state_dir)
    if day is None:
        days = available_days(state_dir, ROWS_SUBDIR)
        if not days:
            raise ValueError(f"no SNDK PRO rows under {state_dir / ROWS_SUBDIR}")
        day = days[-1]
    rows = load_rows(state_dir, day)
    if not rows:
        raise ValueError(f"no usable SNDK PRO rows for {day}")
    if at is not None:
        tz = parse_ts(rows[0]["ts"]).tzinfo
        cutoff = datetime.combine(date.fromisoformat(day), at, tzinfo=tz)
        rows = [r for r in rows if parse_ts(r["ts"]) <= cutoff]
        if not rows:
            raise ValueError(f"no SNDK PRO row at or before {at} on {day}")
    row = rows[-1]
    now = parse_ts(row["ts"])
    sigma = row.get("sigma")
    if not _is_num(sigma) or sigma <= 0:
        raise ValueError(f"row at {row['ts']} carries no sigma ruler")
    bars = [b for b in load_bars(state_dir, day) if parse_ts(b["ts"]) + timedelta(minutes=1) <= now]
    since_open = (now - session_open(now)).total_seconds() / 60.0
    return Scene(row=row, rows_today=rows, bars=bars, prior_bars=prior_bar_days(state_dir, day),
                 now=now, sigma=float(sigma), news=news,
                 side_packet=load_side_packet(state_dir, day, since_open),
                 chain_cache=load_chain_cache(state_dir, now),
                 prev_last_row=load_prev_last_row(state_dir, day))


# ----------------------------------------------------------------------------- helpers

def _t(b: dict) -> datetime:
    return parse_ts(b["ts"])


def _mod(t: datetime) -> int:
    """Minute of day."""
    return t.hour * 60 + t.minute


def session_open(now: datetime) -> datetime:
    return now.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)


_RUNTIME = Path(__file__).resolve().parents[3] / "runtime"


def half_days(year: int) -> frozenset:
    """NYSE early-close days from the station's own market-hours gate (the watch package's
    computed calendar), the same source SNDK PRO's scanner and feeds use. Fail-open to no half
    days, which is the old 16:00 assumption, rather than a crash."""
    try:
        if str(_RUNTIME) not in sys.path:
            sys.path.insert(0, str(_RUNTIME))
        from watch.intraday import market_status as _ms
        return _ms._half_days(year)
    except Exception:
        return frozenset()


def session_close(now: datetime) -> datetime:
    """16:00 ET, or 13:00 on a half day. The grader reads the same close, so a horizon past it is
    closed out instead of waiting forever for bars that will never come."""
    c = EARLY_CLOSE if now.date() in half_days(now.year) else SESSION_CLOSE
    return now.replace(hour=c.hour, minute=c.minute, second=0, microsecond=0)


def session_minutes(now: datetime) -> float:
    return (session_close(now) - session_open(now)).total_seconds() / 60.0


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


def fifth_band(rank_frac: float) -> str:
    if rank_frac >= TOP_FIFTH:
        return "top fifth"
    if rank_frac <= BOTTOM_FIFTH:
        return "bottom fifth"
    return "middle band"


def rank_frac(value: float, others: list[float]) -> float:
    """Share of ``others`` that sit below ``value``."""
    return sum(1 for o in others if o < value) / len(others)


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

    A labeller may also hand over two stripped forms of the same sentence and the
    answer option the code itself landed on. Both are only used by the A/B harness
    (``build_ab``); the live path ignores them.

    * ``no_figure``  the same sentence with the measured number deleted
    * ``no_band``    the same sentence with the threshold clause deleted

    Both must be strict deletions from the full sentence: no new words, no rephrasing.
    Introducing vocabulary in one arm would decide the experiment by wording.
    """

    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.omitted: dict[str, str] = {}
        self.variants: dict[str, dict[str, str]] = {}
        self.verdicts: dict[str, str] = {}

    def put(self, group: str, key: str, value: Any, *, answer: str | None = None,
            no_figure: str | None = None, no_band: str | None = None) -> None:
        self.state.setdefault(group, {})[key] = value
        if answer is not None:
            self.verdicts[f"{group}.{key}"] = answer
        if no_figure is not None or no_band is not None:
            self.variants[f"{group}.{key}"] = {
                "full": value,
                "no_figure": no_figure if no_figure is not None else value,
                "no_band": no_band if no_band is not None else value,
            }

    def skip(self, group: str, key: str, reason: str) -> None:
        self.omitted[f"{group}.{key}"] = reason


def _run(scene: Scene) -> _Out:
    out = _Out()
    b = _Builder(scene, out)
    for step in (b.context, b.price, b.range, b.iv, b.gex, b.options, b.volume, b.momentum,
                 b.context_more, b.price_more, b.range_more, b.iv_more, b.gex_more, b.options_more,
                 b.volume_more, b.momentum_more,
                 b.session_shape, b.nearest_level, b.new_activity, b.path_efficiency, b.news):
        step()
    return out


def build_state(scene: Scene) -> tuple[dict, dict]:
    """Return ``(state, omitted)``. ``state`` holds only what could be measured."""
    out = _run(scene)
    return out.state, out.omitted


def build_ab(scene: Scene) -> tuple[dict, dict, dict, dict]:
    """Return ``(state, omitted, variants, verdicts)`` for the A/B harness.

    ``verdicts`` is what the code decided, keyed by label path, and is the ground
    truth the harness scores JEV against. It is computed, never interpreted.
    """
    out = _run(scene)
    return out.state, out.omitted, out.variants, out.verdicts


def variant_state(state: dict, variants: dict, arm: str) -> dict:
    """A copy of ``state`` with every label that has variants swapped to ``arm``."""
    if arm == "full":
        return state
    swapped = {g: dict(labels) for g, labels in state.items()}
    for path, forms in variants.items():
        group, key = path.split(".", 1)
        if group in swapped and key in swapped[group]:
            swapped[group][key] = forms[arm]
    return swapped


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

    # ---- context
    def context(self) -> None:
        o = self.o
        o.put("context", "symbol", SYMBOL)
        o.put("context", "units", UNITS)
        o.put("context", "horizon", HORIZON)
        # where we are in the session is one label, context.session_progress (see context_more)

        expiries = (self.row.get("meta") or {}).get("expiries") or []
        front = expiries[0] if expiries and isinstance(expiries[0], dict) else None
        if not front or not _is_num(front.get("dte")) or not isinstance(front.get("date"), str):
            o.skip("context", "expiry", "row carries no front expiry")
            return
        dte = int(front["dte"])
        try:
            edate = date.fromisoformat(front["date"])
        except ValueError:
            o.skip("context", "expiry", "front expiry date unreadable")
            return
        weekday = edate.strftime("%A")
        today = self.now.date()
        if dte <= 0:
            o.put("context", "expiry", "the front weekly expires today")
        elif dte == 1:
            o.put("context", "expiry", f"the front weekly expires tomorrow, {weekday}")
        elif edate.isocalendar()[:2] == today.isocalendar()[:2]:
            o.put("context", "expiry", f"the front weekly expires later this week, on {weekday}, {plural(dte, 'day')} away")
        else:
            o.put("context", "expiry", f"the front weekly expires next week or later, on {weekday}, {plural(dte, 'day')} away")

    # ---- price
    def price(self) -> None:
        o = self.o
        if self.since_open < 30:
            o.skip("price", "recent_move", "needs 30 minutes of session")
        else:
            ref = close_at(self.bars, self.now - timedelta(minutes=30))
            if ref is None:
                o.skip("price", "recent_move", "no finished bar 30 minutes ago")
            else:
                d = (self.spot - ref) / self.sigma
                self.move30 = d
                if abs(d) < MOVE_RULE_SIGMA:
                    o.put("price", "recent_move",
                          f"over the last 30 minutes price stayed within {MOVE_RULE_SIGMA} sigma of where it was, moving {signed(d)} sigma",
                          answer="going_nowhere",
                          no_figure="over the last 30 minutes price stayed within the move rule of where it was",
                          no_band=f"over the last 30 minutes price moved {signed(d)} sigma")
                elif d > 0:
                    o.put("price", "recent_move",
                          f"over the last 30 minutes price rose {sig(d)}, more than the {MOVE_RULE_SIGMA} sigma move rule",
                          answer="rising",
                          no_figure="over the last 30 minutes price rose, more than the move rule",
                          no_band=f"over the last 30 minutes price rose {sig(d)}")
                else:
                    o.put("price", "recent_move",
                          f"over the last 30 minutes price fell {sig(-d)}, more than the {MOVE_RULE_SIGMA} sigma move rule",
                          answer="falling",
                          no_figure="over the last 30 minutes price fell, more than the move rule",
                          no_band=f"over the last 30 minutes price fell {sig(-d)}")

        if not self.bars:
            o.skip("price", "day_range_position", "no finished bars yet")
        else:
            hi = max(max(float(x["high"]) for x in self.bars), self.spot)
            lo = min(min(float(x["low"]) for x in self.bars), self.spot)
            if hi - lo <= 0:
                o.skip("price", "day_range_position", "today's range is zero so far")
            else:
                pos = (self.spot - lo) / (hi - lo)
                o.put("price", "day_range_position",
                      f"price is in the {third(pos)} third of today's range so far, {pct(pos)} of the way up from the low to the high",
                      answer=third(pos),
                      no_figure=f"price is in the {third(pos)} third of today's range so far",
                      no_band=f"price is {pct(pos)} of the way up from today's low to its high")

        vwap = self.row.get("vwap")
        if not _is_num(vwap) or vwap <= 0:
            o.skip("price", "vs_vwap", "row carries no vwap")
        else:
            d = (self.spot - float(vwap)) / self.sigma
            if abs(d) < MOVE_RULE_SIGMA:
                o.put("price", "vs_vwap",
                      f"price is within {MOVE_RULE_SIGMA} sigma of the day's volume-weighted average price, {signed(d)} sigma from it",
                      answer="at_it",
                      no_figure="price is within the move rule of the day's volume-weighted average price",
                      no_band=f"price is {signed(d)} sigma from the day's volume-weighted average price")
            elif d > 0:
                o.put("price", "vs_vwap",
                      f"price is {sig(d)} above the day's volume-weighted average price, more than the {MOVE_RULE_SIGMA} sigma move rule",
                      answer="above",
                      no_figure="price is above the day's volume-weighted average price, more than the move rule",
                      no_band=f"price is {sig(d)} above the day's volume-weighted average price")
            else:
                o.put("price", "vs_vwap",
                      f"price is {sig(-d)} below the day's volume-weighted average price, more than the {MOVE_RULE_SIGMA} sigma move rule",
                      answer="below",
                      no_figure="price is below the day's volume-weighted average price, more than the move rule",
                      no_band=f"price is {sig(-d)} below the day's volume-weighted average price")

    # ---- range
    def range(self) -> None:
        o = self.o
        opening = bars_between(self.bars, self.open_t, self.open_t + timedelta(minutes=OPENING_BOX_MIN))
        if len(opening) < OPENING_BOX_MIN:
            o.put("range", "box_status", f"the opening box is still forming, {plural(len(opening), 'minute')} of its 30 are in")
        else:
            box_hi = max(float(x["high"]) for x in opening)
            box_lo = min(float(x["low"]) for x in opening)
            recent = self.bars[-30:]
            med = statistics.median(float(x["high"]) - float(x["low"]) for x in recent)
            bar = MOVE_BAR_MINUTES * med
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

        if not self.bars:
            o.skip("range", "today_vs_normal", "no finished bars yet")
            return
        hi = max(max(float(x["high"]) for x in self.bars), self.spot)
        lo = min(min(float(x["low"]) for x in self.bars), self.spot)
        open0 = float(self.bars[0]["open"])
        today_pct = (hi - lo) / open0 if open0 > 0 else None
        if today_pct is None:
            o.skip("range", "today_vs_normal", "no opening price")
            return
        cutoff = _mod(self.now)
        prior: list[float] = []
        for day, pbars in list(self.s.prior_bars.items())[:RANGE_PRIOR_SESSIONS]:
            same = [x for x in pbars if _mod(_t(x)) + 1 <= cutoff]
            if len(same) < 5:
                continue
            p_open = float(same[0]["open"])
            if p_open <= 0:
                continue
            prior.append((max(float(x["high"]) for x in same) - min(float(x["low"]) for x in same)) / p_open)
        if len(prior) < MIN_RANGE_SESSIONS:
            o.skip("range", "today_vs_normal", f"needs {MIN_RANGE_SESSIONS} prior sessions of bars at this time of day, have {len(prior)}")
            return
        below = sum(1 for p in prior if p < today_pct)
        band = third(below / len(prior))
        o.put("range", "today_vs_normal",
              f"today's range so far is in the {band} third of the last {len(prior)} sessions at this time of day, larger than {below} of them",
              answer={"bottom": "small", "middle": "normal", "top": "large"}[band],
              no_figure=f"today's range so far is in the {band} third of recent sessions at this time of day",
              no_band=f"today's range so far is larger than {below} of the last {len(prior)} sessions at this time of day")

    # ---- implied volatility
    def iv(self) -> None:
        o = self.o
        iv_now = self.row.get("atm_iv")
        front_dte = (self.row.get("gex_views") or {}).get("front_dte")
        if not _is_num(iv_now):
            o.skip("iv", "trend_30min", "row carries no at-the-money implied volatility")
        elif front_dte == 0 and self.to_close < 60:
            o.skip("iv", "trend_30min", "final hour of expiry day, the front-book clock is unreliable")
        else:
            target = self.now - timedelta(minutes=30)
            earlier = [r for r in self.s.rows_today if parse_ts(r["ts"]) <= target and _is_num(r.get("atm_iv"))]
            if not earlier or parse_ts(earlier[-1]["ts"]) < target - timedelta(minutes=10):
                o.skip("iv", "trend_30min", "no row with implied volatility about 30 minutes ago")
            else:
                d = (float(iv_now) - float(earlier[-1]["atm_iv"])) * 100.0
                if abs(d) <= IV_FLAT_BAND_PTS:
                    o.put("iv", "trend_30min",
                          f"over the last 30 minutes at-the-money implied volatility stayed within the {IV_FLAT_BAND_PTS:g} vol point flat band, changing {signed(d, 1)} vol points",
                          answer="flat",
                          no_figure="over the last 30 minutes at-the-money implied volatility stayed within the flat band",
                          no_band=f"over the last 30 minutes at-the-money implied volatility changed {signed(d, 1)} vol points")
                elif d > 0:
                    o.put("iv", "trend_30min",
                          f"over the last 30 minutes at-the-money implied volatility rose {d:.1f} vol points, more than the {IV_FLAT_BAND_PTS:g} point flat band",
                          answer="rising",
                          no_figure="over the last 30 minutes at-the-money implied volatility rose, more than the flat band",
                          no_band=f"over the last 30 minutes at-the-money implied volatility rose {d:.1f} vol points")
                else:
                    o.put("iv", "trend_30min",
                          f"over the last 30 minutes at-the-money implied volatility fell {-d:.1f} vol points, more than the {IV_FLAT_BAND_PTS:g} point flat band",
                          answer="falling",
                          no_figure="over the last 30 minutes at-the-money implied volatility fell, more than the flat band",
                          no_band=f"over the last 30 minutes at-the-money implied volatility fell {-d:.1f} vol points")

        self._realized_vs_priced()

        skew = self.row.get("iv_skew") or {}
        pts = skew.get("skew_pts")
        if not _is_num(pts) and _is_num(skew.get("put_side_iv")) and _is_num(skew.get("call_side_iv")):
            pts = (float(skew["put_side_iv"]) - float(skew["call_side_iv"])) * 100.0
        if not _is_num(pts):
            o.skip("iv", "skew", "row carries no put-call skew")
        elif abs(pts) < SKEW_EVEN_BAND_PTS:
            o.put("iv", "skew", f"puts and calls carry about the same implied volatility at equal distance, puts {signed(pts, 1)} vol points against calls, inside the {SKEW_EVEN_BAND_PTS:g} point even band")
        elif pts > 0:
            o.put("iv", "skew", f"puts carry a higher implied volatility than calls at equal distance, by {pts:.1f} vol points")
        else:
            o.put("iv", "skew", f"calls carry a higher implied volatility than puts at equal distance, by {-pts:.1f} vol points")

        ruler = self.row.get("range_ruler") or {}
        used = ruler.get("em_consumed")
        if not _is_num(used):
            em_open = ruler.get("em_open")
            if _is_num(em_open) and em_open > 0 and self.bars:
                hi = max(max(float(x["high"]) for x in self.bars), self.spot)
                lo = min(min(float(x["low"]) for x in self.bars), self.spot)
                used = (hi - lo) / float(em_open)
        if not _is_num(used):
            o.skip("iv", "expected_move_used", "row carries no expected-move ruler")
        elif used < 0.5:
            o.put("iv", "expected_move_used", f"today's range so far has used {used:.1f} of today's expected move, under half of it",
                  answer="under_half", no_figure="today's range so far has used under half of today's expected move",
                  no_band=f"today's range so far has used {used:.1f} of today's expected move")
        elif used <= 1.0:
            o.put("iv", "expected_move_used", f"today's range so far has used {used:.1f} of today's expected move, between half and all of it",
                  answer="half_to_full", no_figure="today's range so far has used between half and all of today's expected move",
                  no_band=f"today's range so far has used {used:.1f} of today's expected move")
        else:
            o.put("iv", "expected_move_used", f"today's range so far has used {used:.1f} times today's expected move, more than all of it",
                  answer="beyond_full", no_figure="today's range so far has used more than all of today's expected move",
                  no_band=f"today's range so far has used {used:.1f} times today's expected move")

    # ---- dealer gamma map
    def gex(self) -> None:
        o = self.o
        gv = self.row.get("gex_views") or {}
        above, below = gv.get("gamma_above_spot"), gv.get("gamma_below_spot")
        if _is_num(above) and _is_num(below) and (above + below) > 0:
            share = float(above) / float(above + below)
            if share > WEIGHT_EVEN_BAND[1]:
                o.put("gex", "weight_side", f"most of the options weight sits above price, {pct(share)} of it",
                      answer="above", no_figure="most of the options weight sits above price",
                      no_band=f"{pct(share)} of the options weight sits above price")
            elif share < WEIGHT_EVEN_BAND[0]:
                o.put("gex", "weight_side", f"most of the options weight sits below price, {pct(1 - share)} of it",
                      answer="below", no_figure="most of the options weight sits below price",
                      no_band=f"{pct(1 - share)} of the options weight sits below price")
            else:
                o.put("gex", "weight_side", f"the options weight is split about evenly above and below price, {pct(share)} above",
                      answer="even", no_figure="the options weight is split about evenly above and below price",
                      no_band=f"{pct(share)} of the options weight sits above price")
        else:
            o.skip("gex", "weight_side", "row carries no gamma above and below spot")

        walls = []
        for name, key in (("call wall", "call_wall"), ("put wall", "put_wall")):
            w = self.row.get(key)
            if _is_num(w):
                walls.append((name, (float(w) - self.spot) / self.sigma))
        if not walls:
            o.put("gex", "air_to_wall", "no heavy strike sits within reach on either side of price", answer="no_wall_in_reach")
        else:
            name, d = min(walls, key=lambda w: abs(w[1]))
            side = "above" if d >= 0 else "below"
            if abs(d) <= WALL_NEAR_SIGMA:
                o.put("gex", "air_to_wall",
                      f"a heavy strike sits within {WALL_NEAR_SIGMA} sigma of price: the {name} {sig(abs(d))} {side} price",
                      answer="heavy_strike_close",
                      no_figure=f"a heavy strike sits within the near-wall rule of price: the {name}, {side} price",
                      no_band=f"the nearest heavy strike is the {name} {sig(abs(d))} {side} price")
            else:
                o.put("gex", "air_to_wall",
                      f"the nearest heavy strike is the {name} {sig(abs(d))} {side} price, more than {WALL_NEAR_SIGMA} sigma away, with open air between",
                      answer="open_air",
                      no_figure=f"the nearest heavy strike is the {name} {side} price, further than the near-wall rule, with open air between",
                      no_band=f"the nearest heavy strike is the {name} {sig(abs(d))} {side} price")

        prev = self._previous_book_row()
        if prev is None:
            o.skip("gex", "since_last_book", "no earlier distinct options book today")
            return
        gap = round((self.now - parse_ts(prev["ts"])).total_seconds() / 60)
        parts = [f"since the last book, {plural(gap, 'minute')} earlier:"]
        h_now, h_prev = self._heaviest_strike(self.row), self._heaviest_strike(prev)
        if h_now is None or h_prev is None:
            parts.append("the heaviest strike could not be compared;")
        elif h_now == h_prev:
            parts.append("the heaviest strike now is the same strike as at the last book;")
        else:
            parts.append("the heaviest strike now is a different strike from the last book;")
        for name, key in (("nearest call wall", "call_wall"), ("nearest put wall", "put_wall")):
            a, b_ = self.row.get(key), prev.get(key)
            if _is_num(a) and _is_num(b_):
                parts.append(f"the {name} is the same strike as at the last book;" if a == b_ else f"the {name} is a different strike from the last book;")
            elif _is_num(a):
                parts.append(f"the {name} appeared since the last book;")
            elif _is_num(b_):
                parts.append(f"the {name} disappeared since the last book;")
            else:
                parts.append(f"there was no {name} at either book;")
        g_now, g_prev = self.row.get("gamma_sign"), prev.get("gamma_sign")
        if isinstance(g_now, str) and isinstance(g_prev, str):
            parts.append("the gamma sign now matches the gamma sign at the last book" if g_now == g_prev
                         else "the gamma sign now differs from the gamma sign at the last book")
        else:
            parts.append("the gamma sign could not be compared")
        o.put("gex", "since_last_book", " ".join(parts))

    def _previous_book_row(self) -> dict | None:
        cur = (self.row.get("meta") or {}).get("book_asof")
        for r in reversed(self.s.rows_today[:-1]):
            asof = (r.get("meta") or {}).get("book_asof")
            if asof and asof != cur:
                return r
        return None

    @staticmethod
    def _heaviest_strike(row: dict) -> float | None:
        mass = (row.get("gex_views") or {}).get("mass_by_strike") or []
        best = None
        for item in mass:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and _is_num(item[0]) and _is_num(item[1]):
                if best is None or abs(item[1]) > best[1]:
                    best = (float(item[0]), abs(float(item[1])))
        return best[0] if best else None

    # ---- options activity
    def options(self) -> None:
        o = self.o
        vols = (self.row.get("gex_views") or {}).get("vol_gross_by_strike") or []
        totals = sorted((float(v[1]) for v in vols if isinstance(v, (list, tuple)) and len(v) >= 2 and _is_num(v[1])), reverse=True)
        total = sum(totals)
        if total <= 0:
            o.skip("options", "activity", "no contracts traded yet today")
            return
        top3 = sum(totals[:3]) / total
        if top3 >= CONCENTRATED_SHARE:
            o.put("options", "activity", f"the three busiest strikes hold {pct(top3)} of today's contracts, most of them",
                  answer="concentrated", no_figure="the three busiest strikes hold most of today's contracts",
                  no_band=f"the three busiest strikes hold {pct(top3)} of today's contracts")
        else:
            o.put("options", "activity", f"today's contracts trade across many strikes; the three busiest hold {pct(top3)} of them, less than half",
                  answer="spread_out", no_figure="today's contracts trade across many strikes; the three busiest hold less than half of them",
                  no_band=f"the three busiest strikes hold {pct(top3)} of today's contracts")

    # ---- stock volume
    def _window_volume(self, bars: list[dict], start: datetime, end: datetime) -> float | None:
        win = bars_between(bars, start, end)
        if len(win) < VOLUME_WINDOW_MIN * 0.8:
            return None
        if not all(_is_num(x.get("volume")) for x in win):
            return None
        return sum(float(x["volume"]) for x in win)

    def _baseline_volumes(self, start_min: int, end_min: int) -> list[float]:
        out = []
        for _, pbars in self.s.prior_bars.items():
            win = slot(pbars, start_min, end_min)
            if len(win) >= VOLUME_WINDOW_MIN * 0.8 and all(_is_num(x.get("volume")) for x in win):
                out.append(sum(float(x["volume"]) for x in win))
        return out

    def volume(self) -> None:
        o = self.o
        if self.since_open < VOLUME_WINDOW_MIN:
            o.skip("volume", "now", f"needs {VOLUME_WINDOW_MIN} minutes of session")
            o.skip("volume", "on_move", f"needs {2 * VOLUME_WINDOW_MIN} minutes of session")
            return
        end = self.now.replace(second=0, microsecond=0)
        start = end - timedelta(minutes=VOLUME_WINDOW_MIN)
        now_vol = self._window_volume(self.bars, start, end)
        base = self._baseline_volumes(_mod(start), _mod(end))
        if now_vol is None:
            o.skip("volume", "now", "today's bars carry no volume for the last 30 minutes")
        elif len(base) < MIN_VOLUME_SESSIONS:
            o.skip("volume", "now", f"needs {MIN_VOLUME_SESSIONS} prior sessions of bars at this time of day, have {len(base)}")
        else:
            rf = rank_frac(now_vol, base)
            band = fifth_band(rf)
            under = sum(1 for x in base if x < now_vol)
            o.put("volume", "now",
                  f"volume over the last 30 minutes is in the {band} for this time of day, higher than {under} of {len(base)} prior sessions",
                  answer={"top fifth": "heavy", "bottom fifth": "light", "middle band": "normal"}[band],
                  no_figure=f"volume over the last 30 minutes is in the {band} for this time of day",
                  no_band=f"volume over the last 30 minutes was higher than {under} of {len(base)} prior sessions at this time of day")

        if self.since_open < 2 * VOLUME_WINDOW_MIN:
            o.skip("volume", "on_move", f"needs {2 * VOLUME_WINDOW_MIN} minutes of session")
            return
        b_start = start - timedelta(minutes=VOLUME_WINDOW_MIN)
        before_vol = self._window_volume(self.bars, b_start, start)
        before_base = self._baseline_volumes(_mod(b_start), _mod(start))
        if now_vol is None or before_vol is None:
            o.skip("volume", "on_move", "today's bars carry no volume for the last 60 minutes")
        elif len(base) < MIN_VOLUME_SESSIONS or len(before_base) < MIN_VOLUME_SESSIONS:
            o.skip("volume", "on_move", f"needs {MIN_VOLUME_SESSIONS} prior sessions of bars, have {min(len(base), len(before_base))}")
        else:
            band_now = fifth_band(rank_frac(now_vol, base))
            band_before = fifth_band(rank_frac(before_vol, before_base))
            o.put("volume", "on_move", f"volume during the move, the last 30 minutes, is in the {band_now} for its time of day, and volume before the move began, the 30 minutes before that, is in the {band_before} for its time of day")

    # ---- momentum
    def momentum(self) -> None:
        o = self.o
        closes = [float(x["close"]) for x in self.bars]
        rsi = wilder_rsi(closes)
        if rsi is None:
            o.skip("momentum", "rsi_1min", f"needs {RSI_PERIOD + 1} finished bars, have {len(closes)}")
        elif rsi > 70:
            o.put("momentum", "rsi_1min", f"the 1-minute RSI is {rsi:.0f}, above 70", answer="above_70",
                  no_figure="the 1-minute RSI is above 70", no_band=f"the 1-minute RSI is {rsi:.0f}")
        elif rsi < 30:
            o.put("momentum", "rsi_1min", f"the 1-minute RSI is {rsi:.0f}, below 30", answer="below_30",
                  no_figure="the 1-minute RSI is below 30", no_band=f"the 1-minute RSI is {rsi:.0f}")
        else:
            o.put("momentum", "rsi_1min", f"the 1-minute RSI is {rsi:.0f}, between 30 and 70", answer="between",
                  no_figure="the 1-minute RSI is between 30 and 70", no_band=f"the 1-minute RSI is {rsi:.0f}")

        c0 = closes[-1] if closes else None
        c10 = close_at(self.bars, self.now - timedelta(minutes=10))
        c20 = close_at(self.bars, self.now - timedelta(minutes=20))
        if self.since_open < 20 or c0 is None or c10 is None or c20 is None:
            o.skip("momentum", "pace", "needs 20 minutes of finished bars")
        else:
            last = abs(c0 - c10) / self.sigma
            prev = abs(c10 - c20) / self.sigma
            if prev <= 0 and last <= 0:
                o.put("momentum", "pace", "the last 10 minutes and the 10 minutes before both moved about the same, close to nothing")
            elif prev <= 0:
                o.put("momentum", "pace", f"the last 10 minutes moved {sig(last)} after the 10 minutes before moved close to nothing, so more than {PACE_BIGGER:g} times as far")
            else:
                ratio = last / prev
                if ratio > PACE_BIGGER:
                    word = f"more than {PACE_BIGGER:g} times as far as"
                elif ratio < PACE_SMALLER:
                    word = "less than two thirds as far as"
                else:
                    word = "between two thirds and 1.5 times as far as"
                o.put("momentum", "pace", f"the last 10 minutes moved {sig(last)}, {word} the 10 minutes before, which moved {sig(prev)}")

        if self.move30 is None:
            o.skip("momentum", "closes", "no 30-minute move to judge")
            o.skip("momentum", "pauses", "no 30-minute move to judge")
            return
        if abs(self.move30) < MOVE_RULE_SIGMA:
            # a quiet read is a fact, not a gap: the questions that read these labels have a
            # "no move" answer, and leaving the labels out would skip those questions instead
            quiet = f"the last 30 minutes moved {sig(abs(self.move30))}, under the {MOVE_RULE_SIGMA} sigma move rule, so there is no move to judge"
            o.put("momentum", "closes", quiet, answer="no_move")
            o.put("momentum", "pauses", quiet, answer="no_move")
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
            return
        ext = start                 # the move's running extreme
        last_ext = -1               # index of the bar that last pushed it
        longest_stall = 0
        deepest = 0.0               # the deepest dip from the running extreme, in price
        for i, x in enumerate(win):
            hi, lo = float(x["high"]), float(x["low"])
            # a dip is measured from the extreme reached before this bar
            deepest = max(deepest, (ext - lo) if up else (hi - ext))
            if up and hi > ext:
                ext, last_ext = hi, i
            elif (not up) and lo < ext:
                ext, last_ext = lo, i
            stall = i - last_ext if last_ext >= 0 else i + 1
            longest_stall = max(longest_stall, stall)
        # the pullback is that dip as a share of the move's full extent over the window,
        # never of the distance travelled so far, which is tiny early on and inflates a wobble
        extent = abs(ext - start)
        max_retrace = deepest / extent if extent > 0 else 0.0
        if max_retrace > 1.0:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} gave back more than all of itself at its deepest pullback, far more than a quarter")
        elif max_retrace >= PULLBACK_SHARE:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} gave back {pct(max_retrace)} of itself at its deepest pullback, more than a quarter")
        elif longest_stall > PAUSE_LONG_MIN:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} paused for {plural(longest_stall, 'minute')} without a new extreme, longer than 10 minutes, and gave back {pct(max_retrace)} of itself, less than a quarter")
        elif longest_stall >= PAUSE_BRIEF_MIN:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} paused for {plural(longest_stall, 'minute')}, between 3 and 10 minutes, and gave back {pct(max_retrace)} of itself, less than a quarter")
        else:
            o.put("momentum", "pauses", f"during the last 30 minutes the move {word} ran without a pause longer than 3 minutes and gave back {pct(max_retrace)} of itself, less than a quarter")

    # ======================================================================
    # The labels added on 2026-09-21: 20 from numbers already on the row or in the
    # side packets and chain cache, 13 designed from the five viewpoints.
    # ======================================================================

    def _move_bar(self) -> float | None:
        """SNDK PRO's move bar: two typical minutes, twice the median 1-minute range of the last 30 bars."""
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

    def _nearest_wall(self) -> tuple[str, float, str] | None:
        """(name, distance in sigma, key) of the nearer wall, or None."""
        walls = []
        for name, key in (("call wall", "call_wall"), ("put wall", "put_wall")):
            w = self.row.get(key)
            if _is_num(w):
                walls.append((name, (float(w) - self.spot) / self.sigma, key))
        return min(walls, key=lambda w: abs(w[1])) if walls else None

    def _atm(self, expiry: str, right: str = "call") -> dict | None:
        cc = self.s.chain_cache
        if not cc:
            return None
        cands = [c for c in cc["contracts"] if c.get("expiry") == expiry and c.get("right") == right and _is_num(c.get("strike"))]
        return min(cands, key=lambda c: abs(float(c["strike"]) - self.spot)) if cands else None

    # ---- context, added
    def context_more(self) -> None:
        o = self.o
        frac = max(0.0, min(1.0, self.since_open / session_minutes(self.now)))
        fifth = min(4, int(frac * 5))
        words = ["first", "second", "third", "fourth", "last"][fifth]
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
        if ago < 10:
            band = "under 10 minutes ago"
        elif ago <= 60:
            band = "between 10 and 60 minutes ago"
        else:
            band = "over 60 minutes ago"
        o.put("context", "time_since_last_move", f"price last made a real move of {MOVE_RULE_SIGMA} sigma within 10 minutes {plural(round(ago), 'minute')} ago, {band}")

    # ---- price, added
    def price_more(self) -> None:
        o = self.o
        pc = self.row.get("prior_close")
        if not _is_num(pc) or pc <= 0:
            o.skip("price", "vs_prior_close", "row carries no prior close")
        else:
            d = (self.spot - float(pc)) / self.sigma
            if abs(d) < MOVE_RULE_SIGMA:
                o.put("price", "vs_prior_close", f"price is within {MOVE_RULE_SIGMA} sigma of last night's close, {signed(d)} sigma from it, an ordinary day so far", answer="ordinary")
            elif abs(d) >= 1.0:
                o.put("price", "vs_prior_close", f"price is {sig(abs(d))} {'above' if d > 0 else 'below'} last night's close, more than 1.0 sigma, an outlier day", answer="outlier_up" if d > 0 else "outlier_down")
            else:
                o.put("price", "vs_prior_close", f"price is {sig(abs(d))} {'above' if d > 0 else 'below'} last night's close, within 1.0 sigma, an ordinary day so far", answer="ordinary")

        if len(self.bars) < 15:
            o.skip("price", "minute_width", "needs 15 finished bars")
        else:
            last = float(self.bars[-1]["high"]) - float(self.bars[-1]["low"])
            med = statistics.median(float(x["high"]) - float(x["low"]) for x in self.bars)
            if med <= 0:
                o.skip("price", "minute_width", "today's typical minute has no range")
            else:
                r = last / med
                o.put("price", "minute_width", f"this minute's range is {r:.1f} times a typical minute today, {'wider than' if r > 2 else 'within'} the 2-times cut")

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
        else:
            whi = max(max(float(x["high"]) for x in b) for b in week)
            wlo = min(min(float(x["low"]) for x in b) for b in week)
            n = len(week)
            if whi - wlo <= 0:
                o.skip("price", "multi_day_position", "the prior sessions' range is zero")
            else:
                dh, dl = (self.spot - whi) / self.sigma, (self.spot - wlo) / self.sigma
                edges = f"{sig(abs(dh))} {'above' if dh > 0 else 'below'} that high and {sig(abs(dl))} {'above' if dl > 0 else 'below'} their low"
                if self.spot > whi:
                    o.put("price", "multi_day_position", f"price is above the last {n} sessions' high, in new ground, {edges}", answer="new_ground_above")
                elif self.spot < wlo:
                    o.put("price", "multi_day_position", f"price is below the last {n} sessions' low, in new ground, {edges}", answer="new_ground_below")
                else:
                    pos = (self.spot - wlo) / (whi - wlo)
                    o.put("price", "multi_day_position", f"price is in the {third(pos)} third of the last {n} sessions' range, {pct(pos)} of the way up from its low to its high, {edges}", answer="inside_the_week")

    # ---- range, added
    def range_more(self) -> None:
        o = self.o
        y = self._session(1)
        bar = self._move_bar()
        if not y or bar is None or not self.bars:
            o.skip("range", "prior_level_touches", "needs yesterday's bars and 10 finished bars today")
        else:
            hi, lo, cl = self._hlc(y)
            counts = {}
            for name, lvl in (("high", hi), ("low", lo), ("close", cl)):
                counts[name] = sum(1 for x in self.bars if float(x["low"]) <= lvl + bar and float(x["high"]) >= lvl - bar)
            total = sum(counts.values())
            # a count of minutes, not of visits: a bar sitting on the level all afternoon counts every minute
            o.put("range", "prior_level_touches",
                  f"price has spent {plural(counts['high'], 'minute')} within reach of yesterday's high, {plural(counts['low'], 'minute')} within reach of yesterday's low and {plural(counts['close'], 'minute')} within reach of yesterday's close today, counting reach as two typical minutes of range",
                  answer="true" if total > 0 else "false")

        pk = self.s.side_packet
        if not pk:
            o.skip("range", "wall_retests", "no side packet at or before now")
            o.skip("range", "high_retests", "no side packet at or before now")
        else:
            walls = [lv for lv in pk.get("levels") or [] if lv.get("role") in ("wall_call", "wall_put") and _is_num(lv.get("price")) and _is_num(lv.get("visits"))]
            if not walls:
                o.skip("range", "wall_retests", "the side packet lists no wall level")
            else:
                lv = min(walls, key=lambda w: abs(float(w["price"]) - self.spot))
                v = int(lv["visits"])
                # the wall level starts at the session's first bar, so its first visit is price arriving,
                # not coming back: the sentence counts visits, the first arrival included
                band = "none" if v == 0 else ("once" if v == 1 else "two or more times")
                o.put("range", "wall_retests", f"price has visited the nearest heavy strike {plural(v, 'time')} today, counting its first arrival, {band}")
            highs = [lv for lv in pk.get("levels") or [] if lv.get("role") == "session_high" and _is_num(lv.get("visits"))]
            if not highs:
                o.skip("range", "high_retests", "the side packet lists no session-high level")
            else:
                # The packet's session-high level starts at the bar that set the high, and that bar
                # always touches it, so its first visit is the setting run. A return is every visit
                # after it: one visit means price has not come back.
                v = max(0, int(highs[0]["visits"]) - 1)
                band = "none" if v == 0 else ("once" if v == 1 else "two or more times")
                o.put("range", "high_retests", f"price has come back to the day's high {plural(v, 'time')} since setting it, {band}")

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
        if r < REALIZED_QUIET:
            answer, shown = "quieter_than_priced", min(r, REALIZED_QUIET - 0.01)
            cut = f"under the {REALIZED_QUIET:g}-times cut, so quieter than priced"
        elif r > REALIZED_WILD:
            answer, shown = "wilder_than_priced", max(r, REALIZED_WILD + 0.01)
            cut = f"over the {REALIZED_WILD:g}-times cut, so wilder than priced"
        else:
            answer, shown = "about_priced", min(max(r, REALIZED_QUIET), REALIZED_WILD)
            cut = f"between the {REALIZED_QUIET:g}-times and {REALIZED_WILD:g}-times cuts, so about as priced"
        # the shown ratio never crosses the cut its verdict names, whatever the rounding does
        o.put("iv", "vs_realized_30",
              f"over the last {w} minutes price's realized movement, from its 1-minute closes, was {sig(realized)}, "
              f"{shown:.2f} times the {sig(priced)} move the options market prices for {w} minutes, {cut}",
              answer=answer)

    # ---- implied volatility, added
    def iv_more(self) -> None:
        o = self.o
        ds = (self.row.get("adaptive_em") or {}).get("down_share")
        if not _is_num(ds):
            o.skip("iv", "move_sides", "row carries no expected-move split")
        else:
            ds = float(ds)
            if ds > 0.6:
                o.put("iv", "move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, skewed to the downside beyond the 60% cut", answer="skewed_down")
            elif ds < 0.4:
                o.put("iv", "move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, skewed to the upside beyond the 60% cut", answer="skewed_up")
            else:
                o.put("iv", "move_sides", f"the day's expected move splits {pct(ds)} down and {pct(1 - ds)} up, even within the 40% to 60% band", answer="even")

        cc = self.s.chain_cache
        exps = (cc or {}).get("expiries") or []
        if not cc:
            o.skip("iv", "term_slope", "the chain cache is not this row's book")
        elif len(exps) < 2:
            o.skip("iv", "term_slope", "the chain cache holds only one expiry")
        else:
            front, nxt = self._atm(exps[0]), self._atm(exps[1])
            if not front or not nxt or not _is_num(front.get("iv")) or not _is_num(nxt.get("iv")):
                o.skip("iv", "term_slope", "no at-the-money implied volatility for both expiries")
            else:
                d = (float(front["iv"]) - float(nxt["iv"])) * 100.0
                if d > IV_FLAT_BAND_PTS:
                    o.put("iv", "term_slope", f"this week's at-the-money implied volatility sits {d:.1f} vol points above next week's, more than the {IV_FLAT_BAND_PTS:g} point flat band", answer="hotter")
                elif d < -IV_FLAT_BAND_PTS:
                    o.put("iv", "term_slope", f"this week's at-the-money implied volatility sits {-d:.1f} vol points below next week's, more than the {IV_FLAT_BAND_PTS:g} point flat band", answer="cooler")
                else:
                    o.put("iv", "term_slope", f"this week's and next week's at-the-money implied volatility are within the {IV_FLAT_BAND_PTS:g} vol point flat band, this week's {abs(d):.1f} vol points {'above' if d >= 0 else 'below'} next week's", answer="level")

    # ---- dealer gamma map, added
    def gex_more(self) -> None:
        o = self.o
        gv = self.row.get("gex_views") or {}
        near = self._nearest_wall()
        share = None
        if near:
            share = gv.get("call_wall_gamma_share" if near[2] == "call_wall" else "put_wall_gamma_share")
        if not near or not _is_num(share):
            o.skip("gex", "wall_thickness", "no nearest wall with a gamma share on the row")
        else:
            share = float(share)
            band = "thick, above the 15% cut" if share >= 0.15 else ("thin, below the 5% cut" if share < 0.05 else "middling, between the 5% thin cut and the 15% thick cut")
            o.put("gex", "wall_thickness", f"the nearest wall, the {near[0]}, holds {pct(share)} of all the options weight on the board, {band}")

        top = gv.get("pin_top_share")
        if not _is_num(top):
            o.skip("gex", "heaviest_strike_grip", "row carries no top-strike share")
        else:
            top = float(top)
            band = "concentrated, above the 20% cut" if top >= 0.20 else ("spread thin, below the 10% cut" if top < 0.10 else "middling, between the 10% and 20% cuts")
            o.put("gex", "heaviest_strike_grip", f"the single heaviest strike holds {pct(top)} of the board's weight, {band}")

        above = (self.row.get("dex_views") or {}).get("dex_above_spot")
        if not _is_num(above):
            o.skip("gex", "delta_weight_side", "row carries no delta split")
        else:
            below = 1.0 - float(above)
            if below > 0.6:
                o.put("gex", "delta_weight_side", f"{pct(below)} of dealers' directional exposure sits at strikes below price, most of it")
            elif below < 0.4:
                o.put("gex", "delta_weight_side", f"{pct(1 - below)} of dealers' directional exposure sits at strikes above price, most of it")
            else:
                o.put("gex", "delta_weight_side", f"dealers' directional exposure is split about evenly, {pct(below)} below price and {pct(1 - below)} above")

        ne = self.row.get("net_exposure") or {}
        cur, prev = ne.get("net_delta_total"), ne.get("prev_close_delta")
        if not _is_num(cur) or not _is_num(prev) or prev <= 0:
            o.skip("gex", "book_vs_yesterday", "row carries no usable delta book against yesterday's close")
        else:
            r = float(cur) / float(prev)
            band = "bigger, above the 1.25 cut" if r > 1.25 else ("smaller, below the 0.8 cut" if r < 0.8 else "about the same, within 0.8 to 1.25")
            o.put("gex", "book_vs_yesterday", f"dealers' directional book is {r:.1f} times its size at yesterday's close, {band}")

        parts, measured = [], False
        for side, k, kt in (("call", "call_wall", "call_wall_tenor"), ("put", "put_wall", "put_wall_tenor")):
            a, b_ = self.row.get(k), self.row.get(kt)
            if _is_num(a) and _is_num(b_):
                measured = True
                parts.append(f"on the {side} side the later-dated book defends {'the same strike as' if a == b_ else 'a different strike from'} today's book")
        if not measured:
            o.skip("gex", "expiry_roll", "no wall on both today's and the later-dated book")
        else:
            o.put("gex", "expiry_roll", "; ".join(parts))

        st = (self.row.get("profile_ladder") or {}).get("state")
        if not isinstance(st, str) or not st:
            o.skip("gex", "ladder_state", "the gamma ladder state was not measured on this row")
        else:
            o.put("gex", "ladder_state", f"the board's gamma ladder is in the {st} state")

        heavy = self._heaviest_strike(self.row)
        prev_row = self.s.prev_last_row
        if heavy is None or not prev_row:
            o.skip("gex", "crowd_change_overnight", "needs the heaviest strike and yesterday's closing row")
        else:
            def oi_at(row, strike):
                for item in (row.get("gex_views") or {}).get("oi_side_by_strike") or []:
                    if isinstance(item, (list, tuple)) and len(item) >= 3 and _is_num(item[0]) and float(item[0]) == strike:
                        return abs(float(item[1])) + abs(float(item[2]))
                return None
            now_oi, then_oi = oi_at(self.row, heavy), oi_at(prev_row, heavy)
            if now_oi is None:
                o.skip("gex", "crowd_change_overnight", "no open interest at the heaviest strike on this row")
            elif then_oi is None:
                # yesterday's closing book did not cover this strike: unknown, never guessed
                o.skip("gex", "crowd_change_overnight", "the heaviest strike sat outside yesterday's closing book, so its open interest then is unknown")
            elif then_oi == 0:
                o.put("gex", "crowd_change_overnight", "the crowd at the heaviest strike carried no open interest at yesterday's close, so it was built today")
            else:
                ch = (now_oi - then_oi) / then_oi
                if ch > 0.10:
                    o.put("gex", "crowd_change_overnight", f"the crowd at the heaviest strike grew {pct(ch)} overnight, more than the 10% cut")
                elif ch < -0.10:
                    o.put("gex", "crowd_change_overnight", f"the crowd at the heaviest strike shrank {pct(-ch)} overnight, more than the 10% cut")
                else:
                    o.put("gex", "crowd_change_overnight", f"the crowd at the heaviest strike held within the 10% cut overnight, changing {signed(ch * 100, 0)}%")

        sh = gv.get("shove") or {}
        up, dn = sh.get("shove_up_margin"), sh.get("shove_down_margin")
        if not _is_num(up) or not _is_num(dn):
            o.skip("gex", "cushion_if_moved", "row carries no shove margins")
        else:
            def word(m):
                m = max(-3.0, min(3.0, float(m)))
                return f"would flip sign, at {m:.1f} times what it is now" if m < 0 else f"would be {m:.1f} times what it is now, the same sign"
            o.put("gex", "cushion_if_moved", f"if price were shoved up by half a normal move for the time left, the dealers' calming gamma {word(up)}; shoved down, it {word(dn)}")

        mass = [(float(m[0]), abs(float(m[1]))) for m in gv.get("mass_by_strike") or [] if isinstance(m, (list, tuple)) and len(m) >= 2 and _is_num(m[0]) and _is_num(m[1])]
        vols = {float(v[0]): (float(v[1]), float(v[2])) for v in gv.get("vol_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[0]) and _is_num(v[1]) and _is_num(v[2])}
        total_mass = sum(m for _, m in mass)
        if not mass or total_mass <= 0:
            o.skip("gex", "strike_ladder", "row carries no per-strike weight")
            o.skip("gex", "cluster_near_price", "row carries no per-strike weight")
            return
        share_of = {k: m / total_mass for k, m in mass}
        contracts = {k: c + p for k, (c, p) in vols.items()}
        top8 = set(sorted(contracts, key=lambda k: -contracts[k])[:8])
        qualifying = [k for k in share_of if share_of[k] >= 0.05 or k in top8]

        def describe(k):
            d = (k - self.spot) / self.sigma
            c, p = vols.get(k, (0.0, 0.0))
            if c + p <= 0:
                side = "no contracts traded there today"
            else:
                cs = c / (c + p)
                side = "mostly calls traded there today" if cs > 0.6 else ("mostly puts traded there today" if cs < 0.4 else "calls and puts traded there evenly today")
            return f"{sig(abs(d))} {'above' if d > 0 else 'below'} price: {pct(share_of[k])} of the board's weight, {side}"

        above = sorted([k for k in qualifying if k > self.spot])[:3]
        below = sorted([k for k in qualifying if k < self.spot], reverse=True)[:3]
        ladder = [describe(k) for k in above] + [describe(k) for k in below]
        if not above:
            ladder.append("the ladder lists no qualifying strike above price")
        if not below:
            ladder.append("the ladder lists no qualifying strike below price")
        o.put("gex", "strike_ladder", ladder)

        heavy_keys = sorted(k for k in share_of if share_of[k] >= 0.05)
        grid = sorted(share_of)
        bands, run = [], []
        for k in grid:
            if k in heavy_keys and (not run or grid.index(k) == grid.index(run[-1]) + 1):
                run.append(k)
            else:
                if len(run) >= 2:
                    bands.append(run)
                run = [k] if k in heavy_keys else []
        if len(run) >= 2:
            bands.append(run)
        near_bands = [b for b in bands if any(abs((k - self.spot) / self.sigma) <= 1.0 for k in b)]
        if not near_bands:
            o.put("gex", "cluster_near_price", "no two adjacent strikes each holding 5% or more of the weight sit within 1 sigma of price, so there is no band near price")
        else:
            b = min(near_bands, key=lambda b: min(abs(k - self.spot) for k in b))
            lo_d, hi_d = (b[0] - self.spot) / self.sigma, (b[-1] - self.spot) / self.sigma
            tot = sum(share_of[k] for k in b)
            if lo_d > 0:
                where = f"spans {sig(lo_d)} to {sig(hi_d)} above price"
            elif hi_d < 0:
                where = f"spans {sig(-hi_d)} to {sig(-lo_d)} below price"
            else:
                where = f"spans from {sig(-lo_d)} below price to {sig(hi_d)} above it, around price"
            o.put("gex", "cluster_near_price", f"a band of {len(b)} adjacent heavy strikes {where} and holds {pct(tot)} of the board's weight")

    # ---- options, added
    def options_more(self) -> None:
        o = self.o
        gv = self.row.get("gex_views") or {}
        vols = [(float(v[1]), float(v[2])) for v in gv.get("vol_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2])]
        ois = [abs(float(v[1])) + abs(float(v[2])) for v in gv.get("oi_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2])]
        calls, puts = sum(c for c, _ in vols), sum(p for _, p in vols)
        traded, standing = calls + puts, sum(ois)
        if traded <= 0 or standing <= 0:
            o.skip("options", "turnover", "no contracts traded or no standing open interest")
        else:
            r = traded / standing
            band = "under 1 times it" if r < 1 else ("between 1 and 3 times it" if r <= 3 else "more than 3 times it")
            o.put("options", "turnover", f"today's contracts traded are {r:.1f} times the standing open position, {band}")
        if traded <= 0:
            o.skip("options", "call_put_split", "no contracts traded yet today")
        else:
            cs = calls / traded
            band = "mostly calls, beyond the 60% cut" if cs > 0.6 else ("mostly puts, calls under the 40% cut" if cs < 0.4 else "an even split within 40% to 60%")
            o.put("options", "call_put_split", f"{pct(cs)} of today's contracts traded near price were calls, {band}")
        nxt = sum(float(v[1]) + float(v[2]) for v in gv.get("vol_side_by_strike_next") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and _is_num(v[1]) and _is_num(v[2]))
        if traded + nxt <= 0:
            o.skip("options", "next_week_share", "no contracts traded in either expiry")
        else:
            s = nxt / (traded + nxt)
            o.put("options", "next_week_share", f"{pct(s)} of contracts traded went to next week's expiry rather than the front one, {'a meaningful share, above the 25% cut' if s >= 0.25 else 'a small share, under the 25% cut'}")

        cc = self.s.chain_cache
        exps = (cc or {}).get("expiries") or []
        atm = self._atm(exps[0]) if cc and exps else None
        if not atm or not _is_num(atm.get("bid")) or not _is_num(atm.get("ask")) or float(atm["ask"]) <= 0:
            o.skip("options", "quote_width", "the chain cache is not this row's book, or no at-the-money quote")
            o.skip("options", "size_showing", "the chain cache is not this row's book, or no at-the-money quote")
            return
        bid, ask = float(atm["bid"]), float(atm["ask"])
        mid = (bid + ask) / 2
        if mid <= 0:
            o.skip("options", "quote_width", "the at-the-money mid is zero")
        else:
            w = (ask - bid) / mid
            o.put("options", "quote_width", f"the at-the-money spread is {pct(w)} of the mid, {'wider than' if w > 0.03 else 'within'} the 3% cut")
        bs, as_ = atm.get("bid_size"), atm.get("ask_size")
        if not _is_num(bs) or not _is_num(as_) or (bs + as_) <= 0:
            o.skip("options", "size_showing", "no sizes on the at-the-money quote")
        else:
            share = float(bs) / float(bs + as_)
            band = "more size on the bid, beyond the 60% cut" if share > 0.6 else ("more size on the offer, bid under the 40% cut" if share < 0.4 else "about even within 40% to 60%")
            o.put("options", "size_showing", f"{pct(share)} of the size showing on the at-the-money quote is on the bid, {band}")

    # ---- volume, added
    def volume_more(self) -> None:
        o = self.o
        win = bars_finished_between(self.bars, self.now - timedelta(minutes=30), self.now)
        if len(win) < 20 or not all(_is_num(x.get("volume")) for x in win):
            o.skip("volume", "by_direction", "needs 30 minutes of finished bars with volume")
        else:
            total = sum(float(x["volume"]) for x in win)
            if total <= 0:
                o.skip("volume", "by_direction", "no volume in the last 30 minutes")
            else:
                up = sum(float(x["volume"]) for x in win if float(x["close"]) > float(x["open"])) / total
                band = "leaning up, beyond the 60% cut" if up > 0.6 else ("leaning down, up-minutes under the 40% cut" if up < 0.4 else "even within 40% to 60%")
                o.put("volume", "by_direction", f"{pct(up)} of the last 30 minutes' volume printed in minutes that closed up, {band}")

        if len(self.bars) < 30 or not all(_is_num(x.get("volume")) for x in self.bars):
            o.skip("volume", "at_price", "needs 30 finished bars with volume")
            o.skip("volume", "at_price_thickness", "needs 30 finished bars with volume")
            return
        bin_w = 0.1 * self.sigma
        bins: dict[int, float] = {}
        for x in self.bars:
            mid = (float(x["high"]) + float(x["low"])) / 2
            bins[int((mid - self.spot) // bin_w)] = bins.get(int((mid - self.spot) // bin_w), 0.0) + float(x["volume"])
        if not bins or sum(bins.values()) <= 0:
            o.skip("volume", "at_price", "no volume today")
            o.skip("volume", "at_price_thickness", "no volume today")
            return
        k = max(bins, key=lambda b: bins[b])
        lo_d, hi_d = k * 0.1, (k + 1) * 0.1
        if lo_d <= 0 <= hi_d:
            o.put("volume", "at_price", "the heaviest-volume stretch of the day includes the current price", answer="at_price")
        elif hi_d <= 0:
            o.put("volume", "at_price", f"the heaviest-volume stretch of the day sits {abs(hi_d):.1f} to {abs(lo_d):.1f} sigma below price", answer="below_price")
        else:
            o.put("volume", "at_price", f"the heaviest-volume stretch of the day sits {lo_d:.1f} to {hi_d:.1f} sigma above price", answer="above_price")

        # How thick the profile is where price sits: each bin blended with its neighbours at 1-2-1
        # weights, then the two bins that meet at price against the heaviest bin of the day.
        smooth = {k: (bins.get(k - 1, 0.0) + 2 * bins.get(k, 0.0) + bins.get(k + 1, 0.0)) / 4
                  for k in range(min(bins) - 1, max(bins) + 2)}
        heaviest = max(smooth.values())
        share = (smooth.get(-1, 0.0) + smooth.get(0, 0.0)) / 2 / heaviest if heaviest > 0 else float("nan")
        if not math.isfinite(share):
            o.skip("volume", "at_price_thickness", "today's volume profile does not give a finite share")
            return
        shown = math.floor(share * 100 + 1e-9)       # rounded down, so it never crosses the cut it names
        if share >= PROFILE_THICK:
            o.put("volume", "at_price_thickness",
                  f"the current price sits in a thick part of today's volume profile: its stretch traded {shown}% as much volume as the day's heaviest stretch, at or above the {pct(PROFILE_THICK)} cut",
                  answer="thick")
        elif share < PROFILE_THIN:
            o.put("volume", "at_price_thickness",
                  f"the current price sits in a thin part of today's volume profile: its stretch traded {shown}% as much volume as the day's heaviest stretch, under the {pct(PROFILE_THIN)} cut",
                  answer="thin")
        else:
            o.put("volume", "at_price_thickness",
                  f"the current price sits in a middling part of today's volume profile: its stretch traded {shown}% as much volume as the day's heaviest stretch, between the {pct(PROFILE_THIN)} and {pct(PROFILE_THICK)} cuts",
                  answer="middling")

    # ---- momentum, added
    def momentum_more(self) -> None:
        o = self.o
        five = [float(self.bars[i]["close"]) for i in range(4, len(self.bars), 5)]
        rsi = wilder_rsi(five)
        if rsi is None:
            o.skip("momentum", "rsi_5min", f"needs {(RSI_PERIOD + 1) * 5} minutes of finished bars")
        elif rsi > 70:
            o.put("momentum", "rsi_5min", f"the 5-minute RSI is {rsi:.0f}, above 70")
        elif rsi < 30:
            o.put("momentum", "rsi_5min", f"the 5-minute RSI is {rsi:.0f}, below 30")
        else:
            o.put("momentum", "rsi_5min", f"the 5-minute RSI is {rsi:.0f}, between 30 and 70")

        pk = self.s.side_packet
        if not pk:
            o.skip("momentum", "stretch", "no side packet at or before now")
        else:
            eps = [e for e in pk.get("episodes") or [] if e.get("subject_ref") == "ind.rsi14" and e.get("open") and _is_num(e.get("bars_in_state"))]
            if not eps:
                o.put("momentum", "stretch", "the 1-minute RSI sits between its overbought and oversold marks right now, so no extreme is being held")
            else:
                e = max(eps, key=lambda e: e["bars_in_state"])
                n = int(e["bars_in_state"])
                band = "under 3 minutes" if n < 3 else ("between 3 and 10 minutes" if n <= 10 else "longer than 10 minutes")
                o.put("momentum", "stretch", f"the 1-minute RSI has been {e.get('state', 'at an extreme')} for {plural(n, 'minute')} without a break, {band}")

        if len(self.bars) < 31 or not all(_is_num(x.get("volume")) for x in self.bars):
            o.skip("momentum", "vwap_slope", "needs 30 minutes of finished bars with volume")
            return
        def vwap_upto(bars):
            pv = sum((float(x["high"]) + float(x["low"]) + float(x["close"])) / 3 * float(x["volume"]) for x in bars)
            v = sum(float(x["volume"]) for x in bars)
            return pv / v if v > 0 else None
        cutoff = self.now - timedelta(minutes=30)
        then_bars = [x for x in self.bars if _t(x) + timedelta(minutes=1) <= cutoff]
        v_now, v_then = vwap_upto(self.bars), vwap_upto(then_bars) if then_bars else None
        if v_now is None or v_then is None:
            o.skip("momentum", "vwap_slope", "no volume to weight the average")
            return
        d = (v_now - v_then) / self.sigma
        if d > 0.05:
            o.put("momentum", "vwap_slope", f"the day's average price has drifted {sig(d)} higher over the last 30 minutes, more than the 0.05 sigma flat cut", answer="drifting_higher")
        elif d < -0.05:
            o.put("momentum", "vwap_slope", f"the day's average price has drifted {sig(-d)} lower over the last 30 minutes, more than the 0.05 sigma flat cut", answer="drifting_lower")
        else:
            o.put("momentum", "vwap_slope", f"the day's average price has stayed within the 0.05 sigma flat cut over the last 30 minutes, moving {signed(d)} sigma", answer="flat")

    # ======================================================================
    # The labels added on 2026-09-23 from the intraday question review: the
    # session's shape, the nearest level and the path to it, where today's option
    # volume sits, and how straight the last 30 minutes ran.
    # ======================================================================

    # ---- range: the session's shape
    def session_shape(self) -> None:
        o = self.o
        if self.since_open < 30 or not self.bars:
            o.skip("range", "session_shape", "needs 30 minutes of session")
            return
        open0 = float(self.bars[0]["open"])
        hi = max(max(float(x["high"]) for x in self.bars), self.spot)
        lo = min(min(float(x["low"]) for x in self.bars), self.spot)
        net = (self.spot - open0) / self.sigma
        up_ext, dn_ext = (hi - open0) / self.sigma, (open0 - lo) / self.sigma
        from_high, from_low = (hi - self.spot) / self.sigma, (self.spot - lo) / self.sigma
        cut = f"{SHAPE_CUT:.2f}"
        if up_ext >= SHAPE_CUT and from_high >= SHAPE_CUT and from_high >= up_ext / 2:
            o.put("range", "session_shape",
                  f"so far today price rose {sig(up_ext)} above the open then gave back {sig(from_high)} of it, so the session has reversed down, past the {cut} sigma cut",
                  answer="reversed_down")
        elif dn_ext >= SHAPE_CUT and from_low >= SHAPE_CUT and from_low >= dn_ext / 2:
            o.put("range", "session_shape",
                  f"so far today price fell {sig(dn_ext)} below the open then recovered {sig(from_low)} of it, so the session has reversed up, past the {cut} sigma cut",
                  answer="reversed_up")
        elif net >= SHAPE_CUT:
            o.put("range", "session_shape",
                  f"so far today price is {sig(net)} above the open, more than the {cut} sigma cut, so the session is rising",
                  answer="rising")
        elif net <= -SHAPE_CUT:
            o.put("range", "session_shape",
                  f"so far today price is {sig(-net)} below the open, more than the {cut} sigma cut, so the session is falling",
                  answer="falling")
        else:
            o.put("range", "session_shape",
                  f"so far today price is {sig(abs(net))} from the open, within the {cut} sigma cut, so the session is flat",
                  answer="flat")

    # ---- range: the nearest level and the path to it
    def nearest_level(self) -> None:
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
        for name, key in (("the call wall", "call_wall"), ("the put wall", "put_wall")):
            w = self.row.get(key)
            if _is_num(w):
                levels.append((name, float(w)))
        if not levels:
            o.skip("range", "nearest_level", "no level to measure against")
            return
        name, lvl = min(levels, key=lambda lv: abs(lv[1] - self.spot))
        d = (self.spot - lvl) / self.sigma
        was_below, is_below = ref < lvl, self.spot < lvl
        side = "above" if is_below else "below"
        if was_below and not is_below:
            o.put("range", "nearest_level",
                  f"price crossed {name} from below in the last 30 minutes and is {sig(d)} above it",
                  answer="crossed_up")
        elif is_below and not was_below:
            o.put("range", "nearest_level",
                  f"price crossed {name} from above in the last 30 minutes and is {sig(-d)} below it",
                  answer="crossed_down")
        elif is_below and any(float(x["high"]) > lvl for x in win):
            o.put("range", "nearest_level",
                  f"in the last 30 minutes price pushed above {name} and is back below it, {sig(-d)} under",
                  answer="crossed_and_back")
        elif not is_below and any(float(x["low"]) < lvl for x in win):
            o.put("range", "nearest_level",
                  f"in the last 30 minutes price pushed below {name} and is back above it, {sig(d)} over",
                  answer="crossed_and_back")
        elif abs(d) <= MOVE_RULE_SIGMA:
            o.put("range", "nearest_level",
                  f"the nearest level is {name}, {sig(abs(d))} {side} price, within the {MOVE_RULE_SIGMA} sigma reach, untouched in the last 30 minutes",
                  answer=f"within_reach_{side}")
        elif abs(d) <= WALL_NEAR_SIGMA:
            o.put("range", "nearest_level",
                  f"the nearest level is {name}, {sig(abs(d))} {side} price, beyond the {MOVE_RULE_SIGMA} sigma reach but within {WALL_NEAR_SIGMA} sigma, untouched in the last 30 minutes",
                  answer="near_but_untouched")
        else:
            o.put("range", "nearest_level",
                  f"no level is within {WALL_NEAR_SIGMA} sigma of price; the nearest is {name}, {sig(abs(d))} {side}",
                  answer="no_level_close")

    # ---- options: where today's volume sits
    def new_activity(self) -> None:
        o = self.o
        vols = [(float(v[0]), float(v[1])) for v in (self.row.get("gex_views") or {}).get("vol_gross_by_strike") or []
                if isinstance(v, (list, tuple)) and len(v) >= 2 and _is_num(v[0]) and _is_num(v[1])]
        total = sum(v for _, v in vols)
        if not vols or total <= 0:
            o.skip("options", "new_activity", "no contracts traded yet today")
            return
        busiest, top = max(vols, key=lambda kv: kv[1])
        share = top / total
        nearest = min(vols, key=lambda kv: abs(kv[0] - self.spot))[0]
        if share <= BUSIEST_STRIKE_SHARE:
            o.put("options", "new_activity",
                  f"today's option volume is spread across strikes, no strike holding more than a fifth of it; the busiest holds {pct(share)}",
                  answer="spread_out")
        elif busiest == nearest:
            o.put("options", "new_activity",
                  f"the busiest strike today is the one nearest price, holding {pct(share)} of the day's option volume, more than a fifth",
                  answer="at_price")
        elif busiest > self.spot:
            o.put("options", "new_activity",
                  f"the busiest strike today sits above price, holding {pct(share)} of the day's option volume, more than a fifth",
                  answer="above_price")
        else:
            o.put("options", "new_activity",
                  f"the busiest strike today sits below price, holding {pct(share)} of the day's option volume, more than a fifth",
                  answer="below_price")

    # ---- momentum: how straight the last 30 minutes ran
    def path_efficiency(self) -> None:
        o = self.o
        win = bars_finished_between(self.bars, self.now - timedelta(minutes=30), self.now)
        if self.move30 is None or len(win) < 20:
            o.skip("momentum", "path_efficiency", "needs 30 minutes of finished bars")
            return
        if abs(self.move30) < MOVE_RULE_SIGMA:
            o.put("momentum", "path_efficiency",
                  f"price stayed within the {MOVE_RULE_SIGMA} sigma move rule over the last 30 minutes, so there is no path to judge",
                  answer="no_move")
            return
        closes = [float(x["close"]) for x in win]
        net = abs(closes[-1] - closes[0])
        travel = sum(abs(b_ - a) for a, b_ in zip(closes[:-1], closes[1:]))
        if net <= 0 or travel <= 0:
            o.skip("momentum", "path_efficiency", "the window's closes netted nothing, so there is no path to judge")
            return
        eff = net / travel
        lead = f"over the last 30 minutes price travelled {travel / net:.1f} times its net move, path efficiency {pct(eff)}"
        if eff >= PATH_ORDERLY:
            o.put("momentum", "path_efficiency", f"{lead}, orderly ({pct(PATH_ORDERLY)} or more)", answer="orderly")
        elif eff >= PATH_CHOPPY:
            o.put("momentum", "path_efficiency", f"{lead}, mixed (between {pct(PATH_CHOPPY)} and {pct(PATH_ORDERLY)})", answer="mixed")
        else:
            o.put("momentum", "path_efficiency", f"{lead}, choppy (under {pct(PATH_CHOPPY)})", answer="choppy")

    # ---- news (optional, supplied by the news desk)
    def news(self) -> None:
        item = self.s.news
        if not item:
            return
        o = self.o
        headline = item.get("headline")
        if not isinstance(headline, str) or not headline.strip():
            o.skip("news", "headline", "news item carries no headline")
            return
        o.put("news", "headline", headline.strip())
        if isinstance(item.get("source"), str):
            o.put("news", "source", item["source"].strip())
        excerpt = item.get("excerpt") or item.get("brief")
        if isinstance(excerpt, str) and excerpt.strip():
            o.put("news", "excerpt", excerpt.strip())
        else:
            o.skip("news", "excerpt", "news item carries no excerpt or brief")
        recent = item.get("recent_headlines") or []
        lines = []
        for r in recent:
            if isinstance(r, dict) and isinstance(r.get("text"), str):
                age = r.get("age")
                lines.append(f"{age}: {r['text'].strip()}" if isinstance(age, str) else r["text"].strip())
            elif isinstance(r, str):
                lines.append(r.strip())
        if lines:
            o.put("news", "recent_headlines", lines)
        else:
            o.put("news", "recent_headlines", ["no earlier headlines on this topic today"])
        if isinstance(item.get("expectation"), str) and item["expectation"].strip():
            o.put("news", "expectation", item["expectation"].strip())
        arrived = item.get("arrived")
        if not isinstance(arrived, str):
            o.skip("news", "age", "news item carries no arrival time")
            o.skip("news", "price_reaction", "news item carries no arrival time")
            return
        try:
            t_arr = parse_ts(arrived)
        except ValueError:
            o.skip("news", "age", "arrival time unreadable")
            o.skip("news", "price_reaction", "arrival time unreadable")
            return
        age_min = (self.now - t_arr).total_seconds() / 60.0
        if age_min < 0:
            o.skip("news", "age", "arrival time is after now")
            o.skip("news", "price_reaction", "arrival time is after now")
            return
        o.put("news", "age", f"the item arrived {plural(round(age_min), 'minute')} ago")
        if age_min < 1:
            o.put("news", "price_reaction", "the item has just arrived and price has had no time to react")
            return
        ref = close_at(self.bars, t_arr)
        since = bars_between(self.bars, t_arr, self.now)
        if ref is None or not since:
            o.skip("news", "price_reaction", "no finished bars since the item arrived")
            return
        d = (self.spot - ref) / self.sigma
        hi = max(float(x["high"]) for x in since)
        lo = min(float(x["low"]) for x in since)
        wander = max(abs(hi - ref), abs(ref - lo)) / self.sigma
        if abs(d) < MOVE_RULE_SIGMA:
            move = f"price is within {MOVE_RULE_SIGMA} sigma of where it was when the item arrived, {signed(d)} sigma from it"
        else:
            move = f"price has {'risen' if d > 0 else 'fallen'} {sig(abs(d))} since the item arrived"
        o.put("news", "price_reaction", f"{move}, and got as far as {sig(wander)} away at its furthest point")
