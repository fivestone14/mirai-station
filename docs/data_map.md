# Data map

The one page to look a data store up on. `station_paths.py` at the repo root is the source of truth for every path
(each store is a constant there; jobs, the server and the pages ask it by that name); this page says, for each store,
where it is, what one row or file holds, which job writes it and who reads it. Writers and readers were taken from the
code on 2026-10-11 (cleanup phase 10). When the two disagree, `station_paths.py` is what runs, and this page is fixed.

Every path below is relative to the data root, `~/mirai_awakening/data/` (git-ignored; `station_paths.state_root()`
decides it: `--state-dir`, else `MIRAI_STATE_DIR`, else `MIRAI_STATION_STATE`, else `<repo>/data`). `{day}` is
`YYYY-MM-DD`; a `{lane}` is one of `spx/jev/live_lane`, `spx/jev/opening_lane`, `spx/jev/premarket_lane`.

## The naming rules

- **Shape:** `<subject>_<content>_by_<what one row is>`: `spx_price_bars_by_minute`, `jev_answers_by_read`.
- **Grain:** the ending says what ONE row really is. When a row is one read per horizon, one question per read, one
  forecaster per read per horizon, or one table per day, the grain is compound and written with `_and_`:
  `by_read_and_horizon`, `by_read_and_question`, `by_forecaster_read_and_horizon`, `by_table_and_day`.
- **Fixed endings** take the place of a grain: `_latest` (one current-snapshot file other parts read), `_card` (a file
  written only for a screen), `_state` (a job's own working memory between runs; `_today_state` is cleared each
  session), `_log` (an operational log).
- **Tables:** a Parquet table sits in the `tables/` folder of the domain that holds its raw files and keeps the raw
  folder's `<subject>_<content>` stem; the two differ only in the grain when the table unnests the rows
  (`jev_answers_by_read/` and `tables/jev_answers_by_read_and_question/`). In `station_paths.py` the table's constant
  takes the `_TABLE` ending, so a table and its raw folder can share the map's name
  (`JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON` and `..._TABLE`).
- **Daily files** are `{day}.jsonl` inside the store's folder, one line per row. A few daily files hold one JSON
  object (`{day}.json`): the order-flow grades, the SPY minute volumes, the open-interest history, the 3D view's cache.
- **Keyed files** inside a folder are named by their key: `{symbol}.jsonl`, `{horizon}.json`, `{day}_{horizon}.json`.
- **Lanes and horizons** are folder levels or file keys, never words inside a store name: JEV's three lanes hold the
  same store names; pool state is one file per horizon (`next_30_minutes`, `next_60_minutes`,
  `average_of_next_30_minutes`; `end_price` and `average_price` name the two grading families inside a store name).
- **Subjects** are the approved product names, used bare: `jev`, `claude_forecast`, `base_pool`, `learner_pool`. The
  `spx/` folder supplies "SPX", so the forecaster tables under `spx/prediction/` carry no subject word.
- **Full words, no code names:** lowercase snake_case, no abbreviations, seven words or fewer where the truth allows.

### Vocabulary

