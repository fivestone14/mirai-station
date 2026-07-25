"""lefteye_profile_ladder.py — the GAMMA PROFILE STATE LADDER (SHADOW, record-only).

The named levels of one scan's gamma profile, top to bottom in the GW notation
standard (docs/gw-vocab.md), each with its price and signed % distance from spot:

    GWc — nearest call-side gamma-wall cluster peak ABOVE spot
    cT  — call transition: the UPPER edge of the flip chop band
    HVL — the gamma flip itself (High Volatility Level — the ladder's DISPLAY
          name for the level; the storage key stays `gamma_flip`, keys are
          never renamed)
    pT  — put transition: the LOWER edge of the chop band
    GWp — nearest put-side gamma-wall cluster peak BELOW spot

plus `state`, the zone word for where spot actually sits:

    "positive"             spot above cT      (clear of the flip's doorstep)
    "positive transition"  cT ≥ spot > HVL    (inside the band, gamma-positive side)
    "negative transition"  HVL ≥ spot ≥ pT    (inside the band, gamma-negative side)
    "negative"             spot below pT

NOTHING NEW IS COMPUTED: the walls are gw_vocab.cluster_walls on the SAME
measured `gex_views.net_by_strike` surface every other GW consumer clusters,
and the transition band is the wt-8 chop band verbatim (± FLIP_CHOP_BAND_SIGMA
· σ around the flip — the zone watchtower already words as IN TRANSITION).
One rule, three rooms: doctrine, tower, ladder.

HONESTY: every level is measured-or-absent. No flip → no HVL/cT/pT and no
`state` (the zone word cannot be derived without the border — never guessed
from the regime sign). No measured per-strike surface → no GWc/GWp. Nothing
derivable at all → None (the diary key stays absent).

Pure, stdlib-only, no I/O. SHADOW: recorded to telemetry as `profile_ladder`,
consumed by nothing in the decision path (viewstation display data).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

import gw_vocab  # noqa: E402 — the one clustering rule (JS-twinned; never fork it)
from watchtower import FLIP_CHOP_BAND_SIGMA  # noqa: E402 — the wt-8 chop band, shared

# Surface era tag (cf. pin_field_v / dex_v): no A/B may ever pool rows across a
# definition change without checking this.
LADDER_V = 1


def _pct(level: Optional[float], spot: float) -> Optional[float]:
    """Signed % distance of a level from spot (+ above / − below spot)."""
    if level is None or not spot:
        return None
    return round((level - spot) / spot * 100.0, 3)


def read(net_by_strike, spot: Optional[float], sigma: Optional[float],
         flip: Optional[float]) -> Optional[dict]:
    """One ladder read off already-computed surfaces: `net_by_strike` is
    gex_views' measured per-strike signed field, `sigma` the day's σ ruler,
    `flip` the recorded gamma flip. Pure + fail-open: returns None when no
    rung is derivable (no spot, or neither a flip band nor a cluster)."""
    if not spot:
        return None

    # HVL + the transition band (wt-8 chop band: flip ± FLIP_CHOP_BAND_SIGMA·σ)
    ct = pt = state = None
    if flip is not None and sigma and sigma > 0:
        band = FLIP_CHOP_BAND_SIGMA * sigma
        ct, pt = round(flip + band, 4), round(flip - band, 4)
        if spot > ct:
            state = "positive"
        elif spot > flip:
            state = "positive transition"
        elif spot >= pt:
            state = "negative transition"
        else:
            state = "negative"

    # GWc / GWp — nearest same-side cluster peak each side of spot, from the ONE
    # clustering rule (concentration + noise floors already applied inside).
    gwc = gwp = None
    try:
        clusters = gw_vocab.cluster_walls(net_by_strike or [], spot)
    except (TypeError, ValueError):
        clusters = []          # a torn surface shows no walls, never a stand-in
    calls = [c for c in clusters if c["side"] == "call" and c["peak"] > spot]
    puts = [c for c in clusters if c["side"] == "put" and c["peak"] < spot]
    if calls:
        gwc = min(calls, key=lambda c: c["peak"] - spot)
    if puts:
        gwp = min(puts, key=lambda c: spot - c["peak"])

    if flip is None and gwc is None and gwp is None:
        return None            # no rung derivable → key absent, not a hollow record

    return {
        "state": state,
        "gwc": gwc["peak"] if gwc else None,
        "gwc_pct": _pct(gwc["peak"] if gwc else None, spot),
        "gwc_tier": gwc["tier"] if gwc else None,
        "ct": ct, "ct_pct": _pct(ct, spot),
        "hvl": flip, "hvl_pct": _pct(flip, spot),
        "pt": pt, "pt_pct": _pct(pt, spot),
        "gwp": gwp["peak"] if gwp else None,
        "gwp_pct": _pct(gwp["peak"] if gwp else None, spot),
        "gwp_tier": gwp["tier"] if gwp else None,
        "band_sigma": FLIP_CHOP_BAND_SIGMA,
        "ladder_v": LADDER_V,
    }
