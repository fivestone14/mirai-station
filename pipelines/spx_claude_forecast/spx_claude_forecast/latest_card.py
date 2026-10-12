"""``latest.json``: the one record the phone's card reads, rewritten after every read.

The phone (runtime/viewstation/static/m/jev-spx.html, the "Claude's read" card) never opens a daily file: it
polls this one and draws what it holds. So it carries everything the card and its sheet show: the final
forecast per horizon with the base rate beside it (the final line already has both), the reasons the
answers gave in plain words (reason_words), the strongest reason against the lean, and the scorecard's
verdict as the record line. Field names follow spec/record_formats.md ("latest.json").
"""
from __future__ import annotations

from .reason_words import reason_in_words

REASONS_SHOWN_MAX = 4     # the sheet lists at most this many; two answers can give eight between them
BASE_RATE_REFERENCE = "base_rate_shown_to_claude"     # the scorecard's name for the base rate among its references


def latest_card_for(built, final: dict, answers: list[dict], scorecard: dict | None) -> dict:
    """``built`` is the built payload (``line`` and ``claude_input_sha256``), ``final`` the final forecast line,
    ``answers`` the checked answer lines (empty when no call was made), ``scorecard`` the nightly scorecard or None."""
    against = next((a["checked_strongest_reason_against_lean"] for a in answers
                    if isinstance(a.get("checked_strongest_reason_against_lean"), dict)), None)
    return {"read_id": final["read_id"], "trading_day": final["trading_day"], "half_hour_slot_et": final["half_hour_slot_et"],
            "read_at": built.line.get("cut_at"), "status": final["status"],
            "usable_answer_count": final["usable_answer_count"], "claude_input_sha256": built.claude_input_sha256,
            "prompt_and_model_version": final.get("prompt_and_model_version"),
            "forecast": final["forecast"],
            "reasons": unique_reasons_in_words(answers),
            "strongest_reason_against_lean": reason_in_words(against),
            "record": record_line_of(scorecard),
            "written_at": final["written_at"]}


def unique_reasons_in_words(answers: list[dict]) -> list[dict]:
    """Each distinct reason across the answers, in the order first given, with its words, at most REASONS_SHOWN_MAX."""
    seen: set[tuple] = set()
    out = []
    for answer in answers:
        for reason in answer.get("checked_reasons") or []:
            key = (reason.get("input_field_path"), reason.get("pushes_toward"), reason.get("horizon"))
            if key in seen:
                continue
            seen.add(key)
            out.append(reason_in_words(reason))
    return out[:REASONS_SHOWN_MAX]


def record_line_of(scorecard: dict | None) -> dict:
    """The scorecard as the card's one record line: the verdict, the graded reads, and per horizon how often
    Claude's top choice was right and its skill against the base rate (None until scored)."""
    card = scorecard if isinstance(scorecard, dict) else {}
    horizons = {}
    for horizon, block in (card.get("horizons") or {}).items():
        skill = ((block.get("skill_vs") or {}).get(BASE_RATE_REFERENCE) or {}).get("skill_pct")
        horizons[horizon] = {"claude_right_pct": block.get("claude_right_pct"), "skill_vs_base_rate_pct": skill,
                             "reads": block.get("reads", 0)}
    return {"verdict": card.get("verdict") or "Too early", "graded_reads": card.get("graded_reads", 0),
            "scorecard_built_at": card.get("built_at"), "horizons": horizons}
