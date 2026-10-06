"""Every voice's forecast for one read: the voices pool_v1 already has, renamed, plus the three learners.

Today's voices come from the live system's own records, never recomputed:
    sum "next_60" (an end-price sum)    pool_v1's per-read snapshot in hour/{day}.jsonl: its experts, renamed through name_map
                                         (historical_odds, historical_odds_calibrated, blend_50_50, question_tilt, jev_mix_*_percent;
                                         JEV's own call is there as jev_mix_100_percent, so it is not added a second time)
    sum "average_30" (the phone's call)  pool_v1 keeps no per-read snapshot for it, so its voices are the row's own columns:
                                         historical_odds (clock_probs), jev_own (jev_probs), blend_50_50 (shown_probs)
The learners (additive_scorer, matcher, jev_corrected) forecast from the day's fits; a learner that is asleep or cannot
forecast a read is simply absent, so pool_v2 neither mixes nor updates it for that read.
"""
from __future__ import annotations

from ..scores import floored
from .name_map import REFERENCE_VOICE, rename_voice

END_PRICE_SUMS = {"next_30": "next_30", "next_60": "next_60"}   # sum_id -> the horizon key of pool_v1's snapshot


def existing_voice_forecasts(row, pool_v1_snapshot: dict | None) -> dict[str, dict[str, float]]:
    """The voices pool_v1 already has, for this read and sum, by voice name."""
    out: dict[str, dict[str, float]] = {}
    horizon = END_PRICE_SUMS.get(row.sum_id)
    snapshot = ((pool_v1_snapshot or {}).get(horizon) or {}) if horizon else {}
    experts = snapshot.get("experts")
    if isinstance(experts, dict) and experts:
        for old, probs in experts.items():
            if isinstance(probs, dict):
                out[rename_voice(old)] = floored(probs)
        if isinstance(snapshot.get("pool"), dict):
            out["pool_v1"] = floored(snapshot["pool"])          # scored beside the others, never mixed into pool_v2
    else:
        if row.pool_v1_probs is not None:
            out["pool_v1"] = floored(row.pool_v1_probs)
        if row.jev_own_probs is not None:
            out["jev_own"] = floored(row.jev_own_probs)
        if row.shown_probs is not None:
            out["blend_50_50"] = floored(row.shown_probs)
    if row.historical_odds_probs is not None:
        out.setdefault(REFERENCE_VOICE, floored(row.historical_odds_probs))
    return out


def learner_forecasts(fits: dict, row, candidates) -> tuple[dict[str, dict[str, float]], dict[str, dict]]:
    """The three learners' forecasts from the day's fits, the matcher comparing against ``candidates`` (the matrix's
    trainable rows): ({voice: probs}, {voice: diagnostics}); an absent voice is asleep."""
    from .additive_scorer import forecast_with_additive_scorer
    from .jev_corrected import forecast_with_jev_corrected
    from .matcher import forecast_with_matcher
    out: dict[str, dict[str, float]] = {}
    notes: dict[str, dict] = {}
    scorer = fits.get("additive_scorer")
    if scorer is not None:
        probs = forecast_with_additive_scorer(scorer, row)
        if probs is not None:
            out["additive_scorer"] = probs
    matcher = fits.get("matcher")
    if matcher is not None:
        probs, diagnostics = forecast_with_matcher(matcher, row, candidates)
        notes["matcher"] = diagnostics
        if probs is not None:
            out["matcher"] = probs
    corrected = fits.get("jev_corrected")
    if corrected is not None:
        probs = forecast_with_jev_corrected(corrected, row)
        if probs is not None:
            out["jev_corrected"] = probs
    return out, notes


def all_voice_forecasts(fits: dict, row, pool_v1_snapshot: dict | None, candidates) -> tuple[dict[str, dict[str, float]], dict[str, dict]]:
    """Every voice's forecast for the read: existing voices plus the learners that are awake."""
    forecasts = existing_voice_forecasts(row, pool_v1_snapshot)
    learned, notes = learner_forecasts(fits, row, candidates)
    forecasts.update(learned)
    return forecasts, notes
