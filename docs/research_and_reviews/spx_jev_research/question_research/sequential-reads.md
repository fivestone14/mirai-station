# Sequential reads before the open (SPX JEV)

2026-09-27. Notes only: no repo file and nothing under state/ was edited. This merges four designs and three skeptic reviews. The questions are in `sequential-questions.json` and the tags for existing questions are in `sequential-tags.json`, both in this folder.

## The short version

- **More reads help, but the reads that help are the saved data snapshots.** JEV has no memory and every proposed question has an answer code can work out, so a JEV answer at 02:35 is fully set by the bars saved at 02:35. Save a snapshot at every checkpoint, and run JEV live only at 08:48 and 09:28.
- **The sequence reaches JEV as labels built by code.** At every read, code rebuilds a short "story so far" from saved futures bars. It never feeds JEV's earlier answers back in.
- **The evidence is thin.** Three independent replays (118 to 136 nights of /ES 5-minute bars, March to September 2026) found no gain from splitting the night into steps over the plain overnight move. The one hint is where the gap was made: the part made early reverses in the first hour, and the Europe leg carries on to the close. That hint argues for rebuilding `gap_origin` on the story legs.
- **Question count.** Eight new SEQUENTIAL_READ questions (6 before the open, 2 intraday), two more untagged bitcoin questions (the third bitcoin question, `weekend_btc_lead`, is among the eight), 20 existing questions tagged. Everything new starts dark, because the premarket lane, the collector and a premarket grader do not exist yet.

## 1) Pre-market read schedule

Times are ET, anchored to each exchange's own clock. Europe changes clocks on 10-25 and the US on 11-01. In the week of 10-26 the DAX opens at 04:00 ET. From 11-01 Tokyo closes at 01:30 ET. The Mac's launchd runs on Pacific time (02:35 ET = 23:35 PT the day before), so every record is keyed by the ET session date.

