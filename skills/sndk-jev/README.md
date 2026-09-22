# sndk-jev — the state builder for JEV, SNDK PRO's fast judge

A **read-only sidecar** beside `sndk-pro`. It reads the diary rows and minute
bars SNDK PRO already writes, turns the numbers into plain-English labels, and
packs them with the JEV questions into requests. It writes nothing into SNDK
PRO's own files and never touches the live loop.

JEV reads words and cannot compare numbers. So every comparison happens here,
in code, and is written out as a sentence with its threshold in it:

    price.recent_move = "over the last 30 minutes price rose 0.42 sigma, more than the 0.15 sigma move rule"

A question then points at that label by name, and JEV returns a probability
for each answer option. JEV makes no trading call; code does whatever comes
next with the answers.

## Glossary

| File | Plain name | What it does |
|---|---|---|
| `sndk_jev/state_builder.py` | The Labeller | Reads one moment of SNDK PRO (a diary row, today's finished bars, the prior sessions' bars) and writes the labels. Anything it cannot measure is left out, with the reason kept in `omitted`. |
| `sndk_jev/ask.py` | The Packer | Cuts the state into one slice per question group, keeps only the questions whose labels all exist, and can POST a request to JEV. |
| `sndk_jev/build.py` | The Command | `python -m sndk_jev.build` for one moment, a replay of a day, or a live send. |
| `sndk_jev/cadence.py` | The Cadence | Which questions are due this read, what is held in between, and the daily recount. |
| `sndk_jev/service.py` | The Service | One run per scan: build, ask (when a key exists), write `state/jev/{day}.jsonl` and `state/jev/latest.json`, the phone's card. Its own job, never on the scan path. |
| `run-sndk-jev.sh`, `launchd/` | The Job | The launchd wrapper and plist template for the service. Installed on the station since 2026-09-22; fires at :02 and :32, so JEV reads once per half hour in market hours. |
| `runtime/viewstation/static/m/jev.html` | The Card | The phone page. Polls `latest.json` through the viewstation's existing read-only raw-file route, so `server.py` is unchanged. |
| `questions/sndk_pro.json` | The Questions | 41 questions in 13 groups, each group one request: 30 live, 3 shadow, 8 dark (news, no source yet). |
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
    TYPESAFE_API_KEY=... python3 -m sndk_jev.service --send
    python3 -m sndk_jev.service --loop 120             # keep running by hand instead of launchd

The output is one JSON document: `state` (the labels by group), `omitted`
(what could not be measured and why), `requests` (ready to send, one per
group) and `skipped` (questions left out and why). With `--send` an `answers`
block is added, one entry per request, and the answers are also printed.

## Question statuses and cadence

Each question in `questions/sndk_pro.json` carries a `viewpoint`, a `status`,
a plain `ask`, a one-line `why` and a `cadence`. The packer strips all of those
before anything reaches JEV; only `type`, `instructions` and `criteria` are sent.

- **live**: every label it reads is built today.
- **waiting**: it reads a free or new label and the packer skips it until that
  label exists. `tests/test_ask.py` checks that a waiting question is skipped
  only because of an unbuilt label, never a built one.
- **shadow**: a forecast with no right answer at ask time. Logged, graded by the
  bars 30 minutes later, never acted on.
- **dark**: never asked and never counted, because the source it needs is not
  plugged in. The eight news questions are dark until a news engine exists;
  `dark_was` in the doc keeps the status each returns to.

Cadence is enforced (`sndk_jev/cadence.py`, since 2026-09-22): a live question is
asked afresh only when its cadence has elapsed since its last fresh answer, else
the last answer is held, tagged "held from HH:MM", on the card and in the sums'
sentences. A held answer also covers a read whose label is missing, for up to
twice the cadence. Cadences are 30, 60 or 120 minutes (reads come every 30) and
are recounted once a day from the previous day's runs: the 25th percentile of
how long each answer held, halved and snapped; never changed all day gives 60
or 120; under six reads keeps the last value. A change is the whole answer
moving, not the pick flipping: consecutive answers are compared as probability
vectors and a total shift of 0.3 or more counts, so heavy 0.90 to heavy 0.55 is
a change and 0.90 to 0.88 is not. A question whose last two fresh answers moved
0.3 or more is in motion and is asked again on the next read regardless. The doc's `cadence` text is the
starting value. Files: `state/jev/cadence.json`, `state/jev/last_asked.json`.

    python3 -m sndk_jev.cadence --day 2026-09-22          # the table a recount would set
    python3 -m sndk_jev.cadence --day 2026-09-22 --write  # and write it

`--send` posts every request of a scan at the same time (`ask.send_all`), so
the seven to thirteen round trips collapse into one wait of about the slowest
request.

## The rules it lives by

- **Omit, never null.** A missing label means "not measured"; the packer skips
  every question that needs it. A forced answer is worse than no answer.
- **Point in time.** `now` is the row's own timestamp. Only bars that finished
  before it count, so a replay never reads the unfinished minute, and prior
  sessions are only days before the day being built.
- **No prices in labels.** Distances are in sigma (today's expected-move unit
  for SanDisk), shares are percentages, strikes are described and never named.
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

