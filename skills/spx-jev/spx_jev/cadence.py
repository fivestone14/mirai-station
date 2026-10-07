"""Cadence: how often each live question is asked afresh, and what is held in between.

The question's schedule (schedule.py) says when it may be asked at all; the cadence below can only thin
that out on the live lane. Between fresh answers the last answer is held, and a question its schedule
does not ask at a read holds too: a day constant (asked at 09:35, or at 10:02, and held) all day, a
question held from its other lane until the hour its schedule names, and an hourly one while young.
A day constant with no answer from today to hold (its one read was skipped, it slept there or its label
was missing) is asked at the lane's next read instead, and held from that answer on.
A day constant whose schedule says ``then`` (re-asked when the code's answer changes) is asked again
on the first read whose code answer differs from the one the code had when its held answer was given.
Only this side is built: nothing works out a code answer yet, so the service passes plan() none and
stores none in last_asked.json, and such a question is held all day.

An ask that got no answer (JEV timed out or refused the request, after its retries) is not an ask: the
service writes it into the question's last-asked entry as ``lost``, and the lane's next read asks the
live question again whatever its schedule or cadence says (a day constant, one off its schedule, one
held), and every read after that until a fresh answer replaces the entry or the day ends. A read whose
labels cannot ask it leaves it for the next.

    python3 -m spx_jev.cadence                     # the table for the newest day with records
    python3 -m spx_jev.cadence --day 2026-09-29 --write    # recount that day and write cadence.json

A read comes every 30 minutes (the job fires at :02 and :32), so a cadence is one of
    30   asked on every read
    60   asked on every other read
   120   asked on every fourth read
Between fresh answers the last answer is held: it stays on the card and in the sums'
sentences, stamped with the moment it was given. A held answer also covers a read on which
the question's label was missing, for up to twice its cadence.

What counts as a change: the whole answer, not the picked word. Two answers are compared
as probability vectors and their distance is the total absolute difference across the
options (0 to 2); a distance of CHANGE_CUT or more is a change. So heavy 0.90 turning into
heavy 0.55 is a change (0.70) although the pick held, and 0.90 to 0.88 is not.

How a cadence is set (``recount``), once a day from the previous day's runs:
    for each live question, the time its answer held before changing, read to read;
    the 25th percentile of those holds, halved, snapped down to 30, 60 or 120.
At read time a question whose last two fresh answers differed by CHANGE_CUT or more is
"in motion" and is asked again whatever its cadence says.
    A question that never changed gets 60 with three hours of runs behind it and 120 with
    five hours and ten reads, and keeps its previous cadence under three hours; fewer than six
    reads leaves the previous cadence in place. The hold still running at the last read counts,
    and under four holds the median stands in for the quartile.
The schedule's ``every_min`` is the starting value until a recount exists; the doc's free-text
``cadence`` is for people and is never read.

Files, under state/spx_jev/:
    cadence.json      {"recounted_from": day, "questions": {qid: {"minutes", "p25_hold_min", "changes", "reads", "why"}}}
    last_asked.json   {qid: {"row_ts", "answer", "moved", "code_answer", "lost", "asleep"}}   the newest fresh answer per
                      question, how far it moved from the one before, the code's answer at that read for a question
                      that has one (not written yet: see the note on ``then`` above), and, until a fresh answer
                      replaces the entry, the ask since then that got no answer: {"row_ts", "why"}, and the newest
                      read at which the question slept (``asleep``, its row's timestamp): an answer from before
                      its gate or its label said the thing it asked about was over is never held again
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .ask import paths_in
from .lane import LIVE
from .schedule import every_min, holds_for_the_day
from .state_builder import DEFAULT_STATE_DIR, load_jsonl, parse_ts

CADENCE_NAME = "cadence.json"
LAST_NAME = "last_asked.json"
STEPS = (30, 60, 120)
MIN_READS = 6
GRACE_MIN = 5                 # a read up to five minutes early still counts as on time (the :02 and :32 ticks drift)
MIN_GAP_MIN = 25              # reads closer than this (a by-hand run, the old two-minute schedule) are not separate reads
CHANGE_CUT = 0.3              # total probability moved across the options that counts as a change


def snap(minutes: float) -> int:
    out = STEPS[0]
    for step in STEPS:
        if minutes >= step:
            out = step
    return out


def load_cadence(out_dir: Path) -> dict:
    p = Path(out_dir) / CADENCE_NAME
    if not p.is_file():
        return {"recounted_from": None, "questions": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("questions"), dict) else {"recounted_from": None, "questions": {}}
    except json.JSONDecodeError:
        return {"recounted_from": None, "questions": {}}


def cadence_of(cad: dict, q: dict, qid: str) -> int:
    """The recounted minutes, else the schedule's every_min snapped to the steps (every read without one)."""
    entry = (cad.get("questions") or {}).get(qid) or {}
    m = entry.get("minutes")
    return int(m) if isinstance(m, (int, float)) and m in STEPS else snap(every_min(q.get("schedule")) or STEPS[0])


