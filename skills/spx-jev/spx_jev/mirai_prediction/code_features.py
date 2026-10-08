"""The code feature builder: the code questions of the merged set, as the question pressure test of 2026-10-07 left them
(code_feature_catalog.json), answered by code from one read's stored data.

The catalog (code_feature_catalog.json beside this file) lists every question: id, title, layer (1 a market value ranked
against its own history, 2 a discrete label), group (the voting group the scorer and matcher take one vote per), method
(label / bars / market_value), source, label_keys, options (the exact answer list) and notes. An entry with
``"votes": false`` is answered and stored like any other (the judgment gates and JEV's sentences read it) but casts no
vote: the answer matrix marks its column and the scorer and the matcher leave it out (answer_matrix.voting_columns).

    answer_code_features(read_record, bars_up_to_read, market_history) -> {question_id: answer | None}

answers every question for one read from what was known at the read time only: the label sentences as archived, the SPX
minute bars finished by row_ts, and a MarketHistory of prior sessions (never the read's own day) plus the night's /ES and
the day's premarket reads, and (code_features_market.py) the market feed's symbols cut at the read, the daily
closes before the day, the index weights dated on or before it, the calendar, the day's diary rows (and the prior
sessions' at the read's clock), the quote sweeps and the lob-flow collector's records. A
question whose data is missing, or whose sentence is an unknown template, answers None (silent) and never raises. Every
parser is a pure function of the sentences; the bars and market questions compute their measure and rank it against the
same measure at the same minute on the trailing HISTORY_SESSIONS sessions. The options-book questions (GEX_BOOK_QUESTIONS)
answer only while the read's diary row comes from SPX's own book: a scaled SPY stand-in is not ranked against SPX sessions.
"""
from __future__ import annotations

import json
import re
import statistics
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from ..cuts import SKEW_FLAT_RANK, SKEW_STEEP_RANK
from ..events import WORDS as EVENT_WORDS
from ..labels.options_flow import CollectorRecord
from ..labels.measures import move_bar
from ..labels.vol import LOADING_EVENTS

CATALOG_FILE = Path(__file__).with_name("code_feature_catalog.json")
COLUMN_PREFIX = "code"               # a question's column in the answer matrix is "code:<question_id>"

HISTORY_SESSIONS = 20                # a measure is ranked against the trailing 20 sessions at the same minute
MIN_HISTORY_SESSIONS = 10            # fewer prior sessions with the measure than this: the rank is unknown, the answer None
GAP_HISTORY_SESSIONS = 60            # VOLATILITY-08 ranks the opening gap against 60 sessions: the SPX bars history reaches that far
BREAK_TOLERANCE_SIGMA = 0.02         # TREND-10: a break must clear the 60-minute high/low by this share of the read's sigma
# TREND-08: a 20-minute leg within this share of sigma has no side (None), and an opposite 10-minute move within it is
# ranked, not "reversing" (0.02 let any wiggle after a 1.4-point leg read as a reversal)
REVERSE_TOLERANCE_SIGMA = 0.05
FAST_GIVE_BACK_SHARE = 0.5           # TREND-09: a pullback counts as fast only once it has given back half the usual give-back
FAST_MIN_MINUTES = 5                 # TREND-09: and only once the extreme is this many minutes old (a 2-minute pullback is noise)
# FLOW-08's thresholds are a DRAFT (the rules are not written anywhere else yet): the settled open (the 09:31 bar's open)
# sits in the outer FLOW_08_OPEN_EDGE of the first half hour's range, the close in the opposite FLOW_08_CLOSE_EDGE, and a
# level counts as tagged, or re-crossed, within FLOW_08_TOUCH_SIGMA of the read's sigma.
FLOW_08_OPEN_EDGE = 0.10
FLOW_08_CLOSE_EDGE = 0.20
FLOW_08_TOUCH_SIGMA = 0.05
FLOW_08_FROM = "10:30"               # FLOW-08 answers from 10:30: at 10:00 its window is TREND-02's
FLOW_08_SETTLED_BAR = "09:31"        # FLOW-08's open: the 09:31 bar's open (the 09:30 bar opens on the last print)
OPENING_RANGE_WINDOW = ("09:35", "10:04")    # LEVELS-09 from 10:05: the first 30 minutes from the settled open
OPENING_HALF_HOUR = ("09:30", "09:59")       # FLOW-08, and LEVELS-06's opening box
NIGHT_REOPEN = "18:00"               # LEVELS-08's fallback: the night's range since the 18:00 reopen ...
NIGHT_UNTIL = "09:30"                # ... to the cash open, as overnight.range_vs_normal measures it
MACRO_09_UNTIL = "11:00"             # MACRO-09 answers the first cash hours only: later it is a day label
LOADING_WORDS = tuple(EVENT_WORDS[k] for k in LOADING_EVENTS)    # VOLATILITY-16: the events that load the ruler (labels/vol.py)
# OPTIONS-01/02/03/04/06/10/11 read the diary's options book: answered only while it is SPX's own (gex_source "native")
GEX_BOOK_QUESTIONS = frozenset({"OPTIONS-01", "OPTIONS-02", "OPTIONS-03", "OPTIONS-04", "OPTIONS-06", "OPTIONS-10", "OPTIONS-11"})
NATIVE_BOOK = "native"


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict, ...]:
    return tuple(json.loads(CATALOG_FILE.read_text(encoding="utf-8")))


def catalog_by_id() -> dict[str, dict]:
    return {q["id"]: q for q in load_catalog()}


