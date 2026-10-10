# spx-claude-forecast storage design

Status: design only, written 2026-10-09. No files have been written yet, and every step below waits on Will's go.

Path shorthand used throughout:
- S = `~/.claude/plugins/mirai-station/state`
- F = `S/spx_claude_forecast`, which does not exist yet

## The answer

Every payload, every Claude reply and every result is kept forever in plain daily files that Claude never reads. Each night one small library file is rebuilt from them. At each half-hour read, code hands Claude a fixed block of history of about 930 tokens, made of two parts:
- the usual odds for that time of day
- 8 similar past moments, each with what happened after it

That block is the same size with 2 months of history or 5 years. More history makes the numbers inside it better, but the block never gets longer.

Claude's own track record is stored and scored from the first read and shown on Will's scorecard. It stays out of Claude's prompt in year one. At 13 reads a day, no test can tell a useful record from noise before about 100 sessions.

## Diagram

```
EXISTING FEEDS  (station stores, read-only to this project)
  S/reversion/   S/spx_jev/{bars,context,daily_closes,overnight,headlines,store}   S/lob_flow/agg
  S/spx_jev/mirai_prediction/   Pool 2 and clock odds, used only as comparisons
  AT RISK  dated_gex/book.json · siege/baseline.json · lob_flow/raw · Schwab 5-min history
        │                                   │
        ▼                                   ▼
KEPT FOREVER  (append-only, backed up off-disk nightly, Claude never reads it)
  F/recorder/   copies of the at-risk feeds                  ◄── START HERE
  F/payloads/{day}.jsonl ──read_id──► F/reads/{day}.jsonl
        └──────────────────────────► F/outcomes/{day}.jsonl   sealed no earlier than close + 75 min
  F/seed/   rebuilt reads 07-20 → 10-09        F/arms/   test replies + seed replay
        │ nightly, after the seal
        ▼
NIGHTLY LIBRARY  (two files, rebuilt whole in under 1 s)
  F/library/rows.parquet     one row per past read: market facts + what happened
  F/library/scores.parquet   one row per read × horizon × forecaster: the track record
  F/scorecard.json           Will's view of the track record, never sent to Claude
        │ each half-hour read, about 4 ms, only days before today
        ▼
WHAT CLAUDE SEES
  today's scene (~9k chars)  +  base rate (~100 tok)  +  8 past cases (~830 tok)
  = ~930 tokens of history, the same size forever
```

## What exists today (checked 10-09)

| Store | Size | What it holds | Lost over time? |
|---|---|---|---|
| `S/` total | 1.4 GB | everything | 326 GiB free on disk |
| `S/reversion/` | 536 MB | diary, 146 day files from 06-25 | kept |
| `S/spx_jev/` | 367 MB | `context/` 114 MB, `store/` 38 MB, `mirai_prediction/` 18 MB, `bars/` 0.7 MB, plus `daily_closes`, `overnight`, `headlines` | kept, but `spx_jev/store` is rebuilt whole every night over a 7-day window, so this project never writes there |
| `S/lob_flow/agg` | 48 MB | per-minute options-flow summaries the flow block reads | never deleted (`lob_flow/io_state.py:174`) |
| `S/lob_flow/raw` | 341 MB | raw options tape, 23 days from 09-09 to 10-09 | deleted after 30 days or at 500 MB; 09-09 to 09-11 go on Mon 10-12 |
| `S/siege/baseline.json` | 0.6 MB | SPY minute baseline, 59 days from 07-20 to 10-09 | keeps 60 days (`siege/contracts.py:103`, `effort.py:49`); 07-20 is deleted on the 61st day, Tue 10-13 |
| `S/dated_gex/book.json` | 0.2 MB | dated options book, `as_of` 2026-10-09T17:10 | overwritten on every fetch; the next one is Mon 08:17 ET |
| `F/` | none | nothing | does not exist |
| backup of `S/` | none | no Time Machine; the last tar is from 07-04 | |

## Ground rules

