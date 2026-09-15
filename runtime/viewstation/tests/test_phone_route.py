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
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    """Every file the two phone pages load — scripts, the typeface, the link
    between them — is served by the real route table as a file that exists. A
    page that names a script the station 404s paints nothing and says nothing."""
    refs = set()
    for html in (PHONE, THREAD):
        refs.update(re.findall(r'(?:src|href)="(/m/[^"]*)"', html))
        refs.update(re.findall(r'url\("(/m/[^"]+)"\)', html))
    assert {"/m/glance.js", "/m/page.js", "/m/press.js"} <= refs, refs
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


# --- the rebuilt glance: §14 MUST NOT REGRESS ------------------------------
# Every item below is measured. Breaking one is a defect, not a taste call.
# The numbers in the docstrings are the evidence, kept here so a later reader
# can weigh the rule instead of guessing at it.
M = Path(__file__).resolve().parents[1] / "static" / "m"
PHONE = (M / "index.html").read_text()
GLANCE = (M / "glance.js").read_text()
PAGE = (M / "page.js").read_text()
THREAD = (M / "thread.html").read_text()
ET = ZoneInfo("America/New_York")
_NODE = shutil.which("node")


def _glance(js, data=None, tz=None):
    """Run `js` against the REAL glance.js in node — `g` is its exports, `D` is
    `data` — and return the one JSON value the script prints. glance.js is pure
    on purpose so its rules can be run rather than grepped. Skips (after any
    source assertions the caller made first) when node is not installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const g=require(%s);const D=JSON.parse(require('fs').readFileSync(0,'utf8'));%s"
              % (json.dumps(str(M / "glance.js")), js))
    env = dict(os.environ, TZ=tz) if tz else None
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data),
                         capture_output=True, text=True, timeout=20, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _built_payload(tmp_path, monkeypatch):
    """The payload the phone fetches, built by the real builder from one diary
    row in a throwaway state dir. No model call: sndk_payload only builds."""
    import snapshot
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 13, 1, tzinfo=ET)
    row = {"ticker": "SNDK", "ts": t.isoformat(), "spot": 1586.2, "sigma": 80.0,
           "prior_close": 1554.5, "gamma_sign": "negative", "regime": "trending",
           "gex_views": {"front_dte": 2, "magnet": 1600.0,
                         "mass_by_strike": [[1600.0, 40.0], [1700.0, 30.0], [1500.0, 25.0]],
                         "net_by_strike": [[1550.0, -3.0e6], [1600.0, -2.0e6], [1700.0, 4.0e6]]},
           "meta": {"expiries": [{"date": "2026-08-21", "dte": 2}]}}
    d = tmp_path / "sndk_reversion"
    d.mkdir(parents=True)
    (d / "2026-08-19.jsonl").write_text(json.dumps(row) + "\n")
    return snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))


def _phone_scene(payload):
    # the same choice page.js state() makes: the legacy Scene Payload when it rides
    return (payload.get("legacy") or {}).get("scene") or payload["scene"]


def _reader():
    import snapshot
    if str(snapshot._SNDK_PRO_DIR) not in sys.path:
        sys.path.insert(0, str(snapshot._SNDK_PRO_DIR))
    import sndk_read
    return sndk_read


def test_the_regime_word_carries_no_claim_about_what_price_will_do():
    """The gloss under the regime word said "walls hold" or "walls give way",
    read off the gamma sign. That is a claim that hedging damps or speeds a
    move — the sentence the model is forbidden to write (sndk_read.py's
    doctrine) and the effect docs/sndk-plan.md records as measured absent on
    SNDK. The sign itself was the literal string "unknown" on 490 of 5,423
    scans (9.0%), and on the rest it rests on an assumed dealer convention.

    So the gamma sign reaches no pixel at all now: not the gloss, not the card,
    not the footer, not a colour. The regime word stands alone. Its whole blast
    radius, if it ever came back, should be one sentence and not the
    instrument."""
    code = _code_only(GLANCE) + _code_only(PAGE)
    assert "gamma_sign" not in code, "the phone reads the gamma sign again"
    assert "gammaIsLong" not in code
    for gone in ("walls hold", "walls give way"):
        assert gone not in code, gone
    # nor a wash or class keyed on the sign
    for cls in ("regime-wash", "r-long", "r-short", ".wash"):
        assert cls not in PHONE, cls
    assert "Regime not measured" in PAGE
    # the footer names what every mark is, rather than caveating a claim
    assert "not a forecast of where price goes" in PAGE


def test_vwap_is_a_price_at_a_position():
    """vwap_minus_live_spot_sigma (sr-8 rename of vwap_dist_sigma, the value
    unchanged) is (vwap - live spot)/sigma, so a NEGATIVE value means price is
    ABOVE its average. 13 of 15 reviewers read it backwards. A price cannot be
    read backwards."""
    assert "vwap_minus_live_spot_sigma" not in PAGE                 # the ratio never printed
    assert "vwapPrice(scene, diaryLast)" in PAGE
    got = _glance("""
      const sc = {price:{live_spot:1700, vwap_minus_live_spot_sigma:-0.5}, scale:{one_sigma_dollars:40}};
      console.log(JSON.stringify([
        g.vwapPrice(sc, null),
        g.vwapPrice(sc, {vwap:1688.25}),
        g.vwapPrice({price:sc.price, scale:{}}, null),
        g.vwapPrice({price:{live_spot:1700}, scale:sc.scale}, null)]));""")
    # negative ratio: the average sits BELOW price, recovered by adding it back
    assert got[0] == 1680
    # the diary's exact vwap is preferred over the rounded recovery
    assert got[1] == 1688.25
    # no sigma or no ratio: no price, never a guess
    assert got[2] is None and got[3] is None


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
    # no second scale hiding in the page, and the page draws through the shared rules
    assert "FULL = 20" not in PAGE
    assert "wallStroke(l.gex)" in PAGE and "railWidth(l.gex" in PAGE and "shareBarPct(r.share)" in PAGE
    assert "wallTier" not in PAGE + GLANCE
    # `gex` is the INTERNAL name only; the scene entry ships the share as
    # cluster_share_of_book_gamma_pp (sr-7/obs-2) and both files must read that
    assert "cluster_share_of_book_gamma_pp" in GLANCE
    assert "cluster_share_of_book_gamma_pp" in PAGE
    got = _glance("""
      const at = s => ({bar: g.shareBarPct(s), rail: g.railWidth(s, 20), stroke: g.wallStroke(s)});
      console.log(JSON.stringify({full: g.FULL_SHARE, zero: at(0), half: at(15), cap: at(30),
                                  over: at(45), none: [g.shareBarPct(null), g.railWidth(null, 20)]}));""")
    assert got["full"] == 30
    # all three are full at the same share...
    assert got["cap"]["bar"] == 100 and got["cap"]["rail"] == {"w": 20, "clipped": False}
    # ...all three stand at half their range at half of it...
    assert got["half"]["bar"] == 50 and got["half"]["rail"]["w"] == 10
    assert got["half"]["stroke"] == pytest.approx((got["zero"]["stroke"] + got["cap"]["stroke"]) / 2)
    # ...and past it nothing grows further, with the clip marked on the rail
    assert got["over"]["bar"] == 100 and got["over"]["stroke"] == got["cap"]["stroke"]
    assert got["over"]["rail"] == {"w": 20, "clipped": True}
    # no share: no bar and no track, never a zero-width one
    assert got["none"] == [None, None]


def test_a_refused_level_is_always_named():
    """Exiled or refused by the legibility test, it becomes an edge marker
    carrying its strike. That is the 2026-08-24 bug, where the heaviest wall on
    the board reached no pixel at all."""
    # the page names both kinds at the plot's edge rather than dropping them
    assert "WIN.refused.concat(WIN.exiled)" in PAGE
    assert "p-edge" in PAGE and "HEAVIEST" in PAGE
    got = _glance("""
      const w = g.solveWindow(
        [{y:1700, kind:'price'}, {y:1720, kind:'wall', side:'call'}, {y:1900, kind:'wall', side:'call'}],
        [{y:1640, kind:'wall', side:'put', gex:40}], 1700, 40, 5);
      console.log(JSON.stringify({exiled: w.exiled.map(l => l.y), refused: w.refused.map(l => l.y),
                                  admitted: w.admitted.map(l => l.y)}));""")
    assert got["exiled"] == [1900]      # past the 1.75-sigma radius
    assert got["refused"] == [1640]     # would flatten a $5 day into a line
    assert got["admitted"] == []


def test_the_magnet_never_shares_the_gex_gauge():
    """top_strikes shares are a fraction of mass_by_strike; wall gex is a
    fraction of net_by_strike. Two denominators must never share one gauge."""
    assert "p-diamond" in PAGE
    assert "railWidth(l.gex" in PAGE
    tag = PAGE.split("if(l && l.kind === 'wall'){")[1].split("}")[0]
    assert "railWidth" in tag                      # bars are drawn for walls only


def test_the_magnet_list_is_read_as_dicts(tmp_path, monkeypatch):
    """sr-7 reshaped magnet.top_strikes from [strike, share] pairs into
    {strike, share_of_book_gamma_pp} dicts. The phone kept indexing arrays,
    Array.isArray(mag[0]) went false on every scan, and the magnet never drew
    again — while nothing here noticed.

    So the scene the real builder hands back is fed to the real glance.js: if
    either end reshapes the list, the magnet stops drawing here too."""
    assert "mag[0].strike" in PAGE                 # the page reads the lead for its rules
    assert "mag[0][0]" not in PAGE
    scene = _phone_scene(_built_payload(tmp_path, monkeypatch))
    top = scene["magnet"]["top_strikes"]
    assert len(top) >= 2, "the fixture no longer builds runners to draw"
    got = _glance("""
      const core = g.coreLevels(D, D.price.live_spot, null, []);
      console.log(JSON.stringify({mag: core.filter(l => l.kind === 'magnet'),
                                  runners: g.magnetRunners(D)}));""", scene)
    assert [(m["y"], m["lead"], m["share"]) for m in got["mag"]] == \
        [(top[0]["strike"], True, top[0]["share_of_book_gamma_pp"])]
    assert [(r["y"], r["share"]) for r in got["runners"]] == \
        [(t["strike"], t["share_of_book_gamma_pp"]) for t in top[1:]]


def test_no_magnet_tie_threshold():
    """sr-3 deleted a hardcoded 5.0pp constant for shipping a near-constant as
    a finding. A near-tie must look like a tie without anyone deciding where a
    tie begins."""
    shares = [20, 19.99, 19.9, 19, 15, 10, 6, 5.6, 2]
    w = _glance("""
      const top = D.map((s, i) => ({strike: 1700 - 10*i, share_of_book_gamma_pp: s}));
      console.log(JSON.stringify(g.magnetRunners({magnet:{top_strikes: top}}).map(r => r.weight)));""",
                shares)
    assert len(w) == len(shares) - 1
    assert w[0] > 0.99                                    # 19.99 against 20 draws as a tie
    assert all(a >= b for a, b in zip(w, w[1:]))          # lighter never draws heavier
    assert min(w) > 0                                     # a runner never vanishes
    # linear in share above the visibility floor: no step where a "tie" begins
    steps = [(w[i] - w[i + 1]) / (shares[i + 1] - shares[i + 2])
             for i in range(len(w) - 1) if w[i + 1] > min(w) + 1e-9]
    assert len(steps) >= 4 and max(steps) - min(steps) < 1e-9, steps


def test_book_age_min_is_never_read():
    """Off-live, build_scene stamps clock.book_age_min from the row's own
    timestamp, so it reads ~0 however old the scan is. That is the failure that
    let a dead Schwab login look healthy for 3.1 days."""
    assert "book_age_min" not in PAGE
    assert "bookAge(PAY)" in PAGE
    got = _glance("""
      const now = Date.parse('2026-08-19T16:01:00-04:00');
      const fresh = {clock:{book_age_min:0}};
      console.log(JSON.stringify([
        g.bookAge({row_ts:'2026-08-19T13:01:00-04:00', scene:fresh, legacy:{scene:fresh}}, now),
        g.bookAge({scene:fresh, legacy:{scene:fresh}}, now)]));""")
    # three hours old by the wall clock, whatever the scene claims
    assert got[0] == {"min": 180, "unknown": False}
    # no row_ts: unknown, never the scene's self-measured zero
    assert got[1] == {"min": None, "unknown": True}


def test_staleness_thresholds_come_from_the_payload():
    assert "gates.stale_book_min" in PAGE and "gates.heartbeat_min" in PAGE


def test_the_countdown_does_not_age_silently():
    """minutes_to_close is computed at scan time. A countdown read hours later
    is a lie, and it is the one label on the plot that ages without saying so."""
    ladder = PAGE.split("function paintLadder")[1].split("\nfunction ")[0]
    foot = ladder.split("let rightFoot")[1].split("if(rightFoot)")[0]
    assert "if(st.stale)" in foot and "minutes_to_close" in foot
    assert foot.index("if(st.stale)") < foot.index("minutes_to_close"), \
        "the countdown is chosen before the staleness of the scan is asked"
    stale = foot.split("if(st.stale)")[1].split("} else")[0]
    assert "'SCAN '" in stale, "a stale scan no longer says which scan it is"


def test_the_reading_is_sourced_by_reading_ts_and_never_shown_without_its_age():
    """The store re-emits the same reading with a fresh ts while reading_ts
    stays put: the reference row carries ts 15:58 and reading_ts 11:47, a
    251-minute reading wearing a 0-minute timestamp. Median 12, p95 214,
    max 341.

    obs-1: the 120/30-minute tiers are gone. They were pegged to a 30-minute
    forecast horizon that no longer exists — an observation describes a
    measurement, so what makes it stale is that measurement no longer being
    current, which is the reader's own book ceiling. The phone's copy of that
    ceiling must be the reader's number."""
    m = re.search(r"const STALE_BOOK_MIN_UI\s*=\s*(\d+(?:\.\d+)?)\s*;", GLANCE)
    assert m, "the phone's book ceiling is no longer a literal"
    ceiling = float(m.group(1))
    assert ceiling == _reader().STALE_BOOK_MIN, "the phone and the reader disagree on when a book is stale"
    # obs-1 removed the LAST READING blanking branch with the expired tier. The
    # invariant this test is named for survives and is now unconditional: the
    # age is written on every painted reading, so a reading can never appear
    # without one. Genuine ABSENCE is still its own message.
    assert "NO READING TODAY" in PAGE
    assert "LAST READING" not in PAGE
    body = PAGE.split("function paintRead")[1].split("function ")[0]
    assert body.count("age.textContent") == 2      # the absent case, then always
    got = _glance("""
      const at = s => '2026-09-10T' + s + ':00-04:00';
      const now = Date.parse(at('15:58'));
      const reemitted = {ts: at('15:58'), reading_ts: at('11:47'), reading: {read: 'The old sentence.'}};
      const newer = {ts: at('12:00'), reading_ts: at('11:59'), reading: {read: 'The newer sentence.'}};
      const aged = mins => g.modelRead([{ts: at('15:58'), reading_ts: new Date(now - mins*60000).toISOString(),
                                         reading: {quiet: true}}], now).tier;
      const r1 = g.modelRead([reemitted], now);
      console.log(JSON.stringify({
        age: r1.ageMin, tier: r1.tier, line: r1.line,
        pick: g.modelRead([newer, reemitted], now).line,
        edge: [aged(D), aged(D + 0.5)],
        unstamped: g.modelRead([{ts: at('15:58'), reading: {read: 'no reading_ts'}}], now)}));""", ceiling)
    # aged by reading_ts, never by the fresh ts, and still painted: no 'expired' tier
    assert got["age"] == 251 and got["line"] == "The old sentence." and got["tier"] == "aged"
    # the newest READING wins, not the newest row
    assert got["pick"] == "The newer sentence."
    assert got["edge"] == ["fresh", "aged"]
    assert got["unstamped"] is None


