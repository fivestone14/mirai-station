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
    # obs-1: the 120/30-minute tiers are gone. They were pegged to a 30-minute
    # forecast horizon that no longer exists — an observation describes a
    # measurement, so what makes it stale is that measurement no longer being
    # current, which is the reader's own book ceiling.
    assert "STALE_BOOK_MIN_UI" in mr
    assert "'expired'" not in mr and '"expired"' not in mr   # the tier, not the word
    # declared BEFORE modelRead, so it is not inside this slice — check the
    # whole file, and check the ordering explicitly rather than by accident
    assert GLANCE.index("const STALE_BOOK_MIN_UI") < GLANCE.index("function modelRead")
    # obs-1 removed the LAST READING blanking branch with the expired tier. The
    # invariant this test is named for survives and is now unconditional: the
    # age is written on every painted reading, so a reading can never appear
    # without one. Genuine ABSENCE is still its own message.
    assert "NO READING TODAY" in PAGE
    assert "LAST READING" not in PAGE
    body = PAGE.split("function paintRead")[1].split("function ")[0]
    assert body.count("age.textContent") == 2      # the absent case, then always


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


# --- amendments after the 2026-08-24 adversarial review --------------------
# 38 findings raised, 23 survived refutation, 14 work items. These pin the ones
# that changed behaviour, so a later "tidy" cannot walk them back.

def test_only_book_levels_are_named_at_an_edge():
    """A tape point has no strike, and ~70 exiled ones could take both slots
    while the wall the gate names in 30px type reached no pixel — the very bug
    the edge marker exists to prevent, coming back through the queue."""
    assert "l.kind === 'wall' || l.kind === 'magnet'" in PAGE
    assert "!drawnY.has(+l.y)" in PAGE            # a level with a rule is not named twice
    assert "rank(b) - rank(a)" in PAGE            # kind before weight: two denominators
    assert "leftover.filter(l => l.y > ref)" in PAGE   # split on price, not padded bounds


def test_the_frozen_window_is_actually_frozen():
    """A fresh window seats price 5.36% inside its own edge, already within the
    12% re-anchor band, so without a travel gate the board re-solved on every
    quote — 296 of 300 ticks moved a rule."""
    assert "WIN.anchor" in PAGE
    assert "0.05*span0" in PAGE                   # and the constant sits under 5.36%
    assert "if(w2){ WIN = w2;" in PAGE            # a null re-solve must not blank WIN


def test_no_nan_reaches_an_svg_attribute():
    """With one_sigma_dollars absent the degenerate floor cannot fire, and one
    distinct core level gives a zero span. A browser silently falls back to 0
    for each invalid length and renders garbage pinned to the top edge."""
    assert "if(!(span > 0))" in PAGE


def test_the_gate_direction_is_derived_not_read():
    """walls_ladder buckets a cluster by the SCAN spot, so a live tick through
    the wall makes the payload's side label contradict the plot above it."""
    assert "w.strike > ref ? '▲ NEXT ABOVE' : '▼ NEXT BELOW'" in PAGE
    assert "'g-k ' + w.side" in PAGE     # ...but the HUE stays the side of the BOOK


def test_the_clear_side_bracket_is_qualified_and_conditional():
    """call_side_clear means no CALL-SIGNED cluster above spot; a wrongly-signed
    pile there is dropped from both pools and the flag still fires — true on 79
    of 79 rows of the reference diary, over a cluster carrying 34.6% of book
    gamma. And a live tick can cross a wall of the other pool."""
    assert "NO CALL WALL ABOVE" in PAGE and "NO PUT WALL BELOW" in PAGE
    assert "if(cross) continue;" in PAGE
    assert "_side_clear'] !== true" in PAGE       # === true stays necessary


def test_the_footer_speaks_only_when_the_label_did_not():
    """Gating on the bracket PATH said the note twice in the withdrawn state and
    lost it entirely at 320px, where the bracket is under 34px tall."""
    assert "CLEAR_SAID" in PAGE
    assert "CLEAR_SAID[_far]" in PAGE
    assert "CLEAR_SAID = {call:false, put:false};   // reset" in PAGE


def test_the_tag_cap_respects_the_never_drop_tiers():
    """Without the tier a cap overflow could drop a heaviest_behind, leaving the
    thickest stroke on the plot with its price nowhere on screen."""
    assert "(l.nearest || l.behind) ? 2 : 1" in PAGE


def test_a_dropped_request_does_not_blank_the_board():
    """visibilitychange fires loadPayload on wake — exactly when the radio has
    just reassociated — and a transport failure was byte-identical to an empty
    station."""
    assert "reached:false" in PAGE
    assert "if(PAY && PAY.scene){ paintAll(); return; }" in PAGE


def test_the_gate_region_holds_its_own_content():
    """The one finding verified in a real engine: .g-foot is the only gate child
    whose overflow:hidden zeroes its automatic minimum, so the whole deficit
    landed on it and a sanctioned sentence rendered as a 2px smear."""
    assert "--r-gate:144px" in PHONE
    assert "FIXED = SHORT ? 332 : 392" in PAGE    # must move with the region
    assert "height:48px" in PHONE                 # the head row holds a 30px strike
    assert ".gate{gap:2px;padding:6px 0 6px}" in PHONE   # short sheds instead


def test_the_measure_is_inviolable_and_the_label_gives_way():
    """Both were nowrap with no min-width, so margin-left:auto collapsed under
    overflow and the measure ran off the edge last-character-first. On the
    censored path the first casualty is the '+', which turns 'held at least'
    into 'held exactly'."""
    # read to the closing brace, not a fixed slice: the explanatory comment
    # inside the block pushes the declaration past any character count, and a
    # test that cannot see past prose teaches you to delete the prose.
    def block(sel):
        return PHONE.split(sel)[1].split("}")[0]
    assert "min-width:0" in block(".g-dir{") and "text-overflow:ellipsis" in block(".g-dir{")
    assert "flex:none" in block(".g-meas{")


def test_gminutes_cannot_print_sixty():
    """Math.round(m % 60) returns 60 for the last thirty seconds of every hour,
    and the chip repaints every 5s."""
    gm = GLANCE.split("function gMinutes")[1].split("/* ---- the environment")[0]
    assert "const t=Math.round(m);" in gm
    assert "r=t%60" in gm
    assert "Math.round(m%60)" not in gm.replace(" ", "")


def test_the_named_edge_carries_the_weight_the_bug_cannot():
    assert "namedEdge" in PAGE and "edgeCls" in PAGE
    assert ".p-edge.lead{font-weight:600}" in PHONE
