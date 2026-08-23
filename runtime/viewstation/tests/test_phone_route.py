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
