---
name: mirai-left-eye
description: |
  The SPX scan (NOT user-invokable). A launchd job runs hunter.py every 60 s
  during market hours: the Gravity Engine (lefteye_gex_box, fed by the native
  ThetaData chain, its flow sensor and the fill ledger) maps where dealer
  positioning pulls price, three heads read that map, and one row goes into
  the SPX diary (state/spx/scanner/spx_options_scanner_results_by_scan/<date>.jsonl) that spx-jev and the Claude
  forecast are built on. Everything is paper: a fire is a diary entry, never
  an order. The nightly report cards (gex_polarity_ab) grade the record.
argument-hint: ""
allowed-tools: Read
---

> **Interpreter**: `~/.local/share/mirai-station/venv/bin/python hunter.py`.
> Do NOT use `python3 hunter.py` or `./hunter.py` — the shebang is
> `#!/usr/bin/env python3` (an absolute path would hardcode one machine's
> home), so both get system python, which lacks `schwab`/`scipy`: the signal
> modules then fail silently and the scan reads quiet for the wrong reason.

# mirai-left-eye — the SPX scan

This is not a slash command. Nothing registers `/mirai-left-eye`; the skill is
a directory of Python that the station runs on a schedule. Names follow
`docs/gex-glossary.md`.

## What runs

`runtime/launchd/com.mirai-station.left-eye.plist` fires
`runtime/scripts/run-watch-left-eye.sh` every 60 s. The script checks the
market calendar itself (Mon-Fri, 09:30-16:00 ET, no network call) and runs two
phases:

1. **Scan** (live minutes only) — `hunter.py`, the Shift Manager, calls
   `reversion_lens.evaluate("SPX")`:
   - `native_gex_feed` fetches the SPX chain from ThetaData over the
     Cassandra's Edge MCP (OAuth in `cassandra_oauth.py`) and reads the flow
     sensor; `lefteye_fill_ledger` keeps where orders are filling.
   - `lefteye_gex_box`, the Gravity Engine, prices the chain into the six
     slides: flip zone, magnet and walls, terrain, volume overlay, reconcile,
     drift. `lefteye_dex`, `lefteye_profile_ladder`, `lefteye_range_ruler`,
     `lefteye_speedometer`, `lefteye_event_lens`, `lefteye_adaptive_em` and
     `dated_gex_feed` add their shadow reads to the same row.
   - Three heads judge the map: the Fade Lens (`reversion_extreme`), the
     Break Lens (`level_reclaim`) and the Watchtower (`watchtower.py`, a
     capped Claude call with its own memory in `lefteye_rag.py`). When the
     two lenses would fire opposite ways, hunter marks a `referee` note on
     the row; nothing is suppressed.
   - `record()` appends the row to the diary, `state/spx/scanner/spx_options_scanner_results_by_scan/<date>.jsonl`.
   - The heartbeat (what was evaluated, any fire) goes to
     `state/spx/scanner/spx_options_scanner_signal_checks_by_scan/<date>.jsonl`;
     `dashboard.py` rewrites the Obsidian "Today" note and
     `reversion_lens.write_learning_view` the learning view.
2. **Alerts** (every minute, open or closed) — `python -m watch.cli gex-alerts`
   (`runtime/watch/intraday/gex_alerts.py`) reads today's heartbeat log and
   pushes each fresh paper fire to the phone once, over ntfy. hunter.py itself
   never pushes.

Launchd will not run two copies at once, so a slow tick drops the next minute
rather than overlapping.

## Paper only

`REVERSION_LIVE = False`. A fire is marked live only when
`reversion_lens.live_allowed()` clears both the hand switch and the Wilson
promotion gate; the switch alone arms nothing. The Watchtower can never place
or veto a trade and never reaches the phone — it exists to be graded.

## Who reads the diary

- `skills/spx-jev` — the question pool and its labels are built from diary
  rows (`spx_jev/row_adapter.py` names the fields it reads).
- `skills/spx-claude-forecast` — the Claude read freezes its payload from the
  same station stores.
- `gex_polarity_ab.py` — the report cards, run by the `gex-polarity` job at
  16:15 ET, grade both engines and the lens against the tape.
- `runtime/viewstation` — `snapshot.py` reads the latest row for the desktop
  pages.

## Other jobs in this directory

- `dated_gex_feed.py` — the dated-book sidecar (`dated-book` job, 08:00 ET):
  far-dated OI walls stored in `state/market/spx_monthly_quarterly_open_interest_latest.json`, read by the scan and never
  fetched inside it.
- `lefteye_rag.py` — the Watchtower's memory CLI: slices and day summaries in
  `state/spx/scanner/spx_options_scanner_claude_notes_by_read/` and the summaries and terrain files beside it.
- `lob_bridge.py`, `siege_bridge.py` — the only coupling points to the lob-flow
  and siege boxes; both record-only.
- `lefteye_fetcher.py` — Schwab daily bars and quotes.

## State written

Under `state/spx/scanner/`: `spx_options_scanner_results_by_scan/` (the diary),
`spx_options_volume_last_scan_state.json` (the previous scan's flow snapshot, so
one-shot ticks can difference), `spx_options_strike_volume_weights_state.json` and
`spy_options_strike_volume_weights_state.json` (the fill ledger),
`spx_options_scanner_claude_notes_by_read/` with its summaries and terrain (tower
memory), the wall sensor's files (`spx_dealer_walls_latest.json` and its kin, through
the siege bridge), `spx_options_scanner_signal_checks_by_scan/<date>.jsonl` (the
heartbeat; `gex_alerts` reads today's, so never prune it) and
`spx_options_scanner_signals_sent_state.json` (the per-day fire dedup). Under
`state/market/`: `spx_monthly_quarterly_open_interest_latest.json` (the dated book) and
the order-flow collector's raw day folders the lob bridge reads.

## By hand

```
PY=~/.local/share/mirai-station/venv/bin/python
$PY hunter.py --dry-run --verbose     # one tick, nothing written
$PY hunter.py --ticker SPX            # one real tick
$PY -m pytest -q tests                # the suite; every test writes to a tmp state dir
```

## Docs

`docs/gex-glossary.md` (names), `docs/gw-vocab.md` (level notation),
`docs/spx-payload-inventory.md` and `docs/wt8-doctrine.md` (the Watchtower's
scene and doctrine), and `docs/reversion-lens-spec.md` in this directory (the
2026-06-23 lens plan).
