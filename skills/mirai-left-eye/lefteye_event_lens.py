"""lefteye_event_lens.py — THE EVENT LENS: did the macro print move the tape MORE
than the options market had budgeted, and did the budget itself collapse after?

Watches the three scheduled macro prints that repeatedly bend an SPX session —
FOMC (14:00 statement), CPI (08:30), NFP (08:30) — and on those days ONLY emits
one small record-only telemetry dict per scan:

  * BEFORE an intraday print: freeze the pre-event expected-move budget
    (em_pre_pts) from the diary, so the post-print compare has an untainted
    denominator.
  * AFTER the print: realized_post_pts (what the tape actually did in the first
    K minutes, measured on 1-min bars) / em_pre_pts = surprise_ratio. A ratio
    above 1 means the print ate more than the whole remaining budget — the
    continuation alarm.
  * crush_flag: did the expected-move budget deflate by more than the natural
    clock decay right after the print (vol crush) — the "event premium came
    out, fade regime restored" tell.

Behavior notes (deliberate, v1):
  * KINDS is an ALLOWLIST. The shared calendar file may also carry OPEX /
    VIXEXP / QTR_ROLL and holiday-shift rows — those are structural-flow days
    owned elsewhere (lob-flow baselines / fill ledger), not macro prints, and
    are excluded here (belt and suspenders: whole-day rows carry a null
    time_et, so the truthy-time filter drops them too).
  * Calendar coupling is SOFT and READ-ONLY: the single WRITER of
    state/lob_flow/calendar.json stays lob-flow's FileCalendar (it seeds the
    file on first run). This module only reads it — at most ONE file read per
    ET day (module-level cache) — and a missing/corrupt file simply means
    "no event day". Never writes, never repairs, never networks.
  * NFP is NOT in the JSON: FileCalendar computes it by rule (first Friday of
    the month, 08:30 ET) and we replicate the same 3-line rule, deduped on
    kind against any seeded holiday-shift NFP row.
  * Non-event day -> None: the caller leaves the diary key ABSENT, matching
    the gex_motion / lob_flow absent-key pattern — absence means "lens off",
    never "event with empty fields", so downstream readers need no schema for
    the quiet 200+ days a year.
  * Fail-open: read() never raises. A structural gap degrades one field to
    None; any genuine surprise degrades the whole read to None. It can never
    stall or bend a scan.
"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent

# Macro-print allowlist + impact rank (multi-event day: max rank wins the read).
# REQUIRED as an allowlist: the calendar also carries OPEX/VIXEXP/QTR_ROLL and
# holiday-shift rows — structural flow days owned elsewhere, never scored here.
KINDS = {"FOMC": 3, "CPI": 2, "NFP": 1}

# K = 15 min for an intraday print (FOMC 14:00): captures the statement
# repricing and STOPS BEFORE the 14:30 presser — a separate, differently
# shaped animal that would smear the statement's clean first move.
K_INTRADAY_MIN = 15

# K = 30 min for a pre-market print (CPI/NFP 08:30): the overnight gap alone
# misses the 9:30-10:00 opening price-discovery leg, so the realized window
# runs from the open through 10:00 ET.
K_PREMARKET_MIN = 30

# em_post/em_pre below this = vol crush. 0.85 comfortably clears the natural
# sqrt(time-remaining) decay of an expected-move budget over ~15 minutes (a
# few percent at most) — only a genuine event-premium collapse trips it.
CRUSH_RATIO = 0.85

# Single WRITER of this file stays lob-flow's FileCalendar — soft, read-only
# data coupling; this module must never create or repair it.
CALENDAR_PATH = SKILL_DIR.parent.parent / "state" / "lob_flow" / "calendar.json"

# Deterministic quality-flag order (telemetry diffs stay stable across scans).
_FLAG_ORDER = ("sigma_fallback", "em_post_print", "no_pre_event_em", "multi_event")

_CAL_CACHE: dict = {}   # (et-date-string, path) -> events; ONE file read per day


def reset_calendar_cache() -> None:
    """Test hook (mirrors native_gex_feed.reset_chain_cache)."""
    _CAL_CACHE.clear()


def load_calendar(path=None) -> list:
    """READ-ONLY view of the shared event calendar (state/lob_flow/calendar.json).

    The single WRITER of that file stays lob-flow's FileCalendar — this is a
    soft read-only data coupling: we never create, seed, or repair the file.
    Missing / corrupt / odd-shaped file -> [] (a quiet no-event day, fail-open).
    Cached keyed on the ET date-string so the lens costs at most ONE file read
    per day no matter how many scans call it.
    """
    p = Path(path) if path is not None else CALENDAR_PATH
    key = (datetime.now(ET).date().isoformat(), str(p))
    if key in _CAL_CACHE:
        return _CAL_CACHE[key]
    events: list = []
    try:
        obj = json.loads(p.read_text())
        ev = obj.get("events") if isinstance(obj, dict) else None
        if isinstance(ev, list):
            events = [e for e in ev if isinstance(e, dict)]
    except Exception:
        events = []          # missing/corrupt -> no event day, never a write
    _CAL_CACHE[key] = events
    return events


def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def events_for(day: date, events: list) -> list:
    """The day's qualifying macro prints, winner FIRST.

    Keeps entries with date == day, kind in the KINDS allowlist and a truthy
    time_et (OPEX / QTR_ROLL whole-day rows carry time_et null -> dropped).

    NFP is NOT in the JSON: FileCalendar computes it by rule (first Friday of
    the month, 08:30 ET) — we replicate the same 3-line rule here, deduped on
    kind so a seeded (e.g. holiday-shift) NFP row is never doubled.

    Order: max impact rank first, ties broken by earliest time_et. The caller
    takes [0] as the winner and flags multi_event when len > 1.
    """
    iso = day.isoformat()
    out = [e for e in (events or [])
           if isinstance(e, dict) and e.get("date") == iso
           and e.get("kind") in KINDS and e.get("time_et")]
    if day == _first_friday(day.year, day.month) and \
            not any(e.get("kind") == "NFP" for e in out):
        out.append({"date": iso, "kind": "NFP", "time_et": "08:30"})
    out.sort(key=lambda e: (-KINDS[e["kind"]], str(e.get("time_et"))))
    return out


# --------------------------------------------------------------------- helpers
def _ts(v):
    """ISO string (or datetime) -> aware ET datetime; naive assumed ET; None on garbage."""
    try:
        dt = datetime.fromisoformat(v) if isinstance(v, str) else v
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt.astimezone(ET)
    except Exception:
        return None


def _num(v):
    try:
        f = float(v)
        return f if f == f else None      # NaN guard
    except Exception:
        return None


def _spx_rows(todays_rows):
    """(ts, row) pairs sorted by ts. Rows from other price spaces are skipped
    (QQQ mood rows share the diary; per the gex_motion precedent we never mix
    sigma scales across tickers)."""
    out = []
    for r in todays_rows or []:
        if not isinstance(r, dict):
            continue
        if r.get("ticker") not in (None, "SPX"):
            continue
        t = _ts(r.get("ts"))
        if t is not None:
            out.append((t, r))
    out.sort(key=lambda p: p[0])
    return out


def _row_budget(row, field):
    """(value, source) from one diary row: range_ruler[field] first, else the
    flat row sigma. source is 'em' or 'sigma' — crush compares SAME-source only."""
    rr = row.get("range_ruler")
    if isinstance(rr, dict):
        v = _num(rr.get(field))
        if v is not None:
            return v, "em"
    v = _num(row.get("sigma"))
    if v is not None:
        return v, "sigma"
    return None, None


def _sorted_bars(bars):
    out = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        t = _ts(b.get("ts"))
        if t is not None:
            out.append((t, b))
    out.sort(key=lambda p: p[0])
    return out


# ------------------------------------------------------------------- the read
def read(now, spot, prior_close, bars, range_ruler_now=None,
         todays_rows=None, events=None):
    """One EVENT-LENS telemetry read for this scan.

    Returns the 8-field dict on FOMC/CPI/NFP days, None otherwise — the caller
    leaves the diary key ABSENT on quiet days (gex_motion/lob_flow pattern).
    Fail-open: never raises; any internal surprise -> None. `events` may be
    injected (tests / caller reuse); None -> load_calendar() + events_for.
    `spot` is accepted for signature parity with the other lens reads; v1
    computes from bars/prior_close only.
    """
    try:
        now_et = _ts(now)
        if now_et is None:
            return None
        day = now_et.date()
        todays = events_for(day, events if events is not None else load_calendar())
        if not todays:
            return None      # non-event day: diary key stays ABSENT
        winner = todays[0]
        flags = []
        if len(todays) > 1:
            flags.append("multi_event")

        hh, mm = int(winner["time_et"][:2]), int(winner["time_et"][3:5])
        t0 = datetime.combine(day, time(hh, mm), tzinfo=ET)
        rows = _spx_rows(todays_rows)
        bts = _sorted_bars(bars)

        em_pre = realized = ratio = None
        crush = None
        src = None

        if (hh, mm) < (9, 30):
            # ---------------- PRE-MARKET print (CPI/NFP 08:30) ----------------
            # The print lands before the bell: the entire session is aftermath.
            phase = "post_event"

            # em_pre: em_open from the EARLIEST diary row carrying range_ruler,
            # else the earliest row's sigma; no rows yet -> the current scan's
            # range_ruler_now / sigma equivalent. ALWAYS flagged em_post_print:
            # the denominator was priced AFTER the print — a consistent,
            # documented bias rather than a silent one.
            try:
                carrier = next((r for _, r in rows
                                if isinstance(r.get("range_ruler"), dict)
                                and _num(r["range_ruler"].get("em_open")) is not None),
                               None)
                if carrier is not None:
                    em_pre, src = _num(carrier["range_ruler"]["em_open"]), "em"
                elif rows:
                    v = _num(rows[0][1].get("sigma"))
                    if v is not None:
                        em_pre, src = v, "sigma"
                        flags.append("sigma_fallback")
                elif isinstance(range_ruler_now, dict):
                    v = _num(range_ruler_now.get("em_open"))
                    if v is not None:
                        em_pre, src = v, "em"
                    else:
                        v = _num(range_ruler_now.get("sigma"))
                        if v is not None:
                            em_pre, src = v, "sigma"
                            flags.append("sigma_fallback")
            except Exception:
                em_pre = None
            flags.append("em_post_print")

            # realized: |open - prior_close| + (max high - min low) over bars
            # before 10:00 — the overnight GAP plus the 9:30-10:00 opening
            # price-DISCOVERY leg (K=30: the gap alone misses that leg).
            # Only once now >= 10:00 ET and >= 10 bars exist before 10:00, so a
            # thin/half-formed open can't print a fake small "realized".
            try:
                ten = datetime.combine(day, time(10, 0), tzinfo=ET)
                pc = _num(prior_close)
                if now_et >= ten and pc is not None:
                    early = [b for t, b in bts if t < ten]
                    if len(early) >= 10:
                        o = _num(early[0].get("open"))
                        highs = [h for b in early if (h := _num(b.get("high"))) is not None]
                        lows = [l for b in early if (l := _num(b.get("low"))) is not None]
                        if o is not None and highs and lows:
                            # 2 dp like the range-ruler point fields — raw float
                            # arithmetic writes 55.72000000000025-style noise
                            realized = round(abs(o - pc) + (max(highs) - min(lows)), 2)
            except Exception:
                realized = None

            # crush_flag stays None in v1: an 08:30 print has no same-session
            # pre-print budget to compare against, and this module does NO
            # cross-day diary reads by design (its one file read is the
            # calendar, nothing else).
            crush = None
        else:
            # ---------------- INTRADAY print (FOMC 14:00) ---------------------
            phase = "pre_event" if now_et < t0 else "post_event"
            k = timedelta(minutes=K_INTRADAY_MIN)

            # em_pre: the LAST diary row strictly before t0 — em_points from its
            # range_ruler, else that row's flat sigma (sigma_fallback flag).
            try:
                pre_rows = [r for t, r in rows if t < t0]
                if pre_rows:
                    em_pre, src = _row_budget(pre_rows[-1], "em_points")
                    if src == "sigma":
                        flags.append("sigma_fallback")
                if em_pre is None:
                    flags.append("no_pre_event_em")
            except Exception:
                em_pre = None

            # realized: bars-based so a scan gap across the print can't blank
            # the read — |close(last bar <= t0+15m) - close(last bar < t0)|,
            # only once now >= t0+15m AND the bars actually reach t0+14m.
            try:
                if now_et >= t0 + k and bts and \
                        bts[-1][0] >= t0 + k - timedelta(minutes=1):
                    pre_bar = next((b for t, b in reversed(bts) if t < t0), None)
                    post_bar = next((b for t, b in reversed(bts) if t <= t0 + k), None)
                    if pre_bar is not None and post_bar is not None:
                        c0, c1 = _num(pre_bar.get("close")), _num(post_bar.get("close"))
                        if c0 is not None and c1 is not None:
                            realized = round(abs(c1 - c0), 2)   # 2 dp, diary hygiene
            except Exception:
                realized = None

            # crush: em_post = the SAME-source budget from the FIRST diary row
            # at/after t0+15m (em_points if em_pre came from em_points, sigma if
            # from sigma), else the current scan's range_ruler_now equivalently
            # sourced. Mixed sources are incomparable -> None, never a guess.
            try:
                if em_pre is not None and em_pre > 0 and src is not None \
                        and now_et >= t0 + k:
                    em_post, mixed = None, False
                    post_row = next((r for t, r in rows if t >= t0 + k), None)
                    if post_row is not None:
                        want, other = (("em_points", "sigma") if src == "em"
                                       else ("sigma", "em_points"))
                        rr = post_row.get("range_ruler")
                        rr = rr if isinstance(rr, dict) else {}
                        w = _num(rr.get(want)) if want == "em_points" else _num(post_row.get(want))
                        o = _num(post_row.get(other)) if other == "sigma" else _num(rr.get(other))
                        if w is not None:
                            em_post = w
                        elif o is not None:
                            mixed = True
                    if em_post is None and not mixed and isinstance(range_ruler_now, dict):
                        want, other = (("em_points", "sigma") if src == "em"
                                       else ("sigma", "em_points"))
                        w, o = _num(range_ruler_now.get(want)), _num(range_ruler_now.get(other))
                        if w is not None:
                            em_post = w
                        elif o is not None:
                            mixed = True
                    if em_post is not None and not mixed:
                        # 0.85 clears the natural sqrt(t) budget decay over
                        # 15 min — only true event-premium collapse trips it.
                        crush = (em_post / em_pre) < CRUSH_RATIO
            except Exception:
                crush = None

        # surprise_ratio is RAW — no time-scaling on purpose: a 15-minute move
        # that eats the WHOLE remaining session budget IS the continuation
        # alarm; rescaling it to a per-15-min budget would mute exactly the
        # days this lens exists for.
        if realized is not None and em_pre is not None and em_pre > 0:
            ratio = round(realized / em_pre, 3)

        return {
            "event_kind": winner["kind"],
            "event_time_et": winner["time_et"],
            "phase": phase,
            "em_pre_pts": em_pre,
            "realized_post_pts": realized,
            "surprise_ratio": ratio,
            "crush_flag": crush,
            "quality": ",".join(f for f in _FLAG_ORDER if f in flags) or "ok",
        }
    except Exception:
        return None          # fail-open: an event lens must never bend a scan
