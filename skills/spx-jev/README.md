# spx-jev — the JEV decision service beside the SPX diary

A **read-only sidecar** beside the left-eye scanner. Twice an hour on market
days it reads the SPX diary rows the scanner already writes, today's minute
bars and the market around the index, turns the numbers into plain-English
labels, asks JEV the questions, sums the answers into a 30-minute and a
60-minute forecast, grades the earlier forecasts against the bars, and writes
the phone's card. A second lane reads every 5 minutes through the opening, and
a third reads the night's futures at six checkpoints before the open. It
writes only under `state/spx_jev/`, never into the scanner's files, and never
runs on the scan path.

The mechanics are SNDK JEV's (`skills/sndk-jev/`), copied and adapted; the
questions are the final SPX set (`spec/question_set.json`, 129 questions). Every
label they read is built except the dark ones, whose data no feed carries yet; a
question whose label is dark, or missing on a read, is skipped with the reason.
Its nine jobs are in `runtime/launchd/` and go live with the checklist in
"Going live".

JEV reads words and cannot compare numbers. So every comparison happens here,
in code, and is written out as a sentence with what it was judged against in it. A size is never judged
on a fixed line: it is ranked against the same measure at the same minute on up to the last 20 sessions,
needing 10 of them, and said by its third:

    price.recent_move = "over the last 30 minutes price rose 0.13 sigma, larger than 16 of the last 19 sessions
                         at this minute, top third: a strong move; the half hour closed between the top and
                         bottom fifths of its own range"

