"""Every route the station serves answers, end to end, through the real handler.

The rest of this suite drives do_GET with the transport cut out, or calls the
builders directly, so nothing proved that the pages' data routes still work once
the pieces are wired together — and the live station was once found serving
five-day-old code with every test green. Here a real _Station listens on an
ephemeral loopback port (never 8787, which the live station holds) and each
route is fetched over HTTP.

A 200 alone proves nothing. Every data route catches its own exceptions and
answers 200 with {"error", "trace"} ("never 500 the page"), so a broken builder
looks healthy from the browser. A recorded session must come back with no error
and no trace at all; an empty state dir may say it has nothing yet, but never
with a trace.
"""
import ast
import http.client
import inspect
import json
import re
import textwrap
import threading
from datetime import datetime, timedelta

import pytest

import pipeline
import server
import snapshot
import gex_polarity_ab      # on sys.path once snapshot is imported
import lefteye_fetcher

DAY = "2026-08-19"
UNLOCK = "user=will"
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

PAGES = ["/", "/index.html", "/m", "/m/", "/m/index.html"]

# route -> the query string it is called with
OPEN_JSON = {
    "/api/snapshot": "",
    "/api/pipeline": "",
    "/api/spot": "ticker=SNDK",
    "/api/replay": f"day={DAY}",
    "/api/lob/days": "ticker=SNDK",
    "/api/lob/board": f"day={DAY}&ticker=SNDK",
    "/api/raw/index": "",
    "/api/raw/file": f"root=state&path=sndk_reversion/{DAY}.jsonl",
    "/api/health": "",
    "/api/version": "",
    "/api/whoami": "",
    "/api/sndk/thread": "",
    "/api/sndk/thread/days": "",
}
LOCKED_JSON = {
    "/api/_access": "",
    # the query kind runs the history CLI in a subprocess; the overview is the
    # half of the Memory view that answers in-process
    "/api/sndk/memory": "kind=overview",
    "/api/sndk/pipeline": "",
    "/api/sndk/pipeline/days": "",
    "/api/sndk/payload": "",
}
QUERIES = {**OPEN_JSON, **LOCKED_JSON}
EVERY_ROUTE = PAGES + list(QUERIES)


