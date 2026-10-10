"""The flat zone: how many index points either way count as flat for a box, sized at every read from the day's own tape and
the VIX, one rule for every box of every lane.

A box is a sum's window: the live lane's 30- and 60-minute end-price sums and its 30-minute average-price call, the opening
lane's 10-minute call, the premarket lane's 10- and 30-minute sums from the settled open (lane.py). For a box of W minutes
starting at minute s of the session:

    zone = max(the lane's floor, multiplier x shrink x sqrt(W) x level x clock_factor)

    shrink         integral.factor(W) for a box graded on the average price over its window (0.5918 at 30 minutes, 0.6205
                   at 10), 1 for one graded on the close at its mark: the average of a random walk spreads that much less
                   than its end
    level          today's typical 1-minute move in points as the read can know it (level): the VIX's view, counted as
                   VIX_TRUST_MINUTES of tape, averaged with every minute finished before the read, each minute's squared
                   move divided by the clock shape at its minute and weighted half per RECENCY_HALF_LIFE_MIN of age
    the VIX's view the day's first close x the VIX known by VIX_OPEN_KNOWN (its 09:31 bar) / 100 / the root of the minutes in
                   a year (vix_level); failing that the VIX known at the read against the newest close; failing that the
                   median realized level of the sessions before (realized_level)
    clock shape    the mean squared 1-minute move at each minute of the session over the last NIGHT_RANK_COUNT full sessions,
                   each scaled to its own mean, smoothed over SHAPE_SMOOTH_MIN minutes either side past the first
                   OPENING_MINUTES, normalised to mean 1 (clock_shape); it needs SAME_CLOCK_MIN_SESSIONS of them
    clock_factor   the root of the clock shape's mean over the window, each minute weighted ((W - j + 1) / W)^2 on the
                   average price, evenly at the end price (clock_factor)
    multiplier     refit each day from the same prior sessions so the box's share of flat windows is its target (the percent
                   in lane.horizons, or the lane's average_flat_pct): the target quantile of |move| / (shrink x sqrt(W) x
                   level x clock_factor) over every start a read of the lane could make on those sessions, each sized as a
                   read then could have known it (multiplier); it needs MIN_RANK_SESSIONS sessions

The opening lane's five-way box is banded from its call's zone: flat within the call's zone widened back by shrink, big
beyond that times a ratio refit the same way to the big steps' share (BIG_PCT). The premarket lane's boxes run from the
settled open, before any minute of the day has printed: their level is the VIX's view alone, the prior close x the prior
session's closing VIX (pre_open_level). A lane whose average-price sum has no target of its own (the premarket's) narrows
its primary's zone by shrink.

Inputs: SPX minute bars and the VIX alone (state_builder.load_bars, load_market_context), never the options reading, never
SPY. Every number is in index points. A read is told its zones (hour.py: the sums' text and context line) and stamps them on
its sum record under RULE_VERSION; the grader reads the stamp (grade.zones_of), so a call is graded against the number JEV
saw, and sizes a read from before this rule again from what was on file at it, which is the same number.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from . import integral
from .cuts import (MIN_RANK_SESSIONS, NIGHT_RANK_COUNT, RECENCY_HALF_LIFE_MIN, SAME_CLOCK_MIN_SESSIONS, TAPE_DOWN_BIG_PCT,
                   TAPE_UP_BIG_PCT, VIX_TRUST_MINUTES)
from .labels.measures import ET, ONE_MINUTE, SETTLED_OPEN_BAR, bar_time
from .labels.ranks import percentile
from .lane import LIVE, RECORD, Lane
from .sessions import SESSION_OPEN, previous_trading_day, session_close
from .state_builder import MarketContext, load_bars, load_market_context, prior_bar_days

RULE_VERSION = 1                 # bump when the sizing changes: a record's stamp from another rule is sized again
SESSION_MINUTES = 390
MINUTES_IN_A_YEAR = 525_600
SHAPE_SMOOTH_MIN = 5             # the clock shape is smoothed over this many minutes either side
OPENING_MINUTES = 5              # except its first minutes, whose moves fall off too fast to smooth
VIX = "$VIX"
VIX_OPEN_KNOWN = time(9, 32)     # the VIX's 09:31 bar is known when it finishes
BIG_PCT = TAPE_DOWN_BIG_PCT + TAPE_UP_BIG_PCT   # the five-way box's big steps' share: its big line's target
OPEN_MINUTE = SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
SETTLED_OPEN_MINUTE = SETTLED_OPEN_BAR.hour * 60 + SETTLED_OPEN_BAR.minute - OPEN_MINUTE


@dataclass(frozen=True)
class Box:
    minutes: int
    average: bool            # graded on the average price over the window (shrunk), else on the close at its mark
    flat_pct: float          # the share of its windows on the prior sessions that should land flat


@dataclass(frozen=True)
class Session:
    """One past session as the zone reads it, everything as the session's own reads could have known it."""
    day: str
    closes: tuple[float | None, ...]     # the close of the bar that started each minute of the session, None where there is none
    shape: tuple[float, ...] | None      # the clock shape from the sessions before it
    vix: MarketContext | None            # its VIX series, for the view at the open
    vix_level: float | None              # the VIX's view of its 1-minute move at the open
    realized_level: float | None         # the median realized level of the sessions before it, the last fallback

    def view(self) -> float | None:
        """The VIX's view of the session's 1-minute move as a read on it stood on it, else the realized median."""
        return self.vix_level if self.vix_level is not None else self.realized_level