| Checkpoint | Kind | Why it is there |
|---|---|---|
| C0 16:20 (day before) | snapshot | Records the 16:00 anchors (futures, bonds, bitcoin, SPX close, VIX close, yesterday's ruler, last-hour move) that every later leg is measured from. |
| C1 02:35 | snapshot | Tokyo has closed, so the Asia leg is finished. It also saves $N225 and $HSI, which have no history later. |
| C2 03:35 (04:35 in the week of 10-26) | snapshot, optional JEV | Europe's first half hour is done. JEV here only serves the phone; the answer can be recreated from the snapshot. |
| C3 08:05 | snapshot | The Europe morning is finished and the US pre-market has started (SPY from 07:00). This is the pre-report baseline. |
| C4 08:48 | snapshot + **JEV** | The 08:30-08:45 report window is complete (08:48 leaves time for the 08:44 bar to arrive). It is recorded every day, so the same-clock rank always has its window. |
| C5 09:05 | snapshot | Will's 09:00 slot. Only about 2% of the gap moves after it, and no event falls in its window. It is saved because it is cheap. |
| C6 09:28 | snapshot + **JEV** | The final read: every premarket question, answered from the full night. |

Why the early checkpoints are snapshots rather than JEV reads:
- JEV has no memory. Each question's answer is worked out by code (`code_answer: true`). The labels are rebuilt from bars exactly as they were seen.
- So a 02:35 JEV answer can be recreated later, and will match. What cannot be recreated is quote data with no history ($VIX, $VIX1D and SPY before the open, $N225, $HSI, bitcoin spot), any news text shown at that moment, and a change of JEV version.
- **Catch-up option (Will to decide, section 5).** At 09:28, code rebuilds each earlier checkpoint's labels from saved bars and asks JEV about each one in the same run. That gives one JEV answer per checkpoint for machine learning, from the same JEV version, with no 3 a.m. fire that can fail.

Cost: one quote batch plus about 8 minute-bar calls per snapshot (/ES, /NQ, /ZN, /MBT, $DAX, $N225, $HSI, SPY) at 0.5 s spacing, about 5 s in all. Bitcoin comes from Coinbase spot (see section 3). Storage is about 1 MB of 1-minute bars a night.

### Saving for machine learning (Will's item 1)

- **Raw data.** Every snapshot appends to `state/spx_jev/overnight/{session_day}.jsonl` and never overwrites. It holds the 1-minute bars since 16:00 as they were seen at that moment (stamped with the contract and `fetched_at`), plus the quote snapshot.
- **Checkpoint records.** A record per checkpoint goes to `state/spx_jev/lanes/premarket/{day}.jsonl` (archive schema v2, lane `premarket`). It holds the leg figures, the ranks, the `LabelSet.figures` (number, cut and verdict for each label), every code verdict, labels left out with the reason, and the exact JEV requests and replies whenever JEV ran.
- **Outcomes, appended afterwards.**
  - /ES from each checkpoint to the next one and to 09:34.
  - SPX from the 09:34 settled open to 10:04, to 10:34, and from 10:05 to the close.
- **Retention.** Everything is append-only and nothing is pruned. The full night is saved again at 16:20.
- **Backfill now.**
  - Schwab keeps about 40,000 bars per symbol, so a night of 1-minute /ES and /ZN history is lost every day.
  - /MBT 5-minute history already hits that cap (it now starts on 04-15).
  - Coinbase history is deep and can be backfilled at any time.

## 2) The story-so-far labels

Rules. Each one fixes a defect a reviewer found.

1. **Built from bars, never from earlier answers.** Every read rebuilds every leg from bars that finished before `now`. A missed read (Mac asleep, Schwab error) changes nothing, because the next read rebuilds the same legs. When bars are missing, two legs are merged and the label says so. JEV's earlier answers and confidence never go into a label. They would compound mistakes, create a feedback loop and break replay. Earlier checkpoint verdicts are recomputed from bars cut at that checkpoint, not read from the saved record.
2. **Legs cover 16:00 to now with no gaps.**
   - L0: after the close, 16:00 to 18:00. This holds the after-hours earnings move and about 20% of overnight volume.
   - L1: Asia, 18:00 to the Tokyo close.
   - L2: Europe's open, Tokyo close to DAX open + 30 minutes.
   - L3: the Europe morning, to 08:00.
   - L4: pre-report, 08:00 to the release minute (08:29 when no report is scheduled).
   - L5: the report window, release minute to 08:45, measured every day.
   - L6: the last stretch, 08:45 to the read.
   - Mondays and the first session after a holiday add a weekend clause for bitcoin only.
   - L6 has a data-only split at 09:05.
   - The window is pre-registered: "the Europe leg" means Tokyo close to 08:00, so replays cannot pick a window after seeing results.
3. **One unit and one rank.** Sizes are in yesterday's ruler, the 20-session median anchor known at yesterday's close (the `rulers` convention, the same as `overnight.es_move`). Proposal 1's "normal night" is rejected as a third unit. Each leg is ranked in thirds against the same clock window over the last `{night_rank_count}` (20) nights, needing at least `{min_rank_sessions}`. Roll nights, holidays and nights with an estimated ruler are skipped. There are no fixed cut-offs.
4. **Where price is now comes first** (the `location_first` convention). The reference price is always called "futures' 16:00 price", never "yesterday's close", which existing labels use for SPX's cash close.
5. **Neutral words for each leg, the option name only at the end.** Legs say "added", "gave some back" or "quiet". Only the final verdict clause may use an option name, so no leg word clashes with an option. Each leg gives its own size and its own third. The running total is stated once, first. (Proposal 1 put the running total beside the rank of a single leg.)
6. **Short labels, because a group shares them.** `ask.build_requests` puts the group's whole `reads` list in front of every question in that group (checked: ask.py lines 153-161, `GROUP_CAP = 8`). Proposal 2's claim that each question sees only its own labels is false. So the new premarket group reads four labels of at most 45 words each (existing labels run a median of 33 words and 71 at most). Proposal 1's single 90-word, 11-number label is rejected.
7. **Guards against a bad series.**
   - A leg that crosses a contract roll is measured on one contract, or left out.
   - Bitcoin comes from Coinbase spot, because the stitched /MBT series gave the wrong sign on 09-25 after the 09-24 roll.
   - A stale feed leaves the label out and the question skips.

The labels of group `premarket_sequence`, with example text:

- `premarket.where_now`: "at 09:28 S&P futures are 0.31 sigma above their 16:00 price, higher than 16 of the last 20 nights at this time, top third; 85% of the way up the night's range"
- `premarket.legs`: "oldest first: after the close quiet; Asia up 0.24 sigma, top third; Europe's open quiet; Europe morning down 0.09, middle third, gave some back; report window up 0.12, top third, added; since 08:45 quiet. Of 3 legs that moved, all went with the night's rise: one way"
- `premarket.arc`: "the first leg that moved was Asia, up 0.24 sigma; futures now keep 129% of it, still above their 16:00 price: later trading built on it"
- `premarket.since_checkpoint`: "since the 08:48 checkpoint futures fell 0.09 sigma, top third for 08:48-09:28 over the last 20 nights, against the night's rise, still above their 16:00 price: the latest leg gave back"

## 3) Final SEQUENTIAL_READ questions

The full specs are in `sequential-questions.json`. All are dark until the collector, the premarket lane and the premarket grader exist; after that the owner rule makes them live. Every one has three options or fewer, because rarely used options never move off the prior when the table only remembers about 50 days. Each puts "nothing happened" to sleep in code rather than offering it as an option (the `gating` convention). The options are sign-free (the `sign_free_crosses` convention).

Before the open:
1. **`overnight_arc`** (09:28; optional 03:35 and 08:48). Did the night's first real move build, hold, fade or reverse? It is anchored to the first leg that moved. Proposals 1 and 2 anchored "reversed" to the furthest point of the night, which fired on 1 of 119 nights against 16 to 19 real reversals. This replaces `overnight_story_shape` (both versions) and `seq_overnight_arc`.
2. **`latest_leg_vs_night`** (08:48, 09:28). Since the scheduled previous checkpoint, did futures add to the night's move, give some back, or cross their 16:00 price? It merges `move_since_last_read`, `latest_leg_vs_story`, `seq_since_last_read` and `seq_into_open`, and is always measured from the scheduled checkpoint. That avoids the late-read trap in `range_size._since_last_read`, which measures from `scene.last_read`.
3. **`night_legs_agree`** (09:28). Did every leg that moved push the night's way, or did the latest one turn against it? This is Proposal 3's sign-free version. It covers Will's "is the direction consistent across reads" idea.
4. **`release_vs_night`** (08:48, 09:28). Did the report's first 15 minutes extend the night's move, undo part of it, or carry futures across their 16:00 price? "Crossed" is tested at the window's end (08:45), not now. It sits beside `release_reaction_path`, which covers the reaction against its own aftermath.
5. **`night_vs_last_hour`** (09:28). Did the night carry on yesterday's last cash hour, or turn against it? The options were renamed so that "reversed" is not used twice in one lane.
6. **`weekend_btc_lead`** (Mondays and the first session after a holiday, 09:28). Bitcoin moved over the weekend; did futures follow it after the Sunday reopen, ignore it, or go the other way? It stays dark until a replay across Coinbase's years of Mondays against daily SPX backs it. That test is cheap and can run now.

During the session (30-minute lane, group `read_sequence`; dark until an offline replay on the SPX bars already saved):

7. **`seq_day_move_stage`**. Is the day's move from the open still making new ground, stalled, or unwinding, judged across the lane's last reads? This is the only price sequence with a hint in replay: extending went with the day 38% vs against 32% (n=168); stalled went with it 32% vs against 41% (n=191).
8. **`seq_breadth_drift`**. Across the last reads, has the volume share of rising stocks built behind the day's move or faded under it?

Bitcoin (Will's item 3; bitcoin data verified available and accurate as Coinbase spot):
- **`btc_overnight_vs_gap`** (untagged, a single window). Did bitcoin move further toward stocks-up or stocks-down than the gap would match? The label drops the "1.2 times" ratio sentence (mixed units) and gives only the rank of the leftover move.
- **`btc_divergence_30m`** (untagged, 30-minute lane, dark). The intraday evidence was -0.064, the opposite of "bitcoin leads". It stays dark until a replay.
- `weekend_btc_lead` is listed above.
- Also recommended, as an edit to an existing question (the questions file adds no new question for it): remove bitcoin from the criteria of `overnight_bonds_vs_gap`. Correction to the reviewers: bitcoin does not put that question to sleep. Checked in the spec: a bitcoin reading in the opposite outer third blocks the "ahead" answers and forces `in_line`. The effect is the same: bitcoin noise mutes the only cross-asset input with evidence (/ZN +0.21 to +0.23 to the close, about 2.3 standard errors).

Evidence, honestly stated:
- Every new question is untested as a JEV reading.
- Critique 1 found that shape, extreme timing, last hour and leg agreement repeat 27-47% of what the overnight move bucket already carries, against 5% for shuffled data.
- None of those four beat the bucket.

### Cut, with the reason

- **Overlaps a kept question or is duplicated by one:** `europe_open_vs_asia` / `europe_vs_asia` (Europe's first hour adds about 4% of the gap's variance; the leg stays in the label as data), `overnight_extreme_timing`, `fresh_night_extreme` (overlap "built" and `overnight_range_position`), `leg_agreement` (it names up/down in its options, and its 08:05 leg is zero minutes long), `seq_legs_agree` (replaced by `night_legs_agree`), `seq_report_chain` (it compares only against the Europe leg), `release_vs_overnight_leg`.
- **Bonds per checkpoint:** `bonds_since_last_read` and `seq_bonds_led`. The fit would sleep until about November; bonds stay as one question at 09:28.
- **Bitcoin echoes futures** (+0.54 to +0.59 in the same hours): `btc_vs_futures_since_last_read`, `bitcoin_leads_futures`, `seq_bitcoin_led`.
- **Can't be learned yet:** `seq_open_vs_story`, because the opening lane has no learning loop (`lane.py` `pool: bool = False` by default; `TAPE pool=False` per critique 1).
- **Replay showed no edge, and they overlap `price_move_5way`:** `seq_leg_run`, `tape_slice_run`.
- **Parked as data ideas, not questions:** `seq_fear_drift` and `seq_book_drift` (they need fixed counts such as `seq_majority`), `tape_extreme_staircase` and `tape_flow_persistence` (the opening lane has no loop).
- **Code features for the combiner, not JEV questions:** `seq_call_path`, `tape_call_path`, `seq_picture_churn`. Asking JEV about its own calls risks an echo and a grade leak.

### Critiques applied and critiques rejected

Applied: everything above, plus the rest of this list.
- Only 09:28 feeds the weights.
- Grades start from the 09:34 settled open.
- A 10:05-to-close outcome is added.
- The runner refuses a late fire.
- Schwab calls retry with backoff.
- The Schwab login is alerted the evening before it lapses.

Rejected or partly rejected:
- **Critique 3's cut of 02:35, 08:05 and 09:05.** I cut them as JEV reads but kept them as code snapshots. Will asked for more reads, they cost about 5 s, and quotes taken then cannot be recreated.
- **Critique 3's choice of Proposal 1's `overnight_story_shape`.** Its "reversed" rule almost never fires (critique 1's replay).
- **Critique 1's "fold the report question into `release_reaction_path`".** A 2x3 grid of reaction side by what happened next creates the rare cells that critique 1's own point says never learn.
- **Critique 1's cut of the night-vs-last-hour and leg-agreement ideas.** Under the owner rule an unproven question stays, one question per idea, and they are dark anyway. The duplication problem is handled by decision D3, not by deleting.
- **Proposal 2's separate weights per read slot.** That is 48 weights learned from about 20 outcomes.
- **Proposal 1's per-read /ES targets as weight inputs.** They are kept as archive outcomes only. The night so far predicts the next overnight leg at -0.02 to -0.10.
- **Proposal 1's "the spec needs a writer change to accept `tags`".** No key whitelist was found in `tests/test_spec.py` (critique 2; I grepped too). The real blocker is that `night_rank_count` and `btc_stale_min` must be added to `cuts.py`, or the constant check fails. `write_question_docs.py` was not checked.

## 4) Existing questions to tag SEQUENTIAL_READ

The rule, from critique 2: tag only where the label states two or more steps in time order and the answer depends on that order. That gives 20 questions; each reason is in `sequential-tags.json`.

Left untagged on purpose, because each is one change or one shape rather than a before-then-after story:
- `walls_motion` (one 30-minute change; Proposals 1 and 2 tagged it)
- `open_contested` (a count of crossings, whatever their order)
- `day_character` (one ratio)
- `breadth_day_regime`
- `fresh_extreme_5m`
- `zero_dte_side_shift_open`

Tagging these would put about 45 questions under the tag and make it mean little.

Two recommended edits to existing questions (not tags):
- Rebuild `gap_origin` on the story legs, naming the leg with the most unusual rank. It fixes the overlapping windows the plan found, and it is where the only order-of-events hint lives: the Asia leg against the first 60 minutes gave -0.23.
- Remove bitcoin from `overnight_bonds_vs_gap`.

## 5) Risks and decisions for Will

Decisions:
- **D1: which reads run JEV.** Recommended: JEV live at 08:48 and 09:28, snapshots at all seven checkpoints, and optionally JEV at 03:35 for the phone.
- **D2: the catch-up replay at 09:28.** Should the 09:28 run also ask JEV about every earlier checkpoint's rebuilt labels, giving one answer per checkpoint for machine learning from the same JEV version? Recommended yes. It costs about 0.1 s per question.
- **D3: how the premarket group counts in the phone's sum.** Should the premarket answers held into 09:32 and 10:02 count as one vote (the arc verdict) instead of about 10? Recommended one vote. They all come from one /ES price path, and weights start neutral at 1.0.
- **D4: which horizon to grade.** Build a premarket grader with a 10:05-to-close outcome? Recommended yes. It is the only horizon where overnight signals showed up; the loop grades 30 and 60 minutes only.
- **D5: the pre-registered verdict.** After about 40 live nights, does the sequential bundle beat `overnight_move_vs_expected` alone when each night is predicted by a fit that excludes it? If not, the questions stay as dataset features without full weight. Recommended yes.
- **D6: the Monday bitcoin replay.** Run the Coinbase-history replay of weekend bitcoin moves against Monday SPX before `weekend_btc_lead` leaves dark? Recommended yes.

Risks:
- **The core idea is unsupported so far.** Leave-one-night-out, splitting the night into legs did worse than the net move (Proposal 3). No sequential or bitcoin effect showed up at noise level (Proposal 1; critique 1 on 119 nights). One regime, /ES standing in for SPX, about 40 tests. The replays also disagree with each other: the Europe leg to the close scored +0.13 in one and +0.27 in another, and bonds scored +0.13, +0.23 and +0.43 depending on the measure.
- **The learning loop cannot judge these questions yet.** The loop adds one day of weight per day. A pre-market answer held into the live lane is in play at about 2 of 13 reads. When a question is idle, `pool.py` `member()` (line 461) scores it as the whole question block's forecast. So about 85% of its daily score measures the other questions. This affects every question that often sleeps, including live ones. The fix is to score only the reads where a question was in play. It is outside this brief; flag it for its own task.
- **Grader leak.** `grade.py` measures from the record's spot price. Before 09:30 that price is yesterday's SPX close, so a 09:28 read would be credited with the whole gap. Premarket grades must start from the 09:34 settled open.
- **Late fire.** `schedule.read_slot` (schedule.py line 86) maps any moment to the latest slot at or before it. A job launchd runs late at 07:00 after the Mac wakes would be filed as the 03:35 read. The premarket lane must refuse to run outside its grace window.
- **Operations.**
  - The Schwab client has no retry (`raise_for_status`), and three per-minute jobs share one login.
  - The Schwab login lapses Mon 09-28 about 17:58 ET. auth-watch runs at 08:00 ET, after every overnight read has already failed.
  - launchd uses Pacific time, so the Weekday lists shift for fires after 21:00 PT.
- **Clock and contract traps.**
  - Clock changes: Europe on 10-25, the US on 11-01.
  - Rolls: bitcoin about 10-30, /ZN late November, /ES about 12-10. Each needs the roll guard.
  - $DAX, $N225 and $HSI are not yet cross-checked against an exchange source.
  - Coinbase is a new outside feed and needs a staleness check with /MBT as the fallback.
- **Probes still needed Monday (about 09:20):**
  - whether $VIX and $VIX1D quotes update before the open
  - whether S&P option chains trade in Cboe's overnight session (from memory, unchecked)
  - VIX futures are not on Schwab (`/VX` is an invalid symbol)
- **Calendar gap.** Calendar `pre_open` rows start on 09-01, so a replay of August calls real report days "no report".
- **Label reading is unproven.** JEV has never been reading-checked on multi-leg label sentences. The set's `reading_checks` convention needs a 40-read check before trusting them.
