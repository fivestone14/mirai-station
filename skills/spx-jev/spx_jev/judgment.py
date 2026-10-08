"""The judgment group: the six questions of the merged set that ask JEV for a judgment, each behind a gate the code decides.

A judgment question is asked only when the moment calls for one: a push partly given back, a compressed range, a
heavyweight in play, SPX far from what bonds imply (once, until that changes), the ten-year yield moving more than
usual, material news freshly on the tape. Each gate is read off the code feature builder's answers for the read (mirai_prediction/code_features.py, the
same answers the Mirai Prediction System's matrix holds as its layers 1 and 2), the headline feed (headlines.py) and
the ten-year yield's move since the prior close ranked against the same minute on the prior sessions. A gate whose
code answer is None is not fired: nothing the code could not measure wakes a question. A question whose gate is not
fired is skipped with the reason ``gate: <code question> not fired`` and costs the read nothing (ask.build_requests).

The group's labels, written under ``judgment.*``, are the code answers each ask names, in words (the heavyweight in
play named, with its pull and the headlines naming it; the bond gap's size and side), and one label of the headlines
first captured in the HEADLINE_WINDOW_MIN minutes ending HEADLINE_CUT_MIN minutes before the read
(headlines.headlines_before), the CNBC, MarketWatch, Fed and market-news titles first when there are more than one
label holds, so JEV reads only what the station could have read before the read.

The questions are ``shadow`` in the set: asked and logged, never summed, never weighted, never held, and never a
member of the old learning loop (hour.answer_sentences, grade.live_options, pool_snapshots and the cadence all keep to
``live`` questions). The learning store files their answers as ``answered`` all the same, so the Mirai Prediction
System's matrix takes them as its layer-3 columns ``jev:<question id>`` (answer_matrix.load_jev_answers).

Since 2026-10-07 the service answers the code questions before it builds the read's requests (service.run_once), so
the gates can read them; the live read records them under raw/code_features/ first and the forecast after the read
reuses the stored line by read_id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import daily_closes
from .cuts import HEADLINE_CUT_MIN, HEADLINE_FRESH_MIN, HEADLINE_WINDOW_MIN, THIRD_HI
from .headlines import MAX_TITLES, headlines_before
from .labels.label_set import LabelSet
from .labels.measures import ET
from .labels.ranks import SameClockRank, rank_sessions
from .state_builder import CONTEXT_SUBDIR, Scene, load_jsonl, parse_ts

from .ask import GATED            # a gate's reason starts with this; ask.build_requests keeps it as the skipped reason
TNX = "$TNX"
NOT_MEASURED = "not measured this read"
FAR_GAP = ("far up", "far down")                 # MACRO-02's top fifth: the macro_gap gate
# The heavyweights' names as headlines write them, by the market feed's ticker; a ticker is matched as written too.
MEGACAP_NAMES = {"NVDA": ("Nvidia",), "MSFT": ("Microsoft",), "AAPL": ("Apple",), "AMZN": ("Amazon",), "GOOGL": ("Alphabet", "Google", "GOOG"),
                 "META": ("Meta",), "AVGO": ("Broadcom",), "TSLA": ("Tesla",), "BRK/B": ("Berkshire", "BRK.B")}
# A material headline names stocks, rates, the Fed or a heavyweight (news_reaction's gate, and the titles a label shows first).
MATERIAL = re.compile(r"\b(stocks?|equit(?:y|ies)|shares|S&P|Nasdaq|Dow|Wall St(?:reet)?|futures|yields?|Treasur(?:y|ies)|bonds?|rates?|"
                      r"Fed|Federal Reserve|Powell|FOMC|" + "|".join(re.escape(w) for t, ws in MEGACAP_NAMES.items() for w in (t, *ws)) + r")\b",
                      re.IGNORECASE)
PREFERRED_FEEDS = ("cnbc_top", "marketwatch_top", "fed_press")      # shown first beside the material titles: Google News is mostly noise
# news_reaction's gate reads only the preferred feeds' titles that name an index-wide mover: the Fed, a major release, trade,
# Treasury yields, or a heavyweight's earnings or guidance (MATERIAL matched ~85% of 10-07's titles, so it gated nothing).
INDEX_NEWS = re.compile(r"\b(Fed|FOMC|Federal Open Market Committee|Powell|CPI|PPI|jobs report|payrolls|GDP|PCE|retail sales|tariffs?|"
                        r"trade (?:war|deal|talks)|Treasury yields?|10-year yield|bond yields?)\b", re.IGNORECASE)
_MEGACAP = "(?:" + "|".join(re.escape(w) for ws in MEGACAP_NAMES.values() for w in ws) + ")"
_RESULTS = r"(?:earnings|guidance|results|forecast|outlook)"
MEGACAP_RESULTS = re.compile(rf"\b{_MEGACAP}\b.*\b{_RESULTS}\b|\b{_RESULTS}\b.*\b{_MEGACAP}\b", re.IGNORECASE)


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
    row_ts: str | None = None                                      # the read's time: news_reaction's fresh window ends HEADLINE_CUT_MIN before it
    answered_code: dict[str, str | None] = field(default_factory=dict)   # the code answers of the day's last read JEV answered macro_gap_equity_reason on
    news_seen_until: str | None = None                             # the cut of the day's last read news_reaction was asked on
    heavyweight: tuple[str, float, float] | None = None            # BREADTH-09's (ticker, its pull as a share of the index, the rest's move)
    macro_gap: tuple[float, float] | None = None                   # MACRO-02's gap since the close (a share; negative: SPX short), in points


@dataclass(frozen=True)
class Gate:
    reads: tuple[str, ...]                           # the code questions (or feeds) it is decided from, named in its reason
    fired: Callable[[Facts], bool]
    held: Callable[[Facts], str | None] | None = None      # why a gate whose reading holds is still not fired, if it can be


def _code(facts: Facts, qid: str) -> str | None:
    return facts.code.get(qid)


def _push(facts: Facts) -> bool:
    return any(_code(facts, q) not in (None, "none") for q in ("TREND-10", "TREND-11"))


def _in_play(facts: Facts) -> bool:
    return str(_code(facts, "BREADTH-09") or "").startswith("in play")


def _far_gap(facts: Facts) -> bool:
    # answered once while the gap stays in its top fifth on the same side: the answer to one fact, not one per read; an ask
    # JEV did not answer counts for nothing, so the next read asks again
    return _code(facts, "MACRO-02") in FAR_GAP and facts.answered_code.get("MACRO-02") != _code(facts, "MACRO-02")


def _far_gap_held(facts: Facts) -> str | None:
    a = _code(facts, "MACRO-02")
    return (f"MACRO-02 {a!r} as on the last read JEV answered it on (answered once until it changes)"
            if a in FAR_GAP and facts.answered_code.get("MACRO-02") == a else None)


def material(headline: dict) -> bool:
    return bool(MATERIAL.search(headline.get("title") or ""))


def index_news(headline: dict) -> bool:
    """A preferred feed's title naming an index-wide mover (news_reaction's gate)."""
    title = headline.get("title") or ""
    return headline.get("feed") in PREFERRED_FEEDS and bool(INDEX_NEWS.search(title) or MEGACAP_RESULTS.search(title))


def _fresh_news(facts: Facts) -> bool:
    # first captured in the HEADLINE_FRESH_MIN minutes before the cut, and after the cut of the last read that asked it
    if facts.row_ts is None:
        return False
    since = parse_ts(facts.row_ts) - timedelta(minutes=HEADLINE_CUT_MIN + HEADLINE_FRESH_MIN)
    if facts.news_seen_until is not None:
        since = max(since, parse_ts(facts.news_seen_until))
    return any(index_news(h) and parse_ts(h["captured_at"]) > since for h in facts.headlines)


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
    "macro_gap_equity_reason": Gate(("MACRO-02",), _far_gap, _far_gap_held),
    "yield_move_meaning": Gate((TNX, "EVENTS-03"), _yield_moved),
    "news_reaction": Gate(("headlines",), _fresh_news),
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
    held = gate.held(facts) if gate.held else None
    if held:
        return held
    parts = []
    for what in gate.reads:
        if what == "headlines" and facts.headlines:
            parts.append(f"no new index-wide headline (CNBC, MarketWatch or the Fed: the Fed, a major release, trade, Treasury yields, a "
                         f"heavyweight's results) first captured in the {HEADLINE_FRESH_MIN} minutes before the cut and since the last ask, "
                         f"of {len(facts.headlines)} in the {HEADLINE_WINDOW_MIN}")
        elif what == "headlines":
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


def code_answers_for(state_dir: Path | str, lane_name: str, day: str, read_record: dict, record: bool, history=None,
                     day_bars: list[dict] | None = None) -> dict[str, str | None]:
    """The code feature builder's answers for the read. A live read records them under raw/code_features/ first
    (code_feature_inputs.record_code_features, reused by read_id when the read is forecast); a replay or an unsent run
    computes them and writes nothing. ``history`` and ``day_bars`` are the day's, when the caller loaded them."""
    from .mirai_prediction.code_feature_inputs import load_day_bars, load_market_history, record_code_features
    from .mirai_prediction.code_features import answer_code_features, bars_up_to
    from .mirai_prediction.paths import data_root, ensure_folders
    history = history if history is not None else load_market_history(state_dir, lane_name, day)
    day_bars = day_bars if day_bars is not None else load_day_bars(state_dir, day)
    if record:
        return record_code_features(ensure_folders(data_root(state_dir)), state_dir, lane_name, day, read_record, "live",
                                    market_history=history, day_bars=day_bars)
    return answer_code_features(read_record, bars_up_to(day_bars, read_record["row_ts"]), history)


