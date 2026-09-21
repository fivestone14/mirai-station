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
import json
import re
import shutil
import subprocess
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
    for sel in (".mast", ".daymove", ".card", ".today", ".read", ".foot"):
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

    2026-09-19: the same hazard, and the same fix, for what a finger on the
    chart brings up. Both readouts are numbers that change as the finger
    moves, so proportional figures would make them jitter under it.

    The fix is a restatement after the shorthand, and the hazard is the next
    chart rule someone writes with a font shorthand and no restatement. The
    render harness cannot see it — it has no fonts — so the rule is held here,
    on every chart rule rather than on the five that exist today."""
    checked = _tabular_after_every_font(".p-") + _tabular_after_every_font(".lens-") \
        + _tabular_after_every_font(".sc-")
    # every class the ladder writes a price or a clock in is among those checked
    for need in (".p-chiptx", ".p-tag", ".p-edge", ".p-axis", ".p-word", ".p-tradednum", ".p-newword",
                 ".lens-now", ".lens-now b", ".lens-t td", ".sc-read", ".sc-read em"):
        assert need in checked, f"{need} no longer sets its own font; this proves nothing"


def test_no_chart_text_is_under_11px():
    """The chart's words, its time feet and its price ruler were 10px, and each
    of them sat over other ink: the word over the shade, the feet under the
    volume ribbon, the ruler beside the wall bugs. They went to 11px on
    2026-09-18 (INK-SPEC.md 3, SIDE-SPEC.md ruling 6); the ruler's rungs went
    to 20px apart with them, which test_phone_route's
    test_the_ruler_counts_in_round_steps_and_prints_them_exactly holds. Held
    on every chart rule that sets a size, so the next one cannot come in under
    it."""
    sized = {}
    for sel, decls in _flat_rules(_css_code(PHONE)):
        if not sel.startswith(".p-"):
            continue
        for d in decls:
            m = re.match(r"(?:font:(?:[^;]*?\s)?|font-size:)([\d.]+)px", d)
            if m:
                sized[sel] = float(m.group(1))
    for need in (".p-word", ".p-axis", ".p-scale", ".p-edge", ".p-tag", ".p-chiptx", ".p-tradednum", ".p-newword"):
        assert need in sized, f"{need} no longer sets its own size; this proves nothing"
    small = {sel: px for sel, px in sized.items() if px < 11}
    assert not small, f"chart text under 11px: {small}"


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
# document.fonts.ready: 12px/400 unless noted. Whether the ladder fits is decided
# at the second decimal of these. A string the ladder prints that is missing
# here fails the test that needs it, rather than passing unmeasured.
_LADDER_W = {"further above": 76.73, "further below": 76.09,
             "got busy": 49.68, "busy all day": 65.04, "went quiet": 60.06,
             "small pile": 52.28, "faster": 33.18, "steady": 39.08, "slower": 37.48,
             "× what was already there": 138.14, "trading now against earlier": 148.62,
             "09:38": 32.23,                       # a gone row's time, tabular
             "0.06×": 32.89,                       # 12px/500 tabular, the widest multiple
             "1×": 13.46, "5×": 13.46}             # 11px/500 tabular, the gauge's scale
# the head a pixel smaller, 11px/400, where the card is too narrow for it at 12
_HEAD_11 = {"× what was already there": 126.63, "trading now against earlier": 136.24}
GLANCE = (M / "glance.js").read_text()
_NODE = shutil.which("node")


def _side():
    """The glance's own side padding, the body's, past the safe-area inset."""
    return int(re.search(r"padding:calc\(env\([^)]*\)[^)]*\)\s+(\d+)px", _rule("body")).group(1))


def _px(sel, prop, css=None):
    m = re.search(r"(?:^|;)" + prop + r":(-?[\d.]+)px", (_rule(sel, css) or "").replace(" ", ""))
    return float(m.group(1)) if m else None


def _pct(sel, prop):
    """A length set as a share of its box, "20%" or "calc(20% - .5px)", as
    (percent, px taken off)."""
    m = re.search(r"(?:^|;)" + prop + r":(?:calc\()?([\d.]+)%(?:-([\d.]+)px\))?", _rule(sel).replace(" ", ""))
    return float(m.group(1)), float(m.group(2) or 0)


def _head():
    """The fourth column's head as page.js declares it: (text, width at 12px)."""
    m = re.search(r"const AC_HEAD = \{text: '([^']*)', w: ([\d.]+)", PAGE)
    assert m, "AC_HEAD no longer carries its text and its width where this test reads them"
    return m.group(1), float(m.group(2))


