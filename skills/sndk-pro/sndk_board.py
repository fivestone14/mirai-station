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
# The table ships as one object per strike, keys on every row. A columns-once
# layout with one array per strike was written beside it and never used: it
# halves the characters and measured THREE TIMES the model's wall time — 48 s
# against 15 s median on the 2026-09-05 eval. The constant that chose between
# them was removed on 2026-09-13, having only ever held one value; this comment
# is what is worth keeping from it.
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
READ_WORDS_BUDGET = 40      # strikes-7 asks for forty; measured, never a drop
                                # (forty was a dead letter: 47 to 62 words on every eval read)
GAP_FACTOR = 2.0                # an interval longer than this times the day's median cadence is a gap
MAX_CLUSTERS = 4
CHANGE_WORDS = sndk_regions.CHANGE_WORDS
# `history` came off this list on 2026-09-13: the only flag it could still
# carry is `tape_abnormal_vs_own_history`, which this builder popped anyway, so
# the block reached the reading model empty on 880 of 880 rebuilt boards and
# then got dropped. It is NOT dead in the legacy scene the voice reads — that
# one carries it on 264 of 2,029 scans — so only the board loses it.
KEPT_BLOCKS = ("data_sources", "clock", "scale", "price",
               "freshness_rules", "context")
# verdict words the live lists let through and this scene forbids; the live
# reader keeps "magnet" because its scene has a block by that name
BANNED_V2 = ("magnet", "magnets", "magnetic", "momentum", "building toward", "building towards",
             "stronger", "strongest", "weaker", "weakest", "wall", "walls", "flip",
             # 2026-09-10: a crossing count invites a grade on top of it
             "rejected", "rejection", "rejecting", "reclaimed", "reclaiming", "reclaim",
             "contested")

_ET = SR._ET

COLUMNS_BASE = ["strike", "side", "dist_sigma"]
COLUMNS_TOUCH = ["first_touch", "last_touch", "minutes_touched_today",
                 "visits_today", "passed_through_today"]
COLUMNS_BOOK = ["oi_calls", "oi_puts", "vol_calls", "vol_puts", "contracts_share_pp"]
COLUMNS_GAMMA = ["dealer_gamma_sign", "dealer_gamma_share_pp"]
COLUMNS_RANK_C = ["rank_by_contracts"]
COLUMNS_RANK_V = ["rank_by_volume_today"]
COLUMNS_RANK_G = ["rank_by_dealer_gamma"]
COLUMNS_TAIL = ["on_list_for_min", "change", "vol_added_per_book", "vol_added_in_series"]
LEAD_MEASURES = ("contracts", "volume", "gamma")   # the three ranks, in the reply's own words
LEAD_HOLD_BOOKS = 2             # a lead is stated in `day.leaders` once it has held this many
                                # consecutive distinct books. Measured over 13
                                # sessions on 2026-09-15: a new volume or gamma
                                # leader is gone again one book later about one
                                # time in five (21% and 22%; contracts 6%), and
                                # two books in the next book keeps it 76-79%.
                                # The cost is one book, about four minutes.
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
        # review item #8: a book whose volume was withheld says why
        absent.append(((row.get("meta") or {}).get("volume_withheld"))
                      or "volume by side per strike; contracts counted from open interest only")
    if not net:
        absent.append("signed dealer gamma per strike")
    oi_next = _triples(gv.get("oi_side_by_strike_next"))
    vol_next = _triples(gv.get("vol_side_by_strike_next"))
    # review item #8, second pass: next week's volume withheld on its own
    # evidence says so, the same way the front book's does
    if not vol_next and (nw := (row.get("meta") or {}).get("volume_next_withheld")):
        absent.append(nw)
    return {"contracts": contracts, "net": net, "oi_side": oi_side, "vol_side": vol_side,
            "oi_next": oi_next, "vol_next": vol_next,
            "next_dte": (int(gv["next_dte"]) if SR._fin(gv.get("next_dte")) is not None else None),
            "next_recorded": ("oi_side_by_strike_next" in gv or "vol_side_by_strike_next" in gv),
            "absent": absent}


def _tau_to_zero(row: dict) -> bool:
    """True when the front book is expiring TODAY and the session is in the
    window where its solve reprices on the clock rather than on the market.

    On the weekly's own expiry day the at-the-money solve balloons into the
    bell on pure time-to-expiry mechanics. Measured on the three recorded
    expiry Fridays, every scan whose ruler ran past 1.5x the day's own value
    sits in this window and nowhere else: 08-28 from 15:46 (seven scans, up to
    102.9 against a 46.6 anchor), 09-04 at 15:56 (201.9 against 55.3, a factor
    of 3.65) and 09-11 at 15:54 (95.8 against 54.1). `vol_trend` already
    refuses to read a vol move under exactly this condition; the payload was
    still shipping the ballooned numbers as the day's ruler and as the vol
    "now"."""
    rr = row.get("range_ruler") if isinstance(row.get("range_ruler"), dict) else {}
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    return gv.get("front_dte") == 0 and rr.get("quality") == "late_day"


def _ruler(row: dict) -> tuple:
    """(ruler_spot, sigma, live_spot, ruler_name) — the same rule build_scene
    uses: divide the BOOK's spot when the chain saw one within sanity, else
    the live spot and say so."""
    spot = SR._fin(row.get("spot"))
    sig = SR._fin(row.get("sigma")) or 0.0
    # into the bell on expiry day the ruler's live leg is a clock artifact, so
    # the day's own anchor is the honest day-scale number — and every distance
    # on the table divides by this, so a ballooned ruler makes the whole board
    # read close to price when it is not.
    if _tau_to_zero(row):
        anchor = SR._fin(row.get("sigma_anchor"))
        if anchor and anchor < sig:
            sig = anchor
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
    # a strike with no trades has no volume rank: on a zero-volume open every
    # strike tied at nothing and the lowest one was ranked the leader (item #8)
    by_v = rank(lambda k: _volume_today(surf, k) or 0.0, lambda k: (_volume_today(surf, k) or 0.0) > 0) if surf["vol_side"] else {}
    by_g = rank(lambda k: abs(surf["net"].get(k, 0.0)), lambda k: k in surf["net"]) if surf["net"] else {}
    return {k: (by_c.get(k), by_v.get(k), by_g.get(k)) for k in window}


def select_strikes(surf: dict, window: list, ruler_spot: float,
                   crossed: Optional[list] = None,
                   mode: str = STRIKE_LIST_MODE, top_n: int = TOP_N) -> list:
    """The union the design names: top N by contracts, top N by today's
    volume, top N by |dealer gamma|, the nearest NEAREST_EACH_SIDE strikes
    either side of `ruler_spot`, and anything crossed since the last read. A
    measure that was not recorded nominates nobody. The table passes the LIVE
    price here (item #9), so the strikes nearest where price is now are the
    ones listed; the regions rule passes each book's own price."""
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


def _bar_side(b: dict, k: float) -> int:
    """Which side of k a minute that did NOT touch it sat on: +1 above, -1 below."""
    lo = SR._fin(b.get("low"))
    return 1 if lo is not None and lo > k else -1


def _visits(bars_now: list, k: float) -> tuple:
    """(visits, passed_through) at k over the completed minute bars.

    review item #10: a VISIT is a run of minutes whose low-to-high range held
    the strike, and it ends when a whole minute passes without touching it. A
    visit PASSED THROUGH when price came in from one side and left on the
    other — the minute before it and the minute after it sit on opposite sides.
    The day's first visit enters from the side the day opened on. A visit still
    going on has not left yet, so it counts as a visit and not as passed
    through, and passed_through can never exceed visits."""
    visits = through = 0
    entry = None        # the side the current visit came in from, None outside one
    prev = None         # the side of the last minute that did not touch k
    inside = False
    for b in bars_now:
        if _wick_includes(b, k):
            if not inside:
                visits += 1
                inside = True
                if prev is not None:
                    entry = prev
                else:
                    o = SR._fin(b.get("open"))
                    entry = (1 if o > k else -1 if o < k else None) if o is not None else None
        else:
            side = _bar_side(b, k)
            if inside and entry is not None and side == -entry:
                through += 1
            inside = False
            prev = side
    return visits, through


def touch_facts(bars_now: list, k: float) -> dict:
    """What price did at the strike today, from the completed minute bars:
    whether a minute's range held it, the first and last minute that did, how
    many minutes did, in how many separate visits, and how many of those went
    through it. Every time is the bar's own timestamp. The minutes are minutes
    and not times: 43 of them were 10 separate visits on 09-08 (item #10).

    review item #28 (2026-09-13): `shares_traded_at_strike_pp` used to ride
    here — the share of the day's stock volume printed in minutes whose range
    held the strike. A minute's whole volume was credited to every strike its
    range covered, so the listed values summed past 100 on 58 percent of
    boards, median 108 and once 400. It was cut rather than repaired: any
    repair would have had to invent a split of a minute's volume across the
    strikes its range covered, and the number nothing else on the board could
    check."""
    hits = [b for b in bars_now if _wick_includes(b, k)]
    visits, through = _visits(bars_now, k)
    return {"first_touch": _hhmm(_bar_ts(hits[0])) if hits else None,
            "last_touch": _hhmm(_bar_ts(hits[-1])) if hits else None,
            "minutes_touched_today": len(hits),
            "visits_today": visits,
            "passed_through_today": through}


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
    # review item #8: a change measured from or to a book still carrying the
    # prior session's volume would be a change against yesterday
    if _withheld(books[-1]):
        return None, {"unavailable": "this_book_carries_prior_session_volume"}
    if last_read_ts is not None:
        before = [b for b in books if (t := _asof(b)) is not None and t <= last_read_ts]
        if before:
            ref = before[-1]
            n = len([b for b in books if (t := _asof(b)) is not None and t > _asof(ref)])
            if n == 0:
                # the book has not refreshed since the last read: comparing it with
                # itself would ship a change of zero on every strike and read as calm
                return None, {"unavailable": "no_new_book_since_last_read"}
            if _withheld(ref):
                return None, {"unavailable": "earlier_book_carried_prior_session_volume"}
            return ref, {"basis": "last_read", "books_compared": n}
    if len(books) > CHANGE_BOOKS_FALLBACK:
        ref = books[-1 - CHANGE_BOOKS_FALLBACK]
        if _withheld(ref):
            clean = [b for b in books[-1 - CHANGE_BOOKS_FALLBACK:-1] if not _withheld(b)]
            if not clean:
                return None, {"unavailable": "earlier_book_carried_prior_session_volume"}
            ref = clean[0]
            k = len(books) - 1 - books.index(ref)
            return ref, {"basis": f"{k}_books", "books_compared": k}
        return ref, {"basis": f"{CHANGE_BOOKS_FALLBACK}_books", "books_compared": CHANGE_BOOKS_FALLBACK}
    return None, None


def volume_leader(row: dict, carried: Optional[dict] = None) -> Optional[float]:
    """The strike ranking 1 on today's volume in this row's window, ranked the
    way the table ranks it (strikes-5, for the wake gate). None when the row
    measured no volume, ranks nobody (a zero-volume open), or is one of the
    books `carried_books` withholds because its volume is the prior session's;
    `carried` is that dict, computed once by the caller."""
    if carried and (row.get("meta") or {}).get("book_asof") in carried:
        return None
    surf = surfaces(row)
    if not surf["vol_side"]:
        return None
    rs, sg, _, _ = _ruler(row)
    win = _window(surf, rs, sg)
    if not win:
        return None
    return next((k for k, v in _ranks(surf, win).items() if v[1] == 1), None)


# ---------------------------------------------------------------------------
# strikes-6 (2026-09-15): THE DAY BLOCK
# ---------------------------------------------------------------------------
# Everything else on the board is the book now and the gap since the last read.
# A review of the recorded readings found the model framing 77 percent of them
# against its previous read and never once against the session, and three
# holes behind that: nothing said who had led each measure through the day
# (`leads_since` gave only the lead standing now and the one before), nothing
# told it whether what it said an hour ago still held (on 09-11 at 13:56 1650
# overtook 1700 on contracts, and at 14:00 the reading said "1650 still" and
# dropped its 13:11 claim about 1700 without a word), and a strike it had named
# vanished from the table when the price window slid past it — three in four of
# them are gone from the stored book too, so "tracked all day" can only ever be
# honest about the ones it still holds. `day` is one block written by code from
# every distinct book and every reading of the session.
CLAIM_CALLS = 3               # the readings whose claims are graded. Measured over
                              # 84 recorded calls: 7 graded claims a call at the
                              # median (548 characters), 9 and 716 at five
CLAIM_SHARE_PP = 1.0          # a pile's summed contracts share moving this far is a change
NAMED_OFF_LIST_MAX = 6        # strikes named today and off the table now, nearest first
LEAD_RANK_COL = {"contracts": "rank_by_contracts", "volume": "rank_by_volume_today",
                 "gamma": "rank_by_dealer_gamma"}


def day_timeline(rows: list) -> list:
    """One entry per distinct book of the day, oldest first: its time, the
    strike ranking 1 on each measure (None where the book measured nothing on
    it: volume withheld as the prior session's, item #8, or a surface not
    recorded), the strikes the table's own rule would list at that book, and
    whether its volume was withheld. `rows` must already carry the builder's
    withholding, so every entry ranks the board the table ranks."""
    tl = []
    for r in SR._distinct_books_rows(rows):
        asof = _asof(r)
        if asof is None:
            continue
        surf = surfaces(r)
        rs, sg, lv, _ = _ruler(r)
        win = _window(surf, rs, sg)
        ranks = _ranks(surf, win) if win else {}
        tl.append({"t": asof,
                   "lead": tuple(next((k for k, v in ranks.items() if v[i] == 1), None)
                                 for i in range(len(LEAD_MEASURES))),
                   "listed": (set(select_strikes(surf, win, lv if lv is not None else rs)) if win else set()),
                   "withheld": _withheld(r)})
    return tl


def book_gaps(tl: list) -> set:
    """Indices of the books that came after a hole: the interval before them ran
    longer than GAP_FACTOR times the day's median book interval and more than
    eight minutes — the rule `frames_block` applies to its series. On 09-09 the
    books stop at 09:43 and resume at 15:48; on 09-08 there is none from 10:20
    to 11:25, and on 09-15 none from 11:35 to 13:23."""
    ts = [x["t"] for x in tl]
    iv = [(b - a).total_seconds() / 60.0 for a, b in zip(ts, ts[1:])]
    if not iv:
        return set()
    med = statistics.median(iv)
    return {j + 1 for j, m in enumerate(iv) if m > GAP_FACTOR * med and m > 8}


def leader_runs(tl: list, gaps: set) -> dict:
    """{measure: [run, ...]}, oldest first; a run is {strike, from, until,
    books, j0}. A run breaks when the leader changes, at a book that ranks
    nobody on that measure, and at a hole in the books, so a lead is never
    carried across time nobody measured. strikes-5 did not break at holes and
    on 19 of 84 recorded calls dated a lead across one ("1800 since 09:30" on
    09-09, with no book between 09:43 and 15:48)."""
    out = {}
    for i, name in enumerate(LEAD_MEASURES):
        runs, cur = [], None
        for j, x in enumerate(tl):
            k = x["lead"][i]
            if cur is not None and (k != cur["strike"] or j in gaps):
                runs.append(cur)
                cur = None
            if k is None:
                continue
            if cur is None:
                cur = {"strike": k, "from": x["t"], "until": x["t"], "books": 1, "j0": j}
            else:
                cur["until"] = x["t"]
                cur["books"] += 1
        if cur is not None:
            runs.append(cur)
        out[name] = runs
    return out


