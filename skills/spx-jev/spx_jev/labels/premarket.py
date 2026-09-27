"""The premarket family: the overnight futures before the open (overnight.*), from the night's bars in the
overnight store (scene.night), ranked against the same measure on the last nights (night_ranks), never
against a fixed line. Its sizes are in the pre-open ruler, the median morning anchor of the last sessions
(rulers.normal_day_sigma), the one stamped on the premarket lane's records: a move of ``pct`` percent is
``pct / 100 * prior_close / sigma`` of it.

* overnight.es_move: S&P futures from their prior close (16:00) to the read, its size ranked against the
  last nights' moves to the same minute, in thirds.
* overnight.range_vs_normal: the night's high-low range since the 18:00 reopen in percent of price, ranked
  the same way.
* overnight.gap_origin: which stretch of the night (story.py) moved most unusually against the same stretch
  on the last nights; it sleeps when the night's net move is in its bottom third, with no gap to place.
* overnight.release_reaction: on a day the calendar lists a report before the open (claims-only days
  too), the release minute to 08:45 ranked against the same window on the last days, report or not, then
  what futures did from 08:45 to the read against GAP_HALF_SHARE of it.
* overnight.bond_gap: ten-year Treasury futures (/ZN) against the S&P futures' move through a fit on the
  last nights: where bonds point S&P futures, less where they stand, ranked in thirds. No bitcoin.

A move across a futures roll (rolls.py), or on a root whose quoted contract the roll table has not
reached yet (the manifest's ``contract_quoted``), is never measured, and neither is a night that spans a
market holiday or follows a half day, which the last nights' ranks leave out. Written only on a premarket read
(PREMARKET): a session read neither writes nor omits them, and leaves its gates to the registry. Each
label's sentence, how it is computed and its source are in spec/question_set.json ``labels``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .. import events, overnight, rolls, story
from ..cuts import GAP_HALF_SHARE, OVERNIGHT_RANK_MIN_NIGHTS, THIRD_LO
from ..night_ranks import (READ_STALE_MIN, Move, Night, NightRank, RankedMove, night_move, prior_nights, prior_window_nights,
                           prior_window_ranges, quoted_contract, rank_night, ranked_move, roll_pending, window_move, window_range)
from ..sessions import previous_trading_day
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ET, is_num
from .words import above_or_below, listed, pct, sig, third

LABELS = ("overnight.bond_gap", "overnight.es_move", "overnight.gap_origin", "overnight.range_vs_normal", "overnight.release_reaction")
GATES = ("gap_origin", "overnight_bonds_vs_gap", "release_reaction_path")
DARK: dict[str, str] = {}
PREMARKET = LABELS

ES, ZN = "/ES", "/ZN"
STALE = timedelta(minutes=READ_STALE_MIN)
# gap_origin's answer for each stretch of the night; the report window is "at the release" only on a report day.
ORIGINS = {"after_close": "made_early", "asia": "made_early", "europe_open": "made_in_europe", "europe_morning": "made_in_europe",
           "pre_report": "made_late", "report_window": "made_at_release", "last_stretch": "made_late"}
STRETCH_WORDS = {"after_close": "after the close", "asia": "Asia's session", "europe_open": "Europe's open",
                 "europe_morning": "Europe's morning", "pre_report": "the run-up to the report window",
                 "report_window": "the report window", "last_stretch": "the last stretch"}


@dataclass(frozen=True)
class Tonight:
    """What every overnight label reads: the session the night leads into, the read's clock minute, the roll
    table, the state folder holding the last nights, and the pre-open ruler in sigma per percent of price."""
    day: date
    clock: time
    table: dict
    state_dir: Path
    sigma_per_pct: float

    @property
    def at(self) -> datetime:
        """The read's minute: the clock the last nights are measured to."""
        return datetime.combine(self.day, self.clock, tzinfo=ET)

    def sigma(self, move_pct: float) -> float:
        return move_pct * self.sigma_per_pct


