"""Typed schemas for mirai-right-eye.

Defines the input item, the stored Payload, sub-scores, and the result
returned to the host. Used by every module in the skill.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


GateOutcome = Literal["stored", "duplicate", "below_keep"]
Decision = Literal["push", "digest", "log"]
FeedbackOutcome = Literal["dismissed", "acted_on", "more_like_this"]


class Item(BaseModel):
    """Incoming item passed by the host. Single unit of work."""
    source: str
    url: str
    timestamp: datetime
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    embedding: Optional[list[float]] = None


class SubScores(BaseModel):
    position_impact: int = Field(ge=0, le=35)
    materiality: int = Field(ge=0, le=25)
    surprise: int = Field(ge=0, le=20)
    source_reliability: int = Field(ge=0, le=10)
    time_sensitivity: int = Field(ge=0, le=10)

    @property
    def total(self) -> int:
        return (
            self.position_impact
            + self.materiality
            + self.surprise
            + self.source_reliability
            + self.time_sensitivity
        )


class Classification(BaseModel):
    sector: str
    thesis_or_ticker: Optional[str] = None


class Payload(BaseModel):
    """The stored record. What retrieve() returns to mirai."""
    statement: str
    thesis_or_ticker: str
    sub_scores: SubScores
    total_score: int = Field(ge=0, le=100)
    decision: Decision
    source: str
    timestamp: datetime
    so_what: str


class Result(BaseModel):
    """What process() returns to the host."""
    novelty_score: float = Field(ge=0.0, le=1.0)
    gate_outcome: GateOutcome
    total_score: int = Field(ge=0, le=100)
    sub_scores: SubScores
    classification: Classification
    decision: Decision
    payload: Optional[Payload] = None
    affected_theses: list[str] = Field(default_factory=list)
    item_id: Optional[int] = None
