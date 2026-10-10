"""Code's checks on one Claude answer: delete what fails, never rewrite it, and say what was done.

The rules, from spec/record_formats.md:
- up_pct + flat_pct + down_pct off 100 by at most 2 is rescaled to 100; off by more and the horizon is
  rejected (scored as the base rate).
- a size split (up_by_size_pct / down_by_size_pct) off its total by 1 is fixed on its largest bucket; off
  by more, the split is discarded and only up / flat / down kept.
- similar_precedents must name real card ids (never "now"), one to four, weights summing to 100; a bad
  entry is removed.
- reasons must point at a path that exists in SCENE and is not listed in absent; a bad reason is removed.
  With no reason left the answer is still graded, flagged ungrounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .rulebook import (DIRECTION_KEYS, DOWN_SIZE_KEYS, HORIZON_NAMES, PUSHES_TOWARD, REASON_HORIZONS, REASONS_MAX,
                       REASONS_MIN, SIMILAR_PRECEDENTS_MAX, UP_SIZE_KEYS)
from .grading import SIZE_BUCKETS

SUM_RESCALE_TOLERANCE = 2        # up/flat/down may miss 100 by this much and be rescaled
SIZE_SPLIT_FIX_TOLERANCE = 1     # a size split may miss its total by this much and be fixed on its largest bucket


@dataclass
class CheckedAnswer:
    """The answer after code's checks: the forecast per horizon, the surviving precedents and reasons, and
    the record of everything removed or fixed."""

    checked_forecast: dict = field(default_factory=dict)
    checked_similar_precedents: list[dict] = field(default_factory=list)
    checked_reasons: list[dict] = field(default_factory=list)
    checked_strongest_reason_against_lean: dict | None = None
    answer_checks: dict = field(default_factory=lambda: {
        "removed_invalid_items": [], "horizons_rescaled_to_sum_100": [], "horizons_rejected_sum_far_from_100": [],
        "horizons_size_split_fixed_off_by_1": [], "horizons_size_split_discarded": [], "valid_reason_count": 0})

    @property
    def is_grounded(self) -> bool:
        return self.answer_checks["valid_reason_count"] > 0

    def remove(self, answer_path: str, why: str) -> None:
        self.answer_checks["removed_invalid_items"].append({"answer_path": answer_path, "removed_because": why})


def scene_has_path(scene: dict, dotted: str) -> bool:
    node = scene
    for key in dotted.split("."):
        if isinstance(node, list):
            if not key.isdigit() or int(key) >= len(node):
                return False
            node = node[int(key)]
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return False
    return True


def check_answer(answer: dict | None, scene: dict, asked_horizons: list[str], card_ids: dict[str, str]) -> CheckedAnswer:
    """``card_ids`` maps each shown card id to its read_id, so a precedent can be tied back to its row."""
    checked = CheckedAnswer()
    if not isinstance(answer, dict):
        for horizon in asked_horizons:
            checked.checked_forecast[horizon] = {"status": "rejected", "why": "no answer"}
            checked.answer_checks["horizons_rejected_sum_far_from_100"].append(horizon)
        return checked
    absent_paths = {a.get("path") for a in scene.get("absent", []) if isinstance(a, dict)}
    _check_precedents(answer.get("similar_precedents"), card_ids, checked)
    _check_reasons(answer.get("reasons"), scene, absent_paths, checked)
    against = answer.get("strongest_reason_against_lean")
    if _reason_is_valid(against, scene, absent_paths):
        checked.checked_strongest_reason_against_lean = {k: against[k] for k in ("input_field_path", "pushes_toward", "horizon")}
    elif against is not None:
        checked.remove("strongest_reason_against_lean", "not a valid reason")
    forecast = answer.get("forecast") if isinstance(answer.get("forecast"), dict) else {}
    for horizon in asked_horizons:
        checked.checked_forecast[horizon] = _check_horizon(horizon, forecast.get(horizon), checked)
    for horizon in forecast:
        if horizon not in asked_horizons:
            checked.remove(f"forecast.{horizon}", "horizon not asked")
    return checked


def _check_precedents(items, card_ids: dict[str, str], checked: CheckedAnswer) -> None:
    if not isinstance(items, list):
        if items is not None:
            checked.remove("similar_precedents", "not a list")
        return
    kept = []
    for i, item in enumerate(items):
        pid = item.get("precedent_id") if isinstance(item, dict) else None
        weight = item.get("weight_pct_of_picked_precedents") if isinstance(item, dict) else None
        if pid not in card_ids:
            checked.remove(f"similar_precedents.{i}", f"not a shown card: {pid!r}")
            continue
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
            checked.remove(f"similar_precedents.{i}", "weight is not a number")
            continue
        kept.append({"precedent_id": pid, "read_id": card_ids[pid], "weight_pct_of_picked_precedents": int(round(weight)),
                     "similar_on": [s for s in (item.get("similar_on") or []) if isinstance(s, str)][:8],
                     "differs_most_on": item.get("differs_most_on") if isinstance(item.get("differs_most_on"), str) else None})
    if len(kept) > SIMILAR_PRECEDENTS_MAX:
        for extra in kept[SIMILAR_PRECEDENTS_MAX:]:
            checked.remove(f"similar_precedents.{extra['precedent_id']}", f"more than {SIMILAR_PRECEDENTS_MAX} precedents")
        kept = kept[:SIMILAR_PRECEDENTS_MAX]
    total = sum(k["weight_pct_of_picked_precedents"] for k in kept)
    if kept and total and abs(total - 100) > SUM_RESCALE_TOLERANCE:
        checked.remove("similar_precedents", f"weights sum to {total}, not 100")
        kept = []
    checked.checked_similar_precedents = kept


def _reason_is_valid(reason, scene: dict, absent_paths: set) -> bool:
    if not isinstance(reason, dict):
        return False
    path, pushes, horizon = reason.get("input_field_path"), reason.get("pushes_toward"), reason.get("horizon")
    return (isinstance(path, str) and scene_has_path(scene, path) and path not in absent_paths
            and pushes in PUSHES_TOWARD and horizon in REASON_HORIZONS)


def _check_reasons(items, scene: dict, absent_paths: set, checked: CheckedAnswer) -> None:
    if not isinstance(items, list):
        if items is not None:
            checked.remove("reasons", "not a list")
        return
    kept = []
    for i, reason in enumerate(items):
        if _reason_is_valid(reason, scene, absent_paths):
            kept.append({k: reason[k] for k in ("input_field_path", "pushes_toward", "horizon")})
        else:
            path = reason.get("input_field_path") if isinstance(reason, dict) else None
            why = ("path is listed in absent" if path in absent_paths else
                   "path is not in the scene" if not (isinstance(path, str) and scene_has_path(scene, path)) else
                   "pushes_toward or horizon is not an allowed value")
            checked.remove(f"reasons.{i}", why)
    if len(kept) > REASONS_MAX:
        for extra in kept[REASONS_MAX:]:
            checked.remove(f"reasons.{extra['input_field_path']}", f"more than {REASONS_MAX} reasons")
        kept = kept[:REASONS_MAX]
    checked.checked_reasons = kept
    checked.answer_checks["valid_reason_count"] = len(kept)
    if 0 < len(kept) < REASONS_MIN:
        checked.answer_checks["fewer_reasons_than_asked"] = True


def _whole(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def _check_horizon(horizon: str, block, checked: CheckedAnswer) -> dict:
    if not isinstance(block, dict):
        checked.answer_checks["horizons_rejected_sum_far_from_100"].append(horizon)
        return {"status": "rejected", "why": "missing"}
    shares = {k: _whole(block.get(k)) for k in DIRECTION_KEYS}
    if any(v is None or v < 0 for v in shares.values()):
        checked.answer_checks["horizons_rejected_sum_far_from_100"].append(horizon)
        return {"status": "rejected", "why": "up_pct, flat_pct or down_pct missing"}
    total = sum(shares.values())
    if total == 0 or abs(total - 100) > SUM_RESCALE_TOLERANCE:
        checked.answer_checks["horizons_rejected_sum_far_from_100"].append(horizon)
        return {"status": "rejected", "why": f"shares sum to {total}"}
    if total != 100:
        shares = _rescale_to_100(shares)
        checked.answer_checks["horizons_rescaled_to_sum_100"].append(horizon)
    out = {"status": "ok", **shares}
    buckets = _size_buckets(horizon, block, shares, checked)
    if buckets is not None:
        out["size_buckets_pct"] = buckets
    return out


def _rescale_to_100(shares: dict[str, int]) -> dict[str, int]:
    total = sum(shares.values())
    scaled = {k: v * 100 / total for k, v in shares.items()}
    whole = {k: int(v) for k, v in scaled.items()}
    for k in sorted(scaled, key=lambda key: scaled[key] - whole[key], reverse=True)[: 100 - sum(whole.values())]:
        whole[k] += 1
    return whole


def _size_buckets(horizon: str, block: dict, shares: dict[str, int], checked: CheckedAnswer) -> dict | None:
    """The seven-bucket split, or None when a side's split was discarded."""
    sides = {"up_pct": ("up_by_size_pct", UP_SIZE_KEYS), "down_pct": ("down_by_size_pct", DOWN_SIZE_KEYS)}
    split: dict[str, int] = {}
    for share_key, (block_key, keys) in sides.items():
        raw = block.get(block_key)
        values = {k: _whole(raw.get(k)) if isinstance(raw, dict) else None for k in keys}
        if any(v is None or v < 0 for v in values.values()):
            checked.answer_checks["horizons_size_split_discarded"].append(horizon)
            return None
        gap = shares[share_key] - sum(values.values())
        if gap != 0:
            if abs(gap) <= SIZE_SPLIT_FIX_TOLERANCE:
                largest = max(keys, key=lambda k: values[k])
                values[largest] += gap
                if horizon not in checked.answer_checks["horizons_size_split_fixed_off_by_1"]:
                    checked.answer_checks["horizons_size_split_fixed_off_by_1"].append(horizon)
            else:
                checked.answer_checks["horizons_size_split_discarded"].append(horizon)
                return None
        split.update(values)
    split["flat_within_1_flat_edge"] = shares["flat_pct"]
    return {bucket: split[bucket] for bucket in SIZE_BUCKETS}


__all__ = ["CheckedAnswer", "check_answer", "scene_has_path", "HORIZON_NAMES"]