@dataclass(frozen=True)
class RankedStretch:
    """One stretch of tonight, its move, and its size's rank against the same stretch on the last nights."""
    stretch: story.Stretch
    move: Move
    rank: NightRank


def build_premarket_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    if not scene.premarket:
        return ls
    tonight, why = _tonight(scene)
    if tonight is None:
        for path in LABELS:
            ls.omit(path, why)
        for qid in GATES:
            ls.sleep(qid, why)
        return ls
    quoted = quoted_contract(tonight.state_dir, tonight.day, ES, scene.now)
    es, es_why = night_move(scene.night, ES, tonight.day, tonight.clock, tonight.table, quoted)
    net, net_why = ranked_move(scene.night, tonight.state_dir, tonight.day, ES, tonight.clock, tonight.table, quoted)
    where = _where(tonight, es) if es else ""
    _es_move(tonight, net, net_why, ls)
    _range_vs_normal(scene, tonight, roll_pending(tonight.table, ES, quoted), where, ls)
    _gap_origin(scene, tonight, net, net_why, ls)
    _release_reaction(scene, tonight, es, es_why, where, ls)
    _bond_gap(scene, tonight, es, es_why, ls)
    return ls


def _tonight(scene: Scene) -> tuple[Tonight | None, str]:
    if scene.state_dir is None:
        return None, "no state folder to read the last nights from"
    pc = scene.row.get("prior_close")
    if not is_num(pc) or pc <= 0 or not is_num(scene.sigma) or scene.sigma <= 0:
        return None, "no pre-open ruler: the read carries no prior close or no median morning anchor"
    now = scene.now.astimezone(ET)
    if overnight.holiday_night(now.date()):
        return None, "the night spans a market holiday or follows a half day, unlike the nights it would be ranked against"
    return Tonight(now.date(), now.time().replace(second=0, microsecond=0),
                   rolls.load(Path(scene.state_dir) / overnight.OVERNIGHT_SUBDIR), Path(scene.state_dir),
                   float(pc) / 100.0 / scene.sigma), ""


def _where(tonight: Tonight, es: Move) -> str:
    """Where S&P futures stand against their prior close: the clause every sentence opens with."""
    s = tonight.sigma(es.pct)
    return f"at {tonight.clock:%H:%M} S&P futures stand {sig(abs(s))} {above_or_below(s)} their {_hm(es.start_at)} price"


def _hm(t: datetime) -> str:
    return f"{t.astimezone(ET):%H:%M}"


def _joined(*clauses: str) -> str:
    return "; ".join(c for c in clauses if c)


def _es_move(tonight: Tonight, net: RankedMove | None, why: str, ls: LabelSet) -> None:
    """Where S&P futures stand against their prior close, then how large that move is against the last nights'
    moves to the same minute: its third and its side give the answer."""
    if net is None:
        ls.omit("overnight.es_move", why)
        return
    move, rank = net.move, net.rank
    band = third(rank.share)
    verdict = "flat" if band == "bottom" else f"{'big_' if band == 'top' else ''}{'up' if move.pct > 0 else 'down'}"
    ls.put("overnight.es_move",
           f"{_where(tonight, move)} ({'up' if move.pct >= 0 else 'down'} {abs(move.pct):.2f}%), a move {rank.words('moves to this time')}",
           figure={"kind": "rank", "value": round(tonight.sigma(move.pct), 3), "cut": rank.band, "verdict": verdict})


def _reopen(day: date) -> datetime:
    """The Globex reopen the night's session starts at: 18:00 on the prior trading day (a Monday's night trades
    from Sunday's 18:00, so the closed weekend adds nothing)."""
    return datetime.combine(previous_trading_day(day), story.GLOBEX_REOPEN, tzinfo=ET)