def _ladder(phone):
    """-> (grid, content width) for the activity card on a phone `phone` px wide:
    the content box the body's side padding and the card's leave, and the columns
    activityGrid in the real glance.js gives it, run in node — the page sets them
    from that one function, on the card's measured width. Skips when node is not
    installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    content = phone - 2 * _side() - 2 * _px(".today", "padding")
    js = "const g=require(%s);console.log(JSON.stringify(g.activityGrid(%s, %s)));" % (
        json.dumps(str(M / "glance.js")), content, _head()[1])
    out = subprocess.run([_NODE, "-e", js], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout), content


def _ladder_rows(phone):
    """-> ({row: [(what, left, right)]}, content width): every text and mark each
    kind of ladder row can hold, each at its widest, where the grid puts it on a
    phone `phone` px wide. The figure and the dot are left out: they never move."""
    grid, content = _ladder(phone)
    at = [sum(grid["cols"][:i]) for i in range(6)]         # each track's left edge
    track = grid["track"]
    head, head_w = _head()
    if grid["head"] == "smaller":
        head_w = _HEAD_11[head]
    states = re.search(r"const AC_WORD = \{new: '([^']+)', held: '([^']+)', gone: '([^']+)'\}", PAGE).groups()
    counts = dict(re.findall(r"acMore\(map\.more(Above|Below), '([^']+)'", PAGE))
    # the warning, and the three words pace() in glance.js can return
    lasts = re.findall(r"const last = r\.thin \? '([^']+)'", PAGE)
    lasts += re.findall(r"'([a-z]+)'", GLANCE.split("function pace(")[1].split("\nfunction ")[0])
    assert len(states) == 3 and len(counts) == 2 and len(lasts) == 4, \
        "the ladder's words are no longer where this test reads them"

    def cell(text, x):
        return (text, x, x + _LADDER_W[text])

    rows = {}
    top = [cell(counts["Above"], at[2])]
    if grid["head"] == "alone":
        rows["the head's own row"] = [(head, content - head_w, content)]
    else:
        top.append((head, content - head_w, content))
    rows["+N further above"] = top
    gauge = [("the gauge", at[4], at[4] + track)] if track else []
    for state in states[:2]:
        for last in lasts:
            rows[f"{state} … {last}"] = [cell(state, at[2]), cell("0.06×", at[3])] + gauge + [cell(last, at[5])]
    rows["gone"] = [cell(states[2], at[2]), cell("09:38", at[3])]
    scale = []
    if track:
        for label, sel in (("1×", ".ac-scale .one"), ("5×", ".ac-scale .full")):
            mid = at[4] + _pct(sel, "left")[0] / 100 * track
            scale.append((label, mid - _LADDER_W[label] / 2, mid + _LADDER_W[label] / 2))
    rows["+N further below"] = [cell(counts["Below"], at[2])] + scale
    return rows, content


def test_the_ladders_fourth_column_is_built_on_its_widest_contents():
    """The fourth column is three cells — the multiple, its gauge, one word —
    with the card's 12px clearance baked into each track the way the first three
    columns bake theirs. Each is sized on the widest thing it can hold, not on
    today's board: the multiple on "0.06×", the word column on "busy all day"
    (the widest word with anything to its right) and on "further above", which
    overhung the old 72px cell by 4.73.

    The gauge's 1× tick is where one turn of the pile lands on its FIXED scale,
    at whatever length the card gives the track, and each label under the column
    is centred on the mark it names, so moving the scale in glance.js without
    moving the marks fails here. The track is never shorter than GRID_TRACK_MIN,
    and that floor is where the gauge still reads as a ratio: one turn more than
    the 6.6px GAUGE-SPEC found stops reading as a mark, "1×" starting inside the
    track rather than off its zero, and the card's 12 between "1×" and "5×"."""
    grid, _ = _ladder(375)
    cols, track = grid["cols"], grid["track"]
    assert cols[2] >= _LADDER_W["busy all day"] + 12 and cols[2] >= _LADDER_W["further above"]
    assert cols[3] >= _LADDER_W["0.06×"] + 12
    assert cols[4] == track + 12
    assert "width" not in (_rule(".ac-gauge") or ""), "the stylesheet fixes the track the card sizes"

    full = int(re.search(r"const FULL_TURNOVER=(\d+);", GLANCE).group(1))
    pct, off = _pct(".ac-gauge::after", "left")
    assert pct / 100 - off / track + _px(".ac-gauge::after", "width") / 2 / track == pytest.approx(1 / full), \
        "the 1× tick is not where one turn lands"
    assert _pct(".ac-scale .one", "left") == (100 / full, 0), "1× is not on its tick"
    assert _pct(".ac-scale .full", "left") == (100, 0), "5× is not on the track's end"

    floor = int(re.search(r"GRID_TRACK_MIN=(\d+);", GLANCE).group(1))
    for t in (track, floor):
        assert t / full > 6.6, f"one turn on a {t}px track is too near the zero to read as a mark"
        assert t / full - _LADDER_W["1×"] / 2 >= 0, f"'1×' hangs off the zero of a {t}px track"
        assert (t - _LADDER_W["5×"] / 2) - (t / full + _LADDER_W["1×"] / 2) >= 12, \
            f"'1×' and '5×' run together on a {t}px track"
    # nothing added to the ladder is set under 11px
    assert re.search(r"font:500 11px/", _rule(".ac-scale div"))
    assert _px(".ac-head.smaller", "font-size") == 11


def test_the_ladder_is_as_drawn_on_a_375px_phone():
    """The reflow below 375 is a function of the card's width, and on the phone
    the card was designed at it has to come out as drawn: every x GAUGE-SPEC and
    RATE-SPEC placed and the previous commits measured in WebKit, to a quarter
    of a pixel. The multiple at 148, the track 193 to 239 with its tick on 202.2,
    the last cell from 251 with 60 to hold its word, the head right-aligned to
    311 from 162.38, and the scale's 1× and 5× on their marks."""
    grid, content = _ladder(375)
    assert content == 311 and grid["head"] == "beside"
    cols, track = grid["cols"], grid["track"]
    x3, x4, x5 = sum(cols[:3]), sum(cols[:4]), sum(cols)
    pct, off = _pct(".ac-gauge::after", "left")
    tick = x4 + pct / 100 * track - off + _px(".ac-gauge::after", "width") / 2
    drawn = {"the multiple": (x3, 148), "the track's start": (x4, 193), "its end": (x4 + track, 239),
             "the 1× tick": (tick, 202.2), "the last cell": (x5, 251), "its width": (content - x5, 60),
             "the head": (content - _LADDER_W[_head()[0]], 162.38),
             "1×": (x4 + _pct(".ac-scale .one", "left")[0] / 100 * track, 202.2),
             "5×": (x4 + _pct(".ac-scale .full", "left")[0] / 100 * track, 239)}
    for what, (got, want) in drawn.items():
        assert got == pytest.approx(want, abs=0.25), f"{what} moved at 375: {got:.2f}, drawn at {want}"


