---
name: mirai-left-eye
description: |
  GEX-ONLY since the 2026-07-03 restructure. Scheduled intraday scanner that
  runs the Fade Lens (the brain) on SPX every 5 minutes during RTH: the
  GRAVITY ENGINE (lefteye_gex_box, fed by the fill ledger + the native
  ThetaData read with its FLOW sensor) maps where dealer positioning pulls
  price; the lens asks four questions — stretched? gravity favors a snap-back?
  runway to the magnet? did it turn? — and makes PAPER fades only, graded
  nightly by the report cards (gex_polarity_ab). Nothing trades real money
  until the Wilson-guarded record proves the engine. The nine-voter
  confluence/pick system was retired (backup:
  mirai-station.backup-pre-gexmodule-20260703; salvage notes in
  docs/salvage-notes.md; glossary in docs/gex-glossary.md).

  TRIGGER when the user types `/mirai-left-eye` (one tick) or asks about the
  gex read / dealer map / fade lens state.
---

> [!warning] 2026-07-03 gex-module restructure
> Everything below the description that mentions the nine voters, confluence,
> pick_builder, outcomes or /oracle describes the RETIRED system — kept for
> historical context. The live system is: hunter (Shift Manager) → Fade Lens →
> gravity engine + flow sensor → diary → report cards. See docs/gex-glossary.md.

> **Interpreter**: invoke as `./hunter.py` (shebang pins
> `~/.local/share/mirai-station/venv/bin/python`). Do NOT use `python3 hunter.py` —
> system python lacks `schwab`/`scipy` and 4 of 5 signal modules silently fail.

# /mirai-left-eye — Intraday 0DTE Tape Hunter

You are running the `/mirai-left-eye` skill. Mirai's left eye watches the tape
continuously through the trading day and surfaces single-leg long call/put
contracts when the confluence threshold fires.

## Mandate

Find the highest-expected-value 0DTE single-leg long call or put on each
5-minute scan, **only when** rule-based confluence clears the threshold. This
skill returns null when no edge exists — that is the correct behavior. The
forced-verdict semantics of `/bet` do NOT apply here. Quiet tape returns
quiet output.

A pick fires when:
1. **Time gate passes** — clock is between 09:45 and 15:15 ET, market is open
2. **0DTE exists** for the ticker today (SPX/SPY/QQQ/IWM always; megacaps on
   Mon/Wed/Fri)
3. **Confluence score** ≥ alert threshold (default 0.12 on [-1.0, +1.0];
   panel-normalized — see "Confluence engine" below)
4. **No active dedup lock** — same direction on same ticker hasn't already
   fired within the last 20 minutes with flat or weakening signal evolution

## Universe

```
ALWAYS_DAILY = ["SPX", "SPY", "QQQ", "IWM"]
MON_WED_FRI  = ["AAPL", "NVDA", "MSFT", "GOOGL", "META", "AMZN", "TSLA"]
```

On Tue/Thu, the megacap list is skipped entirely — they have no 0DTE that
day, so there is nothing to hunt.

## Signal layers

**What "reading the tape" means here.** Two layers stacked:

1. **Price action** — VWAP behavior, OR breaks, CVD imbalance, intraday
   regime (the `lefteye_*` modules + `algo-read`).
2. **Bookmap-style read** — dealer-hedging gravity (OI density + GEX + walls
   + gamma flip). This is exactly what `/oracle` produces — its ASCII heatmap
   IS the bookmap. Left-eye doesn't reinvent this; it consumes
   `oracle --once <TKR> --json` and treats the gravity output as the
   bookmap layer.

**Multi-day context** is the third layer that turns a raw tape read into
human-style reasoning: "semis flushed yesterday → today is either a
continuation fade or a bounce reclaim, depending on how price reacts to
yesterday's levels at the open." Without this, the skill would score signals
in isolation and miss the regime context that distinguishes a 70% setup
from a 40% setup.

**Pulled from existing skills:**
- `oracle --once <TKR> --json` — bookmap (OI density), gamma flip, call/put
  walls, VWAP, drift target
- `algo-read <TKR>` — intraday regime classification (trend/range/vol-expansion)
- `iv-viability <CONTRACT>` — per-contract gating once a candidate is picked

