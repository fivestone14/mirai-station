# Decisions

The choices this project runs on, and why. How it works is in the code, its docstrings and the README;
what every stored field means is in `record_formats.md`; what is built and what is next is in `phases.md`.
When the code and this page disagree, the code is what runs: fix the page.

## What it is

Claude's own forecast beside the SPX voices. After every half-hour SPX read, code freezes one payload of
the market, Claude reads it next to the base rate for that time of day and eight past moments with their
outcomes, and answers with up / flat / down percentages (plus size buckets) for the next 30 minutes, the
next 60 minutes and the close. Payload, answers and what actually happened are stored together on one key
and graded every night. It is never mixed into the combined call; it earns a place or it does not.

## The payload

- **Built from saved files only, cut at the read's own time.** Never a live quote: a read rebuilt next
  month from the same files gives the same scene. Fifteen blocks, one module each.
- **Units Claude cannot recall.** Every move and distance is in `sig` (the day's expected move) or in flat
  edges; never an index level, a strike, a date, a weekday, a headline title, or another voice's answer
  (JEV, Pool 2, the clock odds). The leak check refuses the whole payload if any of these appear.
- **Absences are declared, never guessed.** A field that cannot be given is listed under `absent` with a
  short reason (`stand_in_book`, `not_until_09:35`, `tape_stale` ...).
- **The SPY stand-in options book never reaches Claude.** Every field priced off the book is absent on
  those reads; the day is also kept out of the base rate and the precedents when it sized the ruler.
- **Same-clock ranks** follow the station's own rule (up to 20 sessions, at least 10), on the measured
  value, never the rounded one shown.

## The history Claude sees

- **The base rate is shown, unsmoothed.** How this time of day ended on earlier sessions, the neighbouring
  half-hours pooled in, one vote per session, all history, no fading, counts exactly as they are. Shown
  because a forecaster with no base rate spreads its chances evenly (seen on the first real call); the
  scorer floors a chance at 2 percent, so smoothing would only flatter Claude's skill against it.
- **Eight past moments, matched on seven everyday items.** The opening gap, the move since yesterday's
  close, the last 30 minutes, the day's range, where price sits in it, the distance from VWAP and the VIX
  term ratio. A time-of-day gate first (within 45 minutes), then a root-mean-square distance over the
  items with each difference divided by its spread across the pool, nearest first, one per session.
  No event matching (one-off days do not pair up), no text embeddings (they rate "rose" and "fell" as
  nearly the same). Cards show those items and the outcome in flat edges; never a date or a price.
- **Claude never sees its own record** in year one: with 13 reads a day no digest can be told from noise
  before about 100 sessions. The scorecard is Will's.

## The call

- **Opus 5.5, effort medium, pinned.** Always passed explicitly: an unpinned effort is production configured
  from outside the repo (a global `xhigh` once made every call on this machine reason ten times longer).
- **Two answers per read**, the cards shuffled one way then reversed with the direction order flipped,
  averaged, so order bias cancels; the gap between them is the noise floor.
- **Subscription only.** `claude -p` on the claude.ai login, every tool denied (`--disallowedTools` last on
  the command line, the only flag that actually denies), MCP emptied, run from an empty folder, no session
  files. The call refuses to run if an API key is in the environment, so it can never bill the API.
- **One attempt, 120 seconds, no retry.** Nothing waits on the call: the SPX read starts it in its own
  process and never sees it. A failed call is recorded, not retried.
- **Abstaining is code's decision, not Claude's.** There is no "unsure" key; an answer that cannot be told
  from the base rate is the base rate, and the scorer treats it so.

## Code's checks on an answer

Delete what fails, never rewrite it, and say what was done. Shares off 100 by at most 2 are rescaled, off
by more the horizon is rejected and scores as the base rate. A size split off by 1 is fixed on its largest
bucket, off by more it is dropped. A precedent must be a shown card; a reason must point at a field that
is in the scene and not under `absent`. With no reason left the answer is still graded, flagged ungrounded.

## Grading

- **The station's own rule.** The next 30 and 60 minutes are graded on the average price over the window
  against the read price, with the flat edge the station sizes for the read under its flat-zone rule
  (`average_30` zone for 30 minutes, the `next_60` zone narrowed by the average-price factor for 60). On
  the 10-09 reads the grades are identical to the station's.
- **To the close** is graded on the official close; the station has no box for it, so its edge is the
  60-minute zone scaled by the square root of the minutes left, with the station's 2-point floor.
- **Exactly one flat edge counts as flat.** Seven size buckets from `down_over_3_flat_edges` to
  `up_over_3_flat_edges`.
- **Staleness is the grader's call, not the payload's.** The station's rule looks at the bar of the read's
  own minute, which has not finished at the cut.
- **Scores** use the station's floored log loss (2 percent); a missing, paused or rejected Claude horizon
  scores as the base rate, so skipping a hard read can never flatter the score; coverage is reported apart.
  "Too early" under 100 graded reads.

## Storage

- **One folder, `state/spx_claude_forecast/`, append-only daily files**: `payloads/` (what Claude saw,
  written before any call), `reads/` (each answer as it lands, then the final forecast), `outcomes/`
  (sealed nightly, never edited; a regrade appends a new rule version). Everything joins on `read_id`,
  with `claude_input_sha256` fingerprinting the exact scene.
- **The library is rebuilt whole every night** from those files and the seed, and can always be deleted
  and rebuilt. The seed (`seed:` reads) is history only, never scored as Claude.
- **The package never writes anywhere else.** Every writer refuses a path outside its folder; a line
  written into the prediction system's files would make Pool 2 adopt the voice at a weight by accident.
- **The recorder** copies station data before it is overwritten or deleted (the dated options book, siege's
  SPY minutes, the raw tape, the $VIX1D close, SPX 5-minute bars), and the folder is mirrored to iCloud
  Drive; nothing else in `state/` is backed up.

## Operations

- Kill switch `SPX_CLAUDE_FORECAST_DISABLE=1`; it also stands down under `SPX_JEV_DISABLE=1`. Pause and
  daily caps (45 calls, $15 notional) in `control.json`. A cold-cache call costs about $0.31 at list
  price; warm calls far less; on the subscription it is quota, not dollars.
- Under pytest nothing is spawned and the real caller is refused: the SPX read's own tests run on
  synthetic states realistic enough to reach a real call (they did once).
- Jobs: the recorder at 08:40 and 16:25 ET plus Friday 17:30 ET; the nightly at 17:15 ET, after the daily
  close is saved. The hook is one guarded call in `spx_jev/service.py`, right after the read archives its
  record.

## The test variants (Phase 8)

- **Proof, not forecasting power.** The live reads are untouched. Each night three of the day's usable reads
  (the first, the middle, the last) are asked again, one answer each, with the history deliberately broken:
  the cards removed (`no_precedents_shown`), the same cards with their outcomes dealt to other cards
  (`precedent_outcomes_shuffled`), and eight cards drawn at random from the same time-of-day pool
  (`random_precedents_shown`). Everything else in the scene is the production read's, byte for byte.
- **Scored beside production on the same outcome.** The scorecard's `test_variants` block pairs each
  variant with the production forecast read by read: production's skill over a variant near zero means
  Claude was not using the cards; well above zero means the history as shown was worth something. That is
  what decides whether the cards stay, change, or go, before Claude is trusted with any weight.
- **Never mixed in.** The replies live in `arms/`, stamped with the variant and the fingerprint of the
  changed scene; they are never graded as the read, never in the library, never on the phone.
- **Same gates, own cap.** The variants run under the live call's gates (pytest, pause, the daily cap) and a
  cap of nine answers a night; every shuffle and draw is seeded by the production scene's hash, so a night can
  be replayed.
- **Checkpoints.** The scorecard says which of Will's review points (150, 300, 600 graded reads) are
  reached and which comes next; the review itself is Will's.

## Where the build departed from the plan

Eight cards, not ten. No event matching. No freeze-only phase: live from day one, seeded from 07-20. The
base rate is shown unsmoothed. Staleness comes from the outcome line. The station's grader changed to its
flat-zone rule the day this was built, and the grader follows it rather than the planned sigma bands.

## Not built

The other test variants the record formats name (no base rate shown, earlier results today shown, five
answers averaged, own track record shown), a recorder for foreign quotes, the leveraged-ETF estimate, the
Schwab login days-left, and situation-level score cells.