def load_last(out_dir: Path) -> dict:
    p = Path(out_dir) / LAST_NAME
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_last(out_dir: Path, last: dict) -> None:
    p = Path(out_dir) / LAST_NAME
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def vector(answer: dict | None) -> dict[str, float]:
    """An answer as probabilities per option; a yes/no answer becomes {yes, no}."""
    if not isinstance(answer, dict):
        return {}
    p = answer.get("probabilities")
    if isinstance(p, dict) and p:
        return {str(k): float(v) for k, v in p.items() if isinstance(v, (int, float))}
    n = answer.get("noul")
    if isinstance(n, (int, float)):
        return {"yes": round(float(n), 6), "no": round(1.0 - float(n), 6)}
    return {}


def distance(a: dict | None, b: dict | None) -> float:
    """Total absolute difference across the options, 0 (identical) to 2 (all the weight moved)."""
    va, vb = vector(a), vector(b)
    if not va or not vb:
        return 0.0
    return sum(abs(va.get(k, 0.0) - vb.get(k, 0.0)) for k in set(va) | set(vb))


def _age_min(entry: dict | None, now: datetime) -> float | None:
    if not entry or not entry.get("row_ts"):
        return None
    try:
        return (now - parse_ts(entry["row_ts"])).total_seconds() / 60.0
    except ValueError:
        return None


def is_due(entry: dict | None, now: datetime, minutes: int) -> bool:
    age = _age_min(entry, now)
    return age is None or age >= minutes - GRACE_MIN


def slept_since(entry: dict | None) -> bool:
    """Whether the question slept at a read after its last fresh answer (``asleep``, mark_asleep): what that answer
    was about has ended, so it is never held again."""
    try:
        return bool(entry and entry.get("asleep") and entry.get("row_ts") and parse_ts(entry["asleep"]) >= parse_ts(entry["row_ts"]))
    except ValueError:
        return False


def mark_asleep(last: dict, skipped: dict[str, dict[str, str]], live: set[str], row_ts: str) -> None:
    """Stamp ``row_ts`` as ``asleep`` on the last-asked entry of every live question that slept at this read
    (ask.build_requests' "asleep: " reasons), so no answer from before it is held again (slept_since)."""
    for qs in skipped.values():
        for qid, why in qs.items():
            if qid in live and qid in last and str(why).startswith("asleep:"):
                last[qid] = {**last[qid], "asleep": row_ts}


def held_answer(entry: dict | None, now: datetime, minutes: int) -> dict | None:
    """The last fresh answer, stamped with the read it was given on (``held_from``, the full timestamp),
    while it is still young enough: up to twice the cadence, never under an hour, so a held answer is
    never a stale one. Never one the question slept after (slept_since)."""
    age = _age_min(entry, now)
    if age is None or age < 0 or not isinstance(entry.get("answer"), dict) or slept_since(entry):
        return None                     # a negative age is a replay of an earlier day: never hold a later answer
    if age > max(2 * minutes, 60) + GRACE_MIN:
        return None
    return {**entry["answer"], "held_from": entry["row_ts"]}


def held_today(entry: dict | None, now: datetime) -> dict | None:
    """The last fresh answer, stamped with its read (``held_from``), when it was given earlier the same day:
    a day constant holds whatever its age, but never one the question slept after (slept_since)."""
    age = _age_min(entry, now)
    if age is None or age < 0 or not isinstance(entry.get("answer"), dict) or entry["row_ts"][:10] != now.isoformat()[:10]:
        return None
    if slept_since(entry):
        return None
    return {**entry["answer"], "held_from": entry["row_ts"]}