Sigma is the day's expected move for the S&P 500 index, so every distance is
a share of a normal day. A question points at that label by name, and JEV
returns a probability for each answer option. JEV makes no trading call.

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
| `spx_jev/hour.py` | The Sums | Rewrites the live answers as sentences and asks the sum questions over them in one request. |
| `spx_jev/clock.py` | The Clock | How often price ended up, down or flat at this time of day over the last 20 SPX sessions, scored the way the grader scores a sum, and the half-and-half blend of JEV's sum with those odds. |
| `spx_jev/scores.py` | The Scores | One way to score a three-way forecast: floored at 2% a side, and its log loss split exactly into a move part (did it move?) and a direction part (which way, given a move). |
| `spx_jev/baseline.py`, `spec/fit_baseline.py`, `spec/baseline.json` | The Baseline | The price-only forecast the learning loop measures JEV against: the time-of-day odds, and the same odds split by how far SPX has moved today, counted once on the 41 qualifying sessions and frozen, with the leave-one-day-out validation that picked the reference (the time-of-day odds; the movement split lost out of sample). |
| `spx_jev/grade.py` | The Grader | Reads the bars at each sum's mark, scores both sums, and hands the grades to the question weights. The premarket lane's sums are graded from the settled open (the close of the 09:34 bar, known at 09:35) to the 09:44 and 10:04 bars, in the pre-open ruler the read stamped, never from yesterday's close; no other lane grades a read stamped before the open. |
| `spx_jev/weights.py` | The Weights | One interface, `QuestionWeights`, for how much each question counts in the sums. Every live question weighs 1.0: the neutral method on the tape lane, the learning loop on the live lane. |
| `spx_jev/pool.py` | The Learning Loop | 06-learning-loop-design: at each live read, the forecasts it will score (the fixed mixes of JEV's sum with the price-only reference, today's blend, the clock, the question block "no change" competes in, the pool); once a session is sealed, one day's evidence moves the move and the direction weights apart, the tables and calibration decay, and day-level e-processes decide the "earning" labels (e-BH) and whether the pool replaces the blend on the phone. The phone switch, `POOL_ON_PHONE`, is on: the phone shows the pool only while it is promoted, and nothing is promoted until the simulation gates pass (`SIM_GATES_PASSED`). |
| `spx_jev/archive.py` | The Archive | The raw record for machine learning: every read, grade and close-out of every lane, append only, one typed record per line. |
| `spx_jev/store.py` | The Learning Store | Each market day's raw files (the archive, the bars, the market feed, the overnight store, the roll table, the calendar) as typed Parquet under `state/spx_jev/store/`, every row checked and a refused one quarantined with its reason, with DuckDB views over it. Rebuilt nightly; the raw files are only read. |
| `spx_jev/service.py` | The Service | One run per read: build, ask (when a key exists), sum, grade, write the record, the archive and the phone's card. |
| `spx_jev/lane.py` | The Lanes | The settings one run takes. `LIVE` reads at :02 and :32 with the 30- and 60-minute sums, and its 16:02 fire (13:02 on a half day) is a close-out that grades the day's last calls; `TAPE` is the opening lane: every 5 minutes 09:35 to 10:30, each read stamped at the newest finished bar and sized in tape units, one five-way 10-minute sum priced in index points, every question its schedule asks asked afresh (its day constants at 09:35, then held), no blend, the exact bar at the mark, and a close-out at 10:42 that asks JEV nothing and grades the morning's last calls. Its records, card, grades and weights are under `state/spx_jev/lanes/tape/`. `PREMARKET` reads at the six checkpoints before the open, its two sums (`open_10`, `open_30`) graded from the settled open, unblended, with no learning loop and neutral weights, under `state/spx_jev/lanes/premarket/`. Every lane archives into the one shared `state/spx_jev/archive/`, each record naming its lane. |
| `spx_jev/premarket.py` | The Premarket Lane | Six checkpoints before the open (02:35; 03:35, or 04:35 in the week Frankfurt is on winter time and New York is not; 08:05, 08:48, 09:05, 09:28 ET). Each saves the night's futures and builds a scene without a diary row: SPX's prior close carried by /ES on one contract, sized in the pre-open ruler (the median morning anchor of the last sessions, stamped on every record). Every label is rebuilt from the bars at every read; JEV's earlier answers are never fed back. JEV is asked only where a question is due (08:48, 09:28). It writes its records, sums, the archive and the before-the-open card, refuses a fire 5 minutes or more late or from the open on, and closes out at 10:06, once the bar its 10:04 check ends on is on file (up to 55 s), grading from the settled open. |
| `spx_jev/story.py`, `spx_jev/labels/story.py` | The Story So Far | The night's stretches fixed by each exchange's own clock (after the close, Asia, Europe's open and morning, before the report, the report window to 08:45, the last stretch), and the `premarket.*` labels told from them: where futures stand against their 16:00 price, the legs, the arc, the leg since the previous checkpoint, the report window against the night, and the night against yesterday's last hour, each ranked against the same window on the last 20 nights. |
| `spx_jev/labels/premarket.py` | The Overnight Labels | `overnight.es_move`, `.range_vs_normal`, `.gap_origin`, `.release_reaction` (asked on every report day, claims-only days too) and `.bond_gap` (/ZN only), each ranked against the last 20 nights and refused across a roll or on a night that spans a market holiday or follows a half day. Before the open only. |
| `spx_jev/labels/bitcoin.py` | The Bitcoin Family | Micro bitcoin futures (/MBT) against the index. Before the open, its night beyond its usual multiple of /ES, and after a weekend or holiday its weekend and reopen legs; in the session, its half hour, three-half-hour streak, link since 09:35 and last five sessions. Each is ranked against the last nights or sessions, never across a roll, and refused while a roll is pending. |
| `spx_jev/labels/read_sequence.py` | The Read Sequence | The 30-minute lane's last reads rebuilt from the bars at each scheduled read minute (`seq.*`): the day's move from the settled open and the rising-stock volume share beside it, ranked against the same reads on the prior sessions. |
| `spx_jev/bars.py` | The Bars Feed | Appends today's finished SPX minute bars to `state/spx_jev/bars/{day}.jsonl` every minute, from the station's Schwab client. Past sessions come from `state/reversion/bars/{day}-SPX.json`, saved after each close. |
| `spx_jev/market_context.py` | The Market Feed | A snapshot a minute of the market around SPX (NYSE breadth, the VIX family, the ES future and micro bitcoin futures (/MBT), rates, the 11 sector funds, SMH, RSP, QQQ, IWM, SPY, the seven megacaps, and TLT, HYG, USO and GLD) to `state/spx_jev/context/{day}.jsonl`, and a backfill of past sessions' minute bars, since Schwab keeps only about 34 sessions. The labeller reads a futures quote under its root (Schwab answers `/ES` as `/ESZ26`) and the Treasury yields in percent (Schwab quotes `$TNX` at ten times the yield). A minute Schwab serves no `$VOLD` for takes it from `$UVOL` and `$DVOL` (their difference times 1000, marked `derived`) while those are in thousands of shares, and a breadth symbol answered with no bars is logged and counted under the snapshot's `failed`. |
| `spx_jev/save_day.py` | The Day Saver | After the close, every market-feed symbol's full 1-minute day to `state/spx_jev/context/bars/{day}.jsonl`, and SPX's own day to `state/spx_jev/bars/{day}.jsonl` when that file is short; market days only, a day on disk never fetched again, a missed night caught up by the next. |
| `spx_jev/overnight.py` | The Overnight Store | At 09:26 and 16:20 ET on market days, every /ES, /ZN, /BTC and /MBT bar, 1- and 5-minute, from five minutes before the prior close to the read, to `state/spx_jev/overnight/{day}.jsonl`, one file per night named for the day it leads into: merged, never written twice, a bad bar kept with its flags, and each save's checks (bars against the market's hours, gaps, flags, duplicates, contracts) in `manifest.jsonl`. `--backfill` takes every night Schwab still serves (1-minute from mid-August, 5-minute from March). Bitcoin is saved twice: /MBT, which trades nearly every minute, is the one labels read; /BTC is the same price, thinly traded. |
| `spx_jev/rolls.py` | The Roll Table | Schwab's futures history is one series stitched across contracts, so a roll looks like a move (on 09-14 it turned a big-down open into "up"). Each roll is found in the saved data as a step in the futures' basis against a cash market that does not roll ($SPX, IBIT, the ten-year yield) inside the product's roll window (the /ES expiry Friday left out), named back from the quoted contract, and kept for good in `state/spx_jev/overnight/rolls.json`, the current contract counted from the rolls; `same_contract` says whether two moments can be compared. |
| `spx_jev/night_ranks.py` | The Night Ranks | A night's measure against the same measure at the same minute on the last 20 nights, in thirds, leaving out roll, holiday and short nights; under 10 usable nights it is omitted with the reason. `night_move` and `ranked_move` measure and rank the move from the prior close on one contract; `window_move` and `window_range` with `prior_window_nights` and `prior_window_ranges` rank a stretch's move or high-low range against the same stretch on the last nights; `quoted_contract` reads the manifest for the roll guard. |
| `spx_jev/schwab.py` | The Schwab Link | The feeds' calls through the station's shared client (REST only, never the lob-flow streamer), batched and spaced: 1- and 5-minute bars (regular hours unless a caller asks for extended hours), quotes, and the contract a futures root is quoted under. |
| `spec/question_set.json`, `spec/write_question_docs.py`, `questions/spx_questions.json` | The Questions | The final question set (129 questions, 136 labels, 67 constants, with its conventions and the review behind each question) and the step-2 doc every lane asks from, written from it by `python3 spec/write_question_docs.py` (never edited by hand; `--check` says whether it is current, and a test holds it). No number is typed into them: every threshold is a name in braces filled from `cuts.py`, which must hold the set's constants to the number before the writer writes. |
| `questions/spx_hour.json`, `questions/spx_lane_hour.json`, `questions/spx_premarket_hour.json` | The Sums | Each lane's call, where the average price over its window sits (up, flat or down, no unsure), and beside it in shadow the end-price sums as before: the live lane's two, the opening lane's five-way 10-minute sum, and the premarket lane's two from the settled open. |
| `calendar/events.json`, `spx_jev/events.py` | The Calendar | The scheduled events, kept by hand from `covers_from` through `covers_through`. Tier 1 (the Fed, rebalance closes, half days, copied from SNDK's calendar less SanDisk's own) tags the reads; the other tiers (the 08:30, 10:00 and 14:00 releases and the Fed's scheduled speakers) feed the event labels, and all but the 08:30 releases also keep reads out of the learning loop. The releases before the open (08:30, and ADP at 08:15) are listed from `pre_open_covers_from`, 2026-08-01, and set the premarket lane's report window; the minor ones (`events.MINOR_PRE_OPEN`) are left out of the session's event labels. |
| `spec/labels.json` | The Label Spec | The 50 labels built before the final set, with source, logic, cut and a real sentence; a test pins it to the code. The set's own labels are specified in `spec/question_set.json`. |
| `spec/cuts.json`, `spec/measure_cuts.py` | The Measurements | How each measured cut was found, with its percentile and sample size, and where each declared cut falls (the share of SPX and of SNDK observations under it). |
| `spec/replay_premarket.py` | The Premarket Replay | Replays the premarket lane over the saved nights, read only, never asking JEV: each question's answer is its label's verdict, scored against SPX from the settled open. A sign-free question is scored along its own reference, and "holds" is Holm-adjusted across every question and read tested. |
| `launchd/*.plist.template`, `runtime/launchd/com.mirai-station.spx-jev*.plist`, `runtime/launchd/com.mirai-station.spx-premarket-deadman.plist`, `runtime/scripts/run-spx-jev*.sh`, `runtime/scripts/run-spx-premarket-deadman.sh` | The Jobs | Nine jobs: eight templates, the copies of them `install-launchd.sh` loads, and their runners; and the premarket lane's dead-man, station code (`runtime/watch/intraday/spx_premarket_deadman.py`) with its plist in `runtime/launchd/` only. |
| `tests/` | The Proof | Offline pytest with synthetic rows, bars and market context. No network, no host state. |

## Run it

    cd ~/.claude/plugins/mirai-station/skills/spx-jev
    python3 -m spx_jev.build                                  # latest row of the latest day
    python3 -m spx_jev.build --day 2026-09-25 --at 12:45
    python3 -m spx_jev.build --day 2026-09-25 --at 09:50 --lane tape
    python3 -m spx_jev.build --day 2026-09-25 --every 15 --out /tmp/states.jsonl

    python3 -m spx_jev.service                                # one run on the newest row, writes state/spx_jev/, no send
    python3 -m spx_jev.service --day 2026-09-25               # replay a past day's newest row, into a scratch folder
    python3 -m spx_jev.service --day 2026-09-25 --out-dir /tmp/trial   # the same, into a folder you name
    python3 -m spx_jev.service --send                         # the key comes from .env
    python3 -m spx_jev.service --send --lane tape             # the opening lane

    python3 -m spx_jev.bars                                   # today's finished bars
    python3 -m spx_jev.market_context                         # one market snapshot
    python3 -m spx_jev.market_context --backfill 2026-08-10   # every past session's minute bars since then
    python3 -m spx_jev.save_day                               # after the close: today's full minute bars, and any missed day
    python3 -m spx_jev.overnight                              # the overnight futures: tonight's and the last week's nights
    python3 -m spx_jev.overnight --day 2026-09-25             # one night by hand, named for the day it leads into
    python3 -m spx_jev.overnight --backfill                   # every night Schwab still serves, then the roll table

    python3 -m spx_jev.premarket --send                       # the premarket lane at the checkpoint just passed
    python3 -m spx_jev.premarket --day 2026-09-25 --at 09:28  # replay a checkpoint, into a scratch folder
    python3 -m spx_jev.premarket --day 2026-09-25 --at 10:06 --out-dir /tmp/trial   # grade that replay
    python3 -m spx_jev.build --day 2026-09-25 --at 09:28 --lane premarket
    python3 -m spx_jev.grade --lane premarket                 # grade the reads before the open
    python3 -m spx_jev.grade --integral-backfill              # the shadow integral grade of past graded calls, from their bars
    python3 -m spx_jev.grade --integral-report                # per box, the flat share on the average price against the end price
    python3 -m spx_jev.grade --integral-loop-dry-run          # what the loop would learn from the average-price grade; writes nothing live
    python3 spec/replay_premarket.py --out /tmp/replay --workers 8   # the premarket questions over the saved nights, read only

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
  against its sigma, so most land near half of SNDK's (the 30-minute flat band
  is 0.07 sigma). The sums' base rates are counted on SPX. `python3
  spec/measure_cuts.py` re-measures, read only. These cuts grade the sums and
  word the facts only the phone shows; nothing JEV is asked sizes a market
  measure on one (the owner's rule, 2026-09-27).
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
3. Answers as sentences, code (`hour.py`): "Over the last 30 minutes, did price
   rise, fall, or go nowhere? rising, JEV was 98% sure", with "held since 11:02
   ET" on a held one. Shadow answers never go in; a question under the weight
   cut would be left out, and with neutral weights none is.
4. The sums, JEV (`questions/spx_hour.json`), two requests sent at once on
   the same sentences. The call: where the average price over the next 30
   minutes sits against the price now, every minute counting equally, up,
   down or flat within an edge JEV is given in points (the 30-minute flat
   band narrowed by `integral.factor`, rounded as told, and graded against
   that same edge), with the read's price and the window's real length (28
   minutes on the 15:32 read); no unsure. A reply that cannot be read is the
   new sum's error and never costs the read. In shadow, asked exactly
   as before: where price is in 30 minutes (flat within 0.07 sigma, 55% of
   reads on SPX) and in 60 (flat within 0.11 sigma, 59%), up, down, flat or
   unsure.
4b. The blend, code (`clock.py`): each end-price sum mixed half and half with
   how often the same horizon ended up, down or flat at this time of day over
   the last 20 SPX sessions, and the call with how often the average over its
   window did, counted on the average price alone (`clock_integral_days.json`),
   each once 10 sessions are on disk; never on a half day.
5. The card: `state/spx_jev/latest.json`.
6. Grading, code (`grade.py`), every sent run: each sum at its own mark, a hit
   and a Brier score for the blend, JEV's own sum and the clock's odds on the
   same outcome. The grades go to the question weights' `learn`: on the live
   lane the learning loop (`pool.py`) applies every newly sealed session.
   Beside it (`integral.py`), each graded window is graded again on the
   average price over it against the flat band narrowed to fit an average,
   its direction deciding: the call's pick where the average-price sum
   answered, with the Brier and log loss of its odds, else the end-price
   sum's, an unsure one passed. That grade is the phone's verdict, and the
   second loop (6b) learns from it.
6b. The switch, on (`integral_loop=True` on `LIVE` in `lane.py`): a second
   learning loop learns from the average-price grade (`integral_loop.py`):
   the call's odds, the time-of-day odds on the average price as its
   reference, its own state and log, stale and event reads left out, and
   constants of its own, so neither loop can ever load the other's state.
   `weights.json` reports it under `pool_integral`, beside the end-price
   loop's report; the end-price loop keeps learning beside it, byte for
   byte as with the switch off, since the phone's pool, its promotion and
   demotion are the end price's: the phone never shows this loop's pool. A
   session is learnt at the first grading run after its reads' average-price
   grades are on file. The gate, 10 SPX sessions of average-price grades, is
   for trusting and promoting what it learns, not for learning:
   `python3 -m spx_jev.grade --integral-loop-dry-run` builds the loop from
   nothing on the graded history in a scratch folder and prints what it
   learned (sessions and reads, what it left out and why, the weights, each
   question's standing, how far the gate is) without writing anything live.
   Set the switch to `False` and the grader learns the end-price loop alone,
   as before; this loop's files are left as they are, and it picks up at its
   own watermark when switched back on.

## The files it writes, all under `state/spx_jev/`

- `{day}.jsonl`, one record per run: the state, the requests, the answers, the
  sum. `hour/{day}.jsonl`, one sum record per run with a request: what step 6
  grades. `latest.json`, the phone's card (below).
- `last_asked.json`, `cadence.json`, `clock_days.json`,
  `clock_integral_days.json`: the cadence and the clock's stored counts, on
  the end price and on the average price, never mixed. `grades.jsonl`,
  `weights.json` (the sums' tallies, `method` and the per-question weights,
  the end-price loop's `pool` and the average-price loop's `pool_integral`),
  `weights_log.jsonl`. `integral_grades.jsonl`, the integral grade, one line
  per graded horizon (append only, keyed by the read, the horizon and
  `rule_version`), in every lane's folder: from `rule_version` 2 each line
  names the sum it graded (`sum`) and, for the call, carries its `scores`
  and the edge JEV was told (`edge_told`), from 3 the call is set against
  that edge; older lines still read, and a read graded under more than one
  version stands on its newest.
- `pool_30.json`, `pool_60.json`, `pool_log.jsonl`: the learning loop's state
  per horizon and one log line per horizon per session applied or refused.
- `pool_30_integral.json`, `pool_integral_log.jsonl`: the same loop learnt
  from the average-price grade, written while its switch is on (6b, on).
- `archive/{day}.jsonl`: the raw archive for later machine learning, one line
  per record, append only, `schema_version` 5. A `read` record holds the read
  id (lane and row timestamp), the labels and the omitted ones with reasons,
  the exact requests and JEV's exact replies, the sums request and reply, the
  average-price sum's (`average_request`, `average_response`, from version 5),
  the sum as shown, the cadence state (held, not due, asked, and asked again
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
- `bars/{day}.jsonl` (the bars feed) and `context/{day}.jsonl`,
  `context/bars/{day}.jsonl` (the market feed, and its full days from the
  backfill and the day saver).
- `lanes/tape/`: the opening lane's own records, card, grades and weights.
- `lanes/premarket/`: `{day}.jsonl`, one read per checkpoint; `hour/{day}.jsonl`, its sums;
  `latest.json`, the before-the-open card; `grades.jsonl` (each line with `from.settled_open`
  and `anchor.source` `"pre_open"`), `weights.json`, `weights_log.jsonl`.
- `overnight/{day}.jsonl`, the overnight futures, one bar per line (`schema_version`,
  `day`, `ts`, `symbol`, `contract`, `contract_from`, `bar_minutes`, the prices and volume,
  `session`, `source`, `saved_at`, `flags`); `overnight/manifest.jsonl`, one line per save
  that changed a night, with its checks; `overnight/rolls.json`, the roll table;
  `overnight/.save.lock`, held while a save merges and writes.
- `store/`, the learning store built from the archive, the sum records, every lane's
  `integral_grades.jsonl`, both learning loops' logs, the bars, the market feed, the
  overnight store and the calendar (below).

The card carries: `symbol`, `generated_at`, `row_ts`, `freshness`, `sigma`,
`situation` (four facts with a verdict word and the figure to draw), `labels`,
`omitted`, `sent`, `asked` (the questions the read put to JEV), `model`,
`questions` (each with `answer` or `skipped`, and `held_from` when held),
`hour` (the blended end-price sum with `jev`, `clock` and `blend`, and the
call under `average`: its pick, odds, window and edge in points, blended the
same way on the average price), `event`, `calls` and `tally` (the day's newest
calls, each the average-price sum's pick and odds where it answered, `sum`
naming the one, and where it was asked and got no readable answer the
end-price sum's, marked `average_missing` and graded on its end price; and
their grades: `integral`, the grade on the average price
over the window from `integral_grades.jsonl`, with its label, points against
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

The before-the-open card (`lanes/premarket/latest.json`) carries the day, the
read's checkpoint, `freshness` (how old /ES was at the read and what the spot
stood on), the pre-open `ruler`, the open, the settled open (`start`), the
`marks` and the `handover` at 09:35, the day's `schedule` of reads and JEV
reads, `hour` (the newest call, with `read_at`), `hour_error` when this read's
own sum failed, `story` (one chip per read, the report read marked), the
`situation` facts, the questions, and the day's calls. The SPX phone page
(`runtime/viewstation/static/m/jev-spx.html`) leads with it from the day's first
pre-market read until the 09:35 hand-over, every time in the viewer's own zone.

## The learning store

`spx_jev/store.py` turns each market day's raw files into typed tables for
machine learning. The raw files stay the record: the store only reads them, and
any day can be rebuilt from them whole.

    state/spx_jev/store/<table>/day=YYYY-MM-DD/part-0.parquet   one file per table per built day, zero rows included
    state/spx_jev/store/spx_jev.duckdb                          a view per table over those files, and `meta`

| Table | One row per | What it holds |
|---|---|---|
| `reads` | read, any lane | `read_id` (lane and row time), `lane`, `row_ts`, `archived_at`, `minute_et` and `minutes_from_open`, `checkpoint`, `sent`, `model`, `spot`, `sigma`, the ruler (`ruler_source`, `ruler_points`, `ruler_unit_sigma`, `ruler_sessions`, `ruler_omitted`) and the opening lane's bands, the event tag, how many questions were `answered`, `lost`, `unsent`, `held`, `not_due`, `asleep`, `missing`, `dark`, `unread` or `other`, how many were `reasked`, the labels written, omitted and asleep, the end-price sums' `sum_used`, `sum_left_out`, `sum_missing` and `sum_error`, the average-price sum's `average_error` (why the read has no call on the average price), and `skip_reasons` (question to why) |
| `facts` | label, or market value, per read | `source` (`label` or `market_context`), `path` (a label archived under a name it has since lost is filed under its new one, `labels/registry.py`'s `RENAMED`), `status` (`written`, `omitted` or `asleep`: omitted on a reason a question slept on), the sentence in `text`, the `reason` a label is missing, a market `value`, and `known_at` |
| `answers` | question per read | `status` as counted on the read and its `reason`, `type`, `pick`, `confidence`, `probabilities` (option to probability; a yes/no as `true` and `false`), `score` and `noul` as JEV sent them, `question_hash` (the question exactly as JEV was sent it), `pool_version` (the learning loop's version of it), `held_from` and `held_found` for a held answer (its values are copied from the read that asked it; a lost ask whose last answer the sum read instead is `held`, its `reason` starting `lost`), `reasked` (empty for a read archived before the lane recorded its re-asks), `reasked_from`, `reask_why`. The average-price sum's own question is a row too (`group_id` `average`, from archive version 5), left out of the read's question counts |
| `calls` | sum per read | `sum_id` (`next_30`, `average_30` and the like), `horizon` (the box whose window it forecasts), `is_call` (the call the phone showed: the average-price sum's where it answered, else the end-price primary's), `minutes` (the call's window as it was told, 28 on the 15:32 read), `mark`, `is_primary` (the end-price box the weights and pools learn from), the time of day, the sum's final odds (`shown_source`, `shown_pick`, `shown_probs`: the blend, or the learning loop's mix once promoted), the call's `price`, `flat_points` and `edge_points`, JEV alone (`jev_pick`, `jev_probs`), the time-of-day odds (`clock_probs`, `clock_n`), the blend (`blended`, `blend_jev_share`, `blend_phase`), the end-price loop's mix (`pool_probs`, `pool_p_move`, `pool_p_up_given_move`, `pool_reference_version`, or why it was left out in `pool_left_out`), the opening lane's `direction_*` and `size_*` views, and `learn_exclude` |
| `grades` | graded end-price sum | `mark`, `outcome`, the realized move, `pick`, `abstained` (an unsure pick), `correct` (empty when abstained), `hit`, `brier`, `p_band`, JEV's and the clock's scores, the opening lane's direction and size scores, and the ruler it was graded in |
| `average_grades` | window graded on the average price, per lane | `horizon`, `sum_id` (the sum whose call it graded: the average-price sum's, else the end-price sum's), `rule_version` (a read graded under more than one stands on its newest; the older counts as `superseded`), `graded` and why not (`reason`, `minutes_missing`), `mark`, `from_price`, `flat_points`, `edge`, `edge_told`, `average_move` (the average against the read, in points), `outcome` and the end price's `end_outcome`, `pick`, `verdict` (`right`, `wrong` or `passed`), `abstained`, `correct` (empty when passed), `margin`, a passed call's `lean`, the `running` labels, the best, worst and sharpest minute, `minutes_filled`, `bad_ticks`, `stale_read`, the opening lane's size, and the scores (`brier`, `log_loss`, and JEV's and the clock's when blended) |
| `pool_log` | learning-loop log line | `loop` (`end_price` from `pool_log.jsonl`, `average_price` from `pool_integral_log.jsonl`), `lane`, `horizon`, `session` (the day it learnt; the day is the line's own when it names none), `logged_at`, `applied` and `why` not, `reads`, `included` and `excluded`, the reference's change (`reference_from`, `reference_to`), `phone` (a promotion or demotion), `pool_loss` and `blend_loss`, and the whole line in `line_json` |
| `spx_bars` | SPX minute | open, high, low, close, volume, and `source` (the saved session file kept over the bars feed's) |
| `context_bars` | symbol and minute | the market feed's bars in the labels' units (a future under its root, `served_as` the contract; the yields in percent), `derived` and `derived_from` for a `$VOLD` built from `$UVOL` and `$DVOL`, `source` (the saved day kept over a live snapshot, Schwab's own `$VOLD` over a derived one), `known_at` and `written_at` |
| `context_quotes` | symbol and snapshot | `last`, `prior_close`, `volume`, `taken_at` |
| `overnight_bars` | futures bar of the night leading into `day` | `symbol`, `contract`, `contract_from`, `bar_minutes`, the prices and volume, `session`, `saved_at`, `flags`, `overnight_schema` (the overnight file's own version), and `prior_session` for the prior session's last minutes a night starts with, which the prior day's night also holds: `WHERE NOT prior_session` gives each bar once |
| `rolls` | roll, on the day it took effect | `symbol`, `from_contract`, `to_contract`, `rolled_at`, the basis step and its reference |
| `events` | calendar row on the day | `starts_at`, `ends_at`, `kind`, `tier`, `in_session`, `verified`, `q_and_a`, `calendar_built` |
| `quarantine` | refused row | `table_name` (`raw_line` for a line that is not a JSON object, or a raw record in the wrong shape), `key`, `reason`, `source` (file and line), `row_json` |
| `validation` | table per day | `table_name`, `rows_in`, `kept`, `duplicates`, `superseded`, `quarantined`, `reasons` (reason to count), `sources`, `store_schema`, `built_at` |

Every table has `day`, from its folder. Times are instants shown in New York
time. The column names and types are fixed by `store.TABLES` at schema version
`store.SCHEMA_VERSION` (3), written into every file's metadata and into `meta`.

Each row is typed column by column, then held to its table's checks: ranges,
probabilities that sum to one, a bar's high above its low, and point in time
(no fact known after its read, no grade written before its mark, no bar saved
before it finished, no session learnt before it closed). A fact, answer, call
or grade must belong to one of the day's kept reads. A refused row goes to `quarantine` with its reason, never
dropped. Copies of a row that agree but for where they came from count as
`duplicates`; where a table ranks its sources the better copy is kept and a
disagreeing one from a worse source counts as `superseded`; any other
disagreement, between two equally good sources included, is quarantined.

The nightly job (`com.mirai-station.spx-jev-store`, 16:40 ET) rebuilds the last
week's market days. It writes the DuckDB file only when its views are missing
or out of date, so a notebook holding it open read only never stops a run; one
holding it open for writing leaves the views to the next run, with a line in
the job's log, while the day's Parquet is still built.
`python3 -m spx_jev.store --backfill` builds every market day with a raw file on
disk, and names the days it leaves out for not being market days (Labor Day's
futures, say).

From Python, in the station's venv:

    from pathlib import Path
    import duckdb
    STORE = Path.home() / ".claude/plugins/mirai-station/state/spx_jev/store"
    con = duckdb.connect(str(STORE / "spx_jev.duckdb"), read_only=True)
    con.sql("SET TimeZone = 'America/New_York'")              # show times in market time
    con.sql("SELECT day, lane, count(*) AS reads FROM reads GROUP BY ALL ORDER BY ALL").show()
    con.sql("""SELECT question_id, avg(confidence) AS sure, count(*) AS n
               FROM answers WHERE status = 'answered' GROUP BY question_id ORDER BY n DESC""").show()
    con.sql("""SELECT c.row_ts, c.sum_id, c.shown_pick, c.shown_probs[c.shown_pick] AS p, a.outcome, a.verdict
               FROM calls c JOIN average_grades a ON a.read_id = c.read_id AND a.sum_id = c.sum_id
               WHERE c.is_call AND c.lane = 'live'""").show()               # the phone's calls and their grades
    con.sql("""SELECT c.row_ts, c.shown_pick, c.jev_probs['up'] AS jev_up, g.outcome, g.correct
               FROM calls c JOIN grades g ON g.read_id = c.read_id AND g.horizon = c.sum_id
               WHERE c.sum_id = 'next_30'""").show()                        # the end-price sum at its mark
    con.sql("SELECT day, table_name, reason, source FROM quarantine").show()

The views name the store's files by their full path, so a copied store is
read over its Parquet directly, or given views of its own with
`store.write_views(<copy>)`. Without the DuckDB file, over the Parquet directly:

    duckdb.sql(f"""SELECT symbol, count(*) FROM read_parquet('{STORE}/context_bars/*/*.parquet', hive_partitioning = true)
                   GROUP BY symbol""").show()

`.show()`, `.df()` and `.arrow()` read any column. `.fetchall()` and
`.fetchone()` hand back a timestamp as a Python datetime, which DuckDB builds
with pytz: `venv-bootstrap.sh` installs it, and a venv without it raises
"Required module 'pytz' failed to import" on any time column.

Into pandas: `con.sql("SELECT * FROM answers").df()` (a map column such as
`probabilities` arrives as a dict per row), or one table's folder with pandas
alone, the day read as a date:

    import pandas as pd, pyarrow as pa, pyarrow.dataset as ds
    bars = pd.read_parquet(STORE / "spx_bars",
                           partitioning=ds.partitioning(pa.schema([("day", pa.date32())]), flavor="hive"))

## The jobs

Nine launchd jobs, loaded by `runtime/scripts/install-launchd.sh` from
`runtime/launchd/`. Each of the eight `spx-jev*` plists there is its template in
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
or the 01:35 fire outside Frankfurt's winter-time week exits having written nothing. The key lives only in `skills/spx-jev/.env`, which is
git-ignored, so a merge never brings it. Without it the service runs unsent
and the card says "not sent: no key on this machine".

## Going live

SPX JEV has been live on the mini since 2026-09-28: the code is on `main`, the
key is in place, the market feed's past sessions are backfilled from
2026-08-11, and the nine jobs are loaded. To set it up on another machine, at
that machine, in order:

1. The key: in `~/.claude/plugins/mirai-station/skills/spx-jev`, run
   `cp .env.example .env && chmod 600 .env` and fill in `TYPESAFE_API_KEY`
   (the key in `skills/sndk-jev/.env`).
2. The past sessions' market bars, from the same folder:
   `~/.local/share/mirai-station/venv/bin/python -m spx_jev.market_context --backfill 2026-08-10`.
3. Hire the jobs: `~/.claude/plugins/mirai-station/runtime/scripts/install-launchd.sh`
   (it reloads every station job, the nine here among them).
4. Check: `launchctl list | grep -E 'spx-jev|spx-premarket'` shows nine, and after the first fire
   `grep -E 'FAILED|Traceback' /tmp/mirai-station.spx-jev*.err /tmp/mirai-station.spx-premarket-deadman.err`
   finds nothing. The `.err` files are never empty: the service writes its
   normal progress lines to stderr, so a failure is found by its words.

To pause all nine, `launchctl disable gui/$UID/<label>` and then `launchctl bootout gui/$UID/<label>`
for each label above: bootout alone stops a job only until the next login,
since the plists stay in `~/Library/LaunchAgents/` and the mini logs in by
itself after a restart. `launchctl enable gui/$UID/<label>` and then
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/<label>.plist` bring one
back (docs/OPERATIONS.md, "Disabling temporarily", has both loops).
`SPX_JEV_DISABLE=1` in the jobs' environment makes every runner exit 0 without
running.