1. **Claude never reads raw history.** One full past read costs about 5.7k tokens and one day about 75k. SNDK's on-demand history tool was used 0 times in 391 calls and still added 48% to the token bill. Embedding search scored 0 of 8 on the test panel.
2. **The history block has a fixed size.** It has 1 base-rate row and 8 cards, and character caps are checked when the block is built. If it runs over, code first drops `vs_close` from the cards, then cuts the cards from 8 to 6. A row is never cut halfway.
3. **Point in time.** Everything Claude sees at a read on day D comes from earlier days, and results count only if they were sealed before D's open. Code enforces this at read time. It does not depend on file timing.
4. **There is one source of truth for success.** Nothing ever edits a line in `outcomes/`. `library/scores` is computed from those lines every night, and every scorecard number is a GROUP BY over `library/scores`.
5. **Count sessions, not reads.** Reads from the same day are near-copies, so counting reads overstates how much evidence there is. The overstatement factor is 1.45 at h30 on the seed, 2.85 at h30 live, and 4.6 at to-close.

## Folder layout

### Kept forever (Tier 0)

| Path | Holds | Written by, when | Size/day |
|---|---|---|---|
| `STORE_MAP.json` | every path with its writer, reader, retention and backup; a test fails if code writes a path not listed here | by hand | none |
| `control.json` | pause switch, daily call cap, $ cap | Will or CLI, atomic replace | none |
| `eras.jsonl` | one line per era: `era, start_day, end_day, rules_sha, builder_sha, fp_sha, prior_v, card_v, model, effort, model_cutoff, comparable_to[]` | nightly job, between sessions only | ~0 |
| `rules/<sha>.txt`, `events/<sha>.json`, `scalers/<fp_sha>.json` | rulebook as sent, event calendar as used, frozen field scaler | written once per new hash | ~0 |
| `payloads/{day}.jsonl` | one line per scheduled slot, 13 a day, including `blind`, `paused` and `missing`: the exact scene, the ask, provenance | the read hook, **before** any Claude call | 120–145 KB |
| `reads/{day}.jsonl` | 2 sample lines and 1 voice line per read | each sample as it lands, then the voice line | 70–150 KB |
| `outcomes/{day}.jsonl` | one line per `(read_id, horizon, rule_version, seal_seq)`: result plus comparison forecasts | nightly seal, refused before close + 75 min | ~17 KB |
| `arms/{day}.jsonl` | test-arm replies on frozen payloads, and the seed replay | nightly job | ~31 KB |
| `seed/payloads/<builder_sha>/{day}.jsonl`, `seed/outcomes/{day}.jsonl` | rebuilt scenes from 07-20 to 10-09: 767 slots, 663 built and 104 blind, 609 usable over 54 sessions | `seed.py`, once per builder version | ~7.3 MB per version |
| `recorder/dated_book/{as_of}.json` | each new `dated_gex/book.json`, with its full ISO `as_of` | whenever `as_of` changes | 0.1–0.2 MB per copy |
| `recorder/siege_spy_minutes/{day}.json` | that day's entry from `siege/baseline.json` | nightly 16:25 ET; all 59 days backfilled first | small |
| `recorder/spx_5m/{day}.jsonl` | SPX 5-minute bars from Schwab | one backfill to about 01-22, then nightly | ~10 KB |
| `recorder/vix1d_close.jsonl` | VIX1D close, one line per day | backfilled from `spx_jev/context` (from 09-28), then 16:25 ET | ~0 |
| `recorder/lob_raw/{day}/` | **optional**, verbatim gz copy of the raw tape | nightly 16:25 ET if Will says yes | ~10 MB |
| `recorder/letf/`, `recorder/foreign/` | LETF shares outstanding; Nikkei, CAC 40, DAX, /6J | only if those payload blocks ship | small |

### Rebuilt nightly (Tier 1)

| Path | Holds |
|---|---|
| `library/rows.parquet` | one row per past read, seed and live; on the same day and slot, live wins. Holds market facts and outcomes only, with **no Claude columns**, so the card builder cannot show Claude's past calls |
| `library/scores.parquet` | the track record: one row per read × horizon × forecaster × arm × rule_version |
| `library/manifest.json` | `built_at`, `source_max_day`, file shas, row counts, torn-line and quarantine counts |

Each night the job rebuilds both parquet files from Tier 0 in under 1 s. The analysis queries in `q.sql` run directly against them.

### Ops

`latest.json` is the phone tile and `scorecard.json` is Will's view; both are replaced atomically. Alongside them sit `health.json` for auth-watch and ntfy, `locks/`, and `run_logs/`, which are pruned after 30 days.

### Totals