def lost_today(entry: dict | None, now: datetime) -> dict | None:
    """The ask that got no answer (``lost``: its read and why) when it was made earlier the same day and no fresh
    answer has come since: the question is due again."""
    lost = (entry or {}).get("lost")
    if not isinstance(lost, dict) or not isinstance(lost.get("row_ts"), str) or lost["row_ts"][:10] != now.isoformat()[:10]:
        return None
    try:
        return lost if parse_ts(lost["row_ts"]) < now else None
    except ValueError:
        return None


def code_answer_moved(q: dict, entry: dict | None, code_answer: str | None, now: datetime) -> bool:
    """Whether a question held until the code's answer changes (its schedule's ``then``) is due again: its
    held answer was given earlier today, beside a code answer that is not this read's ``code_answer``."""
    if "then" not in (q.get("schedule") or {}) or not q.get("code_answer") or code_answer is None:
        return False
    was = entry.get("code_answer") if held_today(entry, now) else None
    return was is not None and was != code_answer


def scheduled_hold(q: dict, entry: dict | None, borrowed: dict | None, now: datetime, slot: str | None, minutes: int) -> dict | None:
    """What a live question its schedule does not ask at the read ``slot`` holds: a day constant its answer
    from earlier today, a question held from its other lane that lane's answer from today (``borrowed``,
    its last-asked entry) through the read its schedule names, and any other its last answer while young."""
    sched = q.get("schedule") or {}
    if holds_for_the_day(sched):
        return held_today(entry, now)
    if "hold_until" in sched:
        return held_today(borrowed, now) if slot is not None and slot <= sched["hold_until"] else None
    return held_answer(entry, now, minutes)


def past_its_reads(q: dict, slot: str | None) -> bool:
    """Whether the read ``slot`` comes after the last read a day constant is asked at."""
    sched = q.get("schedule") or {}
    return slot is not None and holds_for_the_day(sched) and slot > sched["at"][-1]


def hold_reason(q: dict, h: dict | None, slot: str) -> str | None:
    """The skip reason of a question its schedule leaves out at the read ``slot``, worded from what scheduled_hold
    found (``h``); None where the schedule's own reason stands (schedule.not_due)."""
    sched = q.get("schedule") or {}
    since = f"{parse_ts(h['held_from']):%H:%M} ET" if h else None
    if holds_for_the_day(sched) and h:
        return f"a day constant: asked at {since} and held"
    if "hold_until" in sched and slot <= sched["hold_until"]:
        return (f"asked on its other lane at {since} and held here until {sched['hold_until']} ET" if h
                else "asked on its other lane only, which has no answer from today to hold here")
    return None


def unheld(doc: dict, no_hold: set[str]) -> set[str]:
    """The questions reading a label the builder marked this read as one no earlier answer may stand in for
    (LabelSet.no_hold: a wall touch the siege box has not judged yet): they hold nothing at this read."""
    return {qid for g in doc["groups"] for qid, q in g["questions"].items() if no_hold & set(paths_in(q))}


