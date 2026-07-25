# GEX Glossary — plain names for every moving part

Vocabulary: **GRAVITY** = dealer positioning, where price gets *pulled* (magnet,
walls, flip, regime). **FLOW** = the live force, who is *shoving* price right now.
The pin-vs-trend failure mode reads as: gravity gets overpowered by flow.

Code identifiers and storage keys are never renamed (the `gex_views` key
precedent) — these plain names live in docstrings, labels, and conversation.
Reference diagram: `~/Desktop/mirai-gex-flow.png`.

## Wall / magnet / pin notation

The standard notation for every *labeled* wall, magnet, or pin level lives in
**`docs/gw-vocab.md`** (2026-07-19): `GWc`/`GWp` = call/put gex walls, `MagP` =
magnetic pull, `Pin` = pinning, each with a prime intensity mark (`'` `''`
`'''`) and a tenor prefix (`[0DTE]`, `[1-7DTE]`, `[AUG21]`-style dates).
`skills/mirai-left-eye/gw_vocab.py` is the Python source of truth for the
thresholds and the wall-clustering rule; the viewstation carries its JS twin.
Prose stays plain English ("the magnet", "a wall") — the notation is for
labeled levels in rendered views.

## Tick loop
| Code | Plain name | What it actually does |
|---|---|---|
| `hunter.py` | Shift Manager | The every-5-minutes worker: wakes up, runs every read, records the results, rings alerts, goes back to sleep |
| `reversion_lens.evaluate()` | Scan | One full look at one ticker: gathers all inputs, runs both gravity engines, packs one diary row |

## Fetch
| Code | Plain name | What it actually does |
|---|---|---|
| `lefteye_fetcher` | Data Runner | Fetches raw market data on request — price bars, quotes, option chains, VIX |
| `lefteye_vwap.compute_vwap_series()` | Fair-Price Line | The day's volume-weighted "fair value" that stretch is measured from |

(RETIRED 2026-07-03: `lefteye_gamma.fetch_oracle_state()` — the Old Gravity Photo —
was deleted; its σ/walls/spot helpers were absorbed into the Gravity Engine, which
is now the only gravity read.)

## Flow sensor + native gravity
| Code | Plain name | What it actually does |
|---|---|---|
| `native_gex_feed.read()` | Real-Data Read | One time-boxed trip to ThetaData: real option chain + live flow, never allowed to stall the scan |
| `native_chain()` | Real Chain | Pulls the actual SPX option book, strike by strike |
| `aggressor_flow()` | Flow Sensor | Who's hitting the buy vs sell button on today's ATM options (−1 all sold … +1 all bought) |
| `lefteye_fill_ledger.annotate()` | Fill Ledger | Stamps each strike with its *recent* order fills (45-min fade) so the magnet follows fresh money |

## Gravity engine
| Code | Plain name | What it actually does |
|---|---|---|
| `GexBox()` | Gravity Engine | The orchestrator: fetch rarely, re-price every scan, produce the six slides |
| `_rescaled_chain()` | Stand-In Chain | Borrows SPY's option book and stretches it ×10 into SPX's price space |
| `slide_flip()` | Border Finder (A) | The price where dealer behavior flips from calming to amplifying, with an honest uncertainty band |
| `slide_0dte()` | Magnet Finder (B) | Today's pull-strike (the MagP level, fill-ledger weighted) and today's ceiling/floor walls ([0DTE]GWc / [0DTE]GWp) |
| `slide_tenor_walls()` | Terrain Map (C) | The stable multi-day walls ([1-7DTE]GWs) that don't drift intraday |
| `reconcile_sign()` | Gravity-vs-Flow Check (E) | Downgrades a "price will pin" call to 🟡 uncertain when heavy one-way flow is fighting it |
| `slide_flows()` | Drift Gauges (F) | Vanna/charm: the slow pressure from volatility moves and time decay into the close |

## Direction-side sensor — DEX (added 2026-07-25, shadow)
| Code | Plain name | What it actually does |
|---|---|---|
| `lefteye_dex.dex_views()` | Delta Ledger | Net dealer DELTA still to re-hedge, per strike — the direction-side twin of the Gravity Engine, priced off the same cached chain (no extra fetch). Naive net is LONG by construction; the flow-signed twin carries the only live sign |
| `dex_views` (key) | Dealer-delta snapshot | The DEX surface recorded on each scan row; an absent key is NO READ, never a zero book |

