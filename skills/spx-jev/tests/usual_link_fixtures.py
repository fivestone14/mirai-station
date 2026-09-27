"""Market days for the labels read against the index (labels.usual_link), shared by the leadership and macro
tests: SPX bars, and a market context in which each symbol moves a set multiple of the index, drifts a
little of its own (a different amount each prior day, so a same-clock rank has a spread), and makes any
extra move a test puts in."""
from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime, timedelta

from conftest import DAY, at, bars_from_closes, make_row, prior_sessions
from spx_jev.labels.rulers import SigmaRuler
from spx_jev.row_adapter import labeller_row
from spx_jev.state_builder import MarketContext, Scene

SIGMA = 75.0
PRIOR_DAYS = 10
NOW = at(12, 32, ss=10)
READ_MINUTE = 182                   # the bar that starts at 12:32 finishes after NOW: the first one a read may not see
# Each prior day k drifts (k - 4.5) of this a minute beyond its multiple: its same-clock residuals rank in day order.
# SPY stands for the index and keeps to it.
DRIFT_STEP = 1e-5
NO_DRIFT = ("SPY",)


def prior_drift(k: int) -> float:
    return (k - 4.5) * DRIFT_STEP


def sawtooth(i: int) -> float:
    """The prior sessions' SPX close at minute ``i`` (conftest.prior_sessions)."""
    return 7690.0 + 3 * (i % 20) - 0.05 * i


def index_closes(move_points: float = 0.0) -> list[float]:
    """A full session of SPX closes: the prior sessions' sawtooth until 30 minutes before the read, then a
    steady ``move_points`` over those 30 minutes with a half-point zigzag, held after."""
    start = READ_MINUTE - 31
    return [sawtooth(i) if i <= start else sawtooth(start) + move_points * min(i - start, 30) / 30 + 0.5 * (i % 2)
            for i in range(390)]


def follow(closes: list[float], multiple: float, base: float = 100.0, drift: float = 0.0,
           jumps: dict[int, float] | None = None, wiggle: float = 0.0) -> list[float]:
    """One price a minute: ``multiple`` times the index's log move from the first close, ``drift`` more a
    minute, from each minute in ``jumps`` on that much more, and a ``wiggle`` of its own unrelated to the index."""
    out, extra = [], 0.0
    for i, c in enumerate(closes):
        extra += (jumps or {}).get(i, 0.0)
        out.append(base * math.exp(multiple * math.log(c / closes[0]) + drift * i + extra + wiggle * math.sin(i * 2.3)))
    return out


def known(day: str, series: dict[str, list[float]]) -> dict[str, list[tuple[datetime, float]]]:
    """Each price known when its minute finished."""
    start = at(9, 30, day)
    return {s: [(start + timedelta(minutes=i + 1), v) for i, v in enumerate(vals)] for s, vals in series.items()}


def prior_markets(multiples: dict[str, float], wiggles: dict[str, float] | None = None) -> tuple[dict, dict]:
    """PRIOR_DAYS prior sessions of SPX bars (newest first) and their market contexts."""
    bars = prior_sessions(PRIOR_DAYS)
    closes = [sawtooth(i) for i in range(390)]
    markets = {}
    for k, day in enumerate(reversed(list(bars))):
        markets[day] = MarketContext(known(day, {s: follow(closes, m, drift=0.0 if s in NO_DRIFT else prior_drift(k), wiggle=(wiggles or {}).get(s, 0.0))
                                                 for s, m in multiples.items()}))
    return bars, markets


def scene_with(closes: list[float], today: dict[str, list[float]], multiples: dict[str, float], now: datetime = NOW,
               wiggles: dict[str, float] | None = None, state_dir=None) -> Scene:
    """A read at ``now`` on DAY with the anchor set by 09:40, today's market ``today`` and prior sessions in
    which each symbol moved its ``multiples`` of the index."""
    pbars, pmarkets = prior_markets(multiples, wiggles)
    bars = [b for b in bars_from_closes(closes) if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= now]
    spot = float(bars[-1]["close"])
    rows = [labeller_row(make_row(at(9, 32), closes[2], sigma=SIGMA)), labeller_row(make_row(now, spot, sigma=SIGMA))]
    return Scene(row=rows[-1], rows_today=rows, bars=bars, prior_bars=pbars, now=now, sigma=SIGMA,
                 market=MarketContext(known(DAY, today)), prior_rulers={d: SigmaRuler(SIGMA, "anchor") for d in pbars},
                 prior_markets=pmarkets, state_dir=state_dir)


def without(scene: Scene, **over) -> Scene:
    return replace(scene, **over)


def shape(sentence: str) -> str:
    """The sentence with every number written as #, so a test pins its words and verdicts."""
    return re.sub(r"[+-]?\d+(\.\d+)?", "#", sentence)
