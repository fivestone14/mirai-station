# SNDK property inventory — in the scene payload vs not (living list)

Scope: **SNDK only** (blueprint `mirai-sndk-payload-blueprint.html`, draft v3,
applied 2026-08-02). The scene payload is what `sndk_read.build_scene` hands the
model. Everything else recorded on the diary row is view/telemetry/training
surface, deliberately NOT reasoning input. This list is the pressure-test
shortlist: anything marked *not in payload* is a candidate to argue back in.

Sources: diary row = `sndk_views.build_row` (ROW_V 3), read row =
`sndk_read.read_once` (ERA obs-3), chain = `sndk_feed.sndk_chain`.

## side-1 (2026-09-03) — a second packet, and why it is not in this budget

Everything above is about the SCENE: what the model is handed, measured in
bytes because the model is paid for by the token ~19 times a day. `sndk_side`
builds a different document on the same scan — bars-first rather than
options-first, its clock the bar rather than the two-minute scan — and it is
**never sent to a model**. It is kept in its own file, `state/sndk_side/<day>.jsonl`, one line for each minute the model spoke; the read row carries only `side_bar`, the bar index it was built on. That split is measured too — the packet is ~6.4 KB against a 1.2 KB read row, and the phone polls forty of those rows a minute for two fields the packet does not contain. Quiet minutes keep nothing and rebuild exactly through `sndk_side.side_for_day`, because the packet is a pure function of the bars and the diary row.

That is the whole reason it does not belong in the sr-8 arithmetic. Its bytes
are disk and display, not tokens, so the question "does this leaf vary?" is
the wrong test for it; the right one is "can a person catch a wrong number
here?" It still keeps sr-8's actual rule — its fifteen standing sentences live
in `sndk_side.DOCTRINE` and the packet carries a version pointer, which took
it from 6,549 to 5,577 bytes with no measurement lost.

Measured over the seven recorded sessions on disk it builds at 5.5–6.0 KB
compact, against the scene's 2,645. The viewstation test pins a ceiling of
8 KB — not because smaller is better, but because it should not be able to
grow without someone arguing for it.

WHAT IT CARRIES THAT THE SCENE DOES NOT. Levels drawn from the tape (the
session high and low as lines price has actually respected, not just extremes
in a `price` block), how long a state has held in bars rather than minutes,
each part of the session against what that part usually trades on this
symbol's own recent sessions, and eight checks that run rather than being
asserted. Every reduce returns the bar it came from, so every value can be
checked against a bar the packet also carries.

WHAT IT DELIBERATELY DOES NOT CARRY. Anything derivable from what is already
in it, any combination rule (none qualifies yet on the data available), and
any forecast at all. The four things it declares it cannot see — the
operator's book, option premium, a calendar feed, and a combination with a
measured control — are in `absent`, because an absence is the one thing a dump
of what IS there cannot show you.

## sr-8 (2026-08-30) — the payload pays only for what varies

sr-7 doubled the packet. Measured over 4,149 recorded scans replayed through the
live builder (2026-07-27 → 08-28) it stood at **3,868 bytes, 144 leaf names,
120.4 leaf instances** per call, of which `data_sources` alone was 872 bytes
(22.8%) — a bigger line item than `walls`. sr-8 took it to **2,645 bytes, 115
names, 75.2 instances** (−31.6%) without deleting a single measurement.

The governing distinction is WHERE a sentence rides, not whether it is said.
Two halves reach the model on every call: the scene, which changes every scan
and is paid for in full, and the doctrine, which rides `--append-system-prompt`
byte-identical all day and is prompt-cached at roughly a tenth of the price.
The read fires every ~2 minutes through the session, so the doctrine is never
cold. **A sentence that never changes belongs in the cached half.**

Three tiers of "constant" and only the first was cut:

1. **Never varies, ever** — one value across all 4,149 scans of all 25 sessions.
   Cut, and stated once in the doctrine.
2. **Frozen intraday, refreshed overnight** — magnet, walls, breadth, dealer
   positioning. KEPT. These carry real information, just not today's news, and
   `PRESENT_TENSE_FORBIDDEN_FOR` is what guards them.
3. **Varies intraday** — kept, obviously.

**Cut as duplicates of a doctrine sentence:** `scale.move_30min_sigma_distribution`
(5 keys), `freshness_rules.present_tense_forbidden_for`, `data_sources.built_from`
(269 B, the largest single line item), `walls.ordered_by`,
`breadth.measures_same_gamma_pile_as`, `dealer_positioning.vanna_matters_when`,
`dealer_positioning.net_delta_sign_is_formula_artifact`,
`freshness_rules.{when_source_exceeds_max_age, only_the_options_book_has_a_ceiling,
max_options_book_age_min}`.

**Cut as standing facts about the instrument or the feed:** `instrument`,
`scale.expected_move_today_asym.derived_from`,
`data_sources.open_interest.{snapshot_is, updates_intraday}`.

