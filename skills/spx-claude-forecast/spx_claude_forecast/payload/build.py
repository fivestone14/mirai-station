"""Assemble one read's payload: the scene Claude sees, the ask, the hashes, and the line that records it.

Order of work: every content block is built from the frozen inputs (a block that raises is declared absent,
never fatal); the seven everyday items are read off the built blocks as the read's fingerprint; the library
gives the base rate and the eight nearest past moments for that fingerprint; the leak checks run over the
finished scene; then the canonical scene is hashed (cards sorted by id, so both answers share one
``claude_input_sha256``) and the two prompts are rendered: answer 1 with the cards shuffled, answer 2 with
the cards reversed and the direction order flipped.

The payload line is written BEFORE any call, with everything a reviewer needs: what Claude saw, how the
cards were picked, the rules and builder hashes, the quality flags, and the facts logged but never sent.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .. import grading, jsonl_store, library, precedents, rulebook, station_stores
from ..control import now_utc_iso
from ..paths import PACKAGE_DIR, STATION_ROOT
from .block_result import BlockResult
from .frozen_inputs import FrozenInputs

SCHEMA = "spx_claude_payload/1.0.0"
CONTENT_BLOCKS = ("data_sources", "clock", "calendar", "scale", "tape", "open_signals", "close_signals", "options", "flow",
                  "internals", "cross_asset", "overnight", "foreign", "news", "code")   # built from the inputs, in this order
SCENE_CHAR_CAP = 9_500               # the scene body; over it, the leftover blocks are trimmed in TRIM_ORDER
TRIM_ORDER = ("code", "news")        # what goes first when the scene is over the cap (the cards are cut by precedents.py)
PRICE_LEVEL_FROM = 1000           # a number this big in the scene reads as an index level, unless its key is a count
COUNT_KEYS = frozenset({"trades", "add_live_approx", "add_30m_change", "fill_base_n", "captured_60m", "sessions", "candidate_sessions",
                        "big_prints_10m", "minutes_present", "stale_dropped", "index_mover_items_60m"})   # counts that may pass 1,000
LEAK_DATE_PATTERN = re.compile(r"20\d\d-\d\d-\d\d")
LEAK_WORDS = ("JEV", "pool_v", "blend", "% sure", "read_id", "row_ts")
BUILDER_SOURCES = ("payload", "grading.py", "library.py", "precedents.py", "rulebook.py")   # what builder_sha covers
VIX_IMPLIED_TRADING_DAYS = 252


@dataclass
class BuiltPayload:
    """One read's payload: the scene as sent, the ask, both prompts, and the record line."""

    scene: dict
    ask: dict
    claude_input_sha256: str
    prompts: dict[str, str]            # answer_1 / answer_2 -> the exact body
    prompt_sha256: dict[str, str]
    direction_order: dict[str, list[str]]
    precedent_order: dict[str, str]
    card_ids: dict[str, str]           # card id -> read_id, for the answer checks
    nearest_card_ids: list[str]        # code's order, nearest first
    base_rate_pct: dict[str, dict]     # per horizon: up/flat/down pct, for the reads and outcomes lines
    asked_horizons: list[str]
    line: dict                         # the payload line, status "built" or "refused"
    refused_because: list[str] = field(default_factory=list)


