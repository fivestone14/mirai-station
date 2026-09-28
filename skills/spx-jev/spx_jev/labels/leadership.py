"""The leadership family: who carries the index, from the market context (market_context.py): equal
weight against cap weight, semis, the megacaps, sector agreement and rotation, size (leaders.*,
sectors.*, tells.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. Every
fund and stock is measured against its usual multiple of the index (usual_link), and every size is ranked
against the same measure at this minute on up to the last 20 sessions, needing 10 (ranks.rank_sessions),
never against a fixed line; a stock's own move against its own (usual_link.OwnMoves). The megacap labels read
the index weights from ``state/spx_leaders/weights.json``, ``{"as_of": day, "weights": {symbol: share of
the index}}``, written from SSGA's SPY holdings; without it they are omitted with the reason.

Not built: the heavyweight gap's earnings tag (the spec's "NVDA reported after yesterday's close"). It waits
for megacap earnings rows in calendar/events.json, which has none and no row format for them yet.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from typing import Callable

from ..cuts import MEGA_COUNT, MEGA_ONE_NAME_SHARE, SPREAD_COUNT, WINDOW_10_MIN, WINDOW_30_MIN
from ..market_context import SYMBOLS
from ..sessions import next_trading_day
from ..state_builder import MarketContext, Scene
from .label_set import LabelSet
from .measures import ET, ONE_MINUTE, SETTLED_OPEN_BAR, bar_time, settled_open, yesterdays_bars
from .ranks import FIFTH_WORDS, SameClockRank, fifth_side, move_rank, rank_days, rank_sessions, same_clock_market
from .rulers import NO_ANCHOR, sigma_anchor
from .usual_link import (SPX, AgainstIndex, OwnMoves, Session, UsualLink, against_usual, beyond, beyond_rank, minutes_back, needs_link,
                         needs_move)
from .words import listed, pct, sig, signed

LABELS = ("leaders.equal_weight_vs_cap_30m", "leaders.heavyweight_gap", "leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m",
          "leaders.rotation_30m", "leaders.semis_vs_index_30m", "leaders.single_name_10m", "leaders.size_spread_day",
          "sectors.agreement_30m", "tells.sector_lead_10m")
GATE_OF = {"leaders.heavyweight_gap": "heavyweight_gap_split", "leaders.single_name_10m": "single_name_shock"}  # each gated label's question
GATES = tuple(GATE_OF.values())
DARK: dict[str, str] = {}

WEIGHTS_FILE = "spx_leaders/weights.json"
# Alphabet's two share classes count as one name, priced by the class the market feed carries.
SHARE_CLASSES = {"GOOG": "GOOGL"}
CAP_WEIGHT, EQUAL_WEIGHT, SEMIS, FINANCIALS = "SPY", "RSP", "SMH", "XLF"
SIZE_FUNDS = ("QQQ", "IWM")
SENSITIVE = (("XLF", "financials"), ("XLY", "discretionary"), ("XLI", "industrials"))
DEFENSIVE = (("XLU", "utilities"), ("XLP", "staples"), ("XLV", "health care"))
BOTTOM_THIRD, TOP_THIRD = "bottom third", "top third"


def build_leadership_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    ruler = sigma_anchor(scene)
    why = ("no market-context snapshot today" if scene.market is None else
           NO_ANCHOR if ruler is None else None)
    if why:
        for path in LABELS:
            _unmeasured(ls, path, why)
        return ls
    against = AgainstIndex(scene, ruler)
    _equal_weight(against, ls)
    _semis(against, ls)
    _sector_tells(against, ls)
    _size_spread(against, ls)
    _sector_agreement(against, ls)
    _rotation(against, ls)
    names = _largest_names(scene)
    if isinstance(names, str):
        for path in ("leaders.heavyweight_gap", "leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m", "leaders.single_name_10m"):
            _unmeasured(ls, path, names)
        return ls
    _heavyweight_gap(against, names, ls)
    _megacaps(against, names, ls)
    _single_name(against, names, ls)
    return ls


def _unmeasured(ls: LabelSet, path: str, why: str) -> None:
    """Omit ``path`` with the reason, and sleep the question it gates, if any."""
    ls.omit(path, why)
    if path in GATE_OF:
        ls.sleep(GATE_OF[path], f"{path} is not measured: {why}")


def _equal_weight(against: AgainstIndex, ls: LabelSet) -> None:
    """RSP's 30-minute move against its usual multiple of SPY's: did the typical stock keep up, the size of the
    split ranked against the same half hour of the prior sessions (top third: a split)."""
    path = "leaders.equal_weight_vs_cap_30m"
    eq, cap = against.move(EQUAL_WEIGHT, WINDOW_30_MIN), against.move(CAP_WEIGHT, WINDOW_30_MIN)
    if eq is None or cap is None:
        ls.omit(path, needs_move([s for s, m in ((EQUAL_WEIGHT, eq), (CAP_WEIGHT, cap)) if m is None], WINDOW_30_MIN))
        return
    link = against.link(EQUAL_WEIGHT, CAP_WEIGHT)
    if link is None:
        ls.omit(path, needs_link([EQUAL_WEIGHT], CAP_WEIGHT))
        return
    expected, actual = against.sigma(link.multiple * cap), against.sigma(eq)
    gap = actual - expected

    def then_split(s: Session, then: datetime, sigma_share: float) -> float | None:
        start = then - timedelta(minutes=WINDOW_30_MIN)
        e, c = s.move(EQUAL_WEIGHT, start, then), s.move(CAP_WEIGHT, start, then)
        return None if e is None or c is None else abs(beyond(link, e, c)) / sigma_share
    rank, why = against.rank(abs(gap), then_split, f"{EQUAL_WEIGHT} and {CAP_WEIGHT} over the half hour to this minute")
    if rank is None:
        ls.omit(path, why)
        return
    ls.put(path, f"equal-weight {EQUAL_WEIGHT} usually moves {link.multiple:.2f} times the index ({CAP_WEIGHT}), so {signed(expected)} sigma "
                 f"was expected over the last {WINDOW_30_MIN} minutes; it {'rose' if actual >= 0 else 'fell'} {sig(abs(actual))}, "
                 f"{sig(abs(gap))} {'above' if gap >= 0 else 'below'} that, by size {rank.words()}, {rank.band}: "
                 f"{'a split' if rank.band == TOP_THIRD else 'no split'}{against.ruler_note}")


def _semis(against: AgainstIndex, ls: LabelSet) -> None:
    path = "leaders.semis_vs_index_30m"
    got = beyond_rank(against, SEMIS, WINDOW_30_MIN)
    if isinstance(got, str):
        ls.omit(path, got)
        return
    value, link, rank = got
    side = fifth_side(rank)
    verdict = {1: "ahead upward", -1: "ahead downward", 0: "in line"}[side]
    ls.put(path, f"chips ({SEMIS}) usually move {link.multiple:.1f} times the index; over the last {WINDOW_30_MIN} minutes they "
                 f"{'beat' if value >= 0 else 'trailed'} that by {sig(abs(value))}{against_usual(value, side)}, {verdict}: "
                 f"{FIFTH_WORDS[side]} for this half hour, {rank.words()}{against.ruler_note}")


def _sector_tells(against: AgainstIndex, ls: LabelSet) -> None:
    """Semis and financials over the last 10 minutes beyond their usual multiple, each ranked by the clock."""
    path = "tells.sector_lead_10m"
    span = minutes_back(against.scene.now, WINDOW_10_MIN)
    parts = []
    for symbol, name in ((SEMIS, "semiconductors"), (FINANCIALS, "financials")):
        got = beyond_rank(against, symbol, span)
        if isinstance(got, str):
            ls.omit(path, got)
            return
        value, _, rank = got
        side = fifth_side(rank)
        verdict = {1: "broke away upward", -1: "broke away downward", 0: "moved in line"}[side]
        parts.append(f"{name} ({symbol}) ran {sig(abs(value))} {'above' if value >= 0 else 'below'} their usual multiple of the index"
                     f"{against_usual(value, side)}, {verdict}, {FIFTH_WORDS[side]} for this time, {rank.words()}")
    ls.put(path, f"over the last {span} minutes " + "; ".join(parts) + against.ruler_note)


def _size_spread(against: AgainstIndex, ls: LabelSet) -> None:
    """QQQ and IWM since the settled open, each beyond its usual multiple of SPY."""
    path = "leaders.size_spread_day"
    scene = against.scene
    start = datetime.combine(date.fromisoformat(scene.day), SETTLED_OPEN_BAR, tzinfo=ET) + ONE_MINUTE
    if scene.now <= start:
        ls.omit(path, f"the settled open is not in until {start:%H:%M}")
        return
    cap = against.today.move(CAP_WEIGHT, start, scene.now)
    moves = {s: against.today.move(s, start, scene.now) for s in SIZE_FUNDS}
    missing = [s for s, m in {**moves, CAP_WEIGHT: cap}.items() if m is None]
    if missing:
        ls.omit(path, f"needs a price for {', '.join(missing)} at the settled open and now")
        return
    links = {s: against.link(s, CAP_WEIGHT) for s in SIZE_FUNDS}
    if any(link is None for link in links.values()):
        ls.omit(path, needs_link(list(SIZE_FUNDS), CAP_WEIGHT))
        return
    ahead = {s: against.sigma(beyond(links[s], moves[s], cap)) for s in SIZE_FUNDS}

    def then_ahead(s: Session, then: datetime, sigma_share: float, fund: str) -> float | None:
        opened = datetime.combine(then.date(), SETTLED_OPEN_BAR, tzinfo=ET) + ONE_MINUTE
        m, c = s.move(fund, opened, then), s.move(CAP_WEIGHT, opened, then)
        return None if m is None or c is None else beyond(links[fund], m, c) / sigma_share
    ranks: dict[str, SameClockRank] = {}
    for f in SIZE_FUNDS:
        rank, why = against.rank(ahead[f], lambda s, then, share, f=f: then_ahead(s, then, share, f),
                                 f"{f} and {CAP_WEIGHT} since the settled open to this minute")
        if rank is None:
            ls.omit(path, why)
            return
        ranks[f] = rank
    first, second = SIZE_FUNDS

    def then_spread(s: Session, then: datetime, sigma_share: float) -> float | None:
        a, b = then_ahead(s, then, sigma_share, first), then_ahead(s, then, sigma_share, second)
        return None if a is None or b is None else abs(a - b)
    spread = ahead[first] - ahead[second]
    spread_rank, why = against.rank(abs(spread), then_spread, f"{first}, {second} and {CAP_WEIGHT} since the settled open to this minute")
    if spread_rank is None:
        ls.omit(path, why)
        return

    def fund(s: str) -> str:
        v, rank = ahead[s], ranks[s]
        side = {TOP_THIRD: 1, BOTTOM_THIRD: -1}.get(rank.band, 0)
        word = {1: "ahead for this time of day", -1: "behind for this time of day", 0: "in line"}[side]
        return f"{s} is {sig(abs(v))} {'ahead' if v >= 0 else 'behind'}{against_usual(v, side)}, {rank.words()}, {rank.band}: {word}"
    if spread_rank.band == TOP_THIRD:
        lead = f"{first if spread > 0 else second} leads by {sig(abs(spread))}, by size {spread_rank.words()}, top third: a lead"
    else:
        lead = f"the two are {sig(abs(spread))} apart, by size {spread_rank.words()}, {spread_rank.band}: neither leads"
    ls.put(path, f"since {start:%H:%M}, after allowing for their usual swing against {CAP_WEIGHT}, {fund(first)}; and {fund(second)}; "
                 f"{lead}{against.ruler_note}")


def _moves_to(s: Session, then: datetime, symbols: tuple[str, ...]) -> tuple[float, dict[str, float]] | None:
    """The index's and each symbol's return over the 30 minutes to ``then`` in session ``s``; None without every price."""
    start = then - timedelta(minutes=WINDOW_30_MIN)
    index_move, moves = s.move(SPX, start, then), {sym: s.move(sym, start, then) for sym in symbols}
    return None if index_move is None or None in moves.values() else (index_move, moves)


def _sector_moves(against: AgainstIndex, symbols: tuple[str, ...]) -> tuple[float, dict[str, float], dict[str, UsualLink]] | str:
    """Each sector fund's 30-minute move in sigma and its link, with the index's move; or the reason not."""
    index_move = against.move(SPX, WINDOW_30_MIN)
    moves = {s: against.move(s, WINDOW_30_MIN) for s in symbols}
    missing = [s for s, m in {SPX: index_move, **moves}.items() if m is None]
    if missing:
        return needs_move(missing, WINDOW_30_MIN)
    links = {s: against.link(s) for s in symbols}
    unlinked = [s for s, link in links.items() if link is None]
    if unlinked:
        return needs_link(unlinked)
    return index_move, moves, links


def _sector_agreement(against: AgainstIndex, ls: LabelSet) -> None:
    path = "sectors.agreement_30m"
    funds = SYMBOLS["sectors"]
    got = _sector_moves(against, funds)
    if isinstance(got, str):
        ls.omit(path, got)
        return
    index_move, moves, links = got
    own = OwnMoves(against.scene, list(funds), WINDOW_30_MIN)
    for s in funds:
        _, why = own.rank(s, moves[s])
        if why:
            ls.omit(path, why)
            return

    def with_index(index_move: float, moves: dict[str, float]) -> int:
        """How many funds moved the index's way past their usual move, each judged against its own at this minute."""
        return sum(1 for s in funds if index_move and (moves[s] > 0) == (index_move > 0) and own.past_usual(s, moves[s]))

    def dispersion(index_move: float, moves: dict[str, float], sigma_share: float) -> float:
        return statistics.pstdev(beyond(links[s], moves[s], index_move) / sigma_share for s in funds)

    def then_count(s: Session, then: datetime, _sigma_share: float) -> float | None:
        got = _moves_to(s, then, funds)
        return None if got is None else with_index(*got)

    def then_dispersion(s: Session, then: datetime, sigma_share: float) -> float | None:
        got = _moves_to(s, then, funds)
        return None if got is None else dispersion(*got, sigma_share)
    count, spread = with_index(index_move, moves), dispersion(index_move, moves, against.sigma_share)
    what = f"every sector fund and {SPX} over the half hour to this minute"
    (count_rank, why), (spread_rank, spread_why) = against.rank(count, then_count, what), against.rank(spread, then_dispersion, what)
    if count_rank is None or spread_rank is None:
        ls.omit(path, why or spread_why)
        return
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes {count} of {len(funds)} sector funds moved the index's way past their usual move "
                 f"(each above the bottom third of its own at this minute); that count is {count_rank.words()}, {count_rank.band}: "
                 f"{'one way' if count_rank.band == TOP_THIRD else 'not one way'}; sector dispersion beyond each fund's usual multiple was "
                 f"{sig(spread)}, {spread_rank.words()}, {spread_rank.band}: {'wide' if spread_rank.band == TOP_THIRD else 'not wide'}"
                 f"{against.ruler_note}")


