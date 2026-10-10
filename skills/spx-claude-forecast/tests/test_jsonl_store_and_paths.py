"""The writers: append-only lines that survive a torn file, atomic replacement, and the write guard."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from spx_claude_forecast import jsonl_store, paths as forecast_paths


def test_ensure_folders_creates_every_folder_and_the_store_map(tmp_path):
    paths = forecast_paths.ensure_folders(tmp_path / "state")
    assert all(folder.is_dir() for folder in paths.folders)
    store_map = json.loads(paths.store_map_file.read_text())
    assert "payloads/{day}.jsonl" in store_map["stores"] and store_map["root"].endswith("spx_claude_forecast")


def test_append_json_line_writes_one_record_per_line_and_reads_it_back(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    target = paths.payloads_file("2026-10-09")
    jsonl_store.append_json_line(target, {"read_id": "live:a", "n": 1})
    jsonl_store.append_json_line(target, {"read_id": "live:b", "n": 2})
    assert [r["read_id"] for r in jsonl_store.read_json_lines(target)] == ["live:a", "live:b"]


def test_a_torn_last_line_is_fenced_off_not_glued_to_the_next_record(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    target = paths.reads_file("2026-10-09")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"read_id": "live:a", "n": 1}\n{"read_id": "live:torn", "n":', encoding="utf-8")
    jsonl_store.append_json_line(target, {"read_id": "live:b", "n": 2})
    records = jsonl_store.read_json_lines(target)
    assert [r["read_id"] for r in records] == ["live:a", "live:b"]
    assert target.read_text().count("\n") == 3


def test_write_json_atomically_leaves_no_temp_file_and_sorts_keys(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    jsonl_store.write_json_atomically(paths.latest_file, {"b": 1, "a": 2})
    assert paths.latest_file.read_text().index('"a"') < paths.latest_file.read_text().index('"b"')
    assert not list(paths.root.glob("latest.json.*.tmp"))


def test_write_bytes_once_writes_only_when_missing_or_short(state_dir):
    paths = forecast_paths.ForecastPaths(state_dir)
    target = paths.rules_file("abc")
    assert jsonl_store.write_bytes_once(target, b"rules") is True
    assert jsonl_store.write_bytes_once(target, b"rules") is False
    target.write_bytes(b"ru")                                   # a crash left it short
    assert jsonl_store.write_bytes_once(target, b"rules") is True and target.read_bytes() == b"rules"


def test_every_writer_refuses_a_path_outside_the_forecast_folder(state_dir, tmp_path):
    pool_2_file = state_dir / "spx_jev" / "mirai_prediction" / "raw" / "new_voice_forecasts" / "2026-10-09.jsonl"
    with pytest.raises(jsonl_store.WriteOutsideOwnFolder):
        jsonl_store.append_json_line(pool_2_file, {"voice_name": "claude_forecast"})
    with pytest.raises(jsonl_store.WriteOutsideOwnFolder):
        jsonl_store.write_json_atomically(state_dir / "spx_jev" / "latest.json", {})
    with pytest.raises(jsonl_store.WriteOutsideOwnFolder):
        jsonl_store.write_bytes_once(tmp_path / "elsewhere.txt", b"x")
    assert not pool_2_file.exists()


def test_canonical_hash_ignores_key_order_and_spacing():
    a = jsonl_store.canonical_json_sha256({"b": [1, 2], "a": {"y": 1, "x": 2}})
    b = jsonl_store.canonical_json_sha256({"a": {"x": 2, "y": 1}, "b": [1, 2]})
    assert a == b and a.startswith("sha256:") and len(a) == len("sha256:") + 16


def _append_many(root: str, target: str, tag: str) -> None:
    jsonl_store.allow_writes_under(root)
    for i in range(200):
        jsonl_store.append_json_line(target, {"tag": tag, "i": i, "pad": "x" * 500}, fsync=False)


def test_two_appenders_never_interleave_lines(state_dir):
    """Two processes append to the same reads file at once (Claude's two answers); every line stays whole."""
    import multiprocessing as mp

    paths = forecast_paths.ForecastPaths(state_dir)
    target = paths.reads_file("2026-10-09")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_append_many, args=(str(paths.root), str(target), t)) for t in ("a", "b")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    records = jsonl_store.read_json_lines(target)
    assert len(records) == 400 and target.read_text().count("\n") == 400
