"""
track.py — where the underlying actually WAS while the board was filling.

The board draws activity per strike per slice. On its own it cannot say whether
a hot column was the money or a far wing nobody was trading near: a strike is a
fixed number, and 7730 means something completely different at 09:40 spot 7712
than it does at 15:30 spot 7741. The price track is the missing half — the
market's own path across the same price axis, on the same clock.

The source is the diary the station already writes (`state/reversion/{day}.jsonl`,
one row a scan, ~90 s apart, carrying `spot` and `vwap`). Nothing new is
recorded and nothing reaches the network: this is a second read of a file that
has been on disk since the session ran.

Per slice it reports FOUR numbers, not one:

    lo / hi   the range spot covered inside that slice
    last      where it finished the slice
    vwap      the session's volume-weighted average at that moment

lo/hi matter because a single point per slice is a lie about a fast tape. A
five-minute slice in which price ran twelve handles and one where it sat still
are the same dot and a very different session, so the renderer is given the
travel and can draw a band whose WIDTH is how much ground price covered.

Slices with no scan in them (the diary skips, and 30 s slices are finer than the
scan cadence) get `last` carried forward and `lo`/`hi` left null — a carried
level is a real thing to draw, an invented range is not.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# The diary is PER SYMBOL and the directory name is not derivable from the
# ticker: SPX writes state/reversion, SNDK writes state/sndk_reversion. Reading
# the wrong one is silent — the file parses, the rows carry a `ticker` that the
# filter then rejects, and `spot_track` returns None as if the day had no diary
# at all. A board would simply draw no price line and give no reason.
DIARY_DIR = {"SPX": "reversion", "SNDK": "sndk_reversion"}
DIARY = PLUGIN_ROOT / "state" / "{dir}" / "{day}.jsonl"


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _read_diary(day: str, ticker: str) -> list[tuple[datetime, float, Optional[float]]]:
    """(clock, spot, vwap) for one session, in time order. A row whose ticker
    does not match is skipped rather than blended — the diary is per-symbol and
    a mixed track would draw one symbol's path over another's board."""
    sub = DIARY_DIR.get(ticker)
    if sub is None:
        return []                                  # no diary for this symbol
    path = Path(str(DIARY).format(dir=sub, day=day))
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ticker") and r["ticker"] != ticker:
            continue
        ts, spot = _parse(r.get("ts") or ""), r.get("spot")
        if ts is None or not spot:
            continue
        vwap = r.get("vwap")
        out.append((ts, float(spot), float(vwap) if vwap else None))
    out.sort(key=lambda x: x[0])
    return out


def spot_track(day: str, ticker: str, slice_starts: list[str],
               slice_s: int) -> Optional[dict]:
    """Fold the diary onto the board's own slice grid.

    Returns arrays positionally aligned to `slice_starts` — the renderer indexes
    them with the same k the slider already holds, so the track cannot drift out
    of step with the board it is drawn on. Returns None when the day has no
    diary at all, which is a real state (the board can be older than the diary)
    and must not be drawn as a flat line at zero.
    """
    rows = _read_diary(day, ticker)
    if not rows:
        return None

    edges = [_parse(s) for s in slice_starts]
    if not edges or edges[0] is None:
        return None

    n = len(edges)
    lo: list[Optional[float]] = [None] * n
    hi: list[Optional[float]] = [None] * n
    last: list[Optional[float]] = [None] * n
    vw: list[Optional[float]] = [None] * n

    # One forward walk over both sequences. The diary is already sorted and the
    # slice grid is uniform, so a bisect would cost more than it saves.
    k = 0
    for ts, spot, vwap in rows:
        while k + 1 < n and edges[k + 1] is not None and ts >= edges[k + 1]:
            k += 1
        if edges[k] is None or ts < edges[k]:
            continue                              # before the board opens
        if k == n - 1 and (ts - edges[k]).total_seconds() >= slice_s:
            break                                 # after the board closes
        lo[k] = spot if lo[k] is None else min(lo[k], spot)
        hi[k] = spot if hi[k] is None else max(hi[k], spot)
        last[k] = spot
        if vwap is not None:
            vw[k] = vwap

    # Carry a known level forward through the gaps. `last` and `vwap` are LEVELS
    # — the price did not stop existing because no scan landed in that slice —
    # but lo/hi are RANGES measured inside a slice and are left null, because
    # carrying a range forward would draw travel that was never observed.
    for arr in (last, vw):
        prev = None
        for i in range(n):
            if arr[i] is None:
                arr[i] = prev
            else:
                prev = arr[i]

    obs = sum(1 for v in lo if v is not None)
    seen = [s for _, s, _ in rows]
    return {"lo": lo, "hi": hi, "last": last, "vwap": vw,
            "obs": obs, "n_rows": len(rows),
            "min": min(seen), "max": max(seen),
            "source": "reversion diary"}