**Cut as one-step derivable:** `regime.flip.{band_upper_edge_ct_sigma,
band_lower_edge_pt_sigma}` (ct ≡ centre+0.25 and pt ≡ centre−0.25 on 2,814/2,814
scans carrying a band, one distinct half-width — the WIDTH stays, so a ladder
that ever produced a different band would say so), `scale.sigma_pct_of_price`,
`price.live_minus_book_spot_dollars`, `clock.minutes_since_open` (sums with
`minutes_to_close` to 389 on 4,148/4,149).

**`sigma_measured_from` INVERTED.** sr-7 stamped it on regime, magnet and walls
every scan; it read `spot_when_book_was_measured` on 4,149/4,149. A tag that says
the same word every time is read past — the same failure sr-7 itself diagnosed
when it deleted the staleness label nobody discounted for. The rule moved to the
doctrine; the tag now ships ONLY on the fallback, where there was no chain_spot
and `price.live_minus_book_spot_sigma` is missing too. It fires zero times on the
recorded tape, which is what "exception" should mean.

**DELIBERATELY NOT CUT, though formally derivable.** Reasoning tokens are paid
for too, so re-deriving is a saving only when the arithmetic is one step AND the
read does not lean on the field:
`magnet.top_strike_lead_pp` (the cue for "no strike is really in charge"),
`walls[].sigma` (what the model judges what price will meet on — a wrong
distance costs more than the bytes), `clock.front_expiry.expiry_date`
(session_date + dte BUSINESS days; this module reads the prior session off the
diary precisely so a holiday cannot be handed over as a day that never traded).

**`net_delta_change_30min_bn` KEPT, on measurement.** It was suspected of being
the stock's own move re-scaled through yesterday's positioning, since OI is
frozen intraday while delta re-prices every tick. Measured against
`price.moved_last_30min_sigma`: r = +0.28 (r² = 0.077), and within-day it swings
−0.45 to +0.68 across 23 sessions. Not a duplicate.

**A THIRD CLOCK THAT WAS THE FIRST ONE.** Found while reviewing sr-8 itself, and
cut under the same rule: `data_sources.price_quote` claimed to be one of "three
separate clocks that disagree on purpose", but `quoted_at` equalled
`scan_taken_at` on 4,149/4,149 and `age_min` was 0.0 on 4,149/4,149, both being
computed from the row's own `ts`. sr-7 had already diagnosed this — its comment
rejecting a quote ceiling says "the diary carries no quote timestamp at all" —
and shipped the block regardless. Also cut:
`momentum.window_between_books.last_book_measured_at`, which equalled
`data_sources.options_book.measured_at` on 4,026/4,026.

**A NOTE ON THE RATE.** sr-7's comments, and sr-8's first drafts, justified
deletions with "paid for ~190 times a day". That is the SCAN rate. The wake gate
and `DAILY_CALL_CAP` mean the model itself speaks about **19** times a day —
measured, 391 calls over 21 sessions. The scan rate is not the billing rate.
Byte figures quoted in this section are compact JSON; production serialises with
`json.dumps(scene, default=str)`, which is about 7% larger (2,817 bytes at HEAD).

**One rename, no bytes saved:** `price.vwap_dist_sigma_from_live_spot` →
`price.vwap_minus_live_spot_sigma`. The old name said which spot it measured from
and never which way it pointed; 13 of 15 reviewers read the sign backwards. The
phone glance dodged it by rendering the vwap PRICE; the scene ships sigma
distances and cannot, so the name carries the subtraction — matching
`price.live_minus_book_spot_sigma` four lines above it.

A CONSEQUENCE WORTH NAMING: with its two constants gone, an `open_interest` block
that can say nothing measurable now vanishes rather than shipping a hollow shell.
It still ships on 4,148/4,149 recorded scans; the miss is the first session on
record, which has no prior session to name.

## sr-7 (2026-08-30) — provenance, and names that survive being read alone

Two findings drove a rename-and-provenance pass over the whole payload. Neither
added a measurement; both changed what the existing ones say about themselves.

**1. Nothing said when, or from what.** Zero of 53 leaves carried a source tag
or a measurement timestamp. The scene re-priced one overnight open-interest
snapshot on every one of ~190 daily scans against a live quote and presented the result in the
present tense. Every block declared `built_from` (`live_tape` /
`options_book` / `open_interest_snapshot` / `wall_clock`) — *sr-8 moved that map
out of the payload into the `BUILT_FROM` constant and the doctrine; nothing but
the freshness gate had ever read it, at 269 bytes a scan*; `data_sources`
carries the three clocks that were being conflated; and `freshness_rules`
**deletes** a block whose source has aged out rather than labelling it — a label
had already been tried and ignored.