| word | means |
|---|---|
| `watchlist` | the ~47 symbols watched beside SPX: the VIX family, rates, futures, the 10 largest stocks, sector and leveraged funds, NYSE breadth |
| `futures` | /ES /ZN /BTC /MBT, overnight and day |
| `same_day` | options that expire the same day (0DTE) |
| `monthly_quarterly` | SPX options expiring at a month end or quarter end (the "dated book") |
| `options_scanner`, `options_reader` | the SPX or SNDK option-chain scanner (the "left eye" for SPX); the SNDK Claude reader of the scanner's board |
| `order_flow` | who is buying or selling options, and whether market makers pull or rebuild their quotes |
| `dealer_wall`, `dealer_gamma_levels` | the strike where dealers' options are largest; magnet, call wall, put wall and gamma flip |
| `forecaster` | one source of up/flat/down chances in the pools (JEV mixes, historical odds, scorer, matcher, corrected JEV, Claude); was "voice" |
| `historical_odds` | how often SPX went up, flat or down at this time of day on past days; was "clock" |
| `code_question` | a question that code answers, not JEV |
| `lane` | one JEV schedule: live = every 30 min 09:32-16:02; opening = every 5 min 09:35-10:30 (the code's `tape`); premarket = 6 checkpoints 02:35-09:28 |
| `outage_alert` | the alarm that pages Will when a job goes silent; was "deadman" |
| `price_bars`, `quotes`, `depth`, `trades`, `closes`, `open_interest` | OHLC+volume for a span; a price snapshot at a moment; bid/ask size and spread per delta band; one print each; a session's last value; open contracts per strike |
| `readings`, `results`, `signal_checks`, `notes`, `answers`, `test_answers`, `gates` | sensor measurements; what one scan computed; one scan's check of whether a paper signal fires; Claude's short summary of a read; what JEV, Claude or the code replied; answers to a test variant of Claude's rules on frozen inputs; the checks a reader runs before it may ask Claude |
| `forecasts`, `outcomes`, `grades`, `scoreboard`, `updates`, `weights`, `baselines` | up/flat/down chances for a horizon; what price did after a read; a forecast against its outcome; each forecaster's skill vs the usual odds; one nightly learning step of a pool; how much each question or forecaster counts; the normal value for each 5-minute slot of a quiet day |
| `inputs`, `rebuilt`, `variant`, `fingerprints`, `rulebooks`, `trained_forecasters` | the frozen scene sent to Claude (was "payload"); past reads rebuilt from history (was "seed"); a test version of Claude's rules (was "arm"); the seven market facts of one read's moment; the rule text sent to Claude; the fitted settings of the learning forecasters (was "voice_fits") |
| `by_read`, `by_scan`, `by_run`, `by_save`, `by_event`, `by_quote`, `by_article`, `by_update`, `by_version` | one time a model is asked (live or rebuilt); one scanner pass; one job run, whether or not it asked Claude; one save that changed rows; one discrete happening (a touch, a roll, a refusal); one quote taken; one news item; one hand-entered update; one distinct version |

## The folder tree

```
data/
├── market/                     prices, quotes, depth, trades, open interest, the calendar; tables/ (Parquet of the minute bars)
├── spx/
│   ├── scanner/                the SPX options scanner (the left eye), the order-flow collector's readings, the wall sensor,
│   │                           the Watchtower's memory
│   ├── jev/                    live_lane/ · opening_lane/ · premarket_lane/ (the same stores in each), the shared
│   │                           jev_reads_and_grades_by_event/, tables/
│   ├── prediction/             base_pool/ · learner_pool/ · the code questions' answers · the forecasters' forecasts · tables/
│   └── claude_forecast/        Claude's own SPX forecast: inputs, answers, outcomes, test answers, rebuilt history,
│                               rulebooks, tables/, the scoreboard, the pause switch
├── sndk/                       the SNDK options scanner, its minute bars, its Claude reader and the reader's memory
├── voice_desk/                 the spoken SNDK desk's session and transcripts
├── screen_files/
│   ├── phone/                  the phone's cards (written only for the phone)
│   └── desktop/                the desktop's 3D view cache
├── operations/
│   ├── logs/                   every job's output and errors, the job-run logs, the runtime log
│   ├── locks/                  single-copy locks
│   ├── health/                 the table builds' counts and refused rows, the SNDK bars' health
│   └── *_state.json            the three alert states
└── archive/                    rollback copies (live writers) and retired data, dated at archiving
```

## Who writes what, who reads it

Readers are named by their consumer: **Claude forecast** (read runner, nightly, recorder), **SPX JEV** (the live,
opening and premarket lanes), **Matcher / Additive** (the prediction system's learners), **pools** (the base pool and the
learner pool), **prediction nightly** (`spx_jev.mirai_prediction.nightly_job`), **nightly store build**
(`spx_jev.store`), **scanner** (hunter, the lenses, the dashboard), **Watchtower**, **grader** (`gex_polarity_ab`),
**tape collector** (`lob_flow`), **wall sensor** (`siege`), **SNDK scanner / SNDK bars / SNDK reader**, **desktop**
(index.html and the server's snapshot), **phone** (the pages under `static/m/`), **alerts** (the `watch` package),
**health** (`health_checks`), **3D view** (`book_flow`), **voice desk**. The job in the writer column is the launchd
label without `com.mirai-station.`; `docs/system_map.md` maps each job to its entry module.

Five stores are **interim twins**: each keeps its own place today so nothing is lost, and a later phase merges it into
the store named on its row.

### market/

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `SPX_PRICE_BARS_BY_MINUTE` | `market/spx_price_bars_by_minute/{day}.jsonl` | one SPX one-minute price bar; the one store of SPX minute bars since 2026-10-12 (the scanner grader's saved sessions, `spx_price_bars_by_minute_scanner_copy/{day}-SPX.json`, were folded in and the grader writes none); read through `station_store_files.read_spx_price_bars` | spx-jev-bars (`bars.append_day`; spx-jev-save-day tops up a short file) | SPX JEV (the scene, the clock, the flat zone, the grader, labels), nightly store build, Matcher / Additive, Claude forecast (payload builder, nightly grading, the seed CLI), the scanner (the grader's re-grades, the speedometer's baseline), desktop (the replay's price path), phone |
| `WATCHLIST_QUOTES_BY_MINUTE` | `market/watchlist_quotes_by_minute/{day}.jsonl` | one minute's quotes for ~46 watchlist symbols | spx-jev-context (`market_context.append_snapshot`) | SPX JEV (the scene, premarket, the clock, the flat zone, judgment), Matcher / Additive, Claude forecast (payload builder; the recorder's VIX1D task) |
| `WATCHLIST_PRICE_BARS_BY_MINUTE` | `market/watchlist_price_bars_by_minute/{day}.jsonl` | one minute's price bars for ~47 watchlist symbols; a full day per file | spx-jev-save-day (`market_context.backfill_day`, `refresh_breadth`; `--backfill` by hand) | SPX JEV, Matcher / Additive |
| `FUTURES_PRICE_BARS_BY_MINUTE` | `market/futures_price_bars_by_minute/{day}.jsonl` | one futures bar (/ES /ZN /BTC /MBT, 1- and 5-minute); a file runs 15:55 the prior day to 15:55, named for the day it leads into | spx-jev-overnight (`overnight.save_night`; spx-jev-premarket saves the night too) | SPX JEV (premarket, night ranks, labels), Matcher / Additive, nightly store build |
| `FUTURES_PRICE_BAR_CHANGE_COUNTS_BY_SAVE` | `market/futures_price_bar_change_counts_by_save.jsonl` | one save that changed a night's bars: rows added, restamped, failed, coverage per symbol | spx-jev-overnight (and premarket, through the same save) | SPX JEV (night ranks → labels) |
| `FUTURES_CONTRACTS_AND_PAST_ROLLS_LATEST` | `market/futures_contracts_and_past_rolls_latest.json` | the current contract per futures symbol, plus every roll detected so far | spx-jev-overnight (`rolls.save`) | SPX JEV (overnight, premarket, labels) |
| `SPX_VIX_RATES_PRICE_BARS_BY_DAY` | `market/spx_vix_rates_price_bars_by_day/{symbol}.jsonl` | one daily bar ($SPX, TLT, $TNX, $VIX, $VIX9D); rewritten whole each save | spx-jev-save-day (`daily_closes.save`) | SPX JEV (judgment), Matcher / Additive, Claude forecast (the nightly's official close, payload builder) |
| `NEWS_HEADLINES_BY_ARTICLE` | `market/news_headlines_by_article/{day}.jsonl` | one news headline captured from a feed, stamped with the poll's own clock | spx-jev-headlines (`headlines.poll`) | SPX JEV (the judgment questions) |
| `NEWS_HEADLINES_SEEN_STATE` | `market/news_headlines_seen_state.json` | fingerprints of headlines already captured (48 h) | spx-jev-headlines | the same poll |
| `SPX_SAME_DAY_OPTIONS_RAW_DAY_FOLDER` | `market/spx_same_day_options_raw_day_folders/{day}/` | one day's raw folder: `tape.jsonl`, `sweeps.jsonl`, `spy-depth.jsonl`, gzipped in place once the day is finished and kept forever (nothing prunes it; a retention phase may). The one copy of the raw tape since 2026-10-12: the recorder's gzip copies were folded in | lob-collector (`io_state.append`, `rotate` gzips finished days) | Matcher / Additive (depth by day), 3D view (which days exist), the Claude forecast's backup (finished days, to iCloud) |
| `SPX_SAME_DAY_OPTIONS_AND_SPY_DEPTH_RAW_STREAM` | `.../{day}/{stream}.jsonl` (`tape`, `sweeps`, `spy-depth`; `.jsonl.gz` once the day is over) | one raw line of a stream, plus its gap markers | lob-collector | tape collector (crash replay, the gzip at start) |
| `SPX_SAME_DAY_OPTIONS_TRADES_BY_TRADE` | `.../{day}/tape.jsonl` | one same-day SPX option trade with its quote | lob-collector (through the stream store) | SPX JEV (labels: volume sources, options flow), 3D view (the board's source) |
| `SPX_SAME_DAY_OPTIONS_DEPTH_BY_MINUTE` | `.../{day}/sweeps.jsonl` | one minute's bid/ask sizes and spread per delta band | lob-collector (through the stream store) | SPX JEV (options-flow labels), Matcher / Additive |
| `SPY_VOLUME_BY_MINUTE` | `market/spy_volume_by_minute/{day}.json` | one day's SPY volume for every minute (`{day, minutes_present, minute_volumes: {"<minute of day>": shares}, written_at}`; the files the recorder copied before 2026-10-12 carry `copied_at` and `source` instead). The one store of SPY's volume per minute: the wall sensor's one-file memory (`spx/scanner/spy_volume_baselines_state.json`) was folded in; read and written through `station_store_files` | left-eye (the wall sensor, today's file on every healthy scan, `siege/engine`) | the wall sensor (its baseline: the newest 60 files), SPX JEV (the volume labels), Matcher / Additive (code features), Claude forecast (payload builder: SPY volume vs usual); mirrored to iCloud |
| `SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_LATEST` | `market/spx_monthly_quarterly_open_interest_latest.json` | newest open interest and walls for the monthly and quarter-end expiries (the "dated book") | dated-book (`dated_gex_feed --pull`) | scanner (the diary summary inside the scan), Claude forecast (payload builder), desktop |
| `SPX_MONTHLY_QUARTERLY_OPEN_INTEREST_BY_DAY` | `market/spx_monthly_quarterly_open_interest_by_day/{stamp}.json` | one pull's open interest and walls for those expiries, the stamp its own `as_of` (`station_store_files.open_interest_book_stamp`) | dated-book (`dated_gex_feed --pull`, beside the latest; the recorder copied the latest to make it until 2026-10-12) | Claude forecast (payload builder: the newest book at or before a read's cut); mirrored to iCloud |
| `SPX_PRICE_BARS_BY_5_MINUTES` | `market/spx_price_bars_by_5_minutes/{day}.jsonl` | one SPX five-minute price bar | spx-claude-forecast-recorder | nothing yet (kept as insurance); mirrored to iCloud |
| `VIX_ONE_DAY_CLOSES_BY_DAY` | `market/vix_one_day_closes_by_day.jsonl` | one session's last VIX1D quote | spx-claude-forecast-recorder | Claude forecast (payload builder: the prior close; the recorder's already-written check) |
| `LEVERAGED_FUND_ASSETS_BY_RUN` | `market/leveraged_fund_assets_by_run.jsonl` | leveraged index funds' assets and leverage at one recorder run | spx-claude-forecast-recorder | Claude forecast (payload builder: the close rebalance estimate) |
| `SPX_LARGEST_STOCK_WEIGHTS_BY_UPDATE` | `market/spx_largest_stock_weights_by_update.json` | one dated, hand-kept set of the 10 largest index weights | by hand (`python -m spx_jev.index_weights --add`) | SPX JEV (leadership labels), Matcher / Additive |
| `MARKET_EVENT_CALENDAR_BY_EVENT` | `settings/market_event_calendar_by_event.json` (in the repo, not under `data/`) | one scheduled event and its time, with its tier (1, pre_open, data_10am, data_2pm, fed_speaker, auction), source and whether its date is published; `covers_from`, `pre_open_covers_from` and `covers_through` say which days it is kept for. The station's one event calendar since 2026-10-12: the tape collector's copy under `market/` was folded in (one row added, 2026-07-14 CPI) | by hand (extend it before it runs out) | SPX JEV (events, story labels), Matcher / Additive, Claude forecast (payload builder); the tape collector (baselines' clean days, alert blocks) and the scanner's event lens and Watchtower read its CPI and FOMC rows only |
| `SPX_PRICE_BARS_BY_MINUTE_TABLE` | `market/tables/spx_price_bars_by_minute/day=…/` | Parquet of the SPX minute bars (one row = one minute) | spx-jev-store | Matcher / Additive |
| `FUTURES_PRICE_BARS_BY_MINUTE_TABLE` | `market/tables/futures_price_bars_by_minute/day=…/` | Parquet of the futures bars | spx-jev-store | Matcher / Additive |

### spx/scanner/

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN` | `spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl` | one scan: spot, dealer levels, regime, verdicts, sensors (the scanner's "diary") | left-eye (`reversion_lens.record`, from `hunter.py`) | scanner, Watchtower, grader, alerts, Claude forecast (payload builder, seed), SPX JEV (state builder, labels), Matcher / Additive (code question inputs), desktop, phone, 3D view (`track.py`), the spx-jev runner's wait on its mtime |
| `SPX_OPTIONS_SCANNER_GRADES_BY_DAY` | `spx/scanner/spx_options_scanner_grades_by_day.jsonl` | one day's grades of magnets, engines, heads and stance (the "report card") | gex-polarity (`gex_polarity_ab.persist`, one row per day, upsert) | scanner (`promotion_gate` → `live_allowed`), tape collector nightly (`lob_bridge.promotion_gate`), Watchtower, desktop |
| `SPX_ORDER_FLOW_READINGS_BY_MINUTE` | `spx/scanner/spx_order_flow_readings_by_minute/{day}.jsonl` | one minute's order-flow reading (SPX, plus the SPY control) | lob-collector (`lob_flow/daemon.fold_once`) | tape collector nightly (baselines fold), Claude forecast (payload builder), SPX JEV (labels), Matcher / Additive |
| `SPX_ORDER_FLOW_GRADES_BY_DAY` | `spx/scanner/spx_order_flow_grades_by_day/{day}.json` | one day's hit counts for order-flow calls | gex-polarity (`lob_bridge.nightly`) | desktop |
| `SPX_OPTIONS_DEPTH_BASELINES_STATE` | `spx/scanner/spx_options_depth_baselines_state.json` | normal SPX option bid/ask size and spread for each 5-minute slot, per delta band; the last ~20 days' samples per slot | gex-polarity (`lob_bridge.nightly` → `baselines.fold_day_into_baselines`) | tape collector (the daemon's fold loop), desktop |
| `SPY_DEPTH_BASELINES_STATE` | `spx/scanner/spy_depth_baselines_state.json` | normal SPY bid/ask size for each 5-minute slot, same shape | gex-polarity (same fold, engine `spy_depth`) | tape collector |
| `SPX_DEALER_WALL_TOUCHES_BY_EVENT` | `spx/scanner/spx_dealer_wall_touches_by_event/{day}.jsonl` | one dealer-wall touch: SPY volume vs normal, held or broke | left-eye (wall sensor inside the scan, `siege/engine._resolve`) | desktop |
| `SPX_DEALER_WALLS_LATEST` | `spx/scanner/spx_dealer_walls_latest.json` | each watched dealer wall's distance and status now (field names frozen for the desktop) | left-eye (wall sensor, every scan) | desktop, `siege_bridge.py --status` |
| `SPX_OPTIONS_SCANNER_CLAUDE_NOTES_BY_READ` | `spx/scanner/spx_options_scanner_claude_notes_by_read/{day}.jsonl` | one Claude second-opinion note on the scanner's board, with a level snapshot | left-eye (Watchtower, `lefteye_rag.record_slice`) | Watchtower (its history CLI during the scan) |
| `SPX_OPTIONS_SCANNER_CLAUDE_SESSION_SUMMARIES_BY_DAY` | `spx/scanner/spx_options_scanner_claude_session_summaries_by_day.jsonl` | one session's summary of the scanner's Claude notes; rebuilt on demand by the history CLI | Watchtower (`lefteye_rag.rollup`, on demand) | Watchtower (the same CLI) |
| `SPX_OPTIONS_SCANNER_CLAUDE_TERRAIN_STATE` | `spx/scanner/spx_options_scanner_claude_terrain_state.json` | the standing terrain drawn from the summaries; rebuilt on demand | Watchtower (`lefteye_rag.terrain`, on demand) | Watchtower |
| `SPX_OPTIONS_SCANNER_SIGNAL_CHECKS_BY_SCAN` | `spx/scanner/spx_options_scanner_signal_checks_by_scan/{day}.jsonl` | one scan's signal check: what was evaluated, would it fire, any paper signals fired | left-eye (`hunter._persist_scan`) | alerts (`gex_alerts`, fresh fires) |
| `SPX_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE` | `spx/scanner/spx_options_strike_volume_weights_state.json` | decaying traded-volume weight per SPX strike (ledger `native_SPX`) | left-eye (`lefteye_fill_ledger.annotate`, from the native feed) | scanner (the same ledger, previous weights) |
| `SPY_OPTIONS_STRIKE_VOLUME_WEIGHTS_STATE` | `spx/scanner/spy_options_strike_volume_weights_state.json` | decaying traded-volume weight per SPY strike (ledger `proxy_SPY`) | left-eye (the same ledger, from the SPY proxy book) | scanner |
| `SPX_OPTIONS_VOLUME_LAST_SCAN_STATE` | `spx/scanner/spx_options_volume_last_scan_state.json` | the previous scan's cumulative volume per strike | left-eye (`reversion_lens._save_tape_prev`) | scanner (the next scan) |
| `SPX_DEALER_WALL_TOUCHES_TODAY_STATE` | `spx/scanner/spx_dealer_wall_touches_today_state.json` | today's wall-touch episodes, open and resolved (cleared each session) | left-eye (wall sensor) | wall sensor |
| `SPX_OPTIONS_TRADES_RESUME_POINT_STATE` | `spx/scanner/spx_options_trades_resume_point_state.json` | the last trade time read for each contract (the collector's cursor) | lob-collector | tape collector |
| `SPX_ORDER_FLOW_READINGS_LATEST` | `spx/scanner/spx_order_flow_readings_latest.json` | the newest order-flow reading for the scanner (the one file the scan tick reads from the collector) | lob-collector (`daemon.fold_once`) | scanner (`lob_bridge.read_latest`), tape collector (replay after a restart) |
| `SPX_DEALER_GAMMA_LEVELS_LATEST` | `spx/scanner/spx_dealer_gamma_levels_latest.json` | the scanner's magnet, walls and flip, handed to the collector | left-eye (`lob_bridge.write_gex_context`, inside the scan) | tape collector (which contracts to focus on) |
| `SPX_OPTIONS_SCANNER_SIGNALS_SENT_STATE` | `spx/scanner/spx_options_scanner_signals_sent_state.json` | today's signals already fired, so none repeat | left-eye (`hunter.save_state`) | scanner |

### spx/jev/

The grader (`grade.run`) runs inside whichever lane job just read or closed out (spx-jev, spx-jev-tape,
spx-jev-premarket); `grade.main` runs it by hand.

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `JEV_LANE_FOLDER` | `{lane}/` | a lane's folder, made on its first write | the lane's job | pools (find `spx/prediction/` from the live lane's folder), SPX JEV (cadence), alerts, health |
| `JEV_READS_AND_GRADES_BY_EVENT` | `spx/jev/jev_reads_and_grades_by_event/{day}.jsonl` | one read, grade or close-out from any lane (the archive: the raw record for learning) | the three lane jobs (`archive.append`; the grader's record under whichever job grades) | nightly store build, the prediction read hook, Matcher / Additive, SPX JEV (judgment), Claude forecast (payload builder, nightly); the backfill and the reading checks by hand |
| `JEV_ANSWERS_BY_READ` | `{lane}/jev_answers_by_read/{day}.jsonl` | one read: the questions sent and JEV's answers (`row_ts`; the lane is the folder) | the three lane jobs | SPX JEV (last read, same-row check, cadence), alerts (premarket outage), health |
| `JEV_AND_POOL_FORECASTS_BY_READ` | `{lane}/jev_and_pool_forecasts_by_read/{day}.jsonl` | one read's combined up/flat/down forecast per horizon (the "hour" record) | the three lane jobs | SPX JEV (day calls, grader, premarket), pools, nightly store build, the prediction read hook, Claude forecast (payload builder) |
| `JEV_END_PRICE_GRADES_BY_READ` | `{lane}/jev_end_price_grades_by_read.jsonl` | one read's end-price grades, each horizon | the grader | SPX JEV (grader, card), pools (the end-price loop) |
| `JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON` | `{lane}/jev_average_price_grades_by_read_and_horizon.jsonl` | one horizon of one read, graded on average price | the grader (`integral_run`, under the lock; `--integral-backfill` by hand) | SPX JEV (grader, the card's grade box), pools (the average-price loop), nightly store build, Claude forecast (nightly grading), phone |
| `JEV_AVERAGE_PRICE_GRADES_LOCK` | `{lane}/jev_average_price_grades_by_read_and_horizon.jsonl.lock` | the grader's lock while it reads, dedupes and appends | the grader | lock only |
| `JEV_QUESTION_WEIGHTS_STATE` | `{lane}/jev_question_weights_state.json` | each question's weight plus the latest grading tallies | the grader | SPX JEV (the sums) |
| `JEV_LAST_ANSWERS_STATE` | `{lane}/jev_last_answers_state.json` | the last answer to each slow question, reused when not due | spx-jev, spx-jev-tape (`cadence.save_last`) | SPX JEV (cadence; a lane reads the other's too) |
| `JEV_QUESTION_ASK_SCHEDULE_STATE` | `spx/jev/live_lane/jev_question_ask_schedule_state.json` | which questions are due at each read (the live lane's learned cadence) | spx-jev (`cadence.ensure_cadence`; `--write` by hand) | SPX JEV (cadence) |
| `HISTORICAL_ODDS_END_PRICE_DAYS_STATE` | `spx/jev/live_lane/historical_odds_end_price_days_state.json` | the past days behind each day's historical odds (end price); a cache read and rewritten in one call | spx-jev (`clock.odds`) | SPX JEV |
| `HISTORICAL_ODDS_AVERAGE_PRICE_DAYS_STATE` | `spx/jev/live_lane/historical_odds_average_price_days_state.json` | the same, for the average price | spx-jev (`clock.integral_odds`) | SPX JEV, pools (the average-price loop's reference) |
| `JEV_TABLES_FOLDER` | `spx/jev/tables/` | the four JEV tables' folder | the tables make it | named in the store build's summary line |
| `JEV_READ_SUMMARIES_BY_READ_TABLE` | `spx/jev/tables/jev_read_summaries_by_read/day=…/` | one read: lane, times, questions asked, answered, lost | spx-jev-store | prediction nightly (the day's read ids) |
| `JEV_ANSWERS_BY_READ_AND_QUESTION_TABLE` | `spx/jev/tables/jev_answers_by_read_and_question/day=…/` | one question's answer in one read (layer 3 of the answer matrix) | spx-jev-store | Matcher / Additive (the answer matrix: the nightly refit, the hook's first-day fallback, the replay) |
| `JEV_AND_POOL_FORECASTS_BY_READ_AND_HORIZON_TABLE` | `spx/jev/tables/jev_and_pool_forecasts_by_read_and_horizon/day=…/` | one horizon's forecast at one read, every source | spx-jev-store | Matcher / Additive (the matrix's base rows), the replay |
| `JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON_TABLE` | `spx/jev/tables/jev_average_price_grades_by_read_and_horizon/day=…/` | one horizon of one read, graded on average price (same name as its raw file) | spx-jev-store | Matcher / Additive (outcomes), prediction nightly (scores, the learner pool's day) |

### spx/prediction/

The read hook runs inside the spx-jev live read (in-process, else detached by `service_hook`); the base pool learns
in the grader's weights step, the live lane only.

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `PREDICTION_FOLDER` | `spx/prediction/` | the prediction system's folder | its jobs make it | every prediction path helper |
| `BASE_POOL_STATE` | `spx/prediction/base_pool/base_pool_state/{horizon}.json` | base pool members, weights and evidence for one horizon: `next_30_minutes`, `next_60_minutes` (the end-price loop), `average_of_next_30_minutes` (the average-price loop) | spx-jev (`base_pool.save_state`, `integral_loop.save_state`) | pools (the snapshot at each read, the learning step) |
| `BASE_POOL_UPDATES_BY_DAY_AND_HORIZON` | `spx/prediction/base_pool/base_pool_updates_by_day_and_horizon/average_of_next_30_minutes.jsonl` | one night's learning step for the average-price horizon: applied, or why not | spx-jev (`integral_loop._log`) | pools (learned-fresh check, dry run) |
| `BASE_POOL_STATE_ROLLBACK_COPIES` | `archive/base_pool_state_rollback_copies/` | see the archive, below | spx-jev (`base_pool.archive_state`) | by hand |
| `LEARNER_POOL_STATE` | `spx/prediction/learner_pool/learner_pool_state/{horizon}.json` | the learner pool's forecaster weights (move and direction), days learned, the freeze; the code's sum ids `next_60` and `average_30` are the files `next_60_minutes.json` and `average_of_next_30_minutes.json` | spx-jev (the read hook, on the first read), spx-jev-prediction-nightly (`learner_pool.save_learner_pool`) | pools (the hook, the nightly) |
| `LEARNER_POOL_TRAINED_FORECASTERS_BY_DAY` | `spx/prediction/learner_pool/learner_pool_trained_forecasters_by_day/{day}_{horizon}.json` | one day's trained Matcher, Additive scorer and corrected JEV, with the answer matrix they were fitted on; the newest 10 days are kept | spx-jev-prediction-nightly (`voice_fits.fit_voices_for_day`; the replay by hand) | Matcher / Additive (the read hook loads the day's fits) |
| `LEARNER_POOL_ROLLBACK_COPIES` | `archive/learner_pool_rollback_copies/` | see the archive, below | the prediction system (`backup_before_change`, the replay) | by hand; the nightly prunes it |
| `CODE_QUESTION_ANSWERS_BY_READ` | `spx/prediction/code_question_answers_by_read/{day}.jsonl` | one read's ~79 code-computed answers | spx-jev (inside the read, `code_feature_inputs.record_code_features`; the backfill by hand) | Matcher / Additive (the matrix's layers 1-2), SPX JEV (the judgment gates), Claude forecast (the payload's code block) |
| `FORECASTS_BY_FORECASTER_READ_AND_HORIZON` | `spx/prediction/forecasts_by_forecaster_read_and_horizon/{day}.jsonl` | one forecaster's up/flat/down at one read and horizon (one line per forecaster plus the learner pool's mix) | spx-jev (the read hook) | prediction nightly (the table build), the hook's already-written check, the replay |
| `FORECASTS_BY_FORECASTER_READ_AND_HORIZON_TABLE` | `spx/prediction/tables/forecasts_by_forecaster_read_and_horizon/day=…/` | the same, checked, as Parquet | spx-jev-prediction-nightly | prediction nightly (scores, the learner pool's day) |
| `GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE` | `spx/prediction/tables/grades_by_forecaster_read_and_horizon/day=…/` | one forecaster graded at one read and horizon: its penalty, right or wrong, the reference's penalty | spx-jev-prediction-nightly | prediction nightly (the scoreboard card) |

### spx/claude_forecast/

The read runner is started detached by the SPX live read (`spx_jev.service` → `spx_claude_forecast.hook`), so it runs
under the spx-jev job; the seed (rebuild) CLI and the control CLI are run by hand.

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `CLAUDE_FORECAST_FOLDER` | `spx/claude_forecast/` | the package's own folder: the record, the tables, the rulebooks; the only place under `data/` its writers may write, beside the recorder's copies, its card, logs and locks | every Claude forecast job (`ensure_folders`) | the recorder's and nightly's backup walk it |
| `CLAUDE_FORECAST_INPUTS_BY_READ` | `spx/claude_forecast/claude_forecast_inputs_by_read/{day}.jsonl` | the frozen scene sent to Claude for one read (a `blind` line when it could not be built) | read runner | read runner (already-written check), nightly (seal, library, test variants) |
| `CLAUDE_FORECAST_ANSWERS_BY_READ` | `spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl` | Claude's two answers and the final forecast for one read | read runner | nightly (test variants, scores, scorecard), the daily call cap (read runner and nightly) |
| `CLAUDE_FORECAST_OUTCOMES_BY_READ` | `spx/claude_forecast/claude_forecast_outcomes_by_read/{day}.jsonl` | what price did after one read, one line per horizon, sealed, never edited | spx-claude-forecast-nightly (seal and catch-up) | nightly (seal, library, scores) |
| `CLAUDE_FORECAST_TEST_ANSWERS_BY_VARIANT_AND_READ` | `spx/claude_forecast/claude_forecast_test_answers_by_variant_and_read/{day}.jsonl` | one test variant's answer and forecast lines on one read's frozen inputs; never mixed into the live record | spx-claude-forecast-nightly (`test_variants`) | nightly (test variants, scores), the daily call cap |
| `CLAUDE_FORECAST_RULEBOOKS_BY_VERSION` | `spx/claude_forecast/claude_forecast_rulebooks_by_version/{sha}.txt` | one distinct rulebook text sent to Claude | read runner (write once) | nothing in code; kept so a sent prompt can be rebuilt by hand |
| `CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ` | `spx/claude_forecast/claude_forecast_rebuilt_inputs_by_read/{day}.jsonl` | one past read's inputs, rebuilt from history | the seed CLI (`python -m spx_claude_forecast.seed`, by hand) | nightly (library), the seed CLI (slots already seeded) |
| `CLAUDE_FORECAST_REBUILT_OUTCOMES_BY_READ` | `spx/claude_forecast/claude_forecast_rebuilt_outcomes_by_read/{day}.jsonl` | what price did after one rebuilt past read | the seed CLI | nightly (library) |
| `CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BY_READ_TABLE` | `spx/claude_forecast/tables/claude_forecast_fingerprints_and_outcomes_by_read.parquet` | one past read, live or rebuilt: its seven-fact fingerprint, quality gates and sealed outcome; precedents are drawn from it | spx-claude-forecast-nightly (`library.write_library`) | read runner (base rate, precedents), nightly (test variants) |
| `CLAUDE_FORECAST_GRADES_BY_FORECASTER_READ_AND_HORIZON_TABLE` | `spx/claude_forecast/tables/claude_forecast_grades_by_forecaster_read_and_horizon.parquet` | one forecaster (Claude final, Claude answer 1, the comparison forecasters, test variants) graded at one read and horizon | spx-claude-forecast-nightly (`scoring`) | nightly (scorecard) |
| `CLAUDE_FORECAST_FINGERPRINTS_AND_OUTCOMES_BUILD_LATEST` | `spx/claude_forecast/tables/claude_forecast_fingerprints_and_outcomes_build_latest.json` | the last build of the fingerprints table: when, from which day, how many rows, which fields, the rows' hash | spx-claude-forecast-nightly (`library`) | read runner (stamped on every payload) |
| `CLAUDE_FORECAST_SCOREBOARD_LATEST` | `spx/claude_forecast/claude_forecast_scoreboard_latest.json` | Claude's skill vs the base rate, plus watchdog counts (Will's view) | spx-claude-forecast-nightly (`scorecard`) | read runner (the card's verdict) |
| `CLAUDE_FORECAST_PAUSE_AND_LIMITS` | `spx/claude_forecast/claude_forecast_pause_and_limits.json` | the pause switch and the daily call limits | the control CLI (`python -m spx_claude_forecast.control`) or Will by hand | read runner, nightly (test variants), the control CLI |

### sndk/

The reader's memory CLI (`sndk_rag.py`) is run as a subprocess by the desktop server and by the voice desk's model;
it is the writer of the two on-demand files.

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN` | `sndk/sndk_options_scanner_results_by_scan/{day}.jsonl` | one SNDK scan (~2 min): spot, dealer levels, verdicts | sndk (`sndk_hunter.tick`) | SNDK scanner (its prior rows), SNDK reader, voice desk, alerts (SNDK outage), health, desktop, 3D view (`track.py`) |
| `SNDK_PRICE_BARS_BY_MINUTE` | `sndk/sndk_price_bars_by_minute/{day}.jsonl` | one SNDK one-minute price bar | sndk-bars | SNDK bars, SNDK reader, voice desk, desktop, health |
| `SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN` | `sndk/sndk_options_reader_gates_and_answers_by_run/{day}.jsonl` | one reader run: the gate checks, then Claude's answer or why it was withheld | sndk-read (`read_once`) | SNDK reader (its prior rows), voice desk, alerts, health, desktop |
| `SNDK_PRICE_BAR_FACTS_BY_READ` | `sndk/sndk_price_bar_facts_by_read/{day}.jsonl` | price-bar facts prepared for one read | sndk-read (`sndk_side.append`) | desktop (the server rebuilds the packet from the bars rather than reading it) |
| `SNDK_LAST_PRICES_BY_QUOTE` | `sndk/sndk_last_prices_by_quote/{day}.jsonl` | one SNDK price quote the desktop server took | viewstation (`snapshot.live_spot`) | desktop |
| `SNDK_OPTIONS_READER_NOTES_BY_READ` | `sndk/sndk_options_reader_notes_by_read/{day}.jsonl` | one short written note on one reading | sndk-read (`sndk_rag.record_slice`) | the memory CLI (desktop server, voice desk), desktop |
| `SNDK_OPTIONS_READER_SESSION_SUMMARIES_BY_DAY` | `sndk/sndk_options_reader_session_summaries_by_day.jsonl` | one session's summary of the reader's notes; rebuilt on demand | the memory CLI (`sndk_rag.rollup`) | the memory CLI, desktop |
| `SNDK_OPTIONS_READER_TERRAIN_STATE` | `sndk/sndk_options_reader_terrain_state.json` | the standing terrain drawn from the summaries; rebuilt on demand | the memory CLI (`sndk_rag.terrain`) | the memory CLI, desktop |
| `SNDK_OPTIONS_CHAIN_STATE` | `sndk/sndk_options_chain_state.json` | the last option chain pulled (strikes, open interest, gamma) | sndk (`sndk_feed`) | SNDK scanner |
| `SNDK_OPTIONS_EXPIRIES_STATE` | `sndk/sndk_options_expiries_state.json` | today's listed expiries, found by probing | sndk | SNDK scanner |
| `SNDK_IMPLIED_VOLATILITY_STATE` | `sndk/sndk_implied_volatility_state.json` | the latest at-the-money volatility and daily move | sndk | SNDK scanner |
| `SNDK_OPTIONS_READER_REASONING_SWITCH` | `sndk/sndk_options_reader_reasoning_switch.json` | reasoning on or off (the pause; the server refuses to write it) | by hand | SNDK reader, desktop |
| `SNDK_OPTIONS_READER_PERCENTILE_HISTORY_STATE` | `sndk/sndk_options_reader_percentile_history_state.json` | past sessions' values, used for percentile ranks; a cache | whichever process builds the scene first: sndk-read, viewstation, voice | the same three |
| `SNDK_PAST_VOLUME_CURVES_STATE` | `sndk/sndk_past_volume_curves_state.json` | past days' cumulative volume by minute; a cache | sndk-read, viewstation (`sndk_board.prior_volume_series`) | the same |

### voice_desk/

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `VOICE_DESK_SESSION_STATE` | `voice_desk/voice_desk_session_state.json` | the open agent session's id and day | voice (`convo._persist`) | voice desk (resume on connect) |
| `VOICE_DESK_TRANSCRIPTS_BY_EXCHANGE` | `voice_desk/voice_desk_transcripts_by_exchange/{day}.jsonl` | one spoken exchange: what was asked and what the desk said | voice (`convo._log`) | nothing in code; the raw explorer never serves them |

### screen_files/

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `JEV_LIVE_CARD` | `screen_files/phone/jev_live_card.json` | the live lane's newest read, as the phone shows it | spx-jev (`service.write_card`) | phone (`jev-spx.html`), SPX JEV (the close-out reads it back) |
| `JEV_OPENING_CARD` | `screen_files/phone/jev_opening_card.json` | the opening lane's newest read (the code's `tape` lane) | spx-jev-tape | phone, SPX JEV (close-out) |
| `JEV_PREMARKET_CARD` | `screen_files/phone/jev_premarket_card.json` | the premarket lane's newest read | spx-jev-premarket (`premarket.py`) | phone, alerts (`spx_premarket_deadman`), health, SPX JEV (close-out retries) |
| `CLAUDE_FORECAST_CARD` | `screen_files/phone/claude_forecast_card.json` | Claude's newest forecast beside the base rate, the reasons in plain words, the scorecard's verdict | the Claude forecast read runner (`latest_card`) | phone |
| `FORECASTER_SCOREBOARD_CARD` | `screen_files/phone/forecaster_scoreboard_card.json` | each forecaster's skill vs the historical odds | spx-jev-prediction-nightly (`scoreboard.py`; also the replay CLI) | phone |
| `OPTIONS_FEED_STATUS_CARD` | `screen_files/phone/options_feed_status_card.json` | the options-feed outage banner: down since, back at | left-eye (`gex_alerts`, the alert step) | phone |
| `SPX_OPTIONS_TRADES_3D_VIEW_CARD` | `screen_files/desktop/spx_options_trades_3d_view_card/{day}.json` | one day's executed option trades binned by price and 5 minutes, for the desktop's 3D view; a rebuildable cache keyed on the tape file's size and time | viewstation (`book_flow/cache.py`, on the desktop's request) | 3D view (the same cache check), desktop through the server |

