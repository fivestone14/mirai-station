"""/m — the phone view's address.

The static fallback at the bottom of do_GET resolves a route under static/ and
serves it only when it is a FILE. "/m" is a directory, so before this route
existed the address a phone would actually be given 404'd while
"/m/index.html" worked — the classic case of the documented URL and the working
URL being different strings. The Android shell is configured with "/m", so
these pin all three spellings onto the same file.

Driving do_GET needs no socket: the handler's only I/O goes through _send_file
and _send_json, and a subclass that captures both exercises the route table
directly (the same stand-in habit test_host_guard uses for _host_ok)."""
from pathlib import Path

import pytest

import server


class _Stub(server.Handler):
    """Handler with the transport removed — never call BaseHTTPRequestHandler's
    __init__, which would start servicing a connection we do not have."""

    def __init__(self, path):
        self.path = path
        self.headers = {"Host": "localhost:8787"}
        self.request_version = "HTTP/1.1"
        self.sent_file = None
        self.sent_json = None

    def _send_file(self, path):
        self.sent_file = path

    def _send_json(self, payload, code=200):
        self.sent_json = (payload, code)


@pytest.mark.parametrize("route", ["/m", "/m/", "/m/index.html"])
def test_every_spelling_of_the_phone_view_serves_one_file(route):
    h = _Stub(route)
    h.do_GET()
    assert h.sent_json is None                      # not a 404
    assert h.sent_file == server.STATIC / "m" / "index.html"


def test_the_desktop_page_is_untouched_by_the_phone_route():
    """/m must not shadow "/" — the viewstation is still the default view."""
    h = _Stub("/")
    h.do_GET()
    assert h.sent_file == server.STATIC / "index.html"


def test_the_phone_page_exists_on_disk():
    assert (server.STATIC / "m" / "index.html").is_file()
    assert (server.STATIC / "m" / "glance.js").is_file()


def test_an_apk_declares_itself_installable():
    """The shell is downloaded from this server, behind the same password wall
    it later talks through. Chrome tolerates the octet-stream fallback; naming
    the type means the file says what it is rather than relying on that."""
    assert server._CONTENT_TYPES[".apk"] == "application/vnd.android.package-archive"


def test_the_phone_page_is_not_reachable_by_a_rebound_name():
    """The phone view is served through the same host guard as everything
    else — a new route must not become a new hole."""
    h = _Stub("/m")
    h.headers = {"Host": "evil.com"}
    h.do_GET()
    assert h.sent_file is None


# --- the chart's layout contract ------------------------------------------
# Measured at phone width the first chart put eleven text elements in a 374px
# frame: five overlapping pairs and one label hanging 9px outside the border.
# The cut-back that fixed it over-corrected, and on 2026-08-24 the chart drew
# the LIGHTEST wall on the board (6.9%) while hiding the heaviest (14.8%).
# These pin both lessons as strings, the way test_page_contract pins the
# desktop's, so a later edit that drops one fails here and not on the phone.
PHONE = (Path(__file__).resolve().parents[1] / "static" / "m" / "index.html").read_text()
GLANCE = (Path(__file__).resolve().parents[1] / "static" / "m" / "glance.js").read_text()


def test_label_rows_are_solved_not_nudged():
    assert "function layoutLabels(" in GLANCE
    assert "layoutLabels(numbered.map(" in PHONE
    assert "ROW = 16" in PHONE                   # a gutter row sized to its content


def test_coincident_levels_merge_instead_of_stacking():
    """The heaviest wall and the second magnet were the same strike on the
    first live board tested. Two things in one place cannot be spaced apart."""
    assert "function mergeLevels(" in GLANCE
    assert "hit.magnet=true" in GLANCE            # ...and the merge keeps the confluence
    assert "l.magnet) out +=" in PHONE            # painted as a glow under the wall's casing


def test_a_band_that_covers_the_screen_becomes_its_edges():
    assert "h / geom.h < 0.55" in PHONE
    assert "flipedge" in PHONE