_SESSIONS: dict[tuple[str, str], Session] = {}   # a finished session's files never change, so one process reads each once


def minute_index(t: datetime | time) -> int:
    """A moment's minute of the session: 0 at the open."""
    t = t.astimezone(ET) if isinstance(t, datetime) else t
    return t.hour * 60 + t.minute - OPEN_MINUTE


def closes_by_minute(bars: list[dict]) -> tuple[float | None, ...]:
    out: list[float | None] = [None] * SESSION_MINUTES
    for b in bars:
        k = minute_index(bar_time(b))
        if 0 <= k < SESSION_MINUTES:
            out[k] = float(b["close"])
    return tuple(out)


def squared_moves(closes: tuple[float | None, ...]) -> list[float | None]:
    """Each minute's squared close-to-close move, None at the first minute and wherever a close is missing."""
    return [None if k == 0 or closes[k] is None or closes[k - 1] is None else (closes[k] - closes[k - 1]) ** 2
            for k in range(SESSION_MINUTES)]


def clock_shape(sessions: list[tuple[float | None, ...]]) -> tuple[float, ...] | None:
    """How a session's minute-by-minute movement is shaped by the clock: the mean squared 1-minute move at each minute over
    ``sessions``, each scaled to its own mean so a wild day counts like a quiet one, smoothed past the opening minutes,
    mean 1; the opening minute stands at the first move's. None under SAME_CLOCK_MIN_SESSIONS sessions."""
    scaled = []
    for closes in sessions:
        sq = squared_moves(closes)
        have = [x for x in sq if x is not None]
        if have and sum(have) > 0:
            mean = sum(have) / len(have)
            scaled.append([None if x is None else x / mean for x in sq])
    if len(scaled) < SAME_CLOCK_MIN_SESSIONS:
        return None
    raw = []
    for k in range(1, SESSION_MINUTES):
        xs = [s[k] for s in scaled if s[k] is not None]
        raw.append(sum(xs) / len(xs) if xs else 1.0)
    edge = lambda i: raw[min(max(i, 0), len(raw) - 1)]     # the window past either end repeats the end minute
    smooth = [sum(edge(i + d) for d in range(-SHAPE_SMOOTH_MIN, SHAPE_SMOOTH_MIN + 1)) / (2 * SHAPE_SMOOTH_MIN + 1)
              for i in range(len(raw))]
    smooth[:OPENING_MINUTES] = raw[:OPENING_MINUTES]
    mean = sum(smooth) / len(smooth)
    shape = [x / mean for x in smooth]
    return (shape[0], *shape)


def realized_level(sessions: list[tuple[tuple[float | None, ...], tuple[float, ...] | None]]) -> float | None:
    """The median over ``sessions`` (each its closes and the clock shape it stood on) of each one's realized 1-minute move
    in points, its clock taken out by its own shape."""
    levels = []
    for closes, shape in sessions:
        xs = [x / shape[k] for k, x in enumerate(squared_moves(closes)) if x is not None] if shape else []
        if xs:
            levels.append(math.sqrt(sum(xs) / len(xs)))
    return statistics.median(levels) if levels else None


