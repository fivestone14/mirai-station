"""The bitcoin family: micro bitcoin futures (/MBT) against the index. Before the open, bitcoin's night
against the S&P futures' night (overnight.btc_vs_futures) and, on the first session after a weekend or a
holiday, its weekend leg against what it did once the S&P futures reopened (weekend.btc_path); in the
session, its half hour against the index's (xasset.btc_gap_30min), three such half hours in a row
(xasset.btc_gap_streak), how tightly the two trade together today (xasset.btc_link) and its last five
sessions against the index's (xasset.btc_five_day).

Every "which way" is the direction that has gone with stocks rising or falling over the last nights or
sessions, and every size a rank against them, so the learning loop sets the sign. Schwab's bitcoin
history is one stitched front-month series: a move across a roll (rolls.py, the Thursday before the
last-Friday expiry) is never measured. The calendar refuses the Thursday evening before the table can (it
locates a roll only from the next session's bars), and a read whose quote shows a roll the table has not
located yet (rolls.pending) measures nothing on that contract. Each label's sentence, how it is
computed and its source are in spec/question_set.json ``labels``.

Sources: before the open, the night's bars (scene.night) and the same stretch on the last nights in the
overnight store (night_ranks); in the session, today's /MBT quotes in the market context and the prior
sessions' /MBT prices, from their market context when it carries them, else from the overnight store
(each night's file runs to its day's close). A premarket read writes only the two premarket labels and
decides only their gates; a session read only the four session ones.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional, Union

from .. import overnight, rolls
from ..cuts import (MIN_RANK_SESSIONS, NIGHT_RANK_COUNT, OVERNIGHT_RANK_MIN_NIGHTS, THIRD_HI, THIRD_LO, WINDOW_10_MIN, WINDOW_30_MIN,
                    WINDOW_60_MIN)
from ..night_ranks import Move, Night, price_by, prior_window_nights, quoted_contract, rank_night, window_move
from ..sessions import previous_trading_day, session_close
from ..state_builder import MarketContext, Scene, load_bars
from ..story import GLOBEX_REOPEN
from .label_set import LabelSet
from .measures import ET, ONE_MINUTE, bar_time
from .ranks import FIFTH_WORDS, fifth_side, rank_against, same_clock_values
from .usual_link import SPX, Session, UsualLink, beyond, needs_link, needs_move, needs_rank, usual_link
from .words import above_or_below, listed, sig, third

LABELS = ("overnight.btc_vs_futures", "weekend.btc_path", "xasset.btc_gap_30min", "xasset.btc_gap_streak", "xasset.btc_link",
          "xasset.btc_five_day")
GATES = ("btc_overnight_vs_futures", "btc_weekend_path", "btc_gap_30m", "btc_gap_streak", "btc_link_today", "btc_five_day_lead")
DARK: dict[str, str] = {}
# Written only before the open; the session's four are the ones a premarket read leaves out.
PREMARKET = ("overnight.btc_vs_futures", "weekend.btc_path")
GATE_OF = dict(zip(LABELS, GATES))     # each label's question, whose sleep gate the label decides

BITCOIN = overnight.BITCOIN
FUTURES = "/ES"
NAME = f"bitcoin futures ({BITCOIN})"
FIFTH_CUTS = {1: "top fifth", -1: "bottom fifth", 0: "middle fifths"}
STREAK_HALF_HOURS = 3
FIVE_SESSIONS = 5
LINK_FROM = time(9, 35)          # the day's link starts at the first five-minute edge after the opening minutes
LINK_STEP_MIN = 5
MIN_LINK_RETURNS = 11            # the 10:32 read's five-minute returns (09:35 to 10:30), the first read the link is asked at
NO_NORMAL = "every value it is ranked against is zero, so it has no normal size"
KEPT = {"extended": "it kept going", "held": "it held about where the weekend left it", "reversed": "it turned back"}
ROLL_EVENING = (time(17), time(18))  # the Thursday's daily break, where every bitcoin roll in the table landed (17:06 to 18:00)

# What a label hands back: why it cannot be measured, or its sentence, its figure and why its gate sleeps (None: awake).
Got = Union[str, tuple[str, dict, Optional[str]]]


def build_bitcoin_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    for path, got in (_premarket_labels(scene) if scene.premarket else _session_labels(scene)).items():
        qid = GATE_OF[path]
        if isinstance(got, str):
            ls.omit(path, got)
            ls.sleep(qid, got)
            continue
        sentence, figure, asleep = got
        ls.put(path, sentence, figure=figure)
        if asleep:
            ls.sleep(qid, asleep)
        else:
            ls.wake(qid)
    return ls


# ---- shared ---------------------------------------------------------------------------------------

def rank_figure(value: float, cut: str, verdict: str) -> dict:
    return {"kind": "rank", "value": round(value, 2), "cut": cut, "verdict": verdict}


def slope(pairs: list[tuple[float, float]]) -> float | None:
    """The usual multiple of y on x over ``pairs``: the median of the slopes between every two of them (Theil-Sen),
    so one night far out (bitcoin up 5% with futures down) cannot turn which way bitcoin goes with stocks, as it
    turns a least-squares fit over twenty nights; None when x never moved."""
    slopes = [(y2 - y1) / (x2 - x1) for k, (x1, y1) in enumerate(pairs) for x2, y2 in pairs[k + 1:] if x2 != x1]
    return statistics.median(slopes) if slopes else None


def correlation(pairs: list[tuple[float, float]]) -> float | None:
    """The correlation of x and y over ``pairs``; None when either never moved."""
    mx, my = statistics.fmean(x for x, _ in pairs), statistics.fmean(y for _, y in pairs)
    sxx, syy = sum((x - mx) ** 2 for x, _ in pairs), sum((y - my) ** 2 for _, y in pairs)
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(sxx * syy) if sxx and syy else None


def normal(values: list[float]) -> float | None:
    """The median size of ``values``: what a normal one is; None when it is zero."""
    return statistics.median(abs(v) for v in values) or None


def way_of(x: float) -> int:
    """+1 for a value at or above zero, else -1: the side a multiple or a leg points to."""
    return 1 if x >= 0 else -1


def went_with(way: int, over: str) -> str:
    """Which way bitcoin has gone with stocks, the reference every "which way" is said against."""
    return f"over {over} bitcoin up has gone with stocks {'up' if way > 0 else 'down'}"


def ahead(side: int, beside: str) -> str:
    return f"bitcoin is ahead in the stocks-{'up' if side > 0 else 'down'} direction" if side else f"bitcoin is in line with {beside}"


def prior_close(day: date) -> datetime:
    """The prior session's close, the price every night into ``day`` is measured from (16:00, 13:00 after a half day)."""
    return overnight.night_window(day)[0] + timedelta(minutes=overnight.NIGHT_LEAD_MIN)


