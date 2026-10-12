"""
sensors.py — the EYES of the box. Three pure sensors, no I/O anywhere:

  * TapeReader  ("Who's eating") — signs each minute's trades per contract by
    where their VWAP sits inside the prevailing quote (the sim-proven
    VWAP-in-spread rule) and folds the DELTA-WEIGHTED signed flow into one
    tilt in [-1, +1]. Premium dollars measure vega; hedge pressure scales
    with delta x contracts — the tilt weights by the latter.
  * FadeDial    ("Are the monkeys fleeing or huddling") — scatter = resting
    size being pulled and STAYING pulled (>= 2 time-adjacent bins, breadth
    across buckets); huddle = size rebuilding fast AND AT PRICE at a FIXED
    gravity strike after trades eat it (refill half-life, per right — call
    and put books are different animals; size re-posted a tick worse is
    polite abandonment, not defense).
  * purge signature — an all-bucket simultaneous step across MATCHED
    contracts is a mass-cancel / risk-monitor trip: mechanical, veto not
    signal. (Bucket-median steps can be faked by a violent spot move
    reshuffling bucket membership; matched-contract steps cannot.)

Complex-order defense: multi-leg packages print inside the NBBO without
consuming displayed size. Events sharing a millisecond across >= 2 distinct
contracts (the package signature) are excluded from eat detection and tape
signing; eats additionally require the print AT the touch, not merely
above/below mid.

Everything consumes frozen dataclasses from contracts.py and returns plain
dicts; the engine composes them, the daemon feeds them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from .contracts import LobConfig, QuoteRow, TradeEvent

OPTION_MULTIPLIER = 100        # market constant, not a knob
DEFAULT_DELTA = 0.5            # weight for strikes the sweep hasn't priced

# ---------------------------------------------------------------------------
# Moneyness bucketing (per-sweep deltas — never stale ones)
# ---------------------------------------------------------------------------


def bucket_of(delta: Optional[float], cfg: LobConfig) -> Optional[str]:
    if delta is None:
        return None
    a = abs(delta)
    last = cfg.delta_buckets[-1][0]
    for name, lo, hi in cfg.delta_buckets:
        # the top bucket is CLOSED at its ceiling: dead-ATM |delta| == 0.50
        # is the most-watched strike on the chain, not an out-of-range reject
        if lo <= a < hi or (name == last and a == hi):
            return name
    return None


def aggregate_sweep(quotes: Sequence[QuoteRow], prev_fingerprint: Optional[dict],
                    cfg: LobConfig) -> dict:
    """One chain sweep -> per-bucket medians + a quote fingerprint + the
    matched-contract purge hint. Metrics per bucket (None when fewer than
    bucket_min_contracts contribute — one lonely contract drives nothing):
        size / bid_size / ask_size = median resting size at touch
        spread = median $ spread (ask - bid)
        n      = contracts contributing
    Revisions = contracts whose quote changed since the previous sweep (the
    chain-wide PROXY for quote-update intensity).
    purge_hint = {"buckets_stepped": k, "matched": n} — buckets where >=
    purge_drop_frac of MATCHED contracts stepped down by >= purge_drop_frac
    (matched by contract id, immune to bucket-membership reshuffles)."""
    per: dict[str, dict[str, list[float]]] = {}
    fingerprint: dict[str, tuple] = {}
    deltas: dict[str, float] = {}
    stepped: dict[str, list[bool]] = {}
    revisions = 0
    for q in quotes:
        b = bucket_of(q.delta, cfg)
        cid = f"{q.expiry}|{q.right}|{q.strike}"
        fp = (q.bid, q.ask, q.bid_size, q.ask_size)
        prev = prev_fingerprint.get(cid) if prev_fingerprint else None
        fingerprint[cid] = fp
        if q.delta is not None:
            # float()ed so the key always matches TradeEvent.strike's form
            deltas[f"{float(q.strike)}|{q.right}"] = q.delta
        if prev_fingerprint is not None and prev is not None and prev != fp:
            revisions += 1
        if b is None:
            continue
        if prev is not None and prev[2] is not None and prev[3] is not None:
            p_size = prev[2] + prev[3]
            c_size = ((q.bid_size or 0) + (q.ask_size or 0))
            if p_size > 0:
                stepped.setdefault(b, []).append(
                    (p_size - c_size) / p_size >= cfg.purge_drop_frac)
        slot = per.setdefault(b, {"size": [], "bid_size": [], "ask_size": [],
                                  "spread": []})
        if q.bid_size is not None and q.ask_size is not None:
            slot["size"].append(float(q.bid_size + q.ask_size))
            slot["bid_size"].append(float(q.bid_size))
            slot["ask_size"].append(float(q.ask_size))
        if q.bid is not None and q.ask is not None and q.ask >= q.bid:
            slot["spread"].append(float(q.ask - q.bid))

    def med(v: list[float]) -> Optional[float]:
        return sorted(v)[len(v) // 2] if v else None

    buckets = {}
    for b, v in per.items():
        n = len(v["size"])
        if n < cfg.bucket_min_contracts:
            buckets[b] = {"size": None, "bid_size": None, "ask_size": None,
                          "spread": None, "n": n}
        else:
            buckets[b] = {"size": med(v["size"]), "bid_size": med(v["bid_size"]),
                          "ask_size": med(v["ask_size"]),
                          "spread": med(v["spread"]), "n": n}
    hint = {"buckets_stepped": sum(
                1 for b, flags in stepped.items()
                if len(flags) >= cfg.bucket_min_contracts
                and sum(flags) / len(flags) >= cfg.purge_drop_frac),
            "matched": sum(len(f) for f in stepped.values())}
    return {"buckets": buckets, "fingerprint": fingerprint, "deltas": deltas,
            "revisions": revisions, "purge_hint": hint}


# ---------------------------------------------------------------------------
# Complex-package detection (shared by tape + refill)
# ---------------------------------------------------------------------------


def package_cluster_ms(trades: Sequence[TradeEvent]) -> set:
    """Milliseconds where >= 2 DISTINCT contracts printed simultaneously —
    the multi-leg package signature. Legs price inside the NBBO and consume
    no displayed size; both the eat detector and the signer must skip them."""
    seen: dict[int, set] = {}
    for t in trades:
        seen.setdefault(t.ts_ms, set()).add((t.strike, t.right))
    return {ms for ms, contracts in seen.items() if len(contracts) >= 2}


# ---------------------------------------------------------------------------
# TapeReader — VWAP-in-spread signed flow, delta-weighted, minute-bucketed
# ---------------------------------------------------------------------------


def _spread_position(vwap: float, mid: float, half: float) -> Optional[float]:
    if half <= 0:
        return None
    return max(-1.0, min(1.0, (vwap - mid) / half))


def read_tape(trades: Sequence[TradeEvent], cfg: LobConfig,
              deltas: Optional[dict] = None) -> dict:
    """Sign the tape. Per (strike, right, MINUTE): bucket VWAP vs the
    size-weighted prevailing quote mid; position in the half-spread is the
    sign strength. Minute sub-buckets keep a 15-minute two-way battle from
    VWAP-ing itself to an indeterminate mid. NEVER re-sorted by exchange
    timestamps (the OPRA sequencing defense) — each trade carries its own
    quote-at-print. Package-cluster prints are excluded before anything.

    Weighting: |delta| x contracts (hedge pressure), not premium dollars
    (vega). deltas: {"strike|right": delta} from the latest sweep; unknown
    contracts weight at DEFAULT_DELTA.

    Direction convention: buying calls / selling puts = bullish (+).
    determinate_share reports how much of the (delta-weighted) flow the tilt
    actually rests on — a tilt built on 5% of the tape says so."""
    deltas = deltas or {}
    clusters = package_cluster_ms(trades)
    groups: dict[tuple, list[TradeEvent]] = {}
    n_dropped = n_cluster = 0
    for t in trades:
        if t.ts_ms in clusters:
            n_cluster += 1
            continue
        if t.price and t.size and t.bid is not None and t.ask is not None:
            groups.setdefault((t.strike, t.right, t.ts_ms // 60_000), []).append(t)
        else:
            n_dropped += 1

    signed = gross_det = gross_all = 0.0
    per_strike: dict[float, float] = {}
    n_ind = 0
    for (strike, right, _minute), evs in groups.items():
        contracts = sum(t.size for t in evs)
        if contracts <= 0:
            continue
        d = abs(deltas.get(f"{strike}|{right}", DEFAULT_DELTA)) or DEFAULT_DELTA
        weight = d * contracts * OPTION_MULTIPLIER      # share-equivalents
        gross_all += weight
        vwap = sum(t.price * t.size for t in evs) / contracts
        mid = sum(((t.bid + t.ask) / 2) * t.size for t in evs) / contracts
        half = sum(((t.ask - t.bid) / 2) * t.size for t in evs) / contracts
        pos = _spread_position(vwap, mid, half)
        if pos is None or abs(pos) < cfg.indeterminate_pos:
            n_ind += len(evs)
            continue
        side = 1.0 if right == "call" else -1.0      # bought call +, bought put -
        contribution = side * pos * weight
        signed += contribution
        gross_det += weight
        per_strike[strike] = per_strike.get(strike, 0.0) + contribution

    tilt = (signed / gross_det) if gross_det > 0 else 0.0
    if gross_det > 0:
        per_strike = {k: round(v / gross_det, 4) for k, v in per_strike.items()}
    return {"tilt": round(max(-1.0, min(1.0, tilt)), 4),
            "gross_delta_weight": round(gross_det, 0),
            "determinate_share": (round(gross_det / gross_all, 3)
                                  if gross_all > 0 else None),
            "per_strike": per_strike,
            "n_trades": sum(len(v) for v in groups.values()),
            "n_indeterminate": n_ind,
            "n_cluster_excluded": n_cluster,
            "n_dropped": n_dropped}


# ---------------------------------------------------------------------------
# Refill defense test — huddle measured at FIXED gravity strikes, event-time
# ---------------------------------------------------------------------------


def _tick(price: float) -> float:
    """SPX option tick regime: $0.05 under $3 premium, $0.10 at/above."""
    return 0.05 if price < 3.0 else 0.10


def _eaten_side(t: TradeEvent) -> Optional[str]:
    """Which side's resting size did this print consume. STRICT: only prints
    AT the touch qualify ('ask' = at/through the ask, 'bid' = at/through the
    bid) — inside-the-spread prints are package legs or price improvement and
    consumed no displayed size. Refill bookkeeping ONLY; the signed tilt
    never classifies per trade."""
    if t.bid is None or t.ask is None or t.ask < t.bid:
        return None
    if t.price >= t.ask:
        return "ask"
    if t.price <= t.bid:
        return "bid"
    return None


def _side_size(t: TradeEvent, side: str) -> Optional[int]:
    return t.ask_size if side == "ask" else t.bid_size


def _side_price(t: TradeEvent, side: str) -> Optional[float]:
    return t.ask if side == "ask" else t.bid


def refill_half_lives(events: Sequence[TradeEvent], strike: float,
                      cfg: LobConfig) -> dict:
    """At ONE fixed strike: after each trade that visibly ate resting size,
    how long until that side's size recovered to >= recover_frac of what
    stood before — AT THE SAME PRICE (within one tick)? Size re-posted a
    tick or two worse is the canonical polite retreat, not defense. Measured
    purely event-time from quote-at-print records, so poll latency does not
    blur it. The FIXED strike is the point: auto-quoters rebuilding around
    spot look like a huddle at every price (the re-centering trap);
    rebuilding HERE, at price, after being traded through is real defense.

    Calls and puts are SEPARATE books — bookkeeping runs per right. Each
    recovery event closes at most ONE eat. Package-cluster prints are
    excluded entirely.

    verdict: defended (median half-life <= refill_defended_s, n >= min_events)
           | abandoned (recoveries mostly absent/slow) | thin (not enough data)."""
    clusters = package_cluster_ms(events)
    half_lives: list[float] = []
    unrecovered = 0
    per_right: dict[str, str] = {}
    for right in ("call", "put"):
        evs = sorted((e for e in events
                      if e.strike == strike and e.right == right
                      and e.ts_ms not in clusters),
                     key=lambda e: e.ts_ms)
        hl, unrec = _refill_one_book(evs, cfg)
        half_lives.extend(hl)
        unrecovered += unrec
        if hl or unrec:
            per_right[right] = f"{len(hl)} recovered / {unrec} not"
    n = len(half_lives)
    out = {"strike": strike, "n_events": n, "n_unrecovered": unrecovered,
           "per_right": per_right,
           "refill_half_life_s": (round(sorted(half_lives)[n // 2], 1) if n else None)}
    if n + unrecovered < cfg.refill_min_events:
        out["verdict"] = "thin"
    elif n >= cfg.refill_min_events and out["refill_half_life_s"] is not None \
            and out["refill_half_life_s"] <= cfg.refill_defended_s \
            and n > unrecovered:
        out["verdict"] = "defended"
    else:
        out["verdict"] = "abandoned"
    return out


def _refill_one_book(evs: list[TradeEvent], cfg: LobConfig) -> tuple[list[float], int]:
    """One right's book at one strike -> (recovery half-lives, unrecovered
    count). Recovery = size back to >= recover_frac of pre-eat AND the
    re-posted price within one tick of the pre-eat price (or better). A
    shared cursor guarantees each later event closes at most one eat."""
    half_lives: list[float] = []
    unrecovered = 0
    next_free = 0                       # first event index not yet used as a recovery
    for i, t in enumerate(evs):
        side = _eaten_side(t)
        if side is None:
            continue
        before = _side_size(t, side)
        price_before = _side_price(t, side)
        if not before or before <= 0 or price_before is None \
                or t.size < before * cfg.refill_min_bite_frac:
            continue                    # too small a bite to measure recovery
        target = before * cfg.refill_recover_frac
        tick = _tick(price_before)
        recovered = False
        for j in range(max(i + 1, next_free), len(evs)):
            level = _side_size(evs[j], side)
            price = _side_price(evs[j], side)
            if level is None or level < target or price is None:
                continue
            # "at price": ask re-posted no more than a tick HIGHER, bid no
            # more than a tick LOWER, than where it stood before the eat
            worse = (price - price_before) if side == "ask" \
                else (price_before - price)
            if worse > tick + 1e-9:
                continue                # size is back but the price walked away
            half_lives.append((evs[j].ts_ms - t.ts_ms) / 1000.0)
            next_free = j + 1           # this rebuild is spent
            recovered = True
            break
        if not recovered:
            unrecovered += 1
    return half_lives, unrecovered


# ---------------------------------------------------------------------------
# Purge signature (legacy bucket-median fallback for hint-less bins)
# ---------------------------------------------------------------------------


def purge_step(prev_bins: Optional[dict], cur_bins: dict, cfg: LobConfig) -> bool:
    """FALLBACK detector over bucket medians for bins that carry no
    matched-contract purge_hint (replayed old journals). Prefer the hint:
    median steps can be faked by a violent move reshuffling bucket
    membership; matched contracts cannot."""
    if not prev_bins:
        return False
    dropped = 0
    for b, prev in prev_bins.items():
        p = (prev or {}).get("size")
        if not p or p <= 0:
            continue
        c = (cur_bins.get(b) or {}).get("size")
        if c is None or (p - c) / p >= cfg.purge_drop_frac:
            dropped += 1
    return dropped >= cfg.purge_min_buckets


# ---------------------------------------------------------------------------
# FadeDial — scatter/huddle over a window of scored bins
# ---------------------------------------------------------------------------


def _adjacent(rows: list[dict], cfg: LobConfig) -> bool:
    """Persistence only counts across bins that are actually consecutive in
    time — a breach before a feed gap and another after it are two blips,
    not one sustained signal."""
    max_gap = cfg.bin_adjacency_factor * cfg.quote_sweep_s
    for a, b in zip(rows, rows[1:]):
        try:
            gap = (datetime.fromisoformat(b["ts"])
                   - datetime.fromisoformat(a["ts"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return False
        if gap < 0 or gap > max_gap:
            return False
    return True


def fade_dial(scored_bins: Sequence[dict], defense: Sequence[dict],
              purge_flag: bool, calendar_blocked: bool, cfg: LobConfig) -> dict:
    """The dial itself, over the last few SCORED bins (each: {"ts": iso,
    "buckets": {bucket: {"size_z":, "spread_z":}}}). The SIGNAL is
    persistence: z at/beyond threshold in the SAME bucket for >=
    scatter_min_bins time-adjacent bins — for BOTH dials. On SPX the spread
    dial matters as much as size: mandated minimum-size quoting means a
    scared MM often widens at minimum size rather than pulling it. A purge
    or calendar block vetoes outright.

    Returns {scatter, scatter_breadth, scatter_buckets, spread_alert,
             defense: {str(strike): verdict...}, defended_magnet, veto}."""
    window = list(scored_bins)[-max(cfg.scatter_min_bins, 2):]
    scatter_buckets: list[str] = []
    spread_alert = False
    if len(window) >= cfg.scatter_min_bins and _adjacent(window, cfg):
        names = {b for row in window for b in row.get("buckets", {})}
        for b in sorted(names):
            size_zs = [row.get("buckets", {}).get(b, {}).get("size_z")
                       for row in window]
            if all(z is not None and z <= -cfg.size_z_alert for z in size_zs):
                scatter_buckets.append(b)
            spread_zs = [row.get("buckets", {}).get(b, {}).get("spread_z")
                         for row in window]
            if all(z is not None and z >= cfg.spread_z_alert for z in spread_zs):
                spread_alert = True
    veto = purge_flag or calendar_blocked
    scatter = bool(scatter_buckets) and not veto

    defended = [d for d in defense if d.get("verdict") == "defended"]
    best = None
    if defended:
        best = min(defended,
                   key=lambda d: d.get("refill_half_life_s") or 1e9)["strike"]
    return {"scatter": scatter,
            "scatter_breadth": len(scatter_buckets),
            "scatter_buckets": scatter_buckets,
            "spread_alert": spread_alert and not veto,
            # str keys: these dicts ride through JSON, where float keys mutate
            "defense": {str(d["strike"]): d for d in defense},
            "defended_magnet": best,
            "veto": veto}
