"""The premarket lane (lane.PREMARKET): a read at each checkpoint before the open, on a scene without a diary row.

    python3 -m spx_jev.premarket                                # the launchd job's run at a checkpoint
    python3 -m spx_jev.premarket --send                         # the key comes from .env
    python3 -m spx_jev.premarket --day 2026-09-25 --at 09:28    # a replay, into a scratch folder
    python3 -m spx_jev.premarket --day 2026-09-25 --at 10:06 --out-dir /tmp/trial   # that replay's close-out

The checkpoints are the lane's schedule in market time: 02:35 (Tokyo has closed), 03:35 (Europe's first
half hour: Frankfurt's open plus 35 minutes, so 04:35 in a week Frankfurt keeps winter time and New York
does not; checkpoints), 08:05, 08:48 (the report window has closed), 09:05 and 09:28. A fire runs only on
a market day, from EARLY_FIRE before one of the day's checkpoints to LATE_FIRE_MIN minutes after it and
before the open, or as near the 10:06 close-out (due); any other fire (a Mac that woke late, a weekend, a holiday) logs why
and writes nothing.

Every checkpoint is a read:

1. the night's overnight futures are saved into the store (overnight.save_nights), unless it is a replay;
2. the scene (make_premarket_scene): a synthetic row priced off /ES, the pre-open ruler, and the night's bars
   finished by the read; the label families that serve a read before the open build from it;
3. the questions due at the checkpoint (08:48 and 09:28 in the set) are asked when a key is present, a group that
   got no answer once more while the read has time before the open, and their answers summed into the call,
   open_average_30, the average price over the 30 minutes from the settled open (the close of the 09:34 bar), with
   the end-price questions open_10 and open_30 beside it, all measured from that price, never from yesterday's close;
4. the read, its sum, the archive and the card.

No answer JEV gave earlier, and no earlier record, reaches a label: every read rebuilds the night from the
bars. Nothing is held either: a sum stands on its own read's answers. A read whose pre-open ruler or prior
close cannot be formed writes its record with the reason, builds no labels and asks nothing.

    state/spx_jev/lanes/premarket/{day}.jsonl         one read per checkpoint
    state/spx_jev/lanes/premarket/hour/{day}.jsonl    one sum per read that asked JEV: what the grader grades
    state/spx_jev/lanes/premarket/latest.json         the card, shown from the day's first read until the tape lane's first
    state/spx_jev/lanes/premarket/grades.jsonl, weights.json   (grade.py; the lane keeps no learning loop, lane.PREMARKET.pool)
    state/spx_jev/archive/{day}.jsonl                 its ReadRecord, GradeRecord and CloseOutRecord lines, lane "premarket",
                                                      in the archive every lane shares

The close-out at 10:06 waits up to service.BAR_WAIT_S for the bar that finishes at 10:05, where its 30-minute check is
measured (last_check), grades
the morning's calls from the settled open (grade.run) and refreshes the card's calls and tally. Every time on the card is a full timestamp with its offset. A replay (--day with --at) saves
nothing, checks nothing against the wall clock, and writes into a fresh scratch folder unless --out-dir names one.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time as _clock
import traceback
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import archive, events, grade, overnight, rolls, service, story
from .ask import build_requests, get_path, load_questions, send, send_all
from .cuts import MIN_RANK_SESSIONS
from .expiry import calendar_of
from .hour import answer_sentences, average_request, average_window, hour_request, hour_summary, load_hour_doc, named_levels
from .labels.label_set import LabelSet
from .labels.registry import build_labels
from .labels.rulers import normal_day_sigma
from .lane import PREMARKET, TAPE
from .night_ranks import READ_STALE_MIN, price_by
from .schedule import EARLY_FIRE, asks_at, not_due
from .sessions import is_trading_day, session_close, session_open
from .state_builder import DEFAULT_STATE_DIR, Scene, load_jsonl, load_market_context, parse_ts, prior_sessions
from .weights import QuestionWeights

ET = overnight.ET
LATE_FIRE_MIN = 5                          # a fire this many minutes or more after its checkpoint reads nothing
EUROPE_CHECKPOINT = "03:35"                 # named in the lane's schedule at Frankfurt's usual six hours ahead (checkpoints)
# the job fires at both of the Europe checkpoint's market times, 03:35 and 04:35 (five hours ahead in the weeks one
# city keeps summer time and the other does not); the one that is not the day's checkpoint reads nothing
EUROPE_FIRES = ("03:35", "04:35")
CHECKPOINT_LAG = timedelta(minutes=5)       # a checkpoint follows the stretch it closes by five minutes, for its last bar to arrive
FUTURES = "/ES"
# A group that got no answer is asked once more when the read has time for it before the open: the round's own span
# (service.SEND_GROUPS_S), then the sums' (service.SEND_READ_S less that), must both end by the open. No later checkpoint
# asks the 09:28 read's questions, so a group lost there went unanswered all day (09-28).
ASK_AGAIN_MIN_S = 10.0                     # a second round shorter than one request's timeout (ask.send_all) is not tried
HORIZON = "10 and 30 minutes after the settled open, the index's price at the close of its 09:34 bar"
SUM_CONTEXT = {
    "horizon": ("10 minutes and 30 minutes after the settled open, the index's price at the close of its 09:34 bar; "
                "both are measured from that price, never from yesterday's close"),
    "units": ("sigma is the pre-open ruler: the median of the last sessions' morning expected move for the S&P 500 index. "
              "Each answer below was given by JEV about the night before the open, at this read"),
}
# the average-price request's own: one window, the primary's, from the settled open
AVERAGE_CONTEXT = {
    "horizon": (f"the {PREMARKET.horizons[PREMARKET.primary][0]} minutes after the settled open, the index's price at the close of its 09:34 bar; "
                "measured from that price, never from yesterday's close"),
    "units": SUM_CONTEXT["units"],
}
WHERE_NOW = "premarket.where_now"          # its figure's verdict is the story chip's lean
REPORT = "overnight.release_reaction"
# the facts the card draws: the key, the label, the title; the report row follows them on a day with one
SITUATION = (("futures", "overnight.es_move", "S&P futures"), ("bonds", "overnight.bond_gap", "Bonds"),
             ("bitcoin", "overnight.btc_vs_futures", "Bitcoin"))
VERDICT_WORDS = {"big_down": "Big fall", "down": "Down", "flat": "Quiet", "up": "Up", "big_up": "Big rise",
                 "overnight_ahead_up": "Leaning up", "overnight_ahead_down": "Leaning down", "in_line": "In line",
                 "btc_ahead_up": "Leaning up", "btc_ahead_down": "Leaning down",
                 "shrugged": "Shrugged", "extended": "Extended", "held": "Held", "faded": "Faded"}
CALL_KEYS = ("pick", "probabilities", "confidence", "primary", "by", "model", "blend", "shown_source", "average")
# what the premarket card keeps of the live card's make-up (service.card): the questions and the labels' count
LIVE_CARD_KEYS = ("version", "labels_count", "omitted", "questions", "fresh_count", "dark", "dark_note", "shadow_note")


class NoPreOpenRead(ValueError):
    """A read before the open cannot stand: the index has no prior close on file, or no pre-open ruler."""


def market_at(day: date, hhmm: str | time) -> datetime:
    """``hhmm`` market time on ``day``."""
    return datetime.combine(day, hhmm if isinstance(hhmm, time) else time.fromisoformat(hhmm), tzinfo=ET)


# ---- when it reads -------------------------------------------------------------------------------

def checkpoints(day: date) -> tuple[str, ...]:
    """The lane's checkpoints on ``day``, "HH:MM" market time: its schedule, with Europe's taken from
    Frankfurt's own clock (the europe_open stretch's end, story.py, plus CHECKPOINT_LAG)."""
    europe = datetime.combine(day, story.FRANKFURT_OPEN_PLUS_30, tzinfo=story.FRANKFURT).astimezone(ET) + CHECKPOINT_LAG
    return tuple(europe.strftime("%H:%M") if c == EUROPE_CHECKPOINT else c for c in PREMARKET.schedule)


def due(now: datetime) -> tuple[str | None, str]:
    """What a fire at ``now`` runs: ``(checkpoint, why)``, the checkpoint being the lane's close_out for the
    grade-only run, or ``(None, why)`` when it runs nothing. A checkpoint is read within LATE_FIRE_MIN
    minutes after it and never from the open on; the close-out within as long after it. A fire up to
    EARLY_FIRE before either is that run, so launchd firing a few seconds early never reads the 09:28
    checkpoint as a late 09:05 one. The job's fire at the Europe time that is not today's checkpoint
    (EUROPE_FIRES) says so, rather than passing for a late fire."""
    local = now.astimezone(ET)
    day = local.date()
    if not is_trading_day(day):
        return None, f"{day} is not a market day"
    passed = [c for c in (*checkpoints(day), PREMARKET.close_out) if market_at(day, c) <= local + EARLY_FIRE]
    if not passed:
        return None, f"{local:%H:%M} ET is before the day's first checkpoint"
    last = passed[-1]
    kind = "close-out" if last == PREMARKET.close_out else "checkpoint"
    if kind == "checkpoint" and local >= session_open(local):
        return None, f"{local:%H:%M} ET is after the {session_open(local):%H:%M} open: a premarket read comes before it"
    late = int((local - market_at(day, last)).total_seconds() // 60)
    europe = checkpoints(day)[PREMARKET.schedule.index(EUROPE_CHECKPOINT)]
    other = next((f for f in EUROPE_FIRES if f != europe and market_at(day, f) <= local + EARLY_FIRE
                  and local < market_at(day, f) + timedelta(minutes=LATE_FIRE_MIN)), None)
    if other is not None:
        return None, f"{local:%H:%M} ET is the {other} ET fire, not a checkpoint today: Europe's checkpoint is {europe} ET"
    if late >= LATE_FIRE_MIN:
        return None, f"{local:%H:%M} ET is {late} minutes after the {last} ET {kind}, past the {LATE_FIRE_MIN}-minute line: a late fire reads nothing"
    return last, f"the {last} ET {kind}"


def last_check(day: date) -> datetime:
    """When the day's last check is graded: the finish of the bar its longest horizon ends on, counted from the
    settled open (grade.settled_open_at), 10:05 ET. The bars job writes that bar up to a minute or so later, so the
    10:06 close-out waits for it (service.wait_for_bar) rather than leave the lane's main call ungraded for the day."""
    return grade.settled_open_at(day) + timedelta(minutes=max(m for m, _ in PREMARKET.horizons.values()))