| What | Per day | Per year |
|---|---|---|
| Tier 0 without the recorder | 0.25–0.35 MB | 65–90 MB |
| Recorder without the tape copy | under 1 MB | |
| Recorder with the tape copy | ~10 MB | ~2.5 GB |
| Library | | under 5 MB |

Disk space is not a limit.

### Cut from the first draft, and why

- **`cache/{day}/`** (pool, base rates, manifest). It existed only because a day-partitioned library took 58–63 ms to read, which is over the 50 ms budget. Tested on 1.1 years of simulated rows (3,654), one parquet file is 114 KB, reads in 1 ms, and finds the 8 nearest cards in 3.5 ms.
- **`library/*/day=*/` partitions, `forecast.duckdb`, `validation.jsonl`.** A whole rebuild takes under 1 s, and the validation counts go into the manifest.
- **`memory/`, `pack.json`, `casebook/`.** See the track record section.
- **The `recorder/flow_minutes` builder.** The flow block reads `lob_flow/agg`, and that is never deleted.
- **The `base_rate_slot` and `clamped_10` comparison refs.** They were kept only for comparison with the mockup and add nothing.

## Keys

| Key | Form |
|---|---|
| `read_id` | `live:<row_ts>`, the same as the `spx_jev` archive and `store.average_grades.read_id`, for example `live:2026-09-28T10:30:46.451741-04:00`. Seed reads use `seed:<day>T<slot>`. It is the primary key everywhere; never join on a bare `row_ts` |
| `payload_id` | `sha256:` + 16 hex of the canonical scene (sorted keys, compact, cards sorted by `read_id`). Both samples of a read share it |
| `sample_id` | `<read_id>#s1`, `#s2` or `#arm:<name>` |
| `session_idx` | trading-day number from `sessions.py` |
| `era`, `schema` | for example `cr-1` and `spx_claude_payload/1.0.0`. Readers refuse an unknown major version |
| versions | `fp_sha` (matcher), `prior_v` (base rate), `card_v`, `cells_v`, `rule_version` (grader), `score_v` (scorer), `unit_suspect_v`, `events_sha`, `library_sha` |
| `origin` | `live`, `seed`, `rebuilt_live` or `bars_5m`. Seed rows are never scored as Claude |

## Record shapes

### Payload line (`payloads/`)

- **Fields from the plan.** `kind, schema, era, read_id, payload_id, day, slot, origin, status` (built, blind, refused, paused or missing), `why, cut_at, built_at, built_lag_s, scene, ask, prompt_sha256` per sample, `rules_sha, builder_sha, events_sha, git_commit, call{model, effort, cli_version}, feeds{}, quality_flags{}, logged_never_sent{}`.
- **Added for history.** `fp_sha, prior_v, card_v, cells_v, library_sha, library_built_at, library_source_max_day, library_stale_sessions, precedent_set[{alias, read_id, dist, code_rank, out_rule_version}]`.
- **Added for scoring.** These are frozen at build time and never recomputed.
  - `base_rate_shown{h30[7], h60[7], to_close[7], sessions, n_eff}`
  - `sit{slot, phase, gamma, book, event, trend, range_pos, vix_term}`
  - `unit_suspect_v`
  - `event_coverage`
  - `book_as_of` (full ISO)
- **Scene change.** Add `ruler` to the `scale` block. It comes from `anchor_vs_vix_x`, which today exists only in `quality_flags`.

### Reads (`reads/`)

- **Sample line.**
  - Identity: `sample_id, read_id, payload_id, era, order`.
  - Reply: `raw` (at most 4,000 chars), `parsed`, `checks{deleted[], rescaled, malformed[], ungrounded}`.
  - Forecast: `p7{h30,h60,to_close}`, `matched[]{read_id, share}`.
  - Cost: `cost_usd, tokens{in, cache_read, cache_write, out}, num_turns, latency_s, error`.
- **Voice line** (`arm: prod`).
  - Per horizon: `status, p3, p7, s1_p3, s2_p3, s_gap, kl_vs_br, sat_on_br`.
  - Per read: `grounded, leans_with_m30, top3_overlap, n_samples_ok`.
- **Arm lines** have the same shape, plus `arm, arm_v, arm_payload_id`.

### Outcome line (`outcomes/`)

There is one line per `(read_id, horizon, rule_version, seal_seq)`, and lines are never edited. A `no_data` horizon that is sealed later gets a new `seal_seq`, and a regrade appends a new `rule_version`.

