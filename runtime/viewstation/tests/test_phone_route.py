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
    assert (server.STATIC / "m" / "page.js").is_file()


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


# --- the rebuilt glance: §14 MUST NOT REGRESS ------------------------------
# Every item below is measured. Breaking one is a defect, not a taste call.
# The numbers in the docstrings are the evidence, kept here so a later reader
# can weigh the rule instead of guessing at it.
M = Path(__file__).resolve().parents[1] / "static" / "m"
PHONE = (M / "index.html").read_text()
GLANCE = (M / "glance.js").read_text()
PAGE = (M / "page.js").read_text()


def test_an_unmeasured_gamma_sign_claims_nothing():
    """gamma_sign is the literal string 'unknown' on 241 of 3,393 recorded rows
    (7.1%). No sign, no sentence — the strike, direction, distance and gauge
    all still stand."""
    assert "if(s==='negative'||s==='short'||s==='-') return false;" in GLANCE
    assert "set('gMech', b ? b.english : '');" in PAGE
    assert "gamma sign not measured" in PAGE
    assert "no dealer behaviour claimed" in PAGE


def test_gamma_sign_colours_nothing():
    """Its whole blast radius is one gloss line, one sentence and the footer.
    If the sign is wrong, one sentence is wrong, not the instrument."""
    assert "regime-wash" not in PHONE and "r-long" not in PHONE and "r-short" not in PHONE
    assert ".wash" not in PHONE


def test_vwap_is_a_price_at_a_position():
    """vwap_dist_sigma is (vwap - spot)/sigma, so a NEGATIVE value means price
    is ABOVE its average. 13 of 15 reviewers read it backwards. A price cannot
    be read backwards."""
    assert "function vwapPrice(" in GLANCE
    assert "diaryLast&&diaryLast.vwap" in GLANCE.replace(" ", "")   # exact source preferred
    assert "vwap_dist_sigma" not in PAGE                            # the ratio never printed


def test_weight_rides_a_fixed_scale_and_absence_is_not_zero():
    """20.4% is p90 of 18,510 recorded wall observations. A per-scan maximum
    makes the biggest wall full-width every scan and destroys comparison
    between days. gex null draws no bar AND no track: an empty track reads as
    zero."""
    assert "gex/20*full" in GLANCE.replace(" ", "")
    assert "FULL = 20" in PAGE
    rw = GLANCE.split("function railWidth")[1].split("/* ---- level assembly")[0]
    assert "return null;" in rw


def test_a_refused_level_is_always_named():
    """Exiled or refused by the legibility test, it becomes an edge marker
    carrying its strike. That is the 2026-08-24 bug, where the heaviest wall on
    the board reached no pixel at all."""
    assert "refused" in GLANCE and "exiled" in GLANCE
    assert "p-edge" in PAGE and "HEAVIEST" in PAGE


def test_the_magnet_never_shares_the_gex_gauge():
    """top_strikes shares are a fraction of mass_by_strike; wall gex is a
    fraction of net_by_strike. Two denominators must never share one gauge."""
    assert "p-diamond" in PAGE
    assert "railWidth(l.gex" in PAGE
    tag = PAGE.split("if(l && l.kind === 'wall'){")[1].split("}")[0]
    assert "railWidth" in tag                      # bars are drawn for walls only


def test_no_magnet_tie_threshold():
    """sr-3 deleted a hardcoded 5.0pp constant for shipping a near-constant as
    a finding. A near-tie must look like a tie without anyone deciding where a
    tie begins."""
    mr = GLANCE.split("function magnetRunners")[1].split("function solveWindow")[0]
    assert "Math.max(0.28, share/top)" in mr.replace(" ", "").replace("Math.max(0.28,share/top)", "Math.max(0.28, share/top)") or "share/top" in mr.replace(" ", "")
    assert "gap_pp" not in mr


def test_book_age_min_is_never_read():
    """Off-live, build_scene stamps clock.book_age_min from the row's own
    timestamp, so it reads ~0 however old the scan is. That is the failure that
    let a dead Schwab login look healthy for 3.1 days."""
    assert "book_age_min" not in PAGE
    assert "book_age_min" not in GLANCE.split("function bookAge")[1].split("function shownPrice")[0].replace(
        "clock.book_age_min is never read", "")


