"""The flow block: what the near-price 0DTE options tape and SPY's own tape say in the minutes before the cut.

Two records of the same tape. The lob-flow collector's record, ``state/lob_flow/agg/{day}.jsonl``, is one line
a minute with the delta-weighted tilt of the last 15 minutes' trades, the share of that flow whose side could be
told and the trade count (lob_flow.sensors.read_tape); it is kept forever, and it gates the block: no line by
the cut is a missing tape, a newest line older than three minutes a stale one, and a count of zero or one far
from its usual for the minute (the median of the prior sessions' counts at the same clock) a failed content
check. The collector's raw tape, ``state/lob_flow/raw/{day}/tape.jsonl``, is one line per trade and is deleted
after thirty days; it is read through the station's own reader (spx_jev.labels.options_flow.tape_minutes) for
the fields the record cannot give: the signed lean of the last 30 minutes' premium, the large prints of the
last 10 minutes and the premium pace. Those are absent, never guessed, once the raw tape is gone.

SPY's volume is the per-minute volume the loader froze for today against the recorder's copies of the prior
sessions, at the same minutes. Its quoted spread is the collector's SPY reading (the context job saves SPY's
last and volume, never its bid and ask). Ranks keep every prior session with the measure, as the station's tape
and SPY ranks do: the ruler never touches these numbers.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .. import units
from ... import jsonl_store, station_stores
from ...control import ET
from ..block_result import BlockResult, whole_block_absent
from ..frozen_inputs import FrozenInputs

BLOCK = "flow"
COLLECTOR_ENGINE = "lob_flow"
TILT_WINDOW_MIN = 15              # the collector signs the last 15 minutes of trades (lob_flow.daemon.TAPE_WINDOW_MIN)
TAPE_STALE_MIN = 3                # the collector writes a line a minute, so an older newest line means it stopped
CONTENT_RATIO_LOW = 0.3           # a 15-minute trade count under 0.3x or over 3x its usual is a feed fault, not a market
CONTENT_RATIO_HIGH = 3.0
LEAN_WINDOW_MIN = 30
BIG_PRINT_WINDOW_MIN = 10
PACE_WINDOW_MIN = 30
SPY_WINDOW_MIN = 30
SPY_FEED_STALE_MIN = 5            # the siege box folds SPY's minutes about every two minutes; an older newest minute means it stopped
MIN_WINDOW_COVERAGE = 0.5         # a window with under half its minutes on file is a feed hole, not a quiet stretch
TILT_DECIMALS = 3
SHARE_DECIMALS = 2
MULTIPLE_DECIMALS = 2


@dataclass(frozen=True)
class TapeReading:
    """One line of the collector's record: when it was written, the 15-minute tilt (-1 puts bought and calls sold,
    +1 calls bought and puts sold), the share of the flow whose side could be told, and the trades counted."""
    ts: datetime
    tilt: float | None
    told_share: float | None
    trades: int


def build_flow_block(inputs: FrozenInputs) -> BlockResult:
    state_dir = inputs.scene.state_dir
    if state_dir is None:
        return whole_block_absent(BLOCK, "no_state_dir")
    prior_days = list(inputs.scene.prior_bars)
    prior = _prior_readings(inputs, prior_days)
    reading, why = _gated_reading(inputs, prior)
    if reading is None:
        return whole_block_absent(BLOCK, why)
    result = BlockResult()
    tape = _raw_tape(inputs, prior_days)
    block = {
        "tilt_15m": _tilt(reading, prior, result),
        "told_share": _told_share(reading, result),
        "lean_30m_vs_usual": _lean_vs_usual(tape, result),
        **_big_prints(tape, result),
        "premium_pace_30m_x": _premium_pace(tape, result),
        "spy_volume_30m_x": _spy_volume(inputs, prior_days, result),
        "spy_spread_cents": _spy_spread(inputs, result),
    }
    result.data = units.clean(block)
    return result


# --- the collector's record and its gate ------------------------------------------------------------------

def tape_readings(state_dir: Any, day: str) -> list[TapeReading]:
    """The collector's lines for ``day``, oldest first; a line without a trade count is not a reading."""
    out = []
    for line in jsonl_store.iter_json_lines(station_stores.lob_flow_agg_file(state_dir, day)):
        snap = line.get("snapshot") if line.get("engine") == COLLECTOR_ENGINE else None
        if not isinstance(snap, dict) or not isinstance(line.get("ts"), str) or not _is_num(snap.get("tape_trades")):
            continue
        out.append(TapeReading(datetime.fromisoformat(line["ts"]), _num(snap.get("tilt")), _num(snap.get("determinate_share")),
                               int(snap["tape_trades"])))
    return sorted(out, key=lambda r: r.ts)