**2. Distances lied by a spot.** Every σ distance divided a LIVE spot into a
book measured at a different spot — median $2.13 apart on cached rows, $41.99 at
worst. Book-derived distances now measure from `spot_when_book_was_measured`,
the blocks said so in `sigma_measured_from` — *sr-8 inverted that tag: the rule
is stated once in the doctrine and the tag now ships only on the fallback, since
it read the same word on 4,149 of 4,149 scans* — and
`price.live_minus_book_spot_sigma` converts between the two frames.

**And the cadence was wrong by 2x.** The feed serves a disk cache on half the
scans (measured 08-28: 188 scans, 94 distinct books), so "bigger than N% of
scans" counted every cached book twice. Percentiles now count distinct books —
`_prior_sessions` dedups on `meta.book_asof`, and `PCTL_CACHE_V` invalidates the
old on-disk distribution.

**Naming rule, applied to every leaf:** a name must survive being read alone,
out of context, by someone who has never seen the rest of the file. Units ride
on the name (`_pp`, `_bn`, `_musd`, `_min`, `_sigma`). `gex` → 
`share_of_book_gamma_pp`; `net` → `net_delta_bn`; `now` → `live_spot`;
`window` → `window_between_books`. The full old→new map is the table below.

## In the scene payload (sr-7 shape)

