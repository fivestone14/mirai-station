"""The reads page — the riskiest change in the light rebuild, and until now the
least tested.

The list order was inverted (oldest-first-and-scroll-to-bottom became
newest-first), which moved four separate things at once: which end new readings
arrive at, which of two adjacent messages a silence belongs to, where the
retired-contract banner lands, and what "hold the reader's place" means. An
audit of that commit found three defects in this file and none of them could
have been caught, because the whole page had two incidental greps of coverage.

These are source invariants rather than a rendered DOM, in the same style as the
rest of this suite: the page's logic is inline in the HTML, and the alternative
is a browser in CI. They pin the properties whose violation is silent — an order
that reverses, a gap attributed to the wrong pair, a banner that outlives the
outage it describes.
"""
import re
from pathlib import Path

THREAD = (Path(__file__).resolve().parents[1] / "static" / "m" / "thread.html").read_text()


def _fn(name):
    """One function's body, to its closing brace at column 0."""
    m = re.search(r"(?ms)^(?:async )?function " + re.escape(name) + r"\([^)]*\)\{(.*?)^\}", THREAD)
    return m.group(1) if m else None


def test_the_list_runs_newest_first():
    """You open this to see what the model just said. The old page appended
    oldest-to-newest and auto-scrolled to the bottom, so on a 33-message session
    the newest reading was 33 cards down."""
    r = _fn("render")
    assert r is not None
    assert re.search(r"for\s*\(\s*let i\s*=\s*msgs\.length\s*-\s*1;\s*i\s*>=\s*0;\s*i--\s*\)", r), \
        "render no longer walks the messages backwards"
    # and the auto-scroll-to-bottom that made the old order bearable must be gone
    assert "scrollHeight" not in r, "render is scrolling; the newest reading is already on top"


def test_a_silence_is_attributed_to_the_older_of_the_pair():
    """The one piece of arithmetic the reversal could have broken silently.

    `i` counts DOWN, so `msgs[i-1]` is the OLDER message and the gap is
    newer-minus-older. Reverse those two operands and every gap goes negative,
    `minutesBetween` returns null for anything <= 0, and every silence on the
    page quietly stops being drawn."""
    r = _fn("render")
    assert "const prev = msgs[i - 1];" in r, "the older neighbour is no longer msgs[i-1]"
    assert "minutesBetween(prev.ts, m.ts)" in r, \
        "the gap operands are reversed: every silence would compute negative and vanish"
    assert "gap >= GAP_MIN" in r


def test_the_oldest_reading_gets_no_divider_below_it():
    """`msgs[-1]` is undefined in JS, not the last element — so the loop's final
    iteration must guard on it rather than wrapping around to the newest."""
    r = _fn("render")
    assert re.search(r"if\s*\(\s*prev\s*\)", r), \
        "nothing guards the oldest message; a wrap-around would draw a bogus gap"


def test_the_retired_contract_banner_is_drawn_before_the_list():
    """It explains a whole session. The old page appended it at the top and then
    scrolled to the bottom, so it was never once seen."""
    r = _fn("render")
    i_banner = r.index("oldcontract")
    m = re.search(r"for\s*\(\s*let i\s*=\s*msgs\.length", r)
    assert m, "the descending loop is not where it was"
    i_loop = m.start()
    assert i_banner < i_loop, "the banner is drawn after the list and will be scrolled past"


def test_the_poll_cannot_duplicate_the_tail():
    """Two callers — the 45s interval and every visibilitychange — could put two
    requests in the air with the same `since`. Both answers concat, and the pair
    draws as two identical cards with NO divider between them, because
    minutesBetween returns null for equal timestamps.

    Both guards are required: the in-flight flag stops the common case, the
    filter stops the rest, and the symptom of missing either is silent."""
    p = _fn("poll")
    assert p is not None
    assert "S.polling" in p, "there is no in-flight guard"
    assert re.search(r"filter\(\s*m\s*=>\s*m\s*&&\s*m\.ts\s*>\s*seen\s*\)", p), \
        "the tail is concatenated without a timestamp filter"
    assert "S.polling = false" in THREAD and "finally" in p, \
        "the in-flight flag is not released on a throw and would wedge the poll forever"


def test_a_recovered_station_stops_saying_it_is_unreachable():
    """`S.stalled = false` sat behind an early return on an empty answer, so a
    day with no readings yet kept the banner up permanently while every poll
    behind it succeeded. And the subheader — the element actually asserting
    'station unreachable' — was written only by loadDay, so it never recanted."""
    p = _fn("poll")
    i_clear = p.index("S.stalled = false")
    i_empty = p.index("if(!doc.messages.length)")
    assert i_clear < i_empty, \
        "the stalled flag is cleared after the empty-answer return; the banner would stick"
    assert "paintSub" in p, "poll cannot repaint the subheader that says 'station unreachable'"
    assert _fn("paintSub") is not None, "paintSub was not extracted from loadDay"


def test_the_poll_holds_the_readers_place():
    """render() empties the container and rebuilds every node, so scrollTop
    survives nothing and browser scroll anchoring has no anchor left. A reading
    arrives ABOVE everything, so a reader parked mid-list shifts by its height
    — onto a different card, mid-sentence."""
    p = _fn("poll")
    assert "scrollHeight" in p and "scrollTop" in p, "nothing compensates for the inserted height"
    assert re.search(r"atTop\s*\?\s*0\s*:", p), \
        "arriving at the top must still land at the top; that is the point of descending"


def test_the_scroller_contains_its_own_overscroll():
    """Without this a drag at the top of the list chains to the root document
    and arms the browser's own pull-to-refresh — the same reload-and-jump the
    shell bridge below it exists to prevent, through a different door. The
    Android shell masks it; a plain Chrome tab does not."""
    m = re.search(r"(?ms)^\.wrap\{(.*?)\}", THREAD)
    assert m and "overscroll-behavior:contain" in m.group(1).replace(" ", "")


def test_the_struck_chip_stays_legible_on_the_filled_card():
    """--put-ink on the dark --fill card is 2.39:1, under even the 3:1 floor for
    a border. `.rc.struck .wake` and `.rc.now .wake` have identical specificity,
    so source order alone was deciding it."""
    assert ".rc.now.struck .wake" in THREAD, \
        "a struck reading that is also the newest would render at 2.39:1"


def test_model_text_cannot_carry_markup():
    """The one boundary for model-authored prose. The <b> chips this adds are
    the only markup that may ever enter that string."""
    f = _fn("setSaid")
    assert f is not None
    assert 'replace(/[<>"¦]/g' in f and "replace(/&/g, '&amp;')" in f
    i_strip = f.index('replace(/[<>"¦]/g')
    i_html = f.index("innerHTML")
    assert i_strip < i_html, "the string reaches innerHTML before it is stripped"
