"""The one place old names meet the diagram's names, and the few shared words every module uses.

The live store keeps its folder names; this system reads them through STORE_NAMES so every other module uses the
diagram's words. Pool_v1's expert names become voice names the same way (VOICE_NAMES). Nothing on disk is renamed.
STAGES, SUMS_BY_LANE and REFERENCE_VOICE live here too, so the hook, the nightly job and the learners share one spelling, and
CUT_OVER_DAY (cuts.py's) is read from here: the day the live lane stopped asking the pre-merge questions.
"""
from __future__ import annotations

from pathlib import Path

from ..cuts import CUT_OVER_DAY  # noqa: F401  (the cut-over day is cuts.py's; this system reads it from here)
from .paths import spx_jev_dir

# diagram name -> the live store's folder under state/spx_jev/store/ (hive-partitioned Parquet, one folder per day)
STORE_NAMES = {
    "jev_reads": "reads",                      # one row per read of any lane
    "jev_answers": "answers",                  # JEV's answer per question per read (layer 3)
    "forecasts_at_read_time": "calls",         # one row per sum per read: historical odds, JEV's call, the shown call, pool_v1's mix
    "graded_results": "average_grades",        # the average-price grade per sum per read (the older end-price grade is not used)
}

# pool_v1's expert names (spx_jev.pool) -> voice names
VOICE_NAMES = {
    "baseline": "historical_odds",
    "baseline_cal": "historical_odds_calibrated",
    "clock": "historical_odds",
    "clock_cal": "historical_odds_calibrated",
    "blend50": "blend_50_50",
    "blend50_exact": "blend_50_50_as_shown",
    "questions": "question_tilt",
    "jev": "jev_own",
    "pool": "pool_v1",
    **{f"jev_share_{s:.1f}": f"jev_mix_{int(round(s * 100))}_percent" for s in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)},
}

# plain names for the phone and reports
PLAIN_NAMES = {
    "historical_odds": "Historical odds",
    "historical_odds_calibrated": "Historical odds (tuned)",
    "blend_50_50": "50/50 blend",
    "blend_50_50_as_shown": "50/50 blend (as shown)",
    "question_tilt": "Question tilt",
    "jev_own": "JEV's own call",
    "additive_scorer": "Additive scorer",
    "matcher": "Matcher",
    "jev_corrected": "Corrected JEV",
    "pool_v1": "Old forecast (retired)",
    "pool_v2": "Combined forecast",          # the user-facing name of pool_v2, Will 2026-10-06
    **{f"jev_mix_{p}_percent": f"JEV mix {p}%" for p in (0, 20, 40, 60, 80, 100)},
}

LEARNER_NAMES = ("additive_scorer", "matcher", "jev_corrected")
REFERENCE_VOICE = "historical_odds"            # every skill and every pool_v2 step is measured against this voice
STAGES = ("move", "direction")                 # the two questions every forecast is split into: will it move? which way?
SUMS_BY_LANE = {"live": ("average_30", "next_60")}   # what each lane learns on (only the live lane feeds this system)
SUM_WINDOW_MINUTES = {"average_30": 30, "next_30": 30, "next_60": 60, "average_10": 10}   # how far ahead each sum looks


def store_path(state_dir: Path | str, new_name: str) -> Path:
    """The folder of a store table by its diagram name, e.g. store_path(S, "graded_results") -> .../store/average_grades."""
    try:
        old = STORE_NAMES[new_name]
    except KeyError as e:
        raise KeyError(f"no store is named {new_name!r}; known: {sorted(STORE_NAMES)}") from e
    return spx_jev_dir(state_dir) / "store" / old


def rename_voice(old: str) -> str:
    """A pool_v1 expert name as a voice name; an unknown name is kept as it is."""
    return VOICE_NAMES.get(old, old)


def plain_name(voice: str) -> str:
    return PLAIN_NAMES.get(voice, voice.replace("_", " "))