### operations/

| store (constant) | path | one row or file holds | written by | read by |
|---|---|---|---|---|
| `STATION_RUNTIME_LOG` | `operations/logs/station_runtime_log/{day}.log` | one day of `env.sh`'s `log()` lines (a runner's gate failure or disabled run, the installer, the venv bootstrap) | the job start scripts | by hand |
| `LAUNCHD_JOB_OUTPUT_LOG`, `LAUNCHD_JOB_ERRORS_LOG` | `operations/logs/{job}_output_log.txt`, `{job}_errors_log.txt` (`{job}` = the label without `com.mirai-station.`, hyphens as underscores) | what one launchd job printed, and its errors, today's only: appended on every run, rotated daily into `operations/logs/older/` (below) | `start_job_with_logs.sh`, every job | by hand; the health check names the path in a failure hint |
| `CLAUDE_FORECAST_JOB_RUNS_LOG` | `operations/logs/claude_forecast_job_runs_log.jsonl` | one Claude-forecast job run | the Claude forecast (read runner, nightly, recorder, seed, test variants) | its own pruner (30 days; nightly and recorder) |
| `CLAUDE_FORECAST_READ_JOB_LOG` | `operations/logs/claude_forecast_read_job_log.txt` | the detached read runner's printed output, appended per read | the hook that spawns the read runner (under spx-jev) | by hand |
| `PREDICTION_JOB_RUNS_LOG` | `operations/logs/prediction_job_runs_log.jsonl` | one prediction-system job or hook run | the prediction system (nightly, read hook, replay, backfill) | by hand |
| `FORECASTER_FORECASTS_JOB_LOG` | `operations/logs/forecaster_forecasts_job_log.txt` | the detached after-read forecaster hook's printed output | `service_hook` (under spx-jev) | by hand |
| `SPX_OPTIONS_TAPE_COLLECTOR_LOCK` | `operations/locks/spx_options_tape_collector.lock` | the collector's single-copy lock (its PID) | lob-collector | lock only |
| `CLAUDE_FORECAST_NIGHTLY_JOB_LOCK`, `CLAUDE_FORECAST_RECORDER_JOB_LOCK`, `CLAUDE_FORECAST_REBUILD_PAST_READS_JOB_LOCK` | `operations/locks/claude_forecast_{nightly,recorder,rebuild_past_reads}_job.lock` | one job's single-copy lock | spx-claude-forecast-nightly, -recorder, the seed CLI (the code's lock name `seed`) | lock only |
| `SCHWAB_SIGN_IN_LOCK` | `operations/locks/schwab_sign_in.lock` | the Schwab sign-in's single-copy lock while a bearer is refreshed | every process that refreshes the bearer through the Schwab client (left-eye, sndk, lob-collector, gex-polarity, dated-book, auth-watch) | lock only |
| `FUTURES_PRICE_BARS_SAVE_LOCK` | `operations/locks/futures_price_bars_save.lock` | the overnight saver's single-copy lock | spx-jev-overnight, spx-jev-premarket | lock only |
| `NEWS_HEADLINES_POLL_LOCK` | `operations/locks/news_headlines_poll.lock` | the headline poller's single-copy lock | spx-jev-headlines | lock only |
| `SNDK_PRICE_BARS_HEALTH_LATEST` | `operations/health/sndk_price_bars_health_latest.json` | the SNDK bar fetch's last result | sndk-bars | health, desktop |
| `TABLE_BUILD_COUNTS_BY_TABLE_AND_DAY` | `operations/health/table_build_counts_by_table_and_day/day=…/` | one table's row counts from one day's build | spx-jev-store | health (was the store built?) |
| `TABLE_BUILD_REFUSED_ROWS_BY_EVENT` | `operations/health/table_build_refused_rows_by_event/day=…/` | one row a table check refused, with its reason (written only when a row is refused) | spx-jev-store | by hand |
| `FORECASTER_TABLE_REFUSED_ROWS_BY_EVENT` | `operations/health/forecaster_table_refused_rows_by_event/{day}.jsonl` | one forecaster-table row refused, with its reason | spx-jev-prediction-nightly | by hand |
| `JEV_PREMARKET_OUTAGE_ALERT_STATE` | `operations/jev_premarket_outage_alert_state.json` | today's premarket outage pages already sent | spx-premarket-deadman | alerts |
| `SNDK_OUTAGE_ALERT_STATE` | `operations/sndk_outage_alert_state.json` | today's SNDK outage pages and down-since times | sndk-deadman | alerts, health |
| `PHONE_PUSHES_WALL_TIMERS_FEED_OUTAGES_STATE` | `operations/phone_pushes_wall_timers_feed_outages_state.json` | the alerts job's working memory for today: pushes already sent, dealer-wall alert timers, feed sirens and feed-down-since times | left-eye (the `gex-alerts` step) | alerts |

