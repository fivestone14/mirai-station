"""The price-only baseline: the forecast the learning loop measures JEV against, with no JEV in it.

Two forecasts per horizon, each counted once on the qualifying SPX sessions and frozen for the trial
in ``spec/baseline.json`` (spec/fit_baseline.py, read only):

    E_clock   how often price ended up, flat or down in this phase of the day: clock.py's five phases,
              each shrunk toward the whole day's shares with SHRINK reads' weight
    E_state   the same, split by the tercile of today's movement so far,
                  move_so_far = |spot - open| / (sigma * sqrt(the share of the session gone)),
              each phase-and-tercile cell shrunk toward its phase's E_clock odds with SHRINK reads' weight

Every read is replayed as the clock replays it (clock.replayed_reads): every 10 minutes from 09:32 on
the newest diary row no more than 10 minutes old, graded by the live grader on that row's spot and
sigma, so the baseline forecasts exactly the outcome the sums are graded on. A qualifying session has
the current sigma definition on its diary (``sigma_live``), at least MIN_ROWS diary rows and a full
day of bars.

The fit also replays every qualifying session left out in turn (leave one day out) and records each
forecast's out-of-sample day-mean log loss. That decides the reference per horizon: E_state when it
beat E_clock out of sample, else E_clock (pool.py calibrates whichever it is).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from .clock import OUTCOMES, PHASES, SHRINK, phase_of
from .scores import floored, log_loss, losses
from .sessions import session_minutes, session_open
from .state_builder import parse_ts

BASELINE_FILE = Path(__file__).resolve().parent.parent / "spec" / "baseline.json"
MIN_ROWS = 200
TERCILES = ("low", "middle", "high")


def move_so_far(spot: float, day_open: float, sigma: float, now: datetime) -> float | None:
    """How far price stands from the day's open, in sigma scaled to the share of the session gone, so a
    quarter-sigma at 10:00 and a half-sigma at 13:00 read alike. None before the open."""
    gone = (now - session_open(now)).total_seconds() / 60.0 / session_minutes(now)
    if gone <= 0 or sigma <= 0:
        return None
    return abs(spot - day_open) / (sigma * math.sqrt(min(gone, 1.0)))


def tercile_of(x: float, cuts: list[float]) -> str:
    return TERCILES[bisect.bisect_right(cuts, x)]


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo)


def with_move(reads: list[dict], day_open: float) -> list[dict]:
    """The replayed reads with each one's move so far."""
    return [{**r, "move_so_far": move_so_far(r["spot"], day_open, r["sigma"], parse_ts(r["row_ts"]))} for r in reads]


def fit(days: dict[str, list[dict]], horizons: list[str]) -> dict:
    """The tables from ``{day: reads}`` (with_move's reads): whole-day, per-phase and per-cell outcome
    counts per horizon, and the tercile cut points of the move so far over every read."""
    moves = [r["move_so_far"] for reads in days.values() for r in reads if r["move_so_far"] is not None]
    cuts = [round(_quantile(moves, 1 / 3), 6), round(_quantile(moves, 2 / 3), 6)] if moves else [0.0, 0.0]
    empty = lambda: {o: 0 for o in OUTCOMES}
    clock = {h: {p[0]: empty() for p in PHASES} for h in horizons}
    state = {h: {p[0]: {t: empty() for t in TERCILES} for p in PHASES} for h in horizons}
    for reads in days.values():
        for r in reads:
            for h, band in r["bands"].items():
                if h not in clock:
                    continue
                clock[h][r["phase"]][band] += 1
                if r["move_so_far"] is not None:
                    state[h][r["phase"]][tercile_of(r["move_so_far"], cuts)][band] += 1
    return {"tercile_cuts": cuts, "clock": clock, "state": state}


