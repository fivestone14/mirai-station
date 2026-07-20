"""lefteye_speedometer.py — THE SPEEDOMETER: how fast is the tape actually
moving, right now, versus how fast it usually moves at this time of day?

Vocabulary: GRAVITY = positioning pull, FLOW = the live shove. This module is
neither — it is SPEED, the realized-volatility nowcast that tells the lens how
violent the tape underneath gravity and flow actually is. Every scan, from
today's 1-min index bars, it derives:

  rv_pace              realized variance SPENT so far vs the trailing same-
                       time-of-day norm (1.0 = normal tape, 4.0 = 2x vol)
  rod_range_pts        rest-of-day expected high-low range in index points,
                       diurnal-adjusted (not a naive sqrt-of-time stretch)
  jump_z / jump_sign   BNS jump detection (bipower variation vs RV) with the
                       direction of the single biggest 1-min move
  semivar_down_share   signed semivariance: what share of today's variance
                       came from DOWN moves (0.5 = symmetric tape)
  quality              guard label so consumers never trust a mangled feed

ADJACENCY (deliberate non-overlap): the reversion-lens telemetry already
carries range_em (realized range vs expected move) and variance_ratio — this
module deliberately does NOT re-emit a realized range or a VR. rv_pace is the
DIURNAL upgrade of that idea: "spent variance vs the norm through THIS minute
of the day", not vs a flat whole-day expected move.

Derived sensory layer, SHADOW-adjacent: pure functions, no votes, no trades.
READ-ONLY — writes nothing anywhere, touches no network. The baseline is the
saved real-range bars store (state/reversion/bars, written by gex_polarity_ab's
grader); the baseline is injectable so every path is testable offline.
Stateless: a mid-session restart just re-derives everything from today's bars.

Known limitation: the rest-of-day projection assumes a 16:00 ET close — on a
half-day (early close) it overestimates the remaining range.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent

# --- session grid ---------------------------------------------------------
# 78 five-minute buckets cover the 09:30-16:00 cash session; a bar's bucket is
# clamp((minute_of_day - 570) // 5, 0, 77). Bars outside 09:30-15:59 ET are junk
# (pre/post prints, a stray 16:00 settle bar) and get dropped.
N_BUCKETS = 78
BUCKET_MIN = 5
SESSION_OPEN_MIN = 9 * 60 + 30    # 570 = 09:30 ET
SESSION_LAST_MIN = 15 * 60 + 59   # 959 = 15:59 ET

# A return spanning more than this (halt, feed hole) is not a 1-min return —
# squaring it would fake a variance burst, so it is dropped (and counted).
MAX_GAP_S = 180.0

# --- quality guard --------------------------------------------------------
MIN_USABLE_BARS = 3     # below this there is nothing to say → read() = None
THIN_BARS = 15          # young session: same-day stats exist but jump is muted
MAX_GAP_DROPS = 5       # more holes than this → the feed itself is suspect

# --- baseline / diurnal ---------------------------------------------------
MIN_BASELINE_DAYS = 5   # fewer qualifying days than this → "cold", no norms
QUALIFYING_BARS = 300   # a baseline day must be (near) full — excludes half-days
BASELINE_FILES = 20     # newest N saved-bar files considered
SHARE_CUM_FLOOR = 0.02  # first-minutes guard: never divide by a ~0 share

# --- jump detection -------------------------------------------------------
MIN_JUMP_RETURNS = 20   # BNS asymptotics are meaningless on a handful of returns
JUMP_Z = 3.0            # z at/above this → a jump is "detected"
# BNS ratio-statistic noise constant: theta = (pi/2)^2 + pi - 5 ≈ 0.6090.
BNS_THETA = (math.pi / 2.0) ** 2 + math.pi - 5.0
# mu_{4/3} = E|N(0,1)|^{4/3} = 2^(2/3)·Γ(7/6)/Γ(1/2), the tripower-quarticity
# normalizer.
MU_43 = 2.0 ** (2.0 / 3.0) * math.gamma(7.0 / 6.0) / math.gamma(0.5)

# Rest-of-day range coefficient: for a driftless Brownian motion with total
# remaining std-dev sigma, E[high - low] = 2·sqrt(2/pi)·sigma ≈ 1.596·sigma.
RANGE_COEF = 1.596


# =========================================================================
# bar hygiene + returns
# =========================================================================
def _parse_ts(ts: str) -> datetime:
    """ISO timestamp → ET-aware datetime (naive stamps assumed already ET)."""
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt.astimezone(ET)


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _bucket(dt: datetime) -> int:
    """5-min bucket index 0..77 for an in-session ET timestamp."""
    return max(0, min(N_BUCKETS - 1, (_minute_of_day(dt) - SESSION_OPEN_MIN) // BUCKET_MIN))


def _bucket_frac(dt: datetime) -> float:
    """How far through its 5-min bucket this minute sits: 0.2 (first minute)
    … 1.0 (last minute). The baseline's cumulative tables are built on FULL
    buckets, but the live read usually lands mid-bucket — comparing a
    mid-bucket RV against a full-bucket norm understates rv_pace by up to one
    bucket of variance (~15% at 09:46, a 0→rod artifact at 15:55), so the
    norms are linearly interpolated inside the bucket instead."""
    mod = max(0, _minute_of_day(dt) - SESSION_OPEN_MIN) % BUCKET_MIN
    return (mod + 1) / BUCKET_MIN


def _through(seq: list[float], t: int, frac: float) -> float:
    """A cumulative per-bucket series read AT the frac-point inside bucket t
    (linear within the bucket). frac=1.0 reproduces seq[t] exactly."""
    lo = seq[t - 1] if t > 0 else 0.0
    return lo + frac * (seq[t] - lo)


def _usable_bars(bars: Optional[list]) -> list[tuple[datetime, float]]:
    """Hygiene pass: parse, sort + dedupe by ts (first wins), drop non-positive
    closes and anything outside the 09:30-15:59 ET session. Returns
    [(ts, close)] — the only two fields the speedometer needs."""
    rows: list[tuple[datetime, float]] = []
    for b in bars or []:
        try:
            dt = _parse_ts(str(b["ts"]))
            close = float(b["close"])
        except (KeyError, ValueError, TypeError):
            continue                                     # malformed bar → junk
        if close <= 0:
            continue                                     # dead print (log breaks)
        if not (SESSION_OPEN_MIN <= _minute_of_day(dt) <= SESSION_LAST_MIN):
            continue                                     # outside cash session
        rows.append((dt, close))
    # sort, then dedupe by ts (a re-fetched feed can repeat a minute; first wins)
    rows.sort(key=lambda x: x[0])
    out: list[tuple[datetime, float]] = []
    for dt, close in rows:
        if out and dt == out[-1][0]:
            continue
        out.append((dt, close))
    return out


def _returns_from(usable: list[tuple[datetime, float]]) -> tuple[list[tuple[int, float]], int]:
    """Consecutive log-returns off clean bars → ([(bucket, r)], gap_drops).
    A return belongs to its LATER bar's bucket (that is when the move finished);
    returns spanning a >MAX_GAP_S ts hole are dropped and counted."""
    pairs: list[tuple[int, float]] = []
    gap_drops = 0
    for (t0, c0), (t1, c1) in zip(usable, usable[1:]):
        if (t1 - t0).total_seconds() > MAX_GAP_S:
            gap_drops += 1                               # halt / feed hole
            continue
        pairs.append((_bucket(t1), math.log(c1 / c0)))
    return pairs, gap_drops


# =========================================================================
# realized measures + jump statistic
# =========================================================================
def realized_measures(rs: list[float]) -> dict:
    """Realized variance family off one day's returns:
      rv       = Σ r²                                (realized variance)
      bv       = (π/2)·(n/(n-1))·Σ|r_i||r_{i-1}|     (bipower variation —
                                                      jump-robust variance)
      tq       = n·μ_{4/3}^{-3}·(n/(n-2))·Σ(|r_i||r_{i-1}||r_{i-2}|)^{4/3}
                                                     (tripower quarticity)
      rs_down / rs_up = Σ r² over r<0 / r>0          (signed semivariances)
      n        = number of returns
    Degenerate-n guards: bv=0 below 2 returns, tq=0 below 3 (jump_z refuses
    tiny n anyway)."""
    n = len(rs)
    rv = sum(r * r for r in rs)
    ab = [abs(r) for r in rs]
    bv = 0.0
    if n >= 2:
        bv = (math.pi / 2.0) * (n / (n - 1)) * sum(ab[i] * ab[i - 1] for i in range(1, n))
    tq = 0.0
    if n >= 3:
        tq = (n * MU_43 ** -3 * (n / (n - 2))
              * sum((ab[i] * ab[i - 1] * ab[i - 2]) ** (4.0 / 3.0) for i in range(2, n)))
    rs_down = sum(r * r for r in rs if r < 0)
    rs_up = sum(r * r for r in rs if r > 0)
    return {"rv": rv, "bv": bv, "tq": tq, "rs_down": rs_down, "rs_up": rs_up, "n": n}


def jump_z(rv: float, bv: float, tq: float, n: int) -> Optional[float]:
    """Barndorff-Nielsen–Shephard ratio jump statistic (Huang–Tauchen
    max-adjusted): under a continuous path (RV-BV)/RV → 0; a jump inflates RV
    but not BV, pushing z up. z ≥ ~3 flags a jump.

        z = ((rv - bv) / rv) / sqrt(theta · (1/n) · max(1, tq / bv²))

    None when the statistic is meaningless: n < 20 or rv/bv non-positive."""
    if n < MIN_JUMP_RETURNS or rv is None or bv is None or rv <= 0 or bv <= 0:
        return None
    denom = math.sqrt(BNS_THETA * (1.0 / n) * max(1.0, tq / (bv * bv)))
    return ((rv - bv) / rv) / denom


# =========================================================================
# diurnal baseline
# =========================================================================
def diurnal_table(baseline: Optional[dict]) -> Optional[dict]:
    """Trailing days → the same-time-of-day norms:
      cum_med[t]   median (over days) of cumulative RV through bucket t —
                   the denominator of rv_pace
      share_cum[t] cumulative diurnal profile: per-bucket median of each day's
                   bucket-RV SHARE, renormalized to Σ=1, then cumsum — the
                   "how much of the day's variance is usually spent by t" curve
      days         qualifying-day count
    A day qualifies with ≥QUALIFYING_BARS usable bars (drops half-days) and
    non-zero total RV. None when fewer than MIN_BASELINE_DAYS qualify (cold)."""
    if not baseline:
        return None
    per_day_bucket_rv: list[list[float]] = []
    for _day, bars in sorted((baseline or {}).items()):
        usable = _usable_bars(bars)
        if len(usable) < QUALIFYING_BARS:
            continue                                     # half-day / broken day
        pairs, _ = _returns_from(usable)
        brv = [0.0] * N_BUCKETS
        for b, r in pairs:
            brv[b] += r * r
        if sum(brv) <= 0:
            continue                                     # flat/dead file
        per_day_bucket_rv.append(brv)
    days = len(per_day_bucket_rv)
    if days < MIN_BASELINE_DAYS:
        return None
    # cumulative RV per day, then a per-bucket median across days
    cums = []
    for brv in per_day_bucket_rv:
        acc, cum = 0.0, []
        for v in brv:
            acc += v
            cum.append(acc)
        cums.append(cum)
    cum_med = [median(c[t] for c in cums) for t in range(N_BUCKETS)]
    # diurnal profile: median of day-SHARES per bucket (share, not raw RV, so a
    # single hot day cannot warp the shape), renormalized to sum to 1
    shares = [[brv[t] / sum(brv) for t in range(N_BUCKETS)] for brv in per_day_bucket_rv]
    prof = [median(s[t] for s in shares) for t in range(N_BUCKETS)]
    tot = sum(prof)
    prof = [p / tot for p in prof] if tot > 0 else [1.0 / N_BUCKETS] * N_BUCKETS
    share_cum, acc = [], 0.0
    for p in prof:
        acc += p
        share_cum.append(acc)
    return {"cum_med": cum_med, "share_cum": share_cum, "days": days}


# =========================================================================
# baseline loader (read-only; the bars store is WRITTEN by gex_polarity_ab)
# =========================================================================
def _bars_dir() -> Path:
    """Saved real-range bars store — mirrors gex_polarity_ab._bars_dir (the
    writer); monkeypatch point for tests."""
    return SKILL_DIR.parent.parent / "state" / "reversion" / "bars"


def _read_json(path: Path):
    """Single file-read seam (tests monkeypatch this to count opens)."""
    return json.loads(path.read_text())


# (path, mtime)-keyed cache: a day-file that has not changed is never re-read.
_BASELINE_CACHE: dict[tuple[str, float], list] = {}


def _load_baseline(ticker: str = "SPX", exclude_day: str = "",
                   n_files: int = BASELINE_FILES) -> dict[str, list[dict]]:
    """Newest n_files saved {day}-{ticker}.json bar files → {day: bars}.
    exclude_day (normally today) is skipped — today's live bars must never
    grade themselves against themselves. Per-file failures are skipped, never
    raised (a corrupt file costs one baseline day, not the read)."""
    d = _bars_dir()
    suffix = f"-{ticker}.json"
    try:
        files = sorted(d.glob(f"*{suffix}"), key=lambda p: p.name, reverse=True)[:n_files]
    except OSError:
        return {}
    if len(_BASELINE_CACHE) > 256:                       # bound stale-mtime growth
        _BASELINE_CACHE.clear()
    out: dict[str, list[dict]] = {}
    for f in files:
        day = f.name[: -len(suffix)]
        if day == exclude_day:
            continue
        try:
            key = (str(f), f.stat().st_mtime)
        except OSError:
            continue
        cached = _BASELINE_CACHE.get(key)
        if cached is not None:
            out[day] = cached
            continue
        try:
            bars = _read_json(f)
        except Exception:
            continue                                     # corrupt file → skip
        if not isinstance(bars, list) or not bars:
            continue
        _BASELINE_CACHE[key] = bars
        out[day] = bars
    return out


# =========================================================================
# the read
# =========================================================================
def _r3(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 3)


def _r1(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 1)


def read(bars: list[dict], now: Optional[datetime] = None, ticker: str = "SPX",
         baseline: Optional[dict[str, list[dict]]] = None) -> Optional[dict]:
    """The speedometer read: today's 1-min bars → the 9-field telemetry dict
    (see module docstring). baseline is injectable for tests; None loads the
    saved bars store (excluding today). Returns None only when there are fewer
    than 3 usable bars — otherwise it always answers, with quality labeled."""
    now = now if now is not None else datetime.now(ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)

    usable = _usable_bars(bars)
    n_bars = len(usable)
    if n_bars < MIN_USABLE_BARS:
        return None
    pairs, gap_drops = _returns_from(usable)
    rs = [r for _, r in pairs]
    m = realized_measures(rs)
    rv = m["rv"]
    last_dt, last_close = usable[-1]
    # "now" on the session grid is the last bar we actually HAVE: RV-spent must
    # be compared to the norm through the same MINUTE (bucket + intra-bucket
    # fraction), and a stalled feed then reads as stale data rather than as a
    # fake calm-down.
    t = _bucket(last_dt)
    frac = _bucket_frac(last_dt)

    if baseline is None:
        baseline = _load_baseline(ticker, exclude_day=now.astimezone(ET).date().isoformat())
    table = diurnal_table(baseline)
    baseline_days = table["days"] if table else 0

    # quality — one label. degraded/thin are about TODAY's tape (the numbers
    # themselves are suspect: holes, or barely any sample), cold is about
    # missing HISTORY (today's numbers are fine, only the comparisons are
    # absent). A suspect number outranks a missing comparison, and a mangled
    # feed outranks a merely-young session: degraded > thin > cold > ok.
    if gap_drops > MAX_GAP_DROPS:
        quality = "degraded"
    elif n_bars < THIN_BARS:
        quality = "thin"
    elif table is None:
        quality = "cold"
    else:
        quality = "ok"

    # rv_pace — spent variance vs the same-time-of-day norm, interpolated to
    # the live minute inside the current 5-min bucket (see _bucket_frac)
    rv_pace = None
    if table is not None and rv > 0:
        cum_norm = _through(table["cum_med"], t, frac)
        if cum_norm > 0:
            rv_pace = rv / cum_norm

    # day-so-far realized sigma, in index points
    rv_day_pts = last_close * math.sqrt(rv)

    # rest-of-day range projection, diurnal-adjusted: project the day's total
    # variance from the share usually spent by now, subtract what is already
    # spent, and turn the remainder into an expected high-low range.
    # The spent share is interpolated to the live minute too — the full-bucket
    # value would claim the whole current bucket is already spent (at 15:55
    # share_cum[77]=1.0 makes the projection say the day is OVER four minutes
    # early; rod must stay a small positive number until the last bar).
    rod_range_pts = None
    if table is not None:
        proj_total = rv / max(_through(table["share_cum"], t, frac), SHARE_CUM_FLOOR)
        rv_remaining = max(0.0, proj_total - rv)
        rod_range_pts = RANGE_COEF * last_close * math.sqrt(rv_remaining)

    # jump detection — muted while thin (the n<20 rule inside jump_z would
    # refuse anyway; the mute keeps the contract explicit)
    z = None if n_bars < THIN_BARS else jump_z(m["rv"], m["bv"], m["tq"], m["n"])
    jump_sign = None
    if z is not None and z >= JUMP_Z:
        biggest = max(rs, key=abs)                       # the move that IS the jump
        jump_sign = "up" if biggest > 0 else "down"

    # signed semivariance share — what fraction of variance came from down-moves
    semivar_down_share = None
    if rv > 0 and (m["rs_down"] + m["rs_up"]) > 0:
        semivar_down_share = m["rs_down"] / (m["rs_down"] + m["rs_up"])

    return {
        "rv_pace": _r3(rv_pace),
        "rv_day_pts": _r1(rv_day_pts),
        "rod_range_pts": _r1(rod_range_pts),
        "jump_z": _r3(z),
        "jump_sign": jump_sign,
        "semivar_down_share": _r3(semivar_down_share),
        "n_bars": n_bars,
        "baseline_days": baseline_days,
        "quality": quality,
    }
