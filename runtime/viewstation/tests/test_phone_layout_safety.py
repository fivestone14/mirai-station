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
     out yet, or a hidden parent. That is behaviour of page.js, so it is run
     against the real page in test_phone_route.py; this file keeps to what
     the stylesheet and the shell decide.
"""
import re
from pathlib import Path

import pytest

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
    for sel in (".mast", ".regime", ".card", ".today", ".read", ".foot"):
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
    # the MEASURED height, never a viewport unit — see
    # test_no_phone_page_reads_a_viewport_unit for why
    assert "min-height:var(--app-h)" in flat


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


def test_the_svg_is_sized_by_attribute_not_by_percentage():
    """svg{height:100%} against a parent with no definite height resolves to
    nothing — the 08-24 failure in one line. Width and height are ATTRIBUTES,
    set from the same number the viewBox carries; the page setting them is run
    in test_phone_route's test_a_chart_it_cannot_draw_says_so_and_never_draws_nan,
    so this holds the stylesheet to not overriding them."""
    svg = _rule("#svg")
    assert svg is not None
    flat = svg.replace(" ", "")
    assert "height:100%" not in flat
    assert "height:auto" in flat


def _flat_rules(css):
    """(selector, [declaration, ...]) for every rule with no rule nested in it,
    each declaration normalised to "property:value"."""
    out = []
    for sel, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decls = []
        for d in block.split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decls.append(k.strip() + ":" + " ".join(v.split()))
        out.append((" ".join(sel.split()), decls))
    return out


def _tabular_after_every_font(prefix):
    """Every rule whose selector starts with `prefix` and sets the font
    shorthand must restate tabular-nums after it. -> the selectors checked."""
    body = _rule("body")
    assert body is not None and "font-variant-numeric:tabular-nums" in body.replace(" ", "")
    checked = []
    for sel, decls in _flat_rules(_css_code(PHONE)):
        if not any(part.strip().startswith(prefix) for part in sel.split(",")):
            continue
        fonts = [i for i, d in enumerate(decls) if d.startswith("font:")]
        if not fonts:
            continue
        checked.append(sel)
        assert "font-variant-numeric:tabular-nums" in decls[fonts[-1] + 1:], \
            f"{sel} sets the font shorthand and draws its figures proportional"
    return checked


def test_the_chart_keeps_its_tabular_figures():
    """<body> sets tabular-nums and every chart label then set the font
    shorthand, which resets font-variant-numeric — so the chart drew its prices
    in proportional figures while its gutter was sized for tabular ones. The
    chip's "1,517" drew 28.04px wide against 33.08 tabular, "4,000" 37.62, and
    "09:30" went the other way; the width of a price depended on its digits.

    The fix is a restatement after the shorthand, and the hazard is the next
    chart rule someone writes with a font shorthand and no restatement. The
    render harness cannot see it — it has no fonts — so the rule is held here,
    on every chart rule rather than on the five that exist today."""
    checked = _tabular_after_every_font(".p-")
    # every class the ladder writes a price or a clock in is among those checked
    for need in (".p-chiptx", ".p-tag", ".p-edge", ".p-axis", ".p-word"):
        assert need in checked, f"{need} no longer sets its own font; this proves nothing"


def test_the_activity_panel_keeps_its_tabular_figures():
    """The same fault, on the card under the chart. Every rule in WHERE THE
    ACTIVITY IS set the font shorthand, so the strike column was proportional
    under a layout sized for aligned figures: at 13px/500 "1,510" set 32.05px
    and "1,495" 34.56, so the column's left edge wandered 2.5px down ten rows
    whose right edge is the whole point, and the price chip's 35px floor was
    holding a proportional integer to a column no tabular one fits. Held on
    every .ac- rule, so the next one written with a shorthand cannot drop them."""
    checked = _tabular_after_every_font(".ac-")
    # the classes that print a strike, a count, the price, a clock or a note
    for need in (".ac-rows", ".ac-k", ".ac-more", ".ac-chip", ".ac-note"):
        assert need in checked, f"{need} no longer sets its own font; this proves nothing"


# Advances of the shipped face, pjs-153fc85b7029.woff2, measured in WebKit after
# document.fonts.ready: 12px/400 unless noted. The ladder's fourth column is
# fixed pixels, so whether it fits is decided at the second decimal of these.
# A string the ladder prints that is missing here fails the test that needs it,
# rather than passing unmeasured.
_LADDER_W = {"further above": 76.73, "busy all day": 65.04, "small pile": 52.28,
             "× what was already there": 138.14,
             "0.06×": 32.89,                       # 12px/500 tabular, the widest multiple
             "1×": 13.46, "5×": 13.46}             # 11px/500 tabular, the gauge's scale
GLANCE = (M / "glance.js").read_text()


def _px(sel, prop, css=None):
    m = re.search(r"(?:^|;)" + prop + r":(-?[\d.]+)px", (_rule(sel, css) or "").replace(" ", ""))
    return float(m.group(1)) if m else None


def _ladder(phone):
    """-> (columns, content width, last cell width) for the activity card's grid
    on a phone `phone` px wide: the body's side padding and the card's."""
    tpl = re.search(r"grid-template-columns:([^;]+)", _rule(".ac-rows")).group(1).split()
    assert tpl[-1] == "auto", "the ladder's last cell is no longer the one that takes the rest"
    cols = [float(c[:-2]) for c in tpl[:-1]]
    side = int(re.search(r"padding:calc\(env\([^)]*\)[^)]*\)\s+(\d+)px", _rule("body")).group(1))
    content = phone - 2 * side - 2 * _px(".today", "padding")
    return cols, content, content - sum(cols)


def _ladder_fits(phone):
    """Every cell of the fourth column, at its widest, inside the card's content
    box, and the column's head clear of 'further above' by the card's 12."""
    cols, content, last = _ladder(phone)
    words = re.findall(r"acEl\('ac-tr', '([^']+)'\)", PAGE)
    assert words, "the last cell's words are no longer where this test reads them"
    for word in words:
        assert _LADDER_W[word] <= last, f"{word!r} runs {_LADDER_W[word] - last:.2f}px past the card at {phone}"
    head = re.search(r"const AC_HEAD = \{text: '([^']*)'", PAGE).group(1)
    clear = content - _LADDER_W[head] - (cols[0] + cols[1] + _LADDER_W["further above"])
    assert clear >= 12, f"the head is {clear:.2f}px from 'further above' at {phone}"


def test_the_ladders_fourth_column_is_built_on_its_widest_contents():
    """The fourth column is three fixed cells — the multiple, its gauge, one
    word — with the card's 12px clearance baked into each track the way the
    first three columns bake theirs. Each is sized on the widest thing it can
    hold, not on today's board: the multiple on "0.06×", the word column on
    "busy all day" (the widest word with anything to its right) and on "further
    above", which overhung the old 72px cell by 4.73.

    The gauge's 1× tick is where one turn of the pile lands on its FIXED scale,
    and each label under the column is centred on the mark it names, so moving
    the scale in glance.js without moving the marks fails here."""
    cols, _, _ = _ladder(375)
    track = _px(".ac-gauge", "width")
    assert cols[2] >= _LADDER_W["busy all day"] + 12 and cols[2] >= _LADDER_W["further above"]
    assert cols[3] >= _LADDER_W["0.06×"] + 12
    assert cols[4] == track + 12
    full = int(re.search(r"const FULL_TURNOVER=(\d+);", GLANCE).group(1))
    tick = _px(".ac-gauge::after", "left") + _px(".ac-gauge::after", "width") / 2
    assert tick == pytest.approx(track / full), "the 1× tick is not where one turn lands"
    assert _px(".ac-scale .one", "left") == pytest.approx(cols[3] + tick)
    assert _px(".ac-scale .full", "left") == pytest.approx(cols[3] + track)
    gap = (_px(".ac-scale .full", "left") - _LADDER_W["5×"] / 2) - (_px(".ac-scale .one", "left") + _LADDER_W["1×"] / 2)
    assert gap >= 12
    # nothing added to the ladder is set under 11px
    assert re.search(r"font:500 11px/", _rule(".ac-scale div"))


def test_the_ladder_fits_a_375px_phone():
    """On the phone the card was designed at, the last cell's widest word and
    the column's head both fit inside the content box, with the card's 12px
    between the head and the count's label."""
    _ladder_fits(375)


