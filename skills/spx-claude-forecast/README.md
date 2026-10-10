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

**Status (2026-10-09 night): built and wired; the first live reads are Monday 2026-10-12 from 09:32 ET.**
The SPX read (`skills/spx-jev/spx_jev/service.py`) starts the forecast for each read in its own process
right after it archives the read's record. Nothing here is in the combined call.

| Path | What it is |
|---|---|
| `spec/phases.md` | The build order, phases 0 to 9, each with the check that proves it is done. |
| `spec/decisions.md` | The choices this runs on and why, and where the build departed from the plan. |
| `spec/record_formats.md` | The locked plain-English field names of the reads and outcomes files and of Claude's reply, with examples. |
| `spx_claude_forecast/paths.py` | Every path the package writes, under `state/spx_claude_forecast/`, and `STORE_MAP.json`. |
| `spx_claude_forecast/jsonl_store.py` | The only writers: append-only lines under a file lock, atomic replacement, write-once blobs; every writer refuses a path outside the forecast folder. |
| `spx_claude_forecast/control.py` | The kill switch (`SPX_CLAUDE_FORECAST_DISABLE=1`), the pause switch and daily caps in `control.json`, the single-instance lock, the run log. |
| `spx_claude_forecast/station_stores.py` | Where the station's own stores are (read-only) and how the sibling `spx_jev` code is imported. |
| `spx_claude_forecast/recorder.py` | Copies station data before it is overwritten or deleted (the dated options book, siege's SPY minutes, the raw tape, the $VIX1D close, SPX 5-minute bars) and mirrors the folder to iCloud Drive. |
| `spx_claude_forecast/payload/` | The payload: `frozen_inputs.py` loads one read's inputs cut at the read's time; `blocks/` holds one module per block; `build.py` assembles the scene, picks the history, runs the leak checks, hashes it and renders the two prompts. |
| `spx_claude_forecast/precedents.py` | The base rate for the time of day and the eight nearest past moments, from the library. |
| `spx_claude_forecast/library.py` | `library/rows.parquet`: one row per past read (seven everyday items, quality gates, sealed outcome), rebuilt nightly. |
| `spx_claude_forecast/rulebook.py` + `rulebook_cr-1.txt` | The fixed instructions Claude gets on every call, hashed onto every payload. |
| `spx_claude_forecast/claude_call.py` | One `claude -p` call on the subscription: refuses to run with an API key set, every tool denied, the bill kept. |
| `spx_claude_forecast/answer_checks.py` | Code's checks on an answer: delete what fails, never rewrite, say what was done. |
| `spx_claude_forecast/final_forecast.py` | The two answers averaged into the final forecast, with the gaps between them and from the base rate. |
| `spx_claude_forecast/read_runner.py` | One read end to end; `hook.py` is the call the SPX read makes to start it. |
| `spx_claude_forecast/grading.py` | What happened after a read, graded the way the station grades (its flat-zone rule), plus the to_close edge. |
| `spx_claude_forecast/seed.py` | Past half-hours rebuilt from stored files as the starting history (`seed/`). |
| `spx_claude_forecast/nightly.py`, `scoring.py`, `scorecard.py` | The 17:15 ET job: seal outcomes, rebuild the library, score every forecaster, write `scorecard.json`. |
| `launchd/` | The recorder (08:40 and 16:25 ET, Friday 17:30 ET) and the nightly job (17:15 ET). |
| `tests/` | Offline tests over a temp state root, plus real-store smoke tests that skip without the station: `cd skills/spx-claude-forecast && python -m pytest tests -q`. |

The rule for `spec/`: it holds only the data contract, the roadmap and the decisions. How the system works lives in the code, its docstrings and this README; when the two disagree, the code is what runs, and the page is fixed.

## Where things will live (per the plan)

- **Code:** `skills/spx-claude-forecast/spx_claude_forecast/`
- **Data:** `state/spx_claude_forecast/`, holding `payloads/`, `reads/` and `outcomes/` per day, plus the library, the seed, `recorder/`, `latest.json` and `scorecard.json`. `STORE_MAP.json` there lists every path with its writer and retention.
- **Backup:** the folder is mirrored to `~/Library/Mobile Documents/com~apple~CloudDocs/mirai-station-backups/spx_claude_forecast/` by the recorder (nothing else in `state/` is backed up today).
- **The one change outside this folder:** a single guarded call to `spawn_claude_forecast` right after `archive.append` in `skills/spx-jev/spx_jev/service.py`.
- **Kill switch:** `SPX_CLAUDE_FORECAST_DISABLE=1`.

Every build step waits for Will's go. Parallel Claude sessions share this
working tree, so propose changes before making them.