## Old days are gzipped after 14 days; logs rotate daily, kept 14 days

**Day files.** Nothing is deleted from a data store. Once a night, after the store build (the spx-jev-store job,
16:40 ET on weekdays), `station_store_files.compress_finished_days` gzips in place every day file older than 14 days
(`KEEP_PLAIN_DAYS`) of the big daily stores: `{day}.jsonl` becomes `{day}.jsonl.gz`, the same bytes (checked to
decompress identically before the plain file goes) and the same modified time. Today's and the last 14 days' files
stay plain. The stores (`COMPRESSED_DAY_FILE_STORES`): `SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN`,
`SPX_ORDER_FLOW_READINGS_BY_MINUTE`, `WATCHLIST_QUOTES_BY_MINUTE`, `WATCHLIST_PRICE_BARS_BY_MINUTE`,
`JEV_READS_AND_GRADES_BY_EVENT`, each lane's `JEV_ANSWERS_BY_READ` and `JEV_AND_POOL_FORECASTS_BY_READ`,
`CLAUDE_FORECAST_INPUTS_BY_READ`, `CLAUDE_FORECAST_ANSWERS_BY_READ`, `CLAUDE_FORECAST_REBUILT_INPUTS_BY_READ`,
`SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN` and `SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN`. Not gzipped: the futures bars
(spx-jev-overnight restamps every saved night whole when a roll changes), the small daily stores, the Parquet tables,
the raw tape (the collector gzips its own finished days), and every `_state`, `_latest` and `_card` file. Every reader
of a gzipped store reads a day through `station_store_files` (`read_day_file_text`, `read_day_file_lines`,
`open_day_file`, `day_file_exists`, `day_files`, `day_file_days`, `day_file_signature`) with the plain path the store
names, so a `.gz` day reads exactly like the plain file; the server's `/api/store/index`, `/api/store/file` and raw
explorer list and serve it as the plain file, decompressed. A line written to a day already gzipped (a backfill by hand)
lands in a new plain file beside the `.gz`, is read after it, and is folded into the `.gz` the next night;
`station_store_files.replace_day_file_text` rewrites a gzipped day as its `.gz`. By hand:
`python station_store_files.py --compress-finished-days [--state-dir DIR]`.

