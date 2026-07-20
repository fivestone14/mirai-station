"""
gw_vocab.py — the single Python source of truth for the GW notation standard.

The full standard lives in docs/gw-vocab.md (2026-07-19, amended same day after
the live-data pressure test): GWc/GWp = call/put gex walls, MagP = magnetic
pull, Pin = pinning; every structure carries a prime intensity mark (' '' ''')
relative to the strongest structure on the same surface; tenor prefixes
[0DTE] / [1-7DTE] / [AUG21]-style dates. Storage keys and code identifiers are
NEVER renamed (`call_wall` stays `call_wall` on disk — it *renders* as GWc);
this module only produces display text and clusters.

The measured surface is `gex_views.net_by_strike` ([[strike, net_gamma], …];
`gex_theta` never carries it in the diaries) — surfaces without data show no
wall labels, never modeled stand-ins.

Pure, stdlib-only, no I/O — it mirrors a JS twin inside the viewstation, so
keep the two in step when the thresholds or the clustering rule change.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

# Prime-intensity thresholds (share of the strongest structure on the surface).
PRIME_DOMINANT = 0.60      # ≥ → '''
PRIME_SIGNIFICANT = 0.25   # ≥ → ''  (else ')
TIER_HYSTERESIS = 0.07     # live surfaces: promote at cut+0.07, demote below cut−0.07

# Clustering (docs/gw-vocab.md §Clustering, pressure-tested 2026-07-19):
STRIKE_FLOOR = 0.25        # per-strike concentration floor vs the strongest strike —
                           # without it same-sign grouping merges each side of the
                           # book into one ~220-pt blob
NOISE_FLOOR = 0.05         # clusters under 5% of the strongest are dropped

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

_TIER_MARKS = ("'", "''", "'''")

_EPS = 1e-9


def _tier_n(share: float, cuts: tuple[float, float]) -> int:
    """1..3 from a share against (significant, dominant) cuts."""
    if share >= cuts[1]:
        return 3
    if share >= cuts[0]:
        return 2
    return 1


def prime_tier(share: float, prev_tier: str | None = None) -> str:
    """' / '' / ''' from a structure's share of the strongest structure.

    With `prev_tier` (a tracked live wall), hysteresis applies: promote only
    when the share clears the cut by +0.07, demote only when it falls 0.07
    below — labels must not flicker scan to scan."""
    plain = (PRIME_SIGNIFICANT, PRIME_DOMINANT)
    if prev_tier not in _TIER_MARKS:
        return _TIER_MARKS[_tier_n(share, plain) - 1]
    prev = _TIER_MARKS.index(prev_tier) + 1
    up = _tier_n(share, (plain[0] + TIER_HYSTERESIS, plain[1] + TIER_HYSTERESIS))
    down = _tier_n(share, (plain[0] - TIER_HYSTERESIS, plain[1] - TIER_HYSTERESIS))
    if up > prev:
        return _TIER_MARKS[up - 1]
    if down < prev:
        return _TIER_MARKS[down - 1]
    return prev_tier


def magp_tier(magnet: float | None, clusters: list[dict],
              step: float) -> str:
    """MagP intensity — inherits the GW tier of the wall the magnet sits on
    (within one strike step); magnet on no displayed wall → default ''.
    (Recorded pin-zones are always a single ~1.0-share zone, so zone-share
    cuts can never tier — wall-tier inheritance is the computable basis.)"""
    if magnet is None:
        return "''"
    host = pin_status(clusters, magnet, step)
    return host["tier"] if host else "''"


def _tenor_prefix(tenor: str | None) -> str:
    """[] tenor prefix: '0DTE' / '1-7DTE' verbatim; an ISO date renders as the
    MMMDD expiry (never a DTE number — that number changes daily)."""
    if not tenor:
        return ""
    if tenor in ("0DTE", "1-7DTE"):
        return f"[{tenor}]"
    d = date.fromisoformat(tenor)
    return f"[{_MONTHS[d.month - 1]}{d.day:02d}]"


def gw_label(side: str, tier: str, tenor: str | None = None) -> str:
    """Display label for one wall, e.g. gw_label('call', \"'''\", '0DTE') →
    \"[0DTE]GWc'''\". side is the wall's gamma side ('call' | 'put')."""
    if side not in ("call", "put"):
        raise ValueError(f"side must be 'call' or 'put', got {side!r}")
    return f"{_tenor_prefix(tenor)}GW{'c' if side == 'call' else 'p'}{tier}"


def infer_step(strikes: list[float]) -> float:
    """The modal spacing between consecutive sorted strikes — the grid's step.
    Callers that need the clustering step (e.g. for magp_tier / pin_status)
    infer it from the FULL grid, before the concentration floor thins it."""
    diffs = [round(b - a, 6) for a, b in zip(strikes, strikes[1:]) if b > a]
    if not diffs:
        return 1.0
    return Counter(diffs).most_common(1)[0][0]


def cluster_walls(net_by_strike: list, spot: float,
                  step: float | None = None) -> list[dict]:
    """docs/gw-vocab.md §Clustering — find the walls on one per-strike surface.

    net_by_strike: [strike, net_gamma] pairs (calls +, puts −), typically
    `gex_views.net_by_strike`. Concentration floor FIRST: only strikes with
    |gamma| ≥ 25% of the scan's strongest strike survive (without it grouping
    merges each side into one wide blob). Survivors group as same-sign adjacent
    strikes, allowing at most ONE missing strike step inside a group; strength
    = Σ|gamma|, label price = the peak-|gamma| strike; clusters under 5% of the
    strongest are dropped. step defaults to the modal spacing of the FULL input
    grid (inferred before the floor, so a thinned surface keeps the true grid).
    Returns [{side, peak, lo, hi, strength, share, tier, above}] in strike order.
    """
    pts = sorted((float(k), float(g)) for k, g in (net_by_strike or []))
    if not pts:
        return []
    if step is None:
        step = infer_step([k for k, _ in pts])
    strongest_strike = max(abs(g) for _, g in pts)
    if strongest_strike <= 0:
        return []
    floor = STRIKE_FLOOR * strongest_strike
    pts = [(k, g) for k, g in pts if abs(g) >= floor - _EPS]

    groups: list[list[tuple[float, float]]] = [[pts[0]]]
    for k, g in pts[1:]:
        pk, pg = groups[-1][-1]
        if (g > 0) == (pg > 0) and (k - pk) <= 2 * step + _EPS:
            groups[-1].append((k, g))
        else:
            groups.append([(k, g)])

    raw = []
    for grp in groups:
        peak_k, peak_g = max(grp, key=lambda kg: abs(kg[1]))
        raw.append({"side": "call" if peak_g > 0 else "put",
                    "peak": peak_k, "lo": grp[0][0], "hi": grp[-1][0],
                    "strength": sum(abs(g) for _, g in grp)})
    strongest = max(c["strength"] for c in raw)

    clusters = []
    for c in raw:
        share = c["strength"] / strongest if strongest else 0.0
        if share < NOISE_FLOOR:
            continue
        clusters.append({**c, "share": round(share, 4),
                         "tier": prime_tier(share), "above": c["peak"] > spot})
    return clusters


def nearest_walls(clusters: list[dict], spot: float,
                  per_side: int = 3) -> dict:
    """The map's pick: up to `per_side` nearest clusters above and below spot,
    each list sorted nearest-first. Never forces the count — shows what exists."""
    ranked = sorted(clusters, key=lambda c: abs(c["peak"] - spot))
    return {"above": [c for c in ranked if c["peak"] > spot][:per_side],
            "below": [c for c in ranked if c["peak"] <= spot][:per_side]}


def pin_status(clusters: list[dict], spot: float, step: float) -> dict | None:
    """The cluster spot is currently pinned inside (its span ± one strike step),
    nearest peak winning when spans overlap — or None when spot is in the open."""
    inside = [c for c in clusters
              if c["lo"] - step - _EPS <= spot <= c["hi"] + step + _EPS]
    if not inside:
        return None
    return min(inside, key=lambda c: abs(c["peak"] - spot))
