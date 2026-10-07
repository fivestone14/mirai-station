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
| `archive/` | backups taken before a state file changes (the newest 10 per file name are kept) and what a forced replay cleared (`replay_force_<stamp>/`, kept 7 days); the nightly job prunes the rest |

The live store keeps its folder names; `name_map.py` maps them (`graded_results` = `store/average_grades`,
`forecasts_at_read_time` = `store/calls`, `jev_answers` = `store/answers`).

## How it runs

- **At every live read** `service.py` calls `live_call.forecast_now` inside the read (Pool 2's mix is the call since
  2026-10-06), falling back to `service_hook.spawn_after_read`, which starts `read_hook` in its own process. The hook
  first has the code feature builder answer its 90 questions for the read and appends the line to `raw/code_features/`
  (the record the nightly matrix joins on), then loads the day's fit file (fits + answer matrix, no store scan), adds
  today's row with those answers and JEV's picks, forecasts the read with every voice and appends the lines to `raw/`
  (about 0.4 s; the first sum always runs, a later one is skipped past the 5-second budget; never raises). Each line
  carries the live loop's `learn_exclude` flag for the read.
- **At 17:05 ET** (`com.mirai-station.spx-jev-prediction-nightly`, after the store's 16:40 rebuild) `nightly_job` first catches
  up any day of the last 10 with raw lines pool_v2 has not learned (a night the box slept through), then for today builds the
  tables, scores every voice against `graded_results` (joined on `read_id` + `sum_id`; excluded reads are scored and flagged
  but not learned), lets pool_v2 learn the day, refits the learners for the next market day, prunes old fit files, writes
  `scoreboard.json`, and prunes `archive/`.
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

`code_features.py` answers the 90 code questions of the merged question set for one read (the 51 ready ones, and since
2026-10-07 the 39 of Phase 2), from the read's own record and what was known at its time: the label sentences as
archived (one small parser per template), the SPX minute bars finished by the read (TREND-04/05/08/09/10/12, LEVELS-09,
FLOW-08, VOLATILITY-04/14, ...), the night's /ES bars (MACRO-08, LEVELS-07) and the derived advance-decline line
`$ADVN - $DECN` against the gap's side (BREADTH-11). `code_features_market.py` holds the Phase 2 answerers that read the
Phase 1 feeds: the market feed's minute bars per symbol (`context/bars/`, today's from the quotes until the day is saved)
for SPY/QQQ/IWM, /ZN, HYG/USO/GLD, KRE/XHB/XLE, the 3x funds, the megacaps, $VIX/$VIX9D and breadth; the daily closes
(VOLATILITY-08/17, SESSION-02); the index weights dated on or before the day (BREADTH-03/07/08/09); the calendar read at
the read's time (EVENTS-01/02/03/05, the auction tier answered from the rows present, so never named until rows exist);
the day's diary walls and magnet (OPTIONS-04/06); and the lob-flow quote sweeps (OPTIONS-08). Two catalog entries carry
`status: needs_new_feed` and stay silent: OPTIONS-12 (no 1-to-7-day volume by strike anywhere) and FLOW-09 (the
collector folds SPY's bid and ask size into one). `code_feature_catalog.json` beside it lists every question with its
id, title, layer, group (the voting group of the question set, kept as it is), method, source (the exact formula and
window), label keys, the exact answer list and notes (every DRAFT threshold says so). A ranked measure is compared with
the same measure at the same minute on the trailing 20 sessions (at least 10, else None), from `bars/`, `context/bars/`
and the store's `spx_bars`, `overnight_bars` and `context_bars` (`code_feature_inputs.py`; the SPX bars reach 60 sessions
for the gap rank); a symbol's usual multiple of SPX (the beta of its 30-minute returns on SPX's over the prior sessions'
half hours) is fitted once per day and applied to every session in a rank. The three premarket questions (LEVELS-08,
MACRO-09, MACRO-10) take the day's last premarket-lane read. A question whose data is missing, or whose sentence is a
template the parser does not know, answers None, never an error. A symbol's own-day bars live are the quotes
(one price a snapshot), so every market answerer reads closes and volume only, never a minute's high or low: live and
backfill measure the same thing. A live read runs in a fresh process, so each read loads the day's history cold (about
1.1 s) and answers all 90 in about 0.3 s; the prior-sessions parts are cached only within one process (the replay and
the backfill, which walk a day's reads together).

Each read's answers are written to `raw/code_features/{day}.jsonl` first (live, before the read is forecast; or by the
backfill command, idempotent, source `backfill`) and the nightly matrix joins them on `read_id` as columns
`code:<question_id>` with the catalog's layer and group; a read without a line is silent in every code column. Layer 3
(`jev:<question>`) is JEV's pick per answered question of the live set, grouped by request group; since Phase 3 that includes
the six judgment questions (below). The live question set is still the one asked.

