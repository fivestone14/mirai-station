# SPX Watchtower scene inventory — in the payload vs not (living list)

Scope: **SPX only** (the wt-11 era, 2026-08-10 — the SNDK sr-2→sr-5 port,
every threshold re-measured on this tape). The scene payload is what
`watchtower.build_payload` hands the model each judged scan. Everything else
recorded on the diary row is view/telemetry/training surface, deliberately NOT
reasoning input. This list is the pressure-test shortlist: anything marked
*not in payload* is a candidate to argue back in.

Sources: diary row = `reversion_lens.evaluate` (telemetry), scene =
`watchtower.build_payload` (PROMPT_VERSION wt-11), memory =
`lefteye_rag.py` → `state/spx_rag/`. The SNDK twin of this document is
`docs/sndk-payload-inventory.md` — the two scenes are deliberately parallel
where the tapes allow it, and deliberately different where they don't
(0DTE daily vs weekly expiries; the SPX scene is THOROUGH by doctrine where
the SNDK scene is lean).

## Payload hygiene (the standing rules)

- **No absolute index levels, ever** — every level is a signed σ-distance from
  spot (models have SPX history memorized; test-pinned in
  `test_clock_carries_the_date_and_no_absolute_levels_leak`).
- **The date ships since wt-11** — a documented amendment: live dates sit past
  the pinned model's cutoff, and the history CLI's `--date` filters are
  unusable without today's date. Levels stay banned; the model meets absolute
  strikes only if it *chooses* to query history.
- **Omit-never-null** — an absent field means *not measured*; a
  `wall_cluster_*_clear` flag means *measured empty* (sr-5). The doctrine
  forbids confusing the two.
- **No constants wearing signal clothes** — a field measured (near-)constant
  across the recorded rows may not ship as if it varied. This retired
  `naive_net_sign`, `vanna_sign`/`charm_sign`, and `charm_drift_into_close`
  in wt-11 (details below).

## wt-11 additions (each measured-or-absent)

| Scene field | Source | Note |
|---|---|---|
| clock.date | row ts | sr-4 port; the amendment above |
| scale.move_30min_sigma {half_under 0.08, one_in_five_over 0.15, one_in_twenty_over 0.27, worst_recorded 0.46, sessions_measured 30} | measured spread, 30 sessions / 336 disjoint windows (06-25→08-10) | sr-5: a spread teaches a distribution; a point teaches a ceiling. `tape.moved_last_30min_sigma` (timestamp-true) is the live number beside it |
| scale.aem {up_sigma, down_sigma, source} | `adaptive_em` | shipped ONLY when `asym_source != "watchtower"` — the tower must never eat its own called range as evidence (feedback loop) |
| dealer_map_gravity.magnet_band {gap_pp, top_strikes [[σ, share%]]} | `gex_views.mass_by_strike` | the tie in numbers — median top1−top2 gap 0.99pp (n=4,258 scans); pin_top_share alone hid it. Strikes as σ-distances only |
| dealer_map_gravity.charm {strength, funnels_toward_sigma} · vanna {strength} | `gex_views.cex/vex/charm_wall` | N11's final form — neutral magnitude + level, NO sign field, NO direction word (cex_sign was 97% one-way, vex_sign 96%, and charm_word_ok never once opened on 5,176 rows) |
| dealer_delta_dex.net_change_30min_bn | today's rows, timestamp-true (28–45 min window) | the one part of the naive dex level that can be news |
| momentum {window, by_strike strike_at_±σ → gex_share_d_pp / vol_d / read} | `mass_by_strike` + `vol_gross_by_strike` deltas over the last 5 scans, intersection-denominated | build/fade bar 0.15pp ≈ p75 of the recorded \|Δshare\| (n=4,064); a window shift is the telescope, not flow |
| tape.vol_trend {direction, iv_change_last_30min} | `atm_iv` (new row field) / derived from `sigma_live` on older rows | the switch that arms vanna/charm; flat band 2.0 vol pts ≈ p72 (n=3,390); omitted on a `late_day` ruler (the τ→0 solve balloons on clock mechanics) or an outage-shaped window |
| wall_cluster_above_clear / wall_cluster_below_clear | `_gw_cluster_read` | sr-5 measured-empty: the surface WAS read and the side holds nothing — open air is a reading, often the loudest |
| history {level_unseen_today, abnormal_tape} | today's rows | the under-pull guard licensing the two tools; abnormal = ≥1.5σ day move or ≥0.5σ/30min (≈2× the recorded p95) — rare by construction |