def plan(doc: dict, last: dict, cad: dict, now: datetime, not_due: dict[str, str] | None = None, borrowed: dict | None = None,
         slot: str | None = None, learned: bool = True,
         code_answers: dict[str, str] | None = None, no_hold: set[str] | None = None) -> tuple[dict[str, str], dict[str, dict]]:
    """Which questions to leave out of this read, with the answer to hold for each live one. ``not_due``
    is what the schedule leaves out at the read ``slot`` (schedule.not_due, schedule.read_slot), each
    holding by scheduled_hold, unless the code's answer this read (``code_answers``) moved it
    (code_answer_moved); ``borrowed`` is the other lane's last-asked answers. A day constant past its
    reads with no answer from today to hold (its read was skipped, it slept or its label was missing) is
    asked at this read. A question held by its schedule says what it holds and since when (hold_reason).
    With ``learned`` a live question that is due is also left out while its cadence has not elapsed. A
    live question whose last ask today got no answer (lost_today) is asked at any read of the lane. A
    question in ``no_hold`` (unheld) holds nothing and is left to this read's labels, which say why it is
    not asked. Shadow questions are forecasts: never held, asked when due."""
    skip: dict[str, str] = dict(not_due or {})
    held: dict[str, dict] = {}
    for g in doc["groups"]:
        for qid, q in g["questions"].items():
            if q.get("status") != "live":
                continue
            minutes = cadence_of(cad, q, qid)
            if qid in (no_hold or ()):
                skip.pop(qid, None)            # its label says an earlier answer was about another moment: hold none
                continue
            if slot is not None and lost_today(last.get(qid), now):
                skip.pop(qid, None)            # its last ask got no answer: a failed ask is not an ask, so ask it again now
                continue
            if qid in skip and slot is not None and code_answer_moved(q, last.get(qid), (code_answers or {}).get(qid), now):
                del skip[qid]                  # the code's answer changed since the held one was given: ask it again now
                continue
            if qid in skip:
                h = scheduled_hold(q, last.get(qid), (borrowed or {}).get(qid), now, slot, minutes)
                if h is None and past_its_reads(q, slot):
                    del skip[qid]                  # nothing from today to hold: its ask never happened, so ask it now
                    continue
                if h is not None:
                    held[qid] = h
                if slot is not None and (why := hold_reason(q, h, slot)):
                    skip[qid] = why
                continue
            if not learned:
                continue
            entry = last.get(qid)
            if is_due(entry, now, minutes):
                continue
            if isinstance(entry, dict) and float(entry.get("moved") or 0.0) >= CHANGE_CUT:
                continue                       # in motion: its last two answers differed, ask again now
            h = held_answer(entry, now, minutes)
            if h is None:
                continue                       # nothing to hold: ask it after all
            skip[qid] = f"not due: cadence {minutes} min, last asked {parse_ts(h['held_from']):%H:%M} ET"
            held[qid] = h
    return skip, held


def fill_missing(doc: dict, skipped: dict, last: dict, cad: dict, now: datetime, held: dict,
                 no_hold: set[str] | None = None) -> dict[str, dict]:
    """A live question skipped for a missing label keeps its held answer, if one is young enough. One asleep
    because its label's condition is over (ask.build_requests, LabelSet.ended) holds nothing: the shock has
    passed, and an answer about it would outlive it. Nor does one in ``no_hold`` (unheld), whose label is
    missing only while what it describes is still under way."""
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    for gid, qs in skipped.items():
        for qid, why in qs.items():
            if qid == "*" or qid in held or qid in (no_hold or ()) or not str(why).startswith("missing"):
                continue
            q = by_id.get(qid)
            if not q or q.get("status") != "live":
                continue
            h = held_answer(last.get(qid), now, cadence_of(cad, q, qid))
            if h:
                held[qid] = h
    return held


def answer_series(records: list[dict], live: set[str]) -> dict[str, list[tuple[datetime, dict]]]:
    """One (time, vector) per scheduled read per question. Reads closer than MIN_GAP_MIN to the
    previous kept read are dropped, so a day of two-minute runs counts as its half-hour marks.
    A read on which the question was held repeats the last fresh vector: the answer stood."""
    out: dict[str, list] = {qid: [] for qid in live}
    last_kept: datetime | None = None
    last_vec: dict[str, dict] = {}
    for r in sorted(records, key=lambda r: r.get("row_ts", "")):
        try:
            t = parse_ts(r["row_ts"])
        except (KeyError, ValueError):
            continue
        fresh: dict[str, dict] = {}
        for a in (r.get("answers") or {}).values():
            for qid, ans in (a.get("answers") or {}).items():
                if qid in live and isinstance(ans, dict):
                    v = vector(ans)
                    if v:
                        fresh[qid] = v
        # a fresh answer is the newest one even on a read that is not kept, so a later held read
        # repeats what was actually said last, not what was said on the last kept read
        last_vec.update(fresh)
        if last_kept is not None and (t - last_kept).total_seconds() < MIN_GAP_MIN * 60:
            continue
        last_kept = t
        held = r.get("held") or {}
        for qid in live:
            if qid in fresh:
                out[qid].append((t, fresh[qid]))
            elif qid in held and qid in last_vec:
                out[qid].append((t, last_vec[qid]))
    return out


