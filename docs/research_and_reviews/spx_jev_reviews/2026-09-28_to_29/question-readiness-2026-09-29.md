# SPX JEV question readiness — Tuesday 2026-09-29

> A dated note: the paths and folder names in it are those of its day. Where every store lives today, with its writer and readers, is `docs/data_map.md` at the repo root.

Written 02:25 ET Tuesday, before the 02:35 pre-market read. Six checkers looked at the questions and the logic around them: a full replay, two logic reviews, a rank-history check, a request check and a sums-and-grades check. The nine fixes they agreed on were built and tested in a scratch copy of the repo. **None of them is on the live station and nothing was pushed** (section 7 says why). No Schwab calls were made. The only JEV call was the request checker's one-question key test.

Scratch work: `<session scratchpad>/readiness/` (the fixes are in `fixclone/` and `ontop/`, the before-and-after replays in `verify/` and `verify2/`).

---

## 1. Verdict

**Tuesday can run on what is live now. The fixes make it better, but they are not required.** All six checkers said "ready with notes". Nothing crashed in 124 replayed reads (four days, three lanes). No request breaks JEV's format, and every question that can be asked was asked at least once. The known data gaps remain.

Tuesday will see the same wrong answers Monday saw unless the fixes go in before the pre-market read at 02:35 ET. The worst of them:
- **put_tilt_vs_clock:** JEV reads "flat" as "steep".
- **Stale answers in the sums:** round_level_cross and strike_defense hold old answers after the level or strike has left play.

## 2. What was checked

- **Replay:** every lane on 09-17, 09-24, 09-25 and 09-28 (premarket, opening 5-minute and 30-minute reads, 31 a day). A stand-in for JEV gave random valid answers, and nothing was sent.
- **Monday compared with the rules:** Monday's real JEV answers were checked against the answer each question's own rules call for.
- **Rank history:** a stand-in Tuesday built from Monday's data, with every ranking call counting the sessions it compared against.
- **Requests:** checked against TypeSafe's published format, plus one live JEV key test.
- **Sums and grades:** Monday's 11 live reads pushed through today's code using JEV's recorded replies, and every grade redone by hand.
- **Tonight's final check:**
  - Replayed 09-25 and 09-28 before and after the fixes, with the stand-in JEV giving the same answer to the same question at the same read.
  - Compared every request sent, field by field.
  - Ran all six test suites on the fixed code.

## 3. Readiness by group (replay, 4 days, all lanes)

"Asked" means JEV was asked. "Asleep" means nothing happened, so the question sat out. "Missing" means a fact was not available, so it was skipped with a reason. Counts are read-question pairs. These are at `8bcab7c`, before the fixes.

