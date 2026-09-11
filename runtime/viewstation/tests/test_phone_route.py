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
import inspect
import re
from pathlib import Path

import pytest

import server


def _block(sel):
    """The declaration block for a selector, read to its closing brace rather
    than by a character count — the comments inside these rules are longer than
    any slice, and a test that cannot see past prose teaches you to delete the
    prose.

    `sel` is given with its brace (".p-edge{"). The stylesheet pads some
    selectors out to a column (".p-edge    {"), so the brace is matched
    separately from the name rather than as one literal."""
    import re as _re
    name = sel.rstrip("{").rstrip()
    m = _re.search(r"(?m)^" + _re.escape(name) + r"\s*\{([^}]*)\}", PHONE)
    return m.group(1) if m else None


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
THREAD = (M / "thread.html").read_text()


def test_the_regime_word_carries_no_claim_about_what_price_will_do():
    """The gloss under the regime word said "walls hold" or "walls give way",
    read off the gamma sign. That is a claim that hedging damps or speeds a
    move — the sentence the model is forbidden to write (sndk_read.py's
    doctrine) and the effect docs/sndk-plan.md records as measured absent on
    SNDK. The sign itself was the literal string "unknown" on 490 of 5,423
    scans (9.0%), and on the rest it rests on an assumed dealer convention.

    So the gamma sign reaches no pixel at all now: not the gloss, not the card,
    not the footer. The regime word stands alone."""
    code = _code_only(GLANCE) + _code_only(PAGE)
    assert "gamma_sign" not in code, "the phone reads the gamma sign again"
    assert "gammaIsLong" not in code
    for gone in ("walls hold", "walls give way"):
        assert gone not in code, gone
    assert "Regime not measured" in PAGE
    # the footer names what every mark is, rather than caveating a claim
    assert "not a forecast of where price goes" in PAGE


def test_gamma_sign_colours_nothing():
    """Its whole blast radius is one gloss line, one sentence and the footer.
    If the sign is wrong, one sentence is wrong, not the instrument."""
    assert "regime-wash" not in PHONE and "r-long" not in PHONE and "r-short" not in PHONE
    assert ".wash" not in PHONE


def test_vwap_is_a_price_at_a_position():
    """vwap_minus_live_spot_sigma (sr-8 rename of vwap_dist_sigma, the value
    unchanged) is (vwap - live spot)/sigma, so a NEGATIVE value means price is
    ABOVE its average. 13 of 15 reviewers read it backwards. A price cannot be
    read backwards."""
    assert "function vwapPrice(" in GLANCE
    assert "diaryLast&&diaryLast.vwap" in GLANCE.replace(" ", "")   # exact source preferred
    # the CURRENT name, recovered by ADDING the subtraction back to the spot —
    # pinning the retired name here is how this test stayed green while the
    # phone read a field that no longer shipped
    assert "p.live_spot + p.vwap_minus_live_spot_sigma * sig" in GLANCE
    assert "vwap_minus_live_spot_sigma" not in PAGE                 # the ratio never printed


def test_weight_rides_one_fixed_scale_and_absence_is_not_zero():
    """ONE full scale for the card's bars, the chart's rail bars and the chart's
    line thickness, so the three can never rank a wall differently.

    30, not 20. Over 15,653 wall observations since 07-27 the share runs p50
    9.4%, p90 25.6%, p95 33.1%; the last eight sessions run heavier. At 20 a
    full bar was 15.5% of all walls and 27.1% of recent ones — a quarter of the
    levels drew identically at the cap. At 30 the cap takes 6.4%. A per-scan
    maximum is still wrong: it makes the biggest wall full every scan and
    destroys comparison between days. gex null draws no bar AND no track: an
    empty track reads as zero."""
    g = GLANCE.replace(" ", "")
    assert "constFULL_SHARE=30;" in g
    assert "gex/FULL_SHARE*full" in g                     # the rail bar
    assert "share/FULL_SHARE*100" in g                    # the card's bar
    assert "Math.min(share,FULL_SHARE)/FULL_SHARE" in g   # the line thickness
    # no second scale hiding anywhere
    assert "FULL = 20" not in PAGE and "gex/20" not in g
    assert "wallStroke(l.gex)" in PAGE and "wallTier" not in PAGE + GLANCE
    # `gex` is the INTERNAL name only; the scene entry ships the share as
    # cluster_share_of_book_gamma_pp (sr-7/obs-2) and both files must read that
    assert "cluster_share_of_book_gamma_pp" in GLANCE
    assert "cluster_share_of_book_gamma_pp" in PAGE
    rw = GLANCE.split("function railWidth")[1].split("/* ---- level assembly")[0]
    assert "return null;" in rw
    sb = GLANCE.split("function shareBarPct")[1].split("function wallStroke")[0]
    assert "return null;" in sb


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