def _rotation(against: AgainstIndex, ls: LabelSet) -> None:
    path = "leaders.rotation_30m"
    symbols = tuple(s for s, _ in SENSITIVE + DEFENSIVE)
    got = _sector_moves(against, symbols)
    if isinstance(got, str):
        ls.omit(path, got)
        return
    index_move, moves, links = got

    def rotation(index_move: float, moves: dict[str, float], sigma_share: float) -> float:
        """The sensitive sectors' mean move beyond their usual multiple less the defensives', in sigma."""
        def mean_beyond(group: tuple[tuple[str, str], ...]) -> float:
            return statistics.fmean(beyond(links[s], moves[s], index_move) / sigma_share for s, _ in group)
        return mean_beyond(SENSITIVE) - mean_beyond(DEFENSIVE)

    def then_rotation(s: Session, then: datetime, sigma_share: float) -> float | None:
        got = _moves_to(s, then, symbols)
        return None if got is None else rotation(*got, sigma_share)
    gap = rotation(index_move, moves, against.sigma_share)
    rank, why = against.rank(gap, then_rotation, f"every sensitive and defensive sector fund and {SPX} over the half hour to this minute")
    if rank is None:
        ls.omit(path, why)
        return
    side = {TOP_THIRD: 1, BOTTOM_THIRD: -1}.get(rank.band, 0)
    names = [listed([n for _, n in group]) for group in (SENSITIVE, DEFENSIVE)]
    verdict = {1: "the sensitive sectors ahead", -1: "the defensives ahead", 0: "level"}[side]
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes, after allowing for each sector's usual link to the index, {names[0]} "
                 f"{'beat' if gap >= 0 else 'trailed'} {names[1]} by {sig(abs(gap))}{against_usual(gap, side)}, {rank.words()}, "
                 f"{rank.band}: {verdict}{against.ruler_note}")


