"""What the code feature builder reads from the state dir (read-only), and the raw-first record of its answers.

    load_day_bars            today's SPX minute bars, bars/{day}.jsonl (the live-bars sidecar)
    load_market_history      the trailing sessions' SPX bars, the nights' /ES bars, the market feed's minute bars per
                             symbol (the read's day's SPY volume from the siege box's minute volumes), the daily closes,
                             the index weights, the day's SPX diary rows, the lob-flow quote sweeps and the day's premarket
                             reads: bars/, overnight/, context/, context/bars/, daily_closes/, reversion/, lob_flow/raw/ and
                             siege/baseline.json files where they exist, the store's Parquet tables (spx_bars,
                             overnight_bars) for the sessions before the files begin. The parts that are prior sessions
                             only are cached in-process for the day (the live service loads them once, then each read
                             loads the day's own parts).
    record_code_features     the answers for one read: the raw line already written for it, else computed now and
                             appended to raw/code_features/{day}.jsonl BEFORE anything forecasts with them (the write
                             path rule: new data goes to raw JSONL first, every table is built from it)

Every bar's ts is normalised to ET so the builder compares minutes of day as strings.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

from .. import daily_closes
from ..events import ET
from ..index_weights import WEIGHTS_FILE
from ..labels.options_flow import SPY_VOLUMES, _read_spy_volumes
from ..state_builder import LIVE_BARS_SUBDIR, load_jsonl
from .code_features import GAP_HISTORY_SESSIONS, HISTORY_SESSIONS, MarketHistory, answer_code_features, bars_up_to
from .live_records import _json_lines
from .paths import append_json_line, now_utc_iso, raw_code_features_file, read_json_lines, spx_jev_dir

OVERNIGHT_SUBDIR = "overnight"
CONTEXT_SUBDIR = "context"                           # context/{day}.jsonl quotes a minute apart; context/bars/{day}.jsonl full days
DIARY_SUBDIR = "reversion"                           # the SPX diary (walls, magnet, at-the-money vol) a minute apart
SWEEPS_SUBDIR = Path("lob_flow") / "raw"             # the lob-flow collector's quote sweeps, {day}/sweeps.jsonl(.gz)
SWEEP_BUCKETS = ("d25_40", "d10_25", "d00_10")      # the sweep's delta buckets OPTIONS-08 reads, nearest the money first: the
                                                     # 25-40 bucket empties after ~13:10 ET, so the next one out stands in
STORE_SPX_BARS = "spx_bars"
STORE_OVERNIGHT_BARS = "overnight_bars"
ES = "/ES"
SPY = "SPY"
QUOTE_BAR_SYMBOLS = ("SPY", "QQQ", "IWM")           # FLOW-07 reads the read's day's quotes for these, saved bars or not
DIARY_FIELDS = ("call_wall", "put_wall", "call_wall_tenor", "put_wall_tenor", "atm_iv", "gex_source")
DIARY_GAMMA_FIELDS = ("call_wall_gamma", "put_wall_gamma")    # the near gamma walls, under the row's gex_views


def _et(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ET).isoformat()


def _bar(ts, open_, high, low, close, volume=0.0) -> dict | None:
    """One bar in the builder's shape, or None when a field is missing or malformed (a bad line is skipped, never fatal)."""
    try:
        return {"ts": _et(ts), "open": float(open_), "high": float(high), "low": float(low), "close": float(close),
                "volume": float(volume) if isinstance(volume, (int, float)) else 0.0}
    except (TypeError, ValueError):
        return None


def _store_glob(state_dir: Path | str, table: str) -> str | None:
    folder = spx_jev_dir(state_dir) / "store" / table
    if not folder.exists() or not any(folder.glob("day=*/*.parquet")):
        return None
    return str(folder / "*" / "*.parquet")


def _rows(sql: str) -> list[tuple]:
    import duckdb
    return duckdb.sql(sql).fetchall()


