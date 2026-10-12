"""What actually happened after a read: one outcome line per horizon, graded the way the station grades.

Three horizons. ``next_30_minutes`` and ``next_60_minutes`` are graded on the AVERAGE price over the window
against the read's price, exactly as the station's own grader does (``spx_jev.integral.grade_window``),
with the flat edge the station sizes for the read under its flat-zone rule: the ``average_30`` zone for 30
minutes and the ``next_60`` zone narrowed by ``integral.factor(60)`` for 60 minutes. ``to_close`` is graded
on the official closing price against the read's price; the station has no box for it, so its flat edge
is the station's 60-minute end-price zone scaled by the square root of the minutes left (TO_CLOSE_RULE).

A move is also written in flat edges (move / flat edge) and sorted into seven size buckets:
down_over_3_flat_edges, down_2_to_3_flat_edges, down_1_to_2_flat_edges, flat_within_1_flat_edge,
up_1_to_2_flat_edges, up_2_to_3_flat_edges, up_over_3_flat_edges. Exactly one edge counts as flat.

Nothing here writes: ``grade_read`` returns the lines, and the nightly job (sealing) appends them to
``outcomes/{day}.jsonl`` with the comparison forecasts attached. Field names follow spec/record_formats.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import jsonl_store, station_stores
from .control import ET, now_et

GRADING_RULE_VERSION = 1             # this package's own rule (the to_close edge, the buckets); bump when either changes
TO_CLOSE_RULE = "next_60_zone_x_sqrt(minutes_left/60)"   # how the to_close flat edge is sized from the station's zones
TO_CLOSE_FLOOR_POINTS = 2.0          # the station's live-lane zone floor, kept for the to_close edge too
HORIZON_WINDOWS = {"next_30_minutes": 30, "next_60_minutes": 60}
CLOSE_GRACE_MIN = 2                  # the station grades a window ending within this of the close at the close
SIZE_BUCKETS = ("down_over_3_flat_edges", "down_2_to_3_flat_edges", "down_1_to_2_flat_edges", "flat_within_1_flat_edge",
                "up_1_to_2_flat_edges", "up_2_to_3_flat_edges", "up_over_3_flat_edges")
DIRECTIONS = ("up", "flat", "down")
STATION_HORIZONS = {"next_30_minutes": "next_30", "next_60_minutes": "next_60"}   # the station's names for the same windows
MAIN_FORECAST_SOURCE = "pool_v2"     # the station's headline forecast (Pool 2); any other shown source is not the main forecast
MAIN_FORECAST_LIVE_WITHIN_SECONDS = 600   # a main forecast archived later than this after the read is a replay, never used


@dataclass(frozen=True)
class SessionPrices:
    """One session's prices as the grader needs them: the full day's 1-minute bars and the official close."""

    day: str
    bars: list[dict]
    official_close: float | None       # from daily_closes once the 16:20 ET save-day job has run
    prior_bars: dict[str, list[dict]]  # the sessions before, newest first (the station's ranks read them)

    @property
    def price_bars_sha256(self) -> str:
        return jsonl_store.canonical_json_sha256([(b.get("ts"), b.get("close")) for b in self.bars])


def load_session_prices(state_dir: Path | str, day: str) -> SessionPrices:
    state_dir = Path(state_dir)
    state_builder = station_stores.import_spx_jev("state_builder")
    bars = state_builder.load_bars(state_dir, day)
    official_close = None
    for line in jsonl_store.iter_json_lines(station_stores.daily_closes_file(state_dir, "$SPX")):
        if line.get("day") == day and isinstance(line.get("close"), (int, float)):
            official_close = float(line["close"])
    prior = state_builder.prior_bar_days(state_dir, day, limit=20)
    return SessionPrices(day=day, bars=bars, official_close=official_close, prior_bars=prior)


def direction_of(move_edges: float) -> str:
    if move_edges > 1:
        return "up"
    if move_edges < -1:
        return "down"
    return "flat"


def size_bucket_of(move_edges: float) -> str:
    """The seven-way bucket of a move in flat edges; exactly one edge is flat."""
    size = abs(move_edges)
    if size <= 1:
        return "flat_within_1_flat_edge"
    side = "up" if move_edges > 0 else "down"
    if size <= 2:
        return f"{side}_1_to_2_flat_edges"
    if size <= 3:
        return f"{side}_2_to_3_flat_edges"
    return f"{side}_over_3_flat_edges"


def flat_edges_for(zones: dict[str, float], minutes_left: int) -> dict[str, float]:
    """The flat edge in points per horizon from the station's zones for the read."""
    integral = station_stores.import_spx_jev("integral")
    edges = {}
    if "average_30" in zones:
        edges["next_30_minutes"] = round(float(zones["average_30"]), 2)
    if "next_60" in zones:
        edges["next_60_minutes"] = round(integral.factor(60) * float(zones["next_60"]), 2)
        edges["to_close"] = round(max(TO_CLOSE_FLOOR_POINTS, float(zones["next_60"]) * math.sqrt(max(minutes_left, 1) / 60)), 2)
    return edges