def _largest_names(scene: Scene) -> list[tuple[str, float]] | str:
    """The MEGA_COUNT largest index stocks and their shares of the index, largest first, from the weights on
    file; or the reason they are not known at this read."""
    if scene.state_dir is None:
        return "no state folder to read the index weights from"
    path = scene.state_dir / WEIGHTS_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"no index weights: {WEIGHTS_FILE} is not written yet"
    except (OSError, ValueError) as e:
        return f"the index weights in {WEIGHTS_FILE} cannot be read: {type(e).__name__}"
    as_of = doc.get("as_of") if isinstance(doc, dict) else None
    if not isinstance(as_of, str) or as_of > scene.day:
        return f"the index weights in {WEIGHTS_FILE} carry no date on or before {scene.day}"
    weights: dict[str, float] = {}
    for symbol, w in (doc.get("weights") or {}).items():
        if isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w < 1:
            name = SHARE_CLASSES.get(symbol, symbol)
            weights[name] = weights.get(name, 0.0) + float(w)
    if len(weights) < MEGA_COUNT:
        return f"the index weights in {WEIGHTS_FILE} name {len(weights)} stocks, fewer than the {MEGA_COUNT} largest"
    return sorted(weights.items(), key=lambda kv: -kv[1])[:MEGA_COUNT]


