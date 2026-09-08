# book-flow — the price × time board

What size was **added** and **removed** at each price through a session, and
the collector that records it. Feeds the viewstation's **Day Visual LOB** tab.

Vocabulary note: **LOB** = limit order book, the ladder of resting buy and sell
orders at each price. Nothing more exotic.

## The one idea

    price  ×  time  →  how much size appeared, how much left

Resting size at a price is a *level*; the board wants *flows*, so every
observation is differenced against the last one seen **at that price**. Grew =
added. Shrank = removed — and snapshots cannot say whether a removal was a
cancel or a fill, so the field is named `rem` and never `cancelled`.

## Two feeds, one shape

| Feed | Source | Add/remove is | Hole |
|---|---|---|---|
| `options_tape` | `state/lob_flow/raw/{day}/tape.jsonl` (recording since 2026-08-05) | **inferred** — each print carries its own contract's bid/ask size | a strike is only sampled when it trades |
| `equity_book` | `state/flow/{ticker}/{day}/book.jsonl` (this package's collector) | **literal** — the difference between two whole snapshots | none |

Both produce the same wire document, so the renderer cannot tell them apart.
`kind` says which, purely so a reader knows how much to trust it.

## Files

| File | What it does |
|---|---|
| `contracts.py` | data shapes and knobs — no logic, no I/O |
| `board.py` | the accumulator: observations in, slices out. Pure |
| `sources.py` | the two feeds, plus gzip-transparent reading of rotated days |
| `cache.py` | the only thing the viewstation calls; disk-cached wire documents |
| `stream.py` | Schwab `NASDAQ_BOOK` / `NYSE_BOOK` → flat level rows |
| `daemon.py` | the long-lived collector. **Shipped disabled** |

## Three things that will bite whoever edits this

1. **Per-price memory survives the slice boundary.** A book does not reset at
   09:31:00. Reset `_last` per slice and every level books its whole queue as a
   phantom add at the top of every minute.
2. **One price can carry several books.** An option strike has a call book and
   a put book; differencing them together invents a queue-sized removal every
   time the feed alternates. That is what `series=` scopes.
3. **Binning uses integer arithmetic.** `1739.50 // 0.05` floors to 1739.45 in
   binary floating point, filing every round price one tick low.

## Running it

```bash
# prebuild board caches (a cold day is a 2-3s pass over ~900k rows)
python -m book_flow.cache --ticker SPX                 # every recorded day
python -m book_flow.cache --ticker SPX --day 2026-09-04 --slice-s 300

# tests — fully offline, no network, no host state
pytest tests/
```

## The collector is off, and why

`daemon.ENABLED` is `False` and no launchd job is loaded. Schwab allows exactly
**one streamer connection per login**, and the lob-flow SPX collector holds it
all session. Starting this one takes that connection, dropping the SPX box from
`stream+mcp` to mcp-only and silencing its SPY control.

**Not yet built:** the handshake that would publish this collector's SPY depth
samples back into `state/lob_flow/` so the SPX box keeps its control. Until
that exists, enabling this is a straight trade, not a free upgrade.
