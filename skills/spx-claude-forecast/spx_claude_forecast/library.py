"""The library of past moments: one row per read with what the market looked like and what happened next.

Rebuilt whole every night from the record (``payloads/`` + ``outcomes/`` for live reads, ``seed/`` for the
rebuilt history) into ``library/rows.parquet``; nothing in it is the source of truth, so it can always be
deleted and rebuilt. The read-time builder asks it for the base rate and the precedent cards
(``precedents.py``). A row carries the everyday fingerprint the matching uses (seven numbers every payload
has), the quality gates, and the sealed outcome per horizon; it never carries Claude's answer, so a card
can never show Claude its own past call.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from . import jsonl_store
from .control import now_utc_iso
from .paths import ForecastPaths

HORIZONS = ("next_30_minutes", "next_60_minutes", "to_close")
FINGERPRINT_FIELDS = ("gap_sig", "price_minus_close_sig", "last_30_minutes_sig", "range_sig", "range_position",
                      "price_minus_vwap_sig", "vix_term_ratio")   # the everyday items every payload carries; nothing event-shaped
FINGERPRINT_PATHS = {                 # where each item sits in the payload scene
    "gap_sig": ("open_signals", "gap", "settled_sig", "v"),
    "price_minus_close_sig": ("tape", "price_minus_close_sig", "v"),
    "last_30_minutes_sig": ("tape", "m30_sig", "v"),
    "range_sig": ("tape", "range_sig", "v"),
    "range_position": ("tape", "range_pos"),
    "price_minus_vwap_sig": ("tape", "price_minus_vwap_sig", "v"),
    "vix_term_ratio": ("cross_asset", "vix_vix3m"),
}
MANUAL_EXCLUDED_DAYS = {              # days the design review found graded on a wrong-sized ruler (see spec/storage_design.md)
    "2026-08-17": "stand-in options book sized the morning ruler",
    "2026-09-09": "stand-in options book sized the morning ruler",
    "2026-10-06": "options outage: SPY stand-in ruler",
    "2026-10-07": "options outage: SPY stand-in ruler",
    "2026-10-08": "options outage: SPY stand-in ruler most of the day",
}
ANCHOR_VS_VIX_SUSPECT_BELOW = 0.6     # a morning ruler this far under the VIX-implied move is a wrong unit, not a quiet day


def scene_value(scene: dict, path: tuple[str, ...]):
    node = scene
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, (int, float)) and not isinstance(node, bool) else None


def fingerprint_of(scene: dict) -> dict[str, float | None]:
    return {field: scene_value(scene, path) for field, path in FINGERPRINT_PATHS.items()}


def unit_suspect_reason(payload_line: dict) -> str | None:
    """Why a read's ruler cannot be trusted, or None: a day on the manual list, or a morning anchor far below
    the VIX-implied move (quality_flags.anchor_vs_vix_x, written by the payload builder)."""
    day = payload_line.get("trading_day")
    if day in MANUAL_EXCLUDED_DAYS:
        return MANUAL_EXCLUDED_DAYS[day]
    flags = payload_line.get("quality_flags") or {}
    ratio = flags.get("anchor_vs_vix_x")
    if isinstance(ratio, (int, float)) and ratio < ANCHOR_VS_VIX_SUSPECT_BELOW:
        return f"anchor is {ratio:.2f} of the VIX-implied move"
    return None


def library_row(payload_line: dict, outcomes: list[dict]) -> dict:
    """One library row from a payload line and its outcome lines (one per horizon, newest attempt wins)."""
    scene = payload_line.get("scene") or {}
    flags = payload_line.get("quality_flags") or {}
    suspect = unit_suspect_reason(payload_line)
    built = payload_line.get("status") == "built"
    row = {"read_id": payload_line.get("read_id"), "origin": payload_line.get("origin"),
           "trading_day": payload_line.get("trading_day"), "half_hour_slot_et": payload_line.get("half_hour_slot_et"),
           "slot_minute_of_day": _minute_of_day(payload_line.get("half_hour_slot_et")),
           "cut_at": payload_line.get("cut_at"), "claude_input_sha256": payload_line.get("claude_input_sha256"),
           "options_book": flags.get("options_book"), "anchor_vs_vix_x": flags.get("anchor_vs_vix_x"),
           "is_unit_suspect": suspect is not None, "exclude_reason": suspect,
           **fingerprint_of(scene)}
    final = {h: None for h in HORIZONS}
    for line in outcomes:
        horizon = line.get("horizon")
        if horizon in final and (final[horizon] is None or
                                 int(line.get("finalize_attempt_number", 1)) >= int(final[horizon].get("finalize_attempt_number", 1))):
            final[horizon] = line
    stale = bool(((final["next_30_minutes"] or {}).get("result") or {}).get("is_stale_read"))
    row["is_stale_read"] = stale
    for horizon in HORIZONS:
        result = (final[horizon] or {}).get("result") or {}
        row[f"{horizon}_status"] = result.get("status") or "missing"
        row[f"{horizon}_move_flat_edges"] = result.get("graded_move_flat_edges")
        row[f"{horizon}_direction"] = result.get("direction")
        row[f"{horizon}_size_bucket"] = result.get("size_bucket")
    has_fingerprint = sum(row[f] is not None for f in FINGERPRINT_FIELDS) >= 5
    row["is_eligible_precedent"] = bool(built and not suspect and not stale and has_fingerprint
                                        and row["next_30_minutes_status"] == "final")
    row["is_eligible_base_rate"] = bool(built and not suspect and not stale)
    return row


def _minute_of_day(slot: str | None) -> int | None:
    if not slot or ":" not in slot:
        return None
    hh, mm = slot.split(":")
    return int(hh) * 60 + int(mm)


# --- building and reading rows.parquet -----------------------------------------------------------------------

def collect_rows(paths: ForecastPaths) -> list[dict]:
    """Every library row from the record, one per read_id: the seed, then the live payloads with their outcomes.
    A seed row is dropped when a live row stands on the same day and slot (the live read is the real one)."""
    rows: dict[str, dict] = {}
    live_slots: set[tuple[str, str]] = set()
    for origin, payload_dir, outcome_dir in (("live", paths.root / "payloads", paths.root / "outcomes"),
                                             ("seed", paths.root / "seed" / "payloads", paths.root / "seed" / "outcomes")):
        if not payload_dir.exists():
            continue
        for payload_file in sorted(payload_dir.glob("20??-??-??.jsonl")):
            day = payload_file.name[:10]
            outcomes_by_read: dict[str, list[dict]] = {}
            for line in jsonl_store.iter_json_lines(outcome_dir / f"{day}.jsonl"):
                outcomes_by_read.setdefault(str(line.get("read_id")), []).append(line)
            for payload_line in jsonl_store.iter_json_lines(payload_file):
                if payload_line.get("line_type") not in (None, "payload"):
                    continue
                row = library_row({**payload_line, "origin": origin}, outcomes_by_read.get(str(payload_line.get("read_id")), []))
                slot_key = (row["trading_day"] or day, row["half_hour_slot_et"] or "")
                if origin == "live":
                    live_slots.add(slot_key)
                elif slot_key in live_slots:
                    continue
                rows[str(row["read_id"])] = row
    return sorted(rows.values(), key=lambda r: (r["trading_day"] or "", r["half_hour_slot_et"] or "", r["read_id"]))


def write_parquet_rows(path: Path, rows: list[dict]) -> None:
    """Replace a parquet file atomically with these rows (an empty table keeps a read_id column so readers can open it)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows) if rows else pa.table({"read_id": pa.array([], pa.string())})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    jsonl_store.write_bytes_atomically(path, buffer.getvalue())


