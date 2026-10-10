"""Everything a payload block may read about one SPX read, loaded once and cut at the read's own time.

A payload is built from SAVED files only, never from a live quote, so a read rebuilt next month from the
same files gives the same scene. ``load_frozen_inputs`` rebuilds the station's own Scene for the read
(``spx_jev.state_builder.make_scene`` with ``at`` = the read's timestamp, which keeps the read's own
diary row and the bars finished by then), runs the station's labeller over it, and gathers the read's
archive record, code answers, flat zones, the dated options book known at the cut, SPY minute volumes,
the prior $VIX1D close, daily closes before the day and the event calendar. Blocks read from this
object and nothing else.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import jsonl_store, station_stores
from ..control import ET
from ..paths import ForecastPaths

LIVE_LANE = "live"
SLOT_MINUTES = 30                     # reads land near :00 and :30; the slot is the nearest mark
DAILY_CLOSE_SYMBOLS = ("$SPX", "$VIX", "$VIX9D", "$TNX", "TLT")


@dataclass
class FrozenInputs:
    """One read's inputs, all known at ``cut``. Station objects keep their own types (Scene, LabelSet)."""

    state_dir: Path
    paths: ForecastPaths
    read_id: str
    lane: str
    day: str                          # the market day, ET
    row_ts: str                       # the diary row's timestamp, as the station stamps it
    cut: datetime                     # the same instant as a datetime (ET)
    slot: str                         # "14:30": the half-hour mark the read belongs to
    horizon_start: datetime           # the read's minute, where the station measures its sums from
    scene: Any                        # spx_jev.state_builder.Scene
    labels: Any                       # spx_jev.labels.label_set.LabelSet
    read_record: dict | None          # the archive line (kind "read") for this read_id
    hour_record: dict | None          # the station's sum record for this read (state/spx_jev/hour/)
    code_answers: dict[str, str | None]
    flat_zones: dict[str, float] | str   # {next_30, next_60, average_30} in points, or why there are none
    anchor: Any                       # spx_jev.labels.rulers.SigmaRuler | None: the morning ruler the read could know
    dated_book: dict | None           # the newest dated options book with as_of <= cut
    spy_minute_volumes: dict[str, int] | None   # today's SPY per-minute volume so far, {minute_of_day: shares}
    vix1d_prior_close: float | None
    daily_closes: dict[str, list[dict]] = field(default_factory=dict)   # per symbol, days strictly before ``day``
    events: dict | list | None = None
    load_notes: list[str] = field(default_factory=list)                 # what could not be loaded, and why

    @property
    def spot(self) -> float:
        return float(self.scene.row["spot"])

    @property
    def sigma_points(self) -> float:
        """The read's expected daily move in index points: the morning anchor, else the row's sigma."""
        if self.anchor is not None and getattr(self.anchor, "points", None):
            return float(self.anchor.points)
        return float(self.scene.sigma)

    @property
    def minutes_to_close(self) -> int:
        return int(self.scene.minutes_to_close)


def slot_of(cut: datetime) -> str:
    """The half-hour mark a read belongs to, rounded to the nearest :00 or :30."""
    minutes = cut.hour * 60 + cut.minute + (1 if cut.second >= 30 else 0)
    rounded = int(round(minutes / SLOT_MINUTES)) * SLOT_MINUTES
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def parse_read_id(read_id: str) -> tuple[str, str]:
    """``("live", "2026-10-09T14:30:12.458122-04:00")`` from ``live:2026-10-09T14:30:12.458122-04:00``."""
    lane, _, row_ts = read_id.partition(":")
    if not lane or not row_ts:
        raise ValueError(f"not a read id: {read_id!r}")
    return lane, row_ts


def load_frozen_inputs(state_dir: Path | str, paths: ForecastPaths, read_id: str, *, with_labels: bool = True) -> FrozenInputs:
    """Rebuild one live read's inputs from the saved files. Raises when the read itself cannot be rebuilt
    (no diary row, no bars); anything optional that is missing is noted in ``load_notes`` instead."""
    lane, row_ts = parse_read_id(read_id)
    state_builder = station_stores.import_spx_jev("state_builder")
    cut = state_builder.parse_ts(row_ts).astimezone(ET)
    inputs = load_frozen_inputs_at(state_dir, paths, cut, read_id=read_id, lane=lane, with_labels=with_labels)
    if inputs.row_ts != row_ts:
        raise ValueError(f"the diary row at the cut is {inputs.row_ts}, not the read's {row_ts}")
    return inputs


