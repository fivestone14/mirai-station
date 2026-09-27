"""The gamma family: the dealers' options book against price, its walls, its weight and its ladder (gex.*).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..cuts import (EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, GRIP_CONCENTRATED_SHARE, GRIP_SPREAD_SHARE, WALL_NEAR_SIGMA, WALL_THICK_SHARE,
                    WALL_THIN_SHARE, WALL_TOUCH_QUIET_PERCENTILE, WALL_TOUCH_SIEGE_PERCENTILE)
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import is_num, walls
from .words import ordinal, pct, plural, sig

LABELS = ("gex.weight_side", "gex.air_to_wall", "gex.wall_thickness", "gex.heaviest_strike_grip", "gex.delta_weight_side",
          "gex.expiry_roll", "gex.ladder_state", "gex.wall_touch_volume",
          "gex.balance_vs_yesterday", "gex.book_balance", "gex.charm_wall_distance", "gex.cushion_if_moved", "gex.flip_distance",
          "gex.magnet_distance", "gex.settle_pull", "gex.wall_box", "gex.walls_since_30min", "gex.weight_both_books")
GATES = ("book_vs_pace", "settle_pull_side")
DARK = {"gex.cushion_if_moved": "the row carries no cushion: sliding the gamma flip by half a sigma needs the full options chain, "
                                "which the scan has and the diary does not write (gex_views.cushion_up and cushion_down)"}

# A wall touch's SPY volume (gex.wall_touch_volume) is described while its verdict is this recent.
WALL_TOUCH_RECENT_MIN = 30


def build_gamma_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    gv = scene.row.get("gex_views") or {}
    _weight_side(gv, ls)
    near = _air_to_wall(scene, ls)
    _wall_thickness(gv, near, ls)
    _heaviest_strike_grip(gv, ls)
    _delta_weight_side(scene, ls)
    _expiry_roll(scene, ls)
    _ladder_state(scene, ls)
    _wall_touch_volume(scene, ls)
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


def _delta_weight_side(scene: Scene, ls: LabelSet) -> None:
    above_d = (scene.row.get("dex_views") or {}).get("dex_above_spot")
    if not is_num(above_d):
        ls.omit("gex.delta_weight_side", "row carries no delta split")
    elif 1.0 - above_d > EVEN_SPLIT_HIGH:
        ls.put("gex.delta_weight_side", f"{pct(1.0 - above_d)} of dealers' directional exposure across the 0-to-7-day books sits at strikes below price, most of it")
    elif 1.0 - above_d < EVEN_SPLIT_LOW:
        ls.put("gex.delta_weight_side", f"{pct(above_d)} of dealers' directional exposure across the 0-to-7-day books sits at strikes above price, most of it")
    else:
        ls.put("gex.delta_weight_side", f"dealers' directional exposure across the 0-to-7-day books is split about evenly, {pct(1.0 - above_d)} below price and {pct(above_d)} above")


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
