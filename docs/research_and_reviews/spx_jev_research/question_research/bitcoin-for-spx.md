# Bitcoin for SPX direction: data check, research and draft JEV questions (2026-09-27)

Read-only study. No repo or state file was touched. There were 13 Schwab REST calls (1 quote batch, 12 price-history requests) through a throwaway client that kept its refreshed token in memory. The lob-flow stream was not used and there was no login. Scripts and raw pulls are in `scratchpad/btc/` (`fetch.py`, `fetch2.py`, `q1.py`, `q2.py`, `build.py`, `an_on.py`, `an_daily.py`, `an_intra.py`, `an_lag.py`, `an_more.py`). The drafted questions are in `bitcoin-questions.json` in this folder.

## Verdict

Bitcoin moves with stocks at the same moment, but on this data it does not move first. Overnight and over weekends, whatever bitcoin knows is already in the S&P futures by 09:28. During the day the index leads bitcoin by about a minute. The one repeatable effect runs against bitcoin's direction: when bitcoin has run ahead of the index in the last half hour, the index's next half hour leans slightly the other way (43.5% up vs about 53%). It is small (t about -2) and should be learned by the combiner, not assumed. Confidence: **likely** for "no overnight lead beyond futures" (119 nights, plus 287 weekends back to 2020). **Speculative** for the intraday contrarian effect (one six-month sample, borderline significance).

---

## Part 1: the data

**Correction to the task's premise:** CME bitcoin futures are no longer shut at weekends. CME moved all its crypto futures to 24/7 trading on **Friday 2026-05-29** ([CME release, 2026-02-19](https://www.cmegroup.com/media-room/press-releases/2026/2/19/cme_group_to_launch247cryptocurrencyfuturesandoptionstradingonma.html); [CoinDesk/Yahoo, 2026-05-31](https://finance.yahoo.com/markets/crypto/articles/bitcoin-first-cme-gap-free-174649486.html)). What remains is a 2-minute weekday maintenance break and a 2-hour one on Saturdays. Schwab's bars confirm it: the first Saturday bars are 2026-05-30, and there are none on any earlier weekend back to March. So the old "Friday 17:00 to Sunday 18:00" gap exists only before 05-29. Since then a weekend move shows up as thin weekend trading, not as a Sunday gap.