def jev_reads(doc: dict, day: date) -> list[str]:
    """The day's checkpoints at which a question of ``doc`` (the lane's share) is due: where JEV is asked."""
    today = dict(zip(PREMARKET.schedule, checkpoints(day)))
    entries = [q.get("schedule") for g in doc["groups"] for q in g["questions"].values() if q.get("status") != "dark"]
    return [today[slot] for slot in PREMARKET.schedule
            if any(e is None or asks_at(e, slot, day, PREMARKET.read_times()) for e in entries)]


# ---- the scene -----------------------------------------------------------------------------------

def night_rows(state_dir: Path, day: str, now: datetime) -> list[dict]:
    """The overnight store's rows of the night into ``day`` (every symbol, both resolutions) finished by ``now``."""
    return [r for r in load_jsonl(overnight.night_path(state_dir, day))
            if parse_ts(r["ts"]) + timedelta(minutes=r["bar_minutes"]) <= now]


def prior_close_at(prior_day: tuple[str, list[dict]] | None) -> tuple[float, datetime]:
    """SPX's close on the trading day before, and when it was made: the close of the bar that finished at
    that day's close (16:00, 13:00 after a half day)."""
    if prior_day is None:
        raise NoPreOpenRead("the trading day before has no saved SPX bars, so there is no prior close")
    d, bars = prior_day
    close = session_close(market_at(date.fromisoformat(d), "12:00"))
    last = next((b for b in reversed(bars) if parse_ts(b["ts"]) + timedelta(minutes=1) == close), None)
    if last is None:
        raise NoPreOpenRead(f"SPX's bars of {d} have no closing bar, so there is no prior close")
    return float(last["close"]), close