def _asked(read: dict, qid: str) -> bool:
    return any(qid in (r.get("questions") or {}) for r in read.get("requests") or [] if isinstance(r, dict))


def _answered(read: dict, qid: str) -> bool:
    return any(isinstance(((reply or {}).get("answers") or {}).get(qid), dict) and reply["answers"][qid].get("choice") is not None
               for reply in (read.get("responses") or {}).values() if isinstance(reply, dict))


def last_read(state_dir: Path | str, lane_name: str, day: str, row_ts: str, took: Callable[[dict], bool]) -> dict | None:
    """The lane's newest read of the day before ``row_ts`` in the station's archive that ``took`` holds for; None without one."""
    from .lane import LANES
    from .mirai_prediction.live_records import _json_lines
    reads = [r for r in _json_lines(Path(LANES[lane_name].archive_folder(state_dir)) / f"{day}.jsonl")
             if r.get("kind") in (None, "read") and r.get("lane", lane_name) == lane_name and isinstance(r.get("row_ts"), str)
             and r["row_ts"] < row_ts and took(r)]
    return max(reads, key=lambda r: r["row_ts"]) if reads else None


def code_answers_of(state_dir: Path | str, day: str, read_id: str) -> dict[str, str | None]:
    """One read's code answers as raw/code_features/ holds them (the last line for it); empty without one."""
    from .mirai_prediction.code_feature_inputs import stored_code_features
    from .mirai_prediction.paths import data_root
    return dict(stored_code_features(data_root(state_dir), day).get(read_id, {}).get("answers") or {})


