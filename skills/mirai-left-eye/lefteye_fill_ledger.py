"""lefteye_fill_ledger.py — the per-strike FILL LEDGER (recency input to the magnet).

Vocabulary: the GRAVITY ENGINE (lefteye_gex_box) computes where price gets PULLED;
this ledger tracks where orders are FILLING NOW. Since the pin-basis work its fill
weights feed the RECORD-ONLY pin_abs diagnostic (the live magnet rides the
OI-else-volume mag_basis) plus the flow/aggressor reads — not the live pin itself.

The failure it fixes: the 0DTE pin is volume-weighted, and day volume is CUMULATIVE —
on a thin chain a busy first hour can never be overtaken later, freezing the magnet
at a strike the market left behind (the stale-755-pin failure, 2026-07-02). The fix is the
diff ledger: track each contract's volume INCREMENT between scans (new fills only)
and decay the running weight with a half-life, so recent fills dominate and stale
bursts fade:

    weight_new = weight_old · 0.5^(Δt / HALFLIFE_MIN) + max(vol_now − vol_last, 0)

Price is NOT tracked directly (a magnet that just chases spot is useless); price
enters through the gamma × fill_weight product in the pin — gamma collapses on
strikes the market has left, fills concentrate near the money.

Every watch tick is a FRESH process, so the ledger persists to
state/gex_fills/{ledger_id}.json. Day rollover reseeds (0DTE volume is per-day;
the first scan of a day seeds weight = cumulative volume, which equals the legacy
read at the open and diverges usefully after). All IO is best-effort: any failure
leaves contracts un-annotated and the pin falls back to plain cumulative volume —
exactly yesterday's behavior. SHADOW, like everything it feeds.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

HALFLIFE_MIN = 45.0        # dial: fill relevance half-life (grade via magnet A/B)
_PRUNE_BELOW = 0.5         # drop decayed keys lighter than half a contract
_SKILL_DIR = Path(__file__).resolve().parent
_DEFAULT_DIR = _SKILL_DIR.parent.parent / "state" / "gex_fills"


def _key(c: dict) -> Optional[str]:
    k, r = c.get("strike"), c.get("right")
    if k is None or r not in ("call", "put"):
        return None
    return f"{round(float(k), 4)}:{r}"


def annotate(ledger_id: str, contracts: list[dict], now: datetime,
             state_dir: Optional[Path] = None) -> bool:
    """FILL LEDGER stamp — marks each strike with its recent order fills (45-min
    fade) so the magnet follows fresh money. Sets `fill_weight` (decayed
    new-fills weight) on every 0DTE contract and
    persist the updated ledger. Returns True when weights were annotated, False
    on any failure (callers then keep the legacy cumulative-volume pin)."""
    try:
        zero = [c for c in contracts if c.get("dte") == 0 and _key(c)]
        if not zero:
            return False
        d = Path(state_dir) if state_dir else _DEFAULT_DIR
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{ledger_id}.json"

        prev: dict = {}
        try:
            prev = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            prev = {}
        today = now.date().isoformat()
        if prev.get("date") != today:                     # day rollover → fresh book
            prev = {}

        decay = 1.0
        try:
            prev_ts = datetime.fromisoformat(prev["ts"])
            dt_min = (now - prev_ts).total_seconds() / 60.0
            if dt_min > 0:
                decay = 0.5 ** (dt_min / HALFLIFE_MIN)
        except (KeyError, ValueError, TypeError):
            pass

        prev_vol = prev.get("vol") or {}
        w = {k: v * decay for k, v in (prev.get("w") or {}).items()
             if v * decay >= _PRUNE_BELOW}

        vol_out = dict(prev_vol)                          # keep baselines for unseen strikes
        for c in zero:
            k = _key(c)
            vol_now = float(c.get("volume") or 0)
            base = prev_vol.get(k)
            inc = vol_now if base is None else max(vol_now - float(base), 0.0)
            w[k] = w.get(k, 0.0) + inc
            vol_out[k] = vol_now
            c["fill_weight"] = round(w[k], 4)

        path.write_text(json.dumps(
            {"date": today, "ts": now.isoformat(), "halflife_min": HALFLIFE_MIN,
             "vol": vol_out, "w": {k: round(v, 4) for k, v in w.items()}}))
        return True
    except Exception:
        return False
