"""The clock: how often price ended up, down or flat at this time of day, and the blend with JEV's sum.

Time of day was the biggest single lever SNDK JEV measured, so the sum the phone shows, and the one
the grader scores, is JEV's sum blended with the time of day's odds on SPX's own prior sessions.
JEV's own answer rides beside it, untouched, and is graded too, as are the clock's odds, so the
blend has to keep beating both of its parts.

The odds
    For each of up to 20 prior SPX sessions, a read is replayed every 10 minutes from 09:32 (the same
    minutes past the hour the service reads at, and every 10 minutes between) on the newest diary
    row at that minute, and each read is handed to the grader's own ``grade_one``: the same spot,
    morning anchor (grade.read_anchor), flat bands (lane.LIVE.horizons, from cuts.py), close, grace
    and bar-gap rules, so the odds and the graded sums can never be scored two ways. The outcomes are
    counted per phase of the day and shrunk toward the whole day's shares, so a thin phase cannot
    swing. A session counts only when at least MIN_SCORED_READS of its replayed reads were graded at
    30 minutes; a day the scanner barely covered is left out rather than counted as a full session.

    Point in time: prior sessions only, never today. A past session's counts are kept in
    ``state/spx_jev/clock_days.json`` with the number of bars and the size and time of the diary and
    market-context files they were counted from, and are counted again only if any of them changes,
    or if the rule itself (bands, phases, grid, version) changes. Fewer than MIN_SESSIONS counted sessions, or a read on an
    NYSE half day (the odds come from full sessions), and the blend is left out, and the card says why.

The settled-open odds (the premarket lane)
    Every premarket sum forecasts the same window, from the settled open to 10 and 30 minutes on
    (grade.horizon_start), so its clock has one phase: on each of up to 20 prior sessions, one read
    at the settled open is handed to the grader's own ``grade_one`` on the premarket lane, measured
    in that session's own morning anchor as its grading could know it (grade.read_anchor at the last
    mark). A session with no anchor, or no bar at the settled open or a mark, has no outcome; a half
    day's morning is a morning like any other. The shares are shrunk toward even thirds by
    PRIOR_SESSIONS, so no outcome is ever given nothing. Fewer than MIN_SESSIONS sessions with an
    outcome and they are left out. The premarket lane does not blend them (lane.PREMARKET.clock_blend):
    replayed on the 47 saved sessions of July to September 2026 with 10 counted before them, they
    forecast the window worse than even thirds out of sample (mean log loss 1.22 against 1.10 at 10
    minutes, 1.14 against 1.10 at 30), the outcomes falling about a third each way.

The blend
    p = JEV_SHARE * JEV's probability + (1 - JEV_SHARE) * the clock's, per outcome; JEV's
    "unsure" keeps its JEV_SHARE and the clock gives it nothing. JEV_SHARE is 0.5, declared, not
    fitted: it scored best on SNDK's twelve blended reads, far too few to fit a share on. The top
    of the blended summary is the lane's primary sum, as the summary names it.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .grade import grade_one, mark_at, read_anchor, settled_open_at
from .lane import LIVE, PREMARKET
from .sessions import SESSION_CLOSE, session_close
from .state_builder import CONTEXT_SUBDIR, ROWS_SUBDIR, MarketContext, load_market_context, load_rows, parse_ts

JEV_SHARE = 0.5          # declared, not fitted
MIN_SESSIONS = 10        # fewer counted prior sessions than this and the odds are too thin to blend
MAX_SESSIONS = 20
MIN_SCORED_READS = 18    # about half of a full session's 37 replayed reads that can be graded at 30 minutes
PRIMARY = LIVE.primary
SHRINK = 10.0            # a phase's counts are pulled toward the whole day's shares with this weight
STEP_MIN = 10            # replayed reads every 10 minutes
FIRST_READ = time(9, 32)
ROW_MAX_AGE_MIN = 10     # a replayed read needs a diary row this fresh, as a live read does
OUTCOMES = ("up", "down", "flat")
CACHE_NAME = "clock_days.json"
RULE_VERSION = 2         # bump when the counting changes, so every stored day is counted again
PRIOR_SESSIONS = 1.0     # the settled-open odds' pull toward even thirds, in sessions

# Phases of the day, by the read's clock (minute of day, from and before).
PHASES = (
    ("opening", 9 * 60 + 30, 10 * 60, "the opening half hour, before 10:00"),
    ("morning", 10 * 60, 11 * 60, "the morning, 10:00 to 11:00"),
    ("late_morning", 11 * 60, 12 * 60, "late morning, 11:00 to 12:00"),
    ("lunch", 12 * 60, 14 * 60, "lunch, 12:00 to 14:00"),
    ("afternoon", 14 * 60, 16 * 60, "the afternoon, 14:00 to the close"),
)


def phase_of(t: datetime) -> str:
    m = t.hour * 60 + t.minute
    for name, lo, hi, _ in PHASES:
        if lo <= m < hi:
            return name
    return PHASES[-1][0] if m >= PHASES[-1][1] else PHASES[0][0]


def phase_words(name: str) -> str:
    return next((w for n, _, _, w in PHASES if n == name), name)


def _rule_key(hz: dict[str, tuple[int, float]]) -> str:
    """The counts depend on the bands, the phases, the read grid and the counting code: a change to
    any of them invalidates the stored days rather than mixing two rules."""
    return json.dumps({"v": RULE_VERSION, "h": {q: list(v) for q, v in sorted(hz.items())}, "p": [p[:3] for p in PHASES],
                       "step": STEP_MIN, "first": FIRST_READ.isoformat(), "row_age": ROW_MAX_AGE_MIN}, sort_keys=True)


def replayed_reads(bars: list[dict], rows: list[dict], hz: dict[str, tuple[int, float]],
                   market: MarketContext | None = None) -> list[dict]:
    """Every replayed read of one finished session, oldest first: ``{"row_ts", "phase", "spot", "sigma",
    "bands": {qid: outcome}}``, each graded by the grader's own ``grade_one`` on the newest diary row
    at that minute, in the morning anchor that read could know (``sigma``); a horizon the grader could
    not grade has no band, and a read with no anchor is no read."""
    if not bars or not rows:
        return []
    rows = [r for r in rows if isinstance(r.get("spot"), (int, float))]
    if not rows:
        return []
    stamps = [parse_ts(r["ts"]) for r in rows]
    t = parse_ts(bars[0]["ts"]).replace(hour=FIRST_READ.hour, minute=FIRST_READ.minute, second=0, microsecond=0)
    close = session_close(t)
    k, seen, out = -1, set(), []
    while t < close:
        while k + 1 < len(stamps) and stamps[k + 1] <= t:
            k += 1
        if k >= 0 and t - stamps[k] <= timedelta(minutes=ROW_MAX_AGE_MIN) and rows[k]["ts"] not in seen:
            seen.add(rows[k]["ts"])                       # one row read twice is one read, as the grader has it
            anchor = read_anchor(rows, bars, market, rows[k]["ts"])
            if anchor is not None:
                rec = {"row_ts": rows[k]["ts"], "spot": rows[k]["spot"], "by": {q: {"pick": "flat", "probabilities": {}} for q in hz}}
                g = grade_one(rec, bars, anchor=anchor) or {}
                bands = {q: (g.get(q) or {}).get("band") for q in hz}
                out.append({"row_ts": rows[k]["ts"], "phase": phase_of(stamps[k]), "spot": float(rows[k]["spot"]),
                            "sigma": anchor.points, "bands": {q: b for q, b in bands.items() if b in OUTCOMES}})
        t += timedelta(minutes=STEP_MIN)
    return out


def day_counts(bars: list[dict], rows: list[dict], hz: dict[str, tuple[int, float]], market: MarketContext | None = None) -> dict:
    """``{qid: {phase: {up, down, flat}}}`` for one finished session: every replayed read's outcome."""
    out = {qid: {p[0]: {o: 0 for o in OUTCOMES} for p in PHASES} for qid in hz}
    for r in replayed_reads(bars, rows, hz, market):
        for q, band in r["bands"].items():
            out[q][r["phase"]][band] += 1
    return out


