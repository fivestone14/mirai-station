"""The options-flow family: what is trading in today's 0DTE book and who is taking which side (options.*), and
SPY's own tape and quote (volume.*, liquidity.*).

The 0DTE tape labels read the lob-flow collector's raw tape, ``state/lob_flow/raw/{day}/tape.jsonl``
(``tape.jsonl.gz`` once the collector archives the day): one line per 0DTE SPXW trade with the quote it
printed into, on the strikes the collector watches: 10 either side of price for each right, plus the book's
magnet and walls. That set moves with price, so the tape is the near-price 0DTE trading, never a far print.
Lines land out of time order, so each trade is placed by its own ``ts_ms``, never by where it sits in the file,
and a minute counts once it has finished. The collector only appends, so the file is read up to its first line
stamped after the read: a trade journaled late (a catch-up pull, or the first 5 minutes of a strike newly
watched) sits past that line and was not on file yet, for a replay of today and the prior sessions alike. A
trade's side is told from where it printed inside that quote: above the mid it was bought, below it sold. A
multi-leg trade (an OPRA multi-leg condition) is part of a package, so it is no single trade and has no side of
its own; its premium still counts as premium traded. Reading 20 prior sessions' tapes (about a million lines
each) takes some 15 seconds a read: only the lines inside the window asked for are parsed.

The other sources: the collector's quote sweeps (``sweeps.jsonl`` beside the tape) and its record
(``state/lob_flow/agg/{day}.jsonl``: the refill test and SPY's quote), the diary's same-day volume by strike,
and SPY's volume by minute as the siege box keeps it (``state/siege/baseline.json``), since the market-context
job saves SPY's quote live but not its minute bars. Every one is read point in time and ranked against the same
minutes of up to the last 20 prior sessions, needing 10 of them (ranks.rank_sessions), never a fixed size: what a
large trade is, which way premium leaned, how far the swing to calls went, how wide SPY's quote is, how near a
contested strike sits and how well it was refilled.

The final question set's labels a family does not write yet are listed after its built ones; each one's sentence,
how it is computed and its source are in spec/question_set.json ``labels``, and the registry omits it as not built."""
from __future__ import annotations

import gzip
import json
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from ..cuts import (BIG_MIN_PRINTS, BIG_PRINT_PCT, BOTTOM_FIFTH, BUSIEST_STRIKE_SHARE, DEFENSE_MIN_EVENTS, EVEN_SPLIT_HIGH, EVEN_SPLIT_LOW,
                    FLOW_LEAN_RANK, HALF_RANK, NIGHT_RANK_COUNT, SAME_CLOCK_MIN_SESSIONS, SPY_SPREAD_TIGHT, THIN_VOLUME_PCT, TOP_FIFTH,
                    TURNOVER_HIGH, TURNOVER_LOW, WINDOW_10_MIN, WINDOW_30_MIN)
from ..row_adapter import labeller_row
from ..state_builder import (ET, OPTIONS_TAPE_MAX_AGE_MIN, OPTIONS_TAPE_SUBDIR, OPTIONS_TAPE_WINDOW_MIN, ROWS_SUBDIR, Scene, load_jsonl,
                             parse_ts)
from .label_set import LabelSet
from .measures import close_at, is_num, minute_of_day
from .ranks import fifth, rank_sessions, same_clock_values
from .rulers import estimated_note, sigma_anchor
from .vol_sources import TAPE_LINE_START, TAPE_RAW_SUBDIR, tape_path
from .words import pct, plural, sig, signed

LABELS = ("options.turnover", "options.call_put_split", "options.aggressor_side", "options.new_activity",
          "options.big_prints_10", "options.call_put_shift_10m", "options.flow_lean_30", "options.premium_burst_5m",
          "options.premium_pace_30", "options.quote_liquidity", "options.strike_defense", "volume.spy_last30_share",
          "volume.spy_pace_30", "liquidity.spy_quote", "liquidity.spy_book_lean")
GATES = ("opening_premium_burst",)
DARK = {"liquidity.spy_book_lean": "the lob-flow collector saves no per-minute SPY bid and ask sizes or order-flow imbalance"}

