# spx-jev — the JEV decision service beside the SPX diary

A **read-only sidecar** beside the left-eye scanner. Twice an hour on market
days it reads the SPX diary rows the scanner already writes, today's minute
bars and the market around the index, turns the numbers into plain-English
labels, asks JEV the questions, sums the answers into its call (where the
average price over the next 30 minutes sits, with the 30- and 60-minute
end-price sums kept beside it), grades the earlier calls against the bars,
and writes the phone's card. A second lane reads every 5 minutes through the opening, and
a third reads the night's futures at six checkpoints before the open. It
writes only its own stores (the lanes under `data/spx/jev/`, the learning
loops under `data/spx/prediction/`, its feeds under `data/market/` and the
phone's cards under `data/screen_files/phone/`), never into the scanner's files,
and never runs on the scan path.

The mechanics are SNDK JEV's (`skills/sndk-jev/`), copied and adapted; the
questions are the final SPX set (`spec/question_set.json`, 135 questions).
Since the cut-over of 2026-10-08 the live lane asks none of its 104 pre-merge
questions: they are retired there, kept in the set and the doc but never asked
again ("The pipeline", step 2), and the live lane's sums ride on the code's own
measurements and the judgment group's gated questions instead. A
question whose data no feed carries yet, or whose label is not built, is dark
and never asked, and says what it waits for; a question whose label is missing
on a read is skipped with the reason. Its eleven jobs are in `scheduled_jobs/launchd_job_definitions/`;
"Going live" says how they are set up.

JEV reads words and cannot compare numbers. So every comparison happens here,
in code, and is written out as a sentence with what it was judged against in it. A size is never judged
on a fixed line: it is ranked against the same measure at the same minute on up to the last 20 sessions,
needing 10 of them, and said by its third:

    price.recent_move = "over the last 30 minutes price rose 0.13 sigma, larger than 16 of the last 19 sessions
                         at this minute, top third: a strong move; the half hour closed between the top and
                         bottom fifths of its own range"

Sigma is the day's expected move for the S&P 500 index, so every distance is
a share of a normal day. A question points at that label by name, and JEV
returns a probability for each answer option. A call is a forecast, not a trade signal.

## Glossary

| File | Plain name | What it does |
|---|---|---|
| `spx_jev/state_builder.py` | The Scene | Loads one moment, point in time: a diary row with its siege read, today's finished bars, the prior sessions' bars, the market context, the lob-flow collector's signed 0DTE options tape. |
| `spx_jev/labels/` | The Labeller | One module per label family, each owning a fixed list of labels and exposing `build_<family>_labels(scene) -> LabelSet`, registered in `labels/registry.py`; the shared reads beside them: bar windows, the settled open (the 09:34 close) and the session extremes (`measures`), the sigma rulers (the morning anchor with its 09:40 guard, the live sigma, the straddle left, the normal-day sigma) and the tape unit (`rulers`), the same-minute rank against up to 20 prior sessions, in thirds and needing 10 of them under the owner's rule, with the half hour's move ranked once for every family (`ranks`), and how numbers are written (`words`). Anything a family cannot measure is left out, with the reason kept in `omitted`. |
| `spx_jev/row_adapter.py` | The Row Adapter | Cuts each 40 KB SPX diary row down to the few dozen fields the labeller reads, by name, and says which options book each field describes. |
| `spx_jev/cuts.py` | The Cuts | Every threshold in one place. The measured ones come from `spec/measure_cuts.py` over the 48 SPX sessions on disk and are held to `spec/cuts.json` by a test; the declared ones are splits and ratios whose meaning is their own words, kept at their declared values, with where each falls on SPX and SNDK history recorded beside them. |
| `spx_jev/sessions.py` | The Session Calendar | The open, the close (13:00 on a half day), holidays and trading days, from the station's own market-hours gate. |
| `spx_jev/expiry.py` | The Expiries | SPX expires every trading day: today's 0DTE and its settle, the next expiry, the monthly (third Friday, the AM-settled SPX and the PM-settled SPXW) and the quarterly and quarter-end expiries. Point in time. |
| `spx_jev/ask.py` | The Packer | Loads a lane's share of the question doc with its constants filled and its schedules checked (a group over 8 questions stops the load), cuts the state into one slice per question group, and keeps only the questions asked this read: live and shadow (dark never), due on their schedule, awake (a question with `sleep_when` only when its label family says so) and with every label present. It can POST a request to JEV. |
| `spx_jev/build.py` | The Command | `python -m spx_jev.build` for one moment, a replay of a day, or a live send. |
| `spx_jev/schedule.py` | The Schedule | Which read a moment stands for, and which questions a lane asks at it, from each question's machine schedule (every N minutes in a window, at named reads, a day constant asked once and held, held from the other lane until an hour, FOMC days from the press conference). The free-text cadence is never parsed. |
| `spx_jev/cadence.py` | The Cadence | What a question the schedule does not ask holds (a day constant all day, a borrowed answer until its hour, an hourly one while young), what the live lane's learned cadence thins out on top, the ask that got no answer asked again at the next read, and the daily recount. |
| `spx_jev/hour.py` | The Sums | Rewrites the live answers as sentences and asks over them, in two requests sent at once, the call's question on the average price and the end-price sums kept beside it. |
| `spx_jev/clock.py` | The Clock | How often price ended up, down or flat at this time of day over the last 20 SPX sessions, scored the way the grader scores a sum, and the half-and-half blend of JEV's sum with those odds; for the call, the same on the average price over its window. |
| `spx_jev/scores.py` | The Scores | One way to score a three-way forecast: floored at 2% a side, and its log loss split exactly into a move part (did it move?) and a direction part (which way, given a move). |
| `spx_jev/baseline.py`, `spec/fit_baseline.py`, `spec/baseline.json` | The Baseline | The price-only forecast the learning loop measures JEV against: the time-of-day odds, and the same odds split by how far SPX has moved today, counted once on the 41 qualifying sessions and frozen, with the leave-one-day-out validation that picked the reference (the time-of-day odds; the movement split lost out of sample). |
| `spx_jev/grade.py` | The Grader | Reads the bars at each end-price sum's mark and scores it, grades the call and every graded window again on the average price over it (`integral.py`), and hands the grades to the question weights and both learning loops. The premarket lane's sums are graded from the settled open (the close of the 09:34 bar, known at 09:35) to the 09:44 and 10:04 bars, in the pre-open ruler the read stamped, never from yesterday's close; no other lane grades a read stamped before the open. |
| `spx_jev/weights.py` | The Weights | One interface, `QuestionWeights`, for how much each question counts in the sums. Every live question weighs 1.0: the neutral method on the tape lane, the learning loop on the live lane. |
| `spx_jev/pool.py` | The Learning Loop | 06-learning-loop-design: at each live read, the forecasts it will score (the fixed mixes of JEV's sum with the price-only reference, today's blend, that frozen reference alone (`baseline`), the question block "no change" competes in, the pool); once a session is sealed, one day's evidence moves the move and the direction weights apart, the tables and calibration decay, and day-level e-processes decide the "earning" labels (e-BH). This is the end-price loop; the average-price loop (`integral_loop.py`, 6b) learns beside it on the average-price grade. Neither pool reaches the phone: since 2026-10-06 the phone shows Pool 2's combined forecast (`mirai_prediction/live_call.py`), and this loop's pool (`pool_v1`) is one of its voice sources only. The promotion machinery that once decided whether the pool replaced the blend (`POOL_ON_PHONE`, `SIM_GATES_PASSED`, the phone e-processes) was retired on 2026-10-07. |
| `spx_jev/archive.py` | The Archive | The raw record for machine learning: every read, grade and close-out of every lane, append only, one typed record per line. |
| `spx_jev/store.py` | The Learning Store | Each market day's raw files (the archive, the sum records, every lane's `jev_average_price_grades_by_read_and_horizon.jsonl`, the bars and the overnight store) as typed Parquet under `data/spx/jev/tables/` (the bars' and the futures' tables under `data/market/tables/`), every row checked and a refused one kept with its reason under `data/operations/health/`. Rebuilt nightly; the raw files are only read. |
| `spx_jev/service.py` | The Service | One run per read: build, ask (when a key exists), sum, grade, write the record, the archive and the phone's card. |
| `spx_jev/lane.py` | The Lanes | The settings one run takes. `LIVE` reads at :02 and :32, its call the average price over the next 30 minutes (`average_30`) with the 30- and 60-minute end-price sums beside it, and its 16:02 fire (13:02 on a half day) is a close-out that grades the day's last calls; `TAPE` is the opening lane: every 5 minutes 09:35 to 10:30, each read stamped at the newest finished bar and sized in tape units, its call the average price over the next 10 minutes (`average_10`) with one five-way 10-minute end-price sum priced in index points beside it, every question its schedule asks asked afresh (its day constants at 09:35, then held), no blend, the exact bar at the mark, and a close-out at 10:42 that asks JEV nothing and grades the morning's last calls. Its records, grades and weights are under `data/spx/jev/opening_lane/`, its card at `data/screen_files/phone/jev_opening_card.json`. `PREMARKET` reads at the six checkpoints before the open, its call the average price over the first 30 minutes (`open_average_30`) with the end-price sums `open_10` and `open_30` beside it, all graded from the settled open, unblended, with no learning loop and neutral weights, under `data/spx/jev/premarket_lane/` (its card at `data/screen_files/phone/jev_premarket_card.json`). Every lane archives into the one shared `data/spx/jev/jev_reads_and_grades_by_event/`, each record naming its lane. |
| `spx_jev/premarket.py` | The Premarket Lane | Six checkpoints before the open (02:35; 03:35, or 04:35 in the week Frankfurt is on winter time and New York is not; 08:05, 08:48, 09:05, 09:28 ET). Each saves the night's futures and builds a scene without a diary row: SPX's prior close carried by /ES on one contract, sized in the pre-open ruler (the median morning anchor of the last sessions, stamped on every record). Every label is rebuilt from the bars at every read; JEV's earlier answers are never fed back. JEV is asked only where a question is due (08:48, 09:28). It writes its records, sums, the archive and the before-the-open card, refuses a fire 5 minutes or more late or from the open on, and closes out at 10:06, once the bar its 10:04 check ends on is on file (up to 68 s, `service.BAR_WAIT_S`), grading from the settled open. |
| `spx_jev/story.py`, `spx_jev/labels/story.py` | The Story So Far | The night's stretches fixed by each exchange's own clock (after the close, Asia, Europe's open and morning, before the report, the report window to 08:45, the last stretch), and the `premarket.*` labels told from them: where futures stand against their 16:00 price, the legs, the arc, the leg since the previous checkpoint, the report window against the night, and the night against yesterday's last hour, each ranked against the same window on the last 20 nights. |
| `spx_jev/labels/premarket.py` | The Overnight Labels | `overnight.es_move`, `.range_vs_normal`, `.gap_origin`, `.release_reaction` (asked on every report day, claims-only days too) and `.bond_gap` (/ZN only), each ranked against the last 20 nights and refused across a roll or on a night that spans a market holiday or follows a half day. Before the open only. |
| `spx_jev/labels/bitcoin.py` | The Bitcoin Family | Micro bitcoin futures (/MBT) against the index. Before the open, its night beyond its usual multiple of /ES, and after a weekend or holiday its weekend and reopen legs; in the session, its half hour, three-half-hour streak, link since 09:35 and last five sessions. Each is ranked against the last nights or sessions, never across a roll, and refused while a roll is pending. |
| `spx_jev/labels/read_sequence.py` | The Read Sequence | The 30-minute lane's last reads rebuilt from the bars at each scheduled read minute (`seq.*`): the day's move from the settled open and the rising-stock volume share beside it, ranked against the same reads on the prior sessions. |
| `spx_jev/bars.py` | The Bars Feed | Appends today's finished SPX minute bars to `data/market/spx_price_bars_by_minute/{day}.jsonl` every minute, from the station's Schwab client. Past sessions come from `data/market/spx_price_bars_by_minute_scanner_copy/{day}-SPX.json`, saved after each close. |
| `spx_jev/headlines.py` | The Headline Feed | Four public RSS feeds (CNBC Top News, Google News search, MarketWatch Top Stories, the Fed's press feed) polled every 3 minutes, 07:00-16:30 ET weekdays, each new item written once to `data/market/news_headlines_by_article/{day}.jsonl` stamped with the poll's own clock (`captured_at`, the truth; the feed's `pubDate` kept as `pub_claimed`, never trusted). `headlines_before(state_dir, row_ts, minutes)` is the questions' reader: what was captured in the window ending 2 minutes before the read. Stdlib only. Phase 3, 2026-10-07. |
| `spx_jev/judgment.py` | The Judgment Group | The four JEV judgment questions of the merged set still asked (two more went dark on 2026-10-07), each asked only when its gate fires on the code feature builder's answers for the read (answered in `run_once` before the requests are built), the headline feed or the ten-year yield's move since the prior close ranked at the same minute. A gate not fired skips its question with `gate: <code question> not fired` and sends nothing. Its labels, `judgment.*`, are the code answers each ask names, in words (the heavyweight in play named with its side and pull, the bond gap's size and side), and the headlines, each story once from its first capture. Shadow in the set, so the old loop never sums, weights, holds or pools them; the learning store files their answers and the Mirai Prediction System's matrix takes them as `jev:<id>` columns. |
| `spx_jev/market_context.py` | The Market Feed | A snapshot a minute of the market around SPX (NYSE breadth, the VIX family, the ES, ten-year note (/ZN, from 2026-10-06) and micro bitcoin (/MBT) futures, the ten-year yield, the 11 sector funds, SMH, RSP, QQQ, IWM, SPY, the 3x funds SPXL, TQQQ, SPXS and SQQQ, the nine megacaps (TSLA and BRK/B from 2026-10-06), and TLT, HYG, USO and GLD) to `data/market/watchlist_quotes_by_minute/{day}.jsonl`, and a backfill of past sessions' minute bars, since Schwab keeps only about 34 sessions (`--backfill --symbols` puts a symbol added later into the saved sessions). The labeller reads a futures quote under its root (Schwab answers `/ES` as `/ESZ26`) and the ten-year yield in percent (Schwab quotes `$TNX` at ten times the yield). A minute Schwab serves no `$VOLD` for takes it from `$UVOL` and `$DVOL` (their difference times 1000, marked `derived`) while those are in thousands of shares, and a breadth symbol answered with no bars is logged and counted under the snapshot's `failed`. Today's own NYSE breadth is never read: Schwab serves a session's breadth wrong until the session is over (2026-09-28 and 09-29: `$ADD`, `$VOLD` and `$VOLSPD` missing, `$UVOL` and `$DVOL` thousands of times their size, `$TICK` floored at zero), so `$ADD` comes live from `$ADVN` and `$DECN` (`derived_add`), the day's breadth is saved again once Schwab puts it right (`refresh_breadth`), and a series that does not read like its own history is taken out before any label reads it, each label that needs it saying why (`labels/plausible.py`, `SAME_DAY_WRONG`). The saved sessions before 2026-10-10 also hold `$IRX`, the 13-week bill yield, and the KRE and XHB funds, quoted until then and never read. |
| `spx_jev/save_day.py` | The Day Saver | After the close, every market-feed symbol's full 1-minute day to `data/market/watchlist_price_bars_by_minute/{day}.jsonl`, and SPX's own day to `data/market/spx_price_bars_by_minute/{day}.jsonl` when that file is short; market days only, a day on disk never fetched again, a missed night caught up by the next. Then the daily closes (below). |
| `spx_jev/daily_closes.py` | The Daily Closes | Each session's open, high, low, close and volume for $SPX, TLT, $TNX, $VIX and $VIX9D, years back (Schwab serves a symbol's whole daily history in one call), to `data/market/spx_vix_rates_price_bars_by_day/{symbol}.jsonl`, one line per market day as served, the file rewritten whole and atomically by every run of the day saver; `load` gives a day the sessions before it only. |
| `spx_jev/index_weights.py` | The Index Weights | `data/market/spx_largest_stock_weights_by_update.json`: dated sets of the largest stocks' shares of the S&P 500 for the megacap labels. A read takes the newest set dated on or before its day; a new set is a new entry under its own day, never written over an old one (`--add`); the first entry (2026-10-06) is approximate and hand-kept. |
| `spx_jev/overnight.py` | The Overnight Store | At 09:26 and 16:20 ET on market days, every /ES, /ZN, /BTC and /MBT bar, 1- and 5-minute, from five minutes before the prior close to the read, to `data/market/futures_price_bars_by_minute/{day}.jsonl`, one file per night named for the day it leads into: merged, never written twice, a bad bar kept with its flags, and a manifest line of checks (bars against the market's hours, gaps, flags, duplicates, contracts) in `data/market/futures_price_bar_change_counts_by_save.jsonl` for each save that changed a night; a save that added nothing writes none. `--backfill` takes every night Schwab still serves (1-minute from mid-August, 5-minute from March). Bitcoin is saved twice: /MBT, which trades nearly every minute, is the one labels read; /BTC is the same price, thinly traded. |
| `spx_jev/rolls.py` | The Roll Table | Schwab's futures history is one series stitched across contracts, so a roll looks like a move (on 09-14 it turned a big-down open into "up"). Each roll is found in the saved data as a step in the futures' basis against a cash market that does not roll ($SPX, IBIT, the ten-year yield) inside the product's roll window (the /ES expiry Friday left out), named back from the quoted contract, and kept for good in `data/market/futures_contracts_and_past_rolls_latest.json`, the current contract counted from the rolls; `same_contract` says whether two moments can be compared. |
| `spx_jev/night_ranks.py` | The Night Ranks | A night's measure against the same measure at the same minute on the last 20 nights, in thirds, leaving out roll, holiday and short nights; under 10 usable nights it is omitted with the reason. `night_move` and `ranked_move` measure and rank the move from the prior close on one contract; `window_move` and `window_range` with `prior_window_nights` and `prior_window_ranges` rank a stretch's move or high-low range against the same stretch on the last nights; `quoted_contract` reads the manifest for the roll guard. |
| `spx_jev/schwab.py` | The Schwab Link | The feeds' calls through the station's shared client (REST only, never the lob-flow streamer), batched and spaced: 1- and 5-minute bars (regular hours unless a caller asks for extended hours), quotes, and the contract a futures root is quoted under. |
| `spec/question_set.json`, `spec/write_question_docs.py`, `questions/spx_questions.json` | The Questions | The final question set (135 questions, 142 labels, 68 constants, with its conventions and the review behind each question) and the step-2 doc every lane asks from, written from it by `python3 spec/write_question_docs.py` (never edited by hand; `--check` says whether it is current, and a test holds it). No number is typed into them: every threshold is a name in braces filled from `cuts.py`, which must hold the set's constants to the number before the writer writes. |
| `questions/spx_hour.json`, `questions/spx_lane_hour.json`, `questions/spx_premarket_hour.json` | The Sums | Each lane's call, where the average price over its window sits (up, flat or down, no unsure), and the end-price sums kept beside it, still graded and still what the question weights learn from: the live lane's two, the opening lane's five-way 10-minute sum, and the premarket lane's two from the settled open. |
| `calendar/events.json`, `spx_jev/events.py` | The Calendar | The scheduled events, kept by hand from `covers_from` through `covers_through`. Tier 1 (the Fed, rebalance closes, half days, copied from SNDK's calendar less SanDisk's own) tags the reads; the other tiers (the 08:30, 10:00 and 14:00 releases and the Fed's scheduled speakers) feed the event labels, and all but the 08:30 releases also keep reads out of the learning loop. The releases before the open (08:30, and ADP at 08:15) are listed from `pre_open_covers_from`, 2026-08-01, and set the premarket lane's report window; the minor ones (`events.MINOR_PRE_OPEN`) are left out of the session's event labels. |
| `spec/labels.json` | The Label Spec | The 50 labels built before the final set, with source, logic, cut and a real sentence; a test pins it to the code. The set's own labels are specified in `spec/question_set.json`. |
| `spec/cuts.json`, `spec/measure_cuts.py` | The Measurements | How each measured cut was found, with its percentile and sample size, and where each declared cut falls (the share of SPX and of SNDK observations under it). |
| `spx_jev/reading_checks.py` | The Reading Checks | JEV's archived picks against the option a question's own labels name, where they name exactly one: a pick that differs is a misreading worth rewording the question or its label for, never a grade. Read only; no job runs it. |
| `spec/replay_premarket.py` | The Premarket Replay | Replays the premarket lane over the saved nights, read only, never asking JEV: each question's answer is its label's verdict, scored against SPX from the settled open. A sign-free question is scored along its own reference, and "holds" is Holm-adjusted across every question and read tested. |
| `launchd/*.plist.template`, `scheduled_jobs/launchd_job_definitions/com.mirai-station.spx-jev*.plist`, `scheduled_jobs/launchd_job_definitions/com.mirai-station.spx-premarket-deadman.plist`, `scheduled_jobs/job_start_scripts/run-spx-jev*.sh`, `scheduled_jobs/job_start_scripts/run-spx-premarket-deadman.sh` | The Jobs | Eleven jobs: ten templates, the copies of them `install-launchd.sh` loads, and their runners; and the premarket lane's dead-man, station code (`alerts_and_phone_pushes/watch/intraday/spx_premarket_deadman.py`) with its plist in `scheduled_jobs/launchd_job_definitions/` only. |
| `tests/` | The Proof | Offline pytest with synthetic rows, bars and market context. No network, no host state. |

## Run it

    cd ~/mirai_awakening/pipelines/spx_jev_questions_and_pools
    python3 -m spx_jev.build                                  # latest row of the latest day
    python3 -m spx_jev.build --day 2026-09-25 --at 12:45
    python3 -m spx_jev.build --day 2026-09-25 --at 09:50 --lane tape
    python3 -m spx_jev.build --day 2026-09-25 --every 15 --out /tmp/states.jsonl

    python3 -m spx_jev.service                                # one run on the newest row, writes the live lane's stores, no send
    python3 -m spx_jev.service --day 2026-09-25               # replay a past day's newest row, into a scratch folder
    python3 -m spx_jev.service --day 2026-09-25 --out-dir /tmp/trial   # the same, into a folder you name
    python3 -m spx_jev.service --send                         # the key comes from .env
    python3 -m spx_jev.service --send --lane tape             # the opening lane

    python3 -m spx_jev.bars                                   # today's finished bars
    python3 -m spx_jev.market_context                         # one market snapshot
    python3 -m spx_jev.market_context --backfill 2026-08-10   # every past session's minute bars since then
    python3 -m spx_jev.save_day                               # after the close: today's full minute bars, any missed day, the daily closes
    python3 -m spx_jev.daily_closes                           # the daily closes alone, every symbol's history to the last closed session
    python3 -m spx_jev.index_weights --show                   # the index weights on file; --add YYYY-MM-DD SYMBOL=SHARE ... adds a dated set
    python3 -m spx_jev.overnight                              # the overnight futures: tonight's and the last week's nights
    python3 -m spx_jev.overnight --day 2026-09-25             # one night by hand, named for the day it leads into
    python3 -m spx_jev.overnight --backfill                   # every night Schwab still serves, then the roll table

    python3 -m spx_jev.premarket --send                       # the premarket lane at the checkpoint just passed
    python3 -m spx_jev.premarket --day 2026-09-25 --at 09:28  # replay a checkpoint, into a scratch folder
    python3 -m spx_jev.premarket --day 2026-09-25 --at 10:06 --out-dir /tmp/trial   # grade that replay
    python3 -m spx_jev.build --day 2026-09-25 --at 09:28 --lane premarket
    python3 -m spx_jev.grade --lane premarket                 # grade the reads before the open
    python3 -m spx_jev.grade --integral-backfill              # the average-price grade of past graded calls, from their bars
    python3 -m spx_jev.grade --integral-report                # per box, the flat share on the average price against the end price
    python3 -m spx_jev.grade --integral-loop-dry-run          # what the loop would learn from the average-price grade; writes nothing live
    python3 spec/replay_premarket.py --out /tmp/replay --workers 8   # the premarket questions over the saved nights, read only
    python3 -m spx_jev.reading_checks --day 2026-09-29        # JEV's picks against the answers their labels name, read only

    python3 -m spx_jev.store                                  # the learning store: the last week's market days, today once saved
    python3 -m spx_jev.store --day 2026-09-28                 # rebuild one day
    python3 -m spx_jev.store --backfill                       # every market day with a raw file on disk

A replay (`--day`) never writes into the station's records unless `--out-dir`
names them: without one it writes its records, card, grades and archive into a
fresh scratch folder and logs where. A run given its own `--out-dir` keeps its
archive there too, under `archive/`.

## The rules it lives by

- **Omit, never null.** A missing label means "not measured"; the packer skips
  every question that needs it. A label left out because what it describes did
  not happen (no shock in the last hour, no new session high or low, no heavy
  strike touched, not a stress day, the 0DTE book's last hour) is ended
  instead: the questions reading it are asleep, with that reason on the card,
  the record and the archive, and "missing" is kept for a real gap in the data.
  A gate that could not measure what decides it (no snapshot on file, too few
  sessions to rank against, a stopped feed, a roll) never sleeps its question:
  the question is missing its label (or, where its label is still on the row,
  its gate's reason starts "could not measure"), so the gap shows on the card
  and in the health reviews instead of passing for "nothing happened".
- **Point in time.** `now` is the row's own timestamp. Only bars that finished
  before it count, prior sessions are only days before the day being built,
  and a market-context value counts only once it was known (a bar once its
  minute finished, a quote once its snapshot was taken).
- **No prices in labels.** Distances are in sigma, shares are percentages,
  strikes are described and never named. The opening lane's stretch labels
  are in index points, because their unit is points.
- **Every cut from SPX.** Each measured cut sits at the same percentile of
  the same measurement on SPX history as SNDK's cut sits on SNDK's history, so
  it keeps SNDK's meaning without SNDK's scale; SPX's intraday moves are small
  against its sigma, so most land near half of SNDK's. The sums' base rates
  are counted on SPX. `python3 spec/measure_cuts.py` re-measures, read only.
  These cuts word the facts only the phone shows; nothing JEV is asked sizes a
  market measure on one (the owner's rule, 2026-09-27).
- **The flat zone, sized at every read (`flat_zone.py`).** How many index
  points either way count as flat for a box is not a share of the morning
  sigma anchor (that ruler mixed clocks, came from one unchecked 09:30
  reading and made 31% of opening half hours flat against 71% at 15:00): it
  is sized at each read from the day's own tape and the VIX alone, never the
  options reading. A box's zone is a multiplier times its random-walk spread
  (shrunk for an average-price box) times today's typical 1-minute move,
  which blends the VIX's view with the minutes finished so far, each divided
  by the clock shape at its minute, over the clock shape of the coming window;
  the multiplier is refit each day from the prior 20 sessions so the box
  lands flat at its target (the end-price sums' base rates, the call's 52.5%),
  never below a floor of 2 points (1.25 on the opening lane). The read is told
  the number, stamps it on its sum record, and is graded against exactly it;
  a read from before the rule is sized again from what was on file at it.
- **Thresholds in the words, from one number.** The question docs name their
  thresholds in braces ("{same_clock_min_sessions}"); a name the code does not define
  stops the load, and a test fails on any number typed into a doc.
- **Every options label says which book.** SPX has an expiry every trading
  day. The weight, wall shares, heaviest strike, gamma ladder and option
  volume are today's 0DTE book; the nearest walls are the scanner's operative
  walls (0DTE first, else the 1-to-7-day book); the delta split is the
  0-to-7-day books; the dated weight is the monthly and quarter-end books
  pulled each morning. The expiry labels say when today's book settles,
  whether it is in its last hour (when its gamma decays fastest), whether
  today is a monthly or quarterly expiry, which dated book holds the most
  weight, and whether today's book and the week's read the same regime.
  The options tape is today's 0DTE SPXW trades.
- **Two borrowed reads, each only while it is sound.** The options tape
  (the lob-flow collector's tilt of the last 15 minutes of 0DTE trades, signed
  by where each printed inside the quote) counts only when its newest line is
  under 3 minutes old; the SPY volume at a wall touch (the siege box's read on
  the row) only while its feed is OK and its baseline robust, and only for a
  touch the box itself judged. The speedometer's pace re-measures the bars the
  labeller already reads, and no source carries 0DTE premium in dollars, so
  neither is labelled.
- **The last hour of the 0DTE book.** Its at-the-money implied volatility
  moved a median 2.1 vol points in 30 minutes against 0.8 earlier in the day,
  the expiry clock rather than the market, so the IV trend is not described
  then.
- **Safety check.** A sent read on today's newest row, when that row is more
  than 6 minutes old, is skipped (the SPX diary is not being written; the last
  card stays). The SPX row has no separate options-book time (the book is rebuilt
  on every scan), so that one check covers the book too. A replay checks
  nothing against the wall clock.
- **Scheduled events.** A read with a tier-1 event due within the hour carries
  it in its record and the card; JEV never sees it. One due within 30 minutes
  is graded but never handed to the question weights. The learning loop
  keeps out more (`events.learn_exclude`, written on the sum record): a
  tier-1, 10:00, 14:00 or Fed-speaker row inside the window, and the close of
  a monthly expiry or of the month's last session.
- **Times on the card are full timestamps.** Every time field carries its
  date and offset (`2026-09-25T16:00:00-04:00`), never a bare clock, so the
  phone can show it in the viewer's own zone. Prose meant for a reader names
  the market clock and says ET.

## The pipeline

1. Labels, code: one module per family (`spx_jev/labels/`) from the row, the bars,
   the market context and the options tape, and the sleep gates of the questions
   whose "nothing happened" default must never reach the weights.
2. The questions, JEV, in parallel: a probability per option. A question its
   schedule does not ask this read keeps its held answer where it has one; one
   whose label is missing, or that is asleep, is skipped. A live question whose
   ask got no answer (JEV failed after its retries) is asked again at the lane's
   next read that can ask it, a day constant or a held one included.
   **The cut-over (Phase 4, `cuts.CUT_OVER_DAY` = 2026-10-08):** from that day
   the live lane asks none of its 104 pre-merge questions. Each carries
   `retired_from: {thirty_minute: 2026-10-08}` in the set and the read reads it
   as dark, reason "retired at the cut-over" (`ask.retired`, applied to the doc
   on every read and close-out by the read's day): never asked, counted, held,
   weighed or pooled, listed apart on the card. The live lane asks only the four
   gated judgment questions (two more went dark on 2026-10-07). The questions stay in the set and the doc, dated,
   never deleted; their answers before the day stay on record. The opening and
   pre-market lanes are not cut over (no code feature builder answers their
   reads, so their sums would have nothing to ride on): the questions they
   share with the live lane stay live there. Before the day nothing changes.
3. Answers as sentences, code (`hour.py`): "Over the last 30 minutes, did price
   rise, fall, or go nowhere? rising, JEV was 98% sure", with "held since 11:02
   ET" on a held one. Shadow answers never go in; a question under the weight
   cut would be left out, and with neutral weights none is. From the cut-over,
   on the live lane (`hour.cut_over_sentences`): the judgment questions'
   answers given afresh on the read, in the same words, then every code-feature
   answer the read has as "<catalog title>: <answer>" ("Last 30-minute move,
   signed: big up", keyed `code:<id>`), so JEV sums the market state the code
   measured; the request is shaped as before, its units line saying what the
   lines are, and the sums are asked even over no sentence at all. The hour
   record and the card's `hour` keep `used` as the answered questions summed
   and, from the cut-over, `code_sentences`, how many of the code's
   measurements the sum rode on beside them. The old
   loop's `pool_v1` keeps forming its snapshot as a voice source with no
   members.
4. The sums, JEV (`questions/spx_hour.json`), two requests sent at once on
   the same sentences. The call: where the average price over the next 30
   minutes sits against the price now, every minute counting equally, up,
   down or flat within an edge JEV is given in points (the call's own flat
   zone, `flat_zone.py`, rounded as told, and graded against that same
   edge), with the read's price and the window's real length (28 minutes on
   the 15:32 read); no unsure. A reply that cannot be read is the call's
   error and never costs the read. Beside it, the end-price sums, asked
   exactly as before: where price is in 30 minutes and in 60, up, down, flat
   or unsure, each flat within its own zone in points for the read (filled
   into its text and named in the context), sized so that on the prior
   sessions it lands flat at its base rate (55% and 59% of reads on SPX).
4b. The blend, code (`clock.py`): each end-price sum mixed half and half with
   how often the same horizon ended up, down or flat at this time of day over
   the last 20 SPX sessions, and the call with how often the average over its
   window did, counted on the average price alone (`historical_odds_average_price_days_state.json`),
   each once 10 sessions are on disk; never on a half day.
5. The card: `data/screen_files/phone/jev_live_card.json`.
6. Grading, code (`grade.py`), every sent run: each sum at its own mark, a hit
   and a Brier score for the blend, JEV's own sum and the clock's odds on the
   same outcome. The grades go to the question weights' `learn`: on the live
   lane the learning loop (`pool.py`) applies every newly sealed session.
   Beside it (`integral.py`), each graded window is graded again on the
   average price over it against the call's zone, or the box's narrowed to fit an average,
   its direction deciding: the call's pick where the average-price sum
   answered, with the Brier and log loss of its odds, else the end-price
   sum's, an unsure one passed. That grade is the phone's verdict, and the
   second loop (6b) learns from it.
6b. The switch, on (`integral_loop=True` on `LIVE` in `lane.py`): a second
   learning loop learns from the average-price grade (`integral_loop.py`):
   the call's odds, the time-of-day odds on the average price as its
   reference, its own state and log, stale and event reads left out, and
   constants of its own, so neither loop can ever load the other's state.
   `jev_question_weights_state.json` reports it under `pool_integral`, beside the end-price
   loop's report; the end-price loop keeps learning beside it, byte for
   byte as with the switch off. Neither loop's pool decides the call: the
   call carries its exact blend, `shown_source` on `hour.average` saying so
   (`pool.SHOWN_BLEND`), and since 2026-10-06 Pool 2's combined forecast
   replaces it after the read (`mirai_prediction/live_call.py`); the
   promotion that once decided the call was retired on 2026-10-07. A
   session is learnt at the first grading run after its reads' average-price
   grades are on file. The gate, 10 SPX sessions of average-price grades, is
   for trusting what it learns, not for learning:
   `python3 -m spx_jev.grade --integral-loop-dry-run` builds the loop from
   nothing on the graded history in a scratch folder and prints what it
   learned (sessions and reads, what it left out and why, the weights, each
   question's standing, how far the gate is) without writing anything live.
   Set the switch to `False` and the grader learns the end-price loop alone,
   as before; this loop's files are left as they are, and it picks up at its
   own watermark when switched back on.

## The files it writes

The live lane's own files, under `data/spx/jev/live_lane/` (the opening lane
keeps the same files under `opening_lane/`, the pre-market lane under
`premarket_lane/`):

- `jev_answers_by_read/{day}.jsonl`, one record per run: the state, the requests, the answers, the
  sum. `jev_and_pool_forecasts_by_read/{day}.jsonl`, one sum record per run with a request: what step 6
  grades. The phone's card is `data/screen_files/phone/jev_live_card.json` (below).
- `jev_last_answers_state.json`, `jev_question_ask_schedule_state.json`, `historical_odds_end_price_days_state.json`,
  `historical_odds_average_price_days_state.json`: the cadence and the clock's stored counts, on
  the end price and on the average price, never mixed. `jev_end_price_grades_by_read.jsonl`,
  `jev_question_weights_state.json` (the sums' tallies, `method` and the per-question weights,
  the end-price loop's `pool` and the average-price loop's `pool_integral`).
  `jev_average_price_grades_by_read_and_horizon.jsonl`, the average-price grade, one line
  per graded horizon (append only, keyed by the read, the horizon and
  `rule_version`), in every lane's folder: from `rule_version` 2 each line
  names the sum it graded (`sum`) and, for the call, carries its `scores`
  and the edge JEV was told (`edge_told`), from 3 the call is set against
  that edge; older lines still read, and a read graded under more than one
  version stands on its newest.

The learning loops' state, under `data/spx/prediction/base_pool/`:

- `base_pool_state/next_30_minutes.json`, `next_60_minutes.json`: the end-price loop's state per horizon.
- `base_pool_state/average_of_next_30_minutes.json`, `base_pool_updates_by_day_and_horizon/average_of_next_30_minutes.jsonl`:
  the same loop learnt from the average-price grade, written while its switch is on (6b, on).
  A state file's rollback copy goes to `data/archive/base_pool_state_rollback_copies/`.

The archive every lane shares:

- `data/spx/jev/jev_reads_and_grades_by_event/{day}.jsonl`: the raw archive for later machine learning, one line
  per record, append only, `schema_version` 5. A `read` record holds the read
  id (lane and row timestamp), the labels and the omitted ones with reasons,
  the exact requests and JEV's exact replies, the sums request and reply, the
  average-price sum's (`average_request`, `average_response`, from version 5),
  the end-price sums as shown and the call under `average`, the cadence state (held, not due, asked, and asked again
  after an ask that got no answer), the market-context values the read could
  see with when each was known, the event tag, on the live lane the learning
  loop's forecasts, and on the opening lane the unit
  and bands, and on the premarket lane the checkpoint and the night it saw. A `grade` record is each graded horizon
  keyed to its read's id; each lane writes its own `close_out` record, its
  calls and tally at its close-out (a lane-day can hold several: a close-out
  that left a call to grade is run again by the live lane's later reads, and
  writes another when its tally moved, so the newest per lane and day stands), graded on the average price from version 4 (`right` is right
  on the average, `passed` was `unsure`, `end_price_only` counts the calls that
  stood on the end price alone, `closed` the calls closed for good, never
  graded); `archive.read_close_out` gives a version 3
  line in that shape. No secret is ever written.

The feeds, under `data/market/`:

- `spx_price_bars_by_minute/{day}.jsonl` (the bars feed) and `watchlist_quotes_by_minute/{day}.jsonl`,
  `watchlist_price_bars_by_minute/{day}.jsonl` (the market feed, and its full days from the
  backfill and the day saver).
- `spx_vix_rates_price_bars_by_day/{symbol}.jsonl`, each symbol's sessions years back, one line per
  market day (`day`, `ts`, the prices and volume, `fetched_at`), rewritten whole by
  the day saver; and `spx_largest_stock_weights_by_update.json`, the dated index weights.
- `futures_price_bars_by_minute/{day}.jsonl`, the overnight futures, one bar per line (`schema_version`,
  `day`, `ts`, `symbol`, `contract`, `contract_from`, `bar_minutes`, the prices and volume,
  `session`, `source`, `saved_at`, `flags`); `futures_price_bar_change_counts_by_save.jsonl`, one line per save
  that changed a night, with its checks; `futures_contracts_and_past_rolls_latest.json`, the roll table;
  `data/operations/locks/futures_price_bars_save.lock`, held while a save merges and writes.
- `news_headlines_by_article/{day}.jsonl`, the headline feed, with `news_headlines_seen_state.json` beside it.

The other lanes and the tables:

- `data/spx/jev/opening_lane/`: the opening lane's own records, grades and weights; its card is
  `data/screen_files/phone/jev_opening_card.json`.
- `data/spx/jev/premarket_lane/`: `jev_answers_by_read/{day}.jsonl`, one read per checkpoint;
  `jev_and_pool_forecasts_by_read/{day}.jsonl`, its sums; `jev_end_price_grades_by_read.jsonl` (each line with `from.settled_open`
  and `anchor.source` `"pre_open"`), `jev_question_weights_state.json`; its card, the before-the-open card, is
  `data/screen_files/phone/jev_premarket_card.json`.
- `data/spx/jev/tables/` and `data/market/tables/`, the learning store built from the archive, the sum records, every lane's
  `jev_average_price_grades_by_read_and_horizon.jsonl`, the bars and the overnight store (below).

The card carries: `symbol`, `generated_at`, `row_ts`, `freshness`, `sigma`,
`situation` (four facts with a verdict word and the figure to draw),
`labels_count`, `omitted`, `sent`, `asked` (the questions the read put to JEV),
`model`, `questions` (each with `answer` or `skipped`, a plain reason whose
raw text stays in the archive, and `held_from` when held), `fresh_count` and
`held_count`, `dark` (each with its `dark_reason`) with `dark_note`, `shadow_note`,
`hour` (the blended end-price sums with `jev`, `clock` and `blend`, kept
beside the call, and the call under `average`: its pick, odds, window and edge
in points, blended the same way on the average price, and `shown_source`, its
exact blend, or Pool 2's mix once it takes the call over after the read), `event`, `calls` and `tally` (the day's newest
calls, each the average-price sum's pick and odds where it answered, `sum`
naming the one, and where it was asked and got no readable answer the
end-price sum's, marked `average_missing` and graded on its end price; a read
whose end-price sums got no answer and whose average-price sum answered is a
call in its own right, marked `end_price_missing` and graded on the average
price over its window; and
their grades: `integral`, the grade on the average price
over the window from `jev_average_price_grades_by_read_and_horizon.jsonl`, with its label, points against
the edge, verdict, size, path and, once the box has ten of its last 20
sessions on file holding at least ten right calls to rank it against
(`TIER_MIN_RIGHT_CALLS`), its strength `tier`; and `end_price`, the grade at the mark, with the
end-price sum's own `pick` and `p`, kept beside it for the side-by-side weeks;
a call with no average-price grade is marked `end_price_only` and stands on
its end price until a later card finds the line, and an open call carries its
average so far, `so_far`. The tally
counts the average-price grade: a call's direction decides it, an unsure call is
`passed`, never among the calls right or wrong, and `end_price_only` says how
many stood on the end price), `marks`, `session` (`close` and `last_read`), `expiries` (today's
settle, the next expiry's, the next monthly's, and what expires today), and on
the opening lane `lane`, `ruler`, `band`, `stretch`, `schedule`, `graded_at`
(each close-out run's, which the phone redraws on) and, once every call is
graded or closed for good, `closed_out_at`.

The before-the-open card (`data/screen_files/phone/jev_premarket_card.json`) carries the day, the
read's checkpoint, `freshness` (how old /ES was at the read and what the spot
stood on), the pre-open `ruler`, the open, the settled open (`start`), the
`marks` and the `handover` at 09:35, the day's `schedule` of reads and JEV
reads, `hour` (the newest call, with `read_at`), `hour_error` when this read's
own sum failed, `story` (one chip per read, the report read marked), the
`situation` facts, the questions, and the day's calls. The SPX phone page
(`screens/desktop_and_phone_server/static/m/jev-spx.html`) leads with it from the day's first
pre-market read until the 09:35 hand-over, every time in the viewer's own zone.

## The learning store

`spx_jev/store.py` turns each market day's raw files into typed tables for
machine learning. The raw files stay the record: the store only reads them, and
any day can be rebuilt from them whole.

    data/spx/jev/tables/<table>/day=YYYY-MM-DD/part-0.parquet   one file per table per built day, zero rows included
                                                                 (`spx_bars` and `overnight_bars` under data/market/tables/,
                                                                 `quarantine` and `validation` under data/operations/health/;
                                                                 `quarantine` has a file only for a day a row was refused on)

| Table | One row per | What it holds |
|---|---|---|
| `reads` (`jev_read_summaries_by_read/`) | read, any lane | `read_id` (lane and row time), `lane`, `row_ts`, `archived_at`, `minute_et` and `minutes_from_open`, `checkpoint`, `sent`, `model`, `spot`, `sigma`, the ruler (`ruler_source`, `ruler_points`, `ruler_unit_sigma`, `ruler_sessions`, `ruler_omitted`) and the opening lane's bands, the event tag, how many questions were `answered`, `lost`, `unsent`, `held`, `not_due`, `asleep`, `missing`, `dark`, `unread` or `other`, how many were `reasked`, the labels written, omitted and asleep, the end-price sums' `sum_used`, `sum_left_out`, `sum_missing` and `sum_error`, the average-price sum's `average_error` (why the read has no call on the average price), and `skip_reasons` (question to why) |
| `answers` (`jev_answers_by_read_and_question/`) | question per read | `status` as counted on the read and its `reason`, `type`, `pick`, `confidence`, `probabilities` (option to probability; a yes/no as `true` and `false`), `score` and `noul` as JEV sent them, `question_hash` (the question exactly as JEV was sent it), `pool_version` (the learning loop's version of it), `held_from` and `held_found` for a held answer (its values are copied from the read that asked it; a lost ask whose last answer the sum read instead is `held`, its `reason` starting `lost`), `reasked` (empty for a read archived before the lane recorded its re-asks), `reasked_from`, `reask_why`. The average-price sum's own question is a row too (`group_id` `average`, from archive version 5), left out of the read's question counts |
| `calls` (`jev_and_pool_forecasts_by_read_and_horizon/`) | sum per read | `sum_id` (`next_30`, `average_30` and the like), `horizon` (the box whose window it forecasts), `is_call` (the call the phone showed: the average-price sum's where it answered, else the end-price primary's), `minutes` (the call's window as it was told, 28 on the 15:32 read), `mark`, `is_primary` (the end-price box the weights and pools learn from), the time of day, the sum's final odds (`shown_source`, `shown_pick`, `shown_probs`: the blend, or Pool 2's mix since 2026-10-06), the call's `price`, `flat_points` and `edge_points`, JEV alone (`jev_pick`, `jev_probs`), the time-of-day odds (`clock_probs`, `clock_n`), the blend (`blended`, `blend_jev_share`, `blend_phase`), the end-price loop's mix (`pool_probs`, `pool_p_move`, `pool_p_up_given_move`, `pool_reference_version`, or why it was left out in `pool_left_out`), the opening lane's `direction_*` and `size_*` views, and `learn_exclude` |
| `average_grades` (`jev_average_price_grades_by_read_and_horizon/`) | window graded on the average price, per lane | `horizon`, `sum_id` (the sum whose call it graded: the average-price sum's, else the end-price sum's), `rule_version` (a read graded under more than one stands on its newest; the older counts as `superseded`), `graded` and why not (`reason`, `minutes_missing`), `mark`, `from_price`, `flat_points`, `edge`, `edge_told`, `average_move` (the average against the read, in points), `outcome` and the end price's `end_outcome`, `pick`, `verdict` (`right`, `wrong` or `passed`), `abstained`, `correct` (empty when passed), `margin`, a passed call's `lean`, the `running` labels, the best, worst and sharpest minute, `minutes_filled`, `bad_ticks`, `stale_read`, the opening lane's size (`size_outcome`, the end-price five-way pick's `size_pick` and `size_right`), and the scores (`brier`, `log_loss`, and JEV's and the clock's when blended) |
| `spx_bars` (`market/tables/spx_price_bars_by_minute/`) | SPX minute | open, high, low, close, volume, and `source` (the saved session file kept over the bars feed's) |
| `overnight_bars` (`market/tables/futures_price_bars_by_minute/`) | futures bar of the night leading into `day` | `symbol`, `contract`, `contract_from`, `bar_minutes`, the prices and volume, `session`, `saved_at`, `flags`, `overnight_schema` (the overnight file's own version), and `prior_session` for the prior session's last minutes a night starts with, which the prior day's night also holds: `WHERE NOT prior_session` gives each bar once |
| `quarantine` (`operations/health/table_build_refused_rows_by_event/`) | refused row | `table_name` (`raw_line` for a line that is not a JSON object, or a raw record in the wrong shape), `key`, `reason`, `source` (file and line), `row_json` |
| `validation` (`operations/health/table_build_counts_by_table_and_day/`) | table per day | `table_name`, `rows_in`, `kept`, `duplicates`, `superseded`, `quarantined`, `reasons` (reason to count), `sources`, `store_schema`, `built_at` |

Every table has `day`, from its folder. Times are instants shown in New York
time. The column names and types are fixed by `store.TABLES` at schema version
`store.SCHEMA_VERSION` (4), written into every file's metadata. Seven more
tables were kept until 2026-10-11, when nothing read them: the end-price
`grades`, both loops' `pool_log`, the market feed's `context_bars` and
`context_quotes`, the futures `rolls`, the `daily_closes` and the calendar's
`events`; their old day files stay on disk until they are archived.

Each row is typed column by column, then held to its table's checks: ranges,
probabilities that sum to one, a bar's high above its low, and point in time
(nothing archived, held or marked before its read, no bar saved before it
finished). An answer, call or grade must belong to one of the day's kept reads.
A refused row goes to `quarantine` with its reason, never
dropped. Copies of a row that agree but for where they came from count as
`duplicates`; where a table ranks its sources the better copy is kept and a
disagreeing one from a worse source counts as `superseded`; any other
disagreement, between two equally good sources included, is quarantined.

The nightly job (`com.mirai-station.spx-jev-store`, 16:40 ET) rebuilds the last
week's market days.
`python3 -m spx_jev.store --backfill` builds every market day with a raw file on
disk, and names the days it leaves out for not being market days (Labor Day's
futures, say).

From Python, in the station's venv, each table is read over its folder (a
DuckDB views file, `spx_jev.duckdb`, was written beside the tables until
2026-10-11; nothing opened it):

    from pathlib import Path
    import duckdb
    TABLES = Path.home() / "mirai_awakening/data/spx/jev/tables"
    def table(folder):
        return f"read_parquet('{TABLES}/{folder}/day=*/*.parquet', hive_partitioning = true, hive_types = {{'day': DATE}}, union_by_name = true)"
    duckdb.sql("SET TimeZone = 'America/New_York'")              # show times in market time
    duckdb.sql(f"SELECT day, lane, count(*) AS reads FROM {table('jev_read_summaries_by_read')} GROUP BY ALL ORDER BY ALL").show()
    duckdb.sql(f"""SELECT question_id, avg(confidence) AS sure, count(*) AS n
                   FROM {table('jev_answers_by_read_and_question')} WHERE status = 'answered' GROUP BY question_id ORDER BY n DESC""").show()
    duckdb.sql(f"""SELECT c.row_ts, c.sum_id, c.shown_pick, c.shown_probs[c.shown_pick] AS p, a.outcome, a.verdict
                   FROM {table('jev_and_pool_forecasts_by_read_and_horizon')} c
                   JOIN {table('jev_average_price_grades_by_read_and_horizon')} a ON a.read_id = c.read_id AND a.sum_id = c.sum_id
                   WHERE c.is_call AND c.lane = 'live'""").show()           # the phone's calls and their grades
    duckdb.sql(f"SELECT day, table_name, quarantined, reasons FROM {table('validation')} WHERE quarantined > 0").show()

`validation` is written for every built day; `quarantine` has a day folder only
when a row was refused, so read it (`table('quarantine')`, the refused rows with
their `reason` and `source`) only once the query above names a day, or
`read_parquet` stops on the empty glob.

`.show()`, `.df()` and `.arrow()` read any column. `.fetchall()` and
`.fetchone()` hand back a timestamp as a Python datetime, which DuckDB builds
with pytz: `venv-bootstrap.sh` installs it, and a venv without it raises
"Required module 'pytz' failed to import" on any time column.

Into pandas: `duckdb.sql(f"SELECT * FROM {table('answers')}").df()` (a map column such as
`probabilities` arrives as a dict per row), or one table's folder with pandas
alone, the day read as a date:

    import pandas as pd, pyarrow as pa, pyarrow.dataset as ds
    bars = pd.read_parquet(STORE / "spx_bars",
                           partitioning=ds.partitioning(pa.schema([("day", pa.date32())]), flavor="hive"))

## The jobs

Eleven launchd jobs, loaded by `scheduled_jobs/job_start_scripts/install-launchd.sh` from
`scheduled_jobs/launchd_job_definitions/`. Each of the ten `spx-jev*` plists there is its template in
`launchd/` byte for byte, and a test holds them equal, so a change is made to
both. The dead-man is the station's own code and has no template.

| Label | When (the box's Pacific clock; market time is three hours later) | Runner |
|---|---|---|
| `com.mirai-station.spx-jev` | 06:32 to 13:02, at :02 and :32; the fire after the close only grades | `run-spx-jev.sh` |
| `com.mirai-station.spx-jev-tape` | 06:35 to 07:30 every 5 minutes, and 07:42 | `run-spx-jev.sh --lane tape` |
| `com.mirai-station.spx-jev-bars` | every 60 s, gated to market hours plus 13 minutes after the close | `run-spx-jev-bars.sh` |
| `com.mirai-station.spx-jev-context` | every 60 s, gated to market hours | `run-spx-jev-context.sh` |
| `com.mirai-station.spx-jev-save-day` | 13:20, once a day after the close | `run-spx-jev-save-day.sh` |
| `com.mirai-station.spx-jev-overnight` | 06:26 and 13:20: before the open, and after the close | `run-spx-jev-overnight.sh` |
| `com.mirai-station.spx-jev-premarket` | Sunday to Thursday 23:35; Monday to Friday 00:35, 01:35, 05:05, 05:48, 06:05, 06:28 and 07:06 | `run-spx-jev-premarket.sh` |
| `com.mirai-station.spx-jev-store` | 13:40, once a day after the saves | `run-spx-jev-store.sh` |
| `com.mirai-station.spx-jev-prediction-nightly` | 14:05, once a day after the store's rebuild (17:05 ET), weekdays only: the Mirai Prediction System learns the day and refits for the next (`spx_jev/mirai_prediction/README.md`); `SPX_JEV_PREDICTION_DISABLE=1` stops it alone | `run-spx-jev-prediction-nightly.sh` |
| `com.mirai-station.spx-jev-headlines` | every 180 s; the poll keeps itself to 07:00-16:30 ET weekdays (04:00-13:30 Pacific) and polls four public RSS feeds, no Schwab (`spx_jev/headlines.py`, Phase 3) | `run-spx-jev-headlines.sh` |
| `com.mirai-station.spx-premarket-deadman` | every 5 minutes; on a market day it pages the phone once for a premarket checkpoint with no read 15 minutes after it, and for a 10:06 ET close-out that has not landed, or left a call ungraded, by 10:21 ET | `run-spx-premarket-deadman.sh` |

Each runner exits quietly on weekends and when the market is closed, fails
loudly when the market-hours check itself cannot run, and stops at
`SPX_JEV_DISABLE=1`. The day saver runs after the close by design, so it has
no market-hours gate: the command saves market days only, and only a session
that has closed. The learning store runs after the saves by design and has no
gate either: it builds market days only, and today only once the saves have run. The overnight store runs before the open by design and has
no gate either: its command exits on a day the market is shut. Nor has the
premarket lane: its command reads only on a market day, within 5 minutes after
a checkpoint and before the open, so a holiday, a late fire after the Mac slept,
or the 01:35 fire (04:35 ET) outside Frankfurt's winter-time week exits having written nothing. The key lives only in `pipelines/spx_jev_questions_and_pools/.env`, which is
git-ignored, so a merge never brings it. Without it the service runs unsent
and the card says "not sent: no key on this machine".

## Going live

SPX JEV has been live on the mini since 2026-09-28: the code is on `main`, the
key is in place, the market feed's past sessions are backfilled from
2026-08-11, and the eleven jobs are loaded. To set it up on another machine, at
that machine, in order:

1. The key: in `~/mirai_awakening/pipelines/spx_jev_questions_and_pools`, run
   `cp .env.example .env && chmod 600 .env` and fill in `TYPESAFE_API_KEY`
   (the key in `skills/sndk-jev/.env`).
2. The past sessions' market bars, from the same folder:
   `~/.local/share/mirai-station/venv/bin/python -m spx_jev.market_context --backfill 2026-08-10`.
3. Hire the jobs: `~/mirai_awakening/scheduled_jobs/job_start_scripts/install-launchd.sh`
   (it reloads every station job, the eleven here among them).
4. Check: `launchctl list | grep -E 'spx-jev|spx-premarket'` shows eleven, and after the first fire
   `grep -iE 'failed|traceback' data/operations/logs/spx_jev*_errors_log.txt data/operations/logs/spx_premarket_deadman_errors_log.txt`
   finds nothing. The errors logs are never empty: the service writes its
   normal progress lines to stderr, so a failure is found by its words, in
   either case: the runners' gate says `FAILED`, the jobs' own lines say
   `failed` (the average-price grade, a loop, a day's store build) and often
   carry no traceback. A `spx-jev-bars :: fetch failed` line means the Schwab
   bars request itself failed, and its error says why.

To pause all eleven, `launchctl disable gui/$UID/<label>` and then `launchctl bootout gui/$UID/<label>`
for each label above: bootout alone stops a job only until the next login,
since the plists stay in `~/Library/LaunchAgents/` and the mini logs in by
itself after a restart. `launchctl enable gui/$UID/<label>` and then
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/<label>.plist` bring one
back (docs/how_to_run/OPERATIONS.md, "Disabling temporarily", has both loops).
`SPX_JEV_DISABLE=1` in the jobs' environment makes every runner exit 0 without
running.

## Tests

    cd ~/mirai_awakening/pipelines/spx_jev_questions_and_pools && python3 -m pytest tests -q

## Not done yet

- Every question whose data no feed carries yet, or whose label is not built,
  is dark and says what it waits for (its `dark_reason`): among them the
  /6E future for the macro lean. Since 2026-10-06 the index weights (`data/market/spx_largest_stock_weights_by_update.json`,
  approximate and hand-kept: replace them with SSGA's SPY holdings as a new
  dated entry), the daily closes (`data/market/spx_vix_rates_price_bars_by_day/`) and the /ZN
  future are on file, for the Mirai Prediction System's questions first
  (`spx_jev/mirai_prediction/README.md`, "Feeds").
- The premarket lane keeps no learning loop and its weights are neutral: the
  settled-open odds forecast its window worse than even thirds, so its sums
  stand unblended.
- The premarket lane keeps no last-asked answers, and all but two of its
  questions are due only at 09:28, its last checkpoint. A group that got no
  answer is asked once more within the same read while the second round and
  the sums still fit before the open; a read too close to the open for both
  asks once, and no later checkpoint asks its questions again.
- The cut-over retires the live lane's questions only; the opening and
  pre-market lanes still ask theirs, with no code sentences in their sums.
- Nothing works out the set's `code_answer`s yet, so re-asking a question when
  the code's answer changes (a schedule's `then`) is built only on the cadence
  side: such a question is held.
- Neither learning loop's pool reaches the phone: the promotion machinery
  (`pool.POOL_ON_PHONE`, `pool.SIM_GATES_PASSED`, the phone e-processes) was
  retired on 2026-10-07, since Pool 2 has taken the call since 2026-10-06 and
  pool_v1 is one of its voices. 06's simulation acceptance gates were never
  built. Nor is 06's direction test (Primary B: the edge score, with day-level
  e-processes for JEV against the reference and the pool against the exact
  blend), or the per-forecast losses, Brier and edge score 06 adds to
  `jev_end_price_grades_by_read.jsonl`: whether JEV helps call SPX's direction has no test yet.