def read_clock(now: datetime) -> time:
    return now.astimezone(ET).time().replace(second=0, microsecond=0)


def expiry_roll_between(start: datetime, end: datetime) -> date | None:
    """The Thursday before a last-Friday expiry whose ROLL_EVENING falls between ``start`` and ``end``, or None:
    the calendar's roll guard, which holds before the roll table has located the roll or the quote shows it."""
    for d in {start.astimezone(ET).date(), end.astimezone(ET).date()}:
        thursday = rolls.roll_window(BITCOIN, d.year, d.month)[1] - timedelta(days=1)
        lo, hi = (datetime.combine(thursday, t, tzinfo=ET) for t in ROLL_EVENING)
        if start <= hi and end >= lo:
            return thursday
    return None


def rolls_on(thursday: date) -> str:
    return f"{NAME} rolls to the next contract on the evening of Thursday {thursday:%m-%d}, before Friday's expiry"


class NightStore:
    """The overnight store's bitcoin rows, each night's file read once per build. A file holds every symbol,
    a few MB a night, and a read needs only bitcoin's rows, so only the lines naming it are parsed."""

    def __init__(self, state_dir: Path | None):
        self.state_dir = state_dir
        self._rows: dict[str, list[dict]] = {}

    def rows(self, day: str) -> list[dict]:
        if day not in self._rows:
            path = overnight.night_path(self.state_dir, day) if self.state_dir else None
            out = []
            for line in path.read_text(encoding="utf-8").splitlines() if path and path.exists() else []:
                if BITCOIN not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("symbol") == BITCOIN:
                    out.append(row)
            self._rows[day] = out
        return self._rows[day]

    def known(self, day: str) -> list[tuple[datetime, float]]:
        """``(when each bar finished, its close)``, oldest first; a 1-minute bar over a 5-minute one finishing with it."""
        closes: dict[datetime, float] = {}
        for r in sorted(self.rows(day), key=lambda r: -r["bar_minutes"]):
            closes[datetime.fromisoformat(r["ts"]) + timedelta(minutes=r["bar_minutes"])] = float(r["close"])
        return sorted(closes.items())


