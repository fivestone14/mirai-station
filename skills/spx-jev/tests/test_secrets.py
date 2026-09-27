"""The key must never reach git, the state files, or a log line."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
KEY = re.compile(r"apikey_[0-9a-f]{20,}_[0-9a-f]{20,}|TYPESAFE_API_KEY=\S{16,}")
SCAN_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".sh", ".txt", ".template", ".example"}


def test_env_is_git_ignored_and_the_example_is_not():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout, so there is no ignore list to check")
    assert subprocess.run(["git", "check-ignore", "-q", "skills/spx-jev/.env"], cwd=REPO).returncode == 0
    assert subprocess.run(["git", "check-ignore", "-q", "skills/spx-jev/.env.example"], cwd=REPO).returncode == 1


def test_no_key_in_any_skill_file_but_env():
    for p in SKILL.rglob("*"):
        if not p.is_file() or p.name == ".env" or p.suffix not in SCAN_SUFFIXES or ".pytest_cache" in p.parts:
            continue
        assert not KEY.search(p.read_text(encoding="utf-8", errors="replace")), f"{p.relative_to(SKILL)} holds what looks like a key"


def test_send_never_logs_the_key():
    src = (SKILL / "spx_jev" / "ask.py").read_text(encoding="utf-8")
    assert "print(" not in src.split("def send(")[1].split("def send_all(")[0], "send() must not print"
    svc = (SKILL / "spx_jev" / "service.py").read_text(encoding="utf-8")
    assert "TYPESAFE_API_KEY']" not in svc and 'environ["TYPESAFE_API_KEY"]' not in svc.replace("os.environ.get", "")


def test_the_env_loader_fills_missing_values_only(tmp_path, monkeypatch):
    import os
    from spx_jev.service import load_env_file
    f = tmp_path / ".env"
    f.write_text("# comment\nTYPESAFE_API_KEY=dummy\nexport OTHER='quoted value'\nEMPTY=\n", encoding="utf-8")
    for name in ("TYPESAFE_API_KEY", "OTHER", "EMPTY"):
        monkeypatch.delenv(name, raising=False)
    assert load_env_file(f) == ["TYPESAFE_API_KEY", "OTHER"] and os.environ["OTHER"] == "quoted value" and "EMPTY" not in os.environ
    monkeypatch.setenv("TYPESAFE_API_KEY", "already-set")
    assert load_env_file(f) == [] and os.environ["TYPESAFE_API_KEY"] == "already-set"