## wt-11 removals (constants retired)

| Field | Why it left |
|---|---|
| `dealer_delta_dex.naive_net_sign` (`dex_word`) | `net_dex_total > 0` BY CONSTRUCTION — positive on 2,627/2,627 recorded rows. wt-8 demoted it with a caveat; the SNDK sr-3 lesson is that a demoted constant is still a constant in the room. The magnitude line now names the formula-fixed sign in place |
| `vanna_sign` / `charm_sign` | near-constants (96%/97% one-way) — a constant that reads like a signal manufactures conviction |
| `charm_drift_into_close` | its `charm_word_ok` gate opened on 0 of 5,176 recorded rows — a word that never ships is dead weight; and the sign it would speak from is the locked one above |

## The memory (sr-2 port)

Exactly two tools are granted per call (`_ALLOWED_TOOLS`): the
`lefteye_rag.py` CLI as one allow-listed Bash prefix, and WebSearch for the
abnormal-tape catalyst check. Everything else stays stripped (`DENY_TOOLS` —
the 07-31 A/B's cost finding holds; its pre-registered fired-rate kill
condition keeps running under this era).

- One slice per judged verdict → `state/spx_rag/slices/{date}.jsonl` —
  Face A = filterable metadata (levels, σ, the tower's own vector/stance),
  Face B = the tower's scene sentence. **Head A's verdict is never stored**
  (the 08-02 anchoring rule, ported); gate-naming interest strings neutralize
  to "gate event".
- Day summaries (numeric spine + ≤3 stitched slice sentences) and month
  terrain (recent/all_time split) self-build on query; the reversion diary
  stays the plain numeric store (`series`, with `--strike`).
- Doctrine: history is context, NEVER the trigger — the live scene stays
  primary, and a recalled read is the tower's own past opinion, not a fact.

## Long-standing blocks (unchanged in wt-11)

task · clock minutes · scale.points_per_sigma · dealer_map_gravity
(gamma_sign word, net_gex_binding, regime_intensity_1d, pull_toward_magnet,
magnet/fade targets, flip + chop band + measured cT/pT fences, walls +
GW clusters + air pockets, structural/dated walls, profile_clean,
pin_top_share) · live_flow · flow_confirmation · strike_tags ·
scheduled_event · wall_breach_event · containment_road · order_flow ·
gravity_motion · terrain_asymmetry · break_lens_head_c ·
siege_effort_at_walls · dated_structure · tape (stretches, range vs EM,
√time reach decay, variance ratio, VIX TS, tape_speed) · day_context
(macro_mood, session, my_recent_verdicts, lifetime_scorecards) ·
gates_verdict_head_a (reveal pass only).

## On the diary row but NOT in the payload (and why)

| Property | Why it stays out |
|---|---|
| `dex_views.dex_word / dex_flow_by_strike` (raw array) | the word is the retired constant; the per-strike array is a view surface — `strike_tags` D± carries its derived read |
| `gex_views` raw arrays (net/oi/vol/mass_by_strike…) | view surfaces — the payload carries derived reads (clusters, tags, magnet band, momentum) |
| `adaptive_em` when `asym_source == "watchtower"` | the tower's own prior — feedback, not measurement |
| `profile_ladder.ct/pt/hvl` | the viewstation's chop-band surface; ct/pt are flip ± 0.25σ ARITHMETIC (2,627/2,627 rows) — the payload's fences are the *measured* pair from `net_by_strike` instead |
| `sigma_live / sigma_anchor / atm_iv` | σ plumbing; atm_iv feeds vol_trend |
| `vwap` (raw $) | ships as `tape.vwap_stretch_sigma` (scale-free); the raw level is the viewstation's to draw |
| `net_exposure` raw totals | `regime_intensity_1d` is its derived read |
| `regime_conf / regime_reads / regime_tie_guard` | vote plumbing behind the regime word |
| `meta.*` (coverage, source notes…) | feed provenance/health — pager territory |

## Verification (08-10, pre-deploy)

Built and dry-verified against a real recorded session (2026-08-07): all new
blocks present, all retired fields absent, no raw index level anywhere in the
scene. 18 new tests in `tests/test_watchtower_wt11.py`; 33 in
`tests/test_lefteye_rag.py`; full suites green. Multi-agent verification
round logged in the wt-11 commit series.
