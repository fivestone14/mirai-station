"""lefteye_gamma_roll.py — SHADOW gamma-roll lens over the UW Periscope v2 mark.

NOT ACTIVE IN THIS BUILD: the vendor feed that writes uw_mark.json is not
included here, so this lens finds no mark and returns its "no data" stub on
every tick. It is kept because the shape of the question it asks — does the
0DTE wall sit somewhere different from the structural wall? — is reusable
against any per-strike, per-expiry gamma source.

Pure, READ-ONLY consumer of state/gex_uw/uw_mark.json (mark_version >= 2). It
never fetches, never writes, never touches the live GEX map. One question only:
does the 0DTE gamma wall sit somewhere DIFFERENT from the 8-45 DTE wall near
spot? When the two walls diverge, the pin the 0DTE book defends today is not the
level the structural book defends after today's expiry burns off — the magnet
can "teleport" at the roll. Output is a small telemetry record for diary/
Watchtower consumption; quiet days emit a 3-field stub, gated/blind ticks None.

FIX #1 (age gate is the real protection): the on-disk mark never persists
stale=True — uw_periscope.ensure_mark returns the stale flag on an IN-MEMORY
copy only ({**prev, "stale": True}) and the written mark always carries
stale=False. A reader of the file therefore cannot trust the stale field; the
(now_ms - grid_ms) <= MAX_AGE_MS gate is what actually rejects a dead feed.

FIX #2 (tick ordering): where the feed was wired, the mark was refreshed AFTER
reversion.evaluate(), so anything evaluate-side read the PREVIOUS grid bucket.
MAX_AGE_MS = 25 min deliberately covers 2 grid buckets (20 min) plus the ~3-min
publish lag, so a healthy feed always passes even one bucket behind. Any future
writer of uw_mark.json inherits that constraint.

FIX #3 (independent noisy estimates): bucket signs are NEVER netted or summed
across buckets — UW's flow-inferred sign per DTE slice is an independent noisy
estimate; netting would launder disagreement into a fake consensus. We only
count agreement/disagreement per strike (sign_disagreement rate).
"""
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import atomic_io  # noqa: E402

# --- tuning --------------------------------------------------------------------
NEAR_PCT = 2.5               # "near spot" window, percent of spot. Widened 07-12:
                             # 1.5% was only ~1.15σ on a high-vol day (live σ hit
                             # 1.30% of spot on 07-10), hiding roll divergence at
                             # 1.5-3% OTM exactly when it matters most.
# 4x the 5-pt SPX strike grid, ~0.27% of spot at 7500 — big enough to clear grid
# noise, small enough to sit inside the documented 30-65pt magnet-teleport scale.
K_DIVERGE_PTS = 20.0
MIN_WALL_SHARE = 0.10        # argmax must hold >= 10% of the bucket's near-spot |gamma|
MIN_NEAR_STRIKES = 10        # thinner than this near spot -> blind, emit None
MIN_SIGN_ELIGIBLE = 5        # fewer eligible strikes -> sign_disagreement is None
MAX_AGE_MS = 25 * 60_000     # 2 grid buckets + publish lag (see FIX #1/#2)

BUCKETS = ("0d", "1_7", "8_45")


def _state_base():
    env = os.environ.get("MIRAI_STATE_DIR")
    return Path(env) if env else (SKILL_DIR.parent.parent / "state")


def load_mark():
    """Read the UW mark read-only, fail-open. NEVER fetches (single-writer rule:
    only uw_periscope.ensure_mark writes uw_mark.json)."""
    m = atomic_io.read_json_or(_state_base() / "gex_uw" / "uw_mark.json", {"ok": False})
    return m if isinstance(m, dict) else {"ok": False}


