"""sndk_board.py — the STRIKES PAYLOAD (strikes-1), the board-first scene.

Names (agreed 2026-09-05): Scene Payload v5 is the live scene sndk_read builds;
the Strikes Payload is this module's scene; the History additions (a
twelve-book series per strike, the next expiry's book) ride inside it under
`frames` and the strike table; the Regions block is sndk_regions' rule
answer; the Gate Payload is `legacy()`, the old magnet and walls kept beside
for the wake gate and the eval, never sent.

What the builder does, in order: calls the live `sndk_read.build_scene` (or
takes its output), keeps its clock, ruler, price, boxes, since-last-read and
freshness blocks with the verdict words stripped, drops the verdict blocks,
and adds `strikes` (a table: columns once, one row per strike), `frames`,
`between_frames`, `regions` and the rebuilt do-not-cite list.

Rules this file enforces, each pinned by a test:
  1. Every time is derived from a bar or a book, never typed separately.
  2. Absence is stated, never zero: a missing surface drops its columns and is
     named on the header; a strike missing from the earlier book says so; a
     missing minute is counted; an empty side is named.
  3. Nothing is blended: three rank columns, never one score. A rank is only
     emitted when its surface was measured.
  4. One fact, one home: the audit of 2026-09-05 removed every second copy.
  5. No verdict reaches the model: no magnet, wall, regime, band, breadth,
     flip, or label word survives, including the ones that rode inside the
     since-last-read frame as the reason for the wake.

Measured on this tape and respected here: a strike price already touched
returns less often than a plain strike at the same distance; contracts share
moves only through volume because open interest is last night's; the
per-strike gamma number is re-priced with spot every book and correlates 0.70
with distance, so only its SIGN and its SHARE ship, never its change.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import sndk_read as SR  # noqa: E402
import sndk_bars  # noqa: E402
import sndk_regions  # noqa: E402

# ---------------------------------------------------------------------------
# the switches the design names
# ---------------------------------------------------------------------------
STRIKE_LIST_MODE = "selected"   # "selected" (the union below) or "window" (everything in reach)
STRIKE_WINDOW_SIGMA = 1.5       # reach either side of the BOOK's spot
TOP_N = 8                       # per measure, for the union
NEAREST_EACH_SIDE = 2
AT_SIGMA = 0.05                 # |dist| under this reads "at"
CHANGE_BOOKS_FALLBACK = 5       # first read of a session: change over this many distinct books
LIST_AGE_LOOKBACK_ROWS = 120    # ~4h at 2-min scans, same as the wall ager
SERIES_BOOKS = 12               # the history behind each strike, in distinct books
STRIKE_LAYOUT = "records"       # "records": one object per strike, keys on every row;
                                # "table": columns once and one array per strike (half the
                                # characters, measured 3x the model's wall time: 48 s vs 15 s
                                # median on the 2026-09-05 eval, so records is the default)
SHIP_REGIONS = False            # the rule's LIVE regions in the scene. Off: in the first
                                # eval the model drew 34 of 34 clusters on the rule's regions,
                                # the anchoring the regions review said to stop on. The rule
                                # still runs every read for the guard's change word and for
                                # the Gate Payload record (v1["regions_rule"]).
SHIP_RESOLVED = True            # ...but `resolved` ships regardless, and the distinction is
                                # the whole point. `regions` is a list of piles that are THERE,
                                # and handing it over told the model what to draw. `resolved`
                                # is a list of piles that are GONE — it cannot anchor a cluster
                                # because there is nothing left at those prices to draw. It is
                                # the only evidence in the scene that a crowd the model named
                                # an hour ago has since left, and without it the doctrine's
                                # instruction to un-say things had nothing to stand on: the
                                # schema asked for `resolved` and the validator checked it
                                # against a block the model was never shown, so every answer
                                # it could give was dropped as invented.
# The doctrine's ceiling; over it is logged, never deleted. Raised 50 -> 60
# with the three-sentence rule, and the honesty matters: at 40 the old cap was
# breached by 34% of readings with no consequence, which teaches a model that
# the number is decoration. A cap nobody enforces still costs the prose, because
# the model clips its last thought to fit a line it is about to break anyway.
READ_WORDS_BUDGET = 60
                                # (forty was a dead letter: 47 to 62 words on every eval read)
GAP_FACTOR = 2.0                # an interval longer than this times the day's median cadence is a gap
MAX_CLUSTERS = 4
CHANGE_WORDS = sndk_regions.CHANGE_WORDS
KEPT_BLOCKS = ("data_sources", "clock", "scale", "price", "history",
               "freshness_rules", "context")
NULL_MEANS = "not measured for that strike"
# verdict words the live lists let through and this scene forbids; the live
# reader keeps "magnet" because its scene has a block by that name
BANNED_V2 = ("magnet", "magnets", "magnetic", "momentum", "building toward", "building towards",
             "stronger", "strongest", "weaker", "weakest", "wall", "walls", "flip")

_ET = SR._ET

COLUMNS_BASE = ["strike", "side", "dist_sigma"]
COLUMNS_TOUCH = ["touched_today", "first_touch", "last_touch", "bars_touched_today",
                 "shares_traded_at_strike_pp"]
COLUMNS_BOOK = ["oi_calls", "oi_puts", "vol_calls", "vol_puts", "contracts_share_pp"]
COLUMNS_GAMMA = ["dealer_gamma_sign", "dealer_gamma_share_pp"]
COLUMNS_RANK_C = ["rank_by_contracts"]
COLUMNS_RANK_V = ["rank_by_volume_today"]
COLUMNS_RANK_G = ["rank_by_dealer_gamma"]
COLUMNS_TAIL = ["on_list_for_min", "change", "vol_added_per_book", "vol_added_in_series"]
COLUMNS_TOUCH_SERIES = ["touched_in_books"]
COLUMNS_NEXT = ["next_week"]


# ---------------------------------------------------------------------------
# surfaces off a diary row — the per-strike arrays the engine already stores
# ---------------------------------------------------------------------------
def _pairs(seq) -> dict:
    out = {}
    for p in (seq or []):
        try:
            k, v = SR._fin(p[0]), SR._fin(p[1])
        except (TypeError, IndexError, KeyError):
            continue
        if k is not None and v is not None:
            out[k] = v
    return out


def _triples(seq) -> dict:
    out = {}
    for p in (seq or []):
        try:
            k, a, b = SR._fin(p[0]), SR._fin(p[1]), SR._fin(p[2])
        except (TypeError, IndexError, KeyError):
            continue
        if k is not None:
            out[k] = (a, b)
    return out


def surfaces(row: dict) -> dict:
    """Everything the engine kept per strike, keyed by strike, plus what the
    row does NOT carry, by name.

    `contracts` is open interest plus today's volume, both rights, computed
    from the side arrays (the identity mass == oi_c + oi_p + vol_c + vol_p held
    on 15,114 of 15,114 strike-rows checked), so a strike outside the engine's
    narrower mass window still gets a contracts count instead of ranking last
    for being unmeasured. The engine's mass array is the fallback for rows that
    carry no side arrays."""
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    net = _pairs(gv.get("net_by_strike"))
    oi_side = _triples(gv.get("oi_side_by_strike"))
    vol_side = _triples(gv.get("vol_side_by_strike"))
    mass = _pairs(gv.get("mass_by_strike"))
    absent = []
    contracts = {}
    if oi_side or vol_side:
        for k in set(oi_side) | set(vol_side):
            oc, op = oi_side.get(k, (None, None))
            vc, vp = vol_side.get(k, (None, None))
            contracts[k] = float((oc or 0) + (op or 0) + (vc or 0) + (vp or 0))
    elif mass:
        contracts = dict(mass)
        absent.append("open interest and volume by side per strike; contracts taken from the engine's mass array")
    else:
        absent.append("contracts per strike")
    if not oi_side:
        absent.append("open interest by side per strike")
    if not vol_side:
        absent.append("volume by side per strike; contracts counted from open interest only")
    if not net:
        absent.append("signed dealer gamma per strike")
    oi_next = _triples(gv.get("oi_side_by_strike_next"))
    vol_next = _triples(gv.get("vol_side_by_strike_next"))
    return {"contracts": contracts, "net": net, "oi_side": oi_side, "vol_side": vol_side,
            "oi_next": oi_next, "vol_next": vol_next,
            "next_dte": (int(gv["next_dte"]) if SR._fin(gv.get("next_dte")) is not None else None),
            "next_recorded": ("oi_side_by_strike_next" in gv or "vol_side_by_strike_next" in gv),
            "absent": absent}


def _ruler(row: dict) -> tuple:
    """(ruler_spot, sigma, live_spot, ruler_name) — the same rule build_scene
    uses: divide the BOOK's spot when the chain saw one within sanity, else
    the live spot and say so."""
    spot = SR._fin(row.get("spot"))
    sig = SR._fin(row.get("sigma")) or 0.0
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    book_spot = SR._fin(meta.get("chain_spot"))
    if book_spot is not None and spot is not None and (
            book_spot <= 0 or abs(book_spot - spot) / spot > SR.RULER_MAX_DRIFT):
        book_spot = None
    if book_spot is not None:
        return book_spot, sig, spot, None
    return spot, sig, spot, "live_spot_because_chain_spot_was_absent"


def _window(surf: dict, ruler_spot: float, sig: float,
            width_sigma: float = STRIKE_WINDOW_SIGMA) -> list:
    """Strikes in reach, sorted: the union of every surface's strikes, so a
    strike heavy on one measure is never dropped for being light on another."""
    if ruler_spot is None or not sig:
        return []
    ks = set(surf["contracts"]) | set(surf["net"]) | set(surf["oi_side"]) | set(surf["vol_side"])
    return sorted(k for k in ks if abs(k - ruler_spot) / sig <= width_sigma)


def _shares(surf: dict, window: list) -> dict:
    """Contracts share and dealer-gamma share, each over the WINDOW total."""
    ctot = sum(surf["contracts"].get(k, 0.0) for k in window)
    gtot = sum(abs(surf["net"].get(k, 0.0)) for k in window)
    out = {}
    for k in window:
        out[k] = {
            "contracts_share_pp": (round(surf["contracts"].get(k, 0.0) / ctot * 100, 2)
                                   if ctot > 0 and k in surf["contracts"] else None),
            "dealer_gamma_share_pp": (round(abs(surf["net"].get(k, 0.0)) / gtot * 100, 2)
                                      if gtot > 0 and k in surf["net"] else None),
        }
    return out


def _volume_today(surf: dict, k: float) -> Optional[float]:
    vc, vp = surf["vol_side"].get(k, (None, None))
    if vc is None and vp is None:
        return None
    return float((vc or 0) + (vp or 0))


def _ranks(surf: dict, window: list) -> dict:
    """Three rank columns, each over the window, 1 = heaviest, and each ONLY
    when its surface was measured (an absent surface ranks nothing). Unblended
    on purpose: the eval decides which one carried signal."""
    def rank(key, have):
        ks = [k for k in window if have(k)]
        order = sorted(ks, key=lambda k: (-key(k), k))
        return {k: i + 1 for i, k in enumerate(order)}
    by_c = rank(lambda k: surf["contracts"].get(k, 0.0), lambda k: k in surf["contracts"]) if surf["contracts"] else {}
    by_v = rank(lambda k: _volume_today(surf, k) or 0.0, lambda k: _volume_today(surf, k) is not None) if surf["vol_side"] else {}
    by_g = rank(lambda k: abs(surf["net"].get(k, 0.0)), lambda k: k in surf["net"]) if surf["net"] else {}
    return {k: (by_c.get(k), by_v.get(k), by_g.get(k)) for k in window}


def select_strikes(surf: dict, window: list, ruler_spot: float,
                   crossed: Optional[list] = None,
                   mode: str = STRIKE_LIST_MODE, top_n: int = TOP_N) -> list:
    """The union the design names: top N by contracts, top N by today's
    volume, top N by |dealer gamma|, the nearest NEAREST_EACH_SIDE strikes
    either side of spot, and anything crossed since the last read. A measure
    that was not recorded nominates nobody."""
    if mode == "window":
        return list(window)
    keep = set()
    if surf["contracts"]:
        keep |= set(sorted((k for k in window if k in surf["contracts"]),
                           key=lambda k: -surf["contracts"][k])[:top_n])
    if surf["vol_side"]:
        keep |= set(sorted((k for k in window if _volume_today(surf, k) is not None),
                           key=lambda k: -(_volume_today(surf, k) or 0))[:top_n])
    if surf["net"]:
        keep |= set(sorted((k for k in window if k in surf["net"]),
                           key=lambda k: -abs(surf["net"][k]))[:top_n])
    above = [k for k in window if k > ruler_spot]
    below = [k for k in window if k < ruler_spot]
    keep |= set(above[:NEAREST_EACH_SIDE])
    keep |= set(below[-NEAREST_EACH_SIDE:])
    for c in (crossed or []):
        c = SR._fin(c)
        if c is not None and c in window:
            keep.add(c)
    return sorted(keep)


# ---------------------------------------------------------------------------
# bars: touches and shares, every time derived from the bar that saw it
# ---------------------------------------------------------------------------
def _bar_ts(b: dict) -> Optional[datetime]:
    return SR._parse_ts(b.get("ts"))


def _hhmm(t: Optional[datetime]) -> Optional[str]:
    return t.astimezone(_ET).strftime("%H:%M") if t else None


def _wick_includes(b: dict, k: float) -> bool:
    lo, hi = SR._fin(b.get("low")), SR._fin(b.get("high"))
    return lo is not None and hi is not None and lo <= k <= hi


def touch_facts(bars_now: list, k: float, day_volume: float) -> dict:
    """What price did at the strike today, from the completed minute bars:
    whether a wick held it, the first and last minute that did, how many bars
    did, and the share of the day's traded shares that printed in those bars.
    Every time is the bar's own timestamp."""
    hits = [b for b in bars_now if _wick_includes(b, k)]
    vol = sum(SR._fin(b.get("volume")) or 0.0 for b in hits)
    return {"touched_today": bool(hits),
            "first_touch": _hhmm(_bar_ts(hits[0])) if hits else None,
            "last_touch": _hhmm(_bar_ts(hits[-1])) if hits else None,
            "bars_touched_today": len(hits),
            "shares_traded_at_strike_pp": (round(vol / day_volume * 100, 1)
                                           if day_volume > 0 else None)}


