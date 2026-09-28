"""The gamma family: the dealers' options book against price, its walls, its weight and its ladder (gex.*).

The book is described as the call-against-put gamma balance at price under the usual assumption that dealers
hold the calls and are short the puts, never as long or short gamma: the scanner's ``long_gamma`` is price above
the flip, where call positions carry more gamma (it matches the flip side on all but 4 of 6,780 rows of the last
30 sessions). Every sentence names the expiry book it reads; the flip, the magnet, the charm wall and the gamma
walls are today's same-day (0DTE) book, ``net_by_strike_tenor`` the 1-to-7-day book.

A distance, a width or a share the question set judges is ranked against the same measure on each prior session's
diary row at this minute (up to the last 20, needing 10, ranks.rank_sessions), each distance in its session's own
ruler, and worded by its third: the owner's rule, never a fixed cut.

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from ..cuts import (EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, GRIP_CONCENTRATED_SHARE, GRIP_SPREAD_SHARE, HALF_RANK, SETTLE_SEAT_SIGMA,
                    WALL_NEAR_SIGMA, WALL_THICK_SHARE, WALL_THIN_SHARE, WALL_TOUCH_QUIET_PERCENTILE, WALL_TOUCH_SIEGE_PERCENTILE,
                    WINDOW_30_MIN)
from ..expiry import settle_at, todays_settle
from ..row_adapter import labeller_row
from ..sessions import next_trading_day
from ..state_builder import ROWS_SUBDIR, Scene, row_days
from .label_set import LabelSet
from .measures import ET, bars_finished_between, is_num, walls
from .ranks import SameClockRank, rank_sessions
from .rulers import NO_ANCHOR, SigmaRuler, remaining_straddles, sigma_anchor
from .words import above_or_below, ordinal, pct, plural, sig

LABELS = ("gex.weight_side", "gex.air_to_wall", "gex.wall_thickness", "gex.heaviest_strike_grip", "gex.delta_weight_side",
          "gex.expiry_roll", "gex.ladder_state", "gex.wall_touch_volume",
          "gex.balance_vs_yesterday", "gex.book_balance", "gex.charm_wall_distance", "gex.cushion_if_moved", "gex.flip_distance",
          "gex.magnet_distance", "gex.settle_pull", "gex.wall_box", "gex.walls_since_30min", "gex.weight_both_books")
GATES = ("book_vs_pace", "settle_pull_side")
DARK = {"gex.cushion_if_moved": "the row carries no cushion: sliding the gamma flip by half a sigma needs the full options chain, "
                                "which the scan has and the diary does not write (gex_views.cushion_up and cushion_down)"}

# A wall touch's SPY volume (gex.wall_touch_volume) is described while its verdict is this recent.
WALL_TOUCH_RECENT_MIN = 30
# A diary row stands for a moment when it was written at most this long before it; the scanner writes one
# about every 90 seconds, so a wider gap means it was not running.
ROW_SLACK_MIN = 5
# book_vs_pace sleeps before this (its sleep_when).
BOOK_VS_PACE_FROM = time(11, 30)

DEALERS = "under the usual assumption that dealers hold the calls and are short the puts"
BALANCE = {"long_gamma": "call positions carry more gamma than put positions",
           "short_gamma": "put positions carry more gamma than call positions",
           "uncertain": "the two are too close to call"}
LEANS_TO = {"long_gamma": "calls", "short_gamma": "puts"}
# every diary line starts with its timestamp, so a past day's row can be found without parsing the day
ROW_TS = re.compile(r'^\{"ts": "([^"]+)"')


def build_gamma_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    gv = scene.row.get("gex_views") or {}
    _weight_side(gv, ls)
    near = _air_to_wall(scene, ls)
    _wall_thickness(gv, near, ls)
    _heaviest_strike_grip(gv, ls)
    _expiry_roll(scene, ls)
    _ladder_state(scene, ls)
    _wall_touch_volume(scene, ls)
    ruler = sigma_anchor(scene)
    earlier = _row_minutes_ago(scene, WINDOW_30_MIN)
    prior = prior_books(scene)
    _delta_weight_side(scene, prior, ls)
    _book_balance(scene, gv, ruler, ls)
    _balance_vs_yesterday(scene, gv, ls)
    _flip_distance(scene, gv, ruler, earlier, prior, ls)
    _weight_both_books(scene, gv, prior, ls)
    _magnet_distance(scene, gv, ruler, earlier, prior, ls)
    _settle_pull(scene, gv, ruler, prior, ls)
    _charm_wall_distance(scene, gv, ruler, prior, ls)
    _wall_box(scene, ruler, earlier, prior, ls)
    _walls_since_30min(scene, ruler, earlier, ls)
    _book_vs_pace_gate(scene, ls)
    _settle_pull_gate(scene, gv, ruler, prior, ls)
    return ls


def _weight_side(gv: dict, ls: LabelSet) -> None:
    above, below = gv.get("gamma_above_spot"), gv.get("gamma_below_spot")
    if not (is_num(above) and is_num(below) and (above + below) > 0):
        ls.omit("gex.weight_side", "row carries no gamma above and below spot")
        return
    share = float(above) / float(above + below)
    if share > EVEN_SPLIT_HIGH:
        ls.put("gex.weight_side", f"most of today's 0DTE options weight sits above price, {pct(share)} of it")
    elif share < EVEN_SPLIT_LOW:
        ls.put("gex.weight_side", f"most of today's 0DTE options weight sits below price, {pct(1 - share)} of it")
    else:
        ls.put("gex.weight_side", f"today's 0DTE options weight is split about evenly above and below price, {pct(share)} above")


def _air_to_wall(scene: Scene, ls: LabelSet) -> tuple[str, float, str] | None:
    """The nearest wall, written as gex.air_to_wall and returned for its thickness."""
    ws = walls(scene.row, scene.spot, scene.sigma)
    if not ws:
        ls.put("gex.air_to_wall", "no heavy strike sits within reach on either side of price", figure={"kind": "distance", "verdict": "no_wall_in_reach"})
        return None
    nearest = min(ws, key=lambda w: abs(w[1]))
    name, d, _ = nearest
    side = "above" if d >= 0 else "below"
    close = abs(d) <= WALL_NEAR_SIGMA
    fig = {"kind": "distance", "value": round(abs(d), 3), "near": WALL_NEAR_SIGMA, "unit": "sigma", "side": side, "name": name,
           "verdict": "heavy_strike_close" if close else "open_air"}
    if close:
        ls.put("gex.air_to_wall", f"a heavy strike sits within {WALL_NEAR_SIGMA} sigma of price: the {name} {sig(abs(d))} {side} price", figure=fig)
    else:
        ls.put("gex.air_to_wall", f"the nearest heavy strike is the {name} {sig(abs(d))} {side} price, more than {WALL_NEAR_SIGMA} sigma away, with open air between", figure=fig)
    return nearest


def _wall_thickness(gv: dict, nearest: tuple[str, float, str] | None, ls: LabelSet) -> None:
    share = None if nearest is None else gv.get("call_wall_gamma_share" if nearest[2] == "call_wall" else "put_wall_gamma_share")
    if nearest is None or not is_num(share):
        ls.omit("gex.wall_thickness", "no nearest wall with a gamma share on the row")
        return
    band = (f"thick, at or above the {pct(WALL_THICK_SHARE)} cut" if share >= WALL_THICK_SHARE else
            f"thin, below the {pct(WALL_THIN_SHARE)} cut" if share < WALL_THIN_SHARE else
            f"middling, between the {pct(WALL_THIN_SHARE)} thin cut and the {pct(WALL_THICK_SHARE)} thick cut")
    ls.put("gex.wall_thickness", f"on the nearest wall's side, today's 0DTE {nearest[0]} holds {pct(share)} of today's 0DTE options weight, {band}")


def _heaviest_strike_grip(gv: dict, ls: LabelSet) -> None:
    top = gv.get("pin_top_share")
    if not is_num(top):
        ls.omit("gex.heaviest_strike_grip", "row carries no top-strike share")
        return
    band = (f"concentrated, at or above the {pct(GRIP_CONCENTRATED_SHARE)} cut" if top >= GRIP_CONCENTRATED_SHARE else
            f"spread thin, below the {pct(GRIP_SPREAD_SHARE)} cut" if top < GRIP_SPREAD_SHARE else
            f"middling, between the {pct(GRIP_SPREAD_SHARE)} and {pct(GRIP_CONCENTRATED_SHARE)} cuts")
    ls.put("gex.heaviest_strike_grip", f"the single heaviest strike of today's 0DTE book holds {pct(top)} of its weight, {band}")


def _expiry_roll(scene: Scene, ls: LabelSet) -> None:
    row = scene.row
    parts = [f"on the {side} side the 1-to-7-day book's wall is {'the same strike as' if row[k] == row[kt] else 'a different strike from'} the nearest {side} wall"
             for side, k, kt in (("call", "call_wall", "call_wall_tenor"), ("put", "put_wall", "put_wall_tenor"))
             if is_num(row.get(k)) and is_num(row.get(kt))]
    if not parts:
        ls.omit("gex.expiry_roll", "no side with both a nearest wall and a 1-to-7-day wall")
    else:
        ls.put("gex.expiry_roll", "; ".join(parts))


def _ladder_state(scene: Scene, ls: LabelSet) -> None:
    st = (scene.row.get("profile_ladder") or {}).get("state")
    if not isinstance(st, str) or not st:
        ls.omit("gex.ladder_state", "the gamma ladder state was not measured on this row")
    else:
        ls.put("gex.ladder_state", f"today's 0DTE gamma ladder is in the {st} state")


def _wall_touch_volume(scene: Scene, ls: LabelSet) -> None:
    """The siege box's judgement of the newest wall or magnet touch: how much SPY volume was spent
    while spot touched it, as a percentile of normal for that clock window. A touch is judged when
    its window closes; one the box would not judge (a level that hugged spot all along, a session
    that is one long touch) carries no verdict and is not described. When it was judged is the
    first of today's rows to carry the verdict, so only what the scanner had seen by now counts."""
    sg = scene.row.get("siege")
    if not sg:
        ls.omit("gex.wall_touch_volume", "row carries no siege read")
        return
    if sg.get("health") != "OK":
        ls.omit("gex.wall_touch_volume", f"the siege box's SPY feed is {sg.get('health') or 'unreported'}, not OK")
        return
    if sg.get("baseline") != "robust":
        ls.omit("gex.wall_touch_volume", f"the siege box's volume baseline is {sg.get('baseline') or 'unreported'}, not yet robust")
        return
    judged: dict[tuple, tuple[datetime, dict]] = {}
    for r in scene.rows_today:
        for t in (r.get("siege") or {}).get("towers") or []:
            key = (t.get("kind"), t.get("level"))
            if t.get("verdict") and is_num(t.get("effort_pct")) and is_num(t.get("level")) and key not in judged:
                judged[key] = (datetime.fromisoformat(r["ts"]), t)
    recent = [(when, t) for when, t in judged.values() if scene.now - when <= timedelta(minutes=WALL_TOUCH_RECENT_MIN)]
    if not recent:
        ls.put("gex.wall_touch_volume", f"no touch of a wall or the magnet has had its SPY volume judged in the last {WALL_TOUCH_RECENT_MIN} minutes")
        return
    when, t = max(recent, key=lambda wt: wt[0])
    name = {"call_wall": "call wall", "put_wall": "put wall", "magnet": "magnet"}.get(t["kind"], "heavy strike")
    # the tower's latest state on this row: its outcome, once graded, and where its frozen level now sits
    now_t = next((x for x in sg.get("towers") or [] if (x.get("kind"), x.get("level")) == (t["kind"], t["level"])), t)
    d = (float(t["level"]) - scene.spot) / scene.sigma
    effort = float(t["effort_pct"])
    siege, quiet = WALL_TOUCH_SIEGE_PERCENTILE, WALL_TOUCH_QUIET_PERCENTILE
    band = (f"a siege, at or above the {siege}th-percentile cut" if effort >= siege else
            f"quiet, at or below the {quiet}th-percentile cut" if effort <= quiet else
            f"neither, between the {quiet}th and {siege}th-percentile cuts")
    after = {"HOLD": "; half an hour after the touch the level had held", "BREAK": "; half an hour after the touch the level had broken",
             "UNRESOLVED": "; half an hour after the touch the level had neither held nor broken"}.get(now_t.get("outcome"), "")
    ago = plural(round((scene.now - when).total_seconds() / 60.0), "minute")
    ls.put("gex.wall_touch_volume",
           f"a touch of the {name}, now {sig(abs(d))} {'above' if d >= 0 else 'below'} price, was judged {ago} ago: "
           f"SPY volume during it was in the {ordinal(round(effort))} percentile of normal for that time of day, {band}{after}")


