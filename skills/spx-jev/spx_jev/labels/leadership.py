"""The leadership family: who carries the index, from the market context (market_context.py): equal
weight against cap weight, semis, the megacaps, sector agreement and rotation, size (leaders.*,
sectors.*, tells.*).

Each label's sentence, how it is computed and its source are in spec/question_set.json ``labels``. Every
fund and stock is measured against its usual multiple of the index (usual_link). The megacap labels read
the index weights from ``state/spx_leaders/weights.json``, ``{"as_of": day, "weights": {symbol: share of
the index}}``, written from SSGA's SPY holdings; without it they are omitted with the reason.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta

from ..cuts import (DISPERSION_WIDE_SIGMA, EQW_SPLIT_SIGMA, HEAVY_SHOCK_SIGMA, MEGA_COUNT, MEGA_ONE_NAME_SHARE, MEGA_TOGETHER,
                    MIN_RANK_SESSIONS, MOVE_RULE_SIGMA, NAME_SHOCK_MULT, PULL_RULE_SIGMA, REST_GAP_SIGMA, ROTATION_GAP_SIGMA,
                    SECTOR_ONE_WAY, SIZE_RESID_SIGMA, SIZE_SPREAD_SIGMA, SPREAD_COUNT, WINDOW_10_MIN, WINDOW_30_MIN)
from ..market_context import SYMBOLS
from ..sessions import next_trading_day
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ET, ONE_MINUTE, SETTLED_OPEN_BAR, bar_time, settled_open
from .rulers import sigma_anchor
from .usual_link import (FIFTH_WORDS, SPX, AgainstIndex, Session, UsualLink, beyond, beyond_rank, fifth_side, needs_link, needs_move,
                         needs_rank)
from .words import pct, sig, signed

LABELS = ("leaders.equal_weight_vs_cap_30m", "leaders.heavyweight_gap", "leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m",
          "leaders.rotation_30m", "leaders.semis_vs_index_30m", "leaders.single_name_10m", "leaders.size_spread_day",
          "sectors.agreement_30m", "tells.sector_lead_10m")
GATES = ("heavyweight_gap_split",)
DARK: dict[str, str] = {}

WEIGHTS_FILE = "spx_leaders/weights.json"
# Alphabet's two share classes count as one name, priced by the class the market feed carries.
SHARE_CLASSES = {"GOOG": "GOOGL"}
CAP_WEIGHT, EQUAL_WEIGHT, SEMIS, FINANCIALS = "SPY", "RSP", "SMH", "XLF"
SIZE_FUNDS = ("QQQ", "IWM")
SENSITIVE = (("XLF", "financials"), ("XLY", "discretionary"), ("XLI", "industrials"))
DEFENSIVE = (("XLU", "utilities"), ("XLP", "staples"), ("XLV", "health care"))


def build_leadership_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    ruler = sigma_anchor(scene)
    why = ("no market-context snapshot today" if scene.market is None else
           "no sigma ruler today: no morning anchor, live sigma or VIX at the settled open" if ruler is None else None)
    if why:
        for path in LABELS:
            ls.omit(path, why)
        ls.sleep("heavyweight_gap_split", f"leaders.heavyweight_gap is not measured: {why}")
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
            ls.omit(path, names)
        ls.sleep("heavyweight_gap_split", f"leaders.heavyweight_gap is not measured: {names}")
        return ls
    _heavyweight_gap(against, names, ls)
    _megacaps(against, names, ls)
    _single_name(against, names, ls)
    return ls


def _equal_weight(against: AgainstIndex, ls: LabelSet) -> None:
    """RSP's 30-minute move against its usual multiple of SPY's: did the typical stock keep up."""
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
    rule = f"more than the {EQW_SPLIT_SIGMA} sigma split rule" if abs(gap) > EQW_SPLIT_SIGMA else f"within the {EQW_SPLIT_SIGMA} sigma split rule"
    ls.put(path, f"equal-weight {EQUAL_WEIGHT} usually moves {link.multiple:.2f} times the index ({CAP_WEIGHT}), so {signed(expected)} sigma "
                 f"was expected over the last {WINDOW_30_MIN} minutes; it {'rose' if actual >= 0 else 'fell'} {sig(abs(actual))}, "
                 f"{sig(abs(gap))} {'above' if gap >= 0 else 'below'} that, {rule}{against.ruler_note}")


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
                 f"{'beat' if value >= 0 else 'trailed'} that by {sig(abs(value))}{against_usual(value, side)}, {verdict}: {FIFTH_WORDS[side]} for this half hour, "
                 f"{rank.words()}{against.ruler_note}")


def _sector_tells(against: AgainstIndex, ls: LabelSet) -> None:
    """Semis and financials over the last 10 minutes beyond their usual multiple, each ranked by the clock."""
    path = "tells.sector_lead_10m"
    parts = []
    for symbol, name in ((SEMIS, "semiconductors"), (FINANCIALS, "financials")):
        got = beyond_rank(against, symbol, WINDOW_10_MIN)
        if isinstance(got, str):
            ls.omit(path, got)
            return
        value, _, rank = got
        side = fifth_side(rank)
        verdict = {1: "broke away upward", -1: "broke away downward", 0: "moved in line"}[side]
        parts.append(f"{name} ({symbol}) ran {sig(abs(value))} {'above' if value >= 0 else 'below'} their usual multiple of the index"
                     f"{against_usual(value, side)}, {verdict}, {FIFTH_WORDS[side]} for this time, {rank.words()}")
    ls.put(path, f"over the last {WINDOW_10_MIN} minutes " + "; ".join(parts) + against.ruler_note)


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

    def fund(s: str) -> str:
        v = ahead[s]
        if abs(v) > SIZE_RESID_SIGMA:
            return f"{s} is {sig(abs(v))} {'ahead' if v > 0 else 'behind'}, past the {SIZE_RESID_SIGMA} sigma own-swing rule"
        return f"{s} is in line, {sig(abs(v))} {'ahead' if v >= 0 else 'behind'}, within the {SIZE_RESID_SIGMA} sigma own-swing rule"
    first, second = SIZE_FUNDS
    spread = ahead[first] - ahead[second]
    if abs(spread) > SIZE_SPREAD_SIGMA:
        lead = f"{first if spread > 0 else second} leads by {sig(abs(spread))}, past the {SIZE_SPREAD_SIGMA} sigma leadership rule"
    else:
        lead = f"the two are {sig(abs(spread))} apart, within the {SIZE_SPREAD_SIGMA} sigma leadership rule"
    ls.put(path, f"since {start:%H:%M}, after allowing for their usual swing against {CAP_WEIGHT}, {fund(first)}, and {fund(second)}; "
                 f"{lead}{against.ruler_note}")


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
    idx = against.sigma(index_move)
    with_index = sum(1 for s in funds if idx and (moves[s] > 0) == (idx > 0) and abs(against.sigma(moves[s])) > MOVE_RULE_SIGMA * links[s].scale)
    count = (f"at least the {SECTOR_ONE_WAY}-fund one-way count" if with_index >= SECTOR_ONE_WAY else
             f"short of the {SECTOR_ONE_WAY}-fund one-way count")
    spread = statistics.pstdev(against.sigma(beyond(links[s], moves[s], index_move)) for s in funds)
    wide = f"past the {DISPERSION_WIDE_SIGMA} sigma wide line" if spread > DISPERSION_WIDE_SIGMA else f"under the {DISPERSION_WIDE_SIGMA} sigma wide line"
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes {with_index} of {len(funds)} sector funds moved the index's way past their own move rule, "
                 f"{count}; sector dispersion beyond each fund's usual multiple was {sig(spread)}, {wide}{against.ruler_note}")


def _rotation(against: AgainstIndex, ls: LabelSet) -> None:
    path = "leaders.rotation_30m"
    got = _sector_moves(against, tuple(s for s, _ in SENSITIVE + DEFENSIVE))
    if isinstance(got, str):
        ls.omit(path, got)
        return
    index_move, moves, links = got

    def mean_beyond(group: tuple[tuple[str, str], ...]) -> float:
        return statistics.fmean(against.sigma(beyond(links[s], moves[s], index_move)) for s, _ in group)
    gap = mean_beyond(SENSITIVE) - mean_beyond(DEFENSIVE)
    names = [listed([n for _, n in group]) for group in (SENSITIVE, DEFENSIVE)]
    rule = f"past the {ROTATION_GAP_SIGMA} sigma rotation rule" if abs(gap) > ROTATION_GAP_SIGMA else f"within the {ROTATION_GAP_SIGMA} sigma rotation rule"
    ls.put(path, f"over the last {WINDOW_30_MIN} minutes, after allowing for each sector's usual link to the index, {names[0]} "
                 f"{'beat' if gap >= 0 else 'trailed'} {names[1]} by {sig(abs(gap))}, {rule}{against.ruler_note}")


def against_usual(value: float, side: int) -> str:
    """What a sentence adds when a move beyond the usual multiple and its same-clock fifth point opposite ways
    (the prior sessions at this minute sat mostly on one side of zero), so the fifth's verdict reads true."""
    if not side or (value >= 0) == (side > 0):
        return ""
    return f", {'above' if side > 0 else 'below'} the usual for this minute"


