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


def replay_day(day: str) -> dict:
    """Authoritative Watchtower grading for one past session — the data behind the
    tablet's Replay tab. Reuses the canonical resolver (target-before-stop over the
    paper hold window, keyed on the watchtower signal) so the tab's win/loss/scratch
    is IDENTICAL to the paper ledger — nothing re-derives outcomes here. Read-only.

    Returns the fresh Watchtower fires with their resolved outcome + excursion vector;
    the client matches each fire back to its diary scan (by ts) for entry price / σ and
    plots it where it triggered, coloured by how it resolved.
    """
    import gex_polarity_ab as ab  # same SKILL_DIR, already on sys.path
    rows = ab._read_rows(day)
    wt = ab._grade_watchtower(rows, lambda tk: ab._default_bars(tk, day)) or {}
    trades = wt.get("trades") or []
    fires = [{"ts": t.get("ts"), "dir": t.get("dir"), "outcome": t.get("outcome"),
              "mfe_sigma": t.get("mfe_sigma"), "mae_sigma": t.get("mae_sigma"),
              "move_60m": t.get("move_60m"), "ttr_min": t.get("ttr_min"), "r": t.get("r"),
              "pred_magnitude_sigma": t.get("pred_magnitude_sigma")}
             for t in trades]
    return {
        "day": day,
        "target_sigma": rev.PAPER_TARGET_SIGMA,
        "stop_sigma": rev.PAPER_STOP_SIGMA,
        "max_hold_min": rev.PAPER_MAX_HOLD_MIN,
        "pending": wt.get("pending", 0),
        "summary": {k: wt.get(k) for k in ("n", "wins", "scratch", "win_rate")},
        "fires": fires,
    }


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), indent=2, default=str))
