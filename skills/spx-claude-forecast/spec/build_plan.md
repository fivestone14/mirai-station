# SPX Claude Forecast: build plan (`spx_claude_payload/1.0.0`, era `cr-1`)

**Status: design only.** Nothing has been built, edited, scheduled or committed. Every step below waits for Will's go.

Path shorthand: **P** = `~/.claude/plugins/mirai-station`, **S** = `P/state`, **W** = `P/skills/spx-claude-forecast/spec` (the design session's scratch files were copied here).

## Bottom line

- **What it is:** a new branch that runs next to today's SPX voices. At every half-hour SPX read, code builds one frozen "scene" of the market (the payload) and saves it. Claude then gives **up / flat / down percentages** for three horizons: the next 30 minutes, the next 60 minutes, and the close. Its answer is saved too, and that night it is graded against what actually happened.
- **Where its answer goes:** onto the list of graded reads and onto the scoreboard as its own tile. It does **not** go into the combined call (Pool 2) until Will decides it has earned a place.
- **Live from day one**, with no freeze-only trial phase. Stored history back to 07-20 seeds the library, so on the first morning Claude already has past examples to compare with (50 usable sessions at the 14:30 slot).
- **Its own folder:** the code goes in `P/skills/spx-claude-forecast/` and the data in `S/spx_claude_forecast/`. The one change outside that folder is a single guarded line in `spx_jev/service.py`.
- **The mockup has been rebuilt from the real stores** with every valid pressure-test fix applied (section 11). Measured size: 9,076 characters sent per call, which is about 6.1k new tokens.

---

## 1. Goal

1. Store, for every SPX read, **exactly what Claude saw**, then **what Claude predicted**, then **what actually happened**. All three are joined on one key, so any read can be audited or re-scored later.
2. Let Claude **do the matching**. Code puts the 10 nearest past moments on the table, each with its known outcome. Claude says which ones today looks like, and why.
3. Have Claude **set the percentages itself** for each horizon, in the same "flat line" units the SPX grader already uses. That lets its score sit beside Pool 2 and the clock odds, measured the same way.
4. Prove or disprove that Claude adds anything **beyond the plain base rate** before it is ever allowed near the combined call.

## 2. The pipeline, in plain steps

```
 every half hour, 09:30 .. 15:30 ET (13 reads a day)
 SPX read lands  ──►  build the payload (scene cut at the read's own timestamp, from saved files only, zero Schwab calls)
                       │   leak checks; a failed check writes "refused", and no call is made
                       ▼
                 save the payload line FIRST  ──►  payload library (state/spx_claude_forecast/payloads/)
                       │
                       ▼
                 Claude reads today's scene next to 10 past scenes (with their outcomes) + the base rate
                       │   2 samples: s1 up-first, s2 down-first with the cards reversed
                       ▼
                 Claude's read: up/flat/down % for 30 min, 60 min and to the close + the matches it chose + reasons
                       │   code checks every claim; anything wrong is deleted, never rewritten
                       ▼
                 list of graded reads + phone tile  (NOT in Pool 2 / the combined call)
 17:15 ET nightly ──► seal the actual result ──► score vs base rate, clock odds, Pool 2 ──► the day joins the library
                       for tomorrow's matching and base rates
```

- **Seeded history:** `seed.py` rebuilds a scene and its outcome for every past half-hour from 07-20 onward, using the same builder and the same grader. Seed rows are tagged `src: seed`. They serve only as precedents and base-rate counts and are never scored as Claude reads.
- **Missed reads are written down.** Every scheduled slot gets a line: built, blind, refused, paused or missing. A gap shows up as "missing" rather than vanishing.

## 3. Folder and project structure (own folder: yes)

**Where everything lives (all inside the Mirai project, beside its siblings):**

| What | Path | Next to |
|---|---|---|
| Code, plan, specs | `P/skills/spx-claude-forecast/` | `skills/spx-jev/`, `skills/sndk-pro/` |
| Data (payloads, reads, outcomes, library) | `S/spx_claude_forecast/` | `state/spx_jev/` |
| Map folder (shortcuts + review diagrams) | `~/Desktop/Mirai-Awakening/13 - SPX Claude Forecast (Claude's own read)/` | `12 - SPX-JEV` |
| The one outside change | one guarded line in `P/skills/spx-jev/spx_jev/service.py` | none |


A separate folder keeps the reader out of Pool 2's path, lets it have its own launchd jobs and kill switch, and follows the station's `skills/<kebab>/<snake_pkg>/` pattern.

```
P/skills/spx-claude-forecast/
  README.md  pyproject.toml
  launchd/  com.mirai-station.spx-claude-forecast-nightly.plist.template     (17:15 ET)
            com.mirai-station.spx-claude-forecast-recorder.plist.template    (02:35, 03:35, 09:25, 11:35, 16:25 ET)
  spx_claude_forecast/
    paths.py        state root, file names
    hook.py         spawn_claude_forecast(read_id): detached, never raises, never blocks the SPX read
    build.py        scene builder, cut at row_ts; blocks/ holds one small module per block
    fingerprint.py  the 14 match fields + frozen scaler;  library.py: shortlist, base rates
    render.py       canonical JSON, payload_id, prompt text, keep_payload()
    rulebook.py     cached rules + OUTPUT text (rules_sha)
    call.py         claude -p transport, isolated working folder, bill logging, canary
    checks.py       leak, sum and grounding checks;  grade.py: seals outcomes, reusing spx_jev.integral / grade / scores
    nightly.py      coverage, seal, score, library, base rates, arms, scorecard
    seed.py         rebuilds past payloads and outcomes (src "seed")
    recorder.py     LETF shares, foreign snapshot, VIX1D close, dated options-book copy, flow-minute freeze
  tests/            every leak or sum check made to fail once; a "never writes new_voice_forecasts" test
P/runtime/scripts/run-spx-claude-forecast-nightly.sh, run-spx-claude-forecast-recorder.sh

S/spx_claude_forecast/
  control.json                     pause switch, daily call cap, $ cap
  rules/<rules_sha>.txt            rulebook text, stored once per hash
  payloads/{day}.jsonl             Part 1: the payload, written before any call
  reads/{day}.jsonl                Part 2: two sample lines + one voice line per read
  outcomes/{day}.jsonl             Part 3: sealed nightly, never rewritten
  arms/{day}.jsonl                 nightly control-arm replies (never mixed into the record)
  library/rows/day=YYYY-MM-DD/part-0.parquet   payload + outcome per read (rebuildable)
  library/base_rates/{next_day}.json
  seed/payloads/{day}.jsonl  seed/outcomes/{day}.jsonl
  recorder/letf/  recorder/foreign/  recorder/vix1d_close.jsonl  recorder/dated_book/  recorder/flow_minutes/
  latest.json  scorecard.json      phone tile files (replaced atomically)
  run_logs/                        30-day prune
```

**The one change outside the folder:** a guarded call to `spawn_claude_forecast` placed right after `archive.append` in `P/skills/spx-jev/spx_jev/service.py`, **outside** the `if do_send and lane.pool and ... not forecast_now_done ...` block at line 928.
- Inside that block it would almost never fire: on all 12 reads on 10-09, `forecast_now` ran first, so the branch was skipped.
- The guards are: `do_send`, live lane, `day is None`, and `SPX_CLAUDE_FORECAST_DISABLE` not set.
- Under the "ask before editing Mirai" rule, this line waits for Will.

---

## 4. The payload (what Claude reads)

**Units in plain English**
- **sig:** today's expected daily move of the index, fixed each morning (the "anchor"). It was 75.31 points on 10-09. Every distance in the scene is in sig, never in index points.
- **edge:** the grader's flat line for a horizon. Inside one edge either way counts as "flat", and an exact edge counts as flat too.
  - 30 minutes: 0.041 sig.
  - 60 minutes: 0.064 sig.
  - To the close: 0.07 sig × (minutes left ÷ 30)^0.652, which is 0.143 sig at 14:30.
- **rank `r: [a, b]`:** bigger than a of the last b sessions at the same minute.
- **Buckets:** D3 (beyond −3 edges), D2, D1, F (−1 to +1), U1, U2, U3 (beyond +3).

**Blocks.** Token counts are measured on the rebuilt 10-09 14:30 mockup, using SNDK's measured 1.83 characters per token.

| # | Angle | What it holds (units) | Source | Status on day one | ~tokens |
|---|---|---|---|---|---|
| 1 | `base_rate` (shown first) | how this half-hour ended on earlier sessions: counts in the 7 buckets per horizon, with the session count | library (seed from 07-20) | live: 50 sessions at 14:30 | 100 |
| 2 | `data_sources` | cut time; age of each feed; which options book (native / Schwab SPX / stand-in); tape content check vs usual; Schwab login days left | file ages of bars, diary, context, lob_flow agg, headlines, dated book; token age | live | 234 |
| 3 | `clock` | minutes after open and to close; phase | `spx_jev/clock.py`, `sessions.py` | live | 34 |
| 4 | `calendar` | day class; today's releases; next big release; Fed phase; trading day of month and the T−3..T+3 window; pension stock-minus-bond gap this month with its rank; expiry | `calendar/events.json`; `code_features_market.month_position`; `daily_closes` $SPX, TLT | live (events file ends 12-31) | 204 |
| 5 | `scale` | ruler source; live vs morning ruler (`sigma_live_x`); ATM IV %; straddle at open / left / used; VIX1D and its prior close; realized vs priced | diary `sigma_anchor`, `range_ruler`; context $VIX1D | live; VIX1D only since 09-28 | 112 |
| 6 | `tape` | price vs prior close / open / VWAP; last 30 and 60 min; range and position in it; high and low with clock time; path efficiency; open crossings; realized vs usual; last six 5-min moves; half-hour path | SPX 1-min bars; diary VWAP (median of 3 rows) | live; VWAP from 08-11 | 395 |
| 7 | `open_signals` | gap (settled and print, named apart; share kept; same-day fill rate for this print-gap size, with its n); noise band; first-10-min option tilt with feed check | `daily_closes` (2022+), bars, `lob_flow/agg` | live | 193 |
| 8 | `close_signals` | r1 (with the minute it ran to); r12 and rROD at the 15:30 read only; opening dealer gamma and the rROD gate; leveraged-ETF rebalance estimate | bars, `daily_closes`, first diary row, new 09:25 LETF recorder | live except LETF (needs the recorder) | 163 |
| 9 | `options` (whole block dropped on a stand-in book) | regime by book; net gamma in $bn per 1% (0DTE, 0–7DTE, prior close); share of gamma above price; top strike share; levels as distances (flip, heavy strikes, magnet, charm wall as a location only, OI walls); IV change; skew | diary `gex_views`, `net_exposure`; dated copy of `dated_gex/book.json` | live on native book | 381 |
| 10 | `flow` (dropped if the tape fails its content check) | 15-min tilt; signed share; 30-min lean vs usual; big prints; premium pace; SPY volume and spread | `lob_flow/agg` folds as written (never recomputed from raw tape); `siege/baseline.json` | live | 140 |
| 11 | `internals` | advancers minus decliners as seen live (`add_live_approx`); 30-min change; advancing share | archive `market_context` | live since 09-28 | 34 |
| 12 | `cross_asset` | VIX and change; VIX/VIX3M and prior close; backwardated flag; VIX9D/VIX; 10-year since close | context quotes, `daily_closes` | live | 97 |
| 13 | `overnight` | /ES vs close; range; where the gap was made; 10-year futures; seven overnight stretches | `spx_jev/overnight` via `story.py`, `night_ranks.py` | live since 03-05 | 142 |
| 14 | `foreign` (background only) | Tokyo; CAC 40 frozen at Europe's 11:30 close; DAX; USD/JPY from /6J; shock flag | new recorder (02:35, 03:35, 09:25, 11:35) + daily-close backfill | needs the recorder | 69 |
| 15 | `news` (titles withheld) | items in the last 60 min; index-mover items; kinds; minutes since newest; stale repeats dropped | `headlines/{day}.jsonl` via `headlines_before` | live since 10-07 | 71 |
| 16 | `code` | at most 12 code words not already shown elsewhere | `mirai_prediction/raw/code_features` (live rows only) | live since 10-08 | 180 |
| 17 | `precedents` | the "now" row + 10 nearest past rows: 14 match fields, time of day, outcome in edges | library | seed from 07-20 | 1,885 |
| 18 | `absent` | every missing field, with the reason | builder | — | 239 |

**Size.**
- Scene: 8,768 characters (about 4.8k tokens).
- Precedents: 3,450 characters.
- Per-read `ask`: 235 characters.
- Per call, including SNDK's fixed CLI overhead: about **6.1k new tokens**.

**Caps are set in characters:** scene ≤ 9,500 and precedents ≤ 3,600. When over a cap, trim in this order: compress `absent`, then `code`, then cut the cards from 10 to 8. Recalibrate from the first day's logged bills.

**Special reads**
- **First read (row stamped before 09:31):**
  - These are declared absent: `tape.*` (except prior-day fields), the settled gap, the noise band, `flow.*`, VIX1D, VIX/VIX3M and VIX9D/VIX.
  - A quote counts only once it differs from the prior close. All of these stay stale until 09:31–09:32 on 9 of 9 opens.
  - r1 is absent ("not until 10:00").
- **15:30 read:**
  - h60 is left out, because it ends past the close.
  - r12 and rROD are present, computed from the **newest finished bar at or before 15:30**, with `to: "HH:MM"`. If that bar has not finished, they are absent with "target minute not finished". The test case is the 10-07 read at 15:29:17.
- **15:00 read:** h60 is graded at the closing bar.
- **Stand-in options book** (29% of live reads, 36 of 123):
  - Every field built from the options book is dropped: the `options` block, `scale.atm_iv_pct`, `straddle_*`, `realized_30m_vs_priced_x`, gamma at the open, and the card fields `gamma` and `price_minus_flip`.
  - So are 30-minute changes that cross a book switch.
  - Precedents allow both books, but match without the gamma fields.
- **Blind read:** written when the Schwab login has lapsed, or bars or the diary row are more than 3 minutes old. The line is written; no call is made.

---

## 5. Data access results

| Signal | Status | Source | Decision |
|---|---|---|---|
| rROD (prior close → 15:30, short-gamma days only) | computable from stored data | `daily_closes/$SPX`, `spx_jev/bars`, `reversion/bars`; gate from first diary row | **include** at 15:30 only. Own data ran the other way: 7 of 23 |
| Dealer gamma sign at the open | live + history since 06-26 | diary `gex_views.regime/net_gex`, `gex_source` | **include**, tagged with its book |
| r1 / r12 agreement | computable | bars + `daily_closes` | **include**. Own data: 13 of 41, against the paper's ~77% |
| Leveraged-ETF rebalance | Schwab serves it, not recorded | `get_quotes` sharesOutstanding + fundLeverageFactor for SPXL, UPRO, SSO, SPXU, SPXS, SDS, SH (~$1.07B per 1%) | **include** via 09:25 recorder; no history |
| Charm direction | recorded, but always negative (73 of 73 days) | diary `cex_sign` | **exclude** the direction; charm wall kept as a location |
| Signed charm from the tape | computable (30-day raw tape) | `lob_flow/raw` | later |
| SPX 5-min history back to 01-22 | Schwab serves it, not recorded, loses a session a day | `schwab.five_minute_bars` | **include** as `seed_5m` close-signal rows; save now |
| Fed decision flag | live, no history (from 07-29) | `events.json`, EVENTS-02 | **include** |
| Pre-Fed drift value | computable for 5 meetings | bars, /ES 5-min | later (needs the 2016+ Fed dates) |
| Month cycle T−3..T+3 | computable for any date | `month_position` | **include** |
| Pension 60/40 drift | live + history since 2016 | `daily_closes` $SPX, TLT | **include** (+1.37 pts, larger than 53 of 119 months) |
| Pension with AGG / SPY | Schwab serves it, not recorded | `daily_bars` | later (one-line `daily_closes` add) |
| Macro release day | live; tier 1 since 07-14, all tiers since 09-01; ends 12-31 | `events.json` | **include**; extend the file before January |
| VIX futures curve | **not available** (Schwab: invalidSymbols) | — | exclude; use VIX/VIX3M instead |
| VIX/VIX3M | live + history | context quotes, diary `vix_ts` | **include** |
| VIX3M / VIX6M daily depth | Schwab serves it, not recorded | `daily_bars` | later |
| Noise band + VWAP | computable / recorded (VWAP since 08-11) | bars, diary `vwap` | **include** |
| Gap size + fill base rate | recorded since 2016 | `daily_closes` | **include** (536 gaps, 58.0% filled) |
| $TICK, $VOLD | wrong during the session, clean only next day | — | **exclude** |
| $ADD (advancers − decliners) | live since 09-28 | context $ADVN − $DECN | **include** as `add_live_approx` |
| Sector-fund breadth | recorded | context | later (not in the map) |
| Option tilt, first 10 min | recorded since 07-07 | `lob_flow/agg` | **include**, with a feed check |
| Premium-signed imbalance (raw) | computable, deleted after 30 days | raw tape | later; freeze nightly now |
| Schwab $SPX chain (fallback book) | Schwab serves it (0.25 s, not delayed), not used | `lefteye_fetcher.chain_snapshot` | **include on day one** (Will's go) |
| Gamma history before 06-25 | paid vendor | ThetaData | later |
| VIX1D expected move | live, no history (since 09-28) | context $VIX1D | **include** + nightly close snapshot |
| Straddle expected move | live + history since 07-10 | diary `range_ruler` | **include** |
| 0DTE gamma and volume | recorded since 07-13 | diary | **include** |
| /ES overnight shape | recorded since 03-05 | overnight store | **include** |
| Order-flow imbalance (SPY / ES, 1-min) | streamed but thrown away | lob-flow daemon memory | later (daemon change) |
| 0DTE tape flow | recorded | agg folds | **include** (`flow` block) |
| Bar-based flow proxy | computable | — | **exclude** (it copies the price move) |
| Nikkei | Schwab serves it, not recorded | $N225 | **include** (daily close; check for a 15-min delay) |
| /NKD | Schwab serves it | — | exclude (too thin) |
| Europe (CAC 40, DAX) | Schwab serves it, not recorded | $FCHI, $DAX | **include**, frozen at 11:30 |
| Europe ETFs before the open | barely trade | — | exclude |
| Korea semis | not available | — | exclude; EWY proxy later |
| Taiwan (TSM ADR) | Schwab serves it | — | later (needs /NQ overnight) |
| USD/JPY shock | Schwab serves it (/6J daily since 2016) | /6J | **include** |
| China / HK / CNH / KRW / EU yields | dropped by the map | — | exclude |
| LLM-read headlines | dropped by the map | — | exclude (titles logged for a later arm) |
| Own SPXW chain recorder | Schwab serves it | — | later (own job, avoiding :00/:30) |
| Past chains from Schwab | not available (HTTP 400) | — | exclude |
| Far-dated OI walls | recorded (latest copy only) | `dated_gex/book.json` | **include** + dated copy |
| 25-delta skew | computable (30-day tape) | `labels/vol_sources.py` | **include**; freeze history |
| ThetaData backfill (Value, $40/mo) | paid | — | later (Will's go) |

**Rules for every source**
- Zero Schwab calls at read time; the scene is built from saved files only.
- Never open a second Schwab stream (one per login, owned by lob-flow).
- Take the last good quote per symbol, because the :00/:30 rate-limit bursts empty whole snapshots.
- Move the three feeds that are always empty (`$ADD`, `$VOLD`, `$VOLSPD`) to the log, not the scene.

---

## 6. What was carried from SNDK, and why

| Carried over | Why (SNDK evidence) |
|---|---|
| Save the exact message **before** the call, with rules hash, builder hash, commit and prompt hash | 411 of 411 payloads join their read; a call rebuilt with later code differed in 175 places |
| Add `payload_id` and join on SPX's `read_id` (SNDK joined on timestamp only, and never stored outcomes) | SNDK could grade only 3.2% of calls, because nothing joined |
| Units and sign in every field name | 13 of 15 reviewers read SNDK's VWAP distance backwards |
| Missing data listed with a reason, never sent as null | v3 schema rule; labelling stale data was tried and ignored |
| Times from bars, as HH:MM + minutes ago | only 6 of 28 hand-built times were right before this rule |
| Stale blocks deleted, not labelled | SNDK found the labels ignored |
| One fact in one place | sr-8 cut 30 duplicated fields |
| One object per row | the column layout took 48 s against 15 s |
| No verdicts and no other voice's answer in the scene | handed-in regions: 34 of 34 clusters drawn on them; a handed-in arrow anchored Claude |
| Pin model, effort and era; bump the era only between sessions | 15 eras, 6 never live; a global `xhigh` effort caused 72–121 s calls and timeouts |
| Code checks every claim, and deletes rather than rewrites | Claude's "quiet" claims were wrong on 14 of 17; 33% of replies had something deleted |
| No example numbers in the rulebook | half of SNDK's size calls were exactly 0.15σ |
| The OUTPUT block wording is the strongest lever, so write it carefully | Step-3 A/B: 59 → 34 words, and it held live (36) |
| A row on every scheduled slot | SNDK writes quiet and paused rows; SPX skipped 10-09 09:30 without a trace |
| A fixed schedule, not a wake gate | the wake reason predicted the next move, which would bias what gets graded |
| Log each call's bill from the CLI's reply | the pre-measurement estimate was 10× too low |
| A decoy arm (outcomes shuffled) | planned in `docs/sndk-plan.md`, never built |

**Left behind:**
- the anti-forecast framing of DOCTRINE_V2;
- the Haiku prose reviewer (reasons here are field paths, not prose);
- the wake gate;
- rows that copy the last reading forward;
- the side-1 packet, which no model reads.

---

## 7. Output contract: Claude's percentages

**What Claude returns:** one JSON object.
1. `matched`: 1–4 past cards with whole-number shares summing to 100. Each names the fields it shares with now and the one field that most separates it.
2. `reasons`: 2–4 pointers to scene fields. Each says what the field pushes (up / down / move / still) and for which horizon.
3. `against`: the strongest fact against its lean.
4. One object per horizon asked (h30, h60, to_close). Each holds up, flat and down as whole numbers summing to 100, plus `up_size [U1, U2, U3]` and `down_size [D1, D2, D3]`. Together these give a 7-bucket distribution.

**Where the format lives**
- The template sits in the **cached rulebook**, with placeholders only and no numbers. It is shown in the mockup under `rulebook_output` for review.
- The per-read `ask` carries only the horizons, their edges and the key order. s1 is up-first; s2 is down-first with the cards reversed.
- The rulebook is order-neutral ("in the order ask.order gives"), so s2's order holds.
- Draft rulebook: `W/rulebook_draft_cr-1.txt`, 3,255 characters, sha `c4dc8ed4e807ce4f`.

**What code does with each reply (delete, never rewrite)**
- Parse the first JSON object of the CLI's `result`. `--json-schema` is not used, because it routes through a hidden tool with retries.
- Shares off 100 by 2 or less are rescaled. Off by more, that horizon is "malformed", stored, and scored as the base rate.
- Size splits that are off by 1 are fixed on the largest bucket. Off by more, the split is dropped and the three-way answer kept.
- Match ids must be real cards and not "now". Reason paths must exist in the scene and must not be listed under `absent`. Failures are deleted and logged. If no reason survives, the read is flagged `ungrounded`, still graded, and reported apart.
- The two samples are averaged per horizon. The gap between them is stored as the noise floor.
- Logged and never shown to Claude:
  - `kl_vs_base_rate` and `sat_on_base_rate` (every horizon within 3 points of the base rate);
  - `leans_with_m30`;
  - overlap with code's top 3 matches;
  - a `clamped_10` shadow: base rate ±10 points in Claude's direction.

**Illustrative reply for the 10-09 14:30 read.** For Will only; never in a prompt.
```json
{"matched": [{"id": "pd8d7b", "share": 45, "same_on": ["range_pos", "price_minus_flip", "gamma"], "differs_on": "range"},
             {"id": "p0ee3a", "share": 30, "same_on": ["price_minus_flip", "vix_vix3m", "price_minus_vwap"], "differs_on": "range"},
             {"id": "p7ebc0", "share": 25, "same_on": ["range_pos", "price_minus_open"], "differs_on": "m60"}],
 "reasons": [{"field": "tape.rv30_vs_clock_x", "pushes": "still", "for": "h30"},
             {"field": "options.regime.0dte_oi", "pushes": "still", "for": "all"},
             {"field": "scale.straddle_used_x", "pushes": "still", "for": "to_close"}],
 "against": {"field": "flow.lean_30m_vs_usual", "pushes": "up", "for": "h30"},
 "h30": {"up": 10, "flat": 70, "down": 20, "up_size": [7, 2, 1], "down_size": [14, 4, 2]},
 "h60": {"up": 14, "flat": 68, "down": 18, "up_size": [11, 2, 1], "down_size": [12, 4, 2]},
 "to_close": {"up": 8, "flat": 72, "down": 20, "up_size": [7, 1, 0], "down_size": [14, 4, 2]}}
```

The actual result was flat on all three horizons (+0.22, +0.52 and −0.14 edges). Log loss, floored at 0.02 as in `spx_jev/scores.py`; lower is better:

| | this reply | base rate | clock odds | Pool 2 |
|---|---|---|---|---|
| h30 | 0.357 | 0.517 | 0.425 | 0.484 |
| h60 | 0.386 | 0.460 | 0.391 (end-price odds, average-price label) | 0.412 |
| to_close | 0.329 | 0.405 | none exists | none exists |

**Note:** the shown base rate (50 sessions at this slot, 60% flat) was a weaker prior than the clock odds (about 65% flat) on this read. One read proves nothing, but it is why the `no_base_rate` arm matters.

---

## 8. Storage: payload + prediction + actual result

**Keys**
- `read_id` is SPX's own `live:<row_ts>` and the primary key everywhere. Seed rows use `seed:<slot_ts>`, so the two can never collide.
- `payload_id` is the sha256 of the canonical scene: keys sorted, compact separators, and **precedent rows sorted by id**. That makes s1 and s2 share it even though their card order differs.
- `prompt_sha256` is stored per sample.
- `slot` is `row_ts` rounded to the nearest :00 / :30.
- Never join on `row_ts` strings across stores, because their formats differ.

**Files (append-only, one set per day)**
1. `payloads/{day}.jsonl`, Part 1. Written **before** any call, with:
   - `status`: built, blind, refused or paused;
   - the scene, the ask and `prompt_head`;
   - hashes: rules, builder, commit, events;
   - model, effort, CLI version, `date_shown`;
   - `precedent_set` (card id, read_id, code distance, code rank);
   - `feeds`, including `always_empty`;
   - `quality_flags`: `anchor_src`, `anchor_vs_vix_x`, `unit_suspect`, `code_source`, `flow_src`, `no_vwap_before_08-11`, `calendar_built`;
   - `logged_never_sent`: spot, sig in points, edges in points, headline ids.
2. `reads/{day}.jsonl`, Part 2. One line per sample, written as soon as it lands: raw reply (up to 4,000 characters), parsed reply, checks, 7-bucket vectors, cost, latency, error. Then one **voice** line: averaged probabilities, gap between s1 and s2, `kl_vs_base_rate`, flags.
3. `outcomes/{day}.jsonl`, Part 3. Sealed at 17:15 ET and keyed on (`read_id`, `payload_id`, `rule_version`). It holds:
   - per horizon: minutes, edge in points, average move, edges, bucket, best and worst;
   - `bars_sha` and `close_src: official_close`;
   - **the baselines' probability vectors** (base rate, clock odds, Pool 2, `knn_code`, `clamped_10`), so everything can be re-scored later;
   - scores.

   Pool 2 and clock odds are copied from `raw/new_voice_forecasts` at the nightly run, not at spawn time.
4. **Coverage lines** for any of the 13 slots with no payload, for example `{"kind": "missing", "slot": "09:30", "why": "spx read skipped: diary had no row yet"}`.

**Write order during a live read (nothing waits on Claude)**
1. The SPX read appends its archive line, then calls `spawn_claude_forecast`, which starts a detached process and returns at once.
2. Gate. If the switch, a pause, a cap, stale bars or diary, or a lapsed login stops the read, a `blind` or `paused` line is written and nothing else happens.
3. Build the scene from saved files cut at `row_ts`, then run the leak checks. A failure writes `refused`.
4. `keep_payload`: write `rules/<sha>.txt` once (temp file, fsync, replace), then append the Part 1 line.
5. Call s1, then s2. On the first read of the day s2 waits for s1 so it hits the cache; after that they run in parallel. Each sample line is appended as it lands.
6. Write the voice line, then replace `latest.json` atomically.

**Grading join:** `read_id` + `payload_id` + the newest `rule_version`. h30 and h60 also match `integral_grades.jsonl` on `read_id`, with `next_30` ↔ h30 and `next_60` ↔ h60.

**Rules**
- Payload and outcome lines are never edited. A grading change appends a new `rule_version`.
- Any change to the rulebook, builder or payload bumps the era, between sessions only.
- **Retention:** payloads, replies, outcomes, rules and recorder output are kept forever (about 0.3 MB a day). The library parquet is rebuildable. Run logs are pruned after 30 days.

**Seed (`seed.py --from 2026-07-20`)**
- The cut is the diary row within ±2 minutes of each :00 / :30, which is where live reads cut. No such row means a blind slot.
- Outcomes come from the **same live grade functions**: the read's own price, the average price from the read minute, the official close, strict boundaries.
- Rows on unit-suspect days are kept out of base rates and precedents.
- Measured at 14:30:
  - 58 bar-days, of which 3 are blind (07-23, 08-14, 08-24) and 5 unit-suspect (08-17, 09-09, 10-06, 10-07, 10-08), leaving **50 sessions**;
  - precedent pool: 148 rows from 50 sessions (slots 14:00, 14:30 and 15:00, native book).

---

## 9. Grading and scoreboard entry

- **Scores per horizon**, through the station's own `spx_jev/scores.py` (`floored`, `log_loss`, `losses`): three-way log loss (floor 0.02), Brier, and ranked probability score over the 7 buckets.
- **Skill** uses `scoreboard.py`'s formula: 1 − mean penalty ÷ reference penalty over paired reads, with a 90% range from resampling whole days.
- **Baselines**, all scored on the same outcome line:
  - `base_rate`, the counts Claude was shown;
  - clock odds on the average price, with h60's own average-price odds counted nightly (today's stored h60 odds are end-price odds);
  - Pool 2, for comparison only;
  - `knn_code`: (counts of the 10 cards + k × base rate) ÷ (10 + k);
  - the `clamped_10` shadow, and later a Platt shadow after 150 graded reads.
  - To the close, only the base rate is a reference.
- **Missing, malformed, blind or failed horizons are scored as the base rate** (zero skill) in the headline. Coverage is reported separately, so failing on hard reads cannot flatter the score.
- **Effective sample per horizon:** h30 about 13 a day, h60 about 6.5 a day, to the close about **1 a day** (all of a day's to-close reads share one closing price).
  - Telling 55% from 50% needs about 780 h30 reads, roughly 60 sessions.
  - Checkpoints at 150, 300 and 600 h30 reads.
- **Scoreboard entry:**
  - its own tile ("Claude forecast", never the headline; the combined forecast keeps the lead);
  - listed in the graded-reads list with its own skill vs base rate and vs clock odds;
  - kept out of Pool 2 by `NEVER_MIXED`;
  - phone tile checked at 360 px.
- **Never in Pool 2:**
  - The reader never writes to `S/spx_jev/mirai_prediction/raw/new_voice_forecasts/`. `pool_v2.add_missing_voices` would hand any new voice a weight, and `nightly_job.py:40` filters out only `pool_v2` and `pool_v1`.
  - A test asserts the package never imports or writes that path.
- **Nightly arms**, one per read in rotation, on frozen payloads; results go to `arms/` only:
  - decoy (outcomes shuffled among the cards);
  - `no_base_rate`;
  - blind (no precedents);
  - `five_sample`.
- **Watchdogs:**
  - leans with the last 30 minutes;
  - copies the nearest card;
  - `sat_on_base_rate` share;
  - up share vs realized up share;
  - sum failures;
  - ungrounded share;
  - s1–s2 gap;
  - latency;
  - cost.

---

## 10. Operations

- **Model and effort:** `claude-opus-5-5`, with `--effort medium` **always passed explicitly**. The global `effortLevel: xhigh` caused SNDK's timeouts. No fallback model.
- **Transport, day one:** SNDK's proven flags, with no `--json-schema`:
  - `claude -p --append-system-prompt <rulebook> --disallowedTools <all> --strict-mcp-config --mcp-config '{"mcpServers":{}}' --output-format json --effort medium --no-session-persistence`
- **Keeping the call clean:**
  - Run from an **empty folder outside the repo**, so no CLAUDE.md or auto-memory rides along.
  - `--bare` (CLI 2.1.296) skips both, but it needs an API key rather than the OAuth login.
  - Once per era, a canary call checks that the transcript holds no CLAUDE.md or memory attachment.
  - Append mode still shows today's date, which is logged as `date_shown: true`. That is harmless live; **`call.py` refuses any payload dated before 2026-07-01**, so pre-cutoff replays never happen.
- **Timeout and attempts:** 120 s, one attempt (SNDK's Sonnet p95 is 63 s; Opus is slower). Nothing waits on the call.
- **Volume:** 26 live calls a day plus about 13 nightly arm calls. Cap: 45 calls a day, plus a $ cap in `control.json`.
- **Billing: the Claude subscription only, never the API.** Calls go through `claude -p` on Will's claude.ai login, the same way SNDK's do (`claude auth status` showed `authMethod: claude.ai` on 10-09). They cost nothing extra but draw on the same usage limits as Will's own Claude use.
  - `call.py` refuses to run if `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set in its environment, because the CLI would then bill the API instead.
  - It calls the real binary (`~/.local/bin/claude`), never the interactive shell alias.
  - `--bare` is never used, since it needs an API key.
  - When the usage limit is hit, the read is written as `blind` and nothing is retried.
  - Keep claude.ai's "extra usage" turned off, so hitting the limit can never turn into a bill.
- **No keys anywhere.** No key, token or account id goes into the payload, the reads or outcomes files, the specs or the logs. Market-data secrets stay in macOS Keychain, as `runtime/scripts/env.sh` already does. `state/` is git-ignored, and the repo's pre-commit scrub must pass before any commit.
- **Cost at list prices** (Opus 5.5: $4 input, $20 output, $8 for a 1-hour cache write, $0.20 cache read per MTok):
  - about $0.12 per warm call;
  - about $0.38 for the first call of the day;
  - **about $5 a day**, roughly $110 a month.
  - Sonnet 5 would be about $0.06 a call, roughly $2.60 a day.
  - Check against the first day's logged bills. Through the subscription login the CLI's dollar figure is notional.
- **Kill switch and pauses:** `SPX_CLAUDE_FORECAST_DISABLE=1`, or `control.json` paused. Three failures in a row pause the reader and send an ntfy page (the `sndk_deadman` pattern).
- **Schwab weekly login:**
  - The next lapse is about **10:24 ET on Tue 10-13** (login age was 3.27 days at 16:50 on 10-09). Reads in a lapse are written as blind.
  - The payload carries `schwab_login_days_left`.
  - The auth-watch warning fires only about 2.4 hours before a lapse; moving that threshold earlier is a decision for Will.
  - Re-login with `iv_fetcher.py --reauth`.
- **Schwab rules:** the builder makes zero Schwab calls at read time. Recorder jobs use the shared `spx_jev/schwab.py` client and stay clear of :00 / :30.
- **Commits:** on main, with `TZ=UTC`. Ask before any branch. Other Claude sessions share the working tree, so propose before editing.

---

## 11. Pressure-test fixes applied (what changed since the last draft)

| Problem found | Fix | Visible in the mockup |
|---|---|---|
| The CLI adds CLAUDE.md, memory and today's date to every call | empty working folder, canary check, `date_shown` logged, pre-July payloads refused | `contract.call` |
| r1 / r12 / rROD could be read after the cut | newest finished bar ≤ cut, with `to`; absent with "target minute not finished"; 10-07 15:29:17 test | `close_signals.r1.to` |
| Seed rows and backfilled code words used breadth corrected after the session | internals from the archive's live `market_context`; backfilled code words dropped; `code_source` flag | `quality_flags.code_source` |
| "Latest" files are overwritten after the close | read only timestamped rows ≤ cut; dated book copies keyed by `as_of`; test of banned sources | `dated_book.as_of` |
| Same-day closes leaked into base rates | `daily_closes.load(before=day)` everywhere; gap-fill recomputed per day (536 gaps, 58.0%); pension rank 53 of 119 | `open_signals.gap`, `calendar.month` |
| Replays from before Claude's training cutoff | refuse session dates before 2026-07-01 | contract note |
| Archive day files mix grades in with reads | filter `kind == "read"`, live lane, exact `read_id`; take only `market_context` | — |
| A later arm showing "whole" past payloads | only the past scene, put back through the leak checks | — |
| Event calendar built after the fact | `events_sha` per payload; seed flag `calendar_built: 2026-09-27` | `contract.events_sha` |
| Mockup mixed two diary rows | uses the read's own 14:30:12 row: net gamma 36.3 / 82.9 bn, IV 8.44%, age 0 s | `options`, `scale` |
| sig drifted away from the grader's unit | always the morning anchor; `sigma_live_x` shown (0.55); check edge ÷ (0.07 × 0.5918) = anchor | `scale.sigma_live_x` |
| Tape outages pass an age-only gate | content gate: trades > 0, signed share present, 0.3–3× the usual count at that minute | `data_sources.tape` (0.87×) |
| Seeded flow can't be rebuilt from raw tape | seeds take agg folds only; `flow_src: agg_live` | `quality_flags.flow_src` |
| Stand-in book hits fields outside `options` | book read per read; book-dependent fields dropped; Schwab $SPX chain fallback moved to day one | §4 special reads |
| Non-native anchors inflate outcomes | `anchor_src`, `anchor_vs_vix_x`; 5 unit-suspect days kept out (flagged by source: the ratio alone would also drop 3 real native days at 0.74–0.79) | `base_rate.sessions` 50 |
| The "09:32" read is really a 09:30 read | open-sensitive fields absent before 09:31 | §4 special reads |
| Seeds used stale diary rows | freshness gate: ±2 min of the slot, else blind | 3 blind days at 14:30 |
| Rate-limit bursts empty a quote line | last good quote per symbol; always-empty feeds moved to the log | `failed: []`, `always_empty_feeds` |
| Two different "opens" for the gap | `print_pct` and `settled_pct` named apart; fill rate looked up on the print gap | `open_signals.gap` |
| To-close graded on the last bar | official close (7811.54, not 7811.09) | `close_src` |
| `typical_30m_move_sig` was a constant | deleted | — |
| Login days measured at 08:00 | measured at the cut: 3.83 | `data_sources` |
| USD/JPY taken from spot | /6J: +0.26 | `foreign` |
| VWAP had one-row spikes | median of 3 rows | `price_minus_vwap` |
| Ambiguous names | `_usd_bn_per_1pct`, `at_prior_close_0_7dte`, regime from `gex_views` | `options` |
| ADD overclaimed | `add_live_approx` | `internals` |
| Pool counts taken before the gates | counted after them: 50 sessions | `precedents.pool` |
| Spawn placed inside a branch that almost never runs | spawn after `archive.append`, outside the `if` | §3 |
| Token budget 2× low | caps in characters; reply format moved to the rulebook | 9,076 characters sent |
| h60 clock odds mislabeled; no to-close odds | own average-price h60 odds counted nightly; base rate as the to-close reference | `sealed_outcome` |
| `--json-schema` isn't SNDK's transport | SNDK flags; first JSON object of the result; order-neutral rulebook | `rulebook_output` |
| Log-loss floor 1% vs the station's 2% | `scores.py`, EPS 0.02 | `log_loss_eps_0.02` |
| Skipped horizons flatter skill | scored as the base rate | §9 |
| To-close sample size overstated | effective n per horizon; to-close is about 1 a day | §9 |
| Seed outcomes built differently from live grading | live grade functions, strict boundaries, read price as the start | base rate counts regenerated |
| Baselines stored as scores only | probability vectors stored per read | `baselines_up_flat_down` |
| Slot naming and h60 edge | slot = `row_ts` rounded; h60 edge = factor(60) × 0.11 × anchor | `ask` |

**Rebuilt numbers for 14:30, all from days before 10-09:**
- Base rate: h30 [1, 4, 4, 33, 5, 2, 1], h60 [1, 1, 2, 35, 9, 2, 0], to the close [2, 2, 5, 37, 4, 0, 0].
- Code's shortlist, by horizon:
  - h30: 7 flat, 2 D1, 1 D2;
  - h60: 7 flat, 2 D1, 1 U1;
  - to the close: 10 flat.
- The actual result was flat on all three.

---

## 12. Open decisions for Will

| Decision | Options | Recommendation |
|---|---|---|
| **Conflict 1: base rate shown or hidden** | the map says show the analog base rate first; SNDK history says anything handed in anchors | **Show it**, scored so copying it earns zero. Run the `no_base_rate` arm nightly and watch `sat_on_base_rate`. Flip to hidden at 150 reads if over half the reads sit on it, or if the hidden arm scores better. |
| **Conflict 2: 2 samples vs 5–10 with the median** | the map rates "5–10 samples, take the median" moderate; 10 live samples would be about 130 calls and ~$15 a day on Opus | **2 live samples** (up-first and down-first, averaged), plus a nightly five-sample arm on 3 reads a day. Move to 5 live only if that arm wins at 150 reads. |
| **Conflict 3: abstain allowed, or code-only** | the map rates "abstain when unsure" moderate; SNDK's own "quiet" claims were wrong 14 of 17 times; skipping hard reads inflates skill | **Code-only.** No abstain key. Claude's "unsure" is answering the base rate, which code flags. Selective prediction is tested in the scorecard on the reads where Claude moved furthest from the base rate. Feed failures write blind rows. |
| Model | Opus 5.5 (~$5/day) or Sonnet 5 (~$2.60/day) | Opus 5.5; it copied the last 30 minutes less in the earlier 16-call bench (not re-run) |
| $ cap | — | $8 a day, 45 calls |
| Go for the `service.py` spawn line, the new folder and its jobs | — | yes, as one change |
| Recorder jobs (LETF, foreign, VIX1D close, dated book, flow-minute freeze, SPX 5-min save) | each adds Schwab calls outside the read | yes; the flow freeze and the 5-min save first (that data is disappearing) |
| Schwab $SPX chain fallback on day one | covers the 29% of reads on a stand-in book | yes |
| `--bare` with an API key vs isolated folder + OAuth | `--bare` bills the API directly | **decided: subscription only**, isolated folder + canary; never an API key |
| To-close flat band | keep the formula (about 66–76% flat by slot) or fit a band per slot to 55–60% flat | keep the formula for cr-1; revisit at 60 sessions |
| One-line adds to `daily_closes` ($VIX3M, $VIX6M, AGG, SPY) | outside the folder | yes, later |
| Paid backfill (ThetaData Value, $40/mo) | about 1,000 gamma sessions instead of 47 | after the first checkpoint |
| Earlier Schwab login warning | currently 6 days of age, about 2.4 h before a lapse | warn at 5 days |
| Extend `events.json` past 12-31 | otherwise every read in January says "uncovered" | yes, before December |

---

## 13. Milestones (build order, each with an acceptance check)

0. **Capture what is disappearing (recorder first).** The nightly flow-minute freeze (raw tape rotates; the oldest day is 09-10), saving Schwab SPX 5-min bars back to 01-22, dated `book.json` copies, the VIX1D close.
   *Accept:* files land each night; a test reads the previous day's files.
1. **Skeleton.** Folder, `paths.py`, `control.json`, kill switch, tests scaffold.
   *Accept:* the "never writes `new_voice_forecasts`" test passes; with the switch on, nothing is written beyond a `paused` line.
2. **Scene builder (18 blocks), cut at `row_ts`, allow-listed keys, leak checks.**
   *Accept:* rebuilding 10-09 14:30 reproduces the mockup scene exactly; each leak check is made to fail once:
   - a planted future row leaves earlier payloads byte-identical;
   - the 10-07 15:29:17 rROD case;
   - the 10-08 back-pull tape case (flow block dropped);
   - a stand-in day (book fields dropped);
   - a first read before 09:31;
   - banned "latest" files.
3. **Grader and bucket function** (reusing `spx_jev.integral`, `grade`, `scores`; official close; strict boundaries).
   *Accept:* matches stored `integral_grades.jsonl` on h30 and h60 for every live read 09-28..10-09; to-close on 10-09 gives −0.14 edges.
4. **Seed + library + base rates + shortlist.**
   *Accept:* the 14:30 base rate reproduces [1,4,4,33,5,2,1] / [1,1,2,35,9,2,0] / [2,2,5,37,4,0,0] from 50 sessions; the shortlist reproduces the 10 cards; no seed row is ever scored.
5. **Rulebook + `call.py` + `checks.py`.**
   *Accept:*
   - the canary transcript has no CLAUDE.md or memory attachment;
   - `prompt_sha256` rebuilds from the saved pieces;
   - a smoke test on 5 frozen payloads from after 07-01 parses, sums and grounds;
   - scene ≤ 9,500 characters.
6. **Hook + live (Will's go).** The one guarded line in `service.py`.
   *Accept:*
   - day one has 13 lines per day in `payloads/` (built, blind, or missing);
   - every built line has two sample lines;
   - the SPX read time is unchanged;
   - the kill switch works mid-session.
7. **Nightly job + scorecard.** No phone change here; the phone comes last, in milestone 10.
   *Accept:* integrity checks pass (every voice has a payload, every built payload has two samples, every outcome has a payload); `scorecard.json` written.
8. **Arms + watchdogs** (decoy, `no_base_rate`, blind, five-sample).
   *Accept:* arm results land only in `arms/`; the watchdogs show on the scorecard.
9. **Checkpoints at 150 / 300 / 600 h30 reads.** Decide on base-rate showing, sample count, model, and whether Claude has earned a Pool 2 trial (a separate decision).

---

10. **Phone view, last.** Once the build is running, mock up the SPX phone page (`jev-spx.html`) with the Claude read in place and every change highlighted, checked at 360 px. Build the tile only after Will approves the mockup.
   *Accept:* Will signs off on the mockup; the tile is built; the 360 px tests pass; nothing else on the page moves.

---

## 14. Risks

- **Small sample.** About 60 sessions to tell 55% from 50% on h30; to the close is one outcome a day. Early swings mean nothing.
- **Anchoring.** Claude may copy the base rate or the nearest card. Watchdogs and arms measure this; they cannot prevent it.
- **Own data runs against the research.**
  - r1/r12 agreement held on 13 of 41 days.
  - rROD under short gamma held on 7 of 23.
  - These are shown only as facts and past outcomes, never as rules.
- **Feed outages.**
  - The native options book was out for whole sessions (10-06, 10-07, most of 10-08).
  - The tape failed or arrived late at the open on 3 of the last 5 sessions.
  - Schwab login lapses blank everything.
  - Mitigation: blind rows, the content gate, and the Schwab $SPX chain fallback.
- **Unit drift.** Days with a non-native morning anchor distort outcomes measured in edges; they are flagged and kept out of base rates.
- **Context leaking through the CLI** (CLAUDE.md, memory, date). Mitigation: isolated folder and a canary each era.
- **Cost and latency on Opus.** Bills logged per call, caps, 120 s timeout. Nothing waits on the call.
- **Era sprawl** (SNDK ran 15 eras). Bump only between sessions, and compare only within comparable `builder_sha` and era.
- **Model availability.** Opus 5.5 is newly launched; the model id is pinned and there is no silent fallback.
- **Shared working tree.** Parallel Claude sessions edit the same repo, so build in the new folder and ask before touching anything else.
- **Calendar file ends 12-31.**

---

## 15. Files

- Final mockup (pretty, 219 lines): `W/payload_mockup.json`.
- Rendered image: `W/payload_v1/mock_final.png` (2004 × 6780). This step was read-only on `~/Desktop`, so the image still needs copying there for Will's review.
- Builders (read-only over the stores):
  - `W/prototype/fix_lib2.py`: base rate, seeds, precedents with the fixes;
  - `W/prototype/mock_final.py`: mockup, hashes, sizes.
- Draft rulebook: `W/rulebook_draft_cr-1.txt`.
- Key code read:
  - `P/skills/spx-jev/spx_jev/{scores.py, cuts.py, integral.py, grade.py, service.py}`;
  - `P/skills/sndk-pro/{sndk_read.py, sndk_board.py}`.
- Data read:
  - `S/reversion/2026-10-09.jsonl` (14:30:12 row);
  - `S/lob_flow/agg/*.jsonl`;
  - `S/spx_jev/daily_closes/{$SPX, $VIX, TLT}.jsonl`.
