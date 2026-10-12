"""
engine.py — the COMPOSER + the REPORT CARD of the box.

evaluate_once() folds one moment's sensor readings + baselines into the two
engine dicts the host records ("lob_flow" = tape (+) dial fused, "spy_depth" =
dial-only CONTROL) plus LayerViews for the future weighted stack. Stale input
windows are dropped at the door: a feed that died minutes ago must read as
"no opinion", never as a live one.

grade_day() is the package's own head-to-head card: it grades the module's
ACTUAL claims (scatter -> turbulence next 30m; defended/asserted pin -> price
holds near the strike) rather than borrowing anyone else's scoring. The
host's nightly grader additionally scores both engines pin/break in its own
ledger.

promotion_check() is the EARN-TRUST math (dormant): lob_flow must beat BOTH
the gravity-alone record AND the spy_depth control with non-overlapping
Wilson 90% intervals over >= 30 episodes each, before it is allowed even a
clamped whisper of confidence influence.

Both engines state a magnet for the host grader: lob_flow prefers its OWN
defended strike and only borrows the gravity magnet when scattering (source
annotated); spy_depth always borrows (it is the control asking "would the
boring futures-style book read have called the same pin/break?").
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .baselines import BaselineStore, key_of, session_block, tod_bin, vix_band
from .contracts import BlockInfo, LayerView, LobConfig, TradeEvent
from .sensors import fade_dial, purge_step, read_tape, refill_half_lives

# ---------------------------------------------------------------------------
# Compose one moment
# ---------------------------------------------------------------------------


def evaluate_once(now: datetime, cfg: LobConfig, *,
                  lob_bins: Sequence[dict],
                  tape_trades: Sequence[TradeEvent],
                  gex_strikes: Optional[dict],
                  spy_bins: Sequence[dict],
                  lob_store: BaselineStore,
                  spy_store: BaselineStore,
                  cal_block: BlockInfo,
                  vix: Optional[float],
                  purge_until: Optional[datetime] = None,
                  fed_until: Optional[str] = None,
                  deltas: Optional[dict] = None) -> dict:
    """One fold: raw bins + tape window -> engine dicts + baseline rows.

    lob_bins: recent chain bins, oldest->newest, each
        {"ts": iso, "buckets": {bucket: {"size":, "spread":, "n":}},
         "revisions_per_s": float|None, "purge_hint": {...}|absent}
    spy_bins: recent control bins, each {"ts": iso, "size":, "spread":}
    purge_until: sticky veto horizon from a previous fold's purge — a
        mass-cancel suppresses scatter for purge_suppress_min, not one bin.
    fed_until: newest bin ts already folded into the memory (a stalled sweep
        must not re-feed the same bin every fold).
    deltas: {"strike|right": delta} from the latest sweep (tape weighting).
    Returns ... plus "purge_until" and "fed_until" (the caller carries both)."""
    band = vix_band(vix)
    baseline_rows: list[dict] = []          # chain keys -> lob_flow store
    spy_baseline_rows: list[dict] = []      # control keys -> spy_depth store

    # ---- stale inputs are dropped at the door (fail-closed) ------------------
    lob_bins = _fresh(lob_bins, now, cfg)
    spy_bins = _fresh(spy_bins, now, cfg)

    # ---- score the chain bins against the normalcy memory -------------------
    scored: list[dict] = []
    new_fed_until = fed_until
    for b in lob_bins:
        ts = datetime.fromisoformat(b["ts"])
        blk = session_block(ts.hour * 60 + ts.minute, cfg)
        if blk is None:
            continue
        tod = tod_bin(ts)
        row: dict = {"ts": b["ts"], "buckets": {}}
        feeds = fed_until is None or b["ts"] > fed_until   # each bin feeds ONCE
        for bucket, m in (b.get("buckets") or {}).items():
            entry = {}
            for metric in ("size", "spread"):
                k = key_of(metric, bucket, blk, tod, band)
                v = m.get(metric)
                if v is not None and feeds:
                    baseline_rows.append({"key": k, "value": v})
                entry[f"{metric}_z"] = lob_store.score(k, v, now.date())["z"]
            row["buckets"][bucket] = entry
        if feeds:
            new_fed_until = max(new_fed_until or "", b["ts"])
        scored.append(row)

    # ---- purge signature ------------------------------------------------------
    # Prefer the MATCHED-CONTRACT hint (immune to bucket reshuffles); fall back
    # to bucket medians for hint-less bins. THE DISCRIMINATOR: a one-firm purge
    # leaves spreads ~normal (competitors hold the touch); a genuine panic
    # blows them out — spread-elevated steps are NOT classified as purges (that
    # would veto the single most valuable scatter alarm).
    spread_elevated = bool(scored) and any(
        (e.get("spread_z") or 0) >= cfg.spread_z_alert
        for e in scored[-1]["buckets"].values())
    step = False
    if lob_bins:
        hint = lob_bins[-1].get("purge_hint")
        prev = lob_bins[-2] if len(lob_bins) >= 2 else None
        adjacent = prev is not None and _adjacent_pair(prev, lob_bins[-1], cfg)
        if hint is not None and adjacent:
            step = hint.get("buckets_stepped", 0) >= cfg.purge_min_buckets
        elif adjacent:
            step = purge_step(_sizes(prev), _sizes(lob_bins[-1]), cfg)
    purge_now = step and not spread_elevated
    if purge_now:
        purge_until = now + timedelta(minutes=cfg.purge_suppress_min)
    elif purge_until is not None and scored:
        # EARLY LIFT: the book has visibly re-formed -> stop suppressing
        latest = scored[-1]["buckets"].values()
        zs = [e.get("size_z") for e in latest]
        if zs and all(z is not None and z > -cfg.size_z_alert for z in zs):
            purge_until = None
    purge_active = purge_now or (purge_until is not None and now < purge_until)

    # ---- tape + defense at the gravity strikes -------------------------------
    tape = read_tape(tape_trades, cfg, deltas)
    focus = _gravity_strikes(gex_strikes, cfg)
    defense = [refill_half_lives(tape_trades, s, cfg) for s in focus]

    dial = fade_dial(scored, defense, purge_active, cal_block.blocked, cfg)

    # ---- noise proxy ----------------------------------------------------------
    rev = lob_bins[-1].get("revisions_per_s") if lob_bins else None
    noise_z = None
    if rev is not None and scored:
        ts = datetime.fromisoformat(lob_bins[-1]["ts"])
        blk = session_block(ts.hour * 60 + ts.minute, cfg)
        if blk:
            k = key_of("noise", "chain", blk, tod_bin(ts), band)
            if fed_until is None or lob_bins[-1]["ts"] > fed_until:
                baseline_rows.append({"key": k, "value": rev})
            noise_z = lob_store.score(k, rev, now.date())["z"]

    # ---- regimes --------------------------------------------------------------
    # TURBULENCE = size-scatter OR persistent spread blowout: on SPX a scared
    # LMM (mandated minimum size) widens at least as often as it pulls.
    # DEFENSE never asserts a regime late in the session — post-15:15 charm/
    # pin mechanics make both verdicts unreliable, and +60min grading
    # self-censors past the close.
    gex_magnet = (gex_strikes or {}).get("magnet")
    turbulence = dial["scatter"] or dial["spread_alert"]
    late = (now.hour * 60 + now.minute) >= cfg.defense_cutoff_min
    if dial["veto"]:
        regime, magnet, magnet_src = None, None, None
    elif dial["defended_magnet"] is not None and not turbulence and not late:
        regime, magnet, magnet_src = "long_gamma", dial["defended_magnet"], "defense"
    elif turbulence:
        regime, magnet, magnet_src = "short_gamma", gex_magnet, "gravity"
    else:
        regime, magnet, magnet_src = None, None, None

    label = lob_store.label
    det = tape["determinate_share"]
    lob_conf = _confidence(label, len(scored), dial["veto"])
    if det is not None:
        # a tilt resting on 5% of the tape carries 5%-grade conviction
        lob_conf = round(lob_conf * (0.3 + 0.7 * det), 2)
    lob = {"regime": regime, "magnet": magnet, "magnet_source": magnet_src,
           "tilt": tape["tilt"], "tape_gross": tape["gross_delta_weight"],
           "tape_trades": tape["n_trades"],
           "determinate_share": det,
           "buckets": scored[-1]["buckets"] if scored else {},
           "scatter": dial["scatter"], "scatter_breadth": dial["scatter_breadth"],
           "scatter_buckets": dial["scatter_buckets"],
           "spread_alert": dial["spread_alert"],
           "turbulence": turbulence, "late_session": late,
           "defense": dial["defense"],
           "noise_proxy": rev, "noise_z": noise_z,
           "purge_flag": purge_active, "calendar_blocked": cal_block.blocked,
           "baseline_label": label, "baseline_days": lob_store.clean_days,
           "source": "lob_flow"}

    # ---- the control ------------------------------------------------------------
    spy = _control_engine(now, cfg, spy_bins, spy_store, band, cal_block,
                          gex_magnet, spy_baseline_rows)

    views = [
        LayerView(layer="lob_flow", ts=now.isoformat(),
                  bias=tape["tilt"], confidence=lob_conf, weight=0.0,
                  veto=dial["veto"],
                  annotations={"scatter": dial["scatter"],
                               "defended_magnet": dial["defended_magnet"],
                               "determinate_share": tape["determinate_share"],
                               "baseline": label}),
        LayerView(layer="spy_depth", ts=now.isoformat(),
                  bias=0.0, confidence=_confidence(spy_store.label,
                                                   len(spy_bins), False),
                  weight=0.0, veto=cal_block.blocked,
                  annotations={"scatter": spy.get("scatter")}),
    ]
    return {"lob_flow": lob, "spy_depth": spy, "layer_views": views,
            "purge_until": purge_until, "fed_until": new_fed_until,
            "baseline_rows": {"lob_flow": baseline_rows,
                              "spy_depth": spy_baseline_rows}}


def _fresh(bins: Sequence[dict], now: datetime, cfg: LobConfig) -> list[dict]:
    """Drop bins older than fresh_window_factor x quote_sweep_s — a dead feed
    must read as 'no bins' (no opinion), not as a frozen live picture."""
    max_age = cfg.fresh_window_factor * cfg.quote_sweep_s
    out = []
    for b in bins:
        try:
            age = (now - datetime.fromisoformat(b["ts"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= age <= max_age:
            out.append(b)
    return out


def _adjacent_pair(a: dict, b: dict, cfg: LobConfig) -> bool:
    """A purge is a STEP within ~one sweep interval; two bins straddling a
    feed gap can fake a step out of a slow bleed (and veto real scatter)."""
    try:
        gap = (datetime.fromisoformat(b["ts"])
               - datetime.fromisoformat(a["ts"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return 0 <= gap <= cfg.bin_adjacency_factor * cfg.quote_sweep_s


def _sizes(b: dict) -> dict:
    return {k: {"size": (v or {}).get("size")}
            for k, v in (b.get("buckets") or {}).items()}


def _gravity_strikes(gex: Optional[dict], cfg: LobConfig) -> list[float]:
    """SNAPPED to the strike grid — a gamma-weighted magnet of 6037.5 would
    float-match zero trades and silently kill the defense test forever."""
    if not gex:
        return []
    seen = []
    for k in ("magnet", "call_wall", "put_wall"):
        v = gex.get(k)
        if v is None:
            continue
        s = round(float(v) / cfg.strike_step) * cfg.strike_step
        if s not in seen:
            seen.append(s)
    return seen


def _confidence(label: str, n_bins: int, veto: bool) -> float:
    base = {"robust": 1.0, "percentile": 0.6, "cold": 0.3}.get(label, 0.3)
    if n_bins < 2:
        base *= 0.5
    if veto:
        base *= 0.5
    return round(base, 2)


def _control_engine(now: datetime, cfg: LobConfig, spy_bins: Sequence[dict],
                    store: BaselineStore, band: str, cal_block: BlockInfo,
                    gex_magnet: Optional[float],
                    baseline_rows: list[dict]) -> dict:
    """spy_depth — dial-only scatter/huddle on the control instrument's
    top-of-book. Always borrows the gravity magnet: it exists to answer
    'would the boring proven book-read have said the same thing?'. Says
    nothing during a calendar block (same manners as the main dial)."""
    scored: list[dict] = []
    for b in spy_bins:
        ts = datetime.fromisoformat(b["ts"])
        blk = session_block(ts.hour * 60 + ts.minute, cfg)
        if blk is None:
            continue
        tod = tod_bin(ts)
        entry = {}
        for metric in ("size", "spread"):
            k = key_of(metric, "spy", blk, tod, band)
            v = b.get(metric)
            if v is not None:
                baseline_rows.append({"key": k, "value": v})
            entry[f"{metric}_z"] = store.score(k, v, now.date())["z"]
        scored.append(entry)

    window = scored[-max(cfg.scatter_min_bins, 2):]
    enough = len(window) >= cfg.scatter_min_bins
    scatter = (enough and not cal_block.blocked and
               all((w.get("size_z") is not None and
                    w["size_z"] <= -cfg.size_z_alert) for w in window))
    if cal_block.blocked:
        regime = None
    elif scatter:
        regime = "short_gamma"
    elif enough and all(w.get("size_z") is not None for w in window):
        regime = "long_gamma"
    else:
        regime = None
    return {"regime": regime, "magnet": gex_magnet if regime else None,
            "magnet_source": "gravity" if regime else None,
            "scatter": scatter,
            "size_z": (window[-1].get("size_z") if window else None),
            "spread_z": (window[-1].get("spread_z") if window else None),
            "calendar_blocked": cal_block.blocked,
            "baseline_label": store.label, "baseline_days": store.clean_days,
            "source": "spy_depth"}


# ---------------------------------------------------------------------------
# Wilson interval (the earn-trust math)
# ---------------------------------------------------------------------------


def wilson_bounds(hits: float, misses: float, z: float = 1.645) -> tuple[float, float]:
    n = hits + misses
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


# ---------------------------------------------------------------------------
# The package's own report card
# ---------------------------------------------------------------------------


def grade_day(day: str, rows: Sequence[dict], bars: Sequence[dict],
              cfg: LobConfig) -> dict:
    """Grade the module's OWN claims for one day:
      * scatter call -> did realized range in the next 30m reach
                        grade_scatter_sigma x sigma? (turbulence claim)
      * pin call     -> was price still within grade_pin_sigma x sigma of the
                        stated magnet ~60m later? (lob_flow grades its
                        defended strikes; the control grades its borrowed-
                        gravity pins — that head-to-head is its entire job)
    Consecutive identical calls collapse into one episode (first scan wins).
    bars: [{"ts": iso, "high": f, "low": f}] for the day."""
    path = []
    for b in bars:
        try:
            path.append((datetime.fromisoformat(b["ts"]), b["high"], b["low"]))
        except (KeyError, ValueError, TypeError):
            continue
    path.sort(key=lambda x: x[0])

    card = {"day": day, "engines": {}}
    for engine in ("lob_flow", "spy_depth"):
        scatter_eps = _episodes(rows, engine,
                                lambda e: bool(e.get("turbulence")
                                               or e.get("scatter")))
        if engine == "lob_flow":
            pin_pred = (lambda e: e.get("regime") == "long_gamma"
                        and e.get("magnet_source") == "defense")
        else:
            pin_pred = lambda e: e.get("regime") == "long_gamma"
        defense_eps = _episodes(rows, engine, pin_pred)
        sc = _grade_eps(scatter_eps, path, cfg,
                        lambda r, p, c: _scatter_hit(r, p, c))
        df = _grade_eps(defense_eps, path, cfg,
                        lambda r, p, c, eng=engine: _pin_hit(r, eng, p, c))
        card["engines"][engine] = {"scatter": sc, "defense": df}
    return card


def _episodes(rows: Sequence[dict], engine: str, pred) -> list[dict]:
    eps, prev = [], False
    for r in sorted(rows, key=lambda x: x.get("ts") or ""):
        e = r.get(engine) or {}
        cur = bool(pred(e))
        if cur and not prev:
            eps.append(r)
        prev = cur
    return eps


def _grade_eps(eps: list[dict], path: list, cfg: LobConfig, hit_fn) -> dict:
    n = hits = 0
    for r in eps:
        h = hit_fn(r, path, cfg)
        if h is None:
            continue
        n += 1
        hits += 1 if h else 0
    out = {"n": n, "hits": hits}
    if n:
        lo, hi = wilson_bounds(hits, n - hits)
        out.update({"rate": round(hits / n, 2),
                    "wilson_lo": round(lo, 3), "wilson_hi": round(hi, 3)})
    return out


def _window(path: list, t0: datetime, minutes: int) -> list:
    end = t0 + timedelta(minutes=minutes)
    return [(bt, hi, lo) for bt, hi, lo in path if t0 <= bt <= end]


def _scatter_hit(r: dict, path: list, cfg: LobConfig) -> Optional[bool]:
    """Turbulence claim: range after the call must beat what a plain
    diffusion would deliver in the same window — the bar scales with
    sigma x sqrt(window/session), else a stopped clock grades ~50% at 10am
    and 0% at 3pm."""
    sigma = r.get("sigma")
    try:
        t0 = datetime.fromisoformat(r["ts"])
    except (KeyError, ValueError, TypeError):
        return None
    if not sigma:
        return None
    w = _window(path, t0, cfg.grade_scatter_window_min)
    if not w:
        return None
    rng = max(h for _, h, _ in w) - min(l for _, _, l in w)
    bar = cfg.grade_scatter_range_mult * sigma * math.sqrt(
        cfg.grade_scatter_window_min / 390.0)
    return rng >= bar


def _pin_hit(r: dict, engine: str, path: list, cfg: LobConfig) -> Optional[bool]:
    """Level-hold claim: price near the ENGINE'S OWN stated magnet ~60m later."""
    e = r.get(engine) or {}
    magnet, sigma = e.get("magnet"), r.get("sigma")
    try:
        t0 = datetime.fromisoformat(r["ts"])
    except (KeyError, ValueError, TypeError):
        return None
    if not (magnet and sigma):
        return None
    end = t0 + timedelta(minutes=cfg.grade_pin_window_min)
    w = [(bt, hi, lo) for bt, hi, lo in path
         if end - timedelta(minutes=10) <= bt <= end + timedelta(minutes=10)]
    if not w:
        return None
    dist = min(0.0 if lo <= magnet <= hi else min(abs(hi - magnet), abs(lo - magnet))
               for _, hi, lo in w)
    return dist <= cfg.grade_pin_sigma * sigma


