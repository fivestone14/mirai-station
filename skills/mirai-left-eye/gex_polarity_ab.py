"""
gex_polarity_ab.py — the REPORT CARDS: grades the shadow GEX engines against what
price actually did (gravity's predictions vs the tape, one card per day).

Each scan, BOTH shadow engines — gex_views (SPY-proxy GexBox) and gex_theta (native
ThetaData) — predict whether price will PIN toward their gamma magnet (long_gamma
regime) or BREAK away from it (short_gamma). This grades each prediction
TARGET-BEFORE-STOP against the day's 1-min bar highs/lows, then tallies the hit-rate
under the CURRENT sign convention (+calls / −puts) vs the INVERTED one — whichever
scores higher is the correct polarity. It also grades MAGNET GRAVITATION per engine
(closest approach of the price path to the stated magnet, in σ) — the head-to-head
that settles which engine's pin is real when the two disagree.

Read-only vs the live loop: reads state/reversion/{day}.jsonl + 1-min bars; places
no orders itself, but its promotion_gate() is THE earn-trust check consumed by
reversion_lens.live_allowed(). Persists its verdicts (state/reversion/polarity-{day}.json
+ the polarity_history.jsonl ledger) so the record accumulates instead of scrolling away.
Scheduled after close via com.mirai-station.gex-polarity; manual:
    python3 gex_polarity_ab.py [YYYY-MM-DD] [--no-write]
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, time as _time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SKILL_DIR = Path(__file__).resolve().parent
TICKERS = ("SPX",)
ENGINES = ("gex_views", "gex_theta",   # SPY-proxy GexBox · native ThetaData
           "lob_flow", "spy_depth")    # Layer-2 LOB sensor · its SPY control

PIN_TARGET_SIGMA = 0.30    # move TOWARD the magnet that confirms a pin (matches PAPER_TARGET)
BREAK_SIGMA = 0.30         # move AWAY from the magnet that confirms a break (matches PAPER_STOP)
                           # SYMMETRIC like the lens dials: unequal lines hand a random
                           # walk stop/(target+stop) free "pins" (0.25/0.35 graded a
                           # skill-free engine at 58%) while _verdict compares to 0.60
MAX_HOLD_MIN = 120         # watch up to ~2h after each snapshot
MAGNET_TAG_SIGMA = 0.05    # "tagged" = closest approach within this σ — an exact-touch rule
                           # would never fire on past-day point-bars (high == low)


def _read_rows(day: str) -> list[dict]:
    path = SKILL_DIR.parent.parent / "state" / "reversion" / f"{day}.jsonl"
    if not path.exists():
        return []
    # TORN-LINE TOLERANCE: one interrupted append must never kill the whole
    # nightly grade (or /api/replay for the day) — skip the bad line, keep the
    # rest, mirroring the intraday reader (_read_today_telemetry).
    rows = []
    for ln in open(path):
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return rows


def _bar_path(ticker: str, bars_lookup) -> list[tuple]:
    """[(bar_time_ET, high, low)] for the day, time-sorted. Degrades to [] if the bar
    source is unreachable (e.g. Schwab auth) so grading skips rather than crashes."""
    path = []
    try:
        bars = bars_lookup(ticker) or []
    except Exception:
        return []
    for b in bars:
        try:
            bt = datetime.fromisoformat(b["ts"]).astimezone(ET)
        except (ValueError, TypeError, KeyError):
            continue
        if b.get("high") is not None and b.get("low") is not None:
            path.append((bt, b["high"], b["low"]))
    path.sort(key=lambda x: x[0])
    return path


def grade_snapshot(spot, magnet, sigma, t0, path) -> str:
    """PIN-OR-BREAK JUDGE — for one call: did price reach toward the magnet or
    run away first, within the hold window?
    Returns 'pin' | 'break' | 'undecided'. 'undecided' when the magnet is <target away
    (no room to predict), a bar touches both lines, or neither line is hit in time."""
    if not (spot and magnet and sigma):
        return "undecided"
    d = 1 if magnet > spot else -1 if magnet < spot else 0
    if d == 0 or abs(magnet - spot) < PIN_TARGET_SIGMA * sigma:
        return "undecided"
    pin_lvl = spot + d * PIN_TARGET_SIGMA * sigma      # a move toward the magnet
    brk_lvl = spot - d * BREAK_SIGMA * sigma           # a move away from the magnet
    deadline = t0 + timedelta(minutes=MAX_HOLD_MIN)
    for (bt, hi, lo) in path:
        if bt < t0 or bt > deadline:
            continue
        pin_hit = hi >= pin_lvl if d > 0 else lo <= pin_lvl
        brk_hit = lo <= brk_lvl if d > 0 else hi >= brk_lvl
        if pin_hit and brk_hit:
            return "undecided"                          # same bar hit both → ambiguous
        if pin_hit:
            return "pin"
        if brk_hit:
            return "break"
    return "undecided"


def _verdict(n: int, current_hits: int) -> dict:
    cur = current_hits / n
    if cur >= 0.60:
        v = "current (+calls/-puts) CORRECT"
    elif cur <= 0.40:
        v = "INVERT the sign"
    else:
        v = "inconclusive - need more"
    return {"n": n, "hits": current_hits,      # exact hits: rounded rates drift
            "current_hit_rate": round(cur, 2),  # by a hit when pooled at scale
            "inverted_hit_rate": round(1 - cur, 2), "verdict": v}


def _bars_dir() -> Path:
    return SKILL_DIR.parent.parent / "state" / "reversion" / "bars"


def _bars_have_ranges(bars: list) -> bool:
    """True when the bars carry real high/low ranges. Flat point-bars (high==low)
    can't see a stop-out wick — grades built on them are estimates, not evidence."""
    return any((b.get("high") or 0) != (b.get("low") or 0) for b in bars)


def _persist_bars(day: str, ticker: str, bars: list) -> None:
    """Save real-range bars so any future re-grade replays actual price ranges
    instead of falling back to flat point-bars. NEVER lets a shorter set clobber
    a longer one — a midday manual grade must not freeze a partial morning over
    the full session (best-effort)."""
    try:
        d = _bars_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{day}-{ticker}.json"
        if f.exists():
            try:
                if len(json.loads(f.read_text()) or []) > len(bars):
                    return
            except (OSError, json.JSONDecodeError):
                pass
        f.write_text(json.dumps(bars))
    except (OSError, TypeError):
        pass


