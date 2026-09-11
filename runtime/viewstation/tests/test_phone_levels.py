"""The three-levels card (2026-09-10) — the nearest call wall, the strike with
the most contracts, the nearest put wall.

It replaced a card that named one wall under a direction word with a dealer
sentence beside it. Two reviews shaped what replaced it, and the numbers in
these docstrings are theirs:

  - a replay of 1,473 scans over 8 sessions, which found the first design's
    central claim ("the pin is the heaviest strike nearby") false on 66.9% of
    them, because the pin is chosen by CONTRACTS and every bar measured GAMMA;
  - a fact-check of the explainer text against the station's own findings,
    which found its textbook sentences (dealers buy dips, price gets pinned,
    walls are bounced off) recorded as measured false on SNDK.

So the card shows a count where the contracts are and a bar where the gamma
is, orders its rows by price, and its explainer says where the weight is and
stops there.
"""
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import snapshot

ET = ZoneInfo("America/New_York")
M = Path(__file__).resolve().parents[1] / "static" / "m"
PHONE = (M / "index.html").read_text()
GLANCE = (M / "glance.js").read_text()
PAGE = (M / "page.js").read_text()
_NODE = shutil.which("node")


# --- the server's half: a count and a heaviest-pile check -------------------

import sys  # noqa: E402
if str(snapshot._SNDK_PRO_DIR) not in sys.path:
    sys.path.insert(0, str(snapshot._SNDK_PRO_DIR))
import sndk_read as _R  # noqa: E402  — the reader's own module, as the route uses it


def _row(**gv):
    base = {"mass_by_strike": [[1600.0, 9307.0], [1700.0, 16419.0], [1750.0, 8435.0]],
            "vol_gross_by_strike": [[1600.0, 6579], [1700.0, 14066]],
            "net_by_strike": [[1595.0, -0.15], [1600.0, -3.82], [1605.0, -1.9],
                              [1650.0, -2.6], [1700.0, 0.3], [1760.0, 1.7]]}
    base.update(gv)
    return {"spot": 1694.12, "sigma": 64.5438, "gex_views": base}


def _scene(**walls):
    w = {"call": [{"strike": 1760.0, "cluster_share_of_book_gamma_pp": 16.8}],
         "put": [{"strike": 1650.0, "cluster_share_of_book_gamma_pp": 25.7}],
         "put_heaviest_wall_behind_the_ladder": {"strike": 1600.0,
                                                 "cluster_share_of_book_gamma_pp": 57.4}}
    w.update(walls)
    return {"magnet": {"top_strikes": [{"strike": 1700.0, "share_of_book_gamma_pp": 14.63}]},
            "walls": w}


def test_the_count_is_the_one_that_chose_the_strike():
    """mass_by_strike is open interest plus today's volume, calls and puts
    together — the argmax IS the scene's magnet. The count is what chose it, so
    the count is what is shown; the scene's own number for it is a share of
    contracts shipped under a gamma name, and never reaches the card."""
    lv = snapshot._levels_display(_R, _row(), _scene())
    mc = lv["most_contracts"]
    assert mc["strike"] == 1700.0 and mc["contracts"] == 16419
    assert mc["traded_today"] == 14066
    # read off the engine's own constant: 1.5 x the row's sigma
    import lefteye_gex_box as gx
    assert mc["window_dollars"] == round(gx.MAG_WINDOW_SIGMA * 64.5438, 2) == 96.82


def test_a_traded_figure_larger_than_the_count_is_dropped():
    """The two come from the same front-expiry contracts, so traded <= total by
    construction. If they ever disagree the sentence "N contracts, and M of
    them traded today" with M > N is unreadable, so the figure goes rather than
    the sentence lying."""
    lv = snapshot._levels_display(_R, _row(vol_gross_by_strike=[[1700.0, 99999]]), _scene())
    assert "traded_today" not in lv["most_contracts"]