@pytest.mark.xfail(strict=True, reason="the fourth column is fixed pixels sized for 375: at 320 'small "
                   "pile' runs 47.28px past the content box and 15.28px off the screen (WebKit, "
                   "2026-09-18). Reflowing it is the owner's decision; this passes when it is made.")
def test_the_ladder_fits_a_320px_phone():
    """The smallest phone the page is built for. The six tracks sum to 251 of a
    256px content box, so the last cell has 5px for a 52.28px word. Kept as a
    strict expected failure so the overflow is on the record, not hidden, and
    so this marker has to come off the day it is fixed."""
    _ladder_fits(320)


def test_the_chart_bleeds_to_the_cards_edge_and_no_further():
    """The ladder takes back the card's side padding (2026-09-18), which is 32
    of the 39px the plot gained. By exactly the padding: any more and the chart
    leaves the card for the ground, where the card-coloured halo under an
    in-plot word and the ring round the price dot would paint a colour that is
    no longer behind them — and the card and the ground are 1.17:1 apart, so
    nothing would show where the card had ended.

    The harness supplies the ladder's width, so it cannot see what width a real
    phone gives it. The smallest phone the page is built for is 320px, and there
    the bled ladder has to clear the page's own too-narrow floor."""
    card, ladder, body = _rule(".card"), _rule(".ladder"), _rule("body")
    assert card and ladder and body
    pad = int(re.search(r"padding:(\d+)px", card).group(1))
    m = re.search(r"margin:(\d+)px -(\d+)px 0\b", ladder)
    assert m, "the ladder no longer bleeds out of the card"
    assert int(m.group(2)) == pad, "the ladder's bleed and the card's padding disagree"
    side = int(re.search(r"padding:calc\(env\([^)]*\)[^)]*\)\s+(\d+)px", body).group(1))
    floor = int(re.search(r"rawW\s*<\s*(\d+)", PAGE).group(1))
    assert 320 - 2 * side - 2 * pad + 2 * int(m.group(2)) >= floor, \
        "a 320px phone would say CHART TOO NARROW"


