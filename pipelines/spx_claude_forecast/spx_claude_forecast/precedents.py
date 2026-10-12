"""The history Claude sees at a read: the base rate for this time of day, and the 8 past moments most like now.

Both come from the library (``library.py``) and only from sessions BEFORE the read's day, so nothing from
today, and nothing whose outcome was not sealed, can reach the prompt.

Base rate: how this half-hour ended on earlier sessions, pooled with the half-hours on either side
(±30 minutes beat the exact slot in every design test), one vote per session, all history, no fading;
under BASE_RATE_MIN_SESSIONS it falls back to the exact slot and says so.

Precedents: first a gate, then a score. The gate keeps past reads within ±45 minutes of now's time of day
with a clean ruler and a sealed 30-minute outcome. The score is a root-mean-square distance over the seven
everyday items every payload carries (gap, move since the prior close, last 30 minutes, range, position in
the range, distance from VWAP, VIX term ratio), each difference divided by that item's spread over the pool,
so no single item dominates. No embeddings, no text similarity, no event matching (Will, 2026-10-09). The
nearest 8 win, at most one per session. Each card shows those seven items and what happened next; never a
date, a price level, or Claude's own past call.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field

from .grading import DIRECTIONS, SIZE_BUCKETS, direction_of
from .library import FINGERPRINT_FIELDS, HORIZONS, sessions_before

BASE_RATE_POOL_MINUTES = 30          # the half-hours on either side of the slot are pooled in
BASE_RATE_MIN_SESSIONS = 15          # under this the pool is too thin: exact slot only, flagged
PRECEDENT_CLOCK_WINDOW_MINUTES = 45  # a past read counts only within this of now's time of day
PRECEDENT_COUNT = 8
PRECEDENTS_PER_SESSION_MAX = 1       # one card per session, so a single day can never dominate
PRECEDENT_MIN_SHARED_FIELDS = 5      # a pair is scored only when at least this many items exist on both sides
PRECEDENT_MIN_SPREAD_FIELDS = 3      # and the pool must spread on at least this many items, or nothing is "near"
PRECEDENT_MIN_POOL = 5               # fewer qualifying past reads than this and the cards are left out
CARD_DECIMALS = 2


@dataclass
class HistoryForRead:
    """What the payload builder puts in the scene: the base-rate block, the precedent cards, and the record of
    how they were picked (written to the payload line, never shown to Claude)."""

    base_rate: dict | None
    cards: list[dict]
    precedent_set: list[dict] = field(default_factory=list)
    picker: dict = field(default_factory=dict)
    absent: list[dict] = field(default_factory=list)


# --- the base rate ------------------------------------------------------------------------------------------------

def base_rate_for(rows: list[dict], slot_minute: int, before_day: str) -> dict | None:
    """Counts per size bucket per horizon for this time of day on sessions before ``before_day``, one vote per
    session. None when no session qualifies."""
    pool = [r for r in rows if r.get("is_eligible_base_rate") and r.get("trading_day") and r["trading_day"] < before_day
            and r.get("slot_minute_of_day") is not None]
    near = [r for r in pool if abs(r["slot_minute_of_day"] - slot_minute) <= BASE_RATE_POOL_MINUTES]
    sessions = {r["trading_day"] for r in near}
    pooled = True
    if len(sessions) < BASE_RATE_MIN_SESSIONS:
        near = [r for r in pool if r["slot_minute_of_day"] == slot_minute]
        sessions = {r["trading_day"] for r in near}
        pooled = False
    if not sessions:
        return None
    out = {"slot_minute_of_day": slot_minute, "sessions": len(sessions), "pooled_minutes": BASE_RATE_POOL_MINUTES if pooled else 0,
           "bucket_order": list(SIZE_BUCKETS)}
    for horizon in HORIZONS:
        votes = {b: 0.0 for b in SIZE_BUCKETS}
        per_session: dict[str, list[str]] = {}
        for r in near:
            if r.get(f"{horizon}_status") == "final" and r.get(f"{horizon}_size_bucket") in votes:
                per_session.setdefault(r["trading_day"], []).append(r[f"{horizon}_size_bucket"])
        for buckets in per_session.values():
            for b in buckets:
                votes[b] += 1.0 / len(buckets)
        counts = apportion([votes[b] for b in SIZE_BUCKETS], len(per_session))
        out[horizon] = {"counts": counts, "sessions": len(per_session), **three_way_pct(counts)}
    return out


def apportion(votes: list[float], total: int) -> list[int]:
    """Whole-number counts that sum to ``total`` from fractional votes (largest remainder), so a session split
    across the pooled half-hours still counts exactly once."""
    if total <= 0 or sum(votes) <= 0:
        return [0] * len(votes)
    scaled = [v * total / sum(votes) for v in votes]
    counts = [int(x) for x in scaled]
    for i in sorted(range(len(votes)), key=lambda k: scaled[k] - counts[k], reverse=True)[: total - sum(counts)]:
        counts[i] += 1
    return counts


def three_way_pct(counts: list[int]) -> dict[str, float]:
    """up / flat / down percentages from seven bucket counts, exactly as counted (the scorer floors a chance at
    2 percent; smoothing here would pull the reference Claude is scored against toward even and flatter its skill)."""
    total = sum(counts)
    if total <= 0:
        return {"up_pct": 0.0, "flat_pct": 0.0, "down_pct": 0.0}
    down, flat, up = sum(counts[:3]), counts[3], sum(counts[4:])
    return {"up_pct": round(100 * up / total, 1), "flat_pct": round(100 * flat / total, 1), "down_pct": round(100 * down / total, 1)}


# --- the precedents ------------------------------------------------------------------------------------------------

def precedent_pool(rows: list[dict], slot_minute: int, before_day: str) -> list[dict]:
    return [r for r in rows if r.get("is_eligible_precedent") and r.get("trading_day") and r["trading_day"] < before_day
            and r.get("slot_minute_of_day") is not None
            and abs(r["slot_minute_of_day"] - slot_minute) <= PRECEDENT_CLOCK_WINDOW_MINUTES]


def field_spreads(pool: list[dict]) -> dict[str, float]:
    """Each fingerprint item's population standard deviation over the pool (the scaler); an item with no spread
    is left out of the distance."""
    spreads = {}
    for name in FINGERPRINT_FIELDS:
        values = [float(r[name]) for r in pool if isinstance(r.get(name), (int, float))]
        if len(values) >= 2:
            sd = statistics.pstdev(values)
            if sd > 0:
                spreads[name] = sd
    return spreads


def distance(now: dict, past: dict, spreads: dict[str, float]) -> tuple[float | None, int]:
    """Root-mean-square of the standardized differences over the items both sides carry; None when too few."""
    terms = []
    for name, sd in spreads.items():
        a, b = now.get(name), past.get(name)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            terms.append(((float(a) - float(b)) / sd) ** 2)
    if len(terms) < min(PRECEDENT_MIN_SHARED_FIELDS, len(spreads)) or len(spreads) < PRECEDENT_MIN_SPREAD_FIELDS:
        return None, len(terms)
    return math.sqrt(sum(terms) / len(terms)), len(terms)


def pick_precedents(rows: list[dict], now_fingerprint: dict, slot_minute: int, before_day: str,
                    count: int = PRECEDENT_COUNT) -> tuple[list[dict], dict]:
    """The nearest past reads (``count``, at most one per session) and the picker's record."""
    pool = precedent_pool(rows, slot_minute, before_day)
    spreads = field_spreads(pool)
    scored = []
    for r in pool:
        d, shared = distance(now_fingerprint, r, spreads)
        if d is not None:
            scored.append((d, r["trading_day"], shared, r))
    scored.sort(key=lambda t: (round(t[0], 6), -_day_number(t[1])))          # nearest first, newer day breaks ties
    picked, per_session = [], {}
    for d, day, shared, r in scored:
        if per_session.get(day, 0) >= PRECEDENTS_PER_SESSION_MAX:
            continue
        per_session[day] = per_session.get(day, 0) + 1
        picked.append({"read_id": r["read_id"], "trading_day": day, "distance": round(d, 4), "shared_fields": shared, "row": r})
        if len(picked) == count:
            break
    record = {"rule": "time of day within 45 min, then RMS distance over the seven everyday items, one per session",
              "candidates": len(pool), "candidate_sessions": len({r["trading_day"] for r in pool}),
              "scaler": {k: round(v, 4) for k, v in spreads.items()}, "picked": len(picked)}
    return picked, record


