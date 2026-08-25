"""Layout invariants the render harness is structurally blind to.

The harness SUPPLIES the chart's height instead of deriving it from the page,
so a chart that collapses to zero in a real browser passes every headless
check. That is not hypothetical: it shipped on 2026-08-24. A fixed-height flex
column (height:100dvh + overflow:hidden) made the plot the only shrinkable
item, so the moment the other rows exceeded the viewport it absorbed the whole
overflow and went to nothing. The svg's height:100% then resolved against a
zero-height parent. Correct viewBox, nothing drawn, no error anywhere.

There is no browser in this environment, so these read the CSS directly and
pin the two halves of that lesson as rules rather than as anecdote.
"""
import re
from pathlib import Path

PHONE = (Path(__file__).resolve().parents[1] / "static" / "m" / "index.html").read_text()


def _rule(selector):
    """The declaration block for a rule whose selector STARTS a line.

    Anchored, because an unanchored search for "body" happily matches inside
    "html,body{...}" and then asserts against the wrong block."""
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", PHONE)
    # normalised to single spaces, not stripped of them: collapsing all
    # whitespace turns the shorthand "flex:1 1 0" into "flex:110".
    return re.sub(r"\s*\n\s*", "", re.sub(r"[ \t]+", " ", m.group(1))) if m else None


def test_the_column_may_grow_rather_than_crush_its_children():
    """min-height on the column, never a fixed height with hidden overflow.

    With `height` the column cannot grow, so overflow has to come out of some
    child. With `min-height` the page grows instead and nothing is crushed."""
    body = _rule("body")
    assert body is not None, "body rule not found"
    assert "min-height:100dvh" in body, "the column must size with min-height"
    fixed = re.search(r"(?<!min-)height:100dvh", body)
    assert not (fixed and "overflow:hidden" in body), (
        "height:100dvh with overflow:hidden makes the column fixed, and the "
        "flexible child then absorbs every overflow down to zero"
    )


def test_the_flexible_child_keeps_a_floor():
    """The plot takes the slack AND cannot vanish. Both, not either."""
    chart = _rule(".chart")
    assert chart is not None, ".chart rule not found"
    assert "flex:1 1 0" in chart, "the plot must take the slack"
    m = re.search(r"min-height:(\d+)px", chart)
    assert m and int(m.group(1)) >= 120, (
        "the plot needs a real floor; min-height:0 is what let it collapse"
    )


def test_only_the_plot_is_allowed_to_flex():
    """Every other row is sized by its content, so the slack has one home and
    the layout cannot redistribute in a way nobody designed."""
    assert re.search(r"header,[^{]*\.wall,[^{]*\.key[^{]*\{flex:none\}", PHONE), (
        "header, card and key must be flex:none"
    )


def test_no_element_hangs_its_height_off_a_zero_height_parent():
    """An svg at height:100% inside a container with no definite height
    resolves to nothing. The container must carry a floor (asserted above) and
    the svg must not be what defines it."""
    svg = _rule("svg")
    assert svg is not None and "height:100%" in svg
    chart = _rule(".chart")
    assert "min-height" in chart, (
        "svg{height:100%} is only safe because .chart has a definite minimum"
    )