- **Identity.** `read_id, payload_id, horizon, rule_version, seal_seq, sealed_at, day, slot, session_idx, era, origin`.
- **Result.** `status` (sealed, no_data or not_asked), `minutes, from_price, edge_pts, g_pts, edges, label3, bucket7, best_edges, worst_edges, bars_sha, close_src`.
- **`refs`** are copied at seal time, so later rebuilds cannot change them:
  - `base_rate_shown`, copied from the payload.
  - `clock` at h30 is pinned to `average_30` from `store.calls.clock_probs`. At h60 it is labelled `clock_endprice`, because only `next_60` exists.
  - `pool_v2` comes from `raw/new_voice_forecasts`, only when `source == "live"` and `created_at − row_ts ≤ 600 s`. The line stores `ref_src` and `ref_created_at`. Rows from 09-29 to 10-06 are replays written 1.4 to 8.7 days late and never count.
  - `knn_code` is a tally of the outcomes printed on the 8 shown cards, pulled toward the base rate with k0 = 20. It is never rebuilt from `precedent_set` against the library.
  - `ref_missing{name: reason}`.
- **`cross_check{integral_grades_outcome, agree}`** for h30 and h60.

### `library/rows.parquet`

- **Keys.** `read_id, payload_id, day, slot, slot_min, session_idx, origin, era, fp_sha, rule_version, sealed_at, seal_seq, schema`.
- **Gates.** `status, book, unit_suspect, unit_suspect_v, manual_exclude, stale, anchor_src, event_coverage, eligible_precedent, eligible_base_rate, exclude_reason`.
- **Groups.** `phase, gamma, day_t1, release_ahead_kind, trend3, pos3`.
- **Fingerprint.** All plan fields are kept as float32 columns.
  - Distance `fp-1` uses `gap, vs_close, vs_flip, range, vix_term, ruler`, plus 1 for a gamma mismatch and 1 for a `day_t1` mismatch, then takes the square root.
  - The stand-in book drops gamma and `vs_flip`.
  - If `day_t1` is unknown, the distance skips that term.
- **Outcomes.** `out_edges_h, out_bucket_h, out3_h` for each horizon.
- **`card_json`.** Pre-rendered without `ago`, about 157 chars.

### `library/scores.parquet`

- **Columns.**
  - `read_id, payload_id, day, slot, session_idx, era, origin, arm, rule_version, horizon`.
  - `voice`: claude, claude_s1, claude_s2, base_rate, clock, pool_v2 or knn_code.
  - `status, label3, bucket7, p_up, p_flat, p_down, conf`.
  - `ll3, move_loss, dir_loss, brier3, rps7, right3, score_v, sit_*`.
- **Scoring.** Scores come from `spx_jev/scores.py` with a floor of 0.02. A missing or malformed Claude row scores as the base rate, which means zero skill, and coverage is reported separately.

## What Claude sees per call

| Block | Where in the prompt | Size | Hard cap |
|---|---|---|---|
| `base_rate` | in the scene, fresh each read | ~100 tok | 260 chars |
| `precedents`: a "now" row plus 8 cards | in the scene, fresh each read | ~830 tok | 1,700 chars |
| **Total history** | | **~930 tok** | **~1.07k tok** |

This costs about $0.10 a day for 26 calls at $4 per million tokens.

### Base rate

- **How it is built.**
  - Pool the read's slot with the half-hours on either side (±30 min).
  - Give one vote per session.
  - Use all history with no fading.
  - Show `sessions` and `n_eff`.
  - Under 15 sessions, fall back to the exact slot and flag it.
  - At 15:30, `h60` is null.
- **Why pool the neighbouring half-hours.** It beats the exact slot in every test:

  | Test | h30 | h60 | to-close |
  |---|---|---|---|
  | Efficiency check | +1.7% | +0.7% | +1.0% |
  | Retrieval team | +2.0% | +1.2% | +1.4% |
  | Memory team | +1.9% | +1.6% | +1.3% |

- **Why use all history.** Using only the last 10, 20 or 30 sessions does worse:

  | Window | h30 | to-close |
  |---|---|---|
  | Last 10 sessions | −2.2% | −7.8% |
  | Last 20 sessions | −0.9% | −4.1% |
  | Last 30 sessions | −1.0% | −3.3% |

  A weekly drift alarm pages Will.
