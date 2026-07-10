# lob-flow — Layer 2 (FLOW) of the 0DTE reasoning stack

A **self-contained, shadow-only** sensor box over the SPX 0DTE options chain.
It watches two things a human would watch on a chain heatmap:

* **the TAPE** — who is actually buying vs selling options right now, and
* **the LIGHTS** — resting quote sizes per strike: are the market makers
  *fleeing* (scatter = storm warning) or *huddling back* around a level the
  gravity (GEX) map already marked (defense = the level is real)?

Vocabulary: **GRAVITY** = dealer positioning pull (the map, handed in from the
host's GEX layer). **FLOW** = live force (what this box measures).

It may **never** pick a direction, move a strike, or place anything. Its only
possible influence — after it beats both a control and the gravity engine on
a nightly-graded record — is a confidence nudge clamped to ×[0.85, 1.15].

## Glossary — plain names for every moving part

| Code | Plain name | What it actually does |
|---|---|---|
| `contracts.py` | Shared Language | Every data shape, adapter interface, and knob — no logic, no I/O |
| `io_state.py` | Filing System | Atomic writes, the `latest.json` handshake, journals + cursors, disk rotation |
| `options_feed.py` | Chain Tap | Pulls the chain's top-of-book (with sizes) and the trade tape (with quote-at-print) from the hosted MCP |
| `spy_stream.py` | Control's Feed | Streams SPY top-of-book + the ~30-strike SPXW focus set over Schwab |
| `daemon.py` | Collector | The only long-lived process: four loops — sweep, tape, stream, fold |
| `sensors.py :: read_tape()` | Tape Reader | Who's eating: signs each trade bucket by where its VWAP sits inside the quote |
| `sensors.py :: fade_dial()` | Fade Dial | Are the monkeys fleeing (scatter, ≥2 bins) or is nothing happening |
| `sensors.py :: refill_half_lives()` | Defense Test | After trades eat a gravity strike's size, how fast do the robots rebuild it — fast rebuild = defended level |
| `sensors.py :: purge_step()` | Panic-Button Filter | All strikes emptying at once = one firm's mass-cancel — veto, not signal |
| `baselines.py :: BaselineStore` | Normalcy Memory | What every 5-minute slot of a boring day looks like (median/MAD, 21 clean days) |
| `baselines.py :: FileCalendar` | Event Gate | FOMC/CPI/NFP/OPEX awareness — scheduled dealer pull-back is Tuesday, not terror |
| `engine.py :: evaluate_once()` | Composer | Folds sensors + memory into the two recorded views |
| `engine.py :: grade_day()` | Own Report Card | Did turbulence calls precede real range? Did defended pins hold? (driven nightly by the host bridge — no CLI of its own) |
| `engine.py :: promotion_check()` | Earn-Trust Gate | Beat the control AND gravity-alone, Wilson 90%, ≥30 episodes — else stay silent |
| `lob_flow` (telemetry key) | Book-sensor view | The fused tape ⊕ dial read, recorded every scan |
| `spy_depth` (telemetry key) | The Control | Same scatter math on SPY's book — the proven rival the fancy version must beat |

Host-side (in the host's tree, not this package): `lob_bridge.py` — the
**Bridge**, the only file that knows both worlds (adapters, paths, secrets,
the dormant promotion switch `LOB_LIVE`).

## What each cadence tier can and cannot see

| Tier | Feed | Can detect | Cannot detect |
|---|---|---|---|
| 60s chain sweep (hosted-MCP cache ceiling) | `options_feed.sweep` | delta-bucket scatter/persistence, spread dial, purge steps at 1-min resolution | sub-minute pull-and-refill; true revision rates |
| event-time tape, 30s poll (ms resolution) | `options_feed.trades_since` | signed flow; refill half-life at ACTIVE strikes (exactly the gravity strikes while being tested) | quiet-strike refill |
| Schwab stream (SPY + SPXW focus set) | `spy_stream` | true revisions/sec + full-fidelity refill on the focus set | the rest of the chain |
| future: direct Theta Terminal | new feed impls only | full refill curves, chain-wide true noise | — |

Chain-wide `noise_proxy` is a **proxy** until a direct quote-tick feed exists;
it is recorded for calibration only — no alert path consumes it yet.

## Market-semantics guardrails (domain-expert review, 2026-07-04)

* Refill "recovery" = size back **and price within one tick** — size re-posted
  a tick worse is polite abandonment, not defense.
* Same-millisecond multi-contract prints (complex-package legs) are excluded
  from eats and tape signing; eats require prints AT the touch.
* Tape tilt weights by |delta| × contracts (hedge pressure), minute-bucketed;
  its `determinate_share` scales the layer confidence.
* z thresholds (±4) are calibrated to BIN-LEVEL dispersion; the nightly fold
  reports a reachability audit (median max-attainable |z|) so a structurally
  dead alarm is visible.
* Purge = matched-contract step **with spreads normal** (a real panic blows
  spreads out and must stay a signal); the veto lifts early when the book
  re-forms.
* Turbulence regime = size-scatter OR persistent spread blowout (SPX LMMs
  widen at mandated minimum size at least as often as they pull it).
* Defense asserts no regime after 15:15 ET (charm/pin mechanics), and the
  calendar also dirties VIX-expiration Wednesdays + quarter-end roll days,
  with the FOMC block extended through the press conference.

## Standalone usage (no host required)

```python
from lob_flow import LobConfig, evaluate_once, read_latest
```

Provide: a token provider + endpoint (or your own feed implementing the
`OptionsQuoteFeed`/`OptionsTapeFeed` Protocols), optionally a schwab-py client
factory for the control (`pip install lob-flow[schwab]`), a state dir, and two
small JSON files (`gex_context.json` optional — the defense test simply
disables without it; `calendar.json` seeds itself). Call
`lob_flow.daemon.main(cfg, adapters)` from your own supervised launcher, and
consume with `read_latest(state_dir, now)`.

Probes (live smoke tests, never used by the test suite):
`python -m lob_flow.options_feed --probe --token-cmd '...'` ·
`python -m lob_flow.spy_stream --probe --client-py '...'`

## Trust labels & the clock

Baselines need **21 clean trading days** (event days excluded) before robust
z-scores, judged **per key**; 15–20 days = `percentile` (confidence tier only,
no alerts); below that a key is `cold` and holds **no opinion** (never "calm",
never "panic") — except a cold **VIX band**, which borrows its calmer
neighbor's baseline at 1.5× MAD (labeled `borrowed`) so the storm sensor
doesn't go mute in the first storm. Every recorded row carries its label —
downstream graders ignore cold-period calls.

## Tests

`pytest tests/` — fully offline (fakes + synthetic fixtures), no network, no
host state touched.
