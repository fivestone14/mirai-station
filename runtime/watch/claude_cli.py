"""Thin subprocess wrapper around `claude -p`.

All reasoning calls in Mirai Watch route through this module so we have:
- consistent timeout (180s default — well above the measured 7-12s median),
- single retry on non-zero exit,
- JSON extraction with strict prompt convention,
- structured wall_s logging for ongoing latency telemetry.

The subscription-not-API constraint means we never call the Anthropic SDK;
every reasoning call is a `claude -p` subprocess.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional

# Exponential-backoff schedule for retries. We only re-fire on transient failures
# (timeout, non-zero exit, rate-limit-looking stderr). Anything else is fatal.
# Caps at 3 attempts (initial + 2 retries) so a wedged subprocess can't burn a tick.
_BACKOFF_S = (4.0, 16.0)  # gaps between attempt 1→2 and 2→3

# Patterns we treat as rate-limit / transient signals from `claude -p` stderr.
_RATE_LIMIT_RE = re.compile(
    r"(rate.?limit|429|too\s+many\s+requests|usage\s+limit|quota|please\s+wait)",
    re.IGNORECASE,
)


@dataclass
class CallResult:
    text: str
    parsed: Optional[Any]
    wall_s: float
    returncode: int
    error: Optional[str] = None
    retried: bool = False
    attempts: int = 1


def extract_json(text: str) -> Optional[Any]:
    """Find and parse the first COMPLETE JSON object in `text`.

    Robust to arbitrary nesting — a regex cannot balance nested braces, so the old
    pattern silently returned the first *innermost* object on a deeply-nested payload
    (e.g. the macro-brief / insight schemas), yielding a wrong sub-object. This tries
    the whole stripped output first (our prompts request exactly the JSON, optionally
    in a ```json fence), then scans for the first `{` that begins a fully-parseable
    object via JSONDecoder.raw_decode."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):                      # strip a ```json / ``` fence
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip().rstrip("`").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text, i)
                return obj
            except json.JSONDecodeError:
                continue
    return None


def call(
    prompt: str,
    *,
    model: Optional[str] = None,
    task: Optional[str] = None,
    timeout: float = 180.0,
    parse_json: bool = False,
    retry_once: bool = True,  # kept for backward compat; ignored — see _BACKOFF_S
    allowed_tools: Optional[list[str]] = None,
    disallowed_tools: Optional[list[str]] = None,
    permission_mode: Optional[str] = None,
) -> CallResult:
    """Invoke `claude -p <prompt>` with exponential-backoff retry.

    Args:
        prompt: full prompt text.
        model: explicit model override (e.g. "claude-haiku-4-5"). Wins over task.
        task: kind of call (e.g. "fetch", "distill", "counsel_argue",
            "thesis_reasoning", "tick_reasoning") — resolved to a model via the
            `models` map in limits-and-cooldowns.json. The tier policy: haiku
            gathers, sonnet drafts and argues, opus decides. Omitting both
            model and task resolves to the map's "default" tier — a call must
            never silently inherit the CLI's session default model.
        timeout: hard timeout per attempt, in seconds.
        parse_json: if True, attempt to extract a JSON object from stdout.
        retry_once: legacy flag; the wrapper now follows ``_BACKOFF_S`` regardless.
        allowed_tools: permission rules passed as ``--allowedTools``. Headless
            `claude -p` never prompts, so any call that needs tools (MCP
            fetchers, web search) must allowlist them here. NOTE: MCP tools are
            allowed by *server* name (e.g. ``mcp__market-research``), not the full
            tool name. Reasoning-only calls omit this and run tool-less.
        disallowed_tools: tool names to deny via ``--disallowedTools`` — removes
            them from the model's toolset entirely, and wins over any allow/grant.
            Pair with ``permission_mode='bypassPermissions'`` to get scoped
            read-only tool use (research tools run, Bash/Write/Edit cannot).
        permission_mode: ``--permission-mode`` value (e.g. ``bypassPermissions``).
            Headless `-p` in the default mode will NOT auto-approve a tool from
            ``allowed_tools`` alone (it expects an interactive grant it can't get),
            so a call that must actually run tools sets this; keep it scoped with
            ``disallowed_tools``. Omit for tool-less reasoning.

    Behavior:
        - On rc==0: return immediately.
        - On timeout: sleep per ``_BACKOFF_S`` and retry.
        - On non-zero rc that looks like a rate-limit (stderr matches
          ``_RATE_LIMIT_RE``): sleep per ``_BACKOFF_S`` and retry.
        - On any other non-zero rc: fail fast (no retry — this is usually a
          programming error in the prompt, not a transient).

    Returns:
        CallResult with ``attempts`` reflecting how many invocations ran.
    """
    if model is None:
        from .intraday import settings as _settings
        model = _settings.model_for(task or "default")

    # Prompt must precede the flags: --allowedTools is variadic and would
    # swallow a trailing positional argument.
    cmd = ["claude", "-p", prompt]
    if model:
        cmd.extend(["--model", model])
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])
    if disallowed_tools:
        cmd.extend(["--disallowedTools", ",".join(disallowed_tools)])
    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])

    last_text = ""
    last_err = ""
    last_rc = -2
    last_wall = 0.0

    max_attempts = 1 + len(_BACKOFF_S)
    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            wall = time.time() - t0
            if r.returncode == 0:
                parsed = extract_json(r.stdout) if parse_json else None
                return CallResult(
                    text=r.stdout,
                    parsed=parsed,
                    wall_s=round(wall, 2),
                    returncode=0,
                    retried=attempt > 1,
                    attempts=attempt,
                )
            # Non-zero rc. Decide whether to retry.
            err_text = (r.stderr or r.stdout or "")[-500:]
            transient = bool(_RATE_LIMIT_RE.search(err_text))
            last_text, last_err, last_rc, last_wall = r.stdout, err_text, r.returncode, wall
            if not transient or attempt == max_attempts:
                return CallResult(
                    text=last_text,
                    parsed=None,
                    wall_s=round(last_wall, 2),
                    returncode=last_rc,
                    error=f"rc={last_rc}: {last_err}",
                    retried=attempt > 1,
                    attempts=attempt,
                )
        except subprocess.TimeoutExpired:
            last_wall = time.time() - t0
            last_text, last_err, last_rc = "", "timeout", -1
            if attempt == max_attempts:
                return CallResult(
                    text="",
                    parsed=None,
                    wall_s=round(last_wall, 2),
                    returncode=-1,
                    error="timeout",
                    retried=attempt > 1,
                    attempts=attempt,
                )

        # Sleep before the next attempt.
        time.sleep(_BACKOFF_S[attempt - 1])

    # Unreachable; safety net.
    return CallResult(
        text=last_text,
        parsed=None,
        wall_s=round(last_wall, 2),
        returncode=last_rc,
        error=f"exhausted retries: {last_err}",
        retried=True,
        attempts=max_attempts,
    )


def json_prompt(instruction: str, schema_example: dict) -> str:
    """Compose a strict JSON-output prompt following the convention validated in feasibility."""
    return (
        f"{instruction}\n\n"
        f"Output ONLY this JSON object, nothing else, no markdown fences, no commentary:\n"
        f"{json.dumps(schema_example)}"
    )
