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
| **UW Periscope sign-check** | The UW SIGN measured INCOHERENT — 0/72 recorded slices showed field structure (spatial-coherence z ≈ −0.16 vs native −7.94; a coin flip with a moneyness-drifting bias, see uw_coherence.py) — so it is deliberately NOT consulted: no disagreement flag, no conviction effect. Only its \|gamma\| MAGNITUDE survives (lag-1 autocorr +0.375), riding record-only via `lefteye_gamma_roll`. |
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
