"""The code feature builder: the 90 code questions of the merged set (the 51 ready ones and the 39 of Phase 2), answered by
code from one read's stored data.

The catalog (code_feature_catalog.json beside this file) lists every question: id, title, layer (1 a market value ranked
against its own history, 2 a discrete label), group (the voting group the scorer and matcher take one vote per), method
(label / bars / market_value), source, label_keys, options (the exact answer list) and notes; an entry with status
needs_new_feed is in the catalog and silent until its feed exists.

    answer_code_features(read_record, bars_up_to_read, market_history) -> {question_id: answer | None}

answers every question for one read from what was known at the read time only: the label sentences as archived, the SPX
minute bars finished by row_ts, and a MarketHistory of prior sessions (never the read's own day) plus the night's /ES and
/ZN bars, the day's premarket reads, and (code_features_market.py) the market feed's symbols cut at the read, the daily
closes before the day, the index weights dated on or before it, the calendar, the day's diary rows and quote sweeps. A
question whose data is missing, or whose sentence is an unknown template, answers None (silent) and never raises. Every
parser is a pure function of the sentences; the bars and market questions compute their measure and rank it against the
same measure at the same minute on the trailing HISTORY_SESSIONS sessions.
"""
from __future__ import annotations

import json
import re
import statistics
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

CATALOG_FILE = Path(__file__).with_name("code_feature_catalog.json")
COLUMN_PREFIX = "code"               # a question's column in the answer matrix is "code:<question_id>"

