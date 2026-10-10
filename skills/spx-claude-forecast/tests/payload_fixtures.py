"""The one home for every shared test helper: file writers, a synthetic read's inputs, the station's line shapes, the
absence and leak readers, and the real station state for the smoke tests.

The scene is a duck-typed stand-in for ``spx_jev.state_builder.Scene`` with the attributes the blocks read
(``row``, ``rows_today``, ``bars``, ``prior_bars``, ``market``, ``now``, ``sigma``, ``day``, ``spot``,
``minutes_since_open``, ``minutes_to_close``, ``options_tape``, ``state_dir``). ``labels`` carries
``state`` (sentences by group) and ``figures``. Build one with ``fake_inputs(...)`` and override what a
test needs. The real-store smoke tests take the ``real_inputs`` fixture from conftest.py, which loads the
10-09 14:30 read once per session over ``state_over_the_station`` and skips without the station's state.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from spx_claude_forecast import station_stores
from spx_claude_forecast.control import ET
from spx_claude_forecast.paths import ForecastPaths
from spx_claude_forecast.payload.frozen_inputs import FrozenInputs, TapeFold, previous_session_day, slot_of

DAY = "2026-10-09"
SPOT = 7813.01
SIGMA = 75.31
REAL_READ_ID = "live:2026-10-09T14:30:12.458122-04:00"     # the real read every smoke test rebuilds
STATION_FOLDERS = ("reversion", "spx_jev", "lob_flow", "siege", "dated_gex")   # the station stores a read is built from
RECORDER_COPIES = ("dated_book", "siege_spy_minutes", "spx_5m", "vix1d_close.jsonl")   # the recorder's copies the loader reads

# A leaf of a block that may pass 1,000 without being a price level: issue counts, trade counts, headline counts,
# a feed's age in seconds, and basis points on a big day.
COUNT_FIELDS = frozenset({"add_live_approx", "add_30m_change", "trades", "fill_base_n", "captured_60m", "big_prints_10m",
                          "age_s", "bp", "dist_bp", "r12_bp", "rrod_bp"})
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


# --- files ------------------------------------------------------------------------------------------------------

def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


# --- the clock and the bars -------------------------------------------------------------------------------------

def at(hh: int, mm: int, ss: int = 0, day: str = DAY) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def prior_days(n: int, before: str = DAY) -> list[str]:
    """The ``n`` session days before ``before``, newest first, as ``Scene.prior_bars`` orders them."""
    days, day = [], before
    while len(days) < n:
        day = previous_session_day(day)
        days.append(day)
    return days


def flat_bars(day: str = DAY, until_hh: int = 14, until_mm: int = 29, price: float = SPOT, step: float = 0.0) -> list[dict]:
    """One-minute bars from 09:30 to ``until`` inclusive, each close ``step`` above the last."""
    bars, t, p = [], at(9, 30, day=day), price
    end = at(until_hh, until_mm, day=day)
    while t <= end:
        bars.append({"ts": t.isoformat(), "open": p, "high": p + 0.5, "low": p - 0.5, "close": p, "volume": 1000})
        t, p = t + timedelta(minutes=1), p + step
    return bars


def make_row(ts: datetime, spot: float = SPOT, sigma: float = SIGMA, **over) -> dict:
    row = {"ts": ts.isoformat(), "spot": spot, "sigma": sigma, "sigma_anchor": sigma, "sigma_live": sigma * 0.55,
           "vwap": spot - 15.0, "prior_close": spot - 47.0, "atm_iv": 8.44, "vix_ts": 0.833,
           "range_ruler": {"em_points": 21.0}, "gex_views": {"regime": "long_gamma", "flip": spot - 41.0},
           "dated_gex": None, "siege": None}
    row.update(over)
    return row


# --- the station's own line shapes ------------------------------------------------------------------------------

def tape_fold_line(ts: datetime, *, tilt: float = 0.054, told: float = 0.54, trades: int | None = 1500,
                   engine: str = "lob_flow") -> dict:
    """One line of the collector's record (``lob_flow/agg/<day>.jsonl``) with its 15-minute trade count; a line
    without a count is one the collector wrote before it counted."""
    snapshot = {"tilt": tilt, "determinate_share": told}
    if trades is not None:
        snapshot["tape_trades"] = trades
    return {"ts": ts.isoformat(), "engine": engine, "snapshot": snapshot, "baseline_rows": []}


def write_tape_folds(state_dir: Path, day: str, lines: list[dict]) -> Path:
    return write_jsonl(station_stores.lob_flow_agg_file(state_dir, day), lines)


def context_line(ts: datetime | str, quotes: dict | None = None, *, failed: list[str] | None = None,
                 bars: dict | None = None) -> dict:
    """One line of the station's context record as its poll writes it: a quotes snapshot with what failed, or a
    bars-only line, which is not a snapshot."""
    line = {"ts": ts if isinstance(ts, str) else ts.isoformat()}
    if quotes is not None:
        line["quotes"] = quotes
        line["failed"] = failed or []
    if bars is not None:
        line["bars"] = bars
    return line


# --- reading a block's result -----------------------------------------------------------------------------------

def absent_reasons(result) -> dict[str, str]:
    """``{path: why}`` for everything a block declared absent."""
    return {a["path"]: a["why"] for a in result.absent}


def leaks(block: str, data) -> list[str]:
    """Every leaf of a block's data or absences that could reach Claude as a price level (a number of 1,000 or
    more outside the count fields) or a date; a list's items carry the list's key."""
    found: list[str] = []

    def walk(path: str, key: str, value) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{path}.{k}", k, v)
        elif isinstance(value, list):
            for v in value:
                walk(path, key, v)
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float)) and abs(value) >= 1000 and key not in COUNT_FIELDS:
            found.append(f"{path}={value}")
        elif isinstance(value, str) and DATE_PATTERN.search(value):
            found.append(f"{path}={value!r}")

    walk(block, block, json.loads(json.dumps(data)))
    return found


def assert_no_leak(block: str, result) -> None:
    """A block's data and its absences carry no price level and no date, and the data is JSON."""
    assert leaks(block, result.data) == [], block
    assert leaks(block, result.absent) == [], block
    json.dumps(result.data)