def _estimated(ruler: SigmaRuler) -> str:
    return "; ruler estimated" if ruler.estimated else ""


def _row_minutes_ago(scene: Scene, minutes: int) -> dict | None:
    """Today's newest diary row written by ``minutes`` before now, and not more than ROW_SLACK_MIN before that."""
    then = scene.now - timedelta(minutes=minutes)
    rows = [r for r in scene.rows_today if then - timedelta(minutes=ROW_SLACK_MIN) <= datetime.fromisoformat(r["ts"]) <= then]
    return rows[-1] if rows else None


def _diary_row_at(state_dir: Path, day: str, then: datetime) -> dict | None:
    """A past day's newest diary row written by ``then``, and not more than ROW_SLACK_MIN before it, as the
    labeller reads it. A day's diary is about 10 MB, so it is read a line at a time and only the chosen row
    is parsed whole; the scanner writes its rows in time order."""
    path = Path(state_dir) / ROWS_SUBDIR / f"{day}.jsonl"
    if not path.exists():
        return None
    chosen = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = ROW_TS.match(line)
            if m is None:
                continue
            if datetime.fromisoformat(m.group(1)) > then:
                break
            chosen = line
    try:
        row = labeller_row(json.loads(chosen)) if chosen else None
    except json.JSONDecodeError:
        return None
    return row if row and datetime.fromisoformat(row["ts"]) >= then - timedelta(minutes=ROW_SLACK_MIN) else None