# ---- before the open ------------------------------------------------------------------------------

def _premarket_labels(scene: Scene) -> dict[str, Got]:
    if scene.state_dir is None:
        return {path: "no overnight store to rank the night against" for path in PREMARKET}
    table = rolls.load(Path(scene.state_dir) / overnight.OVERNIGHT_SUBDIR)
    futures = night_move(scene, table, FUTURES, "S&P futures", prior_close(date.fromisoformat(scene.day)))
    return {"overnight.btc_vs_futures": btc_vs_futures(scene, table, futures), "weekend.btc_path": weekend_path(scene, table, futures)}


def pending_roll(scene: Scene, table: dict, symbol: str, span: str) -> str | None:
    """Why ``span`` cannot be measured when Schwab quotes ``symbol`` under a contract the table has not rolled to."""
    quoted = quoted_contract(scene.state_dir, date.fromisoformat(scene.day), symbol, scene.now)
    if not rolls.pending(table, symbol, quoted):
        return None
    return (f"Schwab quotes {quoted} but the roll table is still on {table['current'][symbol]}: the switch is not located yet, "
            f"so {span} may span two contracts")


def night_move(scene: Scene, table: dict, symbol: str, name: str, start: datetime) -> Move | str:
    """``symbol``'s move from ``start`` to the read on one contract, or why it cannot be measured: a roll
    pending or in the night, or a price at either end more than WINDOW_10_MIN minutes old."""
    refused = pending_roll(scene, table, symbol, f"the {name} night")
    if refused:
        return refused
    move = window_move(scene.night, symbol, start, scene.now)
    if move is None or start - move.start_at > timedelta(minutes=WINDOW_10_MIN):
        return f"no {name} price within {WINDOW_10_MIN} minutes of {start:%A} {start:%H:%M} in the overnight store"
    if scene.now - move.end_at > timedelta(minutes=WINDOW_10_MIN):
        return f"the newest {name} bar finished at {move.end_at:%H:%M}, more than {WINDOW_10_MIN} minutes before the read"
    if not rolls.same_contract(table, symbol, move.start_at, move.end_at):
        return f"{name} rolled to the next contract overnight, so the move since {start:%A} {start:%H:%M} is the spread between two contracts"
    return move


def where_futures(scene: Scene, futures: Move) -> str:
    """Where the S&P futures stand against their price at the prior close, in the pre-open ruler: every premarket label's first clause."""
    x = futures.pct / 100 * float(scene.row["prior_close"]) / scene.sigma
    return f"S&P futures stand {sig(abs(x))} {above_or_below(x)} their {prior_close(date.fromisoformat(scene.day)):%H:%M} price"


def night_start(day: date) -> datetime:
    """Where bitcoin's night against the futures starts: the prior close, or after a weekend or a holiday the close
    of the S&P futures' first minute after their reopen (before it their newest price is the Friday one). Bitcoin
    trades through the weekend and the futures do not, so a night from the Friday close would carry the weekend's
    move (weekend.btc_path's leg) and rank far out against the weeknights."""
    return weekend_edges(day)[1] + ONE_MINUTE if after_break(day) else prior_close(day)