# --- a read's inputs, offline -----------------------------------------------------------------------------------

class TapeFoldsOnDisk(dict):
    """The fixture's stand-in for ``inputs.tape_folds``: reads the collector's files under the temp state on every
    access, as the live loader would have, so a test may write the lines after building the inputs."""

    def __init__(self, state_dir: Path, day: str, prior_days: list[str], cut: datetime) -> None:
        super().__init__()
        self._args = (state_dir, day, prior_days, cut)

    def _load(self) -> dict:
        from spx_claude_forecast.payload import frozen_inputs
        return frozen_inputs.load_tape_folds(*self._args)

    def get(self, key, default=None):
        return self._load().get(key, default)

    def __getitem__(self, key):
        return self._load()[key]

    def __contains__(self, key):
        return key in self._load()

    def keys(self):
        return self._load().keys()

    def items(self):
        return self._load().items()

    def __iter__(self):
        return iter(self._load())

    def __len__(self):
        return len(self._load())


class FakeMarket:
    """``known[symbol]`` is a list of ``(datetime, value)``; ``last`` and ``first`` follow MarketContext."""

    def __init__(self, known: dict[str, list[tuple[datetime, float]]] | None = None) -> None:
        self.known = known or {}
        self.bars = {}

    def last(self, symbol: str, now: datetime, max_age_min: int | None = None):
        points = [(t, v) for t, v in self.known.get(symbol, []) if t <= now]
        if not points:
            return None
        t, v = points[-1]
        if max_age_min is not None and now - t > timedelta(minutes=max_age_min):
            return None
        return v

    def first(self, symbol: str):
        points = self.known.get(symbol, [])
        return points[0][1] if points else None

    def between(self, symbol: str, a: datetime, b: datetime):
        return [(t, v) for t, v in self.known.get(symbol, []) if a <= t <= b]

    def change(self, symbol: str, a: datetime, b: datetime):
        pts = self.between(symbol, a, b)
        return (pts[-1][1] - pts[0][1]) if len(pts) >= 2 else None

    def at(self, now: datetime):
        return {s: {"value": self.last(s, now), "known_at": now.isoformat()} for s in self.known}


