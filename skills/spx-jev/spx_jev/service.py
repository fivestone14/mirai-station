"""The JEV decision service for SPX: one run per half hour, at :02 and :32, output for the phone.

This is the microservice boundary. It reads what Mirai station already stores (the SPX diary
rows, minute bars, the market context), builds the labels, asks JEV, sums the answers, blends the
sums with the time-of-day odds (clock.py), grades them, and writes only under ``state/spx_jev/``:

    state/spx_jev/{day}.jsonl        every run, appended: the state, the requests, the answers
    state/spx_jev/hour/{day}.jsonl   the sums, one record per run: what step 6 grades
    state/spx_jev/latest.json        the phone's file: the newest run, small, self-describing
    state/spx_jev/last_asked.json    the last fresh answer per question, for the cadence
    state/spx_jev/cadence.json       how often each question is asked, recounted daily
    state/spx_jev/clock_days.json    the time-of-day counts per past session (see clock.py)
    state/spx_jev/grades.jsonl, weights.json, weights_log.jsonl   step 6 (see grade.py, weights.py)
    state/spx_jev/archive/{day}.jsonl   the raw archive: every read, grade and close-out of both lanes (archive.py)
    state/spx_jev/pool_30.json, pool_60.json, pool_log.jsonl   the learning loop (pool.py)

Every time the card carries is a full timestamp with its offset, never a bare clock, so the phone
can show it in the viewer's own zone; prose meant for a reader names the market clock and says ET.

A sent run on today's newest row checks the wall clock: a row more than STALE_ROW_SKIP_MIN old is
skipped (the scanner has stopped; the last card stays). The SPX row carries no separate options-book
time (the book is rebuilt on every scan), so that one line covers the book too. A replay (--day)
checks nothing against the wall clock, and never writes into the station's records unless its
--out-dir names them: without one it writes into a fresh scratch folder, archive included, and says
where. Every read carries the tier-1 events due within the hour
(events.py) in its record, its sum record and the card; JEV never sees them.

On the live lane every sum also carries the learning loop's forecasts (pool.snapshot): the fixed
mixes of JEV's sum with the price-only reference, today's blend, the question block and the pool,
scored once the session is sealed. The phone keeps the exact blend (``shown_source``) unless the
loop was promoted and pool.POOL_ON_PHONE is set, which it is not.

A lane (lane.py) is the same run with its own docs, folder, clock and grader. The tape lane
(``--lane tape``) stamps each read at the newest finished bar (a sent run first waits, under a minute,
for the bar that finishes at its fire minute: wait_for_bar), measures the tape unit
(labels.rulers.tape_unit), asks what its schedule asks afresh, prices its sum's bands from the unit, and writes the
same files under state/spx_jev/lanes/tape/, each record marked with the lane and the unit.

    python3 -m spx_jev.service            # one run on the newest row, not sent
    python3 -m spx_jev.service --send     # post to JEV; the key comes from .env
    python3 -m spx_jev.service --day 2026-09-25   # replay a past day's newest row, into a scratch folder
    python3 -m spx_jev.service --send --lane tape # the opening lane's read
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time as _clock
import traceback
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import archive, pool
from .ask import build_requests, confidence, load_questions, pick, send, send_all
from .baseline import Baseline
from .cadence import cadence_of, distance, ensure_cadence, fill_missing, held_answer, load_cadence, load_last, plan, save_last
from .clock import blend as clock_blend, odds as clock_odds
from .events import tag as event_tag
from .expiry import calendar_of
from .grade import live_options, mark_at, run as grade_run
from .hour import answer_sentences, band_of, hour_request, hour_summary, load_hour_doc, named_levels
from .labels.registry import build_labels
from .lane import LANES, LANES_BY_KEY, LIVE, Lane
from .schedule import not_due, read_slot
from .sessions import session_close
from .state_builder import DEFAULT_STATE_DIR, load_bars, load_jsonl, make_scene, parse_ts
from .weights import QuestionWeights

# the situation the phone draws: four facts, each with a short title, the builder's verdict in a word, its
# figure (number, cut, kind) to draw and its full sentence behind a tap
SITUATION = (("price.recent_move", "Price, last 30 min"), ("price.vs_vwap", "Price against the day's average"),
             ("gex.air_to_wall", "Nearest heavy strike"), ("iv.trend_30min", "Implied volatility, last 30 min"))
VERDICT_WORDS = {"going_nowhere": "Flat", "rising": "Rising", "falling": "Falling", "flat": "Flat",
                 "at_it": "At it", "above": "Above", "below": "Below",
                 "heavy_strike_close": "Close", "open_air": "Open air", "no_wall_in_reach": "None in reach"}
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
ET = ZoneInfo("America/New_York")
STALE_ROW_S = 15 * 60          # a card built on a row older than this says so
STALE_ROW_SKIP_MIN = 6.0       # a live read on a row older than this is skipped: the scanner has stopped
LAST_READ_BEFORE_CLOSE_MIN = 28   # the job reads at :02 and :32, so the day's last read is 28 minutes before the close
UNSENT_DEFAULT = "not sent: this run was not asked to send"
CALLS_SHOWN = 4                # the phone draws the newest calls on one clock, so an overlap is visible
# A read on the bar clock waits for the bar that finishes at its fire minute. The bars job runs once a
# minute at no fixed second, so a wait under a minute spans one of its runs and the read stays in its minute.
BAR_WAIT_S = 55
BAR_POLL_S = 2.0


def log(msg: str) -> None:
    """One line to stderr with the UTC clock, so the launchd log reads in order."""
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} spx-jev :: {msg}", file=sys.stderr)


class NoRowYet(RuntimeError):
    """The scanner has no row for today: a sent run must wait for one, not build on yesterday's."""


