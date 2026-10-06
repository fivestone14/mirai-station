"""Pool_v2: today's voices plus the three learners, each weighted by its track record. Pool_v1 is never touched.

The mix is pool_v1's rule (spx_jev.pool.mixed): a linear pool with one set of weights for "will it move?" and another
for "which way?", renormalised over the voices present and awake for the read. The daily update is pool_v1's rule too
(spx_jev.pool.weights_step): each voice's log weight moves by -ETA x the day's reads x its day-mean penalty gap against
the reference voice (the historical odds) over the reads the voice spoke on, the gap clipped to GAP_CLIP, then
Fixed-Share pulls every weight ALPHA of the way back to its starting weight. Reads are counted by their window coverage
(pool_v1.coverage): a 60-minute read every 30 minutes shares half its window with the next, so two such reads count as
about one and a half, and one stretch of market is never learned twice.

Differences from pool_v1, by design: a learner starts at NEW_VOICE_START_WEIGHT and earns from day one; a voice with no
forecast on a read is asleep for it (neither mixed nor updated, and the reference is measured on the same reads); the
freeze (pool_v1's rule) watches the existing voices only, so one wild newcomer cannot stop the pool learning. When the gap
clip binds on more than FREEZE_CLIP_LIMIT of their voice-days over FREEZE_WINDOW_DAYS learned days, the weights stop
moving and say why under "frozen" until someone reviews them (unfreeze_pool_v2); the mix keeps the last weights
meanwhile. A starting weight of zero is refused: it could never grow. Reads the live loop marks learn_exclude are left
out by the nightly job before they reach here.
"""
from __future__ import annotations

import math
from pathlib import Path

from .. import pool as pool_v1
from ..scores import OUTCOMES, floored, losses
from ..events import ET
from .name_map import LEARNER_NAMES, REFERENCE_VOICE, STAGES, rename_voice
from .paths import append_json_line, backup_before_change, now_utc_iso, pool_v2_updates_file, pool_v2_weights_file, write_json_atomically

NEW_VOICE_START_WEIGHT = 0.03
NEVER_MIXED = ("pool_v1", "pool_v2")        # the pools are scored beside the voices, never mixed as voices
STATE_VERSION = 1
FREEZE_WINDOW_DAYS = pool_v1.CAP_BIND_WINDOW     # 20 learned days
FREEZE_CLIP_LIMIT = pool_v1.CAP_BIND_LIMIT       # 5% of the existing voices' voice-days


def start_weights(voice_names: list[str]) -> dict[str, float]:
    """Starting weights: the existing voices keep pool_v1's starting shape (spx_jev.pool.W0, renamed), rescaled to leave
    NEW_VOICE_START_WEIGHT for each learner; a voice pool_v1 never had shares the existing voices' mean share."""
    voice_names = [v for v in voice_names if v not in NEVER_MIXED]
    learners = [v for v in voice_names if v in LEARNER_NAMES]
    existing = [v for v in voice_names if v not in LEARNER_NAMES]
    if not existing:
        raise ValueError("pool_v2 needs at least one existing voice beside the learners")
    v1 = {rename_voice(old): w for old, w in pool_v1.W0.items()}
    base = {v: v1.get(v) for v in existing}
    mean_known = sum(w for w in base.values() if w) / max(1, sum(1 for w in base.values() if w))
    base = {v: (w if w else mean_known) for v, w in base.items()}
    room = 1.0 - NEW_VOICE_START_WEIGHT * len(learners)
    total = sum(base.values())
    weights = {v: room * w / total for v, w in base.items()}
    weights.update({v: NEW_VOICE_START_WEIGHT for v in learners})
    for v, w in weights.items():
        if w <= 0:
            raise ValueError(f"voice {v!r} would start at weight {w}: a voice at zero can never earn weight")
    return weights


def new_pool_v2(sum_id: str, voice_names: list[str]) -> dict:
    w = start_weights(voice_names)
    return {"version": STATE_VERSION, "sum_id": sum_id, "created_at": now_utc_iso(), "start_weights": w,
            "voice_log_weights": {stage: {v: math.log(x) for v, x in w.items()} for stage in STAGES},
            "days_learned": [], "last_day_learned": None, "reads_learned": 0, "clip_hits": [], "frozen": None,
            "reference_voice": REFERENCE_VOICE, "constants": {"eta": pool_v1.ETA, "gap_clip": pool_v1.GAP_CLIP, "alpha": pool_v1.ALPHA}}