def column_name(question_id: str) -> str:
    return f"{COLUMN_PREFIX}:{question_id}"


@dataclass
class MarketHistory:
    """What a read's own record does not carry. Prior sessions only, never the read's day, except where said:
    spx_bars_by_day        prior sessions' SPX minute bars (the bars/{day}.jsonl shape, ts in ET), day -> bars in time order
    es_night_bars_by_day   /ES 1-minute bars outside the regular session for the night into each day, the read's own day
                           included (its bars are cut at the read time)
    premarket_reads        the read's day's premarket-lane read records, in time order (only those before the read are used)
    context_bars_by_day    the market feed's minute bars per symbol (context/bars, with volume): day -> symbol -> bars in
                           time order, prior sessions and the read's own day (its bars are cut at the read time; its SPY
                           is its quotes' prices with the siege box's minute volume, saved or not:
                           code_feature_inputs.with_siege_spy_volume)
    today_quote_bars       the read's day's quote snapshots as one-price bars for SPY, QQQ and IWM (context/{day}.jsonl),
                           whether or not the day's bars are saved: FLOW-07 reads them, live and in backfill alike
    today_context_from_quotes  the read's day's symbols came from the quotes (its bars file not saved yet: a live read)
    daily_closes           symbol -> daily rows (day, open, high, low, close) of the sessions before the read's day
    index_weights          the index weights document (index_weights.pick takes the entry dated on or before the day)
    diary_rows             the read's day's SPX diary rows, slim: ts, spot, sigma, call_wall, put_wall, call_wall_tenor,
                           put_wall_tenor, call_wall_gamma, put_wall_gamma, magnet, mass_by_strike, atm_iv, gex_source (only
                           those at or before the read are used)
    prior_diary_row_at     (day, "HH:MM:SS") -> a prior session's slim diary row at that clock (the newest written by it,
                           within the labeller's slack), or None; None when no diary is on hand
    collector_records      the lob-flow collector's record per day (labels/options_flow.CollectorRecord: the refill test's
                           readings), prior sessions and the read's day (only readings at or before the read are used)
    quote_sweeps_by_day    the lob-flow collector's quote sweeps: day -> [(ts, quoted spread, delta bucket)], prior
                           sessions and the read's day (cut at the read)
    calendar_path          the event calendar file read for the EVENTS questions; None for the skill's own calendar/events.json
    cache                  what the market answerers derive once per day (series, usual links), never saved
    """
    spx_bars_by_day: dict[str, list[dict]] = field(default_factory=dict)
    es_night_bars_by_day: dict[str, list[dict]] = field(default_factory=dict)
    premarket_reads: list[dict] = field(default_factory=list)
    context_bars_by_day: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    today_quote_bars: dict[str, list[dict]] = field(default_factory=dict)
    today_context_from_quotes: bool = False
    daily_closes: dict[str, list[dict]] = field(default_factory=dict)
    index_weights: dict | None = None
    diary_rows: list[dict] = field(default_factory=list)
    prior_diary_row_at: Callable[[str, str], dict | None] | None = None
    collector_records: dict[str, CollectorRecord] = field(default_factory=dict)
    quote_sweeps_by_day: dict[str, list[tuple[str, float, str]]] = field(default_factory=dict)
    calendar_path: str | None = None
    cache: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------- the read's own pieces

def flat_labels(record: dict) -> dict[str, str]:
    """{"group.key": sentence} from a read record's labels."""
    out: dict[str, str] = {}
    for group, labels in (record.get("labels") or {}).items():
        if isinstance(labels, dict):
            for key, sentence in labels.items():
                if isinstance(sentence, str):
                    out[f"{group}.{key}"] = sentence
    return out


def hhmm_of(row_ts: str) -> str:
    return row_ts[11:16]


def bars_up_to(bars: list[dict], row_ts: str) -> list[dict]:
    """The bars finished by ``row_ts``: a bar's minute has fully elapsed (its start plus one minute is at or before the read)."""
    t = datetime.fromisoformat(row_ts)
    return [b for b in bars if datetime.fromisoformat(b["ts"]) + timedelta(minutes=1) <= t]


def bars_before_minute(bars: list[dict], hhmm: str) -> list[dict]:
    """A prior session's bars finished by the same minute of day: those starting before ``hhmm``."""
    return [b for b in bars if hhmm_of(b["ts"]) < hhmm]


def bars_in_window(bars: list[dict], window: tuple[str, str]) -> list[dict]:
    lo, hi = window
    return [b for b in bars if lo <= hhmm_of(b["ts"]) <= hi]


def trailing_days(by_day: dict[str, list], day: str, sessions: int = HISTORY_SESSIONS) -> list[str]:
    return sorted(d for d in by_day if d < day)[-sessions:]


def rank_fraction(value: float, earlier: list[float]) -> float | None:
    """The share of prior values below ``value``; None with fewer than MIN_HISTORY_SESSIONS of them."""
    if len(earlier) < MIN_HISTORY_SESSIONS:
        return None
    return sum(1 for v in earlier if v < value) / len(earlier)


def third_of(fraction: float) -> str:
    return "bottom" if fraction < 1 / 3 else "top" if fraction >= 2 / 3 else "middle"


# ---------------------------------------------------------------- sentence pieces (the label templates' shared words)

THIRDS = ("bottom", "middle", "top")


def third(s: str, nth: int = 0) -> str | None:
    m = re.findall(r"(bottom|middle|top) third", s)
    return m[nth] if len(m) > nth else None