def listed(names: list[str]) -> str:
    """Names as a sentence lists them, the same in both families: "a, b and c"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


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


def _heavyweight_gap(against: AgainstIndex, names: list[tuple[str, float]], ls: LabelSet) -> None:
    """Each of the largest stocks' move from yesterday's close to the settled open, as its share of the index's
    gap, and the gap of the rest of the index; the gate wakes when one name alone moved the index past the
    shock rule."""
    path, qid = "leaders.heavyweight_gap", "heavyweight_gap_split"
    scene = against.scene
    opened = settled_open(scene.bars)
    if opened is None:
        why = f"the settled open (the close of the {SETTLED_OPEN_BAR:%H:%M} bar) is not in yet"
        ls.omit(path, why)
        ls.sleep(qid, f"{path} is not measured: {why}")
        return
    if not scene.prior_bars:
        why = "no prior session's bars for yesterday's close"
        ls.omit(path, why)
        ls.sleep(qid, f"{path} is not measured: {why}")
        return
    last_day, last_bars = next(iter(scene.prior_bars.items()))
    if next_trading_day(date.fromisoformat(last_day)).isoformat() != scene.day:
        why = f"yesterday's bars are not on file: the newest stored session is {last_day}"
        ls.omit(path, why)
        ls.sleep(qid, f"{path} is not measured: {why}")
        return
    closed_at = bar_time(last_bars[-1]) + ONE_MINUTE
    yesterday = Session(last_bars, scene.prior_markets.get(last_day))
    open_at = datetime.combine(date.fromisoformat(scene.day), SETTLED_OPEN_BAR, tzinfo=ET) + ONE_MINUTE
    overnight = {}
    for symbol, _ in names:
        a, b = yesterday.price(symbol, closed_at), against.today.price(symbol, open_at)
        if a and b is not None:
            overnight[symbol] = b / a - 1.0
    missing = [s for s, _ in names if s not in overnight]
    if missing:
        why = f"needs a price for {', '.join(missing)} at yesterday's close and at the settled open"
        ls.omit(path, why)
        ls.sleep(qid, f"{path} is not measured: {why}")
        return
    gap = against.sigma(opened / float(last_bars[-1]["close"]) - 1.0)
    added = {s: against.sigma(w * overnight[s]) for s, w in names}
    name, weight = max(names, key=lambda sw: abs(added[sw[0]]))
    shock, ret = added[name], overnight[name]
    rest = gap - shock
    rule = f"past the {HEAVY_SHOCK_SIGMA} sigma shock rule" if abs(shock) >= HEAVY_SHOCK_SIGMA else f"under the {HEAVY_SHOCK_SIGMA} sigma shock rule"
    rest_rule = f"past the {REST_GAP_SIGMA} sigma rest-gap rule" if abs(rest) > REST_GAP_SIGMA else f"within the {REST_GAP_SIGMA} sigma rest-gap rule"
    ls.put(path, f"from yesterday's close to the settled open {name} ({weight * 100:.1f}% of the index) {'rose' if ret >= 0 else 'fell'} "
                 f"{abs(ret) * 100:.1f}%, {'adding' if shock >= 0 else 'taking'} {sig(abs(shock))} {'to' if shock >= 0 else 'from'} the open "
                 f"by itself, {rule}; the rest of the index gapped {'up' if rest >= 0 else 'down'} {sig(abs(rest))}, {rest_rule}"
                 f"{against.ruler_note}")
    if abs(shock) >= HEAVY_SHOCK_SIGMA:
        ls.wake(qid)
    else:
        ls.sleep(qid, f"no one heavyweight moved the index past the {HEAVY_SHOCK_SIGMA} sigma shock rule overnight "
                      f"(the largest, {name}, {sig(abs(shock))})")


def _megacaps(against: AgainstIndex, names: list[tuple[str, float]], ls: LabelSet) -> None:
    """The largest stocks over the last 30 minutes: how many moved together past their own move rule, the
    one that supplied most of the index's move, and their summed pull against the rest of the index."""
    paths = ("leaders.megacap_cohesion_30m", "leaders.pull_vs_rest_30m")
    index_move = against.move(SPX, WINDOW_30_MIN)
    moves = {s: against.move(s, WINDOW_30_MIN) for s, _ in names}
    missing = [s for s, m in {SPX: index_move, **moves}.items() if m is None]
    if missing:
        for path in paths:
            ls.omit(path, needs_move(missing, WINDOW_30_MIN))
        return
    idx = against.sigma(index_move)
    added = {s: against.sigma(w * moves[s]) for s, w in names}
    lead = sum(added.values())
    rest = idx - lead
    share = sum(w for _, w in names)
    both = abs(lead) > PULL_RULE_SIGMA and abs(rest) > PULL_RULE_SIGMA
    passed = (f"both passed the {PULL_RULE_SIGMA} sigma pull rule, {'the same way' if (lead > 0) == (rest > 0) else 'in opposite directions'}" if both else
              f"only the largest names passed the {PULL_RULE_SIGMA} sigma pull rule" if abs(lead) > PULL_RULE_SIGMA else
              f"only the rest passed the {PULL_RULE_SIGMA} sigma pull rule" if abs(rest) > PULL_RULE_SIGMA else
              f"neither passed the {PULL_RULE_SIGMA} sigma pull rule")
    ls.put("leaders.pull_vs_rest_30m", f"over the last {WINDOW_30_MIN} minutes the {len(names)} largest names ({pct(share)} of the index) "
                                       f"contributed {signed(lead)} sigma and the other {pct(1 - share)} contributed {signed(rest)} sigma; "
                                       f"{passed}{against.ruler_note}")

    links = {s: against.link(s) for s, _ in names}
    unlinked = [s for s, link in links.items() if link is None]
    if unlinked:
        ls.omit("leaders.megacap_cohesion_30m", needs_link(unlinked))
        return
    rules = {s: MOVE_RULE_SIGMA * links[s].scale for s, _ in names}
    up = sum(1 for s, _ in names if against.sigma(moves[s]) > rules[s])
    down = sum(1 for s, _ in names if against.sigma(moves[s]) < -rules[s])
    together = (f"at least the {MEGA_TOGETHER}-name together count" if max(up, down) >= MEGA_TOGETHER else
                f"short of the {MEGA_TOGETHER}-name together count")
    top = max(names, key=lambda sw: added[sw[0]] if idx >= 0 else -added[sw[0]])[0]
    if idx == 0 or (added[top] > 0) != (idx > 0):
        one = "no single name moved the index's way"
    else:
        part = added[top] / idx
        one = (f"{top} alone supplied {pct(part)} of the index's {sig(abs(idx))} {'rise' if idx > 0 else 'fall'}, "
               f"{'past' if part > MEGA_ONE_NAME_SHARE else 'under'} the {pct(MEGA_ONE_NAME_SHARE)} one-name share")
    index_rule = f"past the {MOVE_RULE_SIGMA} sigma move rule" if abs(idx) > MOVE_RULE_SIGMA else f"within the {MOVE_RULE_SIGMA} sigma move rule"
    ls.put("leaders.megacap_cohesion_30m", f"over the last {WINDOW_30_MIN} minutes {up} of the {len(names)} largest stocks rose past their own "
                                           f"move rule and {down} fell past it, {together}; {one}; the index move was {index_rule}"
                                           f"{against.ruler_note}")