def load_frozen_inputs_at(state_dir: Path | str, paths: ForecastPaths, cut: datetime, *, read_id: str | None = None,
                          lane: str = LIVE_LANE, with_labels: bool = True) -> FrozenInputs:
    """Rebuild the inputs a read at ``cut`` could know: the newest diary row at or before the cut and the bars
    finished by then. A seed read passes its slot time and gets ``read_id`` ``seed:<day>T<HH:MM>``. The
    station's labeller is the slow part (about 19 s); ``with_labels=False`` skips it and leaves ``labels``
    empty, which the seed uses because its blocks read the scene, not the sentences."""
    state_dir = Path(state_dir)
    state_builder = station_stores.import_spx_jev("state_builder")
    grade = station_stores.import_spx_jev("grade")
    cut = cut.astimezone(ET)
    day = cut.date().isoformat()
    notes: list[str] = []

    scene = state_builder.make_scene(state_dir, day, at=cut.timetz().replace(tzinfo=None))
    row_ts = scene.row["ts"]
    if with_labels:
        registry = station_stores.import_spx_jev("labels.registry")
        labels = registry.build_labels(scene)
    else:
        labels = EmptyLabels()
        notes.append("labels: skipped (seed rebuild)")
    horizon_start = grade.horizon_start(row_ts)
    read_id = read_id or f"seed:{day}T{slot_of(cut)}"

    read_record = _load_archive_read(state_dir, lane, day, read_id, notes) if read_id.startswith("live:") else None
    hour_record = _load_hour_record(state_dir, day, row_ts, notes) if read_id.startswith("live:") else None
    code_answers = _load_code_answers(state_dir, day, read_id, notes) if read_id.startswith("live:") else {}
    flat_zones = _load_flat_zones(state_dir, day, hour_record, horizon_start, scene.bars, notes)
    anchor = _load_anchor(grade, scene, row_ts, notes)
    dated_book = _load_dated_book(paths, state_dir, cut, notes)
    spy_minute_volumes = _load_spy_minute_volumes(paths, state_dir, day, notes)
    vix1d_prior_close = _load_vix1d_prior_close(paths, day, notes)
    daily_closes = _load_daily_closes(state_dir, day, notes)
    events = _load_events(notes)

    return FrozenInputs(state_dir=state_dir, paths=paths, read_id=read_id, lane=lane, day=day, row_ts=row_ts, cut=cut,
                        slot=slot_of(cut), horizon_start=horizon_start, scene=scene, labels=labels,
                        read_record=read_record, hour_record=hour_record, code_answers=code_answers,
                        flat_zones=flat_zones, anchor=anchor, dated_book=dated_book,
                        spy_minute_volumes=spy_minute_volumes, vix1d_prior_close=vix1d_prior_close,
                        daily_closes=daily_closes, events=events, load_notes=notes)


class EmptyLabels:
    """What ``labels`` is when the labeller was skipped: no sentences, no figures, nothing omitted."""

    state: dict = {}
    figures: dict = {}
    omitted: dict = {}


# --- the optional pieces, each noting its own absence ----------------------------------------------------

def _load_archive_read(state_dir: Path, lane: str, day: str, read_id: str, notes: list[str]) -> dict | None:
    try:
        live_records = station_stores.import_spx_jev("mirai_prediction.live_records")
        record = live_records.load_archive_read(state_dir, lane, day, read_id)
    except Exception as e:
        record = None
        notes.append(f"archive read: {type(e).__name__}: {e}")
    if record is None:
        notes.append("archive read: no line for this read_id")
    return record


def _load_hour_record(state_dir: Path, day: str, row_ts: str, notes: list[str]) -> dict | None:
    path = station_stores.spx_jev_dir(state_dir) / "hour" / f"{day}.jsonl"
    for line in jsonl_store.iter_json_lines(path):
        if line.get("row_ts") == row_ts:
            return line
    notes.append("hour record: none for this read")
    return None


def _load_code_answers(state_dir: Path, day: str, read_id: str, notes: list[str]) -> dict[str, str | None]:
    try:
        judgment = station_stores.import_spx_jev("judgment")
        answers = judgment.code_answers_of(state_dir, day, read_id)
    except Exception as e:
        answers = {}
        notes.append(f"code answers: {type(e).__name__}: {e}")
    if not answers:
        notes.append("code answers: none on file for this read")
    return answers