def fifth(s: str, nth: int = 0) -> str | None:
    m = re.findall(r"(in the bottom fifth|between the top and bottom fifths|between the bottom and top fifths|in the top fifth)", s)
    if len(m) <= nth:
        return None
    return {"in the bottom fifth": "bottom", "between the top and bottom fifths": "middle", "between the bottom and top fifths": "middle",
            "in the top fifth": "top"}[m[nth]]


def rank_of(s: str, nth: int = 0) -> float | None:
    """k / n from '... than (on) k of the (last) n ...'."""
    m = re.findall(r"(?:than(?: on)?) (\d+) of the(?: last)? (\d+)", s)
    if len(m) <= nth:
        return None
    k, n = map(int, m[nth])
    return k / n if n else None


def move_sign(s: str) -> int:
    m = re.search(r"\b(rose|risen|fell|fallen|moved ([+-])?[\d.]+)", s)
    if not m:
        return 0
    w = m.group(1)
    if w in ("rose", "risen"):
        return 1
    if w in ("fell", "fallen"):
        return -1
    return -1 if m.group(2) == "-" else 1


def signed_third(sign: int, t: str | None, words: tuple[str, ...]) -> str | None:
    """A ranked size with a direction into five words (big down, small down, flat, small up, big up)."""
    if t is None:
        return None
    if t == "bottom":
        return words[2]
    i = 1 if t == "middle" else 0
    return words[i] if sign < 0 else words[4 - i]


def words_by_third(key: str, words: tuple[str, str, str], nth: int = 0, fifths: bool = False):
    """A parser reading one tercile (or fifth) of the sentence under ``key`` into three words, bottom to top."""
    def parse(labels: dict[str, str], hhmm: str) -> str | None:
        s = labels.get(key)
        if not s:
            return None
        t = (fifth(s, nth) or third(s, nth)) if fifths else third(s, nth)
        return words[THIRDS.index(t)] if t else None
    return parse


# ---------------------------------------------------------------- the label parsers, one per template

def parse_trend_01(labels, hhmm):
    s = labels.get("price.recent_move")
    return signed_third(move_sign(s), third(s), ("big down", "small down", "flat", "small up", "big up")) if s else None


def parse_trend_02(labels, hhmm):
    s = labels.get("open.noise_band")
    if not s:
        return None
    if "above the usual move-from-the-open band" in s:
        return "above band"
    if "below the usual move-from-the-open band" in s or "below the lower edge of the usual" in s:
        return "below band"
    if "between the settled open and yesterday's close" in s:
        return "flat"
    if "above the higher" in s:
        return "up inside"
    if "below the lower" in s:
        return "down inside"
    return None                      # the older template says "inside" without the side


def parse_trend_03(labels, hhmm):
    s = labels.get("price.afternoon_leg")
    if not s or hhmm < "14:45":                  # at 14:30 the leg since 14:00 is TREND-01's 30 minutes
        return None
    return signed_third(move_sign(s), third(s), ("strong down", "down", "flat", "up", "strong up"))


def parse_trend_06(labels, hhmm):
    s = labels.get("price.day_character")
    if not s:
        return None
    word = s.rsplit(": ", 1)[-1].replace(" (ruler estimated)", "")
    return {"building": "followed-through", "mixed": "mixed", "cancelling": "reversed"}.get(word)


def parse_trend_14(labels, hhmm):
    s = labels.get("price.prior_close_push")
    if not s:
        return None
    if third(s, 1) == "bottom":
        return "flat day"
    if " away from yesterday's close" in s:
        return "with the day's side"
    if " toward yesterday's close" in s:
        return "against the day's side"
    return None


def parse_volatility_03(labels, hhmm):
    s = labels.get("range.pace_vs_priced")
    if not s:
        return None
    for words, answer in (("under the usual pace", "little"), ("near the usual pace", "normal share"), ("over the usual pace", "most")):
        if words in s:
            return answer
    return None


def parse_volatility_10(labels, hhmm):
    s = labels.get("skew.put_tilt_vs_usual")
    if not s:
        return None
    if "a call tilt" in s:
        return "tilted to calls"
    for words, answer in (("a steep put tilt", "steep"), ("a usual tilt", "usual"), ("a flat tilt", "flat")):
        if words in s:
            return answer
    k = rank_of(s)                   # the older template: "steeper than k of n sessions", cut at the label's fifths
    if k is None:
        return None
    return "steep" if k >= SKEW_STEEP_RANK else "flat" if k <= SKEW_FLAT_RANK else "usual"


def parse_volatility_11(labels, hhmm):
    s = labels.get("vol.vvix_vs_vix")
    if not s:
        return None
    m = re.search(r"VVIX (rose|fell) ([\d.]+) points; .*?explain a (rise|fall) of about ([\d.]+)", s)
    if not m:
        return None
    if "under the median" in s:
        return "about as much"
    change = float(m.group(2)) * (1 if m.group(1) == "rose" else -1)
    explained = float(m.group(4)) * (1 if m.group(3) == "rise" else -1)
    return "more" if change - explained > 0 else "less"