- **Later.** Add `origin=bars_5m` rows from the Schwab backfill, about 120 more sessions, for the base rate only.
  - These rows have no options data, so each move is sized with the VIX-implied move × 1.06, the median `anchor_vs_vix_x`.
  - On seed days that stand-in gives the same up/flat/down label as the real anchor 94% of the time at h30, 92% at h60 and 94% at to-close.
  - Turn it on only after a test that predicts later days from earlier ones shows a gain, then bump `prior_v`. It adds zero tokens.

### The 8 cards

> **Will's change (10-09), which overrides the matching rules below.** Rare, one-off items are not matched on. The release-type filter (step 4) and the big-release-day term are dropped. Cards are matched only on the everyday items that every payload carries:
> - opening gap;
> - move since yesterday's close;
> - last 30 minutes;
> - range so far;
> - spot in the day's range;
> - distance from VWAP;
> - VIX ÷ VIX3M.
>
> Each difference is divided by that item's usual day-to-day spread, a scaler frozen per fit day. The standardized differences are combined into one root-mean-square distance, and the smallest win. There are no embeddings and no cosine similarity. The two remaining filters are earlier days only and the same time of day ±45 min. Book-dependent items (flip distance, dealer stance) are left out of the distance.


- **What a card shows.** Each card has 9 fields: alias `id`, `ago` in sessions, `at`, `gamma`, `gap`, `vs_close`, `vs_flip`, `range`, `vix_term`, `ruler`, and `out[h30,h60,tc]` in edges clamped into their bucket.
  - The header reads `"examples, not odds; use base_rate for odds"`.
  - Sample s1 sees the cards shuffled; s2 sees them reversed.
- **How cards are picked.** Code applies these filters in order:
  1. `day < D`, and the outcome was sealed before D's 09:30 open under the `rule_version` in force on D. For each read and horizon it takes the newest `seal_seq` that qualifies. Same `fp_sha`. Never an arm row.
  2. `status = built`, not unit-suspect, not stale.
  3. `|slot_min − now| ≤ 45`.
  4. Release partition, which is provisional because it rests on only 11 sessions. If a release is still ahead, draw from same-class rows, falling back with an `ev` tag when fewer than 5 sessions qualify. Otherwise leave out release-ahead rows. Rows with incomplete event coverage form their own bucket.
  5. Rank by distance, then by newer day. Take at most 1 per session until 8 are picked. If fewer than 5 qualify, the block is left out with a reason.
- **What we know about their value.** The cards have not yet shown value at 30 and 60 minutes. Measured as log loss, nearest cards against random cards come out:

  | Horizon | Nearest vs random | 90% range |
  |---|---|---|
  | h30 | −1.0% | −2.8 to +0.7 |
  | h60 | −0.7% | −2.4 to +1.1 |
  | to-close | +1.8% | −0.7 to +4.2 |

  Just tallying the cards' outcomes as odds scores −16.8% at h30. The cards stay because they are the only place Claude sees what happened after moments like this one. The seed replay below decides whether they keep their 830 tokens.

### Never shown to Claude

- dates, weekdays, index levels, strikes, headline titles
- any result from today
- Claude's past prose or its past call on a card
- Pool 2 or clock odds, or Claude's skill against them
- watchdogs, situation tables, ledgers, arm results
- seed rows presented as Claude's record
- unit-suspect days

## Claude's track record

### Stored from read 1

The chain runs from the payload (`base_rate_shown`, `sit{}`) to the voice line, then to the sealed per-horizon outcome with all its `refs`, then nightly into `library/scores`, and from there into `scorecard.json`.

### What Will sees in `scorecard.json`

- per-horizon skill against `base_rate_shown`, with a 90% range from resampling whole days, never a raw "% right" (the flat share alone runs from 30% at 10:00 to 78% at 13:30)
- calibration and coverage
- watchdogs: sat on the base rate, copied the top card, leaned with m30
- skill against Pool 2 and clock odds
- "Too early" under 100 reads
- situation cells only once they have 20 or more sessions and survive a correction for testing many cells at once

### What Claude sees about itself in year one

Nothing. The evidence says a digest would be noise:

