"""The JEV decision service: one run per SNDK PRO scan, output for the phone.

This is the microservice boundary. It reads what Mirai station already stores
(diary rows, minute bars, side packets, the chain cache), builds the labels,
asks JEV, and writes two things of its own under ``state/jev/``:

    state/jev/{day}.jsonl    every run, appended: the state, the requests, the answers
    state/jev/latest.json    the phone's file: the newest run, small, self-describing

It never writes into SNDK PRO's files and never runs on the scan path. Point the
viewstation's read-only route at ``latest.json`` and the phone has its card.

    python3 -m sndk_jev.service            # one run on the newest row, no send (no key)
    TYPESAFE_API_KEY=... python3 -m sndk_jev.service --send
    python3 -m sndk_jev.service --loop 120 # keep running, once per SNDK PRO tick
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as _clock
from datetime import datetime, timezone
from pathlib import Path

from .ab_test import pick, confidence
from .ask import DEFAULT_QUESTIONS, build_requests, load_questions, send, send_all
from .cadence import cadence_of, distance, ensure_cadence, fill_missing, load_cadence, load_last, plan, save_last
from .grade import run as grade_run
from .hour import answer_sentences, hour_request, hour_summary, load_hour_doc, load_weights
from .state_builder import DEFAULT_STATE_DIR, build_state, make_scene, parse_ts

SITUATION_PATHS = ("price.recent_move", "price.vs_vwap", "volume.now", "gex.air_to_wall", "iv.trend_30min")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


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
    """The phone's house rule is no Greek and no jargon in a string, so the unit is spelled out
    once per line and later figures in the same line stand bare. Labels keep 'sigma' for JEV;
    only the card's situation lines are rewritten."""
    out = _SIGMA_RULE.sub(r"\1 move rule", label)
    out = _SIGMA.sub(r"\1 of a normal day's move", out, count=1)
    out = _SIGMA.sub(r"\1", out)
    return out.replace(" sigma", "")


def named_probabilities(answer: dict) -> dict | None:
    """A Score answer's probabilities are keyed by level number; the phone should read the level's
    words instead, in plain units. Choice answers pass through unchanged."""
    probs = answer.get("probabilities")
    legend = answer.get("legend")
    if not probs or not isinstance(legend, dict):
        return probs
    return {plain(str(legend.get(str(k), k))): v for k, v in probs.items()}


def sum_the_hour(doc: dict, hour_doc: dict, answered: dict[str, dict], weights: dict) -> tuple[dict, dict | None]:
    """Steps 3 and 4: sentences from the answers, one question over them, one reply from JEV.
    Returns the hour record (what was used, what was left out, the request) and JEV's summary."""
    sentences, left_out = answer_sentences(doc, answered, weights)
    if not sentences:
        return {"used": {}, "left_out": left_out, "sentences": {}, "request": None}, None
    req = hour_request(sentences, hour_doc)
    try:
        reply = send(req)
    except RuntimeError as e:
        reply = {"error": str(e)}
    summary = hour_summary(reply)
    rec = {"used": {qid: answered[qid]["pick"] for qid in sentences}, "left_out": left_out,
           "sentences": sentences, "request": req}
    return rec, summary


def card(scene, state: dict, omitted: dict, doc: dict, requests: list, skipped: dict, answers: dict | None,
         sent: bool, send_seconds: float | None, hour: dict | None = None, held: dict | None = None,
         cad: dict | None = None) -> dict:
    """The phone's document. Small, plain, and honest about what was and was not sent."""
    now = datetime.now(timezone.utc)
    row_ts = parse_ts(scene.row["ts"])
    book = (scene.row.get("meta") or {}).get("book_asof")
    held = held or {}
    cad = cad or {}
    qs = []
    dark = []
    n_fresh = 0
    for group in doc["groups"]:
        for qid, q in group["questions"].items():
            if q.get("status") == "dark":
                # never asked, never counted; listed apart so the phone can say they exist
                dark.append({"id": qid, "viewpoint": q.get("viewpoint"), "ask": q.get("ask") or q["instructions"]})
                continue
            crit = q.get("criteria")
            entry = {"id": qid, "viewpoint": q.get("viewpoint"), "status": q.get("status", "live"),
                     "ask": q.get("ask") or q["instructions"], "why": q.get("why", ""), "type": q["type"],
                     # the options in the question's own order, so the phone draws them in a fixed place
                     "options": ["yes", "no"] if q["type"] == "noul" else (list(crit) if isinstance(crit, dict) else [])}
            if q.get("status") == "live":
                entry["cadence_min"] = cadence_of(cad, q, qid)
            asked = any(qid in r["questions"] for r in requests)
            if not asked and qid in held:
                # not due this read, or its label was missing: the last fresh answer stands, and says since when
                entry["answer"] = {k: v for k, v in held[qid].items() if k != "held_from"}
                entry["held_from"] = held[qid]["held_from"]
            elif not asked:
                reason = next((v for g in skipped.values() for k, v in g.items() if k == qid), "not asked")
                entry["answer"] = None
                entry["skipped"] = reason
            elif answers and any(qid in (a.get("answers") or {}) for a in answers.values()):
                a = next(a["answers"][qid] for a in answers.values() if qid in (a.get("answers") or {}))
                entry["answer"] = {"pick": pick(a), "confidence": confidence(a),
                                   "probabilities": named_probabilities(a), "noul": a.get("noul"), "score": a.get("score")}
                n_fresh += 1
            else:
                entry["answer"] = None
                entry["skipped"] = "asked, no answer received" if sent else "not sent, no key"
            qs.append(entry)
    return {
        "version": 1,
        "symbol": scene.row.get("ticker", "SNDK"),
        "generated_at": now.isoformat(timespec="seconds"),
        "row_ts": scene.row["ts"],
        "book_asof": book,
        "freshness": {"row_age_s": round((now - row_ts.astimezone(timezone.utc)).total_seconds()),
                      "bars_used": len(scene.bars), "prior_sessions": len(scene.prior_bars)},
        "sigma": scene.sigma,
        "situation": [plain(_get(state, p)) for p in SITUATION_PATHS if _get(state, p)],
        "labels": sum(len(v) for v in state.values()),
        "omitted": omitted,
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
        "shadow_note": "shadow questions and the sums are forecasts graded by the bars 30 and 60 minutes later; they are never a call",
    }


