# siege — underlying EFFORT at the towers (shadow)

A **self-contained, shadow-only** sensor box that measures one thing: how much
underlying (SPY) volume gets spent while spot is TOUCHING a dealer-gamma level
(a **tower**: GWc / GWp / MagP — call wall, put wall, magnet; notation per the
host's `docs/gw-vocab.md`) — and whether that effort predicts the level
breaking or holding.

Vocabulary: **GRAVITY** = dealer positioning pull (the walls, handed in from
the host's GEX layer). **EFFORT** = underlying volume spent at the wall (what
this box measures).

## THE REVERSED SIGN — read this before touching anything

The empirical finding this box exists for (backtest sweep 2026-07: **n=168
touches, 11 of 13 days agreeing**):

> **HIGH volume at a wall touch predicts the wall BREAKS.** Sieged walls held
> only **39%** of the time vs **54%** for quiet touches.

The intuitive read — "defenders absorbing heavy volume = the level is strong" —
is the **wrong sign** on this tape. Hence the name: a wall under **siege**
falls; a **quiet** wall stands.

| Verdict | Cut | Expectation |
|---|---|---|
| `SIEGE` | effort_pct ≥ 70 | expect **BREAK** |
| `QUIET` | effort_pct ≤ 30 | expect **HOLD** |
| `NEUTRAL` | middle third | none |

Never flip these labels back to the intuitive sign without a new sweep.

## Glossary — plain names for every moving part

| Code | Plain name | What it actually does |
|---|---|---|
| `contracts.py` | Shared Language | Kinds, verdicts, outcomes, every knob — no logic, no I/O |
| `basis.py` | The Ruler | SPY↔SPX multiplicative ratio from time-matched closes — **frozen per observation**, history never re-derived, last frozen ratio carried when a matched bar is missing |
| `touch.py` | The Watch | Touch detection (\|spot−level\| ≤ 0.001·spot), **level FROZEN at first touch** (a re-picked wall never re-anchors an open window), near-spot artifact tag, window/coverage geometry |
| `effort.py` | Effort Meter | Window SPY volume as a percentile vs the same clock-window on trailing days (`trailing_median`), or — before the baseline is robust — vs today's own minute-volume distribution (`same_day_pct`); the basis rides every row |
| `engine.py` | The Crank | One call per host scan: verdicts at window close, outcomes at +30 min, coverage self-invalidation, the FEED-LOST union guard |
| `store.py` | Filing System | Atomic writes (tmp + `os.replace`) to the injected state root |

Host-side (in the host's tree, not this package): `siege_bridge.py` — the
**Bridge**, the only file that knows both worlds. Kill switch:
`SIEGE_DISABLE=1`.

## The episode state machine

1. **watching** — a tower stands somewhere; the watch tracks its max
   σ-distance from spot (the near-spot evidence) and the approach side.
2. **engaged** — first touch: the level, the SPY-side level, and the basis
   ratio are FROZEN into an episode; the window is first-touch ±10 min. One
   episode per (tower, frozen level) per session. No new windows after 15:20
   ET (window close + grade horizon must fit before the close).
   * verdict at window close — suppressed (recorded as `null`) when the tower
     is a **near-spot artifact** (never observed ≥ 0.25σ from spot before the
     touch — the spot-hugging raw-0DTE-wall lesson) or when the session is
     **saturated** (open windows already cover > 60% of elapsed minutes: a day
     that is one long touch can't tell a touch from the day).
3. **resolved** — graded +30 min after window close on the **frozen** ratio:
   `BREAK` = SPX closes beyond the frozen level in the breach direction by
   ≥ 0.05%; `HOLD` = back on the original side; marooned in the shoulder, out
   of clock, or a magnet with no approach side = `UNRESOLVED`.

## FEED-LOST union guard

Any of {no bars · last bar stale > 3 min in RTH · ≥ 3 consecutive zero-volume
RTH bars · flat-OHLC-with-volume point bars} ⇒ keep last state, stamp the
health file, and **never fabricate**: no touches, no verdicts, no grades, no
baseline fold on that scan. Episodes that lived through a feed hole carry
`degraded: true` on their card.

## Store schema (the viewstation contract — field names are FROZEN)

Under the injected root (`state/siege/` in the mirai wiring):

* `latest.json` — `{as_of, session, spot, spx_spy_ratio, baseline: {days_accrued,
  label: cold|warming|robust}, health: {feed: OK|DEGRADED|FEED-LOST, reason},
  saturated, towers: [{kind, level, sigma_dist, status: watching|engaged|resolved,
  frozen_at, effort_pct, verdict, outcome, near_spot}]}`
* `cards/<date>.jsonl` — one row per resolved episode: `{ts_open, ts_resolve,
  tower_kind, level_frozen, spy_level, ratio_used, window_vol, baseline_med,
  effort_basis, effort_pct, verdict, outcome, move_after_30m_pts,
  coverage_share, near_spot, degraded}`
* `health.json`, `baseline.json` (minute-volume normalcy memory, accrued day
  by day — labels `cold` < 5 accrued days ≤ `warming` < 21 ≤ `robust`),
  `session.json` (intraday episode state).

## What it may never do

Pick a direction, move a strike, touch a payload word, or wake the
Watchtower. Strictly shadow: its outputs are its own store plus one
record-only `"siege"` diary field written through the bridge seam.

## Tests

`pytest tests/` — fully offline (synthetic bars, tmp-path stores), no
network, no host state touched.
