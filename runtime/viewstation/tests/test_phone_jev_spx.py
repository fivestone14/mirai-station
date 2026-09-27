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
                                          "leftWords", "callWords", "laneLeads", "svgEl", "lastLaneRead", "hhmm", "marketClock",
                                          "marketDay", "sentence"))
              + _var("VIEWER_FMT") + _var("NS") + _var("ROW_H") + js)
    out = subprocess.run([_NODE, "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=20,
                         env={**os.environ, "TZ": tz})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def call(read, mark, pick, p, **grade):
    minutes = (int(mark[:2]) * 60 + int(mark[3:])) - (int(read[:2]) * 60 + int(read[3:]))
    return {"read": f"2026-09-28T{read}:00-04:00", "mark": f"2026-09-28T{mark}:00-04:00",
            "minutes": minutes, "pick": pick, "p": p, **grade}


MORNING = [call("09:50", "10:00", "flat", 0.42), call("09:45", "09:55", "up_small", 0.38),
           call("09:40", "09:50", "flat", 0.51, outcome="flat", hit=True),
           call("09:35", "09:45", "down_small", 0.33, outcome="up_small", hit=False)]


# ---- the wiring


def test_the_page_reads_the_spx_cards_and_keeps_the_sndk_pages_modes():
    assert "var URL_ = '/api/raw/file?root=state&path=spx_jev/latest.json';" in JS
    assert "var TAPE_URL = '/api/raw/file?root=state&path=spx_jev/lanes/tape/latest.json';" in JS
    assert "fetch(URL_, {cache:'no-store'})" in JS and "fetch(TAPE_URL, {cache:'no-store'})" in JS
    assert "shownLeads = laneLeads(tape, Date.now(), marketDay());" in _fn("paint")
    assert "main.appendChild(laneCard(tape, tapeOk));" in _fn("paint") and "main.appendChild(foldLive(c));" in _fn("paint")
    assert "sumCard(c, main);" in _fn("paint") and "openingDone(tape)" in _fn("paint")
    assert "var ex = expiryLine(c); if(ex) main.appendChild(ex);" in _fn("paint")
    assert "laneLeads(tape, Date.now(), marketDay()) !== shownLeads" in _fn("tick")
    assert "setInterval(function(){ if(!document.hidden){ poll(); pollTape(); tick(); } }, POLL_MS);" in JS
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
    js = _fn("fitting") + _fn("fits") + _fn("callsSvg") + "console.log(JSON.stringify(dump(callsSvg(D.calls, Date.parse(D.at)))));"
    svg = _run(js, {"calls": MORNING, "at": "2026-09-28T09:51:00-04:00"}, TOKYO)
    texts = [k for k in svg["kids"] if k["tag"] == "text"]
    assert [t["text"] for t in texts if t["attrs"].get("class") == "t-axis"][:3] == ["22:35", "22:45", "22:55"]
    hits = [k for k in svg["kids"] if k["tag"] == "rect" and k["attrs"].get("class") == "hit"]
    assert hits[1]["attrs"]["aria-label"] == "The 22:45 call, Up small 38%: its odds and result"
    side = [t["text"] + "".join(k["text"] for k in t["kids"]) for t in texts if t["attrs"].get("class") == "t-side"]
    assert side == ["9 min left", "4 min left", "Was Flat · Right", "Was Up small · Wrong"]
    assert all(float(t["attrs"]["x"]) <= 300 for t in texts)


def _sheet(c, now, tz=LA):
    stubs = """
      var nodes = {csTitle: el('div'), csBody: el('div')};
      function $(id){ return nodes[id]; }
      var MiraiSheet = {open: function(){ nodes.opened = true; }};
      function tag(t){ return el('div', 'tag', sentence(t)); }
      function bar(name, p, pick){ var b = el('div', 'bar' + (pick ? ' pick' : '')); b.textContent = name.replace(/_/g, ' ') + ' ' + Math.round(p * 100) + '%'; return b; }
    """
    js = (stubs + _var("ODDS_ORDER") + _var("ODDS_TRACK_PX") + _fn("oddsKeys") + _fn("oddsBar") + _fn("unitsWords") + _fn("movedWords") +
          _fn("openCall") + "openCall(D.c, {}); console.log(JSON.stringify({title: nodes.csTitle.textContent, body: dump(nodes.csBody)}));")
    return _run(js, {"c": c, "now": now}, tz)


def test_the_sheet_gives_the_result_in_index_points_at_the_viewers_time():
    c = {**call("09:45", "09:55", "up_small", 0.38, outcome="up_big", hit=False, moved={"realized_points": 8.22, "realized_units": 1.962}),
         "odds": {"down_big": 0.05, "down_small": 0.12, "flat": 0.3, "up_small": 0.38, "up_big": 0.1, "unsure": 0.05}}
    got = _sheet(c, "2026-09-28T10:02:00-04:00")
    assert got["title"] == "The 06:45 call · looks 10 min ahead"
    assert [_flat_text(k) for k in got["body"]["kids"][0]["kids"]] == [
        "WrongResult", "It ended Up big. The call said Up small 38%.",
        "Price ended 8.22 points higher at 06:55 than at the read, 1.96 tape units."]
    small = {**c, "moved": {"realized_points": -1.5, "realized_units": 0.358}}
    assert _flat_text(_sheet(small, "2026-09-28T10:02:00-04:00")["body"]["kids"][0]["kids"][2]) == \
        "Price ended 1.50 points lower at 06:55 than at the read, 0.36 of a tape unit."
    live = {**call("10:32", "11:02", "flat", 0.7), "odds": {"up": 0.2, "down": 0.1, "flat": 0.7}}
    assert [_flat_text(k) for k in _sheet(live, "2026-09-28T10:40:00-04:00", TOKYO)["body"]["kids"][0]["kids"]] == \
        ["Open22 min left", "It is graded at 00:02, against the bar at that minute."]


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
          + _fn("nextRead") + _fn("ageWord") + _fn("subLine") + "console.log(JSON.stringify(subLine(D.c, D.m)));")
    c = {"row_ts": "2026-09-28T11:01:56-04:00", "labels": 43,
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T11:14:00-04:00", "c": c, "m": 12}, LA) == "row 08:01, 12 min ago, 43 labels, next read 08:32"
    last = {**c, "row_ts": "2026-09-28T15:31:56-04:00"}
    assert _run(js, {"now": "2026-09-28T16:10:00-04:00", "c": last, "m": 38}, LA) == "after the close, last row 12:31, 43 labels"
    assert _run(js, {"now": "2026-09-28T15:50:00-04:00", "c": last, "m": 18}, TOKYO).startswith("row 04:31, 18 min ago")


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
    assert _var("ODDS_TRACK_PX").strip() == "var ODDS_TRACK_PX = 290, LETTER_PX = 6.4;" and 290 <= card


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


def test_the_opening_lane_is_drawn_while_the_30_minute_card_cannot_be_read():
    """Monday's first morning with no 30-minute card on file: the open 5-minute call still leads, drawn alone;
    once a 30-minute card has been read, paint draws both and this draws nothing."""
    js = ("var last = D.last, tape = D.tape, tapeOk = true, tickers = [], shownLeads = false, MAIN = el('div', 'main');"
          "function $(id){ return MAIN; } function clearLoading(){} function laneCard(t, ok){ return el('div', 'lane', t.row_ts); }"
          + _fn("laneOnly") +
          "var drew = laneOnly(); console.log(JSON.stringify([drew, shownLeads, MAIN.kids.map(function(k){ return k.textContent; })]));")
    tape = {"lane": "tape", "row_ts": "2026-09-28T10:00:00-04:00", "calls": [call("10:00", "10:10", "flat", 0.4)]}
    drew = _run(js, {"now": "2026-09-28T10:05:00-04:00", "last": None, "tape": tape})
    assert drew == [True, True, ["opening · a call every 5 min", "2026-09-28T10:00:00-04:00", "30-min cardnot read yet"]]
    assert _run(js, {"now": "2026-09-28T10:05:00-04:00", "last": {"row_ts": "x"}, "tape": tape})[0] is False
    assert _run(js, {"now": "2026-09-28T10:11:00-04:00", "last": None, "tape": tape})[0] is False    # the call has closed


def test_the_blend_note_names_the_phase_by_its_hours_in_the_viewers_zone():
    """clock.py names the phase in New York words and hours; in Los Angeles "lunch" would sit beside 09:00, so
    the page keeps only the hours, redrawn in the viewer's zone."""
    js = (_var("ODDS_ORDER") + _fn("oddsKeys") + _fn("top1") + _fn("oneAnswer") + _fn("phaseSpan") + _fn("howChart") +
          "console.log(JSON.stringify(D.w.map(function(w){ return howChart(D.h, {used: true, sessions: 19, phase_words: w}, D.at)"
          ".kids.slice(-1)[0].textContent; })));")
    h = {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}, "jev": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}},
         "clock": {"probabilities": {"up": 0.2, "down": 0.3, "flat": 0.5}}}
    got = _run(js, {"h": h, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00",
                    "w": ["lunch, 12:00 to 14:00", "the afternoon, 14:00 to the close", "the opening half hour, before 10:00"]}, LA)
    assert got == [f"Shown is half JEV, half how this time of day ({span}) went over the last 19 sessions."
                   for span in ("09:00 to 11:00", "11:00 to the close", "before 07:00")]
