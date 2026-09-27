"""The story family: the night so far, told from the stretches of the overnight futures session (story.py)
as each read rebuilds them from bars (premarket.*): where futures stand against their 16:00 price, each
stretch in time order, the arc from the first stretch that moved, the leg since the previous checkpoint,
the report window against the night, and the night against yesterday's last cash hour.

JEV's earlier answers never reach these sentences: a read measures every stretch afresh from the bars
finished by ``now``, so a missed read changes nothing. Sizes are in the pre-open ruler
(rulers.normal_day_sigma, ``scene.sigma`` on a premarket read), each move ranked in percent against the
same clock window on the last nights (night_ranks.prior_window_nights), never against a fixed line: a
leg "moved" when it ranks above the bottom third, and is "quiet" otherwise. A stretch whose edge has no
/ES bar within READ_STALE_MIN trading minutes (a gap in the store; the daily 17:00 halt is not one) is
told together with its neighbour. A night across a contract roll is not told at all, nor one whose roll
Schwab's quote shows before the roll table has located it: its move from the 16:00 price, which every label
states first, would be the spread between two contracts. Written only on a premarket read (PREMARKET). Each label's sentence, how it is computed and its source are in
spec/question_set.json ``labels``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import groupby
from pathlib import Path
from typing import Callable

from .. import events, overnight, rolls, story
from ..cuts import GAP_HALF_SHARE, NIGHT_RANK_COUNT, THIRD_LO
from ..night_ranks import (READ_STALE_MIN, Move, Night, NightRank, prior_window_nights, price_by, quoted_contract, rank_night, roll_pending,
                           window_move)
from ..sessions import session_close
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ET, close_at, is_num, yesterdays_bars
from .ranks import SameClockRank, rank_against
from .words import above_or_below, listed, pct, sig, third

LABELS = ("premarket.where_now", "premarket.legs", "premarket.arc", "premarket.since_checkpoint", "premarket.release_vs_night",
          "premarket.vs_last_hour")
GATES = ("overnight_arc", "latest_leg_vs_night", "night_legs_agree", "release_vs_night", "night_vs_last_hour")
DARK: dict[str, str] = {}
PREMARKET = LABELS

ES = "/ES"
LAST_HOUR = timedelta(minutes=60)
# How each stretch is named in a sentence; on a day without a report its two stretches say their clocks.
STRETCH_WORDS = {"after_close": "after hours", "asia": "Asia", "europe_open": "Europe's open", "europe_morning": "Europe's morning",
                 "pre_report": "before the report", "report_window": "the report window", "last_stretch": "since 08:45"}
REPORT_STRETCHES = ("pre_report", "report_window")
# premarket.where_now's lean, the card's story chip: the net's side by its third; the bottom third is flat.
LEANS = {("top", 1): "up", ("middle", 1): "up_small", ("top", -1): "down", ("middle", -1): "down_small"}
GATE_LABELS = {"overnight_arc": "premarket.arc", "latest_leg_vs_night": "premarket.since_checkpoint",
               "night_legs_agree": "premarket.legs", "release_vs_night": "premarket.release_vs_night",
               "night_vs_last_hour": "premarket.vs_last_hour"}


def build_story_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    if not scene.premarket:
        return ls
    night, why = _read_night(scene)
    if night is None:
        for path in LABELS:
            ls.omit(path, why)
        for qid in GATES:
            ls.sleep(qid, why)
        return ls
    _where_now(night, ls)
    legs, why = night.legs()
    if legs is None:
        _omit(ls, "night_legs_agree", why)
        _omit(ls, "overnight_arc", why)
    else:
        _legs(night, legs, ls)
        _arc(night, legs, ls)
    _since_checkpoint(night, ls)
    _release_vs_night(night, ls)
    _vs_last_hour(night, ls)
    return ls


# ---- the night as the read measures it -----------------------------------------------------------------

@dataclass(frozen=True)
class Leg:
    """One leg of the night's story: the stretches it spans (two or more when a missing price merged them),
    its edges, whether the read has passed its end, its move and that move's rank against the same window
    on the last nights."""
    names: tuple[str, ...]
    start: datetime
    end: datetime
    finished: bool
    move: Move
    rank: NightRank

    @property
    def moved(self) -> bool:
        return self.rank.share > THIRD_LO


def _side(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def _risen(x: float) -> str:
    return "rose" if x >= 0 else "fell"


def _sized(s: float) -> str:
    """A signed size against a price: "0.31 sigma above"."""
    return f"{sig(abs(s))} {above_or_below(s)}"


class NightSoFar:
    """The night into the read's day: /ES's bars in the overnight store, the roll table, the stretches, the
    ruler and futures' move since their 16:00 price with its rank (``net`` None, and ``unmeasured`` why, when
    it cannot be had), with each window's prior nights read from the store once per read."""

    def __init__(self, scene: Scene, points_per_pct: float) -> None:
        self.scene = scene
        self.now = scene.now.astimezone(ET)
        self.day = self.now.date()
        self.clock = self.now.time()
        self.points_per_pct = points_per_pct
        self.rows = [r for r in scene.night if r["symbol"] == ES]
        self.table = rolls.load(Path(scene.state_dir) / overnight.OVERNIGHT_SUBDIR)
        self.pending = roll_pending(self.table, ES, quoted_contract(scene.state_dir, self.day, ES, self.now))
        self.release = story.release_minute(self.day)
        self.report = bool(story.releases(self.day))
        self.stretches = story.stretches(self.day, self.now, self.release)
        self.start = story.night_start(self.day)
        self._nights: dict[tuple, list[Night]] = {}
        self.net, self.net_rank, self.unmeasured = self.since_start(self.now)

    def sigma(self, pct_move: float) -> float:
        """A move in percent as sigma of the pre-open ruler, on SPX's prior close."""
        return pct_move * self.points_per_pct

    def price(self, t: datetime) -> tuple[float, datetime] | None:
        """/ES's price at ``t``: its newest bar finished by then, unless the market traded more than
        READ_STALE_MIN minutes after that bar with none on file (a gap in the store, not a halt)."""
        got = price_by(self.rows, ES, t)
        if got is None or len(overnight.expected_slots(ES, 1, got[1], t)) > READ_STALE_MIN:
            return None
        return got

    def move(self, start: datetime, end: datetime) -> tuple[Move | None, str]:
        """/ES's move from ``start`` to ``end`` on one contract, or None and why: also while Schwab quotes a
        contract the roll table has not rolled to, since the roll's minute is not located yet."""
        if self.pending:
            return None, self.pending
        for t in (start, end):
            if self.price(t) is None:
                return None, f"no /ES bar in the overnight store within {READ_STALE_MIN} trading minutes before {t:%H:%M}"
        move = window_move(self.rows, ES, start, end)
        if move is None:
            return None, f"no /ES price from {start:%H:%M} to {end:%H:%M}"
        if not rolls.same_contract(self.table, ES, move.start_at, move.end_at):
            return None, (f"the futures rolled to the next contract between {start:%H:%M} and {end:%H:%M}, "
                          "so the move is the spread between two contracts")
        return move, ""

    def rank(self, move: Move, key: tuple, window: Callable[[date], tuple[datetime, datetime]]) -> tuple[NightRank | None, str | None]:
        """The move's size against ``window`` (``day -> (start, end)``) on the last nights, each window read once."""
        if key not in self._nights:
            self._nights[key] = prior_window_nights(self.scene.state_dir, self.day, ES, window, self.table, lambda m: abs(m.pct))
        return rank_night(abs(move.pct), self._nights[key])

    def since_start(self, t: datetime) -> tuple[Move | None, NightRank | None, str]:
        """Futures against their 16:00 price at ``t`` and that move's rank against the same clock on the last nights."""
        move, why = self.move(self.start, t)
        if move is None:
            return None, None, why
        clock = t.astimezone(ET).time()
        rank, why = self.rank(move, ("net", clock), lambda d: (story.night_start(d), datetime.combine(d, clock, tzinfo=ET)))
        return (move, rank, "") if rank is not None else (None, None, f"not ranked: {why}")

    def where(self, move: Move) -> str:
        """Where futures stand against their 16:00 price: "0.31 sigma above their 16:00 price"."""
        return f"{_sized(self.sigma(move.pct))} their {self.start:%H:%M} price"

    def legs(self) -> tuple[list[Leg] | None, str]:
        """The stretches seen so far as legs, oldest first: two told together where the price at the edge
        between them is missing, each measured and ranked against the same clock window on the last nights."""
        groups: list[list[story.Stretch]] = []
        for k, s in enumerate(self.stretches):
            if k and self.price(s.start) is None:
                groups[-1].append(s)
            else:
                groups.append([s])
        out = []
        for g in groups:
            names, start, end, finished = tuple(s.name for s in g), g[0].start, g[-1].end, g[-1].finished
            move, why = self.move(start, end)
            if move is None:
                return None, f"the night's legs: {why}"
            until = None if finished else self.clock
            first = story.stretch_window(names[0], release=self.release)
            last = story.stretch_window(names[-1], until=until, release=self.release)
            rank, why = self.rank(move, (names, until), lambda d, first=first, last=last: (first(d)[0], last(d)[1]))
            if rank is None:
                return None, f"the night's legs are not ranked: {why}"
            out.append(Leg(names, start, end, finished, move, rank))
        return out, ""

    def leg_name(self, leg: Leg) -> str:
        """The leg's stretches in words, saying when a missing price told two together."""
        words = [STRETCH_WORDS[n] if self.report or n not in REPORT_STRETCHES else f"{s:%H:%M}-{e:%H:%M}"
                 for n, s, e in story.edges(self.day, self.release) if n in leg.names]
        return listed(words) + (" together, bars missing" if len(words) > 1 else "")