HISTORY_SESSIONS = 20                # a measure is ranked against the trailing 20 sessions at the same minute
MIN_HISTORY_SESSIONS = 10            # fewer prior sessions with the measure than this: the rank is unknown, the answer None
GAP_HISTORY_SESSIONS = 60            # VOLATILITY-08 ranks the opening gap against 60 sessions: the SPX bars history reaches that far
BREAK_TOLERANCE_SIGMA = 0.02         # TREND-10: a break must clear the 60-minute high/low by this share of the read's sigma
REVERSE_TOLERANCE_SIGMA = 0.02       # TREND-08: an opposite 10-minute move smaller than this share of sigma is ranked, not "reversing"
# FLOW-08's thresholds are a DRAFT (the rules are not written anywhere else yet): the open sits in the outer
# FLOW_08_OPEN_EDGE of the first half hour's range, the close in the opposite FLOW_08_CLOSE_EDGE, and a reference level
# counts as tagged within FLOW_08_TOUCH_SIGMA of the read's sigma.
FLOW_08_OPEN_EDGE = 0.10
FLOW_08_CLOSE_EDGE = 0.20
FLOW_08_TOUCH_SIGMA = 0.05
LATE_NIGHT_FROM = "03:00"            # MACRO-08: the night's range built from this hour ET is "late", before it "Asia"
FIRST_BAR_WINDOW = ("09:30", "09:34")        # LEVELS-09 before 10:05: the first five minutes
OPENING_RANGE_WINDOW = ("09:35", "10:04")    # LEVELS-09 from 10:05: the first 30 minutes from the settled open
OPENING_HALF_HOUR = ("09:30", "09:59")       # FLOW-08


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
    es_night_bars_by_day   /ES bars outside the regular session for the night into each day, the read's own day included
                           (its bars are cut at the read time)
    add_by_day             prior sessions' advance-decline line ($ADD) per minute: day -> [{"ts", "value"}] in time order
    premarket_reads        the read's day's premarket-lane read records, in time order (only those before the read are used)
    zn_night_bars_by_day   /ZN bars outside the regular session, as es_night_bars_by_day
    context_bars_by_day    the market feed's minute bars per symbol (context/bars, with volume): day -> symbol -> bars in
                           time order, prior sessions and the read's own day (its bars are cut at the read time)
    daily_closes           symbol -> daily rows (day, open, high, low, close) of the sessions before the read's day
    index_weights          the index weights document (index_weights.pick takes the entry dated on or before the day)
    diary_rows             the read's day's SPX diary rows, slim: ts, call_wall, put_wall, call_wall_tenor, put_wall_tenor,
                           magnet, atm_iv (only those at or before the read are used)
    quote_sweeps_by_day    the lob-flow collector's quote sweeps: day -> [(ts, 25-40 delta quoted spread)], prior sessions
                           and the read's day (cut at the read)
    calendar_path          the event calendar file read for the EVENTS questions; None for the skill's own calendar/events.json
    cache                  what the market answerers derive once per day (series, usual links), never saved
    """
    spx_bars_by_day: dict[str, list[dict]] = field(default_factory=dict)
    es_night_bars_by_day: dict[str, list[dict]] = field(default_factory=dict)
    add_by_day: dict[str, list[dict]] = field(default_factory=dict)
    premarket_reads: list[dict] = field(default_factory=list)
    zn_night_bars_by_day: dict[str, list[dict]] = field(default_factory=dict)
    context_bars_by_day: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    daily_closes: dict[str, list[dict]] = field(default_factory=dict)
    index_weights: dict | None = None
    diary_rows: list[dict] = field(default_factory=list)
    quote_sweeps_by_day: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
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
    m = re.findall(r"(in the bottom fifth|between the top and bottom fifths|in the top fifth)", s)
    if len(m) <= nth:
        return None
    return {"in the bottom fifth": "bottom", "between the top and bottom fifths": "middle", "in the top fifth": "top"}[m[nth]]


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
    if not s or hhmm < "14:30":
        return None
    return signed_third(move_sign(s), third(s), ("strong down", "down", "flat", "up", "strong up"))


def parse_trend_06(labels, hhmm):
    s = labels.get("price.day_character")
    if not s:
        return None
    word = s.rsplit(": ", 1)[-1].replace(" (ruler estimated)", "")
    return word if word in ("building", "mixed", "cancelling") else None


def parse_trend_14(labels, hhmm):
    s = labels.get("price.prior_close_push")
    if not s:
        return None
    if third(s, 0) == "bottom":
        return "quiet leg"
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
    k = rank_of(s)                   # the older template: "steeper than k of n sessions"
    if k is None:
        return None
    return "steep" if k >= 2 / 3 else "flat" if k < 1 / 3 else "usual"


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
    calendar = re.search(r"on the event calendar today: (.*?)(?:; none of them|; this morning)", s)
    times = re.findall(r" at (\d\d:\d\d)", calendar.group(1)) if calendar else []
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
    return "filled / too small" if "gap" in s else None


def parse_levels_02(labels, hhmm):
    s = labels.get("price.day_move_split")
    t = third(s) if s else None
    if not t:
        return None
    side = "above" if " above yesterday's close" in s.split(";")[0] else "below"
    position = "near the close" if t == "bottom" else f"far {side}" if t == "top" else side
    m = re.search(r"so the gap is (-?\d+)% of the day's move", s)
    if m:
        driver = "mostly gap" if int(m.group(1)) >= 50 else "mostly trading"
    elif "came from the opening gap" in s and "most of it came from the gap" in s:
        driver = "mostly gap"
    else:
        driver = "mostly trading" if "trading" in s.split(";")[-1] else "mixed"
    return f"{position}; {driver}"


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


def parse_levels_04(labels, hhmm):
    s = labels.get("levels.prior_value")
    if not s:
        return None
    side = ("above" if "above the high of yesterday's value area" in s
            else "below" if "below the low of yesterday's value area" in s else "inside")
    move = labels.get("price.day_move")
    if "value area" not in s:
        return None
    if side == "inside":
        return side
    if not move:
        return None
    with_it = (move_sign(move) > 0) == (side == "above")
    return f"{side}, {'with' if with_it else 'against'}"


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


def parse_levels_06(labels, hhmm):
    s = labels.get("range.first_hour") if hhmm >= "10:30" else labels.get("range.box_status")
    if not s or "still forming" in s:
        return None
    if s.startswith("price is above the first hour's high") or s.startswith("price has broken above"):
        return "up break holding"
    if s.startswith("price is below the first hour's low") or s.startswith("price has broken below"):
        return "down break holding"
    if "back inside" in s or "came back inside" in s:
        broke_up = "broke above" in s or ("broke its high" in s and "never broke its high" not in s)
        return "up break failed" if broke_up else "down break failed"
    return "inside" if "inside" in s else None


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


def parse_options_02(labels, hhmm):
    s = labels.get("gex.magnet_distance")
    m = re.search(r"its grip \(top-strike share\) is stronger than on (\d+) of the last (\d+)", s) if s else None
    if not m or int(m.group(2)) == 0:
        return None
    k = int(m.group(1)) / int(m.group(2))
    return "high" if k >= 2 / 3 else "low" if k < 1 / 3 else "normal"


def parse_options_03(labels, hhmm):
    s = labels.get("gex.magnet_distance")
    t = third(s) if s else None
    if not t:
        return None
    spx_side = "below" if "sigma above price" in s.split(",")[0] else "above"      # a strike above price: SPX is below it
    return "on it" if t == "bottom" else spx_side if t == "middle" else f"far {spx_side}"


def parse_options_05(labels, hhmm):
    s = labels.get("options.strike_defense")
    if not s or ("defended" not in s and "abandoned" not in s):
        return None
    side = "above" if re.search(r"is [\d.]+ sigma above price", s) else "below"
    verdict = "defended" if s.rstrip().endswith("defended") or ": defended" in s else "abandoned"
    return f"{verdict} {side}"


def parse_options_07(labels, hhmm):
    s = labels.get("options.flow_lean_30")
    k = rank_of(s) if s else None
    if k is None:
        return None
    return ("strong put lean" if k <= 0.1 else "put lean" if k <= 0.2 else "balanced" if k < 0.8
            else "call lean" if k < 0.9 else "strong call lean")


def parse_options_09(labels, hhmm):
    s, d = labels.get("gex.weight_both_books"), labels.get("gex.delta_weight_side")
    if not s or not d:
        return None
    thirds = (third(s, 0), third(s, 1), third(d))
    if None in thirds:
        return None
    words = {"bottom": "mostly below", "middle": "balanced", "top": "mostly above"}
    today, week, delta = (words[t] for t in thirds)
    return f"today {today}; week {week}; delta {delta}"


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


def parse_options_11(labels, hhmm):
    s = labels.get("gex.walls_since_30min")
    if not s:
        return None
    if "both stayed put" in s:
        return "stand still"
    call = re.search(r"call-side heavy strike of today's same-day book (moved (up|down)|stayed put)", s)
    put = re.search(r"put-side strike (moved (up|down)|stayed put)", s)
    call_way, put_way = (call.group(2) if call else None), (put.group(2) if put else None)
    if call_way and call_way == put_way:
        return f"both moved {call_way}"
    if "closer together" in s:
        return "pulled in"
    return "spread out" if "further apart" in s or "farther apart" in s else None


def parse_breadth_01(labels, hhmm):
    s = labels.get("leaders.equal_weight_vs_cap_30m")
    t = third(s) if s else None
    if t is None:
        return None
    if t != "top":
        return "as expected"
    return "more up" if re.search(r"[\d.]+ sigma above that", s) else "more down"


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
    tail = s.split("so far. ")[-1]
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


def parse_macro_10(labels, hhmm):
    s, c = labels.get("premarket.arc"), labels.get("premarket.since_checkpoint")
    if not s or not c:
        return None
    arc = next((a for w, a in (("built on it", "built"), ("first move held", "held"), ("first move faded", "faded"),
                               ("reversed its first move", "reversed"), ("no finished leg", "no real move")) if w in s), None)
    if arc is None:
        return None
    verdict = next((a for w, a in (("latest leg added", "added"), ("latest leg gave back", "gave back"),
                                   ("latest leg crossed", "crossed 16:00")) if w in c), "small / no verdict")
    return f"{arc}; since checkpoint {verdict}"


def parse_events_04(labels, hhmm):
    s, v = labels.get("shock.burst"), labels.get("shock.vs_day_range")
    if not s or not v or "shock" not in s or "shock" not in v:
        return None
    state = ("fresh" if "inside the 10-minute fresh window" in s else "round-tripped" if "past the half line" in s else "settling")
    extreme = ("no new extreme" if "stayed inside" in v
               else "new extreme held" if "still at the new extreme" in v or "at or" in v else "new extreme given up")
    return f"{state}; {extreme}"


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
    return words_by_third("sectors.agreement_30m", ("less together", "about usual", "more together"), nth=1)(labels, hhmm)


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
    "LEVELS-01": parse_levels_01, "LEVELS-02": parse_levels_02, "LEVELS-03": parse_levels_03, "LEVELS-04": parse_levels_04,
    "LEVELS-05": parse_levels_05, "LEVELS-06": parse_levels_06,
    "LEVELS-08": words_by_third("overnight.range_vs_normal", ("small", "normal", "large")),
    "LEVELS-11": parse_levels_11,
    "OPTIONS-01": parse_options_01, "OPTIONS-02": parse_options_02, "OPTIONS-03": parse_options_03, "OPTIONS-05": parse_options_05,
    "OPTIONS-07": parse_options_07, "OPTIONS-09": parse_options_09, "OPTIONS-10": parse_options_10, "OPTIONS-11": parse_options_11,
    "BREADTH-01": parse_breadth_01, "BREADTH-04": parse_breadth_04,
    "BREADTH-05": words_by_third("leaders.rotation_30m", ("lag", "match", "beat")),
    "BREADTH-06": parse_breadth_06,
    "FLOW-01": parse_flow_01,
    "FLOW-03": words_by_third("liquidity.spy_quote", ("thin", "normal", "thick"), fifths=True), "FLOW-05": parse_flow_05,
    "MACRO-09": parse_macro_09, "MACRO-10": parse_macro_10,
    "EVENTS-04": parse_events_04, "SESSION-01": parse_session_01, "SENTIMENT-01": parse_sentiment_01,
}
PREMARKET_QUESTIONS = ("LEVELS-08", "MACRO-09", "MACRO-10")      # their labels live on the premarket lane's reads


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


def pace_ratio(bars: list[dict]) -> tuple[float, float] | None:
    """TREND-08's measure: (d10, ratio) with d20 = C[t-10]-C[t-30], d10 = C[t]-C[t-10], ratio = sign(d20) d10 / (|d20| / 2)."""
    if len(bars) < 31:
        return None
    c = _closes(bars)
    d10, d20 = c[-1] - c[-11], c[-11] - c[-31]
    if d20 == 0:
        return None
    return d10, (d10 * (1 if d20 > 0 else -1)) / (abs(d20) / 2)