def parse_volatility_16(labels, hhmm):
    s = labels.get("vol.ruler_event_load")
    if not s:
        return None
    m = re.search(r"morning rulers, (bottom|middle|top) third", s)
    if not m:
        return None
    size = {"bottom": "compressed", "middle": "normal", "top": "swollen"}[m.group(1)]
    calendar = re.search(r"on the event calendar today: ([^;]*)", s)
    times = []
    if calendar and "; none of them is" not in s:          # the label's own clause: no event of the day loads the ruler
        times = [t for words in LOADING_WORDS for t in re.findall(re.escape(words) + r" at (\d\d:\d\d)", calendar.group(1))]
    event = "no event" if not times else ("event ahead" if any(t > hhmm for t in times) else "event released")
    return f"{size}, {event}"


def parse_levels_01(labels, hhmm):
    s = labels.get("gap.fill_progress")
    if not s:
        return None
    if s.startswith("the gap up is still open"):
        return "holding up"
    if s.startswith("the gap down is still open"):
        return "holding down"
    if s.startswith("there was no real gap"):
        return "no real gap"
    if s.startswith("the gap up is losing ground") or s.startswith("the gap down is losing ground"):
        return "filled" if "it first touched yesterday's close" in s else "fading"     # the label's losing side holds both
    return None


def parse_levels_02(labels, hhmm):
    s = labels.get("price.day_move_split")
    t = third(s) if s else None
    if not t:
        return None
    side = "above" if " above yesterday's close" in s.split(";")[0] else "below"
    return "near the close" if t == "bottom" else f"far {side}" if t == "top" else side


def parse_levels_03(labels, hhmm):
    s = labels.get("levels.prior_day")
    if not s:
        return None
    if "crossed above yesterday's high" in s and "at or past the 20-minute acceptance" in s:
        return "accepted above"
    if "crossed below yesterday's low" in s and "at or past the 20-minute acceptance" in s:
        return "accepted below"
    if "back inside" in s and "after trading above" in s:
        return "rejected from high"
    if "back inside" in s and "after trading below" in s:
        return "rejected from low"
    if s.startswith("price is inside yesterday's range"):
        return "inside"
    return "beyond, not yet accepted" if "yesterday's" in s else None


def parse_levels_05(labels, hhmm):
    s = labels.get("open.fresh_extreme")
    if not s:
        return None
    if "set a new session high" in s:
        return "new high"
    if "set a new session low" in s:
        return "new low"
    m = re.search(r"\((\d+)% of the way up", s)
    if not m:
        return None
    p = int(m.group(1))
    return "top fifth" if p >= 80 else "bottom fifth" if p <= 20 else "middle"


def parse_levels_11(labels, hhmm):
    s = labels.get("levels.break_armed")
    if not s:
        return None
    if "a break upward is armed" in s:
        return "armed up"
    if "a break downward is armed" in s:
        return "armed down"
    if s.startswith("no break is armed now"):
        return "expired / called off"
    return "nothing armed" if "armed" in s else None


def parse_options_01(labels, hhmm):
    s = labels.get("gex.flip_distance")
    t = third(s) if s else None
    if not t:
        return None
    side = "above" if "sigma above the gamma flip" in s else "below"
    return "near" if t == "bottom" else side if t == "middle" else f"far {side}"


def parse_options_03(labels, hhmm):
    s = labels.get("gex.magnet_distance")
    t = third(s) if s else None
    if not t:
        return None
    spx_side = "below" if "sigma above price" in s.split(",")[0] else "above"      # a strike above price: SPX is below it
    return "on it" if t == "bottom" else spx_side if t == "middle" else f"far {spx_side}"


def parse_options_07(labels, hhmm):
    s = labels.get("options.flow_lean_30")
    k = rank_of(s) if s else None
    if k is None:
        return None
    return ("strong put lean" if k <= 0.1 else "put lean" if k <= 0.2 else "balanced" if k < 0.8
            else "call lean" if k < 0.9 else "strong call lean")


def parse_options_10(labels, hhmm):
    s = labels.get("gex.charm_wall_distance")
    if not s or hhmm < "14:00":
        return None
    if "at price" in s:
        return "at price"
    side = "above" if "sigma above price" in s else "below"
    if "near price" in s:
        return f"near {side}"
    return "far" if "from price" in s else None


def parse_breadth_01(labels, hhmm):
    s = labels.get("leaders.equal_weight_vs_cap_30m")
    t = third(s) if s else None
    if t is None:
        return None
    if t == "bottom":
        return "as expected"
    if re.search(r"[\d.]+ sigma above that", s):
        return "more up"
    return "more down" if re.search(r"[\d.]+ sigma below that", s) else None


def parse_breadth_06(labels, hhmm):
    s = labels.get("leaders.semis_vs_index_30m")
    f = fifth(s) if s else None
    return {"bottom": "more down", "middle": "in line", "top": "more up"}[f] if f else None


def parse_flow_01(labels, hhmm):
    s = labels.get("price.vs_vwap")
    t = third(s) if s else None
    if not t:
        return None
    side = "above" if "sigma above the day's volume-weighted" in s else "below"
    return "at" if t == "bottom" else f"near {side}" if t == "middle" else f"far {side}"


def parse_macro_09(labels, hhmm):
    s = labels.get("premarket.legs")
    if not s:
        return None
    if hhmm > MACRO_09_UNTIL:
        return None
    tail = s.split("so far. ")[-1]
    if "No finished leg" in tail:
        return "night quiet"
    if "one way" in tail:
        return "all agree"
    if "the latest against" in tail:
        return "latest turned against"
    if "split" in tail:
        return "split, latest with the night"
    if "Only one finished leg" in tail:
        return "one leg moved"
    if "quiet" in tail:
        return "night quiet"
    return None


