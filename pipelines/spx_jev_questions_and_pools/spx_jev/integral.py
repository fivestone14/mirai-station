"""The average-price grade: a call graded on where price sat over its whole window, not on the one minute at its end.
(Its files and fields keep the name integral: integral_grades.jsonl, RULE_VERSION, the loop in integral_loop.py.)

For a read at spot P0 and a window of T one-minute bars (the bars grade.py reads for the horizon, from its
start to its mark, the last one being the mark bar):

    slices   x_k = close_k - P0, k = 1..T
    grade    G = (x_1 + ... + x_T) / T         the average price over the window against the read; the same as
                                              the minutes' moves d_j = x_j - x_(j-1) weighted d_j * (T - j + 1) / T
    edge     the call's flat zone in points (flat_zone.py), the one JEV was told; on a box without a call of its own,
             factor(T) * f, factor(T) = sqrt((T + 1)(2T + 1) / (6 T^2)), 0.5918 at T = 30 and 0.6205 at T = 10:
             f is the box's end-price zone, shrunk by how much less the average of a random walk spreads than its end
    label    up if G > edge, down if G < -edge, else flat (strict, as the end-price band)

Direction decides. A call is right when its side is the label (up_small and up_big are up), wrong when it is
not, and an unsure call is passed, never a miss, with the side it leaned. The margin says by how much: the
call's side of G over the edge, or for a flat call 1 - |G| / edge. Nothing weighs the late minutes more and
nothing boosts a sharp move; the biggest minute is a note, ranked against the same window on the recent
sessions ("top 2 of 21" is sharp), each minute's move taken close to close as the sessions' are (the
first from the bar before the window, when it is on file, since a read can land mid-minute). The running labels are the average so far against the same edge, one per
minute, the last one being the label. The strength (the phone's tier) ranks a right call's margin, as headroom past the
line that made it right, against the right calls on the box's recent sessions: Strong right, Right or Weak right by
third. While a window is open, so_far is its average over the minutes finished, against the same edge.

The guards
    missing bars   up to INTEGRAL_MISSING_BARS_MAX of the window's bars may be missing, never the mark bar, and a
                   missing one stands at the midpoint of its neighbours; with more, the window is not graded
    bad ticks      a close that jumps at least the BAD_TICK_PCT share of the same window's 1-minute moves on the
                   recent sessions, and that the next minute neither opens near (its open as far away) nor closes
                   near (its close jumping back as far), is replaced by the midpoint of the close before it and
                   the open after it, and the line records each one. A real move, a release minute's, opens the
                   next minute where it closed and is left alone
    stale read     a read whose spot no bar traded in the STALE_READ_MIN minutes up to its row minute is stale:
                   the line says so, and nothing that learns may read it
The ranks look at up to the last NIGHT_RANK_COUNT sessions of the same clock window and need
SAME_CLOCK_MIN_SESSIONS of them (labels.ranks.rank_sessions): with fewer, no tick is corrected and the
sharp-move note says the history is too short.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime

from .cuts import (BAD_TICK_PCT, INTEGRAL_MISSING_BARS_MAX, NIGHT_RANK_COUNT, SAME_CLOCK_MIN_SESSIONS, SHARP_MOVE_TOP,
                   STALE_READ_MIN, TIER_MIN_RIGHT_CALLS)
from .labels.measures import ET, ONE_MINUTE, bar_time
from .labels.ranks import percentile, rank_sessions
from .labels.words import third

# bump when the grade's rule changes: a line is keyed by its read, its horizon and this. 4: the edge is the flat zone sized at
# the read (flat_zone.py), not a share of the morning sigma anchor; 3: the average-price sum's call is set against the edge
# JEV was told; 2: the box a lane's average-price sum forecasts is graded on that sum's call when it answered, with its
# scores (grade.integral_line); 1 graded the end-price sum's
RULE_VERSION = 4
READABLE_VERSIONS = (1, 2, 3, 4)   # the rules whose lines still read, a read's newest standing: 1's grade the end-price sum's call,
                                   # as the later ones do where there is no other
NOT_GRADED = "not graded: bars missing"
TIERS = {"top": "Strong right", "middle": "Right", "bottom": "Weak right"}   # a right call's headroom by its third


def factor(minutes: int) -> float:
    """How much narrower the average of a ``minutes``-step random walk spreads than its end: the flat zone's share."""
    return math.sqrt((minutes + 1) * (2 * minutes + 1) / (6 * minutes ** 2))


def label(g: float, edge: float) -> str:
    return "up" if g > edge else "down" if g < -edge else "flat"


def direction(pick) -> str | None:
    """The side a call takes: up_small and up_big are up, down_small and down_big down; unsure, or no pick, takes none."""
    if not isinstance(pick, str) or pick == "unsure":
        return None
    return pick.split("_")[0]


def verdict(pick, lab: str) -> str:
    side = direction(pick)
    return "passed" if side is None else "right" if side == lab else "wrong"


