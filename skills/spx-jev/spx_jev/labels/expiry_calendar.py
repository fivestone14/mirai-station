"""The expiry and calendar family: which book expires when and which book the weight sits in (expiry.*),
from expiry.py, and the calendar around it (calendar.*).

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

from datetime import date, timedelta

from ..cuts import AFTER_OPEX_DAYS, MONTH_TURN_DAYS, ONE_DAY, OPEX_WEEK_DAYS, REBAL_WINDOW_DAYS, ZERO_DTE_LAST_HOUR_MIN
from ..events import covered_through, on_day
from ..expiry import QUARTER_MONTHS, expiry_kinds, monthly_expiry, next_monthly, settle_at, todays_settle, trading_days_between
from ..sessions import SESSION_OPEN, is_trading_day, next_trading_day
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ET, is_num
from .words import ordinal, pct, plural

LABELS = ("expiry.settle_clock", "expiry.opex_today", "expiry.dated_weight", "expiry.today_vs_week",
          "calendar.event_cycle", "calendar.expiry_phase", "calendar.month_turn", "expiry.next_book_magnet", "expiry.quarter_end_strikes")
GATES: tuple[str, ...] = ()
# The major releases, as the calendar names them: a day is the eve of the Fed or of a report out before the next
# open, or the day after any of them.
PRE_OPEN_RELEASES = ("JOBS", "CPI")
MAJOR_RELEASES = ("FOMC", *PRE_OPEN_RELEASES)
DARK = {"expiry.next_book_magnet": "the SPX scan writes no 1DTE slice, so tomorrow's book is not on the row",
        "expiry.quarter_end_strikes": "the dated-book pull drops the expiring quarter-end band, so its strikes are not on the row"}


def build_expiry_calendar_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    _settle_clock(scene, ls)
    _opex_today(scene, ls)
    _dated_weight(scene, ls)
    _today_vs_week(scene, ls)
    today = scene.now.astimezone(ET).date()
    if not is_trading_day(today):
        for path in ("calendar.event_cycle", "calendar.expiry_phase", "calendar.month_turn"):
            ls.omit(path, f"{today} is not a trading day")
        return ls
    _event_cycle(today, ls)
    _expiry_phase(scene, today, ls)
    _month_turn(today, ls)
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


def _day_words(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B}"


def _previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _event_cycle(today: date, ls: LabelSet) -> None:
    """Where today sits in the cycle of major releases (the calendar's rows of any tier): the eve of the Fed's
    decision, the eve of a jobs or consumer price report out before the next open, or the day after one of them."""
    prev, nxt = _previous_trading_day(today), next_trading_day(today)
    through = covered_through()
    if through is None or nxt > through:
        ls.omit("calendar.event_cycle", f"the event calendar is kept through {through or 'no named day'}, so the next session's releases are unknown")
        return
    fed_next = any(e.kind == "FOMC" for e in on_day(nxt))
    before_open = [e for e in on_day(nxt) if e.kind in PRE_OPEN_RELEASES and e.start.time() < SESSION_OPEN]
    after = [e.words for e in on_day(prev) if e.kind in MAJOR_RELEASES]
    if fed_next:
        ls.put("calendar.event_cycle", f"the next session, {_day_words(nxt)}, brings the Fed's rate decision: today is the eve of the Fed")
    elif before_open:
        e = before_open[0]
        ls.put("calendar.event_cycle", f"{e.words} comes out at {e.start:%H:%M} before the next session's open, {_day_words(nxt)}: "
                                       f"today is the eve of a major release")
    elif after:
        ls.put("calendar.event_cycle", f"the previous session, {_day_words(prev)}, brought {' and '.join(after)}: today is the day after a major release")
    else:
        ls.put("calendar.event_cycle", f"no jobs report, consumer price report or Fed decision fell on the previous session, {_day_words(prev)}, "
                                       f"or comes before or during the next, {_day_words(nxt)}: an ordinary day")


def _previous_monthly(today: date) -> date:
    """The latest monthly expiry before today."""
    m = monthly_expiry(today.year, today.month)
    if m < today:
        return m
    return monthly_expiry(today.year - (today.month == 1), (today.month - 2) % 12 + 1)


def _expiry_phase(scene: Scene, today: date, ls: LabelSet) -> None:
    """Which part of the expiry cycle today is: the quarter-end, a monthly or quarterly expiry, or its distance
    in trading days to the next monthly and from the last one, each against its window. Which dated book holds
    the most gamma is left out: the dated-book pull drops the expiring quarter-end band."""
    kinds = expiry_kinds(today)
    close = f"today's {settle_at(today):%H:%M} close"
    parts = []
    if "quarter_end" in kinds:
        parts.append(f"today, {_day_words(today)}, is the quarter's last trading day: the quarter-end SPXW options settle at {close}")
    if "monthly" in kinds:
        which = "quarterly" if "quarterly" in kinds else "monthly"
        parts.append(f"today, {_day_words(today)}, is the {which} expiry: the morning-settled SPX monthly options settled on this "
                     f"morning's opening prints and the daily SPXW options settle at {close}")
    else:
        if not parts:
            parts.append(f"today, {_day_words(today)}, is an ordinary daily expiry")
        ahead = next_monthly(scene.now)
        n = trading_days_between(today, ahead)
        inside = "inside" if ONE_DAY <= n <= OPEX_WEEK_DAYS else "outside"
        parts.append(f"the {ahead.day} {ahead:%B} monthly is {plural(n, 'trading day')} away, {inside} the {OPEX_WEEK_DAYS}-day opex-week window")
        last = _previous_monthly(today)
        n = trading_days_between(last, today)
        inside = "inside" if ONE_DAY <= n <= AFTER_OPEX_DAYS else "outside"
        parts.append(f"the {last.day} {last:%B} monthly was {plural(n, 'trading day')} ago, {inside} the {AFTER_OPEX_DAYS}-day after-opex window")
    ls.put("calendar.expiry_phase", "; ".join(parts))


def _month_turn(today: date, ls: LabelSet) -> None:
    """Today's place among the month's trading days: one of its last (month-end rebalancing) or its first
    (new money)."""
    month_end = date(today.year + (today.month == 12), today.month % 12 + 1, 1) - timedelta(days=1)
    left = trading_days_between(today, month_end)
    nth = trading_days_between(today.replace(day=1) - timedelta(days=1), today)
    if left < REBAL_WINDOW_DAYS:
        which = {0: "the last", 1: "the second-to-last"}.get(left, f"the {ordinal(left + 1)}-to-last")
        quarter = " and of the quarter" if left == 0 and today.month in QUARTER_MONTHS else ""
        text = f"today, {_day_words(today)}, is {which} trading day of the month{quarter}, inside the month's last {REBAL_WINDOW_DAYS} trading days"
        if left == 0:
            text += f"; the next session is the first trading day of {next_trading_day(today):%B}"
    elif nth <= MONTH_TURN_DAYS:
        text = f"today, {_day_words(today)}, is the {ordinal(nth)} trading day of {today:%B}, inside the month's first {MONTH_TURN_DAYS} trading days"
    else:
        text = (f"today, {_day_words(today)}, is the {ordinal(nth)} trading day of {today:%B} with {plural(left, 'trading day')} left after it, "
                f"outside the month's first {MONTH_TURN_DAYS} and last {REBAL_WINDOW_DAYS} trading days")
    ls.put("calendar.month_turn", text)