def load_day_bars(state_dir: Path | str, day: str) -> list[dict]:
    """The day's SPX minute bars from the live-bars sidecar, in time order."""
    bars = [_bar(b.get("ts"), b.get("open"), b.get("high"), b.get("low"), b.get("close"))
            for b in load_jsonl(Path(state_dir) / LIVE_BARS_SUBDIR / f"{day}.jsonl") if isinstance(b.get("ts"), str)]
    return sorted((b for b in bars if b), key=lambda b: b["ts"])


def _file_days(folder: Path) -> list[str]:
    return sorted(p.stem for p in folder.glob("????-??-??.jsonl")) if folder.exists() else []


def load_spx_bars_by_day(state_dir: Path | str, before_day: str, sessions: int = HISTORY_SESSIONS) -> dict[str, list[dict]]:
    """The trailing ``sessions`` sessions' SPX bars before ``before_day``: the bars/ files, then the store for older days."""
    out: dict[str, list[dict]] = {}
    for day in [d for d in _file_days(Path(state_dir) / LIVE_BARS_SUBDIR) if d < before_day][-sessions:]:
        bars = load_day_bars(state_dir, day)
        if bars:
            out[day] = bars
    glob = _store_glob(state_dir, STORE_SPX_BARS)
    if glob is not None and len(out) < sessions:
        days = [r[0] for r in _rows(f"SELECT DISTINCT CAST(day AS VARCHAR) FROM read_parquet('{glob}', hive_partitioning=1) "
                                    f"WHERE CAST(day AS VARCHAR) < '{before_day}' ORDER BY 1")]
        wanted = [d for d in days if d not in out][-(sessions - len(out)):]
        if wanted:
            sql = (f"SELECT CAST(day AS VARCHAR), CAST(ts AS VARCHAR), open, high, low, close FROM read_parquet('{glob}', hive_partitioning=1) "
                   f"WHERE CAST(day AS VARCHAR) IN ({', '.join(repr(d) for d in wanted)}) ORDER BY ts")
            for day, ts, o, h, l, c in _rows(sql):
                bar = _bar(ts, o, h, l, c)
                if bar:
                    out.setdefault(day, []).append(bar)
    return out


def load_night_bars(state_dir: Path | str, day: str, symbol: str = ES) -> list[dict]:
    """The symbol's 1-minute bars outside the regular session for the night into ``day`` from overnight/{day}.jsonl, in
    time order (the file also holds 5-minute bars of the same stretch, left out)."""
    rows = load_jsonl(Path(state_dir) / "spx_jev" / OVERNIGHT_SUBDIR / f"{day}.jsonl")
    bars = [_bar(b.get("ts"), b.get("open"), b.get("high"), b.get("low"), b.get("close"), b.get("volume"))
            for b in rows if b.get("symbol") == symbol and b.get("session") != "regular" and b.get("bar_minutes") == 1
            and isinstance(b.get("ts"), str)]
    return sorted((b for b in bars if b), key=lambda b: b["ts"])


