"""/m — the phone view's address, and what the server sends the two phone pages.

The static fallback at the bottom of do_GET resolves a route under static/ and
serves it only when it is a FILE. "/m" is a directory, so before this route
existed the address a phone would actually be given 404'd while the page's own
name worked — the classic case of the documented URL and the working URL being
different strings. The Android shell is configured with "/m", so
test_every_route_answers pins both spellings onto the same file, over HTTP.

Driving do_GET needs no socket: the handler's only I/O goes through _send_file
and _send_json, and a subclass that captures both exercises the route table
directly (the same stand-in habit test_host_guard uses for _host_ok).

What the pages draw is test_phone_jev_spx's and test_phone_spx_reads's. This
file keeps to the files they share and the headers they are sent with: the
typeface and the one header that makes it affordable, the APK, the palette held
equal across the pages, and the sheet (sheet.js, one copy) on the page that
opens one."""
import hashlib
import io
import re
from pathlib import Path

import server

M = Path(__file__).resolve().parents[1] / "static" / "m"
SPX = (M / "jev-spx.html").read_text()
READS = (M / "reads.html").read_text()
SHEET = (M / "sheet.js").read_text()
PRESS = (M / "press.js").read_text()


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


class _Wire(server.Handler):
    """Handler that runs the REAL _send_file and keeps what it put on the wire.
    The caching and content-type rules exist only as headers, so they are
    judged as headers rather than by reading _send_file's source."""

    def __init__(self, path="/"):
        self.path = path
        self.headers = {"Host": "localhost:8787"}
        self.request_version = "HTTP/1.1"
        self.client_address = ("127.0.0.1", 0)
        self.close_connection = False
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = {}

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        self.sent[key] = value

    def end_headers(self):
        pass


def _code_only(js):
    """Strip comments first: the measured-history comments in these files cite
    retired names on purpose, and a pin that cannot tell prose from code
    teaches you to delete the history (the wallDistance lesson)."""
    lines = [l for l in js.splitlines() if not l.strip().startswith(("//", "*", "/*"))]
    return "\n".join(l.split("//")[0] for l in lines)


def test_the_phone_page_exists_on_disk():
    """Every file the phone pages load — scripts, the typeface, the links
    between them — is served by the real route table as a file that exists. A
    page that names a script the station 404s paints nothing and says nothing."""
    refs = set()
    for html in (p.read_text() for p in sorted(M.glob("*.html"))):
        refs.update(re.findall(r'(?:src|href)="(/m/[^"]*)"', html))
        refs.update(re.findall(r'url\("(/m/[^"]+)"\)', html))
    assert {"/m/press.js", "/m/sheet.js"} <= refs, refs
    for ref in sorted(refs):
        h = _Stub(ref)
        h.do_GET()
        assert h.sent_json is None, f"{ref} 404s: {h.sent_json}"
        assert h.sent_file is not None and Path(h.sent_file).is_file(), ref


def test_an_apk_declares_itself_installable(tmp_path):
    """The shell is downloaded from this server, behind the same password wall
    it later talks through. Chrome tolerates the octet-stream fallback; naming
    the type means the file says what it is rather than relying on that."""
    apk = tmp_path / "mirai-mobile-1.2.apk"
    apk.write_bytes(b"PK\x03\x04")
    h = _Wire("/mirai-mobile-1.2.apk")
    h._send_file(apk)
    assert h.status == 200
    assert h.sent["Content-Type"] == "application/vnd.android.package-archive"


def test_the_phone_page_is_not_reachable_by_a_rebound_name():
    """The phone view is served through the same host guard as everything
    else — a new route must not become a new hole."""
    h = _Stub("/m")
    h.headers = {"Host": "evil.com"}
    h.do_GET()
    assert h.sent_file is None


# --- the typeface, and the one header that makes it affordable -------------
# The phone downloads a 27 KB variable font. Everything else this server sends
# is `no-cache`, and it sends no ETag and no Last-Modified — so a revalidation
# cannot return 304 and a no-cache font is re-fetched in full on every app
# open, over a tunnel, on cell data. The filename's own content hash is what
# makes a year-long immutable cache safe instead of reckless.

