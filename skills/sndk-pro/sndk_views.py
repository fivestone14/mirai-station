"""
sndk_views.py — SNDK-PRO row assembler: chain + live spot → one diary row.

PURE (no network, no file I/O): everything here is computed from the contracts
list, the live spot, and today's prior rows the hunter passes in. The GEX math
is the left-eye engine's own pure functions (build_views, slide_0dte, dex_views,
profile_ladder, classify_regime) — imported, never forked — so the SNDK map is
the SAME math on a different underlying.

THE FRONT-BOOK ADAPTATION (the one real difference from SPX): SPX has a daily
0DTE expiry, so the engine's "today's book" (slide B/A0) is always populated.
SNDK expires weekly — 4 of 5 days the dte==0 book is EMPTY, which is normal,
not an outage. The tradeable book is the FRONT (nearest) weekly, so the magnet,
walls, and every per-strike pane are computed by handing the front-expiry
contracts to the very same slide_0dte the SPX 0DTE book goes through — each
contract's real dte drives its own tau (sessions/252), so a 3-DTE SNDK book is
priced as a 3-DTE book, never as a fake 0DTE. On SNDK's actual expiry day the
front book IS the 0DTE book and the whole engine collapses to SPX semantics
(live minutes-to-close clock included).

Row schema mirrors the SPX reversion diary field names the viewstation reads;
SPX-only fields (watchtower, siege, aggressor_flow, speedometer...) are OMITTED
rather than faked. Scale: every constant here is fractional-of-spot or derived
from the chain (σ from ATM IV) — no SPX point-scale assumptions.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_LEFT_EYE = str(_SKILL_DIR.parent / "mirai-left-eye")
if _LEFT_EYE not in sys.path:
    sys.path.insert(0, _LEFT_EYE)

import lefteye_gex_box as _gxb                       # noqa: E402
import lefteye_dex as _dex                           # noqa: E402
import lefteye_profile_ladder as _pl                 # noqa: E402
import lefteye_adaptive_em as _aem                   # noqa: E402
from lefteye_range_ruler import DEBIAS, LATE_DAY_MIN, _atm_strike, _leg_price  # noqa: E402
from reversion_lens import classify_regime, sigma_ruler                        # noqa: E402
from sndk_feed import _tau_front                     # noqa: E402 — M1 front-book clock (pure)

_ET = ZoneInfo("America/New_York")
ROW_V = 2                     # row-schema era tag (cf. pin_field_v / dex_v);
                              # v2 = 2026-07-27 audit fixes: front-book τ (M1 —
                              # σ/EM values re-based) + meta honesty fields


def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def front_book(contracts: list) -> tuple[list, Optional[int]]:
    """The FRONT (nearest-expiry) book and its dte — SNDK's tradeable book.
    On expiry day this is the dte==0 book; otherwise the nearest weekly."""
    dtes = sorted({c["dte"] for c in contracts
                   if isinstance(c.get("dte"), int) and c["dte"] >= 0})
    if not dtes:
        return [], None
    fd = dtes[0]
    return [c for c in contracts if c.get("dte") == fd], fd


def _prev_rows_scan(prev_rows: Optional[list]) -> dict:
    """Day-memory off today's earlier SNDK rows (the one-shot process has no
    other): σ anchor (first row's), em_open (first positive), prev pin (last
    same-era pin, H4 hysteresis). Fail-open to empties."""
    out = {"sigma_anchor": None, "em_open": None, "prev_pin": None}
    for r in prev_rows or []:
        try:
            if out["sigma_anchor"] is None:
                sa = _num(r.get("sigma_anchor")) or _num(r.get("sigma"))
                if sa:
                    out["sigma_anchor"] = sa
            if out["em_open"] is None:
                eo = _num((r.get("range_ruler") or {}).get("em_open"))
                if eo and eo > 0:
                    out["em_open"] = eo
        except Exception:
            continue
    for r in reversed(prev_rows or []):
        try:
            gv = r.get("gex_views") or {}
            if gv.get("pin_field_v") == _gxb.PIN_FIELD_V and gv.get("pin") is not None:
                out["prev_pin"] = gv["pin"]
                break
        except Exception:
            continue
    return out


def _em_read(front: list, front_dte: Optional[int], spot: float,
             minutes_to_close: Optional[float], em_open: Optional[float],
             day_open: Optional[float], day_high: Optional[float],
             day_low: Optional[float]) -> dict:
    """RANGE RULER, front-weekly edition. The SPX ruler prices the 0DTE ATM
    straddle; SNDK has one only on expiry day, so the front-weekly straddle is
    priced instead and scaled DOWN to the remaining-session horizon on the
    engine's own trading-time clock: em_today = em_front × √(τ_day / τ_front)
    (the √time decay rule). On expiry day the scale factor is exactly 1.0 and
    this IS the SPX read. Same DEBIAS, same quality ladder, fail-open."""
    try:
        was_unanchored = em_open is None
        k_atm, call, put = _atm_strike(front, float(spot))
        em_raw, anchor = None, None
        if k_atm is not None:
            pc, tc = _leg_price(call)
            pp, tp = _leg_price(put)
            if pc is not None and pp is not None:
                em_raw = pc + pp
                anchor = "straddle_mid" if (tc == "mid" and tp == "mid") else "straddle_mark"
        scale = None
        if em_raw is not None and front_dte is not None:
            tau_day = _gxb._tau_for(0, minutes_to_close)
            # M1: the engine's dated τ excludes today's remaining session (a
            # Thursday-morning weekly read as 1 session when ~2 remain → EM
            # inflated ×1.41 at that open); the front-book clock counts it.
            # On expiry day _tau_front(0, mtc) IS _tau_for(0, mtc) → scale 1.0.
            tau_front = _tau_front(front_dte, minutes_to_close)
            if tau_day and tau_front and tau_front > 0:
                scale = math.sqrt(tau_day / tau_front)
        if scale is None:
            em_raw = None
        em_points = round(DEBIAS * em_raw * scale, 2) if em_raw is not None else None
        em_pct = round(em_points / float(spot), 5) if (em_points is not None and spot) else None
        if was_unanchored:
            em_open = em_points
        spent_pts = None
        do, dh, dl = _num(day_open), _num(day_high), _num(day_low)
        if do is not None and dh is not None and dl is not None:
            spent_pts = round(max(dh - do, do - dl), 2)
        em_consumed = None
        eo = _num(em_open)
        if spent_pts is not None and eo is not None and eo > 0:
            em_consumed = round(spent_pts / eo, 4)
        if em_points is None:
            quality = "missing"
        elif minutes_to_close is not None and float(minutes_to_close) < LATE_DAY_MIN:
            quality = "late_day"
        elif anchor == "straddle_mark":
            quality = "fallback"
        else:
            quality = "ok"
        return {"em_points": em_points, "em_pct": em_pct, "em_open": em_open,
                "em_consumed": em_consumed, "spent_pts": spent_pts,
                "anchor": (f"{anchor}_scaled" if (anchor and front_dte) else anchor),
                "front_dte": front_dte, "quality": quality,
                "event_flag": None, "vol_carry": None, "source": "native_chain"}
    except Exception:
        return {"em_points": None, "em_pct": None, "em_open": em_open,
                "em_consumed": None, "spent_pts": None, "anchor": None,
                "front_dte": front_dte, "quality": "missing",
                "event_flag": None, "vol_carry": None, "source": "native_chain"}


def _semivar_down_share(bars: Optional[list]) -> Optional[float]:
    """Realized downside-semivariance share off today's 1-min closes — the
    adaptive-EM asymmetry source (SNDK has no watchtower/speedometer). Needs a
    minimum sample so an open print can't fabricate asymmetry."""
    try:
        closes = [_num(b.get("close")) for b in bars or []]
        closes = [c for c in closes if c and c > 0]
        if len(closes) < 30:
            return None
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0]
        tot = sum(r * r for r in rets)
        if tot <= 0:
            return None
        return sum(r * r for r in rets if r < 0) / tot
    except Exception:
        return None


