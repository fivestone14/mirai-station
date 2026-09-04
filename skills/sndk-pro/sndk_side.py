"""SNDK side payload — the bar-anchored second packet (2026-09-03).

A second, smaller document built beside the scene on the same scan. The scene
is options-first: it is assembled from the diary row, and its clock is the
2-minute scan. This one is bars-first — it is assembled from the minute-bar
sidecar (`sndk_bars`, state/sndk_bars/<day>.jsonl) and its clock is the bar.
The two are meant to disagree about what matters, not about the facts.

WHY IT EXISTS. A day's trade map from a working SNDK trader was taken apart in
detail (2026-09-02/03). What he reasons in is a small set of price levels, how
fast price arrived at one, and which part of the session it is — and, measured
against his own written reasons for ten trades, six of the ten name a level
outright. What he never names once is a momentum reading. The scene serves the
options map; nothing in the stack served the bars, which is what this is.

THE ONE RULE THAT SHAPES IT. Every time in this document is DERIVED from a bar
index, never written down separately. The failure that forced the rule was
measured: in a hand-built draft of this packet, 41 of 44 magnitudes replayed
exactly from the bars while only 6 of 28 times and durations did — because
`max()` returns the value and discards the row, so the time field had no source
and got filled in from memory. Hence `_reduce`: every maximum, minimum, first
and last returns {"value", "at_bar"}, and a bare number from a reduce is a bug.

WHAT WAS TAKEN OUT AGAIN, AND WHY. A first version of this packet ran 25,075
bytes; ten reviewers cut it to 9,388 without losing a verdict on any of the
trader's ten trades. What went was arithmetic on fields already present (a last
price that is the last bar's close, distances that are one subtraction), blocks
that restated each other (an "activity" level that was the volume percentile
again), and one event detector that lost to a plain volume filter on its own
measurements (5 of 9 against 7 of 10) and was withdrawn. What stayed is what
lets a reader disbelieve the document: what is missing and why, how old each
feed is, and the bar index stapled to every derived number.

TWO COUNTS THAT ARE NOT COMPARABLE. `visits` and `crosses` on a level are
measured on different rails — a visit is a RUN of consecutive bars within half
a bar-range-median of the line, counted once, and a cross is a close a whole
bar-range-median onto the other side — so both rails ship with the counts in
`level_rails`. Counting bars rather than runs inflated an early draft's touch
count from 14 to 61.

HONESTY. Only completed bars are read (`sndk_bars.completed`), so nothing here
is computed from a minute still being written. Levels from the options engine
carry the age of the row they came from; levels drawn from the tape carry the
bar that set them and count no interaction before it — a pivot made at 10:00
was not "visited" at 09:40. With fewer than `MIN_BARS` bars the packet reports
what it has and says so rather than inventing a baseline from one minute.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))
try:
    import sndk_bars                       # the sidecar's reader half
except Exception:                          # the packet must degrade, never crash
    sndk_bars = None

_ET = ZoneInfo("America/New_York")
VERSION = "side-1"                # bump on ANY change to what this packet says
SESSION_OPEN = time(9, 30)
BARS_PER_SESSION = 390            # 09:30-16:00 at one a minute
MIN_BARS = 15                     # under this, no baseline is honest
RSI_LEN = 14
RSI_BANDS = {"oversold": (30.0, 33.0), "overbought": (70.0, 67.0)}  # enter, exit
MERGE_GAP_BARS = 5                # closed episodes only; an open one never merges
KEEP_EPISODE_WITHIN = 60          # bars; older spells are archaeology
CLIMAX_X = (4.0, 3.0)             # enter, exit — against the trailing-30 median
VOL_WINDOW = 30
SEGMENTS = (("open_drive", 0, 59), ("lull", 60, 269),
          ("afternoon", 270, 329), ("power_hour", 330, 389))


# sr-8's rule, applied here: A SENTENCE THAT NEVER CHANGES DOES NOT RIDE THE
# PACKET. These are the standing explanations — identical on every build, 16%
# of the bytes when they shipped inline — held once and surfaced by whatever
# renders the packet. The packet carries `doctrine` as a version pointer, so a
# stored packet can always be paired with the words that were true when it was
# built.
DOCTRINE = {
    "as_of": "Every timestamp in this packet is derived from a bar_index against "
             "session_start; none is stored on its own. A reduce that dropped the "
             "row it came from is what let times be typed in by hand.",
    "bl.bar_range_median": "Median bar range, over bars that actually traded — the "
                           "unit every distance here is quoted in. NOT "
                           "gw_vocab.NOISE_FLOOR, which is a gamma-cluster share cut.",
    "bars.records": "Every bar cited by an at_bar anywhere in this packet, plus the "
                    "last two. A value checked against a bar nobody carries is not "
                    "checked.",
    "ind.vol": "The percentile leads; the raw multiple tracks the clock and inverts "
               "inside the closing ramp.",
    "ind.rsi14.warmup": "Undefined until RSI_LEN + 1 bars have closed. No value is "
                        "invented for the opening minutes.",
    "level_rails": "Visits and crosses are measured on DIFFERENT rails and are not "
                   "comparable. A VISIT is a run of consecutive bars in which price "
                   "came within half a bar-range-median of the line, counted ONCE; a "
                   "CROSS is a close a whole bar-range-median onto the other side.",
    "levels.vintage": "A level from the options book carries the bar its row was "
                      "measured on and can be older than the price it is compared "
                      "against; a level drawn from the tape cannot. Neither counts an "
                      "interaction from before it existed.",
    "episodes.bars_in_state": "The bars actually in the band, never the span. A merged "
                              "spell covers a gap, and counting the gap credits the "
                              "state to minutes that were never in it. An open spell "
                              "is never merged.",
    "session_segments": "Expected shares are measured from this symbol's own recent "
                        "sessions and normalised, because a median taken per segment "
                        "does not sum to a session on its own.",
    "absent.positions": "The operator's own book. Nothing in this packet models it, so "
                        "no reading here can say whether a level is a reason to act.",
    "absent.premium": "Implied volatility and premium per strike.",
    "absent.events": "No calendar feed is wired: index rebalances, macro releases and "
                     "earnings are knowable in advance and are not here.",
    "absent.confluence": "No combination rule qualifies yet. On the sessions on disk a "
                         "strict three-condition rule fires too rarely to score.",
    "absent.baselines": "Under MIN_BARS bars no baseline is honest.",
    "integrity": "These checks run; they are not asserted. Every one of them is forced "
                 "to fail once in the tests, because a check never observed failing is "
                 "not evidence.",
}


# ---------------------------------------------------------------- the clock --
def bar_index(ts, day: Optional[str] = None) -> Optional[int]:
    """Minutes since the 09:30 open. THE identity of a bar: every time in this
    packet is derived from one of these, and nothing carries a time of its own."""
    t = ts if isinstance(ts, datetime) else _parse(ts)
    if t is None:
        return None
    t = t.astimezone(_ET)
    open_at = datetime.combine(t.date(), SESSION_OPEN, tzinfo=_ET)
    i = int((t - open_at).total_seconds() // 60)
    return i if 0 <= i < BARS_PER_SESSION else None


def bar_time(i: int, day: str) -> str:
    d = datetime.fromisoformat(day).date()
    return (datetime.combine(d, SESSION_OPEN, tzinfo=_ET) + timedelta(minutes=i)).isoformat()


def _parse(s) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_ET)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _r2(v) -> Optional[float]:
    return None if v is None else round(float(v), 2)


def _r1(v) -> Optional[float]:
    return None if v is None else round(float(v), 1)


def indexed(bars: list[dict], day: str) -> list[dict]:
    """The sidecar's bars keyed by bar index, in order, one per minute. Bars
    outside the regular session and bars that will not parse are dropped."""
    out: dict[int, dict] = {}
    for b in bars or []:
        i = bar_index(b.get("ts"), day)
        if i is None:
            continue
        vals = {k: _num(b.get(k)) for k in ("open", "high", "low", "close")}
        if any(v is None for v in vals.values()):
            continue
        out[i] = {"bar_index": i, **vals, "volume": _num(b.get("volume")) or 0.0,
                  "closed": True}
    return [out[i] for i in sorted(out)]


# ------------------------------------------------------------- the reduces --
def _reduce(bars: list[dict], key: Callable[[dict], float], pick) -> Optional[dict]:
    """The whole point of the module in four lines: a reduce keeps the row it
    came from. Returns {"value", "at_bar"}; never a bare number."""
    live = [b for b in bars if key(b) is not None]
    if not live:
        return None
    b = pick(live, key=key)
    return {"value": key(b), "at_bar": b["bar_index"]}


def bar_range_median(bars: list[dict]) -> Optional[float]:
    """Median bar range — the unit every distance in this packet is quoted in.
    A move smaller than this is not a move."""
    rs = [b["high"] - b["low"] for b in bars if (b.get("volume") or 0) > 0]
    return st.median(rs) if rs else None


def _p90_range(bars: list[dict]) -> Optional[float]:
    rs = sorted(b["high"] - b["low"] for b in bars if (b.get("volume") or 0) > 0)
    return rs[int(0.9 * len(rs))] if rs else None


def rsi_wilders(bars: list[dict], length: int = RSI_LEN) -> dict[int, float]:
    """Wilder's RSI on closes, seeded from the session open — so the first
    `length` bars have no value at all rather than a made-up one."""
    cl = [b["close"] for b in bars]
    idx = [b["bar_index"] for b in bars]
    out: dict[int, float] = {}
    if len(cl) <= length:
        return out
    gain = loss = 0.0
    for i in range(1, length + 1):
        d = cl[i] - cl[i - 1]
        gain += max(d, 0.0)
        loss += max(-d, 0.0)
    gain /= length
    loss /= length
    out[idx[length]] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    for i in range(length + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        gain = (gain * (length - 1) + max(d, 0.0)) / length
        loss = (loss * (length - 1) + max(-d, 0.0)) / length
        out[idx[i]] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    return out


# --------------------------------------------------------------- episodes ---
def _runs(order: list[int], holds: Callable[[int], bool],
          releases: Callable[[int], bool]) -> list[list[int]]:
    runs, cur = [], None
    for i in order:
        if cur is None:
            if holds(i):
                cur = [i]
        elif releases(i):
            runs.append(cur)
            cur = None
        else:
            cur.append(i)
    if cur:
        runs.append(cur)
    return runs


def _merge_closed(runs: list[list[int]], last_bar: int,
                  gap: int = MERGE_GAP_BARS) -> list[list[tuple[int, int, list[int]]]]:
    """Two spells split by a poke of a point or two are one spell. NEVER
    applied to the open run: merging it across a break overstates how long the
    current state has held, which is the number a reader leans on hardest."""
    out: list[dict] = []
    for r in runs:
        r_open = r[-1] == last_bar
        prev_open = bool(out) and out[-1]["to"] == last_bar
        if out and not r_open and not prev_open and r[0] - out[-1]["to"] - 1 <= gap:
            out[-1]["to"] = r[-1]
            out[-1]["in_band"].extend(r)
            out[-1]["merged"] = True
        else:
            out.append({"from": r[0], "to": r[-1], "in_band": list(r),
                        "merged": False, "open": r_open})
    return out


def rsi_episodes(rsi: dict[int, float], last_bar: int) -> list[dict]:
    """Every spell in a band that still bears on now: the open one, plus any
    that ended within KEEP_EPISODE_WITHIN bars."""
    order = sorted(rsi)
    eps: list[dict] = []
    for state, (enter, exit_) in RSI_BANDS.items():
        over = state == "overbought"
        holds = (lambda i: rsi[i] > enter) if over else (lambda i: rsi[i] < enter)
        rel = (lambda i: rsi[i] <= exit_) if over else (lambda i: rsi[i] >= exit_)
        for run in _merge_closed(_runs(order, holds, rel), last_bar):
            pick = max if over else min
            k = pick(run["in_band"], key=lambda i: rsi[i])
            is_open = run["open"]
            rec = {"id": f"ep.rsi14.{state}.{run['from']}",
                   "subject_ref": "ind.rsi14", "state": state,
                   "from_bar": run["from"],
                   "to_bar": None if is_open else run["to"],
                   "open": is_open,
                   # bars actually in the band, NOT the span: a merged spell
                   # covers a gap, and counting the gap credits the state to
                   # minutes that were never in it
                   "bars_in_state": len(run["in_band"]),
                   "span_bars": run["to"] - run["from"] + 1,
                   "merge_gap_bars": MERGE_GAP_BARS if run["merged"] else None,
                   "extreme": _r1(rsi[k]), "extreme_at_bar": k}
            eps.append(rec)
    keep = [e for e in eps
            if e["open"] or (last_bar - (e["to_bar"] or 0)) <= KEEP_EPISODE_WITHIN]
    dropped = len(eps) - len(keep)
    if keep and dropped:
        keep[0]["_pruned"] = (f"{dropped} earlier spell(s) today ended more than "
                              f"{KEEP_EPISODE_WITHIN} bars ago and are not listed")
    return sorted(keep, key=lambda e: e["from_bar"])


# ----------------------------------------------------------------- levels ---
def level_record(price: float, role: str, bars: list[dict], nf: float,
                 p90: float, active_from: int, built_from: str,
                 price_as_of_bar: int, src: str) -> dict:
    """One price line and how price has behaved around it since it existed.

    A VISIT is price coming within half a bar-range-median of the line, and
    consecutive bars are ONE visit — counting bars instead put an early draft's
    touch count at 61 where there were 14. A CROSS is a close a whole
    bar-range-median onto the other side; the side is tracked against that same
    distance, so a close that creeps over the line cannot swallow the next real
    crossing.
    """
    touch, cross = nf / 2.0, nf
    window = [b for b in bars if b["bar_index"] >= active_from]
    side: Optional[int] = None
    crosses: list[dict] = []
    for b in window:
        d = b["close"] - price
        if abs(d) >= cross:
            s = 1 if d > 0 else -1
            if side is not None and s != side:
                crosses.append({"at_bar": b["bar_index"],
                                "direction": "up" if s > 0 else "down",
                                "strength": "decisive" if abs(d) >= p90 else "cross"})
            side = s
    visits: list[list[int]] = []
    for b in window:
        if b["low"] - touch <= price <= b["high"] + touch:
            i = b["bar_index"]
            if visits and i == visits[-1][1] + 1:
                visits[-1][1] = i
            else:
                visits.append([i, i])
    return {"id": f"lvl.{role}.{price:g}", "price": _r2(price), "role": role,
            "built_from": built_from, "src": src,
            "price_as_of_bar": price_as_of_bar, "active_from_bar": active_from,
            "interactions_covered_to_bar": window[-1]["bar_index"] if window else active_from,
            "visits": len(visits), "last_visit": visits[-1] if visits else None,
            "visits_rail": "touch_abs",
            "crosses": len(crosses), "last_cross": crosses[-1] if crosses else None,
            "crosses_rail": "cross_abs"}


# ----------------------------------------------------------------- phases ---
def segment_profile(prior_days: list[list[dict]]) -> Optional[dict]:
    """What share of a session's volume each part of the day usually carries,
    measured on THIS symbol's own recent sessions. Hardcoding it was the one
    thing in the draft that summed to 0.93 instead of 1."""
    shares: dict[str, list[float]] = {n: [] for n, _, _ in SEGMENTS}
    used = 0
    for bars in prior_days:
        total = sum(b["volume"] for b in bars)
        if len(bars) < BARS_PER_SESSION * 0.75 or total <= 0:
            continue
        used += 1
        for name, a, b_ in SEGMENTS:
            shares[name].append(
                sum(x["volume"] for x in bars if a <= x["bar_index"] <= b_) / total)
    if used < 2:
        return None
    med = {n: st.median(v) for n, v in shares.items() if v}
    total = sum(med.values())
    if not total:
        return None
    # a median is taken per segment, so the four do not sum to one on their own;
    # normalising is what makes them a share of a session rather than four
    # unrelated medians
    return {"n_sessions": used, "normalised": True,
            "fractions": {n: round(v / total, 3) for n, v in med.items()}}


# ------------------------------------------------------------------ build ---
def build_side(bars: list[dict], day: str, now: Optional[datetime] = None,
               levels: Optional[list[dict]] = None,
               levels_as_of_bar: Optional[int] = None,
               profile: Optional[dict] = None) -> dict:
    """The packet. `bars` are the sidecar's completed bars for `day`; `levels`
    is an optional list of {price, role} from the options engine, stamped with
    the bar its row was measured on. Pure: no clock, no disk, no network."""
    now = now or datetime.now(_ET)
    b = indexed(bars, day)
    if not b:
        return {"version": VERSION, "session": day, "bars_seen": 0,
                "absent": [{"path": "bars[]", "why": "not_observed",
                            "observer": "sndk_bars sidecar", "computable": True}]}
    last = b[-1]
    n = len(b)
    nf, p90 = bar_range_median(b), _p90_range(b)
    thin = n < MIN_BARS
    cum_vol = sum(x["volume"] for x in b)

    hi, lo = (_reduce(b, lambda x: x["high"], max),
              _reduce(b, lambda x: x["low"], min))
    readings = [
        {"id": "px.session_high", "value": _r2(hi["value"]), "at_bar": hi["at_bar"],
         "unit": "points"},
        {"id": "px.session_low", "value": _r2(lo["value"]), "at_bar": lo["at_bar"],
         "unit": "points"},
    ]
    by_i = {x["bar_index"]: x for x in b}
    for w in (5, 15):
        prev = by_i.get(last["bar_index"] - w)
        if prev:
            readings.append({"id": f"px.change_{w}b", "window_bars": w,
                           "value": _r2(last["close"] - prev["close"]),
                           "from_bar": prev["bar_index"], "at_bar": last["bar_index"],
                           "unit": "points_signed"})
    if nf:
        readings.append({"id": "px.bar_range", "value": _r2(last["high"] - last["low"]),
                       "at_bar": last["bar_index"], "unit": "points_abs",
                       "x": _r2((last["high"] - last["low"]) / nf),
                       "x_baseline": "bl.bar_range_median"})

    rsi = rsi_wilders(b)
    inds, eps = [], []
    if rsi:
        cur = rsi.get(last["bar_index"])
        label = None
        if cur is not None:
            label = ("overbought" if cur > RSI_BANDS["overbought"][0]
                    else "oversold" if cur < RSI_BANDS["oversold"][0] else "mid")
        dmin = min(rsi, key=lambda i: rsi[i])
        dmax = max(rsi, key=lambda i: rsi[i])
        inds.append({"id": "ind.rsi14", "value": _r1(cur), "at_bar": last["bar_index"],
                     "label": label, "day_min": _r1(rsi[dmin]), "day_min_at_bar": dmin,
                     "day_max": _r1(rsi[dmax]), "day_max_at_bar": dmax,
                     "src": "bars_1m_sidecar"})
        eps = rsi_episodes(rsi, last["bar_index"])
    else:
        inds.append({"id": "ind.rsi14", "value": None, "label": "warmup",
                     "at_bar": last["bar_index"], "src": "bars_1m_sidecar",
                     })

    vols = [x["volume"] for x in b]
    v30 = st.median(vols[-VOL_WINDOW - 1:-1]) if n > VOL_WINDOW else st.median(vols)
    vx = (last["volume"] / v30) if v30 else None
    inds.append({"id": "ind.vol", "at_bar": last["bar_index"],
                 "percentile_of_session": (None if thin else
                     round(100.0 * sum(1 for v in vols if v <= last["volume"]) / n, 1)),
                 "x": None if thin else _r2(vx),
                 "x_baseline": None if thin else "bl.vol_trailing_30",
                 "label": ("warmup" if thin else
                           "climax" if vx and vx >= CLIMAX_X[0] else "normal"),
                 "src": "bars_1m_sidecar",
                 })

    lv: list[dict] = []
    if nf:
        for L in (levels or []):
            price, role = _num(L.get("price")), L.get("role")
            if price is None or not role:
                continue
            lv.append(level_record(price, role, b, nf, p90, b[0]["bar_index"],
                                   "options_book",
                                   levels_as_of_bar if levels_as_of_bar is not None
                                   else last["bar_index"], "gex_engine"))
        for red, role in ((hi, "session_high"), (lo, "session_low")):
            lv.append(level_record(_r2(red["value"]), role, b, nf, p90,
                                   red["at_bar"], "live_tape", red["at_bar"],
                                   "bars_1m_sidecar"))

    ph = []
    for name, a, b_ in SEGMENTS:
        idx = [x for x in b if a <= x["bar_index"] <= b_]
        if not idx:
            continue
        row = {"id": f"segment.{name}", "from_bar": a, "to_bar": b_,
               "status": ("active" if a <= last["bar_index"] <= b_
                          else "closed" if last["bar_index"] > b_ else "pending"),
               "volume_fraction_to_date": round(sum(x["volume"] for x in idx) / cum_vol, 3)
               if cum_vol else None}
        if profile and profile.get("fractions", {}).get(name) is not None:
            row["expected_volume_fraction"] = profile["fractions"][name]
            row["expected_from_n_sessions"] = profile["n_sessions"]
        ph.append(row)

    cited = {s["at_bar"] for s in readings if s.get("at_bar") is not None}
    cited |= {s["from_bar"] for s in readings if s.get("from_bar") is not None}
    cited |= {e["extreme_at_bar"] for e in eps}
    for L in lv:
        if L.get("last_visit"):
            cited |= set(L["last_visit"])
        if L.get("last_cross"):
            cited.add(L["last_cross"]["at_bar"])
    cited |= {i["day_min_at_bar"] for i in inds if i.get("day_min_at_bar") is not None}
    cited |= {i["day_max_at_bar"] for i in inds if i.get("day_max_at_bar") is not None}
    cited |= {last["bar_index"]}
    if len(b) > 1:
        cited.add(b[-2]["bar_index"])

    absent = [
        {"path": "positions[]", "why": "not_observed", "observer": "broker account",
         "computable": True,
         },
        {"path": "premium[]", "why": "not_observed", "observer": "options chain",
         "computable": True, },
        {"path": "events[]", "why": "not_computed", "computable": True,
         },
        {"path": "confluence[]", "why": "not_computed", "computable": True,
         },
    ]
    if thin:
        absent.append({"path": "baselines[]", "why": "in_progress",
                       "computable": True,
                       "bars_seen": n})

    out = {
        "version": VERSION,
        "doctrine": VERSION,          # the standing sentences: sndk_side.DOCTRINE
        "session": day,
        "as_of": {"bar_index": last["bar_index"],
                  "timestamp": bar_time(last["bar_index"], day),
                  "closed": True,
                  "bars_seen": n, "bars_in_session": BARS_PER_SESSION,
                  "session_start": bar_time(0, day), "interval": "1m",
                  "covered_until_bar": last["bar_index"],
                  },
        "baselines": [
            {"id": "bl.bar_range_median", "value": _r2(nf), "kind": "session_to_date",
             "n": n, "drifts_intraday": True,
             },
            {"id": "bl.vol_trailing_30", "value": round(v30, 1) if v30 else None,
             "kind": "trailing_window", "window_bars": min(VOL_WINDOW, n),
             "drifts_intraday": True},
        ],
        "bars": {"count": n, "src": "bars_1m_sidecar",
                 "cumulative_volume": round(cum_vol),
                 "records": [by_i[i] for i in sorted(cited) if i in by_i],
                 },
        "readings": readings,
        "indicators": inds,
        "episodes": eps,
        "levels": lv,
        "level_rails": ({"touch_abs": _r2(nf / 2.0), "cross_abs": _r2(nf),
                         "decisive_abs": _r2(p90), "basis": "bl.bar_range_median",} if nf else None),
        "session_segments": ph,
        "absent": absent,
    }
    out["integrity"] = _integrity(out, b, rsi)
    return {k: v for k, v in out.items() if v not in (None, [], {})}


def _integrity(out: dict, bars: list[dict], rsi: dict) -> list[dict]:
    """Checks that run. Every one of these can fail; a check that cannot is
    worse than none, because it looks like evidence and is not."""
    by_i = {b["bar_index"]: b for b in bars}
    rows: list[dict] = []

    def add(check: str, scope: str, ok, detail: Optional[str] = None) -> None:
        r = {"check": check, "scope": scope,
             "status": "pass" if ok is True else ("warn" if ok == "warn" else "fail")}
        if detail:
            r["detail"] = detail
        rows.append(r)

    cited = {s["at_bar"] for s in out["readings"] if s.get("at_bar") is not None}
    cited |= {e["extreme_at_bar"] for e in out.get("episodes", [])}
    on_wire = {r["bar_index"] for r in out["bars"]["records"]}
    add("cited_bars_on_wire", f"{len(cited)} cited bars against {len(on_wire)} carried",
        cited <= on_wire,
        None if cited <= on_wire else f"missing {sorted(cited - on_wire)}")

    ok = True
    for s in out["readings"]:
        b = by_i.get(s.get("at_bar"))
        if b is None:
            continue
        if s["id"] == "px.session_high" and _r2(b["high"]) != s["value"]:
            ok = False
        if s["id"] == "px.session_low" and _r2(b["low"]) != s["value"]:
            ok = False
    add("values_recomputed_from_bars", "session extremes against the bar each names", ok)

    eps = out.get("episodes", [])
    open_eps = [e for e in eps if e["open"]]
    ok = all(all((rsi.get(i, 0) > RSI_BANDS["overbought"][0]) if e["state"] == "overbought"
                 else (rsi.get(i, 100) < RSI_BANDS["oversold"][0])
                 for i in range(e["from_bar"], bars[-1]["bar_index"] + 1))
             for e in open_eps)
    add("open_episode_not_merged",
        f"{len(open_eps)} open episode(s): every bar between from_bar and now is in the band", ok)

    # the check the first draft lacked: a merged spell spans a gap, so its
    # bars_in_state must count the bars that were IN the band, never the span
    bad = []
    for e in eps:
        end = e["to_bar"] if e["to_bar"] is not None else bars[-1]["bar_index"]
        # HYSTERESIS, not the entry rail: a spell is entered at 70/30 and only
        # released at 67/33, so the bars between the two rails are in state.
        # Checking against the entry rail undercounts every episode by however
        # long the reading loitered in the gap.
        exit_rail = RSI_BANDS[e["state"]][1]
        in_band = sum(1 for i in range(e["from_bar"], end + 1)
                      if (rsi.get(i) is not None and
                          (rsi[i] > exit_rail if e["state"] == "overbought"
                           else rsi[i] < exit_rail)))
        if in_band != e["bars_in_state"]:
            bad.append(f"{e['id']}: says {e['bars_in_state']}, bars give {in_band}")
    add("bars_in_state_counts_only_bars_in_the_band", f"{len(eps)} episodes",
        not bad, "; ".join(bad) if bad else None)

    add("counts_declare_their_rail", f"{len(out.get('levels', []))} levels",
        all("visits_rail" in l and "crosses_rail" in l for l in out.get("levels", [])))
    add("no_interaction_before_level_existed",
        "every level's visits and crosses start at or after active_from_bar",
        all((l["last_visit"] is None or l["last_visit"][0] >= l["active_from_bar"])
            and (l["last_cross"] is None or l["last_cross"]["at_bar"] >= l["active_from_bar"])
            for l in out.get("levels", [])))
    add("levels_declare_origin_and_vintage", f"{len(out.get('levels', []))} levels",
        all({"built_from", "price_as_of_bar", "active_from_bar"} <= set(l)
            for l in out.get("levels", [])))

    fr = [p["volume_fraction_to_date"] for p in out.get("session_segments", [])
          if p.get("volume_fraction_to_date") is not None]
    closed_or_active = len(fr) == len([p for p in out.get("session_segments", [])])
    s = sum(fr)
    add("segment_fractions_sum_to_one", f"{len(fr)} segments seen so far",
        (abs(s - 1.0) < 0.002) if closed_or_active and fr else "warn",
        f"sum={round(s, 3)} over the segments that have any bars yet")
    return rows


# ------------------------------------------------------------------ adapt ---
def prior_sessions(day: str, back: int = 10) -> list[list[dict]]:
    """The most recent completed sessions on disk before `day`, for the volume
    profile. Reads only the sidecar; a missing or thin file is simply skipped."""
    if sndk_bars is None:
        return []
    try:
        files = sorted(p for p in sndk_bars.bars_dir().glob("[0-9]" * 4 + "-*.jsonl")
                       if p.stem < day)
    except OSError:
        return []
    out = []
    for p in files[-back:]:
        out.append(indexed(sndk_bars.read_bars(p.stem), p.stem))
    return [b for b in out if b]


def side_for_day(day: Optional[str] = None, now: Optional[datetime] = None,
                 levels: Optional[list[dict]] = None,
                 levels_as_of_bar: Optional[int] = None) -> dict:
    """Read the sidecar for one session and build the packet from it."""
    now = now or datetime.now(_ET)
    day = day or now.astimezone(_ET).date().isoformat()
    if sndk_bars is None:
        return {"version": VERSION, "session": day, "bars_seen": 0,
                "absent": [{"path": "bars[]", "why": "unobservable",
                            "blocked_by": "sndk_bars", "computable": False}]}
    bars = sndk_bars.completed(sndk_bars.read_bars(day), now)
    return build_side(bars, day, now, levels=levels,
                      levels_as_of_bar=levels_as_of_bar,
                      profile=segment_profile(prior_sessions(day)))


def levels_from_row(row: dict) -> list[dict]:
    """The options engine's lines, in the shape build_side wants. The names are
    the project's own (gw_vocab): a wall carries a side, the flip is the flip."""
    out = []
    for key, role in (("call_wall", "wall_call"), ("put_wall", "wall_put"),
                      ("gamma_flip", "flip")):
        p = _num((row or {}).get(key))
        if p is not None:
            out.append({"price": p, "role": role})
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SNDK side payload (bar-anchored)")
    ap.add_argument("--day", help="YYYY-MM-DD (default today)")
    ap.add_argument("--compact", action="store_true", help="one line, no indent")
    a = ap.parse_args()
    pkt = side_for_day(a.day)
    print(json.dumps(pkt, separators=(",", ":")) if a.compact
          else json.dumps(pkt, indent=2))
