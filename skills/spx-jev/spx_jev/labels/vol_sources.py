"""The volatility family's own sources, read point in time from the station's state: the prior sessions'
diary series (the VIX, the straddle and the VIX curve through each day), and the lob-flow collector's raw
0DTE tape, whose quotes rebuild the same-day skew.

* Prior diaries. A same-clock rank of the straddle or the VIX curve needs each prior session's rows, and
  the Scene keeps only each day's first. They are read here, only for days before the one being built,
  and kept for the process: a past day's file never changes.
* The tape. ``state/lob_flow/raw/{day}/tape.jsonl`` (``tape.jsonl.gz`` once the collector archives the
  day): one line per 0DTE SPXW trade, with the quote it printed into. Lines land up to two hours out of
  time order, so every line up to the read's clock is read (the first line stamped after it was written
  after it, as tape_flow reads the tape, today and on the prior sessions alike) and only the minutes
  asked for are kept: for each, the newest quote per contract in the minute before it.
* The skew. Each quote's mid is turned into an implied volatility (Black's formula on the forward, time
  to the settle in calendar years, as the scanner's ``atm_iv``), the forward from put-call parity at the
  strike where the call and the put are nearest in price. Only out-of-the-money quotes are read: a put
  below the forward, a call above.
"""
from __future__ import annotations

import gzip
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from ..sessions import session_close
from ..state_builder import load_rows
from .measures import is_num

# The scanner writes a row every one to two minutes; a row further than this from a moment does not stand for it.
ROW_MAX_GAP = timedelta(minutes=10)
TAPE_RAW_SUBDIR = Path("lob_flow") / "raw"
TAPE_LINE_START = '{"ts_ms": '          # a trade line; the collector also writes gap markers
SECONDS_PER_YEAR = 365 * 24 * 3600
# A quote wider than this share of its mid is a stale or one-sided book (a 2.40 bid under a 17.90 ask), not a price.
MAX_SPREAD_SHARE = 0.25
# The implied volatility search: the band any 0DTE SPX quote falls in, and enough halvings for 4 decimals.
IV_LOW, IV_HIGH, IV_STEPS = 0.005, 5.0, 60


# ----------------------------------------------------------------------------- prior diaries

@dataclass(frozen=True)
class DiaryPoint:
    """What one diary row says about volatility at its time; a field the row lacks is None."""
    ts: datetime
    vix: float | None
    vix_ts: float | None
    atm_iv: float | None
    em_points: float | None
    em_open: float | None


def diary_point(row: dict) -> DiaryPoint:
    ruler = row.get("range_ruler") or {}
    vix = (ruler.get("vol_carry") or {}).get("vix")

    def num(v):
        return float(v) if is_num(v) and v > 0 else None

    return DiaryPoint(datetime.fromisoformat(row["ts"]), num(vix), num(row.get("vix_ts")), num(row.get("atm_iv")),
                      num(ruler.get("em_points")), num(ruler.get("em_open")))


@lru_cache(maxsize=32)
def prior_diary(state_dir: Path, day: str) -> tuple[DiaryPoint, ...]:
    """A finished session's diary as DiaryPoints, oldest first; empty when its file is missing."""
    return tuple(diary_point(r) for r in load_rows(state_dir, day))


def point_at(points: Sequence[DiaryPoint], t: datetime) -> DiaryPoint | None:
    """The newest point stamped at or before ``t``, when it is within ROW_MAX_GAP of it: an older one
    says where things stood before a gap in the diary, not at ``t``."""
    done = [p for p in points if p.ts <= t]
    return done[-1] if done and done[-1].ts >= t - ROW_MAX_GAP else None


# ----------------------------------------------------------------------------- the tape

Contract = tuple[float, str]          # (strike, "call" | "put")


def tape_path(state_dir: Path, day: str) -> Path | None:
    folder = Path(state_dir) / TAPE_RAW_SUBDIR / day
    return next((p for p in (folder / "tape.jsonl", folder / "tape.jsonl.gz") if p.exists()), None)


def tape_minutes(state_dir: Path, day: str, minute_ends: tuple[datetime, ...],
                 clock: datetime) -> dict[datetime, dict[Contract, tuple[float, float]]] | None:
    """For each minute end ``t`` (on a minute boundary), the newest ``(bid, ask)`` per contract quoted in
    ``(t - 1 minute, t]``, from the tape as it was on file at ``clock``: nothing after ``t`` counts, and no
    line written after ``clock``. None when the day has no tape file."""
    path = tape_path(state_dir, day)
    if path is None:
        return None
    ends_ms = tuple(int(t.timestamp() * 1000) for t in minute_ends)
    got = _read_minutes(str(path), path.stat().st_mtime_ns, ends_ms, int(clock.timestamp() * 1000))
    return {t: got[ms] for t, ms in zip(minute_ends, ends_ms)}


