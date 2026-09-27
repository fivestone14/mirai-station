"""The expiry and calendar family: which book expires when and which book the weight sits in (expiry.*),
from expiry.py."""
from __future__ import annotations

from ..cuts import ZERO_DTE_LAST_HOUR_MIN
from ..expiry import expiry_kinds, next_monthly, todays_settle, trading_days_between
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import is_num
from .words import pct, plural

LABELS = ("expiry.settle_clock", "expiry.opex_today", "expiry.dated_weight", "expiry.today_vs_week")


def build_expiry_calendar_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _settle_clock(scene, ls)
    _opex_today(scene, ls)
    _dated_weight(scene, ls)
    _today_vs_week(scene, ls)
    return ls


def _settle_clock(scene: Scene, ls: LabelSet) -> None:
    settle = todays_settle(scene.now)
    if settle is None:
        ls.omit("expiry.settle_clock", "no 0DTE expiry is left today")
        return
    left = max(round((settle - scene.now).total_seconds() / 60.0), 0)
    close = "the 13:00 ET half-day close" if settle.hour == 13 else "the 16:00 ET close"
    window = (f"inside their last {ZERO_DTE_LAST_HOUR_MIN} minutes, when their gamma decays fastest" if left <= ZERO_DTE_LAST_HOUR_MIN
              else f"before their last {ZERO_DTE_LAST_HOUR_MIN} minutes, when their gamma decays fastest")
    ls.put("expiry.settle_clock", f"today's 0DTE SPXW options settle at {close}, {plural(left, 'minute')} from now, {window}")


def _opex_today(scene: Scene, ls: LabelSet) -> None:
    today = scene.now.date()
    kinds = expiry_kinds(today)
    monthly = ("the AM-settled SPX monthly options settled on this morning's opening prints "
               "and the PM-settled SPXW monthly options settle at the close")
    if "quarterly" in kinds:
        text = f"today is the quarterly expiry: {monthly}"
    elif "monthly" in kinds:
        text = f"today is the monthly expiry: {monthly}"
    else:
        ahead = trading_days_between(today, next_monthly(scene.now))
        text = f"today is an ordinary daily expiry; the next monthly expiry is {plural(ahead, 'trading day')} away"
    if "quarter_end" in kinds:
        text += "; it is also the quarter's last trading day, when the quarter-end SPXW options settle at the close"
    ls.put("expiry.opex_today", text)


def _dated_weight(scene: Scene, ls: LabelSet) -> None:
    dated = scene.row.get("dated_gex") or {}
    bands = [b for b in dated.get("bands") or [] if is_num(b.get("gamma_mass")) and b["gamma_mass"] > 0 and is_num(b.get("dte"))]
    if not bands:
        ls.omit("expiry.dated_weight", "row carries no dated books")
        return
    if dated.get("staleness") != "fresh":
        ls.omit("expiry.dated_weight", f"the dated books are {dated.get('staleness') or 'of unknown age'}, not this morning's")
        return
    names = {"monthly": "monthly", "quarter_end": "quarter-end"}
    top = max(bands, key=lambda b: b["gamma_mass"])
    counts = ", ".join(f"{sum(1 for b in bands if b.get('band') == k)} {names.get(k, str(k))}"
                       for k in dict.fromkeys(b.get("band") for b in bands))
    share = top["gamma_mass"] / sum(b["gamma_mass"] for b in bands)
    ls.put("expiry.dated_weight", f"of the dated books pulled this morning ({counts}), the {names.get(top.get('band'), 'dated')} expiry "
                                  f"{plural(int(top['dte']), 'day')} out holds the most options weight, {pct(share)} of their gamma")


def _today_vs_week(scene: Scene, ls: LabelSet) -> None:
    gv = scene.row.get("gex_views") or {}
    words = {"long_gamma": "long gamma, where dealers' hedging damps moves", "short_gamma": "short gamma, where dealers' hedging feeds moves",
             "uncertain": "balanced, too close to call"}
    today_book, week_book = gv.get("regime"), gv.get("regime_tenor")
    if gv.get("regime_source") != "0dte":
        ls.omit("expiry.today_vs_week", "today's 0DTE book could not be read on this row, so the scanner fell back to the blended book")
    elif today_book not in words or week_book not in words:
        ls.omit("expiry.today_vs_week", "row carries no regime for both today's 0DTE book and the 0-to-7-day book")
    elif today_book == week_book:
        ls.put("expiry.today_vs_week", f"today's 0DTE book and the blended 0-to-7-day book both read {words[today_book]}, so they agree")
    else:
        ls.put("expiry.today_vs_week", f"today's 0DTE book reads {words[today_book]}, while the blended 0-to-7-day book reads {words[week_book]}, so they disagree")