def leg_shape(bars: list[dict]) -> tuple[float, float, bool] | None:
    """TREND-09's measure over the last 60 minutes: (net, given-back share of the push, pullback faster per minute than the push)."""
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
    fast = back / max(60 - i, 1) > push / max(i, 1)
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


def night_late_share(night_bars: list[dict], day: str) -> float | None:
    """MACRO-08's measure: the share of the night's /ES range not already built before LATE_NIGHT_FROM on ``day``."""
    if not night_bars:
        return None
    highs = [float(b["high"]) for b in night_bars]
    lows = [float(b["low"]) for b in night_bars]
    whole = max(highs) - min(lows)
    if whole <= 0:
        return None
    early = [b for b in night_bars if not (b["ts"][:10] == day and hhmm_of(b["ts"]) >= LATE_NIGHT_FROM)]
    if not early:
        return 1.0
    early_range = max(float(b["high"]) for b in early) - min(float(b["low"]) for b in early)
    return 1 - early_range / whole


def night_bars_up_to(night_bars: list[dict], day: str, hhmm: str) -> list[dict]:
    """A night's bars finished by ``hhmm`` on ``day`` (every bar from the earlier calendar days, then those before the minute)."""
    return [b for b in night_bars if b["ts"][:10] < day or (b["ts"][:10] == day and hhmm_of(b["ts"]) < hhmm)]