def parse_macro_09_arc(labels, hhmm):
    s = labels.get("premarket.arc")
    if not s or hhmm > MACRO_09_UNTIL:
        return None
    return next((a for w, a in (("built on it", "built"), ("first move held", "held"), ("first move faded", "faded"),
                                ("reversed its first move", "reversed"), ("no finished leg", "no real move")) if w in s), None)


def parse_events_04(labels, hhmm):
    s = labels.get("shock.burst")
    if not s or "shock" not in s or "inside the first" in s:       # a burst at a release is EVENTS-03's reaction
        return None
    side = "up" if "price rose" in s else "down" if "price fell" in s else None
    if side is None:
        return None
    return f"{side} burst {'given back' if 'past the half line' in s else 'held'}"


def parse_session_01(labels, hhmm):
    phase = labels.get("calendar.expiry_phase") or ""
    turn = labels.get("calendar.month_turn") or ""
    opex = labels.get("expiry.opex_today") or ""
    if "of the quarter" in turn or "quarter's last trading day" in opex:
        return "quarter-end"
    if "is the monthly expiry" in phase or "is the quarterly expiry" in phase:
        return "monthly/quarterly expiry day"
    if "inside the 4-day opex-week window" in phase:
        return "OPEX week"
    if "inside the 5-day after-opex window" in phase:
        return "week after OPEX"
    return "ordinary" if "expiry" in phase else None


def parse_sentiment_01(labels, hhmm):
    s = labels.get("xasset.btc_gap_30min")
    if not s:
        return None
    if "stocks-up direction" in s:
        return "ahead up"
    if "stocks-down direction" in s:
        return "ahead down"
    return "in line" if "bitcoin" in s else None


def parse_breadth_04(labels, hhmm):
    s = labels.get("sectors.agreement_30m")
    m = re.search(r"that count is .*?(bottom|middle|top) third", s) if s else None
    return {"bottom": "less together", "middle": "about usual", "top": "more together"}[m.group(1)] if m else None


def parse_flow_03(labels, hhmm):
    s = labels.get("liquidity.spy_quote")
    size = s.split("; the size showing", 1)[1] if s and "; the size showing" in s else None
    k = rank_of(size) if size else None
    return {"bottom": "thin", "middle": "normal", "top": "thick"}[third_of(k)] if k is not None else None


def parse_flow_05(labels, hhmm):
    s = labels.get("volume.spy_pace_30")
    k = rank_of(s) if s else None
    return {"bottom": "light", "middle": "normal", "top": "heavy"}[third_of(k)] if k is not None else None


LABEL_PARSERS = {
    "TREND-01": parse_trend_01, "TREND-02": parse_trend_02, "TREND-03": parse_trend_03, "TREND-06": parse_trend_06,
    "TREND-14": parse_trend_14,
    "VOLATILITY-01": words_by_third("vol.straddle_vs_clock", ("kept less", "usual share", "kept more")),
    "VOLATILITY-02": words_by_third("vol.realized_vs_clock", ("quiet", "ordinary", "busy"), fifths=True),
    "VOLATILITY-03": parse_volatility_03,
    "VOLATILITY-05": words_by_third("vol.vix_vs_price", ("calmer", "in line", "more fearful")),
    "VOLATILITY-06": words_by_third("vol.atm_iv_residual", ("less", "about as much", "risen more")),
    "VOLATILITY-09": words_by_third("skew.shift_vs_price", ("puts cheaper", "in line", "puts richer")),
    "VOLATILITY-10": parse_volatility_10, "VOLATILITY-11": parse_volatility_11,
    "VOLATILITY-13": words_by_third("vol.front_fear_shift", ("fallen", "held", "risen")),
    "VOLATILITY-16": parse_volatility_16,
    "LEVELS-01": parse_levels_01, "LEVELS-02": parse_levels_02, "LEVELS-03": parse_levels_03, "LEVELS-05": parse_levels_05,
    "LEVELS-11": parse_levels_11,
    "OPTIONS-01": parse_options_01, "OPTIONS-03": parse_options_03, "OPTIONS-07": parse_options_07, "OPTIONS-10": parse_options_10,
    "BREADTH-01": parse_breadth_01, "BREADTH-04": parse_breadth_04,
    "BREADTH-05": words_by_third("leaders.rotation_30m", ("lag", "match", "beat")),
    "BREADTH-06": parse_breadth_06,
    "FLOW-01": parse_flow_01,
    "FLOW-03": parse_flow_03, "FLOW-05": parse_flow_05,
    "MACRO-09": parse_macro_09, "MACRO-09-ARC": parse_macro_09_arc,
    "EVENTS-04": parse_events_04, "SESSION-01": parse_session_01, "SENTIMENT-01": parse_sentiment_01,
}
PREMARKET_QUESTIONS = ("MACRO-09", "MACRO-09-ARC")      # their labels live on the premarket lane's reads (LEVELS-08 too, answer_levels_08)


# ---------------------------------------------------------------- bars measures (one per bars question)

def _closes(bars: list[dict]) -> list[float]:
    return [float(b["close"]) for b in bars]


def hour_path(bars: list[dict]) -> tuple[float, float] | None:
    """TREND-05's measure over the last 60 finished minutes: (net move, efficiency = net / summed absolute 5-minute moves)."""
    if len(bars) < 61:
        return None
    c = _closes(bars)
    net = c[-1] - c[-61]
    path = sum(abs(c[-1 - 5 * i] - c[-6 - 5 * i]) for i in range(12))
    return net, (net / path if path else 0.0)


