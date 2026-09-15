"""Live health check for the running SNDK Pro station: the morning health review, automated.

Every check below holds the station to a limit its own code states (the dead-man's
silence ceiling, first-row deadline and reader ceiling, the reader's stale-book and
minute-log limits, each job's plist schedule, and "the dashboard serves the code on disk").
The check functions and their unit tests always run. The live tests only read the
real station (launchd, its state/ directory, GET on 127.0.0.1:8787) and skip unless
MIRAI_LIVE=1:

    cd <repo>/runtime && MIRAI_LIVE=1 ~/.local/share/mirai-station/venv/bin/python -m pytest -q -rA health
"""
from __future__ import annotations

import ast
import json
import modulefinder
import operator
import os
import plistlib
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_RUNTIME = Path(__file__).resolve().parents[1]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from watch.intraday import market_status  # noqa: E402

ET = ZoneInfo("America/New_York")
SNDK_JOBS = ("com.mirai-station.sndk", "com.mirai-station.sndk-read",
             "com.mirai-station.sndk-bars", "com.mirai-station.sndk-deadman",
             "com.mirai-station.viewstation")
DASHBOARD_URL = "http://127.0.0.1:8787/api/health"


# --- limits, read from the station's own code ---------------------------------

_ARITH = {ast.Add: operator.add, ast.Sub: operator.sub,
          ast.Mult: operator.mul, ast.Div: operator.truediv}


def _number(node: ast.AST, known: dict) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in known:
        return known[node.id]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("float", "int") and len(node.args) == 1 and not node.keywords):
        return {"float": float, "int": int}[node.func.id](_number(node.args[0], known))
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
        return _ARITH[type(node.op)](_number(node.left, known), _number(node.right, known))
    raise ValueError(ast.dump(node))


def code_limit(source_path: Path, name: str) -> float:
    """The module-level number `name` exactly as the station's source defines it.

    Parsed, not imported: importing a live module would write __pycache__ into the
    running station's tree and run its sys.path side effects."""
    known: dict = {}
    for node in ast.parse(Path(source_path).read_text()).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            try:
                known[node.targets[0].id] = _number(node.value, known)
            except ValueError:
                continue
    if name not in known:
        raise LookupError(f"{name} is no longer a plain number in {source_path}")
    return known[name]


def station_limits(root: Path) -> dict:
    deadman = Path(root) / "runtime" / "watch" / "intraday" / "sndk_deadman.py"
    reader = Path(root) / "skills" / "sndk-pro" / "sndk_read.py"
    return {"silent_min": code_limit(deadman, "SNDK_SILENT_MIN"),
            "first_row_by_min": code_limit(deadman, "FIRST_ROW_BY_MIN"),
            "reader_silent_min": code_limit(deadman, "READER_SILENT_MIN"),
            "book_age_min": code_limit(reader, "MAX_BOOK_AGE_MIN"),
            "bar_record_min": code_limit(reader, "BAR_RECORD_STALE_MIN")}


# --- shared readers -------------------------------------------------------------

def _rows(path: Path) -> list:
    """Every JSON object in a JSONL file; torn or non-object lines are skipped."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=ET)


def _hhmm(value) -> str:
    ts = _parse_ts(value)
    return f"{ts.astimezone(ET):%H:%M} ET" if ts else str(value)


def _closed(now: datetime) -> str | None:
    """None while the market is live, else the skip reason."""
    status = market_status.check(now.astimezone(ET))
    if status.is_live:
        return None
    return "skipped, market closed " + status.reason.removeprefix("market is closed ")


def _open_at(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(ET).date(), market_status._RTH_OPEN, tzinfo=ET)


def _newest_scan(diary_path: Path) -> tuple[dict | None, datetime | None]:
    """The newest scheduled SNDK diary row and its time; --force rows are a human's, not the scanner's."""
    for row in reversed(_rows(diary_path)):
        if row.get("ticker") != "SNDK" or (row.get("meta") or {}).get("forced"):
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is not None:
            return row, ts
    return None, None


# --- the checks: each returns (ok, plain-English reason); ok is None for a skip ---

def scanner_fresh(diary_path: Path, now: datetime, ceiling_min: float,
                  first_row_by_min: int) -> tuple[bool | None, str]:
    """The scanner lands its first row by the dead-man's deadline and its newest row is never older than the dead-man's silence ceiling."""
    closed = _closed(now)
    if closed:
        return None, closed
    now = now.astimezone(ET)
    _, ts = _newest_scan(diary_path)
    if ts is None or ts < _open_at(now):
        hour, minute = divmod(int(first_row_by_min), 60)
        if now.hour * 60 + now.minute < first_row_by_min:
            return True, f"no scanner row yet; the first is due by {hour:02d}:{minute:02d} ET"
        return False, (f"the scanner has written no row since the open; the dead-man expects "
                       f"the first by {hour:02d}:{minute:02d} ET and it is now {now:%H:%M} ET")
    age = (now - ts).total_seconds() / 60.0
    if age > ceiling_min:
        return False, (f"the newest scanner row is {age:.1f} min old (written {_hhmm(ts)}); "
                       f"the dead-man's silence ceiling is {ceiling_min:g} min")
    return True, f"the newest scanner row is {age:.1f} min old (ceiling {ceiling_min:g} min)"


def book_fresh(diary_path: Path, now: datetime, limit_min: float) -> tuple[bool | None, str]:
    """The options book behind the newest scanner row is no older than the reader's stale-book limit, so the reader reads it instead of refusing it."""
    closed = _closed(now)
    if closed:
        return None, closed
    now = now.astimezone(ET)
    row, ts = _newest_scan(diary_path)
    if row is None or ts < _open_at(now):
        return None, "skipped, no scanner row since the open to take a book from"
    # the reader's own fallback and rounding (sndk_read._book_asof, _age_min), so
    # this check and the reader's gate can never disagree
    book = _parse_ts((row.get("meta") or {}).get("book_asof")) or ts
    age = round(max(0.0, (now - book).total_seconds() / 60.0), 1)
    if age > limit_min:
        return False, (f"the options book behind the newest scanner row is {age:.1f} min old "
                       f"(measured {_hhmm(book)}); the reader refuses a book older than {limit_min:g} min")
    return True, f"the options book is {age:.1f} min old (limit {limit_min:g} min)"