def asks_before(facts: Facts, state_dir: Path | str, lane_name: str, day: str, row_ts: str) -> None:
    """What the once-only gates read of the day's earlier asks: the code answers of the last read JEV answered
    macro_gap_equity_reason on, and the cut of the last read news_reaction was asked on."""
    answered = last_read(state_dir, lane_name, day, row_ts, lambda r: _answered(r, "macro_gap_equity_reason"))
    facts.answered_code = code_answers_of(state_dir, day, answered["read_id"]) if answered else {}
    asked = last_read(state_dir, lane_name, day, row_ts, lambda r: _asked(r, "news_reaction"))
    facts.news_seen_until = (parse_ts(asked["row_ts"]) - timedelta(minutes=HEADLINE_CUT_MIN)).isoformat() if asked else None


def code_details(facts: Facts, read_record: dict, history, day_bars: list[dict]) -> None:
    """The measures behind the code answers a fired gate's label states: the heavyweight in play and the bond gap."""
    from .mirai_prediction.code_features import bars_up_to, hhmm_of
    from .mirai_prediction.code_features_market import heavyweight_pull, macro_gap
    row_ts = read_record["row_ts"]
    day, hhmm, bars = row_ts[:10], hhmm_of(row_ts), bars_up_to(day_bars, row_ts)
    if _in_play(facts):
        facts.heavyweight = heavyweight_pull(read_record, bars, history, day, hhmm)
    if _code(facts, "MACRO-02") in FAR_GAP:
        gap = macro_gap(read_record, bars, history, day, hhmm)
        facts.macro_gap = (gap, gap * float(read_record["spot"])) if gap is not None else None


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
    """A saved session's $TNX close at the last bar finished by ``hhmm`` (context/bars/{day}.jsonl: a bar starting before
    it), as served."""
    last = None
    for line in load_jsonl(Path(state_dir) / CONTEXT_SUBDIR / "bars" / f"{day}.jsonl"):
        bar = (line.get("bars") or {}).get(TNX)
        if (isinstance(bar, dict) and isinstance(bar.get("ts"), str) and _et_hhmm(bar["ts"]) < hhmm
                and isinstance(bar.get("close"), (int, float))):
            last = float(bar["close"])
    return last


