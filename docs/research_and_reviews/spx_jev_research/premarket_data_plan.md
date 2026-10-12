# SPX JEV pre-market lane: data plan (2026-09-27)

> A dated note: the paths and folder names in it are those of its day. Where every store lives today, with its writer and readers, is `docs/data_map.md` at the repo root.

One read at 09:28 ET covers 5 dark questions. Six reviewers checked the data read-only, with a few live Schwab calls on 09-27.

**In short:** Schwab already has every price these questions need. Nothing saves those prices, nothing runs before 09:30, and the grader would skip a 09:28 read. None of the 5 questions needs a new feed. All of them need building.

---

## 1. Per question

### overnight_move_vs_expected (how far futures moved from yesterday's 16:00 to 09:27)
| Data needed | Source | Available now? | To build |
|---|---|---|---|
| /ES at 09:27 | Schwab /ES minute bars, overnight included (or the quote as a fallback) | Partly: the feed has it, nothing saves it | Overnight collector |
| /ES at yesterday's 15:59, on the same contract | Saved context bars (from 08-11), or the same fetch | Yes | Roll guard (see risks) |
| A ruler for "how far" | Diary sigma_anchor. Today's anchor doesn't exist at 09:28, so use the 20-session median | Yes | Pre-open ruler |
| Prior ~20 nights of the same move | Computed from saved overnight files | Partly: ~25 sessions can be rebuilt today | Backfill plus ranking |

### pm_overnight_session (how wide last night's futures session was)
| Data needed | Source | Available now? | To build |
|---|---|---|---|
| /ES high and low, 18:00 to 09:27 | Schwab /ES bars | Partly: 1-min from 08-17, 5-min from 03-11 (5-min gives the same high and low) | Collector plus a 5-min backfill (~142 nights) |
| Size unit | Night range as % of price. Today's sigma is not known yet at 09:28 | Yes | Change the spec's unit |
| Holiday and short nights | sessions.py plus the calendar | Partly | Skip rule |

### gap_origin (which window made the gap: overnight, the 08:30 release, or the last hour)
| Data needed | Source | Available now? | To build |
|---|---|---|---|
| /ES path from 16:00 yesterday to 09:28 | Schwab /ES bars | Partly: 1-min from 08-17, 5-min from ~03-05 | Collector plus backfill |
| 08:30 release rows | calendar/events.json, pre_open tier | Partly: from 09-01 only, several 08:30 reports missing | Add August rows and the missing report kinds |
| Gap size without sigma | Rank the gap itself against 20 sessions | No | Ranking helper |
| Non-overlapping windows | Spec | No: the release window sits inside the last hour | Redefine as 16:00 to 08:30, 08:30 to 08:45, 08:45 to 09:28 |

### release_reaction_path (what futures did after this morning's 08:30 report)
| Data needed | Source | Available now? | To build |
|---|---|---|---|
| /ES 08:29 to 09:25 | Schwab /ES bars | Partly: 1-min from 08-17, 5-min from 03-05 (147 weekdays) | Collector, trimmed at the read time |
| Which report and when | calendar pre_open rows (time_et) | Partly: from 09-01; claims rows not verified; no 08:15 reports | Take the window start from time_et; add past rows only if the release branch is backtested |
| The same 08:30 to 08:45 window on the prior 20 days (report or not) | 5-min /ES history | Yes | Same-clock rank |
| Pre-open ruler | 20-session median anchor (normal_day_sigma) | Yes | Pre-open scene |

### overnight_bonds_vs_gap (did bonds and bitcoin move ahead of the gap)
| Data needed | Source | Available now? | To build |
|---|---|---|---|
| /ZN at 16:00 and 09:28 | Schwab /ZN (answers as /ZNZ26) | Partly: from 08-12; not in SYMBOLS | Add to the collector and to SYMBOLS |
| Bitcoin at 16:00 and 09:28, weekends included | Schwab /MBT (busier) or /BTC (thin: 1 to 5 bars before 09:29 on some days) | Partly: from 08-11 / 08-20 | Collector; take the last trade at or before each time; staleness check |
| Gap at 09:28 | /ES premarket move (not the 09:34 open) | Partly | Write the definition into the spec's conventions |
| Rolling 20-night beta and sign | Saved overnight files | No | Beta helper. A clean 20-night beta plus a 20-night rank needs ~40 nights (about November) |

---