# ---------------------------------------------------------------------------
# the books behind the strikes: reference, age, series
# ---------------------------------------------------------------------------
def _asof(r: dict) -> Optional[datetime]:
    return SR._book_asof(r) or SR._ts(r)


def _reference(rows: list, last_read_ts: Optional[datetime]) -> tuple:
    """The book the change block compares against: the last distinct book at
    or before the model's last read, else CHANGE_BOOKS_FALLBACK distinct books
    back. Returns (ref_row | None, meta | None)."""
    books = SR._distinct_books_rows(rows)
    if not books:
        return None, None
    if last_read_ts is not None:
        before = [b for b in books if (t := _asof(b)) is not None and t <= last_read_ts]
        if before:
            ref = before[-1]
            n = len([b for b in books if (t := _asof(b)) is not None and t > _asof(ref)])
            if n == 0:
                # the book has not refreshed since the last read: comparing it with
                # itself would ship a change of zero on every strike and read as calm
                return None, {"unavailable": "no_new_book_since_last_read"}
            return ref, {"basis": "last_read", "books_compared": n}
    if len(books) > CHANGE_BOOKS_FALLBACK:
        ref = books[-1 - CHANGE_BOOKS_FALLBACK]
        if _first_book_suspect(books) and ref is books[0] and len(books) > CHANGE_BOOKS_FALLBACK + 1:
            ref = books[1]   # the day's first book carries the prior session's volume
            return ref, {"basis": f"{CHANGE_BOOKS_FALLBACK - 1}_books", "books_compared": CHANGE_BOOKS_FALLBACK - 1}
        return ref, {"basis": f"{CHANGE_BOOKS_FALLBACK}_books", "books_compared": CHANGE_BOOKS_FALLBACK}
    return None, None