def test_the_whole_ladder_reaches_the_canvas():
    """The chart was not under-drawn, it was selecting wrong: only walls.X[0]
    was read, so *_heaviest_behind — which exists precisely because the
    distance-ordered ladder cuts the heaviest cluster — never rendered."""
    sel = GLANCE.split("function drawableLevels")[1].split("function wallTier")[0]
    assert "ladder.forEach" in sel                       # every rung, not just the first
    assert "side+'_heaviest_behind'" in sel
    assert "side+'_side_clear'" in sel


def test_the_window_holds_the_structure_and_bands_never_widen_it():
    """A wall just outside the day's range is the whole question the chart
    answers. The flip band routinely spans multiples of that range."""
    geo = GLANCE.split("function chartGeometry")[1].split("function linePath")[0]
    assert "if(l.rank>3) continue;" in geo               # bands excluded from the window
    assert "FAR_SIGMA" in geo and "exiled" in geo        # ...and far levels get a chevron
    assert "if(!l.lead) continue;" not in geo            # the field rename that once matched nothing


def test_weight_is_encoded_without_adding_text():
    """18,510 recorded wall observations run p10 3.5%, p50 7.3%, p90 20.4% —
    spread enough that weight is worth encoding, and the fixed 20% full scale
    is that p90 rather than a guess or the scan's own maximum."""
    assert "function wallTier(" in GLANCE and "function railWidth(" in GLANCE
    assert "gex / 20 * full" in GLANCE
    assert "const FULL = 20;" in PHONE                    # the card uses the same scale


def test_an_unmeasured_gamma_sign_is_not_a_direction():
    """gamma_sign is literally 'unknown' on 241 of 3,393 recorded rows (7.1%),
    and every one of them used to print "walls give way" and a confident
    dealer sentence."""
    assert "if(s==='negative'||s==='short'||s==='-') return false;" in GLANCE
    assert "return null;\n}" in GLANCE.split("function gammaIsLong")[1][:900]
    env = GLANCE.split("function envWords")[1].split("function gammaIsLong")[0]
    assert "gammaIsLong(regime)" in env                   # header shares the one gate


def test_the_key_describes_what_was_painted():
    """The flip band is entirely off-window on roughly a third of scans, and
    the key was built from the candidate list, so it named a mark the reader
    could not find."""
    assert "const drawn = {}" in PHONE
    assert "drawn.flip = true" in PHONE
    assert "if(drawn.call)" in PHONE and "if(drawn.magnet)" in PHONE


def test_call_and_put_keep_the_desktop_colour_law():
    """The desktop's WMETA is gwc: jade / gwp: coral. The phone had these
    inverted, which put a coral call wall on the same screen as a coral
    negative-gamma rule meaning the opposite thing."""
    assert ".lv-call{stroke:var(--jade)}" in PHONE
    assert ".lv-put{stroke:var(--coral)}" in PHONE


def test_the_card_survives_a_board_that_is_clear_both_ways():
    """nearestWall returns null there and the card used to hide itself —
    deleting the loudest reading the scene can produce."""
    assert "function bothSidesClear(" in GLANCE
    assert "bothSidesClear(walls)" in PHONE


def test_the_dead_open_air_branch_is_gone():
    """walls_ladder sets *_side_clear only when a side's pool is empty and
    continues before writing the ladder, so beyondWall's first branch could
    never be reached and that string never rendered."""
    beyond = GLANCE.split("function beyondWall")[1].split("function farSideNote")[0]
    assert "_side_clear" not in beyond.split('"""')[0].replace("//", "\n//").split("if(!walls")[1]
    assert "alone:true" in beyond                        # the sound inference that replaced it


def test_distance_is_measured_against_the_price_on_screen():
    """The header repaints off the live quote every 5s while the scene rebuilds
    every 60s, so the shipped sigma is measured from a spot the reader can no
    longer see — 0.58 printed where 0.51 was honest."""
    assert "function wallDistance(" in GLANCE
    assert "wallDistance(w.strike, price, sigma)" in PHONE
    assert "pickPrice(scene, LIVE," in PHONE.split("function paintWall")[1][:600]


def test_card_apparatus_clears_the_contrast_floor():
    """--faint is 3.17:1 on the card surface and fails WCAG AA, which outdoors
    means gone. Apparatus is subordinate by size, not by contrast."""
    card = PHONE.split("/* ---- zone 3")[1].split(".msg{")[0]
    assert ".w-cav{" in card and "color:var(--dim)" in card
    assert "var(--faint)" not in card.replace("--faint is 3.17:1", "")


