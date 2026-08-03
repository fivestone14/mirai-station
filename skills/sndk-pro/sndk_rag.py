#!/usr/bin/env python3
# Pinned interpreter: same venv as sndk_read.py (embedder lives there).
"""
sndk_rag.py — SNDK's on-demand memory: day tier + month tier, as a TOOL.

CORE RULE (2026-08-02 blueprint, section 05): history is a callable tool the
reading model reaches for when the live scene does not add up — it is NEVER
auto-injected into the payload. That keeps the live snapshot primary by
default: recalled context is supporting/sentiment, never equal weight. The
payload's own `history` flags (level_unseen_today / abnormal_tape) are the
under-pull guard that taps the model on the shoulder.

WHAT ONE RECORD IS — one entry, two faces:
  Face A, structured metadata (the GATE): time, levels, gamma, walls, sigma,
     and the vector + magnitude the model gave. Exact-filterable, NOT embedded
     (numbers embed poorly — similarity blurs strikes — but filter precisely).
  Face B, narrative (the MATCH): ONE natural-language sentence of what
     happened — the model's own line for that slice. This face is what gets
     ranked by meaning at query time.

TIERS:
  day    state/sndk_rag/slices/{date}.jsonl — per-wake slice records, plus the
         DIARY (state/sndk_reversion) as the raw numeric series the `series`
         command reads. Slices roll up into a day summary with a sentiment read.
  month  summaries.jsonl (one row per session) + terrain.json (standing walls,
         recurring battle lines, stock character) — context, never the trigger.

RETRIEVAL — hybrid, same order every time: (1) filter on metadata (tier, date,
time window, near-strike), (2) rank the survivors' narratives by meaning —
the right-eye embedder (bge-small, forced offline) when available, lexical
overlap otherwise; the output names which ranker ran. Corpus per query is tens
of sentences, so survivors are embedded at query time — no vector store to
drift out of sync with the rows.

Isolation: writes ONLY under state/sndk_rag/. Reads the SNDK diary and reads
store; never touches any SPX store. Called two ways:
  * imported by sndk_read (record_slice — fail-open, must never cost a read)
  * as the allow-listed CLI the reading model may run (see sndk_read._RAG_CMD)

CLI:
  sndk_rag.py query --tier slices  [--date D] [--from HH:MM --to HH:MM]
                    [--near-strike K [--tolerance PTS]] [--text "..."] [--limit N]
  sndk_rag.py query --tier days    [--days-back N] [--text "..."]
  sndk_rag.py query --tier month
  sndk_rag.py series [--date D] [--from HH:MM] [--to HH:MM] [--step MIN]
  sndk_rag.py rollup [--date D] [--force]      # manual; queries auto-rollup
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_SKILL_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_LEFT_EYE = str(_SKILL_DIR.parent / "mirai-left-eye")
if _LEFT_EYE not in sys.path:
    sys.path.insert(0, _LEFT_EYE)

import atomic_io                       # noqa: E402

_ET = ZoneInfo("America/New_York")
RAG_V = 1                    # record-schema era tag — bump on ANY shape change
MONTH_SESSIONS = 22          # the month tier looks back this many sessions
NEAR_TOL_PTS = 15.0          # default "near a strike" tolerance, $ (3 grid steps)
QUERY_LIMIT = 6              # default rows returned — context, not a dump


def _state_dir() -> Path:
    env = os.environ.get("MIRAI_STATE_DIR")
    base = Path(env) if env else (_SKILL_DIR.parent.parent / "state")
    return base


def _rag_dir() -> Path:
    return _state_dir() / "sndk_rag"


def _slices_path(day: str) -> Path:
    return _rag_dir() / "slices" / f"{day}.jsonl"


def _summaries_path() -> Path:
    return _rag_dir() / "summaries.jsonl"


def _terrain_path() -> Path:
    return _rag_dir() / "terrain.json"


def _diary_dir() -> Path:
    return _state_dir() / "sndk_reversion"


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for ln in path.read_text().splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue           # a torn tail line must not hide earlier rows
            if isinstance(r, dict):
                out.append(r)
    except OSError:
        return []
    return out


def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


# ---------------------------------------------------------------------------
# writing — one slice per completed read (called from sndk_read, fail-open)
# ---------------------------------------------------------------------------
def record_slice(row: dict, read_out: dict, scene: dict, now: datetime) -> None:
    """One RAG entry: what was true (Face A) + what it meant (Face B).

    Face B is the model's OWN sentence for the slice — the reading already
    happened, so memory costs no extra call. Raw numeric series are NOT
    duplicated here (the diary is the plain store `series` reads); this file
    holds only the memorable-narrative record."""
    reading = read_out.get("reading") or {}
    line = reading.get("line")
    if not line:
        return                      # no sentence → nothing memorable to store
    narrative = str(line)
    if reading.get("breaks_if"):
        narrative += f" Changes if {reading['breaks_if']}"
    walls = scene.get("walls") or {}
    ladder = row.get("profile_ladder") or {}
    band = read_out.get("magnet_band") or {}
    meta = {
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M"),
        "spot": _num(row.get("spot")),
        "sigma": _num(row.get("sigma")),
        "gamma_sign": row.get("gamma_sign"),
        "regime": row.get("regime"),
        "magnet_top": (band.get("top") or [])[:2],
        "gap_pp": band.get("gap_pp"),
        "walls_call": [w.get("strike") for w in (walls.get("call") or [])],
        "walls_put": [w.get("strike") for w in (walls.get("put") or [])],
        "ct": _num(ladder.get("ct")), "hvl": _num(ladder.get("hvl")),
        "pt": _num(ladder.get("pt")), "vwap": _num(row.get("vwap")),
        "vector": reading.get("vector"),
        "magnitude_sigma": reading.get("magnitude_sigma"),
        # THE AGGREGATOR'S ARROW IS DELIBERATELY NOT STORED (adversarial audit
        # 08-02): §05's Face A holds "the direction + magnitude CLAUDE gave" —
        # the model's own past vector is sanctioned memory; the deterministic
        # arrow is the very verdict the scene redesign stripped, and slices
        # are one allow-listed call away at exactly the uncertain moments
        # where anchoring bites. Same rule for wake strings that name the
        # arrow ("arrow appeared"/"stood down") — neutralized to "gate event".
        "wake": ("gate event"
                 if str(read_out.get("wake") or "").startswith("arrow")
                 else read_out.get("wake")),
    }
    rec = {"ts": now.isoformat(), "kind": "slice", "rag_v": RAG_V,
           "era": read_out.get("era"),
           "meta": {k: v for k, v in meta.items() if v is not None},
           "narrative": narrative[:500]}
    atomic_io.append_jsonl(_slices_path(meta["date"]), rec)


# ---------------------------------------------------------------------------
# day summaries — slices (or the diary, for pre-RAG days) rolled up
# ---------------------------------------------------------------------------
def _diary_day(day: str) -> list[dict]:
    """The day's LIVE tape only — the same rule the reader itself applies.
    Forced off-hours rows (meta.forced) and rows stamped outside RTH (the
    07-27 23:31 warmup predates the forced flag) are excluded: the 07-28
    summary once reported the 00:05 warmup as the session open/high, and a
    single midnight row counted as a whole session in the terrain (final
    verification pass 08-02). History must never hand the model tape the
    reader was built to ignore."""
    out = []
    for r in _read_jsonl(_diary_dir() / f"{day}.jsonl"):
        if r.get("ticker") != "SNDK":
            continue
        if (r.get("meta") or {}).get("forced"):
            continue
        hhmm = str(r.get("ts") or "")[11:16]
        if not ("09:25" <= hhmm <= "16:05"):
            continue
        out.append(r)
    return out


def _mode(xs) -> Optional[str]:
    xs = [x for x in xs if x]
    return Counter(xs).most_common(1)[0][0] if xs else None


def build_summary(day: str) -> Optional[dict]:
    """One session, one row: metadata + a deterministic sentiment sentence.
    Slices preferred (they carry the model's reads); the diary alone still
    yields an honest pre-RAG summary (source says which). None = no data."""
    slices = _read_jsonl(_slices_path(day))
    diary = _diary_day(day)
    spots = [s for r in diary if (s := _num(r.get("spot"))) is not None]
    if not spots:
        return None
    o, c, lo, hi = spots[0], spots[-1], min(spots), max(spots)
    pct = round((c / o - 1) * 100, 2) if o else None
    vecs = Counter(s["meta"].get("vector") for s in slices
                   if (s.get("meta") or {}).get("vector"))
    magnets = [(s.get("meta") or {}).get("magnet_top") for s in slices]
    magnets = [m[0][0] for m in magnets if m]
    if not magnets:
        magnets = [(r.get("gex_views") or {}).get("magnet") for r in diary]
        magnets = [m for m in magnets if m is not None]
    meta = {
        "open": o, "close": c, "low": lo, "high": hi, "pct_open_to_close": pct,
        "gamma_sign_mode": _mode(r.get("gamma_sign") for r in diary),
        "regime_mode": _mode(r.get("regime") for r in diary),
        "magnet_mode": _mode(f"{m:g}" for m in magnets if m is not None),
        "call_wall_mode": _mode(f"{w:g}" for r in diary
                                if (w := _num(r.get("call_wall"))) is not None),
        "put_wall_mode": _mode(f"{w:g}" for r in diary
                               if (w := _num(r.get("put_wall"))) is not None),
        "n_scans": len(diary), "n_slices": len(slices),
        "vector_counts": dict(vecs) or None,
    }
    lean = None
    if vecs:
        top, n = vecs.most_common(1)[0]
        if top in ("up", "down") and n > vecs.get("none", 0):
            lean = top
    mood = ("more bearish than it opened" if (pct or 0) <= -1.0 else
            "more bullish than it opened" if (pct or 0) >= 1.0 else
            "little changed from its open")
    bits = [f"SNDK closed {abs(pct):.1f}% {'below' if pct < 0 else 'above'} its open"
            if pct else "SNDK closed near its open",
            f"ranged {lo:g}–{hi:g}",
            f"{meta['regime_mode']} regime most of the day" if meta["regime_mode"] else None,
            f"magnet camped at {meta['magnet_mode']}" if meta["magnet_mode"] else None,
            f"the model leaned {lean}" if lean else None]
    narrative = "; ".join(b for b in bits if b) + f" — closed {mood}."
    return {"kind": "day_summary", "date": day, "rag_v": RAG_V,
            "source": "slices+diary" if slices else "diary",
            "meta": {k: v for k, v in meta.items() if v is not None},
            "narrative": narrative}


def _summaries() -> list[dict]:
    return [r for r in _read_jsonl(_summaries_path())
            if r.get("kind") == "day_summary"]


def rollup(day: Optional[str] = None, force: bool = False) -> list[str]:
    """Summarize `day` (or every un-summarized past diary day). Idempotent —
    an existing summary is kept unless --force rebuilds that one day.

    A LIVE day is never summarized, explicit --date included: the rollup CLI
    sits inside the reading model's Bash grant, and a mid-session summary
    would freeze half a day as "the day" (and poison terrain counts) since
    idempotency keeps first writes forever (SE review 08-02)."""
    have = {s["date"] for s in _summaries()}
    today = datetime.now(_ET).date().isoformat()
    if day:
        days = [day] if day < today else []
    else:
        days = sorted({p.stem for p in _diary_dir().glob("*.jsonl")}
                      | {p.stem for p in (_rag_dir() / "slices").glob("*.jsonl")})
        days = [d for d in days if d < today]
    done = []
    rows = _summaries()
    for d in days:
        if d in have and not force:
            continue
        s = build_summary(d)
        if not s:
            # a forced rebuild that comes back EMPTY must also retire any
            # stale summary — a day with no live tape is not a session, and
            # leaving the old row made 07-27's single midnight warmup a
            # phantom session in the terrain (final verification pass 08-02)
            if force and d in have:
                rows = [r for r in rows if r["date"] != d]
                done.append(d)
            continue
        rows = [r for r in rows if r["date"] != d] + [s]
        done.append(d)
    if done:
        # atomic rewrite of the whole (small) summaries file: pid-suffixed tmp
        # + replace (atomic_io.write_json_atomic's pattern) — two parallel
        # queries may both auto-rollup, and a SHARED tmp name lets one replace
        # the other's file out from under it (SE review 08-02)
        rows.sort(key=lambda r: r["date"])
        _rag_dir().mkdir(parents=True, exist_ok=True)
        tmp = _summaries_path().with_suffix(f".jsonl.{os.getpid()}.tmp")
        tmp.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        tmp.replace(_summaries_path())
    return done


# ---------------------------------------------------------------------------
# month tier — standing terrain, not moments
# ---------------------------------------------------------------------------
def terrain(rebuild: bool = False) -> Optional[dict]:
    """The persistent map the day tier plays out on: which strikes keep
    showing up as walls/magnets, the multi-session range, and character
    (trends clean vs chops). Cached per day; deterministic."""
    today = datetime.now(_ET).date().isoformat()
    if not rebuild:
        try:
            t = json.loads(_terrain_path().read_text())
            if t.get("built") == today:
                return t
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    rollup()                                     # summaries current first
    sums = _summaries()[-MONTH_SESSIONS:]
    if not sums:
        return None
    n = len(sums)
    walls_c = Counter(s["meta"].get("call_wall_mode") for s in sums
                      if s["meta"].get("call_wall_mode"))
    walls_p = Counter(s["meta"].get("put_wall_mode") for s in sums
                      if s["meta"].get("put_wall_mode"))
    mags = Counter(s["meta"].get("magnet_mode") for s in sums
                   if s["meta"].get("magnet_mode"))
    regs = Counter(s["meta"].get("regime_mode") for s in sums
                   if s["meta"].get("regime_mode"))
    lows = [s["meta"].get("low") for s in sums if s["meta"].get("low")]
    highs = [s["meta"].get("high") for s in sums if s["meta"].get("high")]
    trend_n = regs.get("trending", 0)
    bits = []
    if mags:
        k, c = mags.most_common(1)[0]
        bits.append(f"{k} has been the magnet {c} of {n} sessions")
    if walls_c:
        k, c = walls_c.most_common(1)[0]
        bits.append(f"{k} the recurring ceiling ({c}/{n})")
    if walls_p:
        k, c = walls_p.most_common(1)[0]
        bits.append(f"{k} the recurring floor ({c}/{n})")
    bits.append(f"character: trending {trend_n} of {n} sessions"
                + (" (chops otherwise)" if trend_n < n else ""))
    out = {"kind": "terrain", "built": today, "sessions": n, "rag_v": RAG_V,
           "standing": {"magnets": dict(mags.most_common(3)),
                        "call_walls": dict(walls_c.most_common(3)),
                        "put_walls": dict(walls_p.most_common(3))},
           "range": {"low": min(lows) if lows else None,
                     "high": max(highs) if highs else None},
           "regimes": dict(regs),
           "narrative": "; ".join(bits) + ".",
           "note": "standing terrain — context, never the trigger; the live "
                   "scene outranks all of this"}
    try:
        _rag_dir().mkdir(parents=True, exist_ok=True)
        atomic_io.write_json_atomic(_terrain_path(), out)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# retrieval — filter metadata FIRST, then rank narratives by meaning
# ---------------------------------------------------------------------------
def _lex_score(query: str, text: str) -> float:
    """Dependency-free fallback ranker: token overlap + phrase bonus."""
    qt = set(re.findall(r"[a-z0-9.]+", query.lower()))
    tt = set(re.findall(r"[a-z0-9.]+", text.lower()))
    if not qt or not tt:
        return 0.0
    j = len(qt & tt) / len(qt | tt)
    return j + (0.25 if query.lower().strip() in text.lower() else 0.0)


def _rank(query: str, records: list[dict]) -> tuple[list[dict], str]:
    """Rank survivors by narrative meaning. Embedder preferred (right-eye
    bge-small, FORCED offline so a hub outage can never hang a read); lexical
    overlap otherwise. Returns (ranked, method) — the method ships in the
    output so a reader knows which ranker spoke."""
    texts = [r.get("narrative") or "" for r in records]
    try:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        # progress bars would leak into the reading model's tool output
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TQDM_DISABLE", "1")
        sys.path.insert(0, str(_SKILL_DIR.parent / "mirai-right-eye"))
        from embed import embed_text                      # noqa: PLC0415
        cfg = json.loads((_SKILL_DIR.parent / "mirai-right-eye" /
                          "config.json").read_text())
        qv = embed_text(query, cfg)
        scored = []
        for r, t in zip(records, texts):
            tv = embed_text(t, cfg)
            scored.append((sum(a * b for a, b in zip(qv, tv)), r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored], "semantic"
    except Exception:
        scored = sorted(((_lex_score(query, t), r)
                         for r, t in zip(records, texts)), key=lambda x: -x[0])
        return [r for _, r in scored], "lexical"


def _hhmm(s: Optional[str]) -> Optional[str]:
    """Normalize '9:30' → '09:30' so lexical time compares work. The help says
    HH:MM, but a model dropping the leading zero would otherwise get a
    silently wrong slice set ('9:30' >= '15:00' lexically)."""
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s).strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else s


def _near(meta: dict, strike: float, tol: float) -> bool:
    """Face-A gate: does this slice touch `strike` (magnet/walls/levels/spot)?"""
    levels = ([meta.get("spot"), meta.get("ct"), meta.get("hvl"),
               meta.get("pt"), meta.get("vwap")]
              + [k for k, _ in (meta.get("magnet_top") or [])]
              + (meta.get("walls_call") or []) + (meta.get("walls_put") or []))
    return any(isinstance(x, (int, float)) and abs(x - strike) <= tol
               for x in levels)


def query(tier: str = "slices", date: Optional[str] = None,
          t_from: Optional[str] = None, t_to: Optional[str] = None,
          near_strike: Optional[float] = None, tolerance: float = NEAR_TOL_PTS,
          text: Optional[str] = None, days_back: int = 5,
          limit: int = QUERY_LIMIT) -> dict:
    """The hybrid lookup, always in the same order: metadata narrows,
    narrative carries the meaning."""
    now = datetime.now(_ET)
    if tier == "month":
        t = terrain()
        return {"tier": "month", "terrain": t or "no terrain yet"}
    if tier == "days":
        rollup()
        cands = _summaries()[-max(1, days_back):]
        method = "chronological"
        if text and cands:
            cands, method = _rank(text, cands)
        return {"tier": "days", "rank": method,
                "results": [{"date": s["date"], "narrative": s["narrative"],
                             "meta": s["meta"]} for s in cands[:limit]]}
    # slices (default) — today unless asked otherwise
    day = date or now.date().isoformat()
    t_from, t_to = _hhmm(t_from), _hhmm(t_to)
    cands = [s for s in _read_jsonl(_slices_path(day)) if s.get("kind") == "slice"]
    if t_from:
        cands = [s for s in cands if (s.get("meta") or {}).get("time", "") >= t_from]
    if t_to:
        cands = [s for s in cands if (s.get("meta") or {}).get("time", "") <= t_to]
    if near_strike is not None:
        cands = [s for s in cands
                 if _near(s.get("meta") or {}, float(near_strike), tolerance)]
    n_matched = len(cands)               # the FILTER's count, before any cut
    method = "chronological"
    if text and cands:
        cands, method = _rank(text, cands)
    else:
        cands = cands[-limit:]           # newest of the day when un-ranked
    return {"tier": "slices", "date": day, "rank": method,
            "n_matched": n_matched,
            "results": [{"time": (s.get("meta") or {}).get("time"),
                         "narrative": s.get("narrative"),
                         "meta": s.get("meta")} for s in cands[:limit]]}


def series(date: Optional[str] = None, t_from: Optional[str] = None,
           t_to: Optional[str] = None, step_min: int = 10,
           strike: Optional[float] = None) -> dict:
    """The plain numeric store, bucketed — spot/magnet/walls by time, off the
    diary. This is the 'queryable by time + strike' face of the day tier; RAG
    records stay narrative-only. With --strike, each bucket also reports that
    strike's own gamma mass and gross traded volume from the row's per-strike
    arrays (absent when the strike sat outside that scan's window — the
    telescope, not the book)."""
    day = date or datetime.now(_ET).date().isoformat()
    t_from, t_to = _hhmm(t_from), _hhmm(t_to)
    rows = _diary_day(day)
    out = []
    last_bucket = None
    for r in rows:
        ts = r.get("ts", "")
        hhmm = ts[11:16]
        if (t_from and hhmm < t_from) or (t_to and hhmm > t_to):
            continue
        try:
            b = (int(hhmm[:2]) * 60 + int(hhmm[3:5])) // step_min
        except ValueError:
            continue
        if b == last_bucket:
            continue
        last_bucket = b
        rec = {"time": hhmm, "spot": r.get("spot"),
               "magnet": (r.get("gex_views") or {}).get("magnet"),
               "call_wall": r.get("call_wall"),
               "put_wall": r.get("put_wall")}
        if strike is not None:
            gv = r.get("gex_views") or {}
            mass = dict(gv.get("mass_by_strike") or [])
            volg = dict(gv.get("vol_gross_by_strike") or [])
            k = float(strike)
            if k in mass:
                rec["gamma_mass_at_strike"] = mass[k]
            if k in volg:
                rec["vol_gross_at_strike"] = volg[k]
        out.append(rec)
    o = {"tier": "series", "date": day, "step_min": step_min, "rows": out}
    if strike is not None:
        o["strike"] = float(strike)
    return o


# ---------------------------------------------------------------------------
# CLI — what the reading model actually runs
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="sndk_rag.py",
        description="SNDK history tool — day slices, day summaries, month "
                    "terrain, numeric series. Metadata filters first, then "
                    "narrative ranking. History is context; the live scene "
                    "stays primary.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="recall narrative history")
    q.add_argument("--tier", choices=("slices", "days", "month"),
                   default="slices",
                   help="slices=today's moments · days=multi-day summaries · "
                        "month=standing terrain")
    q.add_argument("--date", help="YYYY-MM-DD (slices tier; default today)")
    q.add_argument("--from", dest="t_from", help="HH:MM lower bound (slices)")
    q.add_argument("--to", dest="t_to", help="HH:MM upper bound (slices)")
    q.add_argument("--near-strike", type=float,
                   help="only slices touching this level (magnet/walls/spot)")
    q.add_argument("--tolerance", type=float, default=NEAR_TOL_PTS,
                   help=f"near-strike tolerance in $ (default {NEAR_TOL_PTS:g})")
    q.add_argument("--text", help="semantic ask, e.g. 'rejected the call wall'")
    q.add_argument("--days-back", type=int, default=5,
                   help="days tier: how many summaries (default 5)")
    q.add_argument("--limit", type=int, default=QUERY_LIMIT)

    s = sub.add_parser("series", help="numeric spot/magnet/walls by time (diary)")
    s.add_argument("--date")
    s.add_argument("--from", dest="t_from")
    s.add_argument("--to", dest="t_to")
    s.add_argument("--step", type=int, default=10, help="bucket minutes")
    s.add_argument("--strike", type=float,
                   help="also report this strike's own gamma mass + traded "
                        "volume per bucket")

    r = sub.add_parser("rollup", help="build day summaries (queries auto-run this)")
    r.add_argument("--date")
    r.add_argument("--force", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd == "query":
        out = query(tier=a.tier, date=a.date, t_from=a.t_from, t_to=a.t_to,
                    near_strike=a.near_strike, tolerance=a.tolerance,
                    text=a.text, days_back=a.days_back, limit=a.limit)
    elif a.cmd == "series":
        out = series(date=a.date, t_from=a.t_from, t_to=a.t_to,
                     step_min=a.step, strike=a.strike)
    else:
        out = {"rolled_up": rollup(a.date, force=a.force)}
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