@pytest.fixture(scope="module")
def station():
    httpd = server._Station(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An empty state dir, and every reader behind a route pointed at it."""
    root = tmp_path / "state"
    root.mkdir()
    # The station has no single state root. Every SNDK reader (sndk_read,
    # snapshot's own, the pipeline map and the raw explorer) takes
    # MIRAI_STATE_DIR per call; snapshot's SPX session finder and spot tape, the
    # SPX modules and book_flow derive theirs from where their file sits.
    monkeypatch.setenv("MIRAI_STATE_DIR", str(root))
    monkeypatch.setattr(snapshot, "REVERSION_DIR", root / "reversion")
    monkeypatch.setattr(snapshot, "_TAPE_DIR", root / "sndk_tape")
    skill_dir = tmp_path / "skills" / "mirai-left-eye"     # their state is SKILL_DIR.parent.parent / "state"
    monkeypatch.setattr(snapshot.rev, "SKILL_DIR", skill_dir)
    monkeypatch.setattr(gex_polarity_ab, "SKILL_DIR", skill_dir)
    monkeypatch.setattr(snapshot.dash, "STATE_DIR", root)
    flow = server._book_flow()
    monkeypatch.setattr(flow, "STATE", root)
    monkeypatch.setattr(flow, "CACHE_DIR", root / "flow" / "boards")
    monkeypatch.setattr(flow, "SPX_TAPE", root / "lob_flow" / "raw" / "{day}" / "tape.jsonl")
    monkeypatch.setattr(flow, "BOOK_SNAP", root / "flow" / "{ticker}" / "{day}" / "book.jsonl")
    from book_flow import track
    monkeypatch.setattr(track, "DIARY", root / "{dir}" / "{day}.jsonl")
    # the broker is unreachable, which /api/spot answers with a null spot by contract
    monkeypatch.setattr(lefteye_fetcher, "live_spot", lambda ticker: None)
    monkeypatch.setattr(snapshot, "_SPOT_CACHE", {"ts": 0.0, "val": None, "src": None})
    monkeypatch.setattr(server, "_cache", {"ts": 0.0, "data": None})
    monkeypatch.setattr(server, "_PAYLOAD_USER", "will")
    return root


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _record_session(state, day):
    """One ordinary recorded SNDK session: scans every two minutes, the minute
    bars under them, one reading and its memory slice, and the equity book the
    LOB board replays — each in the shape its writer puts on disk."""
    opened = datetime.fromisoformat(f"{day}T09:30:00-04:00")
    expiry = (opened + timedelta(days=2)).date().isoformat()
    scans = []
    for i in range(6):
        t = opened + timedelta(minutes=2 + 2 * i)
        scans.append({
            "ticker": "SNDK", "ts": t.isoformat(), "spot": 1580.0 + i, "sigma": 80.0,
            "prior_close": 1554.5, "gamma_sign": "negative", "regime": "trending",
            "gex_views": {
                "front_dte": 2, "magnet": 1600.0,
                "mass_by_strike": [[1600.0, 40.0], [1700.0, 30.0], [1500.0, 25.0]],
                "net_by_strike": [[1550.0, -3.0e6], [1600.0, -2.0e6], [1700.0, 4.0e6]],
            },
            "meta": {"expiries": [{"date": expiry, "dte": 2}], "book_asof": t.isoformat()},
        })
    _write_jsonl(state / "sndk_reversion" / f"{day}.jsonl", scans)
    _write_jsonl(state / "sndk_bars" / f"{day}.jsonl", [
        {"ts": (opened + timedelta(minutes=i)).isoformat(),
         "open": 1580.0, "high": 1581.0, "low": 1579.0, "close": 1580.0, "volume": 1000.0}
        for i in range(30)])
    read_at = (opened + timedelta(minutes=5)).isoformat()
    _write_jsonl(state / "sndk_reads" / f"{day}.jsonl", [
        {"ts": read_at, "wake": "first read", "wall_s": 4.5, "quiet": False, "error": None,
         "spot": 1582.0, "reading": {"read": "1600 holds the most contracts.", "points": []},
         "reading_ts": read_at}])
    _write_jsonl(state / "sndk_rag" / "slices" / f"{day}.jsonl", [
        {"kind": "slice", "rag_v": 2, "narrative": "1600 holds the most contracts.",
         "meta": {"date": day, "time": "09:35", "quiet": False, "notable_count": 1}}])
    book = []
    for k in range(5):
        ts_ms = int((opened + timedelta(minutes=k, seconds=5)).timestamp() * 1000)
        book += [{"ts_ms": ts_ms, "symbol": "SNDK", "side": "bid", "price": 1579.5, "size": 100 + k},
                 {"ts_ms": ts_ms, "symbol": "SNDK", "side": "ask", "price": 1580.5, "size": 90 + k}]
    _write_jsonl(state / "flow" / "SNDK" / day / "book.jsonl", book)


@pytest.fixture
def session(state):
    """DAY, recorded in the state dir every route is pointed at."""
    _record_session(state, DAY)
    return state


def _map_files(state, day):
    """A file named for `day` under each state folder the pipeline map searches."""
    for rel in ("gex_fills/{day}.json", "reversion/polarity-{day}.json"):
        path = state / rel.format(day=day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    for rel in ("reversion/{day}.jsonl", "market_expectation/learning-{day}.jsonl",
                "logs/watch-pushes-{day}.jsonl"):
        _write_jsonl(state / rel.format(day=day), [{"ts": f"{day}T10:00:00-04:00"}])


def _path(route, *queries):
    q = "&".join(p for p in queries if p)
    return f"{route}?{q}" if q else route


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type") or "", resp.read()
    finally:
        conn.close()


def _page(status, ctype, body, route):
    assert status == 200, f"{route} answered {status}"
    assert ctype.startswith("text/html"), f"{route} is not a page: {ctype}"
    assert body.lstrip().lower().startswith(b"<!doctype html"), route


def _clean_json(status, ctype, body, route):
    assert status == 200, f"{route} answered {status}: {body[:200]!r}"
    assert ctype.startswith("application/json"), f"{route} is not JSON: {ctype}"
    doc = json.loads(body)
    assert isinstance(doc, dict), route
    assert "error" not in doc and "trace" not in doc, \
        f"{route} hid a failure behind a 200: {doc.get('error')!r}"
    return doc


def _crashed(doc):
    """The server's exception envelope: a trace, or an error that is
    `<ExceptionName>: message` — the shape every route's catch-all writes."""
    err = doc.get("error")
    return "trace" in doc or (isinstance(err, str) and bool(re.match(r"^\w+(Error|Exception): ", err)))


def _routes_in_do_get():
    tree = ast.parse(textwrap.dedent(inspect.getsource(server.Handler.do_GET)))
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "route"):
            continue
        for op, rhs in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(rhs, ast.Constant):
                found.add(rhs.value)
            elif isinstance(op, ast.In) and isinstance(rhs, (ast.Tuple, ast.List, ast.Set)):
                found.update(e.value for e in rhs.elts if isinstance(e, ast.Constant))
    return found