- **Cells.** A stand-in forecaster with no real skill produced 8 "significant" cells out of 74, and chance alone gives about 7.4.
- **Calibration bands.** A band of 20 reads from 5 sessions can sit anywhere within ±20 points of its true rate.
- **The test arm.** At about 1.6 reads a night, the `with_record` test is known only to ±8.2 points after 10 sessions, and still to ±2.6 points at 100 sessions. The draft's gate ("upper end below +2%") could not pass, so its earliest date of 11-06 was never reachable.
- **The casebook.** 261 candidate facts gave 11 at 2σ, where luck alone gives about 13, and none held up on later data.

### When to revisit

At 100 live sessions, about early March 2027. Test a digest of at most 1,200 chars as a paired arm. If it helps, add `memory/<sha>.txt` and stamp `memory_sha` on every payload. Nothing in this layout blocks that.

## Point-in-time rules, enforced in code

1. **Read-time history.** Rule 1 of the card filters applies to every row the read-time builder touches. Every payload stores `library_sha` and each card's `out_rule_version`.
2. **Sealing.**
   - Sealing day D is refused before close(D) + 75 min.
   - `locks/nightly.lock` stops two runs at once.
   - Live reads end by about 15:35, so they never overlap the 17:15 job.
3. **Arms.**
   - Arm builders read only the frozen payload, never `library/`.
   - Read times drift around the half hour, and on 9 of the 33 back-to-back pairs from 10-07 to 10-09 the earlier read's 30-minute window was still open at the next cut.
   - So if `with_today` ever runs, it may include an earlier same-day result only when `floor_min(src.cut) + h ≤ floor_min(this.cut)` and that result's last bar closed at or before the cut. To-close is never included.
   - Test: replay the 10-08 12:58:41 read and expect the 12:31:41 read's 30-minute result to be absent.
4. **Options book.**
   - Store the full ISO `as_of` and use the newest copy with `as_of ≤ cut`.
   - The 08:17 fetch failed and kept the previous book on 10-06, 10-07 and 10-08, so 10-08 reads really used the 10-05 book.
   - Test: a Friday read must not see the 17:10 copy.
5. **Siege SPY minutes.** The seed uses only days before D, never `BaselineStore.prior_days`. That function (`effort.py:52`) returns every day except the session itself, so later days would leak in.
6. **Unit-suspect days.**
   - One rule, `unit_suspect_v1(cut_row, first_row, prior_closes)`, uses only facts known at the cut and applies to seed and live alike.
   - The hand list `{08-17, 09-09, 10-06, 10-07, 10-08}` in `fix_lib2.py` becomes `manual_exclude{day, reason}`, and the scorecard reports where the list and the rule disagree.
   - 08-10 also opened on the stand-in book, with a ratio of 1.16, and is not on the hand list.
7. **Event calendar.**
   - `skills/spx-jev/calendar/events.json` covers in-session events from 09-01 and pre-open events from 08-01. 334 of the 609 seed rows (30 of 54 sessions) fall before 09-01.
   - Rows with incomplete coverage get `day_t1` and `release_ahead_kind` set to unknown.
   - Every calendar version is saved to `events/<sha>.json`.
   - Optionally, backfill the published agency calendars and bump `events_sha`.
8. **Model cutoff.** `model_cutoff` is stored per era, and any call or arm on a payload dated on or before it is refused.
9. **Scaler.** The scaler is fit on rows before the era starts. Seed reliability numbers are marked `scaler: full_sample`.
10. **Units.** Each field reads from one pinned source. For example, `$TNX` is ×10 in `daily_closes/$TNX.jsonl` (52.93) but in percent in `store/daily_closes` (5.293).

### Tests

- **Rebuild test.** Delete `library/`, rebuild it from Tier 0, then rerun the read-time builder for past reads. The output must be byte-identical to the stored `precedent_set` and `base_rate_shown`.
- **Leak test.** Plant a day-D outcome. Day D's output must not change.
- **Write guard.** The package must never open `spx_jev/**`, `lob_flow/**`, `siege/**`, `dated_gex/**` or `mirai_prediction/**` for writing. Any write to `mirai_prediction/**` would be picked up by Pool 2.

## Write protocol

- **Appends.**
  - Open with `O_APPEND`, take `flock(LOCK_EX)`, and write one whole line in a single `os.write`.
  - If the file does not end in `\n`, write one first, so a torn line is fenced off. This matters because s1 and s2 append to the same `reads/{day}.jsonl` from two processes.
  - `fsync` payloads and outcomes.
