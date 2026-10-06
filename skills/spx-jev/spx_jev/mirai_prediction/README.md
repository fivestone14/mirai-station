# Mirai Prediction System

A second learning loop that runs beside the SPX JEV service and learns on its own. It never touches the live read, the
grader, pool_v1 or the call the phone shows. Its names are the design diagram's names.

```
market data --> layer 1 (code: market values vs their own history)  --\
            --> layer 2 (code: discrete labels)                        >--> additive_scorer, matcher
            --> layer 3 (JEV's answers)                              --/
JEV's own call ---------------------------------------------------------> jev_corrected
historical odds + today's voices + the three learners ------------------> pool_v2  (pool_v1 stays as it is)
graded result -> scored per voice -> pool_v2 weights -> refit each morning -> phone scoreboard
```

## Where the data lives

`state/spx_jev/mirai_prediction/`

| folder / file | what it holds |
|---|---|
| `raw/new_voice_forecasts/{day}.jsonl` | every voice's forecast per read, written live; append-only; the record |
| `tables/new_voice_forecasts/day=…/` | the same, checked and built nightly (Parquet); safe to delete and rebuild |
| `tables/voice_scores/day=…/` | each voice's penalty and right/wrong per graded read |
| `tables/_checks/refused_rows/{day}.jsonl` | rows a check refused, with the reason |
| `pools/pool_v2/weights_{sum_id}.json` | pool_v2's voice weights (move and direction), days learned |
| `pools/pool_v2/weight_updates_{sum_id}.jsonl` | one line per day learned |
| `catalogs/voice_fits/{day}_{sum_id}.json` | the three learners fitted for that day (on days before it) and the answer matrix they were fitted on; the newest 10 days are kept, older files are pruned (every one can be rebuilt) |
| `scoreboard.json` | the phone card's file |
| `run_logs/job_run_log.jsonl` | every job and hook run |
| `archive/` | backups taken before a state file changes |

The live store keeps its folder names; `name_map.py` maps them (`graded_results` = `store/average_grades`,
`forecasts_at_read_time` = `store/calls`, `jev_answers` = `store/answers`, `code_facts` = `store/facts`).

## How it runs

- **At every live read** `service.py` calls `service_hook.spawn_after_read`, which starts `read_hook` in its own process.
  The hook loads the day's fit file (fits + answer matrix, no store scan), adds today's row, forecasts the read with every
  voice and appends the lines to `raw/` (about 0.3 s; the first sum always runs, a later one is skipped past the 5-second
  budget; never raises). Each line carries the live loop's `learn_exclude` flag for the read.
- **At 17:05 ET** (`com.mirai-station.spx-jev-prediction-nightly`, after the store's 16:40 rebuild) `nightly_job` first catches
  up any day of the last 10 with raw lines pool_v2 has not learned (a night the box slept through), then for today builds the
  tables, scores every voice against `graded_results` (joined on `read_id` + `sum_id`; excluded reads are scored and flagged
  but not learned), lets pool_v2 learn the day, refits the learners for the next market day, prunes old fit files, and writes
  `scoreboard.json`.
- **Walk-forward, always:** a fit for day D uses graded rows from days before D only (`voice_fits.check_no_leak`), and a
  market value is ranked against earlier days only, never the same day's earlier reads.

```
cd skills/spx-jev
python -m spx_jev.mirai_prediction.nightly_job --state-dir ../../state --lane live [--day YYYY-MM-DD]
python -m spx_jev.mirai_prediction.read_hook   --state-dir ../../state --lane live [--read-id live:<row_ts>]
python -m spx_jev.mirai_prediction.replay      --state-dir ../../state --lane live --from 2026-09-29 --to 2026-10-05 [--force]
python -m pytest tests/test_mirai_prediction_*.py -q
```

Turn it off: `SPX_JEV_PREDICTION_DISABLE=1` stops both the hook and the nightly job (`SPX_JEV_DISABLE=1` stops the nightly
job too, with the rest of the service).

## Interim inputs (until the code feature builder exists)

Layers 1-2 come from the live store's `facts` table: layer 1 (`level:<symbol>`) is a market value ranked `low / middle / high`
against that symbol's own values from earlier days (trailing 60 sessions, at least 20 values); layer 2 (`fact:<name>`) is a
code label with few distinct texts (a category). Free-text labels are not used. Layer 3 (`jev:<question>`) is JEV's pick per
answered question; the questions of one request group share a group, so the additive scorer takes one vote per group
(19 groups over 77 questions today). The planned ~250-feature builder replaces layers 1-2.

## Rules added 2026-10-06 (the design-spec review)

- **Overlapping reads count once:** pool_v2's daily step counts each read by its window coverage (pool_v1's rule): a
  60-minute read every 30 minutes shares half its window with the next, so a day's 11 such reads count as about 6.
- **Matcher, one vote per group:** a column's distance (0 same / 1 different / 0.5 silent) is averaged within its group
  before the groups are averaged, so a group of eight questions on one topic counts once.
- **Finer layer volumes:** the scorer's grid starts 0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, then 0.20 to 1.50
  in 0.05 steps; three search passes.
- **The freeze:** when the gap clip binds on more than 5% of the existing voices' voice-days over 20 learned days, pool_v2's
  weights stop moving and `frozen` says why (the learners never count toward it); `pool_v2.unfreeze_pool_v2` after a review.