def pace_ratio(bars: list[dict]) -> tuple[float, float, float] | None:
    """TREND-08's measure: (d10, ratio, d20) with d20 = C[t-10]-C[t-30], d10 = C[t]-C[t-10], ratio = sign(d20) d10 / (|d20| / 2)."""
    if len(bars) < 31:
        return None
    c = _closes(bars)
    d10, d20 = c[-1] - c[-11], c[-11] - c[-31]
    if d20 == 0:
        return None
    return d10, (d10 * (1 if d20 > 0 else -1)) / (abs(d20) / 2), d20


def leg_shape(bars: list[dict]) -> tuple[float, float, bool] | None:
    """TREND-09's measure over the last 60 minutes: (net, given-back share of the push, pullback faster per minute than the
    push, counted only once the extreme is FAST_MIN_MINUTES old)."""
    if len(bars) < 61:
        return None
    w = bars[-61:]
    start, now = float(w[0]["close"]), float(w[-1]["close"])
    net = now - start
    if net >= 0:
        i = max(range(61), key=lambda j: float(w[j]["high"]))
        extreme = float(w[i]["high"])
        push, back = extreme - start, extreme - now
    else:
        i = min(range(61), key=lambda j: float(w[j]["low"]))
        extreme = float(w[i]["low"])
        push, back = start - extreme, now - extreme
    give_back = back / push if push else 0.0
    fast = 60 - i >= FAST_MIN_MINUTES and back / (60 - i) > push / max(i, 1)
    return net, give_back, fast


def bar_width_ratio(bars: list[dict]) -> float | None:
    """TREND-12's measure: the widest of the last 30 one-minute ranges over their median."""
    if len(bars) < 30:
        return None
    ranges = [float(b["high"]) - float(b["low"]) for b in bars[-30:]]
    median = statistics.median(ranges)
    return max(ranges) / median if median else None


def window_range(bars: list[dict], window: tuple[str, str], minutes: int) -> float | None:
    """High minus low over a fixed clock window, only when every minute of it is there."""
    inside = bars_in_window(bars, window)
    if len(inside) < minutes:
        return None
    return max(float(b["high"]) for b in inside) - min(float(b["low"]) for b in inside)


def night_bars_up_to(night_bars: list[dict], day: str, hhmm: str) -> list[dict]:
    """A night's bars finished by ``hhmm`` on ``day`` (every bar from the earlier calendar days, then those before the minute)."""
    return [b for b in night_bars if b["ts"][:10] < day or (b["ts"][:10] == day and hhmm_of(b["ts"]) < hhmm)]


def night_range_share(night_bars: list[dict], day: str) -> float | None:
    """LEVELS-08's fallback measure: the night's /ES high-low from the NIGHT_REOPEN reopen to NIGHT_UNTIL, as a share of its
    last price there."""
    night = [b for b in night_bars_up_to(night_bars, day, NIGHT_UNTIL) if b["ts"][:10] == day or hhmm_of(b["ts"]) >= NIGHT_REOPEN]
    if not night or not float(night[-1]["close"]):
        return None
    return (max(float(b["high"]) for b in night) - min(float(b["low"]) for b in night)) / float(night[-1]["close"])


# ---------------------------------------------------------------- the bars questions

def prior_measures(history: MarketHistory, day: str, hhmm: str, measure) -> list:
    """``measure`` applied to each trailing session's bars finished by the same minute; the sessions where it is defined."""
    out = []
    for d in trailing_days(history.spx_bars_by_day, day):
        m = measure(bars_before_minute(history.spx_bars_by_day[d], hhmm))
        if m is not None:
            out.append(m)
    return out


def answer_trend_05(record, bars, history, day, hhmm):
    m = hour_path(bars)
    if m is None:
        return None
    prior = prior_measures(history, day, hhmm, hour_path)
    nets = [abs(p[0]) for p in prior]
    size = rank_fraction(abs(m[0]), nets)
    if size is None:
        return None
    if third_of(size) == "bottom":
        return "no hour move"
    # a big hour is ranked against the hours that moved: an hour that went nowhere has no telling efficiency
    moved = [abs(p[1]) for p in prior if rank_fraction(abs(p[0]), nets) >= 1 / 3]
    efficiency = rank_fraction(abs(m[1]), moved)
    if efficiency is None:
        return None
    way = "one-way" if efficiency >= 0.5 else "two-way"
    return f"{way} {'up' if m[0] > 0 else 'down'}"


def answer_trend_08(record, bars, history, day, hhmm):
    m = pace_ratio(bars)
    sigma = record.get("sigma")
    if m is None or not isinstance(sigma, (int, float)) or sigma <= 0:
        return None
    d10, ratio, d20 = m
    tolerance = REVERSE_TOLERANCE_SIGMA * sigma
    if abs(d20) <= tolerance:
        return None
    side = "up" if d20 > 0 else "down"
    if ratio < 0 and abs(d10) > tolerance:
        return f"{side} reversing"
    rank = rank_fraction(ratio, [p[1] for p in prior_measures(history, day, hhmm, pace_ratio) if p[1] >= 0])
    if rank is None:
        return None
    return f"{side} {({'top': 'accelerating', 'middle': 'steady', 'bottom': 'fading'})[third_of(rank)]}"


