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
from pathlib import Path

import pytest

import server
import snapshot
import lefteye_fetcher      # on sys.path once snapshot is imported
import station_paths

DAY = "2026-08-19"
UNLOCK = "user=will"
DIARY = station_paths.SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN


def _rel(store, **keys):
    """A store's file relative to the state root, as the raw explorer's `path=` names it."""
    return str(store.relative_folder / store.file_name(**keys))

# address -> the file under static/ it must serve: the pages do_GET names (the phone opens on the SPX
# forecast since the redesign of 2026-10-10)...
PAGES = {"/": "index.html", "/index.html": "index.html",
         "/m": "m/jev-spx.html", "/m/": "m/jev-spx.html"}
# ...and the phone pages reached by their path through the static fallback: the reads, and the retired SNDK
# forecast as its redirect. The SNDK glance, /m/index.html, left this table with phase 5 of the redesign: its
# file is gone, so the fallback 404s it (test_phone_jev_spx pins that) like any other missing name
FILES = {"/m/reads.html": "m/reads.html", "/m/jev.html": "m/jev.html"}

# route -> the query string it is called with
OPEN_JSON = {
    "/api/spot": "ticker=SNDK",
    "/api/replay": f"day={DAY}",
    "/api/lob/days": "ticker=SNDK",
    "/api/lob/board": f"day={DAY}&ticker=SNDK",
    "/api/raw/index": "",
    "/api/raw/file": f"root=state&path={_rel(DIARY, day=DAY)}",
    "/api/store/file": f"name=SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN&day={DAY}",
    "/api/store/index": "name=SNDK_OPTIONS_SCANNER_RESULTS_BY_SCAN",
    "/api/health": "",
    "/api/version": "",
    "/api/whoami": "",
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
EVERY_ROUTE = list(PAGES) + list(QUERIES)


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
    # Every reader that resolves its root per call (sndk_read, the SPX modules,
    # snapshot's own and the raw explorer) takes MIRAI_STATE_DIR through
    # station_paths; snapshot's spot tape and book_flow fixed theirs at import,
    # so each is pointed at the same stores under this root.
    monkeypatch.setenv("MIRAI_STATE_DIR", str(root))
    monkeypatch.setattr(snapshot, "_TAPE_DIR", station_paths.SNDK_LAST_PRICES_BY_QUOTE.folder(root))
    flow = server._book_flow()
    monkeypatch.setattr(flow, "STATE", root)
    monkeypatch.setattr(flow, "CACHE_DIR", station_paths.SPX_OPTIONS_TRADES_3D_VIEW_CARD.folder(root))
    monkeypatch.setattr(flow, "SPX_TAPE", station_paths.SPX_SAME_DAY_OPTIONS_TRADES_BY_TRADE.path(root, day="{day}"))
    monkeypatch.setattr(flow, "BOOK_SNAP",
                        station_paths.EQUITY_BOOK_DEPTH_SNAPSHOTS_BY_SLICE.path(root, ticker="{ticker}", day="{day}"))
    from book_flow import track
    monkeypatch.setattr(track, "DIARY", root / "{dir}" / station_paths.SPX_OPTIONS_SCANNER_RESULTS_BY_SCAN.file_name(day="{day}"))
    # the broker is unreachable, which /api/spot answers with a null spot by contract
    monkeypatch.setattr(lefteye_fetcher, "live_spot", lambda ticker: None)
    monkeypatch.setattr(snapshot, "_SPOT_CACHE", {"ts": 0.0, "val": None, "src": None})
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
    _write_jsonl(DIARY.path(state, day=day), scans)
    _write_jsonl(station_paths.SNDK_PRICE_BARS_BY_MINUTE.path(state, day=day), [
        {"ts": (opened + timedelta(minutes=i)).isoformat(),
         "open": 1580.0, "high": 1581.0, "low": 1579.0, "close": 1580.0, "volume": 1000.0}
        for i in range(30)])
    read_at = (opened + timedelta(minutes=5)).isoformat()
    _write_jsonl(station_paths.SNDK_OPTIONS_READER_GATES_AND_ANSWERS_BY_RUN.path(state, day=day), [
        {"ts": read_at, "wake": "first read", "wall_s": 4.5, "quiet": False, "error": None,
         "spot": 1582.0, "reading": {"read": "1600 holds the most contracts.", "points": []},
         "reading_ts": read_at}])
    _write_jsonl(station_paths.SNDK_OPTIONS_READER_NOTES_BY_READ.path(state, day=day), [
        {"kind": "slice", "rag_v": 2, "narrative": "1600 holds the most contracts.",
         "meta": {"date": day, "time": "09:35", "quiet": False, "notable_count": 1}}])
    book = []
    for k in range(5):
        ts_ms = int((opened + timedelta(minutes=k, seconds=5)).timestamp() * 1000)
        book += [{"ts_ms": ts_ms, "symbol": "SNDK", "side": "bid", "price": 1579.5, "size": 100 + k},
                 {"ts_ms": ts_ms, "symbol": "SNDK", "side": "ask", "price": 1580.5, "size": 90 + k}]
    _write_jsonl(station_paths.EQUITY_BOOK_DEPTH_SNAPSHOTS_BY_SLICE.path(state, ticker="SNDK", day=day), book)


@pytest.fixture
def session(state):
    """DAY, recorded in the state dir every route is pointed at."""
    _record_session(state, DAY)
    return state


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


@pytest.mark.parametrize("route", list(PAGES) + list(FILES))
def test_every_page_address_serves_html(station, route):
    """Every address the desktop page and the phone shell are opened at serves its own page: the desktop at "/", the forecast at both spellings of "/m", the other phone pages by name, never another."""
    status, ctype, body = _get(station, route)
    _page(status, ctype, body, route)
    page = {**PAGES, **FILES}[route]
    assert body == (server.STATIC / page).read_bytes(), f"{route} does not serve static/{page}"


def test_the_phone_shell_lands_on_the_forecast_and_the_old_sndk_address_forwards_to_it(station):
    """The Android shell opens /m?user=will: that is the SPX forecast (jev-spx.html), the query left to the page,
    which ignores it. /m/jev.html, the retired SNDK forecast, is a page whose only script forwards to /m/ with the
    query kept, and which says in words where it went for a browser without scripts."""
    status, ctype, body = _get(station, "/m?user=will")
    _page(status, ctype, body, "/m?user=will")
    assert body == (server.STATIC / "m" / "jev-spx.html").read_bytes()
    assert b"<title>SPX \xc2\xb7 Forecast</title>" in body
    status, ctype, body = _get(station, "/m/jev.html")
    _page(status, ctype, body, "/m/jev.html")
    assert re.findall(rb"(?s)<script>(.*?)</script>", body) == [b"location.replace('/m/' + location.search);"]
    assert b"This page moved to" in body and b'href="/m/"' in body


@pytest.mark.parametrize("route", list(OPEN_JSON))
def test_every_open_data_route_answers_cleanly_on_a_recorded_session(station, session, route):
    """On a healthy recorded session every open data route answers 200 JSON with no error and no trace."""
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
    for route, query in (("/api/sndk/pipeline/days", UNLOCK), ("/api/lob/days", "ticker=SNDK")):
        doc = _clean_json(*_get(station, _path(route, query)), route)
        assert DAY in doc["days"], f"{route} does not offer the recorded session: {doc['days']}"


def _sessions_shown(port, recorded):
    """{route: which of the `recorded` days it offers or opens} for every
    viewstation reader of SNDK state. The payload reads through sndk_read and is
    checked on its own."""
    def doc(route, query=""):
        return _clean_json(*_get(port, _path(route, query)), route)

    def opens(day):
        body = _get(port, _path("/api/raw/file", f"root=state&path={_rel(DIARY, day=day)}"))[2]
        return "error" not in json.loads(body)

    return {
        "/api/sndk/pipeline/days": doc("/api/sndk/pipeline/days", UNLOCK)["days"],
        "/api/sndk/pipeline": [doc("/api/sndk/pipeline", UNLOCK)["day"]],
        "/api/sndk/memory": [d["date"] for d in doc("/api/sndk/memory", f"kind=overview&{UNLOCK}")["days"]],
        "/api/raw/index": sorted({Path(i["rel"]).stem for i in doc("/api/raw/index")["state"]
                                  if i["rel"].startswith(f"{DIARY.relative_folder}/")}),
        "/api/raw/file": [day for day in sorted(recorded) if opens(day)],
    }


def test_with_mirai_state_dir_set_every_sndk_reader_reads_only_that_folder(station, session, tmp_path, monkeypatch):
    """MIRAI_STATE_DIR names the one state folder: every SNDK route and the raw explorer read it, and a newer session in the install's own folder reaches none of them, so one page never shows two folders."""
    newer = (datetime.fromisoformat(DAY) + timedelta(days=7)).date().isoformat()
    install = tmp_path / "install-state"
    _record_session(install, newer)
    monkeypatch.setattr(snapshot, "STATE_DIR", install)
    monkeypatch.setitem(server.RAW_ROOTS, "state", install)

    shown = _sessions_shown(station, recorded=(DAY, newer))
    assert shown == dict.fromkeys(shown, [DAY])
    payload = _clean_json(*_get(station, _path("/api/sndk/payload", UNLOCK)), "/api/sndk/payload")
    assert payload["session"] == DAY


def test_without_mirai_state_dir_the_viewstation_reads_the_install_state_folder(station, session, monkeypatch):
    """Unset, as the live station runs, the viewstation's SNDK readers and the raw explorer all read the install's own state folder."""
    monkeypatch.delenv("MIRAI_STATE_DIR")
    monkeypatch.setattr(snapshot, "STATE_DIR", session)
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