def load_pool_v2(root: Path, sum_id: str) -> dict | None:
    path = pool_v2_weights_file(root, sum_id)
    if not path.exists():
        return None
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def save_pool_v2(root: Path, state: dict) -> Path:
    path = pool_v2_weights_file(root, state["sum_id"])
    backup_before_change(root, path)
    write_json_atomically(path, state)
    return path


def add_missing_voices(state: dict, voice_names: list[str]) -> dict:
    """A voice the pool has never seen joins at its starting weight (a learner at NEW_VOICE_START_WEIGHT, another voice at
    the existing voices' mean start); the starting weights are then rescaled to sum to one, so Fixed-Share keeps pulling
    toward a proper share. The current weights of the others are left as they are."""
    known = set(state["start_weights"])
    missing = [v for v in voice_names if v not in known and v not in NEVER_MIXED]
    if not missing:
        return state
    existing_mean = sum(w for v, w in state["start_weights"].items() if v not in LEARNER_NAMES) / max(1, sum(1 for v in state["start_weights"] if v not in LEARNER_NAMES))
    for v in missing:
        w = NEW_VOICE_START_WEIGHT if v in LEARNER_NAMES else existing_mean
        state["start_weights"][v] = w
        for stage in STAGES:
            state["voice_log_weights"][stage][v] = math.log(w)
    total = sum(state["start_weights"].values())
    state["start_weights"] = {v: w / total for v, w in state["start_weights"].items()}
    return state


def weight_shares(state: dict, stage: str, voices: list[str] | None = None) -> dict[str, float]:
    """The weights of ``stage`` as shares summing to one over ``voices`` (all when None)."""
    logs = state["voice_log_weights"][stage]
    names = [v for v in (voices or list(logs)) if v in logs]
    if not names:
        return {}
    top = max(logs[v] for v in names)
    e = {v: math.exp(logs[v] - top) for v in names}
    s = sum(e.values())
    return {v: x / s for v, x in e.items()}