def margin(pick, g: float, edge: float) -> float | None:
    """The call's signed margin: its side of G over the edge, or 1 - |G| / edge for a flat call; None when passed."""
    side = direction(pick)
    if side is None:
        return None
    return round(1 - abs(g) / edge if side == "flat" else (g if side == "up" else -g) / edge, 3)


def headroom(line: dict) -> float | None:
    """How far a graded call cleared the line that decides it, in edges: a flat call's margin (from the edge in to the
    read's price), an up or down call's margin less the edge it had to pass. Below zero it was wrong; None when it
    was passed. The one scale a flat call and a sided one rank on together, since a sided call's margin passes 1
    where it turns right and a flat call's passes 0."""
    m = line.get("margin")
    if m is None:
        return None
    return m if line.get("direction") == "flat" else m - 1


def strength(line: dict, base: list[list[float]]) -> str | None:
    """The tier of a graded call: Wrong when it was wrong, else where its headroom ranks among the right calls' on
    the box's recent sessions (``base``, each session's right calls' headrooms, newest first), by third: Strong
    right, Right or Weak right. None for a passed call, and for every call while fewer than SAME_CLOCK_MIN_SESSIONS
    of the last NIGHT_RANK_COUNT sessions are on file or they hold fewer than TIER_MIN_RIGHT_CALLS right calls."""
    recent = base[:NIGHT_RANK_COUNT]
    pool = [h for session in recent for h in session]
    if len(recent) < SAME_CLOCK_MIN_SESSIONS or len(pool) < TIER_MIN_RIGHT_CALLS or line.get("verdict") == "passed":
        return None
    if line.get("verdict") == "wrong":
        return "Wrong"
    return TIERS[third(sum(1 for h in pool if h < headroom(line)) / len(pool))]


def so_far(bars: list[dict], t0: datetime, minutes: int, spot: float, flat: float, edge: float | None = None) -> dict | None:
    """An open window's average so far: its finished minutes from ``t0`` up to the first one not on file, against
    the whole window's edge (``edge`` when the call was told one, else ``flat`` narrowed by factor), as the running
    labels are, and the minute it stands at (``as_of``, the last one's end). None before its first minute has finished."""
    closes = []
    for bar in window(bars, t0, minutes):
        if bar is None:
            break
        closes.append(float(bar["close"]))
    if not closes:
        return None
    edge, g = edge or factor(minutes) * flat, sum(c - spot for c in closes) / len(closes)
    return {"g": round(g, 2), "edge": round(edge, 2), "label": label(g, edge), "minutes": len(closes), "of": minutes,
            "as_of": (t0 + len(closes) * ONE_MINUTE).isoformat()}


def lean(probs: dict) -> dict | None:
    """Where a passed call leaned: its odds summed by side, the unsure mass left out."""
    sides: Counter = Counter()
    for k, p in probs.items():
        if direction(k) and isinstance(p, (int, float)):
            sides[direction(k)] += float(p)
    if not sides:
        return None
    side = max(sides, key=sides.get)
    return {"direction": side, "p": round(sides[side], 3)}


def window(bars: list[dict], t0: datetime, minutes: int) -> list[dict | None]:
    """One slot per minute from ``t0``: the bar that started that minute, or None where there is none."""
    by = {bar_time(b): b for b in bars}
    return [by.get(t0 + k * ONE_MINUTE) for k in range(minutes)]


def same_window_moves(prior: dict[str, list[dict]], t0: datetime, minutes: int) -> list[list[float]]:
    """Each prior session's 1-minute moves, close to close and in size, over the same clock window (from the bar
    before its first to its mark bar, wherever both minutes are on file), in ``prior``'s order: newest first."""
    out = []
    clock = t0.astimezone(ET).time()
    for d, bars in prior.items():
        slots = window(bars, datetime.combine(date.fromisoformat(d), clock, tzinfo=ET) - ONE_MINUTE, minutes + 1)
        moves = [abs(float(b["close"]) - float(a["close"])) for a, b in zip(slots, slots[1:]) if a and b]
        if moves:
            out.append(moves)
    return out


def bad_tick_cut(base: list[list[float]]) -> float | None:
    """The BAD_TICK_PCT share of the recent sessions' moves over the window; None with too few sessions."""
    recent = base[:NIGHT_RANK_COUNT]
    if len(recent) < SAME_CLOCK_MIN_SESSIONS:
        return None
    return percentile([m for moves in recent for m in moves], BAD_TICK_PCT)