def btc_vs_futures(scene: Scene, table: dict, futures: Move | str) -> Got:
    """Bitcoin's move from the night's start (night_start) to the read beyond its usual multiple of the S&P
    futures' over the same span (fitted on the last nights to the same minute), ranked in fifths against theirs."""
    if isinstance(futures, str):
        return futures
    day, clock = date.fromisoformat(scene.day), read_clock(scene.now)
    start = night_start(day)
    thursday = expiry_roll_between(start, scene.now)
    if thursday:
        return f"{rolls_on(thursday)}, so the night since {start:%A} {start:%H:%M} would be partly the spread between two contracts"
    es = night_move(scene, table, FUTURES, "S&P futures", start) if after_break(day) else futures
    btc = night_move(scene, table, BITCOIN, NAME, start)
    if isinstance(es, str) or isinstance(btc, str):
        return es if isinstance(es, str) else btc

    def window(d: date) -> tuple[datetime, datetime]:
        return night_start(d), datetime.combine(d, clock, tzinfo=ET)
    es_nights = prior_window_nights(scene.state_dir, day, FUTURES, window, table, lambda m: m.pct)
    btc_nights = prior_window_nights(scene.state_dir, day, BITCOIN, window, table, lambda m: m.pct)
    both = [(e, b) for e, b in zip(es_nights, btc_nights) if e.skip is None and b.skip is None]
    if len(both) < OVERNIGHT_RANK_MIN_NIGHTS:
        return (f"bitcoin's gap to the futures not ranked: only {len(both)} of the last {len(es_nights)} nights have both on one "
                f"contract, fewer than {OVERNIGHT_RANK_MIN_NIGHTS}")
    multiple = slope([(e.value, b.value) for e, b in both])
    if multiple is None:
        return "S&P futures did not move on the last nights, so bitcoin's usual multiple of them is unknown"
    gaps = [Night(e.day, b.value - multiple * e.value) if e.skip is None and b.skip is None else Night(e.day, None, e.skip or b.skip)
            for e, b in zip(es_nights, btc_nights)]
    value = btc.pct - multiple * es.pct
    rank = rank_night(value, gaps)[0]          # enough usable nights: counted above
    usual_gap, usual_btc = normal([n.value for n in gaps if n.skip is None]), normal([b.value for _, b in both])
    if usual_gap is None or usual_btc is None:
        return f"bitcoin's night not sized: {NO_NORMAL}"
    side, way = fifth_side(rank), way_of(multiple)
    verdict = {1: "btc_ahead_up", -1: "btc_ahead_down", 0: "in_line"}[side * way]
    since = f"since the S&P futures reopened at {weekend_edges(day)[1]:%H:%M} {start:%A}" if after_break(day) else "since then"
    sentence = (f"{where_futures(scene, futures)}; {since} {NAME} {'rose' if btc.pct >= 0 else 'fell'} "
                f"{abs(btc.pct) / usual_btc:.1f} of its normal nights to this time, {abs(value) / usual_gap:.1f} normal gaps "
                f"{'above' if value >= 0 else 'below'} what the futures' move would match, {FIFTH_WORDS[side]} of the last {rank.of} nights; "
                f"{went_with(way, 'those nights')}, so {ahead(side * way, 'the futures')}")
    asleep = None if side else f"bitcoin's gap to the futures is between the top and bottom fifths of the last {rank.of} nights: in line"
    return sentence, rank_figure(value / usual_gap, FIFTH_CUTS[side], verdict), asleep


def after_break(day: date) -> bool:
    """True on the first session after a weekend or a market holiday."""
    return (day - previous_trading_day(day)).days > 1


def weekend_edges(day: date) -> tuple[datetime, datetime]:
    """The weekend leg before ``day``: from the last cash close to the S&P futures' reopen at 18:00 the evening before."""
    return prior_close(day), datetime.combine(day - timedelta(days=1), GLOBEX_REOPEN, tzinfo=ET)


def weekend_legs(rows: list[dict], close: datetime, reopen: datetime, until: datetime) -> tuple[Move, Move] | None:
    """Bitcoin's weekend leg (``close`` to ``reopen``) and its reopen leg (``reopen`` to ``until``); None when a price
    is missing or stale: at the close or the read over WINDOW_10_MIN minutes old, at the reopen over WINDOW_60_MIN
    (the Saturday maintenance and a quiet Sunday leave long gaps)."""
    weekend, since = window_move(rows, BITCOIN, close, reopen), window_move(rows, BITCOIN, reopen, until)
    if weekend is None or since is None or close - weekend.start_at > timedelta(minutes=WINDOW_10_MIN) \
            or reopen - weekend.end_at > timedelta(minutes=WINDOW_60_MIN) or until - since.end_at > timedelta(minutes=WINDOW_10_MIN):
        return None
    return weekend, since


def prior_weekends(store: NightStore, table: dict, day: date, clock: time) -> tuple[list[Night], list[Night]]:
    """The weekend legs, and the reopen legs to ``clock`` taken their own weekend's way, of the last NIGHT_RANK_COUNT
    first sessions after a weekend or a holiday before ``day`` since CME bitcoin traded round the clock, oldest
    first; legs with a missing or stale price are unmeasured, legs across a roll left out."""
    weekends, reopens, d = [], [], day
    while len(weekends) < NIGHT_RANK_COUNT:
        d = previous_trading_day(d)
        if not after_break(d):
            continue
        close, reopen = weekend_edges(d)
        if close.date() < overnight.CRYPTO_ROUND_THE_CLOCK_FROM.date():
            break
        legs = weekend_legs(store.rows(d.isoformat()), close, reopen, datetime.combine(d, clock, tzinfo=ET))
        if legs is None:
            weekends.append(Night(d.isoformat(), None, "unmeasured"))
            reopens.append(Night(d.isoformat(), None, "unmeasured"))
        elif not rolls.same_contract(table, BITCOIN, legs[0].start_at, legs[1].end_at):
            weekends.append(Night(d.isoformat(), None, "roll"))
            reopens.append(Night(d.isoformat(), None, "roll"))
        else:
            weekends.append(Night(d.isoformat(), legs[0].pct))
            reopens.append(Night(d.isoformat(), legs[1].pct * way_of(legs[0].pct)))
    return weekends[::-1], reopens[::-1]