def vix_level(price: float | None, vix: float | None) -> float | None:
    """The VIX's view of a 1-minute move in points: ``price`` x ``vix`` / 100 over the root of the minutes in a year."""
    if price is None or vix is None:
        return None
    return price * vix / 100.0 / math.sqrt(MINUTES_IN_A_YEAR)


def vix_series(market: MarketContext | None) -> MarketContext | None:
    """The VIX alone out of a day's market context, the one series the zone reads of it."""
    pts = (market.known.get(VIX) if market else None) or []
    return MarketContext({VIX: pts}) if pts else None


def first_close(closes: tuple[float | None, ...]) -> float | None:
    return next((c for c in closes if c is not None), None)


def levels_by_minute(closes: tuple[float | None, ...], shape: tuple[float, ...], vix: float) -> list[float]:
    """The level a read at each minute of the session could know, by minute (level at every start at once)."""
    decay = 0.5 ** (1.0 / RECENCY_HALF_LIFE_MIN)
    tape = weight = 0.0
    out = []
    for s in range(SESSION_MINUTES):
        out.append(math.sqrt((VIX_TRUST_MINUTES * vix ** 2 + tape) / (VIX_TRUST_MINUTES + weight)))
        # the move ending at bar s is known at minute s + 1, the newest a read then has, at full weight
        tape, weight = tape * decay, weight * decay
        if s and closes[s] is not None and closes[s - 1] is not None:
            tape += (closes[s] - closes[s - 1]) ** 2 / shape[s]
            weight += 1.0
    return out


def level(closes: tuple[float | None, ...], shape: tuple[float, ...], vix: float, read_minute: int) -> float:
    """Today's typical 1-minute move in points at minute ``read_minute`` of the session: the VIX's view ``vix``, counted as
    VIX_TRUST_MINUTES of tape, averaged with the squared moves of the minutes finished by then, each divided by the clock
    shape at its minute and weighted half per RECENCY_HALF_LIFE_MIN of age."""
    return levels_by_minute(closes, shape, vix)[min(max(read_minute, 0), SESSION_MINUTES - 1)]


def shrink(minutes: int, average: bool) -> float:
    return integral.factor(minutes) if average else 1.0


def clock_factor(shape: tuple[float, ...], start: int, minutes: int, average: bool) -> float:
    """The root of the clock shape's mean over the ``minutes`` from ``start``: on the average price each minute weighted by
    its share of the average, ((W - j + 1) / W)^2, so the first minutes count most; evenly at the end price."""
    weights = [((minutes - j + 1) / minutes) ** 2 for j in range(1, minutes + 1)] if average else [1.0] * minutes
    return math.sqrt(sum(w * shape[start + j] for j, w in enumerate(weights)) / sum(weights))


def sigma(shape: tuple[float, ...], start: int, minutes: int, average: bool, lvl: float) -> float:
    """The spread of a box's move, in points, before the multiplier: shrink x sqrt(W) x level x clock_factor."""
    return shrink(minutes, average) * math.sqrt(minutes) * lvl * clock_factor(shape, start, minutes, average)


def move(closes: tuple[float | None, ...], start: int, minutes: int, average: bool) -> float | None:
    """The box's move from the close before ``start``: the mean of the window's closes, or the last, less it; None where a
    close is missing."""
    window = closes[start:start + minutes]
    if start < 1 or closes[start - 1] is None or len(window) < minutes or any(c is None for c in window):
        return None
    return (sum(window) / minutes if average else window[-1]) - closes[start - 1]


def session(state_dir: Path | str, day: str) -> Session:
    """A finished session as the zone reads it, read once per process."""
    key = (str(state_dir), day)
    if key not in _SESSIONS:
        prior = [closes_by_minute(b) for b in prior_bar_days(Path(state_dir), day, NIGHT_RANK_COUNT).values()]
        shape = clock_shape(prior)
        closes = closes_by_minute(load_bars(Path(state_dir), day))
        vix = vix_series(load_market_context(Path(state_dir), day))
        at_open = vix.last(VIX, datetime.combine(datetime.fromisoformat(day).date(), VIX_OPEN_KNOWN, tzinfo=ET)) if vix else None
        if at_open is None and vix:
            at_open = vix.first(VIX)                 # no opening print: the day's first, as a read falls to the VIX at the read
        _SESSIONS[key] = Session(day, closes, shape, vix, vix_level(first_close(closes), at_open), realized_level([(c, shape) for c in prior]))
    return _SESSIONS[key]