def _read_night(scene: Scene) -> tuple[NightSoFar | None, str]:
    """The night so far with futures' move since their 16:00 price and its rank, or None and why nothing can be told."""
    if scene.state_dir is None:
        return None, "no state folder to read the last nights from"
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0 or not is_num(scene.sigma) or scene.sigma <= 0:
        return None, "the read carries no pre-open ruler or no prior close"
    night = NightSoFar(scene, float(pc) / 100.0 / scene.sigma)
    if night.net is None:
        return None, f"futures against their {night.start:%H:%M} price: {night.unmeasured}"
    return night, ""


def _band(rank: NightRank | SameClockRank) -> str:
    return f"{third(rank.share)} third"


def _figure(value: float, rank: NightRank | SameClockRank, verdict: str | None) -> dict:
    """The label's figure: its number, its rank's band and the option the code answers (None while the gate sleeps)."""
    return {"kind": "rank", "value": round(value, 3), "cut": _band(rank), "verdict": verdict}


def _decide(ls: LabelSet, qid: str, verdict: str | None, why: str | None) -> None:
    if verdict is None:
        ls.sleep(qid, why)
    else:
        ls.wake(qid)


def _omit(ls: LabelSet, qid: str, why: str) -> None:
    """The gated question's label left out and the question asleep, for one reason."""
    ls.omit(GATE_LABELS[qid], why)
    ls.sleep(qid, why)