def gap_side_from_bars(day_bars: list[dict], prior_bars: list[dict]) -> int | None:
    """+1 / -1 for a session that settled (at 09:35) above / below the prior session's last close; None without both."""
    settled = next((b for b in day_bars if hhmm_of(b["ts"]) == OPENING_RANGE_WINDOW[0]), None)
    if settled is None or not prior_bars:
        return None
    gap = float(settled["open"]) - float(prior_bars[-1]["close"])
    return 1 if gap >= 0 else -1


def gap_side_from_label(labels: dict[str, str]) -> int | None:
    s = labels.get("gap.size")
    if not s:
        return None
    return 1 if "opened above" in s else -1 if "opened below" in s else None


def add_value(record: dict) -> float | None:
    """The read's advance-decline line: $ADVN - $DECN from its market context (the derived value), else $ADD."""
    context = record.get("market_context") or {}

    def number(symbol):
        v = (context.get(symbol) or {}).get("value") if isinstance(context.get(symbol), dict) else None
        return float(v) if isinstance(v, (int, float)) else None
    advancing, declining = number("$ADVN"), number("$DECN")
    if advancing is not None and declining is not None:
        return advancing - declining
    return number("$ADD")


def add_at_minute(series: list[dict], hhmm: str) -> float | None:
    """The last $ADD value finished by the minute, from a prior session's per-minute series."""
    value = None
    for point in series:
        if hhmm_of(point["ts"]) < hhmm:
            value = point.get("value")
        else:
            break
    return float(value) if isinstance(value, (int, float)) else None


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
    size = rank_fraction(abs(m[0]), [abs(p[0]) for p in prior])
    if size is None:
        return None
    if third_of(size) == "bottom":
        return "no hour move"
    efficiency = rank_fraction(abs(m[1]), [abs(p[1]) for p in prior])
    way = "one-way" if efficiency >= 0.5 else "two-way"
    return f"{way} {'up' if m[0] > 0 else 'down'}"


