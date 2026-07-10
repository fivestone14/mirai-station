"""io_state — atomic writes, latest handshake, journals, cursors, rotation."""
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lob_flow.io_state import (StateDir, atomic_write_json, load_cursor,
                               read_json, save_cursor)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)


def test_atomic_write_and_read(tmp_path):
    p = tmp_path / "x" / "y.json"
    atomic_write_json(p, {"a": 1})
    assert read_json(p) == {"a": 1}
    assert not p.with_suffix(".json.tmp").exists()


def test_latest_fresh_and_stale(sd):
    sd.write_latest({"k": "v"}, NOW)
    got = sd.read_latest(NOW + timedelta(seconds=30), max_age_s=600)
    assert got and got["k"] == "v" and got["age_s"] == 30.0
    assert sd.read_latest(NOW + timedelta(seconds=601), max_age_s=600) is None


def test_latest_missing_or_corrupt(sd):
    assert sd.read_latest(NOW, 600) is None
    sd.latest.parent.mkdir(parents=True, exist_ok=True)
    sd.latest.write_text("{not json")
    assert sd.read_latest(NOW, 600) is None
    sd.latest.write_text(json.dumps({"no_written_at": True}))
    assert sd.read_latest(NOW, 600) is None


def test_journal_append_and_tolerant_replay(sd):
    p = sd.raw("2026-07-07", "sweeps")
    sd.append(p, {"a": 1})
    with open(p, "a") as f:
        f.write("{torn line\n")            # crash mid-append
    sd.append(p, {"a": 2})
    assert [r["a"] for r in sd.rows(p)] == [1, 2]


def test_gap_marker(sd):
    sd.gap_marker("2026-07-07", "sweeps", NOW, "disconnect")
    rows = list(sd.rows(sd.raw("2026-07-07", "sweeps")))
    assert rows[0]["gap"] is True and rows[0]["reason"] == "disconnect"


def test_flock_single_instance(sd):
    h1 = sd.acquire_lock()
    assert h1 is not None
    assert sd.acquire_lock() is None      # second collector refused
    h1.close()


def test_cursor_roundtrip_and_day_reset(sd):
    cur = load_cursor(sd, "2026-07-07")
    assert cur == {"day": "2026-07-07", "contracts": {}}
    cur["contracts"]["6300.0|call"] = 123
    save_cursor(sd, cur)
    assert load_cursor(sd, "2026-07-07")["contracts"]["6300.0|call"] == 123
    assert load_cursor(sd, "2026-07-08") == {"day": "2026-07-08", "contracts": {}}


def test_rotate_gzips_drops_and_caps(sd):
    old = (NOW - timedelta(days=40)).date().isoformat()
    yday = (NOW - timedelta(days=1)).date().isoformat()
    today = NOW.date().isoformat()
    for day in (old, yday, today):
        sd.append(sd.raw(day, "sweeps"), {"day": day})
    rep = sd.rotate(NOW, raw_retention_days=30, disk_cap_mb=500)
    assert rep["dropped_days"] == 1
    assert not (sd.root / "raw" / old).exists()
    assert not sd.raw(yday, "sweeps").exists()                 # gzipped
    assert (sd.root / "raw" / yday / "sweeps.jsonl.gz").exists()
    assert sd.raw(today, "sweeps").exists()                    # today untouched
    assert [r["day"] for r in sd.rows(sd.raw(yday, "sweeps"))] == [yday]  # gz replay


def test_rotate_hard_cap_drops_oldest_first(sd):
    d1, d2 = "2026-07-01", "2026-07-06"
    for day in (d1, d2):
        sd.append(sd.raw(day, "sweeps"), {"pad": "x" * 200000})
    rep = sd.rotate(NOW, raw_retention_days=300, disk_cap_mb=0)
    assert rep["cap_dropped"] >= 1
    assert not (sd.root / "raw" / d1).exists()


def test_health_writes_disk_usage(sd):
    sd.write_health(NOW, stream_up=True)
    h = read_json(sd.health)
    assert h["stream_up"] is True and "disk_mb" in h


def test_lock_pid_written(sd):
    h = sd.acquire_lock()
    h.flush()
    assert str(os.getpid()) in sd.lock.read_text()
    h.close()
