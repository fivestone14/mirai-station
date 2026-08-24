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
# Measured at phone width on 2026-08-24 the first chart put eleven text
# elements in a 374px frame: five overlapping pairs and one label hanging 9px
# outside the border. The cause was two lines of text per level in a gutter
# sized for one, "fixed" by stacking 13px apart when the pair needed 21. These
# pin the rebuild's four rules as strings, the way test_page_contract pins the
# desktop's — a later edit that drops one fails here rather than on the phone.
PHONE = (Path(__file__).resolve().parents[1] / "static" / "m" / "index.html").read_text()
GLANCE = (Path(__file__).resolve().parents[1] / "static" / "m" / "glance.js").read_text()


def test_label_rows_are_solved_not_nudged():
    assert "function layoutLabels(" in GLANCE     # the two-pass separator
    assert "layoutLabels(ruled.map(" in PHONE     # ...and the chart actually calls it
    assert "const GUT = 54, ROW = 16" in PHONE    # a gutter sized to its content


def test_coincident_levels_merge_instead_of_stacking():
    """The magnet sat exactly on the call wall in the first live scene read.
    Two things in one place cannot be spaced apart, only merged."""
    assert "function mergeLevels(" in GLANCE
    assert "prev.also" in GLANCE                  # ...and the merge keeps both names
    assert "for(const a of (l.also || []))" in PHONE


def test_the_chart_does_not_repeat_what_is_already_on_screen():
    """session high/low duplicate the price path's own extremes, and the word
    labels duplicated the card directly beneath the chart."""
    assert "session_high" not in GLANCE.split("function drawableLevels")[1].split("function mergeLevels")[0]
    assert "'call wall'" not in PHONE.split("function paintChart")[1].split("$('key')")[0]


def test_a_band_that_covers_the_screen_becomes_its_edges():
    assert "h / geom.h < 0.55" in PHONE
    assert "band-edge" in PHONE


def test_ruled_levels_set_the_window_and_bands_do_not():
    """A wall just outside the day's range is the whole question the chart
    answers, so it must widen the view; the flip band routinely spans the
    session and would squash the price path flat."""
    geo = GLANCE.split("function chartGeometry")[1].split("function linePath")[0]
    assert "l.rank!=null && l.rank<=2" in geo
    # the exact line that once silently matched nothing. Checked as the
    # STATEMENT, not the bare name: the comment above it names the field on
    # purpose, and a test that cannot tell prose from code teaches you to
    # delete the explanation.
    assert "if(!l.lead) continue;" not in geo


def test_the_header_shows_the_price_and_withholds_the_wrong_percentage():
    """Measured 2026-08-24: the scan said 1598 while the stock was 1487. The
    header led with the scan, which on a stale scene is simply the wrong
    number. The quote leads now — but the CHANGE needs a prior close, and the
    only one derivable belongs to the scene's own session, so across days it
    is withheld rather than computed from a stale anchor."""
    assert "function pickPrice(" in GLANCE
    assert "function priorClose(" in GLANCE
    assert "if(sceneIsLive){" in GLANCE          # the percentage is gated on it
    assert "pickPrice(scene, LIVE, sceneLive)" in PHONE


def test_the_quote_polls_faster_than_the_scene():
    """The scene costs a real payload build and stays on 60s. The quote is a
    2s-cached reading the station already buys for anyone watching, so 5s adds
    nothing upstream."""
    assert "setInterval(loadSpot, 5000)" in PHONE
    assert "setInterval(load, 60000)" in PHONE
    assert "/api/spot?ticker=SNDK" in PHONE
    assert "clearInterval(SPOT_TIMER)" in PHONE   # ...and both stop with the screen


def test_the_chart_is_the_elastic_element():
    assert "flex:1 1 auto" in PHONE
    assert "header,.wall,.key{flex:none}" in PHONE
    assert "min-height:100dvh" in PHONE
