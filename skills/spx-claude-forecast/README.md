# spx-claude-forecast — Claude's own forecast beside the SPX voices

**Status: Phase 0 (the recorder) is live since 2026-10-09; the forecast itself is not built yet, and nothing in the station calls it.**

A parallel branch beside `skills/spx-jev/`. At every half-hour SPX read, code
builds one frozen scene of the market (the payload), built only from files the
station has already saved and cut at the read's own time. Claude reads that
scene next to the 10 nearest past moments, each with what actually happened
afterwards, and does the matching itself. It then sets **up / flat / down
percentages**, with sizes, for the next 30 minutes, the next 60 minutes and
the close.

The payload, Claude's answer and the actual result are stored together on one
key (`read_id` + `payload_id`). The forecast is graded every night beside the
base rate, the clock odds and Pool 2. It joins the list of graded reads as its
own tile, and it is **never mixed into the combined call** (Pool 2) until Will
decides it has earned a place.

It runs live from day one. Stored history back to 07-20 seeds the library of
past moments, so the first live read already has precedents to compare against.

The payload's structure follows SNDK Pro's (`skills/sndk-pro/`): the exact
message is saved before the call, absences are declared, units and signs are in
every field name, and code checks every claim and deletes failures rather than
rewriting them. Its angles come from Will's SPX 0DTE Signal Map, kept only
where the station can actually get the data.

## What is here now

| Path | What it is |
|---|---|
| `spec/phases.md` | The implementation phases, 0 to 7, each with the check that proves it is done. |
| `spec/build_plan.md` | The full build plan: pipeline, folder layout, the 18 payload blocks, data-access results, output contract, storage, grading, operations, open decisions, milestones with acceptance checks. |
| `spec/record_formats.md` | Plain-English field names for the reads and outcomes files and Claude's reply, with examples and the full rename map. |
| `spec/storage_design.md` | Where every payload, reply and result is kept, the nightly library, the fixed ~930-token history slice Claude sees, and what to start recording now. |
| `spec/payload_mockup.json` | A complete example payload, filled with real values from the 10-09 14:30 read. |
| `spec/rulebook_draft_cr-1.txt` | Draft of the cached rulebook (system prompt) Claude reads every call. |
| `spec/prototype/` | The read-only scripts the design used to rebuild base rates, precedents and the mockup from the stores. They are kept for reference and still point at the design session's scratch folder, so they will not run as they are. |
| `spx_claude_forecast/paths.py` | Every path the package writes, under `state/spx_claude_forecast/`, and `STORE_MAP.json`. |
| `spx_claude_forecast/jsonl_store.py` | The only writers: append-only JSON lines under a file lock, atomic replacement, write-once blobs. Every writer refuses a path outside the forecast folder, symlinks resolved. |
| `spx_claude_forecast/control.py` | The kill switch (`SPX_CLAUDE_FORECAST_DISABLE=1`), the pause switch and caps in `control.json`, the single-instance lock, the run log. |
| `spx_claude_forecast/station_stores.py` | Where the station's own stores are (read-only) and how the sibling `spx_jev` code is imported. |
| `spx_claude_forecast/recorder.py` | Phase 0: copies the dated options book, siege's SPY minutes, the raw options tape, the $VIX1D close and SPX 5-minute bars before they are overwritten or deleted, finished sessions only; mirrors the folder to iCloud Drive. |
| `launchd/`, `runtime/launchd/com.mirai-station.spx-claude-forecast-recorder.plist`, `runtime/scripts/run-spx-claude-forecast-recorder.sh` | The recorder job: the template, the copy `install-launchd.sh` loads, and its runner. 08:40 and 16:25 ET every day (weekends are a second chance at Friday's book), plus Friday 17:30 ET. |
| `tests/` | Offline tests over a temp state root; `cd skills/spx-claude-forecast && python -m pytest tests -q`. |

## Where things will live (per the plan)

- **Code:** `skills/spx-claude-forecast/spx_claude_forecast/`
- **Data:** `state/spx_claude_forecast/`, holding `payloads/`, `reads/` and `outcomes/` per day, plus the library, the seed, `recorder/`, `latest.json` and `scorecard.json`. `STORE_MAP.json` there lists every path with its writer and retention.
- **Backup:** the folder is mirrored to `~/Library/Mobile Documents/com~apple~CloudDocs/mirai-station-backups/spx_claude_forecast/` by the recorder (nothing else in `state/` is backed up today).
- **The one change outside this folder:** a single guarded call to `spawn_claude_forecast` right after `archive.append` in `skills/spx-jev/spx_jev/service.py`.
- **Kill switch:** `SPX_CLAUDE_FORECAST_DISABLE=1`.

Every build step waits for Will's go. Parallel Claude sessions share this
working tree, so propose changes before making them.