def _et_hhmm(ts: str) -> str:
    return parse_ts(ts).astimezone(ET).strftime("%H:%M")


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
    from .mirai_prediction.code_feature_inputs import load_day_bars, load_market_history
    facts = Facts(row_ts=read_record["row_ts"])
    history = day_bars = None
    try:
        history, day_bars = load_market_history(state_dir, lane_name, day), load_day_bars(state_dir, day)
        facts.code = code_answers_for(state_dir, lane_name, day, read_record, record, history=history, day_bars=day_bars)
    except Exception as e:  # each gate then reads its code answers as not measured
        facts.code = {}
        _log(f"the code answers failed this read: {type(e).__name__}: {e}")
    try:
        asks_before(facts, state_dir, lane_name, day, read_record["row_ts"])
        if history is not None:
            code_details(facts, read_record, history, day_bars)
    except Exception as e:  # the labels then say the answers alone
        _log(f"the code answers' measures failed this read: {type(e).__name__}: {e}")
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


def _titles(headlines: list[dict]) -> str:
    return "; ".join(f"{h['title']} ({h.get('source') or h.get('feed')}, {parse_ts(h['captured_at']).astimezone(ET):%H:%M} ET)" for h in headlines)


def _shown_first(h: dict) -> tuple[int, str]:
    return (h.get("feed") in PREFERRED_FEEDS) + material(h), h["captured_at"]


def headline_label(facts: Facts) -> str:
    """The headlines captured in the window before the read as one label, newest first, at most MAX_TITLES: the
    CNBC, MarketWatch and Fed titles and the material ones first, then the newest."""
    if not facts.headlines:
        why = f" ({facts.headlines_why})" if facts.headlines_why else ""
        return f"no headlines captured in the {HEADLINE_WINDOW_MIN} minutes before the read{why}"
    kept = sorted(sorted(facts.headlines, key=_shown_first, reverse=True)[:MAX_TITLES], key=lambda h: h["captured_at"], reverse=True)
    stamps = sorted(parse_ts(h["captured_at"]).astimezone(ET) for h in facts.headlines)
    more = (f" ({len(facts.headlines) - len(kept)} more not shown; the CNBC, MarketWatch, Fed and market-news titles are shown first)"
            if len(facts.headlines) > len(kept) else "")
    return (f"{len(facts.headlines)} headlines captured from {stamps[0]:%H:%M} to {stamps[-1]:%H:%M} ET, all before the read, newest first{more}: "
            f"{_titles(kept)}")


def _names_of(ticker: str) -> tuple[str, ...]:
    return (ticker, *MEGACAP_NAMES.get(ticker, ()))


def heavyweight_label(facts: Facts) -> str:
    """BREADTH-09's answer in words and, while a heavyweight is in play, which one, its pull and the rest of the index
    since the prior close, and the captured headlines that name it."""
    said = _said(facts, "BREADTH-09", "the heavyweights since the close")
    if facts.heavyweight is None or not _in_play(facts):
        return said
    ticker, pull, rest = facts.heavyweight
    names = _names_of(ticker)
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    naming = [h for h in facts.headlines if pattern.search(h.get("title") or "")][:MAX_TITLES]
    called = " or ".join(names)
    news = f"headlines naming {called}, newest first: {_titles(naming)}" if naming else f"no captured headline names {called}"
    return (f"{said}: {ticker} pulls the index {'up' if pull > 0 else 'down'}, its move since the prior close beyond its usual link "
            f"to the index worth {pull * 100:+.2f}% of the index; the rest of the index moved {rest * 100:+.2f}% since the close; {news}")


def macro_gap_label(facts: Facts) -> str:
    """MACRO-02's answer in words and, in its top fifth, the gap's size and side."""
    said = f"{_said(facts, 'MACRO-02', 'SPX against what bonds imply since the close')}: 'up' is SPX short of what bonds imply, 'down' past it, 'far' the top fifth"
    if facts.macro_gap is None or _code(facts, "MACRO-02") not in FAR_GAP:
        return said
    gap, points = facts.macro_gap
    side = "below" if gap < 0 else "above"
    return (f"{said}; SPX sits {abs(gap) * 100:.2f}% (about {abs(points):.0f} points) {side} what the ten-year note's move since the prior "
            f"close implies, so the gap closing would move SPX {'up' if gap < 0 else 'down'}")


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
    release = _said(facts, "EVENTS-03", "the latest release's reaction")
    ls.put("judgment.push_exhaustion", "; ".join((sweep, spike, trend)))
    ls.put("judgment.quiet", "; ".join((compression, swing, heavy, yields)))
    ls.put("judgment.heavyweight", heavyweight_label(facts))
    ls.put("judgment.macro_gap", macro_gap_label(facts))
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