def grade_read(prices: SessionPrices, read_id: str, row_ts: str, spot: float, zones: dict[str, float] | str, *,
               read_source: str = "live", slot: str | None = None, claude_input_sha256: str | None = None,
               prompt_and_model_version: str | None = None, now: datetime | None = None) -> list[dict]:
    """The outcome lines (one per horizon) for the read at ``row_ts`` with price ``spot``, laid out as
    spec/record_formats.md has them. A horizon the station would never grade (its window ends past the close)
    is ``not_asked``; one the bars cannot settle is ``no_data``. The caller attaches the comparison forecasts."""
    grade = station_stores.import_spx_jev("grade")
    integral = station_stores.import_spx_jev("integral")
    sessions = station_stores.import_spx_jev("sessions")
    t0 = grade.horizon_start(row_ts)
    close = sessions.session_close(t0)
    minutes_left = int((close - t0).total_seconds() // 60)
    finalized_at = (now or now_et()).isoformat(timespec="seconds")

    def line(horizon: str, result: dict) -> dict:
        return {"line_type": "outcome", "read_id": read_id, "claude_input_sha256": claude_input_sha256, "horizon": horizon,
                "grading_rule_version": GRADING_RULE_VERSION, "finalize_attempt_number": 1, "finalized_at": finalized_at,
                "trading_day": prices.day, "half_hour_slot_et": slot, "prompt_and_model_version": prompt_and_model_version,
                "read_source": read_source, "result": result,
                "station_rule_versions": {"integral": getattr(integral, "RULE_VERSION", None),
                                          "flat_zone": getattr(station_stores.import_spx_jev("flat_zone"), "RULE_VERSION", None)}}

    if isinstance(zones, str):
        return [line(h, {"status": "no_data", "why": f"no flat zones: {zones}"}) for h in (*HORIZON_WINDOWS, "to_close")]
    edges = flat_edges_for(zones, minutes_left)
    lines = []
    for horizon, minutes in HORIZON_WINDOWS.items():
        if t0 + timedelta(minutes=minutes) > close + timedelta(minutes=CLOSE_GRACE_MIN):
            lines.append(line(horizon, {"status": "not_asked", "why": "window ends past the close"}))
            continue
        window_minutes = min(minutes, minutes_left)
        edge = edges.get(horizon)
        if edge is None:
            lines.append(line(horizon, {"status": "no_data", "why": "no flat zone for the horizon"}))
            continue
        graded = integral.grade_window(prices.bars, prices.prior_bars, t0, window_minutes, spot, flat=edge, pick=None, edge=edge)
        lines.append(line(horizon, _average_price_result(graded, spot, edge, window_minutes, prices)))
    if minutes_left <= 0:
        lines.append(line("to_close", {"status": "not_asked", "why": "read at or after the close"}))
    else:
        lines.append(line("to_close", _to_close_result(prices, t0, spot, edges.get("to_close"), minutes_left)))
    return lines


def _average_price_result(graded: dict, spot: float, edge: float, window_minutes: int, prices: SessionPrices) -> dict:
    if not graded.get("graded"):
        return {"status": "no_data", "why": graded.get("reason", "not graded"), "missing_minutes": graded.get("missing"),
                "graded_move_measured_to": "window_average_price", "window_minutes": window_minutes,
                "price_at_read": spot, "flat_edge_points": edge}
    move_points = float(graded["g"])
    move_edges = round(move_points / edge, 3)
    return {"status": "final", "graded_move_measured_to": "window_average_price", "window_minutes": window_minutes,
            "price_at_read": spot, "flat_edge_points": edge, "graded_move_points": round(move_points, 2),
            "graded_move_flat_edges": move_edges, "direction": direction_of(move_edges), "size_bucket": size_bucket_of(move_edges),
            "highest_move_in_window_flat_edges": round(float(graded["best"]["points"]) / edge, 3),
            "lowest_move_in_window_flat_edges": round(float(graded["worst"]["points"]) / edge, 3),
            "minutes_filled_in": graded.get("filled") or [], "bad_tick_count": graded.get("bad_ticks"),
            "is_stale_read": bool(graded.get("stale_read")), "price_bars_sha256": prices.price_bars_sha256}


def _to_close_result(prices: SessionPrices, t0: datetime, spot: float, edge: float | None, minutes_left: int) -> dict:
    state_builder = station_stores.import_spx_jev("state_builder")
    measured_to, close_price = "official_close_price", prices.official_close
    after = [b for b in prices.bars if state_builder.parse_ts(b["ts"]) >= t0]
    if close_price is None:
        if not after:
            return {"status": "no_data", "why": "no closing price and no bars after the read", "window_minutes": minutes_left}
        measured_to, close_price = "last_bar_close_price", float(after[-1]["close"])
    if edge is None:
        return {"status": "no_data", "why": "no flat zone to size the to_close edge", "window_minutes": minutes_left}
    move_points = close_price - spot
    move_edges = round(move_points / edge, 3)
    moves_after_read = [float(b["close"]) - spot for b in after] or [move_points]
    return {"status": "final", "graded_move_measured_to": measured_to, "window_minutes": minutes_left, "price_at_read": spot,
            "flat_edge_points": edge, "flat_edge_rule": TO_CLOSE_RULE, "graded_move_points": round(move_points, 2),
            "graded_move_flat_edges": move_edges, "direction": direction_of(move_edges), "size_bucket": size_bucket_of(move_edges),
            "highest_move_in_window_flat_edges": round(max(moves_after_read) / edge, 3),
            "lowest_move_in_window_flat_edges": round(min(moves_after_read) / edge, 3), "price_bars_sha256": prices.price_bars_sha256}


# --- the station's own forecasts for the same read, copied at seal time -----------------------------------

def comparison_forecasts_from_read_record(read_record: dict | None, horizon: str) -> tuple[dict, dict]:
    """``(comparison_forecasts, missing)`` for one horizon from the station's archived read record: its time-of-day
    odds and its main forecast (Pool 2, as it was shown live), each as up/flat/down percentages. The 60-minute
    odds are end-price odds, and the line says so. A main forecast archived later than
    MAIN_FORECAST_LIVE_WITHIN_SECONDS after the read is a replay and goes into ``missing``."""
    found: dict = {}
    missing: dict = {}
    hour = (read_record or {}).get("hour") or {}
    if horizon == "next_30_minutes":
        block, measured = hour.get("average") or {}, "window_average_price"
    elif horizon == "next_60_minutes":
        block, measured = ((hour.get("by") or {}).get("next_60")) or {}, "window_end_price"
    else:
        return found, {"existing_system_time_of_day_odds_20_sessions": "no station odds to the close",
                       "existing_system_main_forecast": "no station forecast to the close"}
    clock = (block.get("clock") or {}).get("probabilities")
    if isinstance(clock, dict):
        found["existing_system_time_of_day_odds_20_sessions"] = {**_pct(clock), "forecasts_move_measured_to": measured}
    else:
        missing["existing_system_time_of_day_odds_20_sessions"] = "not in the read record"
    main = block.get("probabilities")
    written = _written_after_read(read_record or {})
    if not isinstance(main, dict):
        missing["existing_system_main_forecast"] = "not in the read record"
    elif block.get("shown_source") != MAIN_FORECAST_SOURCE:
        missing["existing_system_main_forecast"] = f"the shown forecast was {block.get('shown_source')!r}, not {MAIN_FORECAST_SOURCE}"
    elif written is None:
        missing["existing_system_main_forecast"] = "the read record has no archived_at stamp"
    elif written[1] > MAIN_FORECAST_LIVE_WITHIN_SECONDS:
        missing["existing_system_main_forecast"] = f"a replay: archived {written[1]} s after the read"
    else:
        found["existing_system_main_forecast"] = {**_pct(main), "forecasts_move_measured_to": measured, "is_written_live": True,
                                                  "written_at": written[0], "written_after_read_seconds": written[1]}
    return found, missing


def _pct(probabilities: dict) -> dict:
    return {f"{k}_pct": round(float(probabilities[k]) * 100, 1) for k in DIRECTIONS if k in probabilities}


def _written_after_read(read_record: dict) -> tuple[str, int] | None:
    """``(written_at in Eastern time, seconds after the read)`` from the record's ``archived_at`` stamp, or None."""
    try:
        archived = datetime.fromisoformat(str(read_record["archived_at"]))
        row = datetime.fromisoformat(str(read_record["row_ts"]))
    except (KeyError, TypeError, ValueError):
        return None
    if archived.tzinfo is None or row.tzinfo is None:
        return None
    return archived.astimezone(ET).isoformat(timespec="seconds"), int((archived - row).total_seconds())


def load_existing_system_grades(state_dir: Path | str, day: str) -> dict[tuple[str, str], dict]:
    """The station's own newest grade per ``(row_ts, horizon)`` for ``day``, read once from integral_grades.jsonl:
    the highest rule version wins, and among equals the latest line."""
    newest: dict[tuple[str, str], dict] = {}
    for line in jsonl_store.iter_json_lines(station_stores.integral_grades_file(state_dir)):
        row_ts = str(line.get("row_ts", ""))
        if not row_ts.startswith(day) or not line.get("graded"):
            continue
        key = (row_ts, str(line.get("horizon")))
        if key not in newest or int(line.get("rule_version", 0)) >= int(newest[key].get("rule_version", 0)):
            newest[key] = line
    return newest


def existing_system_result_check(station_grades: dict[tuple[str, str], dict], row_ts: str, horizon: str,
                                 direction: str | None) -> dict | None:
    """This result against the station's own grade of the same window (next_30/next_60 only); None without one."""
    station_horizon = STATION_HORIZONS.get(horizon)
    line = station_grades.get((row_ts, station_horizon)) if station_horizon else None
    if line is None:
        return None
    return {"direction": line.get("label"), "grading_rule_version": line.get("rule_version"),
            "flat_edge_points": line.get("edge"), "graded_move_points": line.get("g"),
            "is_same_direction": line.get("label") == direction}


__all__ = ["GRADING_RULE_VERSION", "SIZE_BUCKETS", "DIRECTIONS", "SessionPrices", "load_session_prices", "grade_read",
           "direction_of", "size_bucket_of", "flat_edges_for", "comparison_forecasts_from_read_record",
           "load_existing_system_grades", "existing_system_result_check"]
