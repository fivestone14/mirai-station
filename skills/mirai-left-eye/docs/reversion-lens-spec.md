# Reversion Lens — BETA spec (session conclusions, 2026-06-23)

The mean-reversion / exhaustion lens for SPX. Born from the 6/23 review: the
system caught nothing on a textbook gap-down V-bounce (7347.6 → 7424) because all
9 existing signals are **momentum/agreement** detectors and the system is
structurally blind to **reversion/exhaustion**. This is the missing instrument.

## What we concluded (the plan)

**It boxes up as ONE module that emits TWO confluence signals**, gated by a regime
switch — the same pattern `gamma_regime` already uses (big machinery inside, tidy
signal dicts out). The confluence engine just sees extra rows; nothing downstream
changes.

- **reversion_extreme** — fades a σ-stretch (gap from prior close / distance from
  VWAP) or a gamma-wall tag, *only in a pinning (positive-gamma) regime*. Arms on
  proximity, fires on the *reaction* (reject), never on raw distance — so it
  catches today's 7424 overshoot (24 pts past the 7400 wall) that a "price ≤ wall"
  rule would miss. Symmetric: put-wall/oversold → fade LONG; call-wall/overbought
  → fade SHORT.
- **level_reclaim** — goes WITH a decisive reclaim/break of a key level (prior
  close/high/low, round 50/100, or a broken wall) confirmed by volume; a wall
  break flips the regime pinning→trending (the circuit-breaker that stops a fade
  from fighting a real breakout).

## Regime switch (the gate, NOT a signal)

A **stack** — ≥2 reads must agree, never one dial (research convergence: it's all
one state, every read is a corroborating tilt not gospel; dealer positioning is
modeled-not-observed):

- **gamma sign / flip** (PRIMARY — native GEX, already built): spot vs flip, or
  inside-the-wall-band when flip is null.
- **realized-range vs expected-move**: compressed (<0.7×) → pinning; expanded
  (>1×) → trending.
- **intraday variance-ratio** (Lo-MacKinlay): VR<0.9 reverting / VR>1.1 trending.
- **VIX term structure** (VIX/VIX3M): contango <0.95 calm/pinning; ≥1.0 stress/trend.

Proven reads only; explicitly NOT Hurst / HMM / Vol-Trigger (fragile/curve-fit).

## BETA / SHADOW posture (why it's safe to ship tomorrow)

- `REVERSION_LIVE = False` by default. The lens **computes and LOGS every tick**
  (regime reads + verdict, σ-stretches, wall proximity in σ, armed/reaction/
  would-fire, direction) to a telemetry stream + the heartbeat — but does **NOT**
  enter the confluence vote and does **NOT** touch `WEIGHTS`/`TOTAL_WEIGHT`. Zero
  risk to live trading; existing behavior byte-for-byte unchanged.
- Going live (later, deliberate): add the 2 signals to `WEIGHTS` in their own
  `reversion` correlation group, accept the renormalization, flip `REVERSION_LIVE`.

## Forward-learning (record → self-tune)

- Telemetry → `state/reversion/{date}.jsonl`, one record per SPX per tick.
- A later reconcile resolves each armed/would-fire moment's **forward outcome**
  (did the fade pay over the next ~15-30 min?) and folds it into the existing
  posterior loop, so the σ thresholds, wall-proximity band, and break-volume
  level **self-tune** instead of staying hard-coded.

## Open decision (parked)

- Entry style at a wall: **anticipatory** (on approach — catches more, risks
  break-throughs) vs **confirmatory** (after the rejection prints — safer). User
  leans anticipatory ("don't want to miss the trade"). Beta logs BOTH so the data
  decides.

Scope: SPX only until proven.

---

# Adaptive Dials — BETA spec (2026-06-24)

The reversion lens fires on STATIC thresholds (fire ≥1.0σ, target +0.25σ, stop
−0.35σ, hold 120m). A single number can't fit every market mood — a fade that
should arm at 0.7σ in a strong midday pin shouldn't arm until 1.5σ in a wild gap
morning, and toward the close theta forces tighter targets + shorter holds. The
adaptive dials replace the constants with a **small trained model** that sets
SEPARATE up/down thresholds from a few learned contexts. Built to real quality;
runs in shadow and is scored before it ever touches a live decision.

