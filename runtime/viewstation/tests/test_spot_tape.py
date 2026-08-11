"""The SNDK spot tape (snapshot._tape_append) — the free by-product of the
/api/spot poll. Contract: appends only for tape tickers, only inside the
pure-clock RTH window, floored at one row per _TAPE_MIN_GAP_S, and NEVER
raises into the spot path (the tape is a bonus; /api/spot must not break)."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import snapshot

ET = ZoneInfo("America/New_York")
MONDAY_RTH = datetime(2026, 8, 10, 10, 15, tzinfo=ET)      # Mon, mid-session
SATURDAY = datetime(2026, 8, 8, 10, 15, tzinfo=ET)
AFTER_HOURS = datetime(2026, 8, 10, 16, 1, tzinfo=ET)


def _wire(monkeypatch, tmp_path, when):
    monkeypatch.setattr(snapshot, "_TAPE_DIR", tmp_path / "sndk_tape")
    monkeypatch.setattr(snapshot, "_TAPE_LAST", {"ts": 0.0})
    monkeypatch.setattr(snapshot, "_now_et", lambda: when)


def _rows(tmp_path, when):
    p = tmp_path / "sndk_tape" / f"{when.date().isoformat()}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()]


def test_appends_one_row_in_rth(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path, MONDAY_RTH)
    snapshot._tape_append("SNDK", 1248.4017, 100.0)
    rows = _rows(tmp_path, MONDAY_RTH)
    assert len(rows) == 1
    assert rows[0]["spot"] == 1248.4017
    assert rows[0]["src"] == "schwab_quote"
    assert rows[0]["ts"].startswith("2026-08-10T10:15")


def test_min_gap_floors_burst_to_one_row(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path, MONDAY_RTH)
    snapshot._tape_append("SNDK", 1248.0, 100.0)
    snapshot._tape_append("SNDK", 1249.0, 101.0)          # < _TAPE_MIN_GAP_S later
    assert len(_rows(tmp_path, MONDAY_RTH)) == 1
    snapshot._tape_append("SNDK", 1250.0, 100.0 + snapshot._TAPE_MIN_GAP_S)
    assert len(_rows(tmp_path, MONDAY_RTH)) == 2


def test_weekend_and_after_hours_stay_silent(tmp_path, monkeypatch):
    for when in (SATURDAY, AFTER_HOURS):
        _wire(monkeypatch, tmp_path, when)
        snapshot._tape_append("SNDK", 1248.0, 100.0)
        assert _rows(tmp_path, when) == []


def test_non_tape_ticker_never_writes(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path, MONDAY_RTH)
    snapshot._tape_append("SPX", 6423.0, 100.0)
    assert _rows(tmp_path, MONDAY_RTH) == []


def test_write_failure_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the tape dir should be")
    _wire(monkeypatch, tmp_path, MONDAY_RTH)
    monkeypatch.setattr(snapshot, "_TAPE_DIR", blocker / "sndk_tape")
    snapshot._tape_append("SNDK", 1248.0, 100.0)          # must not raise
    assert snapshot._TAPE_LAST["ts"] == 0.0               # failed append leaves the floor untouched