def _ended(verdict: str | None, text: str, ends: dict[str, str]) -> str:
    """A sentence closed by its verdict's words, the option name's only place in it; bare while the gate sleeps."""
    return f"{text}: {ends[verdict]}" if verdict else text


# ---- the labels -----------------------------------------------------------------------------------------

def _where_now(night: NightSoFar, ls: LabelSet) -> None:
    """Where futures stand against their 16:00 price, how that ranks against the last nights at this clock,
    and where in the night's range they sit."""
    s, rank = night.sigma(night.net.pct), night.net_rank
    lean = LEANS.get((third(rank.share), _side(s)), "flat")
    text = f"at {night.now:%H:%M} S&P futures are {night.where(night.net)}, {rank.words('moves to this time')}"
    minutes = 1 if any(r["bar_minutes"] == 1 for r in night.rows) else overnight.BAR_MINUTES[-1]
    done = [r for r in night.rows if r["bar_minutes"] == minutes and datetime.fromisoformat(r["ts"]) >= night.start]
    hi, lo = max((r["high"] for r in done), default=0.0), min((r["low"] for r in done), default=0.0)
    if hi > lo:
        text += f"; {pct((night.price(night.now)[0] - lo) / (hi - lo))} of the way up the night's range"
    ls.put("premarket.where_now", text, figure=_figure(s, rank, lean))


