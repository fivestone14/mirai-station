"""Intent conformance — the documents that say what SNDK Pro is for, held
against what the code does.

Every test here reads its side of the contract out of a document on each run:
the README, docs/sndk-payload-inventory.md, the pipeline map
(runtime/viewstation/static/pipeline.html and the station's _PL_NODES), and the
rulebook the model is sent. Nothing a document lists is copied into this file,
so a change made to one side alone fails here and the same change made to the
document and the code together passes. A test that pins a number pins the
number the document states, not a copy of the constant.
"""
import html
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import atomic_io
import sndk_bars
import sndk_board as B
import sndk_feed
import sndk_read as SR
import synth
from synth import board_row as mkrow, mkrows, flat_bars, T0
from synth import write_prior, at, fronted, DAY as CARRIED_DAY, YEST
from synth import _every_scene_shape
# the real model calls, for the no-tools test that stubs the subprocess under them
from synth import _REAL_CALL_THE_MODEL, _REAL_CALL_THE_MODEL_V2

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "skills" / "sndk-pro" / "README.md").read_text()
INVENTORY = (ROOT / "docs" / "sndk-payload-inventory.md").read_text()
PIPELINE = (ROOT / "runtime" / "viewstation" / "static" / "pipeline.html").read_text()
STATION = (ROOT / "runtime" / "viewstation" / "static" / "index.html").read_text()


# ---------------------------------------------------------------- the documents
_JS_STR = r"'((?:[^'\\]|\\.)*)'"


def _schema_rows(name):
    """[(depth, property, type, what)] of the pipeline map's S.<name> card."""
    body = re.search(r"\bS\.%s = \{.*?rows:\[(.*?)\n    \]\};" % name, PIPELINE, re.S).group(1)
    return [(int(d), p, t, w) for d, p, t, w in re.findall(r"\[(\d),%s,%s,%s\]" % ((_JS_STR,) * 3), body)]


def _names(prop, suffixed=True):
    """The field names one schema cell lists: 'a.b · c (+_at)' is a.b, a.c,
    a.b_at and a.c_at. A suffix counts only where the cell spells it out."""
    out, parent = [], None
    for part in prop.split(" · "):
        part = re.sub(r"\(.*?\)", "", part).replace("[]", "").strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_.]*", part):
            continue
        if "." in part:
            parent = part.rsplit(".", 1)[0]
        elif parent:
            part = f"{parent}.{part}"
        out.append(part)
    suffixes = re.findall(r"\+(_[a-z_]+)", prop) if suffixed else []
    return out + [n + s for n in out for s in suffixes]


def _board_schema():
    """What the pipeline map says the Strikes Payload holds, as dotted paths,
    and what it says the model replies with. A depth-0 name is a path from the
    top of the payload; a deeper row hangs under the row above it, dotted or
    not, so a field drawn under the wrong block names a path that is not there."""
    paths, reply, parents, in_reply = set(), set(), {}, False
    for depth, prop, _type, _what in _schema_rows("board"):
        if prop.startswith("—"):
            in_reply = True
            continue
        names = _names(prop)
        if in_reply:
            reply.update(names)
            continue
        for n in names:
            paths.add(n if depth == 0 else f"{parents[depth - 1]}.{n}")
        if names:
            last = _names(prop, suffixed=False)[-1]
            parents[depth] = last if depth == 0 else f"{parents[depth - 1]}.{last}"
    return paths, reply


def _documented_words():
    """Every identifier the intent sources use for the payload: the README, the
    inventory, every schema card on the pipeline map (with the suffixes a cell
    spells out), and the rulebook. The inventory's shorthand `up/down_dollars`
    names both fields."""
    cards = " ".join(f"{p} {w} {' '.join(_names(p))}"
                     for name in re.findall(r"\bS\.([a-z]+) = \{", PIPELINE)
                     for _, p, _, w in _schema_rows(name))
    text = " ".join((README, INVENTORY, cards, B.DOCTRINE_V2))
    text = re.sub(r"\b([a-z]+)/([a-z]+)(_[a-z_]+)", r"\1\3 \2\3", text)
    return set(re.findall(r"[a-z_][a-z0-9_]*", text))


def _rulebook_names():
    """(field names the rulebook points at in backticks, the reply keys its
    OUTPUT section asks for)."""
    named = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", B.DOCTRINE_V2))
    output = B.DOCTRINE_V2[B.DOCTRINE_V2.index("\nOUTPUT."):]
    return named, set(re.findall(r'"([a-z_]+)":', output))


def _stores():
    """The rows of the map's "Where it is saved" table, each with a regex over
    paths relative to state/ and the code its writer column names."""
    section = PIPELINE[PIPELINE.index('<div class="stores">'):]
    section = section[:section.index("</table>")]
    out = []
    for cls, body in re.findall(r'<tr(?: class="([a-z ]+)")?>(.*?)</tr>', section, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
        if len(cells) < 7:
            continue
        writer = re.search(r"\b(sndk_[a-z]+|snapshot)(?:\.([a-z_]+))?\b", cells[1])
        out.append({"file": cells[0], "writer": cells[1], "holds": cells[3], "readers": cells[5],
                    "module": writer.group(1) if writer else None,
                    "function": writer.group(2) if writer else None,
                    "dead": "dead" in cls.split(),
                    "code_free": "no code writes it" in cells[1],
                    "pattern": _store_pattern(cells[0])})
    return out


def _store_pattern(spec):
    """'sndk_rag/slices/<day>.jsonl · summaries.jsonl' — the later names sit in
    the first name's top folder, every file is named in full, and a name ending
    in / is the whole folder."""
    pieces = spec.split(" · ")
    top = pieces[0].split("/")[0] + "/" if "/" in pieces[0].strip("/") else ""
    alts = []
    for i, piece in enumerate(pieces):
        rel = piece if i == 0 or piece.startswith("/") else top + piece
        rx = re.escape(rel).replace(re.escape("<day>"), r"\d{4}-\d{2}-\d{2}")
        rx = rx.replace(re.escape("<sha>"), "[0-9a-f]+").replace(r"\*", "[^/]*")
        if rel.endswith("/"):
            rx += ".+"
        alts.append(rx)
    return re.compile("^(?:%s)$" % "|".join(alts))


def _station_store_folders():
    nodes = STATION[STATION.index("const _PL_NODES=["):STATION.index("const _PL_EDGES=[")]
    return set(re.findall(r"state/(sndk_[a-z_]+)/", nodes))


def _pipeline_note(node):
    body = re.search(r"\b%s:\{name:.*?note:\[(.*?)\]\}" % node, PIPELINE, re.S).group(1)
    return dict(re.findall(r"\['([^']+)',%s\]" % _JS_STR, body))


def _numbers(text):
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]


# ------------------------------------------------------------ the payloads built
_OPEN = datetime(2026, 7, 28, 9, 30, tzinfo=ET)      # a Tuesday; the front weekly is Friday's
_GRID = [1150.0 + 25 * i for i in range(13)]