def _range_vs_normal(scene: Scene, tonight: Tonight, pending: str, where: str, ls: LabelSet) -> None:
    """Where S&P futures stand, then the night's high-low range since the reopen in percent of price against
    the last nights' ranges to the same minute, in thirds."""
    label = "overnight.range_vs_normal"
    if pending:
        ls.omit(label, pending)
        return
    rng = window_range(scene.night, ES, _reopen(tonight.day), tonight.at)
    if rng is None or tonight.at - rng.end_at > STALE:
        ls.omit(label, f"no {ES} bar within the last {READ_STALE_MIN} minutes in the overnight store")
        return
    if not rolls.same_contract(tonight.table, ES, rng.start_at, rng.end_at):
        ls.omit(label, f"{ES} rolled to the next contract overnight, so the night's range spans two contracts")
        return
    base = prior_window_ranges(tonight.state_dir, tonight.day, ES,
                               lambda d: (_reopen(d), datetime.combine(d, tonight.clock, tzinfo=ET)), tonight.table)
    rank, why = rank_night(rng.pct, base)
    if rank is None:
        ls.omit(label, f"night range not ranked: {why}")
        return
    verdict = {"bottom": "quiet_night", "middle": "normal_night", "top": "wide_night"}[third(rank.share)]
    s = tonight.sigma(rng.pct)
    ls.put(label,
           _joined(where, f"since the {story.GLOBEX_REOPEN:%H:%M} reopen their high-low range is {sig(s)} ({rng.pct:.2f}% of price), "
                          f"{rank.words('ranges to this time')}"),
           figure={"kind": "rank", "value": round(s, 3), "cut": rank.band, "verdict": verdict})


def _stretch_words(s: story.Stretch) -> str:
    return f"{STRETCH_WORDS[s.name]} ({_hm(s.start)} to {_hm(s.end)})"


def _gap_origin(scene: Scene, tonight: Tonight, net: RankedMove | None, why: str, ls: LabelSet) -> None:
    """Where S&P futures stand, then the stretch of the night whose move ranks most unusual against the same
    stretch on the last nights (the larger move breaking a tie): it names where the gap was made. Sleeps
    without a net move to place, on one in its bottom third, or on a day the calendar does not cover, since
    the report window's edges and whether it holds a release come from it."""
    label, gate = "overnight.gap_origin", "gap_origin"
    if net is None:
        ls.omit(label, why)
        ls.sleep(gate, f"no overnight move to place: {why}")
        return
    if net.rank.share <= THIRD_LO:
        quiet = (f"the move since futures' {_hm(net.move.start_at)} price is {net.rank.words('moves to this time')}: "
                 "too small a gap to place")
        ls.omit(label, quiet)
        ls.sleep(gate, quiet)
        return
    uncovered = events.uncovered(tonight.day, tier=events.PRE_OPEN)
    if uncovered:
        unknown = f"where the report window falls is unknown: {uncovered}"
        ls.omit(label, unknown)
        ls.sleep(gate, unknown)
        return
    release = story.release_minute(tonight.day)
    ranked, left_out = [], []
    for s in story.stretches(tonight.day, tonight.at, release):
        name = _stretch_words(s)
        move = window_move(scene.night, ES, s.start, s.end)
        if move is None:
            left_out.append(f"{name}, no price")
            continue
        if not rolls.same_contract(tonight.table, ES, move.start_at, move.end_at):
            left_out.append(f"{name}, across a roll")
            continue
        window = story.stretch_window(s.name, until=None if s.finished else tonight.clock, release=release)
        rank, _ = rank_night(abs(move.pct), prior_window_nights(tonight.state_dir, tonight.day, ES, window, tonight.table,
                                                                lambda m: abs(m.pct)))
        if rank is None:
            left_out.append(f"{name}, too few nights")
            continue
        ranked.append(RankedStretch(s, move, rank))
    if not ranked:
        reason = f"no stretch of the night could be ranked: {'; '.join(left_out)}"
        ls.omit(label, reason)
        ls.sleep(gate, reason)
        return
    best = max(ranked, key=lambda r: (r.rank.share, abs(r.move.pct)))
    verdict = ORIGINS[best.stretch.name]
    if verdict == "made_at_release" and not story.releases(tonight.day):
        verdict = "made_late"
    s = tonight.sigma(best.move.pct)
    ls.put(label,
           _joined(f"{_where(tonight, net.move)}, a move {net.rank.words('moves to this time')}",
                   f"the most unusual stretch was {_stretch_words(best.stretch)}, {'up' if s >= 0 else 'down'} {sig(abs(s))}, "
                   f"{best.rank.words('moves over the same stretch')}",
                   f"left out: {'; '.join(left_out)}" if left_out else ""),
           figure={"kind": "rank", "value": round(s, 3), "cut": best.rank.band, "verdict": verdict})
    ls.wake(gate)