def bars_fresh(bars_dir: Path, now: datetime, limit_min: float) -> tuple[bool | None, str]:
    """The minute-bar sidecar's last run reported no error and its record trails now by no more than the reader's minute-log limit."""
    closed = _closed(now)
    if closed:
        return None, closed
    now = now.astimezone(ET)
    bars_dir = Path(bars_dir)
    problems = []
    try:
        health = json.loads((bars_dir / "health.json").read_text())
    except (OSError, ValueError):
        health = {}
    if isinstance(health, dict) and health.get("error"):
        problems.append(f"the minute-bar sidecar's last run ({_hhmm(health.get('ts'))}) "
                        f"failed with {health['error']}")
    stamps = [t for b in _rows(bars_dir / f"{now.date().isoformat()}.jsonl")
              if (t := _parse_ts(b.get("ts"))) is not None]
    last = max(stamps, default=None)
    behind = (now - max(last or _open_at(now), _open_at(now))).total_seconds() / 60.0
    if behind > limit_min:
        where = f"stops at {_hhmm(last)}" if last else "has no bar since the open"
        problems.append(f"the minute-bar record {where}, {behind:.1f} min behind; "
                        f"the reader's minute-log limit is {limit_min:g} min")
    if problems:
        return False, "; and ".join(problems)
    return True, f"the minute-bar record is {behind:.1f} min behind (limit {limit_min:g} min), no error"


def reader_clean(reads_path: Path) -> tuple[bool | None, str]:
    """Every reader row written today carries an empty error field."""
    if not Path(reads_path).exists():
        return None, f"skipped, the reader has written no rows today ({reads_path})"
    rows = _rows(reads_path)
    bad = [r for r in rows if r.get("error")]
    if bad:
        return False, (f"{len(bad)} of {len(rows)} reader rows today carry an error; the latest, "
                       f"at {_hhmm(bad[-1].get('ts'))}, says {str(bad[-1]['error'])[:200]}")
    return True, f"all {len(rows)} reader rows today have no error"


def _newest_read(reads_path: Path) -> datetime | None:
    """The newest scheduled reader row's time; a --force read is a human's, not the reader's."""
    for row in reversed(_rows(reads_path)):
        ts = _parse_ts(row.get("ts"))
        if ts is not None and not row.get("forced"):
            return ts
    return None


def _scanner_run_began(diary_path: Path, ceiling_min: float) -> datetime | None:
    """When the scanner's current run of rows began: its oldest row with no gap past the silence ceiling since."""
    began = None
    for row in reversed(_rows(diary_path)):
        ts = _parse_ts(row.get("ts"))
        if ts is None or row.get("ticker") != "SNDK" or (row.get("meta") or {}).get("forced"):
            continue
        if began is not None and (began - ts).total_seconds() / 60.0 > ceiling_min:
            break
        began = ts
    return began


def reader_fresh(reads_path: Path, diary_path: Path, now: datetime, ceiling_min: float,
                 scanner_ceiling_min: float) -> tuple[bool | None, str]:
    """While the scanner writes, the reader's newest row is no older than the dead-man's reader ceiling, counted from when the scanner's current run of rows began if that is later."""
    closed = _closed(now)
    if closed:
        return None, closed
    now = now.astimezone(ET)
    _, newest = _newest_scan(diary_path)
    if newest is None or newest < _open_at(now) or (now - newest).total_seconds() / 60.0 > scanner_ceiling_min:
        return None, "skipped, the scanner is not writing rows, so the reader has nothing new to read"
    # the dead-man's own rule (sndk_deadman._run_began): a reader is not late for
    # rows the scanner never wrote
    began = _scanner_run_began(diary_path, scanner_ceiling_min)
    read = _newest_read(reads_path)
    since = max(read, began) if read else began
    age = (now - since).total_seconds() / 60.0
    if age > ceiling_min:
        last = f"its newest row is from {_hhmm(read)}" if read else "it has written no row today"
        return False, (f"the reader is {age:.1f} min behind: {last}, while the scanner has written rows "
                       f"since {_hhmm(began)}; the dead-man's reader ceiling is {ceiling_min:.1f} min")
    return True, f"the reader is {age:.1f} min behind the scanner (ceiling {ceiling_min:.1f} min)"