def _on_list_minutes(rows: list, now: datetime, k: float, crossed: Optional[list]) -> Optional[int]:
    """How long the strike has been on the selected list, walking back the
    distinct books up to LIST_AGE_LOOKBACK_ROWS. None when never on it before."""
    since = None
    for r in reversed(rows[-LIST_AGE_LOOKBACK_ROWS:]):
        rs, sg, _, _ = _ruler(r)
        surf = surfaces(r)
        win = _window(surf, rs, sg)
        if not win or k not in select_strikes(surf, win, rs, crossed):
            break
        since = _asof(r)
    if since is None:
        return None
    return int((now - since).total_seconds() // 60)


def _window_volume(row: dict) -> Optional[float]:
    surf = surfaces(row)
    vols = [v for k in surf["vol_side"] if (v := _volume_today(surf, k)) is not None]
    return sum(vols) if vols else None


def _first_book_suspect(books: list) -> bool:
    """True when the day's first distinct book carries more volume than the
    second: the vendor's first print of the day is usually the prior session's
    cumulative count (measured on seven days, five had it)."""
    if len(books) < 2:
        return False
    a, b = _window_volume(books[0]), _window_volume(books[1])
    return a is not None and b is not None and a > b


def series_books(rows: list) -> tuple:
    """(books, first_book_dropped): the last SERIES_BOOKS distinct books,
    oldest first, each as {row, asof, spot, surf}; the day's first book is left
    out when its volume is the prior session's."""
    all_books = SR._distinct_books_rows(rows)
    dropped = False
    if _first_book_suspect(all_books):
        all_books = all_books[1:]
        dropped = True
    books = all_books[-SERIES_BOOKS:]
    out = []
    for r in books:
        rs, _, _, _ = _ruler(r)
        out.append({"row": r, "asof": _asof(r), "spot": rs, "surf": surfaces(r)})
    return [b for b in out if b["asof"] is not None], dropped


def frames_block(books: list, now: datetime, last_read_ts: Optional[datetime],
                 sig: float, ruler_spot: Optional[float], bars_now: list) -> Optional[dict]:
    """The shared time axis behind every per-strike series: the book times
    listed once, the minutes between them, any gap, whether the window reaches
    the last read, and where price sat at each book in sigma from now."""
    if not books:
        return None
    times = [b["asof"] for b in books]
    intervals = [round((b - a).total_seconds() / 60.0) for a, b in zip(times, times[1:])]
    med = statistics.median(intervals) if intervals else None
    gaps = []
    if med:
        for i, m in enumerate(intervals):
            if m > GAP_FACTOR * med and m > 8:
                gaps.append({"from": _hhmm(times[i]), "to": _hhmm(times[i + 1]), "minutes": m})
    out = {"books_in_series": len(books),
           "book_times": [_hhmm(t) for t in times],
           "interval_min": intervals}
    if gaps:
        out["gaps"] = gaps
    if last_read_ts is not None:
        out["reaches_last_read"] = times[0] <= last_read_ts
        out["books_since_last_read"] = sum(1 for t in times if t > last_read_ts)
    if sig and ruler_spot is not None:
        out["price_path_sigma_from_now"] = [
            (round((b["spot"] - ruler_spot) / sig, 2) if b["spot"] is not None else None) for b in books]
    if not bars_now:
        out["touches_unavailable"] = "no_minute_bars"
    return out


def _vol_added(books: list, k: float) -> tuple:
    """Contracts traded at the strike between consecutive books, both rights,
    and their sum. A book that did not carry the strike yields None for the
    intervals it bounds, never zero."""
    cum = [_volume_today(b["surf"], k) for b in books]
    per = []
    for a, b in zip(cum, cum[1:]):
        per.append(int(round(b - a)) if a is not None and b is not None else None)
    known = [v for v in per if v is not None]
    return per, (int(sum(known)) if known else None)


def _touched_in_books(books: list, k: float, bars_now: list) -> list:
    """Interval indexes (0-based, aligned to frames.interval_min) whose
    minute-bar wicks held the strike."""
    out = []
    for i, (a, b) in enumerate(zip(books, books[1:])):
        if any(a["asof"] < (t := _bar_ts(bar)) <= b["asof"] and _wick_includes(bar, k)
               for bar in bars_now if _bar_ts(bar) is not None):
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# the strike table
# ---------------------------------------------------------------------------
def _next_book_header(row: dict, surf: dict, now: datetime) -> dict:
    if not surf["next_recorded"]:
        return {"next_book": "not_recorded"}
    if not (surf["oi_next"] or surf["vol_next"]):
        return {"next_book_absent": "not_in_chain"}
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    nb = {"days_to_expiry": surf["next_dte"]}
    exps = meta.get("expiries")
    if isinstance(exps, list) and surf["next_dte"] is not None:
        for x in exps:
            if isinstance(x, dict) and x.get("dte") == surf["next_dte"] and isinstance(x.get("date"), str):
                nb["expiry_date"] = x["date"]
    nb = {k: v for k, v in nb.items() if v is not None}
    return {"next_book": nb} if nb else {"next_book_absent": "dte_unknown"}


def strikes_block(row: dict, rows: list, now: datetime, bars_now: list,
                  last_read_ts: Optional[datetime], crossed: Optional[list] = None) -> tuple:
    """(strikes, listed, ref_row, books). The table plus what the other blocks
    need from it."""
    ruler_spot, sig, spot, ruler_name = _ruler(row)
    surf = surfaces(row)
    window = _window(surf, ruler_spot, sig)
    if not window:
        return None, [], None, []
    shares = _shares(surf, window)
    ranks = _ranks(surf, window)
    listed = select_strikes(surf, window, ruler_spot, crossed)
    ref_row, ref_meta = _reference(rows, last_read_ts)
    ref = None
    if ref_row is not None:
        rsurf = surfaces(ref_row)
        rrs, rsg, _, _ = _ruler(ref_row)
        rwin = _window(rsurf, rrs, rsg)
        ref = {"surf": rsurf, "shares": _shares(rsurf, rwin), "win": rwin, "ruler": rrs}
    books, first_book_dropped = series_books(rows)
    day_volume = sum(SR._fin(b.get("volume")) or 0.0 for b in bars_now)

    # the columns present on THIS row: a surface that was not measured drops
    # its columns rather than shipping ranks built from zeros
    cols = list(COLUMNS_BASE)
    if bars_now:
        cols += COLUMNS_TOUCH
    cols += COLUMNS_BOOK
    if surf["net"]:
        cols += COLUMNS_GAMMA
    if surf["contracts"]:
        cols += COLUMNS_RANK_C
    if surf["vol_side"]:
        cols += COLUMNS_RANK_V
    if surf["net"]:
        cols += COLUMNS_RANK_G
    cols += COLUMNS_TAIL
    if bars_now:
        cols += COLUMNS_TOUCH_SERIES
    if surf["next_recorded"] and (surf["oi_next"] or surf["vol_next"]):
        cols += COLUMNS_NEXT

    rows_out = []
    listed_by_weight = set(select_strikes(surf, window, ruler_spot))   # without the crossed set
    for k in listed:
        d = (k - ruler_spot) / sig if sig else None
        oc, op = surf["oi_side"].get(k, (None, None))
        vc, vp = surf["vol_side"].get(k, (None, None))
        net = surf["net"].get(k)
        rec = {"strike": k,
               "side": ("at" if d is not None and abs(d) < AT_SIGMA else "above" if k > ruler_spot else "below"),
               "dist_sigma": round(d, 2) if d is not None else None,
               "oi_calls": int(oc) if oc is not None else None,
               "oi_puts": int(op) if op is not None else None,
               "vol_calls": int(vc) if vc is not None else None,
               "vol_puts": int(vp) if vp is not None else None,
               "contracts_share_pp": shares[k]["contracts_share_pp"],
               "dealer_gamma_sign": (None if net is None else "+" if net > 0 else "-" if net < 0 else "0"),
               "dealer_gamma_share_pp": shares[k]["dealer_gamma_share_pp"],
               "rank_by_contracts": ranks[k][0], "rank_by_volume_today": ranks[k][1],
               "rank_by_dealer_gamma": ranks[k][2],
               # a strike on the list only because it was crossed since the last
               # read has no listed age: "never listed before" is the honest answer
               "on_list_for_min": (_on_list_minutes(rows, now, k, None) if k in listed_by_weight else None)}
        if bars_now:
            rec.update(touch_facts(bars_now, k, day_volume))
            rec["touched_in_books"] = _touched_in_books(books, k, bars_now) or None   # empty ships as absent
        # the change cell: differences against the reference book, or why not
        if ref is None:
            rec["change"] = None
        elif k in ref["surf"]["contracts"] or k in ref["surf"]["vol_side"]:
            rvc, rvp = ref["surf"]["vol_side"].get(k, (None, None))
            rshare = ref["shares"].get(k, {}).get("contracts_share_pp")
            rec["change"] = [
                (round(shares[k]["contracts_share_pp"] - rshare, 2)
                 if shares[k]["contracts_share_pp"] is not None and rshare is not None else None),
                (int(vc - rvc) if vc is not None and rvc is not None else None),
                (int(vp - rvp) if vp is not None and rvp is not None else None)]
        else:
            rec["change"] = "strike_not_in_earlier_book"
        per, tot = _vol_added(books, k)
        rec["vol_added_per_book"] = per
        rec["vol_added_in_series"] = tot
        if "next_week" in cols:
            noc, nop = surf["oi_next"].get(k, (None, None))
            nvc, nvp = surf["vol_next"].get(k, (None, None))
            if k in surf["oi_next"] or k in surf["vol_next"]:
                rec["next_week"] = [int(noc) if noc is not None else None, int(nop) if nop is not None else None,
                                    int(nvc) if nvc is not None else None, int(nvp) if nvp is not None else None]
            else:
                rec["next_week"] = "strike_not_in_next_weekly_book"
        rows_out.append(rec)
    rows_out.sort(key=lambda r: (-(r.get("contracts_share_pp") or 0), r["strike"]))

    above = [k for k in window if k > ruler_spot]
    below = [k for k in window if k < ruler_spot]
    ctot = sum(surf["contracts"].get(k, 0.0) for k in window)
    gtot = sum(abs(surf["net"].get(k, 0.0)) for k in window)
    la = [k for k in listed if k > ruler_spot]
    lb = [k for k in listed if k < ruler_spot]
    head = {
        "strikes_in_window": len(window),
        "sigma_measured_from": ruler_name,
        "contracts_above_spot_pp": (round(sum(surf["contracts"].get(k, 0.0) for k in above) / ctot * 100, 1) if ctot > 0 else None),
        "dealer_gamma_above_spot_pp": (round(sum(abs(surf["net"].get(k, 0.0)) for k in above) / gtot * 100, 1) if gtot > 0 else None),
        "no_strikes_above": True if not above else None,
        "no_strikes_below": True if not below else None,
        "nearest_above": (la[0] if la else None),
        "nearest_below": (lb[-1] if lb else None),
        "change_basis": (ref_meta.get("basis") if ref_meta else None),
        "change_books_compared": (ref_meta.get("books_compared") if ref_meta else None),
        "change_unavailable": ((ref_meta or {}).get("unavailable") if ref_meta else "no_earlier_book"),
        "change_columns": (["contracts_share_pp", "vol_calls", "vol_puts"] if ref is not None else None),
        "first_book_dropped": ("the day's first book carried the prior session's volume" if first_book_dropped else None),
        "next_week_columns": (["oi_calls", "oi_puts", "vol_calls", "vol_puts"] if "next_week" in cols else None),
    }
    head.update(_next_book_header(row, surf, now))
    if ref is not None:
        prev_listed = set(select_strikes(ref["surf"], ref["win"], ref["ruler"])) if ref["win"] else set()
        head["entered_since_reference"] = sorted(set(listed) - prev_listed) or None
        head["left_since_reference"] = sorted(prev_listed - set(listed)) or None
    # `absent` is the ONE place the doctrine tells a reader to look for a
    # missing column ("a field missing from `columns` ... `strikes.absent` says
    # why"). The bar sidecar going down drops six columns and used to say so
    # only through `touches_unavailable`, a second name for the same fact that
    # the doctrine never mentions — so the promise was false exactly when it
    # mattered. Both ship now: the flag for the consumers that already read it,
    # the sentence for the reader the doctrine addressed.
    absent = list(surf["absent"])
    if not bars_now:
        head["touches_unavailable"] = "no_minute_bars"
        absent.append("which strikes price touched today, and when; the minute-bar "
                      "record was not on disk for this session")
    if absent:
        head["absent"] = absent
    head["columns"] = cols
    if STRIKE_LAYOUT == "table":
        head["rows"] = [[r.get(c) for c in cols] for r in rows_out]
    else:
        head["rows"] = [{c: r.get(c) for c in cols if r.get(c) is not None} for r in rows_out]
    head = {k: v for k, v in head.items() if v is not None}
    return head, listed, ref_row, books


def rows_as_records(strikes: Optional[dict]) -> list:
    """The strikes as one dict per strike whichever layout shipped, for guards
    and evals."""
    if not strikes or not strikes.get("rows"):
        return []
    cols = strikes["columns"]
    out = []
    for r in strikes["rows"]:
        if isinstance(r, dict):
            out.append({c: r.get(c) for c in cols})
        else:
            out.append(dict(zip(cols, r)))
    return out


# ---------------------------------------------------------------------------
# between the frames — what happened while the model slept
# ---------------------------------------------------------------------------
def between_frames_block(rows: list, bars_now: list, now: datetime,
                         last_read_ts: Optional[datetime], sig: float) -> Optional[dict]:
    """The gap's tape. Its clock (from, to, minutes) and its net change live in
    context.since_last_read; boxes broken in it are context.ranges.breaks_today
    with their clocks; the books in it are strikes.change_books_compared. None
    of those is repeated here."""
    if last_read_ts is None:
        return {"first_read_of_session": True}
    bars = [b for b in bars_now if (t := _bar_ts(b)) is not None and last_read_ts < t <= now]
    span = [r for r in rows if (t := SR._ts(r)) is not None and last_read_ts < t <= now]
    out = {}
    expected = max(0, int((now - last_read_ts).total_seconds() // 60) - 1)
    if bars_now:
        # sr-9: zero on 130 of 130 replayed scans that carried it. A gap is a
        # gap and must always be stated; NO gap is the standing case and is
        # said once in the doctrine instead of on every scan.
        if (missing := max(0, expected - len(bars))):
            out["missing_minutes"] = missing
    else:
        out["minute_bars_unavailable"] = "no_minute_bars_on_disk"
    ref = next((r for r in reversed(rows) if (t := SR._ts(r)) is not None and t <= last_read_ts), None)
    price = {}
    if bars:
        lo_b = min(bars, key=lambda b: SR._fin(b.get("low")) if SR._fin(b.get("low")) is not None else float("inf"))
        hi_b = max(bars, key=lambda b: SR._fin(b.get("high")) if SR._fin(b.get("high")) is not None else float("-inf"))
        # sr-9: the witness is stated only when it is NOT the minute bars.
        # This arm IS the minute bars, so it says nothing; the `elif span` arm
        # below still names itself, because that is the case worth knowing.
        price = {"low": SR._fin(lo_b.get("low")), "low_at": _hhmm(_bar_ts(lo_b)),
                 "high": SR._fin(hi_b.get("high")), "high_at": _hhmm(_bar_ts(hi_b))}
        closes = [SR._fin(b.get("close")) for b in bars if SR._fin(b.get("close")) is not None]
        if len(closes) >= 2 and sig:
            price["path_travelled_sigma"] = round(sum(abs(b - a) for a, b in zip(closes, closes[1:])) / sig, 2)
        vols = [SR._fin(b.get("volume")) or 0.0 for b in bars]
        day_vols = [SR._fin(b.get("volume")) or 0.0 for b in bars_now]
        med = statistics.median(day_vols) if day_vols else 0.0
        if med > 0 and vols:
            out["shares_traded"] = {"in_gap": int(sum(vols)),
                                    "per_minute_vs_day_median": round((sum(vols) / len(vols)) / med, 2)}
    elif span:
        lo_r = min(span, key=lambda r: SR._fin(r.get("spot")) or float("inf"))
        hi_r = max(span, key=lambda r: SR._fin(r.get("spot")) or float("-inf"))
        price = {"low": SR._fin(lo_r.get("spot")), "low_at": _hhmm(SR._ts(lo_r)),
                 "high": SR._fin(hi_r.get("spot")), "high_at": _hhmm(SR._ts(hi_r)),
                 "measured_from": "scans_every_2_min"}
        spots = [SR._fin(r.get("spot")) for r in span if SR._fin(r.get("spot")) is not None]
        if len(spots) >= 2 and sig:
            price["path_travelled_sigma"] = round(sum(abs(b - a) for a, b in zip(spots, spots[1:])) / sig, 2)
    if price:
        out["price"] = {k: v for k, v in price.items() if v is not None}
    iv_then = SR._fin(ref.get("atm_iv")) if ref is not None else None
    if iv_then is not None:
        out["implied_vol_at_last_read"] = round(iv_then, 4)   # now is scale.implied_vol_atm
    return out or None


# ---------------------------------------------------------------------------
# the shadow: the Gate Payload, computed beside, never shown
# ---------------------------------------------------------------------------
def legacy(row: dict, rows: list, now: datetime, v1: Optional[dict] = None) -> dict:
    """The old magnet, walls, bands, regime label and gamma sign, plus the
    blocks the model stopped seeing, for the read row's `legacy` key, the
    wake gate and the eval."""
    band = SR.magnet_band(row)
    ruler_spot, sig, spot, ruler_name = _ruler(row)

    def sd(v):
        v = SR._fin(v)
        return round((v - ruler_spot) / sig, 2) if v is not None and sig and ruler_spot is not None else None
    out = {"magnet": (band["top"][0][0] if band["top"] else None),
           "magnet_top3": band["top"],
           "magnet_lead_pp": band["gap_pp"],
           "gamma_sign": row.get("gamma_sign"),
           "regime_label": row.get("regime")}
    if v1 is not None:
        for k in ("walls", "structure", "breadth", "momentum", "dealer_positioning", "regime", "magnet", "regions_rule"):
            if k in v1:
                out[k if k not in ("regime", "magnet") else k + "_block"] = v1[k]
        return out
    try:
        out["walls"] = SR.walls_ladder(row, sd, rows, now, ruler_spot=ruler_spot, ruler_name=ruler_name)
    except Exception:
        out["walls"] = None
    try:
        out["structure"] = SR.structure_block(row, ruler_spot)
    except Exception:
        out["structure"] = None
    return out


# ---------------------------------------------------------------------------
# the v2 scene
# ---------------------------------------------------------------------------
def _crossed_from_frame(frame: Optional[dict]) -> list:
    out = []
    for c in ((frame or {}).get("crossed_since_then") or []):
        if isinstance(c, dict):
            for key in ("level", "strike", "price"):
                v = SR._fin(c.get(key))
                if v is not None:
                    out.append(v)
                    break
        else:
            v = SR._fin(c)
            if v is not None:
                out.append(v)
    return out


def _strip_frame(slr: dict, bf: Optional[dict]) -> None:
    """The since-last-read frame minus every old label: the wake reason (it
    named a hedging flip on 8 of 22 scenes that had none), the wall labels on
    crossings, the nothing-crossed flag that meant the gate's walls, the move
    or hold word, the live price copy and the scan-witness range."""
    for k in ("frame_is", "walls_absent_then_and_now", "why_this_read",
              "nothing_crossed_since_then", "spot_now", "unchanged_since_then"):
        slr.pop(k, None)
    for k in list(slr):
        if "wall" in k or "magnet" in k or "relabel" in k or "flip" in k:
            slr.pop(k, None)
    cs = slr.get("crossed_since_then")
    if isinstance(cs, list):
        cleaned = []
        for c in cs:
            if isinstance(c, dict):
                lvl = next((SR._fin(c.get(key)) for key in ("level", "strike", "price") if SR._fin(c.get(key)) is not None), None)
                if lvl is not None:
                    d = {"level": lvl}
                    for key in ("direction", "price_went"):
                        if isinstance(c.get(key), str):
                            d["direction"] = c[key]
                            break
                    cleaned.append(d)
        if cleaned:
            slr["crossed_since_then"] = cleaned
        else:
            slr.pop("crossed_since_then", None)
    if isinstance((bf or {}).get("price"), dict):
        slr.pop("held_between_since_last_read", None)
    for k in list(slr):
        if k.startswith("first_read"):
            slr.pop(k, None)   # between_frames says it, once


def _trim_data_sources(ds: dict) -> None:
    """The clocks that vary and can be checked; the derived and constant ones
    leave (cache_age_s is age_min in seconds; the counts and cadences were
    cited in 0 of 22 reads)."""
    for k in ("scans_so_far_today", "scan_interval_min"):
        ds.pop(k, None)
    ob = ds.get("options_book")
    if isinstance(ob, dict):
        for k in ("cache_age_s", "distinct_books_so_far_today", "refresh_interval_min"):
            ob.pop(k, None)
    mb = ds.get("minute_bars")
    if isinstance(mb, dict):
        mb.pop("age_min", None)


def frozen_v2(rows: list, now: datetime, strikes: Optional[dict]) -> list:
    """The do-not-cite list rebuilt from the table itself, so the two agree by
    construction: the top row's age and the list membership's age."""
    recs = rows_as_records(strikes)
    if not recs:
        return []
    out = []
    top = recs[0]
    since = None
    for r in reversed(rows[-LIST_AGE_LOOKBACK_ROWS:]):
        rs, sg, _, _ = _ruler(r)
        surf = surfaces(r)
        win = _window(surf, rs, sg)
        if not win or not surf["contracts"]:
            break
        lead = max((k for k in win if k in surf["contracts"]), key=lambda k: (surf["contracts"][k], -k), default=None)
        if lead != top["strike"]:
            break
        since = _asof(r)
    if since is not None:
        mins = int((now - since).total_seconds() // 60)
        if mins >= SR.FROZEN_MIN:
            out.append(f"{top['strike']:g} top of list for {mins}m")
    ages = [r.get("on_list_for_min") for r in recs if r.get("on_list_for_min") is not None]
    if ages and len(ages) == len(recs) and min(ages) >= SR.FROZEN_MIN:
        out.append(f"list membership unchanged {min(ages)}m")
    return out


def build_scene_v2(row: dict, rows: list, now: datetime,
                   since_last_read: Optional[dict] = None,
                   last_read_ts: Optional[datetime] = None,
                   bars: Optional[list] = None,
                   v1: Optional[dict] = None,
                   clusters_then: Optional[list] = None) -> tuple:
    """(scene_v2, scene_v1). The kept blocks are the live builder's output with
    the labels stripped; the verdict blocks are dropped; `strikes`, `frames`,
    `between_frames` and `regions` are added. `bars` is the day's minute bars
    (clipped here), or None to read them off disk. `v1` is the live scene when
    the caller already built it. `clusters_then` is what the model drew at its
    last read, so `new` and `resolved` have a reference."""
    if v1 is None:
        band = SR.magnet_band(row)
        frozen = SR.frozen_fields(rows, now)
        v1 = SR.build_scene(row, band, frozen, rows, now, since_last_read)
    day = now.strftime("%Y-%m-%d")
    all_bars = bars if bars is not None else SR.minute_bars(day)
    bars_now = sndk_bars.completed(all_bars, now) if all_bars else []
    ruler_spot, sig, spot, ruler_name = _ruler(row)

    v2 = {}
    for k in KEPT_BLOCKS:
        if k in v1:
            v2[k] = json.loads(json.dumps(v1[k], default=str))
    book_age = SR._fin(((v1.get("data_sources") or {}).get("options_book") or {}).get("age_min"))
    book_too_old = book_age is not None and book_age > SR.MAX_BOOK_AGE_MIN
    if isinstance(v2.get("data_sources"), dict):
        _trim_data_sources(v2["data_sources"])
    if "scale" in v1:   # never rebuilt after the freshness gate dropped it
        sc = v2.get("scale") or {}
        if isinstance(sc.get("expected_move_today_asym"), dict):
            sc["expected_move_today_asym"].pop("skewed_toward", None)
        iv = SR._fin(row.get("atm_iv"))
        if iv is not None and not book_too_old:
            sc["implied_vol_atm"] = round(iv, 4)
        v2["scale"] = sc
    hist = v2.get("history") or {}
    hist.pop("tape_abnormal_vs_own_history", None)
    if not hist:
        v2.pop("history", None)
    ctx = v2.get("context") or {}
    # only the boxes and the frame survive from the live context: the ranks
    # against prior sessions describe removed blocks, and changed_since_last_book
    # named the old gamma sign and the nearest walls (it reached two of eleven
    # reads in the Sep 5 eval as "the nearest call wall moved down to 1550")
    for k in list(ctx):
        if k not in ("ranges", "since_last_read"):
            ctx.pop(k, None)
    slr = ctx.get("since_last_read")
    # crossings are LISTED strikes price moved through since the last read, not the
    # gate's three verdict levels: every window strike between price then and now
    spot_then = SR._fin((slr or {}).get("spot_then"))
    surf0 = surfaces(row)
    crossed = []
    if spot_then is not None and spot is not None:
        for k in _window(surf0, ruler_spot, sig):
            if (spot - k) * (spot_then - k) < 0 or (spot_then == k and spot != k):
                crossed.append(k)

    if book_too_old:
        strikes, listed, ref_row, books = None, [], None, []
        v2["strikes"] = {"unavailable": "book_too_old", "age_min": book_age, "ceiling_min": SR.MAX_BOOK_AGE_MIN}
    else:
        strikes, listed, ref_row, books = strikes_block(row, rows, now, bars_now, last_read_ts, crossed)
    bf = between_frames_block(rows, bars_now, now, last_read_ts, sig)
    if isinstance(slr, dict):
        _strip_frame(slr, bf)
        if crossed:
            slr["crossed_since_then"] = [{"level": k, "direction": ("up" if spot > k else "down")} for k in sorted(crossed)]
        else:
            slr.pop("crossed_since_then", None)
        if clusters_then:
            slr["clusters_then"] = [{"center": SR._fin(c.get("center")), "strikes": [SR._fin(k) for k in (c.get("strikes") or [])]}
                                    for c in clusters_then if isinstance(c, dict) and SR._fin(c.get("center")) is not None]
    if strikes:
        v2["strikes"] = strikes
        fr = frames_block(books, now, last_read_ts, sig, ruler_spot, bars_now)
        if fr:
            v2["frames"] = fr
        try:
            rg = sndk_regions.regions_block(rows, now, ref_row, listed, sys.modules[__name__])
        except Exception as exc:   # the block goes absent with its reason, never a stale answer
            rg = {"unavailable": f"{type(exc).__name__}: {exc}"[:120]}
        if rg:
            # the rule's answer always rides on the legacy scene for the guard and
            # the Gate Payload record; it reaches the model only when SHIP_REGIONS
            v1["regions_rule"] = rg
            if SHIP_REGIONS:
                v2["regions"] = rg
            elif SHIP_RESOLVED and rg.get("resolved"):
                v2["regions"] = {"resolved": rg["resolved"]}
    if bf:
        v2["between_frames"] = bf
    fz = frozen_v2(rows, now, strikes)
    if fz:
        v2["frozen_do_not_cite"] = fz
    v2 = {k: v for k, v in v2.items() if v not in (None, {}, [])}
    return v2, v1


# ---------------------------------------------------------------------------
# side by side
# ---------------------------------------------------------------------------
def leaf_paths(x, prefix: str = "") -> set:
    out = set()
    if isinstance(x, dict):
        for k, v in x.items():
            out |= leaf_paths(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(x, list):
        if not x:
            out.add(prefix + "[]")
        for v in x:
            out |= leaf_paths(v, prefix + "[]")
    else:
        out.add(prefix)
    return out


def compare_scenes(v1: dict, v2: dict) -> dict:
    a, b = leaf_paths(v1), leaf_paths(v2)
    return {"removed": sorted(a - b), "added": sorted(b - a), "kept": sorted(a & b),
            "chars_v1": len(json.dumps(v1, default=str)),
            "chars_v2": len(json.dumps(v2, default=str))}


# ---------------------------------------------------------------------------
# the doctrine for the board-first scene
# ---------------------------------------------------------------------------
DOCTRINE_V2 = f"""You are watching one stock's option board and saying what you NOTICE. You are not forecasting. Nobody wants to know where you think price is going: that question was asked of you for a year, measured, and found to carry no information at all. What is wanted is what you would actually say to someone who just walked up and asked "anything going on?" — what is there, what changed since you last spoke, and nothing about what happens next.

TALK LIKE A PERSON. Someone is sitting next to you who knows markets but not options jargon, and you are pointing at the screen. "1750 has been eating calls all afternoon — a couple of thousand since we last spoke, and it now leads the board on every measure" is what a human says. "contracts_share_pp = 16.3" is not, and neither is a sentence built from these instructions rather than from today's board: THE EXAMPLES HERE ARE SHAPES, NOT SENTENCES TO RETURN. If your answer could have been written before the market opened, it is not a reading. Full sentences, plain words, no field names in the prose. Every number is checked against the scene before anyone reads your answer. The numbers inside these instructions are teaching aids, never board values.

THE BOARD IS AN AUCTION HOUSE, AND A SHARED DOCUMENT. Read it that way:
- CONTRACTS ARE WHERE THE CROWD SITS. Open interest is last night's crowd: positions that exist, struck at the prior close and constant all day. It still matters because everyone else is reading the same document and reacting to it. Round strikes gather the crowd; say that by the share, never by the roundness.
- VOLUME TODAY IS WHERE THE ACTION IS. Contracts traded so far today, cumulative. It is the only thing on the board that moved today because someone traded. Market makers go where the volume is, so today's leader on volume is where the day's business is being done.
- DEALER GAMMA IS THE ASSUMED HEDGING SURFACE. Signed, calls positive and puts negative, under a convention written for the index and probably wrong for this stock. It moves during the day because price and implied vol moved under fixed open interest, not because anyone took a position. That is why only its sign and its share ship, never its change.
- THE THREE RANKS ARE KEPT APART ON PURPOSE, AND THEIR DISAGREEMENT IS THE STORY. When contracts, volume today and gamma all lead at the same strike, say so in five words. When they split, say which measure leads where: "1500 holds the most contracts as of last night, 1550 has taken the most volume today, and the gamma piles at 1510." That sentence is the reading. A blended winner is not.
- A HEAVIER SIDE, NEVER A STRONGER ONE. Forces sit on both sides of price. Which side is heavier is a share you can read (`contracts_above_spot_pp`, `dealer_gamma_above_spot_pp`, the strikes themselves). Say heavier and lighter, by measure. Strong and weak are promises about what price will do, and they delete your sentence.

THE STRIKE TABLE. `strikes.rows` holds one record per strike, sorted by contracts share, heaviest first, and `strikes.columns` names every field a record can carry. A field missing from a record was not measured for that strike; a field missing from `columns` was not measured for any strike this scan, and `strikes.absent` says why. The fields:
- `strike`, `side`, `dist_sigma`: where the STRIKE sits relative to the price the book was measured at, in sigma (a normal day's move). "above" means the strike is above price; `at` is within a twentieth of a sigma. To place a strike against the live price use `price.live_spot`; on a cached book the two prices differ, and `price.live_minus_book_spot_sigma` is the difference.
- `touched_today`, `first_touch`, `last_touch`, `bars_touched_today`: whether a completed minute bar's wick held that strike today, the first and last minute that did, and how many bars did. A strike price sat on for forty bars, a strike it brushed once, and a strike it has not reached are three different things; say which.
- `shares_traded_at_strike_pp`: the share of the day's stock volume that printed in bars whose range held the strike. Where the shares actually traded.
- `oi_calls`, `oi_puts`: open interest as of last night's close. "1700 holds the most open interest as of last night's close" is a correct sentence; "open interest is building at 1700" is false by construction.
- `vol_calls`, `vol_puts`: contracts traded today so far, cumulative.
- `contracts_share_pp`: this strike's share of all contracts in reach (open interest plus today's volume, both rights). Where the crowd is, counting today's arrivals.
- `dealer_gamma_sign`, `dealer_gamma_share_pp`: the sign at the strike and its share of the surface's absolute total. Report a sign as "positive under the assumed convention"; never as a behaviour. Bounce, break, punch through, how violent: all forecast, all deleted.
- `rank_by_contracts`, `rank_by_volume_today`, `rank_by_dealer_gamma`: three separate rankings over every strike in reach, 1 is heaviest. A rank column that is missing was not measured this scan.
- `on_list_for_min`: how long the strike has been on this list. Hours means standing structure, not news.
- `change`: this book against an earlier one, three differences in the order `strikes.change_columns` gives: contracts share, calls traded, puts traded. The header says once which earlier book (`change_basis`: the book at your last read, or five books back on the session's first read) and how many books lie between (`change_books_compared`); `change_unavailable` says why there is none, including `no_new_book_since_last_read`, which means the book has not refreshed since you last spoke and nothing on it can have changed. The string `strike_not_in_earlier_book` means the strike was outside the earlier window, a fact about the window.
- `vol_added_per_book`: contracts traded at the strike, both rights, between consecutive book times. The book times are listed once in `frames.book_times` and `frames.interval_min` says how many minutes each entry covers. `vol_added_in_series` is the sum; it is the only sum you may quote. Describe the series by counting: "rose in 9 of the last 12 books", "800 of its 1,200 contracts came between 11:02 and 11:06", "added nothing since 12:31". A null entry means the strike was not in one of the two books. A negative entry is the vendor correcting its count, not selling. `strikes.first_book_dropped`, when present, says the day's first book was left out because it carried the prior session's volume.
- `touched_in_books`: which of those intervals had a wick at the strike, by index; absent when none did.
- `next_week`: the next weekly expiry's open interest and volume at the same strike, in the order `strikes.next_week_columns` gives. Open interest in either book is last night's; volume in either is today's. On expiry day the front list dies at the close and the next week's book is Monday's list. `not_recorded` on the header means the diary had not yet kept the next book that day.
The header also carries `strikes_in_window` (how many were in reach), `contracts_above_spot_pp` and `dealer_gamma_above_spot_pp`, `nearest_above` and `nearest_below` (the nearest listed strike each side of the book's price, or absent when the side is empty; `no_strikes_above` and `no_strikes_below` say so outright), and `entered_since_reference` and `left_since_reference` (strikes that joined or left the list since the earlier book).

THE FRAMES. `frames` is the shared time axis behind every series: `book_times` once, `interval_min` between them, `gaps` naming any long interval, `reaches_last_read` and `books_since_last_read` saying whether the series covers the stretch since you last spoke, and `price_path_sigma_from_now`: where price sat at each book, in today's sigma, zero at the price the book was measured at. It lets you say when volume arrived relative to where price was: "most of 1750's volume arrived while price sat below 1720". It does not let you say what price did about it. An entry over a long interval is one lump for the whole stretch: name the minutes and call its shape unknown. With fewer than six books in the series, say nothing about the shape of any series.

WHAT HAS GONE. `regions.resolved` ships on every read that has one, and it is the shortest and most useful block in the scene: piles that were on the board at your last read and are not on it now, each with the last book that held them. It is the evidence behind the rule above about un-saying things. A centre in that list is a crowd that has left — say so before you say anything new, and name it in your `resolved` field. The live `regions` list is deliberately NOT shipped, because when it was, the model drew 34 of 34 clusters on it and stopped looking at the board; `resolved` cannot do that, because there is nothing at those prices left to draw.

THE REGIONS RULE, for reading `resolved` and the change words. When the scene carries the full `regions` block (it usually does not), `regions` is the output of a fixed rule that code ran over the last twelve distinct books. It is not a reading and not a truth about the market; it is a stated rule's answer, and you may disagree with it. The rule: a listed strike holding at least 8 percent of the contracts in reach, or 8 percent of the absolute dealer gamma in reach, is a member; a member stays while it holds 6; members with no other strike of the window between them are one region; a region at one book is the same region at the next when the two share a strike. `by` says which bar it cleared: contracts, gamma, both, or held (under the entry bar now, kept because it was over it earlier). `strikes` and `center` are listed strikes; every number about them lives in the strike table and none is repeated here. `first_seen` is the book the region has been present since without a break; when it equals `first_book` the region was already there when today's record began. `present` is how many of the last `books` books held it. `change` is judged on the region's share of contracts over a strike set every book of the series carries, so the window sliding as price moves does not read as trading: `increased` and `decreased` mean the straight-line fit over the series moved at least one point; `stable` means it did not; `new` means no region overlapped it at the book of your last read; `resolved` lists regions that were there at that book and are not now, with the last book that held them. These words count contracts. They say nothing about price, and a region price has already been through is still a region.

WHAT THE MEASUREMENTS SAY, and the description rule each one sets. Measured on this stock over 28 sessions:
- A strike price had already touched was returned to within the hour LESS often than a plain strike at the same distance, and almost never when a heavier strike sat between price and it. So a touched strike is a place price has been. DESCRIBE IT AS TOUCHED, WITH THE CLOCK, and never as anything price is drawn back to. "1700 holds the most contracts and was last touched at 12:27" is complete. The idea that a passed strike still counts because price could come back was tested and failed; do not carry it.
- A strike price had not yet reached was reached slightly more often than a plain one, which is noise. Say "not yet reached" and nothing more.
- A strike whose share grew over twenty minutes was approached by about three dollars more on average, on a sigma of fifty to seventy dollars. A rising share is a fact about volume, not a force. A falling share did not read as a pullback.
- The book predicts neither price nor volatility. Name where the weight is and which way it moved; never what price will do about it.
- Open interest does not change during the session. The map holds still; price moves under it.

BOTH SIDES, EVERY TIME. Price always has a side above it and a side below it, and each side is either empty within reach or holds a nearest heavy strike. Your reading names that strike on each side, or says the side is empty, and for each says whether it has been touched today. Heavy means the strike ranks in the top three on its side by at least one of the three measures; say which. Naming the crowd above and forgetting the crowd below is half a reading. An empty side is a real reading and often the loudest one; say "nothing listed above within reach", never a number.

INTERVAL CHANGE, IN FIVE WORDS. Every change is described the way a follow-up film is: NEW, INCREASED, DECREASED, STABLE, and UNKNOWN when the earlier book is missing; RESOLVED is for a pile that was there at your last read and is gone. On a cluster the word is checked by the code against a fixed rule over the last twelve books and rewritten when it disagrees, so write what the change cells and the series show. In prose the same words apply to the change cell and to the series, and two rules ride with them. First, THE WINDOW IS ALWAYS NAMED: "since your last read at 12:35", "over the last twelve books", "against the book five books back". A change with no window is a guess. Second, YOU CANNOT SEE GAMMA CHANGE ON THIS BOARD: the scene ships one gamma sign and one gamma share per strike and no earlier value, so never say a gamma share rose or fell. Change is contracts and volume: "1750 added 1,296 calls since your last read", "1700's share of contracts slipped a point".

BETWEEN THE FRAMES. `between_frames` is what happened while you were not called: `missing_minutes` when the record was SHORT of bars for the window (a gap in the data is a gap, never calm — and its absence means there was no gap), the low and high with the minute each was set, the path travelled in sigma, `shares_traded` in the gap against the day's median minute, and implied vol from and to. Its clock is `context.since_last_read`; boxes broken in the gap are the entries of `context.ranges.breaks_today` whose clock falls after `last_read_at`; the books in it are `strikes.change_books_compared`. Nothing is written twice. On the session's first read it says only that there is no earlier frame.

OPEN WITH THE FRAME. `context.since_last_read` carries `last_read_at`, `minutes_since`, `spot_then`, `spot_change_dollars`, `spot_change_sigma`, anything crossed since (`crossed_since_then`, as a level and a direction), and `clusters_then`, the clusters you drew last time. Price now is `price.live_spot`. A change of 0.15 sigma or more is a move: say price then and price now. Under that it is a hold, and you say so in your own words with the two prices from `between_frames.price.low` and `high` and the clock — not in these words, which every reading for a month has copied. A crossing is named as the level, "just through 1650", never "decisively through" (crossings are shallow at the moment you speak, median about two dollars). Nothing crossed is not the same as nothing changed: the change cells and `between_frames` decide whether the board moved, and "unchanged" is only true when every listed strike's change reads within a point and vol held.

THE DAY'S BOXES. `context.ranges` tells the price-range story as boxes, every number measured, no verdict. `opening` is the first half hour's box and whether it broke; `in_force` is the box that stands now; `breaks_today` lists every break with its clock and direction; `prior_sessions` is the range of the last few closed sessions. Say what a box DID, "broke above the opening box at 10:07", and nothing about what follows.

SAY THE ONE THING WORTH SAYING. `read` is three sentences at the outside, sixty words, and it is the only part of your answer most people ever read. Lead with what you would lead with if you had one breath. Usually that is what changed since your last read. Sometimes it is a strike that has been taking volume all afternoon. On a dead board it is that the board is dead, said in one line, and then you stop — a short true answer is finished work, not a thin one.
The standing board on both sides is a real duty and you pay it in `sides`, which is where the screen draws it from. Do not pay it twice. The prose is for the news, and four strikes in a read is already too many.
Every number you say has to be one that APPEARS IN THE SCENE, exactly as it appears; a number you computed is not on the board and the sentence carrying it is deleted rather than corrected. If you want to say a level is far, name the two prices and let the reader see it.

CHECK WHAT YOU SAID LAST TIME, BEFORE YOU SAY ANYTHING NEW. `context.since_last_read.clusters_then` holds the piles you drew last time and `strikes.left_since_reference` names strikes that have since dropped off the board altogether. Look each one up in the table. If a pile has stopped adding, or has gone, SAY SO, and say it first: "the crowd I pointed at around 1650 has left the board", "1750 has taken nothing since 12:31". Un-saying something you said an hour ago is the most useful sentence available to you and it costs you nothing. A reader who watched you name a level and then never heard of it again learns not to trust the next one.
Say what the board DID; never grade your earlier self. "That pile has gone" and "nothing has traded there since 13:26" are observations and they survive. "That pile was never real", "so it was noise", "that was fake" are verdicts about your own reading, and a verdict is deleted before anyone sees it — you will have said nothing at all. The fact is the retraction. It does not need a ruling on top of it.

THE INSTRUMENT. This is SNDK, a single stock, not an index. Its sigma runs 8-10% of the share price. It has weekly expiries, so most days have no expiry at all; the table is built from the nearest one and `clock.front_expiry` says where in the week you are. On expiry day the whole table dies at the close, and gamma shares near price swing with every dollar; say so rather than reading the swing as a crowd.

FIELD NAMES SAY WHAT THEY ARE. `_pp` is percentage points, `_min` is minutes, `_sigma` is a distance in sigma. `price.vwap_minus_live_spot_sigma` is positive when the day's average price sits ABOVE the live price. `price.moved_last_30min_sigma` is positive when price ROSE.

THE KEPT BLOCKS, in a clause each. `clock.minutes_to_close` is session left for a read to resolve in; `scale.one_sigma_dollars` is a normal day's move, the ruler every distance uses; `scale.implied_vol_atm` is the at-the-money implied vol now; `scale.expected_move_today_asym` is the up and down dollars the options price for the rest of the day; `price.vs_prior_close_pct` is today's change; `price.session_high` and `session_low` are the day's extremes from the bars; `history.price_at_level_unseen_earlier_today` says price is somewhere it has not been today.

WHERE EVERY NUMBER CAME FROM. `price` is the live tape. The table comes out of the options book, which is minutes old and often a cached repeat: `data_sources.options_book` carries its age and `is_repeat_of_previous_scan`. Open interest rests on last night's snapshot; `data_sources.open_interest` re-proves that it held still today. `freshness_rules.blocks_dropped_this_scan` names any block deleted for age, and the whole freshness_rules block is absent when nothing was dropped. Every `dist_sigma` divides the price the book was measured at (`price.spot_when_book_was_measured`); to move a distance to the live frame, SUBTRACT `price.live_minus_book_spot_sigma`.

HONESTY RULES, all of them load-bearing:
- Never cite an entry in `frozen_do_not_cite` as the reason for anything new.
- Never narrate open interest or dealer gamma as something happening now. Open interest is stated as of last night's close; a gamma change is stated with the price move that made it.
- Never invent a level. If price is past the last strike on the list, say exactly that.
- Absence is unknown; an empty side is known empty. Never confuse the two.
- A touched strike is described as touched, with its clock. Never as a place price is drawn to.
- "Unchanged" is a measured claim. It is true only when every change cell reads within a point and vol held; nothing crossed is not it.
- THE WORD MAGNET IS A VERDICT AND IT IS NOT YOURS TO GIVE. Say "holds the most contracts", "took the most volume today", "carries the most gamma". Any sentence calling a strike the magnet, or magnetic, is deleted.
- NOTHING STANDING OUT IS THE COMMON ANSWER AND IT IS COMPLETE WORK. Returning quiet, with the frame and both sides stated, is a correct, finished answer and the one expected most often.

WORDS THAT DELETE YOUR SENTENCE, listed in full so there is no guessing. Any of these in `read` or in a note throws that text away:

  {", ".join(sorted(SR._BANNED_FORECAST))}
  {", ".join(sorted(SR._BANNED_CAUSAL))}

  {", ".join(sorted(SR._BANNED_JUDGEMENT))}

  and in this scene: {", ".join(BANNED_V2)}

Every inflection counts. A second reviewer reads what survives for a forecast hiding in ordinary words. Describe position, not consequence: not "1700 is support" but "1700 holds the most contracts and price is just above it". Not "the crowd is pulling price to 1750" but "1750 took the most volume in the last twenty minutes and price drifted up toward it". Not "gamma is building at 1750" but "1750 added the most contracts since your last read". Not "1700 is the magnet" but "1700 holds the most contracts and was last touched at 12:27".

OUTPUT. Reply with ONLY a JSON object, no prose around it, no code fence. Every key every time; lists may be empty:
{{"quiet": true | false,
 "read": "<sentence one: what changed since the last read, with its window. Sentence two: the standing board on both sides. Fifty words at the outside. Never omitted.>",
 "sides": {{"above": {{"heavy": <the heavy listed strike above the live price, or null>, "leads_on": ["contracts" | "volume" | "gamma", ...]}},
           "below": {{"heavy": <the heavy listed strike below the live price, or null>, "leads_on": [...]}}}},
 "clusters": [{{"strikes": [<listed strikes, adjacent on the list, one or more>],
               "center": <one of those strikes>,
               "rank": <1 is the pile you weigh heaviest>,
               "change": "new" | "increased" | "decreased" | "stable" | "unknown"}}],
 "resolved": [<centers from regions.resolved you choose to mention>],
 "points": [{{"level": <a price that appears in the scene>, "note": "<ten words at most>"}}],
 "absent": ["<anything you looked for and the scene did not carry>"]}}

SIDES is the both-ways rule made checkable: one entry per side, always; heavy must be a listed strike on that side of the live price and in the top three there by some measure, and `leads_on` names only the measures it ranks first on among that side's strikes. The code fills in whether it was touched and when. CLUSTERS are your reading of the piles, at most {MAX_CLUSTERS}, adjacent listed strikes grouped as one, ranked by your own weighing of the three measures; the code appends the pile's side, distance, touch and summed shares, so never add numbers yourself. You may draw a cluster the rule's regions did not, and you may leave a region out. Clusters are for piles that stand out: one or two is normal, zero is common, and a cluster for every heavy strike is a list, not a reading. POINTS are levels a reader should look at now, up to four, with a note of ten words or fewer; on an unchanged board an empty list beside your quiet sentence is usually the better answer. ABSENT goes to the diary, not the screen."""


def prompt_v2(scene: dict) -> str:
    return ("Read this scene cold and reply with the JSON object only.\n\n"
            "SCENE:\n" + json.dumps(scene, default=str))


def call_the_model_v2(prompt: str, model: str = SR.PINNED_MODEL,
                      timeout: float = SR.CALL_TIMEOUT_S):
    """The live reader's call with DOCTRINE_V2 on the system prompt. One
    command line in the codebase, not two."""
    return SR.call_the_model(prompt, model, timeout, doctrine=DOCTRINE_V2)


# ---------------------------------------------------------------------------
# the v2 output guard
# ---------------------------------------------------------------------------
_BANNED_V2_RE = __import__("re").compile(r"\b(" + "|".join(__import__("re").escape(w) for w in BANNED_V2) + r")\b", __import__("re").I)
# only "<strike> above/below": "price is just above 1700" is the doctrine's own
# sentence shape and belongs to the live position guard, not this one
# The side claim, in both word orders and with more than one filler word.
# The original allowed exactly ONE connector, so "1550 sits just above spot"
# — the literal phrasing of the false reading that prompted this audit — walked
# straight past it, as did every reversed form. Fillers are an explicit list
# rather than \w+ so that "1200 and 1300 are above" cannot bind 1200 to the
# wrong verb.
_SIDE_FILL = (r"(?:is|are|was|were|sits|sitting|sat|now|just|still|right|"
              r"currently|the|a|nearest|next|thing|one|strike|level)")
_SIDE_RE = __import__("re").compile(
    r"(\d{3,4}(?:\.\d+)?)\s+(?:" + _SIDE_FILL + r"\s+){0,4}(above|below)\b",
    __import__("re").I)
# ...and the reverse: "above spot, 1550 is the heaviest". The trailing
# lookahead is what makes this safe: in "1300 is above price, 1250 just below"
# the 1250 carries its OWN side word, so it belongs to that clause and not to
# the "above" in front of it. A number with its own side word always wins, and
# without that rule this pattern deletes true readings — which is the worse
# failure, since a deleted reading is silence and a missed one is only a miss.
_SIDE_REV_RE = __import__("re").compile(
    r"\b(above|below)\s+(?:spot|the\s+price|price|here|it)\b[,\s]+"
    r"(?:" + _SIDE_FILL + r"\s+){0,3}(\d{3,4}(?:\.\d+)?)"
    r"(?!\s+(?:" + _SIDE_FILL + r"\s+){0,4}(?:above|below)\b)",
    __import__("re").I)
_UNCHANGED_RE = __import__("re").compile(r"\b(unchanged|nothing (?:has )?changed|no change|the board is the same)\b", __import__("re").I)
_TOUCH_CLOCK_RE = __import__("re").compile(r"(\d{3,4}(?:\.\d+)?)[^.;]{0,60}?\b(?:touch|touched|touching|wick|wicks|tagged|brushed)\b[^.;]{0,40}?\b(\d\d:\d\d)\b", __import__("re").I)
_MOST_RE = __import__("re").compile(r"(\d{3,4}(?:\.\d+)?)[^.;]{0,50}?\b(?:took|added|holds|has|had|leads on|leads|with)\b[^.;]{0,30}?\bthe most (?:added |new )?(volume|contracts|open interest|gamma)\b", __import__("re").I)
POINTS_MAX_V2 = 4
NOTE_CHARS_V2 = 70


# The OTHER way a leadership claim is phrased, and the gap that let a false one
# reach a screen. _MOST_RE requires the literal "the most &lt;measure&gt;"; on
# 2026-09-03 15:18 the model wrote "1550 sits just above spot leading the same
# three measures" while 1600 out-ranked it on all three. The sides gate caught
# it (side_leads_on_unsupported x3) and stripped `heavy_leads_on` from the
# structured block — and the sentence shipped anyway, so the phone drew a claim
# the block beside it contradicted. A reading and its own fields must not
# disagree on the screen.
_LEADS_ALL_RE = __import__("re").compile(
    r"(\d{3,4}(?:\.\d+)?)[^.;]{0,60}?\b(?:leads?|leading)\b[^.;]{0,30}?"
    r"\b(?:all|every|each|the same)\b[^.;]{0,20}?"
    r"\b(?:three|3|those|these|of them|measures?|measure)\b",
    __import__("re").I)

_RANK_COLS = ("rank_by_contracts", "rank_by_volume_today", "rank_by_dealer_gamma")


def _leads_its_side(rec: dict, recs: dict, col: str) -> bool:
    """Rank 1 outright, or rank 1 among the strikes on its own side — the same
    two-step _MOST_RE uses, because the doctrine names a heavy strike per side."""
    if rec.get(col) == 1:
        return True
    side = rec.get("side")
    own = [r.get(col) for r in recs.values()
           if r.get("side") == side and isinstance(r.get(col), int)]
    return side in ("above", "below") and bool(own) and rec.get(col) == min(own)


def _prose_slips_v2(text: str, scene: dict) -> list:
    """The v2-only checks on a sentence: a verdict word the live lists let
    through, a strike placed on the wrong side of the live price, and the
    word unchanged on a board whose change block or vol moved."""
    out = []
    if not text:
        return out
    m = _BANNED_V2_RE.search(text)
    if m:
        out.append(f"banned_v2:{m.group(1).lower()}")
    spot = SR._fin((scene.get("price") or {}).get("live_spot"))
    recs = {r["strike"]: r for r in rows_as_records(scene.get("strikes"))}
    if spot is not None:
        pairs = [(m.group(1), m.group(2)) for m in _SIDE_RE.finditer(text)]
        pairs += [(m.group(2), m.group(1)) for m in _SIDE_REV_RE.finditer(text)]
        for raw_num, raw_word in pairs:
            try:
                num = float(raw_num)
            except (TypeError, ValueError):
                continue
            word = (raw_word or "").lower()
            if num not in recs:
                continue
            if (word == "above" and num < spot) or (word == "below" and num > spot):
                out.append(f"strike_side_contradicts_spot:{num:g}:{word}")
    for m in _TOUCH_CLOCK_RE.finditer(text):
        try:
            k = float(m.group(1))
        except ValueError:
            continue
        if k in recs:
            own = {recs[k].get("first_touch"), recs[k].get("last_touch")}
            if m.group(2) not in own:
                out.append(f"touch_clock_not_that_strikes:{k:g}:{m.group(2)}")
    for m in _LEADS_ALL_RE.finditer(text):
        try:
            k = float(m.group(1))
        except ValueError:
            continue
        rec = recs.get(k)
        if rec is None:
            continue
        short = [c for c in _RANK_COLS if not _leads_its_side(rec, recs, c)]
        if short:
            out.append("leads_all_unsupported:%g:%s" % (k, ",".join(
                c.replace("rank_by_", "").replace("_today", "") for c in short)))
    for m in _MOST_RE.finditer(text):
        try:
            k = float(m.group(1))
        except ValueError:
            continue
        what = m.group(2).lower()
        if k not in recs:
            continue
        col = {"volume": "rank_by_volume_today", "contracts": "rank_by_contracts",
               "open interest": "rank_by_contracts", "gamma": "rank_by_dealer_gamma"}[what]
        leads_today = recs[k].get(col) == 1
        if not leads_today:
            # the doctrine names a heavy strike per side, so "above, 1600 holds
            # the most contracts" is a claim about the above side and is true
            # when 1600 out-ranks every other strike on that side
            side = recs[k].get("side")
            own = [r.get(col) for r in recs.values()
                   if r.get("side") == side and isinstance(r.get(col), int)]
            leads_today = side in ("above", "below") and bool(own) and recs[k].get(col) == min(own)
        added = [(r.get("vol_added_in_series") or 0, kk) for kk, r in recs.items()]
        leads_series = bool(added) and max(added)[1] == k and what == "volume"
        chg = [((c[1] or 0) + (c[2] or 0) if isinstance(c := r.get("change"), list) and len(c) == 3 else 0, kk) for kk, r in recs.items()]
        leads_gap = bool(chg) and max(chg)[1] == k and what == "volume"
        if not (leads_today or leads_series or leads_gap):
            out.append(f"most_{what.replace(' ', '_')}_unsupported:{k:g}")
    if _UNCHANGED_RE.search(text):
        moved = False
        for r in recs.values():
            ch = r.get("change")
            if isinstance(ch, list) and ch and isinstance(ch[0], (int, float)) and abs(ch[0]) >= 1.0:
                moved = True
        iv = (scene.get("between_frames") or {}).get("implied_vol") or {}
        if isinstance(iv.get("from"), (int, float)) and isinstance(iv.get("to"), (int, float)) and abs(iv["to"] - iv["from"]) >= 0.05:
            moved = True
        if moved:
            out.append("unchanged_contradicted_by_change_block")
    return out


def _guard_scene(scene: dict) -> dict:
    """The live guard finds nameable prices under `magnet.top_strikes`; the v2
    scene has no magnet, so hand it a shim carrying the listed strikes and the
    gap's low and high. The shim is for the guard only and never reaches the
    model."""
    shim = dict(scene)
    prices = [r["strike"] for r in rows_as_records(scene.get("strikes"))]
    bp = (scene.get("between_frames") or {}).get("price") or {}
    for k in ("low", "high"):
        v = SR._fin(bp.get(k))
        if v is not None:
            prices.append(v)
    shim["magnet"] = {"top_strikes": [{"strike": k} for k in prices]}
    return shim


def _adjacent_on_list(ks: list, listed: list) -> bool:
    idx = {k: i for i, k in enumerate(sorted(listed))}
    order = sorted(idx[k] for k in ks)
    return all(b - a == 1 for a, b in zip(order, order[1:]))


def check_reading_v2(obj: dict, scene: dict, regions: Optional[dict] = None) -> dict:
    """The live word / number / position gates on `read` and `points`, plus
    the cluster gate: strikes must be listed and adjacent on the list, the
    center one of them, ranks unique; `change` is set from the rule's region
    when the cluster sits on one and `unknown` otherwise, with the model's own
    word kept beside it when they differ; `resolved` must name resolved
    regions. Accepted clusters gain the code's own facts: side, distance,
    touched, and the summed shares, so a multi-strike pile has a stated weight
    without the model adding."""
    base = {k: v for k, v in obj.items() if k in ("quiet", "read", "points")}
    # up to four points on the board scene, the live reader's cap is three
    _obs_max = SR._OBS_MAX
    SR._OBS_MAX = POINTS_MAX_V2
    try:
        reading = SR.check_reading_against_scene(base, _guard_scene(scene))
    finally:
        SR._OBS_MAX = _obs_max
    words = len((reading.get("read") or "").split())
    if words > READ_WORDS_BUDGET:
        reading["notes"] = [f"read_over_budget:{words}_words"]   # measured, never a drop
    v2_slips = _prose_slips_v2(reading.get("read") or "", scene)
    if v2_slips:
        reading["read"] = None
        reading.setdefault("dropped_observations", [])
        reading["dropped_observations"] = list(reading["dropped_observations"]) + [f"read_{x}" for x in v2_slips]
        if not reading.get("points"):
            reading["quiet"] = True
            reading["abstain"] = "forced"
    kept_points = []
    for p in (reading.get("points") or []):
        slips = _prose_slips_v2(str(p.get("note") or ""), scene)
        if slips:
            reading.setdefault("dropped_observations", [])
            reading["dropped_observations"] = list(reading["dropped_observations"]) + [f"point_{x}" for x in slips]
            continue
        if isinstance(p.get("note"), str) and len(p["note"]) > NOTE_CHARS_V2:
            p["note"] = p["note"][:NOTE_CHARS_V2].rstrip()
        kept_points.append(p)
    reading["points"] = kept_points
    recs = {r["strike"]: r for r in rows_as_records(scene.get("strikes"))}
    listed = sorted(recs)
    # Two SOURCES, not one, and collapsing them was a live regression (4dab972):
    # the scene now carries a resolved-only view of the rule (SHIP_RESOLVED),
    # while the rule's full output — the part holding `regions` — still arrives
    # only as the argument. `scene.get("regions") or regions` therefore stopped
    # falling through the moment `resolved` began shipping, because
    # {"resolved": [...]} is truthy and has no "regions" key. Measured effect:
    # on 26% of wakes rule_regions went empty, which forces every cluster's
    # change word to "unknown" and on_rule_region to False. Read each from
    # where it actually lives.
    rule = regions or {}
    scene_rg = scene.get("regions") or {}
    rule_regions = rule.get("regions") or scene_rg.get("regions") or []
    resolved_ok = {SR._fin(r.get("center"))
                   for r in (rule.get("resolved") or scene_rg.get("resolved") or [])
                   if isinstance(r, dict)}
    dropped = list(reading.get("dropped_observations") or [])
    clusters, ranks_seen = [], set()
    for c in (obj.get("clusters") or [])[:MAX_CLUSTERS * 2]:
        if not isinstance(c, dict):
            continue
        ks = [SR._fin(k) for k in (c.get("strikes") or []) if SR._fin(k) is not None]
        if not ks or any(k not in recs for k in ks):
            dropped.append(f"cluster_names_unlisted_strike:{c.get('strikes')}")
            continue
        if not _adjacent_on_list(ks, listed):
            dropped.append(f"cluster_not_adjacent_on_list:{ks}")
            continue
        center = SR._fin(c.get("center"))
        if center is None or center not in ks:
            dropped.append(f"cluster_center_not_in_its_strikes:{c.get('center')}")
            continue
        rank = c.get("rank")
        if not isinstance(rank, int) or rank < 1 or rank in ranks_seen:
            dropped.append(f"cluster_rank_invalid:{rank}")
            continue
        ranks_seen.add(rank)
        on_region = next((r for r in rule_regions if set(SR._fin(k) for k in r.get("strikes") or []) & set(ks)), None)
        model_word = c.get("change") if c.get("change") in CHANGE_WORDS else "unknown"
        word = (on_region.get("change") or "unknown") if on_region else "unknown"
        rec = recs[center]
        out = {"strikes": sorted(ks), "center": center, "rank": rank, "change": word,
               "side": rec.get("side"), "dist_sigma": rec.get("dist_sigma"),
               "touched_today": rec.get("touched_today"),
               "on_rule_region": on_region is not None}
        if model_word != word:
            out["change_model"] = model_word
        cs = [recs[k].get("contracts_share_pp") for k in ks if recs[k].get("contracts_share_pp") is not None]
        gs = [recs[k].get("dealer_gamma_share_pp") for k in ks if recs[k].get("dealer_gamma_share_pp") is not None]
        if cs:
            out["contracts_share_pp_sum"] = round(sum(cs), 2)
        if gs:
            out["dealer_gamma_share_pp_sum"] = round(sum(gs), 2)
        clusters.append(out)
        if len(clusters) >= MAX_CLUSTERS:
            break
    clusters.sort(key=lambda c: c["rank"])
    for i, c in enumerate(clusters, 1):
        if c["rank"] != i:
            dropped.append(f"cluster_rank_renumbered:{c['rank']}->{i}")
            c["rank"] = i
    reading["clusters"] = clusters
    # sides: one entry each way, always; heavy must be a listed strike on that
    # side of the live price and in the top three there on some rank
    pr = scene.get("price") or {}
    spot = SR._fin(pr.get("live_spot"))
    if spot is None:
        spot = SR._fin(pr.get("spot_when_book_was_measured"))
    sides = {}
    model_sides = obj.get("sides") if isinstance(obj.get("sides"), dict) else {}
    if spot is None:
        sides = {"unavailable": "no_spot"}
    for name, keep in (() if spot is None else (("above", lambda k: k > spot), ("below", lambda k: k < spot))):
        on_side = sorted(k for k in recs if keep(k))
        entry = {"empty": not on_side,
                 "nearest": ((min(on_side) if name == "above" else max(on_side)) if on_side else None)}
        ms = model_sides.get(name) if isinstance(model_sides.get(name), dict) else {}
        heavy = SR._fin(ms.get("heavy"))
        leads = [x for x in (ms.get("leads_on") or []) if x in ("contracts", "volume", "gamma")]
        if heavy is not None and heavy in on_side:
            rank_keys = {"contracts": "rank_by_contracts", "volume": "rank_by_volume_today", "gamma": "rank_by_dealer_gamma"}
            def side_rank(k, col):
                vals = [(recs[j].get(col), j) for j in on_side if recs[j].get(col) is not None]
                order = sorted(vals)
                return next((i + 1 for i, (_, j) in enumerate(order) if j == k), None)
            top3 = any((side_rank(heavy, col) or 99) <= 3 for col in rank_keys.values())
            if top3:
                entry["heavy"] = heavy
                entry["heavy_leads_on"] = [m for m in leads if side_rank(heavy, rank_keys[m]) == 1]
                for m in leads:
                    if m not in entry["heavy_leads_on"]:
                        dropped.append(f"side_leads_on_unsupported:{name}:{m}")
                entry["heavy_touched_today"] = recs[heavy].get("touched_today")
                entry["heavy_last_touch"] = recs[heavy].get("last_touch")
            else:
                dropped.append(f"side_heavy_not_heavy:{name}:{heavy:g}")
        elif heavy is not None:
            dropped.append(f"side_heavy_not_on_side:{name}:{heavy:g}")
        sides[name] = entry
    reading["sides"] = sides
    res = []
    for v in (obj.get("resolved") or []):
        fv = SR._fin(v)
        if fv is not None and fv in resolved_ok:
            res.append(fv)
        else:
            dropped.append(f"resolved_not_a_resolved_region:{v}")
    reading["resolved"] = res
    reading["absent"] = [str(a)[:120] for a in (obj.get("absent") or [])][:8]
    # quiet is the model's own flag; the live rule overwrote it with "no points"
    # and filed a reply with a cluster as quiet (eval, Sep 5)
    forced = reading.get("abstain") == "forced"
    if isinstance(obj.get("quiet"), bool) and not forced:
        reading["quiet"] = obj["quiet"]
        reading["abstain"] = "chosen" if obj["quiet"] else None
    elif not forced:
        reading["quiet"] = not (reading.get("points") or clusters)
        reading["abstain"] = "chosen" if reading["quiet"] else None
    reading = {k: v for k, v in reading.items() if v is not None}
    if dropped:
        reading["dropped_observations"] = dropped[:12]
    return reading


# ---------------------------------------------------------------------------
# replay over a recorded day
# ---------------------------------------------------------------------------
def _day_rows(day: str) -> list:
    return [r for r in SR._read_jsonl(SR._diary_dir() / f"{day}.jsonl")
            if r.get("ticker") == "SNDK" and not (r.get("meta") or {}).get("forced")]


def replay_day(day: str, at: Optional[set] = None, call_model: bool = False,
               model: str = SR.PINNED_MODEL) -> list:
    """Walk the recorded day with the LIVE gate; at each wake build both
    scenes with the same since-last-read frame. `at` restricts the model calls
    to those HH:MM wakes (the first wake at or after each). Returns one record
    per wake, with the outcome 60 minutes on."""
    rows = _day_rows(day)
    if not rows:
        return []
    bars = SR.minute_bars(day)
    out = []
    last_call = None
    last_read_ts = None
    clusters_then = None
    pending = set(at or [])
    call_every_wake = call_model and not pending
    for i, row in enumerate(rows):
        now = SR._ts(row)
        if now is None:
            continue
        rows_i = rows[:i + 1]
        row = SR.with_path(row, rows_i)
        wake = SR.should_wake(row, rows[i - 1] if i else None, last_call, now, rows_i)
        if not wake:
            continue
        frame = SR.frame_since_last_read(row, rows_i, last_call, wake, False, now,
                                         prior_rows_today=i > 0)
        v2, v1 = build_scene_v2(row, rows_i, now, frame, last_read_ts, bars, clusters_then=clusters_then)
        cmp_ = compare_scenes(v1, v2)
        lg = legacy(row, rows_i, now, v1=v1)
        hhmm = now.strftime("%H:%M")
        recs = rows_as_records(v2.get("strikes"))
        rec = {"day": day, "at": hhmm, "wake": wake, "spot": row.get("spot"),
               "sigma": row.get("sigma"),
               "chars_v1": cmp_["chars_v1"], "chars_v2": cmp_["chars_v2"],
               "removed": cmp_["removed"], "added": cmp_["added"],
               "code_magnet": lg["magnet"],
               "v2_top_by_contracts": (recs[0]["strike"] if recs else None),
               "v2_listed": len(recs),
               "v2_window": (v2.get("strikes") or {}).get("strikes_in_window"),
               "rule_regions": (v1.get("regions_rule") or {}).get("regions")}
        fut = [r for r in rows[i + 1:] if (t := SR._ts(r)) is not None and t <= now + timedelta(minutes=60)]
        if fut:
            rec["spot_60m"] = fut[-1].get("spot")
            rec["low_60m"] = min(SR._fin(r.get("spot")) for r in fut if SR._fin(r.get("spot")) is not None)
            rec["high_60m"] = max(SR._fin(r.get("spot")) for r in fut if SR._fin(r.get("spot")) is not None)
        want = call_every_wake or any(hhmm >= a for a in list(pending))
        if want:
            for a in [a for a in pending if hhmm >= a]:
                pending.discard(a)
            p1 = ("Read this scene cold and reply with the JSON object only.\n\nSCENE:\n"
                  + json.dumps(v1, default=str))
            o1, e1, w1, raw1 = SR.call_the_model(p1, model)
            o2, e2, w2, raw2 = call_the_model_v2(prompt_v2(v2), model)
            rec["v1_reply"] = (SR.check_reading_against_scene(o1, v1) if isinstance(o1, dict) else None)
            rec["v1_error"] = e1 or (None if isinstance(o1, dict) else "unparseable")
            rec["v1_wall_s"] = w1
            rec["v2_obj"] = o2 if isinstance(o2, dict) else None
            rec["v2_reply"] = (check_reading_v2(o2, v2, regions=v1.get("regions_rule")) if isinstance(o2, dict) else None)
            rec["v2_error"] = e2 or (None if isinstance(o2, dict) else "unparseable")
            rec["v2_wall_s"] = w2
            rec["v2_raw"] = raw2
            rec["scene_v1"] = v1
            rec["scene_v2"] = v2
            if rec["v2_reply"]:
                clusters_then = rec["v2_reply"].get("clusters") or None
        out.append(rec)
        last_call = {"ts": row["ts"], "spot": row.get("spot"),
                     "magnet_band": SR.magnet_band(row),
                     "gate": SR.state_for_next_wake(row, v1)}
        last_read_ts = now
    return out


def _main(argv: list) -> int:
    if "--replay" in argv:
        days = [a for a in argv[argv.index("--replay") + 1:] if not a.startswith("--")]
        for d in days:
            recs = replay_day(d)
            if not recs:
                print(f"{d}: no rows")
                continue
            c1 = sorted(r["chars_v1"] for r in recs)
            c2 = sorted(r["chars_v2"] for r in recs)
            print(f"{d}: {len(recs)} wakes; chars v1 median {c1[len(c1)//2]}, v2 median {c2[len(c2)//2]}")
        return 0
    if "--eval" in argv:
        i = argv.index("--eval")
        day = argv[i + 1]
        at = {a for a in argv[i + 2:] if ":" in a}
        out_path = argv[argv.index("--out") + 1] if "--out" in argv else None
        recs = replay_day(day, at=at, call_model=True)
        if out_path:
            with open(out_path, "a") as f:
                for r in recs:
                    if "v2_reply" in r:
                        f.write(json.dumps(r, default=str) + "\n")
        for r in recs:
            if "v2_reply" in r:
                print(json.dumps({k: r[k] for k in ("day", "at", "wake", "spot", "code_magnet",
                                                    "v1_error", "v2_error")}, default=str))
        return 0
    print("usage: sndk_board.py --replay DAY [DAY...] | --eval DAY [HH:MM ...] [--out FILE]")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