def reading_at(readings: list[TapeReading], t: datetime) -> TapeReading | None:
    """The newest reading written at or before ``t`` and not more than TAPE_STALE_MIN before it."""
    done = [r for r in readings if r.ts <= t]
    if not done or t - done[-1].ts > timedelta(minutes=TAPE_STALE_MIN):
        return None
    return done[-1]


def _gated_reading(inputs: FrozenInputs, prior: list[TapeReading]) -> tuple[TapeReading | None, str | None]:
    """The reading at the cut, or why the whole block is absent: no reading by the cut, a stale one, or a trade
    count that fails the content check against the same minute on the prior sessions (``prior``)."""
    readings = [r for r in tape_readings(inputs.scene.state_dir, inputs.day) if r.ts <= inputs.cut]
    if not readings:
        return None, "tape_missing"
    reading = reading_at(readings, inputs.cut)
    if reading is None:
        return None, "tape_stale"
    if reading.trades <= 0:
        return None, "tape_content_failed"
    usual = _usual([r.trades for r in prior if r.trades > 0])
    if usual is not None and not CONTENT_RATIO_LOW * usual <= reading.trades <= CONTENT_RATIO_HIGH * usual:
        return None, "tape_content_failed"
    return reading, None


def _prior_readings(inputs: FrozenInputs, prior_days: list[str]) -> list[TapeReading]:
    """The prior sessions' readings at this minute, newest session first, where the collector had one."""
    out = []
    for day in prior_days:
        reading = reading_at(tape_readings(inputs.scene.state_dir, day), same_clock(inputs.cut, day))
        if reading is not None:
            out.append(reading)
    return out


def same_clock(cut: datetime, day: str) -> datetime:
    """The cut's clock minute on a prior session, market time."""
    return datetime.combine(date.fromisoformat(day), cut.astimezone(ET).time(), tzinfo=ET)


def _tilt(reading: TapeReading, prior: list[TapeReading], result: BlockResult) -> dict | None:
    """The collector's 15-minute tilt, ranked against the same minute's on the prior sessions."""
    if reading.tilt is None:
        result.leave_out(f"{BLOCK}.tilt_15m", "not_in_record")
        return None
    return units.ranked(round(reading.tilt, TILT_DECIMALS), [r.tilt for r in prior if r.tilt is not None])


def _told_share(reading: TapeReading, result: BlockResult) -> float | None:
    if reading.told_share is None:
        result.leave_out(f"{BLOCK}.told_share", "not_in_record")
        return None
    return round(reading.told_share, SHARE_DECIMALS)


# --- the raw tape -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RawTape:
    """Today's raw tape over the last 30 minutes as it was on file at the cut (options_flow.TapeMinutes), the prior
    sessions' over the same minutes, and the minutes of day the windows end at and the session opened at."""
    today: Any
    prior: list[Any]
    end: int
    opened: int


def _raw_tape(inputs: FrozenInputs, prior_days: list[str]) -> RawTape | None:
    """None when today's raw tape is not on file (the collector did not run, or the day is past the tape's
    thirty-day life)."""
    options_flow = station_stores.import_spx_jev("labels.options_flow")
    measures = station_stores.import_spx_jev("labels.measures")
    now_et = inputs.cut.astimezone(ET)
    end = measures.minute_of_day(now_et)
    opened = measures.minute_of_day(inputs.scene.session_open.astimezone(ET))
    clock = now_et.time()
    today = options_flow.tape_minutes(inputs.scene.state_dir, inputs.day, end - LEAN_WINDOW_MIN, end, clock)
    if today is None:
        return None
    prior = [m for d in prior_days if (m := options_flow.tape_minutes(inputs.scene.state_dir, d, end - LEAN_WINDOW_MIN, end, clock)) is not None]
    return RawTape(today, prior, end, opened)


def _window(tape: RawTape | None, minutes: int) -> tuple[Any, str | None]:
    """Today's last ``minutes`` finished minutes of the raw tape (a TapeFlow), or None and why: no raw tape on
    file, a session younger than the window, or a minute of the window with no trade (a hole in the tape)."""
    if tape is None:
        return None, "raw_tape_missing"
    if tape.end - minutes < tape.opened:
        return None, "session_too_young"
    window = tape.today.window(tape.end - minutes, tape.end)
    return window, None if window is not None else "tape_minute_hole"