def test_model_output_never_touches_innerhtml():
    """It is model output. It never enters the SVG string either."""
    assert "rdLine').innerHTML" not in PAGE
    body = PAGE.split("function paintRead")[1].split("\nfunction ")[0]
    assert "line.textContent =" in body
    assert "innerHTML" not in _code_only(body), "paintRead writes markup"
    ladder = PAGE.split("function paintLadder")[1].split("\nfunction ")[0]
    assert "READS" not in ladder and "modelRead" not in ladder, "the reading reaches the SVG string"


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
    assert "ref: q," in PAGE                                   # ref is the shown price
    assert "levelRows(walls, most, lv.heaviest || null, ref)" in PAGE
    assert PAGE.count("wallPassed(l.side, l.y, ref)") >= 3     # line, tag, rail bar
    assert "wallPassed(side, k, ref)" in PAGE                  # the bug triangles
    # a missing price or strike is checked on BOTH sides: in JS null compares as
    # 0, so without the guard a put wall with no price reads as passed
    cases = [["call", 1700, 1700.01], ["call", 1700, 1700], ["call", 1700, 1699.99],
             ["put", 1650, 1649.99], ["put", 1650, 1650], ["put", 1650, 1650.01],
             ["call", 1700, None], ["put", 1650, None], ["call", None, 1700],
             ["most", 1700, 1800]]
    got = _glance("""
      console.log(JSON.stringify({passed: D.map(a => g.wallPassed(...a)),
                                  shown: g.shownPrice({price:{live_spot:1690}}, {spot:1705}),
                                  book: g.shownPrice({price:{live_spot:1690}}, null)}));""", cases)
    assert got["passed"] == [True, False, False, True, False, False, False, False, False, False]
    # the price on screen is the live quote whenever there is one
    assert got["shown"] == {"v": 1705, "live": True}
    assert got["book"] == {"v": 1690, "live": False}


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