@dataclass(frozen=True)
class PriorBook:
    """A prior session's diary row at this read's clock minute (``then``, market time), with its morning ruler."""
    day: str
    row: dict
    ruler: SigmaRuler | None
    then: datetime


def prior_books(scene: Scene) -> list[PriorBook] | None:
    """Each prior session's diary row at this read's clock minute, newest first, read once a read for every rank of
    the book; None without a state folder. A session whose ruler was estimated is left out, as ranks.rank_days
    leaves it out; the days are the ones with a ruler, since the book is read from the diary, not the bars."""
    if scene.state_dir is None:
        return None
    clock = scene.now.astimezone(ET).time()
    out = []
    for day, ruler in scene.prior_rulers.items():
        if ruler is not None and ruler.estimated:
            continue
        then = datetime.combine(date.fromisoformat(day), clock, tzinfo=ET)
        if (row := _diary_row_at(scene.state_dir, day, then)) is not None:
            out.append(PriorBook(day, row, ruler, then))
    return out


def _book_rank(prior: list[PriorBook] | None, value: float, measure: Callable[[PriorBook], float | None],
               what: str) -> tuple[SameClockRank | None, str | None]:
    """``value`` against ``measure`` on each prior session's row at this minute (ranks.rank_sessions), ``what`` naming
    what a session's row needed; the reason instead without a state folder or with too few sessions."""
    if prior is None:
        return None, "no state folder to read the prior sessions' diaries from"
    return rank_sessions(value, [v for book in prior if (v := measure(book)) is not None], f"{what} at this minute")