def answer_trend_09(record, bars, history, day, hhmm):
    m = leg_shape(bars)
    if m is None:
        return None
    prior = prior_measures(history, day, hhmm, leg_shape)
    if len(prior) < MIN_HISTORY_SESSIONS:
        return None
    net, give_back, fast = m
    if abs(net) <= statistics.median(abs(p[0]) for p in prior):
        return "no leg"
    usual_give_back = statistics.median(p[1] for p in prior)
    deep = give_back > usual_give_back
    fast = fast and give_back >= FAST_GIVE_BACK_SHARE * usual_give_back
    return f"{'up' if net > 0 else 'down'} leg {'deep-or-fast' if deep or fast else 'shallow-slow'}"


def answer_trend_10(record, bars, history, day, hhmm):
    sigma = record.get("sigma")
    if len(bars) < 70 or not isinstance(sigma, (int, float)) or sigma <= 0:
        return None
    reference, recent = bars[-70:-10], bars[-10:]
    high = max(float(b["high"]) for b in reference)
    low = min(float(b["low"]) for b in reference)
    tolerance = BREAK_TOLERANCE_SIGMA * sigma
    now = float(recent[-1]["close"])
    if max(float(b["high"]) for b in recent) > high + tolerance:
        return "up held" if now > high else "up failed"
    if min(float(b["low"]) for b in recent) < low - tolerance:
        return "down held" if now < low else "down failed"
    return "none"


def answer_trend_12(record, bars, history, day, hhmm):
    ratio = bar_width_ratio(bars)
    if ratio is None:
        return None
    rank = rank_fraction(ratio, prior_measures(history, day, hhmm, bar_width_ratio))
    if rank is None:
        return None
    return "normal" if rank < 2 / 3 else "large" if rank < 0.9 else "burst"


def answer_levels_09(record, bars, history, day, hhmm):
    if hhmm < "10:05":
        return None
    size = window_range(bars, OPENING_RANGE_WINDOW, 30)
    if size is None:
        return None
    prior = [r for d in trailing_days(history.spx_bars_by_day, day) if (r := window_range(history.spx_bars_by_day[d], OPENING_RANGE_WINDOW, 30)) is not None]
    rank = rank_fraction(size, prior)
    if rank is None:
        return None
    return {"bottom": "narrow", "middle": "normal", "top": "wide"}[third_of(rank)]


def _minutes_ago(s: str, words: str) -> int | None:
    m = re.search(words + r" (\d+) minutes? ago", s)
    return int(m.group(1)) if m else None


def box_latest_break(bars: list[dict]) -> str | None:
    """"up" / "down": the side of the opening box (the first 30 minutes) a close after it broke past most recently, by
    range.box_status's rule (past the box by measures.move_bar); None without the box or a break."""
    box = bars_in_window(bars, OPENING_HALF_HOUR)
    bar = move_bar(bars)
    if len(box) < 30 or bar is None:
        return None
    high, low = max(float(b["high"]) for b in box), min(float(b["low"]) for b in box)
    for b in reversed([b for b in bars if hhmm_of(b["ts"]) > OPENING_HALF_HOUR[1]]):
        if float(b["close"]) > high + bar:
            return "up"
        if float(b["close"]) < low - bar:
            return "down"
    return None


def answer_levels_06(record, bars, history, day, hhmm):
    """The first hour's range from 10:30, the opening box before it. A break past both edges is the failed break of the
    side broken more recently: the first-hour label says how many minutes ago each was; the box label does not, so the
    bars say which side broke last."""
    labels = flat_labels(record)
    s = labels.get("range.first_hour") if hhmm >= "10:30" else labels.get("range.box_status")
    if not s or "still forming" in s:
        return None
    if "no bar has gone more than" in s:                       # within the break buffer: no break, whatever side price sits
        return "inside"
    if s.startswith("price is above the first hour's high") or s.startswith("price has broken above"):
        return "up break holding"
    if s.startswith("price is below the first hour's low") or s.startswith("price has broken below"):
        return "down break holding"
    if "broke both edges" in s:
        up, down = _minutes_ago(s, "above its high"), _minutes_ago(s, "below its low")
        if up is None or down is None:
            return None
        return "up break failed" if up < down else "down break failed"
    if "broke out both ways and came back" in s:
        side = box_latest_break(bars)
        return f"{side} break failed" if side else None
    if "back inside" in s or "came back inside" in s:
        broke_up = "broke above" in s or ("broke its high" in s and "never broke its high" not in s)
        return "up break failed" if broke_up else "down break failed"
    return "inside" if "inside" in s else None


def answer_levels_08(record, bars, history, day, hhmm):
    """The night's range by its third: the day's last premarket read's overnight.range_vs_normal, else (no such label before
    the read) the night's /ES high-low since the reopen ranked against the trailing nights'."""
    s = premarket_labels(history, record.get("row_ts") or "").get("overnight.range_vs_normal")
    words = {"bottom": "small", "middle": "normal", "top": "large"}
    if s:
        t = third(s)
        return words[t] if t else None
    tonight = night_range_share(history.es_night_bars_by_day.get(day, []), day)
    if tonight is None:
        return None
    prior = [r for d in trailing_days(history.es_night_bars_by_day, day) if (r := night_range_share(history.es_night_bars_by_day[d], d)) is not None]
    rank = rank_fraction(tonight, prior)
    return words[third_of(rank)] if rank is not None else None


