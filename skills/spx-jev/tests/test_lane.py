"""The lanes, their folders, and the launchd jobs held to the lanes' schedules."""
from __future__ import annotations

import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from spx_jev.cuts import NEXT_30_FLAT_BAND_SIGMA, NEXT_60_FLAT_BAND_SIGMA
from spx_jev.lane import LANES, LIVE, RECORD, TAPE

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
LAUNCHD = SKILL / "launchd"


def _job(name: str) -> dict:
    return plistlib.loads((LAUNCHD / f"{name}.plist.template").read_bytes())


def _pacific(hhmm: str) -> tuple[int, int]:
    """A market time as the box's launchd reads it: Pacific, three hours behind New York all year."""
    return int(hhmm[:2]) - 3, int(hhmm[3:])


def test_the_live_lane_and_the_opening_lane():
    assert LANES == {"live": LIVE, "tape": TAPE}
    assert LIVE.out_dir == "spx_jev" and LIVE.questions.name == "spx_questions.json" and LIVE.hour_doc.name == "spx_hour.json"
    assert (LIVE.key, TAPE.key) == ("thirty_minute", "opening_five_minute") and TAPE.questions == LIVE.questions
    assert LIVE.horizons == {"next_30": (30, NEXT_30_FLAT_BAND_SIGMA), "next_60": (60, NEXT_60_FLAT_BAND_SIGMA)}
    assert (LIVE.primary, LIVE.cadence, LIVE.tag, LIVE.bar_clock, LIVE.clock_blend, LIVE.bar_gap_min) == ("next_30", True, None, False, True, 2)
    assert LIVE.pool is True and TAPE.pool is False                     # only the live lane feeds the learning loop
    assert TAPE.out_dir == "spx_jev/lanes/tape"
    assert TAPE.horizons == {"next_10": (10, RECORD)} and TAPE.primary == "next_10"
    assert (TAPE.cadence, TAPE.tag, TAPE.bar_clock, TAPE.clock_blend, TAPE.bar_gap_min) == (False, "tape", True, False, 0)
    assert len(TAPE.schedule) == 12 and TAPE.schedule[0] == "09:35" and TAPE.schedule[-1] == "10:30" and TAPE.close_out == "10:42"


def test_a_tagged_lane_refuses_to_run_without_a_folder_of_its_own(tmp_path):
    assert LIVE.folder(tmp_path) == tmp_path / "spx_jev" and TAPE.folder(tmp_path) == tmp_path / "spx_jev" / "lanes" / "tape"
    with pytest.raises(ValueError, match="no folder of its own"):
        replace(TAPE, out_dir=None).folder(tmp_path)
    with pytest.raises(ValueError, match="must not write into"):
        TAPE.folder(tmp_path, tmp_path / "spx_jev")


def test_the_live_job_fires_at_02_and_32_through_the_session():
    fires = [(e["Hour"], e["Minute"]) for e in _job("com.mirai-station.spx-jev")["StartCalendarInterval"]]
    assert fires == [_pacific(t) for t in LIVE.read_times()] and fires[0] == (6, 32) and fires[-1] == (13, 2)


def test_the_tape_job_fires_at_the_lanes_reads_and_its_close_out():
    fires = [(e["Hour"], e["Minute"]) for e in _job("com.mirai-station.spx-jev-tape")["StartCalendarInterval"]]
    assert fires == [_pacific(t) for t in TAPE.schedule + (TAPE.close_out,)]


@pytest.mark.parametrize("name, script, lane", [("com.mirai-station.spx-jev", "run-spx-jev.sh", None),
                                                ("com.mirai-station.spx-jev-tape", "run-spx-jev.sh", "tape"),
                                                ("com.mirai-station.spx-jev-bars", "run-spx-jev-bars.sh", None),
                                                ("com.mirai-station.spx-jev-context", "run-spx-jev-context.sh", None),
                                                ("com.mirai-station.spx-jev-save-day", "run-spx-jev-save-day.sh", None)])
def test_each_job_runs_its_repo_script_and_logs_to_its_own_files(name, script, lane):
    job = _job(name)
    command = job["ProgramArguments"][2]
    assert job["Label"] == name and command.split()[0].endswith(f"runtime/scripts/{script}")
    assert (REPO / "runtime" / "scripts" / script).is_file()
    assert (lane is None) == ("--lane" not in command) and (lane is None or command.endswith(f"--lane {lane}"))
    assert job["StandardOutPath"] == f"/tmp/mirai-station.{name.split('.')[-1]}.out"
    assert job["EnvironmentVariables"]["TZ"] == "America/New_York" and job["RunAtLoad"] is False


def test_the_feeds_run_every_minute_and_the_day_is_saved_after_the_close():
    assert _job("com.mirai-station.spx-jev-bars")["StartInterval"] == 60
    assert _job("com.mirai-station.spx-jev-context")["StartInterval"] == 60
    save = _job("com.mirai-station.spx-jev-save-day")["StartCalendarInterval"]
    assert (save["Hour"], save["Minute"]) == _pacific("16:20")        # after the bars feed's last run and gex-polarity's save


def test_the_installed_plists_match_their_templates():
    """runtime/launchd holds what install-launchd.sh loads; each SPX job there is its template byte for
    byte, so the schedules the tests above hold are the ones launchd runs, and no SPX plist is loaded
    that has no template."""
    templates = {p.name.removesuffix(".template") for p in LAUNCHD.glob("*.plist.template")}
    installed = {p.name for p in (REPO / "runtime" / "launchd").glob("com.mirai-station.spx-jev*.plist")}
    assert len(templates) == 5 and installed == templates
    for name in sorted(templates):
        assert (REPO / "runtime" / "launchd" / name).read_bytes() == (LAUNCHD / f"{name}.template").read_bytes(), name
