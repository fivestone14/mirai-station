"""Phase 8: the nightly test variants — a few of the day's reads asked again with the history deliberately broken.

Three variants, one answer each, on up to READS_PER_NIGHT of the day's usable reads (the first, the middle
and the last by slot):

  no_precedents_shown           the eight cards removed; everything else in the scene the production read's
  precedent_outcomes_shuffled   the same cards, their outcomes dealt to other cards (no card keeps its own)
  random_precedents_shown       eight cards drawn at random from the same time-of-day pool, nearness ignored

Byte for byte the rest of the scene is the production read's, so the only thing that separates a variant's
score from the production score is what the cards were worth. The replies land in ``arms/{day}.jsonl`` as
answer lines and ``test_variant_forecast`` lines (spec/record_formats.md), stamped with the variant and the
fingerprint of the changed scene; the scorer pairs them with the production forecast on the same outcome
lines (scoring, scorecard ``test_variants``). They are never mixed into the record and never shown on the
phone. The calls run under the same gates as a live call (pytest, pause, the daily cap) and a cap of their
own per night; every shuffle and draw is seeded by the production scene's hash, so a night can be replayed.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from . import claude_call, control, jsonl_store, library, precedents, read_runner, rulebook
from .control import log, log_job_run, now_utc_iso
from .paths import ForecastPaths
from .payload.build import leak_checks, render_both_answers

TEST_VARIANT_VERSION = 1
NO_PRECEDENTS_SHOWN = "no_precedents_shown"
PRECEDENT_OUTCOMES_SHUFFLED = "precedent_outcomes_shuffled"
RANDOM_PRECEDENTS_SHOWN = "random_precedents_shown"
VARIANTS = (NO_PRECEDENTS_SHOWN, PRECEDENT_OUTCOMES_SHUFFLED, RANDOM_PRECEDENTS_SHOWN)
READS_PER_NIGHT = 3                       # the first, the middle and the last usable read of the day
ANSWER_NAME = "answer_1"                  # one answer per variant, the cards in the production answer's order


@dataclass
class VariantPayload:
    """What one variant sends: the production scene with its precedents block changed, rendered as one prompt. The
    attributes read_runner.answer_line and read_runner.final_line need carry the same names as BuiltPayload's."""

    test_variant: str
    scene: dict
    ask: dict
    asked_horizons: list[str]
    claude_input_sha256: str                 # the production scene's: the join key stays production's
    test_variant_claude_input_sha256: str    # the changed scene's
    prompts: dict[str, str]
    prompt_sha256: dict[str, str]
    direction_order: dict[str, list[str]]
    precedent_order: dict[str, str]
    card_ids: dict[str, str]
    nearest_card_ids: list[str]
    base_rate_pct: dict[str, dict]

    def stamp(self) -> dict:
        """The three fields that mark a line as this variant's (spec/record_formats.md)."""
        return {"test_variant": self.test_variant, "test_variant_version": TEST_VARIANT_VERSION,
                "test_variant_claude_input_sha256": self.test_variant_claude_input_sha256}