def _default_bars(ticker: str, day: str) -> list:
    """Bars for grading, best source first. For TODAY the live Schwab fetch wins
    (a saved midday snapshot must never shadow the rest of the session); for past
    days the SAVED real-range bars replay; ThetaData's flat underlying-price path
    is the last resort — grades from it are marked estimate-only and never feed
    the promotion gate."""
    saved: list = []
    f = _bars_dir() / f"{day}-{ticker}.json"
    try:
        if f.exists():
            saved = json.loads(f.read_text()) or []
    except (OSError, json.JSONDecodeError):
        saved = []
    today = datetime.now(ET).date().isoformat()
    if day == today:
        try:
            import lefteye_fetcher
            bars = lefteye_fetcher.intraday_bars(ticker)
            if bars and len(bars) >= len(saved):
                return bars
        except Exception:
            pass
    if saved:
        return saved
    try:
        import native_gex_feed
        return native_gex_feed.native_bars(ticker, day)
    except Exception:
        return []


# --- Wilson confidence guard (salvaged from the retired confluence engine) ----
# The promotion gate's math: an engine's graded record only earns trust once the
# confidence interval rules out "no edge". Use this when the accumulated report
# cards decide whether the gravity engine gets promoted to load-bearing.
def wilson_bounds(hits: float, misses: float, z: float = 1.645) -> tuple[float, float]:
    """(lower, upper) Wilson score interval for a hit-rate; z=1.645 → 90% CI.
    WIDE when evidence is thin, tightens as graded episodes accumulate."""
    n = hits + misses
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


def promotion_gate(min_outcomes: int = 12, promote_at: float = 0.58,
                   hist: Path | None = None,
                   record_key: str = "lens") -> tuple[bool, str]:
    """THE EARN-TRUST GATE — actually executed (the review found this math had
    zero call sites while a hand-edited switch decided live). Pools the lifetime
    graded paper record from the history ledger — REAL-RANGE (ohlc) bar days
    only, since flat point-bars can't see a stop-out — and allows live only when
    the record is big enough AND its Wilson floor rules out coin-flip luck AND
    the raw rate clears promote_at.

    COUNTS DAYS, NOT TRADES (2026-07-05 honest-stats fix): 20 same-afternoon
    fires all watched the SAME market move — counting each one inflates the
    evidence ~20×, which is exactly how a lucky afternoon fools a luck filter.
    One day = one observation: a HIT day won more trades than it lost, a MISS
    day the reverse, an even day teaches nothing. min_outcomes is therefore a
    number of DAYS (12 decided days ≈ 3-4 calendar weeks of fires).

    `record_key` picks whose record is judged: "lens" (Head A, the gates),
    "watchtower" (Head B — since 2026-07-17 read from the SWEEP card's day_hit
    bit, not the retired trade card), "break_lens" (Head C, the offense lens's
    GATED fires — the ledger live privileges ride) or "break_lens_pre_gates"
    (Head C's ungated twin — the faster ledger shadow-alert privileges
    ride). Each brain must earn its privileges on its OWN ledger.
    Consumed by reversion_lens.live_allowed(). Returns (allowed, reason)."""
    if hist is None:
        hist = SKILL_DIR.parent.parent / "state" / "reversion" / "polarity_history.jsonl"
    # One (hit?, prompt_version) per decided day. The watchtower's record is only
    # comparable WITHIN a prompt era (wt-2 and wt-3 are different forecasters), so
    # its gate counts only days from the newest era on the ledger — a version bump
    # resets its earn-trust clock instead of pooling incompatible heads.
    decided: list = []
    try:
        for ln in hist.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            kinds = (rec.get("meta") or {}).get("bars_kind") or {}
            if not kinds or all(k != "ohlc" for k in kinds.values()):
                continue                  # point-bar day: an estimate, not evidence
            if record_key == "watchtower":
                # SWEEP ERA (2026-07-17): Head B's day-bit is the sweep card's
                # day_hit — sign(Σ area_sess), permutation-null-verified ≈50%
                # under no skill on the stored real-bar days (shuffled-direction
                # null 50.2%, fixed all-day caller 5/10). Legacy trade cards
                # ("watchtower") are a different exam and are invisible here —
                # the sweep_grade_v/prompt_version era restarts the Wilson clock.
                card = rec.get("watchtower_sweep") or {}
                dh = card.get("day_hit")
                if dh in ("hit", "miss"):
                    decided.append((dh == "hit", card.get("prompt_version"),
                                    card.get("sweep_grade_v")))
                continue
            card = rec.get(record_key) or {}
            w = card.get("wins") or 0
            losses_day = max(0, (card.get("n") or 0) - w)
            if w > losses_day:
                decided.append((True, card.get("prompt_version"), card.get("grade_v")))
            elif losses_day > w:          # even days (w == losses) teach nothing
                decided.append((False, card.get("prompt_version"), card.get("grade_v")))
    except OSError:
        pass
    if record_key == "watchtower" and decided:
        era = (decided[-1][1], decided[-1][2])    # newest (prompt, sweep_grade_v) era
        decided = [d for d in decided if (d[1], d[2]) == era]
    # N18 GRADING-ERA SEGMENTATION (2026-07-12): hold-scaled barriers (grade_v=2) and
    # the old daily-σ barriers are different exams — a record must never pool both.
    # EXPLICIT era filter (verify round 3): once ANY grade_v=2 day exists, only
    # grade_v=2 days count — keying off the newest row alone let a stale-code deploy
    # append one unversioned day and silently re-pool every era (demonstrated).
    # (record_key="watchtower" never reaches this: its tuples carry sweep_grade_v.)
    if record_key != "watchtower" and any(d[2] == 2 for d in decided):
        decided = [d for d in decided if d[2] == 2]
    hit_days = sum(1 for h, _, _ in decided if h)
    miss_days = len(decided) - hit_days
    n = hit_days + miss_days
    if n < min_outcomes:
        return False, (f"paper record too thin: {n}/{min_outcomes} decided "
                       f"real-bar DAYS ({record_key})")
    rate = hit_days / n
    lo, _ = wilson_bounds(hit_days, miss_days)
    if lo <= 0.5:
        return False, (f"cannot rule out luck: Wilson floor {lo:.2f} ≤ 0.50 "
                       f"(day hit-rate {rate:.2f}, days={n})")
    if rate < promote_at:
        return False, f"day hit-rate {rate:.2f} below promote_at {promote_at:.2f} (days={n})"
    return True, (f"record clears the gate: {rate:.2f} on {n} decided days, "
                  f"Wilson floor {lo:.2f}")


def _rth_ts(r: dict) -> datetime | None:
    """Snapshot timestamp if it falls inside regular trading hours, else None."""
    try:
        t0 = datetime.fromisoformat(r["ts"]).astimezone(ET)
    except (ValueError, TypeError, KeyError):
        return None
    return t0 if _time(9, 45) <= t0.time() < _time(16, 0) else None