- **Replaced files.** Temp file, `fsync`, `os.replace`, then `fsync` the directory.
- **Content-addressed files.** Written once.
- **Order during a read.** Payload, then samples, then voice, then `latest.json`.
- **Stale library.** Use the newest library and stamp `library_stale_sessions`, and send an ntfy alert if it is above 0. Write `blind` only if no library exists.

## Arms: tests that decide what Claude sees

**Live.** At most 2 arms at a time, about 6 reads a night each, paired on the same payload. `blind` (no cards) and `random_cards` run first. `no_base_rate`, `slot_prior`, `decoy`, `with_today` and `five_sample` wait until after the first checkpoint. `five_sample` alone needs 15 calls a night, which is more than the whole budget of 13.

**Seed replay before launch.** This needs Will's yes, at about $157.
- **What runs.** The 436 seed reads that have at least 15 earlier sessions, in 3 versions (production, blind, random_cards) with 1 sample each. That is about 1,300 calls at $0.12.
- **Why it's worth it.** Paired over 39 sessions, it pins the difference to ±1.6 points. Ten live sessions would give ±8.2.
- **Why it's clean.** The seed starts 07-20, after the model's June 2026 cutoff.
- **Caveat.** Seed scenes are thinner. They have no VIX1D before 09-28, no headlines before 10-07, no code words before 10-08 and no VWAP before 08-11.
- **Rule set in advance.** If nearest cards do not beat blind by at least 1% at h30 and h60, drop the h30 and h60 outcomes from the cards and keep to-close, or cut to 4 cards (about 450 tokens).

## Start now

Everything here is new files under `F/recorder/` copied from existing stores. No existing file or job changes, and the recorder is a new folder plus a new launchd job. It still waits on Will's go.

| # | Deadline | New path | From | Why |
|---|---|---|---|---|
| 1 | before Mon 10-12 08:17 ET | `recorder/dated_book/{as_of}.json` | `S/dated_gex/book.json` (as_of 10-09 17:10) | overwritten on every fetch |
| 2 | before Mon 10-12 ~09:30 ET, **optional** | `recorder/lob_raw/{day}/` | `S/lob_flow/raw/` (23 days, 341 MB) | 09-09 to 09-11 rotate out Monday; Claude's flow block uses `lob_flow/agg`, which is kept forever, so this is insurance only (~2.5 GB/yr) |
| 3 | before Tue 10-13 | `recorder/siege_spy_minutes/{day}.json` (all 59 days) | `S/siege/baseline.json` | 07-20 is deleted on the 61st day, then about one session a day; it feeds `flow.spy_volume_30m_x` |
| 4 | as soon as possible | `recorder/spx_5m/` | Schwab 5-min history back to ~01-22 | loses about a session a day; feeds better time-of-day odds |
| 5 | soon | `recorder/vix1d_close.jsonl` | `S/spx_jev/context/` (VIX1D since 09-28) | never recorded as a close |
| 6 | with #1 | nightly off-disk copy of Tier 0 + recorder | | `S/` has no backup today |
| 7 | at go-live | `payloads/`, `reads/`, `outcomes/` | read hook and nightly job | the record itself |
| 8 | any time | `seed/` | `reversion/`, `spx_jev/{bars,daily_closes,overnight,store}`, `lob_flow/agg`, plus the siege copy from #3 | every other source is kept forever |

## Open decisions for Will

1. Go on `F/recorder/` items 1 and 3, which have Monday and Tuesday deadlines.
2. Tape copy, yes or no. It costs about 2.5 GB a year, and the deadline is Monday morning.
3. Off-disk backup target: iCloud Drive (`~/Library/Mobile Documents/com~apple~CloudDocs`, which exists and is empty) or an external drive.
4. Seed replay before launch, about $157.
5. Accept that Claude does not see its own record in year one, and revisit at 100 live sessions.

## Sources

- Plan: `~/.claude/plugins/mirai-station/skills/spx-claude-forecast/spec/build_plan.md` (storage at lines 72–84) and `spec/payload_mockup.json`.
- Team evidence and scripts: `/private/tmp/claude-501/-Users-will-Desktop-Mirai-Awakening/3ffd3e1c-b125-40a7-bbc5-e45838ebcfe6/scratchpad/`, in `retr2/`, `compress/`, `evalds/` (`q.sql` holds the scorecard queries), `ltm/`, `effcrit/a1.py`–`a6.py` and `seed_rows.json`.