def test_only_a_content_hashed_name_earns_an_immutable_cache():
    assert server._looks_hashed("pjs-153fc85b7029")          # 12 hex, the real one
    assert server._looks_hashed("x-0123456789abcdef")        # longer is fine
    assert not server._looks_hashed("pjs")                   # no hash at all
    assert not server._looks_hashed("pjs-153fc85b702")       # 11 hex, one short
    assert not server._looks_hashed("pjs-153fc85b7029g")     # g is not hex
    assert not server._looks_hashed("PlusJakartaSans-OFL")   # words, not a hash


def test_an_unhashed_font_falls_back_to_no_cache(tmp_path):
    """The failure worth having. An unhashed font served immutable can never be
    replaced — every phone that fetched it holds it for a year and no edit on
    the mini reaches them. Wasting bandwidth is the cheaper mistake.

    Judged on the headers the real _send_file sends: the rule is (woff2 AND
    hashed), not (woff2 OR hashed)."""
    shipped = sorted((server.STATIC / "m").glob("*.woff2"))
    assert shipped, "the phone's typeface is missing from static/m"
    h = _Wire()
    h._send_file(shipped[0])
    assert h.status == 200 and h.sent["Content-Type"] == "font/woff2"
    assert h.sent["Cache-Control"] == "public, max-age=31536000, immutable"   # one year

    unhashed = tmp_path / "PlusJakartaSans.woff2"
    unhashed.write_bytes(b"wOF2")
    h = _Wire()
    h._send_file(unhashed)
    assert h.sent["Cache-Control"] == "no-cache", "an unhashed font is cached forever"

    hashed_js = tmp_path / "sheet-0123456789abcdef.js"
    hashed_js.write_text("0")
    h = _Wire()
    h._send_file(hashed_js)
    assert h.sent["Cache-Control"] == "no-cache", "a hash alone earned an immutable cache"

    h = _Wire()
    h._send_file(M / "jev-spx.html")
    assert h.sent["Cache-Control"] == "no-cache", "the phone page itself is cached"


def test_the_raw_file_route_is_never_stored(tmp_path, monkeypatch):
    """The SPX cards and every grade row come through /api/raw/file, and the
    pages fetch them with cache:'no-store'; the answer has to say the same, or a
    proxy between the phone and the station may hand back yesterday's card."""
    (tmp_path / "spx_jev").mkdir()
    (tmp_path / "spx_jev" / "latest.json").write_text('{"questions": []}')
    monkeypatch.setattr(server, "RAW_ROOTS", {"state": tmp_path})
    h = _Wire("/api/raw/file?root=state&path=spx_jev/latest.json")
    h.do_GET()
    assert h.status == 200
    assert h.sent["Cache-Control"] == "no-store"


def test_the_font_is_on_disk_and_named_by_its_own_bytes():
    fonts = sorted((server.STATIC / "m").glob("*.woff2"))
    assert fonts, "the phone's typeface is missing from static/m"
    for f in fonts:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
        assert f.stem.endswith(digest), (
            f"{f.name} claims a hash its bytes do not match — "
            f"immutable caching would pin the wrong file for a year")
        assert f.read_bytes()[:4] == b"wOF2", f"{f.name} is not a woff2"


def test_the_font_ships_its_licence():
    """Plus Jakarta Sans is OFL, which permits redistribution and requires the
    licence to travel with the font."""
    assert (server.STATIC / "m" / "PlusJakartaSans-OFL.txt").is_file()


