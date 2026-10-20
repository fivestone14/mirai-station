# schwab_live_stream — the station's one Schwab streaming connection

Schwab allows one streaming connection per account, so the station has exactly one, and every live listener rides
it. The **lob-collector** job (`com.mirai-station.lob-collector`, launched each minute in market hours by
`scheduled_jobs/job_start_scripts/run-lob-collector.sh` → `pipelines/spx_options_scanner/lob_bridge.py --run-collector`)
owns it: its stream loop (`data_collectors/spx_options_tape_collector/lob_flow/daemon.py`, T3) opens the connection,
reconnects it with backoff, and closes it after the close. Nothing here is a process or a job of its own.

## What streams

| Subscription | Symbols | Fields | Listener | Where it goes |
|---|---|---|---|---|
| LEVELONE_EQUITIES | `SPY` | bid, ask, bid size, ask size | `on_depth` | Mirai's options-flow sensor, in memory (the SPY control) |
| the same call | `$SPX` | + last price, trade time | `on_index_tick` → `SpxPriceWriter.save` | `data/market/spx_live_price_latest.json` |
| LEVELONE_OPTIONS | the ~30-strike SPXW focus set | bid, ask, bid size, ask size | `on_option_quote` | Mirai's options-flow sensor, in memory (true quote-revision counts) |

Schwab keeps one field list per service, so SPY's rows can carry the two `$SPX` fields too; a SPY row with no book
field is dropped, which keeps SPY's samples exactly what they were before `$SPX` joined (2026-10-20).

```
            Schwab streamer (one connection per account)
                              |
                 stream_connection.SchwabLiveStream.run()
              owned by lob-collector's stream loop (daemon T3)
                 /                  |                    \
          SPY book            SPXW focus set          $SPX last trade
         on_depth            on_option_quote          on_index_tick
             \                    /                         |
      Mirai options-flow sensor (memory)          spx_price_writer.SpxPriceWriter
      -> spx_order_flow_readings_latest.json      -> market/spx_live_price_latest.json
                                                   (atomic, at most once a second)
                                                            |
                                                  Kaze reads it (engine/collect/mirai_files.py)
```

## Files

| File | What it holds |
|---|---|
| `stream_connection.py` | login, subscribe, message loop (`SchwabLiveStream`); a `--probe` live smoke test |
| `stream_symbols.py` | every symbol the stream subscribes: SPY's book, `$SPX`, `spxw_symbol()` for the focus set |
| `spx_price_writer.py` | saves `$SPX`'s latest trade to the live-price file; a failed write is printed at most once a minute and never reaches the stream |

## The file it writes

`market/spx_live_price_latest.json` (`station_paths.SPX_LIVE_PRICE_LATEST`), rewritten whole:
`{"price": 6712.43, "at": "<trade time, ET ISO>", "written_at": "<save time, ET ISO>"}`. It is written only while the
stream is up, so outside market hours (and while the stream reconnects) it simply goes old; a reader judges it by its
age. Kaze is its only reader.

## Importing it

Like the tape collector's `lob_flow`, it is a folder put on `sys.path` by its parent: `lob_flow/daemon.py` and
`lob_bridge.py` append `data_collectors/`, then `from schwab_live_stream.stream_connection import SchwabLiveStream`.
schwab-py is imported lazily inside `run()`. Probe, from `data_collectors/`:
`python -m schwab_live_stream.stream_connection --probe --client-py '...'`.