def _overnight(yesterday: Session, today: Session, closed_at: datetime, open_at: datetime,
               names: list[tuple[str, float]]) -> dict[str, float]:
    """Each name's return from yesterday's close to the settled open, where both prices are known."""
    out = {}
    for symbol, _ in names:
        a, b = yesterday.price(symbol, closed_at), today.price(symbol, open_at)
        if a and b is not None:
            out[symbol] = b / a - 1.0
    return out


def _gap_split(overnight: dict[str, float], names: list[tuple[str, float]], gap: float, sigma_share: float) -> tuple[str, float, float]:
    """The name whose overnight move added most to the index's ``gap`` (a return), what it added and the gap of
    the rest of the index, both in sigma."""
    added = {s: w * overnight[s] / sigma_share for s, w in names}
    name = max(added, key=lambda s: abs(added[s]))
    return name, added[name], gap / sigma_share - added[name]


def _prior_gap_splits(scene: Scene, names: list[tuple[str, float]]) -> list[tuple[float, float]]:
    """Each prior session's largest single-name contribution to its gap and the gap of the rest of the index,
    unsigned, measured as today's (the same names and weights, from the session before's close to its settled
    open, in its own morning anchor), newest first. rank_days leaves out a session whose anchor was estimated; one
    without the trading day before on file, a settled open or every name's price is left out too."""
    days, out = list(scene.prior_bars), []
    for day in rank_days(scene):
        k = days.index(day) + 1
        before = days[k] if k < len(days) else None
        bars, ruler = scene.prior_bars[day], scene.prior_rulers.get(day)
        opened = settled_open(bars)
        if before is None or next_trading_day(date.fromisoformat(before)).isoformat() != day or ruler is None or opened is None:
            continue
        last = scene.prior_bars[before][-1]
        open_at = datetime.combine(date.fromisoformat(day), SETTLED_OPEN_BAR, tzinfo=ET) + ONE_MINUTE
        overnight = _overnight(Session(scene.prior_bars[before], scene.prior_markets.get(before)), Session(bars, scene.prior_markets.get(day)),
                               bar_time(last) + ONE_MINUTE, open_at, names)
        if len(overnight) == len(names):
            _, shock, rest = _gap_split(overnight, names, opened / float(last["close"]) - 1.0, ruler.points / opened)
            out.append((abs(shock), abs(rest)))
    return out