def read_parquet_rows(path: Path) -> list[dict]:
    """Every row of a parquet file as dicts, or an empty list when the file is not there."""
    if not path.exists():
        return []
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()


def write_library(paths: ForecastPaths, rows: list[dict]) -> dict:
    """Write rows.parquet and the manifest atomically; returns the manifest."""
    write_parquet_rows(paths.library_rows_file, rows)
    manifest = {"built_at": now_utc_iso(), "rows": len(rows),
                "source_max_day": max((r["trading_day"] for r in rows if r.get("trading_day")), default=None),
                "sessions": len({r["trading_day"] for r in rows if r.get("trading_day")}),
                "eligible_precedents": sum(1 for r in rows if r.get("is_eligible_precedent")),
                "rows_sha256": jsonl_store.file_sha256(paths.library_rows_file),
                "fingerprint_fields": list(FINGERPRINT_FIELDS)}
    jsonl_store.write_json_atomically(paths.library_manifest_file, manifest)
    return manifest


def rebuild_library(paths: ForecastPaths) -> dict:
    return write_library(paths, collect_rows(paths))


def read_library(paths: ForecastPaths) -> list[dict]:
    """Every library row, or an empty list when the library has not been built."""
    return read_parquet_rows(paths.library_rows_file)


def read_manifest(paths: ForecastPaths) -> dict | None:
    try:
        return json.loads(paths.library_manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sessions_before(rows: list[dict], day: str) -> list[str]:
    return sorted({r["trading_day"] for r in rows if r.get("trading_day") and r["trading_day"] < day})


__all__ = ["HORIZONS", "FINGERPRINT_FIELDS", "fingerprint_of", "library_row", "collect_rows", "write_parquet_rows",
           "read_parquet_rows", "write_library", "rebuild_library", "read_library", "read_manifest", "sessions_before",
           "unit_suspect_reason"]