def test_every_route_do_get_answers_is_covered_here():
    """A route added to or removed from do_GET cannot land without this file's route table changing with it."""
    found = _routes_in_do_get()
    assert found, "no `route == ...` branches found in do_GET; if routing moved, read the new table here"
    covered = set(EVERY_ROUTE)
    assert found - covered == set(), f"do_GET answers routes nothing here fetches: {sorted(found - covered)}"
    assert covered - found == set(), f"this table fetches routes do_GET no longer answers: {sorted(covered - found)}"


@pytest.mark.parametrize("route", PAGES)
def test_every_page_address_serves_html(station, route):
    """Every address the desktop page and the phone shell are opened at serves the page itself."""
    _page(*_get(station, route), route)


@pytest.mark.parametrize("route", list(OPEN_JSON))
def test_every_open_data_route_answers_cleanly_on_a_recorded_session(station, session, route):
    """On a healthy recorded session every open data route, the phone's unlocked thread included, answers 200 JSON with no error and no trace."""
    _clean_json(*_get(station, _path(route, OPEN_JSON[route])), route)


@pytest.mark.parametrize("route", list(LOCKED_JSON))
def test_every_locked_route_refuses_a_request_that_names_nobody(station, session, route):
    """The payload-locked routes answer only the one permitted user; a bare request gets 403 locked and no data."""
    status, ctype, body = _get(station, _path(route, LOCKED_JSON[route]))
    assert status == 403, f"{route} answered {status} to a request naming nobody"
    assert json.loads(body) == {"error": "locked"}


@pytest.mark.parametrize("unlock", [{"query": UNLOCK}, {"headers": {"X-Forwarded-User": "will"}}],
                         ids=["named-in-query", "forwarded-by-front-door"])
@pytest.mark.parametrize("route", list(LOCKED_JSON))
def test_every_locked_route_answers_cleanly_once_unlocked(station, session, route, unlock):
    """Named by ?user= or by the front door's forwarded user, every locked route answers 200 JSON with no error and no trace."""
    path = _path(route, LOCKED_JSON[route], unlock.get("query", ""))
    _clean_json(*_get(station, path, unlock.get("headers")), route)