@pytest.mark.parametrize("phone", [320, 360, 375, 390, 412])
def test_the_ladder_fits_the_phone(phone):
    """No two things in a ladder row, the head included, closer than the card's
    12px, and nothing past the card's content edge, on every phone width that
    matters: 320, the smallest the page is built for; 360, the owner's own
    Galaxy S20+; 375, where the card was drawn; and 390 and 412, today's
    common widths.

    These were strict expected failures until 2026-09-18. With six fixed tracks
    summing to 251, "small pile" ran 7.28px into the card's padding at 360, and
    the head started 1.27px after "further above", so the row read as one run
    of words; at 320 the head ran 8.62px off the screen. activityGrid now gives
    up width in order — the gauge's length, then the gaps beside it, then the
    gauge — and moves the head, which no column can make room for, a pixel
    smaller or onto a row of its own."""
    rows, content = _ladder_rows(phone)
    for name, row in rows.items():
        row = sorted(row, key=lambda t: t[1])
        assert row[0][1] >= 0, f"{row[0][0]!r} starts off the card's left edge at {phone}"
        assert row[-1][2] <= content, \
            f"{row[-1][0]!r} runs {row[-1][2] - content:.2f}px past the content edge at {phone} ({name})"
        for a, b in zip(row, row[1:]):
            assert b[1] - a[2] >= 12, \
                f"{a[0]!r} and {b[0]!r} are {b[1] - a[2]:.2f}px apart at {phone} ({name})"


# THE LAST HALF HOUR, measured the same way, each variable line at the widest
# form it can take: the clock from 09:30 to 15:30, a move under $1,000, a usual
# half hour under $100, and every count and span under 1,000. The larger bucket
# held 195 after 34 sessions, so 1,000 is some 140 sessions off. Tracked labels
# are ink: the 11px/700 box less its trailing .12em (1.32).
_HALF_W = {"THE LAST HALF HOUR": 136.83, "SINCE 09:40": 82.09,
           "EARLIER HALF HOURS": 139.14, "400 TRADING DAYS": 124.69,
           "Down $400 in the last half hour.": 221.18,                 # 15px
           "A usual half hour on this stock is $40.": 255.20,          # 15px
           "That was no bigger than usual. So were 400 earlier": 280.18,
           "half hours. Here is what came next each time:": 251.24,
           "Nothing past that next half hour was measured.": 263.20,
           "400": 27.12,                                               # 13px/500, a count
           " ": 2.05,
           "went back up": 75.96, "went back down": 93.26, "kept going up": 78.71,
           "kept going down": 96.01, "went the other way": 106.72, "went the same way": 107.26}


def _half_rows(phone):
    """-> ({row: [width of each thing on it]}, {row: the gap its box keeps},
    content width) for THE LAST HALF HOUR on a phone `phone` px wide. Every
    caption vocabulary glance.js can print, each count at its widest."""
    content = phone - 2 * _side() - 2 * _px(".card", "padding")
    code = GLANCE.split("function halfHour(")[1].split("\n}")[0]
    pairs = re.findall(r"dir==='Up' \? '([a-z ]+)' : dir==='Down' \? '([a-z ]+)' : '([a-z ]+)'", code)
    assert len(pairs) == 2, "the captions are no longer where this test reads them"
    cap = lambda words: _HALF_W["400"] + _HALF_W[" "] + _px(".hh-out b", "margin-right") + _HALF_W[words]
    rows = {"the card's head": [_HALF_W["THE LAST HALF HOUR"], _HALF_W["SINCE 09:40"]],
            "the record's head": [_HALF_W["EARLIER HALF HOURS"], _HALF_W["400 TRADING DAYS"]]}
    gaps = dict.fromkeys(rows, _px(".lab", "gap"))
    for other, same in zip(*pairs):
        rows[f"{other} / {same}"] = [cap(other), cap(same)]
        gaps[f"{other} / {same}"] = _px(".hh-out", "gap")
    for line in ("Down $400 in the last half hour.", "A usual half hour on this stock is $40.",
                 "That was no bigger than usual. So were 400 earlier",
                 "half hours. Here is what came next each time:",
                 "Nothing past that next half hour was measured."):
        rows[line], gaps[line] = [_HALF_W[line]], 0
    return rows, gaps, content


