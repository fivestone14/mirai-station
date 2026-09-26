"""The JEV tab's two modes and its calls in play. While today's newest opening-lane call is still open, the
lane's 5-minute call leads and the 30-minute call folds to one line; once it closes, the 30-minute call
leads again. Every call is drawn on one clock from its read to its mark, from the calls the service puts
on each card (sndk_jev.service.day_calls). The rules are functions in the page, lifted out by name and run
in node against a stand-in DOM, so the test holds what the page draws rather than how it is spelt."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

M = Path(__file__).resolve().parents[1] / "static" / "m"
JEV = (M / "jev.html").read_text()
JS = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", JEV))
_NODE = shutil.which("node")

# a stand-in for the few DOM calls the drawing makes: elements keep their attributes, children and text
FAKE_DOM = """
function Node(tag){ this.tag = tag; this.attrs = {}; this.kids = []; this._t = ''; }
Node.prototype.setAttribute = function(k, v){ this.attrs[k] = String(v); };
Node.prototype.appendChild = function(n){ this.kids.push(n); return n; };
Node.prototype.addEventListener = function(){};
Object.defineProperty(Node.prototype, 'textContent', {get: function(){ return this._t + this.kids.map(function(k){ return k.textContent; }).join(''); },
                                                      set: function(v){ this._t = String(v); this.kids = []; }});