TAPE_LINE_BYTES = TAPE_LINE_START.encode()   # the tape is read as bytes here, so only a line in the window is decoded
OPTION_MULTIPLIER = 100
MULTI_LEG_CONDITIONS = range(130, 145)   # OPRA's multi-leg and stock-option trade conditions (ThetaData 130-144)
# Prints and quotes are in cents, so a print nearer the mid than this is at it: 5.70 inside 5.60/5.80 is 1e-15 off in floats.
AT_MID_TOLERANCE = 1e-6
# The premium burst's window: the opening lane reads every 5 minutes, so a burst is the stretch since the last read.
BURST_WINDOW_MIN = 5
OPTIONS_TAPE_LABELS = ("options.big_prints_10", "options.flow_lean_30", "options.premium_burst_5m", "options.premium_pace_30")
# The diary writes a row about every 75 seconds: one further than this before the moment it stands for means the scanner paused.
ROW_SLACK_MIN = 5
ROW_LINE_START = '{"ts": "'
# A quote read is the median of the collector's minute-by-minute readings over this many minutes: one sweep's
# depth swings by half from one minute to the next.
QUOTE_WINDOW_MIN = 5
QUOTE_BUCKET = "d25_40"                  # the collector's 25-40 delta bucket, the same-day quotes near price that trade
SPY_VOLUMES = Path("siege") / "baseline.json"   # the siege box's SPY volume by minute of day, folded on every diary scan
# The siege box folds SPY's minutes on each diary scan, about every two minutes: a newest minute older than this means it stopped.
SPY_VOLUME_MAX_AGE_MIN = 5
# A window with under half its minutes on file is a feed hole, not a quiet stretch (the siege box's own rule).
MIN_WINDOW_COVERAGE = 0.5
VOLUME_LABELS = ("volume.spy_last30_share", "volume.spy_pace_30")


def build_options_flow_labels(scene: Scene) -> LabelSet:
    ls = LabelSet()
    gv = scene.row.get("gex_views") or {}
    _turnover_and_split(gv, ls)
    _aggressor_side(scene, ls)
    _new_activity(scene, gv, ls)
    _call_put_shift_10m(scene, ls)
    _tape_labels(scene, ls)
    _quote_liquidity(scene, ls)
    _strike_defense(scene, ls)
    _spy_quote(scene, ls)
    _spy_volume_labels(scene, ls)
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
    rank, why = rank_sessions(tilt, base, "an options tape reading at this minute")
    if rank is None:
        ls.omit("options.aggressor_side", why)
        return
    shown = round(tilt, 2) or 0.0          # the lean is the sign of the tilt as the sentence prints it
    lean = ("toward buying calls and selling puts" if shown > 0 else "toward buying puts and selling calls" if shown < 0 else "neither way")
    ls.put("options.aggressor_side",
           f"over the last {OPTIONS_TAPE_WINDOW_MIN} minutes the 0DTE options tape leaned {lean}, a tilt of {signed(tilt)} on a scale "
           f"from -1 to +1 with each trade weighted by its delta, resting on the {pct(determinate)} of that flow whose side could be told; "
           f"in the {rank.band} for this minute, higher than {rank.higher_than} of {rank.of} prior sessions")


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


def _call_shares(book: Book, calls: float, puts: float) -> tuple[float, float]:
    """The call share of the new contracts and of the day's; the first less the second is the swing to calls."""
    day_calls, day_puts = sum(c for c, _ in book.values()), sum(p for _, p in book.values())
    return calls / (calls + puts), day_calls / (day_calls + day_puts)


# The swing's verdict by the third it ranks in at this minute: a signed measure, its top third the calls' side.
SHIFT_WORDS = {"top third": "a shift to calls for this time", "middle third": "no shift for this time",
               "bottom third": "a shift to puts for this time"}


def _call_put_shift_10m(scene: Scene, ls: LabelSet) -> None:
    """The call share of the last 10 minutes' new same-day volume against the day's, the swing between them ranked
    against the same swing at this minute on the prior sessions, and whether that new volume is too thin to judge
    against the same 10 minutes of the prior sessions."""
    books = [(parse_ts(r["ts"]), _book(r)) for r in scene.rows_today]
    got = _new_contracts(books, scene.now)
    if got is None:
        first = next((ts for ts, b in books if b), None)
        ls.omit("options.call_put_shift_10m",
                f"the diary's first row with same-day volume by strike today came under {WINDOW_10_MIN} minutes ago: "
                f"no earlier volume to set the last {WINDOW_10_MIN} minutes against"
                if first is not None and first > scene.now - timedelta(minutes=WINDOW_10_MIN) else
                f"no diary row with same-day volume by strike from {WINDOW_10_MIN} minutes ago and now")
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
    base_new, base_swing = [], []
    for d in scene.prior_bars:
        then = _new_contracts(_prior_books(scene.state_dir, d, clock), datetime.combine(date.fromisoformat(d), clock, tzinfo=ET))
        if then is not None:
            base_new.append(then[1] + then[2])
            if then[1] + then[2] > 0:
                new_share, day_share = _call_shares(*then)
                base_swing.append(new_share - day_share)
    what = "diary rows at this minute and 10 minutes before"
    new_share, day_share = _call_shares(book, calls, puts)
    thin_rank, why = rank_sessions(new, base_new, what)
    shift_rank, shift_why = rank_sessions(new_share - day_share, base_swing, what)
    if thin_rank is None or shift_rank is None:
        ls.omit("options.call_put_shift_10m", why or shift_why)
        return
    points = round(new_share * 100) - round(day_share * 100)      # the swing between the two shares as the sentence prints them
    article = "an" if abs(points) in (8, 11, 18) or 80 <= abs(points) < 90 else "a"      # "an 11-point swing"
    said = f"{article} {abs(points)}-point swing to {'calls' if points > 0 else 'puts'}" if points else "no swing either way"
    thin = ("new volume was above the too-thin line for this time, so it is not too thin to judge" if thin_rank.share >= THIN_VOLUME_PCT else
            f"new volume was under the too-thin line for this time, {thin_rank.words()}, too thin to judge")
    ls.put("options.call_put_shift_10m", f"in the last {WINDOW_10_MIN} minutes {pct(new_share)} of new same-day option volume was calls, "
                                         f"against {pct(day_share)} since the open: {said}, {shift_rank.words()}, "
                                         f"{shift_rank.band}: {SHIFT_WORDS[shift_rank.band]}; {thin}")


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
class SizeFlow:
    """The single trades of one size, or of a size and up: how many printed, and the premium of those whose side
    could be told, split by which way it leans."""
    prints: int = 0
    bullish: float = 0.0
    bearish: float = 0.0

    def add(self, other: SizeFlow) -> None:
        self.prints += other.prints
        self.bullish += other.bullish
        self.bearish += other.bearish