def _scored(counts: dict, qid: str = PRIMARY) -> int:
    return sum(sum(c.values()) for c in counts.get(qid, {}).values())


def _file_print(path: Path) -> list[int] | None:
    """A file's size and modification time, or None when it is not there."""
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _load_cache(path: Path, key: str) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d.get("days", {}) if d.get("rule") == key else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _save_cache(path: Path, key: str, days: dict) -> None:
    """Atomic and collision-free: each run writes its own temporary file, and a failed write only
    means the next run counts again."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".clock_days.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"rule": key, "days": days}, f, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, UnboundLocalError):
            pass


def odds(state_dir: Path, out_dir: Path, prior_bars: dict[str, list[dict]], now: datetime,
         hz: dict[str, tuple[int, float]] | None = None) -> dict:
    """The clock's odds for a read at ``now``: ``{"phase", "phase_words", "sessions", "by": {qid:
    {"probabilities", "n"}}}``, or ``{"left_out": reason}``. ``prior_bars`` is the scene's, which
    holds only sessions before today."""
    hz = hz or LIVE.horizons
    if session_close(now).time() != SESSION_CLOSE:
        return {"left_out": "a 13:00 half day; the time-of-day odds are counted on full sessions"}
    days = sorted((d for d in prior_bars if d < now.date().isoformat()), reverse=True)[:MAX_SESSIONS]
    key = _rule_key(hz)
    cache_path = out_dir / CACHE_NAME
    cache = _load_cache(cache_path, key)
    changed = False
    for d in days:
        # a stored day is trusted only while its bars, its diary file and its market-context files (the
        # VIX an estimated anchor falls back on) are the ones it was counted from
        context = Path(state_dir) / CONTEXT_SUBDIR
        seen = {"n_bars": len(prior_bars[d]), "rows_file": _file_print(Path(state_dir) / ROWS_SUBDIR / f"{d}.jsonl"),
                "context_files": [_file_print(context / f"{d}.jsonl"), _file_print(context / "bars" / f"{d}.jsonl")]}
        entry = cache.get(d)
        if entry is not None and all(entry.get(k) == v for k, v in seen.items()) and "counts" in entry:
            continue
        cache[d] = {**seen, "counts": day_counts(prior_bars[d], load_rows(state_dir, d), hz, load_market_context(state_dir, d))}
        changed = True
    if changed:
        _save_cache(cache_path, key, cache)
    counted = [d for d in days if _scored(cache[d]["counts"]) >= MIN_SCORED_READS]
    if len(counted) < MIN_SESSIONS:
        return {"left_out": f"only {len(counted)} prior sessions with enough scored reads; the time-of-day odds need {MIN_SESSIONS}"}
    ph = phase_of(now)
    by = {}
    for qid in hz:
        whole = {o: 0 for o in OUTCOMES}
        here = {o: 0 for o in OUTCOMES}
        for d in counted:
            for p, c in cache[d]["counts"].get(qid, {}).items():
                for o in OUTCOMES:
                    whole[o] += c.get(o, 0)
                    if p == ph:
                        here[o] += c.get(o, 0)
        n_whole, n_here = sum(whole.values()), sum(here.values())
        if n_whole == 0:
            continue
        base = {o: whole[o] / n_whole for o in OUTCOMES}
        probs = {o: round((here[o] + SHRINK * base[o]) / (n_here + SHRINK), 4) for o in OUTCOMES}
        by[qid] = {"probabilities": probs, "n": n_here}
    if not by:
        return {"left_out": "no prior session produced a scored read"}
    return {"phase": ph, "phase_words": phase_words(ph), "sessions": len(counted), "by": by}


def premarket_odds(state_dir: Path, prior_bars: dict[str, list[dict]], now: datetime) -> dict:
    """The settled-open odds for a premarket read at ``now``: ``{"phase", "phase_words", "sessions", "by":
    {qid: {"probabilities", "n"}}}`` in the shape odds() returns, so blend() takes either, or ``{"left_out":
    reason}``. ``prior_bars`` is the scene's, which holds only sessions before today."""
    days = sorted((d for d in prior_bars if d < now.date().isoformat()), reverse=True)[:MAX_SESSIONS]
    hz = PREMARKET.horizons
    last = max(hz, key=lambda q: hz[q][0])
    counts = {qid: {o: 0 for o in OUTCOMES} for qid in hz}
    for d in days:
        at = settled_open_at(date.fromisoformat(d)).isoformat()
        mark = mark_at(at, hz[last][0], PREMARKET)
        anchor = read_anchor(load_rows(state_dir, d), prior_bars[d], load_market_context(state_dir, d), mark.isoformat())
        if anchor is None:
            continue
        rec = {"row_ts": at, "by": {q: {"pick": "flat", "probabilities": {}} for q in hz}}
        g = grade_one(rec, prior_bars[d], final=True, lane=PREMARKET, anchor=anchor) or {}
        for q in hz:
            band = (g.get(q) or {}).get("band")
            if band in OUTCOMES:
                counts[q][band] += 1
    sessions = sum(counts[PREMARKET.primary].values())
    if sessions < MIN_SESSIONS:
        return {"left_out": f"only {sessions} prior sessions with an outcome from the settled open; the odds need {MIN_SESSIONS}"}
    by = {}
    for qid, c in counts.items():
        n = sum(c.values())
        by[qid] = {"probabilities": {o: round((c[o] + PRIOR_SESSIONS / len(OUTCOMES)) / (n + PRIOR_SESSIONS), 4) for o in OUTCOMES}, "n": n}
    return {"phase": "settled_open", "phase_words": "the half hour after the settled open", "sessions": sessions, "by": by}


