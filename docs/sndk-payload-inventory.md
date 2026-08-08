# SNDK property inventory — in the scene payload vs not (living list)

Scope: **SNDK only** (blueprint `mirai-sndk-payload-blueprint.html`, draft v3,
applied 2026-08-02). The scene payload is what `sndk_read.build_scene` hands the
model. Everything else recorded on the diary row is view/telemetry/training
surface, deliberately NOT reasoning input. This list is the pressure-test
shortlist: anything marked *not in payload* is a candidate to argue back in.

Sources: diary row = `sndk_views.build_row` (ROW_V 3), read row =
`sndk_read.read_once` (ERA sr-3), chain = `sndk_feed.sndk_chain`.

## In the scene payload (v2 — the blueprint shape)

| Scene field | Source (row/derived) | Note |
|---|---|---|
| instrument, clock.minutes_since_open | constant / wall clock | |
| scale.one_sigma_dollars / sigma_pct_of_price | `sigma` (σ-ruler: max(anchor, live), N10) | σ from rebuilt front-book ATM IV |
| scale.typical_30min_move_sigma | measured constant 0.08 (756-row study) | honesty yardstick, not a signal |
| scale.aem {up/down_dollars, skew, source} | `range_ruler.em_points` split by `iv_skew.down_share` | **IV-skew split** (blueprint semantics); the pre-existing `adaptive_em` (realized-semivariance split) stays recorded on the row as shadow — divergence flagged in the 08-02 report |
| price.now / vs_prior_close_pct / session_low / high / moved_last_30min_sigma | `spot`, `prior_close`, day path over rows | |
| price.vwap_dist_sigma | `vwap` (Schwab 1-min bars, Σp·v/Σv) vs spot on σ | NEW row field |
| regime.gamma_sign / word | `gamma_sign`, `regime` | |
| regime.vol_trend {direction, iv_change_last_30min} | `atm_iv` across today's rows (30-min lookback) | vol pts; the switch that arms vanna/charm |
| regime.flip {ct_sigma, pt_sigma, center_sigma, price_in_band} | `profile_ladder.ct/pt`, `gamma_flip`, ladder `state` | **live schema fact: `hvl` IS the gamma flip** (High Volatility Level), so center ≡ hvl — see flag below |
| regime.charm {magnitude, drift_toward} | `flows_front.cex` / `charm_wall` (front-book clock) | N11: no direction word ever (net charm structurally sign-locked); magnitude + target strike only; omitted when not cleanly computed |
| magnet {gap_pp, gap_vs_own_history, top_strikes, sigma_from_spot} | `gex_views.mass_by_strike` band | **sr-3 (08-08)**: `is_a_tie` DELETED — it was `gap_pp < MAGNET_SEP_PP`, a July constant (5.0pp) shipped as a finished verdict and true on ~95% of August scans, the third instance of the `dex_word` pattern. The gap is the evidence; `gap_vs_own_history` grades it against prior sessions instead of a fixed cut |
| breadth {lopsidedness, heavier, note, vs_own_history} | `gex_views.shove` (`\|up−dn\|/max(\|up\|,\|dn\|)`) | **NEW sr-3.** The number existed only inside the aggregator, spent on one `>= 0.30` comparison and discarded — 0.299 and 0.001 both read "not admissible" and the model saw neither. Ships with NO direction (both conventions unestablished across 86 sign runs) and a note naming the magnet as the same witness (they agreed 69/69 on replay) |
| momentum {window, by_strike gex_share_d_pp/vol_d + read} | `mass_by_strike` share deltas + `vol_gross_by_strike` deltas over the last 5 scans (~10 min), intersection-denominated | `oi_d` **omitted**: upstream OI updates once daily — an intraday oi_d is a permanent 0 (measured 07-30: OI@1300 unique value all day). `cvd` **omitted**: no bid/ask aggressor tape exists for SNDK stock (1-min bars only; a bar-direction proxy measures drift, not aggression — the 07-17 BVC lesson) |
| dealer_flow.dex {net, note, net_change_30min} | `dex_views.net_dex_total` + timestamp-true 30-min Δ across rows | assumed-sign book, $bn. **No lean word** — the adversarial audit measured a sign-derived word constant on 756/756 scenes, the exact banned pattern; the change is the signal and now actually ships. **sr-3 (08-08)**: the doctrine's gloss "+ = dealers net long delta" was the same constant wearing a definition — `net_dex_total > 0` BY CONSTRUCTION (`lefteye_dex.py:22`), measured positive on 1,675/1,675 recorded rows. Doctrine now names the constancy and `note` rides the field itself; the level stays only because the 30-min Δ is measured off it |
| dealer_flow.vanna {net, note} | `flows_front.vex` (front-book clock) | omitted when not cleanly computed; block dropped when empty |
| walls {call[2], put[2]} each {strike, sigma, gex, unchanged_min \| unchanged_min_at_least} + {call,put}_heaviest_behind | `gw_vocab.cluster_walls(net_by_strike)` nearest-first per side | `gex` = cluster share of total \|net γ\| in % (scale-free); walls.call[0] ≡ old `gwc`, walls.put[0] ≡ old `gwp`. **sr-3 (08-08)**: selection UNCHANGED (side-by-gamma-sign AND side-of-spot), but (a) each wall now carries its own age measured on the numbers the scene ships — `frozen_fields` probes the diary's scalar `call_wall`/`put_wall` and on 08-06 warned about put wall 1200, a strike absent from the entire scene, while `walls.put[0]=1250` at −0.15σ went unlabelled; an age still standing at the 120-row lookback reports as `_at_least`, censored not silent; (b) the ladder is ordered by DISTANCE, so a side's heaviest cluster can sit third and never ship — 08-06 cut put 1150 (gex 7.6, the heaviest) while the doctrine called `walls.put[0]` (gex 6.8) "the strongest" — so the heaviest now ships separately when it is not in the ladder, and the doctrine no longer claims [0] is strongest |
| ~~named_levels_sigma_from_spot {ct, hvl, pt}~~ | — | **DELETED sr-3 (2026-08-08)**: a duplicate of `regime.flip`. Measured over the 923 recorded rows carrying the family, `profile_ladder.hvl == gamma_flip` on 923/923 and `(ct−flip)/σ, (pt−flip)/σ` take exactly one value, `(+0.25, −0.25)` — so these were flip_block's own three numbers a second time, presenting one measurement as three levels agreeing. Cited 0 times in 61 sr-2 readings |
| ~~lowest_named_level~~ | — | **DELETED sr-3**: `min()` of the duplicate above |
| history {level_unseen_today, abnormal_tape} | today's rows (spot beyond all prior scans = new session ground; ≥1.5σ day move on the stock's OWN ruler or ≥0.5σ/30min = abnormal) | the under-pull flags; block omitted when nothing to say. σ-relative on purpose: a fixed 8% is ~1σ on this name and fired on 56% of recorded scans |
| frozen_do_not_cite | `frozen_fields` over today's rows, **minus the wall entries (sr-3)** | guardrail, kept — but it may only name fields the scene actually ships. Wall staleness moved onto the wall itself; the ROW's frozen list is untouched, since the chart greys diary fields and is right to keep describing them |