## Feeds (Phase 1, 2026-10-06)

The feeds the blocked questions of the merged set waited for, built first; the Phase 2 questions read them since 2026-10-07.

| feed | where it lives | what it is | unblocks |
|---|---|---|---|
| market-feed symbols | `state/spx_jev/context/{day}.jsonl` (quotes, a minute apart) and `context/bars/{day}.jsonl` (full days); `market_context.SYMBOLS` | /ZN (the ten-year note future, under its root), KRE and XHB (`industry_funds`), SPXL, TQQQ, SPXS and SQQQ (`leveraged_funds`), TSLA and BRK/B (`megacaps`, Schwab's name for Berkshire's B share). The 41 saved sessions back to 2026-08-11 were backfilled once for the new symbols (`--backfill --symbols`, 2026-10-07 01:30 ET); Schwab served the funds and stocks from 2026-08-21 and /ZN from 2026-08-24, so the eight oldest saved sessions lack them all and 08-21 lacks /ZN (31 sessions hold all nine; the thin 3x funds and XHB miss a few minutes a day, as any thin fund does). The 11-sector group and the other groups the labels count are unchanged. | /ZN: MACRO-01, MACRO-02, MACRO-06, MACRO-07, EVENTS-05; KRE/XHB: MACRO-05; the 3x funds: SENTIMENT-02; TSLA/BRK/B: MACRO-04 |
| daily closes | `state/spx_jev/daily_closes/{symbol}.jsonl` (`daily_closes.py`; the store's `daily_closes` table, one row per symbol per built day) | $SPX, TLT, $TNX, $VIX and $VIX9D, one line per market day from 2016-10-10, as Schwab served it ($TNX at ten times the yield; the store row in percent), rewritten whole by the 16:20 day saver, so a missed night costs nothing. `daily_closes.load(state_dir, symbol, before=day)` is the point-in-time read: the sessions before the day only. | VOLATILITY-17, VOLATILITY-12, SESSION-02 |
| index weights | `state/spx_leaders/weights.json` (`index_weights.py`) | Dated sets of the largest stocks' shares of the index, read by `labels/leadership.py` (the newest set dated on or before the read's day, so a day replayed later reads what it had); a new set goes under its own `as_of`, never over an old one. The first entry, as_of 2026-10-06, is approximate and hand-kept from memory (NVDA 7.8%, MSFT 6.6%, AAPL 6.2%, AMZN 3.9%, GOOGL 2.5% + GOOG 2.0%, META 2.9%, AVGO 2.6%, TSLA 1.9%, BRK/B 1.6%); replace it with SSGA's SPY holdings as a new entry. With it on file the four megacap labels (`leaders.heavyweight_gap`, `.megacap_cohesion_30m`, `.pull_vs_rest_30m`, `.single_name_10m`) read from the next live read on. | BREADTH-03, BREADTH-07, BREADTH-08, BREADTH-09 |
| event calendar | `calendar/events.json`, `events.AUCTION` | The tier `auction` (Treasury note and bond auctions, results about 13:00 ET) has a shape and a name; the calendar has no rows of it yet, and none are invented: add them from the Treasury's published schedule. `learn_exclude` does not read the tier (that would change what the live loop learns from; a later phase). | EVENTS-05 (with /ZN), once rows exist |

## Rules added 2026-10-06 (the design-spec review)

- **Overlapping reads count once:** pool_v2's daily step counts each read by its window coverage (pool_v1's rule): a
  60-minute read every 30 minutes shares half its window with the next, so a day's 11 such reads count as about 6.
- **Matcher, one vote per group:** a column's distance (0 same / 1 different / 0.5 silent) is averaged within its group
  before the groups are averaged, so a group of eight questions on one topic counts once.
- **Finer layer volumes:** the scorer's grid starts 0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, then 0.20 to 1.50
  in 0.05 steps; three search passes.