def _day_number(day: str) -> int:
    return int(day.replace("-", ""))


def card_for(picked: dict, earlier_sessions: list[str]) -> dict:
    """A card as Claude sees it: how long ago in sessions (counted over the library's sessions before the read's
    day), the slot, the seven items, and what happened next."""
    r = picked["row"]
    ago = sum(1 for d in earlier_sessions if d > r["trading_day"]) + 1
    card = {"ago_sessions": ago, "at": r.get("half_hour_slot_et"), "src": r.get("origin")}
    for name in FINGERPRINT_FIELDS:
        if isinstance(r.get(name), (int, float)):
            card[name] = round(float(r[name]), CARD_DECIMALS)
    outcome = {}
    for horizon in HORIZONS:
        if r.get(f"{horizon}_status") == "final" and isinstance(r.get(f"{horizon}_move_flat_edges"), (int, float)):
            outcome[horizon] = round(float(r[f"{horizon}_move_flat_edges"]), CARD_DECIMALS)
    card["outcome_flat_edges"] = outcome
    content = json.dumps(card, sort_keys=True, separators=(",", ":"))
    return {"id": "p" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:5], **card}


def history_for_read(rows: list[dict], now_fingerprint: dict, slot_minute: int, before_day: str) -> HistoryForRead:
    """Everything the payload builder needs from history for one read."""
    base = base_rate_for(rows, slot_minute, before_day)
    picked, record = pick_precedents(rows, now_fingerprint, slot_minute, before_day)
    sessions = sessions_before(rows, before_day)
    history = HistoryForRead(base_rate=base, cards=[], picker=record)
    if base is None:
        history.absent.append({"path": "base_rate", "why": "no_sessions_before_the_day"})
    if len(picked) < PRECEDENT_MIN_POOL:
        history.absent.append({"path": "precedents", "why": f"fewer_than_{PRECEDENT_MIN_POOL}_qualifying_past_reads"})
        history.picker["left_out"] = True
        return history
    cards = []
    for p in picked:
        card = card_for(p, sessions)
        cards.append(card)
        history.precedent_set.append({"card_id": card["id"], "read_id": p["read_id"], "trading_day": p["trading_day"],
                                      "distance": p["distance"], "shared_fields": p["shared_fields"]})
    history.cards = cards
    return history