@lru_cache(maxsize=64)
def _read_minutes(path: str, mtime_ns: int, ends_ms: tuple[int, ...], read_ms: int) -> dict[int, dict[Contract, tuple[float, float]]]:
    """``mtime_ns`` keys the cache, so a file still being written is read afresh. The first line stamped
    after ``read_ms`` was written after it, and so was every line past it."""
    wanted = set(ends_ms)
    newest: dict[int, dict[Contract, tuple[int, float, float]]] = {ms: {} for ms in ends_ms}
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.startswith(TAPE_LINE_START):
                continue
            try:
                ts = int(line[len(TAPE_LINE_START):line.index(",")])
            except ValueError:
                continue
            if ts > read_ms:
                break
            end = -(-ts // 60000) * 60000           # the minute boundary at or after the trade
            if end not in wanted:
                continue
            try:
                t = json.loads(line)
            except ValueError:
                continue
            key = (t.get("strike"), t.get("right"))
            if not is_num(key[0]) or key[1] not in ("call", "put") or not is_num(t.get("bid")) or not is_num(t.get("ask")):
                continue
            seen = newest[end].get(key)
            if seen is None or seen[0] <= ts:
                newest[end][key] = (ts, float(t["bid"]), float(t["ask"]))
    return {ms: {k: (b, a) for k, (_, b, a) in quotes.items()} for ms, quotes in newest.items()}


# ----------------------------------------------------------------------------- the skew

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1(forward: float, strike: float, iv: float, years: float) -> float:
    return (math.log(forward / strike) + 0.5 * iv * iv * years) / (iv * math.sqrt(years))


def black_price(forward: float, strike: float, iv: float, years: float, right: str) -> float:
    d1 = _d1(forward, strike, iv, years)
    d2 = d1 - iv * math.sqrt(years)
    call = forward * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return call if right == "call" else call - forward + strike


def implied_vol(price: float, forward: float, strike: float, years: float, right: str) -> float | None:
    """The volatility at which Black's formula gives ``price``; None for a price outside what any
    volatility in the search band gives."""
    lo, hi = IV_LOW, IV_HIGH
    if not black_price(forward, strike, lo, years, right) < price < black_price(forward, strike, hi, years, right):
        return None
    for _ in range(IV_STEPS):
        mid = 0.5 * (lo + hi)
        if black_price(forward, strike, mid, years, right) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _mid(quote: tuple[float, float] | None) -> float | None:
    if quote is None:
        return None
    bid, ask = quote
    mid = 0.5 * (bid + ask)
    return mid if bid > 0 and ask > bid and ask - bid <= MAX_SPREAD_SHARE * mid else None


def _interpolate(points: list[tuple[float, float]], x: float) -> float | None:
    """The value at ``x`` on the straight line between the two points either side of it; None unless
    ``x`` falls between two of them."""
    points = sorted(points)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


@dataclass(frozen=True)
class Skew:
    """The same-day smile at one minute, in implied volatility (0.155 is 15.5 vol points): at the money,
    at the 25- and 10-delta puts and the 25-delta call, and one remaining standard deviation either side
    of the forward (the move the at-the-money volatility prices for the rest of the day). A wing no
    fresh quote either side of reaches is None."""
    atm: float
    put_25: float | None
    call_25: float | None
    put_10: float | None
    put_1sd: float | None
    call_1sd: float | None


def skew_from_quotes(quotes: dict[Contract, tuple[float, float]], at: datetime) -> Skew | None:
    """The smile from one minute's quotes; None without a strike quoted on both sides to find the
    forward, or without an at-the-money volatility."""
    years = (session_close(at) - at).total_seconds() / SECONDS_PER_YEAR
    if years <= 0:
        return None
    mids = {k: m for k, q in quotes.items() if (m := _mid(q)) is not None}
    pairs = [k for (k, r) in mids if r == "call" and (k, "put") in mids]
    if not pairs:
        return None
    # the forward from parity at the strike where the call and the put are nearest in price
    k_atm = min(pairs, key=lambda k: abs(mids[(k, "call")] - mids[(k, "put")]))
    forward = k_atm + mids[(k_atm, "call")] - mids[(k_atm, "put")]
    ivs = [implied_vol(mids[(k_atm, r)], forward, k_atm, years, r) for r in ("call", "put")]
    if any(iv is None for iv in ivs):
        return None
    atm = sum(ivs) / 2.0
    wings: dict[str, list[tuple[float, float, float]]] = {"put": [], "call": []}   # (strike, |delta|, iv)
    for (k, right), mid in mids.items():
        if (right == "put" and k >= forward) or (right == "call" and k <= forward):
            continue
        iv = implied_vol(mid, forward, k, years, right)
        if iv is None:
            continue
        up = _norm_cdf(_d1(forward, k, iv, years))
        wings[right].append((k, up if right == "call" else 1.0 - up, iv))
    sd = forward * atm * math.sqrt(years)

    def at_delta(right: str, delta: float) -> float | None:
        return _interpolate([(d, iv) for _, d, iv in wings[right]], delta)

    def at_strike(right: str, strike: float) -> float | None:
        return _interpolate([(k, iv) for k, _, iv in wings[right]], strike)

    return Skew(atm, at_delta("put", 0.25), at_delta("call", 0.25), at_delta("put", 0.10),
                at_strike("put", forward - sd), at_strike("call", forward + sd))


def minute_floor(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def skew_at(state_dir: Path, day: str, ends: tuple[datetime, ...], clock: datetime) -> dict[datetime, Skew | None] | None:
    """The smile at each minute end on ``day``'s tape as it was on file at ``clock``; None when the day has no tape."""
    minutes = tape_minutes(state_dir, day, ends, clock)
    if minutes is None:
        return None
    return {t: skew_from_quotes(q, t) if q else None for t, q in minutes.items()}