def _heavyweight_gap(against: AgainstIndex, names: list[tuple[str, float]], ls: LabelSet) -> None:
    """Each of the largest stocks' move from yesterday's close to the settled open, as its share of the index's
    gap, and the gap of the rest of the index, each ranked against the same on the prior sessions; the gate wakes
    when one name alone moved the index by a top-third contribution."""
    path = "leaders.heavyweight_gap"
    qid, scene = GATE_OF[path], against.scene
    opened = settled_open(scene.bars)
    if opened is None:
        _unmeasured(ls, path, f"the settled open (the close of the {SETTLED_OPEN_BAR:%H:%M} bar) is not in yet")
        return
    last_day, last_bars, why = yesterdays_bars(scene)
    if last_bars is None:
        _unmeasured(ls, path, why)
        return
    open_at = datetime.combine(date.fromisoformat(scene.day), SETTLED_OPEN_BAR, tzinfo=ET) + ONE_MINUTE
    overnight = _overnight(Session(last_bars, scene.prior_markets.get(last_day)), against.today, bar_time(last_bars[-1]) + ONE_MINUTE,
                           open_at, names)
    missing = [s for s, _ in names if s not in overnight]
    if missing:
        _unmeasured(ls, path, f"needs a price for {', '.join(missing)} at yesterday's close and at the settled open")
        return
    name, shock, rest = _gap_split(overnight, names, opened / float(last_bars[-1]["close"]) - 1.0, against.ruler.points / opened)
    prior = _prior_gap_splits(scene, names)
    what = "yesterday's close, a settled open and every heavyweight's price at both"
    shock_rank, why = rank_sessions(abs(shock), [p for p, _ in prior], what)
    rest_rank, _ = rank_sessions(abs(rest), [r for _, r in prior], what)
    if shock_rank is None or rest_rank is None:
        _unmeasured(ls, path, why)
        return
    weight, ret = dict(names)[name], overnight[name]
    shocked = shock_rank.band == TOP_THIRD
    ls.put(path, f"from yesterday's close to the settled open {name} ({weight * 100:.1f}% of the index) {'rose' if ret >= 0 else 'fell'} "
                 f"{abs(ret) * 100:.1f}%, {'adding' if shock >= 0 else 'taking'} {sig(abs(shock))} {'to' if shock >= 0 else 'from'} the open "
                 f"by itself, larger than {shock_rank.higher_than} of the last {shock_rank.of} sessions' largest single-name gaps, "
                 f"{shock_rank.band}: {'a heavyweight shock' if shocked else 'no heavyweight shock'}; the rest of the index gapped "
                 f"{'up' if rest >= 0 else 'down'} {sig(abs(rest))}, larger than {rest_rank.higher_than} of the last {rest_rank.of} "
                 f"sessions' rest-of-index gaps, {rest_rank.band}: {'the rest barely moved' if rest_rank.band == BOTTOM_THIRD else 'the rest gapped too'}"
                 f"{against.ruler_note}")
    if shocked:
        ls.wake(qid)
    else:
        ls.sleep(qid, f"no one heavyweight's overnight contribution was in the top third of the last {shock_rank.of} sessions' "
                      f"(the largest, {name}, {sig(abs(shock))}, {shock_rank.band})")


