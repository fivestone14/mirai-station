# wt-8 Doctrine — Variable Weighting for the Watchtower GEX-Profile Read

**Era: wt-8 (regime-first). Ruled 2026-07-25.**
Sources: TanukiTrade "How to Read GEX Step by Step" (Gery Nagy, verified
full-text 07-25) + Mirai's own backtests (07-17 32-day SPY 1-min sweep;
07-12 pressure test; gamma-tape audit 07-12) + the 07-22/23 live forensic
(every 07-22 loss was the same potential-energy put-lean fired under LONG
gamma; the identical play won under SHORT gamma on 07-23).

Governing principle (article + prompt-era evidence): **GEX is context, not
signal.** The read is a fixed interpretation sequence — tenor, then regime,
then structure, then cleanliness — and only then a call. Structure the prompt
this way: prompt eras alone moved hit rate 9%→38%. The sequence IS the edge;
individual variables mostly are not.

## Tier Table

### RULES — gates the play (a failed gate = no call, full stop)
| Variable | Placement rationale (one line) |
|---|---|
| **Tenor surface selection** (0DTE standalone vs dated book; never mix) | Article Step 1: wrong tenor makes every downstream level "contextually incorrect"; Mirai's own operative_wall lesson — raw 0DTE wall hugs spot, must never feed fade logic. |
| **Gamma sign / flip at spot** (regime) | Article Step 2 is explicit that regime comes BEFORE walls ("the same GEX level can look wrong" otherwise); Mirai's regime stack has been the first gate since the reversion lens, and regime-first prompt eras drove 9%→38%. wt-8 sharpens this into the PERMISSION SLIP: positive gamma FORBIDS drift/momentum/continuation bets (fade at an edge, pin/settle, or stand down only); negative — confirmed, not unknown — allows continuation; unknown stands down. |
| **Profile cleanliness** (clean hierarchy vs messy interleave; includes chain-coverage guard) | Article Step 4: "sometimes the most advanced read is recognizing there is no clean read" — skip it. IMPLEMENTED 2026-07-25 (hierarchy half): the tower's `profile_clean` payload word, from the gw_vocab cluster pass — a put-dominant cluster peak stranded ABOVE a call-dominant one, or no dominant structure on a present surface, reads MESSY and the prompt makes it a stand-down gate (absent surface → absent field, never a fabricated verdict). The chain-coverage half rides separately in the engine's coverage guard. |
| **speedometer / range_ruler (rv_pace)** | ρ=0.634 to 30-min realized range — the strongest correlation anywhere in the stack; a target outside the pace budget is unreachable in the window, so pace VETOES the play rather than merely coloring it. |

### WEIGHS — shifts conviction (never fires a play alone)
| Variable | Placement rationale |
|---|---|
| **Walls GWc/GWp** (dominant call/put gamma walls) | Article: walls are *reaction zones, not trade triggers* — "we don't trade the level, we trade the reaction"; they pick WHERE the play lives, dominance/cluster separation scales conviction, but the wall itself never fires. |
| **Siege effort-at-wall (REVERSED SIGN)** | Backtest's sole tape survivor: high volume AT the wall → BREAK 61% (n=168), not hold — the article's "trade the reaction" made quantitative; grading still record-only (0/12 Wilson clock), so it weighs the hold/break lean, not gates. |
| **Net GEX magnitude** | Article's dominant-vs-minor distinction: magnitude says how hard the regime binds, scaling conviction in regime-consistent behavior — it carries no direction of its own. (Squeeze-zone flag: FUTURE — not yet computed anywhere in the stack; the shove test's sign flips are the current analogue.) |
| **Shove (terrain tilt)** | Live hedging-force direction from the gravity stack, "rides the clock" (m2); not killed by the sweep but never independently graded, so it nudges direction inside an already-gated play. Speaks in the payload as `terrain_asymmetry` (wt-8 rename): the slope of the book — which side is CHEAPER to move IF pushed — never a push, never a standalone direction. |

