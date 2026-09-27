"""The futures roll table: when Schwab's stitched history moved from one contract to the next, and a
guard that says whether two moments are on the same contract.

Schwab serves one futures history per root, stitched across contracts: /ESH27 answers with the same
bars as /ESZ26, and nothing in a bar says which contract it came from. At a roll the price jumps by
the spread between the two contracts, about 70 points on /ES, which is no market move at all: on
2026-09-14 the stitch turned a big-down open into "up". So a move measured across a roll is
meaningless, and every measure that spans one must be refused.

A roll is found in the data, never assumed. Each futures root is held against a cash market that
does not roll (the basis): /ES against $SPX and bitcoin against IBIT as a log ratio in percent, /ZN
against the ten-year yield ($TNX) as ZN minus beta x TNX in points, beta fitted on same-day 5-minute
changes. The day's basis is its median over the regular-session 5-minute bars both have. A roll is a
day-to-day step in that basis at least STEP_VS_TYPICAL times the median day-to-day step, on a day
inside the product's roll window (the exchange's calendar, ROLL_WINDOWS). A large step outside a
window (a Fed day moving the bond's convexity, say) is kept under ``rejected`` with its reason, and a
window the data covers without a step is listed under ``windows_without_roll``, so a missed roll is
visible. Within the night before the step, the switch is the bar-to-bar jump furthest in the step's
direction: that bar is the roll's ``at``, the first bar on the new contract.

Contract names follow the product's month cycle: the table is named once from the contract Schwab
quotes the root under (``/ESZ26``), walking back one contract per roll, and each later roll advances
it by one. A quote that disagrees with the table's current contract is a roll the data has not shown
yet (``pending``); the manifest carries it and the rank skips that night.

The table lives beside the overnight store, ``state/spx_jev/overnight/rolls.json``, since the daily
job rewrites it.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, time, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
REFERENCES = {"/ES": "$SPX", "/ZN": "$TNX", "/BTC": "IBIT", "/MBT": "IBIT"}
YIELD_REFERENCES = ("$TNX",)          # quoted as a yield, so the basis is a fitted difference, not a ratio
BASIS_FROM, BASIS_UNTIL = time(9, 35), time(16, 0)   # the regular-session 5-minute bars the day's basis is taken over
MIN_BASIS_BARS = 30                   # fewer shared bars than this and the day has no basis
STEP_VS_TYPICAL = 5.0                 # a roll's basis step against the median day-to-day step (rolls measured 6 to 75 times)
MONTH_CODES = "FGHJKMNQUVXZ"
QUARTERLY = "HMUZ"
CYCLES = {"/ES": QUARTERLY, "/ZN": QUARTERLY, "/BTC": MONTH_CODES, "/MBT": MONTH_CODES}


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def _last_friday(year: int, month: int) -> date:
    last = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - 4) % 7)


def roll_window(symbol: str, year: int, month: int) -> tuple[date, date] | None:
    """The days a roll may land on in this month, or None when the product does not roll in it.
    /ES: the week up to the quarterly contract's third-Friday expiry (Schwab moved on the Monday of
    expiry week in March, June and September 2026). /ZN: the second half of the month before the
    delivery month, where the first notice day pushes the market over. Bitcoin: the week up to the
    monthly last-Friday expiry."""
    if symbol == "/ES":
        if month % 3:
            return None
        f = _third_friday(year, month)
        return f - timedelta(days=7), f
    if symbol == "/ZN":
        if month % 3 != 2:
            return None
        return date(year, month, 15), date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    if symbol in ("/BTC", "/MBT"):
        f = _last_friday(year, month)
        return f - timedelta(days=7), f
    return None


def in_roll_window(symbol: str, day: date) -> bool:
    w = roll_window(symbol, day.year, day.month)
    return w is not None and w[0] <= day <= w[1]


def shift_contract(contract: str, symbol: str, steps: int) -> str:
    """The contract ``steps`` places later (negative: earlier) in the product's cycle: /ESZ26, -1 -> /ESU26."""
    cycle = CYCLES[symbol]
    code, year = contract[len(symbol)], 2000 + int(contract[len(symbol) + 1:])
    k = cycle.index(code) + steps
    return f"{symbol}{cycle[k % len(cycle)]}{(year + k // len(cycle)) % 100:02d}"