def test_the_magnet_list_is_read_as_dicts():
    """sr-7 reshaped magnet.top_strikes from [strike, share] pairs into
    {strike, share_of_book_gamma_pp} dicts. The phone kept indexing arrays,
    Array.isArray(mag[0]) went false on every scan, and the magnet never drew
    again — while nothing here noticed."""
    assert "mag[0].strike" in GLANCE and "m.strike" in GLANCE
    assert "mag[0].strike" in PAGE
    assert "share_of_book_gamma_pp" in GLANCE and "share_of_book_gamma_pp" in PAGE
    assert "mag[0][0]" not in GLANCE and "mag[0][0]" not in PAGE


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


def test_no_dealer_behaviour_is_claimed_anywhere_on_the_phone():
    """The four sentences were copied byte-for-byte from the desktop's snkArrows,
    and the copying was never the problem — the sentences were. (The desktop's
    arrows were removed the same day; test_page_contract pins that side.) "Dealers sell
    the rallies here — it caps the move" states what hedging does to price, on a
    name where no damping or amplifying effect was found, and it was keyed on a
    sign that is "unknown" on 9.0% of scans. Removed 2026-09-10, with the rule
    that replaced them: say where the weight is, never what price will do."""
    for blob, where in ((_code_only(GLANCE), "glance.js"), (_code_only(PAGE), "page.js"),
                        (PHONE, "index.html")):
        for claim in ("Dealers buy", "Dealers sell", "Dealers must", "caps the move",
                      "holds price up", "moves speed up", "wallBehaviour", "gMech"):
            assert claim not in blob, f"{claim!r} is back in {where}"


def test_a_passed_wall_is_judged_against_the_price_on_screen():
    """The card and the chart both ask one question of the price the reader can
    SEE — the 5-second quote — never of the book's spot or the shipped sigma,
    which were measured against a price that has since moved. Replayed over 8
    sessions, price stood beyond a wall the card still showed on 2.7% of
    minutes, 5.8% on 09-10."""
    assert "levelRows(walls, most, lv.heaviest || null, ref)" in PAGE
    assert PAGE.count("wallPassed(l.side, l.y, ref)") >= 3     # line, tag, rail bar
    assert "wallPassed(side, k, ref)" in PAGE                  # the bug triangles
    wp = GLANCE.split("function wallPassed")[1].split("function levelRows")[0]
    code = "\n".join(l for l in wp.splitlines() if not l.strip().startswith("//"))
    assert "sigma" not in code
    assert "side==='call' ? p>k : side==='put' ? p<k" in code


def test_side_has_no_wall_absent_is_not_false():
    assert "_side_has_no_wall'] !== true" in PAGE


def test_the_banned_fields_reach_no_pixel():
    for f in ("magnitude_sigma", "dealer_flow", "breadth", "momentum",
              "drift_toward", "gap_vs_own_history", "frozen_do_not_cite"):
        assert f not in PAGE, f
        assert f not in GLANCE, f


def _code_only(js):
    """Strip comments first: the measured-history comments in these files cite
    retired names on purpose, and a pin that cannot tell prose from code
    teaches you to delete the history (the wallDistance lesson)."""
    lines = [l for l in js.splitlines() if not l.strip().startswith(("//", "*", "/*"))]
    return "\n".join(l.split("//")[0] for l in lines)