def fix_bad_ticks(slots: list[dict | None], before: dict | None, cut: float | None) -> tuple[list[float | None], list[dict]]:
    """The window's closes, None where a bar is missing, with each bad tick replaced, and the ticks replaced: a close
    ``cut`` or more from the close before it and from the next minute's open, the next close jumping back ``cut`` or
    more. A tick needs both neighbours on file, so the mark bar, which has none after it inside the window, is never one."""
    closes = [float(b["close"]) if b else None for b in slots]
    ticks = []
    if not cut:
        return closes, ticks
    for k in range(len(slots) - 1):
        prev, bar, nxt = slots[k - 1] if k else before, slots[k], slots[k + 1]
        if not (prev and bar and nxt):
            continue
        out, back = float(bar["close"]) - float(prev["close"]), float(nxt["close"]) - float(bar["close"])
        # the next minute opening near the close traded there: a real move, however fast it came back
        opened_away = abs(float(nxt["open"]) - float(bar["close"])) >= cut
        if abs(out) >= cut and abs(back) >= cut and out * back < 0 and opened_away:
            closes[k] = round((float(prev["close"]) + float(nxt["open"])) / 2, 2)
            ticks.append({"minute": k + 1, "ts": bar["ts"], "close": float(bar["close"]), "replaced_by": closes[k]})
    return closes, ticks


def stale_read(bars: list[dict], t0: datetime, spot: float) -> bool | None:
    """True when no bar of the STALE_READ_MIN minutes up to the row minute traded the read's spot; None with none on file."""
    near = [b for b in bars if t0 - STALE_READ_MIN * ONE_MINUTE <= bar_time(b) <= t0]
    if not near:
        return None
    return not any(float(b["low"]) <= spot <= float(b["high"]) for b in near)


def sharp_move(closes: list[float], first_from: float, base: list[list[float]]) -> dict:
    """The window's biggest minute, close to close from ``first_from`` (the close before the window), against the
    biggest in the same window on the recent sessions (rank_sessions)."""
    d = [c - p for p, c in zip([first_from, *closes], closes)]
    k = max(range(len(d)), key=lambda i: abs(d[i]))
    out = {"points": round(d[k], 2), "minute": k + 1}
    rank, why = rank_sessions(abs(d[k]), [max(moves) for moves in base], "this window's minutes on file")
    if rank is None:
        return {**out, "sharp": None, "note": f"too little history to rank the biggest minute: {why}"}
    sharp = rank.of - rank.higher_than < SHARP_MOVE_TOP
    return {**out, "higher_than": rank.higher_than, "of": rank.of, "sharp": sharp,
            "note": f"{'a sharp move' if sharp else 'no sharp move'}: the biggest minute, {d[k]:+.2f} at minute {k + 1}, was bigger "
                    f"than the biggest in this window on {rank.higher_than} of the last {rank.of} sessions"}


def grade_window(bars: list[dict], prior: dict[str, list[dict]], t0: datetime, minutes: int, spot: float, flat: float,
                 pick, probs: dict | None = None, edge: float | None = None) -> dict:
    """The average-price grade of a call at ``spot`` over the ``minutes`` bars from ``t0``, against the box's flat zone ``flat``
    in points narrowed by factor, or against ``edge`` itself when the call was told one (its own zone, rounded as it was
    told), with ``prior`` the recent sessions' bars (newest first) the ranks read. A ``graded:
    False`` result names the minutes missing; its guard fields are filled all the same."""
    slots, before = window(bars, t0, minutes), window(bars, t0 - ONE_MINUTE, 1)[0]
    base = same_window_moves(prior, t0, minutes)
    closes, ticks = fix_bad_ticks(slots, before, bad_tick_cut(base))
    missing = [k + 1 for k, c in enumerate(closes) if c is None]
    guards = {"bad_ticks": ticks, "stale_read": stale_read(bars, t0, spot)}
    if closes[-1] is None or len(missing) > INTEGRAL_MISSING_BARS_MAX:
        return {"graded": False, "reason": NOT_GRADED, "missing": missing, **guards}
    for k in missing:
        closes[k - 1] = ((closes[k - 2] if k > 1 else spot) + closes[k]) / 2
    x = [c - spot for c in closes]
    share = factor(minutes)
    edge, g = edge or share * flat, sum(x) / minutes
    lab = label(g, edge)
    running, total = [], 0.0
    for k, v in enumerate(x, start=1):
        total += v
        running.append(label(total / k, edge))
    best, worst = max(range(minutes), key=x.__getitem__), min(range(minutes), key=x.__getitem__)
    out = {"graded": True, "minutes": minutes, "from": spot, "f": round(flat, 4), "factor": round(share, 4), "edge": round(edge, 2),
           "g": round(g, 2), "label": lab, "pick": pick, "direction": direction(pick), "verdict": verdict(pick, lab),
           "margin": margin(pick, g, edge), "running": running,
           "best": {"points": round(x[best], 2), "minute": best + 1}, "worst": {"points": round(x[worst], 2), "minute": worst + 1},
           "sharp_move": sharp_move(closes, float(before["close"]) if before else spot, base), "filled": missing, **guards}
    if out["verdict"] == "passed":
        out["lean"] = lean(probs or {})
    return out