def _single_name(against: AgainstIndex, names: list[tuple[str, float]], ls: LabelSet) -> None:
    """The largest stock's 10-minute move against its usual 10-minute move at this minute, and whether the
    other large names went the same way."""
    path = "leaders.single_name_10m"
    moves = {s: against.move(s, WINDOW_10_MIN) for s, _ in names}
    missing = [s for s, m in moves.items() if m is None]
    if missing:
        ls.omit(path, needs_move(missing, WINDOW_10_MIN))
        return
    usual = {}
    for symbol, _ in names:
        def then_size(s: Session, then: datetime, _sigma_share: float, symbol: str = symbol) -> float | None:
            m = s.move(symbol, then - timedelta(minutes=WINDOW_10_MIN), then)
            return None if m is None else abs(m)
        base = against.same_clock(then_size)
        if len(base) < MIN_RANK_SESSIONS or statistics.median(base) == 0:
            ls.omit(path, needs_rank(symbol, len(base)))
            return
        usual[symbol] = statistics.median(base)
    times = {s: abs(moves[s]) / usual[s] for s, _ in names}
    name, weight = max(names, key=lambda sw: times[sw[0]])
    ret = moves[name]
    rose = ret >= 0
    others = sum(1 for s, _ in names if s != name and times[s] > 1 and (moves[s] >= 0) == rose)
    verb = "rose" if rose else "fell"
    rule = f"past the {NAME_SHOCK_MULT:g}-times name-shock rule" if times[name] >= NAME_SHOCK_MULT else f"under the {NAME_SHOCK_MULT:g}-times name-shock rule"
    spreading = (f"at least the {SPREAD_COUNT}-name spreading count" if others >= SPREAD_COUNT else f"short of the {SPREAD_COUNT}-name spreading count")
    joined = (f"none of the other {len(names) - 1} largest {verb} past its usual {WINDOW_10_MIN}-minute move" if others == 0 else
              f"{others} of the other {len(names) - 1} largest also {verb} past their usual {WINDOW_10_MIN}-minute move")
    ls.put(path, f"in the last {WINDOW_10_MIN} minutes {name} {verb} {abs(ret) * 100:.1f}%, {times[name]:.1f} times its usual "
                 f"{WINDOW_10_MIN}-minute move for this time of day, {rule}, worth {signed(against.sigma(weight * ret))} sigma of SPX at its "
                 f"{weight * 100:.1f}% weight; {joined}, {spreading}{against.ruler_note}")