# --- pure helpers ----------------------------------------------------------------
def _near_spot(per_strike_buckets, spot):
    """{strike(float): {bucket: gamma(float)}} for strikes within +/-NEAR_PCT% of spot.
    Junk entries (unparsable strikes, non-dict buckets, non-numeric gammas) are skipped."""
    near = {}
    for k, b in per_strike_buckets.items():
        try:
            s = float(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(b, dict) or abs(s - spot) / spot * 100.0 > NEAR_PCT:
            continue
        clean = {}
        for name in BUCKETS:
            v = b.get(name)
            if isinstance(v, dict):
                try:
                    clean[name] = float(v.get("gamma", 0.0))
                except (TypeError, ValueError):
                    continue
        if clean:
            near[s] = clean
    return near


def _wall(near, bucket, spot):
    """(strike, share) of the |gamma| argmax for one bucket among near-spot strikes.
    Valid only if its share of the bucket's near-spot Sum|gamma| >= MIN_WALL_SHARE;
    argmax tie -> strike nearest spot. None when the bucket is absent/flat/diffuse."""
    vals = {s: abs(g[bucket]) for s, g in near.items() if bucket in g}
    total = sum(vals.values())
    if not vals or total <= 0.0:
        return None
    best = max(sorted(vals), key=lambda s: (vals[s], -abs(s - spot)))
    share = vals[best] / total
    if share < MIN_WALL_SHARE:
        return None
    return best, share


def _owner_bucket(strike_buckets):
    """Bucket holding the largest |gamma| at one strike (attribution, not netting)."""
    return max(BUCKETS, key=lambda b: abs(strike_buckets.get(b, 0.0)))


def _sign_disagreement(near):
    """Fraction of eligible near-spot strikes whose bucket signs DIFFER.
    Eligible = strike with >= 2 buckets of nonzero sign; None if fewer than
    MIN_SIGN_ELIGIBLE. Signs are compared, never netted (FIX #3)."""
    eligible = disagree = 0
    for g in near.values():
        signs = {1 if v > 0 else -1 for v in g.values() if v != 0.0}
        n_nonzero = sum(1 for v in g.values() if v != 0.0)
        if n_nonzero >= 2:
            eligible += 1
            if len(signs) > 1:
                disagree += 1
    if eligible < MIN_SIGN_ELIGIBLE:
        return None
    return round(disagree / eligible, 4)


def _read(mark, spot, now):
    # --- HARD gate: any miss -> None (blind, not quiet) --------------------------
    if not isinstance(mark, dict) or not mark.get("ok"):
        return None
    if mark.get("stale") or mark.get("frozen"):
        return None
    if int(mark.get("mark_version") or 0) < 2:
        return None
    psb = mark.get("per_strike_buckets")
    if not isinstance(psb, dict) or not psb:
        return None
    spot = float(spot or 0.0)
    if spot <= 0.0:
        return None
    grid_ms = float(mark.get("grid_ms"))
    now_ms = now.timestamp() * 1000.0
    # The REAL freshness gate (FIX #1) — and BOTH sides of it: a healthy mark's
    # grid bucket always sits in the past (grid_floor(now-3min)), so a
    # FUTURE-dated grid_ms is a corrupt file / clock skew, not a fresh feed.
    # Without the lower bound it would pass the age check forever.
    age_ms = now_ms - grid_ms
    if age_ms > MAX_AGE_MS or age_ms < -60_000:
        return None
    grid_out = mark.get("grid_ms")

    near = _near_spot(psb, spot)
    if len(near) < MIN_NEAR_STRIKES:
        return None                                        # too thin near spot -> blind

    w0 = _wall(near, "0d", spot)
    wf = _wall(near, "8_45", spot)
    if not (w0 and wf and abs(w0[0] - wf[0]) >= K_DIVERGE_PTS):
        # QUIET default: fresh feed, no divergence -> tiny stub
        return {"source": "uw_periscope", "grid_ms": grid_out, "divergence": False}

    walls = []
    for name in BUCKETS:
        w = w0 if name == "0d" else (wf if name == "8_45" else _wall(near, name, spot))
        if w:
            walls.append({"strike": w[0], "share": round(w[1], 4),
                          "owner_bucket": _owner_bucket(near[w[0]])})
    return {
        "source": "uw_periscope",
        "grid_ms": grid_out,
        "age_ok": True,
        "zero_dte_wall": w0[0],
        "far_wall": wf[0],
        "walls": walls,
        "divergence": True,
        "divergence_dir": "far_above" if wf[0] > w0[0] else "far_below",
        "sign_disagreement": _sign_disagreement(near),
        "calendar_step": bool(mark.get("calendar_step")),
    }


def read(mark, spot, now):
    """Gamma-roll telemetry from one v2 mark. Divergence day -> 10-field record,
    quiet -> 3-field stub, gated/blind/junk -> None. Never raises (fail-open reader)."""
    try:
        return _read(mark, spot, now)
    except Exception:
        return None