def _leaders_block(tl: list, gaps: set) -> dict:
    """Per measure, every lead that held LEAD_HOLD_BOOKS books or more, as
    [strike, from, until]; `until` is None for the lead standing at the last
    book. A lead broken only by one-book flickers of another strike is one lead
    (a new volume or gamma leader is gone again a book later about one time in
    five); a lead broken by a book that measured nothing on the measure, or by
    a hole, stays two."""
    runs = leader_runs(tl, gaps)
    last_t = tl[-1]["t"] if tl else None
    out = {}
    for i, name in enumerate(LEAD_MEASURES):
        merged = []
        for r in (x for x in runs.get(name, []) if x["books"] >= LEAD_HOLD_BOOKS):
            end = r["j0"] + r["books"] - 1
            if (merged and merged[-1]["strike"] == r["strike"]
                    and all(tl[x]["lead"][i] is not None for x in range(merged[-1]["j_end"] + 1, r["j0"]))
                    and not any(merged[-1]["j_end"] < g <= r["j0"] for g in gaps)):
                merged[-1]["until"], merged[-1]["j_end"] = r["until"], end
            else:
                merged.append({**r, "j_end": end})
        if merged:
            out[name] = [[_k(r["strike"]), _hhmm(r["from"]), (None if r["until"] == last_t else _hhmm(r["until"]))]
                         for r in merged]
    return out


def _lists_block(tl: list, gaps: set, table_now: set) -> dict:
    """What stood, what joined and what left, from the first book whose volume
    is today's (or the first book after the last hole) to the table as it is
    now. The table's own list is the one on the right-hand side: lists rebuilt
    book by book differ from it on 27 of 84 recorded calls (the crossed-strike
    rule, a re-priced window), and "now" must be what the model is looking at.
    Nothing is said until the lists span more than LEAD_HOLD_BOOKS books: on
    09-09 one book "stood" seventeen strikes."""
    start = max([0] + list(gaps))
    clean = [j for j in range(start, len(tl)) if not tl[j]["withheld"]]
    if not clean:
        return {}
    seg = tl[clean[0]:]
    out = {"lists_from": _hhmm(seg[0]["t"])}
    if len(seg) <= LEAD_HOLD_BOOKS:
        return out
    past = seg[:-1]
    stood = set.intersection(*[x["listed"] for x in past]) & table_now
    joined = []
    for k in sorted(table_now - stood):
        j = len(seg) - 1
        while j > 0 and k in seg[j - 1]["listed"]:
            j -= 1
        joined.append(_k(k))
    ever = set().union(*[x["listed"] for x in past])
    left = [[_k(k), _hhmm(max(x["t"] for x in past if k in x["listed"]))] for k in sorted(ever - table_now)]
    out["stood"] = [_k(k) for k in sorted(stood)]
    if joined:
        out["joined"] = joined
    if left:
        out["left"] = left
    return out


def _fresh_calls(calls: Optional[list]) -> list:
    """The day's readings a person was shown, oldest first: read rows that spent
    a call, did not error, and wrote their own reading (a failed call's row
    carries the older sentence forward under an older `reading_ts`)."""
    out = []
    for c in calls or []:
        rd = c.get("reading") if isinstance(c, dict) else None
        if not isinstance(rd, dict) or c.get("wall_s") is None or c.get("error"):
            continue
        t, tr = SR._parse_ts(c.get("ts")), SR._parse_ts(c.get("reading_ts"))
        if t is None or tr is None or t != tr:
            continue
        out.append(c)
    return out


def _claims_of(call: dict) -> list:
    """The structured claims a checked reading made: each side's heaviest
    strike with the measures the checker let it lead on, each pile's centre and
    strikes with the summed contracts share the checker appended, each point's
    level with the price when it was said."""
    rd, t = call["reading"], SR._parse_ts(call["ts"])
    out = []
    for side in ("above", "below"):
        s = (rd.get("sides") or {}).get(side)
        k = SR._fin(s.get("heavy")) if isinstance(s, dict) else None
        if k is not None:
            out.append({"type": "side", "key": k, "side": side, "t": t,
                        "leads_on": [m for m in (s.get("heavy_leads_on") or []) if m in LEAD_MEASURES]})
    for c in rd.get("clusters") or []:
        k = SR._fin(c.get("center")) if isinstance(c, dict) else None
        if k is not None:
            out.append({"type": "cluster", "key": k, "t": t,
                        "strikes": [v for v in (SR._fin(x) for x in (c.get("strikes") or [])) if v is not None],
                        "share": SR._fin(c.get("contracts_share_pp_sum"))})
    for p in rd.get("points") or []:
        k = SR._fin(p.get("level")) if isinstance(p, dict) else None
        if k is not None:
            out.append({"type": "point", "key": k, "t": t, "spot_then": SR._fin(call.get("spot"))})
    return out


def _side_rank(recs: dict, k: float, col: str, side: str) -> Optional[int]:
    """The strike's place among the table's strikes on that side by one rank
    column — the checker's own ordering (check_reading_v2's side_rank)."""
    order = sorted((r[col], j) for j, r in recs.items() if r.get("side") == side and r.get(col) is not None)
    return next((i + 1 for i, (_, j) in enumerate(order) if j == k), None)


def _grade_claim(cl: dict, recs: dict, prices: set, spot_now: Optional[float], bars_now: list) -> tuple:
    """(now, what) for one claim against the table as it is now: `holds`,
    `changed` with what changed, `off_list`, or (None, None) when this board
    cannot measure it. Observational words only — the doctrine forbids a
    verdict on its own earlier reading, and "reversed" and "faded" are a price
    call and a strength grade."""
    if cl["type"] == "side":
        r = recs.get(cl["key"])
        if r is None:
            return "off_list", None
        if r.get("side") != cl["side"]:
            return "changed", ("now at price" if r.get("side") == "at" else f"now {r.get('side')} price")
        have = [m for m in LEAD_MEASURES if any(x.get(LEAD_RANK_COL[m]) is not None for x in recs.values())]
        if cl["leads_on"]:
            meas = [m for m in cl["leads_on"] if m in have]
            if not meas:
                return None, None
            lost = [m for m in meas if _side_rank(recs, cl["key"], LEAD_RANK_COL[m], cl["side"]) != 1]
            return ("holds", None) if not lost else ("changed", f"no longer first {cl['side']} on {' and '.join(lost)}")
        top3 = any((_side_rank(recs, cl["key"], LEAD_RANK_COL[m], cl["side"]) or 99) <= 3 for m in have)
        return ("holds", None) if top3 else ("changed", f"no longer among the three heaviest {cl['side']}")
    if cl["type"] == "cluster":
        if cl["key"] not in recs:
            return "off_list", None
        if any(k not in recs for k in cl["strikes"]):
            return "changed", "some of its strikes left the list"
        if cl["share"] is None:
            return "holds", None
        d = sum(recs[k].get("contracts_share_pp") or 0 for k in cl["strikes"]) - cl["share"]
        if abs(d) < CLAIM_SHARE_PP:
            return "holds", None
        return "changed", ("contracts share up" if d > 0 else "contracts share down")
    level = cl["key"]
    if round(level, 2) not in prices:
        return "off_list", None
    st = cl["spot_then"]
    s0 = (1 if st > level else -1 if st < level else 0) if st is not None else 0
    if not s0:
        return None, None
    for b in bars_now or []:
        bt, cc = _bar_ts(b), SR._fin(b.get("close"))
        if bt is None or cc is None or bt <= cl["t"]:
            continue
        if (1 if cc > level else -1 if cc < level else 0) == -s0:
            return "changed", "price crossed it"
    if spot_now is not None and spot_now != level and (spot_now > level) != (s0 > 0):
        return "changed", "price crossed it"
    return "holds", None


def _earlier_claims(calls: list, recs: dict, prices: set, spot_now: Optional[float], bars_now: list) -> list:
    """The last CLAIM_CALLS readings' claims, newest reading first, one entry per
    strike or level (the newest claim on it), each graded against this board. A
    point that still holds is left out: its note cannot be checked, and saying
    "still a price on the board" is noise."""
    seen, groups = set(), {}
    for c in reversed(calls[-CLAIM_CALLS:]):
        at = _hhmm(SR._parse_ts(c["ts"]))
        for cl in _claims_of(c):
            if cl["key"] in seen:
                continue
            now, what = _grade_claim(cl, recs, prices, spot_now, bars_now)
            if now is None or (cl["type"] == "point" and now == "holds"):
                continue
            seen.add(cl["key"])
            if cl["type"] == "side":
                e = {"strike": _k(cl["key"]),
                     "said": f"heaviest {cl['side']}" + (f" on {' and '.join(cl['leads_on'])}" if cl["leads_on"] else "")}
            elif cl["type"] == "cluster":
                e = {"strike": _k(cl["key"]), "said": "pile centre"}
            else:
                e = {"level": _k(cl["key"]), "said": "point"}
            e["now"] = now
            if what:
                e["what"] = what
            groups.setdefault(at, []).append(e)
    return [{"said_at": t, "claims": v} for t, v in groups.items()]


def _named_off_list(calls: list, recs: dict, surf: dict, live: Optional[float], sig: float,
                    bars_now: list) -> list:
    """Strikes a reading named today (a side's heaviest, a pile's strikes) that
    are not on the table now, nearest first, each with the last time it was
    named, where it sits, what the book still holds at it, and when price last
    touched it. Measured over 81 calls, the engine keeps strikes to about 1.5
    sigma of price (never past 1.87), so most strikes that slid out of the
    window are gone from the book as well; those say `in_book: false` rather
    than borrow a number from an earlier book."""
    if live is None or not sig:
        return []
    named = {}
    for c in calls:
        at = _hhmm(SR._parse_ts(c["ts"]))
        for cl in _claims_of(c):
            for k in ([cl["key"]] if cl["type"] == "side" else cl.get("strikes") or [] if cl["type"] == "cluster" else []):
                named[k] = at
    out = []
    for k, at in named.items():
        if k in recs:
            continue
        d = (k - live) / sig
        e = {"strike": _k(k), "named_at": at,
             "side": ("at" if abs(d) < AT_SIGMA else "above" if k > live else "below"),
             "dist_sigma": round(d, 2)}
        if k in surf["oi_side"] or k in surf["vol_side"] or k in surf["contracts"]:
            oc, op = surf["oi_side"].get(k, (None, None))
            vc, vp = surf["vol_side"].get(k, (None, None))
            for name, v in (("oi_calls", oc), ("oi_puts", op), ("vol_calls", vc), ("vol_puts", vp)):
                if v is not None:
                    e[name] = int(v)
        else:
            e["in_book"] = False
        if bars_now:
            lt = touch_facts(bars_now, k).get("last_touch")
            if lt:
                e["last_touch"] = lt
        out.append(e)
    out.sort(key=lambda e: (abs(e["dist_sigma"]), e["strike"]))
    return out[:NAMED_OFF_LIST_MAX]


# strikes-6 (#12): TODAY AGAINST PAST SESSIONS. The board judged today alone:
# open interest as of the prior close with nothing to say what changed
# overnight, and volume with nothing to say whether this is a busy hour. Measured
# over 08-26..09-15: open interest never moved within a session (0 changes in
# about 57,000 book-to-book checks) and was republished every night, and every
# overnight change was smaller than that strike's own volume the day before
# (198 of 199) — a real fact about the previous session's trading. Volume per
# strike against past days was noise (a strike's own five past values vary about
# six times and mostly track where price sits), so volume is compared only
# across the whole window, and only against sessions that were not expiry days:
# expiry Fridays ran at about 2.3 times an ordinary day and dragged every
# baseline with them.
PRIOR_VOLUME_SESSIONS = 5        # recent closed sessions that were not expiry days
PRIOR_VOLUME_MIN_SESSIONS = 3    # fewer usable than this and nothing is said
PRIOR_VOLUME_MAX_AGE_MIN = 15    # a prior book older than this at the clock minute is not that minute
PRIOR_VOLUME_CACHE_V = 1
_CLOSED_ROWS: dict = {}


def _closed_day_rows(day: str) -> list:
    """A closed session's diary rows, read once per process and per version of
    the file on disk: the carried-volume check, the open-interest change and the
    volume baseline all open the same files."""
    p = SR._diary_dir() / f"{day}.jsonl"
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError:
        return []
    if key not in _CLOSED_ROWS:
        if len(_CLOSED_ROWS) > 12:
            _CLOSED_ROWS.clear()
        _CLOSED_ROWS[key] = _day_rows(day)
    return _CLOSED_ROWS[key]


def _prior_oi(day: str, row: dict) -> Optional[dict]:
    """{strike: (calls, puts)} open interest in the previous session's book for
    THIS row's front expiry, or None when the record cannot answer: no recorded
    previous session, a dark weekday between, or a previous book that did not
    carry this expiry (Monday's own next weekly never has one). The previous
    session's last book stands for its whole day, because open interest does
    not move within one."""
    front = _front(row)
    pd = SR._prior_session_date(day)
    if front is None or pd is None or not _is_previous_session(pd, day):
        return None
    prior = _closed_day_rows(pd)
    if not prior:
        return None
    last = prior[-1]
    gv = last.get("gex_views") if isinstance(last.get("gex_views"), dict) else {}
    if _front(last) == front:
        oi = _triples(gv.get("oi_side_by_strike"))
    elif _next_expiry(last) == front:
        oi = _triples(gv.get("oi_side_by_strike_next"))
    else:
        return None
    return oi or None


def _oi_change(prior: dict, k: float, oc, op) -> Optional[list]:
    """[calls, puts] open interest now minus the previous session's, or None when
    either side is missing at either end."""
    if k not in prior:
        return None
    pc, pp = prior[k]
    if None in (oc, op, pc, pp):
        return None
    return [int(oc - pc), int(op - pp)]


def _window_volume(row: dict) -> Optional[float]:
    """Today's volume across the row's whole window, both rights, or None when
    the row measured no volume."""
    surf = surfaces(row)
    if not surf["vol_side"]:
        return None
    rs, sg, _, _ = _ruler(row)
    win = _window(surf, rs, sg)
    if not win:
        return None
    return float(sum(_volume_today(surf, k) or 0.0 for k in win))


def prior_volume_series(today: str) -> dict:
    """{day: {"expiry": bool, "series": [[minute of day, volume in reach], ...]}}
    for the recent closed sessions, back to PRIOR_VOLUME_SESSIONS that were not
    expiry days. Kept in sndk_reads/prior_volume.json and rebuilt only for days
    not already in it: the reader is a fresh process every tick, and those
    diaries are about 9 MB of JSON. A book whose volume was the prior session's
    is left out, judged with the whole closed day in hand."""
    days = sorted(p.stem for p in SR._diary_dir().glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")
                  if p.stem < today)
    cache = SR._reads_dir() / "prior_volume.json"
    try:
        blob = json.loads(cache.read_text())
        have = blob.get("days") if isinstance(blob, dict) and blob.get("v") == PRIOR_VOLUME_CACHE_V else {}
    except (OSError, ValueError):
        have = {}
    have = have if isinstance(have, dict) else {}
    out, built, taken = {}, False, 0
    for d in reversed(days):
        if taken >= PRIOR_VOLUME_SESSIONS:
            break
        e = have.get(d)
        if not (isinstance(e, dict) and isinstance(e.get("series"), list)
                and all(isinstance(x, list) and len(x) == 2 and all(isinstance(v, (int, float)) for v in x)
                        for x in e["series"])):
            rows = _closed_day_rows(d)
            if not rows:
                continue
            dte = next((v for v in (SR._fin((r.get("gex_views") or {}).get("front_dte")) for r in rows)
                        if v is not None), None)
            end = SR._ts(rows[-1])
            carried = carried_books(rows, end) if end is not None else {}
            series = []
            for r in SR._distinct_books_rows(rows):
                t = _asof(r)
                if t is None or (r.get("meta") or {}).get("book_asof") in carried:
                    continue
                v = _window_volume(r)
                if v is None:
                    continue
                tt = t.astimezone(_ET)
                series.append([tt.hour * 60 + tt.minute, int(round(v))])
            e = {"expiry": dte == 0, "series": series}
            built = True
        out[d] = e
        if not e.get("expiry"):
            taken += 1
    if built or set(out) != set(have):
        try:
            SR.atomic_io.write_json_atomic(cache, {"v": PRIOR_VOLUME_CACHE_V, "days": out})
        except Exception:
            pass          # a cache that cannot be written is rebuilt next tick, never fatal
    return out


