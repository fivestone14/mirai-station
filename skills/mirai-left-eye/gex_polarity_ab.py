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
    "watchtower" (Head B), "break_lens" (Head C, the offense lens's GATED
    fires — the ledger live privileges ride) or "break_lens_pre_gates"
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
            card = rec.get(record_key) or {}
            w = card.get("wins") or 0
            losses_day = max(0, (card.get("n") or 0) - w)
            if w > losses_day:
                decided.append((True, card.get("prompt_version"), card.get("grade_v")))
            elif losses_day > w:          # even days (w == losses) teach nothing
                decided.append((False, card.get("prompt_version"), card.get("grade_v")))
    except OSError:
        pass
    if record_key == "watchtower" and decided and decided[-1][1] is not None:
        era = decided[-1][1]              # newest decided day's era
        decided = [d for d in decided if d[1] == era]
    # N18 GRADING-ERA SEGMENTATION (2026-07-12): hold-scaled barriers (grade_v=2) and
    # the old daily-σ barriers are different exams — a record must never pool both.
    # EXPLICIT era filter (verify round 3): once ANY grade_v=2 day exists, only
    # grade_v=2 days count — keying off the newest row alone let a stale-code deploy
    # append one unversioned day and silently re-pool every era (demonstrated).
    if any(d[2] == 2 for d in decided):
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

    # WATCHTOWER TRADE LEDGER — Head B graded on the same rules, plus the
    # disagreement split (only present on days the tower actually judged).
    wt = _grade_watchtower(rows, bars_lookup)
    if wt:
        out["watchtower"] = wt

    # WATCHTOWER SCENE LEDGER (N20) — the SECOND scoreboard beside the Trade
    # Ledger: the same calls graded as SCENE-SCOPED EPISODES (was the call right
    # for as long as its scene lasted?). Always attached — an empty day is a
    # well-formed zero card, per the UI contract.
    ws = _grade_watchtower_scene(rows, bars_lookup)
    if ws is not None:
        out["watchtower_scene"] = ws

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


def _is_solo(interest) -> bool:
    """A SOLO wake = the tower spoke on a scan the gates never treated as a
    candidate (hourly heartbeat / big-change interrupt); a CONTESTED wake = the
    gates were engaged (gates active / stretched tape / wall pressed). Segmenting
    on this keeps the tower-vs-gates head-to-head honest now that the hybrid wake
    (2026-07-07) judges quiet scans too."""
    return isinstance(interest, str) and (interest == "hourly heartbeat"
                                          or interest.startswith("shift"))


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