def _distance_to(key: str) -> Callable[[PriorBook], float | None]:
    """How far a prior row's same-day ``key`` level sat from its spot, in its session's own ruler."""
    def measure(book: PriorBook) -> float | None:
        level, spot = (book.row.get("gex_views") or {}).get(key), book.row.get("spot")
        return abs(float(level) - float(spot)) / book.ruler.points if book.ruler and is_num(level) and is_num(spot) else None
    return measure


def _farther(rank: SameClockRank) -> str:
    """A distance's rank in words: "farther from price than on 4 of the last 20 sessions at this minute, bottom third"."""
    return f"farther from price than on {rank.higher_than} of the last {rank.of} sessions at this minute, {rank.band}"


# A share of the book above price by its third: more of it above or below price than usual, never a side of price,
# since a top-third share can still be under half.
LEANS = {"top third": "more of it above price than usual", "bottom third": "more of it below price than usual",
         "middle third": "about as usual"}


def _share_words(rank: SameClockRank, also: str = "") -> str:
    return f"a larger share than on {rank.higher_than} of the last {rank.of} sessions at this minute, {rank.band}: {also}{LEANS[rank.band]}"


def _book_balance(scene: Scene, gv: dict, ruler: SigmaRuler | None, ls: LabelSet) -> None:
    """Which positions carry more gamma at price in today's same-day book, by open interest (``regime``) and
    by today's volume (``regime_0dte_vol``), and how far away the open-interest balance tips (the flip)."""
    oi, vol = gv.get("regime"), gv.get("regime_0dte_vol")
    if gv.get("regime_source") != "0dte":
        ls.omit("gex.book_balance", "today's same-day book could not be read on this row, so the scanner fell back to the blended book")
        return
    if oi not in BALANCE or vol not in BALANCE:
        ls.omit("gex.book_balance", "row carries no balance for today's same-day book by both open interest and today's volume")
        return
    if oi == vol == "uncertain":
        text = "at the current price, today's same-day book is too close to call both by open interest and by today's volume"
    elif oi == vol:
        text = f"at the current price, same-day {BALANCE[oi]}, both by open interest and by today's volume, {DEALERS}"
    else:
        text = f"at the current price, today's same-day book reads two ways: by open interest {BALANCE[oi]}, while by today's volume {BALANCE[vol]}, {DEALERS}"
    flip = gv.get("flip")
    if is_num(flip) and ruler is not None:
        d = (float(flip) - scene.spot) / ruler.points
        if oi == "long_gamma" and d < 0:
            text += f"; the open-interest balance would tip to puts {sig(-d)} lower"
        elif oi == "short_gamma" and d > 0:
            text += f"; the open-interest balance would tip to calls {sig(d)} higher"
        else:
            text += f"; the gamma flip sits {sig(abs(d))} {above_or_below(d)} price"
        text += _estimated(ruler)
    ls.put("gex.book_balance", text)


def _balance_vs_yesterday(scene: Scene, gv: dict, ls: LabelSet) -> None:
    """Today's open-interest balance against the same-day book's at the previous session's close (its last
    diary row by the close)."""
    today = gv.get("regime")
    if gv.get("regime_source") != "0dte" or today not in BALANCE:
        ls.omit("gex.balance_vs_yesterday", "row carries no open-interest balance for today's same-day book")
        return
    if scene.state_dir is None:
        ls.omit("gex.balance_vs_yesterday", "no state folder to read yesterday's diary from")
        return
    earlier = [d for d in row_days(scene.state_dir) if d < scene.day]
    if not earlier:
        ls.omit("gex.balance_vs_yesterday", "the diary has no day before today")
        return
    prev = earlier[-1]
    if next_trading_day(date.fromisoformat(prev)) != date.fromisoformat(scene.day):
        ls.omit("gex.balance_vs_yesterday", f"the diary has no rows from the previous session; its newest earlier day is {prev}")
        return
    close_row = _diary_row_at(scene.state_dir, prev, settle_at(date.fromisoformat(prev)))
    yesterday = ((close_row or {}).get("gex_views") or {}).get("regime")
    if yesterday not in BALANCE:
        ls.omit("gex.balance_vs_yesterday", f"no diary row within {ROW_SLACK_MIN} minutes of yesterday's close carries the book's balance")
        return
    then = "was too close to call" if yesterday == "uncertain" else f"leaned to {LEANS_TO[yesterday]}"
    if today == yesterday:
        verdict = "so it is still too close to call" if today == "uncertain" else "so it has not tipped overnight"
        ls.put("gex.balance_vs_yesterday", f"by open interest the same-day book {then} at yesterday's close too, {DEALERS}, {verdict}")
        return
    now = "is too close to call now" if today == "uncertain" else f"leans to {LEANS_TO[today]} now"
    verdict = "so its lean has faded overnight" if today == "uncertain" else "so it has tipped overnight"
    ls.put("gex.balance_vs_yesterday", f"by open interest the same-day book {then} at yesterday's close and {now}, {DEALERS}, {verdict}")