def clock_odds(tables: dict, h: str, phase: str) -> dict[str, float]:
    """E_clock: the phase's counts shrunk toward the whole day's shares."""
    whole = {o: sum(c[o] for c in tables["clock"][h].values()) for o in OUTCOMES}
    n_whole = sum(whole.values())
    base = {o: whole[o] / n_whole for o in OUTCOMES} if n_whole else {o: 1.0 / 3 for o in OUTCOMES}
    here = tables["clock"][h][phase]
    n = sum(here.values())
    return {o: (here[o] + SHRINK * base[o]) / (n + SHRINK) for o in OUTCOMES}


def state_odds(tables: dict, h: str, phase: str, move: float | None) -> dict[str, float]:
    """E_state: the phase-and-tercile cell's counts shrunk toward the phase's E_clock odds; without a
    move so far, the phase's odds."""
    ph = clock_odds(tables, h, phase)
    if move is None:
        return ph
    cell = tables["state"][h][phase][tercile_of(move, tables["tercile_cuts"])]
    n = sum(cell.values())
    return {o: (cell[o] + SHRINK * ph[o]) / (n + SHRINK) for o in OUTCOMES}


def whole_day_odds(tables: dict, h: str) -> dict[str, float]:
    whole = {o: sum(c[o] for c in tables["clock"][h].values()) for o in OUTCOMES}
    n = sum(whole.values())
    return {o: whole[o] / n for o in OUTCOMES} if n else {o: 1.0 / 3 for o in OUTCOMES}


def _day_scores(tables: dict, reads: list[dict], h: str) -> dict[str, dict[str, float]] | None:
    """Each forecast's mean three-way, move and direction log loss over one day's graded reads."""
    graded = [r for r in reads if h in r["bands"]]
    if not graded:
        return None
    out = {}
    for name, f in (("whole_day", lambda r: whole_day_odds(tables, h)),
                    ("clock", lambda r: clock_odds(tables, h, r["phase"])),
                    ("state", lambda r: state_odds(tables, h, r["phase"], r["move_so_far"]))):
        total = move = direction = 0.0
        for r in graded:
            p, y = floored(f(r)), r["bands"][h]
            lm, ld = losses(p, y)
            total, move, direction = total + log_loss(p, y), move + lm, direction + ld
        n = len(graded)
        out[name] = {"log_loss": total / n, "move": move / n, "direction": direction / n}
    return out


def leave_one_day_out(days: dict[str, list[dict]], horizons: list[str]) -> dict[str, dict[str, dict]]:
    """``{h: {day: {forecast: day-mean losses}, "reads": n}}``: each day scored by tables fitted on every
    other day, tercile cuts included."""
    out: dict[str, dict[str, dict]] = {h: {} for h in horizons}
    for d in sorted(days):
        tables = fit({k: v for k, v in days.items() if k != d}, horizons)
        for h in horizons:
            s = _day_scores(tables, days[d], h)
            if s:
                out[h][d] = {**s, "reads": sum(1 for r in days[d] if h in r["bands"])}
    return out


def rule_hash(doc: dict) -> str:
    """The frozen tables' fingerprint: sessions, cuts and counts. A change to any of them is a new baseline."""
    keep = {k: doc[k] for k in ("version", "sessions", "tercile_cuts", "clock", "state", "reference")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


class Baseline:
    """The frozen tables as the loop reads them at every read (pool.py): E_clock and its long-run shares.
    E_state lost the validation at both horizons, so the loop's reference is the calibrated E_clock and
    E_state is not read; a test holds the file's reference to that."""

    def __init__(self, doc: dict) -> None:
        self.doc = doc
        self.version = f"v{doc['version']}:{doc['rule_hash']}"

    @classmethod
    def load(cls, path: Path | str = BASELINE_FILE) -> "Baseline":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def clock(self, h: str, now: datetime) -> dict[str, float]:
        return clock_odds(self.doc, h, phase_of(now))

    def whole_day(self, h: str) -> dict[str, float]:
        """The long-run shares the calibration and the question tilts are shrunk toward."""
        return whole_day_odds(self.doc, h)