def test_the_two_phone_pages_share_one_palette():
    """The forecast and the results each carry their own :root block, and most
    tokens are declared in both. Duplicated bytes are not the risk — DRIFT is:
    somebody darkens --i-mute on one page and the other quietly disagrees.

    A shared stylesheet would cost more than it saves. This server sends
    Cache-Control: no-cache to everything that is not a content-hashed woff2,
    and sends no ETag, so a revalidation cannot come back 304 — a shared file
    would be re-fetched in full on every open, plus a render-blocking round
    trip before first paint on both pages. So the pages keep their own copies
    and this test guards the only thing the shared file was for: every token
    both pages declare has one value."""

    def tokens(css):
        m = re.search(r"(?ms)^:root\{(.*?)^\}", css)
        assert m, "no :root block"
        # a value broken over lines, or spaced after its commas, is the same value: only its words count
        return {k: re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", v)).strip()
                for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", m.group(1))}

    a, b = tokens(SPX), tokens(READS)
    shared = set(a) & set(b)
    assert len(shared) >= 20, f"the pages have stopped sharing a palette ({len(shared)} tokens)"
    drift = {k: (a[k], b[k]) for k in shared if a[k] != b[k]}
    assert not drift, f"the two phone pages disagree about {drift}"


def _css_rules(html):
    """{selector: declarations} for every rule in the page's stylesheet, media
    blocks included, with comments and whitespace out. A selector written twice
    keeps its first rule: the second is a @media block's override of it."""
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", html)))
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        out.setdefault(re.sub(r"\s+", " ", sel).strip(), re.sub(r"\s+", "", body))
    return out


def test_the_page_that_opens_a_sheet_draws_it_the_one_way():
    """The forecast page opens sheets: what is in the combined call, how to
    read the page, Claude's why, the questions, the price-today chart. What a
    sheet does is one script, sheet.js, and the open one is the one it has
    unhidden, so the page carries several without a second copy of anything.
    What it looks like is one set of rules in the page's own stylesheet, for
    the palette's reason above: the sheet capped at 86% of the measured height
    and scrolling, not clipping, past it; not selectable; its head, its caveat
    and the one close button. A page that opens no sheet (the results) loads
    neither the script nor the rules: a page carries the sheet whole or not at
    all."""
    core = {".scrim", ".sheet", "body.sheet-open .scrim", '.sheet[aria-hidden="false"]', ".sh-grab",
            ".sh-h", ".sh-caveat", ".sh-caveat b", ".sh-close",
            ".sh-close:focus-visible", '.sheet,.sheet[aria-hidden="false"]'}
    for name, html in (("jev-spx.html", SPX), ("reads.html", READS)):
        opens = "MiraiSheet.open(" in html
        assert ('<script src="/m/sheet.js"></script>' in html) == opens, name
        assert (core <= set(_css_rules(html))) == opens, (name, core - set(_css_rules(html)))
    assert "MiraiSheet.open(" in SPX
    sheet = _css_rules(SPX)[".sheet"]
    assert "max-height:calc(var(--app-h)*.86);overflow-y:auto" in sheet
    assert "user-select:none" in sheet


def test_the_sheet_text_cannot_start_a_selection():
    """A live text selection turns the next drag into handle-dragging instead
    of scrolling, which was half of how an explainer card once became a dead
    zone. The sheet is explanation, not something to copy."""
    m = re.search(r"(?m)^\.sheet\{([^}]*)\}", SPX)
    assert m, ".sheet has no rule"
    flat = m.group(1).replace(" ", "").replace("\n", "")
    assert "user-select:none" in flat and "-webkit-touch-callout:none" in flat
    # 86% of the MEASURED height: as 84dvh it was 0px inside the app
    assert "max-height:calc(var(--app-h)*.86)" in flat


def test_a_drag_inside_the_sheet_cannot_reload_the_page():
    """Opened with the page scrolled to the top, a downward drag in the sheet
    read as the shell's pull-to-refresh and reloaded the page out from under
    the reader. The page tells the shell it is not at the top while the sheet
    is open, and keeps the answer true afterwards on every scroll. Since
    2026-09-18 that is sheet.js's, the one copy every sheet runs on
    (test_phone_jev_spx drives it on the forecast page).

    The price-today chart is the reason this matters twice. It covers the page
    and it is a screen a reader drags a finger over, so the pull would fire
    inside it too — and it does not, because it is opened through MiraiSheet
    and so rides the same one bridge. The page has no copy of its own.

    What this pins is the intent and not the literal: the page answers about
    its scroll position in ONE expression, in this one file, and the page's
    own script says nothing to the shell. The haptic tick is the only thing
    press.js asks the shell for."""
    ts = SHEET.split("function tellShell")[1].split("\n  }\n")[0]
    assert "MiraiShell.atTop(!isOpen() && window.scrollY <= 0)" in ts
    assert "window.addEventListener('scroll', () => { if(spoke) tellShell(); }" in SHEET
    assert len(re.findall(r"MiraiShell\.atTop\(", _code_only(SHEET))) == 1, \
        "a second answer about the scroll position"
    page = _code_only("\n".join(re.findall(r"(?s)<script>(.*?)</script>", SPX)))
    assert "MiraiShell" not in page and "tellShell" not in page, \
        "the forecast page speaks to the shell behind sheet.js's back"
    assert set(re.findall(r"MiraiShell\.(\w+)", _code_only(PRESS))) <= {"tick"}, \
        "press.js asks the shell for something other than the haptic"
