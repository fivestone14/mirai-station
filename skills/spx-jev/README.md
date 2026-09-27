# spx-jev — the JEV decision service beside the SPX diary

A **read-only sidecar** beside the left-eye scanner. Twice an hour on market
days it reads the SPX diary rows the scanner already writes, today's minute
bars and the market around the index, turns the numbers into plain-English
labels, asks JEV the questions, sums the answers into a 30-minute and a
60-minute forecast, grades the earlier forecasts against the bars, and writes
the phone's card. A second lane reads every 5 minutes through the opening. It
writes only under `state/spx_jev/`, never into the scanner's files, and never
runs on the scan path.

The mechanics are SNDK JEV's (`skills/sndk-jev/`), copied and adapted; the
questions are the final SPX set (`spec/question_set.json`, 115 questions), whose
labels are being built family by family: until a label is built, every question
that reads it is skipped with the reason. Nothing here is installed yet (see "The
jobs").

JEV reads words and cannot compare numbers. So every comparison happens here,
in code, and is written out as a sentence with its threshold in it:

    price.recent_move = "over the last 30 minutes price rose 0.40 sigma, more than the 0.09 sigma move rule"

Sigma is the day's expected move for the S&P 500 index, so every distance is
a share of a normal day. A question points at that label by name, and JEV
returns a probability for each answer option. JEV makes no trading call.

## Glossary

| File | Plain name | What it does |
|---|---|---|
| `spx_jev/state_builder.py` | The Scene | Loads one moment, point in time: a diary row with its siege read, today's finished bars, the prior sessions' bars, the market context, the lob-flow collector's signed 0DTE options tape. |
| `spx_jev/labels/` | The Labeller | One module per label family, each owning a fixed list of labels and exposing `build_<family>_labels(scene) -> LabelSet`, registered in `labels/registry.py`; the shared reads beside them: bar windows, the settled open (the 09:34 close) and the session extremes (`measures`), the sigma rulers (the morning anchor with its 09:40 guard, the live sigma, the straddle left, the normal-day sigma) and the tape unit (`rulers`), the same-minute rank against up to 20 prior sessions (`ranks`), and how numbers are written (`words`). Anything a family cannot measure is left out, with the reason kept in `omitted`. |
| `spx_jev/row_adapter.py` | The Row Adapter | Cuts each 40 KB SPX diary row down to the few dozen fields the labeller reads, by name, and says which options book each field describes. |
| `spx_jev/cuts.py` | The Cuts | Every threshold in one place. The measured ones come from `spec/measure_cuts.py` over the 48 SPX sessions on disk and are held to `spec/cuts.json` by a test; the declared ones are splits and ratios whose meaning is their own words, kept at their declared values, with where each falls on SPX and SNDK history recorded beside them. |
| `spx_jev/sessions.py` | The Session Calendar | The open, the close (13:00 on a half day), holidays and trading days, from the station's own market-hours gate. |
| `spx_jev/expiry.py` | The Expiries | SPX expires every trading day: today's 0DTE and its settle, the next expiry, the monthly (third Friday, the AM-settled SPX and the PM-settled SPXW) and the quarterly and quarter-end expiries. Point in time. |
| `spx_jev/ask.py` | The Packer | Loads a lane's share of the question doc with its constants filled and its schedules checked (a group over 8 questions stops the load), cuts the state into one slice per question group, and keeps only the questions asked this read: live and shadow (dark never), due on their schedule, awake (a question with `sleep_when` only when its label family says so) and with every label present. It can POST a request to JEV. |
| `spx_jev/build.py` | The Command | `python -m spx_jev.build` for one moment, a replay of a day, or a live send. |
| `spx_jev/schedule.py` | The Schedule | Which read a moment stands for, and which questions a lane asks at it, from each question's machine schedule (every N minutes in a window, at named reads, a day constant asked once and held, held from the other lane until an hour, FOMC days from the press conference). The free-text cadence is never parsed. |
| `spx_jev/cadence.py` | The Cadence | What a question the schedule does not ask holds (a day constant all day, a borrowed answer until its hour, an hourly one while young), what the live lane's learned cadence thins out on top, and the daily recount. |
| `spx_jev/hour.py` | The Sums | Rewrites the live answers as sentences and asks the sum questions over them in one request. |
| `spx_jev/clock.py` | The Clock | How often price ended up, down or flat at this time of day over the last 20 SPX sessions, scored the way the grader scores a sum, and the half-and-half blend of JEV's sum with those odds. |
| `spx_jev/scores.py` | The Scores | One way to score a three-way forecast: floored at 2% a side, and its log loss split exactly into a move part (did it move?) and a direction part (which way, given a move). |
| `spx_jev/baseline.py`, `spec/fit_baseline.py`, `spec/baseline.json` | The Baseline | The price-only forecast the learning loop measures JEV against: the time-of-day odds, and the same odds split by how far SPX has moved today, counted once on the 41 qualifying sessions and frozen, with the leave-one-day-out validation that picked the reference (the time-of-day odds; the movement split lost out of sample). |
| `spx_jev/grade.py` | The Grader | Reads the bars at each sum's mark, scores both sums, and hands the grades to the question weights. |
| `spx_jev/weights.py` | The Weights | One interface, `QuestionWeights`, for how much each question counts in the sums. Every live question weighs 1.0: the neutral method on the tape lane, the learning loop on the live lane. |
| `spx_jev/pool.py` | The Learning Loop | 06-learning-loop-design: at each live read, the forecasts it will score (the fixed mixes of JEV's sum with the price-only reference, today's blend, the clock, the question block "no change" competes in, the pool); once a session is sealed, one day's evidence moves the move and the direction weights apart, the tables and calibration decay, and day-level e-processes decide the "earning" labels (e-BH) and whether the pool may replace the blend on the phone. The phone switch, `POOL_ON_PHONE`, is off. |
| `spx_jev/archive.py` | The Archive | The raw record for machine learning: every read, grade and close-out of both lanes, append only, one typed record per line. |
| `spx_jev/service.py` | The Service | One run per read: build, ask (when a key exists), sum, grade, write the record, the archive and the phone's card. |
| `spx_jev/lane.py` | The Lanes | The settings one run takes. `LIVE` reads at :02 and :32 with the 30- and 60-minute sums, and its 16:02 fire (13:02 on a half day) is a close-out that grades the day's last calls; `TAPE` is the opening lane: every 5 minutes 09:35 to 10:30, each read stamped at the newest finished bar and sized in tape units, one five-way 10-minute sum priced in index points, every question its schedule asks asked afresh (its day constants at 09:35, then held), no blend, the exact bar at the mark, and a close-out at 10:42 that asks JEV nothing and grades the morning's last calls. It writes only under `state/spx_jev/lanes/tape/`. |
| `spx_jev/bars.py` | The Bars Feed | Appends today's finished SPX minute bars to `state/spx_jev/bars/{day}.jsonl` every minute, from the station's Schwab client. Past sessions come from `state/reversion/bars/{day}-SPX.json`, saved after each close. |
| `spx_jev/market_context.py` | The Market Feed | A snapshot a minute of the market around SPX (NYSE breadth, the VIX family, the ES future, rates, the 11 sector funds, SMH, RSP, QQQ, IWM, SPY, the seven megacaps, and TLT, HYG, USO and GLD) to `state/spx_jev/context/{day}.jsonl`, and a backfill of past sessions' minute bars, since Schwab keeps only about 34 sessions. The labeller reads a futures quote under its root (Schwab answers `/ES` as `/ESZ26`) and the Treasury yields in percent (Schwab quotes `$TNX` at ten times the yield). |
| `spx_jev/save_day.py` | The Day Saver | After the close, every market-feed symbol's full 1-minute day to `state/spx_jev/context/bars/{day}.jsonl`, and SPX's own day to `state/spx_jev/bars/{day}.jsonl` when that file is short; market days only, a day on disk never fetched again, a missed night caught up by the next. |
| `spx_jev/schwab.py` | The Schwab Link | The two feeds' calls through the station's shared client (REST only, never the lob-flow streamer), batched and spaced. |
| `spec/question_set.json`, `spec/write_question_docs.py`, `questions/spx_questions.json` | The Questions | The final question set (115 questions, 121 labels, 166 constants, with its conventions and the review behind each question) and the step-2 doc both lanes ask from, written from it by `python3 spec/write_question_docs.py` (never edited by hand; `--check` says whether it is current, and a test holds it). No number is typed into them: every threshold is a name in braces filled from `cuts.py`, which must hold the set's constants to the number before the writer writes. |
| `questions/spx_hour.json`, `questions/spx_lane_hour.json` | The Sums | The live lane's two sums, and the opening lane's five-way 10-minute sum. |
| `calendar/events.json`, `spx_jev/events.py` | The Calendar | The scheduled events, kept by hand from `covers_from` through `covers_through`. Tier 1 (the Fed, rebalance closes, half days, copied from SNDK's calendar less SanDisk's own) tags the reads; the other tiers (the 08:30, 10:00 and 14:00 releases and the Fed's scheduled speakers) feed the event labels, and all but the 08:30 releases also keep reads out of the learning loop. |
| `spec/labels.json` | The Label Spec | The 50 labels built before the final set, with source, logic, cut and a real sentence; a test pins it to the code. The set's own labels are specified in `spec/question_set.json`. |
| `spec/cuts.json`, `spec/measure_cuts.py` | The Measurements | How each measured cut was found, with its percentile and sample size, and where each declared cut falls (the share of SPX and of SNDK observations under it). |
| `launchd/*.plist.template`, `runtime/scripts/run-spx-jev*.sh` | The Jobs | Five staged jobs and their runners. Not installed. |
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

A replay (`--day`) never writes into the station's records unless `--out-dir`
names them: without one it writes its records, card, grades and archive into a
fresh scratch folder and logs where. A run given its own `--out-dir` keeps its
archive there too, under `archive/`.

## The rules it lives by

- **Omit, never null.** A missing label means "not measured"; the packer skips
  every question that needs it.
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
  against its sigma, so most land near half of SNDK's (the move rule is 0.09
  sigma, the 30-minute flat band 0.07). The sums' base rates are counted on
  SPX. `python3 spec/measure_cuts.py` re-measures, read only.
- **Thresholds in the words, from one number.** The question docs name their
  thresholds in braces ("{move_rule_sigma}"); a name the code does not define
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
  than 6 minutes old, is skipped (the scanner has stopped; the last card
  stays). The SPX row has no separate options-book time (the book is rebuilt
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
   whose label is missing, or that is asleep, is skipped.
3. Answers as sentences, code (`hour.py`): "Over the last 30 minutes, did price
   rise, fall, or go nowhere? rising, JEV was 98% sure", with "held since 11:02
   ET" on a held one. Shadow answers never go in; a question under the weight
   cut would be left out, and with neutral weights none is.
4. The sums, JEV (`questions/spx_hour.json`): where price is in 30 minutes
   (flat within 0.07 sigma, 55% of reads on SPX) and in 60 (flat within 0.11
   sigma, 59%). Up, down, flat or unsure.
4b. The blend, code (`clock.py`): each sum mixed half and half with how often
   the same horizon ended up, down or flat at this time of day over the last
   20 SPX sessions, once 10 are on disk; never on a half day.
5. The card: `state/spx_jev/latest.json`.
6. Grading, code (`grade.py`), every sent run: each sum at its own mark, a hit
   and a Brier score for the blend, JEV's own sum and the clock's odds on the
   same outcome. The grades go to the question weights' `learn`: on the live
   lane the learning loop (`pool.py`) applies every newly sealed session.

## The files it writes, all under `state/spx_jev/`

- `{day}.jsonl`, one record per run: the state, the requests, the answers, the
  sum. `hour/{day}.jsonl`, one sum record per run with a request: what step 6
  grades. `latest.json`, the phone's card (below).
- `last_asked.json`, `cadence.json`, `clock_days.json`: the cadence and the
  clock's stored counts. `grades.jsonl`, `weights.json` (the sums' tallies,
  `method` and the per-question weights), `weights_log.jsonl`.
- `pool_30.json`, `pool_60.json`, `pool_log.jsonl`: the learning loop's state
  per horizon and one log line per horizon per session applied or refused.
- `archive/{day}.jsonl`: the raw archive for later machine learning, one line
  per record, append only, `schema_version` 2. A `read` record holds the read
  id (lane and row timestamp), the labels and the omitted ones with reasons,
  the exact requests and JEV's exact replies, the sums request and reply, the
  sum as shown, the cadence state (held, not due, asked), the market-context
  values the read could see with when each was known, the event tag, on the
  live lane the learning loop's forecasts, and on the opening lane the unit
  and bands. A `grade` record is each graded horizon
  keyed to its read's id; a `close_out` record is the opening lane's calls and
  tally at 10:42. No secret is ever written.
- `bars/{day}.jsonl` (the bars feed) and `context/{day}.jsonl`,
  `context/bars/{day}.jsonl` (the market feed, and its full days from the
  backfill and the day saver).
- `lanes/tape/`: the opening lane's own records, card, grades and weights.

The card carries: `symbol`, `generated_at`, `row_ts`, `freshness`, `sigma`,
`situation` (four facts with a verdict word and the figure to draw), `labels`,
`omitted`, `sent`, `model`, `questions` (each with `answer` or `skipped`, and
`held_from` when held), `hour` (the blended sum with `jev`, `clock` and
`blend`), `event`, `calls` and `tally` (the day's newest calls and their
grades), `marks`, `session` (`close` and `last_read`), `expiries` (today's
settle, the next expiry's, the next monthly's, and what expires today), and on
the opening lane `lane`, `ruler`, `band`, `stretch`, `schedule` and, after the
close-out, `closed_out_at`.

## The jobs

Five launchd templates are staged in `launchd/` and none is installed. To
hire one, copy it to `runtime/launchd/` as `<label>.plist` and name it in
`install-launchd.sh` and `uninstall-launchd.sh`.

| Label | When (the box's Pacific clock; market time is three hours later) | Runner |
|---|---|---|
| `com.mirai-station.spx-jev` | 06:32 to 13:02, at :02 and :32; the fire after the close only grades | `run-spx-jev.sh` |
| `com.mirai-station.spx-jev-tape` | 06:35 to 07:30 every 5 minutes, and 07:42 | `run-spx-jev.sh --lane tape` |
| `com.mirai-station.spx-jev-bars` | every 60 s, gated to market hours plus 12 minutes after the close | `run-spx-jev-bars.sh` |
| `com.mirai-station.spx-jev-context` | every 60 s, gated to market hours | `run-spx-jev-context.sh` |
| `com.mirai-station.spx-jev-save-day` | 13:20, once a day after the close | `run-spx-jev-save-day.sh` |

Each runner exits quietly on weekends and when the market is closed, fails
loudly when the market-hours check itself cannot run, and stops at
`SPX_JEV_DISABLE=1`. The day saver runs after the close by design, so it has
no market-hours gate: the command saves market days only, and only a session
that has closed. The key lives only in `skills/spx-jev/.env`, which is
git-ignored, so a merge never brings it: before the first live read, run
`cp .env.example .env && chmod 600 .env` in this folder and fill in
`TYPESAFE_API_KEY` (the key in `skills/sndk-jev/.env`). Without it the service
runs unsent and the card says "not sent: no key on this machine".

## Tests

    cd ~/.claude/plugins/mirai-station/skills/spx-jev && python3 -m pytest tests -q

## Not done yet

- Every label of the final set is built except the dark ones, which no feed
  carries yet. Some built labels still wait on data nothing saves: the index
  weights (`state/spx_leaders/weights.json`, the four largest-stock labels),
  daily closes for the month- and quarter-end rebalance, and the /ZN and /6E
  futures. The set's `monday_prerequisites` list the rest.
- Nothing works out the set's `code_answer`s yet, so re-asking a question when
  the code's answer changes (a schedule's `then`) is built only on the cadence
  side: such a question is held.
- The learning loop runs, but the phone switch (`pool.POOL_ON_PHONE`) is off
  pending Will's decision, and its simulation acceptance gates (06) are not built.
- No market context is on disk yet: the breadth labels are omitted on every
  replay until the feed has run, and the backfill has not been run against the
  station.