def load_night_bars_by_day(state_dir: Path | str, day: str, symbol: str = ES, sessions: int = HISTORY_SESSIONS,
                           with_day: bool = True) -> dict[str, list[dict]]:
    """The trailing ``sessions`` nights before ``day`` from the store, with the overnight/ files filling any night the
    store does not hold yet, and (``with_day``) the night into ``day`` itself from its overnight/ file, the night still
    being written. The files are large (every symbol, every session), so only the nights the store lacks are read from them."""
    out: dict[str, list[dict]] = {}
    glob = _store_glob(state_dir, STORE_OVERNIGHT_BARS)
    store_days: list[str] = []
    if glob is not None:
        store_days = [r[0] for r in _rows(f"SELECT DISTINCT CAST(day AS VARCHAR) FROM read_parquet('{glob}', hive_partitioning=1) "
                                          f"WHERE symbol = '{symbol}' AND CAST(day AS VARCHAR) < '{day}' ORDER BY 1")]
    file_days = [d for d in _file_days(Path(state_dir) / "spx_jev" / OVERNIGHT_SUBDIR) if d < day]
    prior = sorted(set(store_days) | set(file_days))[-sessions:]
    from_store = [d for d in prior if d in store_days]
    if from_store:
        sql = (f"SELECT CAST(day AS VARCHAR), CAST(ts AS VARCHAR), open, high, low, close, volume FROM read_parquet('{glob}', hive_partitioning=1) "
               f"WHERE symbol = '{symbol}' AND session <> 'regular' AND bar_minutes = 1 AND CAST(day AS VARCHAR) IN ({', '.join(repr(d) for d in from_store)}) ORDER BY ts")
        for d, ts, o, h, l, c, v in _rows(sql):
            bar = _bar(ts, o, h, l, c, v)
            if bar:
                out.setdefault(d, []).append(bar)
    for d in [x for x in prior if x not in out] + ([day] if with_day else []):
        bars = load_night_bars(state_dir, d, symbol)
        if bars:
            out[d] = bars
    return out


# ---------------------------------------------------------------- the Phase 1 feeds

def _context_bars_file(state_dir: Path | str, day: str) -> Path:
    return spx_jev_dir(state_dir) / CONTEXT_SUBDIR / "bars" / f"{day}.jsonl"


def load_context_bars(state_dir: Path | str, day: str) -> dict[str, list[dict]]:
    """A saved session's minute bars per symbol from context/bars/{day}.jsonl (one line a minute, every symbol's bar
    under ``bars``), symbol -> bars in time order; a malformed bar is skipped."""
    out: dict[str, list[dict]] = {}
    for line in load_jsonl(_context_bars_file(state_dir, day)):
        bars = line.get("bars")
        if not isinstance(bars, dict):
            continue
        for symbol, b in bars.items():
            if isinstance(b, dict) and isinstance(b.get("ts"), str):
                bar = _bar(b.get("ts"), b.get("open"), b.get("high"), b.get("low"), b.get("close"), b.get("volume"))
                if bar:
                    out.setdefault(symbol, []).append(bar)
    for bars in out.values():
        bars.sort(key=lambda b: b["ts"])
    return out


def load_context_quotes_as_bars(state_dir: Path | str, day: str) -> dict[str, list[dict]]:
    """Today's symbols, before the day's bars file is saved (the day saver writes it after the close): each quote
    snapshot of context/{day}.jsonl as a one-price bar (open = high = low = close = last) at its snapshot time, its
    volume the day's cumulative volume since the snapshot before; the breadth symbols come as bars already. Symbol ->
    bars in time order. So a market answerer reads a symbol's closes and volume only, never its high or low: live and
    backfill then measure the same thing. The snapshot volume arrives in lumps about 70 seconds apart and runs above the
    saved bars', so SPY's is replaced by the siege box's minute volumes (with_siege_spy_volume)."""
    out: dict[str, list[dict]] = {}
    cumulative: dict[str, float] = {}
    for line in load_jsonl(spx_jev_dir(state_dir) / CONTEXT_SUBDIR / f"{day}.jsonl"):
        ts = line.get("ts")
        if not isinstance(ts, str):
            continue
        for symbol, q in (line.get("quotes") or {}).items():
            last = q.get("last") if isinstance(q, dict) else None
            if not isinstance(last, (int, float)):
                continue
            total = q.get("volume")
            traded = 0.0
            if isinstance(total, (int, float)):
                traded = max(float(total) - cumulative.get(symbol, float(total)), 0.0)
                cumulative[symbol] = float(total)
            bar = _bar(ts, last, last, last, last, traded)
            if bar:
                out.setdefault(symbol, []).append(bar)
        for symbol, b in (line.get("bars") or {}).items():
            if isinstance(b, dict) and isinstance(b.get("ts"), str):
                bar = _bar(b.get("ts"), b.get("open"), b.get("high"), b.get("low"), b.get("close"), b.get("volume"))
                if bar and (not out.get(symbol) or out[symbol][-1]["ts"] != bar["ts"]):
                    out.setdefault(symbol, []).append(bar)
    for bars in out.values():
        bars.sort(key=lambda b: b["ts"])
    return out


