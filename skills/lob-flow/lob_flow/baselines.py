"""
baselines.py — the NORMALCY MEMORY: what does every 5-minute slot of a boring
day look like, per moneyness bucket? Every sensor reading is judged against
this memory as a robust z-score; nothing is ever judged against a raw number.

Method (research-backed, see plan doc):
  * key = metric x delta-bucket x session-block x time-of-day bin x VIX band
  * baseline = exponentially-weighted median/MAD over the trailing clean days
    (window 21, half-life ~10d) — medians because quote data is so bursty that
    an average would be poisoned by the very events we hunt
  * trust label: cold (<15 clean days) -> no z, percentile (15-20) -> band
    check only, robust (>=21) -> full z
  * event days (FOMC/CPI/NFP/OPEX) are NEVER folded into the memory, and the
    calendar gate silences alerts inside the scheduled windows — dealer
    pull-back there is Tuesday, not terror.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

from .contracts import BlockInfo, LobConfig
from .io_state import StateDir, atomic_write_json, read_json

MAD_K = 1.4826           # MAD -> sigma-equivalent under normality

# ---------------------------------------------------------------------------
# Time / bucket keys
# ---------------------------------------------------------------------------


def session_block(minute_et: int, cfg: LobConfig) -> Optional[str]:
    """open / mid / close — judged by their OWN normals (0DTE spreads widen
    into the close mechanically; the open is structurally wild)."""
    if minute_et < cfg.open_start_min or minute_et >= cfg.session_end_min:
        return None
    if minute_et < cfg.open_block_end_min:
        return "open"
    if minute_et >= cfg.close_block_start_min:
        return "close"
    return "mid"


def tod_bin(now: datetime) -> str:
    """5-minute time-of-day slot id, e.g. '0935'."""
    m = (now.hour * 60 + now.minute) // 5 * 5
    return f"{m // 60:02d}{m % 60:02d}"


def vix_band(vix: Optional[float]) -> str:
    """Fixed regime bands (v1): a vol-regime shift must not read as anomaly."""
    if vix is None:
        return "na"
    return "low" if vix < 15 else "mid" if vix < 25 else "high"


def key_of(metric: str, bucket: str, block: str, tod: str, band: str) -> str:
    return f"{metric}|{bucket}|{block}|{tod}|{band}"


# ---------------------------------------------------------------------------
# Weighted median/MAD (half-life day weights)
# ---------------------------------------------------------------------------


def _wmedian(pairs: list[tuple[float, float]]) -> Optional[float]:
    """Weighted median of (value, weight) pairs."""
    pairs = [(v, w) for v, w in pairs if v is not None and w > 0]
    if not pairs:
        return None
    pairs.sort(key=lambda p: p[0])
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def _wmed_mad(values: list, today: date,
              halflife_d: float) -> tuple[Optional[float], Optional[float]]:
    """(median, MAD) over BIN-LEVEL samples, each day's samples weighted by
    2^(-age/halflife). Bin-level dispersion is the point: judged against
    daily-median dispersion alone, a size z of -8 was arithmetically
    unreachable in most keys. Age is CALENDAR days, so the nominal 10d
    half-life is ~7 trading days — the knob is the decay shape, not an exact
    trading-day count. Accepts [day, sample] and [day, [samples...]] rows."""
    pairs = []
    for day_iso, v in values:
        try:
            age = (today - date.fromisoformat(day_iso)).days
        except ValueError:
            continue
        w = 2.0 ** (-max(0, age) / halflife_d)
        samples = v if isinstance(v, list) else [v]
        for s in samples:
            if s is not None:
                pairs.append((float(s), w))
    med = _wmedian(pairs)
    if med is None:
        return None, None
    mad = _wmedian([(abs(v - med), w) for v, w in pairs])
    return med, mad


# ---------------------------------------------------------------------------
# BaselineStore
# ---------------------------------------------------------------------------


class BaselineStore:
    """Per-key trailing history of daily medians -> robust z with trust label."""

    def __init__(self, path: Path, cfg: LobConfig):
        self.path = path
        self.cfg = cfg
        raw = read_json(path) or {}
        self.days: list[str] = raw.get("days", [])          # clean days folded
        self.data: dict[str, list] = raw.get("data", {})    # key -> [[day, value]...]

    # -- maintenance (nightly) -------------------------------------------------
    def fold_day(self, day: str, day_values: dict, clean: bool) -> bool:
        """Append one CLEAN day's per-key BIN SAMPLES (a list per key; a bare
        float is accepted and wrapped). Event days are refused here — the
        caller passes clean=False and nothing is learned from them."""
        if not clean or day in self.days:
            return False
        self.days = sorted(set(self.days + [day]))[-self.cfg.baseline_days:]
        keep_from = self.days[0]
        for key, val in day_values.items():
            samples = val if isinstance(val, list) else [val]
            samples = [float(s) for s in samples if s is not None]
            samples = samples[:self.cfg.baseline_samples_per_day]
            if not samples:
                continue
            hist = [p for p in self.data.get(key, []) if p[0] >= keep_from and p[0] != day]
            hist.append([day, samples])
            self.data[key] = sorted(hist)[-self.cfg.baseline_days:]
        # drop keys that fell entirely out of the window
        self.data = {k: [p for p in v if p[0] >= keep_from]
                     for k, v in self.data.items() if any(p[0] >= keep_from for p in v)}
        return True

    def save(self) -> None:
        atomic_write_json(self.path, {"days": self.days, "data": self.data})

    # -- scoring (live) ----------------------------------------------------------
    @property
    def clean_days(self) -> int:
        return len(self.days)

    @property
    def label(self) -> str:
        """Store-level maturity — REPORTING ONLY. Scoring trust is per key
        (a fresh VIX band or time slot must not inherit the store's age)."""
        return self._label_for(self.clean_days)

    def _label_for(self, n: int) -> str:
        if n >= self.cfg.baseline_days:
            return "robust"
        if n >= self.cfg.baseline_percentile_days:
            return "percentile"
        return "cold"

    def _quantum(self, key: str) -> float:
        metric = key.split("|", 1)[0]
        return dict(self.cfg.metric_quantum).get(metric, 0.0)

    def score(self, key: str, value: Optional[float],
              today: Optional[date] = None) -> dict:
        """{'z': float|None, 'label': str, 'breach': bool|None}
        Trust is judged from THIS KEY's history length (days): cold -> no
        opinion; percentile -> confidence tier only (no z, no alerts —
        min/max bands on 15-20 samples false-alarm ~12%/observation, so they
        are deliberately not wired to alerts); robust -> full z off BIN-LEVEL
        dispersion; 'borrowed' -> a cold VIX band scored against its calmer
        neighbor at inflated MAD (the storm sensor must not go mute exactly
        when the first storm arrives). MAD is floored by the metric's
        quantization (one lot / one tick) so a frozen calm history can't turn
        a single-tick wiggle into a monster z."""
        hist = self.data.get(key) or []
        label = self._label_for(len(hist))
        out = {"z": None, "label": label, "breach": None}
        if value is None:
            return out
        inflate = 1.0
        if label != "robust":
            borrowed = self._borrow_key(key)
            b_hist = self.data.get(borrowed) or [] if borrowed else []
            if self._label_for(len(b_hist)) != "robust":
                return out
            hist, label, inflate = b_hist, "borrowed", self.cfg.band_borrow_mad_inflate
            out["label"] = label
        med, mad = _wmed_mad(hist, today or date.today(),
                             self.cfg.baseline_ewma_halflife_d)
        if med is None:
            return out
        mad = max(mad or 0.0, self.cfg.mad_floor, self._quantum(key),
                  abs(med) * 0.01) * inflate
        z = (value - med) / (MAD_K * mad)
        out["z"] = round(z, 2)
        out["breach"] = abs(z) >= self.cfg.size_z_alert
        return out

    @staticmethod
    def _borrow_key(key: str) -> Optional[str]:
        """The neighbor a cold VIX band may borrow from: high->mid, low->mid,
        mid->low. Non-banded keys have nothing to borrow."""
        parts = key.rsplit("|", 1)
        if len(parts) != 2:
            return None
        base, band = parts
        neighbor = {"high": "mid", "low": "mid", "mid": "low"}.get(band)
        return f"{base}|{neighbor}" if neighbor else None

    def reachability(self, today: Optional[date] = None) -> Optional[float]:
        """Median max-attainable |z| across keys ((med - 0) / scaled MAD) —
        the audit that catches a structurally dead scatter alarm."""
        maxes = []
        for key, hist in self.data.items():
            if self._label_for(len(hist)) != "robust":
                continue
            med, mad = _wmed_mad(hist, today or date.today(),
                                 self.cfg.baseline_ewma_halflife_d)
            if med is None or med <= 0:
                continue
            mad = max(mad or 0.0, self.cfg.mad_floor, self._quantum(key),
                      abs(med) * 0.01)
            maxes.append(med / (MAD_K * mad))
        if not maxes:
            return None
        return round(sorted(maxes)[len(maxes) // 2], 1)


def day_samples(bin_rows: list[dict], cap: int) -> dict[str, list[float]]:
    """Collapse a day of bin rows [{key, value}...] into per-key sample lists
    (evenly thinned to `cap`) — bin-level dispersion is what makes the
    z thresholds reachable and honest."""
    acc: dict[str, list[float]] = {}
    for r in bin_rows:
        k, v = r.get("key"), r.get("value")
        if k and v is not None:
            acc.setdefault(k, []).append(float(v))
    out = {}
    for k, vs in acc.items():
        if len(vs) > cap:
            step = len(vs) / cap
            vs = [vs[int(i * step)] for i in range(cap)]
        out[k] = vs
    return out


# ---------------------------------------------------------------------------
# Calendar gate (FOMC / CPI / NFP seeded; NFP + OPEX also computed by rule)
# ---------------------------------------------------------------------------

# Seed: FOMC decision days verified against the Fed calendar 2026-07-04;
# CPI 2026-07-14 verified (BLS); later CPI dates follow the BLS second-week
# pattern and are marked verified:false pending the quarterly refresh.
DEFAULT_CALENDAR = {
    "note": "refresh quarterly; unverified dates follow the standard pattern",
    "events": [
        {"date": "2026-07-14", "kind": "CPI", "time_et": "08:30", "verified": True},
        {"date": "2026-07-29", "kind": "FOMC", "time_et": "14:00", "verified": True},
        {"date": "2026-08-12", "kind": "CPI", "time_et": "08:30", "verified": False},
        {"date": "2026-09-11", "kind": "CPI", "time_et": "08:30", "verified": False},
        {"date": "2026-09-16", "kind": "FOMC", "time_et": "14:00", "verified": True},
        {"date": "2026-10-13", "kind": "CPI", "time_et": "08:30", "verified": False},
        {"date": "2026-10-28", "kind": "FOMC", "time_et": "14:00", "verified": True},
        {"date": "2026-11-10", "kind": "CPI", "time_et": "08:30", "verified": False},
        {"date": "2026-12-09", "kind": "FOMC", "time_et": "14:00", "verified": True},
        {"date": "2026-12-10", "kind": "CPI", "time_et": "08:30", "verified": False},
    ],
}

BLOCK_HALF_WIDTH_MIN = 45


def first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def third_friday(year: int, month: int) -> date:
    return first_friday(year, month) + timedelta(days=14)


def vix_expiration_wednesday(day: date) -> Optional[date]:
    """VIX expiration = the Wednesday 30 days before the FOLLOWING month's
    SPX monthly OPEX (third Friday). Returns `day` if it IS that Wednesday."""
    if day.weekday() != 2:
        return None
    settle_ref = day + timedelta(days=30)
    return day if settle_ref == third_friday(settle_ref.year,
                                             settle_ref.month) else None


def _is_quarter_end_trading_day(day: date) -> bool:
    """Last weekday of Mar/Jun/Sep/Dec (approx; holiday shifts via seed)."""
    if day.month not in (3, 6, 9, 12) or day.weekday() >= 5:
        return False
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.month != day.month


class FileCalendar:
    """EventCalendar over a seeded JSON file + two computed rules:
    NFP = first Friday 08:30 ET, OPEX = third Friday (whole day dirty).

    Holiday shifts (an NFP released Thursday because Friday is a holiday, an
    OPEX moved off Good Friday) are handled by SEEDING explicit entries — the
    quarterly refresh must verify them. Beyond the seeded coverage horizon the
    calendar FAILS CLOSED: unknown-territory days are never 'clean', so a
    stale file can't quietly poison the baselines with unseen FOMC days."""

    def __init__(self, path: Path, seed: Optional[dict] = None):
        self.path = path
        obj = read_json(path)
        if obj is None:
            obj = seed or DEFAULT_CALENDAR
            atomic_write_json(path, obj)
        self.events = obj.get("events", [])
        dates = [e.get("date") for e in self.events if e.get("date")]
        self.coverage_until = max(dates) if dates else None

    def _events_on(self, day: date) -> list[dict]:
        out = [e for e in self.events if e.get("date") == day.isoformat()]
        if day == first_friday(day.year, day.month):
            out.append({"date": day.isoformat(), "kind": "NFP", "time_et": "08:30"})
        if day == third_friday(day.year, day.month):
            out.append({"date": day.isoformat(), "kind": "OPEX", "time_et": None})
        if day == vix_expiration_wednesday(day):
            # VIX settlement morning: SOQ-driven SPX flow distorts the open
            out.append({"date": day.isoformat(), "kind": "VIXEXP",
                        "time_et": "09:30"})
        if _is_quarter_end_trading_day(day):
            # JHEQX-style quarter-end collar rolls: massive known SPX prints
            # + dealer re-hedge — dirty baseline day, no intraday block
            out.append({"date": day.isoformat(), "kind": "QTR_ROLL",
                        "time_et": None})
        return out

    def block(self, now: datetime) -> BlockInfo:
        """Inside [event - 45m, event + END] -> blocked (alerts silenced).
        FOMC's window runs through the press conference (14:00 decision ->
        ~15:30) — the violent half of a Fed day is the presser, not the
        statement. OPEX/QTR_ROLL block nothing intraday by themselves (they
        dirty the baseline day)."""
        for e in self._events_on(now.date()):
            t = e.get("time_et")
            if not t:
                continue
            hh, mm = int(t[:2]), int(t[3:5])
            centre = datetime.combine(now.date(), time(hh, mm), tzinfo=now.tzinfo)
            after = 90 if e.get("kind") == "FOMC" else BLOCK_HALF_WIDTH_MIN
            lo = centre - timedelta(minutes=BLOCK_HALF_WIDTH_MIN)
            hi = centre + timedelta(minutes=after)
            if lo <= now <= hi:
                return BlockInfo(blocked=True, kind=e.get("kind"),
                                 until=hi.isoformat())
        return BlockInfo(blocked=False)

    def is_clean_day(self, day: date) -> bool:
        """Clean = eligible for the normalcy memory. Any scheduled event or
        OPEX makes the whole day dirty; so does any day past the seeded
        coverage horizon (fail closed — refresh the calendar instead)."""
        if self.coverage_until and day.isoformat() > self.coverage_until:
            return False
        return not self._events_on(day)


# ---------------------------------------------------------------------------
# Nightly fold entry point (called by the host after close)
# ---------------------------------------------------------------------------


def fold_day_into_baselines(sd: StateDir, day: str, cfg: LobConfig,
                            calendar: FileCalendar, store_name: str) -> dict:
    """Fold one finished day's agg rows into the named baseline store.
    Returns a small report (including the reachability audit — the median
    max-attainable |z|, which catches a structurally dead alarm); refuses
    event days (they stay out of the memory)."""
    clean = calendar.is_clean_day(date.fromisoformat(day))
    rows = [r for r in sd.rows(sd.agg(day))
            if r.get("engine") == store_name and r.get("baseline_rows")]
    flat = [x for r in rows for x in r["baseline_rows"]]
    store = BaselineStore(sd.baseline(store_name), cfg)
    folded = store.fold_day(day, day_samples(flat, cfg.baseline_samples_per_day),
                            clean=clean)
    if folded:
        store.save()
    return {"day": day, "engine": store_name, "clean": clean,
            "folded": folded, "keys": len(store.data),
            "clean_days": store.clean_days,
            "reachability_median_max_z": store.reachability()}