@pytest.mark.parametrize("phone", [360, 375, 390, 412])
def test_the_half_hour_card_keeps_every_line_whole_on_the_phone(phone):
    """From the owner's 360px Galaxy up, every line of THE LAST HALF HOUR stays
    on one line at its widest, and the two pairs that share a line — each head
    and its scope, the two captions — keep the gap their row sets. The captions
    are the tight one: at 360 the two flat-board captions with two counts of
    400 need 294.32 of 296. Measured in WebKit off the shipped face on the real
    page, the card is 266px tall at 360, 375, 390 and 412 on the four real
    boards and on two built wide: a $119 move over counts of 408, 208 and 200,
    and the same counts on a flat board."""
    # the sizes _HALF_W was measured at; a change of size has to re-measure
    for sel, font in ((".lab", "font:700 11px/1.2"), (".hh-say", "font:400 15px/24px"),
                      (".hh-set", "font:400 12px/18px"), (".hh-out", "font:400 12px/18px"),
                      (".hh-note", "font:400 12px/18px")):
        assert font in _rule(sel), f"{sel} is no longer set at {font}"
    assert "letter-spacing:.12em" in _rule(".lab").replace(" ", "")
    assert "font-size:13px;font-weight:500" in _rule(".hh-out b").replace(" ", "")
    rows, gaps, content = _half_rows(phone)
    for name, row in rows.items():
        need = sum(row) + gaps[name] * (len(row) - 1)
        assert need <= content, f"{name!r} needs {need:.2f} of {content} at {phone}"


def test_a_narrower_phone_wraps_the_half_hour_card_and_never_runs_it_off():
    """Under 340 the card grows rather than overflowing, in the order SPLIT-SPEC
    drew: the 12px framing line wraps first, then the record's scope drops to a
    line of its own, right-aligned, with the label left whole, and the captions
    each wrap in their own box. So nothing here may be held to one line or a
    fixed width. The card's own head still fits at 320, where the record's does
    not. Measured at 320 on the four real boards: nothing past the card, no two
    texts touching, the card 323px tall, 341 on a flat board."""
    rows, gaps, content = _half_rows(320)
    head, rec = rows["the card's head"], rows["the record's head"]
    assert sum(head) + gaps["the card's head"] <= content
    assert sum(rec) + gaps["the record's head"] > content
    assert "flex-wrap:wrap" in _rule(".hh-rec").replace(" ", "")
    assert "white-space:nowrap" in _rule(".lab .r").replace(" ", "")
    for sel, decls in _flat_rules(_css_code(PHONE)):
        if ".hh" in sel:
            assert not any(d.startswith(("white-space:nowrap", "width:", "flex:none")) for d in decls), sel
    assert "display:flex" in _rule(".hh-out").replace(" ", "") and _px(".hh-out", "gap") >= 12


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
    floor = int(re.search(r"rawW\s*<\s*(\d+)", PAGE).group(1))
    assert 320 - 2 * _side() - 2 * pad + 2 * int(m.group(2)) >= floor, \
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


# --- the reads page's explainer button (2026-09-18) --------------------------
# Measured in WebKit off the shipped face on the real page: the label at
# 13px/700 and the chevron at 17px/400. The balanced two-line label at 320 is
# "What faster, steady" over "and slower mean", the longer 123.78.
_WHY_W = {"What faster, steady and slower mean": 232.32, "›": 5.11, "balanced": 123.78}


def _why_room(phone):
    """The label's room on the button: the page less the margins, the padding,
    the ring, the two gaps and the chevron."""
    why = _rule(".why", THREAD).replace(" ", "")
    assert "width:calc(100%-32px);margin:4px16px12px;padding:016px015px" in why, why
    return (phone - 32 - 16 - 15 - _px(".why i", "width", THREAD) - 2 * _px(".why", "gap", THREAD)
            - _WHY_W["›"])


@pytest.mark.parametrize("phone", [343, 360, 375, 390, 412])
def test_the_explainer_button_keeps_its_label_on_one_line(phone):
    """The button under the reads page's header says what it opens on one
    line from a 343px phone up, the owner's 360px Galaxy included: 232.32 of
    text in 249.89 there, 264.89 at 375. A <button> shrinks to its content
    whatever its display, so its width is set: the page less its 16px
    margins. Measured in WebKit at 320, 360, 375 and 412: the button 48 tall,
    nothing past the page's 16px gutter, nothing touching."""
    assert "font:700 13px/1" in _rule(".why", THREAD) and "font:400 17px/1" in _rule(".why span", THREAD)
    assert "height:var(--tap-min)" in _rule(".why", THREAD)
    assert _WHY_W["What faster, steady and slower mean"] <= _why_room(phone)


def test_a_narrower_phone_gives_the_explainer_label_two_even_lines():
    """Under 343px the label wraps rather than running under the chevron, and
    it wraps evenly: at 320 "What faster, steady" over "and slower mean", not
    "mean" alone. Two lines at 1.25 are 32.5px, inside the button's 48."""
    lab = _rule(".why s", THREAD).replace(" ", "")
    assert _WHY_W["What faster, steady and slower mean"] > _why_room(342)
    assert "min-width:0" in lab and "white-space" not in lab and "text-wrap:balance" in lab
    assert _WHY_W["balanced"] <= _why_room(320)
    line = float(re.search(r"line-height:([\d.]+)", lab).group(1)) * 13
    assert 2 * line <= 48


# --- the chart's key, behind a link under the chart (2026-09-18) --------------
# Measured in WebKit off the shipped face on the real page: the label at
# 12px/600, and the link's box at 320, 360, 375 and 412 (164.9 by 48 at x 24).
_HOWTO_W = {"How to read this chart": 125.9}