def load_context_bars_by_day(state_dir: Path | str, before_day: str, sessions: int = HISTORY_SESSIONS) -> dict[str, dict[str, list[dict]]]:
    """The trailing ``sessions`` saved sessions' minute bars per symbol before ``before_day`` (the context/bars files hold
    every symbol the market feed carries, the new ones backfilled; the store's context_bars does not carry them)."""
    out: dict[str, dict[str, list[dict]]] = {}
    for day in [d for d in _file_days(spx_jev_dir(state_dir) / CONTEXT_SUBDIR / "bars") if d < before_day][-sessions:]:
        bars = load_context_bars(state_dir, day)
        if bars:
            out[day] = bars
    return out


def load_spy_minute_volumes(state_dir: Path | str, day: str) -> dict[int, float]:
    """SPY's volume per minute of the day as the siege box keeps it (state/siege/baseline.json, a minute keyed by its
    start in minutes after midnight ET, 570 = 09:30): the same figures as the saved minute bars, live and after the save."""
    path = Path(state_dir) / SPY_VOLUMES
    if not path.exists():
        return {}
    return dict(_read_spy_volumes(str(path), path.stat().st_mtime_ns).get(day) or {})


def with_siege_spy_volume(today: dict[str, list[dict]], minutes: dict[int, float], day: str, from_quotes: bool) -> dict[str, list[dict]]:
    """The read's day's symbols with SPY rebuilt one bar a minute from the siege box's minute volumes, its close the last
    SPY price at or before the minute. load_market_history hands it SPY's quotes whether or not the day is saved, so live
    and backfill read the same closes and the same volume. Without the siege box's minutes, SPY from saved bars keeps its
    bars; SPY from quotes keeps the prices with no volume, so the volume questions stay silent rather than read the lumpy
    snapshot volume."""
    spy = today.get(SPY)
    if not spy:
        return today
    if not minutes:
        return today if not from_quotes else {**today, SPY: [{**b, "volume": 0.0} for b in spy]}
    rebuilt, k = [], 0
    for minute in sorted(minutes):
        stamp = datetime(*map(int, day.split("-")), minute // 60, minute % 60, tzinfo=ET).isoformat()
        while k < len(spy) and spy[k]["ts"][:16] <= stamp[:16]:
            k += 1
        if k:
            close = spy[k - 1]["close"]
            rebuilt.append({"ts": stamp, "open": close, "high": close, "low": close, "close": close, "volume": minutes[minute]})
    return {**today, SPY: rebuilt}


def load_daily_closes(state_dir: Path | str, before_day: str) -> dict[str, list[dict]]:
    """Each daily-closes symbol's sessions before the day (daily_closes.load, the point-in-time read), symbol -> rows."""
    return {symbol: daily_closes.load(Path(state_dir), symbol, before=before_day) for symbol in daily_closes.SYMBOLS}


def load_index_weights(state_dir: Path | str) -> dict | None:
    """The index weights file as a document (index_weights.pick chooses the entry for a day), or None without one."""
    path = Path(state_dir) / WEIGHTS_FILE
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) else None