**Job logs.** `start_job_with_logs.sh` rotates a job's `{job}_output_log.txt` and `{job}_errors_log.txt` as the job
starts: a log last written on an earlier day moves to `operations/logs/older/{that day}/` and is gzipped; a rotated
day older than 14 days is removed. A job that never restarts (viewstation, voice, caffeinate) rotates at its next start.
The `.jsonl` run logs are not rotated (`claude_forecast_job_runs_log.jsonl` prunes itself to 30 days).

## The training sets and their join keys

| set | files | one row is | join key |
|---|---|---|---|
| Claude forecast | `claude_forecast_inputs_by_read/` + `claude_forecast_answers_by_read/` + `claude_forecast_outcomes_by_read/` (live); `claude_forecast_rebuilt_inputs_by_read/` + `claude_forecast_rebuilt_outcomes_by_read/` (history); `claude_forecast_test_answers_by_variant_and_read/` (test variants); the two tables built from them | one read (inputs, answers); one read per horizon (outcomes); one variant per read (test answers) | `read_id`: `live:<row_ts>` for a live read, `seed:<day>T<HH:MM>` for a rebuilt one. Outcomes add `horizon` (the newest `finalize_attempt_number` wins); test answers add `test_variant`. `claude_input_sha256` rides every line as the frozen scene's fingerprint but nothing joins on it; the library dedups a rebuilt row against a live one by `(trading_day, half_hour_slot_et)` |
| JEV reads, answers and grades | `{lane}/jev_answers_by_read/` (reads and answers), `{lane}/jev_and_pool_forecasts_by_read/` (the sums), `{lane}/jev_end_price_grades_by_read.jsonl`, `{lane}/jev_average_price_grades_by_read_and_horizon.jsonl`; `spx/jev/jev_reads_and_grades_by_event/` (every read, grade and close-out of every lane); the four tables under `spx/jev/tables/` | one read; one read's sums; one read's end-price grades; one horizon of one read on the average price | `read_id = "<lane>:<row_ts>"` (`archive.read_id`). The raw lane files carry `row_ts` alone (the lane is the folder); the archive and every table carry `read_id`. The end-price grades are keyed by `row_ts`; the average-price grades dedupe on `(row_ts, horizon, rule_version)` and `sum` names the sum graded. The store derives `read_id` and `sum_id` and keys its tables `read_summaries (read_id)`, `answers (read_id, question_id)`, `forecasts (read_id, sum_id)`, `average_grades (read_id, horizon)`. The Claude forecast matches the station's grades by `(row_ts, horizon)`; `row_ts` is recovered as `read_id.split(":", 1)[1]` |
| code question answers | `spx/prediction/code_question_answers_by_read/` | one read's ~79 code-computed answers (the matrix's `code:<question_id>` columns) | `read_id` (the line also carries `lane`, `day`, `row_ts`); the matrix joins it on `read_id`, and a read with no line is None in every code column |
| forecaster forecasts and grades | `spx/prediction/forecasts_by_forecaster_read_and_horizon/` → `tables/forecasts_by_forecaster_read_and_horizon/`; `tables/grades_by_forecaster_read_and_horizon/` | one forecaster's up/flat/down at one read and horizon; the same, graded | `(read_id, sum_id, voice_name)` is one forecast (the last line wins; `sum_id` is `average_30` or `next_60`). The grades table is the forecasts joined to the JEV average-price grades table on `(read_id, sum_id)`, never on `horizon`; the answer matrix's rows are the JEV forecasts table joined to those grades the same way, with the answers and the code answers pivoted onto them by `read_id` |

