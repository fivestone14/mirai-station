"""The key must never reach git, the state files, or a log line."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
KEY = re.compile(r"apikey_[0-9a-f]{20,}_[0-9a-f]{20,}|TYPESAFE_API_KEY=\S{16,}")   # README's "TYPESAFE_API_KEY=..." is a placeholder
SCAN_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".sh", ".html", ".txt", ".template", ".example"}


def test_env_is_git_ignored_and_the_example_is_not():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout, so there is no ignore list to check")
    r = subprocess.run(["git", "check-ignore", "-q", "skills/sndk-jev/.env"], cwd=REPO)
    assert r.returncode == 0, "skills/sndk-jev/.env is not git-ignored"
    r = subprocess.run(["git", "check-ignore", "-q", "skills/sndk-jev/.env.example"], cwd=REPO)
    assert r.returncode == 1, ".env.example should stay tracked"


def test_no_key_in_any_skill_file_but_env():
    for p in SKILL.rglob("*"):
        if not p.is_file() or p.name == ".env" or p.suffix not in SCAN_SUFFIXES or ".pytest_cache" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        assert not KEY.search(text), f"{p.relative_to(SKILL)} holds what looks like a key"


def test_service_output_never_carries_the_header():
    out = REPO / "state" / "jev"
    if not out.exists():
        pytest.skip("no state/jev on this machine: the service has not run here")
    for p in out.rglob("*.json*"):           # hour/ and watch/ too
        text = p.read_text(encoding="utf-8", errors="replace")
        assert "Bearer " not in text and not KEY.search(text), f"{p} holds a secret"


def test_send_never_logs_the_key():
    src = (SKILL / "sndk_jev" / "ask.py").read_text(encoding="utf-8")
    assert "print(" not in src.split("def send(")[1].split("def send_all(")[0], "send() must not print"
    svc = (SKILL / "sndk_jev" / "service.py").read_text(encoding="utf-8")
    assert "TYPESAFE_API_KEY']" not in svc and 'environ["TYPESAFE_API_KEY"]' not in svc.replace("os.environ.get", ""), "service must not read the key into a variable it could print"