def load_diary_rows(state_dir: Path | str, day: str) -> list[dict]:
    """The day's SPX diary rows (reversion/{day}.jsonl), slimmed to the fields the builder reads: ts, the 0DTE and
    1-to-7-day open-interest walls, the near gamma walls, the magnet, the at-the-money vol and the book's source
    ("native", or a scaled SPY stand-in); in time order, a malformed row skipped."""
    out = []
    for row in load_jsonl(Path(state_dir) / DIARY_SUBDIR / f"{day}.jsonl"):
        ts = row.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            views = row.get("gex_views") or {}
            slim = {"ts": _et(ts), "magnet": views.get("magnet"), **{k: views.get(k) for k in DIARY_GAMMA_FIELDS}}
        except (TypeError, ValueError, AttributeError):
            continue
        slim.update({k: row.get(k) for k in DIARY_FIELDS})
        out.append(slim)
    return sorted(out, key=lambda r: r["ts"])


def load_quote_sweeps(state_dir: Path | str, day: str) -> list[tuple[str, float, str]]:
    """The day's lob-flow quote sweeps as (ts, the quoted spread of the nearest-the-money bucket that held one, rounded to
    the cent, that bucket; SWEEP_BUCKETS)."""
    folder = Path(state_dir) / SWEEPS_SUBDIR / day
    path = next((p for p in (folder / "sweeps.jsonl", folder / "sweeps.jsonl.gz") if p.exists()), None)
    if path is None:
        return []
    out = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                sweep = json.loads(line)
            except ValueError:
                continue
            if not isinstance(sweep, dict) or not isinstance(sweep.get("ts"), str):
                continue
            buckets = sweep.get("buckets") or {}
            found = next(((v, name) for name in SWEEP_BUCKETS if isinstance(v := (buckets.get(name) or {}).get("spread"), (int, float))), None)
            if found is not None:
                try:
                    out.append((_et(sweep["ts"]), round(float(found[0]), 2), found[1]))
                except ValueError:
                    continue
    return sorted(out)


def load_quote_sweeps_by_day(state_dir: Path | str, before_day: str, sessions: int = HISTORY_SESSIONS) -> dict[str, list[tuple[str, float, str]]]:
    folder = Path(state_dir) / SWEEPS_SUBDIR
    days = sorted(p.name for p in folder.glob("????-??-??") if p.name < before_day) if folder.exists() else []
    out = {}
    for day in days[-sessions:]:
        sweeps = load_quote_sweeps(state_dir, day)
        if sweeps:
            out[day] = sweeps
    return out


def load_premarket_reads(state_dir: Path | str, lane: str, day: str) -> list[dict]:
    """The day's premarket-lane read records from the shared archive, in time order."""
    from ..lane import LANES
    path = Path(LANES[lane].archive_folder(state_dir)) / f"{day}.jsonl"
    reads = [r for r in _json_lines(path) if r.get("kind") in (None, "read") and r.get("lane") == "premarket" and r.get("row_ts")]
    return sorted(reads, key=lambda r: r["row_ts"])


def _part(load, empty):
    try:
        return load()
    except Exception:
        return empty


_PRIOR_SESSIONS_CACHE: dict[tuple[str, str, str], dict] = {}    # one day's prior-sessions parts, reused across its reads


def _prior_sessions_parts(state_dir: Path | str, lane: str, day: str) -> dict:
    """The parts of a day's history that hold prior sessions only (fixed for the day), loaded once per process and day."""
    key = (str(state_dir), lane, day)
    parts = _PRIOR_SESSIONS_CACHE.get(key)
    if parts is None:
        parts = {"spx_bars_by_day": _part(lambda: load_spx_bars_by_day(state_dir, day, GAP_HISTORY_SESSIONS), {}),
                 "es_night_bars_by_day": _part(lambda: load_night_bars_by_day(state_dir, day, ES, with_day=False), {}),
                 "context_bars_by_day": _part(lambda: load_context_bars_by_day(state_dir, day), {}),
                 "daily_closes": _part(lambda: load_daily_closes(state_dir, day), {}),
                 "index_weights": _part(lambda: load_index_weights(state_dir), None),
                 "quote_sweeps_by_day": _part(lambda: load_quote_sweeps_by_day(state_dir, day), {})}
        _PRIOR_SESSIONS_CACHE.clear()
        _PRIOR_SESSIONS_CACHE[key] = parts
    return parts


