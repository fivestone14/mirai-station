"""What a label family hands back: its sentences, the ones it left out and why, and the figures behind some."""
from __future__ import annotations

from typing import Any


class LabelSet:
    """Labels by path (``"price.recent_move"``), kept as the state JEV reads: ``{group: {key: sentence}}``.

    ``omitted`` maps a path to the reason it could not be written. ``figures`` maps a path to the figure
    behind its sentence (its kind, its number, its cut and the verdict it landed on) so the phone can draw
    the fact rather than print it; JEV never sees a figure.
    """

    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.omitted: dict[str, str] = {}
        self.figures: dict[str, dict] = {}

    def put(self, path: str, sentence: str, *, figure: dict | None = None) -> None:
        group, key = path.split(".", 1)
        self.state.setdefault(group, {})[key] = sentence
        if figure is not None:
            self.figures[path] = figure

    def omit(self, path: str, reason: str) -> None:
        self.omitted[path] = reason

    def paths(self) -> set[str]:
        """Every label path this set wrote or omitted."""
        return {f"{g}.{k}" for g, labels in self.state.items() for k in labels} | set(self.omitted)

    def update(self, other: LabelSet) -> None:
        for group, labels in other.state.items():
            self.state.setdefault(group, {}).update(labels)
        self.omitted.update(other.omitted)
        self.figures.update(other.figures)