def weekend_path(scene: Scene, table: dict, futures: Move | str) -> Got:
    """On the first session after a weekend or a holiday: bitcoin's weekend leg ranked in fifths against the last
    weekends', and its reopen leg taken the weekend's way, ranked in thirds against theirs: extended, held or reversed."""
    day = date.fromisoformat(scene.day)
    if not after_break(day):
        return "not the first session after a weekend or a holiday"
    close, reopen = weekend_edges(day)
    if close.date() < overnight.CRYPTO_ROUND_THE_CLOCK_FROM.date():
        return f"CME bitcoin futures trade through the weekend only from {overnight.CRYPTO_ROUND_THE_CLOCK_FROM:%Y-%m-%d}"
    refused = pending_roll(scene, table, BITCOIN, f"the {NAME} night")
    if refused:
        return refused
    thursday = expiry_roll_between(close, scene.now)
    if thursday:
        return f"{rolls_on(thursday)}, so the weekend since the {close:%A} close would be partly the spread between two contracts"
    legs = weekend_legs(scene.night, close, reopen, scene.now)
    if legs is None:
        return (f"no {NAME} price within {WINDOW_10_MIN} minutes of the {close:%A} close or of the read, or within {WINDOW_60_MIN} "
                "minutes of the futures' reopen")
    weekend, since = legs
    if not rolls.same_contract(table, BITCOIN, weekend.start_at, since.end_at):
        return f"{NAME} rolled to the next contract since the {close:%A} close, so the weekend spans two contracts"
    weekends, reopens = prior_weekends(NightStore(scene.state_dir), table, day, read_clock(scene.now))
    weekend_rank, why = rank_night(weekend.pct, weekends)
    if weekend_rank is None:
        return f"the weekend leg not ranked: {why}"
    taken = since.pct * way_of(weekend.pct)
    reopen_rank = rank_night(taken, reopens)[0]        # the same nights are usable in both
    usual_weekend, usual_reopen = normal([n.value for n in weekends if n.skip is None]), normal([n.value for n in reopens if n.skip is None])
    if usual_weekend is None or usual_reopen is None:
        return f"bitcoin's weekend not sized: {NO_NORMAL}"
    side = fifth_side(weekend_rank)
    verdict = "extended" if reopen_rank.share >= THIRD_HI else "reversed" if reopen_rank.share <= THIRD_LO else "held"
    band = f"{third(reopen_rank.share)} third"
    location = f"{where_futures(scene, futures)}; " if isinstance(futures, Move) else ""
    sentence = (f"{location}{NAME} {'rose' if weekend.pct >= 0 else 'fell'} {abs(weekend.pct) / usual_weekend:.1f} of its normal weekends "
                f"from the {close:%A} {close:%H:%M} close to the S&P futures' reopen at {reopen:%H:%M} {reopen:%A}, "
                f"{FIFTH_WORDS[side]} of the last {weekend_rank.of} weekends; since the reopen it has moved {abs(since.pct) / usual_reopen:.1f} "
                f"of a normal reopen leg {'the same way' if taken >= 0 else 'back against it'}, {band} of those weekends' reopen legs "
                f"taken their weekend's way: {KEPT[verdict]}")
    asleep = None if side else (f"an ordinary weekend for bitcoin: its weekend leg is between the top and bottom fifths of the last "
                                f"{weekend_rank.of} weekends")
    return sentence, rank_figure(taken / usual_reopen, band, verdict), asleep


# ---- in the session -------------------------------------------------------------------------------

