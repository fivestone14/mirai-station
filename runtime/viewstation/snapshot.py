"""Mirai Viewstation — snapshot builder (STORYTELLER, tablet side).
Renders the one dealer-map story into the JSON the tablet client draws.

Assembles ONE structured JSON snapshot of the current Mirai state for the tablet
view. It reuses the project's own renderers (dashboard.py, reversion_lens.py) so
every number is identical to the canonical Obsidian markdown views — nothing here
re-derives or fakes data; it reads the same live state files and calls the same
helpers, then also bundles the official rendered markdown verbatim.

No third-party deps. Designed to run under the mirai-station venv (so the
imported skill modules find their own dependencies).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    ET = None

# --- locate the plugin + make the skill modules importable -------------------
PLUGIN_ROOT = Path(__file__).resolve().parents[2]          # .../mirai-station
SKILL_DIR = PLUGIN_ROOT / "skills" / "mirai-left-eye"
STATE_DIR = PLUGIN_ROOT / "state"
REVERSION_DIR = STATE_DIR / "reversion"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import dashboard as dash          # noqa: E402
import reversion_lens as rev      # noqa: E402


# --- helpers -----------------------------------------------------------------
def _now_et() -> datetime:
    return datetime.now(ET) if ET else datetime.now()


def _latest_session_now(now: datetime) -> datetime:
    """If today has no telemetry yet (pre-market / fresh day), fall back to the
    most recent session that does, so the view always shows something real."""
    if rev._read_today_telemetry(now):
        return now
    if not REVERSION_DIR.exists():
        return now
    files = sorted(REVERSION_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl"))
    if not files:
        return now
    last = files[-1].stem  # YYYY-MM-DD
    try:
        y, m, d = (int(x) for x in last.split("-"))
        return datetime(y, m, d, 16, 0, tzinfo=ET) if ET else datetime(y, m, d, 16, 0)
    except Exception:
        return now


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # keep one broken section from sinking the snapshot
        return {"_error": f"{type(exc).__name__}: {exc}"} if default is None else default


def _fnum(x: Any) -> Optional[float]:
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


# --- heatmap (mirrors reversion_lens._gex_heatmap, but emits structured cells) -
def _heatmap_cells(latest: dict, width: int = 24) -> Optional[dict]:
    # flip/magnet/regime come from the resolved dealer map (reversion_lens.
    # resolve_dealer_map) so the band is coloured from the SAME levels the
    # markdown heat strip and the ladder use — one story, every surface.
    dm = rev.resolve_dealer_map(latest) or {}
    spot, cw, pw = latest.get("spot"), dm.get("call_wall"), dm.get("put_wall")
    flip, sign = dm.get("flip"), latest.get("gamma_sign")
    if not (spot and cw and pw and cw > pw):
        return None
    magnet = dm.get("magnet")
    # No per-strike GEX is stored, so model "pull intensity" as gravity wells at
    # the dealer nodes — the magnet (primary) and both walls — each Gaussian-
    # decaying with distance. Brightest cells = where price is most drawn/pinned.
    nodes = [(pw, 0.7), (cw, 0.7)]
    if magnet is not None and pw <= magnet <= cw:
        nodes.append((magnet, 1.0))
    sigma = (cw - pw) / 8.0 or 1.0
    cells, field = [], []
    for i in range(width):
        p = pw + (cw - pw) * (i + 0.5) / width
        if flip is not None:
            kind = "long" if p >= flip else "short"
        else:
            kind = {"positive": "long", "negative": "short"}.get(sign, "flip")
        field.append(sum(w * math.exp(-((p - c) / sigma) ** 2) for c, w in nodes))
        cells.append({"price": round(p, 2), "kind": kind})
    lo_f, rng = min(field), (max(field) - min(field)) or 1.0
    for cell, f in zip(cells, field):
        cell["intensity"] = round((f - lo_f) / rng, 3)
    if flip is not None and pw <= flip <= cw:
        fi = min(width - 1, max(0, int((flip - pw) / (cw - pw) * width)))
        cells[fi]["kind"] = "flip"
    si = min(width - 1, max(0, int((spot - pw) / (cw - pw) * width)))
    cells[si]["spot"] = True

    def idx(level):
        if level is None or not (pw <= level <= cw):
            return None
        return min(width - 1, max(0, int((level - pw) / (cw - pw) * width)))

    return {
        "cells": cells,
        "lo": round(pw, 2),
        "hi": round(cw, 2),
        "spot_index": si,
        "markers": {
            "call_wall": {"index": idx(cw), "price": round(cw, 2)},
            "put_wall": {"index": idx(pw), "price": round(pw, 2)},
            "magnet": {"index": idx(magnet), "price": _fnum(magnet)},
            "flip": {"index": idx(flip), "price": _fnum(flip)},
        },
        "pull": ({
            "spot": round(spot, 2), "magnet": _fnum(magnet),
            "dpts": round(magnet - spot, 2),
            "dpct": round((magnet - spot) / spot * 100, 3),
            "dir": "up" if magnet > spot else ("down" if magnet < spot else "flat"),
        } if magnet is not None else None),
    }


def _ticker_block(rows: list[dict], tk: str) -> dict:
    agg = rev._aggregate(rows, tk)
    latest = agg.get("latest") or {}
    re_ = latest.get("reversion_extreme") or {}
    gv = latest.get("gex_views") or {}
    status, setup, stretch = rev._prospect_status(latest) if latest else ("💤 stand by", "—", "—")
    return {
        # the ONE resolved dealer read (proxy-led, native as challenger badges) —
        # the client renders this; the raw per-engine fields below stay for debug
        "dealer_map": _safe(lambda: rev.resolve_dealer_map(latest), default=None),
        "ticker": tk,
        "spot": _fnum(latest.get("spot")),
        "prior_close": _fnum(latest.get("prior_close")),
        "regime": latest.get("regime"),
        "regime_conf": _fnum(latest.get("regime_conf")),
        "regime_reads": latest.get("regime_reads"),
        "sigma": _fnum(latest.get("sigma")),
        "status": {"badge": status, "setup": setup, "stretch": stretch},
        "gex": {
            "call_wall": _fnum(latest.get("call_wall")),
            "put_wall": _fnum(latest.get("put_wall")),
            "gamma_sign": latest.get("gamma_sign"),
            "gamma_flip": _fnum(latest.get("gamma_flip")),
            "source": latest.get("gex_source"),
            # isolated GEX-views engine (shadow)
            "magnet": _fnum(gv.get("magnet")),
            "flip": _fnum(gv.get("flip")),
            "flip_band": [_fnum(x) for x in (gv.get("flip_band") or [])] or None,
            "views_regime": gv.get("regime"),
            "views_source": gv.get("source"),
            "runway_new": _fnum(gv.get("runway_new")),
            # vanna/charm dealer-flow exposures (shadow, record-only)
            "vex": _fnum(gv.get("vex")),
            "cex": _fnum(gv.get("cex")),
            "vex_sign": gv.get("vex_sign"),
            "cex_sign": gv.get("cex_sign"),
            "charm_wall": _fnum(gv.get("charm_wall")),
            "vanna_wall": _fnum(gv.get("vanna_wall")),
            # UW-style 0DTE gamma walls, shown beside the legacy OI walls
            "call_wall_gamma": _fnum(gv.get("call_wall_gamma")),
            "put_wall_gamma": _fnum(gv.get("put_wall_gamma")),
            # smoother, stable companion to the (teleporting) argmax magnet
            "pin_centroid": _fnum(gv.get("pin_centroid")),
            # magnet zones (2026-07-09): `magnet` above is now zone-1's weighted
            # CENTER; the ranked zone list + contested flag ride beside it and
            # `pin_argmax` keeps the legacy single-strike magnet for A/B. Rows
            # written before the zones landed carry null here — old days render
            # exactly as before.
            "pin_zones": (gv.get("pin_zones")
                          if isinstance(gv.get("pin_zones"), list) else None),
            "pin_contested": (None if gv.get("pin_contested") is None
                              else bool(gv.get("pin_contested"))),
            "pin_argmax": _fnum(gv.get("pin_argmax")),
        },
        "reversion": {
            "armed": bool(re_.get("armed")),
            "fired": bool(re_.get("fired")),
            "direction": re_.get("direction"),
            "score": _fnum(re_.get("score")),
            "confidence": _fnum(re_.get("confidence")),
            "gap_stretch": _fnum(re_.get("gap_stretch")),
            "vwap_stretch": _fnum(re_.get("vwap_stretch")),
            "runway_sigma": _fnum(re_.get("runway_sigma")),
            "runway_ok": bool(re_.get("runway_ok")),
            "magnet": _fnum(re_.get("magnet")),
            "arm_reason": re_.get("arm_reason"),
        },
        "counts": {
            "scans": agg.get("scans"),
            "pin": agg.get("pin"),
            "trend": agg.get("trend"),
            "neutral": agg.get("neutral"),
            "armed": agg.get("armed"),
            "would_fire": agg.get("would_fire"),
            "would_fire_pre": agg.get("would_fire_old"),
            "no_runway": agg.get("no_runway"),
            "level_breaks": agg.get("level_breaks"),
        },
        "heatmap": _heatmap_cells(latest) if latest else None,
        # 2026-07-09 shadow layers — the raw dicts passed through verbatim (small,
        # optional / fail-open; null on rows written before the layers landed)
        "speedometer": latest.get("speedometer"),
        "range_ruler": latest.get("range_ruler"),
        "gamma_roll": latest.get("gamma_roll"),
        "event_lens": latest.get("event_lens"),
        # dated book (2026-07-10): far structural OI walls from the nightly
        # sidecar — same verbatim/fail-open contract as the layers above
        "dated_gex": latest.get("dated_gex"),
        "last_ts": latest.get("ts"),
    }


def _series(rows: list[dict], tk: str) -> list[dict]:
    out = []
    for r in rows:
        if r.get("ticker") != tk:
            continue
        re_ = r.get("reversion_extreme") or {}
        # 2026-07-09 shadow-layer telemetry — every key optional / fail-open;
        # rows written before these layers landed simply carry null.
        sp = r.get("speedometer")
        rr = r.get("range_ruler")
        gr = r.get("gamma_roll")
        ev = r.get("event_lens")
        # magnet zones (2026-07-09) — compact per-scan echo: [{"c","s","side"}...]
        # or null on rows written before the zones landed (same gv/gt sources the
        # magnet reads from; gex_views first, gex_theta as fallback).
        gvr = r.get("gex_views") or {}
        gtr = r.get("gex_theta") or {}
        pz = gvr.get("pin_zones")
        pc = gvr.get("pin_contested")
        if not isinstance(pz, list):
            pz = gtr.get("pin_zones")
        if pc is None:
            pc = gtr.get("pin_contested")
        zones = ([{"c": _fnum(z.get("center")), "s": _fnum(z.get("share")),
                   "side": z.get("side")}
                  for z in pz if isinstance(z, dict)]
                 if isinstance(pz, list) else None) or None
        out.append({
            "ts": r.get("ts"),
            "spot": _fnum(r.get("spot")),
            "regime": r.get("regime"),
            "armed": bool(re_.get("armed")),
            "fired": bool(re_.get("fired")),
            "direction": re_.get("direction"),
            "gap_stretch": _fnum(re_.get("gap_stretch")),
            "runway_sigma": _fnum(re_.get("runway_sigma")),
            "runway_ok": bool(re_.get("runway_ok")),
            "spd": ({"pace": _fnum(sp.get("rv_pace")), "jz": _fnum(sp.get("jump_z")),
                     "jsign": sp.get("jump_sign"), "down": _fnum(sp.get("semivar_down_share")),
                     "q": sp.get("quality")} if isinstance(sp, dict) else None),
            "rr": ({"em": _fnum(rr.get("em_points")), "emo": _fnum(rr.get("em_open")),
                    "cons": _fnum(rr.get("em_consumed")),
                    "q": rr.get("quality")} if isinstance(rr, dict) else None),
            "groll": ({"div": bool(gr.get("divergence")), "dir": gr.get("divergence_dir"),
                       "zw": _fnum(gr.get("zero_dte_wall")),
                       "fw": _fnum(gr.get("far_wall")),
                       # N12: defended (net long γ — a real pin) vs accelerant (net
                       # short γ — fuel); the wording surface must key off this
                       "kind": gr.get("zero_dte_wall_kind")} if isinstance(gr, dict) else None),
            "evl": ({"kind": ev.get("event_kind"), "phase": ev.get("phase"),
                     "ratio": _fnum(ev.get("surprise_ratio"))} if isinstance(ev, dict) else None),
            "zones": zones,
            # None preserved: "no zone data" (old rows) ≠ "not contested"
            "contested": None if pc is None else bool(pc),
        })
    return out


def _paper(rows: list[dict]) -> dict:
    resolved, pending = rev._resolve_beta_trades(rows)
    trades = []
    wins = losses = scratches = 0
    win_mins = []
    best_sigmas = []
    for t in resolved:
        outcome = t.get("outcome")
        if outcome == "win":
            wins += 1
            if t.get("ttr_min") is not None:
                win_mins.append(t["ttr_min"])
        elif outcome == "loss":
            losses += 1
        else:
            scratches += 1
        if t.get("mfe_sigma") is not None:
            best_sigmas.append(t["mfe_sigma"])
        ts = t.get("ts") or ""
        trades.append({
            "ts": ts,                                  # full ISO (client renders in PT)
            "time": ts[11:16] if len(ts) >= 16 else ts,
            "ticker": t.get("ticker"),
            "fade": "LONG" if t.get("dir") == "call" else "SHORT",
            "direction": t.get("dir"),
            "best_sigma": _fnum(t.get("mfe_sigma")),
            "outcome": outcome,
            "ttr_min": t.get("ttr_min"),
        })
    trades.sort(key=lambda x: x["time"], reverse=True)
    scored = wins + losses
    return {
        "trades": trades,
        "summary": {
            "win": wins, "loss": losses, "scratch": scratches,
            "pending": pending,
            "scored": scored,
            "win_rate": round(100 * wins / scored) if scored else None,
            "avg_win_min": round(sum(win_mins) / len(win_mins)) if win_mins else None,
            "avg_best_sigma": round(sum(best_sigmas) / len(best_sigmas), 2) if best_sigmas else None,
            "target_sigma": rev.PAPER_TARGET_SIGMA,
            "stop_sigma": rev.PAPER_STOP_SIGMA,
            "runway_min_sigma": rev.RUNWAY_MIN_SIGMA,
        },
    }


def _today_block(now: datetime) -> dict:
    # Gex-only (2026-07-03 restructure): the scoreboard is the Fade Lens's own
    # paper record; the watchers are the gex system's fixed watch-list.
    mood = dash._macro_mood(now) or {}
    overall = (mood.get("overall") or {})
    sectors = mood.get("sectors") or {}
    rev_sum = dash._reversion_summary(now) or {}
    won = int(rev_sum.get("wins") or 0)
    lost = int(rev_sum.get("losses") or 0)
    scored = won + lost
    watchers = [{"code": m["code"], "what": m["what"]} for m in dash.GEX_WATCHERS]
    return {
        "scoreboard": {
            "live": int(rev_sum.get("pending") or 0),
            "finished": scored + int(rev_sum.get("scratch") or 0),
            "won": won, "lost": lost,
            "win_rate": round(100 * won / scored) if scored else None,
            "paper": True,
        },
        "mood": {
            "direction": _fnum(overall.get("direction")),
            "magnitude": _fnum(overall.get("magnitude")),
            "confidence": _fnum(overall.get("confidence")),
            "words": dash._mood_words(overall.get("direction", 0.0)),
            "conf_words": dash._conf_words(overall.get("confidence", 0.0)),
            "reason": mood.get("reason"),
            "reasoning": mood.get("reasoning"),
            "sectors": {k: {"direction": _fnum(v.get("direction")),
                            "magnitude": _fnum(v.get("magnitude")),
                            "label": dash._sector_plain(k)}
                        for k, v in sectors.items()},
            "shadow": True,  # macro-mood is shadow-only (not allowed to trade)
        },
        "watchers": watchers,
    }


# --- top-level ----------------------------------------------------------------
def build_snapshot() -> dict:
    real_now = _now_et()
    now = _latest_session_now(real_now)
    rows = _safe(lambda: rev._read_today_telemetry(now), default=[]) or []
    tickers = {}
    for tk in getattr(rev, "TICKERS", ("SPX",)):
        tickers[tk] = _safe(lambda tk=tk: _ticker_block(rows, tk), default={"ticker": tk})

    snap = {
        "generated_at": real_now.isoformat(),
        "session_date": now.date().isoformat(),
        "is_stale": now.date() != real_now.date(),
        "market_phase": _safe(lambda: dash._market_phase(real_now), default="unknown"),
        "telemetry_rows": len(rows),
        "today": _safe(lambda: _today_block(now), default={}),
        "ult": {
            "tickers": tickers,
            "paper": _safe(lambda: _paper(rows), default={"trades": [], "summary": {}}),
            "dials": {
                "PAPER_TARGET_SIGMA": rev.PAPER_TARGET_SIGMA,
                "PAPER_STOP_SIGMA": rev.PAPER_STOP_SIGMA,
                "RUNWAY_MIN_SIGMA": rev.RUNWAY_MIN_SIGMA,
                "WALL_PROX_SIGMA": rev.WALL_PROX_SIGMA,
            },
        },
        "series": {tk: _safe(lambda tk=tk: _series(rows, tk), default=[])
                   for tk in getattr(rev, "TICKERS", ("SPX",))},
        "official_markdown": {
            "today": _safe(lambda: dash.render(now), default=""),
            "ult": _safe(lambda: rev.render_learning_view(rows, now), default=""),
        },
    }
    return snap


"""=== LIVE SPOT (the streaming price) ================================================