## The archive and the backup

**`data/archive/`** holds what is kept but no longer live:

| folder | holds | written by |
|---|---|---|
| `archive/2026-10-11_cleanup_retired/` | every store the cleanup retired, each under its old path, with `MANIFEST.md` saying what it was and why it went | the cleanup (by hand); delete after two weeks if nothing broke |
| `archive/2026-10-11_desktop_map_folder/` | the old Desktop map's numbered shortcuts and plain-English READMEs | the cleanup (phase 8) |
| `archive/2026-10-12_merged_duplicates_retired/` | the four superseded copies of phase 5's merges, each under its old path (`market/spx_price_bars_by_minute_scanner_copy/`, `market/spx_same_day_options_raw_day_folders_recorder_copy/`, `spx/scanner/spy_volume_baselines_state.json`, `market/market_event_calendar_by_event.json`) with `MANIFEST.md` | `scheduled_jobs/maintenance/merge_duplicate_stores_2026_10.py`, run once by hand; delete after two weeks if nothing broke |
| `archive/base_pool_state_rollback_copies/` | a base pool state learned under other constants, kept before a rebuild replaced it (`<state file>.pre-<constants hash>-<day>`) | the base pool (`base_pool.archive_state`), a live writer |
| `archive/learner_pool_rollback_copies/` | a learner pool file before it changed (`<file>.<UTC stamp>`, the newest 10 per name) and what a forced replay cleared (`replay_force_<stamp>/`, kept 7 days) | the prediction system (`backup_before_change`, the replay); the nightly prunes it |
| `archive/<YYYY-MM-DD>_<store name>_retired/` | the pattern for a later retirement: the store's files, dated at archiving | whoever retires a store |