| Group | Questions | Asked | Asleep | Missing | Never asked on any day |
|---|---|---|---|---|---|
| opening_price | 6 | 251 | 0 | 0 | |
| opening_flow | 8 | 305 | 75 | 27 | |
| opening_context | 4 | 12 | 4 | 0 | heavyweight_gap_split (no index weights file) |
| price_move | 6 | 258 | 0 | 10 | |
| gap_and_day | 7 | 214 | 36 | 2 | |
| size | 8 | 401 | 8 | 2 | |
| vol_signals | 6 | 254 | 8 | 20 | |
| events_regime | 6 | 98 | 121 | 0 | stress_state (no stress day in the sample, by design) |
| dealer_regime | 5 | 223 | 0 | 1 | |
| gamma_map | 6 | 223 | 9 | 47 | |
| levels_and_tape | 5 | 169 | 27 | 83 | |
| breadth | 8 | 217 | 82 | 68 | (Monday's breadth excluded, accepted) |
| leadership | 6 | 185 | 0 | 94 | megacap_cohesion, leaders_vs_rest (no index weights file) |
| macro_and_calendar | 6 | 148 | 0 | 62 | macro_lean (no /6E), month_turn_flow (no daily closes) |
| shocks_and_news | 6 | 43 | 381 | 2 | single_name_shock (no index weights file) |
| close | 6 | 51 | 5 | 0 | fed_presser_path (Fed decision days only, by design) |
| read_sequence | 2 | 38 | 42 | 0 | |
| bitcoin | 4 | 13 | 102 | 0 | btc_gap_streak (no /MBT feed before 09-28; half hours split on 09-28) |
| premarket_overnight | 4 | 14 | 2 | 0 | |
| premarket_context | 5 | 10 | 14 | 0 | btc_weekend_path (Mondays after a big weekend only, by design) |
| premarket_sequence | 3 | 12 | 4 | 0 | |
| shadow_log | 3 | 59 | 0 | 10 | |

Notes:
- **Two big "missing" counts:** most of levels_and_tape's 83 and part of gamma_map's 47 were really "nothing in play". With the fixes these become "asleep" (section 4).
- **Blocked by missing data:** the "4 questions awaiting data" note undercounts. There are 6, blocked by 3 sources:
  - The index weights file blocks 4 (megacap_cohesion, leaders_vs_rest, heavyweight_gap_split, single_name_shock).
  - /6E blocks macro_lean.
  - Daily closes block month_turn_flow.
- **day_character** is missing at 10:02 and 10:32 every day. Its label cannot exist before about 11:00.

## 4. Fixed tonight (built and tested, NOT on the live station)

Nine commits are ready. They were first built on `8bcab7c` and have also been re-applied cleanly on the new live HEAD (section 7). Each one has a new test, and each new test failed on the old code.

1. **round_level_cross.** When no round level is near price, or price pushed through and fell back, the question now sleeps instead of reading "missing". It can no longer carry an old "pressing from above" into the sums.
2. **strike_defense.** Same fix, for when the busy strike is out of reach or no strike was hit enough times. A stopped collector still counts as missing data.
3. **Doc counts.** The README now says 129 questions, 136 labels and 67 constants. The opening-lane list gains btc_five_day_lead, and a test keeps these counts in step.
4. **put_tilt_vs_clock.** The label no longer says "steeper than". It names the answer: "a steep put tilt", "a usual tilt" or "a flat tilt" for this minute. On Monday JEV picked steep on all 11 reads where the rules meant flat.
5. **opening_pace.** The options now say "bottom / middle / top third", the words the labels use, instead of narrow / ordinary / wide. On Monday JEV missed the intended answer on 4 of 7 reads.
6. **realized_vs_clock.** The label names its answer (ordinary, busy or very busy for this half hour). It adds "no real move" only in the bottom fifth, where that decides coiled against quiet.
7. **gap_reasserts.** On a morning with no real gap, the question sleeps with "no real gap this morning".
8. **ruler_loaded.** The options name the three events that count: the Fed's rate decision, the chair's testimony and Jackson Hole. "Loaded" no longer says "far more", and "swollen" needs an empty calendar. The label also says when none of today's calendar rows is one of those three. That covers Tuesday's 10:00 releases and the 12:40 and 13:25 Fed governor speeches.
9. **context.units.** The RSI sentence with its fixed 70/30 lines is gone. No question reads RSI.

**Replay proof, before and after, 09-25 and 09-28.** The request differences are exactly these nine changes and nothing else:
- **Wording changes:** the units sentence, the put-tilt, swing and ruler labels, and the options for put_tilt_vs_clock, opening_pace, realized_vs_clock and ruler_loaded.
- **Status changes:** round_level_cross went from "missing" to "asleep" on 23 reads (09-25) and 13 reads (09-28). strike_defense did the same on 7 and 3.
- **Stale answers gone:** the old answers held in the sums disappeared at 11:00, 12:01, 13:00 and 13:31 on 09-25, and at 11:01, 11:31, 15:00 and 15:31 on 09-28.
- **No other change:** nothing else in any request changed, and no other question changed status.
- **gap_reasserts:** neither replayed day had a no-gap morning, so fix 7 is shown only by its test.

**Tests on the fixes over the new live HEAD:** all six suites pass.
- skills/spx-jev: 1329 passed
- runtime/viewstation: 558 passed
- runtime/watch: 248 passed
- runtime/health: 57 passed, 14 skipped
- skills/sndk-jev: 192 passed, 1 skipped
- skills/sndk-pro: 503 passed

The question doc check (`write_question_docs --check`) also passes.

## 5. Defects left open (need your call)

| # | Question or area | Problem | Why it's left | Proposed fix |
|---|---|---|---|---|
| 1 | wall_touch_effort | While price is still touching a strike that has not been judged yet, the sums carry an answer about a different strike or side (Monday 11:31, 12:01, 14:31, 15:00). | An existing test deliberately keeps this behaviour. | Store which side the answer was about and hold it only while that side is unchanged, or never hold while the touch is unjudged. |
| 2 | Answers about an ended event come back | A read where the question sleeps because its event ended does not clear the stored answer. A "missing" read after it then brings the old answer back. | Changes how the scheduler holds answers. | Stamp the time a question fell asleep, and never hold an answer from before that stamp. |
| 3 | Once-a-day questions whose one read is lost (vol_curve_vs_usual at 10:02; opening-lane 09:35 questions; expiry_calendar) | If that read is skipped, the question is never asked that day, yet the reason given says "asked at 10:02 and held". This happened live on Monday. | Changes how every day-constant question behaves. | Ask it at the next read when nothing from today is held, and fix the reason text. |
| 4 | Data gaps recorded as "asleep" (single_name_shock, heavyweight_gap_split, the bitcoin gates, put_tilt_vs_clock, seq_breadth_drift, plus short-history ranks in several gates) | A missing fact is counted as "nothing happened", which breaks your rule. Health reviews can't see these gaps. | The spec texts agree with the code, so both would change. | Let the gates say "could not measure", which is reported as missing. Reword the spec's sleep conditions. |
| 5 | day_character | It is due from 10:02, but its label needs data up to about 11:00, so it is missing at 10:02 and 10:32 every day. | This is your choice. | Start it at 11:02, or lower the label's minimum. |
| 6 | move_shape | A spike that gives the move back scores over 100% as a "burst". The rules check burst before choppy, so a choppy half hour is called burst. | Two different fixes are possible. | Check choppy first, or measure the burst against the distance travelled. |
| 7 | size_leadership_day | No option fits "one fund ahead, one behind, gap ordinary". JEV answered small_led on 7 Monday reads. | Needs a new option. | Add a one_ahead_one_behind option and reword the label. |
| 8 | Calendar "ordinary" options (event_cycle_day, expiry_calendar, month_turn_flow) | "Nothing on the calendar" is answered and counted every day instead of the question sleeping. | You need to confirm that calendar-ordinary counts as nothing happened. | Remove the ordinary options and add sleep conditions. |
| 9 | Premarket 09:28 single shot | 10 of 12 premarket questions are asked only at 09:28. On Monday a JEV timeout lost 4 of them for the whole day. | Changes the schedule. | Also ask at 09:05 and fall back to that answer, or retry within 09:28. |
| 10 | break_armed label | It says "expires if price does not close through that level" when price is already through the level. | Wording only, and low impact. | Say "price is already through; the setup has not fired". |
| 11 | event_clock (opening lane) | The 09:35 answer "due" is held after the 10:00 release is out. Tuesday has two 10:00 releases. | Schedule change. | Also ask at 10:05. |
| 12 | 10:32 read, first-hour questions | If the newest row is stamped before 10:30, three questions come up one bar short. | Timing change. | Wait for a fresh row at 10:32 as well as at 10:02. |
| 13 | breadth_flip_after_release | It wakes at 10:02 on only 1-2 minutes of data. A change of exactly zero reads as "selling". | Spec change. | Keep it asleep for 15 minutes after a release (as move_reaction_path does), and fix how ties are ranked. |
| 14 | Reading checks | The spec promises that JEV's pick is scored against the rules' own answer on every read. That check was never built, which is why problems like the put-tilt misreading went unnoticed. | New feature. | Build it, or at least run it nightly on the archive. |
| 15 | Learning loop after the baseline refit | Monday is learned against the old baseline, and the next snapshots apply that old correction. The phone is not affected because the loop is gated off. | Your call on re-snapshotting. | Reset the correction at snapshot time when the baseline changes. |
| 16 | Unsure picks in weights.json | Unsure picks are counted as misses, while the card and the store count them as no call. | Other readers may depend on that field. | Add a hit rate on committed calls beside the existing one. |
| 17 | Fed speakers in the learning loop | Speeches have no end time, so a read taken during a speech is still learned from. Tuesday's 13:32 read is affected. | Calendar data. | Add end times to the speaker rows. |
| 18 | Put-tilt check with JEV | The reworded put-tilt text has not been sent to JEV to confirm it now picks flat. | This run made no JEV calls. | Send Monday's saved put-tilt states once. |

## 6. JEV key test and requests

- **Key test:** one yes/no request with the station key (never printed). It came back HTTP 200 in 0.19 s from model jev-1.13.0, answering 0.98 yes.
- **Request format:**
  - Every request is a choice, yes/no or score type.
  - Choice options match their criteria, and score levels are in order.
  - No request has an unfilled placeholder, a typed number or a raw index price.
  - No group has more than 8 questions.
  - Requests are at most 10.4 KB each, about 97 KB per live read.

## 7. Shipping status: not pushed

- **Merge and push need your go-ahead.** The fixes live only in scratch. Putting them on the live station needs your explicit go-ahead in the current message (your standing rule), and this run carries none.
- **Another session changed live `main` tonight.** It is now `4576873`, not `8bcab7c`: 27 commits ahead of GitHub and not pushed. They add the average-price question, move the phone's call onto it and switch on the average-price learning loop. The checkers did not review these. Pushing `main` now would also push that session's work, which is not this run's to push.
- **The old one-line merge no longer works.** The planned fast-forward from `fixclone` would fail. The nine fixes were re-applied on top of `4576873` in `.../readiness/ontop` (detached, no branch created). There was one text conflict in the README's file table, resolved by keeping the other session's Sums row and the corrected counts. All suites pass, and the before-and-after replays of 09-25 and 09-28 on the new HEAD show the same nine changes and nothing else.
- **Timing.** Your rule is that changes to what JEV is sent land between sessions. The pre-market lane starts at 02:35 ET. After that, the choice is to land the fixes after Tuesday's close or to accept a change partway through the day.
- **To apply on your go-ahead** (it lands on top of the other session's commits):
  ```
  cd ~/.claude/plugins/mirai-station (now ~/mirai_awakening) && git fetch <scratchpad>/readiness/ontop HEAD && git merge --ff-only FETCH_HEAD
  ```
  After that, `git push origin main` pushes both sessions' commits.

## 8. Rank history gaps expected Tuesday

- **Enough history:** every measure ranked against the last 20 sessions has 19 or so. The ruler for 09-01 was estimated, and Monday's breadth was dropped.
  - Skew has 12–18.
  - Overnight measures have 15–19 usable nights.
  - Breadth has 19 for every series.
- **Under the 10 needed:**
  - **first_hour_break_side:** 0–2 prior sessions at 10:32, about 7 at 11:02. The comparison only uses sessions that had already broken out by that minute, and only about 35% have by 11:02. It will be skipped as missing whenever a live break is in play at those reads. More history will not fix this (your call on how to rank it).
  - **option_quote_liquidity** (shadow): 6–7 at 14:02 and 2–3 at 15:02. The contract bucket it ranks empties toward expiry. The impact is small because shadow questions don't feed the sums.
- **Skipped all day, and expected:** xasset.btc_five_day, because bitcoin futures rolled to a new contract within the last 5 sessions. It clears around 10-05.
- **Operational:** the Schwab login has lapsed. If it isn't renewed before 02:35 ET, the premarket futures save, the minute bars and the market feed all go dark on Tuesday.
