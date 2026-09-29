"""The SPX JEV page (jev-spx.html): the SNDK JEV page's two modes and calls in play over the SPX service's
cards (skills/spx-jev), with every time drawn in the viewer's own zone. The SPX card writes every time as a
full stamp with its offset, so the page draws each one through one formatter, viewerTime, in whatever zone
the phone is in; the schedule (which mode leads, when to poll, what "today" is) stays on the New York
market clock. As in test_phone_jev_calls.py, the rules are functions in the page, lifted out by name and
run in node against a stand-in DOM, here under a TZ that is not New York's."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_phone_jev_calls import FAKE_DOM, _flat_text

M = Path(__file__).resolve().parents[1] / "static" / "m"
SPX = (M / "jev-spx.html").read_text()
JS = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", SPX))
_NODE = shutil.which("node")
LA, TOKYO, NY = "America/Los_Angeles", "Asia/Tokyo", "America/New_York"


def _fn(name):
    m = re.search(r"\n  function %s\(.*?(?=\n  (?:function |var |//|[a-z]))" % re.escape(name), JS, re.S)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _var(name):
    m = re.search(r"\n  var %s = .*?;" % re.escape(name), JS)
    assert m, f"{name} is gone from the page"
    return m.group(0)


# the clock the page runs on, fixed: Date.now() and new Date() both read D.now when the data carries one
FIXED_NOW = """
if(D && D.now){ var RealDate = Date, NOW = RealDate.parse(D.now);
  Date = function(a){ return arguments.length ? new RealDate(a) : new RealDate(NOW); };
  Date.now = function(){ return NOW; }; Date.parse = RealDate.parse; Date.prototype = RealDate.prototype; }
"""


def _run(js, data=None, tz=LA):
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + FAKE_DOM
              + "".join(_fn(f) for f in ("viewerTime", "marketAt", "marketWords", "cap", "pct", "words", "startOf", "endOf",
                                          "leftWords", "verdict", "endPrice", "gradeOf", "callWords", "laneLeads", "svgEl", "lastLaneRead", "hhmm", "marketClock",
                                          "marketDay", "sentence", "callSum", "endVerdict", "fallbackWords", "fallbackLine"))
              + _var("VIEWER_FMT") + _var("WEEKDAYS") + _var("MARKET_TIME") + _var("NS") + _var("ROW_H") + js)
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=20,
                         env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def call(read, mark, pick, p, **grade):
    minutes = (int(mark[:2]) * 60 + int(mark[3:])) - (int(read[:2]) * 60 + int(read[3:]))
    return {"read": f"2026-09-28T{read}:00-04:00", "mark": f"2026-09-28T{mark}:00-04:00",
            "minutes": minutes, "pick": pick, "p": p, **grade}


def graded(label, verdict, g=0.0, edge=1.59, **more):
    """A call's ``integral`` as the service puts it on the card (service.day_integral): its grade on the average price."""
    return {"graded": True, "label": label, "g": g, "edge": edge, "verdict": verdict, "running": [label] * 10,
            "best": {"points": g, "minute": 10}, "worst": {"points": 0.0, "minute": 1}, **more}


MORNING = [call("09:50", "10:00", "flat", 0.42), call("09:45", "09:55", "up_small", 0.38),
           call("09:40", "09:50", "flat", 0.51, integral=graded("flat", "right"), end_price={"outcome": "flat", "hit": True}),
           call("09:35", "09:45", "down_small", 0.33, integral=graded("up", "wrong", 2.1), end_price={"outcome": "up_small", "hit": False})]
# the owner's S9 at 10:30: down_small, the average -4.52 against 1.59 (down from the second minute on), the end price
# down big, 10.15 points under the read
S9 = {**call("10:30", "10:40", "down_small", 0.38),
      "integral": {"graded": True, "label": "down", "g": -4.52, "edge": 1.59, "verdict": "right", "margin": 2.844,
                   "size": {"band": "down_big", "size": "big", "call": "small", "right": False},
                   "best": {"points": -0.93, "minute": 1}, "worst": {"points": -10.15, "minute": 10},
                   "sharp_move": {"points": -3.44, "minute": 9, "higher_than": 12, "of": 20, "sharp": False, "note": "no sharp move"},
                   "running": ["flat"] + ["down"] * 9},
      "end_price": {"outcome": "down_big", "hit": False, "moved": {"realized_points": -10.15, "realized_units": 2.42}},
      "odds": {"down_big": 0.2, "down_small": 0.38, "flat": 0.3, "up_small": 0.05, "up_big": 0.02, "unsure": 0.05}}


# ---- the wiring


def test_the_page_reads_the_spx_cards_and_keeps_the_sndk_pages_modes():
    assert "var URL_ = '/api/raw/file?root=state&path=spx_jev/latest.json';" in JS
    assert "var TAPE_URL = '/api/raw/file?root=state&path=spx_jev/lanes/tape/latest.json';" in JS
    assert "fetch(URL_, {cache:'no-store'})" in JS and "fetch(TAPE_URL, {cache:'no-store'})" in JS
    assert "shownLeads = laneLeads(tape, Date.now(), marketDay());" in _fn("paint")
    assert "main.appendChild(laneCard(tape, tapeOk));" in _fn("paint") and "main.appendChild(foldLive(c));" in _fn("paint")
    assert "sumCard(c, main, count);" in _fn("paint") and "openingDone(tape)" in _fn("paint")
    assert "var ex = expiryLine(c); if(ex) main.appendChild(ex);" in _fn("paint")
    assert "laneLeads(tape, Date.now(), marketDay()) !== shownLeads" in _fn("tick")
    assert "setInterval(function(){ if(!document.hidden){ poll(); pollTape(); tick(); } }, POLL_MS);" in JS
    # the live price rides the station's existing quote route, every SPOT_MS in a session the card is today's, and on load and waking
    assert "var SPOT_URL = '/api/spot?ticker=SPX', SPOT_MS = 5000, SESSION_HOURS = ['09:30', '16:00'];" in JS
    assert "setInterval(function(){ if(!document.hidden && todaysCard() && within(SESSION_HOURS)) pollSpot(); }, SPOT_MS);" in JS
    assert "pollSpot();" in _fn("wake") and "poll(); pollTape(); pollSpot(); armMark(); armTapeMark();" in JS
    assert "LANE_HOURS = ['09:33', '16:05']" in JS and "within(LANE_HOURS)" in _fn("pollTape")
    assert "'jev.spx.folded'" in JS and "'jev.folded'" not in JS, "the SPX folds are kept apart from the SNDK ones"
    assert "<title>SPX · JEV</title>" in SPX and '<span id="h1">SPX &middot; JEV</span>' in SPX
    assert "innerHTML" not in JS
    # the same call sheet as the SNDK page, opened by sheet.js loaded first
    assert 'id="jevSheet" role="dialog" aria-modal="true" aria-hidden="true"' in SPX
    assert SPX.index('<script src="/m/sheet.js"></script>') < SPX.index("'use strict'")


def test_every_time_on_the_page_goes_through_the_one_formatter():
    """No time is read off a stamp's text: the SNDK page's clock() prints the wall time as written (New York
    time), which is what this page must never do. Nothing adds minutes to an "HH:MM" string either."""
    assert not re.search(r"(?<![A-Za-z])clock\(", JS), "a stamp's own wall time is drawn somewhere"
    assert "hhmmPlus" not in JS and "lastRead(" not in JS
    assert "new Intl.DateTimeFormat('en-US', {weekday" in _fn("viewerTime")
    assert "timeZone:" not in _fn("viewerTime"), "the viewer's zone is the phone's own, never named"
    # the market clock stays New York's, and is used only for the schedule
    assert "timeZone: 'America/New_York'" in _fn("marketClock") and "timeZone: 'America/New_York'" in _fn("marketDay")
    for f in ("clockBlock", "callsSvg", "openCall", "movedWords", "foldLive", "laneCard", "question", "subLine", "expiryLine"):
        assert "viewerTime(" in _fn(f), f


# ---- the viewer's zone


@pytest.mark.parametrize("tz, plain, zoned, day", [
    (LA, "06:35", "06:35 PDT", "Mon 06:35 PDT"),
    (TOKYO, "22:35", "22:35 GMT+9", "Mon 22:35 GMT+9"),
    (NY, "09:35", "09:35 EDT", "Mon 09:35 EDT"),
])
def test_a_read_stamped_in_new_york_is_drawn_in_the_viewers_zone(tz, plain, zoned, day):
    got = _run("var t = '2026-09-28T09:35:00-04:00'; console.log(JSON.stringify([viewerTime(t), viewerTime(t, {zone: true}), "
               "viewerTime(t, {day: true, zone: true}), viewerTime(Date.parse(t)), viewerTime(null), viewerTime('')]));", {}, tz)
    assert got == [plain, zoned, day, plain, "?", "?"]


def test_a_time_past_midnight_in_tokyo_is_the_next_day_there():
    got = _run("console.log(JSON.stringify(viewerTime('2026-09-28T11:30:00-04:00', {day: true, zone: true})));", {}, TOKYO)
    assert got == "Tue 00:30 GMT+9"


def test_market_clock_words_the_service_wrote_are_redrawn_in_the_viewers_zone():
    got = _run("""console.log(JSON.stringify([
        marketWords('not due: cadence 60 min, last asked 09:31 ET', D.at),
        marketWords('late morning, 11:00 to 12:00', D.at),
        marketWords('a scheduled event is ahead: the Fed decision, due in 25 minutes, at 14:00 ET', D.at),
        marketWords('0.09 of a normal day', D.at),
        viewerTime(marketAt('2026-12-01T10:05:00-05:00', '09:45'))]));""", {"at": "2026-09-28T11:01:56-04:00"}, LA)
    assert got == ["not due: cadence 60 min, last asked 06:31", "late morning, 08:00 to 09:00",
                   "a scheduled event is ahead: the Fed decision, due in 25 minutes, at 11:00", "0.09 of a normal day",
                   "06:45"]                                   # in winter New York is five hours behind UTC, the offset says so


def test_the_calls_in_play_axis_and_tap_targets_read_in_the_viewers_zone():
    js = _side() + _fn("fitting") + _fn("fits") + _fn("callsSvg") + "console.log(JSON.stringify(dump(callsSvg(D.calls, Date.parse(D.at)))));"
    svg = _run(js, {"calls": MORNING, "at": "2026-09-28T09:51:00-04:00"}, TOKYO)
    texts = [k for k in svg["kids"] if k["tag"] == "text"]
    assert [t["text"] for t in texts if t["attrs"].get("class") == "t-axis"][:3] == ["22:35", "22:45", "22:55"]
    hits = [k for k in svg["kids"] if k["tag"] == "rect" and k["attrs"].get("class") == "hit"]
    assert hits[1]["attrs"]["aria-label"] == "The 22:45 call, Up small 38%: its odds and result"
    side = [t["text"] + "".join(k["text"] for k in t["kids"]) for t in texts if t["attrs"].get("class") == "t-side"]
    assert side == ["9 min left", "4 min left", "Was Flat · Right", "Was Up · Wrong"]
    assert all(float(t["attrs"]["x"]) <= 300 for t in texts)


def _sheet(c, now, tz=LA):
    stubs = """
      var nodes = {csTitle: el('div'), csBody: el('div')};
      function $(id){ return nodes[id]; }
      var MiraiSheet = {open: function(){ nodes.opened = true; }};
      function tag(t){ return el('div', 'tag', sentence(t)); }
      function bar(name, p, pick){ var b = el('div', 'bar' + (pick ? ' pick' : '')); b.textContent = name.replace(/_/g, ' ') + ' ' + Math.round(p * 100) + '%'; return b; }
    """
    js = (stubs + _odds() + _var("MIN_WORD") + "".join(_fn(f) for f in ("unitsWords", "movedWords", "signed", "sizeWords", "pathWords", "soFarWords", "sharpWords", "runningWords")) +
          _fn("openCall") + "openCall(D.c, {}); console.log(JSON.stringify({title: nodes.csTitle.textContent, body: dump(nodes.csBody)}));")
    return _run(js, {"c": c, "now": now}, tz)


def _result(c, now, tz=LA):
    return [_flat_text(k) for k in _sheet(c, now, tz)["body"]["kids"][0]["kids"]]


def test_the_sheet_gives_the_average_price_verdict_the_size_and_the_path_with_the_end_price_muted_under_them():
    """S9: right on the average price, its direction deciding, though the end price, down big against a small call,
    is wrong; the size, measured at the end price, is a line of its own, the path names where price stood highest and
    lowest whatever the call, and the end price's verdict is a muted line at the foot."""
    got = _sheet(S9, "2026-09-28T10:45:00-04:00")
    assert got["title"] == "The 07:30 call · looks 10 min ahead"
    assert [_flat_text(k) for k in got["body"]["kids"][0]["kids"]] == [
        "RightResult", "The average price over the window was Down. The call said Down small 38%.",
        "Size at the end price: called Down small, ended Down big.",
        "Average \u22124.52 vs \u00B11.59 · high \u22120.93 at min\u00a01 · low \u221210.15 at min\u00a010",
        "Minute by minute, the average stood flat min\u00a01 · down min\u00a02\u2013\u206010.",
        "At the end price: Wrong, it ended Down big. Price ended 10.15 points lower at 07:40 than at the read, 2.42 tape units."]
    assert got["body"]["kids"][0]["kids"][-1]["attrs"]["class"] == "cs-end"
    # on the 30-minute box a call names no size: no size line; a finished window's runs are never "so far"
    live = _result({**S9, "pick": "down", "integral": {**S9["integral"], "size": None}}, "2026-09-28T10:45:00-04:00")
    assert not any(t.startswith("Size") for t in live) and not any("so far" in t for t in live)


def test_the_sheet_names_the_tier_in_words_that_say_right_and_a_sharp_move_once_the_service_has_them():
    """The tier (service.day_integral, from the box's last 20 sessions) replaces the bare verdict, the lowest of a
    right call's three reading Weak right, never a bare Weak; a sharp move is a note."""
    sharp = {**S9, "integral": {**S9["integral"], "tier": "Strong right",
                                "sharp_move": {"points": -3.44, "minute": 9, "higher_than": 20, "of": 20, "sharp": True}}}
    got = _result(sharp, "2026-09-28T10:45:00-04:00")
    assert got[0] == "Strong rightResult" and got[4] == (
        "A sharp move: the biggest minute, \u22123.44 at min\u00a09, was bigger than the biggest in this window on 20 of the last 20 sessions.")
    weak = _result({**S9, "integral": {**S9["integral"], "tier": "Weak right"}}, "2026-09-28T10:45:00-04:00")
    assert weak[0] == "Weak rightResult" and not any(t.startswith("A sharp move") for t in weak)
    stale = _result({**S9, "integral": {**S9["integral"], "stale_read": True}}, "2026-09-28T10:45:00-04:00")
    assert "The read\u2019s price was stale, so this call is kept out of the average-price loop." in stale
    assert not any("stale" in t for t in _result(S9, "2026-09-28T10:45:00-04:00"))


def test_an_open_call_shows_its_average_so_far():
    """While a call's window runs, the sheet gives the partial grade the card carries (service.open_grade): the
    average over the minutes finished against the whole window's edge, its side, and the minute it stands at in the
    viewer's zone. Without one it says when one comes, and never a verdict."""
    open_call = {**call("10:30", "10:40", "down_small", 0.38), "odds": S9["odds"],
                 "so_far": {"g": -2.31, "edge": 1.59, "label": "down", "minutes": 5, "of": 10, "as_of": "2026-09-28T10:35:00-04:00"}}
    got = _result(open_call, "2026-09-28T10:36:00-04:00", TOKYO)
    assert got[0] == "Open4 min left" and got[2] == "Average so far \u22122.31 vs \u00B11.59: down, as of 23:35."
    bare = _result({k: v for k, v in open_call.items() if k != "so_far"}, "2026-09-28T10:36:00-04:00")
    assert len(bare) == 3 and "so far" in bare[2] and not any(w in "".join(bare) for w in ("Right", "Wrong", "Passed"))


def test_a_call_with_no_average_price_grade_stands_on_its_end_price_said_so():
    """The average could not grade it (the average-price grade failed, or its window missed bars): the row and the sheet
    give the end price's verdict, marked as the end price's alone ("Ended", "End price only"), as the tally counts it
    (service.call_verdict), never "Grade pending" for ever."""
    live = {**call("10:32", "11:02", "flat", 0.7), "odds": {"up": 0.2, "down": 0.1, "flat": 0.7},
            "end_price": {"outcome": "flat", "hit": True, "moved": {"realized_sigma": 0.02}}, "end_price_only": True}
    assert _result(live, "2026-09-28T11:04:00-04:00", TOKYO) == [
        "RightEnd price only", "No average-price grade for this call, so it stands on its end price alone. It ended Flat. The call said Flat 70%.",
        "Price ended 0.02 of a normal day\u2019s move higher at 00:02 than at the read."]
    words = "console.log(JSON.stringify(callWords(D.c, Date.parse(D.now))));"
    assert _run(words, {"c": live, "now": "2026-09-28T11:04:00-04:00"}) == {"text": "Ended Flat · ", "strong": "Right", "short": "Flat · "}
    holed = {**live, "pick": "unsure", "integral": {"graded": False, "reason": "not graded: bars missing"}}
    got = _result(holed, "2026-09-28T16:30:00-04:00")
    assert got[0] == "PassedEnd price only" and got[1].startswith("No average-price grade for this call (bars missing), so it stands")
    assert _run(words, {"c": holed, "now": "2026-09-28T16:30:00-04:00"}) == {"text": "Ended Flat · ", "strong": "Passed", "short": "Flat · "}
    # the window closed and nothing graded yet at all: pending, until the next run
    pending = {k: v for k, v in live.items() if k not in ("end_price", "end_price_only")}
    assert _result(pending, "2026-09-28T11:04:00-04:00", TOKYO)[0] == "Grade pending"
    assert _run(words, {"c": pending, "now": "2026-09-28T11:04:00-04:00"}) == {"text": "Grade pending"}


def test_a_card_from_before_the_average_price_grade_still_gives_its_end_price():
    c = {**call("09:45", "09:55", "up_small", 0.38, outcome="up_big", hit=False, moved={"realized_points": 8.22, "realized_units": 1.962}),
         "odds": {"down_big": 0.05, "down_small": 0.12, "flat": 0.3, "up_small": 0.38, "up_big": 0.1, "unsure": 0.05}}
    got = _result(c, "2026-09-28T10:02:00-04:00")
    assert got[0] == "WrongEnd price only" and "It ended Up big. The call said Up small 38%." in got[1]
    assert got[2] == "Price ended 8.22 points higher at 06:55 than at the read, 1.96 tape units."
    small = {**c, "moved": {"realized_points": -1.5, "realized_units": 0.358}}
    assert _result(small, "2026-09-28T10:02:00-04:00")[2] == "Price ended 1.50 points lower at 06:55 than at the read, 0.36 of a tape unit."
    assert _run("console.log(JSON.stringify(callWords(D.c, Date.parse(D.now))));", {"c": c, "now": "2026-09-28T10:02:00-04:00"}) == \
        {"text": "Ended Up big · ", "strong": "Wrong", "short": "Up · "}


