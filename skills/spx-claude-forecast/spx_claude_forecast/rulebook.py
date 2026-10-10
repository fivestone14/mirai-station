"""The rulebook Claude reads on every call, and the contract its answer must follow.

The text lives beside the code in ``rulebook_cr-1.txt`` so a change to it is a change in git; it is sent
as the system prompt, byte-identical all day so the CLI serves it from cache, and its hash
(``rules_sha``) is stamped on every payload line. Nothing in it carries a number Claude could copy.
"""
from __future__ import annotations

from pathlib import Path

from .jsonl_store import text_sha256

PROMPT_AND_MODEL_VERSION = "cr-1"           # bumped, between sessions only, when the rulebook, the builder or the model changes
TEST_VARIANT_NONE = "none"                  # the test_variant stamp of production lines: no experiment changed what Claude saw
RULEBOOK_FILE = Path(__file__).resolve().parent / f"rulebook_{PROMPT_AND_MODEL_VERSION}.txt"
HORIZON_NAMES = ("next_30_minutes", "next_60_minutes", "to_close")
DIRECTION_KEYS = ("up_pct", "flat_pct", "down_pct")
UP_SIZE_KEYS = ("up_1_to_2_flat_edges", "up_2_to_3_flat_edges", "up_over_3_flat_edges")
DOWN_SIZE_KEYS = ("down_1_to_2_flat_edges", "down_2_to_3_flat_edges", "down_over_3_flat_edges")
PUSHES_TOWARD = ("up", "down", "bigger_move", "smaller_move")
REASON_HORIZONS = HORIZON_NAMES + ("all",)
SIMILAR_PRECEDENTS_MAX = 4
REASONS_MIN, REASONS_MAX = 2, 4
PROMPT_HEAD = "Read this scene cold and reply with the JSON object only.\n\nSCENE:\n"
ASK_HEAD = "\n\nASK:\n"


def rulebook_text() -> str:
    return RULEBOOK_FILE.read_text(encoding="utf-8")


def rulebook_sha() -> str:
    return text_sha256(rulebook_text())


def render_prompt(scene_json: str, ask_json: str) -> str:
    """The exact body sent for one answer: the scene, then the ask, both already rendered as compact JSON."""
    return PROMPT_HEAD + scene_json + ASK_HEAD + ask_json
