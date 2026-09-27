"""The tape and flow family: what is trading in today's 0DTE book and who is taking which side (options.*), and
SPY's own tape and quote (volume.*, liquidity.*).

The 0DTE tape labels read the lob-flow collector's raw tape, ``state/lob_flow/raw/{day}/tape.jsonl``
(``tape.jsonl.gz`` once the collector archives the day): one line per 0DTE SPXW trade with the quote it
printed into. Lines land out of time order, so each trade is placed by its own ``ts_ms``, never by where it
sits in the file, and a minute counts once it has finished. A trade's side is told from where it printed
inside that quote: above the mid it was bought, below it sold. A multi-leg trade (an OPRA multi-leg
condition) is part of a package, so it is no single trade and has no side of its own; its premium still
counts as premium traded.

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

import gzip
import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from ..cuts import (BIG_LEAN_SHARE, BIG_MIN_PRINTS, BIG_PRINT_LOTS, BOTTOM_FIFTH, BUSIEST_STRIKE_SHARE, CALL_PUT_SHIFT_SHARE,
                    DEFENSE_MIN_EVENTS, DEFENSE_NEAR_SIGMA, DEFENSE_REFILL_SHARE, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW, FLOW_LEAN_RANK,
                    MIN_RANK_SESSIONS, THIN_VOLUME_PCT, TOP_FIFTH, TURNOVER_HIGH, TURNOVER_LOW, WINDOW_10_MIN, WINDOW_30_MIN)
from ..row_adapter import labeller_row
from ..state_builder import (ET, OPTIONS_TAPE_MAX_AGE_MIN, OPTIONS_TAPE_SUBDIR, OPTIONS_TAPE_WINDOW_MIN, ROWS_SUBDIR, Scene, load_jsonl,
                             parse_ts)
from .label_set import LabelSet
from .measures import is_num, minute_of_day
from .ranks import SameClockRank, rank_against, rank_at_slot
from .rulers import sigma_anchor
from .words import pct, plural, sig, signed

LABELS = ("options.turnover", "options.call_put_split", "options.aggressor_side", "options.new_activity",
          "options.big_prints_10", "options.call_put_shift_10m", "options.flow_lean_30", "options.premium_burst_5m",
          "options.premium_pace_30", "options.quote_liquidity", "options.strike_defense", "volume.spy_last30_share",
          "volume.spy_pace_30", "liquidity.spy_quote", "liquidity.spy_book_lean")
GATES = ("opening_premium_burst",)
DARK = {"liquidity.spy_book_lean": "the lob-flow collector saves no per-minute SPY bid and ask sizes or order-flow imbalance"}

TAPE_RAW_SUBDIR = Path("lob_flow") / "raw"
TAPE_LINE_START = b'{"ts_ms": '         # a trade line; the collector also writes gap markers
OPTION_MULTIPLIER = 100
MULTI_LEG_CONDITIONS = range(130, 145)   # OPRA's multi-leg and stock-option trade conditions (ThetaData 130-144)
# The premium burst's window: the opening lane reads every 5 minutes, so a burst is the stretch since the last read.
BURST_WINDOW_MIN = 5
TAPE_LABELS = ("options.big_prints_10", "options.flow_lean_30", "options.premium_burst_5m", "options.premium_pace_30")
# The diary writes a row about every 75 seconds: one further than this before the moment it stands for means the scanner paused.
ROW_SLACK_MIN = 5
ROW_LINE_START = '{"ts": "'
# A quote read is the median of the collector's minute-by-minute readings over this many minutes: one sweep's
# depth swings by half from one minute to the next.
QUOTE_WINDOW_MIN = 5
QUOTE_BUCKET = "d25_40"                  # the collector's 25-40 delta bucket, the same-day quotes near price that trade


def build_tape_flow_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    gv = scene.row.get("gex_views") or {}
    _turnover_and_split(gv, ls)
    _aggressor_side(scene, ls)
    _new_activity(scene, gv, ls)
    _call_put_shift_10m(scene, ls)
    _tape_labels(scene, ls)
    _quote_liquidity(scene, ls)
    _strike_defense(scene, ls)
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


# ----------------------------------------------------------------------------- new volume by side

Book = dict[float, tuple[float, float]]   # strike: (call contracts, put contracts) traded today (gex_views.vol_side_by_strike)


def _book(row: dict) -> Book:
    return {float(v[0]): (float(v[1]), float(v[2])) for v in (row.get("gex_views") or {}).get("vol_side_by_strike") or []
            if isinstance(v, (list, tuple)) and len(v) >= 3 and all(is_num(x) for x in v[:3])}


def _book_at(books: list[tuple[datetime, Book]], t: datetime) -> Book | None:
    """The newest book written at or before ``t``, and not more than ROW_SLACK_MIN before it."""
    done = [b for ts, b in books if t - timedelta(minutes=ROW_SLACK_MIN) <= ts <= t and b]
    return done[-1] if done else None


def _new_contracts(books: list[tuple[datetime, Book]], end: datetime) -> tuple[Book, float, float] | None:
    """The book at ``end`` and the calls and puts traded in the 10 minutes before it, on the strikes both rows
    carry (the row's strikes follow price); None without a row at either end."""
    now, then = _book_at(books, end), _book_at(books, end - timedelta(minutes=WINDOW_10_MIN))
    if now is None or then is None:
        return None
    common = now.keys() & then.keys()
    return now, sum(now[k][0] - then[k][0] for k in common), sum(now[k][1] - then[k][1] for k in common)


def _call_put_shift_10m(scene: Scene, ls: LabelSet) -> None:
    """The call share of the last 10 minutes' new same-day volume against the day's, and whether that new volume
    is too thin to judge against the same 10 minutes of the prior sessions."""
    got = _new_contracts([(parse_ts(r["ts"]), _book(r)) for r in scene.rows_today], scene.now)
    if got is None:
        ls.omit("options.call_put_shift_10m", f"no diary row with same-day volume by strike from {WINDOW_10_MIN} minutes ago and now")
        return
    book, calls, puts = got
    new = calls + puts
    if new <= 0:
        ls.omit("options.call_put_shift_10m", f"no new same-day contracts in the diary over the last {WINDOW_10_MIN} minutes")
        return
    if scene.state_dir is None:
        ls.omit("options.call_put_shift_10m", "no state folder to read the prior sessions' diaries from")
        return
    clock = scene.now.astimezone(ET).time()
    base = []
    for d in scene.prior_bars:
        then = _new_contracts(_prior_books(scene.state_dir, d, clock), datetime.combine(date.fromisoformat(d), clock, tzinfo=ET))
        if then is not None:
            base.append(then[1] + then[2])
    rank = rank_against(new, base)
    if rank is None:
        ls.omit("options.call_put_shift_10m", f"needs {MIN_RANK_SESSIONS} prior sessions' diaries at this minute, have {len(base)}")
        return
    day_calls, day_puts = sum(c for c, _ in book.values()), sum(p for _, p in book.values())
    new_share, day_share = calls / new, day_calls / (day_calls + day_puts)
    shift = new_share - day_share
    points = round(new_share * 100) - round(day_share * 100)      # the swing between the two shares as the sentence prints them
    line = f"the {round(CALL_PUT_SHIFT_SHARE * 100)}-point shift line"
    swing = (f"a {abs(points)}-point swing to {'calls' if points > 0 else 'puts'}" if points else "no swing either way")
    swing += f", {'past' if abs(shift) > CALL_PUT_SHIFT_SHARE else 'within'} {line}"
    thin = ("new volume was above the too-thin line for this time, so it is not too thin to judge" if rank.share >= THIN_VOLUME_PCT else
            f"new volume was under the too-thin line for this time, {rank.words()}, too thin to judge")
    ls.put("options.call_put_shift_10m", f"in the last {WINDOW_10_MIN} minutes {pct(new_share)} of new same-day option volume was calls, "
                                         f"against {pct(day_share)} since the open: {swing}; {thin}")


@lru_cache(maxsize=64)
def _prior_books(state_dir: Path, day: str, clock: time) -> list[tuple[datetime, Book]]:
    """A past day's diary books from the rows written in the 10 minutes (and the slack) before ``clock``. A row is
    about 40 KB, so each line's time is read from its start and only the rows in the stretch are parsed; the
    scanner writes its rows in time order."""
    path = Path(state_dir) / ROWS_SUBDIR / f"{day}.jsonl"
    if not path.exists():
        return []
    end = datetime.combine(date.fromisoformat(day), clock, tzinfo=ET)
    start = end - timedelta(minutes=WINDOW_10_MIN + ROW_SLACK_MIN)
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith(ROW_LINE_START):
                continue
            ts = parse_ts(line[len(ROW_LINE_START):line.index('"', len(ROW_LINE_START))])
            if ts > end:
                break
            if ts < start:
                continue
            try:
                row = labeller_row(json.loads(line))
            except ValueError:
                continue
            if row:
                out.append((ts, _book(row)))
    return out


# ----------------------------------------------------------------------------- the 0DTE tape

@dataclass
class TapeFlow:
    """Premium traded over a stretch of the 0DTE tape, in dollars: all of it, and the single-leg part whose
    side could be told, split by which way it leans (calls bought or puts sold is bullish); the same for the
    single trades of BIG_PRINT_LOTS or more."""
    premium: float = 0.0
    bullish: float = 0.0
    bearish: float = 0.0
    big_prints: int = 0
    big_bullish: float = 0.0
    big_bearish: float = 0.0

    def add(self, other: TapeFlow) -> None:
        for k, v in vars(other).items():
            setattr(self, k, getattr(self, k) + v)


@dataclass(frozen=True)
class TapeMinutes:
    """A stretch of a day's tape by minute of day, market time."""
    flows: dict[int, TapeFlow]

    def window(self, start: int, end: int) -> TapeFlow | None:
        """The minutes in [start, end) summed; None when one of them has no trade. Same-day SPX options trade in
        every minute of the session, so an empty minute is a hole in the collector's tape, which would read as a
        quiet stretch."""
        if any(m not in self.flows for m in range(start, end)):
            return None
        out = TapeFlow()
        for m in range(start, end):
            out.add(self.flows[m])
        return out


def _tape_labels(scene: Scene, ls: LabelSet) -> None:
    """The labels over the 0DTE tape's last 30 finished minutes, each window ranked against the same minutes on
    the prior sessions whose tape is on file."""
    end = minute_of_day(scene.now.astimezone(ET))
    start = end - WINDOW_30_MIN
    today, why = _today_tape(scene, start, end)
    if today is None:
        for path in TAPE_LABELS:
            ls.omit(path, why)
        ls.sleep("opening_premium_burst", why)
        return
    prior = [m for d in scene.prior_bars if (m := tape_minutes(scene.state_dir, d, start, end)) is not None]
    opened = minute_of_day(scene.session_open.astimezone(ET))
    _big_prints_10(today, opened, end, ls)
    _flow_lean_30(today, prior, opened, end, ls)
    _premium_burst_5m(today, prior, opened, end, ls)
    _premium_pace_30(today, prior, opened, end, ls)


def _today_tape(scene: Scene, start: int, end: int) -> tuple[TapeMinutes | None, str]:
    """Today's tape over [start, end), or None and why: no state folder, no file, or a collector that stopped."""
    if scene.state_dir is None:
        return None, "no state folder to read the lob-flow collector's 0DTE tape from"
    today = tape_minutes(scene.state_dir, scene.day, start, end)
    if today is None:
        return None, f"no lob-flow tape for {scene.day} under {TAPE_RAW_SUBDIR}: the collector did not run"
    if not any(m >= end - OPTIONS_TAPE_MAX_AGE_MIN for m in today.flows):
        return None, f"no 0DTE trade in the lob-flow tape in the last {OPTIONS_TAPE_MAX_AGE_MIN} minutes: the collector stopped"
    return today, ""


def _window(today: TapeMinutes, opened: int, end: int, minutes: int, path: str, ls: LabelSet) -> TapeFlow | None:
    """Today's last ``minutes`` finished minutes, or None with ``path`` omitted: the session or the tape on file is younger."""
    if end - minutes < opened:
        ls.omit(path, f"the session is {plural(end - opened, 'minute')} old, under the {minutes}-minute window")
        return None
    w = today.window(end - minutes, end)
    if w is None:
        ls.omit(path, f"the lob-flow tape has a minute with no trade in the last {minutes} minutes: the collector missed it")
    return w


def _thin_base(base: list) -> str:
    return f"needs {MIN_RANK_SESSIONS} prior sessions with a lob-flow tape at this minute, have {len(base)}"


def _lean(bullish: float, bearish: float, of_what: str) -> str:
    """Which side the premium whose side could be told leaned to, against the big_lean_share lean line."""
    line = f"the {pct(BIG_LEAN_SHARE)} lean line"
    told = bullish + bearish
    if told <= 0:
        return f"neither side passed {line}: the side of none of {of_what} could be told"
    bull, bear = bullish / told, bearish / told
    of_told = f"{of_what} whose side could be told"
    if bull > BIG_LEAN_SHARE:
        return f"{pct(bull)} of {of_told} was calls bought or puts sold, past {line}"
    if bear > BIG_LEAN_SHARE:
        return f"{pct(bear)} of {of_told} was puts bought or calls sold, past {line}"
    return f"neither side passed {line}: {pct(bull)} of {of_told} was calls bought or puts sold and {pct(bear)} puts bought or calls sold"


def _fifth(rank: SameClockRank) -> str:
    return "in the top fifth" if rank.share >= TOP_FIFTH else "in the bottom fifth" if rank.share <= BOTTOM_FIFTH else "between the bottom and top fifths"


def _big_prints_10(today: TapeMinutes, opened: int, end: int, ls: LabelSet) -> None:
    w = _window(today, opened, end, WINDOW_10_MIN, "options.big_prints_10", ls)
    if w is None:
        return
    trades = f"over the last {WINDOW_10_MIN} minutes {plural(w.big_prints, 'single 0DTE trade')} of {BIG_PRINT_LOTS} lots or more printed"
    if w.big_prints < BIG_MIN_PRINTS:
        ls.put("options.big_prints_10", f"{trades}, fewer than the {BIG_MIN_PRINTS}-trade minimum")
        return
    ls.put("options.big_prints_10", f"{trades}, at least the {BIG_MIN_PRINTS}-trade minimum; {_lean(w.big_bullish, w.big_bearish, 'their premium')}")


def _signed_share(w: TapeFlow) -> float | None:
    """The premium whose side could be told, signed: -1 all puts bought or calls sold, +1 all calls bought or puts sold."""
    told = w.bullish + w.bearish
    return (w.bullish - w.bearish) / told if told > 0 else None


def _flow_lean_30(today: TapeMinutes, prior: list[TapeMinutes], opened: int, end: int, ls: LabelSet) -> None:
    """The last 30 minutes' signed premium with the usual level for this half hour (the prior sessions' median)
    removed, ranked against those sessions."""
    w = _window(today, opened, end, WINDOW_30_MIN, "options.flow_lean_30", ls)
    if w is None:
        return
    lean = _signed_share(w)
    if lean is None:
        ls.omit("options.flow_lean_30", f"the side of no 0DTE trade in the last {WINDOW_30_MIN} minutes could be told")
        return
    base = [v for p in prior if (pw := p.window(end - WINDOW_30_MIN, end)) is not None and (v := _signed_share(pw)) is not None]
    rank = rank_against(lean, base)
    if rank is None:
        ls.omit("options.flow_lean_30", _thin_base(base))
        return
    beyond = round(lean - statistics.median(base), 2) or 0.0     # the side is the sign as the sentence prints it
    side = "toward buying calls and selling puts" if beyond > 0 else "toward buying puts and selling calls"
    how = (f"leaned {side} by {abs(beyond):.2f} of signed premium {'above' if beyond > 0 else 'below'} its usual level" if beyond else
           "leaned no further either way than its usual level")
    line = (f"at or past the {pct(FLOW_LEAN_RANK)} rank line" if rank.share >= FLOW_LEAN_RANK else
            f"at or under the {pct(1 - FLOW_LEAN_RANK)} rank line" if rank.share <= 1 - FLOW_LEAN_RANK else
            f"between the {pct(1 - FLOW_LEAN_RANK)} and {pct(FLOW_LEAN_RANK)} rank lines, no lean")
    ls.put("options.flow_lean_30", f"over the last {WINDOW_30_MIN} minutes the 0DTE tape {how} for this half hour, {rank.words()}, {line}")


def _premium_burst_5m(today: TapeMinutes, prior: list[TapeMinutes], opened: int, end: int, ls: LabelSet) -> None:
    """The last 5 minutes' premium against the same minutes on the prior sessions, and which way it leaned; the
    opening burst question wakes only for a top-fifth burst."""
    w = _window(today, opened, end, BURST_WINDOW_MIN, "options.premium_burst_5m", ls)
    if w is None:
        ls.sleep("opening_premium_burst", ls.omitted["options.premium_burst_5m"])
        return
    base = [pw.premium for p in prior if (pw := p.window(end - BURST_WINDOW_MIN, end)) is not None and pw.premium > 0]
    rank = rank_against(w.premium, base)
    if rank is None:
        ls.omit("options.premium_burst_5m", _thin_base(base))
        ls.sleep("opening_premium_burst", _thin_base(base))
        return
    lean = _lean(w.bullish, w.bearish, "its premium")
    head = f"in the last {BURST_WINDOW_MIN} minutes 0DTE premium traded was"
    if rank.share >= TOP_FIFTH:
        ls.put("options.premium_burst_5m", f"{head} in the top fifth for these minutes, {rank.words()}; {lean}")
        ls.wake("opening_premium_burst")
        return
    ls.put("options.premium_burst_5m", f"{head} under the top fifth for these minutes, {rank.words()}, so it was no burst; {lean}")
    ls.sleep("opening_premium_burst", f"the last {BURST_WINDOW_MIN} minutes' 0DTE premium is under the top fifth for these minutes, {rank.words()}")


def _premium_pace_30(today: TapeMinutes, prior: list[TapeMinutes], opened: int, end: int, ls: LabelSet) -> None:
    w = _window(today, opened, end, WINDOW_30_MIN, "options.premium_pace_30", ls)
    if w is None:
        return
    base = [pw.premium for p in prior if (pw := p.window(end - WINDOW_30_MIN, end)) is not None and pw.premium > 0]
    rank = rank_against(w.premium, base)
    if rank is None:
        ls.omit("options.premium_pace_30", _thin_base(base))
        return
    ls.put("options.premium_pace_30", f"0DTE premium traded in the last {WINDOW_30_MIN} minutes is {_fifth(rank)} for this half hour, {rank.words()}")


def tape_path(state_dir: Path, day: str) -> Path | None:
    folder = Path(state_dir) / TAPE_RAW_SUBDIR / day
    return next((p for p in (folder / "tape.jsonl", folder / "tape.jsonl.gz") if p.exists()), None)


def tape_minutes(state_dir: Path, day: str, start: int, end: int) -> TapeMinutes | None:
    """A day's 0DTE tape summed by the minute a trade printed in, for the minutes of day in [start, end); None
    when the day has no tape file."""
    path = tape_path(state_dir, day)
    if path is None:
        return None
    return _read_tape(str(path), path.stat().st_mtime_ns, day, start, end)


@lru_cache(maxsize=64)
def _read_tape(path: str, mtime_ns: int, day: str, start: int, end: int) -> TapeMinutes:
    """``mtime_ns`` keys the cache, so today's file, still being written, is read afresh. A day's tape is about a
    million lines: every line's time is read from its start, and only the lines inside the window are parsed."""
    midnight_ms = int(datetime.combine(date.fromisoformat(day), time(0), tzinfo=ET).timestamp() * 1000)
    flows: dict[int, TapeFlow] = {}
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        for line in f:
            if not line.startswith(TAPE_LINE_START):
                continue
            try:
                minute = (int(line[len(TAPE_LINE_START):line.index(b",")]) - midnight_ms) // 60_000
            except ValueError:
                continue
            if not start <= minute < end:
                continue
            try:
                t = json.loads(line)
            except ValueError:             # the line being written as the file is read
                continue
            _add_trade(flows.setdefault(minute, TapeFlow()), t)
    return TapeMinutes(flows)


def _add_trade(flow: TapeFlow, t: dict) -> None:
    price, size = t.get("price"), t.get("size")
    if not is_num(price) or not is_num(size) or price <= 0 or size <= 0:
        return
    premium = float(price) * float(size) * OPTION_MULTIPLIER
    flow.premium += premium
    if t.get("condition") in MULTI_LEG_CONDITIONS:
        return
    big = size >= BIG_PRINT_LOTS
    if big:
        flow.big_prints += 1
    bid, ask = t.get("bid"), t.get("ask")
    if t.get("right") not in ("call", "put") or not is_num(bid) or not is_num(ask) or ask < bid or price == (bid + ask) / 2:
        return
    if (price > (bid + ask) / 2) == (t["right"] == "call"):
        flow.bullish += premium
        flow.big_bullish += premium if big else 0.0
    else:
        flow.bearish += premium
        flow.big_bearish += premium if big else 0.0


# ----------------------------------------------------------------------------- the collector's quotes and defense

def _medians(readings: list[tuple[datetime, float, float]], end: datetime) -> tuple[float, float] | None:
    """The median of each of a reading's two numbers over the QUOTE_WINDOW_MIN minutes to ``end``; None with no reading."""
    win = [(a, b) for ts, a, b in readings if end - timedelta(minutes=QUOTE_WINDOW_MIN) < ts <= end]
    return (statistics.median(a for a, _ in win), statistics.median(b for _, b in win)) if win else None


def _stopped(stamps: list[datetime], now: datetime) -> bool:
    """The collector wrote nothing in the OPTIONS_TAPE_MAX_AGE_MIN minutes to ``now``."""
    seen = [ts for ts in stamps if ts <= now]
    return not seen or now - seen[-1] > timedelta(minutes=OPTIONS_TAPE_MAX_AGE_MIN)


def _cents(dollars: float) -> str:
    return plural(round(dollars * 100), "cent")


def _quote_liquidity(scene: Scene, ls: LabelSet) -> None:
    """Depth and width of the same-day quotes near price over the last 5 minutes, against the same minutes of the prior sessions."""
    if scene.state_dir is None:
        ls.omit("options.quote_liquidity", "no state folder to read the lob-flow collector's quote sweeps from")
        return
    sweeps = quote_sweeps(scene.state_dir, scene.day)
    if sweeps is None:
        ls.omit("options.quote_liquidity", f"no lob-flow quote sweeps for {scene.day} under {TAPE_RAW_SUBDIR}: the collector did not run")
        return
    if _stopped(sweeps.stamps, scene.now):
        ls.omit("options.quote_liquidity", f"no sweep of the 0DTE quotes from the lob-flow collector in the last {OPTIONS_TAPE_MAX_AGE_MIN} minutes: the collector stopped")
        return
    got = _medians(sweeps.near, scene.now)
    if got is None:
        ls.omit("options.quote_liquidity", f"the collector's 25-40 delta bucket held too few quoted contracts over the last {QUOTE_WINDOW_MIN} minutes")
        return
    depth, spread = got
    clock = scene.now.astimezone(ET).time()
    base = [m for d in scene.prior_bars if (sw := quote_sweeps(scene.state_dir, d)) is not None
            and (m := _medians(sw.near, datetime.combine(date.fromisoformat(d), clock, tzinfo=ET))) is not None]
    rank = rank_against(depth, [b[0] for b in base])
    if rank is None:
        ls.omit("options.quote_liquidity", f"needs {MIN_RANK_SESSIONS} prior sessions with lob-flow quote sweeps at this minute, have {len(base)}")
        return
    now_c, usual_c = round(spread * 100), round(statistics.median(b[1] for b in base) * 100)
    width = (f"wider than their usual {_cents(usual_c / 100)} for this time" if now_c > usual_c else
             f"tighter than their usual {_cents(usual_c / 100)} for this time" if now_c < usual_c else "their usual width for this time")
    book = "deep" if rank.share >= TOP_FIFTH else "thin" if rank.share <= BOTTOM_FIFTH else "middling"
    ls.put("options.quote_liquidity", f"over the last {QUOTE_WINDOW_MIN} minutes 25-40 delta same-day quotes have been {_cents(spread)} wide, "
                                      f"{width}, with a median {depth:.0f} contracts displayed, a {book} book for this time, "
                                      f"{_fifth(rank)}, {rank.words()}")


def _strike_defense(scene: Scene, ls: LabelSet) -> None:
    """The nearest strike whose same-day quotes keep getting hit, from the collector's refill test over its last
    15 minutes of trades: a hit is a trade that ate the size showing at the touch, refilled when that size came
    back at the same price (within a tick)."""
    if scene.state_dir is None:
        ls.omit("options.strike_defense", "no state folder to read the lob-flow collector's record from")
        return
    record = collector_record(scene.state_dir, scene.day)
    if record is None:
        ls.omit("options.strike_defense", f"no lob-flow record for {scene.day} under {OPTIONS_TAPE_SUBDIR}: the collector did not run")
        return
    if _stopped([ts for ts, _ in record.defense], scene.now):
        ls.omit("options.strike_defense", f"no lob-flow reading in the last {OPTIONS_TAPE_MAX_AGE_MIN} minutes: the collector stopped")
        return
    ruler = sigma_anchor(scene)
    if ruler is None:
        ls.omit("options.strike_defense", "no sigma ruler for today to measure the strike's distance in")
        return
    defense = [x for ts, x in record.defense if ts <= scene.now][-1]
    hits = [(float(v["strike"]), int(v["n_events"]) + int(v["n_unrecovered"]), int(v["n_events"])) for v in defense.values()
            if isinstance(v, dict) and all(is_num(v.get(k)) for k in ("strike", "n_events", "n_unrecovered"))]
    contested = [h for h in hits if h[1] >= DEFENSE_MIN_EVENTS]
    if not contested:
        ls.omit("options.strike_defense", f"no strike the collector watches was hit {DEFENSE_MIN_EVENTS} times or more in its last {OPTIONS_TAPE_WINDOW_MIN} minutes")
        return
    strike, hit, refilled = min(contested, key=lambda h: abs(h[0] - scene.spot))
    d = (strike - scene.spot) / ruler.points
    where = f"{sig(abs(d))} {'above' if d >= 0 else 'below'} price"
    if abs(d) > DEFENSE_NEAR_SIGMA:
        ls.omit("options.strike_defense", f"the nearest strike hit {DEFENSE_MIN_EVENTS} times or more is {where}, beyond the {DEFENSE_NEAR_SIGMA} sigma defense distance")
        return
    share = refilled / hit
    ls.put("options.strike_defense",
           f"the nearest strike where same-day quotes keep getting hit is {where}, inside the {DEFENSE_NEAR_SIGMA} sigma defense distance; "
           f"it was hit {hit} times in the last {OPTIONS_TAPE_WINDOW_MIN} minutes, at least the {DEFENSE_MIN_EVENTS}-hit minimum; "
           f"market makers refilled its quotes at price on {refilled} of them ({pct(share)}), "
           f"{'at least' if share >= DEFENSE_REFILL_SHARE else 'under'} the {pct(DEFENSE_REFILL_SHARE)} refill share"
           f"{'; ruler estimated' if ruler.estimated else ''}")


@dataclass(frozen=True)
class QuoteSweeps:
    """A day's minute-by-minute sweeps of the 0DTE quotes (``state/lob_flow/raw/{day}/sweeps.jsonl``): when each
    was taken, and the 25-40 delta bucket's median displayed size and spread where it held enough contracts."""
    stamps: list[datetime]
    near: list[tuple[datetime, float, float]]


def quote_sweeps(state_dir: Path, day: str) -> QuoteSweeps | None:
    folder = Path(state_dir) / TAPE_RAW_SUBDIR / day
    path = next((p for p in (folder / "sweeps.jsonl", folder / "sweeps.jsonl.gz") if p.exists()), None)
    return None if path is None else _read_sweeps(str(path), path.stat().st_mtime_ns)


@lru_cache(maxsize=32)
def _read_sweeps(path: str, mtime_ns: int) -> QuoteSweeps:
    """``mtime_ns`` keys the cache, so today's file, still being written, is read afresh."""
    opener = gzip.open if path.endswith(".gz") else open
    stamps, near = [], []
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                sweep = json.loads(line)
            except ValueError:
                continue
            if not isinstance(sweep, dict) or not isinstance(sweep.get("ts"), str):
                continue
            ts = parse_ts(sweep["ts"])
            stamps.append(ts)
            b = (sweep.get("buckets") or {}).get(QUOTE_BUCKET) or {}
            if is_num(b.get("size")) and is_num(b.get("spread")):
                near.append((ts, float(b["size"]), float(b["spread"])))
    return QuoteSweeps(sorted(stamps), sorted(near))


@dataclass(frozen=True)
class CollectorRecord:
    """A day's lob-flow record (``state/lob_flow/agg/{day}.jsonl``), oldest first: the refill test's block per reading."""
    defense: list[tuple[datetime, dict]]


def collector_record(state_dir: Path, day: str) -> CollectorRecord | None:
    path = Path(state_dir) / OPTIONS_TAPE_SUBDIR / f"{day}.jsonl"
    return _read_record(str(path), path.stat().st_mtime_ns) if path.exists() else None


@lru_cache(maxsize=32)
def _read_record(path: str, mtime_ns: int) -> CollectorRecord:
    """``mtime_ns`` keys the cache, so today's file, still being written, is read afresh."""
    defense = []
    for line in load_jsonl(Path(path)):
        if not isinstance(line.get("ts"), str):
            continue
        ts = parse_ts(line["ts"])
        if line.get("engine") == "lob_flow" and isinstance((line.get("snapshot") or {}).get("defense"), dict):
            defense.append((ts, line["snapshot"]["defense"]))
    return CollectorRecord(sorted(defense, key=lambda x: x[0]))