def _session_labels(scene: Scene) -> dict[str, Got]:
    store = NightStore(scene.state_dir)
    got: dict[str, Got] = {"xasset.btc_five_day": five_day(scene, store)}
    today = ("xasset.btc_gap_30min", "xasset.btc_gap_streak", "xasset.btc_link")
    if scene.market is None or scene.market.last(BITCOIN, scene.now, max_age_min=WINDOW_10_MIN) is None:
        return {**got, **{path: f"no {NAME} price in the last {WINDOW_10_MIN} minutes" for path in today}}
    filled = with_bitcoin(scene, store)
    link = usual_link(filled, BITCOIN)
    if link is None:
        return {**got, **{path: needs_link([BITCOIN]) for path in today}}
    against = BitcoinAgainstIndex(filled, link)
    return {**got, "xasset.btc_gap_30min": gap_30(against), "xasset.btc_gap_streak": gap_streak(against),
            "xasset.btc_link": link_today(against)}


def with_bitcoin(scene: Scene, store: NightStore) -> Scene:
    """The scene with bitcoin's prices put into each prior session's market context that lacks them, from the overnight store."""
    markets = dict(scene.prior_markets)
    for day in scene.prior_bars:
        m = markets.get(day)
        if m is not None and m.known.get(BITCOIN):
            continue
        known = store.known(day)
        if known:
            markets[day] = MarketContext({**(m.known if m else {}), BITCOIN: known}, m.bars if m else {})
    return replace(scene, prior_markets=markets)


class BitcoinAgainstIndex:
    """Bitcoin against the index at one session read: today's prices, the prior sessions' (with_bitcoin), and
    bitcoin's usual multiple of the index's half hours (usual_link)."""

    def __init__(self, scene: Scene, link: UsualLink):
        self.scene, self.link = scene, link
        self.today = Session(scene.bars, scene.market)
        self.way = way_of(link.multiple)

    def excess(self, s: Session, start: datetime, end: datetime) -> float | None:
        """Bitcoin's return from ``start`` to ``end`` beyond its usual multiple of the index's."""
        m, i = s.move(BITCOIN, start, end), s.move(SPX, start, end)
        return None if m is None or i is None else beyond(self.link, m, i)

    def missing(self, start: datetime, end: datetime) -> list[str]:
        return [s for s in (BITCOIN, SPX) if self.today.move(s, start, end) is None]

    def same_clock(self, measure: Callable[[Session, datetime], float | None]) -> list[float]:
        """``measure(session, then)`` on each prior session at this read's minute (ranks.same_clock_values)."""
        return same_clock_values(self.scene, lambda bars, then, _: measure(Session(bars, self.scene.prior_markets.get(then.date().isoformat())),
                                                                           then))

    @property
    def with_stocks(self) -> str:
        return went_with(self.way, f"the last {self.link.sessions} sessions")


def gap_30(a: BitcoinAgainstIndex) -> Got:
    """Bitcoin's last half hour beyond its usual multiple of the index's, ranked in fifths against the same half hour."""
    now, half = a.scene.now, timedelta(minutes=WINDOW_30_MIN)
    value = a.excess(a.today, now - half, now)
    if value is None:
        return needs_move(a.missing(now - half, now), WINDOW_30_MIN)
    base = a.same_clock(lambda s, then: a.excess(s, then - half, then))
    rank = rank_against(value, base)
    if rank is None:
        return needs_rank(f"{BITCOIN} and {SPX}", len(base))
    usual = normal(base)
    if usual is None:
        return f"bitcoin's half hour not sized: {NO_NORMAL}"
    side = fifth_side(rank)
    verdict = {1: "btc_ahead_up", -1: "btc_ahead_down", 0: "in_line"}[side * a.way]
    sentence = (f"over the last {WINDOW_30_MIN} minutes {NAME} {'rose' if value >= 0 else 'fell'} {abs(value) / usual:.1f} times its normal "
                f"half-hour gap more than the index's move usually brings it, {FIFTH_WORDS[side]} for this half hour, {rank.words()}; "
                f"{a.with_stocks}, so {ahead(side * a.way, 'the index')}")
    asleep = None if side else "bitcoin's half hour beyond the index is between the top and bottom fifths for this half hour"
    return sentence, rank_figure(value / usual, FIFTH_CUTS[side], verdict), asleep