**Net-new modules (this skill):**
- `lefteye_vwap.py` — VWAP reclaim + retest detection
- `lefteye_orb.py` — Opening-Range Breakout (first 15-min H/L with volume confirm)
- `lefteye_skew.py` — Intraday IV skew slope shifts (call wing vs put wing)
- `lefteye_prior_session.py` — multi-day context: underlying's prior N days
  + peer-group breadth → bounce / continuation / exhaustion classifier.
  Reasons like a market participant: "if peer breadth was down hard
  yesterday and today gaps up, algos sell into strength (continuation_short);
  if today gaps down and reclaims yesterday's low on volume, that's a
  high-edge bounce_long." Peer groups defined in `PEER_GROUPS`
  (semis / megacap_tech / indices); extend as universe grows.

**Confluence engine:** `confluence.py` — rule-based weighted scoring with hard
time-window gates. LLM strategist tiebreaker is invoked only when the rule
score crosses threshold, never on the cheap path. Current weights:

| Signal | Weight | Source |
|---|---|---|
| `gamma_regime` | 0.20 | oracle (bookmap) |
| `prior_session` | 0.15 | lefteye_prior_session |
| `vwap_reclaim` | 0.15 | lefteye_vwap |
| `algo_regime` | 0.10 | algo-read |
| `orb_break` | 0.10 | lefteye_orb |
| `iv_skew_slope` | 0.10 | lefteye_skew |
| `sweep_proxy` | 0.05 | chain volume vs OI |
| `cross_asset_div` | 0.05 | net-new (v2) |

**Panel normalization (breadth fix).** The signed conviction is the weighted
sum of *fired* signals divided by the **full** panel weight (≈1.0), NOT by the
weight that happened to fire. A non-firing signal abstains (counts as 0), so a
lone signal can't max out conviction — magnitude rises with the *breadth* of
agreement, the whole point of "confluence." Because few signals co-fire on a
given scan, scores top out ~0.24 in practice; thresholds live on that scale:
fire/lottery 0.12, mid 0.16, high 0.20 (lunch fire 0.15). Downstream,
`pick_builder.DRIFT_PER_CONVICTION` was rescaled 0.5→2.1 to keep the directional
drift constant against the compressed conviction range. Backtest of the
2026-06-11..17 resolved trades: the ≤2-signal fires this suppresses averaged
−45% PnL; the ≥3-signal fires it keeps averaged +30%.

## Output

When a pick fires, the entry written to `logs/YYYY-MM-DD.jsonl` and appended
to the vault's Captain's Log:

```json
{
  "ts": "2026-05-19T10:35:00-04:00",
  "ticker": "SPY",
  "direction": "call",
  "contract": "SPY 2026-05-19 580C",
  "spot": 579.80,
  "entry_zone": [1.45, 1.60],
  "target": 2.30,
  "hard_stop": 0.80,
  "time_stop_min": 15,
  "conviction": 0.68,
  "tier": "high",
  "signals_fired": ["vwap_reclaim", "orb_break", "gamma_below_flip", "skew_shift_bullish"],
  "rationale_one_line": "Reclaimed VWAP at 579.50 with successful retest, ORB high broken on 2.1x volume, spot below gamma flip with dealer short-gamma amplifying"
}
```

Quiet scans write a one-line heartbeat to the log and produce no output.

## Sizing

Single-leg long calls/puts only. Lottery sizing per CLAUDE.md exceptions —
the skill does NOT compute position size; that stays a manual decision for
the user based on account liquidity. The skill outputs the contract and the
risk levels; the user sizes.

## Phone push (opt-in)

When `config.json` has `"push_to_phone": true`, fires emit a `[PUSH] ...` marker
on stdout (single line, ≤180 chars, batched for multi-fire ticks). The Claude
turn that ran the scan must scan its own output for `[PUSH] ` lines and forward
each one to the `PushNotification` tool — Python cannot call Claude tools
directly. Default is OFF: validate signal quality from chat output for a few
sessions before flipping.

Mobile delivery via `PushNotification` requires Claude Remote Control to be
paired with the active session. If Remote Control isn't connected, the
notification still lands in the terminal/desktop but does not push to phone.

## Invocation modes

- `/mirai-left-eye` — one-off manual scan, returns the slate for the current
  tick (or null if no fire)