def futures_spot(night: list[dict], table: dict, close_price: float, close_at: datetime, now: datetime) -> float | None:
    """SPX's prior close carried forward by /ES: ``close_price`` times /ES now over /ES at ``close_at``, on one
    contract. None when /ES has no bar finishing at the close, none within READ_STALE_MIN minutes of ``now``,
    or rolled between the two."""
    at_close, latest = price_by(night, FUTURES, close_at), price_by(night, FUTURES, now)
    if at_close is None or latest is None or at_close[1] != close_at or at_close[0] <= 0:
        return None
    if now - latest[1] > timedelta(minutes=READ_STALE_MIN) or not rolls.same_contract(table, FUTURES, at_close[1], latest[1]):
        return None
    return close_price * latest[0] / at_close[0]


def make_premarket_scene(state_dir: Path, now: datetime) -> Scene:
    """The moment ``now`` before the open: a synthetic row (``ts`` now, ``spot`` SPX's prior close times /ES now
    over /ES at that close, ``sigma`` the pre-open ruler in points, ``prior_close``), no bars today, the prior
    sessions' bars and rulers, and the night's overnight store rows finished by ``now`` (Scene.night).

    ``spot_from`` on the row says ``futures``, or ``prior_close`` when /ES could not carry it (futures_spot).
    Raises NoPreOpenRead, with the reason, when SPX's prior close or the pre-open ruler (rulers.normal_day_sigma,
    the median morning anchor of the prior sessions) cannot be formed."""
    state_dir = Path(state_dir)
    now = now.astimezone(ET).replace(microsecond=0)
    day = overnight.night_for(now).isoformat()
    prior = prior_sessions(state_dir, day)
    close_price, close_at = prior_close_at(prior["prior_day"])
    night = night_rows(state_dir, day, now)
    spot = futures_spot(night, rolls.load(state_dir / overnight.OVERNIGHT_SUBDIR), close_price, close_at, now)
    row = {"ts": now.isoformat(timespec="seconds"), "spot": round(spot if spot is not None else close_price, 2), "sigma": None,
           "prior_close": close_price, "spot_from": "futures" if spot is not None else "prior_close"}
    scene = Scene(row=row, rows_today=[row], bars=[], now=now, sigma=0.0, market=load_market_context(state_dir, day),
                  horizon=HORIZON, state_dir=state_dir, premarket=True, night=night, **prior)
    sigma = normal_day_sigma(scene)
    if sigma is None:
        raise NoPreOpenRead(f"no pre-open ruler: fewer than {MIN_RANK_SESSIONS} of the last sessions have a morning anchor on file")
    scene.sigma = row["sigma"] = sigma
    return scene