# ---- the basis ------------------------------------------------------------------------------------

def _clock_ok(ts: datetime) -> bool:
    return BASIS_FROM <= ts.time() < BASIS_UNTIL


def daily_basis(symbol: str, futures: list[dict], reference: list[dict]) -> tuple[dict[str, float], str]:
    """``({day: basis}, unit)``: the day's median basis over the 5-minute bars both markets have in the
    regular session. ``futures`` and ``reference`` are bars ``{ts, close}``."""
    ref = {datetime.fromisoformat(b["ts"]): b["close"] for b in reference if b.get("close")}
    fut = {datetime.fromisoformat(b["ts"]): b["close"] for b in futures if b.get("close")}
    shared = sorted(t for t in fut.keys() & ref.keys() if _clock_ok(t))
    if REFERENCES[symbol] in YIELD_REFERENCES:
        pairs = [(fut[b] - fut[a], ref[b] - ref[a]) for a, b in zip(shared, shared[1:]) if a.date() == b.date()]
        denom = sum(y * y for _, y in pairs)
        beta = sum(x * y for x, y in pairs) / denom if denom else 0.0
        value, unit = (lambda t: fut[t] - beta * ref[t]), "points"
    else:
        value, unit = (lambda t: 100.0 * math.log(fut[t] / ref[t])), "percent"
    by_day: dict[str, list[float]] = {}
    for t in shared:
        if fut[t] > 0 and ref[t] > 0:
            by_day.setdefault(t.date().isoformat(), []).append(value(t))
    return {d: statistics.median(v) for d, v in sorted(by_day.items()) if len(v) >= MIN_BASIS_BARS}, unit


def _switch_bar(night: list[dict], sign: float) -> tuple[str | None, float | None]:
    """The bar whose open jumps furthest from the bar before it in the step's direction: ``(ts, jump)``."""
    best = None
    for a, b in zip(night, night[1:]):
        jump = b["open"] - a["close"]
        if best is None or sign * jump > sign * best[1]:
            best = (b["ts"], jump)
    return best if best else (None, None)


def detect(symbol: str, futures_5min: list[dict], reference: list[dict], nights: dict[str, list[dict]]) -> dict:
    """The rolls in one root's history: ``{"rolls": [...], "rejected": [...], "windows_without_roll": [...],
    "typical_step", "unit", "basis_days": [first, last]}``. ``nights`` maps each day to the root's
    bars of the night before it (finest resolution, sorted), where the switch bar is looked for.
    Contract names are left to ``name``."""
    basis, unit = daily_basis(symbol, futures_5min, reference)
    days = list(basis)
    steps = [(b, basis[b] - basis[a]) for a, b in zip(days, days[1:])]
    out = {"unit": unit, "basis_days": [days[0], days[-1]] if days else None, "rolls": [], "rejected": [],
           "windows_without_roll": [], "typical_step": None}
    if len(steps) < 2:
        return out
    typical = statistics.median(abs(s) for _, s in steps) or 1e-12
    out["typical_step"] = round(typical, 5)
    for day, step in steps:
        ratio = abs(step) / typical
        if ratio < STEP_VS_TYPICAL:
            continue
        entry = {"symbol": symbol, "day": day, "basis_step": round(step, 4), "basis_unit": unit,
                 "step_vs_typical": round(ratio, 1), "reference": REFERENCES[symbol]}
        if not in_roll_window(symbol, date.fromisoformat(day)):
            out["rejected"].append({**entry, "reason": "outside the product's roll window"})
            continue
        at, jump = _switch_bar(nights.get(day, []), 1.0 if step > 0 else -1.0)
        out["rolls"].append({**entry, "at": at, "jump": round(jump, 4) if jump is not None else None})
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    seen = {r["day"][:7] for r in out["rolls"]}
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        w = roll_window(symbol, y, m)
        if w and first < w[0] and w[1] <= last and f"{y:04d}-{m:02d}" not in seen:
            out["windows_without_roll"].append([w[0].isoformat(), w[1].isoformat()])
        y, m = y + m // 12, m % 12 + 1
    return out


