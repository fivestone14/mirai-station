# sndk-pro — SNDK live GEX map (beta, record-only)

A **fully isolated** subproject that produces a live GEX-map data stream for
the equity ticker **SNDK** (SanDisk, ~$1,250, $5 strike grid, weekly Friday
expiries). Nothing here changes SPX behavior: no left-eye module is modified —
the pure engines (`build_views`, `slide_0dte`, `dex_views`, `profile_ladder`)
and the hardened MCP transport (`native_gex_feed._run`) are **imported** from
the sibling skill, never forked, and every SNDK rule lives in this directory.

Vocabulary: **GRAVITY** = dealer positioning pull, **FLOW** = live force
(host convention, `docs/gw-vocab.md`).

## The three SNDK facts everything here is built around

1. **Weekly expiries, not dailies** (Friday, shifting to Thursday on a holiday
   Friday). "No expiry today" is the NORMAL state 4 of 5 days — it never trips
   an SPX-style `zero_dte_dead` alarm; the coverage teeth bite on the **front
   (nearest) expiry** instead. Bulk expiry discovery is broken for SNDK (only
   the monthly comes back), so the next ~2 weeks of business days are probed
   **per day** (the 2026-07-13 doctrine) and the verdict is cached for the day.
2. **The chain's own spot is ~15% stale** (live-verified 1088.5 vs a real
   ~1246). Every strike window and every spot-relative read anchors on the
   Schwab live quote (`lefteye_fetcher.live_spot`); **no live quote → no book,
   no row** (fail-closed — the stale spot is never an anchor, it only rides as
   the for-the-record `meta.chain_spot`). The strike window is vol-adaptive:
   `max(±8%, 2·σ_daily/spot)` clamped to ±25% (SNDK runs ~105% IV, where a
   fixed ±8% would truncate the magnet's own ±1.5σ reach window).
3. **Provider IVs are computed off that stale spot** and are garbage on BOTH
   sides near the real money (ATM call ~3.5, ATM put 0.0). IV is REBUILT per
   strike from the live bid/ask mid of the OTM right (Black-Scholes bisection
   vs the live spot) and shared with its parity twin; provider IV survives
   only as a plausibility-banded, parity-clamped fallback. Gamma is then
   recomputed from the rebuilt IV (`gamma_src="bs_iv"`), as for SPX.

## The front-book adaptation

The SPX engine's "today's book" (slide B / A0) is the daily 0DTE. SNDK's
tradeable book is the front weekly, so the magnet / walls / per-strike panes
are computed by handing the **front-expiry contracts to the same
`slide_0dte`** the SPX 0DTE book goes through — each contract's real dte
drives its own tau, so a 3-DTE book prices as a 3-DTE book. On SNDK's actual
expiry day `front_dte == 0` and the engine collapses to full SPX semantics
(live minutes-to-close clock, `regime_source: "0dte"`). The expected-move
ruler prices the front-weekly ATM straddle and scales it to the remaining
session by `√(τ_day/τ_front)`, where `τ_front` is the SNDK **front-book
clock** (`sndk_feed._tau_front`): the engine's sessions-ahead count PLUS
today's remaining session fraction — the engine's own dated τ excludes today
and would read a Thursday-morning weekly ×1.41 hot. The same clock drives the
quote-derived IV solve, so σ and EM agree; on expiry day it IS the engine's
clock, verbatim.

## Glossary

| File | Plain name | What it does |
|---|---|---|
| `sndk_feed.py` | The Chain Runner | Per-day probe discovery (day-cached; probe ERRORS on a candidate front day → degraded, never cached), one batched `cass_market_run` pull per 240s (raw book persisted to `state/sndk_gex/chain_cache.json` — each tick is a fresh process, so the off tick re-prices the cached book at the fresh quote instead of re-pulling), chunked + clip-detected, IV rebuild + gamma fill on the front-book clock, front-expiry coverage teeth |
| `sndk_views.py` | The Map Maker | Pure: chain + live spot → one diary row via the left-eye engines (gex/dex/ladder/EM/net-exposure) |
| `sndk_hunter.py` | The Shift Worker | One tick per invocation: quote → chain → row → append `state/sndk_reversion/{date}.jsonl`. `--force` bypasses the RTH gate (manual proof runs) |
| `sndk_read.py` | The Reader | The chart's live reading. Recomputes the ARROW every run (pure, free) and spends one `claude -p` call — the model's OWN vector + magnitude off an unbiased scene (sr-3) — only when the wake gate fires → `state/sndk_reads/{date}.jsonl` |
| `sndk_rag.py` | The Memory | On-demand history TOOL (never auto-injected): per-read slice records (metadata face + narrative face), day summaries with a sentiment read, month-tier standing terrain, hybrid metadata-filter→narrative-rank retrieval → `state/sndk_rag/` |

## The arrow and the reading are separate on purpose

`sndk_read.py` is **not** a ported Watchtower. The SPX tower's call *is* the
model's opinion; here the two paths are decoupled:

* the **arrow** is decided by a deterministic aggregator — no model — and
  still draws on the chart exactly as before,
* the **reading** (sr-2 blueprint 2026-08-02, sr-3 since 2026-08-08) is the model's **own
  inference** — a direction vector + a magnitude in σ — made cold from an
  **unbiased scene** that carries evidence only. The old
  `arrow_already_decided` block anchored the model and was removed from the
  scene JSON; the arrow never enters the payload, the model never sees it,
  and the two surfaces may disagree on the chart (that disagreement is
  information, not a bug).

The scene (docs/sndk-payload-inventory.md is the field-by-field map):
grouped by force — **data_sources** (**new in sr-7**: the three clocks that
were being conflated — the scan, the price quote, and the option book, which
the feed re-serves from a disk cache on half of all scans and which is
routinely minutes older than both; plus the night the standing open interest
was struck, re-verified unchanged on every scan), **freshness_rules**
(**new in sr-7**: the ceilings, and the blocks DELETED for exceeding them —
labelling a stale number had already been tried and ignored), `clock`
(**day-scoped since sr-4**, and the session calendar and nothing else since
sr-7: session_date, minutes each way, and `front_expiry`
{days_to_expiry, expiry_date}; weekday deliberately absent, it equals dte on
every recorded session), `scale` (σ + **expected_move_today_asym**, the
IV-skew-split asymmetric range), `price` (the live spot AND the spot the book
was measured at, the gap between them, and
**vwap_minus_live_spot_sigma**), `regime` (+ **vol_trend**, **flip** band
with center + position — told ONCE since sr-3: the edges are the flip ±0.25σ
by construction, and the old named_levels duplicate left the scene — plus
**charm** magnitude/drift-target on the front-book clock), `magnet` (the lead
as a NUMBER + its percentile against prior sessions, counted per distinct
BOOK since sr-7; the `is_a_tie` verdict was a July threshold worn as a
finding and left in sr-3), **breadth** (the shove ratio, direction-free —
sr-3 is the first time it reaches the model), **momentum** (gamma-share +
gross-volume deltas over the last 5 scans, its window told on the book clock
since sr-7; `oi_d` and `cvd` deliberately absent — OI is static intraday
upstream, and no aggressor tape exists for the stock leg; the `read` verdict
word left in sr-7 for the same reason `is_a_tie` did), **dealer_positioning**
(dex $-delta + front-book vanna; the sign is positive by construction and the
field name now says so), **walls** (laddered, 2 per side, NEAREST first —
never "strongest"; each wall carries its own `unchanged_for_min`, and the
heaviest cluster ships as `*_heaviest_wall_behind_the_ladder` when the
distance cut would hide it), `history` flags (the under-pull guard), and
`frozen_do_not_cite` (minus walls — their staleness rides the wall entries
now). Omit-never-null throughout: a field without a clean source is absent,
not nulled — the model treats missing as "no data" — and, since sr-5,
measured-empty is stated rather than deleted: a `*_side_has_no_wall` flag or
`flip.no_flip_anywhere_on_board` means the board WAS read and genuinely holds
nothing there.

Every block's source is declared once, in `BUILT_FROM` and in the doctrine, and every σ distance derived from the book
is measured from `spot_when_book_was_measured`, not from the live quote — the
two disagree by a median $2.13 on a cached row and by $41.99 at worst, and
`price.live_minus_book_spot_sigma` is subtracted to convert between the
frames — the subject and direction spelled out in the name itself. Leaf
names carry
their own units (`_pp`, `_bn`, `_musd`, `_min`, `_sigma`) so a name survives
being read alone, which is the whole sr-7 rename.

The read call may reach for exactly two on-demand tools, doctrine-gated:
the **history CLI** (`sndk_rag.py` — day slices / day summaries / month
terrain; the payload's `history.price_at_level_unseen_earlier_today` flag taps the model on
the shoulder) and **WebSearch** (abnormal-tape catalyst checks only,
`history.tape_abnormal_vs_own_history`). Everything else stays banned; the live scene is
always primary.

That is what four recorded sessions measured (2026-07-28..31, 756 rows):

* 12 of ~20 candidate payload fields are **constants**. `dex_word` is the
  identical string on 756/756 rows and renders as *"Dealers sell into
  strength"* — it read that way through all 189 scans of a **+12%** day. A
  constant that reads like a signal manufactures conviction for free, in one
  direction, forever. Constants are named in `INADMISSIBLE` and never reach the
  model.
* The magnet is a **tie**: median gap between the #1 and #2 strike is 3.87pp of
  mass, under 5pp on 62% of scans. It ships as a band, never a scalar.
* The old arrow pointed **8x further than price travels** in the window it
  implied (median 30-min move 0.077σ; median |magnet − spot| 0.612σ).
* Counting independent sign **runs** rather than rows, four days hold 42 magnet
  runs and 86 shove runs. Nothing on this surface is outside the noise band,
  and nothing here is presented as an edge.
* Shove and the magnet are **not independent** — an earlier draft required
  "two layers to agree"; replayed, both gates cleared on 69 of 756 scans and
  agreed on **69 of 69**, because the magnet *is* the largest gamma pile and
  shove measures that same mass's asymmetry. Roles are now named honestly:
  magnet = source, shove = breadth gate (may veto, never votes), path =
  caution flag (the one genuinely independent read).

Replayed over those four sessions the gate spends **23 / 22 / 20 / 32** calls a
day against ~189 scans, with the heartbeat filling only 1–4 slots. The arrow is
live on 0% / 0% / 14% / 41% of scans — the zeros are honest: on 07-28/29 the
book's lopsidedness sat at 0.004 and there was nothing to say.

`python3 sndk_read.py --replay YYYY-MM-DD` re-runs the gate over a recorded day
and writes nothing; that is how every number above was measured.

## Wiring

* launchd: `com.mirai-station.sndk` fires `runtime/scripts/run-sndk.sh` every
  120s (half the SPX rate — conservative with the shared server); the wrapper
  gates on `watch.intraday.market_status` RTH.
* launchd: `com.mirai-station.sndk-read` fires `runtime/scripts/run-sndk-read.sh`
  every 120s. **Its own job on purpose** — a model call can hang to its 60s
  timeout, and it must never be able to eat the scanner's tick and cost a diary
  row.
* Store: `state/sndk_reversion/` (diary rows — the viewstation reads them via
  the generic `/api/raw` endpoints; **pinned UI contract**),
  `state/sndk_reads/` (the arrow + reading rows, same pinned contract),
  `state/sndk_gex/` (discovery cache + raw-book cache + vol hint + fetch log),
  and `state/sndk_rag/` (slice records + day summaries + terrain — the
  on-demand memory).
  Off-hours `--force` rows carry `meta.forced: true` so they never pool
  silently with live rows. Read rows are stamped `era` (`sr-8` since
  2026-08-30 — the payload pays only for what VARIES: 30 leaves left the scene,
  18 of them a single value across 4,149 recorded scans and 12 exactly
  reconstructable from leaves that stay, their explanations moved into the
  prompt-cached doctrine. `sigma_measured_from` now names only the EXCEPTION,
  `price_quote` is gone (it was `scan_taken_at` twice over, so there are two
  clocks and not three), and `vwap_dist_sigma_from_live_spot` became
  `vwap_minus_live_spot_sigma` so its sign reads off its name; `sr-7` since
  the same day — provenance and the leaf rename: every block's source was
  declared, `data_sources` carries the scan and book clocks
  separately, book-derived σ distances measure from the book's own spot,
  percentiles count distinct books rather than scans, and `freshness_rules`
  deletes a block whose source aged out instead of labelling it. Read rows
  gain `book_asof` + `scan_age_min`, and `book_age_min` is finally measured
  off the book; `sr-6` since
  2026-08-11 — the pulse check: a stale book (newest row > 6 min old)
  never wakes the model, rows stamp `wake:"stale_book"` + book_age_min;
  `sr-5` since
  2026-08-10 PM — measured-empty says so (`*_side_clear`, renamed
  `*_side_has_no_wall` in sr-7,
  `flip.none_on_board`, renamed `flip.no_flip_anywhere_on_board` in sr-7)
  and the 0.08 point became the measured spread
  (`scale.move_30min_sigma`); `sr-4` since
  2026-08-10 — the scene learns what day it is: date, minutes_to_close,
  front_expiry dte; `sr-3` since
  2026-08-08 — the flip told once, dex's sign named, walls aged +
  heaviest-behind, gates handed over as numbers; `sr-2` since
  2026-08-02; `sr-1` rows are the pre-blueprint rule set) so a later read
  of the history can never blend two rule sets — bump it on ANY change to the
  gates or the prompt.
* Kill switches: `SNDK_PRO_DISABLE=1` (scanner), `SNDK_READ_DISABLE=1`
  (reading) — each checked in its wrapper AND in python. `SNDK_PRO_DISABLE`
  also stops the reading: with no fresh rows, reading on would only re-narrate
  a frozen book.
* **Pause — not a kill switch**, and the difference is the whole point:
  `state/sndk_reads/control.json` (`{"reasoning": bool}`). It silences the
  model's **sentence** and nothing else: the arrow, the gate separations, the
  magnet ranking, the frozen list and the wake reason are all pure functions of
  a diary row already on disk, so they keep computing and every scan still lands
  a row, stamped `paused: true`. A gap in a training set costs far more than a
  gap in the prose. `sndk_read.reasoning_on` fails **OPEN** — a missing or
  unreadable file means nobody ever touched the switch, which is not the same as
  asking for silence.
  It is an **operator** control: edit the file. It used to be a toggle on the
  viewstation's SNDK tab, backed by the only write that server accepted; both
  went on 2026-08-23 when the station went public behind a password, because a
  switch that silences the model is not something a visitor should reach. The
  viewstation is strictly read-only now.
* Tests: `tests/` (own conftest — all state redirected to tmp, transport
  dead-ended). Run: `~/.local/share/mirai-station/venv/bin/python -m pytest tests/ -q`.

BETA / record-only: nothing here trades, alerts, or feeds any SPX consumer.