def pre_open_ruler(scene: Scene) -> dict:
    """The ruler stamped on every record of the read: its points and how many sessions' anchors it is the median of."""
    return {"kind": "pre_open", "points": round(scene.sigma, 2),
            "sessions": sum(1 for r in scene.prior_rulers.values() if r is not None and not r.estimated)}


def night_seen(rows: list[dict], now: datetime) -> dict[str, dict]:
    """Per symbol, the night's bars on file at each resolution and when the newest of them finished."""
    out = {}
    for symbol in sorted({r["symbol"] for r in rows}):
        newest = price_by(rows, symbol, now)
        out[symbol] = {**{str(m): sum(1 for r in rows if r["symbol"] == symbol and r["bar_minutes"] == m) for m in overnight.BAR_MINUTES},
                       "last": newest[1].isoformat() if newest else None}
    return out


def futures_prices(rows: list[dict], now: datetime) -> dict[str, dict]:
    """Every symbol's newest price in the night by ``now``, and when its bar finished: what the read saw."""
    return {symbol: {"value": p[0], "known_at": p[1].isoformat()}
            for symbol in sorted({r["symbol"] for r in rows}) if (p := price_by(rows, symbol, now))}


def save_the_night(state_dir: Path, now: datetime) -> dict:
    """The night in progress saved into the overnight store up to ``now`` (overnight.save_nights): the bars it
    added and the calls that failed. The read goes on with what is on disk whatever the save did."""
    try:
        lines, failed = overnight.save_nights(state_dir, [overnight.night_for(now)], now)
    except Exception as e:  # a failed save must never cost the read
        return {"added": 0, "failed": [f"{type(e).__name__}: {e}"]}
    return {"added": sum(line["added"] for line in lines), "failed": sorted(set(failed))}


# ---- the read ------------------------------------------------------------------------------------

def average_window_of(ruler: dict, spot: float | None = None) -> dict | str:
    """The window the average-price sum forecasts, the primary's from the settled open (hour.average_window), its flat
    band in the pre-open ruler stamped on the record as the grader reads it (grade.stamped_ruler), with the read's
    spot on S&P futures; why there is none when the read has no ruler."""
    minutes, flat = PREMARKET.horizons[PREMARKET.primary]
    points = ruler.get("points")
    if not isinstance(points, (int, float)) or points <= 0:
        return "no pre-open ruler to price its edge in points this read"
    return average_window(minutes, flat * float(points), spot)


def sum_the_read(doc: dict, fresh: dict[str, dict], missing: list[str], out_dir: Path,
                 deadline: float | None = None, window: dict | str | None = None) -> tuple[dict, dict | None, dict | None, dict | None]:
    """Steps 3 and 4 before the open: the read's answers as sentences, the two end-price sums over them from the
    settled open (SUM_CONTEXT says so in the request) and the average-price sum over ``window`` in its own
    (AVERAGE_CONTEXT), both sent at once and retried until ``deadline`` (ask.send). Returns what
    service.sum_the_hour returns: the hour record, JEV's summary, and its two replies untouched."""
    sentences, left_out = answer_sentences(doc, fresh, QuestionWeights.load(out_dir))
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    base = {"used": {qid: fresh[qid]["pick"] for qid in sentences},
            "fresh": {qid: a["pick"] for qid, a in fresh.items() if by_id.get(qid, {}).get("status") == "live" and a.get("pick") is not None},
            "left_out": left_out, "missing": sorted(missing), "sentences": sentences}
    if not sentences:
        return {**base, "request": None}, None, None, None
    hour_doc = load_hour_doc(lane=PREMARKET)
    req = hour_request(sentences, hour_doc, context=SUM_CONTEXT, lane=PREMARKET)
    avg_req = average_request(sentences, window, hour_doc, context=AVERAGE_CONTEXT, lane=PREMARKET) if isinstance(window, dict) else None
    replies = service.send_sums([req, avg_req], send, deadline)
    reply, avg_reply = replies[req["id"]], replies.get("average")
    return ({**base, "request": req, "average_request": avg_req},
            service.with_average(hour_summary(reply, PREMARKET), PREMARKET, window, avg_reply), reply, avg_reply)


