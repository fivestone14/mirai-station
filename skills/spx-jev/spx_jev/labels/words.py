"""How a number is written inside a label sentence, the same way in every family."""
from __future__ import annotations

from datetime import datetime


def sig(x: float) -> str:
    return f"{x:.2f} sigma"


def signed(x: float, nd: int = 2) -> str:
    """A signed number with no '-0.00': a tiny negative rounds to a plain +0.00."""
    r = round(x, nd) or 0.0
    return f"{r:+.{nd}f}"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def third(frac: float) -> str:
    if frac < 1 / 3:
        return "bottom"
    if frac > 2 / 3:
        return "top"
    return "middle"


def units_of(x: float, unit_points: float) -> str:
    """``x`` tape units in words, with what one unit is in points, rounded as the sum's context line rounds it."""
    return f"{x:.2f} of a tape unit ({unit_points:.1f} points)" if x <= 1 else f"{x:.2f} tape units (one is {unit_points:.1f} points)"


def minutes_ago(now: datetime, then: datetime) -> str:
    """How long before ``now`` a thing finished, in whole minutes."""
    minutes = int((now - then).total_seconds() // 60)
    return "within the last minute" if minutes == 0 else f"{plural(minutes, 'minute')} ago"