### COLOR — context only (may lower conviction; never raises it to a call)
| Variable | Placement rationale |
|---|---|
| **gex_motion** (melt/walk/creep/grip) | Shipped at w≈0.10 prior, shadow, no backtest confirmation either way — vocabulary for describing the map's drift, nothing more yet. wt-8 adds the stuck-vane guard: a soft_side that has not turned in ~50 diary rows is worded as a terrain fact, not today's opinion. |
| **LOB tape** | Shadow layer mid-baseline, promotion gate dormant — record it, cite it as texture, give it zero weight until the gate opens. |
| **UW Periscope sign-check** | The UW SIGN measured INCOHERENT — 0/72 recorded slices showed field structure (spatial-coherence z ≈ −0.16 vs native −7.94; a coin flip with a moneyness-drifting bias) — so it was deliberately NOT consulted: no disagreement flag, no conviction effect. Only its \|gamma\| MAGNITUDE survived (lag-1 autocorr +0.375). The vendor feed this rested on is **not part of this build**, so the layer is inert here; the finding is kept because the negative result is the useful part. |
| **VEX (vanna)** | Signs uncalibrated pending A/B (06-27 memory); the article omits vanna entirely — a context word at most. |
| **DEX net dealer delta** | The article name-checks Net DEX in its tooling but never operationalizes it in the four steps; zero Mirai grading history — record-first, color-only until a sweep grades it. Shipped 2026-07-25 as `lefteye_dex` → the `dex_views` diary key, spoken to the tower as `dealer_delta_dex` (advisory, no gate reads it); naive net is LONG by construction — read the split + flow-signed twin, never the naive sign. |
| **flow_recent / gamma_tape** | gamma_tape is record-only with a pre-registered kill condition and its sign claim ran anti-correlated 3/3 live days — descriptive texture only. |

### IGNORE — known artifacts (must not appear in the conviction path)
| Variable | Placement rationale |
|---|---|
| **Magnet pull (as direction)** | 33% hit in the 32-day sweep = a drift artifact BELOW coin flip; by construction a permanent bull sitting above spot ~90% on yesterday's OI — the single most seductive artifact in the stack. wt-8 demotes it to a LOCATION/late-day pin target: use it to place pins and fades, never to pick a drift direction. |
| **aggressor_flow (signed difference)** | Mathematically cancels to 0.00 precisely on the accelerant day (customers buying both wings = dealers short gamma), so the H5 veto can never fire at any threshold. |
| **CEX / charm word** | A constant 804/806 scans (H6 audit) — a word that never changes carries zero information. |
| *(also dead, keep out)* volume-profile/POC layers, break_lens cock | Placebo-null and conviction-SUBTRACTING respectively in the 07-17 sweep. |

### wt-9 fold-ins (2026-07-27) — three COLOR-tier advisories (record-only, no gate reads them)

TanukiTrade chart-reading doctrine folded into existing machinery, all advisory/ungraded; a
prompt change, so the ledger resets from wt-8. None gates a play — each may lower conviction only.

| Advisory (payload field) | What it adds | Folds into |
|---|---|---|
| **regime_intensity_1d** | how the whole-book (0-7DTE) net Γ moved vs yesterday's close, worded by the sign of TODAY'S level: a long-gamma pin *entrenching/weakening*, short-gamma amplification *deepening/easing*, or a *flip* across zero. An entrenchment scaler, never a direction; repriced at spot, so partly the overnight move — soft context only. | reads the existing `net_exposure` pair beside `net_gex_binding` (Article §1.1: distance/trend scales the regime, never sets it) |
| **siege_effort_at_walls[].role** | the target-selection LEAN off the reversed-sign verdict — QUIET *leans HOLD* (terminus-type pin/turn), SIEGE *leans BREAK* (pause-type speed bump); **subordinate to the gamma regime** (under long gamma even a SIEGE wall is not a license to trade through) | a relabel of the existing reversed-sign siege verdict — no new siege math |
| **air_pocket_above / air_pocket_below** | a thin *same-side* σ-span behind the near wall → once it breaks, extend the objective ACROSS the gap; **gated to a CLEAN profile** and **dormant under long gamma** (a break is faded there) | inline in `_gw_cluster_read` (nearest_walls per_side=2); the inverse of the runway/reach read |

