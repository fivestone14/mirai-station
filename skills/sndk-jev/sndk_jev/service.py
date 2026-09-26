"""The JEV decision service: one run per half hour, at :02 and :32, output for the phone.

This is the microservice boundary. It reads what Mirai station already stores
(diary rows, minute bars, side packets, the chain cache), builds the labels,
asks JEV, sums the answers, blends the sums with the time-of-day odds (clock.py),
grades them, and writes only under ``state/jev/``:

    state/jev/{day}.jsonl        every run, appended: the state, the requests, the answers
    state/jev/hour/{day}.jsonl   the sums, one record per run: what step 6 grades
    state/jev/latest.json        the phone's file: the newest run, small, self-describing
    state/jev/last_asked.json    the last fresh answer per question, for the cadence
    state/jev/cadence.json       how often each question is asked, recounted daily
    state/jev/clock_days.json    the time-of-day counts per past session (see clock.py)
    state/jev/grades.jsonl, weights.json, weights_log.jsonl   step 6 (see grade.py)

A run on today's newest row checks the wall clock twice: a sent read on a row more than
STALE_ROW_SKIP_MIN old is skipped (the scanner has stopped; the last card stays), and the labels
built from the options book are left out when the book is more than state_builder.STALE_BOOK_MIN
old (sent or not, so an unsent card is as honest as a sent one). A replay (--day) checks neither.
Every read carries the tier-1 events due within the hour (events.py) in its record, its sum record
and the card; JEV never sees them.

It never writes into SNDK PRO's files and never runs on the scan path. Point the
viewstation's read-only route at ``latest.json`` and the phone has its card.

A lane (lane.py) is the same run with its own docs, folder, clock and grader. The tape lane
(``--lane tape``) stamps each read at the newest finished bar, measures the tape unit
(state_builder.ruler), asks everything afresh, prices its sum's bands from the unit, and writes the
same files under state/jev/lanes/tape/, each record marked with the lane and the unit. The live
lane is the default and runs as it always has.

    python3 -m sndk_jev.service            # one run on the newest row, not sent
    python3 -m sndk_jev.service --send     # post to JEV; the key comes from .env
    python3 -m sndk_jev.service --day 2026-09-22   # replay a past day's newest row
    python3 -m sndk_jev.service --send --lane tape # the opening lane's read
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as _clock
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .ab_test import pick, confidence
from .ask import build_requests, load_questions, send, send_all
from .clock import blend as clock_blend, odds as clock_odds
from .events import tag as event_tag
from .cadence import cadence_of, distance, ensure_cadence, fill_missing, held_answer, load_cadence, load_last, plan, save_last
from .grade import live_options, run as grade_run
from .hour import answer_sentences, band_of, hour_request, hour_summary, load_hour_doc, load_weights
from .lane import LANES, LIVE, Lane
from .state_builder import DEFAULT_STATE_DIR, build_state, load_jsonl, make_scene, omit_stale_book, parse_ts, session_close

SITUATION_PATHS = ("price.recent_move", "price.vs_vwap", "volume.now", "gex.air_to_wall", "iv.trend_30min")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
ET = ZoneInfo("America/New_York")
STALE_ROW_S = 15 * 60          # a card built on a row older than this says so
STALE_ROW_SKIP_MIN = 6.0       # a live read on a row older than this is skipped: the scanner has stopped
LAST_READ_BEFORE_CLOSE_MIN = 28   # the job reads at :02 and :32, so the day's last read is 28 minutes before the close
UNSENT_DEFAULT = "not sent: this run was not asked to send"


def log(msg: str) -> None:
    """One line to stderr with the UTC clock, so the launchd log reads in order."""
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} sndk-jev :: {msg}", file=sys.stderr)


class NoRowYet(RuntimeError):
    """The scanner has no row for today: a sent run must wait for one, not build on yesterday's."""


def today_et() -> str:
    return datetime.now(ET).date().isoformat()


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


