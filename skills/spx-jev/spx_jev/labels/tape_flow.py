"""The tape and flow family: what is trading in today's 0DTE book and who is taking which side (options.*), and
SPY's own tape and quote (volume.*, liquidity.*).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

from ..cuts import BUSIEST_STRIKE_SHARE, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, MIN_RANK_SESSIONS, TURNOVER_HIGH, TURNOVER_LOW
from ..state_builder import ET, OPTIONS_TAPE_MAX_AGE_MIN, OPTIONS_TAPE_WINDOW_MIN, Scene
from .label_set import LabelSet
from .measures import is_num
from .ranks import rank_at_slot
from .words import pct, signed

LABELS = ("options.turnover", "options.call_put_split", "options.aggressor_side", "options.new_activity",
          "options.big_prints_10", "options.call_put_shift_10m", "options.flow_lean_30", "options.premium_burst_5m",
          "options.premium_pace_30", "options.quote_liquidity", "options.strike_defense", "volume.spy_last30_share",
          "volume.spy_pace_30", "liquidity.spy_quote", "liquidity.spy_book_lean")
GATES = ("opening_premium_burst",)
DARK = {"liquidity.spy_book_lean": "the lob-flow collector saves no per-minute SPY bid and ask sizes or order-flow imbalance"}


def build_tape_flow_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    gv = scene.row.get("gex_views") or {}
    _turnover_and_split(gv, ls)
    _aggressor_side(scene, ls)
    _new_activity(scene, gv, ls)
    return ls


def _turnover_and_split(gv: dict, ls: LabelSet) -> None:
    vols = [(float(v[1]), float(v[2])) for v in gv.get("vol_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and is_num(v[1]) and is_num(v[2])]
    ois = [abs(float(v[1])) + abs(float(v[2])) for v in gv.get("oi_side_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 3 and is_num(v[1]) and is_num(v[2])]
    calls, puts = sum(c for c, _ in vols), sum(p for _, p in vols)
    traded, standing = calls + puts, sum(ois)
    low, high = TURNOVER_LOW, TURNOVER_HIGH
    if traded <= 0 or standing <= 0:
        ls.omit("options.turnover", "no contracts traded or no standing open interest")
    else:
        r = traded / standing
        band = f"under {low:g} times it" if r < low else f"between {low:g} and {high:g} times it" if r <= high else f"more than {high:g} times it"
        ls.put("options.turnover", f"today's 0DTE contracts traded are {r:.1f} times their standing open position, {band}")
    lo_cut, hi_cut = EVEN_SPLIT_LOW, EVEN_SPLIT_HIGH
    if traded <= 0:
        ls.omit("options.call_put_split", "no contracts traded yet today")
    else:
        cs = calls / traded
        band = (f"mostly calls, beyond the {pct(hi_cut)} cut" if cs > hi_cut else
                f"mostly puts, calls under the {pct(lo_cut)} cut" if cs < lo_cut else f"an even split within {pct(lo_cut)} to {pct(hi_cut)}")
        ls.put("options.call_put_split", f"{pct(cs)} of today's 0DTE contracts traded near price were calls, {band}")


def _aggressor_side(scene: Scene, ls: LabelSet) -> None:
    """Who is taking the other side of the 0DTE options market makers over the last 15 minutes: the
    lob-flow collector's delta-weighted tilt, ranked against the same minute on the prior sessions."""
    tape = scene.options_tape
    reading = tape.at(scene.now) if tape else None
    if reading is None:
        ls.omit("options.aggressor_side", f"no reading of the 0DTE options tape from the lob-flow collector in the last {OPTIONS_TAPE_MAX_AGE_MIN} minutes")
        return
    tilt, determinate = reading
    now_et = scene.now.astimezone(ET)
    base = [r[0] for d in scene.prior_bars
            if (r := tape.at(now_et.replace(year=int(d[:4]), month=int(d[5:7]), day=int(d[8:10])))) is not None]
    rank = rank_at_slot(tilt, base)
    if rank is None:
        ls.omit("options.aggressor_side", f"needs {MIN_RANK_SESSIONS} prior sessions with an options tape reading at this minute, have {len(base)}")
        return
    shown = round(tilt, 2) or 0.0          # the lean is the sign of the tilt as the sentence prints it
    lean = ("toward buying calls and selling puts" if shown > 0 else "toward buying puts and selling calls" if shown < 0 else "neither way")
    ls.put("options.aggressor_side",
           f"over the last {OPTIONS_TAPE_WINDOW_MIN} minutes the 0DTE options tape leaned {lean}, a tilt of {signed(tilt)} on a scale "
           f"from -1 to +1 with each trade weighted by its delta, resting on the {pct(determinate)} of that flow whose side could be told; "
           f"in the {rank['band']} for this minute, higher than {rank['higher_than']} of {rank['of']} prior sessions")


def _new_activity(scene: Scene, gv: dict, ls: LabelSet) -> None:
    gross = [(float(v[0]), float(v[1])) for v in gv.get("vol_gross_by_strike") or []
             if isinstance(v, (list, tuple)) and len(v) >= 2 and is_num(v[0]) and is_num(v[1])]
    total = sum(v for _, v in gross)
    if not gross or total <= 0:
        ls.omit("options.new_activity", "no contracts traded yet today")
        return
    busiest, top = max(gross, key=lambda kv: kv[1])
    share = top / total
    nearest = min(gross, key=lambda kv: abs(kv[0] - scene.spot))[0]
    if share <= BUSIEST_STRIKE_SHARE:
        ls.put("options.new_activity", f"today's 0DTE option volume is spread across strikes, no strike holding more than {pct(BUSIEST_STRIKE_SHARE)} of it; the busiest holds {pct(share)}")
    elif busiest == nearest:
        ls.put("options.new_activity", f"the busiest 0DTE strike today is the one nearest price, holding {pct(share)} of the day's 0DTE option volume, more than the {pct(BUSIEST_STRIKE_SHARE)} cut")
    else:
        ls.put("options.new_activity", f"the busiest 0DTE strike today sits {'above' if busiest > scene.spot else 'below'} price, holding {pct(share)} of the day's 0DTE option volume, more than the {pct(BUSIEST_STRIKE_SHARE)} cut")