A full copy of the data root from before any cleanup is
`~/.local/share/mirai-station/backups/state-before-cleanup-2026-10-11.tar.gz`.

**The iCloud backup.** The Claude forecast's recorder job (08:40 and 16:25 ET, Friday 17:30) and its nightly job
mirror two things into `~/Library/Mobile Documents/com~apple~CloudDocs/mirai-station-backups/`, each file under its
path relative to `data/`: the forecast folder `spx/claude_forecast/` and the irreplaceable stores under `market/`
(the recorder's own `spx_price_bars_by_5_minutes/`, `vix_one_day_closes_by_day.jsonl` and
`leveraged_fund_assets_by_run.jsonl`, plus `spx_monthly_quarterly_open_interest_by_day/`, `spy_volume_by_minute/` and
the finished days of `spx_same_day_options_raw_day_folders/`, which other jobs keep). A file is copied only when it
changed. Nothing else in `data/` is backed up; the recorder's retired copies already there stay.
The files the recorder put under `spx_claude_forecast/` there before 2026-10-11 stay until the owner decides
(`recorder.DEFAULT_BACKUP_DIR`; `--backup-dir` points a run elsewhere).

## How to add a store

1. **Name it by the rules above**: `<subject>_<content>_by_<what one row is>`, or a `_latest` / `_card` / `_state` /
   `_log` ending; full words, no code names; a daily file is `{day}.jsonl`; a table goes in its domain's `tables/`
   and keeps the raw folder's stem (its constant takes `_TABLE`).
2. **Define it once in `station_paths.py`**, in the section of the folder it lives in, as a `Store(...)` constant of
   the same name in upper case. A keyed family names its allowed keys there (`horizon={...}`); a store a package
   reaches from a folder it holds uses `within` / `folder_within`; a lane store uses `{lane}`.
3. **Reach it only through the constant** (`store.path(root, day=...)`, or the viewstation's by-name endpoint from a
   page); never spell the path in code or a page.
4. **Add its row here**: path, what one row holds, the job that writes it, who reads it. If it has a join key with a
   training set, say which. If it is a twin of a store that already exists, mark it interim and name the merge.