def test_the_calls_row_gives_the_average_prices_verdict_its_direction_deciding():
    """S9 called down small and fell big: right. S1 called flat and sat up on average though it ended flat: wrong."""
    s1 = call("13:01", "13:31", "flat", 0.5, integral=graded("up", "wrong", 4.45, 3.19), end_price={"outcome": "flat", "hit": True})
    got = _run("console.log(JSON.stringify(D.c.map(function(c){ return callWords(c, Date.parse(D.now)); })));",
               {"c": [S9, s1], "now": "2026-09-28T14:00:00-04:00"})
    assert got == [{"text": "Was Down · ", "strong": "Right", "short": "Down · "}, {"text": "Was Up · ", "strong": "Wrong", "short": "Up · "}]


def test_the_sheet_gives_the_reason_a_call_was_not_graded_in_the_viewers_zone():
    """The grader writes its reason in market words ("no settled open (the 09:34 bar)"); the sheet redraws the time."""
    c = {**call("09:45", "09:55", "up_small", 0.38,
                closed="halted window: no settled open (the 09:34 bar) on a finished day"),
         "odds": {"down_big": 0.05, "down_small": 0.12, "flat": 0.3, "up_small": 0.38, "up_big": 0.1, "unsure": 0.05}}
    assert [_flat_text(k) for k in _sheet(c, "2026-09-28T16:30:00-04:00")["body"]["kids"][0]["kids"]] == \
        ["Not graded", "Halted window: no settled open (the 06:34 bar) on a finished day."]


def _opening(tallies):
    sched = {"reads": ["2026-09-28T09:35:00-04:00", "2026-09-28T10:30:00-04:00"], "looks_ahead_min": 10}
    return _run(_fn("tallyWords") + _fn("openingDone") + "console.log(JSON.stringify(D.t.map(function(t){ return openingDone(t).kids[1].textContent; })));",
                {"t": [{"row_ts": "2026-09-28T10:30:00-04:00", "schedule": sched, "tally": t} for t in tallies]})


def test_an_unsure_call_is_passed_on_the_page_with_its_lean_never_a_wrong_one():
    """The service counts a call whose pick was unsure as passed (service.calls_block): the morning's line says how
    many of the calls graded right or wrong were right, and the passes apart; the call itself says Passed and where
    it leaned, never Wrong, its end price's line too. S8: the average sat down."""
    now = "2026-09-28T10:45:00-04:00"
    unsure = call("09:35", "09:45", "unsure", 0.4, integral=graded("down", "passed", -4.03, 2.41, lean={"direction": "flat", "p": 0.45}),
                  end_price={"outcome": "down_small", "hit": False})
    assert _run("console.log(JSON.stringify(callWords(D.c, Date.parse(D.now))));", {"c": unsure, "now": now}) == \
        {"text": "Was Down · ", "strong": "Passed", "short": "Down · "}
    assert _opening([{"calls": 4, "graded": 4, "right": 2, "passed": 1}, {"calls": 6, "graded": 4, "right": 2, "passed": 1},
                     {"calls": 8, "graded": 8, "right": 5, "passed": 0}, {"calls": 8, "graded": 7, "right": 0, "passed": 7}]) == [
        "2 of 3 calls right · 1 passed", "2 of 3 calls right · 1 passed · 2 still to grade", "5 of 8 calls right", "7 passed · 1 still to grade"]
    sheet = _result({**unsure, "odds": {"down_big": 0.1, "down_small": 0.2, "flat": 0.2, "up_small": 0.05, "up_big": 0.05, "unsure": 0.4}}, now)
    assert sheet[:2] == ["PassedLeaned flat 45%", "The average price over the window was Down. The call said Unsure 40%. Unsure makes no call, "
                                                  "so it is passed: counted apart from the calls right and wrong, never as a miss."]
    assert sheet[-1] == "At the end price: Passed, it ended Down small." and not any("Wrong" in t or "Unsure," in t for t in sheet)


def test_a_morning_with_nothing_graded_says_so_and_an_old_cards_unsure_reads_as_passed():
    """A tally with nothing graded reads "no calls graded yet", never "0 of 0"; a card written before the average-price
    grade counted its passes as unsure, and they read as passes, never "0 of 7"."""
    assert _opening([{"calls": 3, "graded": 0, "right": 0, "passed": 0},
                     {"calls": 8, "graded": 8, "right": 0, "unsure": 7}, {"calls": 3, "graded": 3, "right": 1}]) == [
        "no calls graded yet", "0 of 1 calls right · 7 passed", "1 of 3 calls right"]


def test_the_page_words_the_tally_as_the_close_out_log_does(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "skills" / "spx-jev"))
    from spx_jev.service import tally_words
    tallies = [{"calls": c, "graded": g, "right": r, "passed": p, "end_price_only": e} for c, g, r, p, e in
               ((4, 4, 2, 1, 0), (6, 4, 2, 1, 0), (8, 8, 5, 0, 0), (8, 7, 0, 7, 0), (3, 0, 0, 0, 0), (2, 2, 0, 2, 0), (5, 4, 2, 1, 2))]
    assert _opening(tallies) == [tally_words(t) for t in tallies]



def test_a_call_closed_for_good_is_never_graded_on_the_page_not_still_to_grade():
    """A lane call whose window was halted is closed for good (tally.closed): the morning's line counts it apart, in
    the service's words (service.tally_words), never as a call still to grade. A card from before the average-price
    grade counted its passes as unsure."""
    sched = {"reads": ["2026-09-29T09:35:00-04:00", "2026-09-29T10:30:00-04:00"], "looks_ahead_min": 10}
    lines = _run(_fn("tallyWords") + _fn("openingDone") + "console.log(JSON.stringify(D.t.map(function(t){ return dump(openingDone(t)); })));", {"t": [
        {"row_ts": "2026-09-29T10:30:00-04:00", "schedule": sched, "tally": {"calls": 12, "graded": 11, "right": 3, "closed": 1}},
        {"row_ts": "2026-09-29T10:30:00-04:00", "schedule": sched, "tally": {"calls": 12, "graded": 9, "right": 3, "closed": 1}},
        {"row_ts": "2026-09-29T10:30:00-04:00", "schedule": sched, "tally": {"calls": 12, "graded": 11, "right": 3, "unsure": 2, "closed": 1}},
        {"row_ts": "2026-09-29T10:30:00-04:00", "schedule": sched,
         "tally": {"calls": 12, "graded": 11, "right": 3, "passed": 2, "end_price_only": 1, "closed": 1}},
        {"row_ts": "2026-09-29T10:30:00-04:00", "schedule": sched, "tally": {"calls": 1, "graded": 0, "right": 0, "passed": 0, "closed": 1}}]})
    assert [_flat_text(l) for l in lines] == ["opening done3 of 11 calls right \u00B7 1 never graded",
                                              "opening done3 of 9 calls right \u00B7 2 still to grade \u00B7 1 never graded",
                                              "opening done3 of 9 calls right \u00B7 2 passed \u00B7 1 never graded",
                                              "opening done3 of 9 calls right \u00B7 2 passed \u00B7 1 on the end price only \u00B7 1 never graded",
                                              "opening done1 never graded"]


