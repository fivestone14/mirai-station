"""The macro family: markets outside the index running ahead of it (bonds, oil, the pooled macro gap),
and the flows around it (xasset.*, flows.*, close.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. Every
outside market is measured against its usual multiple of the index (usual_link) and ranked against the
same half hour of the prior sessions. A label listed here and not written is omitted by the registry as
not built; the paid feeds are DARK.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta

from ..cuts import BOND_LINK_TIGHT, WINDOW_30_MIN, WINDOW_60_MIN
from ..sessions import session_open
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ONE_MINUTE
from .rulers import sigma_anchor
from .usual_link import (FIFTH_WORDS, SPX, AgainstIndex, Session, against_usual, beyond, beyond_rank, fifth_side, minutes_back, needs_link,
                         needs_move, needs_rank)
from .words import listed, sig

LABELS = ("xasset.bond_gap_30min", "xasset.macro_gap_30min", "xasset.oil_gap_30min", "flows.rebalance_side", "flows.etf_creations",
          "close.moc_imbalance")
GATES: tuple[str, ...] = ()
DARK = {
    "flows.etf_creations": "no intraday ETF creation feed",
    "close.moc_imbalance": "no closing-auction imbalance feed (a paid feed), and no read after 15:32 to use it",
}

REBALANCE_SIDE_UNMEASURED = ("no daily closes of $SPX, TLT or $TNX are saved: the month and quarter gaps are ranked against the "
                             "last 24 of each, and the minute bars on file reach back about 34 sessions")
# Long Treasuries, first the fund, then the ten-year future (under its root, as the market context keeps it).
BONDS = (("TLT", "long Treasury prices (TLT)"), ("/ZN", "ten-year Treasury futures (/ZN)"))
OIL = "USO"
# The outside markets pooled into one lean, each in the words the sentence uses.
COMPLEX = (("TLT", "bonds"), ("HYG", "high-yield credit"), ("USO", "oil"), ("GLD", "gold"), ("/6E", "the euro"))
# A minute-by-minute link over fewer of the hour's minutes than this is noise; over a shorter span, the same share.
MIN_LINK_MINUTES = 45


def build_macro_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    ls.omit("flows.rebalance_side", REBALANCE_SIDE_UNMEASURED)
    ruler = sigma_anchor(scene)
    why = ("no market-context snapshot today" if scene.market is None else
           "no sigma ruler today: no morning anchor, live sigma or VIX at the settled open" if ruler is None else None)
    if why:
        for path in ("xasset.bond_gap_30min", "xasset.macro_gap_30min", "xasset.oil_gap_30min"):
            ls.omit(path, why)
        return ls
    against = AgainstIndex(scene, ruler)
    _bond_gap(against, ls)
    _oil_gap(against, ls)
    _macro_gap(against, ls)
    return ls


def bond_market_closed(day: date) -> bool:
    """SIFMA's full closes on days the stock market opens: Columbus Day (the second Monday of October) and
    Veterans Day (November 11, kept on the Friday when it falls on a Saturday and the Monday on a Sunday)."""
    if day.month == 10:
        return day.weekday() == 0 and 8 <= day.day <= 14
    if day.month == 11:
        veterans = date(day.year, 11, 11)
        kept = veterans + timedelta(days={5: -1, 6: 1}.get(veterans.weekday(), 0))
        return day == kept
    return False


def link_words(end: datetime) -> str:
    """When the link to ``end`` was measured, as a sentence says it: the 10:02 read's hour is the session so far."""
    return "this hour" if minutes_back(end, WINDOW_60_MIN) == WINDOW_60_MIN else f"since {session_open(end) + ONE_MINUTE:%H:%M}"


def link_need(span: int) -> int:
    """The minutes with both prices a link over ``span`` minutes needs."""
    return math.ceil(MIN_LINK_MINUTES * span / WINDOW_60_MIN)