def ask_again(lost: list[dict], answers: dict[str, dict], sums_by: float, open_at: float) -> tuple[dict[str, dict], float]:
    """The groups that got no answer (``lost``) asked once more, when a round of service.SEND_GROUPS_S and the sums'
    time after it still end by ``open_at`` (a time.monotonic() reading, as the deadlines are). Returns the answers with
    those the round got in place of their errors, and the sums' deadline: after this round, or ``sums_by`` as it was
    when there was no time to ask again."""
    sums_span = service.SEND_READ_S - service.SEND_GROUPS_S
    start = _clock.monotonic()
    until = min(start + service.SEND_GROUPS_S, open_at - sums_span)
    if until - start < ASK_AGAIN_MIN_S:
        return answers, sums_by
    service.log(f"asked again before the open, their first ask got no answer: {', '.join(r['id'] for r in lost)}")
    again = send_all(lost, deadline=until)
    out = {**answers, **{rid: a for rid, a in again.items() if not (a or {}).get("error")}}
    return out, max(sums_by, _clock.monotonic() + sums_span)


def run_checkpoint(state_dir: Path, out_dir: Path, doc: dict, do_send: bool, now: datetime, checkpoint: str,
                   save: bool = True, unsent_reason: str = service.UNSENT_DEFAULT) -> dict:
    """One read at ``checkpoint`` (see the module note), written to ``out_dir``; returns the card. A group that got no
    answer is asked once more while the read has time before the open (ask_again)."""
    started = _clock.monotonic()
    now = now.astimezone(ET).replace(microsecond=0)
    day, row_ts = now.date().isoformat(), now.isoformat(timespec="seconds")
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = save_the_night(state_dir, now) if save else None
    try:
        event = events.tag(now)
    except Exception as e:  # a broken calendar must never cost the read
        service.log(f"the event calendar could not be read this run: {type(e).__name__}: {e}")
        event = None
    try:
        scene = make_premarket_scene(state_dir, now)
        ruler = pre_open_ruler(scene)
        skip = not_due(doc, PREMARKET, now)
    except NoPreOpenRead as e:
        service.log(f"premarket {checkpoint}: no read: {e}")
        scene, ruler = None, {"omitted": str(e)}
        skip = {qid: f"no read before the open: {e}" for g in doc["groups"] for qid in g["questions"]}
    labels = build_labels(scene) if scene else LabelSet()
    requests, skipped = build_requests(labels.state, doc, skip=skip, gates=labels.gates, ended=labels.ended_reasons())
    by_id = {qid: q for g in doc["groups"] for qid, q in g["questions"].items()}
    answers = send_seconds = hour = hour_rec = hour_reply = average_reply = None
    if do_send and requests:
        t0 = _clock.monotonic()
        answers = send_all(requests, deadline=t0 + service.SEND_GROUPS_S)
        sums_by = t0 + service.SEND_READ_S
        if lost := [r for r in requests if (answers.get(r["id"]) or {}).get("error")]:
            answers, sums_by = ask_again(lost, answers, sums_by, started + (session_open(now) - now).total_seconds())
        for r in requests:
            if err := (answers.get(r["id"]) or {}).get("error"):
                service.log(f"group {r['id']} got no answer: {err}")
        fresh = {qid: service.answer_entry(named_levels(by_id.get(qid, {}), ans))
                 for a in answers.values() for qid, ans in (a.get("answers") or {}).items()}
        # live questions due now whose label the builder could not measure
        missing = [qid for g in skipped.values() for qid, why in g.items()
                   if by_id.get(qid, {}).get("status") == "live" and str(why).startswith("missing")]
        hour_rec, hour, hour_reply, average_reply = sum_the_read(doc, fresh, missing, out_dir, sums_by, average_window_of(ruler, scene.spot if scene else None))
        send_seconds = round(_clock.monotonic() - t0, 3)
        if hour is not None:
            hour = {**hour, "used": len(hour_rec["used"]), "left_out": len(hour_rec["left_out"]), "missing": len(missing)}
            if hour.get("error"):
                service.log(f"the end-price sums got no answer: {hour['error']}")
            if (hour.get("average") or {}).get("error"):
                service.log(f"the average-price sum got no answer: {hour['average']['error']}")
            hour = service.plain_errors(hour)
    sent = answers is not None
    if not sent and do_send:
        unsent_reason = f"nothing to ask at the {checkpoint} ET read" + ("" if scene else f": {ruler['omitted']}")
    rows = scene.night if scene else night_rows(state_dir, day, now)
    night = {"file": f"overnight/{day}.jsonl", "saved": saved, "seen": night_seen(rows, now)}
    record = {"row_ts": row_ts, "checkpoint": checkpoint, "lane": PREMARKET.tag, "spot": scene.spot if scene else None,
              "sigma": scene.sigma if scene else None, "ruler": ruler, "event": event, "state": labels.state, "omitted": labels.omitted,
              "figures": labels.figures, "requests": requests, "skipped": skipped, "answers": answers, "sent": sent,
              "send_seconds": send_seconds, "hour": hour, "night": night}
    with open(out_dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if hour_rec and hour_rec.get("request"):
        # the sum record is what the grader grades, from the settled open in the stamped ruler; the learning
        # loop's event hold-out is judged over the windows the sums forecast, which start at the settled open
        learn = {"learn_exclude": events.learn_exclude(grade.settled_open_at(now.date()), tuple(m for m, _ in PREMARKET.horizons.values()))}
        (out_dir / "hour").mkdir(parents=True, exist_ok=True)
        with open(out_dir / "hour" / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"row_ts": row_ts, "spot": record["spot"], "sigma": record["sigma"], "event": event, **learn,
                                "lane": PREMARKET.tag, "ruler": ruler, **(hour or {}), **hour_rec}, ensure_ascii=False) + "\n")
    archive.append(PREMARKET.archive_folder(state_dir, out_dir), day, archive.ReadRecord(
        read_id=archive.read_id(PREMARKET.name, row_ts), lane=PREMARKET.name, row_ts=row_ts, sent=sent, spot=record["spot"],
        sigma=record["sigma"], labels=labels.state, omitted=labels.omitted, requests=requests, skipped=skipped, responses=answers,
        hour_request=(hour_rec or {}).get("request"), hour_response=hour_reply, hour=hour, pool=(hour_rec or {}).get("pool"),
        average_request=(hour_rec or {}).get("average_request"), average_response=average_reply,
        cadence={"from": None, "held": {}, "not_due": skip, "asked": [qid for r in requests for qid in r["questions"]],
                 "reasked": {}},
        market_context=futures_prices(rows, now), event=event, ruler=ruler, checkpoint=checkpoint, night=night))
    if sent:
        try:
            grade.run(state_dir, out_dir, grade.live_options(doc), lane=PREMARKET)
        except Exception as e:  # grading must never stop the card
            service.log(f"grading skipped this run: {type(e).__name__}: {e}\n{traceback.format_exc()}")
    c = card(scene, record, doc, out_dir, unsent_reason)
    service.write_card(out_dir, c)
    return c