| Scene field | Source (row/derived) | Note |
|---|---|---|
| **data_sources** {scan_taken_at, scans_so_far_today, scan_interval_min, spot_feed, ~~price_quote {quoted_at, age_min, feed}~~, options_book {measured_at, age_min, served_from, is_repeat_of_previous_scan, cache_age_s, distinct_books_so_far_today, refresh_interval_min}, open_interest {~~snapshot_is~~, ~~updates_intraday~~, prior_session_date, measured_unchanged_so_far_today, strikes_compared_today}} | `meta.book_asof` / `cache_age_s` / `book_source` / `spot_source`, today's rows, `gex_views.oi_by_strike` | **NEW sr-7.** The three clocks, which are not the same clock. `meta.book_asof` and `cache_age_s` already existed on the row and were being dropped on the floor. `measured_unchanged_so_far_today` re-proves the OI-is-overnight claim on every scan, on the INTERSECTION of the two strike windows (the window walks with spot, so comparing raw surfaces would answer a question about the telescope). **sr-8 (08-30)**: there were only ever TWO clocks. `price_quote.quoted_at` equalled `scan_taken_at` on 4,149/4,149 recorded scans and `price_quote.age_min` was 0.0 on 4,149/4,149 — both came off the row's own `ts`, and sr-7 had already written down why ("the diary carries no quote timestamp at all") when it rejected a quote ceiling, then shipped the clock anyway. A 0.0-minute quote age reads as evidence the price is fresh when it is only evidence the row timestamped itself. `spot_source` survives alone as `data_sources.spot_feed` — the one part of the block that was never the scan clock |
| **freshness_rules** {when_source_exceeds_max_age, max_options_book_age_min, only_the_options_book_has_a_ceiling, blocks_dropped_this_scan, present_tense_forbidden_for} | `MAX_BOOK_AGE_MIN` (= `STALE_BOOK_MIN`) | **NEW sr-7.** Enforced, not advisory: `_drop_stale_blocks` deletes an aged-out block and names it in `blocks_dropped_this_scan`, landing the outage in the vocabulary the scene already has (absence = not measured). ONE ceiling, and it is the book's. A `MAX_QUOTE_AGE_MIN` of 2.0 was drafted and removed the same day: the diary carries no quote clock (`quoted_at` IS the scan timestamp), so it was a ceiling on the 120s scan cadence wearing a quote's name, and it deleted `price` on 42 of 3,409 recorded August scans — on healthy tape. Book age is never smaller than scan age, so the book ceiling already covers every case a real quote ceiling would. `open_interest` carries no ceiling for the opposite reason: it is ~18h old BY DESIGN and saying so is the fix |
| ~~instrument~~, ~~clock.minutes_since_open~~, clock {minutes_to_close, session_date, front_expiry {days_to_expiry, expiry_date}} | wall clock + `gex_views.front_dte` + `meta.expiries` | **sr-4 (08-10)**: the day-scoped calendar — the model could not tell a Monday 4-dte book from expiry Friday, could not use the history CLI's `--date` filters, and could not know a 15:45 call has no room to resolve. **sr-6 (08-11)**: + `book_age_min` — the book's own pulse; the reader never wakes the model past STALE_BOOK_MIN=6, rows land stamped `wake:"stale_book"`, and the marginal band stays visible in-scene instead of laundered. Weekday deliberately absent (≡ dte on every recorded session — one fact must not wear two names); 16:00 close is the standing RTH assumption, half-days owned by the open no-pulse blind spot. **sr-7 (08-30)**: `book_age_min` MOVED OUT to `data_sources.options_book.age_min`, and is now measured off `meta.book_asof` instead of the row's own `ts`. The old number was `now − scan`, which read 0–1 min on a book up to 3.4 min old, and the doctrine compounded it by telling the model every number was that many minutes stale — false in both directions. `clock` is now the SESSION calendar and nothing else. **sr-8 (08-30)**: `instrument` DELETED (the string "SNDK" from a reader that has never watched anything else — the doctrine opens on it); `minutes_since_open` DELETED (sums with `minutes_to_close` to 389 on 4,148/4,149, and the kept half is the one a 30-minute call is decided on). `front_expiry.expiry_date` was on the cut list and taken off it: session_date + dte is BUSINESS-day arithmetic, and handing holiday math to the model gives back exactly what `_prior_session_date` exists to prevent |
| scale.one_sigma_dollars / ~~sigma_pct_of_price~~ | `sigma` (σ-ruler: max(anchor, live), N10) | σ from rebuilt front-book ATM IV. **sr-8 (08-30)**: `sigma_pct_of_price` DELETED — it is `one_sigma_dollars` over `price.live_spot`, both shipped, and the doctrine states the 8–10% band this name existed to convey |
| scale.move_30min_sigma_distribution {half_of_windows_under, one_in_five_over, one_in_twenty_over, worst_recorded, sessions_measured} | measured spread, 8 sessions (replication 08-08: p50 0.094 / p95 0.46 / max 1.71) | **sr-5 (08-10)**: replaced `typical_30min_move_sigma: 0.08` — the single point was the model's one calibration hint and it copied it as a ceiling (33/33 emitted magnitudes inside 0.06–0.20 while a fifth of real windows exceed 0.20). Doctrine + voice doctrine restated as the spread. **DELETED sr-8 (08-30)**: five frozen literals from one 8-session study, identical on every scan, paid for at full payload price on every model call while the doctrine already restated all five in prose. The SPREAD lesson stands and is asserted against `_DOCTRINE` in the tests; only the second copy went. A standing recompute is still future work |
| scale.expected_move_today_asym {up/down_dollars, skewed_toward, ~~derived_from~~} | `range_ruler.em_points` split by `iv_skew.down_share` | **IV-skew split** (blueprint semantics); the pre-existing `adaptive_em` (realized-semivariance split) stays recorded on the row as shadow — divergence flagged in the 08-02 report. **sr-8 (08-30)**: `derived_from` DELETED — one frozen string, and the doctrine already names put/call IV skew as the source |
| price.live_spot / spot_when_book_was_measured / ~~live_minus_book_spot_dollars~~ / live_minus_book_spot_sigma / vs_prior_close_pct / session_low / high / moved_last_30min_sigma | `spot`, `meta.chain_spot`, `prior_close`, day path over rows | **sr-7 (08-30)**: `meta.chain_spot` was on the row and dropped. The two spots differ by a median $2.13 on cached rows and by $41.99 at worst; `live_minus_book_spot_sigma` is the one number that converts any book-measured σ distance into a distance from where price actually is — SUBTRACTED, not added. The doctrine said "add" for one afternoon, which walks the wrong way by twice the gap (0.18σ on the 08-28 11:04 row, ~0.8σ on the worst recorded row). The field was renamed to match its dollar twin so the direction rides on the name. **sr-8 (08-30)**: the dollar twin itself DELETED — the same quantity in the other unit, with both spots it differences in the same block. Sigma is the frame every other distance is already in, so sigma is the copy that survives |
| price.vwap_minus_live_spot_sigma | `vwap` (Schwab 1-min bars, Σp·v/Σv) vs LIVE spot on σ | the only σ distance in the scene measured off the live quote — VWAP is a live-tape level. **RENAMED sr-8 (08-30)** from `vwap_dist_sigma_from_live_spot`, which said which ruler it used and never which way it pointed: it is (vwap − live spot)/σ, so POSITIVE means vwap ABOVE price — price BELOW its average — the opposite sense to `moved_last_30min_sigma`. 13 of 15 reviewers read the sign backwards. The phone glance dodged it by rendering the vwap PRICE; the scene ships σ distances and cannot, so the name now carries the subtraction, matching `live_minus_book_spot_sigma` |
| regime.gamma_sign / regime_label | `gamma_sign`, `regime` | |
| regime.vol_trend {direction, iv_change_last_30min} | `atm_iv` across today's rows (30-min lookback) | vol pts; the switch that arms vanna/charm |
| regime.flip {~~band_upper_edge_ct_sigma~~, ~~band_lower_edge_pt_sigma~~, band_center_is_gamma_flip_sigma, edges_are_center_plus_minus_sigma, live_price_vs_band} \| {no_flip_anywhere_on_board} | `profile_ladder.ct/pt`, `gamma_flip`, ladder `state` | **live schema fact: `hvl` IS the gamma flip** (High Volatility Level), so center ≡ hvl — see flag below. **sr-5 (08-10)**: a measured book with no flip (gamma one-signed at every strike) ships `{none_on_board: true}` instead of vanishing — it read as "flip unknown" on 49% of scenes under a doctrine that calls absence "not measured". **sr-8 (08-30)**: the two EDGES deleted — ct ≡ centre+0.25 and pt ≡ centre−0.25 on 2,814/2,814 scans carrying a band, one distinct half-width, and sr-7's own doctrine already called them "arithmetic, not three independent levels". The WIDTH stays and is what makes it safe: it is the measured number, so a ladder producing a different band says so where a doctrine constant would quietly lie. A partial band (one edge, no width) keeps its raw edge — unreached on the tape, reachable in the code, now tested |
| regime.charm {magnitude_musd_per_day_uncalibrated, drifts_toward_strike} | `flows_front.cex` / `charm_wall` (front-book clock) | N11: no direction word ever (net charm structurally sign-locked); magnitude + target strike only; omitted when not cleanly computed |
| magnet {top_strikes [{strike, share_of_book_gamma_pp}], top_strike_lead_pp, top_strike_lead_vs_own_history, top_strike_sigma} | `gex_views.mass_by_strike` band | **sr-3 (08-08)**: `is_a_tie` DELETED — it was `gap_pp < MAGNET_SEP_PP`, a July constant (5.0pp) shipped as a finished verdict and true on ~95% of August scans, the third instance of the `dex_word` pattern. The lead is the evidence; `top_strike_lead_vs_own_history` grades it against prior sessions instead of a fixed cut — and **sr-7** grades it against DISTINCT BOOKS, since a cached book was voting twice |
| breadth {lopsidedness_0_is_even, heavier_side, ~~measures_same_gamma_pile_as~~, lopsidedness_vs_own_history} | `gex_views.shove` (`\|up−dn\|/max(\|up\|,\|dn\|)`) | **NEW sr-3.** The number existed only inside the aggregator, spent on one `>= 0.30` comparison and discarded — 0.299 and 0.001 both read "not admissible" and the model saw neither. Ships with NO direction (both conventions unestablished across 86 sign runs) and a note naming the magnet as the same witness (they agreed 69/69 on replay). **sr-8 (08-30)**: that note DELETED as a payload field — one frozen string, and the doctrine carries the same "one witness, not two" warning with room to say why |
| momentum {window_between_books {books_compared, span_min, first/last_book_measured_at}, by_strike share_of_book_gamma_change_pp / gross_volume_change_contracts} | `mass_by_strike` share deltas + `vol_gross_by_strike` deltas over the last 5 scans, intersection-denominated, window told on the BOOK clock | `oi_d` **omitted**: upstream OI updates once daily — an intraday oi_d is a permanent 0 (measured 07-30: OI@1300 unique value all day). `cvd` **omitted**: no bid/ask aggressor tape exists for SNDK stock (1-min bars only; a bar-direction proxy measures drift, not aggression — the 07-17 BVC lesson) |
| dealer_positioning.net_delta_bn / ~~net_delta_sign_is_formula_artifact~~ / net_delta_change_30min_bn | `dex_views.net_dex_total` + timestamp-true 30-min Δ across rows | assumed-sign book, $bn. **No lean word** — the adversarial audit measured a sign-derived word constant on 756/756 scenes, the exact banned pattern; the change is the signal and now actually ships. **sr-3 (08-08)**: the doctrine's gloss "+ = dealers net long delta" was the same constant wearing a definition — `net_dex_total > 0` BY CONSTRUCTION (`lefteye_dex.py:22`), measured positive on 1,675/1,675 recorded rows. Doctrine now names the constancy and `note` rides the field itself; the level stays only because the 30-min Δ is measured off it. **sr-8 (08-30)**: the flag REMOVED from the field and returned to the doctrine alone — `True` on 4,149/4,149, level positive on all of them (1.15–8.24, never crossing zero), and the doctrine can name the measurement where a boolean could only assert it. `net_delta_change_30min_bn` was suspected of being the price move re-scaled through frozen OI and was MEASURED before being kept: r = +0.28 (r² = 0.077) against `moved_last_30min_sigma`, within-day −0.45 to +0.68 across 23 sessions. Not a duplicate |
| dealer_positioning.net_vanna_musd_per_vol_point / ~~vanna_matters_when~~ | `flows_front.vex` (front-book clock) | omitted when not cleanly computed; block dropped when empty. **sr-7**: the `dex`/`vanna` nesting is flattened and the two `note` strings became named leaves — a `note` key tells a reader nothing about what it notes. **sr-8 (08-30)**: `vanna_matters_when` DELETED — one frozen string the doctrine already states |
| walls {call[2], put[2]} each {strike, sigma, cluster_share_of_book_gamma_pp, unchanged_for_min \| unchanged_for_at_least_min} + {call,put}_heaviest_wall_behind_the_ladder + {call,put}_side_has_no_wall | `gw_vocab.cluster_walls(net_by_strike)` nearest-first per side | `gex` = cluster share of total \|net γ\| in % (scale-free); walls.call[0] ≡ old `gwc`, walls.put[0] ≡ old `gwp`. **sr-3 (08-08)**: selection UNCHANGED (side-by-gamma-sign AND side-of-spot), but (a) each wall now carries its own age measured on the numbers the scene ships — `frozen_fields` probes the diary's scalar `call_wall`/`put_wall` and on 08-06 warned about put wall 1200, a strike absent from the entire scene, while `walls.put[0]=1250` at −0.15σ went unlabelled; an age still standing at the 120-row lookback reports as `_at_least`, censored not silent; (b) the ladder is ordered by DISTANCE, so a side's heaviest cluster can sit third and never ship — 08-06 cut put 1150 (gex 7.6, the heaviest) while the doctrine called `walls.put[0]` (gex 6.8) "the strongest" — so the heaviest now ships separately when it is not in the ladder, and the doctrine no longer claims [0] is strongest. **sr-5 (08-10)**: a measured board whose side holds nothing between price and open air ships `{call,put}_side_has_no_wall: true` — measured-empty is a finding, and deleting the key had made it indistinguishable from a torn feed |
| ~~named_levels_sigma_from_spot {ct, hvl, pt}~~ | — | **DELETED sr-3 (2026-08-08)**: a duplicate of `regime.flip`. Measured over the 923 recorded rows carrying the family, `profile_ladder.hvl == gamma_flip` on 923/923 and `(ct−flip)/σ, (pt−flip)/σ` take exactly one value, `(+0.25, −0.25)` — so these were flip_block's own three numbers a second time, presenting one measurement as three levels agreeing. Cited 0 times in 61 sr-2 readings |
| ~~lowest_named_level~~ | — | **DELETED sr-3**: `min()` of the duplicate above |
| **context** {vs_prior_sessions {top_strike_lead_pp, lopsidedness}, changed_since_last_book {gamma_sign, heaviest_strike, nearest_call_wall, nearest_put_wall}} | `_prior_sessions` day medians + the last two DISTINCT books | **NEW obs-2 (08-31)**. The only thing Python still decides for the model, and it decides nothing: the model reads one snapshot, so it cannot know how today ranks against closed sessions or what moved since the last book. Those are facts about TIME and the payload has none. States facts, never verdicts — a rank, not "extreme"; a was/now, not "changed_this_scan". Ranked one value per SESSION, because 94-98.5% of the variance in these is between days and a per-scan rank lets one unusual session vote ~90 times |
| context.since_last_read {last_read_at, minutes_since, spot_then/now, spot_change_dollars, why_this_read, crossed_since_then [{level, was_labelled_then, price_went}] \| nothing_crossed_since_then, unchanged_since_then, range_since {low, high}, session_range_unbroken_since \| first_read_of_session} | `frame_since_last_read` over the last row that SPENT a call — crossings tested against that row's frozen `gate` snapshot | **NEW obs-3 (09-01)**. The bridge every reading opens with. Every number a model might quote ships pre-computed, because the doctrine forbids model arithmetic and the number gate deletes what is not on the board — without this block the honest "price has held between X and Y since HH:MM" sentence was deleted and logged as a forced abstain. The gamma flip is deliberately NOT in the crossed list: its uncertainty exceeds its distance from spot, and freezing it does not cure that | **obs-4 (09-02)**: `held_between_since_last_read` seeded with spot_then; `unchanged_since_then` walls ladder-to-ladder via `ladder_nearest`; new `spot_change_sigma`, `frame_is` (a move ≥0.15σ / a hold), `walls_absent_then_and_now`; the gate never freezes a diary wall when a surface exists.
| **data_sources.minute_bars** {bars_so_far_today, last_bar_at, age_min} · **price.extremes_from** · **context.ranges.measured_from** | `state/sndk_bars/<day>.jsonl` (the minute-bar sidecar, `sndk_bars.py`, own launchd job) | **NEW obs-5 (09-02)**: session_low/high and the boxes come from the minute wicks when the file exists (live spot folded in), from the 2-minute scans otherwise; the label says which. Prior sessions' range prefers a day's bars file when it holds ≥300 minutes (`mixed` when days differ). |
| **structure** {bands [{low, high, share_of_book_gamma_pp, side}], air [{side, from, to}], weight_above_spot_pp} | `gex_views.net_by_strike` through `gw_vocab.cluster_walls`, measured from the book's spot | **NEW obs-4 (09-02)**: where the weight SITS — the span of a multi-strike band (the ladder ships only the peak), thin gamma between price and the nearest wall, the share above price. Measured before build: no attraction to bands, no extra hold for a dominant strike (23 sessions). OI-derived → present-tense forbidden, dropped with the book. |
| **context.ranges** {opening {low, high, formed_over, status \| still_forming}, in_force {low, high, formed_over, is_the_opening_box, live_spot_is \| still_forming, since, replaced_a_box_broken_at}, breaks_today {count, breaks [{at, went, box_low, box_high}]}, prior_sessions {sessions, from, to, low, high, measured_from, live_spot_is, today_traded_beyond_it, todays_opening_box_is}} | today's scans + the prior 5 sessions' diary files (never today's own) | **NEW obs-4 (09-02)**: the day as boxes. Opening box = first 30 min; a frozen box breaks past 0.05σ; a break starts a new box that forms over 30 min. LIVE-TAPE sourced, so it survives a stale book. Box edges are admitted as pointable levels. |
| history {price_at_level_unseen_earlier_today, tape_abnormal_vs_own_history} | today's rows (spot beyond all prior scans = new session ground; ≥1.5σ day move on the stock's OWN ruler or ≥0.5σ/30min = abnormal) | the under-pull flags; block omitted when nothing to say. σ-relative on purpose: a fixed 8% is ~1σ on this name and fired on 56% of recorded scans |
| frozen_do_not_cite | `frozen_fields` over today's rows, **minus the wall entries (sr-3)** | guardrail, kept — but it may only name fields the scene actually ships. Wall staleness moved onto the wall itself; the ROW's frozen list is untouched, since the chart greys diary fields and is right to keep describing them |