def _legs(night: NightSoFar, legs: list[Leg], ls: LabelSet) -> None:
    """Each leg oldest first, a quiet one (the bottom third) by name and one that moved by its side and size;
    then night_legs_agree's verdict over the legs that moved, against the night's net move. The running leg
    counts, ranked against the same part of its window; the gate needs two finished legs that moved. No leg
    says its third or "added" (latest_leg_vs_night's option, read beside it), so seven legs fit a short sentence."""
    parts, after_first = [], False
    for moved, run in groupby(legs, key=lambda leg: leg.moved):
        run = list(run)
        if not moved:
            parts.append(f"{listed([night.leg_name(leg) for leg in run])} quiet{'' if run[-1].finished else ' so far'}")
            continue
        for leg in run:
            s = night.sigma(leg.move.pct)
            size = f"{abs(s):.2f}{'' if after_first else ' sigma'}{'' if leg.finished else ' so far'}"
            parts.append(f"{night.leg_name(leg)} {'up' if s >= 0 else 'down'} {size}")
            after_first = True
    moved = [leg for leg in legs if leg.moved]
    finished = [leg for leg in moved if leg.finished]
    net_side = _side(night.net.pct)
    verdict, why = None, None
    if night.net_rank.share <= THIRD_LO:
        why = f"the night's net move is quiet, {_band(night.net_rank)} for this time"
    elif len(finished) < 2:
        why = f"{'only one finished leg' if finished else 'no finished leg'} of the night moved"
    elif all(_side(leg.move.pct) == net_side for leg in moved):
        verdict = "one_way"
    elif _side(moved[-1].move.pct) != net_side:
        verdict = "latest_against"
    else:
        verdict = "split_latest_with"
    way = f"the night's {'rise' if net_side >= 0 else 'fall'}"
    ends = {"one_way": f", all with {way}: one way", "latest_against": f"; the latest against {way}",
            "split_latest_with": f", split; the latest with {way}"}
    tail = f"{len(moved)} moved{ends[verdict]}" if verdict else why
    ls.put("premarket.legs", f"oldest first: {'; '.join(parts)}. {tail[0].upper()}{tail[1:]}",
           figure=_figure(night.sigma(night.net.pct), night.net_rank, verdict))
    _decide(ls, "night_legs_agree", verdict, why)


def _arc(night: NightSoFar, legs: list[Leg], ls: LabelSet) -> None:
    """Where futures stand, then how much of the night's first finished leg that moved they keep: overnight_arc
    checks reversed (the other side of the 16:00 price, the net not quiet), faded, built and held in that order."""
    first = next((leg for leg in legs if leg.finished and leg.moved), None)
    where = f"futures are {night.where(night.net)}"
    if first is None:
        ls.put("premarket.arc", f"{where}; no finished leg of the night moved past the bottom third of the last nights")
        ls.sleep("overnight_arc", "no finished leg of the night moved: a quiet night")
        return
    s, kept = night.sigma(first.move.pct), night.net.pct / first.move.pct
    if _side(night.net.pct) == -_side(first.move.pct) and night.net_rank.share > THIRD_LO:
        verdict, end = "reversed", "the night reversed its first move"
    elif kept < GAP_HALF_SHARE:
        verdict, end = "faded", "the first move faded"
    elif kept > 1:
        verdict, end = "built", "later trading built on it"
    else:
        verdict, end = "held", "the first move held"
    keeps = f"keep {pct(kept)} of it" if kept >= 0 else "keep none of it"
    ls.put("premarket.arc", f"{where}; the night's first leg that moved was {night.leg_name(first)}, {'up' if s >= 0 else 'down'} "
                            f"{sig(abs(s))}, {_band(first.rank)}; they {keeps}: {end}",
           figure=_figure(kept, first.rank, verdict))
    ls.wake("overnight_arc")


