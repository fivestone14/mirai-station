"""The direction line: a separate SPX line that answers only "if it moves, which way?" for the 30-minute average-price
box, calling Up or Down when it is confident and nothing when it is not. Will, 2026-10-13. The combined forecast
(live_call, learner_pool) is never touched: this line reads its voices' lines and keeps its own files.

    python -m spx_jev.mirai_prediction.direction_line --read-id <id> [--state-dir <state>]     after a live read (service_hook)
    python -m spx_jev.mirai_prediction.direction_line --catch-up YYYY-MM-DD                      one day's reads with no call yet
    python -m spx_jev.mirai_prediction.direction_line --replay-from YYYY-MM-DD                   every day from there to today (the warm start)

Every run, under the lock (DIRECTION_LINE_JOB_LOCK), in this order:

    1. GRADE   every earlier call, any day, whose window has closed: the read's spot and stamped ``average_30`` zone edge
               (flat_zone.stamped on the hour record) against the day's minute bars. A window with a bar missing waits
               while its day is today and is written as not graded once the day is over: no bar is ever filled. The
               previous day's last read is graded at the next morning's first run, so nothing nightly is needed.
    2. LEARN   one weight step per newly graded read, never twice (the state keeps the graded read ids).
    3. CALL    wait up to LINES_WAIT_S for the read's ``average_30`` forecaster lines (source "live" only), mix the voices'
               which-way odds by the weights, gate, append the call (revision 0) and write the card.
    4. CLAUDE  poll until the read's minute + CLAUDE_WAIT_MIN for Claude's final forecast of the read (test_variant none);
               when it lands, mix again with Claude as a voice and append revision 1, for the phone alone. Revision 0 is
               what is graded: no call waits on Claude.

The voices answer "which way?" alone: P(up | move) = up / (up + down), floored to [Q_FLOOR, 1 - Q_FLOOR], from the
forecaster lines of historical_odds, additive_scorer, matcher and jev_corrected, and from Claude's next_30_minutes
when present. JEV's own read, the blends, the JEV mixes and the two pools are left out (raw JEV direction was 40%
right over the graded reads). The weights start equal and are renormalised over the voices present each read.

The points grade, one signed number S per read in the Up view (a Down call scores -S): with Z the stamped edge and
a_t the average of the closes from the read through minute t less the read's spot (the running average, so the last
minute is the official average-price grade), d_t = min((a_t - Z) / Z, D_CAP) past the edge above, max((a_t + Z) / Z,
-D_CAP) past it below, 0 inside; w_t = T + 1 - t, sooner counting more; S = 100 x sum(w_t d_t) / (D_CAP x sum(w_t)),
in [-100, 100]. A call is right when its side's points are RIGHT_POINTS or more, wrong at -RIGHT_POINTS or less,
stayed_flat between. The official side is the last minute's average against Z.

Learning, per graded read: s = S / 100; for each voice present q_c = q if s > 0 else 1 - q; g = |s| x (1 + log2 q_c)
(a coin flip scores 0, a proper score, no reward for overconfidence); log w += clip(ETA x g, -STEP_CLIP, STEP_CLIP);
renormalise over every voice, Fixed-Share ALPHA toward the start weights, no voice above SHARE_CAP of the say.

The call gate: p = sum(share x q) over the voices present; Up when p >= 0.5 else Down; confidence c = max(p, 1 - p).
At the cold start a call needs c >= COLD_START_CONFIDENCE. Each confidence bin (BINS) keeps a decayed count and
points sum (BIN_DECAY per read landing in it), every graded read counted on the side the mix leaned, called or not;
once a bin holds BIN_MIN_READS graded reads it is on when its mean points are above BIN_ON_ABOVE, off below
BIN_OFF_BELOW, and keeps its last decision between. Expected points = (2c - 1) x the running mean |S| of the read's
half-hour slot (SLOT_DEFAULT_MEAN until SLOT_MIN_READS reads).

Files (station_paths, under spx/prediction/direction_line/): the calls (one row per revision of one read), the grades
(one row per graded read), the weights state (one file for the average-price box), the phone card under
screen_files/phone/ and the lock under operations/locks/. Every run is logged in PREDICTION_JOB_RUNS_LOG as
"direction_line". The replay makes a call only for a read that has none, as of the moment the live hook would have
run (the forecaster lines' created_at), so its weights at each call are the ones the live line would have had.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .. import flat_zone
from ..grade import horizon_start, mark_at
from ..labels.measures import ET, ONE_MINUTE, bar_time
from ..lane import LIVE
from ..state_builder import load_bars, load_jsonl
from .name_map import LEARNER_POOL
from .paths import (append_json_line, backup_before_change, data_root, direction_line_calls_dir, direction_line_calls_file,
                    direction_line_card_file, direction_line_grades_dir, direction_line_grades_file, direction_line_lock_file,
                    direction_line_weights_file, log_job_run, now_utc_iso, raw_forecasts_file, read_json_lines, station_paths,
                    today_et, write_json_atomically)

LANE = "live"
SUM_ID = "average_30"
LINE_VOICES = ("historical_odds", "additive_scorer", "matcher", "jev_corrected")   # from the forecaster lines
CLAUDE = "claude_forecast"
VOICES = (*LINE_VOICES, CLAUDE)
CLAUDE_HORIZON = "next_30_minutes"
Q_FLOOR = 0.1                      # P(up | move) is kept in [Q_FLOOR, 1 - Q_FLOOR]
D_CAP = 2.0                        # the distance past the edge is capped at this many edges
RIGHT_POINTS = 10.0                # a call is right from this many points on its side, wrong from -this
ETA = 1.0                          # the learning rate
STEP_CLIP = 0.5                    # one read moves a log weight at most this much
ALPHA = 0.002                      # Fixed-Share toward the start weights, per read
SHARE_CAP = 0.60                   # no voice above this share of the say
COLD_START_CONFIDENCE = 0.55       # a call needs this confidence until the bins have decided
BINS = (("50_55", 0.50, 0.55), ("55_60", 0.55, 0.60), ("60_65", 0.60, 0.65), ("65_up", 0.65, 1.01))
BIN_DECAY = 0.98                   # a bin's count and points sum decay this much per read landing in it
BIN_MIN_READS = 8                  # a bin decides for itself from this many graded reads
BIN_ON_ABOVE = 0.0                 # mean points above this: the bin calls
BIN_OFF_BELOW = -5.0               # mean points below this: it does not; between, it keeps its last decision
SLOT_DEFAULT_MEAN = 15.0           # the expected |S| of a half-hour slot until SLOT_MIN_READS of its reads are graded
SLOT_MIN_READS = 4
LINES_WAIT_S = 60.0                # how long a live run waits for the read's forecaster lines
LINES_POLL_S = 2.0
CLAUDE_WAIT_MIN = 7                # Claude's forecast joins when it lands by the read's minute + this
CLAUDE_POLL_S = 15.0
DAYS_SCANNED = 10                  # the newest call-file days looked through for calls still to grade
GRADED_IDS_KEPT = 300              # the state keeps the newest graded read ids
REPLAY_HOOK_LAG = timedelta(minutes=3)   # a replayed read's run moment when its lines carry no created_at
STATE_VERSION = 1
NOT_GRADED = "not_graded"
JOB = "direction_line"


# --- the voices ------------------------------------------------------------------------------------------------------

def which_way(probs: dict | None) -> float | None:
    """P(up | move) from up/flat/down odds: up / (up + down), floored to [Q_FLOOR, 1 - Q_FLOOR]; None without a move."""
    if not isinstance(probs, dict):
        return None
    try:
        up, down = float(probs.get("up") or 0.0), float(probs.get("down") or 0.0)
    except (TypeError, ValueError):
        return None
    if up + down <= 0:
        return None
    return min(1 - Q_FLOOR, max(Q_FLOOR, up / (up + down)))


def forecaster_lines(root: Path, day: str, read_id: str) -> list[dict]:
    """The read's live ``average_30`` lines from the forecaster file, in file order."""
    return [r for r in read_json_lines(raw_forecasts_file(root, day))
            if r.get("read_id") == read_id and r.get("sum_id") == SUM_ID and r.get("source") == "live"]


