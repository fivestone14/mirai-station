"""
IV history cache — daily ATM front-month IV snapshots per ticker.
Stored as JSONL at ./cache/iv_history.jsonl.

Hardening:
  - Malformed lines are skipped on read (survives partial writes).
  - Dedupe on read: (ticker, date) keeps the last write.
  - Prune to last 400 days on every write.
  - Validate 0.01 <= iv <= 5.0 on write (outlier rejection).
  - Drop top/bottom 0.5% as outlier guard on read.
"""
from __future__ import annotations

import json
from datetime import date as date_t, datetime
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "cache" / "iv_history.jsonl"
RETENTION_DAYS = 400


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def read_all(ticker: str) -> list[float]:
    """Return deduped, outlier-filtered list of historical IVs for a ticker."""
    if not CACHE_FILE.exists():
        return []
    by_date: dict[str, float] = {}
    with CACHE_FILE.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ticker") != ticker:
                continue
            d = row.get("date")
            iv = row.get("iv")
            if not isinstance(d, str) or not isinstance(iv, (int, float)):
                continue
            if iv < 0.01 or iv > 5.0:
                continue
            by_date[d] = float(iv)
    values = sorted(by_date.items())  # by date ascending
    ivs = [v for _, v in values]
    # outlier guard: drop top/bottom 0.5%
    if len(ivs) >= 200:
        n = len(ivs)
        cut = max(1, n // 200)
        sorted_ivs = sorted(ivs)
        low = sorted_ivs[cut]
        high = sorted_ivs[-cut - 1]
        ivs = [v for v in ivs if low <= v <= high]
    return ivs


def append(ticker: str, iv: float, today: str | None = None) -> None:
    """Append today's ATM IV snapshot, pruning the file to RETENTION_DAYS."""
    if iv is None or iv < 0.01 or iv > 5.0:
        return
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    today = today or _today_str()
    row = {"ticker": ticker, "date": today, "iv": float(iv)}

    existing: list[dict] = []
    if CACHE_FILE.exists():
        with CACHE_FILE.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("ticker") == ticker and r.get("date") == today:
                    continue
                existing.append(r)

    # prune by date
    cutoff = date_t.today().toordinal() - RETENTION_DAYS
    pruned = []
    for r in existing:
        d = r.get("date")
        try:
            ord_d = datetime.strptime(d, "%Y-%m-%d").date().toordinal()
        except (TypeError, ValueError):
            continue
        if ord_d >= cutoff:
            pruned.append(r)

    pruned.append(row)

    tmp = CACHE_FILE.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in pruned:
            f.write(json.dumps(r) + "\n")
    tmp.replace(CACHE_FILE)


def reset(ticker: str | None = None) -> None:
    """Wipe cache for one ticker (or all if ticker is None)."""
    if not CACHE_FILE.exists():
        return
    if ticker is None:
        CACHE_FILE.unlink()
        return
    kept: list[dict] = []
    with CACHE_FILE.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ticker") != ticker:
                kept.append(r)
    tmp = CACHE_FILE.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in kept:
            f.write(json.dumps(r) + "\n")
    tmp.replace(CACHE_FILE)