**Removed from the payload:** `arrow_already_decided` (direction, silent_because,
layer cites) — the verdict anchored the model. The deterministic arrow itself
outlived that exclusion by a month and was then deleted whole (Lane A removal,
obs-3 2026-09-01): rows written before that date carry an `arrow` field as
history, and nothing computes or draws one any more.

## On the diary row but NOT in the payload (and why)

| Property | Why it stays out |
|---|---|
| `gex_views.pin_* / zone1_share / pin_basis / regime_basis` | measured constants across 756 rows (INADMISSIBLE) — a constant cannot inform |
| `dex_views.dex_word / dex_flow_word / dex_flow_signed` | INADMISSIBLE (constant string / muted flow read) |
| `gex_views.net_gex / net_gex_tenor / gross_gex` | magnitude uncalibrated; regime word already carries the sign; raw $ units are unit-opaque to the model |
| `gex_views.net_by_strike / mass_by_strike / oi_by_strike / vol_*_by_strike` (raw arrays) | view surfaces — the payload carries their *derived* reads (walls ladder, magnet band, momentum deltas); raw 29–78-element arrays dilute the read |
| `gex_views.shove` (raw margins) | the raw up/down margins stay out; **sr-3 ships their RATIO as `breadth.lopsidedness_0_is_even`** — a magnitude with no direction, so no vote is smuggled (the sign convention stays unestablished, per README) |
| `gex_views.vex/cex` (whole-book SPX-clock) | replaced in-payload by `flows_front` on the SNDK front-book clock; SPX-clock cex is 0DTE-only (absent 4 of 5 days) |
| `gex_views.flip_band / flip_tenor / regime_tenor / regime_source` | flip band ships as ct/pt already; tenor twins are display honesty fields |
| `dex_views.net_dex_by_strike / dex_above/below_spot / basis / n` | per-strike view surface + provenance; headline net is the read |
| `adaptive_em` (semivar split) | superseded in-payload by IV-skew aem per blueprint; stays recorded (shadow) for the A/B — flagged |
| `net_exposure` (incl. prev_close twins) | viewstation tile; day-over-day gamma/delta context belongs to the RAG month tier, not every snapshot |
| `range_ruler.em_open / em_consumed / spent_pts / anchor / quality` | ruler provenance; em_points rides inside aem; em_consumed already voted inside the regime word |
| `regime_conf / regime_reads` | vote plumbing behind the regime word |
| `call/put_wall_tenor` | dated-tenor context — month-tier terrain, not the live book |
| `sigma_live / sigma_anchor / atm_iv` | σ plumbing; the payload's σ is the ruler; atm_iv feeds vol_trend |
| `meta.*` (coverage, discovery, iv counters) | feed provenance/health — pager territory, not market evidence. **AMENDED sr-7 (08-30)**: `book_asof`, `cache_age_s`, `book_source`, `spot_source` and `chain_spot` are no longer in this category and now ship in `data_sources` / `price`. The old line was the mistake: when half the scans re-serve a cached book and the two spots disagree by up to $42, provenance IS market evidence — it decides what every other number is worth |
| `prior_close` (raw) | ships as vs_prior_close_pct |
| `vwap` (raw $) | ships as `price.vwap_minus_live_spot_sigma` (scale-free) |
| `iv_skew` (raw put/call IVs) | ships as aem split + skew word |
| `flows_front` raw basis/counters | provenance for the charm/vanna reads |