def _megacaps(against: AgainstIndex, names: list[tuple[str, float]], ls: LabelSet) -> None:
    """The largest stocks over the last 30 minutes: how many moved together past their usual move, the one that
    supplied most of the index's move, and their summed pull against the rest of the index, each ranked against
    the same half hour of the prior sessions."""
    paths = ("leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m")
    scene = against.scene
    symbols = tuple(s for s, _ in names)
    index_move = against.move(SPX, WINDOW_30_MIN)
    moves = {s: against.move(s, WINDOW_30_MIN) for s in symbols}
    missing = [s for s, m in {SPX: index_move, **moves}.items() if m is None]
    if missing:
        for path in paths:
            ls.omit(path, needs_move(missing, WINDOW_30_MIN))
        return
    _pull_vs_rest(against, names, index_move, moves, ls)

    path = "leaders.megacap_cohesion_30m"
    idx = against.sigma(index_move)
    index_rank, why = move_rank(scene, idx, WINDOW_30_MIN)
    own = OwnMoves(scene, list(symbols), WINDOW_30_MIN)
    why = why or next((w for s in symbols if (w := own.rank(s, moves[s])[1])), None)
    if why:
        ls.omit(path, why)
        return

    def one_way(moves: dict[str, float]) -> tuple[int, int]:
        """How many names rose and how many fell past their usual move, each judged against its own at this minute."""
        past = [s for s in symbols if own.past_usual(s, moves[s])]
        return sum(1 for s in past if moves[s] > 0), sum(1 for s in past if moves[s] < 0)

    def then_together(mk: MarketContext, then: datetime) -> float | None:
        s, start = Session([], mk), then - timedelta(minutes=WINDOW_30_MIN)
        m = {sym: s.move(sym, start, then) for sym in symbols}
        return None if None in m.values() else max(one_way(m))
    up, down = one_way(moves)
    together_rank, why = rank_sessions(max(up, down), same_clock_market(scene, then_together),
                                       f"every one of the {len(names)} largest stocks over the half hour to this minute")
    if together_rank is None:
        ls.omit(path, why)
        return
    together = (f"moving together {'up' if up > down else 'down'}" if together_rank.band == TOP_THIRD and up != down else
                "not moving together")
    added = {s: against.sigma(w * moves[s]) for s, w in names}
    top = max(symbols, key=lambda s: added[s] if idx >= 0 else -added[s])
    if idx == 0 or (added[top] > 0) != (idx > 0):
        one = "no single name moved the index's way"
    else:
        part = added[top] / idx
        one = (f"{top} alone supplied {pct(part)} of the index's {sig(abs(idx))} {'rise' if idx > 0 else 'fall'}, "
               f"{'past' if part > MEGA_ONE_NAME_SHARE else 'under'} the {pct(MEGA_ONE_NAME_SHARE)} one-name share")
    real = "no real move" if index_rank.band == BOTTOM_THIRD else "a real move"
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes {up} of the {len(names)} largest stocks rose past their usual move and {down} fell "
                 f"past it (each above the bottom third of its own at this minute); the larger count, {max(up, down)}, is "
                 f"{together_rank.words()}, {together_rank.band}: {together}; {one}; the index's move was {index_rank.words()}, {index_rank.band}: {real}"
                 f"{against.ruler_note}")