def line_voices(lines: list[dict]) -> tuple[dict[str, float], float | None, str | None]:
    """({voice: P(up | move)} over the line voices present, the learner pool's P(move) for the display, when the
    lines were created); the last line of a voice wins."""
    probs: dict[str, dict] = {}
    created = None
    for r in lines:
        probs[r.get("voice_name")] = r.get("voice_probs")
        created = r.get("created_at") or created
    qs = {v: q for v in LINE_VOICES if (q := which_way(probs.get(v))) is not None}
    pool = probs.get(LEARNER_POOL)
    p_move = None
    if isinstance(pool, dict):
        try:
            p_move = round(float(pool.get("up") or 0.0) + float(pool.get("down") or 0.0), 4)
        except (TypeError, ValueError):
            p_move = None
    return qs, p_move, created


def claude_line(state_dir: Path | str, day: str, read_id: str) -> dict | None:
    """Claude's final forecast line of the read (the live one, test_variant none), else None."""
    path = station_paths.CLAUDE_FORECAST_ANSWERS_BY_READ.path(state_dir, day=day)
    found = None
    for r in load_jsonl(path):
        if r.get("line_type") == "final_forecast" and r.get("read_id") == read_id and r.get("test_variant") in (None, "none"):
            found = r
    return found


def claude_which_way(line: dict | None) -> float | None:
    """Claude's P(up | move) over the next 30 minutes from its final forecast line; None when the horizon is not ok."""
    block = ((line or {}).get("forecast") or {}).get(CLAUDE_HORIZON)
    if not isinstance(block, dict) or block.get("status") != "ok":
        return None
    return which_way({"up": block.get("up_pct"), "down": block.get("down_pct")})