def mix_pool_v2(state: dict, forecasts: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """The pooled forecast over the voices present (awake) for this read: pool_v1's mixed() rule with pool_v2's weights."""
    present = [v for v in forecasts if v in state["voice_log_weights"]["move"]]
    if not present:
        return None
    logs = {"M": {v: state["voice_log_weights"]["move"][v] for v in present},
            "D": {v: state["voice_log_weights"]["direction"][v] for v in present}}
    return floored(pool_v1.mixed({v: floored(forecasts[v]) for v in present}, logs))


def read_coverage(read_times: list[str | None], window_minutes: int | None) -> list[float]:
    """Each read's share of its window not already covered by an earlier read's window (pool_v1.coverage), in the order
    given; 1.0 for every read when the window or a time is unknown."""
    if not window_minutes or any(t is None for t in read_times):
        return [1.0] * len(read_times)
    from datetime import datetime
    stamps = [datetime.fromisoformat(t).astimezone(ET) for t in read_times]
    order = sorted(range(len(stamps)), key=lambda i: stamps[i])
    shares = pool_v1.coverage([stamps[i] for i in order], window_minutes)
    out = [0.0] * len(stamps)
    for i, share in zip(order, shares):
        out[i] = share
    return out


def unfreeze_pool_v2(state: dict) -> dict:
    """After a review: the weights move again and the freeze's window starts afresh."""
    state["frozen"], state["clip_hits"] = None, []
    return state


def update_pool_v2_after_day(state: dict, day: str, reads: list[tuple[dict[str, dict[str, float]], str]],
                             read_times: list[str | None] | None = None, window_minutes: int | None = None) -> tuple[dict, dict]:
    """Learn one day: ``reads`` = [(forecasts by voice, outcome)] for the day's graded reads, ``read_times`` their row_ts
    and ``window_minutes`` how far the sum looks ahead, so overlapping reads count by their coverage. Returns (new state,
    log line). A voice absent on a read is asleep for it: its day-mean penalty, and the reference's it is measured
    against, are both over the reads it spoke on. The reference voice must be present on every scored read, else that
    read is skipped. While frozen the day is recorded but the weights do not move."""
    state.setdefault("clip_hits", [])
    state.setdefault("frozen", None)
    if day in state["days_learned"]:
        return state, {"day": day, "applied": False, "why": "already learned"}
    times = read_times if read_times is not None else [None] * len(reads)
    kept = [(f, y, t) for (f, y), t in zip(reads, times) if y in OUTCOMES and REFERENCE_VOICE in f]
    if not kept:
        return state, {"day": day, "applied": False, "why": "no graded read with the reference voice"}
    coverage = read_coverage([t for _, _, t in kept], window_minutes)
    penalty_sum: dict[str, dict[str, float]] = {stage: {} for stage in STAGES}         # stage -> voice -> coverage x penalty, summed
    reference_sum: dict[str, dict[str, float]] = {stage: {} for stage in STAGES}       # stage -> voice -> the reference's, same reads
    reads_spoken: dict[str, float] = {}                                                # voice -> coverage summed over its reads
    for (forecasts, outcome, _), share in zip(kept, coverage):
        reference = dict(zip(STAGES, losses(floored(forecasts[REFERENCE_VOICE]), outcome)))
        for voice, probs in forecasts.items():
            if voice not in state["voice_log_weights"]["move"]:
                continue
            own = dict(zip(STAGES, losses(floored(probs), outcome)))
            for stage in STAGES:
                penalty_sum[stage][voice] = penalty_sum[stage].get(voice, 0.0) + share * own[stage]
                reference_sum[stage][voice] = reference_sum[stage].get(voice, 0.0) + share * reference[stage]
            reads_spoken[voice] = reads_spoken.get(voice, 0.0) + share
    frozen = state["frozen"] is not None
    steps: dict[str, dict] = {}
    existing_days, existing_hits = 0, 0
    for stage in STAGES:
        logs = state["voice_log_weights"][stage]
        new_logs = dict(logs)
        for voice, total in penalty_sum[stage].items():
            n = reads_spoken[voice]
            if n <= 0:
                continue
            gap = (total - reference_sum[stage][voice]) / n
            clipped = max(-pool_v1.GAP_CLIP, min(pool_v1.GAP_CLIP, gap))
            new_logs[voice] = logs[voice] - pool_v1.ETA * n * clipped
            steps.setdefault(voice, {})[stage] = {"gap": round(gap, 5), "clipped": gap != clipped, "reads": round(n, 4)}
            if voice not in LEARNER_NAMES:              # the freeze watches the existing voices, never the newcomers
                existing_days += 1
                existing_hits += gap != clipped
        if frozen:
            continue
        # renormalise, then Fixed-Share toward the starting weights (pool_v1's rule)
        top = max(new_logs.values())
        e = {v: math.exp(x - top) for v, x in new_logs.items()}
        s = sum(e.values())
        shares = {v: (1 - pool_v1.ALPHA) * e[v] / s + pool_v1.ALPHA * state["start_weights"][v] for v in new_logs}
        state["voice_log_weights"][stage] = {v: math.log(max(x, 1e-12)) for v, x in shares.items()}
    state["days_learned"].append(day)
    state["last_day_learned"] = day
    state["reads_learned"] = state.get("reads_learned", 0) + len(kept)
    line = {"day": day, "applied": not frozen, "reads": len(kept), "counted_reads": round(sum(coverage), 4), "at": now_utc_iso(),
            "steps": steps, "shares": {stage: {v: round(x, 4) for v, x in weight_shares(state, stage).items()} for stage in STAGES}}
    if frozen:
        line["why"] = f"frozen: {state['frozen']}"
        return state, line
    state["clip_hits"] = (state["clip_hits"] + [[day, existing_days, existing_hits]])[-FREEZE_WINDOW_DAYS:]
    seen, hit = sum(d for _, d, _ in state["clip_hits"]), sum(h for _, _, h in state["clip_hits"])
    if len(state["clip_hits"]) >= FREEZE_WINDOW_DAYS and seen and hit / seen > FREEZE_CLIP_LIMIT:
        state["frozen"] = (f"the gap clip bound on more than {FREEZE_CLIP_LIMIT:.0%} of the existing voices' voice-days over "
                           f"{FREEZE_WINDOW_DAYS} days ({hit} of {seen}): the weights are frozen for review")
        line["frozen"] = state["frozen"]
    return state, line


def log_update(root: Path, state: dict, line: dict) -> None:
    append_json_line(pool_v2_updates_file(root, state["sum_id"]), line)