def test_staleness_thresholds_come_from_the_payload():
    assert "gates.stale_book_min" in PAGE and "gates.heartbeat_min" in PAGE


def test_the_countdown_does_not_age_silently():
    """minutes_to_close is computed at scan time. A countdown read hours later
    is a lie, and it is the one label on the plot that ages without saying so."""
    assert "if(st.stale)" in PAGE and "'SCAN '" in PAGE


def test_the_reading_is_sourced_by_reading_ts_and_never_shown_without_its_age():
    """The store re-emits the same reading with a fresh ts while reading_ts
    stays put: the reference row carries ts 15:58 and reading_ts 11:47, a
    251-minute reading wearing a 0-minute timestamp. Median 12, p95 214,
    max 341."""
    mr = GLANCE.split("function modelRead")[1]
    assert "Date.parse(r.reading_ts)" in mr
    assert "r.ts" not in mr.split("if(!best)")[0]
    assert "age>120" in mr.replace(" ", "") and "age>30" in mr.replace(" ", "")
    assert "LAST READING" in PAGE and "NO READING TODAY" in PAGE


def test_model_output_never_touches_innerhtml():
    """It is model output. It never enters the SVG string either."""
    assert "line.textContent =" in PAGE
    assert "rdLine').innerHTML" not in PAGE


def test_the_english_is_not_authored_here():
    """The four mechanism sentences are byte-identical to snkArrows and the
    gloss strings to envWords. The desktop and the phone must never describe
    one board in two voices."""
    for s_ in ("Dealers buy the dips here — it holds price up.",
               "Dealers sell the rallies here — it caps the move.",
               "Dealers must buy a break up — moves speed up.",
               "Dealers must sell a break down — moves speed up."):
        assert s_ in GLANCE
    assert "'walls hold'" in GLANCE and "'walls give way'" in GLANCE


def test_distance_is_measured_against_the_price_on_screen():
    assert "wallDistance(w.strike, ref)" in PAGE
    # checked on the CODE lines only. The comment above the function names
    # `sigma` on purpose, to say what it refuses to use, and a test that cannot
    # tell prose from code teaches you to delete the explanation.
    wd = GLANCE.split("function wallDistance")[1].split("/* ---- price, age")[0]
    code = "\n".join(l for l in wd.splitlines() if not l.strip().startswith("//"))
    assert "sigma" not in code


def test_side_clear_absent_is_not_false():
    assert "_side_clear'] !== true" in PAGE


def test_the_banned_fields_reach_no_pixel():
    for f in ("magnitude_sigma", "dealer_flow", "breadth", "momentum",
              "drift_toward", "gap_vs_own_history", "frozen_do_not_cite"):
        assert f not in PAGE, f
        assert f not in GLANCE, f


def test_no_emoji_no_legend_no_greek():
    """Emoji are colour bitmaps: no theme token, cannot be tinted to mean a
    side, do not dim with the page. A legend is a confession that the marks do
    not read. And no Greek: the ruler is stated once, in English."""
    import re as _re
    for blob in (PHONE, PAGE, GLANCE):
        assert not _re.search(r"[\U0001F300-\U0001FAFF]", blob)
    assert "σ" not in PHONE and "σ" not in PAGE
    assert "TYPICAL MOVE $" in PAGE
    assert "class=\"key\"" not in PHONE


def test_nothing_is_tappable():
    for bad in ("cursor:pointer", "onclick", "addEventListener('click'", "<button", "<a ", "title="):
        assert bad not in PHONE, bad
    assert "addEventListener('click'" not in PAGE


def test_the_window_is_frozen_between_payloads():
    """At every 5-second repaint the geometry is bit-identical and exactly one
    mark has moved, so a glance is a comparison rather than a fresh read."""
    assert "WIN = null;" in PAGE                 # only a new payload earns a new window
    assert "if(!WIN){" in PAGE
    assert "0.12*span0" in PAGE                  # ...and the re-anchor test


def test_market_time_not_viewer_time():
    """The session is 09:30-16:00 in New York and the scene is stamped that
    way. Rendered locally on a Pacific machine the 12:12 scan reads 09:12 and
    the open reads 06:31."""
    assert "America/New_York" in GLANCE
    assert "toLocaleTimeString" not in PAGE      # every clock face goes through etTime
    assert "etTime(" in PAGE
    assert "etToday()" in GLANCE