def _flip_distance(scene: Scene, gv: dict, ruler: SigmaRuler | None, earlier: dict | None, prior: list[PriorBook] | None,
                   ls: LabelSet) -> None:
    """Price against the gamma flip of today's same-day book, its distance ranked against the prior sessions' at this
    minute (the top third far), and whether it crossed since the row 30 minutes ago. Never says what the flip does to price."""
    if gv.get("regime_source") != "0dte" or gv.get("regime") not in BALANCE:
        ls.omit("gex.flip_distance", "row carries no balance for today's same-day book")
        return
    flip = gv.get("flip")
    if gv["regime"] == "uncertain":
        ls.put("gex.flip_distance", "today's same-day book is too close to call at the current price, so no gamma flip stands out")
        return
    if not is_num(flip):
        ls.put("gex.flip_distance", "no gamma flip was found in today's same-day book")
        return
    if ruler is None:
        ls.omit("gex.flip_distance", NO_ANCHOR)
        return
    then_flip = ((earlier or {}).get("gex_views") or {}).get("flip")
    if not is_num(then_flip):
        ls.omit("gex.flip_distance", f"no diary row from {WINDOW_30_MIN} minutes ago carries a gamma flip")
        return
    d = (scene.spot - float(flip)) / ruler.points
    rank, no_rank = _book_rank(prior, abs(d), _distance_to("flip"), "a gamma flip")
    if rank is None:
        ls.omit("gex.flip_distance", no_rank)
        return
    crossed = (d >= 0) != (float(earlier["spot"]) >= float(then_flip))
    reach = f"{_farther(rank)}: {'far from the flip' if rank.band == 'top third' else 'near the flip'}"
    cross = (f"it has crossed the flip in the last {WINDOW_30_MIN} minutes" if crossed else
             f"it has not crossed the flip in the last {WINDOW_30_MIN} minutes")
    ls.put("gex.flip_distance", f"price is {sig(abs(d))} {above_or_below(d)} the gamma flip, the level where today's same-day book switches between "
                                f"call-heavy and put-heavy gamma {DEALERS}; {reach}; {cross}{_estimated(ruler)}")


def _today_share(gv: dict) -> float | None:
    """The share of today's same-day book's gamma above price (``gamma_above_spot``)."""
    above, below = gv.get("gamma_above_spot"), gv.get("gamma_below_spot")
    return float(above) / float(above + below) if is_num(above) and is_num(below) and above + below > 0 else None


def _week_share(gv: dict, spot: float) -> float | None:
    """The share of the 1-to-7-day book's gamma above price (``net_by_strike_tenor``, today's expiry excluded), a
    strike at price counted on neither side."""
    tenor = [(float(x[0]), abs(float(x[1]))) for x in gv.get("net_by_strike_tenor") or []
             if isinstance(x, (list, tuple)) and len(x) >= 2 and is_num(x[0]) and is_num(x[1])]
    above = sum(v for k, v in tenor if k > spot)
    total = above + sum(v for k, v in tenor if k < spot)
    return above / total if total > 0 else None


def _prior_share(share: Callable[[dict, float], float | None]) -> Callable[[PriorBook], float | None]:
    def measure(book: PriorBook) -> float | None:
        spot = book.row.get("spot")
        return share(book.row.get("gex_views") or {}, float(spot)) if is_num(spot) else None
    return measure


def _weight_both_books(scene: Scene, gv: dict, prior: list[PriorBook] | None, ls: LabelSet) -> None:
    """Where the gamma sits against price in today's same-day book and in the 1-to-7-day book: each book's share above
    price ranked against the same book's at this minute on the prior sessions, its top third more of it above price
    than usual and its bottom third more of it below."""
    today, week = _today_share(gv), _week_share(gv, scene.spot)
    if today is None:
        ls.omit("gex.weight_both_books", "row carries no gamma above and below price for today's same-day book")
        return
    if week is None:
        ls.omit("gex.weight_both_books", "row carries no gamma by strike for the 1-to-7-day book")
        return
    today_rank, no_today = _book_rank(prior, today, _prior_share(lambda g, _spot: _today_share(g)), "today's same-day book's gamma split")
    week_rank, no_week = _book_rank(prior, week, _prior_share(_week_share), "the 1-to-7-day book's gamma by strike")
    if today_rank is None or week_rank is None:
        ls.omit("gex.weight_both_books", no_today or no_week)
        return
    also = "also " if today_rank.band == week_rank.band else ""
    ls.put("gex.weight_both_books", f"today's same-day options gamma has {pct(today)} of it above price, {_share_words(today_rank)}; "
                                    f"the 1-to-7-day book (today's expiry excluded) has {pct(week)} of it above price, "
                                    f"{_share_words(week_rank, also)}")


