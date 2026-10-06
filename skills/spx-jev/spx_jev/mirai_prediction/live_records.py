"""The live service's own raw files this system reads for today's read (read-only): archive/{day}.jsonl and hour/{day}.jsonl.

    archive/{day}.jsonl   one record per read of every lane (kind "read"), with the labels and JEV's answers as sent
    hour/{day}.jsonl      one sum record per live read: the calls, learn_exclude per horizon and pool_v1's per-read snapshot
"""
from __future__ import annotations

import json
from pathlib import Path


def lane_folder(state_dir: Path | str, lane: str) -> Path:
    """The lane's own folder under the state dir (the live lane's is state/spx_jev)."""
    from ..lane import LANES
    return LANES[lane].folder(Path(state_dir))


def _json_lines(path: Path):
    """Every well-formed JSON object of a .jsonl file; a bad or half-written line is skipped."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def load_archive_read(state_dir: Path | str, lane: str, day: str, read_id: str | None) -> dict | None:
    """The archive read record of ``read_id``, or the lane's newest read of the day when ``read_id`` is None."""
    from ..archive import read_id as make_read_id
    from ..lane import LANES
    path = Path(LANES[lane].archive_folder(state_dir)) / f"{day}.jsonl"
    chosen = None
    for rec in _json_lines(path):
        if not rec.get("requests") or rec.get("kind") not in (None, "read") or rec.get("lane", lane) != lane:
            continue
        this_id = rec.get("read_id") or make_read_id(lane, rec.get("row_ts", ""))
        if read_id is None or this_id == read_id:
            chosen = {**rec, "read_id": this_id}
            if read_id is not None:
                break
    return chosen


def load_hour_records(state_dir: Path | str, lane: str, day: str) -> dict[str, dict]:
    """{row_ts: sum record} for one day from the lane's hour/{day}.jsonl."""
    path = lane_folder(state_dir, lane) / "hour" / f"{day}.jsonl"
    return {rec["row_ts"]: rec for rec in _json_lines(path) if rec.get("row_ts")}


def load_pool_v1_snapshots(state_dir: Path | str, lane: str, day: str) -> dict[str, dict]:
    """{row_ts: pool_v1's snapshot by horizon} for one day (the "pool" field of each sum record)."""
    return {ts: rec["pool"] for ts, rec in load_hour_records(state_dir, lane, day).items() if isinstance(rec.get("pool"), dict)}