def test_the_heaviest_pile_is_found_on_the_bars_own_measure_and_placed():
    """The heaviest cluster on the whole net_by_strike surface, as a share of
    that surface — the same number the wall bars show — plus which role it plays
    on the card, so the sheet's "why it can look light" sentence can name it."""
    lv = snapshot._levels_display(_R, _row(), _scene())
    h = lv["heaviest"]
    assert h["strike"] == 1600.0 and h["role"] == "put_further"
    assert h["holds_most_contracts"] is False           # 1,700 is not inside that pile
    # the denominator is the WHOLE surface; the numerator only what survived
    # the 25% concentration floor (1,595 at 0.15 did not), as in walls_ladder
    tot = 0.15 + 3.82 + 1.9 + 2.6 + 0.3 + 1.7
    assert h["share_pct"] == round((3.82 + 1.9) / tot * 100, 1)


def test_a_heaviest_pile_that_is_no_wall_has_no_role():
    """A call-heavy pile below price qualifies as neither side and is on no
    part of the card. role None is what hides the sheet's sentence, which would
    otherwise name a level the screen never draws."""
    lv = snapshot._levels_display(_R, _row(), _scene(put=[], put_heaviest_wall_behind_the_ladder={}))
    assert lv["heaviest"]["role"] is None


def test_nothing_measured_is_nothing_shipped():
    assert snapshot._levels_display(_R, {"spot": 1600.0}, {}) is None


def test_a_torn_surface_costs_only_its_own_half():
    """One bad pair in the gamma surface used to throw inside the clustering
    rule and take a perfectly good contract count down with it, so the card
    printed "Count not measured" beside a strike it had measured. The halves
    now fail apart, and the rule is handed the cleaned pairs."""
    row = _row(net_by_strike=[[1600.0, -3.82], [1650.0, None], [1605.0, "x"], [1760.0, 1.7]])
    lv = snapshot._levels_display(_R, row, _scene(call=["not a dict", {"strike": 1760.0}]))
    assert lv["most_contracts"]["contracts"] == 16419          # untouched by the tear
    assert lv["heaviest"]["strike"] == 1600.0                  # the clean pairs still cluster
    # and a surface that is ALL tear leaves the count standing on its own
    lv = snapshot._levels_display(_R, _row(net_by_strike=[[1650.0, None]]), _scene())
    assert "heaviest" not in lv and lv["most_contracts"]["strike"] == 1700.0


def test_the_levels_ride_the_wrapper_and_reach_no_model(tmp_path, monkeypatch):
    """A sibling of the scene on the display side of the fence, like
    `instrument`: not in the Strikes Payload, not in the legacy scene the wake
    gate reads, and not in the user_prompt the model is sent."""
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
    p = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))

    assert p["levels"]["most_contracts"] == {"strike": 1600.0, "contracts": 40,
                                             "window_dollars": 120.0}
    assert p["levels"]["heaviest"]["strike"] == 1550.0
    assert p["levels"]["heaviest"]["role"] == "put"
    assert p["levels"]["heaviest"]["holds_most_contracts"] is True   # 1,600 sits in 1550-1600
    assert "levels" not in p["scene"] and "levels" not in p["legacy"]["scene"]
    assert "most_contracts" not in p["user_prompt"] and "holds_most_contracts" not in p["user_prompt"]


# --- the phone's half --------------------------------------------------------

def test_the_most_contracts_row_is_a_count_never_a_bar():
    """A bar beside it measured something that did not choose it: on 66.9% of
    replayed scans the most-contracts strike was not the heaviest gamma strike
    in its own window, and on 09-02 its share sat under 1% on 100 of 186 scans
    because its calls and puts cancel in the netted surface."""
    row = PAGE.split("function lvRow")[1].split("\nfunction ")[0]
    bar_block = row.split("if(r.kind === 'wall'){")[1].split("\n  }\n")[0]
    assert "shareBarPct(r.share)" in bar_block, "the bar is built outside the wall branch"
    assert row.count("shareBarPct(") == 1
    assert "' contracts'" in row


def test_a_level_that_is_also_a_wall_shares_its_row():
    """The same strike as a wall on 35.3% of replayed scans. Two rows would
    print one price twice with two different bars; one row with both tags
    cannot."""
    lr = GLANCE.split("function levelRows")[1].split("function lightNote")[0]
    assert "rows.find(r=>r.kind==='wall' && r.strike===mk)" in lr
    assert "if(hit) hit.most=m;" in lr