def previous_checkpoint(now: datetime) -> datetime | None:
    """The checkpoint before the read's own, its own being the newest of the day's checkpoints at or before
    ``now`` (a late read is still its checkpoint's read); None at the first."""
    from ..premarket import checkpoints   # the premarket lane builds these labels, so it is imported only here
    local = now.astimezone(ET)
    due = [t for t in (time.fromisoformat(c) for c in checkpoints(local.date())) if t <= local.time()]
    return datetime.combine(local.date(), due[-2], tzinfo=ET) if len(due) > 1 else None


def _since_checkpoint(night: NightSoFar, ls: LabelSet) -> None:
    """The leg from the previous scheduled checkpoint to the read, never from when the previous read fired,
    against the night's move at that checkpoint; latest_leg_vs_night checks crossed first."""
    prev = previous_checkpoint(night.now)
    if prev is None:
        _omit(ls, "latest_leg_vs_night", "the first checkpoint of the night: no earlier checkpoint to measure from")
        return
    leg, why = night.move(prev, night.now)
    before, before_rank, why_before = night.since_start(prev)
    if leg is None or before is None:
        _omit(ls, "latest_leg_vs_night", f"the leg since the {prev:%H:%M} checkpoint: {why}" if leg is None
              else f"the night at the {prev:%H:%M} checkpoint: {why_before}")
        return
    start, clock = prev.time(), night.clock
    rank, why = night.rank(leg, ("since", start, clock),
                           lambda d: (datetime.combine(d, start, tzinfo=ET), datetime.combine(d, clock, tzinfo=ET)))
    if rank is None:
        _omit(ls, "latest_leg_vs_night", f"the leg since the {prev:%H:%M} checkpoint is not ranked: {why}")
        return
    s, b = night.sigma(leg.pct), night.sigma(before.pct)
    verdict, why = None, None
    if rank.share <= THIRD_LO:
        why = f"a quiet leg: {_band(rank)} for {prev:%H:%M}-{night.now:%H:%M} over the last nights"
    elif before_rank.share <= THIRD_LO:
        why = f"the night had no direction at the {prev:%H:%M} checkpoint: {_band(before_rank)} for that time"
    elif _side(night.net.pct) == -_side(b):
        verdict = "crossed"
    elif _side(s) == _side(b):
        verdict = "added"
    else:
        verdict = "gave_back"
    ends = {"crossed": f"the latest leg crossed their {night.start:%H:%M} price", "added": "the latest leg added",
            "gave_back": "the latest leg gave back"}
    text = (f"futures are {night.where(night.net)}; since the {prev:%H:%M} checkpoint they {_risen(s)} {sig(abs(s))}, "
            f"{_band(rank)} for {prev:%H:%M}-{night.now:%H:%M} over the last {rank.of} nights, "
            f"{'with' if _side(s) == _side(b) else 'against'} the night's {'rise' if b >= 0 else 'fall'} before it")
    ls.put("premarket.since_checkpoint", _ended(verdict, text, ends), figure=_figure(s, rank, verdict))
    _decide(ls, "latest_leg_vs_night", verdict, why)


