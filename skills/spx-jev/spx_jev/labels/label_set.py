"""What a label family hands back: its sentences, the ones it left out and why, the figures behind some,
and the sleep gates it decided."""
from __future__ import annotations

from typing import Any


class LabelSet:
    """Labels by path (``"price.recent_move"``), kept as the state JEV reads: ``{group: {key: sentence}}``.

    ``omitted`` maps a path to the reason it could not be written. ``ended`` holds the omitted paths whose
    condition is over (no shock in the lookback, no new extreme, the last hour) rather than unmeasured: a
    question on one of them is not held from its last answer (cadence.fill_missing). ``figures`` maps a path to the figure
    behind its sentence (its kind, its number, its cut and the verdict it landed on) so the phone can draw
    the fact rather than print it; JEV never sees a figure. ``gates`` maps a gated question's id (one whose
    ``sleep_when`` a family judges) to None when it is awake this read, or to why it sleeps.
    """

    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.omitted: dict[str, str] = {}
        self.ended: set[str] = set()
        self.figures: dict[str, dict] = {}
        self.gates: dict[str, str | None] = {}

    def put(self, path: str, sentence: str, *, figure: dict | None = None) -> None:
        group, key = path.split(".", 1)
        self.state.setdefault(group, {})[key] = sentence
        if figure is not None:
            self.figures[path] = figure

    def omit(self, path: str, reason: str, *, ended: bool = False) -> None:
        self.omitted[path] = reason
        if ended:
            self.ended.add(path)

    def wake(self, question_id: str) -> None:
        self.gates[question_id] = None

    def sleep(self, question_id: str, reason: str) -> None:
        self.gates[question_id] = reason

    def paths(self) -> set[str]:
        """Every label path this set wrote or omitted."""
        return {f"{g}.{k}" for g, labels in self.state.items() for k in labels} | set(self.omitted)

    def update(self, other: LabelSet) -> None:
        for group, labels in other.state.items():
            self.state.setdefault(group, {}).update(labels)
        self.omitted.update(other.omitted)
        self.ended.update(other.ended)
        self.figures.update(other.figures)
        self.gates.update(other.gates)