def gap_streak(a: BitcoinAgainstIndex) -> Got:
    """The last three half hours beyond the index: whether they all point one way, and their sum ranked in thirds
    against the same 90 minutes of the prior sessions."""
    now, half = a.scene.now, timedelta(minutes=WINDOW_30_MIN)
    ends = [now - k * half for k in range(STREAK_HALF_HOURS - 1, -1, -1)]
    parts = [a.excess(a.today, end - half, end) for end in ends]
    if any(p is None for p in parts):
        return f"needs a price for {BITCOIN} and {SPX} at every edge of the last {STREAK_HALF_HOURS} half hours"

    def then_total(s: Session, then: datetime) -> float | None:
        got = [a.excess(s, then - (k + 1) * half, then - k * half) for k in range(STREAK_HALF_HOURS)]
        return None if any(g is None for g in got) else sum(got)
    total = sum(parts)
    base = a.same_clock(then_total)
    rank = rank_against(total, base)
    if rank is None:
        return needs_rank(f"{BITCOIN} and {SPX}", len(base))
    usual = normal(base)
    if usual is None:
        return f"bitcoin's half hours not sized: {NO_NORMAL}"
    one_way = len({p > 0 for p in parts}) == 1
    side = (1 if total > 0 and rank.share >= THIRD_HI else -1 if total < 0 and rank.share <= THIRD_LO else 0) if one_way else 0
    verdict = {1: "streak_up", -1: "streak_down", 0: "no_streak"}[side * a.way]
    moved = (f"{'rose' if total > 0 else 'fell'} more than the index's move usually brings it each time" if one_way else
             f"went beyond the index's move by turns ({', '.join('rose more' if p > 0 else 'fell more' for p in parts)})")
    streak = f"a streak in the stocks-{'up' if side * a.way > 0 else 'down'} direction" if side else "no streak"
    sentence = (f"over the last {STREAK_HALF_HOURS} half hours (to {listed([f'{e:%H:%M}' for e in ends])}) {NAME} {moved}; together "
                f"{abs(total) / usual:.1f} times a normal {STREAK_HALF_HOURS * WINDOW_30_MIN}-minute gap, {third(rank.share)} third for this time, "
                f"{rank.words()}; {a.with_stocks}, so {streak}")
    asleep = None if side else ("the last three half hours point different ways" if not one_way else
                                "the three half hours together are not in the outer third they point to for this time")
    return sentence, rank_figure(total / usual, f"{third(rank.share)} third", verdict), asleep


def five_minute_link(s: Session, end: datetime) -> float | None:
    """The correlation of bitcoin's five-minute returns with the index's from LINK_FROM to ``end``; None under
    MIN_LINK_RETURNS returns with both, or when either never moved."""
    step = timedelta(minutes=LINK_STEP_MIN)
    t, pairs = end.replace(hour=LINK_FROM.hour, minute=LINK_FROM.minute, second=0, microsecond=0), []
    while t + step <= end:
        x, y = s.move(SPX, t, t + step), s.move(BITCOIN, t, t + step)
        if x is not None and y is not None:
            pairs.append((x, y))
        t += step
    return correlation(pairs) if len(pairs) >= MIN_LINK_RETURNS else None


def link_today(a: BitcoinAgainstIndex) -> Got:
    """How tightly bitcoin and the index have moved together since LINK_FROM, ranked in thirds against the same span."""
    corr = five_minute_link(a.today, a.scene.now)
    if corr is None:
        return f"needs {MIN_LINK_RETURNS} five-minute returns of {BITCOIN} and {SPX} since {LINK_FROM:%H:%M}"
    base = a.same_clock(five_minute_link)
    rank = rank_against(corr, base)
    if rank is None:
        return needs_rank(f"{BITCOIN} and {SPX}", len(base))
    verdict = "tight" if rank.share >= THIRD_HI else "loose" if rank.share <= THIRD_LO else "usual"
    how = {"tight": "more tightly than on most recent sessions", "loose": "more loosely than on most recent sessions",
           "usual": "about as tightly as usual"}[verdict]
    band = f"{third(rank.share)} third"
    sentence = (f"since {LINK_FROM:%H:%M} {NAME} and the index have moved together five minutes by five minutes {how}: link {corr:+.2f}, "
                f"{rank.words()}, {band}; {a.with_stocks}")
    asleep = None if verdict != "usual" else "today's link is in the middle third for this time"
    return sentence, rank_figure(corr, band, verdict), asleep