EPISODE_MAGNET_SIGMA = 0.10   # magnet drift below this (in σ) = still the same idea


def _episodes(tk_rows: list[dict], engine: str) -> list[dict]:
    """IDEA COUNTER — collapses repeated identical calls into one gradeable idea.
    Returns the first scan of each EPISODE: an unbroken run of scans making the SAME call
    (same pin/break direction AND magnet unmoved within EPISODE_MAGNET_SIGMA·σ).
    One idea restated every 5 minutes for an hour must grade ONCE, not twelve
    times — grading episodes removes the serial-correlation noise that made
    2026-07-02's proxy read as 0/12 when it was really one wrong idea."""
    eps: list[dict] = []
    cur = None
    for r in sorted(tk_rows, key=lambda x: x.get("ts") or ""):
        eng = r.get(engine) or {}
        regime, m, s = eng.get("regime"), eng.get("magnet"), r.get("sigma")
        pred = ("pin" if regime == "long_gamma"
                else "break" if regime == "short_gamma" else None)
        if pred is None or m is None or not s or _rth_ts(r) is None:
            cur = None                                    # gap breaks the run
            continue
        if cur and cur[0] == pred and abs(m - cur[1]) < EPISODE_MAGNET_SIGMA * s:
            continue                                      # same idea, keep waiting
        cur = (pred, m)
        eps.append(r)
    return eps


def _grade_regime(tk_rows: list[dict], engine: str, path: list[tuple]) -> tuple[int, int, int]:
    """(episodes_decided, hits, prediction_scans): pin/break tally graded per
    EPISODE, with the raw scan count kept for transparency."""
    scans = sum(1 for r in tk_rows
                if (r.get(engine) or {}).get("regime") in ("long_gamma", "short_gamma")
                and _rth_ts(r) is not None)
    n = hits = 0
    for r in _episodes(tk_rows, engine):
        eng = r[engine]
        predicted = "pin" if eng["regime"] == "long_gamma" else "break"
        actual = grade_snapshot(r.get("spot"), eng.get("magnet"), r.get("sigma"),
                                _rth_ts(r), path)
        if actual == "undecided":
            continue
        n += 1
        if predicted == actual:                                  # current polarity right
            hits += 1
    return n, hits, scans


MAGNET_MIN_SIGMA = 0.25   # a magnet closer than this to spot AT ISSUANCE is a free
                          # tag (price is already "there") — excluded from the tape-
                          # measure so a spot-hugging magnet can't grade itself perfect