@dataclass
class TapeFlow:
    """Premium traded over a stretch of the 0DTE tape, in dollars: all of it, and the single-leg part whose
    side could be told, split by which way it leans (calls bought or puts sold is bullish); and the single trades
    by their size in lots, so a large trade is told by the sizes of the prior sessions, not by a fixed size."""
    premium: float = 0.0
    bullish: float = 0.0
    bearish: float = 0.0
    by_lots: dict[float, SizeFlow] = field(default_factory=dict)

    def add(self, other: TapeFlow) -> None:
        self.premium += other.premium
        self.bullish += other.bullish
        self.bearish += other.bearish
        for lots, flow in other.by_lots.items():
            self.by_lots.setdefault(lots, SizeFlow()).add(flow)

    def from_lots(self, lots: float) -> SizeFlow:
        """The single trades of ``lots`` or more, summed."""
        out = SizeFlow()
        for size, flow in self.by_lots.items():
            if size >= lots:
                out.add(flow)
        return out


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
    now_et = scene.now.astimezone(ET)
    clock, end = now_et.time(), minute_of_day(now_et)
    start = end - WINDOW_30_MIN
    today, why = _today_tape(scene, start, end, clock)
    if today is None:
        for path in OPTIONS_TAPE_LABELS:
            ls.omit(path, why)
        ls.unmeasured("opening_premium_burst")
        return
    prior = [m for d in scene.prior_bars if (m := tape_minutes(scene.state_dir, d, start, end, clock)) is not None]
    opened = minute_of_day(scene.session_open.astimezone(ET))
    _big_prints_10(today, prior, opened, end, ls)
    _flow_lean_30(today, prior, opened, end, ls)
    _premium_burst_5m(today, prior, opened, end, ls)
    _premium_pace_30(today, prior, opened, end, ls)


def _today_tape(scene: Scene, start: int, end: int, clock: time) -> tuple[TapeMinutes | None, str]:
    """Today's tape over [start, end), or None and why: no state folder, no file, or a collector that stopped."""
    if scene.state_dir is None:
        return None, "no state folder to read the lob-flow collector's 0DTE tape from"
    today = tape_minutes(scene.state_dir, scene.day, start, end, clock)
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


# What a tape rank's prior sessions need, as its omission names it.
TAPE_AT_MINUTE = "a whole lob-flow tape at this minute"
# A lean's verdict by the third its bullish share ranks in at this minute: a signed measure, its top third the bullish side.
LEAN_WORDS = {"top third": "a bullish lean", "middle third": "no lean", "bottom third": "a bearish lean"}


def _bullish_share(bullish: float, bearish: float) -> float | None:
    """The share of the premium whose side could be told that was calls bought or puts sold; None when none could be told."""
    told = bullish + bearish
    return bullish / told if told > 0 else None


def _lean(share: float | None, base: list[float], of_what: str, what: str) -> tuple[str | None, str | None]:
    """Which way ``of_what`` leaned, by the third its bullish share ranks in against ``base``, the same share on the
    prior sessions: the words, or None and why the rank could not be made (rank_sessions, with ``what``)."""
    if share is None:
        return f"the side of none of {of_what} could be told: no lean", None
    rank, why = rank_sessions(share, base, what)
    if rank is None:
        return None, why
    return (f"{pct(share)} of {of_what} whose side could be told was calls bought or puts sold, {rank.words()}, "
            f"{rank.band}: {LEAN_WORDS[rank.band]}"), None