def test_the_scene_is_read_by_its_current_names():
    """sr-7/sr-8 (2026-08-30) renamed every scene key and reshaped the magnet
    list; the phone was built against the old names and painted nothing while
    this file stayed green, because every pin here spelt the OLD names. The
    source of truth is build_scene (docs/sndk-payload-inventory.md). If a
    rename lands upstream, this is the test that must go red."""
    code = _code_only(GLANCE) + _code_only(PAGE)
    for current in ("regime_label", "session_date", "live_spot",
                    "days_to_expiry", "expiry_date",
                    "vwap_minus_live_spot_sigma",
                    "cluster_share_of_book_gamma_pp",
                    "unchanged_for_min", "unchanged_for_at_least_min",
                    "_side_has_no_wall", "_heaviest_wall_behind_the_ladder",
                    "share_of_book_gamma_pp"):
        assert current in code, current
    # sr-8 moved `instrument` to the wrapper; reading it off the scene — or off
    # `d`, the stash-transplant typo that threw on every paint — must not return
    assert "PAY.instrument" in code
    assert "d.instrument" not in code
    # strikes-1 (09-05): the walls and the magnet live on the legacy Scene
    # Payload now; the ladder must read them there or paint nothing
    assert "PAY.legacy" in code
    for stale in ("vwap_dist_sigma", "_side_clear", "unchanged_min",
                  "heaviest_behind", "fe.dte", "fe.date", "regime.word"):
        assert stale not in code, stale


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


def test_the_glance_itself_is_not_a_control():
    """AMENDED 2026-09-07, and again 2026-09-10. The rule was "nothing is
    tappable", and its purpose was that the READING must never be a control: a
    screen you poke is a screen you are working, and this one is read at arm's
    length in a second.

    That purpose survives verbatim. 09-07 permitted exactly ONE link, to the
    readings archive. 09-10 permits exactly ONE press-and-hold, on the
    three-levels card, because the user asked for the card to explain itself —
    and a card whose words (gamma, call wall, most contracts) need a paragraph
    each cannot carry those paragraphs at arm's length. What the hold opens is
    an EXPLANATION: nothing on the card changes by touching it, and it is
    dismissed by one button.

    Everything that would make the DATA interactive stays banned: no onclick,
    no pointer cursors, no tooltips, no second button, no second hold, and the
    one click listener on the page does nothing but close the sheet. Each count
    is exact. A second of anything means the rule has started eroding and this
    test should be argued with again rather than edited again."""
    for bad in ("cursor:pointer", "onclick", "title="):
        assert bad not in PHONE, bad
    links = re.findall(r"<a\s[^>]*>", PHONE)
    assert len(links) == 1, f"exactly one link is allowed on the glance, found {len(links)}: {links}"
    assert 'href="/m/thread.html"' in links[0], links[0]

    holds = re.findall(r"<[^>]*\bdata-hold\b[^>]*>", PHONE)
    assert len(holds) == 1 and 'id="levels"' in holds[0], holds
    dialogs = re.findall(r'role="dialog"', PHONE)
    assert len(dialogs) == 1
    buttons = re.findall(r"<button\b[^>]*>", PHONE)
    assert len(buttons) == 1, buttons
    assert "data-sheet-close" in buttons[0], "the one button must close the sheet"
    sheet = PHONE.split('id="sheet"')[1]
    assert "<button" in sheet, "the button lives outside the sheet"

    assert PAGE.count("addEventListener('click'") == 1
    click = PAGE.split("addEventListener('click'")[1].split("});")[0]
    assert "data-sheet-close" in click and "dismiss()" in click


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


def test_the_levels_card_asserts_no_direction():
    """"▲ NEXT ABOVE" / "▼ NEXT BELOW" came from the live price while the wall
    beside it came from a book up to minutes old, and the two could disagree on
    screen. The card now lists both walls and the most-contracts strike ORDERED
    BY PRICE, which says where each one sits without a word that can go stale.

    By price and never by kind: the most-contracts strike sat above the call
    wall on 10.9% of replayed scans and below the put wall on 1.4%, so a fixed
    call / most / put order would have drawn those upside down."""
    code = _code_only(PAGE) + _code_only(GLANCE)
    assert "NEXT ABOVE" not in code and "NEXT BELOW" not in code
    lr = GLANCE.split("function levelRows")[1].split("function lightNote")[0]
    assert "rows.sort((a,b)=>key(b)-key(a))" in lr
    assert "r.strike!=null ? r.strike" in lr


def test_the_clear_side_bracket_is_qualified_and_conditional():
    """call_side_has_no_wall means no CALL-SIGNED cluster above spot; a wrongly-signed
    pile there is dropped from both pools and the flag still fires — true on 79
    of 79 rows of the reference diary, over a cluster carrying 34.6% of book
    gamma. And a live tick can cross a wall of the other pool."""
    assert "NO CALL WALL ABOVE" in PAGE and "NO PUT WALL BELOW" in PAGE
    assert "if(cross) continue;" in PAGE
    assert "_side_has_no_wall'] !== true" in PAGE  # === true stays necessary