def deadman_quiet(state_path: Path, today: str) -> tuple[bool | None, str]:
    """The dead-man's switch holds no open outage for today: down_since and reader_down_since are empty."""
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError):
        state = None
    if not isinstance(state, dict) or state.get("date") != today:
        return None, f"skipped, the dead-man has written no state for {today} yet"
    outages = [f"the {who} down since {_hhmm(state[key])}"
               for who, key in (("scanner", "down_since"), ("reader", "reader_down_since"))
               if state.get(key)]
    if outages:
        paged = ", ".join(state.get("paged") or []) or "nothing"
        return False, (f"the dead-man's switch has had {' and '.join(outages)} "
                       f"and has paged: {paged}")
    return True, "the dead-man's switch holds no open outage today"


def jobs_loaded(launchctl_list_text: str, required_labels) -> tuple[bool, str]:
    """Every SNDK Pro launchd job is loaded."""
    loaded = {parts[2] for parts in map(str.split, launchctl_list_text.splitlines())
              if len(parts) >= 3}
    missing = [label for label in required_labels if label not in loaded]
    if missing:
        return False, f"not loaded in launchd: {', '.join(missing)}"
    return True, f"all {len(required_labels)} SNDK Pro jobs are loaded"


def _print_field(launchctl_print_text: str, field: str) -> str | None:
    found = re.search(rf"^\t{field} = (.+)$", launchctl_print_text, re.M)
    return found.group(1) if found else None


def _launchd_plist(path) -> dict:
    """A plist as launchd reads it. Apple's parser, not plistlib: com.mirai-station.sndk-bars.plist
    carries `--day` inside an XML comment, which launchd and plutil accept and expat rejects."""
    shown = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(path)],
                           capture_output=True, text=True)
    if shown.returncode != 0:
        raise ValueError((shown.stderr or shown.stdout).strip())
    return json.loads(shown.stdout)


def jobs_on_schedule(launchctl_print_texts: dict) -> tuple[bool, str]:
    """Each loaded job runs on the interval its plist sets, and a KeepAlive job is running."""
    problems = []
    for label, text in launchctl_print_texts.items():
        try:
            plist = _launchd_plist(_print_field(text, "path"))
        except ValueError as exc:
            problems.append(f"{label} was loaded from a plist that cannot be read now ({exc})")
            continue
        interval, running = plist.get("StartInterval"), _print_field(text, "run interval")
        if interval is not None and running != f"{interval} seconds":
            problems.append(f"{label}: its plist says every {interval} seconds but launchd "
                            f"runs it {'every ' + running if running else 'on no interval'}")
        state = _print_field(text, "state")
        if plist.get("KeepAlive") and state != "running":
            problems.append(f"{label}: its plist says keep it alive but launchd reports it {state}")
    if problems:
        return False, "; ".join(problems)
    return True, f"all {len(launchctl_print_texts)} loaded jobs match their plist schedules"


def dashboard_code_paths(server_py: Path) -> list[Path]:
    """Every station source file the dashboard's server.py can import, lazy imports inside functions included."""
    server_py = Path(server_py).resolve()
    root = server_py.parents[2]
    # the directories server.py, snapshot.py and sndk_read.py put on sys.path
    search = [server_py.parent, root / "skills" / "mirai-left-eye",
              root / "skills" / "sndk-pro", root / "skills" / "book-flow"]
    finder = modulefinder.ModuleFinder(path=[str(d) for d in search])
    finder.run_script(str(server_py))
    return sorted({Path(m.__file__) for m in finder.modules.values()
                   if m.__file__ and Path(m.__file__).is_relative_to(root)})


def dashboard_code_current(process_start: datetime, code_paths) -> tuple[bool, str]:
    """The dashboard process started after every code file it runs last changed, so it serves the code on disk."""
    # ps reports a start to the whole second, so mtimes are compared at that precision
    started = int(process_start.timestamp())
    changed = sorted((m, Path(p)) for p in code_paths
                     if (m := int(Path(p).stat().st_mtime)) > started)
    if changed:
        files = "; ".join(f"{p.name} changed {datetime.fromtimestamp(m):%b %d %H:%M:%S}"
                          for m, p in changed)
        return False, (f"the dashboard has run since {process_start:%b %d %H:%M:%S} and never "
                       f"reloads code, but {len(changed)} of its code files changed after that: {files}")
    return True, (f"none of the dashboard's {len(code_paths)} code files changed since it "
                  f"started {process_start:%b %d %H:%M:%S}")