## Phase-P display records (added 2026-07-25, shadow)
| Code | Plain name | What it actually does |
|---|---|---|
| `net_exposure` (key) | Whole-book totals | NET Γ / NET Δ for the strip tiles — `gex_views.net_gex_tenor` + `dex_views.net_dex_total` lifted off the same row (no new fetch), with yesterday's close pair from a once-per-process diary read |
| `lefteye_profile_ladder.read()` → `profile_ladder` (key) | Profile Ladder | The named rungs of one scan's gamma profile: GWc / **cT** / **HVL** / **pT** / GWp + the zone word for spot. cT/pT are the wt-8 chop band's upper/lower edges; **HVL** (high volatility level) is the adopted display name for the gamma flip line — the storage key stays `gamma_flip`, keys are never renamed |
| `lefteye_adaptive_em.read()` → `adaptive_em` (key) | Adaptive EM | The Range Ruler's symmetric straddle EM re-split into asymmetric down/up bounds by measured asymmetry only (live tower range band, else the realized semivariance split); total width preserved — never adds to the priced budget |

## Watchtower payload words (wt-8, 2026-07-25)
| Payload field | Plain name | What it actually says |
|---|---|---|
| `terrain_asymmetry` | The book's slope | Which side is CHEAPER to move IF pushed — resistance, not live pushing (renamed from `flow_asymmetry` in wt-8; the `shove` storage keys are untouched) |
| `dealer_delta_dex` | Advisory delta context | The DEX read spoken to the Watchtower — advisory this era, record-only, no gate reads it |

## Stage + record
| Code | Plain name | What it actually does |
|---|---|---|
| `reversion_lens.record()` | Diary Writer | Appends the scan's row to today's diary file — the permanent evidence stream |
| `gex_views` (key) | Proxy gravity snapshot | The estimated map, recorded every scan |
| `gex_theta` (key) | Native gravity+flow snapshot | The real-data map + live flow, recorded every scan |

## Layer 2 — LOB (FLOW) sensor box (added 2026-07-04, shadow)
| Code | Plain name | What it actually does |
|---|---|---|
| `skills/lob-flow/` | The Box | Self-contained Layer-2 sensor package — full glossary in its own README.md |
| `lob_bridge.py` | The Bridge | The only file that knows both worlds: hands gravity strikes down, reads the collector's fold up, holds the dormant `LOB_LIVE` switch |
| `lob_flow.daemon` | Collector | Launchd worker that owns all Layer-2 network: chain sweeps, tape polls, Schwab stream, folds |
| `read_tape()` | Tape Reader | Who's eating: signs each trade bucket by where its VWAP sits inside the quote |
| `fade_dial()` | Fade Dial | Monkeys fleeing (scatter = storm warning) vs nothing happening |
| `refill_half_lives()` | Defense Test | How fast size rebuilds at a gravity strike after trades eat it — fast = defended pin |
| `purge_step()` | Panic-Button Filter | All strikes emptying at once = one firm's mass-cancel — veto, not signal |
| `BaselineStore` | Normalcy Memory | What every 5-minute slot of a boring day looks like (21 clean days, median/MAD) |
| `lob_flow` (key) | Book-sensor view | The fused tape ⊕ dial read, recorded every scan |
| `spy_depth` (key) | The Control | Same scatter math on SPY's book — the proven rival lob_flow must beat to earn a whisper |

## Readers
| Code | Plain name | What it actually does |
|---|---|---|
| `resolve_dealer_map()` | Dealer Map / One Story | Collapses the competing engine reads into a single displayed truth (native leads, SPY-proxy is the labeled fallback) |
| `_gex_section()` / `snapshot.py` / `index.html` | Storytellers | Render that one story into the Obsidian note and the tablet map |
| `gex_polarity_ab.grade_day()` | Report Card | After close: did each engine's calls match what price actually did? |
| `_episodes()` | Idea Counter | Collapses repeated identical calls into one gradeable idea |
| `grade_snapshot()` | Pin-or-Break Judge | For one call: did price reach toward the magnet or run away first? |
| `_magnet_stats()` | Magnet Tape-Measure | How close price actually came to each engine's stated magnet |
| `persist()` | Filing Cabinet | Saves the report cards so the record accumulates instead of scrolling away |