def sum_the_hour(doc: dict, hour_doc: dict, answered: dict[str, dict], weights: dict,
                 fresh: dict[str, dict] | None = None, missing: list[str] | None = None,
                 lane: Lane = LIVE, unit: dict | None = None) -> tuple[dict, dict | None]:
    """Steps 3 and 4: sentences from the answers, the lane's sum questions over them, one reply from
    JEV. Returns the hour record (what was used, what was fresh, what was left out or missing, the
    request) and JEV's summary. A lane on the tape needs its ``unit`` to price the bands: without
    one there is no sum to ask."""
    sentences, left_out = answer_sentences(doc, answered, weights)
    fresh = fresh if fresh is not None else answered
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    # the grader pairs only answers given afresh on this read with the read's outcome
    fresh_picks = {qid: a["pick"] for qid, a in fresh.items() if by_id.get(qid, {}).get("status") == "live" and a.get("pick") is not None}
    base = {"used": {qid: answered[qid]["pick"] for qid in sentences}, "fresh": fresh_picks,
            "left_out": left_out, "missing": sorted(missing or []), "sentences": sentences}
    if not sentences:
        return {**base, "request": None}, None
    if lane.bar_clock and not unit:
        return {**base, "request": None, "no_sum": "no tape unit this read: the bars have stopped, so the bands cannot be priced"}, None
    req = hour_request(sentences, hour_doc, lane=lane, ruler=unit)
    try:
        reply = send(req)
    except Exception as e:  # send() scrubs the key and turns the network into RuntimeError; be safe anyway
        reply = {"error": str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"}
    return {**base, "request": req}, hour_summary(reply, lane)


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


def _stamp(lane: Lane, unit: dict | None, band: dict | None = None) -> dict:
    """What a tagged lane writes on its record, its hour record and its card: the lane, the unit and,
    when the unit priced the sum's bands (hour.band_of), those bands in dollars, so the grader, the
    phone and the reader all see the ones JEV was told. The live lane writes nothing new."""
    if not lane.tag:
        return {}
    return {"lane": lane.tag, "ruler": unit, **({"band": band} if band else {})}


def card(scene, state: dict, omitted: dict, doc: dict, requests: list, skipped: dict, answers: dict | None,
         sent: bool, send_seconds: float | None, hour: dict | None = None, held: dict | None = None,
         cad: dict | None = None, unsent_reason: str = UNSENT_DEFAULT, event: dict | None = None,
         lane: Lane = LIVE, unit: dict | None = None, band: dict | None = None) -> dict:
    """The phone's document. Small, plain, and honest about what was and was not sent. A lane on the
    bar clock also carries its ``stretch``: the sentences the builder wrote about the stretch since
    the lane's last read, which the phone's strip shows in the builder's own words."""
    now = datetime.now(timezone.utc)
    row_ts = parse_ts(scene.row["ts"])
    book = (scene.row.get("meta") or {}).get("book_asof")
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
                dark.append({"id": qid, "viewpoint": q.get("viewpoint"), "ask": q.get("ask") or q["instructions"]})
                continue
            crit = q.get("criteria")
            entry = {"id": qid, "viewpoint": q.get("viewpoint"), "status": q.get("status", "live"),
                     "ask": q.get("ask") or q["instructions"], "why": q.get("why", ""), "type": q["type"],
                     # the options in the question's own order, so the phone draws them in a fixed place;
                     # a yes/no answer's pick is "true" or "false", a Score's levels are its legend words
                     "options": (["true", "false"] if q["type"] == "noul" else list(crit) if isinstance(crit, dict)
                                 else [plain(str(c)) for c in crit] if isinstance(crit, list) else [])}
            if q.get("status") == "live":
                entry["cadence_min"] = cadence_of(cad, q, qid)
            asked_in = next((r["id"] for r in requests if qid in r["questions"]), None)
            fresh_a = next((a["answers"][qid] for a in (answers or {}).values() if qid in (a.get("answers") or {})), None)
            if fresh_a is not None:
                entry["answer"] = answer_entry(fresh_a)
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
        "symbol": scene.row.get("ticker", "SNDK"),
        "generated_at": now.isoformat(timespec="seconds"),
        "row_ts": scene.row["ts"],
        "book_asof": book,
        "freshness": {"row_age_s": row_age_s, "bars_used": len(scene.bars), "prior_sessions": len(scene.prior_bars),
                      "stale": row_age_s > STALE_ROW_S,
                      "note": (f"the newest row is {row_age_s // 60} minutes old; nothing newer has been scanned"
                               if row_age_s > STALE_ROW_S else "built on a fresh row")},
        "sigma": scene.sigma,
        "situation": [plain(_get(state, p)) for p in SITUATION_PATHS if _get(state, p)],
        "labels": sum(len(v) for v in state.values()),
        "omitted": {k: plain(str(v)) for k, v in omitted.items()},
        "sent": sent,
        "send_seconds": send_seconds,
        "model": next((a.get("model") for a in (answers or {}).values() if isinstance(a, dict) and a.get("model")), None),
        "questions": qs,
        "fresh": n_fresh,
        "held": len(held),
        "cadence_from": cad.get("recounted_from"),
        "cadence_note": "a live question is asked afresh only when its cadence has elapsed; in between, its last answer is held and says since when",
        "dark": dark,
        "dark_note": "dark questions are never asked and never counted; the news questions wait for a news source",
        "hour": hour,
        # a tier-1 scheduled event due within the hour (events.py): a tag for the reader, never sent to JEV
        "event": event,
        **_stamp(lane, unit, band),
        **({"stretch": state.get("tape") or {}} if lane.bar_clock else {}),
        # the phone's clock words ("after the close", "next read") follow the day's real close, 13:00 on a half day
        "session": {"close": session_close(row_ts).strftime("%H:%M"),
                    "last_read": (session_close(row_ts) - timedelta(minutes=LAST_READ_BEFORE_CLOSE_MIN)).strftime("%H:%M")},
        "shadow_note": "the sums are forecasts graded by the bars 30 and 60 minutes later; shadow questions are forecasts logged and never graded; neither is ever a call",
    }