def _pull_vs_rest(against: AgainstIndex, names: list[tuple[str, float]], index_move: float, moves: dict[str, float],
                  ls: LabelSet) -> None:
    """The largest names' summed pull on the index over the last 30 minutes and the rest's, each ranked by size
    against the same half hour of the prior sessions: a part moved when it is above the bottom third."""
    path = "leaders.pull_vs_rest_30m"
    symbols = tuple(s for s, _ in names)

    def parts(index_move: float, moves: dict[str, float], sigma_share: float) -> tuple[float, float]:
        lead = sum(w * moves[s] for s, w in names) / sigma_share
        return lead, index_move / sigma_share - lead

    def then_part(k: int) -> Callable[[Session, datetime, float], float | None]:
        def measure(s: Session, then: datetime, sigma_share: float) -> float | None:
            got = _moves_to(s, then, symbols)
            return None if got is None else abs(parts(*got, sigma_share)[k])
        return measure
    lead, rest = parts(index_move, moves, against.sigma_share)
    what = f"every one of the {len(names)} largest stocks and {SPX} over the half hour to this minute"
    (lead_rank, why), (rest_rank, _) = against.rank(abs(lead), then_part(0), what), against.rank(abs(rest), then_part(1), what)
    if lead_rank is None or rest_rank is None:
        ls.omit(path, why)
        return
    moved_lead, moved_rest = lead_rank.band != BOTTOM_THIRD, rest_rank.band != BOTTOM_THIRD
    passed = (f"both parts moved (each above the bottom third), {'the same way' if (lead > 0) == (rest > 0) else 'in opposite directions'}"
              if moved_lead and moved_rest else
              "only the largest names moved (above the bottom third)" if moved_lead else
              "only the rest moved (above the bottom third)" if moved_rest else
              "neither part moved (both in the bottom third)")
    share = sum(w for _, w in names)
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes the {len(names)} largest names ({pct(share)} of the index) contributed {signed(lead)} "
                 f"sigma, by size {lead_rank.words()}, {lead_rank.band}, and the other {pct(1 - share)} contributed {signed(rest)} sigma, "
                 f"by size {rest_rank.words()}, {rest_rank.band}; {passed}{against.ruler_note}")


