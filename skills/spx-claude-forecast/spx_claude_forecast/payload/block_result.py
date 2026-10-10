"""What every payload block returns: its facts, and every fact it could not give with the reason why."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BlockResult:
    """``data`` is the block as the payload carries it (``{}`` when the whole block is absent); ``absent`` lists
    ``{"path": "block.field", "why": "..."}`` for every field left out, so Claude never guesses a value."""

    data: dict = field(default_factory=dict)
    absent: list[dict] = field(default_factory=list)

    def leave_out(self, path: str, why: str) -> None:
        self.absent.append({"path": path, "why": why})

    @property
    def is_empty(self) -> bool:
        return not self.data


def whole_block_absent(block: str, why: str) -> BlockResult:
    result = BlockResult()
    result.leave_out(block, why)
    return result