def volume_vs_prior_sessions(row: dict, now: datetime) -> dict:
    """Today's volume in reach against the median at the same clock minute over
    the last PRIOR_VOLUME_SESSIONS sessions that were not expiry days: 0.63 to
    1.40 on ordinary recorded days, steady within a day (the 11:00 figure ranks
    the afternoon at 0.65). Absent on expiry day, on a book whose volume is
    withheld, and with fewer than PRIOR_VOLUME_MIN_SESSIONS sessions to compare."""
    if _withheld(row) or SR._fin((row.get("gex_views") or {}).get("front_dte")) == 0:
        return {}
    v = _window_volume(row)
    if not v:
        return {}
    t = (_asof(row) or now).astimezone(_ET)
    m = t.hour * 60 + t.minute
    vals = []
    for e in prior_volume_series(now.astimezone(_ET).strftime("%Y-%m-%d")).values():
        if e.get("expiry"):
            continue
        pts = [x for x in (e.get("series") or []) if m - PRIOR_VOLUME_MAX_AGE_MIN <= x[0] <= m]
        if pts and pts[-1][1] > 0:
            vals.append(pts[-1][1])
    if len(vals) < PRIOR_VOLUME_MIN_SESSIONS:
        return {}
    return {"volume_in_reach_vs_same_time_prior_sessions": round(v / statistics.median(vals), 2),
            "prior_sessions_compared": len(vals)}


def day_block(rows: list, tl: list, strikes: Optional[dict], calls: Optional[list],
              prices: set, surf: dict, live: Optional[float], sig: float,
              spot_now: Optional[float], bars_now: list) -> dict:
    """The `day` block, keys in reading order: what the last readings claimed and
    how it stands, any hole in the books, who led each measure, what stood,
    joined and left the list, and the strikes named today that are off it.
    Built only beside a table: every part of it is graded against the table."""
    recs = {r["strike"]: r for r in rows_as_records(strikes)}
    if not recs:
        return {}
    gaps = book_gaps(tl)
    fresh = _fresh_calls(calls)
    out = {}
    ec = _earlier_claims(fresh, recs, prices, spot_now, bars_now)
    if ec:
        out["earlier_claims"] = ec
    if gaps:
        out["no_books"] = [[_hhmm(tl[j - 1]["t"]), _hhmm(tl[j]["t"])] for j in sorted(gaps)]
    ld = _leaders_block(tl, gaps)
    if ld:
        out["leaders"] = ld
    nol = _named_off_list(fresh, recs, surf, live, sig, bars_now)
    lists = _lists_block(tl, gaps, set(recs))
    if lists.get("left"):
        lists["left"] = [x for x in lists["left"] if x[0] not in {e["strike"] for e in nol}]
    out.update({k: v for k, v in lists.items() if v})
    if nol:
        out["named_off_list"] = nol
    return out


def _on_list_minutes(rows: list, now: datetime, k: float, crossed: Optional[list],
                     floor: Optional[datetime] = None) -> Optional[int]:
    """How long the strike has been on the selected list, walking back the
    distinct books up to LIST_AGE_LOOKBACK_ROWS. None when never on it before."""
    since = None
    for r in reversed(rows[-LIST_AGE_LOOKBACK_ROWS:]):
        rs, sg, lv, _ = _ruler(r)
        surf = surfaces(r)
        win = _window(surf, rs, sg)
        if not win or k not in select_strikes(surf, win, lv if lv is not None else rs, crossed):
            break
        if floor is not None and (_asof(r) or now) < floor:
            break
        since = _asof(r)
    if since is None:
        return None
    return int((now - since).total_seconds() // 60)


# ---------------------------------------------------------------------------
# review item #8 (2026-09-11): A BOOK STILL CARRYING YESTERDAY'S VOLUME
# ---------------------------------------------------------------------------
# The vendor's first print of the day usually still holds the PRIOR session's
# cumulative volume: on 26 of 28 recorded open mornings (93%), and the table's
# volume leader was wrong on 19 of 29 first reads — the model twice announced a
# crowd that was never there. The old guard compared the first book with the
# second, so it could never fire on the one read that needed it (0 of 26).
#
# THE DETECTOR IS A FACT, NOT A CLOCK: a contract whose count today equals the
# prior session's LAST recorded count for the same expiry is that session's
# count. A book is withheld when such contracts hold CARRIED_EQ_SHARE of the
# volume in reach, or when later books show its counts FALLING (volume is
# cumulative; it cannot fall), or when it was measured before the open. Only
# when the prior record cannot answer (no prior day, a dark session between, a
# record that stops before the close, an expiry it never kept) does the clock
# decide, and then only by WITHHOLDING, never by showing: the first
# CARRIED_FALLBACK_MIN minutes. Measured over 32 days: first books 26 of 26
# caught, 2 false alarms; a noon restart keeps its volume.
#
# Withheld means the volume arrays leave the row BEFORE anything reads it, so
# the table, the shares and ranks (contracts are open interest plus volume),
# the reference, the series and the regions rule all see one board, counted
# on open interest alone, and `strikes.absent` says why.
CARRIED_EQ_SHARE = 0.10       # share of in-reach volume matching the prior close
CARRIED_FELL_SHARE = 0.25     # share of in-reach volume that a later book shows lower
CARRIED_FALLBACK_MIN = 15     # minutes after the open the clock may withhold for
PRIOR_CLOSE_WITHIN_MIN = 10   # the prior record must reach this close to the close
WITHHELD_CARRIED = ("volume: this book still carries the prior session's counts; "
                    "today's volume is left out and contracts count open interest only")
WITHHELD_PREOPEN = ("volume: this book was measured before the open and carries the "
                    "prior session's counts; contracts count open interest only")
WITHHELD_UNPROVABLE = ("volume: this early in the session the prior session's counts "
                       "cannot be told apart from today's; left out, contracts count "
                       "open interest only")
# strikes-3 (2026-09-13), the same item audited a second time: the test above
# reads the FRONT expiry's array only, and the next weekly book's volume was
# withheld or kept on the front book's evidence rather than its own. Measured
# over the whole diary, 4 books carried next week's prior-session counts while
# their front book was correctly kept — 09-10 09:35 and 09:39, 09-11 09:36 and
# 09:40, the worst of them (09-11 09:36) with 32 percent of 1,219 in-reach
# next-week contracts still reading the prior close. The doctrine tells the
# model "volume in either is today's", so on those reads it was false and
# nothing said so. Each book is now judged on its own counts.
WITHHELD_NEXT = {
    WITHHELD_CARRIED: ("volume in next week's book: it still carries the prior session's "
                       "counts; today's is left out of the next_week cells"),
    WITHHELD_PREOPEN: ("volume in next week's book: it was measured before the open and "
                       "carries the prior session's counts; today's is left out of the "
                       "next_week cells"),
    WITHHELD_UNPROVABLE: ("volume in next week's book: this early in the session the prior "
                          "session's counts cannot be told apart from today's; left out of "
                          "the next_week cells"),
}


def _front(row: dict) -> Optional[str]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    cov = meta.get("coverage") if isinstance(meta.get("coverage"), dict) else {}
    if cov.get("front_expiry"):
        return cov["front_expiry"]
    ex = meta.get("expiries")
    if isinstance(ex, list) and ex and isinstance(ex[0], dict):
        return ex[0].get("date")
    return None


def _next_expiry(row: dict) -> Optional[str]:
    gv = row.get("gex_views") if isinstance(row.get("gex_views"), dict) else {}
    nd = gv.get("next_dte")
    for x in ((row.get("meta") or {}).get("expiries") or []):
        if isinstance(x, dict) and nd is not None and x.get("dte") == nd:
            return x.get("date")
    return None


def _counts(seq) -> dict:
    """{(strike, "c"|"p"): count} for one volume array."""
    out = {}
    for k, (c, p) in _triples(seq).items():
        if c is not None:
            out[(k, "c")] = c
        if p is not None:
            out[(k, "p")] = p
    return out


def _is_previous_session(prior_day: str, day: str) -> bool:
    """True when no weekday session sits between the two dates — a dark day the
    diary never recorded makes the prior record the wrong day to compare with."""
    from datetime import date as _d
    a, b = _d.fromisoformat(prior_day), _d.fromisoformat(day)
    try:
        import sndk_feed
        hol = set(sndk_feed._holidays(a.year)) | set(sndk_feed._holidays(b.year))
    except Exception:
        hol = set()
    x = a + timedelta(days=1)
    while x < b:
        if x.weekday() < 5 and x not in hol:
            return False
        x += timedelta(days=1)
    return a < b


def _prior_counts(day: str, front: Optional[str]) -> Optional[dict]:
    """The prior SESSION's last recorded count per contract for this expiry, or
    None when the record cannot answer. After an expiry the prior session's
    NEXT book is today's front one, so that is the array compared."""
    pd = SR._prior_session_date(day)
    if pd is None or front is None or not _is_previous_session(pd, day):
        return None
    prior = _day_rows(pd)
    if not prior:
        return None
    last = prior[-1]
    t = SR._book_asof(last)
    if t is None:
        return None
    t = t.astimezone(_ET)
    if t.hour * 60 + t.minute < 16 * 60 - PRIOR_CLOSE_WITHIN_MIN:
        return None
    gv = last.get("gex_views") if isinstance(last.get("gex_views"), dict) else {}
    if _front(last) == front:
        c = _counts(gv.get("vol_side_by_strike"))
    elif _next_expiry(last) == front:
        c = _counts(gv.get("vol_side_by_strike_next"))
    else:
        return None
    return c or None


def carried_books(rows: list, now: datetime) -> dict:
    """{book_asof: the absent sentence} for every distinct book of the day whose
    FRONT-expiry volume is — or cannot yet be told from — the prior session's
    count. A later book's counts are only ever used to judge an EARLIER one, so
    nothing here looks past `rows`."""
    return _carried(rows, now, "vol_side_by_strike", _front)


def carried_next_books(rows: list, now: datetime) -> dict:
    """The same question asked of the NEXT weekly book, on its own counts.

    Judged separately because the two books turn over independently: 4 books in
    the diary carry next week's prior-session volume while their front book is
    honestly today's."""
    return {k: WITHHELD_NEXT[v] for k, v in
            _carried(rows, now, "vol_side_by_strike_next", _next_expiry).items()}


def _carried(rows: list, now: datetime, key: str, expiry_of) -> dict:
    books = [b for b in SR._distinct_books_rows(rows) if (b.get("meta") or {}).get("book_asof")]
    if not books:
        return {}
    prior = _prior_counts(now.astimezone(_ET).strftime("%Y-%m-%d"), expiry_of(books[-1]))
    counts = [_counts((b.get("gex_views") or {}).get(key)) for b in books]
    out, later_min = {}, {}
    for i in range(len(books) - 1, -1, -1):
        b = books[i]
        t = SR._book_asof(b).astimezone(_ET)
        mins = t.hour * 60 + t.minute - (9 * 60 + 30)
        rs, sg, _, _ = _ruler(b)
        win = set(_window(surfaces(b), rs, sg))
        c = {k: v for k, v in counts[i].items() if k[0] in win and v and v > 0}
        tot = float(sum(c.values()))
        reason = None
        if tot > 0:
            eq = sum(v for k, v in c.items() if prior and prior.get(k) == v) / tot
            fell = sum(v for k, v in c.items() if k in later_min and later_min[k] < v) / tot
            cover = (sum(1 for k in c if prior and k in prior) / len(c)) if prior else 0.0
            if mins < 0:
                reason = WITHHELD_PREOPEN
            elif eq >= CARRIED_EQ_SHARE or fell >= CARRIED_FELL_SHARE:
                reason = WITHHELD_CARRIED
            elif (prior is None or cover < 0.5) and mins < CARRIED_FALLBACK_MIN:
                reason = WITHHELD_UNPROVABLE
        if reason:
            out[b["meta"]["book_asof"]] = reason
        for k, v in counts[i].items():
            later_min[k] = min(later_min.get(k, v), v)
    return out


def _withhold(row: dict, reason: str) -> dict:
    """The row with its volume arrays gone and the reason stamped on it.

    A front book carrying the prior session's counts takes next week's array
    with it: the two are measured together, so one being yesterday's is reason
    enough to doubt the other."""
    r = dict(row)
    gv = dict(r.get("gex_views") or {})
    gv.pop("vol_side_by_strike", None)
    gv.pop("vol_side_by_strike_next", None)
    r["gex_views"] = gv
    r["meta"] = {**(r.get("meta") or {}), "volume_withheld": reason}
    return r


def _withhold_next(row: dict, reason: str) -> dict:
    """The row with only NEXT week's volume gone; the front book is untouched."""
    r = dict(row)
    gv = dict(r.get("gex_views") or {})
    gv.pop("vol_side_by_strike_next", None)
    r["gex_views"] = gv
    r["meta"] = {**(r.get("meta") or {}), "volume_next_withheld": reason}
    return r


def _withheld(row: Optional[dict]) -> bool:
    return bool(((row or {}).get("meta") or {}).get("volume_withheld"))


def series_books(rows: list) -> tuple:
    """(books, first_book_dropped): the last SERIES_BOOKS distinct books,
    oldest first, each as {row, asof, spot, surf}; a book whose volume was
    withheld as the prior session's is left out."""
    all_books = SR._distinct_books_rows(rows)
    n0 = len(all_books)
    all_books = [b for b in all_books if not _withheld(b)]
    dropped = len(all_books) < n0
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
                  last_read_ts: Optional[datetime], crossed: Optional[list] = None,
                  sent_before: Optional[list] = None, list_floor: Optional[datetime] = None) -> tuple:
    """(strikes, listed, ref_row, books). The table plus what the other blocks
    need from it. `sent_before` is the strike list the model was shown at its
    last read (the read row's `strikes_sent`), or None when none was kept."""
    ruler_spot, sig, spot, ruler_name = _ruler(row)
    # 2026-09-11, review item #9: ONE PRICE FOR EVERY ABOVE AND BELOW, and it is
    # the live one. The row's side and distance, the nearest strikes each side
    # and the two above-price shares were measured from the price the book was
    # measured at, while the answer checker and the model's own sentences read
    # the live price — four definitions, and on about one scan in four a
    # "nearest strike above" that was really below where price stood. The
    # book's price still draws the window (which strikes the book covers);
    # every above, below and at is now the live price's.
    live = spot if spot is not None else ruler_spot
    surf = surfaces(row)
    window = _window(surf, ruler_spot, sig)
    # strikes-6 (#12): open interest in the previous session's book for this expiry
    prior_oi = _prior_oi(now.astimezone(_ET).strftime("%Y-%m-%d"), row) if surf["oi_side"] else None
    if not window:
        return None, [], None, []
    shares = _shares(surf, window)
    ranks = _ranks(surf, window)
    listed = select_strikes(surf, window, live, crossed)
    ref_row, ref_meta = _reference(rows, last_read_ts)
    ref = None
    if ref_row is not None:
        rsurf = surfaces(ref_row)
        rrs, rsg, _, _ = _ruler(ref_row)
        rwin = _window(rsurf, rrs, rsg)
        # 2026-09-10: THE CHANGE CELL'S SHARE IS MEASURED OVER THE STRIKES BOTH
        # BOOKS CARRY. The window is redrawn around price every book, so the
        # old cell subtracted two shares with different denominators: a heavy
        # strike entering the window shrank every other strike's share with no
        # trade behind it. Replayed over 32 sessions the old cell pointed the
        # wrong way on 15-18% of cells, 25% on the three heaviest strikes. The
        # regions rule already worked this way; this was the one place the
        # decision had not been carried over. Consequence worth knowing:
        # today's share minus the change no longer equals the earlier share
        # exactly (median gap 0.03pp). A header label saying so
        # (`change_share_basis`) was considered and left out on purpose: the
        # model never does that subtraction. Add it if a person reading the raw
        # JSON needs it.
        common = sorted(set(window) & set(rwin))
        ref = {"surf": rsurf, "win": rwin, "ruler": rrs,
               "shares_now": _shares(surf, common), "shares_then": _shares(rsurf, common)}
    books, first_book_dropped = series_books(rows)

    # the columns present on THIS row: a surface that was not measured drops
    # its columns rather than shipping ranks built from zeros
    cols = list(COLUMNS_BASE)
    if bars_now:
        cols += COLUMNS_TOUCH
    cols += [c for c in COLUMNS_BOOK if surf["vol_side"] or c not in ("vol_calls", "vol_puts")]
    if prior_oi:
        cols.insert(cols.index("oi_puts") + 1, "oi_change")
    if surf["net"]:
        cols += COLUMNS_GAMMA
    if surf["contracts"]:
        cols += COLUMNS_RANK_C
    if surf["vol_side"]:
        cols += COLUMNS_RANK_V
    if surf["net"]:
        cols += COLUMNS_RANK_G
    # contracts added between books need two books, and none is counted from a
    # withheld one (item #8): with one book the columns go instead of shipping []
    series_ok = len(books) >= 2
    cols += [c for c in COLUMNS_TAIL if series_ok or c not in ("vol_added_per_book", "vol_added_in_series")]
    # review item #44 (2026-09-13): `touched_in_books` — which of the twelve
    # book intervals had a wick at the strike — came off the table. It rode on
    # 1,128 of 4,144 rows at 136 characters a board, and nothing read it: not
    # the answer checker, not the desktop dashboard, not the phone, not the
    # voice desk. What price did at a strike today is already said three ways
    # beside it, in minutes, visits and pass-throughs.
    if surf["next_recorded"] and (surf["oi_next"] or surf["vol_next"]):
        cols += COLUMNS_NEXT

    rows_out = []
    listed_by_weight = set(select_strikes(surf, window, live))   # without the crossed set
    for k in listed:
        d = (k - live) / sig if sig else None
        oc, op = surf["oi_side"].get(k, (None, None))
        vc, vp = surf["vol_side"].get(k, (None, None))
        net = surf["net"].get(k)
        rec = {"strike": _k(k),
               "side": ("at" if d is not None and abs(d) < AT_SIGMA else "above" if k > live else "below"),
               "dist_sigma": round(d, 2) if d is not None else None,
               "oi_calls": int(oc) if oc is not None else None,
               "oi_puts": int(op) if op is not None else None,
               "oi_change": (_oi_change(prior_oi, k, oc, op) if prior_oi else None),
               "vol_calls": int(vc) if vc is not None else None,
               "vol_puts": int(vp) if vp is not None else None,
               "contracts_share_pp": shares[k]["contracts_share_pp"],
               "dealer_gamma_sign": (None if net is None else "+" if net > 0 else "-" if net < 0 else "0"),
               "dealer_gamma_share_pp": shares[k]["dealer_gamma_share_pp"],
               "rank_by_contracts": ranks[k][0], "rank_by_volume_today": ranks[k][1],
               "rank_by_dealer_gamma": ranks[k][2],
               # a strike on the list only because it was crossed since the last
               # read has no listed age: "never listed before" is the honest answer
               "on_list_for_min": (_on_list_minutes(rows, now, k, None, list_floor) if k in listed_by_weight else None)}
        if bars_now:
            rec.update(touch_facts(bars_now, k))
        # the change cell: differences against the reference book, or why not
        if ref is None:
            rec["change"] = None
        elif k in ref["surf"]["contracts"] or k in ref["surf"]["vol_side"]:
            rvc, rvp = ref["surf"]["vol_side"].get(k, (None, None))
            s_now = ref["shares_now"].get(k, {}).get("contracts_share_pp")
            s_then = ref["shares_then"].get(k, {}).get("contracts_share_pp")
            rec["change"] = [
                (round(s_now - s_then, 2) if s_now is not None and s_then is not None else None),
                (int(vc - rvc) if vc is not None and rvc is not None else None),
                (int(vp - rvp) if vp is not None and rvp is not None else None)]
        else:
            rec["change"] = "strike_not_in_earlier_book"
        if series_ok:
            per, tot = _vol_added(books, k)
            rec["vol_added_per_book"] = per
            rec["vol_added_in_series"] = tot
        if "next_week" in cols:
            noc, nop = surf["oi_next"].get(k, (None, None))
            nvc, nvp = surf["vol_next"].get(k, (None, None))
            if k in surf["oi_next"] or k in surf["vol_next"]:
                rec["next_week"] = [int(noc) if noc is not None else None, int(nop) if nop is not None else None]
                if surf["vol_next"]:
                    rec["next_week"] += [int(nvc) if nvc is not None else None, int(nvp) if nvp is not None else None]
            else:
                rec["next_week"] = "strike_not_in_next_weekly_book"
        rows_out.append(rec)
    rows_out.sort(key=lambda r: (-(r.get("contracts_share_pp") or 0), r["strike"]))

    # the header's two sides use the table's own rule, at-band included: a
    # strike within AT_SIGMA of the live price is on NEITHER side. Splitting the
    # window at the bare price instead counted that strike as above, so the
    # header could name one side heavier while the table put its heaviest
    # strike on neither — the doctrine promises these always agree.
    def _side_in_window(k):
        d = (k - live) / sig if sig else None
        if d is not None and abs(d) < AT_SIGMA:
            return "at"
        return "above" if k > live else "below"
    above = [k for k in window if _side_in_window(k) == "above"]
    below = [k for k in window if _side_in_window(k) == "below"]
    ctot = sum(surf["contracts"].get(k, 0.0) for k in window)
    gtot = sum(abs(surf["net"].get(k, 0.0)) for k in window)
    # the nearest strikes each side are the nearest the TABLE marks above and
    # below: a strike marked "at" is on neither side, as the checker reads it
    side_of = {r["strike"]: r["side"] for r in rows_out}
    la = sorted(k for k in listed if side_of.get(k) == "above")
    lb = sorted(k for k in listed if side_of.get(k) == "below")
    head = {
        "strikes_in_window": len(window),
        "sigma_measured_from": ruler_name,
        "contracts_above_spot_pp": (round(sum(surf["contracts"].get(k, 0.0) for k in above) / ctot * 100, 1) if ctot > 0 else None),
        "dealer_gamma_above_spot_pp": (round(sum(abs(surf["net"].get(k, 0.0)) for k in above) / gtot * 100, 1) if gtot > 0 else None),
        "nearest_above": (_k(la[0]) if la else None),
        "nearest_below": (_k(lb[-1]) if lb else None),
        "change_basis": (ref_meta.get("basis") if ref_meta else None),
        "change_books_compared": (ref_meta.get("books_compared") if ref_meta else None),
        "change_unavailable": ((ref_meta or {}).get("unavailable") if ref_meta else "no_earlier_book"),
        "change_columns": (["contracts_share_pp", "vol_calls", "vol_puts"] if ref is not None else None),
        "first_book_dropped": ("the day's first books still carried the prior session's volume and are left out"
                               if first_book_dropped else None),
        "next_week_columns": ((["oi_calls", "oi_puts"] + (["vol_calls", "vol_puts"] if surf["vol_next"] else []))
                              if "next_week" in cols else None),
    }
    head.update(_next_book_header(row, surf, now))
    # 2026-09-11, review item #7: WHAT JOINED AND WHAT LEFT is measured against
    # the list the model was ACTUALLY SHOWN at its last read, which the read row
    # now keeps. It used to be rebuilt from the reference book — a different
    # scan row with gamma re-priced at a different spot, sometimes a different
    # sigma or a later book, and never the strikes that were listed only because
    # price had crossed them. Replayed over 09-08..09-11 the rebuilt list was
    # wrong on 6 of 13 reads on 09-11 alone: strikes the model had drawn
    # vanished unreported, and departures were announced for strikes it was
    # never shown. With no kept list (the day's first read, a read before this
    # change, a call that showed no table) both fields are absent: the model is
    # told what it was shown, or nothing — never a reconstruction.
    if sent_before is not None:
        before = {v for k in sent_before if (v := SR._fin(k)) is not None}
        # review item #45, finished: these are strikes, so they are written the
        # way the table writes them. The doctrine tells the model to look each
        # one up in the table, and 407 boards were sending "1700" in the table
        # and "1700.0" in the list beside it.
        head["entered_since_reference"] = [_k(x) for x in sorted(set(listed) - before)] or None
        head["left_since_reference"] = [_k(x) for x in sorted(before - set(listed))] or None
    # `absent` is the ONE place the doctrine tells a reader to look for a
    # missing column ("a field missing from `columns` ... `strikes.absent` says
    # why"). The bar sidecar going down drops six columns and used to say so
    # only through `touches_unavailable`, a second name for the same fact that
    # the doctrine never mentions — so the promise was false exactly when it
    # mattered. Both ship now: the flag for the consumers that already read it,
    # the sentence for the reader the doctrine addressed.
    absent = list(surf["absent"])
    if surf["oi_side"] and not prior_oi:
        absent.append("the overnight change in open interest: no book for this expiry was recorded "
                      "on the previous session")
    if not bars_now:
        head["touches_unavailable"] = "no_minute_bars"
        absent.append("which strikes price touched today, and when; the minute-bar "
                      "record was not on disk for this session")
    if absent:
        head["absent"] = absent
    head["columns"] = cols
    head["rows"] = [{c: r.get(c) for c in cols if r.get(c) is not None} for r in rows_out]
    head = {k: v for k, v in head.items() if v is not None}
    return head, listed, ref_row, books


