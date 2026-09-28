"""The read-sequence family: the 30-minute lane's last reads told in order (seq.*), each rebuilt from the
bars and the market context as they stood at that read's minute, never from the answers JEV gave then:
the day's move from the open at each read (seq.day_move_by_read) and the volume share of rising stocks at
each read against the day's side (seq.breadth_by_read). Each label's sentence, how it is computed and its
source are in spec/question_set.json ``labels``.

The reads are the live lane's minutes (:02 and :32) from its first after the settled open (10:02, the
set's thirty_minute_start) to this read; a sentence lists the last LISTED_READS of them (BREADTH_READS for
breadth, whose sentence carries two numbers a read), while the day's furthest point from the open is
sought over them all. The earlier ones are taken
at their scheduled minute, so a late or missed read changes nothing. The day's move is in the morning
anchor from the settled open, its size ranked against the same move to this minute on the last sessions
(ranks.move_rank): in their bottom third the day has not moved, and both questions sleep. What changed
across the reads is ranked against the same read minutes on the prior sessions, never against a fixed line.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from ..lane import LIVE
from ..state_builder import MarketContext, Scene
from .breadth import FRESH_MIN
from .label_set import LabelSet
from .measures import ET, SETTLED_OPEN_BAR, close_at, settled_open
from .price import _minutes_since_bar
from .ranks import SameClockRank, move_rank, rank_sessions, same_clock_values
from .rulers import NO_ANCHOR, SigmaRuler, ruled, sigma_anchor
from .words import above_or_below, listed, pct, sig, signed

LABELS = ("seq.day_move_by_read", "seq.breadth_by_read")
GATES = ("seq_day_move_stage", "seq_breadth_drift")
DARK: dict[str, str] = {}

LISTED_READS = 4                  # the reads the day's move lists, this one included
BREADTH_READS = 3                 # the reads the breadth sentence lists, each with a share beside its move
FIRST_READ = time(10, 2)          # the lane's first read after the 09:34 settled open
MIN_EARLIER_READS = 2


def build_read_sequence_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    reads, why = _reads(scene)
    if reads is None:
        for path, qid in zip(LABELS, GATES):
            ls.omit(path, why)
            ls.sleep(qid, why)
        return ls
    _day_move_by_read(scene, reads, ls)
    _breadth_by_read(scene, reads, ls)
    return ls


@dataclass(frozen=True)
class Reads:
    """This read and the lane's earlier ones today, oldest first: their minutes and the day's move from the
    settled open at each in the morning anchor, the last this read's own; with this read's move ranked
    against the same move to this minute on the last sessions, or why it is not: the questions' gate, which
    the sleep reason words (the sentences stay the figures)."""
    day: date
    clocks: list[time]
    moves: list[float]
    anchor: SigmaRuler
    move_rank: SameClockRank | None
    move_why: str | None

    @property
    def now(self) -> float:
        return self.moves[-1]

    @property
    def side(self) -> int:
        return 1 if self.now > 0 else -1 if self.now < 0 else 0

    @property
    def earlier(self) -> list[time]:
        return self.clocks[:-1]

    def last(self, n: int) -> Reads:
        return Reads(self.day, self.clocks[-n:], self.moves[-n:], self.anchor, self.move_rank, self.move_why)

    def where(self) -> str:
        return f"SPX is {sig(abs(self.now))} {above_or_below(self.now)} the settled open"


def _earlier_clocks(now: datetime) -> list[time]:
    """The lane's read minutes before this read's own, oldest first: its own is the lane's read nearest ``now``
    (a live read is stamped at its diary row, a few minutes before its minute at most)."""
    minute = now.hour * 60 + now.minute
    reads = [time.fromisoformat(t) for t in LIVE.read_times()]
    own = min(reads, key=lambda t: abs(t.hour * 60 + t.minute - minute))
    return [t for t in reads if FIRST_READ <= t < own]


def _day_moves(bars: list[dict], day: date, clocks: list[time], now_price: float, points: float) -> list[float] | None:
    """The day's move from the settled open at each of ``clocks`` on ``day`` and then at ``now_price``, in ``points``;
    None when the settled open or a read minute's price is not in ``bars``."""
    so = settled_open(bars)
    prices = [close_at(bars, datetime.combine(day, c, tzinfo=ET)) for c in clocks]
    if so is None or any(p is None for p in prices):
        return None
    return [(p - so) / points for p in [*prices, now_price]]


