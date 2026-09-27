"""A value placed against the same clock window on the prior sessions."""
from __future__ import annotations

from ..cuts import MIN_RANK_SESSIONS
from .words import third


def rank_at_slot(value: float, base: list[float]) -> dict | None:
    """Where ``value`` sits against the same clock window on the prior sessions (``base``, one number per
    session): the third and the count, as the label words them ("in the top third for this minute,
    higher than 15 of 20 prior sessions"). None under MIN_RANK_SESSIONS sessions: too thin to call a
    third, so the label is omitted and the packer skips its question rather than guess."""
    if len(base) < MIN_RANK_SESSIONS:
        return None
    under = sum(1 for b in base if b < value)
    return {"band": f"{third(under / len(base))} third", "higher_than": under, "of": len(base)}