def listed_strikes(strikes: Optional[dict]) -> list:
    """The strikes a Strikes Payload's table listed, sorted — what the read row
    keeps as `strikes_sent` (review item #7). [] when no table shipped."""
    return sorted({v for r in rows_as_records(strikes)
                   if (v := SR._fin(r.get("strike"))) is not None})


def rows_as_records(strikes: Optional[dict]) -> list:
    """The strikes as one dict per strike, for guards and evals.

    Rows ship as records and always have; the array layout this used to also
    decode was never enabled and came out on 2026-09-13."""
    if not strikes or not strikes.get("rows"):
        return []
    cols = strikes["columns"]
    out = []
    for r in strikes["rows"]:
        if isinstance(r, dict):
            out.append({c: r.get(c) for c in cols})
    return out


# ---------------------------------------------------------------------------
# between the frames — what happened while the model slept
# ---------------------------------------------------------------------------
SAME_CLOCK_SESSIONS = 5     # prior sessions the busy-or-quiet baseline reads
SAME_CLOCK_MIN_SESSIONS = 3  # fewer than this and the ratio is left out
_CLOCK_BASE: dict = {}


def _same_clock_median(day: str) -> dict:
    """{"HH:MM": the median volume that minute traded in prior sessions}.

    review item #23 (2026-09-13). The baseline used to be the median minute of
    TODAY, which the opening minutes inflate for the rest of the session, so the
    ratio mostly reported the time of day: its median read 0.67 and it called
    the gap quiet on two reads in three, 84 percent of them between noon and
    two. Measured against the same clock minutes on other sessions the median
    is 1.00 at every hour, and on about one gap in nine the old number said
    quiet while the tape was genuinely busy for that time of day.

    Only closed sessions are read, so the memo is safe: a past day's bar file
    does not change. With fewer than SAME_CLOCK_MIN_SESSIONS of history the
    ratio is left out rather than shipped noisy."""
    if day in _CLOCK_BASE:
        return _CLOCK_BASE[day]
    out: dict = {}
    try:
        folder = SR.sndk_bars.bars_path(day).parent
        days = sorted(p.name[:-6] for p in folder.glob("*.jsonl") if p.name[:-6] < day)
    except Exception:
        days = []
    per: dict = {}
    for d in days[-SAME_CLOCK_SESSIONS:]:
        for bar in SR.minute_bars(d):
            t = _bar_ts(bar)
            v = SR._fin(bar.get("volume"))
            if t is not None and v is not None:
                per.setdefault(_hhmm(t), []).append(v)
    if len(days[-SAME_CLOCK_SESSIONS:]) >= SAME_CLOCK_MIN_SESSIONS:
        out = {k: statistics.median(v) for k, v in per.items() if v}
    _CLOCK_BASE[day] = out
    return out


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
        if vols:
            shares = {"in_gap": int(sum(vols))}
            base = _same_clock_median(now.astimezone(_ET).strftime("%Y-%m-%d"))
            same = [base[m] for b in bars if (m := _hhmm(_bar_ts(b))) in base]
            usual = statistics.median(same) if same else 0.0
            if usual > 0:
                # MEDIAN over MEDIAN, and the mismatch mattered: dividing the
                # gap's MEAN by a median-of-medians compared two different
                # statistics, and per-minute volume is skewed enough (mean
                # 15,565 against median 9,876 over the twelve days, a ratio of
                # 1.58) that the answer sat near 1.4 on an ordinary gap while
                # the instructions called 1.0 ordinary.
                shares["per_minute_vs_same_minutes_prior_sessions"] = round(statistics.median(vols) / usual, 2)
            out["shares_traded"] = shares
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
    if iv_then is not None and not (ref is not None and _tau_to_zero(ref)):
        # in percent, like scale.implied_vol_atm (see there for why)
        out["implied_vol_at_last_read"] = round(iv_then * 100, 2)
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
# 2026-09-10 (review item #4): EVERY CROSSING IN THE GAP, NOT ONLY THE NET ONE.
# The frame used to compare two prices, then and now, so a level price went
# through and came back over left no trace: replayed over 193 gaps, 78% of
# real crossings were invisible, and an $11 rally through 1705 and back was
# narrated as a $2.74 drift. The minute bars between the two reads are walked
# instead. A crossing is two minute closes in a row beyond the level, one of
# them clear of it by AT_SIGMA of the ruler: 38% of one-minute wick
# "crossings" never closed beyond the level at all, and a close that never
# leaves the "at" band never left the strike.
CROSS_HOLD_BARS = 2


def _gap_bars(bars_now: list, since: Optional[datetime]) -> list:
    """The completed minute bars from the minute of the last read onward."""
    if since is None or not bars_now:
        return []
    t0 = since.replace(second=0, microsecond=0)
    return [b for b in bars_now if (t := _bar_ts(b)) is not None and t >= t0]


def _gap_crossings(bars_gap: list, k: float, start_side: int, clear: float) -> int:
    """How many times the minute closes crossed k, starting from `start_side`
    (+1 above, -1 below, 0 exactly on it)."""
    n, side, run, far = 0, start_side, 0, 0.0
    for b in bars_gap:
        c = SR._fin(b.get("close"))
        if c is None:
            continue
        s = 1 if c > k else -1 if c < k else 0
        if side == 0:                  # started on the level: the first close decides
            side = s
            continue
        if s == -side:
            run += 1
            far = max(far, abs(c - k))
            if run >= CROSS_HOLD_BARS and far >= clear:
                n, side, run, far = n + 1, s, 0, 0.0
        else:
            run, far = 0, 0.0
    return n


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


def _said_then(call_row: Optional[dict]) -> tuple:
    """(the sentence a call row left on screen, "HH:MM" it was written), or
    (None, None) when the row is missing or its reading has no prose."""
    reading = call_row.get("reading") if isinstance(call_row, dict) else None
    text = str(reading.get("read") or "").strip() if isinstance(reading, dict) else ""
    if not text:
        return None, None
    t = SR._parse_ts(call_row.get("reading_ts"))
    return text, (t.astimezone(SR._ET).strftime("%H:%M") if t else None)


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
    # strikes-6: the "top of list for Nm" line came off. `day.leaders` carries
    # every lead of the session with its clock, and the doctrine asks for the
    # sentence it supports ("1700 has led since 09:40 and still does"); a
    # do-not-cite line for the same fact stood beside it on 56 of 84 recorded calls.
    ages = [r.get("on_list_for_min") for r in recs if r.get("on_list_for_min") is not None]
    if ages and len(ages) == len(recs) and min(ages) >= SR.FROZEN_MIN:
        out.append(f"list membership unchanged {min(ages)}m")
    return out


