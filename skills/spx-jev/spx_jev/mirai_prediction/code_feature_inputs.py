"""What the code feature builder reads from the state dir (read-only), and the raw-first record of its answers.

    load_day_bars            today's SPX minute bars, bars/{day}.jsonl (the live-bars sidecar)
    load_market_history      the trailing sessions' SPX bars, the nights' /ES bars, the sessions' $ADD and the day's
                             premarket reads: bars/ and overnight/ files where they exist, the store's Parquet tables
                             (spx_bars, overnight_bars, context_bars) for the sessions before the files begin
    record_code_features     the answers for one read: the raw line already written for it, else computed now and
                             appended to raw/code_features/{day}.jsonl BEFORE anything forecasts with them (the write
                             path rule: new data goes to raw JSONL first, every table is built from it)

Every bar's ts is normalised to ET so the builder compares minutes of day as strings.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..events import ET
from ..state_builder import LIVE_BARS_SUBDIR, load_jsonl
from .code_features import HISTORY_SESSIONS, MarketHistory, answer_code_features, bars_up_to
from .live_records import _json_lines
from .paths import append_json_line, now_utc_iso, raw_code_features_file, read_json_lines, spx_jev_dir

OVERNIGHT_SUBDIR = "overnight"
STORE_SPX_BARS = "spx_bars"
STORE_OVERNIGHT_BARS = "overnight_bars"
STORE_CONTEXT_BARS = "context_bars"
ES = "/ES"
ADD = "$ADD"


def _et(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ET).isoformat()


def _bar(ts, open_, high, low, close) -> dict | None:
    """One bar in the builder's shape, or None when a field is missing or malformed (a bad line is skipped, never fatal)."""
    try:
        return {"ts": _et(ts), "open": float(open_), "high": float(high), "low": float(low), "close": float(close)}
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


def _days_between(from_day: str, before_day: str) -> str:
    return f"CAST(day AS VARCHAR) >= '{from_day}' AND CAST(day AS VARCHAR) < '{before_day}'"


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


def load_night_bars(state_dir: Path | str, day: str) -> list[dict]:
    """The /ES bars outside the regular session for the night into ``day`` from overnight/{day}.jsonl, in time order."""
    rows = load_jsonl(Path(state_dir) / "spx_jev" / OVERNIGHT_SUBDIR / f"{day}.jsonl")
    bars = [_bar(b.get("ts"), b.get("open"), b.get("high"), b.get("low"), b.get("close"))
            for b in rows if b.get("symbol") == ES and b.get("session") != "regular" and isinstance(b.get("ts"), str)]
    return sorted((b for b in bars if b), key=lambda b: b["ts"])


def load_es_night_bars_by_day(state_dir: Path | str, day: str, sessions: int = HISTORY_SESSIONS) -> dict[str, list[dict]]:
    """The night into ``day`` (its overnight/ file, the night still being written) and the trailing ``sessions`` nights
    before it from the store, with the overnight/ files filling any night the store does not hold yet. The files are
    large (every symbol, every session), so only the nights the store lacks are read from them."""
    out: dict[str, list[dict]] = {}
    glob = _store_glob(state_dir, STORE_OVERNIGHT_BARS)
    store_days: list[str] = []
    if glob is not None:
        store_days = [r[0] for r in _rows(f"SELECT DISTINCT CAST(day AS VARCHAR) FROM read_parquet('{glob}', hive_partitioning=1) "
                                          f"WHERE symbol = '{ES}' AND CAST(day AS VARCHAR) < '{day}' ORDER BY 1")]
    file_days = [d for d in _file_days(Path(state_dir) / "spx_jev" / OVERNIGHT_SUBDIR) if d < day]
    prior = sorted(set(store_days) | set(file_days))[-sessions:]
    from_store = [d for d in prior if d in store_days]
    if from_store:
        sql = (f"SELECT CAST(day AS VARCHAR), CAST(ts AS VARCHAR), open, high, low, close FROM read_parquet('{glob}', hive_partitioning=1) "
               f"WHERE symbol = '{ES}' AND session <> 'regular' AND CAST(day AS VARCHAR) IN ({', '.join(repr(d) for d in from_store)}) ORDER BY ts")
        for d, ts, o, h, l, c in _rows(sql):
            bar = _bar(ts, o, h, l, c)
            if bar:
                out.setdefault(d, []).append(bar)
    for d in [x for x in prior if x not in out] + [day]:
        bars = load_night_bars(state_dir, d)
        if bars:
            out[d] = bars
    return out


def load_add_by_day(state_dir: Path | str, before_day: str, sessions: int = HISTORY_SESSIONS) -> dict[str, list[dict]]:
    """The trailing sessions' $ADD per minute from the store's context_bars (clean the next day), day -> [{ts, value}]."""
    glob = _store_glob(state_dir, STORE_CONTEXT_BARS)
    if glob is None:
        return {}
    days = [r[0] for r in _rows(f"SELECT DISTINCT CAST(day AS VARCHAR) FROM read_parquet('{glob}', hive_partitioning=1) "
                                f"WHERE symbol = '{ADD}' AND CAST(day AS VARCHAR) < '{before_day}' ORDER BY 1")][-sessions:]
    if not days:
        return {}
    sql = (f"SELECT CAST(day AS VARCHAR), CAST(ts AS VARCHAR), close FROM read_parquet('{glob}', hive_partitioning=1) "
           f"WHERE symbol = '{ADD}' AND CAST(day AS VARCHAR) IN ({', '.join(repr(d) for d in days)}) ORDER BY ts")
    out: dict[str, list[dict]] = {}
    for day, ts, value in _rows(sql):
        out.setdefault(day, []).append({"ts": _et(ts), "value": value})
    return out


def load_premarket_reads(state_dir: Path | str, lane: str, day: str) -> list[dict]:
    """The day's premarket-lane read records from the shared archive, in time order."""
    from ..lane import LANES
    path = Path(LANES[lane].archive_folder(state_dir)) / f"{day}.jsonl"
    reads = [r for r in _json_lines(path) if r.get("kind") in (None, "read") and r.get("lane") == "premarket" and r.get("row_ts")]
    return sorted(reads, key=lambda r: r["row_ts"])


def load_market_history(state_dir: Path | str, lane: str, day: str) -> MarketHistory:
    """Everything the builder needs beyond the read's own record and the day's bars, for one day (reuse it across the day's reads).
    Each part is loaded on its own: a store that cannot be read, or a broken file, empties that part only, so the label
    questions (which need none of this) still answer."""
    def part(load, empty):
        try:
            return load()
        except Exception:
            return empty
    return MarketHistory(spx_bars_by_day=part(lambda: load_spx_bars_by_day(state_dir, day), {}),
                         es_night_bars_by_day=part(lambda: load_es_night_bars_by_day(state_dir, day), {}),
                         add_by_day=part(lambda: load_add_by_day(state_dir, day), {}),
                         premarket_reads=part(lambda: load_premarket_reads(state_dir, lane, day), []))


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