## Read-row fields (never in the payload)

`magnet_band`, `frozen`, `wake`, `gate` (the frozen snapshot the NEXT wake
gate and the obs-3 frame measure from), `scans`, `reading` (+ `reading_ts`,
`reading_age_min`, `quiet`, `abstain`), `book_asof`/`book_age_min`/
`scan_age_min`, `paused`, `wall_s/model/error` — these are the *output*
surface (chart + training store), not model input. Rows written before obs-3
(2026-09-01) also carry `arrow` (dir/state/since/run/fading/caution/layers/
ghost) and `spoke`; those are history — Lane A is deleted and nothing writes
them any more.

## Verification log (08-02, pre-deploy)

Six agent passes ran against this build; artifacts of record:

1. **Software-engineer review** — verified units/signs/off-by-ones/isolation
   and the `--allowedTools` grant against the installed CLI tokenizer; found
   3 MED (walls gex denominator, rollup tmp-file race, rollup live-day
   bypass) + lows — ALL fixed same session, each with a regression test
   marked "SE review 08-02" in code comments.
2. **UI-hardening agent** — 50/50 execution checks via JavaScriptCore against
   real ROW_V 2, synthetic ROW_V 3, and real sr-2 read rows; served the SPA
   and hit the reasoning endpoint; added one boundary guard (vector enum
   re-check in snkVectorRow); post-edit full-script syntax check clean.
