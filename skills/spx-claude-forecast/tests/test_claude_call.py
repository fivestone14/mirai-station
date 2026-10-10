"""The claude -p caller: subscription only, tools denied last, the bill read from the envelope, never raising."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from spx_claude_forecast import claude_call

ENVELOPE = {"type": "result", "is_error": False, "result": '{"forecast": {"next_30_minutes": {"up_pct": 10}}}',
            "total_cost_usd": 0.0702, "num_turns": 1, "duration_ms": 4200, "model": "claude-opus-5-5",
            "usage": {"input_tokens": 2, "cache_creation_input_tokens": 16136, "cache_read_input_tokens": 28129,
                      "output_tokens": 5, "output_tokens_details": {"thinking_tokens": 0}}}


def _fake_runner(stdout: str, returncode: int = 0, stderr: str = ""):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    run.calls = calls
    return run


def test_the_call_refuses_to_run_when_an_api_key_is_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = claude_call.call_claude("p", "rules", runner=_fake_runner(json.dumps(ENVELOPE)))
    assert result.error.startswith("refused: ANTHROPIC_API_KEY") and result.answer_parsed is None


def test_the_command_pins_model_and_effort_and_denies_every_tool_last(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(claude_call, "claude_binary", lambda: "/usr/local/bin/claude")
    runner = _fake_runner(json.dumps(ENVELOPE))
    result = claude_call.call_claude("the scene", "the rules", runner=runner)
    cmd, kwargs = runner.calls[0]
    assert cmd[:3] == ["/usr/local/bin/claude", "-p", "the scene"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5" and cmd[cmd.index("--effort") + 1] == "medium"
    assert cmd[cmd.index("--append-system-prompt") + 1] == "the rules"
    assert "--disallowedTools" in cmd and cmd[cmd.index("--disallowedTools") + 1:] == list(claude_call.DENIED_TOOLS)
    assert "--allowedTools" not in cmd and "--dangerously-skip-permissions" not in cmd
    assert "ANTHROPIC_API_KEY" not in kwargs["env"] and kwargs["cwd"]
    assert result.error is None and result.answer_parsed == {"forecast": {"next_30_minutes": {"up_pct": 10}}}
    assert result.call_stats["cost_usd"] == 0.0702 and result.call_stats["cache_read_input_tokens"] == 28129
    assert result.call_stats["model_turn_count"] == 1 and result.model_served == "claude-opus-5-5"
    assert result.call_stats["cli_duration_seconds"] == 4.2 and "duration_ms" not in result.call_stats


def test_a_failed_exit_and_a_reply_without_json_come_back_as_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_call, "claude_binary", lambda: "/usr/local/bin/claude")
    failed = claude_call.call_claude("p", "r", runner=_fake_runner("", returncode=2, stderr="usage limit reached"))
    assert failed.error.startswith("exit 2") and "usage limit" in failed.error
    prose = claude_call.call_claude("p", "r", runner=_fake_runner(json.dumps({**ENVELOPE, "result": "I cannot say."})))
    assert prose.error == "no JSON object in the reply" and prose.answer_text == "I cannot say."


def test_a_timeout_is_an_error_not_an_exception(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_call, "claude_binary", lambda: "/usr/local/bin/claude")

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    result = claude_call.call_claude("p", "r", timeout_s=7, runner=slow)
    assert result.error == "timeout after 7 s"


def test_first_json_object_skips_prose_around_the_object():
    assert claude_call.first_json_object('note: {"a": 1} trailing') == {"a": 1}
    assert claude_call.first_json_object("nothing here") is None
