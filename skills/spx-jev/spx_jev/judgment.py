"""The judgment group: the six questions of the merged set that ask JEV for a judgment, each behind a gate the code decides.

A judgment question is asked only when the moment calls for one: a push partly given back, a compressed range, a
heavyweight in play, SPX far from what bonds imply, the ten-year yield moving more than usual, material news on the
tape. Each gate is read off the code feature builder's answers for the read (mirai_prediction/code_features.py, the
same answers the Mirai Prediction System's matrix holds as its layers 1 and 2), the headline feed (headlines.py) and
the ten-year yield's move since the prior close ranked against the same minute on the prior sessions. A gate whose
code answer is None is not fired: nothing the code could not measure wakes a question. A question whose gate is not
fired is skipped with the reason ``gate: <code question> not fired`` and costs the read nothing (ask.build_requests).

The group's labels, written under ``judgment.*``, are the code answers each ask names, in words, and one label of
the headlines captured in the HEADLINE_WINDOW_MIN minutes ending HEADLINE_CUT_MIN minutes before the read
(headlines.headlines_before), so JEV reads only what the station could have read before the read.

The questions are ``shadow`` in the set: asked and logged, never summed, never weighted, never held, and never a
member of the old learning loop (hour.answer_sentences, grade.live_options, pool_snapshots and the cadence all keep to
``live`` questions). The learning store files their answers as ``answered`` all the same, so the Mirai Prediction
System's matrix takes them as its layer-3 columns ``jev:<question id>`` (answer_matrix.load_jev_answers).

Since 2026-10-07 the service answers the code questions before it builds the read's requests (service.run_once), so
the gates can read them; the live read records them under raw/code_features/ first and the forecast after the read
reuses the stored line by read_id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import daily_closes
from .cuts import HEADLINE_WINDOW_MIN, THIRD_HI
from .headlines import MAX_TITLES, headlines_before
from .labels.label_set import LabelSet
from .labels.measures import ET
from .labels.ranks import SameClockRank, rank_sessions
from .state_builder import CONTEXT_SUBDIR, Scene, load_jsonl, parse_ts

from .ask import GATED            # a gate's reason starts with this; ask.build_requests keeps it as the skipped reason
TNX = "$TNX"
NOT_MEASURED = "not measured this read"


@dataclass(frozen=True)
class YieldMove:
    """The ten-year yield's move since the prior close at the read's minute, ranked against the same minute on the
    prior sessions (daily_closes for each session's prior close, context/bars for its reading at the minute)."""
    basis_points: float | None
    rank: SameClockRank | None
    why: str | None                                  # why there is no rank

    @property
    def fired(self) -> bool:
        return self.rank is not None and self.rank.share >= THIRD_HI

    def words(self) -> str:
        if self.basis_points is None:
            return f"the ten-year yield's move since the prior close is {NOT_MEASURED} ({self.why})"
        way = "rose" if self.basis_points > 0 else "fell" if self.basis_points < 0 else "is unchanged"
        moved = f"the ten-year yield {way} {abs(self.basis_points):.1f} basis points since the prior close" if self.basis_points else \
            "the ten-year yield is unchanged since the prior close"
        if self.rank is None:
            return f"{moved}, not ranked ({self.why})"
        return f"{moved}, {self.rank.words()}, {self.rank.band}"


@dataclass
class Facts:
    """What the gates and labels are decided from."""
    code: dict[str, str | None] = field(default_factory=dict)      # the code feature builder's answers for the read
    headlines: list[dict] = field(default_factory=list)            # headlines_before's lines, newest first
    yield_move: YieldMove | None = None
    headlines_why: str | None = None                               # why there are no headlines, when the feed could not be read


@dataclass(frozen=True)
class Gate:
    reads: tuple[str, ...]                           # the code questions (or feeds) it is decided from, named in its reason
    fired: Callable[[Facts], bool]


def _code(facts: Facts, qid: str) -> str | None:
    return facts.code.get(qid)


def _push(facts: Facts) -> bool:
    return any(_code(facts, q) not in (None, "none") for q in ("TREND-10", "TREND-11"))


def _in_play(facts: Facts) -> bool:
    return str(_code(facts, "BREADTH-09") or "").startswith("in play")


def _yield_moved(facts: Facts) -> bool:
    # off while a release is in its reaction window (EVENTS-03; EVENTS-05, the rates surprise, left the set on 2026-10-07):
    # the release names the cause
    return (facts.yield_move is not None and facts.yield_move.fired
            and _code(facts, "EVENTS-03") in (None, "no release"))


# the judgment questions of spec/question_set.json by id, each with the gate it is asked behind. push_blowoff_or_fresh and
# quiet_coiled_or_resting are dark since the question pressure test of 2026-10-07 (gated_questions passes them over; the
# second lives on as the code question VOLATILITY-18); the set keeps them, dated, so their gates and labels stay.
GATES: dict[str, Gate] = {
    "push_blowoff_or_fresh": Gate(("TREND-10", "TREND-11"), _push),
    "quiet_coiled_or_resting": Gate(("VOLATILITY-04",), lambda f: _code(f, "VOLATILITY-04") == "compressed"),
    "heavyweight_catalyst_or_flow": Gate(("BREADTH-09",), _in_play),
    "macro_gap_equity_reason": Gate(("MACRO-02",), lambda f: _code(f, "MACRO-02") in ("far up", "up", "down", "far down")),
    "yield_move_meaning": Gate((TNX, "EVENTS-03"), _yield_moved),
    "news_reaction": Gate(("headlines",), lambda f: bool(f.headlines)),
}
LABELS = ("judgment.push_exhaustion", "judgment.quiet", "judgment.heavyweight", "judgment.macro_gap", "judgment.yield_move",
          "judgment.headlines")


def gated_questions(doc: dict) -> dict[str, dict]:
    """The doc's questions that stand behind a gate here, by id."""
    return {qid: q for g in doc["groups"] for qid, q in g["questions"].items() if qid in GATES and q.get("status") != "dark"}


def _not_fired(gate: Gate, detail: str) -> str:
    return f"{GATED} {' or '.join(gate.reads)} not fired: {detail}"


def _why_not(gate: Gate, facts: Facts) -> str:
    """What the gate saw: each code answer it reads (None as not measured), the headline feed's state, the yield's words."""
    parts = []
    for what in gate.reads:
        if what == "headlines":
            parts.append(facts.headlines_why or f"no headlines captured in the {HEADLINE_WINDOW_MIN} minutes before the read")
        elif what == TNX:
            parts.append(facts.yield_move.words() if facts.yield_move else NOT_MEASURED)
        else:
            a = _code(facts, what)
            parts.append(f"{what} {a!r}" if a is not None else f"{what} {NOT_MEASURED}")
    return ", ".join(parts)


def verdicts(qids, facts: Facts) -> dict[str, str | None]:
    """Each question's gate verdict as ask.build_requests reads it: None when fired (asked), else why it is not."""
    out: dict[str, str | None] = {}
    for qid in qids:
        gate = GATES[qid]
        try:
            fired = gate.fired(facts)
        except Exception as e:  # a gate that cannot be decided is not fired: nothing unmeasured wakes a question
            out[qid] = _not_fired(gate, f"the gate failed ({type(e).__name__})")
            continue
        out[qid] = None if fired else _not_fired(gate, _why_not(gate, facts))
    return out


# ---------------------------------------------------------------- the facts

def read_record_for_code(lane_name: str, scene: Scene, labels: LabelSet, event: dict | None) -> dict:
    """The read as the code feature builder reads it, before the requests exist: what archive.ReadRecord will carry of it."""
    from .archive import read_id
    now = parse_ts(scene.row["ts"])
    return {"read_id": read_id(lane_name, scene.row["ts"]), "lane": lane_name, "row_ts": scene.row["ts"], "spot": float(scene.row["spot"]),
            "sigma": scene.sigma, "labels": labels.state, "omitted": labels.omitted,
            "market_context": scene.market.at(now) if scene.market else None, "event": event}


def code_answers_for(state_dir: Path | str, lane_name: str, day: str, read_record: dict, record: bool) -> dict[str, str | None]:
    """The code feature builder's answers for the read. A live read records them under raw/code_features/ first
    (code_feature_inputs.record_code_features, reused by read_id when the read is forecast); a replay or an unsent run
    computes them and writes nothing."""
    from .mirai_prediction.code_feature_inputs import load_day_bars, load_market_history, record_code_features
    from .mirai_prediction.code_features import answer_code_features, bars_up_to
    from .mirai_prediction.paths import data_root, ensure_folders
    if record:
        return record_code_features(ensure_folders(data_root(state_dir)), state_dir, lane_name, day, read_record, "live")
    bars = bars_up_to(load_day_bars(state_dir, day), read_record["row_ts"])
    return answer_code_features(read_record, bars, load_market_history(state_dir, lane_name, day))


def _tnx_quote_at(state_dir: Path | str, day: str, now: datetime) -> tuple[float, float] | None:
    """The newest $TNX quote taken at or before ``now`` from the day's live snapshots: (last, prior close), as served."""
    found = None
    for line in load_jsonl(Path(state_dir) / CONTEXT_SUBDIR / f"{day}.jsonl"):
        q = (line.get("quotes") or {}).get(TNX)
        if not isinstance(q, dict) or not isinstance(line.get("ts"), str):
            continue
        try:
            t = parse_ts(line["ts"])
        except ValueError:
            continue
        if t <= now and isinstance(q.get("last"), (int, float)) and isinstance(q.get("close"), (int, float)) and q["last"] and q["close"]:
            found = (float(q["last"]), float(q["close"]))
    return found


def _tnx_at_minute(state_dir: Path | str, day: str, hhmm: str) -> float | None:
    """A saved session's $TNX close at the last bar finished by ``hhmm`` (context/bars/{day}.jsonl), as served."""
    last = None
    for line in load_jsonl(Path(state_dir) / CONTEXT_SUBDIR / "bars" / f"{day}.jsonl"):
        bar = (line.get("bars") or {}).get(TNX)
        if isinstance(line.get("ts"), str) and line["ts"][11:16] <= hhmm and isinstance(bar, dict) and isinstance(bar.get("close"), (int, float)):
            last = float(bar["close"])
    return last


def yield_move(state_dir: Path | str, day: str, row_ts: str) -> YieldMove:
    """The ten-year yield's move since the prior close at the read, ranked against the same minute on up to the last
    NIGHT_RANK_COUNT saved sessions (each from its own prior close, daily_closes), needing SAME_CLOCK_MIN_SESSIONS.
    $TNX is served at ten times the yield, so a difference of 0.1 is one basis point."""
    now = parse_ts(row_ts)
    quote = _tnx_quote_at(state_dir, day, now)
    if quote is None:
        return YieldMove(None, None, f"no {TNX} quote in today's market feed by {now.astimezone(ET):%H:%M} ET")
    last, prior_close = quote
    closes = daily_closes.load(Path(state_dir), TNX, before=day)
    prior_close_of = {}
    for earlier, session in zip(closes, closes[1:]):
        if isinstance(earlier.get("close"), (int, float)):
            prior_close_of[session["day"]] = float(earlier["close"])
    hhmm = now.astimezone(ET).strftime("%H:%M")
    base = []
    for session in sorted(prior_close_of, reverse=True):
        at_minute = _tnx_at_minute(state_dir, session, hhmm)
        if at_minute is not None:
            base.append(abs(at_minute - prior_close_of[session]))
    rank, why = rank_sessions(abs(last - prior_close), base, f"a {TNX} reading at this minute and a prior close")
    return YieldMove(round((last - prior_close) * 10.0, 1), rank, why)


def facts_for(state_dir: Path | str, lane_name: str, day: str, read_record: dict, record: bool) -> Facts:
    """Every fact the gates read, each part on its own: a part that fails is left empty with the reason, never a raise."""
    facts = Facts()
    try:
        facts.code = code_answers_for(state_dir, lane_name, day, read_record, record)
    except Exception as e:  # each gate then reads its code answers as not measured
        facts.code = {}
        _log(f"the code answers failed this read: {type(e).__name__}: {e}")
    try:
        facts.headlines = headlines_before(state_dir, read_record["row_ts"], HEADLINE_WINDOW_MIN)
    except Exception as e:
        facts.headlines, facts.headlines_why = [], f"the headline feed could not be read: {type(e).__name__}"
        _log(facts.headlines_why)
    try:
        facts.yield_move = yield_move(state_dir, day, read_record["row_ts"])
    except Exception as e:
        facts.yield_move = YieldMove(None, None, f"its reading failed: {type(e).__name__}")
        _log(f"the ten-year yield's move failed this read: {type(e).__name__}: {e}")
    return facts


def _log(msg: str) -> None:
    from .service import log
    log(f"judgment: {msg}")


# ---------------------------------------------------------------- the labels

def _said(facts: Facts, qid: str, what: str) -> str:
    a = _code(facts, qid)
    return f"{what} reads '{a}' ({qid})" if a is not None else f"{what} is {NOT_MEASURED} ({qid})"


def headline_label(facts: Facts) -> str:
    """The headlines captured in the window before the read as one label, newest first, at most MAX_TITLES."""
    if not facts.headlines:
        why = f" ({facts.headlines_why})" if facts.headlines_why else ""
        return f"no headlines captured in the {HEADLINE_WINDOW_MIN} minutes before the read{why}"
    kept = facts.headlines[:MAX_TITLES]
    newest, oldest = parse_ts(kept[0]["captured_at"]).astimezone(ET), parse_ts(kept[-1]["captured_at"]).astimezone(ET)
    more = f" ({len(facts.headlines) - len(kept)} more not shown)" if len(facts.headlines) > len(kept) else ""
    lines = "; ".join(f"{h['title']} ({h.get('source') or h.get('feed')}, {parse_ts(h['captured_at']).astimezone(ET):%H:%M} ET)" for h in kept)
    return (f"{len(facts.headlines)} headlines captured from {oldest:%H:%M} to {newest:%H:%M} ET, all before the read, newest first{more}: "
            f"{lines}")


def build_judgment_labels(facts: Facts) -> LabelSet:
    """The judgment labels: the code answers each ask names, in words, and the headlines. Every one is written on every
    read (a gate that is not fired skips its question before the label is read)."""
    ls = LabelSet()
    yields = facts.yield_move.words() if facts.yield_move else f"the ten-year yield's move is {NOT_MEASURED}"
    sweep = _said(facts, "TREND-10", "the code's 60-minute range sweep")
    spike = _said(facts, "TREND-11", "its volume-spike follow-through")
    trend = _said(facts, "TREND-04", "the last 30 minutes")
    compression = _said(facts, "VOLATILITY-04", "the last 30 minutes' range against the half hour before")
    swing = _said(facts, "VOLATILITY-02", "the last 30 minutes' swing against the same half hour")
    heavy = _said(facts, "BREADTH-09", "the heavyweights since the close")
    gap = _said(facts, "MACRO-02", "SPX against what bonds imply since the close")
    release = _said(facts, "EVENTS-03", "the latest release's reaction")
    ls.put("judgment.push_exhaustion", "; ".join((sweep, spike, trend)))
    ls.put("judgment.quiet", "; ".join((compression, swing, heavy, yields)))
    ls.put("judgment.heavyweight", heavy)
    ls.put("judgment.macro_gap", f"{gap}: 'up' is SPX short of what bonds imply, 'down' past it, 'far' the top fifth")
    ls.put("judgment.yield_move", "; ".join((yields, release)))
    ls.put("judgment.headlines", headline_label(facts))
    return ls


def judge(state_dir: Path | str, lane_name: str, day: str, scene: Scene, labels: LabelSet, event: dict | None, qids,
          record: bool) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """The judgment questions' gate verdicts for one read and the code feature builder's answers the gates read (what
    the sums ride on from the cut-over, hour.cut_over_sentences), never raising. The labels go into ``labels``; a failure
    leaves every gate not fired, with the failure as its reason, and no code answers."""
    try:
        facts = facts_for(state_dir, lane_name, day, read_record_for_code(lane_name, scene, labels, event), record)
        labels.update(build_judgment_labels(facts))
        return verdicts(qids, facts), facts.code
    except Exception as e:  # the judgment group must never cost the read
        _log(f"left out this read: {type(e).__name__}: {e}")
        return {qid: f"{GATED} {' or '.join(GATES[qid].reads)} not fired: the judgment group failed ({type(e).__name__})" for qid in qids}, {}