def recount(records: list[dict], doc: dict, previous: dict | None = None, day: str | None = None) -> dict:
    """A day's runs in, one cadence per live question out. See the module note for the rule."""
    previous = previous or {"questions": {}}
    live = {qid: q for g in doc["groups"] for qid, q in g["questions"].items() if q.get("status") == "live"}
    series = answer_series(records, set(live))
    out = {"recounted_from": day, "recounted_at": datetime.now().astimezone().isoformat(timespec="seconds"), "questions": {}}
    for qid, q in live.items():
        s = series.get(qid) or []
        prev = cadence_of(previous, q, qid)
        if len(s) < MIN_READS:
            out["questions"][qid] = {"minutes": prev, "p25_hold_min": None, "changes": None, "reads": len(s),
                                     "why": f"only {len(s)} reads, under {MIN_READS}; kept {prev}"}
            continue
        span = (s[-1][0] - s[0][0]).total_seconds() / 60.0
        holds, start, changes = [], s[0][0], 0
        for (t0, v0), (t1, v1) in zip(s, s[1:]):
            if distance({"probabilities": v0}, {"probabilities": v1}) >= CHANGE_CUT:
                holds.append((t1 - start).total_seconds() / 60.0); start = t1; changes += 1
        if not holds:
            # under three hours of reads says nothing about a question that never changed: keep what it had
            minutes = 120 if (span >= 300 and len(s) >= 10) else 60 if span >= 180 else prev
            why = f"never changed over {span:.0f} min and {len(s)} reads"
            p25 = None
        else:
            # the hold still running at the day's last read counts too: a question that flipped once
            # early and then stood all afternoon held for hours, not for the minutes before the flip
            holds.append((s[-1][0] - start).total_seconds() / 60.0)
            holds.sort()
            # under four holds a quartile is one hold picked at random: use the middle one instead
            p25 = holds[len(holds) // 4] if len(holds) >= 4 else holds[len(holds) // 2]
            minutes = snap(p25 / 2.0)
            at = "the 25th percentile" if len(holds) >= 4 else "the median"
            why = f"answer held {p25:.0f} min at {at} over {changes} moves of {CHANGE_CUT}+; half of that, snapped"
        out["questions"][qid] = {"minutes": minutes, "p25_hold_min": None if p25 is None else round(p25, 1),
                                 "changes": changes, "reads": len(s), "why": why}
    return out


def previous_day_with_records(out_dir: Path, today: str) -> str | None:
    days = sorted(p.stem for p in Path(out_dir).glob("20??-??-??.jsonl") if p.stem < today)
    return days[-1] if days else None


def ensure_cadence(out_dir: Path, doc: dict, today: str) -> dict:
    """The cadence to use today: recounted once from the newest earlier day with records."""
    cad = load_cadence(out_dir)
    src = previous_day_with_records(out_dir, today)
    if src is None or cad.get("recounted_from") == src:
        return cad
    new = recount(load_jsonl(Path(out_dir) / f"{src}.jsonl"), doc, cad, src)
    path = Path(out_dir) / CADENCE_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)               # never a half file for the next tick or the page builder
    return new


def main(argv: list[str] | None = None) -> int:
    from .ask import load_questions, retired
    ap = argparse.ArgumentParser(description="Recount each question's cadence from a day's runs.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--day", help="the day to recount; default the newest day with records")
    ap.add_argument("--write", action="store_true", help="write state/spx_jev/cadence.json")
    args = ap.parse_args(argv)
    out_dir = LIVE.folder(args.state_dir)
    doc = load_questions(LIVE.questions, LIVE.key)
    day = args.day or previous_day_with_records(out_dir, "9999-99-99")
    if not day:
        print("no day with records", file=sys.stderr)
        return 1
    # a question retired from the lane by that day has no cadence (ask.retired), as the service's own recount
    new = recount(load_jsonl(out_dir / f"{day}.jsonl"), retired(doc, day), load_cadence(out_dir), day)
    for qid, v in sorted(new["questions"].items(), key=lambda kv: (kv[1]["minutes"], kv[0])):
        print(f"{v['minutes']:4d} min  {qid:30s} {v['why']}")
    if args.write:
        (out_dir / CADENCE_NAME).write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {out_dir / CADENCE_NAME}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