def claude_in_time(line: dict | None, row_ts: str) -> bool:
    """Whether Claude's line landed by the read's minute + CLAUDE_WAIT_MIN, as the live run would have taken it."""
    written = (line or {}).get("written_at")
    if not isinstance(written, str):
        return line is not None
    try:
        return datetime.fromisoformat(written) <= horizon_start(row_ts, LIVE) + timedelta(minutes=CLAUDE_WAIT_MIN)
    except ValueError:
        return False


# --- the reads -------------------------------------------------------------------------------------------------------

def hour_records(state_dir: Path | str, day: str) -> dict[str, dict]:
    """The live lane's hour records of the day that carry a stamped zone and a spot, by row_ts; the first of a row
    written twice is the one kept, as the grader keeps it."""
    out: dict[str, dict] = {}
    for r in load_jsonl(station_paths.JEV_AND_POOL_FORECASTS_BY_READ.path(state_dir, lane=LANE, day=day)):
        row_ts = r.get("row_ts")
        if not isinstance(row_ts, str) or row_ts in out or zone_edge(r) is None or not isinstance(r.get("spot"), (int, float)):
            continue
        out[row_ts] = r
    return out


def zone_edge(rec: dict) -> float | None:
    """The read's stamped ``average_30`` edge in points (flat_zone.stamped), else None: a read without the stamp is
    neither called nor graded."""
    zones = flat_zone.stamped(rec)
    edge = zones.get(SUM_ID) if zones else None
    return float(edge) if isinstance(edge, (int, float)) and edge > 0 else None


def read_id_of(row_ts: str) -> str:
    return f"{LANE}:{row_ts}"


def day_of(read_id: str) -> str:
    return read_id.split(":", 1)[1][:10]


def slot_of(row_ts: str) -> str:
    """The read's half-hour slot on the market clock: 09:32 is 09:30, 10:02 is 10:00."""
    t0 = horizon_start(row_ts, LIVE).astimezone(ET)
    return f"{t0.hour:02d}:{(t0.minute // 30) * 30:02d}"


# --- the points grade ------------------------------------------------------------------------------------------------

def window_closes(row_ts: str) -> tuple[datetime, datetime | None]:
    """(the window's start, its mark) for the read: the read's minute and 30 minutes on, or the close when the box
    ends within the grader's grace; a mark of None is a window that never closes in the session."""
    return horizon_start(row_ts, LIVE), mark_at(row_ts, LIVE.horizons[LIVE.primary][0], LIVE)


def known_bars(bars: list[dict], now: datetime) -> list[dict]:
    """The bars finished by ``now`` (every bar on file, live; the ones a replayed run could have seen)."""
    return [b for b in bars if bar_time(b) + ONE_MINUTE <= now]