def _release_vs_night(night: NightSoFar, ls: LabelSet) -> None:
    """On a day with a report before the open, the report window (its minute to 08:45) against where the night
    stood at the report; release_vs_night checks crossed_price first, tested at 08:45."""
    uncovered = events.uncovered(night.day)
    if uncovered:
        _omit(ls, "release_vs_night", uncovered)
        return
    due = [e for e in story.releases(night.day) if e.start.time() == night.release]
    if not due:
        ls.omit("premarket.release_vs_night", "no report before the open today", ended=True)
        ls.sleep("release_vs_night", "no report before the open today")
        return
    at, end = (datetime.combine(night.day, t, tzinfo=ET) for t in (night.release, story.REPORT_WINDOW_END))
    if night.now < end:
        _omit(ls, "release_vs_night", f"the report window runs to {end:%H:%M}")
        return
    reaction, why = night.move(at, end)
    before, before_rank, why_before = night.since_start(at)
    after, _, why_after = night.since_start(end)
    if reaction is None or before is None or after is None:
        _omit(ls, "release_vs_night", f"the report window: {why or why_before or why_after}")
        return
    rank, why = night.rank(reaction, (("report_window",), None), story.stretch_window("report_window", release=night.release))
    if rank is None:
        _omit(ls, "release_vs_night", f"the report window is not ranked: {why}")
        return
    r, b = night.sigma(reaction.pct), night.sigma(before.pct)
    verdict, why = None, None
    if rank.share <= THIRD_LO:
        why = f"a quiet report window: {_band(rank)} for {at:%H:%M}-{end:%H:%M} over the last nights"
    elif before_rank.share <= THIRD_LO:
        why = f"the night had no direction before the report: {_band(before_rank)} for {at:%H:%M}"
    elif _side(after.pct) == -_side(b):
        verdict = "crossed_price"
    elif _side(r) == -_side(b):
        verdict = "unwound_night"
    else:
        verdict = "extended_night"
    ends = {"crossed_price": f"it carried them across their {night.start:%H:%M} price",
            "unwound_night": "it undid part of the night's move", "extended_night": "it went the night's way"}
    text = (f"futures are {night.where(night.net)}; before {listed([e.words for e in due])} at {at:%H:%M} they were "
            f"{abs(b):.2f} {above_or_below(b)}, {_band(before_rank)}; by {end:%H:%M} they {_risen(r)} {sig(abs(r))}, "
            f"{_band(rank)} for that window")
    ls.put("premarket.release_vs_night", _ended(verdict, text, ends), figure=_figure(r, rank, verdict))
    _decide(ls, "release_vs_night", verdict, why)


def _last_hour_pct(bars: list[dict], day: str) -> float | None:
    """SPX's move in percent over the session's last hour (to 13:00 on a half day), from its bars."""
    close = session_close(datetime.combine(date.fromisoformat(day), time(12), tzinfo=ET))
    a, b = close_at(bars, close - LAST_HOUR), close_at(bars, close)
    return 100.0 * (b / a - 1.0) if a and b else None


def _vs_last_hour(night: NightSoFar, ls: LabelSet) -> None:
    """Where futures stand, then SPX's move over yesterday's last cash hour, ranked against the last sessions'
    last hours; night_vs_last_hour asks whether the night carried it on."""
    day, bars, why = yesterdays_bars(night.scene)
    hour = _last_hour_pct(bars, day) if bars else None
    if hour is None:
        _omit(ls, "night_vs_last_hour", f"yesterday's last hour: {why or 'no SPX bars at its start and end'}")
        return
    base = [abs(v) for d, b in night.scene.prior_bars.items() if d != day and (v := _last_hour_pct(b, d)) is not None]
    rank = rank_against(abs(hour), base[:NIGHT_RANK_COUNT])
    if rank is None:
        _omit(ls, "night_vs_last_hour", f"yesterday's last hour is not ranked: {len(base)} prior sessions' last hours on file")
        return
    s = night.sigma(hour)
    verdict, why = None, None
    if rank.share <= THIRD_LO:
        why = f"yesterday's last hour was quiet, {_band(rank)} of the last {rank.of} sessions' last hours"
    elif night.net_rank.share <= THIRD_LO:
        why = f"the night's net move is quiet, {_band(night.net_rank)} for this time"
    else:
        verdict = "carried_on" if _side(night.net.pct) == _side(hour) else "turned_against"
    ends = {"carried_on": "the night carried on yesterday's last hour", "turned_against": "the night turned against yesterday's last hour"}
    text = (f"futures are {night.where(night.net)}, {_band(night.net_rank)}; yesterday SPX {_risen(s)} {sig(abs(s))} in its "
            f"last hour, {_band(rank)} of the last {rank.of} sessions' last hours")
    ls.put("premarket.vs_last_hour", _ended(verdict, text, ends), figure=_figure(s, rank, verdict))
    _decide(ls, "night_vs_last_hour", verdict, why)