- **The freeze:** when the gap clip binds on more than 5% of the existing voices' voice-days over 20 learned days, pool_v2's
  weights stop moving and `frozen` says why (the learners never count toward it); `pool_v2.unfreeze_pool_v2` after a review.

## Phase 3 (2026-10-07): the headline feed and the judgment group

**The headline feed** (`spx_jev/headlines.py`, job `com.mirai-station.spx-jev-headlines`, every 180 s; the poll keeps itself to
07:00-16:30 ET on weekdays, `--now` runs it by hand). Four public RSS feeds, stdlib only, a 10-second timeout each: CNBC Top News
(`cnbc.com/id/100003114/device/rss/rss.html`), a Google News search (`"S&P 500" OR Nvidia OR Fed OR tariff OR "Treasury yields"`,
the last hour; the outlet from its `<source>` tag), MarketWatch Top Stories (`feeds.content.dowjones.io/public/rss/mw_topstories`)
and the Fed's press feed (`federalreserve.gov/feeds/press_all.xml`). One JSON line per NEW item to `state/spx_jev/headlines/{day}.jsonl`
(ET day): `captured_at` (the poll's own clock, ISO ET: **the truth**), `pub_claimed` (the raw pubDate, untrusted), `feed`, `source`,
`title`, `url`, `guid`. `seen.json` dedupes across polls (sha1 of the url and of the lower-cased title, pruned to 48 h, written
atomically); a feed that fails goes to `errors.jsonl` and costs only its own items; `.lock` keeps two polls apart. **The
capture-time rule:** a question reads only what the station captured by its cut: `headlines_before(state_dir, row_ts, minutes)`
returns the lines captured in the `headline_window_min` (60) minutes ending `headline_cut_min` (2) minutes before the read, newest
first, never one captured later, whatever its feed claimed.

**The judgment group** (`spx_jev/judgment.py`; `spec/question_set.json` group `judgment`, lane `thirty_minute`, every 30-minute
read): the six JEV judgment questions of the merged set, each behind a gate read off the code feature builder's answers (the
same answers the matrix holds as layers 1-2), the headline feed, or the ten-year yield's move. A gate whose code answer is
None is not fired; a question not fired is skipped with `gate: <code question> not fired: <what it read>` and sends nothing.