def pre_open_level(state_dir: Path | str, day: str) -> float | None:
    """The VIX's view before ``day``'s open: the prior session's close x its closing VIX, the level a read before any minute
    of the day has printed stands on."""
    prev = session(state_dir, previous_trading_day(datetime.fromisoformat(day).date()).isoformat())
    last = next((k for k in range(SESSION_MINUTES - 1, -1, -1) if prev.closes[k] is not None), None)
    if last is None or prev.vix is None:
        return None
    finish = datetime.combine(datetime.fromisoformat(prev.day).date(), SESSION_OPEN, tzinfo=ET) + (last + 1) * ONE_MINUTE
    return vix_level(prev.closes[last], prev.vix.last(VIX, finish))


def fit_starts(lane: Lane, minutes: int) -> range:
    """The starts a read of the lane could make on a session, as minutes of it: the settled open alone on a lane graded from
    it, every minute of a scheduled lane's reads, else every start with a whole window before the close."""
    if lane.graded_from_settled_open:
        return range(SETTLED_OPEN_MINUTE + 1, SETTLED_OPEN_MINUTE + 2)
    if lane.schedule:
        return range(minute_index(time.fromisoformat(lane.schedule[0])), minute_index(time.fromisoformat(lane.schedule[-1])) + 1)
    return range(1, SESSION_MINUTES - minutes + 1)


def multiplier(ratios: list[list[float]], flat_pct: float) -> float | None:
    """The box's multiplier from each prior session's |move| over sigma at its starts: the quantile that lands ``flat_pct``
    of them inside; None under MIN_RANK_SESSIONS sessions."""
    sessions = [r for r in ratios if r]
    if len(sessions) < MIN_RANK_SESSIONS:
        return None
    return percentile([x for r in sessions for x in r], flat_pct / 100.0)


