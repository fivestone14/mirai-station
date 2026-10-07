# Mirai Prediction System

A second learning loop that runs beside the SPX JEV service and learns on its own. It never touches the live read, the
grader, pool_v1 or the call the phone shows. Its names are the design diagram's names.

```
market data --> layer 1 (code feature builder: measures ranked vs their own history)  --\
            --> layer 2 (code feature builder: discrete labels)                          >--> additive_scorer, matcher
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
| `raw/code_features/{day}.jsonl` | the code feature builder's answers per read (layers 1-2), written live before the read is forecast or by the backfill; append-only; the record |
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
`forecasts_at_read_time` = `store/calls`, `jev_answers` = `store/answers`).

## How it runs

- **At every live read** `service.py` calls `live_call.forecast_now` inside the read (Pool 2's mix is the call since
  2026-10-06), falling back to `service_hook.spawn_after_read`, which starts `read_hook` in its own process. The hook
  first has the code feature builder answer its 51 questions for the read and appends the line to `raw/code_features/`
  (the record the nightly matrix joins on), then loads the day's fit file (fits + answer matrix, no store scan), adds
  today's row with those answers and JEV's picks, forecasts the read with every voice and appends the lines to `raw/`
  (about 0.4 s; the first sum always runs, a later one is skipped past the 5-second budget; never raises). Each line
  carries the live loop's `learn_exclude` flag for the read.
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
python -m spx_jev.mirai_prediction.backfill_code_features --state-dir ../../state --from 2026-09-28 --to 2026-10-06 [--force]
python -m pytest tests/test_mirai_prediction_*.py -q
```

Turn it off: `SPX_JEV_PREDICTION_DISABLE=1` stops both the hook and the nightly job (`SPX_JEV_DISABLE=1` stops the nightly
job too, with the rest of the service).

## The code feature builder (layers 1-2)

`code_features.py` answers the 51 ready code questions of the merged question set for one read, from the read's own
record and what was known at its time: the label sentences as archived (one small parser per template), the SPX minute
bars finished by the read (TREND-05/08/09/10/12, LEVELS-09, FLOW-08), the night's /ES bars (MACRO-08) and the derived
advance-decline line `$ADVN - $DECN` against the gap's side (BREADTH-11). `code_feature_catalog.json` beside it lists
every question with its id, title, layer, group (the voting group of the question set, kept as it is), method, source,
label keys, the exact answer list and notes. A ranked measure is compared with the same measure at the same minute on
the trailing 20 sessions (at least 10, else None), from `bars/` and the store's `spx_bars`, `overnight_bars` and
`context_bars` (`code_feature_inputs.py`); the three premarket questions (LEVELS-08, MACRO-09, MACRO-10) take the day's
last premarket-lane read. A question whose data is missing, or whose sentence is a template the parser does not know,
answers None, never an error. FLOW-08's thresholds are a draft.

Each read's answers are written to `raw/code_features/{day}.jsonl` first (live, before the read is forecast; or by the
backfill command, idempotent, source `backfill`) and the nightly matrix joins them on `read_id` as columns
`code:<question_id>` with the catalog's layer and group; a read without a line is silent in every code column. Layer 3
(`jev:<question>`) is unchanged: JEV's pick per answered question of the live set, grouped by request group. The six JEV
judgment questions of the merged set are NOT built yet, and the live question set is still the one asked.

## Rules added 2026-10-06 (the design-spec review)

- **Overlapping reads count once:** pool_v2's daily step counts each read by its window coverage (pool_v1's rule): a
  60-minute read every 30 minutes shares half its window with the next, so a day's 11 such reads count as about 6.
- **Matcher, one vote per group:** a column's distance (0 same / 1 different / 0.5 silent) is averaged within its group
  before the groups are averaged, so a group of eight questions on one topic counts once.
- **Finer layer volumes:** the scorer's grid starts 0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, then 0.20 to 1.50
  in 0.05 steps; three search passes.
- **The freeze:** when the gap clip binds on more than 5% of the existing voices' voice-days over 20 learned days, pool_v2's
  weights stop moving and `frozen` says why (the learners never count toward it); `pool_v2.unfreeze_pool_v2` after a review.