def _reads(scene: Scene) -> tuple[Reads | None, str]:
    anchor = sigma_anchor(scene)
    if anchor is None:
        return None, NO_ANCHOR
    if settled_open(scene.bars) is None:
        return None, "no settled open yet: the 09:34 bar has not finished"
    now = scene.now.astimezone(ET)
    earlier = _earlier_clocks(now)
    if len(earlier) < MIN_EARLIER_READS:
        return None, f"needs {MIN_EARLIER_READS} earlier 30-minute reads today from {FIRST_READ:%H:%M}, have {len(earlier)}"
    moves = _day_moves(scene.bars, now.date(), earlier, scene.spot, anchor.points)
    if moves is None:
        return None, f"no SPX bar finished by one of the reads at {listed([f'{c:%H:%M}' for c in earlier])}"
    # every session's move from its 09:34 close, timed as price.day_move times it
    rank, why = move_rank(scene, moves[-1], _minutes_since_bar(scene, SETTLED_OPEN_BAR))
    return Reads(now.date(), [*earlier, now.time()], moves, anchor, rank, why), ""


def _sleep_why(reads: Reads, rank: SameClockRank | None, why: str | None) -> str | None:
    """Why a read-sequence question sleeps: the day's move from the settled open is not ranked or ranks in the
    bottom third for this minute (the day has not moved), or the question's own rank is missing (``why``)."""
    if reads.move_rank is None:
        return f"the day's move from the settled open is not ranked: {reads.move_why}"
    if reads.move_rank.band == "bottom third":
        return f"the day's move from the settled open is in the bottom third of the last {reads.move_rank.of} sessions at this minute"
    return why if rank is None else None


def _decide(ls: LabelSet, qid: str, verdict: str | None, why: str | None) -> None:
    if verdict is None:
        ls.sleep(qid, why)
    else:
        ls.wake(qid)


def _giveback(moves: list[float]) -> float | None:
    """The share of the furthest point from the open among ``moves``, on whichever side it came, that the last
    has given back (past 100% once the last has crossed the open); 0 when the last is the furthest, None when
    none has left the open."""
    peak = max(moves, key=abs)
    if not peak:
        return None
    if abs(moves[-1]) >= abs(peak):
        return 0.0
    side = 1 if peak > 0 else -1
    return (abs(peak) - side * moves[-1]) / abs(peak)


def _day_move_by_read(scene: Scene, reads: Reads, ls: LabelSet) -> None:
    """The day's move at each listed read, then how much of the day's furthest point, over every read since
    FIRST_READ, this read has given back (the point's size said when it came before the listed reads), ranked
    against the same read minutes on the prior sessions; seq_day_move_stage's verdict."""
    def prior_giveback(bars: list[dict], then: datetime, _points: float | None) -> float | None:
        price = close_at(bars, then)
        moves = _day_moves(bars, then.date(), reads.earlier, price, 1.0) if price is not None else None
        return _giveback(moves) if moves else None

    share = _giveback(reads.moves)
    rank, why = (rank_sessions(share, same_clock_values(scene, prior_giveback), "the day's move at these read minutes")
                 if share is not None else (None, "the day has not left the settled open at any read"))
    why = _sleep_why(reads, rank, why)
    listed_reads = reads.last(LISTED_READS)
    stood = [f"{signed(m)} ({c:%H:%M})" for m, c in zip(listed_reads.moves, listed_reads.clocks)]
    text = f"{reads.where()}; at the last {len(stood)} reads it stood {listed(stood)}"
    if share == 0:
        text += "; this read is its furthest from the open"
    elif share is not None:
        peak = max(range(len(reads.moves)), key=lambda k: abs(reads.moves[k]))
        size = "" if reads.clocks[peak] in listed_reads.clocks else f"{signed(reads.moves[peak])} "
        text += f"; {pct(share)} of its furthest, {size}at {reads.clocks[peak]:%H:%M}, has been given back"
        if rank is not None:
            text += f", {rank.band} of the last {rank.of} sessions for these reads"
    verdict = None if why else "extending" if share == 0 else "unwinding" if rank.band == "top third" else "stalled"
    ls.put("seq.day_move_by_read", ruled(reads.anchor, f"{text}: {verdict}" if verdict else text))
    _decide(ls, "seq_day_move_stage", verdict, why)


