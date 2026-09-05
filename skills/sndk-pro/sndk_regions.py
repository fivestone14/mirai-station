"""sndk_regions.py — the REGIONS block (regions-1): a stated rule's answer to
"where are the piles", run at read time over the day's distinct books.

Design and measurements: the Regions review of 2026-09-05 (vault note "Mirai
Awakening — Board Imaging Model", section Regions). The rule, in full:

  1. Window and shares are the Strikes Payload's own (sndk_board._window,
     _shares): strikes within STRIKE_WINDOW_SIGMA of the book's spot, contracts
     share and absolute dealer-gamma share over that window.
  2. Membership: a LISTED strike holding at least ENTER_PP of the contracts in
     reach, or ENTER_PP of the absolute dealer gamma in reach, is a member.
  3. Hysteresis: a member at the previous distinct book stays while it holds
     EXIT_PP on either measure; it leaves under EXIT_PP on both.
  4. Adjacency: two members are one region when no strike of the window sits
     between them. The rule reads the grid; it does not assume $5.
  5. Center: the member with the largest contracts share (largest gamma share
     for a region that qualified on gamma only).
  6. Identity across books: a region at one book is the same region at the
     next when the two share a strike. first_seen is the earliest book of the
     unbroken chain walking back from now.
  7. Reference book: the same one the Strikes Payload's change block uses.
  8. Words: new (no overlapping region at the reference), resolved (a region at
     the reference with no overlap now), and increased / decreased / stable
     from a straight-line fit of the region's contracts share over the series,
     measured against the set of window strikes EVERY book of the series
     carries, so a window sliding with price cannot read as trading. The fit
     moved at least CHANGE_RAIL_PP up, down, or neither.

Why a function at read time and not a job: measured, parsing and sharing all
95 books of a day takes about 60 ms and the chain 1 ms; a separate job would
be a second store that must agree with the diary, and a dead job leaves a
stale memo in the prompt looking current, which is the worst failure class
this project has had (the Jul 21 dead-flow week).

The block carries NO number from the strike list: only strike identities,
two counts, and times derived from book_asof. Everything about a region's
weight lives in strikes.rows and is never repeated here.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import sndk_read as SR  # noqa: E402

RULE = "regions-1"
ENTER_PP = 8.0
EXIT_PP = 6.0
SERIES_BOOKS = 12
CHANGE_RAIL_PP = 1.0
CHANGE_WORDS = ("new", "increased", "decreased", "stable", "unknown")


def _hhmm(t: Optional[datetime]) -> Optional[str]:
    return t.astimezone(SR._ET).strftime("%H:%M") if t else None


def frame(row: dict, board) -> Optional[dict]:
    """One distinct book, measured with the Strikes Payload's own rulers.
    `board` is the sndk_board module (passed in to avoid a circular import)."""
    rs, sig, spot, _ = board._ruler(row)
    surf = board.surfaces(row)
    win = board._window(surf, rs, sig)
    if not win:
        return None
    return {"asof": SR._book_asof(row) or SR._ts(row), "win": win,
            "shares": board._shares(surf, win),
            "listed": board.select_strikes(surf, win, rs),
            "contracts": surf["contracts"]}


def _members(fr: dict, prev: set) -> set:
    out = set()
    for k in fr["listed"]:
        c = fr["shares"][k]["contracts_share_pp"]
        g = fr["shares"][k]["dealer_gamma_share_pp"]
        thr = EXIT_PP if k in prev else ENTER_PP
        if (c is not None and c >= thr) or (g is not None and g >= thr):
            out.add(k)
    return out


def _group(members: set, grid: list) -> list:
    idx = {k: i for i, k in enumerate(grid)}
    regs, cur = [], []
    for k in sorted(members):
        if cur and idx[k] - idx[cur[-1]] == 1:
            cur.append(k)
        else:
            if cur:
                regs.append(cur)
            cur = [k]
    if cur:
        regs.append(cur)
    return regs


def _by(fr: dict, ks: list) -> str:
    c = any((fr["shares"][k]["contracts_share_pp"] or 0) >= ENTER_PP for k in ks)
    g = any((fr["shares"][k]["dealer_gamma_share_pp"] or 0) >= ENTER_PP for k in ks)
    return "both" if c and g else "contracts" if c else "gamma" if g else "held"


def _center(fr: dict, ks: list, by: str) -> float:
    key = "dealer_gamma_share_pp" if by == "gamma" else "contracts_share_pp"
    return max(ks, key=lambda k: ((fr["shares"][k][key] or -1), k))


def regions_by_book(frames: list) -> list:
    """The rule's answer at every distinct book of the day, in order. A None
    frame (no window) resets the hysteresis chain, as an outage should."""
    prev, out = set(), []
    for fr in frames:
        if fr is None:
            out.append(None)
            prev = set()
            continue
        m = _members(fr, prev)
        regs = []
        for ks in _group(m, fr["win"]):
            by = _by(fr, ks)
            regs.append({"strikes": ks, "center": _center(fr, ks, by), "by": by})
        out.append(regs)
        prev = m
    return out


def _overlap(a: dict, b: dict) -> bool:
    return bool(set(a["strikes"]) & set(b["strikes"]))


def _chain_back(regs_by_book: list, i: int, reg: dict) -> int:
    j, cur = i, reg
    while j > 0:
        m = [q for q in (regs_by_book[j - 1] or []) if _overlap(cur, q)]
        if not m:
            break
        cur, j = m[0], j - 1
    return j


def _chain_forward(regs_by_book: list, i_ref: int, reg: dict, i_now: int) -> int:
    j, cur = i_ref, reg
    while j < i_now:
        m = [q for q in (regs_by_book[j + 1] or []) if _overlap(cur, q)]
        if not m:
            break
        cur, j = m[0], j + 1
    return j


def _fixed_share(fr: dict, strikes: list, fixed: list) -> Optional[float]:
    tot = sum(fr["contracts"].get(k, 0.0) for k in fixed)
    if tot <= 0:
        return None
    return sum(fr["contracts"].get(k, 0.0) for k in strikes) / tot * 100.0


def _fitted_change(series: list) -> Optional[float]:
    pts = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(i for i, _ in pts) / n
    my = sum(v for _, v in pts) / n
    sxx = sum((i - mx) ** 2 for i, _ in pts)
    if sxx == 0:
        return None
    slope = sum((i - mx) * (v - my) for i, v in pts) / sxx
    return slope * (pts[-1][0] - pts[0][0])


def _word(delta: Optional[float]) -> str:
    if delta is None:
        return "unknown"
    return ("increased" if delta >= CHANGE_RAIL_PP else
            "decreased" if delta <= -CHANGE_RAIL_PP else "stable")


def regions_block(rows: list, now: datetime, ref_row: Optional[dict],
                  listed: list, board) -> Optional[dict]:
    """The block for the newest distinct book in `rows`, or None when the day
    has no measurable book. `ref_row` is the change block's reference book
    (None when there is none). `listed` is the Strikes Payload's listed
    strikes at this book; every region strike is one of them by construction
    (the rule only admits listed strikes) and this is re-checked."""
    books = SR._distinct_books_rows(rows)
    if not books:
        return None
    frames = [frame(r, board) for r in books]
    i_now = len(frames) - 1
    if frames[i_now] is None:
        return None
    rb = regions_by_book(frames)
    lo = max(0, i_now - SERIES_BOOKS + 1)
    series_idx = list(range(lo, i_now + 1))
    n_books = len(series_idx)
    first_book = next((fr["asof"] for fr in frames if fr), None)

    # the reference book's index, when there is one
    i_ref = None
    if ref_row is not None:
        ref_asof = SR._book_asof(ref_row) or SR._ts(ref_row)
        for i, fr in enumerate(frames):
            if fr and fr["asof"] <= ref_asof:
                i_ref = i
    if i_ref is not None and i_ref >= i_now:
        i_ref = None   # the reference must lie before this book

    # the fixed strike set every series book carries, for drift-free change
    fixed = None
    for i in series_idx:
        fr = frames[i]
        if fr is None:
            continue
        fixed = set(fr["win"]) if fixed is None else fixed & set(fr["win"])
    fixed = sorted(fixed or [])
    win_now = frames[i_now]["win"]

    listed_set = set(listed or [])
    regions, absent = [], ["next expiry's book", "books before today's open"]
    if fixed and win_now and len(fixed) < 0.5 * len(win_now):
        absent.append("change words rest on fewer than half the strikes in reach")
    for reg in (rb[i_now] or []):
        if listed_set and not set(reg["strikes"]) <= listed_set:
            continue   # never name a strike the model cannot see
        j0 = _chain_back(rb, i_now, reg)
        present = sum(1 for i in series_idx if any(_overlap(reg, q) for q in (rb[i] or [])))
        out = {"strikes": [float(k) for k in reg["strikes"]], "center": float(reg["center"]),
               "by": reg["by"], "first_seen": _hhmm(frames[j0]["asof"]) if frames[j0] else None,
               "present": present}
        if i_ref is not None:
            at_ref = any(_overlap(reg, q) for q in (rb[i_ref] or []))
            if not at_ref:
                out["change"] = "new"
            else:
                series = [(_fixed_share(frames[i], reg["strikes"], fixed) if frames[i] else None)
                          for i in series_idx]
                out["change"] = _word(_fitted_change(series))
        regions.append(out)

    resolved: Optional[list] = None
    if i_ref is not None:
        resolved = []
        for reg in (rb[i_ref] or []):
            if any(_overlap(reg, q) for q in (rb[i_now] or [])):
                continue
            j_last = _chain_forward(rb, i_ref, reg, i_now)
            j_first = _chain_back(rb, i_ref, reg)
            resolved.append({"center": float(reg["center"]),
                             "first_seen": _hhmm(frames[j_first]["asof"]) if frames[j_first] else None,
                             "last_seen": _hhmm(frames[j_last]["asof"]) if frames[j_last] else None})

    block = {"rule": RULE, "books": n_books, "first_book": _hhmm(first_book),
             "regions": regions, "resolved": resolved, "absent": absent}
    if i_ref is None:
        block["reference_unavailable"] = "no_earlier_book"
    if not regions:
        block["none_at_threshold"] = True
    return block