def test_the_tab_bar_is_measured_rather_than_asserted():
    """--tab-h shipped at 64px against a bar that renders 74, so the body
    reserved ten pixels too few for a position:fixed bar and the footer drew
    five pixels underneath it.

    The height is a sum of eight CSS values — an icon, two gaps, a label, a
    dot, item padding and bar padding — every one of which someone can change
    without ever looking at the constant. That is exactly the shape of the
    region budget this rebuild removed, reintroduced at a smaller scale. So the
    number is measured at runtime, and the constant is a first-paint fallback
    that must be at least as large as the bar can be."""
    assert "function fitTabs" in PAGE
    src = PAGE.split("function fitTabs")[1].split("\nfunction ")[0]
    # Strip the comments first, as the chart-height test above does: fitTabs
    # explains at length WHY it measures and names the call while doing it, so
    # an unstripped grep is satisfied by the prose of a body that has replaced
    # the measurement with a constant — the very regression this test names.
    code = re.sub(r"//[^\n]*", "", src)
    assert "getBoundingClientRect" in code, "fitTabs is not measuring anything"
    assert "setProperty('--tab-h'" in code

    # measured on load AND on resize: a rotation changes the safe-area inset the
    # bar pads itself with, and a stale reserve then clips the footer again
    assert PAGE.count("fitTabs()") >= 2, "fitTabs runs once; a resize would leave it stale"
    assert "resize" in PAGE and "fitTabs(); sizeLadder()" in PAGE

    # the fallback must not UNDER-reserve — that is the bug, and a too-small
    # literal reintroduces it for anyone whose script never runs
    m = re.search(r"--tab-h:\s*(\d+)px", PHONE)
    assert m, "--tab-h has no first-paint value"
    assert int(m.group(1)) >= 74, "the fallback under-reserves and would clip the footer"

    # and the inset must not be counted twice: the bar's measured height already
    # contains the safe-area padding it applies to itself
    body = _rule("body")
    pad = body[body.index("padding:"):]
    assert "var(--tab-h)" in pad
    assert "safe-area-inset-bottom" not in pad, \
        "the bottom inset is double-counted: it is already inside the measured bar"


