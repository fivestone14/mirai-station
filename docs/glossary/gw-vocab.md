# GW Vocabulary — the standard notation for walls, magnets, and pins

Standardized 2026-07-19. This is the ONE naming scheme used everywhere a wall,
magnet, or pin is written for a human: chart labels, tooltips, layer
descriptions, docs, markdown views, and conversation. It exists so that when we
talk about any layer of any stack (gex, volume, order book, …) we can point at
exactly which structure we mean.

Doctrine (unchanged, `gex_views` precedent): **storage keys and code
identifiers are never renamed.** The standard applies to display text,
labels, tooltips, docstrings, and docs. `call_wall` stays `call_wall` on disk;
it *renders* as `GWc`.

## Core terms

| Notation | Name | Meaning |
|---|---|---|
| **GW** | Gex Wall | A strike — or tight cluster of adjacent strikes — where net dealer gamma is concentrated. There can be many at once, calls and puts, above and below spot. |
| **GWc** | Gex Wall · Call | A wall whose concentration is call-side (net dealer gamma **positive** at those strikes). |
| **GWp** | Gex Wall · Put | A wall whose concentration is put-side (net dealer gamma **negative** at those strikes). |
| **MagP** | Magnetic Pull | A Gex Wall that is currently *attracting* spot toward it (dealer re-hedging mechanics). A wall *has* MagP status; MagP is not a separate level. |
| **Pin** | Pinning | Spot is *stuck at* a Gex Wall or wall region — price is being held there, not merely pulled. |

## Prime intensity — ' '' '''

Every GW, MagP, and Pin carries one of three intensity marks. `'` is the
weakest, `'''` the strongest. Intensity is **relative to the strongest
structure on the same surface** (same tenor set, same scan) — gamma magnitude
scales day to day, so absolute thresholds would lie.

Let `share = structure_strength / strongest_structure_strength`:

| Mark | Share of strongest | Read as |
|---|---|---|
| `'''` | ≥ 0.60 | dominant — the heaviest concentration on the board |
| `''` | 0.25 – 0.60 | significant — clearly shaping price |
| `'` | < 0.25 | present — visible but minor |

- **GW intensity**: cluster strength = Σ|net gamma| over the cluster's strikes.
- **MagP intensity**: inherits the GW tier of the wall the magnet sits on
  (within one strike step); when the magnet is not on a displayed wall,
  default `MagP''`. (Pressure-tested 2026-07-19 on 5 live sessions: recorded
  pin-zones are always a single ~1.0-share zone, so zone-share cuts can never
  tier — wall-tier inheritance is the honest, computable basis.)
- **Pin intensity**: inherits the prime tier of the wall doing the pinning
  (`Pin'''` = held at a dominant wall).
- **Hysteresis** (live surfaces): a tracked wall's tier only promotes when its
  share clears the cut by +0.07 and only demotes when it falls 0.07 below —
  labels must not flicker scan to scan (churn 10.1 % → 5.5 % measured).

## Tenor prefix

Prefixed in square brackets when the expiry matters (charts, map, tooltips):

| Prefix | Applies to |
|---|---|
| `[0DTE]` | today's book (`net_by_strike`, 0DTE gamma walls) |
| `[1-7DTE]` | the near-tenor terrain (`net_by_strike_tenor`, tenor walls) |
| `[AUG21]` | dated book — far expiries labeled by **date** (MMMDD), never by a DTE number, because that number changes daily |

Examples: `[0DTE]GWc'''` · `[1-7DTE]GWp''` · `[AUG21]GWc'`.
Un-prefixed (`GWc''`) is allowed only where the whole surface is one tenor and
the surface says so once.

**Untiered carve-out**: a GW label may omit the prime mark where an honest
share is not computable — scalar legacy levels (a stored single-strike
`call_wall`), dated-book picks merged across expiries. Never invent a tier;
`GWc 7550` with no mark means "tier unknown", not "tier one".

## Clustering rule (how a "wall" is found from per-strike gamma)

1. Take the per-strike net dealer gamma surface (calls +, puts −).
2. **Concentration floor first**: keep only strikes with |net gamma| ≥ 25 % of
   the scan's single strongest strike. (Pressure-tested 2026-07-19: without
   this floor, same-sign grouping merges each side of the book into one
   ~220-pt blob and every downstream tier degenerates. With it, walls come out
   ~1–5 strikes wide and one `'''` per scan in 82 % of scans — the intended
   shape.)
3. Group surviving **same-sign adjacent strikes**, allowing at most one
   missing strike step inside a group (SPX: 5-pt steps, 10-pt when wide).
4. Cluster strength = Σ|net gamma|; label price = the peak (|max|) strike.
5. Drop noise: clusters under 5 % of the strongest cluster's strength.
6. On the map: show the nearest **up to 3** clusters above spot and **up to 3**
   below, each labeled by its own sign (GWc/GWp) + prime intensity. If fewer
   than 3 exist on a side, show what exists — never force 3.
7. Data note: the measured surface lives at `gex_views.net_by_strike`
   (`gex_theta` never carries it in the diaries); rows before 2026-07-13 have
   none — surfaces without data show no wall labels, never modeled stand-ins.

## Status rules (tooltips)

- **MagP** — the wall coincides with the current magnet / #1 pin zone
  (within one strike step). Tooltip reads e.g. `MagP'' — pulling spot from
  7482 toward 7500`.
- **Pin** — spot sits inside the wall's cluster span (± one strike step).
  Tooltip reads e.g. `Pin''' — price held at this wall`.
- A wall can be neither (plain terrain), and the magnet's wall becomes the
  pinning wall the moment spot reaches it.

## Plain-English translations (Dictionary tab source of truth)

- `GWc'''` — "the biggest call-side gamma wall on the board"
- `[0DTE]GWp'` — "a minor put-side wall in today's expiry"
- `MagP''` — "a wall with a significant magnetic pull on price right now"
- `Pin'''` — "price is stuck hard at a dominant wall"
- `[AUG21]GWc''` — "a significant call wall in the Aug 21 dated book"