def dashboard_answers(url: str) -> tuple[bool, str]:
    """The dashboard answers its health endpoint with {"ok": true}."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read())
    except (OSError, ValueError) as exc:
        return False, f"nothing healthy answered at {url} ({exc})"
    if not isinstance(body, dict) or body.get("ok") is not True:
        return False, f"{url} answered but did not say ok: {json.dumps(body)[:200]}"
    return True, f"{url} answered ok"


# --- unit tests on synthetic inputs -----------------------------------------------

LIVE = datetime(2026, 8, 27, 11, 4, tzinfo=ET)          # a Thursday, mid-session
WEEKEND = datetime(2026, 8, 29, 11, 4, tzinfo=ET)
AFTER_CLOSE = datetime(2026, 8, 27, 17, 30, tzinfo=ET)
SILENT_MIN, FIRST_ROW_BY_MIN, BOOK_MIN, BARS_MIN = 6.0, 9 * 60 + 35, 6.0, 4
READER_MIN = 9.5


def _write(path: Path, *rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return path


def _scan(now: datetime, minutes_ago: float, **meta) -> dict:
    return {"ticker": "SNDK", "ts": (now - timedelta(minutes=minutes_ago)).isoformat(), "meta": meta}


def _bar(now: datetime, minutes_ago: float) -> dict:
    return {"ts": (now - timedelta(minutes=minutes_ago)).isoformat(), "close": 1540.0}


def test_scanner_fresh_passes_a_row_inside_the_ceiling_despite_a_torn_last_line(tmp_path):
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, 2), '{"ticker": "SND')
    ok, why = scanner_fresh(diary, LIVE, SILENT_MIN, FIRST_ROW_BY_MIN)
    assert ok is True and "2.0 min old" in why


def test_scanner_fresh_passes_a_row_exactly_at_the_ceiling(tmp_path):
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, SILENT_MIN))
    assert scanner_fresh(diary, LIVE, SILENT_MIN, FIRST_ROW_BY_MIN)[0] is True


def test_scanner_fresh_fails_when_the_newest_row_is_past_the_ceiling(tmp_path):
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, 50), _scan(LIVE, 41))
    ok, why = scanner_fresh(diary, LIVE, SILENT_MIN, FIRST_ROW_BY_MIN)
    assert ok is False
    assert "41.0 min old" in why and "10:23 ET" in why and "ceiling is 6 min" in why


def test_scanner_fresh_does_not_count_a_forced_row_as_the_scanner(tmp_path):
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, 41), _scan(LIVE, 1, forced=True))
    assert scanner_fresh(diary, LIVE, SILENT_MIN, FIRST_ROW_BY_MIN)[0] is False


def test_scanner_fresh_fails_with_no_row_since_the_open(tmp_path):
    before_open = _write(tmp_path / "d.jsonl", _scan(LIVE, 120))      # 09:04
    for diary in (before_open, tmp_path / "missing.jsonl"):
        ok, why = scanner_fresh(diary, LIVE, SILENT_MIN, FIRST_ROW_BY_MIN)
        assert ok is False and "no row since the open" in why and "09:35 ET" in why


def test_scanner_fresh_waits_for_the_first_row_until_the_deadline_and_no_longer(tmp_path):
    missing = tmp_path / "missing.jsonl"
    before = datetime(2026, 8, 27, 9, 34, tzinfo=ET)
    after = datetime(2026, 8, 27, 9, 35, tzinfo=ET)
    assert scanner_fresh(missing, before, SILENT_MIN, FIRST_ROW_BY_MIN)[0] is True
    assert scanner_fresh(missing, after, SILENT_MIN, FIRST_ROW_BY_MIN)[0] is False


def test_book_fresh_passes_a_book_inside_the_limit(tmp_path):
    book = (LIVE - timedelta(minutes=BOOK_MIN)).isoformat()
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, 1, book_asof=book))
    ok, why = book_fresh(diary, LIVE, BOOK_MIN)
    assert ok is True and "6.0 min old" in why


def test_book_fresh_fails_an_old_book_behind_a_fresh_row(tmp_path):
    book = (LIVE - timedelta(minutes=83.4)).isoformat()
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, 1, book_asof=book))
    ok, why = book_fresh(diary, LIVE, BOOK_MIN)
    assert ok is False and "83.4 min old" in why and "older than 6 min" in why


def test_book_fresh_dates_a_row_without_book_asof_by_its_scan_like_the_reader(tmp_path):
    assert book_fresh(_write(tmp_path / "a.jsonl", _scan(LIVE, 2)), LIVE, BOOK_MIN)[0] is True
    assert book_fresh(_write(tmp_path / "b.jsonl", _scan(LIVE, 9)), LIVE, BOOK_MIN)[0] is False


def test_bars_fresh_passes_a_current_record_with_no_error(tmp_path):
    _write(tmp_path / f"{LIVE.date()}.jsonl", _bar(LIVE, 30), _bar(LIVE, BARS_MIN))
    _write(tmp_path / "health.json", {"ts": LIVE.isoformat(), "appended": 1, "last_bar_at": "x"})
    ok, why = bars_fresh(tmp_path, LIVE, BARS_MIN)
    assert ok is True and "4.0 min behind" in why


def test_bars_fresh_fails_a_stalled_record(tmp_path):
    _write(tmp_path / f"{LIVE.date()}.jsonl", _bar(LIVE, 90), _bar(LIVE, 88))
    _write(tmp_path / "health.json", {"ts": LIVE.isoformat(), "appended": 0})
    ok, why = bars_fresh(tmp_path, LIVE, BARS_MIN)
    assert ok is False and "stops at 09:36 ET" in why and "88.0 min behind" in why


def test_bars_fresh_fails_on_a_health_error_even_with_a_current_record(tmp_path):
    _write(tmp_path / f"{LIVE.date()}.jsonl", _bar(LIVE, 1))
    _write(tmp_path / "health.json", {"ts": LIVE.isoformat(), "appended": 0,
                                      "error": '<OAuthError "unsupported_token_type">'})
    ok, why = bars_fresh(tmp_path, LIVE, BARS_MIN)
    assert ok is False and "unsupported_token_type" in why


def test_bars_fresh_allows_the_first_minutes_after_the_open_and_no_more(tmp_path):
    assert bars_fresh(tmp_path, datetime(2026, 8, 27, 9, 34, tzinfo=ET), BARS_MIN)[0] is True
    ok, why = bars_fresh(tmp_path, datetime(2026, 8, 27, 9, 35, tzinfo=ET), BARS_MIN)
    assert ok is False and "no bar since the open" in why


@pytest.mark.parametrize("now", [WEEKEND, AFTER_CLOSE], ids=["weekend", "after-close"])
def test_freshness_checks_skip_when_the_market_is_closed(tmp_path, now):
    """Every input here is stale, so a check that forgot the market clock would fail instead."""
    diary = _write(tmp_path / "d.jsonl", _scan(now, 300, book_asof=(now - timedelta(hours=5)).isoformat()))
    _write(tmp_path / "health.json", {"error": "boom"})
    for ok, why in (scanner_fresh(diary, now, SILENT_MIN, FIRST_ROW_BY_MIN),
                    book_fresh(diary, now, BOOK_MIN),
                    reader_fresh(tmp_path / "missing.jsonl", diary, now, READER_MIN, SILENT_MIN),
                    bars_fresh(tmp_path, now, BARS_MIN)):
        assert ok is None and why.startswith("skipped, market closed")


def test_reader_clean_passes_rows_whose_error_is_empty(tmp_path):
    reads = _write(tmp_path / "r.jsonl", {"ts": LIVE.isoformat(), "error": None},
                   {"ts": LIVE.isoformat(), "error": ""})
    assert reader_clean(reads) == (True, "all 2 reader rows today have no error")


def test_reader_clean_fails_a_row_with_an_error(tmp_path):
    reads = _write(tmp_path / "r.jsonl", {"ts": LIVE.isoformat(), "error": None},
                   {"ts": LIVE.isoformat(), "error": "claude -p timed out after 100s"})
    ok, why = reader_clean(reads)
    assert ok is False and "1 of 2" in why and "11:04 ET" in why and "timed out" in why


def test_reader_clean_skips_when_the_reader_has_written_nothing_today(tmp_path):
    ok, why = reader_clean(tmp_path / "missing.jsonl")
    assert ok is None and why.startswith("skipped")


def test_deadman_quiet_passes_with_no_open_outage(tmp_path):
    state = _write(tmp_path / "s.json", {"date": "2026-08-27", "paged": [], "down_since": None})
    assert deadman_quiet(state, "2026-08-27")[0] is True


def test_deadman_quiet_fails_when_down_since_is_set(tmp_path):
    state = _write(tmp_path / "s.json", {"date": "2026-08-27", "paged": ["silent"],
                                         "down_since": "2026-08-27T11:37:18-04:00"})
    ok, why = deadman_quiet(state, "2026-08-27")
    assert ok is False and "since 11:37 ET" in why and "silent" in why


def test_deadman_quiet_fails_when_the_reader_is_down(tmp_path):
    state = _write(tmp_path / "s.json", {"date": "2026-08-27", "paged": ["reader_silent"],
                                         "down_since": None,
                                         "reader_down_since": "2026-08-27T10:50:00-04:00"})
    ok, why = deadman_quiet(state, "2026-08-27")
    assert ok is False and "reader down since 10:50 ET" in why and "reader_silent" in why


def _read(now: datetime, minutes_ago: float, **kw) -> dict:
    return {"ts": (now - timedelta(minutes=minutes_ago)).isoformat(), "error": None, **kw}


def _scans(now: datetime, oldest: float, newest: float = 1.0) -> list:
    return [_scan(now, oldest - 2 * i) for i in range(int((oldest - newest) // 2) + 1)]


def test_reader_fresh_passes_a_reader_inside_the_ceiling(tmp_path):
    diary = _write(tmp_path / "d.jsonl", *_scans(LIVE, 40))
    reads = _write(tmp_path / "r.jsonl", _read(LIVE, READER_MIN - 1), '{"ts": "2026-08')
    ok, why = reader_fresh(reads, diary, LIVE, READER_MIN, SILENT_MIN)
    assert ok is True and f"{READER_MIN - 1:.1f} min behind" in why


def test_reader_fresh_fails_a_reader_that_stopped_while_the_scanner_writes(tmp_path):
    diary = _write(tmp_path / "d.jsonl", *_scans(LIVE, 40))
    reads = _write(tmp_path / "r.jsonl", _read(LIVE, 30), _read(LIVE, 20), _read(LIVE, 1, forced=True))
    ok, why = reader_fresh(reads, diary, LIVE, READER_MIN, SILENT_MIN)
    assert ok is False and "20.0 min behind" in why and "10:44 ET" in why


def test_reader_fresh_fails_a_reader_that_never_wrote_once_the_scanner_outlasts_the_ceiling(tmp_path):
    missing = tmp_path / "missing.jsonl"
    young = _write(tmp_path / "young.jsonl", *_scans(LIVE, READER_MIN - 1))
    assert reader_fresh(missing, young, LIVE, READER_MIN, SILENT_MIN)[0] is True
    old = _write(tmp_path / "old.jsonl", *_scans(LIVE, READER_MIN + 1))
    ok, why = reader_fresh(missing, old, LIVE, READER_MIN, SILENT_MIN)
    assert ok is False and "no row today" in why


def test_reader_fresh_counts_from_the_scanners_return_after_an_outage(tmp_path):
    reads = _write(tmp_path / "r.jsonl", _read(LIVE, 41))
    back = _write(tmp_path / "back.jsonl", _scan(LIVE, 41), *_scans(LIVE, READER_MIN - 1))
    assert reader_fresh(reads, back, LIVE, READER_MIN, SILENT_MIN)[0] is True
    long_back = _write(tmp_path / "long.jsonl", _scan(LIVE, 41), *_scans(LIVE, READER_MIN + 1))
    assert reader_fresh(reads, long_back, LIVE, READER_MIN, SILENT_MIN)[0] is False


def test_reader_fresh_leaves_a_silent_scanner_to_the_scanner_check(tmp_path):
    diary = _write(tmp_path / "d.jsonl", _scan(LIVE, 41))
    ok, why = reader_fresh(tmp_path / "missing.jsonl", diary, LIVE, READER_MIN, SILENT_MIN)
    assert ok is None and why.startswith("skipped")


def test_deadman_quiet_skips_an_outage_left_over_from_another_day(tmp_path):
    state = _write(tmp_path / "s.json", {"date": "2026-08-26", "paged": ["silent"],
                                         "down_since": "2026-08-26T11:37:18-04:00"})
    ok, why = deadman_quiet(state, "2026-08-27")
    assert ok is None and why.startswith("skipped")


_LAUNCHCTL_LIST = """PID\tStatus\tLabel
-\t0\tcom.mirai-station.sndk
-\t0\tcom.mirai-station.sndk-read
-\t1\tcom.mirai-station.sndk-bars
-\t0\tcom.mirai-station.sndk-deadman
33988\t-15\tcom.mirai-station.viewstation
"""


def test_jobs_loaded_passes_when_every_label_is_listed():
    assert jobs_loaded(_LAUNCHCTL_LIST, SNDK_JOBS)[0] is True


def test_jobs_loaded_fails_a_missing_label_even_when_a_longer_one_is_listed():
    listing = _LAUNCHCTL_LIST.replace("-\t0\tcom.mirai-station.sndk\n", "")
    ok, why = jobs_loaded(listing, SNDK_JOBS)
    assert ok is False and why == "not loaded in launchd: com.mirai-station.sndk"


def _launchd_job(tmp_path: Path, label: str, plist: dict, *, state: str, interval: int | None) -> str:
    path = tmp_path / f"{label}.plist"
    path.write_bytes(plistlib.dumps({"Label": label, **plist}))
    run = f"\trun interval = {interval} seconds\n" if interval is not None else ""
    return (f"gui/501/{label} = {{\n\tactive count = 0\n\tpath = {path}\n\ttype = LaunchAgent\n"
            f"\tstate = {state}\n\n\tendpoints = {{\n\t\tstate = active\n\t}}\n{run}}}\n")


def test_jobs_on_schedule_passes_jobs_that_match_their_plists(tmp_path):
    texts = {"a": _launchd_job(tmp_path, "a", {"StartInterval": 120}, state="not running", interval=120),
             "v": _launchd_job(tmp_path, "v", {"KeepAlive": True}, state="running", interval=None)}
    assert jobs_on_schedule(texts)[0] is True


def test_jobs_on_schedule_reads_a_plist_that_launchd_accepts_and_expat_rejects(tmp_path):
    text = _launchd_job(tmp_path, "b", {"StartInterval": 60}, state="not running", interval=60)
    plist = tmp_path / "b.plist"
    plist.write_text(plist.read_text().replace(
        "<dict>", "<dict>\n\t<!-- backfill by hand with `sndk_bars.py --day` -->", 1))
    assert jobs_on_schedule({"b": text})[0] is True


def test_jobs_on_schedule_fails_a_job_running_off_its_plist_interval(tmp_path):
    texts = {"a": _launchd_job(tmp_path, "a", {"StartInterval": 120}, state="not running", interval=300)}
    ok, why = jobs_on_schedule(texts)
    assert ok is False and "every 120 seconds" in why and "every 300 seconds" in why


def test_jobs_on_schedule_fails_a_keepalive_job_that_is_not_running(tmp_path):
    texts = {"v": _launchd_job(tmp_path, "v", {"KeepAlive": True}, state="not running", interval=None)}
    ok, why = jobs_on_schedule(texts)
    assert ok is False and "keep it alive" in why and "not running" in why


def _aged(path: Path, when: datetime) -> Path:
    path.write_text("x = 1\n")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def test_dashboard_code_current_passes_when_every_file_predates_the_process(tmp_path):
    start = datetime(2026, 9, 15, 7, 23, 1).astimezone()
    code = [_aged(tmp_path / "snapshot.py", start - timedelta(hours=11)),
            _aged(tmp_path / "sndk_read.py", start)]
    assert dashboard_code_current(start, code)[0] is True


def test_dashboard_code_current_fails_when_a_file_changed_after_the_process_started(tmp_path):
    """The Sep 10 process that kept serving Sep 14's replaced snapshot.py."""
    start = datetime(2026, 9, 10, 8, 0, 0).astimezone()
    code = [_aged(tmp_path / "server.py", start - timedelta(days=1)),
            _aged(tmp_path / "snapshot.py", datetime(2026, 9, 14, 20, 19, 0).astimezone())]
    ok, why = dashboard_code_current(start, code)
    assert ok is False and "1 of its code files" in why and "snapshot.py changed Sep 14 20:19:00" in why


