"""Layout invariants the render harness is structurally blind to.

The harness SUPPLIES the ladder's height instead of deriving it from the page,
so a region that collapses to zero in a real browser passes every headless
check. That shipped once, on 2026-08-24: a fixed-height flex column made the
plot the only shrinkable item, it absorbed the whole overflow and rendered at
nothing, and the svg's height:100% resolved against a zero-height parent.
Correct viewBox, nothing drawn, no error anywhere.

The rebuilt page is safe by a DIFFERENT mechanism, so these pin that mechanism
rather than the old one: no region flexes at all. Every region is flex:none at
a fixed height, and the ladder's height is an explicit pixel value JS computes
from what is left over. There is nothing for an overflow to squeeze.
"""
import re
from pathlib import Path

M = Path(__file__).resolve().parents[1] / "static" / "m"
PHONE = (M / "index.html").read_text()
PAGE = (M / "page.js").read_text()


def _rule(selector):
    """The declaration block for a rule whose selector STARTS a line.

    Anchored, because an unanchored search for "body" happily matches inside
    "html,body{...}" and asserts against the wrong block. Normalised to single
    spaces, not stripped of them: collapsing whitespace turns the shorthand
    "flex:1 1 0" into "flex:110"."""
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", PHONE)
    if not m:
        return None
    return re.sub(r"\s*\n\s*", "", re.sub(r"[ \t]+", " ", m.group(1)))


def test_no_region_can_be_squeezed():
    """Every region is flex:none. An overflow has nothing to take space from,
    so no region can be driven to zero by another region growing."""
    for sel in (".masthead", ".regime", ".ladder", ".gate", ".read", ".foot"):
        r = _rule(sel)
        assert r is not None, f"{sel} rule not found"
        assert "flex:none" in r, f"{sel} must not flex"


def test_the_ladder_height_is_an_explicit_pixel_value():
    """Never flex:1. The height is computed from the viewport minus the fixed
    regions and written as a custom property, so it cannot be negotiated."""
    r = _rule(".ladder")
    assert "height:var(--ladder-h)" in r
    assert "setProperty('--ladder-h'" in PAGE
    assert "clientHeight" in PAGE and "paddingTop" in PAGE  # env() resolved, not guessed
    assert "flex:1" not in r


def test_the_ladder_height_is_clamped_at_both_ends():
    """A floor so it cannot vanish, a ceiling so a tall screen does not hand it
    absurd space that the marks cannot fill."""
    m = re.search(r"Math\.max\((\d+),\s*Math\.min\((\d+)", PAGE)
    assert m, "the clamp is not where it is expected"
    assert int(m.group(1)) >= 180 and int(m.group(2)) <= 700


def test_a_collapse_would_be_visible_rather_than_silent():
    """The explicit height makes a collapse impossible. This makes it LOUD if
    one occurs anyway, because the failure mode last time was silence."""
    assert "LADDER TOO SHORT" in PAGE
    assert re.search(r"getBoundingClientRect\(\)\.height\s*<\s*180", PAGE)


def test_the_svg_is_sized_by_attribute_not_by_percentage():
    """svg{height:100%} against a parent with no definite height resolves to
    nothing. This page sets width and height as ATTRIBUTES from the same number
    the CSS custom property carries."""
    svg = _rule("#svg")
    assert svg is not None and "height:100%" not in svg
    assert "setAttribute('height', SVGH)" in PAGE
    assert "setAttribute('viewBox'" in PAGE


def test_the_page_does_not_scroll():
    body = _rule("body")
    assert "height:100dvh" in body and "overflow:hidden" in body