def _prior_windows(tape: RawTape, minutes: int) -> list[Any]:
    """The same minutes on each prior session's raw tape, newest first, where every minute has a trade."""
    return [w for p in tape.prior if (w := p.window(tape.end - minutes, tape.end)) is not None]


def _signed_share(flow: Any) -> float | None:
    """The premium whose side could be told, signed: -1 all puts bought or calls sold, +1 all calls bought or puts sold."""
    told = flow.bullish + flow.bearish
    return (flow.bullish - flow.bearish) / told if told > 0 else None


def _bullish_share(flow: Any) -> float | None:
    told = flow.bullish + flow.bearish
    return flow.bullish / told if told > 0 else None


def _usual(base: list[float]) -> float | None:
    """The median of the prior sessions' values, newest first, over the station's rank window; None under its
    smallest count for a same-clock median."""
    cuts = station_stores.import_spx_jev("cuts")
    recent = base[:cuts.NIGHT_RANK_COUNT]
    return statistics.median(recent) if len(recent) >= cuts.MIN_RANK_SESSIONS else None


def _against_usual(value: float, base: list[float], shown: Callable[[float], float], decimals: int) -> dict | None:
    """``{"v": shown(usual), "r": ...}``: the value set against the prior sessions' median, with the rank of the
    value itself among them; None when there are too few sessions for a median."""
    usual = _usual(base)
    if usual is None:
        return None
    out = {"v": round(shown(usual), decimals)}
    rank = units.ranked(value, base)
    if rank and "r" in rank:
        out["r"] = rank["r"]
    return out


def _lean_vs_usual(tape: RawTape | None, result: BlockResult) -> dict | None:
    """The last 30 minutes' signed premium share less its usual level for this half hour, ranked against the prior
    sessions' leans at these minutes."""
    field = f"{BLOCK}.lean_30m_vs_usual"
    window, why = _window(tape, LEAN_WINDOW_MIN)
    if window is None:
        result.leave_out(field, why)
        return None
    lean = _signed_share(window)
    if lean is None:
        result.leave_out(field, "no_side_told")
        return None
    base = [v for w in _prior_windows(tape, LEAN_WINDOW_MIN) if (v := _signed_share(w)) is not None]
    out = _against_usual(lean, base, lambda usual: lean - usual, SHARE_DECIMALS)
    if out is None:
        result.leave_out(field, "too_few_sessions")
    return out


def _large_trade_lots(windows: list[Any], share: float) -> float | None:
    """The size in lots that ``share`` of the single trades in ``windows``, pooled, stayed at or under: the smallest
    large trade. None with no single trade."""
    counts: dict[float, int] = {}
    for w in windows:
        for lots, flow in w.by_lots.items():
            counts[lots] = counts.get(lots, 0) + flow.prints
    need, seen = share * sum(counts.values()), 0
    for lots in sorted(counts):
        seen += counts[lots]
        if seen >= need and seen > 0:
            return lots
    return None


def _big_prints(tape: RawTape | None, result: BlockResult) -> dict:
    """The single trades of the last 10 minutes at or above the large-trade size of these minutes on the prior
    sessions (the station's cut), and the bullish share of their told premium (calls bought or puts sold), ranked
    against the same trades' share on those sessions."""
    count_field, side_field = f"{BLOCK}.big_prints_10m", f"{BLOCK}.big_prints_bullish_pct"
    window, why = _window(tape, BIG_PRINT_WINDOW_MIN)
    if window is None:
        for field in (count_field, side_field):
            result.leave_out(field, why)
        return {}
    cuts = station_stores.import_spx_jev("cuts")
    windows = _prior_windows(tape, BIG_PRINT_WINDOW_MIN)[:cuts.NIGHT_RANK_COUNT]
    lots = _large_trade_lots(windows, cuts.BIG_PRINT_PCT)
    if len(windows) < cuts.SAME_CLOCK_MIN_SESSIONS or lots is None:
        for field in (count_field, side_field):
            result.leave_out(field, "too_few_sessions")
        return {}
    big = window.from_lots(lots)
    out = {"big_prints_10m": big.prints}
    if big.prints < cuts.BIG_MIN_PRINTS:
        result.leave_out(side_field, "under_min_prints")
        return out
    share = _bullish_share(big)
    if share is None:
        result.leave_out(side_field, "no_side_told")
        return out
    base = [v for w in windows if (b := w.from_lots(lots)).prints >= cuts.BIG_MIN_PRINTS and (v := _bullish_share(b)) is not None]
    out["big_prints_bullish_pct"] = units.ranked(round(share * 100), [round(b * 100) for b in base])
    return out


