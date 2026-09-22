"""The station's launchd jobs, checked as definitions.

No other test reads runtime/launchd, so a plist pointing at a renamed script, a
job the installer never names, or a scanner slowed past its dead-man's ceiling
passes every other test and is noticed only when the data stops. Each test here
holds one rule the job definitions are meant to keep, and every number it
compares comes from a plist or from a module's own constant.

Nothing is loaded into launchd and the plists are only parsed. A run script is
executed only as a copy inside a throwaway stand-in station (no env.sh, no
Keychain, no network, no real state), to see what its market-hours gate does.
"""
import ast
import math
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from watch.intraday import market_status, sndk_deadman

REPO = Path(__file__).resolve().parents[3]
LAUNCHD = REPO / "runtime" / "launchd"
SCRIPTS = REPO / "runtime" / "scripts"
SNDK_PRO = REPO / "skills" / "sndk-pro"
SCANNER = SNDK_PRO / "sndk_hunter.py"
READER = SNDK_PRO / "sndk_read.py"

PLISTS = sorted(LAUNCHD.glob("*.plist"))
SCHEDULE_KEYS = ("StartInterval", "StartCalendarInterval", "KeepAlive")
LOG_KEYS = ("StandardOutPath", "StandardErrorPath")
# env.sh finds the checkout two levels above its own directory, so a job's
# program path is always "<checkout>/runtime/scripts/<script>".
SCRIPTS_REL = "runtime/scripts/"
SHELL_VARS = {"MIRAI_STATION_ROOT": str(REPO), "SCRIPT_DIR": str(SCRIPTS)}


def _job(path):
    # launchd's parser accepts "--" inside an XML comment and expat does not
    # (sndk-bars documents `sndk_bars.py --day` in one), so comments are dropped
    # rather than failing a plist launchd itself loads.
    xml = re.sub(rb"<!--.*?-->", b"", Path(path).read_bytes(), flags=re.S)
    return plistlib.loads(xml)


def _shell_array(script, name):
    """The words of a bash `NAME=( ... )` array, comments dropped."""
    m = re.search(rf"^{name}=\((.*?)^\)", script.read_text(), re.M | re.S)
    assert m, f"{script.name} no longer defines {name}=( ... )"
    return shlex.split(m.group(1), comments=True)


def _program(job):
    """What the job executes: the command inside `bash -lc "..."` when it goes
    through a shell, otherwise the program itself."""
    argv = job.get("ProgramArguments") or [job.get("Program")]
    if Path(argv[0]).name in ("bash", "sh", "zsh"):
        for flag, command in zip(argv[1:], argv[2:]):
            if flag.startswith("-") and "c" in flag:
                return shlex.split(command)[0]
    return argv[0]


def _run_script(job):
    """The repo run script a job starts, or None when it starts a system tool."""
    program = _program(job)
    if SCRIPTS_REL not in program:
        return None
    return SCRIPTS / program.split(SCRIPTS_REL, 1)[1]


def _expand(word):
    for var, value in SHELL_VARS.items():
        word = word.replace("${%s}" % var, value).replace("$" + var, value)
    return word


def _launches(script):
    """(what, candidate files) for everything a run script sources or hands to
    Python. Any one candidate existing is enough.

    Walks the script's words in order and follows its `cd`s, so `-m watch.cli`
    is looked up in the directory the script really runs it from."""
    # a `\` line continuation survives shlex as a bare newline word
    words = [_expand(w) for w in shlex.split(script.read_text(), comments=True) if w.strip()]
    cwd, needs = None, []
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if word == "cd":
            cwd = None if "$" in nxt else Path(nxt)
        elif word == "source":
            needs.append((f"source {nxt}", [Path(nxt)]))
        elif "/bin/python" in word:
            needs.extend(_python_needs(words[i + 1:], cwd))
    return needs


