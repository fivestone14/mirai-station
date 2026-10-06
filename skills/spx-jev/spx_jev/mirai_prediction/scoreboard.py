"""The phone card's file: after N graded reads, how right each voice is and how it compares with the historical odds.

For each sum the lane learns on, and each voice (the existing voices, the learners, pool_v2, and pool_v1 where it has a
per-read mix; for the phone's call, average_30, the call as shown stands in for pool_v1 and is labelled so):

    reads_count                        graded reads the voice forecast
    right_pct                          share where the band it called (the largest probability) was the outcome
    skill_vs_historical_odds_pct       1 - (its mean penalty / the historical odds' mean penalty) over the same reads, in percent
    interval_90                        a 90% range for the skill from resampling whole days (200 draws)
    status                             Too early (under 100 reads, or the range crosses zero under 300), No edge (crosses zero
                                       at 300+), Edge (the whole range is above zero), Worse (the whole range is below zero)
Also a daily line of pool_v2's and pool_v1's skill, so the card can draw it.
"""
from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from .name_map import LEARNER_NAMES, REFERENCE_VOICE, plain_name
from .paths import now_utc_iso, scoreboard_file, voice_scores_table_dir, write_json_atomically

POOL_V1_STAND_IN = {"average_30": "blend_50_50", "average_10": "blend_50_50"}   # sums with no stored pool_v1 mix per read
MIN_READS_FOR_STATUS = 100
MIN_READS_FOR_VERDICT = 300
BOOTSTRAP_DRAWS = 200
BOOTSTRAP_SEED = 7


def load_all_scores(root: Path) -> list[dict]:
    folder = voice_scores_table_dir(root)
    rows: list[dict] = []
    if not folder.exists():
        return rows
    for part in sorted(folder.glob("day=*/part-0.parquet")):
        day = part.parent.name[len("day="):]
        for r in pq.read_table(part).to_pylist():
            r["day"] = day
            rows.append(r)
    return rows


def skill_pct(rows: list[dict]) -> float | None:
    """1 - mean penalty / mean reference penalty, in percent, over rows that have a reference penalty."""
    paired = [r for r in rows if r.get("reference_penalty") is not None]
    if not paired:
        return None
    ref = sum(r["reference_penalty"] for r in paired)
    if ref <= 0:
        return None
    return 100.0 * (1 - sum(r["penalty"] for r in paired) / ref)


def bootstrap_interval(rows: list[dict], draws: int = BOOTSTRAP_DRAWS) -> list[float] | None:
    """A 90% range for the skill, resampling whole days."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("reference_penalty") is not None:
            by_day[r["day"]].append(r)
    days = sorted(by_day)
    if len(days) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    values = []
    for _ in range(draws):
        sample = [r for d in rng.choices(days, k=len(days)) for r in by_day[d]]
        s = skill_pct(sample)
        if s is not None:
            values.append(s)
    if not values:
        return None
    values.sort()
    return [round(values[int(0.05 * (len(values) - 1))], 2), round(values[int(0.95 * (len(values) - 1))], 2)]


def status_word(reads_count: int, interval: list[float] | None) -> str:
    if reads_count < MIN_READS_FOR_STATUS or interval is None:
        return "Too early"
    lo, hi = interval
    if lo > 0:
        return "Edge"
    if hi < 0:
        return "Worse"
    return "Too early" if reads_count < MIN_READS_FOR_VERDICT else "No edge"


def voice_summary(name: str, rows: list[dict], label: str | None = None) -> dict:
    n = len(rows)
    right = sum(1 for r in rows if r["right"])
    skill = skill_pct(rows)
    interval = bootstrap_interval(rows)
    return {"name": name, "plain_name": label or plain_name(name), "reads_count": n,
            "right_pct": round(100.0 * right / n, 1) if n else None,
            "skill_vs_historical_odds_pct": round(skill, 2) if skill is not None else None,
            "interval_90": interval, "status": status_word(n, interval)}


def build_scoreboard(root: Path, lane: str, sums: tuple[str, ...]) -> dict:
    scores = [r for r in load_all_scores(root) if r.get("lane") == lane]
    out = {"built_at": now_utc_iso(), "lane": lane, "sums": {}}
    for sum_id in sums:
        rows = [r for r in scores if r["sum_id"] == sum_id]
        by_voice: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_voice[r["voice_name"]].append(r)
        if not rows:
            out["sums"][sum_id] = {"graded_reads_count": 0, "voices": [], "pool_v1": None, "pool_v2": None, "daily_skill": []}
            continue
        pool_v1_name = "pool_v1" if "pool_v1" in by_voice else POOL_V1_STAND_IN.get(sum_id)
        pool_v1 = (voice_summary(pool_v1_name, by_voice[pool_v1_name], "Pool 1" + (" (the call as shown)" if pool_v1_name != "pool_v1" else ""))
                   if pool_v1_name in by_voice else None)
        pool_v2 = voice_summary("pool_v2", by_voice["pool_v2"]) if "pool_v2" in by_voice else None
        order = [REFERENCE_VOICE, "jev_own", "blend_50_50", "question_tilt"] + list(LEARNER_NAMES)
        voices = [voice_summary(v, by_voice[v]) for v in order if v in by_voice]
        voices += [voice_summary(v, by_voice[v]) for v in sorted(by_voice) if v not in order and v not in ("pool_v1", "pool_v2")]
        daily = []
        rows_by_day: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            rows_by_day[r["day"]].append(r)
        for day, day_rows in sorted(rows_by_day.items()):
            p2 = skill_pct([r for r in day_rows if r["voice_name"] == "pool_v2"])
            p1 = skill_pct([r for r in day_rows if r["voice_name"] == pool_v1_name]) if pool_v1_name else None
            daily.append({"day": day, "pool_v2_pct": round(p2, 2) if p2 is not None else None, "pool_v1_pct": round(p1, 2) if p1 is not None else None})
        graded = len({r["read_id"] for r in rows})
        out["sums"][sum_id] = {"graded_reads_count": graded, "since_day": min(r["day"] for r in rows), "voices": voices,
                               "pool_v1": pool_v1, "pool_v2": pool_v2, "daily_skill": daily}
    return out


def write_scoreboard(root: Path, lane: str, sums: tuple[str, ...]) -> Path:
    board = build_scoreboard(root, lane, sums)
    path = scoreboard_file(root)
    write_json_atomically(path, board)
    return path