def _price_at(minute):
    """The session's price: an opening box, a break up that comes back inside,
    then — after the first read — 1300 crossed three times."""
    if minute < 30:
        return 1290.0 + (minute % 4) - 1.5
    for start, price in ((70, 1302.0), (67, 1312.0), (63, 1290.0), (60, 1310.0), (34, 1290.0), (30, 1300.0)):
        if minute >= start:
            return price


def _scan(minute, open_at=_OPEN):
    """A diary row at `minute` after the open, the book re-served from cache
    every other scan, as the feed does."""
    ts = open_at + timedelta(minutes=minute)
    oi = {k: (600, 300) if k == 1300.0 else (300, 150) if k == 1250.0 else
          (240, 120) if k == 1350.0 else (60, 30) for k in _GRID}
    vol = {k: (c // 10 + minute * (3 if k == 1325.0 else 1), p // 10 + minute // 2) for k, (c, p) in oi.items()}
    net = {k: float(c - p) if k >= 1300.0 else float(p - c) for k, (c, p) in oi.items()}
    row = mkrow(ts, spot=_price_at(minute), oi=oi, vol=vol, net=net, next_arrays=True,
                book_asof=ts - timedelta(minutes=minute % 4))
    row["meta"]["expiries"] = [{"date": "2026-07-31", "dte": 3}, {"date": "2026-08-07", "dte": 10}]
    row["gex_views"].update({"front_dte": 3, "next_dte": 10,
                             "oi_by_strike": [[k, c + p] for k, (c, p) in sorted(oi.items())]})
    row.update({"vwap": 1292.5, "prior_close": 1270.0,
                "range_ruler": {"em_points": 40.0, "quality": "ok"},
                "iv_skew": {"down_share": 0.55, "skew_pts": 8.0, "put_side_iv": 0.55,
                            "call_side_iv": 0.5, "n_down": 5, "n_up": 5, "skew_v": 1}})
    return row


def _bars(open_at, minutes, volume=1000.0):
    return [{"ts": (open_at + timedelta(minutes=m)).isoformat(), "open": _price_at(m),
             "high": _price_at(m) + 1.0, "low": _price_at(m) - 1.0, "close": _price_at(m),
             "volume": volume} for m in minutes]


# the reply the fake model gives: well formed, plus the lane that was removed
_REPLY = {"quiet": False, "read": "The board is quiet.", "points": [],
          "sides": {"above": {"heavy": None, "leads_on": []}, "below": {"heavy": None, "leads_on": []}},
          "clusters": [{"strikes": [1300.0], "center": 1300.0, "rank": 1, "change": "stable"}],
          "resolved": [], "absent": [],
          "direction": "up", "vector": "up", "arrow": {"dir": "up", "state": "live"}}


def _session(state, monkeypatch):
    """An ordinary session through the reader's own code: a prior session on
    disk, the minute-bar sidecar, a scanner outage, and two reads forty minutes
    apart. Returns (the scenes the model was sent, the read rows)."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(state))
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    diary = state / "sndk_reversion"
    diary.mkdir(parents=True)
    prior_open = _OPEN - timedelta(days=1)
    (diary / f"{prior_open.date()}.jsonl").write_text(
        "".join(json.dumps(_scan(m, prior_open)) + "\n" for m in range(380, 390, 2)))
    # volume_vs_prior_sessions needs PRIOR_VOLUME_MIN_SESSIONS closed sessions with
    # a book inside PRIOR_VOLUME_MAX_AGE_MIN of the read's clock minute: weekdays
    # back, each spanning both reads (minute 40 and minute 80)
    for back in (4, 5, 6, 7):
        past = _OPEN - timedelta(days=back)
        (diary / f"{past.date()}.jsonl").write_text(
            "".join(json.dumps(_scan(m, past)) + "\n" for m in range(30, 84, 2)))
    for back in (5, 4, 1):
        d = _OPEN - timedelta(days=back)
        sndk_bars.write_day(d.date().isoformat(), _bars(d, range(100), volume=800.0), d.replace(hour=16))
    day = _OPEN.date().isoformat()
    scans = [m for m in range(0, 82, 2) if not 64 <= m <= 76]
    sent = []

    def model(prompt, model, timeout=None, doctrine=None):
        sent.append(json.loads(prompt.split("SCENE:\n", 1)[1]))
        return json.loads(json.dumps(_REPLY)), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", model)
    done = -1
    for read_at in (40, 80):
        with open(diary / f"{day}.jsonl", "a") as f:
            f.writelines(json.dumps(_scan(m)) + "\n" for m in scans if done < m <= read_at)
        done = read_at
        now = _OPEN + timedelta(minutes=read_at, seconds=30)
        sndk_bars.write_day(day, _bars(_OPEN, range(read_at)), now)
        SR.read_once(now=now, force=True)
    rows = [json.loads(line) for line in (state / "sndk_reads" / f"{day}.jsonl").read_text().splitlines()]
    return sent, rows


def _edge_payloads(tmp_path, monkeypatch):
    """The Strikes Payload in the states an ordinary session does not reach."""
    out = {}
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path / "edges"))
    rows = mkrows(n=8)
    out["first_book_of_the_day"], _ = B.build_scene_v2(
        rows[0], rows[:1], SR._ts(rows[0]) + timedelta(minutes=1), None, None, flat_bars(16))
    out["book_not_refreshed"], _ = B.build_scene_v2(
        rows[-1], rows, T0, None, SR._ts(rows[-1]) + timedelta(seconds=10), flat_bars(30))
    old = mkrows(n=8, start=T0 - timedelta(minutes=30))
    out["book_too_old"], _ = B.build_scene_v2(old[-1], old, T0, None, None, flat_bars(30))
    morning = mkrows(n=21, start=T0 - timedelta(minutes=30))
    out["no_minute_bars"], _ = B.build_scene_v2(
        morning[-1], morning, SR._ts(morning[-1]) + timedelta(minutes=1), None, SR._ts(morning[10]), [])
    # the diary records the next weekly, and the chain carried none of it
    unlisted = mkrows(n=8)
    for row in unlisted:
        row["gex_views"].update({"oi_side_by_strike_next": [], "vol_side_by_strike_next": []})
    out["next_weekly_not_in_chain"], _ = B.build_scene_v2(
        unlisted[-1], unlisted, SR._ts(unlisted[-1]) + timedelta(minutes=1), None, None, flat_bars(16))

    carried = tmp_path / "carried"
    monkeypatch.setenv("MIRAI_STATE_DIR", str(carried))
    write_prior(carried, vol=YEST)
    first = [fronted(mkrow(at(CARRIED_DAY, 9, 31), vol=YEST))]
    out["book_carries_prior_volume"], _ = B.build_scene_v2(
        first[-1], first, at(CARRIED_DAY, 9, 33), None, None, flat_bars(5))
    later = first + [fronted(mkrow(at(CARRIED_DAY, 9, 31 + 4 * i), vol={k: (5 * i, 3 * i) for k in YEST}))
                     for i in range(1, 4)]
    out["reference_carried_prior_volume"], _ = B.build_scene_v2(
        later[-1], later, at(CARRIED_DAY, 9, 44), None, at(CARRIED_DAY, 9, 33), flat_bars(5))

    # price walks from 1250 to 1450 and each book covers only its own window, as
    # the feed fetches it: the pile at 1200 leaves reach and resolves
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path / "moved"))
    walked = []
    for i in range(9):
        spot, heavy = (1250.0, 1200.0) if i <= 3 else (1450.0, 1500.0)
        grid = [spot - 150.0 + 50 * j for j in range(7)]
        oi = {k: (900, 450) if k == heavy else (40, 20) for k in grid}
        walked.append(mkrow(T0 + timedelta(minutes=4 * i), spot=spot, oi=oi,
                            vol={k: (10 + 5 * i, 5 + 3 * i) for k in grid},
                            net={k: float(c) for k, (c, _) in oi.items()}))
    out["price_walked_away"], _ = B.build_scene_v2(
        walked[-1], walked, SR._ts(walked[-1]) + timedelta(seconds=30), None, SR._ts(walked[3]),
        flat_bars(62), strikes_sent_before=[1100.0, 1150.0, 1200.0, 1250.0])
    # the same walk, with the reading that named the 1200 pile before price left it:
    # the day block grades each claim (off_list for the pile it walked away from,
    # holds for the one still on the table) and lists what is named but off it
    said = {"quiet": False, "read": "The pile at 1200 is carrying the morning.",
            # claimed from BELOW 1250: a point level equal to the spot then has
            # no side, and _grade_claim cannot measure it
            "points": [{"level": 1300.0, "note": "the board stops here"}],
            "sides": {"above": {"heavy": None, "leads_on": []},
                      "below": {"heavy": None, "leads_on": []}},
            "clusters": [{"strikes": [1200.0], "center": 1200.0, "rank": 1, "change": "stable"},
                         {"strikes": [1500.0], "center": 1500.0, "rank": 2, "change": "stable"}],
            "resolved": [], "absent": []}
    # _fresh_calls keeps a row only when its ts equals its reading_ts: a failed
    # call carries the older sentence forward and must not be graded as new
    early_ts = SR._ts(walked[1]).isoformat()
    early = {"ts": early_ts, "reading_ts": early_ts, "spot": 1250.0, "wall_s": 1.0,
             "reading": said, "era": SR.ERA}
    out["a_reading_named_a_strike_price_left"], _ = B.build_scene_v2(
        walked[-1], walked, SR._ts(walked[-1]) + timedelta(seconds=30), None, SR._ts(walked[3]),
        flat_bars(62), strikes_sent_before=[1100.0, 1150.0, 1200.0, 1250.0],
        calls_today=[early])
    return out


def _strikes_payloads(tmp_path, monkeypatch):
    sent, _ = _session(tmp_path / "session", monkeypatch)
    return {"first_read": sent[0], "second_read": sent[1], **_edge_payloads(tmp_path, monkeypatch)}


def _keys_and_strings(node, keys=None, strings=None):
    keys = set() if keys is None else keys
    strings = set() if strings is None else strings
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            _keys_and_strings(v, keys, strings)
    elif isinstance(node, list):
        for v in node:
            _keys_and_strings(v, keys, strings)
    elif isinstance(node, str):
        strings.add(node)
    return keys, strings


def _resolves(node, parts):
    """Whether a dotted path a document names is in `node`, every container on
    the way named. Lists are walked through."""
    if not parts:
        return True
    if isinstance(node, list):
        return any(_resolves(x, parts) for x in node)
    return isinstance(node, dict) and parts[0] in node and _resolves(node[parts[0]], parts[1:])


def _named_nulls(node, prefix=""):
    """Paths of named fields shipped as null. A null in a positional list slot
    is the payload saying which book lacked the strike, and is not one."""
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if v is None:
                yield path
            elif isinstance(v, list):
                for item in v:
                    yield from _named_nulls(item, path + "[]")
            else:
                yield from _named_nulls(v, path)


# ------------------------------------------------ the Strikes Payload vs its map
def test_the_strikes_payload_ships_exactly_the_blocks_the_pipeline_map_lists(tmp_path, monkeypatch):
    """The Strikes Payload card is the one field-by-field map of what the model
    reads. A block added to the builder and not to the card, or a block the
    card promises that the builder stopped writing, fails here."""
    documented = {p for p in _board_schema()[0] if "." not in p}
    shipped = set().union(*(set(p) for p in _strikes_payloads(tmp_path, monkeypatch).values()))
    assert shipped - documented == set(), "blocks the builder ships that the map does not list"
    assert documented - shipped == set(), "blocks the map lists that no payload carries"


def test_the_strike_table_columns_are_the_ones_the_pipeline_map_lists(tmp_path, monkeypatch):
    paths = _board_schema()[0]
    documented = {p.rsplit(".", 1)[1] for p in paths if p.startswith("strikes.rows.")}
    payloads = _strikes_payloads(tmp_path, monkeypatch).values()
    shipped = set().union(*(set(p["strikes"].get("columns", ())) for p in payloads))
    assert shipped - documented == set(), "columns the table ships that the map does not list"
    assert documented - shipped == set(), "columns the map lists that no table carries"


def test_every_field_the_pipeline_map_places_in_the_payload_is_really_there(tmp_path, monkeypatch):
    payloads = list(_strikes_payloads(tmp_path, monkeypatch).values())
    missing = sorted(p for p in _board_schema()[0]
                     if not any(_resolves(s, p.split(".")) for s in payloads))
    assert missing == [], f"the map names fields no payload carries: {missing}"


def test_every_field_the_strikes_payload_ships_is_documented(tmp_path, monkeypatch):
    """The other direction, at every depth: a field name the builder writes
    must appear, whole, in the README, the inventory, a pipeline card or the
    rulebook. A unit suffix counts only where a card spells it out for that
    name, as `(+_at, +_min_ago)`; nothing is excused."""
    words = _documented_words()
    keys = set()
    for payload in _strikes_payloads(tmp_path, monkeypatch).values():
        _keys_and_strings(payload, keys)
    assert sorted(keys - words) == [], "fields shipped to the model that no document names"


# --------------------------------------------------- the rulebook vs the payload
def test_the_rulebook_names_only_fields_the_strikes_payload_carries(tmp_path, monkeypatch):
    """The sr-8 guard, for the rulebook the model is sent today. Delete a field
    and leave its sentence, and the model is instructed about something it
    cannot see. A backticked name must resolve to a key or a value some built
    payload carries; the reply's own keys and the unit suffixes are exempt,
    both read out of the rulebook itself."""
    named, reply = _rulebook_names()
    keys, strings = set(), set()
    for payload in _strikes_payloads(tmp_path, monkeypatch).values():
        _keys_and_strings(payload, keys, strings)
    carried = keys | strings | reply
    outlived = sorted(n for n in named if not n.startswith("_")
                      and any(part not in carried for part in n.split(".")))
    assert len(named) > 50, "the rulebook names almost no fields — the pattern broke"
    assert outlived == [], f"the rulebook names {outlived}, which no Strikes Payload carries"


# -------------------------------------------------------------- the inventory doc
def _inventory_strikes_note():
    """(per-strike fields, blocks, cut or renamed-away fields) of the
    inventory's strikes-1 note on what reaches the model."""
    note = INVENTORY[INVENTORY.index("> **strikes-1"):]
    note = re.sub(r"\s*\n>\s*", " ", note[:note.index("\n\n")])
    names = set(re.findall(r"`([a-z_]+)`", note))
    cut = set(re.findall(r"\(`([a-z_]+)` was cut", note)) | set(re.findall(r"\(was `([a-z_]+)`\)", note))
    blocks = set(re.findall(r"`([a-z_]+)`", re.search(r"plus ((?:`[a-z_]+`(?:, | and )?)+)", note).group(1)))
    return names - cut - blocks, blocks, cut


def test_the_inventory_names_what_reaches_the_model_and_nothing_it_cut(tmp_path, monkeypatch):
    columns, blocks, cut = _inventory_strikes_note()
    payloads = _strikes_payloads(tmp_path, monkeypatch).values()
    shipped_columns = set().union(*(set(p["strikes"].get("columns", ())) for p in payloads))
    shipped_blocks = set().union(*(set(p) for p in payloads))
    keys = set()
    for p in payloads:
        _keys_and_strings(p, keys)
    assert columns and cut and blocks, "the inventory's strikes note did not parse"
    assert columns - shipped_columns == set(), "per-strike fields the inventory says reach the model"
    assert blocks - shipped_blocks == set(), "blocks the inventory says reach the model"
    assert cut & keys == set(), "fields the inventory records as cut are shipping again"


def _table_field_names(field):
    """The field names one row of the scene table says ship: everything outside
    strike-through, parentheses and a trailing ' — ' remark, with the table's
    shorthands `{call,put}_x` and `a/b_x` read as both names."""
    text = re.sub(r"\([^()]*\)", "", re.sub(r"~~.*?~~", "", field)).split(" — ")[0].replace("`", "")
    text = re.sub(r"\{([a-z]+),([a-z]+)\}(_[a-z_]+)", r"\1\3 \2\3", text)
    text = re.sub(r"\b([a-z]+)/([a-z]+)(_[a-z_]+)", r"\1\3 \2\3", text)
    return {part for name in re.findall(r"[a-z_][a-z0-9_.]*", text) for part in name.split(".") if part}


def _inventory_scene_table():
    """(blocks the legacy scene table lists, names it strikes through or
    records as removed, every name it says ships)."""
    table = INVENTORY[INVENTORY.index("## In the scene payload"):INVENTORY.index("**Removed from the payload:**")]
    blocks, struck, named = set(), set(), set()
    for line in table.splitlines():
        if not line.startswith("| ") or line.startswith(("| Scene field", "|---")):
            continue
        field = line[2:].split(" | ")[0]
        for span in re.findall(r"~~(.*?)~~", field):
            name = re.search(r"[a-z_][a-z_.]*", span)
            if name:
                struck.add(name.group(0).rsplit(".", 1)[-1])
        head = re.search(r"[a-z_]+", re.sub(r"~~.*?~~", "", field))
        if head:
            blocks.add(head.group(0))
        named |= _table_field_names(field)
    removed = INVENTORY[INVENTORY.index("**Removed from the payload:**"):]
    struck.update(re.findall(r"`([a-z_]+)`", removed[:removed.index("\n\n")]))
    return blocks, struck, named


def _legacy_scene_with_history(state, monkeypatch):
    """The legacy scene in the states the shared shapes leave out: enough closed
    sessions on disk for the ranks and the history words, a book re-served from
    the cache, a heavier wall behind each side's ladder, walls that moved
    between two books, and the day's first read."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(state))
    now, rich_row = synth.NOW, synth.rich_row
    (state / "sndk_reversion").mkdir(parents=True)
    for back in range(1, SR.PCTL_MIN_SESSIONS + 1):
        day = now - timedelta(days=back)
        rows = [rich_row(ts=day - timedelta(minutes=2 * i),
                         mass=[[1300, 50.0 + back + i], [1100, 30.0], [1200, 10.0]],
                         meta={"book_asof": (day - timedelta(minutes=4 * (i // 2))).isoformat()})
                for i in range(8)]
        (state / "sndk_reversion" / f"{day.date()}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    # three clusters a side over a sub-floor field, the heaviest furthest out
    net = {k: 0.1 for k in range(1050, 1350, 5)}
    net.update({1240: 8.0, 1260: 8.0, 1300: 30.0, 1180: -8.0, 1160: -8.0, 1100: -30.0})
    then = rich_row(ts=now - timedelta(minutes=2), call_wall=1290.0, put_wall=1110.0,
                    meta={"book_asof": (now - timedelta(minutes=5)).isoformat(),
                          "book_source": "pull", "chain_spot": 1200.0})
    row = rich_row(ts=now, call_wall=1310.0, put_wall=1090.0, nbs=sorted([k, v] for k, v in net.items()),
                   meta={"book_asof": (now - timedelta(minutes=1)).isoformat(), "book_source": "disk_cache",
                         "cache_age_s": 120.0, "chain_spot": 1200.0,
                         "expiries": [{"date": (now + timedelta(days=5)).date().isoformat(), "dte": 3}]})
    rows = [then, row]
    return SR.build_scene(row, SR.magnet_band(row), [], rows, now,
                          since_last_read=SR.frame_since_last_read(row, rows, None, "first read", False, now))


def test_the_inventory_and_the_scene_builder_agree_on_the_legacy_scene(tmp_path, monkeypatch):
    """The inventory's main table maps the Scene Payload that still feeds the
    wake gate, the frame, the memory slice and the voice desk. Its blocks and
    the builder's must be the same set, a name it lists without striking it
    through must ship, and a name it strikes through must never ship again."""
    blocks, struck, named = _inventory_scene_table()
    scenes = _every_scene_shape(tmp_path) + [_legacy_scene_with_history(tmp_path / "history", monkeypatch)]
    shipped = set().union(*(set(s) for s in scenes))
    keys = set()
    for s in scenes:
        _keys_and_strings(s, keys)
    assert shipped - blocks == set(), "blocks the scene ships that the inventory does not list"
    assert blocks - shipped == set(), "blocks the inventory lists that no scene carries"
    assert sorted(named - keys) == [], "names the inventory lists that no scene carries: cut or strike them"
    assert struck and struck & keys == set(), "names the inventory strikes through are shipping"


# -------------------------------------------------------------- omit, never null
def test_no_named_field_ships_as_null_on_any_strikes_payload(tmp_path, monkeypatch):
    """README: a field without a clean source is absent, not nulled — the model
    reads missing as "no data". Walked over every payload state built here, not
    only an ordinary board."""
    nulls = {name: list(_named_nulls(p)) for name, p in _strikes_payloads(tmp_path, monkeypatch).items()}
    assert {k: v for k, v in nulls.items() if v} == {}


# ------------------------------------------------------- where everything is saved
_WRITE_LOG = {"hooked": False, "events": None}


def _on_audit_event(event, args):
    events = _WRITE_LOG["events"]
    if events is None:
        return
    if event == "open":
        path, mode, flags = args
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        writing = (set(mode) & set("wax+")) if isinstance(mode, str) else (
            mode is None and isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR))
        events.append(("open" if writing else "read", os.fsdecode(path), _stack_names()))
    elif event == "os.rename" and isinstance(args[1], (str, bytes, os.PathLike)):
        events.append(("rename", (os.fsdecode(args[0]), os.fsdecode(args[1])), _stack_names()))


def _stack_names():
    frame, names = sys._getframe(2), set()
    while frame is not None:
        names.add(f"{frame.f_globals.get('__name__')}.{frame.f_code.co_name}")
        frame = frame.f_back
    return names


@contextmanager
def _writes_recorded():
    """Every file opened, for writing or reading, or renamed into place inside
    the block, with the functions on the stack at that moment. An audit hook
    cannot be removed once added, so one is added once and records only in here."""
    if not _WRITE_LOG["hooked"]:
        sys.addaudithook(_on_audit_event)
        _WRITE_LOG["hooked"] = True
    _WRITE_LOG["events"] = events = []
    try:
        yield events
    finally:
        _WRITE_LOG["events"] = None


def _files_written(events, root):
    """{path under root: the function names that wrote it}; a temp file renamed
    into place counts as its destination."""
    renamed_away = {paths[0] for kind, paths, _ in events if kind == "rename"}
    out = {}
    for kind, paths, stack in events:
        path = paths[1] if kind == "rename" else paths
        if kind == "read" or (kind == "open" and path in renamed_away):
            continue
        resolved = Path(path).resolve()
        if root in resolved.parents:
            out.setdefault(resolved.relative_to(root).as_posix(), set()).update(stack)
    return out


def _files_read(events, root):
    """{path under root: the function names that opened it for reading}."""
    out = {}
    for kind, path, stack in events:
        resolved = Path(path).resolve()
        if kind == "read" and root in resolved.parents:
            out.setdefault(resolved.relative_to(root).as_posix(), set()).update(stack)
    return out


def _run_every_writer(state, monkeypatch):
    """One pass of every SNDK Pro job against a stubbed feed: two scanner days
    (the last tick re-served from the book cache), the bar sidecar, a read that
    spends a call, the memory's rollups, and the dead-man."""
    import lefteye_fetcher
    import sndk_hunter
    import sndk_rag
    monkeypatch.syspath_prepend(str(ROOT / "runtime"))
    from watch.intraday import sndk_deadman

    monkeypatch.setenv("MIRAI_STATE_DIR", str(state))
    monkeypatch.setenv("SNDK_PAYLOAD", "strikes")
    expiries = [("2026-07-31", 4), ("2026-08-07", 11)]
    sides = [{"exp": e, "side": s, "rows": 30, "dte": d} for e, d in expiries for s in ("call", "put")]
    # the book is priced around the LIVE quote, as the feed's rebuilt book is:
    # with the chain's own stale spot the strike window lands nowhere near the
    # grid, no strike is in reach, and the read never reaches the volume work
    # (and so never writes the prior-volume cache the store table names)
    # every tick must not hand back the SAME volume: a book whose counts equal the
    # prior session's is withheld by the carried-volume rule, and then the read
    # never reaches the volume baseline (and never writes prior_volume.json)
    pulls = {"n": 0}

    def _chain(code):
        pulls["n"] += 1
        legs = synth.book(spot=1250.0, lo=1150.0, hi=1350.0)
        for leg in legs:
            leg["volume"] = 100 * pulls["n"]
        return {"spot": 1250.0, "found": [e for e, _ in expiries], "errors": [],
                "chunks": 4, "sides": sides, "contracts": legs}
    monkeypatch.setattr(sndk_feed._nf, "_run", _chain)
    monkeypatch.setattr(sndk_hunter, "_quote", lambda: {
        "spot": 1250.0, "open": 1245.0, "high": 1260.0, "low": 1235.0, "prior_close": 1240.0})
    monkeypatch.setattr(sndk_hunter, "_market_live", lambda: True)
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    monkeypatch.setattr(lefteye_fetcher, "intraday_bars", lambda ticker: [])
    monkeypatch.setattr(SR, "call_the_model", lambda *a, **k: (json.loads(json.dumps(_REPLY)), None, 1.0, None))

    prior = datetime(2026, 7, 24, 15, 58, tzinfo=ET)
    now = datetime(2026, 7, 27, 10, 30, tzinfo=ET)
    open_at = now.replace(hour=9, minute=30)
    # seeded OUTSIDE the recorded block (the test writes these, not the station):
    # without closed sessions to compare, the read never reaches
    # sndk_board.volume_vs_prior_sessions and prior_volume.json is never written,
    # so the store table's writer column would go unproven
    diary = state / "sndk_reversion"
    diary.mkdir(parents=True, exist_ok=True)
    for back in (3, 4, 5, 6):
        past = now - timedelta(days=back)
        past_open = past.replace(hour=9, minute=30)
        (diary / f"{past.date()}.jsonl").write_text(
            "".join(json.dumps(_scan(m, past_open)) + "\n" for m in range(30, 84, 2)))
    with _writes_recorded() as events:
        assert sndk_hunter.tick(prior) == 0
        assert sndk_hunter.tick(now) == 0
        assert sndk_hunter.tick(now + timedelta(minutes=2)) == 0
        sndk_bars.write_day(now.date().isoformat(), [
            {"ts": (open_at + timedelta(minutes=i)).isoformat(), "open": 1250.0, "high": 1251.0,
             "low": 1249.0, "close": 1250.0, "volume": 1000.0} for i in range(62)], now + timedelta(minutes=3))
        assert SR.read_once(now=now + timedelta(minutes=3)) == 0
        sndk_rag.rollup(prior.date().isoformat())
        sndk_rag.terrain(rebuild=True)
        sndk_deadman.run(now + timedelta(minutes=3), state_dir=state, channel=[].append)
    return _files_written(events, state.resolve())


def _written_by(stack, module, function):
    for name in stack:
        where, _, fn = name.rpartition(".")
        if where.rsplit(".", 1)[-1] == module and function in (None, fn):
            return True
    return False


def test_every_file_sndk_pro_writes_is_in_the_where_it_is_saved_table(tmp_path, monkeypatch):
    """Both directions of the map's store table, measured on the code rather
    than on its source text: every file a pass of the jobs writes matches a row,
    and every row that names SNDK Pro code as its writer was written, in that
    pass, with that code on the stack. The hand-edited switch and the dead
    store are written by nothing. A writer outside the pass (the viewstation's
    tape) must at least be defined where the row says and name its folder."""
    stores = _stores()
    written = _run_every_writer(tmp_path / "state", monkeypatch)
    assert written, "the pass wrote nothing — the recorder broke"

    live = [s for s in stores if not s["dead"] and not s["code_free"]]
    unlisted = sorted(p for p in written if not any(s["pattern"].match(p) for s in live))
    assert unlisted == [], f"files SNDK Pro writes that the store table does not list: {unlisted}"
    for s in stores:
        if s["dead"] or s["code_free"]:
            assert not any(s["pattern"].match(p) for p in written), f"{s['file']} is written by code"

    skill = ROOT / "skills" / "sndk-pro"
    for s in live:
        if not s["module"]:
            continue
        stacks = [stack for path, stack in written.items() if s["pattern"].match(path)]
        if (skill / f"{s['module']}.py").exists() or stacks:
            assert any(_written_by(st, s["module"], s["function"]) for st in stacks), (
                f"{s['file']}: the table names {s['writer']!r}, which did not write it")
        else:
            source = next((ROOT / "runtime").rglob(f"{s['module']}.py"))
            text = source.read_text()
            assert re.search(r"^def %s\(" % re.escape(s["function"]), text, re.M), s["writer"]
            assert s["file"].split("/")[0] in text, f"{source} never names {s['file']}"


def test_every_state_folder_the_code_joins_is_on_both_pipeline_diagrams_and_in_the_readme():
    """The store table and the station's Pipeline Architecture card are two
    drawings of one system and must name the same SNDK folders, and the
    README's Store list names them too; any folder the SNDK Pro code, the
    viewstation or the dead-man joins onto the state directory must be on all
    three, including code no test pass reaches."""
    sources = [*(ROOT / "skills" / "sndk-pro").glob("sndk_*.py"),
               *(ROOT / "runtime" / "viewstation").glob("*.py"),
               *(ROOT / "runtime" / "watch" / "intraday").glob("sndk_*.py")]
    joined = set().union(*(set(re.findall(r'/\s*"(sndk_[a-z_]+)"', p.read_text())) for p in sources))
    stores = _stores()
    table = {s["file"].split("/")[0] for s in stores if s["file"].startswith("sndk_") and not s["dead"]}
    dead = {s["file"].split("/")[0] for s in stores if s["dead"]}
    readme = README[README.index("* Store:"):]
    readme = set(re.findall(r"`state/(sndk_[a-z_]+)/", readme[:readme.index("\n* ")]))
    assert joined, "no state folder found in the code — the pattern broke"
    assert joined - table == set(), "folders the code uses that the store table does not list"
    assert joined & dead == set(), "a store the table calls dead is still used by the code"
    assert table == _station_store_folders(), "the two pipeline diagrams list different stores"
    assert readme == table, "the README's Store list and the store table name different stores"


# ------------------------------------------------------------------ the dead-man
_WATCHED = datetime(2026, 8, 27, 11, 4, tzinfo=ET)     # a Thursday session; the real market clock agrees


def _dead_man(monkeypatch, logs):
    """sndk_deadman, with its pager's log and channel kept off the station."""
    monkeypatch.syspath_prepend(str(ROOT / "runtime"))
    from watch import push
    from watch.intraday import sndk_deadman
    monkeypatch.setattr(push, "_LOG_DIR", str(logs))
    monkeypatch.setattr(push, "_channel", None)
    return sndk_deadman


def _watched_day(state, scanner_quiet_min=None, read_min_ago=None):
    """Today's files as the dead-man finds them: a scan every two minutes from
    the open until `scanner_quiet_min` ago (none at all when None), and one read
    row `read_min_ago` minutes old (none when None)."""
    day = _WATCHED.date().isoformat()
    (state / "sndk_reversion").mkdir(parents=True)
    (state / "sndk_reads").mkdir(parents=True)
    scans, t = [], _WATCHED.replace(hour=9, minute=30)
    while scanner_quiet_min is not None and t <= _WATCHED - timedelta(minutes=scanner_quiet_min):
        scans.append({"ticker": "SNDK", "ts": t.isoformat()})
        t += timedelta(minutes=2)
    (state / "sndk_reversion" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in scans))
    if read_min_ago is not None:
        (state / "sndk_reads" / f"{day}.jsonl").write_text(
            json.dumps({"ts": (_WATCHED - timedelta(minutes=read_min_ago)).isoformat()}) + "\n")


def _run_dead_man(deadman, state):
    """One dead-man run: (its ledger afterwards, the state files it read)."""
    with _writes_recorded() as events:
        deadman.run(_WATCHED, state_dir=state, channel=[].append)
    read = {path for path, stack in _files_read(events, state.resolve()).items()
            if _written_by(stack, "sndk_deadman", "run")}
    return json.loads((state / "sndk_reads" / "deadman_state.json").read_text()), read


def test_the_dead_man_watches_the_reader_on_the_readme_ceilings(tmp_path, monkeypatch):
    """README: the dead-man pages when the diary has no row by the stated time
    or its newest row passes the stated minutes, and, with the scanner alive,
    when the newest read row passes the scanner's minutes plus the longest one
    read may run. Every number is read from the README and held against the
    job's plist, the dead-man's constants and the reader's own timeouts; the
    reader's ceiling is then measured both sides, and a silent scanner is paged
    once, under its own name."""
    deadman = _dead_man(monkeypatch, tmp_path / "pushes")
    bullet = README[README.index("`com.mirai-station.sndk-deadman`"):]
    bullet = re.sub(r"\s+", " ", bullet[:bullet.index("\n* ")])
    every = int(re.search(r"every (\d+) min", bullet).group(1))
    first = re.search(r"no row by (\d\d):(\d\d) ET", bullet)
    silent = float(re.search(r"newest row is more than ([\d.]+) min old", bullet).group(1))
    reader, base, run_s = map(float, re.search(
        r"read row is more than ([\d.]+) min old: ([\d.]+) min plus (\d+) s", bullet).groups())
    call_s, guard_s = map(float, re.search(
        r"model call to the (\d+) s timeout and then the reviewer to its (\d+) s",
        re.sub(r"\s+", " ", README)).groups())
    plist = (ROOT / "runtime" / "launchd" / "com.mirai-station.sndk-deadman.plist").read_text()
    assert int(re.search(r"<key>StartInterval</key>\s*<integer>(\d+)</integer>", plist).group(1)) == every * 60
    assert int(first.group(1)) * 60 + int(first.group(2)) == deadman.FIRST_ROW_BY_MIN
    assert silent == base == deadman.SNDK_SILENT_MIN
    assert call_s == max(SR.CALL_TIMEOUT_S, SR.CALL_TIMEOUT_STRIKES_S)
    assert guard_s == SR.SEMANTIC_GUARD_TIMEOUT_S
    assert run_s == call_s + guard_s == deadman.READ_MAX_RUN_S
    assert reader == round(deadman.READER_SILENT_MIN, 1)

    def paged(name, **day):
        _watched_day(tmp_path / name, **day)
        return _run_dead_man(deadman, tmp_path / name)[0]["paged"]
    assert paged("reader_late", scanner_quiet_min=1, read_min_ago=reader + 0.5) == ["reader_silent"]
    assert paged("reader_on_time", scanner_quiet_min=1, read_min_ago=reader - 0.5) == []
    assert paged("scanner_silent", scanner_quiet_min=silent + 5, read_min_ago=reader + 5) == ["silent"]


def test_the_store_table_says_what_the_status_files_hold_and_who_reads_them(tmp_path, monkeypatch):
    """The two files a person opens to see a job breathing, the bar job's
    health.json and the dead-man's ledger, hold only keys their row in the
    store table names, after a good run and after each way a run goes wrong;
    and every state file the dead-man reads names it among its readers."""
    stores = [s for s in _stores() if not s["dead"]]

    def row_for(path):
        row = next((s for s in stores if s["pattern"].match(path)), None)
        assert row, f"{path} is on no row of the store table"
        return row

    def named(path):
        return set(re.findall(r"[a-z_][a-z0-9_]*", row_for(path)["holds"]))

    day = _WATCHED.date().isoformat()
    sndk_bars.write_day(day, _bars(_WATCHED.replace(hour=9, minute=30), range(30)), _WATCHED)
    health = sndk_bars.health_path()
    kept = set(json.loads(health.read_text()))

    def schwab_down(*a, **k):
        raise ConnectionError("price history unreachable")
    monkeypatch.setattr(sndk_bars, "fetch_session", schwab_down)
    assert sndk_bars.run(day, _WATCHED) == 1
    failed = json.loads(health.read_text())
    assert "error" in failed and failed["bars_on_disk"] == 30, failed
    kept |= set(failed)
    rel = health.relative_to(tmp_path).as_posix()
    assert kept - named(rel) == set(), f"{rel} holds keys its store row does not name"

    deadman = _dead_man(monkeypatch, tmp_path / "pushes")
    ledger, read = set(), set()
    for outage, files in (("no_first_row", {}), ("silent", {"scanner_quiet_min": 15}),
                          ("reader_silent", {"scanner_quiet_min": 1, "read_min_ago": 30})):
        _watched_day(tmp_path / outage, **files)
        blob, seen = _run_dead_man(deadman, tmp_path / outage)
        assert outage in blob["paged"], blob
        ledger |= set(blob) | set(blob["paged"])
        read |= seen
    assert ledger - named("sndk_reads/deadman_state.json") == set(), "the ledger holds keys its store row does not name"
    assert read, "the dead-man read nothing — the recorder broke"
    for path in sorted(read):
        assert "dead-man" in row_for(path)["readers"], f"the dead-man reads {path} and its store row does not say so"


# ------------------------------------------------------------ the README's rules
def test_the_strike_window_is_the_one_the_readme_states():
    """README fact 2: `max(±8%, 2·σ_daily/spot)` clamped to ±25%. The numbers
    are read from the README, and the chain card on the pipeline map must state
    the same floor and multiple."""
    m = re.search(r"`max\(±([\d.]+)%, ([\d.]+)·σ_daily/spot\)` clamped to ±([\d.]+)%", README)
    floor, k, cap = float(m.group(1)) / 100, float(m.group(2)), float(m.group(3)) / 100
    card = re.search(r"±([\d.]+)% or ([\d.]+)σ", _pipeline_note("chain")["window"])
    assert (float(card.group(1)) / 100, float(card.group(2))) == (floor, k)

    assert sndk_feed._window_frac() == pytest.approx(floor)          # no vol hint yet
    for sigma_frac, expected in ((floor / k / 2, floor),
                                 ((floor + cap) / 2 / k, (floor + cap) / 2),
                                 (cap / k * 2, cap)):
        atomic_io.write_json_atomic(sndk_feed._state_dir() / "vol_hint.json",
                                    {"sigma_daily_frac": sigma_frac, "ts": T0.isoformat()})
        assert sndk_feed._window_frac() == pytest.approx(expected), sigma_frac


def _one_scan_diary(state, book_age_min, forced=False):
    now = _OPEN + timedelta(minutes=60)
    row = _scan(58)
    row["meta"]["book_asof"] = (now - timedelta(minutes=book_age_min)).isoformat()
    row["meta"]["forced"] = forced
    (state / "sndk_reversion").mkdir(parents=True, exist_ok=True)
    (state / "sndk_reversion" / f"{now.date()}.jsonl").write_text(json.dumps(row) + "\n")
    return now, state / "sndk_reads" / f"{now.date()}.jsonl"


def test_a_stale_book_never_wakes_the_model(tmp_path, monkeypatch):
    """README, sr-6: a stale book (newest row older than the stated minutes)
    never wakes the model and the row says `stale_book`. Measured at the
    README's own number: a book exactly that old is still read, and a tenth of
    a minute older is stale."""
    limit = float(re.search(r"a stale book \(newest row > ([\d.]+) min old\)", README).group(1))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    calls = []

    def model(*a, **k):
        calls.append(1)
        return json.loads(json.dumps(_REPLY)), None, 1.0, None
    monkeypatch.setattr(SR, "call_the_model", model)
    now, reads = _one_scan_diary(tmp_path, limit + 0.1)
    SR.read_once(now=now)
    assert json.loads(reads.read_text().splitlines()[-1])["wake"] == "stale_book" and calls == []
    reads.unlink()
    now, reads = _one_scan_diary(tmp_path, limit)
    SR.read_once(now=now)
    assert json.loads(reads.read_text().splitlines()[-1])["wake"] != "stale_book" and calls == [1]


def test_a_forced_row_never_reaches_the_reader(tmp_path, monkeypatch):
    """README: off-hours --force rows carry meta.forced so they never pool with
    live rows. A day holding only forced rows is a day with nothing to read."""
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    now, reads = _one_scan_diary(tmp_path, 1, forced=True)
    assert SR.read_once(now=now) == 0
    assert not reads.exists()


def test_the_reading_kill_switch_is_checked_in_python_too(tmp_path, monkeypatch):
    """README: SNDK_READ_DISABLE=1 is checked in its wrapper AND in python."""
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    monkeypatch.setenv("SNDK_READ_DISABLE", "1")
    now, reads = _one_scan_diary(tmp_path, 1)
    assert SR.read_once(now=now, force=True) == 0
    assert not reads.exists()


def test_the_pause_switch_fails_open():
    """README: `reasoning_on` fails OPEN — a missing or unreadable control file
    means nobody touched the switch. Only an explicit false pauses."""
    control = SR._control_path()
    assert SR.reasoning_on()
    control.parent.mkdir(parents=True)
    for unreadable in ("", "{", "null", "[]", '"off"'):
        control.write_text(unreadable)
        assert SR.reasoning_on(), unreadable
    control.write_text(json.dumps({"reasoning": False}))
    assert not SR.reasoning_on()


def test_the_reading_call_is_granted_no_tool(monkeypatch):
    """README: the read call has no tools — obs-1 revoked the grant of the
    history CLI and WebSearch. Checked on the command line the live call
    actually builds, with the rulebook it actually carries."""
    seen = {}

    class _Done:
        returncode, stderr, stdout = 0, "", json.dumps({"result": "{}"})

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return _Done()
    monkeypatch.setattr(SR.subprocess, "run", run)
    monkeypatch.setattr(SR, "call_the_model", _REAL_CALL_THE_MODEL)
    monkeypatch.setattr(B, "call_the_model_v2", _REAL_CALL_THE_MODEL_V2)
    B.call_the_model_v2("SCENE:\n{}")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--append-system-prompt") + 1] == B.DOCTRINE_V2
    assert not any(a.startswith(("--allowedTools", "--allowed-tools")) for a in cmd)
    revoked = re.search(r"revoked the sr-2 grant of the history\s+CLI and (\w+)", README).group(1)
    # the history CLI is a Bash command
    assert {"Bash", revoked} <= set(cmd[cmd.index("--disallowedTools") + 1:])
    assert "--strict-mcp-config" in cmd
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert revoked not in B.DOCTRINE_V2 and "sndk_rag" not in B.DOCTRINE_V2


def test_a_direction_in_the_reply_never_reaches_the_record(tmp_path, monkeypatch):
    """README: one lane on purpose — the model says what it notices, never a
    direction, and nothing writes a new `arrow`. A reply that tries anyway
    leaves no trace of it on the read row."""
    _, rows = _session(tmp_path / "session", monkeypatch)
    smuggled = {k for k in _REPLY if k not in _rulebook_names()[1]}
    assert smuggled, "the fake reply no longer carries anything the rulebook leaves out"
    for row in rows:
        keys, _ = _keys_and_strings(row)
        assert row["reading"] and smuggled & keys == set(), row["reading"]


# ------------------------------------------------------- the numbers on the map
def test_the_pipeline_map_states_the_numbers_the_reader_runs_on():
    """The operator reads the gate, the call and the reviewer off the map's
    cards. Each number there is the reader's own."""
    gate, observer, reviewer = (_pipeline_note(n) for n in ("gate", "observer", "reviewer"))
    assert _numbers(gate["floor"])[:3] == [SR.MIN_GAP_MIN, SR.INTERRUPT_MIN_GAP_MIN, SR.INTERRUPT_DAILY_CAP]
    assert _numbers(gate["cap"])[0] == SR.DAILY_CALL_CAP
    assert _numbers(gate["pulse"])[0] == SR.HEARTBEAT_MIN
    assert int(re.search(r"≤(\d+)/day", PIPELINE).group(1)) == SR.DAILY_CALL_CAP
    assert observer["model"].split(",")[0].strip() == SR.PINNED_MODEL
    assert observer["effort"] == SR.CALL_EFFORT
    assert _numbers(observer["timeout"])[0] == SR.CALL_TIMEOUT_STRIKES_S
    assert reviewer["model"] == SR.SEMANTIC_GUARD_MODEL
    assert _numbers(reviewer["timeout"])[0] == SR.SEMANTIC_GUARD_TIMEOUT_S
    assert re.search(r"'DOCTRINE_V2 · ([a-z0-9.-]+)'", STATION).group(1) == SR.PINNED_MODEL


def test_every_document_states_the_book_cache_life_the_feed_keeps():
    """The feed re-serves a pulled book for _CHAIN_TTL_S. The README, the map's
    store table, its chain box and card, and the station's pipeline card,
    Payload tab and Diary view each state that life, so a change to the cache
    made on one side alone fails here."""
    stated = {
        "README": re.search(r"re-served for (\d+) s", README).group(1),
        "store table": re.search(r"each fresh pull \((\d+) s life\)", PIPELINE).group(1),
        "chain card": _numbers(_pipeline_note("chain")["cache"])[0],
        "chain box": 60 * float(re.search(r"front weekly \+2 · cache (\d+) min", PIPELINE).group(1)),
        "pipeline card": re.search(r"good for (\d+) seconds", STATION).group(1),
        "payload tab": re.search(r"cached on disk for (\d+) s", STATION).group(1),
        "diary view": re.search(r"from the (\d+) s cache", STATION).group(1),
    }
    assert {k: float(v) for k, v in stated.items()} == dict.fromkeys(stated, sndk_feed._CHAIN_TTL_S)


# ------------------------------------------------------------------- the runbook
def test_the_runbook_names_the_schwab_login_and_the_gate_codes_the_code_has():
    """docs/OPERATIONS.md is what a person follows with a job down. The Schwab
    re-login it gives must be a real flag on a real script, pointing at the
    callback address that script uses; and the market gate's codes must be the
    ones every gated launch script acts on, with every gated script listed."""
    ops = (ROOT / "docs" / "OPERATIONS.md").read_text()
    schwab = ops[ops.index("# Schwab"):]
    script, flag = re.search(r"(skills/[a-z_-]+/[a-z_]+\.py) (--[a-z-]+)", schwab).groups()
    source = (ROOT / script).read_text()
    assert f'add_argument("{flag}"' in source, f"{script} has no {flag}"
    assert re.search(r'DEFAULT_CALLBACK_URL = "([^"]+)"', source).group(1) in schwab
    live, closed = re.search(r"The gate exits (\d) when the market is open and (\d) when it is closed", ops).groups()
    row = next(line for line in ops.splitlines() if "FAILED (rc=N)" in line)
    gated = [p for p in sorted((ROOT / "runtime" / "scripts").glob("run-*.sh")) if "GATE_RC" in p.read_text()]
    assert gated, "no launch script gates on the market clock — the pattern broke"
    for p in gated:
        text, job = p.read_text(), p.stem.removeprefix("run-")
        assert re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(job), row), f"{p.name} is gated and the runbook does not list it"
        assert f"sys.exit({live} if m.check().is_live else {closed})" in text, p.name
        assert f"GATE_RC -eq {closed} ]]" in text, p.name
        assert re.search(r"FAILED \(rc=\$\{GATE_RC\}\).*>&2", text), p.name
