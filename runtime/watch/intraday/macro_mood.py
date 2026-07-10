"""macro_mood.py — the Morning Macro-Mood / news-as-signal layer (BETA, shadow).

Plain English:
Each morning the Mirai analyst sweeps cross-sector news and sentiment and writes a
single **expectation** for the day: which way the market leans (direction), how far
it's likely to travel (magnitude, in implied-daily-move units), how sure it is
(confidence), a per-sector breakdown, and the **reasoning** behind it — plus a
semantic **embedding** of that reasoning so later reads can tell whether the story
actually changed (cosine drift) rather than just diffing text.

The intraday tick loop reads this expectation as the day's backdrop and lets it
**lightly tilt** the reasoning context (never gating or sizing a trade). When price
**breaches a dealer gamma wall and pushes concretely beyond it** — not an ordinary
in-wall pullback — the loop calls the analyst back in to re-dive and refresh the
expectation. The layer is shadow/beta: it records its calls and, at EOD, scores the
morning expectation against the realized index move so it **learns its own
reliability** over time (a Bayes posterior, shrunk to neutral). That reliability is
what scales how loud the tilt is allowed to be — it starts at ~0 (silent) and earns
its voice.

Design notes (match the codebase idioms):
- Pure logic + injected providers. `analyze_fn` (the LLM) and `embed_fn` (the
  embedder) are passed in, so the orchestration is unit-testable with stubs and, in
  production, defaults to a `claude -p` analyzer + the right-eye embedder.
- INERT BY DEFAULT, exactly like insight.analyze_moment: with no analyze_fn wired the
  tick hook still computes tilt + detects wall breaches + records telemetry — it just
  never spends an LLM/embedding call. The morning-brief entrypoint is what injects the
  real analyzer.
- Every I/O helper is best-effort: a macro-mood failure must never cost more than a
  log line, never the tick.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import bayes

# ===========================================================================
# Pure math
# ===========================================================================


def cosine(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[float]:
    """Cosine similarity of two embedding vectors. None if either is missing or
    degenerate (zero norm) — null-not-zero, so a missing embedding never fakes a
    'sentiment unchanged' (1.0) or 'flipped' (0.0) reading."""
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return None
    return dot / (na * nb)


def reliability_weight(p: Optional[bayes.Posterior]) -> float:
    """How much voice the mood has earned, in [0, 1]. Built on the same shrink-to-
    neutral posterior the rest of the system learns with: with no evidence the
    smoothed win-rate sits at the 0.5 prior → weight 0 (silent). As the morning
    call proves directionally right, the win-rate climbs and the weight opens up;
    a proven-wrong call (win-rate < 0.5) also yields 0, never a negative voice."""
    if p is None:
        return 0.0
    wr = bayes.score(p.hits, p.misses)
    return max(0.0, min(1.0, 2.0 * (wr - 0.5)))


def wall_breach(spot: Optional[float], call_wall: Optional[float],
                put_wall: Optional[float], implied_move: Optional[float],
                buffer_frac: float) -> Optional[Dict[str, Any]]:
    """Has price pushed CONCRETELY BEYOND a dealer gamma wall? (the re-dive trigger).

    A breach fires only when spot is past the wall by a buffer — `buffer_frac` of the
    implied daily move (falling back to 0.05% of spot when implied_move is unknown).
    An ordinary pullback that merely *touches* or sits inside the walls does NOT fire;
    the point is a structural level giving way, not intraday noise.

    Returns {"wall": "call"|"put", "direction": +1|-1, "level": float,
             "beyond": float} or None. Direction is the breakout side: a call-wall
    breach is bullish (+1), a put-wall breach bearish (-1)."""
    if spot is None:
        return None
    buf = (abs(implied_move) * buffer_frac if implied_move is not None
           else abs(spot) * 0.0005)
    if call_wall is not None and spot > call_wall + buf:
        return {"wall": "call", "direction": 1, "level": float(call_wall),
                "beyond": round(spot - call_wall, 4)}
    if put_wall is not None and spot < put_wall - buf:
        return {"wall": "put", "direction": -1, "level": float(put_wall),
                "beyond": round(put_wall - spot, 4)}
    return None


def score_direction(predicted_dir: Optional[float],
                    realized_move: Optional[float],
                    flat_eps: float = 1e-9) -> Optional[bool]:
    """Did the morning call get the direction right? Compares the sign of the
    predicted direction to the realized index move. Returns True/False, or None
    when either side is flat/unknown (no evidence — don't pollute the posterior)."""
    if predicted_dir is None or realized_move is None:
        return None
    if abs(predicted_dir) <= flat_eps or abs(realized_move) <= flat_eps:
        return None
    return (predicted_dir > 0) == (realized_move > 0)


# ===========================================================================
# Persistence (best-effort; never raises)
# ===========================================================================


def _dir(state_dir: Path) -> Path:
    return Path(state_dir) / "market_expectation"


def expectation_path(state_dir: Path, date_iso: str) -> Path:
    return _dir(state_dir) / f"{date_iso}.json"


def write_expectation(state_dir: Path, exp: Dict[str, Any]) -> None:
    """Persist the day's expectation as {date}.json and refresh latest.json."""
    try:
        d = _dir(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        date_iso = str(exp.get("date") or "")[:10]
        if date_iso:
            (d / f"{date_iso}.json").write_text(json.dumps(exp, default=str))
        (d / "latest.json").write_text(json.dumps(exp, default=str))
    except OSError:
        pass


def read_expectation(state_dir: Path, date_iso: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(expectation_path(state_dir, date_iso).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def read_latest(state_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((_dir(state_dir) / "latest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def log_learning(state_dir: Path, row: Dict[str, Any], date_iso: str) -> None:
    """Append one telemetry/learning row to learning-{date}.jsonl (shadow record)."""
    try:
        d = _dir(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"learning-{date_iso}.jsonl", "a") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass


def _reliability_path(state_dir: Path) -> Path:
    return _dir(state_dir) / "reliability.json"


def read_reliability(state_dir: Path) -> bayes.Posterior:
    """The mood's learned reliability posterior (how often the morning direction
    is right). Missing/unreadable -> a fresh neutral posterior (weight 0)."""
    try:
        raw = json.loads(_reliability_path(state_dir).read_text())
        return bayes.Posterior.from_dict(raw.get("posterior", {}))
    except (OSError, json.JSONDecodeError):
        return bayes.Posterior()


def write_reliability(state_dir: Path, p: bayes.Posterior,
                      last_scored_date: Optional[str] = None) -> None:
    try:
        d = _dir(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {"posterior": p.to_dict()}
        if last_scored_date:
            payload["last_scored_date"] = last_scored_date
        _reliability_path(state_dir).write_text(json.dumps(payload, default=str))
    except OSError:
        pass


def _already_scored(state_dir: Path, date_iso: str) -> bool:
    try:
        raw = json.loads(_reliability_path(state_dir).read_text())
        return raw.get("last_scored_date") == date_iso
    except (OSError, json.JSONDecodeError):
        return False


def score_eod(state_dir: Path, exp: Optional[Dict[str, Any]],
              realized_move: Optional[float], date_iso: str,
              *, regime: Optional[str] = None) -> Optional[bool]:
    """EOD shadow scoring: did the morning expectation call the day's direction?
    Updates the reliability posterior (idempotent per day) and logs the outcome.
    Returns True/False, or None when there's nothing to score (no expectation,
    flat day, or already scored today)."""
    if exp is None or _already_scored(state_dir, date_iso):
        return None
    predicted = float((exp.get("overall") or {}).get("direction") or 0.0)
    won = score_direction(predicted, realized_move)
    if won is None:
        # Flat/unscoreable day: still stamp the date so the multi-tick EOD window
        # is idempotent and later ticks skip immediately.
        write_reliability(state_dir, read_reliability(state_dir), last_scored_date=date_iso)
        return None
    p = read_reliability(state_dir)
    bayes.update(p, won, regime=regime)
    write_reliability(state_dir, p, last_scored_date=date_iso)
    log_learning(state_dir, {"kind": "eod_score", "predicted_dir": predicted,
                             "realized_move": realized_move, "won": won,
                             "reliability": round(reliability_weight(p), 4)}, date_iso)
    return won


# ===========================================================================
# Orchestration (providers injected)
# ===========================================================================


def build_expectation(now: datetime, analyze_fn: Callable[[], Optional[Dict[str, Any]]],
                      embed_fn: Optional[Callable[[str], Optional[List[float]]]] = None,
                      *, prior: Optional[Dict[str, Any]] = None,
                      reason: str = "morning") -> Optional[Dict[str, Any]]:
    """Run one brief: call the analyzer, normalize, embed the reasoning, and (if a
    prior expectation is given) measure how far the story drifted by cosine.

    `analyze_fn()` returns {"overall": {direction, magnitude, confidence},
    "sectors": {name: {direction, magnitude}}, "reasoning": str} or None on failure.
    `embed_fn(text)` returns an embedding vector or None. Both are injected so this is
    pure orchestration. Returns the expectation dict, or None if the analyzer failed."""
    raw = None
    try:
        raw = analyze_fn()
    except Exception:
        raw = None
    if not raw or not isinstance(raw, dict):
        return None

    overall = raw.get("overall") or {}
    reasoning = str(raw.get("reasoning") or "").strip()
    # An empty-reasoning, all-zero object is a non-answer (the model punted or echoed
    # the schema skeleton because it had no data) — treat it as a failed read rather
    # than persisting a useless placeholder the tick loop would then tilt on.
    _flat = all(float(overall.get(k) or 0.0) == 0.0
                for k in ("direction", "magnitude", "confidence"))
    if not reasoning and _flat:
        return None
    embedding: Optional[List[float]] = None
    if embed_fn is not None and reasoning:
        try:
            embedding = embed_fn(reasoning)
        except Exception:
            embedding = None

    drift = None
    if prior is not None:
        drift = cosine(embedding, prior.get("embedding"))

    return {
        "date": now.date().isoformat(),
        "ts": now.isoformat(),
        "reason": reason,
        "overall": {
            "direction": float(overall.get("direction") or 0.0),
            "magnitude": float(overall.get("magnitude") or 0.0),
            "confidence": float(overall.get("confidence") or 0.0),
        },
        "sectors": raw.get("sectors") or {},
        "reasoning": reasoning,
        "embedding": embedding,
        "drift_cosine": drift,
    }


# ---------------------------------------------------------------------------
# Live providers (thin; not unit-tested — they hit claude -p / the embedder)
# ---------------------------------------------------------------------------

_BRIEF_SCHEMA = {
    "overall": {"direction": 0.0, "magnitude": 0.0, "confidence": 0.0},
    "sectors": {"semis": {"direction": 0.0, "magnitude": 0.0},
                "tech": {"direction": 0.0, "magnitude": 0.0},
                "energy": {"direction": 0.0, "magnitude": 0.0},
                "rates": {"direction": 0.0, "magnitude": 0.0}},
    "reasoning": "one paragraph: the cross-sector news/sentiment story for today",
}

_BRIEF_INSTRUCTION = (
    "You are Mirai's morning macro analyst. Sweep this morning's cross-sector news and "
    "market sentiment (macro data, Fed, rates, mega-cap tech, semis, energy, overnight "
    "futures, overseas sessions). Produce ONE market-wide read for a 0DTE index trader "
    "who trades SPX (broad S&P) and QQQ (Nasdaq tech). For `overall` and each "
    "`sector`: direction in [-1,+1] (negative bearish, positive bullish), magnitude in "
    "implied-daily-move units (0=quiet, 1=about a full implied move, >1=outsized), and "
    "for `overall` a confidence in [0,1]. Keep `reasoning` to one tight paragraph naming "
    "the specific catalysts. Be decisive but honest: a genuinely mixed tape is direction "
    "near 0 with LOW confidence (not zero magnitude).\n\n"
    "IMPORTANT: If research tools are available, use them; if they are unavailable or "
    "return nothing, STILL produce your best-effort read from your own knowledge of the "
    "current macro backdrop. Do NOT ask for permission, do NOT request tool access, and "
    "do NOT return an empty answer or all-zero placeholder. `reasoning` must ALWAYS be a "
    "real, non-empty paragraph. Respond with the JSON object only."
)

# Cassandra research tools the headless brief may use. MCP tools are allowlisted by
# SERVER name (not full tool name) — that's what `claude -p` actually pre-approves.
_DEFAULT_BRIEF_TOOLS = [
    "WebSearch",
    "mcp__market-research",
    "mcp__twitter-mcp",
    "mcp__reddit-mcp",
    "mcp__fetch-mcp",
]

# Scoped read-only: bypassPermissions lets the research tools actually run headless
# (default mode won't auto-approve them), and the denylist removes every write/exec
# tool from the model's reach so the brief can only read. --disallowedTools wins over
# any grant, so this holds even under bypassPermissions.
_BRIEF_DENY = ["Bash", "Write", "Edit", "NotebookEdit", "KillShell"]
_BRIEF_PERMISSION_MODE = "bypassPermissions"


def make_claude_analyzer(*, extra_context: str = "",
                         allowed_tools: Optional[List[str]] = None,
                         task: str = "macro_brief") -> Callable[[], Optional[Dict[str, Any]]]:
    """A live analyze_fn backed by `claude -p` (opus tier via the models map). Runs with
    SCOPED READ-ONLY tool access: the research MCP servers + WebSearch are allowlisted
    and bypassPermissions lets them actually execute headless, while a write/exec denylist
    keeps the brief read-only (it can fetch and search, never edit, write, or run shell).
    Returns a zero-arg callable so build_expectation stays provider-agnostic."""
    from .. import claude_cli

    tools = allowed_tools if allowed_tools is not None else list(_DEFAULT_BRIEF_TOOLS)

    def _analyze() -> Optional[Dict[str, Any]]:
        instruction = _BRIEF_INSTRUCTION + (f"\n\n{extra_context}" if extra_context else "")
        prompt = claude_cli.json_prompt(instruction, _BRIEF_SCHEMA)
        res = claude_cli.call(prompt, task=task, parse_json=True,
                              allowed_tools=tools, disallowed_tools=list(_BRIEF_DENY),
                              permission_mode=_BRIEF_PERMISSION_MODE, timeout=240.0)
        return res.parsed if res.parsed and isinstance(res.parsed, dict) else None

    return _analyze


def make_right_eye_embedder() -> Optional[Callable[[str], Optional[List[float]]]]:
    """A live embed_fn reusing the right-eye embedder (bge-small, 384-dim) so the
    macro reasoning lives in the same vector space as the news items. Returns None if
    the right-eye skill can't be loaded (then expectations carry no embedding and
    drift is simply unavailable — degrade, don't crash)."""
    try:
        from .. import right_eye_skill
        embed_text = right_eye_skill.load("embed", "embed_text")
        load_cfg = right_eye_skill.load("_config", "load")
        config = load_cfg()

        def _embed(text: str) -> Optional[List[float]]:
            try:
                return list(embed_text(text, config))
            except Exception:
                return None

        return _embed
    except Exception:
        return None


def run_brief(state_dir: Path, now: datetime, *,
              analyze_fn: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
              embed_fn: Optional[Callable[[str], Optional[List[float]]]] = None,
              reason: str = "morning", force: bool = False) -> Optional[Dict[str, Any]]:
    """Build and persist the day's expectation. Defaults wire the live claude/embedder
    providers; pass stubs in tests. Returns the persisted expectation (or None).

    Same-day idempotent for the morning brief: if today's expectation already exists
    and this is a `morning` run, it returns the existing one instead of spending
    another opus call (guards a launchd catch-up double-fire). Pass force=True to
    deliberately rebuild."""
    if reason == "morning" and not force:
        existing = read_expectation(state_dir, now.date().isoformat())
        if existing:
            return existing
    if analyze_fn is None:
        analyze_fn = make_claude_analyzer()
    if embed_fn is None:
        embed_fn = make_right_eye_embedder()
    prior = read_expectation(state_dir, now.date().isoformat()) if reason != "morning" else None
    exp = build_expectation(now, analyze_fn, embed_fn, prior=prior, reason=reason)
    if exp is None:
        return None
    write_expectation(state_dir, exp)
    log_learning(state_dir, {"kind": "brief", "reason": reason, "ts": exp["ts"],
                             "overall": exp["overall"], "drift_cosine": exp.get("drift_cosine")},
                 now.date().isoformat())
    write_learning_view(state_dir, now)
    return exp


def write_learning_view(state_dir: Path, now: datetime) -> None:
    """Render a scannable markdown view of the macro-mood layer's current read and
    its learned reliability into state/market_expectation/LEARNING.md (best-effort)."""
    try:
        exp = read_latest(state_dir) or {}
        p = read_reliability(state_dir)
        weight = reliability_weight(p)
        ov = exp.get("overall") or {}
        lines = [
            "# Mirai Awakening — ULT Macro-Mood Learning (BETA, shadow)",
            "",
            f"_rendered {now.isoformat()}_",
            "",
            "## Today's expectation",
            f"- **direction**: {ov.get('direction')}  ·  **magnitude**: {ov.get('magnitude')} "
            f"(implied-move units)  ·  **confidence**: {ov.get('confidence')}",
            f"- **reason**: {exp.get('reason')}  ·  **drift vs prior**: {exp.get('drift_cosine')}",
            f"- **reasoning**: {exp.get('reasoning')}",
            "",
            "## Sectors",
        ]
        for name, s in (exp.get("sectors") or {}).items():
            lines.append(f"- {name}: dir {s.get('direction')} / mag {s.get('magnitude')}")
        lines += [
            "",
            "## Learned reliability (shrink-to-neutral)",
            f"- hits {p.hits:.1f} · misses {p.misses:.1f} · **earned tilt weight {weight:.3f}** "
            f"(0 = silent until it proves itself)",
            "",
            "## Recent EOD scores",
        ]
        d = _dir(state_dir)
        scored: List[str] = []
        try:
            for f in sorted(d.glob("learning-*.jsonl"))[-10:]:
                for line in f.read_text().splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("kind") == "eod_score":
                        scored.append(f"- {f.stem[len('learning-'):]}: predicted "
                                      f"{row.get('predicted_dir')}, realized "
                                      f"{row.get('realized_move')} → {'WIN' if row.get('won') else 'miss'}")
        except OSError:
            pass
        lines += scored[-10:] or ["- (none yet)"]
        (d).mkdir(parents=True, exist_ok=True)
        (d / "LEARNING.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def make_redive_provider(state_dir: Path) -> Callable[[Optional[Dict[str, Any]], Dict[str, Any]], Optional[Dict[str, Any]]]:
    """A live re-dive provider for the tick graph: (prior_expectation, context) ->
    refreshed expectation (persisted) or None. Binds the opus `macro_redive` analyzer
    + the right-eye embedder. Production injects this; tests inject a stub. The node
    only calls it on a fresh wall breach within the daily cap, so it costs nothing on
    a quiet day — the embedder is built lazily on the first re-dive, so a quiet tick
    never even imports the right-eye package."""
    embedder: Dict[str, Any] = {}

    def _embed_fn(text):
        if "fn" not in embedder:
            embedder["fn"] = make_right_eye_embedder()
        fn = embedder["fn"]
        return fn(text) if fn is not None else None

    def _redive(prior: Optional[Dict[str, Any]], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ticker = context.get("ticker", "?")
        wall = context.get("wall", "?")
        now = context.get("now") or datetime.now()
        ctx_line = (f"INTRADAY UPDATE: {ticker} just broke its {wall} gamma wall "
                    f"and pushed beyond it. Re-evaluate the day's expectation given this "
                    f"structural break — has the story changed, or is the morning read intact?")
        analyze_fn = make_claude_analyzer(extra_context=ctx_line, task="macro_redive")
        exp = build_expectation(now, analyze_fn, _embed_fn, prior=prior,
                                reason=f"redive:{ticker}:{wall}")
        if exp is None:
            return None
        write_expectation(state_dir, exp)
        log_learning(state_dir, {"kind": "redive", "ticker": ticker, "wall": wall,
                                 "ts": exp["ts"], "overall": exp["overall"],
                                 "drift_cosine": exp.get("drift_cosine")},
                     now.date().isoformat())
        return exp

    return _redive