def _python_needs(args, cwd):
    """(what, candidate files) for one `python ...` call.

    A `-c` snippet counts for the repo modules it imports: that snippet is the
    market-hours gate, and in run-sndk.sh a gate that cannot import reads as
    "market closed" and exits 0."""
    args = list(args)
    while args and args[0].startswith("-") and args[0] not in ("-m", "-c"):
        args.pop(0)
    if not args or (args[0] in ("-m", "-c") and len(args) < 2):
        return [("python with no target", [])]

    def under(*rels):
        paths = map(Path, rels)
        return [p if p.is_absolute() else cwd / p
                for p in paths if p.is_absolute() or cwd is not None]

    if args[0] == "-m":
        rel = args[1].replace(".", "/")
        return [(f"python -m {args[1]} from {cwd}", under(f"{rel}.py", f"{rel}/__main__.py"))]
    if args[0] != "-c":
        return [(f"python {args[0]} from {cwd}", under(args[0]))]

    needs = []
    for node in ast.walk(ast.parse(args[1])):
        if isinstance(node, ast.Import):
            pairs = [(alias.name, None) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            pairs = [(node.module, alias.name) for alias in node.names]
        else:
            continue
        for module, name in pairs:
            if cwd is None or not (cwd / module.split(".")[0]).exists():
                continue                # stdlib or the venv: not the repo's to vouch for
            rel = module.replace(".", "/")
            if name is None:
                files = under(f"{rel}.py", f"{rel}/__init__.py")
            else:
                files = under(f"{rel}/{name}.py", f"{rel}/{name}/__init__.py", f"{rel}.py")
            dotted = f"{module}.{name}" if name else module
            needs.append((f"python -c 'import {dotted}' from {cwd}", files))
    return needs


def _interval(runs):
    """StartInterval, in seconds, of the one job whose run script launches `runs`."""
    runs = Path(runs).resolve()
    jobs = []
    for path in PLISTS:
        job = _job(path)
        script = _run_script(job)
        if script is not None and script.is_file() and any(
                runs in {p.resolve() for p in files} for _, files in _launches(script)):
            jobs.append((path.stem, job))
    assert len(jobs) == 1, (f"expected exactly one job to run {runs.relative_to(REPO)}, "
                            f"found {[stem for stem, _ in jobs]}")
    stem, job = jobs[0]
    assert job.get("StartInterval"), f"{stem} runs {runs.name} without a StartInterval"
    return job["StartInterval"]


def _sndk_pro(module):
    if str(SNDK_PRO) not in sys.path:
        sys.path.insert(0, str(SNDK_PRO))
    return __import__(module)


# --- the installer --------------------------------------------------------------

def test_every_built_job_is_hired_by_the_installer():
    """Every plist in runtime/launchd is installed, and the installer names none that is missing."""
    listed = _shell_array(SCRIPTS / "install-launchd.sh", "PLISTS")
    built = {p.name for p in PLISTS}
    assert len(listed) == len(set(listed)), "install-launchd.sh names a job twice"
    assert built - set(listed) == set(), "built but never hired: missing from install-launchd.sh"
    assert set(listed) - built == set(), "install-launchd.sh names plists that do not exist"


def test_uninstall_removes_every_job_the_installer_loads():
    """uninstall-launchd.sh boots out exactly the jobs install-launchd.sh loads."""
    installed = {Path(name).stem for name in _shell_array(SCRIPTS / "install-launchd.sh", "PLISTS")}
    assert set(_shell_array(SCRIPTS / "uninstall-launchd.sh", "PLISTS")) == installed


# --- each job definition ----------------------------------------------------------

@pytest.mark.parametrize("path", PLISTS, ids=lambda p: p.stem)
def test_job_is_a_loadable_schedule(path):
    """launchd can load the job: it parses, is named for its file, runs on a schedule and logs."""
    job = _job(path)
    assert job.get("Label") == path.stem
    assert job.get("ProgramArguments") or job.get("Program"), "nothing to run"
    assert any(job.get(key) for key in SCHEDULE_KEYS), \
        f"none of {', '.join(SCHEDULE_KEYS)}: launchd would never start it"
    for key in LOG_KEYS:
        assert job.get(key), f"no {key}: its failures would go nowhere"


def test_every_job_logs_to_its_own_files():
    """No two jobs share a log file, so one job's failure is never filed under another's name."""
    owner = {}
    for path in PLISTS:
        job = _job(path)
        for key in LOG_KEYS:
            log = job.get(key)
            if log:
                assert owner.setdefault(log, path.stem) == path.stem, \
                    f"{path.stem} writes {log}, which is {owner[log]}'s log"


@pytest.mark.parametrize("path", PLISTS, ids=lambda p: p.stem)
def test_job_starts_a_program_that_exists_and_can_run(path):
    """The job starts an executable run script from runtime/scripts, or a system tool present on this Mac."""
    job = _job(path)
    script = _run_script(job)
    if script is None:
        program = _program(job)
        assert os.path.isabs(program), \
            f"{path.stem} starts {program!r}: not a run script and not an absolute path"
        # Whether the tool is INSTALLED can only be asked on the machine that runs
        # the job, and these are launchd's jobs, so that machine is a Mac:
        # /usr/bin/caffeinate is not on a Linux CI box. Asking there fails a job
        # that is fine instead of finding one that is broken.
        if sys.platform != "darwin":
            pytest.skip(f"{program} is a macOS tool; only a Mac can say whether it is installed")
        assert os.access(program, os.X_OK), \
            f"{path.stem} starts {program!r}: not a run script and not an executable here"
        return
    assert script.is_file(), f"{path.stem} starts {script.relative_to(REPO)}, which is not in the repo"
    assert os.access(script, os.X_OK), f"{script.name} is not executable, so bash -lc cannot start it"


def test_every_job_runs_the_same_checkout():
    """All script jobs start from one installed checkout, so the station never runs two versions of its code."""
    checkouts = {}
    for path in PLISTS:
        program = _program(_job(path))
        if SCRIPTS_REL in program:
            checkouts.setdefault(program.split(SCRIPTS_REL, 1)[0], []).append(path.stem)
    assert len(checkouts) == 1, checkouts


@pytest.mark.parametrize("path", PLISTS, ids=lambda p: p.stem)
def test_run_script_launches_only_what_is_in_the_repo(path):
    """Every file a job's run script sources or hands to Python exists where the script looks for it."""
    script = _run_script(_job(path))
    if script is None:
        return
    assert script.is_file(), f"{script.relative_to(REPO)} is not in the repo"
    launches = _launches(script)
    assert any(what.startswith("python") for what, _ in launches), \
        f"{script.name}: found no python call to check"
    missing = [what for what, files in launches if not any(f.exists() for f in files)]
    assert missing == [], f"{script.name} launches what is not there: {missing}"


# --- SNDK Pro cadence ---------------------------------------------------------------
# The scanner, its dead-man's switch and the reader run on three independent
# clocks. These hold the promises those clocks make to each other.

def test_a_skipped_scanner_tick_never_pages_the_phone():
    """The dead-man's silence ceiling outlasts one skipped scanner tick, so a healthy scanner is never paged."""
    # launchd drops a fire that overlaps a running tick, so a skipped tick is
    # ordinary: the newest row is then two intervals old.
    assert 2 * _interval(SCANNER) < sndk_deadman.SNDK_SILENT_MIN * 60


def test_the_dead_man_samples_faster_than_its_ceiling():
    """The dead-man checks more often than its ceiling, so silence is paged before it lasts two ceilings."""
    assert _interval(sndk_deadman.__file__) < sndk_deadman.SNDK_SILENT_MIN * 60


def test_a_healthy_open_writes_its_first_row_before_the_never_started_page():
    """A scanner live from the open writes its first row before the dead-man's first-row deadline, even missing one tick."""
    opens = market_status._RTH_OPEN
    allowance_s = sndk_deadman.FIRST_ROW_BY_MIN * 60 - (opens.hour * 60 + opens.minute) * 60
    assert 2 * _interval(SCANNER) < allowance_s


def test_the_feed_counts_ticks_in_the_scanners_real_interval():
    """The chain cache is sized in scanner ticks, so the feed's tick is the one launchd runs."""
    assert _sndk_pro("sndk_feed")._SCAN_INTERVAL_S == _interval(SCANNER)


def test_the_tick_after_a_pull_reprices_the_cached_book():
    """The scan one tick after a pull re-serves that book, so the chain is pulled every other tick, not every tick."""
    assert _interval(SCANNER) < _sndk_pro("sndk_feed")._CHAIN_TTL_S


def test_the_reader_never_calls_a_healthy_book_stale():
    """A book the scanner still re-serves is inside the reader's stale-book ceiling until a tick replaces it, even a tick as late as the cache forgives."""
    feed, reader = _sndk_pro("sndk_feed"), _sndk_pro("sndk_read")
    scan_s, ttl_s = _interval(SCANNER), feed._CHAIN_TTL_S
    # The scanner re-serves its disk-cached chain until it is ttl_s old, so it
    # forgives the tick after a pull for running up to ttl_s - scan_s late. The
    # row carrying the oldest book it re-serves waits one more tick to be
    # replaced, and that tick is owed the same lateness before the reader may
    # call the book stale. Without it the ceiling is a tie, and one late scan
    # suppresses a healthy book's wakes.
    late_s = ttl_s - scan_s
    assert ttl_s + scan_s + late_s <= reader.MAX_BOOK_AGE_MIN * 60


def test_the_reader_keeps_step_with_the_scanner():
    """The reader fires at least as often as the scanner, so it always sees the newest diary row."""
    assert _interval(READER) <= _interval(SCANNER)


def test_a_slow_read_never_pages_the_phone():
    """The dead-man's reader ceiling outlasts a read that runs to its timeouts, the fires launchd drops meanwhile, and a next read as slow."""
    every_s, read_s = _interval(READER), sndk_deadman.READ_MAX_RUN_S
    # a read row is stamped when its read starts and lands when it ends, and
    # launchd drops every fire that comes while a read is still running
    next_start_s = math.ceil(read_s / every_s) * every_s
    assert next_start_s + read_s < sndk_deadman.READER_SILENT_MIN * 60


# --- market-hours gates -------------------------------------------------------------
# A gated run script asks market_status whether the market is live before it starts
# its job. The gate's own "closed" is the only answer that may skip quietly: a gate
# that cannot answer (a broken venv, an import that fails) must fail the job, so
# launchd shows a failure status instead of what looks like a closed market.

# run script -> what it starts only while the market is live
GATED = {"run-sndk.sh": "sndk_hunter.py", "run-sndk-read.sh": "sndk_read.py",
         "run-sndk-bars.sh": "sndk_bars.py", "run-lob-collector.sh": "lob_bridge.py",
         "run-book-collector.sh": "book_flow", "run-watch-left-eye.sh": "hunter.py",
         "run-sndk-jev.sh": "sndk_jev.service"}
MARKET_STATUS = {
    "open": "def check(now=None):\n    return type('Status', (), {'is_live': True})()\n",
    "closed": "def check(now=None):\n    return type('Status', (), {'is_live': False})()\n",
    "broken": "import a_module_this_station_never_installed\n",
}


def _file(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def _run_gated(tmp_path, script, market, python=True):
    """Run a copy of `script` in a stand-in station whose market_status is `market`.

    The stand-in env.sh sets only what the run scripts take from the real one,
    which reads Keychain. Its venv python runs the gate for real and only records
    any other launch, so nothing the gate lets through ever starts; with
    `python=False` the venv has no python at all. A stub `date` makes every run a
    weekday mid-morning, so the weekend skip never answers before the gate does.
    Returns the finished process and the recorded launches."""
    root, venv, launched = tmp_path / "station", tmp_path / "venv", tmp_path / "launched"
    scripts, intraday = root / "runtime" / "scripts", root / "runtime" / "watch" / "intraday"
    for skill in ("sndk-pro", "mirai-left-eye", "book-flow", "sndk-jev"):
        (root / "skills" / skill).mkdir(parents=True)
    _file(intraday.parent / "__init__.py", "")
    _file(intraday / "__init__.py", "")
    _file(intraday / "market_status.py", MARKET_STATUS[market])
    _file(scripts / "env.sh",
          'export MIRAI_STATION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"\n'
          f'export MIRAI_STATION_VENV="{venv}"\n'
          'log() { echo "$*"; }\n')
    shutil.copy2(SCRIPTS / script, scripts / script)
    _file(tmp_path / "bin" / "date",
          '#!/bin/sh\ncase "$*" in\n  +%u) echo 3 ;;\n  +%H%M) echo 1030 ;;\n'
          '  *) exec /bin/date "$@" ;;\nesac\n', 0o755)
    if python:
        _file(venv / "bin" / "python",
              '#!/bin/sh\ncase "$1 $2" in\n'
              f'  "-c "*market_status*) exec "{sys.executable}" "$@" ;;\nesac\n'
              f'echo "$*" >> "{launched}"\n', 0o755)
    done = subprocess.run(["/bin/bash", str(scripts / script)], capture_output=True, text=True,
                          env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path)},
                          timeout=60)
    return done, launched.read_text() if launched.exists() else ""