def minute_link(s: Session, symbol: str, end: datetime) -> float | None:
    """The correlation of ``symbol``'s 1-minute returns with SPX's over the hour to ``end`` (minutes_back); None under
    WINDOW_30_MIN minutes of session, over fewer than link_need minutes with both, or when either never moved."""
    span = minutes_back(end, WINDOW_60_MIN)
    if span < WINDOW_30_MIN:
        return None
    pairs = []
    for k in range(span):
        a, b = end - timedelta(minutes=k + 1), end - timedelta(minutes=k)
        x, y = s.move(SPX, a, b), s.move(symbol, a, b)
        if x is not None and y is not None:
            pairs.append((x, y))
    if len(pairs) < link_need(span):
        return None
    mx, my = statistics.fmean(x for x, _ in pairs), statistics.fmean(y for _, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(sxx * syy)


def _bond_gap(against: AgainstIndex, ls: LabelSet) -> None:
    """Long Treasuries over the last 30 minutes beyond their usual multiple of the index, and whether this hour's
    minute-by-minute link to stocks is tight enough to say which way that points."""
    path = "xasset.bond_gap_30min"
    reasons = []
    for symbol, name in BONDS:
        got = beyond_rank(against, symbol, WINDOW_30_MIN)
        if isinstance(got, str):
            reasons.append(got)
            continue
        value, _, rank = got
        corr = minute_link(against.today, symbol, against.scene.now)
        if corr is None:
            reasons.append(needs_minute_link(f"{symbol} and {SPX}", against.scene.now))
            continue
        side = fifth_side(rank)
        tight = abs(corr) >= BOND_LINK_TIGHT
        link = (f"{link_words(against.scene.now)} bond prices and stocks moved {'together' if corr >= 0 else 'in opposite directions'} "
                f"minute by minute "
                f"(link {corr:+.2f}, {'tight, at least' if tight else 'loose, under'} the {BOND_LINK_TIGHT} tight line): "
                f"bond prices up has gone with stocks {'up' if corr >= 0 else 'down'}")
        if bond_market_closed(date.fromisoformat(against.scene.day)):
            verdict = "the Treasury cash market is closed today for a bond-market holiday, so the link is too loose to read"
        elif not tight:
            verdict = "the link is too loose to say which way that points; the Treasury cash market is open"
        elif not side:
            verdict = "bonds are in line with the index; the Treasury cash market is open"
        else:
            verdict = f"so bonds are ahead in the stocks-{'up' if side * corr > 0 else 'down'} direction; the Treasury cash market is open"
        ls.put(path, f"over the last {WINDOW_30_MIN} minutes {name} {'rose more' if value >= 0 else 'fell more'} than the index's move "
                     f"usually brings them{against_usual(value, side)}, {FIFTH_WORDS[side]} for this half hour, {rank.words()}; {link}; "
                     f"{verdict}{against.ruler_note}")
        return
    ls.omit(path, "; or ".join(reasons))


def _oil_gap(against: AgainstIndex, ls: LabelSet) -> None:
    path = "xasset.oil_gap_30min"
    got = beyond_rank(against, OIL, WINDOW_30_MIN)
    if isinstance(got, str):
        ls.omit(path, got)
        return
    value, _, rank = got
    side = fifth_side(rank)
    verdict = {1: "oil ran up", -1: "oil ran down", 0: "an ordinary amount given the index"}[side]
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes crude oil ({OIL}) {'rose more' if value >= 0 else 'fell more'} than the index's "
                 f"move usually brings it{against_usual(value, side)}, {verdict}: {FIFTH_WORDS[side]} for this half hour, "
                 f"{rank.words()}{against.ruler_note}")


def _macro_gap(against: AgainstIndex, ls: LabelSet) -> None:
    """The outside markets pooled: each 30-minute return beyond its usual multiple of the index, in its own usual
    spread and signed by how it has moved with stocks this hour, averaged, and ranked by the clock."""
    path = "xasset.macro_gap_30min"
    index_move = against.move(SPX, WINDOW_30_MIN)
    if index_move is None:
        ls.omit(path, needs_move([SPX], WINDOW_30_MIN))
        return
    moves = {s: against.move(s, WINDOW_30_MIN) for s, _ in COMPLEX}
    missing = [s for s, m in moves.items() if m is None]
    if missing:
        ls.omit(path, needs_move(missing, WINDOW_30_MIN))
        return
    links = {s: against.link(s) for s, _ in COMPLEX}
    unlinked = [s for s, link in links.items() if link is None or link.spread == 0]
    if unlinked:
        ls.omit(path, needs_link(unlinked))
        return

    def lean(s: Session, end: datetime) -> float | None:
        start = end - timedelta(minutes=WINDOW_30_MIN)
        i = s.move(SPX, start, end)
        parts = []
        for symbol, _ in COMPLEX:
            m, corr = s.move(symbol, start, end), minute_link(s, symbol, end)
            if i is None or m is None or corr is None or corr == 0:
                return None
            parts.append(beyond(links[symbol], m, i) / links[symbol].spread * math.copysign(1.0, corr))
        return statistics.fmean(parts)
    value = lean(against.today, against.scene.now)
    if value is None:
        ls.omit(path, needs_minute_link(f"each of {', '.join(s for s, _ in COMPLEX)} and {SPX}", against.scene.now))
        return
    rank, have = against.rank(value, lambda s, then, _sigma_share: lean(s, then))
    if rank is None:
        ls.omit(path, needs_rank("every outside market", have))
        return
    side = fifth_side(rank)
    above = [n for s, n in COMPLEX if beyond(links[s], moves[s], index_move) >= 0]
    below = [n for s, n in COMPLEX if beyond(links[s], moves[s], index_move) < 0]
    idx = against.sigma(index_move)
    sides = [f"{listed(group)} ran {way}" for group, way in ((above, "above"), (below, "below")) if group]
    ran = f"{' and '.join(sides)} what the index's {sig(abs(idx))} {'rise' if idx >= 0 else 'fall'} would match"
    verdict = {1: "ahead of the index in the stocks-up direction", -1: "ahead of the index in the stocks-down direction",
               0: "in line with the index"}[side]
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes {ran}: taken together, each signed by how it moved with stocks "
                 f"{link_words(against.scene.now)}, "
                 f"the complex is {verdict}, {FIFTH_WORDS[side]} for this half hour, {rank.words()}{against.ruler_note}")


def needs_minute_link(what: str, end: datetime) -> str:
    span = minutes_back(end, WINDOW_60_MIN)
    return f"needs {link_need(span)} of the last {span} minutes with {what} moving"
