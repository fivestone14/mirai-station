"""The service's .env loader: fills the environment from the file, never overrides, never prints."""
from __future__ import annotations

import os

from sndk_jev.service import load_env_file, plain


def test_env_file_fills_missing_values_only(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\n" "TYPESAFE_API_KEY=dummy\n" "export OTHER='quoted value'\n" "EMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)
    assert load_env_file(f) == ["TYPESAFE_API_KEY", "OTHER"]
    assert os.environ["TYPESAFE_API_KEY"] == "dummy"
    assert os.environ["OTHER"] == "quoted value"
    assert "EMPTY" not in os.environ

    monkeypatch.setenv("TYPESAFE_API_KEY", "already-set")
    assert load_env_file(f) == []
    assert os.environ["TYPESAFE_API_KEY"] == "already-set"


def test_env_file_missing_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope") == []


def test_plain_spells_the_unit_once():
    s = plain("over the last 30 minutes price rose 0.23 sigma, more than the 0.15 sigma move rule")
    assert s == "over the last 30 minutes price rose 0.23 of a normal day's move, more than the 0.15 move rule"
    assert "sigma" not in s
