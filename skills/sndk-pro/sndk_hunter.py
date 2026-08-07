#!/usr/bin/env python3
# Interpreter: the mirai-station venv is the only one with schwab-py + httpx, and
# the shebang cannot pin it — name it explicitly or use runtime/scripts/run-sndk.sh
# (see skills/mirai-left-eye/hunter.py for the rationale).
"""
sndk_hunter.py — SNDK-PRO scanner: one tick = one diary row.

Per tick (launchd fires every 120s; the run wrapper gates RTH):
    1. Schwab quote — LIVE spot (the chain's own spot is ~15% stale for SNDK),
       plus day open/high/low + prior close for the range ruler. NO quote →
       NO row (the stale chain spot is never an anchor — M3).
    2. sndk_feed.sndk_chain() — the discovered-weekly book, disk-cached raw
       for 240s (the off tick re-prices it at the fresh quote instead of
       re-pulling), IV rebuilt from quotes, gamma filled.
    3. sndk_views.build_row() — the pure engines (build_views / slide_0dte on
       the front book / dex / ladder / EM) → one row.
    4. Append to state/sndk_reversion/{YYYY-MM-DD}.jsonl (the viewstation reads
       these files via the generic /api/raw endpoints — pinned contract).

Isolation: writes ONLY under state/sndk_reversion + state/sndk_gex. Never
touches the SPX diary, reversion_lens, or hunter.py's scan loop.

Kill switch: SNDK_PRO_DISABLE=1 (checked here AND in run-sndk.sh).

CLI:
    python3 sndk_hunter.py            # one tick (RTH-gated)
    python3 sndk_hunter.py --force    # one tick, bypassing the RTH gate
                                      # (manual proof runs / backfill checks)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_LEFT_EYE = str(_SKILL_DIR.parent / "mirai-left-eye")
if _LEFT_EYE not in sys.path:
    sys.path.insert(0, _LEFT_EYE)

import atomic_io                       # noqa: E402
import sndk_feed                       # noqa: E402
import sndk_views                      # noqa: E402

_ET = ZoneInfo("America/New_York")


def _diary_dir() -> Path:
    env = os.environ.get("MIRAI_STATE_DIR")
    base = Path(env) if env else (_SKILL_DIR.parent.parent / "state")
    return base / "sndk_reversion"


def _market_live() -> bool:
    """The authoritative RTH gate (same source run-watch-left-eye.sh shells to).
    Fail-CLOSED: if the watch package can't be read, don't scan."""
    try:
        rt = str(_SKILL_DIR.parent.parent / "runtime")
        if rt not in sys.path:
            sys.path.insert(0, rt)
        from watch.intraday import market_status as _ms
        return bool(_ms.check().is_live)
    except Exception:
        return False


def _quote() -> dict:
    """One Schwab quote: live spot + day OHL + prior close. Reuses the left-eye
    fetcher's authenticated client (plain equity symbols pass the symbol map
    unchanged). Fail-open to an empty dict."""
    try:
        import lefteye_fetcher
        r = lefteye_fetcher._client().get_quote("SNDK")
        if r.status_code != 200:
            return {}
        q = ((r.json() or {}).get("SNDK") or {}).get("quote") or {}
        def f(k):
            v = q.get(k)
            return float(v) if isinstance(v, (int, float)) and v else None
        return {"spot": f("lastPrice") or f("mark"),
                "open": f("openPrice"), "high": f("highPrice"),
                "low": f("lowPrice"), "prior_close": f("closePrice")}
    except Exception:
        return {}


def _rows_today(now: datetime) -> list:
    path = _diary_dir() / f"{now.date().isoformat()}.jsonl"
    rows = []
    try:
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue               # torn tail line must not hide earlier rows
            if isinstance(r, dict) and r.get("ticker") == "SNDK":
                rows.append(r)
    except OSError:
        pass
    return rows


def _prev_close_totals(now: datetime) -> Optional[dict]:
    """Yesterday's last whole-book totals (reversion_lens._prev_close_totals
    pattern, against the SNDK diary): walks back ≤6 calendar days."""
    for back in range(1, 7):
        path = _diary_dir() / f"{(now - timedelta(days=back)).date().isoformat()}.jsonl"
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict) or r.get("ticker") != "SNDK":
                continue
            ne = r.get("net_exposure") or {}
            g = ne.get("net_gamma_total") or (r.get("gex_views") or {}).get("net_gex_tenor")
            d = ne.get("net_delta_total") or (r.get("dex_views") or {}).get("net_dex_total")
            if g is not None or d is not None:
                return {"gamma": g, "delta": d,
                        "date": (now - timedelta(days=back)).date().isoformat()}
    return None


def tick(now: Optional[datetime] = None, force: bool = False) -> int:
    if os.environ.get("SNDK_PRO_DISABLE") == "1":
        print("sndk :: disabled (SNDK_PRO_DISABLE=1)")
        return 0
    now = now or datetime.now(_ET)
    live_mkt = _market_live()
    if not force and not live_mkt:
        print("sndk :: market closed — skipping tick")
        return 0

    q = _quote()
    live = q.get("spot")
    if not live:
        # M3: the chain's own spot is ~15% stale for SNDK — with no live Schwab
        # quote there is no honest anchor, so the tick is skipped entirely (no
        # row) rather than written off the known-wrong chain spot
        sndk_feed._log({"ts": now.isoformat(), "skip": "no_live_quote"})
        print("sndk :: no live Schwab quote — skipping tick (no row)")
        return 0
    chain = sndk_feed.sndk_chain(now, live_spot=live)
    if not chain:
        print(f"sndk :: no chain ({(sndk_feed.LAST_REJECT or {}).get('reason')}) — no row")
        return 0
    spot, spot_source = live, "schwab_quote"

    bars = None
    try:
        import lefteye_fetcher
        bars = lefteye_fetcher.intraday_bars("SNDK")
    except Exception:
        bars = None                    # adaptive_em simply rides as None

    row = sndk_views.build_row(
        chain["contracts"], float(spot), now,
        spot_source=spot_source,
        chain_meta=chain.get("meta"),
        chain_spot=chain.get("spot"),
        prev_rows=_rows_today(now),
        prior_close=q.get("prior_close"),
        day_open=q.get("open"), day_high=q.get("high"), day_low=q.get("low"),
        intraday_bars=bars,
        prev_close_totals=_prev_close_totals(now),
        forced=(force and not live_mkt))       # m9: off-hours --force rows say so
    atomic_io.append_jsonl(_diary_dir() / f"{now.date().isoformat()}.jsonl", row)

    gv = row["gex_views"]
    nbs = gv.get("net_by_strike") or []
    print(f"sndk :: row @ {row['ts']} spot={row['spot']} ({spot_source}) "
          f"regime={gv.get('regime')} flip={gv.get('flip')} magnet={gv.get('magnet')} "
          f"walls={row.get('put_wall')}/{row.get('call_wall')} "
          f"front_dte={gv.get('front_dte')} n_strikes={len(nbs)}")
    return 0


if __name__ == "__main__":
    sys.exit(tick(force="--force" in sys.argv or "--once" in sys.argv))