@pytest.mark.parametrize("phone", [320, 360, 375, 412])
def test_the_chart_key_link_is_a_whole_tap_target_on_every_phone(phone):
    """The owner chose the quiet link over a filled bar (READABLE2-SPEC.md 2.8):
    16px of it shows, a 16px ring and the label at 12px/600 in --i-mute, on the
    card's own rail. The target is still --tap-min tall, its 16px of padding
    above and below tucked into the space the chart and the card's own padding
    already leave, so the card grows 22px where a bar grew it 56. Its width is
    its content, which fits the card at every phone, the owner's 360 included:
    164.9px of 256 at 320."""
    link, ring = _rule(".howto").replace(" ", ""), _rule(".howto i").replace(" ", "")
    m = re.search(r"margin:(-?\d+)px0(-?\d+)px(-?\d+)px;padding:(\d+)px(\d+)px", link)
    assert m, link
    top, bottom, left, pad_y, pad_x = map(int, m.groups())
    line = int(re.search(r"font:60012px/(\d+)px", link).group(1))
    assert "color:var(--i-mute)" in link and "width:max-content" in link
    # the target: --tap-min, 48, the shipped value
    assert 2 * pad_y + line == int(re.search(r"--tap-min:(\d+)px", PHONE).group(1)) == 48
    assert _px(".howto i", "height") == _px(".howto i", "width") == line
    # what it costs the card: its box less the space it tucks into
    card_pad = int(re.search(r"padding:(\d+)px", _rule(".card")).group(1))
    assert -bottom == card_pad and 2 * pad_y + line + top + bottom == 22
    # the ring sits on the card's rail: the padding and the pull cancel
    assert left == -pad_x
    width = pad_x + line + _px(".howto", "gap") + _HOWTO_W["How to read this chart"] + pad_x
    assert width == pytest.approx(164.9, abs=0.05)
    assert width <= phone - 2 * _side() - 2 * card_pad + pad_x


def test_the_control_that_opens_the_chart_costs_the_cards_head_nothing():
    """The corner control is the only thing on the glance that says the chart
    can be opened (ZOOM-SPEC.md 5), and it is 16px of icon with the same
    --tap-min target the key's link has. It sits in the head's own row, so its
    48px are tucked back out of it: 18px a side against the head's 13.2px line
    leaves 12, under the line, so the row keeps its height. At 16 a side the
    card ran 3px taller on every phone, which is the whole card column moving
    for one icon. The right-hand pull is the card's rail, so the icon's edge is
    where the head's clock ends."""
    b = _rule(".cf-open").replace(" ", "")
    m = re.search(r"margin:(-?\d+)px(-?\d+)px(-?\d+)px0;padding:(\d+)px", b)
    assert m, b
    top, right, bottom, pad = map(int, m.groups())
    icon = _px(".cf-open svg", "height")
    tap = int(re.search(r"--tap-min:(\d+)px", PHONE).group(1))
    assert _px(".cf-open svg", "width") == icon == 16
    assert 2 * pad + icon == tap == 48
    assert "font:700 11px/1.2" in _rule(".lab") and top == bottom
    assert 2 * pad + icon + top + bottom <= 11 * 1.2, "the control makes the card's head taller"
    assert -right == int(re.search(r"padding:(\d+)px", _rule(".card")).group(1))
    assert "touch-action:manipulation" in b


def test_the_chart_full_screen_is_a_sheet_that_takes_the_whole_screen():
    """It is a sheet so that every way out of it is the one sheet.js knows —
    Back, the close control, Escape — and so that it and the key cannot both be
    open. What it overrides is only its SHAPE: the rounded lip that says "part
    of a screen", the 86% cap, and the sheet's own padding, which the chart
    takes for itself. Everything else it keeps, including overflow-y:auto, so a
    foot one line longer than the room it was measured for scrolls into view
    rather than falling off the bottom.

    The close control is --tap-min and sits in the top right, inside the strip
    Android gives its back gesture. That is deliberate: a tap there closes the
    view and a swipe there is Back, which closes it too, so the two cannot
    disagree."""
    full = _rule(".sheet.full").replace(" ", "")
    for need in ("top:0", "max-height:none", "border-radius:0", "padding:0",
                 "display:flex", "flex-direction:column"):
        assert need in full, need
    base = _rule(".sheet").replace(" ", "")
    for kept in ("position:fixed", "overflow-y:auto", "overscroll-behavior:contain", "user-select:none"):
        assert kept in base and kept not in full, kept
    assert "transform" not in full, "the sheet's own way in and out was overridden"
    close = _rule(".cf-x").replace(" ", "")
    assert "width:var(--tap-min)" in close and "height:var(--tap-min)" in close, close
    # the foot is held to the chart's own floor, and the two insets are kept
    assert int(re.search(r"font:400 (\d+)px", _rule(".cf-foot")).group(1)) >= 11
    assert "env(safe-area-inset-bottom)" in _rule(".cf-foot")
    assert "env(safe-area-inset-top)" in _rule(".cf-head")


def test_the_table_under_the_chart_scrolls_rather_than_pushing_the_foot_off():
    """The view is a fixed column — head, chart, table, foot — and the table is
    the one part that can be taller than the room. It is the only flexible item
    in it, and min-height:0 is what lets a flex item be SHORTER than its own
    content: without it the table would grow to its rows and push the foot off
    the bottom of the screen (FULL2-SPEC.md 4.4, where the 09-17 board lists 15
    prices and 10 of them fit whole).

    overscroll-behavior:contain, because the sheet behind it scrolls too and a
    flick that runs out of table would otherwise start moving the sheet."""
    tw = _rule(".cf-tw").replace(" ", "")
    assert "flex:11auto" in tw, "the table cannot take the room the chart left"
    assert "min-height:0" in tw, "the table cannot be shorter than its rows, so the foot goes"
    assert "overflow-y:auto" in tw and "overscroll-behavior:contain" in tw, tw
    # and everything above and below it is fixed, or the room is not the
    # table's to take
    for sel in (".cf-head", "#cfSvg", ".cf-foot"):
        assert "flex:none" in _rule(sel).replace(" ", ""), sel