def name(rolls: list[dict], symbol: str, current: str) -> list[dict]:
    """Each roll with its ``from`` and ``to`` contract, walking back from the ``current`` one."""
    named, to = [], current
    for r in sorted(rolls, key=lambda r: r["day"], reverse=True):
        named.append({**r, "from": shift_contract(to, symbol, -1), "to": to})
        to = shift_contract(to, symbol, -1)
    return sorted(named, key=lambda r: r["day"])


# ---- the table -----------------------------------------------------------------------------------

def table_path(folder: Path) -> Path:
    return Path(folder) / "rolls.json"


def load(folder: Path) -> dict:
    path = table_path(folder)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "current": {}, "rolls": [], "rejected": [], "windows_without_roll": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save(folder: Path, table: dict) -> Path:
    path = table_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def merge(table: dict, symbol: str, found: dict, since: str, quoted: str | None) -> dict:
    """The table with ``symbol``'s rolls from ``since`` on replaced by ``found`` (detect's answer over
    that span). The current contract is the table's, advanced one per new roll after its last; on a
    first detection, the quoted one."""
    kept = [r for r in table["rolls"] if r["symbol"] == symbol and r["day"] < since]
    old_last = max((r["day"] for r in table["rolls"] if r["symbol"] == symbol), default=None)
    current = table["current"].get(symbol)
    if current is None:
        current = quoted
    else:
        current = shift_contract(current, symbol, sum(1 for r in found["rolls"] if old_last is None or r["day"] > old_last))
    rolls = kept + found["rolls"]
    others = [r for r in table["rolls"] if r["symbol"] != symbol]
    return {
        "schema_version": SCHEMA_VERSION,
        "current": {**table["current"], **({symbol: current} if current else {})},
        "rolls": sorted(others + (name(rolls, symbol, current) if current else rolls), key=lambda r: (r["symbol"], r["day"])),
        "rejected": sorted([r for r in table.get("rejected", []) if r["symbol"] != symbol or r["day"] < since] + found["rejected"],
                           key=lambda r: (r["symbol"], r["day"])),
        "windows_without_roll": {**table.get("windows_without_roll", {}),
                                 symbol: [w for w in table.get("windows_without_roll", {}).get(symbol, []) if w[1] < since]
                                 + found["windows_without_roll"]},
        "typical_step": {**table.get("typical_step", {}), symbol: [found["typical_step"], found["unit"]]},
    }


# ---- the guard -----------------------------------------------------------------------------------

def _roll_times(table: dict, symbol: str) -> list[datetime]:
    return sorted(datetime.fromisoformat(r["at"]) for r in table.get("rolls", []) if r["symbol"] == symbol and r.get("at"))


def same_contract(table: dict, symbol: str, t1: datetime, t2: datetime) -> bool:
    """True when no roll of ``symbol`` falls after the earlier moment and at or before the later one,
    so a price at one can be compared with a price at the other."""
    lo, hi = min(t1, t2), max(t1, t2)
    return not any(lo < at <= hi for at in _roll_times(table, symbol))


def contract_at(table: dict, symbol: str, ts: datetime, quoted: str | None = None) -> tuple[str | None, str]:
    """``(contract, "roll_table" | "quote")``: the contract Schwab's series was on at ``ts`` by the
    table, or the quoted one for a root the table does not know."""
    rolls = sorted((r for r in table.get("rolls", []) if r["symbol"] == symbol and r.get("at")), key=lambda r: r["at"])
    current = table.get("current", {}).get(symbol)
    if current is None:
        return quoted, "quote"
    for r in reversed(rolls):
        if datetime.fromisoformat(r["at"]) <= ts:
            return r["to"], "roll_table"
    return (rolls[0]["from"] if rolls else current), "roll_table"


def pending(table: dict, symbol: str, quoted: str | None) -> bool:
    """True when Schwab quotes the root under a contract the table has not rolled to yet."""
    current = table.get("current", {}).get(symbol)
    return bool(quoted and current and quoted != current)