def test_the_scene_is_read_by_its_current_names(tmp_path, monkeypatch):
    """sr-7/sr-8 (2026-08-30) renamed every scene key and reshaped the magnet
    list; the phone was built against the old names and painted nothing while
    this file stayed green, because every pin here spelt the OLD names. The
    source of truth is build_scene (docs/sndk-payload-inventory.md). If a
    rename lands upstream, this is the test that must go red — so each name is
    checked at BOTH ends: the phone's code reads it, and the builder still
    ships it (in the scene it actually builds, or, for the keys only some scans
    carry, in the builder's own source)."""
    code = _code_only(GLANCE) + _code_only(PAGE)
    built = _phone_scene(_built_payload(tmp_path, monkeypatch))
    reader = Path(_reader().__file__).read_text()
    # checked WHERE the phone reads each one, not anywhere in the scene: the
    # builder also ships share_of_book_gamma_pp on structure.bands, so a rename
    # of the magnet's own key would still find the word somewhere
    where = {"regime_label": ("regime",), "session_date": ("clock",), "live_spot": ("price",),
             "days_to_expiry": ("clock", "front_expiry"), "expiry_date": ("clock", "front_expiry"),
             "cluster_share_of_book_gamma_pp": ("walls", "call", 0),
             "share_of_book_gamma_pp": ("magnet", "top_strikes", 0)}
    for current, path in where.items():
        assert current in code, f"the phone no longer reads {current}"
        node = built
        for step in path:
            node = node[step] if isinstance(node, list) else (node or {}).get(step)
        assert isinstance(node, dict) and current in node, \
            f"the builder no longer ships {current} at {'.'.join(map(str, path))}"
    # a vwap, a wall's age, an empty side, a heavier wall further out: not on
    # every scan, so the builder's source must still write the key
    for current in ("vwap_minus_live_spot_sigma", "unchanged_for_min", "unchanged_for_at_least_min",
                    "_side_has_no_wall", "_heaviest_wall_behind_the_ladder"):
        assert current in code, f"the phone no longer reads {current}"
        assert f'"{current}"' in reader, f"sndk_read.py no longer writes {current}"
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


