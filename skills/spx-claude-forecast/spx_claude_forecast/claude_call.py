"""One ``claude -p`` call on the subscription, every tool denied, with its bill and timing kept.

Why these flags (the pattern proven by SNDK, skills/sndk-pro/sndk_read.py call_the_model): the rulebook
rides ``--append-system-prompt`` so it stays byte-identical and cached; ``--disallowedTools`` is the only
thing that actually denies tools (``--allowedTools`` does not), and it must stay LAST on the command line;
the MCP config is emptied; the model and the effort are pinned explicitly because an unpinned effort is
production configured from outside the repo (a global ``xhigh`` once made every call reason ten times
longer). The call refuses to run with an API key in the environment, so it can never bill the API instead
of the subscription, and it runs from an empty folder so no project file rides along.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

PINNED_MODEL = "claude-opus-5-5"            # an exact id, never an alias
CALL_EFFORT = "medium"                      # always passed; see the module docstring
CALL_TIMEOUT_S = 120.0                      # one attempt, no retries: nothing waits on this call
API_KEY_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")   # either would bill the API; the call refuses to run
DENIED_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "WebFetch", "Task", "Agent",
                "TodoWrite", "ExitPlanMode", "BashOutput", "KillShell", "SlashCommand", "Skill", "Bash", "WebSearch")
ANSWER_TEXT_KEPT_CHARS = 4000               # the reply is kept whole up to this; a longer one is cut, never lost


@dataclass
class CallResult:
    """What one call came back with. ``error`` is None on success; ``answer_parsed`` is the first JSON object in
    the reply, or None when there was none (the raw text is kept in ``answer_text`` either way)."""

    answer_text: str | None
    answer_parsed: dict | None
    error: str | None
    response_seconds: float
    call_stats: dict = field(default_factory=dict)
    cli_version: str | None = None
    model_served: str | None = None


def api_key_in_environment() -> str | None:
    """The name of the API-key variable that is set, or None. Set means the CLI would bill the API."""
    for name in API_KEY_ENVS:
        if os.environ.get(name):
            return name
    return None


def claude_binary() -> str | None:
    """The real ``claude`` executable (never a shell alias, which a subprocess cannot see)."""
    return shutil.which("claude")


def cli_version(binary: str | None = None) -> str | None:
    binary = binary or claude_binary()
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr or "").strip().splitlines()[0][:80] or None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def command_for(prompt: str, rulebook: str, model: str = PINNED_MODEL, effort: str = CALL_EFFORT,
                binary: str = "claude") -> list[str]:
    return [binary, "-p", prompt, "--model", model, "--output-format", "json",
            "--append-system-prompt", rulebook,
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--effort", effort, "--no-session-persistence",
            "--disallowedTools", *DENIED_TOOLS]                    # variadic: must stay LAST


def first_json_object(text: str | None) -> dict | None:
    """The first balanced JSON object in a blob (the CLI envelope wraps the reply)."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text, i)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                continue
    return None


def call_stats_of(envelope: dict | None, response_seconds: float) -> dict:
    """The bill and the shape of one call from the CLI's JSON envelope, in the names spec/record_formats.md gives
    them; a count the envelope did not carry is absent, not null."""
    stats: dict = {"response_seconds": round(response_seconds, 1)}
    if not isinstance(envelope, dict):
        return stats
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    duration_ms = envelope.get("duration_ms")
    for name, value in (("cost_usd", envelope.get("total_cost_usd")), ("input_tokens", usage.get("input_tokens")),
                        ("cache_read_input_tokens", usage.get("cache_read_input_tokens")),
                        ("cache_creation_input_tokens", usage.get("cache_creation_input_tokens")),
                        ("output_tokens", usage.get("output_tokens")), ("thinking_tokens", details.get("thinking_tokens")),
                        ("model_turn_count", envelope.get("num_turns")),
                        ("cli_duration_seconds", round(duration_ms / 1000, 1) if isinstance(duration_ms, (int, float)) else None)):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stats[name] = value
    return stats


def call_claude(prompt: str, rulebook: str, *, model: str = PINNED_MODEL, effort: str = CALL_EFFORT,
                timeout_s: float = CALL_TIMEOUT_S, runner=subprocess.run) -> CallResult:
    """One call. Never raises: every failure comes back as ``error``. ``runner`` is ``subprocess.run`` or a stand-in."""
    key = api_key_in_environment()
    if key:
        return CallResult(None, None, f"refused: {key} is set, which would bill the API instead of the subscription", 0.0)
    binary = claude_binary()
    if not binary:
        return CallResult(None, None, "refused: no claude executable on PATH", 0.0)
    cmd = command_for(prompt, rulebook, model, effort, binary)
    env = {k: v for k, v in os.environ.items() if k not in API_KEY_ENVS}
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="spx-claude-forecast-call-") as empty_folder:
        try:
            completed = runner(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=empty_folder, env=env)
        except subprocess.TimeoutExpired:
            return CallResult(None, None, f"timeout after {timeout_s:.0f} s", round(time.time() - started, 1))
        except OSError as e:
            return CallResult(None, None, f"spawn: {e}", round(time.time() - started, 1))
    seconds = round(time.time() - started, 1)
    envelope = first_json_object(completed.stdout)
    stats = call_stats_of(envelope, seconds)
    model_served = envelope.get("model") if isinstance(envelope, dict) and isinstance(envelope.get("model"), str) else None
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-300:]
        return CallResult((completed.stdout or "")[:ANSWER_TEXT_KEPT_CHARS] or None, None,
                          f"exit {completed.returncode}: {tail}", seconds, stats, model_served=model_served)
    text = envelope.get("result") if isinstance(envelope, dict) and isinstance(envelope.get("result"), str) else completed.stdout
    if isinstance(envelope, dict) and envelope.get("is_error"):
        return CallResult((text or "")[:ANSWER_TEXT_KEPT_CHARS], None, f"the CLI reported an error: {(text or '')[:200]}",
                          seconds, stats, model_served=model_served)
    parsed = first_json_object(text)
    return CallResult((text or "")[:ANSWER_TEXT_KEPT_CHARS] or None, parsed,
                      None if parsed is not None else "no JSON object in the reply", seconds, stats, model_served=model_served)