def test_dashboard_code_paths_include_the_lazily_imported_sndk_modules():
    names = {p.name for p in dashboard_code_paths(_RUNTIME / "viewstation" / "server.py")}
    assert {"server.py", "snapshot.py", "sndk_read.py", "sndk_board.py", "sndk_side.py"} <= names


# file:// stands in for the dashboard so the unit tests open no socket
def test_dashboard_answers_passes_an_ok_health_reply(tmp_path):
    assert dashboard_answers(_write(tmp_path / "h.json", {"ok": True}).as_uri())[0] is True


def test_dashboard_answers_fails_a_reply_that_is_not_ok(tmp_path):
    ok, why = dashboard_answers(_write(tmp_path / "h.json", {"ok": False}).as_uri())
    assert ok is False and "did not say ok" in why


def test_dashboard_answers_fails_when_nothing_answers(tmp_path):
    ok, why = dashboard_answers((tmp_path / "nothing.json").as_uri())
    assert ok is False and "nothing healthy answered" in why


def test_code_limit_resolves_numbers_names_and_arithmetic(tmp_path):
    src = tmp_path / "m.py"
    src.write_text("import os\nSTALE = 6  # note\nMAX_AGE = float(STALE)\nBY = 9 * 60 + 35\n"
                   "LATER = getattr(os, 'x', 3)\n")
    assert (code_limit(src, "STALE"), code_limit(src, "MAX_AGE"), code_limit(src, "BY")) == (6, 6.0, 575)
    with pytest.raises(LookupError):
        code_limit(src, "LATER")


