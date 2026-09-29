"""The SPX diary row, cut down to what the labeller reads.

The left-eye scanner writes one row per scan to ``state/reversion/{day}.jsonl``, about 40 KB of
engines, lenses and fire blocks. The labeller reads a few dozen of those numbers, so each row is
reduced here to exactly them, by name, and everything else is dropped on the way in: a day of rows
fits in memory, the clock can replay twenty sessions, and a field the labeller starts reading has
to be named in LABELLER_FIELDS first.

Which book a field describes (the left-eye GexBox): the strike shares, the gamma split, the heaviest
strike, the per-strike volume and open interest, the regime and the gamma ladder are today's 0DTE
book; ``call_wall``/``put_wall`` are the scanner's operative walls (the 0DTE gamma wall, the 0DTE
open-interest wall or the 1-to-7-day structural wall, whichever stands first); the ``*_tenor`` walls
and ``regime_tenor`` are the blended 1-to-7-day and 0-to-7-day books; ``dex_above_spot`` is the whole
0-to-7-day book; ``dated_gex`` is the monthly and quarter-end books, pulled each morning.

The ``siege`` block is the siege box's read at the scan (skills/siege): SPY volume spent while spot
touched a wall or the magnet, as a percentile of normal for that clock window, with the feed's health
and the baseline's maturity beside it. Its towers keep only the fields the labeller reads.

What the SPX row does not carry and SNDK PRO's does: the options book's own time (``meta.book_asof``;
the SPX book is rebuilt on every scan, so a row's book is as old as the row), put-call skew and
next week's ladders. Labels built on those are not written for SPX.
"""
from __future__ import annotations

from typing import Any

from .labels.measures import is_num

SYMBOL = "SPX"
# top-level fields the labeller reads, as they are on the row
LABELLER_FIELDS = ("ts", "spot", "sigma", "sigma_anchor", "sigma_live", "vwap", "prior_close", "atm_iv", "vix_ts", "range_ruler", "adaptive_em",
                   "call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor")
# the fields read inside the row's nested views
LABELLER_VIEWS = {
    "gex_views": ("gamma_above_spot", "gamma_below_spot", "call_wall_gamma_share", "put_wall_gamma_share",
                  "pin_top_share", "vol_gross_by_strike", "vol_side_by_strike", "oi_side_by_strike",
                  "regime", "regime_tenor", "regime_source"),
    "dex_views": ("dex_above_spot",),
    "profile_ladder": ("state",),
}
# The fields each label family (spx_jev/labels/) reads beyond the ones above, as "field" (kept whole) or
# "view.key", one line per family so that families add theirs without touching each other's lines.
FAMILY_FIELDS: dict[str, tuple[str, ...]] = {
    "context": (),
    "price": (),
    "gap_open": (),
    "range_size": (),
    "levels": ("level_reclaim.break_state", "level_reclaim.cock_direction", "level_reclaim.cock_level", "level_reclaim.cock_level_kind",
               "level_reclaim.cocked_at"),
    "vol": (),
    "gamma": ("gex_views.flip", "gex_views.regime_0dte_vol", "gex_views.magnet", "gex_views.pin_centroid", "gex_views.charm_wall",
              "gex_views.call_wall_gamma", "gex_views.put_wall_gamma", "gex_views.net_by_strike_tenor"),
    "options_flow": (),
    "expiry_calendar": (),
    "breadth": (),
    "leadership": (),
    "macro": (),
    "events_shocks": (),
    "premarket": (),
    "story": (),
    "bitcoin": (),
    "read_sequence": (),
}
# the dated books (the next monthlies and the quarter-end, pulled each morning): per band only what the labeller reads
DATED_BAND_FIELDS = ("expiry", "band", "dte", "gamma_mass")
# the siege read: its health and baseline, and per tower only what the labeller reads
SIEGE_FIELDS = ("health", "baseline", "saturated")
SIEGE_TOWER_FIELDS = ("kind", "level", "status", "effort_pct", "verdict", "outcome")


def labeller_row(raw: Any) -> dict | None:
    """One diary row as the labeller reads it, or None when it is not a usable SPX row (another
    ticker, no timestamp, no numeric spot). A field the row lacks is simply absent, never None, so
    the labeller omits the label that needs it and says why."""
    if not isinstance(raw, dict) or raw.get("ticker", SYMBOL) != SYMBOL:
        return None
    if not isinstance(raw.get("ts"), str) or not is_num(raw.get("spot")):
        return None
    family = [f for fields in FAMILY_FIELDS.values() for f in fields]
    row = {k: raw[k] for k in (*LABELLER_FIELDS, *(f for f in family if "." not in f)) if raw.get(k) is not None}
    views: dict[str, list[str]] = {view: list(keys) for view, keys in LABELLER_VIEWS.items()}
    for f in family:
        if "." in f:
            view, key = f.split(".", 1)
            views.setdefault(view, []).append(key)
    for view, keys in views.items():
        block = raw.get(view)
        if isinstance(block, dict):
            kept = {k: block[k] for k in keys if block.get(k) is not None}
            if kept:
                row[view] = kept
    dated = raw.get("dated_gex")
    if isinstance(dated, dict) and isinstance(dated.get("bands"), list):
        row["dated_gex"] = {"staleness": dated.get("staleness"),
                            "bands": [{k: b.get(k) for k in DATED_BAND_FIELDS} for b in dated["bands"] if isinstance(b, dict)]}
    siege = raw.get("siege")
    if isinstance(siege, dict) and isinstance(siege.get("towers"), list):
        row["siege"] = {**{k: siege.get(k) for k in SIEGE_FIELDS},
                        "towers": [{k: t.get(k) for k in SIEGE_TOWER_FIELDS} for t in siege["towers"] if isinstance(t, dict)]}
    return row