def _pick(p: dict) -> str | None:
    return max(p, key=p.get) if p else None


def blend(hour: dict | None, clock: dict) -> dict | None:
    """The hour summary with every sum blended with the clock's odds for its horizon. JEV's own
    answer is kept under ``jev`` and the clock's under ``clock``, and each sum says whether it was
    blended; the top of the summary is the primary sum, as the phone and the grader read it.
    ``blend.used`` is true only when the primary sum was blended. Without odds, JEV's sums stand
    alone and ``blend`` says why. The primary is the one the summary names, the live lane's without one."""
    if not isinstance(hour, dict) or not isinstance(hour.get("by"), dict):
        return hour
    if clock.get("left_out"):
        return {**hour, "blend": {"used": False, "why": clock["left_out"]}}
    by = {}
    for qid, a in hour["by"].items():
        c = (clock.get("by") or {}).get(qid)
        jp = a.get("probabilities") if isinstance(a, dict) else None
        if not c or not isinstance(jp, dict) or not jp:
            by[qid] = a
            continue
        keys = list(OUTCOMES) + [k for k in jp if k not in OUTCOMES]
        p = {k: round(JEV_SHARE * float(jp.get(k, 0.0)) + (1 - JEV_SHARE) * float(c["probabilities"].get(k, 0.0)), 4)
             for k in keys}
        by[qid] = {"pick": _pick(p), "probabilities": p, "confidence": None, "blended": True,
                   "jev": {"pick": a.get("pick"), "probabilities": jp, "confidence": a.get("confidence")},
                   "clock": {"pick": _pick(c["probabilities"]), "probabilities": c["probabilities"], "n": c["n"]}}
    primary = hour.get("primary") or PRIMARY
    prim = by.get(primary)
    used = isinstance(prim, dict) and prim.get("blended") is True
    out = {**hour, "by": by}
    if used:
        out["blend"] = {"used": True, "jev_share": JEV_SHARE, "phase": clock["phase"], "phase_words": clock["phase_words"],
                        "sessions": clock["sessions"]}
        out.update({k: prim.get(k) for k in ("pick", "probabilities", "confidence", "jev", "clock")})
    else:
        out["blend"] = {"used": False, "why": f"JEV gave no probabilities for {primary}, so its sum stands alone"}
    return out
