# Salvage notes — logic worth re-implementing, from the retired signal system

Deleted 2026-07-03 in the gex-module restructure. The pre-restructure backup
(`mirai-station.backup-pre-gexmodule-20260703`) was itself deleted 2026-07-10
after the live record validated — so the notes below are now the *only* record of
what the retired modules did. One entry per module: what it did, and what a
gex-only system might want back someday.

| Retired module | What it did | Worth re-implementing when… |
|---|---|---|
| `confluence.py` | Weighted voting across nine signals; Wilson LCB guard on learned win-rates; collinearity groups (co-firing correlated signals count once, decayed ×0.5); lunch-hour higher threshold; panel normalization | **Wilson guard ALREADY SALVAGED** into `gex_polarity_ab.wilson_bounds` / `wilson_bounds (promotion_gate built 2026-07-04 uses it directly; confident_hit_rate was removed unused)` (the future promotion gate). The collinearity-group idea matters again if multiple gex signals (regime + flow + drift) ever vote together. |
| `pick_builder.py` | Chain-aware 0DTE contract selection: probability-of-touch/EV gate, spread caps by symbol class (index 0.40/half-charge), reach modeled over the adaptive hold window, gamma-context scalers | The whole module is the blueprint for when the Fade Lens graduates from paper to building real contracts — reach-vs-runway math translates directly to magnet-targeted fades. |
| `outcomes.py` | Pending/resolved paper-trade ledger: target-before-stop reconcile against live chains, intra-poll peak watermark from 1-min bars, EOD expiry sweep | The lens's `_resolve_beta_trades` covers grading; the **intra-poll peak watermark** trick (recover premium spikes between polls) is worth copying if lens grading ever moves to option prices instead of index σ. |
| `lefteye_gamma.py` | The legacy gravity read: oracle subprocess + native-chain ladder + SPY-proxy rescale | Fully absorbed: math helpers (`_atm_iv`, `_weight_wall`, `_live_index_spot`) live in `lefteye_gex_box`; the OI-then-volume weighting ladder became `pick_basis`. Nothing left to salvage. |
| `lefteye_macro.py` | Cross-asset divergence read (TLT/UUP/HYG vs equity direction) | **Prime prefill material** — this is a ready-made template for the foreign-markets world-wave feature's risk-proxy legs (see mirai-foreign-prefill-pending memory). |
| `lefteye_algo.py` | SPY price-action "algo regime" anchors (trend/chop classification) | The regime stack already has range/VR reads; revisit only if a second tape-character opinion is wanted for day-type labeling of report cards. |
| `lefteye_skew.py` | IV skew slope vs session baseline (call/put slope history per scan) | A vol-side sentiment read — natural sibling for the Drift Gauges (vanna) if slide F ever grows a skew input. |
| `lefteye_orb.py` | Opening-range breakout detection with volume confirmation | Superseded by gap/stretch logic in the lens; revisit only for an opening-context flag in the world-wave prefill. |
| `lefteye_prior_session.py` | Prior-session levels signal | The lens's `level_reclaim` already watches prior close/high/low from daily bars. |
| `lefteye_common.py` | `safe_signal` crash-isolation wrapper, adaptive time-stop schedule, terminal trade-card renderers | The **adaptive time-stop schedule** (hold window shrinking into the close) is the piece to copy when paper fades get explicit time stops. |
| hunter's retired parts | Breadth-first pick ranking (independent factors beat raw conviction — the resolved data proved conviction was anti-predictive), book-level direction cap (net-lean penalty, λ=0.5), registration tiers | The **direction cap** concept matters again the day the lens can hold multiple concurrent paper positions; the breadth-beats-conviction finding is a hard-won empirical lesson — keep it in mind for any future multi-signal scoring. |
| `verify_index_fixes.py` | One-shot verification harness for the 06-22 index fixes | Historical; nothing to salvage. |

Learned-state files that may still be on disk (history, harmless): `state/memory/*_memory.json` (old per-signal posteriors), `skills/mirai-left-eye/outcomes/*.jsonl` (old paper-trade ledger), `skills/mirai-left-eye/logs/*.jsonl` (old heartbeats). The full pre-restructure source is **no longer retained** — the backup was deleted 2026-07-10, so these notes are the only surviving description.