# --- the measured height (2026-09-10) ----------------------------------------
# Inside the app every viewport unit reads ZERO. Measured on a Galaxy S20+
# (Chrome 152 WebView): 100vh = 0 and 100dvh = 0 against a visible height of
# 779px, because Android WebView reports a zero-height viewport to a page when
# the view is sized wrap-content, and the shell's SwipeRefreshLayout gave it
# that by default. The explainer sheet was max-height:84dvh and opened as a
# 32px strip; the reads page's min-height:100dvh has never done anything on a
# phone. Every desktop check passed, because desktop browsers report the real
# height. So the phone pages read one measured number instead, and these pin
# that no viewport unit can creep back in.

_UNIT = re.compile(r"(?<![\w-])\d*\.?\d+(?:d|s|l)?v(?:h|w|min|max|i|b)\b")


def _css_code(html):
    """Every stylesheet in the page with its comments removed, because the
    comments quote the retired units on purpose."""
    css = "\n".join(re.findall(r"(?s)<style>(.*?)</style>", html))
    return re.sub(r"(?s)/\*.*?\*/", "", css)


def test_no_phone_page_reads_a_viewport_unit():
    for name, html in (("index.html", PHONE), ("thread.html", THREAD)):
        css = _css_code(html)
        found = _UNIT.findall(css)
        # the ONE permitted use is the fallback value of the token itself
        assert found == ["100dvh"], f"{name} reads a viewport unit: {found}"
        assert re.search(r"--app-h:100dvh;", css), f"{name} lost the token"
        assert "var(--app-h)" in css, f"{name} declares the token and never reads it"
    for name, js in (("page.js", PAGE),):
        code = "\n".join(l.split("//")[0] for l in js.splitlines()
                         if not l.strip().startswith(("//", "*", "/*")))
        assert not re.search(r"['\"]\d+(?:d|s|l)?vh", code), f"{name} writes a vh"


def _measure_script(html):
    m = re.search(r"(?s)<body>\s*(<script>.*?</script>)", html)
    return m.group(1) if m else None


def test_both_pages_measure_the_height_before_anything_paints():
    """The same script, as the first thing in <body>, on both pages — kept as
    two copies for the same reason the palette is (a shared file is a full
    re-fetch on every open over the tunnel), so the copies are pinned equal."""
    a, b = _measure_script(PHONE), _measure_script(THREAD)
    assert a and b, "a page has lost the script at the top of <body>"
    assert a == b, "the two pages measure the height differently"
    for need in ("window.innerHeight", "setProperty('--app-h'", "'resize'",
                 "'orientationchange'", "visualViewport"):
        assert need in a, need


def test_the_shell_gives_the_webview_a_real_height():
    """The cause, not the symptom: WRAP_CONTENT is what made the WebView report
    a zero-height viewport. Android's WebView guidance is match_parent."""
    kt = (Path(__file__).resolve().parents[3] / "mirai-mobile" / "app" / "src" / "main"
          / "java" / "com" / "mirai" / "mobile" / "MainActivity.kt").read_text()
    code = "\n".join(l.split("//")[0] for l in kt.splitlines())
    assert re.search(r"addView\(web,\s*ViewGroup\.LayoutParams\(\s*ViewGroup\.LayoutParams\.MATCH_PARENT,"
                     r"\s*ViewGroup\.LayoutParams\.MATCH_PARENT\)\)", code), \
        "the WebView is added without MATCH_PARENT params"
    assert "addView(web)" not in code