# ---- the card ------------------------------------------------------------------------------------

def newest_call(out_dir: Path, day: str) -> dict | None:
    """The day's newest sum that made a call, as the card shows it, with ``read_at`` the read it came from: one whose
    end-price sums answered, or whose average-price sum answered alone (grade.average_alone)."""
    for rec in reversed(load_jsonl(out_dir / "hour" / f"{day}.jsonl")):
        if (rec.get("pick") and isinstance(rec.get("by"), dict)) or grade.average_alone(rec, PREMARKET):
            return {**{k: rec[k] for k in CALL_KEYS if k in rec}, "read_at": rec["row_ts"]}
    return None


def story_so_far(out_dir: Path, day: str) -> list[dict]:
    """One chip per checkpoint read today, oldest first (a checkpoint read twice shows its newest read): where
    futures stood against their 16:00 price (premarket.where_now's figure), whether the read is the first on
    file at or after the day's first report (a missed checkpoint passes the mark to the next read), and its
    call when it made one (service.call_of: the average-price sum's when JEV answered it). For the phone only: no
    label ever reads it."""
    calls = {r["row_ts"]: {k: v for k, v in service.call_of(r, PREMARKET).items() if k != "sum"}
             for r in load_jsonl(out_dir / "hour" / f"{day}.jsonl") if r.get("pick") or grade.average_alone(r, PREMARKET)}
    reads = sorted({rec.get("checkpoint"): rec for rec in load_jsonl(out_dir / f"{day}.jsonl")}.values(), key=lambda r: r["row_ts"])
    released = story.releases(date.fromisoformat(day))
    first_after = next((rec["row_ts"] for rec in reads if released and parse_ts(rec["row_ts"]) >= released[0].start), None)
    out = []
    for rec in reads:
        fig = (rec.get("figures") or {}).get(WHERE_NOW) or {}
        out.append({"at": rec["row_ts"], "checkpoint": rec.get("checkpoint"), "lean": fig.get("verdict"), "net_sigma": fig.get("value"),
                    "band": fig.get("cut"), "report": rec["row_ts"] == first_after, "call": calls.get(rec["row_ts"])})
    return out