3. **Blueprint fidelity audit** — verdict: faithful replica; per-field table;
   found the nested-null keep-field leak (fixed: build_scene prunes inside
   blocks, pinned by test_no_nulls_anywhere_in_the_scene) and that the
   artifact's own put-wall#2 σ was arithmetically wrong (−0.26 correct).
4. **Playbook/§05 compliance audit** — 13/16 PASS, 3 PARTIAL, 0 FAIL;
   PARTIALs closed or accepted-with-reason (series gained --strike; this log
   IS the UI-attestation artifact; the "agentic pipeline adds reasoning on
   top" clause is deliberately unimplemented — Face B reuses the model's own
   per-slice sentence so memory costs no extra call; revisit if a nightly
   second-pass summarizer is ever wanted).
5. **Adversarial unbiased-payload pressure test** — ran the real build_scene
   over all 756 recorded rows + a 22-case synthetic battery; core decoupling
   held (no aggregate/shove/dwell output in any scene, all INADMISSIBLE
   fields absent, read-words genuinely vary), and its 5 confirmed findings
   were fixed same session — see standing-flag #5 below for the list.
6. **End-to-end live proof** — one real `claude -p` call in a sandboxed
   state dir: vector=down 0.15σ returned, validated, RAG slice landed;
   replay of 4 recorded days reproduces sr-1 wake/arrow counts exactly;
   suites green (135 sndk-pro / 631 left-eye).