def run_once(state_dir: Path, out_dir: Path | None, doc: dict, do_send: bool, day: str | None = None,
             unsent_reason: str = UNSENT_DEFAULT, lane: Lane = LIVE) -> dict:
    out_dir = lane.folder(state_dir, out_dir)     # a tagged lane without a folder of its own refuses here
    scene = make_scene(state_dir, day, bar_clock=lane.bar_clock, horizon=f"the next {lane.horizons[lane.primary][0]} minutes")
    if lane.bar_clock:
        # the stretch labels measure from the lane's previous read today, stamped on its own records
        scene.last_read = last_read_of(out_dir, scene.row["ts"][:10], scene.now)
    unit = scene.unit
    # the sum's bands for this read, in dollars from the unit: stamped on everything the lane writes
    band = band_of(unit, load_hour_doc(lane=lane), lane.primary) if unit else None
    if day is None and do_send and scene.row["ts"][:10] != today_et():
        # the scanner has no row for today yet: sending on yesterday's last row would hold every
        # answer against a stale clock and file the read under the wrong day. Say so and stop.
        raise NoRowYet(f"no diary row for today yet: the newest row is {scene.row['ts'][:16]}; nothing sent, card unchanged")
    if day is None and do_send:
        # a live read on a stalled scanner would answer, sum and grade a row that no longer describes
        # the market; skip it and leave the last card, whose age the phone shows
        age_min = (datetime.now(timezone.utc) - parse_ts(scene.row["ts"]).astimezone(timezone.utc)).total_seconds() / 60.0
        if age_min > STALE_ROW_SKIP_MIN:
            raise NoRowYet(f"the newest row is {age_min:.1f} minutes old, past the {STALE_ROW_SKIP_MIN:g}-minute line: the scanner has stopped; nothing sent, card unchanged")
    state, omitted = build_state(scene)
    if day is None:
        # the options book is judged at the read, by the wall clock: a fresh row can still carry a book
        # the scanner has not refreshed, and a strike description from it would be passed off as now
        asof = (scene.row.get("meta") or {}).get("book_asof")
        try:
            book_age = (datetime.now(timezone.utc) - parse_ts(asof).astimezone(timezone.utc)).total_seconds() / 60.0
        except (TypeError, ValueError, AttributeError):
            book_age = float("inf")            # no book time, or one that cannot be read: not a book to describe
        state, omitted = omit_stale_book(state, omitted, book_age)
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
    # cadence: a live question is asked afresh only when its cadence has elapsed; the rest hold
    # their last answer. Without a key nothing is answered, so there is nothing to hold. A lane
    # without a cadence asks everything afresh on every read and keeps no last-asked file.
    if lane.cadence:
        cad = ensure_cadence(out_dir, doc, day_name) if do_send else load_cadence(out_dir)
        last = load_last(out_dir) if do_send else {}
        skip, held = plan(doc, last, cad, now) if do_send else ({}, {})
    else:
        cad, last, skip, held = {}, {}, {}, {}
    requests, skipped = build_requests(state, doc, skip=skip)
    if do_send and lane.cadence:
        held = fill_missing(doc, skipped, last, cad, now, held)
    answers, send_seconds, hour, hour_rec = None, None, None, None
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
        fresh = {qid: answer_entry(ans) for a in answers.values() for qid, ans in (a.get("answers") or {}).items()}
        answered = {**held, **fresh}
        # live questions skipped for a label the builder could not measure, and not covered by a held answer
        missing = [qid for g in skipped.values() for qid, why in g.items()
                   if qid in live_ids and str(why).startswith("missing") and qid not in held]
        hour_doc = load_hour_doc(lane=lane)
        hour_rec, hour = sum_the_hour(doc, hour_doc, answered, load_weights(out_dir), fresh, missing, lane, unit)
        send_seconds = round(_clock.monotonic() - t0, 3)      # JEV's round trips only; the blend below is code
        if hour is not None and lane.clock_blend:
            # the sum the phone shows and the grader scores is JEV's sum blended half and half with
            # how often this time of day ended each way on prior sessions; JEV's own sum rides beside it
            try:
                hour = clock_blend(hour, clock_odds(state_dir, out_dir, scene.prior_bars, now))
            except Exception as e:  # the clock must never cost the read its sum
                log(f"the clock was left out this run: {type(e).__name__}: {e}")
                hour = {**hour, "blend": {"used": False, "why": f"the time-of-day odds failed this run: {type(e).__name__}"}}
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
        if lane.cadence:
            save_last(out_dir, last)
    record = {"row_ts": scene.row["ts"], "book_asof": (scene.row.get("meta") or {}).get("book_asof"), "sigma": scene.sigma, "event": event,
              **_stamp(lane, unit, band), "state": state, "omitted": omitted, "requests": requests, "skipped": skipped,
              "held": {qid: h["held_from"] for qid, h in held.items()}, "cadence_from": cad.get("recounted_from"),
              "answers": answers, "sent": do_send, "send_seconds": send_seconds, "hour": hour}
    with open(out_dir / f"{day_name}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if hour_rec and hour_rec.get("request"):
        # the sum record is what step 6 grades: spot and sigma are needed to read the bars against it
        (out_dir / "hour").mkdir(parents=True, exist_ok=True)
        # a lane on the tape stores the bands JEV was told, in dollars, so the grader reads the same ones
        with open(out_dir / "hour" / f"{day_name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"row_ts": scene.row["ts"], "spot": scene.row.get("spot"), "sigma": scene.sigma, "event": event,
                                **_stamp(lane, unit, band), **(hour or {}), **hour_rec}, ensure_ascii=False) + "\n")
    if do_send:
        # step 6, every run: grade every mark that has passed and refresh the weights step 3 reads
        try:
            grade_run(state_dir, out_dir, allowed=live_options(doc), lane=lane)
        except Exception as e:  # grading must never stop the card
            log(f"grading skipped this run: {type(e).__name__}: {e}\n{traceback.format_exc()}")
    c = card(scene, state, omitted, doc, requests, skipped, answers, do_send, send_seconds, hour, held, cad, unsent_reason, event, lane, unit, band)
    tmp = out_dir / "latest.json.tmp"
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out_dir / "latest.json")
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JEV decision service for SNDK PRO: one run on the newest row, output for the phone.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default the lane's folder under <state-dir>: jev, or jev/lanes/<lane>")
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
    out_dir = lane.folder(state_dir, args.out_dir)
    doc = load_questions(args.questions or lane.questions)
    last_row = None
    while True:
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