def build_scene_v2(row: dict, rows: list, now: datetime,
                   since_last_read: Optional[dict] = None,
                   last_read_ts: Optional[datetime] = None,
                   bars: Optional[list] = None,
                   v1: Optional[dict] = None,
                   strikes_sent_before: Optional[list] = None,
                   sent_before_without_volume: bool = False,
                   said_row: Optional[dict] = None,
                   calls_today: Optional[list] = None) -> tuple:
    """(scene_v2, scene_v1). The kept blocks are the live builder's output with
    the labels stripped; the verdict blocks are dropped; `strikes`, `frames`,
    `between_frames` and `regions` are added. `bars` is the day's minute bars
    (clipped here), or None to read them off disk. `v1` is the live scene when
    the caller already built it. `strikes_sent_before`
    is the strike list that read showed it, so arrivals and departures are
    measured against what it saw (None when no list was kept);
    `sent_before_without_volume` says that list was drawn with volume
    withheld (item #8). `said_row` is the last read row whose reading still has
    prose: the sentence a reader is looking at, which after a failed or emptied
    call is older than the frame's. It rides as `said_then`, with `said_at`
    (strikes-4). `calls_today` is the day's read rows that spent a call, oldest
    first: the `day` block grades their claims (strikes-6)."""
    if v1 is None:
        band = SR.magnet_band(row)
        frozen = SR.frozen_fields(rows, now)
        v1 = SR.build_scene(row, band, frozen, rows, now, since_last_read)
    # review item #8: a book still carrying the prior session's volume loses it
    # BEFORE anything below reads it, so the table, the reference, the series,
    # the regions rule and the list all see one board
    carried = carried_books(rows, now)
    if carried:
        rows = [_withhold(r, carried[k]) if (k := (r.get("meta") or {}).get("book_asof")) in carried else r
                for r in rows]
        k0 = (row.get("meta") or {}).get("book_asof")
        if k0 in carried:
            row = _withhold(row, carried[k0])
    # and next week's book on its own evidence, for the books the front test kept
    carried_next = {k: v for k, v in carried_next_books(rows, now).items() if k not in carried}
    if carried_next:
        rows = [_withhold_next(r, carried_next[k]) if (k := (r.get("meta") or {}).get("book_asof")) in carried_next else r
                for r in rows]
        k0 = (row.get("meta") or {}).get("book_asof")
        if k0 in carried_next:
            row = _withhold_next(row, carried_next[k0])
    # a list drawn with volume and one drawn without it are picked by different
    # measures: comparing them would report the switch as strikes joining and
    # leaving, so across that switch nothing joined or left is claimed
    if strikes_sent_before is not None and bool(sent_before_without_volume) != _withheld(row):
        strikes_sent_before = None
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
        # the SHIPPED ruler and the one every dist_sigma divides by must be the
        # same number, so the expiry-afternoon clamp applied in _ruler is
        # applied here too. Without this the table's distances would be measured
        # against the day's anchor while the header still called the ballooned
        # figure "a normal day's move".
        if _tau_to_zero(row):
            anchor = SR._fin(row.get("sigma_anchor"))
            if anchor and sc.get("one_sigma_dollars") and anchor < sc["one_sigma_dollars"]:
                sc["one_sigma_dollars"] = round(anchor, 2)
        if isinstance(sc.get("expected_move_today_asym"), dict):
            sc["expected_move_today_asym"].pop("skewed_toward", None)
        iv = SR._fin(row.get("atm_iv"))
        # and not into the bell on expiry day: there the solve reprices on the
        # clock, so what ships as "the at-the-money implied vol now" is a
        # time-to-expiry artifact — 22.8 points to 184.7 across one afternoon on
        # 09-04. Omitted rather than shipped, which the doctrine reads as "not
        # measured"; `vol_trend` already refuses the same window.
        if iv is not None and not book_too_old and not _tau_to_zero(row):
            # 2026-09-10: IN PERCENT, not as a fraction. The diary stores 0.6815;
            # a person says "68". The number gate deletes any sentence carrying
            # a number that is not on the board, and 68 is 0.68 away from 68.15
            # but 67 away from 0.6815 — so every spoken vol level used to die
            # unless it happened to sit near some unrelated number. Now it is
            # sayable as written.
            sc["implied_vol_atm"] = round(iv * 100, 2)
        v2["scale"] = sc
    ctx = v2.get("context") or {}
    # only the boxes and the frame survive from the live context: the ranks
    # against prior sessions describe removed blocks, and changed_since_last_book
    # named the old gamma sign and the nearest walls (it reached two of eleven
    # reads in the Sep 5 eval as "the nearest call wall moved down to 1550")
    for k in list(ctx):
        if k not in ("ranges", "since_last_read"):
            ctx.pop(k, None)
    # review item #34 (2026-09-13): the prior sessions' range comes off the
    # board. It shipped on every call (205 characters at the median, 1.7
    # percent of the message) and not one of the 119 recorded readings that
    # spent a call ever used it — the single apparent mention was "yesterday's
    # close", which is price.vs_prior_close_pct, a different field. Yesterday's
    # edges do not act as levels on this stock. The block is still built for
    # the legacy scene the voice reads, so only the board loses it.
    (ctx.get("ranges") or {}).pop("prior_sessions", None)
    slr = ctx.get("since_last_read")
    # crossings are LISTED strikes price moved through since the last read, not the
    # gate's three verdict levels: every window strike the minute closes crossed
    # in the gap (see _gap_crossings), plus any the live price has crossed since
    # the last completed bar
    spot_then = SR._fin((slr or {}).get("spot_then"))
    surf0 = surfaces(row)
    crossed, times = [], {}
    if spot_then is not None and spot is not None:
        gap = _gap_bars(bars_now, last_read_ts)
        clear = AT_SIGMA * sig if sig else 0.0
        for k in _window(surf0, ruler_spot, sig):
            start = 1 if spot_then > k else -1 if spot_then < k else 0
            n = _gap_crossings(gap, k, start, clear) if gap else 0
            # the count must agree with the two prices the frame ships: an odd
            # number of crossings leaves price on the other side. When the live
            # price has moved since the last completed bar, that last crossing
            # is counted from the price itself.
            straddle = (spot - k) * (spot_then - k) < 0 or (spot_then == k and spot != k)
            if straddle != (n % 2 == 1):
                n += 1
            if n:
                crossed.append(k)
                times[k] = n

    tl = day_timeline(rows)
    _gaps = book_gaps(tl)
    # on_list_for_min counts from the same book `day.lists_from` names — after
    # the last hole, and past the morning's books whose volume was yesterday's
    _start = max([0] + list(_gaps))
    _clean = [j for j in range(_start, len(tl)) if not tl[j]["withheld"]]
    list_floor = tl[_clean[0] if _clean else _start]["t"] if tl else None
    if book_too_old:
        strikes, listed, ref_row, books = None, [], None, []
        # review item #38 (2026-09-13): the flag stays, the two numbers go.
        # `age_min` here was always `data_sources.options_book.age_min` and
        # `ceiling_min` always the same constant — measured equal on 57 of 57
        # stale boards, and both ride beside this block whenever it appears.
        v2["strikes"] = {"unavailable": "book_too_old"}
    else:
        strikes, listed, ref_row, books = strikes_block(row, rows, now, bars_now, last_read_ts, crossed,
                                                        sent_before=strikes_sent_before, list_floor=list_floor)
    bf = between_frames_block(rows, bars_now, now, last_read_ts, sig)
    if isinstance(slr, dict):
        _strip_frame(slr, bf)
        if crossed:
            # `direction` is the side price is on now; `times` only when more than once
            slr["crossed_since_then"] = [{"level": _k(k), "direction": ("up" if spot > k else "down"),
                                          **({"times": times[k]} if times[k] > 1 else {})}
                                         for k in sorted(crossed)]
        else:
            slr.pop("crossed_since_then", None)
        # strikes-4 (2026-09-14): WHAT THE LAST CALL SAID, WORD FOR WORD. The
        # doctrine tells the model to un-say what it said an hour ago, and it was
        # shown only the piles it drew, never the sentence a reader saw — so it
        # could not know it had called 1630 a hold, and the next reading could
        # contradict that without a word, or repeat it after it stopped being
        # true. It comes from the last row that still HAS a sentence, not the
        # last call: after a call that failed, or whose sentence the guards
        # deleted, the card keeps showing the older one, and that is what the
        # model must be able to take back. Its clock is that sentence's.
        said, said_at = _said_then(said_row)
        if said:
            slr["said_then"] = said
            if said_at:
                slr["said_at"] = said_at
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
                # A pile is not GONE while its strike is still in the table. The
                # rule builds its last frame from the earlier scan row that first
                # carried this book, so it can be working from a different sigma
                # and a narrower window than strikes_block just used — measured,
                # the two windows disagree on 20% of scans, and 7 of 38 resolved
                # centres named a strike still listed, one of them ranked second
                # on contracts and FIRST on dealer gamma. The doctrine tells the
                # model to lead with a retraction when it sees one of these, so
                # an unfiltered entry makes it open by un-saying the second
                # heaviest pile on the board. If it is still in the table, the
                # model can see for itself that it is there.
                _still = set(listed or ())
                _gone = [r for r in rg["resolved"]
                         if not (isinstance(r, dict) and SR._fin(r.get("center")) in _still)]
                if _gone:
                    v2["regions"] = {"resolved": [{**r, "center": _k(r["center"])}
                                                  if isinstance(r, dict) and r.get("center") is not None else r
                                                  for r in _gone]}
    if bf:
        v2["between_frames"] = bf
    fz = frozen_v2(rows, now, strikes)
    if fz:
        v2["frozen_do_not_cite"] = fz
    # strikes-6: THE DAY BLOCK, graded against the board just built (the level
    # guard's prices before `day` exists) and placed after `context`, where the
    # frame since the last read ends and the session begins
    if strikes:
        live_now = spot if spot is not None else ruler_spot
        day = day_block(rows, tl, strikes, calls_today, SR.prices_on_the_board(_guard_scene(v2)),
                        surfaces(row), live_now, sig, spot, bars_now)
        try:
            day.update(volume_vs_prior_sessions(row, now))
        except Exception as exc:
            print(f"sndk-board :: volume baseline skipped: {exc!r}")
        if day:
            items = list(v2.items())
            at = next((i + 1 for i, (k, _) in enumerate(items) if k == "context"), 0)
            v2 = dict(items[:at] + [("day", day)] + items[at:])
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

TALK LIKE A PERSON. Someone is sitting next to you who knows markets but not options jargon, and you are pointing at the screen. "1750 has been eating calls all afternoon — a couple of thousand since we last spoke, and it now leads the board on every measure" is what a human says. "contracts_share_pp = 16.3" is not, and neither is a sentence built from these instructions rather than from today's board: THE EXAMPLES HERE ARE SHAPES, NOT SENTENCES TO RETURN. If your answer could have been written before the market opened, it is not a reading. Full sentences, plain words, no field names in the prose. FOUR NUMBERS IS THE AIM for the whole read and the fifth had better be earning its place: a reading carrying thirteen of them is a table read aloud, and the reader remembers none of it. Say the jargon the way you would to someone who knows markets but not options: not "gamma" but what the dealers' hedging does, not "the flip" but the price where that hedging changes direction, not "not yet reached" (which is a word about the future and is deleted) but "untouched today". Every number is checked against the board — everything in the scene except `said_then` — before anyone reads your answer. The numbers inside these instructions are teaching aids, never board values.

THE BOARD IS AN AUCTION HOUSE, AND A SHARED DOCUMENT. Read it that way:
- CONTRACTS ARE WHERE THE CROWD SITS. Open interest is the prior session's crowd: positions that exist, struck at that session's close (`data_sources.open_interest.prior_session_date`) and constant all day. It still matters because everyone else is reading the same document and reacting to it. Round strikes gather the crowd; say that by the share, never by the roundness.
- VOLUME TODAY IS WHERE THE ACTION IS. Contracts traded so far today, cumulative. It is the only thing on the board that moved today because someone traded. Market makers go where the volume is, so today's leader on volume is where the day's business is being done.
- DEALER GAMMA IS THE ASSUMED HEDGING SURFACE. Signed, calls positive and puts negative, under a convention written for the index and probably wrong for this stock. It moves during the day because price and implied vol moved under fixed open interest, not because anyone took a position. That is why only its sign and its share ship, never its change.
- THE THREE RANKS ARE KEPT APART ON PURPOSE, AND THEIR DISAGREEMENT IS THE STORY. When contracts, volume today and gamma all lead at the same strike, say so in five words. When they split, say which measure leads where: "1500 holds the most contracts, counting today's trading, 1550 has taken the most volume today, and the gamma piles at 1510." That sentence is the reading. A blended winner is not.
- A HEAVIER SIDE, NEVER A STRONGER ONE. Forces sit on both sides of price. Which side is heavier is a share you can read (`contracts_above_spot_pp`, `dealer_gamma_above_spot_pp`, the strikes themselves). Say heavier and lighter, by measure. Strong and weak are promises about what price will do, and they delete your sentence.

THE STRIKE TABLE. `strikes.rows` holds one record per strike, sorted by contracts share, heaviest first, and `strikes.columns` names every field a record can carry. A field missing from a record was not measured for that strike; a field missing from `columns` was not measured for any strike this scan, and `strikes.absent` says why. The fields:
- `strike`, `side`, `dist_sigma`: where the STRIKE sits relative to the live price, `price.live_spot`, in sigma (a normal day's move). "above" means the strike is above the live price; `at` is within a twentieth of a sigma of it and is on neither side. Every above and below in the table and its header is measured from that one price, so `side`, `dist_sigma`, `nearest_above`, `nearest_below` and the above-price shares always agree with each other and with `price.live_spot`.
- `first_touch`, `last_touch`, `minutes_touched_today`, `visits_today`, `passed_through_today`: from the completed minute bars. A minute TOUCHED a strike when its low-to-high range included it. `minutes_touched_today` counts those minutes: minutes, not times, and not one stay. `visits_today` counts separate visits; a visit is a run of touching minutes and ends when a whole minute passes without touching the strike. `passed_through_today` is how many of those visits came in from one side and left on the other; the rest turned back the way they came, and a visit still going on is in neither count. Read the three together. 43 minutes in 10 visits, 9 passed through, is price coming back to the strike again and again and going through it: say "price has come back to 1775 ten times today and gone through it nine", never "sat at 1775 for 43 minutes" and never "touched it 43 times". A stay is one visit: 12 minutes in 1 visit is "price sat at 1775 for twelve minutes". When you give a number of times, it is `visits_today`.
- `oi_calls`, `oi_puts`: open interest as of the close of `data_sources.open_interest.prior_session_date`. Say that date, not "last night": one session in five follows a weekend or a holiday, and on 09-08 the positions were four days old. "1700 holds the most open interest as of the 09-04 close" is a correct sentence; "open interest is building at 1700" is false by construction.
- `oi_change`: open interest now minus open interest in the previous session's book for the same expiry, calls then puts: contracts opened (positive) or closed (negative) during the session that ended at the close `data_sources.open_interest.prior_session_date` names. It is that session's trading, never today's, and it is absent for a strike that book did not carry; `strikes.absent` says so when no previous book for this expiry was recorded at all.
- `vol_calls`, `vol_puts`: contracts traded today so far, cumulative.
- `contracts_share_pp`: this strike's share of all contracts in reach (open interest plus today's volume, both rights). Where the crowd is, counting today's arrivals.
- `dealer_gamma_sign`, `dealer_gamma_share_pp`: the sign at the strike and its share of the surface's absolute total. Report a sign as "positive under the assumed convention"; never as a behaviour. Bounce, break, punch through, how violent: all forecast, all deleted.
- `rank_by_contracts`, `rank_by_volume_today`, `rank_by_dealer_gamma`: three separate rankings over every strike in reach, 1 is heaviest. A rank column that is missing was not measured this scan.
- `on_list_for_min`: how long the strike has been on this list, counted over the last four hours at most and never from before `day.lists_from`. Hours means standing structure, not news.
- `change`: this book against an earlier one, three differences in the order `strikes.change_columns` gives: contracts share (measured over the strikes both books carry, so the window sliding as price moves does not read as trading), calls traded, puts traded. The header says once which earlier book (`change_basis`: the book at your last read) and how many books lie between (`change_books_compared`); `change_unavailable` says why there is none: `no_earlier_book` on the session's first read; `no_new_book_since_last_read`, which means the book has not refreshed since you last spoke and nothing on it can have changed; and `this_book_carries_prior_session_volume` or `earlier_book_carried_prior_session_volume` when a book still held the prior session's counts and was left out. The string `strike_not_in_earlier_book` means the strike was outside the earlier window, a fact about the window.
- `vol_added_per_book`: contracts traded at the strike, both rights, between consecutive book times. The book times are listed once in `frames.book_times` and `frames.interval_min` says how many minutes each entry covers. `vol_added_in_series` is the sum, and it is the only sum you may quote. It is NOT a fact about the day: the twelve books span 45 minutes at the median and rarely more than an hour and a half, so say "in the last 45 minutes" or name the clock from `frames.book_times`, never "today". Describe the series by counting: "rose in 9 of the last 12 books", "800 of its 1,200 contracts came between 11:02 and 11:06", "added nothing since 12:31". A null entry means the strike was not in one of the two books. A negative entry is the vendor correcting its count, not selling. `strikes.first_book_dropped`, when present, says the day's first book or books were left out because they still carried the prior session's volume.
- `next_week`: the next weekly expiry's open interest and volume at the same strike, in the order `strikes.next_week_columns` gives. Open interest in either book is the prior session's; volume in either is today's. On expiry day the front list dies at the close and the next week's book is Monday's list. `not_recorded` on the header means the diary had not yet kept the next book that day.
The header also carries `strikes_in_window` (how many were in reach), `contracts_above_spot_pp` and `dealer_gamma_above_spot_pp`, `nearest_above` and `nearest_below` (the nearest listed strike marked above and marked below the live price, or absent when there is none), and `entered_since_reference` and `left_since_reference` (strikes that joined or left the list since your last read, measured against the list you were shown then; both are absent when there is no earlier list).