def load_market_history(state_dir: Path | str, lane: str, day: str) -> MarketHistory:
    """Everything the builder needs beyond the read's own record and the day's bars, for one day (reuse it across the day's reads).
    Each part is loaded on its own: a store that cannot be read, or a broken file, empties that part only, so the label
    questions (which need none of this) still answer. The prior sessions' parts are cached for the day; the day's own
    (the night into it, its symbols so far, its quotes, SPY's minute volumes, its diary, its sweeps, its premarket reads)
    are read fresh each call."""
    prior = _prior_sessions_parts(state_dir, lane, day)
    tonight = _part(lambda: load_night_bars(state_dir, day, ES), [])
    quotes = _part(lambda: load_context_quotes_as_bars(state_dir, day), {})
    saved = _part(lambda: load_context_bars(state_dir, day) if _context_bars_file(state_dir, day).exists() else None, None)
    today = saved if saved is not None else quotes
    if quotes.get(SPY):         # SPY's prices from the quotes on both paths: a saved bar's close a few cents off flips TREND-07's side
        today = {**today, SPY: quotes[SPY]}
    spy_minutes = _part(lambda: load_spy_minute_volumes(state_dir, day), {})
    today_bars = _part(lambda: with_siege_spy_volume(today, spy_minutes, day, saved is None or bool(quotes.get(SPY))), today)
    today_sweeps = _part(lambda: load_quote_sweeps(state_dir, day), [])
    return MarketHistory(spx_bars_by_day=prior["spx_bars_by_day"],
                         es_night_bars_by_day={**prior["es_night_bars_by_day"], **({day: tonight} if tonight else {})},
                         context_bars_by_day={**prior["context_bars_by_day"], **({day: today_bars} if today_bars else {})},
                         today_quote_bars={s: quotes[s] for s in QUOTE_BAR_SYMBOLS if quotes.get(s)},
                         today_context_from_quotes=saved is None,
                         daily_closes=prior["daily_closes"],
                         index_weights=prior["index_weights"],
                         diary_rows=_part(lambda: load_diary_rows(state_dir, day), []),
                         quote_sweeps_by_day={**prior["quote_sweeps_by_day"], **({day: today_sweeps} if today_sweeps else {})},
                         premarket_reads=_part(lambda: load_premarket_reads(state_dir, lane, day), []))


# ---------------------------------------------------------------- the raw record

def stored_code_features(root: Path, day: str) -> dict[str, dict]:
    """{read_id: raw line} for one day; the last line for a read wins."""
    return {r["read_id"]: r for r in read_json_lines(raw_code_features_file(root, day)) if r.get("read_id")}


def record_code_features(root: Path, state_dir: Path | str, lane: str, day: str, read_record: dict, source: str,
                         market_history: MarketHistory | None = None, day_bars: list[dict] | None = None,
                         stored: dict[str, dict] | None = None) -> dict[str, str | None]:
    """The read's code feature answers: the raw line already written for it, else computed and appended now.
    ``market_history``, ``day_bars`` and ``stored`` are the day's caches when a caller walks a whole day."""
    read_id = read_record.get("read_id")
    stored = stored if stored is not None else stored_code_features(root, day)
    if read_id in stored:
        return dict(stored[read_id].get("answers") or {})
    history = market_history if market_history is not None else load_market_history(state_dir, lane, day)
    bars = day_bars if day_bars is not None else load_day_bars(state_dir, day)
    answers = answer_code_features(read_record, bars_up_to(bars, read_record["row_ts"]), history)
    line = {"read_id": read_id, "lane": lane, "day": day, "row_ts": read_record["row_ts"], "created_at": now_utc_iso(),
            "source": source, "answers": answers}
    append_json_line(raw_code_features_file(root, day), line)
    stored[read_id] = line
    return answers