def _big_print_lots(windows: list[TapeFlow]) -> float | None:
    """The size in lots that BIG_PRINT_PCT of the single trades in ``windows``, pooled, stayed at or under: the
    smallest large trade. None with no single trade."""
    counts: dict[float, int] = {}
    for w in windows:
        for lots, flow in w.by_lots.items():
            counts[lots] = counts.get(lots, 0) + flow.prints
    need, seen = BIG_PRINT_PCT * sum(counts.values()), 0
    for lots in sorted(counts):
        seen += counts[lots]
        if seen >= need and seen > 0:
            return lots
    return None


def _big_prints_10(today: TapeMinutes, prior: list[TapeMinutes], opened: int, end: int, ls: LabelSet) -> None:
    """The single trades of the last 10 minutes at or above the large-trade size of these minutes on the prior
    sessions, and which way their premium leaned against the same trades' lean on those sessions."""
    w = _window(today, opened, end, WINDOW_10_MIN, "options.big_prints_10", ls)
    if w is None:
        return
    windows = [pw for p in prior if (pw := p.window(end - WINDOW_10_MIN, end)) is not None][:NIGHT_RANK_COUNT]
    lots = _big_print_lots(windows)
    if len(windows) < SAME_CLOCK_MIN_SESSIONS or lots is None:
        ls.omit("options.big_prints_10", f"its large-trade size needs {SAME_CLOCK_MIN_SESSIONS} prior sessions with {TAPE_AT_MINUTE}, have {len(windows)}")
        return
    big = w.from_lots(lots)
    trades = (f"over the last {WINDOW_10_MIN} minutes {plural(big.prints, 'single near-price 0DTE trade')} of {lots:g} lots or more printed, "
              f"the largest {1 - BIG_PRINT_PCT:.1%} of single trades in these minutes on the last {len(windows)} sessions")
    if big.prints < BIG_MIN_PRINTS:
        ls.put("options.big_prints_10", f"{trades}, fewer than the {BIG_MIN_PRINTS}-trade minimum")
        return
    base = [v for pw in windows if (b := pw.from_lots(lots)).prints >= BIG_MIN_PRINTS and (v := _bullish_share(b.bullish, b.bearish)) is not None]
    lean, why = _lean(_bullish_share(big.bullish, big.bearish), base, "their premium", f"{BIG_MIN_PRINTS} or more large trades at this minute")
    if lean is None:
        ls.omit("options.big_prints_10", why)
        return
    ls.put("options.big_prints_10", f"{trades}, at least the {BIG_MIN_PRINTS}-trade minimum; {lean}")


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
        ls.omit("options.flow_lean_30", f"the side of no near-price 0DTE trade in the last {WINDOW_30_MIN} minutes could be told")
        return
    base = [v for p in prior if (pw := p.window(end - WINDOW_30_MIN, end)) is not None and (v := _signed_share(pw)) is not None]
    rank, why = rank_sessions(lean, base, TAPE_AT_MINUTE)
    if rank is None:
        ls.omit("options.flow_lean_30", why)
        return
    beyond = round(lean - statistics.median(base[:rank.of]), 2) or 0.0     # the side is the sign as the sentence prints it
    side = "toward buying calls and selling puts" if beyond > 0 else "toward buying puts and selling calls"
    how = (f"leaned {side} by {abs(beyond):.2f} of signed premium {'above' if beyond > 0 else 'below'} its usual level" if beyond else
           "leaned no further either way than its usual level")
    line = (f"at or past the {pct(FLOW_LEAN_RANK)} rank line" if rank.share >= FLOW_LEAN_RANK else
            f"at or under the {pct(1 - FLOW_LEAN_RANK)} rank line" if rank.share <= 1 - FLOW_LEAN_RANK else
            f"between the {pct(1 - FLOW_LEAN_RANK)} and {pct(FLOW_LEAN_RANK)} rank lines, no lean")
    ls.put("options.flow_lean_30", f"over the last {WINDOW_30_MIN} minutes the near-price 0DTE tape {how} for this half hour, {rank.words()}, {line}")


def _premium_burst_5m(today: TapeMinutes, prior: list[TapeMinutes], opened: int, end: int, ls: LabelSet) -> None:
    """The last 5 minutes' premium against the same minutes on the prior sessions, and which way it leaned against
    the same minutes' lean on those sessions, burst or not; the opening burst question wakes only for a top-fifth burst."""
    w = _window(today, opened, end, BURST_WINDOW_MIN, "options.premium_burst_5m", ls)
    if w is None:
        ls.unmeasured("opening_premium_burst")
        return
    windows = [pw for p in prior if (pw := p.window(end - BURST_WINDOW_MIN, end)) is not None]
    rank, why = rank_sessions(w.premium, [pw.premium for pw in windows if pw.premium > 0], TAPE_AT_MINUTE)
    lean, lean_why = _lean(_bullish_share(w.bullish, w.bearish), [v for pw in windows if (v := _bullish_share(pw.bullish, pw.bearish)) is not None],
                           "its premium", TAPE_AT_MINUTE)
    if rank is None or lean is None:
        ls.omit("options.premium_burst_5m", why or lean_why)
        ls.unmeasured("opening_premium_burst")
        return
    head = f"in the last {BURST_WINDOW_MIN} minutes near-price 0DTE premium traded was"
    if rank.share >= TOP_FIFTH:
        ls.put("options.premium_burst_5m", f"{head} in the top fifth for these minutes, {rank.words()}; {lean}")
        ls.wake("opening_premium_burst")
        return
    ls.put("options.premium_burst_5m", f"{head} under the top fifth for these minutes, {rank.words()}, so it was no burst; {lean}")
    ls.sleep("opening_premium_burst", f"the last {BURST_WINDOW_MIN} minutes' near-price 0DTE premium is under the top fifth for these minutes, {rank.words()}")