| Group | Labels | Source in SNDK PRO |
|---|---|---|
| `price_and_range` | recent 30-minute move, place in the day's range, distance from VWAP, opening-box status, today's range against the last 5 sessions | `spot`, `sigma`, `vwap`, the minute bars |
| `volatility` | 30-minute IV trend, put-call skew, expected move used | `atm_iv` across today's rows, `iv_skew.skew_pts`, `range_ruler.em_consumed` |
| `options_and_gex` | weight above or below price, nearest wall, contract concentration, what changed since the last distinct book | `gex_views.gamma_above_spot / gamma_below_spot`, `call_wall`, `put_wall`, `vol_gross_by_strike`, `mass_by_strike`, `gamma_sign`, `meta.book_asof` |
| `volume` | last 30 minutes against the same slot on prior days, and the move against the 30 minutes before it | the minute bars' `volume`, prior days' bars |
| `momentum` | 1-minute RSI, pace of the last 10 minutes, closes agreeing with the move, pauses and pullbacks | the minute bars |
| `time` | session phase, front expiry | the row's timestamp, `meta.expiries` |
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

    cd ~/.claude/plugins/mirai-station/skills/sndk-jev && python3 -m pytest -q

One test asserts that every backticked path in `questions/sndk_pro.json` is a
label the builder can produce, so the questions and the labeller cannot drift
apart without a red test.

## The pipeline, six steps

1. Labels, code: 61 sentences from the row, bars, side packet and chain cache.
2. Thirty live questions, JEV, in parallel: a probability per option.
3. Answers as sentences, code (`sndk_jev/hour.py`): each answered live question
   becomes one line, "Over the last 30 minutes, did price rise, fall, or go
   nowhere? rising, JEV was 100% sure". Shadow answers never go in. A question
   whose weight in `state/jev/weights.json` is under 0.5 is left out, with the
   reason kept.
4. Two sums, JEV (`questions/sndk_hour.json`), in one request over those
   sentences: `next_30`, where is price in 30 minutes (flat within 0.12 sigma,
   measured 59% of the time on this name), and `next_60`, the same at 60 minutes
   (flat within 0.17 sigma, 61%). Up, Down, Flat or Unsure, with the band and the
   base rate written into the criteria. `next_30` is the phone's headline and the
   weights' teacher; `next_60` is graded beside it for the comparison.
5. The card: `state/jev/latest.json` carries the sum under `hour`; the phone
   shows it first, dashed, as a forecast that is graded and never a call.
6. Grading, code (`sndk_jev/grade.py`), every run: for each record whose 30
   and 60 minutes have passed, the close that many minutes later minus spot, in
   sigma, gives each sum's band; hit and Brier score per sum are recorded in
   `state/jev/grades.jsonl`; each step-2 question's weight is its mutual
   information with the 30-minute outcome relative to the best question's, and
   stays 1.0 until 40 records are graded. Every change is appended to
   `state/jev/weights_log.jsonl`.

    python3 -m sndk_jev.grade        # grade by hand and print the tally

## The decision service and the phone

The service is the microservice boundary. It reads what Mirai station already
stores (diary rows, minute bars, side packets, the chain cache) and writes only
into `state/jev/`:

- `state/jev/{day}.jsonl`, one record per run: state, omitted, requests,
  skipped, answers, timing.
- `state/jev/latest.json`, the phone's card: `symbol`, `row_ts`, `book_asof`,
  `freshness`, `sigma`, `situation` (five plain lines, "sigma" spelled out as
  "of a normal day's move" because the phone bans Greek), `labels`, `omitted`,
  `sent`, `model`, and one entry per question with `viewpoint`, `status`,
  `ask`, `why`, `type` and either `answer` (`pick`, `confidence`,
  `probabilities`, `noul`, `score`) or `skipped` (why it was not asked).

The phone reads the card at `/api/raw/file?root=state&path=jev/latest.json`,
the viewstation's existing read-only route, and draws it on `/m/jev.html`
every 60 seconds. Every answer is a probability per option, never an arrow;
shadow questions are drawn dashed and say so in words; the row's time and age
are always shown; a failed fetch keeps the last card and says so. Without a key
the service still runs and every question reads "not sent, no key".

## Overlap review, 21 September

Every fourth stored row over ten sessions (401 moments) was replayed through
`build_ab`, each label's verdict counted, and normalised mutual information
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

- `--send` is written against the documented API shape and has not been run
  against a live key. The key lives in `skills/sndk-jev/.env` (git-ignored,
  see `.env.example`); the service and the runner read it, and a value already
  in the environment wins. Never paste the key into chat or a commit.
- The launchd job is installed (2026-09-22): `runtime/launchd/com.mirai-station.sndk-jev.plist`,
  linked into `~/Library/LaunchAgents`, named in `install-launchd.sh` and
  `uninstall-launchd.sh`. Logs: `/tmp/mirai-station.sndk-jev.out` and `.err`.
  Kill switch: `SNDK_JEV_DISABLE=1` in `runtime/scripts/env.sh`.
- No link from the glance to `/m/jev.html`: the glance allows exactly one link
  and a test enforces it. Open the card by its URL, or decide where the link goes.
- No push. A ntfy message when an answer flips is drawn dashed on the page.
- No grading. Answers are packaged and logged; scoring them against outcomes
  is the next step, on the decoy-control plan SNDK PRO already has.
- No news feed exists in Mirai. The eight news questions run only when an item
  is handed in.