def test_the_column_names_stay_on_the_screen_while_the_rows_scroll():
    """10 whole rows fit the table's box at 360x780, with a sliver of the 11th
    under them, and 263 of the 514 scans of 2026-09-15..17 — 51% — list 15
    prices or more, the worst of them 19. So on more than half of all boards
    the reader scrolls, and until 2026-09-20 both heading rows left the screen
    when they did: six columns of bare figures with nothing saying which one
    was the pile and which was what traded. (12 fitted while the chart took
    0.46 of the screen the head and the foot leave and 11 at 0.50; since
    2026-09-21 the box is counted in whole rows and the line the foot gained
    to say the list runs past it costs the last of them. This file said 14,
    which was reachable only by counting
    the cut row on a short-footed board. Measured on the 514.)

    Both rows stick to the top of the box instead. What this holds is every
    part of that which can go wrong on its own:

    - the group row parks at the box's top and the column row directly under
      it, at the group row's OWN height. Never over it: a larger offset pushes
      the column names down by the difference even at rest and leaves a slot
      for the rows to scroll through, and at 18.5 against a group row of 18 a
      0.5px slit of moving figures showed between the two headings on the
      19-price board. Never more than a pixel under it either, or the row
      above shows over the column names. The browser rounds the group row's
      13.2px line box down, which is why 18 is right for a box of 18.2;
    - each is opaque in the sheet's own colour, or the rows read through them;
    - each is above the rows, or the rows' own 1px rules draw over them, and
      the group row is above the column row, or the column row's background
      hides the group's rule — which is the whole cue that the group words
      head a PAIR of columns;
    - and the rules are box-shadows: a border under border-collapse belongs to
      the table's grid rather than to the cell, and does not travel with a
      stuck one."""
    grp, col = _rule(".cf-grp th").replace(" ", ""), _rule(".cf-col th").replace(" ", "")
    for sel, decls in ((".cf-grp th", grp), (".cf-col th", col)):
        assert "position:sticky" in decls, f"{sel} scrolls away with its rows"
        assert "background:var(--s)" in decls, f"{sel} is not opaque, so the rows read through it"
    assert re.search(r"(?:^|;)top:0(?:px)?(?:;|$)", grp), \
        "the group words do not park at the top of the box"
    # the group row's own box: its type on its own padding, which is what the
    # column row parks under. A change to either row's padding or size fails
    # here rather than on the phone.
    size, lead = re.search(r"font:700 ([\d.]+)px/([\d.]+) ", _rule(".cf-grp th")).groups()
    top, bottom = re.search(r"padding:(\d+)px 0 (\d+)px", _rule(".cf-grp th")).groups()
    box = float(size) * float(lead) + int(top) + int(bottom)
    parks = _px(".cf-col th", "top")
    assert box - 1 <= parks <= box, \
        f"the column names park {parks} down a group row {box} tall"
    z_grp, z_col = int(re.search(r"z-index:(\d+)", grp).group(1)), \
        int(re.search(r"z-index:(\d+)", col).group(1))
    assert z_col > 0 and z_grp > z_col, f"group {z_grp}, column {z_col}"
    assert "box-shadow:01px0var(--s2)" in grp and "box-shadow:01px0var(--s2)" in col, (grp, col)
    assert "border-bottom" not in grp, "a collapsed border does not travel with a stuck cell"
    # the two group cells that head no pair take no rule
    assert _rule(".cf-grp th:first-child,.cf-grp th.c-pace").replace(" ", "") == "box-shadow:none"


def test_the_tables_columns_are_the_same_width_on_every_board():
    """A column that reflowed per board would give up half the chart's height
    for nothing: the figures line up down the page or they do not. So the
    table is table-layout:fixed with its tracks written from JS, and the cells
    restate tabular figures after the font shorthand that resets them — the
    2026-09-09 hazard, on the one screen that is a column of counts.

    The gutter is the one number in this layout the stylesheet and glance.js
    both have to know: the tracks are fitted into the width less two of them
    (TABLE_NARROW), so the two are held equal here."""
    tb = _rule(".cf-tb").replace(" ", "")
    assert "table-layout:fixed" in tb and "border-collapse:collapse" in tb, tb
    td = _rule(".cf-tb td")
    assert td is not None
    decls = [d for d in _flat_rules("x{" + td + "}")[0][1]]
    fonts = [i for i, d in enumerate(decls) if d.startswith("font:")]
    assert fonts and "font-variant-numeric:tabular-nums" in decls[fonts[-1] + 1:], \
        "the table's figures are proportional, so its columns do not line up"
    pad = re.search(r"padding:0 (\d+)px", _rule(".cf-tw"))
    assert pad and int(pad.group(1)) == int(re.search(r"TABLE_PAD=(\d+)", GLANCE).group(1)), \
        "the stylesheet's gutter and the width the tracks are fitted into disagree"