def session_closes(scene: Scene, store: NightStore, day: date, count: int) -> list[tuple[datetime, float | None, float | None]]:
    """``(close, the index's close, bitcoin's)`` on the ``count`` trading days before ``day``, oldest first: the index's
    last bar when it finished at the session's close, bitcoin's newest bar by then when it finished within WINDOW_10_MIN."""
    out, d = [], day
    for _ in range(count):
        d = previous_trading_day(d)
        at = session_close(datetime.combine(d, time(12), tzinfo=ET))
        bars = scene.prior_bars.get(d.isoformat()) or load_bars(scene.state_dir, d.isoformat())
        spx = float(bars[-1]["close"]) if bars and bar_time(bars[-1]) + ONE_MINUTE == at else None
        btc = price_by(store.rows(d.isoformat()), BITCOIN, at)
        out.append((at, spx, btc[0] if btc and at - btc[1] <= timedelta(minutes=WINDOW_10_MIN) else None))
    return out[::-1]


def five_day(scene: Scene, store: NightStore) -> Got:
    """Bitcoin's last five sessions, close to close, beyond its usual daily multiple of the index's, ranked in fifths
    against the five sessions ending on each of the last NIGHT_RANK_COUNT sessions before."""
    if scene.state_dir is None:
        return "no overnight store to read bitcoin's session closes from"
    table = rolls.load(Path(scene.state_dir) / overnight.OVERNIGHT_SUBDIR)
    refused = pending_roll(scene, table, BITCOIN, f"{NAME}'s last {FIVE_SESSIONS} sessions")
    if refused:
        return refused
    closes = session_closes(scene, store, date.fromisoformat(scene.day), NIGHT_RANK_COUNT + FIVE_SESSIONS + 1)

    def move(a: tuple, b: tuple) -> tuple[float, float] | None:
        """The index's and bitcoin's returns from close ``a`` to close ``b``, when both have both prices on one contract."""
        if None in (a[1], a[2], b[1], b[2]) or not rolls.same_contract(table, BITCOIN, a[0], b[0]) or expiry_roll_between(a[0], b[0]):
            return None
        return b[1] / a[1] - 1.0, b[2] / a[2] - 1.0
    daily = [m for a, b in zip(closes, closes[1:]) if (m := move(a, b))]
    if len(daily) < MIN_RANK_SESSIONS:
        return f"needs {MIN_RANK_SESSIONS} daily moves of {BITCOIN} and {SPX} on one contract to know the usual multiple, have {len(daily)}"
    multiple = slope(daily)
    if multiple is None:
        return "the index's close did not move over the last sessions, so bitcoin's usual multiple of it is unknown"
    start, end = closes[-1 - FIVE_SESSIONS], closes[-1]
    if None in (start[1], start[2], end[1], end[2]):
        return f"needs a close of the index and of {NAME} {FIVE_SESSIONS} sessions ago and at the last one"
    if not rolls.same_contract(table, BITCOIN, start[0], end[0]):
        return f"{NAME} rolled to the next contract inside the last {FIVE_SESSIONS} sessions, so their move is partly the spread between two contracts"
    thursday = expiry_roll_between(start[0], end[0])
    if thursday:
        return f"{rolls_on(thursday)}, so the last {FIVE_SESSIONS} sessions' move would be partly the spread between two contracts"
    index, btc = move(start, end)
    value = btc - multiple * index
    prior = [m for k in range(FIVE_SESSIONS, len(closes) - 1) if (m := move(closes[k - FIVE_SESSIONS], closes[k]))]
    base = [b - multiple * i for i, b in prior]
    rank = rank_against(value, base)
    if rank is None:
        return f"needs {MIN_RANK_SESSIONS} prior {FIVE_SESSIONS}-session stretches with {BITCOIN} and {SPX} on one contract, have {len(base)}"
    usual, usual_index = normal(base), normal([i for i, _ in prior])
    if usual is None or usual_index is None:
        return f"bitcoin's five sessions not sized: {NO_NORMAL}"
    side = fifth_side(rank)
    verdict = {1: "btc_ahead", -1: "btc_lagging", 0: "in_line"}[side]
    sentence = (f"over the last {FIVE_SESSIONS} sessions {NAME} did {abs(value) / usual:.1f} normal weeks {'better' if value >= 0 else 'worse'} "
                f"than the index's move would match, {FIFTH_WORDS[side]} of the last {rank.of} {FIVE_SESSIONS}-session stretches, higher than "
                f"{rank.higher_than} of them; the index itself {'rose' if index >= 0 else 'fell'} {abs(index) / usual_index:.1f} of its normal week")
    asleep = None if side else f"bitcoin's five sessions against the index are between the top and bottom fifths of the last {rank.of}"
    return sentence, rank_figure(value / usual, FIFTH_CUTS[side], verdict), asleep