def shown_precedents_outcomes(cards: list[dict], base_counts: list[int] | None, horizon: str, pretend_reads: int = 20) -> dict | None:
    """The cards' own outcomes as up / flat / down percentages, blended with the base rate as if it were
    ``pretend_reads`` more cards; the comparison forecast the outcome line keeps beside Claude's."""
    if not cards:
        return None
    tally = {d: 0.0 for d in DIRECTIONS}
    shown = 0
    for card in cards:
        edges = (card.get("outcome_flat_edges") or {}).get(horizon)
        if isinstance(edges, (int, float)):
            tally[direction_of(edges)] += 1
            shown += 1
    if not shown:
        return None
    base = three_way_pct(base_counts) if base_counts else {"up_pct": 100 / 3, "flat_pct": 100 / 3, "down_pct": 100 / 3}
    out = {}
    for d in DIRECTIONS:
        blended = (tally[d] + pretend_reads * base[f"{d}_pct"] / 100) / (shown + pretend_reads)
        out[f"{d}_pct"] = round(100 * blended, 1)
    return {**out, "precedent_count": shown, "base_rate_blended_in_as_precedent_count": pretend_reads}


__all__ = ["HistoryForRead", "base_rate_for", "three_way_pct", "pick_precedents", "card_for", "history_for_read",
           "shown_precedents_outcomes", "PRECEDENT_COUNT", "BASE_RATE_POOL_MINUTES"]