def test_every_limit_the_live_checks_hold_is_the_value_the_code_runs_on(monkeypatch):
    """Parsing a limit out of source must give the number the module has once imported."""
    monkeypatch.syspath_prepend(str(_RUNTIME.parent / "skills" / "sndk-pro"))
    import sndk_read
    from watch.intraday import sndk_deadman
    assert station_limits(_RUNTIME.parent) == {
        "silent_min": sndk_deadman.SNDK_SILENT_MIN,
        "first_row_by_min": sndk_deadman.FIRST_ROW_BY_MIN,
        "reader_silent_min": sndk_deadman.READER_SILENT_MIN,
        "book_age_min": sndk_read.MAX_BOOK_AGE_MIN,
        "bar_record_min": sndk_read.BAR_RECORD_STALE_MIN}


# --- live tests against the running station (read-only) ---------------------------

live = pytest.mark.skipif(os.environ.get("MIRAI_LIVE") != "1",
                          reason="live station check; set MIRAI_LIVE=1 to run it")


def _run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check,
                          env={**os.environ, "LC_ALL": "C"})


def _hold(result: tuple, confirm: str) -> None:
    ok, why = result
    if ok is None:
        pytest.skip(why)
    if not ok:
        pytest.fail(f"{why}\n  confirm with: {confirm}", pytrace=False)
    print(why)