def _premium_pace(tape: RawTape | None, result: BlockResult) -> dict | None:
    """The last 30 minutes' premium as a multiple of its usual for this half hour, ranked against the prior sessions."""
    field = f"{BLOCK}.premium_pace_30m_x"
    window, why = _window(tape, PACE_WINDOW_MIN)
    if window is None:
        result.leave_out(field, why)
        return None
    if window.premium <= 0:
        result.leave_out(field, "no_premium_traded")
        return None
    base = [w.premium for w in _prior_windows(tape, PACE_WINDOW_MIN) if w.premium > 0]
    out = _against_usual(window.premium, base, lambda usual: window.premium / usual, MULTIPLE_DECIMALS)
    if out is None:
        result.leave_out(field, "too_few_sessions")
    return out


# --- SPY's own tape and quote -----------------------------------------------------------------------------

def _window_volume(minutes: dict[int, float], start: int, end: int) -> float | None:
    """SPY's volume over the minutes of day in [start, end); None when under MIN_WINDOW_COVERAGE of them are on file."""
    got = [minutes[m] for m in range(start, end) if m in minutes]
    return sum(got) if got and len(got) >= MIN_WINDOW_COVERAGE * (end - start) else None


def _minute_volumes(raw: Any) -> dict[int, float]:
    """``{minute of day: shares}`` from a ``{"570": 312741, ...}`` map, a minute keyed by when its bar started."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for m, v in raw.items():
        if _is_num(v) and str(m).isdigit():
            out[int(m)] = float(v)
    return out


def _recorded_volumes(inputs: FrozenInputs, day: str) -> dict[int, float]:
    path = inputs.paths.siege_spy_minutes_file(day)
    try:
        return _minute_volumes(json.loads(path.read_text(encoding="utf-8")).get("minute_volumes"))
    except (OSError, ValueError, AttributeError):
        return {}


def _spy_volume(inputs: FrozenInputs, prior_days: list[str], result: BlockResult) -> dict | None:
    """SPY's volume over the last 30 finished minutes as a multiple of its usual at these minutes (the median of
    the recorder's copies of the prior sessions), ranked against those sessions."""
    field = f"{BLOCK}.spy_volume_30m_x"
    measures = station_stores.import_spx_jev("labels.measures")
    end = measures.minute_of_day(inputs.cut.astimezone(ET))
    opened = measures.minute_of_day(inputs.scene.session_open.astimezone(ET))
    today = {m: v for m, v in _minute_volumes(inputs.spy_minute_volumes).items() if m < end}
    if not today:
        result.leave_out(field, "no_spy_minutes")
        return None
    if end - SPY_WINDOW_MIN < opened:
        result.leave_out(field, "session_too_young")
        return None
    if max(today) < end - SPY_FEED_STALE_MIN:
        result.leave_out(field, "spy_feed_stopped")
        return None
    volume = _window_volume(today, end - SPY_WINDOW_MIN, end)
    if volume is None:
        result.leave_out(field, "spy_minutes_incomplete")
        return None
    base = [v for d in prior_days if (v := _window_volume(_recorded_volumes(inputs, d), end - SPY_WINDOW_MIN, end)) is not None and v > 0]
    out = _against_usual(volume, base, lambda usual: volume / usual, MULTIPLE_DECIMALS)
    if out is None:
        result.leave_out(field, "too_few_sessions")
    return out


def _spy_spread(inputs: FrozenInputs, result: BlockResult) -> int | None:
    """SPY's quoted spread in whole cents from the collector's newest SPY reading by the cut (the context job keeps
    no bid and ask)."""
    field = f"{BLOCK}.spy_spread_cents"
    options_flow = station_stores.import_spx_jev("labels.options_flow")
    record = options_flow.collector_record(inputs.scene.state_dir, inputs.day)
    readings = [(ts, spread) for ts, spread, _size in (record.spy if record is not None else []) if ts <= inputs.cut]
    if not readings:
        result.leave_out(field, "not_recorded")
        return None
    ts, spread = readings[-1]
    if inputs.cut - ts > timedelta(minutes=TAPE_STALE_MIN):
        result.leave_out(field, "spy_quote_stale")
        return None
    return round(spread * 100)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num(v: Any) -> float | None:
    return float(v) if _is_num(v) else None