def test_the_light_note_is_hidden_without_a_referent():
    """"Why it can look light" names the heaviest pile. The pile peaks AT the
    most-contracts strike on 35.0% of scans and contains it on 40.6%, and then
    the strike does not look light at all; and a pile that is no wall is drawn
    nowhere. Distance is not the reason, and the note never says it is: the
    first draft's "too far from price to count" was true on 3.4% of scans."""
    ln = GLANCE.split("function lightNote")[1].split("/* ---- weight")[0]
    assert "!h.role || h.holds_most_contracts || _fin(h.strike)===_fin(m.strike)" in ln
    assert "too far" not in PHONE and "too far" not in _code(PAGE)
    assert 'id="shLight" hidden' in PHONE, "the note must start hidden, not flash"


def test_the_sheet_says_where_the_weight_is_and_stops():
    """Every sentence below was in the first draft and every one is recorded as
    measured false on SNDK: no damping or amplifying effect, no pin on
    weeklies, a wall relabelled on every crossing so "breaks through" is never
    observed, and the S&P "a third of the time" figure from another market and
    an older definition. The station's own word filter flags half of them."""
    sheet = PHONE.split('id="sheet"')[1].split("</div>\n\n<nav")[0]
    import html
    text = html.unescape(re.sub(r"<[^>]+>", " ", sheet))     # as a reader sees it
    for claim in ("buy dips", "sell rallies", "pinned", "settle at", "settles at",
                  "bounce", "break through", "a third of the time", "coin flip",
                  "caps the", "holds price up", "speed up"):
        assert claim not in text, f"the sheet claims {claim!r} again"
    # the renamed level, and the honest caveat, both present
    assert "Most contracts" in text and ">Pin<" not in PHONE
    assert "A location, not a forecast." in text
    assert "hasn't been established" in text


