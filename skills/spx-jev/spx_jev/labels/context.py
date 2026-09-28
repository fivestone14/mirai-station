"""The context family: what every request carries (the symbol, the units, the horizon) and where the
session stands. A read before the open (the premarket lane) says how long until the open, names the
pre-open ruler its sizes are in, and has no cash move today to time."""
from __future__ import annotations

from ..cuts import MOVE_RULE_SIGMA, NIGHT_RANK_COUNT, RSI_OVERBOUGHT, RSI_OVERSOLD
from ..row_adapter import SYMBOL
from ..sessions import session_minutes
from ..state_builder import Scene
from .label_set import LabelSet
from .measures import ONE_MINUTE, bar_time
from .words import pct, plural

LABELS = ("context.symbol", "context.units", "context.horizon", "context.session_progress", "context.time_since_last_move")
GATES: tuple[str, ...] = ()
DARK: dict[str, str] = {}

UNITS = ("all distances are in sigma, today's expected move for the S&P 500 index; "
         "a plus sign means above price and a minus sign means below price; "
         f"a size said 'at this minute' is ranked against the same measure at the same time of day on up to the last {NIGHT_RANK_COUNT} "
         "sessions and named by its third: bottom, middle or top. "
         "Options weight means the hedging exposure of dealers, the market makers on the other side of the options, at each strike; "
         "a wall is a strike where that weight piles up, the nearest one standing in today's 0DTE book, else in the 1-to-7-day book; "
         "0DTE means the options that expire today; the opening box is the first half hour's price range; "
         f"RSI is a 0 to 100 gauge of overbought (above {RSI_OVERBOUGHT}) or oversold (below {RSI_OVERSOLD}); "
         "the NYSE tick is how many NYSE stocks last traded up minus how many last traded down")
# Added to the gloss on the tape lane, where the stretch labels are sized by the tape unit.
TAPE_UNITS = ("; a tape unit is the middle of the last three 5-minute price ranges, in index points, "
              "measured afresh at every read, and the labels that use it state it")
# The gloss before the open, where nothing has traded in the index today and the night is told from the futures.
PREMARKET_UNITS = ("all distances are in sigma, the pre-open ruler: the median of the last sessions' morning expected move "
                   "for the S&P 500 index, the same ruler at every read before the open; "
                   "a move in percent is the change over the price it started from; "
                   "futures' 16:00 price is where S&P futures stood at 16:00 ET on the last session, when the index closed, the night's starting point; "
                   "the settled open is the index's price at the close of its 09:34 bar, four minutes after the open")


def build_context_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    ls.put("context.symbol", SYMBOL)
    if scene.premarket:
        ls.put("context.units", PREMARKET_UNITS)
        ls.put("context.horizon", scene.horizon)
        _before_the_open(scene, ls)
        ls.omit("context.time_since_last_move", "before the open: the index has not traded today")
        return ls
    ls.put("context.units", UNITS + TAPE_UNITS if scene.bar_clock else UNITS)
    ls.put("context.horizon", scene.horizon)
    _session_progress(scene, ls)
    _time_since_last_move(scene, ls)
    return ls


def _before_the_open(scene: Scene, ls: LabelSet) -> None:
    hours, minutes = divmod(max(round(-scene.minutes_since_open), 0), 60)
    if not hours:
        wait = plural(minutes, "minute")
    elif not minutes:
        wait = plural(hours, "hour")
    else:
        wait = f"{plural(hours, 'hour')} and {plural(minutes, 'minute')}"
    ls.put("context.session_progress", f"the session has not opened: it opens at {scene.session_open:%H:%M} ET, in {wait}")


def _session_progress(scene: Scene, ls: LabelSet) -> None:
    since_open, to_close = scene.minutes_since_open, scene.minutes_to_close
    frac = max(0.0, min(1.0, since_open / session_minutes(scene.now)))
    words = ["first", "second", "third", "fourth", "last"][min(4, int(frac * 5))]
    m, k = max(round(since_open), 0), max(round(to_close), 0)
    if since_open < 60:
        phase = f"the first hour, {plural(m, 'minute')} after the open"
    elif to_close <= 60:
        phase = f"the last hour, {plural(k, 'minute')} before the close"
    else:
        phase = f"the middle of the session, {plural(m, 'minute')} after the open and {plural(k, 'minute')} before the close"
    ls.put("context.session_progress", f"{pct(frac)} of the session has passed, the {words} fifth; {phase}")


def _time_since_last_move(scene: Scene, ls: LabelSet) -> None:
    bars = scene.bars
    if len(bars) < 11:
        ls.omit("context.time_since_last_move", "needs 10 minutes of finished bars")
        return
    closes = [float(x["close"]) for x in bars]
    last_idx = None
    for i in range(10, len(closes)):
        if abs(closes[i] - closes[i - 10]) / scene.sigma >= MOVE_RULE_SIGMA:
            last_idx = i
    if last_idx is None:
        ls.put("context.time_since_last_move", f"price has made no real move of {MOVE_RULE_SIGMA} sigma within 10 minutes at any point today, so the last real move is over 60 minutes ago or never")
        return
    ago = (scene.now - (bar_time(bars[last_idx]) + ONE_MINUTE)).total_seconds() / 60.0
    band = "under 10 minutes ago" if ago < 10 else "between 10 and 60 minutes ago" if ago <= 60 else "over 60 minutes ago"
    ls.put("context.time_since_last_move", f"price last made a real move of {MOVE_RULE_SIGMA} sigma within 10 minutes {plural(round(ago), 'minute')} ago, {band}")