def now_et() -> datetime:
    return datetime.now(ET)


def today_et() -> str:
    return now_et().date().isoformat()


def load_env_file(path: Path = ENV_FILE) -> list[str]:
    """Read KEY=VALUE lines from a .env file into the environment, never overriding a value
    already set. Returns the names it set. Values are never printed anywhere."""
    if not path.is_file():
        return []
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            names.append(key)
    return names


def _get(state: dict, path: str):
    g, k = path.split(".", 1)
    return (state.get(g) or {}).get(k)


_SIGMA_RULE = re.compile(r"(-?\d+(?:\.\d+)?) sigma move rule")
_SIGMA = re.compile(r"(-?\d+(?:\.\d+)?) sigma\b")


def plain(label: str) -> str:
    """The phone's house rule is no Greek and no jargon in a string: every figure keeps its unit,
    spelled out, and the move rule keeps its name. Labels keep 'sigma' for JEV; only the card's
    own lines are rewritten."""
    out = _SIGMA_RULE.sub(r"\1 move rule", label)
    out = _SIGMA.sub(r"\1 of a normal day's move", out)
    return out.replace(" sigma", "")


def named_probabilities(answer: dict) -> dict | None:
    """A Score answer's probabilities are keyed by level number; the phone should read the level's
    words instead, in plain units. Choice answers pass through unchanged."""
    probs = answer.get("probabilities")
    legend = answer.get("legend")
    if not probs or not isinstance(legend, dict):
        return probs
    return {plain(str(legend.get(str(k), k))): v for k, v in probs.items()}


def answer_entry(a: dict) -> dict:
    """What the card, the held store and the sums keep of one JEV answer. A Score answer's pick is
    named the way its probabilities are keyed, so the phone can bold it and the sums can quote it."""
    p = pick(a)
    legend = a.get("legend")
    if p is not None and isinstance(legend, dict):
        p = plain(str(legend.get(str(p), p)))
    return {"pick": p, "confidence": confidence(a), "probabilities": named_probabilities(a),
            "noul": a.get("noul"), "score": a.get("score")}


