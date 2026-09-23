# sndk-jev — the JEV decision service beside SNDK PRO

A **read-only sidecar** beside `sndk-pro`. Twice an hour on market days it
reads the diary rows and minute bars SNDK PRO already writes, turns the numbers
into plain-English labels, asks JEV the questions, sums the answers into a
30-minute and a 60-minute forecast, grades the earlier forecasts against the
bars, and writes the phone's card. It writes only under `state/jev/`, never
into SNDK PRO's own files, and never runs on the scan path.

JEV reads words and cannot compare numbers. So every comparison happens here,
in code, and is written out as a sentence with its threshold in it:

    price.recent_move = "over the last 30 minutes price rose 0.42 sigma, more than the 0.15 sigma move rule"

Sigma is the day's expected move for SanDisk, so every distance here is a
share of a normal day. A question then points at that label by name, and JEV
returns a probability for each answer option. JEV makes no trading call; code
does whatever comes next with the answers.

## Glossary

| File | Plain name | What it does |
|---|---|---|
| `sndk_jev/state_builder.py` | The Labeller | Reads one moment of SNDK PRO (a diary row, today's finished bars, the prior sessions' bars) and writes the labels. Anything it cannot measure is left out, with the reason kept in `omitted`. |
| `sndk_jev/ask.py` | The Packer | Cuts the state into one slice per question group, keeps only the questions whose labels all exist, and can POST a request to JEV. |
| `sndk_jev/build.py` | The Command | `python -m sndk_jev.build` for one moment, a replay of a day, or a live send. |
| `sndk_jev/cadence.py` | The Cadence | Which questions are due this read, what is held in between, and the daily recount. |
| `sndk_jev/hour.py` | The Sums | Rewrites the live answers as sentences and asks the two sum questions over them in one request. |
| `sndk_jev/grade.py` | The Grader | Reads the bars 30 and 60 minutes after each read, scores both sums, and moves each question's weight. |
| `sndk_jev/service.py` | The Service | One run per half hour: build, ask (when a key exists), sum, grade, write the record and the phone's card. Its own job, never on the scan path. |
| `runtime/scripts/run-sndk-jev.sh`, `runtime/launchd/com.mirai-station.sndk-jev.plist` | The Job | The launchd wrapper (beside the station's other runners, where its job test expects it) and the plist. Installed on the station since 2026-09-22; fires at :02 and :32 ET, so JEV reads once per half hour in market hours. |
| `runtime/viewstation/static/m/jev.html` | The Card | The phone page. Polls `latest.json` every 60 seconds through the viewstation's existing read-only raw-file route, so `server.py` is unchanged. |
| `page/build_page.py` | The Page | Builds the long-form page: every label, question, weight and the newest sums, from the spec, the question docs and `state/jev/`. |
| `questions/sndk_pro.json` | The Questions | 48 questions in 13 groups, each group one request: 37 live, 3 shadow, 8 dark (news, no source yet). Seven came from the end-of-day reviews (`questions/proposals/`). |
| `questions/sndk_hour.json` | The Sum Questions | The two sums, `next_30` and `next_60`, with their flat bands and base rates written into the criteria. |
| `spec/labels.json` | The Label Spec | All 61 labels, each with its source, logic, cut and the sentence it wrote on a real row; plus the overlap review and the two labels folded away. `tests/test_spec.py` pins the built set to what the code writes. |
| `tests/` | The Proof | Offline pytest with synthetic rows, bars, side packets and a chain cache. No network, no host state. |

## Run it

    cd ~/.claude/plugins/mirai-station/skills/sndk-jev
    python3 -m sndk_jev.build                          # latest row of the latest day
    python3 -m sndk_jev.build --day 2026-09-18 --at 12:45
    python3 -m sndk_jev.build --day 2026-09-18 --every 15 --out /tmp/states.jsonl
    python3 -m sndk_jev.build --news item.json         # add one news item
    TYPESAFE_API_KEY=... python3 -m sndk_jev.build --send

    python3 -m sndk_jev.service                        # one run on the newest row, writes state/jev/, no send
    python3 -m sndk_jev.service --send                 # the key comes from .env
    python3 -m sndk_jev.service --day 2026-09-18       # replay a past day's newest row

The build's output is one JSON document: `state` (the labels by group),
`omitted` (what could not be measured and why), `requests` (ready to send, one
per group) and `skipped` (questions left out and why). With `--send` an
`answers` block is added, one entry per request, and the answers are also
printed. The service writes its record and the card instead; the files are
listed under "The decision service and the phone".

## Question statuses and cadence

Each question in `questions/sndk_pro.json` carries a `viewpoint`, a `status`,
a plain `ask`, a one-line `why` and a `cadence`. The packer strips all of those
before anything reaches JEV; only `type`, `instructions` and `criteria` are sent.

- **live**: every label it reads is built today. A live question whose label is
  missing on a read is skipped, with the reason kept.
- **shadow**: a forecast with no right answer at ask time. Logged, graded by the
  bars 30 minutes later, never acted on and never a sentence in the sums.
- **dark**: never asked and never counted, because the source it needs is not
  plugged in. The eight news questions are dark until a news engine exists;
  `dark_was` in the doc keeps the status each returns to.

Cadence is enforced (`sndk_jev/cadence.py`, since 2026-09-22): a live question is
asked afresh only when its cadence has elapsed since its last fresh answer, else
the last answer is held, tagged "held since HH:MM", on the card and in the sums'
sentences. A held answer lasts up to twice the cadence and never under an hour,
and also covers a read whose label is missing. Cadences are 30, 60 or 120
minutes (reads come every 30) and are recounted once a day from the previous
day's runs: the 25th percentile of how long each answer held, halved and
snapped; never changed all day gives 60 or 120; under six reads keeps the last
value. A change is the whole answer moving, not the pick flipping: consecutive
answers are compared as probability vectors and a total shift of 0.3 or more
counts, so heavy 0.90 to heavy 0.55 is a change and 0.90 to 0.88 is not. A
question whose last two fresh answers moved 0.3 or more is in motion and is
asked again on the next read regardless. The doc's `cadence` text is the
starting value.

    python3 -m sndk_jev.cadence --day 2026-09-22          # the table a recount would set
    python3 -m sndk_jev.cadence --day 2026-09-22 --write  # and write it

`--send` posts every request of a read at the same time (`ask.send_all`), so
the up to eleven round trips (13 groups less the two dark news groups) collapse
into one wait of about the slowest request.

## The rules it lives by

- **Omit, never null.** A missing label means "not measured"; the packer skips
  every question that needs it. A forced answer is worse than no answer.
- **Point in time.** `now` is the row's own timestamp. Only bars that finished
  before it count, so a replay never reads the unfinished minute, and prior
  sessions are only days before the day being built.
- **No prices in labels.** Distances are in sigma, shares are percentages,
  strikes are described and never named.
- **Thresholds in the words.** 0.15 sigma is SNDK PRO's own move rule. The
  0.5 sigma wall cut, the 2 vol point flat band, the top-fifth and
  bottom-fifth volume bands and the momentum cuts are starting values, all
  named at the top of `state_builder.py`.
- **Baselines need history.** Volume bands need 5 prior sessions of bars at
  the same time of day, the range band needs 3. Until then those labels are
  omitted.
- **Standing assumptions.** A 16:00 close (half days are not handled, same as
  SNDK PRO), and the `range_ruler.em_consumed` field as "today's range over
  today's expected move".

## What each group reads

Three terms come up below. VWAP is the day's volume-weighted average price,
the average price of the day's trading so far. IV is implied volatility, how
big a move the options market is pricing in. RSI is a momentum gauge from 0 to
100 that says how one-sided recent bars have been. Dealers are the market
makers on the other side of the options trade, whose hedging is what the strike
labels weigh.

| Group | Labels | Source in SNDK PRO |
|---|---|---|
| `price_and_range` | recent 30-minute move, place in the day's range, distance from VWAP, opening-box status, today's range against the last 5 sessions | `spot`, `sigma`, `vwap`, the minute bars |
| `volatility` | 30-minute IV trend, put-call skew, expected move used | `atm_iv` across today's rows, `iv_skew.skew_pts`, `range_ruler.em_consumed` |
| `strikes` | which side of price holds most of the options weight, and how far the nearest heavy strike is | `gex_views.gamma_above_spot / gamma_below_spot`, `call_wall`, `put_wall`, `spot`, `sigma` |
| `volume` | last 30 minutes against the same slot on prior days, and the move against the 30 minutes before it | the minute bars' `volume`, prior days' bars |
| `momentum` | pace of the last 10 minutes, closes agreeing with the move | the minute bars |
| `strikes_next` | the strike ladder, clustering near price, wall thickness and grip, today's turnover and call-put split, next week's share, what expires tonight, the book against yesterday's and overnight, wall retests | `gex_views` (mass, net, volume and open interest by strike, the wall shares, the pin share), `net_exposure`, the wall tenors, yesterday's diary file, the side packet's level visits |
| `volume_next` | volume by direction and at price, the move's retracement, pauses | the minute bars, volume bucketed by price |
| `history_next` | price against the last two days and the prior close, yesterday's levels tested, retests of the day's high | yesterday's and the day before's bars, today's bars, the side packet's level visits |
| `indicators_next` | 5-minute RSI, stretch, this week's IV against next week's, which way the expected move leans, VWAP slope | closes sampled every 5 minutes, the side packet's RSI episodes, next week's at-the-money IV against this week's, `adaptive_em.down_share`, the bars' running VWAP |
| `space_time_next` | place against the week, minutes since the last move, how far the session has gone, bar width | the prior 5 sessions' bars plus today, the row's timestamp |
| `news_read`, `news_reaction` | headline, excerpt, earlier headlines, age, price move since arrival | a news item handed in with `--news`; SNDK PRO has no feed yet |
| `outcome_shadow` | the whole state | shadow only, never a call |

A news item is a small JSON file:

    {"headline": "...", "source": "company release", "excerpt": "...",
     "arrived": "2026-09-18T11:50:00-04:00",
     "recent_headlines": [{"text": "...", "age": "2 hours ago"}],
     "expectation": "consensus was ..."}

## Does the number in a label help or hurt?

`sndk_jev/ab_test.py` answers that by sending the same request three ways, each
a deletion from the last:

    full        "price rose 0.42 sigma, more than the 0.15 sigma move rule"
    no_figure   "price rose, more than the move rule"
    no_band     "price rose 0.42 sigma"

The right answer comes from your own code. When a labeller decides which band a
number fell into it records that decision beside the label, and the harness
scores JEV against it. A question is only graded when exactly one of the labels
it reads carries such a decision, which rules out the forecasting questions.

    python3 -m sndk_jev.ab_test --days 2026-09-15,2026-09-16 --every 20 --dry-run
    TYPESAFE_API_KEY=... python3 -m sndk_jev.ab_test --days 2026-09-15,2026-09-16 --every 20 --out ab.jsonl

Two guards, because this test is easy to fool:

- **Wording.** If one arm echoes its own correct answer more than another, the
  test measures phrasing rather than the figure. `tests/test_ab_test.py` prints
  that overlap per arm on every run. Measured on the fixture: full 87%,
  no_figure 81%, no_band 65%. The first two are close enough to compare.
- **One-sided questions.** The dry run prints each question's spread of correct
  answers and the score from always naming the most common one. Anything at 90%
  or above is flagged, because an arm can look perfect there without reading
  anything. On 2026-09-15 to 09-18: `option_activity` was `spread_out` on all 64
  moments and `rsi_band` was `between` on 62 of 64. Both should be dropped
  before drawing a conclusion.

## Tests

    cd ~/.claude/plugins/mirai-station/skills/sndk-jev && python3 -m pytest tests -q

One test asserts that every backticked path in `questions/sndk_pro.json` is a
label the builder can produce, so the questions and the labeller cannot drift
apart without a red test.

## The pipeline, six steps

1. Labels, code: 61 sentences from the row, bars, side packet and chain cache.
2. The live questions, JEV, in parallel: a probability per option. There are
   37; a question not due this read keeps its held answer instead of being
   asked, and one whose label is missing is skipped.
3. Answers as sentences, code (`sndk_jev/hour.py`): each answered live question
   becomes one line, "Over the last 30 minutes, did price rise, fall, or go
   nowhere? rising, JEV was 100% sure", with "held since HH:MM" on a held one.
   Shadow answers never go in. A question whose weight in
   `state/jev/weights.json` is under 0.5 is left out, with the reason kept.
4. Two sums, JEV (`questions/sndk_hour.json`), in one request over those
   sentences: `next_30`, where is price in 30 minutes (flat within 0.12 sigma,
   measured 59% of the time on this name), and `next_60`, the same at 60 minutes
   (flat within 0.17 sigma, 61%). Up, Down, Flat or Unsure, with the band and the
   base rate written into the criteria. `next_30` is the phone's headline and the
   weights' teacher; `next_60` is graded beside it for the comparison.
5. The card: `state/jev/latest.json` carries the sum under `hour`; the phone
   shows it first, dashed, as a forecast that is graded and never a call.
6. Grading, code (`sndk_jev/grade.py`), every sent run: each sum is graded at
   its own mark, the 30-minute sum once 30 minutes of bars exist and the
   60-minute sum at 60, so neither waits for the other. The close at the mark
   minus spot, in sigma, gives the band that happened; each sum records a hit
   (did the pick match the band) and a Brier score (how far the probabilities
   sat from what happened, 0 best, 2 worst). A mark that lands up to 2 minutes
   past the close is graded at the closing bar; a mark later than that is
   skipped for good, and a read none of whose marks can ever be graded is
   closed out so it is never retried. A read is graded once however many times
   the service ran on it. Each step-2 question's weight is 1.0 until it has 40
   fresh graded reads of its own (a held answer never pairs, since it was given
   before the outcome it would be judged on); after that its weight is its
   tracking score, the mutual information between its fresh pick and the
   30-minute band, divided by the best question's. Under 0.5 its sentence
   leaves the sums but it is still asked, so it can climb back. The sum's block
   on the card counts answers used, left out, and missing a label.

    python3 -m sndk_jev.grade        # grade by hand and print the tally

## The decision service and the phone

The service is the microservice boundary. It reads what Mirai station already
stores (diary rows, minute bars, side packets, the chain cache) and writes only
into `state/jev/`:

- `{day}.jsonl`, one record per run: `row_ts`, `book_asof`, `sigma`, `state`,
  `omitted`, `requests`, `skipped`, `held` (question to the time its answer was
  given), `cadence_from`, `answers`, `sent`, `send_seconds`, `hour`.
- `hour/{day}.jsonl`, one record per run with a sum: `row_ts`, `spot`, `sigma`,
  the sum (`pick`, `probabilities`, `confidence`, `primary`, `by` for both
  horizons, `model`), `used`, `fresh`, `left_out`, `missing`, `sentences`,
  `request`. This is what step 6 grades.
- `latest.json`, the phone's card: `symbol`, `generated_at`, `row_ts`,
  `book_asof`, `freshness`, `sigma`, `situation` (five plain lines, "sigma"
  spelled out as "of a normal day's move" because the phone bans Greek),
  `labels`, `omitted`, `sent`, `send_seconds`, `model`, `fresh`, `held`,
  `cadence_from`, `dark`, `hour` (the sum, plus `used`, `left_out`, `missing`),
  and `questions`: one entry per live or shadow question with `viewpoint`,
  `status`, `ask`, `why`, `type`, `options`, `cadence_min` and either `answer`
  (`pick`, `confidence`, `probabilities`, `noul`, `score`, with `held_from` when
  held) or `skipped` (why it was not asked). `missing` on the card is the live
  questions with no label this read and nothing held.
- `last_asked.json`: the newest fresh answer per question and how far it moved
  from the one before.
- `cadence.json`: each question's minutes, the hold quartile, changes and reads
  behind it, and the day it was recounted from.
- `grades.jsonl`: one line per graded horizon of a read (`realized_sigma`,
  `band`, `pick`, `hit`, `brier`, `p_band`, with the 30-minute sum's fields flat
  on top), or a `graded: false` line with the reason.
- `weights.json`: the tally per sum and, per question, `weight`, `mi`, `n`,
  `in_step_3` and `why`.
- `weights_log.jsonl`: one line per grading run that graded something.
- `watch/{day}.md`: the day's watch notes, written by the watcher session, not
  by the service.

The phone reads the card at `/api/raw/file?root=state&path=jev/latest.json`,
the viewstation's existing read-only route, and draws it on `/m/jev.html`
every 60 seconds; the glance has a JEV tab that opens it. Every answer is a
probability per option, never an arrow; shadow questions are drawn dashed and
say so in words; the row's time and age are always shown; a failed fetch keeps
the last card and says so. Without a key the service still runs and every
question reads "not sent, no key".

## Pause, logs, run by hand

The job is `com.mirai-station.sndk-jev`. It fires at :02 and :32 ET, exits
quietly on weekends, outside market hours, and before the scanner has written
today's first diary row, and waits until the newest row is at least 20 seconds
old before reading it.

    launchctl disable gui/$UID/com.mirai-station.sndk-jev    # pause
    launchctl enable gui/$UID/com.mirai-station.sndk-jev     # resume

`SNDK_JEV_DISABLE=1` in the job's environment does the same; the runner checks
it and exits 0. Logs: `/tmp/mirai-station.sndk-jev.out` and `.err`.

One run by hand, the way the job runs it:

    ~/.local/share/mirai-station/venv/bin/python -m sndk_jev.service --state-dir ~/.claude/plugins/mirai-station/state --send

Drop `--send` for an unsent card, or add `--day YYYY-MM-DD` to replay a past
day. The key lives only in `skills/sndk-jev/.env` (git-ignored; copy
`.env.example`); the service reads it, a value already in the environment wins,
and the runner never sees it. Never paste the key into chat or a commit.
`--send` has run live since 2026-09-22.

## The page

The long-form page, every label, question, weight and the newest sums, is
built by `page/build_page.py` and published at
https://claude.ai/artifact/BGwzSvugBdBCvUUEyRJTz6.

## Overlap review, 21 September

Every fourth stored row over ten sessions (401 moments) was replayed through
`build_ab`, each label's verdict counted, and normalised mutual information
(NMI, 0 when two labels move independently, 1 when one tells you the other)
measured between every pair of verdict labels. Findings, also in
`spec/labels.json` under `review`:

- One-sided: `momentum.rsi_1min` (between 92%) and `options.activity`
  (spread_out 99.5%) stay as labels only. `iv.move_sides` (even 87%) and
  `momentum.vwap_slope` (flat 84%) stay live and are watched.
- Pairs that move together: `price.day_range_position` and `price.vs_vwap`
  (NMI 0.50) are kept, different objects. Gamma side and delta side agree only
  12% of the time, so both stay.
- Folded: `context.session_phase` into `context.session_progress`;
  `price.vs_week_extremes` into `price.multi_day_position`. Yesterday's close
  now lives only in `price.vs_prior_close`. `range.prior_level_touches` counts
  minutes within reach, not visits.

## Not done yet

- No push. A ntfy message when an answer flips is drawn dashed on the page.
- No decoy control. Feeding JEV an old, unlabelled scene every twentieth call
  to check it reads the labels rather than guessing is on SNDK PRO's plan and
  is not built.
- No news feed exists in Mirai. The eight news questions run only when an item
  is handed in.
- The weights have not moved yet: no question has 40 fresh graded reads, so
  every sentence still counts 1.0.