def _premium_pace_30(today: TapeMinutes, prior: list[TapeMinutes], opened: int, end: int, ls: LabelSet) -> None:
    w = _window(today, opened, end, WINDOW_30_MIN, "options.premium_pace_30", ls)
    if w is None:
        return
    base = [pw.premium for p in prior if (pw := p.window(end - WINDOW_30_MIN, end)) is not None and pw.premium > 0]
    rank, why = rank_sessions(w.premium, base, TAPE_AT_MINUTE)
    if rank is None:
        ls.omit("options.premium_pace_30", why)
        return
    ls.put("options.premium_pace_30", f"near-price 0DTE premium traded in the last {WINDOW_30_MIN} minutes is {fifth(rank)} for this half hour, {rank.words()}")


def tape_minutes(state_dir: Path, day: str, start: int, end: int, clock: time) -> TapeMinutes | None:
    """A day's 0DTE tape as it was on file at ``clock`` market time, summed by the minute a trade printed in, for
    the minutes of day in [start, end); None when the day has no tape file."""
    path = tape_path(state_dir, day)
    if path is None:
        return None
    return _read_tape(str(path), path.stat().st_mtime_ns, day, start, end, clock)


@lru_cache(maxsize=64)
def _read_tape(path: str, mtime_ns: int, day: str, start: int, end: int, clock: time) -> TapeMinutes:
    """``mtime_ns`` keys the cache, so today's file, still being written, is read afresh. A day's tape is about a
    million lines: every line's time is read from its start, and only the lines inside the window are parsed. The
    first line stamped after ``clock`` was written after it, and so was every line past it."""
    midnight_ms = int(datetime.combine(date.fromisoformat(day), time(0), tzinfo=ET).timestamp() * 1000)
    read_ms = int(datetime.combine(date.fromisoformat(day), clock, tzinfo=ET).timestamp() * 1000)
    flows: dict[int, TapeFlow] = {}
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        for line in f:
            if not line.startswith(TAPE_LINE_BYTES):
                continue
            try:
                ts_ms = int(line[len(TAPE_LINE_BYTES):line.index(b",")])
            except ValueError:
                continue
            if ts_ms > read_ms:
                break
            minute = (ts_ms - midnight_ms) // 60_000
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
    single = flow.by_lots.setdefault(float(size), SizeFlow())
    single.prints += 1
    bid, ask = t.get("bid"), t.get("ask")
    if t.get("right") not in ("call", "put") or not is_num(bid) or not is_num(ask) or ask < bid:
        return
    past_mid = price - (bid + ask) / 2
    if abs(past_mid) < AT_MID_TOLERANCE:
        return
    if (past_mid > 0) == (t["right"] == "call"):
        flow.bullish += premium
        single.bullish += premium
    else:
        flow.bearish += premium
        single.bearish += premium


# ----------------------------------------------------------------------------- the collector's quotes and defense

def _medians(readings: list[tuple[datetime, float, float]], end: datetime) -> tuple[float, float] | None:
    """The median of each of a reading's two numbers over the QUOTE_WINDOW_MIN minutes to ``end``; None with no reading."""
    win = [(a, b) for ts, a, b in readings if end - timedelta(minutes=QUOTE_WINDOW_MIN) < ts <= end]
    return (statistics.median(a for a, _ in win), statistics.median(b for _, b in win)) if win else None


def _newest(readings: list[tuple[datetime, float, float]], end: datetime) -> tuple[float, float] | None:
    """The newest reading's two numbers in the QUOTE_WINDOW_MIN minutes to ``end``; None with no reading."""
    win = [(a, b) for ts, a, b in readings if end - timedelta(minutes=QUOTE_WINDOW_MIN) < ts <= end]
    return win[-1] if win else None


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
    rank, why = rank_sessions(depth, [b[0] for b in base], "lob-flow quote sweeps at this minute")
    if rank is None:
        ls.omit("options.quote_liquidity", why)
        return
    now_c, usual_c = round(spread * 100), round(statistics.median(b[1] for b in base[:rank.of]) * 100)
    width = (f"wider than their usual {_cents(usual_c / 100)} for this time" if now_c > usual_c else
             f"tighter than their usual {_cents(usual_c / 100)} for this time" if now_c < usual_c else "their usual width for this time")
    book = "deep" if rank.share >= TOP_FIFTH else "thin" if rank.share <= BOTTOM_FIFTH else "middling"
    ls.put("options.quote_liquidity", f"over the last {QUOTE_WINDOW_MIN} minutes 25-40 delta same-day quotes have been {_cents(spread)} wide, "
                                      f"{width}, with a median {depth:.0f} contracts displayed, a {book} book for this time, "
                                      f"{fifth(rank)}, {rank.words()}")