def build_row(contracts: list, spot: float, now: datetime, *,
              spot_source: str,
              chain_meta: Optional[dict] = None,
              chain_spot: Optional[float] = None,
              prev_rows: Optional[list] = None,
              prior_close: Optional[float] = None,
              day_open: Optional[float] = None,
              day_high: Optional[float] = None,
              day_low: Optional[float] = None,
              intraday_bars: Optional[list] = None,
              prev_close_totals: Optional[dict] = None,
              forced: bool = False) -> dict:
    """One SNDK diary row. Pure; never raises past its fail-open blocks.
    `forced` marks a row taken outside RTH via --force (m9) so it never pools
    silently with live rows."""
    mem = _prev_rows_scan(prev_rows)
    mtc = _gxb._minutes_to_close(now)

    # σ yardstick from the chain's own ATM IV (front book preferred — the
    # rebuilt quote-derived IV, never the provider's stale-spot garbage)
    front, front_dte = front_book(contracts)
    atm_iv = _gxb._atm_iv(front or contracts, spot)
    sigma_live = (round(spot * atm_iv / math.sqrt(_gxb._TRADING_DAYS), 4)
                  if (atm_iv and atm_iv > 0) else None)
    sigma = sigma_ruler(mem["sigma_anchor"], sigma_live)

    # the engine, verbatim: regime/flip read off the whole book (off-expiry the
    # dte==0 slice is empty and build_views falls back to the blended read —
    # named in regime_source, which is the honest description of a weekly book)
    v = _gxb.build_views(contracts, spot, minutes_to_close=mtc,
                         sigma=sigma_live, sigma_floor=mem["sigma_anchor"],
                         prev_pin=mem["prev_pin"], now=now)
    slides = v.get("slides") or {}
    C = slides.get("C_tenor") or {}

    # FRONT-BOOK slides: the same magnet/walls/pane math the SPX 0DTE book gets,
    # on SNDK's tradeable (front weekly) book — per-contract dte drives tau
    reach_sigma = sigma
    reach_hint = None
    try:
        if spot and reach_sigma:
            ds = [abs(w - spot) for w in (C.get("call_wall"), C.get("put_wall"))
                  if w is not None and abs(w - spot) <= 2.5 * reach_sigma]
            reach_hint = max(ds) if ds else None
    except (TypeError, ValueError):
        reach_hint = None
    B = _gxb.slide_0dte(front, spot, mtc, prev_pin=mem["prev_pin"],
                        sigma=reach_sigma, nbs_reach=reach_hint)

    magnet = B.get("pin") if B.get("pin") is not None else v.get("magnet")
    gamma_sign = {"long_gamma": "positive",
                  "short_gamma": "negative"}.get(v.get("regime_raw"), "unknown")

    gex_views = {
        "magnet": magnet,
        "flip": v.get("flip"), "flip_band": v.get("flip_band"),
        "flip_tenor": v.get("flip_tenor"),
        "regime": v.get("regime"), "regime_raw": v.get("regime_raw"),
        "regime_tenor": v.get("regime_tenor"),
        "regime_source": v.get("regime_source"),
        "regime_basis": v.get("regime_basis"),
        "net_gex": v.get("net_gex"), "net_gex_tenor": v.get("net_gex_tenor"),
        "gross_gex": v.get("gross_gex"),
        "vex": v.get("vex"), "cex": v.get("cex"),
        "vex_sign": v.get("vex_sign"), "cex_sign": v.get("cex_sign"),
        "shove": v.get("shove"),
        "source": "native",
        "front_dte": front_dte,                      # SNDK: which book slide B read
        # front-book magnet field + walls + panes (slide B, front-weekly book)
        "pin": B.get("pin"), "pin_field_v": B.get("pin_field_v"),
        "pin_legacy": B.get("pin_legacy"),
        "pin_held": B.get("pin_held"), "pin_candidate": B.get("pin_candidate"),
        "pin_zones": B.get("pin_zones"), "pin_contested": B.get("pin_contested"),
        "pin_argmax": B.get("pin_argmax"), "pin_blended": B.get("pin_blended"),
        "zone1_share": B.get("zone1_share"),
        "pin_total_gamma": B.get("pin_total_gamma"),
        "pin_top_share": B.get("pin_top_share"),
        "pin_basis": B.get("pin_basis"),
        "pin_abs": B.get("pin_abs"), "pin_abs_basis": B.get("pin_abs_basis"),
        "pin_centroid": B.get("pin_centroid"),
        "call_wall_gamma": B.get("call_wall_gamma"),
        "put_wall_gamma": B.get("put_wall_gamma"),
        "call_wall_gamma_share": B.get("call_wall_gamma_share"),
        "put_wall_gamma_share": B.get("put_wall_gamma_share"),
        "gamma_above_spot": B.get("gamma_above_spot"),
        "gamma_below_spot": B.get("gamma_below_spot"),
        "mass_by_strike": B.get("mass_by_strike"),
        "net_by_strike": B.get("net_by_strike"),
        "oi_by_strike": B.get("oi_by_strike"),
        "vol_by_strike": B.get("vol_by_strike"),
        "vol_gross_by_strike": B.get("vol_gross_by_strike"),
        "oi_side_by_strike": B.get("oi_side_by_strike"),
        "vol_side_by_strike": B.get("vol_side_by_strike"),
        "net_by_strike_tenor": C.get("net_by_strike"),
    }

    # DEX — the whole-book standing-delta surface (pure reuse)
    dex_views = None
    try:
        dex_views = _dex.dex_views(contracts, spot, minutes_to_close=mtc)
        if dex_views is not None:
            dex_views["source"] = "native"
    except Exception:
        dex_views = None

    # EM (front-weekly straddle, √time-scaled) + adaptive asymmetry split
    range_ruler = _em_read(front, front_dte, spot, mtc, mem["em_open"],
                           day_open, day_high, day_low)
    adaptive_em = None
    try:
        dsh = _semivar_down_share(intraday_bars)
        if dsh is not None:
            adaptive_em = _aem.read(range_ruler, spot, ticker="SNDK", now=now,
                                    speedometer={"quality": "cold",
                                                 "semivar_down_share": round(dsh, 4)})
    except Exception:
        adaptive_em = None

    # profile ladder — the GW-vocab named levels off the front-book surface
    profile_ladder = None
    try:
        profile_ladder = _pl.read(gex_views.get("net_by_strike"), spot, sigma,
                                  v.get("flip"))
    except Exception:
        profile_ladder = None

    # net-exposure tiles (SPX rule verbatim: blended net GEX + whole-book DEX)
    net_exposure = None
    try:
        ne_g = v.get("net_gex_tenor")
        ne_d = (dex_views or {}).get("net_dex_total")
        if ne_g is not None or ne_d is not None:
            prev = prev_close_totals or {}
            net_exposure = {"net_gamma_total": ne_g, "net_delta_total": ne_d,
                            "prev_close_gamma": prev.get("gamma"),
                            "prev_close_delta": prev.get("delta"),
                            "prev_close_date": prev.get("date"),
                            "net_exposure_v": 1}
    except Exception:
        net_exposure = None

    # regime word — the SPX voting stack with the voters SNDK actually has
    # (gamma + range_em; variance_ratio / vix_ts are SPX-only and abstain)
    reg = classify_regime(gamma_sign, range_ruler.get("em_consumed"), None, None)

    meta = {"spot_source": spot_source,
            "chain_spot": chain_spot,
            "expiries": (chain_meta or {}).get("expiries"),
            "coverage": (chain_meta or {}).get("coverage"),
            "chunks": (chain_meta or {}).get("chunks"),
            "window": (chain_meta or {}).get("window"),
            "discovery": (chain_meta or {}).get("discovery"),
            "book_source": (chain_meta or {}).get("book_source"),
            "book_asof": (chain_meta or {}).get("book_asof"),
            "cache_age_s": (chain_meta or {}).get("cache_age_s"),
            "iv_rebuilt": (chain_meta or {}).get("iv_rebuilt"),
            "iv_kept_provider": (chain_meta or {}).get("iv_kept_provider"),
            "iv_clamped": (chain_meta or {}).get("iv_clamped"),
            "iv_dropped": (chain_meta or {}).get("iv_dropped"),
            "forced": bool(forced),
            "row_v": ROW_V}

    return {
        "ts": now.isoformat(),
        "ticker": "SNDK",
        "spot": round(float(spot), 4),
        "gex_source": "native",
        "gamma_sign": gamma_sign,
        "gamma_flip": v.get("flip"),
        "call_wall": B.get("call_wall") if B.get("call_wall") is not None else C.get("call_wall"),
        "put_wall": B.get("put_wall") if B.get("put_wall") is not None else C.get("put_wall"),
        "call_wall_tenor": C.get("call_wall"),
        "put_wall_tenor": C.get("put_wall"),
        "sigma": sigma,
        "sigma_live": sigma_live,
        "sigma_anchor": mem["sigma_anchor"] or sigma_live,
        "prior_close": prior_close,
        "regime": reg["regime"],
        "regime_conf": reg["confidence"],
        "regime_reads": reg["reads"],
        "gex_views": gex_views,
        "dex_views": dex_views,
        "profile_ladder": profile_ladder,
        "adaptive_em": adaptive_em,
        "net_exposure": net_exposure,
        "range_ruler": range_ruler,
        "meta": meta,
    }