def freshness(scene: Scene | None, record: dict) -> dict:
    """How old the night's /ES was at the read and what the spot stood on, as the live card's freshness says
    how old its row was: stale when /ES has no bar within READ_STALE_MIN minutes of the read, or the spot fell
    back to SPX's prior close (futures_spot). The note names a failed save of the night."""
    read_at = parse_ts(record["row_ts"])
    last = (record["night"]["seen"].get(FUTURES) or {}).get("last")
    age_s = round((read_at - parse_ts(last)).total_seconds()) if last else None
    spot_from = scene.row["spot_from"] if scene else None
    failed = (record["night"]["saved"] or {}).get("failed") or []
    if age_s is None:
        note = f"no {FUTURES} bar on file by the read"
    elif age_s > READ_STALE_MIN * 60:
        note = f"{FUTURES}'s newest bar is {age_s // 60} minutes old at the read"
    elif spot_from == "prior_close":
        note = f"the spot is SPX's prior close: {FUTURES} has no bar at the close or rolled since"
    else:
        note = f"built on {FUTURES} {age_s // 60} minutes old"
    stale = age_s is None or age_s > READ_STALE_MIN * 60 or spot_from == "prior_close"
    if stale and failed:
        note += f"; the night's save failed: {', '.join(failed)}"
    return {"age_s": age_s, "stale": stale, "spot_from": spot_from, "note": note}


def _fact(record: dict, path: str) -> dict | None:
    """A label as a fact row's body: its verdict in a word, its sentence in plain words, its figure to draw."""
    label = get_path(record["state"], path)
    if not label:
        return None
    fig = dict(record["figures"].get(path) or {})
    verdict = fig.pop("verdict", None)
    return {"verdict": VERDICT_WORDS.get(verdict), "sentence": service.plain(label), **({"figure": fig} if fig else {})}


def situation(record: dict, now: datetime) -> list[dict]:
    """The night's facts as the phone draws them: futures, bonds and bitcoin when their labels were written;
    then, on a report day, the report: its reaction once measured, "Due" before it is out, else why it is
    not measured. The report row names its releases and carries their time (``at``)."""
    rows = [{"key": key, "path": path, "title": title, **fact} for key, path, title in SITUATION if (fact := _fact(record, path))]
    out = story.releases(now.date())
    if not out:
        return rows
    at, name = out[0].start, " and ".join(dict.fromkeys(e.words for e in out))
    head = {"key": "report", "path": REPORT, "title": "Report", "name": name, "at": at.isoformat()}
    if fact := _fact(record, REPORT):
        rows.append({**head, **fact})
    elif now < at:
        rows.append({**head, "verdict": "Due", "sentence": f"{name} is due at {at:%H:%M} ET"})
    else:
        why = record["omitted"].get(REPORT) or "its reaction was not measured this read"
        rows.append({**head, "verdict": None, "sentence": f"{name} came out at {at:%H:%M} ET; {service.plain(why)}"})
    return rows


def card(scene: Scene | None, record: dict, doc: dict, out_dir: Path, unsent_reason: str) -> dict:
    """The phone's before-the-open card (the module note): the read, the day's clock, the newest call with the
    read it came from (and ``hour_error`` when this read's own sum failed), the story so far, the facts, the questions as the live card lists them, and the day's
    calls. Every time is a full timestamp. A read without a scene (NoPreOpenRead) carries its reason in ``ruler``."""
    now = datetime.now(timezone.utc)
    read_at = parse_ts(record["row_ts"])
    day = read_at.date()
    # the live card lists the questions reading only the read's time off its scene
    basis = scene or Scene(row={"ts": record["row_ts"]}, rows_today=[], bars=[], prior_bars={}, now=read_at, sigma=0.0, premarket=True)
    live = service.card(basis, record["state"], record["omitted"], doc, record["requests"], record["skipped"], record["answers"],
                        record["sent"], record["send_seconds"], unsent_reason=unsent_reason, lane=PREMARKET)
    start = grade.settled_open_at(day)
    call = newest_call(out_dir, day.isoformat())
    return {
        **{k: live[k] for k in LIVE_CARD_KEYS},
        "symbol": "SPX",
        "lane": PREMARKET.tag,
        "day": day.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "row_ts": record["row_ts"],
        "checkpoint": market_at(day, record["checkpoint"]).isoformat(),
        "freshness": freshness(scene, record),
        "sent": record["sent"],
        **({} if record["sent"] else {"unsent_reason": unsent_reason}),
        "model": live["model"] or (call or {}).get("model"),
        "ruler": record["ruler"],
        "spot": record["spot"],
        # the day's clock: the open, the settled open both sums start from (the close of its 09:34 bar, 09:35), the
        # instants the grader checks them at (grade.mark_at), and the tape lane's first read, where the phone hands over
        "open": session_open(read_at).isoformat(),
        "start": start.isoformat(),
        "marks": [(start + timedelta(minutes=m)).isoformat() for m, _ in PREMARKET.horizons.values()],
        "handover": market_at(day, TAPE.schedule[0]).isoformat(),
        "schedule": {"reads": [market_at(day, c).isoformat() for c in checkpoints(day)],
                     "jev_reads": [market_at(day, c).isoformat() for c in jev_reads(doc, day)],
                     "close_out": market_at(day, PREMARKET.close_out).isoformat()},
        "hour": call,
        # a sum that failed at this read: the card still carries the newest call, so it says this read's was lost
        **({"hour_error": {"read_at": record["row_ts"], "error": record["hour"]["error"]}}
           if record["sent"] and (record["hour"] or {}).get("error") else {}),
        "story": story_so_far(out_dir, day.isoformat()),
        "situation": situation(record, read_at),
        "event": record["event"],
        "expiries": calendar_of(read_at),
        **service.calls_block(service.day_calls(out_dir, day.isoformat(), PREMARKET)),
    }