def _reference_levels(history: MarketHistory, day: str, first_bar: dict) -> list[float]:
    """FLOW-08's references: the prior session's high and low, and the night's /ES high and low moved onto SPX by the
    09:30 basis (SPX's first open less /ES's last night close)."""
    levels = []
    prior_days = trailing_days(history.spx_bars_by_day, day, 1)
    if prior_days:
        prior = history.spx_bars_by_day[prior_days[0]]
        levels += [max(float(b["high"]) for b in prior), min(float(b["low"]) for b in prior)]
    night = night_bars_up_to(history.es_night_bars_by_day.get(day, []), day, OPENING_HALF_HOUR[0])
    if night:
        basis = float(first_bar["open"]) - float(night[-1]["close"])
        levels += [max(float(b["high"]) for b in night) + basis, min(float(b["low"]) for b in night) + basis]
    return levels


def answer_flow_08(record, bars, history, day, hhmm):
    sigma = record.get("sigma")
    if hhmm < FLOW_08_FROM or not isinstance(sigma, (int, float)) or sigma <= 0:
        return None
    half_hour = bars_in_window(bars, OPENING_HALF_HOUR)
    settled = next((i for i, b in enumerate(half_hour) if hhmm_of(b["ts"]) == FLOW_08_SETTLED_BAR), None)
    if len(half_hour) < 30 or settled is None:
        return None
    high = max(float(b["high"]) for b in half_hour)
    low = min(float(b["low"]) for b in half_hour)
    if high <= low:
        return None
    span = high - low
    first_open = float(half_hour[settled]["open"])
    open_at = (first_open - low) / span
    close_at = (float(half_hour[-1]["close"]) - low) / span
    touch = FLOW_08_TOUCH_SIGMA * sigma
    later = half_hour[settled + 1:]
    if open_at <= FLOW_08_OPEN_EDGE and close_at >= 1 - FLOW_08_CLOSE_EDGE and min(float(b["low"]) for b in later) >= first_open - touch:
        return "drive up"
    if open_at >= 1 - FLOW_08_OPEN_EDGE and close_at <= FLOW_08_CLOSE_EDGE and max(float(b["high"]) for b in later) <= first_open + touch:
        return "drive down"
    middle = low + span / 2
    for level in _reference_levels(history, day, half_hour[0]):
        tags = [i for i, b in enumerate(half_hour) if float(b["low"]) - touch <= level <= float(b["high"]) + touch]
        if not tags:
            continue
        down = level >= middle
        if not (close_at <= FLOW_08_CLOSE_EDGE if down else close_at >= 1 - FLOW_08_CLOSE_EDGE):
            continue
        far = next(i for i, b in enumerate(half_hour)
                   if ((float(b["close"]) - low) / span <= FLOW_08_CLOSE_EDGE if down else (float(b["close"]) - low) / span >= 1 - FLOW_08_CLOSE_EDGE))
        if tags[0] < far:                                         # the test came before the drive reached the far edge
            return f"test-then-drive {'down' if down else 'up'}"
    return "rotation"


BARS_ANSWERERS = {
    "TREND-05": answer_trend_05, "TREND-08": answer_trend_08, "TREND-09": answer_trend_09, "TREND-10": answer_trend_10,
    "TREND-12": answer_trend_12, "LEVELS-06": answer_levels_06, "LEVELS-08": answer_levels_08, "LEVELS-09": answer_levels_09,
    "FLOW-08": answer_flow_08,
}


# ---------------------------------------------------------------- the whole read

def premarket_labels(history: MarketHistory, row_ts: str) -> dict[str, str]:
    """The labels of the day's last premarket-lane read made before ``row_ts``; empty without one."""
    before = [r for r in history.premarket_reads if isinstance(r.get("row_ts"), str) and r["row_ts"] <= row_ts]
    return flat_labels(before[-1]) if before else {}


def book_is_native(history: MarketHistory, row_ts: str) -> bool:
    """The read's newest diary row at or before it carries SPX's own options book, not a scaled SPY stand-in."""
    rows = [r for r in history.diary_rows if r["ts"] <= row_ts]
    return bool(rows) and rows[-1].get("gex_source") == NATIVE_BOOK


def answer_code_features(read_record: dict, bars_up_to_read: list[dict], market_history: MarketHistory | None = None) -> dict[str, str | None]:
    """Every catalog question's answer for one read, None where the data is missing or the sentence is not a known template.
    Never raises: a parser's error is that question's None."""
    from .code_features_market import MARKET_ANSWERERS         # the market answerers use this module's helpers, so they import it, not the reverse
    history = market_history or MarketHistory()
    row_ts = read_record.get("row_ts") or ""
    day, hhmm = row_ts[:10], hhmm_of(row_ts)
    own = flat_labels(read_record)
    night = premarket_labels(history, row_ts) if history.premarket_reads else {}
    native = book_is_native(history, row_ts)
    answers: dict[str, str | None] = {}
    for question in load_catalog():
        qid = question["id"]
        try:
            if qid in GEX_BOOK_QUESTIONS and not native:
                answer = None
            elif qid in LABEL_PARSERS:
                labels = night if qid in PREMARKET_QUESTIONS else own
                answer = LABEL_PARSERS[qid](labels, hhmm)
            elif qid in BARS_ANSWERERS:
                answer = BARS_ANSWERERS[qid](read_record, bars_up_to_read, history, day, hhmm)
            elif qid in MARKET_ANSWERERS:
                answer = MARKET_ANSWERERS[qid](read_record, bars_up_to_read, history, day, hhmm)
            else:
                answer = None
        except Exception:
            answer = None
        answers[qid] = answer if answer in question["options"] else None
    return answers
