"""Pool 2 as the call: the Mirai Prediction System forecasts the read inside the live service, and Pool 2's mix is the
call the phone shows and the grader grades. Since 2026-10-06 (Will: "retire Pool 1, go with Pool 2's forecast").

    forecast_now_with_voices
                   every voice forecasts the read from the records the service has in memory (nothing on disk yet), the
                   raw lines are written exactly as the detached hook would write them, and Pool 2's mix per sum is returned
                   with the voices it mixed per sum, with their say
    take_over      the sums with Pool 2's mix as their pick and probabilities, the exact 50/50 blend kept beside each as
                   blend50_exact (so the phone can still say what it replaced), shown_source = SHOWN_SOURCE, and the voices
                   that went into each as ``voices``, most say first (the phone's "what went into it")

Any failure leaves the call on its blend; the service then starts the detached hook as before, so the day's lines are
never lost. Pool 1 (spx_jev.pool) keeps running quietly as a source of voices; it is no longer shown or graded as the call.
"""
from __future__ import annotations

from pathlib import Path

from .name_map import plain_name
from .read_hook import forecast_read

SHOWN_SOURCE = "pool_v2"
BUDGET_SECONDS = 5.0


def forecast_now_with_voices(state_dir: Path | str, lane: str, day: str, read_record: dict,
                             hour_record: dict) -> tuple[dict[str, dict[str, float]], dict[str, dict]]:
    """({sum_id: Pool 2's probabilities}, {sum_id: the voices it mixed, read_hook.mixed_voices}) for the read the service
    is making, from its in-memory records."""
    snapshots = {hour_record["row_ts"]: hour_record["pool"]} if isinstance(hour_record.get("pool"), dict) else {}
    result = forecast_read(state_dir, lane, day, read_record.get("read_id"), source="live", pool_v1_snapshots=snapshots,
                           budget_seconds=BUDGET_SECONDS, read_record=read_record, hour_record=hour_record)
    return result.get("pool_v2") or {}, result.get("voices") or {}


def voice_list(mixed: dict) -> list[dict]:
    """One sum's voices (read_hook.mixed_voices) as the phone lists them: name, plain name, probabilities and share of
    the say, most say first."""
    shares = mixed["shares"]
    rows = [{"name": v, "plain_name": plain_name(v), "probabilities": {k: round(float(x), 4) for k, x in probs.items()},
             "share": round(float(shares[v]), 4)} for v, probs in mixed["probabilities"].items() if v in shares]
    return sorted(rows, key=lambda r: -r["share"])


def _with_voices(sum_doc: dict, voices: dict[str, dict], sum_id: str) -> dict:
    """The sum with its voices beside it; a sum without them, or whose voices cannot be listed, as it was: the list is
    the phone's alone and never costs the call."""
    try:
        listed = voice_list(voices[sum_id]) if sum_id in voices else None
    except Exception:
        return sum_doc
    return {**sum_doc, "voices": listed} if listed else sum_doc


def _with_call(sum_doc: dict, probs: dict[str, float]) -> dict:
    """One sum with Pool 2's probabilities as its call; the blend it replaced kept beside it."""
    return {**sum_doc, "pick": max(probs, key=probs.get), "probabilities": probs,
            "blend50_exact": sum_doc.get("blend50_exact") or sum_doc.get("probabilities")}


def take_over(hour: dict, forecasts: dict[str, dict[str, float]], lane, voices: dict[str, dict] | None = None) -> dict:
    """The hour's sums with Pool 2's mix as the call wherever Pool 2 forecast the sum: the average-price call under
    ``average`` (and its ``by``), and each end-price sum under ``by`` (the primary's copied to the top),
    each with the ``voices`` that went into it (forecast_now_with_voices) when there are any."""
    voices = voices or {}
    out = dict(hour)
    average = out.get("average")
    if lane.average and isinstance(average, dict) and isinstance(average.get("probabilities"), dict) and lane.average in forecasts:
        called = _with_call(average, forecasts[lane.average])
        by = dict(average.get("by") or {})
        by[lane.average] = _with_call(by.get(lane.average, {}), forecasts[lane.average])
        out["average"] = _with_voices({**called, "by": by, "shown_source": SHOWN_SOURCE}, voices, lane.average)
    by = dict(out.get("by") or {})
    changed = False
    for horizon, sum_doc in by.items():
        if horizon in forecasts and isinstance(sum_doc, dict) and isinstance(sum_doc.get("probabilities"), dict):
            by[horizon] = _with_voices({**_with_call(sum_doc, forecasts[horizon]), "shown_source": SHOWN_SOURCE}, voices, horizon)
            changed = True
    if changed:
        out["by"] = by
        out["shown_source"] = SHOWN_SOURCE
        primary = by.get(out.get("primary") or lane.primary)
        if primary and "blend50_exact" in primary and (out.get("primary") or lane.primary) in forecasts:
            out.update({"pick": primary["pick"], "probabilities": primary["probabilities"], "blend50_exact": primary["blend50_exact"]})
    return out
