"""A lane: one schedule, one question doc, one sums doc, one folder under state/jev/, one grader.

LIVE is the service as it has run since 2026-09-22: :02 and :32, the 30- and 60-minute sums in
sigma bands, the cadence and the time-of-day blend, everything under state/jev/. TAPE is the
opening lane of the plan of 2026-09-25: every 5 minutes from 09:35 to 10:30, each read stamped
at the newest finished bar, one 10-minute sum in tape units, every question asked afresh on every
read, everything under state/jev/lanes/tape/. Every step takes a lane and defaults to LIVE, so the
live lane runs exactly as before whether or not another lane exists.

A lane with a ``schedule`` names its reads in market time; the launchd job fires at each of them, and
once more at ``close_out``, a run that asks JEV nothing and only grades the morning's last calls and
refreshes the card (service.close_out). A test holds the plist to these times.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"
LIVE_DIR = "jev"            # the live lane's folder under the state dir; no other lane may write there
RECORD = "record"           # a horizon whose band is the one stored on each record, in dollars from the tape unit


def horizons(doc_path: Path | str) -> dict[str, tuple[int, float | str]]:
    """``{qid: (minutes, flat band)}`` from a sums doc, so grading and asking share one number. The
    band is a width in sigma, or RECORD when the doc sizes it from the tape unit stored on the record."""
    with open(doc_path, encoding="utf-8") as f:
        d = json.load(f)
    return {qid: (int(h["minutes"]), float(h["flat_band_sigma"]) if "flat_band_sigma" in h else RECORD)
            for qid, h in d["horizons"].items()}


@dataclass(frozen=True)
class Lane:
    name: str
    out_dir: str | None                              # its folder under the state dir
    questions: Path                                  # the step-2 questions it asks
    hour_doc: Path                                   # the sums it asks over the answers
    horizons: dict[str, tuple[int, float | str]]     # {qid: (minutes, flat band)}, see horizons()
    primary: str                                     # the sum on the card, and the one the weights learn from
    min_graded: int                                  # fresh pairs a question needs before its weight can move
    pair_gap_min: int                                # a question pairs only with reads this far after its last paired read
    cadence: bool                                    # ask on a cadence and hold in between, or ask everything afresh
    tag: str | None                                  # written on the records, hour records and card; None leaves them as they were
    bar_clock: bool = False                          # stamp each read at the newest finished bar and carry the tape unit;
                                                     # a finished day's bars that stop are then the end of its day
    clock_blend: bool = True                         # blend the sum with the time-of-day odds (clock.py)
    bar_gap_min: int = 2                             # the bar standing for a mark may be this many minutes early; 0 is the exact bar
    shuffles: int = 0                                # day-block shuffles a question's information must beat; 0 turns the test off
    schedule: tuple[str, ...] = ()                   # the reads, "HH:MM" market time; empty for the live lane (:02 and :32 all session)
    close_out: str | None = None                     # "HH:MM" market time of the grade-only run after the last read

    def folder(self, state_dir: Path | str, out_dir: Path | str | None = None) -> Path:
        """Where the lane writes. A tagged lane must have a folder of its own: with none, or pointed at
        the live folder, it refuses rather than write into the live records."""
        live = Path(state_dir) / LIVE_DIR
        out = Path(out_dir) if out_dir else Path(state_dir) / self.out_dir if self.out_dir else None
        if self.tag and (out is None or out.resolve() == live.resolve()):
            raise ValueError(f"lane {self.name} has no folder of its own; it must not write into {live}")
        return out if out is not None else live


LIVE = Lane(name="live", out_dir=LIVE_DIR, questions=QUESTIONS_DIR / "sndk_pro.json",
            hour_doc=QUESTIONS_DIR / "sndk_hour.json", horizons=horizons(QUESTIONS_DIR / "sndk_hour.json"),
            primary="next_30", min_graded=40, pair_gap_min=0, cadence=True, tag=None)

TAPE_EVERY_MIN = 5
TAPE = Lane(name="tape", out_dir="jev/lanes/tape", questions=QUESTIONS_DIR / "sndk_lane_tape.json",
            hour_doc=QUESTIONS_DIR / "sndk_lane_hour.json", horizons=horizons(QUESTIONS_DIR / "sndk_lane_hour.json"),
            primary="next_10", min_graded=120, pair_gap_min=10, cadence=False, tag="tape",
            bar_clock=True, clock_blend=False, bar_gap_min=0, shuffles=1000,
            # 09:35 to 10:30 every 5 minutes; the 10:30 call's mark is 10:40, so the close-out at 10:42
            # finds the bar it needs and grades the morning's last two calls the same day
            schedule=tuple(f"{(575 + TAPE_EVERY_MIN * k) // 60:02d}:{(575 + TAPE_EVERY_MIN * k) % 60:02d}" for k in range(12)),
            close_out="10:42")

LANES = {"live": LIVE, "tape": TAPE}