**Removed from the payload:** `arrow_already_decided` (direction, silent_because,
layer cites) — the verdict anchored the model. The deterministic arrow is still
computed every scan, recorded on the read row, and drawn on the chart —
payload and arrow are decoupled paths.

## On the diary row but NOT in the payload (and why)

| Property | Why it stays out |
|---|---|
| `gex_views.pin_* / zone1_share / pin_basis / regime_basis` | measured constants across 756 rows (INADMISSIBLE) — a constant cannot inform |
| `dex_views.dex_word / dex_flow_word / dex_flow_signed` | INADMISSIBLE (constant string / muted flow read) |
| `gex_views.net_gex / net_gex_tenor / gross_gex` | magnitude uncalibrated; regime word already carries the sign; raw $ units are unit-opaque to the model |
| `gex_views.net_by_strike / mass_by_strike / oi_by_strike / vol_*_by_strike` (raw arrays) | view surfaces — the payload carries their *derived* reads (walls ladder, magnet band, momentum deltas); raw 29–78-element arrays dilute the read |
| `gex_views.shove` (raw margins) | the raw up/down margins stay out; **sr-3 ships their RATIO as `breadth.lopsidedness`** — a magnitude with no direction, so no vote is smuggled (the sign convention stays unestablished, per README) |
| `gex_views.vex/cex` (whole-book SPX-clock) | replaced in-payload by `flows_front` on the SNDK front-book clock; SPX-clock cex is 0DTE-only (absent 4 of 5 days) |
| `gex_views.flip_band / flip_tenor / regime_tenor / regime_source` | flip band ships as ct/pt already; tenor twins are display honesty fields |
| `dex_views.net_dex_by_strike / dex_above/below_spot / basis / n` | per-strike view surface + provenance; headline net is the read |
| `adaptive_em` (semivar split) | superseded in-payload by IV-skew aem per blueprint; stays recorded (shadow) for the A/B — flagged |
| `net_exposure` (incl. prev_close twins) | viewstation tile; day-over-day gamma/delta context belongs to the RAG month tier, not every snapshot |
| `range_ruler.em_open / em_consumed / spent_pts / anchor / quality` | ruler provenance; em_points rides inside aem; em_consumed already voted inside the regime word |
| `regime_conf / regime_reads` | vote plumbing behind the regime word |
| `call/put_wall_tenor` | dated-tenor context — month-tier terrain, not the live book |
| `sigma_live / sigma_anchor / atm_iv` | σ plumbing; the payload's σ is the ruler; atm_iv feeds vol_trend |
| `meta.*` (coverage, discovery, iv counters, book_source…) | feed provenance/health — pager territory, not market evidence |
| `prior_close` (raw) | ships as vs_prior_close_pct |
| `vwap` (raw $) | ships as vwap_dist_sigma (scale-free) |
| `iv_skew` (raw put/call IVs) | ships as aem split + skew word |
| `flows_front` raw basis/counters | provenance for the charm/vanna reads |

## Read-row fields (never in the payload)

`arrow` (dir/state/since/run/fading/caution/layers/ghost), `magnet_band`,
`frozen`, `wake`, `spoke/scans`, `reading`, `paused`, `wall_s/model/error` —
these are the *output* surface (chart + training store), not model input.

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
`gex_share_d_pp` (scale-free share) not raw `gex_d`.

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