def test_the_30_minute_calls_day_score_is_shown_under_the_call_as_the_openings_is():
    """Monday's card and close-out log said 6 of 11 right, while the page drew at most four calls and never the score.
    Under the 30-minute call a line gives the day's score in the service's words, the opening's beside it."""
    got = _whole({k: MONDAY[k] for k in ("live", "tape", "premarket")}, "2026-09-28T16:04:00-04:00")
    assert ["card fold", "30-min calls, day done6 of 11 calls right"] in got["main"]
    assert ["card fold", "opening done0 of 8 calls right"] in got["main"]
    kinds = [c for c, _ in got["main"]]
    assert kinds.index("card dashed") + 1 == got["main"].index(["card fold", "30-min calls, day done6 of 11 calls right"])
    live = {**MONDAY["live"], "closed_out_at": None, "tally": {"calls": 5, "graded": 3, "right": 1, "passed": 1, "end_price_only": 0, "closed": 0}}
    got = _whole({"live": live, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
    assert ["card fold", "30-min calls so far1 of 2 calls right \u00B7 1 passed \u00B7 2 still to grade"] in got["main"]
    none = {**live, "tally": {"calls": 0, "graded": 0, "right": 0}}
    got = _whole({"live": none, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
    assert not any(t.startswith("30-min calls") for _, t in got["main"])


def test_a_call_closed_at_its_end_price_but_graded_on_the_average_reads_graded_on_the_page():
    """The opening lane's end price can close a call its average still grades (service.calls_block counts it graded, never
    closed): its row and its sheet give the average's verdict, not "Not graded", and the morning's line counts it once."""
    both = call("10:30", "10:40", "up", 0.5, integral=graded("up", "right"), closed="halted window: no bar at the mark on a finished day")
    got = _run("console.log(JSON.stringify(callWords(D.c, Date.parse(D.now))));", {"c": both, "now": "2026-09-28T10:45:00-04:00"})
    assert got == {"text": "Was Up \u00B7 ", "strong": "Right", "short": "Up \u00B7 "}
    assert _result(both, "2026-09-28T10:45:00-04:00")[0] == "RightResult"
    sched = {"reads": ["2026-09-29T09:35:00-04:00", "2026-09-29T10:30:00-04:00"], "looks_ahead_min": 10}
    tally = {"calls": 2, "graded": 1, "right": 1, "passed": 0, "end_price_only": 0, "closed": 0}
    line = _run(_fn("tallyWords") + _fn("openingDone") + "console.log(JSON.stringify(dump(openingDone(D.t))));",
                {"t": {"row_ts": "2026-09-29T10:30:00-04:00", "schedule": sched, "tally": tally}})
    assert _flat_text(line) == "opening done1 of 1 calls right \u00B7 1 still to grade"


def test_a_close_out_that_leaves_a_call_to_grade_still_redraws_the_page():
    """09-28: the close-out graded the pre-market open_10 check but not open_30, whose bar had not come, so the card kept
    its read's generated_at and no closed_out_at, and an open page kept the ungraded card until a later run graded
    both. Every close-out run stamps graded_at, and the page's test for a new card reads it."""
    read = {"generated_at": "2026-09-28T13:50:05+00:00", "row_ts": "2026-09-28T09:34:00-04:00"}
    partial = {**read, "graded_at": "2026-09-28T14:06:07+00:00"}
    done = {**partial, "graded_at": "2026-09-28T15:02:40+00:00", "closed_out_at": "2026-09-28T15:02:40+00:00"}
    sigs = _run(_fn("sig") + "console.log(JSON.stringify(D.c.map(sig)));", {"c": [read, partial, done, None]})
    assert len(set(sigs)) == 4 and sigs[3] == ""

# ---- the schedule stays on the market clock


def test_today_is_new_yorks_market_day_even_where_it_is_already_tomorrow():
    """At 11:14 in New York it is 00:14 the next day in Tokyo; the opening lane's card is still today's."""
    lane = {"lane": "tape", "row_ts": "2026-09-28T09:50:00-04:00", "calls": MORNING}
    got = _run("console.log(JSON.stringify([marketDay(), laneLeads(D.lane, Date.now(), marketDay())]));",
               {"now": "2026-09-28T09:51:00-04:00", "lane": {**lane}}, TOKYO)
    assert got == ["2026-09-28", True]
    got = _run("console.log(JSON.stringify([marketDay(), laneLeads(D.lane, Date.now(), marketDay())]));",
               {"now": "2026-09-28T23:30:00-04:00", "lane": lane}, LA)
    assert got == ["2026-09-28", False]


def test_the_next_read_is_found_on_the_market_clock_and_drawn_in_the_viewers_zone():
    js = ("var READ_MINUTES = [2, 32], LAST_READ_DEFAULT = '15:32';" + _fn("nextRead") +
          "var n = nextRead(Date.parse(D.last)); console.log(JSON.stringify(n == null ? null : viewerTime(n)));")
    assert _run(js, {"now": "2026-09-28T11:14:20-04:00", "last": "2026-09-28T15:32:00-04:00"}, LA) == "08:32"
    assert _run(js, {"now": "2026-09-28T11:40:00-04:00", "last": "2026-09-28T15:32:00-04:00"}, TOKYO) == "01:02"
    assert _run(js, {"now": "2026-09-28T15:40:00-04:00", "last": "2026-09-28T15:32:00-04:00"}, LA) is None
    assert _run(js, {"now": "2026-11-27T12:40:00-05:00", "last": "2026-11-27T12:32:00-05:00"}, LA) is None   # a half day
    assert _run(js, {"now": "2026-09-28T09:10:00-04:00", "last": "2026-09-28T15:32:00-04:00"}, LA) == "06:32"   # the day's first
    assert _run(js, {"now": "2026-09-28T08:40:00-04:00", "last": "2026-09-28T15:32:00-04:00"}, LA) is None   # 09:02 is before it


def test_the_header_line_names_the_row_in_the_viewers_time_and_knows_the_close():
    js = ("var READ_MINUTES = [2, 32], GONE_MIN = 60, STALE_MIN = 35, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          + _fn("nextRead") + _fn("ageWord") + _fn("cardCount") + _fn("subLine") + "console.log(JSON.stringify(subLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T11:01:56-04:00", "labels": 43,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": c, "m": 12}, LA) == "row 08:01, 12 min ago, 43 labels, next read 08:32"
    last = {**c, "row_ts": "2026-09-28T15:31:56-04:00"}
    assert _run(js, {"now": "2026-09-28T16:10:00-04:00", "c": last, "m": 38}, LA) == "after the close, last row 12:31, 43 labels"
    assert _run(js, {"now": "2026-09-28T15:50:00-04:00", "c": last, "m": 18}, TOKYO).startswith("row 04:31, 18 min ago")
    # the card's counts under their own names (service.card's labels_count), the bare name only on a card from before them
    counted = {**{k: v for k, v in c.items() if k != "labels"}, "labels_count": 43}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": counted, "m": 12}, LA) == "row 08:01, 12 min ago, 43 labels, next read 08:32"


def test_the_answered_chip_reads_the_cards_counts_under_their_own_names():
    """The card's fresh and held are counts, while a record's are the answers themselves (I25): the card writes
    fresh_count and held_count, and the chip reads them, or the bare names on a card from before them."""
    live = {k: v for k, v in MONDAY["live"].items() if k not in ("fresh", "held", "labels")}
    answered = sum(1 for q in live["questions"] if q.get("answer"))
    for c in ({**live, "fresh_count": 7, "held_count": 3, "labels_count": 119}, {**live, "fresh": 7, "held": 3, "labels": 119}):
        got = _whole({"live": c, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
        # the chip closes the leading card, in its own box; the top keeps only trouble
        card = next(d for k, d in zip(got["main"], got["dom"]) if k[0] == "card dashed")
        assert card["kids"][-1]["attrs"]["class"] == "state answered"
        assert _flat_text(card["kids"][-1]) == f"{answered} of {len(c['questions'])} answered, 7 fresh on the 12:31 row"
        assert got["state"] == []


def test_the_header_line_is_kept_for_a_stale_card_alone():
    """Will's layout of 09-29: the row line under the title is gone. A fresh card and the day's last one after the
    close say nothing there (the live price and the card's own clock do); a card gone stale mid-session still says so,
    in subLine's words, and an error is still said there (say)."""
    js = ("var READ_MINUTES = [2, 32], GONE_MIN = 60, STALE_MIN = 35, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          + _fn("nextRead") + _fn("ageWord") + _fn("cardCount") + _fn("subLine") + _fn("staleLine")
          + "console.log(JSON.stringify(staleLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T11:01:56-04:00", "labels_count": 43,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": c, "m": 12}, LA) == ""
    assert _run(js, {"now": "2026-09-28T11:40:00-04:00", "c": c, "m": 38}, LA) == "row 08:01, 38 min ago, stale, next read 09:02"
    last = {**c, "row_ts": "2026-09-28T15:31:56-04:00"}
    assert _run(js, {"now": "2026-09-28T16:10:00-04:00", "c": last, "m": 38}, LA) == ""
    # yesterday's last card in today's session (the service wrote nothing today) is said at the top
    assert _run(js, {"now": "2026-09-29T11:00:00-04:00", "c": last, "m": 1168}, LA) == "after the close, last row 12:31, 43 labels"
    assert "s.hidden = false;" in _fn("say") and "s.hidden = !text;" in _fn("setSub")
    assert "setSub(staleLine(c, m));" in _fn("paint") and "setSub(staleLine(c, m));" in _fn("ages")


# Widths measured in Chrome with the shipped face at the header line's 13px: the stale line with its label count ran
# past the 328px column on the owner's 360px phone and was cut before its next read ("next read 07:…")
_SUB_W = {"row 06:31, 39 min ago, stale, 125 labels, next read 07:32": 333.80, "row 06:31, 39 min ago, stale, next read 07:32": 268.56,
          "row 12:31, 20 hr 29 min ago, stale, next read 07:32": 297.38, "row 08:01, 12 min ago, 125 labels, next read 08:32": 298.19}


def test_a_stale_header_line_keeps_its_next_read_on_the_owners_360px_phone():
    """Monday 10:10 ET: the 09:31 card was 39 minutes old, and its line was cut at "next read 07:…". A stale line
    drops the label count, which says least about an old card, so the next read fits."""
    js = ("var READ_MINUTES = [2, 32], GONE_MIN = 60, STALE_MIN = 35, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          + _fn("nextRead") + _fn("ageWord") + _fn("cardCount") + _fn("subLine") + "console.log(JSON.stringify(subLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T09:31:20-04:00", "labels": 125,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    stale = _run(js, {"now": "2026-09-28T10:10:00-04:00", "c": c, "m": 39}, LA)
    assert stale == "row 06:31, 39 min ago, stale, next read 07:32"
    column = 360 - 2 * 16
    assert _SUB_W[stale] <= column < _SUB_W["row 06:31, 39 min ago, stale, 125 labels, next read 07:32"]
    assert _SUB_W["row 12:31, 20 hr 29 min ago, stale, next read 07:32"] <= column
    assert _SUB_W["row 08:01, 12 min ago, 125 labels, next read 08:32"] <= column


def test_the_last_lane_read_is_known_from_the_schedules_stamps():
    reads = [f"2026-09-28T{t}:00-04:00" for t in ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10",
                                                    "10:15", "10:20", "10:25", "10:30")]
    cards = [{"row_ts": f"2026-09-28T{t}:00-04:00", "schedule": {"reads": reads}} for t in ("10:30", "10:29", "10:27", "10:25")]
    cards.append({"row_ts": "2026-09-28T10:20:00-04:00", "schedule": {"reads": reads}, "closed_out_at": "2026-09-28T14:42:10+00:00"})
    assert _run("console.log(JSON.stringify(D.map(lastLaneRead)));", cards, TOKYO) == [True, True, False, False, True]
    # the hand-back and the held unit are instants from the card, drawn in the viewer's zone
    body = _fn("laneCard")
    assert "viewerTime(Date.parse(sch.reads[sch.reads.length - 1]) + sch.looks_ahead_min * 60000)" in body
    assert "viewerTime(marketAt(t.row_ts, RULER_HELD_UNTIL))" in body and "RULER_HELD_UNTIL = '09:45'" in JS
    assert "'One tape unit ' + (+r.unit_points).toFixed(1) + ' points'" in body


# ---- the expiry line


def _expiry(x, now, tz=LA):
    js = ("var tickers = [];" + _var("EXPIRY_TAGS") + _fn("untilWords") + _fn("expiryLine") +
          "var r = expiryLine({expiries: D.x}); console.log(JSON.stringify(r && r.kids.map(function(k){ return [k.attrs['class'] || k.tag, k.textContent]; })));")
    return _run(js, {"x": x, "now": now}, tz)


EXPIRIES = {"today_settles_at": "2026-09-30T16:00:00-04:00", "next_settles_at": "2026-10-01T16:00:00-04:00",
            "next_monthly_settles_at": "2026-10-16T16:00:00-04:00", "expiring_today": ["quarter_end"]}


def test_the_expiry_line_counts_down_to_todays_settle_and_tags_the_big_days():
    got = _expiry(EXPIRIES, "2026-09-30T11:14:00-04:00")
    assert got == [["span", "Today’s 0DTE settles 13:00 PDT"], ["b", "4 hr 46 min left"], ["state", "Quarter-end"]]
    opex = {**EXPIRIES, "today_settles_at": "2026-09-18T16:00:00-04:00", "expiring_today": ["monthly", "quarterly"]}
    assert _expiry(opex, "2026-09-18T15:31:00-04:00", TOKYO) == [
        ["span", "Today’s 0DTE settles 05:00 GMT+9"], ["b", "29 min left"], ["state", "Quarterly OpEx"]]
    month = {**EXPIRIES, "today_settles_at": "2026-10-16T16:00:00-04:00", "expiring_today": ["monthly"]}
    assert _expiry(month, "2026-10-16T09:40:00-04:00")[2] == ["state", "Monthly OpEx"]


def test_after_the_settle_the_line_names_the_next_expiry_with_its_day():
    assert _expiry(EXPIRIES, "2026-09-30T16:05:00-04:00") == [["span", "Next expiry settles Thu 13:00 PDT"]]
    # a card from an earlier day (its today is not New York's today) never counts down
    assert _expiry(EXPIRIES, "2026-10-01T09:40:00-04:00", TOKYO) == [["span", "Next expiry settles Fri 05:00 GMT+9"]]
    assert _expiry(None, "2026-09-30T11:14:00-04:00") is None


def test_every_line_the_page_writes_starts_with_a_capital():
    assert JS.count("el('div', 'tag', ") == 1 and JS.count("el('div', 'skip', ") == 1
    for word in ("'Today\\u2019s 0DTE settles '", "'Next expiry settles '", "'Flat within '", "'Big beyond '",
                 "'Price ended '", "'Read '", "'Graded '"):
        assert word in JS, word


# ---- the owner's 360px phone

# Widths measured in Chrome with the shipped face at 360 (Plus Jakarta Sans): each variable line at the
# widest form it takes. A zone name is widest as an offset ("GMT+5:30"); a countdown under 10 hours.
_W = {"SNDK · JEV": 75.06, "SPX · JEV": 63.72, "BETA": 40.73, "switch": 104.39,
      "Today’s 0DTE settles 13:00 GMT+5:30": 223.75, "6 hr 30 min left": 88.44,
      "Next expiry settles Mon 16:00 GMT+5:30": 237.14, "Quarterly OpEx": 129.09,
      "NORMAL · A CALL EVERY 30 MIN": 216.06, "Read 06:50 GMT+5:30 … Graded 07:00": 203.0}


def _rule(sel, html=SPX):
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", html)))
    m = re.search(r"(?m)^%s\{([^}]*)\}" % re.escape(sel), css)
    assert m, f"{sel} has no rule"
    return m.group(1).replace(" ", "").replace("\n", "")


def _px(sel, prop, html=SPX):
    m = re.search(r"(?:^|;)%s:(-?[\d.]+)px" % re.escape(prop), _rule(sel, html))
    assert m, f"{sel} has no {prop} in px"
    return float(m.group(1))


def test_the_page_fits_the_owners_360px_phone():
    """At 360, the Galaxy S20+, nothing runs past the page or is cut: the header row carries the name, the
    beta chip, the refresh spinner and the switch; the expiry line wraps between its parts, and each part
    fits the column alone; the calls in play and the odds are drawn in a 300-wide box scaled to the card.
    Checked in Chrome at 360x800 in both modes, in Los Angeles, Tokyo and New York, with no element past
    the page and none clipped; this pins the widths that check found."""
    side = 16
    column = 360 - 2 * side
    card = column - 2 * _px(".card", "padding")
    gap = _px(".hd-1", "gap")
    poll = _px(".poll", "width") + _px(".poll", "margin-left")
    assert "margin-left:auto" in _rule(".sw") and "flex:none" in _rule(".sw")
    for name in ("SNDK · JEV", "SPX · JEV"):
        row = _W[name] + gap + _W["BETA"] + gap + poll + gap + _W["switch"]
        assert row <= column, f"the header row with {name} is {row:.1f} of {column}"
    assert "flex-wrap:wrap" in _rule(".exp")
    pad = re.search(r"padding:0([\d.]+)px", _rule(".exp"))
    assert pad, ".exp lost its side padding"
    exp = column - 2 * float(pad.group(1))
    for part in ("Today’s 0DTE settles 13:00 GMT+5:30", "6 hr 30 min left", "Next expiry settles Mon 16:00 GMT+5:30", "Quarterly OpEx"):
        assert _W[part] <= exp, f"{part!r} is wider than the expiry line at 360"
    assert _W["NORMAL · A CALL EVERY 30 MIN"] <= column
    assert _W["Read 06:50 GMT+5:30 … Graded 07:00"] <= card
    # the drawings are 300 wide in their own units and scale to the card, so they never run past it
    assert "width:100%;height:auto" in _rule(".inplay") and "viewBox: '0 0 300 '" in _fn("callsSvg")
    assert _var("ODDS_TRACK_PX").strip() == "var ODDS_TRACK_PX = 294, ODDS_GAP_PX = 2, ODDS_FONT_PX = 11.5;"
    assert 294 == card - 2 * 1                                  # the narrowest card holding odds: a dashed one, 1px border a side
    assert "font:40011.5px/" in _rule(".odds-lab")              # ODDS_FONT_PX, the size the odds words are measured at


# Chrome at 360 with the shipped face: a calls row's words after its bar (10.5px, the verdict bold), and the sheet's
# verdict row (22px bold beside 11px capitals spaced .1em)
_ROW_W = {"Down · Wrong": 69.65, "Down · Passed": 72.33, "Flat · Passed": 61.86, "Grade pending": 75.27, "Never graded": 68.17,
          "Was Down · Passed": 95.38, "Was Down · Wrong": 92.69, "Down small · Passed": 99.81, "Ended Down small · Passed": 134.50}
_VERDICT_W = {"Strong right": 127.39, "Weak right": 114.72, "Result": 46.70, "Passed": 77.14, "Leaned down 100%": 133.14,
              "End price only": 104.70}


def test_the_newest_calls_verdict_fits_after_its_bar_on_the_owners_phone():
    """Once the newest call is graded (the close-out), its words have 84 of the drawing's 300 after its bar, and
    "Was Down · Passed" (95px in Chrome), or "Ended Down small · Passed" on the end price alone, would be cut at the
    card's edge: the newest row gives the side alone, the rows with room keep the whole. Checked in Chrome at 360x800
    on the live card's close-out with no text past the drawing."""
    calls = [call("10:30", "10:40", "unsure", 0.4, integral=graded("down", "passed")),
             call("10:25", "10:35", "down", 0.5, integral=graded("down", "right")),
             call("10:20", "10:30", "up", 0.5, integral=graded("down", "wrong")),
             call("10:15", "10:25", "flat", 0.5, integral=graded("flat", "right"))]
    js = _side() + _fn("fitting") + _fn("fits") + _fn("callsSvg") + "console.log(JSON.stringify(dump(callsSvg(D.calls, Date.parse(D.at)))));"
    svg = _run(js, {"calls": calls, "at": "2026-09-28T10:43:00-04:00"}, LA)
    side = [t for t in svg["kids"] if t["tag"] == "text" and t["attrs"].get("class") == "t-side"]
    assert [t["text"] + "".join(k["text"] for k in t["kids"]) for t in side] == [
        "Down · Passed", "Was Down · Right", "Was Down · Wrong", "Was Flat · Right"]
    room = 300 - float(side[0]["attrs"]["x"])
    assert room == 300 - (211 + 5) and _ROW_W["Was Down · Passed"] > room
    for words in ("Down · Wrong", "Down · Passed", "Flat · Passed", "Grade pending", "Never graded"):
        assert _ROW_W[words] <= room, f"{words!r} is cut after the newest bar at 360"
    assert _ROW_W["Was Down · Wrong"] <= 300 - float(side[1]["attrs"]["x"])
    assert _ROW_W["Down small · Passed"] > room                              # a five-band end price needs its side alone
    alone = [{**calls[0], "integral": None, "end_price": {"outcome": "down_small", "hit": False}, "end_price_only": True}] + calls[1:]
    svg = _run(js, {"calls": alone, "at": "2026-09-28T10:43:00-04:00"}, LA)
    first = next(t for t in svg["kids"] if t["tag"] == "text" and t["attrs"].get("class") == "t-side")
    assert first["text"] + "".join(k["text"] for k in first["kids"]) == "Down · Passed"


def test_the_sheets_new_lines_fit_or_wrap_on_the_owners_phone():
    """The verdict row holds its word beside its label on one line at 360; every other line of the result is a
    paragraph that wraps inside the sheet, never a line held to one row."""
    assert "padding:10px20px" in _rule(".sheet") and "padding:13px14px" in _rule(".sh-caveat")
    inside = 360 - 2 * 20 - 2 * 14
    gap = _px(".cs-verdict", "gap")
    for word, label in (("Strong right", "Result"), ("Weak right", "End price only"), ("Passed", "Leaned down 100%"), ("Passed", "End price only")):
        assert _VERDICT_W[word] + gap + _VERDICT_W[label] <= inside, f"{word} {label} runs past the sheet at 360"
    for sel in (".cs-result p", ".cs-result p.cs-end", ".sh-caveat"):
        assert "nowrap" not in _rule(sel) and "overflow" not in _rule(sel), sel
    assert "flex-wrap:wrap" in _rule(".fold") and "nowrap" not in _rule(".fold .l")   # the morning's tally wraps under its name


def test_the_footnote_says_once_what_the_average_over_the_window_is():
    foot = re.search(r'(?s)<p class="foot">(.*?)</p>', SPX).group(1)
    assert foot.count("average price over its window") == 1
    assert "passed, never counted as a miss" in foot and "direction decides" in foot and "end price alone" in foot
    assert "&plusmn;" in foot and "min 3" in foot


def test_a_call_is_a_forecast_not_a_trade_signal_and_shadow_means_only_the_shadow_questions():
    """The page named a thing "the call" and in the same view said it was "never a call", and called the end-price
    sums "in shadow" though they are graded. A call is the graded forecast, said to be no trade signal; shadow is kept
    for the shadow questions, asked and logged, never graded and never weighted (the question set's definition)."""
    assert "never a call" not in SPX and "Neither is a call" not in SPX and "in shadow" not in SPX
    assert JS.count("not a trade signal") == 4
    foot = re.search(r'(?s)<p class="foot">(.*?)</p>', SPX).group(1)
    assert "A call is a forecast, not a trade signal" in foot and "asked and logged, never graded and never weighted" in foot
    js = ("function bar(){ return el('div'); }" + _fn("tag") + _fn("skipLine") + _fn("skipWhy") + _fn("question") +
          "console.log(JSON.stringify(question(D.q, {}, D.at).kids.map(function(k){ return k.textContent; })));")
    q = {"id": "tape_log", "status": "shadow", "ask": "Did the tape lean?", "options": ["up", "down"],
         "answer": {"pick": "up", "probabilities": {"up": 0.6, "down": 0.4}}}
    got = _run(js, {"q": q, "at": "2026-09-28T11:02:00-04:00"})
    assert got[:2] == ["Did the tape lean?", "Shadow: asked and logged, never graded, never weighted"]


# Chrome at 360 with the shipped face (Plus Jakarta Sans) loaded: odds words as drawn, in bold when the pick. The
# first four were cut by a pixel or more at their share under the rule of 290px and 6.4px a letter; the rest fit their
# share and were left out under the rule of 7.9px a letter, "Unsure 27%" in 77px among them
_ODDS_W = {("Down 20%", False): 60.1875, ("Down 20%", True): 61.953125, ("Down 18%", True): 58.828125, ("Up 14%", True): 42.203125,
           ("Unsure 27%", False): 64.34375, ("Unsure 25%", False): 65.1875, ("Flat 16%", False): 44.421875, ("Up 15%", True): 41.75,
           ("Down small 31%", False): 86.234375, ("Up big 100%", True): 71.125}


def _odds():
    return (_var("ODDS_ORDER") + _var("ODDS_TRACK_PX") + _var("ODDS_LETTERS") + _var("ODDS_EM") + _fn("oddsKeys") + _fn("oddsWidth") +
            _fn("oddsBar"))


def test_an_odds_words_width_is_the_shipped_faces_to_a_64th_of_a_pixel():
    got = _run(_odds() + "console.log(JSON.stringify(D.w.map(function(w){ return oddsWidth(w[0], w[1]); })));",
               {"w": [list(k) for k in _ODDS_W]})
    for (word, bold), est in zip(_ODDS_W, got):
        assert _ODDS_W[word, bold] - 1 / 64 <= est <= _ODDS_W[word, bold] + 1.2, f"{word} bold={bold}: {est:.2f}px, Chrome {_ODDS_W[word, bold]}"


@pytest.mark.parametrize("tz", [LA, TOKYO])
def test_an_odds_word_is_drawn_exactly_where_it_fits_whole_on_the_owners_phone(tz):
    """The odds row's words are named only where they fit their share of the row. At 360 "Down 20%" ran a pixel or
    more past its 20% share in the pre-market card (dashed, so 294 wide less a 2px gap between the four options),
    shared with the live card; each word is now measured in the shipped face, so a word drawn is whole and a word
    that fits, "Unsure 27%" in 77px, is drawn."""
    for (word, bold), width in _ODDS_W.items():
        key, share = word.rsplit(" ", 1)[0].lower().replace(" ", "_"), int(word.rsplit(" ", 1)[1][:-1]) / 100
        keys = ["down_big", "down_small", "flat", "up_small", "up_big", "unsure"] if "_" in key else ["down", "flat", "up", "unsure"]
        o = {k: share if k == key else (1 - share) / (len(keys) - 1) for k in keys}
        pick = key if bold else next(k for k in keys if k != key)
        got = dict(_run(_odds() + "var b = oddsBar(D.o, D.pick); "
                        "console.log(JSON.stringify(oddsKeys(D.o).map(function(k, i){ return [k, b.kids[1].kids[i].textContent]; })));",
                        {"o": o, "pick": pick}, tz))
        room = share * (294 - 2 * (len(keys) - 1))
        assert got[key] == (word if width <= room else ""), f"{word} is {width}px wide in {room:.1f}px of room"
    wide = _run(_odds() + "console.log(JSON.stringify(dump(oddsBar(D.o, 'down'))));",
                {"o": {"down": 0.25, "flat": 0.45, "up": 0.25, "unsure": 0.05}}, tz)
    assert [k["text"] for k in wide["kids"][1]["kids"]] == ["Down 25%", "Flat 45%", "Up 25%", ""]   # 72px of room: they fit


# ---- the SNDK | SPX switch


def _switch_script(html):
    m = re.search(r"(?s)(<script>\n/\* the SNDK \| SPX switch.*?</script>)", html)
    return m.group(1) if m else None


def test_both_jev_pages_carry_one_switch_that_remembers_the_choice():
    """Will's choice, 2026-09-26: SPX lives inside the JEV tab, a segmented switch at the top of both pages,
    not a fourth tab. Each page lights its own side and links the other; the side last tapped is kept on the
    phone, and the JEV tab (which links the SNDK page) opens on it. The script is one copy on both pages,
    in <head> so a phone that chose SPX is sent there before the SNDK page paints."""
    sndk = (M / "jev.html").read_text()
    a, b = _switch_script(sndk), _switch_script(SPX)
    assert a and a == b, "the two pages keep the choice differently"
    assert sndk.index(a) < sndk.index("<body>") and SPX.index(b) < SPX.index("<body>")
    assert "try { if(localStorage.getItem('jev.symbol') === 'spx' && location.pathname === '/m/jev.html') location.replace('/m/jev-spx.html'); } catch(e){}" in a
    assert "try { localStorage.setItem('jev.symbol', a.getAttribute('data-sym')); } catch(x){}" in a
    assert ('<nav class="sw" aria-label="Symbol"><span class="on" aria-current="page">SNDK</span>'
            '<a href="/m/jev-spx.html" data-sym="spx">SPX</a></nav>') in sndk
    assert ('<nav class="sw" aria-label="Symbol"><a href="/m/jev.html" data-sym="sndk">SNDK</a>'
            '<span class="on" aria-current="page">SPX</span></nav>') in SPX
    # drawn alike on both pages, and the tab bar untouched: three tabs, JEV lit, the others linked
    for sel in (".sw", ".sw a,.sw span", ".sw a::after", ".sw .on"):
        assert _rule(sel, sndk) == _rule(sel, SPX), sel
    for html in (sndk, SPX):
        nav = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', html).group(1)
        assert re.findall(r'<a class="tab" href="([^"]+)"', nav) == ["/m/", "/m/thread.html"]
        assert nav.count('<span class="tab on">') == 1 and "<s>JEV <em" in nav


# ---- recovering from a failed fetch


def test_a_good_fetch_after_a_failed_one_clears_the_failure_though_the_card_is_the_same():
    """A failed fetch draws the last card with 'fetch failed'; the next good fetch of the same card draws it again
    so the pill goes, and after a station error the error text in the header goes the same way."""
    js = ("var last = null, lastOk = true, drawn = [], SUB = {dataset: {}, cls: {}, classList: {"
          "contains: function(k){ return !!SUB.cls[k]; }, add: function(k){ SUB.cls[k] = true; }, remove: function(k){ delete SUB.cls[k]; }}};"
          "function $(id){ return SUB; } function ages(){ drawn.push('ages'); } function tick(){}"
          "function draw(c, ok){ lastOk = ok; drawn.push(ok); if(ok) SUB.classList.remove('err'); }"
          "function setTimeout(){}" + _fn("arrived") +
          "var c = {generated_at: 'g1', row_ts: 'r1'};"
          "arrived(c); arrived(c); draw(last, false); arrived(c); arrived(c);"
          "SUB.classList.add('err'); arrived(c);"
          "console.log(JSON.stringify(drawn));")
    assert _run(js, {}) == [True, "ages", False, True, "ages", True]


def test_a_fetch_that_keeps_failing_draws_the_last_card_once_and_then_only_moves_its_ages():
    """Through an outage every 20-second poll redrew the whole page from the last card, and any fold a reader had
    opened snapped shut. The first failure draws the card once with the failure said; the next ones only move the
    ages, as the lane and pre-market polls already do; a good fetch draws it again (arrived)."""
    js = ("var last = {row_ts: 'r'}, lastOk = true, inflight = false, drawn = [], URL_ = 'u', reply = null;"
          "function pollPre(){} function polling(){} function say(){ drawn.push('say'); } function preOnly(){ return false; }"
          "function laneOnly(){ return false; } function arrived(){ drawn.push('arrived'); lastOk = true; }"
          "function draw(c, ok){ lastOk = ok; drawn.push('draw ' + ok); } function ages(){ drawn.push('ages'); }"
          "function fetch(){ return reply ? Promise.resolve({ok: true, json: function(){ return Promise.resolve(reply); }}) : Promise.reject(new Error('offline')); }"
          + _fn("failedFetch") + _fn("poll") +
          "var steps = [null, null, null, {kind: 'json', data: {questions: []}}, null, {kind: 'json'}];"
          "(function next(i){ if(i === steps.length){ console.log(JSON.stringify(drawn)); return; }"
          " reply = steps[i]; poll(); setTimeout(function(){ next(i + 1); }, 5); })(0);")
    assert _run(js, {}) == ["draw false", "ages", "ages", "arrived", "draw false", "ages"]


def test_the_opening_lane_is_drawn_while_the_30_minute_card_cannot_be_read():
    """Monday's first morning with no 30-minute card on file: the open 5-minute call still leads, drawn alone;
    once a 30-minute card has been read, paint draws both and this draws nothing."""
    js = ("var last = D.last, tape = D.tape, premarket = D.premarket || null, tapeOk = true, tickers = [], shownLeads = false, MAIN = el('div', 'main');"
          "function $(id){ return MAIN; } function clearLoading(){} function laneCard(t, ok){ return el('div', 'lane', t.row_ts); }"
          + _fn("top1") + _fn("preFolds") + _var("PRE_CHECKS") + _fn("preFold") + _fn("laneOnly") +
          "var drew = laneOnly(); console.log(JSON.stringify([drew, shownLeads, MAIN.kids.map(function(k){ return k.textContent; })]));")
    tape = {"lane": "tape", "row_ts": "2026-09-28T10:00:00-04:00", "calls": [call("10:00", "10:10", "flat", 0.4)]}
    drew = _run(js, {"now": "2026-09-28T10:05:00-04:00", "last": None, "tape": tape})
    assert drew == [True, True, ["2026-09-28T10:00:00-04:00", "30-min cardnot read yet"]]   # the mode chip heads the lane card
    # the pre-market call, handed over, folds under them
    drew = _run(js, {"now": "2026-09-28T10:05:00-04:00", "last": None, "tape": tape, "premarket": pre_card("09:28")})
    assert drew[2][-1] == "pre-market call 06:28Up 41%Checked 06:44 and 07:04"
    assert _run(js, {"now": "2026-09-28T10:05:00-04:00", "last": {"row_ts": "x"}, "tape": tape})[0] is False
    assert _run(js, {"now": "2026-09-28T10:11:00-04:00", "last": None, "tape": tape})[0] is False    # the call has closed


def test_the_blend_note_names_the_phase_by_its_hours_in_the_viewers_zone():
    """clock.py names the phase in New York words and hours; in Los Angeles "lunch" would sit beside 09:00, so
    the page keeps only the hours, redrawn in the viewer's zone."""
    js = (_how() + "console.log(JSON.stringify(D.w.map(function(w){ return howChart(D.h, {used: true, sessions: 19, phase_words: w}, D.at)"
          ".kids.slice(-1)[0].textContent; })));")
    h = {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}, "jev": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}},
         "clock": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}}}
    got = _run(js, {"h": h, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00",
                    "w": ["lunch, 12:00 to 14:00", "the afternoon, 14:00 to the close", "the opening half hour, before 10:00"]}, LA)
    assert got == [f"The call above is half of each. Last 19 days looks only at this time of day ({span})."
                   for span in ("09:00 to 11:00", "11:00 to the close", "before 07:00")]


def _how():
    return (_var("ODDS_ORDER") + _fn("oddsKeys") + _fn("top1") + _fn("oneAnswer") + _fn("phaseSpan") + _fn("howBar") + _fn("tile")
            + _fn("howChart"))


def test_the_blend_is_drawn_as_two_tiles_and_only_the_time_of_day_one_opens_the_sheet():
    """Will's layout of 09-29: the three rows (JEV, Time of day, Shown) fold into two tiles under the call, which is
    what is shown. JEV's tile says how sure it was; the last sessions' tile opens the time-of-day sheet, and only when
    the card carries its counts (the card's time_of_day), so a card without them draws a tile that is not a button."""
    js = (_how() + "var opened = []; function openTod(tod, bl, at, from){ opened.push(from.tag); }"
          "var g = howChart(D.h, {used: true, sessions: 19, phase: 'lunch', phase_words: 'lunch, 12:00 to 14:00'}, D.at, D.tod);"
          "console.log(JSON.stringify(g.kids.map(function(k){ return [k.tag, k.attrs['class'], k.textContent]; })));")
    h = {"probabilities": {"up": 0.13, "down": 0.5, "flat": 0.37}, "jev": {"probabilities": {"up": 0.05, "down": 0.85, "flat": 0.1}, "confidence": 0.78},
         "clock": {"probabilities": {"up": 0.22, "down": 0.14, "flat": 0.64}}}
    tod = {"phases": [{"phase": "lunch", "from": "12:00", "to": "14:00", "probabilities": {"up": 0.22, "down": 0.14, "flat": 0.64}, "n": 215}],
           "days": []}
    got = _run(js, {"h": h, "tod": tod, "at": "2026-09-29T13:01:00-04:00", "now": "2026-09-29T13:04:00-04:00"}, LA)
    assert got == [["div", "tile", "JEV · 78% sureDown 85%"], ["button", "tile", "Last 19 daysFlat 64%"],
                   ["div", "how-note", "The call above is half of each. Last 19 days looks only at this time of day (09:00 to 11:00)."]]
    bare = _run(js, {"h": h, "tod": None, "at": "2026-09-29T13:01:00-04:00", "now": "2026-09-29T13:04:00-04:00"}, LA)
    assert [k[0] for k in bare] == ["div", "div", "div"]
    assert "hc.appendChild(howChart(a, bl, c.row_ts, a !== h ? c.time_of_day : null));" in _fn("sumCard")


def test_the_reason_a_sum_stands_alone_gives_its_times_in_the_viewers_zone():
    """clock.py says why the time-of-day odds were left out in market words ("a 13:00 half day"); the phone redraws
    the time in the viewer's zone like every other."""
    js = (_how() + "console.log(JSON.stringify(howChart(D.h, {used: false, why: D.why}, D.at).kids.slice(-1)[0].textContent));")
    got = _run(js, {"h": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}}, "at": "2026-11-27T10:02:00-05:00",
                    "now": "2026-11-27T10:05:00-05:00", "why": "a 13:00 half day; the time-of-day odds are counted on full sessions"}, LA)
    assert got == "JEV\u2019s sum alone: a 10:00 half day; the time-of-day odds are counted on full sessions"


def test_every_question_group_has_a_heading_in_words():
    """The groups are the SPX question file's, not SNDK's viewpoints: each has a plain name, none a raw id."""
    doc = json.loads((Path(__file__).resolve().parents[3] / "skills" / "spx-jev" / "questions" / "spx_questions.json").read_text())
    shown = [g["id"] for g in doc["groups"] if g["id"] != "dark"]
    decl = "".join(re.search(r"\n  var %s = .*?;" % v, JS, re.S).group(0) for v in ("ORDER", "NAME"))
    names = _run(decl + "console.log(JSON.stringify([ORDER, NAME]));", {})
    assert names[0] == shown and set(names[1]) == set(shown) and "_" not in "".join(names[1].values())
    assert "NAME[vp] || words(vp)" in _fn("paint")


def test_the_folded_30_minute_line_counts_down_only_a_call_that_was_made():
    """The 09:32 read asks nothing, so from 09:35 to 10:02 the opening view has no 30-minute call to time."""
    js = ("var tickers = [], READ_MINUTES = [2, 32], LAST_READ_DEFAULT = '15:32';" + _fn("top1") + _fn("plusIso") + _fn("nextRead")
          + _fn("noCallWords") + _fn("foldLive") + "var f = foldLive(D.c); console.log(JSON.stringify([f.kids.map(function(k){ return k.textContent; }), tickers.length]));")
    c = {"row_ts": "2026-09-28T09:31:20-04:00", "hour": None, "marks": {"next_30": "2026-09-28T10:00:00-04:00"},
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T09:52:00-04:00", "c": c}, LA) == [
        ["30-min read 06:31", "No 30-minute call on this read, next read 07:02"], 0]


# the time-of-day sheet over 09-29's real counts (clock._blocks on the 20 sessions before it): five parts, 09-01 left out
TOD_0929 = {
    "phases": [{"phase": "opening", "from": "2026-09-29T09:30:00-04:00", "to": "2026-09-29T10:00:00-04:00", "probabilities": {"up": 0.2857, "down": 0.4155, "flat": 0.2987}, "n": 58},
               {"phase": "morning", "from": "2026-09-29T10:00:00-04:00", "to": "2026-09-29T11:00:00-04:00", "probabilities": {"up": 0.3068, "down": 0.3627, "flat": 0.3304}, "n": 112},
               {"phase": "late_morning", "from": "2026-09-29T11:00:00-04:00", "to": "2026-09-29T12:00:00-04:00", "probabilities": {"up": 0.3203, "down": 0.1855, "flat": 0.4943}, "n": 110},
               {"phase": "lunch", "from": "2026-09-29T12:00:00-04:00", "to": "2026-09-29T14:00:00-04:00", "probabilities": {"up": 0.2152, "down": 0.1434, "flat": 0.6414}, "n": 215},
               {"phase": "afternoon", "from": "2026-09-29T14:00:00-04:00", "to": "2026-09-29T16:00:00-04:00", "probabilities": {"up": 0.1739, "down": 0.1932, "flat": 0.6329}, "n": 188}],
    "days": [{"day": "2026-09-28", "counted": True, "counts": {"opening": {"up": 1, "down": 3, "flat": 0}, "morning": {"up": 1, "down": 3, "flat": 1},
                                                              "late_morning": {"up": 3, "down": 1, "flat": 2}, "lunch": {"up": 5, "down": 2, "flat": 5},
                                                              "afternoon": {"up": 0, "down": 5, "flat": 5}}},
             {"day": "2026-09-15", "counted": True, "counts": {"opening": {"up": 0, "down": 3, "flat": 0}, "morning": {"up": 1, "down": 4, "flat": 1},
                                                              "late_morning": {"up": 2, "down": 0, "flat": 3}, "lunch": {"up": 1, "down": 0, "flat": 2},
                                                              "afternoon": {"up": 1, "down": 4, "flat": 5}}},
             {"day": "2026-09-01", "counted": False, "counts": {"opening": {"up": 0, "down": 0, "flat": 0}, "morning": {"up": 1, "down": 3, "flat": 0},
                                                               "late_morning": {"up": 1, "down": 0, "flat": 1}, "lunch": {"up": 0, "down": 0, "flat": 1},
                                                               "afternoon": {"up": 3, "down": 5, "flat": 1}}}]}


def _tod_sheet(tz=LA, pick=None):
    js = ("var IDS = {csTitle: el('div'), csBody: el('div')}; function $(id){ return IDS[id]; } var opened = 0, MiraiSheet = {open: function(){ opened++; }};"
          "Node.prototype.addEventListener = function(k, f){ this.on = f; };"
          + _var("ODDS_ORDER") + _fn("oddsKeys") + _fn("top1") + _fn("oneAnswer") + _fn("howBar") + _fn("tag") + _var("MONTHS") + _fn("dayWords")
          + _fn("partSpan") + _fn("readings") + _fn("openTod")
          + "openTod(D.tod, {sessions: 19, phase: 'lunch'}, D.at, el('button'));"
          "var rows = IDS.csBody.kids[1].kids; if(D.pick != null) rows[D.pick].on();"
          "console.log(JSON.stringify({title: IDS.csTitle.textContent, opened: opened,"
          " rows: IDS.csBody.kids[1].kids.map(function(r){ return [r.attrs['class'], r.kids.map(function(k){ return k.textContent; })]; }),"
          " list: IDS.csBody.kids[2].kids.map(function(r){ return [r.attrs['class'], r.textContent, r.kids[1] && r.kids[1].style.width]; })}));")
    return _run(js, {"tod": TOD_0929, "at": "2026-09-29T13:01:00-04:00", "now": "2026-09-29T13:04:00-04:00", "pick": pick}, tz)


def test_the_time_of_day_sheet_opens_on_the_reads_own_part_in_the_viewers_hours():
    """The sheet Will sketched on 09-29: each part of the day's odds over the counted sessions, its hours in the
    viewer's zone, the read's own part chosen and said to be used in this call, then that part day by day, newest first,
    a thin day said so and a day with too few graded reads left out."""
    got = _tod_sheet()
    assert got["title"] == "Time of day · last 19 days" and got["opened"] == 1
    assert got["rows"] == [
        ["tb", ["06:30–07:00", "", "Down 42%", "58 readings"]],
        ["tb", ["07:00–08:00", "", "Down 36%", "112 readings"]],
        ["tb", ["08:00–09:00", "", "Flat 49%", "110 readings"]],
        ["tb on", ["09:00–11:00", "", "Flat 64%", "215 readings · now, used in this call"]],
        ["tb", ["11:00–13:00", "", "Flat 63%", "188 readings"]]]
    assert got["list"][0] == ["td-h", "09:00–11:00, day by day", None]
    assert got["list"][1:4] == [["td", "Sep 2812 of 12", "100%"], ["td thin", "Sep 153 of 12 · thin", "25%"], ["td out", "Sep 1left out", None]]
    assert got["list"][-1][1].startswith("Each part leans a little toward the whole day’s odds")
    # in Tokyo the same parts read in Tokyo's hours
    assert [r[1][0] for r in _tod_sheet(TOKYO)["rows"]] == ["22:30–23:00", "23:00–00:00", "00:00–01:00", "01:00–03:00", "03:00–05:00"]


def test_a_tap_on_another_part_lists_that_part_day_by_day():
    got = _tod_sheet(pick=0)
    assert [r[0] for r in got["rows"]] == ["tb on", "tb", "tb", "tb", "tb"]
    assert got["rows"][3][1][3] == "215 readings · now, used in this call"      # the read's own part is still named
    assert got["list"][0][1] == "06:30–07:00, day by day"
    # the opening's most is Sep 28's 4: Sep 15's 3 is not thin; Sep 1 is left out of every part, its own count unsaid
    assert got["list"][1:4] == [["td", "Sep 284 of 4", "100%"], ["td", "Sep 153 of 4", "75%"], ["td out", "Sep 1left out", None]]


def _price_strip(data, tz=LA):
    js = ("var IDS = {}; ['px', 'pxLive', 'pxK', 'pxV', 'pxC', 'pxS'].forEach(function(k){ IDS[k] = el('div'); IDS[k].hidden = true; });"
          "function $(id){ return IDS[id]; } var last = D.last, spot = D.spot && {price: D.spot, at: Date.parse(D.at), fresh: D.fresh};"
          + _var("SPOT_URL") + _var("SPOT_LIVE_MS") + _fn("within") + _fn("money") + _fn("todaysCard") + _fn("paintPx")
          + "paintPx(); console.log(JSON.stringify({shown: !IDS.px.hidden, live: !IDS.pxLive.hidden, k: IDS.pxK.textContent,"
          " v: IDS.pxV.textContent, c: IDS.pxC.textContent, cls: IDS.pxC.className, s: IDS.pxS.textContent}));")
    return _run(js, data, tz)


def test_the_live_price_says_its_change_since_the_last_close_only_on_todays_card():
    """Will's layout of 09-29: the SPX print on top, from the station's quote (/api/spot), its change measured from the
    card's prior_close (service.prior_close_of: the last minute bar of the session before). Live only in the session on a
    day the card is today's; a quote that failed keeps its price as of when it came; before the open the card is the day
    before's, so no change is said."""
    last = {"row_ts": "2026-09-29T13:01:56-04:00", "prior_close": {"day": "2026-09-28", "at": "2026-09-28T15:59:00-04:00", "price": 7684.5}}
    live = _price_strip({"now": "2026-09-29T13:04:00-04:00", "at": "2026-09-29T13:04:00-04:00", "spot": 7656.39, "fresh": True, "last": last})
    assert live == {"shown": True, "live": True, "k": "SPX live", "v": "7,656.39", "c": "−28.11 (−0.37%)", "cls": "px-c dn",
                    "s": "since the last close"}
    held = _price_strip({"now": "2026-09-29T13:04:30-04:00", "at": "2026-09-29T13:04:00-04:00", "spot": 7656.39, "fresh": False, "last": last})
    assert held["live"] is False and held["k"] == "SPX · 10:04" and held["c"] == "−28.11 (−0.37%)"
    # a quote that stopped coming (a phone woken after half an hour) is never drawn live, however it last came
    old = _price_strip({"now": "2026-09-29T13:34:00-04:00", "at": "2026-09-29T13:04:00-04:00", "spot": 7656.39, "fresh": True, "last": last})
    assert old["live"] is False and old["k"] == "SPX · 10:04"
    # after the close the print is the close, said so, with the day's change
    shut = _price_strip({"now": "2026-09-29T16:20:00-04:00", "at": "2026-09-29T16:20:00-04:00", "spot": 7660.0, "fresh": True, "last": last})
    assert shut["live"] is False and shut["k"] == "SPX · last close" and shut["c"] == "−24.50 (−0.32%)"
    up = _price_strip({"now": "2026-09-29T13:04:00-04:00", "at": "2026-09-29T13:04:00-04:00", "spot": 7700, "fresh": True, "last": last})
    assert up["c"] == "+15.50 (+0.20%)" and up["cls"] == "px-c up"
    before = _price_strip({"now": "2026-09-30T08:10:00-04:00", "at": "2026-09-30T08:10:00-04:00", "spot": 7690.1, "fresh": True, "last": last})
    assert before["live"] is False and before["c"] == "" and before["s"] == "" and before["v"] == "7,690.10" and before["k"] == "SPX · last close"
    assert _price_strip({"now": "2026-09-29T13:04:00-04:00", "at": "2026-09-29T13:04:00-04:00", "spot": None, "fresh": False, "last": last})["shown"] is False
    assert '<div class="px" id="px" hidden>' in SPX and ".px[hidden]{display:none}" in SPX
    assert _fn("draw").rstrip().endswith("paintPx(); }")          # a card that lands after the quote draws the strip again


def test_the_mode_and_answered_chips_live_inside_the_leading_card():
    """The mode chip heads the leading card and the answered chip closes it, each in its own box; the top keeps only
    trouble (a failed fetch, an unsent read, a stale row's note)."""
    assert "hc.appendChild(el('div', 'mode', 'normal \\u00B7 a call every 30 min'));" in _fn("sumCard")
    assert "box.appendChild(el('div', 'mode', 'opening \\u00B7 a call every 5 min'));" in _fn("laneCard")
    assert "'mode'" not in _fn("laneOnly") and "main.appendChild(el('div', 'mode'" not in _fn("paint")
    assert "if(count) hc.appendChild(count);" in _fn("sumCard") and "if(count) he.appendChild(count);" in _fn("sumCard")
    assert ".card>.mode{width:fit-content;margin-bottom:12px}" in SPX and ".card>.answered{display:table;margin-top:12px}" in SPX


def test_a_learned_mix_on_the_phone_is_named_as_such_with_the_half_and_half_kept_beneath():
    """A loop's shown swaps the learned mix into the odds it shows, keeps the exact blend beside it and marks
    shown_source; the chart must name which one is shown and still draw the other."""
    js = (_how() + "var g = howChart(D.h, {used: true, sessions: 19, phase_words: 'lunch, 12:00 to 14:00'}, D.at);"
          "console.log(JSON.stringify(g.kids.map(function(k){ return k.textContent; })));")
    odds = {"up": 0.2, "down": 0.3, "flat": 0.5}
    half = {"up": 0.3, "down": 0.1, "flat": 0.4, "unsure": 0.2}
    h = {"probabilities": odds, "jev": {"probabilities": odds}, "clock": {"probabilities": odds}, "blend50_exact": half,
         "shown_source": "pool_v1"}
    got = _run(js, {"h": h, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00"}, LA)
    assert got[:2] == ["JEVFlat 50%", "Last 19 daysFlat 50%"]
    assert got[2] == ("The call above is the learned mix. It took over from half of each once it did better over at least 20 trading days,"
                      " and it hands back if it starts doing worse; half of each reads Flat 40%. Last 19 days looks only at this time of day"
                      " (09:00 to 11:00).")
    blend = _run(js, {"h": {**h, "shown_source": "blend50_exact"}, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00"}, LA)
    assert blend == ["JEVFlat 50%", "Last 19 daysFlat 50%",
                     "The call above is half of each. Last 19 days looks only at this time of day (09:00 to 11:00)."]


def test_the_calls_chart_names_the_average_price_loops_learned_mix_and_never_the_end_price_loops(monkeypatch):
    """Will's decision of 09-29: once the average-price loop's pool is promoted it is what the call shows
    (integral_loop.shown marks hour.average with pool.SHOWN_POOL and keeps the exact blend beside it), and the
    end-price loop's promotion reaches only the end-price sums. The call's chart reads the call's own shown_source,
    so it names the learned mix for the first and never for the second."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "skills" / "spx-jev"))
    from spx_jev.pool import SHOWN_BLEND, SHOWN_POOL
    odds, half = {"up": 0.52, "flat": 0.33, "down": 0.15}, {"up": 0.45, "flat": 0.35, "down": 0.2}
    blend = {"used": True, "jev_share": 0.5, "phase_words": "lunch, 12:00 to 14:00", "sessions": 19}
    pooled = {**AVG, "pick": "up", "probabilities": odds, "blend50_exact": half, "blend": blend, "shown_source": SHOWN_POOL,
              "jev": {"probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1}, "confidence": 0.3},
              "clock": {"probabilities": {"up": 0.3, "flat": 0.4, "down": 0.3}}}
    end = {**END_30, "used": 12, "by": {"next_30": END_30}, "blend": blend, "jev": {"probabilities": END_30["probabilities"]},
           "clock": {"probabilities": END_30["probabilities"]}}
    c = {"row_ts": "2026-09-28T12:32:10-04:00", "marks": {"next_30": "2026-09-28T13:02:00-04:00"}, "calls": [],
         "hour": {**end, "shown_source": SHOWN_BLEND, "average": pooled}}
    how = next(v for k, v in _sum_card(c) if k == "tod")
    assert "The call above is the learned mix" in how and "half of each reads Up 45%" in how
    # the end-price loop promoted, the call still on its blend: the tiles say the call is half of each
    c["hour"] = {**end, "shown_source": SHOWN_POOL, "blend50_exact": half,
                 "average": {**pooled, "probabilities": half, "pick": "up", "shown_source": SHOWN_BLEND}}
    how = next(v for k, v in _sum_card(c) if k == "tod")
    assert "learned mix" not in how and "The call above is half of each." in how


# ---- before the open: the pre-market card (skills/spx-jev, spx_jev.premarket)

PRE_DAY = "2026-09-28"                                          # a Monday
CHECKPOINTS = ("02:35", "03:35", "08:05", "08:48", "09:05", "09:28")
JEV_READS = ("08:48", "09:28")


def et(hhmm, day=PRE_DAY, s="00"):
    return f"{day}T{hhmm}:{s}-04:00"


def pre_call(pick, probabilities, first=None):
    """A premarket sum as the card carries it: the primary (30 minutes on) call and both horizons' sums."""
    first = first or pick
    return {"pick": pick, "probabilities": probabilities, "primary": "open_30",
            "by": {"open_10": {"pick": first, "probabilities": {**probabilities, first: max(probabilities.values()) + 0.01}},
                   "open_30": {"pick": pick, "probabilities": probabilities}}}


CALL_0848 = pre_call("up", {"down": 0.2, "flat": 0.34, "up": 0.38, "unsure": 0.08})
CALL_0928 = pre_call("up", {"down": 0.21, "flat": 0.30, "up": 0.41, "unsure": 0.08})
LEANS = {"02:35": "up_small", "03:35": "up", "08:05": "up", "08:48": "up", "09:05": "up", "09:28": "up"}
FACTS = [{"key": "futures", "path": "overnight.es_move", "title": "S&P futures", "verdict": "Bigger night",
          "sentence": "S&P futures stand 0.62% above their 16:00 price, larger than 15 of the last 20 nights"},
         {"key": "bonds", "path": "overnight.bond_gap", "title": "Bonds", "verdict": "In line",
          "sentence": "10-year bond futures moved about as far as the night's S&P move would expect"},
         {"key": "bitcoin", "path": "overnight.btc_vs_futures", "title": "Bitcoin", "verdict": "In line",
          "sentence": "bitcoin futures moved with S&P futures overnight"}]
REPORT = {"key": "report", "path": "overnight.release_reaction", "title": "Report", "verdict": "Extended", "at": et("08:30"),
          "sentence": "since the 08:30 jobless claims, S&P futures added to the night's move and kept it"}


def pre_card(at="09:28", day=PRE_DAY, report=True, sent=None, checkpoints=CHECKPOINTS, **over):
    """The card spx_jev.premarket writes at the ``at`` checkpoint (section 2c of the work split): every time a
    full New York stamp; the newest JEV call carried by the snapshot reads after it, with its read_at. A JEV read
    that was not sent made no call."""
    done = [c for c in checkpoints if c <= at]
    calls = {c: call for c, call in (("08:48", CALL_0848), ("09:28", CALL_0928)) if not (sent is False and c == at)}
    newest = [c for c in done if c in calls]
    hour = {**calls[newest[-1]], "read_at": et(newest[-1], day, "04")} if newest else None
    is_jev = at in JEV_READS
    card = {"symbol": "SPX", "lane": "premarket", "day": day, "generated_at": et(at, day, "31"), "row_ts": et(at, day, "04"),
            "checkpoint": at, "freshness": {"age_s": 64, "stale": False}, "sent": is_jev if sent is None else sent, "model": "jev",
            "ruler": {"kind": "pre_open", "points": 52.4, "sessions": 20},
            "open": et("09:30", day), "start": et("09:34", day), "marks": [et("09:44", day), et("10:04", day)],
            "handover": et("09:35", day),
            "schedule": {"reads": [et(c, day) for c in checkpoints], "jev_reads": [et(c, day) for c in JEV_READS],
                         "close_out": et("10:06", day)},
            "hour": hour,
            "story": [{"at": et(c, day, "04"), "checkpoint": c, "lean": LEANS[c], "net_sigma": 0.4, "band": "top third",
                       "report": report and c == "08:48",
                       "call": {"pick": calls[c]["pick"], "probabilities": calls[c]["probabilities"]} if c in calls else None}
                      for c in done],
            "situation": FACTS + ([REPORT] if report and at >= "08:30" else []),
            "questions": [{"id": "overnight_arc", "ask": "How has the night's move built?", "answer": {"pick": "held"} if is_jev else None},
                          {"id": "gap_origin", "ask": "Where was the gap made?", "answer": None}],
            "labels": 14, "omitted": {}, "event": None,
            "expiries": {"today_settles_at": et("16:00", day), "next_settles_at": et("16:00", "2026-09-29"),
                         "next_monthly_settles_at": et("16:00", "2026-10-16"), "expiring_today": []},
            "calls": [], "tally": None}
    return {**card, **over}


PRE_FNS = ("fits", "top1", "oddsKeys", "oddsWidth", "oddsBar", "tag", "skipLine", "averageWords", "endPriceRow", "ageWord", "mins", "untilWords", "expiryLine",
           "preLeads", "preMissed", "preJevRead", "preJevDue", "preUnsent", "preState", "shapeWords", "sumPick", "sumSaid", "preClock",
           "preCall", "preRulerLine", "preStaleLine", "checksSvg", "chipFits", "storySide", "storyHead", "storySvg", "preFacts", "paintPre")
PRE_VARS = ("ODDS_ORDER", "ODDS_TRACK_PX", "ODDS_LETTERS", "ODDS_EM", "EXPIRY_TAGS", "PRE_LATE_MIN", "OPENS", "THEN", "CHECKS_LEAD_MIN", "CHIP_W")
# the page's #h1, #sub, #state and #main, with the classList the pre-market card toggles
PRE_DOM = """
Object.defineProperty(Node.prototype, 'classList', {get: function(){ var n = this; function has(){ return (n.attrs['class'] || '').split(' ').filter(Boolean); }
  return {remove: function(k){ n.attrs['class'] = has().filter(function(x){ return x !== k; }).join(' '); },
          toggle: function(k, on){ var h = has().filter(function(x){ return x !== k; }); if(on) h.push(k); n.attrs['class'] = h.join(' '); }}; }});
var nodes = {h1: el('span'), sub: el('div'), state: el('div'), main: el('main')};
function $(id){ return nodes[id]; }
var tickers = [], shownPre = false, shownLeads = false;
"""


def _pre(js, data, tz=LA):
    return _run(PRE_DOM + "".join(_fn(f) for f in PRE_FNS) + "".join(_var(v) for v in PRE_VARS) + js, data, tz)


def _page(card, now, tz=LA, ok=True):
    """paintPre on ``card`` at ``now``: the header line, the state chips, and each of main's parts as its class and flat text."""
    js = ("paintPre(D.card, D.ok);"
          "function flat(n){ return n._t + n.kids.map(flat).join(''); }"
          "console.log(JSON.stringify({sub: nodes.sub.textContent, state: nodes.state.kids.map(flat),"
          " main: nodes.main.kids.map(function(k){ return [k.attrs['class'] || k.tag, k]; }).map(function(x){ return [x[0], dump(x[1])]; }),"
          " tickers: tickers.length, shownPre: shownPre}));")
    return _pre(js, {"card": card, "now": now, "ok": ok}, tz)


def _card_parts(got):
    """The pre-market card's children, each as [class, flat text]."""
    card = [m[1] for m in got["main"] if "pre" in m[0].split()][0]
    return [[k["attrs"].get("class", k["tag"]), _flat_text(k)] for k in card["kids"]]


def _texts(svg, cls=None):
    return [k["text"] for k in svg["kids"] if k["tag"] == "text" and (cls is None or k["attrs"].get("class") == cls)]


def test_the_page_reads_the_premarket_card_and_it_leads_before_the_open():
    assert "var PRE_URL = '/api/raw/file?root=state&path=spx_jev/lanes/premarket/latest.json';" in JS
    assert "fetch(PRE_URL, {cache:'no-store'})" in JS and "within(PRE_HOURS)" in _fn("pollPre")
    assert "PRE_HOURS = ['02:35', '16:05']" in JS
    # after the hand-over the card is fetched until today's close-out lands, then left as it is
    assert "premarket.day === marketDay() && premarket.closed_out_at)) return;" in _fn("pollPre")
    assert "pollPre();" in _fn("poll")
    # paint hands the whole page to the pre-market card while it leads; tick and the ages follow it
    assert _fn("paint").lstrip("\n").splitlines()[2] == \
        "    if(preLeads(premarket, Date.now(), marketDay())){ paintPre(premarket, premarketOk); return; }"
    assert "preLeads(premarket, Date.now(), marketDay()) !== shownPre" in _fn("tick")
    assert "if(shownPre) return;" in _fn("ages")
    assert _fn("poll").count("preOnly() || laneOnly();") == 3
    for f in ("preState", "preClock", "preCall", "checksSvg", "storySvg", "preFacts", "paintPre"):
        assert "viewerTime(" in _fn(f), f


@pytest.mark.parametrize("card, now, tz, leads", [
    (pre_card("02:35"), et("02:40"), LA, True),                             # Sunday 23:40 in Los Angeles: Monday's night
    (pre_card("09:28"), et("09:34", s="59"), TOKYO, True),
    (pre_card("09:28"), et("09:35"), LA, False),                            # the opening lane's first read takes over
    (pre_card("09:28", day="2026-09-25"), et("09:00"), LA, False),         # Friday's card on Monday
    (pre_card("09:28", day="2026-09-25"), "2026-09-26T09:00:00-04:00", LA, False),   # and on the Saturday after
    ({**pre_card("09:28"), "lane": "tape"}, et("09:00"), LA, False),
    (None, et("09:00"), LA, False),                                          # a holiday: no card written today
])
def test_the_premarket_card_leads_only_on_its_own_day_until_the_hand_over(card, now, tz, leads):
    got = _run(_fn("preLeads") + "console.log(JSON.stringify(preLeads(D.card, Date.now(), marketDay())));",
               {"card": card, "now": now}, tz)
    assert got is leads


def test_a_call_leads_the_card_with_its_shape_its_checks_and_the_story():
    got = _page(pre_card("09:28"), et("09:28", s="20"))
    assert got["sub"] == "pre-market read 06:28, just now, open 06:30"
    assert got["state"] == ["1 of 2 answered"] and got["shownPre"] is True and got["tickers"] == 3
    assert [m[0] for m in got["main"]] == ["mode", "exp", "card dashed pre", "card fold"]
    assert _flat_text(got["main"][0][1]) == "before the open"
    parts = _card_parts(got)
    assert parts[0] == ["lab", "before the openJUST NOW"]
    assert parts[1][1] == "Opens 06:302 min to goRead 06:28 PDTChecked 06:44 and 07:04"
    assert parts[2] == ["big", "Up 41%"]
    assert parts[3] == ["shape", "Opens firm, holds: up at the first check, still up at the second"]
    assert parts[5] == ["inplay-h", "When it is checked"]
    assert parts[7] == ["tag", "Both checks start from the price at 06:34, 4 minutes after the open."]
    assert parts[8] == ["inplay-h", "Story so far · 6 reads, all up"]
    facts = [m for m in parts if m[0] == "facts"][0][1]
    assert facts.startswith("S&P futuresBigger nightS&P futures stand 0.62% above their 13:00 price")   # market words, the viewer's zone
    assert "05:30 reportExtendedSince the 05:30 jobless claims" in facts
    assert parts[-1] == ["tag", "Hands over to the 5-minute opening reads at 06:35. A forecast, graded by the bars, not a trade signal."]
    assert _flat_text(got["main"][3][1]) == "Opening readsFirst 06:35Every 5 min, 10-minute calls"


def _fold(card, now, tz=LA):
    js = (_fn("preFolds") + _var("PRE_CHECKS") + _fn("preFold") +
          "console.log(JSON.stringify({folds: preFolds(D.card, Date.now(), marketDay()),"
          " fold: preFolds(D.card, Date.now(), marketDay()) ? dump(preFold(D.card)) : null}));")
    return _run(PRE_DOM + "".join(_fn(f) for f in PRE_FNS) + "".join(_var(v) for v in PRE_VARS) + js, {"card": card, "now": now}, tz)


# the pre-market call, on the average price over the 30 minutes after the settled open (hour.average_summary)
PRE_AVG = {"pick": "down", "probabilities": {"up": 0.25, "flat": 0.3, "down": 0.45}, "primary": "open_average_30", "box": "open_30",
           "minutes": 30, "flat_points": 4.38, "edge_points": 2.59}


def on_call(label, verdict):
    """A check's average-price grade as the grader writes it for an average-price call: every check grades the call's
    own pick (grade.checked_call), so its sum is the call's."""
    return {**graded(label, verdict), "sum": "open_average_30"}


def _closed(closed=None, average=PRE_AVG, **checks):
    """The card after the close-out that left nothing to grade: the newest call carries each check graded (service.day_calls),
    and ``closed``, the grader's reason, when it can never be graded; the call is ``average``'s when it answered."""
    read = et("09:28", s="04")
    card = pre_card("09:28", closed_out_at="2026-09-28T14:06:04+00:00", graded_at="2026-09-28T14:06:04+00:00",
                    calls=[{"read": read, "mark": et("10:05"), "minutes": 30, "pick": "down", "p": 0.45, "checks": checks,
                            **({"closed": closed} if closed else {})}])
    return {**card, "hour": {**card["hour"], "average": average}} if average else card


def _partial(**checks):
    """The card after a close-out run that left a check to grade: stamped graded_at, never closed_out_at."""
    card = _closed(**checks)
    return {k: v for k, v in card.items() if k != "closed_out_at"}


@pytest.mark.parametrize("card, now, tz, want", [
    (pre_card("09:28"), et("09:50"), LA, "pre-market call 06:28Up 41%Checked 06:44 and 07:04"),
    # each check on the average price over its window, of the call's own pick, as the close-out log counts it
    (_partial(open_10={"outcome": "up", "hit": True, "pick": "up", "integral": on_call("down", "right")}), et("10:30"), LA,
     "pre-market call 06:28Down 45%06:44 was Down, right · 07:04 not graded yet"),
    # the 09:45 check is the call's, whatever the ten-minute end-price question said (unsure here)
    (_closed(open_10={"outcome": "flat", "hit": False, "pick": "unsure", "integral": on_call("up", "wrong")},
             open_30={"outcome": "down", "hit": True, "pick": "down", "integral": on_call("down", "right")}), et("10:30"), LA,
     "pre-market call 06:28Down 45%06:44 was Up, wrong · 07:04 was Down, right"),
    (_closed(open_10={"outcome": "down", "hit": True, "pick": "down", "integral": on_call("down", "right")},
             open_30={"outcome": "down_small", "hit": False, "pick": "flat", "integral": on_call("down", "right")}), et("10:30"), TOKYO,
     "pre-market call 22:28Down 45%22:44 was Down, right · 23:04 was Down, right"),
    # no average-price grade: the end price alone, said so, judged on the call's pick, not the end-price question's
    (_closed(open_10={"outcome": "down", "hit": False, "pick": "up"}, open_30={"outcome": "flat", "hit": False, "pick": "unsure"}), et("10:30"), LA,
     "pre-market call 06:28Down 45%06:44 ended Down, right · 07:04 ended Flat, wrong"),
    # a read whose average-price sum got no answer calls the end-price question's pick, and each box is checked on its own
    (_closed(average={"error": "HTTP 529", "primary": "open_average_30", "box": "open_30"},
             open_10={"outcome": "flat", "hit": False, "pick": "unsure", "integral": graded("down", "passed")},
             open_30={"outcome": "flat", "hit": False, "pick": "unsure"}), et("10:30"), LA,
     "pre-market call 06:28Up 41%06:44 was Down, passed · 07:04 ended Flat, passed"),
    # closed out with a check ungraded: it never will be, said so, with the grader's reason in the viewer's zone
    (_closed(), et("12:00"), NY, "pre-market call 09:28Down 45%09:44 not graded at the close-out · 10:04 not graded at the close-out"),
    (_closed("halted window: no settled open (the 09:34 bar) on a finished day"), et("12:00"), LA,
     "pre-market call 06:28Down 45%06:44 not graded at the close-out · 07:04 not graded at the close-out "
     "(halted window: no settled open (the 06:34 bar) on a finished day)"),
    (_closed("halted window: no bar at the mark", open_10={"outcome": "flat", "hit": True, "pick": "flat", "integral": on_call("up", "wrong")}),
     et("12:00"), LA, "pre-market call 06:28Down 45%06:44 was Up, wrong · 07:04 not graded at the close-out (halted window: no bar at the mark)"),
])
def test_after_the_hand_over_the_call_folds_to_one_line_with_each_checks_result(card, now, tz, want):
    """The call is checked at 09:44 and 10:04 and the close-out grades both at 10:06: once the opening lane takes
    over, the card is still fetched until that close-out lands, and its call folds to a line under the session's
    cards giving each check's result, in the viewer's zone, as the live card's folded line gives its call."""
    got = _fold(card, now, tz)
    assert got["folds"] is True and _flat_text(got["fold"]) == want
    assert got["fold"]["attrs"]["class"] == "card fold"


@pytest.mark.parametrize("card, now", [
    (pre_card("09:28"), et("09:34")),                                        # it still leads the page
    (pre_card("09:28", day="2026-09-25"), et("10:30")),                      # Friday's card on Monday
    (pre_card("08:05"), et("10:30")),                                        # no call made today: nothing to fold
])
def test_no_fold_while_the_card_leads_on_another_day_or_without_a_call(card, now):
    assert _fold(card, now)["folds"] is False


def test_the_fold_follows_the_session_cards_and_the_opening_lane_alone():
    assert "if(preFolds(premarket, Date.now(), marketDay())) main.appendChild(preFold(premarket));" in _fn("paint")
    assert "if(preFolds(premarket, Date.now(), marketDay())) main.appendChild(preFold(premarket));" in _fn("laneOnly")
    assert "!preFolds(premarket, now, today)) return;" in _fn("redrawPre")


def test_the_checks_drawing_names_both_sums_from_the_settled_open_in_the_viewers_zone():
    js = "console.log(JSON.stringify(dump(checksSvg(D.card, Date.parse(D.now)))));"
    svg = _pre(js, {"card": pre_card("09:28"), "now": et("09:28", s="40")}, TOKYO)
    assert _texts(svg, "t-axis") == ["open 22:30", "22:34", "22:44", "23:04"]
    assert _texts(svg, "t-now") == ["Up 42%"] and _texts(svg, "t-open") == ["Up 41%"]
    assert _texts(svg, "t-side") == ["10 min on", "30 min on"] and _texts(svg, "t-now-l") == ["now"]
    assert svg["attrs"]["aria-label"] == "Checked twice from the 22:34 price: at 22:44 and 23:04"
    assert all(0 <= float(k["attrs"].get("x", 0)) <= 300 for k in svg["kids"] if k["tag"] == "text")
    # hours before the open the dashed now line is off the drawing, and a card with no call names no bar
    early = _pre(js, {"card": pre_card("02:35"), "now": et("02:36")}, LA)
    assert _texts(early, "t-now-l") == [] and _texts(early, "t-now") == [] and _texts(early, "t-side") == ["10 min on", "30 min on"]


def test_the_now_word_stays_whole_where_the_now_line_meets_an_edge_of_the_drawing():
    """From 09:20 the checks drawing starts ten minutes before the open, so for its first minutes the dashed now line
    runs along the left edge, and its word, centred on it, was cut in half ("ow"). The line keeps its place; the word
    is kept inside the drawing."""
    js = "console.log(JSON.stringify(dump(checksSvg(D.card, Date.parse(D.now)))));"
    svg = _pre(js, {"card": pre_card("09:28"), "now": et("09:20", s="10")}, LA)
    line = [k for k in svg["kids"] if k["tag"] == "line" and k["attrs"].get("class") == "now"][0]
    word = [k for k in svg["kids"] if k["tag"] == "text" and k["attrs"].get("class") == "t-now-l"][0]
    assert float(line["attrs"]["x1"]) < 1 and float(word["attrs"]["x"]) == 12.0


def test_snapshot_reads_before_the_first_call_say_when_it_comes():
    got = _page(pre_card("03:35"), et("03:40"))
    assert got["state"] == ["snapshot, JEV at 05:48 and 06:28"]
    parts = _card_parts(got)
    assert parts[2:4] == [["big", "No call yet"], ["skip", "First call at 05:48"]]
    assert parts[-1] == ["tag", "Hands over to the 5-minute opening reads at 06:35."]
    assert "REPORT" not in _flat_text([m[1] for m in got["main"] if "pre" in m[0]][0])
    assert [p for p in parts if p[0] == "facts"][0][1].count("report") == 0          # no report row before one is out


def test_a_snapshot_after_a_call_carries_it_and_says_whose_it_is():
    parts = _card_parts(_page(pre_card("09:05"), et("09:06")))
    assert parts[2] == ["big", "Up 38%"]
    assert ["tag", "The call from the 05:48 read; the 06:05 read is a snapshot and asks nothing."] in parts


def test_an_unsent_jev_read_says_why_and_when_the_next_call_is():
    card = pre_card("08:48", sent=False, unsent_reason="not sent: no key on this machine")
    got = _page(card, et("08:50"), TOKYO)
    assert got["state"] == ["not sent: no key on this machine"]
    assert _card_parts(got)[2:4] == [["big", "No call yet"], ["skip", "Next call at 22:28"]]
    assert "Up 38%" not in json.dumps(_page(card, et("08:50"))["main"])        # no call on its chip either
    # the last JEV read unsent: the 08:48 call is still the newest, and nothing more comes before the open
    last = _page(pre_card("09:28", sent=False, unsent_reason="not sent: this run was not asked to send"), et("09:29"))
    assert last["state"] == ["not sent: this run was not asked to send"]
    assert _card_parts(last)[2] == ["big", "Up 38%"]
    assert ["tag", "The call from the 05:48 read; the 06:28 read was not sent."] in _card_parts(last)
    # sent, but its sum made no call (premarket.newest_call keeps only sums with a pick): the 08:48 call is carried
    failed = _page(pre_card("09:28", hour={**CALL_0848, "read_at": et("08:48", PRE_DAY, "04")}), et("09:29"))
    assert failed["state"] == ["1 of 2 answered"] and _card_parts(failed)[2] == ["big", "Up 38%"]
    assert ["tag", "The call from the 05:48 read; the 06:28 read made none."] in _card_parts(failed)
    # the lane put the failed sum's error on the card: the note says it, and with no earlier call so does the line under
    error = {"read_at": et("09:28", PRE_DAY, "04"), "error": "JEV timed out after 60 s"}
    errored = _page(pre_card("09:28", hour={**CALL_0848, "read_at": et("08:48", PRE_DAY, "04")}, hour_error=error), et("09:29"))
    assert ["tag", "The call from the 05:48 read; the 06:28 read made no sum: JEV timed out after 60 s"] in _card_parts(errored)
    alone = _page(pre_card("08:48", hour=None, hour_error={**error, "read_at": et("08:48", PRE_DAY, "04")}), et("08:49"))
    assert _card_parts(alone)[2:5] == [["big", "No call yet"], ["skip", "The 05:48 read made no sum: JEV timed out after 60 s"],
                                       ["skip", "Next call at 06:28"]]
    assert _card_parts(_page(pre_card("09:28", sent=False, hour=None), et("09:29")))[3] == ["skip", "No call before the open"]
    # a JEV read with nothing to ask names its checkpoint on the market clock; the chip gives it in the viewer's zone
    empty = pre_card("08:48", sent=False, unsent_reason="nothing to ask at the 08:48 ET read")
    assert _page(empty, et("08:50"), TOKYO)["state"] == ["not sent: nothing to ask at the 21:48 read"]
    assert _page(empty, et("08:50"), LA)["state"] == ["not sent: nothing to ask at the 05:48 read"]


def test_a_card_left_behind_by_a_sleeping_mac_says_which_read_is_missing():
    got = _page(pre_card("03:35"), et("08:20"))
    assert got["sub"] == "pre-market read 00:35, 4 hr 45 min ago, open 06:30"
    assert got["state"] == ["snapshot, JEV at 05:48 and 06:28", "stale: the 05:05 read has not landed"]
    assert _card_parts(got)[0] == ["lab", "before the open4 HR 45 MIN AGO"]
    age = [m[1] for m in got["main"] if "pre" in m[0]][0]["kids"][0]["kids"][1]
    assert age["attrs"]["class"] == "r old"
    # a checkpoint only minutes late is not yet missing
    assert _page(pre_card("03:35"), et("08:10"))["state"] == ["snapshot, JEV at 05:48 and 06:28"]
    # stale futures are no chip: their age at the read is said under the call (test_a_read_on_stale_futures_says_so_under_the_call)
    stale = pre_card("08:05", freshness={"age_s": 1380, "stale": True, "note": "/ES's newest bar is 23 minutes old at the read"})
    assert _page(stale, et("08:06"))["state"] == ["snapshot, JEV at 05:48 and 06:28"]
    assert _page(pre_card("09:05"), et("09:06"), ok=False)["state"][0] == "fetch failed, showing the last card"


def test_a_missed_last_jev_read_is_said_before_the_hand_over():
    """The lane reads nothing 5 minutes or more after a checkpoint, so by 09:34 the 09:28 call is not coming: the
    card says so before the 09:35 hand-over, and no longer names that read as one to come."""
    lane = (Path(__file__).resolve().parents[3] / "skills" / "spx-jev" / "spx_jev" / "premarket.py").read_text()
    late = re.search(r"\nLATE_FIRE_MIN = (\d+) ", lane)
    assert late and _var("PRE_LATE_MIN").strip().startswith(f"var PRE_LATE_MIN = {int(late.group(1)) + 1};")
    assert _page(pre_card("09:05"), et("09:20"))["state"] == ["snapshot, JEV at 06:28"]
    assert _page(pre_card("09:05"), et("09:33", s="59"))["state"] == ["snapshot, JEV at 06:28"]
    got = _page(pre_card("09:05"), et("09:34"))
    assert got["state"] == ["snapshot", "stale: the 06:28 read has not landed"]
    age = [m[1] for m in got["main"] if "pre" in m[0]][0]["kids"][0]["kids"][1]
    assert age["attrs"]["class"] == "r old"
    # a passed JEV read drops from the chip once it is missing, the one still to come stays
    assert _page(pre_card("08:05"), et("08:54"))["state"] == ["snapshot, JEV at 06:28", "stale: the 05:48 read has not landed"]


NO_RULER = "no pre-open ruler: fewer than 10 of the last sessions have a morning anchor on file"


def unruled_card(at, **over):
    """The card a read writes when it cannot stand (premarket.NoPreOpenRead): the ruler omitted with why, no labels,
    no facts, its own chip in the story not measured."""
    card = pre_card(at, ruler={"omitted": NO_RULER}, situation=[], labels=0, **over)
    card["story"][-1]["lean"] = None
    return card


def test_a_read_without_a_pre_open_ruler_says_why_it_measured_nothing():
    got = _page(unruled_card("03:35"), et("03:40"), TOKYO)
    parts = _card_parts(got)
    assert parts[2:5] == [["big", "No call yet"], ["skip", "First call at 21:48"],
                          ["skip", "Nothing measured at the 16:35 read: " + NO_RULER]]
    assert got["state"] == ["snapshot, JEV at 21:48 and 22:28"]
    assert _card_parts(_page(pre_card("03:35"), et("03:40")))[4] == ["inplay-h", "When it is checked"]   # a read that measured says nothing
    # a JEV read without one: the chip says it was not sent and the card, once, why
    jev = unruled_card("08:48", sent=False, unsent_reason="nothing to ask at the 08:48 ET read: " + NO_RULER)
    got = _page(jev, et("08:50"))
    assert got["state"] == ["not sent: nothing to ask at the 05:48 read"]
    assert ["skip", "Nothing measured at the 05:48 read: " + NO_RULER] in _card_parts(got)


def test_a_day_without_a_report_has_no_report_row_or_chip():
    got = _page(pre_card("09:28", report=False), et("09:29"))
    facts = [p for p in _card_parts(got) if p[0] == "facts"][0][1]
    assert facts.count("report") == 0 and facts.count("BitcoinIn line") == 1
    svg = _pre("console.log(JSON.stringify(dump(storySvg(D.card.story, 6))));", {"card": pre_card("09:28", report=False), "now": et("09:29")})
    assert _texts(svg, "t-rep") == []


def test_the_story_chips_fit_six_reads_and_ring_the_newest():
    svg = _pre("console.log(JSON.stringify(dump(storySvg(D.card.story, 6))));", {"card": pre_card("09:28"), "now": et("09:29")}, LA)
    chips = [k for k in svg["kids"] if k["tag"] == "rect" and k["attrs"]["class"].startswith("c-")]
    assert [c["attrs"]["class"] for c in chips] == ["c-up_small", "c-up", "c-up", "c-up", "c-up", "c-up"]
    edges = [(float(c["attrs"]["x"]), float(c["attrs"]["x"]) + float(c["attrs"]["width"])) for c in chips]
    assert edges[0][0] >= 0 and edges[-1][1] <= 300 and all(a[1] < b[0] for a, b in zip(edges, edges[1:]))
    # "Up small" is too wide for a chip of six, so it takes two lines and every chip the taller height
    assert _texts(svg, "w-dark") == ["Up", "small"] and {c["attrs"]["height"] for c in chips} == {"30"}
    rings = [k for k in svg["kids"] if k["attrs"].get("class") == "ring"]
    assert len(rings) == 1 and float(rings[0]["attrs"]["x"]) > edges[-2][1]
    assert _texts(svg, "t-rep") == ["REPORT"] and _texts(svg, "t-now-l") == ["06:28"]
    assert _texts(svg, "t-p") == ["Up 38%"] and _texts(svg, "t-p last") == ["Up 41%"]
    assert svg["attrs"]["aria-label"].startswith("23:35 Up small; 00:35 Up; 05:05 Up; 05:48 Up, after the report, call Up 38%")
    # a day of five places keeps one line, as the mockup draws it
    five = _pre("console.log(JSON.stringify(dump(storySvg(D.card.story.slice(1), 5))));", {"card": pre_card("09:28"), "now": et("09:29")})
    assert {k["attrs"]["height"] for k in five["kids"] if k["tag"] == "rect" and k["attrs"]["class"].startswith("c-")} == {"21"}


def test_early_in_the_night_the_chips_keep_their_place_and_size():
    """Two reads of six sit in the first two of the day's six places, the size they will have at 09:28, and with no
    call yet the row leaves no room for one."""
    js = "console.log(JSON.stringify(dump(storySvg(D.card.story, D.card.schedule.reads.length))));"
    svg = _pre(js, {"card": pre_card("03:35"), "now": et("03:40")})
    chips = [k["attrs"] for k in svg["kids"] if k["tag"] == "rect" and k["attrs"]["class"].startswith("c-")]
    assert [(float(c["x"]), c["width"], c["height"]) for c in chips] == [(2.0, "46", "30"), (52.0, "46", "30")]
    link = [k["attrs"] for k in svg["kids"] if k["attrs"].get("class") == "link"][0]
    assert (float(link["x1"]), float(link["x2"])) == (25.0, 75.0)
    assert svg["attrs"]["viewBox"] == "0 0 300 66" and _texts(svg, "t-p") == []


@pytest.mark.parametrize("first, second, title", [
    ("up", "up", "Opens firm, holds"), ("down", "down", "Opens weak, holds"), ("flat", "flat", "Opens quiet, holds"),
    ("up", "flat", "Opens firm, then settles"), ("down", "up", "Opens weak, then rises"), ("flat", "down", "Opens quiet, then falls"),
    ("unsure", "up", "Unclear at first, then rises"), ("unsure", "unsure", "Unclear at both checks"), ("up", None, None),
])
def test_the_calls_shape_is_worded_from_both_sums_picks(first, second, title):
    got = _pre("console.log(JSON.stringify(shapeWords(D.a, D.b)));", {"a": first, "b": second})
    assert (got and got["title"]) == title
    if second == "up" and first == "down":
        assert got["words"] == "down at the first check, up at the second"
    if first == second == "flat":
        assert got["words"] == "flat at the first check, still flat at the second"


def test_the_story_heading_says_all_one_way_only_when_it_is():
    js = "console.log(JSON.stringify(D.s.map(storyHead)));"
    got = _pre(js, {"s": [[{"lean": "up"}], [{"lean": "up_small"}, {"lean": "up"}], [{"lean": "down_small"}, {"lean": "down"}],
                          [{"lean": "up"}, {"lean": "flat"}], [{"lean": None}, {"lean": "up"}], [{"lean": None}, {"lean": None}]]})
    assert got == ["Story so far · 1 read", "Story so far · 2 reads, all up", "Story so far · 2 reads, all down", "Story so far · 2 reads",
                   "Story so far · 2 reads, all up", "Story so far · 2 reads"]


def test_a_read_whose_lean_was_not_measured_is_an_empty_dashed_chip():
    """premarket.story_so_far writes lean null for a read without premarket.where_now (no pre-open ruler, say)."""
    card = pre_card("03:35")
    card["story"][0]["lean"] = None
    svg = _pre("console.log(JSON.stringify(dump(storySvg(D.card.story, 6))));", {"card": card, "now": et("03:40")})
    assert [k["attrs"]["class"] for k in svg["kids"] if k["tag"] == "rect" and k["attrs"]["class"].startswith("c-")] == ["c-none", "c-up"]
    assert _texts(svg, "w-light") == ["Up"] and _texts(svg, "w-dark") == [] and "Null" not in json.dumps(svg)
    assert svg["attrs"]["aria-label"] == "23:35 not measured; 00:35 Up"
    assert "stroke-dasharray:33" in _rule(".story .c-none")


def test_the_week_frankfurt_is_on_winter_time_moves_a_checkpoint_and_the_card_follows():
    """In the week of 10-26 the 03:35 checkpoint is 04:35 (premarket.checkpoints); the page reads the card's own
    schedule, so the missing read it names is the one that week has."""
    week = ("02:35", "04:35", "08:05", "08:48", "09:05", "09:28")
    card = pre_card("02:35", day="2026-10-27", checkpoints=week)
    got = _page(card, "2026-10-27T05:00:00-04:00")
    assert got["state"][-1] == "stale: the 01:35 read has not landed"


def test_a_read_on_stale_futures_says_so_under_the_call():
    stale = {"age_s": 1380, "stale": True, "spot_from": "futures", "note": "/ES's newest bar is 23 minutes old at the read"}
    parts = _card_parts(_page(pre_card("03:35", freshness=stale), et("03:40"), TOKYO))
    assert parts[2:5] == [["big", "No call yet"], ["skip", "First call at 21:48"],
                          ["skip", "Futures stale at the 16:35 read: /ES's newest bar is 23 minutes old at the read"]]
    assert "Futures stale" not in json.dumps(_card_parts(_page(pre_card("03:35"), et("03:40"))))     # fresh futures say nothing


# ---- the pre-market card on the owner's 360px phone

# Widths measured in Chrome at 360 with the shipped face (Plus Jakarta Sans), in Los Angeles, Tokyo, New York and
# Kolkata (a zone name is widest as an offset), each line at the widest form it takes before the hand-over
_PRE_W = {"pre-market read 00:35, 4 hr 45 min ago, open 06:30": 319.23, "BEFORE THE OPEN": 113.08,
          "SNAPSHOT, JEV AT 05:48 AND 06:28": 222.83, "STALE: THE 05:05 READ HAS NOT LANDED": 255.12,
          "NOT SENT: NO KEY ON THIS MACHINE": 227.98,
          "NOT SENT: THIS RUN WAS NOT ASKED TO SEND": 284.97, "NOT SENT: NOTHING TO ASK AT THE 05:48 READ": 293.56,
          "OPENS 06:30": 85.72, "5 HR 50 MIN TO GO": 120.45, "Read 13:05 GMT+5:30": 119.91, "Checked 19:14 and 19:34": 143.86}


def test_the_premarket_card_fits_the_owners_360px_phone():
    """Checked in Chrome at 360 in each state (a call, snapshots, unsent, stale, carried, no 30-minute card) in Los
    Angeles, Tokyo, New York and Kolkata: nothing past the page and nothing clipped in the card's own lines. The
    header line is one line cut with an ellipsis, so its widest form must fit whole; a chip wraps, but each is one
    line at 360; the drawings are 300 wide in their own units and scale to the card."""
    column = 360 - 2 * 16
    card = column - 2 * _px(".card", "padding")
    chip = 2 * 9 + 2                                            # .state's side padding and border
    assert _PRE_W["pre-market read 00:35, 4 hr 45 min ago, open 06:30"] <= column
    for w in ("BEFORE THE OPEN", "SNAPSHOT, JEV AT 05:48 AND 06:28", "STALE: THE 05:05 READ HAS NOT LANDED",
              "NOT SENT: NO KEY ON THIS MACHINE", "NOT SENT: THIS RUN WAS NOT ASKED TO SEND",
              "NOT SENT: NOTHING TO ASK AT THE 05:48 READ"):
        assert _PRE_W[w] + chip <= column, w
    assert _PRE_W["OPENS 06:30"] + _px(".clock-top", "gap") + _PRE_W["5 HR 50 MIN TO GO"] <= card
    assert _PRE_W["Read 13:05 GMT+5:30"] + _PRE_W["Checked 19:14 and 19:34"] <= card
    assert "viewBox: '0 0 300 '" in _fn("checksSvg") and "viewBox: '0 0 300 '" in _fn("storySvg")
    assert "'class': 'inplay'" in _fn("checksSvg") and "'class': 'inplay story'" in _fn("storySvg")


# Chrome at 360 with the shipped face: the folded call's parts at their widest (the viewer's zone changes no width,
# a time having no zone name here); .fold wraps between parts, so each must fit the fold alone
_FOLD_W = {"PRE-MARKET CALL 09:28": 159.81, "Unsure 100%": 96.67,
           "09:44 was Down, wrong · 10:04 was Down, wrong": 288.66, "09:44 was Flat, wrong · 10:04 not graded yet": 260.52}


def test_the_folded_premarket_call_fits_the_owners_360px_phone():
    """Checked in Chrome at 360 after the close-out in Los Angeles, Tokyo, New York and Kolkata: nothing past the
    page and nothing clipped; the label and the pick share one row, the results take the next."""
    column = 360 - 2 * 16
    inner = column - 2 * float(re.search(r"padding:12px([\d.]+)px", _rule(".fold")).group(1))
    assert "flex-wrap:wrap" in _rule(".fold")
    assert _FOLD_W["PRE-MARKET CALL 09:28"] + 12 + _FOLD_W["Unsure 100%"] <= inner
    assert all(w <= inner for k, w in _FOLD_W.items() if " · " in k)


# ---- the call on the average price (skills/spx-jev hour.average_summary): up, flat or down over the window, no unsure;
# the end-price sum kept beside it

AVG = {"pick": "up", "probabilities": {"up": 0.55, "flat": 0.3, "down": 0.15}, "primary": "average_30", "box": "next_30",
       "minutes": 30, "flat_points": 5.25, "edge_points": 3.11}
END_30 = {"pick": "flat", "probabilities": {"up": 0.1, "flat": 0.8, "down": 0.05, "unsure": 0.05}}
CARD_STUBS = "var tickers = []; function clockBlock(){ return el('div', 'clock'); } function inPlay(){ return el('div', 'inplay'); }"


def _parts(node):
    return [[k["attrs"].get("class", k["tag"]), _flat_text(k)] for k in node["kids"]]


def _sum_card(c, tz=LA):
    js = (CARD_STUBS + _odds() + "".join(_fn(f) for f in ("top1", "oneAnswer", "phaseSpan", "howBar", "tile", "howChart", "tag", "skipLine", "plusIso",
                                                            "averageWords", "endPriceRow", "sumCard"))
          + "var main = el('main'); sumCard(D.c, main); console.log(JSON.stringify(dump(main.kids[0])));")
    return _parts(_run(js, {"c": c, "now": "2026-09-28T11:10:00-04:00"}, tz))


def test_the_30_minute_card_leads_with_the_average_price_call_and_keeps_the_end_price_beside_it():
    """The big number, the odds and how the call was made are the average-price sum's, with what it is measured on and
    its edge in points; the end-price sum rides beside it as one muted line, and the blend note is the average's own."""
    why = "only 4 prior sessions with enough reads graded on the average price; its time-of-day odds need 10"
    hour = {**END_30, "used": 12, "left_out": 0, "missing": 0, "blend": {"used": False, "why": "the end-price clock's reason"},
            "by": {"next_30": END_30, "next_60": {"pick": "up", "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.1, "unsure": 0.1}}},
            "average": {**AVG, "blend": {"used": False, "why": why}}}
    c = {"row_ts": "2026-09-28T11:02:10-04:00", "hour": hour, "marks": {"next_30": "2026-09-28T11:32:00-04:00", "next_60": "2026-09-28T12:02:00-04:00"},
         "calls": [call("11:02", "11:32", "up", 0.55)]}
    parts = _sum_card(c)
    assert parts[0] == ["mode", "normal · a call every 30 min"]   # the mode chip heads the card
    assert parts[2] == ["big", "Up 55%"]
    said = dict((k, v) for k, v in parts if k in ("tag", "row60") and v.startswith(("On the", "End-price")))
    assert said == {"tag": "On the average price over the next 30 minutes, flat within \u00B13.11 points", "row60": "End-price questionFlat 80%"}
    how = next(v for k, v in parts if k == "how")
    assert how.startswith("JEVUp 55%") and how.endswith("JEV\u2019s sum alone: " + why) and "end-price clock" not in how
    assert any(k == "row60" and v.startswith("Next 60 min, end priceUp 50%") for k, v in parts)   # an end-price sum, said so
    # a card from before the average-price sum leads with the end-price sum and has no end-price line beside it
    old = _sum_card({**c, "hour": {k: v for k, v in hour.items() if k != "average"}})
    assert old[2] == ["big", "Flat 80%"] and not any(v.startswith(("End-price", "On the")) for _, v in old)


def test_the_last_reads_clock_looks_as_far_ahead_as_its_average_runs():
    """The 15:32 read's average runs 28 minutes, to the close, and the card said "Looks 30 min ahead" above "over the next
    28 minutes": the clock takes the call's own minutes (hour.average.minutes); a card with no average-price call keeps
    its calls' horizon."""
    js = (CARD_STUBS + "function clockBlock(read, minutes, mark){ return el('div', 'clock', 'Looks ' + minutes + ' min ahead, graded ' + mark); }"
          + _odds() + "".join(_fn(f) for f in ("top1", "oneAnswer", "phaseSpan", "howBar", "tile", "howChart", "tag", "skipLine", "plusIso", "averageWords",
                                                "endPriceRow", "sumCard"))
          + "console.log(JSON.stringify(D.c.map(function(c){ var main = el('main'); sumCard(c, main); return dump(main.kids[0]); })));")
    hour = {**END_30, "used": 12, "by": {"next_30": END_30}, "average": {**AVG, "minutes": 28}}
    c = {"row_ts": "2026-09-28T15:32:10-04:00", "hour": hour, "marks": {"next_30": "2026-09-28T16:00:00-04:00"},
         "calls": [call("15:32", "16:00", "up", 0.55) | {"minutes": 30}]}
    old = {**c, "hour": {k: v for k, v in hour.items() if k != "average"}}
    now, before = [_parts(d) for d in _run(js, {"c": [c, old], "now": "2026-09-28T15:40:00-04:00"})]
    assert now[1] == ["clock", "Looks 28 min ahead, graded 2026-09-28T16:00:00-04:00"]
    assert ["tag", "On the average price over the next 28 minutes, flat within \u00B13.11 points"] in now
    assert before[1] == ["clock", "Looks 30 min ahead, graded 2026-09-28T16:00:00-04:00"]


def test_the_opening_card_leads_with_the_average_call_and_keeps_the_end_prices_size_as_its_second_line():
    five = {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.4, "up_big": 0.1, "unsure": 0.05}
    hour = {"pick": "up_small", "probabilities": five, "views": {"direction": {"probabilities": {"up": 0.5, "flat": 0.3, "down": 0.15}},
                                                                 "size": {"probabilities": {"big": 0.15, "small": 0.8}}},
            "average": {**AVG, "primary": "average_10", "box": "next_10", "minutes": 10, "edge_points": 1.66}}
    t = {"lane": "tape", "row_ts": "2026-09-28T10:40:00-04:00", "hour": hour, "calls": [call("10:40", "10:50", "up", 0.55)], "stretch": {}}
    js = (CARD_STUBS + "Object.defineProperty(Node.prototype, 'childNodes', {get: function(){ return this.kids; }});" + _odds() + _var("RULER_HELD_UNTIL") + "".join(_fn(f) for f in ("top1", "tag", "skipLine", "averageWords", "endPriceRow", "laneCard"))
          + "console.log(JSON.stringify(dump(laneCard(D.t, true))));")
    parts = _parts(_run(js, {"t": t, "now": "2026-09-28T10:42:00-04:00"}))
    assert parts[0] == ["mode", "opening · a call every 5 min"]   # the mode chip heads the lane card
    assert parts[2] == ["big", "Up 55%"] and ["row60", "End-price questionUp small 40%"] in parts
    assert ["tag", "On the average price over the next 10 minutes, flat within \u00B11.66 points"] in parts
    assert ["odds", "End-price question: a big move 15%"] in parts   # the size, from the end-price sum and said so; its direction is the call's


def test_the_opening_card_says_its_ruler_row_is_the_end_price_questions_and_never_sizes_the_call():
    """The opening call is the average over the next 10 minutes, flat within its own edge and with no size: the tape unit
    row's second flat figure is the end-price question's and says so, and neither the card nor the footer says the call
    is sized in tape units. The footer describes the pre-market call as the 30-minute average it is."""
    hour = {"pick": "up_small", "probabilities": {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.4, "up_big": 0.1, "unsure": 0.05},
            "average": {**AVG, "primary": "average_10", "box": "next_10", "minutes": 10, "edge_points": 2.07}}
    t = {"lane": "tape", "row_ts": "2026-09-28T10:40:00-04:00", "hour": hour, "calls": [call("10:40", "10:50", "up", 0.55)], "stretch": {},
         "ruler": {"unit_points": 4.4, "source": "live"}, "band": {"flat_points": 3.3, "big_points": 8.8}}
    js = (CARD_STUBS + "Object.defineProperty(Node.prototype, 'childNodes', {get: function(){ return this.kids; }});" + _odds() + _var("RULER_HELD_UNTIL")
          + "".join(_fn(f) for f in ("top1", "tag", "skipLine", "averageWords", "endPriceRow", "laneCard"))
          + "console.log(JSON.stringify(D.t.map(function(t){ return dump(laneCard(t, true)); })));")
    old = {**t, "hour": {k: v for k, v in hour.items() if k != "average"}}
    now, before = [_parts(d) for d in _run(js, {"t": [t, old], "now": "2026-09-28T10:42:00-04:00"})]
    assert ["tag", "On the average price over the next 10 minutes, flat within \u00B12.07 points"] in now
    assert ["odds", "End-price question:One tape unit 4.4 pointsFlat within 3.3 pointsBig beyond 8.8 points"] in now
    assert ["odds", "One tape unit 4.4 pointsFlat within 3.3 pointsBig beyond 8.8 points"] in before   # a card with no average call
    assert now[-1] == ["tag", "The 5-minute reads\u2019 call for the next 10 minutes: a forecast, graded by the bars, not a trade signal"]
    foot = re.search(r'(?s)<p class="foot">(.*?)</p>', SPX).group(1)
    assert "sized in tape units" not in foot and "in tape units" not in _fn("laneCard")
    assert "the average price over the 30 minutes after the first settled price" in foot and "where price stands 10 and 30" not in foot


def test_the_pre_market_call_and_its_fold_are_the_average_price_sums():
    avg = {**AVG, "pick": "down", "probabilities": {"up": 0.25, "flat": 0.3, "down": 0.45}, "primary": "open_average_30", "box": "open_30",
           "flat_points": 4.38, "edge_points": 2.59}
    card = pre_card("09:28")
    card = {**card, "hour": {**card["hour"], "average": avg}}
    got = _fold(card, et("09:50"))
    assert _flat_text(got["fold"]) == "pre-market call 06:28Down 45%Checked 06:44 and 07:04"
    box = _parts(_pre("var box = el('div'); preCall(D.card, box); console.log(JSON.stringify(dump(box)));", {"card": card, "now": et("09:30")}))
    assert box[0] == ["big", "Down 45%"] and ["row60", "End-price questionUp 41%"] in box
    assert ["tag", "On the average price over the 30 minutes after the settled open, flat within \u00B12.59 points"] in box


def test_the_sheets_end_price_line_judges_the_call_the_owner_sees():
    """The call is the average-price sum's, so the end price's verdict is about that call: down, and the end price ended
    down big, is right however the end-price question itself (unsure) fared; its own pick is named beside it. A call
    standing on its end price alone is judged the same way: down, where the end price ended up, is wrong."""
    c = {**S9, "pick": "down", "p": 0.6, "sum": "average_10", "odds": {"up": 0.15, "flat": 0.25, "down": 0.6},
         "end_price": {**S9["end_price"], "pick": "unsure", "p": 0.4}}
    got = _result(c, "2026-09-28T10:45:00-04:00")
    assert got[1] == "The average price over the window was Down. The call said Down 60%."
    assert got[-1].startswith("At the end price: Right, it ended Down big (the end-price question said Unsure 40%).")
    alone = {**call("10:32", "11:02", "down", 0.6), "sum": "average_30", "odds": {"up": 0.1, "flat": 0.3, "down": 0.6}, "end_price_only": True,
             "end_price": {"outcome": "up", "hit": True, "pick": "up", "p": 0.5, "moved": {"realized_sigma": 0.12}}}
    got = _result(alone, "2026-09-28T11:04:00-04:00")
    assert got[0] == "WrongEnd price only" and got[1].endswith("It ended Up. The call said Down 60%. Its end-price question said Up 50%.")
    words = "console.log(JSON.stringify(callWords(D.c, Date.parse(D.now))));"
    assert _run(words, {"c": alone, "now": "2026-09-28T11:04:00-04:00"}) == {"text": "Ended Up · ", "strong": "Wrong", "short": "Up · "}


def test_the_size_line_names_the_size_the_end_price_question_called():
    """The five-way end-price question named the size; an average-price call names none, so the line quotes the
    end-price question's own pick, or the size alone when the card does not name it."""
    c = {**S9, "pick": "down", "p": 0.6, "end_price": {**S9["end_price"], "pick": "down_small", "p": 0.38}}
    assert "Size at the end price: called Down small, ended Down big." in _result(c, "2026-09-28T10:45:00-04:00")
    bare = {**S9, "pick": "down", "p": 0.6, "end_price": {k: v for k, v in S9["end_price"].items() if k != "pick"}}
    assert "Size at the end price: called Small, ended Down big." in _result(bare, "2026-09-28T10:45:00-04:00")


def test_a_read_whose_average_question_went_unanswered_says_so_and_stands_on_its_end_price():
    """The service falls back on the end-price question's call (service.day_calls ``average_missing``): the card says
    so under the big number and the sheet says the same, in one sentence with the service's reason, graded on its end
    price, open or closed; the verdict's tag is the fallback grade's, "End price only"."""
    hour = {**END_30, "used": 12, "by": {"next_30": END_30}, "average": {"error": "HTTP 529", "primary": "average_30", "box": "next_30"}}
    c = {"row_ts": "2026-09-28T11:02:10-04:00", "hour": hour, "marks": {"next_30": "2026-09-28T11:32:00-04:00"}, "calls": []}
    parts = _sum_card(c)
    fallback = "No average-price answer (HTTP 529), so the call is the end-price question\u2019s"
    assert parts[2] == ["big", "Flat 80%"] and ["skip", fallback] in parts
    assert not any(v.startswith("End-price question") for _, v in parts)
    fell = {**call("11:02", "11:32", "flat", 0.8), "sum": "next_30", "average_missing": "HTTP 529", "odds": END_30["probabilities"],
            "end_price": {"outcome": "flat", "hit": True, "pick": "flat", "p": 0.8}, "end_price_only": True}
    got = _result(fell, "2026-09-28T11:34:00-04:00")
    assert got[0] == "RightEnd price only" and got[1] == fallback + ", graded on its end price. It ended Flat. The call said Flat 80%."
    opened = {k: v for k, v in fell.items() if k not in ("end_price", "end_price_only")}
    got = _result(opened, "2026-09-28T11:10:00-04:00")
    assert got[1] == fallback + ", graded at 08:32 on its end price." and len(got) == 2


def test_an_average_question_that_was_not_asked_is_never_said_to_be_unanswered():
    """The service's reason is shown as it wrote it (service.with_average): a read whose average-price question was not
    asked says so, its market clock in the viewer's zone, where the page used to say "unanswered" whatever happened."""
    why = "not asked: the window runs past the 16:00 close"
    hour = {**END_30, "used": 12, "by": {"next_30": END_30}, "average": {"error": why, "primary": "average_30", "box": "next_30"}}
    parts = _sum_card({"row_ts": "2026-09-28T15:32:10-04:00", "hour": hour, "marks": {"next_30": None}, "calls": []})
    assert ["skip", "No average-price answer (not asked: the window runs past the 13:00 close), so the call is the end-price "
                    "question\u2019s"] in parts
    assert not any("unanswered" in v for _, v in parts)
    fell = {**call("15:32", "16:00", "flat", 0.8), "sum": "next_30", "average_missing": why, "odds": END_30["probabilities"],
            "end_price": {"outcome": "flat", "hit": True, "pick": "flat", "p": 0.8}, "end_price_only": True}
    assert _result(fell, "2026-09-28T16:05:00-04:00", TOKYO)[1].startswith("No average-price answer (not asked: the window runs past the 05:00 close)")


def test_the_pre_market_card_says_the_shape_and_its_checks_are_the_end_price_questions():
    avg = {**AVG, "pick": "down", "probabilities": {"up": 0.25, "flat": 0.3, "down": 0.45}, "primary": "open_average_30", "box": "open_30",
           "flat_points": 4.38, "edge_points": 2.59}
    card = pre_card("09:28")
    card = {**card, "hour": {**card["hour"], "average": avg}}
    parts = _card_parts(_page(card, et("09:30")))
    assert not any(k == "shape" for k, _ in parts)
    assert ["tag", "End-price question: opens firm, holds: up at the first check, still up at the second"] in parts
    old = _card_parts(_page(pre_card("09:28"), et("09:30")))
    assert any(k == "shape" for k, _ in old) and ["inplay-h", "When it is checked"] in old


def test_the_pre_market_checks_drawing_shows_the_call_it_grades_at_both_marks():
    """Both checks grade the average-price call's own pick (grade.checked_call), so the card heads them as its own, not
    the end-price question's, and draws that call on both bars; a card without one draws each end-price sum's."""
    card = pre_card("09:28")
    card = {**card, "hour": {**card["hour"], "average": PRE_AVG}}
    assert ["inplay-h", "When it is checked"] in _card_parts(_page(card, et("09:30")))
    svg = _pre("console.log(JSON.stringify(dump(checksSvg(D.card, Date.parse(D.now)))));", {"card": card, "now": et("09:28", s="40")})
    assert _texts(svg, "t-now") == ["Down"] and _texts(svg, "t-open") == ["Down 45%"]   # the ten-minute bar has room for the pick alone


def test_the_story_calls_end_inside_the_drawing_on_the_owners_phone():
    """The newest chip's call sits under the drawing's right edge, and the night's first under its left; bold 10px runs
    up to 6.7px a letter, so a call as wide as "Down 45%" is moved in until it sits between 0 and 300 (it ran 1.3px past
    the right in Chrome at 360, and a night of one read put it past the left)."""
    card = pre_card("09:28")
    down = {"pick": "down", "probabilities": {"up": 0.25, "flat": 0.3, "down": 0.45}}
    card["story"][-1]["call"] = down
    js = "console.log(JSON.stringify([dump(storySvg(D.card.story, 6)), dump(storySvg(D.card.story.slice(-1), 6))]));"
    for svg in _pre(js, {"card": card, "now": et("09:29")}, LA):
        (last,) = [k for k in svg["kids"] if k["tag"] == "text" and k["attrs"].get("class") == "t-p last"]
        half = len(last["text"]) * 6.7 / 2
        assert last["text"] == "Down 45%" and half <= float(last["attrs"]["x"]) <= 300 - half


# ---- Monday's real cards, through the whole page

# The three cards the station held after Monday 2026-09-28's close (spx_jev/latest.json and the tape and
# pre-market lanes' latest.json), and the situation rebuilt read-only from the 2026-09-25 11:33 diary row,
# which named no wall (spx_jev.service.situation_rows over the row's labels and figures).
MONDAY = json.loads((Path(__file__).parent / "spx_cards_2026-09-28.json").read_text())
MAIN_JS = re.search(r"(?s)<script>\n(\(function\(\)\{\n  'use strict';.*?)</script>", SPX).group(1)
KOLKATA = "Asia/Kolkata"
# the page's whole script run as the phone runs it: every card the station serves answered from D, no timers
WHOLE_DOM = """
function Node(tag){ this.tag = tag; this.attrs = {}; this.kids = []; this._t = ''; this.style = {}; this.dataset = {}; this.hidden = false; }
Node.prototype.insertBefore = function(n, ref){ var i = this.kids.indexOf(ref); this.kids.splice(i < 0 ? 0 : i, 0, n); return n; };
Node.prototype.setAttribute = function(k, v){ this.attrs[k] = String(v); };
Node.prototype.appendChild = function(n){ this.kids.push(n); return n; };
Node.prototype.addEventListener = function(){};
Object.defineProperty(Node.prototype, 'firstChild', {get: function(){ return this.kids[0] || null; }});
Object.defineProperty(Node.prototype, 'childNodes', {get: function(){ return this.kids; }});
Object.defineProperty(Node.prototype, 'textContent', {get: function(){ return this._t + this.kids.map(function(k){ return k.textContent; }).join(''); },
                                                      set: function(v){ this._t = String(v); this.kids = []; }});
Object.defineProperty(Node.prototype, 'className', {get: function(){ return this.attrs['class'] || ''; }, set: function(v){ this.attrs['class'] = String(v); }});
Object.defineProperty(Node.prototype, 'classList', {get: function(){ var n = this; function has(){ return n.className.split(' ').filter(Boolean); }
  function put(h){ n.className = h.join(' '); }
  return {contains: function(k){ return has().indexOf(k) >= 0; }, add: function(k){ put(has().filter(function(x){ return x !== k; }).concat([k])); },
          remove: function(k){ put(has().filter(function(x){ return x !== k; })); },
          toggle: function(k, on){ put(has().filter(function(x){ return x !== k; }).concat(on ? [k] : [])); }}; }});
var ids = {};
['h1', 'sub', 'state', 'main', 'load', 'poll', 'csTitle', 'csBody'].forEach(function(id){ ids[id] = new Node('div'); });
var document = {hidden: false, getElementById: function(id){ return ids[id] || null; }, createElement: function(t){ return new Node(t); },
                createElementNS: function(ns, t){ return new Node(t); }, querySelectorAll: function(){ return []; }, addEventListener: function(){}};
var window = {addEventListener: function(){}}, localStorage = {getItem: function(){ return null; }, setItem: function(){}}, MiraiSheet = {open: function(){}};
var CARDS = {'spx_jev/latest.json': D.live, 'spx_jev/lanes/tape/latest.json': D.tape, 'spx_jev/lanes/premarket/latest.json': D.premarket};
function fetch(url){
  var c = CARDS[decodeURIComponent(url.split('path=')[1])];
  return Promise.resolve({ok: true, json: function(){ return Promise.resolve(c ? {kind: 'json', data: c} : {error: 'no such file'}); }});
}
var setTimeout = function(){ return 0; }, clearTimeout = function(){}, setInterval = function(){ return 0; };
function dump(n){ return {tag: n.tag, attrs: n.attrs, text: n._t, kids: n.kids.map(dump)}; }
function flat(n){ return n._t + n.kids.map(flat).join(''); }
"""


def _whole(cards, now, tz=LA):
    """The page drawn at ``now`` in ``tz`` from ``cards`` (live, tape, premarket; a missing one is not on file):
    the header line and whether it is an error, the state chips, and each of main's parts as [class, flat text]."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + WHOLE_DOM + MAIN_JS +
              "setImmediate(function(){ console.log(JSON.stringify({sub: ids.sub.textContent, err: ids.sub.classList.contains('err'),"
              " state: ids.state.kids.map(flat), main: ids.main.kids.map(function(k){ return [k.className || k.tag, flat(k)]; }),"
              " dom: ids.main.kids.map(dump)})); });")
    out = subprocess.run([_NODE, "-e", script], input=json.dumps({**cards, "now": now}), capture_output=True, text=True, timeout=20,
                         env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("tz", [LA, TOKYO, NY, KOLKATA])
@pytest.mark.parametrize("now, leads", [
    ("2026-09-28T09:30:00-04:00", "before the open"),                 # the 09:28 pre-market card leads
    ("2026-09-28T10:35:00-04:00", "opening · a call every 5 min"),    # the 10:30 lane call is open
    ("2026-09-28T15:40:00-04:00", "normal · a call every 30 min"),    # the 15:32 read's call is open
    ("2026-09-28T16:10:00-04:00", "normal · a call every 30 min"),
])
def test_mondays_real_cards_draw_whole_in_every_zone(now, leads, tz):
    got = _whole({k: MONDAY[k] for k in ("live", "tape", "premarket")}, now, tz)
    assert not got["err"], got["sub"]
    if leads == "before the open":
        assert got["main"][0] == ["mode", leads]
        return
    # in the session the mode chip heads the leading card, and the header line says nothing of a fresh card
    card = next(d for k, d in zip(got["main"], got["dom"]) if k[0] == "card dashed")
    assert [card["kids"][0]["attrs"]["class"], _flat_text(card["kids"][0])] == ["mode", leads]
    assert got["sub"] == "" and not any(k[0] == "mode" for k in got["main"])


@pytest.mark.parametrize("tz", [LA, TOKYO, KOLKATA])
def test_a_diary_row_with_no_wall_draws_its_fact_in_words(tz):
    """About one diary row in ten names no wall (09-15, 09-21, 09-23, 09-25 in market hours): its nearest-strike fact
    carries a figure with no distance, and the page broke on it ("the card could not be drawn") and lost the situation
    and every question. A fact whose figure has no number is drawn without a gauge, in the builder's sentence."""
    live = {**MONDAY["live"], "situation": MONDAY["no_wall"]["situation"]}
    got = _whole({"live": live, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00", tz)
    assert not got["err"], got["sub"]
    situation = [d for c, d in zip(got["main"], got["dom"]) if c[0] == "card" and c[1].startswith("situation")][0]
    rows = {r["kids"][0]["kids"][0]["text"]: r for r in situation["kids"] if r["attrs"].get("class") == "sit-row"}
    wall = rows["Nearest heavy strike"]
    assert [k["attrs"].get("class") for k in wall["kids"]] == ["sit-top", "sit-c"]      # no gauge
    assert wall["kids"][1]["text"] == "No heavy strike sits within reach on either side of price"
    assert _flat_text(wall["kids"][0]) == "Nearest heavy strikeNone in reach"
    assert [k["attrs"].get("class") for k in rows["Price, last 30 min"]["kids"]] == ["sit-top", "sit-g", "sit-c"]
    assert any(c[0] == "vp" for c in got["main"]), "the questions are drawn below it"


def test_a_signed_fact_that_rounds_to_nothing_says_no_change():
    """The 09-25 11:33 row's implied volatility moved +0.03 vol points, Flat, and the page wrote "Up 0.0 vol points"
    beside the verdict. A change that rounds to nothing at the figure's precision says No change."""
    facts = [{"kind": "signed", "value": 0.03, "unit": "vol points"}, {"kind": "signed", "value": -0.12, "unit": "vol points"},
             {"kind": "signed", "value": 0.004, "unit": "sigma"}, {"kind": "signed", "value": 0.31, "unit": "sigma"},
             {"kind": "signed", "value": -0.004, "unit": "sigma"}]
    got = _run(_fn("signedWords") + "console.log(JSON.stringify(D.f.map(function(f){ return signedWords(f, 'vol.iv_30m'); })));",
               {"f": facts})
    assert got == ["No change", "Down 0.1 vol points", "No change", "Up 0.31 of a normal day\u2019s move", "No change"]


def test_a_dark_question_names_its_own_reason_not_a_news_source_for_every_one():
    """Every dark question sat under "dark, waiting for a news source", overnight_range_position and gamma_cushion among
    them though neither waits for news. The heading says each waits for its own source, and each gives its reason when
    the card carries one, as the questions doc words it (dark_reason)."""
    doc = json.loads((Path(__file__).resolve().parents[3] / "skills" / "spx-jev" / "questions" / "spx_questions.json").read_text())
    dark = [q for g in doc["groups"] for q in g["questions"].values() if q.get("status") == "dark"]
    assert dark and all(q.get("dark_reason") for q in dark), "every dark question in the doc names what it waits for"
    live = {**MONDAY["live"], "dark": [
        {"id": "overnight_range_position", "viewpoint": "levels_and_tape", "ask": "Where is price against the overnight futures range?",
         "dark_reason": "the overnight store (saved at 09:26 ET) holds /ES's bars, but no session read measures the night's range yet"},
        {"id": "news_headline", "viewpoint": "dark", "ask": "What kind of market news just came out?"}]}
    got = _whole({"live": live, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
    (dark,) = [t for c, t in got["main"] if c == "details" and t.startswith("dark")]
    assert dark.startswith("dark, each waiting for its source: 2") and "news source" not in dark
    assert "Dark: the overnight store (saved at 06:26) holds /ES's bars, but no session read measures the night's range yet" in dark
    assert dark.endswith("What kind of market news just came out?Dark until what it needs is on file")


def test_questions_asked_and_lost_are_folded_apart_from_those_not_asked():
    """On 09-28 several reads lost a group to a 503 or a timeout, and its questions sat under "not asked this run" though
    they were asked: the service marks them "asked, no answer received" (service.card), and they fold on their own."""
    live = json.loads(json.dumps(MONDAY["live"]))
    answered = [q for q in live["questions"] if q.get("answer")]
    for q in answered[:3]:
        q.update(answer=None, skipped="asked, no answer received: HTTP 503")
    not_asked = sum(1 for q in live["questions"] if not q.get("answer")) - 3
    got = _whole({"live": live, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
    folds = [t for c, t in got["main"] if c == "details"]
    assert folds[0].startswith("asked, no answer: 3") and folds[0].count("Asked, no answer received: HTTP 503") == 3
    assert folds[1].startswith(f"not asked this run: {not_asked}") and "no answer received" not in folds[1]


@pytest.mark.parametrize("tz, next_read", [(LA, "07:32"), (TOKYO, "23:32"), (KOLKATA, "20:02")])
def test_a_live_card_with_no_sum_says_so_when_the_30_minute_call_leads(tz, next_read):
    """Monday from 10:05 to about 10:17 the live 10:02 read and the lane's 10:00 to 10:10 reads were skipped: the page
    held the 09:32 card, which asks nothing, and said "normal · a call every 30 min" over no call and no reason. It
    says there is no call on this read and when the next read is, as the folded line does while the opening leads."""
    live = {**MONDAY["live"], "row_ts": "2026-09-28T09:31:20-04:00", "generated_at": "2026-09-28T13:32:40+00:00", "hour": None,
            "calls": [], "tally": None, "closed_out_at": None,
            "marks": {"next_30": "2026-09-28T10:00:00-04:00", "next_60": "2026-09-28T10:30:00-04:00"}}
    calls = [c for c in MONDAY["tape"]["calls"] if c["read"] <= "2026-09-28T09:55"]
    tape = {**MONDAY["tape"], "row_ts": "2026-09-28T09:55:00-04:00", "calls": calls, "closed_out_at": None}
    got = _whole({"live": live, "tape": tape}, "2026-09-28T10:10:00-04:00", tz)
    assert not got["err"], got["sub"]
    card = next(d for c, d in zip(got["main"], got["dom"]) if c[0] == "card dashed")
    assert _flat_text(card["kids"][0]) == "normal · a call every 30 min"
    assert _flat_text(card["kids"][2]) == f"No 30-minute call on this read, next read {next_read}"
    assert [k["attrs"].get("class") for k in card["kids"]] == ["mode", "lab", "skip", "tag", "state answered"]
    read = {LA: "06:31", TOKYO: "22:31", KOLKATA: "19:01"}[tz]
    assert _flat_text(card["kids"][3]) == f"The {read} read"      # the header no longer names the row, so the card does
    assert got["sub"].startswith("row ") and ", stale" in got["sub"]     # the 09:31 card is 39 minutes old: said at the top


# Chrome with the shipped face: the result words beside a bar as drawn, "Was <outcome> · " at 10.5px and the verdict
# in bold. The newest bar ends at 211 of the drawing's 300, so 84 units are left after its 5-unit gap
_SIDE_W = {("Was Down · ", "Wrong"): 92.703125, ("Was Down · ", "Right"): 85.5625, ("Was Flat · ", "Wrong"): 82.234375,
           ("Was Flat · ", "Right"): 75.09375, ("Was Up · ", "Wrong"): 78.109375, ("Was Down small · ", "Wrong"): 120.1875,
           ("Was Up big · ", "Unsure"): 97.859375, ("Was Down big · ", "Right"): 103.703125, ("", "Wrong"): 34.125, ("", "Right"): 26.984375,
           ("", "Unsure"): 35.734375, ("Down · ", "Wrong"): 69.65, ("Down · ", "Passed"): 72.33, ("Was Down · ", "Passed"): 95.38,
           ("Ended Down small · ", "Passed"): 134.50}
# the same face shaped by HarfBuzz (hb-shape at wght 400 and 700), for side words no Chrome run measured
_SIDE_W.update({("Down · ", "Right"): 62.52, ("Ended Flat · ", "Right"): 86.74})


def _side():
    return _var("ODDS_LETTERS") + _var("ODDS_EM") + _var("SIDE_FONT_PX") + _var("SIDE_EM") + _fn("sideWidth")


def test_a_result_words_width_is_the_shipped_faces_to_a_64th_of_a_pixel():
    assert "font-size:10.5px" in _rule(".inplay .t-side") and "var SIDE_FONT_PX = 10.5," in _var("SIDE_FONT_PX")
    got = _run(_side() + "console.log(JSON.stringify(D.w.map(function(w){ return sideWidth(w[0], false) + sideWidth(w[1], true); })));",
               {"w": [list(k) for k in _SIDE_W]})
    for words, est in zip(_SIDE_W, got):
        assert _SIDE_W[words] - 1 / 64 <= est <= _SIDE_W[words] + 1.2, f"{words}: {est:.2f}, Chrome {_SIDE_W[words]}"


@pytest.mark.parametrize("tz", [LA, TOKYO, KOLKATA])
@pytest.mark.parametrize("outcome, hit, newest", [("down", False, "Down · Wrong"), ("down", True, "Down · Right"), ("flat", True, "Was Flat · Right")])
def test_the_newest_calls_result_is_never_cut_at_the_drawings_edge(outcome, hit, newest, tz):
    """After Monday's close-out every call in play is graded, the newest bar ending at 211 of 300. "Was Down · Wrong"
    ran to 308.7 and "Was Down · Right" to 301.6, so the verdict was cut on every phone; the side alone now stands with
    the verdict when the whole does not fit (the sheet still says what it ended), and "Was Flat · Right" fits whole.
    Monday's calls are graded here on the average price as a card now carries them, each on the side its end price
    ended on."""
    def on_average(c, outcome, hit):
        rest = {k: v for k, v in c.items() if k not in ("outcome", "hit", "moved")}
        return {**rest, "integral": graded(outcome, "right" if hit else "wrong"), "end_price": {"outcome": outcome, "hit": hit}}
    calls = [on_average(MONDAY["live"]["calls"][0], outcome, hit)] + [on_average(c, c["outcome"], c["hit"]) for c in MONDAY["live"]["calls"][1:]]
    js = (_side() + _fn("fitting") + _fn("fits") + _fn("callsSvg") +
          "console.log(JSON.stringify(dump(callsSvg(D.calls, Date.parse(D.now)))));")
    svg = _run(js, {"calls": calls, "now": "2026-09-28T16:10:00-04:00"}, tz)
    sides = [(float(t["attrs"]["x"]), t["text"], "".join(k["text"] for k in t["kids"])) for t in svg["kids"]
             if t["tag"] == "text" and t["attrs"].get("class") == "t-side"]
    assert sides[0][1] + sides[0][2] == newest
    assert [s[1] + s[2] for s in sides[1:]] == ["Was Down · Right", "Was Flat · Right"]
    for x, text, strong in sides:
        assert x + _SIDE_W[text, strong] <= 300, f"{text}{strong} ends at {x + _SIDE_W[text, strong]:.1f}"


@pytest.mark.parametrize("tz, reopen, close", [(LA, "Sun 15:00", "Fri 13:00"), (TOKYO, "Mon 07:00", "Sat 05:00"),
                                               (NY, "Sun 18:00", "Fri 16:00"), (KOLKATA, "Mon 03:30", "Sat 01:30")])
def test_a_market_time_with_its_weekday_is_drawn_on_the_viewers_day(tz, reopen, close):
    """The builder names a weekend's times with New York's weekday ("reopened at 18:00 Sunday"). The page redrew the
    time on the stamp's own day and left the weekday as written, so in Tokyo Monday's card said "07:00 Sunday" for what
    was Monday 07:00 there. The time is found on its named weekday, the one on or before the stamp's, and both are
    drawn in the viewer's zone."""
    got = _run("console.log(JSON.stringify([marketWords(D.a, D.at), marketWords(D.b, D.at), marketWords('at 14:00 ET', D.at)]));",
               {"at": "2026-09-28T09:28:05-04:00", "a": "since the S&P futures reopened at 18:00 Sunday bitcoin fell",
                "b": "from the Friday 16:00 close to the S&P futures' reopen at 18:00 Sunday"}, tz)
    assert got == [f"since the S&P futures reopened at {reopen} bitcoin fell",
                   f"from the {close} close to the S&P futures' reopen at {reopen}", "at " + _run(
                       "console.log(JSON.stringify(viewerTime('2026-09-28T14:00:00-04:00')));", {}, tz)]


@pytest.mark.parametrize("tz, reopen", [(TOKYO, "Mon 07:00"), (KOLKATA, "Mon 03:30"), (LA, "Sun 15:00")])
def test_mondays_premarket_bitcoin_fact_names_the_viewers_day(tz, reopen):
    got = _whole({k: MONDAY[k] for k in ("live", "tape", "premarket")}, "2026-09-28T09:30:00-04:00", tz)
    card = next(d for c, d in zip(got["main"], got["dom"]) if "pre" in c[0].split())
    facts = _flat_text(next(k for k in card["kids"] if k["attrs"].get("class") == "facts"))
    assert f"since the S&P futures reopened at {reopen} bitcoin futures (/MBT) fell" in facts
    assert "Sunday" not in facts