The map's price used to be a SCAN SNAPSHOT: the tablet polled every 4s but only ever
re-read the diary, and the diary is written by the left-eye scanner — launchd asks for
60s, but an RTH scan overruns that and launchd DROPS the overlapping fire. Measured on
2026-07-13: a new price every 2 min at best, 4.1 min median, 12.5 min at worst. The UI
eased smoothly between those points, so a 12-minute-old print still LOOKED live.

This is the fix: one cheap Schwab quote (`lefteye_fetcher.live_spot` — a single-quote
call, never the chain), served behind a short cache so N tablets and a fast poll can
never multiply into N×poll upstream calls.

Rules it must obey:
  * NEVER fabricate. Upstream returns None (dead bearer, market closed, network) →
    `spot: None` and the tablet keeps showing the diary's scan price, exactly as before.
  * The GRAVITY (magnet, walls, flip, regime) still comes from the diary. Only the PRICE
    streams. A live price hung on stale gravity is honest; a fabricated one is not.
"""
_SPOT_CACHE: dict = {"ts": 0.0, "val": None, "src": None}
_SPOT_TTL_S = 2.0          # ≥ the tablet's poll gap, so the quote is shared, not multiplied
_SPOT_LOCK = __import__("threading").Lock()
_SPOT_FETCH_LOCK = __import__("threading").Lock()   # single-flight for the upstream call (sweep 08-11)

# --- SNDK spot tape (08-11) --------------------------------------------------
# The SNDK tab's 5s /api/spot poll already buys a fresh Schwab quote (2s TTL
# above); persisting it is FREE — zero additional upstream calls, no sampler
# process. Only fresh (non-cached) fetches for a tape ticker append, only
# inside a pure-clock RTH window (never a market-hours API call — the tape may
# not add load to earn its gate), so the file is exactly "what a watcher
# already paid for". Unwatched stretches keep the hunter's 2-min diary spots.
_TAPE_TICKERS = ("SNDK",)
_TAPE_DIR = STATE_DIR / "sndk_tape"
_TAPE_MIN_GAP_S = 4.0      # append floor: a burst of pollers still writes ≤1 row/4s
_TAPE_LAST: dict = {"ts": 0.0}


def _tape_append(ticker: str, px: float, mono_ts: float) -> None:
    """Best-effort by contract: the tape is a by-product, so any failure here
    (full disk, bad perms) must never dent the /api/spot response."""
    if ticker not in _TAPE_TICKERS or mono_ts - _TAPE_LAST["ts"] < _TAPE_MIN_GAP_S:
        return
    try:
        now = _now_et()
        if now.weekday() >= 5:
            return
        hm = now.hour * 60 + now.minute
        if not (9 * 60 + 30 <= hm <= 16 * 60):
            return
        import atomic_io               # SKILL_DIR is on sys.path (module header)
        atomic_io.append_jsonl(_TAPE_DIR / f"{now.date().isoformat()}.jsonl",
                               {"ts": now.isoformat(), "spot": round(float(px), 4),
                                "src": "schwab_quote"})
        _TAPE_LAST["ts"] = mono_ts
    except Exception:
        pass


def live_spot(ticker: str = "SPX") -> dict:
    """The current index print for the map. Cached `_SPOT_TTL_S` so a room full of
    tablets polling every 3s still costs upstream ~1 quote every 2s. Fail-open: any
    error is a null spot, never a stale value dressed up as fresh."""
    import time as _t
    now = _t.time()
    with _SPOT_LOCK:
        c = _SPOT_CACHE
        if c["val"] is not None and (now - c["ts"]) < _SPOT_TTL_S and c.get("tk") == ticker:
            return {"ticker": ticker, "spot": c["val"], "source": c["src"],
                    "age_s": round(now - c["ts"], 2), "cached": True}
    px = None
    # SINGLE-FLIGHT (20-agent sweep 08-11): the upstream call rides its own lock so
    # N tablets missing the cache at the same instant serialize into ONE Schwab hit
    # — the "N tablets never multiply into N× upstream calls" guarantee, made true.
    # Late arrivals re-check the cache after acquiring; the winner's fill serves them.
    with _SPOT_FETCH_LOCK:
        with _SPOT_LOCK:
            c = _SPOT_CACHE
            if c["val"] is not None and (_t.time() - c["ts"]) < _SPOT_TTL_S and c.get("tk") == ticker:
                return {"ticker": ticker, "spot": c["val"], "source": c["src"],
                        "age_s": round(_t.time() - c["ts"], 2), "cached": True}
        try:
            import lefteye_fetcher
            px = lefteye_fetcher.live_spot(ticker)
        except Exception:
            px = None
        px = float(px) if isinstance(px, (int, float)) and px > 0 else None
        with _SPOT_LOCK:
            if px is not None:
                _SPOT_CACHE.update({"ts": now, "val": px, "src": "schwab_quote", "tk": ticker})
    if px is not None:
        _tape_append(ticker, px, now)      # outside the lock — file IO never blocks a poll
    return {"ticker": ticker, "spot": px,
            "source": "schwab_quote" if px is not None else None,
            "age_s": 0.0, "cached": False,
            "ts": datetime.now(ET).isoformat() if ET else None}


def replay_day(day: str) -> dict:
    """Authoritative Watchtower grading for one past session — the data behind the
    tablet's Replay tab. The SWEEP LEDGER (gex_polarity_ab._grade_watchtower_sweep,
    2026-07-17 — the single scoreboard that replaced the trade + scene ledgers) is
    computed on-demand with the exact grader the nightly card uses — nothing
    re-derives outcomes here. Read-only.

    `fires` keeps the per-fire excursion vector from the canonical resolver so the
    chart can still plot mfe/mae/move_60m, but each fire's `outcome` is ITS parent
    sweep call's verdict (win/loss/mixed/pending) — the chips are sweep-authoritative.
    """
    import gex_polarity_ab as ab  # same SKILL_DIR, already on sys.path
    rows = ab._read_rows(day)
    _bars_cache: dict = {}       # one bar fetch shared by BOTH graders (grade_day's pattern)

    def _bars(tk):
        if tk not in _bars_cache:
            _bars_cache[tk] = ab._default_bars(tk, day) or []
        return _bars_cache[tk]

    sweep = ab._grade_watchtower_sweep(rows, _bars) or {}
    calls = sweep.get("calls") or []

    def _parent_verdict(ts):
        # calls are t0-sorted and a dir-flip fire sits exactly AT the next call's
        # t_open, so the LAST call opened at/before the fire is its parent
        v = None
        for c in calls:
            if c["t_open"] <= ts:
                v = c["verdict"]
            else:
                break
        return v

    trades, _pending = rev._resolve_beta_trades(rows, bars_lookup=_bars,
                                                signal_key="watchtower")
    pred_at = {r.get("ts"): (r.get("watchtower") or {}).get("magnitude_sigma")
               for r in rows if isinstance(r.get("watchtower"), dict)}
    fires = [{"ts": t.get("ts"), "dir": t.get("dir"),
              "outcome": _parent_verdict(t.get("ts") or ""),
              "mfe_sigma": t.get("mfe_sigma"), "mae_sigma": t.get("mae_sigma"),
              "move_60m": t.get("move_60m"), "ttr_min": t.get("ttr_min"), "r": t.get("r"),
              "pred_magnitude_sigma": pred_at.get(t.get("ts"))}
             for t in trades]
    return {"day": day, "sweep": sweep, "fires": fires}


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), indent=2, default=str))


# --- SNDK payload (08-21) — the exact scene JSON the reader hands the model ------
# The "SNDK Payload" tab shows ONLY what Claude is fed: the scene dict that
# skills/sndk-pro/sndk_read.build_scene assembles from the newest diary row,
# rebuilt here through the SAME functions (never a copy of the logic), plus the
# 12-word user-message wrapper it rides in. The cached doctrine is deliberately
# not returned — the tab's job is the varying payload, not the rulebook.
#
# `now` matters: clock.*, data_sources.*.age_min, wall/frozen ages and the
# 30-minute windows are all measured against it. While the newest row is fresh
# the scene is built against the wall clock, exactly as a live read would; once
# it is not, the scene is built as of that row's own timestamp, so the tab shows
# the last scene the reader COULD have built rather than a 17-hour-old book with
# minutes_to_close 0. The response says which (`as_of`).
#
# 2026-08-30 — THE CUTOVER IS THE BOOK'S CEILING, not a round number. It was 10
# minutes, chosen before sr-7 gave build_scene a freshness gate that DELETES an
# aged-out block. Those two rules met in the middle and made the tab
# non-monotonic: a 1-minute-old row rendered 4,363 chars, a 5-minute-old row
# rendered 1,871 (eight evidence blocks deleted, because the book behind it had
# crossed six minutes), and an 11-minute-old row rendered the full 4,363 again
# because the as-of build reset every age to ~0. The payload was most complete
# when the data was oldest, every trading day from about 16:02 to 16:09 ET.
#
# Pinning the cutover to the book's own ceiling closes it: while the book would
# survive the gate we build live and nothing is dropped; the moment it would
# not, we switch to the as-of build and show the whole scene with data_sources
# saying how old it is. The tab never renders a gutted scene, which matters
# because the phone reads neither `freshness_rules` nor `data_sources` and would
# otherwise print "NO WALL MEASURED" about walls that were measured and deleted.
_SNDK_PRO_DIR = PLUGIN_ROOT / "skills" / "sndk-pro"


def sndk_payload(now: Optional[datetime] = None) -> dict:
    if str(_SNDK_PRO_DIR) not in sys.path:
        sys.path.insert(0, str(_SNDK_PRO_DIR))
    import sndk_read as R   # lazy: the tab is rarely open; keeps server start light
    now = now or _now_et()
    diary = R._diary_dir()
    files = sorted(diary.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl"))
    rows: list = []
    day = None
    for path in reversed(files):                      # newest session with live rows
        rows = [r for r in R._read_jsonl(path)
                if r.get("ticker") == "SNDK" and not (r.get("meta") or {}).get("forced")]
        if rows:
            day = path.stem
            break
    if not rows:
        return {"error": "no SNDK diary rows yet", "scene": None}
    row = rows[-1]
    t_row = R._ts(row)
    # the BOOK's age decides, not the scan's: the book is always the older of
    # the two, and it is the one the freshness gate measures.
    book_age = R._age_min(R._book_asof(row), now)
    live = book_age is not None and book_age <= R.MAX_BOOK_AGE_MIN
    build_now = now if live else (t_row or now)
    rw = R.with_path(row, rows)
    band = R.magnet_band(rw)
    frozen = R.frozen_fields(rows, build_now)
    # obs-3: the payload tab's charter is the exact scene the reader hands the
    # model, so it carries the same since-last-read frame — anchored on the
    # last row that spent a call, exactly as read_once anchors it. One declared
    # divergence: no wake fires here, so `why_this_read` is absent.
    reads = [r for r in R._read_jsonl(R._reads_dir() / f"{day}.jsonl")
             if r.get("era") == R.ERA]
    last_call = next((r for r in reversed(reads)
                      if r.get("wall_s") is not None), None)
    scene = R.build_scene(rw, band, frozen, rows, build_now,
                          since_last_read=R.frame_since_last_read(
                              rw, rows, last_call, None, False, build_now))
    text = json.dumps(scene, default=str)
    return {
        "session": day,
        # sr-8 deleted scene.instrument — the string "SNDK" on every scan of a
        # reader that watches nothing else, which the model does not need told
        # ~190 times a day. The phone masthead DID need it, so it moves here,
        # to the display wrapper, off the diary row that already carries it.
        # This is not a hardcoded ticker in the view (the phone spec forbids
        # that outright: absent means show nothing, never a literal "SNDK"),
        # it is the same value from the same source, on the side of the fence
        # that pays no model tokens for it.
        "instrument": row.get("ticker"),
        "row_ts": row.get("ts"),
        "built_at": build_now.isoformat(),
        "as_of": "live" if live else "last scan",
        "era": R.ERA,
        "model": R.PINNED_MODEL,
        "scans_today": len(rows),
        "scene": scene,
        "scene_chars": len(text),
        "user_prompt": "Read this scene cold and reply with the JSON object only.\n\nSCENE:\n" + text,
        # what must be true before this scene is worth a model call. Read off the
        # reader's OWN constants rather than retyped into the page: a tab that
        # quotes a threshold the reader stopped using is the doctrine-drifts-from-
        # code trap, and this build has already been caught by it six times.
        "gates": {
            "min_gap_min": R.MIN_GAP_MIN,
            "daily_cap": R.DAILY_CALL_CAP,
            "stale_book_min": R.STALE_BOOK_MIN,
            "heartbeat_min": R.HEARTBEAT_MIN,
            # wk-1 renamed and re-scoped these: the gate now measures spot
            # travel against the state at the last SPEECH, reads implied vol off
            # a 5-book median rather than the raw print, and needs a discrete
            # flip to hold for two consecutive books before it counts.
            "spot_sigma": R.WAKE_SPOT_SIGMA,
            "iv_pp": R.WAKE_IV_PP,
            "flip_sigma": R.WAKE_FLIP_SIGMA,
            "confirm_books": R.WAKE_CONFIRM_BOOKS,
            # 09-02: the two numbers the gate card used to carry as literals
            "iv_median_books": R.WAKE_IV_MEDIAN_BOOKS,
            "flip_cross_sigma": R.WAKE_FLIP_CROSS_SIGMA,
            # sr-7 freshness ceilings, read off the reader's own constants for
            # the same reason as every gate above: the tab must not be able to
            # quote a ceiling the reader has stopped enforcing.
            "max_book_age_min": R.MAX_BOOK_AGE_MIN,
        },
    }


# --- SNDK memory overview (08-21) — what the model's history tool can reach -----
# Read-only inventory of state/sndk_rag (the SNDK RAG store): the per-read
# slices (Face A metadata + the model's own sentence), the rolled-up day
# summaries, and the standing terrain. Counts and dates only — the records
# themselves come back through the real CLI (server._memory_query), so the
# Memory view shows exactly what the model would be handed.
_RAG_DIR = STATE_DIR / "sndk_rag"


def sndk_memory_overview() -> dict:
    days = []
    sl_dir = _RAG_DIR / "slices"
    if sl_dir.exists():
        for p in sorted(sl_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")):
            rows = []
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("kind") == "slice":
                        rows.append(r)
            except OSError:
                continue
            if not rows:
                continue
            # obs-1: the histogram counts what each read FOUND, not which way it
            # leaned. Reading `meta.vector` — a field obs-1 stopped writing —
            # produced three zeros on every day and a bar chart of nothing.
            # 09-02: a forecast-era slice (it carries `vector`, never `quiet`)
            # answered a different question and is counted apart — the Sep-1
            # audit found 390 of them filed as "quiet".
            vec = {"unusual": 0, "quiet": 0, "dropped": 0, "legacy": 0}
            for r in rows:
                m = r.get("meta") or {}
                vec["legacy" if ("vector" in m and "quiet" not in m) else
                    "unusual" if m.get("notable_count") else
                    "dropped" if m.get("abstain") == "forced" else "quiet"] += 1
            days.append({"date": p.stem, "n": len(rows),
                         "first": (rows[0].get("meta") or {}).get("time"),
                         "last": (rows[-1].get("meta") or {}).get("time"),
                         "vectors": vec,
                         "last_line": (rows[-1].get("narrative") or "")[:160]})
    summaries = {"n": 0, "last": None, "first": None, "rag_v": None}
    sp = _RAG_DIR / "summaries.jsonl"
    if sp.exists():
        try:
            dates = []
            vers = set()
            for line in sp.read_text().splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kind") == "day_summary":
                    dates.append(r.get("date"))
                    vers.add(r.get("rag_v"))
            dates = sorted(d for d in dates if d)
            summaries = {"n": len(dates), "first": dates[0] if dates else None,
                         "last": dates[-1] if dates else None,
                         "rag_v": sorted(v for v in vers if v is not None)}
        except OSError:
            pass
    terrain = None
    tp = _RAG_DIR / "terrain.json"
    if tp.exists():
        try:
            t = json.loads(tp.read_text())
            terrain = {"built": t.get("built"), "sessions": t.get("sessions"),
                       "rag_v": t.get("rag_v"), "narrative": t.get("narrative")}
        except (OSError, json.JSONDecodeError, ValueError):
            terrain = {"error": "unreadable"}
    return {"store": str(_RAG_DIR), "days": days, "summaries": summaries, "terrain": terrain,
            "slices_total": sum(d["n"] for d in days)}