def _delta_share(book: PriorBook) -> float | None:
    above = (book.row.get("dex_views") or {}).get("dex_above_spot")
    return float(above) if is_num(above) else None


def _delta_weight_side(scene: Scene, prior: list[PriorBook] | None, ls: LabelSet) -> None:
    """The share of dealers' delta at strikes above price, ranked against the same share at this minute on the prior sessions."""
    above = (scene.row.get("dex_views") or {}).get("dex_above_spot")
    if not is_num(above):
        ls.omit("gex.delta_weight_side", "row carries no delta split")
        return
    rank, no_rank = _book_rank(prior, float(above), _delta_share, "a delta split")
    if rank is None:
        ls.omit("gex.delta_weight_side", no_rank)
        return
    ls.put("gex.delta_weight_side", f"{pct(above)} of the directional exposure across the 0-to-7-day books, {DEALERS}, sits at strikes "
                                    f"above price, {_share_words(rank)}")


def _grip(book: PriorBook) -> float | None:
    share = (book.row.get("gex_views") or {}).get("pin_top_share")
    return float(share) if is_num(share) else None


def _mean_distance(bars: list[dict], magnet: float, sigma: float) -> float:
    """How far the closes of ``bars`` sat from ``magnet`` on average, in sigma."""
    return statistics.fmean(abs(float(b["close"]) - magnet) for b in bars) / sigma


def _hug_on(prior_bars: dict[str, list[dict]]) -> Callable[[PriorBook], float | None]:
    """How far a prior session's closes over the half hour to this minute sat from its heaviest strike at this minute,
    on average, in its own ruler."""
    def measure(book: PriorBook) -> float | None:
        magnet = (book.row.get("gex_views") or {}).get("magnet")
        window = bars_finished_between(prior_bars.get(book.day) or [], book.then - timedelta(minutes=WINDOW_30_MIN), book.then)
        return _mean_distance(window, float(magnet), book.ruler.points) if book.ruler and is_num(magnet) and window else None
    return measure


# The heaviest strike's distance by its third, as magnet_seat's options name it.
SEATS = {"bottom third": "seated on it", "middle third": "near it", "top third": "away from it"}


def _magnet_distance(scene: Scene, gv: dict, ruler: SigmaRuler | None, earlier: dict | None, prior: list[PriorBook] | None,
                     ls: LabelSet) -> None:
    """The heaviest strike of today's same-day book: how far, how strong its grip is, whether it is the strike of 30
    minutes ago, and how close price sat to it over the last 30 minutes, each ranked against the same minute of the
    prior sessions (grip climbs all day and so do the distances' spreads, so no fixed cut)."""
    magnet, grip = gv.get("magnet"), gv.get("pin_top_share")
    if not is_num(magnet) or not is_num(grip):
        ls.omit("gex.magnet_distance", "row carries no heaviest strike or top-strike share for today's same-day book")
        return
    if ruler is None:
        ls.omit("gex.magnet_distance", NO_ANCHOR)
        return
    then_magnet = ((earlier or {}).get("gex_views") or {}).get("magnet")
    if not is_num(then_magnet):
        ls.omit("gex.magnet_distance", f"no diary row from {WINDOW_30_MIN} minutes ago carries the heaviest strike")
        return
    window = bars_finished_between(scene.bars, scene.now - timedelta(minutes=WINDOW_30_MIN), scene.now)
    if not window:
        ls.omit("gex.magnet_distance", f"no minute bar finished in the last {WINDOW_30_MIN} minutes")
        return
    d = (float(magnet) - scene.spot) / ruler.points
    hug = _mean_distance(window, float(magnet), ruler.points)
    seat, no_seat = _book_rank(prior, abs(d), _distance_to("magnet"), "a heaviest same-day strike")
    grip_rank, no_grip = _book_rank(prior, float(grip), _grip, "a top-strike share")
    hug_rank, no_hug = _book_rank(prior, hug, _hug_on(scene.prior_bars), "a heaviest same-day strike and the half hour's bars")
    if seat is None or grip_rank is None or hug_rank is None:
        ls.omit("gex.magnet_distance", no_seat or no_grip or no_hug)
        return
    median = "at or above the median" if grip_rank.share >= HALF_RANK else "below the median"
    same = "the same strike as" if float(magnet) == float(then_magnet) else "a different strike from"
    held = "held there" if hug_rank.band == "bottom third" else "not held there"
    ls.put("gex.magnet_distance",
           f"today's heaviest same-day strike (the magnet) sits {sig(abs(d))} {above_or_below(d)} price, {_farther(seat)}: "
           f"{SEATS[seat.band]}; its grip (top-strike share) is stronger than on {grip_rank.higher_than} of the last {grip_rank.of} "
           f"sessions at {scene.now.astimezone(ET):%H:%M} ET, {median}; it is {same} {WINDOW_30_MIN} minutes ago; over the last "
           f"{plural(len(window), 'minute')} price sat {sig(hug)} from it on average, farther than on {hug_rank.higher_than} of the last "
           f"{hug_rank.of} sessions at this minute, {hug_rank.band}: {held}{_estimated(ruler)}")