def _load_flat_zones(state_dir: Path, day: str, hour_record: dict | None, horizon_start: datetime, bars: list[dict],
                     notes: list[str]) -> dict[str, float] | str:
    """The flat zones the station grades this read against: the ones stamped on its sum record, else sized
    again from the same files (grade.zones_of), which is the same number."""
    try:
        flat_zone = station_stores.import_spx_jev("flat_zone")
        grade = station_stores.import_spx_jev("grade")
        model = flat_zone.Zones(state_dir, day)
        record = hour_record or {"row_ts": horizon_start.isoformat()}
        zones = grade.zones_of(record, model, bars)
    except Exception as e:
        zones = f"{type(e).__name__}: {e}"
    if isinstance(zones, str):
        notes.append(f"flat zones: {zones}")
    return zones


def _load_anchor(grade, scene, row_ts: str, notes: list[str]):
    try:
        return grade.read_anchor(scene.rows_today, scene.bars, scene.market, row_ts)
    except Exception as e:
        notes.append(f"anchor: {type(e).__name__}: {e}")
        return None


def _load_dated_book(paths: ForecastPaths, state_dir: Path, cut: datetime, notes: list[str]) -> dict | None:
    """The newest recorder copy whose as_of is at or before the cut; the live book only if it is older than the cut."""
    candidates: list[tuple[str, Path]] = []
    folder = paths.recorder_dir / "dated_book"
    if folder.exists():
        for copy in folder.glob("*.json"):
            candidates.append((copy.stem, copy))
    live = station_stores.dated_book_file(state_dir)
    if live.exists():
        try:
            as_of = json.loads(live.read_text(encoding="utf-8")).get("as_of")
            if isinstance(as_of, str):
                from ..recorder import book_stamp
                candidates.append((book_stamp(as_of), live))
        except (json.JSONDecodeError, AttributeError, ValueError):
            notes.append("dated book: the live file is not valid JSON")
    cut_stamp = cut.strftime("%Y-%m-%dT%H%M%S%z")
    usable = sorted((stamp, path) for stamp, path in candidates if stamp <= cut_stamp)
    if not usable:
        notes.append("dated book: none known at the cut")
        return None
    try:
        return json.loads(usable[-1][1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        notes.append(f"dated book: {type(e).__name__}: {e}")
        return None


def _load_spy_minute_volumes(paths: ForecastPaths, state_dir: Path, day: str, notes: list[str]) -> dict[str, int] | None:
    """Today's SPY minute volumes: the recorder's copy for a past day, else siege's live baseline (folded by the minute)."""
    copy = paths.siege_spy_minutes_file(day)
    try:
        if copy.exists():
            return json.loads(copy.read_text(encoding="utf-8")).get("minute_volumes")
        live = station_stores.siege_baseline_file(state_dir)
        if live.exists():
            return (json.loads(live.read_text(encoding="utf-8")).get("days") or {}).get(day)
    except (json.JSONDecodeError, OSError) as e:
        notes.append(f"spy minute volumes: {type(e).__name__}: {e}")
    notes.append("spy minute volumes: none for the day")
    return None


def _load_vix1d_prior_close(paths: ForecastPaths, day: str, notes: list[str]) -> float | None:
    prior = [line for line in jsonl_store.iter_json_lines(paths.vix1d_close_file) if str(line.get("day", "")) < day]
    if not prior:
        notes.append("vix1d prior close: none recorded before the day")
        return None
    return prior[-1].get("last")


def _load_daily_closes(state_dir: Path, day: str, notes: list[str]) -> dict[str, list[dict]]:
    """Daily OHLC per symbol for days strictly before ``day`` (the file is rewritten whole at 16:20 ET, so today's
    own line would be a leak on a same-day rebuild)."""
    out: dict[str, list[dict]] = {}
    for symbol in DAILY_CLOSE_SYMBOLS:
        rows = [r for r in jsonl_store.iter_json_lines(station_stores.daily_closes_file(state_dir, symbol))
                if str(r.get("day", "")) < day]
        if rows:
            out[symbol] = rows
        else:
            notes.append(f"daily closes: none for {symbol}")
    return out


def _load_events(notes: list[str]) -> dict | list | None:
    path = station_stores.events_calendar_file()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        notes.append(f"events calendar: {type(e).__name__}: {e}")
        return None


def minutes_between(earlier: datetime, later: datetime) -> int:
    return int((later - earlier).total_seconds() // 60)


def session_close_of(cut: datetime) -> datetime:
    sessions = station_stores.import_spx_jev("sessions")
    return sessions.session_close(cut)


def previous_session_day(day: str) -> str:
    sessions = station_stores.import_spx_jev("sessions")
    return sessions.previous_trading_day(datetime.fromisoformat(day).date()).isoformat()


__all__ = ["FrozenInputs", "load_frozen_inputs", "load_frozen_inputs_at", "EmptyLabels", "slot_of", "parse_read_id", "minutes_between", "session_close_of",
           "previous_session_day", "timedelta"]