def _grade_watchtower(rows: list[dict], bars_lookup) -> dict | None:
    """HEAD B's TRADE LEDGER — the Watchtower graded on EXACTLY the rules Head A
    is graded on (same resolver, same target/stop/hold), plus the piece that
    answers the whole experiment: the DISAGREEMENT split.

    Per judged scan the two heads either agree or split four ways:
        both_fired   — both said trade      (agreement, graded under each head)
        tower_only   — tower fired, gates passed  ← the tower's edge, if any
        gates_vetoed — gates fired, tower said no ← the tower's veto, if any
        both_passed  — both stood down      (agreement, nothing to grade)
    tower_only trades are graded here; gates_vetoed fires are the GATES' trades
    the tower would have skipped — their win rate tells whether the veto helps
    (a good veto skips losers: LOW win rate in that bucket is a POINT FOR the
    tower)."""
    try:
        import reversion_lens as _rl
        judged = [r for r in rows
                  if isinstance(r.get("watchtower"), dict)
                  and r["watchtower"].get("fired") is not None]
        if not judged:
            return None
        tower, pending = _rl._resolve_beta_trades(rows, bars_lookup=bars_lookup,
                                                  signal_key="watchtower")
        # scan-level agreement tallies (only scans the tower actually judged)
        tally = {"both_fired": 0, "tower_only": 0, "gates_vetoed": 0, "both_passed": 0}
        verdict_at = {}                      # ts → (tower_fired, gates_fired)
        for r in judged:
            tf = bool(r["watchtower"].get("fired"))
            gf = bool((r.get("reversion_extreme") or {}).get("fired"))
            verdict_at[r.get("ts")] = (tf, gf)
            key = ("both_fired" if tf and gf else "tower_only" if tf
                   else "gates_vetoed" if gf else "both_passed")
            tally[key] += 1
        skipped = sum(1 for r in rows if isinstance(r.get("watchtower"), dict)
                      and r["watchtower"].get("skipped"))
        # grade the disagreement buckets
        tower_only_trades = [t for t in tower
                             if verdict_at.get(t["ts"], (False, False))[1] is False]
        gates_trades, _ = _rl._resolve_beta_trades(rows, bars_lookup=bars_lookup,
                                                   fire_key="fired")
        vetoed_trades = [t for t in gates_trades
                         if verdict_at.get(t["ts"], (True, True))[0] is False]
        def _bucket(res):
            w = sum(1 for t in res if t["outcome"] == "win")
            d = w + sum(1 for t in res if t["outcome"] == "loss")
            return {"n": d, "wins": w,
                    "win_rate": round(w / d, 2) if d else None,
                    **_r_rollup(res)}
        # RECORD what-happened vs the tower's INFERENCE: stamp each graded trade
        # with the tower's own read (prediction + scene + why-it-woke) so the row
        # shows what it thought against the outcome the resolver measured.
        info_at = {r.get("ts"): r["watchtower"] for r in judged}
        for t in tower:
            wtr = info_at.get(t["ts"]) or {}
            t["interest"] = wtr.get("interest")
            t["scene"] = wtr.get("scene")
            t["pred_magnitude_sigma"] = wtr.get("magnitude_sigma")
            t["conviction"] = wtr.get("conviction")
            t["would_change_mind"] = wtr.get("would_change_mind")
            t["population"] = "solo" if _is_solo(wtr.get("interest")) else "contested"

        # GRADE SEPARATELY by wake population so the blended hybrid-wake record
        # doesn't misread as one head-to-head (see _is_solo).
        def _seg(res):
            ww = sum(1 for t in res if t["outcome"] == "win")
            dd = ww + sum(1 for t in res if t["outcome"] == "loss")
            slo, shi = wilson_bounds(ww, dd - ww) if dd else (None, None)
            return {"n": dd, "wins": ww,
                    "scratch": sum(1 for t in res if t["outcome"] == "scratch"),
                    "win_rate": round(ww / dd, 2) if dd else None,
                    "wilson_lo": round(slo, 3) if slo is not None else None,
                    "wilson_hi": round(shi, 3) if shi is not None else None,
                    **_r_rollup(res), "trades": res}
        contested = [t for t in tower if t["population"] == "contested"]
        solo = [t for t in tower if t["population"] == "solo"]
        solo_judged = sum(1 for r in judged
                          if _is_solo((r["watchtower"] or {}).get("interest")))

        # N4 STANCE GRADING (2026-07-12): the prison-yard axis, finally graded. Per
        # judged row carrying a stance: did the range actually ERUPT (max excursion
        # either way over the row's horizon ≥ the hold-scaled band) or SETTLE inside
        # it? fight is right on eruption, settle on containment; no_read is excluded.
        stance_card = None
        try:
            stance_card = _grade_stance(judged, bars_lookup)
        except Exception:
            stance_card = None

        w = sum(1 for t in tower if t["outcome"] == "win")
        d = w + sum(1 for t in tower if t["outcome"] == "loss")
        lo, hi = wilson_bounds(w, d - w) if d else (None, None)
        # PROMPT-ERA STAMP (07-09): "a bump resets the record" only works if the
        # record knows which prompt produced each day — stamp the era so
        # _scorecards / any future promotion filter can segment wt-1 from wt-2+.
        versions = [r["watchtower"].get("prompt_version") for r in judged
                    if r["watchtower"].get("prompt_version")]
        pv = max(set(versions), key=versions.count) if versions else None
        return {"n": d, "wins": w, "prompt_version": pv, "grade_v": 2,
                **({"stance": stance_card} if stance_card else {}),
                "scratch": sum(1 for t in tower if t["outcome"] == "scratch"),
                "pending": pending,
                "win_rate": round(w / d, 2) if d else None,
                "wilson_lo": round(lo, 3) if lo is not None else None,
                "wilson_hi": round(hi, 3) if hi is not None else None,
                **_r_rollup(tower),
                "scans": {"judged": len(judged), "skipped": skipped,
                          "contested": len(judged) - solo_judged, "solo": solo_judged,
                          **tally},
                "disagree": {"tower_only": _bucket(tower_only_trades),
                             "gates_vetoed": _bucket(vetoed_trades)},
                "by_interest": {"contested": _seg(contested), "solo": _seg(solo)},
                "trades": tower}
    except Exception:
        return None


SCENE_NEVER_MOVED_FLOOR_SIGMA = 0.10  # favorable MFE below this = the call never
                                      # moved at all → LOSS, however short the scene