def test_an_absent_level_is_a_row_never_a_gap():
    """Law 1 on this card. A side flagged empty is a measured finding (no put
    wall on 32.2% of recent scans, every scan of 09-04 and 09-08), and it gets
    its own row saying so. No flag and no entry is no measurement, and says
    THAT — never a zero bar, never a missing row."""
    lr = GLANCE.split("function levelRows")[1].split("function lightNote")[0]
    assert "walls[side+'_side_has_no_wall']===true" in lr     # === true stays necessary
    assert "'None above price'" in lr and "'None below price'" in lr
    assert lr.count("'Not measured'") == 2                     # a wall side, and the count
    # the footer that used to carry the note is gone, and its bookkeeping with it
    assert "CLEAR_SAID" not in PAGE and "farSideNote" not in PAGE + GLANCE


def test_the_tag_cap_respects_the_never_drop_tiers():
    """Without the tier a cap overflow could drop a heaviest_wall_behind_the_ladder, leaving the
    thickest stroke on the plot with its price nowhere on screen."""
    assert "(l.nearest || l.behind) ? 2 : 1" in PAGE


def test_a_dropped_request_does_not_blank_the_board():
    """visibilitychange fires loadPayload on wake — exactly when the radio has
    just reassociated — and a transport failure was byte-identical to an empty
    station."""
    assert "reached:false" in PAGE
    assert "if(PAY && PAY.scene){ paintAll(); return; }" in PAGE


def test_the_card_text_cannot_be_smeared_by_a_deficit():
    """The finding this test was written for, carried forward to the card that
    replaced the gate.

    .g-foot was the only gate child whose overflow:hidden zeroed its automatic
    minimum, so a 7px deficit in the old fixed-height column landed entirely on
    it and a sanctioned sentence rendered as an 11px slice of an 18px line. The
    guard stays on the property that CAUSED the smear rather than on the
    heights that delivered it: no zero automatic minimum, no nowrap sentence,
    no fixed height on the card."""
    for sel in (".lv-cap{", ".lv-none{", ".lv-n{"):
        b = _block(sel)
        assert b is not None, f"{sel} has no rule"
        flat = b.replace(" ", "")
        assert "overflow:hidden" not in flat, f"{sel} can be squeezed to nothing again"
        assert "white-space:nowrap" not in flat, f"{sel} is a nowrap sentence in a card that can shrink"
    card = _block(".levels{") or ""
    assert "height" not in card, "the card has a fixed height; the deficit comes back"


def test_the_height_budget_is_gone_rather_than_merely_unused():
    """Two tests used to live here: one recomputed FIXED from the region
    heights, one pinned the viewport measure sizeLadder chose its branch from.
    Both guarded a fixed-height column that no longer exists.

    Deleting them outright would leave nothing saying the budget must not come
    back — and it is exactly the kind of thing that gets reintroduced by
    someone trying to stop the page scrolling. So this asserts its ABSENCE.
    The layout-safety file guards what replaced it."""
    for gone in ("--r-mast", "--r-regime", "--r-gate", "--r-read", "--r-foot",
                 "max-height:700px"):
        assert gone not in PHONE, f"the fixed height budget is back: {gone}"
    assert "FIXED" not in PAGE, "sizeLadder is deriving a height again"


def test_the_row_label_wraps_rather_than_losing_its_last_tag():
    """A row label is a list of tags — "Put wall · Most contracts · Heaviest ·
    Price passed it" — and the LAST tag is the one that changes minute to
    minute. An ellipsis eats the end first, so a truncating label would drop
    "Price passed it" and leave a greyed strike with no word for why. It wraps."""
    side = _block(".lv-side{")
    assert side is not None
    flat = side.replace(" ", "")
    assert "text-overflow:ellipsis" not in flat and "white-space:nowrap" not in flat
    assert "min-width:0" in flat


def test_gminutes_cannot_print_sixty():
    """Math.round(m % 60) returns 60 for the last thirty seconds of every hour,
    and the chip repaints every 5s."""
    gm = GLANCE.split("function gMinutes")[1].split("/* ---- the environment")[0]
    assert "const t=Math.round(m);" in gm
    assert "r=t%60" in gm
    assert "Math.round(m%60)" not in gm.replace(" ", "")