def answer_trend_08(record, bars, history, day, hhmm):
    m = pace_ratio(bars)
    sigma = record.get("sigma")
    if m is None or not isinstance(sigma, (int, float)) or sigma <= 0:
        return None
    d10, ratio = m
    if ratio < 0 and abs(d10) > REVERSE_TOLERANCE_SIGMA * sigma:
        return "reversing"
    rank = rank_fraction(ratio, [p[1] for p in prior_measures(history, day, hhmm, pace_ratio)])
    if rank is None:
        return None
    return {"top": "accelerating", "middle": "steady", "bottom": "fading"}[third_of(rank)]


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
    deep = give_back > statistics.median(p[1] for p in prior)
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
        window, minutes = FIRST_BAR_WINDOW, 5
    else:
        window, minutes = OPENING_RANGE_WINDOW, 30
    size = window_range(bars, window, minutes)
    if size is None:
        return None
    prior = [r for d in trailing_days(history.spx_bars_by_day, day) if (r := window_range(history.spx_bars_by_day[d], window, minutes)) is not None]
    rank = rank_fraction(size, prior)
    if rank is None:
        return None
    if minutes == 5:
        return "quiet" if rank < 0.25 else "normal" if rank < 0.5 else "busy" if rank < 0.75 else "very busy"
    return {"bottom": "narrow", "middle": "normal", "top": "wide"}[third_of(rank)]


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
    if hhmm < "10:00" or not isinstance(sigma, (int, float)) or sigma <= 0:
        return None
    half_hour = bars_in_window(bars, OPENING_HALF_HOUR)
    if len(half_hour) < 30:
        return None
    high = max(float(b["high"]) for b in half_hour)
    low = min(float(b["low"]) for b in half_hour)
    if high <= low:
        return None
    span = high - low
    open_at = (float(half_hour[0]["open"]) - low) / span
    close_at = (float(half_hour[-1]["close"]) - low) / span
    first_open = float(half_hour[0]["open"])
    touch = FLOW_08_TOUCH_SIGMA * sigma
    later_closes = [float(b["close"]) for b in half_hour[1:]]
    if open_at <= FLOW_08_OPEN_EDGE and close_at >= 1 - FLOW_08_CLOSE_EDGE and min(later_closes) >= first_open - touch:
        return "drive"
    if open_at >= 1 - FLOW_08_OPEN_EDGE and close_at <= FLOW_08_CLOSE_EDGE and max(later_closes) <= first_open + touch:
        return "drive"
    middle = low + span / 2
    for level in _reference_levels(history, day, half_hour[0]):
        tagged = any(float(b["low"]) - touch <= level <= float(b["high"]) + touch for b in half_hour)
        if not tagged:
            continue
        if level >= middle and close_at <= FLOW_08_CLOSE_EDGE:
            return "test-then-drive"
        if level < middle and close_at >= 1 - FLOW_08_CLOSE_EDGE:
            return "test-then-drive"
    return "rotation"