def test_every_day_picker_lists_the_recorded_session(station, session):
    """A recorded session is offered by every day picker that reads it, so the page can open it."""
    for route, query in (("/api/sndk/pipeline/days", UNLOCK), ("/api/sndk/thread/days", ""),
                         ("/api/lob/days", "ticker=SNDK")):
        doc = _clean_json(*_get(station, _path(route, query)), route)
        assert DAY in doc["days"], f"{route} does not offer the recorded session: {doc['days']}"


def test_the_snapshot_is_really_built_not_its_fallbacks(station, session):
    """/api/snapshot is build_snapshot's full document; no section is swapped for the fallback that keeps a broken one from sinking the page."""
    snap = _clean_json(*_get(station, "/api/snapshot"), "/api/snapshot")
    assert {"generated_at", "session_date", "is_stale", "market_phase", "telemetry_rows",
            "today", "ult", "series", "official_markdown"} <= set(snap)
    assert isinstance(snap["is_stale"], bool) and isinstance(snap["telemetry_rows"], int)
    # each fallback is smaller than the section it stands in for:
    # {} for today, {"ticker": tk} for a ticker, an empty summary for the paper record
    assert {"scoreboard", "mood", "watchers"} <= set(snap["today"])
    assert {"tickers", "paper", "dials"} <= set(snap["ult"])
    tickers = snap["ult"]["tickers"]
    assert tickers and set(snap["series"]) == set(tickers)
    for tk, block in tickers.items():
        assert {"dealer_map", "status", "gex", "reversion", "counts"} <= set(block), tk
        assert isinstance(snap["series"][tk], list), tk
    assert snap["ult"]["paper"]["summary"], "the paper record fell back to an empty summary"
    assert set(snap["official_markdown"]) == {"today", "ult"}
    for view, md in snap["official_markdown"].items():
        assert isinstance(md, str) and md.strip(), f"the {view} markdown fell back to empty"


def test_the_pipeline_map_is_really_built(station, session):
    """/api/pipeline is build_pipeline's stage map: stages of modules, each pointing at data the raw explorer opens from the same state folder."""
    _map_files(session, DAY)
    stages = _clean_json(*_get(station, "/api/pipeline"), "/api/pipeline")["stages"]
    assert isinstance(stages, list) and stages
    ids = [s["id"] for s in stages]
    assert len(ids) == len(set(ids)), ids
    found = []
    for stage in stages:
        assert {"id", "title", "what", "modules"} <= set(stage), stage.get("id")
        assert stage["modules"], stage["id"]
        for module in stage["modules"]:
            assert {"name", "title", "what", "data"} <= set(module), module.get("name")
            assert isinstance(module["data"], list), module["name"]
            for ref in module["data"]:
                assert ref["kind"] in ("file", "info"), ref
                if ref["kind"] == "file":
                    assert ref["root"] in server.RAW_ROOTS and ref["path"], ref
                    if ref["root"] == "state" and DAY in ref["path"]:
                        found.append(ref["path"])
    assert found, "the map points at none of the files in the state folder it was given"
    for rel in found:
        _clean_json(*_get(station, _path("/api/raw/file", f"root=state&path={rel}")), rel)


def _sessions_shown(port, recorded):
    """{route: which of the `recorded` days it offers or opens} for every
    viewstation reader of SNDK state. The payload reads through sndk_read and is
    checked on its own."""
    def doc(route, query=""):
        return _clean_json(*_get(port, _path(route, query)), route)

    def opens(day):
        body = _get(port, _path("/api/raw/file", f"root=state&path=sndk_reversion/{day}.jsonl"))[2]
        return "error" not in json.loads(body)

    refs = [r["path"] for s in doc("/api/pipeline")["stages"] for m in s["modules"]
            for r in m["data"] if r["kind"] == "file" and r["root"] == "state"]
    return {
        "/api/sndk/thread/days": doc("/api/sndk/thread/days")["days"],
        "/api/sndk/thread": [doc("/api/sndk/thread")["day"]],
        "/api/sndk/pipeline/days": doc("/api/sndk/pipeline/days", UNLOCK)["days"],
        "/api/sndk/pipeline": [doc("/api/sndk/pipeline", UNLOCK)["day"]],
        "/api/sndk/memory": [d["date"] for d in doc("/api/sndk/memory", f"kind=overview&{UNLOCK}")["days"]],
        "/api/raw/index": sorted({i["rel"].split("/")[1].split(".")[0] for i in doc("/api/raw/index")["state"]
                                  if i["rel"].startswith("sndk_reversion/")}),
        "/api/raw/file": [day for day in sorted(recorded) if opens(day)],
        "/api/pipeline": sorted({m.group() for p in refs if (m := _ISO_DAY.search(p))}),
    }