def points_grade(bars: list[dict], t0: datetime, mark: datetime, spot: float, edge: float) -> dict:
    """The points grade of the window from ``t0`` to ``mark`` on the bars finished: S in the Up view, the official
    side, and the running average at every fifth minute and the last. ``{"graded": False, "missing": [...]}`` when a
    minute of the window has no bar."""
    minutes = int((mark - t0).total_seconds() // 60)
    by = {bar_time(b): b for b in bars}
    slots = [by.get(t0 + k * ONE_MINUTE) for k in range(minutes)]
    missing = [k + 1 for k, b in enumerate(slots) if b is None]
    if minutes <= 0 or missing:
        return {"graded": False, "minutes": minutes, "missing": missing}
    total, weighted, weights, checks = 0.0, 0.0, 0.0, []
    for t, b in enumerate(slots, start=1):
        total += float(b["close"]) - spot
        a = total / t
        d = min((a - edge) / edge, D_CAP) if a > edge else max((a + edge) / edge, -D_CAP) if a < -edge else 0.0
        w = minutes + 1 - t
        weighted += w * d
        weights += w
        if t % 5 == 0 or t == minutes:
            checks.append({"minute": t, "average": round(a, 2), "edges": round(d, 3)})
    s = 100.0 * weighted / (D_CAP * weights)
    last = checks[-1]["average"]
    return {"graded": True, "minutes": minutes, "up_points": round(s, 2), "official_side": "up" if last > edge else "down" if last < -edge else "flat",
            "checks": checks}


def result_of(points: float) -> str:
    return "right" if points >= RIGHT_POINTS else "wrong" if points <= -RIGHT_POINTS else "stayed_flat"


# --- the state: weights, bins, slots ---------------------------------------------------------------------------------

def new_state() -> dict:
    w = 1.0 / len(VOICES)
    return {"version": STATE_VERSION, "sum_id": SUM_ID, "created_at": now_utc_iso(), "start_weights": {v: w for v in VOICES},
            "log_weights": {v: math.log(w) for v in VOICES}, "reads_learned": 0, "graded_read_ids": [], "last_learned": None,
            "bins": {name: {"low": low, "high": high, "reads": 0, "count": 0.0, "points": 0.0, "on": low >= COLD_START_CONFIDENCE, "decided": False}
                     for name, low, high in BINS},
            "slot_means": {},
            "constants": {"eta": ETA, "step_clip": STEP_CLIP, "alpha": ALPHA, "share_cap": SHARE_CAP, "q_floor": Q_FLOOR, "d_cap": D_CAP,
                          "right_points": RIGHT_POINTS, "cold_start_confidence": COLD_START_CONFIDENCE, "bin_decay": BIN_DECAY,
                          "bin_min_reads": BIN_MIN_READS, "bin_on_above": BIN_ON_ABOVE, "bin_off_below": BIN_OFF_BELOW,
                          "slot_default_mean": SLOT_DEFAULT_MEAN, "slot_min_reads": SLOT_MIN_READS}}


def load_state(root: Path) -> dict:
    path = direction_line_weights_file(root, SUM_ID)
    if not path.exists():
        return new_state()
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(root: Path, state: dict) -> Path:
    path = direction_line_weights_file(root, SUM_ID)
    backup_before_change(root, path, name=f"direction_line_weights_state_{path.name}")   # the learner pool's state has the same file name
    write_json_atomically(path, state)
    return path


def shares(state: dict, voices: list[str] | None = None) -> dict[str, float]:
    """The weights as shares summing to one over ``voices`` (every voice when None)."""
    logs = state["log_weights"]
    names = [v for v in (voices if voices is not None else list(logs)) if v in logs]
    if not names:
        return {}
    top = max(logs[v] for v in names)
    e = {v: math.exp(logs[v] - top) for v in names}
    s = sum(e.values())
    return {v: x / s for v, x in e.items()}


def mix(state: dict, qs: dict[str, float]) -> tuple[float | None, dict[str, float]]:
    """(P(up | move) over the voices present, their shares); (None, {}) with no voice."""
    present = shares(state, [v for v in VOICES if v in qs])
    if not present:
        return None, {}
    return sum(present[v] * qs[v] for v in present), present


def learn(state: dict, qs: dict[str, float], up_points: float) -> dict:
    """One weight step from a graded read: every voice present is scored on the side the points went, the step
    clipped, the weights renormalised, pulled ALPHA toward the start and capped at SHARE_CAP. Returns the step."""
    s = up_points / 100.0
    steps = {}
    logs = dict(state["log_weights"])
    for v, q in qs.items():
        if v not in logs or s == 0:
            continue
        q_c = q if s > 0 else 1 - q
        g = abs(s) * (1 + math.log2(q_c))
        step = max(-STEP_CLIP, min(STEP_CLIP, ETA * g))
        logs[v] += step
        steps[v] = round(step, 4)
    top = max(logs.values())
    e = {v: math.exp(x - top) for v, x in logs.items()}
    total = sum(e.values())
    new = {v: (1 - ALPHA) * e[v] / total + ALPHA * state["start_weights"][v] for v in logs}
    over = [v for v, x in new.items() if x > SHARE_CAP]
    if over:                                                      # at most one voice can be over: the rest share the remainder
        rest = sum(x for v, x in new.items() if v not in over)
        new = {v: SHARE_CAP if v in over else (1 - SHARE_CAP) * x / rest for v, x in new.items()} if rest > 0 else new
    state["log_weights"] = {v: math.log(max(x, 1e-12)) for v, x in new.items()}
    state["reads_learned"] = state.get("reads_learned", 0) + 1
    return {"steps": steps, "shares": {v: round(x, 4) for v, x in new.items()}, "capped": over}


def bin_of(confidence: float) -> str:
    for name, low, high in BINS:
        if low <= confidence < high:
            return name
    return BINS[-1][0]


def note_bin(state: dict, name: str, side_points: float) -> dict:
    """A graded read lands in its confidence bin: the decayed count and points move, and from BIN_MIN_READS reads
    the bin decides whether it calls."""
    b = state["bins"][name]
    b["reads"] = b.get("reads", 0) + 1
    b["count"] = BIN_DECAY * b.get("count", 0.0) + 1.0
    b["points"] = BIN_DECAY * b.get("points", 0.0) + side_points
    if b["reads"] >= BIN_MIN_READS:
        mean = b["points"] / b["count"]
        if mean > BIN_ON_ABOVE:
            b["on"] = True
        elif mean < BIN_OFF_BELOW:
            b["on"] = False
        b["decided"] = True
    return b


def note_slot(state: dict, slot: str, up_points: float) -> None:
    m = state["slot_means"].setdefault(slot, {"reads": 0, "abs_points": 0.0})
    m["reads"] += 1
    m["abs_points"] += abs(up_points)


def slot_mean(state: dict, slot: str) -> float:
    m = state["slot_means"].get(slot) or {}
    return m["abs_points"] / m["reads"] if m.get("reads", 0) >= SLOT_MIN_READS else SLOT_DEFAULT_MEAN


def gate(state: dict, p_up: float, slot: str) -> dict:
    """The call from the mixed P(up | move): its side, confidence, bin and whether the bin calls, with the expected points."""
    side = "up" if p_up >= 0.5 else "down"
    confidence = max(p_up, 1 - p_up)
    name = bin_of(confidence)
    b = state["bins"][name]
    called = bool(b.get("on"))
    why = (f"bin {name} {'calls' if called else 'does not call'}: mean points {b['points'] / b['count']:+.1f} over {b['reads']} reads"
           if b.get("decided") else f"cold start: confidence {confidence:.2f} {'reaches' if called else 'is under'} {COLD_START_CONFIDENCE:.2f}")
    return {"side": side, "confidence": round(confidence, 4), "called": called, "gate": {"bin": name, "why": why},
            "expected_points": round((2 * confidence - 1) * slot_mean(state, slot), 1)}


# --- the files -------------------------------------------------------------------------------------------------------

def ensure_direction_line_folders(root: Path) -> Path:
    for folder in (direction_line_calls_dir(root), direction_line_grades_dir(root), direction_line_weights_file(root, SUM_ID).parent,
                   direction_line_card_file(root).parent, direction_line_lock_file(root).parent):
        folder.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def held_lock(root: Path) -> Iterator[None]:
    """The line's lock for the block: a run beside another (a replay by hand beside the live hook) waits its turn."""
    with open(direction_line_lock_file(root), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def call_days(root: Path) -> list[str]:
    folder = direction_line_calls_dir(root)
    return sorted(p.name[:10] for p in folder.glob("????-??-??.jsonl*")) if folder.is_dir() else []


def calls_of_day(root: Path, day: str) -> list[dict]:
    return load_jsonl(direction_line_calls_file(root, day))


def grades_of_day(root: Path, day: str) -> list[dict]:
    return load_jsonl(direction_line_grades_file(root, day))


def grade_days(root: Path) -> list[str]:
    folder = direction_line_grades_dir(root)
    return sorted({p.name[:10] for p in folder.glob("????-??-??.jsonl*")}) if folder.is_dir() else []


def calls_by_read(rows: list[dict]) -> dict[str, list[dict]]:
    """A day's call rows by read id, each read's revisions in order."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        if isinstance(r.get("read_id"), str):
            out.setdefault(r["read_id"], []).append(r)
    for revisions in out.values():
        revisions.sort(key=lambda r: int(r.get("revision") or 0))
    return out


def learned_voices(revisions: list[dict]) -> dict[str, float]:
    """The voices a read is learned on: every voice that spoke on any revision, each at its newest q (Claude joins
    at revision 1, so this is how its weight is earned)."""
    qs: dict[str, float] = {}
    for r in revisions:
        for v, q in (r.get("voices") or {}).items():
            if v in VOICES and isinstance(q, (int, float)):
                qs[v] = float(q)
    return qs


# --- the call --------------------------------------------------------------------------------------------------------

def make_call(root: Path, state: dict, rec: dict, read_id: str, qs: dict[str, float], p_move: float | None, revision: int,
              claude_included: bool) -> dict | None:
    """Mix the voices, gate, append one call row for the read and return it; None with no voice to mix."""
    p_up, say = mix(state, qs)
    if p_up is None:
        return None
    row_ts = rec["row_ts"]
    row = {"read_id": read_id, "day": day_of(read_id), "row_ts": row_ts, "sum_id": SUM_ID, "revision": revision, "spot": rec["spot"],
           "zone_edge_points": zone_edge(rec), "p_move": p_move, "voices": {v: round(q, 4) for v, q in qs.items()},
           "shares": {v: round(x, 4) for v, x in say.items()}, "p_up": round(p_up, 4), **gate(state, p_up, slot_of(row_ts)),
           "claude_included": claude_included, "written_at": now_utc_iso()}
    append_json_line(direction_line_calls_file(root, row["day"]), row)
    return row


# --- grading and learning ----------------------------------------------------------------------------------------------

class DayFiles:
    """One run's view of the days it touches: the hour records and the bars of each day, read once."""

    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self._records: dict[str, dict[str, dict]] = {}
        self._bars: dict[str, list[dict]] = {}

    def records(self, day: str, again: bool = False) -> dict[str, dict]:
        """The day's stamped hour records by row_ts; ``again`` reads the file afresh (a record can land after a run starts)."""
        if day not in self._records or again:
            self._records[day] = hour_records(self.state_dir, day)
        return self._records[day]

    def bars(self, day: str) -> list[dict]:
        if day not in self._bars:
            self._bars[day] = load_bars(self.state_dir, day)
        return self._bars[day]


def graded_ids(root: Path, state: dict, days: list[str]) -> set[str]:
    """The reads already graded: the state's list and the grade files of ``days``."""
    ids = set(state.get("graded_read_ids") or [])
    for day in days:
        ids.update(r["read_id"] for r in grades_of_day(root, day) if isinstance(r.get("read_id"), str))
    return ids


def grade_pending(root: Path, files: DayFiles, state: dict, now: datetime) -> list[dict]:
    """Grade and learn every call (revision 0) whose window has closed by ``now``, oldest first, and write the grade
    rows; a window still open, or one with a bar missing on a day that is today, is left for a later run. Returns
    the rows written."""
    days = call_days(root)[-DAYS_SCANNED:]
    done = graded_ids(root, state, days)
    today = now.astimezone(ET).date().isoformat()
    pending = []
    for day in days:
        for read_id, revisions in calls_by_read(calls_of_day(root, day)).items():
            if read_id not in done and revisions:
                pending.append((revisions[0]["row_ts"], day, read_id, revisions))
    written = []
    for row_ts, day, read_id, revisions in sorted(pending):
        first = revisions[0]
        rec = files.records(day).get(row_ts)
        t0, mark = window_closes(row_ts)
        base = {"read_id": read_id, "day": day, "row_ts": row_ts, "sum_id": SUM_ID, "revision_graded": int(first.get("revision") or 0),
                "side": first.get("side"), "called": bool(first.get("called")), "confidence": first.get("confidence"),
                "bin": (first.get("gate") or {}).get("bin"), "spot": first.get("spot"), "zone_edge_points": first.get("zone_edge_points")}
        if rec is None or mark is None:
            row = {**base, "result": NOT_GRADED, "why": "no hour record with a stamped zone" if rec is None else "the window runs past the close",
                   "graded_at": now_utc_iso()}
        else:
            if now < mark + ONE_MINUTE:
                continue                                          # the window is still open
            grade = points_grade(known_bars(files.bars(day), now), t0, mark, float(rec["spot"]), zone_edge(rec))
            if not grade["graded"]:
                if day >= today:
                    continue                                      # a bar can still come today; a finished day's hole is final
                row = {**base, "result": NOT_GRADED, "why": f"bars missing at minutes {grade['missing']}", "minutes": grade["minutes"],
                       "graded_at": now_utc_iso()}
            else:
                up_points = grade["up_points"]
                side_points = up_points if first.get("side") == "up" else -up_points
                row = {**base, "minutes": grade["minutes"], "points": round(side_points, 2), "up_points": up_points,
                       "official_side": grade["official_side"], "result": result_of(side_points), "checks": grade["checks"],
                       "graded_at": now_utc_iso()}
                step = learn(state, learned_voices(revisions), up_points)
                if base["bin"] in state["bins"]:
                    note_bin(state, base["bin"], side_points)
                note_slot(state, slot_of(row_ts), up_points)
                state["last_learned"] = {"read_id": read_id, "up_points": up_points, **step, "at": row["graded_at"]}
        append_json_line(direction_line_grades_file(root, day), row)
        state["graded_read_ids"] = (list(state.get("graded_read_ids") or []) + [read_id])[-GRADED_IDS_KEPT:]
        written.append(row)
    return written


# --- the card ----------------------------------------------------------------------------------------------------------

def record_of(rows: list[dict]) -> dict:
    """The record over graded rows: the calls made and how they went, with "always up" on the same reads."""
    graded = [r for r in rows if r.get("result") != NOT_GRADED and isinstance(r.get("up_points"), (int, float))]
    calls = [r for r in graded if r.get("called")]
    out = {"graded_reads": len(graded), "calls": len(calls), "right": sum(r["result"] == "right" for r in calls),
           "wrong": sum(r["result"] == "wrong" for r in calls), "stayed_flat": sum(r["result"] == "stayed_flat" for r in calls),
           "mean_points": round(sum(r["points"] for r in calls) / len(calls), 1) if calls else None,
           "always_up_mean_points": round(sum(r["up_points"] for r in calls) / len(calls), 1) if calls else None}
    return out


def write_card(root: Path, state: dict, today: str) -> Path:
    """The phone's card: the newest read's newest call revision, the record today and since the start, the weights."""
    newest = None
    for day in reversed(call_days(root)[-DAYS_SCANNED:]):
        rows = calls_of_day(root, day)
        if rows:
            newest = max(rows, key=lambda r: (r.get("row_ts") or "", int(r.get("revision") or 0)))
            break
    grades = [r for day in grade_days(root) for r in grades_of_day(root, day)]
    days = sorted({r["day"] for r in grades if r.get("day")})
    card = {"read_id": (newest or {}).get("read_id"), "day": (newest or {}).get("day"), "row_ts": (newest or {}).get("row_ts"),
            "call": newest, "record": {"today": record_of([r for r in grades if r.get("day") == today]), "since_start": record_of(grades),
                                       "first_day": days[0] if days else None},
            "shares": {v: round(x, 4) for v, x in shares(state).items()}, "reads_learned": state.get("reads_learned", 0),
            "written_at": now_utc_iso()}
    path = direction_line_card_file(root)
    write_json_atomically(path, card)
    return path


# --- the runs ------------------------------------------------------------------------------------------------------------

def run_for_read(state_dir: Path | str, read_id: str) -> dict:
    """The live hook's whole job for one read: grade and learn what has closed, call the read, then take Claude's
    revision when it lands in time. Never raises; every run is logged."""
    started_at = now_utc_iso()
    root = ensure_direction_line_folders(data_root(state_dir))
    result: dict = {"read_id": read_id}
    try:
        with held_lock(root):
            state = load_state(root)
            files = DayFiles(state_dir)
            day = day_of(read_id)
            graded = grade_pending(root, files, state, datetime.now(ET))
            if graded:
                save_state(root, state)
            result.update(graded=len(graded), learned=sum(r.get("result") != NOT_GRADED for r in graded))
            row_ts = read_id.split(":", 1)[1]
            rec = files.records(day).get(row_ts) or files.records(day, again=True).get(row_ts)
            if rec is None:
                result["why"] = "no hour record with a stamped zone for the read"
                write_card(root, state, today_et())
                log_job_run(root, JOB, started_at, True, **result)
                return result
            deadline = time.monotonic() + LINES_WAIT_S
            lines = forecaster_lines(root, day, read_id)
            while not lines and time.monotonic() < deadline:
                time.sleep(LINES_POLL_S)
                lines = forecaster_lines(root, day, read_id)
            qs, p_move, _ = line_voices(lines)
            if not qs:
                result["why"] = "no forecaster lines for the read"
                write_card(root, state, today_et())
                log_job_run(root, JOB, started_at, True, **result)
                return result
            if calls_by_read(calls_of_day(root, day)).get(read_id):
                result["why"] = "the read already has a call"
                write_card(root, state, today_et())
                log_job_run(root, JOB, started_at, True, **result)
                return result
            claude = claude_line(state_dir, day, read_id)
            claude_q = claude_which_way(claude) if claude_in_time(claude, row_ts) else None
            if claude_q is not None:
                qs[CLAUDE] = claude_q
            call = make_call(root, state, rec, read_id, qs, p_move, 0, claude_q is not None)
            write_card(root, state, today_et())
            result.update(called=call["called"], side=call["side"], p_up=call["p_up"], revision=0, claude=claude_q is not None)
            if claude_q is None:
                until = horizon_start(row_ts, LIVE) + timedelta(minutes=CLAUDE_WAIT_MIN)
                while True:
                    claude = claude_line(state_dir, day, read_id)
                    claude_q = claude_which_way(claude) if claude_in_time(claude, row_ts) else None
                    if claude_q is not None or datetime.now(ET) >= until:
                        break
                    time.sleep(CLAUDE_POLL_S)
                if claude_q is not None:
                    revised = make_call(root, state, rec, read_id, {**qs, CLAUDE: claude_q}, p_move, 1, True)
                    write_card(root, state, today_et())
                    result.update(revision=1, claude=True, revised_side=revised["side"], revised_p_up=revised["p_up"])
        log_job_run(root, JOB, started_at, True, **result)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log_job_run(root, JOB, started_at, False, error=result["error"], read_id=read_id, trace=traceback.format_exc()[-1500:])
    return result


def replay_days(state_dir: Path | str, days: list[str]) -> dict:
    """The warm start: for every stamped live read of ``days`` with no call yet, in time order, grade and learn what
    had closed by the moment the live hook would have run, then call it; Claude's revision joins where its line landed
    in time. Everything closed by now is graded at the end. Never raises; logged as one run."""
    started_at = now_utc_iso()
    root = ensure_direction_line_folders(data_root(state_dir))
    result: dict = {"days": days, "called": 0, "revised": 0, "skipped": 0, "graded": 0}
    try:
        with held_lock(root):
            state = load_state(root)
            files = DayFiles(state_dir)
            for day in days:
                have = calls_by_read(calls_of_day(root, day))
                for row_ts, rec in sorted(files.records(day).items()):
                    read_id = read_id_of(row_ts)
                    if read_id in have:
                        result["skipped"] += 1
                        continue
                    qs, p_move, created = line_voices(forecaster_lines(root, day, read_id))
                    if not qs:
                        result["skipped"] += 1
                        continue
                    as_of = datetime.fromisoformat(created).astimezone(ET) if created else datetime.fromisoformat(row_ts).astimezone(ET) + REPLAY_HOOK_LAG
                    result["graded"] += len(grade_pending(root, files, state, as_of))
                    call = make_call(root, state, rec, read_id, qs, p_move, 0, False)
                    if call is None:
                        result["skipped"] += 1
                        continue
                    result["called"] += 1
                    claude = claude_line(state_dir, day, read_id)
                    claude_q = claude_which_way(claude) if claude_in_time(claude, row_ts) else None
                    if claude_q is not None:
                        make_call(root, state, rec, read_id, {**qs, CLAUDE: claude_q}, p_move, 1, True)
                        result["revised"] += 1
            result["graded"] += len(grade_pending(root, files, state, datetime.now(ET)))
            save_state(root, state)
            write_card(root, state, today_et())
            result["shares"] = {v: round(x, 4) for v, x in shares(state).items()}
        log_job_run(root, JOB, started_at, True, **{k: v for k, v in result.items() if k != "shares"}, replay=True)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log_job_run(root, JOB, started_at, False, error=result["error"], days=days, replay=True, trace=traceback.format_exc()[-1500:])
    return result


def days_from(first: str, last: str) -> list[str]:
    """Every calendar day from ``first`` to ``last``; a day without records is skipped by the replay."""
    a, b = datetime.fromisoformat(first).date(), datetime.fromisoformat(last).date()
    return [(a + timedelta(days=k)).isoformat() for k in range((b - a).days + 1)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the direction line: grade, learn and call one read, or replay days")
    ap.add_argument("--state-dir", default=None, help="the data root (default: station_paths.state_root())")
    ap.add_argument("--read-id", default=None, help="the live read to call (live:<row_ts>)")
    ap.add_argument("--catch-up", default=None, metavar="DAY", help="call that day's reads that have no call yet")
    ap.add_argument("--replay-from", default=None, metavar="DAY", help="call every read from that day to today that has no call yet")
    args = ap.parse_args(argv)
    state_dir = station_paths.state_root(args.state_dir)
    if args.read_id:
        result = run_for_read(state_dir, args.read_id)
    elif args.catch_up:
        result = replay_days(state_dir, [args.catch_up])
    elif args.replay_from:
        result = replay_days(state_dir, days_from(args.replay_from, today_et()))
    else:
        ap.error("pass --read-id, --catch-up DAY or --replay-from DAY")
    print(json.dumps(result, default=str))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