def test_the_count_headings_sit_over_the_counts_they_name():
    """A count cell is two figures — what traded today and what traded there
    since the reading — and the count ends where the since's own track begins.
    PUTS and CALLS were right-aligned to the whole cell, so each sat 36px to
    the right of the column it names, squarely over the grey `+307`
    (LOOK-REVIEW.md 5). The heading is inset by that track and the gap before
    it, and both come from one pair of properties, so a change to the gap
    moves the heading with it rather than leaving it behind.

    THE OTHER HALF IS THAT `899 +307` read as one number, the two inks being
    1.09:1 apart and 11px the floor under the since (LOOK-REVIEW.md 6). SPACE
    IS ZERO-SUM HERE: the inset is capped by PRICE's own clearance, so the gap
    inside a cell and the gap from its "+n" to the next column's count sum to
    a constant, and every pixel put between the pair is taken from what keeps
    the pair apart from the column beside it. 4px is the most the row can pay
    with both still at or over the house 12. The rest is weight — 400 against
    the count's 600 — which costs no space and is the step that survives
    greyscale, where the two inks are the same tone.

    THE ARITHMETIC IS RECOMPUTED HERE rather than pinned, from glance.js's own
    tracks and the figures measured in WebKit with the shipped face, at 360 —
    the owner's phone, and the tightest of the four this is checked on, since
    320 drops the pace column and hands its room to these two. Three things
    have to hold at once and they pull on the same pixels: the heading fits
    inside its own track, it clears PRICE's by 12, and the pair clears the
    next column's count by 12."""
    PRICE_W, PUTS_W, CALLS_W = 36.92, 31.63, 39.81
    COUNT_W, SINCE_W = 30.02, 26.47      # "9,192" at 600, "+975" at 400
    tb = _rule(".cf-tb").replace(" ", "")
    since = float(re.search(r"--since:([\d.]+)px", tb).group(1))
    gap = float(re.search(r"--since-gap:([\d.]+)px", tb).group(1))
    assert "gap:var(--since-gap)" in _rule(".cf-cell").replace(" ", ""), \
        "the cell's gap is no longer the one the heading is inset by"
    cell_s = _rule(".cf-cell .s").replace(" ", "")
    assert "min-width:var(--since)" in cell_s, \
        "the since's track is no longer the one the heading is inset by"
    assert "font-weight:400" in cell_s, \
        "the since is back at the count's own weight, so greyscale has no step left"
    assert _rule(".cf-col th.c-count").replace(" ", "") \
        == "padding-right:calc(var(--since)+var(--since-gap))", \
        "the heading is inset by something other than the track it sits beside"
    heads = re.findall(r'<tr class="cf-col">(.*?)</tr>', PHONE, re.S)[0]
    assert re.findall(r'<th class="c-count">(\w+)</th>', heads) == ["PUTS", "CALLS"], heads
    assert since >= SINCE_W, \
        f"the since's track is narrower than the widest one of 2026-09-15..17 ({SINCE_W})"
    assert gap > 3, "the count and the since are back to one word-space apart"

    pad, price, pile, turn, pace, count = (
        int(re.search(r"TABLE_%s=(\d+)" % n, GLANCE).group(1))
        for n in ("PAD", "PRICE", "PILE", "TURN", "PACE", "COUNT"))
    col = (360 - 2 * pad - price - pile - turn - pace) / 2
    assert col >= count, f"the count columns are under their own floor at 360: {col}"
    inset = since + gap
    assert CALLS_W + inset <= col, \
        f"CALLS and the since's track want {CALLS_W + inset}px of a {col}px column"
    # PRICE is left-aligned from the gutter, PUTS right-aligned to its counts
    clear = (pad + price + col - inset - PUTS_W) - (pad + PRICE_W)
    assert clear >= 12, f"PRICE and PUTS are {round(clear, 2)}px apart, under the house 12"
    # and the widest "+n" to the widest count of the column after it
    between = col - gap - (COUNT_W + SINCE_W)
    assert between >= 12, \
        f"a since and the next column's count are {round(between, 2)}px apart, under the house 12"


def test_no_text_in_the_full_screen_view_is_under_11px():
    """11px is this phone's floor and the full screen view keeps it on its
    table too, headings included — which is why the PRICE track is set by the
    word PRICE and not by "1,700" (FULL2-SPEC.md 4.2). Held on every rule the
    view sets a size in, so the next column cannot come in under it."""
    sized = {}
    for sel, decls in _flat_rules(_css_code(PHONE)):
        if not any(part.strip().startswith(".cf-") for part in sel.split(",")):
            continue
        for d in decls:
            m = re.match(r"(?:font:(?:[^;]*?\s)?|font-size:)([\d.]+)px", d)
            if m:
                sized[sel] = float(m.group(1))
    for need in (".cf-t", ".cf-when", ".cf-foot", ".cf-grp th", ".cf-col th", ".cf-tb td"):
        assert need in sized, f"{need} no longer sets its own size; this proves nothing"
    small = {sel: px for sel, px in sized.items() if px < 11}
    assert not small, f"text under 11px in the full screen view: {small}"


# --- the masthead's first row (2026-09-18) -----------------------------------
# Measured in WebKit off the shipped face on the real stylesheet: each box as
# the browser lays it out, the ticker and the expiry with their trailing
# tracking, the pill with its 20px of padding. The pill's figures are
# proportional (its font shorthand resets them), so each form is the widest of
# the minutes it can show: 1 to 6 while the payload is live.
_MAST_W = {"SNDK": 37.91,
           "OPTIONS END MON": 125.64,          # the widest weekday
           "OPTIONS END TODAY": 137.83,
           "BOOK 4 MIN OLD": 119.95,           # the widest of 1 to 6
           "4 MIN AGO": 83.14}