THE FRAMES. `frames` is the shared time axis behind every series: `book_times` once, `interval_min` between them, `gaps` naming any long interval, `reaches_last_read` and `books_since_last_read` saying whether the series covers the stretch since you last spoke, and `price_path_sigma_from_now`: where price sat at each book, in today's sigma, zero at the price the book was measured at. It lets you say when volume arrived relative to where price was: "most of 1750's volume arrived while price sat below 1720". It does not let you say what price did about it. An entry over a long interval is one lump for the whole stretch: name the minutes and call its shape unknown. With fewer than six books in the series, say nothing about the shape of any series.

WHAT HAS GONE. `regions.resolved` ships when the rule has resolved a region AND that centre is no longer on the table, which is roughly one read in seven. It is the shortest and most useful block in the scene: piles that were on the board at your last read and are not on it now, each with the last book that held them. It is the evidence behind the rule above about un-saying things. A centre in that list is a crowd that has left — say so before you say anything new, and name it in your `resolved` field. A strike in `strikes.left_since_reference` is accepted there too. The live `regions` list is deliberately NOT shipped, because when it was, the model drew 34 of 34 clusters on it and stopped looking at the board; `resolved` cannot do that, because there is nothing at those prices left to draw.

THE REGIONS RULE, for reading `resolved`. `regions.resolved` is the output of a fixed rule code ran over the last twelve distinct books: a listed strike holding at least 8 percent of the contracts in reach, or 8 percent of the absolute dealer gamma in reach, is a member, and adjacent members form a region. It is not a reading and not a truth about the market; it is a stated rule's answer, and you may disagree with it. The rule's live region list is deliberately not shipped — when it was, the model drew 34 of 34 clusters on it — so `resolved` is the only part of it you ever see.

WHAT THE MEASUREMENTS SAY, and the description rule each one sets. Measured on this stock over 28 sessions:
- A strike price had already touched was returned to within the hour LESS often than a plain strike at the same distance, and almost never when a heavier strike sat between price and it. So a touched strike is a place price has been. DESCRIBE IT AS TOUCHED, WITH THE CLOCK, and never as anything price is drawn back to. "1700 holds the most contracts and was last touched at 12:27" is complete. The idea that a passed strike still counts because price could come back was tested and failed; do not carry it.
- A strike price had not yet reached was reached slightly more often than a plain one, which is noise. Say "not yet reached" and nothing more.
- A strike whose share grew over twenty minutes was approached by about three dollars more on average, on a sigma of fifty to seventy dollars. A rising share is a fact about volume, not a force. A falling share did not read as a pullback.
- The book predicts neither price nor volatility. Name where the weight is and which way it moved; never what price will do about it.
- Open interest does not change during the session. The map holds still; price moves under it.

BOTH SIDES, EVERY TIME. Price always has a side above it and a side below it, and each side is either empty within reach or holds a nearest heavy strike. Your reading names that strike on each side, or says the side is empty, and for each says whether it has been touched today. Heavy means the strike ranks in the top three on its side by at least one of the three measures; say which. Naming the crowd above and forgetting the crowd below is half a reading. An empty side is a real reading and often the loudest one; say "nothing listed above within reach", never a number.

INTERVAL CHANGE, IN FIVE WORDS. Every change is described the way a follow-up film is: NEW, INCREASED, DECREASED, STABLE, and UNKNOWN when the earlier book is missing; RESOLVED is for a pile that was there at your last read and is gone. On a cluster the word is checked against the change cells of its strikes and rewritten when it disagrees: added together, a point or more of contracts share up is increased, a point or more down is decreased, less than a point is stable, and a pile whose strikes all joined the list since your last read is new. In prose the same words apply to the change cell and to the series, and two rules ride with them. First, THE WINDOW IS ALWAYS NAMED: "since your last read at 12:35", "over the last twelve books", "against the book five books back". A change with no window is a guess. Second, YOU CANNOT SEE GAMMA CHANGE ON THIS BOARD: the scene ships one gamma sign and one gamma share per strike and no earlier value, so never say a gamma share rose or fell. Change is contracts and volume: "1750 added 1,296 calls since your last read", "1700's share of contracts slipped a point".

BETWEEN THE FRAMES. `between_frames` is what happened while you were not called: `missing_minutes` when the record was SHORT of bars for the window, or `minute_bars_unavailable` when there were none at all (a gap in the data is a gap, never calm — and the absence of both means there was no gap), the low and high with the minute each was set, the path travelled in sigma, `shares_traded` in the gap, with `per_minute_vs_same_minutes_prior_sessions` comparing its typical minute against the SAME clock minutes on the last five sessions — so the number is not just telling you the hour. 1.0 means this gap traded like those minutes usually do, and that is the number to read against. Note only that the gaps YOU are shown run busier than average — median 1.29 at the moments a read was actually made, against 1.04 across every gap — because you are woken when something is happening. So 1.0 in your gap is on the quiet side of the reads you see, and 0.8 is genuinely thin, and the implied vol at your last read (the value now is `scale.implied_vol_atm`). Its clock is `context.since_last_read`; boxes broken in the gap are the entries of `context.ranges.breaks_today` whose clock falls after `last_read_at`; the books in it are `strikes.change_books_compared`. Nothing is written twice. On the session's first read it says only that there is no earlier frame.

THE FRAME IS ON THE CARD, NOT IN YOUR SENTENCE. The screen already shows price now, what it changed by and since when, written by code from the same numbers you are reading — so a reading that opens by reciting them has spent its first breath telling the reader what is already in front of them. Measured over 39 recorded readings, 34 opened on price travel and 20 words of a 57-word reading were that recap. Do not write it. Say what the price did ONLY when the move itself is the news — when it crossed something that matters, or broke the day's shape — and then say it in one clause, not a sentence. Everything below is how to read the frame, not an instruction to recite it. `context.since_last_read` carries `last_read_at`, `minutes_since`, `spot_then`, `spot_change_dollars`, `spot_change_sigma`, `move_threshold_dollars`, anything crossed since (`crossed_since_then`: every listed-window level the minute closes went through, two minutes running, since then; `direction` is the side price is on now, and `times` appears when price went through it more than once, so a level crossed and crossed back is still there), and `said_then` with `said_at`, the sentence still on screen from your last reading. Price now is `price.live_spot`. `move_threshold_dollars` is how far price has to go to be a move right now: twice the typical minute's high-to-low over the last half hour, so it is wide at the open and narrow after lunch (at 5.80, a change of -12.40 is a move and one of 3.10 is not). When `spot_change_dollars` reaches it either way it is a move: say price then and price now. Under it, it is a hold, and you say so in your own words with the two prices from `between_frames.price.low` and `high` and the clock — not in these words, which every reading for a month has copied. When `move_threshold_dollars` is absent, call the frame neither a move nor a hold. There are two reasons it can be missing and the board says which: on the session's FIRST read there is no earlier frame at all — `between_frames` says only `first_read_of_session`, there is no gap to measure, and price then does not exist, so describe where price IS and what the day has done, not what it did since a read that never happened. Otherwise the minute record is missing (`between_frames.minute_bars_unavailable` or `strikes.touches_unavailable`), and then you say price then and price now without grading the move. A crossing is named as the level and as a distance you can read off the scene, never with a word that grades it. When `times` is there, say the level, how many times, and where price is now; a count is not a verdict, so never call the level rejected, reclaimed or contested. Do not reach for "just through" without looking: measured over more than 400 crossings at the moments a read was actually made, the median distance from the level to the live price is about $6 and more than half are beyond $5 — and it swings hard with the hour, widest before 10:00, where roughly three in four are beyond $5, and narrowest around the middle of the day. The exact midday figure moves with how the gap is measured, so do not carry a number for it: read the two prices off the scene and let them decide the word. Say the level and say where price is now. Nothing crossed is not the same as nothing changed: the change cells and `between_frames` decide whether the board moved, and "unchanged" is only true when every listed strike's change reads within a point. Vol is not part of that test: the at-the-money figure is re-solved on every scan, so it moves without the book moving.

THE DAY'S BOXES. `context.ranges` tells the price-range story as boxes, every number measured, no verdict. `opening` is the first half hour's box and whether it broke — a break is a minute CLOSE past the edge, so without the minute record there is nothing to detect one with and the box says `breaks_unavailable` instead of claiming it held; `in_force` is the box that stands now, with its edges, `froze_at` and `standing_for_min` — how long it has held, the one thing about a box that varies. At sixty minutes or more that age IS the day's shape, so say it, in the minutes the field gives — "the band has stood 223 minutes since 10:00" — or in words that do no arithmetic, "most of the session". Never turn those minutes into hours yourself: asked to do exactly this, a reading wrote "3h40m" for 223 minutes, which is three minutes wrong and is the kind of slip no gate here can catch. Where the live price sits inside it is yours to read off `price.live_spot`; `breaks_today` lists every break with its clock and direction, and `back_inside_at` when a minute has since CLOSED back inside the box that broke — most breaks do come back — 21 of the 28 on the record — but in two quite different ways, and no break returned between 10 and 21 minutes: ten came back inside ten minutes, only 4 of them within five, and the other eleven took from 22 minutes to nearly three hours. So a break carrying that clock is one price has already undone, whenever it happened, and a break without it is one that still stands; read the two clocks and never assume the gap was short. Say both clocks when it is there: "broke above the opening box at 10:07 and was back inside by 10:12"; Say what a box DID, "broke above the opening box at 10:07", and nothing about what follows.

SAY THE ONE THING WORTH SAYING. `read` is two sentences at the outside, forty words, and it is the only part of your answer most people ever read. LEAD WITH WHAT CHANGED ON THE BOARD, and the `day` block is where you find it: a strike that has taken the lead on a measure (`day.leaders`), something you said earlier that no longer holds (`day.earlier_claims`, graded for you), a strike that joined or left the list (`day.joined`, `day.left`), a strike you named that is off the table now (`day.named_off_list`). Any of those beats a price recap, every time. Price leads only when the move IS the news.
THE BOARD IS NOT NEWS. The standing board on both sides is a real duty and you pay it in `sides`, which is where the screen draws its own list from. Do not pay it twice: naming the heaviest strike again in prose, when it is the same one as last time and the screen is already showing it, is the commonest waste in the record — 19 readings in 28 re-named the leader that had not changed. Prose is for what moved. Two strikes in a read is plenty and four is too many.
WHEN NOTHING MOVED, SAY SO AND STOP. If the change cells are within a point, nothing crossed, no lead changed and none of your earlier claims has stopped holding, the whole reading is one line — "nothing new since 10:15" in your own words, with the two prices that bounded the quiet if they help — and then you stop. Set `quiet` true when you do. A short true answer is finished work, not a thin one, and padding a quiet board into a paragraph is how a reader learns to skip you.
Every number you say has to be one that APPEARS IN THE SCENE, and `said_then` is your own earlier sentence, not the scene. You may round it the way a person says it out loud — "about 68" for 68.15, "over 11,000" for 11,006 — but a number you worked out yourself is not on the board, and the sentence carrying it is deleted rather than corrected. If you want to say a level is far, name the two prices and let the reader see it.