def test_market_time_not_viewer_time():
    """The session is 09:30-16:00 in New York and the scene is stamped that
    way. Rendered locally on a Pacific machine the 12:12 scan reads 09:12 and
    the open reads 06:31."""
    assert "toLocaleTimeString" not in PAGE      # every clock face goes through etTime
    assert "etTime(" in PAGE
    assert "etToday()" in GLANCE
    got = _glance("""
      const scan = Date.parse('2026-08-19T16:12:00Z');
      console.log(JSON.stringify({viewer: new Date(scan).getHours(), scan: g.etTime(scan),
                                  open: g.etTime(new Date('2026-08-19T13:31:00Z')),
                                  evening: g.etToday(Date.parse('2026-09-10T02:30:00Z')),
                                  late: g.etToday(Date.parse('2026-09-10T05:30:00Z'))}));""",
                  tz="America/Los_Angeles")
    assert got["viewer"] == 9, "the viewer's clock is not Pacific; this proves nothing"
    assert got["scan"] == "12:12" and got["open"] == "09:31"
    # 22:30 in New York is still the 9th, though it is already the 10th in UTC
    assert got["evening"] == "2026-09-09"
    # 01:30 in New York is the 10th while the Pacific viewer is still on the 9th:
    # the one hour of the evening where a date that lost its zone reads wrong
    assert got["late"] == "2026-09-10", "the session date follows the viewer's clock"


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
    """At every 5-second repaint the geometry is bit-identical and exactly one
    mark has moved, so a glance is a comparison rather than a fresh read. Only
    a new payload earns a new window.

    A fresh window seats price 5.36% inside its own edge, already within the
    12% re-anchor band, so without a travel gate the board re-solved on every
    quote — 296 of 300 ticks moved a rule. The gate has to sit under that
    inset, which is derived here from glance.js's own padding rather than
    retyped, so a change to either side is judged against the other."""
    load = PAGE.split("async function loadPayload")[1].split("\nasync function ")[0]
    assert "WIN = null;" in load                  # only a new payload earns a new window
    assert "if(!WIN){" in PAGE
    assert "WIN.anchor" in PAGE
    assert "if(w2){ WIN = w2;" in PAGE            # a null re-solve must not blank WIN
    pad = re.search(r"\(s\.hi-s\.lo\)\*([\d.]+)", GLANCE)
    travel = re.search(r"Math\.abs\(ref - WIN\.anchor\) >= ([\d.]+)\*span0", PAGE)
    assert pad and travel, "the window padding or the travel gate is no longer a literal"
    p = float(pad.group(1))
    inset = p / (1 + 2 * p)                       # where a fresh window seats price from its edge
    assert float(travel.group(1)) < inset, "price can leave its window before it earns a new one"
    # ...and a band to re-anchor in, at BOTH edges and the same width: a zero
    # band only re-anchors once price has already left the window
    band = re.search(r"ref < WIN\.lo \+ ([\d.]+)\*span0 \|\| ref > WIN\.hi - ([\d.]+)\*span0", PAGE)
    assert band, "the re-anchor band is gone from one edge or both"
    assert float(band.group(1)) == float(band.group(2)) > 0, "the re-anchor band is empty or lopsided"


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
    call / most / put order would have drawn those upside down. (The above-call
    case is run in test_phone_levels; this runs the below-put one.)"""
    code = _code_only(PAGE) + _code_only(GLANCE)
    assert "NEXT ABOVE" not in code and "NEXT BELOW" not in code
    rows = _glance("""
      const walls = {call:[{strike:1720, cluster_share_of_book_gamma_pp:9}],
                     put:[{strike:1680, cluster_share_of_book_gamma_pp:8}]};
      const at = k => g.levelRows(walls, {strike:k, contracts:900}, null, 1700).map(r => [r.kind, r.strike]);
      console.log(JSON.stringify({below: at(1650), between: at(1700)}));""")
    # below the put wall: a fixed call / most / put order would draw it upside down
    assert rows["below"] == [["wall", 1720], ["wall", 1680], ["most", 1650]]
    # between them: the order the rows are assembled in (call, put, most) is not price order either
    assert rows["between"] == [["wall", 1720], ["most", 1700], ["wall", 1680]]


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
    # the footer that used to carry the note is gone, and its bookkeeping with it
    assert "CLEAR_SAID" not in PAGE and "farSideNote" not in PAGE + GLANCE
    got = _glance("""
      console.log(JSON.stringify({
        empty: g.levelRows({call_side_has_no_wall:true, put_side_has_no_wall:true},
                           {strike:1700, contracts:12}, null, 1700).map(r => [r.side, r.kind, r.text || r.strike]),
        loose: g.levelRows({call_side_has_no_wall:'true', put_side_has_no_wall:1}, null, null, 1700)
                 .map(r => [r.side, r.text])}));""")
    # both measured-empty sides are rows, each on its own side of price
    assert got["empty"] == [["call", "absent", "None above price"], ["most", "most", 1700],
                            ["put", "absent", "None below price"]]
    # === true stays necessary: a flag that is merely truthy is not a measurement
    assert got["loose"] == [["call", "Not measured"], ["most", "Not measured"], ["put", "Not measured"]]


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
    got = _glance("""
      const bad = [];
      for(let i = 0; i <= 60*24*20; i++){
        const m = i / 20, s = g.gMinutes(m);
        const hm = /^(\\d+)h(?: (\\d+)m)?$/.exec(s), mm = /^(\\d+)m$/.exec(s);
        if(s === 'just now' ? m >= 1 : hm ? +(hm[2] || 0) >= 60 : mm ? +mm[1] >= 60 : true) bad.push([m, s]);
      }
      console.log(JSON.stringify({bad: bad.slice(0, 5), edge: [59.49, 59.5, 119.5, 89.5].map(g.gMinutes)}));""")
    assert got["bad"] == [], got["bad"]
    assert got["edge"] == ["59m", "1h", "2h", "1h 30m"]


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

    hashed_js = tmp_path / "glance-0123456789abcdef.js"
    hashed_js.write_text("0")
    h = _Wire()
    h._send_file(hashed_js)
    assert h.sent["Cache-Control"] == "no-cache", "a hash alone earned an immutable cache"

    h = _Wire()
    h._send_file(M / "index.html")
    assert h.sent["Cache-Control"] == "no-cache", "the phone page itself is cached"


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