@pytest.fixture(scope="module")
def station():
    """The station launchd is running: its job listings, repo root and today's clock."""
    listing = _run("launchctl", "list").stdout
    prints = {}
    for label in SNDK_JOBS:
        shown = _run("launchctl", "print", f"gui/{os.getuid()}/{label}", check=False)
        if shown.returncode == 0:
            prints[label] = shown.stdout
    plists = [p for p in map(lambda t: _print_field(t, "path"), prints.values()) if p]
    if not plists:
        pytest.fail("no SNDK Pro job is loaded in launchd\n"
                    "  confirm with: launchctl list | grep com.mirai-station", pytrace=False)
    root = Path(plists[0]).resolve().parents[2]
    now = datetime.now(ET)
    return type("Station", (), {"listing": listing, "prints": prints, "root": root,
                                "state": root / "state", "now": now,
                                "day": now.date().isoformat()})


@pytest.fixture(scope="module")
def limits(station):
    return station_limits(station.root)


def _log(station, label: str, stream: str) -> str:
    return _print_field(station.prints.get(label, ""), f"{stream} path") or f"<{label} {stream} log>"


def _skip_when_closed(now: datetime) -> None:
    closed = _closed(now)
    if closed:
        pytest.skip(closed)


@live
def test_live_sndk_jobs_are_loaded(station):
    _hold(jobs_loaded(station.listing, SNDK_JOBS), "launchctl list | grep com.mirai-station")