def build_payload(inputs: FrozenInputs, library_rows: list[dict], *, origin: str = "live",
                  library_manifest: dict | None = None) -> BuiltPayload:
    scene: dict = {}
    absent: list[dict] = []
    builder_errors: list[str] = []
    for name in CONTENT_BLOCKS:
        result = _build_block(name, inputs, builder_errors)
        if result.data:
            scene[name] = result.data
        absent.extend(result.absent)

    fingerprint = library.fingerprint_of(scene)
    slot_minute = int(inputs.slot[:2]) * 60 + int(inputs.slot[3:])
    history = precedents.history_for_read(library_rows, fingerprint, slot_minute, inputs.day)
    if history.base_rate:
        scene["base_rate"] = _base_rate_block(history.base_rate, inputs.slot)
    scene["precedents"] = {"rule": history.picker.get("rule"), "candidate_sessions": history.picker.get("candidate_sessions"),
                           "shown": len(history.cards), "order": "by_id",
                           "now": {k: round(v, 2) for k, v in fingerprint.items() if isinstance(v, (int, float))},
                           "cards": sorted(history.cards, key=lambda c: c["id"])}
    absent.extend(history.absent)
    scene = _trim_to_cap(scene, absent)
    scene["absent"] = absent

    ask, asked = _ask_for(inputs)
    canonical = jsonl_store.canonical_json(_ordered_scene(scene))
    claude_input_sha256 = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    prompts, prompt_sha, direction_order, precedent_order = _render_both_answers(scene, ask, claude_input_sha256)
    leaks = leak_checks(scene)

    base_rate_pct = {h: {k: history.base_rate[h][k] for k in ("up_pct", "flat_pct", "down_pct")}
                     for h in library.HORIZONS if history.base_rate and h in history.base_rate}
    line = {
        "line_type": "payload", "read_id": inputs.read_id, "claude_input_sha256": claude_input_sha256,
        "prompt_and_model_version": rulebook.PROMPT_AND_MODEL_VERSION, "schema": SCHEMA, "trading_day": inputs.day,
        "half_hour_slot_et": inputs.slot, "origin": origin, "status": "refused" if leaks else "built",
        "why": "; ".join(leaks) if leaks else None, "cut_at": inputs.cut.isoformat(), "built_at": now_utc_iso(),
        "built_lag_s": _built_lag_seconds(inputs), "scene": scene, "ask": ask, "asked_horizons": asked,
        "prompt_sha256": prompt_sha, "direction_order_asked": direction_order, "precedent_order_shown": precedent_order,
        "rules_sha": rulebook.rulebook_sha(), **code_version(),
        "quality_flags": quality_flags(inputs, builder_errors),
        "base_rate_shown_to_claude": base_rate_pct,
        "precedent_set": history.precedent_set, "precedent_picker": history.picker,
        "library": {k: (library_manifest or {}).get(k) for k in ("built_at", "rows", "sessions", "rows_sha256")},
        "logged_never_sent": _logged_never_sent(inputs, history),
    }
    line = {k: v for k, v in line.items() if v is not None}
    return BuiltPayload(scene=scene, ask=ask, claude_input_sha256=claude_input_sha256, prompts=prompts,
                        prompt_sha256=prompt_sha, direction_order=direction_order, precedent_order=precedent_order,
                        card_ids={p["card_id"]: p["read_id"] for p in history.precedent_set},
                        nearest_card_ids=[p["card_id"] for p in history.precedent_set], base_rate_pct=base_rate_pct,
                        asked_horizons=asked, line=line, refused_because=leaks)


# --- the blocks ----------------------------------------------------------------------------------------------------

def _build_block(name: str, inputs: FrozenInputs, errors: list[str]) -> BlockResult:
    try:
        module = importlib.import_module(f"{__package__}.blocks.{name}")
        result = getattr(module, f"build_{name}_block")(inputs)
        if not isinstance(result, BlockResult):
            raise TypeError(f"build_{name}_block returned {type(result).__name__}")
        return result
    except ModuleNotFoundError as e:
        errors.append(f"{name}: not built yet ({e.name})")
    except Exception as e:                                       # one bad block never costs the read
        errors.append(f"{name}: {type(e).__name__}: {e}")
    result = BlockResult()
    result.leave_out(name, "builder_error")
    return result


def _base_rate_block(base: dict, slot: str) -> dict:
    block = {"slot": slot, "sessions": base["sessions"], "pooled_minutes": base["pooled_minutes"],
             "bucket_order": base["bucket_order"]}
    for horizon in library.HORIZONS:
        if horizon in base:
            block[horizon] = base[horizon]["counts"]
    return block


def _trim_to_cap(scene: dict, absent: list[dict]) -> dict:
    for name in TRIM_ORDER:
        if len(jsonl_store.canonical_json(scene)) <= SCENE_CHAR_CAP:
            break
        if name in scene:
            scene.pop(name)
            absent.append({"path": name, "why": "trimmed_for_size"})
    return scene


def _ordered_scene(scene: dict) -> dict:
    """The scene with its blocks in a fixed order, so the rendering is the same from any builder."""
    order = ("base_rate",) + CONTENT_BLOCKS + ("precedents", "absent")
    return {k: scene[k] for k in order if k in scene}


# --- the ask -------------------------------------------------------------------------------------------------------