def already_read(out_dir: Path, day: str, checkpoint: str) -> bool:
    """Whether the lane's day file holds a read at ``checkpoint``: a second fire there would ask JEV twice."""
    return any(r.get("checkpoint") == checkpoint for r in load_jsonl(out_dir / f"{day}.jsonl"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The SPX JEV premarket lane: one read at a checkpoint before the open, or its close-out.")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out-dir", default=None, help="default <state-dir>/spx_jev/lanes/premarket; a replay defaults to a fresh scratch folder")
    ap.add_argument("--questions", default=None, help="default the lane's question doc")
    ap.add_argument("--send", action="store_true", help="post to JEV; the key comes from .env or TYPESAFE_API_KEY")
    ap.add_argument("--day", help="replay this session day, YYYY-MM-DD, with --at")
    ap.add_argument("--at", type=time.fromisoformat, help="the replay's ET clock HH:MM: a checkpoint, or the close-out")
    args = ap.parse_args(argv)
    if bool(args.day) != bool(args.at):
        ap.error("--day and --at go together: a replay names its moment")
    service.load_env_file()
    do_send, unsent = bool(args.send), service.UNSENT_DEFAULT
    if do_send and not os.environ.get("TYPESAFE_API_KEY"):
        # the job always asks to send; without a key the run still writes the card, unsent
        service.log(f"no TYPESAFE_API_KEY in the environment or in {service.ENV_FILE}: running unsent")
        do_send, unsent = False, "not sent: no key on this machine"
    state_dir = Path(args.state_dir)
    now = market_at(date.fromisoformat(args.day), args.at) if args.day else service.now_et()
    checkpoint, why = due(now)
    if checkpoint is None:
        service.log(f"premarket: {why}; nothing read")
        return 0
    if args.day and not args.out_dir:
        # a replay writes records, sums and the archive like a live read: never into the station's own folders
        args.out_dir = tempfile.mkdtemp(prefix=f"spx-jev-replay-{args.day}-premarket-")
        service.log(f"replay of {args.day} {args.at:%H:%M}: writing under {args.out_dir}, not the station's records; --out-dir chooses the folder")
    out_dir = PREMARKET.folder(state_dir, args.out_dir)
    doc = load_questions(args.questions or PREMARKET.questions, PREMARKET.key)
    day = now.astimezone(ET).date().isoformat()
    try:
        if checkpoint == PREMARKET.close_out:
            last = last_check(now.astimezone(ET).date())
            if not args.day and not service.wait_for_bar(state_dir, last):
                service.log(f"the bar finishing at {last:%H:%M} is not on file after {service.BAR_WAIT_S} s: grading what is")
            c = service.close_out(state_dir, out_dir, doc, PREMARKET, day)
            service.log(f"premarket lane closed out: {service.tally_words(c['tally'])}" if c else "premarket lane: nothing to close out today")
            return 0
        if not args.day and already_read(out_dir, day, checkpoint):
            service.log(f"premarket: the {checkpoint} ET checkpoint was already read today; card unchanged")
            return 0
        c = run_checkpoint(state_dir, out_dir, doc, do_send, now, checkpoint, save=not args.day, unsent_reason=unsent)
    except Exception as e:  # one bad read is a logged failure; the next checkpoint reads afresh
        service.log(f"premarket run failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return 1
    answered = sum(1 for q in c["questions"] if q.get("answer"))
    service.log(f"premarket {checkpoint}: labels {c['labels_count']} answered {answered}/{len(c['questions'])} "
                f"{'sent' if c['sent'] else 'not sent'} -> {out_dir / 'latest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
