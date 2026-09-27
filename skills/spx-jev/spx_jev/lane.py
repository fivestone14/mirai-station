"""A lane: one schedule, one question doc, one sums doc, one folder under state/spx_jev/, one grader.

LIVE reads at :02 and :32 all session: the 30- and 60-minute sums in sigma bands, the cadence and
the time-of-day blend, everything under state/spx_jev/. TAPE is the opening lane: every 5 minutes
from 09:35 to 10:30, each read stamped at the newest finished bar, one 10-minute sum in tape units,
every question asked afresh on every read, everything under state/spx_jev/lanes/tape/. Every step
takes a lane and defaults to LIVE.

A lane with a ``schedule`` names its reads in market time; the launchd job fires at each of them, and
once more at ``close_out``, a run that asks JEV nothing and only grades the morning's last calls and
refreshes the card (service.close_out). A test holds the plist template to these times.

The sums' horizons and bands live here, from cuts.py, so the grader, the clock, the card and the
question text all read one number.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .archive import ARCHIVE_SUBDIR
from .cuts import NEXT_30_FLAT_BAND_SIGMA, NEXT_60_FLAT_BAND_SIGMA

QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"
LIVE_DIR = "spx_jev"        # the live lane's folder under the state dir; no other lane may write there
RECORD = "record"           # a horizon whose band is the one stored on each record, in points from the tape unit


@dataclass(frozen=True)
class Lane:
    name: str
    out_dir: str | None                              # its folder under the state dir
    questions: Path                                  # the step-2 questions it asks
    hour_doc: Path                                   # the sums it asks over the answers
    horizons: dict[str, tuple[int, float | str]]     # {qid: (minutes, flat band in sigma, or RECORD)}
    primary: str                                     # the sum on the card, and the one the question weights learn from
    cadence: bool                                    # ask on a cadence and hold in between, or ask everything afresh
    tag: str | None                                  # written on the records, hour records and card; None for the live lane
    bar_clock: bool = False                          # stamp each read at the newest finished bar and carry the tape unit;
                                                     # a finished day's bars that stop are then the end of its day
    clock_blend: bool = True                         # blend the sum with the time-of-day odds (clock.py)
    bar_gap_min: int = 2                             # the bar standing for a mark may be this many minutes early; 0 is the exact bar
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

    def archive_folder(self, state_dir: Path | str, out_dir: Path | str | None = None) -> Path:
        """Where the lane's raw archive goes: the station's one archive, shared by both lanes, while the
        lane writes into its own folder under the state dir; ``<out_dir>/archive`` when it was pointed
        anywhere else, so a run into a scratch folder keeps every record it makes there."""
        out = self.folder(state_dir, out_dir)
        if out.resolve() == (Path(state_dir) / self.out_dir).resolve():
            return Path(state_dir) / ARCHIVE_SUBDIR
        return out / "archive"


LIVE = Lane(name="live", out_dir=LIVE_DIR, questions=QUESTIONS_DIR / "spx_live.json", hour_doc=QUESTIONS_DIR / "spx_hour.json",
            horizons={"next_30": (30, NEXT_30_FLAT_BAND_SIGMA), "next_60": (60, NEXT_60_FLAT_BAND_SIGMA)},
            primary="next_30", cadence=True, tag=None)

TAPE_EVERY_MIN = 5
TAPE = Lane(name="tape", out_dir=f"{LIVE_DIR}/lanes/tape", questions=QUESTIONS_DIR / "spx_lane_tape.json",
            hour_doc=QUESTIONS_DIR / "spx_lane_hour.json", horizons={"next_10": (10, RECORD)},
            primary="next_10", cadence=False, tag="tape", bar_clock=True, clock_blend=False, bar_gap_min=0,
            # 09:35 to 10:30 every 5 minutes; the 10:30 call's mark is 10:40, so the close-out at 10:42
            # finds the bar it needs and grades the morning's last two calls the same day
            schedule=tuple(f"{(575 + TAPE_EVERY_MIN * k) // 60:02d}:{(575 + TAPE_EVERY_MIN * k) % 60:02d}" for k in range(12)),
            close_out="10:42")

LANES = {"live": LIVE, "tape": TAPE}