- `/mirai-left-eye --once <TICKER>` — single-ticker scan, bypasses universe filter
- `/mirai-left-eye --schedule` — registers the every-5-min loop via `/loop`,
  runs until market close or `--stop`
- `/mirai-left-eye --status` — shows current schedule state, last scan ts,
  fires today
- `/mirai-left-eye --stop` — unregisters the schedule
- `/mirai-left-eye --resolve <PICK_ID> --exit-price <X> [--exit-reason "..."]`
  — manually inject a ground-truth fill for an outstanding pending pick.
  Used when the user actually took a trade and wants the learning loop to
  count it rather than waiting for reconcile to estimate from the chain.
  Outcome is labeled `MANUAL_WIN` (exit ≥ target), `MANUAL_LOSS` (exit ≤
  stop), or `MANUAL` (in between). Summary counts MANUAL_WIN/LOSS alongside
  auto WIN/LOSS.

## Hard rules (locked from thesis)

1. **No entries pre-09:45 ET** — first 15min is noise + IV crush
2. **No fresh entries post-15:15 ET** — MOC imbalance is a coin flip
3. **Flat by 15:50 ET** — all open picks logged with EOD-cut warning
4. **Strike selection by probability + expected value** — direction is decided
   first (by confluence); the picker then scores every liquid contract on the
   trade side and admits one only when its **modeled** reach probability beats
   the **option-implied** reach (≈ N(d2), read off the IV-σ band) by an edge,
   AND the option's expected value is positive. No qualifying contract → no
   trade. The model tilts reach by conviction (directional drift), scales the
   vol band by the dealer-gamma regime (long-gamma above the flip dampens →
   strikes less reachable; short-gamma below amplifies → more), and penalises
   strikes beyond an opposing gamma wall (calibrated on the oracle brain's
   learned wall-hold frequency once it has observations). The conviction tier
   sets a target |Δ| (the barbell map) and the target/stop multiples:
   - `tier=high` (strong score): target |Δ|≈0.32 — a touch further OTM for
     convexity, still near-money (research: stay in the less-overpriced zone).
   - `tier=mid`: target |Δ|≈0.40 — slightly OTM.
   - `tier=lottery` (weak score): target |Δ|≈0.50 — ATM, gamma efficiency.
   The required edge grows the further OTM a strike sits and is stricter for
   index/ETF names (SPX/SPY/QQQ/IWM), which are the more overpriced. An empty
   Δ band falls back explicitly (a `selection_fallback` flag on the pick),
   never silently. This supersedes the old σ-window + OTM-bias rule.
5. **Lunch chop filter** — between 11:30-13:30 ET, raise the confluence
   threshold from 0.12 to 0.15 (mean-reverting low-vol window eats premium)
6. **Pre-catalyst veto** — if a known catalyst (FOMC, CPI, earnings for the
   ticker) is within 4 hours, suppress fires (IV crush risk on the post-event)
7. **Time-stop discipline (time-of-day adaptive)** — every fire carries a
   time-stop that scales with the entry window, because Mirai's own 2-day
   data showed afternoon directional moves systematically grinding past a
   flat 15-min stop. Bands (ET):
   - 09:30-10:15 → **15 min** (morning noise; cut losers fast)
   - 10:15-13:30 → **25 min** (transition; small grind acceptance)
   - 13:30-15:15 → **40 min** (afternoon slow-grind regime; theta still light)
   - 15:15-16:00 → **15 min** (MOC imbalance + gamma cliff; tight cuts only)
   Wired in `lefteye_common.adaptive_time_stop_min(now)`. If the move hasn't
   expressed by the window, the catalyst is gone.

## State management

`state.json` holds rolling intraday state, reset at 09:30 ET daily:
- Last scan timestamp per ticker
- Last fire (ticker, direction, ts, conviction) for dedup
- Signal evolution: rolling 6-tick history per (ticker, signal) for
  strengthening/flat/weakening classification

## Failure modes

- **Chain fetch failure** → skip that ticker for this scan, retry next tick
- **All signals null** → silent (do not invent confluence on missing data)
- **Schedule lock conflict** → if another `--schedule` is already running,
  refuse to start a second one; `--status` shows the live one

---

*v1 ships without a learning loop. Attribution and Bayesian calibration get
layered in after ~30 trades of logged outcomes are available for backtest.*
