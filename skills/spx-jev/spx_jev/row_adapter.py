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

What the SPX row does not carry and SNDK PRO's does: the options book's own time (``meta.book_asof``;
the SPX book is rebuilt on every scan, so a row's book is as old as the row), put-call skew and
next week's ladders. Labels built on those are not written for SPX.
"""
from __future__ import annotations

from typing import Any

SYMBOL = "SPX"
# top-level fields the labeller reads, as they are on the row
LABELLER_FIELDS = ("ts", "spot", "sigma", "vwap", "prior_close", "atm_iv", "vix_ts", "range_ruler", "adaptive_em",
                   "call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor")
# the fields read inside the row's nested views
LABELLER_VIEWS = {
    "gex_views": ("gamma_above_spot", "gamma_below_spot", "call_wall_gamma_share", "put_wall_gamma_share",
                  "pin_top_share", "vol_gross_by_strike", "vol_side_by_strike", "oi_side_by_strike",
                  "regime", "regime_tenor", "regime_source"),
    "dex_views": ("dex_above_spot",),
    "profile_ladder": ("state",),
}
# the dated books (the next monthlies and the quarter-end, pulled each morning): per band only what the labeller reads
DATED_BAND_FIELDS = ("expiry", "band", "dte", "gamma_mass")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def labeller_row(raw: Any) -> dict | None:
    """One diary row as the labeller reads it, or None when it is not a usable SPX row (another
    ticker, no timestamp, no numeric spot). A field the row lacks is simply absent, never None, so
    the labeller omits the label that needs it and says why."""
    if not isinstance(raw, dict) or raw.get("ticker", SYMBOL) != SYMBOL:
        return None
    if not isinstance(raw.get("ts"), str) or not _is_num(raw.get("spot")):
        return None
    row = {k: raw[k] for k in LABELLER_FIELDS if raw.get(k) is not None}
    for view, keys in LABELLER_VIEWS.items():
        block = raw.get(view)
        if isinstance(block, dict):
            kept = {k: block[k] for k in keys if block.get(k) is not None}
            if kept:
                row[view] = kept
    dated = raw.get("dated_gex")
    if isinstance(dated, dict) and isinstance(dated.get("bands"), list):
        row["dated_gex"] = {"staleness": dated.get("staleness"),
                            "bands": [{k: b.get(k) for k in DATED_BAND_FIELDS} for b in dated["bands"] if isinstance(b, dict)]}
    return row