Letter-vs-intent deltas accepted by design (a strict blueprint reader could
challenge; all deliberate): the static history signpost lives in the CACHED
doctrine with only dynamic flags in the payload; Face B narratives are
embedded at query time, not stored as vectors; momentum's key is
`momentum.by_strike.share_of_book_gamma_change_pp` (scale-free share), not raw `gex_d`.

## Flags standing after the 08-02 build

1. **hvl naming**: blueprint glossary calls hvl a volume-profile node; in this
   system `profile_ladder.hvl` IS the gamma flip (display name "High Volatility
   Level"). There is no equity volume-profile shelf anywhere in the SNDK row;
   vwap is the only volume-anchored level. `flip.center_sigma` ≡ `hvl` by
   construction — kept both per blueprint shape.
2. **aem source**: live `adaptive_em` splits by realized downside semivariance,
   not IV skew. The payload's aem now uses the rebuilt-quote IV smile
   (down-side vs up-side mean IV within the reach window) so it matches the
   blueprint's stated source; the semivar split keeps recording beside it.
3. **cvd omitted** (no aggressor tape for the stock), **oi_d omitted**
   (OI static intraday upstream) — omit-never-null.
4. **dex lean word REMOVED** (superseding the earlier keep-with-caution): the
   adversarial audit measured the sign-derived word constant on 756/756
   simulated scenes with no channel through which the model could observe
   the "change" the caution pointed at — the banned-constant pattern. The
   payload now ships the signed number + a measured net_change_30min; the
   sign convention is glossed once in the cached doctrine. (A deliberate
   deviation from the blueprint's §01 `lean` key, on the codebase's own
   pre-registered standard.)
5. **Adversarial round (08-02) closed before first live**: aggregator arrow +
   arrow-naming wake strings stripped from RAG slice metadata (the stripped
   verdict must not be one Bash call away); empty/single-strike books make no
   tie claim (a bare `is_a_tie: true` was manufactured/inverted evidence);
   NaN/malformed inputs skip instead of fabricating confidence or crashing;
   `moved_last_30min_sigma` now rides the timestamp-true window (count-based
   path[-15] disagreed 20/700, once by 0.81σ); forced off-hours rows excluded
   from the reader's session view (07-28's 00:05 warmup overstated
   session_high ~0.7σ all day); momentum "fading" earns the same vol hedge as
   "building"; doctrine gained the mean-reversion caution and the
   price_in_band location-not-stamp gloss.