## Goal (one line)
A trained formula → separate **Bull** (upside-fade) and **Bear** (downside-fade)
dials → from a few broad contexts → run in shadow & scored now → go live only
after weeks of out-of-sample proof.

## Step 0 — LOCK THE RESOLUTION (prerequisite; fixes the wobble)
Each pinpoint gets a stable id `{ticker}:{entry_ts}:{dir}`. Once it resolves
(win/loss/scratch) it is written ONCE to an append-only ledger
`state/reversion/resolved-{date}.jsonl` and never recomputed. Pending entries are
re-checked each scan ONLY until they resolve, then locked. The ULT summary reads
the locked ledger, not a fresh re-derivation → counts only ever grow, never flip.

## Step 1 — THE LABELED DATASET (the fuel)
Every pinpoint stores its CONTEXT at entry + its LOCKED outcome:
  context = { regime, regime_conf, pin_votes, gamma_sign, vix, vix_ts,
              minutes_to_close, stretch_source(gap|vwap|wall), gap_σ, vwap_σ,
              wall_dist_σ, arm_reason, ticker }
  label   = { outcome(win|loss|scratch), mfe_σ, ttr_min }
Most of `context` is already in the telemetry; we add `minutes_to_close` and the
locked label. This (context → outcome) table is what the model learns from.

## Step 2 — THE CONTEXTS (kept FEW — the anti-overslice rule)
Start with the highest-signal handful, learned NOT as a giant cross-product grid
(Mirai's bayes module already refuses that — it would "starve every cell"):
  1. time-of-day / minutes-to-close   (theta — user's prime instinct)
  2. volatility band                   (VIX level)
  3. regime strength                   (pin vote count / confidence)
  4. stretch source                    (gap vs VWAP vs wall tag)
Expand only if the data earns it. Bull and Bear are modelled separately
(market is asymmetric — "stairs up, elevator down").

## Step 3 — THE MODEL (a small trained formula, interpretable)
A regularized model `P(win | context, candidate_threshold)` — start with
**logistic regression** (literally a readable weighted formula; matches "a model
that ingests parameters and outputs the dial") — one each for Bull / Bear. At
decision time it picks the threshold that MAXIMIZES expected value
(EV = P(win)·target − P(loss)·stop), clamped to a sane range. Retrained NIGHTLY
on the accumulated ledger (batch), applied intraday (cheap) — mirrors Mirai's
existing reconcile→learn→scan cadence. Overslice guards: L2 regularization +
an LCB/min-sample floor (don't trust a context bucket until it has enough trades;
fall back to the global static dial below that floor) — same principle as the
confluence LCB guard already in the codebase.

## Step 4 — SHADOW OPERATION (a shadow inside the shadow)
Each scan the model computes the dials it WOULD set and logs them next to the
actual static decision. Nightly, a counterfactual scorer asks: "would the model's
dials have produced more wins / better EV than the static ones on today's locked
outcomes?" Tracked in the ULT learning note. ZERO effect on the live (already
shadow) reversion signals — it is a parallel evaluation track only.

## Step 5 — WHY-WON / WHY-LOST (plain-language, in the ULT)
A short summary grouping wins vs losses by context — e.g. "wins clustered at deep
stretches in strong midday pins; losses clustered at shallow stretches in weak/AM
regime" — so a human can sanity-check the model's logic before trusting it.

## Step 6 — GO-LIVE GATING
Graduate from static → model-set dials ONLY when, on OUT-OF-SAMPLE weeks, the
shadow model beats the static dials on EV AND its logic is inspectable/sensible.
Even live, the model output stays CLAMPED to a bounded range (never an absurd
dial). Still SPX only; still off until proven.

## Build order
0 lock resolution → 1 persist context+label ledger → 2 why-won/lost summary (small)
→ 3 gather weeks → 4 build shadow model + counterfactual scorer → 5 prove out
→ 6 go live (clamped). Steps 0–2 are small and immediate; 3–4 are the patient part.

## Open decisions (for sign-off)
- Model: logistic first (interpretable formula) — upgrade to gradient-boosted
  trees only if the linear form underfits. (Recommend logistic.)
- Prediction target: P(win | context, threshold) → EV-max threshold (recommended)
  vs directly regressing the "best" threshold. (Recommend the former.)
- Cadence: nightly batch retrain + intraday apply. (Recommend.)
- Scope of dials the model sets: fire-threshold first; add target/stop/hold once
  fire-threshold proves out. (Recommend phasing.)