## Tests

    cd ~/.claude/plugins/mirai-station/skills/spx-jev && python3 -m pytest tests -q

## Not done yet

- Every label of the final set is built except the dark ones, which no feed
  carries yet. Some built labels still wait on data nothing saves: the index
  weights (`state/spx_leaders/weights.json`, the four largest-stock labels),
  daily closes for the month- and quarter-end rebalance, and the /6E future
  (the /ZN future is saved in the overnight store, not yet in the market feed).
  The set's `monday_prerequisites` list the rest.
- The premarket lane keeps no learning loop and its weights are neutral: the
  settled-open odds forecast its window worse than even thirds, so its sums
  stand unblended. `night_ranks.es_move` is read by nothing but its tests.
- The premarket lane asks nothing again after an ask that got no answer: it
  keeps no last-asked answers, and all but two of its questions are due only
  at 09:28, its last checkpoint.
- Nothing works out the set's `code_answer`s yet, so re-asking a question when
  the code's answer changes (a schedule's `then`) is built only on the cadence
  side: such a question is held.
- The learning loop's pool takes the phone once promoted (`pool.POOL_ON_PHONE`),
  but its simulation acceptance gates (06) are not built, so nothing is promoted
  (`pool.SIM_GATES_PASSED` is off): the promotion evidence builds up and the
  phone stays on the blend until they pass.
  Nor is 06's direction test (Primary B: the edge score, with day-level
  e-processes for JEV against the reference and the pool against the exact
  blend), or the per-forecast losses, Brier and edge score 06 adds to
  `grades.jsonl`: whether JEV helps call SPX's direction has no test yet.