| Item | Finding |
|---|---|
| Quotes | `/BTC` answers as **/BTCV26**, `/MBT` as **/MBTV26** (October contract, expires Fri 10-30). /BTCX26, /BTCZ26 and /MBTX26 quote as well. /BTCU26 is invalid (expired 09-25). The quote carries bid/ask, last, open interest (/BTCV26 18,008; /MBTV26 21,927), `quotedInSession: True` on a Sunday, and the multiplier (/BTC 5 coins, /MBT 0.1). |
| 1-min history | /BTC from **2026-08-11** (Schwab's ~48-day time cap, 36,099 bars). /MBT from **2026-08-20** (hits the 40,000-bar cap because it trades almost every minute). Both run to Sunday 09-27 16:5x ET. `need_extended_hours_data=False` does not filter crypto: weekend bars come back anyway. |
| 5-min history | /BTC from **2026-03-09**, /MBT from 04-15, /ES from 03-05, IBIT from 03-05, $SPX from 01-12 (each capped at 40,000 bars). This is the backfill source for overnight rankings. |
| Daily history | /BTC from **2020-01-02** (1,694 bars, no weekend bars even after 05-29), /MBT from 2021-05, $SPX and /ES from 2019. |
| Night liquidity (median 1-min bars per ET hour, 08-24 to 09-26) | **/BTC** weekdays: 34-46 an hour overnight, 56-60 in cash hours, **5 in the 17:00 hour**, 24-38 from 18:00-23:00. Saturday **0-5 an hour**, Sunday daytime 0-6, Sunday from 18:00 38-54. **/MBT** weekdays: 52-60 every hour except 17:00 (30). Saturday 0-26 (zero at 03:00-05:00 ET, the maintenance window). Sunday daytime 8-32, from 18:00 57-60. Weekday contracts traded per hour: /BTC 100-300 overnight, 500-1,000 in the first cash hours, 11 at 17:00. /MBT 1,000-2,300 overnight, 4,500-8,200 at 08:00-10:00. |
| /BTC vs /MBT | Same-minute closes differ by a median 0.0 bp (middle 90% within about 4.5 bp) over 26,710 shared minutes. Overnight moves correlate 0.9999 (113 nights). 30-min returns in cash hours correlate 0.995. They are the same price. **Use /MBT** because it trades far more often at night and at weekends. |
| vs spot (IBIT, US hours) | 30-min returns correlate 0.96 with /BTC (5-min: 0.82, because IBIT's 5-min bars are coarser). IBIT per futures price holds steady day to day (median change 3 bp over 140 days) and drifts slowly down with the fee and the futures premium. The prices are accurate. |
| **Rolls (important)** | Schwab's history for /BTC or /MBT, whichever contract you name, is **one stitched front-month series**. It switches at **18:00 ET on the Thursday before the last-Friday expiry**. All 7 rolls from March to September show it: IBIT/futures fell by 28-53 bp overnight at each switch (03-27, 04-24, 05-29, 06-26, 07-31, 08-28, 09-25; the jump is the next month's premium). A median bitcoin night is 1.1%, so a roll adds a false "bitcoin up" of about a third of a normal night. The next switch is **Thu 10-29 18:00 ET**. Guard it: skip the night, or splice on the IBIT ratio. |
| Gaps | Weekdays 17:00-18:00 ET: trading continues but is very thin (5 /BTC bars). Saturday has a 2-hour maintenance break (no bars 03:00-05:00 ET in the data). Holidays not checked. |

```
 ET clock (24/7 since 2026-05-29)
 Mon-Fri  |== busy 08:00-16:00 ==|thin 17-18|== steady night ==|
 Sat      |. . sparse (/BTC 0-5 bars/h, /MBT 5-25) . . 03-05 maintenance|
 Sun      |. . sparse daytime . . . .|18:00 S&P futures reopen -> busy|
```

## Part 2: research (2022-2026)

- **Weekend crypto predicts Monday stocks, one-sided.** Mourey, Shahrour and Soiman ([Finance Research Letters 86, 2025](https://ideas.repec.org/a/eee/finlet/v86y2025ipes1544612325019154.html)) use the top 20 cryptocurrencies from 2021 to mid-2025. Negative weekend returns predict Monday equity declines and positive ones do not. The link got stronger after the May 2022 LUNA collapse. Caveat for JEV: the study measures Monday's full return, and at 09:28 the S&P futures gap already contains most of it (see Part 3).
- **"89% accuracy for the Monday open" is not about bitcoin.** The Binance Research figure ([CoinDesk, 2026-04-11](https://www.coindesk.com/markets/2026/04/11/crypto-perpetuals-predict-the-direction-of-wall-street-s-monday-open-with-89-accuracy-data-shows)) is for perpetual contracts on gold, oil and stock indices traded on crypto exchanges, and no sample size is given. A later tokenized-equity version ([Yahoo/CoinDesk, 2026-09-22](https://finance.yahoo.com/markets/crypto/articles/weekend-chain-prices-monday-don-183331035.html)) shows weekend prices capturing most of the Monday gap. It also reports 70-90% lower weekend liquidity and pricing dislocations: "an observable pattern and a usable one remain different things." Neither says bitcoin leads stocks.
- **Correlation regimes come and go.** Bitcoin-Nasdaq correlation rose from about 0.23 (2024) to about 0.52 (2025), dropped below zero around December 2025, and in February 2026 the 30-day figure swung from about -0.68 to +0.72 in two weeks ([aggregated reporting](https://coincub.com/blog/bitcoin-dollar-index/); secondary sources, lower trust). Bitwise (Sep 2026) says the 260-day bitcoin-S&P correlation is at its lowest since 2015 ([Bitwise Compass 2026-37](https://bitwiseinvestments.eu/blog/regular-updates/Bitwise_Crypto_Market_Compass_2026_37/)). **My data disagrees:** CME /BTC vs SPX daily, 260 sessions, is 0.51 and has stayed between 0.43 and 0.55 all of 2026. The minimum since 2021 was 0.13 in April 2024. Bitwise probably uses spot prices with weekends included, or a different window. Unresolved.
- **Price paths.** Bitcoin peaked near 126k in October 2025, fell to about 60k by July 2026 while the S&P set records, then rallied to about 85k by late September (Schwab daily bars agree with the press levels). 2026 has been the "decoupled" case.
- **When it fails, three cases.** 2025-10-10: tariff news and a $19bn crypto liquidation hit on a Friday. Stocks fell 3% the same day, so bitcoin did not lead, and both rebounded on Monday ([Fortune](https://fortune.com/crypto/2025/10/13/bitcoin-price-today-ethereum-crypto-markets-rebound-liquidation-trump-china-tariffs/)). 2024-08-05, the yen carry unwind: bitcoin fell about 7% into the Sunday reopen, SPX opened 3.7% lower and then **rose 0.7% from the open**, so the weekend signal was in the gap and the day went the other way ([BIS Bulletin 90](https://www.bis.org/publ/bisbull90.pdf)). 2022-05-09 and 06-13 (LUNA, Celsius): bitcoin fell 4.6% and 6.4% over the weekend, and SPX kept falling after the open (-2.2%, -2.3%). These fit the view that bitcoin leads when crypto is the source of the stress and not otherwise.
- **Intraday lead-lag:** I found no primary study showing bitcoin leading S&P futures at minute scale in the ETF era. Studies show futures leading spot within bitcoin, and ETFs with lower trading costs leading other ETFs ([Applied Economics Letters 2026](https://www.tandfonline.com/doi/full/10.1080/13504851.2026.2621132)).

## Part 3: measurements

Rulers: SPX moves are in shares of the trailing 20-session median |open-to-close| (0.32% median). Bitcoin moves are in shares of its own trailing median. Ranks use prior data only.

**A. Overnight (16:00 to 09:25), 119-129 clean nights, 2026-03-10 to 09-24.** Removed: 7 bitcoin roll nights and 3 /ES roll days (03-16, 06-15, 09-14).
- Bitcoin's overnight move vs S&P futures' overnight move over the same window: rank corr **+0.62**. Vs the SPX open gap: **+0.63**. Bitcoin carries the overnight mood.
- Bitcoin **minus what futures imply** (rolling 20-night beta) vs SPX 09:35-10:05: **+0.01** (p 0.91). Vs 09:35-10:35: +0.05. Vs 09:35 to close: -0.05. The regression with both futures and bitcoin gives bitcoin t = -0.04, +0.25 and -0.10 for the three horizons. The halves flip sign (-0.10 then +0.11).
- Raw bitcoin overnight ranked into fifths against the last 20 nights: first 30 min up 60 / 55 / 58 / 37 / 47% from bottom to top fifth. No order.
- On big futures nights (n 61), bitcoin disagreeing (n 9) coincided with the first 30 minutes continuing the gap 78% of the time vs 38% when it agreed. n = 9 is too small to use.
- The round-3 bitcoin figure in `overnight_bonds_vs_gap` (+0.15/+0.24, n 31) **does not replicate** at n 119.
- Bitcoin's position within its overnight range at 09:25 vs the first 30 minutes: -0.06 (ns). Night-range size vs first-30 size: +0.13 (weak; the futures night range probably carries it).

**B. Weekends.**
- 23 Mondays in the 5-min sample: bitcoin weekend move vs Monday gap +0.46 (p 0.03). Minus what futures imply, vs the Monday first 30 min: -0.08, and vs open-to-close: -0.18 (both ns).
- **287 weekends, 2020-2026** (daily bars, Friday close to the Sunday reopen, before 24/7): bitcoin vs SPX Monday gap +0.19 overall, **+0.36 since mid-2022**, and **+0.50 in 2025**. Vs Monday open-to-close: -0.06 (p 0.31). Controlling for the gap, bitcoin's t = -1.5. By year t stays between -2.0 and +1.0 and never reaches significance on the positive side.
- Bottom-fifth bitcoin weekends (n 63): SPX gap -23 bp, then open-to-close **+20 bp, up 60%**, the same as ordinary weekends (+11 bp, 61%). Close-to-close -3 bp vs +20 bp, which is the published asymmetry. It sits entirely in the gap.

**C. Intraday, 30 minutes.** 1,474 half-hours on 134 sessions, 2026-03-17 to 09-25, 5-min bars, same-clock ranks.
- Same-window 30-min correlation 0.48.
- Bitcoin's last 30 min vs SPX next 30 min: raw -0.053 (p 0.04). Excess over the 20-session beta: **-0.074 (p<0.01)**, day-clustered t **-1.97** after controlling for SPX's own last 30.
- By fifth of the excess, next-30 up share and mean: 53.2% (+1.1 bp), 55.0%, 56.4%, 50.8%, **43.5% (-2.1 bp)**. The typical 30-min SPX move is 7.9 bp, so -2.1 bp is about a quarter of it.
- Tight-link half: -0.10 (p 0.01). Loose-link half: -0.04 (ns). Both time halves are negative (-0.069, -0.078). The latest six weeks alone: -0.03, ns.
- A big SPX 30-min move with bitcoin confirming, against, or quiet: next 30 continues 51%, 50% and 48%. Nothing.
- Three same-sign excesses in a row (n 267): SPX next 30 in bitcoin's direction 43.8% vs 46.6% for mixed. The 90-min sum vs next 30: -0.051 (p 0.08).

**D. Minute lead-lag.** 33 sessions of 1-min /BTC and saved SPX bars, 12,672 minutes. Same minute 0.31. Bitcoin one minute earlier vs SPX: 0.009. SPX one minute earlier vs bitcoin: **0.047**. /MBT gives the same (0.37 / 0.019 / 0.028). **The index leads bitcoin by about a minute.** Nothing at 2-5 minutes or at the 5-min scale.

**E. The multi-day "canary."** 1,608 sessions from 2020 to 2026. Bitcoin lagging its beta over 5 days (bottom fifth) was followed by SPX up 57.9% the next day. Middle: 54.0%. Bitcoin ahead: 51.5%. Non-overlapping 5-day rank corr -0.056 (p 0.32). The warning reading ("bitcoin weakness precedes stock weakness") is not supported. In 2025-26 the shape breaks (62.6 / 49.0 / 66.3%).

**Regime context.** CME bitcoin vs SPX daily correlation: 60-session yearly means 0.37 (2020), 0.23, 0.58 (2022), 0.24, 0.32, 0.47, 0.51 (2026). The 2026 20-session figure was 0.69-0.70 in March-April, 0.26 in August (low -0.08), and 0.63 on 09-25.

## Part 4: draft questions (in `bitcoin-questions.json`)

| id | lane | tag | measured evidence | advice |
|---|---|---|---|---|
| btc_overnight_vs_futures | premarket 09:28 | | null (n 119) | build only when the premarket lane is built; expect zero weight. It replaces the bitcoin half of overnight_bonds_vs_gap |
| btc_weekend_path | premarket, first session after a weekend | **SEQUENTIAL_READ** | none direct (17 weekends of 24/7 data); the long sample says the signal sits in the gap | wait for about 20 saved weekends |
| btc_gap_30m | 30-minute, every read | | weak and runs against bitcoin (t -1.97, n 1,474) | live; the best of the set |
| btc_gap_streak | 30-minute from 11:02 | **SEQUENTIAL_READ** | weak, against bitcoin (p 0.08); overlaps btc_gap_30m | live; keep one of the pair if they duplicate |
| btc_link_today | 30-minute, hourly (regime) | | indirect: the btc_gap_30m effect lives on tight days | live, or fold into the btc_gap_30m label |
| btc_five_day_lead | 09:35, held all day | **SEQUENTIAL_READ** | weak, against bitcoin, unstable in 2025-26 (n 1,608) | live under the owner rule |

All six follow the rules. Every cut is a rank against the last 20 sessions, nights or weekends, using the existing constant names (top_fifth, bottom_fifth, third_hi, third_lo, min_rank_sessions). Labels give moves in shares of a normal move with no prices or percents, and use only past data. Where a question compares bitcoin with the index, "which way" is stated as the direction that has gone with stocks rising or falling over the last 20 sessions. The labels never say "bitcoin leads", so the learning loop sets the sign.

Also recommended:
1. Remove bitcoin from `overnight_bonds_vs_gap`. Its example sentence "bitcoin futures fell 1.8%" also breaks the no-percent rule.
2. Replace "/BTCV26" with /MBT plus a roll table everywhere it appears (`gap_open.py` line 35, question_set `unresolved`).
3. If bitcoin is kept pre-open at all, have the overnight collector save /MBT from now on. The 1-min history keeps only about 38 days, and the weekend path needs saved weekends.

## What would change the verdict

- **Overnight:** after 40 or more further clean nights, bitcoin minus what futures imply shows a rank correlation of at least +0.2 with 09:35-10:35 and the same sign in both halves. That would move btc_overnight_vs_futures to a real input.
- **Intraday:** if the -0.07 disappears on the next 60 sessions (it is already -0.03 on the latest six weeks), btc_gap_30m and btc_gap_streak are noise and should be shadowed.
- **Weekends:** on weekends that begin a crypto-specific stress event (exchange failure, stablecoin break), bitcoin may lead. 2022 fits that; 2024-08 and 2025-10 do not. There are too few such weekends to test.

## Open questions

- The Bitwise "lowest correlation since 2015" claim vs my 0.51 (260-session daily, CME). The difference is probably spot with weekends vs futures, or the window. Not resolved.
- Holiday behaviour of 24/7 CME bitcoin (for example Thanksgiving) is untested.
- Whether a 09:28 /MBT quote is fresh on a weekday is untested (it is liquid at 09:00, 60 bars an hour, so it is probably fine).
- The intraday sample is one six-month stretch in which bitcoin fell from 70k to 60k and rallied to 85k. The effect may belong to that period.