def _mast_need(expiry, pill):
    """The first row with the live dot on: the ticker, the dot and the expiry
    at the row's own gap, then the gap the masthead keeps before the pill."""
    row, dot = _px(".mast-row", "gap"), _px(".livedot", "width")
    return _MAST_W["SNDK"] + row + dot + row + _MAST_W[expiry] + _px(".mast", "gap") + _MAST_W[pill]


def _mast_room(phone):
    pad = re.search(r"padding:0 (\d+)px", _rule(".mast"))
    return phone - 2 * _side() - 2 * int(pad.group(1))


@pytest.mark.parametrize("phone", [320, 360, 375, 390, 412])
def test_the_masthead_keeps_the_pills_subject_on_the_owners_phone(phone):
    """The row a reader sees in market hours is the ticker, the live dot, the
    expiry and "BOOK 1 MIN OLD" to "BOOK 6 MIN OLD" (WORDS-SPEC #2, #5). From
    the owner's 360px Galaxy up it keeps the pill's subject whatever weekday
    the options end on: 313.50 of 320 at 360 on a Monday, 302.75 on a Friday.
    On expiry day it keeps it from 375 up; at 360 "OPTIONS END TODAY" leaves
    "BOOK 1 MIN OLD" 2.93px short, and the pill says only the age, which the
    row holds on every phone from 320. paintMast chooses on the row as laid out
    (test_the_pill_gives_up_its_subject_before_the_expiry_leaves_the_row), so
    these are the widths it is choosing between."""
    for sel, font in ((".ticker", "font:700 11px/1"), (".expiry", "font:500 11px/1"), (".fresh", "font:700 11px/1")):
        assert font in _rule(sel), f"{sel} is no longer set at {font}"
    assert "padding:5px 10px" in _rule(".fresh") and "white-space:nowrap" in _rule(".fresh")
    room = _mast_room(phone)
    if phone >= 360:
        assert _mast_need("OPTIONS END MON", "BOOK 4 MIN OLD") <= room
        assert _mast_need("OPTIONS END TODAY", "4 MIN AGO") <= room
    if phone >= 375:
        assert _mast_need("OPTIONS END TODAY", "BOOK 4 MIN OLD") <= room
    assert _mast_need("OPTIONS END MON", "4 MIN AGO") <= room


def test_a_row_too_long_for_the_phone_drops_the_expiry_whole():
    """When even the bare age will not fit, as on an expiry day at 320 or a
    Saturday whose last scan was Friday's, the expiry leaves the row for a
    line under the ticker. It goes whole: the row wraps between items, so
    "OPTIONS END" never parts from its day while the line has room for both,
    and nothing runs into the pill. The expiry itself may still wrap, as a
    last resort, where a line of its own is narrower than it. Measured in
    WebKit over 336 states (seven expiry forms, the dot on and off, live and
    last-scan, twelve ages) at 320, 360, 375 and 412: nothing past the page and
    nothing within the masthead's 10px of the pill."""
    assert "flex-wrap:wrap" in _rule(".mast-row").replace(" ", "")
    assert "white-space" not in _rule(".expiry"), "an expiry held to one line runs into the pill"
    assert "min-width:0" in _rule(".mast-l") and "flex:none" in _rule(".fresh")


def _hidden_by_script(html, js):
    """{id: [class, ...]} for every element the page's script shows and hides
    with the hidden attribute, directly or through a const it holds it in."""
    held = dict(re.findall(r"const (\w+) = \$\('(\w+)'\)", js))
    ids = set(re.findall(r"\$\('(\w+)'\)\.hidden\s*=", js))
    ids |= {held[v] for v in re.findall(r"\b(\w+)\.hidden\s*=", js) if v in held}
    out = {}
    for el in ids:
        tag = re.search(r'<[a-z]+\b[^>]*\bid="%s"[^>]*>' % el, html)
        assert tag, f"#{el} is hidden by the script and not in the markup"
        cls = re.search(r'\bclass="([^"]*)"', tag.group(0))
        out[el] = cls.group(1).split() if cls else []
    return out


def test_what_the_script_hides_stays_hidden():
    """The pages show and hide parts of themselves with the hidden attribute,
    and it hides only through the browser's own [hidden]{display:none}, which
    any author rule that sets a display beats. .chg (inline-flex) and .lastscan
    (block) did, measured in WebKit: once shown, the change pill kept its last
    figure on screen after paintMast hid it, and a withdrawn price's "LAST SCAN
    … AGO" line stayed under the price after the price came back. So every
    element a page's script hides, whose own rule sets a display, carries a
    [hidden] rule that takes it back to none."""
    thread_js = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", THREAD))
    for name, html, js, need in (("index.html", PHONE, PAGE, {"chg", "lastscan", "load", "hh"}),
                                 ("thread.html", THREAD, thread_js, {"load"})):
        hidden = _hidden_by_script(html, js)
        assert need <= set(hidden), f"{name}: the hides are no longer where this test reads them"
        rules = [(part.strip(), decls) for sel, decls in _flat_rules(_css_code(html))
                 for part in sel.split(",")]
        for el, classes in sorted(hidden.items()):
            for c in classes:
                shown = [d for s, decls in rules if s == "." + c
                         for d in decls if d.startswith("display:") and d != "display:none"]
                if shown:
                    assert any(s == f".{c}[hidden]" and "display:none" in decls for s, decls in rules), \
                        f"{name}: #{el} sets {shown[0]} on .{c}, which beats hidden"