*(A fourth fold-in, a DEX `tape_vs_positioning_conflict` word, was built and then REMOVED in the wt-9 pressure test: the naive `net_dex_total` is structurally positive, so a sign-disagreement with `dex_flow_signed` collapsed to a one-signed relabel of flow that contradicted the block's own `flow_signed_read`. A faithful §2.6 conflict — flow hedge-direction vs the live PRICE tape — remains a future item.)*

## wt-8 Read Order (the sequence the prompt walks)

1. **TENOR** — pick the surface: 0DTE standalone for the intraday call; dated
   book (tagged-FAR) only as backdrop; never let a 0DTE wall masquerade as
   structure.
2. **CLEAN?** — chain coverage full, GWc/GWp hierarchy coherent (no put wall
   stranded above a call wall)? If messy → the read ends here: STAND DOWN is a
   valid, graded output.
3. **REGIME** — gamma sign at spot vs the flip, read as PERMISSION: positive =
   suppressing (fade/pin plays only), negative confirmed = amplifying
   (continuation allowed), unknown = stand down. This sentence anchors
   everything after it.
4. **MAGNITUDE** — how hard does the regime bind? Big net GEX = trust the
   regime's character; thin = regime label is weak, demand more confirmation.
5. **STRUCTURE** — locate the dominant GWc above / GWp below as *reaction
   zones* (`wall_cluster_above/below`: span + prime tier — dominance decides
   which walls matter), mark the transition band (chop, no-trade —
   `regime_transition_band`, within ±0.25σ of the flip); the squeeze-zone flag
   beyond the main wall is FUTURE, not yet computed.
6. **PACE BUDGET** — rv_pace → 30m range: is any candidate target physically
   reachable this window? If not, cut the target or stand down.
7. **TERRAIN FORCE** — shove direction (`terrain_asymmetry`) and gex_motion
   word: is live hedging pressure leaning with or against the regime read?
   Slope alone is potential energy — without a live force it is not a trade.
8. **AT THE WALL** — if price is engaging a wall, read siege effort with the
   REVERSED sign: heavy effort → lean BREAK (61%), quiet approach → lean
   hold/fade.
9. **CROSS-CHECKS (color only)** — LOB tape, DEX tilt, VEX word: any of these
   may DOWNGRADE conviction one notch; none may create or flip a call. Magnet,
   aggressor_flow, charm, UW sign (measured incoherent 0/72): not consulted.
10. **CALL OR STAND DOWN** — regime + structure + pace agree → name direction,
    target (inside pace budget), and the wall that voids it; any RULES gate
    failed or conviction < threshold → stand down and say which gate failed.

*Doctrine note: every variable above WEIGHS-tier is promotion-gated — nothing
climbs a tier without its own Wilson-graded A/B. Demotion is free.*

*Record note (2026-07-25, phase P): three display-only diary records ride beside
this doctrine and hold NO tier — the tower never reads them (EXCEPT: wt-9 now reads
the 1-day CHANGE of `net_exposure` as the COLOR advisory `regime_intensity_1d`; the
totals themselves still hold no tier): `net_exposure`
(whole-book NET Γ/Δ totals, reused off the row), `profile_ladder` (the step-5
band and walls as named rungs — GWc/cT/HVL/pT/GWp; HVL is the adopted display
name for the flip line, storage key unchanged), and `adaptive_em` (the ruler's
EM re-split asymmetric by measured asymmetry; total width preserved). Shapes in
docs/gex-glossary.md.*