def run_once(state_dir: Path, out_dir: Path, doc: dict, send: bool, day: str | None = None) -> dict:
    scene = make_scene(state_dir, day)
    state, omitted = build_state(scene)
    now = parse_ts(scene.row["ts"])
    day_name = scene.row["ts"][:10]
    out_dir.mkdir(parents=True, exist_ok=True)
    # cadence: a live question is asked afresh only when its cadence has elapsed; the rest hold
    # their last answer. Without a key nothing is answered, so there is nothing to hold.
    cad = ensure_cadence(out_dir, doc, day_name) if send else load_cadence(out_dir)
    last = load_last(out_dir) if send else {}
    skip, held = plan(doc, last, cad, now) if send else ({}, {})
    requests, skipped = build_requests(state, doc, skip=skip)
    if send:
        held = fill_missing(doc, skipped, last, cad, now, held)
    answers, send_seconds, hour, hour_rec = None, None, None, None
    if send:
        t0 = _clock.monotonic()
        answers = send_all(requests)
        # steps 3 and 4: fresh and held answers become sentences, two questions sum them
        fresh = {}
        for a in answers.values():
            for qid, ans in (a.get("answers") or {}).items():
                fresh[qid] = {"pick": pick(ans), "confidence": confidence(ans), "probabilities": named_probabilities(ans),
                              "noul": ans.get("noul"), "score": ans.get("score")}
        answered = {**held, **fresh}
        hour_rec, hour = sum_the_hour(doc, load_hour_doc(), answered, load_weights(out_dir))
        send_seconds = round(_clock.monotonic() - t0, 3)
        if hour is not None:
            hour = {**hour, "used": len(hour_rec["used"]), "left_out": len(hour_rec["left_out"])}
        for qid, ans in fresh.items():
            # how far this answer moved from the last fresh one: past CHANGE_CUT the question is in motion
            prev = (last.get(qid) or {}).get("answer")
            last[qid] = {"row_ts": scene.row["ts"], "answer": ans, "moved": round(distance(prev, ans), 3)}
        save_last(out_dir, last)
    record = {"row_ts": scene.row["ts"], "book_asof": (scene.row.get("meta") or {}).get("book_asof"), "sigma": scene.sigma,
              "state": state, "omitted": omitted, "requests": requests, "skipped": skipped,
              "held": {qid: h["held_from"] for qid, h in held.items()}, "cadence_from": cad.get("recounted_from"),
              "answers": answers, "sent": send, "send_seconds": send_seconds, "hour": hour}
    with open(out_dir / f"{day_name}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if hour_rec and hour_rec.get("request"):
        # the hour record is what step 6 grades: spot and sigma are needed to read the bars against it
        (out_dir / "hour").mkdir(parents=True, exist_ok=True)
        with open(out_dir / "hour" / f"{day_name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"row_ts": scene.row["ts"], "spot": scene.row.get("spot"), "sigma": scene.sigma,
                                **(hour or {}), **hour_rec}, ensure_ascii=False) + "\n")
    if send:
        # step 6, every run: grade whatever hour has finished by now and refresh the weights step 3 reads
        try:
            grade_run(state_dir, out_dir)
        except Exception as e:  # grading must never stop the card
            print(f"grading skipped this run: {e}", file=sys.stderr)
    c = card(scene, state, omitted, doc, requests, skipped, answers, send, send_seconds, hour, held, cad)
    tmp = out_dir / "latest.json.tmp"
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out_dir / "latest.json")
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JEV decision service for SNDK PRO: one run per scan, output for the phone.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default <state-dir>/jev")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--day", help="build a past day's newest row instead of today's")
    ap.add_argument("--send", action="store_true", help="post to JEV; needs TYPESAFE_API_KEY")
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="keep running every N seconds")
    args = ap.parse_args(argv)
    load_env_file()
    if args.send and not os.environ.get("TYPESAFE_API_KEY"):
        print(f"--send needs TYPESAFE_API_KEY in the environment or in {ENV_FILE}", file=sys.stderr)
        return 2
    state_dir = Path(args.state_dir)
    out_dir = Path(args.out_dir) if args.out_dir else state_dir / "jev"
    doc = load_questions(args.questions)
    last_row = None
    while True:
        try:
            c = run_once(state_dir, out_dir, doc, args.send, args.day)
            if c["row_ts"] != last_row:
                n_ans = sum(1 for q in c["questions"] if q.get("answer"))
                print(f"{c['generated_at']} row {c['row_ts'][11:19]} labels {c['labels']} answered {n_ans}/{len(c['questions'])} "
                      f"{'sent' if c['sent'] else 'not sent'} -> {out_dir / 'latest.json'}", file=sys.stderr)
                last_row = c["row_ts"]
        except Exception as e:  # the service must never die on one bad row
            print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} run failed: {e}", file=sys.stderr)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        _clock.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