def test_the_named_edge_carries_the_weight_the_bug_cannot():
    """The marker the gate NAMES is heavier than the ones it does not. 700, not
    600: Roboto — the fallback whenever the webfont has not landed — ships no
    600 at all, so a requested 600 resolves upward to 700 and the emphasis
    silently collapses into the plain weight beside it."""
    assert "namedEdge" in PAGE and "edgeCls" in PAGE
    lead = _block(".p-edge.lead{")
    base = _block(".p-edge{")
    assert base is not None, ".p-edge has no rule"
    assert lead is not None, ".p-edge.lead has no rule"
    # WEIGHT, not just fill. Both rules used to be 700 and differed only in
    # colour, which makes the distinction contrast-only — and the stylesheet's
    # own law is that subordination is by size or weight, because a
    # low-contrast grey read outdoors is gone.
    import re as _re
    bw = _re.search(r"font:(\d+)", base)
    lw = _re.search(r"font-weight:(\d+)", lead)
    assert bw and lw, "the two edge rules no longer state a weight"
    assert int(lw.group(1)) > int(bw.group(1)), \
        "the named edge is no heavier than an unnamed one; the cue is contrast alone"
    assert "fill:var(--i)" in lead.replace(" ", "")


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
    assert not server._looks_hashed("pjs-153fc85b70")        # 10 hex, too short
    assert not server._looks_hashed("pjs-153fc85b7029g")     # g is not hex
    assert not server._looks_hashed("PlusJakartaSans-OFL")   # words, not a hash


def test_an_unhashed_font_falls_back_to_no_cache():
    """The failure worth having. An unhashed font served immutable can never be
    replaced — every phone that fetched it holds it for a year and no edit on
    the mini reaches them. Wasting bandwidth is the cheaper mistake."""
    assert ".woff2" in server._IMMUTABLE_SUFFIXES
    assert server._IMMUTABLE_MAX_AGE == 31536000
    # the rule is (suffix AND hashed), not (suffix OR hashed)
    src = inspect.getsource(server.Handler._send_file)
    assert "_IMMUTABLE_SUFFIXES" in src and "_looks_hashed" in src
    assert 'and _looks_hashed' in src


def test_the_font_is_on_disk_and_named_by_its_own_bytes():
    import hashlib
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
    """The glance and the reads page each carry their own :root block, and 25
    tokens are declared in both. Duplicated bytes are not the risk — DRIFT is:
    somebody darkens --i-mute on one page and the other quietly disagrees.

    A shared stylesheet would cost more than it saves. This server sends
    Cache-Control: no-cache to everything that is not a content-hashed woff2,
    and sends no ETag, so a revalidation cannot come back 304 — a shared file
    would be re-fetched in full on every open, plus a render-blocking round
    trip before first paint on both pages. So the pages keep their own copies
    and this test guards the only thing the shared file was for."""
    import re

    def tokens(css):
        m = re.search(r"(?ms)^:root\{(.*?)^\}", css)
        assert m, "no :root block"
        return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", m.group(1)))

    a, b = tokens(PHONE), tokens(THREAD)
    shared = set(a) & set(b)
    assert len(shared) >= 20, f"the pages have stopped sharing a palette ({len(shared)} tokens)"
    drift = {k: (a[k].strip(), b[k].strip()) for k in shared if a[k].strip() != b[k].strip()}
    assert not drift, f"the two phone pages disagree about {drift}"


def test_a_passed_wall_drops_its_side_colour():
    """One hue, one meaning: green is the call side. A call wall price has
    already passed sits BELOW price, which is not the call side any more, and
    it stays green until the next scan relabels it. So from the moment the
    price on screen passes it, its strike, bar and chart line go neutral and
    its label says why — the weight is still true, the side is not."""
    row = PAGE.split("function lvRow")[1].split("\nfunction ")[0]
    assert "(r.passed ? 'passed'" in row, "a passed wall keeps its side's class"
    assert "tags.push('Price passed it')" in row
    assert ".lv.passed .lv-k{color:var(--i-mute)}" in PHONE
    assert ".p-wall.passed{stroke:var(--rule-soft)}" in PHONE
    assert ".p-tag.passed{fill:var(--i-mute)}" in PHONE