def _release_reaction(scene: Scene, tonight: Tonight, es: Move | None, es_why: str, where: str, ls: LabelSet) -> None:
    """On a report day: where S&P futures stand, then their move from the release minute to 08:45 against the
    same window on the last days, report or not (the bottom third: shrugged), then from 08:45 to the read
    the share of that reaction they went further or gave back, against GAP_HALF_SHARE (extended, faded,
    else held)."""
    label, gate = "overnight.release_reaction", "release_reaction_path"

    def omit(reason: str) -> None:
        ls.omit(label, reason)
        ls.sleep(gate, reason)

    uncovered = events.uncovered(tonight.day, tier=events.PRE_OPEN)
    if uncovered:
        omit(uncovered)
        return
    reports = story.releases(tonight.day)
    if not reports:
        omit("no report before the open on the calendar today")
        return
    release = story.release_minute(tonight.day)
    names = listed([e.words for e in reports if e.start.time() == release])
    start, end = (datetime.combine(tonight.day, release, tzinfo=ET),
                  datetime.combine(tonight.day, story.REPORT_WINDOW_END, tzinfo=ET))
    if tonight.at < end:
        omit(f"{names} comes out at {release:%H:%M} and its reaction window runs to {end:%H:%M}, after this read")
        return
    if es is None:
        omit(es_why)
        return
    reaction, after = window_move(scene.night, ES, start, end), window_move(scene.night, ES, end, tonight.at)
    if (reaction is None or after is None or start - reaction.start_at > STALE or end - reaction.end_at > STALE
            or tonight.at - after.end_at > STALE):
        omit(f"{ES} bars missing around {names} at {release:%H:%M}")
        return
    if not rolls.same_contract(tonight.table, ES, reaction.start_at, after.end_at):
        omit(f"{ES} rolled to the next contract during the report's reaction")
        return
    window = story.stretch_window("report_window", release=release)
    rank, why = rank_night(abs(reaction.pct), prior_window_nights(tonight.state_dir, tonight.day, ES, window, tonight.table,
                                                                  lambda m: abs(m.pct)))
    if rank is None:
        omit(f"the reaction is not ranked: {why}")
        return
    further = after.pct / reaction.pct if reaction.pct else 0.0
    if not reaction.pct or third(rank.share) == "bottom":
        verdict = "shrugged"
    elif further > GAP_HALF_SHARE:
        verdict = "extended"
    elif -further > GAP_HALF_SHARE:
        verdict = "faded"
    else:
        verdict = "held"
    if verdict == "shrugged":   # a share of a reaction this small says nothing
        since = f"since {end:%H:%M} they moved {'up' if after.pct >= 0 else 'down'} {sig(abs(tonight.sigma(after.pct)))}"
    else:
        line = f"{'past' if abs(further) > GAP_HALF_SHARE else 'within'} the {pct(GAP_HALF_SHARE)} line"
        since = (f"since {end:%H:%M} they went {pct(further)} further its way, {line}" if further >= 0
                 else f"since {end:%H:%M} they gave back {pct(-further)} of it, {line}")
    s = tonight.sigma(reaction.pct)
    minutes = int((end - start).total_seconds() // 60)
    ls.put(label,
           _joined(where, f"{names} at {release:%H:%M} moved them {'up' if s >= 0 else 'down'} {sig(abs(s))} by {end:%H:%M}, "
                          f"{rank.words(f'moves over the same {minutes} minutes')}", since),
           figure={"kind": "rank", "value": round(s, 3), "cut": rank.band, "verdict": verdict})
    ls.wake(gate)


def _through_zero(pairs: list[tuple[str, float, float]]) -> float:
    """Bonds' move per S&P futures' move over ``pairs`` of (day, S&P futures' move, bonds' move), fitted through
    zero; 0.0 when S&P futures never moved."""
    square = sum(e * e for _, e, _ in pairs)
    return sum(e * z for _, e, z in pairs) / square if square else 0.0


def _bond_gap(scene: Scene, tonight: Tonight, es: Move | None, es_why: str, ls: LabelSet) -> None:
    """Where ten-year Treasury futures stand against their prior close, then the S&P futures' move that points
    to by a fit on the last nights (bonds' move per S&P futures' move, through zero, so the fit sets the
    sign), less the move S&P futures made: ranked against the same reading on those nights, the top third
    is bonds ahead toward stocks up, the bottom third toward stocks down. Sleeps when the fit shows no
    steady link: a sign that one night decides, as near a fit of zero, would turn the answer on noise and
    blow its size up."""
    label, gate = "overnight.bond_gap", "overnight_bonds_vs_gap"

    def omit(reason: str) -> None:
        ls.omit(label, reason)
        ls.sleep(gate, reason)

    if es is None:
        omit(f"no S&P futures move to set bonds against: {es_why}")
        return
    zn, why = night_move(scene.night, ZN, tonight.day, tonight.clock, tonight.table,
                         quoted_contract(tonight.state_dir, tonight.day, ZN, scene.now))
    if zn is None:
        omit(why)
        return
    stocks = prior_nights(tonight.state_dir, tonight.day, ES, tonight.clock, tonight.table, lambda m: m.pct)
    bonds = prior_nights(tonight.state_dir, tonight.day, ZN, tonight.clock, tonight.table, lambda m: m.pct)
    pairs = [(e.day, e.value, z.value) for e, z in zip(stocks, bonds) if e.skip is None and z.skip is None]
    if len(pairs) < OVERNIGHT_RANK_MIN_NIGHTS:
        omit(f"the bonds' fit needs {OVERNIGHT_RANK_MIN_NIGHTS} of the last nights with {ES} and {ZN} both measured on one "
             f"contract, has {len(pairs)}")
        return
    beta = _through_zero(pairs)
    if not beta or any(_through_zero(pairs[:k] + pairs[k + 1:]) * beta <= 0 for k in range(len(pairs))):
        omit(f"bonds and S&P futures showed no steady link over the last {len(pairs)} nights: "
             "leaving out a single night turns the fit's sign")
        return
    ahead = zn.pct / beta - es.pct
    rank, _ = rank_night(ahead, [Night(d, z / beta - e) for d, e, z in pairs])
    verdict = {"top": "overnight_ahead_up", "bottom": "overnight_ahead_down", "middle": "in_line"}[third(rank.share)]
    s = tonight.sigma(ahead)
    ls.put(label,
           f"at {tonight.clock:%H:%M} ten-year Treasury futures stand {abs(zn.pct):.2f}% {above_or_below(zn.pct)} their "
           f"{_hm(zn.start_at)} price; over the last {len(pairs)} nights they moved {abs(beta):.2f}% per 1% in S&P futures, "
           f"{'the same way' if beta > 0 else 'the other way'}, so they point S&P futures {sig(abs(s))} {above_or_below(s)} "
           f"where they stand, higher than {rank.larger_than} of the last {rank.of} nights' readings, {rank.band}",
           figure={"kind": "rank", "value": round(s, 3), "cut": rank.band, "verdict": verdict})
    ls.wake(gate)