def _straddles_to_magnet(book: PriorBook) -> float | None:
    """How far a prior row's heaviest strike sat from its spot, in what that day's straddle still priced."""
    magnet, spot = (book.row.get("gex_views") or {}).get("magnet"), book.row.get("spot")
    em = (book.row.get("range_ruler") or {}).get("em_points")
    return abs(float(magnet) - float(spot)) / float(em) if is_num(magnet) and is_num(spot) and is_num(em) and em > 0 else None


def _settle_pull(scene: Scene, gv: dict, ruler: SigmaRuler | None, prior: list[PriorBook] | None, ls: LabelSet) -> None:
    """The heaviest strike of today's same-day book against price in sigma and in what today's straddle still
    prices before the settle (the same distance is out of reach at 13:30 and inside the priced move at 15:30),
    that count ranked against the prior sessions' at this minute (the top third out of reach), with the book's
    gamma-weighted centre beside it."""
    settle = todays_settle(scene.now)
    magnet, centre = gv.get("magnet"), gv.get("pin_centroid")
    if settle is None:
        ls.omit("gex.settle_pull", "today's same-day options have settled")
        return
    if not is_num(magnet):
        ls.omit("gex.settle_pull", "row carries no heaviest strike for today's same-day book")
        return
    if ruler is None:
        ls.omit("gex.settle_pull", NO_ANCHOR)
        return
    straddles = remaining_straddles(scene, float(magnet) - scene.spot)
    if straddles is None:
        ls.omit("gex.settle_pull", "row carries no straddle left for today's same-day book")
        return
    rank, no_rank = _book_rank(prior, straddles, _straddles_to_magnet, "a heaviest same-day strike and a straddle left")
    if rank is None:
        ls.omit("gex.settle_pull", no_rank)
        return
    d = (float(magnet) - scene.spot) / ruler.points
    seat = f"inside the {SETTLE_SEAT_SIGMA} sigma seat distance" if abs(d) <= SETTLE_SEAT_SIGMA else f"outside the {SETTLE_SEAT_SIGMA} sigma seat distance"
    reach = (f"more than on {rank.higher_than} of the last {rank.of} sessions at this minute, {rank.band}: "
             f"{'out of reach' if rank.band == 'top third' else 'in reach'}")
    if is_num(centre):
        c = (float(centre) - scene.spot) / ruler.points
        centre_words = (f"the gamma-weighted centre of the book sits {sig(abs(c))} {above_or_below(c)} price, "
                        f"{'inside' if abs(c) <= SETTLE_SEAT_SIGMA else 'outside'} the seat distance, "
                        f"{'the same side' if (c >= 0) == (d >= 0) else 'the other side'}")
    else:
        centre_words = "no strike of the book pulls, so it has no gamma-weighted centre"
    left = round((settle - scene.now).total_seconds() / 60.0)
    ls.put("gex.settle_pull",
           f"today's heaviest same-day strike sits {sig(abs(d))} {above_or_below(d)} price, {seat}; that is {straddles:.1f} remaining straddles "
           f"(what today's options still price before the {settle.astimezone(ET):%H:%M} settle), {reach}; {centre_words}; "
           f"the options settle in {plural(left, 'minute')}{_estimated(ruler)}")


# The charm wall's distance by its third, as afternoon_charm_wall's options name it.
CHARM_REACH = {"bottom third": "at price", "middle third": "near price", "top third": "far from price"}


def _charm_wall_distance(scene: Scene, gv: dict, ruler: SigmaRuler | None, prior: list[PriorBook] | None, ls: LabelSet) -> None:
    """Where the charm wall of today's same-day book sits against price, its distance ranked against the prior
    sessions' at this minute; a location only, never a direction (the scanner suspends the charm direction word)."""
    wall = gv.get("charm_wall")
    if not is_num(wall):
        ls.omit("gex.charm_wall_distance", "row carries no charm wall for today's same-day book")
        return
    if ruler is None:
        ls.omit("gex.charm_wall_distance", NO_ANCHOR)
        return
    d = (float(wall) - scene.spot) / ruler.points
    rank, no_rank = _book_rank(prior, abs(d), _distance_to("charm_wall"), "a charm wall")
    if rank is None:
        ls.omit("gex.charm_wall_distance", no_rank)
        return
    band = f"{_farther(rank)}: {CHARM_REACH[rank.band]}"
    straddles = remaining_straddles(scene, float(wall) - scene.spot)
    away = f", {straddles:.1f} remaining straddles away" if straddles is not None else ""
    ls.put("gex.charm_wall_distance", f"the charm wall of today's same-day book (the strike where its delta decay piles up) sits "
                                      f"{sig(abs(d))} {above_or_below(d)} price, {band}{away}{_estimated(ruler)}")


def _gamma_walls(row: dict | None) -> tuple[float, float] | None:
    """The heaviest call-side and put-side gamma strikes of a row's same-day book."""
    gv = (row or {}).get("gex_views") or {}
    call, put = gv.get("call_wall_gamma"), gv.get("put_wall_gamma")
    return (float(call), float(put)) if is_num(call) and is_num(put) else None


def _box_width(book: PriorBook) -> float | None:
    strikes = _gamma_walls(book.row)
    return (strikes[0] - strikes[1]) / book.ruler.points if strikes and book.ruler else None