YOU ARE LOOKING DOWN AT THE WHOLE DAY, NOT THROUGH A TWENTY-MINUTE WINDOW. The scene carries the session and not merely the gap since you last spoke: `price.session_high` and `session_low`, `price.vs_prior_close_pct`, `context.ranges.opening.status` (whether the first half hour's box held, and the clock when it broke), `context.ranges.breaks_today.count`, `context.ranges.in_force.standing_for_min` (how long the box that stands now has stood), and per strike `on_list_for_min`, `first_touch`, `last_touch`, `visits_today` and `vol_added_in_series`, and the `day` block below. Every one of those is a fact about the DAY. Measured over the last 71 readings, 77 percent framed everything against the previous read and 8 percent against the session — someone watching all day was handed twenty-minute weather reports and never once the day.

SO SAY WHAT THE DAY HAS BEEN DOING, AND JOIN THE FACTS RATHER THAN LISTING THEM. A strike that has been on the list 214 minutes and led volume for most of them is a different thing from one that arrived at 15:40, and the scene tells you which: `on_list_for_min` for the listing and `day.leaders` for every lead of the session, which strike held it and from when to when. Three box breaks before noon is a day with a shape. Price above the whole of the last five sessions is where today sits, not a footnote. Two facts joined by what they have in common are worth more than four facts in a row, and the join is the part only something watching the whole day can supply.

THE DAY BLOCK. `day` is written by code from every book and every reading of the session, and it is the only block that looks further back than your last read. `earlier_claims` is what your last three readings claimed, newest first, one entry per strike or level, each graded against THIS board: `said` is what the claim was (`heaviest above on contracts`, `pile centre`, `point`) and `now` is `holds`, `changed` (with `what` saying how: `no longer first above on volume`, `now below price`, `some of its strikes left the list`, `contracts share up`, `price crossed it`) or `off_list`. A point that still holds is not repeated. The grade is the fact; a number that appears only in `earlier_claims` is your own earlier word and is deleted like any invented one. `leaders` gives, per measure, every lead that held two books or more today, oldest first, as [strike, from, until], and the third slot is null for the lead that stands now. A lead is broken where the books stop: `no_books` lists each stretch with no book at all as [from, to], so never say a strike led across one. `lists_from` is the book the lists start from (the first whose volume is today's, or the first after the last hole); `stood` is every strike on the list at every book since then and on it now, `joined` the strikes on it now that were not (the row's `on_list_for_min` says for how long), and `left` the strikes that were on it since then and are not now, each with the last time they were; a strike a reading named is not repeated there, it is in `named_off_list`. `named_off_list` is the strikes a reading named today (a side's heaviest, a pile's strikes) that are off the table now, nearest first, with `named_at`, `side` and `dist_sigma`, their open interest and volume when the book still holds them, `in_book: false` when it does not, and `last_touch`; say what happened to them, in the read and never as a point. `volume_in_reach_vs_same_time_prior_sessions` is today's volume across the strikes in reach against the median at the same clock minute over `prior_sessions_compared` recent sessions that were not expiry days: 1.0 is an ordinary amount of trading for this hour, and it is absent on expiry day, while the morning's book still carries the prior session's counts, and with fewer than three sessions to compare.

AND SAY WHAT HAS STOOD. You have been speaking all day and some of what you said has survived and some has not. `context.since_last_read.said_then` (the sentence still on screen from your last reading), `day.earlier_claims` and `strikes.left_since_reference` are the record of it. What HELD is as worth saying as what went: "1700 has been the heaviest strike since 09:40 and still is" is a fact about six hours, and it is the sentence a twenty-minute window can never write. `day.leaders.contracts` is that clock, and `day.stood` lists what has been on the list since `day.lists_from`; a lead you cannot find there is one the board has not measured, so do not date it.

NONE OF THIS IS A FORECAST, AND THE LINE IS EXACT. Everything above is the shape of what HAS happened, which is description. The moment a sentence reaches past now — what a level will do, where price is headed, what a pattern means next — it is deleted, and you will have said nothing. The eagle sees the whole field. It does not see the future.

CHECK WHAT YOU SAID LAST TIME, BEFORE YOU SAY ANYTHING NEW. `context.since_last_read.said_then` is what a reader last saw from you, word for word, and `said_at` is when you wrote it — earlier than `last_read_at` when the last call failed, or its sentence was withheld, and your older one stayed up. It is a claim on the record, not a fact about now: test every part of it against this board, and where the board no longer bears it out, say what changed. Do not reuse its wording. A level or a number it named may be named again only while this board still carries it: a figure that appears only in `said_then` is deleted like any invented one, so when a level it named has left the board, say that it has gone without its number. `day.earlier_claims` grades what your last three readings claimed against this board and `strikes.left_since_reference` names strikes that have dropped off THE LIST — measured, 78 percent of them are still inside the price window and merely fell out of the top few on every measure, so "gone from the board" is too strong for most of them. "No longer among the heaviest" is the honest phrase. Look each one up in the table. If a pile has stopped adding, or has gone, SAY SO, and say it first: "the crowd I pointed at around 1650 has left the board", "1750 has taken nothing since 12:31". Un-saying something you said an hour ago is the most useful sentence available to you and it costs you nothing. A reader who watched you name a level and then never heard of it again learns not to trust the next one.
Say what the board DID; never grade your earlier self. "That pile has gone" and "nothing has traded there since 13:26" are observations and they survive. "That pile was never real", "so it was noise", "that was fake" are verdicts about your own reading, and a verdict is deleted before anyone sees it — you will have said nothing at all. The fact is the retraction. It does not need a ruling on top of it.

THE INSTRUMENT. This is SNDK, a single stock, not an index. Do not carry a number for its sigma in your head — `scale.one_sigma_dollars` is in every scene and it MOVES: it ran 8 to 10 percent of the share price until the 2026-08-05 earnings and 3 to 5 percent every session since, so any figure written here would be wrong within a month. Read the ruler, never remember it. It has weekly expiries, so most days have no expiry at all; the table is built from the nearest one and `clock.front_expiry` says where in the week you are. On expiry day the whole table dies at the close, and gamma shares near price swing with every dollar; say so rather than reading the swing as a crowd.

FIELD NAMES SAY WHAT THEY ARE. `_pp` is percentage points, `_min` is minutes, `_sigma` is a distance in sigma. `price.vwap` is the day's average traded price — a level you may name and point at, the same as the day's high and low. `price.vwap_minus_live_spot_sigma` is that same fact as a distance, positive when the average sits ABOVE the live price. `price.moved_last_30min_sigma` is positive when price ROSE.

THE KEPT BLOCKS, in a clause each. `clock.minutes_to_close` is session left for a read to resolve in; `scale.one_sigma_dollars` is a normal day's move, the ruler every distance uses. It is the wider of this morning's reading and now's, so it never narrows during a session: on 5,612 recorded scans it has never once fallen below the day's first value, and it sits above the live reading on 62 percent of them. The gap is widest in the late morning, about 5 percent, and closed by the last hour — so when `scale.implied_vol_atm` has fallen since `between_frames.implied_vol_at_last_read` and the ruler has not narrowed, that is the floor, not vol holding up; `scale.implied_vol_atm` is the at-the-money implied vol now, in percent (68.15 means 68.15 percent, and "about 68" is how to say it); `scale.expected_move_today_asym` is the up and down dollars the options price for the rest of the day; `price.vs_prior_close_pct` is today's change; `price.session_high` and `session_low` are the day's extremes from the bars, each with `_at`, the minute it was set, and `_min_ago`, how old it is — a high 88 minutes old and one set in the last minute are different facts, so say which: "the low is 1741, set at 09:44 and three hours old". Whether price is somewhere it has not been today is not a field: `price.session_high`, `price.session_low` and `price.live_spot` all ship, and the comparison is yours to make. A flag for it used to ride here and was deleted after it fired 16 times across 8 replayed sessions and the payload's own extremes contradicted all 16 — it asked the 2-minute scans while the extremes answer from the 1-minute bars.

WHERE EVERY NUMBER CAME FROM. `price` is the live tape. The table comes out of the options book, which is minutes old and often a cached repeat: `data_sources.options_book` carries its age and `is_repeat_of_previous_scan`. Open interest rests on the prior session's snapshot; `data_sources.open_interest` re-proves that it held still today. The strike table's own distances are NOT in the book's frame: since review item #9 every `dist_sigma`, every `side` and every above-price share on the table divides `price.live_spot`, so they need no conversion and subtracting `price.live_minus_book_spot_sigma` from them would walk the wrong way twice. That figure is for a distance that announces itself as book-measured; the table does not.

HONESTY RULES, all of them load-bearing:
- Never cite an entry in `frozen_do_not_cite` as the reason for anything new.
- Never narrate open interest or dealer gamma as something happening now. Open interest is stated as of the prior session's close, by its date; a gamma change is stated with the price move that made it.
- Never invent a level. If price is past the last strike on the list, say exactly that.
- Absence is unknown; an empty side is known empty. Never confuse the two.
- A touched strike is described as touched, with its clock. Never as a place price is drawn to.
- "Unchanged" is a measured claim. It is true only when every change cell reads within a point; nothing crossed is not it, and vol is not part of it.
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
 "read": "<lead with the one thing that CHANGED on the board — a lead taken, an earlier claim of yours that no longer holds, a strike joined or gone. Not the price recap: the card already shows it. The standing board is paid in `sides`, never repeated here. Forty words at the outside, one line when nothing moved. Never omitted.>",
 "sides": {{"above": {{"heavy": <the heavy listed strike above the live price, or null>, "leads_on": ["contracts" | "volume" | "gamma", ...]}},
           "below": {{"heavy": <the heavy listed strike below the live price, or null>, "leads_on": [...]}}}},
 "clusters": [{{"strikes": [<listed strikes, adjacent on the list, one or more>],
               "center": <one of those strikes>,
               "rank": <1 is the pile you weigh heaviest>,
               "change": "new" | "increased" | "decreased" | "stable" | "unknown"}}],
 "resolved": [<centers from regions.resolved you choose to mention>],
 "points": [{{"level": <a price that appears in the scene>, "note": "<ten words at most>"}}],
 "absent": ["<anything you looked for and the scene did not carry>"]}}