def _ask_for(inputs: FrozenInputs) -> tuple[dict, list[str]]:
    zones = inputs.flat_zones if isinstance(inputs.flat_zones, dict) else {}
    sessions = station_stores.import_spx_jev("sessions")
    close = sessions.session_close(inputs.horizon_start)
    minutes_left = int((close - inputs.horizon_start).total_seconds() // 60)
    edges = grading.flat_edges_for(zones, minutes_left)
    horizons: dict[str, dict] = {}
    for name, minutes in grading.HORIZON_WINDOWS.items():
        if name in edges and inputs.horizon_start + timedelta(minutes=minutes) <= close + timedelta(minutes=grading.CLOSE_GRACE_MIN):
            horizons[name] = {"window_minutes": min(minutes, minutes_left), "graded_move_measured_to": "window_average_price",
                              "flat_edge_sig": round(edges[name] / inputs.sigma_points, 3)}
    if "to_close" in edges and minutes_left > 0:
        horizons["to_close"] = {"window_minutes": minutes_left, "graded_move_measured_to": "official_close_price",
                                "flat_edge_sig": round(edges["to_close"] / inputs.sigma_points, 3)}
    ask = {"direction_order_asked": list(rulebook.DIRECTION_KEYS), "horizons": horizons}
    return ask, list(horizons)


def _render_both_answers(scene: dict, ask: dict, claude_input_sha256: str):
    cards = scene["precedents"]["cards"]
    shuffled = list(cards)
    random.Random(int(claude_input_sha256[-12:], 16)).shuffle(shuffled)
    orders = {"answer_1": (shuffled, "shuffled", list(rulebook.DIRECTION_KEYS)),
              "answer_2": (list(reversed(shuffled)), "reversed", list(reversed(rulebook.DIRECTION_KEYS)))}
    prompts, shas, directions, precedent_orders = {}, {}, {}, {}
    for answer, (order, label, direction) in orders.items():
        shown = dict(scene)
        shown["precedents"] = {**scene["precedents"], "order": label, "cards": order}
        shown_ask = {**ask, "direction_order_asked": direction}
        body = rulebook.render_prompt(jsonl_store.canonical_json(_ordered_scene(shown)), jsonl_store.canonical_json(shown_ask))
        prompts[answer] = body
        shas[answer] = jsonl_store.text_sha256(body, 64)
        directions[answer] = direction
        precedent_orders[answer] = label
    return prompts, shas, directions, precedent_orders


# --- the checks and the stamps ------------------------------------------------------------------------------------

def leak_checks(scene: dict) -> list[str]:
    """Why the scene must not be sent: a date, a price level, or another voice's name anywhere in it."""
    text = jsonl_store.canonical_json(scene)
    reasons = []
    if LEAK_DATE_PATTERN.search(text):
        reasons.append("a date is in the scene")
    levels = [f"{key}={value}" for key, value in _big_numbers(scene, "scene") if key not in COUNT_KEYS]
    if levels:
        reasons.append(f"a number that reads as a price level is in the scene: {levels[:3]}")
    for word in LEAK_WORDS:
        if word in text:
            reasons.append(f"the word {word!r} is in the scene")
    return reasons


def _big_numbers(node, key: str):
    """Every (key, number) in the scene with the number at or past PRICE_LEVEL_FROM; a list's numbers carry its key."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _big_numbers(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _big_numbers(v, key)
    elif isinstance(node, (int, float)) and not isinstance(node, bool) and abs(node) >= PRICE_LEVEL_FROM:
        yield key, node


def quality_flags(inputs: FrozenInputs, builder_errors: list[str]) -> dict:
    anchor = inputs.anchor
    flags = {"anchor_src": getattr(anchor, "source", None), "anchor_vs_vix_x": _anchor_vs_vix(inputs),
             "options_book": inputs.options_book or "unknown",
             "code_source": "live" if inputs.code_answers else "none",
             "is_stale_read": bool(getattr(inputs.scene, "bar_clock", False)) and False,
             "load_notes": list(inputs.load_notes), "builder_errors": builder_errors}
    return {k: v for k, v in flags.items() if v not in (None, [], {})}


def _anchor_vs_vix(inputs: FrozenInputs) -> float | None:
    """The morning ruler over the VIX-implied daily move (prior close x prior VIX close / 100 / sqrt(252))."""
    spx = inputs.daily_closes.get("$SPX") or []
    vix = inputs.daily_closes.get("$VIX") or []
    if not spx or not vix or not inputs.sigma_points:
        return None
    try:
        implied = float(spx[-1]["close"]) * float(vix[-1]["close"]) / 100 / (VIX_IMPLIED_TRADING_DAYS ** 0.5)
    except (KeyError, TypeError, ValueError):
        return None
    return round(inputs.sigma_points / implied, 2) if implied else None


def _logged_never_sent(inputs: FrozenInputs, history: precedents.HistoryForRead) -> dict:
    zones = inputs.flat_zones if isinstance(inputs.flat_zones, dict) else None
    out = {"spot": inputs.spot, "sigma_points": round(inputs.sigma_points, 2), "flat_zones_points": zones,
           "nearest_card_ids": [p["card_id"] for p in history.precedent_set[:3]], "row_ts": inputs.row_ts}
    return out


def _built_lag_seconds(inputs: FrozenInputs) -> int:
    from datetime import datetime, timezone
    return int((datetime.now(timezone.utc) - inputs.cut).total_seconds())


def code_version() -> dict:
    """The commit the tree is on (read from .git, no subprocess) and a hash of the builder's own sources."""
    out: dict = {}
    try:
        git = STATION_ROOT / ".git"
        head = (git / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            loose = git / ref
            head = loose.read_text().strip() if loose.exists() else next(
                (ln.split()[0] for ln in (git / "packed-refs").read_text().splitlines() if ln.endswith(" " + ref)), "")
        if re.fullmatch(r"[0-9a-f]{40}", head):
            out["git_commit"] = head[:12]
    except OSError:
        pass
    digest = hashlib.sha256()
    for name in BUILDER_SOURCES:
        path = PACKAGE_DIR / name
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            digest.update(str(file.relative_to(PACKAGE_DIR)).encode())
            digest.update(file.read_bytes())
    out["builder_sha"] = digest.hexdigest()[:12]
    return out


__all__ = ["BuiltPayload", "build_payload", "leak_checks", "quality_flags", "code_version", "SCHEMA", "CONTENT_BLOCKS", "Path"]