## 2. Shared pieces, in build order
1. **Backfill the overnight store now.** Schwab keeps only ~40,000 1-min bars per symbol, so about one night a day is lost for good. Pull 1-min bars from 08-17 and 5-min bars from March.
2. **Extended-hours flag on `schwab.minute_bars`**, off by default. Existing callers keep today's behaviour.
3. **Overnight collector** (`spx_jev/overnight.py`): at 09:26 ET, pull /ES, /ZN and /MBT bars from yesterday 15:55 to now, trim them locally (Schwab ignores the end time), stamp the contract name from the quote key, and write `state/spx_jev/overnight/{day}.jsonl`. Re-save at 16:20. This is the one file all 5 questions read.
4. **Roll guard and roll table.** Schwab history is one stitched front-month series whatever contract you ask for. On 09-14 that turned a real big-down open into "up". Next rolls: bitcoin ~10-30, /ZN late November, /ES ~12-10.
5. **Pre-open ruler:** the 20-session median anchor, stamped on the record. Yesterday's anchor swings 0.63 to 1.46x from one day to the next.
6. **Ranking helper:** the last 20 nights, at least 5, skipping roll nights, estimated-ruler days and holiday nights. Includes a same-clock rank for the 08:30 window.
7. **Premarket lane plus scene without a diary row:** the lane key `premarket` with schedule 09:28. The scene is synthetic: spot = SPX prior close x (ES now / ES at yesterday's close). Today `run_once` raises NoRowYet before 09:30.
8. **Runner gate plus launchd plist** (06:26 and 06:28 PT). The gate allows only 09:20 to 09:30 and refuses a late fire. Today both runner scripts exit before 09:30.
9. **Label builders**, one per question, fed from the overnight file.
10. **Grading from the 09:34 settled open** (to 09:44 and 10:04), or holding the answers into the tape and 30-minute lanes. Today every 09:28 read would be skipped with "no morning anchor".
11. **Calendar fill-in:** August pre_open rows and the other 08:30 reports.
12. **Phone:** a pre-open card that polls `lanes/premarket/latest.json` from 09:20. Check it at 360 px.

---

## 3. Fixed cut-offs to replace with 20-session ranks
| Now | Replace with |
|---|---|
| gap_rule_sigma 0.15 (flat line) | Bottom third of the rank of the absolute overnight move |
| gap_large_sigma 0.40 (big line) | Top third (or top fifth) of the same rank. **Conflicts with the 09-27 decision that kept it; see section 4** |
| on_quiet 0.7x / on_wide 1.3x median | Thirds of the night-range rank (the fixed ratios gave 21% quiet and 29% wide) |
| ORIGIN_SHARE 0.5 | Rank each window against the same window over 20 sessions and name the most unusual one. As written, the answer is "overnight" on 81% of days |
| release_small_sigma 0.10 ("shrugged") | Rank the 08:30 to 08:45 move against that window on all of the prior 20 days |
| on_break 0.05 / on_near 0.15 (range position) | A share of the night's range, ranked |
| "Bonds up means stocks up" and a fixed beta | Sign and beta from a rolling 20-night fit |
| Keep as they are | gap_half_share 0.5 (a definition), third_hi / third_lo (already ranks), and the window clock edges |

---

## 4. Risks and open decisions for Will
- **Decide gap_large_sigma:** the 09-27 decision kept 0.40 for the futures bands. That goes against the no-fixed-cut-offs rule. Keep 0.40 or rank it?
- **Decide the ruler:** the 20-session median anchor (recommended), yesterday's anchor, or plain percent?
- **Decide grading:** the premarket lane gets its own grader from the 09:34 open, or its answers are held into the other lanes?
- **Decide on bitcoin:** its evidence is at noise level. Keep the bitcoin half, or build bonds only?
- **Decide on release_reaction_path:** ask on every day with a release, or only when the report is bigger than jobless claims? Claims-only days come out "shrugged" almost every time.
- **Login:** the Schwab refresh token lapses **Mon 09-28 about 17:58 ET**. Without a Monday-evening login, Tuesday's run fails, and 09-30 (GDP and PCE) and 10-02 (jobs) go dark.
- **History loss:** every day without the backfill loses a night of 1-min history.
- **Reviewers disagreed on two points; check both with one call:** (a) whether futures return overnight bars with the extended-hours flag off; (b) whether /ES history is stitched. The 09-14 basis jump and the /ESH27 = /ESZ26 match both say it is stitched.
- **Not tested (it was Sunday):** whether the 09:26 and 09:27 bars have arrived by 09:28 on a weekday, and whether $VIX1D updates before 09:30. Probe both around 09:20 Monday.
- **Grading leak:** grading from yesterday's close counts the gap the read already knew.
- **Thin evidence:** night range vs first-hour range has a rank correlation of only 0.28 (58 days), and /ZN is about +0.43. Replay the 142 nights before trusting pm_overnight_session.
- **Missing release dates:** the calendar has no rows before September and several 08:30 reports aren't listed. A shutdown from 10-01 could also move release dates (not checked).

---

## 5. Verdicts
- **overnight_move_vs_expected:** needs building, no new feed. The roll guard and ranks are required first, because the stitched series gave the wrong sign on 09-14.
- **pm_overnight_session:** needs building, no new feed. It is the easiest one (142 nights available from 5-min bars), but its link to the first hour is weak, so replay it before trusting it.
- **gap_origin:** needs building, no new feed. Fix the overlapping windows and replace the 50% share first. Don't ship it as written.
- **release_reaction_path:** needs building, no new feed. Replace the 0.10 line with a same-clock rank and settle when it gets asked.
- **overnight_bonds_vs_gap:** needs building, no new feed. It has the least history (a clean beta about November), and the bitcoin half may only add answers that keep the question asleep.