var document = {createElementNS: function(ns, tag){ return new Node(tag); }, createElement: function(tag){ return new Node(tag); }};
function el(tag, cls, text){ var e = document.createElement(tag); if(cls) e.attrs['class'] = cls; if(text != null) e.textContent = text; return e; }
function dump(n){ return {tag: n.tag, attrs: n.attrs, text: n._t, kids: n.kids.map(dump)}; }
"""


def _fn(name):
    m = re.search(r"\n  function %s\(.*?(?=\n  (?:function |var |//|[a-z]))" % re.escape(name), JS, re.S)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _var(name):
    m = re.search(r"\n  var %s = .*?;" % re.escape(name), JS)
    assert m, f"{name} is gone from the page"
    return m.group(0)


def _run(js, data=None):
    if not _NODE:
        pytest.skip("node is not installed")
    script = ("const D=JSON.parse(require('fs').readFileSync(0,'utf8'));" + FAKE_DOM
              + "".join(_fn(f) for f in ("clock", "cap", "hhmmPlus", "pct", "words", "startOf", "endOf", "leftWords", "callWords",
                                          "laneLeads", "svgEl", "lastLaneRead", "hhmm"))
              + _var("NS") + _var("ROW_H") + js)
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def call(read, mark, pick, p, **grade):
    minutes = (int(mark[:2]) * 60 + int(mark[3:])) - (int(read[:2]) * 60 + int(read[3:]))
    return {"read": f"2026-09-28T{read}:00-04:00", "mark": f"2026-09-28T{mark}:00-04:00",
            "minutes": minutes, "pick": pick, "p": p, **grade}


# the 09:51 morning of the mockup: two calls open, two graded
MORNING = [call("09:50", "10:00", "flat", 0.42), call("09:45", "09:55", "up_small", 0.38),
           call("09:40", "09:50", "flat", 0.51, outcome="flat", hit=True),
           call("09:35", "09:45", "down_small", 0.33, outcome="up_small", hit=False)]


def test_the_lane_leads_only_while_todays_newest_call_is_open():
    lane = {"lane": "tape", "row_ts": "2026-09-28T09:50:00-04:00", "calls": MORNING}
    cases = [
        (lane, "2026-09-28T09:51:00-04:00", "2026-09-28", True),
        (lane, "2026-09-28T09:59:59-04:00", "2026-09-28", True),
        (lane, "2026-09-28T10:00:00-04:00", "2026-09-28", False),          # its mark: the 30-minute call leads again
        (lane, "2026-09-29T09:51:00-04:00", "2026-09-29", False),          # yesterday's card
        ({**lane, "calls": []}, "2026-09-28T09:51:00-04:00", "2026-09-28", False),   # no call on the card
        ({**lane, "lane": None}, "2026-09-28T09:51:00-04:00", "2026-09-28", False),
        (lane, "2026-09-28T09:51:00-04:00", None, False),                  # no market calendar on this phone
    ]
    got = _run("console.log(JSON.stringify(D.map(function(c){ return laneLeads(c[0], Date.parse(c[1]), c[2]); })));",
               [[c, now, today] for c, now, today, _ in cases])
    assert got == [c[3] for c in cases]


def test_minutes_left_and_what_a_call_says_beside_its_bar():
    now = "2026-09-28T09:51:00-04:00"
    js = """
      var now = Date.parse(D.now);
      console.log(JSON.stringify({
        left: [540, 61, 60, 30, 0, -5].map(function(s){ return leftWords(now + s * 1000, now); }),
        words: D.calls.map(function(c){ return callWords(c, now); }),
        plus: [hhmmPlus('09:32', -4), hhmmPlus('10:30', 10), hhmmPlus('09:55', 10)]}));"""
    got = _run(js, {"now": now, "calls": MORNING + [call("09:30", "09:40", "flat", 0.5), call("09:30", "09:40", "flat", 0.5, closed="halted window")]})
    assert got["left"] == ["9 min left", "2 min left", "Under 1 min left", "Under 1 min left", None, None]
    assert got["words"] == [{"text": "9 min left"}, {"text": "4 min left"},
                            {"text": "Was Flat · ", "strong": "Right"}, {"text": "Was Up small · ", "strong": "Wrong"},
                            {"text": "Grade pending"}, {"text": "Not graded"}]
    assert got["plus"] == ["09:28", "10:40", "10:05"]


def _drawing(calls, now):
    js = (_fn("fitting") + _fn("fits") + _fn("callsSvg") +
          "console.log(JSON.stringify(dump(callsSvg(D.calls, Date.parse(D.now)))));")
    return _run(js, {"calls": calls, "now": now})


def _rows(svg):
    rects = [k for k in svg["kids"] if k["tag"] == "rect" and k["attrs"].get("class") != "hit"]
    texts = [k for k in svg["kids"] if k["tag"] == "text"]
    side = [t for t in texts if t["attrs"].get("class") == "t-side"]
    return rects, texts, side


def test_the_morning_draws_two_open_calls_overlapping_and_the_graded_ones_with_their_outcome():
    svg = _drawing(MORNING, "2026-09-28T09:51:00-04:00")
    rects, texts, side = _rows(svg)
    assert [r["attrs"]["class"] for r in rects] == ["b-now", "b-open", "b-done", "b-done"]
    assert [t["text"] + "".join(k["text"] for k in t["kids"]) for t in side] == [
        "9 min left", "4 min left", "Was Flat · Right", "Was Up small · Wrong"]
    # the two open bars overlap on the clock: the newer starts before the older ends
    x = lambda r: (float(r["attrs"]["x"]), float(r["attrs"]["x"]) + float(r["attrs"]["width"]))
    (a0, b0), (a1, b1) = x(rects[0]), x(rects[1])
    assert a1 < a0 < b1 < b0
    # the now line crosses exactly the open calls
    now = next(k for k in svg["kids"] if k["tag"] == "line" and k["attrs"].get("class") == "now")
    nx = float(now["attrs"]["x1"])
    assert [x(r)[0] <= nx < x(r)[1] for r in rects] == [True, True, False, False]
    axis = [t["text"] for t in texts if t["attrs"].get("class") == "t-axis"]
    assert axis[:3] == ["09:35", "09:45", "09:55"]
    assert all(float(t["attrs"]["x"]) <= 300 for t in texts)
    assert "4 calls on one clock, 2 still open" == svg["attrs"]["aria-label"]


def test_the_day_draws_thirty_minute_calls_end_to_end_one_open():
    day = [call("11:02", "11:32", "flat", 0.64), call("10:32", "11:02", "flat", 0.71, outcome="flat", hit=True),
           call("10:02", "10:32", "up", 0.48, outcome="down", hit=False)]
    svg = _drawing(day, "2026-09-28T11:14:00-04:00")
    rects, texts, side = _rows(svg)
    assert [r["attrs"]["class"] for r in rects] == ["b-now", "b-done", "b-done"]
    assert side[0]["text"] == "18 min left"
    ends = [(float(r["attrs"]["x"]), float(r["attrs"]["x"]) + float(r["attrs"]["width"])) for r in rects]
    assert abs(ends[1][1] - ends[0][0]) < 0.2 and abs(ends[2][1] - ends[1][0]) < 0.2      # end to end, no overlap
    assert [t["text"] for t in texts if t["attrs"].get("class") == "t-axis"][:3] == ["10:02", "10:32", "11:02"]


def test_the_page_is_wired_to_the_cards_and_draws_nothing_it_cannot_read():
    assert 'id="tape"' not in JEV and ".strip" not in JEV, "the old strip is gone"
    assert "var TAPE_URL = '/api/raw/file?root=state&path=jev/lanes/tape/latest.json';" in JS
    assert "fetch(TAPE_URL, {cache:'no-store'})" in JS
    assert "shownLeads = laneLeads(tape, Date.now(), marketDay());" in _fn("paint")
    assert "main.appendChild(laneCard(tape, tapeOk));" in _fn("paint") and "main.appendChild(foldLive(c));" in _fn("paint")
    assert "sumCard(c, main);" in _fn("paint") and "openingDone(tape)" in _fn("paint")
    assert "laneLeads(tape, Date.now(), marketDay()) !== shownLeads" in _fn("tick"), "the clock switches the mode between cards"
    assert "setInterval(function(){ if(!document.hidden){ poll(); pollTape(); tick(); } }, POLL_MS);" in JS
    assert "x.closed_out_at" in _fn("sig"), "the close-out's refresh is a new lane card"
    assert "LANE_HOURS = ['09:33', '16:05']" in JS and "within(LANE_HOURS)" in _fn("pollTape")
    assert "innerHTML" not in JS
    # the tally and the hand-back come from the card, never from a count or a time typed into the page
    assert "t.tally" in _fn("openingDone") and "sch.reads[sch.reads.length - 1], sch.looks_ahead_min" in _fn("laneCard")


def test_a_long_horizon_draws_only_the_calls_that_fit_at_a_readable_width():
    """Four 30-minute calls would squeeze each bar under MIN_BAR; the page draws the newest three, and every
    name inside a bar fits it (a long one drops its percentage rather than run into the words beside it)."""
    day = [call("11:32", "12:02", "down_small", 0.43), call("11:02", "11:32", "flat", 0.64),
           call("10:32", "11:02", "flat", 0.71, outcome="flat", hit=True), call("10:02", "10:32", "up", 0.48, outcome="down", hit=False)]
    svg = _drawing(day, "2026-09-28T11:40:00-04:00")
    rects, texts, side = _rows(svg)
    assert len(rects) == 3 and all(float(r["attrs"]["width"]) >= 70 for r in rects)
    names = [t for t in texts if t["attrs"].get("class", "").startswith("t-") and t["attrs"]["class"] not in ("t-side", "t-axis", "t-now-l")]
    for t, r in zip(names, rects):
        assert len(t["text"]) * 6.2 + 8 <= float(r["attrs"]["width"])
    assert names[0]["text"] == "Down small"                                  # "Down small 43%" does not fit its bar
    assert "3 calls on one clock, 1 still open" == svg["attrs"]["aria-label"]


def test_a_call_that_ends_past_the_close_says_it_is_never_graded_and_the_card_marks_are_used():
    got = _run("console.log(JSON.stringify(callWords(D, Date.now())));",
               {"read": "2026-09-28T15:48:00-04:00", "mark": None, "minutes": 30, "pick": "flat", "p": 0.9})
    assert got == {"text": "Never graded"}
    # the clocks and the 60-minute line take the card's marks (grade.mark_at), never a sum of their own
    assert "marks.next_30" in _fn("sumCard") and "marks.next_60" in _fn("sumCard") and "it ends past the close, so it is never graded" in _fn("sumCard")
    assert "clockBlock(newest.read, newest.minutes, newest.mark)" in _fn("laneCard")
    assert "if(c.calls && c.calls.length) he.appendChild(inPlay(c.calls));" in _fn("sumCard"), "an errored sum still shows the calls"


def test_the_last_lane_read_is_known_even_when_the_bars_run_a_minute_behind():
    reads = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10", "10:15", "10:20", "10:25", "10:30"]
    # a read stamped a minute behind the 10:30 fire is the last; a late 10:25 fire stamped 10:27 is not
    cards = [{"row_ts": f"2026-09-28T{t}:00-04:00", "schedule": {"reads": reads}} for t in ("10:30", "10:29", "10:27", "10:25", "10:24")]
    cards.append({"row_ts": "2026-09-28T10:20:00-04:00", "schedule": {"reads": reads}, "closed_out_at": "2026-09-28T14:42:10+00:00"})
    got = _run("console.log(JSON.stringify(D.map(lastLaneRead)));", cards)
    assert got == [True, True, False, False, False, True]


def test_after_the_close_the_days_last_row_is_not_called_stale():
    js = ("function marketClock(){ return D.mc; } function nextRead(){ return null; } var GONE_MIN = 60, STALE_MIN = 35, ROW_LEAD_MIN = 4, LAST_READ_DEFAULT = '15:32';"
          + _fn("ageWord") + _fn("lastRead") + _fn("subLine") +
          "console.log(JSON.stringify([subLine(D.c, 38), (function(){ D.mc = {h: 15, m: 50}; return subLine(D.c, 18); })()]));")
    got = _run(js, {"mc": {"h": 16, "m": 10}, "c": {"row_ts": "2026-09-28T15:31:56-04:00", "labels": 59,
                                                     "session": {"close": "16:00", "last_read": "15:32"}}})
    assert got[0] == "after the close, last row 15:31, 59 labels"
    assert got[1].startswith("row 15:31, 18 min ago")


def test_every_call_row_is_a_tap_target_that_opens_the_sheet():
    svg = _drawing(MORNING, "2026-09-28T09:51:00-04:00")
    hits = [k for k in svg["kids"] if k["tag"] == "rect" and k["attrs"].get("class") == "hit"]
    assert len(hits) == 4 and svg["kids"][-4:] == hits, "the tap targets sit on top of the drawing"
    assert all(h["attrs"]["role"] == "button" and h["attrs"]["tabindex"] == "0" and h["attrs"]["aria-controls"] == "callSheet" for h in hits)
    assert [float(h["attrs"]["height"]) for h in hits] == [30.0] * 4 and all(h["attrs"]["width"] == "300" for h in hits)
    assert hits[1]["attrs"]["aria-label"] == "The 09:45 call, Up small 38%: its odds and result"
    assert "hit.addEventListener('click', function(){ openCall(c, hit); });" in _fn("callsSvg")
    # the sheet sheet.js opens: its markup, its close control, and sheet.js loaded before the page's script
    assert 'id="callSheet" role="dialog" aria-modal="true" aria-hidden="true"' in JEV
    assert '<button class="sh-close" type="button" data-sheet-close>Close</button>' in JEV
    assert JEV.index('<script src="/m/sheet.js"></script>') < JEV.index("'use strict'")


def _sheet(c, now):
    stubs = """
      var nodes = {csTitle: el('div'), csBody: el('div')};
      function $(id){ return nodes[id]; }
      var MiraiSheet = {open: function(){ nodes.opened = true; }};
      function sentence(s){ s = String(s); return s.charAt(0).toUpperCase() + s.slice(1); }
      function tag(t){ return el('div', 'tag', sentence(t)); }
      function bar(name, p, pick){ var b = el('div', 'bar' + (pick ? ' pick' : '')); b.textContent = name.replace(/_/g, ' ') + ' ' + Math.round(p * 100) + '%'; return b; }
      Date.now = function(){ return Date.parse(D.now); };
    """
    js = (stubs + _var("ODDS_ORDER") + _fn("movedWords") + _fn("openCall") +
          "openCall(D.c, {}); console.log(JSON.stringify({title: nodes.csTitle.textContent, opened: !!nodes.opened, body: dump(nodes.csBody)}));")
    return _run(js, {"c": c, "now": now})


def _flat(n):
    return [n["text"] + "".join(_flat_text(k) for k in n["kids"])] if n["kids"] == [] else [c for k in n["kids"] for c in _flat(k)]


def _flat_text(n):
    return n["text"] + "".join(_flat_text(k) for k in n["kids"])


def test_the_sheet_puts_the_result_first_then_every_options_odds():
    c = {**call("09:45", "09:55", "up_small", 0.38, outcome="down_small", hit=False,
                moved={"realized_dollars": -9.2, "realized_units": 0.418}),
         "odds": {"down_big": 0.05, "down_small": 0.12, "flat": 0.3, "up_small": 0.38, "up_big": 0.1, "unsure": 0.05}}
    got = _sheet(c, "2026-09-28T10:02:00-04:00")
    assert got["opened"] and got["title"] == "The 09:45 call \u00b7 looks 10 min ahead"
    result, odds, note = got["body"]["kids"]
    assert result["attrs"]["class"] == "sh-caveat cs-result"                       # the result comes first
    assert [_flat_text(k) for k in result["kids"]] == [
        "WrongResult", "It ended Down small. The call said Up small 38%.",
        "Price ended $9.20 lower at 09:55 than at the read, 0.42 of a tape unit."]
    rows = [(_flat_text(k), k["attrs"]["class"]) for k in odds["kids"][1:]]
    assert rows == [("Up big 10%", "bar"), ("Up small 38%", "bar pick"), ("Flat 30%", "bar"), ("Down small 12%", "bar"),
                    ("Down big 5%", "bar"), ("Unsure 5%", "bar")]
    assert _flat_text(note) == "A forecast, graded by the bars, never a call."


def test_the_sheet_says_where_an_ungraded_call_stands():
    base = {"odds": {"up": 0.2, "down": 0.1, "flat": 0.7}}
    live_right = {**call("10:02", "10:32", "flat", 0.7, outcome="flat", hit=True, moved={"realized_sigma": 0.034}), **base}
    got = _sheet(live_right, "2026-09-28T11:00:00-04:00")
    assert [_flat_text(k) for k in got["body"]["kids"][1]["kids"][1:]] == ["Up 20%", "Down 10%", "Flat 70%"]   # the card's own order
    assert [_flat_text(k) for k in got["body"]["kids"][0]["kids"]] == [
        "RightResult", "It ended Flat. The call said Flat 70%.",
        "Price ended 0.03 of a normal day\u2019s move higher at 10:32 than at the read."]
    cases = [({**call("10:32", "11:02", "flat", 0.7), **base}, "2026-09-28T10:40:00-04:00", ["Open22 min left", "It is graded at 11:02, against the bar at that minute."]),
             ({**call("10:32", "11:02", "flat", 0.7), **base}, "2026-09-28T11:05:00-04:00", ["Grade pending", "The window closed at 11:02. The grade comes with the next run."]),
             ({**call("10:32", "11:02", "flat", 0.7, closed="halted window: bars missing around the mark on a finished day"), **base},
              "2026-09-28T12:00:00-04:00", ["Not graded", "Halted window: bars missing around the mark on a finished day."]),
             ({**call("15:48", "16:18", "flat", 0.7), "mark": None, **base}, "2026-09-28T15:50:00-04:00",
              ["Never graded", "It ends past the close, so the bars never grade it."])]
    for c, now, want in cases:
        assert [_flat_text(k) for k in _sheet(c, now)["body"]["kids"][0]["kids"]] == want


def test_every_line_on_the_cards_starts_with_a_capital():
    """All-lowercase lines read as unfinished. Notes and skip reasons go through the sentence helpers, and
    the words the page writes itself start with a capital."""
    assert JS.count("el('div', 'tag', ") == 1 and JS.count("el('div', 'skip', ") == 1, "a note or a skip reason is written past the helpers"
    assert "function tag(text){ return el('div', 'tag', sentence(text)); }" in JS
    for word in ("'Looks '", "'Read '", "'Graded '", "'Window closed'", "'Never graded'", "'One tape unit $'", "'Flat within $'",
                 "'Big beyond $'", "'A big move '", "'Calls in play \\u00B7 '"):
        assert word in JS, word
    for lower in ("'looks '", "'read ' + clock", "'graded ' + clock", "'window closed'", "'one tape unit $'", "'a big move '"):
        assert lower not in JS, lower


def test_a_card_without_marks_keeps_its_times_in_market_time():
    """Friday's card on the station predates the marks; the fallback must keep the read's own offset."""
    got = _run(_fn("plusIso") + "console.log(JSON.stringify([plusIso(D[0], 30), plusIso(D[1], 60), plusIso('', 30)]));",
               ["2026-09-25T09:31:56.103349-04:00", "2026-11-27T12:02:10-05:00"])
    assert got == ["2026-09-25T10:01:00-04:00", "2026-11-27T13:02:00-05:00", None]
    assert "toISOString" not in JS, "a UTC string would read five hours off in clock()"
