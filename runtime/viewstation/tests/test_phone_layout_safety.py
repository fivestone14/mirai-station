"""Layout invariants the render harness is structurally blind to.

The harness SUPPLIES the ladder's height instead of deriving it from the page,
so a region that collapses to zero in a real browser passes every headless
check. That shipped once, on 2026-08-24: a fixed-height flex column made the
plot the only shrinkable item, it absorbed the whole overflow and rendered at
nothing, and the svg's height:100% resolved against a zero-height parent.
Correct viewBox, nothing drawn, no error anywhere.

THE MECHANISM CHANGED ON 2026-09-09, so these pin the new one.

The page used to be a fixed-height column with overflow:hidden, and safety came
from every region being flex:none at a constant height whose sum had to stay
under the viewport. That budget was itself the hazard: it produced a region
printing through the footer, a media block that lost every cascade, and a
JS/CSS disagreement about which viewport height to measure. It also made an
unclamped reading impossible, and the reading is the payload.

So the page scrolls now. Safety comes from three things instead, and these are
what this file guards:

  1. The chart's height is a CONSTANT in JS, not derived from anything. There
     is no arithmetic left to get wrong.
  2. Nothing in the column is flexible. A flexible child is the only thing that
     can absorb an overflow, and absorbing it is how the 08-24 collapse
     happened.
  3. A container too NARROW to draw into says so instead of drawing garbage.
     Width is the dimension that can still be zero — a card that has not laid
     out yet, or a hidden parent.
"""
import re
from pathlib import Path

M = Path(__file__).resolve().parents[1] / "static" / "m"
PHONE = (M / "index.html").read_text()
THREAD = (M / "thread.html").read_text()
PAGE = (M / "page.js").read_text()


def _rule(selector, css=None):
    """The declaration block for a rule whose selector STARTS a line.

    Anchored, because an unanchored search for "body" happily matches inside
    "html,body{...}" and asserts against the wrong block. Normalised to single
    spaces, not stripped of them: collapsing whitespace turns the shorthand
    "flex:1 1 0" into "flex:110"."""
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css or PHONE)
    if not m:
        return None
    return re.sub(r"\s*\n\s*", "", re.sub(r"[ \t]+", " ", m.group(1)))


def test_the_chart_height_is_a_constant_with_no_arithmetic_left():
    """The point of the rebuild. sizeLadder used to read the viewport, pick a
    branch, subtract a padding and a five-region sum, then clamp the result —
    five chances to be wrong, and it had been wrong twice. It now assigns a
    number."""
    src = PAGE.split("function sizeLadder")[1].split("\nfunction ")[0]
    assert re.search(r"LADDER_H\s*=\s*\d+\s*;", src), "the height is no longer a literal"
    # Strip the comments before looking. The function explains at length WHY the
    # old budget went, which necessarily names every piece of it — and a test
    # that cannot see past prose teaches you to delete the prose. This file's
    # own header makes that argument; it should not then fall for it.
    code = re.sub(r"//[^\n]*", "", src)
    for gone in ("innerHeight", "clientHeight", "FIXED", "Math.min(560", "padV"):
        assert gone not in code, f"sizeLadder still computes with {gone}"


def test_nothing_in_the_column_is_flexible():
    """A flexible child is the only thing that can absorb an overflow, and on
    2026-08-24 one absorbed all of it and rendered at zero. The page scrolls
    now, so nothing needs to flex — and nothing may."""
    body = _rule("body")
    assert body and "flex-direction:column" in body
    for sel in (".mast", ".regime", ".card", ".read", ".foot"):
        r = _rule(sel)
        assert r is not None, f"{sel} has no rule"
        assert "flex:1" not in r.replace(" ", ""), f"{sel} can absorb an overflow"


def test_the_page_scrolls_and_says_so():
    """The inverse of the test this replaces. overflow:hidden on the body is
    what made a fixed budget mandatory; its absence is what lets the reading go
    unclamped. An edit that reinstates it silently re-clamps the payload."""
    body = _rule("body")
    assert body is not None
    flat = body.replace(" ", "")
    assert "overflow:hidden" not in flat, \
        "the body cannot hide overflow: the reading has no clamp and must be able to run"
    assert "min-height:100dvh" in flat


def test_the_reading_is_never_clamped():
    """Measured against 84 recorded readings: a three-line clamp cuts 47.6% of
    them and a two-line clamp cuts every single one. The median needs six. A
    truncated sentence is indistinguishable from a finished one, which is what
    makes the clamp worse than the scroll it saves."""
    rd = _rule(".rd-line")
    assert rd is not None
    assert "line-clamp" not in rd, "the reading is clamped again"
    assert "16px" in rd, "the reading is no longer at its measured size"
    said = _rule(".said", THREAD)          # the history's copy of the same rule
    assert said is not None and "line-clamp" not in said


def test_a_container_too_narrow_to_draw_into_says_so():
    """Width is the dimension that can still be zero. The old guard measured
    HEIGHT, which is now a constant, so it could never have fired again."""
    src = PAGE.split("function paintLadder")[1].split("\nfunction ")[0]
    assert "CHART TOO NARROW" in src
    # measured RAW, then judged, then clamped. The old line clamped inline with
    # Math.max(240, ...), which erased the very condition worth reporting.
    assert "rawW" in src
    i_raw = src.index("rawW =")
    i_guard = src.index("rawW < 240")
    i_use = src.index("const CW = rawW")
    assert i_raw < i_guard < i_use, "the width is clamped before it is judged"


def test_the_svg_is_sized_by_attribute_not_by_percentage():
    """svg{height:100%} against a parent with no definite height resolves to
    nothing — the 08-24 failure in one line. Width and height are ATTRIBUTES,
    set from the same number the viewBox carries."""
    svg = _rule("#svg")
    assert svg is not None
    flat = svg.replace(" ", "")
    assert "height:100%" not in flat
    assert "height:auto" in flat
    assert "setAttribute('height', SVGH)" in PAGE
    assert "setAttribute('viewBox'" in PAGE