- **Follow-up: the Schwab breadth check.** On 2026-09-28 Schwab served no
  minute bars for `$ADD`, `$VOLD` or `$VOLSPD` all day, live and in the
  after-close save, and the NYSE series it did serve came back changed:
  `$UVOL` and `$DVOL` at thousands of times any saved session's size, `$TICK`
  at zero most minutes and never below it, `$TRIN` far under its usual range.
  It persisted on a second day, 2026-09-29, with the Schwab login healthy: the
  market feed logged no minute bars for `$ADD`, `$VOLD` and `$VOLSPD` at every
  one of its 333 minute runs and in the after-close save, and `$UVOL` and
  `$DVOL` failed the thousands-of-shares check at every minute, so `$VOLD` was
  never derived. Ask Schwab's history for each of the seven breadth symbols over a
  recent session and set it beside a saved one (`state/spx_jev/context/bars/`)
  before changing anything. Until then the labels that need `$ADD` or
  `$VOLSPD` are omitted with the series named, and `$VOLD` is derived only on a
  day `$UVOL` and `$DVOL` are in thousands of shares. A series that does not
  read like its own history at the read's minute (far off its usual size, or
  stuck on one reading) is taken out before any label reads it, and every label
  that needs it says why (`labels/plausible.py`); a day that fails sits out of
  the later days' ranks. If Schwab keeps serving the changed series, those
  labels stay omitted until it makes up most of the last sessions on file.