SCENE_FALLBACK_TARGET_SIGMA = 0.30    # called magnitude when the tower stated none
                                      # (the same 0.30 grammar as the trade barriers)
SCENE_BARS_COMPLETE_MIN = 15          # bars must reach within this many minutes of a
                                      # row/session close to grade it (else PENDING —
                                      # same tolerance as the trade resolver)


def _scene_trigger(open_row: dict, r: dict, sigma_open: float) -> str | None:
    """SCENE DETECTOR for the Scene Ledger — did the dealer picture shift vs the
    EPISODE-OPEN row? Same axes and dials as watchtower.big_change (the tower's
    own interrupt test) but diffed against the episode's OPENING scan, not the
    previous tick. Returns the first tripped trigger name, or None. Field names
    verified against live diary rows: gamma_sign/regime/regime_road/vix_ts are
    row-level, magnet/aggressor_flow live under gex_views."""
    import watchtower as _wt
    g0, g1 = open_row.get("gamma_sign"), r.get("gamma_sign")
    if g0 and g1 and g0 != g1:
        return "gamma_flip"                       # gamma-sign-at-spot flipped
    r0, r1 = open_row.get("regime"), r.get("regime")
    if r0 and r1 and r0 != r1:
        return "regime_flip"                      # pin/trend regime flipped
    if not open_row.get("regime_road") and r.get("regime_road"):
        return "road"                             # the pin→trend ROAD opened
    m0 = (open_row.get("gex_views") or {}).get("magnet")
    m1 = (r.get("gex_views") or {}).get("magnet")
    if (m0 is not None and m1 is not None and sigma_open
            and abs(m1 - m0) >= _wt.MAGNET_JUMP_SIGMA * sigma_open):
        return "magnet"
    f0 = (open_row.get("gex_views") or {}).get("aggressor_flow")
    f1 = (r.get("gex_views") or {}).get("aggressor_flow")
    if f0 is not None and f1 is not None and f0 * f1 < 0 and abs(f1) >= _wt.FLOW_FLIP_MIN:
        return "flow"                             # signed flow crossed zero with force
    v0, v1 = open_row.get("vix_ts"), r.get("vix_ts")
    if v0 is not None and v1 is not None and abs(v1 - v0) >= _wt.VIX_TS_JUMP:
        return "vix_ts"
    return None


