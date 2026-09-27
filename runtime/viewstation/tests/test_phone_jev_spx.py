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
          + _fn("foldLive") + "var f = foldLive(D.c); console.log(JSON.stringify([f.kids.map(function(k){ return k.textContent; }), tickers.length]));")
    c = {"row_ts": "2026-09-28T09:31:20-04:00", "hour": None, "marks": {"next_30": "2026-09-28T10:00:00-04:00"},
         "session": {"close": "2026-09-28T16:00:00-04:00", "last_read": "2026-09-28T15:32:00-04:00"}}
    assert _run(js, {"now": "2026-09-28T09:52:00-04:00", "c": c}, LA) == [
        ["30-min read 06:31", "No 30-minute call on this read, next read 07:02"], 0]


def test_a_pool_on_the_phone_is_named_as_the_pool_not_as_the_blend():
    """pool.shown swaps the pool into the headline odds and marks it shown_source; the chart must say so."""
    js = (_var("ODDS_ORDER") + _fn("oddsKeys") + _fn("top1") + _fn("oneAnswer") + _fn("phaseSpan") + _fn("howChart") +
          "var g = howChart(D.h, {used: true, sessions: 19, phase_words: 'lunch, 12:00 to 14:00'}, D.at);"
          "console.log(JSON.stringify(g.kids.filter(function(k){ return /how-k|how-note/.test(k.attrs['class'] || ''); })"
          ".map(function(k){ return k.textContent; })));")
    odds = {"up": 0.2, "down": 0.3, "flat": 0.5}
    h = {"probabilities": odds, "jev": {"probabilities": odds}, "clock": {"probabilities": odds}, "shown_source": "pool_v1"}
    got = _run(js, {"h": h, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00"}, LA)
    assert got[:3] == ["JEV", "Time of day", "Pool"] and got[3].startswith("Shown is the learning loop\u2019s pool")
    blend = _run(js, {"h": {**h, "shown_source": "blend50_exact"}, "at": "2026-09-28T12:31:00-04:00", "now": "2026-09-28T12:35:00-04:00"}, LA)
    assert blend[2] == "Shown" and blend[3].startswith("Shown is half JEV")


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


PRE_FNS = ("fits", "top1", "oddsKeys", "oddsBar", "tag", "skipLine", "ageWord", "mins", "untilWords", "expiryLine",
           "preLeads", "preMissed", "preJevRead", "preUnsent", "preState", "shapeWords", "sumPick", "sumSaid", "preClock", "preCall",
           "checksSvg", "chipFits", "storySide", "storyHead", "storySvg", "preFacts", "paintPre")
PRE_VARS = ("ODDS_ORDER", "ODDS_TRACK_PX", "EXPIRY_TAGS", "PRE_LATE_MIN", "OPENS", "THEN", "CHECKS_LEAD_MIN", "CHIP_W")
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
    assert "PRE_HOURS = ['02:35', '09:35']" in JS
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
    assert parts[-1] == ["tag", "Hands over to the 5-minute opening reads at 06:35. A forecast, graded by the bars, never a call."]
    assert _flat_text(got["main"][3][1]) == "Opening readsFirst 06:35Every 5 min, 10-minute calls"


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
    # a checkpoint only minutes late is not yet missing, and the service's own staleness is said beside it
    assert _page(pre_card("03:35"), et("08:14"))["state"] == ["snapshot, JEV at 05:48 and 06:28"]
    old = pre_card("08:05", freshness={"age_s": 1500, "stale": True})
    assert _page(old, et("08:06"))["state"] == ["snapshot, JEV at 05:48 and 06:28", "futures 25 min old at the read"]
    assert _page(pre_card("09:05"), et("09:06"), ok=False)["state"][0] == "fetch failed, showing the last card"


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


# ---- the pre-market card on the owner's 360px phone

# Widths measured in Chrome at 360 with the shipped face (Plus Jakarta Sans), in Los Angeles, Tokyo, New York and
# Kolkata (a zone name is widest as an offset), each line at the widest form it takes before the hand-over
_PRE_W = {"pre-market read 00:35, 4 hr 45 min ago, open 06:30": 319.23, "BEFORE THE OPEN": 113.08,
          "SNAPSHOT, JEV AT 05:48 AND 06:28": 222.83, "STALE: THE 05:05 READ HAS NOT LANDED": 255.12,
          "NOT SENT: NO KEY ON THIS MACHINE": 227.98,
          "NOT SENT: THIS RUN WAS NOT ASKED TO SEND": 284.97, "NOT SENT: NOTHING TO ASK AT THE 05:48 READ": 293.56, "FUTURES 25 MIN OLD AT THE READ": 208.96,
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
              "NOT SENT: NOTHING TO ASK AT THE 05:48 READ", "FUTURES 25 MIN OLD AT THE READ"):
        assert _PRE_W[w] + chip <= column, w
    assert _PRE_W["OPENS 06:30"] + _px(".clock-top", "gap") + _PRE_W["5 HR 50 MIN TO GO"] <= card
    assert _PRE_W["Read 13:05 GMT+5:30"] + _PRE_W["Checked 19:14 and 19:34"] <= card
    assert "viewBox: '0 0 300 '" in _fn("checksSvg") and "viewBox: '0 0 300 '" in _fn("storySvg")
    assert "'class': 'inplay'" in _fn("checksSvg") and "'class': 'inplay story'" in _fn("storySvg")