# ---------------------------------------------------------------------------
# Promotion math (DORMANT — nothing calls this on the trading path)
# ---------------------------------------------------------------------------


def promotion_check(history_rows: Sequence[dict], cfg: LobConfig,
                    gex_engine: str = "gex_views") -> tuple[bool, str]:
    """lob_flow earns a whisper ONLY when its pooled host-graded record beats
    BOTH gravity-alone and the spy_depth control with non-overlapping Wilson
    90% intervals, each side >= promote_min_episodes. Prefers exact 'hits'
    when the ledger carries them (rounded rates drift by one hit at scale).
    Fail-closed on any missing/corrupt data. The CALLER must pre-filter to
    real-range (ohlc) bar days — point-bar grades are estimates, not evidence."""
    pooled: dict[str, list[int]] = {}
    for rec in history_rows:
        for eng in ("lob_flow", "spy_depth", gex_engine):
            o = ((rec.get("engines") or {}).get(eng) or {}).get("overall") or {}
            n, rate = o.get("n"), o.get("current_hit_rate")
            if not n:
                continue
            hits = o.get("hits")
            if hits is None:
                if rate is None:
                    continue
                hits = round(rate * n)
            acc = pooled.setdefault(eng, [0, 0])
            acc[0] += hits
            acc[1] += n - hits
    for eng in ("lob_flow", "spy_depth", gex_engine):
        n = sum(pooled.get(eng, [0, 0]))
        if n < cfg.promote_min_episodes:
            return False, (f"record too thin: {eng} has {n}/"
                           f"{cfg.promote_min_episodes} graded episodes")
    lob_lo, _ = wilson_bounds(*pooled["lob_flow"], z=cfg.wilson_z)
    _, spy_hi = wilson_bounds(*pooled["spy_depth"], z=cfg.wilson_z)
    _, gex_hi = wilson_bounds(*pooled[gex_engine], z=cfg.wilson_z)
    if lob_lo <= spy_hi:
        return False, (f"does not beat the control: lob Wilson floor {lob_lo:.2f}"
                       f" <= spy_depth ceiling {spy_hi:.2f}")
    if lob_lo <= gex_hi:
        return False, (f"does not beat gravity alone: lob Wilson floor {lob_lo:.2f}"
                       f" <= {gex_engine} ceiling {gex_hi:.2f}")
    return True, (f"beats control and gravity: lob floor {lob_lo:.2f} > "
                  f"spy ceiling {spy_hi:.2f} and {gex_engine} ceiling {gex_hi:.2f}")


def confidence_modifier(promoted: bool, scatter: bool, defended: bool,
                        cfg: LobConfig) -> float:
    """The ONLY influence this box may ever have, clamped hard: a nudge to the
    host's confidence — never direction, strike, or magnet. Returns 1.0 unless
    promoted (dormant)."""
    lo, hi = cfg.confidence_clamp
    if not promoted:
        return 1.0
    if scatter:
        return lo
    if defended:
        return hi
    return 1.0


# (No CLI here on purpose: the nightly card is driven by the host's bridge —
#  a second entry point over the same card file would just invite a partial
#  manual grade to clobber the real one.)