def test_every_market_hours_gate_is_run_below():
    """Every run script that asks market_status is one the gate tests below execute."""
    asking = sorted(p.name for p in SCRIPTS.glob("run-*.sh") if "market_status" in p.read_text())
    assert asking == sorted(GATED)


@pytest.mark.parametrize("script", sorted(GATED))
def test_an_open_market_starts_the_job(tmp_path, script):
    """The gate lets an open market through to the job it guards."""
    done, launched = _run_gated(tmp_path, script, "open")
    assert done.returncode == 0, done.stderr
    assert GATED[script] in launched


@pytest.mark.parametrize("script", sorted(GATED))
def test_a_closed_market_skips_the_job_quietly(tmp_path, script):
    """A closed market exits 0 with nothing on stderr and never starts the job."""
    done, launched = _run_gated(tmp_path, script, "closed")
    assert (done.returncode, done.stderr) == (0, "")
    assert GATED[script] not in launched


@pytest.mark.parametrize("python", [True, False], ids=["import-fails", "no-venv-python"])
@pytest.mark.parametrize("script", sorted(GATED))
def test_a_gate_that_cannot_answer_fails_the_job_out_loud(tmp_path, script, python):
    """A market-hours check that cannot run exits 1 with one line on stderr saying so, and never starts the job."""
    done, launched = _run_gated(tmp_path, script, "broken", python=python)
    assert done.returncode == 1, (done.returncode, done.stdout)
    assert GATED[script] not in launched
    assert sum("FAILED" in line for line in done.stderr.splitlines()) == 1, done.stderr


