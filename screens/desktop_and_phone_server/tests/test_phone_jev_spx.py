"""The SPX forecast page (jev-spx.html, the phone's home at /m): its two modes and today's calls over the SPX
service's cards (pipelines/spx_jev_questions_and_pools), with every time drawn in the viewer's own zone. The SPX card writes every
time as a full stamp with its offset, so the page draws each one through one formatter, viewerTime, in
whatever zone the phone is in; the schedule (which mode leads, when to poll, what "today" is) stays on the
New York market clock. The rules are functions in the page, lifted out by name and run in node against the
stand-in DOM below, here under a TZ that is not New York's."""
from __future__ import annotations

import json
import os
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from node_run import run_node
from test_phone_route import _Stub


M = Path(__file__).resolve().parents[1] / "static" / "m"
SPX = (M / "jev-spx.html").read_text()
JS = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", SPX))
_NODE = shutil.which("node")
LA, TOKYO, NY = "America/Los_Angeles", "Asia/Tokyo", "America/New_York"


# The stand-in DOM the page's functions run against in node (shared with the Reads tests, which import it from
# here): enough of createElement, appendChild, textContent and setAttribute for the card builders, and dump() to
# read a built tree back as plain data.
FAKE_DOM = """
function Node(tag){ this.tag = tag; this.attrs = {}; this.kids = []; this._t = ''; this.style = {}; }
Node.prototype.insertBefore = function(n, ref){ var i = this.kids.indexOf(ref); this.kids.splice(i < 0 ? 0 : i, 0, n); return n; };
Object.defineProperty(Node.prototype, 'firstChild', {get: function(){ return this.kids[0] || null; }});
Node.prototype.setAttribute = function(k, v){ this.attrs[k] = String(v); };
Node.prototype.appendChild = function(n){ this.kids.push(n); n.parentNode = this; return n; };
Node.prototype.addEventListener = function(){};
Object.defineProperty(Node.prototype, 'textContent', {get: function(){ return this._t + this.kids.map(function(k){ return k.textContent; }).join(''); },
                                                      set: function(v){ this._t = String(v); this.kids = []; }});
var document = {createElementNS: function(ns, tag){ return new Node(tag); }, createElement: function(tag){ return new Node(tag); }};
function el(tag, cls, text){ var e = document.createElement(tag); if(cls) e.attrs['class'] = cls; if(text != null) e.textContent = text; return e; }
function dump(n){ return {tag: n.tag, attrs: n.attrs, text: n._t, kids: n.kids.map(dump)}; }
"""