def _grade_watchtower_scene(rows: list[dict], bars_lookup) -> dict | None:
    """HEAD B's SCENE LEDGER (N20) — the second scoreboard, beside the Trade
    Ledger (_grade_watchtower). The Trade Ledger asks "did the fixed-barrier
    trade win?"; the Scene Ledger asks the question the tower actually answers:
    was the CALL right for as long as ITS scene lasted?

    EPISODE: consecutive judged calls with the SAME direction in an UNCHANGED
    scene collapse into one episode, opened at first issuance (its ts/spot/σ/
    called magnitude are the entry; magnitude falls back to
    SCENE_FALLBACK_TARGET_SIGMA when the tower stated none). The episode closes
    on the FIRST of:
        target   — price touches entry ± dir·called·σ            → win
        adverse  — the mirrored line the other way                → loss
                   (one bar touching both grades pessimistically as a loss,
                    the trade resolver's rule)
        scene_change — _scene_trigger vs the EPISODE-OPEN row
        dir_flip — a judged opposite call (which OPENS the next episode)
        session_close — 16:00 ET
    Scene/flip/close closes grade the signed KEPT move at close: favorable MFE
    below SCENE_NEVER_MOVED_FLOOR_SIGMA = loss (a call that never moved), kept
    ≥ the hold-scaled 0.30σ (the N18 √(hold/390) grammar) = win, else flat.
    After ANY close the next judged call re-arms a fresh episode, so the ledger
    reads all day; out-of-window issuances land in n_dropped_early, never
    silently lost. Empty day → a well-formed zero card (the UI contract).
    Report-card only — nothing live consumes it, and the promotion gate still
    reads the Trade Ledger."""
    try:
        import watchtower as _wt
        calls: list[tuple] = []          # (t0, row, wt_dict) in-window issuances
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
            if wtd.get("direction") not in ("call", "put"):
                continue
            try:
                t0 = datetime.fromisoformat(r["ts"]).astimezone(ET)
            except (KeyError, ValueError, TypeError):
                continue
            if not (_time(9, 45) <= t0.time() < _time(16, 0)):
                n_dropped += 1           # visible-drop accounting (break-lens rule)
                continue
            if not (r.get("spot") and r.get("sigma")):
                continue                 # no entry ruler — ungradeable issuance
            calls.append((t0, r, wtd))
        calls.sort(key=lambda c: c[0])

        # all diary rows are scene witnesses, judged or not
        scene_rows: list[tuple] = []
        if calls:
            for r in rows:
                if r.get("ticker") not in TICKERS:
                    continue
                try:
                    rt = datetime.fromisoformat(r["ts"]).astimezone(ET)
                except (KeyError, ValueError, TypeError):
                    continue
                scene_rows.append((rt, r))
            scene_rows.sort(key=lambda x: x[0])

        # 1-min path WITH closes (the kept-move mark) — the resolver's shape
        path: list[tuple] = []
        if calls:
            for b in bars_lookup(TICKERS[0]) or []:
                try:
                    bt = datetime.fromisoformat(b["ts"]).astimezone(ET)
                except (ValueError, TypeError, KeyError):
                    continue
                if b.get("high") is not None and b.get("low") is not None:
                    path.append((bt, b["high"], b["low"], b.get("close")))
            path.sort(key=lambda x: x[0])

        episodes: list[dict] = []
        i = 0
        while i < len(calls):
            t0, row, wtd = calls[i]
            spot, sigma = row["spot"], row["sigma"]
            d = wtd["direction"]
            sgn = 1 if d == "call" else -1
            called = wtd.get("magnitude_sigma")
            fallback = not (isinstance(called, (int, float)) and called > 0)
            if fallback:
                called = SCENE_FALLBACK_TARGET_SIGMA
            session_close = datetime.combine(t0.date(), _time(16, 0), tzinfo=ET)
            # row-based close: first scene change, unless a direction flip lands sooner
            close_t, reason, trigger = session_close, "session_close", None
            for (rt, r2) in scene_rows:
                if rt <= t0:
                    continue
                if rt >= close_t:
                    break
                trg = _scene_trigger(row, r2, sigma)
                if trg:
                    close_t, reason, trigger = rt, "scene_change", trg
                    break
            for (tj, _rowj, wtj) in calls[i + 1:]:
                if tj >= close_t:
                    break
                if wtj["direction"] != d:
                    close_t, reason, trigger = tj, "dir_flip", None
                    break
            # bar walk: target/adverse can only pre-empt the row-based close
            tgt = spot + sgn * called * sigma
            adv = spot - sgn * called * sigma
            outcome = tt = None
            mfe, last_px, last_bt = 0.0, None, None
            for (bt, hi, lo, cl) in path:
                if bt <= t0:
                    continue
                if bt > close_t:
                    break
                last_bt = bt
                last_px = cl if cl is not None else (hi + lo) / 2.0
                fav = ((hi - spot) if sgn > 0 else (spot - lo)) / sigma
                if fav > mfe:
                    mfe = fav
                tgt_hit = (hi >= tgt) if sgn > 0 else (lo <= tgt)
                adv_hit = (lo <= adv) if sgn > 0 else (hi >= adv)
                if tgt_hit and not adv_hit:
                    outcome, reason, trigger = "win", "target", None
                    tt = (bt - t0).total_seconds() / 60.0
                elif adv_hit:            # both lines in one bar → pessimistic
                    outcome, reason, trigger = "loss", "adverse", None
                if outcome is not None:
                    close_t = bt
                    break
            kept = None
            if outcome == "win":
                kept = called
            elif outcome == "loss":
                kept = -called
            elif (last_bt is None
                    or (close_t - last_bt).total_seconds() / 60.0 > SCENE_BARS_COMPLETE_MIN):
                outcome = "pending"      # bars don't reach the close yet (day running)
            else:
                kept = sgn * (last_px - spot) / sigma
                hold_min = max((close_t - t0).total_seconds() / 60.0, 1.0)
                # N18 hold-scaling: the hit bar lives on the WINDOW's expected move
                hit_bar = PIN_TARGET_SIGMA * math.sqrt(hold_min / 390.0)
                if mfe < SCENE_NEVER_MOVED_FLOOR_SIGMA:
                    outcome = "loss"     # never moved — the call went nowhere
                elif kept >= hit_bar:
                    outcome = "win"
                else:
                    outcome = "flat"
            pend = outcome == "pending"
            episodes.append({"dir": d, "t_open": row["ts"],
                             "t_close": None if pend else close_t.isoformat(),
                             "close_reason": None if pend else reason,
                             "scene_trigger": None if pend else trigger,
                             "outcome": outcome,
                             "called_sigma": round(called, 3),
                             "called_fallback": fallback,
                             "mfe_sigma": round(mfe, 3),
                             "kept_sigma": round(kept, 3) if kept is not None else None,
                             "tt_target_min": round(tt) if tt is not None else None})
            # re-arm: the next issuance at/after the close opens a fresh episode
            # (a dir-flip call sits exactly AT close_t and must open the next one)
            i += 1
            while i < len(calls) and calls[i][0] < close_t:
                i += 1

        wins = sum(1 for e in episodes if e["outcome"] == "win")
        losses = sum(1 for e in episodes if e["outcome"] == "loss")
        flats = sum(1 for e in episodes if e["outcome"] == "flat")
        pending = sum(1 for e in episodes if e["outcome"] == "pending")
        decided = wins + losses          # flats excluded from the denominator
        lo, hi = wilson_bounds(wins, losses) if decided else (None, None)
        closed = [e for e in episodes if e["outcome"] != "pending"]
        mfes = [e["mfe_sigma"] for e in closed]
        kepts = [e["kept_sigma"] for e in closed if e["kept_sigma"] is not None]
        ratios = [e["mfe_sigma"] / e["called_sigma"] for e in closed if e["called_sigma"]]
        tts = [e["tt_target_min"] for e in episodes if e["tt_target_min"]]
        sph = [e["called_sigma"] / (e["tt_target_min"] / 60.0)
               for e in episodes if e["outcome"] == "win" and e["tt_target_min"]]
        pv = max(set(versions), key=versions.count) if versions else None
        return {"scene_grade_v": 1, "prompt_version": pv,
                "n": decided, "wins": wins, "losses": losses, "flats": flats,
                "pending": pending,
                "hit_rate": round(wins / decided, 2) if decided else None,
                "wilson_lo": round(lo, 3) if lo is not None else None,
                "wilson_hi": round(hi, 3) if hi is not None else None,
                "median_mfe_sigma": round(statistics.median(mfes), 3) if mfes else None,
                "median_kept_sigma": round(statistics.median(kepts), 3) if kepts else None,
                "called_vs_realized": round(statistics.median(ratios), 3) if ratios else None,
                "median_tt_target_min": round(statistics.median(tts), 1) if tts else None,
                "sigma_per_hour": round(statistics.median(sph), 3) if sph else None,
                "n_dropped_early": n_dropped,
                "params": {"never_moved_floor_sigma": SCENE_NEVER_MOVED_FLOOR_SIGMA,
                           "fallback_target_sigma": SCENE_FALLBACK_TARGET_SIGMA,
                           "magnet_jump_sigma": _wt.MAGNET_JUMP_SIGMA,
                           "flow_flip_min": _wt.FLOW_FLIP_MIN,
                           "vix_ts_jump": _wt.VIX_TS_JUMP},
                "episodes": episodes}
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
    wt = res.get("watchtower")
    if wt:
        rr = (f"  · total_r {wt['total_r']:+.2f} (median {wt['median_r']:+.2f})"
              if wt.get("total_r") is not None else "")
        sc = wt.get("scans") or {}
        dg = wt.get("disagree") or {}
        to, gv = dg.get("tower_only") or {}, dg.get("gates_vetoed") or {}
        print(f"  HEAD B — WATCHTOWER: {wt['wins']}/{wt['n']} wins "
              f"(rate {wt['win_rate']}){rr}  · {wt['scratch']} scratch "
              f"· {wt['pending']} pending")
        print(f"    scans: {sc.get('judged', 0)} judged / {sc.get('skipped', 0)} skipped  "
              f"· agree both-fired {sc.get('both_fired', 0)} / both-passed {sc.get('both_passed', 0)}")
        print(f"    DISAGREEMENTS (the money column): tower-only "
              f"{to.get('wins', 0)}/{to.get('n', 0)} wins  · gates-vetoed "
              f"{gv.get('wins', 0)}/{gv.get('n', 0)} wins "
              f"(low here = the veto skipped losers = point FOR the tower)\n")
    ws = res.get("watchtower_scene")
    if ws and (ws["n"] or ws["flats"] or ws["pending"]):
        wl = (f"  Wilson {ws['wilson_lo']}-{ws['wilson_hi']}"
              if ws.get("wilson_lo") is not None else "")
        cr = (f"  · called/realized {ws['called_vs_realized']}"
              if ws.get("called_vs_realized") is not None else "")
        print(f"  HEAD B — SCENE LEDGER: {ws['wins']}W/{ws['losses']}L/{ws['flats']}F "
              f"(hit {ws['hit_rate']}){wl}{cr}  · {ws['pending']} pending "
              f"· {len(ws['episodes'])} episodes\n")
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