def select_reads(reads_lines: list[dict]) -> list[dict]:
    """The production final lines a night re-asks: the first, the middle and the last usable read of the day."""
    finals = sorted((line for line in reads_lines
                     if line.get("line_type") == "final_forecast" and line.get("test_variant") in (None, rulebook.TEST_VARIANT_NONE)
                     and line.get("status") == "ok" and int(line.get("usable_answer_count") or 0) > 0),
                    key=lambda line: str(line.get("half_hour_slot_et", "")))
    if len(finals) <= READS_PER_NIGHT:
        return finals
    middle = finals[len(finals) // 2]
    return [finals[0], middle, finals[-1]]


def variant_precedents(payload_line: dict, variant: str, library_rows: list[dict]) -> tuple[dict | None, dict[str, str], str | None]:
    """``(precedents block as the variant sends it, card id -> read_id, why the variant is skipped)``. A variant that
    would send the production scene unchanged is skipped (no cards to remove, fewer than two outcomes to shuffle, too
    few past reads to draw from). The block's own words (its rule, its candidate count) stay production's: like the
    decoy, the random draw is not announced to Claude, or the variant would only measure Claude ignoring the cards."""
    scene = payload_line.get("scene") or {}
    production = dict(scene.get("precedents") or {})
    cards = list(production.get("cards") or [])
    card_ids = {p["card_id"]: p["read_id"] for p in payload_line.get("precedent_set") or []}
    rng = random.Random(int(payload_line["claude_input_sha256"][-12:], 16))
    if variant == NO_PRECEDENTS_SHOWN:
        if not cards:
            return None, {}, "the production read showed no cards"
        return {**production, "shown": 0, "cards": []}, {}, None
    if variant == PRECEDENT_OUTCOMES_SHUFFLED:
        with_outcome = [c for c in cards if c.get("outcome_flat_edges")]
        if len(with_outcome) < 2:
            return None, {}, "fewer than two cards with an outcome to shuffle"
        dealt = _dealt_outcomes([c["outcome_flat_edges"] for c in with_outcome], rng)
        shuffled = []
        for card in cards:
            if card.get("outcome_flat_edges"):
                shuffled.append({**card, "outcome_flat_edges": dealt.pop(0)})
            else:
                shuffled.append(card)
        return {**production, "cards": shuffled}, card_ids, None
    if variant == RANDOM_PRECEDENTS_SHOWN:
        slot = str(payload_line.get("half_hour_slot_et") or "00:00")
        slot_minute = int(slot[:2]) * 60 + int(slot[3:])
        day = str(payload_line.get("trading_day"))
        pool = precedents.precedent_pool(library_rows, slot_minute, day)
        by_session: dict[str, list[dict]] = {}
        for row in pool:
            by_session.setdefault(row["trading_day"], []).append(row)
        if len(by_session) < precedents.PRECEDENT_MIN_POOL:
            return None, {}, f"fewer than {precedents.PRECEDENT_MIN_POOL} sessions to draw from"
        sessions = sorted(by_session)
        drawn_days = rng.sample(sessions, min(precedents.PRECEDENT_COUNT, len(sessions)))
        earlier = library.sessions_before(library_rows, day)
        drawn_cards, drawn_ids = [], {}
        for drawn_day in drawn_days:
            row = rng.choice(by_session[drawn_day])
            card = precedents.card_for({"row": row}, earlier)
            drawn_cards.append(card)
            drawn_ids[card["id"]] = row["read_id"]
        return {**production, "shown": len(drawn_cards), "cards": sorted(drawn_cards, key=lambda c: c["id"])}, drawn_ids, None
    raise ValueError(f"unknown test variant {variant!r}")


def _dealt_outcomes(outcomes: list[dict], rng: random.Random) -> list[dict]:
    """The outcomes in a new order where no card keeps its own (a derangement), for two or more."""
    order = list(range(len(outcomes)))
    while any(i == j for i, j in enumerate(order)):
        rng.shuffle(order)
    return [outcomes[i] for i in order]


def variant_payload(payload_line: dict, variant: str, library_rows: list[dict]) -> tuple[VariantPayload | None, str | None]:
    """``(the variant's prompt from a production payload line, why it was skipped)``: None and the reason when the
    variant would change nothing."""
    block, card_ids, why = variant_precedents(payload_line, variant, library_rows)
    if why:
        return None, why
    scene = dict(payload_line["scene"])
    scene["precedents"] = block
    if variant == NO_PRECEDENTS_SHOWN:
        scene["absent"] = list(scene.get("absent") or []) + [{"path": "precedents.cards", "why": "none_shown"}]
    leaks = leak_checks(scene)                       # the cards carry no date or level, but a changed scene is checked like any other
    if leaks:
        return None, "the changed scene failed the leak check: " + "; ".join(leaks)
    ask = payload_line["ask"]
    changed_sha = jsonl_store.canonical_json_sha256(scene)
    prompts, shas, directions, orders = render_both_answers(scene, ask, changed_sha)
    base_rate_pct = payload_line.get("base_rate_shown_to_claude") or {}
    return VariantPayload(test_variant=variant, scene=scene, ask=ask, asked_horizons=list(payload_line.get("asked_horizons") or []),
                          claude_input_sha256=payload_line["claude_input_sha256"], test_variant_claude_input_sha256=changed_sha,
                          prompts={ANSWER_NAME: prompts[ANSWER_NAME]}, prompt_sha256={ANSWER_NAME: shas[ANSWER_NAME]},
                          direction_order={ANSWER_NAME: directions[ANSWER_NAME]}, precedent_order={ANSWER_NAME: orders[ANSWER_NAME]},
                          card_ids=card_ids, nearest_card_ids=[p["card_id"] for p in payload_line.get("precedent_set") or []],
                          base_rate_pct=base_rate_pct), None


def already_answered(arms_lines: list[dict]) -> set[tuple[str, str]]:
    """``(read_id, variant)`` pairs that already have a forecast line in the night's arms file."""
    return {(str(line.get("read_id")), str(line.get("test_variant"))) for line in arms_lines
            if line.get("line_type") == "test_variant_forecast"}


def why_not_to_run(paths: ForecastPaths, day: str, is_real_caller: bool, arms_lines: list[dict]) -> str | None:
    """The live call's gates, then the night's own cap on variant answers."""
    gate = control.why_not_to_call(paths, day, is_real_caller)
    if gate:
        return gate
    answered = sum(1 for line in arms_lines if line.get("line_type") == "claude_answer")
    cap = int(control.load_control(paths).get("max_test_variant_calls_per_night") or 0)
    return "capped" if answered >= cap else None


def run_test_variants(paths: ForecastPaths, day: str, library_rows: list[dict], *, call=claude_call.call_claude) -> dict:
    """Ask every variant of every selected read of ``day`` that has no forecast line yet; returns counts and the
    reasons for what was skipped. One line lands per answer and per forecast, as the live runner writes them."""
    reads_lines = jsonl_store.read_json_lines(paths.reads_file(day))
    arms_lines = jsonl_store.read_json_lines(paths.arms_file(day))
    payloads = {str(line.get("read_id")): line for line in jsonl_store.iter_json_lines(paths.payloads_file(day))
                if line.get("line_type") == "payload" and line.get("status") == "built"}
    done = already_answered(arms_lines)
    counts: dict = {"day": day, "reads": 0, "asked": 0, "skipped": {}, "errors": []}
    rules = version = None
    for final in select_reads(reads_lines):
        read_id, payload = str(final["read_id"]), payloads.get(str(final["read_id"]))
        if payload is None:
            counts["skipped"][read_id] = "no built payload line"
            continue
        counts["reads"] += 1
        for variant in VARIANTS:
            if (read_id, variant) in done:
                continue
            gate = why_not_to_run(paths, day, call is claude_call.call_claude, arms_lines)
            if gate:
                counts["skipped"][f"{read_id}#{variant}"] = gate
                continue
            built, why = variant_payload(payload, variant, library_rows)
            if built is None:
                counts["skipped"][f"{read_id}#{variant}"] = why
                continue
            started = now_utc_iso()
            if rules is None:
                rules = rulebook.rulebook_text()
                version = claude_call.cli_version()
            answer = read_runner.answer_line(read_id, ANSWER_NAME, built, call(built.prompts[ANSWER_NAME], rules), version,
                                             test_variant=built.stamp())
            answer["test_variant_precedents"] = built.scene["precedents"]
            jsonl_store.append_json_line(paths.arms_file(day), answer)
            arms_lines.append(answer)
            forecast = read_runner.final_line(read_id, built, [answer], "ok", str(final.get("trading_day") or day),
                                              str(final.get("half_hour_slot_et") or ""), test_variant=built.stamp())
            jsonl_store.append_json_line(paths.arms_file(day), forecast)
            arms_lines.append(forecast)
            counts["asked"] += 1
            if answer.get("error"):
                counts["errors"].append(f"{variant}: {answer['error']}")
            log_job_run(paths, f"test_variant:{variant}", started, not answer.get("error"), error=answer.get("error"),
                        read_id=read_id, cost_usd=(answer.get("call_stats") or {}).get("cost_usd"))
    log(f"test variants: {counts['asked']} asked on {counts['reads']} reads, {len(counts['skipped'])} skipped")
    return counts