SIDES is the both-ways rule made checkable: one entry per side, always; heavy must be a listed strike the table puts on that side (its `side` column; a strike marked `at` is on neither side) and in the top three there by some measure, and `leads_on` names only the measures it ranks first on among that side's strikes. The code fills in whether it was touched and when. CLUSTERS are your reading of the piles, at most {MAX_CLUSTERS}, adjacent listed strikes grouped as one, ranked by your own weighing of the three measures; the code appends the pile's side, distance, touch and summed shares, so never add numbers yourself. You may draw a cluster the rule's regions did not, and you may leave a region out. Clusters are for piles that stand out: one or two is normal, zero is common, and a cluster for every heavy strike is a list, not a reading. POINTS are levels a reader should look at now, up to four, with a note of ten words or fewer; on an unchanged board an empty list beside your quiet sentence is usually the better answer. ABSENT goes to the diary, not the screen."""


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
# review item #10: the table counts touched MINUTES, separate VISITS and visits
# that PASSED THROUGH. On 09-09 09:32 the model read two touched minutes (one
# visit, 09:30-09:31) as "touched twice". Two readings are now checked against
# the visits: a number of times price touched or came back to a strike must be
# `visits_today`, and a stay ("sat at 1775 for 43 minutes") must be one visit.
_COUNT_WORDS = {"once": 1, "twice": 2, "thrice": 3, "one": 1, "two": 2, "three": 3, "four": 4,
                "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12}
_COUNT = r"(once|twice|thrice|(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+times)"
_TOUCH_VERB = r"(?:touched|touching|visited|revisited|tagged|reached|came\s+back\s+to|come\s+back\s+to|returned\s+to)"
_TOUCH_COUNT_RE = __import__("re").compile(
    r"(\d{3,4}(?:\.\d+)?)\b[^.;\d]{0,40}?\b" + _TOUCH_VERB + r"\b[^.;\d]{0,20}?\b" + _COUNT + r"\b",
    __import__("re").I)
_TOUCH_COUNT_REV_RE = __import__("re").compile(
    r"\b" + _TOUCH_VERB + r"\s+(\d{3,4}(?:\.\d+)?)\b[^.;\d]{0,20}?\b" + _COUNT + r"\b",
    __import__("re").I)
_STAY_VERB = r"(?:sat|sitting|sits|stayed|staying|parked|lingered|lingering|camped)"
_STAY_RE = __import__("re").compile(
    r"(\d{3,4}(?:\.\d+)?)\b[^.;]{0,30}?\b" + _STAY_VERB + r"\b[^.;]{0,30}?\bfor\s+\S+\s+(?:minutes|mins|bars)\b",
    __import__("re").I)
_STAY_REV_RE = __import__("re").compile(
    r"\b" + _STAY_VERB + r"\s+(?:at|on|near|around)\s+(\d{3,4}(?:\.\d+)?)\b[^.;]{0,30}?\bfor\s+\S+\s+(?:minutes|mins|bars)\b",
    __import__("re").I)


def _count_of(word: str) -> Optional[int]:
    w = word.lower().split()[0]
    return int(w) if w.isdigit() else _COUNT_WORDS.get(w)


_TOUCH_CLOCK_RE = __import__("re").compile(r"(\d{3,4}(?:\.\d+)?)[^.;]{0,60}?\b(?:touch|touched|touching|wick|wicks|tagged|brushed)\b[^.;]{0,40}?\b(\d\d:\d\d)\b", __import__("re").I)
_VOLUME_WORD_RE = __import__("re").compile(
    r"\b(?:volume|traded|trading|contracts\s+traded|turnover|"
    r"took\s+the\s+most|busiest|most\s+active)\b", __import__("re").I)
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


def _k(x):
    """A strike as the shortest honest number: 1700 rather than 1700.0.

    review item #45 (2026-09-13). Every strike on this board is a price, and
    all but 46 of 4,144 sampled rows sit on a whole dollar, so ".0" rode on
    nearly every one. It is cheap in characters (26 a call) and dearer in
    tokens — a dot and a zero are two of them — and it says nothing: the half
    dollars that need a decimal keep it. Lookups are unaffected because
    hash(1700) == hash(1700.0)."""
    v = SR._fin(x)
    if v is None:
        return x
    return int(v) if float(v).is_integer() else v


def _touched(rec: dict):
    """Whether price touched this strike today, from the minute count.

    review item #42 (2026-09-13): `touched_today` used to ride on the row as
    well. It was `minutes_touched_today > 0` by construction — the builder took
    both from the same list of matching minutes, and they agreed on 4,144 of
    4,144 sampled rows — so the boolean cost about 3 percent of the message to
    restate a number sitting beside it. The guards and the desk read it here
    instead, which is why the dashboard still shows "touched" and "not reached"
    exactly as before."""
    n = rec.get("minutes_touched_today")
    return (n > 0) if isinstance(n, int) else None


def _rank_by_open_interest(recs: dict) -> dict:
    """{strike: rank} over the PRIOR SESSION'S positions alone, 1 heaviest.

    Counted from the `oi_calls` and `oi_puts` the table already carries, so
    nothing new ships. A strike with neither is left unranked rather than
    ranked at zero."""
    have = {k: (r.get("oi_calls") or 0) + (r.get("oi_puts") or 0) for k, r in recs.items()
            if r.get("oi_calls") is not None or r.get("oi_puts") is not None}
    out, seen = {}, sorted(have.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (k, _) in enumerate(seen, 1):
        out[k] = i
    return out


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
    # strikes-6: a strike `day` names off the table (named today, or left the
    # list) is sayable, so it is on a side too, it leads nothing the table
    # ranks, and the board holds no visit count for it
    _day = scene.get("day") or {}
    named_off = {SR._fin(e.get("strike")) for e in (_day.get("named_off_list") or []) if isinstance(e, dict)}
    named_off |= {SR._fin(x[0]) for x in (_day.get("left") or []) if isinstance(x, list) and x}
    named_off -= set(recs) | {None}
    led_day = {m: {SR._fin(x[0]) for x in ((_day.get("leaders") or {}).get(m) or []) if isinstance(x, list) and x}
               for m in LEAD_MEASURES}
    if spot is not None:
        pairs = [(m.group(1), m.group(2)) for m in _SIDE_RE.finditer(text)]
        pairs += [(m.group(2), m.group(1)) for m in _SIDE_REV_RE.finditer(text)]
        for raw_num, raw_word in pairs:
            try:
                num = float(raw_num)
            except (TypeError, ValueError):
                continue
            word = (raw_word or "").lower()
            if num not in recs and num not in named_off:
                continue
            if (word == "above" and num < spot) or (word == "below" and num > spot):
                out.append(f"strike_side_contradicts_spot:{num:g}:{word}")
    for rx in (_TOUCH_COUNT_RE, _TOUCH_COUNT_REV_RE):
        for m in rx.finditer(text):
            try:
                k = float(m.group(1))
            except ValueError:
                continue
            said = _count_of(m.group(2))
            if k in named_off and said is not None:
                out.append(f"touch_count_off_the_table:{k:g}:{said}")
                continue
            visits = recs[k].get("visits_today") if k in recs else None
            if isinstance(visits, int) and said is not None and said != visits:
                out.append(f"touch_count_not_visits:{k:g}:{said}")
    for rx in (_STAY_RE, _STAY_REV_RE):
        for m in rx.finditer(text):
            try:
                k = float(m.group(1))
            except ValueError:
                continue
            visits = recs[k].get("visits_today") if k in recs else None
            if isinstance(visits, int) and visits > 1:
                out.append(f"stay_over_several_visits:{k:g}:{visits}")
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
            if k in named_off:
                out.append(f"leads_all_unsupported:{k:g}:off_the_table")
            continue
        # "every measure" means every measure the board SHOWS: with volume
        # withheld (item #8) there is no volume rank to fall short on
        shipped = set((scene.get("strikes") or {}).get("columns") or _RANK_COLS)
        short = [c for c in _RANK_COLS if c in shipped and not _leads_its_side(rec, recs, c)]
        if short:
            out.append("leads_all_unsupported:%g:%s" % (k, ",".join(
                c.replace("rank_by_", "").replace("_today", "") for c in short)))
    # review item #8, the last gap in it: on the session's first read the board
    # often carries NO volume at all — the morning's first book still holds the
    # prior session's counts, so the columns, the ranks and the series are all
    # withheld and `strikes.absent` says why. Nothing stopped the model saying
    # "1700 has taken the most volume today" on that board, because every guard
    # that grades a volume claim looks up a rank that is not there and finds
    # nothing to contradict. A volume word on a board with no volume is a claim
    # about a number the model was never shown.
    if _VOLUME_WORD_RE.search(text) and not {"vol_calls", "vol_puts", "rank_by_volume_today"} & set(
            (scene.get("strikes") or {}).get("columns") or ()):
        out.append("volume_claimed_on_a_board_with_none")
    for m in _MOST_RE.finditer(text):
        try:
            k = float(m.group(1))
        except ValueError:
            continue
        what = m.group(2).lower()
        led_before = (what in led_day and k in led_day[what]
                      and __import__("re").search(r"\bhad\b", m.group(0), __import__("re").I) is not None)
        if k not in recs:
            if k in named_off and not led_before:
                out.append(f"most_{what.replace(' ', '_')}_unsupported:{k:g}")
            continue
        col = {"volume": "rank_by_volume_today", "contracts": "rank_by_contracts",
               "open interest": None, "gamma": "rank_by_dealer_gamma"}[what]
        # review item #17: "the most open interest" was graded against
        # `rank_by_contracts`, which is open interest PLUS today's volume — a
        # mostly-volume number by lunchtime. Measured over 281 rebuilt boards
        # the two name different strikes on 104 (37 percent), on five of the
        # twelve recorded days: on 08-31 the guard would certify 1500 holding
        # "the most open interest" with 1,785 while 1440 held 1,987. Open
        # interest rides on every row, so the ranking is counted here instead
        # of shipped — no new column and no characters on the message.
        rank = ({kk: r.get(col) for kk, r in recs.items()} if col
                else _rank_by_open_interest(recs))
        leads_today = rank.get(k) == 1
        if not leads_today:
            # the doctrine names a heavy strike per side, so "above, 1600 holds
            # the most contracts" is a claim about the above side and is true
            # when 1600 out-ranks every other strike on that side
            side = recs[k].get("side")
            own = [rank.get(kk) for kk, r in recs.items()
                   if r.get("side") == side and isinstance(rank.get(kk), int)]
            leads_today = side in ("above", "below") and bool(own) and rank.get(k) == min(own)
        # a lead is a lead of something: with no volume on the board every
        # strike ties at zero, and the highest strike must not win that tie
        added = [(r.get("vol_added_in_series") or 0, kk) for kk, r in recs.items()]
        leads_series = bool(added) and max(added)[0] > 0 and max(added)[1] == k and what == "volume"
        chg = [((c[1] or 0) + (c[2] or 0) if isinstance(c := r.get("change"), list) and len(c) == 3 else 0, kk) for kk, r in recs.items()]
        leads_gap = bool(chg) and max(chg)[0] > 0 and max(chg)[1] == k and what == "volume"
        if not (leads_today or leads_series or leads_gap or led_before):
            out.append(f"most_{what.replace(' ', '_')}_unsupported:{k:g}")
    if _UNCHANGED_RE.search(text):
        moved = False
        for r in recs.values():
            ch = r.get("change")
            if isinstance(ch, list) and ch and isinstance(ch[0], (int, float)) and abs(ch[0]) >= 1.0:
                moved = True
        # review item #14 (strikes-3): THE VOL HALF IS GONE, NOT REPOINTED.
        # It was written against `between_frames.implied_vol` with a "from" and
        # a "to" — a key that shipped with the first Strikes Payload (eb2eb75),
        # was deleted in a09eae2, and has never existed in the era where this
        # function runs. So it read {} and answered "vol did not move" on every
        # call. Repointing it at the two fields that DO ship
        # (`scale.implied_vol_atm` and `between_frames.implied_vol_at_last_read`)
        # was tried in fadbcaa and is reverted here, because the quantity
        # cannot carry the claim: `atm_iv` is re-solved from the nearest
        # contract on EVERY scan, so the same options book re-served gives a
        # different answer every time. Measured over 636 consecutive scan pairs
        # that re-serve an identical `book_asof`: not one repeated its value,
        # the median move was 2.23 points, the 90th percentile 6.53 and the
        # largest 111.51 — 17.3 percent clear any 5-point bar with no new
        # market data at all. A prose slip nulls the WHOLE reading, so at that
        # bar roughly one reading in four using the word would have been
        # deleted on noise; both firings under the live last-read rule were
        # true sentences ("the heaviest strike is still 1500 and the call wall
        # is still 1550, both unchanged").
        # The codebase already knows this: `vol_trend` (sndk_read.py) reads a
        # 28-minute window, keeps a 2.5-point flat band, and REFUSES to read at
        # all on the weekly's expiry day, where a tau-to-zero reprice moved 59
        # points in half an hour. Any future vol half needs that shape — a
        # window, a regime guard, and a bar the doctrine states — not a
        # point-to-point difference. Until then the change cells carry this
        # check alone, and the doctrine's "and vol held" clause is a promise
        # nothing enforces (see review item #47).
        if moved:
            out.append("unchanged_contradicted_by_change_block")
    return out


def _guard_scene(scene: dict) -> dict:
    """The live guard finds nameable prices under `magnet.top_strikes`; the v2
    scene has no magnet, so hand it a shim carrying the listed strikes and the
    gap's low and high. The shim is for the guard only and never reaches the
    model."""
    shim = dict(scene)
    # strikes-4: the last call's sentence is quoted back to the model, and a
    # number in it is the model's own earlier word, not the board's. Walking it
    # would make every figure it once said sayable again after the board moved
    # on, so the gate is handed the frame without it.
    ctx = scene.get("context")
    slr = ctx.get("since_last_read") if isinstance(ctx, dict) else None
    if isinstance(slr, dict) and "said_then" in slr:
        shim["context"] = {**ctx, "since_last_read": {k: v for k, v in slr.items() if k != "said_then"}}
    # strikes-6: likewise the levels in `day.earlier_claims` — a claim graded
    # `off_list` names a level the board no longer carries (the old box edge
    # 1806.28), and walking it would make it sayable again. The rest of `day`
    # is measured today and stays.
    day = scene.get("day")
    if isinstance(day, dict) and "earlier_claims" in day:
        shim["day"] = {k: v for k, v in day.items() if k != "earlier_claims"}
    prices = [r["strike"] for r in rows_as_records(scene.get("strikes"))]
    bp = (scene.get("between_frames") or {}).get("price") or {}
    for k in ("low", "high"):
        v = SR._fin(bp.get(k))
        if v is not None:
            prices.append(v)
    # 2026-09-12: A BOX EDGE IS A PRICE ON THE BOARD. The doctrine tells the
    # model to say what a box did — "broke above the opening box at 10:07" —
    # and the boxes ship their edges, but the level guard only knew about
    # listed strikes and the gap's low and high. Measured on a live A/B, a
    # point naming 1691.80 (the top of the box that had just broken at 09-10
    # 13:06) was deleted as a level not on the board, in both arms.
    rg = (scene.get("context") or {}).get("ranges") or {}
    for box in (rg.get("in_force"), rg.get("opening")):
        for k in ("low", "high"):
            v = SR._fin((box or {}).get(k))
            if v is not None:
                prices.append(v)
    for b in ((rg.get("breaks_today") or {}).get("breaks") or []):
        for k in ("box_low", "box_high"):
            v = SR._fin((b or {}).get(k))
            if v is not None:
                prices.append(v)
    shim["magnet"] = {"top_strikes": [{"strike": k} for k in prices]}
    return shim


def _adjacent_on_list(ks: list, listed: list) -> bool:
    idx = {k: i for i, k in enumerate(sorted(listed))}
    order = sorted(idx[k] for k in ks)
    return all(b - a == 1 for a, b in zip(order, order[1:]))


def board_moved(scene: dict) -> list:
    """Why this board is NOT quiet, in the rulebook's own four terms, or [] when
    nothing moved: a change cell of a point or more, a level crossed since the
    last read, a lead that changed hands today, and an earlier claim the day
    block has graded as no longer holding. Read-only, and every term comes off
    the scene the model was handed, so the answer is the one it could have
    reached itself."""
    why = []
    recs = rows_as_records(scene.get("strikes"))
    if any(isinstance(r.get("change"), list) and r["change"]
           and isinstance(r["change"][0], (int, float)) and abs(r["change"][0]) >= 1.0
           for r in recs):
        why.append("a change cell moved a point or more")
    slr = (scene.get("context") or {}).get("since_last_read") or {}
    if slr.get("crossed_since_then"):
        why.append("a level was crossed since the last read")
    day = scene.get("day") or {}
    if any(len(runs) > 1 for runs in (day.get("leaders") or {}).values()):
        why.append("a lead changed hands today")
    if any(c.get("now") in ("changed", "off_list")
           for g in (day.get("earlier_claims") or []) for c in (g.get("claims") or [])):
        why.append("an earlier claim no longer holds")
    return why


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
    # eagle-view #11: the v2 prose rules are applied a sentence at a time too,
    # so a strike named on the wrong side no longer deletes the reading around it.
    parts = SR._sentences(reading.get("read") or "")
    v2_slips, survivors = [], []
    for part in parts:
        slips = _prose_slips_v2(part, scene)
        if slips:
            v2_slips += slips
        else:
            survivors.append(part)
    if v2_slips:
        kept_text = "".join(survivors).strip()
        reading["read"] = kept_text or None
        reading.setdefault("dropped_observations", [])
        reading["dropped_observations"] = list(reading["dropped_observations"]) + [f"read_{x}" for x in v2_slips]
        if len(survivors) != len(parts):
            reading["dropped_observations"].append(
                f"read_sentences_dropped:{len(parts) - len(survivors)}_of_{len(parts)}")
        if not kept_text and not reading.get("points"):
            reading["quiet"] = True
            reading["abstain"] = "forced"
    # strikes-7 (#9): THE QUIET CLAIM, CHECKED AGAINST THE BOARD. The rulebook
    # says a quiet board is one where the change cells are within a point,
    # nothing crossed since the last read, no lead changed and none of the
    # earlier claims stopped holding — and until now the model set `quiet`
    # itself with nothing testing it. A wrong claim is recorded, never deleted:
    # the sentence may still be true, and silently flipping the flag would
    # corrupt the one distinction the nightly audit is built on.
    moved = board_moved(scene)
    if reading.get("quiet") and moved:
        reading["notes"] = list(reading.get("notes") or []) + ["quiet_but_board_moved:" + ",".join(moved)]
    kept_points = []
    for p in (reading.get("points") or []):
        note = str(p.get("note") or "")
        # The touch guards key off a strike named IN the sentence, and a point's
        # note almost never repeats its own level — it says "touched twice
        # today" beside level 1775. So every touch check was dead exactly where
        # the model states touch facts. The note is checked twice: once as
        # written, and once with its own level in front so the touch and stay
        # rules have a strike to bind to. Only the touch verdicts are taken from
        # the second pass, so the borrowed number cannot invent a side slip or a
        # leadership claim the model never made.
        slips = _prose_slips_v2(note, scene)
        lvl = SR._fin(p.get("level"))
        if lvl is not None:
            for x in _prose_slips_v2(f"{_k(lvl)} {note}", scene):
                if ("touch" in x or "stay" in x or "visits" in x) and x not in slips:
                    slips.append(x)
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
    # 2026-09-10 (review item #5): THE CHECKER GRADES WITH WHAT THE MODEL WAS
    # SHOWN. A retraction is accepted when it names a strike the message itself
    # said has left the list, not only a centre from the hidden regions rule:
    # that list reached the model on about 1 read in 8, and 4 true retractions
    # in 23 replies were deleted against it. A strike still on the list is
    # still refused.
    hdr = scene.get("strikes") or {}
    left_now = {SR._fin(k) for k in (hdr.get("left_since_reference") or [])
                if SR._fin(k) is not None and SR._fin(k) not in recs}
    resolved_ok = {SR._fin(r.get("center"))
                   for r in (rule.get("resolved") or scene_rg.get("resolved") or [])
                   if isinstance(r, dict)} | left_now
    entered = {SR._fin(k) for k in (hdr.get("entered_since_reference") or []) if SR._fin(k) is not None}

    def cells_word(ks):
        """The change word the cluster's own change cells support, on the
        regions rule's one-point rail: summed contracts-share change of a point
        or more is increased or decreased, less is stable; a pile whose strikes
        all joined the list since the earlier book is new; no earlier book is
        unknown."""
        cells = [recs[k].get("change") for k in ks]
        if all(k in entered or c == "strike_not_in_earlier_book" for k, c in zip(ks, cells)):
            return "new"
        nums = [c[0] for c in cells if isinstance(c, list) and c and isinstance(c[0], (int, float))]
        if not nums:
            return "unknown"
        d = sum(nums)
        rail = sndk_regions.CHANGE_RAIL_PP
        return "increased" if d >= rail else "decreased" if d <= -rail else "stable"
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
        # item #5: the word comes from the change cells the model was shown.
        # It used to come from the hidden regions rule, which overwrote the
        # model's word on 63% of piles and forced "unknown" on a third; the
        # rule's word is kept beside it for the audit when the two differ.
        word = cells_word(ks)
        rec = recs[center]
        out = {"strikes": sorted(ks), "center": center, "rank": rank, "change": word,
               "side": rec.get("side"), "dist_sigma": rec.get("dist_sigma"),
               "touched_today": _touched(rec),
               "on_rule_region": on_region is not None}
        if model_word != word:
            out["change_model"] = model_word
        rule_word = on_region.get("change") if on_region else None
        if rule_word and rule_word != word:
            out["change_rule"] = rule_word
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
    # side and in the top three there on some rank. item #5: "that side" is
    # the table's own `side` column, the split the model reads. The live price
    # used to decide it here while the table used the book's price and an "at"
    # band, so a strike the table called "at" was counted below and true
    # leadership claims were deleted (6 in 23 replies). A strike marked "at"
    # is on neither side. Rows without a side fall back to the live price.
    # item #9: the table's side is now measured from the live price too, so
    # the two splits differ only by the "at" band.
    pr = scene.get("price") or {}
    spot = SR._fin(pr.get("live_spot"))
    if spot is None:
        spot = SR._fin(pr.get("spot_when_book_was_measured"))
    sides = {}
    model_sides = obj.get("sides") if isinstance(obj.get("sides"), dict) else {}
    table_sides = any(r.get("side") in ("above", "below", "at") for r in recs.values())
    if table_sides:
        splits = (("above", lambda k: recs[k].get("side") == "above"),
                  ("below", lambda k: recs[k].get("side") == "below"))
    elif spot is not None:
        splits = (("above", lambda k: k > spot), ("below", lambda k: k < spot))
    else:
        splits = ()
        sides = {"unavailable": "no_spot"}
    for name, keep in splits:
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
                entry["heavy_touched_today"] = _touched(recs[heavy])
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
    said_row = None
    calls = []
    pending = set(at or [])
    call_every_wake = call_model and not pending
    interrupts = 0
    for i, row in enumerate(rows):
        now = SR._ts(row)
        if now is None:
            continue
        rows_i = rows[:i + 1]
        row = SR.with_path(row, rows_i)
        wake = SR.should_wake(row, rows[i - 1] if i else None, last_call, now,
                              rows_i, interrupts_today=interrupts, bars=bars)
        if not wake:
            continue
        # the eval replays the SHIPPED gate, interrupt budget included, or its
        # cadence numbers describe a gate nobody runs
        _lt = SR._ts(last_call) if last_call else None
        if _lt is not None and (now - _lt).total_seconds() / 60.0 < SR.MIN_GAP_MIN:
            interrupts += 1
        frame = SR.frame_since_last_read(row, rows_i, last_call, wake, False, now,
                                         prior_rows_today=i > 0, bars=bars)
        v2, v1 = build_scene_v2(row, rows_i, now, frame, last_read_ts, bars,
                                strikes_sent_before=(last_call or {}).get("strikes_sent"),
                                sent_before_without_volume=bool((last_call or {}).get("strikes_sent_without_volume")),
                                said_row=said_row, calls_today=list(calls))
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
                # strikes-6: the reading, as the live read row keeps it, for `day`
                calls.append({"ts": row["ts"], "wall_s": w2, "spot": row.get("spot"),
                              "reading": rec["v2_reply"], "reading_ts": row["ts"]})
                # strikes-4: live shows the last sentence that survived, so replay
                # keeps it across a failed or emptied call the same way
                if rec["v2_reply"].get("read"):
                    said_row = {"reading": {"read": rec["v2_reply"]["read"]}, "reading_ts": now.isoformat()}
        out.append(rec)
        last_call = {"ts": row["ts"], "spot": row.get("spot"),
                     "magnet_band": SR.magnet_band(row),
                     "gate": SR.state_for_next_wake(row, v1, rows_i, now),
                     **({"strikes_sent": s} if (s := listed_strikes(v2.get("strikes"))) else {}),
                     **({"strikes_sent_without_volume": True}
                        if (row.get("meta") or {}).get("book_asof") in carried_books(rows_i, now) else {})}
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