def test_with_mirai_state_dir_set_every_sndk_reader_reads_only_that_folder(station, session, tmp_path, monkeypatch):
    """MIRAI_STATE_DIR names the one state folder: every SNDK route, the pipeline map and the raw explorer read it, and a newer session in the install's own folder reaches none of them, so one page never shows two folders."""
    newer = (datetime.fromisoformat(DAY) + timedelta(days=7)).date().isoformat()
    install = tmp_path / "install-state"
    _record_session(install, newer)
    _map_files(install, newer)
    _map_files(session, DAY)
    monkeypatch.setattr(snapshot, "STATE_DIR", install)
    monkeypatch.setattr(pipeline, "STATE_DIR", install)
    monkeypatch.setitem(server.RAW_ROOTS, "state", install)

    shown = _sessions_shown(station, recorded=(DAY, newer))
    assert shown == dict.fromkeys(shown, [DAY])
    payload = _clean_json(*_get(station, _path("/api/sndk/payload", UNLOCK)), "/api/sndk/payload")
    assert payload["session"] == DAY


def test_without_mirai_state_dir_the_viewstation_reads_the_install_state_folder(station, session, monkeypatch):
    """Unset, as the live station runs, the viewstation's SNDK readers, the pipeline map and the raw explorer all read the install's own state folder."""
    monkeypatch.delenv("MIRAI_STATE_DIR")
    _map_files(session, DAY)
    monkeypatch.setattr(snapshot, "STATE_DIR", session)
    monkeypatch.setattr(pipeline, "STATE_DIR", session)
    monkeypatch.setitem(server.RAW_ROOTS, "state", session)

    shown = _sessions_shown(station, recorded=(DAY,))
    assert shown == dict.fromkeys(shown, [DAY])


@pytest.mark.parametrize("route", EVERY_ROUTE)
def test_a_fresh_install_answers_every_route_without_a_crash(station, state, route):
    """Before the first session is recorded every route still answers, with no 500 and no trace: "never 500 the page" holds on an empty state dir."""
    if route in PAGES:
        _page(*_get(station, route), route)
        return
    unlock = UNLOCK if route in LOCKED_JSON else ""
    status, ctype, body = _get(station, _path(route, QUERIES[route], unlock))
    assert status == 200, f"{route} answered {status} on an empty state dir: {body[:200]!r}"
    assert ctype.startswith("application/json"), f"{route} is not JSON: {ctype}"
    doc = json.loads(body)
    assert isinstance(doc, dict), route
    assert not _crashed(doc), f"{route} crashed on an empty state dir: {doc.get('error')!r}"


@pytest.mark.parametrize("route", ["/no-such-page", "/api/no-such-route"])
def test_an_unknown_address_is_a_404_and_the_station_keeps_serving(station, route):
    """An address nothing serves is a JSON 404, never a crash, and the next request is still answered."""
    status, ctype, body = _get(station, route)
    assert status == 404, f"{route} answered {status}"
    assert ctype.startswith("application/json")
    assert "trace" not in json.loads(body)
    assert json.loads(_get(station, "/api/health")[2]).get("ok") is True
