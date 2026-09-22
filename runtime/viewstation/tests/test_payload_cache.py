"""The phone's payload is built once and served from memory until a file changes or a minute passes."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import snapshot


def _wire(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(snapshot, "_PAY_CACHE", {"key": None, "val": None, "built": 0.0})
    monkeypatch.setattr(snapshot, "_PAY_THREAD", {"started": True})          # no background thread in tests
    monkeypatch.setattr(snapshot, "_build_payload", lambda now: calls.append(now) or {"scene": {"n": len(calls)}})
    monkeypatch.setattr(snapshot, "STATE_DIR", tmp_path)
    monkeypatch.setattr(snapshot, "_state_dir", lambda: tmp_path)
    (tmp_path / "sndk_reversion").mkdir()
    (tmp_path / "sndk_reversion" / "2026-09-22.jsonl").write_text('{"ticker":"SNDK"}\n')
    return calls


def test_served_from_the_cache_until_a_file_changes(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path)
    first = snapshot.sndk_payload()
    second = snapshot.sndk_payload()
    assert len(calls) == 1 and first["scene"] == second["scene"] == {"n": 1}
    assert "cache_age_s" in second
    # a new row lands: the request is still served from memory; the refresher's rebuild picks it up
    time.sleep(0.01)
    with open(tmp_path / "sndk_reversion" / "2026-09-22.jsonl", "a") as f:
        f.write('{"ticker":"SNDK"}\n')
    assert snapshot._payload_stale()
    third = snapshot.sndk_payload()
    assert len(calls) == 1 and third["scene"] == {"n": 1}
    snapshot._rebuild_payload()
    assert len(calls) == 2 and snapshot.sndk_payload()["scene"] == {"n": 2}


def test_a_caller_with_its_own_now_never_touches_the_cache(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path)
    snapshot.sndk_payload()
    snapshot.sndk_payload(datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc))
    assert len(calls) == 2 and snapshot._PAY_CACHE["val"] == {"scene": {"n": 1}}


def test_the_refresher_rebuilds_when_stale(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path)
    snapshot.sndk_payload()
    assert not snapshot._payload_stale()
    snapshot._rebuild_payload()                                     # fresh already: no second build
    assert len(calls) == 1
    snapshot._PAY_CACHE["built"] -= snapshot._PAY_REFRESH_S + 1
    assert snapshot._payload_stale()
    snapshot._rebuild_payload()
    assert len(calls) == 2 and not snapshot._payload_stale()


def test_the_refresher_starts_once(monkeypatch):
    monkeypatch.setattr(snapshot, "_PAY_THREAD", {"started": False})
    started = []
    monkeypatch.setattr(snapshot.threading, "Thread", lambda **kw: started.append(kw) or type("T", (), {"start": lambda self: None})())
    snapshot.start_payload_refresher(); snapshot.start_payload_refresher()
    assert len(started) == 1 and started[0]["daemon"] is True