def _day_upvol_share(mk: MarketContext, t: datetime) -> float | None:
    """The day's share of NYSE volume in rising stocks at ``t``, from the $UVOL and $DVOL running totals since
    09:30; None when either is not known within FRESH_MIN minutes of ``t`` or no volume has traded."""
    up, down = mk.last("$UVOL", t, max_age_min=FRESH_MIN), mk.last("$DVOL", t, max_age_min=FRESH_MIN)
    return up / (up + down) if up is not None and down is not None and up + down > 0 else None


def _shares(mk: MarketContext, day: date, clocks: list[time], now: datetime) -> list[float] | None:
    """The day's rising-stock volume share at each of ``clocks`` and at ``now``; None when one is missing."""
    got = [_day_upvol_share(mk, datetime.combine(day, c, tzinfo=ET)) for c in clocks] + [_day_upvol_share(mk, now)]
    return None if any(s is None for s in got) else got


def _breadth_by_read(scene: Scene, reads: Reads, ls: LabelSet) -> None:
    """The day's rising-stock volume share at each of the last reads beside the day's move, and how far it
    shifted toward or away from the day's side across them, its size ranked against the same read minutes on
    the prior sessions (every session with a market context: the ruler never touches the share);
    seq_breadth_drift's verdict checks fading_under_move first, and needs the move not to have shrunk."""
    reads = reads.last(BREADTH_READS)
    shares = _shares(scene.market, reads.day, reads.earlier, scene.now) if scene.market else None
    if shares is None:
        why = "no NYSE up and down volume at every listed read: the market-context job stopped or has not saved it"
        ls.omit("seq.breadth_by_read", why)
        ls.sleep("seq_breadth_drift", why)
        return
    shift = (shares[-1] - shares[0]) * reads.side
    clock = scene.now.astimezone(ET).time()
    base = []
    for day, mk in scene.prior_markets.items():
        d = date.fromisoformat(day)
        got = _shares(mk, d, reads.earlier, datetime.combine(d, clock, tzinfo=ET))
        if got is not None:
            base.append(abs(got[-1] - got[0]))
    rank, why = rank_sessions(abs(shift), base, "NYSE up and down volume at these read minutes")
    why = _sleep_why(reads, rank, why)
    verdict = None
    if why is None:
        big = rank.band == "top third"
        if big and shift < 0 and reads.side * reads.now >= reads.side * reads.moves[0]:
            verdict = "fading_under_move"
        elif big and shift > 0:
            verdict = "building_behind_move"
        else:
            verdict = "tracking"
    text = (f"{reads.where()}, at {listed([f'{c:%H:%M}' for c in reads.clocks])} {listed([signed(m) for m in reads.moves])}, "
            f"while the day's rising-stock volume share went {listed([pct(s) for s in shares])}: "
            f"{abs(shift) * 100:.0f} points {'toward' if shift >= 0 else 'away from'} the day's side")
    if rank is not None:
        text += f", {rank.band} for these reads"
    ends = {"fading_under_move": "fading under the move", "building_behind_move": "building behind the move", "tracking": "tracking the move"}
    ls.put("seq.breadth_by_read", ruled(reads.anchor, f"{text}: {ends[verdict]}" if verdict else text))
    _decide(ls, "seq_breadth_drift", verdict, why)