def sum_the_hour(doc: dict, hour_doc: dict, answered: dict[str, dict], weights: QuestionWeights,
                 fresh: dict[str, dict] | None = None, missing: list[str] | None = None,
                 lane: Lane = LIVE, unit: dict | None = None) -> tuple[dict, dict | None, dict | None]:
    """Steps 3 and 4: sentences from the answers, the lane's sum questions over them, one reply from
    JEV. Returns the hour record (what was used, what was fresh, what was left out or missing, the
    request), JEV's summary and its reply untouched, for the archive. A lane on the tape needs its
    ``unit`` to price the bands: without one there is no sum to ask."""
    sentences, left_out = answer_sentences(doc, answered, weights)
    fresh = fresh if fresh is not None else answered
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    # the grader pairs only answers given afresh on this read with the read's outcome
    fresh_picks = {qid: a["pick"] for qid, a in fresh.items() if by_id.get(qid, {}).get("status") == "live" and a.get("pick") is not None}
    base = {"used": {qid: answered[qid]["pick"] for qid in sentences}, "fresh": fresh_picks,
            "left_out": left_out, "missing": sorted(missing or []), "sentences": sentences}
    if not sentences:
        return {**base, "request": None}, None, None
    if lane.bar_clock and not unit:
        return {**base, "request": None, "no_sum": "no tape unit this read: the bars have stopped, so the bands cannot be priced"}, None, None
    req = hour_request(sentences, hour_doc, lane=lane, ruler=unit)
    try:
        reply = send(req)
    except Exception as e:  # send() scrubs the key and turns the network into RuntimeError; be safe anyway
        reply = {"error": str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"}
    return {**base, "request": req}, hour_summary(reply, lane), reply


def pool_snapshots(out_dir: Path, hour: dict, doc: dict, answered: dict[str, dict], fresh: set[str], now: datetime,
                   lane: Lane = LIVE) -> dict[str, dict]:
    """The learning loop's forecasts at this read, one per horizon (pool.snapshot), from JEV's own sum,
    the live clock's odds and the shown blend on the hour summary, and every live question's answer,
    fresh or held. A horizon JEV gave no probabilities for is left out, with the reason."""
    live = {qid: q for g in doc["groups"] for qid, q in g["questions"].items() if q.get("status") == "live"}
    members = {qid: pool.question_version(q) for qid, q in live.items()}
    answers = {qid: a for qid, e in answered.items() if qid in members and (a := pool.soft_answer(e))}
    baseline = Baseline.load()
    out = {}
    for h, (minutes, _) in lane.horizons.items():
        b = (hour.get("by") or {}).get(h)
        if not isinstance(b, dict) or not isinstance(b.get("probabilities"), dict):
            out[h] = {"left_out": f"JEV gave no probabilities for {h}"}
            continue
        jev = b["jev"]["probabilities"] if b.get("blended") else b["probabilities"]
        live_clock = b["clock"]["probabilities"] if b.get("blended") else None
        out[h] = pool.snapshot(pool.load_state(out_dir, minutes), baseline, h, now, jev, live_clock, b["probabilities"],
                               answers, members, fresh)
    return out


def borrowed_answers(state_dir: Path, doc: dict, lane: Lane) -> dict[str, dict]:
    """The last-asked entries of the questions this lane holds from their other lane (a schedule's
    ``hold_until``), read from that lane's own folder under the state dir."""
    out = {}
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            if "hold_until" not in (q.get("schedule") or {}):
                continue
            for key in q.get("lanes", []):
                other = LANES_BY_KEY.get(key)
                if other is not None and other is not lane and qid in (entries := load_last(other.folder(state_dir))):
                    out[qid] = entries[qid]
    return out


def last_read_of(out_dir: Path, day: str, now: datetime) -> datetime | None:
    """The lane's previous read today: the newest record in its day file stamped before ``now``. None on
    the day's first read, or on a replay of a day the lane never read; the stretch labels then measure
    from the open and the move since the last read is omitted."""
    stamps = []
    for rec in load_jsonl(out_dir / f"{day}.jsonl"):
        try:
            t = parse_ts(rec["row_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if t < now:
            stamps.append(t)
    return max(stamps) if stamps else None


def situation_rows(state: dict, figures: dict | None = None) -> list[dict]:
    """The situation as the phone draws it: each fact the builder could measure, in the order of SITUATION,
    with its title, its verdict in a word, its sentence in plain words and, when the builder handed one over,
    its figure (the kind, the number and the cut; the verdict rides on the row)."""
    rows = []
    for path, title in SITUATION:
        label = _get(state, path)
        if not label:
            continue
        fig = dict((figures or {}).get(path) or {})
        verdict = fig.pop("verdict", None)
        rows.append({"path": path, "title": title, "verdict": VERDICT_WORDS.get(verdict), "sentence": plain(label),
                     **({"figure": fig} if fig else {})})
    return rows


def day_calls(out_dir: Path, day: str, lane: Lane = LIVE) -> list[dict]:
    """Every call of the lane's primary sum on ``day``, oldest first, with its grade when it has one: the
    read's time, the mark it is graded at (the read's minute plus the horizon, as the grader counts it),
    the pick and its probability, every option's probability as the phone showed them (``odds``), then
    ``outcome``, ``hit`` and the ``moved`` behind them once graded, or ``closed`` with the reason
    when it can never be graded. The mark is grade.mark_at's: the closing bar for a read that ends just
    past the close, None for one that ends later and is never graded. A read whose sum got no answer is
    not a call."""
    minutes = lane.horizons[lane.primary][0]
    grades: dict[str, dict] = {}
    for g in load_jsonl(out_dir / "grades.jsonl"):
        ts = str(g.get("row_ts", ""))
        if not ts.startswith(day):
            continue
        res = g.get(lane.primary)
        if isinstance(res, dict) and res.get("band"):
            # the move behind the outcome, in the units the sum was banded in: sigma on the live lane,
            # points and tape units on the tape lane
            grades[ts] = {"outcome": res["band"], "hit": bool(res.get("hit")),
                          "moved": {k: res[k] for k in ("realized_sigma", "realized_points", "realized_units") if res.get(k) is not None}}
        elif ts not in grades and lane.primary in (g.get("skipped") or {}):
            grades[ts] = {"closed": str(g["skipped"][lane.primary])}
        elif ts not in grades and g.get("graded") is False:
            grades[ts] = {"closed": str(g.get("reason") or "not graded")}
    calls, seen = [], set()
    for r in load_jsonl(out_dir / "hour" / f"{day}.jsonl"):
        ts, p = r.get("row_ts"), r.get("probabilities")
        if not ts or ts in seen or not r.get("pick") or not isinstance(p, dict):
            continue
        seen.add(ts)                                   # a row written twice (a file from before the guard in run_once) is one call
        mark = mark_at(ts, minutes)
        odds = {k: round(float(v), 4) for k, v in p.items() if isinstance(v, (int, float))}
        calls.append({"read": ts, "mark": mark.isoformat() if mark else None, "minutes": minutes,
                      "pick": r["pick"], "p": odds.get(r["pick"], 0.0), "odds": odds, **grades.get(ts, {})})
    return calls


def calls_block(calls: list[dict]) -> dict:
    """What the card carries of the day's calls: the newest CALLS_SHOWN, newest first, and the day's tally."""
    return {"calls": calls[-CALLS_SHOWN:][::-1],
            "tally": {"calls": len(calls), "graded": sum(1 for c in calls if "outcome" in c),
                      "right": sum(1 for c in calls if c.get("hit"))}}


def _stamp(lane: Lane, unit: dict | None, band: dict | None = None) -> dict:
    """What a tagged lane writes on its record, its hour record and its card: the lane, the unit and,
    when the unit priced the sum's bands (hour.band_of), those bands in points, so the grader, the
    phone and the reader all see the ones JEV was told. The live lane writes nothing new."""
    if not lane.tag:
        return {}
    return {"lane": lane.tag, "ruler": unit, **({"band": band} if band else {})}


def card(scene, state: dict, omitted: dict, doc: dict, requests: list, skipped: dict, answers: dict | None,
         sent: bool, send_seconds: float | None, hour: dict | None = None, held: dict | None = None,
         cad: dict | None = None, unsent_reason: str = UNSENT_DEFAULT, event: dict | None = None,
         lane: Lane = LIVE, unit: dict | None = None, band: dict | None = None, calls: list[dict] | None = None,
         figures: dict | None = None) -> dict:
    """The phone's document. Small, plain, and honest about what was and was not sent. It carries the
    day's newest calls with their grades and the day's tally (day_calls), so the phone draws the calls
    in play on one clock. A lane on the bar clock also carries its ``stretch``, the sentences the builder
    wrote about the stretch since the lane's last read, and a lane with a schedule carries it, so the
    phone knows when the lane hands back to the live reads. Every time on it is a full timestamp."""
    now = datetime.now(timezone.utc)
    row_ts = parse_ts(scene.row["ts"])
    close = session_close(row_ts)
    held = held or {}
    cad = cad or {}
    qs = []
    dark = []
    n_fresh = 0
    errors = {rid: a["error"] for rid, a in (answers or {}).items() if isinstance(a, dict) and a.get("error")}
    for group in doc["groups"]:
        for qid, q in group["questions"].items():
            if q.get("status") == "dark":
                # never asked, never counted; listed apart so the phone can say they exist
                dark.append({"id": qid, "viewpoint": q.get("viewpoint", group["id"]), "ask": q.get("ask") or q["instructions"]})
                continue
            crit = q.get("criteria")
            entry = {"id": qid, "viewpoint": q.get("viewpoint", group["id"]), "status": q.get("status", "live"),
                     "ask": q.get("ask") or q["instructions"], "why": q.get("why", ""), "type": q["type"],
                     # the options in the question's own order, so the phone draws them in a fixed place;
                     # a yes/no answer's pick is "true" or "false", a Score's levels are its option names
                     # (hour.named_levels), else its criteria words
                     "options": (["true", "false"] if q["type"] == "noul" else list(crit) if isinstance(crit, dict)
                                 else list(q["options"]) if q.get("options")
                                 else [plain(str(c)) for c in crit] if isinstance(crit, list) else [])}
            if q.get("status") == "live":
                entry["cadence_min"] = cadence_of(cad, q, qid)
            asked_in = next((r["id"] for r in requests if qid in r["questions"]), None)
            fresh_a = next((a["answers"][qid] for a in (answers or {}).values() if qid in (a.get("answers") or {})), None)
            if fresh_a is not None:
                entry["answer"] = answer_entry(named_levels(q, fresh_a))
                n_fresh += 1
            elif qid in held:
                # not due, its label missing, or its group's request failed: the last fresh answer
                # stands, and says since when
                entry["answer"] = {k: v for k, v in held[qid].items() if k != "held_from"}
                entry["held_from"] = held[qid]["held_from"]
            elif asked_in is None:
                entry["answer"] = None
                entry["skipped"] = next((v for g in skipped.values() for k, v in g.items() if k == qid), "not asked")
            else:
                entry["answer"] = None
                entry["skipped"] = (f"asked, no answer received: {errors[asked_in]}" if asked_in in errors
                                    else "asked, no answer received") if sent else unsent_reason
            qs.append(entry)
    row_age_s = round((now - row_ts.astimezone(timezone.utc)).total_seconds())
    return {
        "version": 1,
        "symbol": "SPX",
        "generated_at": now.isoformat(timespec="seconds"),
        "row_ts": scene.row["ts"],
        "freshness": {"row_age_s": row_age_s, "bars_used": len(scene.bars), "prior_sessions": len(scene.prior_bars),
                      "stale": row_age_s > STALE_ROW_S,
                      "note": (f"the newest row is {row_age_s // 60} minutes old; nothing newer has been scanned"
                               if row_age_s > STALE_ROW_S else "built on a fresh row")},
        "sigma": scene.sigma,
        "situation": situation_rows(state, figures),
        "labels": sum(len(v) for v in state.values()),
        "omitted": {k: plain(str(v)) for k, v in omitted.items()},
        "sent": sent,
        "send_seconds": send_seconds,
        "model": next((a.get("model") for a in (answers or {}).values() if isinstance(a, dict) and a.get("model")), None),
        "questions": qs,
        "fresh": n_fresh,
        "held": len(held),
        "cadence_from": cad.get("recounted_from"),
        "cadence_note": "a question is asked at the reads its schedule names, a live one afresh only when its cadence has elapsed; in between, its last answer is held and says since when",
        "dark": dark,
        "dark_note": "dark questions are never asked and never counted; each waits for the source it needs",
        "hour": hour,
        # a tier-1 scheduled event due within the hour (events.py): a tag for the reader, never sent to JEV
        "event": event,
        **_stamp(lane, unit, band),
        **({"stretch": state.get("tape") or {}} if lane.bar_clock else {}),
        **calls_block(calls or []),
        # where each of this read's sums is measured (grade.mark_at), None for one that ends past the close
        "marks": {qid: (m.isoformat() if (m := mark_at(scene.row["ts"], h)) else None) for qid, (h, _) in lane.horizons.items()},
        **({"schedule": {"reads": [market_time(row_ts, t) for t in lane.schedule], "looks_ahead_min": lane.horizons[lane.primary][0],
                         "close_out": market_time(row_ts, lane.close_out)}} if lane.schedule else {}),
        # the phone's clock words ("after the close", "next read") follow the day's real close, 13:00 on a half day
        "session": {"close": close.isoformat(), "last_read": (close - timedelta(minutes=LAST_READ_BEFORE_CLOSE_MIN)).isoformat()},
        # today's 0DTE settle, the next expiry's and the next monthly's, and what expires today (expiry.py)
        "expiries": calendar_of(row_ts),
        "shadow_note": "the sums are forecasts graded by the bars at their marks; shadow questions are forecasts logged and never graded; neither is ever a call",
    }


def market_time(day_of: datetime, hhmm: str) -> str:
    """A lane's "HH:MM" market time on the read's day, as a full timestamp in New York time."""
    return datetime.combine(day_of.date(), time.fromisoformat(hhmm), tzinfo=ET).isoformat()


def run_once(state_dir: Path, out_dir: Path | None, doc: dict, do_send: bool, day: str | None = None,
             unsent_reason: str = UNSENT_DEFAULT, lane: Lane = LIVE) -> dict:
    out_dir = lane.folder(state_dir, out_dir)     # a tagged lane without a folder of its own refuses here
    scene = make_scene(state_dir, day, bar_clock=lane.bar_clock, horizon=f"the next {lane.horizons[lane.primary][0]} minutes")
    if lane.bar_clock:
        # the stretch labels measure from the lane's previous read today, stamped on its own records
        scene.last_read = last_read_of(out_dir, scene.row["ts"][:10], scene.now)
    unit = scene.unit
    # the sum's bands for this read, in points from the unit: stamped on everything the lane writes
    band = band_of(unit) if unit else None
    if day is None and do_send and scene.row["ts"][:10] != today_et():
        # the scanner has no row for today yet: sending on yesterday's last row would hold every
        # answer against a stale clock and file the read under the wrong day. Say so and stop.
        raise NoRowYet(f"no diary row for today yet: the newest row is {scene.row['ts'][:16]}; nothing sent, card unchanged")
    if day is None and do_send and any(r.get("row_ts") == scene.row["ts"] for r in load_jsonl(out_dir / f"{scene.row['ts'][:10]}.jsonl")):
        # the same row again (a run by hand, or the tape lane's newest bar not yet moved on): a second
        # read of one moment would put a second sum on the card beside the first, which is the one graded
        raise NoRowYet(f"the row at {scene.row['ts'][11:19]} was already read; nothing new to read, card unchanged")
    if day is None and do_send:
        # a live read on a stalled scanner would answer, sum and grade a row that no longer describes
        # the market; skip it and leave the last card, whose age the phone shows
        age_min = (datetime.now(timezone.utc) - parse_ts(scene.row["ts"]).astimezone(timezone.utc)).total_seconds() / 60.0
        if age_min > STALE_ROW_SKIP_MIN:
            raise NoRowYet(f"the newest row is {age_min:.1f} minutes old, past the {STALE_ROW_SKIP_MIN:g}-minute line: the scanner has stopped; nothing sent, card unchanged")
    labels = build_labels(scene)
    state, omitted, figures = labels.state, labels.omitted, labels.figures
    now = parse_ts(scene.row["ts"])
    try:
        event = event_tag(now)
    except Exception as e:  # a broken calendar must never cost the read
        log(f"the event calendar could not be read this run: {type(e).__name__}: {e}")
        event = None
    day_name = scene.row["ts"][:10]
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    live_ids = {qid for qid, q in by_id.items() if q.get("status") == "live"}
    out_dir.mkdir(parents=True, exist_ok=True)
    # the schedule: a question is asked only at the reads its schedule names, on replays too. Sending,
    # a live question it does not ask holds its answer (a day constant all day, one held from the other
    # lane until its hour), and on a lane with a cadence a due one is asked afresh only when its cadence
    # has elapsed. Without a key nothing is answered, so there is nothing to hold.
    skip = not_due(doc, lane, now)
    if lane.cadence:
        cad = ensure_cadence(out_dir, doc, day_name) if do_send else load_cadence(out_dir)
    else:
        cad = {}
    last, held = {}, {}
    if do_send:
        last = load_last(out_dir)
        skip, held = plan(doc, last, cad, now, skip, borrowed_answers(state_dir, doc, lane), read_slot(lane, now), learned=lane.cadence)
    requests, skipped = build_requests(state, doc, skip=skip, gates=labels.gates)
    if do_send and lane.cadence:
        held = fill_missing(doc, skipped, last, cad, now, held)
    answers, send_seconds, hour, hour_rec, hour_reply = None, None, None, None, None
    if do_send:
        t0 = _clock.monotonic()
        answers = send_all(requests)
        for r in requests:
            err = (answers.get(r["id"]) or {}).get("error")
            if not err:
                continue
            # one group failed: its live questions keep their last fresh answer, if young enough
            log(f"group {r['id']} got no answer: {err}")
            for qid in r["questions"]:
                if qid in live_ids and qid not in held:
                    h = held_answer(last.get(qid), now, cadence_of(cad, by_id[qid], qid))
                    if h:
                        held[qid] = h
        # steps 3 and 4: fresh and held answers become sentences, two questions sum them
        # a Score's levels are named as the question's options, so its pick is one the weights, the grader and the loop know
        fresh = {qid: answer_entry(named_levels(by_id.get(qid, {}), ans))
                 for a in answers.values() for qid, ans in (a.get("answers") or {}).items()}
        answered = {**held, **fresh}
        # live questions skipped for a label the builder could not measure, and not covered by a held answer
        missing = [qid for g in skipped.values() for qid, why in g.items()
                   if qid in live_ids and str(why).startswith("missing") and qid not in held]
        hour_doc = load_hour_doc(lane=lane)
        hour_rec, hour, hour_reply = sum_the_hour(doc, hour_doc, answered, QuestionWeights.load(out_dir), fresh, missing, lane, unit)
        send_seconds = round(_clock.monotonic() - t0, 3)      # JEV's round trips only; the blend below is code
        if hour is not None and lane.clock_blend:
            # the sum the phone shows and the grader scores is JEV's sum blended half and half with
            # how often this time of day ended each way on prior sessions; JEV's own sum rides beside it
            try:
                hour = clock_blend(hour, clock_odds(state_dir, out_dir, scene.prior_bars, now))
            except Exception as e:  # the clock must never cost the read its sum
                log(f"the clock was left out this run: {type(e).__name__}: {e}")
                hour = {**hour, "blend": {"used": False, "why": f"the time-of-day odds failed this run: {type(e).__name__}"}}
        if hour is not None and lane.pool and isinstance(hour.get("by"), dict):
            # every forecast the learning loop will score, written down now; the phone keeps the exact
            # blend unless the loop's promotion and POOL_ON_PHONE both say otherwise
            try:
                hour_rec["pool"] = pool_snapshots(out_dir, hour, doc, answered, set(fresh), now, lane)
            except Exception as e:  # the loop must never cost the read its sum
                log(f"the learning loop's snapshot was left out this run: {type(e).__name__}: {e}")
                hour_rec["pool"] = {h: {"left_out": f"the snapshot failed this run: {type(e).__name__}"} for h in lane.horizons}
            hour = pool.shown(hour, hour_rec["pool"], pool.load_state(out_dir, lane.horizons[lane.primary][0]))
        if hour is not None:
            hour = {**hour, "used": len(hour_rec["used"]), "left_out": len(hour_rec["left_out"]), "missing": len(missing)}
            if hour.get("error"):
                log(f"the sums got no answer: {hour['error']}")
        for qid, ans in fresh.items():
            # how far this answer moved from the last fresh one on the same day: past CHANGE_CUT the
            # question is in motion. Yesterday's closing answer is not a move, it is a new day.
            prev_entry = last.get(qid) or {}
            prev = prev_entry.get("answer") if str(prev_entry.get("row_ts", ""))[:10] == day_name else None
            last[qid] = {"row_ts": scene.row["ts"], "answer": ans, "moved": round(distance(prev, ans), 3)}
        # a question that left the doc, or went dark, has nothing to hold
        last = {qid: v for qid, v in last.items() if qid in by_id and by_id[qid].get("status") != "dark"}
        save_last(out_dir, last)
    record = {"row_ts": scene.row["ts"], "sigma": scene.sigma, "event": event,
              **_stamp(lane, unit, band), "state": state, "omitted": omitted, "requests": requests, "skipped": skipped,
              "held": {qid: h["held_from"] for qid, h in held.items()}, "cadence_from": cad.get("recounted_from"),
              "answers": answers, "sent": do_send, "send_seconds": send_seconds, "hour": hour}
    with open(out_dir / f"{day_name}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if hour_rec and hour_rec.get("request"):
        # the sum record is what step 6 grades: spot and sigma are needed to read the bars against it
        (out_dir / "hour").mkdir(parents=True, exist_ok=True)
        # a lane on the tape stores the bands JEV was told, in points, so the grader reads the same ones
        with open(out_dir / "hour" / f"{day_name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"row_ts": scene.row["ts"], "spot": scene.row.get("spot"), "sigma": scene.sigma, "event": event,
                                **_stamp(lane, unit, band), **(hour or {}), **hour_rec}, ensure_ascii=False) + "\n")
    archive.append(lane.archive_folder(state_dir, out_dir), day_name, archive.ReadRecord(
        read_id=archive.read_id(lane.name, scene.row["ts"]), lane=lane.name, row_ts=scene.row["ts"], sent=do_send,
        spot=float(scene.row["spot"]), sigma=scene.sigma, labels=state, omitted=omitted, requests=requests, skipped=skipped,
        responses=answers, hour_request=(hour_rec or {}).get("request"), hour_response=hour_reply, hour=hour,
        pool=(hour_rec or {}).get("pool"),
        cadence={"from": cad.get("recounted_from"), "held": {qid: h["held_from"] for qid, h in held.items()}, "not_due": skip,
                 "asked": [qid for r in requests for qid in r["questions"]]},
        market_context=scene.market.at(now) if scene.market else None, event=event, ruler=unit, band=band))
    if do_send:
        # step 6, every run: grade every mark that has passed and refresh the weights step 3 reads
        try:
            grade_run(state_dir, out_dir, live_options(doc), lane=lane)
        except Exception as e:  # grading must never stop the card
            log(f"grading skipped this run: {type(e).__name__}: {e}\n{traceback.format_exc()}")
    c = card(scene, state, omitted, doc, requests, skipped, answers, do_send, send_seconds, hour, held, cad, unsent_reason, event, lane, unit, band,
             day_calls(out_dir, day_name, lane), figures)
    write_card(out_dir, c)
    return c


def wait_for_bar(state_dir: Path, fire: datetime, timeout_s: float = BAR_WAIT_S, sleep=_clock.sleep) -> bool:
    """Wait until today's bars file holds the bar that finishes at ``fire`` (the lane's read minute),
    at most ``timeout_s``. Without it a read is stamped at the minute before and measures its time
    windows a minute short (the 09:40 read's 10-minute big-print window, the 09:45 read's tape unit).
    True when the bar is there; False after the wait, and the read goes on with the newest bar on file."""
    day = fire.astimezone(ET).date().isoformat()
    deadline = _clock.monotonic() + timeout_s
    while True:
        bars = load_bars(state_dir, day)
        if bars and parse_ts(bars[-1]["ts"]) + timedelta(minutes=1) >= fire:
            return True
        if _clock.monotonic() >= deadline:
            return False
        sleep(BAR_POLL_S)


def write_card(out_dir: Path, c: dict) -> None:
    """Replace latest.json in one step, so the phone never reads half a card."""
    tmp = out_dir / "latest.json.tmp"
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out_dir / "latest.json")


def close_out(state_dir: Path, out_dir: Path, doc: dict, lane: Lane) -> dict | None:
    """A lane's run after its last read (lane.close_out): grade every mark that has passed and refresh
    the calls and the day's tally on the card the last read wrote. JEV is asked nothing and no read is
    recorded (the archive gets a close-out record), so the morning's last calls are graded the same
    day. None when the lane did not read today."""
    grade_run(state_dir, out_dir, live_options(doc), lane=lane)
    try:
        c = json.loads((out_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(c.get("row_ts", ""))[:10] != today_et():
        return None
    day = c["row_ts"][:10]
    calls = day_calls(out_dir, day, lane)
    c.update(calls_block(calls))
    c["closed_out_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_card(out_dir, c)
    archive.append(lane.archive_folder(state_dir, out_dir), day, archive.CloseOutRecord(lane=lane.name, day=day, calls=calls, tally=c["tally"]))
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JEV decision service for SPX: one run on the newest diary row, output for the phone.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default the lane's folder under <state-dir>: spx_jev, or spx_jev/lanes/<lane>; "
                                                   "a replay (--day) defaults to a fresh scratch folder instead")
    ap.add_argument("--questions", default=None, help="default the lane's question doc")
    ap.add_argument("--day", help="build a past day's newest row instead of today's")
    ap.add_argument("--send", action="store_true", help="post to JEV; the key comes from .env or TYPESAFE_API_KEY")
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="keep running every N seconds (the launchd job does not use this)")
    ap.add_argument("--lane", choices=sorted(LANES), default="live", help="live (:02 and :32, the default) or tape (the opening lane)")
    args = ap.parse_args(argv)
    lane = LANES[args.lane]
    load_env_file()
    do_send = bool(args.send)
    unsent = UNSENT_DEFAULT
    if do_send and not os.environ.get("TYPESAFE_API_KEY"):
        # the job always asks to send; without a key the run still writes the card, unsent
        log(f"no TYPESAFE_API_KEY in the environment or in {ENV_FILE}: running unsent")
        do_send, unsent = False, "not sent: no key on this machine"
    state_dir = Path(args.state_dir)
    if args.day and not args.out_dir:
        # a replay writes records, sums, grades and the archive like a live read: never into the
        # station's own folders unless --out-dir says so
        args.out_dir = tempfile.mkdtemp(prefix=f"spx-jev-replay-{args.day}-{lane.name}-")
        log(f"replay of {args.day}: writing under {args.out_dir}, not the station's records; --out-dir chooses the folder")
    out_dir = lane.folder(state_dir, args.out_dir)
    doc = load_questions(args.questions or lane.questions, lane.key)
    if lane.close_out and not args.day and not args.loop and now_et().strftime("%H:%M") >= lane.close_out:
        # the lane's reads are done for the day: the job's last fire only grades and refreshes the card
        c = close_out(state_dir, out_dir, doc, lane)
        log(f"{lane.name} lane closed out: {c['tally']['right']} of {c['tally']['graded']} graded calls right, "
            f"{c['tally']['calls']} calls" if c else f"{lane.name} lane: nothing to close out today")
        return 0
    last_row = None
    while True:
        if lane.bar_clock and do_send and not args.day:
            fire = now_et().replace(second=0, microsecond=0)
            if not wait_for_bar(state_dir, fire):
                log(f"no bar finished at {fire:%H:%M} after {BAR_WAIT_S} s: reading on the newest bar on file")
        try:
            c = run_once(state_dir, out_dir, doc, do_send, args.day, unsent, lane)
            if c["row_ts"] != last_row:
                n_ans = sum(1 for q in c["questions"] if q.get("answer"))
                log(f"row {c['row_ts'][11:19]} labels {c['labels']} answered {n_ans}/{len(c['questions'])} "
                    f"{'sent' if c['sent'] else 'not sent'}{' STALE ROW' if c['freshness']['stale'] else ''} -> {out_dir / 'latest.json'}")
                last_row = c["row_ts"]
        except NoRowYet as e:   # a quiet skip, not a failure: the next tick will find the row
            log(f"skipping this tick: {e}")
            if not args.loop:
                return 0
        except Exception as e:  # the service must never die on one bad row
            log(f"run failed: {type(e).__name__}: {e}" + ("" if isinstance(e, RuntimeError) else "\n" + traceback.format_exc()))
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        _clock.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