class Zones:
    """One lane's zones on one day, sized for any read of it (at): the day's clock shape and VIX view from the sessions before
    it, and each box's multiplier refit from the same sessions, each sized as its own reads could have known it."""

    def __init__(self, state_dir: Path | str, day: str, lane: Lane = LIVE) -> None:
        self.state_dir, self.day, self.lane = Path(state_dir), day, lane
        self.priors = [session(state_dir, d) for d in prior_bar_days(self.state_dir, day, NIGHT_RANK_COUNT)]
        self.shape = clock_shape([s.closes for s in self.priors])
        self.realized = realized_level([(s.closes, s.shape or self.shape) for s in self.priors])
        self.vix = vix_series(load_market_context(self.state_dir, day))
        self.boxes = {qid: Box(minutes, False, float(pct)) for qid, (minutes, pct) in lane.horizons.items() if pct != RECORD}
        if lane.average and lane.average_flat_pct is not None:
            self.boxes[lane.average] = Box(lane.horizons[lane.primary][0], True, float(lane.average_flat_pct))
        self._multipliers: dict[str, float | None] = {}
        self._big: float | None = None

    def _view(self, s: Session) -> float | None:
        return pre_open_level(self.state_dir, s.day) or s.realized_level if self.lane.graded_from_settled_open else s.view()

    def _ratios(self, box: Box, big_of: Box | None = None) -> list[list[float]]:
        """Per prior session, |move| over sigma at each of the lane's starts; with ``big_of``, the end-price move over the
        five-way box's flat line (``big_of``'s zone widened back by shrink) instead, for the big line's ratio."""
        out = []
        for s in self.priors:
            view = self._view(s) if s.shape else None
            if view is None:
                continue
            lvls = levels_by_minute(s.closes, s.shape, view) if not self.lane.graded_from_settled_open else None
            ratios = []
            for start in fit_starts(self.lane, box.minutes):
                lvl = lvls[start] if lvls else view
                if big_of is None:
                    g, line = move(s.closes, start, box.minutes, box.average), sigma(s.shape, start, box.minutes, box.average, lvl)
                else:
                    g = move(s.closes, start, box.minutes, False)
                    line = self.multiplier(self.lane.average) * sigma(s.shape, start, big_of.minutes, True, lvl) / shrink(big_of.minutes, True)
                if g is not None and line > 0:
                    ratios.append(abs(g) / line)
            out.append(ratios)
        return out

    def multiplier(self, qid: str) -> float | None:
        if qid not in self._multipliers:
            self._multipliers[qid] = multiplier(self._ratios(self.boxes[qid]), self.boxes[qid].flat_pct)
        return self._multipliers[qid]

    def big_ratio(self) -> float | None:
        """The five-way box's big line over its flat line, refit so the big steps land their share (BIG_PCT) of the windows."""
        if self._big is None and self.multiplier(self.lane.average) is not None:
            self._big = multiplier(self._ratios(self.boxes[self.lane.average], big_of=self.boxes[self.lane.average]), 100.0 - BIG_PCT)
        return self._big

    def level_at(self, t0: datetime, closes: tuple[float | None, ...]) -> float | None:
        """The level a read at ``t0`` stands on: before the open the VIX's view from the prior close; else today's tape with
        the VIX's view at the open, or at the read, or the realized median."""
        if self.lane.graded_from_settled_open:
            return pre_open_level(self.state_dir, self.day) or self.realized
        day = t0.astimezone(ET).date()
        known = min(t0, datetime.combine(day, VIX_OPEN_KNOWN, tzinfo=ET))      # a read before the opening print cannot see it
        view = vix_level(first_close(closes), self.vix.last(VIX, known) if self.vix else None)
        if view is None:
            newest = next((c for c in reversed(closes) if c is not None), None)
            view = vix_level(newest, self.vix.last(VIX, t0) if self.vix else None)
        if view is None:
            view = self.realized
        return level(closes, self.shape, view, minute_index(t0)) if view is not None else None

    def at(self, t0: datetime, bars: list[dict]) -> dict[str, float] | str:
        """Every sum's zone in points for a read whose boxes start at ``t0`` (grade.horizon_start), from the bars finished by
        then, each box's window cut at the close as its grade is: ``{qid: points}`` and, on the opening lane, the five-way
        box's flat line under its own id and its big line under ``big``. Why there is none when it cannot be sized."""
        if self.shape is None:
            return f"fewer than {SAME_CLOCK_MIN_SESSIONS} prior full sessions on file for the clock shape"
        left = int((session_close(t0) - t0).total_seconds() // 60)
        if left <= 0:
            return "the session has closed"
        closes = closes_by_minute([b for b in bars if bar_time(b) + ONE_MINUTE <= t0])
        lvl = self.level_at(t0, closes)
        if lvl is None:
            return "no VIX on file for the day and no realized level from the prior sessions"
        start, out = minute_index(t0), {}
        for qid, box in self.boxes.items():
            k = self.multiplier(qid)
            if k is None:
                return f"fewer than {MIN_RANK_SESSIONS} prior sessions sized for {qid}"
            minutes = min(box.minutes, left)
            out[qid] = round(max(self.lane.zone_floor, k * sigma(self.shape, start, minutes, box.average, lvl)), 2)
        primary = self.lane.primary
        if self.lane.average and self.lane.average not in out:
            out[self.lane.average] = round(integral.factor(min(self.boxes[primary].minutes, left)) * out[primary], 2)
        if self.lane.horizons[primary][1] == RECORD:
            big = self.big_ratio()
            if big is None:
                return f"fewer than {MIN_RANK_SESSIONS} prior sessions sized for {primary}"
            out[primary] = round(out[self.lane.average] / shrink(min(self.lane.horizons[primary][0], left), True), 2)
            out["big"] = round(big * out[primary], 2)
        return out


def band(zones: dict[str, float], lane: Lane) -> dict[str, float]:
    """The five-way box's bands in points, as the record stamps them: flat within the first, big beyond the second."""
    return {"flat_points": zones[lane.primary], "big_points": zones["big"]}


def stamp(zones: dict[str, float]) -> dict:
    """The zones as a sum record carries them, keyed by this rule."""
    return {**zones, "rule": RULE_VERSION}


def stamped(rec: dict) -> dict[str, float] | None:
    """The zones a sum record was told under this rule, else None."""
    z = rec.get("zone")
    return {k: v for k, v in z.items() if k != "rule"} if isinstance(z, dict) and z.get("rule") == RULE_VERSION else None