| id (`jev:<id>` column) | merged id | gate | labels it reads |
|---|---|---|---|
| `push_blowoff_or_fresh` | TREND-13 | TREND-10 or TREND-11 reads anything but `none` | `judgment.push_exhaustion` (TREND-10, TREND-11, TREND-04 in words), `price.recent_move`, `range.today_vs_normal` |
| `quiet_coiled_or_resting` | VOLATILITY-15 | VOLATILITY-04 reads `compressed` (from 10:32) | `judgment.quiet` (VOLATILITY-04, VOLATILITY-02, BREADTH-09, the yield's move), `judgment.headlines`, `iv.trend_30min`, `price.day_range_position` |
| `heavyweight_catalyst_or_flow` | BREADTH-10 | BREADTH-09 reads `in play, ...` | `judgment.heavyweight`, `judgment.headlines` |
| `macro_gap_equity_reason` | MACRO-03 | MACRO-02 reads `up` or `down` (the code ranks the gap in thirds; it gives no fifth) | `judgment.macro_gap`, `judgment.headlines` |
| `yield_move_meaning` | EVENTS-06 | the ten-year yield's move since the prior close ranks top third at this minute (`judgment.yield_move`: today's `$TNX` quote against its prior close, each prior session's `context/bars` reading at the minute against its own daily close, 20 sessions, needing 10), and EVENTS-05 does not read `large` | `judgment.yield_move`, `judgment.headlines` |
| `news_reaction` | EVENTS-07 | a headline was captured in the window | `judgment.headlines`, `price.recent_move`, `price.day_range_position` |

The questions are `shadow` in the set (each `shadow_proof` says why: not a proof, a wall): `hour.answer_sentences` leaves a shadow
answer out of the sums, `grade.live_options` and the weights never see it, `service.pool_snapshots` takes pool_v1's members from
the live questions only, and the cadence never holds one. The learning store files a shadow question JEV answered as
`status = answered` all the same (the status is the reply's, not the question's), so `answer_matrix.load_jev_answers` takes it
as a layer-3 column `jev:<id>` in the group `jev:judgment`, with nothing added to the matrix code. The old loop is kept away
by the question's status alone.

**The read's order changed:** `service.run_once` now has the code feature builder answer the read right after the labels are
built and before the requests are packed (`judgment.judge`), so the gates can read the answers. A live read records the line
under `raw/code_features/{day}.jsonl` first (source `live`); `live_call.forecast_now` after the read finds it by `read_id` and
computes nothing twice. A replay or an unsent run computes the answers and writes nothing. Every part of the judgment step
catches its own failure (the code answers, the headlines, the yield's move, each gate): a failure leaves the question not
fired, with the failure named in its reason, and the read goes on; the step never raises.

**Not built:** OPTIONS-12 and FLOW-09 (no feed; silent in the catalog); EVENTS-09 (Kalshi); the scope of the news (index-wide or
one name) that EVENTS-07's merged wording asks for, since a choice carries one answer; a fifth-based gate for MACRO-03 (MACRO-02
ranks in thirds).

## Phase 4 (built 2026-10-07, live from 2026-10-08): the cut-over

**The day:** `cuts.CUT_OVER_DAY = "2026-10-08"`, read here through `name_map.CUT_OVER_DAY`. Every edit is gated on it: a read
on 2026-10-07 is byte for byte what it was (the golden old-sums test and `test_run_once`'s eve-of-the-cut-over test hold it).

**What JEV is asked from that day, on the live lane:** the six gated judgment questions (Phase 3), when their gates fire, and the
sums: the end-price sums and the average-price sum, in the same two requests as before, whose `state.answers` is now, in order,
the judgment questions' fresh answers in their old sentence form ("<ask> <pick>, JEV was N% sure") and the read's code-feature
answers as plain sentences, `code:<id>: "<catalog title>: <answer>"` for every catalog question the builder answered
(`hour.cut_over_sentences`; a None is left out, as the matrix holds it). The context's units line says the lines are the code's
measurements and JEV's own judgments. A read with no judgment answer and no code answer still asks the sums over an empty map.
The hour record and the card's `hour` keep `used` (the answered questions summed) and `code_sentences` (how many code measurements
rode beside them; absent before the cut-over). The code answers are the same line `raw/code_features/{day}.jsonl` holds for the read: `judgment.judge` returns them to
`service.run_once` beside the gate verdicts, and from the cut-over the live lane answers them whether or not the doc holds the
judgment group.

**What is no longer asked:** the 104 pre-merge questions of the live lane (every question outside the judgment group whose
`lanes` name `thirty_minute`). Each carries `retired_from: {"thirty_minute": "2026-10-08"}` in `spec/question_set.json` (the
writer keeps it in the doc; `--check` holds the doc to the set) and `ask.retired(doc, day)` reads it as dark from that day with
the reason `retired at the cut-over`: `build_requests` skips it, the cadence, the sums' `used`/`fresh`, `grade.live_options` and
the weights, `pool_snapshots`' members and `last_asked.json` all keep to live questions, and the card lists it under `dark`
with that reason (the phone's "N of M answered" counts the card's questions, so never a retired one; the questions sheet deals
them as their own last group, "retired at the cut-over, no longer asked"). The questions stay in the set and the doc, dated,
never deleted; their answers before the day stay in the store and the archive.

**What stays:** pool_v1 forms its snapshot at every read as before and stays a voice source (`pool_v1` in pool_v2's mix; its own
phone promotion, which could never reach the phone once Pool 2 took the call, was retired on 2026-10-07 and its state files are
read without their `phone` block), its
members empty from the cut-over, so its question block is the prior alone; the blend, the historical odds and the learners are
untouched. The opening and pre-market lanes are not cut over: no code feature builder answers their reads, so their sums have
nothing else to ride on; the 13 opening-only and 12 pre-market-only questions, and the live-lane questions those lanes share,
stay live there, `retired_from` naming the live lane alone.

**The matrix:** `answer_matrix.column_catalog` drops a `jev:` column whose last answered day is before the cut-over once the
matrix is built for a day after it (`before_day > CUT_OVER_DAY`), and the rows' answers with it, so a post-cut fit never carries
the dead columns (the matcher reads a silent column as half a mismatch, which would dilute every match). Every `code:` column
is kept. A matrix for the cut-over day or earlier keeps every column, so the fits before it are what they were.