def answer_macro_08(record, bars, history, day, hhmm):
    tonight = night_late_share(night_bars_up_to(history.es_night_bars_by_day.get(day, []), day, hhmm), day)
    if tonight is None:
        return None
    prior = []
    for d in trailing_days(history.es_night_bars_by_day, day):
        share = night_late_share(night_bars_up_to(history.es_night_bars_by_day[d], d, hhmm), d)
        if share is not None:
            prior.append(share)
    if len(prior) < MIN_HISTORY_SESSIONS:
        return None
    return "mostly late (after 03:00)" if tonight > statistics.median(prior) else "mostly Asia"


def answer_breadth_11(record, bars, history, day, hhmm):
    add = add_value(record)
    side = gap_side_from_label(flat_labels(record))
    if add is None or side is None:
        return None
    prior = []
    days = sorted(history.spx_bars_by_day)
    for d in trailing_days(history.add_by_day, day):
        value = add_at_minute(history.add_by_day[d], hhmm)
        earlier = [x for x in days if x < d]
        prior_side = gap_side_from_bars(history.spx_bars_by_day.get(d, []), history.spx_bars_by_day.get(earlier[-1], []) if earlier else [])
        if value is not None and prior_side is not None:
            prior.append(value * prior_side)
    rank = rank_fraction(add * side, prior)
    if rank is None:
        return None
    return {"top": "more than usual", "middle": "about as usual", "bottom": "less than usual"}[third_of(rank)]


BARS_ANSWERERS = {
    "TREND-05": answer_trend_05, "TREND-08": answer_trend_08, "TREND-09": answer_trend_09, "TREND-10": answer_trend_10,
    "TREND-12": answer_trend_12, "LEVELS-09": answer_levels_09, "FLOW-08": answer_flow_08, "MACRO-08": answer_macro_08,
    "BREADTH-11": answer_breadth_11,
}


# ---------------------------------------------------------------- the whole read

def premarket_labels(history: MarketHistory, row_ts: str) -> dict[str, str]:
    """The labels of the day's last premarket-lane read made before ``row_ts``; empty without one."""
    before = [r for r in history.premarket_reads if isinstance(r.get("row_ts"), str) and r["row_ts"] <= row_ts]
    return flat_labels(before[-1]) if before else {}


def answer_code_features(read_record: dict, bars_up_to_read: list[dict], market_history: MarketHistory | None = None) -> dict[str, str | None]:
    """Every catalog question's answer for one read, None where the data is missing or the sentence is not a known template.
    Never raises: a parser's error is that question's None."""
    from .code_features_market import MARKET_ANSWERERS         # the market answerers use this module's helpers, so they import it, not the reverse
    history = market_history or MarketHistory()
    row_ts = read_record.get("row_ts") or ""
    day, hhmm = row_ts[:10], hhmm_of(row_ts)
    own = flat_labels(read_record)
    night = premarket_labels(history, row_ts) if history.premarket_reads else {}
    answers: dict[str, str | None] = {}
    for question in load_catalog():
        qid = question["id"]
        try:
            if qid in LABEL_PARSERS:
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