@pytest.mark.skipif(not _NODE, reason="node is not installed")
def test_the_hold_opens_on_release_never_under_the_finger():
    """The first build opened the sheet on the 450ms timer, under a finger still
    on the glass, and every source-level test here passed while it did. The
    rest of that touch then belonged to the sheet: Android's long-press fired on
    the sheet's text ~50ms later and began a text selection, so the drag that
    followed selected instead of scrolling. The card became a dead zone and the
    only place a swipe scrolled was the sliver of screen above it. The lift
    could also land as a tap on the backdrop and close the sheet at once.

    So this runs the real page.js against a fake clock and fake touches. The
    harness was checked against the old gesture: it fails there on
    open_while_finger_down, rest_then_scroll_opens and ghost_click_closes."""
    out = subprocess.run([_NODE, str(Path(__file__).with_name("gesture_harness.js")), str(M)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["open_while_finger_down"] is False, "the sheet opened under the finger again"
    assert got["armed_class"] is True                       # the hold says "let go now"
    assert got["open_after_lift"] is True
    assert got["lift_tap_cancelled"] is True, "the lift's tap will hit the backdrop"
    assert got["pushes_after_open"] == 1
    for scroll in ("rest_then_scroll_opens", "quick_scroll_opens", "pan_claimed_opens",
                   "scroll_event_opens", "tap_opens"):
        assert got[scroll] is False, f"{scroll}: a scroll or a tap opened the sheet"
    assert got["armed_survives_pointercancel"] is True, "a long-press cancel strands the hold"
    assert got["ghost_click_closes"] is False, "the opening gesture closed the sheet"
    assert got["closed_by_button"] is True
    assert got["backs_on_double_tap"] == 1, "a double tap on Got it goes back twice"
    assert got["contextmenu_blocked"] == [True, True, False]


def test_the_sheet_text_cannot_start_a_selection():
    """A live text selection turns the next drag into handle-dragging instead
    of scrolling, which is half of how the card became a dead zone. The sheet
    is explanation, not something to copy."""
    import re as _re
    m = _re.search(r"(?m)^\.sheet\{([^}]*)\}", PHONE)
    assert m, ".sheet has no rule"
    flat = m.group(1).replace(" ", "").replace("\n", "")
    assert "user-select:none" in flat and "-webkit-touch-callout:none" in flat
    # and a WebView without dvh keeps a ceiling instead of losing it
    assert "max-height:84vh;max-height:84dvh" in flat


def test_back_closes_the_sheet_before_it_leaves_the_page():
    """The shell's back handler walks the WebView's history first, so the sheet
    pushes an entry and closes on popstate — back never throws the reader out
    of the app from an explanation."""
    assert "history.pushState({sheet: 1}, '')" in PAGE
    assert "window.addEventListener('popstate', shut)" in PAGE


def test_a_drag_inside_the_sheet_cannot_reload_the_page():
    """Opened with the page scrolled to the top, a downward drag in the sheet
    read as the shell's pull-to-refresh and reloaded the page out from under
    the reader. The page tells the shell it is not at the top while the sheet
    is open, and keeps the answer true afterwards on every scroll."""
    ts = PAGE.split("function tellShell")[1].split("\n  }\n")[0]
    assert "MiraiShell.atTop(!isOpen() && window.scrollY <= 0)" in ts
    assert "window.addEventListener('scroll', () => { if(spoke) tellShell(); }" in PAGE


def _code(js):
    return "\n".join(l.split("//")[0] for l in js.splitlines()
                     if not l.strip().startswith(("//", "*", "/*")))


# --- behaviour, where a JS runtime is available -------------------------------


def _run(js):
    out = subprocess.run([_NODE, "-e", "const g=require(%s);%s" % (json.dumps(str(M / "glance.js")), js)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not _NODE, reason="node is not installed")
def test_rows_order_by_price_merge_and_grey_out():
    rows = _run("""
      const a=g.levelRows({call:[{strike:1485,cluster_share_of_book_gamma_pp:9}],
                           put:[{strike:1470,cluster_share_of_book_gamma_pp:8}]},
                          {strike:1500,contracts:6755}, null, 1478);
      const b=g.levelRows({call:[{strike:1700,cluster_share_of_book_gamma_pp:20}],
                           put_side_has_no_wall:true},
                          {strike:1700,contracts:9000},
                          {strike:1700,share_pct:20,role:'call',holds_most_contracts:true}, 1705);
      const c=g.levelRows(null, null, null, 1694);
      console.log(JSON.stringify({a,b,c}));""")
    # 08-28 11:51: the most-contracts strike ABOVE the call wall draws first
    assert [(r["kind"], r["strike"]) for r in rows["a"]] == [("most", 1500), ("wall", 1485), ("wall", 1470)]
    # one row for a shared strike; price through it greys it; the empty side is a row
    b = rows["b"]
    assert len(b) == 2
    assert b[0]["strike"] == 1700 and b[0]["most"]["count"] == 9000
    assert b[0]["passed"] is True and b[0]["heaviest"] is True
    assert b[1] == {"kind": "absent", "side": "put", "text": "None below price"}
    # nothing measured is three rows saying so, never zeros
    assert [r["text"] for r in rows["c"]] == ["Not measured"] * 3


@pytest.mark.skipif(not _NODE, reason="node is not installed")
def test_heavier_draws_thicker_and_the_note_names_the_0910_case():
    got = _run("""
      const lv={most_contracts:{strike:1700,contracts:16419,traded_today:14066},
                heaviest:{strike:1600,share_pct:15,role:'put_further',holds_most_contracts:false}};
      console.log(JSON.stringify({
        w:[3.8,6.3,10.4,25.6,30,45].map(g.wallStroke), none:g.wallStroke(null),
        note:g.lightNote(lv),
        hid:[g.lightNote({most_contracts:lv.most_contracts, heaviest:{...lv.heaviest, role:null}}),
             g.lightNote({most_contracts:lv.most_contracts, heaviest:{...lv.heaviest, holds_most_contracts:true}}),
             g.lightNote({most_contracts:lv.most_contracts, heaviest:{...lv.heaviest, strike:1700}})]}));""")
    w = got["w"]
    assert w == sorted(w) and w[0] > 1.2 and w[-1] == w[-2] == 7.6    # monotone, capped at 30%
    assert got["none"] == 1.8                                           # a default, never a claim
    assert got["note"] == {"side": "put", "further": True, "heavy": 1600, "share": 15,
                           "strike": 1700, "count": 16419, "traded": 14066}
    assert got["hid"] == [None, None, None]