@live
def test_live_jobs_run_on_their_plist_schedules(station):
    _hold(jobs_on_schedule(station.prints),
          "launchctl print gui/$(id -u)/<label> | grep -E '^.(path|state|run interval) ='")


@live
def test_live_dashboard_answers_health(station):
    _hold(dashboard_answers(DASHBOARD_URL),
          f"curl -s {DASHBOARD_URL}; tail -n 20 {_log(station, 'com.mirai-station.viewstation', 'stderr')}")


@live
def test_live_dashboard_serves_the_code_on_disk(station):
    pid = next((parts[0] for parts in map(str.split, station.listing.splitlines())
                if len(parts) >= 3 and parts[2] == "com.mirai-station.viewstation"), "-")
    if not pid.isdigit():
        pytest.fail("the dashboard job has no running process\n  confirm with: "
                    "launchctl list | grep com.mirai-station.viewstation", pytrace=False)
    command = _run("ps", "-o", "command=", "-p", pid).stdout.split()
    server_py = next((Path(arg) for arg in command if arg.endswith("server.py")), None)
    if server_py is None:
        pytest.fail(f"process {pid} is not running a server.py: {' '.join(command)}\n"
                    f"  confirm with: ps -o command= -p {pid}", pytrace=False)
    lstart = _run("ps", "-o", "lstart=", "-p", pid).stdout.strip()
    started = datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y").astimezone()
    _hold(dashboard_code_current(started, dashboard_code_paths(server_py)),
          f"ps -o lstart= -p {pid}; ls -ltT {server_py.parent}/*.py "
          f"{station.root}/skills/sndk-pro/*.py | head")


@live
def test_live_scanner_is_fresh(station, limits):
    diary = station.state / "sndk_reversion" / f"{station.day}.jsonl"
    _hold(scanner_fresh(diary, station.now, limits["silent_min"], limits["first_row_by_min"]),
          f"tail -n 1 {diary} | cut -c1-60; tail -n 5 {_log(station, 'com.mirai-station.sndk', 'stdout')}")


@live
def test_live_options_book_is_fresh(station, limits):
    diary = station.state / "sndk_reversion" / f"{station.day}.jsonl"
    _hold(book_fresh(diary, station.now, limits["book_age_min"]),
          f"tail -n 1 {diary} | grep -o '\"book_asof\": \"[^\"]*\"'")


@live
def test_live_minute_bars_are_fresh_with_no_error(station, limits):
    bars = station.state / "sndk_bars"
    _hold(bars_fresh(bars, station.now, limits["bar_record_min"]),
          f"cat {bars}/health.json; tail -n 1 {bars}/{station.day}.jsonl; "
          f"tail -n 5 {_log(station, 'com.mirai-station.sndk-bars', 'stdout')}")


@live
def test_live_reader_keeps_up_with_the_scanner(station, limits):
    reads = station.state / "sndk_reads" / f"{station.day}.jsonl"
    diary = station.state / "sndk_reversion" / f"{station.day}.jsonl"
    _hold(reader_fresh(reads, diary, station.now, limits["reader_silent_min"], limits["silent_min"]),
          f"tail -n 1 {reads} | cut -c1-60; tail -n 5 {_log(station, 'com.mirai-station.sndk-read', 'stdout')}")


@live
def test_live_reader_rows_today_have_no_error(station):
    _skip_when_closed(station.now)
    reads = station.state / "sndk_reads" / f"{station.day}.jsonl"
    _hold(reader_clean(reads), f"grep -v '\"error\": null' {reads} | cut -c1-200")


@live
def test_live_deadman_holds_no_open_outage(station):
    _skip_when_closed(station.now)
    state = station.state / "sndk_reads" / "deadman_state.json"
    _hold(deadman_quiet(state, station.day),
          f"cat {state}; tail -n 5 {_log(station, 'com.mirai-station.sndk-deadman', 'stdout')}")
