"""A lane: one schedule, one sums doc, one folder under state/spx_jev/, one grader, and its share of the
question doc (the questions whose ``lanes`` name its ``key``, loaded with ask.load_questions).

LIVE reads at :02 and :32 all session: the 30- and 60-minute sums in sigma bands, the cadence and
the time-of-day blend, everything under state/spx_jev/. TAPE is the opening lane: every 5 minutes
from 09:35 to 10:30, each read stamped at the newest finished bar, one 10-minute sum in tape units,
every question its schedule asks asked afresh (a day constant is asked at 09:35 and held),
everything under state/spx_jev/lanes/tape/. PREMARKET reads at six checkpoints before the open: every
read saves the overnight futures and builds the night's labels from them (a scene without a diary row,
premarket.py), and JEV is asked only at the reads its questions' schedules name (08:48 and 09:28). Its
two sums are graded from the settled open, 10 and 30 minutes on, never from yesterday's close, and
stand unblended: how that window ended on prior sessions (clock.premarket_odds, banded in each session's
morning anchor) forecast it worse than even thirds. With no blend and no validated reference for the
window, it keeps no learning loop and its weights are neutral. Everything under
state/spx_jev/lanes/premarket/. Every step takes a lane and defaults to LIVE.

Every lane asks from one question doc, each question on its own schedule per lane (schedule.py). A lane
with a ``schedule`` names its reads in market time; the launchd job fires at each of them, and
once more at ``close_out``, a run that asks JEV nothing and only grades the morning's last calls and
refreshes the card (service.close_out). A test holds the plist template to these times.

The sums' horizons and bands live here, from cuts.py, so the grader, the clock, the card and the
question text all read one number. Beside its end-price sums every lane asks one more, ``average``: where
the average price over its primary's window sits against the read (the settled open on the premarket
lane), up, flat or down, with no unsure. It is the phone's call and what the average-price grade grades
(grade.integral_line); the end-price sums are asked, graded and learnt from as before, in shadow.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .archive import ARCHIVE_SUBDIR
from .cuts import NEXT_30_FLAT_BAND_SIGMA, NEXT_60_FLAT_BAND_SIGMA, OPEN_10_FLAT_BAND_SIGMA

QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"
LIVE_DIR = "spx_jev"        # the live lane's folder under the state dir; no other lane may write there
RECORD = "record"           # a horizon whose band is the one stored on each record, in points from the tape unit
QUESTIONS = QUESTIONS_DIR / "spx_questions.json"   # every question of both lanes, generated from spec/question_set.json
# The live job fires at :02 and :32 from 09:32 to 16:02 (launchd/com.mirai-station.spx-jev.plist.template).
LIVE_READS = tuple(f"{(572 + 30 * k) // 60:02d}:{(572 + 30 * k) % 60:02d}" for k in range(14))


@dataclass(frozen=True)
class Lane:
    name: str
    key: str                                         # its name in the question doc's ``lanes`` and ``schedule``
    out_dir: str | None                              # its folder under the state dir
    questions: Path                                  # the step-2 question doc it asks from
    hour_doc: Path                                   # the sums it asks over the answers
    horizons: dict[str, tuple[int, float | str]]     # {qid: (minutes, flat band in sigma, or RECORD)}
    primary: str                                     # the sum on the card, and the one the question weights learn from
    cadence: bool                                    # thin its schedule by the learned cadence and hold in between, or ask what is due afresh
    tag: str | None                                  # written on the records, hour records and card; None for the live lane
    bar_clock: bool = False                          # stamp each read at the newest finished bar and carry the tape unit;
                                                     # a finished day's bars that stop are then the end of its day
    clock_blend: bool = True                         # blend the sum with the time-of-day odds (clock.py)
    pool: bool = False                               # write the learning loop's forecasts at each read and learn from them (pool.py)
    bar_gap_min: int = 2                             # the bar standing for a mark may be this many minutes early; 0 is the exact bar
    schedule: tuple[str, ...] = ()                   # the reads, "HH:MM" market time; empty for the live lane (:02 and :32 all session)
    close_out: str | None = None                     # "HH:MM" market time of the grade-only run after the last read
    close_out_after_close: bool = False              # a fire after the day's real close (13:00 on a half day) is the grade-only run
    read_grace_min: int = 2                          # a read stamped this many minutes before one of its read times is that read
    graded_from_settled_open: bool = False           # its sums' horizons run from the settled open (the 09:34 close), not from the read
    average: str | None = None                       # the sum on the average price over the primary's window, up, flat or down, asked in
                                                     # its own request beside the end-price sums: the phone's call, graded on the average

    def read_times(self) -> tuple[str, ...]:
        """The lane's reads, "HH:MM" market time: its schedule, or the live job's :02 and :32."""
        return self.schedule or LIVE_READS

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


LIVE = Lane(name="live", key="thirty_minute", out_dir=LIVE_DIR, questions=QUESTIONS, hour_doc=QUESTIONS_DIR / "spx_hour.json",
            horizons={"next_30": (30, NEXT_30_FLAT_BAND_SIGMA), "next_60": (60, NEXT_60_FLAT_BAND_SIGMA)},
            primary="next_30", cadence=True, tag=None, pool=True, average="average_30",
            # the newest diary row can be up to the service's 6-minute staleness line old when the job fires
            read_grace_min=6,
            # the 15:32 read's 30-minute mark and the 15:02 read's 60-minute one are the closing bar: the 16:02
            # fire (13:02 on a half day) grades them the same day
            close_out_after_close=True)

TAPE_EVERY_MIN = 5
TAPE = Lane(name="tape", key="opening_five_minute", out_dir=f"{LIVE_DIR}/lanes/tape", questions=QUESTIONS,
            hour_doc=QUESTIONS_DIR / "spx_lane_hour.json", horizons={"next_10": (10, RECORD)},
            primary="next_10", cadence=False, tag="tape", bar_clock=True, clock_blend=False, bar_gap_min=0, average="average_10",
            # 09:35 to 10:30 every 5 minutes; the 10:30 call's mark is 10:40, so the close-out at 10:42
            # finds the bar it needs and grades the morning's last two calls the same day
            schedule=tuple(f"{(575 + TAPE_EVERY_MIN * k) // 60:02d}:{(575 + TAPE_EVERY_MIN * k) % 60:02d}" for k in range(12)),
            close_out="10:42")

# The checkpoints before the open, market time: Tokyo has closed, Europe's first half hour, the Europe
# morning, the 08:30 report window, 09:05, and the final read.
PREMARKET = Lane(name="premarket", key="premarket", out_dir=f"{LIVE_DIR}/lanes/premarket", questions=QUESTIONS,
                 hour_doc=QUESTIONS_DIR / "spx_premarket_hour.json",
                 horizons={"open_10": (10, OPEN_10_FLAT_BAND_SIGMA), "open_30": (30, NEXT_30_FLAT_BAND_SIGMA)},
                 primary="open_30", cadence=False, tag="premarket", clock_blend=False, average="open_average_30",
                 schedule=("02:35", "03:35", "08:05", "08:48", "09:05", "09:28"),
                 # the 30-minute mark is 10:04: the close-out at 10:06 finds its bar and grades the morning's calls
                 close_out="10:06", graded_from_settled_open=True)

LANES = {"live": LIVE, "tape": TAPE, "premarket": PREMARKET}
LANES_BY_KEY = {lane.key: lane for lane in LANES.values()}