def _single_name(against: AgainstIndex, names: list[tuple[str, float]], ls: LabelSet) -> None:
    """The largest stocks' 10-minute moves, each ranked against its own at this minute: the one furthest past its
    own is a name shock when it is larger than every one of the prior sessions', and whether the other large
    names went the same way by a top-third move of their own; the gate wakes on a name shock."""
    path = "leaders.single_name_10m"
    span = minutes_back(against.scene.now, WINDOW_10_MIN)
    symbols = [s for s, _ in names]
    moves = {s: against.move(s, span) for s in symbols}
    missing = [s for s, m in moves.items() if m is None]
    if missing:
        _unmeasured(ls, path, needs_move(missing, span))
        return
    own = OwnMoves(against.scene, symbols, span)
    ranks: dict[str, SameClockRank] = {}
    times: dict[str, float] = {}
    for s in symbols:
        rank, why = own.rank(s, moves[s])
        if rank is None:
            _unmeasured(ls, path, why)
            return
        usual = statistics.median(own.sizes[s])
        if not usual:
            _unmeasured(ls, path, f"{s}'s usual {span}-minute move at this minute is zero on the prior sessions")
            return
        ranks[s], times[s] = rank, abs(moves[s]) / usual
    name = max(symbols, key=lambda s: (ranks[s].share, times[s]))
    rank, ret, weight = ranks[name], moves[name], dict(names)[name]
    rose = ret >= 0
    shock = rank.higher_than == rank.of
    others = sum(1 for s in symbols if s != name and ranks[s].band == TOP_THIRD and (moves[s] >= 0) == rose)
    verb = "rose" if rose else "fell"
    spreading = (f"at least the {SPREAD_COUNT}-name spreading count" if others >= SPREAD_COUNT else f"short of the {SPREAD_COUNT}-name spreading count")
    joined = (f"none of the other {len(names) - 1} largest {verb} by a top-third {span}-minute move of its own" if others == 0 else
              f"{others} of the other {len(names) - 1} largest also {verb} by a top-third {span}-minute move of their own")
    ls.put(path, f"in the last {span} minutes {name} {verb} {abs(ret) * 100:.1f}%, {times[name]:.1f} times its usual {span}-minute move for "
                 f"this time of day, by size {rank.words()}, {'every one: a name shock' if shock else 'not every one: no name shock'}, "
                 f"worth {signed(against.sigma(weight * ret))} sigma of SPX at its {weight * 100:.1f}% weight; {joined}, {spreading}"
                 f"{against.ruler_note}")
    if shock:
        ls.wake(GATE_OF[path])
    else:
        ls.sleep(GATE_OF[path], f"none of the {len(names)} largest stocks moved more over the last {span} minutes than on every one of its "
                                f"own recent sessions at this minute (the furthest, {name}, {rank.words()})")
