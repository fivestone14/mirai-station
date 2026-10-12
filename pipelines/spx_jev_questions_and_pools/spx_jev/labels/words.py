"""How a number is written inside a label sentence, the same way in every family."""
from __future__ import annotations

from datetime import datetime

from .measures import ET


def sig(x: float) -> str:
    return f"{x:.2f} sigma"


def sig_beside(x: float, rule: float) -> str:
    """``x`` in sigma beside the rule it is judged against: to three decimals where two would print the rule's own
    figure, so a distance past the rule never reads as equal to it ("0.10 past, more than the 0.10 rule")."""
    return f"{x:.3f} sigma" if x != rule and f"{x:.2f}" == f"{rule:.2f}" else sig(x)


def signed(x: float, nd: int = 2) -> str:
    """A signed number with no '-0.00': a tiny negative rounds to a plain +0.00."""
    r = round(x, nd) or 0.0
    return f"{r:+.{nd}f}"


def above_or_below(d: float) -> str:
    """Which side of a level a signed distance puts price on; at zero, above."""
    return "above" if d >= 0 else "below"


def listed(names: list[str]) -> str:
    """Names as a sentence lists them: "a, b and c"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


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


def hm(t: datetime) -> str:
    """A moment as its market clock, "HH:MM" Eastern."""
    return f"{t.astimezone(ET):%H:%M}"


def minutes_ago(now: datetime, then: datetime) -> str:
    """How long before ``now`` a thing finished, in whole minutes."""
    minutes = int((now - then).total_seconds() // 60)
    return "within the last minute" if minutes == 0 else f"{plural(minutes, 'minute')} ago"