# The box's width by its third, as wall_box's options name it.
BOXES = {"bottom third": "a tight box", "middle third": "a middling box", "top third": "a wide box"}


def _wall_box(scene: Scene, ruler: SigmaRuler | None, earlier: dict | None, prior: list[PriorBook] | None, ls: LabelSet) -> None:
    """The width of the box between the two heaviest strikes of today's same-day book, ranked against the prior
    sessions' at this minute, and whether every diary row since the one 30 minutes ago carried the same two strikes."""
    now, then = _gamma_walls(scene.row), _gamma_walls(earlier)
    if now is None:
        ls.omit("gex.wall_box", "row carries no heaviest call-side and put-side strikes for today's same-day book")
        return
    if ruler is None:
        ls.omit("gex.wall_box", NO_ANCHOR)
        return
    if then is None:
        ls.omit("gex.wall_box", f"no diary row from {WINDOW_30_MIN} minutes ago carries the heaviest call-side and put-side strikes")
        return
    width = (now[0] - now[1]) / ruler.points
    rank, no_rank = _book_rank(prior, width, _box_width, "the heaviest call-side and put-side strikes")
    if rank is None:
        ls.omit("gex.wall_box", no_rank)
        return
    band = f"wider than on {rank.higher_than} of the last {rank.of} sessions at this minute, {rank.band}: {BOXES[rank.band]}"
    since = datetime.fromisoformat(earlier["ts"])
    between = {w for r in scene.rows_today if datetime.fromisoformat(r["ts"]) >= since and (w := _gamma_walls(r)) is not None}
    change = (f"a heavy strike has changed in the last {WINDOW_30_MIN} minutes" if now != then else
              f"both have stood at the same strikes for the last {WINDOW_30_MIN} minutes" if between == {now} else
              f"a heavy strike changed within the last {WINDOW_30_MIN} minutes and is back where it was")
    ls.put("gex.wall_box", f"the heaviest call-side and put-side strikes of today's same-day book are {sig(width)} apart, {band}; {change}{_estimated(ruler)}")


def _walls_since_30min(scene: Scene, ruler: SigmaRuler | None, earlier: dict | None, ls: LabelSet) -> None:
    now, then = _gamma_walls(scene.row), _gamma_walls(earlier)
    if now is None:
        ls.omit("gex.walls_since_30min", "row carries no heaviest call-side and put-side strikes for today's same-day book")
        return
    if ruler is None:
        ls.omit("gex.walls_since_30min", NO_ANCHOR)
        return
    if then is None:
        ls.omit("gex.walls_since_30min", f"no diary row from {WINDOW_30_MIN} minutes ago carries the heaviest call-side and put-side strikes")
        return
    if now == then:
        ls.put("gex.walls_since_30min", f"over the last {WINDOW_30_MIN} minutes neither heavy strike of today's same-day book has moved: "
                                        f"the call-side and the put-side strikes both stayed put{_estimated(ruler)}")
        return

    def moved(new: float, old: float) -> str:
        d = (new - old) / ruler.points
        return "stayed put" if d == 0 else f"moved {'up' if d > 0 else 'down'} {sig(abs(d))}"

    width, then_width = now[0] - now[1], then[0] - then[1]
    shift = now[0] - then[0]
    relation = ("so the two are closer together" if width < then_width else "so the two are further apart" if width > then_width else
                f"so the pair moved {'up' if shift > 0 else 'down'} together, as far apart as before")
    ls.put("gex.walls_since_30min", f"over the last {WINDOW_30_MIN} minutes the call-side heavy strike of today's same-day book {moved(now[0], then[0])} "
                                    f"and the put-side strike {moved(now[1], then[1])}, {relation}{_estimated(ruler)}")


def _book_vs_pace_gate(scene: Scene, ls: LabelSet) -> None:
    if scene.now.astimezone(ET).time() < BOOK_VS_PACE_FROM:
        ls.sleep("book_vs_pace", f"before {BOOK_VS_PACE_FROM:%H:%M} ET")
    else:
        ls.wake("book_vs_pace")


def _settle_pull_gate(scene: Scene, gv: dict, ruler: SigmaRuler | None, prior: list[PriorBook] | None, ls: LabelSet) -> None:
    """settle_pull_side sleeps while magnet_seat's code answer is held_on_magnet or seated_fresh: the heaviest
    strike's distance in the bottom third of the prior sessions' at this minute."""
    magnet = gv.get("magnet")
    if not is_num(magnet) or ruler is None:
        ls.sleep("settle_pull_side", "no heaviest same-day strike or no sigma ruler to place it")
        return
    d = abs(float(magnet) - scene.spot) / ruler.points
    seat, no_seat = _book_rank(prior, d, _distance_to("magnet"), "a heaviest same-day strike")
    if seat is None:
        ls.sleep("settle_pull_side", f"whether price is seated on today's heaviest same-day strike is not known: {no_seat}")
    elif seat.band == "bottom third":
        ls.sleep("settle_pull_side", f"price sits {sig(d)} from today's heaviest same-day strike, {_farther(seat)}: {SEATS[seat.band]}")
    else:
        ls.wake("settle_pull_side")
