"""How much each step-2 question counts in the sums: the one seam a learning method plugs into.

The grader scores the sums (grade.py) and hands this module what it graded; ``QuestionWeights``
turns that into one weight per question, and step 3 (hour.answer_sentences) leaves out any
question whose weight is under MIN_WEIGHT. Nothing else reads a weight.

For now the method is neutral: every live question weighs 1.0, whatever the grades say. SNDK JEV's
mutual-information weights were not carried over: on its first 50 graded reads none of them beat
shuffled outcomes (the study of 2026-09-26), so a learning method is being designed separately.
To drop one in, subclass QuestionWeights, set ``method`` and override ``learn``; the grader, the
weights file and step 3 stay as they are.

    weights.json   {"method", "min_weight", "questions": {qid: {"weight", "n", "in_step_3", "why"}}, ...}
                   the grader adds the sums' tallies beside it (grade.run)
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

WEIGHTS_NAME = "weights.json"
MIN_WEIGHT = 0.5        # a question below this is left out of step 3


class QuestionWeights:
    """One weight per step-2 question, learned from the grades of the lane's primary sum."""

    method = "neutral"

    def __init__(self, questions: dict[str, dict] | None = None) -> None:
        self.questions = questions or {}

    @classmethod
    def load(cls, out_dir: Path) -> "QuestionWeights":
        """The weights the grader last wrote; a missing or unreadable file means every weight is 1.0."""
        p = Path(out_dir) / WEIGHTS_NAME
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(d.get("questions") if isinstance(d, dict) and isinstance(d.get("questions"), dict) else {})

    def weight(self, qid: str) -> float:
        w = (self.questions.get(qid) or {}).get("weight", 1.0)
        return float(w) if isinstance(w, (int, float)) else 1.0

    def in_step_3(self, qid: str) -> bool:
        return self.weight(qid) >= MIN_WEIGHT

    @classmethod
    def learn(cls, graded: list[dict], allowed: dict[str, set[str]]) -> "QuestionWeights":
        """``graded`` is every grade line whose primary sum was graded (grade.run keeps the outcome flat
        on each line as ``band``, and the picks given afresh on that read under ``fresh``); ``allowed``
        is each live question's current options (grade.live_options). The neutral method counts the
        fresh picks per question, so the record shows the evidence a method would have, and weighs
        every question 1.0."""
        n = Counter(qid for g in graded for qid, p in (g.get("fresh") or {}).items()
                    if qid in allowed and str(p) in allowed[qid])
        return cls({qid: {"weight": 1.0, "n": n[qid], "in_step_3": True,
                          "why": "neutral weights: every live question counts the same until a learning method is chosen"}
                    for qid in sorted(allowed)})

    def as_json(self) -> dict:
        return {"method": self.method, "min_weight": MIN_WEIGHT, "questions": self.questions}