def _magnet_stats(tk_rows: list[dict], engine: str, path: list[tuple],
                  magnet_key: str = "magnet") -> dict | None:
    """MAGNET TAPE-MEASURE — how close price actually came to this engine's
    stated magnet. Per RTH snapshot:
    the closest approach (in σ) of the next MAX_HOLD_MIN of bar ranges to the
    stated magnet, plus whether the magnet was tagged outright. The per-engine
    medians are the head-to-head when proxy and native magnets disagree.
    Snapshots whose magnet already sits on spot are counted as `n_hugging` and
    earn nothing — only calls with real distance are a test of pull."""
    approaches: list[float] = []
    tagged = hugging = 0
    for r in tk_rows:
        eng = r.get(engine) or {}
        magnet, sigma = eng.get(magnet_key), r.get("sigma")
        t0 = _rth_ts(r)
        if not (magnet and sigma) or t0 is None:
            continue
        spot = r.get("spot")
        if spot and abs(magnet - spot) < MAGNET_MIN_SIGMA * sigma:
            hugging += 1
            continue
        deadline = t0 + timedelta(minutes=MAX_HOLD_MIN)
        best = None
        for (bt, hi, lo) in path:
            if bt < t0 or bt > deadline:
                continue
            d = 0.0 if lo <= magnet <= hi else min(abs(hi - magnet), abs(lo - magnet))
            if best is None or d < best:
                best = d
        if best is None:
            continue
        approaches.append(best / sigma)
        if best / sigma <= MAGNET_TAG_SIGMA:
            tagged += 1
    if not approaches:
        return {"n": 0, "n_hugging": hugging} if hugging else None
    mid = sorted(approaches)[len(approaches) // 2]
    return {"n": len(approaches), "n_hugging": hugging,
            "tag_rate": round(tagged / len(approaches), 2),
            "median_approach_sigma": round(mid, 3)}


def grade_day(day: str | None = None, rows=None, bars_lookup=None) -> dict:
    """REPORT CARD — after close: did each engine's calls match what price
    actually did? Grades BOTH engines' regime predictions + magnet gravitation for `day`.
    Top-level "tickers"/"overall" keep the original gex_theta-only shape (older
    readers); the per-engine detail lives under "engines"."""
    if day is None:
        day = datetime.now(ET).date().isoformat()
    if rows is None:
        rows = _read_rows(day)
    if bars_lookup is None:
        bars_lookup = lambda tk: _default_bars(tk, day)
    # one bar fetch per ticker for the WHOLE run (regime grading + magnet stats
    # + both lens passes) — the lens grader calls bars_lookup again per pass
    _bar_cache: dict[str, list] = {}
    _raw_lookup = bars_lookup

    def bars_lookup(tk):  # noqa: F811 — memoizing shadow of the injected lookup
        if tk not in _bar_cache:
            _bar_cache[tk] = _raw_lookup(tk) or []
        return _bar_cache[tk]

    out = {"day": day, "tickers": {}, "overall": {}, "engines": {}}
    paths: dict[str, list[tuple]] = {}          # one bar fetch per ticker, shared
    for tk in TICKERS:
        if any(r.get("ticker") == tk and any(r.get(e) for e in ENGINES) for r in rows):
            paths[tk] = _bar_path(tk, bars_lookup)
    # provenance so an empty result can say WHY it is empty (no rows vs no bars),
    # plus the bar QUALITY per ticker — and save real-range bars for future re-grades
    kinds: dict[str, str] = {}
    for tk in paths:
        bars = bars_lookup(tk)
        kinds[tk] = "ohlc" if _bars_have_ranges(bars) else "point"
        if kinds[tk] == "ohlc":
            _persist_bars(day, tk, bars)
    out["meta"] = {"rows": len(rows), "bars": {tk: len(p) for tk, p in paths.items()},
                   "bars_kind": kinds}

    for engine in ENGINES:
        eng_out = {"tickers": {}, "overall": {}}
        agg_n = agg_hits = 0
        for tk in TICKERS:
            tk_rows = [r for r in rows if r.get("ticker") == tk and r.get(engine)]
            if not tk_rows:
                continue
            path = paths.get(tk) or []
            n, hits, scans = _grade_regime(tk_rows, engine, path)
            entry: dict = {}
            if n:
                entry = _verdict(n, hits)
                entry["scans"] = scans        # raw restatements behind the episodes
                agg_n += n
                agg_hits += hits
            mstats = _magnet_stats(tk_rows, engine, path)
            if mstats:
                entry["magnet"] = mstats
            # SIGNED-PIN A/B (Phase 5, shadow): the same tape-measure on the |buy−sell|
            # signed magnet, beside the live pin — which did price actually pull toward.
            # Report-card only; does NOT feed the promotion gate.
            sstats = _magnet_stats(tk_rows, engine, path, magnet_key="pin_signed")
            if sstats:
                entry["magnet_signed"] = sstats
                lv = (mstats or {}).get("median_approach_sigma")
                sv = sstats.get("median_approach_sigma")
                if lv is not None and sv is not None:
                    entry["signed_vs_live"] = ("signed" if sv < lv else
                                               "live" if lv < sv else "tie")
            # MAGNET-ZONES A/B (2026-07-09, live swap): the same tape-measure on
            # the legacy argmax magnet recorded beside the live zone-center pin —
            # the retroactive proof of which magnet definition price honored.
            astats = _magnet_stats(tk_rows, engine, path, magnet_key="pin_argmax")
            if astats:
                entry["magnet_argmax"] = astats
                lv = (mstats or {}).get("median_approach_sigma")
                av = astats.get("median_approach_sigma")
                if lv is not None and av is not None:
                    entry["zones_vs_argmax"] = ("zones" if lv < av else
                                                "argmax" if av < lv else "tie")
            profile = _pin_profile_stats(tk_rows, engine, day, tk)
            if profile:
                entry["pin_profile"] = profile      # strength + concentration
            if entry:
                eng_out["tickers"][tk] = entry
        if agg_n:
            eng_out["overall"] = _verdict(agg_n, agg_hits)
        if eng_out["tickers"]:
            out["engines"][engine] = eng_out

    # LENS REPORT CARD — grade the BRAIN's own paper fires (the fade lens),
    # deduped to fresh entries (a fire held across many scans counts ONCE) by
    # _resolve_beta_trades. Persisted via the same per-day upsert as the rest,
    # so re-running a day can never double-count a verdict.
    out["lens"] = _grade_lens(rows, bars_lookup)

    # BREAK LENS REPORT CARDS — Head C, the offense twin riding the
    # level_reclaim storage key: GRAVITY cocks it, FLOW fires it, and both
    # ledgers (gated `fired` + the `fired_pre_gates` ungated twin) grade on
    # the identical symmetric geometry. TWO top-level keys because
    # promotion_gate() cannot read nested cards — each record earns its own
    # privilege tier (gated → live, ungated → shadow-alerts). Silent layer.
    bl, bl_pre = _grade_break_lens(rows, bars_lookup)
    if bl:
        out["break_lens"] = bl
    if bl_pre:
        out["break_lens_pre_gates"] = bl_pre

    # WATCHTOWER SWEEP LEDGER (2026-07-17) — Head B's SINGLE scoreboard,
    # replacing both the Trade Ledger and the Scene Ledger: every fired call
    # graded by the SIGNED AREA between the price path and its entry, on the
    # called side. Always attached — an empty day is a well-formed zero card,
    # per the UI contract. Historical polarity_history rows keep their old
    # watchtower/watchtower_scene cards untouched (never rewritten).
    sw = _grade_watchtower_sweep(rows, bars_lookup)
    if sw is not None:
        out["watchtower_sweep"] = sw

    # N4 STANCE (fight/settle) — formerly nested inside the Trade Ledger card;
    # the grading itself is unchanged, it just rides its own top-level key now
    # that its host card is retired.
    st = _grade_stance_card(rows, bars_lookup)
    if st is not None:
        out["stance"] = st

    theta = out["engines"].get("gex_theta") or {"tickers": {}, "overall": {}}
    out["tickers"] = theta["tickers"]           # backward-compatible top level
    out["overall"] = theta["overall"]
    return out


def _pin_profile_stats(tk_rows: list[dict], engine: str,
                       day: str, tk: str) -> dict | None:
    """STRENGTH + CONCENTRATION verdict inputs: day-end pull total, median magnet
    share, and today ÷ trailing-history median (needs prior days' cards)."""
    ordered = sorted(tk_rows, key=lambda r: r.get("ts") or "")   # true end-of-day
    totals = [r[engine].get("pin_total_gamma") for r in ordered
              if r.get(engine, {}).get("pin_total_gamma")]
    shares = [r[engine].get("pin_top_share") for r in ordered
              if r.get(engine, {}).get("pin_top_share")]
    if not totals:
        return None
    out = {"eod_pin_total": round(totals[-1], 4),
           "top_share_med": round(sorted(shares)[len(shares) // 2], 4) if shares else None}
    # strength baseline from prior report cards (10 most recent other days)
    hist = SKILL_DIR.parent.parent / "state" / "reversion" / "polarity_history.jsonl"
    prior: list[float] = []
    try:
        for ln in hist.read_text().splitlines():
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("day") == day:
                continue
            v = (rec.get("engines", {}).get(engine, {}).get("tickers", {})
                    .get(tk, {}).get("pin_profile", {}).get("eod_pin_total"))
            if v:
                prior.append(float(v))
    except OSError:
        pass
    prior = prior[-10:]
    if prior:
        base = sorted(prior)[len(prior) // 2]
        if base > 0:
            out["strength_vs_hist"] = round(out["eod_pin_total"] / base, 3)
    return out


def _r_rollup(trades: list[dict]) -> dict:
    """THE COOKIE COUNT — day rollup of the outcome vector. total_r = cookies
    won/lost this day; median_r (not mean — one fat-tail trade owns a mean at
    small n); avg full-window best-run for the exit-quality diagnostic."""
    rs = [t["r"] for t in trades if t.get("r") is not None]
    mfes = [t["mfe_sigma"] for t in trades if t.get("mfe_sigma") is not None]
    return {"total_r": round(sum(rs), 2) if rs else None,
            "median_r": round(statistics.median(rs), 2) if rs else None,
            "avg_mfe_sigma": round(sum(mfes) / len(mfes), 3) if mfes else None}


def _grade_lens(rows: list[dict], bars_lookup) -> dict | None:
    """HEAD A's report card: resolve the fade lens's fresh fires by
    target-before-stop (its own grader — one idea graded once, never per scan)
    and file the verdicts so a lifetime record can accumulate. Carries the
    outcome vector (r / mfe / mae / move marks) per trade plus the day rollup,
    and pins the day's grading geometry in `params` so a future constant change
    can never silently re-price history."""
    try:
        import reversion_lens as _rl
        gated, pending = _rl._resolve_beta_trades(rows, bars_lookup=bars_lookup,
                                                  fire_key="fired")
        pre, _ = _rl._resolve_beta_trades(rows, bars_lookup=bars_lookup,
                                          fire_key="fired_pre_runway")
        def _rate(res):
            w = sum(1 for r in res if r["outcome"] == "win")
            d = w + sum(1 for r in res if r["outcome"] == "loss")
            return w, d, (round(w / d, 2) if d else None)
        w, d, rate = _rate(gated)
        pw, pd, prate = _rate(pre)
        if not gated and not pre and not pending:
            return None
        lo, hi = wilson_bounds(w, d - w) if d else (None, None)
        return {"n": d, "wins": w, "grade_v": 2,
                "scratch": sum(1 for r in gated if r["outcome"] == "scratch"),
                "pending": pending, "win_rate": rate,
                "wilson_lo": round(lo, 3) if lo is not None else None,
                "wilson_hi": round(hi, 3) if hi is not None else None,
                **_r_rollup(gated),
                "params": {"target": _rl.PAPER_TARGET_SIGMA,
                           "stop": _rl.PAPER_STOP_SIGMA,
                           "max_hold_min": _rl.PAPER_MAX_HOLD_MIN,
                           "hold_scaled": True},       # N18: barriers on the window's σ
                "pre_runway": {"n": pd, "wins": pw, "win_rate": prate},
                "trades": gated}
    except Exception:
        return None


def _grade_break_lens(rows: list[dict], bars_lookup) -> tuple[dict | None, dict | None]:
    """HEAD C's report card — the BREAK LENS (offense): level_reclaim's
    cock→fire episodes (a GRAVITY storm-side rejection cocks it, the FLOW
    gates fire it) resolved by the SAME resolver and the SAME SYMMETRIC
    0.30/0.30 exits as the fade lens — a coin flip must grade 50%, and the
    tail columns (r / mfe_sigma / move_30-60-120m) carry the runners the
    clipped exit can't see. Grades BOTH ledgers:
        fired            — the gated fire (trending + storm-side + one-way
                           tape + runway), the record live privileges ride
        fired_pre_gates  — the ungated twin, the faster record shadow-alert
                           privileges ride (~0-1 gated fires/day means the
                           gated ledger alone would take months to speak)
    Returns (break_lens, break_lens_pre_gates) as SEPARATE top-level cards —
    promotion_gate() reads only top-level history keys, so the ungated
    record must own its own key. Fully silent layer: no alert path reads
    these cards; they exist so the record accumulates."""
    try:
        import reversion_lens as _rl
        # Only rows that actually CARRY break state grade here: a scan whose
        # break block fail-opened leaves the LEGACY level_reclaim dict on the
        # row (no break fields, `fired` = the old 1.3× volume-pop verdict) —
        # ungraded it would inject a false gated Break fire into the ledger
        # live privileges ride. The diary row itself stays untouched.
        rows = [r for r in rows if "break_state" in (r.get("level_reclaim") or {})]
        gated, pending = _rl._resolve_beta_trades(rows, bars_lookup=bars_lookup,
                                                  signal_key="level_reclaim",
                                                  fire_key="fired")
        pre, pre_pending = _rl._resolve_beta_trades(rows, bars_lookup=bars_lookup,
                                                    signal_key="level_reclaim",
                                                    fire_key="fired_pre_gates")

        def _n_dropped_early(fire_key: str) -> int:
            # break fires are ONE-SHOT (True on exactly one row), so a fire
            # outside the resolver's 09:45-16:00 entry window is skipped once
            # and never re-enters (a stays-lit fade would). Counted here so
            # gap-day opens — where storm-side breaks cluster — can't vanish
            # from the evidence base invisibly.
            n = 0
            for r in rows:
                if r.get("ticker") not in TICKERS:
                    continue
                if not (r.get("level_reclaim") or {}).get(fire_key):
                    continue
                try:
                    t0 = datetime.fromisoformat(r["ts"]).astimezone(ET)
                except (KeyError, ValueError, TypeError):
                    continue
                if not (_time(9, 45) <= t0.time() < _time(16, 0)):
                    n += 1
            return n

        n_drop_gated = _n_dropped_early("fired")
        n_drop_pre = _n_dropped_early("fired_pre_gates")
        if (not gated and not pre and not pending and not pre_pending
                and not n_drop_gated and not n_drop_pre):
            return None, None

        def _summary(res, pend):
            w = sum(1 for t in res if t["outcome"] == "win")
            d = w + sum(1 for t in res if t["outcome"] == "loss")
            lo, hi = wilson_bounds(w, d - w) if d else (None, None)
            return {"n": d, "wins": w,
                    "scratch": sum(1 for t in res if t["outcome"] == "scratch"),
                    "pending": pend,
                    "win_rate": round(w / d, 2) if d else None,
                    "wilson_lo": round(lo, 3) if lo is not None else None,
                    "wilson_hi": round(hi, 3) if hi is not None else None,
                    **_r_rollup(res)}

        pre_sum = _summary(pre, pre_pending)
        pre_sum["n_dropped_early"] = n_drop_pre
        # cocks-per-zone visibility (cap-leak audit): the per-level cap keys on
        # the CURRENT anchor's 5-pt bucket, so an anchor retag or a drifting
        # gamma wall can mint extra 2-cock budgets at one physical zone —
        # counting distinct cocks per bucket here makes any leak visible on
        # the nightly card (the cap behavior itself is locked, unchanged).
        cocks: dict[str, float | None] = {}
        for r in rows:
            lr = r.get("level_reclaim") or {}
            ca = lr.get("cocked_at")
            if ca and ca not in cocks:
                cocks[ca] = lr.get("cock_level")
        step = getattr(_rl, "BREAK_LEVEL_STEP", 5.0)
        cocks_by_level: dict[str, int] = {}
        for lv in cocks.values():
            try:
                b = f"{round(float(lv) / step) * step:g}"
            except (TypeError, ValueError):
                continue    # one junk cock_level skips its bucket — it must not
                            # cost the whole day's card (whole-function fail-open)
            cocks_by_level[b] = cocks_by_level.get(b, 0) + 1
        # pin the day's fire-gate dials beside the exit geometry so a future
        # constant change can never silently re-price history (getattr keeps
        # the grader alive if the lens's BREAK_* dials land in a later deploy;
        # fallbacks = the locked-plan values)
        full = {**_summary(gated, pending), "grade_v": 2,
                "n_dropped_early": n_drop_gated,
                "params": {"target": _rl.PAPER_TARGET_SIGMA,
                           "stop": _rl.PAPER_STOP_SIGMA,
                           "max_hold_min": _rl.PAPER_MAX_HOLD_MIN,
                           "cock_expire_min": getattr(_rl, "BREAK_COCK_EXPIRE_MIN", 45.0),
                           "tape_min": getattr(_rl, "BREAK_TAPE_MIN", 0.15),
                           "runway_min_sigma": getattr(_rl, "BREAK_RUNWAY_MIN_SIGMA", 0.35)},
                "pre_gates": {"n": pre_sum["n"], "wins": pre_sum["wins"],
                              "win_rate": pre_sum["win_rate"]},
                "trades": gated}
        if cocks_by_level:
            full["cocks_by_level"] = cocks_by_level
        thin = ({**pre_sum, "grade_v": 2, "trades": pre}
                if (pre or pre_pending or n_drop_pre) else None)
        return full, thin
    except Exception:
        return None, None


STANCE_BAND_SIGMA = 0.30   # fight/settle band, in horizon-scaled σ (same 0.30 grammar
                           # as the trade barriers — one number, one meaning)


def _grade_stance(judged: list[dict], bars_lookup) -> dict | None:
    """N4 — grade the fight/settle answer. For each judged row with a stance, walk the
    1-min bars over the row's horizon: the max excursion EITHER way from entry (in σ)
    against the hold-scaled band STANCE_BAND_SIGMA·√(horizon/390). Eruption ⇒ fight was
    right; containment (with a complete window) ⇒ settle was right. no_read abstains.
    Returns {n, fight: {n, wins}, settle: {n, wins}, acc} | None."""
    import math as _math
    from datetime import time as _time
    scored = {"fight": [0, 0], "settle": [0, 0]}      # stance → [n, wins]
    path = []
    for b in bars_lookup("SPX") or []:
        try:
            bt = datetime.fromisoformat(b["ts"]).astimezone(ET)
        except (ValueError, TypeError, KeyError):
            continue
        if b.get("high") is not None and b.get("low") is not None:
            path.append((bt, b["high"], b["low"]))
    if not path:
        return None
    path.sort(key=lambda x: x[0])
    for r in judged:
        wt = r.get("watchtower") or {}
        stance = wt.get("stance")
        if stance not in ("fight", "settle"):
            continue
        P, sigma = r.get("spot"), r.get("sigma")
        if not (isinstance(P, (int, float)) and isinstance(sigma, (int, float)) and sigma):
            continue
        try:
            t0 = datetime.fromisoformat(r["ts"]).astimezone(ET)
        except (KeyError, ValueError, TypeError):
            continue
        horizon = wt.get("horizon_min") or 60
        band = STANCE_BAND_SIGMA * _math.sqrt(max(horizon, 5) / 390.0)
        exc, last_in = 0.0, None
        for (bt, hi, lo) in path:
            if bt <= t0:
                continue
            el = (bt - t0).total_seconds() / 60.0
            if el > horizon or bt.time() >= _time(16, 0):
                break
            last_in = el
            exc = max(exc, abs(hi - P) / sigma, abs(P - lo) / sigma)
        erupted = exc >= band
        if not erupted and (last_in is None or last_in < min(horizon, 385) - 15):
            continue                                   # incomplete window: no verdict
        scored[stance][0] += 1
        if (stance == "fight") == erupted:
            scored[stance][1] += 1
    n = scored["fight"][0] + scored["settle"][0]
    if not n:
        return None
    wins = scored["fight"][1] + scored["settle"][1]
    return {"n": n, "acc": round(wins / n, 2),
            "band_sigma": STANCE_BAND_SIGMA,
            "fight": {"n": scored["fight"][0], "wins": scored["fight"][1]},
            "settle": {"n": scored["settle"][0], "wins": scored["settle"][1]}}


def _grade_stance_card(rows: list[dict], bars_lookup) -> dict | None:
    """The judged-row filter + fail-open wrapper the retired Trade Ledger used to
    provide around _grade_stance — the stance card must survive its host card's
    deletion (2026-07-17 sweep replacement). Grading itself is unchanged."""
    try:
        judged = [r for r in rows
                  if isinstance(r.get("watchtower"), dict)
                  and r["watchtower"].get("fired") is not None]
        return _grade_stance(judged, bars_lookup) if judged else None
    except Exception:
        return None


SWEEP_STOP_SIGMA = 0.20        # the tradability dial: mfe_before_stop freezes at the
                               # first slice at/under −0.20σ (a realistic option stop)
SWEEP_SESSION_BARS = 390       # area denominator: one full RTH session of 1-min slices,
                               # so area_sess reads as σ·session-fraction
SWEEP_BARS_COMPLETE_MIN = 15   # bars must reach within this many minutes of a call's
                               # close to grade it (else PENDING — the resolver's rule)


def _grade_watchtower_sweep(rows: list[dict], bars_lookup) -> dict | None:
    """HEAD B's SWEEP LEDGER (2026-07-17) — THE single scoreboard, replacing both
    the Trade Ledger (fixed-barrier trades) and the Scene Ledger (scene-scoped
    episodes). Grades each fired call by the SIGNED AREA between the price path
    and the entry on the called side — "like integrals in calculus": per 1-min
    bar the signed displacement s_i = dir·(close_i − entry)/σ, and
    area_sess = Σ s_i / 390. Time-slicing on bar CLOSES is a Riemann sum on the
    close path (OHLC ranges are not consulted) and is provably identical to the
    price-slice picture by the layer-cake identity.

    CALL CONSTRUCTION (deliberately NO scene_change closure — scene-keyed closes
    were the fragmentation bug the Scene Ledger died of):
      * a fired call OPENS a sweep call at (t0, entry=row.spot, σ=row.sigma, dir)
      * consecutive fired calls in the SAME direction FOLD IN (n_reaffirms) —
        a restated thesis is never a new trial
      * a fired OPPOSITE call CLOSES the open call ('dir_flip') and opens its own
      * otherwise the call runs to 16:00 ET ('session_close'); bars short of the
        close → 'pending' (fields computed over available bars, t_close null)

    VERDICT (pre-registered, area sign + duration majority): 'win' when
    area_sess > 0 AND dur_frac ≥ 0.5; 'loss' when area_sess < 0 AND
    dur_frac ≤ 0.5; else 'mixed' — area and duration disagree, reported but
    excluded from wins/losses. Day hit-bit = sign(Σ area_sess over the day's
    closed calls) — permutation-null-tested 2026-07-17 on the 10 stored real-bar
    days: shuffled-direction day-hit 50.2%, fixed all-day call 5/10 — a clean
    Bernoulli for the promotion gate. Empty day → a well-formed zero card."""
    try:
        issuances: list[tuple] = []      # (t0, ts, spot, sigma, dir), in-window
        versions: list[str] = []
        n_dropped = 0
        for r in rows:
            if r.get("ticker") not in TICKERS:
                continue
            wtd = r.get("watchtower")
            if not isinstance(wtd, dict) or wtd.get("fired") is None:
                continue
            if wtd.get("prompt_version"):
                versions.append(wtd["prompt_version"])
            if not wtd.get("fired") or wtd.get("direction") not in ("call", "put"):
                continue                 # the universe is FIRED calls only
            try:
                t0 = datetime.fromisoformat(r["ts"]).astimezone(ET)
            except (KeyError, ValueError, TypeError):
                continue
            if not (_time(9, 45) <= t0.time() < _time(16, 0)):
                n_dropped += 1           # visible-drop accounting, never silent
                continue
            if not (r.get("spot") and r.get("sigma")):
                continue                 # no entry ruler — ungradeable issuance
            issuances.append((t0, r["ts"], r["spot"], r["sigma"], wtd["direction"]))
        issuances.sort(key=lambda c: c[0])

        # fold reaffirms / split on flips into sweep calls
        grouped: list[dict] = []
        for (t0, ts, spot, sigma, d) in issuances:
            cur = grouped[-1] if grouped else None
            if cur is not None and cur["t_flip"] is None:
                if cur["dir"] == d:
                    cur["n_reaffirms"] += 1          # same thesis, same trial
                    continue
                cur["t_flip"] = t0                   # opposite call closes it...
            grouped.append({"t0": t0, "ts": ts, "entry": spot, "sigma": sigma,
                            "dir": d, "n_reaffirms": 0, "t_flip": None})
                                                     # ...and opens its own

        # 1-min close path (close falls back to the hi/lo midpoint on synthetic bars)
        path: list[tuple] = []
        if grouped:
            for b in bars_lookup(TICKERS[0]) or []:
                try:
                    bt = datetime.fromisoformat(b["ts"]).astimezone(ET)
                except (ValueError, TypeError, KeyError):
                    continue
                px = b.get("close")
                if px is None and b.get("high") is not None and b.get("low") is not None:
                    px = (b["high"] + b["low"]) / 2.0
                if px is not None:
                    path.append((bt, px))
            path.sort(key=lambda x: x[0])
        path_end = path[-1][0] if path else None

        calls: list[dict] = []
        for g in grouped:
            t0, entry, sigma = g["t0"], g["entry"], g["sigma"]
            sgn = 1 if g["dir"] == "call" else -1
            session_close = datetime.combine(t0.date(), _time(16, 0), tzinfo=ET)
            close_t, reason = ((g["t_flip"], "dir_flip") if g["t_flip"] is not None
                               else (session_close, "session_close"))
            s_sum, n_bars, bwin, blose = 0.0, 0, 0, 0
            smax = smin = None
            run_max, mfe_bs, stopped = 0.0, None, False
            for (bt, px) in path:
                if bt <= t0:
                    continue
                if bt > close_t:
                    break
                s = sgn * (px - entry) / sigma
                s_sum += s
                n_bars += 1
                if s > 0:
                    bwin += 1
                elif s < 0:
                    blose += 1
                smax = s if smax is None or s > smax else smax
                smin = s if smin is None or s < smin else smin
                if not stopped:
                    if s <= -SWEEP_STOP_SIGMA:       # MAE crossed the stop line:
                        stopped = True               # freeze the tradability peak
                        mfe_bs = run_max
                    elif s > run_max:
                        run_max = s
            mfe = max(0.0, smax) if smax is not None else 0.0
            mae = min(0.0, smin) if smin is not None else 0.0
            if not stopped:
                mfe_bs = mfe                         # never stopped: the full peak
            decided_bars = bwin + blose
            dur_frac = bwin / decided_bars if decided_bars else None
            area = s_sum / SWEEP_SESSION_BARS
            # pending = the bar path doesn't reach this call's close yet (today,
            # incomplete). A dir_flip close mid-session grades as soon as bars
            # cover it — no waiting for the nightly run.
            pend = (path_end is None
                    or (close_t - path_end).total_seconds() / 60.0 > SWEEP_BARS_COMPLETE_MIN)
            if pend:
                verdict = "pending"
            elif area > 0 and dur_frac is not None and dur_frac >= 0.5:
                verdict = "win"
            elif area < 0 and dur_frac is not None and dur_frac <= 0.5:
                verdict = "loss"
            else:
                verdict = "mixed"                    # area and duration disagree
            calls.append({"t_open": g["ts"],
                          "t_close": None if pend else close_t.isoformat(),
                          "close_reason": None if pend else reason,
                          "dir": g["dir"], "entry": round(entry, 2),
                          "sigma": round(sigma, 4),
                          "area_sess": round(area, 5),
                          "mean_fav_sigma": round(s_sum / n_bars, 4) if n_bars else None,
                          "bars_win": bwin, "bars_lose": blose,
                          "dur_frac": round(dur_frac, 3) if dur_frac is not None else None,
                          "mfe_sigma": round(mfe, 3), "mae_sigma": round(mae, 3),
                          "mfe_before_stop": round(mfe_bs, 3),
                          "n_reaffirms": g["n_reaffirms"], "verdict": verdict})

        closed = [c for c in calls if c["verdict"] != "pending"]
        wins = sum(1 for c in closed if c["verdict"] == "win")
        losses = sum(1 for c in closed if c["verdict"] == "loss")
        mixed = sum(1 for c in closed if c["verdict"] == "mixed")
        net = round(sum(c["area_sess"] for c in closed), 5) if closed else None
        day_hit = (None if net is None or net == 0
                   else "hit" if net > 0 else "miss")
        means = [c["mean_fav_sigma"] for c in closed if c["mean_fav_sigma"] is not None]
        durs = [c["dur_frac"] for c in closed if c["dur_frac"] is not None]
        mfes = [c["mfe_sigma"] for c in closed]
        pv = max(set(versions), key=versions.count) if versions else None
        return {"sweep_grade_v": 1, "prompt_version": pv,
                "n_calls": len(calls), "wins": wins, "losses": losses,
                "mixed": mixed, "pending": len(calls) - len(closed),
                "day_hit": day_hit, "net_area_sess": net,
                "med_mean_fav_sigma": round(statistics.median(means), 4) if means else None,
                "med_dur_frac": round(statistics.median(durs), 3) if durs else None,
                "med_mfe_sigma": round(statistics.median(mfes), 3) if mfes else None,
                "n_dropped_early": n_dropped,
                "params": {"stop_sigma": SWEEP_STOP_SIGMA,
                           "verdict": "area sign + dur_frac majority",
                           "closure": "dir_flip|session_close"},
                "calls": calls}
    except Exception:
        return None


def persist(res: dict) -> Path:
    """FILING CABINET — saves the report cards so the record accumulates instead
    of scrolling away: writes the day report + upserts the one-line-per-day
    history ledger."""
    d = SKILL_DIR.parent.parent / "state" / "reversion"
    d.mkdir(parents=True, exist_ok=True)
    day_file = d / f"polarity-{res['day']}.json"
    day_file.write_text(json.dumps(res, indent=2) + "\n")
    hist = d / "polarity_history.jsonl"
    lines: list[str] = []
    if hist.exists():
        for ln in hist.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                if json.loads(ln).get("day") != res["day"]:
                    lines.append(ln)
            except json.JSONDecodeError:
                continue
    lines.append(json.dumps(res))
    # ATOMIC upsert: a crash mid-write must not lose the lifetime ledger the
    # promotion gate depends on — temp file + os.replace, same guarantee as
    # atomic_io.write_json_atomic (which is JSON-object-only, hence inline).
    tmp = hist.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, hist)
    return day_file


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    day = next((a for a in args if not a.startswith("-")), None)
    res = grade_day(day)
    # only persist a report card that actually graded something — a premarket or
    # bars-outage run must not leave a blank file that reads as "engines failed"
    if "--no-write" not in args and res.get("engines"):
        persist(res)
    _LABEL = {"gex_views": "gex_views (SPY-proxy)", "gex_theta": "gex_theta (native)",
              "lob_flow": "lob_flow (Layer-2 book sensor)",
              "spy_depth": "spy_depth (its SPY control)"}
    print(f"GEX dealer-sign polarity + magnet A/B — {res['day']}\n")
    for eng, er in res.get("engines", {}).items():
        print(f"  {_LABEL.get(eng, eng)}")
        for tk, s in er.get("tickers", {}).items():
            if s.get("n"):
                scans = f" (from {s['scans']} scans)" if s.get("scans") else ""
                print(f"    {tk}: episodes={s['n']}{scans}  current {s['current_hit_rate']:.0%} "
                      f"vs inverted {s['inverted_hit_rate']:.0%}  -> {s['verdict']}")
            m = s.get("magnet")
            if m and m.get("n"):
                hug = f"  ({m['n_hugging']} hugging, excluded)" if m.get("n_hugging") else ""
                print(f"    {tk} magnet: n={m['n']}  tagged {m['tag_rate']:.0%}  "
                      f"median approach {m['median_approach_sigma']}σ{hug}")
            elif m:
                print(f"    {tk} magnet: 0 gradeable — all {m.get('n_hugging', 0)} "
                      "snapshots hugged spot (<0.25σ)")
        o = er.get("overall")
        if o:
            print(f"    OVERALL: n={o['n']}  current {o['current_hit_rate']:.0%} "
                  f"vs inverted {o['inverted_hit_rate']:.0%}  -> {o['verdict']}")
        print()
    lens = res.get("lens")
    if lens:
        wl = (f"  Wilson {lens['wilson_lo']}-{lens['wilson_hi']}"
              if lens.get("wilson_lo") is not None else "")
        rr = (f"  · total_r {lens['total_r']:+.2f} (median {lens['median_r']:+.2f})"
              if lens.get("total_r") is not None else "")
        print(f"  HEAD A — FADE LENS (the gates): {lens['wins']}/{lens['n']} wins "
              f"(rate {lens['win_rate']}){wl}{rr}  · {lens['scratch']} scratch "
              f"· {lens['pending']} pending  · pre-runway "
              f"{lens['pre_runway']['wins']}/{lens['pre_runway']['n']}\n")
    bl = res.get("break_lens")
    if bl:
        wl = (f"  Wilson {bl['wilson_lo']}-{bl['wilson_hi']}"
              if bl.get("wilson_lo") is not None else "")
        rr = (f"  · total_r {bl['total_r']:+.2f} (median {bl['median_r']:+.2f})"
              if bl.get("total_r") is not None else "")
        print(f"  HEAD C — BREAK LENS (offense, gated): {bl['wins']}/{bl['n']} wins "
              f"(rate {bl['win_rate']}){wl}{rr}  · {bl['scratch']} scratch "
              f"· {bl['pending']} pending  · pre-gates "
              f"{bl['pre_gates']['wins']}/{bl['pre_gates']['n']}")
        blp = res.get("break_lens_pre_gates")
        if blp:
            pwl = (f"  Wilson {blp['wilson_lo']}-{blp['wilson_hi']}"
                   if blp.get("wilson_lo") is not None else "")
            print(f"    ungated twin (shadow-alert ledger): {blp['wins']}/{blp['n']} "
                  f"wins (rate {blp['win_rate']}){pwl}  · {blp['scratch']} scratch "
                  f"· {blp['pending']} pending")
        print()
    sw = res.get("watchtower_sweep")
    if sw and (sw["n_calls"] or sw["n_dropped_early"]):
        net = (f"{sw['net_area_sess']:+.5f}" if sw.get("net_area_sess") is not None
               else "—")
        print(f"  HEAD B — SWEEP LEDGER: {sw['wins']}W/{sw['losses']}L/{sw['mixed']}X "
              f"of {sw['n_calls']} calls  · day {sw['day_hit'] or 'undecided'} "
              f"(net area {net}σ·sess)  · {sw['pending']} pending "
              f"· {sw['n_dropped_early']} dropped-early")
        for c in sw["calls"]:
            print(f"    {c['t_open'][11:16]} {c['dir'].upper():4} entry {c['entry']}  "
                  f"area {c['area_sess']:+.5f}  dur {c['dur_frac']}  "
                  f"mfe {c['mfe_sigma']} (pre-stop {c['mfe_before_stop']})  "
                  f"mae {c['mae_sigma']}  +{c['n_reaffirms']} reaffirms  "
                  f"-> {c['verdict']} ({c['close_reason'] or 'open'})")
        print()
    st = res.get("stance")
    if st:
        print(f"  HEAD B — STANCE: {st['n']} judged  acc {st['acc']}  "
              f"fight {st['fight']['wins']}/{st['fight']['n']}  "
              f"settle {st['settle']['wins']}/{st['settle']['n']}\n")
    if not res.get("engines"):
        meta = res.get("meta") or {}
        if not meta.get("rows"):
            print("  (no telemetry recorded for this day — nothing to grade)")
        elif not any((meta.get("bars") or {}).values()):
            print(f"  ({meta['rows']} telemetry rows present but NO price bars could be fetched —\n"
                  "   grading skipped, nothing persisted. Check Schwab auth / ThetaData token\n"
                  "   (an overnight run can hit a locked keychain).)")
        else:
            print("  (telemetry + bars present but no gradeable calls — nothing persisted)")