def _contested(record: CollectorRecord, t: datetime, spot: float) -> tuple[float, int, int] | None:
    """The refill test's newest reading by ``t``: of the strikes hit DEFENSE_MIN_EVENTS times or more, the nearest to
    ``spot`` as ``(strike, hits, refilled)``; None with no such strike, or with no reading in the
    OPTIONS_TAPE_MAX_AGE_MIN minutes to ``t``."""
    seen = [(ts, x) for ts, x in record.defense if ts <= t]
    if not seen or t - seen[-1][0] > timedelta(minutes=OPTIONS_TAPE_MAX_AGE_MIN):
        return None
    hits = [(float(v["strike"]), int(v["n_events"]) + int(v["n_unrecovered"]), int(v["n_events"])) for v in seen[-1][1].values()
            if isinstance(v, dict) and all(is_num(v.get(k)) for k in ("strike", "n_events", "n_unrecovered"))]
    contested = [h for h in hits if h[1] >= DEFENSE_MIN_EVENTS]
    return min(contested, key=lambda h: abs(h[0] - spot)) if contested else None


def _prior_contested(state_dir: Path, bars: list[dict], then: datetime) -> tuple[float, int, int] | None:
    """A prior session's nearest contested strike at ``then`` as ``(points from price, hits, refilled)``, or None."""
    record, spot = collector_record(state_dir, then.date().isoformat()), close_at(bars, then)
    got = _contested(record, then, spot) if record is not None and spot is not None else None
    return (got[0] - spot, got[1], got[2]) if got is not None else None


# Whether market makers held a contested strike: its refill share at or above the middle of the same minute's on the
# recent sessions (HALF_RANK), since the question's two answers a side split the sessions in halves.
DEFENSE_WORDS = {True: "at or above the middle: defended", False: "below the middle: abandoned"}


def _strike_defense(scene: Scene, ls: LabelSet) -> None:
    """Of the book's magnet and walls, the only strikes the collector runs its refill test at, the nearest whose
    same-day quotes keep getting hit, over the collector's last 15 minutes of trades: a hit is a trade that ate the
    size showing at the touch, refilled when that size came back at the same price (within a tick) at any point
    in those 15 minutes, so the refill share says nothing of how fast. Its distance from price and its refill share
    are each ranked against the nearest contested strike's at this minute on the prior sessions."""
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
    got = _contested(record, scene.now, scene.spot)
    if got is None:
        ls.omit("options.strike_defense", f"none of the book's magnet and walls, the strikes the collector tests, "
                                          f"was hit {DEFENSE_MIN_EVENTS} times or more in its last {OPTIONS_TAPE_WINDOW_MIN} minutes", ended=True)
        return
    strike, hit, refilled = got
    d = (strike - scene.spot) / ruler.points

    def distance(bars: list[dict], then: datetime, sigma: float | None) -> float | None:
        prior = _prior_contested(scene.state_dir, bars, then)
        return abs(prior[0]) / sigma if sigma and prior else None

    far, why = rank_sessions(abs(d), same_clock_values(scene, distance), "a contested strike at this minute")
    if far is None:
        ls.omit("options.strike_defense", why)
        return
    clock = scene.now.astimezone(ET).time()
    held_base = [p[2] / p[1] for day, bars in scene.prior_bars.items()
                 if (p := _prior_contested(scene.state_dir, bars, datetime.combine(date.fromisoformat(day), clock, tzinfo=ET)))]
    held, why = rank_sessions(refilled / hit, held_base, "a contested strike at this minute")
    if held is None:
        ls.omit("options.strike_defense", why)
        return
    where = (f"{sig(abs(d))} {'above' if d >= 0 else 'below'} price, farther than the nearest one on {far.higher_than} "
             f"of the last {far.of} sessions at this minute, {far.band}")
    if far.band == "top third":
        ls.omit("options.strike_defense", f"the nearest of the book's magnet and walls hit {DEFENSE_MIN_EVENTS} times or more is {where}: out of reach", ended=True)
        return
    ls.put("options.strike_defense",
           f"of the book's magnet and walls, the strikes the collector tests, the nearest where same-day quotes keep getting hit is {where}: within reach; "
           f"it was hit {hit} times in the last {OPTIONS_TAPE_WINDOW_MIN} minutes, at least the {DEFENSE_MIN_EVENTS}-hit minimum; "
           f"market makers refilled its quotes at price at some point in those {OPTIONS_TAPE_WINDOW_MIN} minutes on {refilled} of them ({pct(refilled / hit)}), "
           f"{held.words()}, {DEFENSE_WORDS[held.share >= HALF_RANK]}"
           f"{estimated_note(ruler)}")


