"""What a label family hands back: its sentences, the ones it left out and why, the figures behind some,
and the sleep gates it decided."""
from __future__ import annotations

from typing import Any


class LabelSet:
    """Labels by path (``"price.recent_move"``), kept as the state JEV reads: ``{group: {key: sentence}}``.

    ``omitted`` maps a path to the reason it could not be written. ``ended`` holds the omitted paths whose
    condition is not there (no shock in the lookback, no new extreme, not a stress day, the last hour) rather
    than unmeasured: a question reading one is asleep on that reason, not missing a label, and holds no earlier
    answer (ask.build_requests, ``ended_reasons``). ``no_hold`` holds the omitted paths that are missing
    only for now, while what they describe is still under way (a touch the siege box has not judged yet): an
    answer given before it was about another moment, so the question holds none. ``figures`` maps a path to the figure
    behind its sentence (its kind, its number, its cut and the verdict it landed on) so the phone can draw
    the fact rather than print it; JEV never sees a figure. ``gates`` maps a gated question's id (one whose
    ``sleep_when`` a family judges) to None when it is awake this read, or to why it sleeps.
    """

    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.omitted: dict[str, str] = {}
        self.ended: set[str] = set()
        self.no_hold: set[str] = set()
        self.figures: dict[str, dict] = {}
        self.gates: dict[str, str | None] = {}

    def put(self, path: str, sentence: str, *, figure: dict | None = None) -> None:
        group, key = path.split(".", 1)
        self.state.setdefault(group, {})[key] = sentence
        if figure is not None:
            self.figures[path] = figure

    def omit(self, path: str, reason: str, *, ended: bool = False, hold: bool = True) -> None:
        self.omitted[path] = reason
        if ended:
            self.ended.add(path)
        if not hold:
            self.no_hold.add(path)

    def wake(self, question_id: str) -> None:
        self.gates[question_id] = None

    def sleep(self, question_id: str, reason: str) -> None:
        self.gates[question_id] = reason

    def unmeasured(self, question_id: str) -> None:
        """A gate that could not be measured: a fact nobody saw is not "nothing happened", so the question is not put
        to sleep on it. It is left awake with the label it reads omitted for the same reason, so the read counts it
        missing (ask.build_requests), which the health reviews see."""
        self.gates[question_id] = None

    def decide(self, question_id: str, verdict: str | None, why: str | None) -> None:
        """Wake the question when its label reached a verdict, else sleep it on ``why``."""
        if verdict is None:
            self.sleep(question_id, why)
        else:
            self.wake(question_id)

    def ended_reasons(self) -> dict[str, str]:
        """Each ended path with why it was left out: the reason a question reading it sleeps on."""
        return {p: self.omitted[p] for p in self.ended}

    def paths(self) -> set[str]:
        """Every label path this set wrote or omitted."""
        return {f"{g}.{k}" for g, labels in self.state.items() for k in labels} | set(self.omitted)

    def update(self, other: LabelSet) -> None:
        for group, labels in other.state.items():
            self.state.setdefault(group, {}).update(labels)
        self.omitted.update(other.omitted)
        self.ended.update(other.ended)
        self.no_hold.update(other.no_hold)
        self.figures.update(other.figures)
        self.gates.update(other.gates)
