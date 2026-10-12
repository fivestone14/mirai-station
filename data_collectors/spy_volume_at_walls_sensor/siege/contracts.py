"""
contracts.py — the SHARED LANGUAGE of the SIEGE box: names, verdicts,
outcomes, and every knob. No I/O, no math — importable vocabulary only
(mirroring lob-flow's contracts seam, the box stays standalone).

Vocabulary: a TOWER is one dealer-gamma level handed down by the host's
GRAVITY map (call_wall / put_wall / magnet). A TOUCH is the underlying
arriving at a tower. EFFORT is the underlying (SPY) volume spent during the
touch window, read as a percentile against normalcy.

=========================== THE REVERSED SIGN =============================
THE CORE FINDING (backtest sweep 2026-07: n=168 touches, 11 of 13 days
agreeing): HIGH volume effort at a wall touch predicts the wall BREAKS —
sieged walls held only 39% vs 54% for quiet touches. The intuitive read
("defenders absorbing heavy volume = the level is strong") is the WRONG
SIGN on this tape. Hence the name: a wall under siege falls; a quiet wall
stands.

    SIEGE  (effort_pct >= 70)  ->  expect BREAK
    QUIET  (effort_pct <= 30)  ->  expect HOLD
    NEUTRAL otherwise          ->  no expectation

Never flip these labels back to the intuitive sign without a new sweep.
===========================================================================
"""
from __future__ import annotations

from dataclasses import dataclass

# tower kinds (which gravity level is being watched)
CALL_WALL = "call_wall"
PUT_WALL = "put_wall"
MAGNET = "magnet"
TOWER_KINDS = (CALL_WALL, PUT_WALL, MAGNET)

# verdicts (issued at window close — REVERSED SIGN, see module docstring)
SIEGE = "SIEGE"          # heavy effort  -> expect BREAK
QUIET = "QUIET"          # quiet touch   -> expect HOLD
NEUTRAL = "NEUTRAL"      # middle third  -> no expectation

# outcomes (graded +30 min after window close)
BREAK = "BREAK"
HOLD = "HOLD"
UNRESOLVED = "UNRESOLVED"

# feed health (the union guard's stamp — never fabricate past a dead tape)
FEED_OK = "OK"
FEED_DEGRADED = "DEGRADED"
FEED_LOST = "FEED-LOST"

# which distribution produced effort_pct (recorded on every row — a grader
# must never mix the two bases silently)
BASIS_TRAILING = "trailing_median"   # same clock-window sums, trailing days
BASIS_SAME_DAY = "same_day_pct"      # today's own minute-volume distribution

# baseline maturity labels
LABEL_COLD = "cold"
LABEL_WARMING = "warming"
LABEL_ROBUST = "robust"

# episode statuses
WATCHING = "watching"
ENGAGED = "engaged"
RESOLVED = "resolved"


@dataclass(frozen=True)
class SiegeConfig:
    """All tunables, one place. Thresholds mirror the backtest sweep the
    reversed-sign finding came from (effort_at_wall.py definitions where
    they exist)."""
    ticker: str = "SPX"
    proxy_symbol: str = "SPY"

    # touch geometry
    touch_tol_frac: float = 0.001        # touch = |spot − level| <= 0.1% of spot
    near_spot_sigma: float = 0.25        # a tower never seen >= 0.25σ from spot
                                         # before its touch = ATM artifact —
                                         # recorded, verdict suppressed
    level_repick_tol: float = 0.01       # level moves beyond this = a re-pick

    # windows / grading (ET minutes)
    window_min: int = 10                 # touch window = first-touch ±10 min
    grade_delay_min: int = 30            # outcome graded +30 min after window close
    break_frac: float = 0.0005           # BREAK = beyond frozen level by >= 0.05%

    # verdict cuts (REVERSED SIGN — see module docstring)
    siege_pct: float = 70.0
    quiet_pct: float = 30.0

    # coverage self-invalidation: windows covering > this share of the
    # elapsed session = the day is one long touch — new verdicts say nothing
    saturation_share: float = 0.60

    # FEED-LOST union guard shapes
    stale_bar_s: int = 180               # last bar older than 3 min in RTH
    zero_vol_run: int = 3                # >= 3 consecutive zero-volume RTH bars
    flat_run: int = 3                    # flat-OHLC-with-volume point bars

    # baseline maturity (accrued PRIOR days)
    warming_days: int = 5
    robust_days: int = 21
    baseline_keep_days: int = 60

    # session clock (ET minutes from midnight)
    session_open_min: int = 9 * 60 + 30
    session_close_min: int = 16 * 60
    # no new windows after 15:20 — window close (+10) plus the grade horizon
    # (+30) must land at or before the close, else every late touch would
    # grade UNRESOLVED by construction
    last_open_min: int = 15 * 60 + 20

    # SPY↔SPX basis: a time-matched close must be within this of the scan
    # clock to derive a fresh ratio (or to serve as the grading close)
    bar_match_tol_min: int = 3