def test_glyphs_mark_places_and_cannot_collide():
    """Icons replaced the dotted rectangle for a measured-clear side, and the
    magnet gets its own emoji. Position already carries the side, so the glyph
    carries the CONSEQUENCE: clear sky above, a hole below. They live in their
    own lane and their rows go through the same solver the gutter uses — a
    magnet near the top of a short chart lands on the open-air icon otherwise."""
    assert "const glyphs = []" in PHONE
    assert "layoutLabels(glyphs.map(" in PHONE
    assert "TERM = 16" in PHONE                      # a lane of its own, never a bar's
    assert "\\uD83E\\uDDF2" in PHONE                 # magnet
    assert "\\u2601\\uFE0F" in PHONE                 # open air above
    assert "\\uD83D\\uDD73\\uFE0F" in PHONE          # nothing below


def test_only_the_lead_magnet_earns_a_glyph():
    """Runners-up are already spoken for by line weight. Three magnets in a
    16px lane is how a legend stops meaning anything."""
    assert "hit.magnetLead" in GLANCE
    assert "if(l.magnetLead) glyphs.push" in PHONE


def test_the_chart_is_given_the_room_but_cannot_collapse():
    """The plot takes the slack, and it keeps a floor.

    Removing the floor blanked the chart on a real phone: with a FIXED
    height:100dvh column and overflow:hidden, the plot was the only item
    permitted to shrink, so the moment header + key + card exceeded the
    viewport it absorbed the whole overflow and went to zero. The svg's
    height:100% then resolved against 0 — correct viewBox, nothing drawn, no
    error anywhere. min-height on the COLUMN (so the page may grow) plus a
    floor on the plot (so it may not vanish) is the pair that holds."""
    assert "min-height:100dvh" in PHONE
    assert "height:100dvh;overflow:hidden" not in PHONE.replace(" ", "")
    assert "flex:1 1 0;min-height:240px" in PHONE
    assert "header,.wall,.key{flex:none}" in PHONE


def test_the_off_window_flip_chevron_names_itself():
    """The chevron was the one drawn mark with no key entry — the key fired on
    `drawn.flip`, which is only set when the band paints INSIDE the window, and
    the band is entirely off-screen on roughly a third of scans. A glyph nobody
    can name is a glyph that is not working, so it carries which way and how
    far, which is more use than the band itself was on those scans."""
    assert "drawn.flipAway" in PHONE
    assert "off chart" in PHONE
    assert "else if(drawn.flipAway)" in PHONE


def test_each_legend_chip_is_one_flex_item():
    """The swatch and its own label were siblings in the flex row, so the
    inter-chip gap fell between every icon and the words it belonged to, and a
    2px bar and an emoji sat on different baselines."""
    assert 'class="chip"' in PHONE
    assert ".key .chip{display:inline-flex;align-items:center" in PHONE




def test_the_now_dot_is_joined_to_the_line_it_left():
    """The dot took its height from the live quote and its X from the last
    diary point, so it hung in space — measured $15.70 and five hours adrift,
    jumping every 5s against a frozen line. The unobserved stretch is now drawn
    as what it is: a DASHED reach, because the path across it was never
    observed and a solid line would invent one. The quote never enters the
    measured path."""
    assert "function livePoint(" in GLANCE
    assert "class=\\"preach\\"" in PHONE
    assert "const d = linePath(points, geo);" in PHONE      # measured points only
    assert "const dotX =" in PHONE and "dotX.toFixed(1)" in PHONE   # one shared endpoint


def test_the_quote_is_timed_off_age_not_off_ts():
    """live_spot stamps `ts` only on a FRESH fetch; every cached branch omits
    the key, and with a 2s cache against a 5s poll a good share of readings are
    cached. Keying on `ts` left the quote with no time and the reach never
    drew."""
    lp = GLANCE.split("function livePoint")[1].split("/* ---- freshness")[0]
    assert "live.age_s" in lp
    assert "Date.parse(live.ts)" in lp                       # exact when offered, never required