def _spy_quote(scene: Scene, ls: LabelSet) -> None:
    """SPY's quoted spread and the size showing at its best bid and offer over the last 5 minutes, from the
    collector's newest SPY reading, itself the median of its last 5 minutes of SPY quotes; each is ranked against
    the same reading on the prior sessions, the spread in whole cents (SPY quotes in cents, so sessions at the
    same width tie and none is beaten) and the size never stated in shares."""
    if scene.state_dir is None:
        ls.omit("liquidity.spy_quote", "no state folder to read the lob-flow collector's SPY quote from")
        return
    record = collector_record(scene.state_dir, scene.day)
    if record is None:
        ls.omit("liquidity.spy_quote", f"no lob-flow record for {scene.day} under {OPTIONS_TAPE_SUBDIR}: the collector did not run")
        return
    if _stopped([ts for ts, _, _ in record.spy], scene.now):
        ls.omit("liquidity.spy_quote", f"no SPY quote from the lob-flow collector in the last {OPTIONS_TAPE_MAX_AGE_MIN} minutes: its SPY stream stopped")
        return
    spread, size = _newest(record.spy, scene.now)
    now_et = scene.now.astimezone(ET)
    base = [m for d in scene.prior_bars if (r := collector_record(scene.state_dir, d)) is not None
            and (m := _newest(r.spy, datetime.combine(date.fromisoformat(d), now_et.time(), tzinfo=ET))) is not None]
    what = "the collector's SPY quote at this minute"
    wide, why = rank_sessions(round(spread * 100), [round(b[0] * 100) for b in base], what)
    rank, _ = rank_sessions(size, [b[1] for b in base], what)
    if wide is None:
        ls.omit("liquidity.spy_quote", why)
        return
    width = ("at the tight tick" if spread < SPY_SPREAD_TIGHT else
             "top third: wide for this time" if wide.band == "top third" else f"{wide.band}: its usual width for this time")
    # the spy_liquidity question's thin book is strictly under the bottom fifth
    fifth = "in the bottom fifth" if rank.share < BOTTOM_FIFTH else "in the top fifth" if rank.share >= TOP_FIFTH else "between the bottom and top fifths"
    ls.put("liquidity.spy_quote", f"over the last {QUOTE_WINDOW_MIN} minutes SPY's quoted spread has been {_cents(spread)}, "
                                  f"wider than on {wide.higher_than} of the last {wide.of} sessions at this minute, {width}; "
                                  f"the size showing at SPY's best bid and offer combined is {fifth} for {now_et:%H:%M}, {rank.words()}")


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
    """A day's lob-flow record (``state/lob_flow/agg/{day}.jsonl``), oldest first: the refill test's block per
    reading, and SPY's quoted spread and displayed size per reading (the control's baseline rows)."""
    defense: list[tuple[datetime, dict]]
    spy: list[tuple[datetime, float, float]]


def collector_record(state_dir: Path, day: str) -> CollectorRecord | None:
    path = Path(state_dir) / OPTIONS_TAPE_SUBDIR / f"{day}.jsonl"
    return _read_record(str(path), path.stat().st_mtime_ns) if path.exists() else None


@lru_cache(maxsize=32)
def _read_record(path: str, mtime_ns: int) -> CollectorRecord:
    """``mtime_ns`` keys the cache, so today's file, still being written, is read afresh."""
    defense, spy = [], []
    for line in load_jsonl(Path(path)):
        if not isinstance(line.get("ts"), str):
            continue
        ts = parse_ts(line["ts"])
        if line.get("engine") == "lob_flow" and isinstance((line.get("snapshot") or {}).get("defense"), dict):
            defense.append((ts, line["snapshot"]["defense"]))
        elif line.get("engine") == "spy_depth":
            got = {}
            for r in line.get("baseline_rows") or []:
                kind, _, rest = str((r or {}).get("key", "")).partition("|")      # "size|spy|mid|1255|mid"
                if rest.startswith("spy|"):
                    got[kind] = r.get("value")
            if is_num(got.get("spread")) and is_num(got.get("size")):
                spy.append((ts, float(got["spread"]), float(got["size"])))
    return CollectorRecord(sorted(defense, key=lambda x: x[0]), sorted(spy))


# ----------------------------------------------------------------------------- SPY's volume