def _flat_text(n):
    """A dumped node's text with its children's, in order, as the page would show it."""
    return n["text"] + "".join(_flat_text(k) for k in n["kids"])


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
              + "".join(_fn(f) for f in ("viewerTime", "marketAt", "marketWords", "cap", "pct", "words", "startOf",
                                          "leftWords", "verdict", "endPrice", "gradeOf", "laneLeads", "svgEl", "lastLaneRead", "hhmm", "marketClock",
                                          "marketDay", "sentence", "callSum", "endVerdict", "fallbackWords", "fallbackLine"))
              + _var("VIEWER_FMT") + _var("WEEKDAYS") + _var("MARKET_TIME") + _var("NS") + _var("ROW_H") + js)
    out = run_node(_NODE, script, input=json.dumps(data), capture_output=True, text=True, timeout=20,
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


# ---- the wiring


def test_the_page_reads_the_spx_cards_and_keeps_the_sndk_pages_modes():
    assert "var URL_ = '/api/store/file?name=JEV_LIVE_CARD';" in JS
    assert "var TAPE_URL = '/api/store/file?name=JEV_OPENING_CARD';" in JS
    assert "fetch(URL_, {cache:'no-store'})" in JS and "fetch(TAPE_URL, {cache:'no-store'})" in JS
    assert "shownLeads = laneLeads(tape, Date.now(), marketDay());" in _fn("paint")
    assert "main.appendChild(laneCard(tape, tapeOk));" in _fn("paint") and "main.appendChild(foldLive(c));" in _fn("paint")
    assert "} else sumCard(c, main);" in _fn("paint")
    assert "var ex = expiryLine(c); if(ex) main.appendChild(ex);" in _fn("paint")
    assert "laneLeads(tape, Date.now(), marketDay()) !== shownLeads" in _fn("tick")
    assert "setInterval(function(){ if(!document.hidden){ poll(); pollTape(); tick(); } }, POLL_MS);" in JS
    # the live price rides the station's existing quote route, every SPOT_MS in the session (inSession, the market-clock gate:
    # the session's hours on a trading day), and on load and waking
    assert "var SPOT_URL = '/api/spot?ticker=SPX', SPOT_MS = 5000, SESSION_HOURS = ['09:30', '16:00'];" in JS
    assert "setInterval(function(){ if(!document.hidden && inSession()) pollSpot(); }, SPOT_MS);" in JS
    assert "pollSpot();" in _fn("wake") and "poll(); pollTape(); pollSpot(); armMark(); armTapeMark();" in JS
    assert "LANE_HOURS = ['09:33', '16:05']" in JS and "within(LANE_HOURS)" in _fn("pollTape")
    # the questions left the page for their own sheet (09-29), so no fold is remembered on the phone any more
    assert "jev.spx.folded" not in JS and "'jev.folded'" not in JS and "el('details'" not in JS
    assert "<title>SPX · Forecast</title>" in SPX and '<span id="h1">SPX &middot; Forecast</span>' in SPX
    assert "' \\u00B7 Forecast'" in _fn("paint") and "' \\u00B7 Forecast'" in _fn("paintPre"), "the script writes the header's name too"
    assert "innerHTML" not in JS
    # the call sheet, opened by sheet.js loaded first
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
    for f in ("clockBlock", "checksSvg", "openSituation", "paintClaude", "openWhy", "foldLive", "laneCard", "question", "subLine", "expiryLine"):
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


def _calls_row(calls, name="Today\u2019s calls", link=True, now="2026-09-28T10:45:00-04:00", tz=LA):
    """callsRow over a card's ``calls`` (newest first, as a card carries them), each on the state callState gives it,
    drawn into a link when ``link``: the link's attributes, the row's words and each square's class, oldest first."""
    js = (_fn("labRow") + _fn("callState") + _fn("callsRow")
          + "var into = D.link ? el('a', 'card calls-link') : null; if(into) into.setAttribute('href', '/m/reads.html');"
          "var r = callsRow(D.calls.slice().reverse().map(callState), D.name, into);"
          "console.log(JSON.stringify({tag: r.tag, cls: r.attrs['class'], into: into && into.attrs, held: into ? into.kids.length : 0, words: r.kids[0].textContent,"
          " sq: r.kids[1].kids.map(function(i){ return i.attrs['class'] || 'right'; }), sqLabel: r.kids[1].attrs['aria-label']}));")
    return _run(js, {"calls": calls, "name": name, "link": link, "now": now}, tz)


def test_todays_calls_are_squares_oldest_first_with_the_days_score_in_words():
    """Final-Home (2026-10-10): the calls-in-play drawing and the day's tally fold are one row, "N of M right · K open",
    with one square per call, oldest on the left: filled when right, outlined when graded otherwise, dashed while still
    to grade. In the dock the row is drawn into a card that links to the reads, which carry each call's numbers, and
    the link says so."""
    got = _calls_row(MORNING)
    assert got["tag"] == "div" and got["cls"] == "calls" and got["held"] == 1
    assert got["into"] == {"class": "card calls-link", "href": "/m/reads.html", "aria-label": "Today\u2019s calls: 1 of 2 right \u00B7 2 open. Open the reads"}
    assert got["words"] == "Today\u2019s calls1 of 2 right \u00B7 2 open" and got["sq"] == ["wrong", "right", "open", "open"]
    assert got["sqLabel"] == "1 of 2 right \u00B7 2 open, oldest first"
    assert ".calls-link .lab::after" in SPX and "dashed" in _rule(".sq i.open") and _rule(".sq i.wrong").startswith("background:none;border:1.2pxsolid")
    # the opening card's row stands alone: the reads list the 30-minute calls only
    lane = _calls_row(MORNING, "Opening calls", False)
    assert lane["into"] is None and lane["words"].startswith("Opening calls1 of 2 right")
    none = _calls_row([])
    assert none["words"] == "Today\u2019s callsnone graded yet" and none["sq"] == [] and none["sqLabel"] == "no call yet"


def test_an_unsure_call_is_passed_in_the_row_never_counted_as_a_miss():
    """The service counts a call whose pick was unsure as passed (service.calls_block): the row counts it apart from
    the calls right and wrong, as an outlined square, never as a miss; a card from before the average-price grade
    counted its passes at the end price, and they read as passes too."""
    unsure = call("09:35", "09:45", "unsure", 0.4, integral=graded("down", "passed", -4.03, 2.41, lean={"direction": "flat", "p": 0.45}),
                  end_price={"outcome": "down_small", "hit": False})
    got = _calls_row([call("09:45", "09:55", "down", 0.5, integral=graded("down", "right")), call("09:40", "09:50", "up", 0.5, integral=graded("down", "wrong")), unsure])
    assert got["words"] == "Today\u2019s calls1 of 2 right \u00B7 1 passed" and got["sq"] == ["wrong", "wrong", "right"]
    old = {**call("09:45", "09:55", "unsure", 0.38), "outcome": "up_big", "hit": False}
    assert _calls_row([old])["words"] == "Today\u2019s callsnone graded yet \u00B7 1 passed"
    assert _run("console.log(JSON.stringify(gradeOf(D.c)));", {"c": old}) == {"label": "up_big", "verdict": "Passed", "endOnly": True}


def test_a_call_with_no_average_price_grade_stands_on_its_end_price_in_the_row():
    """The average could not grade it (the average-price grade failed, or its window missed bars): the square stands on
    the end price's verdict, as the tally counts it (service.call_verdict), never left open for ever."""
    live = {**call("10:32", "11:02", "flat", 0.7), "end_price": {"outcome": "flat", "hit": True, "moved": {"realized_sigma": 0.02}}, "end_price_only": True}
    assert _run("console.log(JSON.stringify(gradeOf(D.c)));", {"c": live}) == {"label": "flat", "verdict": "Right", "endOnly": True}
    assert _calls_row([live], now="2026-09-28T11:04:00-04:00")["sq"] == ["right"]
    holed = {**live, "pick": "unsure", "integral": {"graded": False, "reason": "not graded: bars missing"}}
    assert _calls_row([holed], now="2026-09-28T16:30:00-04:00")["words"].endswith("none graded yet \u00B7 1 passed")
    # the window closed and nothing graded yet at all: still open, until the next run grades it
    pending = {k: v for k, v in live.items() if k not in ("end_price", "end_price_only")}
    assert _calls_row([pending], now="2026-09-28T11:04:00-04:00")["sq"] == ["open"]


def test_a_call_closed_for_good_is_never_graded_in_the_row_not_still_open():
    """A lane call whose window was halted is closed for good (``closed``, as a horizon past the close has no mark): the
    row counts it apart, outlined, never as a call still open; a call closed at its end price but graded on the average
    is graded, counted once (service.calls_block)."""
    halted = call("09:45", "09:55", "up_small", 0.38, closed="halted window: no settled open (the 09:34 bar) on a finished day")
    both = call("10:30", "10:40", "up", 0.5, integral=graded("up", "right"), closed="halted window: no bar at the mark on a finished day")
    past = {**call("15:32", "16:00", "flat", 0.6), "mark": None}
    got = _calls_row([both, halted, past], now="2026-09-28T16:30:00-04:00")
    assert got["words"] == "Today\u2019s calls1 of 1 right \u00B7 2 never graded" and got["sq"] == ["wrong", "wrong", "right"]
    assert _run(_fn("callState") + "console.log(JSON.stringify(D.c.map(callState)));",
                {"c": [both, halted, past, call("10:40", "10:50", "flat", 0.5)], "now": "2026-09-28T10:45:00-04:00"}) == ["right", "never", "never", "open"]


def test_the_days_calls_row_sits_in_the_dock_under_claudes_read_as_a_card():
    """Monday's card and close-out log said 6 of 11 right while the page drew at most four calls (a card carries its last
    few) and never the score: the dock's first card is today's calls, every graded call of the day from the grades file
    (as the chart's squares) as a square with the score in words, before the situation; the fold that gave the tally in
    words is gone with the drawing, and the grades are asked for on every new card."""
    rows = [grade(f"{10 + i // 2:02d}:{'02' if i % 2 == 0 else '32'}", "wrong" if i in (1, 4, 6, 8, 10) else "right", day="2026-09-28") for i in range(11)]
    got = _whole({**{k: MONDAY[k] for k in ("live", "tape", "premarket")}, "grades": rows}, "2026-09-28T16:04:00-04:00")
    assert [k for k, _ in got["dock"]] == ["card calls-link", "card sit"]
    assert got["dock"][0][1] == "Today\u2019s calls6 of 11 right" and got["dock_dom"][0]["attrs"]["href"] == "/m/reads.html"
    assert got["dock_dom"][0]["attrs"]["aria-label"] == "Today\u2019s calls: 6 of 11 right. Open the reads"
    squares = got["dock_dom"][0]["kids"][0]["kids"][1]["kids"]
    assert [q["attrs"].get("class") for q in squares] == [None, "wrong", None, None, "wrong", None, "wrong", None, "wrong", None, "wrong"]
    assert not any(k == "card fold" and t.startswith("30-min calls") for k, t in got["main"])
    bare = _whole({k: MONDAY[k] for k in ("live", "tape", "premarket")}, "2026-09-28T15:40:00-04:00")
    assert bare["dock"][0][1] == "Today\u2019s callsnone graded yet"       # no grades file on the station yet
    assert "if(!same){ gradesAt = 0; pollGrades(); }" in _fn("arrived") and "paintCalls();" in _fn("draw") and "paintCalls();" in _fn("pollGrades")


def test_a_new_card_asks_for_the_grades_at_once_and_keeps_a_call_open_until_its_grade_lands():
    """The grades are asked for at most once a minute while the chart is open: a new card asks past that throttle, since
    the run that grades the last call writes the card with it, and the same card again never asks. Between a call's
    mark passing and its grade landing (the grader runs a minute or two after the mark) the call stays in the row as an
    open square from the card's own calls instead of vanishing; once the grades file carries it, it is drawn once, from
    the file, which is the row's one source of grades (a card carries only its last few)."""
    js = ("var calls = 0, last = null, lastOk = true, barsDay = null;"
          "function fetch(){ calls++; return Promise.resolve({ok: true, json: function(){ return Promise.resolve({kind: 'jsonl', rows: []}); }}); }"
          "function $(id){ return {getAttribute: function(){ return 'true'; }, classList: {contains: function(){ return false; }}, dataset: {}}; }"
          "function priceDay(){ return null; } function pollBars(){} function pollBook(){} function draw(){} function ages(){} function tick(){} function paintCalls(){} function paintChart(){}"
          "function chartDocked(){ return false; }" + _var("GRADES_URL") + _var("BOOK_URL") + _var("grades") + "".join(_fn(f) for f in ("chartVisible", "rawRows", "pollGrades", "arrived"))
          + "function settle(){ return new Promise(function(r){ setImmediate(r); }); }"
          "(async function(){ var out = {};"
          " arrived({generated_at: 'g1', row_ts: 'r1'}); await settle(); out.first = calls;"
          " arrived({generated_at: 'g1', row_ts: 'r1'}); await settle(); out.same = calls;"
          " NOW += 20000; arrived({generated_at: 'g2', row_ts: 'r2'}); await settle(); out.fresh = calls;"
          " pollGrades(); await settle(); out.throttled = calls;"
          " console.log(JSON.stringify(out)); })();")
    assert _run(js, {"now": "2026-10-09T16:02:00-04:00"}) == {"first": 1, "same": 1, "fresh": 2, "throttled": 2}
    stamp = lambda hhmm: f"2026-10-09T{hhmm}:40-04:00"
    calls = [{"read": stamp("15:31"), "mark": "2026-10-09T16:00:00-04:00", "minutes": 30, "pick": "flat", "p": 0.5},
             {"read": stamp("15:01"), "mark": "2026-10-09T15:31:00-04:00", "minutes": 30, "pick": "up", "p": 0.4},       # its mark passed, no grade yet
             {"read": stamp("14:30"), "mark": "2026-10-09T15:00:00-04:00", "minutes": 30, "pick": "flat", "p": 0.6, "integral": graded("flat", "right")},
             {"read": stamp("14:00"), "mark": "2026-10-09T14:30:00-04:00", "minutes": 30, "pick": "down", "p": 0.5, "integral": graded("up", "wrong")}]
    js = ("var last = D.card, grades = null;" + "".join(_fn(f) for f in ("cardDay", "dayCalls", "callState", "dayStates"))
          + "var out = {bare: dayStates(last)}; grades = D.file; out.file = dayStates(last); grades = D.file.concat([D.landed]); out.landed = dayStates(last);"
          "console.log(JSON.stringify(out));")
    got = _run(js, {"card": {"row_ts": stamp("15:31"), "calls": calls}, "file": [grade("14:00", "wrong"), grade("14:30", "right")],
                    "landed": grade("15:01", "right"), "now": "2026-10-09T15:33:00-04:00"})
    assert got == {"bare": ["open", "open"], "file": ["wrong", "right", "open", "open"], "landed": ["wrong", "right", "right", "open"]}
    # through the whole page: Monday's 15:31 call before its grade, with no grades file on the station
    live = {**MONDAY["live"], "calls": [{k: v for k, v in c.items() if k not in ("outcome", "hit", "moved")} if i == 0 else c
                                        for i, c in enumerate(MONDAY["live"]["calls"])]}
    got = _whole({"live": live, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
    assert got["dock"][0][1] == "Today’s callsnone graded yet · 1 open"
    assert [q["attrs"].get("class") for q in got["dock_dom"][0]["kids"][0]["kids"][1]["kids"]] == ["open"]


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


def test_the_header_names_the_cards_market_day_and_the_next_read_in_the_viewers_zone():
    """The redesign's header (Final-Home, 2026-10-10): "SPX · Forecast" on the left; on the right the market day the
    card belongs to, its stamp's own date and never the viewer's, and the next read while one is still ahead, in the
    viewer's zone. After the day's last read, or on a card of another day, the day stands alone; on a session day before
    its first card the day is today's with the day's next read, the card's day being in the trouble line. The pre-market
    card names its day and the night's next checkpoint the same way, then the day's first live read once the checkpoints
    are past, and both move with the clock."""
    js = ("var READ_MINUTES = [2, 32], LAST_READ_DEFAULT = '15:32', LAST_READ_LEAD_MIN = 28, nodes = {hdR: el('span')}; function $(id){ return nodes[id]; } function chartDocked(){ return !!D.wide; }"
          "var last = D.c, premarket = D.pre || null, tape = null;" + _var("SPOT_URL") + _fn("within") + _fn("cardDay") + _fn("todaysCard") + _fn("dayClose") + _fn("inSession")
          + _fn("waiting") + _fn("nextRead") + _fn("dayName") + _fn("mins") + _fn("ageWord") + _fn("headLine") + _fn("setHead") + _fn("preNextRead") +
          "setHead(D.c); console.log(JSON.stringify([nodes.hdR.textContent, D.pre ? headLine(D.pre.day, preNextRead(D.pre, Date.now())) : null]));")
    card = {"row_ts": "2026-10-09T11:31:56-04:00", "session": {"last_read": "2026-10-09T15:32:00-04:00", "close": "2026-10-09T16:00:00-04:00"}}
    assert _run(js, {"now": "2026-10-09T11:40:00-04:00", "c": card}, LA)[0] == "Fri 10-09 · next read 09:02"
    assert _run(js, {"now": "2026-10-09T11:40:00-04:00", "c": card}, TOKYO)[0] == "Fri 10-09 · next read 01:02"   # Saturday in Tokyo, Friday's card
    assert _run(js, {"now": "2026-10-09T15:40:00-04:00", "c": card}, LA)[0] == "Fri 10-09"                         # after the day's last read
    assert _run(js, {"now": "2026-10-10T11:40:00-04:00", "c": card}, LA)[0] == "Fri 10-09"                         # the weekend: no read ahead
    pre = {"day": "2026-10-12", "schedule": {"reads": ["2026-10-12T02:35:00-04:00", "2026-10-12T06:35:00-04:00", "2026-10-12T08:50:00-04:00"]}}
    assert _run(js, {"now": "2026-10-12T05:00:00-04:00", "c": card, "pre": pre}, LA)[1] == "Mon 10-12 · next read 03:35"
    assert _run(js, {"now": "2026-10-12T09:00:00-04:00", "c": card, "pre": pre}, LA)[1] == "Mon 10-12"
    # from 1024px up the board's fuller line (Final-Desktop): the read's time and age before the next read; after the
    # day's last read the day alone, and the pre-market card's line as on a phone
    assert _run(js, {"now": "2026-10-09T11:40:00-04:00", "c": card, "wide": True}, LA)[0] == "Fri 10-09 · read 08:31, 8 min ago · next 09:02"
    assert _run(js, {"now": "2026-10-09T15:40:00-04:00", "c": card, "wide": True}, LA)[0] == "Fri 10-09"
    assert _run(js, {"now": "2026-10-12T05:00:00-04:00", "c": card, "pre": pre, "wide": True}, LA)[1] == "Mon 10-12 · next read 03:35"
    assert "setHead(c);" in _fn("paint") and "setHead(c);" in _fn("ages"), "the next read moves with the clock"
    assert "nr = preNextRead(p, now);" in _fn("paintPre") and "if(head) head.textContent = headLine(p.day, nr);" in _fn("paintPre")
    assert '<span class="hd-r" id="hdR"></span>' in SPX and "BETA" not in SPX.split("<body>", 1)[1].split("<main", 1)[0]


def test_the_header_line_names_the_row_in_the_viewers_time_and_knows_the_close():
    js = ("var READ_MINUTES = [2, 32], GONE_MIN = 60, STALE_MIN = 35, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          + _fn("nextRead") + _fn("ageWord") + _fn("cardCount") + _fn("subLine") + "console.log(JSON.stringify(subLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T11:01:56-04:00", "labels": 43,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": c, "m": 12}, LA) == "row 08:01, 12 min ago, 43 labels"
    last = {**c, "row_ts": "2026-09-28T15:31:56-04:00"}
    assert _run(js, {"now": "2026-09-28T16:10:00-04:00", "c": last, "m": 38}, LA) == "after the close, last row 12:31, 43 labels"
    assert _run(js, {"now": "2026-09-28T15:50:00-04:00", "c": last, "m": 18}, TOKYO).startswith("row 04:31, 18 min ago")
    # the card's counts under their own names (service.card's labels_count), the bare name only on a card from before them
    counted = {**{k: v for k, v in c.items() if k != "labels"}, "labels_count": 43}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": counted, "m": 12}, LA) == "row 08:01, 12 min ago, 43 labels"


def _count(c, tz=LA):
    return _run(_fn("cardCount") + _fn("countWords") + "console.log(JSON.stringify(countWords(D.c)));", {"c": c}, tz)


def test_the_count_of_answers_reads_the_cards_counts_under_their_own_names_in_the_questions_sheet():
    """The card's fresh and held are counts, while a record's are the answers themselves (I25): the card writes
    fresh_count and held_count, and the count reads them, or the bare names on a card from before them. Since
    Final-Home (2026-10-10) the count is said in the questions sheet's header, not as a chip on the card, and the
    questions control on the page says how many the read asked; the top keeps only trouble."""
    live = {k: v for k, v in MONDAY["live"].items() if k not in ("fresh", "held", "labels")}
    answered = sum(1 for q in live["questions"] if q.get("answer"))
    for c in ({**live, "fresh_count": 7, "held_count": 3, "labels_count": 119}, {**live, "fresh": 7, "held": 3, "labels": 119}):
        assert _count(c) == f"{answered} of {len(c['questions'])} answered, 7 fresh on the 12:31 row"
        got = _whole({"live": c, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")
        assert got["state"] == [] and "answered" not in json.dumps(got["dom"])
        assert got["qBtn"] == {"hidden": False, "text": f"Questions \u00B7 {c.get('asked') or 0} asked"}
    assert "qs.count = countWords(c);" in _fn("qsLoad") and "$('qsCount').textContent = 'This read: ' + qs.count;" in _fn("qsDraw")
    assert '<div class="qs-sub" id="qsCount"></div>' in SPX


def test_a_read_that_asked_no_question_says_so_instead_of_a_count_of_nothing():
    """From the cut-over a read whose gates all stayed shut asks nothing: the count says "no question asked" (under the
    sheet's "This read:" prefix), never "0 of 6 answered"; a read that answered, or held, something keeps its count."""
    live = {k: v for k, v in MONDAY["live"].items() if k not in ("fresh", "held", "labels")}
    none = {**live, "asked": 0, "fresh_count": 0, "held_count": 0, "questions": [{**q, "answer": None, "skipped": "gate: not fired"} for q in live["questions"]]}
    assert _count(none) == "no question asked"
    assert _whole({"live": none, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00")["qBtn"]["text"] == "Questions \u00B7 0 asked"
    assert "!answered && !c.asked" in _fn("countWords")


def test_the_header_line_is_kept_for_a_stale_card_alone():
    """Will's layout of 09-29: the row line under the title is gone. A fresh card and the day's last one after the
    close say nothing there (the live price and the card's own clock do); an error is still said there (say). In the
    session a card DELAYED_MIN old with no new one says since when nothing has come and that the station is behind
    (10-13: Schwab's login lapsed and the 10:31 card sat for 4.5 hours under a muted "stale"); on a day that is no
    session day of the card's (no lane has read, no expiry calendar on it) a card of another day is the last session's,
    in subLine's words."""
    js = ("var READ_MINUTES = [2, 32], GONE_MIN = 60, STALE_MIN = 35, DELAYED_MIN = 40, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          "var last = D.c, premarket = null, tape = null;" + _var("SPOT_URL") + _fn("within") + _fn("cardDay") + _fn("todaysCard") + _fn("dayClose") + _fn("inSession")
          + _fn("waiting") + _fn("nextRead") + _fn("ageWord") + _fn("cardCount") + _fn("subLine") + _fn("staleLine")
          + "console.log(JSON.stringify(staleLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T11:01:56-04:00", "labels_count": 43,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": c, "m": 12}, LA) == ""
    assert _run(js, {"now": "2026-09-28T11:44:00-04:00", "c": c, "m": 42}, LA) == "No read since 08:01, station data delayed"
    last = {**c, "row_ts": "2026-09-28T15:31:56-04:00"}
    assert _run(js, {"now": "2026-09-28T16:10:00-04:00", "c": last, "m": 38}, LA) == ""
    # yesterday's last card on a day no lane has read, with no expiry calendar on it to make today a session day: the last session's
    assert _run(js, {"now": "2026-09-29T11:00:00-04:00", "c": last, "m": 1168}, LA) == "after the close, last row 12:31, 43 labels"
    assert "s.hidden = false;" in _fn("say") and "s.hidden = !text;" in _fn("setSub")
    assert "setSub(staleLine(c, m));" in _fn("paint") and "setSub(staleLine(c, m));" in _fn("ages")


# Widths measured in Chrome with the shipped face at the header line's 13px: the stale line with its label count ran
# past the 328px column on the owner's 360px phone and was cut before its next read ("next read 07:…"). Since 10-13 the
# line carries no next read (where it is shown, after the close, none is ahead) and in the session the trouble line
# stands in its place; each line the header shows fits
_SUB_W = {"row 06:31, 39 min ago, stale, 125 labels, next read 07:32": 333.80, "row 11:31, 1 hr 39 min ago, stale": 180.81,
          "row 12:31, 20 hr 29 min ago, stale": 196.17, "No read since 06:31, station data delayed": 251.53}


def test_a_stale_header_line_fits_the_owners_360px_phone():
    """Monday 10:10 ET: the 09:31 card was 39 minutes old, and its line was cut at "next read 07:…". A stale line
    drops the label count, which says least about an old card. Since 10-13 the session's header says nothing at 39
    minutes and that the station is delayed from DELAYED_MIN; the stale line is the close's, when the day's last reads
    were missed, with no next read ahead to carry."""
    js = ("var READ_MINUTES = [2, 32], GONE_MIN = 60, STALE_MIN = 35, DELAYED_MIN = 40, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          "var last = D.c, premarket = null, tape = null;" + _var("SPOT_URL") + _fn("within") + _fn("cardDay") + _fn("todaysCard") + _fn("dayClose") + _fn("inSession")
          + _fn("waiting") + _fn("nextRead") + _fn("ageWord") + _fn("cardCount") + _fn("subLine") + _fn("staleLine")
          + "console.log(JSON.stringify(staleLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T09:31:20-04:00", "labels": 125,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T10:10:00-04:00", "c": c, "m": 39}, LA) == ""
    delayed = _run(js, {"now": "2026-09-28T10:12:00-04:00", "c": c, "m": 41}, LA)
    assert delayed == "No read since 06:31, station data delayed"
    missed = {**c, "row_ts": "2026-09-28T14:31:20-04:00"}
    stale = _run(js, {"now": "2026-09-28T16:10:00-04:00", "c": missed, "m": 99}, LA)
    assert stale == "row 11:31, 1 hr 39 min ago, stale"
    column = 360 - 2 * 16
    assert _SUB_W[stale] <= column < _SUB_W["row 06:31, 39 min ago, stale, 125 labels, next read 07:32"]
    for line in (delayed, "row 12:31, 20 hr 29 min ago, stale"):
        assert _SUB_W[line] <= column


def test_the_last_lane_read_is_known_from_the_schedules_stamps():
    reads = [f"2026-09-28T{t}:00-04:00" for t in ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10",
                                                    "10:15", "10:20", "10:25", "10:30")]
    cards = [{"row_ts": f"2026-09-28T{t}:00-04:00", "schedule": {"reads": reads}} for t in ("10:30", "10:29", "10:27", "10:25")]
    cards.append({"row_ts": "2026-09-28T10:20:00-04:00", "schedule": {"reads": reads}, "closed_out_at": "2026-09-28T14:42:10+00:00"})
    assert _run("console.log(JSON.stringify(D.map(lastLaneRead)));", cards, TOKYO) == [True, True, False, False, True]
    # the hand-back is an instant from the card, drawn in the viewer's zone
    assert "viewerTime(Date.parse(sch.reads[sch.reads.length - 1]) + sch.looks_ahead_min * 60000)" in _fn("laneCard")


# ---- the expiry line


def _expiry(x, now, tz=LA):
    js = ("var tickers = [];" + _var("EXPIRY_TAGS") + _fn("untilWords") + _fn("expiryLine") +
          "var r = expiryLine({expiries: D.x}); console.log(JSON.stringify(r && r.kids.map(function(k){ return [k.attrs['class'] || k.tag, k.textContent]; })));")
    return _run(js, {"x": x, "now": now}, tz)


EXPIRIES = {"today_settles_at": "2026-09-30T16:00:00-04:00", "next_settles_at": "2026-10-01T16:00:00-04:00",
            "next_monthly_settles_at": "2026-10-16T16:00:00-04:00", "expiring_today": ["quarter_end"]}


def test_the_expiry_line_counts_down_to_todays_settle_and_tags_the_big_days():
    got = _expiry(EXPIRIES, "2026-09-30T11:14:00-04:00")
    assert got == [["span", "Options settle 13:00 PDT"], ["b", "4 hr 46 min left"], ["state", "Quarter-end"]]
    opex = {**EXPIRIES, "today_settles_at": "2026-09-18T16:00:00-04:00", "expiring_today": ["monthly", "quarterly"]}
    assert _expiry(opex, "2026-09-18T15:31:00-04:00", TOKYO) == [
        ["span", "Options settle 05:00 GMT+9"], ["b", "29 min left"], ["state", "Quarterly OpEx"]]
    month = {**EXPIRIES, "today_settles_at": "2026-10-16T16:00:00-04:00", "expiring_today": ["monthly"]}
    assert _expiry(month, "2026-10-16T09:40:00-04:00")[2] == ["state", "Monthly OpEx"]


def test_after_the_settle_the_line_names_the_next_expiry_with_its_day():
    assert _expiry(EXPIRIES, "2026-09-30T16:05:00-04:00") == [["span", "Next expiry settles Thu 13:00 PDT"]]
    # a card from an earlier day never counts down its own day's settle; its next one, when that is today's (the session
    # day before its first card), it counts down, without the earlier day's tags
    assert _expiry(EXPIRIES, "2026-10-01T09:40:00-04:00", TOKYO) == [["span", "Options settle 05:00 GMT+9"], ["b", "6 hr 20 min left"]]
    assert _expiry(None, "2026-09-30T11:14:00-04:00") is None


def test_every_line_the_page_writes_starts_with_a_capital():
    assert JS.count("el('div', 'tag', ") == 1 and JS.count("el('div', 'skip', ") == 1
    for word in ("'Options settle '", "'Next expiry settles '", "'Opening call'", "'No change'",
                 "'Called at'", "'Usual odds'", "'Read '", "'Graded '"):
        assert word in JS, word


# ---- the owner's 360px phone

# Widths measured in Chrome with the shipped face at 360 (Plus Jakarta Sans): each variable line at the
# widest form it takes. The header's two sides (11px capitals, spaced .16em and .12em) were measured in headless
# Chromium on 2026-10-10 with the redesign; the right side is widest on a Monday, "MON" being the widest weekday in
# capitals, and its digits are tabular, so no time is wider than another. A zone name is widest as an offset
# ("GMT+5:30"); a countdown under 10 hours.
_W = {"SPX · FORECAST": 113.09, "MON 10-09 · NEXT READ 12:01": 198.44, "FRI 10-09 · NEXT READ 12:01": 187.59,
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
    """At 360, the Galaxy S20+, nothing runs past the page or is cut: the header line carries the name on the left
    and the market day with the next read on the right, on one line on every weekday (at 320 the right side wraps
    under the name, whole), and the refresh spinner, out of the line's flow just after the name, ends before the
    right side begins on the widest day; the expiry line wraps between its parts, and each part fits the column
    alone; the calls in play and the odds are drawn in a 300-wide box scaled to the card. Checked in Chrome at
    360x800 in both modes, in Los Angeles, Tokyo and New York, with no element past the page and none clipped;
    this pins the widths that check found."""
    side = 16
    column = 360 - 2 * side
    card = column - 2 * _px(".card", "padding")
    gap = re.search(r"gap:[\d.]+px([\d.]+)px", _rule(".hd-1"))       # the row gap, then the gap between the two sides
    assert gap, ".hd-1 lost its gaps"
    assert "flex-wrap:wrap" in _rule(".hd-1") and "text-transform:uppercase" in _rule(".hd-1")
    assert "margin-left:auto" in _rule(".hd-r") and "white-space:nowrap" in _rule(".hd-r") and "white-space:nowrap" in _rule(".hd-l")
    # the widths above are of tabular digits, which the font shorthand on .hd-1 resets: with proportional digits the
    # widest line would be another string, and the time would shift the line every half hour
    assert "font-variant-numeric:tabular-nums" in _rule(".hd-r")
    row = _W["SPX · FORECAST"] + float(gap.group(1)) + _W["MON 10-09 · NEXT READ 12:01"]
    assert row <= column, f"the header line is {row:.1f} of {column}"
    assert "position:absolute" in _rule(".poll") and "left:100%" in _rule(".poll") and "position:relative" in _rule(".hd-l")
    spinner = _W["SPX · FORECAST"] + _px(".poll", "margin-left") + _px(".poll", "width")
    assert spinner <= column - _W["MON 10-09 · NEXT READ 12:01"], f"the spinner ends at {spinner:.1f}, under the right side"
    assert "flex-wrap:wrap" in _rule(".exp")
    pad = re.search(r"padding:0([\d.]+)px", _rule(".exp"))
    assert pad, ".exp lost its side padding"
    exp = column - 2 * float(pad.group(1))
    for part in ("Today’s 0DTE settles 13:00 GMT+5:30", "6 hr 30 min left", "Next expiry settles Mon 16:00 GMT+5:30", "Quarterly OpEx"):
        assert _W[part] <= exp, f"{part!r} is wider than the expiry line at 360"
    assert _W["NORMAL · A CALL EVERY 30 MIN"] <= column
    assert _W["Read 06:50 GMT+5:30 … Graded 07:00"] <= card
    # the pre-market drawings are 300 wide in their own units and scale to the card, so they never run past it
    assert "width:100%;height:auto" in _rule(".inplay") and "viewBox: '0 0 300 '" in _fn("checksSvg")
    # the odds line under the bar is one line of words that wraps rather than cut (a five-way call on an old card)
    assert "nowrap" not in _rule(".odds-lab") and "overflow" not in _rule(".odds-lab") and "font:40011.5px/" in _rule(".odds-lab")


# Chrome at 360 with the shipped face, measured on the live page on 2026-10-09: the leading card's mode chip in the clock's
# row (11px capitals spaced .08em, 7px side padding, 1px border) and the time left beside it (11px bold, spaced .1em)
_CLOCK_W = {"30-MIN CALL": 98.09, "10-MIN CALL · EVERY 5 MIN": 183.0, "WINDOW CLOSED": 113.5, "UNDER 1 MIN LEFT": 112.53,
            "10 MIN LEFT": 74.7}


def test_the_leading_cards_chip_and_time_left_share_one_row_on_the_owners_phone():
    """Will's spacing pass of 2026-10-09: the mode chip and the time left are one row, so the 30-minute card's top is a line
    shorter. At 360 the 30-minute chip fits beside the widest time left; the opening's longer chip fits beside its time
    left until the call's last minute, when "Under 1 min left" wraps to its own line, right-aligned, never cut (the
    opening's card leads only while its call is open, so it never says "Window closed"). The card's side padding stays
    .card's 16px, which the 294px odds track is measured on; only its top and bottom tighten."""
    card = 360 - 2 * 16 - 2 * _px(".card", "padding") - 2 * 1          # a dashed card: 1px border a side
    gap = _px(".clock-top", "gap")
    assert _CLOCK_W["30-MIN CALL"] + gap + _CLOCK_W["WINDOW CLOSED"] <= card
    assert _CLOCK_W["10-MIN CALL · EVERY 5 MIN"] + gap + _CLOCK_W["10 MIN LEFT"] <= card
    assert _CLOCK_W["30-MIN CALL"] + gap + _CLOCK_W["UNDER 1 MIN LEFT"] <= card
    assert _CLOCK_W["10-MIN CALL · EVERY 5 MIN"] + gap + _CLOCK_W["UNDER 1 MIN LEFT"] > card   # so it wraps, which it may
    assert _rule(".card.dashed").endswith("padding-top:12px;padding-bottom:14px") and "padding:" not in _rule(".card.dashed")
    assert "#state:empty{display:none}" in SPX                    # no trouble, no gap under the price box
    assert "main>.exp+.card{margin-top:-4px}" in SPX and ".lab.call+.big{margin-top:4px}" in SPX
    assert "flex-wrap:wrap" in _rule(".clock-top") and "margin-left:auto" in _rule(".clock-top b")


def test_the_sheets_lines_fit_or_wrap_on_the_owners_phone():
    """The situation sheet's verdict row holds the word beside the fact's name and wraps when a name is long; every
    other line of a sheet is a paragraph that wraps inside it, never a line held to one row."""
    assert "padding:10px20px" in _rule(".sheet") and "padding:13px14px" in _rule(".sh-caveat")
    assert "flex-wrap:wrap" in _rule(".cs-verdict")
    for sel in (".cs-result p", ".sh-caveat", ".rec-l", ".cl-why-r", ".cl-rec"):
        assert "nowrap" not in _rule(sel) and "overflow" not in _rule(sel), sel
    assert "flex-wrap:wrap" in _rule(".fold") and "nowrap" not in _rule(".fold .l")   # the pre-market fold wraps under its name


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
    assert JS.count("not a trade signal") == 1                   # the pre-market card's closing line
    # the page's last line, under the two buttons on their row (Final-Home, 2026-10-10)
    assert SPX.index('<p class="note">A forecast, graded by the bars. Not advice.</p>') > SPX.index('id="infoBtn"')
    assert "flex:11100%;margin:0;text-align:center" in _rule(".note")
    foot = re.search(r'(?s)<p class="foot">(.*?)</p>', SPX).group(1)
    assert "A call is a forecast, not a trade signal" in foot and "asked and logged, never graded and never weighted" in foot
    js = ("function bar(){ return el('div'); }" + _fn("tag") + _fn("skipLine") + _fn("skipWhy") + _fn("question") +
          "console.log(JSON.stringify(question(D.q, {}, D.at).kids.map(function(k){ return k.textContent; })));")
    q = {"id": "tape_log", "status": "shadow", "ask": "Did the tape lean?", "options": ["up", "down"],
         "answer": {"pick": "up", "probabilities": {"up": 0.6, "down": 0.4}}}
    got = _run(js, {"q": q, "at": "2026-09-28T11:02:00-04:00"})
    assert got[:2] == ["Did the tape lean?", "Shadow: asked and logged, never graded, never weighted"]


def _odds():
    return _var("ODDS_ORDER") + _fn("oddsKeys") + _fn("oddsBar")


@pytest.mark.parametrize("tz", [LA, TOKYO])
def test_the_odds_read_as_one_line_under_the_bar_with_the_pick_in_bold(tz):
    """Final-Home (2026-10-10): under the bar one line names every option's odds, "Down 15 · Flat 62 · Up 24", the pick in
    bold; the bar keeps each option's share of the width, down on the left, up on the right, unsure last. A five-way
    call (an old opening card) says every option the same way, on as many lines as it needs."""
    got = _run(_odds() + "var b = oddsBar(D.o, D.pick); var d = dump(b); d.flex = b.kids[0].kids.map(function(k){ return k.style.flex; }); console.log(JSON.stringify(d));",
               {"o": {"down": 0.15, "flat": 0.62, "up": 0.24}, "pick": "flat"}, tz)
    bar, lab = got["kids"]
    assert [k["attrs"]["class"] for k in bar["kids"]] == ["o-down", "o-flat", "o-up"] and got["flex"] == ["0.15 1 0", "0.62 1 0", "0.24 1 0"]
    assert bar["attrs"] == {"class": "odds-bar", "role": "img", "aria-label": "Down 15%, Flat 62%, Up 24%"}
    assert _flat_text(lab) == "Down 15 \u00B7 Flat 62 \u00B7 Up 24" and [k["attrs"].get("class") for k in lab["kids"]] == [None, None, "pick", None, None]
    five = _run(_odds() + "console.log(JSON.stringify(dump(oddsBar(D.o, D.pick))));",
                {"o": {"down_big": 0.05, "down_small": 0.12, "flat": 0.3, "up_small": 0.38, "up_big": 0.1, "unsure": 0.05}, "pick": "up_small"}, tz)
    assert _flat_text(five["kids"][1]) == "Down big 5 \u00B7 Down small 12 \u00B7 Flat 30 \u00B7 Up small 38 \u00B7 Up big 10 \u00B7 Unsure 5"
    assert _texts(five["kids"][1], "pick") == ["Up small 38"]


# ---- the tab bar, and the old SNDK address


def test_the_switch_is_gone_and_the_old_sndk_forecast_address_forwards_to_the_forecast():
    """The redesign (2026-10-10): SPX only. The SNDK | SPX switch and the choice it kept on the phone are gone from
    the page, with the beta chip; jev.html, the SNDK forecast, keeps its name as a redirect, so an old bookmark or a
    phone that kept the address lands on /m/ with its query, and a browser without scripts reads where it went."""
    for gone in ('class="sw"', "jev.symbol", "data-sym", 'class="beta"', "SNDK", "/m/jev.html"):
        assert gone not in SPX, gone
    sndk = (M / "jev.html").read_text()
    assert re.findall(r"(?s)<script>(.*?)</script>", sndk) == ["location.replace('/m/' + location.search);"]
    assert sndk.index("<script>") < sndk.index("<body>"), "the forward runs before anything paints"
    assert "This page moved to" in sndk and 'href="/m/"' in sndk
    assert "<style>" not in sndk and "fetch(" not in sndk and "jev-spx" not in sndk


def test_the_sndk_glance_is_gone_and_its_address_answers_404():
    """Phase 5 of the redesign (2026-10-10) retired the SNDK glance, /m/index.html with page.js and glance.js: no
    tab, no link and no reader were left. Its address 404s through the static fallback like any other missing name,
    and nothing under static/m names it but jev.html's own note of where the phone went."""
    h = _Stub("/m/index.html")
    h.do_GET()
    assert h.sent_file is None and h.sent_json == ({"error": "not found", "route": "/m/index.html"}, 404)
    for name in ("index.html", "page.js", "glance.js"):
        assert not (M / name).exists(), name
    for p in sorted(M.iterdir()):
        if p.suffix in (".html", ".js", ".txt") and p.name != "jev.html":
            assert not re.search(r"index\.html|page\.js|glance\.js|the glance", p.read_text()), p.name


def test_the_two_tabs_are_forecast_and_reads_on_every_page_that_carries_them():
    """Final-Home: two tabs, Forecast (the page at /m/) and Reads, the Now tab gone; each page lights its own and links
    the other, both answer a press through press.js, and the label is the board's 11px.
    The bar is inside the header on both pages, fixed at the phone's foot and in the header's row from 1024px up
    (Final-Desktop), so a screen reader meets the two tabs with the page's name."""
    for page, lit, link in (("jev-spx.html", "FORECAST", "/m/reads.html"), ("reads.html", "READS", "/m/")):
        html = (M / page).read_text()
        nav = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', html).group(1)
        assert re.findall(r'<a class="tab" href="([^"]+)"', nav) == [link], page
        assert nav.count('<span class="tab on" data-press>') == 1 and f"<s>{lit}</s>" in nav, page
        assert "NOW" not in nav and 'href="/m/jev.html"' not in nav, page
        assert re.search(r'<a class="tab" href="[^"]+" aria-label="\w+" data-press data-haptic>', nav), page
        assert html.index('<header class="hd">') < html.index('<nav class="tabs">') < html.index("</header>"), page
        assert html.index('<script src="/m/press.js"></script>') > html.index("</nav>"), page
        assert ".tab.is-pressed{" in html and ".tab.is-pressed::before{" in html, page
        assert ".tab s{font:500 11px/1 var(--sans);letter-spacing:.1em;text-decoration:none}" in html, page
        assert re.search(r"\n\.tabs\{position:fixed;left:0;right:0;bottom:0;z-index:6;", html), page


def test_the_forecast_tab_is_drawn_alike_on_every_page():
    """Will, 2026-10-09: the Forecast tab's icon is the price so far and a dashed cone of where it may go, in place of
    JEV Beta and its scales; the same on every page that carries the bar, lit only on the forecast page."""
    icon = re.compile(r'(?s)<svg[^>]*><path d="M3 17l3.5-3.5 3 2L13 12"/>.*?</svg>\s*<s>FORECAST</s>')
    for page in ("reads.html", "jev-spx.html"):
        html = (M / page).read_text()
        nav = re.search(r'(?s)<nav class="tabs">(.*?)</nav>', html).group(1)
        assert len(icon.findall(nav)) == 1 and "BETA" not in nav and "M12 4v16" not in nav, page
        assert ".tab s em.beta" not in html, page                     # the tab's beta chip rule went with it


# ---- recovering from a failed fetch


def test_a_good_fetch_after_a_failed_one_clears_the_failure_though_the_card_is_the_same():
    """A failed fetch draws the last card with 'fetch failed'; the next good fetch of the same card draws it again
    so the pill goes, and after a station error the error text in the header goes the same way."""
    js = ("var last = null, lastOk = true, drawn = [], SUB = {dataset: {}, cls: {}, classList: {"
          "contains: function(k){ return !!SUB.cls[k]; }, add: function(k){ SUB.cls[k] = true; }, remove: function(k){ delete SUB.cls[k]; }}};"
          "function $(id){ return SUB; } function ages(){ drawn.push('ages'); } function tick(){}"
          "function draw(c, ok){ lastOk = ok; drawn.push(ok); if(ok) SUB.classList.remove('err'); }"
          "function setTimeout(){} var barsDay = null, gradesAt = 0; function priceDay(){ return null; } function pollBars(){} function pollBook(){} function pollGrades(){}" + _fn("arrived") +
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
          ".kids[0].attrs['aria-label']; })));")
    h = {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}, "jev": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}},
         "clock": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}}}
    got = _run(js, {"h": h, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00",
                    "w": ["lunch, 12:00 to 14:00", "the afternoon, 14:00 to the close", "the opening half hour, before 10:00"]}, LA)
    assert got == [f"Usual odds, last 19 days at this time of day, {span}: Flat 50%" for span in ("09:00 to 11:00", "11:00 to the close", "before 07:00")]


def _how():
    return (_var("ODDS_ORDER") + _fn("oddsKeys") + _fn("top1") + _fn("oneAnswer") + _fn("phaseSpan") + _fn("howBar") + _fn("tile") + _fn("combinedCall")
            + _fn("howChart"))


def test_the_blend_is_drawn_as_two_tiles_and_only_the_usual_odds_one_opens_the_sheet():
    """Will's layout of 09-29, restyled to Final-Home (2026-10-10): under the call sit two tiles, the usual odds (the last
    sessions at this time of day, the yardstick JEV has to beat) and, on an older card, the half-and-half its calls
    grade. The usual odds tile opens the time-of-day sheet, and only when the card carries its counts (the card's
    time_of_day), so a card without them draws a tile that is not a button."""
    js = (_how() + "var opened = []; function openTod(tod, bl, at, from){ opened.push(from.tag); }"
          "var g = howChart(D.h, {used: true, sessions: 19, phase: 'lunch', phase_words: 'lunch, 12:00 to 14:00'}, D.at, D.tod);"
          "console.log(JSON.stringify(g.kids.map(function(k){ return [k.tag, k.attrs['class'], k.textContent]; })));")
    h = {"probabilities": {"up": 0.13, "down": 0.5, "flat": 0.37}, "jev": {"probabilities": {"up": 0.05, "down": 0.85, "flat": 0.1}, "confidence": 0.78},
         "clock": {"probabilities": {"up": 0.22, "down": 0.14, "flat": 0.64}}}
    tod = {"phases": [{"phase": "lunch", "from": "12:00", "to": "14:00", "probabilities": {"up": 0.22, "down": 0.14, "flat": 0.64}, "n": 215}],
           "days": []}
    got = _run(js, {"h": h, "tod": tod, "at": "2026-09-29T13:01:00-04:00", "now": "2026-09-29T13:04:00-04:00"}, LA)
    assert got == [["button", "tile", "Usual oddsFlat 64%"], ["div", "tile graded", "Half of each \u00B7 gradedDown 50%"]]
    bare = _run(js, {"h": h, "tod": None, "at": "2026-09-29T13:01:00-04:00", "now": "2026-09-29T13:04:00-04:00"}, LA)
    assert [k[0] for k in bare] == ["div", "div"]
    assert "var tiles = howChart(a, bl, c.row_ts, a !== h ? c.time_of_day : null, chartDocked() ? voices : null);" in _fn("sumCard")


def test_the_reason_a_sum_stands_alone_gives_its_times_in_the_viewers_zone():
    """clock.py says why the time-of-day odds were left out in market words ("a 13:00 half day"); the phone redraws
    the time in the viewer's zone like every other."""
    js = (_how() + "var n = howChart(D.h, {used: false, why: D.why}, D.at).kids.slice(-1)[0]; console.log(JSON.stringify([n.textContent, n.attrs.title]));")
    got = _run(js, {"h": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}}, "at": "2026-11-27T10:02:00-05:00",
                    "now": "2026-11-27T10:05:00-05:00", "why": "a 13:00 half day; the time-of-day odds are counted on full sessions"}, LA)
    assert got == ["JEV alone today", "A 10:00 half day; the time-of-day odds are counted on full sessions"]


def test_every_question_group_has_a_heading_in_words():
    """The groups are the SPX question file's, not SNDK's viewpoints: each has a plain name, none a raw id."""
    doc = json.loads((Path(__file__).resolve().parents[3] / "pipelines" / "spx_jev_questions_and_pools" / "questions" / "spx_questions.json").read_text())
    shown = [g["id"] for g in doc["groups"] if g["id"] != "dark"]
    decl = "".join(re.search(r"\n  var %s = .*?;" % v, JS, re.S).group(0) for v in ("ORDER", "NAME"))
    names = _run(decl + "console.log(JSON.stringify([ORDER, NAME]));", {})
    assert names[0] == shown and set(names[1]) == set(shown) and "_" not in "".join(names[1].values())
    assert "NAME[vp] || words(vp)" in _fn("qsDeck")


FOLD_JS = ("var tickers = [], READ_MINUTES = [2, 32], LAST_READ_DEFAULT = '15:32';" + _fn("top1") + _fn("plusIso") + _fn("nextRead")
           + _fn("noCallWords") + _fn("jevOwn") + _fn("combinedCall") + _fn("headline") + _fn("foldLive")
           + "var f = foldLive(D.c); console.log(JSON.stringify([f.kids.map(function(k){ return k.textContent; }), tickers.length]));")


def test_the_folded_30_minute_line_counts_down_only_a_call_that_was_made():
    """The 09:32 read asks nothing, so from 09:35 to 10:02 the opening view has no 30-minute call to time."""
    c = {"row_ts": "2026-09-28T09:31:20-04:00", "hour": None, "marks": {"next_30": "2026-09-28T10:00:00-04:00"},
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(FOLD_JS, {"now": "2026-09-28T09:52:00-04:00", "c": c}, LA) == [
        ["30-min call \u00B7 06:31", "No 30-minute call on this read, next read 07:02"], 0]


def test_the_folded_30_minute_line_names_the_pick_the_card_would_lead_with():
    """One rule for the headline (headline): under the opening call the fold names the combined forecast's pick on a
    card since the cut-over (shown_source learner_pool; pool_v2 on a card from before 2026-10-11), never JEV's own beside it (Will, 2026-10-07: two answers on one
    screen confused); on a card from before it, JEV's own read, as the card led with it then."""
    blend = {"used": True, "jev_share": 0.5}
    avg = {"pick": "flat", "probabilities": {"up": 0.38, "flat": 0.47, "down": 0.15}, "blend": blend, "shown_source": "learner_pool",
           "jev": {"pick": "up", "probabilities": {"up": 0.67, "flat": 0.2, "down": 0.13}}}
    c = {"row_ts": "2026-09-28T10:02:10-04:00", "marks": {"next_30": "2026-09-28T10:32:00-04:00"}, "hour": {"average": avg}}
    assert _run(FOLD_JS, {"now": "2026-09-28T10:05:00-04:00", "c": c}, LA)[0] == ["30-min call \u00B7 07:02", "Flat 47%", "27 min left"]
    older = {**c, "hour": {"average": {k: v for k, v in avg.items() if k != "shown_source"}}}
    assert _run(FOLD_JS, {"now": "2026-09-28T10:05:00-04:00", "c": older}, LA)[0] == ["30-min call \u00B7 07:02", "Up 67%", "27 min left"]


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


_PX_IDS = "['px', 'pxLive', 'pxK', 'pxV', 'pxC', 'pxS', 'chLive', 'chV', 'chC', 'chK']"


def _price_run(data, out, tz=LA):
    """paintPx over a quote and a card, the price box's and the sheet's price line's elements hidden to start with
    (the chart's visibility is not this harness's: chartVisible says hidden, chartDocked a phone); ``out`` is the JS object printed back."""
    js = ("var IDS = {}; " + _PX_IDS + ".forEach(function(k){ IDS[k] = el('div'); IDS[k].hidden = true; });"
          "function $(id){ return IDS[id]; } function chartVisible(){ return false; } function chartDocked(){ return false; }"
          "var last = D.last, premarket = null, tape = null, spot = D.spot && {price: D.spot, at: Date.parse(D.at), fresh: D.fresh};"
          + _var("SPOT_URL") + _var("SPOT_LIVE_MS") + _var("AVG_REACH") + _fn("placeNow") + _fn("within") + _fn("money") + _fn("cardDay")
          + _fn("todaysCard") + _fn("dayClose") + _fn("inSession") + _fn("spotState") + _fn("paintPx")
          + "paintPx(); console.log(JSON.stringify(" + out + "));")
    return _run(js, data, tz)


def _price_strip(data, tz=LA):
    return _price_run(data, "{shown: !IDS.px.hidden, live: !IDS.pxLive.hidden, k: IDS.pxK.textContent,"
                            " v: IDS.pxV.textContent, c: IDS.pxC.textContent, cls: IDS.pxC.className, s: IDS.pxS.textContent}", tz)


def _price_sheet(data, tz=LA):
    """What the same paint puts on the price box's control (its aria-label) and on the price-today sheet's price line."""
    return _price_run(data, "{label: IDS.px.attrs['aria-label'], live: !IDS.chLive.hidden, v: IDS.chV.textContent,"
                            " c: IDS.chC.textContent, cls: IDS.chC.className, k: IDS.chK.textContent}", tz)


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
    assert '<button class="px" id="px" type="button" aria-controls="chartSheet" hidden>' in SPX and ".px[hidden]{display:none}" in SPX
    assert "paintPx(); paintCalls();" in _fn("draw")             # a card that lands after the quote draws the strip again


def _needle(price, card_day="2026-10-08", now="2026-10-08T14:35:00-04:00", later=None):
    """The called-at bar of the owner's 14:31 call on 2026-10-08 (7,756.79, flat edge 2.24), drawn with a quote of
    ``price`` (None: no quote yet), then, with ``later``, moved by the next quote through paintPx."""
    js = ("var IDS = {}; " + _PX_IDS + ".forEach(function(k){ IDS[k] = el('div'); });"
          "function $(id){ return IDS[id]; } function chartVisible(){ return false; } function chartDocked(){ return false; } var last = {row_ts: D.card + 'T14:31:03-04:00'};"
          "var premarket = null, tape = null, spot = D.price == null ? null : {price: D.price, at: Date.now(), fresh: true};"
          + _var("SPOT_URL") + _var("SPOT_LIVE_MS") + _var("AVG_REACH") + "".join(_fn(f) for f in ("tag", "averageWords", "indexLevel", "money", "within",
                                                                                 "cardDay", "todaysCard", "dayClose", "inSession", "spotState", "placeNow", "calledAt", "paintPx"))
          + "var bar = calledAt({price: 7756.79, edge_points: 2.24}, 30).kids[1], n = bar.kids[2];"
          "if(D.later != null){ spot = {price: D.later, at: Date.now(), fresh: true}; paintPx(); }"
          "console.log(JSON.stringify({kids: bar.kids.map(function(k){ return k.attrs['class']; }), hidden: !!n.hidden,"
          " left: n.style.left, label: bar.attrs['aria-label']}));")
    return _run(js, {"price": price, "card": card_day, "now": now, "later": later})


def test_the_called_at_bar_keeps_the_call_fixed_and_moves_a_second_needle_with_the_live_price():
    """Will's mockup of 2026-10-09: the ink needle stays on the price the call was made at; a blue one follows the live
    price across the bar, which reaches 1.25 flat edges either side of the call (the band is one), resting at an end
    when price runs past it. No words say where price is: the price box already does."""
    inside = _needle(7758.10)
    assert inside["kids"] == ["band", "at", "now"] and inside["hidden"] is False and inside["left"] == "73.4%"
    assert inside["label"].startswith("Flat from 7,754.") and inside["label"].endswith(", price now 7,758.10")
    assert _needle(7756.79)["left"] == "50.0%"                                # on the call, over the ink needle
    assert _needle(7765.0)["left"] == "100.0%" and _needle(7740.0)["left"] == "0.0%"   # past either end, it rests there
    none = _needle(None)
    assert none["hidden"] is True and ", price now" not in none["label"]       # no quote yet: only the call's needle
    assert _needle(7758.10, card_day="2026-10-07")["hidden"] is True           # another day's card never shows today's price
    moved = _needle(None, later=7755.39)                                       # the next quote moves it (paintPx)
    assert moved["hidden"] is False and moved["left"] == "25.0%"
    assert ".avg-bar .now[hidden]{display:none}" in SPX
    assert "@media (prefers-reduced-motion:reduce){.avg-bar .now{transition:none}}" in SPX   # it jumps for those who ask for less motion


def test_the_mode_chip_heads_the_leading_card_and_the_record_row_closes_it():
    """The mode chip heads the leading card, riding the clock's row beside the time left (Will's 2026-10-09 spacing pass;
    when both don't fit the time left wraps to its own line, right-aligned), and the record row closes it (Final-Home,
    2026-10-10); the top keeps only trouble (a failed fetch, an unsent read, a stale row's note), and the count of
    answers is said in the questions sheet."""
    assert "el('div', 'mode', '30-min call')));" in _fn("sumCard")
    assert "el('div', 'mode', newest.minutes + '-min call \\u00B7 every ' + TAPE_STEP_MIN + ' min')));" in _fn("laneCard")
    assert "if(chip) top.appendChild(chip); top.appendChild(left);" in _fn("clockBlock")
    assert "flex-wrap:wrap" in _rule(".clock-top") and ".clock-top .mode{align-self:center;" in SPX
    assert "'mode'" not in _fn("laneOnly") and "main.appendChild(el('div', 'mode'" not in _fn("paint")
    assert _fn("sumCard").count("appendChild(recordRow());") == 2 and "' answered'" not in _fn("paint")
    assert ".card>.mode{width:fit-content;margin-bottom:12px}" in SPX and "state answered" not in SPX and ".answered{" not in SPX


def test_an_older_cards_half_and_half_is_named_as_the_graded_tile():
    """A card from before the combined forecast shows the exact blend (shown_source blend50_exact): the chart names it
    the graded half-and-half beside the typical day."""
    js = (_how() + "var g = howChart(D.h, {used: true, sessions: 19, phase_words: 'lunch, 12:00 to 14:00'}, D.at);"
          "console.log(JSON.stringify(g.kids.map(function(k){ return k.textContent; })));")
    odds = {"up": 0.2, "down": 0.3, "flat": 0.5}
    h = {"probabilities": odds, "jev": {"probabilities": odds}, "clock": {"probabilities": odds}, "shown_source": "blend50_exact"}
    blend = _run(js, {"h": h, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00"}, LA)
    assert blend == ["Usual oddsFlat 50%", "Half of each \u00B7 gradedFlat 50%"]


# ---- before the open: the pre-market card (pipelines/spx_jev_questions_and_pools, spx_jev.premarket)

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


PRE_FNS = ("fits", "top1", "oddsKeys", "oddsBar", "tag", "skipLine", "averageWords", "jevOwn", "endPriceRow", "ageWord", "mins", "untilWords", "expiryLine",
           "cardDay", "todaysCard", "nextRead", "preLeads", "preNextRead", "preMissed", "preJevRead", "preJevDue", "preUnsent", "preState", "shapeWords",
           "sumPick", "sumSaid", "preClock", "preCall", "preRulerLine", "preStaleLine", "checksSvg", "chipFits", "storySide", "storyHead", "storySvg", "preFacts", "paintPre")
PRE_VARS = ("ODDS_ORDER", "EXPIRY_TAGS", "PRE_LATE_MIN", "OPENS", "THEN", "CHECKS_LEAD_MIN", "CHIP_W", "READ_MINUTES", "LAST_READ_DEFAULT")
# the page's #h1, #sub, #state and #main, with the classList the pre-market card toggles; no 30-minute card is on hand
PRE_DOM = """
Object.defineProperty(Node.prototype, 'classList', {get: function(){ var n = this; function has(){ return (n.attrs['class'] || '').split(' ').filter(Boolean); }
  return {remove: function(k){ n.attrs['class'] = has().filter(function(x){ return x !== k; }).join(' '); },
          toggle: function(k, on){ var h = has().filter(function(x){ return x !== k; }); if(on) h.push(k); n.attrs['class'] = h.join(' '); }}; }});
var nodes = {h1: el('span'), sub: el('div'), state: el('div'), main: el('main'), dock: el('div'), qBtn: el('button')};
function $(id){ return nodes[id]; }
var tickers = [], shownPre = false, shownLeads = false, last = null;
"""


def _pre(js, data, tz=LA):
    return _run(PRE_DOM + "".join(_fn(f) for f in PRE_FNS) + "".join(_var(v) for v in PRE_VARS) + js, data, tz)


def _page(card, now, tz=LA, ok=True):
    """paintPre on ``card`` at ``now``: the header line, the state chips, and each of main's parts as its class and flat text."""
    js = ("paintPre(D.card, D.ok);"
          "function flat(n){ return n._t + n.kids.map(flat).join(''); }"
          "console.log(JSON.stringify({sub: nodes.sub.textContent, state: nodes.state.kids.map(flat),"
          " main: nodes.main.kids.map(function(k){ return [k.attrs['class'] || k.tag, k]; }).map(function(x){ return [x[0], dump(x[1])]; }),"
          " tickers: tickers.length, shownPre: shownPre, qBtn: nodes.qBtn.hidden}));")
    return _pre(js, {"card": card, "now": now, "ok": ok}, tz)


def _card_parts(got):
    """The pre-market card's children, each as [class, flat text]."""
    card = [m[1] for m in got["main"] if "pre" in m[0].split()][0]
    return [[k["attrs"].get("class", k["tag"]), _flat_text(k)] for k in card["kids"]]


def test_the_page_reads_the_premarket_card_and_it_leads_before_the_open():
    assert "var PRE_URL = '/api/store/file?name=JEV_PREMARKET_CARD';" in JS
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
    assert got["state"] == ["1 of 2 answered"] and got["shownPre"] is True and got["tickers"] == 3 and got["qBtn"] is True
    assert [m[0] for m in got["main"]] == ["mode", "exp", "card dashed pre", "card fold"]
    assert _flat_text(got["main"][0][1]) == "before the open"
    parts = _card_parts(got)
    assert parts[0] == ["lab", "Pre-Market CallJUST NOW"]
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


def _alone(**checks):
    """The card of a call whose end-price sums got no answer (grade.average_alone): its hour carries the average-price
    call alone, no end-price odds, and its checks only their average-price grades (service.day_calls)."""
    read = et("09:28", s="04")
    card = pre_card("09:28", graded_at="2026-09-28T14:06:04+00:00",
                    calls=[{"read": read, "mark": et("10:05"), "minutes": 30, "pick": "down", "p": 0.45, "sum": "open_average_30",
                            "end_price_missing": "JEV did not answer in time", "checks": checks}])
    return {**card, "hour": {"primary": "open_30", "average": PRE_AVG, "read_at": read}}


@pytest.mark.parametrize("card, want", [
    (_alone(open_10={"integral": on_call("down", "right")}), "pre-market call 06:28Down 45%06:44 was Down, right · 07:04 not graded yet"),
    (_alone(open_10={"integral": on_call("down", "right")}, open_30={"integral": on_call("up", "wrong")}),
     "pre-market call 06:28Down 45%06:44 was Down, right · 07:04 was Up, wrong"),
    (_alone(open_10={"integral": {"graded": False, "reason": "not graded: a window with bars missing"}}),
     "pre-market call 06:28Down 45%06:44 not graded yet · 07:04 not graded yet"),
])
def test_a_call_whose_end_price_sums_got_no_answer_still_folds_with_its_checks(card, want):
    """Only the end-price request failed: the card's hour holds the average-price call and no end-price odds, and the
    fold once needed those, so the call vanished at the hand-over. It folds on the call's own odds, each check on its
    average-price grade alone."""
    got = _fold(card, et("10:30"))
    assert got["folds"] is True and _flat_text(got["fold"]) == want


def test_a_call_whose_end_price_sums_got_no_answer_is_still_said_to_be_a_forecast():
    parts = _card_parts(_page(_alone(), et("09:28", s="20")))
    assert parts[-1] == ["tag", "Hands over to the 5-minute opening reads at 06:35. A forecast, graded by the bars, not a trade signal."]


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
    assert _card_parts(got)[0] == ["lab", "Pre-Market Call4 HR 45 MIN AGO"]
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
    lane = (Path(__file__).resolve().parents[3] / "pipelines" / "spx_jev_questions_and_pools" / "spx_jev" / "premarket.py").read_text()
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


# ---- the call on the average price (pipelines/spx_jev_questions_and_pools hour.average_summary): up, flat or down over the window, no unsure;
# the end-price sum kept beside it

AVG = {"pick": "up", "probabilities": {"up": 0.55, "flat": 0.3, "down": 0.15}, "primary": "average_30", "box": "next_30",
       "minutes": 30, "flat_points": 5.25, "edge_points": 3.11}
END_30 = {"pick": "flat", "probabilities": {"up": 0.1, "flat": 0.8, "down": 0.05, "unsure": 0.05}}
CARD_STUBS = "var tickers = []; function clockBlock(read, minutes, mark, chip){ var b = el('div', 'clock'); if(chip) b.appendChild(chip); return b; }"
# a card is drawn before any quote here, so its live-price needle starts hidden (placeNow); on a phone unless D.wide
CARD_STUBS += "var spot = null; function todaysCard(){ return false; } function chartDocked(){ return !!D.wide; }" + _var("AVG_REACH") + _fn("placeNow") + _fn("money")


def _parts(node):
    return [[k["attrs"].get("class", k["tag"]), _flat_text(k)] for k in node["kids"]]


SUM_CARD_FNS = ("top1", "oneAnswer", "phaseSpan", "howBar", "tile", "howChart", "tag", "skipLine", "plusIso", "averageWords", "jevOwn", "combinedCall", "headline",
                "labRow", "indexLevel", "calledAt", "threeWay", "voiceName", "voicesOf", "bySay", "sayWords", "voicesButton", "recordRow",
                "signedPts", "directionWords", "paintDirection", "directionRow", "sumCard")


def _sum_card(c, tz=LA, whole=False, wide=False):
    js = (CARD_STUBS + _odds() + "".join(_fn(f) for f in SUM_CARD_FNS) + _var("VOICE_WORDS") + _var("direction")
          + "var main = el('main'); sumCard(D.c, main); console.log(JSON.stringify(dump(main.kids[0])));")
    card = _run(js, {"c": c, "now": "2026-09-28T11:10:00-04:00", "wide": wide}, tz)
    return card if whole else _parts(card)


def sentence_(s):
    return s[:1].upper() + s[1:]


def test_a_blended_call_leads_with_jevs_own_read_and_keeps_the_half_and_half_in_a_tile():
    """Will, 2026-09-30: the main screen shows JEV's read. On a blended call the big number and the odds are JEV's own,
    with how sure it was; the half-and-half the calls in play grade and the last sessions' odds sit in the tiles under it."""
    blend = {"used": True, "jev_share": 0.5, "phase_words": "lunch, 12:00 to 14:00", "sessions": 19}
    avg = {**AVG, "pick": "flat", "probabilities": {"up": 0.3, "flat": 0.45, "down": 0.25}, "blend": blend,
           "jev": {"probabilities": {"up": 0.1, "flat": 0.2, "down": 0.7}, "confidence": 0.78},
           "clock": {"probabilities": {"up": 0.2, "flat": 0.7, "down": 0.1}}}
    c = {"row_ts": "2026-09-28T12:32:10-04:00", "marks": {"next_30": "2026-09-28T13:02:00-04:00"}, "calls": [],
         "hour": {**END_30, "used": 12, "by": {"next_30": END_30}, "average": avg}}
    card = _sum_card(c, whole=True)
    assert _texts(card, "big") == ["Down 70%"] and _texts(card, "lab call") == ["Today’s call · JEV78% sure"]
    assert _cls(card, "lab call")[0]["kids"][1]["attrs"]["class"] == "r"                 # a note, not an age: the ages never move it
    assert _texts(card, "tod")[0].startswith("Usual odds") and "Half of each · gradedFlat 45%" in _texts(card, "tod")[0]


def test_the_combined_forecast_leads_the_card_and_jevs_own_read_sits_in_a_tile():
    """Will, 2026-10-07: once the combined forecast is the call (shown_source learner_pool), the headline is the call the calls in
    play grade, labelled combined; JEV's own read moves to a "JEV alone" tile beside the usual odds, and the 60-minute line
    is the combined forecast's too. (The 13:29 ET read of 10-07 showed JEV's Up 67% big while the graded call was Flat 47%.)"""
    blend = {"used": True, "jev_share": 0.5, "phase_words": "afternoon, 13:00 to 14:00", "sessions": 20}
    avg = {**AVG, "pick": "flat", "probabilities": {"up": 0.38, "flat": 0.47, "down": 0.15}, "blend": blend, "shown_source": "learner_pool",
           "jev": {"probabilities": {"up": 0.67, "flat": 0.2, "down": 0.13}, "confidence": 0.6},
           "clock": {"probabilities": {"up": 0.22, "flat": 0.61, "down": 0.17}}}
    n60 = {"pick": "flat", "probabilities": {"up": 0.2, "flat": 0.5, "down": 0.3}, "blended": True,
           "blend50_exact": {"up": 0.5, "flat": 0.3, "down": 0.2}, "jev": {"pick": "up", "probabilities": {"up": 0.9, "flat": 0.05, "down": 0.05}}}
    c = {"row_ts": "2026-09-28T13:29:10-04:00", "marks": {"next_30": "2026-09-28T13:59:00-04:00", "next_60": "2026-09-28T14:29:00-04:00"},
         "calls": [], "hour": {**END_30, "used": 2, "shown_source": "learner_pool", "by": {"next_30": END_30, "next_60": n60}, "average": avg}}
    card = _sum_card(c, whole=True)
    assert _texts(card, "big") == ["Flat 47%"] and _texts(card, "lab call") == ["Today’s call · combined"]
    assert _texts(card, "tod")[0].startswith("Usual oddsFlat 61%") and "JEV aloneUp 67%" in _texts(card, "tod")[0] and "graded" not in _texts(card, "tod")[0]
    assert "End price" not in json.dumps(card)                                # the end-price sums left the card: the reads carry them


def _voice(name, plain, down, flat, up, share):
    return {"name": name, "plain_name": plain, "probabilities": {"up": up, "flat": flat, "down": down}, "share": share}


# the 15:29 ET card of 2026-10-07 (state/spx_jev/latest.json) as the service wrote it then (shown_source pool_v2, the learner pool's
# name before 2026-10-11, which the page still reads): the call's voices beside it
# (live_call.take_over, listed in the order written, not by share) and each end-price sum's flat band in points
REAL_AVG = {"pick": "flat", "probabilities": {"up": 0.1653, "flat": 0.4803, "down": 0.3545}, "primary": "average_30", "box": "next_30",
            "minutes": 30, "flat_points": 3.62, "edge_points": 2.14, "price": 7798.0, "shown_source": "pool_v2",
            "blend": {"used": True, "jev_share": 0.5, "phase_words": "close, 15:00 to 16:00", "sessions": 20},
            "jev": {"probabilities": {"up": 0.13, "flat": 0.15, "down": 0.72}}, "clock": {"probabilities": {"up": 0.17, "flat": 0.65, "down": 0.17}},
            "voices": [_voice("historical_odds", "Historical odds", 0.172, 0.654, 0.173, 0.224),
                       _voice("blend_50_50", "50/50 blend", 0.446, 0.402, 0.152, 0.604),
                       _voice("jev_corrected", "Corrected JEV", 0.144, 0.69, 0.167, 0.045),
                       _voice("matcher", "Matcher", 0.134, 0.712, 0.153, 0.079),
                       _voice("jev_own", "JEV's own call", 0.72, 0.15, 0.13, 0.021),
                       _voice("additive_scorer", "Additive scorer", 0.109, 0.781, 0.11, 0.026)]}
REAL_30 = {"pick": "flat", "probabilities": {"up": 0.1419, "down": 0.2058, "flat": 0.5472, "unsure": 0.105}, "blended": True, "flat_points": 3.62}
REAL_60 = {"pick": "flat", "probabilities": {"up": 0.1543, "flat": 0.6974, "down": 0.1483}, "blended": True, "flat_points": 5.69,
           "shown_source": "pool_v2", "blend50_exact": {"up": 0.1288, "down": 0.2043, "flat": 0.527, "unsure": 0.14}}
REAL = {"row_ts": "2026-10-07T15:29:17.137096-04:00", "marks": {"next_30": "2026-10-07T15:59:00-04:00", "next_60": None}, "calls": [],
        "hour": {**REAL_30, "primary": "next_30", "shown_source": "pool_v2", "by": {"next_30": REAL_30, "next_60": REAL_60}, "average": REAL_AVG}}


def _voices_sheet(a, tz=LA):
    """openVoices over a call's voices: the sheet's title and its body as [class, flat text] per part."""
    js = ("var nodes = {csTitle: el('div'), csBody: el('div')}; function $(id){ return nodes[id]; } var opened = 0, MiraiSheet = {open: function(){ opened++; }};"
          + _var("ODDS_ORDER") + _var("VOICE_WORDS") + "".join(_fn(f) for f in ("oddsKeys", "howBar", "tag", "labRow", "threeWay", "voiceName", "voicesOf", "bySay", "sayWords", "openVoices"))
          + "openVoices(voicesOf(D.a), el('button')); console.log(JSON.stringify({title: nodes.csTitle.textContent, opened: opened, body: dump(nodes.csBody)}));")
    return _run(js, {"a": a}, tz)


def test_the_combined_call_says_its_price_and_its_flat_band_and_keeps_its_voices_behind_a_control():
    """The owner's mockup of 2026-10-07 and Final-Home (2026-10-10): under the odds the price the call was made at with
    its flat edge as a pill and the band's two prices; beside the call's label a control that counts the voices and
    opens them as a sheet, every voice most say first with its odds, and one line on who has the most say. The
    end-price sums are no longer on the card: the reads carry each call's numbers."""
    card = _sum_card(REAL, whole=True)
    parts = _parts(card)
    at = _cls(card, "avg-at")[0]
    assert [_flat_text(k) for k in at["kids"]] == ["Called at", "7,798.00", "\u00B1 2.1 pts"] and at["kids"][1]["tag"] == "b"
    assert _texts(card, "pill") == ["\u00B1 2.1 pts"]
    assert [_flat_text(k) for k in _cls(card, "avg-lab")[0]["kids"]] == ["7,795.9", "flat on the 30-min average", "7,800.1"]
    assert [k["attrs"]["class"] for k in _cls(card, "avg-bar")[0]["kids"]] == ["band", "at", "now"]
    assert not any(v.startswith("Average price") for _, v in parts)          # the line it replaces
    top = _cls(card, "call-top")[0]
    assert [k["attrs"].get("class") for k in top["kids"]] == [None, "voices"] and _texts(top, "lab call") == ["Today\u2019s call \u00B7 combined"]
    btn = top["kids"][1]
    assert btn["tag"] == "button" and btn["attrs"]["aria-controls"] == "jevSheet"
    assert btn["attrs"]["aria-label"] == "What went into it: 6 voices, 50/50 blend has 60% of the say. Open the list"
    # the control is built with a tile's parts (the board's third tile from 1024px up); the phone's stylesheet shows the
    # count alone, as the label row's words, so on a phone it reads "6 voices" and nothing of the tile's name or sub
    assert [[k["attrs"]["class"], _flat_text(k)] for k in btn["kids"]] == [["tile-k", "What went into it"], ["tile-v", "6 voices"], ["tile-s", "50/50 blend has 60% of the say"]]
    assert ".voices .tile-k,.voices .tile-s{display:none}" in SPX and _rule(".voices .tile-v") == "margin:0;font:inherit;color:inherit"
    assert "JEV aloneDown 72%" in _texts(card, "tod")[0]                     # the tile stays beside the usual odds
    assert [k["attrs"]["class"] for k in _cls(card, "tod")[0]["kids"]] == ["tile", "tile"]
    for gone in ("mix", "ep", "ep-r", "row60", "src", "inplay"):
        assert not _cls(card, gone), gone
    assert "End price" not in json.dumps(card)
    # from 1024px up the one control leaves the label row and stands as the third tile (Final-Desktop): the same button,
    # with the same words and sheet, placed by the builder; its tile face is the stylesheet's, with the tile's chevron
    wide = _sum_card(REAL, whole=True, wide=True)
    assert [k["attrs"].get("class") for k in _cls(wide, "call-top")[0]["kids"]] == [None]
    tiles = _cls(wide, "tod")[0]["kids"]
    assert [k["attrs"]["class"] for k in tiles] == ["tile", "tile", "voices"] and tiles[2]["attrs"]["aria-label"] == btn["attrs"]["aria-label"]
    assert [_flat_text(k) for k in tiles] == ["Usual oddsFlat 65%", "JEV aloneDown 72%", "What went into it6 voices50/50 blend has 60% of the say"]
    # a combined call whose blend tiles cannot draw (the blend not used) has no tile row to stand the control in: it
    # keeps the label row at the wide width too, never lost
    unused = _sum_card({**REAL, "hour": {**REAL["hour"], "average": {**REAL_AVG, "blend": {**REAL_AVG["blend"], "used": False}}}}, whole=True, wide=True)
    assert [k["attrs"].get("class") for k in _cls(unused, "call-top")[0]["kids"]] == [None, "voices"] and not _cls(unused, "tod") and _cls(unused, "how")
    assert re.search(r"\n\.px-k::after,button\.tile \.tile-k::after,\.voices::after,\.voices \.tile-k::after,[^{]*\{\n  content:", SPX)
    assert "button.tile .tile-k::after,.voices .tile-k::after{margin-left:auto}" in SPX
    sheet = _voices_sheet(REAL_AVG)
    assert sheet["title"] == "What went into it \u00B7 6 voices" and sheet["opened"] == 1
    body = sheet["body"]
    assert _flat_text(body["kids"][0]) == "VoiceDown \u00B7 Flat \u00B7 Up" and body["kids"][0]["kids"][1]["attrs"]["class"] == "r"   # a note, not an age
    assert _texts(body, "n") == ["50/50 blend", "Usual odds", "Matcher", "Corrected JEV", "Additive scorer", "JEV alone"]
    assert _texts(body, "v") == ["45 \u00B7 40 \u00B7 15", "17 \u00B7 65 \u00B7 17", "13 \u00B7 71 \u00B7 15", "14 \u00B7 69 \u00B7 17",
                                 "11 \u00B7 78 \u00B7 11", "72 \u00B7 15 \u00B7 13"]
    assert [k["attrs"]["class"] for k in _cls(body, "how-bar")[0]["kids"]] == ["o-down", "o-flat", "o-up"]
    assert _flat_text(body["kids"][-1]) == "Listed by how much say each has: 50/50 blend has 60% of the say."


def test_a_combined_call_without_the_new_fields_keeps_its_edge_line_and_has_no_voices_or_band():
    avg = {k: v for k, v in REAL_AVG.items() if k not in ("price", "voices")}
    card = _sum_card({**REAL, "hour": {**REAL["hour"], "average": avg}}, whole=True)
    assert ["tag", "Average price \u00B7 flat \u00B12.1 pts"] in _parts(card) and not _cls(card, "voices") and not _cls(card, "avg-at")
    assert [k["attrs"].get("class") for k in _cls(card, "call-top")[0]["kids"]] == [None]


# Chrome at 360 with the shipped face, measured on the live page on 2026-10-10 (Final-Home): the record row's label (11px
# capitals spaced .12em) and its count (11px bold), its line (12.5px), the calls row's label and score (12.5px bold),
# Claude's numbers column (12.5px at 600, the pick at 800), the situation's value captions (11.5px), the two buttons'
# words (13.5px bold), and the strip's label row (11px capitals spaced .12em)
_REC_W = {"RECORD · 30 MIN": 112.75, "300 of 300 reads": 92.2, "56 of 107 right · usual odds 62 · a bit worse": 242.88,
          "TODAY’S CALLS": 102.98, "10 of 11 right · 1 open": 120.22, "14 · 58 · 28": 61.33,
          "Most Calls Held · 187 pts above": 165.06, "Busier than 13 of 19 days": 128.5, "by 16 pts": 46.53,
          "Questions · 12 asked": 133.28, "How to read": 79.77,
          "CLAUDE’S READ · 11:30": 150.75, "NOT IN THE CALL": 109.19, "agrees with the call · a move is more likely than the historical odds": 364.08,
          "Nearest Big Strike": 126.63, "None Nearby": 82.25, "Close By": 55.36}


def test_the_new_rows_fit_the_owners_360px_phone():
    """At 360 the dashed card holds 294px and a sheet 320: a voice row's name and figures leave its bar room, the
    price line's pill sits beside the price, the record row's words and the calls row's score sit beside their labels
    on one line each, and Claude's rows leave their bars room in the strip (the board's 44 and 84px columns) and in the
    Why sheet (78 and 110px)."""
    inner = 360 - 2 * 16 - 2 * _px(".card", "padding") - 2
    sheet = 360 - 2 * 20
    cols = re.search(r"grid-template-columns:(\d+)pxminmax\(0,1fr\)(\d+)px;gap:(\d+)px", _rule(".mix-r"))
    assert sheet - int(cols.group(1)) - int(cols.group(2)) - 2 * int(cols.group(3)) >= 110
    assert "text-overflow:ellipsis" in _rule(".mix-r .n")
    assert "margin-left:auto" in _rule(".pill") and "white-space:nowrap" in _rule(".pill")
    assert _REC_W["RECORD · 30 MIN"] + 2 * 8 + _REC_W["300 of 300 reads"] + 6 <= inner
    assert _REC_W["56 of 107 right · usual odds 62 · a bit worse"] <= inner
    assert _REC_W["TODAY’S CALLS"] + 2 * 8 + _REC_W["10 of 11 right · 1 open"] + 6 <= inner
    strip = 360 - 2 * 16 - 2 * 14 - 2
    for sel, room in ((".cl-grid", strip), (".cl-vs", sheet)):
        cols = re.search(r"grid-template-columns:(\d+)pxminmax\(0,1fr\)(\d+)px;gap:\d+px(\d+)px", _rule(sel))
        assert room - int(cols.group(1)) - int(cols.group(2)) - 2 * int(cols.group(3)) >= 110, sel
    assert "white-space:nowrap" in _rule(".cl-n") and _REC_W["14 · 58 · 28"] + 4 <= 84      # one number at 800 adds under 4px
    # the value column's verdict keeps one line and its caption wraps under it when long ("Busier than 13 of 19 days")
    assert "grid-template-columns:minmax(0,1fr)60px100px" in _rule(".sit-row") and _REC_W["by 16 pts"] <= 100 < _REC_W["Most Calls Held · 187 pts above"]
    assert "white-space:nowrap" in _rule(".sit-v") and "white-space:normal" in _rule(".sit-v span")
    assert _REC_W["CLAUDE’S READ · 11:30"] + 8 + _REC_W["NOT IN THE CALL"] <= strip
    assert _REC_W["agrees with the call · a move is more likely than the historical odds"] <= 2 * strip   # two lines under the pick at worst


def test_the_30_minute_card_leads_with_the_average_price_call():
    """The big number, the odds and how the call was made are the average-price sum's, with what it is measured on and
    its edge in points, and the blend note is the average's own; the end-price sums are not on the card."""
    why = "only 4 prior sessions with enough reads graded on the average price; its time-of-day odds need 10"
    hour = {**END_30, "used": 12, "left_out": 0, "missing": 0, "blend": {"used": False, "why": "the end-price clock's reason"},
            "by": {"next_30": END_30, "next_60": {"pick": "up", "probabilities": {"up": 0.5, "flat": 0.3, "down": 0.1, "unsure": 0.1}}},
            "average": {**AVG, "blend": {"used": False, "why": why}}}
    c = {"row_ts": "2026-09-28T11:02:10-04:00", "hour": hour, "marks": {"next_30": "2026-09-28T11:32:00-04:00", "next_60": "2026-09-28T12:02:00-04:00"},
         "calls": [call("11:02", "11:32", "up", 0.55)]}
    card = _sum_card(c, whole=True)
    parts = _parts(card)
    assert parts[0] == ["clock", "30-min call"]   # the mode chip heads the card, on the clock's row
    assert parts[1][0] == "call-top" and _texts(card, "big") == ["Up 55%"]
    assert ["tag", "Average price \u00B7 flat \u00B13.1 pts"] in parts and "End price" not in json.dumps(card)
    how = next(k for k in card["kids"] if k["attrs"].get("class") == "how")
    note = how["kids"][-1]
    assert _flat_text(how).startswith("JEVUp 55%") and _flat_text(note) == "JEV alone today"
    assert note["attrs"]["title"] == sentence_(why) and "end-price clock" not in json.dumps(how)
    assert parts[-1][0] == "rec"                   # the record row closes the card
    # a card from before the average-price sum leads with the end-price sum
    old = _sum_card({**c, "hour": {k: v for k, v in hour.items() if k != "average"}}, whole=True)
    assert _texts(old, "big") == ["Flat 80%"] and not any(v.startswith("Average price") for _, v in _parts(old))


def test_the_last_reads_clock_looks_as_far_ahead_as_its_average_runs():
    """The 15:32 read's average runs 28 minutes, to the close, and the card said "Looks 30 min ahead" above "over the next
    28 minutes": the clock takes the call's own minutes (hour.average.minutes); a card with no average-price call keeps
    its calls' horizon."""
    js = (CARD_STUBS + "function clockBlock(read, minutes, mark){ return el('div', 'clock', 'Looks ' + minutes + ' min ahead, graded ' + mark); }"
          + _odds() + "".join(_fn(f) for f in SUM_CARD_FNS) + _var("VOICE_WORDS") + _var("direction")
          + "console.log(JSON.stringify(D.c.map(function(c){ var main = el('main'); sumCard(c, main); return dump(main.kids[0]); })));")
    hour = {**END_30, "used": 12, "by": {"next_30": END_30}, "average": {**AVG, "minutes": 28}}
    c = {"row_ts": "2026-09-28T15:32:10-04:00", "hour": hour, "marks": {"next_30": "2026-09-28T16:00:00-04:00"},
         "calls": [call("15:32", "16:00", "up", 0.55) | {"minutes": 30}]}
    old = {**c, "hour": {k: v for k, v in hour.items() if k != "average"}}
    now, before = [_parts(d) for d in _run(js, {"c": [c, old], "now": "2026-09-28T15:40:00-04:00"})]
    assert now[0] == ["clock", "Looks 28 min ahead, graded 2026-09-28T16:00:00-04:00"]
    assert ["tag", "Average price \u00B7 flat \u00B13.1 pts"] in now
    assert before[0] == ["clock", "Looks 30 min ahead, graded 2026-09-28T16:00:00-04:00"]


# the opening card's own parts, lifted with it
LANE_FNS = ("top1", "tag", "skipLine", "jevOwn", "endPriceRow", "indexLevel", "averageWords", "labRow", "calledAt", "stretchRow", "callState", "callsRow", "laneCard")
LANE_VARS = ("TAPE_STEP_MIN", "STRETCH_MOVE", "STRETCH_BUSY", "STRETCH_THIRD")
# the builder's own sentences (labels/range_size._since_last_read): the day's first read, from the owner's phone on
# 09-29, and a later read's, from the same day's 10:30 card
FIRST_RANGE = ("the range since the open, 5 minutes ago, is 18.1 points, 1.78 tape units (one is 10.2 points), in the middle third "
               "for this minute, higher than 10 of 19 prior sessions, each in its own morning ruler")
LATER = {"move_since_read": "since the last read, 5 minutes ago, price ended 1.8 points above it, 0.25 of a tape unit (7.2 points), "
                            "within the 0.42 cut, so it held",
         "range_since_read": "the range since the last read, 5 minutes ago, is 7.7 points, 1.08 tape units (one is 7.2 points), in the top "
                             "third for this minute, higher than 14 of 19 prior sessions, each in its own morning ruler"}
# the owner's 10:30 card of 2026-10-08, the lane's last read that day (state/spx_jev/lanes/tape/latest.json): the average
# call flat on 7,792.23 with a 1.17-point edge, the end price unsure, price up 2.4 points since 10:25 on a busy range
TAPE_READS = [f"2026-10-08T{t}:00-04:00" for t in ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10",
                                                    "10:15", "10:20", "10:25", "10:30")]
OCT8 = {"lane": "tape", "row_ts": "2026-10-08T10:30:00-04:00",
        "hour": {"pick": "unsure", "probabilities": {"down_big": 0.0, "down_small": 0.01, "flat": 0.16, "up_small": 0.36, "up_big": 0.06, "unsure": 0.41},
                 "views": {"direction": {"probabilities": {"up": 0.42, "flat": 0.16, "down": 0.01, "unsure": 0.41}},
                           "size": {"probabilities": {"big": 0.06, "small": 0.53, "unsure": 0.41}}},
                 "average": {"pick": "flat", "probabilities": {"up": 0.48, "down": 0.02, "flat": 0.5}, "primary": "average_10", "box": "next_10",
                             "minutes": 10, "flat_points": 1.88, "edge_points": 1.17, "price": 7792.23}},
        "calls": [{"read": "2026-10-08T10:30:00-04:00", "mark": "2026-10-08T10:40:00-04:00", "minutes": 10, "pick": "flat", "p": 0.5}],
        "band": {"flat_points": 1.88, "big_points": 3.98}, "ruler": {"unit_points": 4.47, "source": "tape"},
        "stretch": {"move_since_read": "since the last read, 5 minutes ago, price ended 2.4 points above it, 0.54 of a tape unit (4.5 points), "
                                       "more than the 0.42 cut and no more than 0.89, so it rose small",
                    "range_since_read": "the range since the last read, 5 minutes ago, is 5.8 points, 1.30 tape units (one is 4.5 points), in the "
                                        "top third for this minute, higher than 16 of 19 prior sessions, each in its own morning ruler"},
        "schedule": {"reads": TAPE_READS, "looks_ahead_min": 10}, "closed_out_at": "2026-10-08T14:42:14+00:00"}


def _lane(ts, now="2026-09-28T10:42:00-04:00"):
    js = (CARD_STUBS + _odds() + "".join(_var(v) for v in LANE_VARS) + "".join(_fn(f) for f in LANE_FNS)
          + "console.log(JSON.stringify(D.t.map(function(t){ return dump(laneCard(t, true)); })));")
    return _run(js, {"t": ts, "now": now})


def _cls(node, cls):
    """Every node under ``node`` with class ``cls``, in order."""
    here = [node] if node["attrs"].get("class") == cls else []
    return here + [n for k in node["kids"] for n in _cls(k, cls)]


def _texts(node, cls):
    return [_flat_text(n) for n in _cls(node, cls)]


def test_the_opening_card_is_the_30_minute_cards_shape():
    """Will, 2026-10-08: the 5-minute card reads as the 30-minute one does, lean. Its call and odds under a call label, the
    price it was called at with the flat band on the 10-minute average, the morning's calls as squares (the same row the
    dock draws, standing alone since the reads list the 30-minute calls only), then one row for the end price and one for
    the stretch since the last read, and the hand-back last. Nothing else: no sentence of where SPX should average, no
    flat range line, no direction or size odds row, no move-size scale, no typical-swing note."""
    card = _lane([OCT8], "2026-10-08T10:42:00-04:00")[0]
    parts = _parts(card)
    assert [k for k, _ in parts] == ["clock", "lab call", "big", "div", "div", "calls", "row60", "row60", "tag"]
    assert [v for _, v in parts[:3]] == ["10-min call \u00B7 every 5 min", "Opening call", "Flat 50%"]   # the chip on the clock's row
    assert parts[5] == ["calls", "Opening callsnone graded yet \u00B7 1 open"] and card["kids"][5]["tag"] == "div"
    assert [v for _, v in parts[6:]] == ["End priceUnsure 41%", "Last 5 minUp 2.4 pts \u00B7 busy",
                                         "Last 5-min check; every 30 min again from 07:40"]
    assert [k["attrs"]["class"] for k in card["kids"][3]["kids"]] == ["odds-bar", "odds-lab"]
    assert [k["attrs"]["class"] for k in card["kids"][4]["kids"]] == ["avg-at", "avg-bar", "avg-lab"]
    at = _cls(card, "avg-at")[0]
    assert [_flat_text(k) for k in at["kids"]] == ["Called at", "7,792.23", "\u00B1 1.2 pts"]
    assert [_flat_text(k) for k in _cls(card, "avg-lab")[0]["kids"]] == ["7,791.1", "flat on the 10-min average", "7,793.4"]
    flat = json.dumps(card)
    for gone in ("SPX should average", "Flat: ", "Big move", "Move size", "Typical 5-min swing", "mv-bar", "\"range\"", "\"odds\""):
        assert gone not in flat, gone
    # a point is said once, in the info sheet, in words (no arrow glyph); neither the card nor the footer sizes the call in tape units
    assert "A point is one step of the S&amp;P 500 index, e.g. 7,817 to 7,818." in SPX
    foot = re.search(r'(?s)<p class="foot">(.*?)</p>', SPX).group(1)
    assert "sized in tape units" not in foot and "tape unit" not in _fn("laneCard")
    assert "the average price over the 30 minutes after the first settled price" in foot and "where price stands 10 and 30" not in foot


def test_an_opening_call_on_the_end_price_alone_or_with_no_sum_still_says_its_call():
    """A card from before the average-price call (Monday 09-28's) or a read whose average went unanswered calls the end
    price: its pick and odds lead, with no price it was called at and no end-price row beside it, and an unanswered
    average says so. A read with no sum keeps the newest call it has, said to be that read's."""
    five = {"down_big": 0.05, "down_small": 0.1, "flat": 0.3, "up_small": 0.4, "up_big": 0.1, "unsure": 0.05}
    base = {"lane": "tape", "row_ts": "2026-09-28T10:40:00-04:00", "calls": [call("10:40", "10:50", "up_small", 0.4)], "stretch": LATER}
    old = {**base, "hour": {"pick": "up_small", "probabilities": five}}
    lost = {**base, "hour": {"pick": "up_small", "probabilities": five, "average": {"error": "jev timed out"}}}
    none = {**base, "row_ts": "2026-09-28T10:45:00-04:00", "hour": {"error": "jev timed out"}}
    got = [_parts(c) for c in _lane([old, lost, none])]
    for parts in got[:2]:
        assert parts[1:3] == [["lab call", "Opening call"], ["big", "Up small 40%"]]
        assert not any(k in ("avg-at", "tag") for k, _ in parts) and ["row60", "Last 5 minUp 1.8 pts \u00B7 busy"] in parts
        assert not any(v.startswith("End price") for _, v in parts)
    assert ["skip", "No average answer; end-price call"] in got[1] and not any(k == "skip" for k, _ in got[0])
    assert got[2][1:4] == [["lab call", "Opening call"], ["big", "Up small 40%"],
                           ["skip", "No sum on the 07:45 read: jev timed out; this is the 07:40 call"]]
    assert not any(v.startswith("End price") for _, v in got[2])


@pytest.mark.parametrize("move, rng, said", [
    (LATER["move_since_read"], LATER["range_since_read"], "Last 5 minUp 1.8 pts \u00B7 busy"),
    ("since the last read, 5 minutes ago, price ended level with it, 0 of a tape unit (7.2 points), within the 0.42 cut, so it held",
     None, "Last 5 minNo change"),
    ("since the last read, 5 minutes ago, price ended 4.1 points below it, 0.57 of a tape unit (7.2 points), more than the 0.42 cut "
     "and no more than 0.89, so it fell small",
     "the range since the last read, 5 minutes ago, is 2.1 points, 0.29 of a tape unit (7.2 points), in the bottom third for this "
     "minute, higher than 0 of 19 prior sessions, each in its own morning ruler (ruler estimated)", "Last 5 minDown 4.1 pts \u00B7 quiet"),
    ("since the last read, 10 minutes ago, price ended 9.9 points above it, 1.38 tape units (one is 7.2 points), more than the 0.89 "
     "cut, so it rose big",
     "the range since the last read, 10 minutes ago, is 12.0 points, 1.67 tape units (one is 7.2 points), in the middle third for "
     "this minute, higher than 10 of 19 prior sessions, each in its own morning ruler", "Last 10 minUp 9.9 pts \u00B7 normal"),
])
def test_the_last_5_minutes_row_says_the_move_and_how_busy_it_was(move, rng, said):
    """The builder's sentences come down to one row: the move since the last read in points, and the range's third for
    this minute against the prior sessions as quiet, normal or busy. The row names the minutes the builder measured, so
    a read after a missed one says its 10."""
    stretch = {"move_since_read": move, **({"range_since_read": rng} if rng else {})}
    t = {"lane": "tape", "row_ts": "2026-09-28T10:30:00-04:00", "hour": {}, "calls": [call("10:30", "10:40", "flat", 0.5)], "stretch": stretch}
    assert _texts(_lane([t])[0], "row60") == [said]


def test_the_days_first_read_and_a_move_the_page_cannot_read_have_no_last_5_minutes_row():
    """The day's first read has nothing before it to have moved from; a move the builder left out for another reason, or
    wrote in a shape the page does not know, is left out too, never said whole or as the builder's reason."""
    base = {"lane": "tape", "row_ts": "2026-09-28T09:35:00-04:00", "hour": {}, "calls": [call("09:35", "09:45", "flat", 0.89)]}
    first = {**base, "stretch": {"range_since_read": FIRST_RANGE}, "omitted": {"tape.move_since_read": "the day's first read: no earlier read today to measure from"}}
    gap = {**base, "row_ts": "2026-09-28T10:30:00-04:00", "stretch": {}, "omitted": {"tape.move_since_read": "no finished bar at the last read"}}
    odd = {**base, "stretch": {"move_since_read": "price moved a little", "range_since_read": LATER["range_since_read"]}}
    for card in _lane([first, gap, odd], "2026-09-28T09:40:00-04:00"):
        assert not _cls(card, "row60") and not _cls(card, "tag")
        assert "first read" not in json.dumps(card) and "finished bar" not in json.dumps(card) and "a little" not in json.dumps(card)


# the opening card's widest lines in the shipped face (Plus Jakarta Sans, its advance widths at the line's weight, size
# and spacing): a stretch row after a missed read with a two-figure move, the mode chip with its padding and border
_LANE_W = {"LAST 10 MIN": 76.16, "Down 12.3 pts \u00B7 normal": 152.89, "END PRICE": 67.22, "Down small 100%": 120.55,
           "10-MIN CALL \u00B7 EVERY 5 MIN": 192.70, "flat on the 10-min average": 141.08, "7,791.1": 34.28}


def test_the_opening_cards_rows_fit_the_owners_360px_phone():
    """At 360 the dashed card holds 294px: each row's name and words sit on one line, and wrap under it if a figure
    ever grows; the flat band's prices and its words fit their grid."""
    inner = 360 - 2 * 16 - 2 * _px(".card", "padding") - 2
    assert "flex-wrap:wrap" in _rule(".row60")
    between = float(re.search(r"gap:[\d.]+px([\d.]+)px", _rule(".row60")).group(1))   # the column gap, after the row gap
    for k, v in (("LAST 10 MIN", "Down 12.3 pts \u00B7 normal"), ("END PRICE", "Down small 100%")):
        assert _LANE_W[k] + between + _LANE_W[v] <= inner, f"{k} {v} runs past the card at 360"
    assert _LANE_W["10-MIN CALL \u00B7 EVERY 5 MIN"] <= inner
    assert 2 * _LANE_W["7,791.1"] + 2 * _px(".avg-lab", "gap") + _LANE_W["flat on the 10-min average"] <= inner


def test_the_opening_cards_last_line_says_when_it_hands_back_and_what_a_call_is():
    reads = [f"2026-09-28T{t}:00-04:00" for t in ("09:35", "09:40", "10:25", "10:30")]
    base = {"lane": "tape", "hour": {}, "stretch": {}, "schedule": {"reads": reads, "looks_ahead_min": 10}}
    mid = {**base, "row_ts": reads[1], "calls": [call("09:40", "09:50", "flat", 0.6)]}
    last = {**base, "row_ts": reads[-1], "calls": [call("10:30", "10:40", "flat", 0.6)]}
    bare = {**mid, "schedule": None}
    assert [_parts(c)[-1] for c in _lane([mid, last, bare])] == [
        ["tag", "5-min checks until 07:40"],
        ["tag", "Last 5-min check; every 30 min again from 07:40"],
        ["calls", "Opening callsnone graded yet \u00B7 1 open"]]


def test_the_pre_market_call_and_its_fold_are_the_average_price_sums():
    avg = {**AVG, "pick": "down", "probabilities": {"up": 0.25, "flat": 0.3, "down": 0.45}, "primary": "open_average_30", "box": "open_30",
           "flat_points": 4.38, "edge_points": 2.59}
    card = pre_card("09:28")
    card = {**card, "hour": {**card["hour"], "average": avg}}
    got = _fold(card, et("09:50"))
    assert _flat_text(got["fold"]) == "pre-market call 06:28Down 45%Checked 06:44 and 07:04"
    box = _parts(_pre("var box = el('div'); preCall(D.card, box); console.log(JSON.stringify(dump(box)));", {"card": card, "now": et("09:30")}))
    assert box[0] == ["big", "Down 45%"] and ["row60", "End priceUp 41%"] in box
    assert ["tag", "Average price \u00B7 flat \u00B12.6 pts"] in box


def test_a_read_whose_average_question_went_unanswered_says_so_and_stands_on_its_end_price():
    """The service falls back on the end-price question's call (service.day_calls ``average_missing``): the card says
    so under the big number, in one line with the service's whole sentence a long press away, and the call's square
    stands on the end price's grade (gradeOf), as the tally counts it."""
    hour = {**END_30, "used": 12, "by": {"next_30": END_30}, "average": {"error": "HTTP 529", "primary": "average_30", "box": "next_30"}}
    c = {"row_ts": "2026-09-28T11:02:10-04:00", "hour": hour, "marks": {"next_30": "2026-09-28T11:32:00-04:00"}, "calls": []}
    card = _sum_card(c, whole=True)
    fallback = "No average-price answer (HTTP 529), so the call is the end-price question\u2019s"
    skip, = _cls(card, "skip")
    assert _texts(card, "big") == ["Flat 80%"] and _flat_text(skip) == "No average answer; end-price call" and skip["attrs"]["title"] == fallback
    assert "End price" not in json.dumps(card)
    fell = {**call("11:02", "11:32", "flat", 0.8), "sum": "next_30", "average_missing": "HTTP 529", "odds": END_30["probabilities"],
            "end_price": {"outcome": "flat", "hit": True, "pick": "flat", "p": 0.8}, "end_price_only": True}
    assert _run("console.log(JSON.stringify(gradeOf(D.c)));", {"c": fell}) == {"label": "flat", "verdict": "Right", "endOnly": True}


def test_an_average_question_that_was_not_asked_is_never_said_to_be_unanswered():
    """The service's reason is shown as it wrote it (service.with_average): a read whose average-price question was not
    asked says so, its market clock in the viewer's zone, where the page used to say "unanswered" whatever happened."""
    why = "not asked: the window runs past the 16:00 close"
    hour = {**END_30, "used": 12, "by": {"next_30": END_30}, "average": {"error": why, "primary": "average_30", "box": "next_30"}}
    card = _sum_card({"row_ts": "2026-09-28T15:32:10-04:00", "hour": hour, "marks": {"next_30": None}, "calls": []}, whole=True)
    skip, = _cls(card, "skip")
    assert _flat_text(skip) == "No average answer; end-price call"
    assert skip["attrs"]["title"] == "No average-price answer (not asked: the window runs past the 13:00 close), so the call is the end-price question\u2019s"
    assert "unanswered" not in json.dumps(card)
    assert _run("console.log(JSON.stringify(fallbackWords(D.why, D.at)));", {"why": why, "at": "2026-09-28T15:32:10-04:00"}, TOKYO) \
        == "no average-price answer (not asked: the window runs past the 05:00 close), so the call is the end-price question\u2019s"


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
Node.prototype.getAttribute = function(k){ return k in this.attrs ? this.attrs[k] : null; };
Node.prototype.removeAttribute = function(k){ delete this.attrs[k]; };
Node.prototype.appendChild = function(n){ this.kids.push(n); n.parentNode = this; return n; };
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
['h1', 'sub', 'state', 'main', 'dock', 'load', 'poll', 'csTitle', 'csBody', 'od', 'px', 'pxSpark', 'qBtn', 'infoBtn', 'claude', 'claudeLab',
 'claudePick', 'claudeLine', 'claudeRows', 'claudeWhy', 'qsCount', 'chartSheet', 'chBody', 'chDay'].forEach(function(id){ ids[id] = new Node('div'); });
ids.claude.hidden = true; ids.qBtn.hidden = true;          // hidden in the markup until a read, a card
// an id the page gives a node it builds (the record row's) is found under main
function byId(n, id){ if(n.id === id) return n; for(var i = 0; i < n.kids.length; i++){ var f = byId(n.kids[i], id); if(f) return f; } return null; }
var document = {hidden: false, getElementById: function(id){ return ids[id] || byId(ids.main, id) || byId(ids.dock, id); }, createElement: function(t){ return new Node(t); },
                createElementNS: function(ns, t){ return new Node(t); }, querySelectorAll: function(){ return []; }, addEventListener: function(){}};
// the page's one layout query, a phone unless a test says otherwise, and what it hands the query's change to
var MQ = {matches: false, addEventListener: function(ev, fn){ MQ.change = fn; }};
var window = {addEventListener: function(){}, matchMedia: function(){ return MQ; }}, localStorage = {getItem: function(){ return null; }, setItem: function(){}};
var MiraiSheet = {open: function(){}, isOpen: function(){ return false; }, closed: 0, close: function(){ MiraiSheet.closed++; }};
var CARDS = {'JEV_LIVE_CARD': D.live, 'JEV_OPENING_CARD': D.tape, 'JEV_PREMARKET_CARD': D.premarket,
             'OPTIONS_FEED_STATUS_CARD': D.options, 'FORECASTER_SCOREBOARD_CARD': D.board,
             'CLAUDE_FORECAST_CARD': D.claude};
var grades = null;
function fetch(url){
  var m = /name=([^&]*)/.exec(url), name = m ? decodeURIComponent(m[1]) : '', c = CARDS[name];   // the spot poll names no store
  if(name === 'JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON') return Promise.resolve({ok: !!D.grades, json: function(){ return Promise.resolve({kind: 'jsonl', rows: D.grades}); }});
  return Promise.resolve({ok: true, json: function(){ return Promise.resolve(c ? {kind: 'json', data: c} : {error: 'no such file'}); }});
}
var setTimeout = function(){ return 0; }, clearTimeout = function(){}, setInterval = function(){ return 0; };
function dump(n){ return {tag: n.tag, attrs: n.attrs, text: n._t, kids: n.kids.map(dump)}; }
function flat(n){ return n._t + n.kids.map(flat).join(''); }
"""


def _whole(cards, now, tz=LA):
    """The page drawn at ``now`` in ``tz`` from ``cards`` (live, tape, premarket, options, board, claude; a missing one is
    not on file): the header line and whether it is an error, the state chips, each of main's parts as [class, flat
    text], the dock's, the questions control, Claude's strip and the options-data banner's lines (None while hidden)."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + WHOLE_DOM + MAIN_JS +
              "setImmediate(function(){ console.log(JSON.stringify({sub: ids.sub.textContent, err: ids.sub.classList.contains('err'),"
              " state: ids.state.kids.map(flat), main: ids.main.kids.map(function(k){ return [k.className || k.tag, flat(k)]; }),"
              " dom: ids.main.kids.map(dump),"
              " dock: ids.dock.kids.map(function(k){ return [k.className || k.tag, flat(k)]; }), dock_dom: ids.dock.kids.map(dump),"
              " rec: (function(r){ return r && !r.hidden ? flat(r) : null; })(byId(ids.main, 'rec')),"
              " qBtn: {hidden: ids.qBtn.hidden, text: ids.qBtn.textContent},"
              " claude: ids.claude.hidden ? null : {lab: ids.claudeLab.textContent, pick: ids.claudePick.textContent, line: ids.claudeLine.textContent, rows: dump(ids.claudeRows)},"
              " od: ids.od.hidden ? null : ids.od.kids.map(function(k){ return [k.className, flat(k)]; })})); });")
    out = run_node(_NODE, script, input=json.dumps({**cards, "now": now}), capture_output=True, text=True, timeout=20,
                         env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("tz", [LA, TOKYO, NY, KOLKATA])
@pytest.mark.parametrize("now, leads", [
    ("2026-09-28T09:30:00-04:00", "Market Open"),                     # the 09:28 pre-market card leads; at the bell its mode says the market is open
    ("2026-09-28T10:35:00-04:00", "10-min call · every 5 min"),       # the 10:30 lane call is open
    ("2026-09-28T15:40:00-04:00", "30-min call"),    # the 15:32 read's call is open
    ("2026-09-28T16:10:00-04:00", "30-min call"),
])
def test_mondays_real_cards_draw_whole_in_every_zone(now, leads, tz):
    got = _whole({k: MONDAY[k] for k in ("live", "tape", "premarket")}, now, tz)
    assert not got["err"], got["sub"]
    if leads == "Market Open":
        assert got["main"][0] == ["mode", leads]
        return
    # in the session the mode chip heads the leading card, first on the clock's row, and the header line says nothing
    # of a fresh card
    card = next(d for k, d in zip(got["main"], got["dom"]) if k[0] == "card dashed")
    row = card["kids"][0]["kids"][0]
    assert row["attrs"]["class"] == "clock-top" and [row["kids"][0]["attrs"]["class"], _flat_text(row["kids"][0])] == ["mode", leads]
    assert got["sub"] == "" and not any(k[0] == "mode" for k in got["main"])
    if leads.startswith("10-min"):
        # Monday's lane card is from before the average-price call: its end-price pick, the stretch row, the hand-back
        parts = _parts(card)
        assert parts[1:3] == [["lab call", "Opening call"], ["big", "Down small 44%"]]
        assert [v for k, v in parts if k == "row60"] == ["Last 5 minDown 2.7 pts \u00B7 normal"]
        assert parts[-1][1].startswith("Last 5-min check; every 30 min again from ")


@pytest.mark.parametrize("tz", [LA, TOKYO, KOLKATA])
def test_a_diary_row_with_no_wall_draws_its_fact_in_words(tz):
    """About one diary row in ten names no wall (09-15, 09-21, 09-23, 09-25 in market hours): its nearest-strike fact
    carries a figure with no distance, and the page broke on it ("the card could not be drawn") and lost the situation
    and every question. A fact whose figure has no number is drawn without a gauge, in the builder's sentence."""
    live = {**MONDAY["live"], "situation": MONDAY["no_wall"]["situation"]}
    got = _whole({"live": live, "tape": MONDAY["tape"], "premarket": MONDAY["premarket"]}, "2026-09-28T15:40:00-04:00", tz)
    assert not got["err"], got["sub"]
    # the situation sits in the dock, under today's calls, never in main
    assert not any(c[1].startswith("Situation") for c in got["main"])
    situation = [d for c, d in zip(got["dock"], got["dock_dom"]) if c[0] == "card sit"][0]
    rows = {r["kids"][0]["text"]: r for r in situation["kids"] if r["attrs"].get("class") == "sit-row"}
    wall = rows["Nearest Big Strike"]                                                  # the card's old words, read in the new
    assert [k["attrs"].get("class") for k in wall["kids"]] == ["sit-t", "sit-v"]      # no gauge
    assert _flat_text(wall["kids"][1]) == "None Nearby"                                # the sentence is in the sheet
    assert [k["attrs"].get("class") for k in rows["Price, last 30 min"]["kids"]] == ["sit-t", "sit-g", "sit-v"]
    assert [k for k, _ in got["dock"]] == ["card calls-link", "card sit"] and got["qBtn"]["hidden"] is False


def test_a_signed_fact_that_rounds_to_nothing_says_no_change():
    """The 09-25 11:33 row's implied volatility moved +0.03 vol points, Flat, and the page wrote "Up 0.0 vol points"
    beside the verdict. A change that rounds to nothing at the figure's precision says No change."""
    facts = [{"kind": "signed", "value": 0.03, "unit": "vol points"}, {"kind": "signed", "value": -0.12, "unit": "vol points"},
             {"kind": "signed", "value": 0.004, "unit": "sigma"}, {"kind": "signed", "value": 0.31, "unit": "sigma"},
             {"kind": "signed", "value": -0.004, "unit": "sigma"}]
    got = _run(_fn("signedWords") + "console.log(JSON.stringify(D.f.map(function(f){ return signedWords(f, 'vol.iv_30m'); })));",
               {"f": facts})
    assert got == ["No change", "Down 0.1 vol points", "No change", "Up 0.31\u00D7 a usual day", "No change"]


def _deck(live, tz=LA):
    """The questions sheet's deck for a card (qsDeck), each group as [name, answered, [each card's text]], its cards drawn
    as the sheet draws them (question, darkCard)."""
    decl = "".join(re.search(r"\n  var %s = .*?;" % v, JS, re.S).group(0) for v in ("ORDER", "NAME"))
    js = (decl + "function bar(n, p, pick){ return el('div', null, n + ' ' + Math.round(p * 100) + '%' + (pick ? ' picked' : '')); }"
          + "".join(_fn(f) for f in ("tag", "skipLine", "skipWhy", "question", "darkCard", "qsDeck"))
          + "console.log(JSON.stringify(qsDeck(D.c).map(function(g){ return [g.name, g.answered, g.qs.map(function(q){"
            " return (g.dark ? darkCard(q, D.c.row_ts) : question(q, D.c.omitted || {}, D.c.row_ts)).textContent; })]; })));")
    return _run(js, {"c": live}, tz)


def test_the_questions_sheet_keeps_every_question_in_its_group_in_the_docs_order():
    """Will's layout of 09-29: the page keeps one control, and the sheet deals every question on the card a group at a
    time, in the questions doc's group order and each group's own order, answered or not, with the count answered."""
    live = MONDAY["live"]
    deck = _deck(live)
    decl = "".join(re.search(r"\n  var %s = .*?;" % v, JS, re.S).group(0) for v in ("ORDER", "NAME"))
    order, names = _run(decl + "console.log(JSON.stringify([ORDER, NAME]));", {})
    ids = [vp for vp in order if any((q.get("viewpoint") or "outcome") == vp for q in live["questions"])]
    assert [g[0] for g in deck[:len(ids)]] == [names[vp] for vp in ids]
    assert sum(len(g[2]) for g in deck if not g[0].startswith("dark")) == len(live["questions"])
    for g, vp in zip(deck, ids):
        mine = [q for q in live["questions"] if (q.get("viewpoint") or "outcome") == vp]
        assert g[1] == sum(1 for q in mine if q.get("answer")) and [t[:len(q["ask"])] for t, q in zip(g[2], mine)] == [q["ask"] for q in mine]
    assert "el('details'" not in _fn("paint") and "qBtn.addEventListener('click', function(){ openQuestions(qBtn); });" in JS


def test_a_dark_question_names_its_own_reason_not_a_news_source_for_every_one():
    """Every dark question sat under "dark, waiting for a news source", overnight_range_position and gamma_cushion among
    them though neither waits for news. The sheet's last group says each waits for its own source, and each card gives
    its reason when the card carries one, as the questions doc words it (dark_reason)."""
    doc = json.loads((Path(__file__).resolve().parents[3] / "pipelines" / "spx_jev_questions_and_pools" / "questions" / "spx_questions.json").read_text())
    dark = [q for g in doc["groups"] for q in g["questions"].values() if q.get("status") == "dark"]
    assert dark and all(q.get("dark_reason") for q in dark), "every dark question in the doc names what it waits for"
    live = {**MONDAY["live"], "dark": [
        {"id": "overnight_range_position", "viewpoint": "levels_and_tape", "ask": "Where is price against the overnight futures range?",
         "dark_reason": "the overnight store (saved at 09:26 ET) holds /ES's bars, but no session read measures the night's range yet"},
        {"id": "news_headline", "viewpoint": "dark", "ask": "What kind of market news just came out?"}]}
    name, answered, cards = _deck(live)[-1]
    assert name == "dark, each waiting for its source" and answered == 0 and "news source" not in name
    assert cards == ["Where is price against the overnight futures range?Dark: the overnight store (saved at 06:26) holds /ES's bars, but no "
                     "session read measures the night's range yet", "What kind of market news just came out?Dark until what it needs is on file"]


def test_a_question_asked_and_lost_says_so_on_its_card_in_its_own_group():
    """On 09-28 several reads lost a group to a 503 or a timeout, and its questions sat under "not asked this run" though
    they were asked: the service marks them "asked, no answer received" (service.card), and each card says so, in its
    own group, apart from a question not asked this run, which gives its own reason."""
    live = json.loads(json.dumps(MONDAY["live"]))
    lost = [q for q in live["questions"] if q.get("answer")][:3]
    for q in lost:
        q.update(answer=None, skipped="asked, no answer received: HTTP 503")
    cards = [t for g in _deck(live) for t in g[2]]
    assert sum("Asked, no answer received: HTTP 503" in t for t in cards) == 3
    # the reason under the question, then what it would have chosen between, each option empty ("\u2013")
    for q in lost:
        empty = "".join(("Yes" if k == "true" else "No" if k == "false" else k[0].upper() + k[1:].replace("_", " ")) + "\u2013"
                        for k in q["options"])
        assert q["ask"] + "Asked, no answer received: HTTP 503" + empty in cards


def _nav(deck, steps, g=0, i=0):
    js = ("var qs = {deck: D.deck, g: D.g, i: D.i}, drawn = []; function qsDraw(full){ drawn.push(full); }"
          + _fn("qsGo") + _fn("qsJump") + _fn("qsGroupAt")
          + "var out = D.steps.map(function(s){ if(s[0] === 'go') qsGo(s[1]); else if(s[0] === 'at') qsJump(qsGroupAt(s[1]));"
            " return [qs.g, qs.i, drawn[drawn.length - 1]]; }); console.log(JSON.stringify(out));")
    return _run(js, {"deck": deck, "steps": steps, "g": g, "i": i})


def test_a_swipe_moves_one_question_along_and_on_into_the_next_group():
    """One question along either way; past a group's last question into the next group's first, and back past its first
    into the last question of the one before; nothing past the deck's ends. A move inside a group only slides the
    cards (qsDraw false); into another group deals that group's cards (true)."""
    deck = [{"qs": [1, 2]}, {"qs": [3]}, {"qs": [4, 5, 6]}]
    got = _nav(deck, [["go", -1], ["go", 1], ["go", 1], ["go", 1], ["go", -1], ["go", -1], ["go", 1], ["go", 1], ["go", 1], ["go", 1], ["go", 1]])
    assert [s[:2] for s in got] == [[0, 0], [0, 1], [1, 0], [2, 0], [1, 0], [0, 1], [1, 0], [2, 0], [2, 1], [2, 2], [2, 2]]
    assert got[1][2] is False and got[2][2] is True


def test_the_strip_jumps_to_the_group_under_the_finger_each_as_wide_as_its_questions():
    deck = [{"qs": [1]}, {"qs": [2, 3, 4]}, {"qs": [5, 6, 7, 8]}]          # 1 of 8, 3 of 8, 4 of 8 of the strip
    got = _nav(deck, [["at", 0.05], ["at", 0.2], ["at", 0.49], ["at", 0.51], ["at", 1.0]], g=0, i=0)
    assert [s[:2] for s in got] == [[0, 0], [1, 0], [1, 0], [2, 0], [2, 0]]


def test_a_new_card_keeps_the_question_in_view():
    """A card lands every 30 minutes while the reader is on a question: the sheet is dealt again and stays on that
    question, found by its id, and opens where it was left."""
    decl = "".join(re.search(r"\n  var %s = .*?;" % v, JS, re.S).group(0) for v in ("ORDER", "NAME"))
    js = (decl + "var qs = {deck: [], g: 0, i: 0, at: null, omitted: {}, count: ''};" + _fn("cardCount") + _fn("countWords") + _fn("qsDeck") + _fn("qsLoad")
          + "qsLoad(D.c); var first = [qs.g, qs.i]; qs.g = D.g; qs.i = D.i; var id = qs.deck[qs.g].qs[qs.i].id;"
            "qsLoad(D.c, true); var kept = [qs.g, qs.i, qs.deck[qs.g].qs[qs.i].id === id]; qsLoad(D.c); console.log(JSON.stringify([first, kept, [qs.g, qs.i]]));")
    assert _run(js, {"c": MONDAY["live"], "g": 3, "i": 2}) == [[0, 0], [3, 2, True], [0, 0]]
    assert "qsLoad(last, true);" in _fn("openQuestions") and "if(qsOpen()){ qsLoad(c, true); qsDraw(true); }" in _fn("paint")


def test_a_question_retired_at_the_cut_over_sits_in_its_own_last_group_and_never_in_the_count():
    """From the cut-over (2026-10-08) the service lists the live lane's pre-merge questions under ``dark`` with the reason
    "retired at the cut-over": the sheet deals them as a group of their own after the dark ones, says they are no longer
    asked, and the card's count ("N of M answered") is its questions alone, so a retired one is never among the M."""
    live = {**MONDAY["live"], "dark": [
        {"id": "news_headline", "viewpoint": "dark", "ask": "What kind of market news just came out?", "dark_reason": "no news feed"},
        {"id": "price_move_5way", "viewpoint": "price_move", "ask": "How did price move?", "dark_reason": "retired at the cut-over"}]}
    deck = _deck(live)
    (dark_name, _, dark_cards), (name, answered, cards) = deck[-2], deck[-1]
    assert dark_name == "dark, each waiting for its source" and dark_cards == ["What kind of market news just came out?Dark: no news feed"]
    assert name == "retired at the cut-over, no longer asked" and answered == 0
    assert cards == ["How did price move?Retired at the cut-over: its answers before it stay on record"]
    assert sum(len(g[2]) for g in deck[:-2]) == len(live["questions"]) and "no longer asked" in _fn("qsDraw")
    assert "c.questions.length + ' answered'" in _fn("countWords")


def test_the_questions_sheet_is_a_sheet_with_no_control_but_its_handle():
    """It opens and closes as every sheet on the page does (sheet.js: Back, Escape, and its data-sheet-close control),
    and that control is its handle, which a pull closes too; moving along is a swipe, a tap at either side or the strip.
    Its height is the page's measured one, never a viewport unit."""
    sheet = re.search(r'(?s)<div class="sheet qs" id="qSheet".*?\n</div>', SPX).group(0)
    assert 'role="dialog" aria-modal="true" aria-hidden="true"' in sheet
    assert sheet.count("<button") == 1 and '<button class="qs-grab" type="button" data-sheet-close aria-label="Close the questions">' in sheet
    assert 'class="qs-strip" id="qsStrip" role="slider" tabindex="0"' in sheet
    assert "height:calc(var(--app-h) * .92)" in SPX and "MiraiSheet.open(from);" in _fn("openQuestions")
    assert ".qs-track,.qs-card{transition:none}" in SPX          # reduced motion slides nothing
    # the card fills its room: its options share the height, the confidence line at the foot, the reason of a card
    # with nothing under it in the middle, and an unanswered question's options drawn empty and faded
    assert ".qs-card>.q>.bar{flex:1 1 0;" in SPX and ".qs-card>.q>.tag:last-child{margin-top:auto;" in SPX
    assert ".qs-card>.q>.skip:last-child{margin:auto 0;" in SPX and ".bar.none{opacity:.45}" in SPX


def test_a_sideways_swipe_on_a_question_card_is_the_pages_never_the_phones():
    """The stage hands sideways moves to the page (pan-y), but a touch-action reaches down only to the nearest scroller,
    and a card that scrolls is one: left at auto, Chrome took every sideways swipe on a card for its own pan and sent the
    page a pointercancel, so the card sprang back and nothing moved. Measured with real touches through Chrome at 360
    (DevTools input, not synthetic events): before, four swipes of four cancelled; after, three sideways swipes each moved
    a group and an upward drag still scrolled. Every scroller inside the stage says pan-y."""
    assert _rule(".qs-stage").count("touch-action:pan-y") == 1
    card = _rule(".qs-card>.q")
    assert "overflow-y:auto" in card and "touch-action:pan-y" in card
    inside = [sel for sel, body in re.findall(r"(?m)^(\.qs-(?:card|track)[^{]*)\{([^}]*)\}", SPX) if "overflow" in body and "auto" in body]
    assert inside == [".qs-card>.q"], inside


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
    got = _whole({"live": live, "tape": tape}, "2026-09-28T10:12:00-04:00", tz)
    assert not got["err"], got["sub"]
    card = next(d for c, d in zip(got["main"], got["dom"]) if c[0] == "card dashed")
    assert _flat_text(card["kids"][0]) == "30-min call"
    assert _flat_text(card["kids"][2]) == f"No 30-minute call on this read, next read {next_read}"
    assert [k["attrs"].get("class") for k in card["kids"]] == ["mode", "lab", "skip", "tag", "rec"]
    read = {LA: "06:31", TOKYO: "22:31", KOLKATA: "19:01"}[tz]
    assert _flat_text(card["kids"][3]) == f"The {read} read"      # the header no longer names the row, so the card does
    assert got["sub"] == f"No read since {read}, station data delayed"     # the 09:31 card is 41 minutes old, past DELAYED_MIN: said at the top


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


def test_every_colour_and_size_the_page_names_is_defined():
    """The header line's "brightens for a moment" named --i-ink, which the page never defines, so it never brightened:
    every var(--name) the page uses is one of its own custom properties."""
    used = set(re.findall(r"var\(--([a-z0-9-]+)", SPX))
    assert not used - set(re.findall(r"--([a-z0-9-]+)\s*:", SPX))


# the scoreboard as the station held it on 2026-10-10 (state/spx_jev/mirai_prediction/scoreboard.json), trimmed to what the page
# reads: 107 graded reads, the combined forecast right 52.3% of them, the usual-odds guess 57.9%, the call 5.34 points worse
BOARD = {"sums": {"average_30": {"graded_reads_count": 107, "since_day": "2026-09-29",
                                 "learner_pool": {"name": "learner_pool", "plain_name": "Combined forecast", "reads_count": 107, "right_pct": 52.3,
                                             "skill_vs_historical_odds_pct": -5.34, "status": "Worse", "interval_90": [-11.94, -0.55]},
                                 "voices": [{"name": "historical_odds", "plain_name": "Historical odds", "right_pct": 57.9, "skill_vs_historical_odds_pct": 0.0, "reads_count": 107},
                                            {"name": "jev_own", "plain_name": "JEV's own call", "right_pct": 37.4, "skill_vs_historical_odds_pct": -41.02, "reads_count": 107},
                                            {"name": "blend_50_50", "plain_name": "50/50 blend", "right_pct": 55.7, "skill_vs_historical_odds_pct": -9.38, "reads_count": 97},
                                            {"name": "matcher", "plain_name": "Matcher", "right_pct": 57.9, "skill_vs_historical_odds_pct": -5.29, "reads_count": 95}],
                                 "daily_skill": [{"day": "2026-10-07", "base_pool_pct": -33.81, "learner_pool_pct": -22.11}, {"day": "2026-10-08", "base_pool_pct": -2.06, "learner_pool_pct": 5.01},
                                                 {"day": "2026-10-09", "base_pool_pct": -6.18, "learner_pool_pct": -4.48}]}}}
_RECORD_FNS = ("labRow", "learnSum", "learnGuess", "scorePct", "verdictWords", "sideOf", "recordRow", "paintRecord", "scoreBar", "scoreRow", "skillLine", "openRecord")


def _record(board, tz=LA):
    """The record row built by the card and filled by paintRecord from ``board``, then the sheet a tap opens: the row's
    hidden state, its parts' flat text and label, and the sheet's title and body."""
    js = ("var nodes = {csTitle: el('div'), csBody: el('div')}; var row = null; function $(id){ return id === 'rec' ? row : nodes[id] || byId(row, id); }"
          "function byId(n, id){ if(!n) return null; if(n.id === id) return n; for(var i = 0; i < n.kids.length; i++){ var f = byId(n.kids[i], id); if(f) return f; } return null; }"
          "var opened = 0, MiraiSheet = {open: function(){ opened++; }};"
          + "".join(_var(v) for v in ("LEARN_URL", "BASE", "board")) + "".join(_fn(f) for f in _RECORD_FNS)
          + "row = recordRow(); board = D.board; paintRecord(); var hidden = row.hidden;"
          "openRecord(row); console.log(JSON.stringify({hidden: hidden, parts: row.kids.map(function(k){ return [k.attrs['class'], k.textContent]; }), label: row.attrs['aria-label'],"
          " title: nodes.csTitle.textContent, opened: opened, body: dump(nodes.csBody)}));")
    return _run(js, {"board": board}, tz)


def test_the_record_row_gives_the_scoreboards_numbers_in_the_learning_loops_words():
    """Final-Home (2026-10-10): the 30-minute card closes with one record row, "RECORD · 30 MIN" with the reads counted of
    the 300 that decide, the call's right count against the usual odds' with the learning loop's verdict word for word
    ("a bit worse" under 10 points), and "Too early to judge" until then; the row is hidden until the scoreboard has
    come, and a card drawn before it is filled when it does (draw and pollLearn both paint it)."""
    got = _record(BOARD)
    assert got["hidden"] is False
    assert got["parts"] == [["lab", "Record \u00B7 30 min107 of 300 reads"], ["rec-l", "56 of 107 right \u00B7 usual odds 58 \u00B7 a bit worse"], ["rec-n", "Too early to judge"]]
    assert got["label"] == "Record on 30-minute calls: 56 of 107 right \u00B7 usual odds 58 \u00B7 a bit worse, too early to judge. Open the learning loop"
    assert _record({"sums": {"average_30": {"graded_reads_count": 0}}})["hidden"] is True and _record(None)["hidden"] is True
    judged = {"sums": {"average_30": {**BOARD["sums"]["average_30"], "graded_reads_count": 312, "learner_pool": {**BOARD["sums"]["average_30"]["learner_pool"], "reads_count": 312, "right_pct": 60.0, "skill_vs_historical_odds_pct": 12.5}}}}
    assert _record(judged)["parts"][1:] == [["rec-l", "187 of 312 right \u00B7 usual odds 58 \u00B7 better"], ["rec-n", "Judged at 300 reads"]]
    assert "paintRecord(); drawClaude(); }" in _fn("draw") and "paintRecord(); })" in _fn("pollLearn")
    assert "var LEARN_URL = '/api/store/file?name=FORECASTER_SCOREBOARD_CARD', LEARN_SUM = 'average_30', LEARN_MS = 60000;" in JS
    assert "setInterval(function(){ if(!document.hidden) pollLearn(); }, LEARN_MS);" in JS and ".rec[hidden]{display:none}" in SPX
    assert 'id="learn"' not in SPX and "<section" not in SPX.split("<body>", 1)[1].replace('<section class="claude"', "")   # the learning loop card is gone


def test_the_record_rows_sheet_is_the_learning_loop_against_the_usual_odds_guess():
    """The sheet compares the combined forecast with the usual-odds guess (the historical odds voice), listed first as
    the 0 line, with the days' skill as a line; the old call has no row of its own (it is the 50/50 blend voice)."""
    got = _record(BOARD)
    assert got["title"] == "Learning loop \u00B7 30 min \u00B7 107 of 300 reads" and got["opened"] == 1
    body = got["body"]
    parts = [[k["attrs"].get("class", k["tag"]), _flat_text(k)] for k in body["kids"]]
    assert parts[:3] == [["lp-sub", "How often the direction was right"], ["lp-br", "Combined forecast52.3%"], ["lp-br base", "Usual-odds guess57.9%"]]
    assert parts[3] == ["lp-verdict dn", "Score vs the usual-odds guess: \u22125.3% \u00B7 a bit worse"]
    # the key names the dashed zero line; the drawing carries no label of its own (the old card's 9px "usual guess" sat
    # under the page's 11px floor and the line ran through it)
    assert parts[4] == ["lp-key", "Dashed line = the usual-odds guess · above it = beating the guess · below it = losing to it · too early to judge until 300 reads"]
    line = body["kids"][5]
    assert line["tag"] == "svg" and line["attrs"]["class"] == "lp-line" and [k["tag"] for k in line["kids"]] == ["line", "polyline", "circle"]
    assert "usual guess" not in _fn("skillLine") and ".lp-line text" not in SPX and "font:70011px" in _rule(".lp-t th")
    table = body["kids"][6]
    rows = [[r["attrs"].get("class"), [_flat_text(c) for c in r["kids"]]] for r in table["kids"][1]["kids"]]
    assert rows == [["base", ["Usual-odds guess107 reads", "57.9%", "0 \u00B7 baseline"]], ["pool", ["Combined forecast107 reads", "52.3%", "\u22125.3% a bit worse"]],
                    [None, ["JEV's own call107 reads", "37.4%", "\u221241.0% worse"]], [None, ["50/50 blend97 reads", "55.7%", "\u22129.4% a bit worse"]],
                    ["learner", ["Matcher95 reads", "57.9%", "\u22125.3% a bit worse"]]]
    assert [_flat_text(c) for c in table["kids"][0]["kids"][0]["kids"]] == ["Voice", "Right", "Score vs guess"]
    for gone in ("vs typical", "pool_v1", "learnHead", "learnChip", "lp-chip", "lp-head", "innerHTML"):
        assert gone not in SPX, gone


# ---- Claude's read (2026-10-10): the strip under the call and its Why sheet

# a read as pipelines/spx_claude_forecast writes latest.json (spec/record_formats.md), with the board's numbers (Final-Claude):
# the 14:30 ET read of 10-09, flat 58 on the next 30 minutes against the usual 60, up 28 against 19
CLAUDE = {"read_id": "live:2026-10-09T14:30:12-04:00", "trading_day": "2026-10-09", "half_hour_slot_et": "14:30", "read_at": "2026-10-09T14:30:12-04:00",
          "status": "ok", "usable_answer_count": 2, "claude_input_sha256": "sha256:e3be78dc3f2cd9ac", "prompt_and_model_version": "cr-1",
          "forecast": {"next_30_minutes": {"status": "ok", "up_pct": 28, "flat_pct": 58, "down_pct": 14, "largest_gap_between_answers_pct_points": 4,
                                           "base_rate_shown_to_claude": {"up_pct": 19, "flat_pct": 60, "down_pct": 21}},
                       "next_60_minutes": {"status": "ok", "up_pct": 30, "flat_pct": 55, "down_pct": 15, "largest_gap_between_answers_pct_points": 12,
                                           "base_rate_shown_to_claude": {"up_pct": 15, "flat_pct": 69, "down_pct": 17}},
                       "to_close": {"status": "ok", "up_pct": 34, "flat_pct": 50, "down_pct": 16,
                                    "base_rate_shown_to_claude": {"up_pct": 27, "flat_pct": 55, "down_pct": 18}}},
          "reasons": [{"input_field_path": "tape.price_vs_vwap_sig", "pushes_toward": "up", "horizon": "next_30_minutes",
                       "input_field_path_in_words": "price sits above the day\u2019s average", "pushes_toward_in_words": "points up", "horizon_in_words": "next 30 min"},
                      {"input_field_path": "options.regime.gamma_sign", "pushes_toward": "smaller_move", "horizon": "all",
                       "input_field_path_in_words": "dealers lean against moves today", "pushes_toward_in_words": "points to a smaller move", "horizon_in_words": "every horizon"}],
          "strongest_reason_against_lean": {"input_field_path": "tape.rv30_vs_clock_x", "pushes_toward": "smaller_move", "horizon": "next_30_minutes",
                                            "input_field_path_in_words": "the last 30 min moved less than usual at this hour",
                                            "pushes_toward_in_words": "points to a smaller move", "horizon_in_words": "next 30 min"},
          "record": {"verdict": "Too early", "graded_reads": 0, "scorecard_built_at": None, "horizons": {}}, "written_at": "2026-10-09T14:31:40-04:00"}
_CLAUDE_FNS = ("top1", "jevOwn", "combinedCall", "headline", "clOk", "clBase", "clPick", "clDir", "clHeader", "clBar", "clNumbers", "moveVsUsual", "tiltVsUsual", "against", "leadLine",
               "noAnswerWords", "agreeWords", "paintClaude", "drawClaude", "horizonKey", "reasonRow", "recordLine", "openWhy")
# the 30-minute card the CLAUDE read was made for (its read_id is "live:" and the card's row stamp), calling flat, and one calling up
FLAT_CARD = {"row_ts": "2026-10-09T14:30:12-04:00", "hour": {"average": {"pick": "flat", "probabilities": {"flat": 0.62, "up": 0.24, "down": 0.15}}}}
UP_CARD = {**FLAT_CARD, "hour": {"average": {"pick": "up", "probabilities": {"flat": 0.3, "up": 0.5, "down": 0.2}}}}


def _claude(c, card=None, tz=LA, leads=None, now="2026-10-09T14:32:30-04:00"):
    """paintClaude over a read at ``now`` beside ``card`` (the 30-minute card, for the call Claude's pick is set against;
    ``leads`` puts the pre-market card or the opening lane on screen in its place), then the Why sheet: the strip's
    label, pick, line and rows, and the sheet's title and body."""
    js = ("var IDS = {}; ['claude', 'claudeLab', 'claudePick', 'claudeLine', 'claudeRows', 'csTitle', 'csBody'].forEach(function(k){ IDS[k] = el('div'); });"
          "IDS.claude.hidden = true; function $(id){ return IDS[id]; } var opened = 0, MiraiSheet = {open: function(){ opened++; }};"
          "var last = D.card, shownPre = D.leads === 'pre', shownLeads = D.leads === 'lane';"
          + "".join(_var(v) for v in ("CLAUDE_URL", "HORIZONS", "DIRS", "claude", "STATUS_WORDS")) + "".join(_fn(f) for f in _CLAUDE_FNS)
          + "claude = D.c; drawClaude(); var strip = {hidden: IDS.claude.hidden, lab: IDS.claudeLab.textContent, pick: IDS.claudePick.textContent, line: IDS.claudeLine.textContent, rows: dump(IDS.claudeRows)};"
          "openWhy(el('button')); console.log(JSON.stringify({strip: strip, title: IDS.csTitle.textContent, opened: opened, body: dump(IDS.csBody)}));")
    return _run(js, {"c": c, "card": card, "leads": leads, "now": now}, tz)


def test_claudes_read_is_a_strip_under_the_call_never_a_second_headline():
    """Final-Home (2026-10-10): Claude's read is a hairline strip on the ground paper, no card and no shadow, headed
    "Claude's read · HH:MM" in the viewer's zone with "Not in the call" at the right; its pick at 15px, a size under the
    tiles' (the headline is the graded call, Will 10-07), with how it sits against the call and the usual on one line;
    then one row per horizon, the three chances as a slim bar, the numbers with the pick in bold, and how often this
    time of day stayed flat; and the Why control, 48px tall."""
    got = _claude(CLAUDE, FLAT_CARD)
    strip = got["strip"]
    assert strip["hidden"] is False and strip["lab"] == "Claude\u2019s read \u00B7 11:30" and strip["pick"] == "Flat 58%"
    assert strip["line"] == "agrees with the call \u00B7 leans up more than the historical odds"
    rows = strip["rows"]["kids"]
    assert [k["attrs"].get("class") for k in rows] == ["cl-h"] + ["cl-k", "cl-bar", "cl-n", "cl-usual"] * 3 and _flat_text(rows[0]) == "Down\u00B7Flat\u00B7Up"
    assert [_flat_text(k) for k in rows if k["attrs"].get("class") == "cl-k"] == ["30 min", "60 min", "Close"]
    assert [_flat_text(k) for k in rows if k["attrs"].get("class") == "cl-n"] == ["14\u00B758\u00B728", "15\u00B755\u00B730", "16\u00B750\u00B734"]
    assert [[c["tag"], c["text"]] for c in rows[3]["kids"]] == [["span", "14"], ["i", "\u00B7"], ["b", "58"], ["i", "\u00B7"], ["span", "28"]]
    assert [_flat_text(k) for k in rows if k["attrs"].get("class") == "cl-usual"] == ["Claude\u2019s Historical Odds \u00B7 Flat 60%", "Claude\u2019s Historical Odds \u00B7 Flat 69%", "Claude\u2019s Historical Odds \u00B7 Flat 55%"]
    assert rows[2]["attrs"]["aria-label"] == "Claude: Down 14, Flat 58, Up 28" and [k["attrs"]["class"] for k in rows[2]["kids"]] == ["dn", "fl", "up"]
    # against an up call the same read differs; with no call on the card the line is the lean alone
    assert _claude(CLAUDE, UP_CARD)["strip"]["line"] == "differs from the call \u00B7 leans up more than the historical odds"
    assert _claude(CLAUDE, None)["strip"]["line"] == "leans up more than the historical odds"
    assert _claude(CLAUDE, FLAT_CARD, TOKYO)["strip"]["lab"] == "Claude\u2019s read \u00B7 03:30"
    # the markup and its CSS: the strip, the label row, the 15px pick, the 48px Why control that opens the page's sheet
    assert '<section class="claude" id="claude" hidden aria-labelledby="claudeLab">' in SPX
    assert '<div class="lab"><span id="claudeLab">Claude&rsquo;s read</span><span class="r">Not in the call</span></div>' in SPX
    assert '<button class="cl-why" id="claudeWhy" type="button" aria-controls="jevSheet">Why</button>' in SPX
    assert _rule(".claude").startswith("padding:10px14px0;border:1pxsolidvar(--o-fl)") and "box-shadow" not in _rule(".claude") and "background" not in _rule(".claude")
    assert "color:var(--i-mute)" in _rule(".cl-h i,.cl-n i")                           # the dots read on the paper, as the page's other separators do
    assert "font:70015px/1.2" in _rule(".cl-lead b") and "font:70030px" in _rule(".big") and "height:var(--tap-min)" in _rule(".cl-why")
    assert "grid-template-columns:44pxminmax(0,1fr)84px" in _rule(".cl-grid") and "height:10px" in _rule(".cl-bar")
    assert "% chance the window ends each way" not in SPX and 'id="claudeR"' not in SPX and 'id="claudeSheet"' not in SPX


def test_an_older_read_is_never_set_against_the_call_on_screen():
    """Every morning until 09:32 latest.json is the day before's last read, and for a minute or two after each new card
    it is the last half hour's; under the opening call the 30-minute call is folded and under the pre-market card
    there is none. The strip only says "agrees" or "differs" of the read made for the card on screen (its read_id is
    the card's row stamp) while the 30-minute call leads; a read from another market day carries its weekday, in the
    viewer's zone, so it never passes for today's."""
    yesterday = {**CLAUDE, "read_id": "live:2026-10-08T15:30:12-04:00", "trading_day": "2026-10-08", "read_at": "2026-10-08T15:30:12-04:00"}
    got = _claude(yesterday, FLAT_CARD)["strip"]
    assert got["lab"] == "Claude’s read · Thu 12:30" and got["line"] == "leans up more than the historical odds"
    assert _claude(yesterday, FLAT_CARD, TOKYO)["strip"]["lab"] == "Claude’s read · Fri 04:30"
    last_half_hour = {**CLAUDE, "read_id": "live:2026-10-09T14:00:12-04:00", "read_at": "2026-10-09T14:00:12-04:00"}
    got = _claude(last_half_hour, FLAT_CARD)["strip"]
    assert got["lab"] == "Claude’s read · 11:00" and got["line"] == "leans up more than the historical odds"
    for leads in ("lane", "pre"):
        assert _claude(CLAUDE, FLAT_CARD, leads=leads)["strip"]["line"] == "leans up more than the historical odds", leads
    assert _claude(CLAUDE, FLAT_CARD)["strip"]["line"].startswith("agrees with the call")
    assert "c.read_id !== 'live:' + last.row_ts" in _fn("agreeWords") and "headline(callSum(last.hour))" in _fn("agreeWords")


def test_a_read_with_no_answer_or_a_rejected_horizon_says_so_in_the_strip():
    rejected = {**CLAUDE, "forecast": {**CLAUDE["forecast"], "next_30_minutes": {"status": "rejected", "base_rate_shown_to_claude": {"up_pct": 19, "flat_pct": 60, "down_pct": 21}}}}
    got = _claude(rejected)["strip"]
    assert got["pick"] == "Flat 55%" and got["line"] == "a move is more likely than the historical odds"      # the 60-minute horizon leads
    rows = got["rows"]["kids"]
    assert _flat_text(rows[3]) == "rejected" and rows[2]["attrs"]["class"] == "cl-bar none"
    none = {**CLAUDE, "status": "paused", "usable_answer_count": 0, "forecast": {}}
    got = _claude(none)["strip"]
    assert got["pick"] == "No answer" and got["line"] == "Paused by hand" and len(got["rows"]["kids"]) == 1


def test_the_why_sheet_sets_claudes_bar_over_the_usuals_with_the_reasons_in_plain_words():
    """Final-Claude (2026-10-10): the sheet is titled with the read's time in the viewer's zone; one grid, per horizon
    its lean in words then Claude's bar over the usual's soft bar with the numbers beside each under one "Down · Flat ·
    Up" header; the reasons, a horizon and a sentence each with which way it points in bold; the reason against; the
    record line; the page's own Close."""
    got = _claude(CLAUDE, None)
    assert got["title"] == "Claude\u2019s read \u00B7 why \u00B7 11:30" and got["opened"] == 1
    body = got["body"]
    grid = body["kids"][0]
    assert grid["attrs"]["class"] == "cl-vs"
    kinds = [k["attrs"].get("class") for k in grid["kids"]]
    assert kinds[:2] == ["cl-vs-cap", "cl-h"] and kinds[2:9] == ["cl-vs-k", "cl-k", "cl-bar", "cl-n", "cl-k", "cl-bar usual", "cl-n"]
    assert _flat_text(grid["kids"][0]) == "Claude\u2019s bar over its historical odds"
    leans = [[_flat_text(k["kids"][0]), _flat_text(k["kids"][1])] for k in grid["kids"] if k["attrs"].get("class") == "cl-vs-k"]
    assert leans == [["30 min", "+16 toward up"], ["60 min", "+14 toward a move \u00B7 +17 toward up"], ["Close", "+5 toward a move \u00B7 +9 toward up"]]
    nums = [_flat_text(k) for k in grid["kids"] if k["attrs"].get("class") == "cl-n"]
    assert nums == ["14\u00B758\u00B728", "21\u00B760\u00B719", "15\u00B755\u00B730", "17\u00B769\u00B715", "16\u00B750\u00B734", "18\u00B755\u00B727"]
    usual = [k for k in grid["kids"] if k["attrs"].get("class") == "cl-bar usual"][0]
    assert usual["attrs"]["aria-label"] == "Claude’s historical odds: Down 21, Flat 60, Up 19"
    assert _texts(grid, "cl-gap") == ["Claude\u2019s two answers differed by 12 points here"]       # the 60-minute horizon's two answers
    rest = [[k["attrs"].get("class"), _flat_text(k)] for k in body["kids"][1:]]
    assert rest == [["cl-why-h", "Why"], ["cl-why-r", "30 minPrice sits above the day\u2019s average \u00B7 points up"],
                    ["cl-why-r", "AllDealers lean against moves today \u00B7 points to a smaller move"],
                    ["cl-why-h", "Against"], ["cl-why-r", "30 minThe last 30 min moved less than usual at this hour \u00B7 points to a smaller move"],
                    ["cl-rec", "Record: too early \u00B7 0 graded reads, scored from 100. Not part of the call above."]]
    why = body["kids"][2]
    assert why["kids"][0]["attrs"] == {"class": "cl-k", "aria-label": "next 30 minutes"} and why["kids"][1]["kids"][-1]["tag"] == "b"
    assert body["kids"][3]["kids"][0]["attrs"]["aria-label"] == "all three horizons"
    scored = {**CLAUDE, "reasons": [], "strongest_reason_against_lean": None,
              "record": {"verdict": "Scored", "graded_reads": 120, "horizons": {"next_30_minutes": {"claude_right_pct": 54.2, "skill_vs_base_rate_pct": 4.1, "reads": 120}}}}
    rest = [[k["attrs"].get("class"), _flat_text(k)] for k in _claude(scored)["body"]["kids"][1:]]
    assert rest == [["cl-why-h", "Why"], ["cl-why-r", "AllNo reason survived the checks"],
                    ["cl-rec", "Record: right 54% of 120 reads (next 30 min) \u00B7 +4% vs the historical odds. Not part of the call above."]]
    assert "grid-template-columns:78pxminmax(0,1fr)110px" in _rule(".cl-vs") and "height:14px" in _rule(".cl-vs .cl-bar") and "height:8px" in _rule(".cl-vs .cl-bar.usual")


def _claude_poll(c):
    """pollClaude over the file the station serves: what the page keeps and whether the strip is hidden."""
    js = ("var IDS = {claude: el('div')}; IDS.claude.hidden = D.hidden; function $(id){ return IDS[id]; } var drawn = 0; function drawClaude(){ drawn++; IDS.claude.hidden = false; }"
          "function fetch(){ return Promise.resolve({ok: true, json: function(){ return Promise.resolve({kind: 'json', data: D.c}); }}); }"
          + _var("CLAUDE_URL") + _var("claude") + _fn("pollClaude")
          + "pollClaude(); setImmediate(function(){ console.log(JSON.stringify({kept: claude && claude.read_id, hidden: IDS.claude.hidden, drawn: drawn})); });")
    return _run(js, {"c": c, "hidden": True})


def test_a_sample_read_is_never_shown_as_a_real_one():
    """On 10-10 the owner was shown a sample's numbers as Claude's read. A file whose read_id begins "sample:" is kept
    out and hides the strip; a live read is kept and drawn; a file with no forecast changes nothing."""
    assert _claude_poll(CLAUDE) == {"kept": "live:2026-10-09T14:30:12-04:00", "hidden": False, "drawn": 1}
    assert _claude_poll({**CLAUDE, "read_id": "sample:2026-10-09T14:30"}) == {"kept": None, "hidden": True, "drawn": 0}
    assert _claude_poll({"error": "not found"}) == {"kept": None, "hidden": True, "drawn": 0}
    assert "var CLAUDE_URL = '/api/store/file?name=CLAUDE_FORECAST_CARD', NEAR = 3;" in JS
    assert "drawClaude();" in _fn("draw"), "a new card draws the strip again, so the line against the call is the card's"
    assert "pollOptions(); pollLearn(); pollClaude();" in JS


def test_the_whole_page_draws_claudes_strip_against_mondays_call():
    """Monday's 15:31 card is from before the cut-over, so it leads with JEV's own Down 51% and Claude's Flat differs
    from it; the same read under the opening call at 10:35, where that call is folded, is the lean alone."""
    cards = {k: MONDAY[k] for k in ("live", "tape", "premarket")}
    read = {**CLAUDE, "read_id": "live:" + MONDAY["live"]["row_ts"], "trading_day": "2026-09-28", "read_at": "2026-09-28T15:31:30-04:00"}
    got = _whole({**cards, "claude": read, "board": BOARD}, "2026-09-28T15:40:00-04:00")
    assert got["claude"]["lab"] == "Claude\u2019s read \u00B7 12:31" and got["claude"]["pick"] == "Flat 58%"
    assert got["claude"]["line"] == "differs from the call \u00B7 leans up more than the historical odds"
    assert "Today\u2019s call \u00B7 JEV35% sureDown 51%" in next(t for k, t in got["main"] if k == "card dashed")
    assert got["rec"] == "Record \u00B7 30 min107 of 300 reads56 of 107 right \u00B7 usual odds 58 \u00B7 a bit worseToo early to judge"
    lane = _whole({**cards, "claude": read}, "2026-09-28T10:35:00-04:00")
    assert lane["claude"]["line"] == "leans up more than the historical odds" and lane["claude"]["lab"] == "Claude\u2019s read \u00B7 12:31"
    assert next(t for k, t in lane["main"] if k == "card fold").startswith("30-min call \u00B7 12:31Down 51%")
    assert _whole(cards, "2026-09-28T15:40:00-04:00")["claude"] is None


# ---- the situation card (Final-Home, 2026-10-10)

SITUATION = [{"path": "price.recent_move", "title": "Price, last 30 min", "verdict": "Flat", "figure": {"kind": "signed", "value": 0.03, "band": 0.029, "unit": "sigma"},
              "sentence": "over the last 30 minutes price moved 0.03 of a normal day's move, larger than 4 of the last 19 sessions at this minute, bottom third: no real move"},
             {"path": "price.vs_vwap", "title": "Price vs day\u2019s average", "verdict": "Above", "figure": {"kind": "signed", "value": 0.21, "band": 0.101, "unit": "sigma"},
              "sentence": "price is 0.21 of a normal day's move above the day's volume-weighted average price (VWAP); it last traded at VWAP at 11:02 ET"},
             {"path": "range.rank", "title": "Range, last 30 min", "verdict": "Busy", "figure": {"kind": "rank", "under": 13, "of": 19},
              "sentence": "the last 30 minutes' range was busier than 13 of the last 19 sessions at this minute"},
             {"path": "gex.air_to_wall", "title": "Nearest heavy strike", "verdict": "Open air", "figure": {"kind": "distance", "value": 2.48, "near": 0.63, "unit": "sigma", "side": "above", "name": "call wall"},
              "sentence": "the nearest heavy strike is the call wall 2.48 of a normal day's move above price, with open air between"},
             {"path": "vol.iv_30m", "title": "Implied volatility", "verdict": "Flat", "figure": {"kind": "signed", "value": 0.03, "band": 0.5, "unit": "vol points"},
              "sentence": "implied volatility moved 0.03 vol points in 30 minutes"}]


def _situation(rows, sigma=75.3, age="13 MIN AGO", tz=LA, wide=False):
    """situationCard over ``rows`` on a card with ``sigma`` (on a phone unless ``wide``), then its sheet: the card's tag
    and label, each row as [name, gauge viewBox or None, verdict, caption], and the sheet's title and parts."""
    js = ("var nodes = {csTitle: el('div'), csBody: el('div')}; function $(id){ return nodes[id]; } var opened = 0, MiraiSheet = {open: function(){ opened++; }};"
          "function chartDocked(){ return !!D.wide; }"
          + _var("GAUGE_W") + _var("WALL_WORDS") + _var("SIT_RENAMED") + "".join(_fn(f) for f in ("sitWords", "labRow", "signedWords", "gauge", "measured", "points", "factCaption", "openSituation", "situationCard"))
          + "var c = {row_ts: '2026-10-09T14:30:40-04:00', sigma: D.sigma, situation: D.rows}, card = situationCard(c, D.age, false);"
          "card.kids[0].kids[1].on = null; var rows = card.kids.slice(1).map(function(r){ var g = r.kids.filter(function(k){ return k.tag === 'svg'; })[0], v = r.kids[r.kids.length - 1];"
          " return [r.kids[0].textContent, g ? g.attrs.viewBox : null, v._t, v.kids.length ? v.kids[0].textContent : null]; });"
          "openSituation(c, card); console.log(JSON.stringify({tag: card.tag, attrs: card.attrs, lab: dump(card.kids[0]), rows: rows, title: nodes.csTitle.textContent, opened: opened,"
          " sheet: nodes.csBody.kids.map(function(k){ return k.kids.map(function(x){ return x.textContent; }); })}));")
    return _run(js, {"rows": rows, "sigma": sigma, "age": age, "wide": wide}, tz)


def test_the_situation_is_one_control_of_key_value_rows_with_the_figures_in_points():
    """Final-Home (2026-10-10): the card is a button headed "Situation" with the age and a chevron; three rows, the fact's
    name, a 60px gauge in the middle (the same semantics: a signed figure centred on no change with its flat band shaded,
    a rank with its ends shaded, a distance from price with the near-wall stretch shaded) and the verdict at the right
    with the figure under it in index points on the card's sigma; vol points and a rank stay in their own units; a
    change that rounds to no points is no change. The sentences live in the sheet."""
    got = _situation(SITUATION)
    assert got["tag"] == "button" and got["attrs"] == {"class": "card sit", "aria-controls": "jevSheet", "aria-label": "Situation, 13 min ago. Open the facts in words"}
    assert _flat_text(got["lab"]) == "Situation13 MIN AGO" and got["lab"]["kids"][1]["attrs"]["class"] == "r age"
    assert got["rows"] == [["Price, last 30 min", "0 0 64 14", "Flat", "+2 pts"], ["Price vs day\u2019s average", "0 0 64 14", "Above", "by 16 pts"],
                           ["Range, last 30 min", "0 0 64 14", "Busy", "Busier than 13 of 19 days"], ["Nearest Big Strike", "0 0 64 14", "None Nearby", "Most Calls Held \u00B7 187 pts above"],
                           ["Implied volatility", "0 0 64 14", "Flat", "No change"]]
    assert _situation([{**SITUATION[0], "figure": {**SITUATION[0]["figure"], "value": -0.004}}])["rows"][0][3] == "No change"
    assert _situation([{**SITUATION[0], "figure": {**SITUATION[0]["figure"], "value": -0.102}}])["rows"][0][3] == "\u22128 pts"
    # a card without a sigma says the figure as a share of a usual day, as the card did before the board
    assert [r[3] for r in _situation(SITUATION[:2] + SITUATION[3:4], sigma=None)["rows"]] == ["Up 0.03\u00D7 a usual day", "0.21\u00D7 a usual day above", "Most Calls Held, 2.48\u00D7 a usual day above"]
    # a fact with no number (a diary row with no wall) has no gauge and no caption; a card from before 2026-10-13 (the
    # rows above) is read in the new words, and a card in the new words as it is
    bare = _situation([{"path": "gex.air_to_wall", "title": "Nearest Big Strike", "verdict": "None Nearby", "figure": {"kind": "distance", "value": None}, "sentence": "no heavy strike sits within reach"}])
    assert bare["rows"] == [["Nearest Big Strike", None, "None Nearby", ""]]
    assert "sit-top" not in SPX and "openFact" not in JS and 'role="button"' not in _fn("situationCard") and "margin-top:6px" in _rule(".sit .sit-row:first-of-type")


def test_the_situation_gauge_keeps_its_semantics_in_the_boards_box():
    js = ("function chartDocked(){ return !!D.wide; }" + _var("GAUGE_W") + _fn("gauge")
          + "console.log(JSON.stringify(D.f.map(function(f){ var g = gauge(f); return g && dump(g); })));")
    facts = [{"kind": "signed", "value": 0.03, "band": 0.029}, {"kind": "rank", "under": 13, "of": 19}, {"kind": "distance", "value": 2.48, "near": 0.63}, {"kind": "wind"}]
    signed, rank, distance, none = _run(js, {"f": facts})
    for g in (signed, rank, distance):
        assert g["attrs"] == {"viewBox": "0 0 64 14", "class": "sit-g", "aria-hidden": "true"}
        assert [k["tag"] for k in g["kids"]].count("rect") >= 1 and g["kids"][-1]["tag"] == "circle" and g["kids"][-1]["attrs"]["r"] == "4.2"
        assert all(4 <= float(k["attrs"]["cx"]) <= 60 for k in g["kids"] if k["tag"] == "circle")
    zone, dot = signed["kids"][0]["attrs"], signed["kids"][-1]["attrs"]
    assert zone["class"] == "zone" and float(zone["x"]) < 32 < float(zone["x"]) + float(zone["width"]) and float(dot["cx"]) > 32   # centred on no change, the dot just above
    assert [k["attrs"]["class"] for k in rank["kids"][:2]] == ["zone", "zone"] and abs(float(rank["kids"][-1]["attrs"]["cx"]) - (4 + 13 / 19 * 56)) < 0.1
    assert distance["kids"][0]["attrs"]["x"] == "4.0" and abs(float(distance["kids"][-1]["attrs"]["cx"]) - (4 + 2.48 / 3.1 * 56)) < 0.1   # the span grows with the distance
    assert none is None
    assert "width:60px;height:14px" in _rule(".sit-g")
    # from 1024px up the board's 150-wide gauge (Final-Desktop), the same drawing on a longer track, its ends a sixteenth in
    signed, rank, distance, none = _run(js, {"f": facts, "wide": True})
    for g in (signed, rank, distance):
        assert g["attrs"]["viewBox"] == "0 0 150 14" and all(9.3 <= float(k["attrs"]["cx"]) <= 140.7 for k in g["kids"] if k["tag"] == "circle")
    assert abs(float(rank["kids"][-1]["attrs"]["cx"]) - (9.375 + 13 / 19 * 131.25)) < 0.1 and distance["kids"][0]["attrs"]["x"] == "9.4"
    assert "GAUGE_W_WIDE = 150" in _var("GAUGE_W") and "W = chartDocked() ? GAUGE_W_WIDE : GAUGE_W, L = W / 16, R = W - L" in _fn("gauge")


def test_the_situation_sheet_gives_each_facts_sentence_in_the_viewers_zone():
    got = _situation(SITUATION)
    assert got["title"] == "Situation \u00B7 the 11:30 read" and got["opened"] == 1
    assert got["sheet"][:2] == [["FlatPrice, last 30 min", "Over the last 30 minutes price moved 0.03 of a normal day's move, larger than 4 of the last 19 sessions at this minute, bottom third: no real move."],
                                ["AbovePrice vs day\u2019s average", "Price is 0.21 of a normal day's move above the day's volume-weighted average price (VWAP); it last traded at VWAP at 08:02."]]
    assert len(got["sheet"]) == 5 and got["sheet"][3][0] == "None NearbyNearest Big Strike"
    assert _situation(SITUATION, tz=TOKYO)["title"] == "Situation \u00B7 the 03:30 read"


def test_the_questions_and_the_info_controls_share_one_row_that_fits_a_320px_phone():
    """Final-Home (2026-10-10): two half-width 48px buttons at the foot, "Questions · N asked" (drawn once a card is, hidden
    before the open and while the lane leads alone) and "How to read", on one flex row so the info control has the row
    to itself before a card; at 320 each holds 138px inside, and "Questions · 12 asked" fits it at the 13.5px the
    buttons are set at (at the board's 14px it is 138.2 wide). The footer line is the page's last."""
    assert re.search(r'<div class="vq-row">\n  <button class="vq" id="qBtn" type="button" aria-controls="qSheet" hidden>Questions</button>\n'
                     r'  <button class="vq info" id="infoBtn" type="button" aria-controls="infoSheet"><i aria-hidden="true">i</i>How to read</button>\n'
                     r'  <p class="note">A forecast, graded by the bars. Not advice.</p>\n</div>', SPX)
    assert _rule(".vq-row") == "display:flex;flex-wrap:wrap;gap:8px" and "flex:110;min-width:0;height:var(--tap-min);padding:0" in _rule(".vq") and ".vq[hidden]{display:none}" in SPX
    assert "font:70013.5px/1" in _rule(".vq")
    inside = (320 - 2 * 16 - 8) / 2 - 2
    assert _REC_W["Questions · 12 asked"] <= inside and 18 + 8 + _REC_W["How to read"] <= inside
    assert "$('qBtn').hidden = true;" in _fn("laneOnly") and "$('qBtn').hidden = true;" in _fn("paintPre") and "qb.hidden = false;" in _fn("paint")
    assert 'id="infoBtn"' in SPX and "How to read this page" in SPX.split('id="infoTitle"')[1][:60]


# ---- the options-data banner (2026-10-08)

# On 10-06..08 the options provider sent empty SPX books and 14 of the 79 code questions went blank. The station's
# alert pass (alerts_and_phone_pushes/watch/intraday/gex_alerts.py) writes spx_jev/mirai_prediction/options_data.json on every
# in-session tick; the page says the outage in a red banner under the price box, and only from a file of today's
# market day younger than 10 minutes. The 14 questions themselves are drawn nowhere on the page (the situation rows
# are labels, the questions sheet JEV's own questions, and the card's hour.code_sentences only a count, not drawn), so
# the banner is the whole of it.
OUTAGE = {"down": True, "since": "2026-10-08T09:30:10.048035-04:00", "back_at": None,
          "blank_questions": ["OPTIONS-01", "OPTIONS-02", "OPTIONS-03", "OPTIONS-04", "OPTIONS-05", "OPTIONS-06", "OPTIONS-07",
                              "OPTIONS-08", "OPTIONS-10", "OPTIONS-11", "VOLATILITY-01", "VOLATILITY-09", "VOLATILITY-10",
                              "VOLATILITY-16"],
          "total_questions": 79, "updated": "2026-10-08T11:00:00-04:00"}


def test_the_banner_reads_the_alert_passs_file_and_sits_under_the_price_box():
    assert "var OPTIONS_URL = '/api/store/file?name=OPTIONS_FEED_STATUS_CARD';" in JS
    assert "options = j && j.kind === 'json' ? j.data : null;" in _fn("pollOptions")    # /api/store/file wraps it as {kind, data}
    body = SPX[SPX.index("<body>"):]
    assert body.index('<button class="px" id="px" type="button" aria-controls="chartSheet" hidden>') < body.index('<div class="od" id="od" role="status" hidden></div>') \
        < body.index('<div id="state"></div>')
    assert "setInterval(function(){ if(!document.hidden){ pollOptions(); pollClaude(); } }, POLL_MS);" in JS
    assert "innerHTML" not in JS


@pytest.mark.parametrize("o, now, today, down", [
    (OUTAGE, "2026-10-08T11:04:00-04:00", "2026-10-08", True),
    (OUTAGE, "2026-10-08T11:10:00-04:00", "2026-10-08", True),              # 10 minutes old: still the alert pass's word
    (OUTAGE, "2026-10-08T11:10:01-04:00", "2026-10-08", False),             # older: the alert pass stopped, the book unknown
    (OUTAGE, "2026-10-09T09:31:00-04:00", "2026-10-09", False),             # yesterday's file
    ({**OUTAGE, "down": False}, "2026-10-08T11:04:00-04:00", "2026-10-08", False),
    ({**OUTAGE, "updated": "soon"}, "2026-10-08T11:04:00-04:00", "2026-10-08", False),
    (None, "2026-10-08T11:04:00-04:00", "2026-10-08", False),               # no file on the station
    (OUTAGE, "2026-10-08T11:04:00-04:00", None, False),                     # no market calendar on this phone
])
def test_the_banner_shows_only_while_todays_fresh_file_says_down(o, now, today, down):
    js = _var("OPTIONS_FRESH_MS") + _fn("optionsDown") + "console.log(JSON.stringify(optionsDown(D.o, Date.parse(D.t), D.today)));"
    assert _run(js, {"o": o, "t": now, "today": today}) is down


@pytest.mark.parametrize("tz, since", [(LA, "06:30"), (TOKYO, "22:30"), (NY, "09:30")])
def test_the_banner_says_since_when_in_the_viewers_zone_and_what_the_call_runs_on(tz, since):
    got = _whole({**{k: MONDAY[k] for k in ("live", "tape", "premarket")},
                  "options": {**OUTAGE, "updated": "2026-09-28T11:00:00-04:00"}}, "2026-09-28T11:02:00-04:00", tz)
    assert got["od"] == [["od-1", f"Options data down since {since}"],
                         ["od-2", "14 options questions blank · call uses the other 65 · provider issue, not ours"]]


def test_no_banner_without_a_file_or_once_the_book_is_back():
    cards = {k: MONDAY[k] for k in ("live", "tape", "premarket")}
    assert _whole(cards, "2026-09-28T11:02:00-04:00")["od"] is None
    back = {**OUTAGE, "down": False, "back_at": "2026-09-28T10:40:00-04:00", "blank_questions": [], "updated": "2026-09-28T11:00:00-04:00"}
    assert _whole({**cards, "options": back}, "2026-09-28T11:02:00-04:00")["od"] is None
    stale = {**OUTAGE, "updated": "2026-09-28T10:40:00-04:00"}
    assert _whole({**cards, "options": stale}, "2026-09-28T11:02:00-04:00")["od"] is None


# Chrome at 360 with the shipped face (Plus Jakarta Sans): the banner's first line at its widest time, and the second
# line's three parts, which wrap between words onto a second line inside the banner.
_OD_W = {"Options data down since 23:59": 211.06, "14 options questions blank ·": 161.75, "call uses the other 65 ·": 128.88,
         "provider issue, not ours": 136.41}


def test_the_banner_fits_the_owners_360px_phone():
    """Checked in Chrome at 360x800: the banner is the column's 328 wide, its first line on one line, its second on two,
    nothing past the banner."""
    inner = 360 - 2 * 16 - 2 * 14
    assert re.search(r"padding:10px14px", _rule(".od"))
    assert _OD_W["Options data down since 23:59"] <= inner
    for part in ("14 options questions blank ·", "call uses the other 65 ·", "provider issue, not ours"):
        assert _OD_W[part] <= inner, part
    assert "nowrap" not in _rule(".od") + _rule(".od-1") + _rule(".od-2")


# ---- the sheets: sheet.js on this page's real markup

# Everything after a sheet opens is sheet.js, one copy for every phone page. It was held on the SNDK reads page
# until that page went (2026-10-10); this page is where it is held now, driven against the page's own markup: the
# info sheet, opened by the page's own wiring of its button, and closed the one way whatever closed it.

class _Body(HTMLParser):
    """<body> as [tag, attrs, children], text as strings, scripts and styles left out: the page's own elements,
    so a stand-in DOM can hold them where the markup puts them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ["body", {}, []]
        self.open, self.skip = [self.root], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif not self.skip:
            node = [tag, {k: v or "" for k, v in attrs}, []]
            self.open[-1][2].append(node)
            self.open.append(node)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        elif not self.skip and len(self.open) > 1:
            self.open.pop()

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.open[-1][2].append(data)


def _body_tree():
    p = _Body()
    p.feed(re.split(r"(?m)^<body>$", SPX, maxsplit=1)[1].rsplit("</body>", 1)[0])
    return p.root


# the page's own wiring of the info button, lifted as the functions above are
_INFO_BUTTON = ("function $(id){ return document.getElementById(id); }"
                + re.search(r"\n  var infoBtn = \$\('infoBtn'\);\n  infoBtn\.addEventListener\('click', [^\n]*\);", SPX).group(0))

_SHEET_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const IN = JSON.parse(fs.readFileSync(0, 'utf8'));
let clock = 1e6, timers = [], nextId = 1;
const RealDate = Date;
function FakeDate(...a){ return a.length ? new RealDate(...a) : new RealDate(clock); }
FakeDate.now = () => clock;
FakeDate.parse = RealDate.parse;
FakeDate.prototype = RealDate.prototype;
function setTimeout_(fn, ms){ timers.push({id: nextId, at: clock + (ms || 0), fn}); return nextId++; }
function advance(ms){
  const end = clock + ms;
  for(;;){
    timers.sort((a, b) => a.at - b.at);
    const t = timers[0];
    if(!t || t.at > end) break;
    timers.shift(); clock = t.at; t.fn();
  }
  clock = end;
}

// Each element knows its parent, so closest() can walk up from a tap's target as in a browser.
function mk(tag, attrs){
  const cl = new Set(String(attrs.class || '').split(/\s+/).filter(Boolean)), heard = {};
  return {
    tag, attrs: Object.assign({}, attrs), parent: null, children: [], _text: '',
    dataset: {}, value: '', hidden: 'hidden' in attrs,
    classList: {add: (...c) => c.forEach(x => cl.add(x)), remove: (...c) => c.forEach(x => cl.delete(x)),
                contains: c => cl.has(c)},
    get className(){ return [...cl].join(' '); },
    set className(v){ cl.clear(); String(v).split(/\s+/).filter(Boolean).forEach(x => cl.add(x)); },
    get textContent(){ return this._text + this.children.map(c => c.textContent).join(''); },
    set textContent(v){ this.children.forEach(c => { c.parent = null; }); this.children = []; this._text = String(v); },
    append(...cs){
      for(const c of cs){
        if(c.parent) c.parent.children = c.parent.children.filter(x => x !== c);
        c.parent = this; this.children.push(c);
      }
    },
    setAttribute(k, v){ this.attrs[k] = String(v); },
    getAttribute(k){ return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k){ return k in this.attrs; },
    addEventListener(t, f){ (heard[t] = heard[t] || []).push(f); },
    fire(t, e){ (heard[t] || []).forEach(f => f(e)); },
    focus(){ document.activeElement = this; },
    matches(sel){
      return sel.split(',').map(s => s.trim()).some(s =>
        s[0] === '#' ? this.attrs.id === s.slice(1) : s[0] === '.' ? cl.has(s.slice(1))
        : s[0] === '[' ? s.slice(1, -1) in this.attrs : this.tag === s);
    },
    closest(sel){ for(let x = this; x && x.matches; x = x.parent) if(x.matches(sel)) return x; return null; },
    querySelector(sel){
      for(const c of this.children){
        if(c.matches && c.matches(sel)) return c;
        const f = c.querySelector && c.querySelector(sel);
        if(f) return f;
      }
      return null;
    },
  };
}
const text = t => ({parent: null, children: [], _text: String(t), get textContent(){ return this._text; }});
function build([tag, attrs, kids]){
  const n = mk(tag, attrs);
  n.append(...kids.map(k => typeof k === 'string' ? text(k) : build(k)));
  return n;
}
const body = build(IN.tree);
function find(n, id){
  if(n.attrs && n.attrs.id === id) return n;
  for(const c of n.children){ const f = find(c, id); if(f) return f; }
  return null;
}
const docL = {}, winL = {};
const on = bag => (t, f) => { (bag[t] = bag[t] || []).push(f); };
const document = {
  body, activeElement: null, visibilityState: 'visible', addEventListener: on(docL),
  getElementById: id => find(body, id), createElement: tag => mk(tag, {}), createTextNode: text,
};
// a click as a browser delivers it: the target and its ancestors, then the document
function click(n){
  const e = {type: 'click', target: n};
  for(let x = n; x; x = x.parent) if(x.fire) x.fire('click', e);
  (docL.click || []).forEach(f => f(e));
}
const key = k => (docL.keydown || []).forEach(f => f({key: k, target: document.activeElement}));
const scroll = y => { ctx.scrollY = y; (winL.scroll || []).forEach(f => f({})); };
const shell = [];
let pushes = 0, backs = 0;
const history = {
  state: null,
  pushState(s){ this.state = s; pushes++; },
  back(){ backs++; setTimeout_(() => { history.state = null; (winL.popstate || []).forEach(f => f({})); }, 10); },
};
const ctx = {document, history, Date: FakeDate, setTimeout: setTimeout_, clearTimeout(){},
             setInterval: () => 0, navigator: {}, scrollY: 0, scrollTo(x, y){ ctx.scrollY = y; },
             MiraiShell: {atTop: v => shell.push(v)}};
ctx.window = ctx;
ctx.addEventListener = on(winL);
vm.createContext(ctx);
const run = code => vm.runInContext(code, ctx);
run(fs.readFileSync(__SHEET__, 'utf8'));
run(IN.wiring);

const isOpen = () => body.classList.contains('sheet-open');
const focused = () => document.activeElement && (document.activeElement.attrs.id || document.activeElement.attrs.class);
console.log(JSON.stringify((() => { __STEPS__ })()));
"""


def _sheets(steps, wiring=_INFO_BUTTON):
    """Run sheet.js and the page's ``wiring`` of a control (its info button unless told otherwise) in node against
    a DOM built from the page's real markup, a fake clock, a fake history and a shell that records what it is
    told, and return what ``steps`` returns. In scope: body, document, run(code), advance(ms), click(node),
    key(name), scroll(y), isOpen(), focused() (the id, or failing that the class, of the element with the
    focus), shell (every MiraiShell.atTop answer), pushes and backs. Skips when node is not installed."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = (_SHEET_HARNESS.replace("__SHEET__", json.dumps(str(M / "sheet.js"))).replace("__STEPS__", steps))
    out = run_node(_NODE, script, input=json.dumps({"tree": _body_tree(), "wiring": wiring}),
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_sheet_opens_on_a_tap_and_closes_the_one_way_whatever_closed_it():
    """A tap on the info button opens its sheet, the one the button names in aria-controls, and a second tap while
    it is open pushes no second history entry. Everything after that is sheet.js: a history entry, so the phone's
    back gesture closes the sheet instead of leaving the page; one way to close whatever closed it — the backdrop,
    Close, Escape, Back — and a double tap goes back once; a tap on the backdrop in the first half second is the
    ghost of the tap that opened it and is ignored; the focus goes to the close button and comes back to the
    button that opened it.

    And the shell. The page scrolls the document, so it says nothing to the shell until a sheet first opens. From
    then on it keeps the answer true on every scroll: not at the top while the sheet is open, so a drag in the
    sheet is not a pull that reloads the page, and the page's real position once it closes."""
    button = re.search(r'<button class="vq info" id="infoBtn"[^>]*>', SPX).group(0)
    assert 'type="button"' in button and 'aria-controls="infoSheet"' in button
    got = _sheets("""
      const btn = document.getElementById('infoBtn'), sheet = document.getElementById('infoSheet');
      const scrim = body.children.find(c => c.attrs && c.attrs.class === 'scrim');
      scroll(40); scroll(0);
      const silent = shell.length;
      click(btn);
      const opened = {open: isOpen(), hidden: sheet.getAttribute('aria-hidden'), focus: focused(), pushes, shell: shell.slice()};
      click(btn);
      const again = pushes;
      click(scrim);
      const ghost = isOpen();
      advance(600);
      click(scrim); click(scrim);
      advance(50);
      const closed = {open: isOpen(), hidden: sheet.getAttribute('aria-hidden'), focus: focused(), backs, shell: shell.slice()};
      click(btn); advance(600); key('Escape'); key('Escape'); advance(50);
      const escaped = {open: isOpen(), backs};
      click(btn); advance(600); click(sheet.querySelector('.sh-close')); advance(50);
      const closeButton = {open: isOpen(), backs};
      click(btn); history.back(); advance(50);
      const back = isOpen();
      scroll(120); scroll(0);
      return {silent, opened, again, ghost, closed, escaped, closeButton, back, after: shell.slice(-2)};""")
    assert got["silent"] == 0, "the page speaks to the shell before any sheet has opened"
    assert got["opened"] == {"open": True, "hidden": "false", "focus": "sh-close", "pushes": 1, "shell": [False]}
    assert got["again"] == 1, "a tap on the button while its sheet is open pushed a second history entry"
    assert got["ghost"] is True, "the ghost of the opening tap closed the sheet"
    assert got["closed"] == {"open": False, "hidden": "true", "focus": "infoBtn", "backs": 1,
                             "shell": [False, True]}, "a double tap went back twice, or the sheet stayed open"
    assert got["escaped"] == {"open": False, "backs": 2}
    assert got["closeButton"] == {"open": False, "backs": 3}
    assert got["back"] is False, "the phone's back gesture left the sheet open"
    assert got["after"] == [False, True], "the shell was told once and then left with a stale answer"


# ---- the price-today chart (2026-10-10, Will's board Final-Chart)

# Two rows of state/reversion/2026-10-09.jsonl trimmed to the fields the chart reads, the strikes 7,765 to 7,840 kept:
# the day's last native row (15:58:59) and the last row built from SPY's book (15:47:08, gex_source spy_proxy×10.0346),
# which the file interleaves with native ones. A {_unparsed} row is what the route hands back for a line it could not
# parse.
NATIVE_ROW = {"ts": "2026-10-09T15:58:59.915437-04:00", "gex_source": "native", "gamma_sign": "positive", "spot": 7811.27,
              "gex_views": {"call_wall_gamma": 7815.0, "put_wall_gamma": 7810.0, "magnet": 7810.0,
                            "vol_side_by_strike": [[7765.0, 2975, 47387], [7770.0, 7243, 55417], [7775.0, 9678, 55041], [7780.0, 18144, 74040],
                                                   [7785.0, 27102, 77852], [7790.0, 59306, 93829], [7795.0, 70007, 90878], [7800.0, 131611, 131623],
                                                   [7805.0, 131781, 120797], [7810.0, 201427, 171090], [7815.0, 214495, 103649], [7820.0, 200763, 33470],
                                                   [7825.0, 113181, 7922], [7830.0, 76144, 4681], [7835.0, 57287, 1189], [7840.0, 38974, 1062]]}}
SPY_ROW = {"ts": "2026-10-09T15:47:08.078882-04:00", "gex_source": "spy_proxy×10.0346", "gamma_sign": "unknown", "spot": 7813.03,
           "gex_views": {"call_wall_gamma": 7816.943489038157, "put_wall_gamma": 7806.908901760829, "magnet": 7806.9089,
                         "vol_side_by_strike": [[7766.77, 36692, 250332], [7776.81, 120065, 363454], [7786.84, 293513, 463284], [7796.87, 656495, 680566],
                                                [7806.91, 864072, 672491], [7816.94, 831163, 343463], [7826.98, 731273, 85439], [7837.01, 142559, 23869]]}}
UNPARSED_ROW = {"_unparsed": "{\"ts\": \"2026-10-09T15:59"}


def bar(hhmm, o, h, lo, c, day="2026-10-09"):
    """A one-minute SPX bar as the bars job writes it (state/spx_jev/bars/<day>.jsonl)."""
    return {"ts": f"{day}T{hhmm}:00-04:00", "open": o, "high": h, "low": lo, "close": c, "volume": 0.0}


# the board's day at 14:43 ET as five of its bars: the open, the low's bar (09:48, low 7,779.34), midday, the high's
# bar (14:27, high 7,814.33) and the newest (14:42); the closes average 7,799.23
BOARD_BARS = [bar("09:30", 7786.36, 7790.34, 7786.36, 7789.23), bar("09:48", 7783.88, 7784.12, 7779.34, 7781.73),
              bar("12:00", 7800.5, 7801.0, 7799.9, 7800.27), bar("14:27", 7813.9, 7814.33, 7812.8, 7813.5), bar("14:42", 7811.5, 7812.0, 7811.0, 7811.42)]
# the board's card: the 14:30 read's call at 7,813.01, flat within 3.1, graded at 15:00
BOARD_CARD = {"row_ts": "2026-10-09T14:30:40-04:00", "sigma": 75.3127, "prior_close": {"day": "2026-10-08", "price": 7765.7},
              "session": {"close": "2026-10-09T16:00:00-04:00", "last_read": "2026-10-09T15:32:00-04:00"},
              "marks": {"next_30": "2026-10-09T15:00:00-04:00", "next_60": "2026-10-09T15:30:00-04:00"},
              "hour": {"probabilities": {"flat": 0.5, "up": 0.3, "down": 0.2},
                       "average": {"price": 7813.01, "edge_points": 3.1, "probabilities": {"flat": 0.62, "up": 0.24, "down": 0.15}, "pick": "flat"}}}


def grade(hhmm, verdict, horizon="next_30", day="2026-10-09"):
    return {"row_ts": f"{day}T{hhmm}:40-04:00", "horizon": horizon, "graded": True, "verdict": verdict, "pick": "flat", "label": "flat"}


BOARD_GRADES = [grade("10:02", "right"), grade("10:32", "wrong"), grade("11:02", "right"), grade("11:02", "right", horizon="next_60"),
                grade("10:02", "right", day="2026-10-08")]

_CHART_FNS = ("cardDay", "chartDocked", "todaysCard", "within", "dayClose", "inSession", "priceDay", "money", "indexLevel", "dayName", "skipLine", "tag", "bookRow", "dayCalls", "liveCall",
              "dayFacts", "paintSpark", "strikePitch", "chartWindow", "strikeBars", "kShort", "chWidth", "wordClear", "wordSpot", "bookLevels", "chartSvg", "chTile", "gapWords",
              "barAt", "bookTiles", "keyLine", "spotState", "chartDay", "paintChart")
_CHART_VARS = ("SPOT_URL", "SPOT_LIVE_MS", "SPARK_W", "WINDOW_SHARE", "CH_H", "CH_SIDE", "CH_ADV_400", "CH_ADV_500", "CH_ADV_800", "CH_ADV")


def _chart(data, tz=LA):
    """paintChart over a card, bars, a book and grades, at the sheet body's width D.W (320, a 360 phone's, unless
    told) and in the sheet unless D.wide, the panel from 1024px up: the market day on the title row and the body as dumped nodes."""
    js = ("var IDS = {}; ['chBody', 'chDay', 'pxSpark'].forEach(function(k){ IDS[k] = el('div'); }); function $(id){ return IDS[id]; } var WIDE = {matches: !!D.wide};"
          "if(D.W) IDS.chBody.clientWidth = D.W;"
          "var last = D.card, premarket = null, tape = null, bars = D.bars, book = D.book, grades = D.grades || null, spot = D.spot ? {price: D.spot, at: Date.now(), fresh: true} : null;"
          + "".join(_var(v) for v in _CHART_VARS) + "".join(_fn(f) for f in _CHART_FNS)
          + "paintChart(); paintSpark(); console.log(JSON.stringify({day: IDS.chDay.textContent, body: dump(IDS.chBody), spark: dump(IDS.pxSpark)}));")
    return _run(js, data, tz)


def _find(n, cls=None, tag=None, out=None):
    """The dumped nodes under ``n`` with every word of ``cls`` in their class, or of ``tag``, in drawing order."""
    out = [] if out is None else out
    for k in n["kids"]:
        if (cls is None or set(cls.split()) <= set(k["attrs"].get("class", "").split())) and (tag is None or k["tag"] == tag):
            out.append(k)
        _find(k, cls, tag, out)
    return out


def _board(**more):
    return _chart({"now": "2026-10-09T14:43:00-04:00", "card": BOARD_CARD, "bars": BOARD_BARS, "book": NATIVE_ROW, "grades": BOARD_GRADES,
                   "spot": 7811.47, **more})


def test_the_book_is_the_newest_native_row_of_the_days_last_rows():
    """The reversion file interleaves rows built from SPY's book (gex_source spy_proxy...) with SPX's own (native), and
    the route hands back {_unparsed} for a torn line. The chart draws the newest native one of the three it fetched,
    not the newest row, and with none native it has no book."""
    js = _fn("bookRow") + "console.log(JSON.stringify([bookRow(D.a), bookRow(D.b), bookRow(D.c), bookRow(D.d)].map(function(r){ return r && r.ts; })));"
    got = _run(js, {"a": [SPY_ROW, NATIVE_ROW, UNPARSED_ROW], "b": [NATIVE_ROW, SPY_ROW, SPY_ROW], "c": [SPY_ROW, UNPARSED_ROW, SPY_ROW],
                    "d": [{**NATIVE_ROW, "ts": "2026-10-09T15:57:35-04:00"}, NATIVE_ROW, SPY_ROW]})
    assert got == [NATIVE_ROW["ts"], NATIVE_ROW["ts"], None, NATIVE_ROW["ts"]]
    assert "'/api/store/file?name=SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN&day='" in _var("BOOK_URL") and "day + '&limit=3'" in _fn("pollBook")


def test_the_window_and_the_strike_bars_are_shaped_from_the_books_sides():
    """The window runs from the day's low to its high with the walls, the magnet and the open call's band inside, on the
    book's 5-point pitch, and takes in a strike past that with at least 30% of the busiest side's trading: on 10-09's
    book that is the board's 7,775 to 7,830. Each strike in it gets its calls and puts, the busiest (both sides
    together) is marked, and the longest single side is the scale both are drawn to."""
    js = ("".join(_fn(f) for f in ("dayFacts", "strikePitch", "chartWindow", "strikeBars")) + _var("WINDOW_SHARE")
          + "var d = dayFacts(D.bars), w = chartWindow(d, D.book, D.call), sb = strikeBars(D.book, w);"
          "console.log(JSON.stringify({w: w, n: sb.bars.length, most: sb.most, lead: sb.lead, first: sb.bars[0], last: sb.bars[sb.bars.length - 1],"
          " bare: chartWindow(d, null, null), quiet: chartWindow(dayFacts(D.quiet), null, null), spy: strikeBars(null, w).bars.length}));")
    got = _run(js, {"bars": BOARD_BARS, "book": NATIVE_ROW, "call": {"price": 7813.01, "edge": 3.1},
                    "quiet": [bar("09:30", 7801.2, 7801.9, 7800.4, 7801.5), bar("09:31", 7801.5, 7802.1, 7801.0, 7801.8)]})
    assert got["w"] == {"lo": 7775, "hi": 7830, "step": 5} and got["n"] == 12
    assert got["most"] == 214495 and got["lead"] == {"v": 7810, "calls": 201427, "puts": 171090, "busiest": True}
    assert got["first"] == {"v": 7775, "calls": 9678, "puts": 55041} and got["last"]["v"] == 7830
    assert got["bare"] == {"lo": 7775, "hi": 7815, "step": 5}             # no book: the day's range alone, on 5s
    assert got["quiet"] == {"lo": 7790, "hi": 7815, "step": 5}            # a few points of range are held to six strikes
    assert got["spy"] == 0


def test_the_chart_is_drawn_to_the_boards_scales():
    """At the sheet's 320px on a 360 phone: the price axis from 7,830 at y 20 to 7,775 at y 200 with the walls ruled
    inside it (the call wall solid at 7,815, the magnet dashed at 7,810, named above their lines and the put wall's name
    under the shared line); the open call's box at the called price ± its edge over the read-to-mark minutes with the
    called price through it; the now-line at the newest bar with its time in the viewer's zone; the dot at the live
    price; the low and high marked with their times; the day's average dashed and named; the open and close as the feet,
    the close with the zone; the gutter's puts left and calls right of one zero on one scale with its ends named; the
    day's calls as squares, the open one dashed, counted at the right."""
    got = _board()
    assert got["day"] == "Fri 10-09"
    svg = got["body"]["kids"][0]
    assert svg["tag"] == "svg" and svg["attrs"]["viewBox"] == "0 0 320 248" and svg["attrs"]["role"] == "img"
    texts = {t["text"]: t["attrs"] for t in _find(svg, tag="text")}
    strikes = [t for t in _find(svg, tag="text") if re.fullmatch(r"7,\d{3}", t["text"])]
    assert [t["text"] for t in strikes] == ["7,775", "7,780", "7,785", "7,790", "7,795", "7,800", "7,805", "7,810", "7,815", "7,820", "7,825", "7,830"]
    assert texts["7,830"]["y"] == "24.0" and texts["7,775"]["y"] == "204.0" and texts["7,810"]["class"] == "t-lead halo"
    assert all(t["attrs"]["x"] == "238" and t["attrs"]["text-anchor"] == "end" for t in strikes)
    wall, mag = _find(svg, "wall")[0], _find(svg, "mag")[0]
    assert (wall["attrs"]["y1"], wall["attrs"]["x2"]) == ("69.1", "204") and mag["attrs"]["y1"] == "85.5" and not _find(svg, "pwall")
    names = {t["text"]: t["attrs"] for t in _find(svg, "t-lvl")}
    assert names["Today’s Call Wall"] == {"x": "2.0", "y": "65.1", "text-anchor": "start", "class": "t-lvl halo"}
    assert names["Magnet"]["y"] == "81.5" and names["Today’s Put Wall"]["y"] == "98.0"             # one line, the second name under it
    box, at = _find(svg, "box")[0]["attrs"], _find(svg, "at")[0]["attrs"]
    assert (box["x"], box["y"], box["width"], box["height"]) == ("156.9", "65.5", "15.7", "20.3")  # 14:30 to 15:00, 7,816.11 down to 7,809.91
    assert (at["x1"], at["y1"], at["x2"]) == ("156.9", "75.6", "172.6")
    now = _find(svg, "now")[0]["attrs"]
    assert (now["x1"], now["y1"], now["y2"]) == ("163.2", "14", "208") and texts["11:42"] == {"x": "163.2", "y": "10", "text-anchor": "middle", "class": "t-now"}
    assert _find(svg, "dot")[0]["attrs"]["cy"] == "80.6" and _find(svg, "dot")[0]["attrs"]["cx"] == "163.2"
    marks = [m["attrs"] for m in _find(svg, "mark")]
    assert [(m["cx"], m["cy"]) for m in marks] == [("9.4", "185.8"), ("155.4", "71.3")]
    assert texts["low 7,779.34 · 06:48"]["x"] == "14.0" and texts["low 7,779.34 · 06:48"]["y"] == "198.8"
    assert texts["high so far 7,814.33 · 11:27"]["text-anchor"] == "end" and texts["high so far 7,814.33 · 11:27"]["y"] == "50.3"
    assert _find(svg, "avg")[0]["attrs"]["y1"] == "120.7" and texts["day avg 7,799"] == {"x": "202.0", "y": "117.2", "text-anchor": "end", "class": "t-avg halo"}
    assert texts["OPEN 06:30"]["x"] == "0" and texts["CLOSE 13:00 PDT"] == {"x": "204", "y": "226", "text-anchor": "end", "class": "t-axis"}
    line = _find(svg, "path")[0]["attrs"]["points"].split(" ")
    assert len(line) == 5 and line[0] == "0.0,153.4" and line[-1] == "163.2,80.8"
    puts, calls = _find(svg, "put"), _find(svg, "call")
    assert len(puts) == 12 and len(calls) == 12 and _find(svg, "put lead")[0]["attrs"]["x"] == "247.9"
    assert calls[8]["attrs"] == {"x": "281", "y": "64.6", "width": "39.0", "height": "9.0", "rx": "1.5", "class": "call"}   # 7,815's 214,495, the scale
    assert [(t["text"], t["attrs"]["x"]) for t in _find(svg, tag="text") if t["text"] in ("214k", "0")] == [("214k", "248"), ("0", "280"), ("214k", "320")]
    squares = [(s["attrs"]["class"], s["attrs"]["x"]) for s in _find(svg, "sq")]
    assert squares == [("sq", "12.7"), ("sq wrong", "29.0"), ("sq", "44.1"), ("sq open", "153.5")]
    assert texts["30-min calls · 2 of 3 right"]["x"] == "320"
    assert svg["attrs"]["aria-label"].startswith("SPX today by the minute from the 06:30 open to 11:42, low 7,779.34 at 06:48, high so far 7,814.33 at 11:27, now 7,811.47; "
                                                 "Today’s Call Wall at 7,815, Magnet at 7,810, Today’s Put Wall at 7,810 drawn as lines")
    assert svg["attrs"]["aria-label"].endswith("busiest 7,810; the open 11:30 call drawn as a dashed box, flat within 3.1 points to 12:00; "
                                               "the day’s 30-minute calls as squares under the clock, 2 of 3 right, one open")
    # under the drawing: the legend lines, the two tiles and the caption, the board's words with the day's numbers
    key, tiles, cap = got["body"]["kids"][1], got["body"]["kids"][2], got["body"]["kids"][3]
    assert [_flat_text(k) for k in key["kids"]] == ["Dashed box · the 11:30 call, flat ± 3.1 pts to 12:00", "Lines · Today’s Call Wall (7,815), Magnet (7,810), Today’s Put Wall (7,810)",
                                                   "Bars · today’s options per strike, puts left, calls right"]
    assert [[_flat_text(k) for k in t["kids"]] for t in tiles["kids"]] == [["Range so far", "35 pts", "0.46 of a normal day"], ["Magnet", "7,810", "373k · 1.5 pts below"]]
    assert tiles["attrs"]["class"] == "tod ch-tiles" and cap["attrs"] == {"class": "tag"} and _flat_text(cap) == "Dealers lean against moves · options book 12:58 · not advice"
    assert [k["attrs"]["class"] for k in key["kids"]] == ["box", "walls", "bars"] and cap["kids"][-1]["attrs"]["class"] == "ch-advice"   # what the panel leaves out, by class
    assert _flat_text(got["body"]["kids"][-1]) == _flat_text(cap) and len(got["body"]["kids"]) == 4


def test_the_chart_in_tokyo_and_after_the_close():
    """Every time on the drawing is the viewer's; after the close the high is the day's, not so far, the dot is the
    newest close and the tile says Range."""
    got = _chart({"now": "2026-10-10T19:00:00+09:00", "card": BOARD_CARD, "bars": BOARD_BARS, "book": NATIVE_ROW, "grades": BOARD_GRADES, "spot": 7811.47}, tz=TOKYO)
    svg = got["body"]["kids"][0]
    texts = [t["text"] for t in _find(svg, tag="text")]
    assert "OPEN 22:30" in texts and "CLOSE 05:00 GMT+9" in texts and "low 7,779.34 · 22:48" in texts and "high 7,814.33 · 03:27" in texts and "03:42" in texts
    assert _find(svg, "dot")[0]["attrs"]["cy"] == "80.8"                 # the newest close, 7,811.42: the quote is not this day's
    assert not _find(svg, "box") and not _find(svg, "sq open")           # the 14:30 call's mark has passed
    assert [_flat_text(k) for k in got["body"]["kids"][2]["kids"][0]["kids"]] == ["Range", "35 pts", "0.46 of a normal day"]
    assert [_flat_text(k) for k in got["body"]["kids"][1]["kids"]][0].startswith("Lines")


def test_the_chart_says_so_without_bars_and_without_a_book():
    """Without bars for the day the sheet says so in place of the drawing, and the price box draws no sparkline but
    stays a control; without a native book the chart draws no walls and no bars, the tile and the caption say the
    book is unavailable, and the legend keeps only what is drawn."""
    none = _chart({"now": "2026-10-09T09:31:00-04:00", "card": BOARD_CARD, "bars": None, "book": None})
    assert none["day"] == "Fri 10-09" and none["body"]["kids"] == [{"tag": "div", "attrs": {"class": "skip"}, "text": "No price bars yet for Fri 10-09", "kids": []}]
    assert none["spark"]["kids"] == [] and ".px-spark:empty{display:none}" in SPX
    nocard = _chart({"now": "2026-10-09T09:31:00-04:00", "card": None, "bars": None, "book": None})
    assert nocard["day"] == "" and _flat_text(nocard["body"]["kids"][0]) == "No card yet, so no day to draw"
    got = _board(book=None)
    svg = got["body"]["kids"][0]
    assert not _find(svg, "wall") and not _find(svg, "mag") and not _find(svg, "t-lvl") and not _find(svg, "put") and not _find(svg, "call")
    assert [t["text"] for t in _find(svg, tag="text") if re.fullmatch(r"7,\d{3}", t["text"])] == ["7,775", "7,780", "7,785", "7,790", "7,795", "7,800", "7,805", "7,810", "7,815", "7,820"]
    assert "; no options book; " in svg["attrs"]["aria-label"]
    key, tiles, cap = got["body"]["kids"][1], got["body"]["kids"][2], got["body"]["kids"][3]
    assert [_flat_text(k) for k in key["kids"]] == ["Dashed box · the 11:30 call, flat ± 3.1 pts to 12:00"]
    assert [_flat_text(k) for k in tiles["kids"][1]["kids"]] == ["Magnet", "No book", "options book unavailable"]
    assert _flat_text(cap) == "Options book unavailable · not advice"
    # the sparkline: the closes across the box, the newest as the dot
    spark = got["spark"]["kids"]
    assert [k["tag"] for k in spark] == ["polyline", "circle"] and spark[0]["attrs"]["points"].split(" ")[0] == "1.0,18.8" and spark[1]["attrs"] == {"cx": "63", "cy": "3.4", "r": "2.2"}


def test_the_price_box_is_the_control_that_opens_the_chart():
    """The price box is a button of the page's minimum height that names the sheet it opens, says what it opens in its
    label with the price and the day's change, and keeps the sparkline between the price and the change; the sheet's
    price line says the same price, change and state as the box."""
    assert re.search(r'<button class="px" id="px" type="button" aria-controls="chartSheet" hidden>\n.*\n  <svg class="px-spark" id="pxSpark" viewBox="0 0 64 26" aria-hidden="true"></svg>\n  <div class="px-r">', SPX)
    px = re.search(r"\n\.px\{[^}]*\}", SPX).group(0)
    assert "min-height:var(--tap-min)" in px and "width:100%" in px and "position:sticky" in px and "touch-action:manipulation" in px
    assert ".px:focus-visible{outline:2px solid var(--px-ink);outline-offset:2px}" in SPX
    assert ".px-k::after{margin-left:8px}" in SPX and SPX.count("transform:rotate(45deg)") == 1, "one chevron rule for every tap cue"
    assert re.search(r"\n\.px-k::after,button\.tile \.tile-k::after,[^{]*\.cl-why::after\{\n  content:\"\";flex:none;width:6px;height:6px;border-right:1\.5px solid var\(--i-mute\);"
                     r"border-top:1\.5px solid var\(--i-mute\);transform:rotate\(45deg\)\}", SPX)
    assert "\n  var pxBtn = $('px');\n  pxBtn.addEventListener('click', function(){ openChart(pxBtn); });" in JS
    assert _fn("openChart").index("MiraiSheet.open(from);") < _fn("openChart").index("pollBook(); pollGrades();")   # only an open sheet may fetch the book
    last = {"row_ts": "2026-09-29T13:01:56-04:00", "prior_close": {"day": "2026-09-28", "at": "2026-09-28T15:59:00-04:00", "price": 7684.5}}
    live = _price_sheet({"now": "2026-09-29T13:04:00-04:00", "at": "2026-09-29T13:04:00-04:00", "spot": 7656.39, "fresh": True, "last": last})
    assert live == {"label": "SPX live 7,656.39, down 28.11 today. Open the price chart", "live": True, "v": "7,656.39", "c": "−28.11 (−0.37%)", "cls": "px-c dn", "k": "live"}
    shut = _price_sheet({"now": "2026-09-29T16:20:00-04:00", "at": "2026-09-29T16:20:00-04:00", "spot": 7700, "fresh": True, "last": last})
    assert shut["label"] == "SPX · last close 7,700.00, up 15.50 today. Open the price chart" and shut["k"] == "last close" and shut["live"] is False
    before = _price_sheet({"now": "2026-09-30T08:10:00-04:00", "at": "2026-09-30T08:10:00-04:00", "spot": 7690.1, "fresh": True, "last": last})
    assert before == {"label": "SPX · last close 7,690.10. Open the price chart", "live": False, "v": "7,690.10", "c": "", "cls": "px-c", "k": "last close"}


# the page's own wiring of the price box, lifted as the info button's is, with the chart's painting and fetching stubbed:
# the sheet's opening and closing are what is under test
_PX_BUTTON = ("function $(id){ return document.getElementById(id); } function paintChart(){} function pollBars(){} function pollBook(){} function pollGrades(){} function chartDocked(){ return false; }"
              + _fn("openChart") + re.search(r"\n  var pxBtn = \$\('px'\);\n  pxBtn\.addEventListener\('click', [^\n]*\);", SPX).group(0))


def test_the_price_today_sheet_is_one_more_sheet_that_opens_and_closes_as_the_others_do():
    """One more of the page's sheets, with the one close button, opened by the price box through sheet.js: a tap
    opens it and puts the focus on Close, a second tap while open pushes nothing, Escape and Close close it and the
    focus comes back to the box."""
    sheet = re.search(r'(?s)<div class="sheet" id="chartSheet".*?\n</div>', SPX).group(0)
    assert 'role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="chTitle"' in sheet
    assert sheet.count("<button") == 1 and '<button class="sh-close" type="button" data-sheet-close>Close</button>' in sheet
    assert '<div class="sh-h ch-h"><span id="chTitle">Price today &middot; SPX</span><span class="r" id="chDay"></span></div>' in sheet
    assert '<div class="ch-px"><i id="chLive" hidden></i><b id="chV"></b><span class="px-c" id="chC"></span><span class="k" id="chK"></span></div>' in sheet
    assert ".ch-px i[hidden]{display:none}" in SPX
    assert len(re.findall(r'<div class="sheet[^"]*" id="\w+" role="dialog"', SPX)) == 4
    got = _sheets("""
      const btn = document.getElementById('px'), sheet = document.getElementById('chartSheet');
      click(btn);
      const opened = {open: isOpen(), hidden: sheet.getAttribute('aria-hidden'), focus: focused(), pushes};
      click(btn);
      const again = pushes;
      advance(600); key('Escape'); advance(50);
      const closed = {open: isOpen(), hidden: sheet.getAttribute('aria-hidden'), focus: focused(), backs};
      click(btn); advance(600); click(sheet.querySelector('.sh-close')); advance(50);
      return {opened, again, closed, closeButton: {open: isOpen(), backs}};""", wiring=_PX_BUTTON)
    assert got["opened"] == {"open": True, "hidden": "false", "focus": "sh-close", "pushes": 1}
    assert got["again"] == 1
    assert got["closed"] == {"open": False, "hidden": "true", "focus": "px", "backs": 1}
    assert got["closeButton"] == {"open": False, "backs": 2}


def test_the_book_is_fetched_only_while_the_sheet_is_open_and_each_file_at_most_once_a_minute():
    """The reversion file is 10 MB the server reads whole on every call: it is asked for only while the chart is visible
    (here as the sheet; the panel is the test after),
    and at most every 60 seconds; the grades, which draw the dock's calls row as well as the chart's squares, on every
    new card and while the sheet is open, at most every 60 seconds too; the bars at most every 60 seconds through the
    session and the minutes after it in which the close's last bar lands, from the card's day, and only a card naming
    a new day asks for them outside that, dropping the last day's bars before asking for its own."""
    assert _var("BARS_URL").endswith("BARS_MS = 60000, BARS_HOURS = ['09:30', '16:05'];") and _var("BOOK_URL").endswith("BOOK_MS = 60000;")
    assert "setInterval(function(){ if(!document.hidden && priceDay() === marketDay() && within(BARS_HOURS)) pollBars(); }, BARS_MS);" in JS
    assert "setInterval(function(){ if(!document.hidden && chartVisible()){ pollBook(); pollGrades(); } }, BOOK_MS);" in JS
    assert "if(priceDay() !== barsDay) pollBars();" in _fn("arrived") and "pollBars();" in _fn("wake") and "pollBook" not in _fn("wake")
    js = ("var calls = [], OPEN = 'true', WIDE = {matches: false}, painted = 0, sparked = 0; function $(id){ return {getAttribute: function(){ return OPEN; }}; }"
          "function paintChart(){ painted++; } function paintSpark(){ sparked++; } function paintCalls(){}"
          "function fetch(url){ calls.push(url.split('?')[1]); return Promise.resolve({ok: true, json: function(){ return Promise.resolve({kind: 'jsonl', rows: [D.row]}); }}); }"
          "var last = {row_ts: '2026-10-09T10:02:00-04:00'}, premarket = null, tape = null;"
          + "".join(_var(v) for v in ("BARS_URL", "BOOK_URL", "GRADES_URL", "SPOT_URL", "bars", "book", "grades"))
          + "".join(_fn(f) for f in ("cardDay", "todaysCard", "within", "dayClose", "inSession", "priceDay", "chartDocked", "chartVisible", "rawRows", "bookRow", "pollBars", "pollBook", "pollGrades"))
          + "function tick(){ return new Promise(function(r){ setImmediate(r); }); }"
          "(async function(){ var out = {};"
          " pollBook(); pollGrades(); pollBook(); await tick(); await tick(); out.closed = calls.length;"
          " OPEN = 'false'; pollBook(); pollGrades(); await tick(); await tick(); out.opened = calls.slice(); out.book = book && book.ts; out.painted = painted;"
          " pollBook(); pollGrades(); await tick(); await tick(); out.again = calls.length;"
          " NOW += 59000; pollBook(); pollGrades(); await tick(); await tick(); out.at59 = calls.length;"
          " NOW += 2000; pollBook(); pollGrades(); await tick(); await tick(); out.at61 = calls.length;"
          " calls = []; pollBars(); await tick(); await tick(); out.bars = calls.slice(); out.day = barsDay; out.sparked = sparked;"
          " pollBars(); await tick(); await tick(); out.barsAgain = calls.length;"
          " NOW += 61000; pollBars(); await tick(); await tick(); out.barsLater = calls.length;"
          " var had = bars; last = {row_ts: '2026-10-12T09:32:00-04:00'}; pollBars(); out.dropped = bars === null && had !== null;"
          " await tick(); await tick(); out.newDay = calls[calls.length - 1]; out.kept = bars !== null;"
          " console.log(JSON.stringify(out)); })();")
    got = _run(js, {"now": "2026-10-09T10:05:00-04:00", "row": NATIVE_ROW})
    assert got["closed"] == 1                                               # the grades, never the book, with the sheet shut
    assert got["opened"] == ["name=JEV_AVERAGE_PRICE_GRADES_BY_READ_AND_HORIZON&lane=live&limit=400", "name=SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN&day=2026-10-09&limit=3"] and got["book"] == NATIVE_ROW["ts"] and got["painted"] == 1
    assert got["again"] == 2 and got["at59"] == 2 and got["at61"] == 4
    assert got["bars"] == ["name=SPX_PRICE_BARS_BY_MINUTE&day=2026-10-09&limit=420"] and got["day"] == "2026-10-09" and got["sparked"] == 2   # dropped, then drawn
    assert got["barsAgain"] == 1 and got["barsLater"] == 2
    assert got["dropped"] is True and got["newDay"] == "name=SPX_PRICE_BARS_BY_MINUTE&day=2026-10-12&limit=420" and got["kept"] is True


def test_the_gutter_and_the_strike_column_fit_the_sheet_at_320_and_360():
    """The drawing is laid out at the sheet's measured width, 320 on the owner's 360 phone and 280 on a 320 one, so
    its 11px text never scales under the page's floor; the strike column ends two pixels clear of the longest put bar,
    the calls end at the right edge, nothing is drawn past either edge, and where both feet would not fit the plot the
    close foot gives up its zone's name rather than run into the open's."""
    for W, pw, lab, zone in ((320, 204, 238, "CLOSE 13:00 PDT"), (280, 164, 198, "CLOSE 13:00")):
        svg = _board(W=W)["body"]["kids"][0]
        assert svg["attrs"]["viewBox"] == f"0 0 {W} 248"
        rects = [r["attrs"] for r in _find(svg, tag="rect")]
        assert rects and all(0 <= float(r["x"]) and float(r["x"]) + float(r["width"]) <= W + 0.05 for r in rects)
        assert max(float(r["x"]) + float(r["width"]) for r in rects if "call" in r["class"]) == W
        assert min(float(r["x"]) for r in rects if "put" in r["class"]) >= lab + 2
        strikes = [t for t in _find(svg, tag="text") if re.fullmatch(r"7,\d{3}", t["text"])]
        assert all(t["attrs"]["x"] == str(lab) for t in strikes) and lab - 34 >= pw      # the column's 11px figures start past the plot
        texts = {t["text"]: t["attrs"] for t in _find(svg, tag="text")}
        assert texts["OPEN 06:30"]["x"] == "0" and texts[zone]["x"] == str(pw) and texts["CALLS"]["x"] == str(W)
        assert float(_find(svg, "now")[0]["attrs"]["x1"]) <= pw and float(texts["30-min calls · 2 of 3 right"]["x"]) == W
    assert ".chart{display:block;width:100%;height:auto;" in SPX and ".chart text{font-size:11px;" in SPX
    assert "var W = body.clientWidth || 320" in _fn("paintChart")


def test_a_wide_day_keeps_the_strike_prices_a_labels_height_apart():
    """On a day whose window runs past thirty strikes (a 160-point range) the strike column prices every second,
    third... strike so neighbours stay at least a label's height apart, instead of halving them once and overlapping
    at a few pixels' pitch; the busiest strike is always priced."""
    wide = [bar("09:30", 7700.0, 7702.0, 7698.0, 7700.5), bar("11:00", 7855.0, 7860.0, 7850.0, 7858.0), bar("14:42", 7811.5, 7812.0, 7811.0, 7811.42)]
    got = _board(bars=wide)
    prices = [k for k in _find(got["body"], tag="text") if re.fullmatch(r"\d,\d{3}", k["text"]) and k["attrs"].get("text-anchor") == "end"]
    ys = sorted(float(k["attrs"]["y"]) for k in prices)
    assert len(ys) >= 6 and min(b - a for a, b in zip(ys, ys[1:])) >= 11.5
    assert any(k["text"] == "7,810" and "t-lead" in k["attrs"].get("class", "") for k in prices)



# ---- the wide layout (2026-10-10, Will's board Final-Desktop): from 1024px up the chart stands beside the column


def _wide_css(html=SPX):
    css = re.sub(r"(?s)/\*.*?\*/", "", "\n".join(re.findall(r"(?s)<style>(.*?)</style>", html)))
    return css, re.search(r"(?s)@media \(min-width:1024px\)\{\n(.*?)\n\}", css).group(1)


def test_the_wide_layout_is_the_pages_one_layout_query_and_stands_the_chart_beside_the_column():
    """From 1024px up, the least width at which the 544px column keeps its width and the chart the 320 it is drawn at
    with the board's 40px sides and 32px between (no phone is that wide), the page is the board: the body a 544px column
    with the header across the top and the tabs in it, and the chart sheet placed at the column's right as a panel by
    its id, never slid up: no scrim, no handle, no Close, no price line (the box beside it says it), its words
    selectable as the page's are (the sheet refuses selection for the phone's long press), and the price box's chevron
    gone, its label saying a tap shows the panel. The query is the page's one layout query (the others ask for less
    motion or forced colours) and the script's WIDE is the same words. A window resized within the wide range draws the
    chart again at the panel's new width, as the questions sheet redraws on a resize."""
    css, wide = _wide_css()
    queries = re.findall(r"@media \(([^)]+)\)", css)
    assert queries.count("min-width:1024px") == 1
    assert set(queries) == {"min-width:1024px", "prefers-reduced-motion:reduce", "forced-colors:active"}
    assert "var WIDE = window.matchMedia('(min-width:1024px)');" in JS
    # the breakpoint's arithmetic, from the rules themselves: the left side, the column and the gap put the panel at its
    # left; the panel's padding either side of the chart's drawn 320 (the width the sheet lays it out at on a 360 phone,
    # paintChart's fallback); then the right side
    column = int(re.search(r"body > :not\([^)]*\)\{width:(\d+)px\}", wide).group(1))
    left = int(re.search(r"#chartSheet\{[^}]*left:(\d+)px;right:(\d+)px", wide).group(1)); right = int(re.search(r"#chartSheet\{[^}]*;right:(\d+)px", wide).group(1))
    pad = int(re.search(r"#chartSheet\{[^}]*padding:14px (\d+)px 16px", wide).group(1))
    assert column == 544 and left == 40 + column + 32 and pad == 20 and right == 40
    assert left + pad + 320 + pad + right <= 1024 and "var W = body.clientWidth || 320" in _fn("paintChart")
    for need in ("body{position:relative;max-width:none;margin:0;padding:0 40px 20px}", "body > :not(.hd, .load, .scrim, .sheet){width:544px}",
                 ".hd{flex-wrap:wrap;min-height:56px;margin:0 -40px;padding:0 40px;gap:0 28px}", ".px-k::after{display:none}",
                 "#chartSheet{position:absolute;top:calc(var(--hd-h,56px) + 12px);left:616px;right:40px;bottom:auto;z-index:auto;max-height:none;overflow:visible;",
                 "transform:none;visibility:visible;transition:none;border-radius:18px;padding:14px 20px 16px;box-shadow:var(--sh1);",
                 "-webkit-user-select:auto;user-select:auto}", "#chartSheet .sh-grab,#chartSheet .ch-px,#chartSheet .sh-close,#chartSheet .ch-advice{display:none}"):
        assert need in wide, need
    assert "scrim" not in wide.replace("body > :not(.hd, .load, .scrim, .sheet)", "")   # the scrim is the sheets': the panel never sets it
    # the chart sheet keeps its one markup: no second body, no second painter
    assert SPX.count('id="chBody"') == 1 and JS.count("function chartSvg(") == 1 and JS.count("function paintChart(") == 1
    assert "if(chartDocked()){ $('chartSheet').scrollIntoView({block: 'nearest'}); return; }" in _fn("openChart")
    assert "(chartDocked() ? '. Show the price chart' : '. Open the price chart')" in _fn("paintPx")
    assert "window.addEventListener('resize', function(){ if(chartDocked()) paintChart(); });" in JS
    assert "'/m/press.js'" not in wide and "--on-faint" not in SPX and "--on-mute" not in SPX and "--state-hover" not in SPX   # the tokens nothing on the page reads went
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", JS), "the script names the page's tokens, never a colour"


def test_the_tabs_sit_in_the_headers_row_on_both_pages_from_1024px_up():
    """Final-Desktop's header is one row: the name, the tabs in flow after it (never fixed over the header, so the
    trouble line, which sits between them in flow, can never run under them), and the day at the right; an error on
    the forecast page, read whole, wraps under the row at its full width (at 1024 a long one squeezed between the tabs
    and a full right side ran to six lines) rather than growing the row. The reads page draws the same row, with its
    pinned score centred under it, so the Reads tab stands in the same place on both pages, and neither keeps a bottom
    bar. The bar is the same markup on both: fixed at the phone's foot, static in the row."""
    for page in ("jev-spx.html", "reads.html"):
        html = (M / page).read_text()
        css, wide = _wide_css(html)
        assert ".tabs{order:1;position:static;height:56px;padding:0;gap:4px;align-items:center;background:none;border-radius:0;box-shadow:none}" in wide, page
        assert ".tab{flex-direction:row;gap:8px;min-height:var(--tap-min);padding:0 12px}" in wide and ".tab svg{width:18px;height:18px}" in wide, page
        assert ".hd-2{order:2;flex:0 1 auto;min-width:0;margin:0" in wide and ".hd-r{order:3}" in wide, page
        assert "fixed" not in wide and "translateX" not in wide, page
        assert "padding:0 40px 20px}" in wide, page            # no room kept for a bottom bar
    assert ".hd-t,.hd-1{display:contents}" in _wide_css()[1]
    assert ".hd-2.err{order:4;flex:1 1 100%;margin:0 0 10px}" in _wide_css()[1]     # after the right side, so the row stays one line
    reads = _wide_css((M / "reads.html").read_text())[1]
    assert ".hd{display:flex;flex-wrap:wrap;align-items:center;gap:0 28px;min-height:56px;margin:0 -40px;padding:0 40px 10px}" in reads
    assert ".hd-1{display:contents}" in reads and ".sum{order:4;flex:1 1 100%;max-width:528px;margin:0 auto}" in reads
    assert "main{width:100%;max-width:528px;margin:0 auto}" in reads   # the days stay the phone's centred column


def test_the_wide_column_is_the_boards_three_tiles_a_row_of_horizons_and_the_note_beside_the_buttons():
    """Final-Desktop's column, by the one wide block: the call's tiles are three (the voices control the third, placed by
    the builder), the tiles' grid gives every tile the same share whatever their count (the chart's four too), Claude's
    three horizons stand side by side with the Why control on the strip's label row, the sparkline is the board's 120,
    today's calls are one row (the label, the squares, the score at the right, the chevron: the label row gives up its
    box so its score and chevron follow the squares), and the note sits beside the two 176px buttons."""
    css, wide = _wide_css()
    assert ".calls-link{display:flex;align-items:center}" in wide and ".calls-link .calls{flex:1 1 auto;display:flex;align-items:center;gap:12px}" in wide
    assert ".calls-link .lab{display:contents}" in wide and ".calls-link .lab .r,.calls-link .lab::after{order:1}" in wide and ".calls-link .sq{margin-top:0}" in wide
    assert ".tod{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr)}" in wide
    assert ".ch-tiles{grid-template-columns:repeat(auto-fit,minmax(140px,1fr));grid-auto-flow:row}" in wide
    # the tiles' sub lines wrap at every width, the phone's half-sheet tile too (DEV-7: "options book unavailable" at 320)
    assert ".ch-tiles .tile-s{white-space:normal}" in css.replace(wide, "") and ".ch-tiles .tile-s" not in wide
    assert ".voices{display:block;height:auto;margin:0;padding:9px 10px;border:1px solid var(--rule-soft);border-radius:12px;text-align:left;font:inherit;" in wide
    assert ".voices::after{display:none}" in wide and ".voices .tile-k{display:flex}" in wide and ".voices .tile-s{display:block;white-space:normal}" in wide
    assert ".claude{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;padding-bottom:10px}" in wide
    assert ".cl-foot{grid-column:2;grid-row:1;margin:-16px 0;border-top:0}" in wide and ".cl-h{display:none}" in wide
    assert ".cl-grid{grid-template-columns:repeat(3,auto minmax(0,1fr));gap:4px 8px}" in wide
    assert ".cl-k,.cl-n{grid-row:1;grid-column:auto}" in wide and ".cl-bar{grid-row:2;grid-column:span 2}" in wide and ".cl-usual{grid-row:3;grid-column:span 2;margin-bottom:0;white-space:normal}" in wide
    assert ".px-spark{flex:0 0 120px}" in wide and "SPARK_W_WIDE = 120" in _var("SPARK_W") and "W = chartDocked() ? SPARK_W_WIDE : SPARK_W" in _fn("paintSpark")
    assert ".vq{flex:0 0 176px}" in wide and ".note{flex:1 1 0;min-width:0;text-align:right}" in wide   # shrinks beside them, never under
    spark = _chart({"now": "2026-10-09T14:43:00-04:00", "card": BOARD_CARD, "bars": BOARD_BARS, "book": None, "wide": True})["spark"]
    assert spark["attrs"]["viewBox"] == "0 0 120 26" and spark["kids"][1]["attrs"]["cx"] == "119" and spark["kids"][0]["attrs"]["points"].startswith("1.0,")
    # a crossing of WIDE draws the page again, so the tiles and the gauges take the new shape
    assert "if(last) draw(last, lastOk); else paintPx();" in JS and "paintSpark();\n    if(chartVisible()){ paintChart(); pollBook(); pollGrades(); }" in JS


def test_the_situation_stands_under_the_panel_from_1024px_up_by_the_panels_measured_height():
    """Final-Desktop puts the situation under the chart in the right column, its three facts side by side (the name, the
    verdict with its caption, the gauge). The card stays where the dock puts it in the markup (so the phone's order and
    focus order never change) and the stylesheet places it: absolute at the panel's left and right, its top the header's
    height plus the panel's (--ch-h, measured by the same observer that measures the header for the sticky price box)
    plus the two gaps. A grid could not do it: the right column's two members would share the left column's rows, and
    the card would drop to the column's foot whenever Claude's strip made the column the taller. An absolutely placed
    card's margin adds nothing to the page's height, so the body's 20px under it is a box hung under the card, which
    takes no tap."""
    _, wide = _wide_css()
    assert ".sit{position:absolute;top:calc(var(--hd-h,56px) + var(--ch-h,0px) + 24px);left:616px;right:40px;width:auto;display:flex;flex-wrap:wrap;" in wide   # width:auto: the card's own 100% would be the body's
    assert '.sit::after{content:"";position:absolute;top:100%;left:0;right:0;height:20px;pointer-events:none}' in wide
    # three facts side by side where the card holds three 160px cells (the board's 744, and 1280's 584), each an equal
    # share; stacked at 1024, where the card is the phone sheet's width
    assert "gap:10px 20px;padding:12px 20px 14px}" in wide and ".sit>.lab{flex:1 1 100%}" in wide
    assert ".sit-row,.sit .sit-row:first-of-type{flex:1 1 160px;display:grid;grid-template-columns:minmax(0,1fr);gap:3px;margin:0;padding:0;border-top:0}" in wide
    assert ".sit-v{grid-row:2;text-align:left;white-space:normal;font-size:15px}" in wide and ".sit-v span{display:inline;margin-left:6px;font-size:12.5px}" in wide
    assert ".sit-g{grid-row:3;width:150px}" in wide
    assert "[['.hd', '--hd-h'], ['#chartSheet', '--ch-h']].forEach" in SPX and SPX.index("['#chartSheet', '--ch-h']") > SPX.index('id="chartSheet"')
    assert "document.querySelectorAll('#dock .lab .age')" in _fn("ages")       # the ages still find the card in the dock
    got = _situation(SITUATION, wide=True)
    assert got["rows"][0] == ["Price, last 30 min", "0 0 150 14", "Flat", "+2 pts"] and got["attrs"]["class"] == "card sit"


def test_the_book_is_fetched_while_the_chart_is_visible_as_a_sheet_or_as_a_panel():
    """chartVisible is the one gate on the 10 MB book: on a phone the sheet open, from 1024px up the panel laid out
    (the query's matches), and neither means no fetch; a card's arrival asks through the same gate."""
    js = ("var calls = [], OPEN = 'true', WIDE = {matches: false}; function $(id){ return {getAttribute: function(){ return OPEN; }}; }"
          "function paintChart(){} function fetch(url){ calls.push(url.split('name=')[1].split('&')[0]); return Promise.resolve({ok: true, json: function(){ return Promise.resolve({kind: 'jsonl', rows: [D.row]}); }}); }"
          "var last = {row_ts: '2026-10-09T10:02:00-04:00'}, premarket = null, tape = null;"
          + "".join(_var(v) for v in ("BOOK_URL", "SPOT_URL", "book")) + "".join(_fn(f) for f in ("cardDay", "todaysCard", "within", "dayClose", "inSession", "priceDay", "chartDocked", "chartVisible", "rawRows", "bookRow", "pollBook"))
          + "function tick(){ return new Promise(function(r){ setImmediate(r); }); }"
          "(async function(){ var out = {};"
          " pollBook(); await tick(); await tick(); out.phoneShut = [chartVisible(), calls.length];"
          " WIDE.matches = true; pollBook(); await tick(); await tick(); out.panel = [chartVisible(), calls.slice()];"
          " WIDE.matches = false; bookAt = 0; OPEN = 'false'; pollBook(); await tick(); await tick(); out.sheet = [chartVisible(), calls.length];"
          " console.log(JSON.stringify(out)); })();")
    assert _run(js, {"now": "2026-10-09T10:05:00-04:00", "row": NATIVE_ROW}) == {"phoneShut": [False, 0], "panel": [True, ["SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN"]], "sheet": [True, 2]}
    assert "pollBook();" in _fn("arrived") and "if(!day || !chartVisible() || bookInflight) return;" in _fn("pollBook")


def test_the_panel_draws_the_chart_at_the_boards_height_and_the_sheet_at_its_own():
    """One painter: in the sheet the drawing is 248 tall (Final-Chart); in the panel, at its 744px, 442 (Final-Desktop),
    with the same rows under the plot measured from the foot: the axis 36 up, the feet 22, the squares 12, the bottom
    strike 48; the price axis stretches between. The gutter widens with the drawing (a third of it, as the sheet's 116
    of 320), so the panel's bars are longer and its strike column keeps its room, while a drawing no wider than the
    sheet's keeps the sheet's gutter to the pixel."""
    sheet = _board()["body"]["kids"][0]
    panel = _board(W=744, wide=True)["body"]["kids"][0]
    assert sheet["attrs"]["viewBox"] == "0 0 320 248" and panel["attrs"]["viewBox"] == "0 0 744 442"
    for svg, H in ((sheet, 248), (panel, 442)):
        texts = {t["text"]: t["attrs"] for t in _find(svg, tag="text")}
        axis = [a for a in (r["attrs"] for r in _find(svg, "axis")) if a["x1"] == "0"][0]
        assert float(axis["y1"]) == H - 36 and float(texts["OPEN 06:30"]["y"]) == H - 22 and float(texts["7,775"]["y"]) == H - 48 + 4
        assert float(_find(svg, "sq")[0]["attrs"]["y"]) == H - 12 and float(texts["7,830"]["y"]) == 24
    assert "H = chartDocked() ? CH_H_WIDE : CH_H" in _fn("paintChart") and "CH_H_WIDE = 442" in _var("CH_H")
    texts = {t["text"]: t["attrs"] for t in _find(panel, tag="text")}
    zero = [a for a in (r["attrs"] for r in _find(panel, "axis")) if a["x1"] != "0"][0]
    assert zero["x1"] == "652" and texts["STRIKE"]["x"] == "554" and texts["CLOSE 13:00 PDT"]["x"] == "475"   # 744 - 91 - 1; the column 98 and the plot 177 in from the zero
    calls = _find(panel, "call")
    assert max(float(c["attrs"]["width"]) for c in calls) == 91.0 and max(float(c["attrs"]["x"]) + float(c["attrs"]["width"]) for c in calls) == 744
    assert [t["attrs"]["x"] for t in _find(panel, tag="text") if t["text"] == "214k"] == ["568", "744"]   # the scale's ends: 652 - 91 + 7, and the edge
    assert "g = Math.max(1, W / CH_W), SIDE = Math.round(CH_SIDE * g)" in _fn("chartSvg") and "CH_W = 320" in _var("CH_SIDE")


def test_the_panel_carries_the_boards_title_and_four_tiles_and_the_sheet_keeps_its_own():
    """Final-Desktop's panel: the title row's right says the day, the price's state as the box says it with the quote's
    time, and the viewer's zone ("Fri 10-09 · live 11:43 · PDT"; after the close "last close"); no price line under it
    (hidden by the stylesheet); the legend's lines inline with the bars first and the walls left out (named on the
    chart and in the tiles); four tiles, the range, the biggest strike with everything traded there, the call wall with
    its calls and the put wall with its puts, each with its distance from the dot, the magnet named on the wall it sits
    at; and the caption without "not advice", which the page's note says. The sheet keeps its day, its two tiles, its
    legend lines and its caption, drawn by the same painter and the one tile builder."""
    live = _board(W=744, wide=True)
    assert live["day"] == "Fri 10-09 · live 11:43 · PDT"
    key, tiles, cap = live["body"]["kids"][1], live["body"]["kids"][2], live["body"]["kids"][3]
    assert [k["attrs"]["class"] for k in key["kids"]] == ["box", "walls", "bars"] and ".ch-key .walls{display:none}" in SPX and ".ch-key .bars{order:-1}" in SPX
    assert [[_flat_text(k) for k in t["kids"]] for t in tiles["kids"]] == [["Range so far", "35 pts", "0.46 of a normal day"], ["Biggest strike", "7,810", "373k traded · 1.5 pts below"],
                                                                           ["Today’s Call Wall", "7,815", "214k calls · 3.5 pts above"], ["Today’s Put Wall · Magnet", "7,810", "171k puts · the magnet"]]
    assert tiles["attrs"]["class"] == "tod ch-tiles" and [t["attrs"]["class"] for t in tiles["kids"]] == ["tile"] * 4
    assert _flat_text(cap) == "Dealers lean against moves · options book 12:58 · not advice" and cap["kids"][-1]["attrs"]["class"] == "ch-advice"
    # the magnet off the put wall: the wall's distance in its place, and no magnet on either name
    apart = _board(W=744, wide=True, book={**NATIVE_ROW, "gex_views": {**NATIVE_ROW["gex_views"], "magnet": 7805.0}})["body"]["kids"][2]
    assert [[_flat_text(k) for k in t["kids"]] for t in apart["kids"][2:]] == [["Today’s Call Wall", "7,815", "214k calls · 3.5 pts above"], ["Today’s Put Wall", "7,810", "171k puts · 1.5 pts below"]]
    # after the close, and without a book: the state and the one tile the sheet shows
    shut = _chart({"now": "2026-10-09T16:20:00-04:00", "card": BOARD_CARD, "bars": BOARD_BARS, "book": None, "spot": 7811.0, "W": 744, "wide": True})
    assert shut["day"] == "Fri 10-09 · last close · PDT"
    assert [[_flat_text(k) for k in t["kids"]] for t in _find(shut["body"], "ch-tiles")[0]["kids"]] == [["Range", "35 pts", "0.46 of a normal day"], ["Magnet", "No book", "options book unavailable"]]
    assert _board(W=744, wide=True, spot=None)["day"] == "Fri 10-09"       # no quote yet: the day alone, as the sheet
    assert _board()["day"] == "Fri 10-09" and len(_board()["body"]["kids"][2]["kids"]) == 2
    assert JS.count("function chTile(") == 1 and "if(!chartDocked()){" in _fn("bookTiles") and "$('chDay').textContent = day ? chartDay(day) : '';" in _fn("paintChart")


def test_a_resize_across_the_breakpoint_closes_an_open_sheet_and_asks_for_the_book():
    """The sheet and the panel are one markup: a window widened past 1024px with the chart sheet open closes it the one
    way sheet.js closes anything (MiraiSheet.close), so no scrim is left under a panel, draws the chart for the panel and
    asks for the book; narrowed back, the panel is the hidden sheet again and nothing is asked for. Standing on the page
    the panel is no dialog: docked, it is a region (the board's "SPX today" panel) with its aria-hidden and aria-modal
    gone, so a screen reader reaches the chart as part of the page; narrowed back, the dialog and both attributes
    return (a phone at load keeps the markup's). sheet.js writes aria-hidden last when the widening closed the sheet,
    on the popstate its history entry unwinds through, so the page docks again on every popstate."""
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FIXED_NOW + WHOLE_DOM
              + "var FETCHED = []; var fetched = fetch; fetch = function(url){ FETCHED.push(url.indexOf('name=') < 0 ? 'spot' : url.split('name=')[1].split('&')[0]); return fetched(url); };" + MAIN_JS
              + "function aria(){ return [ids.chartSheet.attrs['aria-hidden'], ids.chartSheet.attrs['aria-modal'], ids.chartSheet.attrs['role']]; }"
              "setImmediate(function(){ var out = {loaded: FETCHED.slice(), query: !!MQ.change, phone: aria()};"
              " ids.chartSheet.attrs['aria-hidden'] = 'false'; MQ.matches = true; MQ.change();"
              " setImmediate(function(){ out.widened = {closed: MiraiSheet.closed, book: FETCHED.slice(out.loaded.length), chart: ids.chBody.kids.length, aria: aria()};"
              "  MQ.matches = false; MQ.change();"
              "  setImmediate(function(){ out.narrowed = {closed: MiraiSheet.closed, more: FETCHED.length - out.loaded.length - out.widened.book.length, aria: aria()}; console.log(JSON.stringify(out)); }); }); });")
    out = run_node(_NODE, script, input=json.dumps({**{k: MONDAY[k] for k in ("live", "tape", "premarket")}, "now": "2026-09-28T15:40:00-04:00"}),
                   capture_output=True, text=True, timeout=20, env={**os.environ, "TZ": LA})
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["query"] is True and "SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN" not in got["loaded"] and got["phone"] == ["true", "true", "dialog"]   # a phone with the sheet shut never asks
    assert got["widened"] == {"closed": 1, "book": ["SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN"], "chart": 1, "aria": ["false", None, "region"]}
    assert got["narrowed"] == {"closed": 1, "more": 0, "aria": ["true", "true", "dialog"]}
    assert 'id="chartSheet" role="dialog" aria-modal="true" aria-hidden="true"' in SPX     # the markup is the phone's: dockChart rewrites all three
    assert "-webkit-user-select:auto;user-select:auto}" in _wide_css()[1]     # docked, its words select as the page's do
    assert "return {open, isOpen, close: dismiss};" in (M / "sheet.js").read_text()
    assert "window.addEventListener('popstate', dockChart);" in JS and "window.addEventListener('popstate', shut);" in (M / "sheet.js").read_text()
    assert SPX.index('<script src="/m/sheet.js"></script>') < SPX.index(MAIN_JS)     # sheet.js's popstate runs first, so the page's docks last