_YEAR_IN_PATTERN = re.compile(r"(?<!\d)(19|20)\d\d(?!\d)")
_PATTERN_CALLS = {"glob", "rglob", "fnmatch", "iglob"}


def _spelled(node, constants):
    """The text an argument spells: a string literal, a module-level string
    constant it names, or the fixed parts of an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values if isinstance(part, ast.Constant))
    return None


def _year_pinned_patterns():
    for path in sorted([*(REPO / "skills").rglob("*.py"), *(REPO / "runtime").rglob("*.py")]):
        if any(part in {"tests", "node_modules", "__pycache__", ".venv", "venv"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # a pattern kept in a constant (sndk_read._DAY_FILE_GLOB) is still a pattern
        constants = {node.targets[0].id: node.value.value for node in tree.body
                     if isinstance(node, ast.Assign) and len(node.targets) == 1
                     and isinstance(node.targets[0], ast.Name)
                     and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in _PATTERN_CALLS:
                continue
            # fnmatch takes the pattern second, glob first
            for arg in node.args:
                text = _spelled(arg, constants)
                if text and _YEAR_IN_PATTERN.search(text):
                    yield f"{path.relative_to(REPO)}:{node.lineno} {name}({text!r})"


def test_no_file_pattern_stops_at_a_calendar_year():
    """A daily record is found by the shape of a date, never a fixed year — a glob of "2026-*" silently stops taking in new sessions on January 1."""
    assert list(_year_pinned_patterns()) == []