def _spy_volume_labels(scene: Scene, ls: LabelSet) -> None:
    """SPY's volume over the last 30 finished minutes, as a share of the day's and against its usual for this half
    hour, both against the same minutes of the prior sessions."""
    days, why = _spy_volume_days(scene)
    if days is None:
        for path in VOLUME_LABELS:
            ls.omit(path, why)
        return
    end = minute_of_day(scene.now.astimezone(ET))
    opened = minute_of_day(scene.session_open.astimezone(ET))
    if end - WINDOW_30_MIN < opened:
        for path in VOLUME_LABELS:
            ls.omit(path, f"the session is {plural(end - opened, 'minute')} old, under the {WINDOW_30_MIN}-minute window")
        return
    today = _last30_and_day(days[scene.day], opened, end)
    if today is None:
        for path in VOLUME_LABELS:
            ls.omit(path, f"the siege box has SPY volume for under half of the minutes since the open or of the last {WINDOW_30_MIN}")
        return
    prior = [v for d in scene.prior_bars if d in days and (v := _last30_and_day(days[d], opened, end)) is not None]
    _spy_last30_share(today, prior, ls)
    _spy_pace_30(today[0], [p[0] for p in prior], ls)


def _spy_volume_days(scene: Scene) -> tuple[dict[str, dict[int, float]] | None, str]:
    """SPY's volume by minute of day for today and the prior sessions on file, or None and why: no state folder, no
    file, no minutes for today, or a feed that stopped."""
    if scene.state_dir is None:
        return None, "no state folder to read the siege box's SPY minute volumes from"
    path = Path(scene.state_dir) / SPY_VOLUMES
    if not path.exists():
        return None, f"no SPY minute volumes at {SPY_VOLUMES}: the siege box has not run"
    days = _read_spy_volumes(str(path), path.stat().st_mtime_ns)
    end = minute_of_day(scene.now.astimezone(ET))
    done = [m for m in days.get(scene.day) or {} if m < end]
    if not done:
        return None, f"no SPY minute volumes for {scene.day} in {SPY_VOLUMES}"
    if max(done) < end - SPY_VOLUME_MAX_AGE_MIN:
        return None, f"no SPY minute volume from the siege box in the last {SPY_VOLUME_MAX_AGE_MIN} minutes: its feed stopped"
    return days, ""


def _window_volume(minutes: dict[int, float], start: int, end: int) -> float | None:
    """SPY's volume over the finished minutes in [start, end); None when under MIN_WINDOW_COVERAGE of them are on file."""
    got = [minutes[m] for m in range(start, end) if m in minutes]
    return sum(got) if got and len(got) >= MIN_WINDOW_COVERAGE * (end - start) else None


def _last30_and_day(minutes: dict[int, float], opened: int, end: int) -> tuple[float, float] | None:
    """The last 30 finished minutes' volume and the day's since the open, or None with either not on file or no volume."""
    last30, day = _window_volume(minutes, end - WINDOW_30_MIN, end), _window_volume(minutes, opened, end)
    return (last30, day) if last30 is not None and day else None


def _pct_tenths(x: float) -> str:
    return f"{x * 100:.1f}%"


SPY_VOLUME_AT_MINUTE = "SPY minute volumes at this minute"
# The last half hour's share of the day's volume by the third it ranks in at this minute.
SHARE_WORDS = {"top third": "heavy for this time", "middle third": "normal for this time", "bottom third": "light for this time"}


def _spy_last30_share(today: tuple[float, float], prior: list[tuple[float, float]], ls: LabelSet) -> None:
    share = today[0] / today[1]
    rank, why = rank_sessions(share, [last30 / day for last30, day in prior], SPY_VOLUME_AT_MINUTE)
    if rank is None:
        ls.omit("volume.spy_last30_share", why)
        return
    ls.put("volume.spy_last30_share", f"SPY traded {_pct_tenths(share)} of today's volume in the last {WINDOW_30_MIN} minutes, "
                                      f"{rank.words()}, {rank.band}: {SHARE_WORDS[rank.band]}")


def _spy_pace_30(last30: float, base: list[float], ls: LabelSet) -> None:
    rank, why = rank_sessions(last30, base, SPY_VOLUME_AT_MINUTE)
    if rank is None:
        ls.omit("volume.spy_pace_30", why)
        return
    ls.put("volume.spy_pace_30", f"SPY traded {last30 / statistics.median(base[:rank.of]):.1f} times its usual volume for this half hour, "
                                 f"{fifth(rank)}, {rank.words()}")


@lru_cache(maxsize=4)
def _read_spy_volumes(path: str, mtime_ns: int) -> dict[str, dict[int, float]]:
    """The siege box's ``{"days": {day: {minute of day: volume}}}``, a minute keyed by when its bar started. ``mtime_ns``
    keys the cache: the siege box rewrites the file on every scan."""
    raw = (json.loads(Path(path).read_text(encoding="utf-8")) or {}).get("days") or {}
    return {d: {int(m): float(v) for m, v in mins.items() if is_num(v)} for d, mins in raw.items() if isinstance(mins, dict)}