def fake_inputs(tmp_path: Path, *, cut: datetime | None = None, bars: list[dict] | None = None, row: dict | None = None,
                prior_bars: dict[str, list[dict]] | None = None, market: FakeMarket | None = None,
                labels_state: dict | None = None, code_answers: dict | None = None, flat_zones=None,
                raw_diary_row: dict | None = None, first_raw_diary_row: dict | None = None, options_book: str | None = "native",
                market_snapshot: dict | None = None, snapshot_prior_closes: dict[str, float] | None = None,
                tape_folds: dict[str, list[TapeFold]] | None = None,
                dated_book: dict | None = None, spy_minute_volumes: dict | None = None, vix1d_prior_close: float | None = 10.23,
                daily_closes: dict | None = None, events=None, hour_record: dict | None = None,
                read_record: dict | None = None, **scene_over) -> FrozenInputs:
    """A read at ``cut`` (14:30:12 by default) on flat bars and a native book; the loader-provided stores (the raw
    diary rows, the market snapshot and its prior closes, the tape folds) are empty unless given."""
    cut = cut or at(14, 30, 12)
    bars = bars if bars is not None else flat_bars(until_hh=cut.hour, until_mm=max(cut.minute - 1, 0))
    row = row or make_row(cut)
    prior_bars = prior_bars if prior_bars is not None else {}
    close = at(16, 0, day=cut.date().isoformat())
    scene = SimpleNamespace(row=row, rows_today=[row], bars=bars, prior_bars=prior_bars, market=market, now=cut,
                            sigma=row["sigma"], day=cut.date().isoformat(), spot=row["spot"],
                            minutes_since_open=int((cut - at(9, 30, day=cut.date().isoformat())).total_seconds() // 60),
                            minutes_to_close=int((close - cut).total_seconds() // 60), options_tape=None,
                            state_dir=tmp_path / "state", prior_rulers={}, prior_markets={}, prior_day=None, night=[],
                            session_open=at(9, 30, day=cut.date().isoformat()), session_close=close)
    for k, v in scene_over.items():
        setattr(scene, k, v)
    labels = SimpleNamespace(state=labels_state or {}, figures={}, omitted={})
    anchor = SimpleNamespace(points=row["sigma"], source="anchor", estimated=False)
    paths = ForecastPaths(tmp_path / "state")
    return FrozenInputs(state_dir=tmp_path / "state", paths=paths, read_id=f"live:{row['ts']}", lane="live",
                        day=cut.date().isoformat(), row_ts=row["ts"], cut=cut, slot=slot_of(cut),
                        horizon_start=cut.replace(second=0, microsecond=0), scene=scene, labels=labels,
                        read_record=read_record, hour_record=hour_record, code_answers=code_answers or {},
                        flat_zones=flat_zones if flat_zones is not None else {"next_30": 3.41, "next_60": 4.89, "average_30": 2.0},
                        anchor=anchor, raw_diary_row=raw_diary_row, first_raw_diary_row=first_raw_diary_row, options_book=options_book,
                        market_snapshot=market_snapshot, snapshot_prior_closes=snapshot_prior_closes or {}, tape_folds=tape_folds if tape_folds is not None else TapeFoldsOnDisk(tmp_path / "state", cut.date().isoformat(), list(prior_bars), cut),
                        dated_book=dated_book, spy_minute_volumes=spy_minute_volumes,
                        vix1d_prior_close=vix1d_prior_close, daily_closes=daily_closes or {}, events=events, load_notes=[])


# --- the real station state -------------------------------------------------------------------------------------

def station_state_dir() -> Path | None:
    """The real station state, for smoke tests that skip without it."""
    root = Path.home() / ".claude" / "plugins" / "mirai-station" / "state"
    return root if (root / "reversion").exists() else None


def state_over_the_station(root: Path, *, recorder_copies: bool = True) -> Path:
    """A temp state root whose station stores are the real ones (read-only symlinks), with the recorder's copies
    linked under a temp forecast folder; everything the package writes lands in the temp folder. The nightly's
    backup never descends a linked folder (rglob does not follow a symlinked directory)."""
    real = station_state_dir()
    state = root / "state"
    state.mkdir()
    for name in STATION_FOLDERS:
        (state / name).symlink_to(real / name)
    if recorder_copies:
        recorder = ForecastPaths(state).recorder_dir
        recorder.mkdir(parents=True)
        for name in RECORDER_COPIES:
            if (ForecastPaths(real).recorder_dir / name).exists():
                (recorder / name).symlink_to(ForecastPaths(real).recorder_dir / name)
    return state
