"""The final forecast for a read: the average of Claude's usable answers, with the flags code keeps beside it.

Two answers are asked per read (the cards shuffled one way, then reversed with the direction order
flipped), so order bias cancels in the average. Per horizon the final line carries each answer's
chances under the answer's own number, the largest gap between them (the noise floor), the base rate
Claude was shown, how far the final sits from it, and whether it merely leans the way the last thirty
minutes went. A horizon with no usable answer is ``rejected`` when an answer came back and failed the
checks, and ``failed`` when no answer came back at all (the statuses spec/record_formats.md lists).
"""
from __future__ import annotations

from .grading import SIZE_BUCKETS
from .rulebook import DIRECTION_KEYS

NEAR_BASE_RATE_PCT_POINTS = 3     # within this of the base rate on every direction, the answer "sat on the base rate"


def answer_number_of(answer_line: dict) -> str:
    """``answer_1`` from an answer id ending ``#answer_1`` (the answer's own number, whatever order it landed in), or
    ``#test:<variant>:answer_1`` on a test variant's answer."""
    return str(answer_line.get("answer_id", "")).rsplit("#", 1)[-1].rsplit(":", 1)[-1]


def final_forecast_for(answers: list[dict], asked_horizons: list[str], base_rate_pct: dict[str, dict] | None,
                       last_30_minutes_sig: float | None) -> dict:
    """``answers`` are the checked answer lines (each with ``checked_forecast`` and ``answer_id``)."""
    usable = [a for a in answers if isinstance(a.get("checked_forecast"), dict)]
    out = {"answer_ids": [a.get("answer_id") for a in answers], "usable_answer_count": 0,
           "any_answer_has_valid_reason": any((a.get("answer_checks") or {}).get("valid_reason_count", 0) > 0 for a in answers),
           "forecast": {}}
    counted = set()
    for horizon in asked_horizons:
        blocks = {answer_number_of(a): a["checked_forecast"].get(horizon) or {} for a in usable}
        oks = {name: b for name, b in blocks.items() if b.get("status") == "ok"}
        if not oks:
            rejected = any(b.get("status") == "rejected" for b in blocks.values())
            out["forecast"][horizon] = {"status": "rejected" if rejected else "failed"}
            continue
        counted.update(oks)
        block = {"status": "ok"}
        for key in DIRECTION_KEYS:
            block[key] = round(sum(b[key] for b in oks.values()) / len(oks), 1)
        buckets = [b["size_buckets_pct"] for b in oks.values() if isinstance(b.get("size_buckets_pct"), dict)]
        block["size_buckets_pct"] = ({k: round(sum(b[k] for b in buckets) / len(buckets), 1) for k in SIZE_BUCKETS}
                                     if buckets else None)
        for name, b in oks.items():
            block[name] = {k: b[k] for k in DIRECTION_KEYS}
        if len(oks) > 1:
            block["largest_gap_between_answers_pct_points"] = max(
                abs(a[k] - b[k]) for k in DIRECTION_KEYS for a in oks.values() for b in oks.values())
        base = (base_rate_pct or {}).get(horizon)
        if isinstance(base, dict) and all(k in base for k in DIRECTION_KEYS):
            block["base_rate_shown_to_claude"] = {k: base[k] for k in DIRECTION_KEYS}
            gap = max(abs(block[k] - base[k]) for k in DIRECTION_KEYS)
            block["largest_gap_from_base_rate_pct_points"] = round(gap, 1)
            block["is_within_3_pct_points_of_base_rate"] = gap <= NEAR_BASE_RATE_PCT_POINTS
            block["is_leaning_same_way_as_last_30_minutes_move_vs_base_rate"] = leans_with_last_30_minutes(
                block, base, last_30_minutes_sig)
        out["forecast"][horizon] = block
    out["usable_answer_count"] = len(counted)
    return out


def leans_with_last_30_minutes(block: dict, base: dict, last_30_minutes_sig: float | None) -> bool | None:
    """True when the forecast tilts, against the base rate, the same way the last 30 minutes moved."""
    if last_30_minutes_sig is None or last_30_minutes_sig == 0:
        return None
    tilt = (block["up_pct"] - base["up_pct"]) - (block["down_pct"] - base["down_pct"])
    if tilt == 0:
        return False
    return (tilt > 0) == (last_30_minutes_sig > 0)


def picked_precedents_among_code_nearest(answers: list[dict], nearest_ids: list[str], top: int = 3) -> int | None:
    """How many of the cards Claude picked (any answer) are among code's ``top`` nearest cards."""
    picked = {p.get("precedent_id") for a in answers for p in (a.get("checked_similar_precedents") or [])}
    if not picked:
        return None
    return len(picked & set(nearest_ids[:top]))
